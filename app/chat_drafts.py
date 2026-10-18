"""Unsent composer text, kept per user so every device shows the same drafts.

A key is a thread's public id, "" for the unsent new chat, or
"p:<host>:<project>" for an unsent project session (static/chat/state.js
draftKey). Attachments stay in the tab that picked them.
"""
from __future__ import annotations

from . import chat_tx, db
from .chat_schema import CONTENT_MAX, ChatValidationError

KEY_MAX = 191


def list_drafts(user_id: int, conn=None) -> dict:
    sql, args = "SELECT draft_key,text FROM chat_drafts WHERE user_id=?", (user_id,)
    rows = conn.execute(sql, args).fetchall() if conn is not None else db.fetchall(sql, args)
    return {row["draft_key"]: row["text"] for row in rows}


def set_draft(user_id: int, key, text, origin="") -> None:
    if not isinstance(key, str) or len(key) > KEY_MAX or any(ord(ch) < 32 for ch in key):
        raise ChatValidationError("invalid draft key")
    if not isinstance(text, str) or len(text) > CONTENT_MAX:
        raise ChatValidationError("draft text must be a string of at most %d characters" % CONTENT_MAX)
    origin = str(origin or "")[:64]
    with chat_tx.transaction() as conn:
        if text.strip():
            conn.execute(
                """INSERT INTO chat_drafts(user_id,draft_key,text,updated_at) VALUES(?,?,?,?)
                   ON CONFLICT(user_id,draft_key) DO UPDATE SET text=excluded.text,updated_at=excluded.updated_at""",
                (user_id, key, text, db.now()),
            )
        else:
            text = ""
            conn.execute("DELETE FROM chat_drafts WHERE user_id=? AND draft_key=?", (user_id, key))
        chat_tx.event(conn, user_id, "draft.updated", payload={"key": key, "text": text, "origin": origin})
