"""Web Push (VAPID) for dashboard chat completions.

Each signed-in device subscribes once; a terminal root run fans a notification
out to every subscription the user owns, so a turn started on the desktop can
surface on a phone. VAPID keys persist in data/vapid.json — regenerating them
silently breaks every existing subscription.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
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


def _ready() -> bool:
    return HAVE_PYWEBPUSH and bool(vapid_public_key())


def _subject() -> str:
    try:
        host = urlsplit(settings.public_base_url or "").hostname
        if host:
            return f"mailto:alerts@{host}"
    except Exception:
        pass
    return "mailto:alerts@fuse"


def count_for(user_id: int) -> int:
    row = db.fetchone("SELECT COUNT(*) AS n FROM push_subscriptions WHERE user_id=?", (user_id,))
    return int(row["n"]) if row else 0


def subscriptions_for(user_id: int) -> list:
    return db.fetchall(
        "SELECT id, endpoint, p256dh, auth FROM push_subscriptions WHERE user_id=?",
        (user_id,),
    )


def save_subscription(user_id: int, endpoint: str, p256dh: str, auth: str, user_agent: str = "") -> None:
    db.execute(
        """INSERT INTO push_subscriptions (user_id, endpoint, p256dh, auth, user_agent, created_at)
           VALUES (?,?,?,?,?,?)
           ON CONFLICT(endpoint) DO UPDATE SET
             user_id=excluded.user_id, p256dh=excluded.p256dh, auth=excluded.auth,
             user_agent=excluded.user_agent""",
        (user_id, endpoint, p256dh, auth, user_agent, db.now()),
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


async def notify_user(user_id: int, payload: dict) -> int:
    """Fan one payload to every device; never raises."""
    if not _ready():
        return 0
    body = json.dumps(payload)
    sent = 0
    for row in subscriptions_for(user_id):
        try:
            ok = await asyncio.wait_for(asyncio.to_thread(_send_one, dict(row), body), 20)
        except Exception:
            ok = False
        sent += 1 if ok else 0
    return sent


async def notify_run_done(user_id: int, thread_id, status: str, content: str = "", error: str = "") -> int:
    thread = None
    try:
        thread = db.fetchone("SELECT title, public_id FROM chat_threads WHERE id=?", (thread_id,))
    except Exception:
        thread = None
    if thread is None:
        return 0
    public = thread["public_id"] or ""
    if not public:
        try:
            from . import chats

            public = chats.public_id_for(thread_id) or ""
        except Exception:
            public = ""
    title = (thread["title"] or "").strip()
    if status == "failed":
        body = error or "Generation failed."
    else:
        body = " ".join(str(content or "").split())
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
