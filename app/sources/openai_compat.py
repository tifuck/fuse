from __future__ import annotations

import threading
import time

import httpx

from .. import accounts, db
from ..proxy import UsageStream, openai_chat
from .base import ChatResult
from .pool_pick import Failover, cooling_down
from .pool_session import bind, lookup

# Rotation cursors must survive source reconstruction: routing builds a fresh
# OpenAICompatSource per request, so an instance attribute resets to key #1
# every time and "rotation" silently degenerates to always-first-key.
_cursors: dict[int, int] = {}
_cursor_lock = threading.Lock()
_models_cache: dict[int, tuple[float, list[str]]] = {}
_MODELS_TTL = 300.0

ROTATION_MODES = ("none", "every_request", "round_robin", "sticky")


def _advance(source_id: int, n: int) -> int:
    with _cursor_lock:
        start = _cursors.get(source_id, 0) % n
        _cursors[source_id] = start + 1
    return start


def reset_cursors() -> None:
    with _cursor_lock:
        _cursors.clear()


def _v1(url: str) -> str:
    u = url.rstrip("/")
    return u if u.endswith("/v1") else u + "/v1"


class OpenAICompatSource:
    type_id = "openai"

    def __init__(self, row, cfg: dict, creds):
        self.source_id = row["id"]
        self.name = row["name"]
        # Pin kind is per source: two OpenAI-compat sources with the same
        # scoped session id must not share a pin and swap each other's key.
        self.pin_kind = f"openai:{self.source_id}"
        self.cfg = cfg
        self.creds = list(creds)
        rotation = cfg.get("rotation")
        if rotation not in ROTATION_MODES:
            rotation = "none" if len(self.creds) <= 1 else "sticky"
        self.rotation = rotation

    def _url(self) -> str:
        return _v1(self.cfg.get("base_url") or "http://127.0.0.1:8080")

    def _headers(self, key: str) -> dict:
        h = {"Content-Type": "application/json"}
        if key:
            h["Authorization"] = f"Bearer {key}"
        h.update(self.cfg.get("extra_headers") or {})
        return h

    def _next_creds(self, exclude: set[int] | None = None, prefer_id: int | None = None):
        exclude = exclude or set()
        if not self.creds:
            return []
        # Keys cooling after a failed request go last (stable: rotation kept).
        ready, cooling = [], []
        for cred in self.creds:
            if cred["id"] not in exclude:
                (cooling if cooling_down(cred["id"]) else ready).append(cred)
        pool = ready + cooling
        if not pool:
            return []
        if prefer_id is not None:
            preferred = [c for c in pool if c["id"] == prefer_id]
            if preferred:
                return preferred + [c for c in pool if c["id"] != prefer_id]
        if self.rotation in ("every_request", "round_robin"):
            rotating = ready or cooling
            start = _advance(self.source_id, len(rotating))
            return rotating[start:] + rotating[:start] + (cooling if ready else [])
        return pool

    async def models(self) -> list[str]:
        if not self.creds:
            return []
        hit = _models_cache.get(self.source_id)
        if hit and time.time() - hit[0] < _MODELS_TTL:
            return list(hit[1])
        for cred in self._next_creds():
            key = accounts.cred_secret(cred)
            try:
                async with httpx.AsyncClient(timeout=30) as client:
                    r = await client.get(self._url() + "/models", headers=self._headers(key))
            except httpx.HTTPError:
                continue
            if r.status_code != 200:
                continue
            data = r.json()
            names = [m.get("id") for m in data.get("data", []) if m.get("id")]
            if names:
                _models_cache[self.source_id] = (time.time(), names)
            return names
        return []

    def _stream_result(self, stream, collected: dict, cred, session_id: str | None, model: str, attempts: list[dict]) -> ChatResult:
        if not session_id:
            result = ChatResult(status=200, stream=stream, credential_id=cred["id"], model=model)
            result.attempts = attempts
            return result
        cid = cred["id"]

        async def _bind_on_finish():
            try:
                async for chunk in stream:
                    yield chunk
            finally:
                close = getattr(stream, "aclose", None)
                if close is not None:
                    await close()
                if (collected or {}).get("stream_status") == "complete":
                    bind(self.pin_kind, session_id, cid)

        wrapped = UsageStream(_bind_on_finish(), collected)
        result = ChatResult(status=200, stream=wrapped, credential_id=cid, model=model)
        result.attempts = attempts
        return result

    async def chat(self, body: dict, *, session_id: str | None = None) -> ChatResult:
        if not self.creds:
            return ChatResult(status=503, body={"error": "No keys configured for this source"})
        tried: set[int] = set()
        attempts: list[dict] = []
        last = ChatResult(status=503, body={"error": "All keys failed"})
        prefer_id = lookup(self.pin_kind, session_id) if session_id else None
        model = body.get("model", "")
        failover = Failover()
        for cred in self._next_creds(tried, prefer_id=prefer_id):
            tried.add(cred["id"])
            key = accounts.cred_secret(cred)
            client = httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=15.0))
            if session_id:
                # Always send the tenant-scoped id, never the raw client/thread
                # key: two users picking the same string must not share an
                # upstream cache-routing group on a shared credential.
                body = {**body, "prompt_cache_key": str(session_id)[:256]}
            status, obj, stream, usage, response_headers = await openai_chat(
                client,
                self._url() + "/chat/completions",
                self._headers(key),
                body,
                close_client=True,
            )
            if stream is not None:
                failover.served()
                return self._stream_result(stream, usage, cred, session_id, model, attempts)
            if status == 200:
                failover.served()
                if session_id:
                    bind(self.pin_kind, session_id, cred["id"])
            last = ChatResult(
                status=status,
                body=obj,
                usage=usage,
                credential_id=cred["id"],
                model=model,
                response_headers=response_headers,
            )
            if usage.get("failure_phase") in ("request_sent", "response_received"):
                last.attempts = attempts
                return last
            # Rotate only what cannot have generated output upstream: an auth
            # rejection is credential-specific and a 5xx arrived before any
            # tokens. 429 stays terminal (callers must see the rate limit) and
            # 504 is a read timeout — upstream may still be running it. A 5xx
            # fails over to at most FAILOVER_SERVER_ERRORS keys, so one
            # poisonous request cannot walk (and bill) every key.
            if status == 401 or (status in (500, 502, 503) and failover.server_error(cred["id"], status, 30)):
                attempts.append({"credential_id": cred["id"], "status": status, "usage": usage or {}})
                continue
            failover.failed(10)
            last.attempts = attempts
            return last
        failover.failed(10)
        if attempts and last.credential_id == attempts[-1].get("credential_id"):
            attempts.pop()
        last.attempts = attempts
        return last
