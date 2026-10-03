"""Offline acceptance/continuation, occupancy and historical job regressions."""
import asyncio
import copy
import json
import os
import sqlite3
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

_TMP = tempfile.TemporaryDirectory(prefix="fuse-continuation-")
os.environ.setdefault("DATABASE_PATH", _TMP.name + "/test.db")
os.environ.setdefault("APP_SECRET", "offline-continuation-secret" * 2)

from app import chat_runs as runs, chat_runtime as runtime, chat_store as store, chats, context, db

PNG = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+ip1sAAAAASUVORK5CYII="
ATT = {"name": "image.png", "mime": "image/png", "kind": "image", "data": PNG}


class BackendContinuationTest(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:", check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        db._init(self.conn)
        self.addCleanup(self.conn.close)
        p = patch.object(db, "connect", return_value=self.conn)
        p.start()
        self.addCleanup(p.stop)
        db.execute("INSERT INTO users(id,email,password_hash,created_at) VALUES(1,'test@test.invalid','unused',?)", (db.now(),))
        self.thread = chats.create_thread(1, model="offline")
        self.run = store.start_turn(1, self.thread["id"], "Start", self.thread["revision"], "start")["run"]

    def view(self):
        return store.thread_view(self.conn, 1, self.run["thread_id"])

    def test_accept_after_window_close_owns_continuation(self):
        self.assertTrue(store.close_steer_window(self.run["id"]))
        accepted = store.steer_turn(1, self.thread["id"], "Next task", "late")
        self.assertFalse(accepted["idempotent"])
        store.finish_run(self.run["id"], {"content": "Old answer"}, "completed")
        continued = store.get_run(1, self.run["id"])
        self.assertEqual(continued["status"], "queued")
        self.assertEqual(store.take_steers(self.run["id"])[0]["text"], "Next task")
        self.assertTrue(store.steer_turn(1, self.thread["id"], "Next task", "late")["idempotent"])

    def test_cancel_wins_and_no_automatic_restart(self):
        store.steer_turn(1, self.thread["id"], "Next task", "next")
        store.request_cancel(1, self.run["id"])
        store.finish_run(self.run["id"], {"content": ""}, "cancelled")
        self.assertEqual(store.get_run(1, self.run["id"])["status"], "cancelled")
        self.assertFalse(store.queue_steer(self.run["id"], "other", "Do not accept"))
        with self.assertRaises(chats.ChatValidationError):
            store.steer_turn(1, self.thread["id"], "Do not accept", "other")

    def test_turn_retry_after_lost_ack_ignores_refreshed_revision(self):
        retry = store.start_turn(1, self.thread["id"], "Start", self.view()["revision"], "start")
        self.assertTrue(retry["idempotent"])
        self.assertEqual(retry["run"]["id"], self.run["id"])
        with self.assertRaises(chats.ChatValidationError):
            store.start_turn(1, self.thread["id"], "Different", self.view()["revision"], "start")

    def test_attachment_send_now_is_atomic_and_replayable(self):
        result = store.steer_turn(1, self.thread["id"], "", "image", attachments=[ATT])
        self.assertEqual(result["thread"]["messages"][-2]["attachments"][0]["data"], PNG)
        pending = store.take_steers(self.run["id"])
        message = store.steer_message(pending[0])
        self.assertEqual(message["content"][-1]["image_url"]["url"], PNG)
        store.save_turn_progress(self.run["id"], [message])
        self.assertEqual(store.take_steers(self.run["id"]), [])
        self.assertTrue(store.steer_turn(1, self.thread["id"], "", "image", attachments=[ATT])["idempotent"])
        self.assertFalse(store.get_run(1, self.run["id"])["cancel_requested"])
        with self.assertRaises(chats.ChatValidationError):
            store.steer_turn(1, self.thread["id"], "changed", "image", attachments=[ATT])

    def test_attachment_endpoint_starts_queued_run_without_cancelling(self):
        from app.web import chat
        async def run():
            start = AsyncMock()
            with patch.object(chat, "_chat_auth", return_value=({"id": 1}, None)), patch.object(chat, "_json_object", AsyncMock(return_value={"text": "", "command_id": "image", "attachments": [ATT]})), patch.object(runs, "start", start):
                response = await chat.chat_steer(runs.internal_request(1), self.thread["id"])
            self.assertEqual(response.status_code, 200)
            self.assertEqual(start.await_count, 1)
            self.assertFalse(store.get_run(1, self.run["id"])["cancel_requested"])
        asyncio.run(run())

    def test_project_history_framing_preserves_multimodal_content(self):
        messages = [
            {"id": "a", "role": "assistant", "content": "Read it", "tools": [{"name": "read_file", "status": "done", "path": "x", "preview": "hello"}]},
            {"id": "u", "role": "user", "content": "Look", "attachments": [ATT]},
        ]
        projected = chats.provider_history(messages, coding=True)
        self.assertIsInstance(projected[-1]["content"], list)
        self.assertEqual(projected[-1]["content"][0]["text"], "Look")
        self.assertTrue(any(m.get("role") == "tool" and "hello" in m.get("content", "") for m in projected))
        self.assertEqual(projected[-1]["content"][-1]["image_url"]["url"], PNG)

    def test_recovery_preserves_steer_offsets_and_does_not_import_history(self):
        store.checkpoint(self.run["id"], {"content": "Current", "segments": [{"type": "content", "text": "Current"}],
            "usage": {"prompt_tokens": 50, "completion_tokens": 10, "total_tokens": 60}})
        store.steer_turn(1, self.thread["id"], "Next", "next")
        store.save_turn_progress(self.run["id"], [{"role": "assistant", "content": "An unrelated earlier turn"}])
        store.recover_one(db.fetchone("SELECT * FROM chat_runs WHERE id=?", (self.run["id"],)))
        recovered = store.get_run(1, self.run["id"])
        self.assertEqual(recovered["message"]["content"], "Current")
        self.assertEqual(recovered["request"]["agent"]["usage_before"]["total_tokens"], 60)
        self.assertEqual([m["content"] for m in self.view()["messages"]], ["Start", "Current", "Next", ""])

    def test_stale_recovery_cannot_overwrite_send_now_or_cancel(self):
        stale = db.fetchone("SELECT * FROM chat_runs WHERE id=?", (self.run["id"],))
        store.steer_turn(1, self.thread["id"], "Latest", "latest")
        store._requeue_for_resume(stale, [{"role": "user", "content": "Start"}])
        self.assertEqual(store.take_steers(self.run["id"])[0]["text"], "Latest")
        stale = db.fetchone("SELECT * FROM chat_runs WHERE id=?", (self.run["id"],))
        store.request_cancel(1, self.run["id"])
        store._requeue_for_resume(stale, [{"role": "user", "content": "Start"}])
        self.assertTrue(store.get_run(1, self.run["id"])["cancel_requested"])

    def test_live_meter_persistence_and_reload_keep_identical_basis(self):
        snap = context.meter_from_messages([
            {"role": "system", "content": "system"},
            {"role": "tool", "content": "x" * 30000},
        ], model="offline", override=100000)
        snap.update(scope="prompt", basis="payload_chars_v1", run_id=self.run["id"])
        store.save_meter(1, self.run["thread_id"], snap)
        self.assertEqual(self.view()["compaction"]["meter"], snap)
        compacted = copy.deepcopy(snap)
        compacted.update(used=100, parts={**snap["parts"], "total": 100})
        store.save_meter(1, self.run["thread_id"], compacted)
        self.assertEqual(self.view()["compaction"]["meter"]["used"], 100)
        stale = {**snap, "run_id": "another-run", "used": 999999}
        store.save_meter(1, self.run["thread_id"], stale)
        self.assertEqual(self.view()["compaction"]["meter"]["used"], 100)

    def test_activity_checkpoint_moves_from_tools_to_model(self):
        for phase in ("tools", "model"):
            store.checkpoint(self.run["id"], {"content": ""}, [{"fuse": {"event": "activity", "phase": phase}}])
        self.assertEqual(store.get_run(1, self.run["id"])["request"]["agent"]["activity"]["phase"], "model")

    def test_new_wait_has_its_own_activity_identity(self):
        output = runs.Output({"message": {"id": "m", "role": "assistant"}})
        for ident, state in (("first", "done"), ("second", "running")):
            output.apply({"fuse": {"event": "tool", "id": ident, "name": "get_subagent_output", "child_id": "child", "status": state}})
        stored = output.stored()
        self.assertEqual([(t["id"], t["status"]) for t in stored["tools"]], [("first", "done"), ("second", "running")])
        self.assertEqual([s["id"] for s in stored["segments"]], ["first", "second"])

    def make_jobs(self):
        self.conn.execute("""CREATE TABLE IF NOT EXISTS command_jobs (
            id TEXT PRIMARY KEY, user_id INTEGER, thread_id INTEGER, status TEXT,
            host TEXT, command TEXT, cwd TEXT, out TEXT, out_bytes INTEGER,
            exit_code INTEGER, duration_ms INTEGER, timeout_ms INTEGER, error TEXT, background INTEGER)""")
        for i in range(30):
            self.conn.execute("INSERT INTO command_jobs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (f"job{i}", 1, self.run["thread_id"], "completed", "host", "test", "/repo", "result" * 200,
                 10000, 7 if i == 0 else 0, 100, 1000, "", 1))
        self.conn.commit()

    def test_old_commands_reconcile_all_references_and_full_details(self):
        self.make_jobs()
        messages = [{"tools": [{"id": f"trace{i}", "name": "run_terminal_command", "status": "running", "child_id": f"job{i}"} for i in range(30)]},
                    {"tools": [{"id": "poll", "name": "get_command_output", "status": "running", "child_id": "job0"}]}]
        store.settle_command_traces(self.conn, 1, self.run["thread_id"], messages)
        # Every launch follows its job, across all 30 references.
        self.assertTrue(all(t["execution_status"] == "completed" for t in messages[0]["tools"]))
        trace = messages[0]["tools"][0]
        self.assertEqual(trace["status"], "done")
        self.assertEqual(trace["exit_code"], 7)
        self.assertEqual(len(trace["output"]), 1200)
        self.assertEqual(trace["job_id"], "job0")
        # A wait keeps its own invocation result; the job never rewrites it.
        poll = messages[1]["tools"][0]
        self.assertEqual(poll["status"], "running")
        self.assertNotIn("execution_status", poll)
        self.assertNotIn("output", poll)
        self.assertFalse(store.command_rows(self.conn, 2, self.run["thread_id"], ["job0"]))
        self.assertFalse(store.command_rows(self.conn, 1, -1, ["job0"]))

    def test_live_job_reconciliation_preserves_output_and_active_wait(self):
        self.make_jobs()
        self.conn.execute("UPDATE command_jobs SET status='running',out='' WHERE id='job0'")
        messages = [{"tools": [{"id": "wait", "name": "get_command_output", "child_id": "job0", "status": "running", "output": "live output", "output_chars": 11}]}]
        store.settle_command_traces(self.conn, 1, self.run["thread_id"], messages)
        trace = messages[0]["tools"][0]
        self.assertEqual(trace["status"], "running")
        self.assertEqual(trace["output"], "live output")
        trace["status"] = "done"
        store.settle_command_traces(self.conn, 1, self.run["thread_id"], messages)
        self.assertEqual(trace["status"], "done")

    def test_historical_endpoint_data_not_limited_to_twelve(self):
        self.make_jobs()
        tools = [{"id": f"t{i}", "name": "run_terminal_command", "status": "running", "child_id": f"job{i}"} for i in range(30)]
        store.checkpoint(self.run["id"], {"content": "", "tools": tools})
        rows = store.historical_command_jobs(1, self.run["thread_id"])
        self.assertEqual(len(rows), 30)

    def test_command_output_unicode_offsets_survive_persistence(self):
        trace = store.command_trace({"id": "unicode", "status": "completed", "out": "🙂" * 30000,
                                    "out_chars": 40000, "exit_code": 0, "termination_confirmed": 1})
        trace["id"] = "tool-id"
        clean = chats._parse_tool_traces([trace], strict=True)[0]
        self.assertEqual(clean["output_start"], 16000)
        self.assertEqual(clean["output_retained_chars"], 24000)
        self.assertTrue(clean["output_chars_exact"])
        self.assertEqual(clean["command_id"], "unicode")
        self.assertTrue(clean["termination_confirmed"])

    def test_command_receipt_is_atomic_with_retained_transcript(self):
        self.make_jobs()
        self.conn.execute("ALTER TABLE command_jobs ADD COLUMN delivered INTEGER DEFAULT 0")
        self.conn.commit()
        extra = {"command_delivery_ids": ["job0"]}
        store.save_turn_progress(self.run["id"], [{"role": "user", "content": "unrelated"}], extra=extra)
        self.assertEqual(db.fetchone("SELECT delivered FROM command_jobs WHERE id='job0'")[0], 0)
        store.save_turn_progress(self.run["id"], [{"role": "user", "content": '<command_result id="job0" state="completed">done</command_result>'}], extra=extra)
        self.assertEqual(db.fetchone("SELECT delivered FROM command_jobs WHERE id='job0'")[0], 1)

    def test_poll_return_releases_foreground_activity(self):
        async def run():
            request = runs.internal_request(1)
            call = {"id": "c", "function": {"name": "get_command_output", "arguments": '{"id":"job"}'}}
            with patch.object(runtime, "execute_tool", AsyncMock(return_value=("still running", {"name": "get_command_output", "status": "running", "child_id": "job"}))):
                items = [item async for item in runtime._run_tools([call], user_id=1, allowed_urls=set(), request=request, completion_i=0, budgets={}, coding=True)]
            trace = items[-1][0].trace
            self.assertEqual(trace["status"], "done")
            self.assertEqual(trace["execution_status"], "running")
            self.assertTrue(trace["background"])
        asyncio.run(run())

    def test_worker_reschedules_accepted_work_after_late_failure(self):
        from starlette.responses import StreamingResponse
        async def frames():
            yield runtime._sse({"choices": [{"delta": {"content": "Old answer"}}]})
            store.steer_turn(1, self.thread["id"], "New task", "late")
            raise RuntimeError("controlled failure")
        async def run():
            start = AsyncMock()
            with patch.object(runtime, "stream_dashboard_chat", AsyncMock(return_value=StreamingResponse(frames()))), patch.object(runs, "start", start), patch.object(runs, "schedule_wake"):
                await runs._run(runs.internal_request(1), {"id": 1}, self.run)
            self.assertEqual(start.await_count, 1)
            self.assertEqual(start.call_args.args[2]["status"], "queued")
            self.assertEqual(store.take_steers(self.run["id"])[0]["text"], "New task")
        asyncio.run(run())

    def test_send_now_gets_tools_and_meter_uses_latest_request_measurement(self):
        async def run():
            payloads = []
            async def invoke(request, user, payload, **_kwargs):
                payloads.append(copy.deepcopy(payload))
                n = len(payloads)
                if n == 1:
                    store.steer_turn(1, self.thread["id"], "New task", "during")
                if n == 2:
                    self.assertNotEqual(payload.get("tool_choice"), "none")
                    self.assertIn("New task", str(payload["messages"][-1]["content"]))
                body = {"choices": [{"message": {"content": "old" if n == 1 else "new"}, "finish_reason": "stop"}]}
                result = SimpleNamespace(status=200, body=body, usage={"prompt_tokens": 900000 if n == 1 else 188, "completion_tokens": 10}, stream=None, credential_id=1)
                return user, SimpleNamespace(type_id="llama", source_id=1), "offline", "offline", result
            with patch.object(runtime.api, "invoke_chat", invoke), patch.object(runtime.api, "_safe_record_usage"), patch.object(runtime, "MAX_COMPLETIONS", 2):
                response = await runtime.stream_dashboard_chat(runs.internal_request(1), {"id": 1}, self.run["request"], frozen_context=self.run["request"])
                data = b"".join([b async for b in response.body_iterator])
            events = [json.loads(line[5:]) for line in data.decode().splitlines() if line.startswith("data:") and "[DONE]" not in line]
            meters = [e["fuse"] for e in events if e.get("fuse", {}).get("event") == "context"]
            self.assertEqual(len(payloads), 2)
            self.assertTrue(all("reported_request_tokens" not in m for m in meters))
            self.assertTrue(all(m["used"] == m["parts"]["total"] for m in meters))
            rounds = [m["used"] for m in meters if m["phase"] == "round"]
            self.assertEqual(len(rounds), 2)
            self.assertGreater(rounds[0], 900000)
            self.assertLess(rounds[0], 900010)
            self.assertGreater(rounds[1], 188)
            self.assertLess(rounds[1], 198)
            self.assertLess(meters[-1]["used"], 10000, "a later smaller request must not inherit a historical floor")
            self.assertFalse(store.take_steers(self.run["id"]))
        asyncio.run(run())

    def test_tool_round_meter_grows_on_one_basis_and_usage_stays_cumulative(self):
        async def run():
            calls = []
            async def invoke(request, user, payload, **_kwargs):
                calls.append(copy.deepcopy(payload))
                first = len(calls) == 1
                message = {"content": "Looking" if first else "Answer"}
                if first:
                    message["tool_calls"] = [{"id": "search", "function": {"name": "web_search", "arguments": '{"query":"offline"}'}}]
                result = SimpleNamespace(status=200, body={"choices": [{"message": message, "finish_reason": "tool_calls" if first else "stop"}]},
                    usage={"prompt_tokens": 900000 if first else 188, "completion_tokens": 10, "total_tokens": 900010 if first else 198}, stream=None, credential_id=1)
                return user, SimpleNamespace(type_id="openai", source_id=1), "offline", "offline", result
            raw = copy.deepcopy(self.run["request"])
            raw["agent"] = {"usage_before": {"prompt_tokens": 50, "completion_tokens": 10, "total_tokens": 60}}
            with patch.object(runtime.api, "invoke_chat", invoke), patch.object(runtime.api, "_safe_record_usage"), patch.object(runtime, "execute_tool", AsyncMock(return_value=("evidence" * 1000, {"name": "web_search", "status": "done"}))):
                response = await runtime.stream_dashboard_chat(runs.internal_request(1), {"id": 1}, raw, frozen_context=raw)
                data = b"".join([b async for b in response.body_iterator])
            events = [json.loads(line[5:]) for line in data.decode().splitlines() if line.startswith("data:") and "[DONE]" not in line]
            meters = [e["fuse"] for e in events if e.get("fuse", {}).get("event") == "context"]
            requests = [m for m in meters if m["phase"] == "request"]
            self.assertGreater(requests[1]["used"], requests[0]["used"])
            self.assertTrue(all(m["used"] == m["parts"]["total"] for m in meters))
            self.assertTrue(all("reported_request_tokens" not in m for m in meters))
            usage = [e["usage"] for e in events if "usage" in e][-1]
            self.assertEqual(usage["total_tokens"], 60 + 900010 + 198)
        asyncio.run(run())
