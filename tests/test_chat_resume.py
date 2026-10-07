"""Mid-turn provider-failure resume in the dashboard generation loop."""
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import chat_runtime as runtime, chat_store as store, chats, db
from app.chat_runs import internal_request

USER = {"id": 1}


def frame(obj):
    return ("data: " + json.dumps(obj, ensure_ascii=False) + "\n\n").encode()


def sse_objects(chunks):
    out = []
    for chunk in chunks:
        for line in chunk.decode().splitlines():
            line = line.strip()
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                out.append("[DONE]")
                continue
            if data:
                out.append(json.loads(data))
    return out


def ok_result(content="final answer", usage=None):
    return SimpleNamespace(
        status=200,
        body={"id": "c1", "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}]},
        stream=None,
        usage=usage or {"total_tokens": 5},
        credential_id=None,
        attempts=[],
    )


def stream_result(frames):
    async def stream():
        for f in frames:
            yield f

    return SimpleNamespace(status=200, body={}, stream=stream(), usage={}, credential_id=None, attempts=[])


def src():
    return SimpleNamespace(source_id=1, type_id="openai")


def notices(objects):
    return [
        (o["fuse"].get("message") or "")
        for o in objects
        if isinstance(o, dict) and isinstance(o.get("fuse"), dict) and o["fuse"].get("event") == "notice"
    ]


class ResumableClassificationTest(unittest.TestCase):
    OLLAMA_BODY = (
        '{"error":"Your account is currently unavailable. '
        'If the problem persists, please contact support@ollama.com."}'
    )

    def test_transient_statuses_are_resumable(self):
        for code in (429, 500, 502, 503, 504, 529):
            with self.subTest(code=code):
                self.assertTrue(runtime._resumable_failure(code, "whatever"))

    def test_definitive_statuses_are_not(self):
        for code in (400, 401, 402, 403, 404, 422):
            with self.subTest(code=code):
                self.assertFalse(runtime._resumable_failure(code, "Forbidden"))

    def test_terminal_messages_win_over_transient_statuses(self):
        cases = (
            (502, "Grok rejected the configured account"),
            (502, "ChatGPT rejected the configured account"),
            (502, "Z.AI rejected the configured API key"),
            (403, "This model requires a subscription"),
            (400, "This model's maximum context length is exceeded"),
            (401, "Invalid API key"),
        )
        for code, message in cases:
            with self.subTest(message=message):
                self.assertFalse(runtime._resumable_failure(code, message))

    def test_transport_messages_are_resumable_even_with_code_zero(self):
        cases = (
            "upstream timeout (ReadTimeout)",
            "upstream stream failed (ReadError)",
            "upstream transport failure (ConnectError)",
            "No upstream keys available",
            "Upstream stream ended without a terminal frame.",
            "service temporarily overloaded",
            "Our servers are currently overloaded. Please try again later.",
            "Please try again later.",
            "The engine is currently overloaded, please try again later",
        )
        for message in cases:
            with self.subTest(message=message):
                self.assertTrue(runtime._resumable_failure(0, message))

    def test_ollama_account_unavailable_is_resumable_despite_403(self):
        self.assertTrue(runtime._resumable_failure(403, self.OLLAMA_BODY))

    def test_string_shaped_openai_error_extracts_message(self):
        code, msg = runtime._error_code_and_message(
            {"error": "Our servers are currently overloaded. Please try again later."}
        )
        self.assertEqual(code, 0)
        self.assertIn("overloaded", msg.lower())
        self.assertTrue(runtime._resumable_failure(code, msg))

    def test_nested_error_with_null_code_extracts_message(self):
        code, msg = runtime._error_code_and_message(
            {
                "error": {
                    "message": "Our servers are currently overloaded. Please try again later.",
                    "type": "server_error",
                    "code": None,
                }
            }
        )
        self.assertEqual(code, 0)
        self.assertIn("overloaded", msg.lower())
        self.assertTrue(runtime._resumable_failure(code, msg))

    def test_provider_error_content_detects_overload_sentence(self):
        self.assertTrue(
            runtime._provider_error_content(
                "Our servers are currently overloaded. Please try again later."
            )
        )
        self.assertFalse(
            runtime._provider_error_content(
                "The cluster was overloaded yesterday, so I switched to a smaller batch "
                "size and reran the job. Here is the report of what I found in the logs."
            )
        )

    def test_resume_delay_grows_and_honors_retry_after(self):
        self.assertEqual(runtime._resume_delay(1), 2.0)
        self.assertEqual(runtime._resume_delay(2), 6.0)
        self.assertEqual(runtime._resume_delay(3), 15.0)
        self.assertEqual(runtime._resume_delay(4), 40.0)
        self.assertEqual(runtime._resume_delay(1, retry_after=30), 30.0)
        self.assertEqual(runtime._resume_delay(1, retry_after=120), runtime._RESUME_MAX_WAIT)

    def test_child_exhausted_error_is_parent_facing(self):
        frame = runtime._exhausted_provider_error(
            True,
            {"error": "Our servers are currently overloaded. Please try again later."},
        )
        msg = frame["error"]["message"]
        self.assertIn("experienced difficulties", msg.lower())
        self.assertIn("overloaded", msg.lower())
        parent = runtime._exhausted_provider_error(
            False,
            {"error": {"message": "Our servers are currently overloaded. Please try again later.", "code": 503}},
            503,
        )
        self.assertEqual(
            parent["error"]["message"],
            "Our servers are currently overloaded. Please try again later.",
        )


class ApplyResumeStateTest(unittest.TestCase):
    def test_partial_text_appends_assistant_then_note(self):
        messages = [{"role": "user", "content": "hi"}]
        runtime._apply_resume_state(messages, "half an answ")
        self.assertEqual(
            messages,
            [
                {"role": "user", "content": "hi"},
                {"role": "assistant", "content": "half an answ"},
                {"role": "user", "content": runtime._RESUME_NOTE},
            ],
        )

    def test_no_partial_after_tool_results_appends_tools_note(self):
        messages = [{"role": "user", "content": "hi"}, {"role": "tool", "tool_call_id": "c1", "content": "r"}]
        runtime._apply_resume_state(messages, "")
        self.assertEqual(messages[-1], {"role": "user", "content": runtime._RESUME_TOOLS_NOTE})
        self.assertEqual(len(messages), 3)

    def test_no_partial_before_any_output_appends_nothing(self):
        messages = [{"role": "system", "content": "s"}, {"role": "user", "content": "hi"}]
        runtime._apply_resume_state(messages, "")
        self.assertEqual(len(messages), 2)

    def test_whitespace_partial_is_treated_as_empty(self):
        messages = [{"role": "user", "content": "hi"}]
        runtime._apply_resume_state(messages, "   \n\t")
        self.assertEqual(len(messages), 1)


class ResumeTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="fuse-resume-")
        self.addCleanup(self.tmp.cleanup)
        self.conn = sqlite3.connect(str(Path(self.tmp.name) / "test.db"), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        db._init(self.conn)
        self.addCleanup(self.conn.close)
        patcher = patch.object(db, "connect", return_value=self.conn)
        patcher.start()
        self.addCleanup(patcher.stop)
        db.execute("INSERT INTO users(id,email,password_hash,created_at) VALUES(1,'t@invalid','x',?)", (db.now(),))
        store.init_schema()
        chats.set_user_context(1, instructions="", profile="")
        for target, value in (
            (patch.object(runtime, "_RESUME_BACKOFF", (0.0, 0.0, 0.0)), None),
            (patch.object(runtime.api, "_safe_record_usage"), None),
            (patch.object(runtime.api, "_tap", side_effect=lambda stream, *a, **k: stream), None),
        ):
            target.start()
            self.addCleanup(target.stop)

    async def stream_turn(self, fake_invoke, raw=None, frozen_context=None):
        raw = raw or {"model": "offline", "messages": [{"role": "user", "content": "hi"}], "tools": []}
        chunks = []

        async def spying_invoke(request, user, body, **_kwargs):
            # send["messages"] aliases the live list; snapshot per send so
            # later mutations (resume notes, final-text append) do not leak
            # into payloads the fakes already captured.
            body = {**body, "messages": [dict(m) for m in (body.get("messages") or [])]}
            return await fake_invoke(request, user, body)

        with patch.object(runtime.api, "invoke_chat", side_effect=spying_invoke):
            response = await runtime.stream_dashboard_chat(internal_request(1), dict(USER), dict(raw), frozen_context=frozen_context)
            # The generator body runs lazily; it must be consumed inside the patch.
            async for chunk in response.body_iterator:
                chunks.append(chunk)
        return sse_objects(chunks)

    def round_messages(self, body):
        return body["messages"]

    async def test_trusted_full_history_bypasses_display_limits_and_reaches_wire(self):
        rows = [{"role": "user", "content": "question"}]
        for i in range(550):
            rows.extend([{"role": "assistant", "content": "", "tool_calls": [{"id": str(i), "type": "function",
                "function": {"name": "read_file", "arguments": "{}"}}]},
                {"role": "tool", "tool_call_id": str(i), "content": f"FULL_RESULT_{i}"}])
        rows.append({"role": "user", "content": "continue"})

        async def invoke(request, user, body, **_kwargs):
            self.assertTrue(request.scope.get("fuse_retained_history"))
            runtime.api._validate_messages(body["messages"], retained=True)
            self.assertEqual(sum(m["role"] == "tool" for m in body["messages"]), 550)
            self.assertIn("FULL_RESULT_0", json.dumps(body))
            return user, src(), "m", "m", ok_result("done")

        await self.stream_turn(invoke, {"model": "offline", "messages": rows, "tools": [], "history_replay": True},
            frozen_context=chats.get_user_context(1))

    async def test_context_uses_single_request_usage_and_calibrates_next_payload(self):
        from app import context as ctx
        sent = []

        async def fake_invoke(request, user, body, **_kwargs):
            sent.append(body)
            return user, src(), "m", "m", stream_result([
                frame({"choices": [{"delta": {"content": "done"}, "finish_reason": "stop"}]}),
                frame({"choices": [], "usage": {"prompt_tokens": 60000, "completion_tokens": 50,
                    "prompt_tokens_details": {"cached_tokens": 59000}, "total_tokens": 60050}}),
                b"data: [DONE]\n\n",
            ])

        objects = await self.stream_turn(fake_invoke)
        meters = [obj["fuse"] for obj in objects if isinstance(obj, dict) and obj.get("fuse", {}).get("event") == "context"]
        measured = next(m for m in meters if m["phase"] == "round")
        self.assertGreater(measured["used"], 60000)
        self.assertLess(measured["used"], 60050)
        self.assertTrue(measured["estimated"])
        self.assertEqual(measured["basis"], "provider_prompt")
        final = next(m for m in meters if m["phase"] == "end")
        self.assertEqual(final["basis"], "provider_prompt")
        self.assertGreater(final["used"], 60000)
        self.assertLess(final["used"], 60050)
        self.assertEqual(final["calibration"]["prompt_tokens"], 60000)
        self.assertEqual(final["calibration"]["estimated_prompt_tokens"],
            ctx.occupancy_parts(sent[0]["messages"], tools=sent[0].get("tools"))["total"])

    async def test_tool_growth_can_compact_twice_from_a_small_start(self):
        from app import context as ctx
        count = 0

        async def fake_invoke(request, user, body, **_kwargs):
            nonlocal count
            count += 1
            if count > 2:
                return user, src(), "m", "m", ok_result("done")
            return user, src(), "m", "m", stream_result([
                frame({"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "c1",
                    "function": {"name": "web_search", "arguments": "{}"}}]}, "finish_reason": "tool_calls"}]}),
                b"data: [DONE]\n\n",
            ])

        async def fake_tools(*args, **kwargs):
            yield [SimpleNamespace(orig_index=0, call_id="c1", name="web_search", arguments="{}",
                event_id="ts_0_0", text="x" * 500000, trace={"id": "ts_0_0", "status": "done"}, done=True)]

        async def compact(rows, **kwargs):
            # Successful compaction leaves a small handoff, then the next
            # tool round fills the window again.
            rebuilt = [{"role": "user", "content": "handoff"}]
            return rebuilt, ctx.meter_from_messages(rebuilt, model="offline", type_id="openai"), "summarize"

        with patch("app.chat_generation.tool_jobs.TOOL_RESULT_CHARS", 600000), patch.object(runtime, "_run_tools", fake_tools), patch.object(runtime, "_compact_current", side_effect=compact) as compactor:
            await self.stream_turn(fake_invoke)
        self.assertEqual(compactor.await_count, 2)

    async def test_transient_status_retries_same_round_without_error_frame(self):
        calls = []

        async def fake_invoke(request, user, body, **_kwargs):
            calls.append(body)
            if len(calls) == 1:
                return user, src(), "m", "m", SimpleNamespace(
                    status=502,
                    body={"error": {"message": "upstream blew up"}},
                    stream=None, usage={}, credential_id=None, attempts=[],
                )
            return user, src(), "m", "m", ok_result()

        objects = await self.stream_turn(fake_invoke)
        self.assertEqual(len(calls), 2)
        # Round 0 with nothing delivered yet: an identical retry payload.
        self.assertEqual(calls[1]["messages"], calls[0]["messages"])
        self.assertTrue(any("retrying this step" in n for n in notices(objects)))
        self.assertFalse(any(isinstance(o, dict) and o.get("error") for o in objects))
        self.assertEqual(objects[-1], "[DONE]")
        content = "".join(
            ((o.get("choices") or [{}])[0].get("delta") or {}).get("content", "")
            or ((o.get("choices") or [{}])[0].get("message") or {}).get("content", "")
            for o in objects if isinstance(o, dict) and o.get("choices")
        )
        self.assertEqual(content, "final answer")

    async def test_midstream_resume_continues_from_partial_text(self):
        calls = []

        async def fake_invoke(request, user, body, **_kwargs):
            calls.append(body)
            if len(calls) == 1:
                return user, src(), "m", "m", stream_result([
                    frame({"choices": [{"delta": {"content": "partial an"}}]}),
                    frame({"choices": [{"delta": {"content": "swer more"}}]}),
                ])
            return user, src(), "m", "m", ok_result(" done")

        objects = await self.stream_turn(fake_invoke)
        self.assertEqual(len(calls), 2)
        resumed = calls[1]["messages"]
        self.assertEqual(resumed[-2], {"role": "assistant", "content": "partial answer more"})
        self.assertEqual(resumed[-1], {"role": "user", "content": runtime._RESUME_NOTE})
        self.assertTrue(any("resuming in" in n for n in notices(objects)))
        self.assertFalse(any(isinstance(o, dict) and o.get("error") for o in objects))
        self.assertEqual(objects[-1], "[DONE]")

    async def test_large_tool_result_keeps_prefix_and_resume_note(self):
        calls = []

        async def fake_invoke(request, user, body, **_kwargs):
            calls.append(body)
            if len(calls) == 1:
                return user, src(), "m", "m", stream_result([
                    frame({"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "c1", "function": {"name": "web_search", "arguments": "{}"}}]}, "finish_reason": "tool_calls"}]}),
                    b"data: [DONE]\n\n",
                ])
            if len(calls) == 2:
                return user, src(), "m", "m", stream_result([
                    frame({"error": {"message": "upstream overloaded", "code": 503}}),
                    b"data: [DONE]\n\n",
                ])
            return user, src(), "m", "m", ok_result("done")

        async def fake_run_tools(*args, **kwargs):
            yield [SimpleNamespace(
                orig_index=0, call_id="c1", name="web_search", arguments="{}",
                event_id="ts_0_0", text="x" * 600_000, trace={"id": "ts_0_0", "status": "done"}, done=True,
            )]

        with patch("app.chat_generation.tool_jobs.TOOL_RESULT_CHARS", 600_000), patch.object(runtime, "_run_tools", fake_run_tools), patch.object(
            runtime.ctxmod, "should_auto_compact", return_value=False
        ), patch.object(runtime.ctxmod, "clip_to_fit", side_effect=AssertionError("history must not be clipped")):
            objects = await self.stream_turn(fake_invoke)
        self.assertEqual(len(calls), 3)
        self.assertEqual(next(m["content"] for m in calls[1]["messages"] if m["role"] == "tool"), "x" * 600_000)
        retry = calls[2]["messages"]
        self.assertEqual(retry[-1], {"role": "user", "content": runtime._RESUME_TOOLS_NOTE})
        # Every row already sent replays byte-identical; the retry only appends.
        self.assertEqual(retry[: len(calls[1]["messages"])], calls[1]["messages"])
        self.assertEqual(objects[-1], "[DONE]")

    async def test_prompt_snapshot_survives_turns_restart_and_live_instruction_edits(self):
        thread = chats.create_thread(1, model="offline", tools=["web_search"])
        sent = []

        async def invoke(request, user, body, **_kwargs):
            sent.append(json.loads(json.dumps(body)))
            return user, src(), "offline", "offline", ok_result()

        run = store.start_turn(1, thread["id"], "first", thread["revision"], None)["run"]
        with patch.object(runtime, "build_system_prompt", return_value="original system"):
            await self.stream_turn(invoke, run["request"], frozen_context=chats.get_user_context(1))
        store.finish_run(run["id"], {"content": "final answer", "reasoning": ""}, "completed")
        view = chats.get_thread(1, thread["id"])
        second = store.start_turn(1, thread["id"], "next", view["revision"], None)["run"]
        self.assertEqual(second["request"]["prompt_snapshot"]["system"], "original system")
        with patch.object(runtime, "build_system_prompt", return_value="edited system"):
            await self.stream_turn(invoke, second["request"], frozen_context=chats.get_user_context(1))
        self.assertEqual(sent[1]["messages"][:len(sent[0]["messages"])], sent[0]["messages"])
        self.assertEqual(sent[1]["tools"], sent[0]["tools"])

        saved = store.get_run(1, second["id"])
        resumed = {**saved["request"], "messages": json.loads(db.fetchone(
            "SELECT provider_messages FROM chat_runs WHERE id=?", (second["id"],))[0])}
        resumed["agent"] = {**resumed.get("agent", {}), "resumed": True, "skip_sanitize": True}
        with patch.object(runtime, "build_system_prompt", return_value="after restart"):
            await self.stream_turn(invoke, resumed, frozen_context=chats.get_user_context(1))
        self.assertEqual(sent[2]["messages"][:len(sent[1]["messages"])], sent[1]["messages"])
        self.assertEqual(sent[2]["tools"], sent[1]["tools"])

    async def test_compaction_is_the_only_automatic_prompt_refresh(self):
        sent = []
        calls = 0
        old = {"system": "old system", "tools": []}

        async def invoke(request, user, body, **_kwargs):
            nonlocal calls
            calls += 1
            sent.append(json.loads(json.dumps(body)))
            if calls == 1:
                return user, src(), "offline", "offline", stream_result([
                    frame({"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "c1",
                        "function": {"name": "web_search", "arguments": "{}"}}]}, "finish_reason": "tool_calls"}]}),
                    b"data: [DONE]\n\n",
                ])
            return user, src(), "offline", "offline", ok_result()

        async def fake_tools(*args, **kwargs):
            yield [SimpleNamespace(orig_index=0, call_id="c1", name="web_search", arguments="{}",
                event_id="ts_0_0", text="large result", trace={"status": "done"}, done=True)]

        async def compact(rows, **kwargs):
            rebuilt = [rows[0], {"role": "user", "content": "new handoff"},
                       {"role": "assistant", "content": runtime.ctxmod.COMPACT_ACK}]
            return rebuilt, runtime.ctxmod.meter(used=100, limit=128000), "summarize"

        raw = {"model": "offline", "messages": [{"role": "user", "content": "go"}],
               "tools": ["web_search"], "prompt_snapshot": old}
        with patch.object(runtime, "build_system_prompt", return_value="new system"), patch.object(
            runtime, "_run_tools", fake_tools
        ), patch.object(runtime, "_compact_current", side_effect=compact), patch.object(
            runtime.ctxmod, "should_auto_compact", side_effect=[False, False, True, False]
        ):
            # A pinned schema can still contain the search tools; its bytes
            # remain fixed until compaction.
            old["tools"] = runtime.openai_tools(["web_search"])
            await self.stream_turn(invoke, raw, frozen_context=chats.get_user_context(1))
        self.assertEqual(sent[0]["messages"][0]["content"], "old system")
        self.assertEqual(sent[1]["messages"][0]["content"], "new system")
        self.assertFalse(any(m.get("role") == "tool" for m in sent[1]["messages"]))
        self.assertIn("new handoff", json.dumps(sent[1]))

    async def test_cache_key_is_thread_for_parent_and_run_for_child(self):
        seen = []

        async def invoke(request, user, body, **_kwargs):
            seen.append(body)
            return user, src(), "m", "m", ok_result()

        await self.stream_turn(invoke, {"model": "offline", "messages": [{"role": "user", "content": "hi"}], "tools": [], "thread_id": 77})
        await self.stream_turn(invoke, {
            "model": "offline", "messages": [{"role": "user", "content": "hi"}], "tools": [],
            "agent": {"parent_run_id": "parent-1", "run_id": "child-1", "depth": 1, "coding": True},
        })
        self.assertEqual(seen[0]["prompt_cache_key"], "thread:77")
        # Siblings must not share the parent's pin: each child conversation
        # keeps its own cache identity.
        self.assertEqual(seen[1]["prompt_cache_key"], "run:child-1")

    async def test_after_tools_resume_note_without_partial(self):
        calls = []

        async def fake_invoke(request, user, body, **_kwargs):
            calls.append(body)
            if len(calls) == 1:
                return user, src(), "m", "m", stream_result([
                    frame({"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "c1", "function": {"name": "web_search", "arguments": "{}"}}]}, "finish_reason": "tool_calls"}]}),
                    b"data: [DONE]\n\n",
                ])
            if len(calls) == 2:
                return user, src(), "m", "m", stream_result([
                    frame({"error": {"message": "upstream overloaded", "code": 503}}),
                    b"data: [DONE]\n\n",
                ])
            return user, src(), "m", "m", ok_result("done")

        async def fake_run_tools(*args, **kwargs):
            yield [SimpleNamespace(
                orig_index=0, call_id="c1", name="web_search", arguments="{}",
                event_id="ts_0_0", text="r", trace={"id": "ts_0_0", "status": "done"}, done=True,
            )]

        with patch.object(runtime, "_run_tools", fake_run_tools):
            objects = await self.stream_turn(fake_invoke)
        self.assertEqual(len(calls), 3)
        resumed = calls[2]["messages"]
        self.assertEqual(resumed[-1], {"role": "user", "content": runtime._RESUME_TOOLS_NOTE})
        self.assertEqual(resumed[-2]["role"], "tool")
        self.assertEqual(resumed[-2]["tool_call_id"], "c1")
        self.assertEqual(objects[-1], "[DONE]")

    async def test_definitive_failure_is_not_retried(self):
        calls = []

        async def fake_invoke(request, user, body, **_kwargs):
            calls.append(body)
            return user, src(), "m", "m", SimpleNamespace(
                status=502,
                body={"error": {"message": "Grok rejected the configured account"}},
                stream=None, usage={}, credential_id=None, attempts=[],
            )

        objects = await self.stream_turn(fake_invoke)
        self.assertEqual(len(calls), 1)
        self.assertTrue(any(isinstance(o, dict) and o.get("error") for o in objects))
        self.assertEqual(objects[-1], "[DONE]")

    async def test_resume_budget_is_bounded(self):
        calls = []

        async def fake_invoke(request, user, body, **_kwargs):
            calls.append(body)
            return user, src(), "m", "m", SimpleNamespace(
                status=502, body={"error": {"message": "upstream blew up"}},
                stream=None, usage={}, credential_id=None, attempts=[],
            )

        objects = await self.stream_turn(fake_invoke)
        self.assertEqual(len(calls), runtime.MAX_RESUME_RETRIES + 1)
        self.assertTrue(any(isinstance(o, dict) and o.get("error") for o in objects))
        self.assertEqual(objects[-1], "[DONE]")

    async def test_ollama_account_unavailable_403_recovers_invisibly(self):
        """The reported scenario: a 403 account-flag mid-turn fails over and
        the turn completes with no error frame and no user action."""
        calls = []

        async def fake_invoke(request, user, body, **_kwargs):
            calls.append(body)
            if len(calls) == 1:
                return user, src(), "m", "m", SimpleNamespace(
                    status=403,
                    body={"error": "Your account is currently unavailable. If the problem persists, "
                                   "please contact support@ollama.com."},
                    stream=None, usage={}, credential_id=None, attempts=[{"credential_id": None}],
                )
            return user, src(), "m", "m", ok_result()

        objects = await self.stream_turn(fake_invoke)
        self.assertEqual(len(calls), 2)
        self.assertFalse(any(isinstance(o, dict) and o.get("error") for o in objects))
        self.assertTrue(any("retrying this step" in n for n in notices(objects)))
        self.assertEqual(objects[-1], "[DONE]")

    async def test_disconnect_stops_resume(self):
        calls = []

        async def fake_invoke(request, user, body, **_kwargs):
            calls.append(body)
            return user, src(), "m", "m", SimpleNamespace(
                status=502, body={"error": {"message": "upstream blew up"}},
                stream=None, usage={}, credential_id=None, attempts=[],
            )

        gone = patch.object(
            type(internal_request(1)), "is_disconnected", new=AsyncMock(return_value=True)
        )
        with gone:
            objects = await self.stream_turn(fake_invoke)
        self.assertEqual(len(calls), 1)
        self.assertTrue(any(isinstance(o, dict) and o.get("error") for o in objects))
        self.assertEqual(objects[-1], "[DONE]")

    async def test_string_shaped_stream_error_retries(self):
        calls = []

        async def fake_invoke(request, user, body, **_kwargs):
            calls.append(body)
            if len(calls) == 1:
                return user, src(), "m", "m", stream_result([
                    frame({"error": "Our servers are currently overloaded. Please try again later."}),
                    b"data: [DONE]\n\n",
                ])
            return user, src(), "m", "m", ok_result()

        objects = await self.stream_turn(fake_invoke)
        self.assertEqual(len(calls), 2)
        self.assertTrue(any("resuming in" in n for n in notices(objects)))
        self.assertFalse(any(isinstance(o, dict) and o.get("error") for o in objects))
        self.assertEqual(objects[-1], "[DONE]")

    async def test_overload_content_is_retried_not_accepted_as_answer(self):
        calls = []

        async def fake_invoke(request, user, body, **_kwargs):
            calls.append(body)
            if len(calls) == 1:
                return user, src(), "m", "m", stream_result([
                    frame({"choices": [{"delta": {"content": "Our servers are currently overloaded. Please try again later."}, "finish_reason": "stop"}]}),
                    b"data: [DONE]\n\n",
                ])
            return user, src(), "m", "m", ok_result("done")

        objects = await self.stream_turn(fake_invoke)
        self.assertEqual(len(calls), 2)
        resumed = calls[1]["messages"]
        self.assertNotEqual((resumed[-1].get("content") or ""), runtime._RESUME_NOTE)
        self.assertFalse(any("Our servers are currently overloaded" in str(m.get("content") or "") for m in resumed))
        self.assertTrue(any("resuming in" in n for n in notices(objects)))
        self.assertFalse(any(isinstance(o, dict) and o.get("error") for o in objects))
        painted = [
            o for o in objects
            if isinstance(o, dict) and (o.get("choices") or [{}])[0].get("delta", {}).get("content")
        ]
        self.assertFalse(
            any("Our servers are currently overloaded" in str((o.get("choices") or [{}])[0].get("delta", {}).get("content") or "") for o in painted)
        )
        self.assertEqual(objects[-1], "[DONE]")

    async def test_child_exhaustion_returns_difficulty_not_raw_overload(self):
        calls = []

        async def fake_invoke(request, user, body, **_kwargs):
            calls.append(body)
            return user, src(), "m", "m", SimpleNamespace(
                status=503,
                body={"error": {"message": "Our servers are currently overloaded. Please try again later."}},
                stream=None, usage={}, credential_id=None, attempts=[],
            )

        objects = await self.stream_turn(
            fake_invoke,
            raw={
                "model": "offline",
                "messages": [{"role": "user", "content": "hi"}],
                "tools": [],
                "agent": {"parent_run_id": "parent-1", "depth": 1, "coding": True},
            },
        )
        self.assertEqual(len(calls), runtime.MAX_RESUME_RETRIES + 1)
        errors = [o for o in objects if isinstance(o, dict) and o.get("error")]
        self.assertTrue(errors)
        msg = errors[-1]["error"]["message"] if isinstance(errors[-1]["error"], dict) else str(errors[-1]["error"])
        self.assertIn("experienced difficulties", msg.lower())
        self.assertIn("overloaded", msg.lower())
        self.assertEqual(objects[-1], "[DONE]")

    async def test_retry_after_header_stretches_backoff(self):
        waits = []

        async def fake_heartbeat_sleep(delay):
            # The production helper slices the wait into heartbeat beats; for
            # the Retry-After contract only the total requested delay matters.
            waits.append(delay)
            return
            yield

        calls = []

        async def fake_invoke(request, user, body, **_kwargs):
            calls.append(body)
            if len(calls) == 1:
                return user, src(), "m", "m", SimpleNamespace(
                    status=503,
                    body={"error": {"message": "Our servers are currently overloaded. Please try again later."}},
                    stream=None, usage={}, credential_id=None, attempts=[],
                    response_headers={"Retry-After": "30"},
                )
            return user, src(), "m", "m", ok_result()

        with patch.object(runtime, "_heartbeat_sleep", fake_heartbeat_sleep):
            objects = await self.stream_turn(fake_invoke)
        self.assertEqual(len(calls), 2)
        self.assertEqual(waits, [30.0])
        self.assertEqual(objects[-1], "[DONE]")


if __name__ == "__main__":
    unittest.main()
