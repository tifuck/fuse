from __future__ import annotations

import asyncio
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
from .grok import apply_usage_guard, is_held
from .pool_pick import Failover, auth_failure_confirmed, note_failure, pick_account, release, reserve
from . import pool_policy
from .pool_session import bind, drop_cache_routing_fields, lookup
from .token_store import log_refresh_failures

USAGE_TTL = 300
HOLD_HOURS = 24
_models_cache: tuple[float, list[str]] | None = None
_refresh_locks: dict[int, asyncio.Lock] = {}

ZAI_MODELS = [
    "glm-5.3",
    "glm-5.3-flash",
    "glm-5.3-highspeed",
    "glm-5.2",
    "glm-5.2-highspeed",
    "glm-5-turbo",
    "glm-4.7",
]

_REGIONS = ("global", "cn")


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


def ensure_zai_source() -> int:
    row = db.fetchone("SELECT id FROM sources WHERE type='zai'")
    if row:
        return row["id"]
    with db.tx() as conn:
        conn.execute(
            "INSERT INTO sources (type, name, enabled, config, created_at) VALUES (?,?,?,?,?)",
            ("zai", "Z.AI", 1, "{}", db.now()),
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


def _as_float(v) -> float | None:
    if v is None or isinstance(v, bool):
        return None
    try:
        value = float(v)
        return value if math.isfinite(value) else None
    except (TypeError, ValueError, OverflowError):
        return None


def _as_int(v) -> int | None:
    if v is None or isinstance(v, bool):
        return None
    try:
        return int(v)
    except (TypeError, ValueError, OverflowError):
        return None


def _endpoints(region: str) -> tuple[str, str]:
    if region == "cn":
        return settings.zai_cn_coding_base.rstrip("/"), settings.zai_cn_quota_url
    return settings.zai_coding_base.rstrip("/"), settings.zai_quota_url


def _quota_headers(key: str, *, bearer: bool) -> dict:
    token = f"Bearer {key}" if bearer else key
    return {
        "Authorization": token,
        "Accept": "application/json",
        "Accept-Language": "en-US,en",
        "Content-Type": "application/json",
    }


def _chat_headers(key: str) -> dict:
    return {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Accept-Language": "en-US,en",
    }


def _unix_iso(value) -> str:
    raw = _as_int(value)
    if raw is None:
        return ""
    if raw > 10_000_000_000:
        raw //= 1000
    try:
        return datetime.fromtimestamp(raw, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (OSError, OverflowError, ValueError):
        return ""


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


def parse_quota_payload(data: dict) -> dict:
    root = data.get("data") if isinstance(data.get("data"), dict) else data
    if not isinstance(root, dict):
        root = {}
    limits = root.get("limits") if isinstance(root.get("limits"), list) else []
    level = str(root.get("level") or "")[:32]
    if not limits:
        five_legacy = _as_float(root.get("fiveHourPercent") if "fiveHourPercent" in root else root.get("five_hour_percent"))
        week_legacy = _as_float(root.get("weeklyPercent") if "weeklyPercent" in root else root.get("weekly_percent"))
        mcp_legacy = _as_float(root.get("monthlyMCPUsage") if "monthlyMCPUsage" in root else root.get("mcp_percent"))
        if five_legacy is not None:
            limits.append({"type": "TOKENS_LIMIT", "unit": 3, "number": 5, "percentage": five_legacy})
        if week_legacy is not None:
            limits.append({"type": "TOKENS_LIMIT", "unit": 6, "number": 1, "percentage": week_legacy})
        if mcp_legacy is not None:
            limits.append({"type": "TIME_LIMIT", "percentage": mcp_legacy})

    token_rows: list[dict] = []
    mcp: dict | None = None
    for item in limits:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("type") or "")
        pct = _as_float(item.get("percentage"))
        reset_ms = _as_int(item.get("nextResetTime") if "nextResetTime" in item else item.get("next_reset_time"))
        if kind in ("TOKENS_LIMIT", "CREDIT_LIMIT"):
            token_rows.append(
                {
                    "kind": kind,
                    "unit": _as_int(item.get("unit")),
                    "number": _as_int(item.get("number")),
                    "percent": pct,
                    "reset_ms": reset_ms,
                    "current": _as_int(item.get("currentValue") if "currentValue" in item else item.get("current_value")),
                    "total": _as_int(item.get("usage") if item.get("usage") is not None else item.get("total")),
                    "remaining": _as_int(item.get("remaining")),
                }
            )
        elif kind == "TIME_LIMIT":
            mcp = {
                "percent": pct,
                "reset_ms": reset_ms,
                "current": _as_int(item.get("currentValue") if "currentValue" in item else item.get("current_value")),
                "total": _as_int(item.get("usage")),
            }

    five = None
    weekly = None
    leftover: list[dict] = []
    for row in token_rows:
        unit, number = row["unit"], row["number"]
        if unit == 3 and number == 5:
            five = row
        elif unit == 6 and number in (1, 7):
            weekly = row
        else:
            leftover.append(row)
    leftover.sort(key=lambda r: r.get("reset_ms") or 0)
    if five is None and leftover:
        five = leftover.pop(0)
    if weekly is None and leftover:
        weekly = leftover.pop(0)

    windows = []
    products = []

    def _add(name: str, seconds: int, row: dict | None) -> None:
        if not row or row.get("percent") is None:
            return
        reset_at = _unix_iso(row.get("reset_ms"))
        windows.append({"name": name, "percent": row["percent"], "seconds": seconds, "reset_at": reset_at})
        products.append({"product": name, "usage_percent": row["percent"]})

    _add("5-hour", 5 * 3600, five)
    _add("Weekly", 7 * 86400, weekly)
    if mcp and mcp.get("percent") is not None:
        products.append({"product": "MCP", "usage_percent": mcp["percent"]})

    pcts = [w["percent"] for w in windows if w.get("percent") is not None]
    hero = None
    for win in windows:
        if hero is None or win["seconds"] > hero["seconds"]:
            hero = win
    period_end = (hero or {}).get("reset_at") or ""
    period_start = ""
    if weekly and weekly.get("reset_ms"):
        period_start = _unix_iso(weekly["reset_ms"])
    elif five and five.get("reset_ms"):
        period_start = _unix_iso(five["reset_ms"])

    return {
        "credit_usage_percent": max(pcts) if pcts else None,
        "period_type": (hero["name"] if hero else "") or "Usage",
        "period_start": period_start,
        "period_end": period_end,
        "products": products,
        "windows": windows,
        "plan_type": level.lower() if level else "",
        "five_hour_percent": five.get("percent") if five else None,
        "weekly_percent": weekly.get("percent") if weekly else None,
        "mcp_percent": mcp.get("percent") if mcp else None,
    }


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
    return False


def known_unsubscribed(cred, extra: dict | None = None) -> bool:
    """A successful billing read shows no plan: route there only as a last resort."""
    extra = extra if extra is not None else accounts.cred_extra(cred)
    billing = extra.get("billing") if isinstance(extra.get("billing"), dict) else {}
    return bool(billing.get("fetched_at")) and not billing_has_subscription(billing)


def credential_is_authed(cred, extra: dict | None = None) -> bool:
    if _row_get(cred, "disabled"):
        return False
    extra = extra if extra is not None else accounts.cred_extra(cred)
    key = extra.get("api_key") or accounts.cred_secret(cred)
    if not key:
        return False
    billing = extra.get("billing") if isinstance(extra.get("billing"), dict) else {}
    err = str(billing.get("last_error") or "").lower()
    if "401" in err or "403" in err:
        return False
    return True


def zai_own_access(user_id: int) -> dict:
    """Access from this user's own donated Z.AI keys only."""
    sid = ensure_zai_source()
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
    return {"ok": False, "reason": "no_sub" if saw_authed else "unauthed"}


def _zai_live(row) -> bool:
    """One key another user could be served by right now."""
    if is_held(row):
        return False
    extra = accounts.cred_extra(row)
    if not credential_is_authed(row, extra):
        return False
    billing = extra.get("billing") if isinstance(extra.get("billing"), dict) else {}
    return billing_has_subscription(billing)


def zai_access_state(user_id: int) -> dict:
    """Whether this user may call Z.AI models, and why not if they may not.

    A live donated Z.AI Coding Plan key still unlocks GLM. When sharing is
    on, a live x.ai or OpenAI account the user shares also unlocks the Z.AI
    pool (a private one does not: it gives nothing back), and the admin
    open-access switch lets any registered user in without a key. Held-out-
    of-pool is a routing concern and does not revoke access.
    """
    own = zai_own_access(user_id)
    if own["ok"]:
        return own
    opened = pool_policy.with_open_access(user_id, "zai", own, _zai_live)
    if opened.get("ok"):
        return opened
    if pool_policy.uses_shared_pool(user_id, "zai") and (
        pool_policy.contributes(user_id, "grok") or pool_policy.contributes(user_id, "chatgpt")
    ):
        return {"ok": True, "reason": "pool"}
    return own


def user_has_zai(user_id: int) -> bool:
    return zai_access_state(user_id)["ok"]


def note_pool_invoke(cred_id: int) -> None:
    db.execute("UPDATE credentials SET pool_invokes = COALESCE(pool_invokes, 0) + 1 WHERE id=?", (cred_id,))


def _envelope_ok(data: dict) -> bool:
    if data.get("success") is False:
        return False
    code = data.get("code")
    if code is None:
        return True
    return code in (200, "200")


async def fetch_quota_for_key(key: str, region: str) -> tuple[dict | None, str]:
    _, quota_url = _endpoints(region)
    last = "quota fetch failed"
    async with httpx.AsyncClient(timeout=15.0, follow_redirects=False) as client:
        for bearer in (False, True):
            try:
                r = await client.get(quota_url, headers=_quota_headers(key, bearer=bearer))
            except Exception:
                last = "fetch failed"
                continue
            if r.status_code in (401, 403):
                last = f"http {r.status_code}"
                continue
            if r.status_code != 200:
                last = f"http {r.status_code}"
                continue
            try:
                data = r.json()
            except json.JSONDecodeError:
                last = "invalid JSON"
                continue
            if not isinstance(data, dict):
                last = "invalid JSON"
                continue
            if not _envelope_ok(data) and not isinstance(data.get("data"), dict) and "limits" not in data:
                last = str(data.get("msg") or data.get("message") or "quota rejected")[:200]
                continue
            parsed = parse_quota_payload(data)
            parsed["_quota_auth"] = "bearer" if bearer else "raw"
            return parsed, ""
    return None, last


async def probe_key(key: str) -> tuple[dict | None, str, str]:
    last_err = "Could not reach Z.AI."
    for region in _REGIONS:
        parsed, err = await fetch_quota_for_key(key, region)
        if parsed is not None:
            if billing_has_subscription(parsed):
                return parsed, region, ""
            last_err = "This key is not a GLM Coding Plan key. Use a key from Individual Coding Plan → Plan Overview."
            return None, region, last_err
        last_err = "Invalid API key." if err in ("http 401", "http 403") else err
        if err in ("http 401", "http 403"):
            continue
        if err == "fetch failed":
            continue
    return None, "", last_err


def store_key(source_id: int, key: str, donated_by: int | None, *, label: str = "", region: str = "global") -> int:
    extra = {
        "api_key": key,
        "region": region if region in _REGIONS else "global",
    }
    name = (label or "").strip()[:80] or ("Z.AI (CN)" if region == "cn" else "Z.AI account")
    return accounts.add_credential(source_id, name, key, extra=extra, donated_by=donated_by)


def _find_key(source_id: int, key: str):
    for row in db.fetchall("SELECT * FROM credentials WHERE source_id=?", (source_id,)):
        try:
            if accounts.cred_secret(row) == key:
                return row
        except Exception:
            continue
    return None


async def add_plan_key(source_id: int, raw: str, donated_by: int, *, label: str = "") -> tuple[int | None, str]:
    key = (raw or "").strip()
    if not key or len(key) < 8 or len(key) > 4096:
        return None, "Enter a Z.AI Coding Plan API key."
    if any(ord(ch) < 32 for ch in key):
        return None, "Enter a Z.AI Coding Plan API key."
    existing = _find_key(source_id, key)
    if existing and not existing["disabled"]:
        return None, "That key is already in the pool."
    parsed, region, err = await probe_key(key)
    if err or parsed is None:
        return None, err or "Could not validate this key."
    if existing:
        name = (label or "").strip()[:80] or existing["label"] or "Z.AI account"
        extra = accounts.cred_extra(existing)
        extra["api_key"] = key
        extra["region"] = region if region in _REGIONS else "global"
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


def _store_billing(cred_id: int, parsed: dict, *, error: str = "", region: str | None = None) -> dict:
    row = db.fetchone("SELECT * FROM credentials WHERE id=?", (cred_id,))
    extra = accounts.cred_extra(row) if row else {}
    billing = extra.get("billing") if isinstance(extra.get("billing"), dict) else {}
    now = db.now()
    billing["checked_at"] = now
    if region in _REGIONS:
        extra["region"] = region
    auth = parsed.get("_quota_auth") if isinstance(parsed, dict) else None
    if auth:
        extra["quota_auth"] = auth
    if error:
        billing["last_error"] = error[:200]
        extra["billing"] = billing
        if row:
            accounts.update_extra(cred_id, extra)
        return billing
    clean = {k: v for k, v in parsed.items() if not str(k).startswith("_")}
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


async def refresh_quota(cred, *, force: bool = False, parsed: dict | None = None, region: str | None = None) -> dict | None:
    async with _refresh_lock(int(cred["id"])):
        row = db.fetchone("SELECT * FROM credentials WHERE id=?", (cred["id"],))
        if not row or row["disabled"]:
            return None
        extra = accounts.cred_extra(row)
        billing = extra.get("billing") if isinstance(extra.get("billing"), dict) else {}
        if not force and parsed is None and _usage_fresh(billing) and billing.get("windows") is not None:
            return billing
        key = extra.get("api_key") or accounts.cred_secret(row)
        if not key:
            return _store_billing(row["id"], {}, error="missing key")
        stored_region = region or extra.get("region") or "global"
        if stored_region not in _REGIONS:
            stored_region = "global"
        if parsed is None:
            parsed, err = await fetch_quota_for_key(key, stored_region)
            if parsed is None and stored_region == "global":
                other, other_err = await fetch_quota_for_key(key, "cn")
                if other is not None:
                    parsed, err, stored_region = other, "", "cn"
                else:
                    err = err or other_err
            elif parsed is None and stored_region == "cn":
                other, other_err = await fetch_quota_for_key(key, "global")
                if other is not None:
                    parsed, err, stored_region = other, "", "global"
                else:
                    err = err or other_err
            if parsed is None:
                return _store_billing(row["id"], {}, error=err or "fetch failed", region=stored_region)
        return _store_billing(row["id"], parsed, region=stored_region)


async def refresh_pool_quota(*, force: bool = False) -> None:
    rows = db.fetchall(
        "SELECT c.* FROM credentials c JOIN sources s ON s.id=c.source_id WHERE s.type='zai' AND c.disabled=0"
    )
    if not rows:
        return
    results = await asyncio.gather(*(refresh_quota(c, force=force) for c in rows), return_exceptions=True)
    log_refresh_failures("Z.AI quota refresh", rows, results)


def _remember_models(names: list[str]) -> list[str]:
    global _models_cache
    fresh = [n for n in names if isinstance(n, str) and n]
    if not fresh and _models_cache and _models_cache[1]:
        return list(_models_cache[1])
    base = list(_models_cache[1]) if _models_cache and _models_cache[1] else list(ZAI_MODELS)
    merged: list[str] = []
    # Upstream order first (new models sit where the provider puts them),
    # then ids an earlier catalog listed and this one omits.
    for name in fresh + base:
        if name not in merged:
            merged.append(name)
    if merged:
        _models_cache = (time.time(), merged)
    return merged or list(ZAI_MODELS)


async def fetch_catalog(key: str, region: str) -> list[str]:
    now = time.time()
    if _models_cache and now - _models_cache[0] < 60:
        return list(_models_cache[1])
    names: list[str] = []
    base, _ = _endpoints(region)
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.get(base + "/models", headers=_chat_headers(key))
            if r.status_code == 200:
                data = r.json()
                names = [m.get("id") for m in (data.get("data") or []) if isinstance(m, dict) and m.get("id")]
    except (httpx.HTTPError, json.JSONDecodeError, ValueError):
        names = []
    return _remember_models(names)


def _chat_payload(body: dict) -> dict:
    payload = drop_cache_routing_fields(body)
    # `reasoning_effort` is the OpenAI Chat Completions control. GLM exposes a
    # per-turn enabled/disabled switch, not effort levels, so retain a native
    # `thinking` object when supplied and otherwise map only its on/off intent.
    reasoning = payload.get("reasoning")
    effort = payload.get("reasoning_effort")
    if effort is None and isinstance(reasoning, dict):
        effort = reasoning.get("effort")
    if "thinking" not in payload and isinstance(effort, str):
        payload["thinking"] = {
            "type": "disabled" if effort == "none" else "enabled"
        }
    payload.pop("reasoning_effort", None)
    payload.pop("reasoning", None)
    return payload


class ZaiSource:
    type_id = "zai"

    def __init__(self, row, cfg: dict, creds):
        self.source_id = row["id"]
        self.name = row["name"]
        self.cfg = cfg
        self.creds = list(creds)

    def _pick(self, exclude: set[int] | None = None, prefer_id: int | None = None, owner_id: int | None = None):
        now = datetime.now(timezone.utc)
        return pick_account(
            self.creds,
            kind="zai",
            exclude=exclude,
            is_held=lambda c: is_held(c, now),
            is_authed=credential_is_authed,
            no_plan=known_unsubscribed,
            prefer_id=prefer_id,
            owner_id=owner_id,
        )

    async def models(self) -> list[str]:
        picked = self._pick()
        if picked:
            cred, extra = picked
            key = extra.get("api_key") or accounts.cred_secret(cred)
            region = extra.get("region") or "global"
            names = await fetch_catalog(key, region)
            if names:
                return names
        if _models_cache and _models_cache[1]:
            return list(_models_cache[1])
        return list(ZAI_MODELS)

    async def chat(self, body: dict, *, session_id: str | None = None, user_id: int | None = None) -> ChatResult:
        await refresh_pool_quota()
        self.creds = pool_policy.filter_credentials(
            db.fetchall(
                "SELECT * FROM credentials WHERE source_id=? AND disabled=0",
                (self.source_id,),
            ),
            "zai",
            user_id,
        )
        tried: set[int] = set()
        attempts: list[dict] = []
        last = ChatResult(status=503, body={"error": "No Z.AI accounts in the pool"})
        model = body.get("model") or ZAI_MODELS[0]
        payload = _chat_payload(body)
        prefer_id = lookup("zai", session_id)
        failover = Failover()
        while True:
            picked = self._pick(exclude=tried, prefer_id=prefer_id, owner_id=user_id)
            if not picked:
                # Parity with grok/chatgpt: the final failed attempt is
                # recorded once via the returned result; drop the duplicate.
                if attempts and last.credential_id == attempts[-1].get("credential_id"):
                    attempts.pop()
                last.attempts = attempts
                return last
            cred, extra = picked
            tried.add(int(cred["id"]))
            note_pool_invoke(cred["id"])
            reserve(cred["id"])
            key = extra.get("api_key") or accounts.cred_secret(cred)
            region = extra.get("region") or "global"
            if region not in _REGIONS:
                region = "global"
            base, _ = _endpoints(region)
            client = httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=15.0))
            stream_reserved = False
            try:
                status, obj, stream, usage, response_headers = await openai_chat(
                    client,
                    base + "/chat/completions",
                    _chat_headers(key),
                    payload,
                    close_client=True,
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
                    if auth_failure_confirmed(cred["id"]):
                        disable_credential(cred["id"], extra=cred.get("extra"))
                    last.status = 502
                    last.body = {"error": {"message": "Z.AI rejected the configured API key"}}
                    last.response_headers = response_headers
                    attempts.append({"credential_id": cred["id"], "status": status, "usage": usage or {}})
                    last.attempts = attempts
                    continue
                if status == 429:
                    try:
                        await refresh_quota(cred, force=True)
                    except Exception:
                        pass
                    note_failure(cred["id"], cooldown_seconds=60)
                    last.response_headers = response_headers
                    # Quota may have been consumed even though the attempt
                    # failed; record whatever usage the attempt reported.
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
                    bind("zai", session_id, cred["id"])
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
                    bind("zai", session_id, cid)
                elif status == "failed":
                    note_failure(cid, cooldown_seconds=5, affinity_safe=True)

        wrapped = UsageStream(_bind_on_finish(), collected, on_close=lambda: release(cid))
        result = ChatResult(status=200, stream=wrapped, credential_id=cid, model=model)
        result.attempts = attempts
        return result
