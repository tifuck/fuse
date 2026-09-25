import hmac
import secrets
import threading
import time
from collections import defaultdict
from urllib.parse import urlparse

from fastapi import HTTPException, Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from .config import settings

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
_DUMMY_CSRF = secrets.token_urlsafe(32)


def client_ip(request: Request) -> str:
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def ensure_csrf(request: Request) -> str:
    tok = request.session.get("csrf")
    if not tok:
        tok = secrets.token_urlsafe(32)
        request.session["csrf"] = tok
    return tok


async def csrf_guard(request: Request) -> None:
    if request.method in SAFE_METHODS:
        return
    token = request.headers.get("x-csrf-token") or ""
    if not token:
        ctype = request.headers.get("content-type") or ""
        if ctype.startswith("application/x-www-form-urlencoded") or ctype.startswith("multipart/form-data"):
            form = await request.form()
            token = str(form.get("csrf_token") or "")
    expected = request.session.get("csrf") or ""
    if not expected or not token or not hmac.compare_digest(str(token), str(expected)):
        # Compare against a dummy so missing tokens still do some work.
        hmac.compare_digest(_DUMMY_CSRF, _DUMMY_CSRF)
        raise HTTPException(403, "CSRF")


class RateLimiter:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._hits: dict[str, list[float]] = defaultdict(list)

    def allow(self, key: str, limit: int, window: float) -> bool:
        now = time.monotonic()
        cutoff = now - window
        with self._lock:
            stamps = [t for t in self._hits[key] if t > cutoff]
            if len(stamps) >= limit:
                self._hits[key] = stamps
                return False
            stamps.append(now)
            self._hits[key] = stamps
            if len(self._hits) > 8192:
                stale = [k for k, v in self._hits.items() if not v or v[-1] < cutoff]
                for k in stale[:4096]:
                    self._hits.pop(k, None)
            return True


limiter = RateLimiter()


class MaxBodyMiddleware:
    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path") or ""
        method = (scope.get("method") or "").upper()
        limit = settings.max_body_bytes
        if path.startswith("/chat/") and method in {"POST", "PATCH", "PUT"}:
            limit = max(limit, settings.chat_body_bytes)
        raw_values = [value for name, value in scope.get("headers", []) if name.lower() == b"content-length"]
        if raw_values:
            if len(raw_values) != 1:
                await Response("Invalid Content-Length", status_code=400)(scope, receive, send)
                return
            try:
                raw = raw_values[0].decode("ascii")
                n = int(raw)
            except (UnicodeDecodeError, ValueError):
                await Response("Invalid Content-Length", status_code=400)(scope, receive, send)
                return
            if n < 0:
                await Response("Invalid Content-Length", status_code=400)(scope, receive, send)
                return
            if n > limit:
                await Response("Payload too large", status_code=413)(scope, receive, send)
                return

        messages = []
        size = 0
        while True:
            message = await receive()
            messages.append(message)
            if message.get("type") == "http.request":
                size += len(message.get("body", b""))
                if size > limit:
                    await Response("Payload too large", status_code=413)(scope, receive, send)
                    return
                if not message.get("more_body", False):
                    break
            elif message.get("type") == "http.disconnect":
                break

        async def replay_receive():
            if messages:
                return messages.pop(0)
            return await receive()

        await self.app(scope, replay_receive, send)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; "
            "script-src 'self'; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "font-src https://fonts.gstatic.com; "
            "img-src 'self' data:; "
            "connect-src 'self'; "
            "frame-ancestors 'none'; "
            "base-uri 'self'; "
            "form-action 'self'",
        )
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        path = request.url.path
        if not path.startswith("/static"):
            response.headers.setdefault("Cache-Control", "no-store")
        return response


_BLOCKED_HOSTS = {
    "169.254.169.254",
    "metadata.google.internal",
    "metadata",
}


def allowed_base_url(url: str) -> bool:
    url = (url or "").strip()
    if not url or len(url) > 500:
        return False
    try:
        p = urlparse(url)
    except Exception:
        return False
    if p.scheme not in ("http", "https"):
        return False
    host = (p.hostname or "").lower().rstrip(".")
    if not host or host in _BLOCKED_HOSTS or host.endswith(".internal"):
        return False
    if host.startswith("169.254."):
        return False
    return True


def safe_verify_url(url: str) -> str:
    url = (url or "").strip()
    if not url or len(url) > 500:
        return ""
    try:
        p = urlparse(url)
    except Exception:
        return ""
    if p.scheme != "https":
        return ""
    host = (p.hostname or "").lower().rstrip(".")
    if host in ("grok.com", "x.ai") or host.endswith(".grok.com") or host.endswith(".x.ai"):
        return url
    if host in ("devin.ai", "app.devin.ai") or host.endswith(".devin.ai"):
        return url
    if host in ("kimi.ai", "kimi.com") or host.endswith(".kimi.ai") or host.endswith(".kimi.com"):
        return url
    if host in ("xiaomimimo.com", "platform.xiaomimimo.com", "mimo.mi.com") or host.endswith(".xiaomimimo.com"):
        return url
    if host in ("claude.ai", "claude.com", "platform.claude.com") or host.endswith(".claude.ai") or host.endswith(".claude.com"):
        return url
    return ""
