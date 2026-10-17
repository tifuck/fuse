"""Claude subscription pool.

Browser PKCE matches Claude Code (`claude /login`). A hosted dashboard cannot
receive Anthropic's localhost redirect, so the user pastes the `code#state`
value Claude shows, the same remote finish as Devin. Chat is the Messages API
translated to OpenAI chunks. Probes are profile only — never a completion.

Model identity/catalog state: `claude_catalog`. Request shaping:
`claude_payload`. Stream translation: `claude_stream`. Their public names are
re-exported here as the provider's API surface.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx

from .. import accounts, db
from ..async_work import durable_call
from ..config import settings
from ..crypto import encrypt
from ..proxy import UsageStream
from .base import ChatResult
from .grok import apply_usage_guard, is_held as credential_is_held
from .pool_pick import Failover, auth_failure_confirmed, note_failure, pick_account, release, reserve
from . import pool_policy
from .pool_session import bind, lookup
from .token_store import log_refresh_failures, persist_rotated, shield_refresh

from .claude_catalog import (
    CLAUDE_MODELS,
    CLAUDE_MODEL_META,
    _meta_for,
    _remember_models,
    cached_models,
    default_effort,
    efforts_for,
    fuse_id,
    is_claude_model,
    model_label,
    model_window,
    parse_catalog_payload,
    upstream_id,
)
from .claude_payload import (
    _ANTHROPIC_VERSION,
    _BETA,
    _EFFORTS,
    _THINKING_OFF,
    _chat_headers,
    _image_block,
    _mark_cache_breakpoint,
    _requested_effort,
    _text_of,
    _thinking_blocks,
    _tool_choice,
    _tool_use_block,
    _user_blocks,
    build_messages_payload,
)
from .claude_stream import (
    UpstreamProtocolError,
    _aggregate,
    _error_message,
    _signature_retryable,
    _sse,
    _translated_stream,
    _upstream_events,
    _Translator,
)

USAGE_TTL = 300
REFRESH_WINDOW = timedelta(minutes=5)
_refresh_locks: dict[int, asyncio.Lock] = {}

SCOPES = (
    "org:create_api_key",
    "user:profile",
    "user:inference",
    "user:sessions:claude_code",
    "user:mcp_servers",
    "user:file_upload",
    "user:plugins",
)


def _refresh_lock(cred_id: int) -> asyncio.Lock:
    lock = _refresh_locks.get(cred_id)
    if lock is None:
        lock = asyncio.Lock()
        _refresh_locks[cred_id] = lock
    return lock


def disable_credential(cred_id: int, *, extra: str | None = None) -> None:
    accounts.disable_credential(cred_id, extra=extra)


def ensure_claude_source() -> int:
    row = db.fetchone("SELECT id FROM sources WHERE type='claude'")
    if row:
        return row["id"]
    with db.tx() as conn:
        conn.execute(
            "INSERT INTO sources (type, name, enabled, config, created_at) VALUES (?,?,?,?,?)",
            ("claude", "Claude", 1, "{}", db.now()),
        )
        return db.last_id(conn)


def _row_get(row, key, default=None):
    try:
        return row[key]
    except (KeyError, IndexError, TypeError):
        return default


def _parse_ts(ts: str) -> datetime | None:
    if not ts:
        return None
    raw = str(ts).replace("Z", "")
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f"):
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _pkce_pair() -> tuple[str, str]:
    verifier = _b64url(secrets.token_bytes(32))
    challenge = _b64url(hashlib.sha256(verifier.encode()).digest())
    return verifier, challenge


def authorize_url(*, challenge: str, state: str, console: bool = False) -> str:
    base = settings.claude_console_authorize_url if console else settings.claude_authorize_url
    query = urlencode(
        {
            "code": "true",
            "client_id": settings.claude_client_id,
            "response_type": "code",
            "redirect_uri": settings.claude_manual_redirect_url,
            "scope": " ".join(SCOPES),
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": state,
        }
    )
    return f"{base}?{query}"


def start_login(*, console: bool = False) -> dict:
    verifier, challenge = _pkce_pair()
    state = _b64url(secrets.token_bytes(32))
    return {
        "verifier": verifier,
        "state": state,
        "auth_url": authorize_url(challenge=challenge, state=state, console=console),
        "console": bool(console),
    }


def parse_manual_code(raw: str) -> tuple[str, str]:
    text = (raw or "").strip()
    if "#" not in text:
        return "", ""
    code, state = text.rsplit("#", 1)
    return code.strip(), state.strip()


def _oauth_revoked(status: int, data) -> bool:
    if status not in (400, 401, 403) or not isinstance(data, dict):
        return False
    err = data.get("error")
    code = str(err.get("code") or err.get("type") or "") if isinstance(err, dict) else str(err or "")
    message = str((err.get("message") if isinstance(err, dict) else "") or data.get("error_description") or "")
    text = f"{code} {message}".lower()
    return "invalid_grant" in text or "revoked" in text


def _cas_update_tokens(cred_id: int, old_extra_raw: str, new_secret: str, new_extra: dict, *, label: str | None = None, reenable: bool = False) -> bool:
    if reenable:
        sql = "UPDATE credentials SET secret=?, extra=?, disabled=0, label=COALESCE(?, label) WHERE id=? AND extra=?"
        args = (new_secret, encrypt(json.dumps(new_extra)), label, cred_id, old_extra_raw)
    else:
        sql = "UPDATE credentials SET secret=?, extra=? WHERE id=? AND extra=?"
        args = (new_secret, encrypt(json.dumps(new_extra)), cred_id, old_extra_raw)
    with db.tx() as conn:
        cur = conn.execute(sql, args)
        return cur.rowcount > 0


def _expires_at(seconds) -> str:
    try:
        span = int(seconds or 28800)
    except (TypeError, ValueError):
        span = 28800
    span = max(60, min(span, 31536000))
    return (datetime.now(timezone.utc) + timedelta(seconds=span)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _account_from_token(data: dict) -> dict:
    account = data.get("account") if isinstance(data.get("account"), dict) else {}
    org = data.get("organization") if isinstance(data.get("organization"), dict) else {}
    return {
        "account_id": str(account.get("uuid") or "")[:128],
        "email": str(account.get("email_address") or account.get("email") or "")[:254],
        "organization_uuid": str(org.get("uuid") or "")[:128],
    }


def store_tokens(source_id: int, tokens: dict, donated_by: int | None, label: str = "", *, profile: dict | None = None) -> int:
    ident = _account_from_token(tokens)
    extra = {
        "auth_kind": "oauth",
        "access_token": tokens["access_token"],
        "refresh_token": tokens.get("refresh_token") or "",
        "expires_at": _expires_at(tokens.get("expires_in")),
        "scope": tokens.get("scope") or " ".join(SCOPES),
        "account_id": ident["account_id"],
        "email": ident["email"],
        "organization_uuid": ident["organization_uuid"],
    }
    if isinstance(profile, dict) and profile:
        extra["profile"] = profile
        sub = str(profile.get("subscription_type") or "")
        if sub:
            extra["subscription_type"] = sub
        if not extra["email"]:
            extra["email"] = str(profile.get("email") or "")[:254]
        if not extra["account_id"]:
            extra["account_id"] = str(profile.get("account_id") or "")[:128]
    account_id = extra.get("account_id") or ""
    if account_id:
        rows = db.fetchall(
            "SELECT * FROM credentials WHERE source_id=? AND donated_by IS ? ORDER BY disabled, id",
            (source_id, donated_by),
        )
        for row in rows:
            current = accounts.cred_extra(row)
            if str(current.get("account_id") or "") != account_id:
                continue
            current.update(extra)
            if _cas_update_tokens(
                int(row["id"]),
                row["extra"],
                encrypt(tokens["access_token"]),
                current,
                label=label or row["label"] or extra["email"] or "Claude account",
                reenable=True,
            ):
                return int(row["id"])
    return accounts.add_credential(
        source_id,
        label or extra["email"] or "Claude account",
        tokens["access_token"],
        extra=extra,
        donated_by=donated_by,
    )


def _bearer(cred, extra: dict) -> str:
    return str(extra.get("access_token") or accounts.cred_secret(cred) or "")


def credential_is_authed(cred, extra: dict | None = None) -> bool:
    if _row_get(cred, "disabled"):
        return False
    extra = extra if extra is not None else accounts.cred_extra(cred)
    token = extra.get("access_token") or accounts.cred_secret(cred)
    if not token:
        return False
    if extra.get("refresh_token"):
        return True
    billing = extra.get("billing") if isinstance(extra.get("billing"), dict) else {}
    err = str(billing.get("last_error") or "").lower()
    if "401" in err or "403" in err:
        return False
    return True


def is_held(cred, now: datetime | None = None) -> bool:
    return credential_is_held(cred, now)


def billing_has_subscription(billing: dict | None) -> bool:
    billing = billing if isinstance(billing, dict) else {}
    if billing.get("subscription_type"):
        return True
    if billing.get("organization_type"):
        return True
    return bool(billing.get("fetched_at")) and not billing.get("last_error")


def account_email(extra: dict | None) -> str:
    extra = extra if isinstance(extra, dict) else {}
    text = str(extra.get("email") or "").strip()
    if text and len(text) <= 254 and " " not in text and text.count("@") == 1:
        return text
    return ""


def _window_percent(raw) -> float | None:
    if not isinstance(raw, dict):
        return None
    value = raw.get("utilization")
    if value is None:
        value = raw.get("used_ratio")
    try:
        pct = float(value)
    except (TypeError, ValueError):
        return None
    if pct <= 1:
        pct *= 100
    if pct < 0:
        return None
    return max(0.0, min(100.0, pct))


def parse_usage_payload(data: dict) -> dict:
    """Claude Code's /api/oauth/usage windows. Utilization is 0..1 or 0..100."""
    data = data if isinstance(data, dict) else {}
    windows = []
    for key, label in (("five_hour", "5-hour"), ("seven_day", "Weekly"), ("seven_day_overage_included", "Weekly extra")):
        raw = data.get(key)
        pct = _window_percent(raw)
        if pct is None:
            continue
        reset = ""
        if isinstance(raw, dict):
            reset = str(raw.get("resets_at") or raw.get("resetsAt") or "")
        windows.append({"id": key, "label": label, "percent": pct, "resets_at": reset})
    peak = max((row["percent"] for row in windows), default=None)
    return {
        "windows": windows,
        "credit_usage_percent": peak,
        "five_hour_percent": next((row["percent"] for row in windows if row["id"] == "five_hour"), None),
        "weekly_percent": next((row["percent"] for row in windows if row["id"] == "seven_day"), None),
        "resets_at": next((row["resets_at"] for row in windows if row["resets_at"]), ""),
    }


def _profile_billing(profile: dict) -> dict:
    org = profile.get("organization") if isinstance(profile.get("organization"), dict) else {}
    account = profile.get("account") if isinstance(profile.get("account"), dict) else {}
    org_type = str(org.get("organization_type") or "")
    mapped = {
        "claude_max": "max",
        "claude_pro": "pro",
        "claude_team": "team",
        "claude_enterprise": "enterprise",
    }.get(org_type, org_type)
    return {
        "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "subscription_type": mapped,
        "organization_type": org_type,
        "plan_type": mapped or "Claude",
        "email": str(account.get("email") or account.get("email_address") or ""),
        "account_id": str(account.get("uuid") or ""),
        "organization_uuid": str(org.get("uuid") or ""),
        "last_error": "",
    }


async def fetch_profile(token: str) -> dict | None:
    if not token:
        return None
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(
                settings.claude_api_base.rstrip("/") + "/api/oauth/profile",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/json",
                    "Cache-Control": "no-cache",
                },
            )
    except httpx.HTTPError:
        return None
    if r.status_code != 200:
        return None
    try:
        data = r.json()
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


async def fetch_usage(token: str) -> tuple[dict | None, str]:
    if not token:
        return None, "missing token"
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(
                settings.claude_api_base.rstrip("/") + "/api/oauth/usage",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/json",
                    "anthropic-beta": "oauth-2025-04-20",
                    "Cache-Control": "no-cache",
                },
            )
    except httpx.HTTPError:
        return None, "usage request failed"
    if r.status_code != 200:
        return None, f"usage http {r.status_code}"
    try:
        data = r.json()
    except json.JSONDecodeError:
        return None, "usage was not json"
    if not isinstance(data, dict):
        return None, "usage was not an object"
    return parse_usage_payload(data), ""


def _store_billing(cred_id: int, billing: dict) -> dict:
    row = db.fetchone("SELECT * FROM credentials WHERE id=?", (cred_id,))
    if not row:
        return billing
    extra = accounts.cred_extra(row)
    extra["billing"] = billing
    if billing.get("email") and not extra.get("email"):
        extra["email"] = str(billing["email"])[:254]
    if billing.get("account_id") and not extra.get("account_id"):
        extra["account_id"] = str(billing["account_id"])[:128]
    if billing.get("organization_uuid"):
        extra["organization_uuid"] = str(billing["organization_uuid"])[:128]
    if billing.get("subscription_type"):
        extra["subscription_type"] = str(billing["subscription_type"])[:32]
    source = db.fetchone("SELECT config FROM sources WHERE id=?", (row["source_id"],))
    try:
        cfg = json.loads((source["config"] if source else None) or "{}")
    except json.JSONDecodeError:
        cfg = {}
    guard_enabled = cfg.get("offpool_usage_guard", True) is not False
    extra, held_until, last_pct = apply_usage_guard(row, extra, billing, enabled=guard_enabled)
    extra["billing"] = billing
    accounts.update_extra(cred_id, extra, last_usage_percent=last_pct, usage_percent_at=db.now(), pool_held_until=held_until)
    return billing


async def refresh_quota(cred, *, force: bool = False) -> dict | None:
    async with _refresh_lock(int(cred["id"])):
        row = db.fetchone("SELECT * FROM credentials WHERE id=?", (cred["id"],))
        if not row or row["disabled"]:
            return None
        extra = accounts.cred_extra(row)
        billing = extra.get("billing") if isinstance(extra.get("billing"), dict) else {}
        if not force and billing.get("fetched_at") and not billing.get("last_error"):
            when = _parse_ts(str(billing.get("fetched_at") or ""))
            if when and datetime.now(timezone.utc) - when < timedelta(seconds=USAGE_TTL):
                return billing
        if extra.get("refresh_token"):
            exp = extra.get("expires_at")
            when = _parse_ts(str(exp or ""))
            if not when or when <= datetime.now(timezone.utc) + REFRESH_WINDOW:
                if await _refresh_one_unlocked(row):
                    row = db.fetchone("SELECT * FROM credentials WHERE id=?", (cred["id"],))
                    extra = accounts.cred_extra(row)
        token = _bearer(row, extra)
        profile = await fetch_profile(token)
        parsed, err = await fetch_usage(token)
        if not profile and parsed is None:
            if billing.get("fetched_at"):
                return billing
            return _store_billing(row["id"], {"last_error": err or "usage unavailable", "fetched_at": ""})
        merged = dict(billing)
        if profile:
            merged.update(_profile_billing(profile))
        if parsed is not None:
            merged.update(parsed)
            merged["last_error"] = ""
            merged["fetched_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        elif err:
            merged["last_error"] = err
        return _store_billing(row["id"], merged)


async def exchange_code(code: str, verifier: str, state: str) -> dict:
    body = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": settings.claude_manual_redirect_url,
        "client_id": settings.claude_client_id,
        "code_verifier": verifier,
        "state": state,
    }
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(
            settings.claude_token_url,
            json=body,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
    try:
        data = r.json()
    except json.JSONDecodeError:
        data = {}
    if r.status_code != 200 or not isinstance(data, dict) or not data.get("access_token"):
        err = data.get("error") if isinstance(data, dict) else None
        message = ""
        if isinstance(err, dict):
            message = str(err.get("message") or err.get("code") or "")
        elif isinstance(err, str):
            message = err
        if r.status_code == 401:
            raise ValueError(message or "Authentication failed: invalid authorization code")
        raise ValueError(message or "Token exchange failed")
    return data


async def complete_login(raw_code: str, verifier: str, expected_state: str, donated_by: int) -> tuple[int | None, str]:
    code, state = parse_manual_code(raw_code)
    if not code or not state:
        return None, "Paste the full code Claude shows, including the part after #."
    if not expected_state or state != expected_state or not verifier:
        return None, "Claude sign-in expired or did not match this session. Start again."
    try:
        tokens = await exchange_code(code, verifier, state)
    except ValueError as exc:
        return None, str(exc) or "Could not finish Claude sign-in."
    except httpx.HTTPError:
        return None, "Could not reach Claude. Try again shortly."
    profile = await fetch_profile(str(tokens.get("access_token") or ""))
    sid = ensure_claude_source()
    cred_id = store_tokens(sid, tokens, donated_by, profile=profile if isinstance(profile, dict) else None)
    if profile:
        try:
            _store_billing(cred_id, _profile_billing(profile))
        except Exception:
            pass
    return cred_id, ""


async def _refresh_one_unlocked(cred) -> bool:
    if not settings.oauth_refresh_enabled:
        return False
    row = db.fetchone("SELECT * FROM credentials WHERE id=?", (cred["id"],))
    if not row or row["disabled"]:
        return False
    extra = accounts.cred_extra(row)
    rt = extra.get("refresh_token")
    if not rt:
        return False
    body = {
        "grant_type": "refresh_token",
        "refresh_token": rt,
        "client_id": settings.claude_client_id,
        "scope": str(extra.get("scope") or " ".join(SCOPES)),
    }
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(
                settings.claude_token_url,
                json=body,
                headers={"Content-Type": "application/json", "Accept": "application/json"},
            )
    except httpx.HTTPError:
        note_failure(row["id"], cooldown_seconds=30)
        return False
    try:
        data = r.json()
    except json.JSONDecodeError:
        note_failure(row["id"], cooldown_seconds=30)
        return False
    if not isinstance(data, dict) or r.status_code != 200 or "access_token" not in data:
        if _oauth_revoked(r.status_code, data if isinstance(data, dict) else {}):
            disable_credential(row["id"], extra=row.get("extra"))
        else:
            note_failure(row["id"], cooldown_seconds=60 if r.status_code == 429 else 30)
        return False
    extra["access_token"] = data["access_token"]
    extra["refresh_token"] = data.get("refresh_token") or rt
    extra["expires_at"] = _expires_at(data.get("expires_in"))
    extra["auth_kind"] = "oauth"
    if data.get("scope"):
        extra["scope"] = str(data["scope"])
    ident = _account_from_token(data)
    for key in ("account_id", "email", "organization_uuid"):
        if ident.get(key) and not extra.get(key):
            extra[key] = ident[key]
    return await durable_call(
        persist_rotated,
        _cas_update_tokens,
        int(row["id"]),
        row["extra"],
        encrypt(data["access_token"]),
        extra,
        {
            "access_token": extra["access_token"],
            "refresh_token": extra["refresh_token"],
            "expires_at": extra["expires_at"],
            "auth_kind": "oauth",
        },
    )


async def refresh_one(cred) -> bool:
    async def run():
        async with _refresh_lock(int(cred["id"])):
            return await _refresh_one_unlocked(cred)

    return await shield_refresh(run())


async def refresh_pool_quota(*, force: bool = False) -> None:
    rows = db.fetchall(
        "SELECT c.* FROM credentials c JOIN sources s ON s.id=c.source_id WHERE s.type='claude' AND c.disabled=0"
    )
    if rows:
        results = await asyncio.gather(*(refresh_quota(c, force=force) for c in rows), return_exceptions=True)
        log_refresh_failures("Claude quota refresh", rows, results)


async def refresh_due() -> None:
    rows = db.fetchall(
        "SELECT c.* FROM credentials c JOIN sources s ON s.id=c.source_id WHERE s.type='claude' AND c.disabled=0"
    )
    soon = datetime.now(timezone.utc) + REFRESH_WINDOW
    due = []
    for cred in rows:
        extra = accounts.cred_extra(cred)
        if not extra.get("refresh_token"):
            continue
        when = _parse_ts(str(extra.get("expires_at") or ""))
        if not when or when <= soon:
            due.append(cred)
    if due:
        results = await asyncio.gather(*(refresh_one(c) for c in due), return_exceptions=True)
        log_refresh_failures("Claude OAuth refresh", due, results)


async def revoke_remote(cred) -> None:
    extra = accounts.cred_extra(cred)
    token = extra.get("refresh_token") or extra.get("access_token")
    if not token:
        return
    hint = "refresh_token" if extra.get("refresh_token") else "access_token"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(
                settings.claude_token_url.rstrip("/") + "/revoke",
                json={"token": token, "token_type_hint": hint, "client_id": settings.claude_client_id},
                headers={"Content-Type": "application/json"},
            )
    except httpx.HTTPError:
        return


def claude_own_access(user_id: int) -> dict:
    sid = ensure_claude_source()
    rows = db.fetchall(
        "SELECT * FROM credentials WHERE source_id=? AND donated_by=? AND disabled=0",
        (sid, user_id),
    )
    if not rows:
        return {"ok": False, "reason": "none"}
    saw_authed = False
    for row in rows:
        extra = accounts.cred_extra(row)
        if not credential_is_authed(row, extra):
            continue
        saw_authed = True
        return {"ok": True, "reason": "ok"}
    return {"ok": False, "reason": "unauthed" if not saw_authed else "no_sub"}


def _claude_live(row) -> bool:
    return not is_held(row) and credential_is_authed(row, accounts.cred_extra(row))


def claude_access_state(user_id: int) -> dict:
    return pool_policy.with_open_access(user_id, "claude", claude_own_access(user_id), _claude_live)


def user_has_claude(user_id: int) -> bool:
    return claude_access_state(user_id)["ok"]


def note_pool_invoke(cred_id: int) -> None:
    db.execute("UPDATE credentials SET pool_invokes = COALESCE(pool_invokes, 0) + 1 WHERE id=?", (cred_id,))


async def fetch_catalog() -> list[str]:
    cached = cached_models(300.0)
    if cached is not None:
        return cached
    rows: list[dict] = []
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.get("https://downloads.claude.ai/model-catalog/v1/catalog.json")
            if r.status_code == 200 and isinstance(r.json(), dict):
                rows = parse_catalog_payload(r.json())
    except (httpx.HTTPError, json.JSONDecodeError, ValueError):
        rows = []
    return _remember_models(rows)


class ClaudeSource:
    type_id = "claude"

    def __init__(self, row, cfg: dict, creds):
        self.source_id = row["id"]
        self.name = row["name"]
        self.cfg = cfg
        self.creds = list(creds)

    def _pick(self, exclude: set[int] | None = None, prefer_id: int | None = None, owner_id: int | None = None, hint_id: int | None = None, primary_id: int | None = None):
        now = datetime.now(timezone.utc)
        return pick_account(
            self.creds,
            kind="claude",
            exclude=exclude,
            is_held=lambda c: is_held(c, now),
            is_authed=credential_is_authed,
            prefer_id=prefer_id,
            owner_id=owner_id,
            hint_id=hint_id,
            primary_id=primary_id,
        )

    async def models(self) -> list[str]:
        names = await fetch_catalog()
        return names or list(CLAUDE_MODELS)

    async def _post(self, client: httpx.AsyncClient, token: str, payload: dict):
        url = settings.claude_api_base.rstrip("/") + "/v1/messages"
        # Streaming send: the body stays unread until the translator iterates
        # it, so the first event reaches the client as Anthropic produces it
        # and a cancelled request stops generation promptly. client.post would
        # buffer the whole SSE response before returning.
        req = client.build_request("POST", url, headers=_chat_headers(token), json=payload)
        return await client.send(req, stream=True)

    async def chat(self, body: dict, *, session_id: str | None = None, user_id: int | None = None, affinity_id: str | None = None) -> ChatResult:
        await refresh_due()
        await refresh_pool_quota()
        self.creds = pool_policy.filter_credentials(
            db.fetchall(
                "SELECT * FROM credentials WHERE source_id=? AND disabled=0",
                (self.source_id,),
            ),
            "claude",
            user_id,
        )
        tried: set[int] = set()
        attempts: list[dict] = []
        last = ChatResult(status=503, body={"error": {"message": "No Claude accounts in the pool"}})
        requested = str(body.get("model") or CLAUDE_MODELS[0])
        upstream = upstream_id(requested)
        want_stream = bool(body.get("stream"))
        payload = build_messages_payload(body, upstream)
        prefer_id = lookup("claude", session_id)
        hint_id = lookup("claude", affinity_id) if prefer_id is None and affinity_id else None
        primary_id = pool_policy.primary_account(user_id, "claude")
        failover = Failover()
        while True:
            picked = self._pick(exclude=tried, prefer_id=prefer_id, owner_id=user_id, hint_id=hint_id, primary_id=primary_id)
            if not picked:
                if attempts and last.credential_id == attempts[-1].get("credential_id"):
                    attempts.pop()
                last.attempts = attempts
                return last
            cred, extra = picked
            tried.add(int(cred["id"]))
            note_pool_invoke(cred["id"])
            token = _bearer(cred, extra)
            client = httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=15.0))
            stream_reserved = False
            try:
                reserve(cred["id"])
                resp = await self._post(client, token, payload)
                status = resp.status_code
                if status == 401 and extra.get("refresh_token"):
                    await resp.aclose()
                    await client.aclose()
                    ok = await refresh_one(cred)
                    if ok:
                        attempts.append({"credential_id": cred["id"], "status": 401, "usage": {}})
                        row = db.fetchone("SELECT * FROM credentials WHERE id=?", (cred["id"],))
                        if row is None:
                            continue
                        extra = accounts.cred_extra(row)
                        token = _bearer(cred, extra)
                        client = httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=15.0))
                        resp = await self._post(client, token, payload)
                        status = resp.status_code
                if status == 400:
                    raw = (await resp.aread()).decode("utf-8", errors="replace")
                    await resp.aclose()
                    await client.aclose()
                    message = _error_message(raw) or "Claude rejected the request"
                    if _signature_retryable(raw):
                        # Rewriting the client's history behind its back is
                        # forbidden: surface the signature rejection so the
                        # caller can start a fresh conversation instead.
                        message = (
                            "Claude rejected the thinking signatures saved in this conversation's history "
                            "(they expire and cannot be replayed). Start a new conversation, or resend the "
                            "turn without the stored thinking blocks."
                        )
                    last = ChatResult(
                        status=400,
                        body={"error": {"message": message}},
                        credential_id=cred["id"],
                        model=fuse_id(upstream),
                    )
                    last.attempts = attempts
                    return last
                if status != 200:
                    # Keep Anthropic's reason (rate limit, overload) and its
                    # Retry-After: the chat loop backs off on it and the user
                    # sees why the request failed, not just a status code.
                    try:
                        detail = _error_message((await resp.aread()).decode("utf-8", errors="replace"))
                    except Exception:
                        detail = ""
                    retry_after = str(resp.headers.get("retry-after") or "").strip()
                    await resp.aclose()
                    await client.aclose()
                    last = ChatResult(
                        status=status,
                        body={"error": {"message": f"Claude request failed ({status})" + (f": {detail[:300]}" if detail else "")}},
                        credential_id=cred["id"],
                        model=fuse_id(upstream),
                        response_headers={"Retry-After": retry_after} if retry_after else {},
                    )
                    if status == 401:
                        if auth_failure_confirmed(cred["id"]):
                            disable_credential(cred["id"], extra=cred.get("extra"))
                        last.status = 502
                        last.body = {"error": {"message": "Claude rejected the configured account"}}
                        attempts.append({"credential_id": cred["id"], "status": status, "usage": {}})
                        last.attempts = attempts
                        continue
                    if status == 429 or (
                        status in (500, 502, 503, 529) and failover.server_error(cred["id"], status, 15)
                    ):
                        if status == 429:
                            note_failure(cred["id"], cooldown_seconds=60)
                        attempts.append({"credential_id": cred["id"], "status": status, "usage": {}})
                        last.attempts = attempts
                        continue
                    last.attempts = attempts
                    return last
                if want_stream:
                    collected: dict = {}
                    stream = _translated_stream(resp, client, upstream, collected)
                    stream_reserved = True
                    failover.served()
                    return self._stream_result(stream, collected, cred, session_id, fuse_id(upstream), attempts)
                obj = await _aggregate(resp, client, upstream)
                if isinstance(obj.get("error"), dict):
                    partial_usage = obj.pop("_fuse_usage", {}) if isinstance(obj, dict) else {}
                    if partial_usage:
                        partial_usage["usage_status"] = "partial"
                    last = ChatResult(status=502, body=obj, usage=partial_usage, credential_id=cred["id"], model=fuse_id(upstream))
                    if failover.server_error(cred["id"], 502, 60):
                        attempts.append({"credential_id": cred["id"], "status": 502, "usage": partial_usage})
                        continue
                    last.attempts = attempts
                    return last
                usage = obj.get("usage") if isinstance(obj.get("usage"), dict) else {}
                failover.served()
                bind("claude", session_id, cred["id"])
                last = ChatResult(status=200, body=obj, usage=usage if isinstance(usage, dict) else {}, credential_id=cred["id"], model=fuse_id(upstream))
                last.attempts = attempts
                return last
            except httpx.HTTPError as exc:
                failed = 504 if isinstance(exc, httpx.TimeoutException) else 502
                retry = failover.server_error(cred["id"], failed, 15)
                if retry:
                    attempts.append({"credential_id": cred["id"], "status": failed, "usage": {}})
                last = ChatResult(status=failed, body={"error": {"message": f"Claude request failed ({type(exc).__name__})"}}, credential_id=cred["id"], model=fuse_id(upstream))
                last.attempts = attempts
                try:
                    await client.aclose()
                except Exception:
                    pass
                if retry:
                    continue
                return last
            finally:
                if not stream_reserved:
                    release(cred["id"])

    def _stream_result(self, stream, collected: dict, cred, session_id: str | None, model: str, attempts: list[dict]) -> ChatResult:
        cid = cred["id"]

        async def _bind_on_finish():
            try:
                async for chunk in stream:
                    yield chunk
            finally:
                close = getattr(stream, "aclose", None)
                if close is not None:
                    await close()
                status = (collected or {}).get("stream_status")
                if status == "complete":
                    bind("claude", session_id, cid)
                elif status == "failed":
                    note_failure(cid, cooldown_seconds=5, affinity_safe=True)

        wrapped = UsageStream(_bind_on_finish(), collected, on_close=lambda: release(cid))
        result = ChatResult(status=200, stream=wrapped, credential_id=cid, model=model)
        result.attempts = attempts
        return result
