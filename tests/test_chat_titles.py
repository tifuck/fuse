"""Offline title integration checks in a disposable MariaDB schema."""
import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from app import chat_titles, chat_store as store, chats, db
from scripts.migrate_chat_metadata import migrate
from tests.mariadb_fixture import isolated_database

REPLY = "Traced the login token check to an expired signing key. " * 5


class TitleSweepTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        fixture = isolated_database()
        fixture.__enter__()
        self.addCleanup(fixture.__exit__, None, None, None)
        with db.tx() as conn:
            migrate(conn)
        store.invalidate_committed_cursors()
        # Turns finished by earlier test modules mark thread ids as due; ids
        # restart at 1 in this fresh schema, so start from an empty queue.
        chat_titles._due.clear()
        chat_titles._inflight.clear()
        db.execute("INSERT INTO users(id,email,password_hash,created_at) VALUES(1,'test@invalid','unused',?)", (db.now(),))

    async def asyncTearDown(self):
        await chat_titles.drain()

    def _pk(self, public_id):
        return db.fetchone("SELECT id FROM chat_threads WHERE public_id=?", (public_id,))["id"]

    def _turn(self, thread, command, text="Hello there, explain the scrape project", *, finish=True, reply=REPLY):
        current = chats.get_thread(1, thread["id"])
        turn = store.start_turn(1, thread["id"], text, current["revision"], command, auto_title=True)
        if finish:
            store.finish_run(turn["run"]["id"], {"content": reply}, "completed")
        return turn

    async def _sweep(self, due=()):
        for thread_id in due:
            chat_titles.notify(thread_id)
        await chat_titles.sweep()
        await asyncio.gather(*list(chat_titles._tasks))

    def test_provisional_title_is_shown_while_the_turn_runs(self):
        thread = chats.create_thread(1, model="offline")
        turn = self._turn(thread, "t1", "Why does `auth.py` reject **valid** tokens after a restart today?", finish=False)
        self.assertEqual(turn["thread"]["title"], "Why does auth.py reject valid tokens…")
        self.assertEqual(store.pending_title_threads(10, attempted_before=""), [])
        store.finish_run(turn["run"]["id"], {"content": REPLY}, "completed")
        pending = store.pending_title_threads(10, attempted_before="")
        self.assertEqual([item["id"] for item in pending], [self._pk(thread["id"])])
        self.assertIn("Request: Why does `auth.py`", pending[0]["prompt"])
        self.assertIn("Result: Traced the login token", pending[0]["prompt"])

    def test_pending_placeholders_and_legacy_but_not_renamed(self):
        fresh = chats.create_thread(1, model="offline")
        self._turn(fresh, "t1", "Tell me about scrape")
        named = chats.create_thread(1, model="offline", title="Already named")
        self._turn(named, "t2")
        legacy = chats.create_thread(1, model="offline", messages=[
            {"role": "user", "content": "Explain the payment gateway retries"},
            {"role": "assistant", "content": REPLY},
        ])
        db.execute("UPDATE chat_threads SET title='Explain the payment gateway retries' WHERE id=?", (self._pk(legacy["id"]),))
        pending = {item["id"] for item in store.pending_title_threads(10, attempted_before="")}
        self.assertIn(self._pk(fresh["id"]), pending)
        self.assertIn(self._pk(legacy["id"]), pending)
        self.assertNotIn(self._pk(named["id"]), pending)
        db.execute("UPDATE chat_threads SET title_state='user' WHERE id IN (?,?)", (self._pk(fresh["id"]), self._pk(legacy["id"])))
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

    def test_small_talk_defers_until_a_topic_or_third_turn(self):
        thread = chats.create_thread(1, model="offline")
        pk = self._pk(thread["id"])
        self._turn(thread, "t1", "hi", reply="Hello! How can I help?")
        self.assertEqual(store.pending_title_threads(10, attempted_before=db.now(), due=[pk]), [])
        self._turn(thread, "t2", "thanks", reply="Sure.")
        self.assertEqual(store.pending_title_threads(10, attempted_before=db.now(), due=[pk]), [])
        self._turn(thread, "t3", "ok", reply="Anything else?")
        self.assertEqual([item["id"] for item in store.pending_title_threads(10, attempted_before="", due=[pk])], [pk])

    async def test_due_thread_skips_backoff_after_its_turn(self):
        thread = chats.create_thread(1, model="offline")
        self._turn(thread, "t1")
        pk = self._pk(thread["id"])
        db.execute("UPDATE chat_threads SET title_attempted_at=? WHERE id=?", (db.now(), pk))
        with patch("app.chat_runtime.generate_thread_title", new_callable=AsyncMock, return_value="Scrape project tour") as gen:
            await self._sweep()
            gen.assert_not_awaited()
            await self._sweep(due=[pk])
        gen.assert_awaited_once()
        self.assertIn("<session>", gen.call_args.args[3])
        self.assertEqual(chats.get_thread(1, thread["id"])["title"], "Scrape project tour")

    async def test_sweep_generates_and_lease_deduplicates(self):
        thread = chats.create_thread(1, model="offline")
        self._turn(thread, "t1")
        pk = self._pk(thread["id"])
        last_activity = "2026-09-20T12:34:56Z"
        db.execute("UPDATE chat_threads SET updated_at=? WHERE id=?", (last_activity, pk))
        with patch("app.chat_runtime.generate_thread_title", new_callable=AsyncMock, return_value="Scrape project tour") as gen:
            await self._sweep()
        row = db.fetchone("SELECT title,title_state,title_job_token FROM chat_threads WHERE id=?", (pk,))
        self.assertEqual(row["title"], "Scrape project tour")
        self.assertEqual(row["title_state"], "generated")
        self.assertEqual(row["title_job_token"], "")
        self.assertEqual(db.fetchone("SELECT updated_at FROM chat_threads WHERE id=?", (pk,))[0], last_activity)
        title_event = store.events_after(1, 0)[-1]
        self.assertEqual(title_event["payload"]["updated_at"], last_activity)
        gen.assert_awaited_once()

    async def test_failed_apply_counts_as_attempt(self):
        thread = chats.create_thread(1, model="offline")
        self._turn(thread, "t1")
        pk = self._pk(thread["id"])
        with patch("app.chat_runtime.generate_thread_title", new_callable=AsyncMock, return_value="New chat"):
            await self._sweep()
        row = db.fetchone("SELECT title_attempts,title_attempted_at,title_state FROM chat_threads WHERE id=?", (pk,))
        self.assertEqual(row["title_attempts"], 1)
        self.assertTrue(row["title_attempted_at"])
        self.assertEqual(row["title_state"], "")

    async def test_fallback_after_exhaustion_never_calls_provider(self):
        thread = chats.create_thread(1, model="offline")
        self._turn(thread, "t1", "Fix login token validation in auth_service.py")
        pk = self._pk(thread["id"])
        last_activity = "2026-09-20T12:34:56Z"
        db.execute("UPDATE chat_threads SET updated_at=? WHERE id=?", (last_activity, pk))
        db.execute("UPDATE chat_threads SET title_attempts=? WHERE id=?", (chats.TITLE_SWEEP_ATTEMPTS, pk))
        with patch("app.chat_runtime.generate_thread_title", new_callable=AsyncMock) as gen:
            await self._sweep()
        row = db.fetchone("SELECT title,title_state,updated_at FROM chat_threads WHERE id=?", (pk,))
        self.assertEqual(row["title_state"], "fallback")
        self.assertIn("auth_service.py", row["title"])
        self.assertLessEqual(len(row["title"].split()), chats.TITLE_WORDS)
        self.assertEqual(row["updated_at"], last_activity)
        gen.assert_not_awaited()

    def test_manual_rename_invalidates_generation_even_same_text(self):
        thread = chats.create_thread(1, model="offline")
        pk = self._pk(thread["id"])
        token = chat_titles.claim(1, pk, explicit=True)
        self.assertTrue(token)
        self.assertFalse(chat_titles.claim(1, pk))
        chats.update_thread(1, pk, title="New chat", expected_revision=thread["revision"], command_id="rename")
        self.assertIsNone(store.apply_regenerated_title(1, pk, "Late title", token=token))

    def test_rename_while_running_wins_over_generation(self):
        thread = chats.create_thread(1, model="offline")
        turn = self._turn(thread, "t1", finish=False)
        chats.update_thread(1, thread["id"], title="Keep me", expected_revision=turn["thread"]["revision"])
        store.finish_run(turn["run"]["id"], {"content": REPLY}, "completed")
        self.assertEqual(store.pending_title_threads(10, attempted_before=""), [])
        self.assertIsNone(store.apply_generated_title(1, thread["id"], "Generated"))
        self.assertEqual(chats.get_thread(1, thread["id"])["title"], "Keep me")

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
        last_activity = "2026-09-20T12:34:56Z"
        db.execute("UPDATE chat_threads SET updated_at=? WHERE id=?", (last_activity, self._pk(thread["id"])))
        with patch("app.chat_runtime.generate_thread_title", new_callable=AsyncMock, return_value="Recap completion guards") as gen:
            await chat_titles.regenerate(1, self._pk(thread["id"]))
        self.assertIn("LATEST_TOPIC", gen.call_args.args[3])
        self.assertLessEqual(len(gen.call_args.args[3]), chats.TITLE_SOURCE_MAX)
        self.assertEqual(chats.get_thread(1, thread["id"])["updated_at"], last_activity)

    def test_title_only_update_preserves_sidebar_order_but_real_message_bumps_it(self):
        older = chats.create_thread(1, model="offline")
        newer = chats.create_thread(1, model="offline")
        pk = self._pk(older["id"])
        db.execute("UPDATE chat_threads SET updated_at='2026-09-20T12:00:00Z' WHERE id=?", (pk,))
        db.execute("UPDATE chat_threads SET updated_at='2026-09-25T12:00:00Z' WHERE id=?", (self._pk(newer["id"]),))
        result = store.apply_generated_title(1, pk, "Login token validation")
        self.assertEqual(result["revision"], older["revision"] + 1)
        rows = db.fetchall("SELECT id FROM chat_threads WHERE user_id=1 ORDER BY updated_at DESC")
        self.assertEqual([row["id"] for row in rows], [self._pk(newer["id"]), pk])
        turn = store.start_turn(1, pk, "Review cookies", result["revision"], "real-message")
        self.assertGreater(turn["thread"]["updated_at"], result["updated_at"])


class TitleTextTest(unittest.TestCase):
    def test_parser_repairs_drafts_to_five_words(self):
        cases = {
            "Fix the login redirect bug in auth.py": "Login redirect bug in auth.py",
            "Add retry handling to the payment gateway webhook client": "Retry handling payment gateway webhook",
            "Recap worker idle timer \u2014 removing the wait": "Recap worker idle timer",
            "Login token validation investigation": "Login token validation",
            '```json\n{"title": "auth.py redirect loop"}\n```': "auth.py redirect loop",
            "Chat title generation review": "Chat title generation",
            "One\nTwo words": "One",
        }
        for raw, expected in cases.items():
            title = chats.parse_generated_title(raw)
            self.assertLessEqual(len(title.split()), chats.TITLE_WORDS, raw)
            self.assertEqual(title, expected, raw)
        self.assertEqual(chats.parse_generated_title("Investigation"), "")

    def test_parser_keeps_identifiers_when_cutting(self):
        title = chats.parse_generated_title("Payment gateway webhook retry handling for stripe_events.py and handleRefund")
        self.assertIn("stripe_events.py", title)
        self.assertIn("handleRefund", title)
        self.assertLessEqual(len(title.split()), chats.TITLE_WORDS)

    def test_provisional_title(self):
        self.assertEqual(chats.provisional_title([]), "New chat")
        self.assertEqual(chats.provisional_title([{"role": "user", "content": "short ask"}]), "short ask")
        self.assertEqual(chats.provisional_title([{"role": "user", "content": "See https://github.com/acme/api/issues/42 now"}]),
                         "See acme/api#42 now")
        self.assertEqual(chats.provisional_title([{"role": "user", "content": "", "attachments": [{"name": "dot.png"}]}]),
                         "dot.png")
        self.assertLessEqual(len(chats.provisional_title([{"role": "user", "content": "word " * 40}])), chats.PROVISIONAL_TITLE_CHARS + 1)

    def test_fallback_prefers_identifiers(self):
        title = chats.fallback_title([{"role": "user", "content": "please can you fix TokenCache in cache_store.py it is broken"}])
        self.assertIn("cache_store.py", title)
        self.assertIn("TokenCache", title)
        self.assertEqual(chats.fallback_title([{"role": "user", "content": "hi"}]), "Conversation notes")
