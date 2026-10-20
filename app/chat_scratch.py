"""Per-run scratch pad: named string slots a run (and its subagents) can reuse."""
from __future__ import annotations

import json
import re

from . import db, chat_tx


# Named string slots for one run. Spawn prompts live here so a failed fan-out
# can retry by id instead of re-emitting every prompt in the next tool call.
# A slot must fit one scratch read: stay under chat_runtime.TOOL_RESULT_CHARS,
# which would otherwise clip the text the model reads back.
SCRATCH_MAX_SLOTS = 32


SCRATCH_VALUE_MAX = 20_000


def _stored_request(raw) -> dict | None:
    """A run's stored JSON object (request or message), or None when it cannot
    be parsed. Callers then refuse to write it back: a {} stand-in erased the
    live run's model, messages and tools."""
    try:
        value = json.loads(raw or "{}")
    except (json.JSONDecodeError, TypeError, RecursionError):
        return None
    return value if isinstance(value, dict) else None


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
        req = _stored_request(row["request"])
        if req is None:
            raise ScratchError("this session's state is unreadable")
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
                if hits == 1 and pad[name].find(find, pad[name].find(find) + 1) >= 0:
                    hits = 2
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
