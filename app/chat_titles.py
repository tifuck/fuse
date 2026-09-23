"""Background retries for thread title generation.

Turn-time attempts cover the common case; this sweep picks up whatever is
still pending (failed attempts, offline models, legacy rows with raw user
text as the title) and keeps retrying with backoff until a title lands.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from . import chat_store, db

logger = logging.getLogger(__name__)

SWEEP_BATCH = 3
SWEEP_BACKOFF = timedelta(minutes=5)
_inflight: set[int] = set()
_tasks: set[asyncio.Task] = set()
_regen_inflight: set[int] = set()
_regen_tasks: set[asyncio.Task] = set()


def _backoff_cutoff() -> str:
    moment = datetime.now(timezone.utc) - SWEEP_BACKOFF
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


async def _generate(user_id: int, thread_id: int, model: str, prompt: str, placeholder: str) -> None:
    from . import accounts, chat_runtime, chat_runs

    try:
        owner = accounts.get_user(user_id)
        if owner is None:
            return
        title = await chat_runtime.generate_thread_title(
            chat_runs.internal_request(user_id), dict(owner), model, prompt
        )
        if not title:
            chat_store.mark_title_attempted(thread_id)
            return
        chat_store.apply_generated_title(user_id, thread_id, title, placeholder)
    except Exception:
        logger.exception("Title sweep failed for thread %s", thread_id)
        try:
            chat_store.mark_title_attempted(thread_id)
        except Exception:
            logger.exception("Title sweep bookkeeping failed for thread %s", thread_id)
    finally:
        _inflight.discard(thread_id)


async def sweep() -> None:
    """Queue title generation for a few pending threads; returns immediately."""
    cutoff = _backoff_cutoff()
    candidates = [
        item
        for item in chat_store.pending_title_threads(SWEEP_BATCH, attempted_before=cutoff)
        if item["id"] not in _inflight
    ]
    for item in candidates:
        _inflight.add(item["id"])
        task = asyncio.create_task(
            _generate(item["user_id"], item["id"], item["model"], item["prompt"], item["title"]),
            name=f"chat-title-sweep-{item['id']}",
        )
        _tasks.add(task)
        task.add_done_callback(_tasks.discard)


async def drain() -> None:
    """Cancel sweep work during shutdown."""
    for task in list(_tasks) + list(_regen_tasks):
        task.cancel()
    await asyncio.gather(*_tasks, *_regen_tasks, return_exceptions=True)
    _tasks.clear()
    _regen_tasks.clear()
    _inflight.clear()
    _regen_inflight.clear()


async def regenerate(user_id: int, thread_id: int) -> bool:
    """Regenerate a thread title from the whole conversation (explicit /title).

    Runs as a standalone upstream request built from the stored transcript;
    the thread's messages are never modified, so the conversation's upstream
    prompt cache stays intact. Returns True when a regeneration was attempted.
    """
    from . import accounts, chat_prompts, chats, chat_runtime, chat_runs

    if thread_id in _regen_inflight:
        return False
    _regen_inflight.add(thread_id)
    try:
        row = db.fetchone(
            "SELECT model, messages FROM chat_threads WHERE id=? AND user_id=?",
            (thread_id, user_id),
        )
        if row is None:
            return False
        try:
            transcript = chats.title_transcript(chats.parse_messages(row["messages"]))
        except Exception:
            logger.exception("Title transcript build failed for thread %s", thread_id)
            transcript = ""
        if not transcript.strip():
            chat_store.title_regen_failed(user_id, thread_id)
            return True
        owner = accounts.get_user(user_id)
        if owner is None:
            return False
        title = await chat_runtime.generate_thread_title(
            chat_runs.internal_request(user_id),
            dict(owner),
            row["model"] or "",
            transcript,
            system=chat_prompts.TITLE_REGEN_SYSTEM,
        )
        if not title:
            chat_store.title_regen_failed(user_id, thread_id)
            return True
        chat_store.apply_regenerated_title(user_id, thread_id, title)
        return True
    except Exception:
        logger.exception("Title regeneration failed for thread %s", thread_id)
        try:
            chat_store.title_regen_failed(user_id, thread_id)
        except Exception:
            logger.exception("Title regen failure event failed for thread %s", thread_id)
        return True
    finally:
        _regen_inflight.discard(thread_id)


def spawn_regen(user_id: int, thread_id: int) -> asyncio.Task:
    """Queue a background regeneration; the route returns immediately."""
    task = asyncio.create_task(regenerate(user_id, thread_id), name=f"chat-title-regen-{thread_id}")
    _regen_tasks.add(task)
    task.add_done_callback(_regen_tasks.discard)
    return task
