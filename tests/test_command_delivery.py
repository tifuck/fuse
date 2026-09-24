"""Background command delivery: handoff labeling, envelope contract, wake copy."""

from __future__ import annotations

import asyncio
import os
import re
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
_TMP = tempfile.mkdtemp(prefix="fuse-cmddel-")
os.environ.setdefault("DATABASE_PATH", str(Path(_TMP) / "cmddel.db"))
os.environ.setdefault("APP_SECRET", "x" * 32)

from app import chat_runs, chat_store as store, chats, command_jobs, db

ENVELOPE_RE = re.compile(r'<command_result id="([^"]+)" state="([^"]+)">([\s\S]*?)</command_result>')


class _Result:
    def __init__(self, exit_status=0, output=""):
        self.exit_status = exit_status
        self.output = output


class CommandDeliveryTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        stopping = patch.object(chat_runs, "_stopping", False)
        stopping.start()
        self.addCleanup(stopping.stop)
        self.tmp = tempfile.TemporaryDirectory(prefix="fuse-cmddel-")
        self.addCleanup(self.tmp.cleanup)
        self.conn = sqlite3.connect(str(Path(self.tmp.name) / "test.db"), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
        db._init(self.conn)
        self.addCleanup(self.conn.close)
        self.connection_patch = patch.object(db, "connect", return_value=self.conn)
        self.connection_patch.start()
        self.addCleanup(self.connection_patch.stop)
        db.execute("INSERT INTO users(id,email,password_hash,created_at) VALUES(1,'1@test.invalid','unused',?)", (db.now(),))
        store.init_schema()
        command_jobs._live.clear()
        command_jobs._schema_ready = False
        command_jobs.ensure_schema()

    def _launch(self, *, background, gate, thread_id=None):
        async def runner(sink):
            await gate.wait()
            return _Result(output="background2")

        return command_jobs.launch(
            user_id=1,
            host_id=1,
            host="box",
            thread_id=thread_id,
            run_id=None,
            command="sleep 2 && echo background2",
            cwd="",
            background=background,
            timeout_ms=60000,
            runner=runner,
        )

    async def test_running_background_render_labels(self):
        gate = asyncio.Event()
        rec = self._launch(background=True, gate=gate)
        text, trace = command_jobs.render(rec)
        self.assertTrue(trace["background"])
        self.assertEqual(trace["status"], "running")
        self.assertEqual(trace["preview"], "")
        self.assertIn("background: true", text)
        gate.set()
        await rec["task"]

    async def test_foreground_handoff_marks_background(self):
        gate = asyncio.Event()
        rec = self._launch(background=False, gate=gate)
        self.assertFalse(rec["background"])
        command_jobs.mark_notified(rec)
        command_jobs.mark_background(rec)
        self.assertTrue(rec["background"])
        row = db.fetchone("SELECT background FROM command_jobs WHERE id=?", (rec["id"],))
        self.assertEqual(int(row["background"]), 1)
        _text, trace = command_jobs.render(rec)
        self.assertTrue(trace["background"])
        self.assertEqual(trace["preview"], "")
        gate.set()
        await rec["task"]

    def test_background_flag_survives_trace_parse(self):
        traces = chats._parse_tool_traces(
            [{"name": "run_terminal_command", "status": "running", "child_id": "j1", "background": True, "command": "sleep 10"}],
            strict=True,
        )
        self.assertEqual(traces[0]["child_id"], "j1")
        self.assertTrue(traces[0].get("background"))

    async def test_stop_is_scoped_to_background_job_thread(self):
        gate = asyncio.Event()
        rec = self._launch(background=True, gate=gate, thread_id=7)
        await asyncio.sleep(0)
        self.assertIsNone(command_jobs.kill_thread_job(1, 8, rec["id"]))
        self.assertEqual(rec["status"], "running")
        self.assertIsNotNone(command_jobs.kill_thread_job(1, 7, rec["id"]))
        await rec["task"]
        self.assertEqual(command_jobs.recent_terminal_jobs(1, 7)[0]["state"], "cancelled")

    async def test_list_thread_jobs_shows_only_running_background(self):
        gate = asyncio.Event()
        rec = self._launch(background=True, gate=gate, thread_id=7)
        jobs = command_jobs.list_thread_jobs(1, 7)
        self.assertEqual([job["id"] for job in jobs], [rec["id"]])
        self.assertEqual(jobs[0]["command"], "sleep 2 && echo background2")
        self.assertGreaterEqual(jobs[0]["elapsed_ms"], 0)
        gate.set()
        await rec["task"]
        self.assertEqual(command_jobs.list_thread_jobs(1, 7), [])
        recent = command_jobs.recent_terminal_jobs(1, 7)
        self.assertEqual([job["id"] for job in recent], [rec["id"]])
        self.assertEqual(recent[0]["state"], "completed")
        self.assertEqual(recent[0]["exit_code"], 0)
        self.assertIn("background2", recent[0]["out"])

    def test_notification_envelope_contract(self):
        env = command_jobs.notification(
            {
                "id": "633a9205596e",
                "status": "completed",
                "host": "box",
                "command": "sleep 2 && echo background2",
                "exit_code": 0,
                "duration_ms": 2043,
                "error": "",
                "out": "background2",
            }
        )
        match = ENVELOPE_RE.search(env)
        self.assertIsNotNone(match)
        self.assertEqual(match.group(1), "633a9205596e")
        self.assertEqual(match.group(2), "completed")
        meta, output = match.group(3).split("\n\n", 1)
        self.assertIn("exit_code: 0", meta)
        self.assertIn("duration_ms: 2043", meta)
        self.assertIn("command: sleep 2 && echo background2", meta)
        self.assertEqual(output.strip(), "background2")

    async def _wake(self, thread_id):
        started = []

        async def fake_start(request, user, run):
            started.append(run)

        with patch.object(chat_runs, "start", fake_start):
            await chat_runs._wake_parent(1, thread_id)
        return started

    def _auto_messages(self, thread_id):
        view = chats.get_thread(1, thread_id)
        return [m for m in view["messages"] if m.get("role") == "user" and m.get("auto")]

    async def test_wake_command_only_copy_has_no_subagent_hints(self):
        thread = chats.create_thread(1, model="offline")
        result = store.start_turn(1, thread["id"], "run tests", thread["revision"], "turn-cmd")
        tid = result["run"]["thread_id"]
        store.finish_run(result["run"]["id"], {"content": "started job", "tools": []}, "completed")
        now = db.now()
        db.execute(
            "INSERT INTO command_jobs (id,user_id,host_id,host,thread_id,run_id,command,cwd,background,"
            " timeout_ms,status,exit_code,error,out,out_bytes,duration_ms,notify,delivered,created_at,started_at,finished_at)"
            " VALUES ('jobcmd1',1,1,'box',?,NULL,'sleep 2 && echo background2','',1,60000,"
            " 'completed',0,'','background2',11,2043,1,0,?,?,?)",
            (tid, now, now, now),
        )
        started = await self._wake(tid)
        self.assertEqual(len(started), 1)
        autos = self._auto_messages(thread["id"])
        self.assertEqual(len(autos), 1)
        text = autos[0]["content"]
        self.assertIn("<command_result", text)
        self.assertNotIn("spawn_subagent", text)
        self.assertNotIn("<task id=", text)
        self.assertEqual(command_jobs.pending_envelopes(1, tid), [])

    async def test_wake_with_children_keeps_resume_hint(self):
        thread = chats.create_thread(1, model="offline")
        result = store.start_turn(1, thread["id"], "go", thread["revision"], "turn-cmd2")
        parent = result["run"]
        child = store.start_child_run(
            user_id=1,
            thread_id=thread["id"],
            parent_run_id=parent["id"],
            prompt="bg",
            description="bg",
            background=True,
        )
        store.finish_run(child["id"], {"content": "child report", "tools": []}, "completed")
        store.finish_run(parent["id"], {"content": "ok", "tools": []}, "completed")
        started = await self._wake(parent["thread_id"])
        self.assertEqual(len(started), 1)
        autos = self._auto_messages(thread["id"])
        self.assertEqual(len(autos), 1)
        text = autos[0]["content"]
        self.assertIn("<task id=", text)
        self.assertIn("spawn_subagent", text)

    async def test_cancelled_root_defers_report_until_user_continues(self):
        thread = chats.create_thread(1, model="offline")
        result = store.start_turn(1, thread["id"], "go", thread["revision"], "turn-cancel")
        parent = result["run"]
        child = store.start_child_run(
            user_id=1,
            thread_id=thread["id"],
            parent_run_id=parent["id"],
            prompt="bg",
            description="bg",
            background=True,
        )
        store.finish_run(child["id"], {"content": "child report", "tools": []}, "completed")
        store.finish_run(parent["id"], {"content": "stopped", "tools": []}, "cancelled")
        started = await self._wake(parent["thread_id"])
        self.assertEqual(len(started), 0)
        autos = self._auto_messages(thread["id"])
        self.assertEqual(len(autos), 0)
        candidates = store.wake_candidates(1, parent["thread_id"])
        self.assertEqual([item["id"] for item in candidates], [child["id"]])
        self.assertEqual(candidates[0]["summary"], "child report")


if __name__ == "__main__":
    unittest.main()
