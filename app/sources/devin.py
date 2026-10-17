"""Devin / Cascade pool source.

Talks to Cognition's unpublished Connect-RPC API (server.codeium.com) the same
way Devin CLI does. Fuse does not shell out to the CLI. Accounts are linked on
/sources with the same browser PKCE login the CLI uses (`/auth/cli/continue`).

Wire codec: `devin_proto`. Model identity/catalog state: `devin_catalog`.
Request shaping: `devin_payload`. Stream translation: `devin_stream`. Their
public names are re-exported here as the provider's API surface.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import secrets
import time
from datetime import datetime, timezone
from urllib.parse import urlencode

import httpx

from .. import accounts, db
from ..config import settings
from ..crypto import encrypt
from ..proxy import UsageStream
from .base import ChatResult
from . import devin_proto as proto
from .pool_pick import Failover, auth_failure_confirmed, note_failure, pick_account, release, reserve
from . import pool_policy
from .pool_session import bind, drop_cache_routing_fields, lookup

from .devin_catalog import (
    DEVIN_MODELS,
    FUSE_PREFIX,
    SEEDED_EFFORTS,
    SESSION_TOKEN_PREFIX,
    _humanize_fallback,
    _label_slug,
    _remember_models,
    _seeded_wire_uid,
    _sku_unavailable_message,
    _split_internal_uid,
    _split_wire_uid,
    _takes_effort,
    canonical_fuse_id,
    canonical_upstream,
    catalog_empty,
    catalog_ids_from_records,
    cached_models,
    efforts_for,
    family_label,
    fuse_id,
    is_devin_model,
    is_internal_uid,
    listed_upstream,
    model_label,
    normalize_token,
    note_sku_failure,
    reset_sku_health,
    resolve_wire_uid,
    sku_dead,
    sku_error_kind,
    upstream_id,
)
from .devin_payload import (
    DEFAULT_STOP,
    _catalog_meta,
    _cascade_id_for,
    _has_remote_image,
    _meta,
    _messages_to_prompts,
    _tools_from_body,
    build_chat_request,
)
from .devin_stream import (
    _ToolBook,
    _chunk,
    _collect_completion,
    _completion_id,
    _debug_tool_call,
    _devin_debug,
    _json_object,
    _merge_tool_args,
    _merge_usage_frame,
    _openai_usage,
    _sse,
    _stream_sse,
)

logger = logging.getLogger(__name__)

USAGE_TTL = 300
AUTH_PATH = "/exa.auth_pb.AuthService/GetUserJwt"
CHAT_PATH = "/exa.api_server_pb.ApiServerService/GetChatMessage"
MODELS_PATH = "/exa.api_server_pb.ApiServerService/GetCliModelConfigs"
STATUS_PATH = "/exa.seat_management_pb.SeatManagementService/GetUserStatus"

_jwt_cache: dict[str, tuple[float, dict]] = {}
_JWT_TTL = 240.0


def _api_server() -> str:
    return settings.devin_api_server.rstrip("/")


def _error_text(body) -> str:
    err = body.get("error") if isinstance(body, dict) else None
    return str(err.get("message") or "") if isinstance(err, dict) else str(err or "")


def _headers(*, streaming: bool = False) -> dict:
    if streaming:
        return {
            "content-type": "application/connect+proto",
            "connect-protocol-version": "1",
            "connect-content-encoding": "gzip",
            "connect-accept-encoding": "gzip",
            "accept-encoding": "identity",
        }
    return {
        "content-type": "application/proto",
        "connect-protocol-version": "1",
        "accept": "*/*",
    }


async def _unary(client: httpx.AsyncClient, path: str, body: bytes) -> tuple[int, bytes]:
    url = _api_server() + path
    try:
        r = await client.post(url, content=body, headers=_headers())
    except httpx.TimeoutException:
        return 504, b""
    except httpx.TransportError:
        return 502, b""
    return r.status_code, r.content or b""


_JWT_REJECTED = "Invalid Devin session token."


async def get_user_jwt(token: str) -> tuple[dict | None, str]:
    token = normalize_token(token)
    hit = _jwt_cache.get(token)
    if hit and time.time() - hit[0] < _JWT_TTL:
        return hit[1], ""
    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=10.0)) as client:
        status, raw = await _unary(client, AUTH_PATH, proto.encode_get_user_jwt(_meta(token)))
    if status in (401, 403):
        return None, _JWT_REJECTED
    if status != 200:
        return None, f"Devin auth http {status}"
    try:
        decoded = proto.decode_get_user_jwt(raw)
    except Exception:
        return None, "Devin auth returned an unreadable response."
    if not decoded.get("user_jwt"):
        return None, "Devin auth returned an empty JWT."
    _jwt_cache[token] = (time.time(), decoded)
    if len(_jwt_cache) > 64:
        cutoff = time.time() - 2 * _JWT_TTL
        for stale in [k for k, v in _jwt_cache.items() if v[0] < cutoff]:
            _jwt_cache.pop(stale, None)
    return decoded, ""


async def fetch_status(token: str) -> dict:
    token = normalize_token(token)
    async with httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=10.0)) as client:
        status, raw = await _unary(client, STATUS_PATH, proto.encode_unary_metadata(_meta(token)))
    if status != 200 or not raw:
        return {}
    try:
        return proto.decode_user_status(raw)
    except Exception:
        return {}


async def fetch_catalog(token: str) -> list[str]:
    cached = cached_models(45.0)
    if cached is not None:
        return cached
    token = normalize_token(token)
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=10.0)) as client:
            status, raw = await _unary(
                client, MODELS_PATH, proto.encode_unary_metadata(_catalog_meta(token))
            )
    except (httpx.HTTPError, ValueError):
        status, raw = 0, b""
    names: list[str] = []
    labels: dict[str, str] = {}
    if status == 200 and raw:
        try:
            names, labels = catalog_ids_from_records(proto.decode_cli_model_configs(raw))
        except Exception:
            logger.warning("Devin model catalog could not be parsed", exc_info=True)
    if not names:
        # A failed fetch after a real catalog keeps the learned maps; seeding
        # here would replace real wire UIDs with guesses.
        if not catalog_empty():
            return _remember_models([], {})
        names, labels = catalog_ids_from_records([])
    return _remember_models(names, labels)


def ensure_devin_source() -> int:
    row = db.fetchone("SELECT id FROM sources WHERE type='devin'")
    if row:
        return row["id"]
    with db.tx() as conn:
        conn.execute(
            "INSERT INTO sources (type, name, enabled, config, created_at) VALUES (?,?,?,?,?)",
            ("devin", "Devin", 1, "{}", db.now()),
        )
        return db.last_id(conn)


def disable_credential(cred_id: int, *, extra: str | None = None) -> None:
    accounts.disable_credential(cred_id, extra=extra)


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


def is_held(cred, now: datetime | None = None) -> bool:
    until = str(_row_get(cred, "pool_held_until") or "")
    if not until:
        return False
    try:
        ts = datetime.fromisoformat(until.replace("Z", "+00:00"))
    except ValueError:
        return False
    return ts > (now or datetime.now(timezone.utc))


def billing_has_subscription(billing: dict | None) -> bool:
    if not isinstance(billing, dict):
        return False
    if billing.get("last_error") and not billing.get("fetched_at"):
        return False
    plan = str(billing.get("plan_type") or "").strip().lower()
    if plan in ("pro", "max", "teams", "enterprise", "team"):
        return True
    if billing.get("pro"):
        return True
    if billing.get("fetched_at") and not billing.get("last_error"):
        return True
    return False


def credential_is_authed(cred, extra: dict | None = None) -> bool:
    if _row_get(cred, "disabled"):
        return False
    extra = extra if extra is not None else accounts.cred_extra(cred)
    key = extra.get("api_key") or accounts.cred_secret(cred)
    if not key:
        return False
    billing = extra.get("billing") if isinstance(extra.get("billing"), dict) else {}
    err = str(billing.get("last_error") or "").lower()
    if "invalid" in err or "401" in err or "403" in err:
        return False
    return True


def user_has_devin(user_id: int) -> bool:
    return devin_access_state(user_id)["ok"]


def _devin_eligible(row, extra: dict) -> bool:
    if not credential_is_authed(row, extra):
        return False
    billing = extra.get("billing") if isinstance(extra.get("billing"), dict) else {}
    return bool(billing_has_subscription(billing) or extra.get("api_key") or accounts.cred_secret(row))


def devin_own_access(user_id: int) -> dict:
    """Access from this user's own donated Devin accounts only."""
    sid = ensure_devin_source()
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
        if _devin_eligible(row, extra):
            return {"ok": True, "reason": "ok"}
    return {"ok": False, "reason": "unauthed" if not saw_authed else "no_sub"}


def _devin_live(row) -> bool:
    return not is_held(row) and _devin_eligible(row, accounts.cred_extra(row))


def devin_access_state(user_id: int) -> dict:
    return pool_policy.with_open_access(
        user_id, "devin", devin_own_access(user_id), _devin_live
    )


def note_pool_invoke(cred_id: int) -> None:
    db.execute("UPDATE credentials SET pool_invokes = COALESCE(pool_invokes, 0) + 1 WHERE id=?", (cred_id,))


def _find_key(source_id: int, key: str):
    for row in db.fetchall("SELECT * FROM credentials WHERE source_id=?", (source_id,)):
        try:
            if accounts.cred_secret(row) == key:
                return row
        except Exception:
            continue
    return None


def _store_billing(cred_id: int, parsed: dict, *, error: str = "") -> dict:
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
    if parsed.get("email"):
        extra["email"] = parsed["email"]
    if parsed.get("name"):
        extra["account_name"] = parsed["name"]
    # Keep the plan the upstream reported; only fall back to the prior value
    # or Pro when the status call said nothing. A forced "Pro" masked
    # demoted/free accounts and kept them pool-eligible.
    billing["plan_type"] = parsed.get("plan_type") or billing.get("plan_type") or ("Pro" if parsed.get("pro") else "")
    billing["pro"] = bool(parsed.get("pro") or billing.get("plan_type") == "Pro")
    billing["fetched_at"] = now
    billing["last_error"] = ""
    extra["billing"] = billing
    if row:
        accounts.update_extra(cred_id, extra)
        if parsed.get("email") or parsed.get("name"):
            label = parsed.get("email") or parsed.get("name") or row["label"]
            db.execute("UPDATE credentials SET label=? WHERE id=?", (str(label)[:80], cred_id))
    return billing


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def start_cli_login(redirect_uri: str = "") -> dict:
    """Browser PKCE, same page as `devin auth login`.

    Devin only accepts redirect_uri = http://127.0.0.1:<port>/callback (the
    local CLI). A hosted dashboard cannot use that, so we omit redirect_uri
    and complete with the one-time login code Devin shows after sign-in.
    """
    verifier = _b64url(secrets.token_bytes(32))
    challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    state = secrets.token_urlsafe(24)
    params = {
        "state": state,
        "prompt": "select_account",
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    if redirect_uri:
        params["redirect_uri"] = redirect_uri
    webapp = (settings.devin_webapp_url or "https://app.devin.ai").rstrip("/")
    return {
        "state": state,
        "verifier": verifier,
        "challenge": challenge,
        "redirect_uri": redirect_uri,
        "auth_url": f"{webapp}/auth/cli/continue?{urlencode(params)}",
    }


def _token_from_exchange(data) -> str:
    if not isinstance(data, dict):
        return ""
    for key in ("token", "api_key", "session_token", "windsurf_api_key", "access_token"):
        val = data.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    inner = data.get("data")
    if isinstance(inner, dict):
        return _token_from_exchange(inner)
    return ""


async def exchange_cli_code(code: str, verifier: str, redirect_uri: str = "") -> tuple[str, str]:
    code = "".join((code or "").split())
    verifier = (verifier or "").strip()
    if not code or not verifier:
        return "", "Devin sign-in did not return an authorization code."
    url = settings.devin_cli_token_url.strip()
    payload = {"code": code, "code_verifier": verifier}
    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=10.0), follow_redirects=False) as client:
        try:
            r = await client.post(url, json=payload, headers={"Accept": "application/json", "Content-Type": "application/json"})
        except httpx.HTTPError:
            return "", "Could not reach Devin to finish sign-in."
    if r.status_code in (401, 403):
        return "", "Devin rejected the sign-in. Try adding the account again."
    if r.status_code != 200:
        return "", f"Devin token exchange failed (HTTP {r.status_code})."
    try:
        data = r.json()
    except json.JSONDecodeError:
        return "", "Devin returned an unreadable sign-in response."
    token = _token_from_exchange(data)
    if not token:
        return "", "Devin sign-in did not return a session token."
    return token, ""


async def probe_token(raw: str) -> tuple[dict | None, str]:
    token = normalize_token(raw)
    if not token or len(token) < 12 or len(token) > 4096:
        return None, "Devin sign-in did not return a usable session."
    if any(ord(ch) < 32 for ch in token):
        return None, "Devin sign-in did not return a usable session."
    jwt, err = await get_user_jwt(token)
    if err or not jwt:
        return None, err or "Could not validate this Devin token."
    status = await fetch_status(token)
    info = {
        "api_key": token,
        "api_server_url": (jwt.get("custom_api_server_url") or "").strip() or _api_server(),
        "email": status.get("email") or "",
        "name": status.get("name") or "",
        "plan_type": status.get("plan_type") or ("Pro" if status.get("pro") else ""),
        "pro": bool(status.get("pro")),
    }
    return info, ""


async def complete_cli_login(
    code: str,
    verifier: str,
    donated_by: int,
    *,
    redirect_uri: str = "",
    label: str = "",
) -> tuple[int | None, str]:
    token, err = await exchange_cli_code(code, verifier, redirect_uri)
    if err or not token:
        return None, err or "Devin sign-in failed."
    return await add_session_token(ensure_devin_source(), token, donated_by, label=label)


async def add_session_token(source_id: int, raw: str, donated_by: int, *, label: str = "") -> tuple[int | None, str]:
    info, err = await probe_token(raw)
    if err or not info:
        return None, err or "Could not add that token."
    key = info["api_key"]
    existing = _find_key(source_id, key)
    if existing and not existing["disabled"]:
        return None, "That Devin account is already in the pool."
    name = (label or "").strip()[:80] or info.get("email") or info.get("name") or "Devin account"
    extra = {
        "api_key": key,
        "api_server_url": info.get("api_server_url") or _api_server(),
        "email": info.get("email") or "",
        "account_name": info.get("name") or "",
    }
    if existing:
        db.execute(
            """UPDATE credentials SET disabled=0, donated_by=?, label=?, secret=?, extra=? WHERE id=?""",
            (donated_by, name, encrypt(key), encrypt(json.dumps(extra)), existing["id"]),
        )
        cred_id = int(existing["id"])
    else:
        cred_id = accounts.add_credential(source_id, name, key, extra=extra, donated_by=donated_by)
    _store_billing(cred_id, info)
    return cred_id, ""


async def refresh_status(cred, *, force: bool = False) -> None:
    extra = accounts.cred_extra(cred)
    billing = extra.get("billing") if isinstance(extra.get("billing"), dict) else {}
    # Gate on checked_at (set on success AND error) like the other pools: a
    # credential whose status keeps failing must not cost an HTTP round trip
    # on every chat call until it succeeds once.
    checked = str(billing.get("checked_at") or billing.get("fetched_at") or "")
    if not force and checked:
        try:
            ts = datetime.fromisoformat(checked.replace("Z", "+00:00"))
            if (datetime.now(timezone.utc) - ts).total_seconds() < USAGE_TTL:
                return
        except ValueError:
            pass
    token = extra.get("api_key") or accounts.cred_secret(cred)
    jwt, err = await get_user_jwt(token)
    if err or not jwt:
        _store_billing(int(cred["id"]), {}, error=err or "auth failed")
        return
    status = await fetch_status(token)
    if not str(status.get("plan_type") or ""):
        status["plan_type"] = billing.get("plan_type") or ""
    _store_billing(int(cred["id"]), status)


async def refresh_pool_status() -> None:
    sid = ensure_devin_source()
    rows = db.fetchall("SELECT * FROM credentials WHERE source_id=? AND disabled=0", (sid,))
    if not rows:
        return
    results = await asyncio.gather(*(refresh_status(row) for row in rows), return_exceptions=True)
    for row, result in zip(rows, results):
        if isinstance(result, Exception):
            logger.warning(
                "Devin status refresh failed for credential %s",
                row["id"],
                exc_info=(type(result), result, result.__traceback__),
            )


def account_email(extra: dict) -> str:
    return str(extra.get("email") or extra.get("account_name") or "").strip()


class DevinSource:
    type_id = "devin"

    def __init__(self, row, cfg: dict, creds):
        self.source_id = row["id"]
        self.name = row["name"]
        self.cfg = cfg
        self.creds = list(creds)

    def _pick(self, exclude: set[int] | None = None, prefer_id: int | None = None, owner_id: int | None = None, hint_id: int | None = None, primary_id: int | None = None):
        now = datetime.now(timezone.utc)
        return pick_account(
            self.creds,
            kind="devin",
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
        token = ""
        if picked:
            cred, extra = picked
            token = extra.get("api_key") or accounts.cred_secret(cred)
        else:
            for cred in self.creds:
                extra = accounts.cred_extra(cred)
                token = extra.get("api_key") or accounts.cred_secret(cred)
                if token:
                    break
        if not token:
            return list(DEVIN_MODELS)
        return await fetch_catalog(token)

    async def chat(self, body: dict, *, session_id: str | None = None, user_id: int | None = None, affinity_id: str | None = None) -> ChatResult:
        await refresh_pool_status()
        self.creds = pool_policy.filter_credentials(
            db.fetchall(
                "SELECT * FROM credentials WHERE source_id=? AND disabled=0",
                (self.source_id,),
            ),
            "devin",
            user_id,
        )
        tried: set[int] = set()
        attempts: list[dict] = []
        last = ChatResult(status=503, body={"error": "No Devin accounts in the pool"})
        requested = body.get("model") or DEVIN_MODELS[0]
        model = listed_upstream(requested) or upstream_id(requested)
        if _has_remote_image(body.get("messages")):
            return ChatResult(
                status=400,
                body={"error": {"message": "Devin accepts only base64 data-URI images, not image URLs"}},
                model=model,
            )
        payload = drop_cache_routing_fields(body)
        effort = payload.get("reasoning_effort")
        if not isinstance(effort, str):
            effort = None
        probe_uid = resolve_wire_uid(model, effort)
        if sku_dead(probe_uid):
            # This exact SKU failed upstream moments ago (circuit breaker).
            # Do not burn a pool attempt on it; fail fast with a clear reason.
            return ChatResult(
                status=503,
                body={"error": {"message": _sku_unavailable_message(requested, effort)}},
                model=model,
            )
        prefer_id = lookup("devin", session_id)
        hint_id = lookup("devin", affinity_id) if prefer_id is None and affinity_id else None
        primary_id = pool_policy.primary_account(user_id, "devin")
        failover = Failover()
        while True:
            picked = self._pick(exclude=tried, prefer_id=prefer_id, owner_id=user_id, hint_id=hint_id, primary_id=primary_id)
            if not picked:
                # Parity with grok/chatgpt/kimi/zai/mimo: the final failed
                # attempt is reported once via the returned result; drop the
                # duplicate so its usage is not counted twice.
                if attempts and last.credential_id == attempts[-1].get("credential_id"):
                    attempts.pop()
                last.attempts = attempts
                return last
            cred, extra = picked
            tried.add(int(cred["id"]))
            note_pool_invoke(cred["id"])
            token = extra.get("api_key") or accounts.cred_secret(cred)
            stream_reserved = False
            try:
                reserve(cred["id"])
                result = await self._chat_one(token, payload, model, cred, session_id, attempts)
                if result.stream is not None:
                    stream_reserved = True
                    failover.served()
                    # result.usage is the dict the stream mutates; `or {}`
                    # would hand the wrapper a different dict and the
                    # completion status (and pin bind) would never be seen.
                    return self._stream_result_wrap(
                        result.stream, result.usage if isinstance(result.usage, dict) else {}, cred, session_id, model, attempts,
                        hints=result.hints,
                    )
                last = result
                if result.status == 401:
                    if auth_failure_confirmed(cred["id"]):
                        disable_credential(cred["id"], extra=cred.get("extra"))
                    last.status = 502
                    last.body = {"error": {"message": "Devin rejected the configured account"}}
                    attempts.append({"credential_id": cred["id"], "status": 401, "usage": result.usage or {}})
                    continue
                if result.status == 429:
                    note_failure(cred["id"], cooldown_seconds=60)
                    attempts.append({"credential_id": cred["id"], "status": 429, "usage": result.usage or {}})
                    continue
                if (result.usage or {}).get("failure_phase") == "response_received":
                    last.attempts = attempts
                    return last
                if result.status == 502 and "quota" in _error_text(result.body).lower():
                    # Same daily-window cooldown the stream path applies.
                    note_failure(cred["id"], cooldown_seconds=900, affinity_safe=False)
                    attempts.append({"credential_id": cred["id"], "status": 502, "usage": result.usage or {}})
                    continue
                if result.status >= 500 and failover.server_error(cred["id"], result.status, 15):
                    attempts.append({"credential_id": cred["id"], "status": result.status, "usage": result.usage or {}})
                    continue
                if result.status == 200:
                    failover.served()
                    bind("devin", session_id, cred["id"])
                last.attempts = attempts
                return last
            finally:
                if not stream_reserved:
                    release(cred["id"])

    async def _chat_one(self, token, body, model, cred, session_id, attempts) -> ChatResult:
        jwt, err = await get_user_jwt(token)
        if err or not jwt:
            # Only an explicit auth rejection strikes the account: a timeout,
            # 5xx or unreadable reply is Devin's outage, not a dead credential.
            return ChatResult(
                status=401 if err == _JWT_REJECTED else 502,
                body={"error": {"message": err or "Devin auth failed"}},
                credential_id=cred["id"],
                model=model,
            )
        effort = body.get("reasoning_effort")
        if not isinstance(effort, str):
            effort = None
        if catalog_empty():
            await fetch_catalog(token)
        model_uid = resolve_wire_uid(model, effort)
        req = build_chat_request(
            body,
            token=token,
            user_jwt=jwt.get("user_jwt") or "",
            model_uid=model_uid,
            cascade_id=_cascade_id_for(session_id),
        )
        frame = proto.connect_frame(proto.encode_get_chat_message(req))
        url = _api_server() + CHAT_PATH
        client = httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=15.0))
        want_stream = bool(body.get("stream"))
        try:
            req_http = client.build_request("POST", url, content=frame, headers=_headers(streaming=True))
            resp = await client.send(req_http, stream=True)
        except httpx.TimeoutException:
            await client.aclose()
            return ChatResult(status=504, body={"error": {"message": "Devin timed out"}}, credential_id=cred["id"], model=model)
        except httpx.TransportError as exc:
            await client.aclose()
            return ChatResult(
                status=502,
                body={"error": {"message": f"Devin transport failure ({type(exc).__name__})"}},
                credential_id=cred["id"],
                model=model,
            )
        if resp.status_code != 200:
            try:
                err_body = await resp.aread()
            except Exception:
                err_body = b""
            await resp.aclose()
            await client.aclose()
            status = resp.status_code
            # Not classified for the SKU breaker: a non-200 on this streaming
            # endpoint is an HTTP-level failure (gateway, auth, outage).
            msg = err_body.decode("utf-8", errors="replace")[:400] or f"http {status}"
            return ChatResult(
                status=status,
                body={"error": {"message": msg}},
                credential_id=cred["id"],
                model=model,
            )
        collected: dict = {}
        if want_stream:
            hints: dict = {}
            result = ChatResult(
                status=200,
                stream=_stream_sse(resp, client, collected, model, model_uid, hints),
                usage=collected,
                credential_id=cred["id"],
                model=model,
                hints=hints,
            )
            result.attempts = attempts
            return result
        try:
            obj, usage, trailer_error = await _collect_completion(resp, model, model_uid)
        except (httpx.HTTPError, OSError, EOFError, ValueError) as exc:
            # After a 200 upstream may already have run the generation: report
            # it on this credential and do not resubmit it elsewhere.
            return ChatResult(
                status=504 if isinstance(exc, httpx.TimeoutException) else 502,
                body={"error": {"message": f"Devin response failed mid-read ({type(exc).__name__})"}},
                usage={"failure_phase": "response_received"},
                credential_id=cred["id"],
                model=model,
            )
        finally:
            await resp.aclose()
            await client.aclose()
        if trailer_error:
            return ChatResult(
                status=502,
                body={"error": {"message": trailer_error}},
                credential_id=cred["id"],
                model=model,
            )
        return ChatResult(status=200, body=obj, usage=usage, credential_id=cred["id"], model=model)

    def _stream_result_wrap(self, stream, collected, cred, session_id, model, attempts, hints=None) -> ChatResult:
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
                    bind("devin", session_id, cid)
                elif status == "failed":
                    # A quota-exhausted account is spent for the whole daily
                    # window; cool it well past the generic failure blip so
                    # the pool stops hammering it turn after turn.
                    quota_hit = "quota" in str((collected or {}).get("trailer_error") or "").lower()
                    limited = (collected or {}).get("trailer_status") == 429
                    note_failure(
                        cid,
                        cooldown_seconds=900 if quota_hit else 60 if limited else 5,
                        affinity_safe=not (quota_hit or limited),
                    )

        wrapped = UsageStream(_bind_on_finish(), collected, on_close=lambda: release(cid), hints=hints)
        result = ChatResult(status=200, stream=wrapped, credential_id=cid, model=model)
        result.attempts = attempts
        return result
