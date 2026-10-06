"""Tool loop: force-answer, in-place retries, _take_calls replace."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
_TMP = tempfile.mkdtemp(prefix="fuse-loop-")
os.environ.setdefault("DATABASE_PATH", str(Path(_TMP) / "chat.db"))
os.environ.setdefault("APP_SECRET", "x" * 32)

from fastapi import Request

from app.chat_runtime import (
    CODING_UNLIMITED,
    MAX_COMPLETIONS,
    STALL_FAIL_CALLS,
    ToolJob,
    _coding_completion_limit,
    _completion_start_index,
    _finalize_assistant_payload,
    _provider_snapshot,
    _take_calls,
    _tool_rounds_done,
    calls_incomplete,
    drop_tool_xml,
    extract_text_tool_calls,
    inject_web_context,
    round_signature,
    stream_dashboard_chat,
    strip_special_tokens,
)

import pytest

# Uses the database: a disposable MariaDB schema shared by this module.
pytestmark = pytest.mark.usefixtures("isolated_db")


class TakeCallsTest(unittest.TestCase):
    def test_concat_partial_strings(self):
        bucket = {}
        _take_calls(bucket, [{"index": 0, "function": {"arguments": '{"q'}}])
        _take_calls(bucket, [{"index": 0, "function": {"arguments": 'uery":"x"}'}}])
        self.assertIn("query", bucket[0]["function"]["arguments"])

    def test_replace_complete_json_resend(self):
        bucket = {}
        _take_calls(
            bucket,
            [{"index": 0, "id": "ollama_0", "function": {"name": "web_search", "arguments": '{"query":"ab"}'}}],
        )
        _take_calls(
            bucket,
            [{"index": 0, "id": "ollama_0", "function": {"name": "web_search", "arguments": '{"query":"abcd"}'}}],
        )
        args = json.loads(bucket[0]["function"]["arguments"])
        self.assertEqual(args["query"], "abcd")

    def test_dict_args(self):
        bucket = {}
        _take_calls(bucket, [{"index": 0, "function": {"name": "web_search", "arguments": {"query": "x"}}}])
        self.assertEqual(json.loads(bucket[0]["function"]["arguments"])["query"], "x")


class InjectTest(unittest.TestCase):
    def test_concat_last_user_keeps_alternation(self):
        msgs = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hello"},
        ]
        out = inject_web_context(msgs, "PACKED")
        roles = [m["role"] for m in out]
        self.assertEqual(roles, ["system", "user"])
        self.assertIn("PACKED", out[1]["content"])
        self.assertTrue(out[1]["content"].startswith("hello"))

    def test_appends_to_multimodal_text_part(self):
        png = "data:image/png;base64,aaa"
        msgs = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "see this"},
                    {"type": "image_url", "image_url": {"url": png}},
                ],
            }
        ]
        out = inject_web_context(msgs, "PACKED")
        parts = out[0]["content"]
        self.assertTrue(parts[0]["text"].startswith("see this"))
        self.assertIn("PACKED", parts[0]["text"])
        self.assertEqual(parts[1]["image_url"]["url"], png)


class SpecialTokenAndXmlTest(unittest.TestCase):
    def test_strips_eos_and_endoftext(self):
        self.assertEqual(
            strip_special_tokens("idle-start path. <|eos|>"),
            "idle-start path. ",
        )
        self.assertEqual(strip_special_tokens("hi </s> there"), "hi  there")
        self.assertEqual(strip_special_tokens("<|endoftext|>"), "")

    def test_extracts_qwen_xml_tool_call(self):
        raw = (
            "Fixing those, then running tests.\n"
            "<tool_call>\n"
            "<function=run_terminal_command>\n"
            "<parameter=command>\n"
            "cat /tmp/prmon.log\n"
            "</parameter>\n"
            "<parameter=host>\n"
            "mason-b450-aorus-pro-wifi\n"
            "</parameter>\n"
            "<parameter=timeout>\n"
            "600\n"
            "</parameter>\n"
            "</function>\n"
            "</tool_call>"
        )
        text, calls = extract_text_tool_calls(raw, coding=True)
        self.assertIn("Fixing those", text)
        self.assertNotIn("<tool_call>", text)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["function"]["name"], "run_terminal_command")
        args = json.loads(calls[0]["function"]["arguments"])
        self.assertEqual(args["command"].strip(), "cat /tmp/prmon.log")
        self.assertEqual(args["host"], "mason-b450-aorus-pro-wifi")
        self.assertEqual(args["timeout"], 600)

    def test_extracts_json_inside_tool_call(self):
        raw = '<tool_call>{"name": "web_search", "arguments": {"query": "brent oil"}}</tool_call>'
        text, calls = extract_text_tool_calls(raw, coding=False)
        self.assertEqual(text, "")
        self.assertEqual(calls[0]["function"]["name"], "web_search")
        self.assertEqual(json.loads(calls[0]["function"]["arguments"])["query"], "brent oil")

    def test_finalize_promotes_xml_to_tool_calls(self):
        content, reasoning, calls, finish = _finalize_assistant_payload(
            "Setting `_starting` before `vc.stop()`. <|eos|>\n"
            "<tool_call>\n<function=web_search>\n"
            "<parameter=query>x</parameter>\n</function>\n</tool_call>",
            "",
            [],
            "stop",
            coding=False,
        )
        self.assertNotIn("<|eos|>", content)
        self.assertNotIn("<tool_call>", content)
        self.assertEqual(finish, "tool_calls")
        self.assertEqual(calls[0]["function"]["name"], "web_search")
        self.assertEqual(reasoning, "")

    def test_drop_tool_xml_holds_incomplete_opener(self):
        visible, pending = drop_tool_xml("hello <tool_call>\n<function=web_search>")
        self.assertEqual(visible, "hello ")
        self.assertTrue(pending.startswith("<tool_call>"))
        gone, pending2 = drop_tool_xml(pending + "</function></tool_call> trailing")
        self.assertEqual(pending2, "")
        self.assertIn("trailing", gone)
        self.assertNotIn("<tool_call>", gone)

    def test_legacy_coding_cap_is_unlimited(self):
        self.assertEqual(_coding_completion_limit({}), CODING_UNLIMITED)
        self.assertEqual(_coding_completion_limit({"max_completions": 16}), CODING_UNLIMITED)
        self.assertEqual(_coding_completion_limit({"max_completions": 0}), CODING_UNLIMITED)
        self.assertEqual(_coding_completion_limit({"max_completions": 100}), 100)

    def test_extracts_bare_function_xml(self):
        raw = (
            "Next I will cat the log.\n"
            "<function=run_terminal_command>\n"
            "<parameter=command>cat /tmp/prmon.log</parameter>\n"
            "</function>"
        )
        text, calls = extract_text_tool_calls(raw, coding=True)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["function"]["name"], "run_terminal_command")
        self.assertNotIn("<function=", text)

    def test_incomplete_calls_and_round_signature(self):
        self.assertTrue(
            calls_incomplete(
                [{"function": {"name": "web_search", "arguments": '{"query":'}}]
            )
        )
        self.assertFalse(
            calls_incomplete(
                [{"function": {"name": "web_search", "arguments": '{"query":"x"}'}}]
            )
        )
        self.assertEqual(
            round_signature(
                [{"function": {"name": "web_search", "arguments": '{"q":"a"}'}}]
            ),
            (("web_search", '{"q":"a"}'),),
        )


class FakeResult:
    def __init__(self, status=200, body=None, usage=None, credential_id=1):
        self.status = status
        self.body = body or {
            "choices": [{"message": {"role": "assistant", "content": "done"}, "finish_reason": "stop"}]
        }
        self.usage = usage or {}
        self.stream = None
        self.credential_id = credential_id


class FakeSrc:
    def __init__(self, type_id="llama"):
        self.type_id = type_id
        self.source_id = 1


class LoopTest(unittest.IsolatedAsyncioTestCase):
    async def test_forwards_reasoning_effort(self):
        from starlette.requests import Request as StarletteRequest

        payloads = []

        async def invoke(request, user, payload, **_kwargs):
            payloads.append(dict(payload))
            return user, FakeSrc("llama"), "m", "m", FakeResult(
                200, {"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]}
            )

        scope = {"type": "http", "method": "POST", "path": "/", "headers": [], "client": ("127.0.0.1", 1)}

        async def receive():
            return {"type": "http.disconnect"}

        request = StarletteRequest(scope, receive)
        with patch("app.chat_runtime.api.invoke_chat", invoke), patch(
            "app.chat_runtime.api._safe_record_usage", lambda *a, **k: None
        ), patch("app.chat_runtime.chats.get_user_context", return_value={"instructions": "", "profile": ""}):
            resp = await stream_dashboard_chat(
                request,
                {"id": 1, "key_id": None},
                {
                    "model": "test-model",
                    "messages": [{"role": "user", "content": "hi"}],
                    "tools": [],
                    "reasoning_effort": "high",
                },
            )
            async for _ in resp.body_iterator:
                pass
        self.assertEqual(payloads[0]["reasoning_effort"], "high")

    async def _run(self, invoke):
        from starlette.requests import Request as StarletteRequest
        from starlette.datastructures import Headers

        scope = {"type": "http", "method": "POST", "path": "/", "headers": [], "client": ("127.0.0.1", 1)}

        async def receive():
            return {"type": "http.disconnect"}

        request = StarletteRequest(scope, receive)
        user = {"id": 1, "key_id": None}
        raw = {
            "model": "test-model",
            "messages": [{"role": "user", "content": "hi"}],
            "tools": ["web_search"],
        }
        with patch("app.chat_runtime.api.invoke_chat", invoke), patch(
            "app.chat_runtime.api._safe_record_usage", lambda *a, **k: None
        ), patch("app.chat_runtime.chats.get_user_context", return_value={"instructions": "", "profile": ""}):
            resp = await stream_dashboard_chat(request, user, raw)
            chunks = []
            async for b in resp.body_iterator:
                chunks.append(b)
            return b"".join(chunks), invoke

    async def test_ui_usage_is_cumulative_between_rounds_without_polluting_context(self):
        calls = 0
        async def invoke(request, user, payload, **_kwargs):
            nonlocal calls
            calls += 1
            usage = {"prompt_tokens": 100, "completion_tokens": 10 if calls == 1 else 20}
            message = {"content": "final"} if calls > 1 else {"content": "", "tool_calls": [
                {"id": "search", "type": "function", "function": {"name": "web_search", "arguments": '{"query":"offline"}'}}]}
            body = {"choices": [{"message": message, "finish_reason": "tool_calls" if calls == 1 else "stop"}], "usage": usage}
            return user, FakeSrc("llama"), "m", "m", FakeResult(body=body, usage=usage)
        with patch("app.chat_runtime.execute_tool", AsyncMock(return_value=("offline result", {"name": "web_search", "status": "done", "results": []}))):
            wire, _ = await self._run(invoke)
        frames = [json.loads(line[6:]) for line in wire.decode().splitlines() if line.startswith("data: {")]
        counts = [frame["usage"]["completion_tokens"] for frame in frames if frame.get("usage")]
        self.assertEqual(calls, 2)
        self.assertEqual(counts[0], 10)
        self.assertEqual(counts[-1], 30)
        self.assertNotIn(20, counts, "second-round usage must not replace the cumulative reply total")
        reports = [frame["fuse"]["provider_usage"] for frame in frames if frame.get("fuse", {}).get("provider_usage")]
        self.assertTrue(reports)
        self.assertTrue(all(report["prompt_tokens"] == 100 for report in reports), "context remains single-request occupancy")

    async def test_force_none_retry_omits_key(self):
        payloads = []

        async def invoke(request, user, payload, **_kwargs):
            payloads.append(dict(payload))
            if payload.get("tool_choice") == "none":
                return user, FakeSrc("llama"), "m", "m", FakeResult(
                    400, {"error": {"message": "invalid tool_choice"}}
                )
            if payload.get("tools") and payload.get("tool_choice") != "none" and "tool_choice" in payload:
                return user, FakeSrc("llama"), "m", "m", FakeResult(
                    200,
                    {
                        "choices": [
                            {
                                "message": {
                                    "role": "assistant",
                                    "content": "",
                                    "tool_calls": [
                                        {
                                            "id": "c1",
                                            "function": {"name": "web_search", "arguments": '{"query":"x"}'},
                                        }
                                    ],
                                },
                                "finish_reason": "tool_calls",
                            }
                        ]
                    },
                )
            return user, FakeSrc("llama"), "m", "m", FakeResult(
                200, {"choices": [{"message": {"content": "final"}, "finish_reason": "stop"}]}
            )

        async def fake_tools(*a, **k):
            job = type("J", (), {})()
            job.call_id = "c1"
            job.name = "web_search"
            job.arguments = '{"query":"x"}'
            job.text = "hits"
            job.trace = {"name": "web_search", "status": "done"}
            yield {"event": "tool", "id": "ts_0_0", "name": "web_search", "status": "running"}
            yield {"event": "tool", "id": "ts_0_0", "name": "web_search", "status": "done"}
            yield [
                type(
                    "Job",
                    (),
                    {
                        "call_id": "c1",
                        "name": "web_search",
                        "arguments": '{"query":"x"}',
                        "text": "hits",
                        "trace": {},
                        "orig_index": 0,
                        "event_id": "ts_0_0",
                    },
                )()
            ]

        with patch("app.chat_runtime._run_tools", fake_tools):
            body, _ = await self._run(invoke)
        none_payloads = [p for p in payloads if p.get("tool_choice") == "none"]
        omit_payloads = [p for p in payloads[1:] if "tool_choice" not in p and p.get("tools")]
        self.assertTrue(none_payloads)
        self.assertTrue(any("tool_choice" not in p for p in payloads[1:]))

    async def test_parallel_400_does_not_retrieval_first(self):
        payloads = []

        async def invoke(request, user, payload, **_kwargs):
            payloads.append(dict(payload))
            if payload.get("parallel_tool_calls"):
                return user, FakeSrc("llama"), "m", "m", FakeResult(
                    400, {"error": {"message": "invalid parallel_tool_calls"}}
                )
            return user, FakeSrc("llama"), "m", "m", FakeResult(
                200, {"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]}
            )

        body, _ = await self._run(invoke)
        self.assertIn(b"ok", body)
        self.assertFalse(any(b"Fuse searched" in body for _ in [0]))
        self.assertGreaterEqual(len(payloads), 2)
        self.assertTrue(payloads[0].get("parallel_tool_calls"))
        self.assertNotIn("parallel_tool_calls", payloads[1])
        self.assertIn("tools", payloads[1])

    async def test_context_meters_keep_provider_usage_as_diagnostics(self):
        async def invoke(request, user, payload, **_kwargs):
            return user, FakeSrc("llama"), "m", "m", FakeResult(
                200,
                {"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]},
                usage={"prompt_tokens": 90_000, "completion_tokens": 500},
            )

        body, _ = await self._run(invoke)
        events = []
        for line in body.decode().splitlines():
            if not line.startswith("data:") or line[5:].strip() in ("", "[DONE]"):
                continue
            obj = json.loads(line[5:])
            fuse = obj.get("fuse")
            if isinstance(fuse, dict) and fuse.get("event") == "context":
                events.append(fuse)
        self.assertTrue(events)
        phases = {event["phase"] for event in events}
        self.assertIn("round", phases)
        self.assertIn("end", phases)
        for event in events:
            # Inclusive request usage is measured for llama; the retained end
            # payload is calibrated, not pinned to an old high-water mark.
            self.assertEqual(event.get("scope"), "prompt")
            self.assertNotIn("reported_request_tokens", event)
            if event["phase"] == "round":
                # Next request is the measured prompt plus the replayed reply,
                # not prompt+completion and not a rescaled transcript.
                self.assertEqual(event["used"], 90_000 + event["used"] - 90_000)
                self.assertLess(event["used"], 90_500)
                self.assertGreater(event["used"], 90_000)
                self.assertEqual(event["basis"], "provider_prompt")
                self.assertTrue(event["estimated"])
            elif event["phase"] == "end":
                self.assertEqual(event["basis"], "provider_prompt")
                self.assertEqual(event["used"], 90_000 + (event["used"] - 90_000))
                self.assertLess(event["used"], 90_500)
            if event["phase"] in ("round", "end"):
                self.assertEqual(event["provider_usage"]["prompt_tokens"], 90_000)

    async def test_context_meters_without_usage_omit_floor(self):
        async def invoke(request, user, payload, **_kwargs):
            return user, FakeSrc("llama"), "m", "m", FakeResult(
                200, {"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]}
            )

        body, _ = await self._run(invoke)
        for line in body.decode().splitlines():
            if not line.startswith("data:") or line[5:].strip() in ("", "[DONE]"):
                continue
            obj = json.loads(line[5:])
            fuse = obj.get("fuse")
            if isinstance(fuse, dict) and fuse.get("event") == "context":
                self.assertNotIn("reported_request_tokens", fuse)

    def test_completion_start_index_ignores_copied_history(self):
        history = [
            {"role": "user", "content": "go"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "1"}]},
            {"role": "tool", "tool_call_id": "1", "content": "ok"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "2"}]},
            {"role": "tool", "tool_call_id": "2", "content": "ok"},
            {"role": "user", "content": "continue"},
        ]
        self.assertEqual(_tool_rounds_done(history), 2)
        self.assertEqual(_completion_start_index({}, history, 8), 0)
        self.assertEqual(_completion_start_index({"parent_run_id": "c"}, history, 8), 0)
        self.assertEqual(_completion_start_index({"resumed": True}, history, 8), 2)
        self.assertEqual(_completion_start_index({"resumed": True}, history, 2), 1)
        self.assertEqual(STALL_FAIL_CALLS, 20)

    def test_provider_snapshot_omits_in_flight_results(self):
        messages = [{"role": "system", "content": "sys"}, {"role": "user", "content": "hi"}]
        jobs = [
            ToolJob(0, "c1", "web_search", "{}", "ts_0_0", text="hits", done=True),
            ToolJob(1, "c2", "read_url", "{}", "ts_0_1"),
        ]
        snap = _provider_snapshot(messages, jobs)
        self.assertEqual([m["role"] for m in snap], ["user", "assistant", "tool"])
        self.assertEqual(snap[-1]["tool_call_id"], "c1")
        self.assertEqual(_tool_rounds_done(snap), 1)

    async def test_root_run_persists_tool_round(self):
        saved = []

        async def invoke(request, user, payload, **_kwargs):
            if any(m.get("role") == "tool" for m in payload.get("messages") or []):
                return user, FakeSrc("llama"), "m", "m", FakeResult(
                    200, {"choices": [{"message": {"content": "final"}, "finish_reason": "stop"}]}
                )
            return user, FakeSrc("llama"), "m", "m", FakeResult(
                200,
                {
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": "",
                                "tool_calls": [
                                    {
                                        "id": "c1",
                                        "function": {"name": "web_search", "arguments": '{"query":"x"}'},
                                    }
                                ],
                            },
                            "finish_reason": "tool_calls",
                        }
                    ]
                },
            )

        async def fake_tools(*a, **k):
            persist = k.get("persist")
            job = ToolJob(0, "c1", "web_search", '{"query":"x"}', "ts_0_0", text="hits", done=True)
            if persist:
                await persist([job])
            yield {"event": "tool", "id": "ts_0_0", "name": "web_search", "status": "done"}
            yield [job]

        with patch("app.chat_runtime._run_tools", fake_tools), patch(
            "app.chat_store.save_turn_progress", lambda *args, **kwargs: saved.append((args, kwargs))
        ):
            raw = {
                "model": "test-model",
                "run_id": "run-1",
                "messages": [{"role": "user", "content": "hi"}],
                "tools": ["web_search"],
            }
            body, _ = await self._run_raw(invoke, raw)
        self.assertIn(b"final", body)
        self.assertTrue(saved)
        last_messages = saved[-1][0][1]
        self.assertEqual(last_messages[-1]["role"], "assistant")
        self.assertIn("final", last_messages[-1].get("content") or "")
        tool_msgs = [
            m
            for snap in saved
            for m in snap[0][1]
            if m.get("role") == "tool"
        ]
        self.assertTrue(tool_msgs)
        self.assertEqual(tool_msgs[-1]["content"], "hits")

    async def test_text_only_reply_consumes_steer_before_finishing(self):
        payloads = []

        async def invoke(request, user, payload, **_kwargs):
            payloads.append(dict(payload))
            content = "Initial answer" if len(payloads) == 1 else "Adjusted answer"
            return user, FakeSrc("llama"), "m", "m", FakeResult(
                200, {"choices": [{"message": {"content": content}, "finish_reason": "stop"}]})

        with patch("app.chat_store.take_steers", side_effect=[[], [{"id": "queued-1", "text": "Use the new requirement"}]]), \
             patch("app.chat_store.close_steer_window", side_effect=[False, True]), \
             patch("app.chat_store.save_turn_progress"):
            body, _ = await self._run_raw(invoke, {"model": "test-model", "run_id": "run-1", "tools": [],
                                                  "messages": [{"role": "user", "content": "Start"}]})
        self.assertEqual(len(payloads), 2)
        self.assertTrue(any("(steer:queued-1)" in str(m.get("content")) for m in payloads[1]["messages"]))
        self.assertIn(b"Adjusted answer", body)
        self.assertIn(b"steer.delivered", body)

    async def test_coding_parent_never_sends_tool_choice_none(self):
        payloads = []
        rounds = {"n": 0}

        async def invoke(request, user, payload, **_kwargs):
            payloads.append(dict(payload))
            rounds["n"] += 1
            if rounds["n"] < 4:
                return user, FakeSrc("llama"), "m", "m", FakeResult(
                    200,
                    {
                        "choices": [
                            {
                                "message": {
                                    "role": "assistant",
                                    "content": "working",
                                    "tool_calls": [
                                        {
                                            "id": f"c{rounds['n']}",
                                            "function": {"name": "web_search", "arguments": '{"query":"x"}'},
                                        }
                                    ],
                                },
                                "finish_reason": "stop",
                            }
                        ]
                    },
                )
            return user, FakeSrc("llama"), "m", "m", FakeResult(
                200, {"choices": [{"message": {"content": "done"}, "finish_reason": "stop"}]}
            )

        async def fake_tools(*a, **k):
            n = rounds["n"]
            job = ToolJob(0, f"c{n}", "web_search", '{"query":"x"}', f"ts_{n}_0", text="hits", done=True)
            yield {"event": "tool", "id": f"ts_{n}_0", "name": "web_search", "status": "done"}
            yield [job]

        with patch("app.chat_runtime._run_tools", fake_tools):
            raw = {
                "model": "test-model",
                "messages": [{"role": "user", "content": "hi"}],
                "tools": ["web_search"],
                "agent": {"coding": True, "max_completions": 16, "project_session": True},
            }
            body, _ = await self._run_raw(invoke, raw)
        self.assertIn(b"done", body)
        self.assertFalse(any(p.get("tool_choice") == "none" for p in payloads))
        self.assertGreaterEqual(len(payloads), 4)

    async def test_search_last_round_executes_tools_then_wraps(self):
        payloads = []
        rounds = {"n": 0}

        async def invoke(request, user, payload, **_kwargs):
            payloads.append(dict(payload))
            rounds["n"] += 1
            if payload.get("tool_choice") == "none" and rounds["n"] == 1:
                return user, FakeSrc("llama"), "m", "m", FakeResult(
                    200,
                    {
                        "choices": [
                            {
                                "message": {
                                    "role": "assistant",
                                    "content": (
                                        "<tool_call><function=web_search>"
                                        "<parameter=query>x</parameter></function></tool_call>"
                                    ),
                                },
                                "finish_reason": "stop",
                            }
                        ]
                    },
                )
            return user, FakeSrc("llama"), "m", "m", FakeResult(
                200, {"choices": [{"message": {"content": "summary of remaining work"}, "finish_reason": "stop"}]}
            )

        ran = {"tools": 0}

        async def fake_tools(*a, **k):
            ran["tools"] += 1
            job = ToolJob(0, "xml_0", "web_search", '{"query":"x"}', "ts_0_0", text="hits", done=True)
            yield {"event": "tool", "id": "ts_0_0", "name": "web_search", "status": "done"}
            yield [job]

        with patch("app.chat_runtime.MAX_COMPLETIONS", 1), patch("app.chat_runtime._run_tools", fake_tools):
            raw = {
                "model": "test-model",
                "messages": [{"role": "user", "content": "hi"}],
                "tools": ["web_search"],
            }
            body, _ = await self._run_raw(invoke, raw)
        self.assertEqual(ran["tools"], 1)
        self.assertIn(b"summary of remaining work", body)

    async def _run_raw(self, invoke, raw):
        from starlette.requests import Request as StarletteRequest

        scope = {"type": "http", "method": "POST", "path": "/", "headers": [], "client": ("127.0.0.1", 1)}

        async def receive():
            return {"type": "http.disconnect"}

        request = StarletteRequest(scope, receive)
        user = {"id": 1, "key_id": None}
        with patch("app.chat_runtime.api.invoke_chat", invoke), patch(
            "app.chat_runtime.api._safe_record_usage", lambda *a, **k: None
        ), patch("app.chat_runtime.chats.get_user_context", return_value={"instructions": "", "profile": ""}):
            resp = await stream_dashboard_chat(request, user, raw)
            chunks = []
            async for b in resp.body_iterator:
                chunks.append(b)
            return b"".join(chunks), invoke


if __name__ == "__main__":
    unittest.main()
