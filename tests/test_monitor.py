"""Monitor tool: line batching, envelopes, job delivery, catalog, traces."""
from __future__ import annotations

import asyncio
import contextlib
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
_TMP = tempfile.mkdtemp(prefix="fuse-monitor-")
os.environ.setdefault("DATABASE_PATH", str(Path(_TMP) / "monitor.db"))
os.environ.setdefault("APP_SECRET", "x" * 32)

from app import agent_catalog, chats, command_jobs, db, monitor, workspace
from app.chat_tools import openai_tools, suggest_tool


class MonitorHelpers(unittest.TestCase):
    def test_deadline_bounds(self):
        self.assertEqual(monitor.deadline_ms(None), 300_000)
        self.assertEqual(monitor.deadline_ms(30), 30_000)
        self.assertEqual(monitor.deadline_ms(1_800_000), 1_800_000)
        self.assertEqual(monitor.deadline_ms(9_999_999), 1_800_000)

    def test_pattern_is_exact(self):
        pat = monitor.compile_pattern("READY")
        self.assertTrue(monitor.match_line("READY now", pat))
        self.assertFalse(monitor.match_line("Traceback (most recent call last):", pat))
        self.assertFalse(monitor.match_line("cpu Error high", pat))
        self.assertFalse(monitor.match_line("still waiting", pat))
        self.assertFalse(monitor.match_line("   ", pat))

    def test_max_events_bounds(self):
        self.assertEqual(monitor.max_events(None), 1)
        self.assertEqual(monitor.max_events(8), 8)
        self.assertEqual(monitor.max_events(99), 50)

    def test_ws_url_ssrf_and_canonicalize(self):
        self.assertEqual(monitor.public_ws_url("wss://example.com/events"), "wss://example.com/events")
        self.assertEqual(monitor.public_ws_url("wss://Example.COM:443/events?x=1"), "wss://example.com/events?x=1")
        self.assertFalse(monitor.public_ws_url("ws://127.0.0.1/events"))
        self.assertFalse(monitor.public_ws_url("https://example.com/events"))
        self.assertFalse(monitor.public_ws_url("wss://user:pass@example.com/events"))

    def test_catalog_and_pack(self):
        self.assertIn("monitor", agent_catalog.WORKSPACE_NAMES)
        self.assertIn("monitor", agent_catalog.PACKS["shell"])
        self.assertIn("monitor", agent_catalog.PACKS["workspace"])
        self.assertNotIn("monitor", agent_catalog.EXPLORE_TOOLS)
        self.assertNotIn("monitor", agent_catalog.PLAN_TOOLS)
        schema = agent_catalog.schema("monitor")
        self.assertIn("max_events", schema["function"]["description"])
        self.assertIn("max_events", schema["function"]["parameters"]["properties"])
        extra = list(agent_catalog.WORKSPACE_NAMES) + ["create_artifact", "update_artifact", "propose_skill"]
        self.assertEqual(len(extra), agent_catalog.MAX_EXTRA)
        names = [s["function"]["name"] for s in openai_tools([], coding=True, extra=["monitor"], project_session=True)]
        self.assertIn("monitor", names)
        self.assertNotIn("tail", __import__("app.chat_tools", fromlist=["_SYNONYMS"])._SYNONYMS)
        self.assertEqual(suggest_tool("watch", coding=True), "monitor")

    def test_trace_allowlist(self):
        traces = chats._parse_tool_traces(
            [{
                "name": "monitor",
                "status": "running",
                "command": "tail -f app.log",
                "url": "",
                "source": "command",
                "event_count": 3,
                "background": True,
                "child_id": "abc123",
            }],
            strict=True,
        )
        self.assertEqual(traces[0]["name"], "monitor")
        self.assertEqual(traces[0]["event_count"], 3)
        self.assertTrue(traces[0]["background"])


class MonitorJobs(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:", check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.addCleanup(self.conn.close)
        for p in (
            patch.object(db, "connect", return_value=self.conn),
            patch.object(command_jobs, "_live", {}),
            patch.object(command_jobs, "_schema_ready", False),
        ):
            p.start()
            self.addCleanup(p.stop)
        command_jobs.ensure_schema()
        # Delivery checks whether a job's owning run is a subagent.
        self.conn.execute("CREATE TABLE chat_runs (id TEXT PRIMARY KEY, parent_run_id TEXT, status TEXT)")

    async def test_stdout_lines_become_events_stderr_does_not(self):
        delivered = []

        async def capture(rec, envelope, seq=None):
            delivered.append(envelope)

        async def runner(sink):
            sink("ready-one\n")
            err = getattr(sink, "err", None)
            if err:
                err("oops on stderr\n")
            await asyncio.sleep(0.25)
            sink("ready-two\n")
            await asyncio.sleep(0.25)
            return SimpleNamespace(exit_status=0)

        with patch.object(command_jobs, "_deliver_monitor_event", capture), patch.object(command_jobs, "_deliver"):
            rec = command_jobs.launch(
                user_id=1, host_id=2, host="box", thread_id=3, run_id="run1",
                command="echo", cwd="/repo", background=True, timeout_ms=10_000,
                runner=runner, kind="monitor", pattern="ready", max_events=8,
            )
            await rec["task"]
        self.assertGreaterEqual(rec["event_count"], 2)
        self.assertTrue(any("ready-one" in env for env in delivered))
        self.assertFalse(any("oops on stderr" in env for env in delivered))
        self.assertIn("oops on stderr", rec.get("err") or "")
        text, trace = command_jobs.render(rec, tool="monitor")
        self.assertEqual(trace["name"], "monitor")
        self.assertEqual(trace["status"], "done")
        self.assertIn("events:", text)
        self.assertIn("[stderr]", text)
        env = command_jobs.notification(rec)
        self.assertIn("<monitor_result", env)
        self.assertIn("events:", env)

    async def test_default_max_events_completes_after_one_line(self):
        async def runner(sink):
            sink("hit\n")
            await asyncio.sleep(2)
            return SimpleNamespace(exit_status=0)

        with patch.object(command_jobs, "_deliver_monitor_event"), patch.object(command_jobs, "_deliver"):
            rec = command_jobs.launch(
                user_id=1, host_id=2, host="box", thread_id=3, run_id=None,
                command="watch", cwd="", background=True, timeout_ms=10_000,
                runner=runner, kind="monitor",
            )
            await rec["task"]
        self.assertEqual(rec["status"], "completed")
        self.assertEqual(rec["event_count"], 1)
        self.assertIn("max_events", rec["error"])

    async def test_timeout_reports_event_count(self):
        async def runner(sink):
            sink("hit\n")
            await asyncio.sleep(2)
            return SimpleNamespace(exit_status=0)

        with patch.object(command_jobs, "_deliver_monitor_event"), patch.object(command_jobs, "_deliver"):
            rec = command_jobs.launch(
                user_id=1, host_id=2, host="box", thread_id=3, run_id=None,
                command="sleep", cwd="", background=True, timeout_ms=400,
                runner=runner, kind="monitor", max_events=50,
            )
            await rec["task"]
        self.assertEqual(rec["status"], "timed_out")
        self.assertGreaterEqual(rec["event_count"], 1)
        self.assertIn("events", rec["error"])

    async def test_firehose_auto_stops(self):
        async def runner(sink):
            for i in range(12):
                sink(f"line-{i}\n")
                await asyncio.sleep(0.22)
            return SimpleNamespace(exit_status=0)

        with patch.object(command_jobs, "_deliver_monitor_event"), patch.object(command_jobs, "_deliver"):
            rec = command_jobs.launch(
                user_id=1, host_id=2, host="box", thread_id=3, run_id=None,
                command="yes", cwd="", background=True, timeout_ms=10_000,
                runner=runner, kind="monitor", max_events=50,
            )
            await rec["task"]
        self.assertEqual(rec["status"], "cancelled")
        self.assertIn("auto-stopped", rec["error"])

    async def test_pending_events_are_peek_only(self):
        gate = asyncio.Event()

        async def runner(sink):
            sink("hit-one\n")
            await asyncio.sleep(0.25)
            await gate.wait()
            return SimpleNamespace(exit_status=0)

        with patch.object(command_jobs, "_deliver"):
            rec = command_jobs.launch(
                user_id=1, host_id=2, host="box", thread_id=3, run_id=None,
                command="watch", cwd="", background=True, timeout_ms=10_000,
                runner=runner, kind="monitor", max_events=8,
            )
            await asyncio.sleep(0.35)
            first = command_jobs.pending_envelopes(1, 3)
            self.assertTrue(first)
            self.assertTrue(first[0]["id"].endswith(":e1"))
            self.assertTrue(rec.get("pending_events"))
            again = command_jobs.pending_envelopes(1, 3)
            self.assertEqual(len(again), len(first))
            command_jobs.mark_delivered([first[0]["id"]])
            self.assertEqual(command_jobs.pending_envelopes(1, 3), [])
            self.assertEqual(int(rec["delivered_event_seq"]), 1)
            gate.set()
            await rec["task"]

    async def test_event_receipt_does_not_mark_job_delivered(self):
        gate = asyncio.Event()

        async def runner(sink):
            sink("hit-one\n")
            await asyncio.sleep(0.25)
            await gate.wait()
            return SimpleNamespace(exit_status=0)

        with patch.object(command_jobs, "_deliver"):
            rec = command_jobs.launch(
                user_id=1, host_id=2, host="box", thread_id=3, run_id=None,
                command="watch", cwd="", background=True, timeout_ms=10_000,
                runner=runner, kind="monitor", max_events=8,
            )
            await asyncio.sleep(0.35)
            items = command_jobs.pending_envelopes(1, 3)
            command_jobs.mark_delivered([items[0]["id"]])
            row = self.conn.execute("SELECT delivered FROM command_jobs WHERE id=?", (rec["id"],)).fetchone()
            self.assertEqual(int(row["delivered"]), 0)
            gate.set()
            await rec["task"]
            pending = command_jobs.pending_envelopes(1, 3)
            self.assertTrue(any("<monitor_result" in item["envelope"] for item in pending))
            self.assertTrue(any(item["id"] == rec["id"] for item in pending))

    async def test_cancel_run_leaves_monitor(self):
        gate = asyncio.Event()

        async def runner(sink):
            await gate.wait()
            return SimpleNamespace(exit_status=0)

        rec = command_jobs.launch(
            user_id=1, host_id=2, host="box", thread_id=3, run_id="run1",
            command="watch", cwd="", background=True, timeout_ms=10_000,
            runner=runner, kind="monitor", max_events=8,
        )
        command_jobs.cancel_run("run1")
        self.assertEqual(rec["status"], "running")
        command_jobs.kill(rec["id"], 1)
        with contextlib.suppress(asyncio.CancelledError):
            await rec["task"]
        self.assertEqual(rec["status"], "cancelled")

    async def test_strict_checkpoint_keeps_monitor_fields(self):
        async def runner(sink):
            sink("ok\n")
            await asyncio.sleep(0.25)
            return SimpleNamespace(exit_status=0)

        with patch.object(command_jobs, "_deliver_monitor_event"), patch.object(command_jobs, "_deliver"):
            rec = command_jobs.launch(
                user_id=1, host_id=2, host="box", thread_id=3, run_id=None,
                command="tail -f app.log", cwd="/repo", background=True, timeout_ms=5_000,
                runner=runner, kind="monitor", max_events=8,
            )
            await rec["task"]
        trace = command_jobs.render(rec, tool="monitor")[1]
        saved = chats._parse_tool_traces([trace], strict=True)[0]
        self.assertEqual(saved["name"], "monitor")
        self.assertEqual(saved["command"], "tail -f app.log")
        self.assertGreaterEqual(saved["event_count"], 1)


class MonitorWorkspace(unittest.IsolatedAsyncioTestCase):
    async def test_rejects_both_command_and_url(self):
        with self.assertRaises(workspace.WorkspaceError):
            await workspace._monitor(
                1, 2, "box",
                {"command": "tail -f x", "url": "wss://example.com/e"},
                "/repo",
            )

    async def test_rejects_neither(self):
        with self.assertRaises(workspace.WorkspaceError):
            await workspace._monitor(1, 2, "box", {}, "/repo")

    async def test_rejects_private_ws(self):
        with self.assertRaises(workspace.WorkspaceError):
            await workspace._monitor(1, 2, "box", {"url": "ws://127.0.0.1/events"}, "/repo")

    async def test_connector_command_monitor_uses_exec_stream(self):
        captured = {}

        async def fake_call(user_id, host_id, op, args=None, timeout=30.0, on_chunk=None):
            captured["op"] = op
            captured["cmd"] = (args or {}).get("cmd")
            if on_chunk:
                on_chunk("out", "READY\n")
                on_chunk("err", "noise\n")
            return {"ok": True, "exit": 0, "out": "", "err": "", "error": "", "timeout": False, "termination_confirmed": True, "truncated": False}

        with patch.object(workspace, "_is_agent", return_value=True), \
             patch.object(workspace, "_rate_ok"), \
             patch("app.agent_hub.call", fake_call), \
             patch.object(command_jobs, "_deliver_monitor_event"), \
             patch.object(command_jobs, "_deliver"):
            text, rec = await workspace._monitor(1, 2, "box", {"command": "tail -f x", "pattern": "READY", "max_events": 8}, "/repo")
            task = command_jobs._live[rec["child_id"]]["task"]
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self.assertEqual(captured["op"], "exec_stream")
        self.assertEqual(captured["cmd"], "tail -f x")
        self.assertEqual(rec["name"], "monitor")
        live = command_jobs._live[rec["child_id"]]
        self.assertIn("READY", live.get("out") or "")
        self.assertIn("noise", live.get("err") or "")


if __name__ == "__main__":
    unittest.main()
