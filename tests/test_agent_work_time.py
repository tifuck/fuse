from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from app import chat_store as store, chats, db


class AgentWorkTimeTest(unittest.TestCase):
    """Subagent time is the sum of executed legs, never wall clock since spawn."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="fuse-work-")
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
        self.addCleanup(store._live_legs.clear)

    def _child(self):
        thread = chats.create_thread(1, model="offline")
        parent = store.start_turn(1, thread["id"], "Hello", thread["revision"], "turn")["run"]
        child = store.start_child_run(
            user_id=1, thread_id=thread["id"], parent_run_id=parent["id"],
            prompt="inspect", description="inspect", background=True,
        )
        return thread, parent, child

    def _age(self, run_id, days):
        old = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        db.execute("UPDATE chat_runs SET created_at=?, updated_at=? WHERE id=?", (old, old, run_id))

    def _row(self, thread, child_id):
        pk = store.get_run(1, child_id)["thread_id"]
        return next(r for r in store.list_thread_agents(1, pk) if r["id"] == child_id)

    def test_task_id_continuation_days_later_counts_only_active_legs(self):
        thread, parent, child = self._child()
        store.save_turn_progress(child["id"], [{"role": "user", "content": "inspect"}, {"role": "assistant", "content": "a"}])
        store.finish_run(child["id"], {"content": "first", "elapsedMs": 60_000}, "completed")
        self._age(child["id"], 2)
        resumed = store.start_child_run(
            user_id=1, thread_id=thread["id"], parent_run_id=parent["id"],
            prompt="again", description="inspect", background=True, resume_from_id=child["id"],
        )
        self.assertEqual(resumed["request"]["agent"]["work_ms_before"], 60_000)
        self.assertEqual(self._row(thread, child["id"])["work_ms"], 60_000)
        store.finish_run(child["id"], {"content": "second", "elapsedMs": 30_000}, "completed")
        row = self._row(thread, child["id"])
        self.assertEqual(row["work_ms"], 90_000)
        self.assertFalse(row["work_live"])
        event = db.fetchone("SELECT payload FROM chat_events WHERE type='agent.completed' ORDER BY id DESC LIMIT 1")
        self.assertIn('"work_ms":90000', event["payload"].replace(" ", ""))

    def test_followup_continuation_banks_the_finished_leg(self):
        thread, _parent, child = self._child()
        store.save_turn_progress(child["id"], [{"role": "user", "content": "inspect"}, {"role": "assistant", "content": "a"}])
        store.finish_run(child["id"], {"content": "first", "elapsedMs": 45_000}, "completed")
        self.assertTrue(store.continue_child_with_followups(child["id"], ["also tests"]))
        run = store.get_run(1, child["id"])
        self.assertEqual(run["request"]["agent"]["work_ms_before"], 45_000)
        store.finish_run(child["id"], {"content": "second", "elapsedMs": 5_000}, "completed")
        self.assertEqual(self._row(thread, child["id"])["work_ms"], 50_000)

    def test_user_resume_keeps_accumulating_on_the_same_message(self):
        thread, _parent, child = self._child()
        store.save_turn_progress(child["id"], [{"role": "user", "content": "inspect"}, {"role": "assistant", "content": "a"}])
        store.finish_run(child["id"], {"content": "", "elapsedMs": 20_000, "stopped": True}, "cancelled")
        self._age(child["id"], 1)
        store.resume_run(1, child["id"])
        run = store.get_run(1, child["id"])
        self.assertEqual(run["status"], "queued")
        # The kept message still carries the first leg; nothing is double counted.
        self.assertEqual(run["message"]["elapsedMs"], 20_000)
        self.assertEqual(self._row(thread, child["id"])["work_ms"], 20_000)

    def test_live_leg_adds_only_its_own_tail(self):
        thread, _parent, child = self._child()
        token = store.begin_leg(child["id"], 10_000)
        store._live_legs[child["id"]] = (token - 2.0, 10_000)
        row = self._row(thread, child["id"])
        self.assertTrue(row["work_live"])
        self.assertGreaterEqual(row["work_ms"], 12_000)
        self.assertLess(row["work_ms"], 20_000)
        store.end_leg(child["id"], token)  # stale token: a newer leg owns the slot
        self.assertIn(child["id"], store._live_legs)
        store.end_leg(child["id"], token - 2.0)
        self.assertNotIn(child["id"], store._live_legs)
        self.assertFalse(self._row(thread, child["id"])["work_live"])

    def test_row_without_any_timing_reports_unknown_not_zero(self):
        thread, _parent, child = self._child()
        self.assertIsNone(self._row(thread, child["id"])["work_ms"])

    def test_settled_spawn_trace_carries_child_work_time(self):
        _thread, _parent, child = self._child()
        store.finish_run(child["id"], {"content": "done", "elapsedMs": 7_000}, "completed")
        messages = [{"tools": [{"name": "spawn_subagent", "child_id": child["id"], "status": "running", "duration_ms": 12}]}]
        with db.tx() as conn:
            store.settle_spawn_traces(conn, 1, messages)
        trace = messages[0]["tools"][0]
        self.assertEqual(trace["status"], "done")
        self.assertEqual(trace["agent_work_ms"], 7_000)
        parsed = chats.parse_messages([{"id": "m", "role": "assistant", "content": "", "tools": [
            {"id": "t", **trace}]}], strict=True)[0]
        self.assertEqual(parsed["tools"][0]["agent_work_ms"], 7_000)


if __name__ == "__main__":
    unittest.main()
