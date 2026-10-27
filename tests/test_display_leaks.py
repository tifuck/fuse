"""Nothing the user sees carries text that only the model is meant to read.

Fuse adds text to provider messages that is not part of the conversation:
the per-turn session tail, compaction preambles and acks, steer envelopes and
receipts, web context, pinned assignments, recovered-trace notices. Any view
that derives rows from a provider transcript must leave it out. Tests built on
hand-written transcripts never contain that text, so this module drives real
turns through the real worker (only the model call and tool execution are
faked) and sweeps every row the client receives: the idle thread, steers, a
live run, compactions at the start of a turn and between tool rounds, an Edit
and a wake turn after compaction, and a subagent panel.
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app import chat_runs as runs, chat_runtime as runtime, chat_store as store, chats, context_meter, db
from app.chat_children import STEER_FRAME_HEAD
from app.chat_generation.messages import SESSION_TAIL_MARK
from app.chat_prompts import WEB_CONTEXT_DELIM
from app.context_compact import COMPACT_ACK, PIN_HEADER
from app.web.chat import _chat_public_thread

pytestmark = pytest.mark.usefixtures("isolated_db")

SERVER_ONLY = {
    "session tail": SESSION_TAIL_MARK.strip(),
    "compaction preamble": "[Compacted conversation]",
    "compaction ack": COMPACT_ACK,
    "steer envelope": STEER_FRAME_HEAD,
    "steer receipt": "(steer:",
    "follow-up receipt": "(followup:",
    "web context": WEB_CONTEXT_DELIM.strip().splitlines()[1],
    "pinned assignment": PIN_HEADER.strip(),
    "recovered trace": "[Recovered historical tool trace",
    "historical tool data": "[Historical tool ",
}
SEARCH_CALL = {"id": "c1", "type": "function", "function": {"name": "web_search", "arguments": '{"query":"x"}'}}


def rows_in(value, where="view"):
    """Every transcript-shaped row anywhere in a payload, with its path."""
    if isinstance(value, dict):
        if value.get("role") in ("user", "assistant", "marker"):
            yield where, value
        for key, item in value.items():
            yield from rows_in(item, f"{where}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from rows_in(item, f"{where}[{index}]")


def shown_text(row) -> list[str]:
    texts = [row.get("content"), row.get("reasoning")]
    texts += [part.get("text") for part in row.get("segments") or [] if isinstance(part, dict)
              and part.get("type") in ("content", "reasoning")]
    return [text for text in texts if isinstance(text, str)]


def problems(payload, label):
    found = []
    for where, row in rows_in(payload):
        for text in shown_text(row):
            for name, needle in SERVER_ONLY.items():
                if needle in text:
                    found.append(f"{label}: {name} in {where} ({row.get('role')}): {text[:120]!r}")
    # The compacted view is sliced from the stored rows; a cut that names no
    # stored row would silently show the whole transcript.
    cut = payload.get("context_cut") if isinstance(payload, dict) else None
    if cut and cut.get("id") not in {msg.get("id") for msg in payload.get("messages") or []}:
        found.append(f"{label}: context_cut names no stored row: {cut}")
    return found


def result(message, finish="stop"):
    return SimpleNamespace(status=200, body={"choices": [{"message": message, "finish_reason": finish}]},
                           usage={"prompt_tokens": 100, "completion_tokens": 5}, stream=None, credential_id=1)


async def fake_tools(calls, **_kw):
    yield {"event": "tool", "id": "ts_0_0", "name": "web_search", "status": "running"}
    yield {"event": "tool", "id": "ts_0_0", "name": "web_search", "status": "done"}
    yield [runtime.ToolJob(call_id=call["id"], name="web_search", arguments=call["function"]["arguments"], text="hits",
                           trace={"name": "web_search", "status": "done"}, orig_index=i, event_id="ts_0_0")
           for i, call in enumerate(calls)]


def run_worker(run_row, on_call=None, tool_calls=0):
    """Drive one run through the real worker. The first `tool_calls` model
    calls ask for a web search; the rest answer."""
    calls = []

    async def invoke(request, user, payload, **_kw):
        calls.append(payload)
        if on_call:
            on_call(len(calls), payload)
        if len(calls) <= tool_calls and payload.get("tools"):
            reply = result({"content": "", "tool_calls": [SEARCH_CALL]}, "tool_calls")
        else:
            reply = result({"content": f"Answer {len(calls)}"})
        return user, SimpleNamespace(type_id="llama", source_id=1), "offline", "offline", reply

    async def go():
        with patch.object(runtime.api, "invoke_chat", invoke), patch.object(runtime.api, "_safe_record_usage"), \
             patch.object(runtime, "_run_tools", fake_tools), \
             patch.object(runs, "start", AsyncMock()), patch.object(runs, "schedule_wake"), \
             patch("app.push.notify_run_done", new=AsyncMock()):
            await runs._run(runs.internal_request(1), {"id": 1}, run_row)

    asyncio.run(go())
    return calls


def view(thread_id):
    return _chat_public_thread(store.thread_view(db.connect(), 1, chats.resolve_thread_pk(1, thread_id)))


def turn(thread_id, text, on_call=None, **kw):
    thread = chats.get_thread(1, thread_id)
    worker = {key: kw.pop(key) for key in ("tool_calls",) if key in kw}
    run = store.start_turn(1, thread_id, text, thread["revision"], "cmd-" + text, **kw)["run"]
    return run, run_worker(run, on_call, **worker)


def provider_rows(run_id):
    return json.loads(db.fetchone("SELECT provider_messages FROM chat_runs WHERE id=?", (run_id,))["provider_messages"] or "[]")


def compacting(armed=lambda: True):
    """Auto-compaction fires once, at the first check after `armed()` holds."""
    fired = []

    def should_auto_compact(*_a, **_k):
        if fired or not armed():
            return False
        fired.append(True)
        return True

    return (patch.object(context_meter, "should_auto_compact", should_auto_compact),
            patch.object(context_meter, "should_compact", lambda *a, **k: True),
            patch.object(runtime, "summarize_conversation", AsyncMock(return_value="<summary>Earlier work.</summary>")))


def shown(payload):
    """The rows the chat page shows (static/chat/transcript/paint.js viewMessages)."""
    rows, cut = payload["messages"], payload.get("context_cut")
    at = next((i for i, msg in enumerate(rows) if cut and msg["id"] == cut["id"]), None)
    if at is None:
        return rows
    rows = rows[at if cut["include"] else at + 1:]
    return [{"role": "marker", "summary": cut["summary"]}, *rows] if cut["summary"] else rows


def opens_with_handoff(rows):
    first = rows[0] if rows else {}
    return first.get("role") == "marker" or any(
        part.get("kind") == "compaction" and part.get("summary") for part in first.get("segments") or [])


def checkpoints(thread_id):
    return len(view(thread_id)["compaction"]["checkpoints"])


def test_views_never_show_server_added_text():
    db.execute("INSERT INTO users(id,email,password_hash,created_at) VALUES(1,'leaks@test.invalid','unused',?)", (db.now(),))
    thread_id = chats.create_thread(1, model="offline")["id"]
    found = []

    for text in ("one", "two", "three", "four"):
        run, _calls = turn(thread_id, text)
    # The sweep only means something if the model really got the server text.
    assert SERVER_ONLY["session tail"] in json.dumps(provider_rows(run["id"]), ensure_ascii=False)
    found += problems(view(thread_id), "idle thread")

    # A message sent while the reply runs is delivered inside the run.
    def steer(n, _payload):
        if n == 1:
            store.steer_turn(1, thread_id, "also check this", "steer-1")
    run, _calls = turn(thread_id, "five", steer)
    assert "(steer:" in json.dumps(provider_rows(run["id"]))
    found += problems(view(thread_id), "after a steer")

    # Auto-compaction at the start of a turn, as on a full context window;
    # the live view is taken while that run is still writing its reply.
    live = []
    first, second, third = compacting()
    with first, second, third:
        run, _calls = turn(thread_id, "six", lambda n, _p: live.append(view(thread_id)))
    assert checkpoints(thread_id) == 1, "the scenario must really compact"
    assert live and any(row.get("role") == "user" for _where, row in rows_in(live[0]))
    found += problems(live[0], "live run after compaction")
    found += problems(view(thread_id), "compacted thread")

    run, _calls = turn(thread_id, "seven")
    found += problems(view(thread_id), "turn after compaction")
    rows = shown(view(thread_id))
    assert opens_with_handoff(rows), rows[:1]
    assert [msg["content"] for msg in rows if msg["role"] == "user"] == ["seven"]

    # Compaction between two tool rounds of one reply (a long agent turn).
    searched = []
    live = []
    first, second, third = compacting(armed=lambda: bool(searched))
    with first, second, third:
        run, calls = turn(thread_id, "search", lambda n, p: (searched.append(n), live.append(view(thread_id))), tool_calls=1)
    assert calls[0].get("tools"), "the scenario needs a tool round"
    assert checkpoints(thread_id) == 2, "the tool round must really compact"
    found += problems(live[-1], "live run after a mid-reply compaction")
    found += problems(view(thread_id), "after a mid-reply compaction")
    assert opens_with_handoff(shown(live[-1])) and opens_with_handoff(shown(view(thread_id)))

    # Edit the newest message after compaction, then a wake turn.
    messages = view(thread_id)["messages"]
    last_user = max(i for i, msg in enumerate(messages) if msg["role"] == "user")
    run, _calls = turn(thread_id, "search again", replace_from=last_user)
    found += problems(view(thread_id), "edit after compaction")
    run, _calls = turn(thread_id, "[Fuse] AUTOMATED EVENT — not written by the user. Background results:", auto=True)
    found += problems(view(thread_id), "wake turn after compaction")

    # A subagent's panel is built from its own provider transcript.
    thread = chats.get_thread(1, thread_id)
    parent = store.start_turn(1, thread_id, "eight", thread["revision"], "cmd-eight")["run"]
    child = store.start_child_run(user_id=1, thread_id=thread_id, parent_run_id=parent["id"],
                                  prompt="inspect src", description="explore")
    # The parent sends the running child a follow-up; the panel shows it while
    # queued and after the child received it inside its envelope.
    pending = []

    def follow_up(n, _payload):
        if n == 1:
            assert store.queue_followup(child["id"], "also check the tests")
            pending.append(store.get_run_snapshot(1, child["id"])["display_messages"])
    run_worker(store.get_run(1, child["id"]), follow_up)
    child_rows = json.dumps(provider_rows(child["id"]), ensure_ascii=False)
    assert SERVER_ONLY["session tail"] in child_rows and "(followup:" in child_rows
    assert any(row.get("pending") for row in pending[0]), pending[0]
    found += problems(pending[0], "subagent panel, follow-up queued")
    panel = store.get_run_snapshot(1, child["id"])["display_messages"]
    found += problems(panel, "subagent panel")
    assert [row["content"] for row in panel if row.get("fromParent")] == ["also check the tests"]

    assert not found, "server-added text reached the client:\n" + "\n".join(found)
