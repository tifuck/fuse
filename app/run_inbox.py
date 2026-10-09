"""Arrival signal for a live run's inbox.

Everything delivered into a generating run while it works lands in one
durable inbox on its chat_runs row and is drained at the next tool boundary
(chat_generation.inbox): user steers, parent follow-ups, subagent reports,
finished background commands, and monitor events. The row is the source of
truth; this module only tells a run that something arrived, so a call that
is blocked waiting (a foreground command, get_command_output,
get_subagent_output, a foreground spawn) can return early and let the
model see it at once instead of after the wait's full timeout.

Fuse serves from one process, so an in-memory signal reaches every waiter.
A signal lost to a restart costs only latency: the item is still durable and
is drained at the next boundary.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import threading

logger = logging.getLogger(__name__)

_lock = threading.Lock()
# run_id -> waiters: (loop, future, ids the waiter ignores)
_waiters: dict[str, list[tuple[asyncio.AbstractEventLoop, asyncio.Future, frozenset[str]]]] = {}


def _resolve(fut: asyncio.Future, item_id: str) -> None:
    if not fut.done():
        fut.set_result(item_id)


def arrived(run_id, item_id: str = "") -> None:
    """Wake waiters on run_id. Safe from any thread; never raises.

    item_id names what arrived (a child run id, a command job id, a steer id).
    A waiter ignores arrivals for the thing it is already waiting on: that
    result comes back as the wait's own return value.
    """
    if not run_id:
        return
    key = str(run_id)
    ident = str(item_id or "")
    with _lock:
        waiters = list(_waiters.get(key) or ())
    for loop, fut, ignore in waiters:
        if ident and ident in ignore:
            continue
        try:
            loop.call_soon_threadsafe(_resolve, fut, ident)
        except RuntimeError:
            # The waiter's loop closed (shutdown); nothing is waiting anymore.
            pass


@contextlib.contextmanager
def listen(run_id, *, ignore=()):
    """Yield a future that resolves with the item id of the next arrival.

    With no run_id the future never resolves, so callers can race it
    unconditionally.
    """
    loop = asyncio.get_running_loop()
    fut = loop.create_future()
    if not run_id:
        try:
            yield fut
        finally:
            fut.cancel()
        return
    key = str(run_id)
    entry = (loop, fut, frozenset(str(i) for i in ignore if i))
    with _lock:
        _waiters.setdefault(key, []).append(entry)
    try:
        yield fut
    finally:
        with _lock:
            rows = _waiters.get(key)
            if rows is not None:
                with contextlib.suppress(ValueError):
                    rows.remove(entry)
                if not rows:
                    _waiters.pop(key, None)
        if not fut.done():
            fut.cancel()


async def wait_or_arrival(awaitable, timeout: float, run_id, *, ignore=()):
    """Await awaitable up to timeout, returning early when run_id's inbox gets an item.

    Returns (finished, arrival): finished is True when awaitable completed;
    arrival is the arriving item id when the inbox cut the wait short ("" for
    a plain timeout). A future or task passed in is never cancelled, so an
    early return leaves the work being waited on running; a coroutine is
    wrapped here and cancelled if it did not finish.
    """
    owned = not asyncio.isfuture(awaitable)
    task = asyncio.ensure_future(awaitable)
    try:
        with listen(run_id, ignore=ignore) as inbox:
            done, _ = await asyncio.wait({task, inbox}, timeout=max(0.0, timeout), return_when=asyncio.FIRST_COMPLETED)
    finally:
        if owned and not task.done():
            task.cancel()
    if task in done:
        return True, ""
    if inbox in done and not inbox.cancelled():
        return False, str(inbox.result() or "") or "inbox"
    return False, ""


EARLY_RETURN_NOTE = (
    "Returned early: new background results arrived for you (delivered in the next message). "
    "Read them, then wait again if you still need this."
)
