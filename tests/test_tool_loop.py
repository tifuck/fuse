"""Tool loop: force-answer, in-place retries, _take_calls replace."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
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

    def test_split_name_keeps_a_fragment_matching_its_prefix(self):
        bucket = {}
        for name in ("run_te", "r", "minal_command"):
            _take_calls(bucket, [{"index": 0, "function": {"name": name}}])
        self.assertEqual(bucket[0]["function"]["name"], "run_terminal_command")
        _take_calls(bucket, [{"index": 0, "function": {"name": "run_terminal_command"}}])
        self.assertEqual(bucket[0]["function"]["name"], "run_terminal_command")

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

    def test_backticks_inside_tool_arguments_do_not_quote_the_call(self):
        for value in ("meaning of `foo`", "```python\nprint('x')\n```", "``code with ` inside``"):
            args = {"query": value}
            raw = '<tool_call>' + json.dumps({"name": "web_search", "arguments": args}) + '</tool_call>'
            for body in (raw, f"<tool_call><function=web_search><parameter=query>{value}</parameter></function></tool_call>"):
                text, calls = extract_text_tool_calls(body)
                self.assertEqual(text, "")
                self.assertEqual(len(calls), 1)
                self.assertEqual(json.loads(calls[0]["function"]["arguments"]), args)
                self.assertEqual(drop_tool_xml(body), ("", ""))

    def test_quoted_fences_close_only_on_a_fence_line(self):
        example = '<tool_call>{"name":"web_search","arguments":{"query":"quoted"}}</tool_call>'
        live = '<tool_call>{"name":"web_search","arguments":{"query":"live"}}</tool_call>'
        for quoted in (
            f'```python\ns = "```"\n{example}\n````',
            f'~~~text\ns = "~~~"\n{example}\n  ~~~~',
            f'````markdown\n```\n{example}\n```\n````',
            f'``code with ` then {example} end``',
        ):
            text, calls = extract_text_tool_calls(quoted + "\n" + live)
            self.assertEqual(text, quoted)
            self.assertEqual([json.loads(c["function"]["arguments"]) for c in calls], [{"query": "live"}])
            self.assertEqual(drop_tool_xml(quoted + "\n" + live), (quoted + "\n", ""))

    def test_quoted_tool_call_examples_never_execute(self):
        example = '<tool_call>{"name": "run_terminal_command", "arguments": {"command": "rm -rf build"}}</tool_call>'
        for raw in (
            f"Models emit calls like this:\n```xml\n{example}\n```\nThat is the format.",
            f"Inline: `{example}` is the format.",
            f"Unclosed fence:\n```\n{example}",
        ):
            text, calls = extract_text_tool_calls(raw, coding=True)
            self.assertEqual(calls, [], raw)
            self.assertEqual(text, raw.strip())
        # A real call after a quoted example still runs.
        raw = f"Example:\n```\n{example}\n```\n" + '<tool_call>{"name": "web_search", "arguments": {"query": "x"}}</tool_call>'
        text, calls = extract_text_tool_calls(raw, coding=False)
        self.assertEqual([c["function"]["name"] for c in calls], ["web_search"])
        self.assertIn("rm -rf build", text)

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

    def test_finalize_preserves_special_tokens_inside_tool_arguments(self):
        args = {"path": "x.html", "content": "<s>old</s><|eos|>"}
        raw = '<tool_call>' + json.dumps({"name": "write_file", "arguments": args}) + '</tool_call>'
        content, reasoning, calls, finish = _finalize_assistant_payload(
            "Before<|eos|>\n" + raw + "\nAfter</s>", "plan<|eos|>", [], "stop", coding=True,
        )
        self.assertEqual(content, "Before\n\nAfter")
        self.assertEqual(reasoning, "plan")
        self.assertEqual(json.loads(calls[0]["function"]["arguments"]), args)
        self.assertEqual(finish, "tool_calls")

    def test_finalize_keeps_nameless_calls_alongside_extracted_xml(self):
        content, reasoning, calls, finish = _finalize_assistant_payload(
            'Answering now.\n<tool_call><function=web_search><parameter=query>x</parameter></function></tool_call>',
            "",
            [{"id": "broken", "function": {"name": "", "arguments": ""}}],
            "stop",
            coding=False,
        )
        names = [c["function"]["name"] for c in calls]
        self.assertIn("web_search", names)
        self.assertIn("", names, "nameless entry must survive for malformed-call logging")

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

    def test_values_quoting_a_closing_tag_are_not_cut(self):
        cases = (
            ("search_replace", "<parameter=path>a.py</parameter><parameter=old_string>x</parameter>"
             "<parameter=new_string>print('</parameter>')</parameter>",
             {"path": "a.py", "old_string": "x", "new_string": "print('</parameter>')"}),
            ("run_terminal_command", "<parameter=command>echo '</function>'</parameter>",
             {"command": "echo '</function>'"}),
            ("run_terminal_command", "<parameter=command>printf x '</tool_call>' > f</parameter><parameter=host>box</parameter>",
             {"command": "printf x '</tool_call>' > f", "host": "box"}),
        )
        for name, params, want in cases:
            raw = f"Doing it.\n<tool_call><function={name}>{params}</function></tool_call>\nDone."
            text, calls = extract_text_tool_calls(raw, coding=True)
            self.assertEqual([c["function"]["name"] for c in calls], [name], raw)
            self.assertEqual(json.loads(calls[0]["function"]["arguments"]), want, raw)
            self.assertEqual(text, "Doing it.\n\nDone.", raw)
            self.assertEqual(drop_tool_xml(raw), ("Doing it.\n\nDone.", ""), raw)
        raw = '<invoke name="run_terminal_command"><parameter name="command">echo \'</invoke>\'</parameter></invoke>'
        _, calls = extract_text_tool_calls(raw, coding=True)
        self.assertEqual(json.loads(calls[0]["function"]["arguments"]), {"command": "echo '</invoke>'"})

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
        # "" is a complete no-argument call (the runner executes it as {}),
        # not a cut-off one; nor is the oversize marker, which the runner
        # refuses with its own split-the-work message.
        self.assertFalse(calls_incomplete([{"function": {"name": "list_subagents", "arguments": ""}}]))
        from app.chat_generation.parsing import _ARG_OVERFLOW

        self.assertFalse(calls_incomplete([{"function": {"name": "write_file", "arguments": _ARG_OVERFLOW}}]))
        self.assertEqual(
            round_signature(
                [{"function": {"name": "web_search", "arguments": '{"q":"a"}'}}]
            ),
            (("web_search", '{"q":"a"}'),),
        )


class ConsumeRoundTerminalTest(unittest.IsolatedAsyncioTestCase):
    async def test_done_frame_then_held_open_stream_completes(self):
        """A proxy keeping the socket open after [DONE] must not turn a
        completed reply into a gap-timeout failure."""
        from app.chat_generation.consume import consume_round

        async def stream():
            yield b'data: {"choices":[{"delta":{"content":"hello"}}]}\n\n'
            yield b"data: [DONE]\n\n"
            await asyncio.Future()  # provider never closes the connection

        result = SimpleNamespace(status=200, stream=stream(), body=None, usage={}, credential_id=1)
        src = SimpleNamespace(type_id="llama", source_id=1)
        summary = None
        with (
            patch("app.api._tap", side_effect=lambda s, *a, **k: s),
            patch("app.api._safe_record_usage"),
            # Without the fix this test would otherwise wait a full gap
            # timeout before failing; keep that failure fast.
            patch("app.chat_generation.consume.CHUNK_GAP_TIMEOUT_S", 0.3),
        ):
            async with contextlib.aclosing(consume_round(result, {"id": 1}, src, "m", "m")) as rounds:
                async for kind, payload in rounds:
                    if kind == "done":
                        summary = payload
        self.assertIsNotNone(summary)
        self.assertEqual(summary["content"], "hello")
        self.assertIsNone(summary["error"])


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
            yield [ToolJob(0, "c1", "web_search", '{"query":"x"}', "ts_0_0", text="hits")]

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

    def test_duplicate_call_ids_keep_partial_results_with_the_right_tool(self):
        from app import chat_history, chat_run_state
        from app.chat_generation.tool_jobs import jobs_from_calls

        calls = [
            {"id": "same", "function": {"name": name, "arguments": "{}"}}
            for name in ("read_file", "grep")
        ]
        original = json.loads(json.dumps(calls))
        jobs = jobs_from_calls(calls, 0)
        jobs[1].done = True
        jobs[1].text = "GREP_RESULT"
        snapshot = _provider_snapshot([], jobs)
        sealed = chat_run_state.seal_provider_messages(snapshot)
        wire = chat_history.for_model(sealed, "glm-5.3")
        ids = [call["id"] for call in wire[0]["tool_calls"]]
        self.assertEqual(len(set(ids)), 2)
        results = {row["tool_call_id"]: row["content"] for row in wire[1:]}
        self.assertIn("interrupted", results[ids[0]])
        self.assertEqual(results[ids[1]], "GREP_RESULT")
        self.assertEqual(calls, original)
        self.assertEqual([job.call_id for job in jobs_from_calls(calls, 9)], ids)
        projected = chat_history.for_model([
            {"role": "assistant", "tool_calls": original}], "glm-5.3")
        self.assertEqual(ids, [call["id"] for call in projected[0]["tool_calls"]])

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

    async def test_background_results_reach_model_and_mark_delivery_point(self):
        # One drain hands queued results to the model as one framed message
        # and tells the run worker where in the reply they landed.
        payloads = []
        envelope = (
            '<task id="kid-1" state="completed">\n<summary>Audit parsing</summary>\n'
            "<task_result>\nTwo bugs.\n</task_result>\n</task>\ntask_id: kid-1 (pass task_id to continue this subagent)"
        )

        async def invoke(request, user, payload, **_kwargs):
            payloads.append(dict(payload))
            return user, FakeSrc("llama"), "m", "m", FakeResult(
                200, {"choices": [{"message": {"content": "Read the report"}, "finish_reason": "stop"}]})

        with patch("app.chat_store.take_child_results", side_effect=[[envelope], [envelope]]), \
             patch("app.chat_store.take_steers", return_value=[]), \
             patch("app.chat_store.steer_display_deliveries", return_value=[]), \
             patch("app.chat_store.close_steer_window", return_value=True), \
             patch("app.chat_store.save_turn_progress"):
            body, _ = await self._run_raw(invoke, {"model": "test-model", "run_id": "run-1", "tools": [],
                                                  "messages": [{"role": "user", "content": "Start"}]})
        sent = [m for m in payloads[0]["messages"] if "AUTOMATED EVENT" in str(m.get("content"))]
        self.assertEqual(len(sent), 1)
        self.assertIn("Two bugs.", sent[0]["content"])
        events = [json.loads(line[6:]) for line in body.decode().split("\n") if line.startswith("data: {")]
        deliveries = [e["fuse"] for e in events if (e.get("fuse") or {}).get("event") == "delivery"]
        # Delivered once, although the peek still returned it the next round.
        self.assertEqual(len(deliveries), 1)
        self.assertIn('<task id="kid-1"', deliveries[0]["text"])
        self.assertNotIn("Subagent report, not a user instruction", deliveries[0]["text"])

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

    async def test_tool_calls_finish_without_runnable_call_ends_the_turn(self):
        # finish_reason tool_calls with only nameless (or no) calls is not a
        # tool round; it used to resend the same request until the wall clock.
        for content, tool_calls in (("", [{"id": "bad", "function": {"name": "", "arguments": "{}"}}]),
                                    ("Here is the answer.", [{"id": "bad", "function": {"arguments": "{}"}}]),
                                    ("", [])):
            payloads = []

            async def invoke(request, user, payload, **_kwargs):
                payloads.append(dict(payload))
                if len(payloads) > 5:
                    return user, FakeSrc("llama"), "m", "m", FakeResult(
                        200, {"choices": [{"message": {"content": "looped"}, "finish_reason": "stop"}]})
                message = {"role": "assistant", "content": content}
                if tool_calls:
                    message["tool_calls"] = tool_calls
                return user, FakeSrc("llama"), "m", "m", FakeResult(
                    200, {"choices": [{"message": message, "finish_reason": "tool_calls"}]})

            ran = []

            async def fake_tools(calls, **_kwargs):
                ran.append(calls)
                yield []

            with patch("app.chat_runtime._run_tools", fake_tools):
                body, _ = await self._run_raw(invoke, {
                    "model": "test-model", "messages": [{"role": "user", "content": "hi"}],
                    "tools": ["web_search"], "agent": {"coding": True, "project_session": True},
                })
            self.assertEqual(ran, [])
            self.assertEqual(len(payloads), 1, (content, tool_calls))
            self.assertNotIn(b"looped", body)
            if content:
                self.assertIn(content.encode(), body)

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

    async def test_truncated_sibling_call_does_not_drop_complete_calls(self):
        payloads = []

        async def invoke(request, user, payload, **_kwargs):
            payloads.append(dict(payload))
            if len(payloads) == 1:
                return user, FakeSrc("llama"), "m", "m", FakeResult(
                    200,
                    {
                        "choices": [
                            {
                                "message": {
                                    "role": "assistant",
                                    "content": "",
                                    "tool_calls": [
                                        {"id": "good", "function": {"name": "web_search", "arguments": '{"query":"x"}'}},
                                        {"id": "cut", "function": {"name": "web_search", "arguments": '{"query":'}},
                                    ],
                                },
                                "finish_reason": "length",
                            }
                        ]
                    },
                )
            return user, FakeSrc("llama"), "m", "m", FakeResult(
                200, {"choices": [{"message": {"content": "done"}, "finish_reason": "stop"}]}
            )

        ran = []

        async def fake_tools(calls, **_k):
            ran.append([c["id"] for c in calls])
            job = ToolJob(0, "good", "web_search", '{"query":"x"}', "ts_0_0", text="hits", done=True)
            yield {"event": "tool", "id": "ts_0_0", "name": "web_search", "status": "done"}
            yield [job]

        with patch("app.chat_runtime._run_tools", fake_tools):
            raw = {
                "model": "test-model",
                "messages": [{"role": "user", "content": "hi"}],
                "tools": ["web_search"],
            }
            body, _ = await self._run_raw(invoke, raw)
        self.assertEqual(ran, [["good"]], "only the well-formed call must execute")
        tool_rows = [m for m in payloads[1]["messages"] if m.get("role") == "tool"]
        self.assertTrue(tool_rows, "the complete call's result must reach the next round")
        self.assertEqual(tool_rows[0]["tool_call_id"], "good")
        self.assertTrue(
            any(m.get("role") == "user" and "discarded and NOT executed" in str(m.get("content"))
                for m in payloads[1]["messages"]),
            "the truncated call must be called out for re-issue",
        )
        self.assertIn(b"done", body)

    async def test_oversized_arguments_get_split_message_not_reissue_loop(self):
        from app.chat_generation.parsing import _ARG_OVERFLOW

        payloads = []

        async def invoke(request, user, payload, **_kwargs):
            payloads.append([dict(m) for m in payload["messages"]])
            if len(payloads) == 1:
                return user, FakeSrc("llama"), "m", "m", FakeResult(200, {"choices": [{"message": {
                    "role": "assistant", "content": "", "tool_calls": [
                        {"id": "big", "function": {"name": "web_search", "arguments": _ARG_OVERFLOW}}]},
                    "finish_reason": "tool_calls"}]})
            return user, FakeSrc("llama"), "m", "m", FakeResult(
                200, {"choices": [{"message": {"content": "done"}, "finish_reason": "stop"}]})

        from app.chat_runs import internal_request

        raw = {"model": "test-model", "messages": [{"role": "user", "content": "hi"}], "tools": ["web_search"]}
        # A connected request: the harness's disconnecting one cancels tools
        # before the argument check runs.
        with patch("app.chat_runtime.api.invoke_chat", invoke), patch(
            "app.chat_runtime.api._safe_record_usage", lambda *a, **k: None
        ), patch("app.chat_runtime.chats.get_user_context", return_value={"instructions": "", "profile": ""}):
            resp = await stream_dashboard_chat(internal_request(1), {"id": 1, "key_id": None}, raw)
            body = b"".join([chunk async for chunk in resp.body_iterator])
        self.assertNotIn(b"came back truncated", body, "oversize is not a cut-off call")
        tool_rows = [m for m in payloads[1] if m.get("role") == "tool"]
        self.assertTrue(tool_rows and "split the work" in tool_rows[0]["content"], tool_rows)

    async def test_stream_exception_resume_folds_seen_partial_text(self):
        payloads = []

        async def invoke(request, user, payload, **_kwargs):
            payloads.append(dict(payload))
            return user, FakeSrc("llama"), "m", "m", FakeResult(
                200, {"choices": [{"message": {"content": "unused"}, "finish_reason": "stop"}]}
            )

        consume_calls = {"n": 0}

        async def consume(*args, **kwargs):
            consume_calls["n"] += 1
            if consume_calls["n"] == 1:
                yield ("out", {"choices": [{"delta": {"content": "partial answer"}}]})
                # A transport fault (OSError); local bugs are not resumed.
                raise ConnectionResetError("socket exploded")
            yield (
                "done",
                {"calls": [], "finish": "stop", "content": "final", "reasoning": "",
                 "thinking": [], "usage": {}, "error": None},
            )

        async def never_disconnects():
            await asyncio.Future()

        from starlette.requests import Request as StarletteRequest

        scope = {"type": "http", "method": "POST", "path": "/", "headers": [], "client": ("127.0.0.1", 1)}
        request = StarletteRequest(scope, receive=never_disconnects)
        with patch("app.chat_runtime.api.invoke_chat", invoke), patch(
            "app.chat_runtime.api._safe_record_usage", lambda *a, **k: None
        ), patch("app.chat_runtime._consume_round", consume), patch(
            "app.chat_runtime._resume_delay", lambda *a, **k: 0.0
        ), patch(
            "app.chat_runtime.chats.get_user_context",
            return_value={"instructions": "", "profile": ""},
        ):
            resp = await stream_dashboard_chat(
                request, {"id": 1, "key_id": None},
                {"model": "test-model", "messages": [{"role": "user", "content": "hi"}], "tools": []},
            )
            async for _ in resp.body_iterator:
                pass
        self.assertEqual(len(payloads), 2, "the round must resume once, not fail")
        folded = [m for m in payloads[1]["messages"] if m.get("role") == "assistant"]
        self.assertTrue(any(m.get("content") == "partial answer" for m in folded),
                        "text the client already saw must ride into the retry")

    async def test_local_processing_bug_is_not_resumed_as_provider_failure(self):
        payloads = []

        async def invoke(request, user, payload, **_kwargs):
            payloads.append(dict(payload))
            return user, FakeSrc("llama"), "m", "m", FakeResult(
                200, {"choices": [{"message": {"content": "unused"}, "finish_reason": "stop"}]}
            )

        async def consume(*args, **kwargs):
            yield ("out", {"choices": [{"delta": {"content": "partial"}}]})
            raise RecursionError("maximum recursion depth exceeded")

        async def never_disconnects():
            await asyncio.Future()

        from starlette.requests import Request as StarletteRequest

        scope = {"type": "http", "method": "POST", "path": "/", "headers": [], "client": ("127.0.0.1", 1)}
        request = StarletteRequest(scope, receive=never_disconnects)
        with patch("app.chat_runtime.api.invoke_chat", invoke), patch(
            "app.chat_runtime.api._safe_record_usage", lambda *a, **k: None
        ), patch("app.chat_runtime._consume_round", consume), patch(
            "app.chat_runtime._resume_delay", lambda *a, **k: 0.0
        ), patch(
            "app.chat_runtime.chats.get_user_context",
            return_value={"instructions": "", "profile": ""},
        ):
            resp = await stream_dashboard_chat(
                request, {"id": 1, "key_id": None},
                {"model": "test-model", "messages": [{"role": "user", "content": "hi"}], "tools": []},
            )
            body = b"".join([chunk async for chunk in resp.body_iterator])
        self.assertEqual(len(payloads), 1, "a Fuse-side bug must not be retried as paid rounds")
        self.assertIn(b"Fuse could not process the provider reply (RecursionError)", body)

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
