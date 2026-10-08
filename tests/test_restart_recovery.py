"""Restart recovery, steer queuing, and resume-loop guards."""
import asyncio
import base64
import json
import sqlite3
import sys
import tempfile
import unittest
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient
from itsdangerous import TimestampSigner
from starlette.middleware.sessions import SessionMiddleware
from app import chat_events, chat_runs as runs, chat_runtime as runtime, chat_store as store, chats, db
from app.web import router as web_router

USER = {"id": 1}
SECRET = "offline-restart-tests-only"


def frame(obj):
    return ("data: " + json.dumps(obj, ensure_ascii=False) + "\r\n\r\n").encode()


class RestartRecoveryTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="fuse-restart-")
        self.addCleanup(self.tmp.cleanup)
        self.conn = sqlite3.connect(str(Path(self.tmp.name) / "test.db"), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        db._init(self.conn)
        self.addCleanup(self.conn.close)
        patcher = patch.object(db, "connect", return_value=self.conn)
        patcher.start()
        self.addCleanup(patcher.stop)
        db.execute("INSERT INTO users(id,email,password_hash,created_at) VALUES(1,'t@invalid','x',?)", (db.now(),))
        store.init_schema()
        runs._stopping = False
        title = patch.object(runtime, "generate_thread_title", new_callable=AsyncMock, return_value="")
        title.start()
        self.addCleanup(title.stop)

    async def asyncTearDown(self):
        await runs.stop()
        runs._stopping = False

    def make_thread(self):
        chats.set_user_context(1, instructions="", profile="")
        return chats.create_thread(1, model="offline", tools=[])

    def start_run(self, thread):
        return store.start_turn(1, thread["id"], "Hello", thread["revision"], None)["run"]

    def save_round(self, run_id):
        store.save_turn_progress(
            run_id,
            [
                {"role": "user", "content": "Hello"},
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {"id": "c1", "type": "function", "function": {"name": "web_search", "arguments": "{}"}}
                    ],
                },
                {"role": "tool", "tool_call_id": "c1", "content": "hits"},
            ],
        )

    # -- resume note idempotency -------------------------------------------

    def test_resume_note_appends_once_and_never_stacks(self):
        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "", "tool_calls": [
                {"id": "c1", "type": "function", "function": {"name": "web_search", "arguments": "{}"}}
            ]},
            {"role": "tool", "tool_call_id": "c1", "content": "hits"},
        ]
        once = store._with_resume_note(messages, "r1", 1)
        self.assertEqual(len(once), len(messages) + 1)
        self.assertIn("resume-note:r1:1", once[-1]["content"])
        # Same generation re-run (double recovery): unchanged.
        again = store._with_resume_note(once, "r1", 1)
        self.assertEqual(again, once)
        # Back-to-back restart with no model progress: unchanged.
        stacked = store._with_resume_note(once, "r1", 2)
        self.assertEqual(stacked, once)
        # After real model progress, a new generation may note once more.
        progressed = [*once, {"role": "assistant", "content": "partial"}]
        noted = store._with_resume_note(progressed, "r1", 2)
        self.assertEqual(len(noted), len(progressed) + 1)
        self.assertIn("resume-note:r1:2", noted[-1]["content"])

    def test_resume_count_increments_and_caps_recovery(self):
        thread = self.make_thread()
        run = self.start_run(thread)
        self.save_round(run["id"])
        db.execute("UPDATE chat_runs SET resume_count=3 WHERE id=?", (run["id"],))
        store.recover_runs()
        recovered = store.get_run(1, run["id"])
        self.assertEqual(recovered["status"], "interrupted")
        self.assertIn("restart", str(recovered["message"].get("notice") or "").lower())

    def test_recover_runs_boot_batch_cap(self):
        ids = []
        for uid in range(2, 12):
            db.execute(
                "INSERT INTO users(id,email,password_hash,created_at) VALUES(?,?,'x',?)",
                (uid, f"u{uid}@invalid", db.now()),
            )
            chats.set_user_context(uid, instructions="", profile="")
            thread = chats.create_thread(uid, model="offline", tools=[])
            run = store.start_turn(uid, thread["id"], "Hello", thread["revision"], None)["run"]
            self.save_round(run["id"])
            ids.append((uid, run["id"]))
        store.recover_runs()
        resumed = [
            rid for uid, rid in ids
            if (store.get_run(uid, rid)["request"].get("agent") or {}).get("resumed")
        ]
        self.assertEqual(len(resumed), store.BOOT_RESUME_BATCH)
        untouched = [rid for uid, rid in ids if rid not in resumed]
        for uid, rid in [(uid, rid) for uid, rid in ids if rid in untouched]:
            run = store.get_run(uid, rid)
            self.assertIn(run["status"], ("queued", "running"))
            self.assertNotIn("resumed", run["request"].get("agent") or {})

    def test_requeue_bumps_resume_count(self):
        thread = self.make_thread()
        run = self.start_run(thread)
        self.save_round(run["id"])
        store.recover_runs()
        first = store.get_run(1, run["id"])
        self.assertEqual(first["status"], "queued")
        self.assertEqual(first["request"]["agent"].get("resumed"), True)
        row = db.fetchone("SELECT resume_count FROM chat_runs WHERE id=?", (run["id"],))
        self.assertEqual(int(row["resume_count"]), 1)

    # -- graceful shutdown salvage ------------------------------------------

    async def test_graceful_stop_leaves_transcript_runs_active(self):
        thread = self.make_thread()
        run = self.start_run(thread)

        async def gen():
            yield frame({"fuse": {"event": "tool", "id": "t1", "name": "web_search", "status": "running", "query": "q"}})
            await asyncio.Future()

        async def mocked(request, user, raw, *, frozen_context):
            return StreamingResponse(gen())

        with patch.object(runtime, "stream_dashboard_chat", side_effect=mocked):
            task = await runs.start(runs.internal_request(1), dict(USER), dict(run))
            await asyncio.sleep(0.2)
            self.save_round(run["id"])
            runs._stopping = True
            task.cancel()
            await task
        row = db.fetchone("SELECT status,cancel_requested FROM chat_runs WHERE id=?", (run["id"],))
        self.assertEqual(row["status"], "running")
        self.assertEqual(int(row["cancel_requested"]), 0)

    async def test_graceful_stop_still_finalizes_runs_without_transcript(self):
        thread = self.make_thread()
        run = self.start_run(thread)

        async def gen():
            yield frame({"choices": [{"delta": {"content": "par"}}]})
            await asyncio.Future()

        async def mocked(request, user, raw, *, frozen_context):
            return StreamingResponse(gen())

        with patch.object(runtime, "stream_dashboard_chat", side_effect=mocked):
            task = await runs.start(runs.internal_request(1), dict(USER), dict(run))
            await asyncio.sleep(0.2)
            runs._stopping = True
            task.cancel()
            await task
        row = db.fetchone("SELECT status FROM chat_runs WHERE id=?", (run["id"],))
        self.assertEqual(row["status"], "interrupted")

    # -- steers ---------------------------------------------------------------

    def test_queue_steer_dedupes_and_drains(self):
        thread = self.make_thread()
        run = self.start_run(thread)
        self.assertTrue(store.queue_steer(run["id"], "s1", "steer one"))
        self.assertTrue(store.queue_steer(run["id"], "s1", "steer one"))
        self.assertTrue(store.queue_steer(run["id"], "s2", "steer two"))
        items = store.take_steers(run["id"])
        self.assertEqual([i["id"] for i in items], ["s1", "s2"])
        # Peek is non-destructive: nothing is acknowledged until the injected
        # envelope is durable in the provider transcript.
        self.assertEqual([i["id"] for i in store.take_steers(run["id"])], ["s1", "s2"])
        store.save_turn_progress(
            run["id"],
            [{"role": "user", "content": store.steer_envelope("s1", "steer one")}],
        )
        self.assertEqual([i["id"] for i in store.take_steers(run["id"])], ["s2"])
        # A delivered steer is an idempotent no-op on retry, never a duplicate.
        self.assertTrue(store.queue_steer(run["id"], "s1", "steer one"))
        self.assertEqual([i["id"] for i in store.take_steers(run["id"])], ["s2"])
        # Overflow is rejected, never silently dropped.
        for i in range(7):
            store.queue_steer(run["id"], f"o{i}", "filler")
        with self.assertRaises(chats.ChatValidationError) as ctx:
            store.queue_steer(run["id"], "overflow", "nope")
        self.assertEqual(ctx.exception.status_code, 429)
        # A queued steer on a terminal run is refused (fallback: new turn).
        db.execute("UPDATE chat_runs SET status='completed' WHERE id=?", (run["id"],))
        self.assertFalse(store.queue_steer(run["id"], "s3", "late"))

    def test_steer_turn_is_idempotent(self):
        thread = self.make_thread()
        run = self.start_run(thread)
        first = store.steer_turn(1, thread["id"], "go faster", "cmd-1")
        self.assertFalse(first["idempotent"])
        again = store.steer_turn(1, thread["id"], "go faster", "cmd-1")
        self.assertTrue(again["idempotent"])
        data = chats.get_thread(1, thread["id"])
        user_msgs = [m for m in data["messages"] if m["role"] == "user"]
        self.assertEqual(len(user_msgs), 2)  # original turn + one steer
        self.assertEqual(data["revision"], thread["revision"] + 2)  # turn + steer

    # -- user-initiated resume -------------------------------------------------

    def test_resume_run_from_interrupted_root(self):
        thread = self.make_thread()
        run = self.start_run(thread)
        self.save_round(run["id"])
        db.execute("UPDATE chat_runs SET status='interrupted' WHERE id=?", (run["id"],))
        resumed = store.resume_run(1, run["id"])
        self.assertEqual(resumed["status"], "queued")
        self.assertEqual(resumed["request"]["agent"].get("resumed"), True)

    def test_continue_after_user_stop_does_not_claim_a_restart(self):
        thread = self.make_thread()
        run = self.start_run(thread)
        store.save_turn_progress(run["id"], [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "", "tool_calls": [
                {"id": "c1", "type": "function", "function": {"name": "web_search", "arguments": "{}"}}]},
        ])
        db.execute("UPDATE chat_runs SET status='cancelled' WHERE id=?", (run["id"],))
        resumed = store.resume_run(1, run["id"])
        sent = resumed["request"]["messages"]
        note = sent[-1]["content"]
        self.assertTrue(note.startswith(store.USER_RESUME_NOTE_HEAD))
        self.assertNotIn("restart", note.lower())
        self.assertIn("user stopped", sent[-2]["content"], "the unfinished call is sealed with the real cause")
        self.assertEqual(resumed["request"]["agent"]["resume_reason"], "user")
        self.assertIn(store.USER_RESUME_NOTICE, resumed["message"]["notice"])
        self.assertNotIn("server restart", resumed["message"]["notice"])
        # The guard against stacking notes also recognises the user variant.
        self.assertIs(store._with_resume_note(sent, run["id"], 9, True), sent)

    def test_resume_run_blocked_while_thread_busy(self):
        thread = self.make_thread()
        run = self.start_run(thread)
        self.save_round(run["id"])
        db.execute("UPDATE chat_runs SET status='interrupted' WHERE id=?", (run["id"],))
        other = store.start_turn(1, thread["id"], "Second", chats.get_thread(1, thread["id"])["revision"], None)["run"]
        with self.assertRaises(chats.ChatValidationError) as ctx:
            store.resume_run(1, run["id"])
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertEqual(store.get_run(1, other["id"])["status"], "queued")

    def test_resume_run_requires_transcript(self):
        thread = self.make_thread()
        run = self.start_run(thread)
        db.execute("UPDATE chat_runs SET status='interrupted' WHERE id=?", (run["id"],))
        with self.assertRaises(chats.ChatValidationError) as ctx:
            store.resume_run(1, run["id"])
        self.assertEqual(ctx.exception.status_code, 409)

    # -- stuck stopping ---------------------------------------------------------

    def test_reaper_force_aborts_wedged_cancel(self):
        import datetime

        thread = self.make_thread()
        run = self.start_run(thread)
        old = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=60)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        db.execute(
            "UPDATE chat_runs SET cancel_requested=1, cancel_requested_at=?, updated_at=? WHERE id=?",
            (old, db.now(), run["id"]),
        )
        row = db.fetchone("SELECT * FROM chat_runs WHERE id=?", (run["id"],))
        runs._reap_one(row)
        self.assertEqual(store.get_run(1, run["id"])["status"], "cancelled")


class SteerEndpointTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="fuse-steer-")
        self.addCleanup(self.tmp.cleanup)
        self.conn = sqlite3.connect(str(Path(self.tmp.name) / "test.db"), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        db._init(self.conn)
        self.addCleanup(self.conn.close)
        p = patch.object(db, "connect", return_value=self.conn)
        p.start()
        self.addCleanup(p.stop)
        db.execute("INSERT INTO users(id,email,password_hash,created_at) VALUES(1,'t@invalid','x',?)", (db.now(),))
        store.init_schema()
        runs._stopping = False

        @asynccontextmanager
        async def lifespan(app):
            runs._stopping = False
            chat_events.start()
            yield
            await chat_events.stop()
            await runs.stop()

        app = FastAPI(lifespan=lifespan)
        app.add_middleware(SessionMiddleware, secret_key=SECRET, https_only=True)
        app.include_router(web_router)
        app.include_router(chat_events.router)
        self.client = TestClient(app, base_url="https://testserver")
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        data = base64.b64encode(json.dumps({"uid": 1, "csrf": "test-csrf"}).encode())
        self.client.cookies.set(
            "session", TimestampSigner(SECRET).sign(data).decode()
        )
        self.client.headers["x-csrf-token"] = "test-csrf"
        chats.set_user_context(1, instructions="", profile="")
        self.thread = chats.create_thread(1, model="offline", tools=[])

    def test_steer_requires_live_run(self):
        response = self.client.post(
            f"/chat/threads/{self.thread['id']}/steer",
            json={"text": "hello", "command_id": "cmd-no-run"},
        )
        self.assertEqual(response.status_code, 409)
        self.assertIn("No active run", response.text)
        # Nothing was appended to the conversation.
        self.assertEqual(chats.get_thread(1, self.thread["id"])["messages"], [])

    def test_steer_appends_message_and_injects(self):
        run = store.start_turn(1, self.thread["id"], "Hello", self.thread["revision"], None)["run"]
        response = self.client.post(
            f"/chat/threads/{self.thread['id']}/steer",
            json={"text": "focus on the API", "command_id": "cmd-1"},
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["live"])
        self.assertTrue(body["injected"])
        row = db.fetchone("SELECT request FROM chat_runs WHERE id=?", (run["id"],))
        pending = json.loads(row["request"])["agent"]["pending_steers"]
        self.assertEqual([item["id"] for item in pending], ["cmd-1"])
        # Idempotent retry: no duplicate thread message, no duplicate queue entry.
        again = self.client.post(
            f"/chat/threads/{self.thread['id']}/steer",
            json={"text": "focus on the API", "command_id": "cmd-1"},
        )
        self.assertEqual(again.status_code, 200)
        self.assertTrue(again.json()["idempotent"])
        row = db.fetchone("SELECT request FROM chat_runs WHERE id=?", (run["id"],))
        pending = json.loads(row["request"])["agent"]["pending_steers"]
        self.assertEqual(len(pending), 1)
        data = chats.get_thread(1, self.thread["id"])
        steers = [m for m in data["messages"] if m["role"] == "user" and m["content"] == "focus on the API"]
        self.assertEqual(len(steers), 1)

    def test_resume_endpoint_requeues_interrupted_run(self):
        run = store.start_turn(1, self.thread["id"], "Hello", self.thread["revision"], None)["run"]
        store.save_turn_progress(
            run["id"],
            [
                {"role": "user", "content": "Hello"},
                {"role": "assistant", "content": "", "tool_calls": [
                    {"id": "c1", "type": "function", "function": {"name": "web_search", "arguments": "{}"}}
                ]},
                {"role": "tool", "tool_call_id": "c1", "content": "hits"},
            ],
        )
        db.execute("UPDATE chat_runs SET status='interrupted' WHERE id=?", (run["id"],))
        response = self.client.post(f"/chat/runs/{run['id']}/resume")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["run"]["status"], "queued")

    def test_force_cancel_endpoint_finalizes_wedged_run(self):
        run = store.start_turn(1, self.thread["id"], "Hello", self.thread["revision"], None)["run"]
        db.execute("UPDATE chat_runs SET cancel_requested=1 WHERE id=?", (run["id"],))
        response = self.client.post(f"/chat/runs/{run['id']}/cancel?force=1")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["run"]["status"], "cancelled")


if __name__ == "__main__":
    unittest.main()
