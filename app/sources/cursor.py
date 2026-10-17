"""Cursor subscription pool, signed in the way `cursor-agent login` does.

Login is Cursor's deep-link PKCE flow: the user opens
cursor.com/loginDeepControl with a challenge and a fresh uuid, approves, and
Fuse picks the tokens up by polling api2.cursor.sh/auth/poll with the
verifier. No callback, no code to paste. Access tokens are JWTs that last
about two months; the refresh token renews them at /oauth/token and does not
rotate. A user API key from the Cursor dashboard is the other way in: it is
exchanged for the same token pair at /auth/exchange_user_api_key.

Usage is DashboardService.GetCurrentPeriodUsage (plan percent, on-demand
spend). Chat runs on the agent backend (see cursor_agent). Probes are
catalog and usage only; never a completion.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import math
import secrets
import time
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx

from .. import accounts, db
from ..async_work import durable_call
from ..config import settings
from ..crypto import encrypt
from ..proxy import UsageStream, usage_from_obj
from .base import ChatResult
from .cursor_agent import TurnError, run_turn
from .grok import apply_usage_guard, is_held
from .pool_pick import Failover, auth_failure_confirmed, note_failure, pick_account, release, reserve
from . import pool_policy
from .pool_session import bind, lookup
from .token_store import log_refresh_failures, persist_rotated, shield_refresh

PREFIX = "cursor/"
USAGE_TTL = 300
# Cursor's Auto router: on every plan, so it is the one seed that is always real.
CURSOR_MODELS = ["cursor/auto"]
_models_cache: tuple[float, list[str]] | None = None
_labels: dict[str, str] = {}
_refresh_locks: dict[int, asyncio.Lock] = {}
_CHAT_TIMEOUT = httpx.Timeout(300.0, connect=15.0, write=30.0, pool=15.0)


def _refresh_lock(cred_id: int) -> asyncio.Lock:
    lock = _refresh_locks.get(cred_id)
    if lock is None:
        lock = asyncio.Lock()
        _refresh_locks[cred_id] = lock
    return lock


def disable_credential(cred_id: int, *, extra: str | None = None) -> None:
    accounts.disable_credential(cred_id, extra=extra)


def ensure_cursor_source() -> int:
    row = db.fetchone("SELECT id FROM sources WHERE type='cursor'")
    if row:
        return row["id"]
    with db.tx() as conn:
        conn.execute(
            "INSERT INTO sources (type, name, enabled, config, created_at) VALUES (?,?,?,?,?)",
            ("cursor", "Cursor", 1, "{}", db.now()),
        )
        return db.last_id(conn)


def is_cursor_model(model: str) -> bool:
    return (model or "").strip().startswith(PREFIX)


def upstream_id(model: str) -> str:
    mid = (model or "").strip()
    return mid[len(PREFIX):] if mid.startswith(PREFIX) else mid


def model_label(model: str) -> str:
    return _labels.get(model, "")


def _base() -> str:
    return settings.cursor_api_base.rstrip("/")


def _headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Connect-Protocol-Version": "1",
        "x-cursor-client-type": "cli",
        "x-cursor-client-version": settings.cursor_client_version,
        # Privacy mode: Cursor does not store or train on these requests.
        "x-ghost-mode": "true",
        # Hide Cursor's native tools; the model sees only the caller's.
        "x-cursor-agent-allowed-tools": "mcp_tool_call",
    }


def _row_get(row, key, default=None):
    try:
        if isinstance(row, dict):
            return row.get(key, default)
        if key in row.keys():
            val = row[key]
            return default if val is None else val
    except Exception:
        return default
    return default


def _as_float(v) -> float | None:
    if v is None or isinstance(v, bool):
        return None
    try:
        value = float(v)
        return value if math.isfinite(value) else None
    except (TypeError, ValueError, OverflowError):
        return None


def _parse_ts(ts: str) -> datetime | None:
    raw = str(ts or "").replace("Z", "")
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f"):
        try:
            return datetime.strptime(raw.split("+")[0], fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _iso(when: datetime) -> str:
    return when.strftime("%Y-%m-%dT%H:%M:%SZ")


def _ms_iso(value) -> str:
    try:
        return _iso(datetime.fromtimestamp(int(value) / 1000, tz=timezone.utc))
    except (TypeError, ValueError, OverflowError, OSError):
        return ""


def jwt_claims(token: str) -> dict:
    try:
        part = token.split(".")[1]
        data = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
    except (IndexError, ValueError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def _expiry(token: str) -> str:
    exp = jwt_claims(token).get("exp")
    if isinstance(exp, (int, float)) and not isinstance(exp, bool):
        try:
            return _iso(datetime.fromtimestamp(exp, tz=timezone.utc))
        except (OverflowError, OSError, ValueError):
            pass
    # No exp claim: assume an hour so the token is renewed rather than trusted.
    return _iso(datetime.now(timezone.utc) + timedelta(hours=1))


def _valid_token(value) -> bool:
    return isinstance(value, str) and 16 <= len(value) <= 16_384 and all(32 < ord(c) < 127 for c in value)


# --- Access ---------------------------------------------------------------------

def billing_has_subscription(billing: dict | None) -> bool:
    return isinstance(billing, dict) and bool(billing.get("fetched_at"))


def credential_is_authed(cred, extra: dict | None = None) -> bool:
    if _row_get(cred, "disabled"):
        return False
    extra = extra if extra is not None else accounts.cred_extra(cred)
    if not extra.get("access_token"):
        return False
    billing = extra.get("billing") if isinstance(extra.get("billing"), dict) else {}
    err = str(billing.get("last_error") or "").lower()
    if "401" in err and not (extra.get("refresh_token") or extra.get("api_key")):
        return False
    return True


def cursor_own_access(user_id: int) -> dict:
    sid = ensure_cursor_source()
    rows = db.fetchall(
        "SELECT * FROM credentials WHERE source_id=? AND donated_by=? AND disabled=0",
        (sid, user_id),
    )
    if not rows:
        return {"ok": False, "reason": "none"}
    for row in rows:
        if credential_is_authed(row, accounts.cred_extra(row)):
            return {"ok": True, "reason": "ok"}
    return {"ok": False, "reason": "unauthed"}


def _cursor_live(row) -> bool:
    return not is_held(row) and credential_is_authed(row, accounts.cred_extra(row))


def cursor_access_state(user_id: int) -> dict:
    return pool_policy.with_open_access(user_id, "cursor", cursor_own_access(user_id), _cursor_live)


def user_has_cursor(user_id: int) -> bool:
    return cursor_access_state(user_id)["ok"]


def account_email(extra: dict | None) -> str:
    extra = extra if isinstance(extra, dict) else {}
    text = str(extra.get("email") or "").strip()
    if text and len(text) <= 254 and " " not in text and text.count("@") == 1:
        return text
    return ""


def note_pool_invoke(cred_id: int) -> None:
    db.execute("UPDATE credentials SET pool_invokes = COALESCE(pool_invokes, 0) + 1 WHERE id=?", (cred_id,))


# --- Sign-in --------------------------------------------------------------------

def start_login() -> dict:
    """One `cursor-agent login` round: the browser link and the verifier
    that redeems it. The verifier never leaves the server."""
    verifier = secrets.token_urlsafe(32)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    login_id = str(uuid.uuid4())
    query = urlencode({"challenge": challenge, "uuid": login_id, "mode": "login", "redirectTarget": "cli"})
    return {
        "uuid": login_id,
        "verifier": verifier,
        "auth_url": f"{settings.cursor_login_url}?{query}",
        "interval": 3,
    }


async def poll_login(login_id: str, verifier: str) -> dict:
    """{"pending": True} until the user approves, then the token pair.
    {"error": ...} is terminal."""
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.get(
            _base() + "/auth/poll",
            params={"uuid": login_id, "verifier": verifier},
            headers={"x-cursor-client-type": "cli", "x-cursor-client-version": settings.cursor_client_version},
        )
    if r.status_code in (202, 404):
        return {"pending": True}
    if r.status_code in (401, 403):
        return {"error": "denied"}
    if r.status_code != 200:
        return {"pending": True, "retry": f"http {r.status_code}"}
    try:
        data = r.json()
    except ValueError:
        return {"pending": True, "retry": "invalid JSON"}
    access = data.get("accessToken") if isinstance(data, dict) else None
    if not access:
        return {"pending": True}
    return {"access_token": access, "refresh_token": data.get("refreshToken") or "", "auth_id": str(data.get("authId") or "")}


def _cas_update_tokens(cred_id: int, old_extra_raw: str, new_secret: str, new_extra: dict) -> bool:
    with db.tx() as conn:
        cur = conn.execute(
            "UPDATE credentials SET secret=?, extra=? WHERE id=? AND extra=?",
            (new_secret, encrypt(json.dumps(new_extra)), cred_id, old_extra_raw),
        )
        return cur.rowcount > 0


def store_tokens(source_id: int, access: str, refresh: str, donated_by: int | None, *, api_key: str = "", label: str = "") -> int:
    """Save a sign-in. The same Cursor account linked again by its donor
    updates that row instead of adding a second one."""
    if not _valid_token(access) or (refresh and not _valid_token(refresh)):
        raise ValueError("token")
    account_id = str(jwt_claims(access).get("sub") or "")[:128]
    extra = {
        "auth_kind": "api" if api_key else "oauth",
        "access_token": access,
        "refresh_token": refresh or "",
        "expires_at": _expiry(access),
        "account_id": account_id,
    }
    if api_key:
        extra["api_key"] = api_key
    name = (label or "").strip()[:80] or "Cursor account"
    if account_id:
        rows = db.fetchall(
            "SELECT * FROM credentials WHERE source_id=? AND donated_by IS ? ORDER BY disabled, id",
            (source_id, donated_by),
        )
        for row in rows:
            current = accounts.cred_extra(row)
            if str(current.get("account_id") or "") != account_id:
                continue
            current.pop("api_key", None)
            current.update(extra)
            db.execute(
                "UPDATE credentials SET disabled=0, label=?, secret=?, extra=? WHERE id=?",
                (name if label else (row["label"] or name), encrypt(access), encrypt(json.dumps(current)), row["id"]),
            )
            return int(row["id"])
    return accounts.add_credential(source_id, name, access, extra=extra, donated_by=donated_by)


_KEY_REJECTED = "Cursor rejected that API key."


async def exchange_api_key(key: str) -> tuple[dict | None, str]:
    async with httpx.AsyncClient(timeout=20) as client:
        try:
            r = await client.post(
                _base() + "/auth/exchange_user_api_key",
                json={},
                headers={**_headers(key), "Content-Type": "application/json"},
            )
        except httpx.HTTPError:
            return None, "Could not reach Cursor."
    if r.status_code in (401, 403):
        return None, _KEY_REJECTED
    if r.status_code != 200:
        return None, f"Cursor answered HTTP {r.status_code}."
    try:
        data = r.json()
    except ValueError:
        return None, "Cursor returned an invalid response."
    if not isinstance(data, dict) or not _valid_token(data.get("accessToken")):
        return None, "Cursor did not return a token for that key."
    return {"access_token": data["accessToken"], "refresh_token": data.get("refreshToken") or ""}, ""


async def add_api_key(source_id: int, raw: str, donated_by: int, *, label: str = "") -> tuple[int | None, str]:
    key = (raw or "").strip()
    if not _valid_token(key):
        return None, "Enter a user API key from cursor.com/dashboard (API Keys)."
    tokens, err = await exchange_api_key(key)
    if tokens is None:
        return None, err
    cred_id = store_tokens(source_id, tokens["access_token"], tokens["refresh_token"], donated_by, api_key=key, label=label)
    row = db.fetchone("SELECT * FROM credentials WHERE id=?", (cred_id,))
    if row:
        await refresh_quota(row, force=True)
    return cred_id, ""


# --- Token refresh --------------------------------------------------------------

async def _refresh_one_unlocked(cred) -> bool:
    row = db.fetchone("SELECT * FROM credentials WHERE id=?", (cred["id"],))
    if not row or row["disabled"]:
        return False
    extra = accounts.cred_extra(row)
    if extra.get("api_key"):
        # A dashboard key is not a single-use refresh token: re-exchanging it
        # is always safe, so it is not gated on OAuth refresh ownership.
        tokens, err = await exchange_api_key(extra["api_key"])
        if tokens is None:
            if err == _KEY_REJECTED:
                disable_credential(row["id"], extra=row["extra"])
            else:
                note_failure(row["id"], cooldown_seconds=30)
            return False
        access, refresh = tokens["access_token"], tokens["refresh_token"] or extra.get("refresh_token", "")
    else:
        if not settings.oauth_refresh_enabled or not extra.get("refresh_token"):
            return False
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                r = await client.post(
                    _base() + "/oauth/token",
                    json={
                        "grant_type": "refresh_token",
                        "client_id": settings.cursor_client_id,
                        "refresh_token": extra["refresh_token"],
                    },
                )
            data = r.json()
        except (httpx.HTTPError, ValueError):
            note_failure(row["id"], cooldown_seconds=30)
            return False
        if not isinstance(data, dict):
            note_failure(row["id"], cooldown_seconds=30)
            return False
        if data.get("shouldLogout"):
            disable_credential(row["id"], extra=row["extra"])
            return False
        if r.status_code != 200 or not _valid_token(data.get("access_token")):
            note_failure(row["id"], cooldown_seconds=60 if r.status_code == 429 else 30)
            return False
        access, refresh = data["access_token"], extra["refresh_token"]
    rotated = {"access_token": access, "refresh_token": refresh, "expires_at": _expiry(access)}
    extra.update(rotated)
    return await durable_call(
        persist_rotated, _cas_update_tokens, int(row["id"]), row["extra"], encrypt(access), extra, rotated,
    )


async def refresh_one(cred) -> bool:
    async def run():
        async with _refresh_lock(int(cred["id"])):
            return await _refresh_one_unlocked(cred)

    return await shield_refresh(run())


def _due(extra: dict, *, within: timedelta) -> bool:
    if not (extra.get("refresh_token") or extra.get("api_key")):
        return False
    when = _parse_ts(str(extra.get("expires_at") or ""))
    return not when or when <= datetime.now(timezone.utc) + within


async def refresh_due() -> None:
    rows = db.fetchall(
        "SELECT c.* FROM credentials c JOIN sources s ON s.id=c.source_id WHERE s.type='cursor' AND c.disabled=0"
    )
    due = [row for row in rows if _due(accounts.cred_extra(row), within=timedelta(minutes=10))]
    if due:
        results = await asyncio.gather(*(refresh_one(c) for c in due), return_exceptions=True)
        log_refresh_failures("Cursor token refresh", due, results)


# --- Usage ----------------------------------------------------------------------

def parse_usage_payload(usage: dict, plan: dict | None = None) -> dict:
    """GetCurrentPeriodUsage (+ GetPlanInfo) as a billing snapshot. Amounts
    are cents; cycle bounds are unix milliseconds."""
    plan_usage = usage.get("planUsage") if isinstance(usage.get("planUsage"), dict) else {}
    total = _as_float(plan_usage.get("totalPercentUsed"))
    limit = _as_float(plan_usage.get("limit"))
    remaining = _as_float(plan_usage.get("remaining"))
    if total is None and limit:
        total = (limit - (remaining or 0.0)) / limit * 100.0
    products = []
    for name, key in (("Auto + Composer", "autoPercentUsed"), ("API", "apiPercentUsed")):
        pct = _as_float(plan_usage.get(key))
        if pct is not None:
            products.append({"product": name, "usage_percent": max(0.0, min(100.0, pct))})
    spend = usage.get("spendLimitUsage") if isinstance(usage.get("spendLimitUsage"), dict) else {}
    info = (plan or {}).get("planInfo") if isinstance((plan or {}).get("planInfo"), dict) else {}
    return {
        "credit_usage_percent": max(0.0, min(100.0, total)) if total is not None else None,
        "products": products,
        "plan_type": str(info.get("planName") or "")[:32].lower(),
        "period_type": "MONTH",
        "period_start": _ms_iso(usage.get("billingCycleStart")),
        "period_end": _ms_iso(usage.get("billingCycleEnd")),
        "on_demand_used_cents": int(_as_float(spend.get("individualUsed")) or 0),
        "on_demand_cap_cents": int(_as_float(spend.get("individualLimit")) or 0),
    }


async def _unary(client: httpx.AsyncClient, path: str, token: str) -> tuple[int, dict]:
    r = await client.post(_base() + path, json={}, headers={**_headers(token), "Content-Type": "application/json"})
    try:
        data = r.json()
    except ValueError:
        data = {}
    return r.status_code, data if isinstance(data, dict) else {}


async def fetch_usage(token: str) -> tuple[dict | None, str, str]:
    """(billing, email, error). The plan name and email are best effort."""
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=False) as client:
            status, usage = await _unary(client, "/aiserver.v1.DashboardService/GetCurrentPeriodUsage", token)
            if status != 200:
                return None, "", f"http {status}"
            _, plan = await _unary(client, "/aiserver.v1.DashboardService/GetPlanInfo", token)
            _, me = await _unary(client, "/aiserver.v1.AuthService/GetEmail", token)
    except httpx.HTTPError:
        return None, "", "fetch failed"
    return parse_usage_payload(usage, plan), str(me.get("email") or ""), ""


def _usage_fresh(billing: dict) -> bool:
    when = _parse_ts(str(billing.get("checked_at") or billing.get("fetched_at") or ""))
    return bool(when) and datetime.now(timezone.utc) - when < timedelta(seconds=USAGE_TTL)


def _store_billing(cred_id: int, parsed: dict, *, error: str = "", email: str = "") -> dict:
    row = db.fetchone("SELECT * FROM credentials WHERE id=?", (cred_id,))
    extra = accounts.cred_extra(row) if row else {}
    billing = extra.get("billing") if isinstance(extra.get("billing"), dict) else {}
    now = db.now()
    billing["checked_at"] = now
    if email and account_email({"email": email}):
        extra["email"] = email
    if error:
        billing["last_error"] = error[:200]
        extra["billing"] = billing
        if row:
            accounts.update_extra(cred_id, extra)
        return billing
    billing.update(parsed)
    billing["fetched_at"] = now
    billing["last_error"] = ""
    extra["billing"] = billing
    guard_enabled = pool_policy.guard_enabled("cursor")
    extra, held_until, last_pct = apply_usage_guard(row, extra, parsed, enabled=guard_enabled)
    extra["billing"] = billing
    accounts.update_extra(cred_id, extra, last_usage_percent=last_pct, usage_percent_at=now, pool_held_until=held_until)
    return billing


async def refresh_quota(cred, *, force: bool = False) -> dict | None:
    async with _refresh_lock(int(cred["id"])):
        row = db.fetchone("SELECT * FROM credentials WHERE id=?", (cred["id"],))
        if not row or row["disabled"]:
            return None
        extra = accounts.cred_extra(row)
        billing = extra.get("billing") if isinstance(extra.get("billing"), dict) else {}
        if not force and _usage_fresh(billing):
            return billing
        if _due(extra, within=timedelta(minutes=5)) and await _refresh_one_unlocked(row):
            row = db.fetchone("SELECT * FROM credentials WHERE id=?", (cred["id"],))
            extra = accounts.cred_extra(row)
        parsed, email, err = await fetch_usage(extra.get("access_token") or "")
        if parsed is None:
            return _store_billing(row["id"], {}, error=err)
        return _store_billing(row["id"], parsed, email=email)


async def refresh_pool_quota(*, force: bool = False) -> None:
    rows = db.fetchall(
        "SELECT c.* FROM credentials c JOIN sources s ON s.id=c.source_id WHERE s.type='cursor' AND c.disabled=0"
    )
    if not rows:
        return
    results = await asyncio.gather(*(refresh_quota(c, force=force) for c in rows), return_exceptions=True)
    log_refresh_failures("Cursor usage refresh", rows, results)


# --- Catalog --------------------------------------------------------------------

def _remember_catalog(models: list) -> list[str]:
    global _models_cache
    names: list[str] = []
    for item in models:
        mid = str(item.get("modelId") or "") if isinstance(item, dict) else ""
        if not mid:
            continue
        fid = PREFIX + mid
        if fid not in names:
            names.append(fid)
        label = str(item.get("displayName") or item.get("displayNameShort") or "")
        if label:
            _labels[fid] = label[:80]
    prior = list(_models_cache[1]) if _models_cache else list(CURSOR_MODELS)
    if not names:
        return prior
    # Upstream order first, then ids an earlier listing had.
    names += [mid for mid in prior if mid not in names]
    _models_cache = (time.time(), names)
    return names


async def fetch_catalog(token: str) -> list[str]:
    if _models_cache and time.time() - _models_cache[0] < 60:
        return list(_models_cache[1])
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            status, data = await _unary(client, "/agent.v1.AgentService/GetUsableModels", token)
    except httpx.HTTPError:
        status, data = 0, {}
    models = data.get("models") if status == 200 and isinstance(data.get("models"), list) else []
    return _remember_catalog(models)


# --- Chat -----------------------------------------------------------------------

def _sse(obj: dict) -> bytes:
    return f"data: {json.dumps(obj, separators=(',', ':'))}\n\n".encode()


def _usage_obj(raw: dict | None) -> dict:
    raw = raw or {}
    prompt = int(raw.get("prompt_tokens") or 0)
    completion = int(raw.get("completion_tokens") or 0)
    return {"prompt_tokens": prompt, "completion_tokens": completion, "total_tokens": prompt + completion}


def _tool_call(index: int, call: dict) -> dict:
    return {"index": index, "id": call["id"], "type": "function",
            "function": {"name": call["name"], "arguments": call["arguments"]}}


async def _stream(first: tuple, events, collected: dict, model: str):
    cid = "chatcmpl-" + uuid.uuid4().hex[:24]
    created = int(time.time())

    def chunk(delta: dict, finish=None, usage=None) -> bytes:
        obj = {"id": cid, "object": "chat.completion.chunk", "created": created, "model": PREFIX + model,
               "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
        if usage is not None:
            obj["usage"] = usage
        return _sse(obj)

    usage: dict = {}
    calls = 0
    try:
        yield chunk({"role": "assistant"})
        event = first
        while True:
            kind, value = event
            if kind == "text":
                yield chunk({"content": value})
            elif kind == "thinking":
                yield chunk({"reasoning_content": value})
            elif kind == "tool_call":
                yield chunk({"tool_calls": [_tool_call(calls, value)]})
                calls += 1
            elif kind == "usage":
                usage = value
            elif kind == "end":
                obj = _usage_obj(usage)
                if usage:
                    collected.update(usage_from_obj({"usage": obj}))
                yield chunk({}, value, obj)
                yield b"data: [DONE]\n\n"
                collected["stream_status"] = "complete"
                return
            event = await events.__anext__()
    except TurnError as exc:
        collected["stream_status"] = "failed"
        yield _sse({"error": {"message": exc.message, "code": exc.status}})
    except Exception:
        collected["stream_status"] = "failed"
        raise
    finally:
        await events.aclose()


async def _collect(first: tuple, events, model: str) -> tuple[dict, dict]:
    text, thinking, calls, usage, finish = [], [], [], {}, "stop"
    event = first
    try:
        while True:
            kind, value = event
            if kind == "text":
                text.append(value)
            elif kind == "thinking":
                thinking.append(value)
            elif kind == "tool_call":
                calls.append(_tool_call(len(calls), value))
            elif kind == "usage":
                usage = value
            elif kind == "end":
                finish = value
                break
            event = await events.__anext__()
    finally:
        await events.aclose()
    message: dict = {"role": "assistant", "content": "".join(text) or None}
    if thinking:
        message["reasoning_content"] = "".join(thinking)
    if calls:
        message["tool_calls"] = [{k: v for k, v in call.items() if k != "index"} for call in calls]
    obj = {
        "id": "chatcmpl-" + uuid.uuid4().hex[:24],
        "object": "chat.completion",
        "created": int(time.time()),
        "model": PREFIX + model,
        "choices": [{"index": 0, "message": message, "finish_reason": finish}],
        "usage": _usage_obj(usage),
    }
    return obj, usage_from_obj(obj) if usage else {}


class CursorSource:
    type_id = "cursor"

    def __init__(self, row, cfg: dict, creds):
        self.source_id = row["id"]
        self.name = row["name"]
        self.cfg = cfg
        self.creds = list(creds)

    def _pick(self, exclude: set[int] | None = None, prefer_id: int | None = None, owner_id: int | None = None, hint_id: int | None = None, primary_id: int | None = None):
        now = datetime.now(timezone.utc)
        return pick_account(
            self.creds,
            kind="cursor",
            exclude=exclude,
            is_held=lambda c: is_held(c, now),
            is_authed=credential_is_authed,
            prefer_id=prefer_id,
            owner_id=owner_id,
            hint_id=hint_id,
            primary_id=primary_id,
        )

    async def models(self) -> list[str]:
        picked = self._pick()
        if picked:
            _cred, extra = picked
            names = await fetch_catalog(extra.get("access_token") or "")
            if names:
                return names
        if _models_cache and _models_cache[1]:
            return list(_models_cache[1])
        return list(CURSOR_MODELS)

    def _load(self, user_id: int | None) -> None:
        self.creds = pool_policy.filter_credentials(
            db.fetchall("SELECT * FROM credentials WHERE source_id=? AND disabled=0", (self.source_id,)),
            "cursor",
            user_id,
        )

    async def chat(self, body: dict, *, session_id: str | None = None, user_id: int | None = None, affinity_id: str | None = None) -> ChatResult:
        await refresh_due()
        await refresh_pool_quota()
        self._load(user_id)
        model = upstream_id(body.get("model") or "") or "auto"
        tried: set[int] = set()
        attempts: list[dict] = []
        last = ChatResult(status=503, body={"error": "No Cursor accounts in the pool"})
        prefer_id = lookup("cursor", session_id)
        hint_id = lookup("cursor", affinity_id) if prefer_id is None and affinity_id else None
        primary_id = pool_policy.primary_account(user_id, "cursor")
        failover = Failover()
        refreshed: set[int] = set()
        while True:
            picked = self._pick(exclude=tried, prefer_id=prefer_id, owner_id=user_id, hint_id=hint_id, primary_id=primary_id)
            if not picked:
                if attempts and last.credential_id == attempts[-1].get("credential_id"):
                    attempts.pop()
                last.attempts = attempts
                return last
            cred, extra = picked
            cid = int(cred["id"])
            tried.add(cid)
            note_pool_invoke(cid)
            reserve(cid)
            events = run_turn(
                base=_base(),
                headers=_headers(extra.get("access_token") or ""),
                model=model,
                messages=body.get("messages"),
                tools=body.get("tools"),
                timeout=_CHAT_TIMEOUT,
            )
            try:
                first = await events.__anext__()
            except TurnError as exc:
                release(cid)
                await events.aclose()
                last = ChatResult(status=exc.status, body={"error": {"message": exc.message}}, credential_id=cid, model=model)
                attempts.append({"credential_id": cid, "status": exc.status, "usage": {}})
                if exc.status == 401:
                    if cid not in refreshed and await refresh_one(cred):
                        # Same account, fresh token: try it once more.
                        refreshed.add(cid)
                        tried.discard(cid)
                        self._load(user_id)
                        continue
                    if auth_failure_confirmed(cid):
                        disable_credential(cid, extra=cred.get("extra"))
                    last.status = 502
                    last.body = {"error": {"message": "Cursor rejected the configured account"}}
                    continue
                if exc.status in (403, 429):
                    try:
                        await refresh_quota(cred, force=True)
                    except Exception:
                        pass
                    note_failure(cid, cooldown_seconds=60)
                    continue
                if exc.status >= 500 and failover.server_error(cid, exc.status, 15):
                    continue
                last.attempts = attempts
                return last
            except BaseException:
                release(cid)
                await events.aclose()
                raise
            failover.served()
            if body.get("stream"):
                return self._stream_result(first, events, cid, session_id, model, attempts)
            try:
                obj, usage = await _collect(first, events, model)
            except TurnError as exc:
                last = ChatResult(status=exc.status, body={"error": {"message": exc.message}}, credential_id=cid, model=model)
                last.attempts = attempts
                return last
            finally:
                release(cid)
            bind("cursor", session_id, cid)
            result = ChatResult(status=200, body=obj, usage=usage, credential_id=cid, model=model)
            result.attempts = attempts
            return result

    def _stream_result(self, first, events, cid: int, session_id: str | None, model: str, attempts: list[dict]) -> ChatResult:
        collected: dict = {}

        async def _bind_on_finish():
            inner = _stream(first, events, collected, model)
            try:
                async for chunk in inner:
                    yield chunk
            finally:
                await inner.aclose()
                status = collected.get("stream_status")
                if status == "complete":
                    bind("cursor", session_id, cid)
                elif status == "failed":
                    note_failure(cid, cooldown_seconds=5, affinity_safe=True)

        wrapped = UsageStream(_bind_on_finish(), collected, on_close=lambda: release(cid))
        result = ChatResult(status=200, stream=wrapped, credential_id=cid, model=model)
        result.attempts = attempts
        return result
