"""Thread title generation after a turn settles.

A chat shows a short preview of its first request while the first root turn
runs. Once that turn ends, chat_runs notifies this module and the chat is
named from the request, the result and the files touched. A periodic sweep
covers missed notifications, failures (with backoff) and legacy rows. After a
few failed attempts it supplies a deterministic label.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from . import chat_store, chats, db

logger = logging.getLogger(__name__)

SWEEP_BATCH = 3
SWEEP_BACKOFF = timedelta(minutes=5)
SWEEP_INTERVAL = 60
_inflight: set[int] = set()
_tasks: set[asyncio.Task] = set()
_regen_inflight: set[int] = set()
_regen_tasks: set[asyncio.Task] = set()
_due: set[int] = set()
_wake: asyncio.Event | None = None
_wake_loop: asyncio.AbstractEventLoop | None = None


def claim(user_id: int, thread_id: int, *, explicit=False) -> str:
    token = uuid4().hex
    until = (datetime.now(timezone.utc) + timedelta(seconds=120)).strftime("%Y-%m-%dT%H:%M:%SZ")
    with db.tx() as conn:
        cur = conn.execute("""UPDATE chat_threads SET title_job_token=?,title_job_until=? WHERE id=? AND user_id=?
                             AND (title_job_until='' OR title_job_until<?)""" + ("" if explicit else " AND title_state=''"),
                           (token, until, thread_id, user_id, db.now()))
        return token if cur.rowcount else ""


def release(thread_id: int, token: str, *, failed=False):
    db.execute("""UPDATE chat_threads SET title_job_token='',title_job_until='',
                  title_attempts=title_attempts+?,title_attempted_at=? WHERE id=? AND title_job_token=?""",
               (1 if failed else 0, db.now() if failed else "", thread_id, token))


def notify(thread_id: int) -> None:
    """A root turn ended: name the chat now instead of waiting for the sweep."""
    _due.add(int(thread_id))
    if _wake is not None and _wake_loop is not None:
        _wake_loop.call_soon_threadsafe(_wake.set)


def _backoff_cutoff() -> str:
    moment = datetime.now(timezone.utc) - SWEEP_BACKOFF
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


async def _generate(user_id: int, thread_id: int, model: str, prompt: str, placeholder: str, fallback: str) -> None:
    from . import accounts, chat_runtime, chat_runs

    token = await asyncio.to_thread(claim, user_id, thread_id)
    if not token:
        _inflight.discard(thread_id)
        return
    failed = False
    try:
        row = await asyncio.to_thread(db.fetchone, "SELECT title_attempts FROM chat_threads WHERE id=?", (thread_id,))
        if row and row["title_attempts"] >= chats.TITLE_SWEEP_ATTEMPTS:
            await asyncio.to_thread(chat_store.apply_generated_title, user_id, thread_id, fallback, placeholder,
                                    token=token, fallback=True)
            return
        owner = await asyncio.to_thread(accounts.get_user, user_id)
        if owner is None:
            failed = True
            return
        title = await chat_runtime.generate_thread_title(
            chat_runs.internal_request(user_id), dict(owner), model, prompt
        )
        if not title:
            failed = True
            return
        applied = await asyncio.to_thread(chat_store.apply_generated_title, user_id, thread_id, title, placeholder,
                                          token=token)
        # A rename settles title_state, so a None here is a miss worth backing off.
        failed = applied is None
    except Exception:
        logger.exception("Title sweep failed for thread %s", thread_id)
        failed = True
    finally:
        await asyncio.to_thread(release, thread_id, token, failed=failed)
        _inflight.discard(thread_id)


async def sweep() -> None:
    """Queue title generation for due and pending threads; returns immediately."""
    due = set(_due)
    _due.difference_update(due)
    cutoff = _backoff_cutoff()
    candidates = [
        item
        for item in await asyncio.to_thread(chat_store.pending_title_threads, max(SWEEP_BATCH, len(due)),
                                            attempted_before=cutoff, due=due)
        if item["id"] not in _inflight
    ]
    for item in candidates:
        _inflight.add(item["id"])
        task = asyncio.create_task(
            _generate(item["user_id"], item["id"], item["model"], item["prompt"], item["title"], item["fallback"]),
            name=f"chat-title-sweep-{item['id']}",
        )
        _tasks.add(task)
        task.add_done_callback(_tasks.discard)


async def loop() -> None:
    global _wake, _wake_loop
    _wake = asyncio.Event()
    _wake_loop = asyncio.get_running_loop()
    try:
        while True:
            _wake.clear()
            try:
                await sweep()
            except Exception:
                logger.exception("Title sweep cycle failed")
            try:
                await asyncio.wait_for(_wake.wait(), timeout=SWEEP_INTERVAL)
            except asyncio.TimeoutError:
                pass
    finally:
        _wake = None
        _wake_loop = None


async def drain() -> None:
    """Cancel sweep work during shutdown."""
    for task in list(_tasks) + list(_regen_tasks):
        task.cancel()
    await asyncio.gather(*_tasks, *_regen_tasks, return_exceptions=True)
    _tasks.clear()
    _regen_tasks.clear()
    _inflight.clear()
    _regen_inflight.clear()
    _due.clear()


async def regenerate(user_id: int, thread_id: int) -> bool:
    """Regenerate a thread title from the whole conversation (explicit /title).

    Runs as a standalone upstream request built from the stored transcript;
    the thread's messages are never modified, so the conversation's upstream
    prompt cache stays intact. Returns True when a regeneration was attempted.
    """
    from . import accounts, chat_runtime, chat_runs

    if thread_id in _regen_inflight:
        return False
    _regen_inflight.add(thread_id)
    token = await asyncio.to_thread(claim, user_id, thread_id, explicit=True)
    if not token:
        _regen_inflight.discard(thread_id)
        return False
    try:
        row = await asyncio.to_thread(db.fetchone,
            "SELECT model, messages FROM chat_threads WHERE id=? AND user_id=?",
            (thread_id, user_id),
        )
        if row is None:
            return False
        try:
            source = chats.title_source(chats.parse_messages(row["messages"]))
        except Exception:
            logger.exception("Title source build failed for thread %s", thread_id)
            source = ""
        if not source.strip():
            await asyncio.to_thread(chat_store.title_regen_failed, user_id, thread_id)
            return True
        owner = await asyncio.to_thread(accounts.get_user, user_id)
        if owner is None:
            return False
        title = await chat_runtime.generate_thread_title(
            chat_runs.internal_request(user_id),
            dict(owner),
            row["model"] or "",
            source,
        )
        if not title:
            await asyncio.to_thread(chat_store.title_regen_failed, user_id, thread_id)
            return True
        await asyncio.to_thread(chat_store.apply_regenerated_title, user_id, thread_id, title, token=token)
        return True
    except Exception:
        logger.exception("Title regeneration failed for thread %s", thread_id)
        try:
            await asyncio.to_thread(chat_store.title_regen_failed, user_id, thread_id)
        except Exception:
            logger.exception("Title regen failure event failed for thread %s", thread_id)
        return True
    finally:
        await asyncio.to_thread(release, thread_id, token)
        _regen_inflight.discard(thread_id)


def spawn_regen(user_id: int, thread_id: int) -> asyncio.Task:
    """Queue a background regeneration; the route returns immediately."""
    task = asyncio.create_task(regenerate(user_id, thread_id), name=f"chat-title-regen-{thread_id}")
    _regen_tasks.add(task)
    task.add_done_callback(_regen_tasks.discard)
    return task
