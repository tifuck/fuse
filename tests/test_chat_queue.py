"""Queue acceptance, durable transcript continuation, and delivery races. Offline."""
import copy
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import chat_store as store, chats, db


class ChatQueueTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="fuse-queue-")
        self.addCleanup(self.tmp.cleanup)
        self.conn = sqlite3.connect(str(Path(self.tmp.name) / "test.db"), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        db._init(self.conn)
        self.addCleanup(self.conn.close)
        self.patch = patch.object(db, "connect", return_value=self.conn)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        db.execute("INSERT INTO users(id,email,password_hash,created_at) VALUES(1,'queue@test.invalid','unused',?)", (db.now(),))
        store.init_schema()
        self.thread = chats.create_thread(1, model="offline")
        self.run = store.start_turn(1, self.thread["id"], "Start", self.thread["revision"], "start")["run"]

    def checkpoint(self, text, tools=None):
        msg = {"content": text, "reasoning": "", "elapsedMs": 1000, "tools": tools or [],
               "segments": [{"type": "content", "text": text}] + [{"type": "tool", "id": t["id"]} for t in tools or []]}
        store.checkpoint(self.run["id"], msg)
        return msg

    def view(self):
        return store.thread_view(self.conn, 1, self.run["thread_id"])

    def deliver(self):
        pending = store.take_steers(self.run["id"])
        row = db.fetchone("SELECT provider_messages FROM chat_runs WHERE id=?", (self.run["id"],))
        transcript = json.loads(row["provider_messages"] or "[]")
        transcript += [{"role": "user", "content": store.steer_envelope(p["id"], p["text"])} for p in pending]
        store.save_turn_progress(self.run["id"], transcript)
        store.seal_steer_display(self.run["id"], [p["id"] for p in pending])

    def test_send_now_creates_real_user_and_live_continuation(self):
        before = self.checkpoint("Before 😀", [{"id": "t1", "name": "read_url", "status": "running"}])
        accepted = store.steer_turn(1, self.thread["id"], "Change direction", "steer-1")
        msgs = accepted["thread"]["messages"]
        self.assertEqual([m["role"] for m in msgs], ["user", "assistant", "user", "assistant"])
        self.assertEqual(msgs[1]["content"], "Before 😀")
        self.assertEqual(msgs[2]["content"], "Change direction")
        self.assertEqual(msgs[3]["id"], self.run["message_id"])
        self.assertEqual(msgs[3]["content"], "")
        self.deliver()
        full = copy.deepcopy(before)
        full["content"] += " After"
        full["segments"] = [{"type": "content", "text": "Before 😀"}, {"type": "tool", "id": "t1"}, {"type": "content", "text": " After"}]
        full["tools"][0]["status"] = "done"
        store.checkpoint(self.run["id"], full, events=[{"fuse": {"event": "message.patch"}}])
        msgs = self.view()["messages"]
        self.assertEqual(msgs[1]["tools"][0]["status"], "done")
        self.assertEqual(msgs[1]["content"], "Before 😀")
        self.assertTrue(msgs[1]["tools_live"])
        self.assertEqual(msgs[3]["content"], " After")
        self.assertEqual(msgs[3]["tools"], [])
        event = db.fetchone("SELECT payload FROM chat_events WHERE type='run.progress' ORDER BY id DESC")
        payload = json.loads(event["payload"])
        self.assertNotIn("message", payload)
        self.assertEqual(payload["events"][0]["fuse"]["content"], {"from": 0, "text": " After"})
        self.assertEqual(payload["updates"][0]["tools"][0]["status"], "done")

    def test_multiple_steers_preserve_text_and_final_history(self):
        self.checkpoint("one")
        store.steer_turn(1, self.thread["id"], "First", "s1")
        self.deliver()
        self.checkpoint("one two")
        store.steer_turn(1, self.thread["id"], "Second", "s2")
        self.deliver()
        full = self.checkpoint("one two three")
        store.finish_run(self.run["id"], full, "completed")
        msgs = self.view()["messages"]
        self.assertEqual([m["content"] for m in msgs], ["Start", "one", "First", " two", "Second", " three"])
        self.assertEqual(len({m["id"] for m in msgs}), len(msgs))
        retry = store.steer_turn(1, self.thread["id"], "First", "s1")
        self.assertTrue(retry["idempotent"])
        self.assertEqual(len(retry["thread"]["messages"]), 6)
        self.assertIsNone(retry["thread"]["run"])

    def test_overflow_rejects_but_finishing_race_accepts_continuation(self):
        for i in range(8):
            store.steer_turn(1, self.thread["id"], f"Instruction {i}", f"s{i}")
        before = self.view()
        with self.assertRaises(chats.ChatValidationError):
            store.steer_turn(1, self.thread["id"], "Overflow", "s9")
        self.assertEqual(self.view()["messages"], before["messages"])
        self.assertEqual(self.view()["revision"], before["revision"])
        self.assertFalse(store.close_steer_window(self.run["id"]))
        pending = store.take_steers(self.run["id"])
        store.save_turn_progress(self.run["id"], [{"role": "user", "content": store.steer_envelope(p["id"], p["text"])} for p in pending])
        self.assertTrue(store.close_steer_window(self.run["id"]))
        store.steer_turn(1, self.thread["id"], "Continue", "late")
        self.assertEqual(self.view()["revision"], before["revision"] + 1)
        store.finish_run(self.run["id"], {"content": ""}, "completed")
        self.assertEqual(store.get_run(1, self.run["id"])["status"], "queued")

    def test_delivery_is_by_command_id_not_repeated_text(self):
        store.save_turn_progress(self.run["id"], [{"role": "user", "content": "Same words"}])
        store.steer_turn(1, self.thread["id"], "Same words", 'first"id')
        store.steer_turn(1, self.thread["id"], "Same words", "second")
        self.assertEqual(len(store.take_steers(self.run["id"])), 2)
        store.save_turn_progress(self.run["id"], [{"role": "user", "content": store.steer_envelope('first"id', "Same words")}])
        self.assertEqual([p["id"] for p in store.take_steers(self.run["id"])], ["second"])

    def test_buffered_old_tokens_stay_above_user_until_delivery(self):
        self.checkpoint("Old response")
        store.steer_turn(1, self.thread["id"], "New instruction", "s1")
        self.checkpoint("Old response…😀")
        msgs = self.view()["messages"]
        self.assertEqual([m["content"] for m in msgs], ["Start", "Old response…😀", "New instruction", ""])
        payload = json.loads(db.fetchone("SELECT payload FROM chat_events WHERE type='run.progress' ORDER BY id DESC")["payload"])
        self.assertEqual(payload["events"][0]["fuse"]["content"], {"from": 0, "text": ""})
        self.deliver()
        final = self.checkpoint("Old response…😀New response")
        store.finish_run(self.run["id"], final, "completed")
        self.assertEqual([m["content"] for m in self.view()["messages"]], ["Start", "Old response…😀", "New instruction", "New response"])

    def test_first_unflushed_output_materializes_before_pending_users(self):
        store.steer_turn(1, self.thread["id"], "First", "s1")
        store.steer_turn(1, self.thread["id"], "Second", "s2")
        self.checkpoint("Buffered first response")
        self.assertEqual([m["content"] for m in self.view()["messages"]], ["Start", "Buffered first response", "First", "Second", ""])
        self.deliver()
        self.checkpoint("Buffered first responseNew response")
        self.assertEqual(self.view()["messages"][-1]["content"], "New response")

    def test_durable_delivery_can_seal_after_recovery(self):
        self.checkpoint("Old")
        store.steer_turn(1, self.thread["id"], "New instruction", "s1")
        store.save_turn_progress(self.run["id"], [{"role": "user", "content": store.steer_envelope("s1", "New instruction")}])
        self.assertEqual(store.take_steers(self.run["id"]), [])
        self.checkpoint("Old tail")
        self.assertEqual(self.view()["messages"][-1]["content"], "")
        self.assertEqual(store.steer_display_deliveries(self.run["id"]), ["s1"])
        store.seal_steer_display(self.run["id"], store.steer_display_deliveries(self.run["id"]))
        self.assertEqual(store.steer_display_deliveries(self.run["id"]), [])
        self.checkpoint("Old tailNew response")
        self.assertEqual(self.view()["messages"][-1]["content"], "New response")

    def test_delivery_seals_only_the_instructions_in_that_model_request(self):
        self.checkpoint("Old")
        store.steer_turn(1, self.thread["id"], "First", "s1")
        store.steer_turn(1, self.thread["id"], "Second", "s2")
        store.save_turn_progress(self.run["id"], [{"role": "user", "content": store.steer_envelope("s1", "First")}])
        store.seal_steer_display(self.run["id"], ["s1"])
        self.checkpoint("Old First reply")
        self.assertEqual([m["content"] for m in self.view()["messages"]], ["Start", "Old", "First", " First reply", "Second", ""])
        self.deliver()
        self.checkpoint("Old First reply Second reply")
        self.assertEqual(self.view()["messages"][-1]["content"], " Second reply")

    def test_rejected_cancelled_run_and_other_user(self):
        before = self.view()["messages"]
        store.request_cancel(1, self.run["id"])
        with self.assertRaises(chats.ChatValidationError):
            store.steer_turn(1, self.thread["id"], "Late", "cancelled")
        with self.assertRaises(chats.ChatValidationError):
            store.steer_turn(2, self.thread["id"], "Intruder", "other-user")
        self.assertEqual(self.view()["messages"], before)

    def test_undelivered_steer_continuation_clock_advances(self):
        def beat(elapsed):
            message = {"content": "Old work", "reasoning": "", "elapsedMs": elapsed, "tools": [],
                       "segments": [{"type": "content", "text": "Old work"}]}
            store.checkpoint(self.run["id"], message)

        self.checkpoint("Old work")
        store.steer_turn(1, self.thread["id"], "Change direction", "clock-1")
        # Heartbeats during the undelivered window (e.g. a long command still
        # running) keep checkpointing the run; the served continuation clock
        # must advance at the same rate a live client ticks, or every reload
        # restarts the dock clock from zero.
        beat(4000)
        self.assertEqual(self.view()["messages"][-1]["elapsedMs"], 3000)
        beat(5000)
        self.assertEqual(self.view()["messages"][-1]["elapsedMs"], 4000)
        pending = store.take_steers(self.run["id"])
        store.save_turn_progress(self.run["id"], [{"role": "user", "content": store.steer_envelope(p["id"], p["text"])} for p in pending])
        store.seal_steer_display(self.run["id"], [p["id"] for p in pending])
        beat(6000)
        msgs = self.view()["messages"]
        # No snap-down at delivery: the clock keeps the same origin, and the
        # frozen prefix keeps exactly its acceptance-time work.
        self.assertEqual(msgs[-1]["elapsedMs"], 5000)
        self.assertEqual(msgs[1]["elapsedMs"], 1000)

    def test_steer_fragments_have_own_duration_and_usage_not_run_totals(self):
        def checkpoint(text, ms, output):
            message = {"content": text, "reasoning": "", "elapsedMs": ms,
                       "usage": {"prompt_tokens": 100 + output, "completion_tokens": output}, "tools": []}
            store.checkpoint(self.run["id"], message)
            return message
        checkpoint("First", 1000, 10)
        store.steer_turn(1, self.thread["id"], "Second instruction", "timed-1")
        self.deliver()
        checkpoint("First Second", 3500, 30)
        store.steer_turn(1, self.thread["id"], "Third instruction", "timed-2")
        self.deliver()
        full = checkpoint("First Second Third", 5000, 45)
        store.finish_run(self.run["id"], full, "cancelled")
        replies = [m for m in self.view()["messages"] if m["role"] == "assistant"]
        self.assertEqual([m["elapsedMs"] for m in replies], [1000, 2500, 1500])
        self.assertEqual([m["usage"]["completion_tokens"] for m in replies], [10, 20, 15])
        self.assertTrue(all(not m.get("tools_live") for m in replies))


if __name__ == "__main__":
    unittest.main()
