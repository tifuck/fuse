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
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx

from .. import accounts, db, file_images
from ..async_work import durable_call
from ..config import settings
from ..crypto import encrypt
from ..proxy import UsageStream, usage_from_obj
from .base import ChatResult
from . import cursor_agent as agent
from .cursor_agent import TurnError
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
#
# The CLI lists models with AiService.AvailableModels(useModelParameters):
# each base model carries parameter definitions and its concrete variants.
# Fuse folds the effort-like parameters (reasoning, effort, reasoning_effort,
# boolean thinking) into its effort picker and turns the rest into sibling ids:
# `:fast` / `:slow` for the fast parameter, `@value` for others (context
# windows), `:max` for Max Mode. A request then becomes the CLI's
# RequestedModel{model_id, parameters, max_mode}. GetUsableModels (flattened
# legacy slugs, no parameters) is the fallback.

_EFFORT_PARAMS = ("reasoning", "effort", "reasoning_effort")
_EFFORT_ALIASES = {"extra-high": "xhigh", "extra_high": "xhigh", "off": "none", "false": "none"}
_EFFORT_ORDER = ("none", "minimal", "low", "medium", "high", "xhigh", "max")
# fuse id -> {"model", "name", "efforts": {label: [(id, value)]}, "default", "max", "window", "fast"}
_catalog: dict[str, dict] = {}


def _variant_params(variant: dict) -> dict[str, str]:
    out = {}
    for item in variant.get("parameterValues") or []:
        if isinstance(item, dict) and item.get("id"):
            out[str(item["id"])] = str(item.get("value") if item.get("value") is not None else "")
    return out


def _effort_label(params: dict[str, str]) -> str:
    for pid in _EFFORT_PARAMS:
        if pid in params:
            value = params[pid].strip().lower()
            return _EFFORT_ALIASES.get(value, value)
    if "thinking" in params:
        return "none" if params["thinking"].strip().lower() in ("false", "off", "0") else "high"
    return ""


def _context_tokens(value: str) -> int:
    raw = value.strip().lower()
    scale = {"k": 1_000, "m": 1_000_000}.get(raw[-1:], 1)
    try:
        return int(float(raw.rstrip("km")) * scale)
    except ValueError:
        return 0


def parse_available_models(models: list) -> list[dict]:
    rows: list[dict] = []
    for m in models:
        if not isinstance(m, dict) or m.get("isHidden") or m.get("supportsAgent") is False:
            continue
        base = str(m.get("name") or "").strip()
        if not base:
            continue
        title = str(m.get("clientDisplayName") or base)
        variants = [v for v in m.get("variants") or [] if isinstance(v, dict)]
        if not variants:
            variants = [{"parameterValues": [], "isMaxMode": m.get("supportsNonMaxMode") is False}]
        default = (
            next((v for v in variants if v.get("isDefaultNonMaxConfig")), None)
            or next((v for v in variants if v.get("isDefaultMaxConfig")), None)
            or variants[0]
        )

        def key_of(variant: dict) -> tuple:
            params = _variant_params(variant)
            rest = tuple(sorted((k, v) for k, v in params.items() if k not in _EFFORT_PARAMS and k != "thinking"))
            return rest, bool(variant.get("isMaxMode"))

        base_rest, base_max = key_of(default)
        groups: dict[tuple, list[dict]] = {}
        for variant in variants:
            groups.setdefault(key_of(variant), []).append(variant)
        for (rest, max_mode), members in groups.items():
            plain = dict(base_rest)
            suffix, words, window = "", [], 0
            for pid, value in rest:
                if plain.get(pid) == value:
                    continue
                if pid == "fast":
                    suffix += ":fast" if value == "true" else ":slow"
                    words.append("Fast" if value == "true" else "Standard")
                else:
                    suffix += f"@{value}"
                    words.append(value.upper())
                    window = _context_tokens(value) if pid == "context" else window
            if max_mode != base_max:
                suffix += ":max" if max_mode else ":nomax"
                words.append("Max" if max_mode else "Standard")
            efforts: dict[str, list[tuple[str, str]]] = {}
            for variant in members:
                efforts.setdefault(_effort_label(_variant_params(variant)), sorted(_variant_params(variant).items()))
            lead = default if default in members else next(
                (v for v in members if v.get("isDefaultNonMaxConfig") or v.get("isDefaultMaxConfig")), members[0]
            )
            limit = m.get("contextTokenLimitForMaxMode") if max_mode else m.get("contextTokenLimit")
            try:
                window = window or int(limit or 0)
            except (TypeError, ValueError):
                window = 0
            rows.append({
                "id": PREFIX + base + suffix,
                "model": base,
                "name": " ".join([title, *words]),
                "efforts": efforts,
                "default": _effort_label(_variant_params(lead)),
                "max": max_mode,
                "window": window,
                "fast": dict(rest).get("fast", plain.get("fast", "")) == "true",
                "images": m.get("supportsImages") if isinstance(m.get("supportsImages"), bool) else None,
            })
    return rows


def _legacy_rows(models: list) -> list[dict]:
    rows = []
    for item in models:
        mid = str(item.get("modelId") or "") if isinstance(item, dict) else ""
        if mid:
            name = str(item.get("displayName") or item.get("displayNameShort") or mid)
            rows.append({"id": PREFIX + mid, "model": mid, "name": name, "efforts": {"": []}, "default": "",
                         "max": bool(item.get("maxMode")), "window": 0, "fast": False})
    return rows


def _remember_catalog(rows: list[dict]) -> list[str]:
    global _models_cache
    prior = list(_models_cache[1]) if _models_cache else list(CURSOR_MODELS)
    if not rows:
        return prior
    for row in rows:
        _catalog[row["id"]] = row
        _labels[row["id"]] = row["name"][:80]
    names = list(dict.fromkeys(row["id"] for row in rows))
    # Upstream order first, then ids an earlier listing had.
    names += [mid for mid in prior if mid not in names]
    _models_cache = (time.time(), names)
    return names


async def fetch_catalog(token: str) -> list[str]:
    if _models_cache and time.time() - _models_cache[0] < 60:
        return list(_models_cache[1])
    rows: list[dict] = []
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            status, data = await _unary_body(
                client, "/aiserver.v1.AiService/AvailableModels", token,
                {"useModelParameters": True, "doNotUseMarkdown": True},
            )
            if status == 200 and isinstance(data.get("models"), list):
                rows = parse_available_models(data["models"])
            if not rows:
                status, data = await _unary(client, "/agent.v1.AgentService/GetUsableModels", token)
                if status == 200 and isinstance(data.get("models"), list):
                    rows = _legacy_rows(data["models"])
    except httpx.HTTPError:
        rows = []
    return _remember_catalog(rows)


async def _unary_body(client: httpx.AsyncClient, path: str, token: str, body: dict) -> tuple[int, dict]:
    r = await client.post(_base() + path, json=body, headers={**_headers(token), "Content-Type": "application/json"})
    try:
        data = r.json()
    except ValueError:
        data = {}
    return r.status_code, data if isinstance(data, dict) else {}


def efforts_for(model: str | None) -> tuple[str, ...]:
    row = _catalog.get(model or "")
    labels = [label for label in (row or {}).get("efforts", {}) if label]
    if len(labels) < 2:
        return ()
    known = [label for label in _EFFORT_ORDER if label in labels]
    return tuple(known + [label for label in labels if label not in known])


def default_effort(model: str | None) -> str:
    row = _catalog.get(model or "") or {}
    return row.get("default", "") if row.get("default") in efforts_for(model) else ""


def model_window(model: str) -> int | None:
    return int((_catalog.get(model) or {}).get("window") or 0) or None


def resolve(model: str, effort: str | None) -> dict:
    """A Fuse id plus requested effort as the CLI's RequestedModel."""
    fid = model if model.startswith(PREFIX) else PREFIX + model
    row = _catalog.get(fid)
    if not row:
        mid = upstream_id(fid)
        return {"model_id": mid, "params": [], "max": False, "name": mid, "slug": mid}
    efforts = row["efforts"]
    label = str(effort or "").strip().lower()
    label = _EFFORT_ALIASES.get(label, label)
    if label not in efforts:
        label = row["default"] if row["default"] in efforts else next(iter(efforts))
    # Usage records the base model with its tier tails, which pricing strips.
    slug = "-".join(part for part in (row["model"], label, "fast" if row["fast"] else "", "max" if row["max"] else "") if part)
    return {"model_id": row["model"], "params": efforts[label], "max": row["max"], "name": row["name"], "slug": slug}


# --- Images ---------------------------------------------------------------------
#
# As in Cursor's SDK: a user's images go up inline with the mime type sniffed
# from the bytes; a tool's images are normalized first (long side at most
# 1024 px, at most 1 MiB), and one that cannot be decoded becomes a note.

TOOL_IMAGE_SIDE = 1024
TOOL_IMAGE_BYTES = 1024 * 1024
_tool_images: OrderedDict[str, tuple | str] = OrderedDict()


def _sniff(data: bytes, declared: str) -> str:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return declared


async def _image_part(part: dict, *, tool: bool) -> dict:
    image = part.get("image_url")
    url = image.get("url") if isinstance(image, dict) else image
    url = url if isinstance(url, str) else ""
    if not url.startswith("data:"):
        note = f"[Image: {url}]" if url.startswith(("https://", "http://")) else "[Image omitted]"
        return {"type": "text", "text": note}
    header, _, payload = url.partition(",")
    mime = header[5:].split(";", 1)[0] or "image/png"
    try:
        data = base64.b64decode(payload)
    except ValueError:
        return {"type": "text", "text": "[Image omitted: invalid image data]"}
    if not tool:
        return {"type": "image_bytes", "mime": _sniff(data, mime), "data": data}
    key = hashlib.sha256(data).hexdigest()
    hit = _tool_images.get(key)
    if hit is None:
        try:
            out = await file_images.decode_image(data, max_side=TOOL_IMAGE_SIDE, max_bytes=TOOL_IMAGE_BYTES)
            hit = (out["mime"], base64.b64decode(out["data"]))
        except Exception:
            hit = f"[Image omitted: the tool returned {mime} data ({len(data)} bytes) that could not be decoded.]"
        _tool_images[key] = hit
        while len(_tool_images) > 64:
            _tool_images.popitem(last=False)
    _tool_images.move_to_end(key)
    if isinstance(hit, str):
        return {"type": "text", "text": hit}
    return {"type": "image_bytes", "mime": hit[0], "data": hit[1]}


async def _resolve_images(messages) -> list[dict]:
    """Copies of the messages with image_url parts turned into image bytes."""
    out = []
    for msg in messages or []:
        if not isinstance(msg, dict):
            continue
        content = msg.get("content")
        if not isinstance(content, list) or not any(isinstance(p, dict) and p.get("type") == "image_url" for p in content):
            out.append(msg)
            continue
        tool = msg.get("role") == "tool"
        parts = [
            await _image_part(p, tool=tool) if isinstance(p, dict) and p.get("type") == "image_url" else p
            for p in content
        ]
        out.append({**msg, "content": parts})
    return out


def image_support(model: str) -> bool | None:
    """Whether Cursor's catalog says this model reads images (None: unknown)."""
    fid = model if model.startswith(PREFIX) else PREFIX + model
    value = (_catalog.get(fid) or {}).get("images")
    return value if isinstance(value, bool) else None


# --- Chat -----------------------------------------------------------------------

def _sse(obj: dict) -> bytes:
    return f"data: {json.dumps(obj, separators=(',', ':'))}\n\n".encode()


def _tool_call(index: int, call: dict) -> dict:
    return {"index": index, "id": call["id"], "type": "function",
            "function": {"name": call["name"], "arguments": call["arguments"]}}


def _reply(text: list[str], calls: list[dict]) -> dict:
    """The assistant message this round adds to the conversation."""
    message: dict = {"role": "assistant", "content": "".join(text)}
    if calls:
        message["tool_calls"] = [{k: v for k, v in call.items() if k != "index"} for call in calls]
    return message


class _Round:
    """One request's share of a Cursor run, and what to do when it ends."""

    def __init__(self, run: agent.CursorRun, conv: agent.Conversation, messages, first: tuple, events):
        self.run = run
        self.conv = conv
        self.messages = messages
        self.first = first
        self.events = events
        self.kept = False

    async def finish(self, text: list[str], calls: list[dict]) -> None:
        agent.keep(self.conv, self.messages, _reply(text, calls), self.run)
        self.kept = True

    async def close(self) -> None:
        await self.events.aclose()
        if not self.kept:
            agent.drop(self.conv)
            await self.run.aclose()


async def _stream(rnd: _Round, collected: dict, model: str):
    cid = "chatcmpl-" + uuid.uuid4().hex[:24]
    created = int(time.time())

    def chunk(delta: dict, finish=None, usage=None) -> bytes:
        obj = {"id": cid, "object": "chat.completion.chunk", "created": created, "model": model,
               "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
        if usage is not None:
            obj["usage"] = usage
        return _sse(obj)

    usage: dict = {}
    text: list[str] = []
    calls: list[dict] = []
    try:
        yield chunk({"role": "assistant"})
        event = rnd.first
        while True:
            kind, value = event
            if kind == "text":
                text.append(value)
                yield chunk({"content": value})
            elif kind == "thinking":
                yield chunk({"reasoning_content": value})
            elif kind == "tool_call":
                calls.append(_tool_call(len(calls), value))
                yield chunk({"tool_calls": [calls[-1]]})
            elif kind == "usage":
                usage = value
            elif kind == "end":
                if usage.get("prompt_tokens") or usage.get("completion_tokens"):
                    collected.update(usage_from_obj({"usage": usage}))
                await rnd.finish(text, calls)
                yield chunk({}, value, usage or None)
                yield b"data: [DONE]\n\n"
                collected["stream_status"] = "complete"
                return
            event = await rnd.events.__anext__()
    except TurnError as exc:
        collected["stream_status"] = "failed"
        yield _sse({"error": {"message": exc.message, "code": exc.status}})
    except Exception:
        collected["stream_status"] = "failed"
        raise
    finally:
        await rnd.close()


async def _collect(rnd: _Round, model: str) -> tuple[dict, dict]:
    text, thinking, calls, usage, finish = [], [], [], {}, "stop"
    event = rnd.first
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
                await rnd.finish(text, calls)
                break
            event = await rnd.events.__anext__()
    finally:
        await rnd.close()
    message = _reply(text, calls)
    message["content"] = message["content"] or None
    if thinking:
        message["reasoning_content"] = "".join(thinking)
    obj = {
        "id": "chatcmpl-" + uuid.uuid4().hex[:24],
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "message": message, "finish_reason": finish}],
        "usage": usage or {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
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

    async def _open(self, extra: dict, cid: int, user_id, body: dict, sel: dict, plan: tuple) -> _Round:
        """Start this request's round: hand tool results to a parked run,
        continue a finished turn from its checkpoint, or replay the history
        into a new conversation."""
        how, conv, tail = plan
        messages = body.get("messages")
        tools = agent.mcp_tools(body.get("tools"))
        if how == "resume":
            run = conv.run
            results = await _resolve_images(tail)
            await run.resume([
                (str(m.get("tool_call_id") or ""), agent._text(m.get("content")), agent.images_of(m.get("content")))
                for m in results
            ])
        else:
            history = None
            images: list = []
            if how == "followup":
                state = conv.checkpoint
                news = await _resolve_images(tail)
                user_text = "\n\n".join(t for t in (agent._text(m.get("content")) for m in news) if t)
                images = [image for m in news for image in agent.images_of(m.get("content"))]
            elif how == "blobs":
                # Text-only replay as root prompt blobs: the fallback when the
                # structured history is refused.
                conv = agent.Conversation(user_id=user_id, cred_id=cid)
                entries, user_text = agent.build_turn(messages)
                ids = []
                for entry in entries:
                    data = json.dumps(entry, ensure_ascii=False, separators=(",", ":")).encode()
                    digest = hashlib.sha256(data).digest()
                    conv.blobs[digest] = data
                    ids.append(digest)
                state = agent.encode_state(ids)
            else:
                conv = agent.Conversation(user_id=user_id, cred_id=cid)
                earlier, active = agent.split_turn(await _resolve_images(messages))
                history = agent.encode_history(earlier) or None
                user_text = agent._text(active.get("content")) if active else agent.CONTINUE_TEXT
                images = agent.images_of(active.get("content")) if active else []
                state = agent.encode_state([])
            run = agent.CursorRun(base=_base(), headers=_headers(extra.get("access_token") or ""),
                                  timeout=_CHAT_TIMEOUT, blobs=conv.blobs, tools=tools)
            await run.start(agent.encode_run_request(
                state=state, user_text=user_text, model_id=sel["model_id"], params=sel["params"],
                max_mode=sel["max"], display_name=sel["name"], tools=tools, conversation_id=conv.conversation_id,
                images=images, history=history,
            ))
        conv.busy = True
        conv.selection = (sel["model_id"], tuple(sel["params"]), sel["max"])
        events = run.round()
        try:
            first = await events.__anext__()
        except BaseException:
            await events.aclose()
            agent.drop(conv)
            await run.aclose()
            raise
        return _Round(run, conv, messages, first, events)

    async def chat(self, body: dict, *, session_id: str | None = None, user_id: int | None = None, affinity_id: str | None = None) -> ChatResult:
        await refresh_due()
        await refresh_pool_quota()
        self._load(user_id)
        reasoning = body.get("reasoning") if isinstance(body.get("reasoning"), dict) else {}
        sel = resolve(body.get("model") or "auto", body.get("reasoning_effort") or reasoning.get("effort"))
        model = PREFIX + upstream_id(body.get("model") or "auto")
        conv, how, tail = agent.match(user_id, body.get("messages"))
        if how == "resume" and conv.selection != (sel["model_id"], tuple(sel["params"]), sel["max"]):
            # A parked run cannot change model mid-turn; replay on the new one.
            agent.drop(conv)
            conv, how = None, ""
        plan = (how, conv, tail) if conv else ("fresh", None, [])
        tried: set[int] = set()
        attempts: list[dict] = []
        last = ChatResult(status=503, body={"error": "No Cursor accounts in the pool"})
        prefer_id = conv.cred_id if conv else lookup("cursor", session_id)
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
            if plan[1] is not None and plan[1].cred_id != cid:
                # That conversation lives on another account.
                agent.drop(plan[1])
                plan = ("fresh", None, [])
            tried.add(cid)
            note_pool_invoke(cid)
            reserve(cid)
            try:
                rnd = await self._open(extra, cid, user_id, body, sel, plan)
            except TurnError as exc:
                release(cid)
                if plan[0] == "fresh" and exc.status == 400:
                    # Structured history refused: replay as text blobs.
                    plan = ("blobs", None, [])
                    tried.discard(cid)
                    continue
                if plan[0] in ("resume", "followup"):
                    # The kept state is gone (expired run, unknown
                    # checkpoint): replay the history on this account.
                    agent.drop(plan[1])
                    plan = ("fresh", None, [])
                    tried.discard(cid)
                    continue
                last = ChatResult(status=exc.status, body={"error": {"message": exc.message}}, credential_id=cid, model=sel["slug"])
                attempts.append({"credential_id": cid, "status": exc.status, "usage": {}})
                if exc.status == 401:
                    if cid not in refreshed and await refresh_one(cred):
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
                raise
            failover.served()
            if body.get("stream"):
                return self._stream_result(rnd, cid, session_id, model, sel["slug"], attempts)
            try:
                obj, usage = await _collect(rnd, model)
            except TurnError as exc:
                last = ChatResult(status=exc.status, body={"error": {"message": exc.message}}, credential_id=cid, model=sel["slug"])
                last.attempts = attempts
                return last
            finally:
                release(cid)
            bind("cursor", session_id, cid)
            result = ChatResult(status=200, body=obj, usage=usage, credential_id=cid, model=sel["slug"])
            result.attempts = attempts
            return result

    def _stream_result(self, rnd: _Round, cid: int, session_id: str | None, model: str, slug: str, attempts: list[dict]) -> ChatResult:
        collected: dict = {}

        async def _bind_on_finish():
            inner = _stream(rnd, collected, model)
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
        result = ChatResult(status=200, stream=wrapped, credential_id=cid, model=slug)
        result.attempts = attempts
        return result


