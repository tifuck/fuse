"""Durable, display-only summaries of settled dashboard work episodes.

Activity invalidates a generation; automated wakes retain the initiating user
message. Jobs survive restarts, and leases plus a final generation check prevent
late output from being published. None of this metadata enters model history.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from . import chat_store, chats, db

logger = logging.getLogger(__name__)
MAX_ATTEMPTS = 3
CONCURRENCY = 3
IDLE_SECONDS = 60
LEASE_SECONDS = 90
_tasks: dict[int, asyncio.Task] = {}
_wake: asyncio.Event | None = None
_wake_loop: asyncio.AbstractEventLoop | None = None


def init_schema(conn):
    db._ensure_column(conn, "users", "chat_recaps", "INTEGER NOT NULL DEFAULT 1")
    db._ensure_column(conn, "chat_threads", "recap_epoch", "INTEGER NOT NULL DEFAULT 0")
    db._ensure_column(conn, "chat_threads", "recap_start_id", "TEXT NOT NULL DEFAULT ''")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS chat_recaps (
            id INTEGER PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            thread_id INTEGER NOT NULL REFERENCES chat_threads(id) ON DELETE CASCADE,
            epoch INTEGER NOT NULL,
            episode_id TEXT NOT NULL DEFAULT '',
            anchor_id TEXT NOT NULL DEFAULT '',
            fingerprint TEXT NOT NULL DEFAULT '',
            text TEXT NOT NULL DEFAULT '',
            state TEXT NOT NULL DEFAULT 'pending',
            manual INTEGER NOT NULL DEFAULT 0,
            attempts INTEGER NOT NULL DEFAULT 0,
            lease_token TEXT NOT NULL DEFAULT '',
            next_at TEXT NOT NULL DEFAULT '',
            quiet_since TEXT NOT NULL DEFAULT '',
            model TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL,
            UNIQUE(thread_id, epoch)
        );
        CREATE INDEX IF NOT EXISTS chat_recaps_pending ON chat_recaps(state, next_at);
    """)


def later(seconds: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%SZ")


def kick():
    if _wake is not None and _wake_loop is not None:
        _wake_loop.call_soon_threadsafe(_wake.set)


def activity(conn, user_id: int, thread_id: int):
    """Called in the same transaction as start/resume/edit, before publication."""
    conn.execute("UPDATE chat_threads SET recap_epoch=recap_epoch+1 WHERE id=? AND user_id=?", (thread_id, user_id))
    row = conn.execute("SELECT recap_epoch,recap_start_id FROM chat_threads WHERE id=? AND user_id=?", (thread_id, user_id)).fetchone()
    if row is None:
        return
    conn.execute("""UPDATE chat_recaps SET state='stale',lease_token='' WHERE thread_id=?
                    AND (state IN ('pending','generating') OR (state='ready' AND episode_id=?))""",
                 (thread_id, row["recap_start_id"]))
    if not row["recap_start_id"]:
        return  # Legacy histories are never automatically backfilled.
    owner = conn.execute("SELECT chat_recaps FROM users WHERE id=?", (user_id,)).fetchone()
    conn.execute("""INSERT INTO chat_recaps(user_id,thread_id,epoch,episode_id,state,next_at,updated_at)
                    VALUES(?,?,?,?,?,?,?)""",
                 (user_id, thread_id, row["recap_epoch"], row["recap_start_id"],
                  "pending" if owner and owner["chat_recaps"] else "disabled", db.now(), db.now()))


def disable(conn, user_id: int):
    conn.execute("""UPDATE chat_recaps SET state='disabled',lease_token='',updated_at=?
                    WHERE user_id=? AND state IN ('pending','generating')""", (db.now(), user_id))


def terminal_activity(conn, user_id: int, thread_id: int):
    """Observe settlement anew after a root/child/command stops."""
    conn.execute("UPDATE chat_recaps SET quiet_since='',next_at=? WHERE user_id=? AND thread_id=? AND state='pending'",
                 (db.now(), user_id, thread_id))


def _settled(conn, user_id: int, thread_id: int) -> bool:
    if conn.execute("SELECT 1 FROM chat_runs WHERE user_id=? AND thread_id=? AND status IN ('queued','running') LIMIT 1",
                    (user_id, thread_id)).fetchone():
        return False
    # Completed children are not finished work until their parent has consumed
    # their report. An exhausted delivery is a terminal failure, not a wait.
    root = conn.execute("SELECT status FROM chat_runs WHERE thread_id=? AND parent_run_id IS NULL ORDER BY created_at DESC,updated_at DESC LIMIT 1",
                        (thread_id,)).fetchone()
    stopped = root and root["status"] == "cancelled"
    if not stopped and conn.execute("""SELECT 1 FROM chat_runs WHERE user_id=? AND thread_id=? AND parent_run_id IS NOT NULL
                       AND COALESCE(json_extract(request,'$.agent.background'),0)=1
                       AND COALESCE(json_extract(request,'$.agent.wake_sent'),0)=0
                       AND COALESCE(json_extract(request,'$.agent.wake_attempts'),0)<2 LIMIT 1""",
                    (user_id, thread_id)).fetchone():
        return False
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='command_jobs'").fetchone():
        return True
    if conn.execute("""SELECT 1 FROM command_jobs WHERE user_id=? AND thread_id=?
                       AND status NOT IN ('completed','timed_out','cancelled','interrupted','lost') LIMIT 1""",
                    (user_id, thread_id)).fetchone():
        return False
    if not stopped and conn.execute("""SELECT 1 FROM command_jobs c WHERE c.user_id=? AND c.thread_id=? AND c.notify=1 AND c.delivered=0
                       AND NOT EXISTS(SELECT 1 FROM chat_runs r WHERE r.id=c.run_id AND r.parent_run_id IS NOT NULL) LIMIT 1""",
                    (user_id, thread_id)).fetchone():
        return False
    return True


def episode(messages: list[dict], start_id: str = "") -> tuple[str, str]:
    """Retain the user's goal, later direction and outcomes, without reasoning/log dumps."""
    start = next((i for i, m in enumerate(messages) if m.get("id") == start_id), None)
    if start is None:
        start = next((i for i in range(len(messages)-1, -1, -1)
                      if messages[i].get("role") == "user" and not messages[i].get("auto")), None)
    if start is None:
        return "", ""
    window = messages[start:]
    if not window or window[-1].get("role") != "assistant":
        return "", ""
    lines = []
    selected = [window[0], *window[max(1, len(window)-7):]]
    for msg in selected:
        label = "Background report" if msg.get("auto") else str(msg.get("role") or "Result")
        text = chats.clip_metadata_text(chats._message_plain_text(msg.get("content")), 650)
        if text:
            lines.append(f"{label}: {text}")
        if msg.get("error"):
            lines.append("Error: " + chats.clip_metadata_text(str(msg["error"]), 240))
        if msg.get("stopped"):
            lines.append("Status: interrupted; completion is not verified")
        for tool in (msg.get("tools") or [])[-4:]:
            detail = chats.clip_metadata_text(str(tool.get("error") or tool.get("summary") or tool.get("preview") or ""), 160)
            if detail:
                lines.append(f"Tool {tool.get('name', 'result')} ({tool.get('status', 'unknown')}): {detail}")
    return str(window[-1].get("id") or ""), chats.clip_metadata_text("\n".join(lines), 6000)


def attach(conn, user_id: int, thread_id: int, view: dict) -> dict:
    anchors = {m.get("id") for m in view.get("messages", [])}
    rows = conn.execute("""SELECT anchor_id,text FROM chat_recaps WHERE user_id=? AND thread_id=? AND state='ready'
                           ORDER BY epoch""", (user_id, thread_id)).fetchall()
    view["recaps"] = [{"anchor_id": r["anchor_id"], "text": r["text"]} for r in rows if r["anchor_id"] in anchors]
    return view


def request_manual(user_id: int, thread_id: int):
    with chat_store.transaction() as conn:
        row = conn.execute("SELECT messages FROM chat_threads WHERE id=? AND user_id=?", (thread_id, user_id)).fetchone()
        if row is None:
            raise chats.ChatValidationError("Chat not found", 404)
        if not _settled(conn, user_id, thread_id):
            raise chats.ChatValidationError("Wait for background work and its results to finish before requesting a recap", 409)
        messages = chats.parse_messages(row["messages"])
        anchor, source = episode(messages)
        if not anchor or not source:
            raise chats.ChatValidationError("Nothing to recap yet", 400)
        start = next(m["id"] for m in reversed(messages) if m.get("role") == "user" and not m.get("auto"))
        conn.execute("UPDATE chat_threads SET recap_start_id=? WHERE id=?", (start, thread_id))
        activity(conn, user_id, thread_id)
        conn.execute("UPDATE chat_recaps SET state='pending',manual=1 WHERE thread_id=? AND epoch=(SELECT recap_epoch FROM chat_threads WHERE id=?)",
                     (thread_id, thread_id))
    kick()


def claim(job_id: int):
    token = uuid4().hex
    locator = db.fetchone("SELECT thread_id FROM chat_recaps WHERE id=?", (job_id,))
    if locator is None:
        return None
    with db.tx() as conn:
        conn.execute("UPDATE chat_threads SET recap_epoch=recap_epoch WHERE id=?", (locator["thread_id"],))
        job = conn.execute("SELECT * FROM chat_recaps WHERE id=?", (job_id,)).fetchone()
        if job is None or job["state"] not in ("pending", "generating") or job["next_at"] > db.now() or job["attempts"] >= MAX_ATTEMPTS:
            return None
        # Lock only the thread first. Run reads here intentionally do not use
        # BEGIN IMMEDIATE's run-row locking (supervisor lock order is run->thread).
        row = conn.execute("SELECT * FROM chat_threads WHERE id=? AND user_id=?", (job["thread_id"], job["user_id"])).fetchone()
        if row is None or row["recap_epoch"] != job["epoch"]:
            conn.execute("UPDATE chat_recaps SET state='stale' WHERE id=?", (job_id,))
            return None
        owner = conn.execute("SELECT * FROM users WHERE id=?", (job["user_id"],)).fetchone()
        if not owner or (not job["manual"] and not owner["chat_recaps"]):
            conn.execute("UPDATE chat_recaps SET state='disabled',lease_token='' WHERE id=?", (job_id,))
            return None
        if not _settled(conn, job["user_id"], job["thread_id"]):
            conn.execute("UPDATE chat_recaps SET quiet_since='',next_at=? WHERE id=?", (later(5), job_id))
            return None
        if not job["quiet_since"]:
            conn.execute("UPDATE chat_recaps SET quiet_since=?,next_at=? WHERE id=?", (db.now(), later(IDLE_SECONDS), job_id))
            return None
        if (datetime.now(timezone.utc) - datetime.fromisoformat(job["quiet_since"].replace("Z", "+00:00"))).total_seconds() < IDLE_SECONDS:
            return None
        messages = chats.parse_messages(row["messages"])
        fingerprint = hashlib.sha256(chats.dump_messages(messages).encode()).hexdigest()
        messages = chat_store.settle_spawn_traces(conn, job["user_id"], messages)
        messages = chat_store.settle_command_traces(conn, job["user_id"], job["thread_id"], messages)
        anchor, source = episode(messages, job["episode_id"])
        if not anchor or not source or not row["model"]:
            conn.execute("UPDATE chat_recaps SET state='skipped' WHERE id=?", (job_id,))
            return None
        cur = conn.execute("""UPDATE chat_recaps SET state='generating',lease_token=?,next_at=?,attempts=attempts+1,
                              anchor_id=?,fingerprint=?,model=?,updated_at=?
                              WHERE id=? AND state IN ('pending','generating') AND next_at<=? AND attempts<?""",
                           (token, later(LEASE_SECONDS), anchor, fingerprint, row["model"], db.now(), job_id, db.now(), MAX_ATTEMPTS))
        if not cur.rowcount:
            return None
        return {**dict(job), "token": token, "source": source, "model": row["model"], "fingerprint": fingerprint,
                "owner": dict(owner)}


def finish(job: dict, text: str):
    with chat_store.transaction(immediate=False) as conn:
        # Acquire the thread without run locks before checking work state.
        conn.execute("UPDATE chat_threads SET recap_epoch=recap_epoch WHERE id=?", (job["thread_id"],))
        row = conn.execute("SELECT recap_epoch,messages FROM chat_threads WHERE id=?", (job["thread_id"],)).fetchone()
        current = conn.execute("SELECT * FROM chat_recaps WHERE id=?", (job["id"],)).fetchone()
        if not current or current["lease_token"] != job["token"] or current["state"] != "generating":
            return
        owner = conn.execute("SELECT chat_recaps FROM users WHERE id=?", (job["user_id"],)).fetchone()
        valid = row and row["recap_epoch"] == job["epoch"] and owner and (current["manual"] or owner["chat_recaps"])
        if valid:
            valid = hashlib.sha256(chats.dump_messages(chats.parse_messages(row["messages"])).encode()).hexdigest() == job["fingerprint"]
        if not valid:
            conn.execute("UPDATE chat_recaps SET state='stale',lease_token='' WHERE id=?", (job["id"],))
            return
        # A command may have started independently while the model was working.
        busy = not _settled(conn, job["user_id"], job["thread_id"])
        if busy:
            text = ""
        text = chats.parse_generated_recap(text)
        state = "ready" if text else "failed" if current["attempts"] >= MAX_ATTEMPTS else "pending"
        cur = conn.execute("""UPDATE chat_recaps SET state=?,text=?,lease_token='',next_at=?,quiet_since=?,updated_at=?
                              WHERE id=? AND state='generating' AND lease_token=?""",
                           (state, text, later(30 * current["attempts"]), "" if busy else current["quiet_since"],
                            db.now(), job["id"], job["token"]))
        if not cur.rowcount:
            return
        if text:
            chat_store.event(conn, job["user_id"], "recap.updated", job["thread_id"],
                             {"anchor_id": current["anchor_id"], "text": text})
        elif state == "failed" and current["manual"]:
            chat_store.event(conn, job["user_id"], "recap.failed", job["thread_id"], {})


async def _generate(job_id: int):
    job = None
    try:
        job = await asyncio.to_thread(claim, job_id)
        if job is None:
            return
        from . import chat_runtime, chat_runs
        text = await chat_runtime.generate_recap(chat_runs.internal_request(job["user_id"]), job["owner"], job["model"], job["source"])
        await asyncio.to_thread(finish, job, text)
    except asyncio.CancelledError:
        raise  # Lease expiration handles restart; disabled jobs cannot publish.
    except Exception:
        logger.exception("Recap job %s failed", job_id)
        if job:
            await asyncio.to_thread(finish, job, "")
    finally:
        _tasks.pop(job_id, None)


async def sweep():
    await asyncio.to_thread(db.execute, """UPDATE chat_recaps SET state='failed',lease_token='',updated_at=?
                                         WHERE state='generating' AND next_at<=? AND attempts>=?""",
                            (db.now(), db.now(), MAX_ATTEMPTS))
    capacity = CONCURRENCY - len(_tasks)
    if capacity <= 0:
        return
    rows = await asyncio.to_thread(db.fetchall, """SELECT id FROM chat_recaps WHERE state IN ('pending','generating')
                                                AND next_at<=? AND attempts<? ORDER BY next_at,id LIMIT ?""",
                                   (db.now(), MAX_ATTEMPTS, capacity))
    for row in rows:
        if row["id"] not in _tasks:
            _tasks[row["id"]] = asyncio.create_task(_generate(row["id"]), name=f"chat-recap-{row['id']}")


async def loop():
    global _wake, _wake_loop
    _wake = asyncio.Event()
    _wake_loop = asyncio.get_running_loop()
    try:
        while True:
            _wake.clear()
            await sweep()
            # Turning off recaps cancels active automatic requests, not just UI.
            disabled = await asyncio.to_thread(db.fetchall, "SELECT id FROM chat_recaps WHERE state IN ('disabled','stale') AND id IN (" +
                                              ",".join("?" for _ in _tasks) + ")", tuple(_tasks)) if _tasks else []
            for row in disabled:
                task = _tasks.get(row["id"])
                if task:
                    task.cancel()
            try:
                await asyncio.wait_for(_wake.wait(), timeout=2)
            except asyncio.TimeoutError:
                pass
    finally:
        _wake = None
        _wake_loop = None
        await drain()


async def drain():
    tasks = list(_tasks.values())
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    _tasks.clear()
