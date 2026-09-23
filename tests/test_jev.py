"""Jev permission judge: clipping, thresholds, fail-closed (httpx mocked)."""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
_TMP = tempfile.mkdtemp(prefix="fuse-jev-")
os.environ.setdefault("DATABASE_PATH", str(Path(_TMP) / "jev.db"))
os.environ.setdefault("APP_SECRET", "x" * 32)

from app import chats, db, jev, permissions


def _answers(dangerous=0.05):
    return {"dangerous": {"type": "noul", "noul": dangerous}}


class PolicyTest(unittest.TestCase):
    def test_allow_when_not_dangerous(self):
        self.assertTrue(jev.should_allow(_answers(0.05)))
        self.assertTrue(jev.should_allow(_answers(0.39)))

    def test_ask_when_dangerous(self):
        self.assertFalse(jev.should_allow(_answers(0.5)))
        self.assertFalse(jev.should_allow(_answers(0.95)))

    def test_ask_when_missing(self):
        self.assertFalse(jev.should_allow({"safe": {"noul": 0.99}}))
        self.assertFalse(jev.should_allow({}))

    def test_noul_out_of_range(self):
        self.assertFalse(jev.should_allow(_answers(1.4)))

    def test_destructive_regex(self):
        self.assertTrue(jev.looks_destructive("rm -rf /"))
        self.assertTrue(jev.looks_destructive("git push --force origin main"))
        self.assertFalse(jev.looks_destructive("ls ../logs"))
        self.assertFalse(jev.looks_destructive("rm file.txt"))


class ClipTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with db.tx() as conn:
            permissions.init_schema(conn)
            ts = db.now()
            conn.execute(
                "INSERT OR IGNORE INTO users(id, email, password_hash, created_at) VALUES (1, 'u@test.local', 'x', ?)",
                (ts,),
            )
            conn.execute(
                "INSERT OR IGNORE INTO chat_threads(id, user_id, created_at, updated_at) VALUES (1, 1, ?, ?)",
                (ts, ts),
            )

    def test_clip_keeps_last_user_and_tools(self):
        messages = [
            {"role": "user", "content": "fix the login bug"},
            {
                "role": "assistant",
                "content": "looking",
                "tools": [{"name": "read_file", "path": "src/auth.py", "status": "done", "preview": "ok"}],
            },
            {"role": "user", "content": "also check ../shared"},
        ]
        with db.tx() as conn:
            conn.execute(
                "UPDATE chat_threads SET messages=? WHERE id=1",
                (chats.dump_messages(messages),),
            )
        state = jev.clip_thread_state(1, 1, {"tool": "list_dir", "path": "/srv/shared"}, roots=["/tmp", "/srv/app"])
        self.assertEqual(state["user_request"], "also check ../shared")
        self.assertEqual(state["allowed_roots"], ["/tmp", "/srv/app"])
        self.assertEqual(state["action"]["path"], "/srv/shared")
        roles = [item["role"] for item in state["recent"]]
        self.assertIn("user", roles)
        self.assertIn("tool", roles)

    def test_clip_caps_size(self):
        huge = "x" * 20_000
        with db.tx() as conn:
            conn.execute(
                "UPDATE chat_threads SET messages=? WHERE id=1",
                (chats.dump_messages([{"role": "user", "content": huge}]),),
            )
        state = jev.clip_thread_state(1, 1, {"command": "y" * 4000})
        self.assertLessEqual(len(state["user_request"]), jev.USER_CLIP)
        self.assertLessEqual(len(state["action"]["command"]), jev.COMMAND_CLIP)


class JudgeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with db.tx() as conn:
            permissions.init_schema(conn)
            ts = db.now()
            conn.execute(
                "INSERT OR IGNORE INTO users(id, email, password_hash, created_at) VALUES (1, 'u@test.local', 'x', ?)",
                (ts,),
            )
            conn.execute(
                "INSERT OR IGNORE INTO chat_threads(id, user_id, created_at, updated_at) VALUES (1, 1, ?, ?)",
                (ts, ts),
            )

    def test_no_key_refuses(self):
        with patch.object(jev, "user_can_auto", return_value=False):
            self.assertFalse(asyncio.run(jev.judge(1, 1, {"path": "/etc/hosts"})))

    def test_http_error_refuses(self):
        with patch.object(jev, "evaluate", new_callable=AsyncMock, return_value=None), patch.object(
            jev, "user_can_auto", return_value=True
        ):
            self.assertFalse(asyncio.run(jev.judge(1, 1, {"path": "../logs"})))

    def test_bad_openrouter_body_refuses(self):
        response = Mock(status_code=200)
        response.json.return_value = {"error": {"message": "model not found"}}
        inner = Mock()
        inner.post = AsyncMock(return_value=response)
        client = Mock()
        client.__aenter__ = AsyncMock(return_value=inner)
        client.__aexit__ = AsyncMock(return_value=False)
        with patch.object(jev, "user_can_auto", return_value=True), patch(
            "app.sources.openrouter.pick_key_for", return_value=(None, {}, "sk-or-v1-test", "user")
        ), patch("app.jev.httpx.AsyncClient", return_value=client):
            self.assertFalse(asyncio.run(jev.judge(1, 1, {"path": "../logs"})))

    def test_allow_when_answers_pass(self):
        payload = {"answers": _answers(), "model": jev.MODEL, "usage": {"input_tokens": 10, "output_tokens": 4}}
        with patch.object(jev, "evaluate", new_callable=AsyncMock, return_value=payload), patch.object(
            jev, "user_can_auto", return_value=True
        ):
            self.assertTrue(asyncio.run(jev.judge(1, 1, {"path": "../src"})))

    def test_destructive_skips_network(self):
        with patch.object(jev, "evaluate", side_effect=AssertionError("should not call")):
            self.assertFalse(asyncio.run(jev.judge(1, 1, {"command": "rm -rf /"})))


class ReviewAutoTest(unittest.TestCase):
    def setUp(self):
        with db.tx() as conn:
            permissions.init_schema(conn)
            conn.execute("DELETE FROM chat_permission_asks")
            conn.execute("DELETE FROM chat_permission_grants")
            conn.execute("UPDATE chat_threads SET bypass_permissions=0, auto_permissions=0")
            ts = db.now()
            conn.execute(
                "INSERT OR IGNORE INTO users(id, email, password_hash, created_at) VALUES (1, 'u@test.local', 'x', ?)",
                (ts,),
            )
            conn.execute(
                "INSERT OR IGNORE INTO chat_threads(id, user_id, created_at, updated_at) VALUES (1, 1, ?, ?)",
                (ts, ts),
            )

    def _gate(self):
        return permissions.Gate(
            user_id=1, thread_id=1, run_id="run-1", requester={"role": "main"},
            unattended=False, notify=lambda event: None, cancel=None,
        )

    def test_auto_off_still_asks(self):
        res = asyncio.run(permissions.review(
            tool="read_file", args={"path": "/etc/passwd"}, user_id=1,
            host_id=7, host_alias="prod", root="/srv/app", windows=False,
            gate=permissions.Gate(
                user_id=1, thread_id=1, run_id="run-1", requester={"role": "main"},
                unattended=True, notify=lambda event: None, cancel=None,
            ),
        ))
        self.assertFalse(res.allowed)

    def test_judge_exception_still_asks(self):
        permissions.set_auto(1, 1, True)
        events = []

        async def flow():
            gate = permissions.Gate(
                user_id=1, thread_id=1, run_id="run-1", requester={"role": "main"},
                unattended=False, notify=events.append, cancel=None,
            )
            with patch("app.jev.user_can_auto", return_value=True), patch(
                "app.jev.judge", new_callable=AsyncMock, side_effect=RuntimeError("boom")
            ):
                task = asyncio.create_task(permissions.review(
                    tool="write_file", args={"path": "/etc/hosts", "content": "x"}, user_id=1,
                    host_id=7, host_alias="prod", root="/srv/app", windows=False, gate=gate,
                ))
                for _ in range(200):
                    await asyncio.sleep(0.02)
                    if events and events[0].get("status") == "pending":
                        break
                permissions.resolve(1, 1, events[0]["id"], "deny")
                return await task

        res = asyncio.run(flow())
        self.assertFalse(res.allowed)
        self.assertTrue(events and events[0].get("status") == "pending")

    def test_enabling_auto_resolves_pending(self):
        with db.tx() as conn:
            conn.execute("DELETE FROM chat_permission_asks")
            conn.execute(
                """INSERT INTO chat_permission_asks(
                       id,user_id,thread_id,run_id,status,decision,kind,tool,host_id,host,path,cwd,command,
                       secret,requester,grant_kind,grant_value,created_at,expires_at)
                   VALUES ('pa_auto',1,1,'r','pending','','path','read_file',7,'prod','/etc','', '',0,'Main agent','','','2026-01-01T00:00:00Z','2099-01-01T00:00:00Z')"""
            )
        permissions.set_auto(1, 1, True)
        row = db.fetchone("SELECT status, decision FROM chat_permission_asks WHERE id='pa_auto'")
        self.assertEqual(row["status"], "allowed")
        self.assertEqual(row["decision"], "allow")

    def test_enabling_auto_keeps_secret_pending(self):
        with db.tx() as conn:
            conn.execute("DELETE FROM chat_permission_asks")
            conn.execute(
                """INSERT INTO chat_permission_asks(
                       id,user_id,thread_id,run_id,status,decision,kind,tool,host_id,host,path,cwd,command,
                       secret,requester,grant_kind,grant_value,created_at,expires_at)
                   VALUES ('pa_secret',1,1,'r','pending','','path','read_file',7,'prod','/srv/app/.env','', '',1,'Main agent','file','/srv/app/.env','2026-01-01T00:00:00Z','2099-01-01T00:00:00Z')"""
            )
        permissions.set_auto(1, 1, True)
        row = db.fetchone("SELECT status FROM chat_permission_asks WHERE id='pa_secret'")
        self.assertEqual(row["status"], "pending")

    def test_auto_allows_list_dir_without_jev(self):
        permissions.set_auto(1, 1, True)
        with patch("app.jev.user_can_auto", return_value=True), patch(
            "app.jev.judge", new_callable=AsyncMock, side_effect=AssertionError("list_dir must not call Jev")
        ):
            res = asyncio.run(permissions.review(
                tool="list_dir", args={"path": "/home"}, user_id=1,
                host_id=7, host_alias="prod", root="/srv/app", windows=False, gate=self._gate(),
            ))
        self.assertTrue(res.allowed)
        self.assertEqual(res.root, "")
        pending = db.fetchone("SELECT COUNT(*) AS n FROM chat_permission_asks WHERE status='pending'")
        self.assertEqual(pending["n"], 0)

    def test_auto_allows_read_file_without_jev(self):
        permissions.set_auto(1, 1, True)
        with patch("app.jev.user_can_auto", return_value=True), patch(
            "app.jev.judge", new_callable=AsyncMock, side_effect=AssertionError("read_file must not call Jev")
        ):
            res = asyncio.run(permissions.review(
                tool="read_file", args={"path": "/etc/hosts"}, user_id=1,
                host_id=7, host_alias="prod", root="/srv/app", windows=False, gate=self._gate(),
            ))
        self.assertTrue(res.allowed)

    def test_auto_allows_without_card(self):
        permissions.set_auto(1, 1, True)
        with patch("app.jev.user_can_auto", return_value=True), patch(
            "app.jev.judge", new_callable=AsyncMock, return_value=True
        ) as mocked:
            res = asyncio.run(permissions.review(
                tool="write_file", args={"path": "../shared/out.txt", "content": "x"}, user_id=1,
                host_id=7, host_alias="prod", root="/srv/app", windows=False, gate=self._gate(),
            ))
        self.assertTrue(res.allowed)
        self.assertEqual(res.root, "")
        mocked.assert_awaited()
        pending = db.fetchone("SELECT COUNT(*) AS n FROM chat_permission_asks WHERE status='pending'")
        self.assertEqual(pending["n"], 0)

    def test_auto_fail_closed_asks(self):
        permissions.set_auto(1, 1, True)
        events = []

        async def flow():
            gate = permissions.Gate(
                user_id=1, thread_id=1, run_id="run-1", requester={"role": "main"},
                unattended=False, notify=events.append, cancel=None,
            )
            with patch("app.jev.user_can_auto", return_value=True), patch(
                "app.jev.judge", new_callable=AsyncMock, return_value=False
            ):
                task = asyncio.create_task(permissions.review(
                    tool="write_file", args={"path": "/etc/hosts", "content": "x"}, user_id=1,
                    host_id=7, host_alias="prod", root="/srv/app", windows=False, gate=gate,
                ))
                for _ in range(200):
                    await asyncio.sleep(0.02)
                    if events and events[0].get("status") == "pending":
                        break
                permissions.resolve(1, 1, events[0]["id"], "deny")
                return await task

        res = asyncio.run(flow())
        self.assertFalse(res.allowed)
        self.assertTrue(events and events[0].get("status") == "pending")

    def test_secret_never_calls_jev(self):
        permissions.set_auto(1, 1, True)
        with patch("app.jev.judge", new_callable=AsyncMock, side_effect=AssertionError("secret must not call Jev")):
            res = asyncio.run(permissions.review(
                tool="read_file", args={"path": ".env"}, user_id=1,
                host_id=7, host_alias="prod", root="/srv/app", windows=False,
                gate=permissions.Gate(
                    user_id=1, thread_id=1, run_id="run-1", requester={"role": "main"},
                    unattended=True, notify=lambda event: None, cancel=None,
                ),
            ))
        self.assertFalse(res.allowed)

    def test_bypass_still_wins(self):
        permissions.set_auto(1, 1, True)
        permissions.set_bypass(1, 1, True)
        with patch("app.jev.judge", new_callable=AsyncMock, side_effect=AssertionError("bypass must skip Jev")):
            res = asyncio.run(permissions.review(
                tool="read_file", args={"path": "/etc/passwd"}, user_id=1,
                host_id=7, host_alias="prod", root="/srv/app", windows=False, gate=self._gate(),
            ))
        self.assertTrue(res.allowed)

    def test_update_thread_does_not_call_jev_inside_write_lock(self):
        import inspect
        from app import chats as chats_mod

        source = inspect.getsource(chats_mod.update_thread)
        key_check = source.find("user_can_auto")
        tx = source.find("with store.transaction()")
        self.assertGreater(key_check, 0)
        self.assertGreater(tx, 0)
        self.assertLess(key_check, tx)

    def test_panel_reports_auto(self):
        permissions.set_auto(1, 1, True)
        with patch.object(jev, "user_can_auto", return_value=True):
            panel = permissions.panel_for(1, 1)
        self.assertTrue(panel["auto"])
        self.assertTrue(panel["auto_available"])


if __name__ == "__main__":
    unittest.main()
