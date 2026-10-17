"""Offline durable supervisor tests; all database writes use a temporary DB."""
import asyncio
import json
import sqlite3
import sys
import tempfile
import unittest
from contextlib import suppress
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi import Request
from fastapi.responses import StreamingResponse
from app import chat_runs as runs, chat_runtime as runtime, chat_store as store, chats, db


def frame(obj):
    return ("data: " + json.dumps(obj, ensure_ascii=False) + "\r\n\r\n").encode()


class RunsTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="fuse-runs-")
        self.addCleanup(self.tmp.cleanup)
        self.conn = sqlite3.connect(str(Path(self.tmp.name) / "test.db"), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        db._init(self.conn)
        self.addCleanup(self.conn.close)
        patcher = patch.object(db, "connect", return_value=self.conn)
        patcher.start()
        self.addCleanup(patcher.stop)
        db.execute("INSERT INTO users(id,email,password_hash,created_at) VALUES(1,'test@invalid','unused',?)", (db.now(),))
        store.init_schema()
        from app import command_jobs
        with patch.object(command_jobs, "_schema_ready", False):
            command_jobs.ensure_schema()
        runs._stopping = False
        title = patch.object(runtime, "generate_thread_title", new_callable=AsyncMock, return_value="")
        title.start()
        self.addCleanup(title.stop)
        self.user = {"id": 1}
        self.request = Request({"type": "http", "headers": [(b"x-session-id", b"session")],
                                "session": {"user_id": 1}, "method": "POST", "path": "/chat"},
                               receive=self.disconnected)

    async def disconnected(self):
        raise AssertionError("Original browser receive must never be used")

    async def test_wake_batches_whole_reports_and_only_selected_command_receipts(self):
        from app import command_jobs
        run = self.turn()
        store.finish_run(run["id"], {"content": "Done"}, "completed")
        candidates = [{"id": "child", "status": "completed", "summary": "REPORT" * 20000,
                       "description": "Large report", "generation": "leg", "attempts": 0}]
        commands = [{"id": "first", "envelope": "<command_result>" + "A" * 20000 + "</command_result>"},
                    {"id": "later", "envelope": "<command_result>" + "B" * 40000 + "</command_result>"}]
        with patch.object(chats, "AUTO_CONTENT_MAX", 160000), \
             patch.object(runs, "_wake_locks", {}), \
             patch.object(store, "wake_candidates", return_value=candidates), \
             patch.object(command_jobs, "pending_envelopes", return_value=commands), \
             patch.object(command_jobs, "mark_delivered") as acknowledge, \
             patch.object(runs, "start", new_callable=AsyncMock):
            await runs._wake_parent(1, run["thread_id"])
            # Both roots share created_at (second granularity) and the uuid
            # tie-break in thread_root_run is a coin flip; the wake turn is
            # deterministically the root that is not the original run.
            roots = db.fetchall(
                "SELECT id FROM chat_runs WHERE user_id=1 AND thread_id=? AND parent_run_id IS NULL",
                (run["thread_id"],),
            )
            wake_id = next(row["id"] for row in roots if row["id"] != run["id"])
            wake = store.get_run(1, wake_id)
            seed = wake["request"]["messages"][-1]["content"]
            self.assertIn(candidates[0]["summary"], seed)
            self.assertIn(commands[0]["envelope"], seed)
            self.assertNotIn(commands[1]["envelope"], seed)
            self.assertEqual(acknowledge.call_args.args[0], ["first"])
            shown = chats.get_thread(1, run["thread_id"])["messages"][-2]["content"]
            self.assertEqual(shown, seed)

    async def asyncTearDown(self):
        await runs.stop()
        runs._stopping = False

    def turn(self, auto_title=False, text="Hello"):
        chats.set_user_context(1, instructions="Frozen instructions", profile="Frozen profile")
        thread = chats.create_thread(1, model="offline", tools=[])
        return store.start_turn(1, thread["id"], text, thread["revision"], None, auto_title=auto_title)["run"]

    async def launch(self, run, gen):
        async def mocked(request, user, raw, *, frozen_context):
            self.assertIsNot(request, self.request)
            self.assertEqual(request.headers["x-session-id"], "session")
            self.assertIsNot(request.scope["session"], self.request.scope["session"])
            self.assertFalse(await request.is_disconnected())
            self.assertEqual(frozen_context["instructions"], "Frozen instructions")
            return StreamingResponse(gen)
        patcher = patch.object(runtime, "stream_dashboard_chat", side_effect=mocked)
        mock = patcher.start()
        self.addCleanup(patcher.stop)
        task = await runs.start(self.request, self.user, run)
        return task, mock

    async def test_stop_during_terminal_commit_cannot_wake_thread(self):
        run = self.turn()
        child = store.start_child_run(
            user_id=1, thread_id=run["thread_id"], parent_run_id=run["id"],
            prompt="background work", description="background", background=True,
        )
        finish = store.finish_run

        def stop_at_commit(ident, message, status):
            if ident == run["id"]:
                store.request_cancel(1, ident)
                finish(child["id"], {"content": "Stopped report"}, "cancelled")
            return finish(ident, message, status)

        async def gen():
            yield frame({"choices": [{"delta": {"content": "Done"}}]})
            yield b"data: [DONE]\n\n"

        with patch.object(store, "finish_run", side_effect=stop_at_commit), \
             patch.object(runs, "schedule_wake"):
            task, _ = await self.launch(run, gen())
            await task
        result = store.get_run(1, run["id"])
        self.assertEqual(result["status"], "cancelled")
        self.assertTrue(result["message"]["stopped"])
        self.assertEqual(result["message"]["status"], "cancelled")
        with patch.object(runs, "start", new_callable=AsyncMock) as start:
            await runs._wake_parent(1, run["thread_id"])
            start.assert_not_called()
        self.assertEqual(len(db.fetchall("SELECT id FROM chat_runs WHERE parent_run_id IS NULL")), 1)
        events = store.events_after(1, 0)
        self.assertTrue(any(e["type"] == "run.cancelled" for e in events))
        self.assertFalse(any(e["type"] == "run.completed" for e in events))

    async def test_stopped_wake_sweeps_do_not_load_reports_or_schedule_wakes(self):
        run = self.turn()
        child = store.start_child_run(
            user_id=1, thread_id=run["thread_id"], parent_run_id=run["id"],
            prompt="background work", description="background", background=True,
        )
        store.request_cancel(1, run["id"])
        store.finish_run(run["id"], {"content": "Stopped"}, "completed")
        store.finish_run(child["id"], {"content": "Undelivered report"}, "cancelled")
        with patch.object(runs, "schedule_wake") as wake, \
             patch.object(store, "wake_candidates", wraps=store.wake_candidates) as candidates, \
             patch.object(db, "fetchall", wraps=db.fetchall) as fetch:
            for _ in range(5):
                runs.sweep_wakes()
            wake.assert_not_called()
            candidates.assert_not_called()
            self.assertFalse(any("message," in str(call.args[0]) for call in fetch.call_args_list))
        self.assertEqual(store.wake_candidates(1, run["thread_id"])[0]["id"], child["id"])
        self.assertEqual(store.get_run(1, child["id"])["message"]["content"], "Undelivered report")
        # Reports stay undelivered, and a later user turn releases the hold.
        thread = chats.get_thread(1, run["thread_id"])
        next_run = store.start_turn(1, run["thread_id"], "Continue", thread["revision"], None)["run"]
        store.finish_run(next_run["id"], {"content": "Continued"}, "completed")
        with patch.object(runs, "schedule_wake") as wake:
            runs.sweep_wakes()
            wake.assert_called_once_with(1, run["thread_id"])

    async def test_explicit_child_resume_after_stop_can_wake(self):
        run = self.turn()
        child = store.start_child_run(
            user_id=1, thread_id=run["thread_id"], parent_run_id=run["id"],
            prompt="background work", description="background", background=True,
        )
        store.request_cancel(1, run["id"])
        store.finish_run(run["id"], {"content": "Stopped"}, "cancelled")
        store.finish_run(child["id"], {"content": "Resumed report"}, "completed")
        request = store.get_run(1, child["id"])["request"]
        request["agent"]["user_resumed_at"] = "9999-01-01T00:00:00Z"
        db.execute("UPDATE chat_runs SET request=?,cancel_requested=0,status='completed' WHERE id=?",
                   (json.dumps(request), child["id"]))
        with patch.object(runs, "schedule_wake") as wake:
            runs.sweep_wakes()
            wake.assert_called_once_with(1, run["thread_id"])
        with patch.object(runs, "start", new_callable=AsyncMock) as start:
            await runs._wake_parent(1, run["thread_id"])
            start.assert_awaited_once()

    async def test_continuation_handoff_keeps_owner_during_database_read(self):
        run = self.turn()
        reads = []
        get_run = store.get_run
        calls = 0

        def read_with_reaper(user_id, ident):
            result = get_run(user_id, ident)
            if result and result["status"] == "queued" and (result["request"].get("agent") or {}).get("followup_continuation"):
                owner = runs._tasks.get(ident)
                reads.append((owner, owner is not None and not owner.done()))
                runs.reap_orphans(user_id)
                result = get_run(user_id, ident)
            return result

        async def mocked(request, user, raw, *, frozen_context):
            nonlocal calls
            calls += 1
            async def gen():
                if calls == 1:
                    store.save_turn_progress(run["id"], [{"role": "user", "content": "Hello"},
                        {"role": "assistant", "content": "First reply"}])
                    store.steer_turn(1, run["thread_id"], "Follow up", "handoff-steer")
                else:
                    steers = store.take_steers(run["id"])
                    store.save_turn_progress(run["id"], raw["messages"] + [store.steer_message(item) for item in steers])
                yield frame({"choices": [{"delta": {"content": "reply"}}]})
                yield b"data: [DONE]\n\n"
            return StreamingResponse(gen())

        with patch.object(runtime, "stream_dashboard_chat", side_effect=mocked), \
             patch.object(store, "get_run", side_effect=read_with_reaper), \
             patch.object(runs, "schedule_wake"):
            task = await runs.start(self.request, self.user, run)
            await task
            continuation = runs._tasks.get(run["id"])
            self.assertIsNotNone(continuation)
            self.assertIsNot(continuation, task)
            await continuation
        self.assertTrue(reads)
        self.assertTrue(any(owner is task for owner, _ in reads))
        self.assertTrue(all(live for _, live in reads))
        result = store.get_run(1, run["id"])
        self.assertEqual(calls, 2)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["resume_count"], 0)
        self.assertNotIn("server restart", result["message"].get("notice", ""))
        self.assertFalse((result["request"].get("agent") or {}).get("resumed"))

    async def test_child_followup_handoff_does_not_spend_restart_budget(self):
        parent = self.turn()
        child = store.start_child_run(
            user_id=1, thread_id=parent["thread_id"], parent_run_id=parent["id"],
            prompt="work", description="child", background=True,
        )
        reads = []
        calls = 0
        get_run = store.get_run

        def read_with_reaper(user_id, ident):
            row = get_run(user_id, ident)
            if row and ident == child["id"] and row["status"] == "queued" and (row["request"].get("agent") or {}).get("followup_continuation"):
                owner = runs._tasks.get(ident)
                reads.append(owner is not None and not owner.done())
                runs.reap_orphans(user_id)
                row = get_run(user_id, ident)
            return row

        async def mocked(request, user, raw, *, frozen_context):
            nonlocal calls
            calls += 1
            async def gen():
                if calls == 1:
                    store.save_turn_progress(child["id"], [{"role": "user", "content": "work"},
                        {"role": "assistant", "content": "report"}])
                    self.assertTrue(store.queue_followup(child["id"], "more work"))
                yield frame({"choices": [{"delta": {"content": "report"}}]})
                yield b"data: [DONE]\n\n"
            return StreamingResponse(gen())

        # The parent need not own a worker for this child-only handoff test.
        store.finish_run(parent["id"], {"content": "Waiting"}, "completed")
        with patch.object(runtime, "stream_dashboard_chat", side_effect=mocked), \
             patch.object(store, "get_run", side_effect=read_with_reaper), \
             patch.object(runs, "schedule_wake"):
            task = await runs.start(self.request, self.user, child)
            await task
            continuation = runs._tasks.get(child["id"])
            self.assertIsNotNone(continuation)
            await continuation
        self.assertTrue(reads)
        self.assertTrue(all(reads))
        result = get_run(1, child["id"])
        self.assertEqual(calls, 2)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["resume_count"], 0)
        self.assertFalse(result["request"]["agent"].get("resumed"))

    async def test_worker_start_offloads_read_and_reserves_ownership(self):
        import threading
        run = self.turn()
        loop_thread = threading.get_ident()
        get_run = store.get_run
        reads = []

        def read_with_reaper(user_id, ident):
            reads.append(threading.get_ident())
            runs.reap_orphans(user_id)
            return get_run(user_id, ident)

        async def gen():
            yield frame({"choices": [{"delta": {"content": "reply"}}]})
            yield b"data: [DONE]\n\n"

        with patch.object(runtime, "stream_dashboard_chat", new_callable=AsyncMock,
                          return_value=StreamingResponse(gen())), \
             patch.object(store, "get_run", side_effect=read_with_reaper), \
             patch.object(runs, "schedule_wake"):
            first, second = await asyncio.gather(
                runs.start(self.request, self.user, run), runs.start(self.request, self.user, run))
            self.assertIsNotNone(first)
            self.assertIs(first, second)
            await first
        self.assertTrue(reads)
        self.assertNotIn(loop_thread, reads)
        self.assertEqual(get_run(1, run["id"])["status"], "completed")

    async def test_wakes_from_maintenance_thread_reach_event_loop(self):
        runs._worker_loop = asyncio.get_running_loop()
        reached = asyncio.Event()
        async def wake_parent(user_id, thread_id):
            reached.set()
        with patch.object(runs, "_wake_parent", side_effect=wake_parent) as wake:
            await asyncio.to_thread(runs.schedule_wake, 1, "off-thread")
            await asyncio.wait_for(reached.wait(), 2)
            wake.assert_awaited_once_with(1, "off-thread")

    async def test_split_frames_reasoning_tools_replay_and_idempotence(self):
        run = self.turn()
        objects = [
            {"choices": [{"delta": {"content": "<thi"}}]},
            {"choices": [{"delta": {"content": "nk>why</think>caf\u00e9", "reasoning": {"summary": "explicit"}}}]},
            {"fuse": {"event": "tool", "id": "tool1", "name": "web_search", "status": "running", "query": "q"}},
            {"fuse": {"event": "tool", "id": "tool1", "status": "progress", "n": 2, "latency_ms": 12}},
            {"fuse": {"event": "tool", "id": "tool1", "status": "done", "results": []}},
            {"fuse": {"event": "notice", "message": "A notice"}},
            {"fuse": {"event": "notice", "message": "a notice"}},
            {"usage": {"total_tokens": 12}},
        ]
        closed = asyncio.Event()

        async def gen():
            try:
                wire = b"".join(frame(o) for o in objects) + b"data: [DONE]\n\n"
                for i in range(0, len(wire), 3):
                    yield wire[i:i + 3]
            finally:
                closed.set()

        task, mock = await self.launch(run, gen())
        self.assertIs(await runs.start(self.request, self.user, run), task)
        await task
        self.assertTrue(closed.is_set())
        result = store.get_run(1, run["id"])
        self.assertEqual(result["status"], "completed")
        msg = result["message"]
        self.assertEqual(msg["content"], "caf\u00e9")
        self.assertEqual(msg["reasoning"], "explicit\nwhy")
        self.assertEqual(msg["tools"][0]["status"], "done")
        self.assertFalse(msg.get("notice"))
        self.assertEqual(msg["segments"][-1], {"type": "marker", "kind": "notice", "text": "A notice"})
        self.assertEqual(sum(part.get("kind") == "notice" for part in msg["segments"]), 1)
        replay = runs.Output(run)
        for event in store.events_after(1, 0):
            if event["type"] == "run.progress":
                for obj in event["payload"]["events"]:
                    patch = obj.get("fuse", {})
                    if patch.get("event") == "message.patch":
                        for field in ("content", "reasoning"):
                            old = replay.message.get(field, "").encode("utf-16-le")
                            replay.message[field] = old[:patch[field]["from"] * 2].decode("utf-16-le") + patch[field]["text"]
                        change = patch.get("segments") or {}
                        if "from" in change:
                            replay.message["segments"][change["from"]:] = change["items"]
                        else:
                            for text_patch in change.get("texts", []):
                                segment = replay.message["segments"][text_patch["index"]]
                                old = segment["text"].encode("utf-16-le")
                                segment["text"] = old[:text_patch["from"] * 2].decode("utf-16-le") + text_patch["text"]
                                if "ms" in text_patch:
                                    segment["ms"] = text_patch["ms"]
                            replay.message["segments"].extend(change.get("append", []))
                        replay.message["usage"] = patch["usage"]
                    else:
                        replay.apply(obj)
        for key in ("content", "reasoning", "tools", "segments", "notice", "usage"):
            self.assertEqual(replay.stored().get(key), msg.get(key))
        self.assertIsNone(await runs.start(self.request, self.user, run))
        self.assertEqual(mock.call_count, 1)

    async def test_cancelled_registry_entry_cannot_block_a_queued_worker(self):
        run = self.turn()
        obsolete = asyncio.create_task(asyncio.sleep(10))
        obsolete.cancel()
        await asyncio.gather(obsolete, return_exceptions=True)
        runs._tasks[run["id"]] = obsolete
        runs._cancelled.add(run["id"])
        async def gen():
            yield frame({"choices": [{"delta": {"content": "Fresh worker produced output"}}]})
            yield b"data: [DONE]\n\n"
        task, _ = await self.launch(run, gen())
        self.assertIsNot(task, obsolete)
        await task
        result = store.get_run(1, run["id"])
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["message"]["content"], "Fresh worker produced output")

    async def test_flush_during_gap_and_http_cancel(self):
        run = self.turn()
        closed = asyncio.Event()

        async def gen():
            try:
                yield frame({"choices": [{"delta": {"content": "partial"}}]})
                await asyncio.Future()
            finally:
                closed.set()

        task, _ = await self.launch(run, gen())
        async with asyncio.timeout(2):
            while store.get_run(1, run["id"])["message"]["content"] != "partial":
                await asyncio.sleep(.01)
        self.assertFalse(task.done())
        store.request_cancel(1, run["id"])
        runs.cancel(run["id"])
        await asyncio.wait_for(task, 2)
        self.assertTrue(closed.is_set())
        self.assertEqual(store.get_run(1, run["id"])["status"], "cancelled")

    async def test_send_now_interrupt_ends_the_reply_but_keeps_its_subagents(self):
        # Send now used Stop's tree cancel, which ended every subagent in the
        # chat with "Subagent stopped by cancellation".
        first = self.turn()
        earlier = store.start_child_run(
            user_id=1, thread_id=first["thread_id"], parent_run_id=first["id"],
            prompt="earlier work", description="earlier", background=True,
        )
        store.finish_run(first["id"], {"content": "Done"}, "completed")
        thread = chats.get_thread(1, first["thread_id"])
        run = store.start_turn(1, first["thread_id"], "Next", thread["revision"], None)["run"]
        background = store.start_child_run(
            user_id=1, thread_id=run["thread_id"], parent_run_id=run["id"],
            prompt="background work", description="background", background=True,
        )
        foreground = store.start_child_run(
            user_id=1, thread_id=run["thread_id"], parent_run_id=run["id"],
            prompt="foreground work", description="foreground",
        )

        async def gen():
            # The reply is waiting on its foreground spawn when Send now lands.
            yield frame({"fuse": {"event": "tool", "id": "spawn", "name": "spawn_subagent",
                                  "status": "running", "child_id": foreground["id"]}})
            await asyncio.Future()

        task, _ = await self.launch(run, gen())
        async with asyncio.timeout(2):
            while not store.get_run(1, run["id"])["message"].get("tools"):
                await asyncio.sleep(.01)
        with patch("app.workspace.cancel_run_jobs") as jobs:
            await runs.interrupt_wait(1, run["id"])
            await asyncio.wait_for(task, 2)
        stopped = store.get_run(1, run["id"])
        self.assertEqual(stopped["status"], "cancelled")
        for child in (earlier, background, foreground):
            row = store.get_run(1, child["id"])
            self.assertIn(row["status"], ("queued", "running"))
            self.assertFalse(row["cancel_requested"])
        # The in-flight foreground child was handed to the background: its
        # row stays live and its report reaches the next turn.
        self.assertTrue(store.get_run(1, foreground["id"])["request"]["agent"]["background"])
        self.assertEqual(stopped["message"]["tools"][0]["status"], "running")
        jobs.assert_not_called()  # background commands keep running too

    async def test_delete_poll_cancels_provider(self):
        run = self.turn()
        entered = asyncio.Event()

        async def gen():
            entered.set()
            await asyncio.Future()
            yield b""

        task, _ = await self.launch(run, gen())
        await entered.wait()
        chats.delete_thread(1, run["thread_id"])
        await asyncio.wait_for(task, 2)
        # Deleting the thread purges its runs; the provider task notices and stops.
        self.assertIsNone(store.get_run(1, run["id"]))
        self.assertIsNone(chats.get_thread(1, run["thread_id"]))

    async def test_shutdown_before_task_enters(self):
        run = self.turn()

        async def gen():
            await asyncio.Future()
            yield b""

        task, mock = await self.launch(run, gen())
        await asyncio.wait_for(runs.shutdown(), 2)
        self.assertTrue(task.done())
        self.assertEqual(store.get_run(1, run["id"])["status"], "interrupted")
        mock.assert_not_called()

    async def test_missing_done_error_timeout_and_output_limit(self):
        for mode in ("eof", "error", "timeout", "limit"):
            with self.subTest(mode=mode):
                run = self.turn()

                async def gen():
                    if mode == "timeout":
                        await asyncio.Future()
                    elif mode == "error":
                        yield frame({"error": {"message": "provider failed"}})
                        yield b"data: [DONE]\n\n"
                    else:
                        yield frame({"choices": [{"delta": {"content": "x" * (chats.CONTENT_MAX + 1 if mode == "limit" else 1)}}]})

                with patch.object(runs, "MAX_RUNTIME_SECONDS", .05), patch.object(chats, "ASSISTANT_CONTENT_MAX", chats.CONTENT_MAX):
                    task, _ = await self.launch(run, gen())
                    await asyncio.wait_for(task, 2)
                result = store.get_run(1, run["id"])
                self.assertEqual(result["status"], "failed")
                self.assertTrue(result["message"]["error"])
                self.assertLessEqual(len(result["message"]["content"]), chats.CONTENT_MAX)

    async def test_runtime_terminal_recognition(self):
        async def tap(stream, *args, **kwargs):
            async for chunk in stream:
                yield chunk

        for terminal in (b"", b"data: [DONE]\n\n", b"data: [DONE]",
                         frame({"choices": [{"finish_reason": "stop"}]}).rstrip()):
            async def stream():
                yield frame({"choices": [{"delta": {"content": "partial"}}]})
                yield terminal

            result = SimpleNamespace(stream=stream(), credential_id=None)
            src = SimpleNamespace(source_id=1, type_id="openai")
            with patch.object(runtime.api, "_tap", tap):
                parts = [p async for p in runtime._consume_round(result, self.user, src, "model", "model")]
            self.assertEqual(bool(parts[-1][1]["error"]), not bool(terminal))

    async def test_running_shutdown_closes_nested_tools(self):
        run = self.turn()
        entered, closed = asyncio.Event(), asyncio.Event()

        async def execute(*args, **kwargs):
            entered.set()
            try:
                await asyncio.Future()
            finally:
                closed.set()

        async def gen():
            from contextlib import aclosing
            calls = [{"id": "call", "function": {"name": "web_search", "arguments": "{}"}}]
            request = Request(dict(self.request.scope), receive=lambda: asyncio.Future())
            async with aclosing(runtime._run_tools(calls, user_id=1, allowed_urls=set(), request=request,
                                                   completion_i=0, budgets={})) as tools:
                async for item in tools:
                    if isinstance(item, dict):
                        yield frame({"fuse": item})

        with patch.object(runtime, "execute_tool", execute):
            task, _ = await self.launch(run, gen())
            await asyncio.wait_for(entered.wait(), 2)
            await asyncio.wait_for(runs.shutdown(), 2)
        self.assertTrue(closed.is_set())
        result = store.get_run(1, run["id"])
        self.assertEqual(result["status"], "interrupted")
        self.assertEqual(result["message"]["tools"][0]["status"], "interrupted")

    async def test_retrieval_disconnect_cannot_wait_forever(self):
        entered, closed = asyncio.Event(), asyncio.Event()

        async def search(*args, **kwargs):
            entered.set()
            try:
                await asyncio.Future()
            finally:
                closed.set()

        class Disconnect:
            calls = 0

            async def is_disconnected(self):
                self.calls += 1
                return self.calls > 1

        with patch("app.search.engine.search_bulk", search), patch("app.chat_generation.messages.HEARTBEAT_S", .01):
            async with asyncio.timeout(2):
                results = [item async for item in runtime._retrieval_first(
                    Disconnect(), 1, [{"role": "user", "content": "test"}], {})]
        self.assertTrue(entered.is_set())
        self.assertTrue(closed.is_set())
        self.assertEqual(results[-1], ("packed", ""))

    async def test_public_context_overrides_ignored(self):
        raw = {"model": "offline", "messages": [{"role": "user", "content": "hello"}],
               "instructions": "browser override", "profile": "browser profile"}
        with patch.object(chats, "get_user_context", return_value={"instructions": "account", "profile": "saved"}), \
             patch.object(runtime.artifacts, "list_for_prompt", return_value=[]), \
             patch.object(runtime, "build_system_prompt", return_value="system") as prompt:
            await runtime.stream_dashboard_chat(self.request, self.user, raw)
            self.assertEqual(prompt.call_args.kwargs["instructions"], "account")
            await runtime.stream_dashboard_chat(self.request, self.user, raw,
                                                frozen_context={"instructions": "frozen", "profile": "old"})
            self.assertEqual(prompt.call_args.kwargs["instructions"], "frozen")

    async def test_title_is_generated_while_the_root_turn_runs(self):
        # Claude Code names the session from its first prompt in parallel with
        # the turn; the title must not wait for the turn to end.
        from app import chat_titles
        run = self.turn(auto_title=True, text="Convert this CSV file to JSON")
        release = asyncio.Event()

        async def gen():
            await release.wait()
            yield frame({"choices": [{"delta": {"content": "answer " + "with details " * 30}}]})
            yield b"data: [DONE]\n\n"

        async def mocked(request, user, raw, *, frozen_context):
            return StreamingResponse(gen())

        chat_titles._due.clear()
        with patch.object(runtime, "stream_dashboard_chat", side_effect=mocked), \
             patch.object(runtime, "generate_thread_title", new_callable=AsyncMock, return_value="CSV to JSON") as gen_title:
            task = await runs.start(self.request, self.user, run)
            chat_titles.notify(run["thread_id"])
            await chat_titles.sweep()
            await asyncio.gather(*list(chat_titles._tasks))
            gen_title.assert_awaited_once()
            self.assertFalse(task.done())
            self.assertEqual(chats.get_thread(1, run["thread_id"])["title"], "CSV to JSON")
            release.set()
            await asyncio.wait_for(task, 2)
        thread = chats.get_thread(1, run["thread_id"])
        self.assertEqual(thread["title"], "CSV to JSON")
        self.assertIn("answer", thread["messages"][-1]["content"])
        await chat_titles.drain()

    async def test_title_failure_keeps_provisional_title(self):
        from app import chat_titles
        run = self.turn(auto_title=True, text="Convert this CSV file to JSON")
        provisional = chats.get_thread(1, run["thread_id"])["title"]

        async def gen():
            yield frame({"choices": [{"delta": {"content": "Here is a longer answer about it. " * 10}}]})
            yield b"data: [DONE]\n\n"

        async def mocked(request, user, raw, *, frozen_context):
            return StreamingResponse(gen())

        with patch.object(runtime, "stream_dashboard_chat", side_effect=mocked), \
             patch.object(runtime, "generate_thread_title", new_callable=AsyncMock, return_value=""):
            task = await runs.start(self.request, self.user, run)
            await asyncio.wait_for(task, 2)
            await chat_titles.sweep()
            await asyncio.gather(*list(chat_titles._tasks))
        self.assertEqual(chats.get_thread(1, run["thread_id"])["title"], provisional)
        row = db.fetchone("SELECT title_attempts FROM chat_threads WHERE id=?", (run["thread_id"],))
        self.assertEqual(row["title_attempts"], 1)
        await chat_titles.drain()

    async def test_output_keeps_checkpointed_tools_and_text(self):
        run = self.turn()
        store.checkpoint(
            run["id"],
            {
                "content": "earlier",
                "tools": [{"name": "web_search", "status": "done", "id": "ts_0_0", "query": "q"}],
            },
        )
        output = runs.Output(store.get_run(1, run["id"]))
        self.assertEqual(output.message["content"], "earlier")
        self.assertEqual(output.message["tools"][0]["id"], "ts_0_0")
        output.apply({"choices": [{"delta": {"content": " more"}}]})
        self.assertEqual(output.stored()["content"], "earlier more")
        self.assertEqual(output.stored()["tools"][0]["status"], "done")

    def test_stream_out_counts_this_round_and_hidden_tool_calls(self):
        output = runs.Output(self.turn())
        output.apply({"choices": [{"delta": {"reasoning": "abcd" * 4}}]})
        self.assertEqual(output._stream_out, 4)
        output.apply({"choices": [{"delta": {"content": "efgh" * 4}}]})
        self.assertEqual(output._stream_out, 8)
        output.apply({"fuse": {"event": "activity", "phase": "model"}})
        self.assertEqual(output._stream_out, 0)
        output.apply({"choices": [{"delta": {"content": "ijkl" * 2}}]})
        self.assertEqual(output._stream_out, 2)
        # The mid-round thinking marker does not start a new round.
        output.apply({"fuse": {"event": "activity", "phase": "model", "thinking": True}})
        self.assertEqual(output._stream_out, 2)
        output.apply({"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"name": "read_file", "arguments": "x" * 20}}]}}]})
        self.assertGreaterEqual(output._stream_out, 7)
        output.apply({"fuse": {"event": "tool", "id": "t1", "name": "read_file", "status": "running", "path": "a"}})
        self.assertEqual(output._stream_out, 0)

    async def test_output_strips_eos_and_hides_xml_tool_dumps(self):
        output = runs.Output(self.turn())
        output.apply({"choices": [{"delta": {"content": "Setting `_starting`. <|eos|>"}}]})
        self.assertEqual(output.stored()["content"], "Setting `_starting`. ")
        output.apply({"choices": [{"delta": {"content": "\n<tool_call>\n<function=web_search>"}}]})
        self.assertNotIn("<tool_call>", output.stored()["content"])
        output.apply({
            "choices": [{
                "delta": {"content": "\n<parameter=query>x</parameter></function></tool_call>After."}
            }]
        })
        stored = output.stored()
        self.assertNotIn("<tool_call>", stored["content"])
        self.assertIn("After.", stored["content"])
        self.assertNotIn("<|eos|>", stored["content"])

    def test_run_notices_sit_in_place_until_output_supersedes_them(self):
        output = runs.Output(self.turn())
        notice = lambda text: {"fuse": {"event": "notice", "message": text}}
        output.apply({"choices": [{"delta": {"content": "Hello wor"}}]})
        output.apply(notice("Provider stream interrupted — resuming in 2s (attempt 1 of 4)."))
        output.apply(notice("Provider stream interrupted — resuming in 6s (attempt 2 of 4)."))
        self.assertEqual([part.get("kind") for part in output.message["segments"]], [None, "notice", "notice"])
        # The resumed stream continues the same text; the notices are gone.
        output.apply({"choices": [{"delta": {"content": "ld."}}]})
        stored = output.stored()
        self.assertEqual(stored["content"], "Hello world.")
        self.assertEqual(stored["segments"], [{"type": "content", "text": "Hello world."}])
        output.apply({"fuse": {"event": "tool", "id": "t1", "name": "read_file", "status": "running", "path": "a"}})
        output.apply(notice("Provider error (500) — retrying this step in 2s (attempt 1 of 4)."))
        output.apply({"fuse": {"event": "tool", "id": "t2", "name": "read_file", "status": "running", "path": "b"}})
        # A notice superseded by a tool row is dropped when the next one lands.
        output.apply(notice("Provider error (500) — retrying this step in 2s (attempt 1 of 4)."))
        self.assertEqual([part.get("kind") or part["type"] for part in output.stored()["segments"]],
                         ["content", "tool", "tool", "notice"])

    async def test_output_preserves_chronological_segments(self):
        output = runs.Output(self.turn())
        output.apply({"choices": [{"delta": {"reasoning": "think one"}}]})
        output.apply({"choices": [{"delta": {"content": "First answer."}}]})
        output.apply({"fuse": {"event": "tool", "id": "tool1", "name": "run_terminal_command",
                               "status": "running", "command": "ps"}})
        output.apply({"fuse": {"event": "tool", "id": "tool1", "name": "run_terminal_command",
                               "status": "done", "command": "ps", "preview": "ok"}})
        output.apply({"choices": [{"delta": {"reasoning": "think two"}}]})
        output.apply({"choices": [{"delta": {"content": "Second answer."}}]})

        stored = output.stored()["segments"]
        # Each thought carries its measured duration.
        self.assertTrue(all(type(part["ms"]) is int and part["ms"] >= 0 for part in stored if part["type"] == "reasoning"))
        self.assertEqual(
            [{k: v for k, v in part.items() if k != "ms"} for part in stored],
            [
                {"type": "reasoning", "text": "think one"},
                {"type": "content", "text": "First answer."},
                {"type": "tool", "id": "tool1"},
                {"type": "reasoning", "text": "think two"},
                {"type": "content", "text": "\n\nSecond answer."},
            ],
        )
        self.assertEqual(output.stored()["content"], "First answer.\n\nSecond answer.")

    async def test_segment_patch_handles_text_growth_and_new_boundaries(self):
        old = [{"type": "content", "text": "hello"}]
        grown = [{"type": "content", "text": "hello world"}]
        self.assertEqual(
            runs._segments_patch(old, grown),
            {"texts": [{"index": 0, "from": 5, "text": " world"}]},
        )
        with_tool = [*grown, {"type": "tool", "id": "tool1"}]
        self.assertEqual(runs._segments_patch(grown, with_tool), {"base": len(grown), "append": [{"type": "tool", "id": "tool1"}]})

    async def test_resume_queued_continues_after_recover(self):
        run = self.turn()
        store.checkpoint(
            run["id"],
            {"content": "", "tools": [{"name": "web_search", "status": "done", "id": "ts_0_0"}]},
        )
        store.save_turn_progress(
            run["id"],
            [
                {"role": "user", "content": "Hello"},
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {"id": "c1", "type": "function", "function": {"name": "web_search", "arguments": "{}"}},
                    ],
                },
                {"role": "tool", "tool_call_id": "c1", "content": "hits"},
            ],
        )
        store.recover_runs()
        recovered = store.get_run(1, run["id"])
        self.assertEqual(recovered["status"], "queued")
        self.assertTrue(recovered["request"]["agent"]["resumed"])

        async def gen():
            yield frame({"choices": [{"delta": {"content": "done"}}]})
            yield b"data: [DONE]\n\n"

        async def mocked(request, user, raw, *, frozen_context):
            self.assertTrue(raw.get("agent", {}).get("skip_sanitize"))
            # The sealed transcript ends with the idempotent resume note (a
            # real user-role chat message), appended after the tool results.
            self.assertEqual(raw["messages"][-2]["role"], "tool")
            self.assertEqual(raw["messages"][-1]["role"], "user")
            self.assertIn(f"resume-note:{run['id']}:1", raw["messages"][-1]["content"])
            return StreamingResponse(gen())

        with patch.object(runtime, "stream_dashboard_chat", side_effect=mocked):
            await runs.resume_queued(1)
            task = runs._tasks.get(run["id"])
            self.assertIsNotNone(task)
            await asyncio.wait_for(task, 2)
        self.assertEqual(store.get_run(1, run["id"])["status"], "completed")
        self.assertIn("done", chats.get_thread(1, run["thread_id"])["messages"][-1]["content"])
        self.assertEqual(chats.get_thread(1, run["thread_id"])["messages"][-1]["tools"][0]["status"], "done")

    async def test_resume_queued_starts_recovered_child(self):
        thread = chats.create_thread(1, model="offline", tools=[])
        parent = store.start_turn(1, thread["id"], "Hello", thread["revision"], None)["run"]
        child = store.start_child_run(
            user_id=1,
            thread_id=thread["id"],
            parent_run_id=parent["id"],
            prompt="look around",
            description="explore",
        )
        store.save_turn_progress(
            child["id"],
            [
                {"role": "user", "content": "look around"},
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {"id": "c1", "type": "function", "function": {"name": "list_dir", "arguments": "{}"}},
                    ],
                },
                {"role": "tool", "tool_call_id": "c1", "content": "files"},
            ],
        )
        store.recover_runs()
        self.assertEqual(store.get_run(1, child["id"])["status"], "queued")

        release = asyncio.Event()
        raws = []

        async def child_gen():
            yield frame({"choices": [{"delta": {"content": "child report"}}]})
            yield b"data: [DONE]\n\n"

        async def parent_gen():
            await release.wait()
            yield frame({"choices": [{"delta": {"content": "parent done"}}]})
            yield b"data: [DONE]\n\n"

        async def mocked(request, user, raw, *, frozen_context):
            raws.append(raw)
            if raw.get("run_id") == child["id"]:
                return StreamingResponse(child_gen())
            return StreamingResponse(parent_gen())

        with patch.object(runtime, "stream_dashboard_chat", side_effect=mocked):
            await runs.resume_queued(1)
            parent_task = runs._tasks.get(parent["id"])
            child_task = runs._tasks.get(child["id"])
            self.assertIsNotNone(parent_task)
            self.assertIsNotNone(child_task)
            await asyncio.wait_for(child_task, 5)
            release.set()
            await asyncio.wait_for(parent_task, 5)

        child_raw = next(r for r in raws if r.get("run_id") == child["id"])
        agent = child_raw.get("agent", {})
        self.assertTrue(agent.get("resumed"))
        self.assertTrue(agent.get("background"))
        self.assertTrue(agent.get("skip_sanitize"))
        roles = [(m.get("role"), m.get("tool_call_id")) for m in child_raw["messages"]]
        self.assertIn(("tool", "c1"), roles)
        self.assertEqual(store.get_run(1, child["id"])["status"], "completed")
        # The child's report was stashed for the still-active parent run.
        parent_row = store.get_run(1, parent["id"])
        pending = (parent_row["request"].get("agent") or {}).get("pending_child_results") or []
        self.assertTrue(any("child report" in str(item.get("text") or "") for item in pending))

    async def test_stored_aligns_over_limit_reasoning(self):
        huge = "y" * (chats.CONTENT_MAX + 50)
        output = runs.Output(self.turn())
        output.message["reasoning"] = huge
        output.message["content"] = "answer"
        output.message["segments"] = [
            {"type": "reasoning", "text": huge},
            {"type": "content", "text": "answer"},
        ]
        with patch.object(chats, "ASSISTANT_CONTENT_MAX", chats.CONTENT_MAX):
            stored = output.stored()
            parsed = chats.parse_messages([{**stored, "id": "m1", "role": "assistant"}], strict=True)[0]
        self.assertEqual(parsed["reasoning"], stored["reasoning"])
        self.assertLessEqual(len(parsed["reasoning"]), chats.CONTENT_MAX)
        self.assertEqual(
            "".join(part["text"] for part in parsed["segments"] if part["type"] == "reasoning"),
            parsed["reasoning"],
        )
        self.assertEqual(parsed["content"], "answer")

    async def test_cancel_without_worker_force_finishes(self):
        run = self.turn()
        self.assertEqual(store.get_run(1, run["id"])["status"], "queued")
        store.request_cancel(1, run["id"])
        runs.cancel(run["id"])
        result = store.get_run(1, run["id"])
        self.assertEqual(result["status"], "cancelled")
        self.assertTrue(result["message"]["stopped"])

    async def test_final_flush_error_still_finishes(self):
        run = self.turn()
        closed = asyncio.Event()

        async def gen():
            try:
                yield frame({"choices": [{"delta": {"content": "partial"}}]})
                await asyncio.Future()
            finally:
                closed.set()

        task, _ = await self.launch(run, gen())
        async with asyncio.timeout(2):
            while store.get_run(1, run["id"])["message"]["content"] != "partial":
                await asyncio.sleep(.01)

        def boom(*args, **kwargs):
            raise chats.ChatValidationError("messages[0].segments do not match its content and reasoning")

        with patch.object(store, "checkpoint", side_effect=boom):
            store.request_cancel(1, run["id"])
            runs.cancel(run["id"])
            await asyncio.wait_for(task, 2)
        self.assertTrue(closed.is_set())
        self.assertEqual(store.get_run(1, run["id"])["status"], "cancelled")
        self.assertTrue(store.get_run(1, run["id"])["message"]["stopped"])

    async def test_reap_orphans_clears_cancel_requested_without_worker(self):
        run = self.turn()
        store.request_cancel(1, run["id"])
        runs.reap_orphans(1)
        self.assertEqual(store.get_run(1, run["id"])["status"], "cancelled")

    async def test_reap_stalls_live_worker_without_progress(self):
        run = self.turn()

        async def forever():
            await asyncio.Future()

        task = asyncio.create_task(forever())
        task._fuse_started = True
        runs._tasks[run["id"]] = task
        old = (datetime.now(timezone.utc) - timedelta(seconds=200)).strftime("%Y-%m-%dT%H:%M:%SZ")
        db.execute("UPDATE chat_runs SET status='running', updated_at=? WHERE id=?", (old, run["id"]))
        runs.reap_orphans(1)
        row = store.get_run(1, run["id"])
        self.assertEqual(row["status"], "interrupted")
        self.assertIn("stalled", str((row.get("message") or {}).get("notice") or "").lower())
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        runs._tasks.pop(run["id"], None)

    async def test_reap_leaves_live_worker_with_fresh_progress(self):
        run = self.turn()

        async def forever():
            await asyncio.Future()

        task = asyncio.create_task(forever())
        task._fuse_started = True
        runs._tasks[run["id"]] = task
        db.execute("UPDATE chat_runs SET status='running', updated_at=? WHERE id=?", (db.now(), run["id"]))
        runs.reap_orphans(1)
        self.assertEqual(store.get_run(1, run["id"])["status"], "running")
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        runs._tasks.pop(run["id"], None)
        store.abort_active(run["id"], "cancelled")

    async def test_cancel_wait_finishes_missing_worker(self):
        run = self.turn()
        await runs.cancel_wait(1, run["id"], tree=True)
        self.assertEqual(store.get_run(1, run["id"])["status"], "cancelled")


if __name__ == "__main__":
    unittest.main()
