"""NanoGPT pool.

Sign-in is NanoGPT's OAuth PKCE key handoff (browser to `/auth`, back to
Fuse's callback, code exchanged at `POST /api/v1/auth/keys`), its CLI device
login, or a pasted `sk-nano-` key. Every path yields a long-lived API key:
there is no refresh token and no revoke endpoint.

Two lanes. A subscription-included model may run on another user's shared
key only when that key cannot reach its owner's balance (billing mode
"Subscription only" or a $0 cap): NanoGPT bills a "subscription first" key's
balance once quota runs out, and provider pins, cache markers or billing_mode
switch a request to pay-as-you-go. Paid models, and keys that can spend
balance, serve only their owner.

Chat, catalog and quota use the direct API host; sign-in uses the website.
Probes read usage and the catalog only, never a completion.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import json
import math
import re
import secrets
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import quote, urlencode, urlparse

import httpx

from .. import accounts, db, file_images
from ..config import settings
from ..crypto import DecryptionError, encrypt
from ..proxy import UsageStream, openai_chat
from . import pool_policy
from .base import EFFORTS, ChatResult, map_effort
from .grok import apply_usage_guard, is_held
from .pool_pick import Failover, auth_failure_confirmed, note_failure, pick_account, release, reserve
from .pool_session import bind, drop_cache_routing_fields, lookup
from .token_store import log_refresh_failures

PUBLIC_PREFIX = "nanogpt/"
SCOPE = "api.use models.read"
USAGE_TTL = 300
CATALOG_TTL = 1800.0
PROVIDERS_TTL = 3600.0
MAX_PAID_MODELS = 500
MAX_PINNED_MODELS = 200
MAX_PINS = 50
# Chat Completions fields a request on someone else's key may carry. Anything
# else (provider, billing_mode, prompt_caching, cache_control, caching,
# web_search, memory, advisor, service_tier, …) can move it to pay-as-you-go
# on the donor's balance or add paid extras.
_BORROWED_FIELDS = frozenset(
    {
        "model", "messages", "stream", "stream_options", "temperature", "top_p", "top_k", "min_p",
        "max_tokens", "max_completion_tokens", "stop", "n", "seed", "presence_penalty",
        "frequency_penalty", "repetition_penalty", "logit_bias", "logprobs", "top_logprobs",
        "response_format", "tools", "tool_choice", "parallel_tool_calls", "user", "reasoning_effort",
    }
)
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)

_catalog: tuple[float, dict] | None = None
_catalog_ok = False
_providers: dict[str, tuple[float, list[dict]]] = {}
_refresh_locks: dict[int, asyncio.Lock] = {}


def _web() -> str:
    return settings.nanogpt_web_base.rstrip("/")


def _api() -> str:
    return settings.nanogpt_api_base.rstrip("/")


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


def ensure_nanogpt_source() -> int:
    from . import _ensure_typed_source

    return _ensure_typed_source("nanogpt", "NanoGPT", {})


# --- Model ids and catalog ---------------------------------------------------

def public_id(upstream: str) -> str:
    return PUBLIC_PREFIX + upstream


def upstream_id(model: str) -> str:
    mid = (model or "").strip()
    return mid[len(PUBLIC_PREFIX):] if mid.startswith(PUBLIC_PREFIX) else mid


def is_nanogpt_model(model: str) -> bool:
    return (model or "").startswith(PUBLIC_PREFIX)


def parse_catalog(payload) -> dict:
    """Text models from `GET /v1/models?detailed=true`, keyed by NanoGPT id."""
    by_id: dict[str, dict] = {}
    for item in (payload.get("data") if isinstance(payload, dict) else None) or []:
        if not isinstance(item, dict):
            continue
        mid = item.get("id")
        if not isinstance(mid, str) or not mid.strip() or len(mid) > 200 or any(ord(ch) < 33 for ch in mid):
            continue
        arch = item.get("architecture") if isinstance(item.get("architecture"), dict) else {}
        outputs = arch.get("output_modalities")
        if isinstance(outputs, list) and "text" not in outputs:
            continue
        caps = item.get("capabilities") if isinstance(item.get("capabilities"), dict) else {}
        sub = item.get("subscription") if isinstance(item.get("subscription"), dict) else {}
        efforts = item.get("reasoning_efforts")
        by_id[mid] = {
            "name": str(item.get("name") or mid)[:120],
            "subscription": sub.get("included") is True,
            "reasoning_efforts": [e for e in EFFORTS if isinstance(efforts, list) and e in efforts],
            "image": caps["vision"] if isinstance(caps.get("vision"), bool) else None,
            "context_length": _as_int(item.get("context_length")),
            "max_output_tokens": _as_int(item.get("max_output_tokens")),
            "pricing": item.get("pricing") if isinstance(item.get("pricing"), dict) else {},
        }
    return {"by_id": by_id}


async def fetch_catalog(*, force: bool = False) -> dict:
    """The public text catalog. The same for every key, so one shared copy."""
    global _catalog, _catalog_ok
    if not force and _catalog and time.time() - _catalog[0] < CATALOG_TTL:
        return _catalog[1]
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.get(_api() + "/v1/models", params={"detailed": "true"})
            r.raise_for_status()
            catalog = parse_catalog(r.json())
    except (httpx.HTTPError, ValueError):
        catalog = {"by_id": {}}
    if not catalog["by_id"]:
        _catalog_ok = False
        return _catalog[1] if _catalog else catalog
    _catalog = (time.time(), catalog)
    _catalog_ok = True
    return catalog


def cached_catalog() -> dict | None:
    return _catalog[1] if _catalog else None


def catalog_available() -> bool:
    return _catalog_ok


def catalog_record(model: str) -> dict | None:
    return ((cached_catalog() or {}).get("by_id") or {}).get(upstream_id(model))


async def refresh_due() -> None:
    """Refresh-loop job: re-read the catalog once it ages past CATALOG_TTL."""
    if _catalog and time.time() - _catalog[0] < CATALOG_TTL:
        return
    await fetch_catalog(force=True)


def efforts_for(model: str | None) -> tuple[str, ...]:
    rec = catalog_record(model or "") or {}
    return tuple(rec.get("reasoning_efforts") or ())


# --- Per-user model list (users.pool_prefs["nanogpt"]["models"]) ------------

def model_prefs(user_id: int | None) -> dict:
    raw = {}
    if user_id:
        item = pool_policy.user_prefs(int(user_id)).get("nanogpt")
        raw = item.get("models") if isinstance(item, dict) and isinstance(item.get("models"), dict) else {}
    paid = raw.get("paid") if isinstance(raw.get("paid"), list) else []
    pins = raw.get("providers") if isinstance(raw.get("providers"), dict) else {}
    return {
        "subscription": raw.get("subscription", True) is not False,
        "paid_all": raw.get("paid_all") is True,
        "paid": [m for m in paid if isinstance(m, str) and m][:MAX_PAID_MODELS],
        "providers": {
            str(mid): [s for s in slugs if isinstance(s, str) and s][:MAX_PINS]
            for mid, slugs in pins.items()
            if isinstance(slugs, list) and slugs
        },
    }


def _save_model_prefs(user_id: int, prefs: dict) -> None:
    uid = int(user_id)
    with db.tx() as conn:
        row = conn.execute("SELECT pool_prefs FROM users WHERE id=? FOR UPDATE", (uid,)).fetchone()
        try:
            data = json.loads((row["pool_prefs"] if row else None) or "{}")
        except (json.JSONDecodeError, TypeError):
            data = {}
        data = data if isinstance(data, dict) else {}
        item = dict(data.get("nanogpt")) if isinstance(data.get("nanogpt"), dict) else {}
        item["models"] = prefs
        data["nanogpt"] = item
        conn.execute("UPDATE users SET pool_prefs=? WHERE id=?", (json.dumps(data), uid))


def set_model_switches(user_id: int, *, subscription: bool | None = None, paid_all: bool | None = None) -> dict:
    prefs = model_prefs(user_id)
    if subscription is not None:
        prefs["subscription"] = bool(subscription)
    if paid_all is not None:
        if not paid_all and prefs["paid_all"]:
            prefs["paid"] = []
        prefs["paid_all"] = bool(paid_all)
    _save_model_prefs(user_id, prefs)
    return prefs


def set_paid_model(user_id: int, upstream: str, on: bool) -> bool:
    prefs = model_prefs(user_id)
    paid = [m for m in prefs["paid"] if m != upstream]
    if on:
        if len(paid) >= MAX_PAID_MODELS:
            return False
        paid.append(upstream)
    prefs["paid"] = paid
    _save_model_prefs(user_id, prefs)
    return True


def set_model_providers(user_id: int, upstream: str, slugs: list[str]) -> bool:
    """Empty slugs restores NanoGPT's own routing for that model."""
    prefs = model_prefs(user_id)
    clean = list(dict.fromkeys(s.strip()[:64] for s in slugs if isinstance(s, str) and s.strip()))[:MAX_PINS]
    if clean:
        if upstream not in prefs["providers"] and len(prefs["providers"]) >= MAX_PINNED_MODELS:
            return False
        prefs["providers"][upstream] = clean
    else:
        prefs["providers"].pop(upstream, None)
    _save_model_prefs(user_id, prefs)
    return True


# --- Credentials, access and lanes ------------------------------------------

def _billing(extra: dict | None) -> dict:
    extra = extra if isinstance(extra, dict) else {}
    return extra.get("billing") if isinstance(extra.get("billing"), dict) else {}


def billing_has_subscription(billing: dict | None) -> bool:
    return isinstance(billing, dict) and billing.get("active") is True


def shareable(extra: dict | None) -> bool:
    """An active subscription on a key that cannot spend its owner's balance."""
    billing = _billing(extra)
    return billing_has_subscription(billing) and billing.get("allows_balance") is False


def lendable_row(row) -> bool:
    try:
        return shareable(accounts.cred_extra(row))
    except (DecryptionError, accounts.CredentialDataError):
        return False


def credential_is_authed(cred, extra: dict | None = None) -> bool:
    try:
        if cred["disabled"]:
            return False
    except (KeyError, IndexError, TypeError):
        return False
    err = str(_billing(extra if extra is not None else accounts.cred_extra(cred)).get("last_error") or "")
    return "401" not in err and "403" not in err


def disable_credential(cred_id: int, *, extra: str | None = None) -> None:
    accounts.disable_credential(cred_id, extra=extra)


def nanogpt_own_access(user_id: int) -> dict:
    sid = ensure_nanogpt_source()
    rows = db.fetchall(
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


def _nanogpt_live(row) -> bool:
    if is_held(row):
        return False
    extra = accounts.cred_extra(row)
    return credential_is_authed(row, extra) and shareable(extra)


def nanogpt_access_state(user_id: int) -> dict:
    own = nanogpt_own_access(user_id)
    if own["ok"]:
        return own
    return pool_policy.with_open_access(user_id, "nanogpt", own, _nanogpt_live)


def user_has_nanogpt(user_id: int) -> bool:
    return nanogpt_access_state(user_id)["ok"]


def note_pool_invoke(cred_id: int) -> None:
    db.execute("UPDATE credentials SET pool_invokes = COALESCE(pool_invokes, 0) + 1 WHERE id=?", (cred_id,))


def listable_models(names: list[str], user_id: int | None) -> list[str]:
    """Subscription models while the user's switch is on; paid ones only on
    their own key, per their selection (all of them, or the allowlist)."""
    if not user_id:
        return []
    by_id = (cached_catalog() or {}).get("by_id") or {}
    prefs = model_prefs(user_id)
    own = nanogpt_own_access(user_id)["ok"]
    paid = set(prefs["paid"])
    out = []
    for name in names:
        upstream = upstream_id(name)
        rec = by_id.get(upstream)
        if rec is None:
            continue
        if rec["subscription"]:
            if prefs["subscription"]:
                out.append(name)
        elif own and (prefs["paid_all"] or upstream in paid):
            out.append(name)
    return out


async def resolve(model: str, user_id: int | None) -> str | None:
    if not is_nanogpt_model(model):
        return None
    catalog = cached_catalog() or await fetch_catalog()
    upstream = upstream_id(model)
    if upstream not in catalog["by_id"]:
        return None
    return upstream if listable_models([model], user_id) else None


def denied_reason(model: str, user_id: int | None) -> str | None:
    rec = catalog_record(model) if is_nanogpt_model(model) else None
    if rec is None or not user_id:
        return None
    if not user_has_nanogpt(user_id):
        return "Connect a NanoGPT account to use NanoGPT models."
    if rec["subscription"]:
        if model_prefs(user_id)["subscription"]:
            return None
        return "NanoGPT subscription models are turned off for your account. Turn them on under Sources."
    if not nanogpt_own_access(user_id)["ok"]:
        return "Paid NanoGPT models run only on your own NanoGPT account. Sign in with NanoGPT on Sources."
    return "That paid NanoGPT model is not enabled for your account. Turn it on under Sources."


# --- Sign-in ------------------------------------------------------------------

def redirect_ok(uri: str) -> bool:
    """NanoGPT accepts HTTPS callbacks, or loopback HTTP with an explicit port."""
    p = urlparse(uri or "")
    host = (p.hostname or "").lower()
    if not host or p.fragment or p.username or p.password:
        return False
    if p.scheme == "https":
        return True
    return p.scheme == "http" and host in ("127.0.0.1", "localhost", "::1") and p.port is not None


def start_login(redirect_uri: str) -> dict:
    """One PKCE round. The verifier stays server-side in the pending sign-in."""
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    state = secrets.token_urlsafe(24)
    params = {
        "callback_url": redirect_uri,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "scope": SCOPE,
        "state": state,
        "client_name": (settings.nanogpt_client_name or "Fuse")[:64],
    }
    invitation = (settings.nanogpt_invitation_code or "").strip()
    if invitation:
        params["invitation_code"] = invitation[:256]
    return {"kind": "oauth", "verifier": verifier, "state": state, "auth_url": f"{_web()}/auth?{urlencode(params)}"}


def key_shape(raw: str) -> str:
    """An inference key (`sk-nano-…` or a legacy UUID), else ''. Management
    tokens (`sk-nano-mgmt-`) cannot run models."""
    key = (raw or "").strip()
    if key.startswith("sk-nano-mgmt-"):
        return ""
    if key.startswith("sk-nano-") and 12 <= len(key) <= 200 and all(33 <= ord(ch) <= 126 for ch in key):
        return key
    return key if _UUID.match(key) else ""


async def exchange_code(code: str, verifier: str) -> tuple[str, str]:
    """(api key, NanoGPT user id). ValueError carries a user-facing message."""
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            r = await client.post(
                _web() + "/api/v1/auth/keys",
                json={"grant_type": "authorization_code", "code": code, "code_verifier": verifier},
            )
    except httpx.HTTPError:
        raise ValueError("Could not reach NanoGPT. Try again.") from None
    try:
        data = r.json()
    except ValueError:
        data = {}
    if r.status_code != 200 or not isinstance(data, dict):
        raise ValueError("NanoGPT did not accept that sign-in. Start again.")
    key = key_shape(str(data.get("key") or data.get("access_token") or ""))
    if not key:
        raise ValueError("NanoGPT returned no API key. Start again.")
    return key, str(data.get("user_id") or "")[:80]


async def start_device() -> dict:
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.post(
            _web() + "/api/cli-login/start", json={"client_name": (settings.nanogpt_client_name or "Fuse")[:64]}
        )
    r.raise_for_status()
    data = r.json()
    if not isinstance(data, dict) or not isinstance(data.get("device_code"), str) or not data["device_code"]:
        raise ValueError("invalid device login response")
    verify = data.get("verification_uri_complete") or data.get("verification_uri") or ""
    return {
        "kind": "device",
        "device_code": data["device_code"],
        "user_code": str(data.get("user_code") or "")[:32],
        "verify": str(verify),
        "interval": _as_int(data.get("interval")) or 3,
    }


async def poll_device(device_code: str) -> dict:
    """{"key": …}, {"pending": True}, or {"error": status}."""
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.post(_web() + "/api/cli-login/poll", json={"device_code": device_code})
    try:
        data = r.json()
    except ValueError:
        data = {}
    data = data if isinstance(data, dict) else {}
    if r.status_code == 200 and data.get("status") == "approved":
        key = key_shape(str(data.get("key") or ""))
        return {"key": key} if key else {"error": "invalid key"}
    if r.status_code in (404, 409, 410):
        return {"error": str(data.get("status") or data.get("error") or r.status_code)}
    return {"pending": True}


def _find_key(source_id: int, key: str):
    for row in db.fetchall("SELECT * FROM credentials WHERE source_id=?", (source_id,)):
        try:
            if accounts.cred_secret(row) == key:
                return row
        except Exception:
            continue
    return None


def save_key(source_id: int, key: str, donated_by: int, *, label: str = "", auth_kind: str = "oauth", account_id: str = "") -> int:
    """NanoGPT hands the same key back for an unchanged grant, so a repeat
    sign-in re-enables the row it already has. An active key stays with its
    donor; another user signing into the same NanoGPT account never takes it."""
    existing = _find_key(source_id, key)
    if existing and not existing["disabled"] and int(existing["donated_by"] or 0) != int(donated_by):
        raise ValueError("pooled")
    extra = {"auth_kind": auth_kind, "account_id": account_id}
    name = (label or "").strip()[:80] or "NanoGPT account"
    if existing:
        db.execute(
            "UPDATE credentials SET disabled=0, donated_by=?, label=?, secret=?, extra=? WHERE id=?",
            (donated_by, name, encrypt(key), encrypt(json.dumps(extra)), existing["id"]),
        )
        return int(existing["id"])
    return accounts.add_credential(source_id, name, key, extra=extra, donated_by=donated_by)


# --- Usage --------------------------------------------------------------------

def _iso_ms(value) -> str:
    ms = _as_float(value)
    if ms is None or ms <= 0:
        return ""
    try:
        return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (OverflowError, OSError, ValueError):
        return ""


def parse_usage(data: dict) -> dict:
    """`GET /subscription/v1/usage`. percentUsed is a fraction and may pass 1;
    a null counter (degraded) is no reading, never an empty quota."""
    windows = []
    for field, name in (("dailyInputTokens", "Daily"), ("weeklyInputTokens", "Weekly")):
        raw = data.get(field)
        pct = _as_float(raw.get("percentUsed")) if isinstance(raw, dict) else None
        if pct is not None:
            windows.append({"name": name, "percent": max(0.0, min(100.0, pct * 100.0)), "reset_at": _iso_ms(raw.get("resetAt"))})
    routing = data.get("routing") if isinstance(data.get("routing"), dict) else {}
    period = data.get("period") if isinstance(data.get("period"), dict) else {}
    weekly = next((w for w in windows if w["name"] == "Weekly"), None)
    active = data.get("active") is True
    allows = routing.get("paidSpendPolicyAllowsBalance")
    return {
        "active": active,
        "state": str(data.get("state") or "")[:16],
        "plan_type": "subscription" if active else "pay as you go",
        "period_type": weekly["name"] if weekly else (windows[0]["name"] if windows else ""),
        # The weekly reset names the period the off-site guard compares within.
        "period_start": weekly["reset_at"] if weekly else "",
        "period_end": (weekly or windows[0])["reset_at"] if windows else str(period.get("currentPeriodEnd") or "")[:40],
        "windows": windows,
        "products": [{"product": w["name"], "usage_percent": w["percent"]} for w in windows],
        "credit_usage_percent": max((w["percent"] for w in windows), default=None),
        "allows_balance": allows if isinstance(allows, bool) else None,
        "recommended": str(routing.get("recommendedMode") or "")[:16],
    }


async def fetch_usage(key: str) -> tuple[dict | None, str]:
    """Quota and routing advice, plus the USD balance. Never a completion."""
    balance = None
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.get(_api() + "/subscription/v1/usage", headers=_headers(key))
            if r.status_code == 200:
                try:
                    b = await client.post(_api() + "/check-balance", headers={"x-api-key": key})
                    if b.status_code == 200 and isinstance(b.json(), dict):
                        balance = _as_float(b.json().get("usd_balance"))
                except (httpx.HTTPError, ValueError):
                    pass
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
    parsed = parse_usage(data)
    if balance is not None and balance >= 0:
        parsed["prepaid_cents"] = int(round(balance * 100))
    return parsed, ""


def _parse_ts(ts: str) -> datetime | None:
    raw = str(ts or "").replace("Z", "")
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f"):
        try:
            return datetime.strptime(raw.split("+")[0], fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _usage_fresh(billing: dict) -> bool:
    when = _parse_ts(billing.get("checked_at") or billing.get("fetched_at") or "")
    return bool(when and datetime.now(timezone.utc) - when < timedelta(seconds=USAGE_TTL))


def _store_billing(cred_id: int, parsed: dict, *, error: str = "") -> dict:
    row = db.fetchone("SELECT * FROM credentials WHERE id=?", (cred_id,))
    if not row:
        return {}
    extra = accounts.cred_extra(row)
    billing = _billing(extra)
    now = db.now()
    billing["checked_at"] = now
    if error:
        billing["last_error"] = error[:200]
        extra["billing"] = billing
        accounts.update_extra(cred_id, extra)
        return billing
    # A fresh snapshot replaces the old one: a lapsed plan must not keep
    # yesterday's windows or allows_balance.
    billing = {**parsed, "checked_at": now, "fetched_at": now, "last_error": ""}
    extra, held_until, last_pct = apply_usage_guard(row, extra, parsed, enabled=pool_policy.guard_enabled("nanogpt"))
    extra["billing"] = billing
    accounts.update_extra(cred_id, extra, last_usage_percent=last_pct, usage_percent_at=now, pool_held_until=held_until)
    return billing


def _refresh_lock(cred_id: int) -> asyncio.Lock:
    lock = _refresh_locks.get(cred_id)
    if lock is None:
        lock = asyncio.Lock()
        _refresh_locks[cred_id] = lock
    return lock


async def refresh_usage(cred, *, force: bool = False) -> dict | None:
    async with _refresh_lock(int(cred["id"])):
        row = db.fetchone("SELECT * FROM credentials WHERE id=?", (cred["id"],))
        if not row or row["disabled"]:
            return None
        billing = _billing(accounts.cred_extra(row))
        if not force and _usage_fresh(billing):
            return billing
        parsed, err = await fetch_usage(accounts.cred_secret(row))
        if parsed is None:
            return _store_billing(row["id"], {}, error=err or "fetch failed")
        return _store_billing(row["id"], parsed)


async def refresh_pool_usage(*, force: bool = False) -> None:
    rows = db.fetchall(
        "SELECT c.* FROM credentials c JOIN sources s ON s.id=c.source_id WHERE s.type='nanogpt' AND c.disabled=0"
    )
    if not rows:
        return
    results = await asyncio.gather(*(refresh_usage(c, force=force) for c in rows), return_exceptions=True)
    log_refresh_failures("NanoGPT usage refresh", rows, results)


async def add_key(source_id: int, raw: str, donated_by: int, *, label: str = "") -> tuple[int | None, str]:
    """A pasted inference key. Validated with the usage endpoint, not a completion."""
    key = key_shape(raw)
    if not key:
        return None, "Enter a NanoGPT API key (sk-nano-…)."
    parsed, err = await fetch_usage(key)
    if parsed is None:
        if err in ("http 401", "http 403"):
            return None, "NanoGPT rejected that key. Copy an active key from nano-gpt.com/api."
        return None, "Could not validate this key. Check the connection and try again."
    try:
        cred_id = save_key(source_id, key, donated_by, label=label, auth_kind="api")
    except ValueError:
        return None, "That NanoGPT key is already linked to another account."
    _store_billing(cred_id, parsed)
    return cred_id, ""


# --- Provider discovery ---------------------------------------------------------

def _per_million(per_1k) -> float | None:
    rate = _as_float(per_1k)
    return round(rate * 1000, 4) if rate is not None and rate >= 0 else None


async def fetch_model_providers(upstream: str) -> list[dict] | None:
    """Providers NanoGPT can pin for one model, priced with its selection
    markup. Empty when the model has no provider selection; None when
    NanoGPT could not be reached and nothing is cached."""
    now = time.time()
    hit = _providers.get(upstream)
    if hit and now - hit[0] < PROVIDERS_TTL:
        return hit[1]
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.get(f"{_api()}/models/{quote(upstream, safe='')}/providers")
            r.raise_for_status()
            data = r.json()
    except (httpx.HTTPError, ValueError):
        return hit[1] if hit else None
    rows = []
    if isinstance(data, dict) and data.get("supportsProviderSelection") is not False:
        for p in data.get("providers") or []:
            if not isinstance(p, dict) or not isinstance(p.get("provider"), str) or not p["provider"]:
                continue
            price = p.get("pricing") if isinstance(p.get("pricing"), dict) else {}
            uptime = p.get("uptime") if isinstance(p.get("uptime"), dict) else {}
            pct = _as_float(uptime.get("uptimePct"))
            privacy = str(p.get("privacyPolicyUrl") or "")
            rows.append(
                {
                    "slug": p["provider"][:64],
                    "name": str(p.get("displayName") or p["provider"])[:80],
                    "input": _per_million(price.get("inputPer1kTokens")),
                    "output": _per_million(price.get("outputPer1kTokens")),
                    "cache_read": _per_million(price.get("cacheReadInputPer1kTokens")),
                    "context": _as_int(p.get("maxInputTokens")),
                    "uptime": round(pct, 1) if pct is not None else None,
                    "up": p.get("available") is not False,
                    "quantization": str(p.get("quantization") or "")[:16],
                    "privacy_url": privacy[:300] if privacy.startswith("https://") else "",
                    "variants": [],
                }
            )
    if len(_providers) >= 512 and upstream not in _providers:
        _providers.pop(min(_providers, key=lambda k: _providers[k][0]), None)
    _providers[upstream] = (now, rows)
    return rows


# --- Chat -------------------------------------------------------------------------

def _clean_message(message, owner: bool):
    if not isinstance(message, dict):
        return message
    out = {k: v for k, v in message.items() if not k.startswith("_fuse") and k != "claude_thinking"}
    if not owner and isinstance(out.get("content"), list):
        out["content"] = [
            {k: v for k, v in part.items() if k != "cache_control"} if isinstance(part, dict) else part
            for part in out["content"]
        ]
    return out


def chat_payload(body: dict, upstream: str, rec: dict, *, owner: bool, prefs: dict | None = None) -> dict:
    payload = {
        k: v for k, v in drop_cache_routing_fields(body).items()
        if not k.startswith("_fuse") and k not in ("session_id", "models")
    }
    if isinstance(payload.get("messages"), list):
        payload["messages"] = [_clean_message(m, owner) for m in payload["messages"]]
    payload["model"] = upstream
    effort = payload.pop("reasoning_effort", None)
    reasoning = payload.pop("reasoning", None)
    thinking = payload.pop("thinking", None)
    if effort is None and isinstance(reasoning, dict):
        effort = reasoning.get("effort")
    if effort is None and isinstance(thinking, dict) and thinking.get("type") == "disabled":
        effort = "none"
    mapped = map_effort(effort, rec.get("reasoning_efforts") or ())
    if mapped:
        payload["reasoning_effort"] = mapped
    # A provider whose output limit is below max_tokens is skipped, not
    # clamped, so an oversized budget can leave no provider at all.
    cap = rec.get("max_output_tokens")
    for field in ("max_tokens", "max_completion_tokens"):
        if cap and isinstance(payload.get(field), int) and payload[field] > cap:
            payload[field] = cap
    if not owner:
        return {k: v for k, v in payload.items() if k in _BORROWED_FIELDS}
    rest = {k: v for k, v in reasoning.items() if k != "effort"} if isinstance(reasoning, dict) else {}
    if rest:
        payload["reasoning"] = rest
    if (
        upstream.startswith("anthropic/")
        and not rec.get("subscription")
        and not any(k in payload for k in ("prompt_caching", "promptCaching", "cache_control"))
    ):
        # Claude caches only with explicit markers. Explicit caching is
        # pay-as-you-go, which paid Claude models are anyway.
        payload["prompt_caching"] = {"enabled": True, "ttl": "1h"}
    pins = ((prefs or {}).get("providers") or {}).get(upstream)
    if pins and "provider" not in payload:
        payload["provider"] = {"only": list(pins), "allow_fallbacks": True}
    return payload


async def png_gifs(messages):
    """NanoGPT takes PNG, JPEG and WebP only: re-encode GIF data URLs (first frame)."""
    if not isinstance(messages, list):
        return messages
    out = []
    for msg in messages:
        content = msg.get("content") if isinstance(msg, dict) else None
        if not isinstance(content, list):
            out.append(msg)
            continue
        parts = []
        for part in content:
            image = part.get("image_url") if isinstance(part, dict) and part.get("type") == "image_url" else None
            url = image.get("url") if isinstance(image, dict) else None
            if isinstance(url, str) and url.startswith("data:image/gif;base64,"):
                try:
                    decoded = await file_images.decode_image(base64.b64decode(url.split(",", 1)[1], validate=True))
                    part = {**part, "image_url": {**image, "url": f"data:{decoded['mime']};base64,{decoded['data']}"}}
                except (ValueError, binascii.Error, KeyError, OSError):
                    pass
            parts.append(part)
        out.append({**msg, "content": parts})
    return out


def _retry_after(headers: dict) -> float:
    """A per-key daily cap answers 429 with Retry-After up to midnight UTC."""
    seconds = _as_float((headers or {}).get("Retry-After"))
    return max(5.0, min(seconds, 86400.0)) if seconds is not None else 60.0


class NanoGPTSource:
    type_id = "nanogpt"

    def __init__(self, row, cfg: dict, creds):
        self.source_id = row["id"]
        self.name = row["name"]
        self.cfg = cfg
        self.creds = list(creds)

    async def models(self) -> list[str]:
        catalog = cached_catalog() or await fetch_catalog()
        if not catalog["by_id"]:
            raise RuntimeError("NanoGPT catalog unavailable")
        return [public_id(mid) for mid in catalog["by_id"]]

    async def chat(self, body: dict, *, session_id: str | None = None, user_id: int | None = None, affinity_id: str | None = None) -> ChatResult:
        upstream = upstream_id(str(body.get("model") or ""))
        catalog = cached_catalog() or await fetch_catalog()
        rec = catalog["by_id"].get(upstream)
        if rec is None:
            return ChatResult(status=404, body={"error": {"message": "That model is not in NanoGPT's catalog."}}, model=upstream)
        await refresh_pool_usage()
        # filter_credentials already drops other users' keys that can spend
        # their owner's balance; paid models also skip every shared key.
        rows = pool_policy.filter_credentials(
            db.fetchall("SELECT * FROM credentials WHERE source_id=? AND disabled=0", (self.source_id,)),
            "nanogpt",
            user_id,
        )
        uid = int(user_id) if user_id is not None else None

        def usable(cred, extra) -> bool:
            if not credential_is_authed(cred, extra):
                return False
            return pool_policy.donated_by(cred) == uid or (rec["subscription"] and shareable(extra))

        body = {**body, "messages": await png_gifs(body.get("messages"))}
        prefs = model_prefs(uid)
        tried: set[int] = set()
        attempts: list[dict] = []
        last = ChatResult(
            status=503,
            body={"error": {"message": "No NanoGPT account can serve this model for you."}},
            model=upstream,
        )
        prefer_id = lookup("nanogpt", session_id)
        hint_id = lookup("nanogpt", affinity_id) if prefer_id is None and affinity_id else None
        primary_id = pool_policy.primary_account(uid, "nanogpt")
        failover = Failover()
        now = datetime.now(timezone.utc)
        while True:
            picked = pick_account(
                rows,
                kind="nanogpt",
                exclude=tried,
                is_held=lambda c: is_held(c, now),
                is_authed=usable,
                prefer_id=prefer_id,
                owner_id=uid,
                hint_id=hint_id,
                primary_id=primary_id,
            )
            if not picked:
                if attempts and last.credential_id == attempts[-1].get("credential_id"):
                    attempts.pop()
                last.attempts = attempts
                return last
            cred, _extra = picked
            cid = int(cred["id"])
            tried.add(cid)
            note_pool_invoke(cid)
            owner = pool_policy.donated_by(cred) == uid
            payload = chat_payload(body, upstream, rec, owner=owner, prefs=prefs)
            request_ids: list[str] = []

            async def capture(response, request_ids=request_ids):
                rid = response.headers.get("x-request-id") or ""
                if rid and all(32 <= ord(ch) < 127 for ch in rid):
                    request_ids.append(rid[:128])

            client = httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=15.0), event_hooks={"response": [capture]})
            stream_reserved = False
            try:
                reserve(cid)
                status, obj, stream, usage, headers = await openai_chat(
                    client, _api() + "/v1/chat/completions", _headers(accounts.cred_secret(cred)), payload, close_client=True
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
                    last.body = {"error": {"message": "NanoGPT rejected this account's key. Its owner needs to sign in again."}}
                    retry = True
                elif status in (402, 403) and not owner:
                    # A borrowed key that cannot spend balance refuses once its
                    # subscription is spent; the next account may still serve.
                    note_failure(cid, cooldown_seconds=300)
                    retry = True
                elif status == 429:
                    note_failure(cid, cooldown_seconds=_retry_after(headers))
                    retry = True
                elif status >= 500:
                    retry = failover.server_error(cid, status, 15)
                if retry:
                    attempts.append({"credential_id": cid, "status": status, "usage": usage or {}})
                    last.attempts = attempts
                    continue
                if status == 200:
                    failover.served()
                    bind("nanogpt", session_id, cid)
                last.attempts = attempts
                return last
            finally:
                if not stream_reserved:
                    release(cid)

    def _stream_result(self, stream, collected: dict, cid: int, session_id: str | None, model: str, attempts: list[dict]) -> ChatResult:
        async def run():
            try:
                async for chunk in stream:
                    yield chunk
            finally:
                close = getattr(stream, "aclose", None)
                if close is not None:
                    await close()
                status = (collected or {}).get("stream_status")
                if status == "complete":
                    bind("nanogpt", session_id, cid)
                elif status == "failed":
                    note_failure(cid, cooldown_seconds=5, affinity_safe=True)

        result = ChatResult(status=200, stream=UsageStream(run(), collected, on_close=lambda: release(cid)), credential_id=cid, model=model)
        result.attempts = attempts
        return result
