"""Owned execution regressions: mocked transports and an isolated in-memory DB."""
import asyncio
import json
import sqlite3
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from app import agent_catalog, agent_hub as hub, command_jobs as jobs, db, workspace as ws
from tests.test_workspace_reliability import SFTP


class ExecutionAudit(unittest.IsolatedAsyncioTestCase):
    def test_schema_limits_match_executor_contract(self):
        props = lambda name: agent_catalog.SCHEMAS[name]["function"]["parameters"]["properties"]
        self.assertEqual(props("read_file")["limit"]["maximum"], 2000)
        self.assertEqual(props("grep")["max_matches"]["maximum"], ws._GREP_MAX_MATCHES)
        self.assertEqual(props("get_command_output")["timeout_ms"]["maximum"], 600000)
        body = ws._read_render(["a" * 100] * 400, 1, total=None, more=True, max_chars=ws._READ_MAX_CHARS)
        self.assertLessEqual(len(body.encode()), ws._READ_MAX_CHARS + 200)
        self.assertIn("Use offset=", body)

    async def test_invalid_mutation_json_never_resolves_host(self):
        with patch.object(ws, "resolve_host_row") as resolve:
            for raw in ('{"path":"a","content":"cut', '[]', 'null'):
                _, rec = await ws.execute("write_file", raw, user_id=1)
                self.assertEqual(rec["kind"], "invalid_arguments")
            resolve.assert_not_called()

    async def test_nul_path_and_multibyte_replace_rejected_before_transport(self):
        with patch.object(ws, "_agent_call", new_callable=AsyncMock) as call:
            for args in (
                {"path": "a\0b", "old_string": "a", "new_string": "b"},
                {"path": "a", "old_string": "a", "new_string": "€" * 70000},
            ):
                with self.assertRaises(ws.WorkspaceError):
                    await ws._replace(1, 2, "host", args, "/repo")
            call.assert_not_awaited()

    async def test_connector_clipped_read_is_not_paged_as_complete(self):
        # A frame that stays clipped even at one line is refused, never shown as complete.
        for result in ({"ok": True, "out": "abc", "truncated": True},
                       {"ok": True, "out": "abc\n[fuse-agent: output truncated]\n"}):
            with patch.object(ws, "_is_agent", return_value=True), patch.object(ws, "_agent_call", AsyncMock(return_value=result)):
                with self.assertRaisesRegex(ws.WorkspaceError, "64KB read frame"):
                    await ws._read(1, 2, "host", {"path": "a"}, "/repo")

    async def test_connector_oversized_page_shrinks_instead_of_failing(self):
        calls = []

        async def call(_uid, _hid, op, args, seconds=30):
            calls.append((op, dict(args)))
            if op == "read" and args["limit"] > 500:
                return {"ok": False, "exit": 1, "out": "", "error": "read failed",
                        "err": "read output exceeds 65536 bytes; use a smaller limit or grep"}
            if op == "read" and args["limit"] == 1:
                return {"ok": True, "out": "next\n"}
            return {"ok": True, "out": "".join(f"line{i}\n" for i in range(args["limit"]))}

        with patch.object(ws, "_is_agent", return_value=True), patch.object(ws, "_agent_call", call):
            text, _ = await ws._read(1, 2, "host", {"path": "a"}, "/repo")
        self.assertEqual([c[1]["limit"] for c in calls if c[0] == "read"], [2000, 1000, 500, 1])
        self.assertIn("500: line499", text)
        self.assertIn("Use offset=501 to continue", text)

    async def test_windows_native_glob_matches_are_not_filtered_again(self):
        result = {"ok": True, "out": "C:\\repo\\src\\a.py:1:hit\n"}
        with patch.object(ws, "_is_agent", return_value=True), patch.object(ws, "_agent_call", AsyncMock(return_value=result)):
            text, _ = await ws._grep(1, 2, "host", {"pattern": "hit", "include": "src/**/*.py"}, "C:\\repo", windows=True)
        self.assertIn("a.py:1:hit", text)

    async def test_ssh_drains_buffered_output_after_exit(self):
        class Reader:
            remaining = True
            async def read(self, n):
                await asyncio.sleep(0.01)
                if self.remaining:
                    self.remaining = False
                    return "last output\n"
                return ""
        proc = SimpleNamespace(stdout=Reader(), stderr=None, wait=AsyncMock(), exit_status=0)
        conn = SimpleNamespace(create_process=AsyncMock(return_value=proc))
        with patch("app.ssh_pool.connection", AsyncMock(return_value=conn)), patch.object(ws, "_rate_ok"):
            output = []
            await ws._make_ssh_runner(1, 2, "true", "/repo", 1000)(output.append)
        self.assertEqual(output, ["last output\n"])

    async def test_ssh_timeout_closes_the_exec_channel(self):
        # Unread output would hold the channel (and its pooled connection) open.
        async def hang(*_):
            await asyncio.Event().wait()

        def process():
            reader = SimpleNamespace(read=hang)
            return SimpleNamespace(stdout=reader, stderr=reader, wait=hang, exit_status=None, kill=Mock(), close=Mock())

        runner_proc, remote_proc = process(), process()
        with patch("app.ssh_pool.connection", AsyncMock(return_value=SimpleNamespace(create_process=AsyncMock(return_value=runner_proc)))), \
                patch.object(ws, "_rate_ok"), patch.object(ws, "_remote_group_kill", AsyncMock(return_value=True)):
            with self.assertRaises(jobs.CommandTimedOut):
                await ws._make_ssh_runner(1, 2, "yes", "/repo", 50)([].append)
        with patch("app.ssh_pool.connection", AsyncMock(return_value=SimpleNamespace(create_process=AsyncMock(return_value=remote_proc)))), \
                patch.object(ws, "_is_agent", return_value=False):
            with self.assertRaisesRegex(ws.WorkspaceError, "timed out"):
                await ws._run_remote(1, 2, "yes", "", 0.05, rate=False)
        for proc in (runner_proc, remote_proc):
            proc.kill.assert_called_once()
            proc.close.assert_called_once()

    async def test_agent_timeout_retains_output(self):
        with patch.object(ws, "_agent_call", AsyncMock(return_value={"ok": False, "exit": 1, "timeout": True, "out": "progress"})), patch.object(ws, "_rate_ok"):
            output = []
            with self.assertRaises(jobs.CommandTimedOut):
                await ws._make_agent_runner(1, 2, "true", "/repo", 1000)(output.append)
        self.assertEqual(output, ["progress"])

    async def test_external_sftp_change_preserves_original(self):
        sftp = SFTP(b"old")
        sftp.lstat = AsyncMock(side_effect=[
            SimpleNamespace(permissions=0o100644, size=3, mtime=1),
            SimpleNamespace(permissions=0o100644, size=3, mtime=1),
            SimpleNamespace(permissions=0o100644, size=3, mtime=2),
        ])
        with patch.object(ws, "_is_agent", return_value=False), patch.object(ws, "_sftp", AsyncMock(return_value=sftp)):
            with self.assertRaisesRegex(ws.WorkspaceError, "changed on disk while this edit was being written, so nothing was written"):
                await ws._replace(1, 2, "host", {"path": "a", "old_string": "old", "new_string": "new"}, "/repo")
        self.assertEqual(sftp.original, b"old")
        self.assertTrue(sftp.removed)


class HubAudit(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        replay_patch = patch.dict(hub._cancel_replay, clear=True)
        replay_patch.start()
        self.addCleanup(replay_patch.stop)
        self.old_conns, self.old_held = hub._conns, hub._held
        hub._conns, hub._held = {}, {}
        self.addCleanup(setattr, hub, "_conns", self.old_conns)
        self.addCleanup(setattr, hub, "_held", self.old_held)

    async def test_cancel_after_reconnect_uses_current_socket_and_cleans_future(self):
        first, second = SimpleNamespace(send_json=AsyncMock()), SimpleNamespace(send_json=AsyncMock())
        rec = {"ws": first, "pending": {}}
        hub._conns[1, 2] = rec
        task = asyncio.create_task(hub.call(1, 2, "exec"))
        await asyncio.sleep(0)
        current = {"ws": second, "pending": dict(rec["pending"])}
        rec["pending"].clear()
        hub._conns[1, 2] = current
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(current["pending"], {})
        self.assertEqual(second.send_json.call_args.args[0]["t"], "cancel")

    async def test_cancel_during_send_does_not_leak_pending(self):
        async def send(payload):
            if payload["t"] == "job":
                await asyncio.Event().wait()
        rec = {"ws": SimpleNamespace(send_json=send), "pending": {}}
        hub._conns[1, 2] = rec
        task = asyncio.create_task(hub.call(1, 2, "exec"))
        await asyncio.sleep(0)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(rec["pending"], {})

    async def test_multibyte_payload_uses_utf8_wire_budget(self):
        rec = {"pending": {}}
        async def send(payload):
            rec["pending"][payload["id"]].set_result({"ok": True})
        rec["ws"] = SimpleNamespace(send_json=send)
        hub._conns[1, 2] = rec
        result = await hub.call(1, 2, "write", {"content": "é" * 200000})
        self.assertTrue(result["ok"])
        with self.assertRaisesRegex(hub.AgentError, "payload too large"):
            await hub.call(1, 2, "write", {"content": "\x01" * 160000})

    async def test_socket_preserves_read_above_model_limit(self):
        incoming = asyncio.Queue()
        await incoming.put(json.dumps({"t": "hello"}))
        sent = asyncio.Queue()
        async def receive():
            return await incoming.get()
        sock = SimpleNamespace(receive_text=receive, send_json=sent.put, close=AsyncMock())
        with patch.object(hub, "_hello", AsyncMock(return_value=(1, 2, "host", ""))), patch.object(hub.hosts, "touch_agent"), patch.object(hub.hosts, "settings_for", return_value={"agent_coding": True}), patch.object(hub.hosts, "mark_agent_offline"):
            serving = asyncio.create_task(hub.handle_socket(sock))
            await sent.get()
            await asyncio.sleep(0)
            call = asyncio.create_task(hub.call(1, 2, "read", {"id": "wrong", "op": "write"}))
            payload = await sent.get()
            self.assertEqual(payload["op"], "read")
            self.assertNotEqual(payload["id"], "wrong")
            text = "line\n" * 2000
            await incoming.put(json.dumps({"t": "res", "id": payload["id"], "ok": True, "out": text}))
            result = await call
            self.assertEqual(result["out"], text)
            self.assertFalse(result["truncated"])
            serving.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await serving


class JobAudit(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:", check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.addCleanup(self.conn.close)
        p = patch.object(db, "connect", return_value=self.conn)
        p.start()
        self.addCleanup(p.stop)
        self.old_live, self.old_ready = jobs._live, jobs._schema_ready
        jobs._live, jobs._schema_ready = {}, False
        self.addCleanup(setattr, jobs, "_live", self.old_live)
        self.addCleanup(setattr, jobs, "_schema_ready", self.old_ready)

    def launch(self, runner, timeout=1000):
        return jobs.launch(user_id=1, host_id=2, host="host", thread_id=None, run_id=None,
                           command="mock", cwd="/repo", background=True, timeout_ms=timeout, runner=runner)

    async def test_kill_before_first_task_step_finalizes_and_releases_capacity(self):
        runner = AsyncMock()
        rec = self.launch(runner)
        jobs.mark_notified(rec)
        jobs.kill(rec["id"], 1)
        await asyncio.wait_for(asyncio.shield(rec["future"]), 1)
        self.assertEqual(rec["status"], "cancelled")
        runner.assert_not_awaited()
        self.assertEqual(jobs.kill(rec["id"], 1)[1]["name"], "kill_command")
        self.assertEqual(db.fetchone("SELECT status FROM command_jobs")["status"], "cancelled")
        self.assertEqual(db.fetchone("SELECT delivered FROM command_jobs")["delivered"], 1)

    async def test_supervisor_deadline_includes_connection_setup(self):
        async def runner(sink):
            await asyncio.Event().wait()
        # A runner that never delivers a terminal result is still cut by the
        # supervisor — at the deadline plus the bounded result-grace window.
        with patch.object(jobs, "_RUNNER_GRACE_S", 0.5):
            rec = self.launch(runner, timeout=10)
            await asyncio.wait_for(rec["task"], 5)
        self.assertEqual(rec["status"], "timed_out")

    async def test_poll_timeout_does_not_cancel_job_and_user_isolation(self):
        gate = asyncio.Event()
        async def runner(sink):
            sink("€" * 30000)
            await gate.wait()
            sink("THE END")
            return SimpleNamespace(exit_status=7, output="")
        rec = self.launch(runner)
        await asyncio.sleep(0)
        self.assertEqual((await jobs.join(rec["id"], 2, 0))[1]["status"], "error")
        self.assertEqual((await jobs.join(rec["id"], 1, 0))[1]["status"], "running")
        self.assertFalse(rec["task"].done())
        gate.set()
        await rec["task"]
        self.assertEqual(rec["out_bytes"], 90007)
        text, trace = await jobs.join(rec["id"], 1, 0)
        # Head and tail both survive, with the gap stated.
        self.assertIn("characters of output omitted", text)
        self.assertIn("€" * jobs.OUT_HEAD_CHARS, text)
        self.assertTrue(text.endswith("THE END"))
        self.assertEqual(trace["exit_code"], 7)
