"""Background title sweep tests; all database writes use a temporary DB."""
import asyncio
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import chat_titles, chat_store as store, chats, db


class TitleSweepTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="fuse-titles-")
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

    def _pk(self, public_id):
        return db.fetchone("SELECT id FROM chat_threads WHERE public_id=?", (public_id,))["id"]

    def _turn(self, thread, command, text="Hello"):
        return store.start_turn(1, thread["id"], text, thread["revision"], command)

    def test_pending_titles_cover_placeholders_legacy_and_project_names(self):
        fresh = chats.create_thread(1, model="offline")
        self._turn(fresh, "t1", "Tell me about the scrape project")
        legacy = chats.create_thread(1, model="offline")
        self._turn(legacy, "t2", "Legacy words")
        db.execute("UPDATE chat_threads SET title=? WHERE id=?", ("Legacy words", self._pk(legacy["id"])))
        named = chats.create_thread(1, model="offline", title="Already named")
        self._turn(named, "t3")
        pending = {item["id"]: item for item in store.pending_title_threads(10, attempted_before="")}
        self.assertIn(self._pk(fresh["id"]), pending)
        self.assertIn(self._pk(legacy["id"]), pending)
        self.assertNotIn(self._pk(named["id"]), pending)
        self.assertEqual(pending[self._pk(fresh["id"])]["prompt"], "Tell me about the scrape project")

    def test_renamed_threads_are_never_pending(self):
        thread = chats.create_thread(1, model="offline")
        self._turn(thread, "t1")
        db.execute("UPDATE chat_threads SET title_state='user' WHERE id=?", (self._pk(thread["id"]),))
        self.assertEqual(store.pending_title_threads(10, attempted_before=""), [])

    def test_attempts_cap_and_backoff_cutoff(self):
        thread = chats.create_thread(1, model="offline")
        self._turn(thread, "t1")
        pk = self._pk(thread["id"])
        self.assertEqual(len(store.pending_title_threads(10, attempted_before="")), 1)
        store.mark_title_attempted(pk)
        self.assertEqual(store.pending_title_threads(10, attempted_before=""), [])
        after = (datetime.now(timezone.utc) + timedelta(seconds=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        self.assertEqual(len(store.pending_title_threads(10, attempted_before=after)), 1)
        for _ in range(chats.TITLE_SWEEP_ATTEMPTS - 1):
            store.mark_title_attempted(pk)
        self.assertEqual(store.pending_title_threads(10, attempted_before=after), [])

    async def _swept(self, title):
        thread = chats.create_thread(1, model="offline")
        self._turn(thread, "t1")
        pk = self._pk(thread["id"])
        with patch("app.accounts.get_user", return_value=db.fetchone("SELECT * FROM users WHERE id=1")), \
             patch("app.chat_runtime.generate_thread_title", new_callable=AsyncMock, return_value=title), \
             patch("app.chat_runs.internal_request", return_value=object()):
            await chat_titles.sweep()
            await asyncio.gather(*list(chat_titles._tasks))
        return pk

    async def test_sweep_generates_and_applies(self):
        pk = await self._swept("Scrape Project Tour")
        self.assertEqual(chat_titles._inflight, set())
        row = db.fetchone("SELECT title, title_state, title_attempts FROM chat_threads WHERE id=?", (pk,))
        self.assertEqual(row["title"], "Scrape Project Tour")
        self.assertEqual(row["title_state"], "generated")
        self.assertEqual(row["title_attempts"], 0)

    async def test_sweep_records_failed_attempts(self):
        pk = await self._swept("")
        self.assertEqual(chat_titles._inflight, set())
        row = db.fetchone("SELECT title, title_state, title_attempts FROM chat_threads WHERE id=?", (pk,))
        self.assertEqual(row["title_state"], "")
        self.assertEqual(row["title_attempts"], 1)


if __name__ == "__main__":
    unittest.main()
