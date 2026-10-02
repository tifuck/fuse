"""Offline regressions for argument integrity, trace replay and agent lifecycle."""
import asyncio
import json
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

_TMP = tempfile.TemporaryDirectory(prefix="fuse-runtime-audit-")
os.environ.setdefault("DATABASE_PATH", _TMP.name + "/test.db")
os.environ.setdefault("APP_SECRET", "audit-test-secret-" * 3)

from app import agents, chat_runs, chat_runtime as runtime, chat_store as store, chat_tools, chats, db, tool_failures


class Request:
    async def is_disconnected(self):
        return False


def call(name, arguments):
    return {"index": 0, "id": "call", "function": {"name": name, "arguments": arguments}}


class ArgumentsTest(unittest.IsolatedAsyncioTestCase):
    async def run_calls(self, calls, executor):
        events, jobs = [], None
        with patch.object(runtime, "execute_tool", executor), patch.object(runtime, "_record_failure"):
            async for item in runtime._run_tools(calls, user_id=1, allowed_urls=set(), request=Request(), completion_i=0, budgets={}, coding=True):
                if isinstance(item, list):
                    jobs = item
                else:
                    events.append(item)
        return events, jobs

    async def test_split_tool_name_preserves_full_write_and_edit(self):
        for name, args in (("write_file", {"path": "x", "content": "🙂" * 20000}),
                           ("search_replace", {"path": "x", "old_string": "a" * 9000, "new_string": "b" * 20000})):
            raw = json.dumps(args, ensure_ascii=False)
            bucket = {}
            runtime._take_calls(bucket, call(name[:3], raw[:16000]))
            runtime._take_calls(bucket, call(name[3:], raw[16000:]))
            execute = AsyncMock(return_value=("ok", {"name": name, "status": "done"}))
            await self.run_calls(list(bucket.values()), execute)
            self.assertEqual(json.loads(execute.call_args.args[1]), args)
            snapshot = runtime._provider_snapshot([], runtime.jobs_from_calls(list(bucket.values()), 0))
            self.assertEqual(json.loads(snapshot[0]["tool_calls"][0]["function"]["arguments"]), args)

    async def test_overflow_is_sticky_and_never_executes_valid_prefix(self):
        bucket = {}
        runtime._take_calls(bucket, call("write_file", '{"path":"x","content":"ok"}' + " " * 400000))
        runtime._take_calls(bucket, call("write_file", '{"path":"x","content":"replacement"}'))
        execute = AsyncMock()
        events, jobs = await self.run_calls(list(bucket.values()), execute)
        execute.assert_not_called()
        self.assertEqual(jobs[0].trace["kind"], "invalid_arguments")
        self.assertTrue(events[-1]["error"])

    async def test_xml_overflow_refused(self):
        rec = runtime._xml_call("write_file", {"path": "x", "content": "x" * 400001}, 0, coding=True)
        execute = AsyncMock()
        await self.run_calls([rec], execute)
        execute.assert_not_called()

    async def test_non_object_json_does_not_dispatch(self):
        for raw in ("[]", "null", '"text"', "123"):
            execute = AsyncMock()
            await self.run_calls([call("write_file", raw)], execute)
            execute.assert_not_called()
            with patch("app.workspace.execute", new_callable=AsyncMock) as workspace:
                _, trace = await chat_tools.execute_tool("write_file", raw, user_id=1, allowed_urls=set(), coding=True)
                self.assertEqual(trace["kind"], "invalid_arguments")
                workspace.assert_not_called()

    async def test_output_is_separate_from_model_clip_and_roundtrips(self):
        text = "output\n" * 6000
        execute = AsyncMock(return_value=(text, {"name": "run_terminal_command", "status": "done", "exit_code": 2}))
        events, jobs = await self.run_calls([call("run_terminal_command", "{}")], execute)
        output = chat_runs.Output({"message": {"id": "m", "role": "assistant"}})
        for event in events:
            output.apply({"fuse": event})
        stored = store._message(output.stored(), "m")
        trace = stored["tools"][0]
        self.assertEqual(trace["status"], "error")
        self.assertEqual(trace["output"], text[:chats.TOOL_OUTPUT_MAX])
        self.assertEqual(trace["output_chars"], len(text))
        self.assertTrue(trace["truncated"])
        model_text = runtime._tool_messages(jobs)[1]["content"]
        self.assertIn("truncated", model_text)
        self.assertLessEqual(len(model_text), runtime.TOOL_RESULT_CHARS)

    async def test_spawn_description_survives_final_event(self):
        execute = AsyncMock(return_value=("ok", {"name": "spawn_subagent", "status": "done", "description": "Inspect files"}))
        events, _ = await self.run_calls([call("spawn_subagent", "{}")], execute)
        self.assertEqual(events[-1]["description"], "Inspect files")

    async def test_cancelled_child_does_not_cancel_parent(self):
        async def cancelled():
            raise asyncio.CancelledError
        task = asyncio.create_task(cancelled())
        await asyncio.gather(task, return_exceptions=True)
        parent = {"id": "p", "thread_id": 1, "request": {"model": "offline", "agent": {}}}
        child = {"id": "c", "status": "cancelled", "message": {"content": ""}, "request": {"model": "offline", "agent": {}}}
        with patch.object(store, "get_run", side_effect=[parent, child]), patch.object(store, "start_child_run", return_value=child), patch.object(chat_runs, "start", AsyncMock(return_value=task)), patch.object(store, "drop_pending_child_result"), patch.object(store, "mark_wake_sent"):
            _, trace = await agents.spawn(user={"id": 1}, request=Request(), parent_run_id="p", arguments='{"prompt":"inspect","description":"Inspect"}', thread_id=1)
        self.assertEqual(trace["status"], "interrupted")

    async def test_new_spawn_requires_description(self):
        # Claude Code's Agent tool requires a description; a follow-up keeps the child's label.
        with patch.object(store, "get_run") as get_run, patch.object(store, "start_child_run") as start_child:
            text, trace = await agents.spawn(user={"id": 1}, request=Request(), parent_run_id="p", arguments='{"prompt":"inspect"}', thread_id=1)
        self.assertEqual(trace["status"], "error")
        self.assertIn("description is required", text)
        get_run.assert_not_called()
        start_child.assert_not_called()

    async def test_spawn_start_exception_returns_error(self):
        parent = {"id": "p", "thread_id": 1, "request": {"model": "offline", "agent": {}}}
        child = {"id": "c", "status": "queued", "message": {"content": ""}, "request": {"model": "offline", "agent": {}}}
        with (
            patch.object(store, "get_run", side_effect=[parent]),
            patch.object(store, "start_child_run", return_value=child),
            patch.object(chat_runs, "start", AsyncMock(side_effect=RuntimeError("no worker"))),
            patch.object(store, "abort_active") as abort,
            patch.object(store, "refund_spawn"),
        ):
            text, trace = await agents.spawn(user={"id": 1}, request=Request(), parent_run_id="p", arguments='{"prompt":"inspect","description":"Inspect"}', thread_id=1)
        abort.assert_called_once_with("c", "failed", "no worker")
        self.assertEqual(trace["status"], "error")
        self.assertIn("no worker", text)

    async def test_background_start_refused_does_not_report_running(self):
        parent = {"id": "p", "thread_id": 1, "request": {"model": "offline", "agent": {}}}
        child = {"id": "c", "status": "interrupted", "message": {"content": ""}, "request": {"model": "offline", "agent": {}}}
        with patch.object(store, "get_run", side_effect=[parent, child, child]), patch.object(store, "start_child_run", return_value=child), patch.object(chat_runs, "start", AsyncMock(return_value=None)), patch.object(store, "drop_pending_child_result"), patch.object(store, "mark_wake_sent"):
            _, trace = await agents.spawn(user={"id": 1}, request=Request(), parent_run_id="p", arguments='{"prompt":"inspect","description":"Inspect","background":true}', thread_id=1)
        self.assertEqual(trace["status"], "interrupted")

    async def test_spawn_passes_resolved_model(self):
        parent = {"id": "p", "thread_id": 1, "request": {"model": "gpt-5.4", "agent": {}}}
        child = {"id": "c", "status": "completed", "message": {"content": "ok"}, "request": {"model": "grok-4.6", "agent": {}}}
        with (
            patch.object(store, "get_run", side_effect=[parent, child, child]),
            patch.object(store, "start_child_run", return_value=child) as start_child,
            patch.object(chat_runs, "start", AsyncMock(return_value=None)),
            patch.object(store, "drop_pending_child_result"),
            patch.object(store, "mark_wake_sent"),
            patch.object(agents, "resolve_spawn_model", AsyncMock(return_value=("grok-4.6", ""))),
        ):
            _, trace = await agents.spawn(
                user={"id": 1},
                request=Request(),
                parent_run_id="p",
                arguments='{"prompt":"inspect","description":"scan","model":"grok-4.6"}',
                thread_id=1,
            )
        self.assertEqual(start_child.call_args.kwargs["model"], "grok-4.6")
        self.assertEqual(trace["model"], "grok-4.6")


class TraceTest(unittest.TestCase):
    def output(self):
        return chat_runs.Output({"message": {"id": "m", "role": "assistant"}})

    def test_unknown_failure_is_preserved(self):
        out = self.output()
        out.apply({"fuse": {"event": "tool", "id": "t", "name": "invented_tool", "status": "error", "kind": "unknown_tool", "error": "Unknown tool"}})
        stored = store._message(out.stored(), "m")
        self.assertEqual(stored["tools"][0]["name"], "invented_tool")
        self.assertEqual(stored["segments"], [{"type": "tool", "id": "t"}])

    def test_permission_segment_survives_and_identity_is_patched(self):
        out = self.output()
        out.apply({"fuse": {"event": "permission", "id": "p", "slug": "skill"}})
        self.assertEqual(out.stored()["segments"], [{"type": "permission", "id": "p"}])
        patch_data = chat_runs._segments_patch([{"type": "permission", "id": "a"}], [{"type": "permission", "id": "b"}])
        self.assertEqual(patch_data, {"from": 0, "items": [{"type": "permission", "id": "b"}]})

    def test_delayed_running_does_not_revive_terminal_step(self):
        for status in ("done", "error", "interrupted"):
            out = self.output()
            for state in (status, "running"):
                out.apply({"fuse": {"event": "tool", "id": "t", "name": "read_file", "status": state}})
            self.assertEqual(out.stored()["tools"][0]["status"], status)

    def test_structural_segment_patch_keeps_earlier_text_changes(self):
        old = [{"type": "content", "text": "old"}, {"type": "tool", "id": "a"}]
        for new in ([{"type": "content", "text": "new"}],
                    [{"type": "content", "text": "new"}, {"type": "tool", "id": "b"}]):
            patch_data = chat_runs._segments_patch(old, new)
            replay = old[:patch_data["from"]] + patch_data["items"]
            self.assertEqual(replay, new)

    def test_tool_update_does_not_break_thinking_order(self):
        out = self.output()
        out.apply({"fuse": {"event": "tool", "id": "t", "name": "read_file", "status": "running"}})
        out.apply({"choices": [{"delta": {"content": "<think>one"}}]})
        out.apply({"fuse": {"event": "tool", "id": "t", "name": "read_file", "status": "done"}})
        out.apply({"choices": [{"delta": {"content": "two</think>answer"}}]})
        stored = out.stored()
        self.assertEqual(stored["reasoning"], "onetwo")
        self.assertEqual(stored["content"], "answer")
        self.assertEqual([s["type"] for s in stored["segments"]], ["tool", "reasoning", "content"])


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:", check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        db._init(self.conn)
        self.addCleanup(self.conn.close)
        p = patch.object(db, "connect", return_value=self.conn)
        p.start()
        self.addCleanup(p.stop)
        db.execute("INSERT INTO users(id,email,password_hash,created_at) VALUES(1,'audit@test.invalid','unused',?)", (db.now(),))
        self.thread = chats.create_thread(1, model="offline")
        self.parent = store.start_turn(1, self.thread["id"], "hello", self.thread["revision"], "turn")["run"]

    def child(self, **kw):
        args = dict(user_id=1, thread_id=self.thread["id"], parent_run_id=self.parent["id"], prompt="inspect", description="inspect")
        args.update(kw)
        return store.start_child_run(**args)

    def test_resume_cannot_bypass_live_tree_cap(self):
        child = self.child()
        store.finish_run(child["id"], {"content": "done"}, "completed")
        with patch.object(store, "LIVE_TREE_CHILDREN", 0), self.assertRaises(store.SpawnCapError):
            self.child(resume_from_id=child["id"])

    def test_parent_thread_and_cancel_are_enforced(self):
        other = chats.create_thread(1, model="offline")
        with self.assertRaises(store.SpawnCapError):
            self.child(thread_id=other["id"])
        store.request_cancel_self(1, self.parent["id"])
        with self.assertRaises(store.SpawnCapError):
            self.child()

    def test_resume_recomputes_depth(self):
        child = self.child(allow_nested_spawn=True)
        nested = self.child(parent_run_id=child["id"], allow_nested_spawn=True, depth=0)
        self.assertEqual(nested["agent_depth"], 2)
        store.finish_run(nested["id"], {"content": "done"}, "completed")
        resumed = self.child(resume_from_id=nested["id"])
        self.assertEqual(resumed["agent_depth"], 1)
        self.assertEqual(resumed["request"]["agent"]["depth"], 1)

    def test_restricted_child_cannot_resume_general_child(self):
        general = self.child()
        store.finish_run(general["id"], {"content": "done"}, "completed")
        restricted = self.child(subagent_type="plan", allow_nested_spawn=True)
        with self.assertRaises(store.SpawnCapError):
            self.child(parent_run_id=restricted["id"], resume_from_id=general["id"], allow_nested_spawn=True)

    def test_resuming_ancestor_cannot_create_cycle(self):
        child = self.child(allow_nested_spawn=True)
        nested = self.child(parent_run_id=child["id"], allow_nested_spawn=True)
        store.finish_run(child["id"], {"content": "done"}, "completed")
        with self.assertRaises(store.SpawnCapError):
            self.child(parent_run_id=nested["id"], resume_from_id=child["id"], allow_nested_spawn=True)

    def test_resume_cannot_orphan_live_descendants_in_another_tree(self):
        child = self.child(allow_nested_spawn=True)
        self.child(parent_run_id=child["id"], allow_nested_spawn=True)
        store.finish_run(child["id"], {"content": "done"}, "completed")
        store.finish_run(self.parent["id"], {"content": "done"}, "completed")
        thread = chats.get_thread(1, self.thread["id"])
        parent = store.start_turn(1, thread["id"], "continue", thread["revision"], "next")["run"]
        with self.assertRaises(store.SpawnCapError):
            self.child(parent_run_id=parent["id"], resume_from_id=child["id"], allow_nested_spawn=True)

    def test_large_failure_trace_remains_json(self):
        tool_failures.record(tool_name="read_file", trace={"output": "x" * 30000, "error": "failed"})
        trace = tool_failures.recent(limit=1)[0]["trace"]
        self.assertIsInstance(trace, dict)
        self.assertTrue(trace["truncated"])
        self.assertEqual(trace["error"], "failed")

    def test_saved_transcript_does_not_start_with_orphan_tool_results(self):
        messages = [{"role": "assistant", "tool_calls": [call("read_file", "{}")]}]
        messages += [{"role": "tool", "tool_call_id": "call", "content": "result"}]
        messages += [{"role": "user", "content": str(i)} for i in range(199)]
        store.save_turn_progress(self.parent["id"], messages)
        saved = json.loads(store.get_run(1, self.parent["id"])["provider_messages"])
        self.assertNotEqual(saved[0]["role"], "tool")

    def test_clipped_steer_is_not_acknowledged_as_durable(self):
        store.queue_steer(self.parent["id"], "steer-id", "new task")
        messages = [{"role": "user", "content": store.steer_envelope("steer-id", "new task")}]
        messages += [{"role": "user", "content": "x" * 10000} for _ in range(60)]
        store.save_turn_progress(self.parent["id"], messages)
        saved = store.get_run(1, self.parent["id"])
        self.assertTrue(saved["request"]["agent"]["pending_steers"])

    def test_child_recovery_preserves_reasoning_order_and_event_namespace(self):
        child = self.child()
        message = {"content": "answer", "reasoning": "thought", "segments": [
            {"type": "reasoning", "text": "thought"}, {"type": "content", "text": "answer"}]}
        store.checkpoint(child["id"], message)
        store.save_provider_messages(child["id"], [{"role": "user", "content": "inspect"}, {"role": "assistant", "content": "answer"}])
        cursor = store.cursor(1)
        store.recover_one(db.fetchone("SELECT * FROM chat_runs WHERE id=?", (child["id"],)))
        recovered = store.get_run(1, child["id"])
        self.assertEqual(recovered["status"], "queued")
        self.assertEqual(recovered["message"]["reasoning"], "thought")
        self.assertEqual(recovered["message"]["segments"], message["segments"])
        events = store.events_after(1, cursor)
        self.assertEqual([e["type"] for e in events], ["agent.resumed", "agent.progress"])
        self.assertEqual(events[0]["payload"]["status"], "queued")
        self.assertEqual(events[0]["payload"]["child_id"], child["id"])

    def test_finalization_keeps_failed_join_and_live_command_poll(self):
        from starlette.responses import StreamingResponse

        child = self.child()
        store.finish_run(child["id"], {"content": "", "error": "child failed"}, "failed")

        async def frames():
            for trace in (
                {"id": "wait", "name": "get_subagent_output", "child_id": child["id"], "status": "running", "preview": "still running"},
                {"id": "poll", "name": "get_command_output", "child_id": "job", "status": "running"},
            ):
                yield runtime._sse({"fuse": {"event": "tool", **trace}})
            yield b"data: [DONE]\n\n"

        async def run():
            response = StreamingResponse(frames())
            with patch.object(runtime, "stream_dashboard_chat", AsyncMock(return_value=response)), patch.object(chat_runs, "schedule_wake"), patch("app.command_jobs.job_status", return_value="running"):
                await chat_runs._run(Request(), {"id": 1}, self.parent)

        asyncio.run(run())
        tools = store.get_run(1, self.parent["id"])["message"]["tools"]
        self.assertEqual([(t["id"], t["status"]) for t in tools], [("wait", "error"), ("poll", "running")])
        self.assertEqual(tools[0]["error"], "child failed")
