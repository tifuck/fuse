"""Stop/handoff regressions: durable Stop guard, atomic followup handoff,
atomic start_turn replacement (Retry/Edit). Offline temporary SQLite DB."""
import json
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import chat_children, chat_runs as runs, chat_store as store, chats, db


def later(seconds: int = 5) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%SZ")


class StopHandoffTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="fuse-stop-")
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

    def tearDown(self):
        db._conn = None

    def turn(self, text="Hello"):
        thread = chats.create_thread(1, model="offline", tools=[])
        return store.start_turn(1, thread["id"], text, thread["revision"], None)["run"]

    def assert_followup(self, texts):
        self.assertEqual(len(texts), 1)
        self.assertIn("keep going", texts[0])

    def background_child(self, run, followup="keep going"):
        child = store.start_child_run(
            user_id=1, thread_id=run["thread_id"], parent_run_id=run["id"],
            prompt="background work", description="background", background=True,
        )
        self.assertTrue(chat_children.queue_followup(child["id"], followup))
        store.finish_run(child["id"], {"content": "child report"}, "completed")
        return child

    def run_agent(self, run_id):
        row = db.fetchone("SELECT request FROM chat_runs WHERE id=?", (run_id,))
        return (json.loads(row["request"] or "{}").get("agent") or {})

    # (1) completed child finalizer must not requeue after a root Stop.

    def test_completed_child_not_continued_after_root_stop(self):
        run = self.turn()
        child = self.background_child(run)
        self.assertTrue(store.request_cancel(1, run["id"]))
        store.finish_run(run["id"], {"content": "Stopped"}, "completed")

        leftover = chat_children.take_followups(child["id"])
        self.assert_followup(leftover)
        self.assertFalse(chat_children.continue_child_with_followups(child["id"], leftover))

        row = db.fetchone("SELECT status FROM chat_runs WHERE id=?", (child["id"],))
        self.assertEqual(row["status"], "completed")
        # The durable queue survived the refused handoff.
        self.assert_followup(chat_children.take_followups(child["id"]))

    def test_explicit_user_resume_allows_continuation(self):
        run = self.turn()
        child = self.background_child(run)
        store.request_cancel(1, run["id"])
        store.finish_run(run["id"], {"content": "Stopped"}, "completed")
        agent = self.run_agent(child["id"])
        agent["user_resumed_at"] = later()
        row = db.fetchone("SELECT request FROM chat_runs WHERE id=?", (child["id"],))
        req = json.loads(row["request"])
        req["agent"] = agent
        db.execute("UPDATE chat_runs SET request=? WHERE id=?", (json.dumps(req), child["id"]))

        leftover = chat_children.take_followups(child["id"])
        self.assertTrue(chat_children.continue_child_with_followups(child["id"], leftover))
        row = db.fetchone("SELECT status,request FROM chat_runs WHERE id=?", (child["id"],))
        self.assertEqual(row["status"], "queued")
        agent = (json.loads(row["request"]).get("agent") or {})
        self.assertTrue(agent.get("followup_continuation"))
        self.assertEqual(agent.get("pending_followups"), [])
        self.assertIn("keep going", json.loads(row["request"])["messages"][-1]["content"])

    def test_root_stop_after_completion_is_durable(self):
        # Stop lands after the root already finished: the flag on the
        # terminal root is what keeps the completed child from requeueing.
        run = self.turn()
        child = self.background_child(run)
        store.finish_run(run["id"], {"content": "Done"}, "completed")
        self.assertTrue(store.request_cancel(1, run["id"]))

        leftover = chat_children.take_followups(child["id"])
        self.assertFalse(chat_children.continue_child_with_followups(child["id"], leftover))
        self.assertEqual(
            db.fetchone("SELECT status FROM chat_runs WHERE id=?", (child["id"],))["status"],
            "completed",
        )

    def test_unconsumed_followups_reported_and_consumed_atomically(self):
        run = self.turn()
        child = self.background_child(run)
        store.request_cancel(1, run["id"])
        store.finish_run(run["id"], {"content": "Stopped"}, "completed")

        leftover = chat_children.take_followups(child["id"])
        chat_children.flag_unconsumed_followups(child["id"], leftover)
        row = db.fetchone("SELECT message,request FROM chat_runs WHERE id=?", (child["id"],))
        self.assertIn("Unconsumed parent follow-up", json.loads(row["message"])["content"])
        agent = json.loads(row["request"]).get("agent") or {}
        self.assertEqual(len(agent.get("unconsumed_followups") or []), 1)
        self.assertIn("keep going", agent["unconsumed_followups"][0])
        self.assertEqual(agent.get("pending_followups"), [])

    # (2) take_followups must be non-destructive until the handoff is durable.

    def test_take_followups_keeps_terminal_queue(self):
        run = self.turn()
        child = self.background_child(run)
        first = chat_children.take_followups(child["id"])
        second = chat_children.take_followups(child["id"])
        self.assertEqual(first, second)
        self.assert_followup(first)
        agent = self.run_agent(child["id"])
        self.assertTrue(any(item.get("text") == "keep going" for item in agent.get("pending_followups") or []))

    # Durable Stop guard for automated (wake) turns; manual turns stay free.

    def test_wake_turn_rejected_on_stopped_thread_manual_still_allowed(self):
        run = self.turn()
        store.request_cancel(1, run["id"])
        store.finish_run(run["id"], {"content": "Stopped"}, "completed")
        thread = chats.get_thread(1, run["thread_id"])

        with self.assertRaises(chats.ChatValidationError) as ctx:
            store.start_turn(1, run["thread_id"], "auto wake", thread["revision"], None, auto=True)
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertEqual(len(db.fetchall("SELECT id FROM chat_runs WHERE parent_run_id IS NULL")), 1)

        # A resumed child is explicit intent: the wake goes through.
        result = store.start_turn(
            1, run["thread_id"], "auto wake", thread["revision"], None, auto=True, auto_resumed_at=later()
        )
        self.assertFalse(result["idempotent"])
        store.finish_run(result["run"]["id"], {"content": "wake done"}, "completed")

        # The user's own next message is never blocked.
        thread = chats.get_thread(1, run["thread_id"])
        result = store.start_turn(1, run["thread_id"], "user message", thread["revision"], None)
        self.assertFalse(result["idempotent"])


class ReplaceFromTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="fuse-replace-")
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
        self.thread = chats.create_thread(1, model="offline", tools=[])
        self.tid = chats.resolve_thread_pk(1, self.thread["id"])

    def tearDown(self):
        db._conn = None

    def history(self):
        run1 = store.start_turn(1, self.thread["id"], "first", self.thread["revision"], None)["run"]
        store.finish_run(run1["id"], {"content": "first answer"}, "completed")
        thread = chats.get_thread(1, self.thread["id"])
        run2 = store.start_turn(1, self.thread["id"], "second", thread["revision"], None)["run"]
        store.finish_run(run2["id"], {"content": "second answer"}, "completed")
        return chats.get_thread(1, self.thread["id"])

    def contents(self):
        return [m.get("content") for m in chats.get_thread(1, self.thread["id"])["messages"]]

    def roots(self):
        return db.fetchall("SELECT id FROM chat_runs WHERE thread_id=? AND parent_run_id IS NULL", (self.tid,))

    def test_atomic_replacement_truncates_and_appends(self):
        thread = self.history()
        self.assertEqual(self.contents(), ["first", "first answer", "second", "second answer"])
        result = store.start_turn(
            1, self.thread["id"], "edited", thread["revision"], None, replace_from=2
        )
        self.assertFalse(result["idempotent"])
        self.assertEqual(self.contents(), ["first", "first answer", "edited", ""])
        new_thread = chats.get_thread(1, self.thread["id"])
        self.assertEqual(new_thread["revision"], thread["revision"] + 1)
        self.assertEqual(len(self.roots()), 3)
        self.assertEqual(result["run"]["status"], "queued")

    def test_replacement_rejections_leave_transcript_intact(self):
        thread = self.history()
        before = self.contents()
        cases = [
            (True, "non-negative integer"),
            (-1, "non-negative integer"),
            ("2", "non-negative integer"),
            (1, "existing user message"),
            (99, "existing user message"),
        ]
        for value, fragment in cases:
            with self.assertRaises(chats.ChatValidationError) as ctx:
                store.start_turn(1, self.thread["id"], "edited", thread["revision"], None, replace_from=value)
            self.assertIn(fragment, str(ctx.exception.detail))
        with self.assertRaises(chats.ChatValidationError) as ctx:
            store.start_turn(1, self.thread["id"], "edited", None, None, replace_from=2)
        self.assertIn("expected_revision is required", str(ctx.exception.detail))
        # Nothing was deleted and no run was created by any rejection.
        self.assertEqual(self.contents(), before)
        self.assertEqual(chats.get_thread(1, self.thread["id"])["revision"], thread["revision"])
        self.assertEqual(len(self.roots()), 2)

    def test_replacement_refused_during_active_run(self):
        thread = self.history()
        store.start_turn(1, self.thread["id"], "live turn", thread["revision"], None)
        with self.assertRaises(chats.ChatValidationError) as ctx:
            store.start_turn(
                1, self.thread["id"], "edited", thread["revision"] + 1, None, replace_from=2
            )
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertIn("active run", str(ctx.exception.detail))
        self.assertEqual(self.contents(), ["first", "first answer", "second", "second answer", "live turn", ""])

    def test_replacement_idempotent_on_command_retry(self):
        thread = self.history()
        first = store.start_turn(
            1, self.thread["id"], "edited", thread["revision"], "cmd-replace", replace_from=2
        )
        self.assertFalse(first["idempotent"])
        run_id = first["run"]["id"]
        store.finish_run(run_id, {"content": "edited answer"}, "completed")
        refreshed = chats.get_thread(1, self.thread["id"])["revision"]
        retry = store.start_turn(
            1, self.thread["id"], "edited", refreshed, "cmd-replace", replace_from=2
        )
        self.assertTrue(retry["idempotent"])
        self.assertEqual(retry["run"]["id"], run_id)
        self.assertEqual(self.contents(), ["first", "first answer", "edited", "edited answer"])
        # A different replace_from index is a different intent.
        with self.assertRaises(chats.ChatValidationError):
            store.start_turn(
                1, self.thread["id"], "edited", refreshed, "cmd-replace", replace_from=0
            )

    def test_replacement_invalidates_dropped_checkpoints(self):
        thread = self.history()
        messages = thread["messages"]
        from app import context_compact

        state = context_compact.parse_state(None)
        state["checkpoints"] = [
            {"id": "keep", "through_message_id": messages[1]["id"], "summary": "kept summary"},
            {"id": "drop", "through_message_id": messages[3]["id"], "summary": "dropped summary"},
        ]
        db.execute(
            "UPDATE chat_threads SET compaction=? WHERE id=?",
            (context_compact.dump_state(state), self.tid),
        )
        result = store.start_turn(
            1, self.thread["id"], "edited", thread["revision"], None, replace_from=2
        )
        self.assertFalse(result["idempotent"])
        stored = context_compact.parse_state(
            db.fetchone("SELECT compaction FROM chat_threads WHERE id=?", (self.tid,))["compaction"]
        )
        self.assertEqual([c["id"] for c in stored.get("checkpoints") or []], ["keep"])


if __name__ == "__main__":
    unittest.main()
