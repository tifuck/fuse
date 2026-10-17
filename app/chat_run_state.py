"""Run lifecycle: turns, cancellation, wake delivery, work time. Recovery: chat_recovery."""
from __future__ import annotations

import logging
import copy
import threading
import hashlib
import json
import time
from uuid import uuid4

from . import db, chat_schema, chat_tx, chat_views, chat_checkpoint, chat_scratch, run_inbox

logger = logging.getLogger(__name__)

# 8-hour safety cap. Grok CLI / OpenCode have no session wall clock; this
# only exists so a wedged worker cannot run forever. Idle stalls are a
# separate 600s provider-stream timeout in chat_runtime. Tool execution is
# included in this cap, so it is long enough for compiles and SSH jobs.
DEFAULT_REMAINING_SECONDS = 8 * 60 * 60
# Live worker with no checkpoint/heartbeat for this long is stalled, even
# if the 8h wall clock has not elapsed. Longer than tool/provider beats
# (2s/5s) and permission asks (60s).
STALL_IDLE_SECONDS = 180
STALL_WAIT_SECONDS = 60


def _cancel_event_payload(item):
    payload = {
        "run_id": item["id"],
        "message_id": item["message_id"],
        "status": item["status"] if "status" in item.keys() else "running",
        "cancel_requested": True,
    }
    parent = item["parent_run_id"] if "parent_run_id" in item.keys() else None
    if parent:
        payload["child_id"] = item["id"]
        payload["parent_run_id"] = parent
    return payload


def resumed_after_stop(agent, stopped_at: str) -> bool:
    """The user resumed this child after the main agent was stopped: that is
    an explicit request to carry on, so its report may wake the thread."""
    resumed = str((agent or {}).get("user_resumed_at") or "")
    # Timestamps are whole seconds: a tie is a resume clicked right after the
    # Stop, which must not be rejected as older intent.
    return bool(resumed and stopped_at and resumed >= stopped_at)


# chat_runs.cancel_requested value for a subagent the user stopped on its own
# (agents pane Stop). 1 is every other cancellation: a root Stop cascade, the
# parent's kill_subagent, a finishing parent killing its foreground children.
USER_STOPPED = 2


def user_stopped(status, cancel_requested) -> bool:
    """A subagent the user stopped by itself. Its report never starts an
    automated turn; it rides with the user's next message instead."""
    try:
        return status == "cancelled" and int(cancel_requested or 0) == USER_STOPPED
    except (TypeError, ValueError):
        return False


def _agent_wake_state(agent, status, stopped_at: str = "", cancel_requested=0):
    if not isinstance(agent, dict) or not agent.get("background") or status in chat_tx.ACTIVE:
        return ""
    if agent.get("wake_sent"):
        return "delivered"
    if user_stopped(status, cancel_requested):
        return "held"
    if stopped_at and not resumed_after_stop(agent, stopped_at):
        # The main agent was stopped: no automated turn will run; the report
        # rides with the user's next message.
        return "held"
    if int(agent.get("wake_attempts") or 0) >= 2:
        return "dropped"
    return "retrying"


def steer_turn(user_id, thread_id, text, command_id, attachments=None):
    """Atomically accept, display, and stage an idempotent mid-run user message."""
    from . import chats

    text = chats._text(text if text is not None else "", "text", chats.CONTENT_MAX)
    attachments = chats.parse_attachments(attachments, strict=True)
    if not text.strip() and not attachments:
        raise chats.ChatValidationError("text cannot be empty")
    with chat_tx.transaction() as conn:
        pk = chats.resolve_thread_pk(user_id, thread_id, conn)
        if pk is None:
            raise chats.ChatValidationError("chat not found", 404)
        identity = ["steer", pk, text]
        if attachments:
            identity.append([{k: a[k] for k in ("name", "mime", "kind", "data")} for a in attachments])
        fingerprint, previous = chat_tx.command(conn, user_id, command_id, identity)
        if previous is not None:
            return {"idempotent": True, "thread": chat_views.thread_view(conn, user_id, pk)}
        run = conn.execute("SELECT * FROM chat_runs WHERE thread_id=? AND user_id=? AND status IN ('queued','running') AND parent_run_id IS NULL",
                           (pk, user_id)).fetchone()
        if run is None or run["cancel_requested"]:
            raise chats.ChatValidationError("No active run; send a turn instead.", 409)
        row = chat_tx.lock_thread(conn, pk, user_id)
        if row is None:
            raise chats.ChatValidationError("chat not found", 404)
        request = json.loads(run["request"] or "{}")
        agent = request.get("agent") if isinstance(request.get("agent"), dict) else {}
        request["agent"] = agent
        # Acceptance can race the last provider response. finish_run atomically
        # requeues accepted work if this worker has already closed its window.
        agent["steer_closed"] = False
        pending = agent.setdefault("pending_steers", [])
        if len(pending) >= 8:
            raise chats.ChatValidationError("Too many pending messages; wait for delivery.", 429)
        messages = chat_schema.stored_messages(row["messages"])
        if messages is None:
            raise chats.ChatValidationError(chat_schema.UNREADABLE_TRANSCRIPT, 409)
        if len(messages) + 2 > chats.MSG_MAX:
            raise chats.ChatValidationError("chat message limit reached", 413)
        full = chat_views._message(json.loads(run["message"] or "{}"), run["message_id"])
        current = copy.deepcopy(chat_views.display_messages(request, full, run["message_id"])[run["message_id"]])
        frozen_id = str(uuid4())
        current.update(id=frozen_id, status="done", stopped=False, error="", usage=None)
        parts = agent.setdefault("display_parts", [])
        has_prefix = bool(current.get("content") or current.get("reasoning") or current.get("tools") or current.get("permissions"))
        user_message = chats.parse_messages([{"role": "user", "content": text, "attachments": attachments}], strict=True)[0]
        parts.append({"id": frozen_id, "end": chat_views._display_mark(full), "elapsedMs": full.get("elapsedMs"), "hidden": not has_prefix,
                      "steer_id": command_id, "user_message_id": user_message["id"], "awaiting_delivery": True})
        continuation = chat_views.display_messages(request, full, run["message_id"])[run["message_id"]]
        replacement = ([current] if has_prefix else []) + [user_message, continuation]
        if not any(msg.get("id") == run["message_id"] for msg in messages):
            raise chats.ChatValidationError("Active message is missing; reload the conversation.", 409)
        messages = [part for msg in messages for part in (replacement if msg.get("id") == run["message_id"] else [msg])]
        pending.append({"id": command_id, "text": text, **({"attachments": attachments} if attachments else {})})
        conn.execute("UPDATE chat_runs SET request=?,updated_at=? WHERE id=?", (chat_tx.dumps(request), db.now(), run["id"]))
        conn.execute(
            "UPDATE chat_threads SET messages=?,revision=revision+1,updated_at=? WHERE id=?",
            (chat_tx.dumps(messages), db.now(), pk),
        )
        chats.unarchive_on_activity(conn, row, db.now())
        chat_tx.remember(conn, user_id, command_id, fingerprint, {"thread_id": pk, "message_id": user_message.get("id")})
        chat_tx.thread_event(conn, user_id, pk, transcript_changed=True)
        result = {"idempotent": False, "thread": chat_views.thread_view(conn, user_id, pk)}
    # Committed: a wait the run is blocked in returns so the steer lands now.
    run_inbox.arrived(run["id"], f"steer:{command_id}")
    return result


def touch_run(run_id: str) -> None:
    """Liveness touch: refresh updated_at without a full checkpoint so the
    stall reaper leaves a live-but-quiet worker (e.g. mid-compaction) alone."""
    if not run_id:
        return
    try:
        with chat_tx.transaction() as conn:
            conn.execute(
                "UPDATE chat_runs SET updated_at=? WHERE id=? AND status IN ('queued','running')",
                (db.now(), run_id),
            )
    except Exception:
        pass


# Root-run admission: the active-run counts are plain reads, so two turns or
# resumes checked concurrently could both pass the cap. Serialize the
# check-and-insert like child spawns (_child_admission_lock); one worker.
_root_admission_lock = threading.Lock()


def start_turn(*args, **kwargs):
    pinned: list[tuple[int, str]] = []
    with _root_admission_lock:
        try:
            return _start_turn(*args, _pinned=pinned, **kwargs)
        except BaseException:
            # Covers the COMMIT too, which runs after _start_turn's body: a pin
            # for a run that never committed would keep the vault unlocked.
            from .vault import unpin_run

            for user_id, run_id in pinned:
                try:
                    unpin_run(user_id, run_id)
                except Exception:
                    logger.exception("Failed to unpin vault after failed turn commit for %s", run_id)
            raise


def _start_turn(user_id, thread_id, text, expected_revision, command_id, model=None, tools=None, context=None,
                attachments=None, reasoning_effort=None, auto_title=False, invoke_skill=None, auto=False,
                scheduled=False, coding_override=None, command_receipt_ids=None, replace_from=None,
                schedule_id=None, schedule_manual=False, auto_resumed_at="", wake=None, _pinned=None):
    from . import chats

    text = chats._text(text if text is not None else "", "text", chats.AUTO_CONTENT_MAX if auto else chats.CONTENT_MAX)
    attachments = chats.parse_attachments(attachments, strict=True)
    if not text.strip() and not attachments:
        raise chats.ChatValidationError("text cannot be empty")
    if model is not None:
        model = chats._text(model, "model", 200)
    if tools is not None:
        tools = chats.parse_thread_tools(tools, strict=True)
    if context is not None:
        context = chats.clip_context(context, strict=True)
    reasoning_effort = chats.parse_reasoning_effort(reasoning_effort, strict=True)
    if replace_from is not None:
        if type(replace_from) is not int or replace_from < 0:
            raise chats.ChatValidationError("replace_from must be a non-negative integer")
        if expected_revision is None:
            raise chats.ChatValidationError("expected_revision is required for replacement")
    with chat_tx.transaction() as conn:
        pk = chats.resolve_thread_pk(user_id, thread_id, conn)
        attachment_identity = [{k: att[k] for k in ("name", "mime", "kind", "data")} for att in attachments]
        legacy = ["turn", thread_id, text, expected_revision, model, tools, context, attachment_identity, reasoning_effort, invoke_skill]
        identity = ["turn_v2", pk if pk is not None else thread_id, text, model, tools, context, attachment_identity, reasoning_effort, invoke_skill]
        if replace_from is not None:
            identity.extend(["replace_from", replace_from])
        old = conn.execute("SELECT fingerprint FROM chat_commands WHERE user_id=? AND command_id=?", (user_id, command_id)).fetchone()
        # Original retry payloads from before this change remain valid. New
        # receipts identify intent, not the revision refreshed after a lost ACK.
        if replace_from is None and old and old["fingerprint"] == hashlib.sha256(chat_tx.dumps(legacy).encode()).hexdigest():
            identity = legacy
        fingerprint, previous = chat_tx.command(conn, user_id, command_id, identity)
        if previous is not None:
            run = chat_views.run_view(conn.execute("SELECT * FROM chat_runs WHERE id=? AND user_id=?", (previous["run_id"], user_id)).fetchone())
            view_id = pk if pk is not None else previous.get("thread_id")
            return {"thread": chat_views.thread_view(conn, user_id, view_id) if view_id is not None else None, "run": run, "idempotent": True}
        if pk is None:
            raise chats.ChatValidationError("chat not found", 404)
        thread_id = pk
        row = conn.execute("SELECT * FROM chat_threads WHERE id=? AND user_id=?", (thread_id, user_id)).fetchone()
        if row is None:
            raise chats.ChatValidationError("chat not found", 404)
        chat_tx.check_revision(row["revision"], expected_revision)
        if scheduled and schedule_id is not None:
            schedule = conn.execute("SELECT enabled,paused FROM chat_schedules WHERE id=? AND user_id=?", (schedule_id, user_id)).fetchone()
            if schedule is None or (not schedule_manual and (not schedule["enabled"] or schedule["paused"])):
                raise chats.ChatValidationError("schedule stopped or unavailable", 409)
        elif auto and automatic_stop_guard(conn, user_id, thread_id, resumed_at=auto_resumed_at):
            raise chats.ChatValidationError("conversation stopped; explicit resume required", 409)
        if replace_from is not None and conn.execute(
            "SELECT 1 FROM chat_runs WHERE thread_id=? AND status IN ('queued','running')", (thread_id,)
        ).fetchone():
            raise chats.ChatValidationError("cannot replace messages during an active run", 409)
        if conn.execute(
            """SELECT 1 FROM chat_runs WHERE thread_id=? AND status IN ('queued','running')
               AND parent_run_id IS NULL""",
            (thread_id,),
        ).fetchone():
            raise chats.ChatValidationError("chat already has an active run", 409)
        if conn.execute(
            """SELECT COUNT(*) FROM chat_runs WHERE user_id=? AND status IN ('queued','running')
               AND parent_run_id IS NULL""",
            (user_id,),
        ).fetchone()[0] >= 4:
            raise chats.ChatValidationError("maximum of 4 active runs reached", 409)
        if conn.execute(
            """SELECT COUNT(*) FROM chat_runs WHERE status IN ('queued','running') AND parent_run_id IS NULL"""
        ).fetchone()[0] >= 32:
            raise chats.ChatValidationError("chat generation capacity reached; try again shortly", 429)
        # The read above may predate a run that finalized while this waited
        # on its lock; build the new transcript from the current row.
        row = chat_tx.lock_thread(conn, thread_id, user_id)
        if row is None:
            raise chats.ChatValidationError("chat not found", 404)
        chat_tx.check_revision(row["revision"], expected_revision)
        messages = chat_schema.stored_messages(row["messages"])
        if messages is None:
            raise chats.ChatValidationError(chat_schema.UNREADABLE_TRANSCRIPT, 409)
        original_messages = messages
        if replace_from is not None:
            if replace_from >= len(messages) or messages[replace_from].get("role") != "user":
                raise chats.ChatValidationError("replace_from must target an existing user message")
            messages = messages[:replace_from]
        if len(messages) + 2 > chats.MSG_MAX:
            raise chats.ChatValidationError("chat message limit reached", 413)
        incoming = {"role": "user", "content": text}
        if auto:
            incoming["auto"] = True
        if attachments:
            incoming["attachments"] = attachments
        turn_model = (model if model is not None else row["model"]) or ""
        user_message, assistant = chats.parse_messages([
            incoming, {"role": "assistant", "content": "", "segments": [], "model": turn_model}], strict=True)
        messages.extend([user_message, assistant])
        if not auto:
            conn.execute("UPDATE chat_threads SET recap_start_id=? WHERE id=?", (user_message["id"], thread_id))
        account = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        from . import context_compact as ctxmod, context_meter
        from . import hosts as hosts_mod

        settings_row = hosts_mod.settings_for(user_id, conn)
        is_project = (row["kind"] if "kind" in row.keys() else "") == "project"
        if scheduled:
            coding = bool(coding_override)
        else:
            coding = bool(settings_row.get("agent_coding")) or is_project

        state = ctxmod.parse_state(row["compaction"] if "compaction" in row.keys() else None)
        if replace_from is not None:
            state = ctxmod.invalidate_edited_checkpoints(state, original_messages, original_messages[:replace_from])
        send_model = model if model is not None else row["model"]
        # A wake turn carries on the turn that started the background work,
        # so it runs at that turn's reasoning effort. Without one it fell back
        # to the provider default (Claude: low), and Anthropic keeps a separate
        # message cache per effort: every switch between a wake and the user's
        # next turn re-sent the whole conversation uncached.
        if auto and not scheduled:
            last = state.get("effort") or {}
            if reasoning_effort is None and last.get("model") == send_model:
                reasoning_effort = last.get("value") or None
        else:
            state["effort"] = {"model": send_model or "", "value": reasoning_effort or ""}
        prior_meter = state.get("meter") if isinstance(state.get("meter"), dict) else None
        send_type_id = context_meter.source_type_for_model(send_model, prior_meter)
        from . import chat_history
        history_cutoffs = []
        request_messages, history_info = chat_history.build(conn, user_id, thread_id, messages, state, cutoffs_out=history_cutoffs)
        prompt_snapshot = history_info.pop("prompt_snapshot", None)
        state["meter"] = context_meter.standing_meter(chat_history.for_model(request_messages, send_model), model=send_model, type_id=send_type_id,
            override=state.get("window_override") or ((prior_meter or {}).get("limit") if send_type_id is None and (prior_meter or {}).get("model") == send_model else None),
            prior=prior_meter if (prior_meter or {}).get("model") == send_model else None)

        request = {
            "model": model if model is not None else row["model"],
            "messages": request_messages,
            "tools": tools if tools is not None else chats.parse_thread_tools(row["tools"]),
            "thread_id": thread_id,
            "thread_context": context if context is not None else row["context"],
            "instructions": chats.effective_instructions(account["chat_instructions"]),
            "profile": account["chat_profile"] or "",
            "history_replay": True,
            "history_info": history_info,
            "history_input_hashes": chat_history.input_hashes(request_messages),
            "history_start_anchor": chat_history.anchors(messages[:-1]),
            "history_cutoffs": history_cutoffs,
        }
        if prompt_snapshot:
            request["prompt_snapshot"] = prompt_snapshot
        if reasoning_effort is not None:
            request["reasoning_effort"] = reasoning_effort
        project_name = ""
        if (row["kind"] if "kind" in row.keys() else "") == "project":
            pid = row["project_id"] if "project_id" in row.keys() else None
            if pid is not None:
                prow = conn.execute(
                    "SELECT name FROM user_projects WHERE id=? AND user_id=?",
                    (pid, user_id),
                ).fetchone()
                if prow:
                    project_name = prow["name"] or ""
        title = row["title"] if str(row["title"] or "").strip() else "New chat"
        # Chat page turns show a short preview of the first request until
        # chat_titles (started alongside this turn) names the chat.
        if auto_title and (row["title_state"] if "title_state" in row.keys() else "") == "" \
                and chats.is_placeholder_title(title, project_name=project_name):
            title = chats.provisional_title(messages)
        run_id, ts = str(uuid4()), db.now()
        request["run_id"] = run_id
        from .vault import VaultLocked, get_dek, pin_run, unpin_run

        if coding:
            try:
                prev_loaded = json.loads(row["loaded_tools"]) if "loaded_tools" in row.keys() and row["loaded_tools"] else []
            except (json.JSONDecodeError, TypeError, ValueError):
                prev_loaded = []
            if not isinstance(prev_loaded, list):
                prev_loaded = []
            from .agent_catalog import sanitize_loaded

            vault_ok = True
            try:
                get_dek(user_id)
            except VaultLocked:
                vault_ok = False
            bound_host_id = row["host_id"] if "host_id" in row.keys() else None
            bound_cwd = ""
            perm = settings_row.get("permission_mode") or "project"
            if row["project_id"] if "project_id" in row.keys() else None:
                proj = conn.execute(
                    "SELECT remote_path, permission_mode FROM user_projects WHERE id=? AND user_id=?",
                    (row["project_id"], user_id),
                ).fetchone()
                if proj:
                    bound_cwd = proj["remote_path"] or ""
                    proj_mode = proj["permission_mode"] if "permission_mode" in proj.keys() else None
                    if proj_mode in ("project", "read_only"):
                        perm = proj_mode
            request["agent"] = {
                "coding": True,
                "project_session": is_project,
                "depth": 0,
                "parent_run_id": None,
                "root_run_id": run_id,
                "run_id": run_id,
                "bound_host_id": bound_host_id,
                "bound_cwd": bound_cwd,
                "permission_mode": perm,
                "allow_nested_spawn": bool(settings_row.get("allow_nested_spawn")),
                "loaded_tools": sanitize_loaded(prev_loaded),
                "remaining_seconds": DEFAULT_REMAINING_SECONDS,
                "max_completions": 0,
                "spawns_used": 0,
                "vault_unlocked": vault_ok,
            }
            carried = chat_scratch._carried_scratch(conn, thread_id, messages)
            if carried:
                request["agent"]["scratch"] = carried
            if auto or scheduled:
                request["agent"]["auto"] = True
            if scheduled:
                request["agent"]["scheduled"] = True
            from .skill_parse import SkillError
            from . import skills as skills_mod

            try:
                parsed_invoke = skills_mod.parse_invoke(
                    user_id, invoke_skill, text, coding=True, conn=conn
                )
            except SkillError as exc:
                raise chats.ChatValidationError(exc.detail, exc.status_code) from exc
            if parsed_invoke:
                request["agent"]["invoke_skill"] = parsed_invoke
        elif scheduled:
            request["agent"] = {
                "coding": False,
                "project_session": is_project,
                "depth": 0,
                "parent_run_id": None,
                "root_run_id": run_id,
                "run_id": run_id,
                "bound_host_id": row["host_id"] if "host_id" in row.keys() else None,
                "bound_cwd": "",
                "permission_mode": settings_row.get("permission_mode") or "project",
                "allow_nested_spawn": False,
                "loaded_tools": [],
                "remaining_seconds": DEFAULT_REMAINING_SECONDS,
                "max_completions": 0,
                "spawns_used": 0,
                "vault_unlocked": True,
                "auto": True,
                "scheduled": True,
            }
        else:
            from .skill_parse import SkillError
            from . import skills as skills_mod

            try:
                parsed_invoke = skills_mod.parse_invoke(
                    user_id, invoke_skill, text, coding=False, conn=conn
                )
            except SkillError as exc:
                raise chats.ChatValidationError(exc.detail, exc.status_code) from exc
            if parsed_invoke:
                raise chats.ChatValidationError("skills require coding chat", 400)
        # Pin only once every raise-style validation has passed, so a rejected
        # turn can never leak a pin for a run id that was never committed.
        if coding and vault_ok:
            try:
                pin_run(user_id, run_id)
                if _pinned is not None:
                    _pinned.append((user_id, run_id))
            except VaultLocked:
                vault_ok = False
                request["agent"]["vault_unlocked"] = False
        if wake is not None:
            # Same transaction as the run: flagged separately, a failure in
            # between left the children wake-pending and delivered them twice.
            _flag_wake(request, *wake)
        conn.execute(
            """INSERT INTO chat_runs(id,thread_id,user_id,status,request,message_id,message,created_at,updated_at,
               parent_run_id, root_run_id, agent_depth)
               VALUES(?,?,?,'queued',?,?,?,?,?,NULL,?,0)""",
            (run_id, thread_id, user_id, chat_tx.dumps(request), assistant["id"], chat_tx.dumps(assistant), ts, ts, run_id),
        )
        conn.execute("""UPDATE chat_threads SET messages=?,title=?,model=?,tools=?,context=?,compaction=?,revision=revision+1,updated_at=? WHERE id=?""",
                     (chat_tx.dumps(messages), title, request["model"], chat_tx.dumps(request["tools"]), request["thread_context"], ctxmod.dump_state(state), ts, thread_id))
        chats.unarchive_on_activity(conn, row, ts)
        chat_tx.thread_event(conn, user_id, thread_id)
        chat_tx.event(conn, user_id, "run.started", thread_id, {"run_id": run_id, "message_id": assistant["id"]})
        from . import command_jobs as command_jobs_mod

        command_jobs_mod.mark_delivered([str(ident) for ident in command_receipt_ids or []], conn=conn)
        chat_tx.remember(conn, user_id, command_id, fingerprint, {"run_id": run_id, "thread_id": thread_id})
        thread = chat_views.thread_view(conn, user_id, thread_id)
        return {"thread": thread, "run": thread["run"], "idempotent": False}


def wake_candidates(user_id, thread_id):
    """Terminal background children of a thread that have not woken the parent yet."""
    # Flags first, blobs second: every terminal child's request/message can be
    # megabytes and only the wake-pending few need them.
    flags = db.fetchall(
        """SELECT child.id,
                  COALESCE(json_extract(child.request, '$.agent.background'), 0) AS background,
                  COALESCE(json_extract(child.request, '$.agent.wake_sent'), 0) AS wake_sent
           FROM chat_runs child
           WHERE child.user_id=? AND child.thread_id=? AND child.parent_run_id IS NOT NULL
              AND child.status IN ('completed','failed','cancelled','interrupted')
              AND NOT EXISTS (
                  SELECT 1 FROM chat_runs parent WHERE parent.id=child.parent_run_id
                  AND parent.parent_run_id IS NOT NULL AND parent.status IN ('queued','running')
              )
           ORDER BY child.created_at""",
        (user_id, thread_id),
    )
    wanted = [row["id"] for row in flags if _json_flag(row["background"]) and not _json_flag(row["wake_sent"])]
    if not wanted:
        return []
    rows = {}
    for offset in range(0, len(wanted), 400):
        batch = wanted[offset:offset + 400]
        rows.update((r["id"], r) for r in db.fetchall(
            f"SELECT id, status, request, message, cancel_requested FROM chat_runs WHERE id IN ({','.join('?' for _ in batch)})",
            batch,
        ))
    out = []
    for ident in wanted:
        row = rows.get(ident)
        if row is None:
            continue
        try:
            agent = (json.loads(row["request"]) or {}).get("agent")
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(agent, dict) or not agent.get("background") or agent.get("wake_sent"):
            continue
        try:
            message = json.loads(row["message"])
        except (json.JSONDecodeError, TypeError):
            message = {}
        out.append(
            {
                "id": row["id"],
                "status": row["status"],
                "user_stopped": user_stopped(row["status"], row["cancel_requested"]),
                "attempts": int(agent.get("wake_attempts") or 0),
                "generation": str(agent.get("delivery_generation") or ""),
                "user_resumed_at": str(agent.get("user_resumed_at") or ""),
                "description": str(agent.get("description") or "subagent"),
                "summary": chat_views.child_writeup(
                    {"id": row["id"], "status": row["status"], "message": message if isinstance(message, dict) else {}, "request": {"agent": agent}}
                ),
            }
        )
    return out


def queue_stranded_reports(user_id, thread_id, run_id: str, messages=None, limit: int = 8) -> list[str]:
    """Queue undelivered child reports onto a live root run; return their envelopes.

    Reports already present in messages (the turn's transcript, e.g. an
    automated wake turn's seed) are skipped so the model never gets one twice.
    """
    from . import chat_children
    candidates = wake_candidates(user_id, thread_id)[:limit]
    if not candidates:
        return []
    seen_text = "\n".join(
        m["content"] for m in (messages or [])
        if isinstance(m, dict) and isinstance(m.get("content"), str)
    )
    out = []
    for item in candidates:
        envelope = chat_views.render_task_output(
            child_id=item["id"],
            state=item["status"],
            text=(item["summary"] or item["status"]).strip() or "Subagent finished without a report.",
            description=item["description"],
            generation=item.get("generation", ""),
        )
        if envelope in seen_text:
            continue
        if chat_children.queue_child_result(str(run_id), envelope, child_id=item["id"]):
            out.append(envelope)
    return out


def mark_wake_sent(run_ids, pending_guard_run: str | None = None):
    """Acknowledge wake delivery for finished children.

    A child that has since been re-spawned (queued/running: a new leg in
    flight) keeps wake_sent=0 — that leg owes a fresh report and must still
    wake the thread when it finishes. With pending_guard_run, a child whose
    latest envelope is still queued unconsumed on that run's
    pending_child_results is also left unacknowledged so the next wake
    re-delivers it.
    """
    ids = [str(r) for r in (run_ids or []) if str(r)]
    if not ids:
        return
    with chat_tx.transaction() as conn:
        guarded: set[str] = set()
        receipts = None
        receipt_sources = []
        if pending_guard_run:
            grow = conn.execute("SELECT request,provider_messages FROM chat_runs WHERE id=?", (pending_guard_run,)).fetchone()
            if grow is not None:
                try:
                    greq = json.loads(grow["request"] or "{}")
                except (json.JSONDecodeError, TypeError):
                    greq = {}
                gagent = greq.get("agent") if isinstance(greq, dict) and isinstance(greq.get("agent"), dict) else {}
                receipts = gagent.get("wake_receipts")
                receipt_sources = [chat_tx.dumps(greq.get("messages") or []), str(grow["provider_messages"] or ""), chat_tx.dumps(gagent.get("wake_seed_receipts") or {})]
                guarded = {
                    str(item.get("id") or "")
                    for item in (gagent.get("pending_child_results") or [])
                    if isinstance(item, dict)
                }
        for run_id in ids:
            if run_id in guarded:
                continue
            row = conn.execute("SELECT user_id,thread_id,status,request FROM chat_runs WHERE id=?", (run_id,)).fetchone()
            if row is None or row["status"] not in chat_tx.TERMINAL:
                continue
            try:
                req = json.loads(row["request"])
            except (json.JSONDecodeError, TypeError):
                continue
            agent = req.get("agent") if isinstance(req, dict) and isinstance(req.get("agent"), dict) else {}
            generation = str(agent.get("delivery_generation") or "")
            if isinstance(receipts, dict):
                receipt = receipts.get(run_id) or ""
                if not receipt or (generation and f"<task_generation>{generation}</task_generation>" not in receipt):
                    continue
                if not any(chat_tx._json_needle(receipt) in source for source in receipt_sources):
                    continue
            elif pending_guard_run and generation:
                continue
            agent["wake_sent"] = True
            req["agent"] = agent
            conn.execute("UPDATE chat_runs SET request=? WHERE id=?", (chat_tx.dumps(req), run_id))
            chat_tx.event(conn, row["user_id"], "agent.delivered", row["thread_id"],
                          {"run_id": run_id, "child_id": run_id, "status": row["status"], "wake_state": "delivered"})


def bump_wake_attempts(run_ids):
    """Count a wake-turn attempt per child so a failed wake can retry once.

    Skips re-spawned children: a new leg in flight did not cause the wake
    failure and its own completion must keep full delivery budget.
    """
    ids = [str(r) for r in (run_ids or []) if str(r)]
    if not ids:
        return
    with chat_tx.transaction() as conn:
        for run_id in ids:
            row = conn.execute("SELECT status,request FROM chat_runs WHERE id=?", (run_id,)).fetchone()
            if row is None or row["status"] not in chat_tx.TERMINAL:
                continue
            try:
                req = json.loads(row["request"])
            except (json.JSONDecodeError, TypeError):
                continue
            agent = req.get("agent") if isinstance(req, dict) and isinstance(req.get("agent"), dict) else {}
            agent["wake_attempts"] = int(agent.get("wake_attempts") or 0) + 1
            req["agent"] = agent
            conn.execute("UPDATE chat_runs SET request=? WHERE id=?", (chat_tx.dumps(req), run_id))


def settle_wake_children(run_id, child_ids):
    """Settle delivery for children carried by a wake turn that did not complete.

    A child whose report envelope is already durable — in the wake turn's
    request seed, its provider transcript, or the thread messages — is
    acknowledged as delivered: the bubble was shown, and a user-stopped turn
    must not be retried into a loop. Only genuinely undelivered children
    spend wake retry budget. Re-spawned children (a new leg in flight) are
    left untouched: that leg owes a fresh report.
    """
    ids = [str(c) for c in (child_ids or []) if str(c)]
    if not ids:
        return
    with chat_tx.transaction() as conn:
        row = conn.execute(
            "SELECT user_id, thread_id, request, provider_messages FROM chat_runs WHERE id=?",
            (run_id,),
        ).fetchone()
        if row is None:
            return
        request = json.loads(row["request"] or "{}")
        receipts = (request.get("agent") or {}).get("wake_receipts") or {}
        # Pending queues and unrelated historical thread bubbles are not proof
        # that this wake delivered a report.
        sources = [chat_tx.dumps(request.get("messages") or []), str(row["provider_messages"] or ""), chat_tx.dumps((request.get("agent") or {}).get("wake_seed_receipts") or {})]
        for cid in ids:
            crow = conn.execute("SELECT status,request FROM chat_runs WHERE id=?", (cid,)).fetchone()
            if crow is None or crow["status"] not in chat_tx.TERMINAL:
                continue
            try:
                creq = json.loads(crow["request"])
            except (json.JSONDecodeError, TypeError):
                continue
            if not isinstance(creq, dict):
                continue
            cagent = creq.get("agent") if isinstance(creq.get("agent"), dict) else {}
            receipt = receipts.get(cid) or ""
            generation = str(cagent.get("delivery_generation") or "")
            if generation and f"<task_generation>{generation}</task_generation>" not in receipt:
                continue
            needle = chat_tx._json_needle(receipt) if receipt else ""
            if needle and any(needle in src for src in sources):
                cagent["wake_sent"] = True
            else:
                cagent["wake_attempts"] = int(cagent.get("wake_attempts") or 0) + 1
            creq["agent"] = cagent
            conn.execute("UPDATE chat_runs SET request=? WHERE id=?", (chat_tx.dumps(creq), cid))


def automatic_stop_guard(conn, user_id, thread_id, *, parent_run_id=None, resumed_at=""):
    """Durable Stop intent, checked inside the caller's admission transaction.

    A thread is stopped when its latest root run carries a user Stop (the
    cancel flag outlives the run: request_cancel writes it even on a root that
    already finished), or when a walked ancestor did. A user resume after the
    stop (user_resumed_at, or the requeued run's cleared flag) is explicit
    intent and lifts the guard. The chat_runs reads lock those rows on
    MariaDB, so this cannot interleave with a concurrent Stop on them.
    """
    roots = conn.execute(
        "SELECT id,message_id,status,cancel_requested,cancel_requested_at,updated_at,created_at FROM chat_runs WHERE thread_id=? AND user_id=? AND parent_run_id IS NULL",
        (thread_id, user_id),
    ).fetchall()
    if roots:
        thread = conn.execute("SELECT messages FROM chat_threads WHERE id=? AND user_id=?", (thread_id, user_id)).fetchone()
        try:
            messages = json.loads((thread or {"messages": None})["messages"] or "[]")
        except (json.JSONDecodeError, TypeError):
            messages = []
        positions = {m.get("id"): i for i, m in enumerate(messages) if isinstance(m, dict)}
        latest = max(roots, key=lambda r: (r["created_at"], positions.get(r["message_id"], -1), r["id"]))
        if _run_stopped(latest, resumed_at):
            return True
    seen = set()
    while parent_run_id and parent_run_id not in seen:
        seen.add(parent_run_id)
        parent = conn.execute("SELECT parent_run_id,status,cancel_requested,cancel_requested_at,updated_at FROM chat_runs WHERE id=? AND user_id=?", (parent_run_id, user_id)).fetchone()
        if parent is None:
            return True
        if _run_stopped(parent, resumed_at):
            return True
        parent_run_id = parent["parent_run_id"]
    return False


def _run_stopped(row, resumed_at: str) -> bool:
    if not (row["cancel_requested"] or row["status"] == "cancelled"):
        return False
    return not resumed_after_stop({"user_resumed_at": resumed_at}, row["cancel_requested_at"] or row["updated_at"])


def thread_root_run(user_id, thread_id, *, include_request=True):
    """Latest root run of a thread with its status."""
    try:
        columns = "id, status, cancel_requested, updated_at, cancel_requested_at, message_id"
        if include_request:
            columns += ", request"
        rows = db.fetchall(
            f"""SELECT {columns} FROM chat_runs
               WHERE user_id=? AND thread_id=? AND parent_run_id IS NULL
                 AND created_at = (SELECT MAX(created_at) FROM chat_runs
                                   WHERE user_id=? AND thread_id=? AND parent_run_id IS NULL)""",
            (user_id, thread_id, user_id, thread_id),
        )
    except Exception:
        return None
    if not rows:
        return None
    row = rows[0]
    if len(rows) > 1:
        # created_at has one-second precision and run ids are random, so two
        # roots started in the same second (a turn failing at once, then a
        # queue drain or wake) cannot be ordered by either. The transcript is
        # authoritative: the later root owns the later assistant message.
        # Reading the wrong one would let a stopped thread wake again.
        try:
            thread = db.fetchone("SELECT messages FROM chat_threads WHERE id=? AND user_id=?", (thread_id, user_id))
            messages = json.loads(thread["messages"]) if thread else []
        except (json.JSONDecodeError, TypeError):
            messages = []
        position = {str(m.get("id")): i for i, m in enumerate(messages if isinstance(messages, list) else []) if isinstance(m, dict)}
        row = max(rows, key=lambda r: (position.get(str(r["message_id"]), -1), str(r["id"])))
    try:
        req = json.loads(row["request"]) if include_request else {}
    except (json.JSONDecodeError, TypeError):
        req = {}
    agent = req.get("agent") if isinstance(req, dict) and isinstance(req.get("agent"), dict) else {}
    stopped_at = ""
    if row["status"] == "cancelled" or row["cancel_requested"]:
        stopped_at = str(row["cancel_requested_at"] or row["updated_at"] or "")
    return {"id": row["id"], "status": row["status"], "wake": bool(agent.get("wake")), "stopped_at": stopped_at}


def _flag_wake(req: dict, child_ids=None, receipts=None) -> None:
    agent = req.get("agent") if isinstance(req.get("agent"), dict) else {}
    agent["wake"] = True
    if receipts is not None:
        agent["wake_receipts"] = receipts
        seed = chat_tx.dumps(req.get("messages") or [])
        agent["wake_seed_receipts"] = {cid: text for cid, text in receipts.items() if text and chat_tx._json_needle(text) in seed}
    if child_ids:
        agent["wake_ids"] = [str(c) for c in child_ids if str(c)][:32]
    req["agent"] = agent


def mark_run_wake(run_id, child_ids=None, receipts=None):
    """Flag a run as an automated wake turn (subagent reports and/or command results)."""
    with chat_tx.transaction() as conn:
        row = conn.execute("SELECT request FROM chat_runs WHERE id=?", (run_id,)).fetchone()
        if row is None:
            return
        try:
            req = json.loads(row["request"])
        except (json.JSONDecodeError, TypeError):
            return
        if not isinstance(req, dict):
            return
        _flag_wake(req, child_ids, receipts)
        conn.execute("UPDATE chat_runs SET request=? WHERE id=?", (chat_tx.dumps(req), run_id))


# Work time is execution time only. message.elapsedMs is the current display
# turn's active time (a user/boot resume keeps the message, so it keeps
# accumulating); agent.work_ms_before carries earlier turns of the same run
# that a task_id continuation or follow-up replaced with a fresh message.
# Idle gaps between legs (stopped, interrupted, waiting for a resume) are
# never counted. Live legs register here so readers can add the unflushed
# tail; the server is a single process, so this is authoritative.
_live_legs: dict[str, tuple[float, int]] = {}


def begin_leg(run_id: str, elapsed_before: int) -> float:
    """Mark a run's execution leg as live; returns the token for end_leg."""
    token = time.monotonic()
    _live_legs[str(run_id)] = (token, max(0, int(elapsed_before or 0)))
    return token


def end_leg(run_id: str, token: float) -> None:
    leg = _live_legs.get(str(run_id))
    if leg is not None and leg[0] == token:
        _live_legs.pop(str(run_id), None)


def _elapsed_int(value) -> int:
    return value if type(value) is int and value >= 0 else 0


def message_work_ms(run_id, message) -> int:
    """Active ms of the run's current message, including a live leg's tail."""
    stored = _elapsed_int((message or {}).get("elapsedMs") if isinstance(message, dict) else None)
    leg = _live_legs.get(str(run_id))
    if leg is None:
        return stored
    return max(stored, leg[1] + int((time.monotonic() - leg[0]) * 1000))


def run_work_ms(run_id, agent, message) -> int:
    """Cumulative active ms across every leg of a run (never wall clock)."""
    before = _elapsed_int((agent or {}).get("work_ms_before") if isinstance(agent, dict) else None)
    return before + message_work_ms(run_id, message)


def row_work_ms(row) -> int:
    """run_work_ms for a raw chat_runs row (JSON request/message columns)."""
    if row is None:
        return 0
    parsed = []
    for key in ("request", "message"):
        try:
            value = json.loads(row[key] or "{}")
        except (json.JSONDecodeError, TypeError, ValueError):
            value = {}
        parsed.append(value if isinstance(value, dict) else {})
    req, message = parsed
    return run_work_ms(row["id"], req.get("agent"), message)


def run_work_fields(run_id, agent, message) -> dict:
    """work_ms plus whether it is advancing right now (a leg is executing).

    work_ms is None for a row with no timing at all (never checkpointed):
    unknown, not zero.
    """
    live = str(run_id) in _live_legs
    known = live or (isinstance(message, dict) and message.get("elapsedMs") is not None) or (
        isinstance(agent, dict) and agent.get("work_ms_before") is not None)
    return {
        "work_ms": run_work_ms(run_id, agent, message) if known else None,
        "work_ms_before": _elapsed_int((agent or {}).get("work_ms_before") if isinstance(agent, dict) else None),
        "work_live": live,
    }


def list_thread_agents(user_id, thread_id, limit=100):
    """Child runs (subagents) of a thread, newest first, with spawned-child counts."""
    rows = db.fetchall(
        """SELECT id, status, request, message, created_at, updated_at, cancel_requested, cancel_requested_at,
                  resume_count, (provider_messages IS NOT NULL) AS has_transcript, parent_run_id, root_run_id, agent_depth
           FROM chat_runs
           WHERE user_id=? AND thread_id=? AND parent_run_id IS NOT NULL
             AND (status IN ('queued','running') OR id IN (
                 SELECT id FROM (SELECT id FROM chat_runs
                   WHERE user_id=? AND thread_id=? AND parent_run_id IS NOT NULL
                     AND status NOT IN ('queued','running')
                   ORDER BY updated_at DESC, id DESC LIMIT ?) recent
             ))
           ORDER BY created_at DESC, id DESC""",
        (user_id, thread_id, user_id, thread_id, max(1, min(int(limit), 200))),
    )
    counts: dict[str, int] = {}
    root = thread_root_run(user_id, thread_id) if rows else None
    stopped_at = str(root.get("stopped_at") or "") if root else ""
    if rows:
        placeholders = ",".join("?" * len(rows))
        for item in db.fetchall(
            f"""SELECT parent_run_id AS pid, COUNT(*) AS c FROM chat_runs
                WHERE parent_run_id IN ({placeholders}) GROUP BY parent_run_id""",
            [row["id"] for row in rows],
        ):
            counts[item["pid"]] = int(item["c"])
    out = []
    for row in rows:
        try:
            req = json.loads(row["request"])
        except (json.JSONDecodeError, TypeError):
            req = {}
        req = req if isinstance(req, dict) else {}
        agent = req.get("agent") if isinstance(req.get("agent"), dict) else {}
        try:
            message = json.loads(row["message"])
        except (json.JSONDecodeError, TypeError):
            message = {}
        message = message if isinstance(message, dict) else {}
        tools = message.get("tools") if isinstance(message.get("tools"), list) else []
        tool_counts: dict[str, int] = {}
        for trace in tools:
            if not isinstance(trace, dict):
                continue
            name = str(trace.get("name") or "")
            if not name:
                continue
            tool_counts[name] = tool_counts.get(name, 0) + 1
        recent = chat_views.agent_recent_calls(tools)
        quiet_s = chat_views._iso_age(row["updated_at"] or row["created_at"])
        current = chat_views.agent_current_activity(message, agent.get("activity"), row["status"])
        if row["status"] in chat_tx.ACTIVE:
            if quiet_s >= STALL_IDLE_SECONDS:
                current = "stalled"
            elif quiet_s >= STALL_WAIT_SECONDS and current in ("thinking", "writing"):
                current = "waiting"
        entry = {
            "id": row["id"],
            "status": row["status"],
            "cancel_requested": bool(row["cancel_requested"]),
            "cancel_requested_at": str(row["cancel_requested_at"] if "cancel_requested_at" in row.keys() else ""),
            "resume_count": int(row["resume_count"] if "resume_count" in row.keys() else 0),
            "has_transcript": bool(row["has_transcript"]),
            "resumable": bool(
                row["status"] in ("interrupted", "cancelled")
                and row["has_transcript"]
                and int(row["resume_count"] if "resume_count" in row.keys() else 0) < MAX_RESUME_ATTEMPTS
            ),
            "description": str(agent.get("description") or "")[:200],
            "subagent_type": str(agent.get("subagent_type") or "general-purpose"),
            "depth": int(row["agent_depth"] or 0),
            "model": str(req.get("model") or ""),
            "parent_run_id": row["parent_run_id"],
            "root_run_id": row["root_run_id"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "summary": (chat_views.child_writeup({"status": row["status"], "message": message})
                        if row["status"] not in chat_tx.ACTIVE else "")[:200],
            "error": str(message.get("error") or "")[:400],
            "children": counts.get(row["id"], 0),
            "tool_counts": tool_counts,
            "recent": recent,
            "current": current,
            "quiet_s": round(quiet_s, 1),
            "wake_state": _agent_wake_state(agent, row["status"], stopped_at, row["cancel_requested"]),
            "context": chat_views._agent_context_chip(req, agent, row),
            **run_work_fields(row["id"], agent, message),
        }
        out.append(entry)
    return out


def _merge_thread_loaded(thread_row, run_row) -> list[str]:
    """Union the run's loaded catalog tools into the thread's; never shrink (user asked: loaded stays loaded)."""
    from .agent_catalog import sanitize_loaded

    def _parse(raw):
        try:
            parsed = json.loads(raw) if raw else []
        except (json.JSONDecodeError, TypeError, ValueError):
            return []
        return parsed if isinstance(parsed, list) else []

    keys = thread_row.keys() if hasattr(thread_row, "keys") else []
    existing = _parse(thread_row["loaded_tools"] if "loaded_tools" in keys else None)
    run_loaded = _parse(run_row["loaded_tools"] if "loaded_tools" in run_row.keys() else None)
    if not run_loaded:
        return existing if isinstance(existing, list) else []
    merged = list(existing)
    for name in run_loaded:
        if name not in merged:
            merged.append(name)
    return sanitize_loaded(merged)


def finish_run(run_id, message, status):
    from . import chats

    if status not in chat_tx.TERMINAL:
        raise chats.ChatValidationError("invalid terminal run status")
    with chat_tx.transaction() as conn:
        row = conn.execute("SELECT * FROM chat_runs WHERE id=?", (run_id,)).fetchone()
        if row is None or row["status"] not in chat_tx.ACTIVE:
            return
        message = chat_views._message(message, row["message_id"])
        if row["cancel_requested"]:
            status = "cancelled"
            message.update(status=status, stopped=True)
        request = json.loads(row["request"])
        chat_views._advance_steer_display(request, message)
        child = bool(row["parent_run_id"] if "parent_run_id" in row.keys() else None)
        agent = request.get("agent") if isinstance(request.get("agent"), dict) else {}
        if (not child and status in ("completed", "failed") and not row["cancel_requested"]
                and agent.get("pending_steers") and int(agent.get("steer_continuations") or 0) < 3):
            # An accepted Send now can race a provider error or finalizer. It
            # owns a continuation; never strand it on a terminal run.
            # followup_continuation freezes the sealed transcript: no session
            # tail re-injection onto its last, already-sent user row.
            source = json.loads(row["provider_messages"] or "null") or request.get("messages") or []
            request["messages"] = seal_provider_messages(source)
            agent.update(skip_sanitize=True, steer_closed=False, followup_continuation=True,
                         steer_continuations=int(agent.get("steer_continuations") or 0) + 1,
                         # The continuation's round counter restarts at 0 on
                         # this same message: start its trace ids past every
                         # round already in the transcript.
                         event_base=sum(1 for item in request["messages"]
                                        if isinstance(item, dict) and item.get("role") == "assistant"
                                        and item.get("tool_calls")))
            agent["activity"] = {"phase": "queued"}
            agent["usage_before"] = message.get("usage") or {}
            request["agent"] = agent
            message.update(error="", stopped=False)
            conn.execute("UPDATE chat_runs SET status='queued',request=?,message=?,updated_at=? WHERE id=?",
                         (chat_tx.dumps(request), chat_tx.dumps(message), db.now(), run_id))
            chat_tx.event(conn, row["user_id"], "run.resumed", row["thread_id"],
                  {"run_id": run_id, "message_id": row["message_id"], "reason": "pending_steer"})
            return
        if not child and agent.get("pending_steers"):
            # Accepted steers the requeue above could not take (continuation
            # budget spent, or the user stopped): their rows stay in the thread
            # and replay with the next turn, so say so instead of dropping them
            # silently.
            _append_notice(message, "A queued message was not delivered this turn; it will be included with your next message.")
        if not child:
            request.pop("messages", None)
        if isinstance(request.get("agent"), dict):
            request["agent"]["activity"] = {"phase": "idle", "status": status}
        conn.execute("UPDATE chat_runs SET message=?,request=?,status=?,updated_at=? WHERE id=?", (chat_tx.dumps(message), chat_tx.dumps(request), status, db.now(), run_id))
        chat_checkpoint._state_write_at.pop(run_id, None)
        chat_checkpoint._forget_state_cursor(run_id)
        chat_checkpoint._state_dirty.discard(run_id)
        chat_checkpoint._request_cache.pop(run_id, None)
        try:
            from .schedules import sync_run_status

            sync_run_status(
                conn, row["user_id"], run_id, status,
                str(message.get("error") or "") if isinstance(message, dict) else "",
            )
        except Exception:
            logger.exception("Schedule fire status sync failed for %s", run_id)
        from .vault import unpin_run

        agent = request.get("agent") if isinstance(request.get("agent"), dict) else {}
        root = row["root_run_id"] if "root_run_id" in row.keys() else None
        if agent.get("coding"):
            unpin_run(row["user_id"], run_id)
        if child:
            payload = {
                "run_id": run_id,
                "message_id": row["message_id"],
                "message": message,
                "status": status,
                "parent_run_id": row["parent_run_id"],
                "root_run_id": root,
                "summary": (message.get("error") or message.get("content") or "")[:2000],
                "child_id": run_id,
                "work_ms": run_work_ms(run_id, agent, message),
            }
            chat_tx.event(conn, row["user_id"], "agent." + status, row["thread_id"], payload)
            chat_tx.thread_event(conn, row["user_id"], row["thread_id"])
            return
        projected = chat_views.display_messages(request, message, row["message_id"])
        aggregate_message = message
        message = projected[row["message_id"]]
        thread = chat_tx.lock_thread(conn, row["thread_id"], row["user_id"])
        revision = None
        messages = chat_schema.stored_messages(thread["messages"]) if thread else None
        if thread and messages is None:
            # The reply stays on the run row; never overwrite the transcript.
            logger.error("Thread %s transcript is unreadable; run %s not merged into it", row["thread_id"], run_id)
            thread = None
        if thread:
            messages = chat_views._project_thread_messages(messages, projected, request)
            revision = thread["revision"] + 1
            merged_tools = _merge_thread_loaded(thread, row)
            conn.execute(
                "UPDATE chat_threads SET messages=?,loaded_tools=?,revision=revision+1,updated_at=?,unread=1 WHERE id=?",
                (chat_tx.dumps(messages), chat_tx.dumps(merged_tools), db.now(), row["thread_id"]),
            )
            from . import chat_history
            chat_history.finalize(conn, row, request, messages, aggregate_message)
        chat_tx.event(conn, row["user_id"], "run." + status, row["thread_id"],
              {"run_id": run_id, "message_id": row["message_id"], "message": message, "status": status, "revision": revision})
        if thread:
            chat_tx.thread_event(conn, row["user_id"], row["thread_id"])


def request_cancel(user_id, run_id):
    with chat_tx.transaction() as conn:
        row = conn.execute("SELECT * FROM chat_runs WHERE id=? AND user_id=?", (run_id, user_id)).fetchone()
        if row is None:
            return False
        if row["parent_run_id"] and row["status"] not in chat_tx.ACTIVE:
            return False
        root = row["root_run_id"] if "root_run_id" in row.keys() and row["root_run_id"] else row["id"]
        ts = db.now()
        if not row["parent_run_id"]:
            # A root Stop is durable thread intent and must outlive the run it
            # stopped: the flag on a root that already finished is what
            # automatic_stop_guard reads to keep a just-completed child from
            # requeueing or waking the thread, and the continuity schedules
            # bound to this thread pause instead of restarting it. The run row
            # is locked (FOR UPDATE on MariaDB), so this cannot interleave with
            # an admission check on the same root.
            flagged = conn.execute(
                "UPDATE chat_runs SET cancel_requested=1,cancel_requested_at=? WHERE id=? AND cancel_requested=0",
                (ts, run_id),
            ).rowcount
            conn.execute(
                "UPDATE chat_schedules SET paused=1,updated_at=? WHERE user_id=? AND thread_id=? AND fresh_each_fire=0",
                (ts, user_id, row["thread_id"]),
            )
            if flagged and row["status"] in chat_tx.ACTIVE:
                chat_tx.event(conn, user_id, "run.cancel_requested", row["thread_id"], _cancel_event_payload(row))
        targets = conn.execute(
            "SELECT * FROM chat_runs WHERE user_id=? AND status IN ('queued','running') AND (id=? OR root_run_id=?)",
            (user_id, run_id, root),
        ).fetchall()
        for item in targets:
            if item["cancel_requested"]:
                continue
            conn.execute("UPDATE chat_runs SET cancel_requested=1,cancel_requested_at=?,updated_at=? WHERE id=?", (ts, ts, item["id"]))
            kind = "agent.cancel_requested" if item["parent_run_id"] else "run.cancel_requested"
            chat_tx.event(conn, user_id, kind, item["thread_id"], _cancel_event_payload(item))
        return True


def request_interrupt(user_id, run_id):
    """Send now: flag one active root run to stop its reply, nothing else.

    Unlike a root Stop (request_cancel), its subagents, the thread's other
    subagents and its schedules are left alone. Returns the root's live
    children that are not already stopping, or None when run_id is not an
    active root. The root row is locked until commit and a stopping parent
    admits no new child, so the list is complete."""
    with chat_tx.transaction() as conn:
        row = conn.execute("SELECT * FROM chat_runs WHERE id=? AND user_id=?", (run_id, user_id)).fetchone()
        if row is None or row["parent_run_id"] or row["status"] not in chat_tx.ACTIVE:
            return None
        ts = db.now()
        if conn.execute(
            "UPDATE chat_runs SET cancel_requested=1,cancel_requested_at=?,updated_at=? WHERE id=? AND cancel_requested=0",
            (ts, ts, run_id),
        ).rowcount:
            chat_tx.event(conn, user_id, "run.cancel_requested", row["thread_id"], _cancel_event_payload(row))
        return [item["id"] for item in conn.execute(
            "SELECT id FROM chat_runs WHERE user_id=? AND parent_run_id=? AND status IN ('queued','running') AND cancel_requested=0",
            (user_id, run_id),
        ).fetchall()]


def request_cancel_thread_agents(user_id, run_id):
    """Stop of a main run also stops the thread's other live subagents.

    Background subagents launched by earlier turns belong to those turns'
    trees, so the tree cancel misses them; after Stop nothing in the
    conversation may keep generating. Returns the ids flagged. Background
    commands and monitors are left alone (they never generate)."""
    row = db.fetchone("SELECT thread_id, parent_run_id FROM chat_runs WHERE id=? AND user_id=?", (run_id, user_id))
    if row is None or row["parent_run_id"]:
        return []
    live = db.fetchall(
        """SELECT id, parent_run_id, message_id, thread_id, status, cancel_requested FROM chat_runs
           WHERE user_id=? AND thread_id=? AND parent_run_id IS NOT NULL AND root_run_id<>?
             AND status IN ('queued','running')""",
        (user_id, row["thread_id"], run_id),
    )
    if not live:
        return []
    ts = db.now()
    with chat_tx.transaction() as conn:
        for item in live:
            cur = conn.execute(
                "UPDATE chat_runs SET cancel_requested=1,cancel_requested_at=?,updated_at=? WHERE id=? AND status IN ('queued','running') AND cancel_requested=0",
                (ts, ts, item["id"]),
            )
            if getattr(cur, "rowcount", 1):
                chat_tx.event(conn, user_id, "agent.cancel_requested", item["thread_id"], _cancel_event_payload(item))
    return [item["id"] for item in live]


def request_cancel_self(user_id, run_id, by_user=False):
    """Cancel one run and its strict descendants only; the rest of the root tree keeps running.

    by_user marks the stopped subagents USER_STOPPED so their reports wait
    for the user's next message instead of waking the main agent."""
    ids = []
    # Lock-free tree read (see request_cancel_descendants): per-row updates
    # re-check liveness, so the scan must not FOR UPDATE the whole tree.
    row = db.fetchone(
        "SELECT id, root_run_id FROM chat_runs WHERE id=? AND user_id=?",
        (run_id, user_id),
    )
    if row is None:
        return ids
    root = row["root_run_id"] or row["id"]
    tree = db.fetchall(
        "SELECT id, parent_run_id, message_id, thread_id, cancel_requested, status FROM chat_runs WHERE user_id=? AND root_run_id=?",
        (user_id, root),
    )
    by_parent: dict[str, list] = {}
    for item in tree:
        by_parent.setdefault(item["parent_run_id"], []).append(item)
    frontier = [run_id]
    seen = set()
    doomed = []
    while frontier:
        current = frontier.pop()
        if current in seen:
            continue
        seen.add(current)
        item = next((r for r in tree if r["id"] == current), None)
        if item is None:
            continue
        ids.append(item["id"])
        if item["status"] in chat_tx.ACTIVE and not item["cancel_requested"]:
            doomed.append(item)
        frontier.extend(child["id"] for child in by_parent.get(current, []))
    if doomed:
        ts = db.now()
        flag = USER_STOPPED if by_user else 1
        with chat_tx.transaction() as conn:
            for item in doomed:
                cur = conn.execute(
                    "UPDATE chat_runs SET cancel_requested=?,cancel_requested_at=?,updated_at=? WHERE id=? AND status IN ('queued','running') AND cancel_requested=0",
                    (flag, ts, ts, item["id"]),
                )
                if getattr(cur, "rowcount", 1):
                    kind = "agent.cancel_requested" if item["parent_run_id"] else "run.cancel_requested"
                    chat_tx.event(conn, user_id, kind, item["thread_id"], _cancel_event_payload(item))
    return ids


def _json_flag(value) -> bool:
    """Truthiness for a json_extract column value: SQLite returns 1/0, while
    MariaDB's JSON_VALUE returns 'true'/'false'/'null' strings (whose plain
    bool() would be True even for 'false')."""
    if isinstance(value, str):
        return value not in ("", "0", "false", "null")
    return bool(value)


def _cancel_tree_rows(user_id, root, with_background):
    """Lightweight root-tree scan for cancellation walks.

    The request blob is only needed for its background flag; resolve it in
    SQL so a fan-out finishing together does not ship every run's full
    request transcript into memory. Malformed JSON in one row falls back to
    decoding in Python with the original semantics."""
    base = "SELECT id, parent_run_id, message_id, thread_id, cancel_requested, status"
    if not with_background:
        return db.fetchall(f"{base} FROM chat_runs WHERE user_id=? AND root_run_id=?", (user_id, root))
    try:
        return [
            {**item, "background": _json_flag(item["background"])}
            for item in db.fetchall(
                f"{base}, COALESCE(json_extract(request, '$.agent.background'), 0) AS background"
                " FROM chat_runs WHERE user_id=? AND root_run_id=?",
                (user_id, root),
            )
        ]
    except Exception:
        out = []
        for item in db.fetchall(f"{base}, request FROM chat_runs WHERE user_id=? AND root_run_id=?", (user_id, root)):
            try:
                agent = (json.loads(item["request"]) or {}).get("agent")
            except (json.JSONDecodeError, TypeError):
                agent = None
            out.append({
                **{key: item[key] for key in item.keys() if key != "request"},
                "background": bool(isinstance(agent, dict) and agent.get("background")),
            })
        return out


def request_cancel_descendants(user_id, run_id, skip_background=False):
    """Cancel a run's descendants. skip_background keeps background subagents
    (and their subtrees) alive past the end of the spawning turn."""
    ids = []
    # Leaf agents are the common case. Do not load and decode the entire
    # root tree for each of hundreds of siblings finishing together.
    if not db.fetchone("SELECT id FROM chat_runs WHERE user_id=? AND parent_run_id=? LIMIT 1", (user_id, run_id)):
        return ids
    # Read the tree without row locks: a FOR UPDATE scan of the whole root
    # tree on every child finalization serializes (and deadlocks) once
    # hundreds of subagents finish concurrently. The per-row updates below
    # re-check liveness in their WHERE clause.
    row = db.fetchone("SELECT id, root_run_id FROM chat_runs WHERE id=? AND user_id=?", (run_id, user_id))
    if row is None:
        return ids
    root = row["root_run_id"] or row["id"]
    tree = _cancel_tree_rows(user_id, root, skip_background)
    by_parent: dict[str, list] = {}
    for item in tree:
        by_parent.setdefault(item["parent_run_id"], []).append(item)
    frontier = [item["id"] for item in by_parent.get(run_id, [])]
    seen = set()
    doomed = []
    while frontier:
        current = frontier.pop()
        if current in seen:
            continue
        seen.add(current)
        item = next((r for r in tree if r["id"] == current), None)
        if item is None:
            continue
        if skip_background and item["background"]:
            continue
        ids.append(item["id"])
        if item["status"] in chat_tx.ACTIVE and not item["cancel_requested"]:
            doomed.append(item)
        frontier.extend(child["id"] for child in by_parent.get(current, []))
    if doomed:
        ts = db.now()
        with chat_tx.transaction() as conn:
            for item in doomed:
                cur = conn.execute(
                    "UPDATE chat_runs SET cancel_requested=1,cancel_requested_at=?,updated_at=? WHERE id=? AND status IN ('queued','running') AND cancel_requested=0",
                    (ts, ts, item["id"]),
                )
                if getattr(cur, "rowcount", 1):
                    kind = "agent.cancel_requested" if item["parent_run_id"] else "run.cancel_requested"
                    chat_tx.event(conn, user_id, kind, item["thread_id"], _cancel_event_payload(item))
    return ids


def descendant_ids(run_id):
    """Live strict descendants of run_id (not siblings, not the root tree)."""
    row = db.fetchone("SELECT id, root_run_id FROM chat_runs WHERE id=?", (run_id,))
    if row is None:
        return []
    root = row["root_run_id"] or row["id"]
    # Walk finished nodes too: a background grandchild can outlive its parent.
    tree = db.fetchall(
        "SELECT id, parent_run_id, status FROM chat_runs WHERE root_run_id=?",
        (root,),
    )
    by_parent: dict[str, list] = {}
    live = set()
    for item in tree:
        by_parent.setdefault(item["parent_run_id"], []).append(item["id"])
        if item["status"] in ("queued", "running"):
            live.add(item["id"])
    out = []
    frontier = list(by_parent.get(run_id, []))
    seen = set()
    while frontier:
        current = frontier.pop()
        if current in seen:
            continue
        seen.add(current)
        if current in live:
            out.append(current)
        frontier.extend(by_parent.get(current, []))
    return out


RESUME_NOTICE = "Resumed after server restart from the last saved tool results."
USER_RESUME_NOTICE = "Continued from the last saved tool results."
USER_STOP_TOOL_NOTICE = "Tool interrupted when the user stopped the turn; outcome unknown. Verify its effect before repeating it."
INTERRUPT_NOTICE = "Generation interrupted by server restart."
STALL_NOTICE = "Generation stalled (no progress from the model)."
STOP_NOTICE = "Stopped."


def _append_notice(message, line):
    lines = [s.strip() for s in str(message.get("notice") or "").splitlines() if s.strip()]
    if line and not any(s.lower() == line.lower() for s in lines):
        lines.append(line)
    from . import chats

    message["notice"] = "\n".join(lines)[: chats.NOTICE_MAX]


def _completed_assistant_text(messages):
    parts = []
    for msg in messages or []:
        if not isinstance(msg, dict) or msg.get("role") != "assistant":
            continue
        text = msg.get("content")
        if isinstance(text, str) and text.strip():
            parts.append(text.strip())
    return "\n\n".join(parts)


def seal_provider_messages(messages, *, notice="Tool interrupted by server restart."):
    """Fill missing tool results so a resumed turn never re-executes a tool."""
    if not isinstance(messages, list):
        return []
    out = [msg for msg in messages if isinstance(msg, dict)]
    i = 0
    while i < len(out):
        msg = out[i]
        calls = msg.get("tool_calls") if msg.get("role") == "assistant" else None
        if not isinstance(calls, list) or not calls:
            i += 1
            continue
        named = []
        for call in calls:
            if isinstance(call, dict) and isinstance(call.get("id"), str) and call["id"]:
                fn = call.get("function") if isinstance(call.get("function"), dict) else {}
                named.append((call["id"], str(fn.get("name") or "")))
        have = set()
        j = i + 1
        while j < len(out) and out[j].get("role") == "tool":
            cid = out[j].get("tool_call_id")
            if isinstance(cid, str) and cid:
                have.add(cid)
            j += 1
        missing = [(cid, nm) for cid, nm in named if cid not in have]
        if missing:
            inserts = [
                {
                    "role": "tool",
                    "tool_call_id": cid,
                    "content": (
                        "Tool interrupted by server restart. The spawned subagent may still be "
                        "running — call list_subagents to check its state before spawning a duplicate."
                        if nm == "spawn_subagent" else notice
                    ),
                }
                for cid, nm in missing
            ]
            out[j:j] = inserts
            i = j + len(inserts)
            continue
        i = j if j > i + 1 else i + 1
    return out


MAX_RESUME_ATTEMPTS = 3

