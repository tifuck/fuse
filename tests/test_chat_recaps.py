"""Durable settlement, size/work gating and metadata-only recap regressions."""
import asyncio
import json
import unittest
from unittest.mock import AsyncMock, patch

from app import chat_recaps as recaps, chat_store as store, chats, db
from scripts.migrate_chat_metadata import migrate
from tests.mariadb_fixture import isolated_database


class RecapTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        fixture = isolated_database()
        fixture.__enter__()
        self.addCleanup(fixture.__exit__, None, None, None)
        with db.tx() as conn:
            migrate(conn)
        store.invalidate_committed_cursors()
        db.execute("INSERT INTO users(id,email,password_hash,created_at) VALUES(1,'test@invalid','unused',?)", (db.now(),))
        self.thread = chats.create_thread(1, model="offline")
        self.pk = chats.resolve_thread_pk(1, self.thread["id"])
        self.turn = store.start_turn(1, self.pk, "Fix token validation", self.thread["revision"], "first")

    async def asyncTearDown(self):
        await recaps.drain()

    def complete(self, status="completed", elapsed=90_000):
        store.finish_run(self.turn["run"]["id"], {"content": "Fixed token validation. Tests passed. " + "Details. " * 100,
                                                  "elapsedMs": elapsed}, status)

    def job(self):
        return dict(db.fetchone("SELECT * FROM chat_recaps WHERE thread_id=? ORDER BY epoch DESC LIMIT 1", (self.pk,)))

    def age(self):
        """Skip the short settle re-check / retry backoff."""
        db.execute("UPDATE chat_recaps SET next_at='' WHERE id=?", (self.job()["id"],))

    def child(self, status="running", background=False):
        req = json.dumps({"agent": {"background": background}})
        db.execute("""INSERT INTO chat_runs(id,user_id,thread_id,status,request,message_id,message,created_at,updated_at,parent_run_id)
                      VALUES('child',1,?,?,?,'child-msg','{}',?,? ,?)""",
                   (self.pk, status, req, db.now(), db.now(), self.turn["run"]["id"]))

    def command(self, status="running", delivered=1):
        db.execute("""INSERT INTO command_jobs(id,user_id,host_id,thread_id,run_id,command,cwd,background,timeout_ms,status,created_at,started_at,notify,delivered)
                      VALUES('cmd',1,1,?,?,'pytest','/repo',1,1000,?,?,?,1,?)""",
                   (self.pk, self.turn["run"]["id"], status, db.now(), db.now(), delivered))

    def test_no_request_until_turn_children_and_commands_are_idle(self):
        self.assertIsNone(recaps.claim(self.job()["id"]))
        self.child()
        self.complete()
        self.assertIsNone(recaps.claim(self.job()["id"]))  # Subagent still running.
        db.execute("UPDATE chat_runs SET status='completed' WHERE id='child'")
        self.command()
        self.age()
        self.assertIsNone(recaps.claim(self.job()["id"]))  # Background command still running.
        db.execute("UPDATE command_jobs SET status='completed' WHERE id='cmd'")
        self.age()
        self.assertIsNotNone(recaps.claim(self.job()["id"]))  # Idle: no extra wait.

    def test_settled_turn_generates_immediately(self):
        self.complete()
        self.assertIsNotNone(recaps.claim(self.job()["id"]))

    def test_short_quick_episode_is_skipped(self):
        self.complete(elapsed=5_000)
        self.assertIsNone(recaps.claim(self.job()["id"]))
        self.assertEqual(self.job()["state"], "skipped")

    def test_busy_publication_does_not_spend_an_attempt(self):
        self.complete()
        job = recaps.claim(self.job()["id"])
        self.command()
        recaps.finish(job, "Fixed token validation.")
        current = self.job()
        self.assertEqual((current["state"], current["attempts"]), ("pending", 0))
        self.assertEqual(chats.get_thread(1, self.pk)["recaps"], [])

    def test_finished_child_report_must_be_delivered_then_wake_settle(self):
        self.complete()
        self.child("completed", background=True)
        self.age()
        self.assertIsNone(recaps.claim(self.job()["id"]))
        db.execute("UPDATE chat_runs SET request=? WHERE id='child'", (json.dumps({"agent": {"background": True, "wake_sent": True}}),))
        view = chats.get_thread(1, self.pk)
        wake = store.start_turn(1, self.pk, "Background results", view["revision"], "wake", auto=True)
        self.assertIsNone(recaps.claim(self.job()["id"]))
        store.finish_run(wake["run"]["id"], {"content": "Merged findings; token tests pass."}, "completed")
        self.assertIsNone(recaps.claim(self.job()["id"]))
        self.age()
        job = recaps.claim(self.job()["id"])
        self.assertIn("Fix token validation", job["source"])
        self.assertIn("Merged findings", job["source"])

    def test_stopped_thread_can_recap_only_after_children_and_commands_finish(self):
        self.complete("cancelled")
        self.child()
        self.command()
        self.age()
        self.assertIsNone(recaps.claim(self.job()["id"]))
        db.execute("UPDATE chat_runs SET status='cancelled' WHERE id='child'")
        db.execute("UPDATE command_jobs SET status='cancelled' WHERE id='cmd'")
        self.age()
        self.assertIsNotNone(recaps.claim(self.job()["id"]))

    def test_lease_deduplication_recovery_and_late_result_guard(self):
        self.complete()
        self.age()
        first = recaps.claim(self.job()["id"])
        self.assertIsNotNone(first)
        self.assertIsNone(recaps.claim(first["id"]))
        db.execute("UPDATE chat_recaps SET next_at='' WHERE id=?", (first["id"],))
        retry = recaps.claim(first["id"])
        self.assertNotEqual(first["token"], retry["token"])
        recaps.finish(first, "Old output must not publish.")
        self.assertEqual(self.job()["state"], "generating")
        view = chats.get_thread(1, self.pk)
        store.start_turn(1, self.pk, "Now review cookies", view["revision"], "second")
        recaps.finish(retry, "Late output must not publish.")
        self.assertEqual(chats.get_thread(1, self.pk)["recaps"], [])

    def test_toggle_disables_generation_but_manual_still_runs(self):
        self.complete()
        job = recaps.claim(self.job()["id"])
        chats.set_user_context(1, recaps=False, expected_revision=0, command_id="off")
        recaps.finish(job, "This must be discarded.")
        self.assertEqual(chats.get_thread(1, self.pk)["recaps"], [])
        recaps.request_manual(1, self.pk)
        self.assertIsNotNone(recaps.claim(self.job()["id"]))

    def test_manual_refused_while_background_work_runs(self):
        self.complete()
        self.command()
        with self.assertRaises(chats.ChatValidationError):
            recaps.request_manual(1, self.pk)

    async def test_worker_publishes_display_metadata_without_changing_history(self):
        self.complete()
        before = db.fetchone("SELECT messages FROM chat_threads WHERE id=?", (self.pk,))[0]
        self.age()
        with patch("app.chat_runtime.generate_recap", new_callable=AsyncMock, return_value="Fixed token validation; tests passed.") as gen:
            await recaps.sweep()
            await asyncio.gather(*list(recaps._tasks.values()))
        gen.assert_awaited_once()
        self.assertEqual(db.fetchone("SELECT messages FROM chat_threads WHERE id=?", (self.pk,))[0], before)
        view = chats.get_thread(1, self.pk)
        self.assertEqual(view["recaps"][0]["text"], "Fixed token validation; tests passed.")
        self.assertEqual(store.events_after(1, 0)[-1]["type"], "recap.updated")
        chats.set_user_context(1, recaps=False, expected_revision=0, command_id="off")
        self.assertEqual(chats.get_thread(1, self.pk)["recaps"], view["recaps"])

    def test_failed_generation_backoff_and_attempt_limit(self):
        self.complete()
        self.age()
        job = recaps.claim(self.job()["id"])
        recaps.finish(job, "")
        self.assertEqual(self.job()["state"], "pending")
        self.assertIsNone(recaps.claim(job["id"]))
        for _ in range(2):
            self.age()
            job = recaps.claim(job["id"])
            recaps.finish(job, "")
        self.assertEqual(self.job()["state"], "failed")
        self.assertIsNone(recaps.claim(job["id"]))

    def test_history_rewrite_and_deletion_remove_metadata(self):
        self.complete()
        self.age()
        job = recaps.claim(self.job()["id"])
        recaps.finish(job, "Fixed token validation.")
        view = chats.get_thread(1, self.pk)
        chats.update_thread(1, self.pk, messages=[], expected_revision=view["revision"], command_id="rewind")
        self.assertEqual(chats.get_thread(1, self.pk)["recaps"], [])
        view = chats.get_thread(1, self.pk)
        chats.delete_thread(1, self.pk, expected_revision=view["revision"], command_id="delete")
        self.assertFalse(db.fetchone("SELECT id FROM chat_recaps WHERE thread_id=?", (self.pk,)))

    def test_command_finish_makes_recap_due_now(self):
        from app import command_jobs
        self.complete()
        self.command()
        self.assertIsNone(recaps.claim(self.job()["id"]))
        command_jobs._persist_finish({"id": "cmd", "user_id": 1, "thread_id": self.pk, "out": "passed"},
                                     status="completed", exit_code=0, error="", duration=100)
        self.assertIsNotNone(recaps.claim(self.job()["id"]))

    def test_resume_invalidates_ready_recap_until_child_settles(self):
        self.complete()
        self.age()
        job = recaps.claim(self.job()["id"])
        recaps.finish(job, "Fixed token validation.")
        self.child()
        with store.transaction() as conn:
            store.event(conn, 1, "agent.resumed", self.pk, {"run_id": "child"})
        self.assertEqual(chats.get_thread(1, self.pk)["recaps"], [])
        self.assertIsNone(recaps.claim(self.job()["id"]))
        store.finish_run("child", {"content": "Cookie follow-up finished."}, "completed")
        self.age()
        self.assertIsNotNone(recaps.claim(self.job()["id"]))

    def test_new_user_message_removes_recap_from_fresh_snapshots(self):
        self.complete()
        self.age()
        job = recaps.claim(self.job()["id"])
        recaps.finish(job, "Fixed token validation.")
        before = chats.get_thread(1, self.pk)
        self.assertEqual(len(before["recaps"]), 1)
        next_turn = store.start_turn(1, self.pk, "Please review cookies", before["revision"], "follow-up")
        self.assertEqual(next_turn["thread"]["recaps"], [])
        self.assertEqual(chats.get_thread(1, self.pk)["recaps"], [])
        self.assertEqual(db.fetchone("SELECT state FROM chat_recaps WHERE id=?", (job["id"],))[0], "stale")
        store.finish_run(next_turn["run"]["id"], {"content": "Reviewed cookie settings. " + "Details. " * 100,
                                                   "elapsedMs": 90_000}, "completed")
        self.age()
        next_job = recaps.claim(self.job()["id"])
        recaps.finish(next_job, "Reviewed cookie settings.")
        self.assertEqual(chats.get_thread(1, self.pk)["recaps"], [
            {"anchor_id": next_turn["run"]["message_id"], "text": "Reviewed cookie settings."},
        ])

    async def test_parallel_workers_can_claim_only_once(self):
        self.complete()
        self.age()
        job_id = self.job()["id"]
        results = await asyncio.gather(asyncio.to_thread(recaps.claim, job_id), asyncio.to_thread(recaps.claim, job_id))
        self.assertEqual(sum(result is not None for result in results), 1)

    async def test_disabling_cancels_inflight_request(self):
        self.complete()
        self.age()
        started, cancelled = asyncio.Event(), asyncio.Event()
        async def generate(*args):
            started.set()
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                cancelled.set()
                raise
        with patch("app.chat_runtime.generate_recap", side_effect=generate):
            task = asyncio.create_task(recaps.loop())
            try:
                await asyncio.wait_for(started.wait(), 10)
                chats.set_user_context(1, recaps=False, expected_revision=0, command_id="off")
                recaps.kick()
                await asyncio.wait_for(cancelled.wait(), 10)
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        self.assertEqual(self.job()["state"], "disabled")

    def test_account_isolation_and_preferences_revision(self):
        with self.assertRaises(chats.ChatValidationError):
            recaps.request_manual(2, self.pk)
        self.assertIsNone(chats.get_thread(2, self.pk))
        first = chats.set_user_context(1, recaps=False, expected_revision=0, command_id="settings")
        again = chats.set_user_context(1, recaps=False, expected_revision=0, command_id="settings")
        self.assertEqual(first, again)
        with self.assertRaises(chats.ChatValidationError):
            chats.set_user_context(1, recaps=True, expected_revision=0, command_id="stale")

    async def test_snapshots_preserve_metadata_in_pack_and_ndjson(self):
        from app.web.chat import _thread_stream
        self.complete()
        self.age()
        job = recaps.claim(self.job()["id"])
        recaps.finish(job, "Fixed token validation.")
        view = chats.get_thread(1, self.pk)
        view["messages"] = chats.pack_segment_text(view["messages"])
        response = _thread_stream(view)
        packed = json.loads(response.body)
        self.assertEqual(packed["recaps"], view["recaps"])
        long = {**view, "messages": view["messages"] * 10}
        response = _thread_stream(long)
        lines = [json.loads(line) async for line in response.body_iterator]
        self.assertTrue(lines[0]["partial"])
        self.assertEqual(lines[0]["recaps"], lines[1]["recaps"])


class SubstantialTest(unittest.TestCase):
    def test_short_quick_exchange_is_not_recapped(self):
        msgs = [{"id": "u", "role": "user", "content": "test"},
                {"id": "a", "role": "assistant", "content": "Hello! How can I help?", "elapsedMs": 3_000}]
        self.assertFalse(recaps.substantial(msgs, "u"))

    def test_long_context_or_long_work_is_recapped(self):
        msgs = [{"id": "old", "role": "user", "content": "y" * recaps.MIN_CONTEXT_CHARS},
                {"id": "u", "role": "user", "content": "hi"}, {"id": "a", "role": "assistant", "content": "ok"}]
        self.assertTrue(recaps.substantial(msgs, "u"))
        msgs = [{"id": "u", "role": "user", "content": "run it"},
                {"id": "a", "role": "assistant", "content": "done", "elapsedMs": recaps.MIN_WORK_SECONDS * 1000}]
        self.assertTrue(recaps.substantial(msgs, "u"))

    def test_work_time_counts_the_whole_session(self):
        third = recaps.MIN_WORK_SECONDS * 1000 // 3 + 1
        msgs = []
        for n in range(3):
            msgs += [{"id": f"u{n}", "role": "user", "content": "go"},
                     {"id": f"a{n}", "role": "assistant", "content": "ok", "elapsedMs": third}]
        self.assertFalse(recaps.substantial(msgs[-2:], "u2"))
        self.assertTrue(recaps.substantial(msgs, "u2"))

    def test_episode_source_is_framed_and_labelled(self):
        msgs = [{"id": "u", "role": "user", "content": "Fix tokens"}, {"id": "a", "role": "assistant", "content": "Fixed tokens."}]
        anchor, source = recaps.episode(msgs, "u")
        self.assertEqual(anchor, "a")
        self.assertTrue(source.startswith("<episode>\nRequest: Fix tokens"))
        self.assertIn("Work output: Fixed tokens.", source)
        self.assertNotIn("assistant:", source)

    def test_recap_parser_strips_markdown_instead_of_rejecting(self):
        self.assertEqual(chats.parse_generated_recap("Recap: Fixed `auth.py` **token** check."), "Fixed auth.py token check.")
        self.assertEqual(chats.parse_generated_recap("# Fixed token validation."), "Fixed token validation.")
        self.assertEqual(chats.parse_generated_recap("Sorry, I cannot summarize this."), "")
