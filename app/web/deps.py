from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import secrets
import time

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from .. import accounts, chats, hosts, skills, vault
from ..config import ROOT, settings
from ..crypto import decrypt, encrypt
from ..security import client_ip, ensure_csrf, limiter

templates = Jinja2Templates(directory=str(ROOT / "templates"))
_LOGO_DIR = ROOT / "static" / "logos"


def provider_logo_url(type_id: str) -> str:
    """The one URL every page uses for a provider's logo. The content hash in
    ?v= makes a replaced SVG a new URL, so no browser keeps showing the old
    mark from its cache; an unknown type falls back to a generic icon."""
    name = type_id if re.fullmatch(r"[a-z0-9_-]+", type_id or "") else ""
    path = _LOGO_DIR / f"{name}.svg"
    try:
        stat = path.stat()
    except OSError:
        return "/static/icon.svg"
    key = (name, stat.st_mtime_ns, stat.st_size)
    version = _logo_versions.get(key)
    if version is None:
        version = hashlib.sha256(path.read_bytes()).hexdigest()[:10]
        _logo_versions[key] = version
    return f"/static/logos/{name}.svg?v={version}"


_logo_versions: dict[tuple, str] = {}
templates.env.globals["provider_logo_url"] = provider_logo_url
logger = logging.getLogger("app.web")
_pending: dict[int, dict] = {}
_chatgpt_pending: dict[int, dict] = {}
_devin_pending: dict[int, dict] = {}
_kimi_pending: dict[int, dict] = {}
_mimo_pending: dict[int, dict] = {}
_claude_pending: dict[int, dict] = {}
_notices: dict[int, str] = {}
_pool_flash: dict[int, tuple[str, str, str]] = {}
_PENDING_TTL = 900
_KEY_REVEAL_TTL = 300

def current_user(request: Request):
    uid = request.session.get("uid")
    if not uid:
        return None
    user = accounts.get_user(uid)
    if not user:
        return None
    # Sessions minted since the fingerprint stamp are bound to the password
    # hash: a password change logs out every cookie issued before it. A
    # session without a stamp predates the stamp and passes unchanged.
    stamped = request.session.get("ph")
    if stamped is not None and stamped != accounts.password_fingerprint(user):
        request.session.clear()
        return None
    return user


def _vault_cookie_args():
    return {
        "key": vault.KEEP_COOKIE,
        "max_age": vault.KEEP_SECONDS,
        "httponly": True,
        "secure": True,
        "samesite": "lax",
        "path": "/",
    }


def _set_vault_cookie(response, user_id: int):
    token = vault.issue_keep(user_id)
    if token:
        response.set_cookie(value=token, **_vault_cookie_args())
    return response


def _clear_vault_cookie(response):
    response.delete_cookie(vault.KEEP_COOKIE, path="/")
    return response


def restore_vault_keep(request: Request, user_id: int) -> None:
    if vault.unlocked(user_id):
        return
    token = request.cookies.get(vault.KEEP_COOKIE)
    if token:
        vault.restore_keep(user_id, token)


def require_user(request: Request):
    user = current_user(request)
    if not user:
        return None, RedirectResponse("/login", status_code=303)
    restore_vault_keep(request, user["id"])
    return user, None


def require_admin(request: Request):
    user, redir = require_user(request)
    if redir:
        return None, redir
    if not user["is_admin"]:
        return None, RedirectResponse("/", status_code=303)
    return user, None


def ctx(request: Request, user=None, **extra):
    data = {
        "request": request,
        "user": user if user is not None else current_user(request),
        "csrf_token": ensure_csrf(request),
    }
    data.update(extra)
    return data


def render(request: Request, name: str, user=None, status_code: int = 200, **extra):
    resp = templates.TemplateResponse(request, name, ctx(request, user, **extra), status_code=status_code)
    resp.headers["Cache-Control"] = "no-store"
    return resp


def _auth_limited(request: Request) -> bool:
    return not limiter.allow(f"auth:{client_ip(request)}", settings.auth_rate_per_minute, 60)


def _public_base(request: Request) -> str:
    configured = (settings.public_base_url or "").rstrip("/")
    if configured:
        return configured + "/v1"
    return str(request.base_url).rstrip("/") + "/v1"


def _stash_key_reveal(request: Request, uid: int, raw: str) -> None:
    payload = json.dumps(
        {"uid": uid, "key": raw, "created_at": time.time(), "nonce": secrets.token_urlsafe(12)},
        separators=(",", ":"),
    )
    request.session["key_reveal"] = encrypt(payload)


def _peek_key_reveal(request: Request, uid: int) -> str | None:
    token = request.session.get("key_reveal") if request.query_params.get("created") == "1" else None
    if not isinstance(token, str):
        return None
    try:
        reveal = json.loads(decrypt(token))
        created_at = float(reveal.get("created_at"))
    except (json.JSONDecodeError, TypeError, ValueError, AttributeError):
        request.session.pop("key_reveal", None)
        return None
    if (
        not isinstance(reveal, dict)
        or reveal.get("uid") != uid
        or not isinstance(reveal.get("key"), str)
        or time.time() - created_at > _KEY_REVEAL_TTL
        or created_at > time.time() + 30
    ):
        request.session.pop("key_reveal", None)
        return None
    return reveal["key"]


def _invalid_json_constant(_value: str):
    raise ValueError("invalid JSON number")


_SURROGATE_RE = re.compile("[\ud800-\udfff]")


def _validate_json_unicode(value, depth: int = 0) -> None:
    if depth > 50:
        raise RecursionError
    if isinstance(value, str):
        if _SURROGATE_RE.search(value):
            raise UnicodeError("invalid Unicode surrogate")
    elif isinstance(value, float) and not math.isfinite(value):
        raise ValueError("invalid JSON number")
    elif isinstance(value, list):
        for item in value:
            _validate_json_unicode(item, depth + 1)
    elif isinstance(value, dict):
        for key, item in value.items():
            _validate_json_unicode(key, depth + 1)
            _validate_json_unicode(item, depth + 1)


def _json_body_limit(request: Request) -> int:
    path = request.url.path
    limit = settings.max_body_bytes
    if path.startswith("/chat/"):
        limit = max(limit, settings.chat_body_bytes)
    return limit


async def _json_object(request: Request) -> dict:
    try:
        body = await request.body()
        if len(body) > _json_body_limit(request):
            raise HTTPException(413, "Payload too large")
        raw = json.loads(body.decode("utf-8"), parse_constant=_invalid_json_constant)
        _validate_json_unicode(raw)
    except HTTPException:
        raise
    except (json.JSONDecodeError, UnicodeDecodeError, UnicodeError, RecursionError, ValueError):
        raise HTTPException(400, "Invalid JSON") from None
    if not isinstance(raw, dict):
        raise HTTPException(400, "JSON body must be an object")
    return raw


def _chat_validation_error(exc: chats.ChatValidationError) -> HTTPException:
    return HTTPException(exc.status_code, exc.detail)


def vault_locked_json():
    return JSONResponse(
        {
            "error": {
                "type": "vault_locked",
                "code": 401,
                "message": "Unlock your machines with your account password.",
            }
        },
        status_code=401,
    )


def _skill_fail(exc: skills.SkillError):
    return JSONResponse(
        {"error": {"type": "skill", "code": exc.status_code, "message": exc.detail}},
        status_code=exc.status_code,
        headers={"Cache-Control": "no-store"},
    )


def _host_fail(exc: hosts.HostError):
    if exc.status_code == 401 and "vault" in str(exc.detail).lower():
        return vault_locked_json()
    return JSONResponse(
        {"error": {"type": "host", "code": exc.status_code, "message": exc.detail}},
        status_code=exc.status_code,
    )


def _host_http_error(exc: hosts.HostError) -> HTTPException:
    if exc.status_code == 401 and "vault" in str(exc.detail).lower():
        return HTTPException(
            status_code=401,
            detail={
                "error": {
                    "type": "vault_locked",
                    "code": 401,
                    "message": "Unlock your machines with your account password.",
                }
            },
        )
    return HTTPException(exc.status_code, exc.detail)



def _chat_auth(request: Request):
    user, redir = require_user(request)
    if redir:
        return None, JSONResponse({"error": {"message": "Sign in required", "type": "auth", "code": 401}}, status_code=401)
    return user, None
