"""Queued composer messages, held by the server so they send with no tab open.

The composer queues a message while a turn runs. The tab keeps a copy for
display (static/chat/turns/queue.js) and mirrors every item here. When the
thread's root run ends, the wake path (chat_runs.start_queued) starts the
first item that is not held as the user's own turn, command id `<id>:turn`,
so a tab sending the same item at the same moment is deduped by the command
ledger. A user Stop holds what is queued then; held items wait for Send now.
"""
from __future__ import annotations

import json

from . import chat_tx, db
from .chat_schema import (CONTENT_MAX, ChatValidationError, _text, clip_context, parse_attachments,
                          parse_reasoning_effort, parse_thread_tools)

ID_MAX = 100
PER_THREAD = 50


def command_id(item_id: str) -> str:
    return item_id + ":turn"


def put(user_id: int, thread_id: int, raw: dict, *, release: bool = False):
    """Insert or update one item. Returns whether it is held, or None when its
    turn was already accepted (a late copy must never queue it again)."""
    item_id = raw.get("id")
    if not isinstance(item_id, str) or not item_id or len(item_id) > ID_MAX or any(ord(ch) <= 32 for ch in item_id):
        raise ChatValidationError("invalid queue item id")
    text = _text(raw.get("text") if raw.get("text") is not None else "", "text", CONTENT_MAX)
    attachments = parse_attachments(raw.get("attachments"), strict=True)
    if not text.strip() and not attachments:
        raise ChatValidationError("text cannot be empty")
    body = {
        "text": text,
        "attachments": attachments,
        "model": _text(raw["model"], "model", 200) if raw.get("model") is not None else None,
        "tools": parse_thread_tools(raw["tools"], strict=True) if raw.get("tools") is not None else None,
        "context": clip_context(raw["context"], strict=True) if raw.get("context") is not None else None,
        "reasoning_effort": parse_reasoning_effort(raw.get("reasoning_effort"), strict=True),
    }
    order = [raw.get("priority") or 0, raw.get("created") or 0]
    if any(type(value) is not int or not 0 <= value < 2 ** 62 for value in order):
        raise ChatValidationError("priority and created must be non-negative integers")
    with chat_tx.transaction() as conn:
        if conn.execute("SELECT 1 FROM chat_commands WHERE user_id=? AND command_id=?",
                        (user_id, command_id(item_id))).fetchone():
            conn.execute("DELETE FROM chat_queue WHERE thread_id=? AND id=?", (thread_id, item_id))
            return None
        row = conn.execute("SELECT held, priority FROM chat_queue WHERE thread_id=? AND id=?",
                           (thread_id, item_id)).fetchone()
        if row is None and conn.execute("SELECT COUNT(*) FROM chat_queue WHERE thread_id=?",
                                        (thread_id,)).fetchone()[0] >= PER_THREAD:
            raise ChatValidationError("queue is full", 413)
        held = 0 if release or row is None else int(row["held"])
        # Send now only raises it; a slower earlier write must not undo that.
        priority = max(order[0], int(row["priority"])) if row is not None else order[0]
        conn.execute(
            """INSERT INTO chat_queue(thread_id,id,user_id,body,priority,created,held,updated_at) VALUES(?,?,?,?,?,?,?,?)
               ON CONFLICT(thread_id,id) DO UPDATE SET body=excluded.body,priority=excluded.priority,
                 held=excluded.held,updated_at=excluded.updated_at""",
            (thread_id, item_id, user_id, json.dumps(body), priority, order[1], held, db.now()),
        )
    return bool(held)


def delete(user_id: int, thread_id: int, item_id: str) -> None:
    db.execute("DELETE FROM chat_queue WHERE thread_id=? AND id=? AND user_id=?", (thread_id, item_id, user_id))


def hold(user_id: int, thread_id: int, item_id: str | None = None, *, before: int | None = None) -> None:
    """Hold one item, or what a user Stop holds: everything queued by the
    click (`before`, the tab's clock like `created`). Typed while stopping is
    a new instruction and still sends."""
    if item_id is not None:
        db.execute("UPDATE chat_queue SET held=1 WHERE thread_id=? AND id=? AND user_id=?", (thread_id, item_id, user_id))
    elif before is not None:
        db.execute("UPDATE chat_queue SET held=1 WHERE thread_id=? AND user_id=? AND created<=?", (thread_id, user_id, before))
    else:
        db.execute("UPDATE chat_queue SET held=1 WHERE thread_id=? AND user_id=?", (thread_id, user_id))


def next_item(user_id: int, thread_id: int) -> dict | None:
    """The item that sends next: Send now's priority first, then queue order
    (the tab sorts the same way, static/chat/turns/queue-store.js)."""
    row = db.fetchone(
        """SELECT id, body FROM chat_queue WHERE thread_id=? AND user_id=? AND held=0
           ORDER BY priority DESC, created, id LIMIT 1""",
        (thread_id, user_id),
    )
    return {"id": row["id"], **json.loads(row["body"])} if row else None


def pending_threads() -> list[dict]:
    return [dict(row) for row in db.fetchall("SELECT DISTINCT user_id, thread_id FROM chat_queue WHERE held=0")]
