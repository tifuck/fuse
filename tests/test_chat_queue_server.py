"""Server-held composer queue (app/chat_queue.py): a queued message starts when
the running turn ends with no tab open, Stop holds it until Send now, and a
tab's late copy never sends it twice. Offline temporary SQLite DB."""
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import chat_queue, chat_runs as runs, chat_store as store, chats, command_jobs, db


class ServerQueueTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="fuse-server-queue-")
        self.addCleanup(self.tmp.cleanup)
        self.conn = sqlite3.connect(str(Path(self.tmp.name) / "test.db"), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        db._init(self.conn)
        self.addCleanup(self.conn.close)
        patcher = patch.object(db, "connect", return_value=self.conn)
        patcher.start()
        self.addCleanup(patcher.stop)
        db.execute("INSERT INTO users(id,email,password_hash,created_at) VALUES(1,'queue@test.invalid','unused',?)", (db.now(),))
        store.init_schema()
        with patch.object(command_jobs, "_schema_ready", False):
            command_jobs.ensure_schema()
        runs._stopping = False
        thread = chats.create_thread(1, model="offline", tools=[])
        self.run = store.start_turn(1, thread["id"], "First", thread["revision"], "first")["run"]
        self.pk = self.run["thread_id"]

    async def asyncTearDown(self):
        await runs.stop()
        runs._stopping = False

    def queue(self, item_id, text, **fields):
        return chat_queue.put(1, self.pk, {"id": item_id, "text": text, "created": 1, **fields})

    def users(self):
        return [m["content"] for m in chats.get_thread(1, self.pk)["messages"] if m["role"] == "user"]

    async def wake(self):
        with patch.object(runs, "start", new=AsyncMock(return_value=None)) as start, \
                patch.object(command_jobs, "pending_envelopes", return_value=[]):
            await runs._wake_parent(1, self.pk)
        return start

    async def test_run_end_sends_the_next_queued_message_without_a_tab(self):
        self.queue("a", "Second")
        self.queue("b", "Third", created=2)
        (await self.wake()).assert_not_awaited()
        self.assertEqual(self.users(), ["First"], "nothing starts while the turn runs")
        store.finish_run(self.run["id"], {"content": "Done"}, "completed")
        (await self.wake()).assert_awaited_once()
        self.assertEqual(self.users(), ["First", "Second"])
        self.assertEqual(chat_queue.next_item(1, self.pk)["id"], "b")
        # The tab's copy of the sent item arriving late must not queue it again.
        self.assertIsNone(self.queue("a", "Second"))
        self.assertEqual(chat_queue.next_item(1, self.pk)["id"], "b")

    async def test_send_now_priority_goes_first_and_is_never_lowered(self):
        self.queue("a", "Older")
        self.queue("b", "Send now", created=2, priority=50)
        self.queue("b", "Send now", created=2)
        self.assertEqual(chat_queue.next_item(1, self.pk)["id"], "b")

    async def test_stop_holds_until_send_now_releases(self):
        self.queue("a", "Held")
        chat_queue.hold(1, self.pk)
        store.finish_run(self.run["id"], {"content": ""}, "cancelled")
        (await self.wake()).assert_not_awaited()
        self.assertEqual(self.users(), ["First"])
        self.assertEqual(await runs.queue_message(1, self.pk, {"id": "a", "text": "Held"}), (False, True))
        with patch.object(runs, "start", new=AsyncMock(return_value=None)):
            self.assertEqual(await runs.queue_message(1, self.pk, {"id": "a", "text": "Held"}, release=True), (True, False))
            self.assertEqual(await runs.queue_message(1, self.pk, {"id": "a", "text": "Held"}), (True, False))
        self.assertEqual(self.users(), ["First", "Held"], "sent once")

    async def test_stop_holds_what_was_queued_by_the_click_only(self):
        self.queue("a", "Before stop", created=100)
        self.queue("b", "Typed while stopping", created=300)
        chat_queue.hold(1, self.pk, before=200)
        store.finish_run(self.run["id"], {"content": ""}, "cancelled")
        await self.wake()
        self.assertEqual(self.users(), ["First", "Typed while stopping"])
        self.assertIsNone(chat_queue.next_item(1, self.pk), "the earlier one waits for Send now")

    def test_sweep_retries_queued_threads_but_not_held_ones(self):
        store.request_cancel(1, self.run["id"])
        store.finish_run(self.run["id"], {"content": ""}, "cancelled")
        self.queue("a", "Held")
        chat_queue.hold(1, self.pk)
        with patch.object(runs, "schedule_wake") as wake:
            runs.sweep_wakes()
        wake.assert_not_called()
        # Queued after the Stop: a new instruction, sent despite the stopped root.
        self.queue("b", "After stop", created=2)
        with patch.object(runs, "schedule_wake") as wake:
            runs.sweep_wakes()
        wake.assert_called_once_with(1, self.pk)


if __name__ == "__main__":
    unittest.main()
