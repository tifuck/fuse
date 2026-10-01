"""Offline title integration checks in a disposable MariaDB schema."""
import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from app import chat_titles, chat_store as store, chats, db
from scripts.migrate_chat_metadata import migrate
from tests.mariadb_fixture import isolated_database


class TitleSweepTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        fixture = isolated_database()
        fixture.__enter__()
        self.addCleanup(fixture.__exit__, None, None, None)
        with db.tx() as conn:
            migrate(conn)
        store.invalidate_committed_cursors()
        db.execute("INSERT INTO users(id,email,password_hash,created_at) VALUES(1,'test@invalid','unused',?)", (db.now(),))

    async def asyncTearDown(self):
        await chat_titles.drain()

    def _pk(self, public_id):
        return db.fetchone("SELECT id FROM chat_threads WHERE public_id=?", (public_id,))["id"]

    def _turn(self, thread, command, text="Hello"):
        return store.start_turn(1, thread["id"], text, thread["revision"], command, auto_title=True)

    def test_pending_placeholders_and_legacy_but_not_renamed(self):
        fresh = chats.create_thread(1, model="offline")
        self._turn(fresh, "t1", "Tell me about scrape")
        named = chats.create_thread(1, model="offline", title="Already named")
        self._turn(named, "t2")
        pending = {item["id"] for item in store.pending_title_threads(10, attempted_before="")}
        self.assertIn(self._pk(fresh["id"]), pending)
        self.assertNotIn(self._pk(named["id"]), pending)
        db.execute("UPDATE chat_threads SET title_state='user' WHERE id=?", (self._pk(fresh["id"]),))
        self.assertEqual(store.pending_title_threads(10, attempted_before=""), [])

    def test_empty_chats_cannot_starve_older_eligible_threads(self):
        for i in range(40):
            chats.create_thread(1, model="offline")
        eligible = chats.create_thread(1, model="offline")
        self._turn(eligible, "eligible", "Login token validation")
        found = []
        for _ in range(4):
            found.extend(store.pending_title_threads(3, attempted_before=db.now()))
        self.assertIn(self._pk(eligible["id"]), {row["id"] for row in found})

    async def test_sweep_generates_and_lease_deduplicates(self):
        thread = chats.create_thread(1, model="offline")
        self._turn(thread, "t1")
        pk = self._pk(thread["id"])
        with patch("app.chat_runtime.generate_thread_title", new_callable=AsyncMock, return_value="Scrape project tour") as gen:
            await chat_titles.sweep()
            await asyncio.gather(*list(chat_titles._tasks))
        row = db.fetchone("SELECT title,title_state,title_job_token FROM chat_threads WHERE id=?", (pk,))
        self.assertEqual(row["title"], "Scrape project tour")
        self.assertEqual(row["title_state"], "generated")
        self.assertEqual(row["title_job_token"], "")
        gen.assert_awaited_once()

    async def test_fallback_after_exhaustion_never_calls_provider(self):
        thread = chats.create_thread(1, model="offline")
        self._turn(thread, "t1", "Fix login token validation")
        pk = self._pk(thread["id"])
        db.execute("UPDATE chat_threads SET title_attempts=5 WHERE id=?", (pk,))
        with patch("app.chat_runtime.generate_thread_title", new_callable=AsyncMock) as gen:
            await chat_titles.sweep()
            await asyncio.gather(*list(chat_titles._tasks))
        self.assertEqual(db.fetchone("SELECT title_state FROM chat_threads WHERE id=?", (pk,))[0], "fallback")
        gen.assert_not_awaited()

    def test_manual_rename_invalidates_generation_even_same_text(self):
        thread = chats.create_thread(1, model="offline")
        pk = self._pk(thread["id"])
        token = chat_titles.claim(1, pk, explicit=True)
        self.assertTrue(token)
        self.assertFalse(chat_titles.claim(1, pk))
        chats.update_thread(1, pk, title="New chat", expected_revision=thread["revision"], command_id="rename")
        self.assertIsNone(store.apply_regenerated_title(1, pk, "Late title", token=token))

    def test_same_title_marks_generation_complete(self):
        thread = chats.create_thread(1, model="offline")
        pk = self._pk(thread["id"])
        db.execute("UPDATE chat_threads SET title='CSV to JSON' WHERE id=?", (pk,))
        result = store.apply_generated_title(1, pk, "CSV to JSON", "CSV to JSON")
        self.assertIsNotNone(result)
        self.assertEqual(db.fetchone("SELECT title_state FROM chat_threads WHERE id=?", (pk,))[0], "generated")

    async def test_regeneration_sends_recent_context(self):
        thread = chats.create_thread(1, model="offline", messages=[
            {"role": "user", "content": "Opening topic " + "x" * 1700},
            {"role": "assistant", "content": "Reply " + "y" * 7000},
            {"role": "user", "content": "LATEST_TOPIC recap guards"},
        ])
        with patch("app.chat_runtime.generate_thread_title", new_callable=AsyncMock, return_value="Recap completion guards") as gen:
            await chat_titles.regenerate(1, self._pk(thread["id"]))
        self.assertIn("LATEST_TOPIC", gen.call_args.args[3])
        self.assertEqual(gen.call_args.kwargs["limit"], 6000)
