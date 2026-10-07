"""Group-commit event publication (one FIFO queue, one committer task)."""
from __future__ import annotations

import logging
import asyncio
import collections
import threading

from . import chat_tx

logger = logging.getLogger(__name__)



# --- Group-commit publication -------------------------------------------------
# Live run/agent progress is published through one FIFO queue drained by a
# single committer task. Each batch is a tiny transaction (cursor upsert,
# inserts, cursor bump) that never carries run payloads, so the per-user
# cursor row is only ever held for a few small statements regardless of how
# many subagents stream concurrently. Durable-but-slow work (the chat_runs
# message/request rewrite) happens in separate, per-run transactions.

_PUB_BATCH = 128
_PUB_MAX_DEPTH = 2048
_pub_queue: collections.deque = collections.deque()
_pub_lock = threading.Lock()
_pub_loop: asyncio.AbstractEventLoop | None = None
_pub_gate: asyncio.Event | None = None
_pub_task: asyncio.Task | None = None
_pub_stopping = False


def start_publisher():
    """Bind the committer to the running loop (application lifespan)."""
    global _pub_loop, _pub_gate, _pub_task, _pub_stopping
    if _pub_gate is not None and _pub_task is not None and not _pub_task.done():
        return
    _pub_loop = asyncio.get_running_loop()
    _pub_stopping = False
    _pub_gate = asyncio.Event()
    _pub_task = _pub_loop.create_task(_pub_committer(), name="fuse-event-publisher")


async def stop_publisher():
    global _pub_task, _pub_stopping, _pub_loop, _pub_gate
    task = _pub_task
    if task is None:
        return
    with _pub_lock:
        _pub_stopping = True
    _pub_gate.set()
    try:
        # Accepted work drains before the database pool is closed. Cancelling
        # a to_thread waiter would leave its transaction running unobserved.
        await asyncio.shield(task)
    finally:
        if task.done():
            _pub_task = None
            _pub_loop = None
            _pub_gate = None


def _publish_submit(user_id, type, thread_id, payload, guard_run=None, future=None):
    """Enqueue one event; safe from worker threads. FIFO order is preserved."""
    with _pub_lock:
        if _pub_stopping:
            raise RuntimeError("Event publisher is stopping")
        if len(_pub_queue) >= _PUB_MAX_DEPTH:
            # Fail the flush explicitly; never drop an ordered patch or grow
            # an unbounded queue while storage is unavailable.
            raise RuntimeError("Event publication queue is full")
        _pub_queue.append((user_id, type, thread_id, payload, guard_run, future))
    loop = _pub_loop
    gate = _pub_gate
    if loop is not None and gate is not None:
        def _wake():
            gate.set()
        try:
            loop.call_soon_threadsafe(_wake)
        except RuntimeError:
            pass  # loop closed during shutdown; stop_publisher owns draining


def _pub_drain():
    batch = []
    with _pub_lock:
        while _pub_queue and len(batch) < _PUB_BATCH:
            batch.append(_pub_queue.popleft())
    return batch


def _commit_publications(batch, attempts=3):
    eids = []
    try:
        with chat_tx.transaction() as conn:
            # Phase 1: every run guard BEFORE any cursor upsert. Holding run
            # locks across the batch is safe (they are released only at
            # commit); acquiring a run row while holding a cursor row would
            # invert finish_run's lock order and deadlock under load.
            alive = []
            for _user_id, _type, _thread_id, _payload, guard_run, _future in batch:
                if guard_run is None:
                    alive.append(True)
                    continue
                row = conn.execute("SELECT status FROM chat_runs WHERE id=?", (guard_run,)).fetchone()
                alive.append(row is not None and row["status"] in chat_tx.ACTIVE)
            # Phase 2: FIFO event allocation under the per-user cursor locks.
            for index, (user_id, type, thread_id, payload, guard_run, _future) in enumerate(batch):
                if not alive[index]:
                    eids.append(None)
                    continue
                eids.append(chat_tx.event(conn, user_id, type, thread_id, payload))
    except Exception as exc:
        # MariaDB may pick the batch as a deadlock victim even under a
        # disciplined lock order; the whole transaction rolled back, so the
        # batch can simply retry.
        code = getattr(exc, "args", (None,))[0]
        retryable = code == 1213 or "Deadlock found" in str(exc)
        if retryable and attempts > 1:
            logger.warning("Event publication batch deadlocked; retrying (%d items)", len(batch))
            return _commit_publications(batch, attempts - 1)
        raise
    return eids


async def _pub_committer():
    while True:
        await _pub_gate.wait()
        _pub_gate.clear()
        while True:
            batch = _pub_drain()
            if not batch:
                if _pub_stopping:
                    return
                break
            eids = None
            error = None
            try:
                eids = await asyncio.to_thread(_commit_publications, batch)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - surfaced to every waiter
                error = exc
                logger.exception("Event publication batch failed (%d items)", len(batch))
            for index, (_user_id, _type, _thread_id, _payload, _guard_run, future) in enumerate(batch):
                if future is None or future.done():
                    continue
                if error is not None:
                    # Do not attach the active committer's traceback to an
                    # exception owned by another task; consumers may clear
                    # traceback frames while this coroutine is suspended.
                    future.set_exception(RuntimeError("event publication failed"))
                else:
                    future.set_result(eids[index] if eids else None)


def publish(user_id, type, thread_id=None, payload=None, guard_run=None, future=None):
    """Queue one event for group commit. Callers that need commit ordering
    pass an asyncio.Future (created on their loop) and await it.

    When the committer is not running (standalone scripts, tests, startup
    recovery), fall back to one direct tiny transaction instead of queueing
    into a drain that will never come.
    """
    if _pub_task is None or _pub_task.done() or _pub_gate is None:
        try:
            eid = None
            with chat_tx.transaction() as conn:
                eid = chat_tx.event(conn, user_id, type, thread_id, payload, guard_run=guard_run)
            if future is not None:
                _resolve_future(future, eid)
            return
        except Exception:
            if future is not None:
                _resolve_future(future, None, error=True)
            raise
    _publish_submit(user_id, type, thread_id, payload, guard_run=guard_run, future=future)


def _resolve_future(future, value, error=False):
    try:
        loop = future.get_loop()
    except Exception:
        return

    def _finish():
        if future.done():
            return
        if error:
            future.set_exception(RuntimeError("event publication failed"))
        else:
            future.set_result(value)

    try:
        loop.call_soon_threadsafe(_finish)
    except RuntimeError:
        pass  # caller's loop is gone; nothing is waiting
