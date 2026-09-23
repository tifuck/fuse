"""Real HTTP/WebSocket protocol with isolated SQLite and a mocked model runtime."""
import asyncio
import base64
import json
import sqlite3
import tempfile
import unittest
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient
from itsdangerous import TimestampSigner
from starlette.middleware.sessions import SessionMiddleware
from starlette.websockets import WebSocketDisconnect

from app import chat_events, chat_runs, chat_runtime, chat_store, chats, db, web


SECRET = "offline-sync-tests-only"


class ChatSyncTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="fuse-sync-")
        self.addCleanup(self.tmp.cleanup)
        self.conn = sqlite3.connect(str(Path(self.tmp.name) / "test.db"), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
        db._init(self.conn)
        self.addCleanup(self.conn.close)
        p = patch.object(db, "connect", return_value=self.conn)
        p.start()
        self.addCleanup(p.stop)
        title = patch.object(chat_runtime, "generate_thread_title", new_callable=AsyncMock, return_value="")
        title.start()
        self.addCleanup(title.stop)
        for uid in (1, 2):
            db.execute("INSERT INTO users(id,email,password_hash,created_at) VALUES(?,?,?,?)",
                       (uid, f"{uid}@sync.invalid", "unused", db.now()))

        @asynccontextmanager
        async def lifespan(app):
            chat_runs._stopping = False
            chat_events.start()
            yield
            await chat_events.stop()
            await chat_runs.stop()

        app = FastAPI(lifespan=lifespan)
        app.add_middleware(SessionMiddleware, secret_key=SECRET, https_only=True)
        app.include_router(web.router)
        app.include_router(chat_events.router)
        self.client = TestClient(app, base_url="https://testserver")
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        self.login(1)

    def cookie(self, uid):
        data = base64.b64encode(json.dumps({"uid": uid, "csrf": "test-csrf"}).encode())
        return TimestampSigner(SECRET).sign(data).decode()

    def login(self, uid):
        self.client.cookies.set("session", self.cookie(uid))
        self.client.headers["x-csrf-token"] = "test-csrf"

    def socket(self, cursor=0, uid=1, origin="https://testserver"):
        return self.client.websocket_connect(f"wss://testserver/chat/events?cursor={cursor}",
                                            headers={"origin": origin, "cookie": "session=" + self.cookie(uid)})

    def create(self, command="create"):
        response = self.client.post("/chat/threads", json={"model": "offline", "tools": [], "command_id": command})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def until(self, ws, event_type):
        for _ in range(30):
            data = ws.receive_json()
            if data["type"] == "ping":
                ws.send_text("pong")
            for event in data.get("events", []):
                if event["type"] == event_type:
                    return event
        self.fail("Expected event " + event_type)

    def test_snapshot_cursor_skips_projection_but_checks_ownership(self):
        thread = self.create()
        url = f"/chat/threads/{thread['id']}?observed_cursor={thread['event_cursor']}"
        with patch.object(chat_store, "thread_view", side_effect=AssertionError("expensive projection")):
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json(), {"unchanged": True, "event_cursor": thread["event_cursor"]})
            self.login(2)
            self.assertEqual(self.client.get(url).status_code, 404)

    def test_progress_cursor_invalidates_snapshot_without_revision_change(self):
        thread = self.create()
        pk = chats.resolve_thread_pk(1, thread["id"])
        with chat_store.transaction() as conn:
            chat_store.event(conn, 1, "run.progress", pk, {"run_id": "offline"})
        response = self.client.get(f"/chat/threads/{thread['id']}?observed_cursor={thread['event_cursor']}")
        self.assertEqual(response.status_code, 200)
        fresh = response.json()
        self.assertIn("messages", fresh)
        self.assertEqual(fresh["revision"], thread["revision"])
        self.assertGreater(fresh["event_cursor"], thread["event_cursor"])
        for value in ("-1", "nope", str(2**53)):
            self.assertEqual(self.client.get(f"/chat/threads/{thread['id']}?observed_cursor={value}").status_code, 422)

    def test_two_observers_create_revision_delete_and_replay(self):
        with self.socket() as desktop, self.socket() as phone:
            self.assertEqual(desktop.receive_json()["type"], "ready")
            self.assertEqual(phone.receive_json()["type"], "ready")
            thread = self.create()
            a = self.until(desktop, "thread.updated")
            b = self.until(phone, "thread.updated")
            self.assertEqual(a, b)
            self.assertEqual(a["thread_id"], thread["id"])
            snapshot = self.client.get("/chat/sync").json()
            self.assertEqual(snapshot["threads"][0]["id"], thread["id"])
            self.assertEqual(snapshot["threads"][0]["kind"], "chat")
            self.assertIn("host_alias", snapshot["threads"][0])
            self.assertFalse(snapshot["threads"][0]["running"])
            snapshot = self.client.get(f"/chat/threads/{thread['id']}").json()
            self.assertEqual(snapshot["event_cursor"], a["id"])
            stale = {"title": "first", "expected_revision": thread["revision"]}
            self.assertEqual(self.client.patch(f"/chat/threads/{thread['id']}", json=stale).status_code, 200)
            self.assertEqual(self.client.patch(f"/chat/threads/{thread['id']}", json=stale).status_code, 409)
            self.assertEqual(self.client.patch(f"/chat/threads/{thread['id']}", json={"messages": []}).status_code, 409)
        with self.socket(a["id"]) as resumed:
            update = self.until(resumed, "thread.updated")
            self.assertEqual(update["payload"]["title"], "first")
            response = self.client.request("DELETE", f"/chat/threads/{thread['id']}", json={"expected_revision": 1})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(self.until(resumed, "thread.deleted")["thread_id"], thread["id"])
            self.assertEqual(self.client.get(f"/chat/threads/{thread['id']}").status_code, 404)

    def test_auth_origin_csrf_tenant_and_retention(self):
        thread = self.create()
        with self.assertRaises(WebSocketDisconnect):
            with self.socket(origin="https://evil.invalid"):
                pass
        with self.assertRaises(WebSocketDisconnect):
            with self.client.websocket_connect("wss://testserver/chat/events", headers={"origin": "https://testserver", "cookie": "session=invalid"}):
                pass
        with self.socket(uid=2) as other:
            self.assertEqual(other.receive_json(), {"type": "ready", "cursor": 0})
        self.login(2)
        self.assertEqual(self.client.get(f"/chat/threads/{thread['id']}").status_code, 404)
        self.assertEqual(self.client.get("/chat/sync").json()["threads"], [])
        self.client.headers.pop("x-csrf-token")
        self.assertEqual(self.client.post("/chat/threads", json={"command_id": "denied"}).status_code, 403)
        db.execute("UPDATE chat_events SET created_at='2000-01-01T00:00:00Z'")
        chat_store.prune_events()
        with self.socket() as stale:
            self.assertEqual(stale.receive_json()["type"], "resync_required")

    def test_run_survives_disconnect_and_cancel_is_shared(self):
        async def mocked(request, user, raw, **kwargs):
            async def gen():
                yield b'data: {"choices":[{"delta":{"content":"Hello"}}]}\n\n'
                await asyncio.sleep(60)
            return StreamingResponse(gen())
        with patch.object(chat_runtime, "stream_dashboard_chat", side_effect=mocked) as model:
            thread = self.create()
            body = {"text": "hello", "expected_revision": thread["revision"], "command_id": "turn"}
            response = self.client.post(f"/chat/threads/{thread['id']}/turns", json=body)
            self.assertEqual(response.status_code, 200, response.text)
            run = response.json()["run"]
            with self.socket(thread["event_cursor"]) as origin:
                event = self.until(origin, "run.progress")
            self.assertIn(chat_store.get_run(1, run["id"])["status"], chat_store.ACTIVE)
            with self.socket(event["id"]) as remote:
                data = self.client.get(f"/chat/threads/{thread['id']}").json()
                self.assertEqual(data["run"]["id"], run["id"])
                self.assertTrue(data["running"])
                listed = self.client.get("/chat/threads").json()["threads"]
                self.assertTrue(any(item["id"] == thread["id"] and item["running"] for item in listed))
                duplicate = self.client.post(f"/chat/threads/{thread['id']}/turns", json=body)
                self.assertTrue(duplicate.json()["idempotent"])
                self.assertEqual(model.call_count, 1)
                self.login(2)
                self.assertEqual(self.client.post(f"/chat/runs/{run['id']}/cancel").status_code, 404)
                self.login(1)
                self.assertEqual(self.client.post(f"/chat/runs/{run['id']}/cancel").status_code, 200)
                terminal = self.until(remote, "run.cancelled")
                self.assertEqual(terminal["payload"]["message"]["content"], "Hello")
                done = self.client.get(f"/chat/threads/{thread['id']}").json()
                self.assertIsNone(done["run"])
                self.assertFalse(done["running"])
                listed = self.client.get("/chat/threads").json()["threads"]
                self.assertTrue(any(item["id"] == thread["id"] and not item["running"] for item in listed))

    def test_compact_endpoint_and_thread_meter(self):
        thread = self.create("compact-thread")
        got = self.client.get(f"/chat/threads/{thread['id']}").json()
        self.assertIn("compaction", got)
        self.assertIn("meter", got["compaction"])
        self.assertGreater(got["compaction"]["meter"]["limit"], 0)
        short = self.client.post(
            f"/chat/threads/{thread['id']}/compact",
            json={"expected_revision": thread["revision"], "command_id": "compact-short", "strategy": "truncate"},
        )
        self.assertEqual(short.status_code, 200, short.text)
        self.assertFalse(short.json()["compacted"])
        messages = []
        for i in range(8):
            messages.append({"role": "user", "content": f"ask {i} " + "word " * 20})
            messages.append({"role": "assistant", "content": f"ans {i} " + "word " * 20})
        filled = self.client.patch(
            f"/chat/threads/{thread['id']}",
            json={"messages": messages, "expected_revision": thread["revision"], "command_id": "fill-compact"},
        )
        self.assertEqual(filled.status_code, 200, filled.text)
        thread = filled.json()
        compacted = self.client.post(
            f"/chat/threads/{thread['id']}/compact",
            json={"expected_revision": thread["revision"], "command_id": "compact-ok", "strategy": "truncate"},
        )
        self.assertEqual(compacted.status_code, 200, compacted.text)
        data = compacted.json()
        self.assertTrue(data["compacted"])
        self.assertTrue(data["thread"]["compaction"]["checkpoints"])
        replay = self.client.post(
            f"/chat/threads/{thread['id']}/compact",
            json={"expected_revision": thread["revision"], "command_id": "compact-ok", "strategy": "truncate"},
        )
        self.assertEqual(replay.status_code, 200)
        self.assertTrue(replay.json().get("compacted"))
        self.assertTrue(replay.json().get("idempotent"))

    def test_context_conflicts_and_logout_closes_stream(self):
        with self.socket() as ws:
            self.assertEqual(ws.receive_json()["type"], "ready")
            body = {"profile": "shared", "expected_revision": 0}
            self.assertEqual(self.client.put("/chat/context", json=body).status_code, 200)
            self.assertEqual(self.until(ws, "context.updated")["payload"]["profile"], "shared")
            self.assertEqual(self.client.put("/chat/context", json=body).status_code, 409)
            self.client.post("/logout", follow_redirects=False)
            with self.assertRaises(WebSocketDisconnect):
                ws.receive_json()


if __name__ == "__main__":
    unittest.main()
