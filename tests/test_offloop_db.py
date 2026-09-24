"""Off-loop DB contract: hot async paths must not block the event loop.

The permission ask poll, command job launch/finish writes, and tool-failure
logging all go through worker threads. A slow or exhausted database pool must
never stall the loop (and therefore every running agent) behind one call.
"""
from __future__ import annotations

import asyncio
import os
import sqlite3
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
_TMP = tempfile.mkdtemp(prefix="fuse-offloop-")
os.environ.setdefault("DATABASE_PATH", str(Path(_TMP) / "offloop.db"))
os.environ.setdefault("APP_SECRET", "x" * 32)

from app import chat_runs  # noqa: F401  (warm the import the poll loop does once)
from app import command_jobs, db, permissions


def _gate(thread_id=1, run_id="run-1", notify=None):
    return permissions.Gate(
        user_id=1,
        thread_id=thread_id,
        run_id=run_id,
        requester={"role": "main"},
        unattended=False,
        notify=notify or (lambda event: None),
        cancel=None,
    )


class AskPollOffLoopTest(unittest.IsolatedAsyncioTestCase):
    async def test_slow_poll_reads_do_not_block_the_loop(self):
        """A permission poll blocked on the DB leaves the loop responsive."""
        ticks = []
        DB_DELAY = 0.15

        def slow_bypass(user_id, thread_id):
            time.sleep(DB_DELAY)
            return False

        def slow_get_ask(user_id, ask_id):
            time.sleep(DB_DELAY)
            return None

        async def heartbeat():
            while True:
                ticks.append(time.monotonic())
                await asyncio.sleep(0.02)

        events = []
        # Warm the default executor and module imports so the measured window
        # contains only poll-loop activity.
        await asyncio.to_thread(lambda: None)
        with patch.object(permissions, "_insert_ask", lambda *a, **k: True), \
             patch.object(permissions, "bypass_enabled", slow_bypass), \
             patch.object(permissions, "_get_ask", slow_get_ask), \
             patch.object(permissions, "_finish_ask", lambda *a, **k: None), \
             patch.object(permissions, "ASK_TIMEOUT_S", 0.4):
            gate = _gate(notify=events.append)
            beat = asyncio.create_task(heartbeat())
            try:
                action = await permissions._ask_and_wait(
                    gate, {"tool": "read_file", "path": "/etc/passwd"}
                )
            finally:
                beat.cancel()
        self.assertEqual(action, "timeout")
        # At least one poll round (>= 2*DB_DELAY of blocked DB time) elapsed;
        # the heartbeat must have ticked during it, proving the loop ran.
        self.assertGreaterEqual(len(ticks), 3)
        gaps = [b - a for a, b in zip(ticks, ticks[1:])]
        self.assertLess(max(gaps), DB_DELAY, f"loop stalled behind a DB read: {gaps}")
        kinds = [e.get("status") for e in events]
        self.assertIn("pending", kinds)
        self.assertIn("timeout", kinds)


class LaunchAsyncTest(unittest.IsolatedAsyncioTestCase):
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

    async def test_launch_async_persists_and_finishes_off_loop(self):
        async def runner(sink):
            sink("hello")
            return SimpleNamespace(exit_status=0)

        rec = await command_jobs.launch_async(
            user_id=1, host_id=2, host="box", thread_id=3, run_id=None,
            command="echo hello", cwd="", background=False,
            timeout_ms=5_000, runner=runner,
        )
        self.assertEqual(rec["status"], "running")
        row = db.fetchone("SELECT status FROM command_jobs WHERE id=?", (rec["id"],))
        self.assertIsNotNone(row)
        await rec["task"]
        self.assertEqual(rec["status"], "completed")
        row = db.fetchone("SELECT status, exit_code FROM command_jobs WHERE id=?", (rec["id"],))
        self.assertEqual(row["status"], "completed")
        self.assertEqual(int(row["exit_code"]), 0)

    async def test_launch_async_admission_matches_sync_path(self):
        async def runner(sink):
            return SimpleNamespace(exit_status=0)

        with patch.object(command_jobs, "JOB_CAP", 1):
            first = await command_jobs.launch_async(
                user_id=1, host_id=2, host="box", thread_id=None, run_id=None,
                command="a", cwd="", background=True, timeout_ms=5_000, runner=runner,
            )
            with self.assertRaises(ValueError):
                await command_jobs.launch_async(
                    user_id=1, host_id=3, host="box2", thread_id=None, run_id=None,
                    command="b", cwd="", background=True, timeout_ms=5_000, runner=runner,
                )
        await first["task"]


if __name__ == "__main__":
    unittest.main()
