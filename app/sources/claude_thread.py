"""Anthropic message threads: send only the new turn of a conversation.

The first request of a conversation creates a thread with the full history;
later requests send the messages after the previous reply plus that reply's
id. Billing and rate limits are unchanged; requests get smaller and time to
first token stays flat as the conversation grows.

Fuse stays the source of truth: every request is built in full first, and it
continues the thread only when that full request starts with exactly what the
thread holds (same account, same create-only and fixed fields, same history,
the reply Anthropic sent). Anything else creates a new thread, and a thread
Anthropic can no longer continue is replayed in full. State is in-process:
a restart costs one full request per conversation.
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass

BETA = "message-threads-2026-08-12"
# Anthropic expires a thread 5 minutes after its last stored response; past
# that a continue is a guaranteed 404, so go straight to a full create.
IDLE_S = 285
# A thread-related refusal on a create (feature off for this org, beta not
# accepted) turns threads off for that account for a while.
OFF_S = 3600
# Errors on a continue that a full create recovers from.
_REPLAY_CODES = frozenset({"thread_not_found", "thread_already_continued", "thread_continue_in_flight", "thread_fingerprint_mismatch"})
# Fields that must match the create request (system/tools are create-only).
_FIXED = ("system", "tools", "model", "tool_choice", "thinking", "output_config")
# Stops after which Anthropic does not store the turn (or must not continue).
_NO_STORE = frozenset({"refusal", "max_tokens", "model_context_window_exceeded", "pause_turn"})


@dataclass
class _State:
    cred_id: int
    message_id: str
    count: int
    digest: str
    fingerprint: str
    reply: tuple
    at: float


_threads: dict[str, _State] = {}
_off: dict[int, float] = {}


def reset() -> None:
    """Drop all thread state. Tests only."""
    _threads.clear()
    _off.clear()


def _canon(value):
    if isinstance(value, dict):
        return {k: _canon(v) for k, v in value.items() if k != "cache_control"}
    if isinstance(value, list):
        return [_canon(v) for v in value]
    return value


def _digest(value) -> str:
    raw = json.dumps(_canon(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()


def _fingerprint(payload: dict) -> str:
    return _digest({key: payload.get(key) for key in _FIXED})


def _reply_key(text: str, tool_ids: list[str]) -> tuple:
    # Tool calls identify a reply exactly; a text-only reply by its text.
    return ("tools", tuple(tool_ids)) if tool_ids else ("text", text.strip())


def _message_key(message: dict) -> tuple | None:
    if message.get("role") != "assistant" or not isinstance(message.get("content"), list):
        return None
    blocks = [b for b in message["content"] if isinstance(b, dict)]
    text = "\n".join(str(b.get("text") or "") for b in blocks if b.get("type") == "text")
    return _reply_key(text, [str(b.get("id") or "") for b in blocks if b.get("type") == "tool_use"])


def enabled(cred_id: int) -> bool:
    until = _off.get(cred_id)
    return until is None or until <= time.monotonic()


def plan(session_id: str | None, cred_id: int, payload: dict) -> tuple[dict, str | None]:
    """The body to send and its mode: "continue", "create", or None (no thread).

    Consumes the session's state: a response id can be continued once, and a
    failed request must not be continued from.
    """
    if not session_id or not enabled(cred_id):
        _threads.pop(session_id or "", None)
        return payload, None
    state = _threads.pop(session_id, None)
    body = continue_body(state, cred_id, payload) if state else None
    if body is not None:
        return body, "continue"
    return create_body(payload), "create"


def continue_body(state: _State, cred_id: int, payload: dict) -> dict | None:
    messages = payload.get("messages") or []
    n = state.count
    if (state.cred_id != cred_id or time.monotonic() - state.at > IDLE_S or len(messages) < n + 2
            or _message_key(messages[n]) != state.reply or messages[n + 1].get("role") != "user"
            or _fingerprint(payload) != state.fingerprint or _digest(messages[:n]) != state.digest):
        return None
    body = {k: v for k, v in payload.items() if k not in ("system", "tools")}
    # Earlier cache breakpoints are already stored with the thread; only the
    # new messages carry theirs (system + tools + these stay within 4).
    body["messages"] = messages[n + 1:]
    body["thread"] = {"type": "continue", "previous_message_id": state.message_id}
    return body


def create_body(payload: dict) -> dict:
    return {**payload, "thread": {"type": "create"}}


def record(session_id: str | None, cred_id: int, payload: dict, mode: str | None, reply: dict) -> None:
    """Remember the thread after a complete response."""
    message_id = str(reply.get("id") or "")
    if not session_id or not mode or not message_id or reply.get("stop_reason") in _NO_STORE:
        return
    messages = payload.get("messages") or []
    _threads[session_id] = _State(
        cred_id=cred_id,
        message_id=message_id,
        count=len(messages),
        digest=_digest(messages),
        fingerprint=_fingerprint(payload),
        reply=_reply_key(str(reply.get("text") or ""), list(reply.get("tool_ids") or [])),
        at=time.monotonic(),
    )


def _error(raw: str) -> tuple[str, str]:
    try:
        err = json.loads(raw).get("error")
    except (json.JSONDecodeError, AttributeError):
        return "", ""
    if not isinstance(err, dict):
        return "", ""
    details = err.get("details") if isinstance(err.get("details"), dict) else {}
    return str(details.get("error_code") or ""), str(err.get("message") or "").lower()


def fallback(mode: str, status: int, raw: str, cred_id: int) -> str | None | bool:
    """What to resend after a 400/404 on a threaded request.

    "create" replays the full conversation into a new thread, None resends it
    without a thread, False means the error is not about the thread.
    """
    code, message = _error(raw)
    if code == "thread_unsupported_request" or (mode == "create" and status == 400 and not code
                                                and ("thread" in message or BETA in message)):
        _off[cred_id] = time.monotonic() + OFF_S
        return None
    if mode != "continue":
        return False
    if code in _REPLAY_CODES or (status == 404 and not code) or (status == 400 and not code and "thread" in message):
        return "create"
    return False
