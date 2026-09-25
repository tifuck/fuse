"""Claude subscription pool.

Browser PKCE matches Claude Code (`claude /login`). A hosted dashboard cannot
receive Anthropic's localhost redirect, so the user pastes the `code#state`
value Claude shows, the same remote finish as Devin. Chat is the Messages API
translated to OpenAI chunks. Probes are profile only — never a completion.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import secrets
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx

from .. import accounts, db
from ..config import settings
from ..crypto import encrypt
from ..proxy import UsageStream
from .base import ChatResult
from .grok import apply_usage_guard, is_held as credential_is_held
from .pool_pick import note_failure, pick_account, release, reserve
from . import pool_policy
from .pool_session import bind, lookup

USAGE_TTL = 300
REFRESH_WINDOW = timedelta(minutes=5)
_refresh_locks: dict[int, asyncio.Lock] = {}
_models_cache: tuple[float, list[str]] | None = None

CLAUDE_MODELS = [
    "claude/claude-opus-5-5",
    "claude/claude-sonnet-5",
    "claude/claude-fable-5-1",
    "claude/claude-haiku-4-5-20251001",
]

CLAUDE_MODEL_META = {
    "claude-opus-5-5": {"name": "Opus 5.5", "window": 1_000_000, "efforts": ("low", "medium", "high", "xhigh", "max"), "default_effort": "medium"},
    "claude-sonnet-5": {"name": "Sonnet 5", "window": 1_000_000, "efforts": ("low", "medium", "high", "xhigh", "max"), "default_effort": "high"},
    "claude-fable-5-1": {"name": "Fable 5.1", "window": 1_000_000, "efforts": ("low", "medium", "high", "xhigh", "max"), "default_effort": "high"},
    "claude-haiku-4-5-20251001": {"name": "Haiku 4.5", "window": 200_000, "efforts": (), "default_effort": ""},
}
_catalog_meta: dict[str, dict] = {}

SCOPES = (
    "org:create_api_key",
    "user:profile",
    "user:inference",
    "user:sessions:claude_code",
    "user:mcp_servers",
    "user:file_upload",
    "user:plugins",
)

_THINKING_OFF = frozenset({"none", "off", "disabled"})
_EFFORTS = ("low", "medium", "high", "xhigh", "max")
# Claude Code's first-party allowlist for current models is claude-code plus
# oauth-2025-04-20 when the token carries user:inference. The old adaptive
# thinking beta is not on that list and these models reject it.
_BETA = "claude-code-20250219,oauth-2025-04-20"
_ANTHROPIC_VERSION = "2023-06-01"


def _refresh_lock(cred_id: int) -> asyncio.Lock:
    lock = _refresh_locks.get(cred_id)
    if lock is None:
        lock = asyncio.Lock()
        _refresh_locks[cred_id] = lock
    return lock


def disable_credential(cred_id: int) -> None:
    db.execute("UPDATE credentials SET disabled=1 WHERE id=?", (cred_id,))


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


def is_claude_model(model: str) -> bool:
    mid = (model or "").strip()
    if not mid.startswith("claude/"):
        return False
    slug = mid[7:]
    if not slug or slug.startswith("claude/"):
        return False
    return True


def upstream_id(model: str) -> str:
    mid = (model or "").strip()
    if mid.startswith("claude/"):
        return mid[7:]
    return mid


def fuse_id(model: str) -> str:
    mid = (model or "").strip()
    if not mid:
        return ""
    if mid.startswith("claude/"):
        return mid
    return "claude/" + mid


def _meta_for(model: str) -> dict:
    slug = upstream_id(model)
    return _catalog_meta.get(slug) or CLAUDE_MODEL_META.get(slug) or {}


def model_label(model: str) -> str:
    return str(_meta_for(model).get("name") or "")


def model_window(model: str) -> int | None:
    raw = _meta_for(model).get("window")
    try:
        value = int(raw or 0)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def efforts_for(model: str | None = None) -> tuple[str, ...]:
    efforts = _meta_for(model or "").get("efforts")
    if efforts is None:
        return ("low", "medium", "high", "xhigh", "max")
    return tuple(efforts)


def default_effort(model: str | None = None) -> str:
    return str(_meta_for(model or "").get("default_effort") or "")


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


def parse_catalog_payload(data: dict) -> list[dict]:
    """First-party models from the published Claude Code catalog."""
    surfaces = data.get("surfaces") if isinstance(data, dict) else None
    cc = surfaces.get("cc") if isinstance(surfaces, dict) else None
    configs = cc.get("model_selector_config") if isinstance(cc, dict) else None
    rows = []
    seen = set()
    for cfg in configs or []:
        if not isinstance(cfg, dict):
            continue
        for model in cfg.get("models") or []:
            if not isinstance(model, dict):
                continue
            offered = model.get("offered_on")
            if isinstance(offered, list) and offered and "first_party" not in offered:
                continue
            mid = str(model.get("id") or "").strip()
            if not mid or mid in seen or not mid.startswith("claude-"):
                continue
            seen.add(mid)
            runtime = model.get("runtime") if isinstance(model.get("runtime"), dict) else {}
            thinking = model.get("thinking") if isinstance(model.get("thinking"), dict) else {}
            efforts = runtime.get("effort_levels")
            if not isinstance(efforts, list):
                options = thinking.get("effort_options") if isinstance(thinking.get("effort_options"), list) else []
                efforts = [str(opt.get("id")) for opt in options if isinstance(opt, dict) and opt.get("id")]
            efforts = tuple(str(item) for item in efforts if isinstance(item, str) and item)
            try:
                window = int(runtime.get("max_input_tokens") or 0)
            except (TypeError, ValueError):
                window = 0
            rows.append(
                {
                    "id": mid,
                    "name": str(model.get("name") or mid),
                    "window": window or 200_000,
                    "efforts": efforts,
                    "default_effort": str(runtime.get("default_effort") or ""),
                    "thinking_type": str(thinking.get("type") or ""),
                }
            )
    return rows


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
    db.execute(
        """UPDATE credentials SET extra=?, last_usage_percent=?, usage_percent_at=?, pool_held_until=?
           WHERE id=?""",
        (encrypt(json.dumps(extra)), last_pct, db.now(), held_until, cred_id),
    )
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
            disable_credential(row["id"])
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
    if not _cas_update_tokens(int(row["id"]), row["extra"], encrypt(data["access_token"]), extra):
        retry = db.fetchone("SELECT * FROM credentials WHERE id=?", (row["id"],))
        if retry is not None and not retry["disabled"]:
            merged = accounts.cred_extra(retry)
            merged.update(
                {
                    "access_token": extra["access_token"],
                    "refresh_token": extra["refresh_token"],
                    "expires_at": extra["expires_at"],
                    "auth_kind": "oauth",
                }
            )
            _cas_update_tokens(int(row["id"]), retry["extra"], encrypt(data["access_token"]), merged)
    return True


async def refresh_one(cred) -> bool:
    async with _refresh_lock(int(cred["id"])):
        return await _refresh_one_unlocked(cred)


async def refresh_pool_quota(*, force: bool = False) -> None:
    rows = db.fetchall(
        "SELECT c.* FROM credentials c JOIN sources s ON s.id=c.source_id WHERE s.type='claude' AND c.disabled=0"
    )
    if rows:
        await asyncio.gather(*(refresh_quota(c, force=force) for c in rows), return_exceptions=True)


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
        await asyncio.gather(*(refresh_one(c) for c in due), return_exceptions=True)


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


def _claude_pool_ready() -> bool:
    sid = ensure_claude_source()
    rows = db.fetchall("SELECT * FROM credentials WHERE source_id=? AND disabled=0", (sid,))
    for row in rows:
        if credential_is_authed(row, accounts.cred_extra(row)):
            return True
    return False


def claude_access_state(user_id: int) -> dict:
    return pool_policy.with_open_access(user_id, "claude", claude_own_access(user_id), _claude_pool_ready())


def user_has_claude(user_id: int) -> bool:
    return claude_access_state(user_id)["ok"]


def note_pool_invoke(cred_id: int) -> None:
    db.execute("UPDATE credentials SET pool_invokes = COALESCE(pool_invokes, 0) + 1 WHERE id=?", (cred_id,))


def _text_of(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, dict) and part.get("type") in ("text", "input_text", "output_text"):
                parts.append(str(part.get("text") or ""))
            elif isinstance(part, str):
                parts.append(part)
        return "\n".join(p for p in parts if p)
    return ""


def _image_block(part: dict) -> dict | None:
    url_obj = part.get("image_url")
    url = url_obj.get("url") if isinstance(url_obj, dict) else url_obj
    if not isinstance(url, str) or not url:
        return None
    if url.startswith("data:"):
        header, _, data = url.partition(",")
        media = "image/png"
        if header.startswith("data:") and ";" in header:
            media = header[5:].split(";", 1)[0] or media
        if not data:
            return None
        return {"type": "image", "source": {"type": "base64", "media_type": media, "data": data}}
    if url.startswith("https://") or url.startswith("http://"):
        return {"type": "image", "source": {"type": "url", "url": url}}
    return None


def _user_blocks(content) -> list[dict]:
    if isinstance(content, str):
        return [{"type": "text", "text": content}] if content else []
    blocks = []
    if isinstance(content, list):
        for part in content:
            if isinstance(part, str):
                if part:
                    blocks.append({"type": "text", "text": part})
                continue
            if not isinstance(part, dict):
                continue
            kind = part.get("type")
            if kind in ("text", "input_text"):
                text = str(part.get("text") or "")
                if text:
                    blocks.append({"type": "text", "text": text})
            elif kind == "image_url":
                image = _image_block(part)
                if image:
                    blocks.append(image)
    return blocks


def _thinking_blocks(msg: dict) -> list[dict]:
    raw = msg.get("claude_thinking")
    if not isinstance(raw, list):
        return []
    out = []
    for block in raw:
        if not isinstance(block, dict):
            continue
        text = str(block.get("thinking") or block.get("text") or "")
        signature = str(block.get("signature") or "")
        if text and signature:
            out.append({"type": "thinking", "thinking": text, "signature": signature})
    return out


def _tool_use_block(call: dict) -> dict | None:
    if not isinstance(call, dict):
        return None
    fn = call.get("function") if isinstance(call.get("function"), dict) else {}
    name = str(fn.get("name") or "").strip()
    if not name:
        return None
    raw = fn.get("arguments")
    parsed: dict
    if isinstance(raw, dict):
        parsed = raw
    else:
        try:
            loaded = json.loads(raw or "{}")
            parsed = loaded if isinstance(loaded, dict) else {"input": loaded}
        except (json.JSONDecodeError, TypeError, ValueError):
            parsed = {}
    return {
        "type": "tool_use",
        "id": str(call.get("id") or "")[:128] or f"toolu_{name[:24]}",
        "name": name[:64],
        "input": parsed,
    }


def _tool_choice(choice, parallel) -> dict | None:
    mapped = None
    if isinstance(choice, str) and choice in ("auto", "none", "any"):
        mapped = {"type": choice}
    elif choice == "required":
        mapped = {"type": "any"}
    elif isinstance(choice, dict) and choice.get("type") == "function":
        fn = choice.get("function") if isinstance(choice.get("function"), dict) else {}
        if fn.get("name"):
            mapped = {"type": "tool", "name": str(fn["name"])[:64]}
    if mapped is None:
        return None
    if parallel is False and mapped["type"] in ("auto", "any"):
        mapped["disable_parallel_tool_use"] = True
    return mapped


def _requested_effort(body: dict, model: str) -> str | None:
    effort = body.get("reasoning_effort")
    if effort is None and isinstance(body.get("reasoning"), dict):
        effort = body["reasoning"].get("effort")
    if not isinstance(effort, str) or not effort.strip():
        # The CLI's non-interactive default for Opus 5.5 is low, and that is
        # the effort a subscription token accepts. The catalog default (medium)
        # is for the interactive picker, not this request.
        effort = "low"
    key = str(effort or "").strip().lower()
    if not key or key in _THINKING_OFF:
        return None
    allowed = efforts_for(model) or _EFFORTS
    if key not in allowed and key not in _EFFORTS:
        return allowed[0] if allowed else "high"
    if key not in allowed:
        return allowed[0] if allowed else key
    return key


def build_messages_payload(body: dict, model: str) -> dict:
    system: list[dict] = []
    messages: list[dict] = []
    pending_results: list[dict] = []

    def flush_results():
        if pending_results:
            messages.append({"role": "user", "content": list(pending_results)})
            pending_results.clear()

    for msg in body.get("messages") or []:
        if not isinstance(msg, dict):
            continue
        role = msg.get("role")
        if role in ("system", "developer"):
            flush_results()
            text = _text_of(msg.get("content"))
            if text:
                system.append({"type": "text", "text": text})
            continue
        if role == "user":
            flush_results()
            blocks = _user_blocks(msg.get("content"))
            if blocks:
                messages.append({"role": "user", "content": blocks})
            continue
        if role == "assistant":
            flush_results()
            blocks: list[dict] = []
            blocks.extend(_thinking_blocks(msg))
            text = _text_of(msg.get("content"))
            if not text and isinstance(msg.get("reasoning_content"), str) and not blocks:
                text = ""
            if text:
                blocks.append({"type": "text", "text": text})
            for call in msg.get("tool_calls") or []:
                block = _tool_use_block(call)
                if block:
                    blocks.append(block)
            if blocks:
                messages.append({"role": "assistant", "content": blocks})
            continue
        if role == "tool":
            call_id = str(msg.get("tool_call_id") or "").strip()
            if not call_id:
                continue
            pending_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": call_id[:128],
                    "content": _text_of(msg.get("content")) or "",
                }
            )
    flush_results()
    if not messages:
        messages = [{"role": "user", "content": [{"type": "text", "text": ""}]}]

    try:
        max_tokens = int(body.get("max_tokens") or settings.max_tokens)
    except (TypeError, ValueError):
        max_tokens = int(settings.max_tokens)
    max_tokens = max(1, min(max_tokens, int(settings.max_tokens)))

    payload: dict = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": messages,
        "stream": True,
    }
    # Subscription OAuth is only accepted for a Claude Code shaped request.
    # A bare Messages call with the same token is refused as rate_limit_error
    # even while the CLI on that account succeeds.
    identity = {
        "type": "text",
        "text": "You are Claude Code, Anthropic's official CLI for Claude.",
        "cache_control": {"type": "ephemeral"},
    }
    payload["system"] = [identity, *system]
    tools = []
    for tool in body.get("tools") or []:
        if not isinstance(tool, dict) or tool.get("type") not in (None, "function"):
            continue
        fn = tool.get("function") if isinstance(tool.get("function"), dict) else tool
        name = str(fn.get("name") or "").strip()
        if not name:
            continue
        schema = fn.get("parameters") if isinstance(fn.get("parameters"), dict) else {"type": "object", "properties": {}}
        tools.append(
            {
                "name": name[:64],
                "description": str(fn.get("description") or "")[:4096],
                "input_schema": schema,
            }
        )
    if tools:
        payload["tools"] = tools
    choice = _tool_choice(body.get("tool_choice"), body.get("parallel_tool_calls"))
    effort = _requested_effort(body, model)
    # A forced tool choice is illegal while thinking is on. Claude Code demotes it.
    if choice and choice.get("type") == "tool" and effort:
        choice = {"type": "auto"}
    if choice and tools:
        payload["tool_choice"] = choice
    # Current Claude Code models take effort in output_config. thinking.type
    # stays adaptive only when the published catalog says so; older models
    # reject a thinking block they did not ask for.
    meta = _meta_for(model)
    thinking_type = str(meta.get("thinking_type") or "")
    if effort:
        payload["output_config"] = {"effort": effort}
        if thinking_type == "adaptive":
            payload["thinking"] = {"type": "adaptive"}
        elif thinking_type == "enabled":
            budget = max(1024, min(max_tokens - 1, 8192))
            if budget >= 1024:
                payload["thinking"] = {"type": "enabled", "budget_tokens": budget}
    else:
        if isinstance(body.get("temperature"), (int, float)) and not isinstance(body.get("temperature"), bool):
            payload["temperature"] = body["temperature"]
        if isinstance(body.get("top_p"), (int, float)) and not isinstance(body.get("top_p"), bool):
            payload["top_p"] = body["top_p"]
    stop = body.get("stop")
    if isinstance(stop, str) and stop:
        payload["stop_sequences"] = [stop[:64]]
    elif isinstance(stop, list):
        seqs = [str(item)[:64] for item in stop if isinstance(item, str) and item][:4]
        if seqs:
            payload["stop_sequences"] = seqs
    return payload


def _sse(obj: dict) -> bytes:
    return f"data: {json.dumps(obj, separators=(',', ':'))}\n\n".encode()


class UpstreamProtocolError(ValueError):
    pass


async def _upstream_events(resp: httpx.Response):
    buf = ""

    def parse(block: str):
        event_name = ""
        data_lines = []
        for line in block.split("\n"):
            if line.startswith("event:"):
                event_name = line[6:].strip()
            elif line.startswith("data:"):
                data_lines.append(line[5:].strip())
        if not data_lines:
            return None
        raw = "\n".join(data_lines)
        if not raw or raw == "[DONE]":
            return None
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise UpstreamProtocolError("upstream sent malformed JSON") from exc
        if not isinstance(obj, dict):
            raise UpstreamProtocolError("upstream sent an invalid event")
        if event_name and "type" not in obj:
            obj["type"] = event_name
        return obj

    async for chunk in resp.aiter_text():
        buf += chunk
        while "\n\n" in buf:
            block, buf = buf.split("\n\n", 1)
            obj = parse(block)
            if obj is not None:
                yield obj
    if buf.strip():
        obj = parse(buf)
        if obj is not None:
            yield obj


class _Translator:
    def __init__(self, model: str):
        self.model = model
        self.created = int(time.time())
        self.completion_id = "chatcmpl-claude"
        self.usage: dict = {}
        self.error = ""
        self.blocks: dict[int, dict] = {}
        self.tool_order: list[int] = []
        self.thinking: list[dict] = []
        self.stop_reason = ""
        self.input_tokens = 0
        self.output_tokens = 0
        self.cache_read = 0
        self.cache_write = 0

    def _chunk(self, delta: dict | None = None, finish: str | None = None, *, usage: dict | None = None) -> bytes:
        obj = {
            "id": self.completion_id,
            "object": "chat.completion.chunk",
            "created": self.created,
            "model": self.model,
            "choices": [{"index": 0, "delta": delta or {}, "finish_reason": finish}],
        }
        if usage:
            obj["usage"] = usage
        return _sse(obj)

    def _error_chunk(self, message: str) -> bytes:
        return _sse(
            {
                "id": self.completion_id,
                "object": "chat.completion.chunk",
                "created": self.created,
                "model": self.model,
                "choices": [],
                "error": {"message": message, "type": "api_error"},
            }
        )

    def _note_usage(self, raw) -> None:
        if not isinstance(raw, dict):
            return
        if raw.get("input_tokens") is not None:
            self.input_tokens = int(raw.get("input_tokens") or 0)
        if raw.get("output_tokens") is not None:
            self.output_tokens = int(raw.get("output_tokens") or 0)
        self.cache_read = int(raw.get("cache_read_input_tokens") or self.cache_read or 0)
        self.cache_write = int(raw.get("cache_creation_input_tokens") or self.cache_write or 0)
        prompt = self.input_tokens + self.cache_read + self.cache_write
        self.usage = {
            "prompt_tokens": prompt,
            "completion_tokens": self.output_tokens,
            "total_tokens": prompt + self.output_tokens,
            "cached_tokens": self.cache_read,
            "cache_write_tokens": self.cache_write,
            "reasoning_tokens": 0,
        }

    def _openai_usage(self) -> dict:
        u = self.usage or {}
        return {
            "prompt_tokens": u.get("prompt_tokens") or 0,
            "completion_tokens": u.get("completion_tokens") or 0,
            "total_tokens": u.get("total_tokens") or 0,
            "prompt_tokens_details": {
                "cached_tokens": u.get("cached_tokens") or 0,
                "cache_write_tokens": u.get("cache_write_tokens") or 0,
            },
            "completion_tokens_details": {"reasoning_tokens": 0},
        }

    def _handle(self, event: dict) -> bytes | None:
        etype = str(event.get("type") or "")
        if etype == "message_start":
            message = event.get("message") if isinstance(event.get("message"), dict) else {}
            if message.get("id"):
                self.completion_id = "chatcmpl-" + str(message["id"])
            self._note_usage(message.get("usage"))
            return self._chunk({"role": "assistant", "content": ""})
        if etype == "content_block_start":
            try:
                index = int(event.get("index") or 0)
            except (TypeError, ValueError):
                index = 0
            block = event.get("content_block") if isinstance(event.get("content_block"), dict) else {}
            kind = str(block.get("type") or "")
            self.blocks[index] = {"type": kind, "id": str(block.get("id") or ""), "name": str(block.get("name") or ""), "arguments": "", "thinking": "", "signature": ""}
            if kind == "tool_use":
                self.tool_order.append(index)
                return self._chunk(
                    {
                        "tool_calls": [
                            {
                                "index": len(self.tool_order) - 1,
                                "id": self.blocks[index]["id"] or f"toolu_{index}",
                                "type": "function",
                                "function": {"name": self.blocks[index]["name"], "arguments": ""},
                            }
                        ]
                    }
                )
            return None
        if etype == "content_block_delta":
            try:
                index = int(event.get("index") or 0)
            except (TypeError, ValueError):
                index = 0
            delta = event.get("delta") if isinstance(event.get("delta"), dict) else {}
            kind = str(delta.get("type") or "")
            rec = self.blocks.get(index)
            if kind == "text_delta" and delta.get("text"):
                return self._chunk({"content": str(delta["text"])})
            if kind == "thinking_delta" and delta.get("thinking"):
                if rec is not None:
                    rec["thinking"] += str(delta["thinking"])
                return self._chunk({"reasoning_content": str(delta["thinking"])})
            if kind == "signature_delta" and delta.get("signature"):
                if rec is not None:
                    rec["signature"] += str(delta["signature"])
                return None
            if kind == "input_json_delta" and delta.get("partial_json") is not None:
                piece = str(delta.get("partial_json") or "")
                if rec is not None:
                    rec["arguments"] += piece
                    tool_index = self.tool_order.index(index) if index in self.tool_order else 0
                else:
                    tool_index = 0
                if not piece:
                    return None
                return self._chunk({"tool_calls": [{"index": tool_index, "function": {"arguments": piece}}]})
            return None
        if etype == "content_block_stop":
            try:
                index = int(event.get("index") or 0)
            except (TypeError, ValueError):
                index = 0
            rec = self.blocks.get(index)
            if rec and rec.get("type") == "thinking" and rec.get("thinking") and rec.get("signature"):
                self.thinking.append({"thinking": rec["thinking"], "signature": rec["signature"]})
            if rec and rec.get("type") == "tool_use" and not rec.get("arguments"):
                rec["arguments"] = "{}"
                tool_index = self.tool_order.index(index) if index in self.tool_order else 0
                return self._chunk({"tool_calls": [{"index": tool_index, "function": {"arguments": "{}"}}]})
            return None
        if etype == "message_delta":
            delta = event.get("delta") if isinstance(event.get("delta"), dict) else {}
            if delta.get("stop_reason"):
                self.stop_reason = str(delta["stop_reason"])
            self._note_usage(event.get("usage"))
            return None
        if etype == "message_stop":
            return b"__FINAL__"
        if etype == "error":
            err = event.get("error") if isinstance(event.get("error"), dict) else event
            self.error = str((err or {}).get("message") or event.get("message") or "upstream error")
            return b"__ERROR__"
        if etype == "ping":
            return None
        return None

    def finish_reason(self) -> str:
        if self.tool_order or self.stop_reason == "tool_use":
            return "tool_calls"
        if self.stop_reason == "max_tokens":
            return "length"
        return "stop"

    def tool_calls(self) -> list[dict]:
        calls = []
        for index in self.tool_order:
            rec = self.blocks.get(index) or {}
            if not rec.get("name"):
                continue
            calls.append(
                {
                    "id": rec.get("id") or f"toolu_{index}",
                    "type": "function",
                    "function": {"name": rec["name"], "arguments": rec.get("arguments") or "{}"},
                }
            )
        return calls

    def _final_chunks(self) -> list[bytes]:
        delta: dict = {}
        if self.thinking:
            delta["claude_thinking"] = self.thinking
        out = [self._chunk(delta, self.finish_reason(), usage=self._openai_usage() if self.usage else None)]
        out.append(b"data: [DONE]\n\n")
        return out


def _signature_retryable(message: str) -> bool:
    text = (message or "").lower()
    return "signature" in text and "thinking" in text


async def _translated_stream(resp: httpx.Response, client: httpx.AsyncClient, model: str, collected: dict | None = None):
    translator = _Translator(fuse_id(model))
    stream_status = "failed"
    try:
        try:
            async for event in _upstream_events(resp):
                out = translator._handle(event)
                if out is None:
                    continue
                if out == b"__FINAL__":
                    stream_status = "complete"
                    break
                if out == b"__ERROR__":
                    yield translator._error_chunk(translator.error)
                    yield b"data: [DONE]\n\n"
                    return
                yield out
        except (UpstreamProtocolError, httpx.HTTPError) as exc:
            translator.error = f"upstream stream failed ({type(exc).__name__})"
        except (GeneratorExit, asyncio.CancelledError):
            stream_status = "cancelled"
            raise
        if stream_status != "complete":
            yield translator._error_chunk(translator.error or "upstream stream ended before a terminal response")
            yield b"data: [DONE]\n\n"
            return
        for piece in translator._final_chunks():
            yield piece
    finally:
        await resp.aclose()
        await client.aclose()
        if collected is not None:
            collected.clear()
            collected.update(translator.usage or {})
            collected["stream_status"] = stream_status
            if translator.usage:
                collected["usage_status"] = "complete" if stream_status == "complete" else "partial"
            if translator.thinking:
                collected["claude_thinking"] = translator.thinking


async def _aggregate(resp: httpx.Response, client: httpx.AsyncClient, model: str) -> dict:
    translator = _Translator(fuse_id(model))
    text_parts: list[str] = []
    reasoning_parts: list[str] = []
    terminal = False
    try:
        async for event in _upstream_events(resp):
            etype = str(event.get("type") or "")
            if etype == "content_block_delta":
                delta = event.get("delta") if isinstance(event.get("delta"), dict) else {}
                if delta.get("type") == "text_delta" and delta.get("text"):
                    text_parts.append(str(delta["text"]))
                elif delta.get("type") == "thinking_delta" and delta.get("thinking"):
                    reasoning_parts.append(str(delta["thinking"]))
            out = translator._handle(event)
            if out == b"__FINAL__":
                terminal = True
                break
            if out == b"__ERROR__":
                break
    except (UpstreamProtocolError, httpx.HTTPError) as exc:
        translator.error = f"upstream stream failed ({type(exc).__name__})"
    finally:
        await resp.aclose()
        await client.aclose()
    if not terminal and not translator.error:
        translator.error = "upstream stream ended before a terminal response"
    if translator.error:
        return {"error": {"message": translator.error, "type": "api_error", "code": 502}, "_fuse_usage": translator.usage}
    message: dict = {"role": "assistant", "content": "".join(text_parts)}
    if reasoning_parts:
        message["reasoning_content"] = "".join(reasoning_parts)
    if translator.thinking:
        message["claude_thinking"] = translator.thinking
    calls = translator.tool_calls()
    if calls:
        message["tool_calls"] = calls
    body = {
        "id": translator.completion_id,
        "object": "chat.completion",
        "created": translator.created,
        "model": fuse_id(model),
        "choices": [{"index": 0, "message": message, "finish_reason": translator.finish_reason()}],
    }
    if translator.usage:
        body["usage"] = dict(translator.usage)
    return body


def _chat_headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
        "anthropic-version": _ANTHROPIC_VERSION,
        "anthropic-beta": _BETA,
    }


def _remember_models(rows: list[dict]) -> list[str]:
    global _models_cache, _catalog_meta
    names = []
    meta = {}
    for row in rows:
        fid = fuse_id(str(row.get("id") or ""))
        if not fid or not is_claude_model(fid) or fid in names:
            continue
        names.append(fid)
        meta[upstream_id(fid)] = row
    if not names:
        names = list(CLAUDE_MODELS)
        meta = {upstream_id(mid): {"id": upstream_id(mid), **CLAUDE_MODEL_META[upstream_id(mid)]} for mid in names}
    else:
        for seeded in CLAUDE_MODELS:
            if seeded not in names:
                names.append(seeded)
            slug = upstream_id(seeded)
            meta.setdefault(slug, {"id": slug, **CLAUDE_MODEL_META.get(slug, {})})
    _catalog_meta = meta
    _models_cache = (time.time(), names)
    return names


async def fetch_catalog() -> list[str]:
    now = time.time()
    if _models_cache and now - _models_cache[0] < 300 and _models_cache[1]:
        return list(_models_cache[1])
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

    def _pick(self, exclude: set[int] | None = None, prefer_id: int | None = None):
        now = datetime.now(timezone.utc)
        return pick_account(
            self.creds,
            kind="claude",
            exclude=exclude,
            is_held=lambda c: is_held(c, now),
            is_authed=credential_is_authed,
            prefer_id=prefer_id,
        )

    async def models(self) -> list[str]:
        names = await fetch_catalog()
        return names or list(CLAUDE_MODELS)

    async def _post(self, client: httpx.AsyncClient, token: str, payload: dict):
        url = settings.claude_api_base.rstrip("/") + "/v1/messages"
        resp = await client.post(url, headers=_chat_headers(token), json=payload)
        return resp

    async def chat(self, body: dict, *, session_id: str | None = None, user_id: int | None = None) -> ChatResult:
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
        want_stream = body.get("stream") is not False
        payload = build_messages_payload(body, upstream)
        prefer_id = lookup("claude", session_id)
        signature_stripped = False
        while True:
            picked = self._pick(exclude=tried, prefer_id=prefer_id)
            if not picked:
                if attempts and last.credential_id == attempts[-1].get("credential_id"):
                    attempts.pop()
                last.attempts = attempts
                return last
            cred, extra = picked
            tried.add(int(cred["id"]))
            note_pool_invoke(cred["id"])
            reserve(cred["id"])
            token = _bearer(cred, extra)
            client = httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=15.0))
            stream_reserved = False
            try:
                resp = await self._post(client, token, payload)
                status = resp.status_code
                if status == 401 and extra.get("refresh_token"):
                    await resp.aclose()
                    await client.aclose()
                    ok = await refresh_one(cred)
                    if ok:
                        attempts.append({"credential_id": cred["id"], "status": 401, "usage": {}})
                        extra = accounts.cred_extra(db.fetchone("SELECT * FROM credentials WHERE id=?", (cred["id"],)))
                        token = _bearer(cred, extra)
                        client = httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=15.0))
                        resp = await self._post(client, token, payload)
                        status = resp.status_code
                if status == 400 and not signature_stripped:
                    raw = (await resp.aread()).decode("utf-8", errors="replace")
                    await resp.aclose()
                    await client.aclose()
                    if _signature_retryable(raw):
                        signature_stripped = True
                        payload = _drop_thinking(payload)
                        tried.discard(int(cred["id"]))
                        release(cred["id"])
                        stream_reserved = True
                        continue
                    last = ChatResult(
                        status=400,
                        body={"error": {"message": _error_message(raw) or "Claude rejected the request"}},
                        credential_id=cred["id"],
                        model=fuse_id(upstream),
                    )
                    last.attempts = attempts
                    return last
                if status != 200:
                    await resp.aclose()
                    await client.aclose()
                    last = ChatResult(
                        status=status,
                        body={"error": {"message": f"Claude request failed ({status})"}},
                        credential_id=cred["id"],
                        model=fuse_id(upstream),
                    )
                    if status == 401:
                        disable_credential(cred["id"])
                        last.status = 502
                        last.body = {"error": {"message": "Claude rejected the configured account"}}
                    if status in (429, 500, 502, 503, 529):
                        note_failure(cred["id"], cooldown_seconds=60 if status == 429 else 15)
                        attempts.append({"credential_id": cred["id"], "status": status, "usage": {}})
                        last.attempts = attempts
                        continue
                    last.attempts = attempts
                    return last
                if want_stream:
                    collected: dict = {}
                    stream = _translated_stream(resp, client, upstream, collected)
                    stream_reserved = True
                    return self._stream_result(stream, collected, cred, session_id, fuse_id(upstream), attempts)
                obj = await _aggregate(resp, client, upstream)
                usage = obj.get("usage") if isinstance(obj.get("usage"), dict) else obj.get("_fuse_usage") or {}
                if obj.get("error"):
                    last = ChatResult(status=502, body=obj, usage=usage if isinstance(usage, dict) else {}, credential_id=cred["id"], model=fuse_id(upstream))
                    last.attempts = attempts
                    return last
                bind("claude", session_id, cred["id"])
                last = ChatResult(status=200, body=obj, usage=usage if isinstance(usage, dict) else {}, credential_id=cred["id"], model=fuse_id(upstream))
                last.attempts = attempts
                return last
            except httpx.HTTPError:
                note_failure(cred["id"], cooldown_seconds=15)
                attempts.append({"credential_id": cred["id"], "status": 502, "usage": {}})
                last = ChatResult(status=502, body={"error": {"message": "Claude request failed"}}, credential_id=cred["id"], model=fuse_id(upstream))
                last.attempts = attempts
                try:
                    await client.aclose()
                except Exception:
                    pass
                continue
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
                    note_failure(cid, cooldown_seconds=5)

        wrapped = UsageStream(_bind_on_finish(), collected, on_close=lambda: release(cid))
        result = ChatResult(status=200, stream=wrapped, credential_id=cid, model=model)
        result.attempts = attempts
        return result


def _drop_thinking(payload: dict) -> dict:
    out = dict(payload)
    out.pop("thinking", None)
    messages = []
    for msg in payload.get("messages") or []:
        if not isinstance(msg, dict):
            messages.append(msg)
            continue
        content = msg.get("content")
        if msg.get("role") == "assistant" and isinstance(content, list):
            kept = [block for block in content if not (isinstance(block, dict) and block.get("type") == "thinking")]
            messages.append({**msg, "content": kept or [{"type": "text", "text": ""}]})
        else:
            messages.append(msg)
    out["messages"] = messages
    return out


def _error_message(raw: str) -> str:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return ""
    if not isinstance(data, dict):
        return ""
    err = data.get("error")
    if isinstance(err, dict):
        return str(err.get("message") or "")[:400]
    return str(data.get("message") or "")[:400]
