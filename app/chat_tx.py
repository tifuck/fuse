"""Write transactions, command ledger and event log (shared ``_writes`` thread-local)."""
from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import threading

from . import db
from .database_errors import IntegrityError


ACTIVE = ("queued", "running")
TERMINAL = {"completed", "cancelled", "failed", "interrupted"}
_writes = threading.local()

# Post-commit, in-process view of the per-user event cursor and per-thread
# watermarks. Written only from transaction() epilogues after COMMIT, so the
# values never exceed what a fresh reader could observe in the database.
# Readers (WebSocket replay validation, thread reconcile short-circuit) use
# them to avoid a query on every wake; prune_events invalidates them.
_committed_cursors: dict[int, int] = {}
_committed_threads: dict[tuple[int, int], int] = {}
_committed_lock = threading.Lock()


def committed_cursor(user_id) -> int | None:
    with _committed_lock:
        return _committed_cursors.get(int(user_id))


def invalidate_committed_cursors():
    with _committed_lock:
        _committed_cursors.clear()
        _committed_threads.clear()


def thread_event_watermark(user_id, thread_id) -> int | None:
    with _committed_lock:
        return _committed_threads.get((int(user_id), int(thread_id)))


def dumps(value):
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True, allow_nan=False)


def _json_needle(text: str) -> str:
    """A text exactly as it appears embedded inside a stored JSON string."""
    return json.dumps(str(text or ""), ensure_ascii=True)[1:-1]


@contextmanager
def transaction(*, immediate=True):
    users = set()
    threads: dict[tuple[int, int], int] = {}
    cursors: dict[int, int] = {}
    after: list = []
    _writes.users = users
    _writes.threads = threads
    _writes.cursors = cursors
    _writes.after = after
    try:
        with db.tx() as conn:
            conn.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
            yield conn
    finally:
        _writes.users = None
        _writes.threads = None
        _writes.cursors = None
        _writes.after = None
    for callback in after:
        callback()
    # Wake observers only after commit; SQLite remains the replay source of truth.
    if cursors or threads:
        with _committed_lock:
            # Monotonic, like the user cursors: a transaction that committed
            # first but got here last must not move a watermark backwards.
            for key, eid in threads.items():
                if eid > _committed_threads.get(key, 0):
                    _committed_threads[key] = eid
            for uid, eid in cursors.items():
                if eid > _committed_cursors.get(uid, 0):
                    _committed_cursors[uid] = eid
    if users:
        from .chat_events import notify
        notify(users)


def after_commit(callback) -> None:
    """Run callback once the open transaction() commits; never on rollback.
    For in-memory state that must not run ahead of the database."""
    pending = getattr(_writes, "after", None)
    if pending is None:
        raise RuntimeError("after_commit needs an open chat transaction")
    pending.append(callback)


def init_schema(connection=None):
    from . import chats
    from .mysql_compat import Conn

    if isinstance(connection or db.connect(), Conn):
        db._init(connection or db.connect())
        return

    with (transaction() if connection is None else connection) as conn:
        db._ensure_column(conn, "chat_threads", "revision", "INTEGER NOT NULL DEFAULT 0")
        db._ensure_column(conn, "chat_threads", "loaded_tools", "TEXT NOT NULL DEFAULT '[]'")
        db._ensure_column(conn, "chat_threads", "retained_history", "TEXT NOT NULL DEFAULT '{}'")
        db._ensure_column(conn, "users", "chat_context_revision", "INTEGER NOT NULL DEFAULT 0")
        for sql in (
            "CREATE TABLE IF NOT EXISTS chat_thread_ids (id INTEGER PRIMARY KEY AUTOINCREMENT)",
            """CREATE TABLE IF NOT EXISTS chat_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                type TEXT NOT NULL, thread_id INTEGER, payload TEXT NOT NULL,
                created_at TEXT NOT NULL)""",
            "CREATE INDEX IF NOT EXISTS chat_events_user_id ON chat_events(user_id,id)",
            "CREATE INDEX IF NOT EXISTS chat_events_created ON chat_events(created_at)",
            """CREATE TABLE IF NOT EXISTS chat_event_cursors (
                user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE, cursor INTEGER NOT NULL)""",
            """CREATE TABLE IF NOT EXISTS chat_commands (
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, command_id TEXT NOT NULL,
                fingerprint TEXT NOT NULL, result TEXT NOT NULL,
                PRIMARY KEY(user_id,command_id))""",
            # No thread FK: deletion must not destroy the worker's cancellation signal.
            """CREATE TABLE IF NOT EXISTS chat_runs (
                id TEXT PRIMARY KEY, thread_id INTEGER NOT NULL, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                status TEXT NOT NULL, request TEXT NOT NULL, message_id TEXT NOT NULL,
                message TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                cancel_requested INTEGER NOT NULL DEFAULT 0)""",
            # Legacy guard superseded by chat_runs_active_parent (children may run
            # concurrently with the root); drop it before any re-create could fire.
            "DROP INDEX IF EXISTS chat_runs_active_thread",
            "CREATE INDEX IF NOT EXISTS chat_runs_user_status ON chat_runs(user_id,status)",
        ):
            conn.execute(sql)
        _ensure_agent_run_columns(conn)
        db._ensure_column(conn, "chat_event_cursors", "floor", "INTEGER NOT NULL DEFAULT 0")
        from . import permissions as permissions_mod

        permissions_mod.init_schema(conn)
        from . import tool_failures

        tool_failures.init_schema(conn)
        chats.ensure_thread_public_ids(conn)
        maximum = conn.execute("SELECT MAX(id) FROM chat_threads").fetchone()[0]
        if maximum is not None:
            conn.execute("INSERT OR IGNORE INTO chat_thread_ids(id) VALUES(?)", (maximum,))
            conn.execute("DELETE FROM chat_thread_ids")
        if not conn.execute("SELECT 1 FROM schema_meta WHERE key='chat_message_ids_v1'").fetchone():
            for row in conn.execute("SELECT id,messages FROM chat_threads").fetchall():
                conn.execute("UPDATE chat_threads SET messages=? WHERE id=?",
                             (dumps(chats.parse_messages(row["messages"])), row["id"]))
            conn.execute("INSERT INTO schema_meta(key,value) VALUES('chat_message_ids_v1',?)", (db.now(),))


def _ensure_agent_run_columns(conn):
    db._ensure_column(conn, "chat_runs", "parent_run_id", "TEXT")
    db._ensure_column(conn, "chat_runs", "root_run_id", "TEXT")
    db._ensure_column(conn, "chat_runs", "agent_depth", "INTEGER NOT NULL DEFAULT 0")
    db._ensure_column(conn, "chat_runs", "loaded_tools", "TEXT NOT NULL DEFAULT '[]'")
    db._ensure_column(conn, "chat_runs", "provider_messages", "TEXT")
    db._ensure_column(conn, "chat_runs", "resume_count", "INTEGER NOT NULL DEFAULT 0")
    db._ensure_column(conn, "chat_runs", "cancel_requested_at", "TEXT NOT NULL DEFAULT ''")
    db._ensure_column(conn, "chat_runs", "activity", "TEXT")
    conn.execute("UPDATE chat_runs SET root_run_id=id WHERE root_run_id IS NULL OR root_run_id=''")
    conn.execute(
        """CREATE UNIQUE INDEX IF NOT EXISTS chat_runs_active_parent
           ON chat_runs(thread_id) WHERE status IN ('queued','running') AND parent_run_id IS NULL"""
    )
    conn.execute("DROP INDEX IF EXISTS chat_runs_active_thread")
    conn.execute("CREATE INDEX IF NOT EXISTS chat_runs_root ON chat_runs(root_run_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS chat_runs_parent ON chat_runs(parent_run_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS chat_runs_user_message ON chat_runs(user_id,message_id)")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS chat_runs_thread_children ON chat_runs(thread_id) WHERE parent_run_id IS NOT NULL"
    )


def lock_thread(conn, thread_id, user_id):
    """The thread row under its row lock, for writers that rebuild it.

    On MariaDB a plain SELECT is a transaction snapshot that can predate
    another writer's commit; writing a messages blob (or title, model, ...)
    computed from it silently reverts that commit. Take this after any
    chat_runs reads: run -> thread is the lock order finalization uses."""
    return conn.execute("SELECT * FROM chat_threads WHERE id=? AND user_id=?" + db.for_update(conn),
                        (thread_id, user_id)).fetchone()


def check_revision(actual, expected):
    from .chats import ChatValidationError

    if expected is not None and (type(expected) is not int or expected < 0):
        raise ChatValidationError("expected_revision must be a non-negative integer")
    if expected is not None and expected != actual:
        raise ChatValidationError("revision conflict; reload the latest state", 409)


def command(conn, user_id, command_id, request):
    from .chats import ChatValidationError, _text

    fingerprint = hashlib.sha256(dumps(request).encode()).hexdigest()
    if command_id is None:
        return fingerprint, None
    if not _text(command_id, "command_id", 200, strip=True):
        raise ChatValidationError("command_id cannot be empty")
    row = conn.execute("SELECT * FROM chat_commands WHERE user_id=? AND command_id=?",
                       (user_id, command_id)).fetchone()
    if row and row["fingerprint"] != fingerprint:
        raise ChatValidationError("command_id already used for a different request", 409)
    if not row:
        return fingerprint, None
    previous = json.loads(row["result"])
    if _is_receipt(previous):
        return fingerprint, _expand_receipt(conn, user_id, previous)
    if isinstance(previous, dict) and _is_receipt(previous.get("thread")):
        return fingerprint, {**previous, "thread": _expand_receipt(conn, user_id, previous["thread"])}
    return fingerprint, previous


# Thread views carry the whole transcript: the ledger keeps a receipt naming
# the thread instead of a full copy per command (it is never pruned, and a
# copy would outlive the thread's own deletion). A retried mutation answers
# with the thread as it is now.
_VIEW_RECEIPT = "thread_view_receipt"


def _is_thread_view(value) -> bool:
    return isinstance(value, dict) and "messages" in value and isinstance(value.get("id"), str)


def _is_receipt(value) -> bool:
    return isinstance(value, dict) and set(value) == {_VIEW_RECEIPT}


def _expand_receipt(conn, user_id, receipt):
    from . import chat_views
    from .chats import ChatValidationError, resolve_thread_pk

    pk = resolve_thread_pk(user_id, receipt[_VIEW_RECEIPT], conn)
    view = chat_views.thread_view(conn, user_id, pk) if pk is not None else None
    if view is None:
        raise ChatValidationError("chat not found", 404)
    return view


def remember(conn, user_id, command_id, fingerprint, result):
    if command_id is not None:
        if _is_thread_view(result):
            result = {_VIEW_RECEIPT: result["id"]}
        elif isinstance(result, dict) and _is_thread_view(result.get("thread")):
            result = {**result, "thread": {_VIEW_RECEIPT: result["thread"]["id"]}}
        try:
            conn.execute("INSERT INTO chat_commands(user_id,command_id,fingerprint,result) VALUES(?,?,?,?)",
                         (user_id, command_id, fingerprint, dumps(result)))
        except IntegrityError as exc:
            if exc.errno not in (None, 1062):
                raise
            from .chats import ChatValidationError

            # The same command_id committed concurrently (the ledger read is
            # not a lock). Roll this copy back; a retry replays that result.
            raise ChatValidationError("this command is already being applied; retry to get its result", 409) from exc


def event(conn, user_id, type, thread_id=None, payload=None, guard_run=None):
    # Progress guard first: run row lock BEFORE the per-user cursor lock, so
    # publication and finish_run share one lock order (chat_runs ->
    # chat_event_cursors) and can never deadlock. Group-committed progress is
    # dropped when the run finalized first; the terminal event already carries
    # the final state, and replaying a stale patch after it would resurrect
    # "running" display state.
    if guard_run is not None:
        row = conn.execute("SELECT status FROM chat_runs WHERE id=?", (guard_run,)).fetchone()
        if row is None or row["status"] not in ACTIVE:
            return None
    if thread_id is not None:
        from . import chat_recaps
        if type in ("run.started", "run.resumed", "agent.started", "agent.resumed"):
            chat_recaps.activity(conn, user_id, thread_id)
        elif type in ("run.completed", "run.failed", "run.cancelled", "run.interrupted",
                      "agent.completed", "agent.failed", "agent.cancelled", "agent.interrupted"):
            chat_recaps.terminal_activity(conn, user_id, thread_id)
    # Acquire the per-user publication lock BEFORE allocating an event ID.
    # This makes IDs a commit-ordered prefix for that user's replay stream.
    # Merely taking MAX(cursor, id) after INSERT would still skip late commits.
    conn.execute("INSERT INTO chat_event_cursors(user_id,cursor) VALUES(?,0) ON CONFLICT(user_id) DO UPDATE SET cursor=chat_event_cursors.cursor", (user_id,))
    eid = conn.execute("INSERT INTO chat_events(user_id,type,thread_id,payload,created_at) VALUES(?,?,?,?,?)",
                       (user_id, type, thread_id, dumps(payload if payload is not None else {}), db.now())).lastrowid
    conn.execute("INSERT INTO chat_event_cursors(user_id,cursor) VALUES(?,?) ON CONFLICT(user_id) DO UPDATE SET cursor=excluded.cursor",
                  (user_id, eid))
    if getattr(_writes, "users", None) is not None:
        _writes.users.add(user_id)
        threads = getattr(_writes, "threads", None)
        if thread_id is not None and threads is not None:
            key = (int(user_id), int(thread_id))
            if eid > threads.get(key, 0):
                threads[key] = eid
        cursors = getattr(_writes, "cursors", None)
        if cursors is not None and eid > cursors.get(int(user_id), 0):
            cursors[int(user_id)] = eid
    return eid


def cursor(user_id):
    row = db.fetchone("SELECT cursor FROM chat_event_cursors WHERE user_id=?", (user_id,))
    return int(row[0]) if row else 0


def events_after(user_id, cursor, limit=256):
    from . import chats

    rows = db.fetchall(
        """SELECT e.id, e.type, e.payload, t.public_id AS thread_id
           FROM chat_events e
           LEFT JOIN chat_threads t ON t.id=e.thread_id
           WHERE e.user_id=? AND e.id>? ORDER BY e.id LIMIT ?""",
        (user_id, cursor, max(1, min(int(limit), 256))),
    )
    out = []
    for row in rows:
        payload = json.loads(row["payload"])
        thread_id = row["thread_id"]
        public = payload.get("id") if isinstance(payload, dict) else None
        # Only thread events carry the thread's own public id as "id" (and the
        # JOIN is NULL once the thread row is gone); other payloads may hold
        # unrelated ids that must not masquerade as the thread.
        if (row["type"] in ("thread.updated", "thread.deleted")
                and isinstance(public, str) and chats.PUBLIC_ID_RE.match(public)):
            thread_id = public
        out.append({"id": int(row["id"]), "type": row["type"], "thread_id": thread_id, "payload": payload})
    return out


def prune_events():
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ")
    with transaction() as conn:
        conn.execute("""UPDATE chat_event_cursors SET floor=MAX(floor,COALESCE(
            (SELECT MAX(id) FROM chat_events WHERE user_id=chat_event_cursors.user_id AND created_at<?),0))""", (cutoff,))
        conn.execute("DELETE FROM chat_events WHERE created_at<?", (cutoff,))
    # Floors moved: the cached committed cursors can no longer validate a
    # replay cursor without a fresh read.
    invalidate_committed_cursors()
    prune_command_receipts()


# Command receipts dedupe a retried command (a lost ACK, a queued turn a client
# resends on reconnect), so they must outlive any realistic retry, but they
# grew without bound. The table has no timestamp: the rowid it had reached is
# recorded, and one retention period later every row at or below it goes, so
# no receipt younger than COMMAND_RECEIPT_DAYS is ever dropped.
COMMAND_RECEIPT_DAYS = 30
_RECEIPT_MARK = "chat_commands_prune_mark"


def prune_command_receipts(now: datetime | None = None) -> int:
    now = now or datetime.now(timezone.utc)
    cutoff = (now - timedelta(days=COMMAND_RECEIPT_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    with transaction() as conn:
        row = conn.execute("SELECT value FROM schema_meta WHERE key=?", (_RECEIPT_MARK,)).fetchone()
        mark = json.loads(row["value"]) if row else None
        if mark and mark["at"] > cutoff:
            return 0
        deleted = 0
        if mark:
            deleted = conn.execute("DELETE FROM chat_commands WHERE rowid<=?", (int(mark["rowid"]),)).rowcount
        hi = conn.execute("SELECT MAX(rowid) AS hi FROM chat_commands").fetchone()["hi"]
        conn.execute(
            "INSERT INTO schema_meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (_RECEIPT_MARK, dumps({"at": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "rowid": int(hi or 0)})),
        )
    return int(deleted or 0)


def thread_event(conn, user_id, thread_id, transcript_changed=False):
    from . import chats

    row = conn.execute(
        f"""SELECT {chats._THREAD_LIST_COLS}
           FROM chat_threads t
           LEFT JOIN user_hosts h ON h.id=t.host_id
           LEFT JOIN user_projects p ON p.id=t.project_id
           LEFT JOIN chat_schedules s ON s.id=t.schedule_id
           WHERE t.id=? AND t.user_id=?""",
        (thread_id, user_id),
    ).fetchone()
    if row:
        payload = chats._view(row)
        if transcript_changed:
            payload["transcript_changed"] = True
        event(conn, user_id, "thread.updated", thread_id, payload)
