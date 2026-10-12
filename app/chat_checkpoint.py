"""Durable state snapshots (checkpoint cadence) and compaction/meter persistence."""
from __future__ import annotations

import json
import re
import time

from . import db, chat_tx, chat_pub, chat_views



# Durable state snapshot cadence for live runs. Events (patches, tools,
# permissions) are published immediately through the group committer; the
# chat_runs message/request rewrite is only a restart-durability snapshot, so
# it is throttled. Tool/permission/context events force an immediate snapshot.
# Snapshot readers must not treat the user event cursor as "included in the
# row": checkpoint stamps each run with the last committed cursor its state
# write covers (_state_cursors), and snapshots clamp event_cursor to it so a
# reconnecting client replays the intervening patches instead of skipping
# them. Each snapshot rewrites the whole message (megabytes for long
# subagents), so a short cadence made every flush a full row write once
# flushes slowed under concurrency; replay covers the gap on reconnect.
STATE_WRITE_SECONDS = 2.0
_state_write_at: dict[str, float] = {}
_state_cursors: dict[str, int] = {}
# Runs whose latest flush published events but skipped the durable row
# rewrite; their flush loop owes a trailing snapshot once the burst ends.
_state_dirty: set[str] = set()


def durable_state_cursor(run_id: str) -> int | None:
    """Last committed event cursor that the run's durable row reflects."""
    return _state_cursors.get(str(run_id))


def state_cursor_snapshot() -> dict[str, int]:
    """Stamps to use for one snapshot; take them BEFORE its transaction's
    first read. A stamp is set only after its row write commits, so a stamp
    taken first is covered by any row the later snapshot sees. A stamp read
    after the snapshot can belong to a newer row the snapshot cannot see,
    and clamping to it would skip patches the returned row lacks."""
    return dict(_state_cursors)


def state_write_pending(run_id) -> bool:
    return run_id in _state_dirty


def _forget_state_cursor(run_id: str) -> None:
    _state_cursors.pop(str(run_id), None)


def stamp_state_cursor(run_id, user_id) -> None:
    """Record the committed cursor the run's durable row now covers.

    Called right after a state-write commit (and again once publication
    committed): every event published so far is covered by the cumulative
    message just persisted. Reading after publication lets the stamp include
    the flush's own event, so snapshot cursors advance past durable
    boundaries; when the group committer defers the commit the value simply
    stays one event behind (conservative, replay covers the gap)."""
    stamped = chat_tx.committed_cursor(user_id)
    if stamped is not None:
        _state_cursors[str(run_id)] = stamped


def checkpoint(run_id, message, events=None, durable=True, future=None):
    # Plain read: no write transaction, no row locks. Heavy work (strict
    # re-parse, projection) never shares a transaction with publication, so
    # concurrent subagents cannot serialize on each other's payloads.
    row = _checkpoint_row(run_id)
    if row is None or row["status"] not in chat_tx.ACTIVE:
        _state_dirty.discard(run_id)
        if future is not None:
            chat_pub._resolve_future(future, None)
        return
    if durable or row["status"] == "queued":
        # Durable boundaries (tool/permission events, round transitions, the
        # final flush) keep the strict shape check: what restart recovery
        # reads back must parse under the same rules as the seed.
        message = chat_views._message(message, row["message_id"])
    else:
        # Periodic token flushes re-send the Output snapshot, which grew from
        # a strictly parsed seed and was capped as it was built. Re-parsing
        # every tool output on each 200ms tick is the dominant CPU cost of
        # many concurrent streams.
        message = {**message, "id": row["message_id"], "role": "assistant"}
    stored_request, guard = _checkpoint_request(run_id, row)
    request = _mutable_request(stored_request)
    request_changed = False
    for item in events or []:
        activity = item.get("fuse") or {}
        if activity.get("event") == "activity" and activity.get("phase") in ("model", "calling", "tools"):
            request.setdefault("agent", {})["activity"] = dict(activity)
            request_changed = True
        elif activity.get("event") == "tool" and activity.get("status") == "running":
            request.setdefault("agent", {})["activity"] = {"phase": "tools", "tool_id": activity.get("id")}
            request_changed = True
        elif activity.get("event") == "heartbeat":
            agent = request.setdefault("agent", {})
            act = agent.get("activity") if isinstance(agent.get("activity"), dict) else {}
            if act.get("phase") in ("tools", "model", "calling"):
                agent["activity"] = {**act, "heartbeat": True}
            else:
                agent["activity"] = {"phase": "waiting", "heartbeat": True}
            request_changed = True
        elif activity.get("event") == "context" and activity.get("phase") == "compacted":
            request.setdefault("agent", {})["history_output_floor"] = len(message.get("content") or "")
            request_changed = True
    if row["parent_run_id"]:
        seed = request.get("messages") or []
        if "display_seed" not in request:
            request["display_seed"] = seed
            request_changed = True
        if any(s.get("type") == "marker" and s.get("kind") == "compaction" and s.get("summary") for s in message.get("segments") or []):
            # The live handoff marker owns the post-compaction projection.
            if request["display_seed"] != []:
                request["display_seed"] = []
                request_changed = True
    if row["status"] == "queued" and isinstance(request.get("agent"), dict):
        if request["agent"].pop("steer_closed", None) is not None:
            request_changed = True
    steer_parts = (request.get("agent") or {}).get("display_parts") or []
    if any(part.get("awaiting_delivery") for part in steer_parts):
        chat_views._advance_steer_display(request, message)
        request_changed = True
    payload = {"run_id": run_id, "message_id": row["message_id"]}
    if row["parent_run_id"]:
        payload["current"] = chat_views.agent_current_activity(message, (request.get("agent") or {}).get("activity"))
    projected = chat_views.display_messages(request, message, row["message_id"], live=True)
    materialize_thread = False
    if (request.get("agent") or {}).get("display_parts"):
        from .chat_runs import _segments_patch

        previous = chat_views.display_messages(stored_request, _checkpoint_message(run_id, row), row["message_id"], live=True)
        if set(projected) != set(previous):
            materialize_thread = True
        old, current = previous[row["message_id"]], projected[row["message_id"]]
        patch = {"event": "message.patch", "elapsedMs": current.get("elapsedMs"), "usage": current.get("usage"), "error": current.get("error", "")}
        source_patch = next((entry.get("fuse") for entry in events or [] if (entry.get("fuse") or {}).get("event") == "message.patch"), None)
        if source_patch and type(source_patch.get("streamOut")) is int and source_patch["streamOut"] >= 0:
            patch["streamOut"] = source_patch["streamOut"]
        for field in ("content", "reasoning"):
            before, after = old.get(field) or "", current.get(field) or ""
            offset = 0
            while offset < min(len(before), len(after)) and before[offset] == after[offset]:
                offset += 1
            patch[field] = {"from": len(before[:offset].encode("utf-16-le")) // 2, "text": after[offset:]}
        segments = _segments_patch(old.get("segments") or [], current.get("segments") or [])
        if segments:
            patch["segments"] = segments
        extra_events = [entry for entry in events or [] if (entry.get("fuse") or {}).get("event") not in ("tool", "message.patch") and entry.get("fuse")]
        payload.update(events=[{"fuse": patch}, *extra_events], tools=current.get("tools") or [], permissions=current.get("permissions") or [],
                       updates=[msg for mid, msg in projected.items() if mid != row["message_id"] and msg != previous.get(mid)])
    else:
        payload.update({"events": events} if events is not None else {"message": message})
        # Subagent checkpoints travel as agent.progress; the dock permission
        # queue is fed from payload.permissions (the raw events array is
        # only replayed when the child's own panel is open). Without this
        # key an ask raised inside a child never renders and times out.
        payload["permissions"] = message.get("permissions") or []
    if row["parent_run_id"]:
        payload["parent_run_id"] = row["parent_run_id"]
        payload["root_run_id"] = row["root_run_id"]
        kind = "agent.progress"
    else:
        kind = "run.progress"

    # 1) Durable state snapshot, own transaction, per-run row lock only.
    now = time.monotonic()
    force_state = (
        durable
        or row["status"] == "queued"
        or bool((request.get("agent") or {}).get("display_parts"))
        or materialize_thread
    )
    if not force_state:
        last = _state_write_at.get(run_id)
        force_state = last is None or now - last >= STATE_WRITE_SECONDS
        if len(_state_write_at) > 4096:
            cutoff = now - 600.0
            for ident, stamp in list(_state_write_at.items()):
                if stamp < cutoff:
                    _state_write_at.pop(ident, None)
                    _state_cursors.pop(ident, None)
                    _state_dirty.discard(ident)
                    _request_cache.pop(ident, None)
    if request_changed and request == stored_request:
        # e.g. a repeated heartbeat: rewriting identical bytes is pure cost.
        request_changed = False
    if force_state:
        with chat_tx.transaction() as conn:
            # Optimistic concurrency: without the old cross-read row lock, a
            # concurrent finish/save_turn_progress/seal between our read and
            # this write must not be clobbered by stale state. updated_at has
            # second granularity, so the request itself is the version: its
            # stored digest (or, before that column exists, the blob).
            # Rowcount 0 means superseded — events still publish, the state
            # snapshot is simply skipped.
            # Lock order matches finish_run: chat_runs before chat_threads
            # before the event cursor.
            if request_changed:
                cur = conn.execute(
                    "UPDATE chat_runs SET message=?,request=?,status='running',updated_at=? WHERE id=? AND status IN ('queued','running') AND " + _request_guard_sql(guard),
                    (chat_tx.dumps(message), chat_tx.dumps(request), db.now(), run_id, guard),
                )
            else:
                # Token-only flush: the request blob is byte-identical to the
                # guarded value, so skip transporting and rewriting it.
                cur = conn.execute(
                    "UPDATE chat_runs SET message=?,status='running',updated_at=? WHERE id=? AND status IN ('queued','running') AND " + _request_guard_sql(guard),
                    (chat_tx.dumps(message), db.now(), run_id, guard),
                )
            _remember_request(conn, run_id, request if request_changed else stored_request, cur, guard, request_changed)
            if materialize_thread and getattr(cur, "rowcount", 1):
                from . import chats

                thread = conn.execute("SELECT messages FROM chat_threads WHERE id=? AND user_id=?", (row["thread_id"], row["user_id"])).fetchone()
                if thread:
                    materialized = chat_views._project_thread_messages(chats.parse_messages(thread["messages"]), projected, request)
                    conn.execute("UPDATE chat_threads SET messages=?,revision=revision+1 WHERE id=?", (chat_tx.dumps(materialized), row["thread_id"]))
                    chat_tx.thread_event(conn, row["user_id"], row["thread_id"], transcript_changed=True)
            wrote = bool(getattr(cur, "rowcount", 1))
        _state_write_at[run_id] = now
        if wrote:
            stamp_state_cursor(run_id, row["user_id"])
            _state_dirty.discard(run_id)
        else:
            # Superseded: the row does not hold this state, so the cursor must
            # not claim it does. Keep the debt; the flush loop retries.
            _state_dirty.add(run_id)
            force_state = False
    else:
        # Events still publish through the group committer, but the durable
        # row now lags them; the run's flush loop must catch it up (see
        # flush_pending_state) or a quiet stream leaves the tail undurable.
        _state_dirty.add(run_id)

    # 2) Publication through the group committer (tiny batched transaction).
    # The guard drops progress for a run that finalized while this checkpoint
    # was in flight, so a terminal event can never be followed by stale patches.
    chat_pub.publish(row["user_id"], kind, row["thread_id"], payload, guard_run=run_id, future=future)
    if force_state:
        # Without a live group committer publish() already committed, so the
        # cursor read now includes this checkpoint's own event; refine the
        # stamp so snapshot cursors advance past the durable boundary.
        stamp_state_cursor(run_id, row["user_id"])
        return True
    return None


def flush_pending_state(run_id, message, future=None):
    """Trailing durable snapshot for a run whose last flush was throttled.

    The skipped checkpoint's events already committed through the group
    committer; only the chat_runs row lags. When the stream goes quiet no
    later flush carries the tail, so the flush loop calls this on its idle
    ticks: same optimistic request guard as checkpoint, strict shape check,
    and no publication (nothing new to tell clients)."""
    row = _checkpoint_row(run_id)
    if row is None or row["status"] not in chat_tx.ACTIVE:
        _state_dirty.discard(run_id)
        if future is not None:
            chat_pub._resolve_future(future, None)
        return
    message = chat_views._message(message, row["message_id"])
    stored_request, guard = _checkpoint_request(run_id, row)
    with chat_tx.transaction() as conn:
        cur = conn.execute(
            "UPDATE chat_runs SET message=?,status='running',updated_at=? WHERE id=? AND status IN ('queued','running') AND " + _request_guard_sql(guard),
            (chat_tx.dumps(message), db.now(), run_id, guard),
        )
        _remember_request(conn, run_id, stored_request, cur, guard, False)
        wrote = bool(getattr(cur, "rowcount", 1))
    _state_write_at[run_id] = time.monotonic()
    if wrote:
        stamp_state_cursor(run_id, row["user_id"])
        _state_dirty.discard(run_id)
    if future is not None:
        chat_pub._resolve_future(future, None)


# Checkpoints run several times a second per live run. Reading the request and
# message blobs back each time (megabytes for long subagents) dominated their
# cost, so the row read carries only the request digest; the parsed request is
# cached per run and re-read only when another writer changed it.
_request_cache: dict[str, tuple[str, dict]] = {}
_digest_column: tuple[object, bool] | None = None


def _has_request_digest() -> bool:
    """Whether chat_runs.request_digest exists (scripts/sql/2026-09-25-*)."""
    global _digest_column
    conn = db.connect()
    if _digest_column is None or _digest_column[0] is not conn:
        present = any(column[1] == "request_digest" for column in db.fetchall("PRAGMA table_info(chat_runs)"))
        _request_cache.clear()
        _digest_column = (conn, present)
    return _digest_column[1]


def _checkpoint_row(run_id):
    if not _has_request_digest():
        return db.fetchone(
            "SELECT id,status,message_id,user_id,thread_id,parent_run_id,root_run_id,request,message,updated_at FROM chat_runs WHERE id=?",
            (run_id,),
        )
    row = db.fetchone(
        "SELECT id,status,message_id,user_id,thread_id,parent_run_id,root_run_id,request_digest,updated_at FROM chat_runs WHERE id=?",
        (run_id,),
    )
    if row is None or row["status"] not in chat_tx.ACTIVE:
        _request_cache.pop(run_id, None)
    return row


def _checkpoint_request(run_id, row):
    """Return (stored request, guard value) for a _checkpoint_row row.

    The request is shared with the cache: callers copy it (_mutable_request)
    before changing anything. The guard is the digest the request was read
    at, or the raw blob when the digest column does not exist yet."""
    if not _has_request_digest():
        raw = row["request"] or ""
        return json.loads(raw or "{}"), raw
    cached = _request_cache.get(run_id)
    if cached is not None and cached[0] == row["request_digest"]:
        return cached[1], cached[0]
    fresh = db.fetchone("SELECT request,request_digest FROM chat_runs WHERE id=?", (run_id,))
    if fresh is None:
        return {}, row["request_digest"]
    request = json.loads(fresh["request"] or "{}")
    _request_cache[run_id] = (fresh["request_digest"], request)
    return request, fresh["request_digest"]


def _checkpoint_message(run_id, row):
    if "message" in row.keys():
        return json.loads(row["message"] or "{}")
    fresh = db.fetchone("SELECT message FROM chat_runs WHERE id=?", (run_id,))
    return json.loads((fresh["message"] if fresh else "") or "{}")


def _mutable_request(request):
    """Copy every level checkpoint() mutates: top-level keys, agent keys and
    steer display parts. Everything else stays shared with the cache."""
    out = dict(request)
    agent = out.get("agent")
    if isinstance(agent, dict):
        agent = dict(agent)
        parts = agent.get("display_parts")
        if isinstance(parts, list):
            agent["display_parts"] = [dict(part) if isinstance(part, dict) else part for part in parts]
        out["agent"] = agent
    return out


def _request_guard_sql(guard) -> str:
    return "request_digest=?" if _has_request_digest() else "COALESCE(request,'')=?"


def _remember_request(conn, run_id, request, cur, guard, rewritten):
    """Keep the cache on the digest this transaction left, or drop it."""
    if not _has_request_digest():
        return
    if not getattr(cur, "rowcount", 0):
        _request_cache.pop(run_id, None)
        return
    if not rewritten:
        # The guard matched and request was not written: digest unchanged.
        _request_cache[run_id] = (guard, request)
        return
    row = conn.execute("SELECT request_digest FROM chat_runs WHERE id=?", (run_id,)).fetchone()
    if row is None:
        _request_cache.pop(run_id, None)
    else:
        _request_cache[run_id] = (row["request_digest"], request)


def _compaction_row(conn, user_id, thread_id):
    from . import chats

    pk = chats.resolve_thread_pk(user_id, thread_id, conn)
    if pk is None:
        return None, None
    row = conn.execute("SELECT * FROM chat_threads WHERE id=? AND user_id=?", (pk, user_id)).fetchone()
    return pk, row


def _hold_revision(conn, pk, user_id, row) -> bool:
    """Row-lock the thread at the revision it was read at; False when another
    writer moved it. The read above is a plain (snapshot) read, so without this
    two compaction writers could both derive from the same state and the later
    one silently drop the other's checkpoint. Take it after any chat_runs read:
    run -> thread is the lock order finalization uses."""
    cur = conn.execute("UPDATE chat_threads SET revision=revision WHERE id=? AND user_id=? AND revision=?",
                       (pk, user_id, row["revision"]))
    return bool(cur.rowcount)


def load_compaction(user_id, thread_id):
    from . import chats, context_compact as ctxmod, context_meter

    pk = chats.resolve_thread_pk(user_id, thread_id)
    if pk is None:
        return None
    row = db.fetchone("SELECT compaction FROM chat_threads WHERE id=? AND user_id=?", (pk, user_id))
    if row is None:
        return None
    keys = row.keys() if hasattr(row, "keys") else []
    raw = row["compaction"] if "compaction" in keys else "{}"
    return ctxmod.parse_state(raw)


def save_child_meter(run_id, meter):
    """Persist occupancy on the child run's request.agent — never the parent thread."""
    if not run_id or not isinstance(meter, dict):
        return
    with chat_tx.transaction() as conn:
        row = conn.execute("SELECT id,status,request,parent_run_id FROM chat_runs WHERE id=?", (run_id,)).fetchone()
        if row is None or not row["parent_run_id"] or row["status"] not in chat_tx.ACTIVE:
            return
        try:
            req = json.loads(row["request"] or "{}")
        except (json.JSONDecodeError, TypeError):
            return
        if not isinstance(req, dict):
            return
        agent = req.get("agent") if isinstance(req.get("agent"), dict) else {}
        agent["meter"] = {
            "used": int(meter.get("used") or 0),
            "limit": int(meter.get("limit") or 0),
            "model": str(meter.get("model") or ""),
            "ratio": meter.get("ratio"),
            "warn": bool(meter.get("warn")),
            "auto": bool(meter.get("auto")),
            "estimated": bool(meter.get("estimated")),
            "scope": str(meter.get("scope") or "prompt"),
            "parts": meter.get("parts") if isinstance(meter.get("parts"), dict) else None,
            "calibration": meter.get("calibration") if isinstance(meter.get("calibration"), dict) else None,
            "run_id": str(run_id),
        }
        req["agent"] = agent
        conn.execute("UPDATE chat_runs SET request=?,updated_at=? WHERE id=?", (chat_tx.dumps(req), db.now(), run_id))


def save_meter(user_id, thread_id, meter):
    from . import context_compact as ctxmod, chats, hosts, context_meter

    if not isinstance(meter, dict):
        return
    with chat_tx.transaction() as conn:
        pk, row = _compaction_row(conn, user_id, thread_id)
        if row is None:
            return
        keys = row.keys() if hasattr(row, "keys") else []
        state = ctxmod.parse_state(row["compaction"] if "compaction" in keys else None)
        if meter.get("scope") == "prompt" and meter.get("run_id"):
            owner = conn.execute("SELECT id FROM chat_runs WHERE id=? AND thread_id=? AND user_id=? AND parent_run_id IS NULL AND status IN ('queued','running')",
                                 (meter["run_id"], pk, user_id)).fetchone()
            if owner is None or meter.get("model") != row["model"]:
                return
            # A compaction landed since the read: writing this state back
            # would erase its checkpoint. The next meter write catches up.
            if not _hold_revision(conn, pk, user_id, row):
                return
            state["meter"] = dict(meter)
            probe = state["meter"].pop("cache_probe", None)
            if probe:
                state["cache"] = probe
            conn.execute("UPDATE chat_threads SET compaction=? WHERE id=? AND user_id=?", (ctxmod.dump_state(state), pk, user_id))
            # Compaction state is view-visible: publish so the thread watermark
            # moves, or get_thread's unchanged short-circuit serves stale data.
            chat_tx.thread_event(conn, user_id, pk)
            return
        messages = chats.parse_messages(row["messages"])
        run = conn.execute(
            "SELECT message_id,message FROM chat_runs WHERE thread_id=? AND parent_run_id IS NULL AND status IN ('queued','running')",
            (pk,),
        ).fetchone()
        if run:
            current = json.loads(run["message"])
            messages = [current if msg.get("id") == run["message_id"] else msg for msg in messages]
        if not _hold_revision(conn, pk, user_id, row):
            return
        from . import chat_history
        replay, _info = chat_history.build(conn, user_id, pk, messages, state)
        state["meter"] = context_meter.standing_meter(
            chat_history.for_model(replay, row["model"]), model=row["model"],
            type_id=context_meter.source_type_for_model(row["model"], meter),
            override=state.get("window_override") or meter.get("limit"), prior=meter,
        )
        conn.execute(
            "UPDATE chat_threads SET compaction=? WHERE id=? AND user_id=?",
            (ctxmod.dump_state(state), pk, user_id),
        )
        chat_tx.thread_event(conn, user_id, pk)


def save_compaction(user_id, thread_id, state, *, bump=True, event_name="compaction.updated"):
    from . import chats, context_compact as ctxmod, context_meter

    parsed = ctxmod.parse_state(state)
    with chat_tx.transaction() as conn:
        pk, row = _compaction_row(conn, user_id, thread_id)
        if row is None:
            raise chats.ChatValidationError("chat not found", 404)
        dumped = ctxmod.dump_state(parsed)
        ts = db.now()
        if bump:
            conn.execute(
                "UPDATE chat_threads SET compaction=?, revision=revision+1, updated_at=? WHERE id=? AND user_id=?",
                (dumped, ts, pk, user_id),
            )
            chat_tx.thread_event(conn, user_id, pk)
        else:
            conn.execute(
                "UPDATE chat_threads SET compaction=?, updated_at=? WHERE id=? AND user_id=?",
                (dumped, ts, pk, user_id),
            )
            # Same contract as save_meter: a view-visible write must advance the
            # thread watermark even without a revision bump.
            chat_tx.thread_event(conn, user_id, pk)
        view = chat_views.thread_view(conn, user_id, pk)
        payload = (view or {}).get("compaction") or {"meter": parsed.get("meter")}
        chat_tx.event(conn, user_id, event_name, pk, payload)
        return view


def prepare_compact(user_id, thread_id, expected_revision, command_id):
    from . import chats

    with chat_tx.transaction() as conn:
        fingerprint, previous = chat_tx.command(conn, user_id, command_id, ["compact", thread_id, expected_revision])
        if previous is not None:
            return previous
        pk, row = _compaction_row(conn, user_id, thread_id)
        if row is None:
            raise chats.ChatValidationError("chat not found", 404)
        chat_tx.check_revision(row["revision"], expected_revision)
        if conn.execute(
            """SELECT 1 FROM chat_runs WHERE thread_id=? AND status IN ('queued','running')
               AND parent_run_id IS NULL""",
            (pk,),
        ).fetchone():
            raise chats.ChatValidationError("chat already has an active run", 409)
        view = chat_views.thread_view(conn, user_id, pk)
        from . import chat_history, context_compact as ctxmod
        messages = chats.parse_messages(row["messages"])
        history, history_info = chat_history.build(conn, user_id, pk, messages, ctxmod.parse_state(row["compaction"]))
        return {"thread": view, "fingerprint": fingerprint, "idempotent": False,
                "history": history, "history_info": history_info}


def remember_compact(user_id, command_id, fingerprint, thread, *, compacted: bool):
    with chat_tx.transaction() as conn:
        chat_tx.remember(conn, user_id, command_id, fingerprint, {"thread": thread, "compacted": compacted, "idempotent": True})


def finish_compact(user_id, thread_id, checkpoint, *, command_id, fingerprint, expected_revision=None, replay=None, history_info=None):
    from . import chats, context_compact as ctxmod, hosts, context_meter

    with chat_tx.transaction() as conn:
        pk, row = _compaction_row(conn, user_id, thread_id)
        if row is None:
            raise chats.ChatValidationError("chat not found", 404)
        chat_tx.check_revision(row["revision"], expected_revision)
        if conn.execute(
            """SELECT 1 FROM chat_runs WHERE thread_id=? AND status IN ('queued','running')
               AND parent_run_id IS NULL""",
            (pk,),
        ).fetchone():
            raise chats.ChatValidationError("chat already has an active run", 409)
        # Two compactions from the same revision must not both succeed.
        if not _hold_revision(conn, pk, user_id, row):
            raise chats.ChatValidationError("revision conflict; reload the latest state", 409)
        keys = row.keys() if hasattr(row, "keys") else []
        state = ctxmod.parse_state(row["compaction"] if "compaction" in keys else None)
        messages = chats.parse_messages(row["messages"])
        if checkpoint.get("provider") and messages:
            # The handoff summarizes every row; an edit after them keeps it.
            checkpoint = {**checkpoint, "display_through_id": messages[-1]["id"]}
        state = ctxmod.append_checkpoint(state, checkpoint)
        from . import chat_history
        if replay is not None:
            rebuilt = [*ctxmod.continuation(checkpoint["summary"]), *ctxmod.retained_skill_rows(replay or [])]
            conn.execute("UPDATE chat_threads SET retained_history=? WHERE id=? AND user_id=?",
                (chat_history.envelope(messages, rebuilt, state, info=history_info), pk, user_id))
        projected, _info = chat_history.build(conn, user_id, pk, messages, state)
        model = row["model"] or ""
        meter = context_meter.standing_meter(
            chat_history.for_model(projected, model),
            model=model,
            type_id=context_meter.source_type_for_model(model, state.get("meter")),
            override=state.get("window_override"),
            prior=state.get("meter"),
        )
        meter["estimated"] = True
        meter["standing"] = True
        state["meter"] = meter
        ts = db.now()
        conn.execute(
            "UPDATE chat_threads SET compaction=?, revision=revision+1, updated_at=? WHERE id=? AND user_id=?",
            (ctxmod.dump_state(state), ts, pk, user_id),
        )
        chat_tx.thread_event(conn, user_id, pk)
        view = chat_views.thread_view(conn, user_id, pk)
        chat_tx.event(conn, user_id, "compaction.done", pk, (view or {}).get("compaction") or {})
        chat_tx.remember(
            conn,
            user_id,
            command_id,
            fingerprint,
            {"thread": view, "compacted": True, "idempotent": True},
        )
        return view


def apply_compaction_checkpoint(user_id, thread_id, checkpoint, *, meter=None, run_id=None):
    from . import chats, context_compact as ctxmod, context_meter

    for attempt in range(3):
        with chat_tx.transaction() as conn:
            pk, row = _compaction_row(conn, user_id, thread_id)
            if row is None:
                raise chats.ChatValidationError("chat not found", 404)
            # Moved since the read: retry from a fresh snapshot rather than
            # writing back state that lacks the other writer's change.
            if not _hold_revision(conn, pk, user_id, row):
                if attempt == 2:
                    raise chats.ChatValidationError("revision conflict; reload the latest state", 409)
                continue
            keys = row.keys() if hasattr(row, "keys") else []
            state = ctxmod.parse_state(row["compaction"] if "compaction" in keys else None)
            if checkpoint and checkpoint.get("provider") and run_id:
                # A turn's handoff summarizes up to that turn's reply row; an
                # Undo, Retry or Edit after it keeps the handoff.
                owner = conn.execute(
                    "SELECT message_id FROM chat_runs WHERE id=? AND thread_id=? AND parent_run_id IS NULL",
                    (run_id, pk),
                ).fetchone()
                if owner is not None and owner["message_id"]:
                    checkpoint = {**checkpoint, "display_through_id": str(owner["message_id"])}
            if checkpoint:
                state = ctxmod.append_checkpoint(state, checkpoint)
            if meter:
                state["meter"] = meter
            ts = db.now()
            conn.execute(
                "UPDATE chat_threads SET compaction=?, revision=revision+1, updated_at=? WHERE id=? AND user_id=?",
                (ctxmod.dump_state(state), ts, pk, user_id),
            )
            chat_tx.thread_event(conn, user_id, pk)
            view = chat_views.thread_view(conn, user_id, pk)
            chat_tx.event(conn, user_id, "compaction.done", pk, (view or {}).get("compaction") or {})
            return view


def save_provider_messages(run_id, messages):
    save_turn_progress(run_id, messages)


def read_file_image(user_id, run_id: str, event_id: str):
    """Return normalized pixels from this user's saved run, never from a host path."""
    import base64
    import binascii

    if not all(re.fullmatch(r"[A-Za-z0-9_-]{1,80}", value or "") for value in (run_id, event_id)):
        return None
    row = db.fetchone("SELECT provider_messages FROM chat_runs WHERE id=? AND user_id=?", (run_id, user_id))
    if not row:
        return None
    try:
        messages = json.loads(row["provider_messages"] or "[]")
    except (TypeError, ValueError):
        return None
    for message in reversed(messages if isinstance(messages, list) else []):
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        parts = message.get("content")
        if not isinstance(parts, list):
            continue
        for index, part in enumerate(parts[:-1]):
            if not isinstance(part, dict) or part.get("type") != "text":
                continue
            text = part.get("text")
            if not isinstance(text, str) or not text.startswith("[Fuse: untrusted read_file image output,"):
                continue
            try:
                label = json.loads(text.split("\n", 1)[1])
            except (IndexError, TypeError, ValueError):
                continue
            if not isinstance(label, dict) or label.get("event_id") != event_id:
                continue
            image = parts[index + 1]
            if not isinstance(image, dict) or image.get("type") != "image_url":
                continue
            image_url = image.get("image_url")
            url = image_url.get("url") if isinstance(image_url, dict) else None
            if not isinstance(url, str) or len(url) > 7 * 1024 * 1024:
                continue
            header, separator, data = url.partition(",")
            if not separator or header not in ("data:image/png;base64", "data:image/jpeg;base64", "data:image/webp;base64"):
                continue
            try:
                pixels = base64.b64decode(data, validate=True)
            except (ValueError, binascii.Error):
                continue
            if 0 < len(pixels) <= 5 * 1024 * 1024:
                return header[5:].split(";", 1)[0], pixels
    return None


def save_turn_progress(run_id, messages, loaded_tools=None, extra=None):
    """Persist the provider transcript (and optional loaded catalog) without a WS event.

    Cheap: one UPDATE, no chat_events row. Call after each completed tool and after
    the assistant tool_calls for a round are known.
    """
    if not isinstance(messages, list):
        return
    # This is recovery/context state, not a UI preview. Only explicit runtime
    # compaction may shorten it; storage tail limits silently lost history.
    payload = chat_tx.dumps(messages)
    scan = payload
    tools_json = None
    if loaded_tools is not None:
        if not isinstance(loaded_tools, list):
            loaded_tools = []
        tools_json = chat_tx.dumps([str(name)[:64] for name in loaded_tools[:64] if isinstance(name, str)])
    with chat_tx.transaction() as conn:
        row = conn.execute(
            "SELECT id,status,request,user_id,thread_id FROM chat_runs WHERE id=?",
            (run_id,),
        ).fetchone()
        if row is None or row["status"] not in chat_tx.ACTIVE:
            return
        req = json.loads(row["request"])
        agent = req.get("agent") if isinstance(req.get("agent"), dict) else {}
        extra = extra if isinstance(extra, dict) else {}
        receipt_ids = extra.get("command_delivery_ids") or []
        if isinstance(extra.get("prompt_snapshot"), dict):
            req["prompt_snapshot"] = extra["prompt_snapshot"]
        from . import command_jobs as command_jobs_mod
        from . import monitor as monitor_mod

        confirmed = []
        for ident in receipt_ids:
            ident = str(ident or "")
            parsed = monitor_mod.parse_event_receipt(ident)
            if parsed:
                job_id, seq = parsed
                if chat_tx._json_needle(f'<monitor_event id="{job_id}" seq="{seq}"') in scan:
                    confirmed.append(ident)
            elif (
                chat_tx._json_needle(f'<command_result id="{ident}"') in scan
                or chat_tx._json_needle(f'<monitor_result id="{ident}"') in scan
            ):
                confirmed.append(ident)
        if confirmed:
            command_jobs_mod.mark_delivered(confirmed, conn=conn)
        if tools_json is not None:
            agent["loaded_tools"] = json.loads(tools_json)
        if extra.get("allowed_urls") is not None:
            agent["allowed_urls"] = [str(u)[:2000] for u in extra["allowed_urls"] if isinstance(u, str)][:64]
        if extra.get("search_queries") is not None:
            try:
                agent["search_queries"] = max(0, int(extra["search_queries"]))
            except (TypeError, ValueError):
                pass
        if extra.get("fetches") is not None:
            try:
                agent["fetches"] = max(0, int(extra["fetches"]))
            except (TypeError, ValueError):
                pass
        if extra.get("fetch_attempts") is not None:
            try:
                agent["fetch_attempts"] = max(0, int(extra["fetch_attempts"]))
            except (TypeError, ValueError):
                pass
        if extra.get("trace_round") is not None:
            try:
                # Monotonic: a resume must never hand out a trace id again.
                agent["trace_round"] = max(int(agent.get("trace_round") or 0), int(extra["trace_round"]))
            except (TypeError, ValueError):
                pass
        delivered_children: list[str] = []
        delivered_generations = {}
        pending_steers = agent.get("pending_steers")
        if isinstance(pending_steers, list) and pending_steers:
            # Cleared in the same transaction that persists the transcript:
            # acknowledgment and durable delivery are atomic.
            agent["pending_steers"] = [
                item for item in pending_steers
                if not (isinstance(item, dict) and chat_tx._json_needle(f"(steer:{item.get('id')})") in scan)
            ]
            if len(agent["pending_steers"]) < len(pending_steers):
                agent["steer_continuations"] = 0
        pending_results = agent.get("pending_child_results")
        if isinstance(pending_results, list) and pending_results:
            kept = []
            for item in pending_results:
                if isinstance(item, dict):
                    text = str(item.get("text") or "")
                    if text and chat_tx._json_needle(text) in scan:
                        delivered_children.append(str(item.get("id") or ""))
                        delivered_generations[str(item.get("id") or "")] = str(item.get("generation") or "")
                    else:
                        kept.append(item)
                else:
                    kept.append(item)
            agent["pending_child_results"] = kept
            kept_ids = {
                str(item.get("id") or "") for item in kept if isinstance(item, dict)
            }
            delivered_children = [cid for cid in delivered_children if cid and cid not in kept_ids]
        pending_followups = agent.get("pending_followups")
        if isinstance(pending_followups, list) and pending_followups:
            agent["pending_followups"] = [
                item for item in pending_followups
                if chat_views.followup_text(item) and not chat_views._followup_delivered(item, scan)
            ]
        req["agent"] = agent
        if tools_json is None:
            conn.execute(
                "UPDATE chat_runs SET provider_messages=?, request=?, updated_at=? WHERE id=?",
                (payload, chat_tx.dumps(req), db.now(), run_id),
            )
        else:
            conn.execute(
                "UPDATE chat_runs SET provider_messages=?, loaded_tools=?, request=?, updated_at=? WHERE id=?",
                (payload, tools_json, chat_tx.dumps(req), db.now(), run_id),
            )
        for cid in delivered_children:
            if not cid:
                continue
            crow = conn.execute("SELECT user_id,thread_id,status,request FROM chat_runs WHERE id=?", (cid,)).fetchone()
            if crow is None or crow["status"] not in chat_tx.TERMINAL:
                # A re-spawned child (new leg in flight) keeps wake_sent=0;
                # that leg owes a fresh report and must still wake the thread.
                continue
            try:
                creq = json.loads(crow["request"])
            except (json.JSONDecodeError, TypeError):
                continue
            if not isinstance(creq, dict):
                continue
            cagent = creq.get("agent") if isinstance(creq.get("agent"), dict) else {}
            if str(cagent.get("delivery_generation") or "") != delivered_generations.get(cid, ""):
                continue
            if cagent.get("wake_sent"):
                continue
            cagent["wake_sent"] = True
            creq["agent"] = cagent
            conn.execute("UPDATE chat_runs SET request=? WHERE id=?", (chat_tx.dumps(creq), cid))
            # Open agent rows drop "Report pending delivery" now, not on the
            # next unrelated refresh.
            chat_tx.event(conn, crow["user_id"], "agent.delivered", crow["thread_id"],
                          {"run_id": cid, "child_id": cid, "status": crow["status"], "wake_state": "delivered"})
