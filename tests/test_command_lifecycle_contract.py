"""Stable command references, pre-display output metadata and lifecycle transitions."""
import asyncio
import json
import sqlite3
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app import agent_hub as hub, command_jobs as jobs, db, workspace as ws, chats, chat_runtime, chat_store


class LifecycleContract(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.addCleanup(self.conn.close)
        for p in (patch.object(db, "connect", return_value=self.conn),
                  patch.object(jobs, "_live", {}), patch.object(jobs, "_schema_ready", False),
                  patch.object(jobs, "_deliver")):
            p.start()
            self.addCleanup(p.stop)

    def launch(self, runner, *, background=False, timeout=1000):
        return jobs.launch(user_id=1, host_id=2, host="host", thread_id=3, run_id=None,
                           command="mock", cwd="/repo", background=background,
                            timeout_ms=timeout, runner=runner)

    async def test_full_command_survives_start_result_and_strict_checkpoint(self):
        command = "cd /repo && python -c '" + "x" * 7400 + "'"
        job = chat_runtime.ToolJob(0, "call", "run_terminal_command", json.dumps({"command": command}), "tool-call")
        self.assertEqual(chat_runtime._running_event(job)["command"], command)
        async def runner(sink):
            sink("result")
            return SimpleNamespace(exit_status=2)
        rec = jobs.launch(user_id=1, host_id=2, host="host", thread_id=3, run_id=None,
                          command=command, cwd="/repo", background=False, timeout_ms=1000, runner=runner)
        await rec["task"]
        trace = jobs.render(rec)[1]
        trace.update(project_root="/repo", platform="posix", call_duration_ms=12)
        saved = chats._parse_tool_traces([trace], strict=True)[0]
        self.assertEqual(saved["command"], command)
        self.assertEqual(saved["project_root"], "/repo")
        self.assertEqual(saved["execution_duration_ms"], trace["duration_ms"])
        row = dict(self.conn.execute("SELECT * FROM command_jobs WHERE id=?", (rec["id"],)).fetchone())
        history = chat_store.command_trace(row)
        self.assertEqual(history["command"], command)
        self.assertEqual(history["status"], "done")
        self.assertEqual(history["exit_code"], 2)
        self.assertFalse(history["background"])

    async def test_shell_announces_execution_identity_before_foreground_finishes(self):
        gate = asyncio.Event()
        async def runner(sink):
            await gate.wait()
            return SimpleNamespace(exit_status=0)
        events = []
        with patch.object(ws, "_is_agent", return_value=True), patch.object(ws, "_make_agent_runner", return_value=runner):
            task = asyncio.create_task(ws._shell(1, 2, "host", {"command": "mock"}, "/repo", on_started=events.append))
            await asyncio.sleep(0.02)
            self.assertFalse(task.done())
            self.assertEqual(events[0]["cwd"], "/repo")
            self.assertTrue(events[0]["command_id"])
            self.assertNotIn("id", events[0], "execution ID must not replace the tool-call ID")
            self.assertFalse(events[0]["background"])
            gate.set()
            await task

    async def test_history_preserves_wait_invocations_independently_of_execution(self):
        async def runner(sink):
            return SimpleNamespace(exit_status=0)
        rec = self.launch(runner)
        await rec["task"]
        row = dict(self.conn.execute("SELECT * FROM command_jobs WHERE id=?", (rec["id"],)).fetchone())
        original = {"id": "receipt", "name": "get_command_output", "status": "done", "child_id": rec["id"], "output": "historical receipt"}
        pending = {"id": "pending", "name": "get_command_output", "status": "done", "child_id": rec["id"], "state": "running"}
        with patch.object(chat_store, "command_rows", return_value={rec["id"]: row}):
            view = chat_store.settle_command_traces(self.conn, 1, 3, [{"tools": [original.copy(), pending]}])
        self.assertEqual(view[0]["tools"][0], original)
        self.assertEqual(view[0]["tools"][1]["state"], "running")
        for state in ("completed", "failed", "cancelled", "lost"):
            for status in ("running", "done", "error", "interrupted"):
                wait = {**pending, "status": status, "output": "observed"}
                original = dict(wait)
                with patch.object(chat_store, "command_rows", return_value={rec["id"]: {**row, "status": state}}):
                    chat_store.settle_command_traces(self.conn, 1, 3, [{"tools": [wait]}])
                self.assertEqual(wait, original)

    async def test_terminal_identity_output_tail_survives_database_reload(self):
        source = " \n" + "€" * 10000 + "  END\n "
        async def runner(sink):
            sink(source)
            return SimpleNamespace(exit_status=7)
        rec = self.launch(runner)
        await rec["task"]
        text, trace = jobs.render(rec)
        self.assertIn(f"id: {rec['id']}", text)
        for key in ("id", "child_id", "command_id"):
            self.assertEqual(trace[key], rec["id"])
        self.assertEqual(trace["state"], "completed")
        self.assertEqual(trace["status"], "done")
        self.assertEqual(trace["exit_code"], 7)
        self.assertEqual(trace["output"], source[-jobs.OUT_TAIL_CHARS:])
        self.assertEqual(trace["output_chars"], len(source))
        self.assertEqual(trace["output_retained_chars"], jobs.OUT_TAIL_CHARS)
        self.assertEqual(trace["output_start"], len(source) - jobs.OUT_TAIL_CHARS)
        self.assertTrue(trace["truncated"])
        self.assertTrue(trace["output_chars_exact"])
        self.assertEqual(trace["output_mode"], "tail")
        jobs._live.clear()
        view = jobs.reconcile_jobs(1, 3, [rec["id"]])["jobs"][rec["id"]]
        for key in ("output", "output_chars", "output_start", "truncated", "command_id", "termination_confirmed"):
            self.assertEqual(view[key], trace[key])

    async def test_partial_connector_output_is_never_called_a_stream_tail(self):
        runner = ws._make_agent_runner(1, 2, "mock", "/repo", 1000)
        with patch.object(ws, "_rate_ok"), patch.object(ws, "_agent_call", AsyncMock(return_value={
                "ok": True, "exit": 0, "out": "prefix" * 2000, "truncated": True})):
            rec = self.launch(runner)
            await rec["task"]
        text, view = jobs.render(rec)
        self.assertEqual(view["output_mode"], "partial")
        self.assertFalse(view["output_chars_exact"])
        self.assertIsNone(view["output_start"])
        self.assertTrue(view["truncated"])
        self.assertNotIn("showing retained tail", text)
        self.assertIn("[Output truncated; total unknown]", text)

    async def test_short_output_is_not_labeled_truncated(self):
        for metadata in ({"truncated": False, "output_complete": True}, {}, {"truncated": True}):
            with patch.object(ws, "_rate_ok"), patch.object(ws, "_agent_call", AsyncMock(return_value={
                    "ok": True, "exit": 0, "out": "ok\n", **metadata})):
                rec = self.launch(ws._make_agent_runner(1, 2, "mock", "/repo", 1000))
                await rec["task"]
            text, view = jobs.render(rec)
            self.assertNotIn("[Output truncated", text)
            self.assertIn("ok\n", text)
            self.assertEqual(view["output"], "ok\n")

    async def test_ssh_utf8_across_chunks_has_exact_character_count(self):
        reader = SimpleNamespace(read=AsyncMock(side_effect=[b"\xe2", b"\x82\xac\n", b""]))
        proc = SimpleNamespace(stdout=reader, stderr=None, wait=AsyncMock(), exit_status=0)
        conn = SimpleNamespace(create_process=AsyncMock(return_value=proc))
        with patch("app.ssh_pool.connection", AsyncMock(return_value=conn)), patch.object(ws, "_rate_ok"):
            rec = self.launch(ws._make_ssh_runner(1, 2, "mock", "/repo", 1000))
            await rec["task"]
        view = jobs.render(rec)[1]
        self.assertEqual(view["output"], "€\n")
        self.assertEqual(view["output_chars"], 2)
        self.assertTrue(view["output_chars_exact"])

    async def test_connector_completeness_requires_positive_protocol_evidence(self):
        for metadata, exact in (({}, False), ({"truncated": False}, True),
                                ({"truncated": False, "output_complete": False}, False)):
            with patch.object(ws, "_rate_ok"), patch.object(ws, "_agent_call", AsyncMock(return_value={
                    "ok": True, "exit": 0, "out": "receipt", **metadata})):
                rec = self.launch(ws._make_agent_runner(1, 2, "mock", "/repo", 1000))
                await rec["task"]
            view = jobs.render(rec)[1]
            self.assertEqual(view["output_chars_exact"], exact)
            self.assertFalse(view["truncated"])
            self.assertEqual(view["output_mode"], "full" if exact else "partial")

    async def test_timeout_error_string_is_not_a_completed_exit(self):
        with patch.object(ws, "_rate_ok"), patch.object(ws, "_agent_call", AsyncMock(return_value={
                "ok": False, "exit": 1, "out": "", "err": "", "error": "timed out"})):
            rec = self.launch(ws._make_agent_runner(1, 2, "mock", "/repo", 1500), timeout=1500)
            await rec["task"]
        _, view = jobs.render(rec)
        self.assertEqual(view["state"], "timed_out")
        self.assertEqual(view["status"], "error")
        self.assertIsNone(view["exit_code"])
        self.assertIn("1500ms", view["error"])

    async def test_connector_stderr_is_labeled(self):
        with patch.object(ws, "_rate_ok"), patch.object(ws, "_agent_call", AsyncMock(return_value={
                "ok": True, "exit": 0, "out": "hello\n", "err": "warn\n", "truncated": False})):
            rec = self.launch(ws._make_agent_runner(1, 2, "mock", "/repo", 1000))
            await rec["task"]
        _, view = jobs.render(rec)
        self.assertEqual(view["output"], "hello\n[stderr]\nwarn\n")
        self.assertTrue(view["output_chars_exact"])

    async def test_kill_of_finished_job_is_a_terminal_delivery_receipt(self):
        async def runner(sink):
            return SimpleNamespace(exit_status=0)
        rec = self.launch(runner)
        await rec["task"]
        self.assertEqual(db.fetchone("SELECT delivered FROM command_jobs")["delivered"], 0)
        _, result = jobs.kill(rec["id"], 1)
        self.assertEqual(result["command_id"], rec["id"])
        self.assertEqual(result["state"], "completed")
        self.assertEqual(db.fetchone("SELECT delivered FROM command_jobs")["delivered"], 1)

    async def test_all_terminal_states_have_stable_ids_and_output(self):
        for state in jobs.TERMINAL:
            rec = {"id": "abc", "status": state, "out": "\n x \n", "out_chars": 5,
                   "output_incomplete": False, "duration_ms": 1}
            _, view = jobs.render(rec)
            self.assertEqual(view["command_id"], "abc")
            self.assertEqual(view["child_id"], "abc")
            self.assertEqual(view["state"], state)
            self.assertEqual(view["output"], "\n x \n")

    async def test_reconciliation_batches_all_historical_references_and_scopes(self):
        jobs.ensure_schema()
        ids = [f"j{i}" for i in range(425)]
        self.conn.executemany(
            "INSERT INTO command_jobs(id,user_id,host_id,host,thread_id,command,timeout_ms,status,"
            "created_at,finished_at,background,out,out_chars) VALUES(?,1,2,'host',3,'mock',1000,"
            "'completed','2001-01-01','2001-01-01',0,'kept',4)", [(i,) for i in ids])
        self.conn.commit()
        result = jobs.reconcile_jobs(1, 3, ids + ["absent", ids[0]])
        self.assertEqual(list(result["jobs"]), ids)
        self.assertEqual(result["missing"], ["absent"])
        self.assertFalse(jobs.reconcile_jobs(2, 3, ids)["jobs"])
        self.assertFalse(jobs.reconcile_jobs(1, 4, ids)["jobs"])
        self.assertEqual(self.conn.execute("SELECT SUM(delivered) FROM command_jobs").fetchone()[0], 0)
        self.conn.execute("UPDATE command_jobs SET background=1,finished_at=?", (db.now(),))
        self.conn.commit()
        self.assertEqual(len(jobs.recent_terminal_jobs(1, 3)), 425)

    async def test_kill_is_cancelling_until_cleanup_and_repeated_kill_does_not_interrupt_it(self):
        started, cleaning, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
        async def runner(sink):
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cleaning.set()
                await release.wait()
                raise
        rec = self.launch(runner)
        await started.wait()
        _, trace = jobs.kill(rec["id"], 1)
        await cleaning.wait()
        self.assertEqual(trace["status"], "running")
        self.assertEqual(trace["state"], "cancelling")
        self.assertEqual(db.fetchone("SELECT status FROM command_jobs")["status"], "cancelling")
        self.assertEqual(jobs.job_status(1, rec["id"]), "cancelling")
        jobs.kill(rec["id"], 1)
        jobs.cancel_run("unrelated")
        self.assertEqual(rec["task"].cancelling(), 1)
        release.set()
        await rec["task"]
        _, final = await jobs.join(rec["id"], 1, 0)
        self.assertEqual(final["state"], "cancelled")
        self.assertFalse(final["termination_confirmed"])
        self.assertFalse(final["output_chars_exact"])
        self.assertEqual(final["output_mode"], "partial")

    async def test_cancelled_waiter_does_not_cancel_attached_job(self):
        gate = asyncio.Event()
        async def runner(sink):
            await gate.wait()
            return SimpleNamespace(exit_status=0)
        rec = self.launch(runner)
        waiter = asyncio.create_task(jobs.join(rec["id"], 1, 1))
        await asyncio.sleep(0)
        waiter.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await waiter
        self.assertEqual(rec["status"], "running")
        self.assertFalse(rec["task"].done())
        gate.set()
        await rec["task"]

    async def test_foreground_background_and_auto_handoff_share_id_contract(self):
        for background, handoff in ((False, False), (True, False), (False, True)):
            gate = asyncio.Event()
            async def runner(sink):
                if background or handoff:
                    await gate.wait()
                sink("done")
                return SimpleNamespace(exit_status=0)
            with patch.object(ws, "_is_agent", return_value=False), patch.object(ws, "_make_ssh_runner", return_value=runner), patch.object(jobs, "FOREGROUND_WAIT_S", 0.01):
                _, trace = await ws._shell(1, 2, "host", {"command": "mock", "background": background}, "/repo", thread_id=3)
            ident = trace["command_id"]
            rec = jobs._live[ident]
            self.assertEqual(trace["state"], "running" if background or handoff else "completed")
            self.assertEqual(trace["background"], background or handoff)
            gate.set()
            await rec["task"]
            _, final = await jobs.join(ident, 1, 0)
            self.assertEqual(final["command_id"], ident)
            self.assertEqual(final["output"], "done")

    async def test_foreground_cancellation_requests_job_cleanup(self):
        started = asyncio.Event()
        async def runner(sink):
            started.set()
            await asyncio.Event().wait()
        with patch.object(ws, "_is_agent", return_value=False), patch.object(ws, "_make_ssh_runner", return_value=runner):
            call = asyncio.create_task(ws._shell(1, 2, "host", {"command": "mock"}, "/repo"))
            await started.wait()
            call.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await call
        rec = next(iter(jobs._live.values()))
        await rec["future"]
        self.assertEqual(rec["status"], "cancelled")

    async def test_recover_cancelling_and_legacy_output_is_unknown(self):
        jobs.ensure_schema()
        self.conn.execute("INSERT INTO command_jobs(id,user_id,host_id,thread_id,command,timeout_ms,status,created_at,out) VALUES('old',1,2,3,'mock',1000,'cancelling','2001-01-01','legacy')")
        self.conn.commit()
        jobs.recover()
        view = jobs.reconcile_jobs(1, 3, ["old"])["jobs"]["old"]
        self.assertEqual(view["state"], "interrupted")
        self.assertFalse(view["termination_confirmed"])
        self.assertFalse(view["output_chars_exact"])
        self.assertEqual(view["output_chars"], 6)

    async def test_no_exit_receipt_is_interrupted_not_completed(self):
        async def runner(sink):
            sink("last known output")
            return SimpleNamespace(exit_status=None)
        rec = self.launch(runner)
        await rec["task"]
        _, view = jobs.render(rec)
        self.assertEqual(view["state"], "interrupted")
        self.assertFalse(view["termination_confirmed"])
        self.assertEqual(view["output"], "last known output")

    async def test_connector_busy_is_not_a_completed_shell_exit(self):
        with patch.object(ws, "_rate_ok"), patch.object(ws, "_agent_call", AsyncMock(return_value={
                "ok": False, "exit": 1, "error": "agent is busy"})):
            rec = self.launch(ws._make_agent_runner(1, 2, "mock", "/repo", 1000))
            await rec["task"]
        self.assertEqual(rec["status"], "interrupted")

    async def test_old_schema_migration_preserves_unknown_counts(self):
        self.conn.execute("CREATE TABLE command_jobs(id TEXT PRIMARY KEY, out TEXT, out_bytes INTEGER)")
        self.conn.execute("INSERT INTO command_jobs VALUES('old','€€',2)")
        self.conn.commit()
        jobs.ensure_schema()
        row = db.fetchone("SELECT * FROM command_jobs")
        self.assertIsNone(row["out_chars"])
        self.assertEqual(row["out"], "€€")
        fields = jobs.output_fields(dict(row))
        self.assertEqual(fields["output_chars"], 2)
        self.assertFalse(fields["output_chars_exact"])
        self.assertFalse(fields["truncated"])
        self.assertEqual(fields["output_mode"], "partial")


class ReconnectCancellation(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        for name in ("_conns", "_held", "_cancel_replay"):
            p = patch.object(hub, name, {})
            p.start()
            self.addCleanup(p.stop)

    async def test_cancel_offline_replayed_after_handshake_until_terminal_receipt(self):
        await hub.cancel(1, 2, "remote-job")
        incoming, sent = asyncio.Queue(), asyncio.Queue()
        await incoming.put(json.dumps({"t": "hello"}))
        sock = SimpleNamespace(receive_text=incoming.get, send_json=sent.put, close=AsyncMock())
        with patch.object(hub, "_hello", AsyncMock(return_value=(1, 2, "host", ""))), patch.object(hub.hosts, "touch_agent"), patch.object(hub.hosts, "settings_for", return_value={"agent_coding": True}), patch.object(hub.hosts, "mark_agent_offline"):
            task = asyncio.create_task(hub.handle_socket(sock))
            self.assertEqual((await sent.get())["t"], "hello_ok")
            self.assertEqual(await sent.get(), {"v": 1, "t": "cancel", "id": "remote-job"})
            self.assertIn("remote-job", hub._cancel_replay[1, 2])
            await incoming.put(json.dumps({"t": "res", "id": "remote-job", "ok": False, "exit": 1}))
            await asyncio.sleep(0)
            self.assertNotIn((1, 2), hub._cancel_replay)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task

    async def test_held_call_cancel_is_cleaned_but_cancellation_retained(self):
        sock = SimpleNamespace(send_json=AsyncMock())
        rec = {"ws": sock, "pending": {}}
        hub._conns[1, 2] = rec
        task = asyncio.create_task(hub.call(1, 2, "exec"))
        await asyncio.sleep(0)
        ident = next(iter(rec["pending"]))
        hub._held[1, 2] = {"pending": dict(rec["pending"])}
        hub._conns.clear()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertFalse(hub._held[1, 2]["pending"])
        self.assertIn(ident, hub._cancel_replay[1, 2])

    async def test_hold_expiry_remembers_cancel_without_claiming_remote_exit(self):
        pending = {"remote": asyncio.get_running_loop().create_future()}
        fut = pending["remote"]
        with patch.object(hub, "HOLD_SECONDS", 0.001), patch.object(hub.hosts, "mark_agent_offline"):
            await hub._hold_pending((1, 2), pending)
            with self.assertRaisesRegex(hub.AgentError, "remote outcome is unknown"):
                await asyncio.wait_for(fut, 1)
        self.assertIn("remote", hub._cancel_replay[1, 2])
        self.assertFalse(hub._held)

    async def test_held_call_can_finish_after_reconnect_without_reexecution(self):
        first = SimpleNamespace(send_json=AsyncMock())
        rec = {"ws": first, "pending": {}}
        hub._conns[1, 2] = rec
        task = asyncio.create_task(hub.call(1, 2, "exec"))
        await asyncio.sleep(0)
        ident = next(iter(rec["pending"]))
        hub._conns.clear()
        await hub._hold_pending((1, 2), rec["pending"])
        transferred = hub._take_held((1, 2))
        second = SimpleNamespace(send_json=AsyncMock())
        hub._conns[1, 2] = {"ws": second, "pending": transferred}
        transferred[ident].set_result({"ok": True, "exit": 0, "out": "completed once"})
        self.assertEqual((await task)["out"], "completed once")
        second.send_json.assert_not_awaited()
        self.assertEqual(first.send_json.await_count, 1)
        self.assertFalse(transferred)
