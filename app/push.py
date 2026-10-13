"""Web Push (VAPID) for dashboard chat completions.

Each signed-in device subscribes once; a terminal root run fans a notification
out to every subscription the user owns, so a turn started on the desktop can
surface on a phone. VAPID keys persist in data/vapid.json — regenerating them
silently breaks every existing subscription.

Payload bodies are visible on lock screens, so secret-looking material is
redacted before it leaves the server.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import re
from urllib.parse import urlsplit

from . import db
from .config import ROOT, settings

logger = logging.getLogger(__name__)

_KEY_PATH = ROOT / "data" / "vapid.json"

try:
    from pywebpush import WebPushException, webpush

    HAVE_PYWEBPUSH = True
except Exception:  # pragma: no cover - dependency missing
    webpush = None
    WebPushException = Exception
    HAVE_PYWEBPUSH = False

_keys: dict | None = None
MAX_SUBSCRIPTIONS = 20

# Subscribing stores an endpoint this server later POSTs to. Web Push
# endpoints always come from the browser's configured push service, so a
# closed allowlist is exact: anything else is SSRF bait.
_PUSH_HOSTS = frozenset({
    "fcm.googleapis.com",
    "android.googleapis.com",
    "updates.push.services.mozilla.com",
    "push.apple.com",
    "pushd.com",
})
_PUSH_HOST_SUFFIXES = (
    ".push.apple.com",
    ".notify.windows.com",
    ".push.windows.net",
)

_SECRET_RES = (
    re.compile(r"-----BEGIN[ A-Z]*PRIVATE KEY-----[\s\S]*?(?:-----END[ A-Z]*PRIVATE KEY-----|$)"),
    re.compile(r"(?i)\b(authorization|api[-_ ]?key|secret|token|password|passwd|private[-_ ]?key)\b\s*[:=]\s*\S+"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{16,}"),
    re.compile(r"\b(?:sk|rk|pk)-[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\b"),
    re.compile(r"\b[0-9a-fA-F]{32,}\b"),
    re.compile(r"(?<![A-Za-z0-9+/=_-])[A-Za-z0-9+/=_-]{40,}={0,2}(?![A-Za-z0-9+/=_-])"),
)


def valid_endpoint(endpoint) -> bool:
    """Only browser push services may be stored and POSTed to."""
    if not isinstance(endpoint, str) or not endpoint.startswith("https://") or len(endpoint) > 2048:
        return False
    try:
        parts = urlsplit(endpoint)
        host = (parts.hostname or "").lower().rstrip(".")
        port = parts.port
    except ValueError:
        return False
    if port not in (None, 443):
        return False
    return host in _PUSH_HOSTS or any(host.endswith(suffix) for suffix in _PUSH_HOST_SUFFIXES)


def _redact_secrets(text: str) -> str:
    out = text or ""
    for pattern in _SECRET_RES:
        out = pattern.sub("[redacted]", out)
    return out


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _load_keys() -> dict | None:
    """None = no file yet (safe to generate). {} = file exists but is corrupt."""
    if not _KEY_PATH.exists():
        return None
    try:
        raw = json.loads(_KEY_PATH.read_text())
    except Exception:
        raw = None
    if isinstance(raw, dict) and isinstance(raw.get("private_key"), str) and isinstance(raw.get("public_key"), str):
        return raw
    # Corrupt/torn file: never mint replacements — regenerating silently
    # breaks every existing subscription. Keep the file for repair from
    # backup and disable push until an operator intervenes.
    logger.critical(
        "VAPID key file %s is unreadable; push notifications are DISABLED. "
        "Restore this file from backup (do not delete it) to recover subscriptions.",
        _KEY_PATH,
    )
    return {}


def _atomic_write_keys(keys: dict) -> None:
    _KEY_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = _KEY_PATH.with_name(_KEY_PATH.name + ".tmp")
    data = json.dumps(keys).encode()
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, _KEY_PATH)
    dir_fd = os.open(_KEY_PATH.parent, os.O_RDONLY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)
    os.chmod(_KEY_PATH, 0o600)


def _generate_keys() -> dict | None:
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ec

        priv = ec.generate_private_key(ec.SECP256R1())
        raw = priv.private_numbers().private_value.to_bytes(32, "big")
        pub = priv.public_key().public_bytes(
            serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
        )
        keys = {"private_key": _b64url(raw), "public_key": _b64url(pub)}
        _atomic_write_keys(keys)
        return keys
    except Exception:
        logger.exception("VAPID key generation failed")
        return None


def vapid_public_key() -> str:
    global _keys
    if _keys is None:
        loaded = _load_keys()
        if loaded == {}:
            # Corrupt file: cache the sentinel so we do not spam the log, and
            # never fall through to generation.
            _keys = {}
            return ""
        _keys = loaded or (_generate_keys() or {})
    return _keys.get("public_key") or "" if isinstance(_keys, dict) else ""


def ready() -> bool:
    if not HAVE_PYWEBPUSH or not vapid_public_key():
        return False
    if not _subject():
        # Push services reject or drop VAPID claims without a reachable
        # contact; sending anyway only accumulates dead subscriptions.
        return False
    return True


def _subject() -> str:
    try:
        host = urlsplit(settings.public_base_url or "").hostname
        if host:
            return f"mailto:alerts@{host}"
    except Exception:
        pass
    return ""


def count_for(user_id: int) -> int:
    row = db.fetchone("SELECT COUNT(*) AS n FROM push_subscriptions WHERE user_id=?", (user_id,))
    return int(row["n"]) if row else 0


def owns(user_id: int, endpoint: str) -> bool:
    return db.fetchone(
        "SELECT 1 FROM push_subscriptions WHERE user_id=? AND endpoint=?", (user_id, endpoint)
    ) is not None


def subscriptions_for(user_id: int) -> list:
    return db.fetchall(
        "SELECT id, endpoint, p256dh, auth FROM push_subscriptions WHERE user_id=?",
        (user_id,),
    )


def save_subscription(user_id: int, endpoint: str, p256dh: str, auth: str, user_agent: str = "") -> None:
    """Store a device subscription. Raises ValueError when the endpoint is not
    a push service or already belongs to another account."""
    if not valid_endpoint(endpoint):
        raise ValueError("endpoint is not a supported push service")
    with db.tx() as conn:
        row = conn.execute("SELECT user_id FROM push_subscriptions WHERE endpoint=?", (endpoint,)).fetchone()
        if row is not None and int(row["user_id"]) != int(user_id):
            raise ValueError("endpoint is registered to another account")
        # The WHERE guard keeps a racing insert for another account from being
        # silently reassigned by this upsert.
        conn.execute(
            """INSERT INTO push_subscriptions (user_id, endpoint, p256dh, auth, user_agent, created_at)
               VALUES (?,?,?,?,?,?)
               ON CONFLICT(endpoint) DO UPDATE SET
                 user_id=excluded.user_id, p256dh=excluded.p256dh, auth=excluded.auth,
                 user_agent=excluded.user_agent
               WHERE push_subscriptions.user_id=excluded.user_id""",
            (user_id, endpoint, p256dh, auth, user_agent, db.now()),
        )
        rows = conn.execute(
            "SELECT id FROM push_subscriptions WHERE user_id=? ORDER BY created_at DESC, id DESC",
            (user_id,),
        ).fetchall()
        drop = [int(r["id"]) for r in rows[MAX_SUBSCRIPTIONS:]]
        if drop:
            conn.execute(
                f"DELETE FROM push_subscriptions WHERE id IN ({','.join('?' * len(drop))})",
                tuple(drop),
            )


def remove_subscription(user_id: int, endpoint: str) -> None:
    db.execute(
        "DELETE FROM push_subscriptions WHERE user_id=? AND endpoint=?",
        (user_id, endpoint),
    )


def _send_one(row, payload: str) -> bool:
    try:
        webpush(
            subscription_info={
                "endpoint": row["endpoint"],
                "keys": {"p256dh": row["p256dh"], "auth": row["auth"]},
            },
            data=payload,
            vapid_private_key=(_keys or {}).get("private_key") or "",
            vapid_claims={"sub": _subject()},
            # The caller's wait_for cannot stop this worker thread: without a
            # socket timeout a stalled endpoint pins a shared executor slot.
            timeout=15,
        )
        db.execute(
            "UPDATE push_subscriptions SET last_push_at=? WHERE id=?",
            (db.now(), row["id"]),
        )
        return True
    except WebPushException as exc:
        response = getattr(exc, "response", None)
        status = getattr(response, "status_code", None)
        if status in (404, 410):
            db.execute("DELETE FROM push_subscriptions WHERE endpoint=?", (row["endpoint"],))
        else:
            logger.warning("Web push rejected for %s: %s", row["endpoint"][:96], exc)
        return False
    except Exception as exc:
        logger.warning("Web push failed: %s", exc)
        return False


async def _send_bounded(row: dict, payload: str) -> bool:
    try:
        return bool(await asyncio.wait_for(asyncio.to_thread(_send_one, row, payload), 20))
    except Exception:
        return False


async def notify_user(user_id: int, payload: dict) -> int:
    """Fan one payload to every device concurrently; never raises."""
    if not ready():
        return 0
    body = json.dumps(payload)
    try:
        rows = [dict(row) for row in await asyncio.to_thread(subscriptions_for, user_id)]
    except Exception:
        # Fired from a bare task: an escaping error would only be an
        # unretrieved-exception warning, and the docstring promises none.
        logger.exception("Push subscriptions lookup failed for user %s", user_id)
        return 0
    results = await asyncio.gather(*(_send_bounded(row, body) for row in rows))
    return sum(1 for ok in results if ok)


async def notify_run_done(user_id: int, thread_id, status: str, content: str = "", error: str = "") -> int:
    thread = None
    try:
        thread = await asyncio.to_thread(db.fetchone, "SELECT title, public_id FROM chat_threads WHERE id=?", (thread_id,))
    except Exception:
        thread = None
    if thread is None:
        return 0
    public = thread["public_id"] or ""
    if not public:
        try:
            from . import chats

            public = await asyncio.to_thread(chats.public_id_for, thread_id) or ""
        except Exception:
            public = ""
    title = _redact_secrets((thread["title"] or "").strip())
    if status == "failed":
        body = _redact_secrets(error or "Generation failed.")
    else:
        body = " ".join(_redact_secrets(str(content or "")).split())
    return await notify_user(
        user_id,
        {
            "title": (f"Fuse · {title}" if title else "Fuse")[:140],
            "body": (body or "Reply finished.")[:220],
            "url": "/chat" + (f"?t={public}" if public else ""),
            "threadId": public,
            "status": status,
        },
    )
