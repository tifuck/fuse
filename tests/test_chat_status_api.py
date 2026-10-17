"""HTTP contracts for the status the chat page polls.

The UI is only as truthful as these payloads: every state the frontend maps
must actually be produced, and a terminal receipt must stay terminal.
"""
import json
import os
import re
from contextlib import contextmanager
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware

from app import accounts, chat_store, chats, command_jobs, db, permissions
from app.security import limiter
from app.config import settings
from tests.mariadb_fixture import isolated_database

pytestmark = pytest.mark.skipif(
    os.environ.get("FUSE_CHAT_TEST_MODE") != "1",
    reason="requires disposable MariaDB fixture",
)

# Frontend mapAgentToolStatus / command_trace buckets. Distinct, never blank
# when the row has a status the UI knows.
_RUN_STATES = ("queued", "running", "completed", "failed", "cancelled", "interrupted")
_TOOL_STATUS = {"completed": "done", "failed": "error", "timed_out": "error",
                "cancelled": "interrupted", "interrupted": "interrupted", "lost": "interrupted"}
_UI_FIELDS = ("status", "execution_status", "output", "output_mode", "output_chars",
              "preview", "summary", "error")
_SYNC_KEYS = {"cursor", "threads", "context", "live_agents", "projects"}
_RUN_KEYS = {"id", "thread_id", "message_id", "status", "created_at", "updated_at",
             "cancel_requested", "resume_count", "activity"}
_AGENT_KEYS = {"id", "status", "resumable", "has_transcript", "resume_count", "summary", "error"}


@contextmanager
def _quiet_sync():
    """GET /chat/sync reaps and relaunches queued runs; pin those for these tests."""
    with patch("app.chat_runs.reap_orphans"), patch("app.chat_runs.resume_queued", new=AsyncMock()):
        yield


@pytest.fixture()
def client():
    with isolated_database():
        accounts.create_user("status@test.invalid", "password12", admin=False)
        accounts.create_user("other@test.invalid", "password12", admin=False)
        app = FastAPI()
        app.add_middleware(
            SessionMiddleware, secret_key=settings.app_secret, https_only=True, same_site="strict"
        )
        from app.web import router

        app.include_router(router)
        with TestClient(app, base_url="https://testserver") as http:
            yield http


def _login(client, email="status@test.invalid"):
    # The auth limiter is process-global (10/min) and every test logs in twice.
    limiter._hits.clear()
    page = client.get("/login").text
    token = re.search(r'name="csrf_token" value="([^"]+)"', page).group(1)
    posted = client.post(
        "/login",
        data={"email": email, "password": "password12", "csrf_token": token},
        follow_redirects=False,
    )
    assert posted.status_code == 303
    meta = re.search(r'name="csrf-token" content="([^"]+)"', client.get("/chat").text)
    client.headers["x-csrf-token"] = meta.group(1)


def _uid():
    return accounts.user_by_email("status@test.invalid")["id"]


def _thread(client, command="create-status"):
    response = client.post("/chat/threads", json={"model": "offline", "tools": [], "command_id": command})
    assert response.status_code == 201, response.text
    body = response.json()
    pk = chats.resolve_thread_pk(_uid(), body["id"])
    return body, pk


def _json(value):
    # ensure_ascii: the MariaDB translator doubles literal '%', which an emoji
    # inside a bound parameter would otherwise be read as.
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"))


def _stamp_run(run_id, status, *, message=None, transcript=None, parent=None, depth=0, request=None):
    sets = ["status=?"]
    args = [status]
    if message is not None:
        sets.append("message=?")
        args.append(_json(message))
    if transcript is not None:
        sets.append("provider_messages=?")
        args.append(_json(transcript))
    if parent is not None:
        sets.append("parent_run_id=?")
        sets.append("agent_depth=?")
        args.extend([parent, depth])
    if request is not None:
        sets.append("request=?")
        args.append(_json(request))
    args.append(run_id)
    db.execute(f"UPDATE chat_runs SET {', '.join(sets)} WHERE id=?", tuple(args))


def _insert_job(job_id, user_id, thread_pk, status, *, background=1, out="", out_chars=None,
                incomplete=0, error="", exit_code=None, finished_at=None):
    command_jobs.ensure_schema()
    db.execute(
        """INSERT INTO command_jobs(id,user_id,host_id,host,thread_id,command,cwd,background,timeout_ms,
               status,exit_code,error,out,out_chars,output_incomplete,created_at,started_at,finished_at)
           VALUES(?,?,0,'box',?,'echo hi','',?,?,?,?,?,?,?,?,?,?,?)""",
        (job_id, user_id, thread_pk, background, 1000, status, exit_code, error, out,
         out_chars, incomplete, "2026-10-05T00:00:00Z", "2026-10-05T00:00:00Z", finished_at),
    )


def _tool(name, **fields):
    return {"name": name, "status": "done", **fields}


def _shell_trace(job_id, **extra):
    base = dict(
        job_id=job_id, child_id=job_id, command_id=job_id, command="echo hi",
        status="running", execution_status="running", state="running", output="",
        output_chars=0, output_mode="full", preview="", error="",
    )
    base.update(extra)
    return _tool("run_terminal_command", **base)


def _save_messages(thread_pk, messages):
    db.execute(
        "UPDATE chat_threads SET messages=?, revision=revision+1 WHERE id=?",
        (_json(messages), thread_pk),
    )


def _snapshot(client, public_id):
    response = client.get(f"/chat/threads/{public_id}")
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    return response.json()


def _tools_of(body):
    return [tool for msg in body["messages"] if isinstance(msg, dict) for tool in msg.get("tools") or []]


# --- GET /chat/sync -------------------------------------------------------


def test_sync_shape_cursor_and_live_agents(client):
    _login(client)
    with _quiet_sync():
        empty = client.get("/chat/sync")
    assert empty.status_code == 200
    assert empty.headers["cache-control"] == "no-store"
    body = empty.json()
    assert set(body) >= _SYNC_KEYS
    assert body["threads"] == []
    assert body["live_agents"] == 0
    assert isinstance(body["cursor"], int) and isinstance(body["projects"], list)

    thread, pk = _thread(client, "sync-create")
    uid = _uid()
    root = chat_store.start_turn(uid, pk, "hello", thread["revision"], "sync-turn")["run"]
    child = "child-sync"
    db.execute(
        """INSERT INTO chat_runs(id,thread_id,user_id,status,request,message_id,message,created_at,updated_at,
               parent_run_id,root_run_id,agent_depth)
           VALUES(?,? ,?,'running','{}','m-child','{}',?,?,?,?,1)""",
        (child, pk, uid, db.now(), db.now(), root["id"], root["id"]),
    )
    with _quiet_sync():
        snap = client.get("/chat/sync").json()
    listed = snap["threads"][0]
    assert listed["id"] == thread["id"] and listed["running"] is True
    assert snap["live_agents"] == 1  # children only; the root turn is not an agent
    assert snap["cursor"] >= thread["event_cursor"]

    client.cookies.clear()
    assert client.get("/chat/sync").status_code == 401


# --- GET /chat/runs/{id} --------------------------------------------------


def test_run_snapshot_states_are_distinct_and_resumable(client):
    _login(client)
    thread, pk = _thread(client, "run-create")
    uid = _uid()
    run = chat_store.start_turn(uid, pk, "hello there friend", thread["revision"], "run-turn")["run"]
    transcript = [{"role": "user", "content": "hello there friend"}, {"role": "assistant", "content": "partial"}]

    for status in _RUN_STATES:
        _stamp_run(run["id"], status, transcript=transcript if status in ("interrupted", "cancelled") else None)
        got = client.get(f"/chat/runs/{run['id']}")
        assert got.status_code == 200, got.text
        assert got.headers["cache-control"] == "no-store"
        body = got.json()
        assert set(body) >= _RUN_KEYS | {"event_cursor", "resumable", "has_transcript", "work_ms", "work_live"}
        assert body["status"] == status and body["status"]  # never blank when set
        assert body["thread_id"] == thread["id"]
        assert body["cancel_requested"] is False
        expect_resume = status in ("interrupted", "cancelled")
        assert body["has_transcript"] is expect_resume
        assert body["resumable"] is expect_resume

    _stamp_run(run["id"], "interrupted", transcript=transcript)
    db.execute("UPDATE chat_runs SET resume_count=? WHERE id=?", (chat_store.MAX_RESUME_ATTEMPTS, run["id"]))
    spent = client.get(f"/chat/runs/{run['id']}").json()
    assert spent["status"] == "interrupted" and spent["resumable"] is False

    _login(client, "other@test.invalid")
    assert client.get(f"/chat/runs/{run['id']}").status_code == 404
    assert client.get("/chat/runs/does-not-exist").status_code == 404


def test_run_snapshot_keeps_child_lineage_and_unicode_message(client):
    _login(client)
    thread, pk = _thread(client, "lineage-create")
    uid = _uid()
    root = chat_store.start_turn(uid, pk, "parent", thread["revision"], "lineage-turn")["run"]
    text = "line one\nline two 🙂 \u2603"
    child_request = {"model": "offline", "agent": {"description": "looks at " + text, "subagent_type": "explore"}}
    child = "child-lineage"
    db.execute(
        """INSERT INTO chat_runs(id,thread_id,user_id,status,request,message_id,message,created_at,updated_at,
               parent_run_id,root_run_id,agent_depth,provider_messages,resume_count)
           VALUES(?,?,?,'interrupted',?,'m-child','{}',?,?,?,?,1,?,0)""",
        (child, pk, uid, _json(child_request), db.now(), db.now(), root["id"], root["id"],
         _json([{"role": "assistant", "content": text}])),
    )
    body = client.get(f"/chat/runs/{child}").json()
    assert body["status"] == "interrupted"
    assert body["parent_run_id"] == root["id"] and body["root_run_id"] == root["id"]
    assert body["agent_depth"] == 1 and body["subagent_type"] == "explore"
    assert body["resumable"] is True
    assert text in body["description"]
    assert body["messages"][-1]["content"] == text  # no \\uXXXX double-encoding


# --- cancel / resume ------------------------------------------------------


def test_cancel_settles_each_active_state_and_resume_round_trips(client):
    _login(client)
    thread, pk = _thread(client, "cancel-create")
    uid = _uid()
    run = chat_store.start_turn(uid, pk, "hello", thread["revision"], "cancel-turn")["run"]

    for status in ("queued", "running"):
        _stamp_run(run["id"], status)
        got = client.post(f"/chat/runs/{run['id']}/cancel")
        assert got.status_code == 200, got.text
        body = got.json()
        assert body["ok"] is True and set(body["run"]) >= _RUN_KEYS
        assert body["run"]["status"] == "cancelled"
        assert body["run"]["status"] not in chat_store.ACTIVE
        snap = _snapshot(client, thread["id"])
        assert snap["run"] is None and snap["running"] is False

    transcript = [{"role": "user", "content": "hello"}, {"role": "assistant", "content": "kept"}]
    _stamp_run(run["id"], "cancelled", transcript=transcript)
    with patch("app.chat_runs.start", new=AsyncMock()):
        resumed = client.post(f"/chat/runs/{run['id']}/resume")
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["run"]["status"] == "queued"
    assert resumed.json()["run"]["resume_count"] == 1
    again = client.post(f"/chat/runs/{run['id']}/resume")
    assert again.status_code == 409  # queued is not a resumable terminal state

    _stamp_run(run["id"], "completed", transcript=transcript)
    assert client.post(f"/chat/runs/{run['id']}/resume").status_code == 409
    assert client.post("/chat/runs/missing/cancel").status_code == 404
    assert client.post("/chat/runs/missing/resume").status_code == 404


def test_cancel_scope_self_spares_a_sibling(client):
    _login(client)
    thread, pk = _thread(client, "scope-create")
    uid = _uid()
    root = chat_store.start_turn(uid, pk, "hello", thread["revision"], "scope-turn")["run"]
    for ident in ("sib-a", "sib-b"):
        db.execute(
            """INSERT INTO chat_runs(id,thread_id,user_id,status,request,message_id,message,created_at,updated_at,
                   parent_run_id,root_run_id,agent_depth)
               VALUES(?,?,?,'running','{}',?,'{}',?,?,?,?,1)""",
            (ident, pk, uid, "m-" + ident, db.now(), db.now(), root["id"], root["id"]),
        )
    got = client.post("/chat/runs/sib-a/cancel?scope=self")
    assert got.status_code == 200
    assert got.json()["run"]["status"] == "cancelled"
    assert chat_store.get_run(uid, "sib-b")["status"] == "running"
    assert chat_store.get_run(uid, root["id"])["status"] == "queued"


def test_cancel_scope_reply_stops_only_the_main_reply(client):
    """Send now: the root's reply ends and its subagents keep running (a
    plain root cancel is Stop, which ends every subagent in the chat)."""
    _login(client)
    thread, pk = _thread(client, "reply-create")
    uid = _uid()
    root = chat_store.start_turn(uid, pk, "hello", thread["revision"], "reply-turn")["run"]
    for ident in ("kid-a", "kid-b"):
        db.execute(
            """INSERT INTO chat_runs(id,thread_id,user_id,status,request,message_id,message,created_at,updated_at,
                   parent_run_id,root_run_id,agent_depth)
               VALUES(?,?,?,'running','{}',?,'{}',?,?,?,?,1)""",
            (ident, pk, uid, "m-" + ident, db.now(), db.now(), root["id"], root["id"]),
        )
    got = client.post(f"/chat/runs/{root['id']}/cancel?scope=reply")
    assert got.status_code == 200
    assert got.json()["run"]["status"] == "cancelled"
    for ident in ("kid-a", "kid-b"):
        kid = chat_store.get_run(uid, ident)
        assert kid["status"] == "running" and not kid["cancel_requested"]
        # Handed to the background, so its report reaches the next turn.
        assert kid["request"]["agent"]["background"] is True


# --- steer ----------------------------------------------------------------


def test_steer_shape_idempotence_and_unicode(client):
    _login(client)
    thread, pk = _thread(client, "steer-create")
    uid = _uid()
    chat_store.start_turn(uid, pk, "hello", thread["revision"], "steer-turn")
    text = "focus on line 1\nand line 2 🙂"
    body = {"text": text, "command_id": "steer-1"}
    first = client.post(f"/chat/threads/{thread['id']}/steer", json=body)
    assert first.status_code == 200, first.text
    data = first.json()
    assert data["ok"] is True and data["live"] is True and data["injected"] is True
    assert data["idempotent"] is False
    assert isinstance(data["revision"], int) and data["revision"] > thread["revision"]
    assert set(data["run"]) >= _RUN_KEYS and data["run"]["status"] in chat_store.ACTIVE
    snap = _snapshot(client, thread["id"])
    assert [m["content"] for m in snap["messages"] if m["role"] == "user" and m["content"] == text] == [text]

    again = client.post(f"/chat/threads/{thread['id']}/steer", json=body)
    assert again.json()["idempotent"] is True
    assert again.json()["revision"] == data["revision"]
    assert client.post(f"/chat/threads/{thread['id']}/steer", json={"text": "x"}).status_code == 422
    assert client.post("/chat/threads/missing/steer", json=body).status_code == 404


def test_steer_rejects_when_no_run_is_live(client):
    _login(client)
    thread, _pk = _thread(client, "steer-dead")
    got = client.post(f"/chat/threads/{thread['id']}/steer", json={"text": "hi", "command_id": "steer-dead-1"})
    assert got.status_code == 409
    assert _snapshot(client, thread["id"])["messages"] == []


# --- thread snapshot: tool states and text integrity ----------------------


def test_snapshot_renders_every_command_state_distinctly(client):
    _login(client)
    thread, pk = _thread(client, "states-create")
    uid = _uid()
    states = ("running", "cancelling", "completed", "failed", "timed_out", "cancelled", "interrupted", "lost")
    messages = [{"role": "assistant", "content": "", "tools": []}]
    for state in states:
        job = "job-" + state
        _insert_job(
            job, uid, pk, state, background=0, out=f"out {state}\n", out_chars=len(f"out {state}\n"),
            error="" if state == "completed" else state, exit_code=0 if state == "completed" else 1,
        )
        messages[0]["tools"].append(_shell_trace(job))
    _save_messages(pk, messages)

    tools = {tool["job_id"]: tool for tool in _tools_of(_snapshot(client, thread["id"]))}
    seen = set()
    for state in states:
        tool = tools["job-" + state]
        for field in _UI_FIELDS:
            if field == "summary":
                continue  # shell traces carry preview, not a summary
            assert field in tool, (state, sorted(tool))
        assert tool["execution_status"] == state and tool["state"] == state
        assert tool["status"] == ("running" if state in ("running", "cancelling") else _TOOL_STATUS[state])
        assert tool["status"]  # never blank
        # A live row is streamed in memory: the snapshot keeps the trace's own output
        # rather than the older launch row. Terminal rows replace it.
        if state in ("running", "cancelling"):
            assert tool["output"] == "" and tool["output_chars"] == 0
        else:
            assert tool["output"] == f"out {state}\n"
            assert tool["output_chars"] == len(tool["output"]) and tool["output_mode"] == "full"
        assert tool["error"] == "" or tool["error"] == state
        seen.add((tool["status"], tool["execution_status"]))
    assert len(seen) == len(states)  # running/done/error/interrupted stay distinct


def test_snapshot_text_integrity_multiline_unicode_and_tail(client):
    _login(client)
    thread, pk = _thread(client, "text-create")
    uid = _uid()
    multiline = "alpha\nbeta\r\ngamma 🙂 \u2603 \U0001f9e0\n"
    huge = ("x" * 1000 + "\n") * 40  # 40_040 chars, past the 24_000 snapshot cap
    assert len(huge) > chats.TOOL_OUTPUT_MAX
    _insert_job("job-multi", uid, pk, "completed", background=0, out=multiline,
                out_chars=len(multiline), exit_code=0)
    _insert_job("job-huge", uid, pk, "completed", background=0, out=huge,
                out_chars=len(huge), exit_code=0)
    _insert_job("job-unknown", uid, pk, "interrupted", background=0, out="partial 🙂",
                out_chars=None, incomplete=1, error="interrupted")
    _save_messages(pk, [{"role": "assistant", "content": "", "tools": [
        _shell_trace("job-multi"), _shell_trace("job-huge"), _shell_trace("job-unknown"),
    ]}])
    tools = {tool["job_id"]: tool for tool in _tools_of(_snapshot(client, thread["id"]))}

    multi = tools["job-multi"]
    assert multi["output"] == multiline and "\\n" not in multi["output"]
    assert multi["output_chars"] == len(multiline) and multi["output_mode"] == "full"
    assert multi["truncated"] is False

    long = tools["job-huge"]
    assert long["output"] == huge[-chats.TOOL_OUTPUT_MAX:]
    assert long["output_chars"] == len(huge) and long["output_mode"] == "tail"
    assert long["output_start"] == len(huge) - len(long["output"])
    assert long["truncated"] is True and "🙂" not in long["output"]

    unknown = tools["job-unknown"]
    assert unknown["output"] == "partial 🙂"
    assert unknown["output_mode"] == "partial" and unknown["output_chars_exact"] is False
    assert unknown["output_start"] is None


def test_killed_command_keeps_partial_output_and_kill_receipt_settles(client):
    _login(client)
    thread, pk = _thread(client, "kill-create")
    uid = _uid()
    partial = "line 1\nstill going 🙂\n"
    _insert_job("job-killed", uid, pk, "cancelled", background=1, out=partial, out_chars=None,
                incomplete=1, error="cancelled", finished_at=db.now())
    _save_messages(pk, [{"role": "assistant", "content": "", "tools": [
        _shell_trace("job-killed"),
        _tool("kill_command", job_id="job-killed", child_id="job-killed", command_id="job-killed",
              status="running", state="cancelling", execution_status="cancelling",
              output="", preview="Cancelling", error="cancellation requested"),
    ]}])
    tools = _tools_of(_snapshot(client, thread["id"]))
    command = next(tool for tool in tools if tool["name"] == "run_terminal_command")
    receipt = next(tool for tool in tools if tool["name"] == "kill_command")
    assert command["output"] == partial
    assert command["execution_status"] == "cancelled" and command["status"] == "interrupted"
    assert command["output_mode"] == "partial"
    assert receipt["status"] == "done" and receipt["error"] == ""
    assert "execution_status" not in receipt and "state" not in receipt  # no eternal "cancelling"


def test_snapshot_spawn_traces_follow_child_status(client):
    _login(client)
    thread, pk = _thread(client, "spawn-create")
    uid = _uid()
    root = chat_store.start_turn(uid, pk, "hello", thread["revision"], "spawn-turn")["run"]
    # A live root run replaces the stored transcript with its own message, so the
    # saved spawn traces are only visible once that turn has settled.
    client.post(f"/chat/runs/{root['id']}/cancel")
    cases = (
        ("spawn-run", "running", "running", ""),
        ("spawn-ok", "completed", "done", "all green 🙂"),
        ("spawn-bad", "failed", "error", ""),
        ("spawn-stop", "cancelled", "interrupted", ""),
        ("spawn-cut", "interrupted", "interrupted", ""),
    )
    tools = []
    for ident, status, _ui, content in cases:
        db.execute(
            """INSERT INTO chat_runs(id,thread_id,user_id,status,request,message_id,message,created_at,updated_at,
                   parent_run_id,root_run_id,agent_depth,provider_messages)
               VALUES(?,?,?,?,?,'m','{}',?,?,?,?,1,?)""",
            (ident, pk, uid, status, _json({"agent": {"description": ident}}),
             db.now(), db.now(), root["id"], root["id"],
             _json([{"role": "assistant", "content": content}]) if content else None),
        )
        tools.append(_tool("spawn_subagent", child_id=ident, status="running", state="running",
                           execution_status="running", summary="Background task started", preview="still running"))
    messages = json.loads(db.fetchone("SELECT messages FROM chat_threads WHERE id=?", (pk,))["messages"])
    messages[-1]["tools"] = tools
    db.execute("UPDATE chat_threads SET messages=? WHERE id=?", (_json(messages), pk))

    settled = {tool["child_id"]: tool for tool in _tools_of(_snapshot(client, thread["id"]))
               if tool["name"] == "spawn_subagent"}
    assert settled["spawn-run"]["status"] == "running" and settled["spawn-run"]["execution_status"] == "running"
    assert "summary" not in settled["spawn-run"] and "preview" not in settled["spawn-run"]
    done = settled["spawn-ok"]
    assert done["status"] == "done" and done["execution_status"] == "completed"
    assert "error" not in done  # a completed child drops the error instead of blanking it
    assert done["summary"] == "all green 🙂" and done["preview"].startswith("all green")
    assert settled["spawn-bad"]["status"] == "error" and settled["spawn-bad"]["error"]
    for ident in ("spawn-stop", "spawn-cut"):
        assert settled[ident]["status"] == "interrupted"
        assert settled[ident]["execution_status"] in ("cancelled", "interrupted")
        assert settled[ident]["summary"]


# --- agents / project-agents ---------------------------------------------


def test_agents_list_child_statuses_including_resumable(client):
    _login(client)
    thread, pk = _thread(client, "agents-create")
    uid = _uid()
    root = chat_store.start_turn(uid, pk, "hello", thread["revision"], "agents-turn")["run"]
    specs = (
        ("ag-run", "running", None, 0, False),
        ("ag-queue", "queued", None, 0, False),
        ("ag-ok", "completed", None, 0, False),
        ("ag-bad", "failed", "boom 🙂", 0, False),
        ("ag-stop", "cancelled", None, 0, True),
        ("ag-cut", "interrupted", None, chat_store.MAX_RESUME_ATTEMPTS, False),
    )
    for ident, status, error, resumes, resumable in specs:
        message = {"content": "report " + ident, "error": error or ""}
        db.execute(
            """INSERT INTO chat_runs(id,thread_id,user_id,status,request,message_id,message,created_at,updated_at,
                   parent_run_id,root_run_id,agent_depth,provider_messages,resume_count)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,1,?,?)""",
            (ident, pk, uid, status,
             _json({"model": "offline", "agent": {"description": "child " + ident, "background": True}}),
             "m-" + ident, _json(message), db.now(), db.now(), root["id"], root["id"],
             _json([{"role": "assistant", "content": "report " + ident}]) if resumable or resumes else None,
             resumes),
        )
    got = client.get(f"/chat/threads/{thread['id']}/agents")
    assert got.status_code == 200 and got.headers["cache-control"] == "no-store"
    body = got.json()
    assert set(body) == {"agents", "live_count"}
    assert body["live_count"] == 2
    rows = {row["id"]: row for row in body["agents"]}
    assert set(rows) == {spec[0] for spec in specs}
    for ident, status, error, resumes, resumable in specs:
        row = rows[ident]
        assert set(row) >= _AGENT_KEYS
        assert row["status"] == status and row["status"]
        assert row["resumable"] is resumable
        assert row["resume_count"] == resumes
        assert row["parent_run_id"] == root["id"]
        if status in chat_store.ACTIVE:
            assert row["summary"] == "" and row["error"] == ""
        elif status == "failed":
            assert row["error"] == "boom 🙂" and row["summary"]
        else:
            assert row["summary"]
    assert client.get("/chat/threads/missing/agents").status_code == 404


def test_project_agents_is_not_applicable_to_a_plain_chat(client):
    _login(client)
    thread, _pk = _thread(client, "proj-create")
    got = client.get(f"/chat/threads/{thread['id']}/project-agents")
    assert got.status_code == 200
    assert got.json() == {"applicable": False}
    assert client.get("/chat/threads/missing/project-agents").status_code == 404


# --- jobs + kill ----------------------------------------------------------


def test_jobs_list_separates_live_and_recent_terminal(client):
    _login(client)
    thread, pk = _thread(client, "jobs-create")
    uid = _uid()
    now = db.now()
    _insert_job("job-live", uid, pk, "running", background=1, out="so far 🙂\n", out_chars=None, incomplete=1)
    _insert_job("job-stopping", uid, pk, "cancelling", background=1, out="draining\n", out_chars=None, incomplete=1)
    _insert_job("job-done", uid, pk, "completed", background=1, out="finished\n", out_chars=9,
                exit_code=0, finished_at=now)
    _insert_job("job-foreground", uid, pk, "running", background=0)
    got = client.get(f"/chat/threads/{thread['id']}/jobs")
    assert got.status_code == 200 and got.headers["cache-control"] == "no-store"
    body = got.json()
    assert set(body) == {"jobs", "live_count", "recent"}
    live = {job["id"]: job for job in body["jobs"]}
    assert set(live) == {"job-live", "job-stopping"} and body["live_count"] == 2
    assert live["job-live"]["status"] == "running" and live["job-live"]["state"] == "running"
    assert live["job-live"]["output"] == "so far 🙂\n" and live["job-live"]["output_mode"] == "partial"
    assert live["job-stopping"]["state"] == "cancelling" and live["job-stopping"]["status"] == "running"
    recent = {job["id"]: job for job in body["recent"]}
    assert "job-done" in recent and "job-live" not in recent
    assert recent["job-done"]["state"] == "completed" and recent["job-done"]["status"] == "done"
    assert recent["job-done"]["output"] == "finished\n"
    assert client.get("/chat/threads/missing/jobs").status_code == 404


def test_kill_receipt_settles_and_does_not_stay_cancelling(client):
    _login(client)
    thread, pk = _thread(client, "killhttp-create")
    uid = _uid()

    missing = client.post(f"/chat/threads/{thread['id']}/jobs/nope/kill")
    assert missing.status_code == 404

    _insert_job("job-bg", uid, pk, "running", background=1, out="partial 🙂\n", out_chars=None, incomplete=1)
    killed = client.post(f"/chat/threads/{thread['id']}/jobs/job-bg/kill")
    assert killed.status_code == 200, killed.text
    assert killed.headers["cache-control"] == "no-store"
    assert killed.json() == {"ok": True, "status": "running"}  # receipt; the row is cancelling
    row = db.fetchone("SELECT status, `out` AS output FROM command_jobs WHERE id='job-bg'")
    assert row["status"] == "cancelling"
    assert row["output"] == "partial 🙂\n"  # the kill keeps what was already delivered
    again = client.post(f"/chat/threads/{thread['id']}/jobs/job-bg/kill")
    assert again.status_code == 200 and again.json()["status"] == "running"

    listed = {job["id"]: job for job in client.get(f"/chat/threads/{thread['id']}/jobs").json()["jobs"]}
    assert listed["job-bg"]["state"] == "cancelling" and listed["job-bg"]["output"] == "partial 🙂\n"

    _insert_job("job-finished", uid, pk, "completed", background=1, out="all done\n", out_chars=9,
                exit_code=0, finished_at=db.now())
    done = client.post(f"/chat/threads/{thread['id']}/jobs/job-finished/kill")
    assert done.status_code == 200
    assert done.json()["status"] == "done"  # terminal, never "cancelling"
    assert db.fetchone("SELECT status FROM command_jobs WHERE id='job-finished'")["status"] == "completed"


def test_historical_jobs_reconcile_only_unsettled_traces(client):
    _login(client)
    thread, pk = _thread(client, "hist-create")
    uid = _uid()
    _insert_job("job-old", uid, pk, "completed", background=0, out="kept tail\n", out_chars=10, exit_code=0)
    _insert_job("job-fresh", uid, pk, "completed", background=0, out="fresh\n", out_chars=6, exit_code=0)
    _save_messages(pk, [{"role": "assistant", "content": "", "tools": [
        _shell_trace("job-old", status="running", execution_status="running"),
        _shell_trace("job-fresh", status="done", execution_status="completed", output="fresh\n"),
    ]}])
    recent = {job["id"]: job for job in client.get(f"/chat/threads/{thread['id']}/jobs").json()["recent"]}
    assert recent["job-old"]["state"] == "completed" and recent["job-old"]["output"] == "kept tail\n"
    assert "job-fresh" not in recent  # a settled trace is already final


# --- permissions ----------------------------------------------------------


def _ask(uid, thread_pk, ask_id, *, grant_kind="", grant_value="", host_id=None):
    db.execute(
        """INSERT INTO chat_permission_asks(id,user_id,thread_id,run_id,status,decision,kind,tool,host_id,host,
               path,cwd,command,secret,requester,grant_kind,grant_value,created_at,expires_at)
           VALUES(?,?,?,?,'pending','','path','write_file',?,'box','/etc/x','','',0,'Main agent',?,?,?,?)""",
        (ask_id, uid, thread_pk, "run-x", host_id, grant_kind, grant_value, db.now(), "2099-01-01T00:00:00Z"),
    )


def test_permissions_ask_answer_grant_round_trip(client):
    _login(client)
    thread, pk = _thread(client, "perm-create")
    uid = _uid()
    db.execute(
        """INSERT INTO user_hosts(user_id,alias,label,hostname,port,username,auth_type,secret,default_cwd,created_at,updated_at)
           VALUES(?,'box','','10.0.0.8',22,'root','agent','x','/work',?,?)""",
        (uid, db.now(), db.now()),
    )
    host_id = db.fetchone("SELECT id FROM user_hosts WHERE user_id=?", (uid,))["id"]
    _ask(uid, pk, "ask-once", host_id=host_id)
    _ask(uid, pk, "ask-session", grant_kind="path", grant_value="/srv/data", host_id=host_id)
    _ask(uid, pk, "ask-deny", host_id=host_id)

    panel = client.get(f"/chat/threads/{thread['id']}/permissions")
    assert panel.status_code == 200 and panel.headers["cache-control"] == "no-store"
    body = panel.json()
    assert set(body) >= {"bypass", "auto", "auto_available", "hosts", "pending"}
    assert {ask["id"] for ask in body["pending"]} == {"ask-once", "ask-session", "ask-deny"}
    for ask in body["pending"]:
        assert ask["status"] == "pending" and ask["kind"] == "tool_permission" and ask["status"]

    once = client.post(f"/chat/threads/{thread['id']}/permissions/ask-once", json={"action": "allow"})
    assert once.status_code == 200, once.text
    assert once.json()["status"] == "allowed" and once.json()["decision"] == "allow"
    assert once.json()["grant_scope"] == ""

    session = client.post(
        f"/chat/threads/{thread['id']}/permissions/ask-session", json={"action": "allow_session"}
    )
    assert session.status_code == 200
    assert session.json()["status"] == "allowed" and session.json()["grant_scope"] == "allow_session"
    after = client.get(f"/chat/threads/{thread['id']}/permissions").json()
    grants = [g for host in after["hosts"] for g in host["grants"]]
    assert len(grants) == 1 and grants[0]["value"] == "/srv/data" and grants[0]["scope"] == "session"
    assert {ask["id"] for ask in after["pending"]} == {"ask-deny"}

    denied = client.post(f"/chat/threads/{thread['id']}/permissions/ask-deny", json={"action": "deny"})
    assert denied.json()["status"] == "denied" and denied.json()["decision"] == "deny"
    replay = client.post(f"/chat/threads/{thread['id']}/permissions/ask-deny", json={"action": "deny"})
    assert replay.json()["status"] == "denied"  # a settled ask is not reopened

    removed = client.delete(f"/chat/permissions/grants/{grants[0]['id']}")
    assert removed.status_code == 200 and removed.json() == {"ok": True}
    left = client.get(f"/chat/threads/{thread['id']}/permissions").json()
    assert [g for host in left["hosts"] for g in host["grants"]] == []
    assert left["pending"] == []

    # More time: a pending ask's deadline moves later; a settled one stays put.
    from datetime import datetime, timedelta, timezone

    soon = (datetime.now(timezone.utc) + timedelta(seconds=10)).strftime("%Y-%m-%dT%H:%M:%SZ")
    db.execute(
        """INSERT INTO chat_permission_asks(id,user_id,thread_id,run_id,status,decision,kind,tool,host_id,host,
               path,cwd,command,secret,requester,grant_kind,grant_value,created_at,expires_at)
           VALUES('ask-more',?,?,'run-x','pending','','path','write_file',?,'box','/etc/x','','',0,'Main agent','','',?,?)""",
        (uid, pk, host_id, db.now(), soon),
    )
    more = client.post(f"/chat/threads/{thread['id']}/permissions/ask-more", json={"action": "extend"})
    assert more.status_code == 200, more.text
    assert more.json()["status"] == "pending" and more.json()["expires_at"] > soon
    settled = client.post(f"/chat/threads/{thread['id']}/permissions/ask-deny", json={"action": "extend"})
    assert settled.json()["status"] == "denied"
    assert client.post(f"/chat/threads/{thread['id']}/permissions/missing", json={"action": "extend"}).status_code == 404

    assert client.post(f"/chat/threads/{thread['id']}/permissions/ask-once", json={"action": "nope"}).status_code == 422
    assert client.post(f"/chat/threads/{thread['id']}/permissions/missing", json={"action": "allow"}).status_code == 404
    assert client.delete("/chat/permissions/grants/9999").status_code == 404
    assert client.delete("/chat/permissions/grants/not-a-number").status_code == 404
    assert client.get("/chat/threads/missing/permissions").status_code == 404


def test_permissions_are_scoped_to_the_owner(client):
    _login(client)
    thread, pk = _thread(client, "perm-owner")
    _ask(_uid(), pk, "ask-owned")
    _login(client, "other@test.invalid")
    assert client.get(f"/chat/threads/{thread['id']}/permissions").status_code == 404
    assert client.post(
        f"/chat/threads/{thread['id']}/permissions/ask-owned", json={"action": "allow"}
    ).status_code == 404
