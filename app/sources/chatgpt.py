from __future__ import annotations

import asyncio
import base64
import json
import logging
import math
import time
from datetime import datetime, timedelta, timezone

import httpx

from .. import accounts, db
from ..config import settings
from ..crypto import encrypt
from ..proxy import UsageStream, usage_from_obj
from .base import ChatResult
from .pool_pick import Failover, note_failure, pick_account, release, reserve
from . import pool_policy
from .pool_session import bind, lookup
from .token_store import expires_at, persist_rotated

logger = logging.getLogger(__name__)
REFRESH_WINDOW = timedelta(minutes=5)
_models_cache: tuple[float, list[str]] | None = None
_refresh_locks: dict[int, asyncio.Lock] = {}

CHATGPT_MODELS = [
    "gpt-6-astra",
    "gpt-6-sol",
    "gpt-6-luna",
    "gpt-5.5",
    "gpt-5.4",
    "gpt-5.4-mini",
    "gpt-5.6-sol",
    "gpt-5.6-terra",
    "gpt-5.6-luna",
]


def _auth_base() -> str:
    return settings.openai_auth_base.rstrip("/")


def _backend_base() -> str:
    return settings.openai_chatgpt_base.rstrip("/")


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=15.0))


def _auth_client(timeout: float = 20) -> httpx.AsyncClient:
    """Client for auth.openai.com. Chat/usage stay on `_client()` (no proxy)."""
    proxy = (settings.openai_auth_proxy or "").strip()
    if proxy:
        return httpx.AsyncClient(timeout=timeout, proxy=proxy)
    return httpx.AsyncClient(timeout=timeout)


def _auth_headers() -> dict:
    return {
        "Content-Type": "application/json",
        "User-Agent": settings.openai_auth_user_agent,
    }


def _chat_headers(token: str, account_id: str | None) -> dict:
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "User-Agent": settings.openai_auth_user_agent,
    }
    if account_id:
        headers["ChatGPT-Account-Id"] = account_id
    return headers


def ensure_chatgpt_source() -> int:
    row = db.fetchone("SELECT id FROM sources WHERE type='chatgpt'")
    if row:
        return row["id"]
    with db.tx() as conn:
        conn.execute(
            "INSERT INTO sources (type, name, enabled, config, created_at) VALUES (?,?,?,?,?)",
            ("chatgpt", "ChatGPT", 1, "{}", db.now()),
        )
        return db.last_id(conn)


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


def _refresh_lock(cred_id: int) -> asyncio.Lock:
    lock = _refresh_locks.get(cred_id)
    if lock is None:
        lock = asyncio.Lock()
        _refresh_locks[cred_id] = lock
    return lock


def _jwt_claims(token: str) -> dict | None:
    try:
        part = token.split(".")[1]
        part += "=" * (-len(part) % 4)
        claims = json.loads(base64.urlsafe_b64decode(part))
        return claims if isinstance(claims, dict) else None
    except Exception:
        return None


def account_from_tokens(tokens: dict) -> str | None:
    for key in ("id_token", "access_token"):
        claims = _jwt_claims(tokens.get(key) or "")
        if not claims:
            continue
        auth = claims.get("https://api.openai.com/auth")
        auth = auth if isinstance(auth, dict) else {}
        acct = claims.get("chatgpt_account_id") or auth.get("chatgpt_account_id")
        if not acct:
            orgs = claims.get("organizations")
            if isinstance(orgs, list) and orgs and isinstance(orgs[0], dict):
                acct = orgs[0].get("id")
        if acct:
            return str(acct)
    return None


def _clean_email(value) -> str:
    text = str(value or "").strip()
    if not text or len(text) > 254 or " " in text or text.count("@") != 1:
        return ""
    return text


def profile_from_tokens(tokens: dict | None = None, extra: dict | None = None) -> dict:
    """OpenAI access tokens carry email/name on https://api.openai.com/profile."""
    blobs: list[str] = []
    for src in (tokens, extra):
        if not isinstance(src, dict):
            continue
        for key in ("id_token", "access_token"):
            val = src.get(key)
            if isinstance(val, str) and val:
                blobs.append(val)
    for tok in blobs:
        claims = _jwt_claims(tok)
        if not claims:
            continue
        profile = claims.get("https://api.openai.com/profile")
        if not isinstance(profile, dict):
            continue
        email = _clean_email(profile.get("email"))
        name = str(profile.get("name") or "").strip()[:80]
        if email or name:
            return {"email": email, "name": name}
    return {}


def apply_profile(extra: dict, tokens: dict | None = None) -> dict:
    profile = profile_from_tokens(tokens=tokens, extra=extra)
    if profile.get("email"):
        extra["email"] = profile["email"]
    if profile.get("name"):
        extra["name"] = profile["name"]
    return extra


def account_email(extra: dict | None) -> str:
    extra = extra if isinstance(extra, dict) else {}
    stored = _clean_email(extra.get("email"))
    if stored:
        return stored
    return str(profile_from_tokens(extra=extra).get("email") or "")


def _billing_auth_failed(extra: dict | None) -> bool:
    """True when the usage probe already failed 401/403 after a token refresh.

    Those accounts still report 0% usage, so pick_account would otherwise
    send every Luna/Terra turn at them first. Chat on the same access token
    fails the same way.
    """
    billing = extra.get("billing") if isinstance(extra, dict) and isinstance(extra.get("billing"), dict) else {}
    err = str(billing.get("last_error") or "").strip().lower()
    return err.startswith("http 401") or err.startswith("http 403")


def credential_is_authed(cred, extra: dict | None = None) -> bool:
    if cred["disabled"]:
        return False
    extra = extra if extra is not None else accounts.cred_extra(cred)
    if not extra.get("refresh_token"):
        return False
    return not _billing_auth_failed(extra)


def chatgpt_own_access(user_id: int) -> dict:
    """Access from this user's own donated ChatGPT accounts only."""
    sid = ensure_chatgpt_source()
    rows = db.fetchall(
        "SELECT * FROM credentials WHERE source_id=? AND donated_by=? AND disabled=0",
        (sid, user_id),
    )
    if not rows:
        return {"ok": False, "reason": "none"}
    for row in rows:
        if credential_is_authed(row):
            return {"ok": True, "reason": "ok"}
    return {"ok": False, "reason": "unauthed"}


def _chatgpt_pool_ready() -> bool:
    sid = ensure_chatgpt_source()
    rows = db.fetchall(
        "SELECT * FROM credentials WHERE source_id=? AND disabled=0",
        (sid,),
    )
    return any(credential_is_authed(row) for row in rows)


def chatgpt_access_state(user_id: int) -> dict:
    return pool_policy.with_open_access(
        user_id, "chatgpt", chatgpt_own_access(user_id), _chatgpt_pool_ready()
    )


def user_has_chatgpt(user_id: int) -> bool:
    return chatgpt_access_state(user_id)["ok"]


def _note_invoke(cred_id: int) -> None:
    db.execute("UPDATE credentials SET pool_invokes = COALESCE(pool_invokes, 0) + 1 WHERE id=?", (cred_id,))


async def _error_body(resp: httpx.Response) -> dict:
    """Read a streamed error response. httpx.json() raises ResponseNotRead
    until the body has been pulled."""
    try:
        await resp.aread()
    except Exception:
        return {"error": {"message": f"upstream {resp.status_code}"}}
    text = resp.text or ""
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        return {"error": {"message": text[:500]}}
    if isinstance(obj, dict):
        return obj
    return {"error": {"message": str(obj)[:500]}}


def _unsupported_param(obj) -> str | None:
    """Codex /responses 400: {'detail': 'Unsupported parameter: max_output_tokens'}."""
    if not isinstance(obj, dict):
        return None
    texts: list[str] = []
    detail = obj.get("detail")
    if isinstance(detail, str):
        texts.append(detail)
    err = obj.get("error")
    if isinstance(err, dict):
        param = err.get("param")
        if isinstance(param, str) and param.strip():
            return param.strip()
        for key in ("message", "msg"):
            val = err.get(key)
            if isinstance(val, str):
                texts.append(val)
    elif isinstance(err, str):
        texts.append(err)
    for text in texts:
        lower = text.lower()
        if "unsupported parameter:" in lower:
            tail = text.split(":", 1)[1].strip()
            name = tail.split()[0].strip(".,;\"'`") if tail else ""
            return name or None
        marker = " is not supported"
        idx = lower.find(marker)
        if idx > 0:
            name = text[:idx].strip().split()[-1].strip(".,;\"'`")
            return name or None
    return None


async def start_device() -> dict:
    async with _auth_client() as client:
        r = await client.post(
            _auth_base() + "/api/accounts/deviceauth/usercode",
            json={"client_id": settings.openai_auth_client_id},
            headers=_auth_headers(),
        )
        r.raise_for_status()
        return r.json()


async def poll_device(device_auth_id: str, user_code: str) -> dict:
    async with _auth_client() as client:
        r = await client.post(
            _auth_base() + "/api/accounts/deviceauth/token",
            json={"device_auth_id": device_auth_id, "user_code": user_code},
            headers=_auth_headers(),
        )
    if r.status_code in (403, 404):
        return {"status": "pending"}
    if r.status_code != 200:
        return {"status": "failed", "error": f"device authorization failed: {r.status_code}"}
    try:
        data = r.json()
    except json.JSONDecodeError:
        return {"status": "failed", "error": "device authorization returned invalid JSON"}
    code = data.get("authorization_code")
    verifier = data.get("code_verifier")
    if not code or not verifier:
        return {"status": "failed", "error": "device authorization missing code"}
    tokens = await exchange_code(code, verifier)
    if not tokens or "access_token" not in tokens:
        return {"status": "failed", "error": "token exchange failed"}
    return {"status": "ok", "tokens": tokens}


async def exchange_code(code: str, verifier: str) -> dict:
    async with _auth_client() as client:
        r = await client.post(
            _auth_base() + "/oauth/token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": _auth_base() + "/deviceauth/callback",
                "client_id": settings.openai_auth_client_id,
                "code_verifier": verifier,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded", "User-Agent": settings.openai_auth_user_agent},
        )
    if r.status_code != 200:
        return {}
    try:
        return r.json()
    except json.JSONDecodeError:
        return {}


def store_tokens(source_id: int, tokens: dict, donated_by: int | None, label: str = "") -> int:
    extra = {
        "access_token": tokens["access_token"],
        "refresh_token": tokens.get("refresh_token", ""),
        "expires_at": expires_at(tokens.get("expires_in"), 3600),
        "account_id": account_from_tokens(tokens) or "",
    }
    apply_profile(extra, tokens)
    return accounts.add_credential(
        source_id,
        label or extra.get("email") or "ChatGPT account",
        tokens["access_token"],
        extra=extra,
        donated_by=donated_by,
    )


def _cas_update_tokens(cred_id: int, old_extra_raw: str, new_secret: str, new_extra: dict) -> bool:
    """Update tokens only if `extra` is unchanged since it was read, so a
    concurrent writer between read and commit cannot be silently clobbered."""
    with db.tx() as conn:
        cur = conn.execute(
            "UPDATE credentials SET secret=?, extra=? WHERE id=? AND extra=?",
            (new_secret, encrypt(json.dumps(new_extra)), cred_id, old_extra_raw),
        )
        return cur.rowcount > 0


async def _refresh_one_unlocked(cred) -> bool:
    row = db.fetchone("SELECT * FROM credentials WHERE id=?", (cred["id"],))
    if not row or row["disabled"]:
        return False
    extra = accounts.cred_extra(row)
    rt = extra.get("refresh_token")
    if not rt:
        return False
    try:
        async with _auth_client() as client:
            r = await client.post(
                _auth_base() + "/oauth/token",
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": rt,
                    "client_id": settings.openai_auth_client_id,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded", "User-Agent": settings.openai_auth_user_agent},
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
        if _oauth_revoked(r.status_code, data):
            disable_credential(row["id"])
        else:
            note_failure(row["id"], cooldown_seconds=60 if r.status_code == 429 else 30)
        return False
    extra["access_token"] = data["access_token"]
    extra["refresh_token"] = data.get("refresh_token", rt)
    extra["expires_at"] = expires_at(data.get("expires_in"), 3600)
    account = account_from_tokens(data) or extra.get("account_id") or ""
    extra["account_id"] = account
    apply_profile(extra, data)
    # `extra` may change while the token endpoint is in flight (re-link or
    # another refresh); persist_rotated re-merges onto the newest copy.
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
            "account_id": account,
        },
        merge=lambda merged: apply_profile(merged, data),
    )


async def refresh_one(cred) -> bool:
    async with _refresh_lock(int(cred["id"])):
        return await _refresh_one_unlocked(cred)


async def refresh_due() -> None:
    rows = db.fetchall(
        "SELECT c.* FROM credentials c JOIN sources s ON s.id=c.source_id WHERE s.type='chatgpt' AND c.disabled=0"
    )
    soon = datetime.now(timezone.utc) + REFRESH_WINDOW
    due = []
    for cred in rows:
        extra = accounts.cred_extra(cred)
        exp = extra.get("expires_at")
        if not exp:
            continue
        try:
            when = datetime.strptime(exp.replace("Z", ""), "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if when <= soon:
            due.append(cred)
    if due:
        await asyncio.gather(*(refresh_one(c) for c in due), return_exceptions=True)


USAGE_TTL = 300


def _usage_url() -> str:
    return settings.openai_chatgpt_base.rsplit("/", 1)[0] + "/wham/usage"


def _models_url() -> str:
    # Codex's own catalog. The ChatGPT web `/backend-api/models` answers 403
    # to a Codex OAuth token, which left the pool on its seed list.
    return _backend_base() + "/models"


def parse_catalog_payload(data) -> list[str]:
    """Listed Codex slugs. `visibility: hide` rows (auto-review, reserve) are
    internal and not callable as chat models."""
    rows = (data.get("models") or data.get("data")) if isinstance(data, dict) else None
    if not isinstance(rows, list):
        return []
    names = []
    for row in rows:
        if not isinstance(row, dict) or str(row.get("visibility") or "list") == "hide":
            continue
        slug = str(row.get("slug") or row.get("id") or "")
        if slug and slug not in names:
            names.append(slug)
    return names


def is_chatgpt_model(model: str) -> bool:
    """Seeded or live-listed Codex slug."""
    if model in CHATGPT_MODELS:
        return True
    return bool(_models_cache and model in _models_cache[1])


def _remember_models(names: list[str]) -> list[str]:
    """Seed plus anything Codex lists. A later fetch only adds ids, so a short
    or failed catalog cannot drop a model the picker already showed."""
    global _models_cache
    fresh = [n for n in names if isinstance(n, str) and n.startswith("gpt-")]
    if not fresh and _models_cache and _models_cache[1]:
        return list(_models_cache[1])
    base = list(_models_cache[1]) if _models_cache and _models_cache[1] else list(CHATGPT_MODELS)
    merged: list[str] = []
    for name in base + fresh:
        if name not in merged:
            merged.append(name)
    if merged:
        _models_cache = (time.time(), merged)
    return merged or list(CHATGPT_MODELS)


async def fetch_catalog(token: str, account_id: str | None = None) -> list[str]:
    now = time.time()
    if _models_cache and now - _models_cache[0] < 300:
        return list(_models_cache[1])
    names: list[str] = []
    if token:
        try:
            async with _client() as client:
                r = await client.get(
                    _models_url(),
                    params={"client_version": settings.openai_codex_client_version},
                    headers=_chat_headers(token, account_id),
                )
            if r.status_code == 200:
                names = parse_catalog_payload(r.json())
            else:
                logger.warning("ChatGPT model catalog returned HTTP %s", r.status_code)
        except (httpx.HTTPError, json.JSONDecodeError, ValueError, AttributeError):
            names = []
    return _remember_models(names)


def _window_name(seconds) -> str:
    try:
        secs = int(seconds)
    except (TypeError, ValueError):
        return "Usage"
    if secs <= 6 * 3600:
        return "5-hour"
    if secs <= 2 * 86400:
        return "Daily"
    if secs <= 8 * 86400:
        return "Weekly"
    return "Monthly"


def _unix_iso(value) -> str:
    try:
        return datetime.fromtimestamp(int(value), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (TypeError, ValueError, OSError, OverflowError):
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


def parse_usage_payload(data: dict) -> dict:
    rate = data.get("rate_limit") if isinstance(data.get("rate_limit"), dict) else {}
    windows = []
    for key in ("primary_window", "secondary_window"):
        win = rate.get(key)
        if not isinstance(win, dict):
            continue
        try:
            value = float(win["used_percent"]) if win.get("used_percent") is not None else None
            pct_v = value if value is None or math.isfinite(value) else None
        except (TypeError, ValueError, OverflowError):
            pct_v = None
        try:
            secs = int(win["limit_window_seconds"]) if win.get("limit_window_seconds") is not None else None
        except (TypeError, ValueError):
            secs = None
        name = _window_name(secs)
        windows.append(
            {
                "product": name,
                "usage_percent": pct_v,
                "percent": pct_v,
                "window_seconds": secs or 0,
                "seconds": secs or 0,
                "reset_at": _unix_iso(win.get("reset_at")),
            }
        )
    hero = max(windows, key=lambda w: w["window_seconds"]) if windows else None
    period = (hero["product"] if hero else "") or "Usage"
    return {
        "credit_usage_percent": hero["usage_percent"] if hero else 0.0,
        "period_type": period,
        "period_end": (hero or {}).get("reset_at") or "",
        "products": [
            {"product": w["product"], "usage_percent": w["usage_percent"]}
            for w in windows
            if w.get("usage_percent") is not None
        ]
        if len(windows) > 1
        else [],
        "windows": [
            {
                "name": w["product"],
                "percent": w["percent"],
                "seconds": w["seconds"],
            }
            for w in windows
        ],
        "plan_type": str(data.get("plan_type") or "")[:32],
    }


def _store_usage(cred_id: int, parsed: dict, *, error: str = "") -> dict:
    row = db.fetchone("SELECT * FROM credentials WHERE id=?", (cred_id,))
    extra = accounts.cred_extra(row) if row else {}
    billing = extra.get("billing") if isinstance(extra.get("billing"), dict) else {}
    now = db.now()
    billing["checked_at"] = now
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
    accounts.update_extra(cred_id, extra)
    return billing


async def refresh_usage(cred, *, force: bool = False) -> dict | None:
    async with _refresh_lock(int(cred["id"])):
        row = db.fetchone("SELECT * FROM credentials WHERE id=?", (cred["id"],))
        if not row or row["disabled"]:
            return None
        extra = accounts.cred_extra(row)
        billing = extra.get("billing") if isinstance(extra.get("billing"), dict) else {}
        if not force and _usage_fresh(billing) and billing.get("windows") is not None:
            return billing
        token = extra.get("access_token") or accounts.cred_secret(row)
        account_id = extra.get("account_id") or None
        status = 0
        payload: dict | None = None
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                r = await client.get(_usage_url(), headers=_chat_headers(token, account_id))
                status = r.status_code
                if status == 401:
                    if await _refresh_one_unlocked(row):
                        row = db.fetchone("SELECT * FROM credentials WHERE id=?", (cred["id"],))
                        extra = accounts.cred_extra(row)
                        token = extra.get("access_token") or accounts.cred_secret(row)
                        account_id = extra.get("account_id") or None
                        r = await client.get(_usage_url(), headers=_chat_headers(token, account_id))
                        status = r.status_code
                if status == 200:
                    try:
                        payload = r.json()
                    except json.JSONDecodeError:
                        payload = None
        except Exception:
            return _store_usage(row["id"], {}, error="fetch failed")
        if status != 200 or not isinstance(payload, dict):
            return _store_usage(row["id"], {}, error=f"http {status or 0}")
        return _store_usage(row["id"], parse_usage_payload(payload))


async def refresh_pool_usage(*, force: bool = False) -> None:
    await refresh_due()
    rows = db.fetchall(
        "SELECT c.* FROM credentials c JOIN sources s ON s.id=c.source_id WHERE s.type='chatgpt' AND c.disabled=0"
    )
    if not rows:
        return
    await asyncio.gather(*(refresh_usage(c, force=force) for c in rows), return_exceptions=True)


def _text_of(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for p in content:
            if isinstance(p, dict) and p.get("type") == "text":
                parts.append(str(p.get("text") or ""))
        return "\n".join(x for x in parts if x)
    return ""


def _input_content(content) -> list:
    if isinstance(content, str):
        return [{"type": "input_text", "text": content}]
    out = []
    if isinstance(content, list):
        for p in content:
            if not isinstance(p, dict):
                continue
            kind = p.get("type")
            if kind == "text":
                out.append({"type": "input_text", "text": str(p.get("text") or "")})
            elif kind == "image_url":
                url_obj = p.get("image_url")
                url = url_obj.get("url") if isinstance(url_obj, dict) else url_obj
                if url:
                    image = {"type": "input_image", "image_url": str(url)}
                    if isinstance(url_obj, dict) and isinstance(url_obj.get("detail"), str):
                        image["detail"] = url_obj["detail"]
                    out.append(image)
            elif kind in ("file", "input_file"):
                file = p.get("file")
                file = file if isinstance(file, dict) else p
                item = {"type": "input_file"}
                for key in ("file_id", "file_data", "filename"):
                    if isinstance(file.get(key), str) and file[key]:
                        item[key] = file[key]
                if "file_id" in item or "file_data" in item:
                    out.append(item)
    return out or [{"type": "input_text", "text": ""}]


def build_responses_payload(body: dict, model: str, session_id: str | None = None) -> dict:
    instructions: list[str] = []
    input_items: list[dict] = []
    for msg in body.get("messages") or []:
        if not isinstance(msg, dict):
            continue
        role = msg.get("role")
        content = msg.get("content")
        if role in ("system", "developer"):
            text = _text_of(content)
            if text:
                instructions.append(text)
            continue
        if role == "user":
            input_items.append({"role": "user", "content": _input_content(content)})
        elif role == "assistant":
            text = _text_of(content)
            if text:
                input_items.append(
                    {"role": "assistant", "content": [{"type": "output_text", "text": text}]}
                )
            for tc in msg.get("tool_calls") or []:
                if not isinstance(tc, dict):
                    continue
                fn = tc.get("function") if isinstance(tc.get("function"), dict) else {}
                input_items.append(
                    {
                        "type": "function_call",
                        "call_id": str(tc.get("id") or fn.get("name") or ""),
                        "name": str(fn.get("name") or ""),
                        "arguments": str(fn.get("arguments") or "{}"),
                    }
                )
        elif role == "tool":
            input_items.append(
                {
                    "type": "function_call_output",
                    "call_id": str(msg.get("tool_call_id") or ""),
                    "output": _text_of(content),
                }
            )
    payload: dict = {"model": model, "input": input_items, "stream": True, "store": False}
    # Always the scoped session id (hmac of user+model+client key), never the
    # raw prompt_cache_key. Two tenants sending the same client string must
    # not share an upstream routing group on a pooled account.
    if session_id:
        payload["prompt_cache_key"] = str(session_id)[:256]
    if instructions:
        payload["instructions"] = "\n\n".join(instructions)
    tools = body.get("tools")
    if isinstance(tools, list) and tools:
        mapped = []
        for t in tools:
            if not isinstance(t, dict) or t.get("type") != "function":
                continue
            fn = t.get("function") if isinstance(t.get("function"), dict) else t
            if not fn.get("name"):
                continue
            mapped.append(
                {
                    "type": "function",
                    "name": str(fn.get("name")),
                    "description": str(fn.get("description") or ""),
                    "parameters": fn.get("parameters") or {"type": "object", "properties": {}},
                }
            )
        if mapped:
            payload["tools"] = mapped
    choice = body.get("tool_choice")
    if isinstance(choice, str) and choice in ("none", "auto", "required"):
        payload["tool_choice"] = choice
    elif isinstance(choice, dict) and choice.get("type") == "function":
        fn = choice.get("function") if isinstance(choice.get("function"), dict) else {}
        if fn.get("name"):
            payload["tool_choice"] = {"type": "function", "name": str(fn["name"])}
    if body.get("parallel_tool_calls") is not None:
        payload["parallel_tool_calls"] = bool(body.get("parallel_tool_calls"))
    # Codex /responses now rejects Chat Completions sampling and cache-lifetime
    # fields (400 Unsupported parameter: max_output_tokens / temperature / top_p
    # / prompt_cache_retention / prompt_cache_options), including on GPT-5.6.
    # prompt_cache_key and reasoning.effort still work.
    reasoning = body.get("reasoning")
    if isinstance(reasoning, dict):
        payload["reasoning"] = dict(reasoning)
    effort = body.get("reasoning_effort")
    if isinstance(effort, str):
        payload.setdefault("reasoning", {})["effort"] = effort
        payload["reasoning"].setdefault("summary", "auto")
    summary = body.get("reasoning_summary")
    if summary is None:
        summary = body.get("reasoningSummary")
    if isinstance(summary, str):
        payload.setdefault("reasoning", {})["summary"] = summary
    return payload


def _usage_from_response(u: dict) -> dict:
    return usage_from_obj({"usage": u}) if isinstance(u, dict) else {}


class UpstreamProtocolError(ValueError):
    pass


async def _upstream_events(resp: httpx.Response):
    buf = ""

    def parse(line: str):
        text = line.strip()
        if not text.startswith("data:"):
            return None
        data = text[5:].strip()
        if not data or data == "[DONE]":
            return None
        try:
            obj = json.loads(data)
        except json.JSONDecodeError as exc:
            raise UpstreamProtocolError("upstream sent malformed terminal JSON") from exc
        if not isinstance(obj, dict):
            raise UpstreamProtocolError("upstream sent an invalid event object")
        return obj

    async for chunk in resp.aiter_text():
        buf += chunk
        while "\n" in buf:
            line, buf = buf.split("\n", 1)
            obj = parse(line)
            if obj is not None:
                yield obj
    if buf.strip():
        obj = parse(buf)
        if obj is not None:
            yield obj


class _CallBook:
    def __init__(self):
        self.order: list[str] = []
        self.calls: dict[str, dict] = {}

    def add(self, item_id: str, call_id: str, name: str, arguments: str) -> dict:
        rec = {
            "index": len(self.order),
            "id": call_id,
            "name": name,
            "arguments": arguments,
        }
        self.calls[item_id] = rec
        self.order.append(item_id)
        return rec

    def get(self, item_id: str) -> dict | None:
        return self.calls.get(item_id)

    def tool_calls(self) -> list[dict]:
        return [
            {
                "id": self.calls[i]["id"],
                "type": "function",
                "function": {
                    "name": self.calls[i]["name"],
                    "arguments": self.calls[i]["arguments"],
                },
            }
            for i in self.order
            if self.calls[i]["name"]
        ]


def _sse(obj: dict) -> bytes:
    return f"data: {json.dumps(obj, separators=(',', ':'))}\n\n".encode()


class _Translator:
    def __init__(self, model: str):
        self.model = model
        self.created = int(time.time())
        self.completion_id = "chatcmpl-chatgpt"
        self.book = _CallBook()
        self.usage: dict = {}
        self.error: str = ""

    def _chunk(self, delta: dict | None = None, finish: str | None = None) -> bytes:
        obj = {
            "id": self.completion_id,
            "object": "chat.completion.chunk",
            "created": self.created,
            "model": self.model,
            "choices": [{"index": 0, "delta": delta or {}, "finish_reason": finish}],
        }
        return _sse(obj)

    def _error_chunk(self, message: str) -> bytes:
        obj = {
            "id": self.completion_id,
            "object": "chat.completion.chunk",
            "created": self.created,
            "model": self.model,
            "choices": [],
            "error": {"message": message, "type": "api_error"},
        }
        return _sse(obj)

    def _handle(self, event: dict) -> bytes | None:
        etype = event.get("type") or ""
        if etype == "response.created":
            response = event.get("response") if isinstance(event.get("response"), dict) else {}
            self.completion_id = "chatcmpl-" + str(response.get("id") or "chatgpt")
            return self._chunk({"role": "assistant", "content": ""})
        if etype == "response.output_text.delta":
            delta = event.get("delta")
            if delta:
                return self._chunk({"content": str(delta)})
            return None
        if etype in ("response.reasoning_summary_text.delta", "response.reasoning_text.delta"):
            delta = event.get("delta")
            if delta:
                return self._chunk({"reasoning_content": str(delta)})
            return None
        if etype == "response.output_item.added":
            item = event.get("item") if isinstance(event.get("item"), dict) else {}
            if item.get("type") == "function_call":
                item_id = str(item.get("id") or "")
                rec = self.book.add(
                    item_id,
                    str(item.get("call_id") or item.get("id") or f"call_{len(self.book.order)}"),
                    str(item.get("name") or ""),
                    str(item.get("arguments") or ""),
                )
                return self._chunk(
                    {
                        "tool_calls": [
                            {
                                "index": rec["index"],
                                "id": rec["id"],
                                "type": "function",
                                "function": {"name": rec["name"], "arguments": rec["arguments"]},
                            }
                        ]
                    }
                )
            return None
        if etype == "response.function_call_arguments.delta":
            rec = self.book.get(str(event.get("item_id") or ""))
            if rec and event.get("delta"):
                rec["arguments"] += str(event.get("delta"))
                return self._chunk(
                    {"tool_calls": [{"index": rec["index"], "function": {"arguments": str(event.get("delta"))}}]}
                )
            return None
        if etype == "response.function_call_arguments.done":
            rec = self.book.get(str(event.get("item_id") or ""))
            if rec and event.get("arguments") is not None:
                rec["arguments"] = str(event.get("arguments") or "")
            return None
        if etype == "response.completed":
            response = event.get("response") if isinstance(event.get("response"), dict) else {}
            if not response:
                self.error = "upstream terminal response is malformed"
                return b"__ERROR__"
            usage = response.get("usage") if isinstance(response.get("usage"), dict) else {}
            if usage:
                self.usage = _usage_from_response(usage)
            return b"__FINAL__"
        if etype in ("response.failed", "response.incomplete"):
            response = event.get("response") if isinstance(event.get("response"), dict) else {}
            usage = response.get("usage") if isinstance(response.get("usage"), dict) else {}
            if usage:
                self.usage = _usage_from_response(usage)
            err = response.get("error") if isinstance(response.get("error"), dict) else {}
            self.error = str(err.get("message") or ("upstream response incomplete" if etype == "response.incomplete" else "upstream response failed"))
            return b"__ERROR__"
        if etype == "error":
            err = event.get("error") if isinstance(event.get("error"), dict) else {}
            self.error = str(err.get("message") or event.get("message") or "upstream error")
            return b"__ERROR__"
        return None

    def _final_chunks(self) -> list[bytes]:
        finish = "tool_calls" if self.book.order else "stop"
        out = [self._chunk({}, finish)]
        if self.usage:
            obj = {
                "id": self.completion_id,
                "object": "chat.completion.chunk",
                "created": self.created,
                "model": self.model,
                "choices": [],
                "usage": {
                    "prompt_tokens": self.usage["prompt_tokens"],
                    "completion_tokens": self.usage["completion_tokens"],
                    "total_tokens": self.usage["total_tokens"],
                    "prompt_tokens_details": {
                        "cached_tokens": self.usage.get("cached_tokens") or 0,
                        "cache_write_tokens": self.usage.get("cache_write_tokens") or 0,
                    },
                    "completion_tokens_details": {
                        "reasoning_tokens": self.usage.get("reasoning_tokens") or 0,
                    },
                },
            }
            out.append(_sse(obj))
        out.append(b"data: [DONE]\n\n")
        return out


async def _translated_stream(
    resp: httpx.Response, client: httpx.AsyncClient, model: str, collected: dict | None = None
):
    translator = _Translator(model)
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


async def _aggregate(resp: httpx.Response, client: httpx.AsyncClient, model: str) -> dict:
    translator = _Translator(model)
    text_parts: list[str] = []
    reasoning_parts: list[str] = []
    terminal = False
    try:
        async for event in _upstream_events(resp):
            etype = event.get("type") or ""
            if etype == "response.created":
                response = event.get("response") if isinstance(event.get("response"), dict) else {}
                translator.completion_id = "chatcmpl-" + str(response.get("id") or "chatgpt")
            elif etype == "response.output_text.delta":
                if event.get("delta"):
                    text_parts.append(str(event.get("delta")))
            elif etype in ("response.reasoning_summary_text.delta", "response.reasoning_text.delta"):
                if event.get("delta"):
                    reasoning_parts.append(str(event.get("delta")))
            elif etype == "response.output_item.added":
                item = event.get("item") if isinstance(event.get("item"), dict) else {}
                if item.get("type") == "function_call":
                    translator.book.add(
                        str(item.get("id") or ""),
                        str(item.get("call_id") or item.get("id") or f"call_{len(translator.book.order)}"),
                        str(item.get("name") or ""),
                        str(item.get("arguments") or ""),
                    )
            elif etype == "response.function_call_arguments.delta":
                rec = translator.book.get(str(event.get("item_id") or ""))
                if rec and event.get("delta"):
                    rec["arguments"] += str(event.get("delta"))
            elif etype == "response.function_call_arguments.done":
                rec = translator.book.get(str(event.get("item_id") or ""))
                if rec and event.get("arguments") is not None:
                    rec["arguments"] = str(event.get("arguments") or "")
            elif etype == "response.completed":
                response = event.get("response") if isinstance(event.get("response"), dict) else {}
                if not response:
                    translator.error = "upstream terminal response is malformed"
                    break
                usage = response.get("usage") if isinstance(response.get("usage"), dict) else {}
                if usage:
                    translator.usage = _usage_from_response(usage)
                terminal = True
                break
            elif etype in ("response.failed", "response.incomplete"):
                response = event.get("response") if isinstance(event.get("response"), dict) else {}
                usage = response.get("usage") if isinstance(response.get("usage"), dict) else {}
                if usage:
                    translator.usage = _usage_from_response(usage)
                err = response.get("error") if isinstance(response.get("error"), dict) else {}
                translator.error = str(err.get("message") or ("upstream response incomplete" if etype == "response.incomplete" else "upstream response failed"))
                break
            elif etype == "error":
                err = event.get("error") if isinstance(event.get("error"), dict) else {}
                translator.error = str(err.get("message") or "upstream error")
                break
    except (UpstreamProtocolError, httpx.HTTPError) as exc:
        translator.error = f"upstream stream failed ({type(exc).__name__})"
    finally:
        await resp.aclose()
        await client.aclose()
    if not terminal and not translator.error:
        translator.error = "upstream stream ended before a terminal response"
    if translator.error:
        return {
            "error": {"message": translator.error, "type": "api_error", "code": 502},
            "_fuse_usage": translator.usage,
        }
    message: dict = {"role": "assistant", "content": "".join(text_parts)}
    if reasoning_parts:
        message["reasoning_content"] = "".join(reasoning_parts)
    calls = translator.book.tool_calls()
    if calls:
        message["tool_calls"] = calls
    finish = "tool_calls" if calls else "stop"
    body = {
        "id": translator.completion_id,
        "object": "chat.completion",
        "created": translator.created,
        "model": model,
        "choices": [{"index": 0, "message": message, "finish_reason": finish}],
    }
    if translator.usage:
        body["usage"] = dict(translator.usage)
    return body


class ChatGPTSource:
    type_id = "chatgpt"

    def __init__(self, row, cfg: dict, creds):
        self.source_id = row["id"]
        self.name = row["name"]
        self.cfg = cfg
        self.creds = list(creds)

    def _pick(self, model: str = "", exclude: set[int] | None = None, prefer_id: int | None = None):
        return pick_account(
            self.creds,
            kind="chatgpt",
            model=model,
            exclude=exclude,
            is_authed=credential_is_authed,
            prefer_id=prefer_id,
        )

    async def models(self) -> list[str]:
        global _models_cache
        if not self.creds:
            return []
        now = time.time()
        if _models_cache and now - _models_cache[0] < 300:
            return list(_models_cache[1])
        picked = self._pick()
        names: list[str] = []
        if picked:
            cred, extra = picked
            token = extra.get("access_token") or accounts.cred_secret(cred)
            names = await fetch_catalog(token, extra.get("account_id") or None)
        return _remember_models(names)

    async def _post(self, token: str, account_id: str | None, payload: dict, client: httpx.AsyncClient):
        # Streaming send: the body stays unread until the translator iterates
        # it, so a cancelled downstream request stops upstream generation
        # promptly instead of buffering the whole response first.
        req = client.build_request(
            "POST",
            _backend_base() + "/responses",
            json=payload,
            headers=_chat_headers(token, account_id),
        )
        return await client.send(req, stream=True)

    @staticmethod
    def _bind_completed_stream(session_id: str | None, cred_id: int, collected: dict | None) -> None:
        """Pin only a fully completed stream.

        Cancelled or truncated streams keep the existing pin and must not
        cooldown the credential. A 5s cooldown would skip prefer_id, so a
        stop-then-retry could land on a different account and miss cache.
        """
        status = (collected or {}).get("stream_status")
        if status == "complete":
            bind("chatgpt", session_id, cred_id)
        elif status == "failed":
            # Short cooldown so an immediate retry of a dead stream skips the
            # sick account. Long enough to fail over, short enough that a
            # user stop-then-retry keeps cache affinity once it expires.
            note_failure(cred_id, cooldown_seconds=5)

    async def chat(self, body: dict, *, session_id: str | None = None, user_id: int | None = None) -> ChatResult:
        await refresh_due()
        await refresh_pool_usage()
        self.creds = pool_policy.filter_credentials(
            db.fetchall(
                "SELECT * FROM credentials WHERE source_id=? AND disabled=0",
                (self.source_id,),
            ),
            "chatgpt",
            user_id,
        )
        model = body.get("model") or CHATGPT_MODELS[0]
        payload = build_responses_payload(body, model, session_id=session_id)
        wants_stream = bool(body.get("stream"))
        tried: set[int] = set()
        attempts: list[dict] = []
        last = ChatResult(status=503, body={"error": "No ChatGPT accounts in the pool"})
        prefer_id = lookup("chatgpt", session_id)
        failover = Failover()
        while True:
            picked = self._pick(model, exclude=tried, prefer_id=prefer_id)
            if not picked:
                if attempts and last.credential_id == attempts[-1].get("credential_id"):
                    attempts.pop()
                last.attempts = attempts
                return last
            cred, extra = picked
            tried.add(int(cred["id"]))
            _note_invoke(cred["id"])
            reserve(cred["id"])
            token = extra.get("access_token") or accounts.cred_secret(cred)
            account_id = extra.get("account_id") or None
            client = _client()
            stream_reserved = False
            try:
                try:
                    resp = await self._post(token, account_id, payload, client)
                except httpx.TimeoutException as exc:
                    await client.aclose()
                    last = ChatResult(
                        status=504,
                        body={"error": {"message": f"upstream timeout ({type(exc).__name__})"}},
                        credential_id=cred["id"],
                        model=model,
                    )
                    failover.server_error(cred["id"], 504, 30)
                    last.attempts = attempts
                    return last
                except httpx.TransportError as exc:
                    await client.aclose()
                    last = ChatResult(
                        status=502,
                        body={"error": {"message": f"upstream transport failure ({type(exc).__name__})"}},
                        credential_id=cred["id"],
                        model=model,
                    )
                    if failover.server_error(cred["id"], 502, 30):
                        attempts.append({"credential_id": cred["id"], "status": 502, "usage": {}})
                        continue
                    last.attempts = attempts
                    return last
                if resp.status_code == 401:
                    await resp.aclose()
                    if await refresh_one(cred):
                        attempts.append({"credential_id": cred["id"], "status": 401, "usage": {}})
                        row = db.fetchone("SELECT * FROM credentials WHERE id=?", (cred["id"],))
                        if row:
                            extra = accounts.cred_extra(row)
                            token = extra.get("access_token") or accounts.cred_secret(row)
                            account_id = extra.get("account_id") or None
                        resp = await self._post(token, account_id, payload, client)
                if resp.status_code != 200:
                    obj = await _error_body(resp)
                    status = resp.status_code
                    await resp.aclose()
                    await client.aclose()
                    last = ChatResult(status=status, body=obj, credential_id=cred["id"], model=model)
                    msg = ""
                    if isinstance(obj, dict):
                        err = obj.get("error")
                        if isinstance(err, dict):
                            msg = str(err.get("message") or "")
                        else:
                            msg = str(obj.get("detail") or err or "")
                    param = _unsupported_param(obj)
                    if status == 400 and param and param in payload:
                        attempts.append({"credential_id": cred["id"], "status": status, "usage": {}})
                        payload.pop(param, None)
                        tried.discard(int(cred["id"]))
                        continue
                    if status == 400 and payload.get("prompt_cache_key") and "prompt_cache_key" in msg.lower():
                        attempts.append({"credential_id": cred["id"], "status": status, "usage": {}})
                        payload.pop("prompt_cache_key", None)
                        tried.discard(int(cred["id"]))
                        continue
                    rejected = status == 401
                    if rejected:
                        status = 502
                        last.status = status
                        last.body = {"error": {"message": "ChatGPT rejected the configured account"}}
                    if status in (402, 429) or rejected:
                        note_failure(cred["id"], cooldown_seconds=30)
                        attempts.append({"credential_id": cred["id"], "status": status, "usage": {}})
                        continue
                    if status >= 500 and failover.server_error(cred["id"], status, 30):
                        attempts.append({"credential_id": cred["id"], "status": status, "usage": {}})
                        continue
                    # Remaining statuses (400/404/413/422...) describe this
                    # request, not the account. Cooling the account here let a
                    # handful of bad requests disable the whole pool.
                    last.attempts = attempts
                    return last
                if wants_stream:
                    collected: dict = {}
                    inner = _translated_stream(resp, client, model, collected)

                    async def _bind_on_finish():
                        try:
                            async for chunk in inner:
                                yield chunk
                        finally:
                            close = getattr(inner, "aclose", None)
                            if close is not None:
                                await close()
                            self._bind_completed_stream(session_id, cred["id"], collected)

                    stream_reserved = True
                    failover.served()
                    stream = UsageStream(
                        _bind_on_finish(),
                        collected,
                        on_close=lambda: release(cred["id"]),
                    )
                    result = ChatResult(status=200, stream=stream, credential_id=cred["id"], model=model)
                    result.attempts = attempts
                    return result
                obj = await _aggregate(resp, client, model)
                if isinstance(obj.get("error"), dict):
                    partial_usage = obj.pop("_fuse_usage", {}) if isinstance(obj, dict) else {}
                    if partial_usage:
                        partial_usage["usage_status"] = "partial"
                    last = ChatResult(
                        status=502,
                        body=obj,
                        usage=partial_usage,
                        credential_id=cred["id"],
                        model=model,
                    )
                    if failover.server_error(cred["id"], 502, 60):
                        attempts.append({"credential_id": cred["id"], "status": 502, "usage": partial_usage})
                        continue
                    last.attempts = attempts
                    return last
                failover.served()
                usage = obj.get("usage") if isinstance(obj.get("usage"), dict) else {}
                bind("chatgpt", session_id, cred["id"])
                result = ChatResult(status=200, body=obj, usage=usage, credential_id=cred["id"], model=model)
                result.attempts = attempts
                return result
            except httpx.HTTPError as exc:
                await client.aclose()
                failed = 504 if isinstance(exc, httpx.TimeoutException) else 502
                last = ChatResult(
                    status=failed,
                    body={"error": {"message": f"upstream request failed ({type(exc).__name__})"}},
                    credential_id=cred["id"],
                    model=model,
                )
                if failover.server_error(cred["id"], failed, 30):
                    attempts.append({"credential_id": cred["id"], "status": failed, "usage": {}})
                    continue
                last.attempts = attempts
                return last
            except Exception:
                # A local failure (encoding, translation bug) would repeat on
                # every account. Do not cool accounts or fail over; surface it.
                await client.aclose()
                logger.exception("ChatGPT request failed locally credential=%s", cred["id"])
                last = ChatResult(
                    status=500,
                    body={"error": {"message": "Fuse could not process this ChatGPT request"}},
                    credential_id=cred["id"],
                    model=model,
                )
                last.attempts = attempts
                return last
            finally:
                if not stream_reserved:
                    release(cred["id"])
