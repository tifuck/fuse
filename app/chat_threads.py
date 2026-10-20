"""Thread, project and user-context database CRUD.

Extracted verbatim from app/chats.py (see chats-extract.py in the modularize
snapshot). chat_store is imported lazily inside the mutating entry points: the
deliberate chats<->chat_store lazy cycle must not become a module-top import.
"""
from __future__ import annotations

import json
import re
from uuid import uuid4

from . import chat_images, db
from .chat_tools import AVAILABLE, DEFAULT_TOOLS
from .chat_schema import (
    CONTEXT_MAX,
    DEFAULT_INSTRUCTIONS,
    TITLE_MAX,
    ChatValidationError,
    _legacy_text,
    _text,
    clip_context,
    dump_messages,
    effective_instructions,
    new_public_id,
    UNREADABLE_TRANSCRIPT,
    parse_messages,
    parse_thread_tools,
    stored_messages,
)
from .chat_titles import _clean_title
from .chat_generation import cache_watch

# Any active run keeps a thread "running", subagents included: a background
# child can outlive the root turn that spawned it and the sidebar must keep
# its spinner until the last one settles.
_THREAD_RUNNING_SQL = (
    "EXISTS (SELECT 1 FROM chat_runs r "
    "WHERE r.thread_id=t.id AND r.status IN ('queued','running'))"
)
_THREAD_LIST_COLS = (
    "t.id, t.public_id, t.title, t.model, t.archived, t.created_at, t.updated_at, t.revision, "
    "t.kind, t.host_id, t.project_id, t.schedule_id, t.bypass_permissions, t.auto_permissions, "
    "t.pinned, t.pin_order, t.unread, "
    "h.alias AS host_alias, p.name AS project_name, "
    "p.color AS project_color, p.logo_rev AS project_logo_rev, p.archived AS project_archived, t.archived_at, "
    "s.name AS schedule_name, "
    f"{_THREAD_RUNNING_SQL} AS running"
)
def _alloc_public_id(conn) -> str:
    for _ in range(12):
        token = new_public_id()
        if conn.execute("SELECT 1 AS ok FROM chat_threads WHERE public_id=?", (token,)).fetchone() is None:
            return token
    raise ChatValidationError("could not allocate a chat id", 500)
def ensure_thread_public_ids(conn) -> None:
    db._ensure_column(conn, "chat_threads", "public_id", "TEXT")
    missing = conn.execute(
        "SELECT id FROM chat_threads WHERE public_id IS NULL OR public_id=''"
    ).fetchall()
    if missing:
        taken = {
            str(row[0])
            for row in conn.execute(
                "SELECT public_id FROM chat_threads WHERE public_id IS NOT NULL AND public_id!=''"
            )
        }
        for row in missing:
            token = new_public_id()
            while token in taken:
                token = new_public_id()
            taken.add(token)
            conn.execute("UPDATE chat_threads SET public_id=? WHERE id=?", (token, row["id"]))
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS chat_threads_public_id ON chat_threads(public_id)")
def resolve_thread_pk(user_id: int, token, conn=None):
    if token is None or token is False:
        return None

    def one(sql, args):
        if conn is not None:
            return conn.execute(sql, args).fetchone()
        return db.fetchone(sql, args)

    if isinstance(token, bool):
        return None
    if isinstance(token, int):
        if token < 1:
            return None
        row = one("SELECT id FROM chat_threads WHERE user_id=? AND id=?", (user_id, token))
        return int(row["id"]) if row else None
    text = str(token).strip()
    if not text:
        return None
    row = one("SELECT id FROM chat_threads WHERE user_id=? AND public_id=?", (user_id, text))
    if row:
        return int(row["id"])
    if text.isdigit():
        try:
            n = int(text)
        except ValueError:
            return None
        if n < 1 or n > db.SQLITE_INT_MAX:
            return None
        row = one("SELECT id FROM chat_threads WHERE user_id=? AND id=?", (user_id, n))
        return int(row["id"]) if row else None
    return None
def public_id_for(thread_pk, conn=None):
    if thread_pk is None:
        return None

    def one(sql, args):
        if conn is not None:
            return conn.execute(sql, args).fetchone()
        return db.fetchone(sql, args)

    try:
        pk = int(thread_pk)
    except (TypeError, ValueError):
        return str(thread_pk) if thread_pk else None
    row = one("SELECT public_id FROM chat_threads WHERE id=?", (pk,))
    if row and row["public_id"]:
        return str(row["public_id"])
    return None
def _norm_remote_path(path: str) -> str:
    text = (path or "").strip().replace("\\", "/")
    while "//" in text:
        text = text.replace("//", "/")
    if len(text) > 1:
        text = text.rstrip("/")
    return text
# Writes only: reading a folder must not rebind the chat (and narrow its
# working root) to that folder.
_FILE_PROMOTE_TOOLS = frozenset({"write_file", "search_replace"})
def _generic_remote_root(root: str) -> bool:
    text = _norm_remote_path(root)
    return text in ("", "/", "~", ".", "~/")
def _temp_root(root: str) -> bool:
    """Temp is always in scope and never a project: binding a chat to /tmp
    would drop home/project access for the rest of the session."""
    from .permissions import is_temp_path

    text = _norm_remote_path(root)
    return is_temp_path(text, False) or is_temp_path(text, True)
def _session_root(cwd: str = "", path: str = "", *, tool: str = "") -> str:
    if cwd:
        return _norm_remote_path(cwd)
    text = _norm_remote_path(path)
    if not text:
        return ""
    if tool in ("read_file", "write_file", "search_replace"):
        return (text.rsplit("/", 1)[0] or "/") if "/" in text else "."
    return text
def get_user_context(user_id: int) -> dict:
    row = db.fetchone("SELECT chat_instructions,chat_profile,chat_context_revision,chat_recaps FROM users WHERE id=?", (user_id,))
    return _context_view(row)
def _context_view(row):
    stored = (row["chat_instructions"] or "") if row else ""
    return {
        "instructions": effective_instructions(stored),
        "profile": _legacy_text(row["chat_profile"], CONTEXT_MAX) if row else "",
        "available": AVAILABLE,
        "default_instructions": DEFAULT_INSTRUCTIONS,
        "revision": row["chat_context_revision"] if row else 0,
        "recaps": bool(row["chat_recaps"]) if row and "chat_recaps" in row.keys() else True,
    }
def set_user_context(user_id: int, *, instructions=None, profile=None, recaps=None, strict: bool = False,
                     expected_revision=None, command_id=None) -> dict:
    from . import chat_store as store

    with store.transaction() as conn:
        fingerprint, previous = store.command(conn, user_id, command_id,
            ["context", instructions, profile, recaps, expected_revision])
        if previous is not None:
            return previous
        row = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        if row is None:
            raise ChatValidationError("user not found", 404)
        store.check_revision(row["chat_context_revision"], expected_revision)
        _lock_revision(conn, "users", "chat_context_revision", row["chat_context_revision"], "id=?", (user_id,))
        stored_instructions = row["chat_instructions"] or ""
        stored_profile = row["chat_profile"] or ""
        if instructions is not None:
            stored_instructions = clip_context(instructions, strict=strict, field="instructions")
            if stored_instructions == DEFAULT_INSTRUCTIONS:
                stored_instructions = ""
        if profile is not None:
            stored_profile = clip_context(profile, strict=strict, field="profile")
        stored_recaps = 1 if (row["chat_recaps"] if "chat_recaps" in row.keys() else 1) else 0
        if recaps is not None:
            if not isinstance(recaps, bool):
                raise ChatValidationError("recaps must be true or false")
            stored_recaps = 1 if recaps else 0
        conn.execute(
            """UPDATE users SET chat_instructions=?,chat_profile=?,chat_recaps=?,
               chat_context_revision=chat_context_revision+1 WHERE id=?""",
            (stored_instructions, stored_profile, stored_recaps, user_id),
        )
        if not stored_recaps:
            from . import chat_recaps
            chat_recaps.disable(conn, user_id)
        result = _context_view(conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone())
        store.event(conn, user_id, "context.updated", payload=result)
        store.remember(conn, user_id, command_id, fingerprint, result)
        return result
def _view(row, *, include_messages: bool = False, coding: bool = False, conn=None) -> dict:
    keys = row.keys() if hasattr(row, "keys") else []
    kind = row["kind"] if "kind" in keys and row["kind"] else "chat"
    if kind not in ("chat", "project"):
        kind = "chat"
    public = ""
    if "public_id" in keys and row["public_id"]:
        public = str(row["public_id"])
    data = {
        "id": public or str(int(row["id"])),
        "title": _legacy_text(row["title"], TITLE_MAX) or "New chat",
        "model": _legacy_text(row["model"], 200),
        "archived": bool(row["archived"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "revision": int(row["revision"]),
        "kind": kind,
        "host_id": int(row["host_id"]) if "host_id" in keys and row["host_id"] is not None else None,
        "project_id": int(row["project_id"]) if "project_id" in keys and row["project_id"] is not None else None,
        "host_alias": row["host_alias"] if "host_alias" in keys else None,
        "project_name": row["project_name"] if "project_name" in keys else None,
        "running": bool(row["running"]) if "running" in keys else False,
        "archived_at": (row["archived_at"] or "") if "archived_at" in keys else "",
        "schedule_id": int(row["schedule_id"]) if "schedule_id" in keys and row["schedule_id"] is not None else None,
        "schedule_name": _legacy_text(row["schedule_name"], TITLE_MAX) if "schedule_name" in keys and row["schedule_name"] else "",
        "bypass_permissions": bool(row["bypass_permissions"]) if "bypass_permissions" in keys else False,
        "auto_permissions": bool(row["auto_permissions"]) if "auto_permissions" in keys else False,
        "pinned": bool(row["pinned"]) if "pinned" in keys else False,
        "pin_order": int(row["pin_order"]) if "pin_order" in keys and row["pin_order"] is not None else 0,
        "unread": bool(row["unread"]) if "unread" in keys else False,
    }
    if kind == "project" and data["project_id"] is not None and "project_name" in keys and row["project_name"] is not None:
        from . import hosts

        data["project_color"] = hosts.project_color(data["project_id"], row["project_color"] if "project_color" in keys else "")
        data["project_logo_url"] = hosts.logo_url(data["project_id"], row["project_logo_rev"] if "project_logo_rev" in keys else 0)
        data["project_archived"] = bool(row["project_archived"]) if "project_archived" in keys and row["project_archived"] else False
    if include_messages:
        data["context"] = _legacy_text(row["context"], CONTEXT_MAX)
        data["tools"] = parse_thread_tools(row["tools"])
        data["messages"] = parse_messages(row["messages"])
        from . import context_compact as ctxmod, context_meter

        raw_comp = row["compaction"] if "compaction" in keys else "{}"
        state = ctxmod.parse_state(raw_comp)
        model = data.get("model") or ""
        stored = state.get("meter") if isinstance(state.get("meter"), dict) else {}
        type_id = context_meter.source_type_for_model(model, stored)
        # Measure the validated replay projection, including saved tool pairs,
        # rather than display previews or summed multi-request billing.
        from . import chat_history
        replay, history_info = chat_history.build(conn if conn is not None else db.connect(), row["user_id"], row["id"], data["messages"], state,
            retained=row["retained_history"] if "retained_history" in keys else "{}", model=model)
        if any(isinstance(msg, dict) and ctxmod.is_compact_preamble(str(msg.get("content") or "")) for msg in replay):
            from .chat_views import compaction_cut
            data["context_cut"] = compaction_cut(data["messages"], state)
        rebuilt = context_meter.standing_meter(
            chat_history.for_model(replay, model),
            model=model,
            type_id=type_id,
            override=state.get("window_override"),
            prior=stored if stored.get("model") == model else None,
        )
        # Preserve a source-resolved window only for generic model IDs whose
        # name cannot identify the backend (type_id None, e.g. a local Qwen
        # model). Named pools resolve to their real window every time.
        if (
            type_id is None
            and stored.get("model") == model
            and stored.get("limit")
            and not state.get("window_override")
        ):
            rebuilt.update(context_meter.meter(used=rebuilt["used"], limit=stored["limit"], model=model,
                                       estimated=True))
        meter = rebuilt
        meter["history_info"] = history_info
        state["meter"] = meter
        data["compaction"] = {
            "meter": meter,
            "strategy": state.get("strategy"),
            "auto": state.get("auto"),
            "warn_ratio": state.get("warn_ratio"),
            "auto_ratio": state.get("auto_ratio"),
            "keep_tail": state.get("keep_tail"),
            "window_override": state.get("window_override"),
            "cache": cache_watch.public(state.get("cache")),
            "checkpoints": [
                {
                    "id": cp.get("id"),
                    "created_at": cp.get("created_at"),
                    "strategy": cp.get("strategy"),
                    "trigger": cp.get("trigger"),
                    "tokens_before": cp.get("tokens_before"),
                    "tokens_after": cp.get("tokens_after"),
                }
                for cp in (state.get("checkpoints") or [])
                if isinstance(cp, dict)
            ],
        }
    return data
def list_threads(user_id: int, *, archived: bool | None = False) -> list[dict]:
    if archived is None:
        rows = db.fetchall(
            f"""SELECT {_THREAD_LIST_COLS}
               FROM chat_threads t
               LEFT JOIN user_hosts h ON h.id=t.host_id
               LEFT JOIN user_projects p ON p.id=t.project_id
               LEFT JOIN chat_schedules s ON s.id=t.schedule_id
               WHERE t.user_id=? ORDER BY t.updated_at DESC, t.id DESC""",
            (user_id,),
        )
    else:
        rows = db.fetchall(
            f"""SELECT {_THREAD_LIST_COLS}
               FROM chat_threads t
               LEFT JOIN user_hosts h ON h.id=t.host_id
               LEFT JOIN user_projects p ON p.id=t.project_id
               LEFT JOIN chat_schedules s ON s.id=t.schedule_id
               WHERE t.user_id=? AND t.archived=? ORDER BY t.updated_at DESC, t.id DESC""",
            (user_id, 1 if archived else 0),
        )
    return [_view(r) for r in rows]
def _snippet(raw: str, needle: str) -> str:
    try:
        msgs = json.loads(raw or "[]")
    except Exception:
        return ""
    for msg in msgs:
        if not isinstance(msg, dict):
            continue
        content = msg.get("content") or ""
        idx = content.lower().find(needle)
        if idx >= 0:
            start = max(0, idx - 42)
            text = " ".join(content[start : idx + len(needle) + 64].split())
            return ("…" if start > 0 else "") + text
    return ""
# Transcript search is a leading-wildcard LIKE over multi-MB blobs: it cannot
# use an index, so it only scans the newest window of threads. Title, project
# and host matches stay unbounded (small columns).
# ponytail: real transcript search needs a FTS column (schema change); widen
# the window if users miss older hits before then.
_SEARCH_MESSAGE_WINDOW = 500
def search_threads(user_id: int, q: str, *, archived: bool = False) -> list[dict]:
    needle = q.strip()
    if not needle:
        return []
    like = "%" + needle.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    rows = db.fetchall(
        f"""SELECT {_THREAD_LIST_COLS}, t.messages
           FROM chat_threads t
           LEFT JOIN user_hosts h ON h.id=t.host_id
           LEFT JOIN user_projects p ON p.id=t.project_id
           LEFT JOIN chat_schedules s ON s.id=t.schedule_id
           WHERE t.user_id=? AND t.archived=? AND (
                t.title LIKE ? ESCAPE '\\' OR IFNULL(p.name,'') LIKE ? ESCAPE '\\'
                OR IFNULL(h.alias,'') LIKE ? ESCAPE '\\'
                OR (t.id IN (SELECT id FROM (SELECT id FROM chat_threads WHERE user_id=? AND archived=?
                             ORDER BY updated_at DESC, id DESC LIMIT ?) recent)
                    AND t.messages LIKE ? ESCAPE '\\')
           )
           ORDER BY t.updated_at DESC, t.id DESC LIMIT 50""",
        (user_id, 1 if archived else 0, like, like, like,
         user_id, 1 if archived else 0, _SEARCH_MESSAGE_WINDOW, like),
    )
    lower = needle.lower()
    out = []
    for row in rows:
        view = _view(row)
        view["snippet"] = _snippet(row["messages"], lower)
        out.append(view)
    return out
def get_thread(user_id: int, thread_id, *, observed_cursor: int | None = None, observed_thread_cursor: int | None = None,
               queued=()):
    from . import chat_checkpoint, chat_store

    # Before the transaction's first read; see state_cursor_snapshot.
    stamps = chat_checkpoint.state_cursor_snapshot(user_id)
    with db.tx() as conn:
        conn.execute("BEGIN")
        pk = resolve_thread_pk(user_id, thread_id, conn)
        if pk is None:
            return None
        # Check inside the same read transaction as the snapshot. A revision
        # alone is insufficient: live progress advances the event cursor without
        # changing the thread revision. Resolve ownership even on a cache hit.
        # The per-thread watermark is the precise check: another thread's
        # subagents advance the user-global cursor constantly, which would
        # defeat the short-circuit for every reconcile while they stream.
        if observed_thread_cursor is not None:
            watermark = chat_store.thread_event_watermark(user_id, pk)
            if watermark is not None and int(observed_thread_cursor) == watermark:
                return {"unchanged": True, "event_cursor": watermark, "thread_cursor": watermark}
        if observed_cursor is not None:
            high = conn.execute("SELECT cursor FROM chat_event_cursors WHERE user_id=?", (user_id,)).fetchone()
            cursor = int(high[0]) if high else 0
            if cursor == observed_cursor:
                return {"unchanged": True, "event_cursor": cursor}
        return chat_store.thread_view(conn, user_id, pk, queued=queued, stamps=stamps)
def create_thread(
    user_id: int,
    *,
    model: str = "",
    messages: list | None = None,
    title: str = "",
    context: str = "",
    tools=None,
    expected_revision=None,
    command_id=None,
    kind=None,
    host_id=None,
    project_id=None,
    schedule_id=None,
    bypass_permissions: bool | None = None,
    auto_permissions: bool | None = None,
) -> dict:
    from . import chat_store as store
    from . import hosts

    request = ["create", model, messages, title, context, tools, expected_revision, kind, host_id, project_id, schedule_id,
               bypass_permissions, auto_permissions]
    msgs = parse_messages(messages or [], strict=True)
    title = _clean_title(_text(title, "title", TITLE_MAX), strict=True)
    model = _text(model, "model", 200)
    context = clip_context(context, strict=True)
    tool_json = json.dumps(parse_thread_tools(tools, strict=tools is not None), separators=(",", ":"))
    payload = json.dumps(msgs, ensure_ascii=False, separators=(",", ":"))
    ts = db.now()
    with store.transaction() as conn:
        fingerprint, previous = store.command(conn, user_id, command_id, request)
        if previous is not None:
            return previous
        store.check_revision(0, expected_revision)
        thread_kind = "chat"
        hid = None
        pid = None
        proj = None
        if kind not in (None, "", "chat"):
            if kind != "project":
                raise ChatValidationError("kind must be chat or project")
            thread_kind = "project"
            try:
                hid = int(host_id)
                pid = int(project_id)
            except (TypeError, ValueError):
                raise ChatValidationError("project threads need host_id and project_id")
            proj = conn.execute(
                "SELECT p.id, p.name, p.host_id, p.default_model, p.default_permissions FROM user_projects p JOIN user_hosts h ON h.id=p.host_id "
                "WHERE p.id=? AND p.user_id=? AND p.host_id=? AND h.user_id=?",
                (pid, user_id, hid, user_id),
            ).fetchone()
            if proj is None:
                raise ChatValidationError("project not found", 404)
            # A new session brings an archived project back.
            conn.execute("UPDATE user_projects SET archived=0, archived_at='' WHERE id=? AND user_id=? AND archived=1", (pid, user_id))
            if not model:
                try:
                    model = (proj["default_model"] or "") if "default_model" in proj.keys() else ""
                except (KeyError, IndexError, TypeError):
                    model = ""
                model = _text(model, "model", 200)
        # The mode the client showed on the unsent chat, else the default.
        if bypass_permissions or auto_permissions:
            perms = "bypass" if bypass_permissions else "auto"
        elif bypass_permissions is False or auto_permissions is False:
            perms = "ask"
        else:
            perms = hosts.new_thread_permissions(user_id, conn, proj)
        if perms == "auto":
            from . import jev

            if not jev.user_can_auto(user_id):
                perms = "ask"
        tid = conn.execute("INSERT INTO chat_thread_ids DEFAULT VALUES").lastrowid
        conn.execute("DELETE FROM chat_thread_ids WHERE id=?", (tid,))
        public = _alloc_public_id(conn)
        sid = None
        if schedule_id is not None:
            try:
                sid = int(schedule_id)
            except (TypeError, ValueError):
                sid = None
        conn.execute(
            """INSERT INTO chat_threads (id, user_id, title, model, archived, messages, context, tools,
               created_at, updated_at, active_at, kind, host_id, project_id, public_id, schedule_id,
               bypass_permissions, auto_permissions)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (tid, user_id, title, model, 0, payload, context, tool_json, ts, ts, ts, thread_kind, hid, pid, public, sid,
             int(perms == "bypass"), int(perms == "auto")),
        )
        store.thread_event(conn, user_id, tid)
        result = store.thread_view(conn, user_id, tid)
        store.remember(conn, user_id, command_id, fingerprint, result)
        return result
def update_thread(
    user_id: int,
    thread_id,
    *,
    title: str | None = None,
    model: str | None = None,
    archived: bool | None = None,
    pinned: bool | None = None,
    messages: list | None = None,
    context: str | None = None,
    tools=None,
    expected_revision=None,
    command_id=None,
    bypass_permissions: bool | None = None,
    auto_permissions: bool | None = None,
) -> dict | None:
    from . import chat_store as store

    if auto_permissions:
        from . import jev

        if not jev.user_can_auto(user_id):
            raise ChatValidationError("auto-approve needs an OpenRouter key on /sources", 409)

    with store.transaction() as conn:
        fingerprint, previous = store.command(conn, user_id, command_id,
            ["update", thread_id, title, model, archived, pinned, messages, context, tools, expected_revision, bypass_permissions, auto_permissions])
        if previous is not None:
            return previous
        pk = resolve_thread_pk(user_id, thread_id, conn)
        if pk is None:
            return None
        row = conn.execute("SELECT * FROM chat_threads WHERE id=? AND user_id=?", (pk, user_id)).fetchone()
        if row is None:
            return None
        store.check_revision(row["revision"], expected_revision)
        if messages is not None and conn.execute("SELECT 1 FROM chat_runs WHERE thread_id=? AND status IN ('queued','running')", (pk,)).fetchone():
            raise ChatValidationError("cannot replace messages during an active run", 409)
        # After the chat_runs read: finalization locks run before thread.
        _lock_revision(conn, "chat_threads", "revision", row["revision"], "id=? AND user_id=?", (pk, user_id))
        _update_thread(conn, row, title=title, model=model, archived=archived, pinned=pinned, messages=messages, context=context, tools=tools)
        if bypass_permissions is not None and bool(bypass_permissions) != bool(row["bypass_permissions"] if "bypass_permissions" in row.keys() else 0):
            from . import permissions as permissions_mod

            permissions_mod.set_bypass(user_id, pk, bool(bypass_permissions), conn=conn)
        if auto_permissions is not None and bool(auto_permissions) != bool(row["auto_permissions"] if "auto_permissions" in row.keys() else 0):
            from . import permissions as permissions_mod

            permissions_mod.set_auto(user_id, pk, bool(auto_permissions), conn=conn)
        doomed = store.purge_orphan_subagents(conn, user_id, pk, messages) if messages is not None else []
        # thread_view reads (and on MariaDB locks) this thread's runs. Take
        # those locks before the cursor upsert in thread_event: publication
        # locks run then cursor, and the reverse order deadlocks with it.
        conn.execute("SELECT id FROM chat_runs WHERE thread_id=?", (pk,)).fetchall()
        store.thread_event(conn, user_id, pk)
        result = store.thread_view(conn, user_id, pk)
        store.remember(conn, user_id, command_id, fingerprint, result)
    if doomed:
        from . import chat_runs

        for run_id in doomed:
            chat_runs.cancel(run_id)
    return result
def _sidebar_view(conn, user_id: int, pk: int):
    row = conn.execute(
        f"""SELECT {_THREAD_LIST_COLS}
           FROM chat_threads t
           LEFT JOIN user_hosts h ON h.id=t.host_id
           LEFT JOIN user_projects p ON p.id=t.project_id
           LEFT JOIN chat_schedules s ON s.id=t.schedule_id
           WHERE t.id=? AND t.user_id=?""",
        (pk, user_id),
    ).fetchone()
    return _view(row) if row else None
def set_pin_order(user_id: int, ids, *, command_id=None) -> dict:
    """Write the pinned section's order. `ids` is the full pinned list, top first."""
    from . import chat_store as store

    order = [str(item).strip() for item in (ids or []) if str(item).strip()]
    with store.transaction() as conn:
        fingerprint, previous = store.command(conn, user_id, command_id, ["pin_order", order])
        if previous is not None:
            return previous
        pks: list[int] = []
        for token in order:
            pk = resolve_thread_pk(user_id, token, conn)
            if pk is not None and pk not in pks:
                pks.append(pk)
        for index, pk in enumerate(pks):
            conn.execute(
                "UPDATE chat_threads SET pin_order=? WHERE id=? AND user_id=? AND pinned=1",
                (index, pk, user_id),
            )
            store.thread_event(conn, user_id, pk)
        result = {"threads": [_sidebar_view(conn, user_id, pk) for pk in pks]}
        store.remember(conn, user_id, command_id, fingerprint, result)
        return result
def mark_thread_read(user_id: int, thread_id) -> bool:
    """Viewing a thread clears its finished-run badge. No revision bump: the
    flag is display metadata, and the thread event carries the new state."""
    from . import chat_store as store

    pk = resolve_thread_pk(user_id, thread_id)
    if pk is None:
        return False
    with store.transaction() as conn:
        row = conn.execute("SELECT unread FROM chat_threads WHERE id=? AND user_id=?", (pk, user_id)).fetchone()
        if row is None or not row["unread"]:
            return False
        conn.execute("UPDATE chat_threads SET unread=0 WHERE id=? AND user_id=?", (pk, user_id))
        store.thread_event(conn, user_id, pk)
    return True
def unarchive_on_activity(conn, row, ts):
    """A new message or transcript edit brings an archived chat (and its project) back."""
    if not int(row["archived"] or 0):
        return
    conn.execute("UPDATE chat_threads SET archived=0, archived_at='', archived_by_project=0, active_at=? WHERE id=?", (ts, row["id"]))
    if (row["kind"] if "kind" in row.keys() else "") == "project" and row["project_id"] is not None:
        conn.execute("UPDATE user_projects SET archived=0, archived_at='' WHERE id=? AND user_id=? AND archived=1",
                     (row["project_id"], row["user_id"]))
def _lock_revision(conn, table: str, column: str, seen, where: str, args: tuple) -> None:
    """Row-lock what was just read, at the revision it was read at. The plain
    SELECT before it does not lock on MariaDB (BEGIN IMMEDIATE is a no-op
    there), so two writers could both pass the revision check and the later
    one write back stale state; this makes the second one conflict."""
    cur = conn.execute(f"UPDATE {table} SET {column}={column} WHERE {where} AND {column}=?", (*args, seen))
    if not cur.rowcount:
        raise ChatValidationError("revision conflict; reload the latest state", 409)


def _held_rows(conn, row, stored: list[dict], incoming) -> dict:
    """Rows the server already holds for the ids a transcript save sends: the
    stored transcript, plus the display rows a finished run wrote for rows the
    save brings back (Redo after Undo)."""
    held = {msg["id"]: msg for msg in stored}
    missing = list(dict.fromkeys(
        item["id"] for item in incoming if isinstance(item, dict) and item.get("role") == "assistant"
        and isinstance(item.get("id"), str) and item["id"] not in held))
    if not missing:
        return held
    from . import chat_views

    for start in range(0, len(missing), 400):
        batch = missing[start:start + 400]
        runs = conn.execute(
            "SELECT message_id,message,request FROM chat_runs WHERE user_id=? AND thread_id=? AND parent_run_id IS NULL "
            "AND status NOT IN ('queued','running') AND message_id IN (" + ",".join("?" for _ in batch) + ")",
            (row["user_id"], row["id"], *batch),
        ).fetchall()
        for run in runs:
            try:
                request = json.loads(run["request"] or "{}")
                message = chat_views._message(json.loads(run["message"] or "{}"), run["message_id"])
            except (TypeError, ValueError):
                continue
            projected = chat_views.display_messages(request if isinstance(request, dict) else {}, message, run["message_id"])
            for mid, msg in projected.items():
                parsed = parse_messages([msg])
                if mid not in held and parsed:
                    held[mid] = parsed[0]
    return held


def _rebase_saved_rows(conn, row, stored: list[dict], messages) -> list[dict]:
    """Parse a transcript save from the page, keeping server-owned state of the
    rows it already holds.

    The page re-serializes every row it shows. Its tool traces are a settled,
    clipped view (child summaries, folded results, dropped fields) and it never
    sends permission records, so writing them back rewrote every earlier row:
    the retained provider history no longer matched, the next turn rebuilt the
    conversation from display traces, and the provider cache missed from the
    first row. A save may delete rows and whole traces and change user text and
    attachments; traces the server holds keep the server's bytes.
    """
    incoming = messages
    if isinstance(incoming, str):
        try:
            incoming = json.loads(incoming)
        except (TypeError, ValueError):
            return parse_messages(messages, strict=True)
    if not isinstance(incoming, list):
        return parse_messages(incoming, strict=True)
    held = _held_rows(conn, row, stored, incoming)
    raw = []
    for item in incoming:
        server = held.get(item.get("id")) if isinstance(item, dict) and isinstance(item.get("id"), str) else None
        if server and server.get("role") == item.get("role") and "permissions" not in item and server.get("permissions"):
            # Before the strict parse: a permission segment must find its record.
            item = {**item, "permissions": json.loads(json.dumps(server["permissions"]))}
        raw.append(item)
    msgs = parse_messages(raw, strict=True)
    for msg in msgs:
        server = held.get(msg["id"])
        if not server or server.get("role") != msg["role"] or not msg.get("tools"):
            continue
        by_id = {}
        for trace in server.get("tools") or []:
            if trace.get("id"):
                by_id.setdefault(trace["id"], trace)
        msg["tools"] = [json.loads(json.dumps(by_id.pop(trace["id"]))) if trace.get("id") in by_id else trace
                        for trace in msg["tools"]]
    return msgs


def _update_thread(conn, row, *, title, model, archived, pinned, messages, context, tools):
    if not row:
        return None
    if archived is not None and not isinstance(archived, bool):
        raise ChatValidationError("archived must be true or false")
    if pinned is not None and not isinstance(pinned, bool):
        raise ChatValidationError("pinned must be true or false")
    new_title = _clean_title(_text(title, "title", TITLE_MAX), strict=True) if title is not None else row["title"]
    new_state = "user" if title is not None else (row["title_state"] if "title_state" in row.keys() else "")
    new_model = _text(model, "model", 200) if model is not None else row["model"]
    new_archived = 1 if archived else 0 if archived is not None else int(row["archived"] or 0)
    # A newly pinned thread lands at the top of the pinned section; unpinning
    # keeps the old order so a re-pin restores the row's place.
    was_pinned = int(row["pinned"] if "pinned" in row.keys() and row["pinned"] else 0)
    pin_bits = ""
    pin_args: list = []
    if pinned is not None and bool(pinned) != bool(was_pinned):
        if pinned:
            pin_bits = ", pinned=1, pin_order=(SELECT COALESCE(MIN(s.pin_order), 1) - 1 FROM chat_threads s WHERE s.user_id=? AND s.pinned=1)"
            pin_args = [row["user_id"]]
        else:
            pin_bits = ", pinned=0"
    edited = False
    if messages is not None:
        stored_msgs = stored_messages(row["messages"])
        if stored_msgs is None:
            raise ChatValidationError(UNREADABLE_TRANSCRIPT, 409)
        msgs = _rebase_saved_rows(conn, row, stored_msgs, messages)
        payload = json.dumps(msgs, ensure_ascii=False, separators=(",", ":"))
        from . import chat_recaps
        if dump_messages(msgs) != dump_messages(stored_msgs):
            chat_recaps.activity(conn, row["user_id"], row["id"])
            edited = True
    else:
        payload = row["messages"]
    if not new_title:
        new_title = "New chat"
    new_context = clip_context(context, strict=True) if context is not None else (row["context"] if "context" in row.keys() else "")
    if tools is not None:
        tool_json = json.dumps(parse_thread_tools(tools, strict=True), separators=(",", ":"))
    else:
        tool_json = row["tools"] if "tools" in row.keys() and row["tools"] is not None else json.dumps(DEFAULT_TOOLS)
    pin_only = pinned is not None and all(value is None for value in (title, model, archived, messages, context, tools))
    ts = row["updated_at"] if pin_only else db.now()
    compaction_json = None
    if messages is not None:
        # Recompute the meter from the new transcript in the same transaction:
        # a second UPDATE would race concurrent compaction writes (e.g. a
        # prune from an in-flight run) and ship without a revision bump.
        from . import context_compact as ctxmod, hosts, context_meter

        state = ctxmod.parse_state(row["compaction"] if "compaction" in row.keys() else None)
        state = ctxmod.invalidate_edited_checkpoints(state, stored_msgs, msgs)
        previous = state.get("meter") or {}
        from . import chat_history
        replay, _history_info = chat_history.build(conn, row["user_id"], row["id"], msgs, state)
        state["meter"] = context_meter.standing_meter(
            chat_history.for_model(replay, new_model), model=new_model,
            override=state.get("window_override") or (
                previous.get("limit")
                if context_meter.type_id_for_model(new_model) is None and previous.get("model") == new_model
                else None
            ), prior=previous,
        )
        compaction_json = ctxmod.dump_state(state)
    conn.execute(
        "UPDATE chat_threads SET title=?, title_state=?, model=?, archived=?, messages=?, context=?, tools=?, updated_at=?, revision=revision+1"
        + pin_bits
        + (", compaction=?" if compaction_json is not None else "")
        + " WHERE id=? AND user_id=?",
        ([new_title, new_state, new_model, new_archived, payload, new_context, tool_json, ts] + pin_args
         + ([compaction_json] if compaction_json is not None else []) + [row["id"], row["user_id"]]),
    )
    old_archived = int(row["archived"] or 0)
    if new_archived != old_archived:
        if new_archived:
            conn.execute("UPDATE chat_threads SET archived_at=?, archived_by_project=0 WHERE id=?", (ts, row["id"]))
        else:
            # Restoring counts as activity, so auto-archive does not take it straight back.
            conn.execute("UPDATE chat_threads SET archived_at='', archived_by_project=0, active_at=? WHERE id=?", (ts, row["id"]))
            if (row["kind"] if "kind" in row.keys() else "") == "project" and row["project_id"] is not None:
                conn.execute(
                    "UPDATE user_projects SET archived=0, archived_at='' WHERE id=? AND user_id=? AND archived=1",
                    (row["project_id"], row["user_id"]),
                )
    elif messages is not None:
        conn.execute("UPDATE chat_threads SET active_at=? WHERE id=?", (ts, row["id"]))
        if edited and archived is None:
            unarchive_on_activity(conn, row, ts)
    if title is not None:
        conn.execute("UPDATE chat_threads SET title_job_token='',title_job_until='' WHERE id=?", (row["id"],))
    elif model is not None and new_model != row["model"] and not new_state:
        conn.execute("UPDATE chat_threads SET title_attempts=0,title_attempted_at='' WHERE id=?", (row["id"],))
def delete_thread(user_id: int, thread_id, *, expected_revision=None, command_id=None,
                  cancel_active: bool = True) -> bool:
    """Delete a thread and its runs. A stale ``expected_revision`` is a no-op
    (False), so background sweepers can skip a thread that moved under them
    instead of aborting. With ``cancel_active=False`` (retention sweeps) the
    deletion refuses while any run is queued/running; interactive callers keep
    the legacy behavior of cancelling those runs and deleting anyway."""
    from . import chat_store as store

    with store.transaction() as conn:
        fingerprint, previous = store.command(conn, user_id, command_id, ["delete", thread_id, expected_revision])
        if previous is not None:
            return previous
        pk = resolve_thread_pk(user_id, thread_id, conn)
        if pk is None:
            return False
        row = conn.execute("SELECT revision, public_id FROM chat_threads WHERE id=? AND user_id=?", (pk, user_id)).fetchone()
        if row is None:
            return False
        if expected_revision is not None:
            if type(expected_revision) is not int or expected_revision < 0:
                raise ChatValidationError("expected_revision must be a non-negative integer")
            if expected_revision != int(row["revision"]):
                return False
        public = str(row["public_id"]) if "public_id" in row.keys() and row["public_id"] else public_id_for(pk, conn)
        active = conn.execute("SELECT * FROM chat_runs WHERE thread_id=? AND user_id=? AND status IN ('queued','running')", (pk, user_id)).fetchall()
        if expected_revision is not None:
            # After the chat_runs read: finalization locks run before thread.
            try:
                _lock_revision(conn, "chat_threads", "revision", row["revision"], "id=? AND user_id=?", (pk, user_id))
            except ChatValidationError:
                return False
        if active and not cancel_active:
            return False
        for run in active:
            conn.execute("UPDATE chat_runs SET cancel_requested=1,updated_at=? WHERE id=?", (db.now(), run["id"]))
            kind = "agent.cancel_requested" if run["parent_run_id"] else "run.cancel_requested"
            store.event(conn, user_id, kind, pk, store._cancel_event_payload(run))
        conn.execute("DELETE FROM chat_runs WHERE thread_id=? AND user_id=?", (pk, user_id))
        conn.execute("DELETE FROM chat_threads WHERE id=? AND user_id=?", (pk, user_id))
        store.event(conn, user_id, "thread.deleted", pk, {"revision": row["revision"] + 1, "id": public})
        store.remember(conn, user_id, command_id, fingerprint, True)
    # Wake live workers after the commit so they stop promptly instead of
    # burning provider quota until the next periodic poll notices the row is gone.
    from . import chat_runs
    from .vault import unpin_run

    for run in active:
        chat_runs.cancel(run["id"])
        # The row is gone, so finish_run can never unpin it; a stale pin
        # keeps the vault unlocked past its idle timeout.
        unpin_run(user_id, run["id"])
    try:
        from . import command_jobs

        command_jobs.cancel_thread(user_id, pk)
    except Exception:
        pass
    return True
def delete_schedule_owned_thread(user_id: int, thread_id: int, *, keep_thread_id: int | None = None) -> bool:
    """Delete one completed schedule-owned thread. Never deletes user chats."""
    from . import chat_store as store

    tid = int(thread_id)
    if keep_thread_id is not None and tid == int(keep_thread_id):
        return False
    with store.transaction() as conn:
        row = conn.execute(
            """SELECT id, schedule_id, public_id FROM chat_threads WHERE id=? AND user_id=?""",
            (tid, user_id),
        ).fetchone()
        if row is None or row["schedule_id"] is None:
            return False
        # Any live run blocks: a background subagent can outlive its root turn,
        # and deleting its row would leave the worker executing for a thread
        # that no longer exists.
        active = conn.execute(
            """SELECT 1 FROM chat_runs WHERE thread_id=? AND user_id=? AND status IN ('queued','running')""",
            (tid, user_id),
        ).fetchone()
        if active:
            return False
        conn.execute("DELETE FROM chat_runs WHERE thread_id=? AND user_id=?", (tid, user_id))
        conn.execute("DELETE FROM chat_threads WHERE id=? AND user_id=? AND schedule_id IS NOT NULL", (tid, user_id))
        public = str(row["public_id"]) if "public_id" in row.keys() and row["public_id"] else tid
        store.event(conn, user_id, "thread.deleted", tid, {"id": public})
    try:
        from . import command_jobs

        command_jobs.cancel_thread(user_id, tid)
    except Exception:
        pass
    return True
# Run ids and message ids are UUIDs; command job ids are 12 hex digits. A
# fork rewrites every occurrence (traces, provider transcripts, "task_id: …"
# lines the model saw) so the copy never shares an id with its source.
_FORK_ID_EDGE = r"(?<![0-9A-Za-z-])(?:{alts})(?![0-9A-Za-z-])"
_FORK_ID_TOKEN = re.compile(r"[0-9A-Za-z-]+")
def _fork_rewriter(id_map: dict):
    """An edge-bounded id made only of [0-9A-Za-z-] is exactly one maximal
    token, so match tokens generically and look them up: linear in the text
    however many ids there are. Ids with other characters (none in practice)
    keep an explicit alternative, tried first as their match is longer."""
    ids = [old for old in id_map if old]
    if not ids:
        return lambda text: text
    odd = [i for i in ids if not _FORK_ID_TOKEN.fullmatch(i)]
    alts = [re.escape(i) for i in sorted(odd, key=len, reverse=True)] + [_FORK_ID_TOKEN.pattern]
    pattern = re.compile(_FORK_ID_EDGE.format(alts="|".join(alts)))

    def rewrite(text):
        if not isinstance(text, str) or not text:
            return text
        return pattern.sub(lambda m: id_map.get(m.group(0), m.group(0)), text)

    return rewrite
def _fork_request(text, rewrite, old_pk: int, new_pk: int):
    text = rewrite(text)
    try:
        req = json.loads(text)
    except (TypeError, ValueError):
        return text
    if isinstance(req, dict) and req.get("thread_id") == old_pk:
        req["thread_id"] = new_pk
        return json.dumps(req, ensure_ascii=False, separators=(",", ":"))
    return text
def _fork_retained(raw, rewrite, old_messages, new_messages) -> str:
    """Carry the provider-history cache over to the fork. Its anchors hash
    message ids, so re-anchor them to the new ids, but only when the cache
    was valid for the source; a stale cache is dropped and rebuilt on send."""
    from . import chat_history

    try:
        memory = json.loads(rewrite(raw or "{}"))
        old_anchor = json.loads(raw or "{}").get("anchor") or []
    except (TypeError, ValueError, AttributeError):
        return "{}"
    if not isinstance(memory, dict) or not old_anchor:
        return "{}"
    if chat_history.anchors(chat_history._retained_display(old_messages))[:len(old_anchor)] != old_anchor:
        return "{}"
    memory["anchor"] = chat_history.anchors(chat_history._retained_display(new_messages))[:len(old_anchor)]
    return json.dumps(memory, ensure_ascii=False, separators=(",", ":"))
def _copyable_columns(conn, table: str, names) -> list:
    """Row columns minus MariaDB generated ones, which refuse explicit values."""
    native = getattr(conn, "native", None)
    if native is None:
        return list(names)
    generated = {r[0] for r in native(
        "SELECT column_name FROM information_schema.columns WHERE table_schema=DATABASE() AND table_name=%s AND extra LIKE %s",
        (table, "%GENERATED%"),
    ).fetchall()}
    return [name for name in names if name not in generated]
def _fork_title(title: str) -> str:
    base = _legacy_text(title, TITLE_MAX) or "New chat"
    suffix = " (fork)"
    return _clean_title(base[: TITLE_MAX - len(suffix)].rstrip() + suffix) or "Fork"
def fork_thread(user_id: int, thread_id, *, expected_revision=None, command_id=None) -> dict | None:
    """Copy a chat or project session with everything it references:
    messages, subagent runs (and their transcripts), command jobs, recaps,
    thread permission grants and artifact links. Every run, message and job
    id is new, so the fork and its source can run side by side."""
    from . import chat_store as store

    with store.transaction() as conn:
        fingerprint, previous = store.command(conn, user_id, command_id, ["fork", thread_id, expected_revision])
        if previous is not None:
            return previous
        pk = resolve_thread_pk(user_id, thread_id, conn)
        if pk is None:
            return None
        src = conn.execute("SELECT * FROM chat_threads WHERE id=? AND user_id=?", (pk, user_id)).fetchone()
        if src is None:
            return None
        store.check_revision(int(src["revision"]), expected_revision)
        active = conn.execute(
            "SELECT COUNT(*) AS n FROM chat_runs WHERE thread_id=? AND user_id=? AND status IN ('queued','running')",
            (pk, user_id),
        ).fetchone()
        if int(active["n"] if active else 0):
            raise ChatValidationError("Wait for the running turn and its subagents to finish, or stop them, before forking.", 409)
        has_jobs = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='command_jobs'").fetchone() is not None
        jobs = conn.execute("SELECT * FROM command_jobs WHERE user_id=? AND thread_id=?", (user_id, pk)).fetchall() if has_jobs else []
        if any(job["status"] in ("queued", "running", "cancelling") for job in jobs):
            raise ChatValidationError("A background command in this chat is still running. Wait for it or stop it before forking.", 409)
        runs = conn.execute("SELECT * FROM chat_runs WHERE thread_id=? AND user_id=? ORDER BY created_at, id", (pk, user_id)).fetchall()
        messages = parse_messages(src["messages"])
        id_map: dict[str, str] = {}
        for msg in messages:
            if msg.get("id"):
                id_map[str(msg["id"])] = str(uuid4())
        for run in runs:
            id_map[str(run["id"])] = str(uuid4())
            if run["message_id"] and str(run["message_id"]) not in id_map:
                id_map[str(run["message_id"])] = str(uuid4())
        for job in jobs:
            id_map[str(job["id"])] = uuid4().hex[:12]
        rewrite = _fork_rewriter(id_map)

        ts = db.now()
        tid = conn.execute("INSERT INTO chat_thread_ids DEFAULT VALUES").lastrowid
        conn.execute("DELETE FROM chat_thread_ids WHERE id=?", (tid,))
        public = _alloc_public_id(conn)
        keys = src.keys()

        def col(name, default=None):
            return src[name] if name in keys and src[name] is not None else default

        new_messages_json = rewrite(src["messages"])
        retained = _fork_retained(col("retained_history", "{}"), rewrite, messages, parse_messages(new_messages_json))

        conn.execute(
            """INSERT INTO chat_threads (id, user_id, title, title_state, model, archived, messages, context, tools,
               created_at, updated_at, active_at, kind, host_id, project_id, public_id, compaction, loaded_tools,
               retained_history, bypass_permissions, auto_permissions, recap_epoch, recap_start_id)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                tid, user_id, _fork_title(src["title"]), "user", src["model"] or "", 0,
                new_messages_json, col("context", ""), col("tools", json.dumps(DEFAULT_TOOLS)),
                ts, ts, ts, col("kind", "chat") or "chat", col("host_id"), col("project_id"), public,
                rewrite(col("compaction", "{}")), col("loaded_tools", "[]"), retained,
                int(col("bypass_permissions", 0) or 0), int(col("auto_permissions", 0) or 0),
                int(col("recap_epoch", 0) or 0), rewrite(col("recap_start_id", "")),
            ),
        )
        mapped = {"id", "thread_id", "parent_run_id", "root_run_id", "message_id", "request", "message", "provider_messages"}
        run_columns = _copyable_columns(conn, "chat_runs", runs[0].keys()) if runs else []
        job_columns = _copyable_columns(conn, "command_jobs", jobs[0].keys()) if jobs else []
        for run in runs:
            names = run_columns
            values = []
            for name in names:
                value = run[name]
                if name == "id":
                    value = id_map[str(value)]
                elif name == "thread_id":
                    value = tid
                elif name in ("parent_run_id", "root_run_id", "message_id") and value:
                    value = id_map.get(str(value), value)
                elif name == "request":
                    value = _fork_request(value, rewrite, pk, tid)
                elif name in mapped:
                    value = rewrite(value)
                values.append(value)
            conn.execute(
                f"INSERT INTO chat_runs ({','.join(names)}) VALUES ({','.join('?' * len(names))})",
                values,
            )
        # The copied runs and history keep the source's image references.
        chat_images.copy_thread(conn, pk, tid)
        for job in jobs:
            names = job_columns
            values = []
            for name in names:
                value = job[name]
                if name == "id":
                    value = id_map[str(value)]
                elif name == "thread_id":
                    value = tid
                elif name == "run_id" and value:
                    value = id_map.get(str(value), value)
                values.append(value)
            conn.execute(
                f"INSERT INTO command_jobs ({','.join(names)}) VALUES ({','.join('?' * len(names))})",
                values,
            )
        for recap in conn.execute(
            "SELECT * FROM chat_recaps WHERE user_id=? AND thread_id=? AND state='ready'", (user_id, pk)
        ).fetchall():
            conn.execute(
                """INSERT INTO chat_recaps (user_id, thread_id, epoch, episode_id, anchor_id, fingerprint, text, state,
                   manual, attempts, lease_token, next_at, quiet_since, model, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (user_id, tid, recap["epoch"], rewrite(recap["episode_id"]), rewrite(recap["anchor_id"]), recap["fingerprint"],
                 recap["text"], "ready", recap["manual"], recap["attempts"], "", "", "", recap["model"], ts),
            )
        for grant in conn.execute(
            "SELECT host_id, kind, value FROM chat_permission_grants WHERE user_id=? AND thread_id=?", (user_id, pk)
        ).fetchall():
            conn.execute(
                "INSERT OR IGNORE INTO chat_permission_grants(user_id, thread_id, host_id, kind, value, created_at) VALUES (?,?,?,?,?,?)",
                (user_id, tid, grant["host_id"], grant["kind"], grant["value"], ts),
            )
        for link in conn.execute("SELECT artifact_id FROM artifact_threads WHERE thread_id=?", (pk,)).fetchall():
            conn.execute("INSERT OR IGNORE INTO artifact_threads(artifact_id, thread_id) VALUES (?,?)", (link["artifact_id"], tid))
        store.thread_event(conn, user_id, tid)
        result = store.thread_view(conn, user_id, tid)
        store.remember(conn, user_id, command_id, fingerprint, result)
        return result
# The old name; the dashboard menu now says Fork.
duplicate_thread = fork_thread
def _project_sidebar_view(row) -> dict:
    from . import hosts

    thread_id = row["thread_id"] if "thread_id" in row.keys() else None
    updated = row["updated_at"] if "updated_at" in row.keys() and row["updated_at"] else row["created_at"]
    return {
        "id": int(row["id"]),
        "host_id": int(row["host_id"]),
        "host_alias": row["host_alias"] if "host_alias" in row.keys() else "",
        "name": row["name"],
        "remote_path": row["remote_path"],
        "default_model": row["default_model"] if "default_model" in row.keys() else "",
        "permission_mode": row["permission_mode"] if "permission_mode" in row.keys() else "project",
        "default_permissions": (row["default_permissions"] or "") if "default_permissions" in row.keys() else "",
        "thread_id": thread_id or None,
        "created_at": row["created_at"],
        "updated_at": updated,
        "running": bool(row["running"]) if "running" in row.keys() else False,
        "color": hosts.project_color(row["id"], row["color"] if "color" in row.keys() else ""),
        "logo_url": hosts.logo_url(row["id"], row["logo_rev"] if "logo_rev" in row.keys() else 0),
        "archived": bool(row["archived"]) if "archived" in row.keys() and row["archived"] else False,
        "archived_at": (row["archived_at"] or "") if "archived_at" in row.keys() else "",
        "sessions": int(row["sessions"] or 0) if "sessions" in row.keys() else 0,
        "last_active_at": (row["last_active_at"] or "") if "last_active_at" in row.keys() else "",
    }
_PROJECT_SIDEBAR_SQL = """
    SELECT p.id, p.host_id, p.name, p.remote_path, p.default_model, p.permission_mode, p.default_permissions,
           p.color, p.logo_rev, p.archived, p.archived_at,
           (SELECT COUNT(*) FROM chat_threads c
            WHERE c.user_id=p.user_id AND c.project_id=p.id AND c.kind='project') AS sessions,
           (SELECT MAX(c.updated_at) FROM chat_threads c
            WHERE c.user_id=p.user_id AND c.project_id=p.id AND c.kind='project') AS last_active_at,
           p.created_at, CASE WHEN p.updated_at IS NULL OR p.updated_at='' THEN p.created_at ELSE p.updated_at END AS updated_at,
           h.alias AS host_alias,
           (SELECT t.public_id FROM chat_threads t
            WHERE t.user_id=p.user_id AND t.host_id=p.host_id AND t.project_id=p.id AND t.archived=0
            ORDER BY t.updated_at DESC, t.id DESC LIMIT 1) AS thread_id,
           EXISTS (
             SELECT 1 FROM chat_threads ct
             JOIN chat_runs r ON r.thread_id=ct.id
             WHERE ct.user_id=p.user_id AND ct.host_id=p.host_id AND ct.project_id=p.id
               AND ct.archived=0 AND r.status IN ('queued','running')
           ) AS running
    FROM user_projects p
    JOIN user_hosts h ON h.id=p.host_id AND h.user_id=p.user_id
    WHERE p.user_id=?
"""
def list_sidebar_projects(user_id: int, conn=None) -> list[dict]:
    sql = _PROJECT_SIDEBAR_SQL + " ORDER BY h.alias, p.name"
    rows = conn.execute(sql, (user_id,)).fetchall() if conn is not None else db.fetchall(sql, (user_id,))
    return [_project_sidebar_view(row) for row in rows]
def search_projects(user_id: int, q: str) -> list[dict]:
    needle = q.strip()
    if not needle:
        return []
    like = "%" + needle.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    rows = db.fetchall(
        _PROJECT_SIDEBAR_SQL + """ AND (
            p.name LIKE ? ESCAPE '\\' OR p.remote_path LIKE ? ESCAPE '\\' OR h.alias LIKE ? ESCAPE '\\'
        ) ORDER BY h.alias, p.name LIMIT 50""",
        (user_id, like, like, like),
    )
    return [_project_sidebar_view(row) for row in rows]
def _match_project_row(conn, user_id: int, host_id: int, cwd: str):
    cwd = _norm_remote_path(cwd)
    rows = conn.execute(
        "SELECT * FROM user_projects WHERE user_id=? AND host_id=?",
        (user_id, host_id),
    ).fetchall()
    exact = [row for row in rows if _norm_remote_path(row["remote_path"]) == cwd]
    if exact:
        return exact[0]
    if not cwd:
        return None
    prefixes = []
    for row in rows:
        root = _norm_remote_path(row["remote_path"])
        if root and (cwd == root or cwd.startswith(root + "/")):
            prefixes.append((len(root), row))
    if not prefixes:
        return None
    prefixes.sort(key=lambda item: -item[0])
    return prefixes[0][1]
def _unique_project_name(conn, user_id: int, host_id: int, name: str) -> str:
    base = _clean_title(name) or "project"
    candidate = base
    n = 2
    while conn.execute(
        "SELECT 1 AS ok FROM user_projects WHERE user_id=? AND host_id=? AND name=?",
        (user_id, host_id, candidate),
    ).fetchone():
        suffix = f" {n}"
        candidate = (base[: TITLE_MAX - len(suffix)] + suffix).strip()
        n += 1
        if n > 50:
            candidate = (base[:40] + " " + new_public_id()[:8]).strip()
            break
    return candidate
def promote_working_session(user_id: int, thread_id, *, host_id=None, cwd: str = "", path: str = "", tool: str = "",
                            existing_only: bool = False):
    """Turn a regular chat into a project session. Messages and thread notes stay as they are.

    existing_only binds only to a project the user already has at that path,
    never creates one: used for writes the permission gate did not mark
    promotable (absolute paths from a home root, approved writes, subagents)."""
    from . import chat_store as store

    if tool and tool not in _FILE_PROMOTE_TOOLS:
        return None
    hid = None
    if host_id is not None and host_id != "":
        try:
            hid = int(host_id)
        except (TypeError, ValueError):
            hid = None
    root = _session_root(cwd, path, tool=tool)
    with store.transaction() as conn:
        pk = resolve_thread_pk(user_id, thread_id, conn)
        if pk is None:
            return None
        row = conn.execute("SELECT * FROM chat_threads WHERE id=? AND user_id=?", (pk, user_id)).fetchone()
        if row is None:
            return None
        keys = row.keys()
        kind = row["kind"] if "kind" in keys and row["kind"] else "chat"
        if kind == "project" and row["project_id"] is not None:
            return None
        if hid is None and "host_id" in keys and row["host_id"] is not None:
            try:
                hid = int(row["host_id"])
            except (TypeError, ValueError):
                hid = None
        host = None
        if hid is not None:
            host = conn.execute(
                "SELECT id, alias, default_cwd FROM user_hosts WHERE id=? AND user_id=?",
                (hid, user_id),
            ).fetchone()
        if host is None:
            return None
        hid = int(host["id"])
        if not root or _temp_root(root):
            return None
        proj = _match_project_row(conn, user_id, hid, root)
        if proj is None:
            if existing_only or tool not in _FILE_PROMOTE_TOOLS or _generic_remote_root(root):
                return None
            name = _unique_project_name(conn, user_id, hid, root.rsplit("/", 1)[-1] or host["alias"] or "project")
            ts = db.now()
            conn.execute(
                """INSERT INTO user_projects(
                    user_id, host_id, name, remote_path, notes, default_model, permission_mode, created_at, updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?)""",
                (user_id, hid, name, root, "", "", "project", ts, ts),
            )
            pid = db.last_id(conn)
            proj = conn.execute("SELECT * FROM user_projects WHERE id=?", (pid,)).fetchone()
        # Run before thread: finalization locks the run row first, so locking
        # the thread here and then the run would deadlock against it.
        run = conn.execute(
            """SELECT id, request FROM chat_runs
               WHERE thread_id=? AND user_id=? AND status IN ('queued','running') AND parent_run_id IS NULL""",
            (pk, user_id),
        ).fetchone()
        conn.execute(
            """UPDATE chat_threads
               SET kind='project', host_id=?, project_id=?, updated_at=?, revision=revision+1
               WHERE id=? AND user_id=?""",
            (hid, int(proj["id"]), db.now(), pk, user_id),
        )
        if run:
            try:
                req = json.loads(run["request"] or "{}")
            except Exception:
                req = {}
            agent = req.get("agent") if isinstance(req.get("agent"), dict) else {}
            agent["coding"] = True
            agent["project_session"] = True
            agent["bound_host_id"] = hid
            agent["bound_cwd"] = proj["remote_path"]
            req["agent"] = agent
            conn.execute("UPDATE chat_runs SET request=? WHERE id=?", (store.dumps(req), run["id"]))
        store.thread_event(conn, user_id, pk)
        return store.thread_view(conn, user_id, pk)
def move_thread_to_project(user_id: int, thread_id, host_id, project_id, *, expected_revision=None, command_id=None) -> dict | None:
    """Make a chat (or another project's session) a session of this project.
    Messages and notes stay; the next turn runs in the project's folder."""
    from . import chat_store as store

    try:
        hid, pid = int(host_id), int(project_id)
    except (TypeError, ValueError):
        raise ChatValidationError("host_id and project_id must be integers", 422) from None
    with store.transaction() as conn:
        fingerprint, previous = store.command(conn, user_id, command_id, ["move_project", thread_id, hid, pid, expected_revision])
        if previous is not None:
            return previous
        pk = resolve_thread_pk(user_id, thread_id, conn)
        if pk is None:
            return None
        row = conn.execute("SELECT * FROM chat_threads WHERE id=? AND user_id=?", (pk, user_id)).fetchone()
        if row is None:
            return None
        store.check_revision(row["revision"], expected_revision)
        proj = conn.execute(
            "SELECT id, archived FROM user_projects WHERE id=? AND user_id=? AND host_id=?",
            (pid, user_id, hid),
        ).fetchone()
        if proj is None:
            raise ChatValidationError("project not found", 404)
        # A running turn keeps the folder it started with; moving it mid-run
        # would change its permission scope under it.
        if conn.execute("SELECT 1 FROM chat_runs WHERE thread_id=? AND status IN ('queued','running')", (pk,)).fetchone():
            raise ChatValidationError("Wait for the running turn and its subagents to finish, or stop them, before moving this chat.", 409)
        ts = db.now()
        conn.execute(
            """UPDATE chat_threads
               SET kind='project', host_id=?, project_id=?, updated_at=?, revision=revision+1
               WHERE id=? AND user_id=?""",
            (hid, pid, ts, pk, user_id),
        )
        if int(proj["archived"] or 0):
            conn.execute("UPDATE user_projects SET archived=0, archived_at='' WHERE id=? AND user_id=?", (pid, user_id))
        store.thread_event(conn, user_id, pk)
        result = store.thread_view(conn, user_id, pk)
        store.remember(conn, user_id, command_id, fingerprint, result)
    return result
