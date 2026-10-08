"""Child-run admission and live-run messaging: results, follow-ups, steers, scratch."""
from __future__ import annotations

import logging
import json
import re
from uuid import uuid4
import threading

from . import db, chat_tx, chat_views, chat_run_state

logger = logging.getLogger(__name__)

# Admission limits remain conservative until synchronized load tests verify
# bounded memory and latency at the proposed higher concurrency.
LIVE_TREE_CHILDREN = 32
# Buffer for results queued onto a generating run. Larger than the live-tree
# cap so finished children are not silently dropped while a long turn runs;
# anything past this still falls back to the wake path (wake_sent stays 0).
PENDING_CHILD_RESULTS = 64
PER_AGENT_PARENT = 32
PER_AGENT_NESTED = 8
LIVE_USER_RUNS = 128
LIVE_PROCESS_RUNS = 256
# Named string slots for one run. Spawn prompts live here so a failed fan-out
# can retry by id instead of re-emitting every prompt in the next tool call.
# A slot must fit one scratch read: stay under chat_runtime.TOOL_RESULT_CHARS,
# which would otherwise clip the text the model reads back.
SCRATCH_MAX_SLOTS = 32
SCRATCH_VALUE_MAX = 20_000


class SpawnCapError(Exception):
    def __init__(self, detail: str):
        super().__init__(detail)
        self.detail = detail


def queue_child_result(parent_run_id: str, envelope: str, child_id: str | None = None) -> bool:
    """If the parent is still generating, stash a result for the next parent round (OpenCode inject).

    Does not mark wake_sent; delivery is acknowledged by save_turn_progress
    once the envelope is durable in the parent transcript, so a parent that
    finishes first still wakes.
    """
    if not parent_run_id or not envelope:
        return False
    try:
        with chat_tx.transaction() as conn:
            row = conn.execute("SELECT id,status,request FROM chat_runs WHERE id=?", (parent_run_id,)).fetchone()
            if row is None or row["status"] not in chat_tx.ACTIVE:
                return False
            try:
                req = json.loads(row["request"] or "{}")
            except (json.JSONDecodeError, TypeError):
                req = {}
            if not isinstance(req, dict):
                req = {}
            agent = req.get("agent") if isinstance(req.get("agent"), dict) else {}
            pending = agent.get("pending_child_results")
            if not isinstance(pending, list):
                pending = []
            pending = [
                item for item in pending
                if not (isinstance(item, dict) and str(item.get("id") or "") == str(child_id or ""))
            ]
            child = conn.execute("SELECT request FROM chat_runs WHERE id=?", (child_id,)).fetchone() if child_id else None
            child_agent = (json.loads(child["request"]).get("agent") or {}) if child else {}
            generation = str(child_agent.get("delivery_generation") or "")
            if generation and f"<task_generation>{generation}</task_generation>" not in envelope:
                return False  # A result from a superseded execution must not be queued.
            pending.append({"id": str(child_id or ""), "text": str(envelope), "generation": generation})
            agent["pending_child_results"] = pending[-PENDING_CHILD_RESULTS:]
            req["agent"] = agent
            conn.execute(
                "UPDATE chat_runs SET request=?,updated_at=? WHERE id=?",
                (chat_tx.dumps(req), db.now(), parent_run_id),
            )
            return True
    except Exception:
        logger.warning(
            "queue_child_result failed for parent %s child %s",
            parent_run_id, child_id, exc_info=True,
        )
        return False


def drop_pending_child_result(parent_run_id: str, child_id: str) -> None:
    """Remove a queued child result the parent already received another way."""
    if not parent_run_id or not child_id:
        return
    with chat_tx.transaction() as conn:
        row = conn.execute("SELECT id,request FROM chat_runs WHERE id=?", (parent_run_id,)).fetchone()
        if row is None:
            return
        try:
            req = json.loads(row["request"] or "{}")
        except (json.JSONDecodeError, TypeError):
            return
        if not isinstance(req, dict):
            return
        agent = req.get("agent") if isinstance(req.get("agent"), dict) else {}
        pending = agent.get("pending_child_results")
        if not isinstance(pending, list) or not pending:
            return
        kept = [
            item for item in pending
            if not (isinstance(item, dict) and str(item.get("id") or "") == str(child_id))
        ]
        if len(kept) == len(pending):
            return
        agent["pending_child_results"] = kept
        req["agent"] = agent
        conn.execute(
            "UPDATE chat_runs SET request=?,updated_at=? WHERE id=?",
            (chat_tx.dumps(req), db.now(), parent_run_id),
        )


def take_child_results(parent_run_id: str) -> list[str]:
    """Peek injected background-subagent results for the live parent turn.

    Non-destructive: an entry is only cleared (and its child marked
    wake-sent) by save_turn_progress, in the same transaction that persists
    the transcript containing the envelope — a crash between injection and
    persistence can no longer acknowledge a report that was never delivered.
    """
    if not parent_run_id:
        return []
    row = db.fetchone("SELECT id,status,request,provider_messages FROM chat_runs WHERE id=?", (parent_run_id,))
    if row is None or row["status"] not in chat_tx.ACTIVE:
        return []
    try:
        req = json.loads(row["request"] or "{}")
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(req, dict):
        return []
    agent = req.get("agent") if isinstance(req.get("agent"), dict) else {}
    pending = agent.get("pending_child_results")
    if not isinstance(pending, list) or not pending:
        return []
    transcript = row["provider_messages"] or ""
    ids = [str(item.get("id") or "") for item in pending if isinstance(item, dict) and item.get("id")]
    generations = {}
    if ids:
        for child in db.fetchall(f"SELECT id,request FROM chat_runs WHERE id IN ({','.join('?' for _ in ids)})", ids):
            generations[child["id"]] = str((json.loads(child["request"]).get("agent") or {}).get("delivery_generation") or "")
    out = []
    for item in pending:
        if isinstance(item, dict):
            cid = str(item.get("id") or "")
            if cid in generations and str(item.get("generation") or "") != generations[cid]:
                continue
            text = str(item.get("text") or "").strip()
            if text and chat_tx._json_needle(text) not in transcript:
                out.append(text)
        else:
            text = str(item).strip()
            if text and chat_tx._json_needle(text) not in transcript:
                out.append(text)
    return out


_SCRATCH_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


class ScratchError(ValueError):
    pass


def _scratch_of(agent: dict) -> dict:
    raw = agent.get("scratch")
    if not isinstance(raw, dict):
        return {}
    out = {}
    for key, value in raw.items():
        if isinstance(key, str) and isinstance(value, str) and _SCRATCH_ID.match(key):
            out[key] = value[:SCRATCH_VALUE_MAX]
            if len(out) >= SCRATCH_MAX_SLOTS:
                break
    return out


def _carried_scratch(conn, thread_id, messages) -> dict:
    """The pad of this thread's latest top-level run, for the next turn or wake.

    "Latest" follows thread message order: created_at has one-second
    resolution, so a wake started right after a turn could tie with it."""
    order = [
        str(m.get("id")) for m in reversed(messages or [])
        if isinstance(m, dict) and m.get("role") == "assistant" and m.get("id")
    ][:50]
    if not order:
        return {}
    rows = conn.execute(
        f"SELECT message_id,request FROM chat_runs WHERE thread_id=? AND parent_run_id IS NULL "
        f"AND message_id IN ({','.join('?' for _ in order)})",
        (thread_id, *order),
    ).fetchall()
    by_message = {row["message_id"]: row["request"] for row in rows}
    for message_id in order:
        if message_id not in by_message:
            continue
        try:
            req = json.loads(by_message[message_id] or "{}")
        except (json.JSONDecodeError, TypeError):
            return {}
        agent = req.get("agent") if isinstance(req, dict) and isinstance(req.get("agent"), dict) else {}
        return _scratch_of(agent)
    return {}


def scratch_list(run_id: str) -> dict[str, str]:
    """Every slot on this run. Empty when the run or pad is missing."""
    if not run_id:
        return {}
    row = db.fetchone("SELECT request FROM chat_runs WHERE id=?", (run_id,))
    if row is None:
        return {}
    try:
        req = json.loads(row["request"] or "{}")
    except (json.JSONDecodeError, TypeError):
        return {}
    if not isinstance(req, dict):
        return {}
    agent = req.get("agent") if isinstance(req.get("agent"), dict) else {}
    return _scratch_of(agent)


def scratch_read(run_id: str, name: str) -> str | None:
    """Return one scratch slot, or None when the run or name is missing."""
    if not run_id or not _SCRATCH_ID.match(str(name or "")):
        return None
    row = db.fetchone("SELECT request FROM chat_runs WHERE id=?", (run_id,))
    if row is None:
        return None
    try:
        req = json.loads(row["request"] or "{}")
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(req, dict):
        return None
    agent = req.get("agent") if isinstance(req.get("agent"), dict) else {}
    pad = _scratch_of(agent)
    return pad.get(name)


def scratch_apply(run_id: str, ops: list[dict]) -> dict:
    """Apply put/append/edit/clear to this run's scratch pad. Returns the pad after the write.

    Each agent has its own pad. The main agent's pad carries into the thread's
    next turn and every wake (start_turn copies it from the previous top-level
    run); a subagent's pad carries into its resume. A child never sees the
    parent's slots. A failing op rolls back the whole call.
    """
    if not run_id:
        raise ScratchError("no active session")
    if not isinstance(ops, list) or not ops:
        raise ScratchError("ops must be a non-empty array")
    if len(ops) > SCRATCH_MAX_SLOTS:
        raise ScratchError(f"at most {SCRATCH_MAX_SLOTS} ops per call")
    with chat_tx.transaction() as conn:
        row = conn.execute("SELECT id,status,request FROM chat_runs WHERE id=?", (run_id,)).fetchone()
        if row is None or row["status"] not in chat_tx.ACTIVE:
            raise ScratchError("no active session")
        try:
            req = json.loads(row["request"] or "{}")
        except (json.JSONDecodeError, TypeError):
            req = {}
        if not isinstance(req, dict):
            req = {}
        agent = req.get("agent") if isinstance(req.get("agent"), dict) else {}
        pad = _scratch_of(agent)
        for index, op in enumerate(ops):
            if not isinstance(op, dict):
                raise ScratchError(f"ops[{index}] must be an object")
            extra = set(op) - {"op", "name", "text", "old", "new"}
            if extra:
                raise ScratchError(f"ops[{index}] has unknown keys: {', '.join(sorted(extra))}")
            kind = str(op.get("op") or "").strip().lower()
            name = str(op.get("name") or "").strip()
            if not _SCRATCH_ID.match(name):
                raise ScratchError(f"ops[{index}].name must match { _SCRATCH_ID.pattern }")
            text = op.get("text")
            if kind == "delete":
                kind = "clear"
            if kind == "edit":
                find, repl = op.get("old"), op.get("new")
                if not isinstance(find, str) or not find or not isinstance(repl, str):
                    raise ScratchError(f"ops[{index}] edit needs old (non-empty) and new strings")
                if name not in pad:
                    raise ScratchError(f"ops[{index}]: scratch slot {name!r} not found")
                hits = pad[name].count(find)
                if hits != 1:
                    raise ScratchError(
                        f"ops[{index}]: old must match exactly once in {name!r} (found {hits}); "
                        "add surrounding text to make it unique, or put the whole slot"
                    )
                text = pad[name].replace(find, repl, 1)
                if len(text) > SCRATCH_VALUE_MAX:
                    raise ScratchError(f"ops[{index}] exceeds {SCRATCH_VALUE_MAX} characters")
                pad[name] = text
                continue
            if kind in ("put", "append"):
                if not isinstance(text, str):
                    raise ScratchError(f"ops[{index}].text is required")
                if len(text) > SCRATCH_VALUE_MAX:
                    raise ScratchError(f"ops[{index}].text exceeds {SCRATCH_VALUE_MAX} characters")
            elif kind != "clear":
                raise ScratchError(f"ops[{index}].op must be put, append, edit, or clear")
            if kind == "clear":
                pad.pop(name, None)
                continue
            if kind == "append":
                text = pad.get(name, "") + text
                if len(text) > SCRATCH_VALUE_MAX:
                    raise ScratchError(f"ops[{index}] exceeds {SCRATCH_VALUE_MAX} characters")
            if name not in pad and len(pad) >= SCRATCH_MAX_SLOTS:
                raise ScratchError(f"scratch pad is full ({SCRATCH_MAX_SLOTS} slots)")
            pad[name] = text
        agent["scratch"] = pad
        req["agent"] = agent
        conn.execute(
            "UPDATE chat_runs SET request=?,updated_at=? WHERE id=?",
            (chat_tx.dumps(req), db.now(), run_id),
        )
        return dict(pad)


def queue_followup(run_id: str, text: str) -> bool:
    """Queue a parent follow-up onto a live child (Grok send_subagent_message)."""
    if not run_id or not str(text or "").strip():
        return False
    with chat_tx.transaction() as conn:
        row = conn.execute(
            "SELECT id,user_id,thread_id,status,cancel_requested,request FROM chat_runs WHERE id=?",
            (run_id,),
        ).fetchone()
        if row is None or row["status"] not in chat_tx.ACTIVE:
            return False
        if row["cancel_requested"]:
            # Same contract as queue_steer: a stopping child never consumes a
            # queued follow-up, so accepting one would lie to the parent.
            return False
        try:
            req = json.loads(row["request"] or "{}")
        except (json.JSONDecodeError, TypeError):
            req = {}
        if not isinstance(req, dict):
            req = {}
        agent = req.get("agent") if isinstance(req.get("agent"), dict) else {}
        pending = agent.get("pending_followups")
        if not isinstance(pending, list):
            pending = []
        if len(pending) >= 8:
            # Reject instead of silently dropping the oldest follow-up.
            return False
        from . import chats as chats_mod

        # A unique id marks delivery. Matching the text itself dropped any
        # follow-up whose words already appeared in the child's transcript
        # ("yes", a repeated "keep going") while telling the parent it was sent.
        pending.append({"id": uuid4().hex[:12], "text": str(text)[: chats_mod.CONTENT_MAX]})
        agent["pending_followups"] = pending
        req["agent"] = agent
        conn.execute(
            "UPDATE chat_runs SET request=?,updated_at=? WHERE id=?",
            (chat_tx.dumps(req), db.now(), run_id),
        )
        # The subagent panel paints pending follow-ups from the run snapshot;
        # this event makes an open panel refetch the moment the tool returns.
        chat_tx.event(
            conn,
            row["user_id"],
            "agent.followup",
            row["thread_id"],
            {"run_id": run_id, "child_id": run_id, "status": row["status"]},
        )
        return True


def take_followups(run_id: str) -> list[str]:
    """Peek queued follow-ups. Delivery is acknowledged by save_turn_progress
    once the follow-up text is durable in provider_messages — same crash
    contract as take_child_results. Terminal queues remain durable until the
    continuation or unconsumed report commits."""
    if not run_id:
        return []
    row = db.fetchone("SELECT id,status,request,provider_messages FROM chat_runs WHERE id=?", (run_id,))
    if row is None:
        return []
    try:
        req = json.loads(row["request"] or "{}")
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(req, dict):
        return []
    agent = req.get("agent") if isinstance(req.get("agent"), dict) else {}
    pending = agent.get("pending_followups")
    if not isinstance(pending, list) or not pending:
        return []
    transcript = row["provider_messages"] or ""
    leftover = [
        chat_views.followup_text(item) for item in pending
        if chat_views.followup_text(item) and not chat_views._followup_delivered(item, transcript)
    ]
    return leftover


def continue_child_with_followups(run_id: str, followups: list) -> bool:
    """Seamlessly continue a just-completed child that still has queued parent follow-ups.

    Same mechanism as the runtime's live drain (chat_runtime take_followups):
    the follow-ups become one new user message on the sealed provider
    transcript and the SAME run id is re-queued, so the child keeps working
    instead of reporting 'unconsumed follow-up' to the parent. The run row is
    terminal when called (post-finish); the durable queue is consumed in the
    same transaction that commits the continuation.
    """
    texts = [chat_views.followup_text(t) for t in (followups or []) if chat_views.followup_text(t)]
    if not texts:
        return False
    with chat_tx.transaction() as conn:
        row = conn.execute(
            "SELECT * FROM chat_runs WHERE id=?", (run_id,)
        ).fetchone()
        if (
            row is None
            or row["status"] != "completed"
            or row["cancel_requested"]
            or not row["parent_run_id"]
        ):
            return False
        try:
            req = json.loads(row["request"] or "{}")
        except (json.JSONDecodeError, TypeError):
            req = {}
        if not isinstance(req, dict):
            req = {}
        agent = req.get("agent") if isinstance(req.get("agent"), dict) else {}
        if chat_run_state.automatic_stop_guard(
            conn, row["user_id"], row["thread_id"], parent_run_id=row["parent_run_id"],
            resumed_at=str(agent.get("user_resumed_at") or ""),
        ):
            return False
        pending = agent.get("pending_followups")
        if isinstance(pending, list):
            undelivered = [chat_views.followup_text(t) for t in pending
                           if chat_views.followup_text(t) and not chat_views._followup_delivered(t, row["provider_messages"] or "")]
            if not undelivered:
                # The queued follow-ups were already delivered into the sealed
                # transcript (or the queue is gone): nothing to continue with.
                return False
        messages = None
        raw_pm = row["provider_messages"] or ""
        if raw_pm:
            try:
                parsed = json.loads(raw_pm)
            except (json.JSONDecodeError, TypeError, ValueError):
                parsed = None
            if isinstance(parsed, list) and parsed:
                messages = [m for m in parsed if isinstance(m, dict)]
        if not messages:
            seed = req.get("messages")
            if isinstance(seed, list) and seed:
                messages = [m for m in seed if isinstance(m, dict)]
        if not messages:
            return False
        from . import chats

        try:
            finished_message = json.loads(row["message"] or "{}")
        except (json.JSONDecodeError, TypeError):
            finished_message = {}
        # The report this leg just wrote was never delivered (the run is
        # continuing instead); carry it so the continuation's report leads
        # with it rather than silently replacing it.
        prior = chat_views.child_writeup({"id": row["id"], "status": "completed",
                               "message": finished_message if isinstance(finished_message, dict) else {},
                               "request": req})
        if prior:
            agent["carried_report"] = prior
        # The continuation starts a fresh message; bank this leg's work time.
        agent["work_ms_before"] = chat_run_state.row_work_ms(row)
        sealed = chat_run_state.seal_provider_messages(messages)
        note = (
            f"{chat_views.FOLLOWUP_FRAME_HEAD} — not written by the end user. "
            "Stay in the assigned task and host/cwd unless this follow-up changes them:\n\n"
            + "\n\n".join(texts)
        )
        sealed = [*sealed, {"role": "user", "content": note}]
        req["messages"] = sealed
        req["display_seed"] = sealed
        agent["pending_followups"] = []
        agent["unconsumed_followups"] = []
        agent.pop("followup_marks", None)
        agent["background"] = True
        agent["followup_continuation"] = True
        agent["delivery_generation"] = uuid4().hex
        agent["wake_sent"] = False
        agent["wake_attempts"] = 0
        req["agent"] = agent
        assistant = chats.parse_messages(
            [{
                "id": row["message_id"],
                "role": "assistant",
                "content": "",
                "tools": [],
                "segments": [],
                "model": req.get("model") or "",
            }],
            strict=True,
        )[0]
        updated = conn.execute(
            """UPDATE chat_runs SET status='queued', request=?, message=?, provider_messages=?,
               cancel_requested=0, updated_at=? WHERE id=? AND status='completed'""",
            (chat_tx.dumps(req), chat_tx.dumps(assistant), chat_tx.dumps(sealed), db.now(), run_id),
        )
        if updated.rowcount == 0:
            return False
        chat_tx.event(
            conn,
            row["user_id"],
            "agent.resumed",
            row["thread_id"],
            {
                "run_id": row["id"],
                "message_id": row["message_id"],
                "status": "queued",
                "child_id": row["id"],
                "parent_run_id": row["parent_run_id"],
                "reason": "followup",
            },
        )
        chat_tx.thread_event(conn, row["user_id"], row["thread_id"])
    return True


def flag_unconsumed_followups(run_id: str, followups: list) -> None:
    """Mark a terminal child with follow-ups it never consumed, so the wake
    envelope reports them (the parent may continue it with task_id)."""
    texts = [chat_views.followup_text(t) for t in (followups or []) if chat_views.followup_text(t)]
    if not texts:
        return
    from . import chats

    note = (
        "Unconsumed parent follow-up" + ("s" if len(texts) > 1 else "")
        + ":\n" + "\n\n".join(texts)
    )
    with chat_tx.transaction() as conn:
        row = conn.execute(
            "SELECT id,user_id,thread_id,message_id,message,request,status FROM chat_runs WHERE id=?",
            (run_id,),
        ).fetchone()
        if row is None or row["status"] not in chat_tx.TERMINAL:
            return
        try:
            message = json.loads(row["message"] or "{}")
        except (json.JSONDecodeError, TypeError):
            message = {}
        if not isinstance(message, dict):
            message = {}
        segments = message.get("segments") if isinstance(message.get("segments"), list) else []
        segments = [*segments, {"type": "content", "text": "\n\n" + note}]
        message["segments"] = segments
        message["content"] = ((str(message.get("content") or "").strip() + "\n\n" + note).strip())[: chats.ASSISTANT_CONTENT_MAX]
        try:
            req = json.loads(row["request"] or "{}")
        except (json.JSONDecodeError, TypeError):
            req = {}
        if not isinstance(req, dict):
            req = {}
        agent = req.get("agent") if isinstance(req.get("agent"), dict) else {}
        agent["background"] = True
        # Stopped/failed writeups ignore message content; keep the list where
        # child_writeup reports it for every terminal status.
        agent["unconsumed_followups"] = texts[:8]
        agent["pending_followups"] = []
        req["agent"] = agent
        conn.execute(
            "UPDATE chat_runs SET message=?,request=?,updated_at=? WHERE id=?",
            (chat_tx.dumps(message), chat_tx.dumps(req), db.now(), run_id),
        )
        chat_tx.event(
            conn,
            row["user_id"],
            "agent.progress",
            row["thread_id"],
            {"run_id": row["id"], "message_id": row["message_id"], "message": message},
        )


def settle_abandoned_followups(run_id: str) -> None:
    """Report follow-ups an abandoned child never consumed.

    Only the abandon paths call this (abort/reap/force-terminal): no live
    finalizer will run for them, so the queue would otherwise sit unread
    forever while the parent was told "delivered". child_writeup prints
    agent.unconsumed_followups, and forcing background/wake_sent hands that
    report to the wake sweep.
    """
    try:
        with chat_tx.transaction() as conn:
            _settle_child_followups(conn, run_id)
    except Exception:
        logger.exception("Failed to settle follow-ups for abandoned run %s", run_id)


def _settle_child_followups(conn, run_id: str) -> None:
    row = conn.execute(
        "SELECT id,parent_run_id,status,provider_messages,request FROM chat_runs WHERE id=?",
        (run_id,),
    ).fetchone()
    if row is None or not row["parent_run_id"] or row["status"] not in chat_tx.TERMINAL:
        return
    try:
        req = json.loads(row["request"] or "{}")
    except (json.JSONDecodeError, TypeError):
        return
    if not isinstance(req, dict):
        return
    agent = req.get("agent") if isinstance(req.get("agent"), dict) else {}
    pending = agent.get("pending_followups")
    if not isinstance(pending, list) or not pending:
        return
    transcript = row["provider_messages"] or ""
    texts = [
        chat_views.followup_text(item)
        for item in pending
        if chat_views.followup_text(item) and not chat_views._followup_delivered(item, transcript)
    ]
    reported = [str(t) for t in (agent.get("unconsumed_followups") or []) if str(t).strip()]
    for text in texts:
        if text not in reported:
            reported.append(text)
    agent["unconsumed_followups"] = reported[:8]
    agent["pending_followups"] = []
    agent["background"] = True
    agent["wake_sent"] = False
    req["agent"] = agent
    conn.execute(
        "UPDATE chat_runs SET request=?,updated_at=? WHERE id=?",
        (chat_tx.dumps(req), db.now(), run_id),
    )


STEER_FRAME_HEAD = "[Fuse] Message from the user, delivered while you were working"


def steer_envelope(steer_id: str, text: str) -> str:
    """Provider-visible framing for one mid-turn user steer (idempotent by id)."""
    return (
        f"{STEER_FRAME_HEAD} (steer:{steer_id}).\n\n"
        f"{text}\n\n"
        "This is the user's latest instruction: it may adjust, redirect, or end the "
        "current work. Weigh it against everything above and continue accordingly."
    )


def steer_message(item):
    from . import chats

    return {"role": "user", "content": chats.model_content(
        steer_envelope(item.get("id") or "", item.get("text") or ""), item.get("attachments") or []
    )}


def queue_steer(run_id: str, steer_id: str, text: str) -> bool:
    """Stage a user steer for the live run's next tool boundary.

    Per-id dedupe: a retried steer POST (same command_id/steer id) never stacks
    a second copy — neither against the pending list nor against an envelope
    already delivered into the provider transcript. Overflow is rejected so an
    instruction can never be silently dropped.
    """
    if not run_id or not steer_id or not str(text or "").strip():
        return False
    with chat_tx.transaction() as conn:
        row = conn.execute(
            "SELECT id,status,request,provider_messages,cancel_requested FROM chat_runs WHERE id=?", (run_id,)
        ).fetchone()
        if row is None or row["status"] not in chat_tx.ACTIVE or row["cancel_requested"]:
            return False
        try:
            req = json.loads(row["request"] or "{}")
        except (json.JSONDecodeError, TypeError):
            req = {}
        if not isinstance(req, dict):
            req = {}
        agent = req.get("agent") if isinstance(req.get("agent"), dict) else {}
        if agent.get("steer_closed"):
            return False
        pending = agent.get("pending_steers")
        if not isinstance(pending, list):
            pending = []
        if any(isinstance(item, dict) and item.get("id") == steer_id for item in pending):
            return True
        if chat_tx._json_needle(f"(steer:{steer_id})") in (row["provider_messages"] or ""):
            # Already delivered in an earlier round; idempotent no-op.
            return True
        from . import chats as chats_mod

        if len(pending) >= 8:
            raise chats_mod.ChatValidationError(
                "too many pending messages for this turn; wait for one to be delivered", 429
            )
        pending.append({"id": str(steer_id), "text": str(text)[: chats_mod.CONTENT_MAX]})
        agent["pending_steers"] = pending
        req["agent"] = agent
        conn.execute(
            "UPDATE chat_runs SET request=?,updated_at=? WHERE id=?",
            (chat_tx.dumps(req), db.now(), run_id),
        )
        return True


def take_steers(run_id: str) -> list[dict]:
    """Peek staged steers for injection at the next tool boundary.

    Non-destructive: entries are cleared by save_turn_progress in the same
    transaction that persists the injected envelope, closing the crash window
    between consumption and durable delivery.
    """
    if not run_id:
        return []
    row = db.fetchone("SELECT id,status,request,provider_messages FROM chat_runs WHERE id=?", (run_id,))
    if row is None or row["status"] not in chat_tx.ACTIVE:
        return []
    try:
        req = json.loads(row["request"] or "{}")
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(req, dict):
        return []
    agent = req.get("agent") if isinstance(req.get("agent"), dict) else {}
    pending = agent.get("pending_steers")
    if not isinstance(pending, list) or not pending:
        return []
    transcript = row["provider_messages"] or ""
    out = []
    for item in pending:
        if isinstance(item, dict) and (str(item.get("text") or "").strip() or item.get("attachments")):
            if chat_tx._json_needle(f"(steer:{item.get('id')})") not in transcript:
                out.append({"id": str(item.get("id") or ""), "text": str(item.get("text") or ""),
                            **({"attachments": item["attachments"]} if item.get("attachments") else {})})
    return out


def close_steer_window(run_id):
    """Close acceptance atomically with the final pending-message check."""
    with chat_tx.transaction() as conn:
        row = conn.execute("SELECT request,status FROM chat_runs WHERE id=?", (run_id,)).fetchone()
        if row is None or row["status"] not in chat_tx.ACTIVE:
            return True
        request = json.loads(row["request"] or "{}")
        agent = request.get("agent") if isinstance(request.get("agent"), dict) else {}
        if agent.get("pending_steers"):
            return False
        agent["steer_closed"] = True
        request["agent"] = agent
        conn.execute("UPDATE chat_runs SET request=? WHERE id=?", (chat_tx.dumps(request), run_id))
        return True


def steer_display_deliveries(run_id):
    """Unsealed deliveries, including recovery after persistence but before flush."""
    row = db.fetchone("SELECT request,provider_messages FROM chat_runs WHERE id=?", (run_id,))
    if row is None:
        return []
    request = json.loads(row["request"] or "{}")
    return [part["steer_id"] for part in (request.get("agent") or {}).get("display_parts") or []
            if part.get("awaiting_delivery") and part.get("steer_id")
            and chat_tx._json_needle(f"(steer:{part['steer_id']})") in (row["provider_messages"] or "")]


def seal_steer_display(run_id, steer_ids):
    """Called by the worker after flushing pre-delivery output, before new tokens."""
    with chat_tx.transaction() as conn:
        row = conn.execute("SELECT request,provider_messages,status FROM chat_runs WHERE id=?", (run_id,)).fetchone()
        if row is None or row["status"] not in chat_tx.ACTIVE:
            return
        request = json.loads(row["request"] or "{}")
        ids = set(steer_ids)
        for part in (request.get("agent") or {}).get("display_parts") or []:
            ident = part.get("steer_id")
            if ident in ids and chat_tx._json_needle(f"(steer:{ident})") in (row["provider_messages"] or ""):
                part["awaiting_delivery"] = False
        conn.execute("UPDATE chat_runs SET request=? WHERE id=?", (chat_tx.dumps(request), run_id))


def mark_followup_display(run_id, followup_ids, message):
    """Record where in the live leg delivered parent follow-ups landed.

    Called by the worker after flushing pre-delivery output, before new tokens."""
    if not followup_ids:
        return
    with chat_tx.transaction() as conn:
        row = conn.execute("SELECT request,status FROM chat_runs WHERE id=?", (run_id,)).fetchone()
        if row is None or row["status"] not in chat_tx.ACTIVE:
            return
        request = json.loads(row["request"] or "{}")
        agent = request.setdefault("agent", {})
        marked = {
            ident
            for part in agent.get("followup_marks") or []
            if isinstance(part, dict)
            for ident in part.get("ids") or []
        }
        ids = [str(item) for item in followup_ids if str(item) not in marked]
        if not ids:
            return
        agent.setdefault("followup_marks", []).append(
            {"ids": ids, "end": chat_views._display_mark(message)})
        conn.execute("UPDATE chat_runs SET request=? WHERE id=?", (chat_tx.dumps(request), run_id))


def followup_display_deliveries(run_id: str) -> list[str]:
    """Follow-up envelopes durable in provider_messages but not yet covered by
    a display mark — the recovery twin of steer_display_deliveries: a crash or
    a lost followup.delivered frame between envelope persistence and the mark
    write would otherwise leave that leg permanently in the old render order."""
    if not run_id:
        return []
    row = db.fetchone("SELECT request,provider_messages FROM chat_runs WHERE id=?", (run_id,))
    if row is None:
        return []
    try:
        req = json.loads(row["request"] or "{}")
    except (json.JSONDecodeError, TypeError):
        req = {}
    if not isinstance(req, dict):
        req = {}
    agent = req.get("agent") if isinstance(req.get("agent"), dict) else {}
    marked = {
        ident
        for part in agent.get("followup_marks") or []
        if isinstance(part, dict)
        for ident in part.get("ids") or []
    }
    seed = req.get("display_seed") if isinstance(req.get("display_seed"), list) else []
    # User rows already covered by the seed belong to earlier legs, whose
    # marks were cleared on requeue; only current-leg envelopes recover.
    skip_users = sum(1 for m in seed if isinstance(m, dict) and m.get("role") == "user")
    raw_pm = row["provider_messages"]
    transcript = raw_pm if isinstance(raw_pm, list) else []
    if isinstance(raw_pm, str) and raw_pm.strip():
        try:
            parsed = json.loads(raw_pm)
        except (json.JSONDecodeError, TypeError):
            parsed = None
        if isinstance(parsed, list):
            transcript = parsed
    ids: list[str] = []
    for item in transcript:
        if not isinstance(item, dict) or item.get("role") != "user":
            continue
        if skip_users:
            skip_users -= 1
            continue
        text = str(item.get("content") or "")
        if not text.lstrip().startswith(chat_views.FOLLOWUP_FRAME_HEAD):
            continue
        ids.extend(re.findall(r"\(followup:([0-9a-f]{1,32})\)", text))
    return [ident for ident in ids if ident not in marked]


def refund_spawn(parent_run_id: str) -> None:
    """Roll back spawns_used when a child row was created but its worker never
    started, so failed starts cannot burn the per-agent spawn cap."""
    if not parent_run_id:
        return
    with chat_tx.transaction() as conn:
        row = conn.execute("SELECT id,request FROM chat_runs WHERE id=?", (parent_run_id,)).fetchone()
        if row is None:
            return
        try:
            req = json.loads(row["request"] or "{}")
        except (json.JSONDecodeError, TypeError):
            return
        if not isinstance(req, dict):
            return
        agent = req.get("agent") if isinstance(req.get("agent"), dict) else {}
        used = int(agent.get("spawns_used") or 0)
        if used <= 0:
            return
        agent["spawns_used"] = used - 1
        req["agent"] = agent
        conn.execute("UPDATE chat_runs SET request=? WHERE id=?", (chat_tx.dumps(req), parent_run_id))


def mark_child_background(run_id: str) -> None:
    """Promote a foreground child to background after the parent await budget."""
    if not run_id:
        return
    with chat_tx.transaction() as conn:
        row = conn.execute("SELECT request FROM chat_runs WHERE id=?", (run_id,)).fetchone()
        if row is None:
            return
        try:
            req = json.loads(row["request"] or "{}")
        except (json.JSONDecodeError, TypeError):
            return
        if not isinstance(req, dict):
            return
        agent = req.get("agent") if isinstance(req.get("agent"), dict) else {}
        agent["background"] = True
        req["agent"] = agent
        conn.execute("UPDATE chat_runs SET request=?,updated_at=? WHERE id=?", (chat_tx.dumps(req), db.now(), run_id))


def mark_child_background_if_pending(run_id: str) -> bool:
    """Promote a terminal child to background only when it still has undelivered
    follow-ups, so the wake path reports them instead of dropping them."""
    if not run_id:
        return False
    with chat_tx.transaction() as conn:
        row = conn.execute("SELECT request FROM chat_runs WHERE id=?", (run_id,)).fetchone()
        if row is None:
            return False
        try:
            req = json.loads(row["request"] or "{}")
        except (json.JSONDecodeError, TypeError):
            return False
        if not isinstance(req, dict):
            return False
        agent = req.get("agent") if isinstance(req.get("agent"), dict) else {}
        pending = agent.get("pending_followups")
        if not isinstance(pending, list) or not any(chat_views.followup_text(t) for t in pending):
            return False
        agent["background"] = True
        req["agent"] = agent
        conn.execute("UPDATE chat_runs SET request=?,updated_at=? WHERE id=?", (chat_tx.dumps(req), db.now(), run_id))
        return True


def promote_run_binding(run_id: str, host_id=None, cwd: str = "") -> None:
    """Persist a mid-turn chat→project promotion onto the live run request.

    The session dict is updated in memory (chat_tools), but children spawn from
    the run ROW's agent (agents.spawn reads request.agent.bound_host_id/cwd);
    without this, subagents spawned after the promotion inherit stale bindings.
    """
    if not run_id:
        return
    with chat_tx.transaction() as conn:
        row = conn.execute(
            "SELECT id,status,request FROM chat_runs WHERE id=?", (run_id,)
        ).fetchone()
        if row is None or row["status"] not in chat_tx.ACTIVE:
            return
        try:
            req = json.loads(row["request"] or "{}")
        except (json.JSONDecodeError, TypeError):
            return
        if not isinstance(req, dict):
            return
        agent = req.get("agent") if isinstance(req.get("agent"), dict) else {}
        agent["coding"] = True
        agent["project_session"] = True
        if host_id is not None:
            agent["bound_host_id"] = host_id
        if cwd:
            agent["bound_cwd"] = cwd
        req["agent"] = agent
        conn.execute(
            "UPDATE chat_runs SET request=?,updated_at=? WHERE id=?",
            (chat_tx.dumps(req), db.now(), run_id),
        )


def purge_orphan_subagents(conn, user_id: int, thread_id: int, kept_messages):
    """Delete subagent runs whose spawn evidence was rewound/edited away.

    A child run survives only if a kept message still references it via a
    spawn trace, or if its parent chain reaches such a child. Root runs whose
    assistant message is gone go with their whole tree. Returns deleted ids.
    """
    kept_msg_ids = {
        str(m.get("id"))
        for m in (kept_messages or [])
        if isinstance(m, dict) and m.get("id")
    }
    kept_children = {
        str(t.get("child_id"))
        for m in (kept_messages or [])
        if isinstance(m, dict)
        for t in (m.get("tools") or [])
        if isinstance(t, dict) and t.get("name") == "spawn_subagent" and t.get("child_id")
    }
    rows = conn.execute(
        "SELECT id, parent_run_id, message_id FROM chat_runs WHERE user_id=? AND thread_id=?",
        (user_id, thread_id),
    ).fetchall()
    roots = [r for r in rows if not r["parent_run_id"]]
    children = [r for r in rows if r["parent_run_id"]]
    by_parent: dict[str, list] = {}
    for item in children:
        by_parent.setdefault(item["parent_run_id"], []).append(item)
    doomed = {r["id"] for r in roots if r["message_id"] not in kept_msg_ids}
    keep = {c for c in kept_children if any(r["id"] == c for r in children)}
    frontier = list(keep)
    while frontier:
        current = frontier.pop()
        for child in by_parent.get(current, []):
            if child["id"] not in keep:
                keep.add(child["id"])
                frontier.append(child["id"])
    doomed.update(r["id"] for r in children if r["id"] not in keep)
    if not doomed:
        return []
    # Defensive: never delete a run a worker may still own. Callers already
    # block message replacement while any thread run is active, but keep the
    # invariant local so future callers cannot rely on it.
    active = {
        r["id"]
        for r in conn.execute(
            f"SELECT id FROM chat_runs WHERE user_id=? AND id IN ({','.join('?' * len(doomed))}) AND status IN ('queued','running')",
            (user_id, *doomed),
        ).fetchall()
    }
    doomed -= active
    if not doomed:
        return []
    placeholders = ",".join("?" * len(doomed))
    conn.execute(
        f"DELETE FROM chat_runs WHERE user_id=? AND id IN ({placeholders})",
        (user_id, *doomed),
    )
    return sorted(doomed)


def resolve_child_run(user_id, token: str, *, thread_id=None, parent_run_id: str | None = None):
    """Resolve a subagent id from a uuid, prefix, description, or spawn tool-call id."""
    raw = str(token or "").strip()
    if not raw:
        return None
    if thread_id is None and parent_run_id:
        parent = chat_views.get_run(user_id, parent_run_id)
        if parent:
            thread_id = parent.get("thread_id")
    pk = thread_id
    if pk is not None:
        try:
            from . import chats

            resolved = chats.resolve_thread_pk(user_id, pk)
            if resolved is not None:
                pk = resolved
        except Exception:
            pass
    hit = chat_views.get_run(user_id, raw)
    if hit and hit.get("parent_run_id"):
        if pk is None or int(hit.get("thread_id") or 0) == int(pk):
            return hit
        return None
    if pk is None:
        return None
    rows = db.fetchall(
        """SELECT * FROM chat_runs
           WHERE user_id=? AND thread_id=? AND parent_run_id IS NOT NULL
           ORDER BY created_at DESC, id DESC LIMIT 24""",
        (user_id, pk),
    )
    needle = raw.lower()
    for row in rows:
        # Prefix ids must be uuid-like: a 1-2 character token would resolve to
        # whichever recent child happens to sort first.
        if len(raw) >= 8 and str(row["id"]).startswith(raw):
            return chat_views.run_view(row)
    # Description fallback must be an exact, unique match: substring matching
    # resolves vague tokens (kill/get_output) to an arbitrary recent child.
    exact = []
    for row in rows:
        try:
            req = json.loads(row["request"] or "{}")
        except (json.JSONDecodeError, TypeError):
            req = {}
        agent = req.get("agent") if isinstance(req, dict) and isinstance(req.get("agent"), dict) else {}
        desc = str(agent.get("description") or "").strip().lower()
        if desc and desc == needle:
            exact.append(row)
    if len(exact) == 1:
        return chat_views.run_view(exact[0])
    if parent_run_id:
        parent = chat_views.get_run(user_id, parent_run_id)
        tools = ((parent or {}).get("message") or {}).get("tools") if parent else None
        for tool in tools or []:
            if not isinstance(tool, dict) or tool.get("name") != "spawn_subagent":
                continue
            if str(tool.get("id") or "") != raw and str(tool.get("id") or "")[:8] != raw[:8]:
                continue
            cid = str(tool.get("child_id") or "")
            if cid:
                hit = chat_views.get_run(user_id, cid)
                if hit:
                    return hit
    return None


_child_admission_lock = threading.Lock()


def _same_host_id(a, b) -> bool:
    if a is None or b is None:
        return a is None and b is None
    try:
        return int(a) == int(b)
    except (TypeError, ValueError):
        return False


def _child_host(host_id, resume_host_id, parent_agent: dict):
    if host_id is not None:
        return host_id
    if resume_host_id is not None:
        return resume_host_id
    return parent_agent.get("bound_host_id")


def _child_cwd(cwd: str, host_id, resume_host_id, resume_cwd: str, parent_agent: dict) -> str:
    """A folder only means something on its own host: fall back to the resumed
    session's or the parent's cwd only when the child runs on that host."""
    if cwd:
        return cwd
    effective = _child_host(host_id, resume_host_id, parent_agent)
    if resume_cwd and _same_host_id(effective, resume_host_id):
        return resume_cwd
    if _same_host_id(effective, parent_agent.get("bound_host_id")):
        return str(parent_agent.get("bound_cwd") or "")
    return ""


def start_child_run(**kwargs):
    # Admission used to run without an await on the sole event loop. Preserve
    # that serialization when callers move the transaction to worker threads:
    # different parents must not both pass the tree/user/process count checks.
    with _child_admission_lock:
        return _start_child_run(**kwargs)


def _start_child_run(
    *,
    user_id: int,
    thread_id: int,
    parent_run_id: str,
    prompt: str,
    description: str,
    subagent_type: str = "",
    host_id=None,
    cwd: str = "",
    model: str = "",
    depth: int = 1,
    remaining_seconds: float = chat_run_state.DEFAULT_REMAINING_SECONDS,
    resume_from_id: str | None = None,
    permission_mode: str = "project",
    max_completions: int = 0,
    allow_nested_spawn: bool = False,
    background: bool = False,
    auto: bool = False,
    scheduled: bool = False,
):
    from . import chats
    from .agent_catalog import default_loaded_for_child, sanitize_loaded

    prompt = chats._text(prompt, "prompt", chats.CONTENT_MAX)
    description = chats._legacy_text(description, 200) or "subagent"
    kind = subagent_type if subagent_type in ("general-purpose", "explore", "plan") else "general-purpose"
    with chat_tx.transaction() as conn:
        pk = chats.resolve_thread_pk(user_id, thread_id, conn)
        if pk is None:
            raise SpawnCapError("chat not found")
        thread_id = pk
        parent = conn.execute(
            "SELECT * FROM chat_runs WHERE id=? AND user_id=?",
            (parent_run_id, user_id),
        ).fetchone()
        if parent is None or parent["status"] not in chat_tx.ACTIVE:
            raise SpawnCapError("parent run is not active")
        if parent["thread_id"] != thread_id or parent["cancel_requested"]:
            raise SpawnCapError("parent run is not active in this session")
        root = parent["root_run_id"] or parent["id"]
        effective_max = 3 if allow_nested_spawn else 1
        n_live_tree = conn.execute(
            """SELECT COUNT(*) FROM chat_runs
               WHERE root_run_id=? AND id != ? AND status IN ('queued','running')""",
            (root, root),
        ).fetchone()[0]
        if n_live_tree >= LIVE_TREE_CHILDREN:
            raise SpawnCapError(f"tree already has {LIVE_TREE_CHILDREN} live children.")
        n_live_user = conn.execute(
            "SELECT COUNT(*) FROM chat_runs WHERE user_id=? AND status IN ('queued','running')",
            (user_id,),
        ).fetchone()[0]
        if n_live_user >= LIVE_USER_RUNS:
            raise SpawnCapError(f"maximum of {LIVE_USER_RUNS} live agents for this user.")
        n_live_all = conn.execute(
            "SELECT COUNT(*) FROM chat_runs WHERE status IN ('queued','running')"
        ).fetchone()[0]
        if n_live_all >= LIVE_PROCESS_RUNS:
            raise SpawnCapError("agent generation capacity reached.")
        parent_depth = int(parent["agent_depth"] or 0)
        depth = parent_depth + 1
        if parent_depth >= effective_max:
            raise SpawnCapError("max depth reached.")
        if depth > effective_max:
            raise SpawnCapError("max depth reached.")
        if not allow_nested_spawn and parent_depth >= 1:
            raise SpawnCapError("nested spawn is off; only the parent may spawn.")
        req = json.loads(parent["request"])
        agent = req.get("agent") if isinstance(req.get("agent"), dict) else {}
        if not resume_from_id:
            used = int(agent.get("spawns_used") or 0)
            cap = PER_AGENT_PARENT if parent_depth == 0 else PER_AGENT_NESTED
            if used >= cap:
                raise SpawnCapError(f"this agent already spawned {cap} children.")
            agent["spawns_used"] = used + 1
        req["agent"] = agent
        conn.execute("UPDATE chat_runs SET request=? WHERE id=?", (chat_tx.dumps(req), parent["id"]))
        resume_messages = None
        resume_loaded: list[str] = []
        resume_host_id = None
        resume_cwd = ""
        resume_scratch: dict = {}
        retype = False
        if resume_from_id:
            source = conn.execute(
                "SELECT * FROM chat_runs WHERE id=? AND user_id=?",
                (resume_from_id, user_id),
            ).fetchone()
            if source is None or source["status"] not in chat_tx.TERMINAL:
                raise SpawnCapError("resume_from is not a completed child in this session.")
            if source["parent_run_id"] is None:
                raise SpawnCapError("resume_from must be a child run.")
            ancestor = conn.execute(
                """WITH RECURSIVE ancestors(id, parent_run_id) AS (
                       SELECT id, parent_run_id FROM chat_runs WHERE id=?
                       UNION
                       SELECT r.id, r.parent_run_id FROM chat_runs r JOIN ancestors a ON r.id=a.parent_run_id
                   ) SELECT 1 FROM ancestors WHERE id=?""",
                (parent_run_id, source["id"]),
            ).fetchone()
            if ancestor:
                raise SpawnCapError("resume_from cannot be an ancestor of the parent.")
            if (source["parent_run_id"] != parent_run_id or source["root_run_id"] != root
                    or int(source["agent_depth"] or 0) != depth):
                live_descendant = conn.execute(
                    """WITH RECURSIVE descendants(id) AS (
                           SELECT id FROM chat_runs WHERE parent_run_id=?
                           UNION
                           SELECT r.id FROM chat_runs r JOIN descendants d ON r.parent_run_id=d.id
                       ) SELECT 1 FROM chat_runs WHERE id IN (SELECT id FROM descendants)
                         AND status IN ('queued','running') LIMIT 1""",
                    (source["id"],),
                ).fetchone()
                if live_descendant:
                    raise SpawnCapError("Cannot reparent a subagent while its descendants are active.")
            if int(source["thread_id"] or 0) != int(thread_id):
                raise SpawnCapError("resume_from is not a completed child in this session.")
            raw_pm = source["provider_messages"]
            if not raw_pm:
                try:
                    src_req = json.loads(source["request"] or "{}")
                except (json.JSONDecodeError, TypeError):
                    src_req = {}
                seed_msgs = src_req.get("messages") if isinstance(src_req, dict) else None
                if isinstance(seed_msgs, list) and seed_msgs:
                    raw_pm = json.dumps(seed_msgs)
            if not raw_pm:
                raise SpawnCapError("resume_from has no stored transcript.")
            try:
                resume_messages = json.loads(raw_pm)
            except (json.JSONDecodeError, TypeError):
                raise SpawnCapError("resume_from has no stored transcript.") from None
            if not isinstance(resume_messages, list) or not resume_messages:
                raise SpawnCapError("resume_from has no stored transcript.")
            resume_messages = [m for m in resume_messages if isinstance(m, dict)]
            # Parity with restart recovery: an unanswered tool call from the
            # prior life must be sealed before the continuation runs.
            resume_messages = chat_run_state.seal_provider_messages(resume_messages)
            try:
                src_req = json.loads(source["request"] or "{}")
            except (json.JSONDecodeError, TypeError):
                src_req = {}
            src_agent = src_req.get("agent") if isinstance(src_req, dict) and isinstance(src_req.get("agent"), dict) else {}
            src_kind = str(src_agent.get("subagent_type") or "")
            parent_kind = str(agent.get("subagent_type") or "")
            if parent_kind in ("explore", "plan") and src_kind != parent_kind:
                raise SpawnCapError("Restricted subagents cannot resume an agent of a different type.")
            if parent_kind not in ("explore", "plan") and subagent_type in ("general-purpose", "explore", "plan"):
                # An unrestricted parent may re-type the session on resume
                # (e.g. promote its explore auditor to apply the fixes).
                kind = subagent_type
            elif src_kind in ("general-purpose", "explore", "plan"):
                kind = src_kind
            retype = bool(resume_from_id) and kind != src_kind
            raw_loaded = source["loaded_tools"] if "loaded_tools" in source.keys() else None
            if raw_loaded:
                try:
                    resume_loaded = json.loads(raw_loaded)
                except (json.JSONDecodeError, TypeError):
                    resume_loaded = []
            if not resume_loaded:
                resume_loaded = src_agent.get("loaded_tools") if isinstance(src_agent.get("loaded_tools"), list) else []
            if src_agent.get("bound_host_id") is not None:
                resume_host_id = src_agent.get("bound_host_id")
            resume_cwd = str(src_agent.get("bound_cwd") or "")
            resume_scratch = _scratch_of(src_agent)
        if resume_messages:
            # OpenCode task_id: continue the prior transcript AND see the new prompt.
            seed = [*resume_messages, {"role": "user", "content": prompt}]
        else:
            seed = [{"role": "user", "content": prompt}]
        if retype:
            # The persisted toolset belongs to the old kind; seed the new kind's
            # defaults instead of carrying a restricted (or unrestricted) set over.
            loaded = default_loaded_for_child(kind)
        else:
            loaded = sanitize_loaded(resume_loaded) if resume_loaded else default_loaded_for_child(kind)
        parent_agent = agent
        ts = db.now()
        if resume_from_id and resume_messages:
            # Same session id (OpenCode). Do not mint a new descendant row.
            child_id = str(resume_from_id)
            source_row = conn.execute("SELECT * FROM chat_runs WHERE id=?", (child_id,)).fetchone()
            message_id = source_row["message_id"] if source_row else str(uuid4())
            child_model = (model or req.get("model") or "")
            assistant = chats.parse_messages(
                [{"id": message_id, "role": "assistant", "content": "", "tools": [], "segments": [], "model": child_model}],
                strict=True,
            )[0]
            child_request = {
                "model": model or req.get("model") or "",
                "run_id": child_id,
                "messages": seed,
                "display_seed": seed,
                "tools": list(req.get("tools") or []),
                "thread_id": thread_id,
                "thread_context": req.get("thread_context") or "",
                "instructions": req.get("instructions") or "",
                "profile": req.get("profile") or "",
                "prompt_snapshot": src_req.get("prompt_snapshot") if not retype else None,
                "agent": {
                    "coding": True,
                    "project_session": True,
                    "depth": depth,
                    "parent_run_id": parent_run_id,
                    "root_run_id": root,
                    "run_id": child_id,
                    "bound_host_id": _child_host(host_id, resume_host_id, parent_agent),
                    "bound_cwd": _child_cwd(cwd, host_id, resume_host_id, resume_cwd, parent_agent),
                    "permission_mode": permission_mode,
                    "allow_nested_spawn": allow_nested_spawn,
                    "loaded_tools": loaded,
                    "remaining_seconds": max(30, float(remaining_seconds)),
                    "max_completions": max_completions,
                    "spawns_used": 0,
                    "skip_sanitize": True,
                    "subagent_type": kind,
                    "description": description,
                    "background": bool(background),
                    "wake_sent": False,
                    "wake_attempts": 0,
                    "delivery_generation": uuid4().hex,
                    "auto": bool(auto),
                    "scheduled": bool(scheduled),
                    # The fresh message restarts elapsedMs; earlier legs'
                    # active time carries over (idle time between them never).
                    "work_ms_before": chat_run_state.row_work_ms(source_row),
                    **({"scratch": resume_scratch} if resume_scratch else {}),
                },
            }
            # created_at is kept so the session keeps its identity and list
            # ordering; the runtime budget is per-leg (worker timeout + stall
            # reaper), never derived from it. A deliberate parent-driven
            # continuation is a fresh leg for crash recovery, so the
            # auto-resume budget resets.
            conn.execute(
                """UPDATE chat_runs SET status='queued', request=?, message=?, cancel_requested=0,
                   provider_messages=?, loaded_tools=?, parent_run_id=?, root_run_id=?, agent_depth=?,
                   resume_count=0, updated_at=? WHERE id=? AND user_id=?""",
                (
                    chat_tx.dumps(child_request),
                    chat_tx.dumps(assistant),
                    chat_tx.dumps(seed),
                    chat_tx.dumps(loaded),
                    parent_run_id,
                    root,
                    depth,
                    ts,
                    child_id,
                    user_id,
                ),
            )
            chat_tx.event(
                conn,
                user_id,
                "agent.started",
                thread_id,
                {
                    "run_id": child_id,
                    "message_id": message_id,
                    "parent_run_id": parent_run_id,
                    "root_run_id": root,
                    "status": "queued",
                    "description": description,
                    "subagent_type": kind,
                    "resumed": True,
                    "child_id": child_id,
                },
            )
            chat_tx.thread_event(conn, user_id, thread_id)
            return chat_views.run_view(conn.execute("SELECT * FROM chat_runs WHERE id=?", (child_id,)).fetchone())
        child_id = str(uuid4())
        message_id = str(uuid4())
        assistant = chats.parse_messages(
            [{"id": message_id, "role": "assistant", "content": "", "tools": [], "segments": [],
              "model": (model or req.get("model") or "")}],
            strict=True,
        )[0]
        child_request = {
            "model": model or req.get("model") or "",
            "run_id": child_id,
            "messages": seed,
            "display_seed": seed,
            "tools": list(req.get("tools") or []),
            "thread_id": thread_id,
            "thread_context": req.get("thread_context") or "",
            "instructions": req.get("instructions") or "",
            "profile": req.get("profile") or "",
            "agent": {
                "coding": True,
                "project_session": True,
                "depth": depth,
                "parent_run_id": parent_run_id,
                "root_run_id": root,
                "run_id": child_id,
                "bound_host_id": _child_host(host_id, None, parent_agent),
                "bound_cwd": _child_cwd(cwd, host_id, None, "", parent_agent),
                "permission_mode": permission_mode,
                "allow_nested_spawn": allow_nested_spawn,
                "loaded_tools": loaded,
                "remaining_seconds": max(30, float(remaining_seconds)),
                "max_completions": max_completions,
                "spawns_used": 0,
                "skip_sanitize": bool(resume_messages),
                "subagent_type": kind,
                "description": description,
                "background": bool(background),
                "auto": bool(auto),
                "scheduled": bool(scheduled),
            },
        }
        conn.execute(
            """INSERT INTO chat_runs(
                   id, thread_id, user_id, status, request, message_id, message,
                   created_at, updated_at, parent_run_id, root_run_id, agent_depth, loaded_tools)
               VALUES (?,?,?,'queued',?,?,?,?,?,?,?,?,?)""",
            (
                child_id,
                thread_id,
                user_id,
                chat_tx.dumps(child_request),
                message_id,
                chat_tx.dumps(assistant),
                ts,
                ts,
                parent_run_id,
                root,
                depth,
                chat_tx.dumps(loaded),
            ),
        )
        chat_tx.event(
            conn,
            user_id,
            "agent.started",
            thread_id,
            {
                    "run_id": child_id,
                    "message_id": message_id,
                    "parent_run_id": parent_run_id,
                    "root_run_id": root,
                    "status": "queued",
                    "description": description,
                    "subagent_type": kind,
                    "child_id": child_id,
                },
        )
        chat_tx.thread_event(conn, user_id, thread_id)
        return chat_views.run_view(conn.execute("SELECT * FROM chat_runs WHERE id=?", (child_id,)).fetchone())
