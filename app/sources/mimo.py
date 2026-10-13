"""Xiaomi MiMo Token Plan pool.

Login matches MiMo Code (`packages/opencode/src/plugin/mimo.ts`): the browser
opens platform.xiaomimimo.com/authorize with an X25519 public key. Xiaomi
returns an encrypted blob (`u`) that decrypts to the Token Plan API key, the
regional base URL, and an account id. There is no refresh token — the key is
the credential. Chat is OpenAI-compatible on that regional host. Probes are
catalog only and never send a completion.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import math
import secrets
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import httpx
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

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
_models_cache: tuple[float, list[str]] | None = None
_refresh_locks: dict[int, asyncio.Lock] = {}

# Text models on the Token Plan. Speech models are not chat completions.
MIMO_MODELS = [
    "mimo-v2.6-pro",
    "mimo-v2.6-flash",
    "mimo-v2.5-pro",
    "mimo-v2.5",
]

# USD per million, API-equivalent. Token Plan bills credits, not these rates.
# Cache hits are ~100x cheaper than a miss (docs: 2.5 vs 300 credits on Pro).
MIMO_PRICING = {
    "mimo-v2.6-pro": ("0.435", "0.0036", None, "0.87"),
    "mimo-v2.6-flash": ("0.14", "0.0028", None, "0.28"),
    "mimo-v2.5-pro": ("0.435", "0.0036", None, "0.87"),
    "mimo-v2.5": ("0.14", "0.0028", None, "0.28"),
}

CONTEXT_WINDOW = 1_048_576
_REGIONS = ("cn", "sgp", "ams")
_HOSTS = {
    "cn": "token-plan-cn.xiaomimimo.com",
    "sgp": "token-plan-sgp.xiaomimimo.com",
    "ams": "token-plan-ams.xiaomimimo.com",
}
_SPKI_PREFIX = bytes.fromhex("302a300506032b656e032100")


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


def ensure_mimo_source() -> int:
    row = db.fetchone("SELECT id FROM sources WHERE type='mimo'")
    if row:
        return row["id"]
    with db.tx() as conn:
        conn.execute(
            "INSERT INTO sources (type, name, enabled, config, created_at) VALUES (?,?,?,?,?)",
            ("mimo", "Xiaomi", 1, "{}", db.now()),
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


def is_mimo_model(model: str) -> bool:
    mid = (model or "").strip()
    return mid in MIMO_MODELS or mid.startswith("mimo-")


def model_window(model: str) -> int:
    mid = (model or "").strip()
    if mid.startswith("mimo-v2.5-tts") or mid.startswith("mimo-v2.5-asr") or mid == "mimo-v2-tts":
        return 8_192
    if mid.startswith("mimo-"):
        return CONTEXT_WINDOW
    return 0


def _platform() -> str:
    return (settings.mimo_platform_url or "https://platform.xiaomimimo.com").rstrip("/")


def region_for_url(url: str) -> str:
    host = (urlparse(url or "").hostname or "").lower()
    for region, name in _HOSTS.items():
        if host == name:
            return region
    return ""


def base_for_region(region: str) -> str:
    host = _HOSTS.get(region) or _HOSTS["cn"]
    return f"https://{host}/v1"


def _chat_headers(key: str) -> dict:
    return {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "X-Mimo-Source": "mimocode-cli",
    }


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


def billing_has_subscription(billing: dict | None) -> bool:
    if not isinstance(billing, dict):
        return False
    if billing.get("last_error") and not billing.get("fetched_at"):
        return False
    if billing.get("fetched_at"):
        return True
    if str(billing.get("plan_type") or "").strip():
        return True
    return billing.get("credit_usage_percent") is not None


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


def mimo_own_access(user_id: int) -> dict:
    sid = ensure_mimo_source()
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


def _mimo_live(row) -> bool:
    if is_held(row):
        return False
    extra = accounts.cred_extra(row)
    if not credential_is_authed(row, extra):
        return False
    billing = extra.get("billing") if isinstance(extra.get("billing"), dict) else {}
    return billing_has_subscription(billing)


def mimo_access_state(user_id: int) -> dict:
    own = mimo_own_access(user_id)
    if own["ok"]:
        return own
    return pool_policy.with_open_access(user_id, "mimo", own, _mimo_live)


def user_has_mimo(user_id: int) -> bool:
    return mimo_access_state(user_id)["ok"]


def note_pool_invoke(cred_id: int) -> None:
    db.execute("UPDATE credentials SET pool_invokes = COALESCE(pool_invokes, 0) + 1 WHERE id=?", (cred_id,))


def start_login() -> dict:
    """X25519 keypair for one MiMo Code authorize round. Private key stays server-side."""
    private = X25519PrivateKey.generate()
    raw_pub = private.public_key().public_bytes_raw()
    public = base64.urlsafe_b64encode(_SPKI_PREFIX + raw_pub).decode("ascii").rstrip("=")
    private_b64 = base64.urlsafe_b64encode(private.private_bytes_raw()).decode("ascii").rstrip("=")
    state = secrets.token_urlsafe(18)
    return {
        "public_key": public,
        "private_key": private_b64,
        "state": state,
        "manual_url": authorize_url(public, manual=True),
    }


def authorize_url(public_key: str, *, manual: bool = False, redirect_uri: str = "") -> str:
    from urllib.parse import urlencode

    if manual or not redirect_uri:
        redirect = _platform() + "/authorize/code/callback"
    else:
        redirect = redirect_uri
    query = urlencode(
        {
            "pk": public_key,
            "redirect_uri": redirect,
            "kn": "mimocode",
            "key_name": "fuse-" + secrets.token_hex(4),
        }
    )
    return f"{_platform()}/authorize?{query}"


def _b64decode(value: str) -> bytes:
    pad = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + pad)


def decrypt_payload(private_key_b64: str, encrypted_b64: str) -> dict:
    """Decrypt the `u` blob MiMo Code's authorize callback returns.

    Layout: ephemeral X25519 public (32) || nonce (12) || ciphertext || tag (16).
    The shared secret is SHA-256'd into the AES-256-GCM key.
    """
    encrypted = _b64decode((encrypted_b64 or "").strip())
    if len(encrypted) < 32 + 12 + 16 + 2:
        raise ValueError("short")
    ephemeral = encrypted[:32]
    nonce = encrypted[32:44]
    body = encrypted[44:]
    tag = body[-16:]
    ciphertext = body[:-16]
    private = X25519PrivateKey.from_private_bytes(_b64decode(private_key_b64))
    shared = private.exchange(X25519PublicKey.from_public_bytes(ephemeral))
    key = hashlib.sha256(shared).digest()
    plain = AESGCM(key).decrypt(nonce, ciphertext + tag, None)
    data = json.loads(plain.decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError("payload")
    return data


def _normalize_login(data: dict) -> tuple[str, str, str]:
    key = str(data.get("sk") or data.get("api_key") or "").strip()
    uid = str(data.get("uid") or "")[:80]
    url = str(data.get("url") or "").strip()
    if not key or len(key) < 8 or len(key) > 4096 or any(ord(ch) < 32 for ch in key):
        raise ValueError("key")
    region = region_for_url(url)
    if url and not region:
        raise ValueError("url")
    if not region:
        region = "cn"
        url = base_for_region(region)
    if not url.endswith("/v1"):
        url = base_for_region(region)
    return key, region, uid


def _find_key(source_id: int, key: str):
    for row in db.fetchall("SELECT * FROM credentials WHERE source_id=?", (source_id,)):
        try:
            if accounts.cred_secret(row) == key:
                return row
        except Exception:
            continue
    return None


def _save_key(
    source_id: int,
    key: str,
    region: str,
    donated_by: int | None,
    *,
    label: str = "",
    uid: str = "",
    auth_kind: str = "oauth",
) -> int:
    extra = {
        "api_key": key,
        "region": region,
        "base_url": base_for_region(region),
        "account_id": uid,
        "auth_kind": auth_kind,
    }
    name = (label or "").strip()[:80] or f"Xiaomi ({region})"
    existing = _find_key(source_id, key)
    if existing and not existing["disabled"] and int(existing["donated_by"] or 0) != int(donated_by or 0):
        # An active key stays with its donor; another login never takes it over.
        raise ValueError("pooled")
    if existing:
        db.execute(
            """UPDATE credentials SET disabled=0, donated_by=?, label=?, secret=?, extra=? WHERE id=?""",
            (donated_by, name, encrypt(key), encrypt(json.dumps(extra)), existing["id"]),
        )
        return int(existing["id"])
    return accounts.add_credential(source_id, name, key, extra=extra, donated_by=donated_by)


def store_login(
    source_id: int,
    data: dict,
    donated_by: int | None,
    *,
    label: str = "",
) -> int:
    key, region, uid = _normalize_login(data)
    return _save_key(source_id, key, region, donated_by, label=label, uid=uid, auth_kind="oauth")


def _key_shape(raw: str) -> str:
    key = (raw or "").strip()
    if not key or len(key) < 8 or len(key) > 4096 or any(ord(ch) < 32 or ord(ch) > 126 for ch in key):
        return ""
    if key.startswith(("tp-", "ttp-")):
        return key
    return ""


async def add_plan_key(
    source_id: int,
    raw: str,
    donated_by: int,
    *,
    label: str = "",
    region: str = "",
) -> tuple[int | None, str]:
    """Paste the Token Plan key from the console (tp- / ttp-). Catalog only — no completion."""
    key = _key_shape(raw)
    if not key:
        return None, "Enter a Xiaomi Token Plan key (tp-… or ttp-…)."
    chosen = region if region in _REGIONS else ""
    order = [chosen] if chosen else list(_REGIONS)
    parsed = None
    last = "Could not reach Xiaomi."
    for candidate in order:
        parsed, err = await fetch_quota(key, candidate)
        if parsed is not None:
            chosen = candidate
            break
        last = "Invalid API key." if err in ("http 401", "http 403") else err
        if chosen:
            break
    if parsed is None or not chosen:
        if last in ("http 401", "http 403", "Invalid API key."):
            return None, "Xiaomi rejected that key. Copy it from Token Plan → Plan Overview."
        return None, "Could not validate this key. Check the connection and try again."
    try:
        cred_id = _save_key(source_id, key, chosen, donated_by, label=label, auth_kind="api")
    except ValueError:
        return None, "That key is already in the pool."
    row = db.fetchone("SELECT * FROM credentials WHERE id=?", (cred_id,))
    if row:
        _store_billing(cred_id, parsed, region=chosen)
    return cred_id, ""


def parse_quota_payload(data: dict) -> dict:
    """Best-effort credit snapshot. Token Plan has no documented quota route,
    so a catalog listing is enough to mark the key live."""
    root = data.get("data") if isinstance(data.get("data"), dict) else data
    if not isinstance(root, dict):
        root = {}
    used = _as_float(root.get("used") if "used" in root else root.get("usedCredits"))
    total = _as_float(root.get("total") if "total" in root else root.get("totalCredits"))
    remain = _as_float(root.get("remaining") if "remaining" in root else root.get("remainCredits"))
    pct = _as_float(root.get("percent") if "percent" in root else root.get("usage_percent"))
    if pct is None and used is not None and total:
        pct = max(0.0, min(100.0, used / total * 100.0))
    elif pct is None and remain is not None and total:
        pct = max(0.0, min(100.0, (1.0 - remain / total) * 100.0))
    plan = str(root.get("plan") or root.get("planName") or root.get("level") or "")[:32]
    windows = []
    if pct is not None:
        windows.append({"name": "Plan", "percent": pct, "seconds": 30 * 86400, "reset_at": ""})
    return {
        "credit_usage_percent": pct,
        "period_type": plan or "Token Plan",
        "period_start": "",
        "period_end": "",
        "products": [{"product": "Token Plan", "usage_percent": pct}] if pct is not None else [],
        "windows": windows,
        "plan_type": plan.lower(),
        "credits_used": used,
        "credits_total": total,
        "credits_remaining": remain,
    }


async def fetch_quota(key: str, region: str) -> tuple[dict | None, str]:
    """Catalog probe stands in for quota. A 200 listing means the plan key is live."""
    base = base_for_region(region)
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=False) as client:
            r = await client.get(base + "/models", headers=_chat_headers(key))
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
    names = [m.get("id") for m in (data.get("data") or []) if isinstance(m, dict) and m.get("id")]
    if not names:
        return None, "no models"
    parsed = parse_quota_payload(data)
    parsed["plan_type"] = parsed.get("plan_type") or "token plan"
    parsed["_models"] = [n for n in names if isinstance(n, str)]
    return parsed, ""


def _store_billing(cred_id: int, parsed: dict, *, error: str = "", region: str | None = None) -> dict:
    row = db.fetchone("SELECT * FROM credentials WHERE id=?", (cred_id,))
    extra = accounts.cred_extra(row) if row else {}
    billing = extra.get("billing") if isinstance(extra.get("billing"), dict) else {}
    now = db.now()
    billing["checked_at"] = now
    if region in _REGIONS:
        extra["region"] = region
        extra["base_url"] = base_for_region(region)
    if error:
        billing["last_error"] = error[:200]
        extra["billing"] = billing
        if row:
            accounts.update_extra(cred_id, extra)
        return billing
    names = parsed.get("_models") if isinstance(parsed.get("_models"), list) else None
    clean = {k: v for k, v in parsed.items() if not str(k).startswith("_")}
    billing.update(clean)
    billing["fetched_at"] = now
    billing["last_error"] = ""
    extra["billing"] = billing
    if names:
        extra["models"] = [n for n in names if isinstance(n, str)][:80]
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


async def refresh_quota(cred, *, force: bool = False) -> dict | None:
    async with _refresh_lock(int(cred["id"])):
        row = db.fetchone("SELECT * FROM credentials WHERE id=?", (cred["id"],))
        if not row or row["disabled"]:
            return None
        extra = accounts.cred_extra(row)
        billing = extra.get("billing") if isinstance(extra.get("billing"), dict) else {}
        if not force and _usage_fresh(billing):
            return billing
        key = extra.get("api_key") or accounts.cred_secret(row)
        if not key:
            return _store_billing(row["id"], {}, error="missing key")
        region = extra.get("region") if extra.get("region") in _REGIONS else "cn"
        parsed, err = await fetch_quota(key, region)
        if parsed is None:
            return _store_billing(row["id"], {}, error=err or "fetch failed", region=region)
        return _store_billing(row["id"], parsed, region=region)


async def refresh_pool_quota(*, force: bool = False) -> None:
    rows = db.fetchall(
        "SELECT c.* FROM credentials c JOIN sources s ON s.id=c.source_id WHERE s.type='mimo' AND c.disabled=0"
    )
    if not rows:
        return
    results = await asyncio.gather(*(refresh_quota(c, force=force) for c in rows), return_exceptions=True)
    log_refresh_failures("MiMo quota refresh", rows, results)


def _remember_models(names: list[str]) -> list[str]:
    global _models_cache
    fresh = [
        n for n in names
        if isinstance(n, str) and n and not n.startswith("mimo-v2.5-tts") and "asr" not in n
    ]
    if not fresh and _models_cache and _models_cache[1]:
        return list(_models_cache[1])
    base = list(_models_cache[1]) if _models_cache and _models_cache[1] else list(MIMO_MODELS)
    merged = []
    # Upstream order first (new models sit where the provider puts them),
    # then ids an earlier catalog listed and this one omits.
    for name in fresh + base:
        if name not in merged:
            merged.append(name)
    if merged:
        _models_cache = (time.time(), merged)
    return merged or list(MIMO_MODELS)


async def fetch_catalog(key: str, region: str) -> list[str]:
    now = time.time()
    if _models_cache and now - _models_cache[0] < 60:
        return list(_models_cache[1])
    parsed, _err = await fetch_quota(key, region)
    names = parsed.get("_models") if isinstance(parsed, dict) else None
    return _remember_models(list(names or []))


def _chat_payload(body: dict) -> dict:
    payload = drop_cache_routing_fields(body)
    reasoning = payload.get("reasoning")
    effort = payload.get("reasoning_effort")
    if effort is None and isinstance(reasoning, dict):
        effort = reasoning.get("effort")
    if "thinking" not in payload and isinstance(effort, str):
        payload["thinking"] = {"type": "disabled" if effort.strip().lower() in ("none", "off", "disabled") else "enabled"}
    payload.pop("reasoning_effort", None)
    payload.pop("reasoning", None)
    return payload


class MimoSource:
    type_id = "mimo"

    def __init__(self, row, cfg: dict, creds):
        self.source_id = row["id"]
        self.name = row["name"]
        self.cfg = cfg
        self.creds = list(creds)

    def _pick(self, exclude: set[int] | None = None, prefer_id: int | None = None, owner_id: int | None = None, hint_id: int | None = None):
        now = datetime.now(timezone.utc)
        return pick_account(
            self.creds,
            kind="mimo",
            exclude=exclude,
            is_held=lambda c: is_held(c, now),
            is_authed=credential_is_authed,
            prefer_id=prefer_id,
            owner_id=owner_id,
            hint_id=hint_id,
        )

    async def models(self) -> list[str]:
        picked = self._pick()
        if picked:
            cred, extra = picked
            key = extra.get("api_key") or accounts.cred_secret(cred)
            region = extra.get("region") if extra.get("region") in _REGIONS else "cn"
            names = await fetch_catalog(key, region)
            if names:
                return names
        if _models_cache and _models_cache[1]:
            return list(_models_cache[1])
        return list(MIMO_MODELS)

    async def chat(self, body: dict, *, session_id: str | None = None, user_id: int | None = None, affinity_id: str | None = None) -> ChatResult:
        await refresh_pool_quota()
        self.creds = pool_policy.filter_credentials(
            db.fetchall("SELECT * FROM credentials WHERE source_id=? AND disabled=0", (self.source_id,)),
            "mimo",
            user_id,
        )
        tried: set[int] = set()
        attempts: list[dict] = []
        last = ChatResult(status=503, body={"error": "No Xiaomi accounts in the pool"})
        model = body.get("model") or MIMO_MODELS[0]
        payload = _chat_payload(body)
        prefer_id = lookup("mimo", session_id)
        hint_id = lookup("mimo", affinity_id) if prefer_id is None and affinity_id else None
        failover = Failover()
        while True:
            picked = self._pick(exclude=tried, prefer_id=prefer_id, owner_id=user_id, hint_id=hint_id)
            if not picked:
                if attempts and last.credential_id == attempts[-1].get("credential_id"):
                    attempts.pop()
                last.attempts = attempts
                return last
            cred, extra = picked
            tried.add(int(cred["id"]))
            note_pool_invoke(cred["id"])
            key = extra.get("api_key") or accounts.cred_secret(cred)
            region = extra.get("region") if extra.get("region") in _REGIONS else "cn"
            base = extra.get("base_url") or base_for_region(region)
            client = httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=15.0))
            stream_reserved = False
            try:
                reserve(cred["id"])
                status, obj, stream, usage, response_headers = await openai_chat(
                    client,
                    base.rstrip("/") + "/chat/completions",
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
                    last.body = {"error": {"message": "Xiaomi rejected the Token Plan key"}}
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
                    bind("mimo", session_id, cred["id"])
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
                    bind("mimo", session_id, cid)
                elif status == "failed":
                    note_failure(cid, cooldown_seconds=5, affinity_safe=True)

        wrapped = UsageStream(_bind_on_finish(), collected, on_close=lambda: release(cid))
        result = ChatResult(status=200, stream=wrapped, credential_id=cid, model=model)
        result.attempts = attempts
        return result
