"""Failed tool-call tracking: classification, storage, runtime hooks, admin pack."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
_TMP = tempfile.mkdtemp(prefix="fuse-tool-failures-")
os.environ.setdefault("DATABASE_PATH", str(Path(_TMP) / "tool-failures.db"))
os.environ.setdefault("APP_SECRET", "x" * 32)

from starlette.requests import Request as StarletteRequest

from app import db, tool_failures
from app.chat_runtime import (
    MAX_CALLS_PER_ROUND,
    ToolJob,
    _consume_round,
    _run_tools,
    jobs_from_calls,
    stream_dashboard_chat,
)

import pytest

# Uses the database: a disposable MariaDB schema shared by this module.
pytestmark = pytest.mark.usefixtures("isolated_db")


def _fresh_db():
    """Other test modules own DATABASE_PATH; just reset this suite's tables."""
    from app import chat_store, config

    live = (config.ROOT / "data" / "fuse.db").resolve()
    current = Path(str(config.settings.database_path)).expanduser().resolve()
    if current == live:
        # A previous test imported app.db against the live file. Rebind; never DELETE it.
        os.environ["DATABASE_PATH"] = str(Path(_TMP) / "tool-failures.db")
        config.settings.database_path = os.environ["DATABASE_PATH"]
        db.close()
    with db.tx() as conn:
        chat_store.init_schema(conn)
        conn.execute("DELETE FROM tool_failures")


def _mk_request():
    import asyncio

    scope = {"type": "http", "method": "POST", "path": "/", "headers": [], "client": ("127.0.0.1", 1)}

    async def receive():
        # Block forever: a real client that is still connected.
        await asyncio.Event().wait()

    return StarletteRequest(scope, receive)


class ClassifyTest(unittest.TestCase):
    def test_explicit_kind_wins(self):
        self.assertEqual(tool_failures.classify("whatever", kind="upstream_error"), "upstream_error")

    def test_text_hints(self):
        cases = {
            "unknown tool": "unknown_tool",
            "Tool is not loaded. Call search_tools": "tool_not_loaded",
            "arguments were invalid JSON": "invalid_arguments",
            "Tool error: no valid query.": "no_valid_query",
            "Search budget for this turn is exhausted.": "budget_exhausted",
            "Search rate limit reached. Try again shortly.": "rate_limited",
            "That URL is not an allowed public HTTPS address.": "not_allowed",
            "That domain is not allowed.": "not_allowed",
            "Refused to fetch a non-public address.": "not_allowed",
            "Page returned HTTP 403.": "upstream_error",
            "subagent not found": "not_found",
            "vault locked": "vault_locked",
            "spawn is not available": "unavailable",
            "agent timed out": "upstream_error",
            "agent is offline": "unavailable",
            "agent is busy": "upstream_error",
            "unknown host alias": "not_found",
            "invalid pattern": "invalid_arguments",
            "rg: regex parse error:\n    print(\nunclosed group": "invalid_arguments",
        }
        for text, want in cases.items():
            self.assertEqual(tool_failures.classify(text), want, text)

    def test_fallback(self):
        self.assertEqual(tool_failures.classify(""), "tool_error")
        self.assertEqual(tool_failures.classify("mystery"), "tool_error")


class RecordTest(unittest.TestCase):
    def setUp(self):
        _fresh_db()

    def test_roundtrip_and_clipping(self):
        tool_failures.record(
            tool_name="web_search",
            error="Tool error: no valid query.",
            kind="no_valid_query",
            arguments='{"query":"' + "x" * 9000,
            call_id="call_0",
            user_id=1,
            thread_id=7,
            run_id="run-1",
            model="glm-5.3-flash",
            source_type="zai",
            upstream_model="glm-5.3-flash",
            round_index=2,
            phase="search",
            latency_ms=123,
            trace={"name": "web_search", "status": "error"},
            reasoning="need to look this up",
            user_text="who won the game",
        )
        rows = tool_failures.recent()
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["error_kind"], "no_valid_query")
        self.assertEqual(len(row["arguments"]), tool_failures.ARGUMENTS_MAX)
        self.assertEqual(row["model"], "glm-5.3-flash")
        self.assertEqual(row["upstream_model"], "glm-5.3-flash")
        self.assertEqual(row["round_index"], 2)
        self.assertEqual(row["phase"], "search")
        self.assertEqual(row["reasoning"], "need to look this up")
        self.assertEqual(row["user_text"], "who won the game")
        self.assertEqual(row["trace"]["name"], "web_search")
        self.assertIsNotNone(row["latency_ms"])

    def test_filters_and_summary(self):
        for i in range(3):
            tool_failures.record(
                tool_name="read_url" if i else "web_search",
                error="Page returned HTTP 403." if i else "unknown tool",
                user_id=1,
                model=f"m{i}",
            )
        self.assertEqual(len(tool_failures.recent(kind="upstream_error")), 2)
        self.assertEqual(len(tool_failures.recent(tool="web_search")), 1)
        self.assertEqual(len(tool_failures.recent(model="m1")), 1)
        summary = tool_failures.summary(hours=24)
        self.assertEqual(summary["total"], 3)
        self.assertEqual(summary["by_kind"]["upstream_error"], 2)
        self.assertEqual(summary["by_tool"]["web_search"], 1)
        self.assertEqual(summary["by_model"]["m0"], 1)

    def test_fingerprints_group_common_errors(self):
        for _ in range(4):
            tool_failures.record(tool_name="read_url", error="Page returned HTTP 403.", model="a")
        tool_failures.record(tool_name="read_url", error="Page returned HTTP 500.", model="b")
        fps = tool_failures.fingerprints(hours=24)
        self.assertEqual(len(fps), 2)
        self.assertEqual(fps[0]["n"], 4)
        self.assertEqual(fps[0]["models"], 1)

    def test_analysis_pack_shape(self):
        tool_failures.record(tool_name="web_search", error="Search failed. Try again shortly.")
        pack = tool_failures.analysis_pack(limit=10, hours=24)
        for key in ("guide", "generated_at", "summary", "fingerprints", "recent"):
            self.assertIn(key, pack)
        self.assertIn("internal_exception", pack["guide"])
        self.assertIn("upstream_error", pack["summary"]["by_kind"])

    def test_long_traceback_keeps_the_exception_line(self):
        trace = "Traceback (most recent call last):\n" + "  frame\n" * 1000 + "TypeError: cannot unpack non-iterable coroutine object"
        tool_failures.record(tool_name="list_subagents", error="Tool failed.", kind="internal_exception", exception=trace)
        row = tool_failures.recent()[0]
        self.assertEqual(len(row["exception"]), tool_failures.EXCEPTION_MAX)
        self.assertTrue(row["exception"].endswith("TypeError: cannot unpack non-iterable coroutine object"))

    def test_record_never_raises(self):
        with patch.object(db, "tx", side_effect=RuntimeError("boom")):
            self.assertIsNone(tool_failures.record(tool_name="x", error="y"))


class FakeResult:
    def __init__(self, body=None, stream=None):
        self.status = 200
        self.body = body or {}
        self.usage = {}
        self.stream = stream
        self.credential_id = 1


class FakeSrc:
    type_id = "llama"
    source_id = 1


class ConsumeReasoningTest(unittest.IsolatedAsyncioTestCase):
    async def test_non_stream_reasoning(self):
        result = FakeResult(
            {
                "choices": [
                    {
                        "message": {
                            "content": "",
                            "reasoning_content": "I should call the tool",
                            "tool_calls": [
                                {"id": "c1", "type": "function", "function": {"name": "web_search", "arguments": "{}"}}
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ]
            }
        )
        with patch("app.chat_runtime.api._safe_record_usage", lambda *a, **k: None):
            summary = None
            agen = _consume_round(result, {"id": 1}, FakeSrc(), "up", "req")
            async for kind, payload in agen:
                if kind == "done":
                    summary = payload
        self.assertEqual(summary["reasoning"], "I should call the tool")
        self.assertEqual(len(summary["calls"]), 1)

    async def test_stream_reasoning_accumulates(self):
        async def stream():
            yield b'data: {"choices":[{"delta":{"reasoning_content":"think "}}]}\n\n'
            yield b'data: {"choices":[{"delta":{"reasoning_content":"more"}}]}\n\n'
            yield b'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"c1","function":{"name":"web_search","arguments":"{}"}}]}}]}\n\n'
            yield b'data: {"choices":[{"delta":{},"finish_reason":"tool_calls"}]}\n\n'
            yield b"data: [DONE]\n\n"

        result = FakeResult(stream=stream())
        with patch("app.chat_runtime.api._tap", lambda s, *a, **k: s), patch(
            "app.chat_runtime.api._safe_record_usage", lambda *a, **k: None
        ):
            summary = None
            agen = _consume_round(result, {"id": 1}, FakeSrc(), "up", "req")
            async for kind, payload in agen:
                if kind == "done":
                    summary = payload
        self.assertEqual(summary["reasoning"], "think more")
        self.assertEqual(summary["finish"], "tool_calls")

    async def _outs(self, stream, result=None):
        result = result or FakeResult(stream=stream)
        with patch("app.chat_runtime.api._tap", lambda s, *a, **k: s), patch(
            "app.chat_runtime.api._safe_record_usage", lambda *a, **k: None
        ):
            return [payload async for kind, payload in _consume_round(result, {"id": 1}, FakeSrc(), "up", "req") if kind == "out"]

    @staticmethod
    def _calling(outs):
        return [(i, o["fuse"]) for i, o in enumerate(outs) if (o.get("fuse") or {}).get("phase") == "calling"]

    async def test_stream_marks_thought_finished_when_tool_call_starts(self):
        async def stream(with_call):
            yield b'data: {"choices":[{"delta":{"reasoning_content":"think"}}]}\n\n'
            if with_call:
                yield b'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"c1","function":{"name":"web_search","arguments":"{\\"q"}}]}}]}\n\n'
                yield b'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"\\":1}"}}]}}]}\n\n'
            yield b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n'
            yield b"data: [DONE]\n\n"

        self.assertEqual(self._calling(await self._outs(stream(False))), [])
        outs = await self._outs(stream(True))
        calling = self._calling(outs)
        # Named as soon as the header arrives, before its arguments finish.
        self.assertEqual([event for _, event in calling], [{"event": "activity", "phase": "calling", "tool": "web_search"}])
        at = calling[0][0]
        self.assertIn("tool_calls", outs[at - 1]["choices"][0]["delta"])
        self.assertEqual(outs[at + 1]["choices"][0]["delta"]["tool_calls"][0]["function"], {"arguments": '":1}'})

    async def test_stream_names_each_new_tool_call(self):
        async def stream():
            yield b'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"c1","function":{"name":"read_file","arguments":""}}]}}]}\n\n'
            yield b'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"{}"}}]}}]}\n\n'
            yield b'data: {"choices":[{"delta":{"tool_calls":[{"index":1,"id":"c2","function":{"name":"web_search","arguments":"{}"}}]}}]}\n\n'
            yield b'data: {"choices":[{"delta":{},"finish_reason":"tool_calls"}]}\n\n'
            yield b"data: [DONE]\n\n"

        tools = [event.get("tool") for _, event in self._calling(await self._outs(stream()))]
        self.assertEqual(tools, ["read_file", "web_search"])

    async def test_stream_detects_text_format_tool_call_before_it_closes(self):
        async def stream():
            yield b'data: {"choices":[{"delta":{"content":"Let me look. <tool_"}}]}\n\n'
            yield b'data: {"choices":[{"delta":{"content":"call>\\n<function=grep>\\n<parameter=pattern>x"}}]}\n\n'
            yield b'data: {"choices":[{"delta":{"content":"</parameter>\\n</function>\\n</tool_call>"}}]}\n\n'
            yield b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n'
            yield b"data: [DONE]\n\n"

        outs = await self._outs(stream())
        calling = self._calling(outs)
        # The opener split across frames is still found, and named on the
        # same frame, before the closing tag streams.
        self.assertEqual([event.get("tool") for _, event in calling], ["grep"])
        self.assertEqual(calling[0][0], 2)

    async def test_stream_reads_tool_name_hint_from_translating_source(self):
        from app.proxy import UsageStream

        hints = {}

        async def stream():
            yield b'data: {"choices":[{"delta":{"reasoning_content":"plan"}}]}\n\n'
            hints["tool_call"] = "run_terminal_command"
            yield b'data: {"choices":[{"delta":{}}]}\n\n'
            yield b'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"c1","function":{"name":"run_terminal_command","arguments":"{}"}}]}}]}\n\n'
            yield b'data: {"choices":[{"delta":{},"finish_reason":"tool_calls"}]}\n\n'
            yield b"data: [DONE]\n\n"

        outs = await self._outs(None, FakeResult(stream=UsageStream(stream(), {}, hints=hints)))
        calling = self._calling(outs)
        self.assertEqual([event.get("tool") for _, event in calling], ["run_terminal_command"])
        self.assertEqual(calling[0][0], 2)


async def _drain_tools(calls, **kwargs):
    import contextlib

    request = _mk_request()
    jobs = []
    agen = _run_tools(
        calls,
        user_id=1,
        allowed_urls=set(),
        request=request,
        completion_i=0,
        budgets={"search_queries": 0, "fetches": 0},
        **kwargs,
    )
    try:
        async for item in agen:
            if isinstance(item, list):
                jobs = item
    finally:
        await agen.aclose()
    return jobs


class RuntimeHookTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        _fresh_db()

    def _diag(self):
        return {
            "run_id": "",
            "model": "test-model",
            "source_type": "llama",
            "upstream_model": "up-1",
            "round": 0,
            "user_text": "hi",
        }

    async def test_error_trace_is_recorded(self):
        async def fake_execute(name, arguments, **kwargs):
            return "Tool error: no valid query.", {
                "name": name,
                "status": "error",
                "kind": "no_valid_query",
                "error": "Tool error: no valid query.",
            }

        calls = [
            {"id": "call_0", "type": "function", "function": {"name": "web_search", "arguments": "{}"}}
        ]
        with patch("app.chat_runtime.execute_tool", fake_execute):
            jobs = await _drain_tools(calls, diag=self._diag())
        self.assertEqual(jobs[0].trace["status"], "error")
        rows = tool_failures.recent(kind="no_valid_query")
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["tool_name"], "web_search")
        self.assertEqual(row["model"], "test-model")
        self.assertEqual(row["source_type"], "llama")
        self.assertEqual(row["upstream_model"], "up-1")
        self.assertEqual(row["round_index"], 0)
        self.assertEqual(row["user_text"], "hi")
        self.assertIsNotNone(row["latency_ms"])
        self.assertEqual(row["arguments"], "{}")

    async def test_exception_on_trace_is_recorded(self):
        async def fake_execute(name, arguments, **kwargs):
            return "Tool failed.", {
                "name": name,
                "status": "error",
                "kind": "internal_exception",
                "error": "Tool failed.",
                "exception": "Traceback: workspace boom",
            }

        calls = [
            {"id": "call_0", "type": "function", "function": {"name": "grep", "arguments": '{"pattern":"x"}'}}
        ]
        with patch("app.chat_runtime.execute_tool", fake_execute):
            await _drain_tools(calls, diag=self._diag())
        rows = tool_failures.recent(kind="internal_exception")
        self.assertEqual(len(rows), 1)
        self.assertIn("workspace boom", rows[0]["exception"])
        self.assertEqual(rows[0]["tool_name"], "grep")

    async def test_internal_exception_is_recorded_with_traceback(self):
        async def boom(name, arguments, **kwargs):
            raise RuntimeError("tool exploded")

        calls = [
            {"id": "call_0", "type": "function", "function": {"name": "web_search", "arguments": "{}"}}
        ]
        with patch("app.chat_runtime.execute_tool", boom):
            jobs = await _drain_tools(calls, diag=self._diag())
        self.assertEqual(jobs[0].trace["kind"], "internal_exception")
        rows = tool_failures.recent(kind="internal_exception")
        self.assertEqual(len(rows), 1)
        self.assertIn("RuntimeError", rows[0]["exception"])
        self.assertIn("tool exploded", rows[0]["exception"])

    async def test_over_call_cap_recorded(self):
        async def fake_execute(name, arguments, **kwargs):
            return "", {"name": name, "status": "done"}

        calls = [
            {"id": f"call_{i}", "type": "function", "function": {"name": "web_search", "arguments": "{}"}}
            for i in range(MAX_CALLS_PER_ROUND + 1)
        ]
        with patch("app.chat_runtime.execute_tool", fake_execute):
            jobs = await _drain_tools(calls, diag=self._diag())
        self.assertEqual(len(jobs), MAX_CALLS_PER_ROUND + 1)
        rows = tool_failures.recent(kind="over_call_cap")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["tool_name"], "web_search")

    async def test_coding_steps_have_no_call_cap(self):
        async def fake_execute(name, arguments, **kwargs):
            return "", {"name": name, "status": "done"}

        calls = [
            {"id": f"call_{i}", "type": "function", "function": {"name": "grep", "arguments": "{}"}}
            for i in range(MAX_CALLS_PER_ROUND + 5)
        ]
        with patch("app.chat_runtime.execute_tool", fake_execute):
            jobs = await _drain_tools(calls, diag=self._diag(), coding=True)
        self.assertEqual(len(jobs), MAX_CALLS_PER_ROUND + 5)
        self.assertTrue(all(j.trace.get("status") != "error" for j in jobs))
        self.assertEqual(len(tool_failures.recent(kind="over_call_cap")), 0)

    async def test_unknown_tool_trace_is_recorded(self):
        async def fake_execute(name, arguments, **kwargs):
            return "Unknown tool.", {"name": name, "status": "error", "kind": "unknown_tool", "error": "unknown tool"}

        calls = [
            {"id": "call_0", "type": "function", "function": {"name": "search_the_web", "arguments": "{}"}}
        ]
        with patch("app.chat_runtime.execute_tool", fake_execute):
            jobs = await _drain_tools(calls, diag=self._diag())
        self.assertEqual(jobs[0].trace["kind"], "unknown_tool")
        rows = tool_failures.recent(kind="unknown_tool")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["tool_name"], "search_the_web")


class StreamIntegrationTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        _fresh_db()

    async def test_failed_and_malformed_calls_are_logged(self):
        bodies = [
            FakeResult(
                {
                    "choices": [
                        {
                            "message": {
                                "content": "",
                                "tool_calls": [
                                    {
                                        "id": "call_ok",
                                        "type": "function",
                                        "function": {"name": "web_search", "arguments": '{"query":"x"}'},
                                    },
                                    {
                                        "id": "call_bad",
                                        "type": "function",
                                        "function": {"name": "", "arguments": "{}"},
                                    },
                                ],
                            },
                            "finish_reason": "tool_calls",
                        }
                    ]
                }
            ),
            FakeResult({"choices": [{"message": {"content": "done"}, "finish_reason": "stop"}]}),
        ]
        seen = []

        async def invoke(request, user, payload, **_kwargs):
            seen.append(dict(payload))
            return {"id": 1, "key_id": None}, FakeSrc(), "up-1", "test-model", bodies[len(seen) - 1]

        async def fake_execute(name, arguments, **kwargs):
            return "Tool error: no valid query.", {
                "name": name,
                "status": "error",
                "kind": "no_valid_query",
                "error": "Tool error: no valid query.",
            }

        request = _mk_request()
        with patch("app.chat_runtime.api.invoke_chat", invoke), patch(
            "app.chat_runtime.api._safe_record_usage", lambda *a, **k: None
        ), patch("app.chat_runtime.chats.get_user_context", return_value={"instructions": "", "profile": ""}), patch(
            "app.chat_runtime.execute_tool", fake_execute
        ):
            resp = await stream_dashboard_chat(
                request,
                {"id": 1, "key_id": None},
                {"model": "test-model", "messages": [{"role": "user", "content": "hi"}], "tools": ["web_search"]},
            )
            async for _ in resp.body_iterator:
                pass
        rows = tool_failures.recent()
        kinds = sorted(row["error_kind"] for row in rows)
        self.assertEqual(kinds, ["malformed_call", "no_valid_query"])
        by_kind = {row["error_kind"]: row for row in rows}
        self.assertEqual(by_kind["no_valid_query"]["call_id"], "call_ok")
        self.assertEqual(json.loads(by_kind["no_valid_query"]["arguments"])["query"], "x")
        self.assertEqual(by_kind["malformed_call"]["call_id"], "call_bad")
        self.assertEqual(by_kind["malformed_call"]["model"], "test-model")
        self.assertEqual(by_kind["malformed_call"]["user_text"], "hi")
        # The malformed call never reached the provider round trip; the valid one did.
        self.assertEqual(by_kind["no_valid_query"]["upstream_model"], "up-1")


class AdminEndpointTest(unittest.TestCase):
    def setUp(self):
        _fresh_db()
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from starlette.middleware.sessions import SessionMiddleware

        from app import accounts, web
        from app.config import settings

        self.accounts = accounts
        self.email = f"admin-{__import__('uuid').uuid4().hex}@test.invalid"
        accounts.create_user(self.email, "password12", admin=True)
        app = FastAPI()
        app.add_middleware(
            SessionMiddleware,
            secret_key=settings.app_secret,
            https_only=True,
            same_site="strict",
        )
        app.include_router(web.router)
        self.client = TestClient(app, base_url="https://testserver")
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)

    def _login(self):
        page = self.client.get("/login")
        import re

        match = re.search(r'name="csrf_token" value="([^"]+)"', page.text)
        if not match:
            match = re.search(r'name="csrf-token" content="([^"]+)"', page.text)
        self.assertIsNotNone(match)
        posted = self.client.post(
            "/login",
            data={"email": self.email, "password": "password12", "csrf_token": match.group(1)},
            follow_redirects=False,
        )
        self.assertEqual(posted.status_code, 303)

    def test_requires_admin(self):
        res = self.client.get("/admin/tool-failures", follow_redirects=False)
        self.assertEqual(res.status_code, 401)

    def test_pack_json(self):
        self._login()
        tool_failures.record(
            tool_name="web_search",
            error="Search failed. Try again shortly.",
            kind="upstream_error",
            model="test-model",
            user_text="hello",
        )
        res = self.client.get("/admin/tool-failures?limit=10&hours=24")
        self.assertEqual(res.status_code, 200)
        pack = res.json()
        self.assertIn("guide", pack)
        self.assertEqual(pack["summary"]["total"], 1)
        self.assertEqual(pack["recent"][0]["model"], "test-model")
        self.assertEqual(pack["fingerprints"][0]["n"], 1)
        filtered = self.client.get("/admin/tool-failures?kind=nope")
        self.assertEqual(filtered.json()["recent"], [])


if __name__ == "__main__":
    unittest.main()
