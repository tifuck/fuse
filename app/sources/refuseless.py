"""Refuseless pool.

Pay-as-you-go API keys for api.refuseless.com, an OpenAI-compatible host for
refusal-removed open weights. Every user links their own keys. Keys are
private by default (pool_policy.DEFAULT_SHARE): another user is routed onto a
key only after its owner turns Sharing on.

One shared copy of the public catalog is read without a key (a key would add
that account's private models and custom prices, which are not everyone's) and
refreshed every CATALOG_TTL; new upstream models list on their own. Probes and
the usage walk read the catalog and the free `GET /v1/credits`, never a
completion.
"""

from __future__ import annotations

import asyncio
import json
import math
import time

import httpx

from .. import accounts, db
from ..crypto import DecryptionError, encrypt
from ..proxy import UsageStream, openai_chat
from . import pool_policy
from .base import EFFORTS, ChatResult, map_effort
from .pool_pick import Failover, auth_failure_confirmed, explain_empty, note_failure, pick_account, release, reserve
from .pool_session import bind, drop_cache_routing_fields, lookup
from .token_store import log_refresh_failures

BASE_URL = "https://api.refuseless.com/v1"
PUBLIC_PREFIX = "refuseless/"
CATALOG_TTL = 300.0
USAGE_TTL = 300
# Upstream spells "reasoning off" as off; Fuse's picker calls it none.
_EFFORT_IN = {"off": "none"}

_catalog: tuple[float, dict] | None = None
_catalog_ok = False
_catalog_lock: asyncio.Lock | None = None
_refresh_locks: dict[int, asyncio.Lock] = {}


def _headers(key: str) -> dict:
    return {"Authorization": f"Bearer {key}", "Content-Type": "application/json", "Accept": "application/json"}


def _as_float(v) -> float | None:
    if v is None or isinstance(v, bool):
        return None
    try:
        value = float(v)
    except (TypeError, ValueError, OverflowError):
        return None
    return value if math.isfinite(value) else None


def _as_int(v) -> int | None:
    value = _as_float(v)
    return int(value) if value is not None and value > 0 else None


def ensure_refuseless_source() -> int:
    from . import _ensure_typed_source

    return _ensure_typed_source("refuseless", "Refuseless", {"base_url": BASE_URL})


# --- Model ids and catalog ---------------------------------------------------

def public_id(upstream: str) -> str:
    return PUBLIC_PREFIX + upstream


def upstream_id(model: str) -> str:
    mid = (model or "").strip()
    return mid[len(PUBLIC_PREFIX):] if mid.startswith(PUBLIC_PREFIX) else mid


def is_refuseless_model(model: str) -> bool:
    return (model or "").startswith(PUBLIC_PREFIX)


def _effort(value) -> str | None:
    if not isinstance(value, str):
        return None
    value = _EFFORT_IN.get(value.strip().lower(), value.strip().lower())
    return value if value in EFFORTS else None


def _image(item: dict) -> bool | None:
    """Vision support from whichever field the catalog carries; None if none."""
    arch = item.get("architecture") if isinstance(item.get("architecture"), dict) else {}
    for inputs in (item.get("input_modalities"), arch.get("input_modalities"), item.get("modalities")):
        if isinstance(inputs, list):
            return "image" in inputs
    caps = item.get("capabilities") if isinstance(item.get("capabilities"), dict) else {}
    for value in (caps.get("vision"), item.get("vision"), item.get("supports_images")):
        if isinstance(value, bool):
            return value
    return None


def parse_catalog(payload) -> dict:
    """Chat models from `GET /v1/models`, keyed by Refuseless id."""
    by_id: dict[str, dict] = {}
    for item in (payload.get("data") if isinstance(payload, dict) else None) or []:
        if not isinstance(item, dict):
            continue
        mid = item.get("id")
        if not isinstance(mid, str) or not mid.strip() or len(mid) > 200 or any(ord(ch) < 33 for ch in mid):
            continue
        arch = item.get("architecture") if isinstance(item.get("architecture"), dict) else {}
        outputs = item.get("output_modalities", arch.get("output_modalities"))
        if isinstance(outputs, list) and "text" not in outputs:
            continue
        if str(item.get("type") or item.get("task") or "").lower() in ("embedding", "embeddings"):
            continue
        raw = item.get("reasoning_efforts")
        efforts = {_effort(e) for e in raw} if isinstance(raw, list) else set()
        top = item.get("top_provider") if isinstance(item.get("top_provider"), dict) else {}
        by_id[mid] = {
            "name": str(item.get("name") or mid)[:120],
            "reasoning_efforts": [e for e in EFFORTS if e in efforts],
            "reasoning_default": _effort(item.get("reasoning_default")) or "",
            "image": _image(item),
            "context_length": _as_int(item.get("context_length")),
            "max_output_tokens": _as_int(
                item.get("max_output_tokens") or item.get("max_completion_tokens") or top.get("max_completion_tokens")
            ),
            "pricing": item.get("pricing") if isinstance(item.get("pricing"), dict) else {},
            "abliterated": item.get("abliterated") is True,
        }
    return {"by_id": by_id}


# The documented public lineup, served only while no live read has succeeded
# (Cloudflare blocks some hosts). No prices: the documented ones are examples.
_FALLBACK = parse_catalog({"data": [
    {"id": "glm-5.3-abliterated", "context_length": 1048576, "reasoning_efforts": ["low", "high", "max"], "reasoning_default": "low"},
    {"id": "glm-5.3-flash-abliterated", "context_length": 1048576, "reasoning_efforts": ["low", "high", "max"], "reasoning_default": "low"},
    {"id": "deepseek-v4.1-flash-abliterated", "context_length": 1048576, "reasoning_efforts": ["off", "low", "high", "max"], "reasoning_default": "low"},
    {"id": "deepseek-v4.1-flash-fast", "context_length": 1048576, "reasoning_efforts": ["off", "low", "high", "max"], "reasoning_default": "low"},
    {"id": "minimax-m3.1-flash", "context_length": 1048576, "reasoning_efforts": ["low", "high", "max"], "reasoning_default": "low"},
    {"id": "deepseek-v4.1-flash-abliterated-free", "context_length": 524288, "reasoning_efforts": ["off", "low", "high", "max"], "reasoning_default": "low"},
]})


def _remember_windows(catalog: dict) -> None:
    from .. import context_meter as ctxmod

    for mid, rec in catalog["by_id"].items():
        if rec.get("context_length"):
            ctxmod.remember_window("refuseless", public_id(mid), rec["context_length"])
            ctxmod.remember_window("refuseless", mid, rec["context_length"])


def _live_rows() -> list:
    sid = ensure_refuseless_source()
    rows = db.fetchall("SELECT * FROM credentials WHERE source_id=? AND disabled=0 ORDER BY id", (sid,))
    out = []
    for row in rows:
        try:
            if credential_is_authed(row):
                out.append(row)
        except (DecryptionError, accounts.CredentialDataError):
            continue
    return out


async def _fetch_catalog() -> tuple[dict | None, int]:
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            r = await client.get(BASE_URL + "/models", headers={"Accept": "application/json"})
    except httpx.HTTPError:
        return None, 0
    if r.status_code != 200:
        return None, r.status_code
    try:
        payload = r.json()
    except ValueError:
        return None, r.status_code
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        return None, r.status_code
    return parse_catalog(payload), 200


async def fetch_catalog(*, force: bool = False) -> dict:
    """The public lineup and list prices, read without a key.
    An empty live catalog is the catalog; a failed read keeps the last one,
    or the documented lineup before any read has succeeded."""
    global _catalog, _catalog_ok, _catalog_lock
    if not force and _catalog and time.time() - _catalog[0] < CATALOG_TTL:
        return _catalog[1]
    if _catalog_lock is None:
        _catalog_lock = asyncio.Lock()
    async with _catalog_lock:
        if not force and _catalog and time.time() - _catalog[0] < CATALOG_TTL:
            return _catalog[1]
        catalog, _status = await _fetch_catalog()
        if catalog is not None:
            _catalog = (time.time(), catalog)
            _catalog_ok = True
            _remember_windows(catalog)
            return catalog
        _catalog_ok = False
        if _catalog:
            return _catalog[1]
        _remember_windows(_FALLBACK)
        return _FALLBACK


def cached_catalog() -> dict:
    return _catalog[1] if _catalog else _FALLBACK


def catalog_available() -> bool:
    return _catalog_ok


def catalog_record(model: str) -> dict | None:
    return ((cached_catalog() or {}).get("by_id") or {}).get(upstream_id(model))


async def refresh_due() -> None:
    """Refresh-loop job: re-read the lineup once it ages past CATALOG_TTL."""
    if _catalog and time.time() - _catalog[0] < CATALOG_TTL:
        return
    if _live_rows():
        await fetch_catalog(force=True)


def efforts_for(model: str | None) -> tuple[str, ...]:
    return tuple((catalog_record(model or "") or {}).get("reasoning_efforts") or ())


def default_effort(model: str | None) -> str:
    rec = catalog_record(model or "") or {}
    default = rec.get("reasoning_default") or ""
    return default if default in (rec.get("reasoning_efforts") or ()) else ""


# --- Credentials and access -----------------------------------------------------

def _billing(extra: dict | None) -> dict:
    extra = extra if isinstance(extra, dict) else {}
    return extra.get("billing") if isinstance(extra.get("billing"), dict) else {}


def credential_is_authed(cred, extra: dict | None = None) -> bool:
    try:
        if cred["disabled"]:
            return False
    except (KeyError, IndexError, TypeError):
        return False
    err = str(_billing(extra if extra is not None else accounts.cred_extra(cred)).get("last_error") or "")
    return "401" not in err


def out_of_credits(extra: dict | None) -> bool:
    """A fresh balance read says this key cannot reserve anything."""
    available = _as_float(_billing(extra).get("available"))
    return available is not None and available <= 0


def refuseless_own_access(user_id: int) -> dict:
    sid = ensure_refuseless_source()
    rows = db.fetchall_memo(
        "SELECT * FROM credentials WHERE source_id=? AND donated_by=? AND disabled=0", (sid, user_id)
    )
    if not rows:
        return {"ok": False, "reason": "none"}
    for row in rows:
        try:
            if credential_is_authed(row):
                return {"ok": True, "reason": "ok"}
        except (DecryptionError, accounts.CredentialDataError):
            continue
    return {"ok": False, "reason": "unauthed"}


def lends_any(user_id: int) -> bool:
    """One of this user's keys is valid and has credit to lend."""
    sid = ensure_refuseless_source()
    rows = db.fetchall_memo(
        "SELECT * FROM credentials WHERE source_id=? AND donated_by=? AND disabled=0", (sid, user_id)
    )
    for row in rows:
        try:
            extra = accounts.cred_extra(row)
        except (DecryptionError, accounts.CredentialDataError):
            continue
        if credential_is_authed(row, extra) and not out_of_credits(extra):
            return True
    return False


def refuseless_access_state(user_id: int) -> dict:
    own = refuseless_own_access(user_id)
    if own["ok"]:
        return own
    return pool_policy.with_open_access(user_id, "refuseless", own, credential_is_authed)


def user_has_refuseless(user_id: int) -> bool:
    return refuseless_access_state(user_id)["ok"]


def disable_credential(cred_id: int, *, extra: str | None = None) -> None:
    accounts.disable_credential(cred_id, extra=extra)


def note_pool_invoke(cred_id: int) -> None:
    db.execute("UPDATE credentials SET pool_invokes = COALESCE(pool_invokes, 0) + 1 WHERE id=?", (cred_id,))


async def resolve(model: str, user_id: int | None) -> str | None:
    if not is_refuseless_model(model):
        return None
    catalog = cached_catalog() or await fetch_catalog()
    upstream = upstream_id(model)
    return upstream if upstream in catalog["by_id"] else None


def key_shape(raw: str) -> str:
    key = (raw or "").strip()
    if key.lower().startswith("bearer "):
        key = key[7:].strip()
    if 8 <= len(key) <= 512 and all(33 <= ord(ch) <= 126 for ch in key):
        return key
    return ""


def _find_key(source_id: int, key: str):
    for row in db.fetchall("SELECT * FROM credentials WHERE source_id=?", (source_id,)):
        try:
            if accounts.cred_secret(row) == key:
                return row
        except Exception:
            continue
    return None


def save_key(source_id: int, key: str, donated_by: int, *, label: str = "") -> int:
    """A repeat link re-enables the row it already has. An active key stays
    with its owner; another user pasting the same key never takes it."""
    existing = _find_key(source_id, key)
    if existing and not existing["disabled"] and int(existing["donated_by"] or 0) != int(donated_by):
        raise ValueError("pooled")
    name = (label or "").strip()[:80] or "Refuseless key"
    if existing:
        db.execute(
            "UPDATE credentials SET disabled=0, donated_by=?, label=?, secret=?, extra=? WHERE id=?",
            (donated_by, name, encrypt(key), encrypt(json.dumps({})), existing["id"]),
        )
        return int(existing["id"])
    return accounts.add_credential(source_id, name, key, extra={}, donated_by=donated_by)


# --- Credits --------------------------------------------------------------------

def parse_credits(data: dict) -> dict:
    """`GET /v1/credits`: USD balance, holds, and what a request can reserve."""
    balance = _as_float(data.get("balance"))
    available = _as_float(data.get("available"))
    reserved = _as_float(data.get("reserved"))
    limit = _as_float(data.get("key_limit"))
    parsed = {
        "plan_type": "credits" if (available or 0) > 0 else "no credits",
        "period_type": "Credits",
        "balance": balance,
        "available": available,
        "reserved": reserved,
        "key_limit": limit,
    }
    if available is not None and available > 0:
        parsed["prepaid_cents"] = int(round(available * 100))
    return parsed


async def fetch_credits(key: str) -> tuple[dict | None, str]:
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.get(BASE_URL + "/credits", headers=_headers(key))
    except httpx.HTTPError:
        return None, "fetch failed"
    if r.status_code != 200:
        return None, f"http {r.status_code}"
    try:
        data = r.json()
    except ValueError:
        return None, "invalid JSON"
    if not isinstance(data, dict):
        return None, "invalid JSON"
    return parse_credits(data), ""


def store_billing(cred_id: int, parsed: dict, *, error: str = "") -> dict:
    row = db.fetchone("SELECT * FROM credentials WHERE id=?", (cred_id,))
    if not row:
        return {}
    extra = accounts.cred_extra(row)
    now = db.now()
    if error:
        billing = {**_billing(extra), "checked_at": now, "last_error": error[:200]}
    else:
        billing = {**parsed, "checked_at": now, "fetched_at": now, "last_error": ""}
    extra["billing"] = billing
    accounts.update_extra(cred_id, extra)
    return billing


def _fresh(billing: dict) -> bool:
    from datetime import datetime, timedelta, timezone

    raw = str(billing.get("checked_at") or "").replace("Z", "").split("+")[0]
    try:
        when = datetime.fromisoformat(raw).replace(tzinfo=timezone.utc)
    except ValueError:
        return False
    return datetime.now(timezone.utc) - when < timedelta(seconds=USAGE_TTL)


def _refresh_lock(cred_id: int) -> asyncio.Lock:
    lock = _refresh_locks.get(cred_id)
    if lock is None:
        lock = _refresh_locks[cred_id] = asyncio.Lock()
    return lock


async def refresh_usage(cred, *, force: bool = False) -> dict | None:
    async with _refresh_lock(int(cred["id"])):
        row = db.fetchone("SELECT * FROM credentials WHERE id=?", (cred["id"],))
        if not row or row["disabled"]:
            return None
        billing = _billing(accounts.cred_extra(row))
        if not force and _fresh(billing):
            return billing
        parsed, err = await fetch_credits(accounts.cred_secret(row))
        if parsed is None:
            return store_billing(row["id"], {}, error=err or "fetch failed")
        return store_billing(row["id"], parsed)


async def refresh_pool_usage(*, force: bool = False) -> None:
    rows = db.fetchall(
        "SELECT c.* FROM credentials c JOIN sources s ON s.id=c.source_id WHERE s.type='refuseless' AND c.disabled=0"
    )
    if not rows:
        return
    results = await asyncio.gather(*(refresh_usage(c, force=force) for c in rows), return_exceptions=True)
    log_refresh_failures("Refuseless credits refresh", rows, results)


async def add_key(source_id: int, raw: str, donated_by: int, *, label: str = "") -> tuple[int | None, str]:
    """A pasted key, validated with the free credits endpoint, never a completion."""
    key = key_shape(raw)
    if not key:
        return None, "Enter a Refuseless API key."
    parsed, err = await fetch_credits(key)
    if parsed is None:
        if err == "http 401":
            return None, "Refuseless rejected that key. Copy an active key from your Refuseless dashboard."
        return None, "Could not validate this key. Check the connection and try again."
    try:
        cred_id = save_key(source_id, key, donated_by, label=label)
    except ValueError:
        return None, "That Refuseless key is already linked to another account."
    store_billing(cred_id, parsed)
    if not catalog_available():
        await fetch_catalog(force=True)
    return cred_id, ""


# --- Chat -----------------------------------------------------------------------

def _clean_message(message):
    if not isinstance(message, dict):
        return message
    out = {k: v for k, v in message.items() if not k.startswith("_fuse") and k != "claude_thinking"}
    if isinstance(out.get("content"), list):
        out["content"] = [
            {k: v for k, v in part.items() if k != "cache_control"} if isinstance(part, dict) else part
            for part in out["content"]
        ]
    return out


def chat_payload(body: dict, upstream: str, rec: dict) -> dict:
    """OpenAI Chat Completions as Refuseless takes it: tools, tool_choice and
    response_format pass through; effort is the model's nearest level (off for
    none); max_tokens is clamped to the model's output limit, and left unset
    when the caller sent none so Refuseless uses the model's full output."""
    payload = {
        k: v for k, v in drop_cache_routing_fields(body).items()
        if not k.startswith("_fuse") and k not in ("session_id", "models", "effort")
    }
    if isinstance(payload.get("messages"), list):
        payload["messages"] = [_clean_message(m) for m in payload["messages"]]
    payload["model"] = upstream
    effort = payload.pop("reasoning_effort", None)
    reasoning = payload.pop("reasoning", None)
    thinking = payload.pop("thinking", None)
    if effort is None and isinstance(reasoning, dict):
        effort = reasoning.get("effort")
    if effort is None and isinstance(thinking, dict) and thinking.get("type") == "disabled":
        effort = "none"
    mapped = map_effort(_effort(effort), rec.get("reasoning_efforts") or ())
    if mapped:
        payload["reasoning_effort"] = "off" if mapped == "none" else mapped
    cap = rec.get("max_output_tokens")
    for field in ("max_tokens", "max_completion_tokens"):
        value = payload.get(field)
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            payload.pop(field, None)
        elif cap and value > cap:
            payload[field] = cap
    return payload


def _ran_out_of_credits(tail: bytes) -> bool:
    """The stream ended on Refuseless's terminal `insufficient_credits` event."""
    for line in tail.decode(errors="replace").splitlines():
        line = line.strip()
        if not line.startswith("data:") or "insufficient_credits" not in line:
            continue
        try:
            obj = json.loads(line[5:].strip())
        except ValueError:
            continue
        err = obj.get("error") if isinstance(obj, dict) else None
        if isinstance(err, dict) and err.get("code") == "insufficient_credits":
            return True
    return False


class RefuselessSource:
    type_id = "refuseless"

    def __init__(self, row, cfg: dict, creds):
        self.source_id = row["id"]
        self.name = row["name"]
        self.cfg = cfg
        self.creds = list(creds)

    async def models(self) -> list[str]:
        catalog = await fetch_catalog()
        if not catalog_available() and not catalog["by_id"] and _live_rows():
            raise RuntimeError("Refuseless catalog unavailable")
        return [public_id(mid) for mid in catalog["by_id"]]

    async def chat(self, body: dict, *, session_id: str | None = None, user_id: int | None = None, affinity_id: str | None = None) -> ChatResult:
        upstream = upstream_id(str(body.get("model") or ""))
        catalog = cached_catalog() or await fetch_catalog()
        rec = catalog["by_id"].get(upstream)
        if rec is None:
            return ChatResult(status=404, body={"error": {"message": "That model is not in Refuseless's catalog."}}, model=upstream)
        rows = pool_policy.filter_credentials(
            db.fetchall("SELECT * FROM credentials WHERE source_id=? AND disabled=0", (self.source_id,)),
            "refuseless",
            user_id,
        )
        uid = int(user_id) if user_id is not None else None
        prepared = chat_payload(body, upstream, rec)
        tried: set[int] = set()
        attempts: list[dict] = []
        last = ChatResult(
            status=403,
            body={"error": {"message": "No Refuseless key can serve this request for you. Link your own key on Sources.", "type": "permission_error"}},
            model=upstream,
        )
        prefer_id = lookup("refuseless", session_id)
        hint_id = lookup("refuseless", affinity_id) if prefer_id is None and affinity_id else None
        primary_id = pool_policy.primary_account(uid, "refuseless")
        failover = Failover()
        why: list = []
        while True:
            picked = pick_account(
                rows,
                kind="refuseless",
                exclude=tried,
                is_authed=credential_is_authed,
                no_plan=lambda _cred, extra: out_of_credits(extra),
                prefer_id=prefer_id,
                owner_id=uid,
                hint_id=hint_id,
                primary_id=primary_id,
                why=why,
            )
            if not picked:
                if not tried and any(reason == "cooling" for _, reason in why):
                    last = explain_empty("refuseless", why, model=upstream)
                if attempts and last.credential_id == attempts[-1].get("credential_id"):
                    attempts.pop()
                last.attempts = attempts
                return last
            cred, _extra = picked
            cid = int(cred["id"])
            tried.add(cid)
            note_pool_invoke(cid)
            payload = pool_policy.lent_payload(prepared, cred, uid)
            request_ids: list[str] = []

            async def capture(response, request_ids=request_ids):
                rid = response.headers.get("x-request-id") or ""
                if rid and all(32 <= ord(ch) < 127 for ch in rid):
                    request_ids.append(rid[:128])

            client = httpx.AsyncClient(timeout=httpx.Timeout(600.0, connect=15.0), event_hooks={"response": [capture]})
            stream_reserved = False
            try:
                reserve(cid)
                status, obj, stream, usage, headers = await openai_chat(
                    client, BASE_URL + "/chat/completions", _headers(accounts.cred_secret(cred)), payload, close_client=True
                )
                if request_ids:
                    usage["provider_request_id"] = request_ids[-1]
                if stream is not None:
                    stream_reserved = True
                    failover.served()
                    return self._stream_result(stream, usage, cid, session_id, upstream, attempts)
                last = ChatResult(status=status, body=obj, usage=usage, credential_id=cid, model=upstream, response_headers=headers)
                if usage.get("failure_phase") in ("request_sent", "response_received"):
                    last.attempts = attempts
                    return last
                retry = False
                if status == 401:
                    if auth_failure_confirmed(cid):
                        disable_credential(cid, extra=cred["extra"])
                    last.status = 502
                    last.body = {"error": {"message": "Refuseless rejected this API key. Its owner needs to link a new one.", "code": "invalid_key"}}
                    retry = True
                elif status == 402:
                    # Out of credits on this key; another key may still have some.
                    store_billing(cid, parse_credits({"available": 0}))
                    note_failure(cid, cooldown_seconds=300, limit=True)
                    retry = True
                elif status >= 500:
                    retry = failover.server_error(cid, status, 15, phase=usage.get("failure_phase", ""))
                # 429 is model_busy / model_warming: the model, not the key, so
                # another key would hit the same wall. It goes back with its
                # retry-after for the caller's own backoff.
                if retry:
                    attempts.append({"credential_id": cid, "status": status, "usage": usage or {}})
                    last.attempts = attempts
                    continue
                if status == 200:
                    failover.served()
                    bind("refuseless", session_id, cid)
                last.attempts = attempts
                return last
            finally:
                if not stream_reserved:
                    release(cid)

    def _stream_result(self, stream, collected: dict, cid: int, session_id: str | None, model: str, attempts: list[dict]) -> ChatResult:
        async def run():
            tail = b""
            try:
                async for chunk in stream:
                    tail = (tail + chunk)[-8192:]
                    yield chunk
            finally:
                close = getattr(stream, "aclose", None)
                if close is not None:
                    await close()
                status = (collected or {}).get("stream_status")
                if status == "complete":
                    bind("refuseless", session_id, cid)
                elif status == "failed":
                    if _ran_out_of_credits(tail):
                        store_billing(cid, parse_credits({"available": 0}))
                    note_failure(cid, cooldown_seconds=5, affinity_safe=True)

        result = ChatResult(status=200, stream=UsageStream(run(), collected, on_close=lambda: release(cid)), credential_id=cid, model=model)
        result.attempts = attempts
        return result
