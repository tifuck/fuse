"""Manual compaction replays durable history, not settled display previews."""
import copy
import json
import sqlite3
from contextlib import nullcontext

import pytest

from app import chat_history, chat_store, chats, context


@pytest.fixture
def history(monkeypatch):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE chat_threads (id INTEGER, user_id INTEGER, retained_history TEXT);
        CREATE TABLE chat_runs (
            thread_id INTEGER, user_id INTEGER, parent_run_id TEXT, status TEXT,
            message_id TEXT, message TEXT, request TEXT, provider_messages TEXT,
            created_at INTEGER
        );
    """)
    raw = chats.parse_messages([
        {"id": "question", "role": "user", "content": "inspect the output"},
        {"id": "answer", "role": "assistant", "content": "done", "tools": [
            {"id": "trace", "name": "run_terminal_command", "status": "running",
             "command": "printf example", "preview": "Still running"}
        ]},
    ])
    full_output = "CANONICAL_OUTPUT_" + "x" * 100000 + "_TAIL"
    provider = [
        {"role": "user", "content": "inspect the output"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "original-call", "type": "function", "function": {
                "name": "run_terminal_command", "arguments": '{"command":"printf example"}'}}
        ]},
        {"role": "tool", "tool_call_id": "original-call", "content": full_output},
        {"role": "assistant", "content": "done"},
    ]
    state = context.parse_state({})
    conn.execute("INSERT INTO chat_threads VALUES (1, 1, ?)",
                 (chat_history.envelope(raw, provider, state),))
    conn.execute("INSERT INTO chat_runs VALUES (1, 1, NULL, 'completed', ?, ?, '{}', ?, 1)",
                 (raw[-1]["id"], json.dumps(raw[-1]), json.dumps(provider)))
    row = {"revision": 7, "messages": json.dumps(raw), "compaction": "{}"}
    view = {"messages": copy.deepcopy(raw), "revision": 7}
    view["messages"][-1]["tools"][0].update(status="done", preview="Short settled preview")
    monkeypatch.setattr(chat_store, "transaction", lambda: nullcontext(conn))
    monkeypatch.setattr(chat_store, "command", lambda *args: ("fingerprint", None))
    monkeypatch.setattr(chat_store, "_compaction_row", lambda *args: (1, row))
    monkeypatch.setattr(chat_store, "thread_view", lambda *args: view)
    yield conn, row, view, raw, provider
    conn.close()


@pytest.mark.parametrize("legacy", [False, True])
def test_prepare_compact_preserves_canonical_exchange(history, legacy):
    conn, row, view, raw, provider = history
    if legacy:
        conn.execute("UPDATE chat_threads SET retained_history='{}'")
    assert raw[-1]["tools"][0]["status"] == "running"
    assert view["messages"][-1]["tools"][0]["status"] == "done"
    prepared = chat_store.prepare_compact(1, 1, 7, "compact")
    assert prepared["history"] == provider
    assert prepared["history"][2]["tool_call_id"] == "original-call"
    assert prepared["history_info"]["trace_fallbacks"] == 0
    assert prepared["history_info"]["recovered_turns"] == int(legacy)
    assert prepared["thread"] is view
    assert prepared["thread"]["messages"][-1]["tools"][0]["preview"] == "Short settled preview"
    assert json.loads(row["messages"]) == raw


@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("edit", ["delete", "redact"])
def test_prepare_compact_does_not_restore_deleted_or_redacted_trace(history, legacy, edit):
    conn, row, view, raw, provider = history
    if legacy:
        conn.execute("UPDATE chat_threads SET retained_history='{}'")
    edited = copy.deepcopy(raw)
    if edit == "delete":
        edited[-1]["tools"] = []
    else:
        edited[-1]["tools"][0]["preview"] = "[redacted]"
    row["messages"] = json.dumps(edited)
    view["messages"] = copy.deepcopy(edited)
    prepared = chat_store.prepare_compact(1, 1, 7, "compact")
    assert "CANONICAL_OUTPUT_" not in json.dumps(prepared["history"])
    assert "original-call" not in json.dumps(prepared["history"])
    assert prepared["history_info"]["recovered_turns"] == 0
    assert prepared["history_info"]["trace_fallbacks"] == int(edit == "redact")
    if edit == "redact":
        assert "[redacted]" in json.dumps(prepared["history"])
    else:
        assert not any(message["role"] == "tool" for message in prepared["history"])
