"""In-process WebSocket hub for connector agents. One live socket per host."""
from __future__ import annotations

import asyncio
import json
import logging
import time
from uuid import uuid4

from . import hosts
from .hosts import HostError

logger = logging.getLogger(__name__)

_conns: dict[tuple[int, int], dict] = {}
_held: dict[tuple[int, int], dict] = {}
# Waiters for a host's socket, woken when it comes up (calls polled every
# 250 ms). One event per waiter: an event never outlives its loop.
_online: dict[tuple[int, int], set[asyncio.Event]] = {}
_background: set[asyncio.Task] = set()
_cancel_replay: dict[tuple[int, int], dict[str, float]] = {}
CANCEL_REPLAY_SECONDS = 3_660.0  # max exec deadline + reconnect slack
CANCEL_REPLAY_CAP = 4096
_lock = asyncio.Lock()
MAX_INFLIGHT = 128
HELLO_TIMEOUT = 15.0
HOLD_SECONDS = 8.0
# A connector reconnects by itself after its socket drops or Fuse restarts,
# usually within seconds (backoff plus TLS). A call in that gap waits for it
# rather than failing "agent is offline" on a host that is back moments
# later: resumed runs make their first tool call right after boot, before
# any connector is back. Only that window waits; a host gone longer fails fast.
RECONNECT_WINDOW = 60.0
RECONNECT_WAIT = 12.0
_started_at: float | None = None
_dropped_at: dict[tuple[int, int], float] = {}
RESULT_CHARS = 262_144  # transport budget, not the model's display budget
# Connector frame limit is 1 MiB. Measure encoded JSON bytes before sending.
MAX_PAYLOAD_CHARS = 900_000


class AgentError(HostError):
    pass


def mark_started() -> None:
    """App startup: every connector is reconnecting for the next minute."""
    global _started_at
    _started_at = time.monotonic()


def _reconnect_wait(key: tuple[int, int], timeout: float) -> float:
    """How long a call may wait for this host's socket to come back: the
    hold for parked jobs, else the reconnect window's wait, never longer
    than the call's own timeout."""
    if key in _held:
        return HOLD_SECONDS
    now = time.monotonic()
    for since in (_dropped_at.get(key), _started_at):
        if since is not None and 0 <= now - since < RECONNECT_WINDOW:
            return min(RECONNECT_WAIT, max(1.0, float(timeout)))
    return 0.0


async def _await_socket(key: tuple[int, int], seconds: float) -> dict | None:
    deadline = time.monotonic() + seconds
    event = asyncio.Event()
    waiters = _online.setdefault(key, set())
    waiters.add(event)
    try:
        while True:
            event.clear()  # before the check: a socket that registers after it still wakes us
            rec = _conns.get(key)
            if rec is not None and not rec.get("closed"):
                return rec
            left = deadline - time.monotonic()
            if left <= 0:
                return None
            try:
                await asyncio.wait_for(event.wait(), left)
            except asyncio.TimeoutError:
                return None
    finally:
        waiters.discard(event)
        if not waiters and _online.get(key) is waiters:
            _online.pop(key, None)


def _mark_online(key: tuple[int, int]) -> None:
    for event in _online.get(key, ()):
        event.set()


def is_online(user_id: int, host_id: int) -> bool:
    rec = _conns.get((int(user_id), int(host_id)))
    return bool(rec and rec.get("ws") is not None)


def connector_version(user_id: int, host_id: int) -> str:
    """The live socket's hello version (no DB read); "" when offline."""
    rec = _conns.get((int(user_id), int(host_id)))
    return str((rec or {}).get("ver") or "")


async def disconnect(user_id: int, host_id: int, reason: str = "disabled") -> None:
    key = (int(user_id), int(host_id))
    async with _lock:
        rec = _conns.pop(key, None)
        held = _held.pop(key, None)
        _dropped_at.pop(key, None)
    if rec is not None:
        await _finish_rec(rec, reason)
    if held is not None:
        task = held.get("task")
        if task is not None:
            task.cancel()
        for job_id in held.get("pending") or {}:
            _remember_cancel(key, job_id)
        _fail_pending(held.get("pending") or {})


async def close_user(user_id: int) -> None:
    uid = int(user_id)
    async with _lock:
        keys = [k for k in _conns if k[0] == uid]
        recs = [_conns.pop(k) for k in keys]
        held_keys = [k for k in _held if k[0] == uid]
        held = [_held.pop(k) for k in held_keys]
        for k in [k for k in _dropped_at if k[0] == uid]:
            _dropped_at.pop(k, None)
    for rec in recs:
        await _finish_rec(rec, "logout")
    for rec in held:
        task = rec.get("task")
        if task is not None:
            task.cancel()
        for job_id in rec.get("pending") or {}:
            _remember_cancel((rec["user_id"], rec["host_id"]), job_id)
        _fail_pending(rec.get("pending") or {})


async def drain() -> None:
    async with _lock:
        recs = list(_conns.values())
        _conns.clear()
        held = list(_held.values())
        _held.clear()
    for rec in recs:
        await _finish_rec(rec, "shutdown")
    for rec in held:
        task = rec.get("task")
        if task is not None:
            task.cancel()
        _fail_pending(rec.get("pending") or {})


_RESTARTED = "connector restarted; remote outcome is unknown"


def _same_process(prev: dict | None, inst: str) -> bool:
    """False when both sessions name their connector process (hello `inst`)
    and they differ: a restarted connector can never answer the old jobs."""
    before = str((prev or {}).get("inst") or "")
    return not (before and inst and before != inst)


def _fail_pending(pending: dict, reason: str = "agent is offline") -> None:
    for fut in list(pending.values()):
        if not fut.done():
            fut.set_exception(AgentError(reason))
    pending.clear()


async def _close_ws(ws, reason: str) -> None:
    if ws is None:
        return
    try:
        async with asyncio.timeout(1):
            await ws.send_json({"v": 1, "t": "bye", "reason": reason})
    except Exception:
        pass
    try:
        async with asyncio.timeout(1):
            await ws.close()
    except Exception:
        pass


async def _cancel_pending(ws, pending: dict) -> None:
    """Best-effort cancel frames so remote execs don't run orphaned."""
    if ws is None:
        return
    try:
        async with asyncio.timeout(2):
            for job_id in list(pending):
                await ws.send_json({"v": 1, "t": "cancel", "id": job_id})
    except Exception:
        return


async def _finish_rec(rec: dict, reason: str) -> None:
    pending = rec.get("pending") or {}
    key = (rec["user_id"], rec["host_id"])
    for job_id in pending:
        _remember_cancel(key, job_id)
    await _cancel_pending(rec.get("ws"), pending)
    _fail_pending(pending, "agent is offline")
    await _close_ws(rec.get("ws"), reason)


async def _hold_pending(key: tuple[int, int], pending: dict, chunks=None, inst: str = "") -> None:
    if not pending:
        return
    rec = {"pending": dict(pending), "chunks": {k: v for k, v in (chunks or {}).items() if k in pending}, "task": None, "user_id": key[0], "host_id": key[1], "inst": inst}
    pending.clear()
    async with _lock:
        live = _conns.get(key)
        if live is not None:
            if _same_process(live, inst):
                live.setdefault("pending", {}).update(rec["pending"])
                live.setdefault("chunks", {}).update(rec["chunks"])
                rec["pending"].clear()
        else:
            old = _held.pop(key, None)
            _held[key] = rec
    if live is not None:
        _fail_pending(rec["pending"], _RESTARTED)
        return
    if old:
        _fail_pending(old.get("pending") or {})
        task = old.get("task")
        if task is not None:
            task.cancel()

    async def expire():
        try:
            await asyncio.sleep(HOLD_SECONDS)
        except asyncio.CancelledError:
            return
        async with _lock:
            cur = _held.get(key)
            if cur is not rec:
                return
            live = _conns.get(key)
            parked = rec.get("pending") or {}
            rec["pending"] = {}
            _held.pop(key, None)
            if live is not None:
                live.setdefault("pending", {}).update(parked)
                live.setdefault("chunks", {}).update(rec["chunks"])
                return
        for job_id in parked:
            _remember_cancel(key, job_id)
        _fail_pending(parked, "agent connection lost; remote outcome is unknown")
        try:
            await asyncio.to_thread(hosts.mark_agent_offline, *key)
        except Exception:
            logger.exception("mark expired agent offline failed")

    rec["task"] = asyncio.create_task(expire())


def _take_held(key: tuple[int, int]) -> dict:
    rec = _held.pop(key, None)
    if rec is None:
        return {}
    task = rec.get("task")
    if task is not None:
        task.cancel()
    return rec.get("pending") or {}


async def call(user_id: int, host_id: int, op: str, args: dict | None = None, timeout: float = 30.0, on_chunk=None) -> dict:
    key = (int(user_id), int(host_id))
    rec = _conns.get(key)
    if rec is None or rec.get("closed"):
        wait = _reconnect_wait(key, timeout)
        rec = await _await_socket(key, wait) if wait > 0 else None
        if rec is None:
            raise AgentError("agent is offline")
    pending = rec.setdefault("pending", {})
    if len(pending) >= MAX_INFLIGHT:
        raise AgentError("agent is busy")
    job_id = uuid4().hex[:12]
    payload = {}
    if isinstance(args, dict):
        payload.update(args)
    payload.update({"v": 1, "t": "job", "id": job_id, "op": op})
    try:
        # Match Starlette's compact UTF-8 serialization (ensure_ascii=False).
        wire = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
        if len(wire) > MAX_PAYLOAD_CHARS:
            raise AgentError("payload too large")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise AgentError("payload is not serializable") from exc
    loop = asyncio.get_running_loop()
    fut: asyncio.Future = loop.create_future()
    pending[job_id] = fut
    chunks = rec.setdefault("chunks", {})
    if on_chunk is not None:
        chunks[job_id] = on_chunk
    try:
        async with asyncio.timeout(max(1.0, float(timeout))):
            try:
                await rec["ws"].send_json(payload)
            except Exception as exc:
                raise AgentError("agent is offline; remote outcome is unknown") from exc
            return await fut
    except (asyncio.TimeoutError, asyncio.CancelledError) as exc:
        await cancel(user_id, host_id, job_id)
        if isinstance(exc, asyncio.CancelledError):
            raise
        raise AgentError("agent timed out") from exc
    except AgentError:
        await cancel(user_id, host_id, job_id)
        raise
    finally:
        # Reconnect transfers futures into a different pending dictionary.
        pending.pop(job_id, None)
        chunks.pop(job_id, None)
        for owner in (_conns.get(key), _held.get(key)):
            if owner is not None:
                owner.get("pending", {}).pop(job_id, None)
                owner.get("chunks", {}).pop(job_id, None)
        if not fut.done():
            fut.cancel()


def _remember_cancel(key: tuple[int, int], job_id: str) -> None:
    now = time.monotonic()
    for owner, jobs in list(_cancel_replay.items()):
        for ident, expires in list(jobs.items()):
            if expires <= now:
                jobs.pop(ident, None)
        if not jobs:
            _cancel_replay.pop(owner, None)
    if sum(map(len, _cancel_replay.values())) >= CANCEL_REPLAY_CAP:
        owner = next(iter(_cancel_replay))
        jobs = _cancel_replay[owner]
        jobs.pop(next(iter(jobs)))
        if not jobs:
            _cancel_replay.pop(owner)
    _cancel_replay.setdefault(key, {})[job_id] = now + CANCEL_REPLAY_SECONDS


async def cancel(user_id: int, host_id: int, job_id: str) -> None:
    key = (int(user_id), int(host_id))
    _remember_cancel(key, job_id)
    rec = _conns.get(key)
    if rec is None:
        return
    try:
        async with asyncio.timeout(1):
            await rec["ws"].send_json({"v": 1, "t": "cancel", "id": job_id})
    except Exception:
        pass


def _exit_code(value) -> int | None:
    """The connector's exit status; anything that is not an integer (1e999,
    a string) is unknown rather than a crash further down."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():  # inf and nan are not
        return int(value)
    return None


def _clip(text) -> str:
    if not isinstance(text, str):
        text = "" if text is None else str(text)
    if len(text) > RESULT_CHARS:
        return text[:RESULT_CHARS]
    return text


async def handle_socket(ws, *, hello_timeout: float = HELLO_TIMEOUT) -> None:
    """First text frame is hello (code or token). Then job/res until close."""
    try:
        raw = await asyncio.wait_for(ws.receive_text(), timeout=hello_timeout)
    except Exception:
        await ws.close(code=4408)
        return
    try:
        msg = json.loads(raw)
    except (json.JSONDecodeError, RecursionError):
        await ws.close(code=4400)
        return
    if not isinstance(msg, dict) or msg.get("t") != "hello":
        await ws.close(code=4400)
        return
    try:
        user_id, host_id, alias, minted = await _hello(msg)
    except HostError as exc:
        err = {"v": 1, "t": "hello_err", "error": exc.detail}
        if exc.status_code == 401:
            # Connectors back off slowly on auth failures; the code spares
            # them guessing from the wording.
            err["code"] = "auth"
        try:
            await ws.send_json(err)
        except Exception:
            pass
        await ws.close(code=4401 if exc.status_code == 401 else 4400)
        return
    hello_ok = {"v": 1, "t": "hello_ok", "host_id": host_id, "alias": alias}
    if minted:
        hello_ok["token"] = minted
    try:
        await ws.send_json(hello_ok)
    except Exception:
        if minted:
            # hello_ok carried the only copy of the device token: undo the
            # pairing so the connector can retry with the same install code.
            try:
                await asyncio.to_thread(hosts.undo_pairing, user_id, host_id, str(msg.get("code") or ""))
            except Exception:
                logger.exception("Could not undo pairing for host %s", host_id)
        raise
    key = (int(user_id), int(host_id))
    inst = str(msg.get("inst") or "")[:64]
    version = str(msg.get("ver") or msg.get("version") or "")
    rec = {"ws": ws, "pending": {}, "user_id": int(user_id), "host_id": int(host_id), "inst": inst, "ver": version[:32]}
    orphaned: dict = {}
    async with _lock:
        old = _conns.pop(key, None)
        held = _held.get(key)
        if held:
            rec.setdefault("chunks", {}).update(held.get("chunks") or {})
        (rec["pending"] if _same_process(held, inst) else orphaned).update(_take_held(key))
        _conns[key] = rec
        _dropped_at.pop(key, None)
    _mark_online(key)
    if old is not None:
        (rec["pending"] if _same_process(old, inst) else orphaned).update(old.get("pending") or {})
        rec.setdefault("chunks", {}).update(old.get("chunks") or {})
        old.get("pending", {}).clear()
        await _close_ws(old.get("ws"), "replaced")
    _fail_pending(orphaned, _RESTARTED)
    # Bookkeeping DB writes run beside the receive loop: results for jobs
    # sent the moment the socket is registered must not wait on them.
    task = asyncio.create_task(_after_hello(user_id, host_id, msg))
    _background.add(task)
    task.add_done_callback(_background.discard)
    try:
        replay = _cancel_replay.get(key, {})
        await _cancel_pending(ws, {i: None for i, deadline in replay.items() if deadline > time.monotonic()})
        while True:
            try:
                text = await ws.receive_text()
            except KeyError:
                continue  # a binary frame: malformed, skipped like bad JSON
            try:
                data = json.loads(text)
            except (json.JSONDecodeError, RecursionError):
                continue
            if not isinstance(data, dict):
                continue
            kind = data.get("t")
            if kind == "pong":
                continue
            if kind == "ping":
                await ws.send_json({"v": 1, "t": "pong"})
                continue
            if kind == "chunk":
                job_id = str(data.get("id") or "")
                cb = rec.get("chunks", {}).get(job_id)
                if cb is not None:
                    try:
                        cb(str(data.get("stream") or "out"), str(data.get("data") or ""))
                    except Exception:
                        logger.debug("agent chunk callback failed", exc_info=True)
                continue
            if kind != "res":
                continue
            job_id = str(data.get("id") or "")
            replay = _cancel_replay.get(key)
            if replay is not None:
                replay.pop(job_id, None)  # terminal result acknowledges remote completion
                if not replay:
                    _cancel_replay.pop(key, None)
            fut = rec["pending"].pop(job_id, None)
            if fut is None or fut.done():
                continue
            fut.set_result(
                {
                    "ok": bool(data.get("ok")),
                    "exit": _exit_code(data.get("exit")),
                    "out": _clip(data.get("out")),
                    "err": _clip(data.get("err")),
                    "error": _clip(data.get("error")),
                    "timeout": bool(data.get("timeout")),
                    "termination_confirmed": data.get("termination_confirmed") is True,
                    "truncated": bool(data.get("truncated")) or any(
                        isinstance(data.get(field), str) and len(data[field]) > RESULT_CHARS
                        for field in ("out", "err")
                    ),
                    # Absence is legacy/unknown, never evidence of completeness.
                    "output_complete": data.get("truncated") in (False, 0) and all(
                        not isinstance(data.get(field), str) or len(data[field]) <= RESULT_CHARS
                        for field in ("out", "err")
                    ),
                }
            )
    except Exception as exc:
        from starlette.websockets import WebSocketDisconnect

        if isinstance(exc, WebSocketDisconnect):
            logger.debug("agent socket ended", exc_info=True)
        else:
            # Not a normal disconnect: a handler bug would otherwise look
            # exactly like the connector going away.
            logger.exception("agent socket loop failed")
    finally:
        # Mark before the pending leftover is handed off: a record whose
        # receive loop ended must not accept new jobs while awaiting cleanup.
        rec["closed"] = True
        current = _conns.get(key)
        if current is rec:
            _conns.pop(key, None)
            _dropped_at[key] = time.monotonic()
            leftover = rec.get("pending") or {}
            rec["pending"] = {}
            if leftover:
                # Connector workers retain results across transient reconnects.
                await _hold_pending(key, leftover, rec.get("chunks"), inst)
            else:
                try:
                    # The hello bookkeeping marks the host online: let it land
                    # first, or a quick drop would leave it "online".
                    if not task.done():
                        await asyncio.wait([task])
                    await asyncio.to_thread(hosts.mark_agent_offline, user_id, host_id)
                except Exception:
                    logger.exception("mark agent offline failed")


async def _after_hello(user_id: int, host_id: int, msg: dict) -> None:
    try:
        await asyncio.to_thread(
            hosts.touch_agent,
            user_id,
            host_id,
            version=str(msg.get("ver") or msg.get("version") or ""),
            hostname=str(msg.get("hostname") or ""),
            username=str(msg.get("user") or msg.get("username") or ""),
            os_name=str(msg.get("os") or ""),
            arch=str(msg.get("arch") or ""),
        )
    except Exception:
        logger.exception("record agent hello failed")
    try:
        await asyncio.to_thread(hosts.enable_coding_if_off, user_id)
    except Exception:
        logger.exception("enable coding after agent hello failed")


async def _hello(msg: dict) -> tuple[int, int, str, str]:
    code = str(msg.get("code") or "").strip()
    token = str(msg.get("token") or "").strip()
    meta = {
        "hostname": msg.get("hostname") or "",
        "username": msg.get("user") or msg.get("username") or "",
        "ver": msg.get("ver") or msg.get("version") or "",
        "os": msg.get("os") or "",
        "arch": msg.get("arch") or "",
    }
    if code:
        view, minted = await asyncio.to_thread(hosts.redeem_pairing, code, meta)
        return int(view["user_id"]), int(view["id"]), view["alias"], minted
    if token:
        row = await asyncio.to_thread(hosts.host_by_device_token, token)
        if row is None:
            raise AgentError("invalid token", 401)
        return int(row["user_id"]), int(row["id"]), row["alias"], ""
    raise AgentError("install code or token required", 401)
