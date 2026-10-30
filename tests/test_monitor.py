"""Monitor tool: line batching, envelopes, job delivery, catalog, traces."""
from __future__ import annotations

import asyncio
import contextlib
import os
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

import pytest

# Uses the database: a disposable MariaDB schema shared by this module.
pytestmark = pytest.mark.usefixtures("isolated_db")


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

    def test_catastrophic_pattern_times_out_instead_of_blocking(self):
        import time

        pat = monitor.compile_pattern(r"(x+x+)+y")
        start = time.monotonic()
        self.assertFalse(monitor.match_line("x" * 5000, pat))
        self.assertLess(time.monotonic() - start, 1.0)

    def test_max_events_bounds(self):
        self.assertEqual(monitor.max_events(None), 1)
        self.assertEqual(monitor.max_events(8), 8)
        self.assertEqual(monitor.max_events(99), 50)

    def test_ws_url_ssrf_and_canonicalize(self):
        with patch.object(monitor, "_resolves_public", return_value=True):
            self.assertEqual(monitor.public_ws_url("wss://example.com/events"), "wss://example.com/events")
            self.assertEqual(monitor.public_ws_url("wss://Example.COM:443/events?x=1"), "wss://example.com/events?x=1")
            self.assertFalse(monitor.public_ws_url("ws://127.0.0.1/events"))
            self.assertFalse(monitor.public_ws_url("https://example.com/events"))
            self.assertFalse(monitor.public_ws_url("wss://user:pass@example.com/events"))
        with patch.object(monitor, "_resolves_public", return_value=False):
            self.assertFalse(monitor.public_ws_url("wss://example.com/events"))

    def test_ws_url_rejects_private_dns_answers(self):
        private = [(2, 1, 6, "", ("10.0.0.5", 80))]
        public = [(2, 1, 6, "", ("93.184.216.34", 80))]
        with patch.object(monitor.socket, "getaddrinfo", return_value=private):
            self.assertFalse(monitor._resolves_public("evil.example", 80))
        with patch.object(monitor.socket, "getaddrinfo", return_value=public + private):
            self.assertFalse(monitor._resolves_public("evil.example", 80))
        with patch.object(monitor.socket, "getaddrinfo", return_value=public):
            self.assertTrue(monitor._resolves_public("ok.example", 443))
        with patch.object(monitor.socket, "getaddrinfo", side_effect=OSError("dns")):
            self.assertFalse(monitor._resolves_public("nope.example", 80))
        with patch.object(monitor.socket, "getaddrinfo", return_value=[]):
            self.assertFalse(monitor._resolves_public("empty.example", 80))

    def test_line_batcher_bounds_huge_lines(self):
        async def run():
            seen = []
            batcher = monitor.LineBatcher({"_pattern": None}, lambda lines, overflow=0: seen.extend(lines))
            batcher.feed("x" * 50_000)
            batcher.feed("y" * 50_000 + "\n")
            await batcher.close()
            return seen

        seen = asyncio.run(run())
        self.assertTrue(seen)
        self.assertLessEqual(max(len(line) for line in seen), monitor.MAX_LINE_CHARS + 1)

    def test_long_logical_lines_are_independent_of_chunk_boundaries(self):
        async def run(chunks, pattern):
            seen = []
            batcher = monitor.LineBatcher(
                {"_pattern": monitor.compile_pattern(pattern)},
                lambda lines, overflow=0: seen.extend(lines),
            )
            for chunk in chunks:
                batcher.feed(chunk)
                self.assertLessEqual(len(batcher.buf), monitor.MAX_MATCH_LINE_CHARS)
            await batcher.close()
            return seen

        cases = [
            ("x" * 1998 + "READY\n", "READY", 1),
            ("x" * 2000 + "READY\n", "^READY$", 0),
            ("x" * 100_000 + "\n", "", 1),
            ("x" * 100_000 + "\n", "^x+$", 0),
        ]
        for line, pattern, expected in cases:
            with self.subTest(pattern=pattern, length=len(line)):
                whole = asyncio.run(run([line], pattern))
                for chunks in ([line[:2001], line[2001:]], [line[:-1], "\n"]):
                    self.assertEqual(asyncio.run(run(chunks, pattern)), whole)
                self.assertEqual(sum(not monitor.is_batch_note(row) for row in whole), expected)

    def test_slow_pattern_has_one_budget_per_feed(self):
        # Each line can burn the full per-line timeout; one feed must not.
        import time as _time

        def slow_match(_line, _pattern):
            _time.sleep(0.03)  # a line that hits the per-line regex timeout
            return False

        async def run():
            seen = []
            batcher = monitor.LineBatcher({"_pattern": monitor.compile_pattern("x")},
                                          lambda lines, overflow=0: seen.extend(lines))
            started = _time.monotonic()
            with patch.object(monitor, "match_line", slow_match):
                batcher.feed("line\n" * 200)
            took = _time.monotonic() - started
            await batcher.close()
            return took, seen

        took, seen = asyncio.run(run())
        self.assertLess(took, monitor.FEED_MATCH_BUDGET_S + monitor.MATCH_TIMEOUT_S + 0.5)
        self.assertTrue(any("not checked" in line for line in seen), seen)

    def test_firehose_keeps_only_shown_lines_and_counts_the_rest(self):
        async def run():
            got = []
            batcher = monitor.LineBatcher({"_pattern": None}, lambda lines, overflow=0: got.append((lines, overflow)))
            batcher.feed("line\n" * 5000)
            self.assertLessEqual(len(batcher.pending), monitor.MAX_BATCH_LINES)
            await batcher.close()
            return got

        (lines, overflow), = asyncio.run(run())
        self.assertEqual(overflow, 5000 - monitor.MAX_BATCH_LINES)
        self.assertIn("more lines in this batch omitted", lines[-1])

    def test_catalog_and_pack(self):
        self.assertIn("monitor", agent_catalog.WORKSPACE_NAMES)
        self.assertIn("monitor", agent_catalog.PACKS["shell"])
        self.assertIn("monitor", agent_catalog.PACKS["workspace"])
        self.assertNotIn("monitor", agent_catalog.EXPLORE_TOOLS)
        self.assertNotIn("monitor", agent_catalog.PLAN_TOOLS)
        schema = agent_catalog.schema("monitor")
        self.assertIn("max_events", schema["function"]["description"])
        self.assertIn("max_events", schema["function"]["parameters"]["properties"])
        self.assertEqual(schema["function"]["parameters"]["required"], ["description"])
        shell = agent_catalog.schema("run_terminal_command")["function"]["parameters"]
        self.assertIn("description", shell["properties"])
        self.assertNotIn("description", shell["required"])
        extra = list(agent_catalog.WORKSPACE_NAMES) + [*agent_catalog.ARTIFACT_NAMES, "propose_skill"]
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
        # The module's disposable MariaDB schema has the full app schema
        # (delivery also reads chat_runs/chat_threads); each test starts empty.
        for p in (
            patch.object(command_jobs, "_live", {}),
            patch.object(command_jobs, "_schema_ready", False),
        ):
            p.start()
            self.addCleanup(p.stop)
        command_jobs.ensure_schema()
        db.execute("DELETE FROM command_jobs")
        db.execute("DELETE FROM chat_runs")

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

    async def test_late_count_write_never_rewinds_the_terminal_count(self):
        db.execute(
            """INSERT INTO command_jobs(id,user_id,host_id,command,timeout_ms,status,created_at,kind,event_count)
               VALUES('mon-late',1,2,'watch',1000,'completed',?,'monitor',5)""",
            (db.now(),),
        )
        command_jobs._persist_monitor_count({"id": "mon-late", "event_count": 3})
        row = db.fetchone("SELECT event_count FROM command_jobs WHERE id='mon-late'")
        self.assertEqual(int(row["event_count"]), 5)

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
        # Reaching max_events is the normal end of a watch, not an error.
        self.assertEqual(rec["error"], "")
        envelope = monitor.result_envelope(rec)
        self.assertIn("\nended: max_events reached (normal stop)\n", envelope)
        self.assertNotIn("error:", envelope)

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
            row = db.fetchone("SELECT delivered FROM command_jobs WHERE id=?", (rec["id"],))
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


class MonitorDescription(unittest.TestCase):
    def test_envelopes_carry_the_description(self):
        rec = {"id": "m1", "status": "completed", "host": "box", "command": "tail -f x", "source": "command",
               "event_count": 2, "out": "ERROR one", "description": "errors in deploy.log"}
        self.assertIn("\ndescription: errors in deploy.log\n", monitor.result_envelope(rec))
        self.assertIn("\ntimeout_ms: 300000\n", monitor.result_envelope({**rec, "timeout_ms": 300_000}))
        self.assertNotIn("timeout_ms:", monitor.result_envelope(rec))
        self.assertIn("\ndescription: errors in deploy.log\n", monitor.event_envelope(rec, ["ERROR one"], 1))
        self.assertNotIn("description:", monitor.result_envelope({**rec, "description": ""}))


class MonitorWorkspace(unittest.IsolatedAsyncioTestCase):
    async def test_rejects_both_command_and_url(self):
        with patch.object(monitor, "_resolves_public", return_value=True):
            with self.assertRaises(workspace.WorkspaceError):
                await workspace._monitor(
                    1, 2, "box",
                    {"command": "tail -f x", "url": "wss://example.com/e"},
                    "/repo",
                )

    async def test_rejects_neither(self):
        with self.assertRaises(workspace.WorkspaceError):
            await workspace._monitor(1, 2, "box", {}, "/repo")

    async def test_requires_description(self):
        with self.assertRaises(workspace.WorkspaceError) as ctx:
            await workspace._monitor(1, 2, "box", {"command": "tail -f x"}, "/repo")
        self.assertIn("description is required", ctx.exception.detail)

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
            text, rec = await workspace._monitor(1, 2, "box", {"command": "tail -f x", "description": "readiness in x", "pattern": "READY", "max_events": 8}, "/repo")
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
