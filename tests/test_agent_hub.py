from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
_TMP = tempfile.mkdtemp(prefix="fuse-hub-")
os.environ.setdefault("DATABASE_PATH", str(Path(_TMP) / "hub.db"))
os.environ.setdefault("APP_SECRET", "y" * 32)

from app import accounts, agent_hub, command_jobs, db, hosts, vault, workspace


class FakeWS:
    def __init__(self, host_id: int):
        self.host_id = host_id
        self.sent = []
        self.client_state = type("S", (), {"name": "CONNECTED"})()

    async def send_json(self, obj):
        self.sent.append(obj)
        if obj.get("t") == "job":
            rec = agent_hub._conns.get((1, self.host_id))
            fut = rec["pending"].get(obj["id"]) if rec else None
            if fut and not fut.done():
                if obj.get("op") == "browse":
                    out = '{"path":"/tmp","parent":"/","home":"/root","entries":[{"name":"proj","kind":"dir"}]}'
                elif obj.get("op") in ("exec", "exec_stream"):
                    out = "ok\n"
                    if obj.get("op") == "exec_stream":
                        rec = agent_hub._conns.get((1, self.host_id))
                        cb = rec.get("chunks", {}).get(obj["id"]) if rec else None
                        if cb:
                            cb("out", "ok\n")
                else:
                    out = (obj.get("content") and "wrote") or "hello"
                fut.set_result({"ok": True, "exit": 0, "out": out, "err": "", "error": ""})

    async def close(self, code=None):
        return None


class AgentHubTest(unittest.TestCase):
    def setUp(self):
        replay_patch = patch.dict(agent_hub._cancel_replay, clear=True)
        replay_patch.start()
        self.addCleanup(replay_patch.stop)
        schema_patch = patch.object(command_jobs, "_schema_ready", False)
        schema_patch.start()
        self.addCleanup(schema_patch.stop)
        live_patch = patch.dict(command_jobs._live, clear=True)
        live_patch.start()
        self.addCleanup(live_patch.stop)
        self.tmp = tempfile.TemporaryDirectory(prefix="fuse-hub-")
        self.addCleanup(self.tmp.cleanup)
        self.conn = sqlite3.connect(str(Path(self.tmp.name) / "test.db"), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
        db._init(self.conn)
        self.addCleanup(self.conn.close)
        self.connection_patch = patch.object(db, "connect", return_value=self.conn)
        self.connection_patch.start()
        self.addCleanup(self.connection_patch.stop)
        vault.lock_all()
        accounts.create_user("hub@test.invalid", "password12", admin=False)
        vault.lock_all()
        pairing = hosts.mint_pairing(1)
        self.host, self.token = hosts.redeem_pairing(pairing["code"], {"hostname": "hub", "username": "u"})
        self.hid = self.host["id"]
        agent_hub._conns.clear()
        agent_hub._held.clear()

    def tearDown(self):
        agent_hub._conns.clear()
        agent_hub._held.clear()

    def test_offline_call(self):
        with self.assertRaises(agent_hub.AgentError):
            asyncio.run(agent_hub.call(1, self.hid, "exec", {"cmd": "true"}, 1))

    def test_reconnect_preserves_stream_callback(self):
        async def go():
            key = (1, self.hid)
            future = asyncio.get_running_loop().create_future()
            received = []
            callback = lambda stream, text: received.append((stream, text))
            pending = {"job": future}
            await agent_hub._hold_pending(key, pending, {"job": callback})
            held = agent_hub._held[key]
            self.assertIs(held["chunks"]["job"], callback)
            live = {"pending": {}, "chunks": {}}
            agent_hub._conns[key] = live
            second = {"other": asyncio.get_running_loop().create_future()}
            await agent_hub._hold_pending(key, second, {"other": callback})
            live["chunks"]["other"]("out", "after reconnect")
            self.assertEqual(received, [("out", "after reconnect")])
            agent_hub._take_held(key)
        asyncio.run(go())

    def test_roundtrip_and_workspace(self):
        ws = FakeWS(self.hid)
        agent_hub._conns[(1, self.hid)] = {"ws": ws, "pending": {}, "user_id": 1, "host_id": self.hid}
        self.assertTrue(agent_hub.is_online(1, self.hid))

        async def go():
            data = await agent_hub.call(1, self.hid, "exec", {"cwd": "~", "cmd": "echo ok"}, 2)
            self.assertEqual(data["out"], "ok\n")
            text, rec = await workspace.execute(
                "run_terminal_command",
                json.dumps({"command": "echo ok"}),
                user_id=1,
                bound_host_id=self.hid,
                bound_cwd="/tmp",
            )
            self.assertEqual(rec["status"], "done")
            self.assertIn("ok", text)
            self.assertEqual(rec["host"], self.host["alias"])

            text, rec = await workspace.execute(
                "run_terminal_command",
                json.dumps({"command": "echo ok"}),
                user_id=1,
                bound_host_id=self.hid,
            )
            self.assertEqual(rec["status"], "done")
            exec_jobs = [m for m in ws.sent if m.get("t") == "job" and m.get("op") == "exec"]
            self.assertTrue(exec_jobs)
            self.assertNotIn("$HOME", exec_jobs[-1].get("cwd") or "")
            self.assertEqual(exec_jobs[-1].get("cwd"), "~")

            listing = await workspace.browse(1, self.hid, "/tmp")
            self.assertEqual(listing["path"], "/tmp")
            self.assertEqual(listing["entries"][0]["name"], "proj")
            self.assertEqual(listing["entries"][0]["kind"], "dir")

        asyncio.run(go())
        self.assertTrue(any(m.get("t") == "job" for m in ws.sent))

    def test_token_lookup(self):
        row = hosts.host_by_device_token(self.token)
        self.assertEqual(int(row["id"]), self.hid)

    def test_oversized_payload_rejected_before_send(self):
        ws = FakeWS(self.hid)
        agent_hub._conns[(1, self.hid)] = {"ws": ws, "pending": {}, "user_id": 1, "host_id": self.hid}

        async def go():
            # UTF-8 wire size, not Unicode character count, determines the cap.
            with self.assertRaises(agent_hub.AgentError) as ctx:
                await agent_hub.call(1, self.hid, "write", {"path": "/tmp/x", "content": "€" * 310_000}, 2)
            self.assertIn("payload too large", str(ctx.exception.detail))
            self.assertEqual(len(ws.sent), 0)
            rec = agent_hub._conns.get((1, self.hid))
            self.assertEqual(rec["pending"], {})

        asyncio.run(go())

    def test_websocket_hello_redeems(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from app.agent_http import router as agent_router

        pairing = hosts.mint_pairing(1)
        app = FastAPI()
        app.include_router(agent_router)
        client = TestClient(app)
        with client.websocket_connect("/agent/v1/connect") as ws:
            ws.send_json(
                {
                    "v": 1,
                    "t": "hello",
                    "code": pairing["code"],
                    "hostname": "wsbox",
                    "user": "ada",
                    "ver": "1.0.0",
                }
            )
            hello = ws.receive_json()
            self.assertEqual(hello["t"], "hello_ok")
            self.assertTrue(hello["token"].startswith("fa_"))
            hid = hello["host_id"]
            self.assertTrue(agent_hub.is_online(1, hid))
            row = hosts.host_by_device_token(hello["token"])
            self.assertEqual(int(row["id"]), hid)
            self.assertEqual(row["username"], "ada")
            self.assertFalse(hosts.list_pending_pairings(1))

    def test_pending_survives_socket_replace(self):
        loop = asyncio.new_event_loop()
        self.addCleanup(loop.close)

        async def go():
            first = FakeWS(self.hid)
            rec = {"ws": first, "pending": {}, "user_id": 1, "host_id": self.hid}
            agent_hub._conns[(1, self.hid)] = rec
            fut = loop.create_future()
            rec["pending"]["abc123abc123"] = fut
            second = FakeWS(self.hid)
            rec2 = {"ws": second, "pending": {}, "user_id": 1, "host_id": self.hid}
            rec2["pending"].update(rec["pending"])
            rec["pending"] = {}
            agent_hub._conns[(1, self.hid)] = rec2
            rec2["pending"]["abc123abc123"].set_result(
                {"ok": True, "exit": 0, "out": "ok", "err": "", "error": ""}
            )
            self.assertEqual((await fut)["out"], "ok")
            self.assertTrue(agent_hub.is_online(1, self.hid))

        loop.run_until_complete(go())

    def test_hold_pending_reattaches_on_hello(self):
        loop = asyncio.new_event_loop()
        self.addCleanup(loop.close)

        async def go():
            first = FakeWS(self.hid)
            rec = {"ws": first, "pending": {}, "user_id": 1, "host_id": self.hid}
            agent_hub._conns[(1, self.hid)] = rec
            fut = loop.create_future()
            rec["pending"]["heldjobheld1"] = fut
            leftover = rec["pending"]
            rec["pending"] = {}
            agent_hub._conns.pop((1, self.hid), None)
            await agent_hub._hold_pending((1, self.hid), leftover)
            self.assertFalse(fut.done())
            second = FakeWS(self.hid)
            rec2 = {"ws": second, "pending": {}, "user_id": 1, "host_id": self.hid}
            rec2["pending"].update(agent_hub._take_held((1, self.hid)))
            agent_hub._conns[(1, self.hid)] = rec2
            self.assertIn("heldjobheld1", rec2["pending"])
            rec2["pending"]["heldjobheld1"].set_result(
                {"ok": True, "exit": 0, "out": "ok", "err": "", "error": ""}
            )
            self.assertEqual((await fut)["out"], "ok")

        loop.run_until_complete(go())

    def test_inflight_cap_allows_burst(self):
        self.assertGreaterEqual(agent_hub.MAX_INFLIGHT, 100)


if __name__ == "__main__":
    unittest.main()
