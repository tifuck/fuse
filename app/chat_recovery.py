"""Run recovery: abandon dead workers, resume interrupted runs after a restart or Continue."""
from __future__ import annotations

import json
import logging
from uuid import uuid4

from . import db, chat_images, chat_schema, chat_tx, chat_views, chat_checkpoint
from .chat_run_state import (
    DEFAULT_REMAINING_SECONDS,
    INTERRUPT_NOTICE,
    MAX_RESUME_ATTEMPTS,
    RESUME_NOTICE,
    STOP_NOTICE,
    USER_RESUME_NOTICE,
    USER_STOP_TOOL_NOTICE,
    _append_notice,
    _root_admission_lock,
    finish_run,
    seal_provider_messages,
)

logger = logging.getLogger(__name__)


def _interrupt_abandoned(row, notice=INTERRUPT_NOTICE):
    _finalize_abandoned(row, "interrupted", notice)


def abort_active(run_id, status="cancelled", notice=None):
    """Force an active run into a terminal status. Never raises to the caller."""
    if status not in chat_tx.TERMINAL:
        status = "interrupted"
    try:
        row = db.fetchone("SELECT * FROM chat_runs WHERE id=?", (run_id,))
        if row is None or row["status"] not in chat_tx.ACTIVE:
            return False
        try:
            from . import workspace

            workspace.cancel_run_jobs(run_id)
        except Exception:
            pass
        chat_checkpoint._request_cache.pop(run_id, None)
        _finalize_abandoned(row, status, notice)
        return True
    except Exception:
        logger.exception("Failed to abort chat run %s", run_id)
        try:
            _force_terminal(run_id, status, None)
        except Exception:
            logger.exception("Failed to force-terminal chat run %s", run_id)
        return False


def _finalize_abandoned(row, status, notice=None):
    from . import chat_children
    if notice is None:
        notice = STOP_NOTICE if status == "cancelled" else INTERRUPT_NOTICE
    try:
        message = json.loads(row["message"]) if row["message"] else {}
        if not isinstance(message, dict):
            message = {}
    except (json.JSONDecodeError, TypeError, ValueError):
        message = {}
    message["stopped"] = True
    _append_notice(message, notice)
    if status == "failed" and notice and not message.get("error"):
        from . import chats

        message["error"] = str(notice)[: chats.ERROR_MAX]
    for tool in message.get("tools") or []:
        if not isinstance(tool, dict) or tool.get("status") not in ("running", "progress"):
            continue
        if tool.get("name") == "spawn_subagent" and tool.get("child_id"):
            child = conn_child_status(row["user_id"], str(tool["child_id"]))
            if child == "completed":
                tool["status"] = "done"
            elif child == "failed":
                tool["status"] = "error"
            else:
                tool["status"] = "interrupted"
            if child:
                tool["execution_status"] = child
        else:
            tool["status"] = "interrupted"
    try:
        finish_run(row["id"], message, status)
        current = db.fetchone("SELECT status FROM chat_runs WHERE id=?", (row["id"],))
        if current is None or current["status"] not in chat_tx.ACTIVE:
            # Abandoned, not finalized: no worker will drain queued follow-ups.
            chat_children.settle_abandoned_followups(row["id"])
            return
    except Exception:
        logger.exception("finish_run failed for %s; forcing terminal status", row["id"])
    _force_terminal(row["id"], status, message)


def _force_terminal(run_id, status, message):
    from . import chat_children
    from . import chats

    if status not in chat_tx.TERMINAL:
        status = "interrupted"
    with chat_tx.transaction() as conn:
        row = conn.execute("SELECT * FROM chat_runs WHERE id=?", (run_id,)).fetchone()
        if row is None or row["status"] not in chat_tx.ACTIVE:
            return
        payload = message if isinstance(message, dict) else {}
        try:
            stored = chat_views._message(payload, row["message_id"])
        except Exception:
            stored = {
                "id": row["message_id"],
                "role": "assistant",
                "content": chats._legacy_text(payload.get("content"), chats.ASSISTANT_CONTENT_MAX),
                "reasoning": chats._legacy_text(payload.get("reasoning"), chats.ASSISTANT_CONTENT_MAX),
                "error": chats._legacy_text(payload.get("error"), chats.ERROR_MAX),
                "stopped": True,
            }
        stored["stopped"] = True
        child = bool(row["parent_run_id"] if "parent_run_id" in row.keys() else None)
        try:
            request = json.loads(row["request"])
        except (json.JSONDecodeError, TypeError, ValueError):
            request = {}
        if not child and isinstance(request, dict):
            request.pop("messages", None)
        try:
            req_raw = chat_tx.dumps(request) if isinstance(request, dict) else row["request"]
        except Exception:
            req_raw = row["request"]
        try:
            msg_raw = chat_tx.dumps(stored)
        except Exception:
            stored = {
                "id": row["message_id"],
                "role": "assistant",
                "content": "",
                "reasoning": "",
                "error": "",
                "stopped": True,
            }
            msg_raw = chat_tx.dumps(stored)
        conn.execute(
            "UPDATE chat_runs SET message=?,request=?,status=?,updated_at=? WHERE id=?",
            (msg_raw, req_raw, status, db.now(), run_id),
        )
        try:
            from .schedules import sync_run_status

            sync_run_status(
                conn, row["user_id"], run_id, status,
                str(stored.get("error") or "") if isinstance(stored, dict) else "",
            )
        except Exception:
            logger.exception("Schedule fire status sync failed for %s", run_id)
        agent = request.get("agent") if isinstance(request, dict) and isinstance(request.get("agent"), dict) else {}
        if agent.get("coding"):
            try:
                from .vault import unpin_run

                unpin_run(row["user_id"], run_id)
            except Exception:
                logger.exception("Failed to unpin vault for run %s", run_id)
        if child:
            # Same abandonment contract as _finalize_abandoned: queued parent
            # follow-ups must be reported, not stranded in the request blob.
            try:
                chat_children._settle_child_followups(conn, run_id)
            except Exception:
                logger.exception("Failed to settle follow-ups for run %s", run_id)
            chat_tx.event(
                conn,
                row["user_id"],
                "agent." + status,
                row["thread_id"],
                {
                    "run_id": run_id,
                    "message_id": row["message_id"],
                    "message": stored,
                    "status": status,
                    "parent_run_id": row["parent_run_id"],
                    "root_run_id": row["root_run_id"] if "root_run_id" in row.keys() else None,
                    "summary": (stored.get("error") or stored.get("content") or "")[:2000],
                    "child_id": run_id,
                },
            )
            chat_tx.thread_event(conn, row["user_id"], row["thread_id"])
            return
        thread = chat_tx.lock_thread(conn, row["thread_id"], row["user_id"])
        revision = None
        if thread:
            try:
                messages = chat_schema.stored_messages(thread["messages"])
                if messages is None:
                    raise chats.ChatValidationError(chat_schema.UNREADABLE_TRANSCRIPT, 409)
                messages = [stored if msg["id"] == row["message_id"] else msg for msg in messages]
                revision = thread["revision"] + 1
                conn.execute(
                    "UPDATE chat_threads SET messages=?,revision=revision+1,updated_at=? WHERE id=?",
                    (chat_tx.dumps(messages), db.now(), row["thread_id"]),
                )
                from . import chat_history
                chat_history.finalize(conn, row, request, messages, stored)
            except Exception:
                logger.exception("Failed to merge aborted run %s into thread", run_id)
                revision = None
        chat_tx.event(
            conn,
            row["user_id"],
            "run." + status,
            row["thread_id"],
            {
                "run_id": run_id,
                "message_id": row["message_id"],
                "message": stored,
                "status": status,
                "revision": revision,
            },
        )
        if thread:
            chat_tx.thread_event(conn, row["user_id"], row["thread_id"])


def conn_child_status(user_id: int, child_id: str):
    row = db.fetchone("SELECT status FROM chat_runs WHERE user_id=? AND id=?", (user_id, child_id))
    return row["status"] if row else None


RESUME_NOTE_HEAD = "[Fuse] Service restart note"


USER_RESUME_NOTE_HEAD = "[Fuse] Continue note"


def _resume_note(run_id: str, resume_count: int, by_user: bool = False) -> str:
    if by_user:
        # The user stopped this turn and then chose Continue: no restart
        # happened, so do not tell the model one did.
        return (
            f"{USER_RESUME_NOTE_HEAD} resume-note:{run_id}:{resume_count} — the user stopped this turn and has "
            "now asked you to continue it. The transcript above is your own prior work; tool results that "
            "begin \"Tool interrupted\" have an UNKNOWN outcome, so verify their effect "
            "before repeating them. Any text you were writing when the turn stopped is not shown above. "
            "Do not repeat completed steps; finish the current turn."
        )
    return (
        f"{RESUME_NOTE_HEAD} resume-note:{run_id}:{resume_count} — the service restarted mid-turn. "
        "Continue exactly where you left off: the transcript above is your own prior work, tool results "
        "that begin \"Tool interrupted\" stopped with an UNKNOWN outcome — the step may or "
        "may not have completed before the interruption, so verify its effect before repeating it — and "
        "any new user messages arrive after this one. The restart cleared which files you have read, so "
        "read a file again before you edit it. Do not repeat completed steps; finish the current turn."
    )


def _with_resume_note(messages: list[dict], run_id: str, resume_count: int, by_user: bool = False) -> list[dict]:
    """Append the continue-where-you-left-off chat message, idempotently.

    Two guards keep consecutive restarts from stacking notes:
    - per-generation sentinel `resume-note:{run}:{n}` must not already exist;
    - the transcript's final message must not itself be an unread resume note
      or steer (no model progress since the last injection → nothing to add).
    """
    from . import chat_children
    note = _resume_note(run_id, resume_count, by_user)
    sentinel = f"resume-note:{run_id}:{resume_count}"
    for msg in messages:
        if isinstance(msg, dict) and msg.get("role") == "user":
            content = str(msg.get("content") or "")
            if sentinel in content:
                return messages
    for msg in reversed(messages):
        if not isinstance(msg, dict):
            continue
        content = str(msg.get("content") or "").strip()
        if not content and not msg.get("tool_calls"):
            continue
        if msg.get("role") == "user" and (content.startswith((RESUME_NOTE_HEAD, USER_RESUME_NOTE_HEAD, chat_children.STEER_FRAME_HEAD))):
            return messages
        break
    return [*messages, {"role": "user", "content": note}]


def _requeue_for_resume(row, messages, allowed=None, *, by_user=False):
    from . import chats

    allowed = allowed or chat_tx.ACTIVE
    resume_count = int(row["resume_count"] if "resume_count" in row.keys() else 0) + 1
    if by_user:
        sealed = seal_provider_messages(messages, notice=USER_STOP_TOOL_NOTICE)
    else:
        sealed = seal_provider_messages(messages)
    sealed = _with_resume_note(sealed, row["id"], resume_count, by_user)
    req = json.loads(row["request"])
    req["messages"] = sealed
    if row["parent_run_id"]:
        req.setdefault("display_seed", json.loads(row["request"]).get("messages") or [])
    req["run_id"] = row["id"]
    agent = req.get("agent") if isinstance(req.get("agent"), dict) else {}
    agent["skip_sanitize"] = True
    agent["resumed"] = True
    agent["resume_reason"] = "user" if by_user else "restart"
    # Recovery is a fresh leg: the worker gets the full runtime budget again
    # no matter how long ago the previous leg ran.
    agent["remaining_seconds"] = DEFAULT_REMAINING_SECONDS
    if row["parent_run_id"] if "parent_run_id" in row.keys() else None:
        # The parent can no longer await a restarted child synchronously, so
        # the result must reach it through the background paths: mid-run
        # injection (take_child_results) or a wake turn. A new execution owes
        # a new report: reset delivery state from any prior life of this run.
        agent["background"] = True
        agent["wake_sent"] = False
        agent["wake_attempts"] = 0
        agent["delivery_generation"] = uuid4().hex
        if by_user:
            agent["user_resumed_at"] = db.now()
    agent.setdefault("allowed_urls", agent.get("allowed_urls") or [])
    raw_loaded = row["loaded_tools"] if "loaded_tools" in row.keys() else None
    if raw_loaded:
        try:
            loaded = json.loads(raw_loaded)
            if isinstance(loaded, list):
                agent["loaded_tools"] = [str(name)[:64] for name in loaded if isinstance(name, str)]
        except (json.JSONDecodeError, TypeError, ValueError):
            pass
    req["agent"] = agent
    message = json.loads(row["message"])
    # Live-only fields: the worker strips them before saving, but a Stop that
    # lands at the final commit stamps status on the stored message
    # (finish_run). Left in, the strict parse below refuses the message and
    # the resume silently leaves the run stopped.
    for key in ("status", "startedAt"):
        message.pop(key, None)
    # UI checkpoints are turn-local and own display-part offsets. Provider
    # snapshots include earlier turns and must never replace this text.
    message.setdefault("content", "")
    agent["usage_before"] = message.get("usage") or {}
    message.setdefault("reasoning", "")
    segments = message.get("segments")
    segment_content = "".join(
        part.get("text", "") for part in segments or []
        if isinstance(part, dict) and part.get("type") == "content"
    )
    if isinstance(segments, list) and segment_content.startswith(message["content"]):
        message["segments"] = chats.align_segments(segments, message["content"], message["reasoning"])
    elif not isinstance(segments, list) or segment_content != message["content"]:
        message["segments"] = [
            *([{"type": "reasoning", "text": message["reasoning"]}] if message["reasoning"] else []),
            *(
                {"type": "tool", "id": tool["id"]}
                for tool in message.get("tools") or []
                if isinstance(tool, dict) and tool.get("id")
            ),
            *([{"type": "content", "text": message["content"]}] if message["content"] else []),
        ]
    message["stopped"] = False
    message.pop("error", None)
    message["segments"].append({
        "type": "marker", "kind": "resume",
        "text": USER_RESUME_NOTICE if by_user else RESUME_NOTICE,
    })
    for tool in message.get("tools") or []:
        if isinstance(tool, dict) and tool.get("status") in ("running", "progress"):
            tool["status"] = "interrupted"
    try:
        message = chats.parse_messages(
            [{**message, "id": row["message_id"], "role": "assistant"}],
            strict=True,
        )[0]
    except Exception:
        _interrupt_abandoned(row)
        return
    ts = db.now()
    with chat_tx.transaction() as conn:
        current = conn.execute("SELECT status,request,message,cancel_requested FROM chat_runs WHERE id=?", (row["id"],)).fetchone()
        if current is None or current["status"] not in allowed:
            return
        if any(current[key] != row[key] for key in ("request", "message", "cancel_requested")):
            # A concurrent acceptance/checkpoint/Stop wins over stale recovery.
            return
        from . import chat_history
        sealed = chat_history.upgrade_legacy_run(conn, row, req, sealed)
        req["messages"] = sealed
        req = chat_images.store_request(conn, row["thread_id"], row["user_id"], req)
        sealed = req["messages"]
        conn.execute(
            """UPDATE chat_runs SET status='queued', request=?, message=?, provider_messages=?,
               cancel_requested=0, cancel_requested_at='', resume_count=?, updated_at=? WHERE id=?""",
            (chat_tx.dumps(req), chat_tx.dumps(message), chat_tx.dumps(sealed), resume_count, ts, row["id"]),
        )
        chat_tx.event(
            conn,
            row["user_id"],
            "agent.resumed" if row["parent_run_id"] else "run.resumed",
            row["thread_id"],
            {
                "run_id": row["id"],
                "message_id": row["message_id"],
                "resume_count": resume_count,
                "status": "queued",
                **({"child_id": row["id"], "parent_run_id": row["parent_run_id"]} if row["parent_run_id"] else {}),
            },
        )
        chat_tx.event(
            conn,
            row["user_id"],
            "agent.progress" if row["parent_run_id"] else "run.progress",
            row["thread_id"],
            {"run_id": row["id"], "message_id": row["message_id"], "message": message},
        )
        if row["parent_run_id"]:
            chat_tx.thread_event(conn, row["user_id"], row["thread_id"])


BOOT_RESUME_BATCH = 8


def _check_resume_admission(row, user_id, is_child):
    """A resume starts generation again: same caps as a new turn or spawn."""
    from . import chat_children
    from . import chats

    def count(sql, params=()):
        return int(db.fetchone(sql, params)["n"])

    live = "status IN ('queued','running')"
    if not is_child:
        if count(f"SELECT COUNT(*) AS n FROM chat_runs WHERE user_id=? AND {live} AND parent_run_id IS NULL", (user_id,)) >= 20:
            raise chats.ChatValidationError("maximum of 20 active runs reached", 409)
        if count(f"SELECT COUNT(*) AS n FROM chat_runs WHERE {live} AND parent_run_id IS NULL") >= 32:
            raise chats.ChatValidationError("chat generation capacity reached; try again shortly", 429)
        return
    root = row["root_run_id"] or row["parent_run_id"]
    if count(f"SELECT COUNT(*) AS n FROM chat_runs WHERE root_run_id=? AND id<>? AND {live}", (root, root)) >= chat_children.LIVE_TREE_CHILDREN:
        raise chats.ChatValidationError(f"tree already has {chat_children.LIVE_TREE_CHILDREN} live children", 409)
    if count(f"SELECT COUNT(*) AS n FROM chat_runs WHERE user_id=? AND {live}", (user_id,)) >= chat_children.LIVE_USER_RUNS:
        raise chats.ChatValidationError(f"maximum of {chat_children.LIVE_USER_RUNS} live agents reached", 409)
    if count(f"SELECT COUNT(*) AS n FROM chat_runs WHERE {live}") >= chat_children.LIVE_PROCESS_RUNS:
        raise chats.ChatValidationError("agent generation capacity reached; try again shortly", 429)


def resume_run(user_id, run_id):
    """User-initiated resume of a terminal run from its durable transcript."""
    from . import chat_children
    from . import chats

    row = db.fetchone("SELECT * FROM chat_runs WHERE id=? AND user_id=?", (run_id, user_id))
    if row is None:
        raise chats.ChatValidationError("run not found", 404)
    if row["status"] not in ("interrupted", "cancelled"):
        raise chats.ChatValidationError("only interrupted or stopped runs can be resumed", 409)
    resume_count = int(row["resume_count"] if "resume_count" in row.keys() else 0)
    if resume_count >= MAX_RESUME_ATTEMPTS:
        raise chats.ChatValidationError("this run already used its resume budget; start a new turn instead", 409)
    raw_pm = row["provider_messages"]
    messages = None
    if raw_pm:
        try:
            parsed = json.loads(raw_pm)
        except (json.JSONDecodeError, TypeError, ValueError):
            parsed = None
        if isinstance(parsed, list) and parsed:
            messages = parsed
    if not messages:
        raise chats.ChatValidationError("run has no saved transcript to resume from", 409)
    is_child = bool(row["parent_run_id"]) if "parent_run_id" in row.keys() else False
    if not is_child:
        thread = db.fetchone("SELECT messages FROM chat_threads WHERE id=? AND user_id=?", (row["thread_id"], user_id))
        durable = chats.parse_messages(thread["messages"]) if thread else []
        if not durable or durable[-1].get("id") != row["message_id"]:
            raise chats.ChatValidationError("this run is no longer the latest turn; start a new turn instead", 409)
        from . import chat_history
        original = json.loads(row["request"])
        saved_message = chat_views.display_messages(original, json.loads(row["message"]), row["message_id"])[row["message_id"]]
        if chat_history.anchors([durable[-1]]) != chat_history.anchors([saved_message]):
            raise chats.ChatValidationError("this reply was edited after the run; start a new turn instead", 409)
        anchor = original.get("history_start_anchor") or []
        if anchor and chat_history.anchors(durable[:len(anchor)]) != anchor:
            raise chats.ChatValidationError("history was edited after this run; start a new turn instead", 409)
        active_root = db.fetchone(
            "SELECT 1 FROM chat_runs WHERE thread_id=? AND status IN ('queued','running') AND parent_run_id IS NULL",
            (row["thread_id"],),
        )
        if active_root:
            raise chats.ChatValidationError("this chat already has an active run", 409)
    # Check and requeue under the same lock new turns/spawns admit under, so
    # concurrent resumes cannot all pass the cap before any is counted.
    with (chat_children._child_admission_lock if is_child else _root_admission_lock):
        _check_resume_admission(row, user_id, is_child)
        _requeue_for_resume(row, messages, allowed=set(chat_tx.ACTIVE) | {"interrupted", "cancelled"}, by_user=True)
    if not is_child:
        with chat_tx.transaction() as conn:
            thread = conn.execute("SELECT * FROM chat_threads WHERE id=? AND user_id=?", (row["thread_id"], user_id)).fetchone()
            if thread is not None and int(thread["archived"] or 0):
                chats.unarchive_on_activity(conn, thread, db.now())
                conn.execute("UPDATE chat_threads SET revision=revision+1 WHERE id=?", (thread["id"],))
                chat_tx.thread_event(conn, user_id, thread["id"])
    updated = chat_views.get_run(user_id, run_id)
    if is_child and updated is not None and updated["status"] in chat_tx.ACTIVE:
        # Tell a live parent the child is back; its real report still arrives
        # through the normal finish/wake path. The synthetic id keeps this
        # notice from clobbering (or being acknowledged as) the final result.
        try:
            parent_id = str(updated.get("parent_run_id") or "")
            if parent_id:
                agent = (updated.get("request") or {}).get("agent") if isinstance(updated.get("request"), dict) else {}
                # The generation makes each resume's notice distinct: receipts
                # match on the envelope text, so a second resume of the same
                # child would otherwise read as already delivered.
                generation = str((agent or {}).get("delivery_generation") or "")
                envelope = chat_views.render_task_output(
                    child_id=run_id,
                    state="running",
                    text=f"This subagent was resumed by the user (resume {int(updated.get('resume_count') or 0)}). "
                    "A fresh report will arrive when it finishes.",
                    description=str((agent or {}).get("description") or ""),
                )
                chat_children.queue_child_result(parent_id, envelope, child_id=f"resume-{run_id}-{generation[:8]}")
        except Exception:
            logger.exception("Failed to notify parent of child resume %s", run_id)
    return updated


def recover_one(row, allow_resume=True):
    """Recover a single active run. Never raises to the caller."""
    try:
        if row["cancel_requested"]:
            abort_active(row["id"], "cancelled")
            return
        child = bool(row["parent_run_id"] if "parent_run_id" in row.keys() else None)
        if row["status"] == "queued" and not row["provider_messages"]:
            return
        raw_pm = row["provider_messages"]
        messages = None
        if raw_pm:
            try:
                parsed = json.loads(raw_pm)
            except (json.JSONDecodeError, TypeError, ValueError):
                parsed = None
            if isinstance(parsed, list) and parsed:
                messages = parsed
        if messages:
            # Children with a durable transcript resume exactly like root
            # runs; parity with the main agent's restart behavior.
            resume_count = int(row["resume_count"] if "resume_count" in row.keys() else 0)
            if resume_count >= MAX_RESUME_ATTEMPTS:
                # Crash/restart loop guard: bound how many times a single run
                # is re-driven automatically, then settle it as interrupted.
                _interrupt_abandoned(row)
                return
            if not allow_resume:
                # Boot batch cap: leave ACTIVE; the 60s reaper requeues the
                # overflow gradually once generation capacity frees up.
                return
            _requeue_for_resume(row, messages)
            return
        _interrupt_abandoned(row)
        if child:
            try:
                agent = (json.loads(row["request"]) or {}).get("agent")
            except (json.JSONDecodeError, TypeError):
                agent = None
            if isinstance(agent, dict) and agent.get("background") and not agent.get("wake_sent"):
                try:
                    from . import chat_runs

                    chat_runs.schedule_wake(row["user_id"], row["thread_id"])
                except Exception:
                    logger.exception("Failed to schedule wake for interrupted child %s", row["id"])
    except Exception:
        logger.exception("Failed to recover chat run %s", row["id"] if row else "?")
        try:
            abort_active(row["id"], "interrupted")
        except Exception:
            logger.exception("Failed to abort chat run %s after recover error", row["id"] if row else "?")


def recover_runs():
    """Re-queue runs (root and child) that have a durable tool transcript; interrupt the rest.

    Never re-executes tools. An in-flight LLM stream with no saved tool round is
    interrupted so a provider call with an uncertain outcome is not repeated.
    Queued runs that never started stay queued for resume_queued(). Only the
    BOOT_RESUME_BATCH oldest transcript runs requeue in the first pass so a
    restart storm cannot stampede generation; the reaper drains the rest.
    """
    seen = set()
    while True:
        # Ids first: no index serves this filter, and selecting every column
        # made InnoDB read each run's transcript blobs (gigabytes, minutes on
        # a cold start) only to discard the inactive ones.
        ids = [row["id"] for row in db.fetchall("SELECT id FROM chat_runs WHERE status IN ('queued','running') LIMIT 256")]
        fresh = [ident for ident in ids if ident not in seen]
        if not fresh:
            break
        seen.update(fresh)
        batch = [row for row in (db.fetchone("SELECT * FROM chat_runs WHERE id=?", (ident,)) for ident in fresh)
                 if row is not None and row["status"] in ("queued", "running")]
        resumable = [
            row for row in batch
            if row["provider_messages"]
            and not row["cancel_requested"]
            and int(row["resume_count"] if "resume_count" in row.keys() else 0) < MAX_RESUME_ATTEMPTS
        ]
        resumable_ids = {row["id"] for row in sorted(resumable, key=lambda r: str(r["created_at"]))[:BOOT_RESUME_BATCH]}
        for row in batch:
            recover_one(row, allow_resume=row["id"] in resumable_ids)
    from . import permissions as permissions_mod

    permissions_mod.recover()
