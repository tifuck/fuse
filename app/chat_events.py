"""Authenticated, replayable WebSocket observations; commands remain CSRF-protected HTTP."""
import asyncio
import logging
import time
from collections import defaultdict
from urllib.parse import urlsplit

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from . import accounts, chat_store, db

logger = logging.getLogger(__name__)
router = APIRouter()
_subscribers = defaultdict(dict)
_loop = None
_closing = False
MAX_CONNECTIONS_PER_USER = 8


def notify(users):
    if _loop is None or _loop.is_closed():
        return
    def wake():
        for uid in users:
            for signal in _subscribers.get(uid, {}).values():
                signal.set()
    _loop.call_soon_threadsafe(wake)


def start():
    global _loop, _closing
    _loop = asyncio.get_running_loop()
    _closing = False


async def stop():
    global _closing
    _closing = True
    sockets = [ws for subscribers in _subscribers.values() for ws in subscribers]
    await asyncio.gather(*(ws.close(code=1012) for ws in sockets), return_exceptions=True)


async def close_session(uid, csrf):
    sockets = [ws for ws in _subscribers.get(uid, {}) if ws.session.get("csrf") == csrf]
    await asyncio.gather(*(ws.close(code=4401) for ws in sockets), return_exceptions=True)


def _origin(origin):
    try:
        parsed = urlsplit(origin)
        if parsed.scheme not in ("http", "https") or parsed.username or parsed.password or parsed.path not in ("", "/"):
            return None
        return parsed.scheme, parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError:
        return None


def replay_valid(uid, cursor):
    row = db.fetchone("SELECT cursor,floor FROM chat_event_cursors WHERE user_id=?", (uid,))
    return 0 <= cursor <= (row["cursor"] if row else 0) and cursor >= (row["floor"] if row else 0)


@router.websocket("/chat/events")
async def events(ws: WebSocket):
    expected = ("https" if ws.url.scheme == "wss" else "http") + "://" + ws.url.netloc
    uid = ws.session.get("uid")
    if _origin(ws.headers.get("origin", "")) != _origin(expected):
        logger.warning("chat/events rejected origin=%r expected=%r scheme=%r", ws.headers.get("origin", ""), expected, ws.url.scheme)
        await ws.close(code=4403)
        return
    if not uid or not accounts.get_user(uid):
        logger.warning("chat/events rejected auth uid=%r", uid)
        await ws.close(code=4401)
        return
    if _closing:
        await ws.close(code=1012)
        return
    live = _subscribers.get(uid, {})
    if len(live) >= MAX_CONNECTIONS_PER_USER:
        # Newest tab wins: retire the oldest socket instead of rejecting.
        oldest_ws = next(iter(live))
        evicted = _subscribers[uid].pop(oldest_ws, None)
        if evicted is not None:
            evicted.set()
        try:
            await oldest_ws.close(code=1012)
        except Exception:
            pass
    try:
        cursor = int(ws.query_params.get("cursor", "0"))
        if cursor < 0 or cursor > 2**53 - 1:
            raise ValueError
    except ValueError:
        logger.warning("chat/events rejected cursor=%r", ws.query_params.get("cursor"))
        await ws.close(code=4400)
        return
    await ws.accept()
    signal = asyncio.Event()
    _subscribers[uid][ws] = signal
    started = last_pong = time.monotonic()

    async def send(data):
        async with asyncio.timeout(5):
            await ws.send_json(data)

    async def receive():
        nonlocal last_pong
        while True:
            message = await ws.receive_text()
            if message != "pong":
                await ws.close(code=4400)
                return
            last_pong = time.monotonic()

    reader = asyncio.create_task(receive())
    reader.add_done_callback(lambda _: signal.set())
    try:
        if not replay_valid(uid, cursor):
            await send({"type": "resync_required"})
            return
        ready = False
        last_ping = time.monotonic()
        while not _closing and not reader.done():
            signal.clear()
            if not replay_valid(uid, cursor):
                await send({"type": "resync_required"})
                return
            rows = chat_store.events_after(uid, cursor, limit=64)
            if rows:
                await send({"type": "events", "events": rows})
                cursor = rows[-1]["id"]
                continue
            if not ready:
                await send({"type": "ready", "cursor": cursor})
                ready = True
            now = time.monotonic()
            if now - last_ping >= 15:
                if now - started > 1800 or now - last_pong > 60 or not accounts.get_user(uid):
                    await ws.close(code=4401 if not accounts.get_user(uid) else 1012)
                    return
                await send({"type": "ping"})
                last_ping = now
            # Local commits wake immediately. Timeout also checks disconnects/auth.
            try:
                await asyncio.wait_for(signal.wait(), timeout=15)
            except TimeoutError:
                pass
    except (WebSocketDisconnect, RuntimeError, TimeoutError, OSError):
        pass
    finally:
        reader.cancel()
        await asyncio.gather(reader, return_exceptions=True)
        _subscribers[uid].pop(ws, None)
        if not _subscribers[uid]:
            _subscribers.pop(uid, None)
        try:
            await ws.close()
        except (RuntimeError, WebSocketDisconnect, OSError):
            pass
