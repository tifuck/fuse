from __future__ import annotations

import asyncio
import json
import math
import platform
import socket
import time
import uuid
from datetime import datetime, timedelta, timezone

import httpx

from .. import accounts, db, provider_errors
from ..async_work import durable_call
from ..config import settings
from ..crypto import encrypt
from ..proxy import UsageStream, openai_chat
from ..security import safe_verify_url
from .base import ChatResult
from .grok import apply_usage_guard, is_held
from .pool_pick import Failover, auth_failure_confirmed, explain_empty, note_failure, pick_account, release, reserve
from . import pool_policy
from .pool_session import bind, drop_cache_routing_fields, lookup
from .token_store import (
    dead_grant, expires_at, log_refresh_failures, oauth_reason, persist_rotated, revoked_grant, shield_refresh,
)

USAGE_TTL = 300
HOLD_HOURS = 24
_models_cache: tuple[float, list[str]] | None = None
_refresh_locks: dict[int, asyncio.Lock] = {}

KIMI_MODELS = [
    "k3",
    "k3-256k",
    "kimi-for-coding",
    "kimi-for-coding-highspeed",
]

KIMI_MODEL_META = {
    "k3": {"name": "Kimi K3", "window": 262_144, "efforts": ("low", "high", "max"), "default_effort": "high"},
    "k3-256k": {"name": "Kimi K3 256K", "window": 262_144, "efforts": ("low", "high", "max"), "default_effort": "high"},
    "kimi-for-coding": {"name": "K2.8 Preview", "window": 1_048_576, "efforts": ("low", "high", "max"), "default_effort": "max"},
    "kimi-for-coding-highspeed": {"name": "K2.7 Code HighSpeed", "window": 262_144, "efforts": (), "default_effort": ""},
}
_catalog_meta: dict[str, dict] = {}

_REGIONS = ("global", "cn")
_EFFORT_MAP = {
    "ultra": "max",
    "max": "max",
    "xhigh": "max",
    "high": "high",
    "medium": "high",
    "low": "low",
    "minimum": "low",
    "light": "low",
    "min": "low",
}


def _refresh_lock(cred_id: int) -> asyncio.Lock:
    lock = _refresh_locks.get(cred_id)
    if lock is None:
        # Never evicted: an unlocked lock may still have a waiter about to
        # acquire it, and a second lock would let two refreshes spend the
        # same rotating refresh token. One per credential is small.
        lock = asyncio.Lock()
        _refresh_locks[cred_id] = lock
    return lock


def disable_credential(cred_id: int, *, extra: str | None = None) -> None:
    accounts.disable_credential(cred_id, extra=extra)


def ensure_kimi_source() -> int:
    row = db.fetchone("SELECT id FROM sources WHERE type='kimi'")
    if row:
        return row["id"]
    with db.tx() as conn:
        conn.execute(
            "INSERT INTO sources (type, name, enabled, config, created_at) VALUES (?,?,?,?,?)",
            ("kimi", "Kimi", 1, "{}", db.now()),
        )
        return db.last_id(conn)


def is_kimi_model(model: str) -> bool:
    mid = (model or "").strip()
    if not mid:
        return False
    if mid in KIMI_MODELS or mid in _catalog_meta or mid in KIMI_MODEL_META:
        return True
    return mid.startswith("kimi-for-coding") or mid == "k3" or mid.startswith("k3-")


def _meta_for(model: str) -> dict:
    mid = (model or "").strip()
    return _catalog_meta.get(mid) or KIMI_MODEL_META.get(mid) or {}


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
    if not model:
        return ("low", "high", "max")
    efforts = _meta_for(model).get("efforts")
    if efforts is None:
        return ("low", "high", "max")
    return tuple(efforts)


def default_effort(model: str | None = None) -> str:
    if not model:
        return "high"
    value = str(_meta_for(model).get("default_effort") or "")
    efforts = efforts_for(model)
    if value in efforts:
        return value
    return efforts[0] if efforts else ""


def parse_catalog_payload(data: dict) -> list[dict]:
    items = data.get("data") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return []
    out: list[dict] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        mid = str(item.get("id") or "").strip()
        if not mid or mid in seen:
            continue
        seen.add(mid)
        efforts_raw = item.get("think_efforts") if isinstance(item.get("think_efforts"), dict) else {}
        valid = efforts_raw.get("valid_efforts") if isinstance(efforts_raw, dict) else None
        efforts: tuple[str, ...] = ()
        if item.get("supports_thinking_type") and isinstance(valid, list):
            efforts = tuple(str(v).strip().lower() for v in valid if isinstance(v, str) and str(v).strip())
        elif item.get("supports_reasoning") and mid != "kimi-for-coding-highspeed":
            efforts = ("low", "high", "max")
        default = ""
        if isinstance(efforts_raw, dict):
            default = str(efforts_raw.get("default_effort") or "").strip().lower()
        if default not in efforts:
            default = efforts[0] if efforts else ""
        try:
            window = int(item.get("context_length") or 0)
        except (TypeError, ValueError):
            window = 0
        seeded = KIMI_MODEL_META.get(mid) or {}
        name = str(item.get("display_name") or seeded.get("name") or mid).strip()
        out.append(
            {
                "id": mid,
                "name": name,
                "window": window or int(seeded.get("window") or 0),
                "efforts": efforts if efforts or mid == "kimi-for-coding-highspeed" else tuple(seeded.get("efforts") or ()),
                "default_effort": default or str(seeded.get("default_effort") or ""),
                "image": bool(item["supports_image_in"]) if "supports_image_in" in item else None,
            }
        )
    return out


def _row_get(row, key, default=None):
    try:
        if isinstance(row, dict):
            return row.get(key, default)
        keys = row.keys()
        if key in keys:
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


def _endpoints(region: str) -> tuple[str, str]:
    if region == "cn":
        return settings.kimi_cn_oauth_host.rstrip("/"), settings.kimi_cn_coding_base.rstrip("/")
    return settings.kimi_oauth_host.rstrip("/"), settings.kimi_coding_base.rstrip("/")


def device_verify_url(pending: dict | None) -> str:
    """HTTPS Kimi device page. Prefer the complete URI; fall back by region."""
    pending = pending if isinstance(pending, dict) else {}
    for key in ("verification_uri_complete", "verification_uri"):
        url = safe_verify_url(str(pending.get(key) or ""))
        if url:
            return url
    code = str(pending.get("user_code") or "").strip()
    region = str(pending.get("_region") or "global")
    host = "https://www.kimi.com/code/authorize_device" if region == "cn" else "https://www.kimi.ai/code/authorize_device"
    if not code:
        return host
    from urllib.parse import quote

    return f"{host}?user_code={quote(code)}"


def _ascii_header(value: str, fallback: str = "unknown") -> str:
    cleaned = "".join(ch for ch in str(value or "") if 32 <= ord(ch) <= 126).strip()
    return cleaned or fallback


def _device_headers(device_id: str) -> dict:
    os_name = platform.system() or "Linux"
    os_ver = platform.release() or ""
    arch = platform.machine() or ""
    model = f"{os_name} {os_ver} {arch}".strip()
    return {
        "User-Agent": _ascii_header(settings.kimi_user_agent, "fuse/1.0"),
        "X-Msh-Platform": _ascii_header(settings.kimi_platform, "fuse"),
        "X-Msh-Version": _ascii_header(settings.kimi_client_version, "1.0"),
        "X-Msh-Device-Name": _ascii_header(socket.gethostname(), "fuse"),
        "X-Msh-Device-Model": _ascii_header(model, "Linux"),
        "X-Msh-Os-Version": _ascii_header(os_ver, "unknown"),
        "X-Msh-Device-Id": _ascii_header(device_id, "fuse"),
    }


def _new_device_id() -> str:
    return str(uuid.uuid4())


def _device_id_for(extra: dict | None) -> str:
    extra = extra if isinstance(extra, dict) else {}
    existing = str(extra.get("device_id") or "").strip()
    if existing:
        return existing
    return _new_device_id()


def _oauth_headers(extra: dict | None = None) -> dict:
    return _device_headers(_device_id_for(extra))


def _chat_headers(token: str, extra: dict | None = None) -> dict:
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    headers.update(_oauth_headers(extra))
    return headers


def _usage_headers(token: str, extra: dict | None = None) -> dict:
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }
    headers.update(_oauth_headers(extra))
    return headers


def _parse_ts(ts: str) -> datetime | None:
    if not ts:
        return None
    raw = str(ts).replace("Z", "")
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f"):
        try:
            return datetime.strptime(raw.split("+")[0], fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _usage_fresh(billing: dict) -> bool:
    checked = billing.get("checked_at") or billing.get("fetched_at")
    when = _parse_ts(str(checked or ""))
    if not when:
        return False
    return datetime.now(timezone.utc) - when < timedelta(seconds=USAGE_TTL)


def _ratio_percent(raw) -> float | None:
    if not isinstance(raw, dict):
        return None
    ratio = _as_float(raw.get("used_ratio") if "used_ratio" in raw else raw.get("usedRatio"))
    if ratio is None:
        return None
    if 0.0 <= ratio <= 1.0:
        return max(0.0, min(100.0, ratio * 100.0))
    return max(0.0, min(100.0, ratio))


def _quota_reset(raw) -> str:
    if not isinstance(raw, dict):
        return ""
    value = raw.get("reset_time") or raw.get("resetAt") or raw.get("reset_at") or ""
    return str(value)[:64] if value else ""


def parse_usage_payload(data: dict) -> dict:
    root = data.get("data") if isinstance(data.get("data"), dict) else data
    if not isinstance(root, dict):
        root = {}
    usages = root.get("usages") if isinstance(root.get("usages"), dict) else root
    if not isinstance(usages, dict):
        usages = {}
    five = usages.get("limit_5h") or usages.get("limit5h")
    week = usages.get("limit_7d") or usages.get("limit7d")
    month = usages.get("limit_month_total") or usages.get("monthTotal") or usages.get("limit_month_code")
    windows: list[dict] = []
    pcts: list[float] = []
    for kind, raw, seconds in (
        ("5h", five, 5 * 3600),
        ("7d", week, 7 * 86400),
        ("month", month, 30 * 86400),
    ):
        pct = _ratio_percent(raw)
        if pct is None:
            continue
        pcts.append(pct)
        windows.append(
            {
                "kind": kind,
                "seconds": seconds,
                "percent": pct,
                "reset_at": _quota_reset(raw),
            }
        )
    plan = str(root.get("user_level_name") or root.get("plan") or root.get("plan_type") or "")[:64]
    return {
        "windows": windows,
        "credit_usage_percent": max(pcts) if pcts else None,
        "five_hour_percent": _ratio_percent(five),
        "weekly_percent": _ratio_percent(week),
        "monthly_percent": _ratio_percent(month),
        "plan_type": plan,
        "period_end": next((w["reset_at"] for w in windows if w.get("reset_at")), ""),
        "period_type": "HOUR" if windows and windows[0]["kind"] == "5h" else "MONTH",
    }


def parse_userinfo_payload(data: dict) -> dict:
    root = data.get("data") if isinstance(data.get("data"), dict) else data
    if not isinstance(root, dict):
        return {}
    user_id = root.get("user_id") or root.get("userId") or root.get("global_id") or ""
    email = str(root.get("email") or "").strip()[:254]
    name = str(root.get("nickname") or root.get("username") or root.get("name") or "").strip()[:80]
    plan = str(root.get("user_level_name") or root.get("plan") or "").strip()[:64]
    status = str(root.get("status") or "").strip()[:64]
    out = {}
    if user_id:
        out["account_id"] = str(user_id)[:128]
    if email and email.count("@") == 1 and " " not in email:
        out["email"] = email
    if name:
        out["name"] = name
    if plan:
        out["plan_type"] = plan
    if status:
        out["status"] = status
    return out


def billing_has_subscription(billing: dict | None) -> bool:
    if not isinstance(billing, dict):
        return False
    if billing.get("last_error") and not billing.get("fetched_at"):
        return False
    if str(billing.get("plan_type") or "").strip():
        return True
    windows = billing.get("windows") if isinstance(billing.get("windows"), list) else []
    if windows:
        return True
    if billing.get("five_hour_percent") is not None or billing.get("weekly_percent") is not None:
        return True
    if billing.get("credit_usage_percent") is not None:
        return True
    return bool(billing.get("fetched_at"))


def credential_is_authed(cred, extra: dict | None = None) -> bool:
    if _row_get(cred, "disabled"):
        return False
    extra = extra if extra is not None else accounts.cred_extra(cred)
    token = extra.get("access_token") or extra.get("api_key") or accounts.cred_secret(cred)
    if not token:
        return False
    billing = extra.get("billing") if isinstance(extra.get("billing"), dict) else {}
    err = str(billing.get("last_error") or "").lower()
    if "401" in err or "403" in err:
        if extra.get("refresh_token") and "401" in err:
            return True
        if "401" in err:
            return False
    return True


def kimi_own_access(user_id: int) -> dict:
    sid = ensure_kimi_source()
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
        billing = extra.get("billing") if isinstance(extra.get("billing"), dict) else {}
        if billing_has_subscription(billing):
            return {"ok": True, "reason": "ok"}
        if extra.get("access_token") or extra.get("api_key"):
            return {"ok": True, "reason": "ok"}
    return {"ok": False, "reason": "unauthed" if not saw_authed else "no_sub"}


def _kimi_live(row) -> bool:
    return not is_held(row) and credential_is_authed(row, accounts.cred_extra(row))


def kimi_access_state(user_id: int) -> dict:
    return pool_policy.with_open_access(
        user_id, "kimi", kimi_own_access(user_id), _kimi_live
    )


def user_has_kimi(user_id: int) -> bool:
    return kimi_access_state(user_id)["ok"]


def note_pool_invoke(cred_id: int) -> None:
    db.execute("UPDATE credentials SET pool_invokes = COALESCE(pool_invokes, 0) + 1 WHERE id=?", (cred_id,))


def account_email(extra: dict | None) -> str:
    extra = extra if isinstance(extra, dict) else {}
    text = str(extra.get("email") or "").strip()
    if text and len(text) <= 254 and " " not in text and text.count("@") == 1:
        return text
    return ""


def _oauth_revoked(status: int, data) -> bool:
    if status not in (400, 401, 403) or not isinstance(data, dict):
        return False
    err = data.get("error")
    code = str(err.get("code") or err.get("type") or "") if isinstance(err, dict) else str(err or "")
    message = str((err.get("message") if isinstance(err, dict) else "") or data.get("error_description") or "")
    text = f"{code} {message}".lower()
    # Only a dead grant disables for good: a bare "unauthorized" (e.g.
    # unauthorized_client, a transient auth fault) only cools the account.
    return "invalid_grant" in text or "revoked" in text


def _capability_denial(data) -> bool:
    """A 401 body that denies a model/context beyond the membership tier.

    Kimi Code documents 401 for entitlement shortfalls (K3 access,
    HighSpeed, 1M context), which is not an authentication failure and must
    never strike the credential. Structured error codes are trusted when
    present; otherwise only distinctive entitlement wording rescues the
    credential, so plain "invalid key" stays an auth failure.
    """
    if not isinstance(data, dict):
        return False
    err = data.get("error")
    # Live Code wording: "Your current subscription does not have access to
    # k3", "Your current plan supports only k3 up to 256K context".
    words = ("capability", "entitlement", "membership", "not included", "does not include", "plan does not",
             "does not have access to", "supports only", "higher-tier")
    if isinstance(err, dict):
        code = str(err.get("code") or err.get("type") or "").strip().lower()
        if code:
            return any(word in code for word in words)
        text = str(err.get("message") or "").lower()
    else:
        text = str(err or "").lower()
    return any(word in text for word in words)


def _map_effort(value) -> str | None:
    if not isinstance(value, str):
        return None
    key = value.strip().lower()
    if key in ("none", "off", "disabled"):
        return "none"
    return _EFFORT_MAP.get(key)


def _chat_payload(body: dict) -> dict:
    payload = drop_cache_routing_fields(body)
    reasoning = payload.get("reasoning")
    effort = payload.get("reasoning_effort")
    if effort is None and isinstance(reasoning, dict):
        effort = reasoning.get("effort")
    mapped = _map_effort(effort) if isinstance(effort, str) else None
    payload.pop("reasoning", None)
    if mapped == "none":
        payload.pop("reasoning_effort", None)
        payload["thinking"] = {"type": "disabled"}
    elif mapped:
        payload["reasoning_effort"] = mapped
    return payload


async def start_device(region: str = "global") -> dict:
    if region not in _REGIONS:
        region = "global"
    oauth_host, _ = _endpoints(region)
    extra = {"device_id": _new_device_id(), "region": region}
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.post(
            oauth_host + "/api/oauth/device_authorization",
            data={"client_id": settings.kimi_client_id},
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
                **_oauth_headers(extra),
            },
        )
        r.raise_for_status()
        data = r.json()
    if not isinstance(data, dict):
        raise ValueError("invalid device response")
    data["_region"] = region
    data["_device_id"] = extra["device_id"]
    return data


async def poll_device(device_code: str, *, region: str = "global", device_id: str = "") -> dict:
    if region not in _REGIONS:
        region = "global"
    oauth_host, _ = _endpoints(region)
    extra = {"device_id": device_id or _new_device_id(), "region": region}
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.post(
            oauth_host + "/api/oauth/token",
            data={
                "client_id": settings.kimi_client_id,
                "device_code": device_code,
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            },
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
                **_oauth_headers(extra),
            },
        )
        try:
            data = r.json()
        except json.JSONDecodeError:
            return {"error": f"http {r.status_code}"}
    if not isinstance(data, dict):
        return {"error": f"http {r.status_code}"}
    if r.status_code == 200 and data.get("access_token"):
        data["_region"] = region
        data["_device_id"] = extra["device_id"]
        return data
    if "error" not in data:
        data["error"] = f"http {r.status_code}"
    return data


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


def store_tokens(source_id: int, tokens: dict, donated_by: int | None, label: str = "", *, region: str = "global", device_id: str = "") -> int:
    extra = {
        "auth_kind": "oauth",
        "access_token": tokens["access_token"],
        "refresh_token": tokens.get("refresh_token", ""),
        "expires_at": expires_at(tokens.get("expires_in"), 21600),
        "scope": tokens.get("scope", ""),
        "region": region if region in _REGIONS else "global",
        "device_id": device_id or _new_device_id(),
        "account_id": str(tokens.get("account_id") or "")[:128],
    }
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
                label=label or row["label"] or "Kimi account",
                reenable=True,
            ):
                return int(row["id"])
    return accounts.add_credential(
        source_id,
        label or "Kimi account",
        tokens["access_token"],
        extra=extra,
        donated_by=donated_by,
    )


def store_key(source_id: int, key: str, donated_by: int | None, *, label: str = "", region: str = "global") -> int:
    extra = {
        "auth_kind": "key",
        "api_key": key,
        "region": region if region in _REGIONS else "global",
        "device_id": _new_device_id(),
    }
    name = (label or "").strip()[:80] or ("Kimi (CN)" if region == "cn" else "Kimi account")
    return accounts.add_credential(source_id, name, key, extra=extra, donated_by=donated_by)


def _find_key(source_id: int, key: str):
    for row in db.fetchall("SELECT * FROM credentials WHERE source_id=?", (source_id,)):
        try:
            extra = accounts.cred_extra(row)
            if extra.get("api_key") == key or accounts.cred_secret(row) == key:
                return row
        except Exception:
            continue
    return None


def _bearer(cred, extra: dict) -> str:
    return extra.get("access_token") or extra.get("api_key") or accounts.cred_secret(cred)


async def fetch_usage_for_token(token: str, region: str, extra: dict | None = None) -> tuple[dict | None, str]:
    _, base = _endpoints(region)
    async with httpx.AsyncClient(timeout=15.0, follow_redirects=False) as client:
        try:
            r = await client.get(base + "/usages", headers=_usage_headers(token, extra))
        except Exception:
            return None, "fetch failed"
        if r.status_code in (401, 403):
            return None, f"http {r.status_code}"
        if r.status_code != 200:
            return None, f"http {r.status_code}"
        try:
            data = r.json()
        except json.JSONDecodeError:
            return None, "invalid JSON"
        if not isinstance(data, dict):
            return None, "invalid JSON"
        return parse_usage_payload(data), ""


async def fetch_userinfo(token: str, region: str, extra: dict | None = None) -> dict:
    _, base = _endpoints(region)
    async with httpx.AsyncClient(timeout=15.0, follow_redirects=False) as client:
        try:
            r = await client.get(base + "/me", headers=_usage_headers(token, extra))
        except Exception:
            return {}
        if r.status_code != 200:
            return {}
        try:
            data = r.json()
        except json.JSONDecodeError:
            return {}
        if not isinstance(data, dict):
            return {}
        return parse_userinfo_payload(data)


async def probe_key(key: str) -> tuple[dict | None, str, str]:
    last_err = "Could not reach Kimi Code."
    extra = {"device_id": _new_device_id()}
    for region in _REGIONS:
        parsed, err = await fetch_usage_for_token(key, region, extra)
        if parsed is not None:
            return parsed, region, ""
        last_err = "Invalid API key." if err in ("http 401", "http 403") else err
        if err in ("http 401", "http 403"):
            continue
    return None, "", last_err


async def add_plan_key(source_id: int, raw: str, donated_by: int, *, label: str = "") -> tuple[int | None, str]:
    key = (raw or "").strip()
    if not key or len(key) < 8 or len(key) > 4096:
        return None, "Enter a Kimi Code API key from the membership console."
    if any(ord(ch) < 32 for ch in key):
        return None, "Enter a Kimi Code API key from the membership console."
    existing = _find_key(source_id, key)
    if existing and not existing["disabled"]:
        return None, "That key is already in the pool."
    parsed, region, err = await probe_key(key)
    if err or parsed is None:
        return None, err or "Could not validate this key."
    # Re-check after the probe's await: a concurrent add of the same key may
    # have committed meanwhile. Nothing below awaits before the write.
    existing = _find_key(source_id, key)
    if existing and not existing["disabled"]:
        return None, "That key is already in the pool."
    if existing:
        name = (label or "").strip()[:80] or existing["label"] or "Kimi account"
        extra = accounts.cred_extra(existing)
        extra["auth_kind"] = "key"
        extra["api_key"] = key
        extra["region"] = region if region in _REGIONS else "global"
        extra.setdefault("device_id", _new_device_id())
        db.execute(
            """UPDATE credentials SET disabled=0, donated_by=?, label=?, secret=?, extra=? WHERE id=?""",
            (donated_by, name, encrypt(key), encrypt(json.dumps(extra)), existing["id"]),
        )
        cred_id = int(existing["id"])
    else:
        cred_id = store_key(source_id, key, donated_by, label=label, region=region)
    row = db.fetchone("SELECT * FROM credentials WHERE id=?", (cred_id,))
    if row:
        await refresh_quota(row, force=True, parsed=parsed, region=region)
    return cred_id, ""


def _store_billing(cred_id: int, parsed: dict, *, error: str = "", region: str | None = None, profile: dict | None = None) -> dict:
    row = db.fetchone("SELECT * FROM credentials WHERE id=?", (cred_id,))
    extra = accounts.cred_extra(row) if row else {}
    billing = extra.get("billing") if isinstance(extra.get("billing"), dict) else {}
    now = db.now()
    billing["checked_at"] = now
    if region in _REGIONS:
        extra["region"] = region
    if isinstance(profile, dict):
        if profile.get("account_id"):
            extra["account_id"] = profile["account_id"]
        if profile.get("email"):
            extra["email"] = profile["email"]
        if profile.get("name"):
            extra["name"] = profile["name"]
        if profile.get("plan_type"):
            extra["plan_type"] = profile["plan_type"]
            parsed = dict(parsed)
            parsed.setdefault("plan_type", profile["plan_type"])
    if error:
        billing["last_error"] = error[:200]
        extra["billing"] = billing
        if row:
            accounts.update_extra(cred_id, extra)
        return billing
    clean = {k: v for k, v in parsed.items() if not str(k).startswith("_")}
    if extra.get("plan_type") and not clean.get("plan_type"):
        clean["plan_type"] = extra["plan_type"]
    billing.update(clean)
    billing["fetched_at"] = now
    billing["last_error"] = ""
    extra["billing"] = billing
    source = db.fetchone("SELECT config FROM sources WHERE id=?", (row["source_id"],)) if row else None
    try:
        cfg = json.loads((source["config"] if source else None) or "{}")
    except json.JSONDecodeError:
        cfg = {}
    guard_enabled = cfg.get("offpool_usage_guard", True) is not False
    extra, held_until, last_pct = apply_usage_guard(row, extra, clean, enabled=guard_enabled)
    extra["billing"] = billing
    accounts.update_extra(cred_id, extra, last_usage_percent=last_pct, usage_percent_at=now, pool_held_until=held_until)
    return billing


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
    if dead_grant(row["id"], rt):
        revoked_grant(row, extra, "kimi", disable_credential)
        return False
    region = extra.get("region") or "global"
    if region not in _REGIONS:
        region = "global"
    oauth_host, _ = _endpoints(region)
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.post(
                oauth_host + "/api/oauth/token",
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": rt,
                    "client_id": settings.kimi_client_id,
                },
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Accept": "application/json",
                    **_oauth_headers(extra),
                },
            )
    except httpx.HTTPError:
        note_failure(row["id"], cooldown_seconds=30)
        return False
    try:
        data = r.json()
    except json.JSONDecodeError:
        note_failure(row["id"], cooldown_seconds=30)
        return False
    if not isinstance(data, dict):
        note_failure(row["id"], cooldown_seconds=30)
        return False
    if r.status_code != 200 or "access_token" not in data:
        if _oauth_revoked(r.status_code, data):
            revoked_grant(row, extra, "kimi", disable_credential, oauth_reason(r.status_code, data))
        else:
            note_failure(row["id"], cooldown_seconds=60 if r.status_code == 429 else 30)
        return False
    extra["access_token"] = data["access_token"]
    extra["refresh_token"] = data.get("refresh_token", rt)
    extra["expires_at"] = expires_at(data.get("expires_in"), 21600)
    extra["auth_kind"] = "oauth"
    return await durable_call(
        persist_rotated,
        _cas_update_tokens,
        int(row["id"]),
        row["extra"],
        encrypt(data["access_token"]),
        extra,
        {
            "access_token": data["access_token"],
            "refresh_token": data.get("refresh_token", rt),
            "expires_at": extra["expires_at"],
            "auth_kind": "oauth",
        },
    )


async def refresh_one(cred) -> bool:
    async def run():
        async with _refresh_lock(int(cred["id"])):
            return await _refresh_one_unlocked(cred)

    return await shield_refresh(run())


async def refresh_quota(cred, *, force: bool = False, parsed: dict | None = None, region: str | None = None) -> dict | None:
    async with _refresh_lock(int(cred["id"])):
        row = db.fetchone("SELECT * FROM credentials WHERE id=?", (cred["id"],))
        if not row or row["disabled"]:
            return None
        extra = accounts.cred_extra(row)
        billing = extra.get("billing") if isinstance(extra.get("billing"), dict) else {}
        if not force and parsed is None and _usage_fresh(billing) and billing.get("windows") is not None:
            return billing
        token = _bearer(row, extra)
        if not token:
            return _store_billing(row["id"], {}, error="missing key")
        stored_region = region or extra.get("region") or "global"
        if stored_region not in _REGIONS:
            stored_region = "global"
        profile = None
        if parsed is None:
            if extra.get("refresh_token"):
                exp = extra.get("expires_at")
                due = True
                if exp:
                    when = _parse_ts(str(exp))
                    due = not when or when <= datetime.now(timezone.utc) + timedelta(minutes=5)
                if due:
                    if await _refresh_one_unlocked(row):
                        row = db.fetchone("SELECT * FROM credentials WHERE id=?", (cred["id"],))
                        extra = accounts.cred_extra(row)
                        token = _bearer(row, extra)
            parsed, err = await fetch_usage_for_token(token, stored_region, extra)
            if parsed is None and stored_region == "global":
                other, other_err = await fetch_usage_for_token(token, "cn", extra)
                if other is not None:
                    parsed, err, stored_region = other, "", "cn"
                else:
                    err = err or other_err
            elif parsed is None and stored_region == "cn":
                other, other_err = await fetch_usage_for_token(token, "global", extra)
                if other is not None:
                    parsed, err, stored_region = other, "", "global"
                else:
                    err = err or other_err
            if parsed is None:
                return _store_billing(row["id"], {}, error=err or "fetch failed", region=stored_region)
        try:
            profile = await fetch_userinfo(token, stored_region, extra)
        except Exception:
            profile = None
        return _store_billing(row["id"], parsed, region=stored_region, profile=profile)


async def refresh_pool_quota(*, force: bool = False) -> None:
    rows = db.fetchall(
        "SELECT c.* FROM credentials c JOIN sources s ON s.id=c.source_id WHERE s.type='kimi' AND c.disabled=0"
    )
    if not rows:
        return
    results = await asyncio.gather(*(refresh_quota(c, force=force) for c in rows), return_exceptions=True)
    log_refresh_failures("Kimi quota refresh", rows, results)


async def refresh_due() -> None:
    rows = db.fetchall(
        "SELECT c.* FROM credentials c JOIN sources s ON s.id=c.source_id WHERE s.type='kimi' AND c.disabled=0"
    )
    soon = datetime.now(timezone.utc) + timedelta(minutes=5)
    due = []
    for cred in rows:
        extra = accounts.cred_extra(cred)
        if not extra.get("refresh_token"):
            continue
        exp = extra.get("expires_at")
        if not exp:
            due.append(cred)
            continue
        when = _parse_ts(str(exp))
        if not when or when <= soon:
            due.append(cred)
    if due:
        results = await asyncio.gather(*(refresh_one(c) for c in due), return_exceptions=True)
        log_refresh_failures("Kimi OAuth refresh", due, results)


def _remember_catalog(rows: list[dict]) -> list[str]:
    global _models_cache, _catalog_meta
    now = time.time()
    names = [row["id"] for row in rows if row.get("id")]
    if not names and _models_cache and _models_cache[1]:
        return list(_models_cache[1])
    if not names:
        names = list(KIMI_MODELS)
        rows = [{"id": mid, **KIMI_MODEL_META[mid]} for mid in names if mid in KIMI_MODEL_META]
    else:
        prior = dict(_catalog_meta)
        for mid in list(_models_cache[1] if _models_cache and _models_cache[1] else []) + list(KIMI_MODELS):
            if mid not in names:
                names.append(mid)
                if mid in prior:
                    rows.append(prior[mid])
    _catalog_meta = {row["id"]: row for row in rows if row.get("id")}
    for mid, seeded in KIMI_MODEL_META.items():
        _catalog_meta.setdefault(mid, {"id": mid, **seeded})
    _models_cache = (now, names)
    return names


async def fetch_catalog(token: str, region: str, extra: dict | None = None) -> list[str]:
    now = time.time()
    if _models_cache and now - _models_cache[0] < 60:
        return list(_models_cache[1])
    rows: list[dict] = []
    _, base = _endpoints(region)
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.get(base + "/models", headers=_chat_headers(token, extra))
            if r.status_code == 200 and isinstance(r.json(), dict):
                rows = parse_catalog_payload(r.json())
    except (httpx.HTTPError, json.JSONDecodeError, ValueError):
        rows = []
    return _remember_catalog(rows)


class KimiSource:
    type_id = "kimi"

    def __init__(self, row, cfg: dict, creds):
        self.source_id = row["id"]
        self.name = row["name"]
        self.cfg = cfg
        self.creds = list(creds)

    def _pick(self, exclude: set[int] | None = None, prefer_id: int | None = None, owner_id: int | None = None, hint_id: int | None = None, primary_id: int | None = None, why: list | None = None):
        now = datetime.now(timezone.utc)
        return pick_account(
            self.creds,
            kind="kimi",
            exclude=exclude,
            is_held=lambda c: is_held(c, now),
            is_authed=credential_is_authed,
            prefer_id=prefer_id,
            owner_id=owner_id,
            hint_id=hint_id,
            primary_id=primary_id,
            why=why,
        )

    async def models(self) -> list[str]:
        picked = self._pick()
        if picked:
            cred, extra = picked
            token = _bearer(cred, extra)
            region = extra.get("region") or "global"
            names = await fetch_catalog(token, region, extra)
            if names:
                return names
        if _models_cache and _models_cache[1]:
            return list(_models_cache[1])
        return list(KIMI_MODELS)

    async def chat(self, body: dict, *, session_id: str | None = None, user_id: int | None = None, affinity_id: str | None = None) -> ChatResult:
        await refresh_due()
        await refresh_pool_quota()
        self.creds = pool_policy.filter_credentials(
            db.fetchall(
                "SELECT * FROM credentials WHERE source_id=? AND disabled=0",
                (self.source_id,),
            ),
            "kimi",
            user_id,
        )
        tried: set[int] = set()
        attempts: list[dict] = []
        last = ChatResult(status=503, body={"error": "No Kimi accounts in the pool"})
        model = body.get("model") or KIMI_MODELS[0]
        payload = _chat_payload(body)
        prefer_id = lookup("kimi", session_id)
        hint_id = lookup("kimi", affinity_id) if prefer_id is None and affinity_id else None
        primary_id = pool_policy.primary_account(user_id, "kimi")
        failover = Failover()
        why: list = []
        while True:
            picked = self._pick(exclude=tried, prefer_id=prefer_id, owner_id=user_id, hint_id=hint_id, primary_id=primary_id, why=why)
            if not picked:
                if not tried:
                    last = explain_empty("kimi", why)
                if attempts and last.credential_id == attempts[-1].get("credential_id"):
                    attempts.pop()
                last.attempts = attempts
                return last
            cred, extra = picked
            tried.add(int(cred["id"]))
            note_pool_invoke(cred["id"])
            token = _bearer(cred, extra)
            region = extra.get("region") or "global"
            if region not in _REGIONS:
                region = "global"
            _, base = _endpoints(region)
            client = httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=15.0))
            stream_reserved = False
            try:
                reserve(cred["id"])
                status, obj, stream, usage, response_headers = await openai_chat(
                    client,
                    base + "/chat/completions",
                    _chat_headers(token, extra),
                    payload,
                    close_client=True,
                    reasoning_separate=True,
                )
                if status == 401 and extra.get("refresh_token") and not _capability_denial(obj):
                    # A capability denial is about the membership tier, not the
                    # token: refreshing OAuth would be wasted work and a second
                    # doomed request.
                    ok = await refresh_one(cred)
                    if ok:
                        attempts.append({"credential_id": cred["id"], "status": status, "usage": usage or {}})
                        extra = accounts.cred_extra(db.fetchone("SELECT * FROM credentials WHERE id=?", (cred["id"],)))
                        token = _bearer(cred, extra)
                        client = httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=15.0))
                        status, obj, stream, usage, response_headers = await openai_chat(
                            client,
                            base + "/chat/completions",
                            _chat_headers(token, extra),
                            payload,
                            close_client=True,
                            reasoning_separate=True,
                        )
                if stream is not None:
                    stream_reserved = True
                    failover.served()
                    return self._stream_result(stream, usage, cred, session_id, model, attempts)
                last = ChatResult(status=status, body=obj, usage=usage, credential_id=cred["id"], model=model)
                if usage.get("failure_phase") in ("request_sent", "response_received"):
                    last.response_headers = response_headers
                    last.attempts = attempts
                    return last
                if status == 401:
                    if _capability_denial(obj):
                        # The coding endpoint answers a model/context beyond
                        # this membership's entitlements with 401. That is not
                        # an auth failure: fail over with a cooldown, never an
                        # auth strike that would disable a valid credential.
                        note_failure(cred["id"], cooldown_seconds=60)
                        last.response_headers = response_headers
                        attempts.append({"credential_id": cred["id"], "status": status, "usage": usage or {}})
                        last.attempts = attempts
                        continue
                    if auth_failure_confirmed(cred["id"]):
                        disable_credential(cred["id"], extra=cred.get("extra"))
                    last.status = 502
                    last.body = {"error": {"message": "Kimi rejected the configured account"}}
                    last.response_headers = response_headers
                    attempts.append({"credential_id": cred["id"], "status": status, "usage": usage or {}})
                    last.attempts = attempts
                    continue
                if status in (403, 429):
                    try:
                        await refresh_quota(cred, force=True)
                    except Exception:
                        pass
                    # 403 is both the 5-hour/weekly/monthly quota and the
                    # concurrency cap; only a spent quota earns a long hold.
                    info = provider_errors.classify("kimi", status, obj, response_headers)
                    note_failure(cred["id"], cooldown_seconds=provider_errors.cooldown(info, 60))
                    last.response_headers = response_headers
                    attempts.append({"credential_id": cred["id"], "status": status, "usage": usage or {}})
                    last.attempts = attempts
                    continue
                if status >= 500 and failover.server_error(cred["id"], status, 15):
                    last.response_headers = response_headers
                    attempts.append({"credential_id": cred["id"], "status": status, "usage": usage or {}})
                    last.attempts = attempts
                    continue
                if status == 200:
                    failover.served()
                    bind("kimi", session_id, cred["id"])
                last.response_headers = response_headers
                last.attempts = attempts
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
                    bind("kimi", session_id, cid)
                elif status == "failed":
                    note_failure(cid, cooldown_seconds=5, affinity_safe=True)

        wrapped = UsageStream(_bind_on_finish(), collected, on_close=lambda: release(cid))
        result = ChatResult(status=200, stream=wrapped, credential_id=cid, model=model)
        result.attempts = attempts
        return result
