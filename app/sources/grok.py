from __future__ import annotations

import asyncio
import base64
import json
import math
import time
from datetime import datetime, timedelta, timezone

import httpx

from .. import accounts, db
from ..config import settings
from ..crypto import encrypt
from ..proxy import UsageStream, openai_chat
from .base import ChatResult
from .pool_pick import Failover, note_failure, pick_account, release, reserve
from . import pool_policy
from .grok_version import current_version
from .pool_session import bind, drop_cache_routing_fields, lookup
from .token_store import expires_at, persist_rotated

CLIENT_ID = settings.grok_client_id
BILLING_TTL = 300
HOLD_HOURS = 24
OFFPOOL_PCT = 0.5
_models_cache: tuple[float, list[str]] | None = None
_refresh_locks: dict[int, asyncio.Lock] = {}

GROK_MODELS = [
    "grok-4.7",
    "grok-4.6",
    "grok-4.5",
]


def _refresh_lock(cred_id: int) -> asyncio.Lock:
    lock = _refresh_locks.get(cred_id)
    if lock is None:
        if len(_refresh_locks) > 256:
            for cid in [cid for cid, v in _refresh_locks.items() if not v.locked()]:
                _refresh_locks.pop(cid, None)
        lock = asyncio.Lock()
        _refresh_locks[cred_id] = lock
    return lock


def disable_credential(cred_id: int) -> None:
    db.execute("UPDATE credentials SET disabled=1 WHERE id=?", (cred_id,))


def _oauth_revoked(status: int, data) -> bool:
    if status not in (400, 401) or not isinstance(data, dict):
        return False
    err = data.get("error")
    code = str(err.get("code") or err.get("type") or "") if isinstance(err, dict) else str(err or "")
    message = str((err.get("message") if isinstance(err, dict) else "") or data.get("error_description") or "")
    text = f"{code} {message}".lower()
    return "invalid_grant" in text or "revoked" in text or "refresh token is invalid" in text


def account_from_tokens(tokens: dict) -> str | None:
    try:
        part = str(tokens.get("access_token") or "").split(".")[1]
        part += "=" * (-len(part) % 4)
        claims = json.loads(base64.urlsafe_b64decode(part))
    except Exception:
        return None
    if not isinstance(claims, dict):
        return None
    account_id = claims.get("principal_id") or claims.get("sub")
    return str(account_id) if account_id else None


def _clean_email(value) -> str:
    text = str(value or "").strip()
    if not text or len(text) > 254 or " " in text or text.count("@") != 1:
        return ""
    return text


def parse_userinfo_payload(data: dict) -> dict:
    if not isinstance(data, dict):
        return {}
    email = _clean_email(data.get("email"))
    name = str(data.get("name") or data.get("given_name") or "").strip()[:80]
    out = {}
    if email:
        out["email"] = email
    if name:
        out["name"] = name
    return out


def account_email(extra: dict | None) -> str:
    extra = extra if isinstance(extra, dict) else {}
    return _clean_email(extra.get("email"))


def apply_profile(extra: dict, profile: dict | None) -> dict:
    if not isinstance(profile, dict):
        return extra
    if profile.get("email"):
        extra["email"] = profile["email"]
    if profile.get("name"):
        extra["name"] = profile["name"]
    return extra


async def fetch_userinfo(token: str) -> dict:
    if not token:
        return {}
    async with httpx.AsyncClient(timeout=12.0, follow_redirects=False) as client:
        try:
            r = await client.get(
                "https://auth.x.ai/oauth2/userinfo",
                headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            )
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


def ensure_grok_source() -> int:
    row = db.fetchone("SELECT id FROM sources WHERE type='grok'")
    if row:
        return row["id"]
    with db.tx() as conn:
        conn.execute(
            "INSERT INTO sources (type, name, enabled, config, created_at) VALUES (?,?,?,?,?)",
            ("grok", "Grok", 1, "{}", db.now()),
        )
        return db.last_id(conn)


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


def is_held(cred, now: datetime | None = None) -> bool:
    until = _parse_ts(str(_row_get(cred, "pool_held_until") or ""))
    if not until:
        return False
    return until > (now or datetime.now(timezone.utc))


_GROK_SUB_PRODUCTS = {"GrokBuild", "GrokChat", "GrokAPI", "GrokVoice", "GrokImagine"}


def billing_has_subscription(billing: dict | None) -> bool:
    """True when x.ai billing shows a live SuperGrok (or similar) subscription."""
    if not isinstance(billing, dict) or not billing.get("fetched_at"):
        return False
    for p in billing.get("products") or []:
        if isinstance(p, dict) and str(p.get("product") or "") in _GROK_SUB_PRODUCTS:
            return True
    if billing.get("period_type"):
        return True
    if billing.get("credit_usage_percent") is not None:
        return True
    if billing.get("prepaid_cents") or billing.get("on_demand_cap_cents") or billing.get("monthly_limit_cents"):
        return True
    return False


def credential_is_authed(cred, extra: dict | None = None) -> bool:
    if _row_get(cred, "disabled"):
        return False
    extra = extra if extra is not None else accounts.cred_extra(cred)
    token = extra.get("access_token") or ""
    if not token:
        return False
    billing = extra.get("billing") if isinstance(extra.get("billing"), dict) else {}
    err = str(billing.get("last_error") or "").lower()
    if "401" in err or "403" in err:
        return False
    return True


def grok_own_access(user_id: int) -> dict:
    """Access from this user's own donated Grok accounts only."""
    sid = ensure_grok_source()
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
    if saw_authed:
        return {"ok": False, "reason": "no_sub"}
    return {"ok": False, "reason": "unauthed"}


def _grok_pool_ready() -> bool:
    sid = ensure_grok_source()
    rows = db.fetchall(
        "SELECT * FROM credentials WHERE source_id=? AND disabled=0",
        (sid,),
    )
    for row in rows:
        extra = accounts.cred_extra(row)
        if not credential_is_authed(row, extra):
            continue
        billing = extra.get("billing") if isinstance(extra.get("billing"), dict) else {}
        if billing_has_subscription(billing):
            return True
    return False


def grok_access_state(user_id: int) -> dict:
    """Whether this user may call Grok models, and why not if they may not.

    Own linked accounts still unlock Grok. When the admin has sharing and
    open access on, any registered user may use the donated pool without
    linking one. Held-out-of-pool is a routing concern and does not revoke
    the user's access.
    """
    return pool_policy.with_open_access(
        user_id, "grok", grok_own_access(user_id), _grok_pool_ready()
    )


def user_has_grok(user_id: int) -> bool:
    return grok_access_state(user_id)["ok"]


def note_pool_invoke(cred_id: int) -> None:
    db.execute("UPDATE credentials SET pool_invokes = COALESCE(pool_invokes, 0) + 1 WHERE id=?", (cred_id,))


def _auth_headers(token: str, model: str = "grok-4.6", conv_id: str | None = None) -> dict:
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "X-XAI-Token-Auth": "xai-grok-cli",
        "x-grok-client-version": current_version(),
        "x-grok-model-override": model or "grok-4.6",
    }
    if conv_id:
        headers["x-grok-conv-id"] = str(conv_id)[:256]
    return headers


def _cli_get_headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "X-XAI-Token-Auth": "xai-grok-cli",
        "x-grok-client-version": current_version(),
        "Accept": "application/json",
    }


def _money_val(obj) -> int | None:
    if obj is None:
        return None
    if isinstance(obj, bool):
        return None
    if isinstance(obj, dict) and "val" in obj:
        try:
            return int(obj["val"])
        except (TypeError, ValueError):
            return None
    if isinstance(obj, (int, float)):
        try:
            return int(obj) if math.isfinite(float(obj)) else None
        except (ValueError, OverflowError):
            return None
    return None


def _usage_percent(billing: dict | None) -> float | None:
    if not isinstance(billing, dict):
        return None
    values = [_as_float(billing.get("credit_usage_percent"))]
    for product in billing.get("products") or []:
        if isinstance(product, dict):
            values.append(_as_float(product.get("usage_percent")))
    values = [max(0.0, min(100.0, value)) for value in values if value is not None]
    return max(values) if values else None


def parse_billing_payload(data: dict) -> dict:
    cfg = data.get("config") if isinstance(data.get("config"), dict) else data
    if not isinstance(cfg, dict):
        cfg = {}
    period = cfg.get("currentPeriod") if isinstance(cfg.get("currentPeriod"), dict) else {}
    raw_products = cfg.get("productUsage") or cfg.get("products") or []
    products = []
    if isinstance(raw_products, list):
        for p in raw_products:
            if not isinstance(p, dict):
                continue
            pct = p.get("usagePercent")
            if pct is None:
                pct = p.get("usage_percent")
            try:
                value = float(pct) if pct is not None else None
                pct_v = max(0.0, min(100.0, value)) if value is not None and math.isfinite(value) else None
            except (TypeError, ValueError, OverflowError):
                pct_v = None
            name = str(p.get("product") or p.get("name") or "")[:40]
            if name:
                products.append({"product": name, "usage_percent": pct_v})
    pct = cfg.get("creditUsagePercent")
    if pct is None:
        pct = cfg.get("credit_usage_percent")
    try:
        value = float(pct) if pct is not None else None
        pct_v = max(0.0, min(100.0, value)) if value is not None and math.isfinite(value) else None
    except (TypeError, ValueError, OverflowError):
        pct_v = None
    if pct_v is None:
        used = _money_val(cfg.get("onDemandUsed"))
        cap = _money_val(cfg.get("onDemandCap"))
        if used is not None and cap:
            pct_v = max(0.0, min(100.0, (used / cap) * 100.0))
    return {
        "credit_usage_percent": pct_v,
        "period_type": str(period.get("type") or ""),
        "period_start": str(period.get("start") or cfg.get("billingPeriodStart") or ""),
        "period_end": str(period.get("end") or cfg.get("billingPeriodEnd") or ""),
        "products": products,
        "prepaid_cents": _money_val(cfg.get("prepaidBalance")) or 0,
        "on_demand_used_cents": _money_val(cfg.get("onDemandUsed")) or 0,
        "on_demand_cap_cents": _money_val(cfg.get("onDemandCap")) or 0,
        "monthly_limit_cents": _money_val(cfg.get("monthlyLimit")),
        "used_cents": _money_val(cfg.get("used")),
        "unified": bool(cfg.get("isUnifiedBillingUser") or cfg.get("unified")),
        "top_up_method": str(cfg.get("topUpMethod") or ""),
    }


def _parse_ts(ts: str) -> datetime | None:
    if not ts:
        return None
    raw = ts.replace("Z", "")
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f"):
        try:
            return datetime.strptime(raw.split("+")[0], fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _billing_fresh(billing: dict) -> bool:
    checked = billing.get("checked_at") or billing.get("fetched_at")
    when = _parse_ts(str(checked or ""))
    if not when:
        return False
    return datetime.now(timezone.utc) - when < timedelta(seconds=BILLING_TTL)


def _as_float(v) -> float | None:
    if v is None or isinstance(v, bool):
        return None
    try:
        value = float(v)
        return value if math.isfinite(value) else None
    except (TypeError, ValueError, OverflowError):
        return None


def apply_usage_guard(
    row,
    extra: dict,
    parsed: dict,
    now: datetime | None = None,
    *,
    enabled: bool = True,
) -> tuple[dict, str | None, float | None]:
    """Compare the new x.ai % to the last reading. If it rose with no Fuse
    invoke in this period, hold the credential out of the pool for 24h.

    Mutates extra['guard']. Returns (extra, pool_held_until, last_usage_percent).
    """
    now = now or datetime.now(timezone.utc)
    now_s = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    guard = extra.get("guard") if isinstance(extra.get("guard"), dict) else {}
    new_pct = _usage_percent(parsed)
    period_start = str(parsed.get("period_start") or "")
    invokes = int(_row_get(row, "pool_invokes") or 0)
    seen_invokes = int(guard.get("seen_invokes") or 0)
    invoked = invokes > seen_invokes
    last_pct = _as_float(guard.get("last_percent"))
    if last_pct is None:
        last_pct = _as_float(_row_get(row, "last_usage_percent"))
    last_period = str(guard.get("period_start") or "")
    period_changed = bool(last_period and period_start and period_start != last_period)
    held_until = _parse_ts(str(_row_get(row, "pool_held_until") or ""))
    currently_held = bool(held_until and held_until > now)

    new_hold = _row_get(row, "pool_held_until") or None
    if currently_held and not period_changed:
        new_hold = row["pool_held_until"]
    elif held_until or period_changed:
        new_hold = None
    if (
        enabled
        and not currently_held
        and new_pct is not None
        and last_pct is not None
        and last_period
        and period_start == last_period
        and (new_pct - last_pct) >= OFFPOOL_PCT
        and not invoked
    ):
        new_hold = (now + timedelta(hours=HOLD_HOURS)).strftime("%Y-%m-%dT%H:%M:%SZ")
        guard["held_from"] = last_pct
        guard["held_to"] = new_pct
        guard["held_at"] = now_s

    stored_pct = new_pct if new_pct is not None else (None if period_changed else last_pct)
    guard["last_percent"] = stored_pct
    if period_start:
        guard["period_start"] = period_start
    guard["seen_invokes"] = invokes
    guard["checked_at"] = now_s
    extra["guard"] = guard
    return extra, new_hold, stored_pct


def _remember_models(names: list[str]) -> list[str]:
    """Seed plus anything the live catalog adds. A later fetch only adds ids."""
    global _models_cache
    fresh = [n for n in names if isinstance(n, str) and n]
    if not fresh and _models_cache and _models_cache[1]:
        return list(_models_cache[1])
    base = list(_models_cache[1]) if _models_cache and _models_cache[1] else list(GROK_MODELS)
    merged: list[str] = []
    # Upstream order first (new models sit where the provider puts them),
    # then ids an earlier catalog listed and this one omits.
    for name in fresh + base:
        if name not in merged:
            merged.append(name)
    if merged:
        _models_cache = (time.time(), merged)
    return merged or list(GROK_MODELS)


async def fetch_catalog(token: str) -> list[str]:
    now = time.time()
    if _models_cache and now - _models_cache[0] < 60:
        return list(_models_cache[1])
    names: list[str] = []
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.get(settings.grok_proxy + "/models", headers=_auth_headers(token))
            if r.status_code == 200:
                names = [m.get("id") for m in r.json().get("data", []) if isinstance(m, dict) and m.get("id")]
            if not names:
                r = await client.get("https://api.x.ai/v1/models", headers={"Authorization": f"Bearer {token}"})
                if r.status_code == 200:
                    names = [m.get("id") for m in r.json().get("data", []) if isinstance(m, dict) and m.get("id")]
    except (httpx.HTTPError, json.JSONDecodeError, ValueError):
        names = []
    return _remember_models(names)


class GrokSource:
    type_id = "grok"

    def __init__(self, row, cfg: dict, creds):
        self.source_id = row["id"]
        self.name = row["name"]
        self.cfg = cfg
        self.creds = list(creds)

    def _headers(self, token: str, model: str, conv_id: str | None = None) -> dict:
        return _auth_headers(token, model, conv_id)

    def _pick(self, exclude: set[int] | None = None, prefer_id: int | None = None):
        now = datetime.now(timezone.utc)
        return pick_account(
            self.creds,
            kind="grok",
            exclude=exclude,
            is_held=lambda c: is_held(c, now),
            is_authed=credential_is_authed,
            prefer_id=prefer_id,
        )

    async def models(self) -> list[str]:
        picked = self._pick()
        if picked:
            cred, extra = picked
            token = extra.get("access_token") or accounts.cred_secret(cred)
            return await fetch_catalog(token)
        if _models_cache and _models_cache[1]:
            return list(_models_cache[1])
        return list(GROK_MODELS)

    async def chat(self, body: dict, *, session_id: str | None = None, user_id: int | None = None) -> ChatResult:
        # refresh_pool_billing() opens with its own refresh_due() sweep.
        await refresh_pool_billing()
        self.creds = pool_policy.filter_credentials(
            db.fetchall(
                "SELECT * FROM credentials WHERE source_id=? AND disabled=0",
                (self.source_id,),
            ),
            "grok",
            user_id,
        )
        tried: set[int] = set()
        attempts: list[dict] = []
        last = ChatResult(status=503, body={"error": "No Grok accounts in the pool"})
        model = body.get("model") or "grok-4.6"
        payload = drop_cache_routing_fields(body)
        prefer_id = lookup("grok", session_id)
        failover = Failover()
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
            token = extra.get("access_token") or accounts.cred_secret(cred)
            client = httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=15.0))
            stream_reserved = False
            try:
                reserve(cred["id"])
                status, obj, stream, usage, response_headers = await openai_chat(
                    client,
                    settings.grok_proxy + "/chat/completions",
                    self._headers(token, model, session_id),
                    payload,
                    close_client=True,
                    reasoning_separate=True,
                )
                if status == 401:
                    ok = await refresh_one(cred)
                    if ok:
                        attempts.append({"credential_id": cred["id"], "status": status, "usage": usage or {}})
                        extra = accounts.cred_extra(db.fetchone("SELECT * FROM credentials WHERE id=?", (cred["id"],)))
                        token = extra.get("access_token") or accounts.cred_secret(cred)
                        client = httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=15.0))
                        status, obj, stream, usage, response_headers = await openai_chat(
                            client,
                            settings.grok_proxy + "/chat/completions",
                            self._headers(token, model, session_id),
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
                rejected = False
                if status == 200:
                    failover.served()
                    bind("grok", session_id, cred["id"])
                elif status == 401:
                    status = 502
                    rejected = True
                    last = ChatResult(
                        status=status,
                        body={"error": {"message": "Grok rejected the configured account"}},
                        usage=usage,
                        credential_id=cred["id"],
                        model=model,
                    )
                if status == 429 or rejected:
                    note_failure(cred["id"], cooldown_seconds=30)
                last.response_headers = response_headers
                if (
                    status in (402, 429)
                    or rejected
                    or (status >= 500 and failover.server_error(cred["id"], status, 30))
                ):
                    attempts.append({"credential_id": cred["id"], "status": status, "usage": usage or {}})
                    continue
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
                # Only a stream that reached its terminal usage frame counts
                # as having served the conversation's cache prefix.
                status = (collected or {}).get("stream_status")
                if status == "complete":
                    bind("grok", session_id, cid)
                elif status == "failed":
                    note_failure(cid, cooldown_seconds=5, affinity_safe=True)

        wrapped = UsageStream(_bind_on_finish(), collected, on_close=lambda: release(cid))
        result = ChatResult(status=200, stream=wrapped, credential_id=cid, model=model)
        result.attempts = attempts
        return result


async def start_device() -> dict:
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.post(
            settings.grok_device_url,
            data={"client_id": CLIENT_ID, "scope": settings.grok_scope},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        r.raise_for_status()
        return r.json()


async def poll_device(device_code: str) -> dict:
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.post(
            settings.grok_token_url,
            data={
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                "device_code": device_code,
                "client_id": CLIENT_ID,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        return r.json()


def _cas_update_tokens(cred_id: int, old_extra_raw: str, new_secret: str, new_extra: dict, *, label: str | None = None, reenable: bool = False) -> bool:
    """Update tokens only if `extra` has not changed since it was read.

    A concurrent OAuth re-link (store_tokens) or another refresh landing in
    between would otherwise be silently overwritten with stale tokens.
    Returns False on conflict so the caller can re-read and re-merge.
    """
    if reenable:
        sql = "UPDATE credentials SET secret=?, extra=?, disabled=0, label=COALESCE(?, label) WHERE id=? AND extra=?"
        args = (new_secret, encrypt(json.dumps(new_extra)), label, cred_id, old_extra_raw)
    else:
        sql = "UPDATE credentials SET secret=?, extra=? WHERE id=? AND extra=?"
        args = (new_secret, encrypt(json.dumps(new_extra)), cred_id, old_extra_raw)
    with db.tx() as conn:
        cur = conn.execute(sql, args)
        return cur.rowcount > 0


def store_tokens(source_id: int, tokens: dict, donated_by: int | None, label: str = "", *, profile: dict | None = None) -> int:
    account_id = account_from_tokens(tokens) or ""
    extra = {
        "access_token": tokens["access_token"],
        "refresh_token": tokens.get("refresh_token", ""),
        "expires_at": expires_at(tokens.get("expires_in"), 21600),
        "scope": tokens.get("scope", ""),
        "account_id": account_id,
    }
    apply_profile(extra, profile)
    if extra.get("email"):
        label = label or extra["email"]
    if account_id:
        rows = db.fetchall(
            "SELECT * FROM credentials WHERE source_id=? AND donated_by IS ? ORDER BY disabled, id",
            (source_id, donated_by),
        )
        for row in rows:
            current = accounts.cred_extra(row)
            current_id = current.get("account_id") or account_from_tokens(current)
            if current_id != account_id:
                continue
            current.update(extra)
            if _cas_update_tokens(
                int(row["id"]), row["extra"], encrypt(tokens["access_token"]), current,
                label=label or row["label"] or "Grok account", reenable=True,
            ):
                return int(row["id"])
            # A concurrent token refresh changed `extra` under us: re-read
            # and re-apply once so both the fresh tokens and whatever the
            # refresh wrote survive.
            retry = db.fetchone("SELECT * FROM credentials WHERE id=?", (row["id"],))
            if retry is not None:
                merged = accounts.cred_extra(retry)
                merged.update(extra)
                if _cas_update_tokens(
                    int(row["id"]), retry["extra"], encrypt(tokens["access_token"]), merged,
                    label=label or retry["label"] or "Grok account", reenable=True,
                ):
                    return int(row["id"])
    return accounts.add_credential(
        source_id,
        label or "Grok account",
        tokens["access_token"],
        extra=extra,
        donated_by=donated_by,
    )


async def _refresh_one_unlocked(cred) -> bool:
    row = db.fetchone("SELECT * FROM credentials WHERE id=?", (cred["id"],))
    if not row or row["disabled"]:
        return False
    extra = accounts.cred_extra(row)
    rt = extra.get("refresh_token")
    if not rt:
        return False
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.post(
                settings.grok_token_url,
                data={"grant_type": "refresh_token", "refresh_token": rt, "client_id": CLIENT_ID},
                headers={"Content-Type": "application/x-www-form-urlencoded"},
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
    if "access_token" not in data:
        if _oauth_revoked(r.status_code, data):
            disable_credential(row["id"])
        else:
            note_failure(row["id"], cooldown_seconds=60 if r.status_code == 429 else 30)
        return False
    extra["access_token"] = data["access_token"]
    extra["refresh_token"] = data.get("refresh_token", rt)
    extra["expires_at"] = expires_at(data.get("expires_in"), 21600)
    if not extra.get("email"):
        apply_profile(extra, await fetch_userinfo(data["access_token"]))
    # `extra` may change while the token endpoint is in flight (re-link or
    # another refresh); persist_rotated re-merges onto the newest copy.

    def _merge(merged: dict) -> None:
        if extra.get("email") and not merged.get("email"):
            merged["email"] = extra["email"]
        if extra.get("name") and not merged.get("name"):
            merged["name"] = extra["name"]

    return persist_rotated(
        _cas_update_tokens,
        int(row["id"]),
        row["extra"],
        encrypt(data["access_token"]),
        extra,
        {
            "access_token": data["access_token"],
            "refresh_token": data.get("refresh_token", rt),
            "expires_at": extra["expires_at"],
        },
        merge=_merge,
    )


async def refresh_one(cred) -> bool:
    async with _refresh_lock(int(cred["id"])):
        return await _refresh_one_unlocked(cred)


def _store_billing(cred_id: int, parsed: dict, *, error: str = "", profile: dict | None = None) -> dict:
    row = db.fetchone("SELECT * FROM credentials WHERE id=?", (cred_id,))
    extra = accounts.cred_extra(row) if row else {}
    apply_profile(extra, profile)
    billing = extra.get("billing") if isinstance(extra.get("billing"), dict) else {}
    now = db.now()
    billing["checked_at"] = now
    if error:
        billing["last_error"] = error[:200]
        extra["billing"] = billing
        accounts.update_extra(cred_id, extra)
        return billing
    billing.update(parsed)
    billing["fetched_at"] = now
    billing["last_error"] = ""
    extra["billing"] = billing
    source = db.fetchone("SELECT config FROM sources WHERE id=?", (row["source_id"],)) if row else None
    try:
        cfg = json.loads((source["config"] if source else None) or "{}")
    except json.JSONDecodeError:
        cfg = {}
    guard_enabled = cfg.get("offpool_usage_guard", True) is not False
    extra, held_until, last_pct = apply_usage_guard(row, extra, parsed, enabled=guard_enabled)
    extra["billing"] = billing
    db.execute(
        """UPDATE credentials SET extra=?, last_usage_percent=?, usage_percent_at=?, pool_held_until=?
           WHERE id=?""",
        (encrypt(json.dumps(extra)), last_pct, now, held_until, cred_id),
    )
    return billing


async def refresh_billing(cred, *, force: bool = False) -> dict | None:
    async with _refresh_lock(int(cred["id"])):
        row = db.fetchone("SELECT * FROM credentials WHERE id=?", (cred["id"],))
        if not row or row["disabled"]:
            return None
        extra = accounts.cred_extra(row)
        billing = extra.get("billing") if isinstance(extra.get("billing"), dict) else {}
        if not force and _billing_fresh(billing):
            return billing
        token = extra.get("access_token") or accounts.cred_secret(row)
        status = 0
        payload: dict | None = None
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                r = await client.get(
                    settings.grok_proxy + "/billing?format=credits",
                    headers=_cli_get_headers(token),
                )
                status = r.status_code
                if status == 401:
                    if await _refresh_one_unlocked(row):
                        row = db.fetchone("SELECT * FROM credentials WHERE id=?", (cred["id"],))
                        extra = accounts.cred_extra(row)
                        token = extra.get("access_token") or accounts.cred_secret(row)
                        r = await client.get(
                            settings.grok_proxy + "/billing?format=credits",
                            headers=_cli_get_headers(token),
                        )
                        status = r.status_code
                if status == 200:
                    try:
                        payload = r.json()
                    except json.JSONDecodeError:
                        payload = None
        except Exception:
            return _store_billing(row["id"], {}, error="fetch failed")
        if status != 200 or not isinstance(payload, dict):
            return _store_billing(row["id"], {}, error=f"http {status or 0}")
        parsed = parse_billing_payload(payload)
        profile = None
        if not extra.get("email"):
            profile = await fetch_userinfo(token)
        return _store_billing(row["id"], parsed, profile=profile)


async def refresh_pool_billing(*, force: bool = False) -> None:
    await refresh_due()
    rows = db.fetchall(
        "SELECT c.* FROM credentials c JOIN sources s ON s.id=c.source_id WHERE s.type='grok' AND c.disabled=0"
    )
    if not rows:
        return
    await asyncio.gather(*(refresh_billing(c, force=force) for c in rows), return_exceptions=True)


async def refresh_due() -> None:
    rows = db.fetchall(
        "SELECT c.* FROM credentials c JOIN sources s ON s.id=c.source_id WHERE s.type='grok' AND c.disabled=0"
    )
    soon = datetime.now(timezone.utc) + timedelta(minutes=5)
    due = []
    for cred in rows:
        extra = accounts.cred_extra(cred)
        exp = extra.get("expires_at")
        if not exp:
            due.append(cred)
            continue
        try:
            when = datetime.strptime(exp.replace("Z", ""), "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
        except ValueError:
            due.append(cred)
            continue
        if when <= soon:
            due.append(cred)
    if due:
        await asyncio.gather(*(refresh_one(c) for c in due), return_exceptions=True)
