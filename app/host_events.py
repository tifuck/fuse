"""Session WebSocket for the Projects page. Snapshot plus live host/pending updates."""
from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from urllib.parse import urlsplit

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from . import accounts, hosts

router = APIRouter()
_subscribers: dict[int, dict] = defaultdict(dict)
_loop = None
_closing = False
MAX_CONNECTIONS_PER_USER = 8


def publish(user_id, msg):
    if _loop is None or _loop.is_closed() or user_id is None:
        return
    uid = int(user_id)
    payload = msg if isinstance(msg, dict) else {"type": "noop"}

    def wake():
        for queue in list(_subscribers.get(uid, {}).values()):
            try:
                queue.put_nowait(payload)
            except Exception:
                pass

    try:
        _loop.call_soon_threadsafe(wake)
    except RuntimeError:
        pass


def start():
    global _loop, _closing
    _loop = asyncio.get_running_loop()
    _closing = False


async def stop():
    global _closing
    _closing = True
    sockets = [ws for subs in _subscribers.values() for ws in subs]
    await asyncio.gather(*(ws.close(code=1012) for ws in sockets), return_exceptions=True)


async def close_user(uid):
    sockets = list(_subscribers.get(uid, {}))
    await asyncio.gather(*(ws.close(code=4401) for ws in sockets), return_exceptions=True)


def _origin(origin):
    try:
        parsed = urlsplit(origin)
        if parsed.scheme not in ("http", "https") or parsed.username or parsed.password or parsed.path not in ("", "/"):
            return None
        return parsed.scheme, parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError:
        return None


@router.websocket("/hosts/events")
async def events(ws: WebSocket):
    expected = ("https" if ws.url.scheme == "wss" else "http") + "://" + ws.url.netloc
    uid = ws.session.get("uid")
    if _origin(ws.headers.get("origin", "")) != _origin(expected):
        await ws.close(code=4403)
        return
    if not uid or not accounts.get_user(uid):
        await ws.close(code=4401)
        return
    if _closing or len(_subscribers.get(uid, {})) >= MAX_CONNECTIONS_PER_USER:
        await ws.close(code=4429)
        return
    await ws.accept()
    queue: asyncio.Queue = asyncio.Queue(maxsize=64)
    _subscribers[uid][ws] = queue
    last_pong = time.monotonic()

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
    try:
        bundle = hosts.list_bundle(uid)
        await send(
            {
                "type": "ready",
                "hosts": bundle.get("hosts") or [],
                "pending": bundle.get("pending") or [],
                "vault": bundle.get("vault") or "locked",
                "base": bundle.get("base") or "",
            }
        )
        last_ping = time.monotonic()
        started = time.monotonic()
        while not _closing and not reader.done():
            now = time.monotonic()
            if now - last_ping >= 15:
                if now - started > 1800 or now - last_pong > 60 or not accounts.get_user(uid):
                    await ws.close(code=4401 if not accounts.get_user(uid) else 1012)
                    return
                await send({"type": "ping"})
                last_ping = now
            try:
                msg = await asyncio.wait_for(queue.get(), timeout=15)
            except TimeoutError:
                continue
            await send(msg)
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
