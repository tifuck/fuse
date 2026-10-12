"""Offline regressions for current capabilities and final-round replay."""
import asyncio
import copy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app import chat_runs, chat_runtime as runtime, chat_store as store, chats, db, hosts
from tests.mariadb_fixture import isolated_database


@pytest.fixture
def thread():
    with isolated_database():
        store.invalidate_committed_cursors()
        db.execute("INSERT INTO users(id,email,password_hash,created_at) VALUES(1,'confirmed@test.invalid','unused',?)", (db.now(),))
        yield chats.create_thread(1, model="glm-offline")
        store.invalidate_committed_cursors()


def result(content="", *, reasoning="", calls=None):
    message = {"content": content, "reasoning_content": reasoning}
    if calls:
        message["tool_calls"] = calls
    return SimpleNamespace(status=200, body={"choices": [{"message": message, "finish_reason": "tool_calls" if calls else "stop"}]},
                           usage={"prompt_tokens": 100, "completion_tokens": 5}, stream=None, credential_id=1)


def run_turn(thread, results, *, tools=None):
    view = chats.get_thread(1, thread["id"])
    run = store.start_turn(1, thread["id"], "Next", view["revision"], str(view["revision"]), tools=tools)["run"]
    payloads = []
    responses = iter(results)

    async def invoke(request, user, payload, **kwargs):
        payloads.append(copy.deepcopy(payload))
        return user, SimpleNamespace(type_id="llama", source_id=1), "glm-offline", "glm-offline", next(responses)

    async def go():
        with patch.object(runtime.api, "invoke_chat", invoke), patch.object(runtime.api, "_safe_record_usage"), \
             patch.object(runtime, "execute_tool", AsyncMock(return_value=("mock evidence", {"name": "web_search", "status": "done"}))), \
             patch.object(chat_runs, "schedule_wake"), patch("app.push.notify_run_done", new=AsyncMock()):
            await chat_runs._run(chat_runs.internal_request(1), {"id": 1}, run)

    asyncio.run(go())
    saved = store.get_run(1, run["id"])
    assert saved["status"] == "completed", saved["message"]
    return payloads, json.loads(saved["provider_messages"]), saved


@pytest.mark.parametrize("toggle", ["search", "coding"])
@pytest.mark.parametrize("initial", [False, True])
def test_capabilities_refresh_across_retained_turns(thread, toggle, initial):
    def turn(enabled):
        hosts.set_settings(1, agent_coding=enabled if toggle == "coding" else True)
        return run_turn(thread, [result("answer")], tools=["web_search"] if toggle != "search" or enabled else [])

    first, _, _ = turn(initial)
    second, _, saved = turn(not initial)
    third, _, _ = turn(not initial)
    name = "web_search" if toggle == "search" else "read_file"
    names = lambda payload: {schema["function"]["name"] for schema in payload.get("tools", [])}
    assert (name in names(first[0])) is initial
    assert (name in names(second[0])) is (not initial)
    assert ("Web search is on." in second[0]["messages"][0]["content"]) is (toggle != "search" or not initial)
    assert ("Loaded tools:" in second[0]["messages"][0]["content"]) is (toggle != "coding" or not initial)
    assert saved["request"]["prompt_snapshot"]["tools"] == second[0]["tools"]
    # Unchanged capabilities retain the cached system, schemas and transcript prefix.
    assert third[0]["tools"] == second[0]["tools"]
    assert third[0]["messages"][:len(second[0]["messages"])] == second[0]["messages"]
    if toggle == "search":
        assert "run_terminal_command" in names(second[0])
    if toggle == "coding" and initial:
        assert "read_file" in json.loads(db.fetchone("SELECT loaded_tools FROM chat_threads WHERE id=?", (saved["thread_id"],))["loaded_tools"])
        restored, _, _ = turn(True)
        assert names(restored[0]) == names(first[0])


@pytest.mark.parametrize("final", [result(reasoning="final thought"), result(), result("final answer", reasoning="final thought")],
                         ids=["reasoning-only", "empty", "normal"])
def test_final_history_never_repeats_tool_round_content(thread, final):
    calls = [{"id": "search-call", "function": {"name": "web_search", "arguments": '{"query":"mock"}'}}]
    with patch.object(runtime, "MAX_EMPTY_RETRIES", 1):
        payloads, history, _ = run_turn(thread, [result("early commentary", calls=calls), final, final])
    assert sum(m.get("content") == "early commentary" for m in history) == 1
    assert any(m.get("role") == "tool" and m.get("content") == "mock evidence" for m in history)
    assert not any(m.get("role") == "assistant" and not m.get("tool_calls") and m.get("content") == "early commentary" for m in history)
    if final.body["choices"][0]["message"]["reasoning_content"]:
        assert history[-1]["role"] == "assistant"
        assert history[-1]["reasoning_content"] == "final thought"
        assert history[-1]["content"] == final.body["choices"][0]["message"]["content"]
    replay, _, _ = run_turn(thread, [result("next answer")])
    # The next provider call replays the durable history without inventing an answer.
    assert replay[0]["messages"][1:1 + len(history)] == [
        {k: v for k, v in m.items() if not k.startswith("_fuse_")} for m in history
    ]
    assert len(payloads) == (3 if not final.body["choices"][0]["message"]["content"] and not final.body["choices"][0]["message"]["reasoning_content"] else 2)


def test_frozen_prompt_follows_project_attach_and_edit(thread):
    # Moving a chat into a project or editing the project's path changes no
    # tool schema, so the frozen prompt used to keep telling the model it was
    # a regular chat (or name the old folder) until a compaction.
    hosts.set_settings(1, agent_coding=True)
    db.execute("INSERT INTO user_hosts(user_id,alias,hostname,username,auth_type,secret,created_at,updated_at) "
               "VALUES(1,'box','box.invalid','u','agent','',?,?)", (db.now(), db.now()))
    host = db.fetchone("SELECT id FROM user_hosts WHERE user_id=1 AND alias='box'")["id"]
    project = hosts.add_project(1, host, {"name": "api", "remote_path": "/srv/api"})
    first, _, _ = run_turn(thread, [result("answer")])
    assert "This is a regular chat, not a project session." in first[0]["messages"][0]["content"]
    chats.move_thread_to_project(1, thread["id"], host, project["id"])
    attached, _, _ = run_turn(thread, [result("answer")])
    system = attached[0]["messages"][0]["content"]
    assert "Project session bound to box cwd=/srv/api." in system
    assert "This is a regular chat" not in system
    # The refresh rebuilds only the system prompt, never the transcript.
    assert attached[0]["messages"][1:len(first[0]["messages"])] == first[0]["messages"][1:]
    # Unchanged framing keeps the cached prefix: no refresh on the next turn.
    again, _, _ = run_turn(thread, [result("answer")])
    assert again[0]["messages"][:len(attached[0]["messages"])] == attached[0]["messages"]
    hosts.patch_project(1, host, project["id"], {"remote_path": "/srv/api-v2"})
    edited, _, _ = run_turn(thread, [result("answer")])
    assert "Project session bound to box cwd=/srv/api-v2." in edited[0]["messages"][0]["content"]


@pytest.mark.parametrize("project_session,bound_label", [(False, ""), (True, ""), (True, "box cwd=/srv/api")])
@pytest.mark.parametrize("kind", ["", "general-purpose", "explore", "plan"])
@pytest.mark.parametrize("subagent", [False, True])
def test_every_coding_prompt_states_its_session_frame(project_session, bound_label, kind, subagent):
    # The runtime rebuilds a frozen prompt that lacks the current framing; a
    # coding prompt built without it would be rebuilt on every turn and never
    # keep its cached prefix.
    system = runtime.build_system_prompt(
        instructions="", profile="", thread_context="", tools=["web_search"], coding=True,
        project_session=project_session, bound_label=bound_label, loaded_tools=["read_file"],
        subagent=subagent, subagent_type=kind,
    )
    assert runtime.session_frame(project_session=project_session, bound_label=bound_label) in system
