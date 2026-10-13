"""Chat command DB work must not monopolize the stream event loop."""
import asyncio
import json
from unittest.mock import Mock

import pytest
from fastapi import HTTPException
from fastapi.responses import JSONResponse
from starlette.requests import Request

from app import main
from app.web import chat


def request(body=None):
    async def receive():
        return {"type": "http.request", "body": json.dumps(body or {}).encode()}

    return Request({"type": "http", "method": "POST", "path": "/chat", "headers": []}, receive)


def off_loop():
    with pytest.raises(RuntimeError, match="no running event loop"):
        asyncio.get_running_loop()


@pytest.fixture
def route_mocks(monkeypatch):
    monkeypatch.setattr(chat, "_chat_auth", lambda req: ({"id": 7}, None))
    monkeypatch.setattr(chat.limiter, "allow", lambda *args: True)
    monkeypatch.setattr(chat.chats, "public_id_for", lambda pk: "public-thread")


def test_start_turn_off_loop_and_run_start_on_loop(monkeypatch, route_mocks):
    body = {"text": "hello", "expected_revision": 4, "command_id": "turn-command",
            "model": "offline", "tools": [], "context": "context", "attachments": [],
            "reasoning_effort": "low", "invoke_skill": "skill"}
    run = {"id": "run", "thread_id": 11, "message_id": "message", "status": "queued",
           "created_at": "now", "cancel_requested": False}
    result = {"thread": {"id": "public-thread", "revision": 5, "run": run},
              "run": run, "idempotent": False}
    calls = []

    def resolve(uid, public):
        off_loop()
        assert (uid, public) == (7, "public-thread")
        calls.append("resolve")
        return 11

    def start_turn(*args, **kwargs):
        off_loop()
        assert args == (7, 11, "hello", 4, "turn-command")
        assert kwargs == {"model": "offline", "tools": [], "context": "context", "attachments": [],
                          "reasoning_effort": "low", "auto_title": True, "invoke_skill": "skill",
                          "replace_from": None}
        calls.append("commit")
        return result

    async def exercise():
        loop = asyncio.get_running_loop()

        async def start(req, user, committed_run):
            assert asyncio.get_running_loop() is loop
            assert user == {"id": 7} and committed_run is run
            calls.append("start")

        def notify(pk):
            assert asyncio.get_running_loop() is loop
            assert pk == 11
            calls.append("notify")

        monkeypatch.setattr(chat.chat_runs, "start", start)
        monkeypatch.setattr(chat.chat_titles, "notify", notify)
        response = await chat.chat_turn(request(body), "public-thread")
        assert response.status_code == 200
        assert json.loads(response.body)["thread"]["revision"] == 5
        assert response.headers["cache-control"] == "no-store"

    monkeypatch.setattr(chat.chats, "resolve_thread_pk", resolve)
    monkeypatch.setattr(chat.chat_store, "start_turn", start_turn)
    asyncio.run(exercise())
    assert calls == ["resolve", "commit", "start", "notify"]


@pytest.mark.parametrize("messages", [[], [{"role": "user", "content": "edited"}],
                                    [{"role": "user", "content": "retry"},
                                     {"role": "assistant", "content": "previous"}]])
def test_update_thread_off_loop_preserves_command(monkeypatch, route_mocks, messages):
    def update(uid, public, **kwargs):
        off_loop()
        assert (uid, public) == (7, "public-thread")
        assert kwargs == {"expected_revision": 4, "command_id": "edit-command", "messages": messages}
        return {"id": public, "revision": 5, "messages": messages}

    monkeypatch.setattr(chat.chats, "update_thread", update)
    response = asyncio.run(chat.chat_thread_patch(request({"messages": messages,
                            "expected_revision": 4, "command_id": "edit-command"}), "public-thread"))
    assert json.loads(response.body) == {"id": "public-thread", "revision": 5,
                                        "messages": messages, "run": None}


def test_update_cancels_orphan_worker_on_event_loop(monkeypatch, route_mocks):
    from app import workspace

    async def exercise():
        loop = asyncio.get_running_loop()
        cancelled = asyncio.Event()

        class Worker:
            _fuse_started = True

            def done(self):
                return False

            def get_loop(self):
                return loop

            def cancel(self):
                assert asyncio.get_running_loop() is loop
                cancelled.set()

        def update(*args, **kwargs):
            off_loop()
            chat.chat_runs.cancel("orphan-worker")
            return {"id": "public-thread", "revision": 5}

        monkeypatch.setitem(chat.chat_runs._tasks, "orphan-worker", Worker())
        monkeypatch.setattr(chat.chat_runs, "_cancelled", set())
        monkeypatch.setattr(chat.chat_runs, "_signal_cancel", lambda ident: None)
        monkeypatch.setattr(workspace, "cancel_run_jobs", lambda ident: None)
        monkeypatch.setattr(chat.chats, "update_thread", update)
        await chat.chat_thread_patch(request({"messages": [], "expected_revision": 4,
                                             "command_id": "undo"}), "public-thread")
        await asyncio.wait_for(cancelled.wait(), timeout=1)

    asyncio.run(exercise())


@pytest.mark.parametrize("handler", [chat.chat_turn, chat.chat_thread_patch])
def test_auth_failure_does_not_reach_db(monkeypatch, handler):
    denied = JSONResponse({"error": "denied"}, status_code=403)
    monkeypatch.setattr(chat, "_chat_auth", lambda req: (None, denied))
    store = Mock(side_effect=AssertionError("unauthenticated database access"))
    monkeypatch.setattr(chat.chat_store, "start_turn", store)
    monkeypatch.setattr(chat.chats, "update_thread", store)
    monkeypatch.setattr(chat.chats, "resolve_thread_pk", store)
    assert asyncio.run(handler(request(), "public-thread")) is denied
    store.assert_not_called()


@pytest.mark.parametrize("handler,owner,name", [(chat.chat_turn, chat.chat_store, "start_turn"),
                                               (chat.chat_thread_patch, chat.chats, "update_thread")])
def test_worker_validation_error_keeps_http_status(monkeypatch, route_mocks, handler, owner, name):
    monkeypatch.setattr(chat.chats, "resolve_thread_pk", lambda *args: 11)

    def conflict(*args, **kwargs):
        off_loop()
        raise chat.chats.ChatValidationError("stale revision", 409)

    monkeypatch.setattr(owner, name, conflict)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(handler(request({"messages": [], "expected_revision": 4,
                                     "command_id": "command"} if handler is chat.chat_thread_patch else
                                    {"text": "hello", "expected_revision": 4, "command_id": "command"}),
                            "public-thread"))
    assert exc.value.status_code == 409
    assert exc.value.detail == "stale revision"


def test_maintenance_db_work_off_loop_and_resume_on_loop(monkeypatch):
    calls = []

    def database_job(name):
        def run():
            off_loop()
            calls.append(name)
        return run

    monkeypatch.setattr(main.chat_store, "prune_events", database_job("prune"))
    monkeypatch.setattr(main.chat_runs, "reap_orphans", database_job("reap"))
    monkeypatch.setattr(main.chat_runs, "sweep_wakes", database_job("sweep"))

    async def exercise():
        loop = asyncio.get_running_loop()

        async def sleep(delay):
            assert delay == main._REFRESH_INTERVAL

        async def resume():
            assert asyncio.get_running_loop() is loop
            calls.append("resume")

        async def retention():
            assert asyncio.get_running_loop() is loop
            calls.append("retention")
            raise asyncio.CancelledError

        monkeypatch.setattr(main.asyncio, "sleep", sleep)
        monkeypatch.setattr(main.chat_runs, "resume_queued", resume)
        monkeypatch.setattr(main.retention, "tick", retention)
        with pytest.raises(asyncio.CancelledError):
            await main._maintenance_loop(main.app)

    asyncio.run(exercise())
    assert calls == ["prune", "reap", "resume", "sweep", "retention"]


def test_one_failing_refresh_job_does_not_delay_oauth_refresh(monkeypatch):
    calls = {"oauth": 0, "catalog": 0}

    async def oauth():
        calls["oauth"] += 1

    async def catalog():
        calls["catalog"] += 1
        raise RuntimeError("catalog down")

    ticks = []

    async def sleep(delay):
        assert delay == main._REFRESH_INTERVAL
        ticks.append(delay)
        if len(ticks) > 6:
            raise asyncio.CancelledError

    async def noop():
        return None

    monkeypatch.setattr(main.asyncio, "sleep", sleep)
    for name in ("grok_version_refresh_due", "chatgpt_refresh_due", "kimi_refresh_due",
                 "claude_refresh_due", "auto_price_due"):
        monkeypatch.setattr(main, name, noop)
    monkeypatch.setattr(main, "refresh_due", oauth)
    monkeypatch.setattr(main, "openrouter_refresh_due", catalog)
    monkeypatch.setattr(main.issues_pipeline, "tick", noop)
    app = type("App", (), {"state": type("State", (), {})()})()
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(main._refresh_loop(app))
    # Six cycles: OAuth refresh ran every cycle; the failing job backed off alone.
    assert calls["oauth"] == 6
    assert calls["catalog"] == 2  # cycle 1, then 120 s later (cycle 3); next after 240 s
    assert app.state.refresh_errors == ["openrouter catalog: RuntimeError"]
