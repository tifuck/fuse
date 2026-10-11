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


async def reauth_user(uid):
    """Drop every socket for uid with a retryable code: each reconnects with
    its current cookie, and a cookie a password change revoked gets 4401."""
    sockets = list(_subscribers.get(uid, {}))
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
    # A high-water mark alone cannot validate replay: the retention floor
    # remains authoritative after pruning. Read both off the event loop.
    row = db.fetchone("SELECT cursor,floor FROM chat_event_cursors WHERE user_id=?", (uid,))
    high = int(row["cursor"]) if row else 0
    floor = int(row["floor"]) if row else 0
    return floor <= cursor <= high


def _downgrade_progress(row, viewed):
    """Compact an agent.progress event for a subagent the socket is not viewing.

    The full patch stream of every subagent would force the client to replay
    patches it mostly discards; the viewed agent streams in full while the
    rest surface as a tick (enough for list rows and tool chips). Pending
    permission asks always pass through — the dock queue is fed from them.
    """
    payload = row["payload"]
    if row["type"] != "agent.progress" or not isinstance(payload, dict):
        return row
    run_id = payload.get("run_id")
    if not isinstance(run_id, str) or run_id == viewed:
        return row
    tick = {"run_id": run_id}
    if "current" in payload:
        tick["current"] = payload["current"]
    events = payload.get("events") if isinstance(payload.get("events"), list) else []
    patch = events[0].get("fuse") if events and isinstance(events[0], dict) else None
    if isinstance(patch, dict) and patch.get("event") == "message.patch":
        content = patch.get("content")
        if isinstance(content, dict):
            tick["len"] = int(content.get("from") or 0) + len(content.get("text") or "")
        if type(patch.get("streamOut")) is int:
            tick["streamOut"] = patch["streamOut"]
        if patch.get("elapsedMs") is not None:
            tick["elapsedMs"] = patch["elapsedMs"]
    tools = payload.get("tools")
    if isinstance(tools, list):
        tick["tools"] = len(tools)
        for trace in reversed(tools) if "current" not in tick else []:
            if isinstance(trace, dict) and trace.get("status") in ("running", "progress", "queued"):
                tick["current"] = str(trace.get("name") or "")
                break
    if payload.get("parent_run_id"):
        tick["parent_run_id"] = payload["parent_run_id"]
        tick["root_run_id"] = payload.get("root_run_id")
    permissions = payload.get("permissions")
    if isinstance(permissions, list):
        # Resolution is as important as the ask: filtering to pending leaves
        # stale cards visible when an unviewed child receives a decision.
        tick["permissions"] = permissions
    return {"id": row["id"], "type": "agent.tick", "thread_id": row["thread_id"], "payload": tick}


@router.websocket("/chat/events")
async def events(ws: WebSocket):
    expected = ("https" if ws.url.scheme == "wss" else "http") + "://" + ws.url.netloc
    uid = ws.session.get("uid")
    if _origin(ws.headers.get("origin", "")) != _origin(expected):
        logger.warning("chat/events rejected origin=%r expected=%r scheme=%r", ws.headers.get("origin", ""), expected, ws.url.scheme)
        await ws.close(code=4403)
        return
    stamp = ws.session.get("ph")
    if not uid or not await asyncio.to_thread(accounts.session_user, uid, stamp):
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
    view = {"run_id": None}

    async def send(data):
        async with asyncio.timeout(5):
            await ws.send_json(data)

    async def receive():
        nonlocal last_pong
        while True:
            message = await ws.receive_text()
            if message == "pong":
                last_pong = time.monotonic()
                continue
            # The client names the subagent whose full patch stream it renders;
            # every other live subagent arrives as a compact agent.tick.
            try:
                import json as _json

                data = _json.loads(message)
            except (ValueError, TypeError):
                await ws.close(code=4400)
                return
            if not isinstance(data, dict) or data.get("type") != "view":
                await ws.close(code=4400)
                return
            run_id = data.get("run_id")
            view["run_id"] = run_id if isinstance(run_id, str) and run_id else None

    reader = asyncio.create_task(receive())
    reader.add_done_callback(lambda _: signal.set())
    try:
        if not await asyncio.to_thread(replay_valid, uid, cursor):
            await send({"type": "resync_required"})
            return
        ready = False
        last_ping = time.monotonic()
        while not _closing and not reader.done():
            signal.clear()
            rows = await asyncio.to_thread(chat_store.events_after, uid, cursor, limit=64)
            # Validate after the read: a prune committed between a check and
            # the read deletes events past the cursor and moves the floor, so
            # only a floor read after the batch proves the batch is gap-free.
            if not await asyncio.to_thread(replay_valid, uid, cursor):
                await send({"type": "resync_required"})
                return
            if rows:
                viewed = view["run_id"]
                rows = [_downgrade_progress(row, viewed) for row in rows]
                await send({"type": "events", "events": rows})
                cursor = rows[-1]["id"]
                continue
            if not ready:
                await send({"type": "ready", "cursor": cursor})
                ready = True
            now = time.monotonic()
            if now - last_ping >= 15:
                owner = await asyncio.to_thread(accounts.session_user, uid, stamp)
                if now - started > 1800 or now - last_pong > 60 or not owner:
                    await ws.close(code=4401 if not owner else 1012)
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
