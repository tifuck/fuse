from __future__ import annotations

import asyncio
import json
import time

import httpx

from .. import accounts, db
from ..crypto import encrypt
from ..proxy import openai_chat
from .base import ChatResult

BASE_URL = "https://api.experientiallabs.ai/v1"
CATALOG_URL = "https://api.experientiallabs.ai/api/models"
PUBLIC_PREFIX = "explabs/"
CATALOG_TTL = 300

_catalogs: dict[int, tuple[tuple, float, dict]] = {}
_locks: dict[int, asyncio.Lock] = {}
_errors: dict[int, str] = {}


class CatalogError(RuntimeError):
    def __init__(self, message: str, *, invalid_key: bool = False):
        super().__init__(message)
        self.invalid_key = invalid_key


def ensure_explabs_source() -> int:
    from . import _ensure_typed_source

    return _ensure_typed_source("explabs", "Experiential Labs", {"base_url": BASE_URL})


def public_id(model: str) -> str:
    return PUBLIC_PREFIX + model


def upstream_id(model: str) -> str:
    return model[len(PUBLIC_PREFIX):] if model.startswith(PUBLIC_PREFIX) else model


def user_credential(user_id: int | None, source_id: int | None = None):
    if not user_id:
        return None
    if source_id is None:
        source = db.fetchone("SELECT id FROM sources WHERE type='explabs' AND enabled=1")
        if not source:
            return None
        source_id = source["id"]
    return db.fetchone(
        "SELECT * FROM credentials WHERE source_id=? AND donated_by=? AND disabled=0 ORDER BY id LIMIT 1",
        (source_id, user_id),
    )


def user_has_explabs(user_id: int | None) -> bool:
    return user_credential(user_id) is not None


def _revision(cred) -> tuple:
    return cred["id"], cred["secret"]


def cached_catalog(user_id: int | None) -> dict | None:
    cred = user_credential(user_id)
    hit = _catalogs.get(user_id)
    if cred and hit and hit[0] == _revision(cred):
        return hit[2]
    return None


def clear_catalog(user_id: int) -> None:
    _catalogs.pop(user_id, None)
    _errors.pop(user_id, None)


def access_state(user_id: int) -> dict:
    owned = user_has_explabs(user_id)
    catalog = cached_catalog(user_id) or {}
    return {
        "ok": owned, "user_key": owned, "reason": "user" if owned else "none",
        "model_count": len(catalog.get("by_id", {})),
        "last_error": _errors.get(user_id, ""),
    }


def _catalog(payload) -> dict:
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise CatalogError("Experiential Labs returned an invalid model catalog.")
    records = {}
    for item in payload["data"]:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not item["id"].strip():
            raise CatalogError("Experiential Labs returned an invalid model catalog.")
        records[item["id"]] = dict(item)
    return {"by_id": records}


async def _fetch(key: str) -> dict:
    headers = {"Authorization": f"Bearer {key}"}
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(BASE_URL + "/models", headers=headers)
            if response.status_code == 401:
                raise CatalogError("Experiential Labs rejected the API key.", invalid_key=True)
            if response.status_code != 200:
                raise CatalogError(f"Could not load Experiential Labs models (HTTP {response.status_code}).")
            catalog = _catalog(response.json())
            # Metadata is optional; the authenticated callable list is authoritative.
            try:
                metadata = await client.get(CATALOG_URL, headers=headers)
                if metadata.status_code == 200:
                    payload = metadata.json()
                    rows = payload.get("models", payload.get("data", [])) if isinstance(payload, dict) else payload
                    if isinstance(rows, list):
                        for row in rows:
                            if not isinstance(row, dict):
                                continue
                            entry = row.get("model", row)
                            if not isinstance(entry, dict):
                                continue
                            mid = entry.get("slug")
                            rec = catalog["by_id"].get(mid)
                            if rec is None:
                                continue
                            rec["name"] = entry.get("display_name") or mid
                            rec["context_length"] = entry.get("context_window")
                            rec["supported_parameters"] = entry.get("supported_params") or {}
                            inputs = entry.get("input_modalities")
                            if isinstance(inputs, list):
                                rec["image"] = "image" in inputs
                            efforts = []
                            for provider in row.get("providers", []):
                                if not isinstance(provider, dict) or provider.get("available_to_org") is False or provider.get("routable") is False:
                                    continue
                                caps = provider.get("capabilities") or {}
                                if isinstance(caps, dict):
                                    for effort in caps.get("supported_reasoning_efforts") or []:
                                        if isinstance(effort, str) and effort not in efforts:
                                            efforts.append(effort)
                            rec["reasoning_efforts"] = efforts
                            outputs = entry.get("output_modalities")
                            if isinstance(outputs, list) and "text" not in outputs:
                                catalog["by_id"].pop(mid, None)
            except (httpx.HTTPError, ValueError, TypeError):
                pass
            return catalog
    except CatalogError:
        raise
    except (httpx.HTTPError, ValueError):
        raise CatalogError("Could not reach Experiential Labs or read its model catalog.") from None


async def fetch_catalog(user_id: int | None, *, force: bool = False) -> dict:
    if not user_id:
        return {"by_id": {}}
    async with _locks.setdefault(user_id, asyncio.Lock()):
        cred = user_credential(user_id)
        if not cred:
            clear_catalog(user_id)
            return {"by_id": {}}
        revision = _revision(cred)
        hit = _catalogs.get(user_id)
        if not force and hit and hit[0] == revision and time.monotonic() - hit[1] < CATALOG_TTL:
            return hit[2]
        try:
            catalog = await _fetch(accounts.cred_secret(cred))
        except CatalogError as exc:
            current = user_credential(user_id)
            if not current or _revision(current) != revision:
                return {"by_id": {}}
            _errors[user_id] = str(exc)
            if exc.invalid_key:
                disable_credential(cred["id"])
                _errors[user_id] = str(exc)
                return {"by_id": {}}
            if hit and hit[0] == revision:
                return hit[2]
            raise
        current = user_credential(user_id)
        if not current or _revision(current) != revision:
            return {"by_id": {}}
        _catalogs[user_id] = (revision, time.monotonic(), catalog)
        _errors.pop(user_id, None)
        return catalog


async def add_user_key(source_id: int, raw: str, user_id: int, *, label: str = "") -> tuple[int | None, str]:
    async with _locks.setdefault(user_id, asyncio.Lock()):
        return await _add_user_key(source_id, raw, user_id, label=label)


async def _add_user_key(source_id: int, raw: str, user_id: int, *, label: str = "") -> tuple[int | None, str]:
    key = (raw or "").strip()
    if not key or len(key) > 4096 or any(ord(ch) < 32 for ch in key):
        return None, "Enter an Experiential Labs API key."
    try:
        catalog = await _fetch(key)
    except CatalogError as exc:
        return None, str(exc)
    for row in db.fetchall("SELECT * FROM credentials WHERE source_id=? AND disabled=0", (source_id,)):
        if row["donated_by"] != user_id and accounts.cred_secret(row) == key:
            return None, "That key is already linked to another account."
    name = (label or "").strip()[:80] or "Experiential Labs key"
    existing = user_credential(user_id, source_id)
    if existing:
        cred_id = existing["id"]
        db.execute(
            "UPDATE credentials SET label=?, secret=?, extra=? WHERE id=? AND donated_by=? AND disabled=0",
            (name, encrypt(key), encrypt(json.dumps({"role": "user"})), cred_id, user_id),
        )
    else:
        cred_id = accounts.add_credential(source_id, name, key, extra={"role": "user"}, donated_by=user_id)
    clear_catalog(user_id)
    current = user_credential(user_id, source_id)
    if current and current["id"] == cred_id:
        _catalogs[user_id] = (_revision(current), time.monotonic(), catalog)
    return int(cred_id), ""


def disable_credential(cred_id: int) -> None:
    row = db.fetchone("SELECT donated_by FROM credentials WHERE id=?", (cred_id,))
    db.execute("UPDATE credentials SET disabled=1 WHERE id=?", (cred_id,))
    if row and row["donated_by"]:
        clear_catalog(row["donated_by"])


async def resolve(model: str, user_id: int | None) -> str | None:
    if not model.startswith(PUBLIC_PREFIX) or not user_has_explabs(user_id):
        return None
    catalog = await fetch_catalog(user_id)
    upstream = upstream_id(model)
    return upstream if upstream in catalog["by_id"] else None


class ExperientialLabsSource:
    type_id = "explabs"

    def __init__(self, row, cfg: dict, creds):
        self.source_id = row["id"]
        self.name = row["name"]
        self.cfg = cfg

    async def models(self, *, user_id: int | None = None) -> list[str]:
        catalog = await fetch_catalog(user_id)
        return [public_id(mid) for mid in catalog["by_id"]]

    async def chat(self, body: dict, *, user_id: int | None = None, session_id: str | None = None) -> ChatResult:
        cred = user_credential(user_id, self.source_id)
        model = upstream_id(str(body.get("model") or ""))
        if not cred:
            return ChatResult(status=403, body={"error": {"message": "Connect an Experiential Labs API key."}}, model=model)
        revision = _revision(cred)
        catalog = await fetch_catalog(user_id)
        current = user_credential(user_id, self.source_id)
        if not current or _revision(current) != revision:
            return ChatResult(status=403, body={"error": {"message": "Experiential Labs key changed. Retry the request."}}, model=model)
        if model not in catalog["by_id"]:
            return ChatResult(status=404, body={"error": {"message": "Model is not available to your Experiential Labs key."}}, model=model)
        payload = {k: v for k, v in body.items() if not k.startswith("_fuse") and k not in ("session_id", "models")}
        payload["model"] = model
        if isinstance(payload.get("messages"), list):
            payload["messages"] = [
                {k: v for k, v in message.items() if not k.startswith("_fuse")}
                if isinstance(message, dict) else message
                for message in payload["messages"]
            ]
        gateway_headers = {}
        responses = []

        async def capture_headers(response):
            responses.append(response)
            for name in ("x-request-id", "x-gateway-provider", "x-gateway-replay-repair"):
                value = response.headers.get(name)
                if value and not any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
                    gateway_headers[name] = value[:256]

        client = httpx.AsyncClient(
            timeout=httpx.Timeout(300.0, connect=15.0),
            event_hooks={"response": [capture_headers]},
        )
        status, obj, stream, usage, headers = await openai_chat(
            client, BASE_URL + "/chat/completions",
            {"Authorization": f"Bearer {accounts.cred_secret(cred)}"}, payload, close_client=True,
        )
        if stream is not None:
            async def close_stream():
                for response in responses:
                    await response.aclose()
                await client.aclose()

            stream._on_close = close_stream
        if status == 401:
            current = user_credential(user_id, self.source_id)
            if current and _revision(current) == revision:
                disable_credential(cred["id"])
            obj = {"error": {"message": "Experiential Labs rejected the configured API key.", "code": "invalid_key"}}
            status = 502
        return ChatResult(status=status, body=obj, stream=stream, usage=usage, credential_id=cred["id"], model=model, response_headers={**headers, **gateway_headers})
