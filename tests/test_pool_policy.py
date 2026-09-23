"""Shared pool admin/user policy: access gates and credential filtering."""

from __future__ import annotations

import os
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
_TMP = tempfile.mkdtemp(prefix="fuse-pool-policy-")
os.environ["DATABASE_PATH"] = str(Path(_TMP) / "pool-policy.db")
os.environ.setdefault("APP_SECRET", "x" * 32)

from app import accounts, db  # noqa: E402
from app.sources import chatgpt as chatgpt_mod  # noqa: E402
from app.sources import devin as devin_mod  # noqa: E402
from app.sources import grok as grok_mod  # noqa: E402
from app.sources import pool_policy  # noqa: E402
from app.sources import kimi as kimi_mod
from app.sources import zai as zai_mod  # noqa: E402
from app.sources.pool_pick import reset_health  # noqa: E402


GROK_EXTRA = {
    "access_token": "tok",
    "email": "grok-owner@test.invalid",
    "billing": {"fetched_at": "2026-01-01T00:00:00Z", "period_type": "month"},
}
CHATGPT_EXTRA = {"refresh_token": "rt", "access_token": "tok"}
ZAI_EXTRA = {"api_key": "zk", "billing": {"plan_type": "pro"}}
DEVIN_EXTRA = {"api_key": "dk"}
KIMI_EXTRA = {"access_token": "kt", "refresh_token": "kr", "billing": {"plan_type": "Plus", "fetched_at": "2026-01-01T00:00:00Z", "windows": [{"kind": "5h", "percent": 10}]}}


class PoolPolicyTest(unittest.TestCase):
    def setUp(self):
        db.execute("DELETE FROM credentials")
        db.execute("DELETE FROM api_keys")
        db.execute("DELETE FROM users")
        self.donor = accounts.create_user("donor@test.invalid", "password12", admin=False)
        self.alice = accounts.create_user("alice@test.invalid", "password12", admin=False)
        self.bob = accounts.create_user("bob@test.invalid", "password12", admin=False)
        for kind in pool_policy.POOL_KINDS:
            pool_policy.set_admin_policy(kind, pool_shared=True, open_access=False)

    def _add(self, kind: str, user_id: int, extra: dict, secret: str = "secret"):
        sid = pool_policy.source_id(kind)
        return accounts.add_credential(sid, kind, secret, extra=extra, donated_by=user_id)

    def test_defaults_match_today(self):
        for kind in pool_policy.POOL_KINDS:
            policy = pool_policy.admin_policy(kind)
            self.assertTrue(policy["pool_shared"], kind)
            self.assertFalse(policy["open_access"], kind)
            self.assertTrue(pool_policy.uses_shared_pool(self.alice["id"], kind), kind)
            self.assertFalse(pool_policy.user_own_only(self.alice["id"], kind), kind)

    def test_admin_off_forces_own_keys_and_locks_override(self):
        pool_policy.set_admin_policy("grok", pool_shared=False, open_access=False)
        pool_policy.set_user_own_only(self.alice["id"], "grok", False)
        self.assertFalse(pool_policy.uses_shared_pool(self.alice["id"], "grok"))
        ui = pool_policy.ui_state(self.alice["id"], "grok")
        self.assertFalse(ui["can_override"])
        self.assertFalse(ui["uses_shared"])
        self.assertFalse(ui["pool_shared"])

    def test_user_own_only_overrides_shared_admin(self):
        pool_policy.set_admin_policy("chatgpt", pool_shared=True, open_access=False)
        pool_policy.set_user_own_only(self.alice["id"], "chatgpt", True)
        self.assertFalse(pool_policy.uses_shared_pool(self.alice["id"], "chatgpt"))
        self.assertTrue(pool_policy.uses_shared_pool(self.bob["id"], "chatgpt"))

    def test_filter_credentials_own_only(self):
        sid = pool_policy.source_id("grok")
        mine = accounts.add_credential(sid, "mine", "a", extra=GROK_EXTRA, donated_by=self.alice["id"])
        other = accounts.add_credential(sid, "other", "b", extra=GROK_EXTRA, donated_by=self.donor["id"])
        rows = db.fetchall("SELECT * FROM credentials WHERE source_id=?", (sid,))
        shared = pool_policy.filter_credentials(rows, "grok", self.alice["id"])
        self.assertEqual({r["id"] for r in shared}, {mine, other})
        pool_policy.set_user_own_only(self.alice["id"], "grok", True)
        own = pool_policy.filter_credentials(rows, "grok", self.alice["id"])
        self.assertEqual([r["id"] for r in own], [mine])

    def test_open_access_unlocks_without_own_account(self):
        self._add("devin", self.donor["id"], DEVIN_EXTRA)
        self.assertFalse(devin_mod.user_has_devin(self.alice["id"]))
        pool_policy.set_admin_policy("devin", pool_shared=True, open_access=True)
        state = devin_mod.devin_access_state(self.alice["id"])
        self.assertTrue(state["ok"])
        self.assertEqual(state["reason"], "open")

    def test_open_access_does_nothing_when_sharing_off(self):
        self._add("devin", self.donor["id"], DEVIN_EXTRA)
        pool_policy.set_admin_policy("devin", pool_shared=False, open_access=True)
        self.assertFalse(devin_mod.user_has_devin(self.alice["id"]))

    def test_open_access_does_nothing_when_user_opts_out(self):
        self._add("grok", self.donor["id"], GROK_EXTRA)
        pool_policy.set_admin_policy("grok", pool_shared=True, open_access=True)
        self.assertTrue(grok_mod.user_has_grok(self.alice["id"]))
        pool_policy.set_user_own_only(self.alice["id"], "grok", True)
        self.assertFalse(grok_mod.user_has_grok(self.alice["id"]))

    def test_contributor_still_unlocks_without_open_access(self):
        self._add("chatgpt", self.alice["id"], CHATGPT_EXTRA)
        self.assertTrue(chatgpt_mod.user_has_chatgpt(self.alice["id"]))
        self.assertFalse(chatgpt_mod.user_has_chatgpt(self.bob["id"]))

    def test_zai_cross_unlock_requires_shared_routing(self):
        self._add("grok", self.alice["id"], GROK_EXTRA)
        self._add("zai", self.donor["id"], ZAI_EXTRA)
        state = zai_mod.zai_access_state(self.alice["id"])
        self.assertTrue(state["ok"])
        self.assertEqual(state["reason"], "pool")
        pool_policy.set_user_own_only(self.alice["id"], "zai", True)
        own = zai_mod.zai_access_state(self.alice["id"])
        self.assertFalse(own["ok"])
        self.assertEqual(own["reason"], "none")

    def test_grok_open_access_does_not_cross_unlock_zai(self):
        self._add("grok", self.donor["id"], GROK_EXTRA)
        self._add("zai", self.donor["id"], ZAI_EXTRA)
        pool_policy.set_admin_policy("grok", pool_shared=True, open_access=True)
        pool_policy.set_admin_policy("zai", pool_shared=True, open_access=False)
        self.assertTrue(grok_mod.user_has_grok(self.alice["id"]))
        self.assertFalse(zai_mod.user_has_zai(self.alice["id"]))

    def test_zai_open_access_without_a_zai_key(self):
        self._add("zai", self.donor["id"], ZAI_EXTRA)
        pool_policy.set_admin_policy("zai", pool_shared=True, open_access=True)
        state = zai_mod.zai_access_state(self.alice["id"])
        self.assertTrue(state["ok"])
        self.assertEqual(state["reason"], "open")


class PoolChatFilterTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        reset_health()
        db.execute("DELETE FROM credentials")
        db.execute("DELETE FROM api_keys")
        db.execute("DELETE FROM users")
        self.alice = accounts.create_user("chat-a@test.invalid", "password12", admin=False)
        self.donor = accounts.create_user("chat-d@test.invalid", "password12", admin=False)
        sid = grok_mod.ensure_grok_source()
        self.mine = accounts.add_credential(sid, "mine", "a", extra=GROK_EXTRA, donated_by=self.alice["id"])
        self.other = accounts.add_credential(sid, "other", "b", extra=GROK_EXTRA, donated_by=self.donor["id"])
        pool_policy.set_admin_policy("grok", pool_shared=True, open_access=False)

    @patch("app.sources.grok.note_pool_invoke")
    @patch("app.sources.grok.openai_chat", new_callable=AsyncMock)
    @patch("app.sources.grok.refresh_pool_billing", new_callable=AsyncMock)
    @patch("app.sources.grok.refresh_due", new_callable=AsyncMock)
    async def test_own_only_chat_skips_other_accounts(self, _due, _bill, chat, _invoke):
        chat.return_value = (
            200,
            {"id": "c", "usage": {"prompt_tokens": 1, "completion_tokens": 1}},
            None,
            {"prompt_tokens": 1, "completion_tokens": 1, "cached_tokens": 0},
            {},
        )
        pool_policy.set_user_own_only(self.alice["id"], "grok", True)
        src = grok_mod.GrokSource({"id": grok_mod.ensure_grok_source(), "name": "Grok"}, {}, [])
        result = await src.chat(
            {"model": "grok-4.6", "messages": [{"role": "user", "content": "hi"}]},
            user_id=self.alice["id"],
        )
        self.assertEqual(result.status, 200)
        self.assertEqual(result.credential_id, self.mine)
        self.assertEqual([c["id"] for c in src.creds], [self.mine])


class PoolPolicyHttpTest(unittest.TestCase):
    def setUp(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from starlette.middleware.sessions import SessionMiddleware

        from app import web
        from app.config import settings

        db.execute("DELETE FROM credentials")
        db.execute("DELETE FROM api_keys")
        db.execute("DELETE FROM users")
        self.admin = accounts.create_user("admin@test.invalid", "password12", admin=True)
        self.user = accounts.create_user("user@test.invalid", "password12", admin=False)
        for kind in pool_policy.POOL_KINDS:
            pool_policy.set_admin_policy(kind, pool_shared=True, open_access=False)
        app = FastAPI()
        app.add_middleware(
            SessionMiddleware,
            secret_key=settings.app_secret,
            https_only=True,
            same_site="strict",
        )
        app.include_router(web.router)
        self.client = TestClient(app, base_url="https://testserver")
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)

    def _csrf(self, html: str) -> str:
        match = re.search(r'name="csrf-token" content="([^"]+)"', html)
        if match:
            return match.group(1)
        match = re.search(r'name="csrf_token" value="([^"]+)"', html)
        if not match:
            self.fail("csrf token missing")
        return match.group(1)

    def _login(self, email: str) -> str:
        page = self.client.get("/login")
        token = self._csrf(page.text)
        posted = self.client.post(
            "/login",
            data={"email": email, "password": "password12", "csrf_token": token},
            follow_redirects=False,
        )
        self.assertEqual(posted.status_code, 303)
        return posted.headers.get("location") or "/sources"

    def test_admin_can_flip_pool_switches(self):
        self._login("admin@test.invalid")
        page = self.client.get("/admin/sources")
        self.assertEqual(page.status_code, 200)
        self.assertIn("Who can use donated accounts", page.text)
        self.assertIn("devin_open_access", page.text)
        self.assertIn("kimi_open_access", page.text)
        csrf = self._csrf(page.text)
        saved = self.client.post(
            "/admin/sources/pools",
            data={
                "csrf_token": csrf,
                "grok_pool_shared": "1",
                "chatgpt_pool_shared": "1",
                "zai_pool_shared": "1",
                "devin_open_access": "1",
                "devin_pool_shared": "1",
                "kimi_pool_shared": "1",
            },
            follow_redirects=False,
        )
        self.assertEqual(saved.status_code, 303)
        policy = pool_policy.admin_policy("devin")
        self.assertTrue(policy["pool_shared"])
        self.assertTrue(policy["open_access"])
        grok = pool_policy.admin_policy("grok")
        self.assertTrue(grok["pool_shared"])
        self.assertFalse(grok["open_access"])

    def test_user_override_and_global_disable(self):
        self._login("user@test.invalid")
        page = self.client.get("/sources")
        self.assertEqual(page.status_code, 200)
        self.assertIn("Use only my linked", page.text)
        self.assertNotIn("disabled globally", page.text)
        csrf = self._csrf(page.text)
        saved = self.client.post(
            "/sources/pool/grok/prefs",
            data={"csrf_token": csrf, "own_only": "1"},
            follow_redirects=False,
        )
        self.assertEqual(saved.status_code, 303)
        self.assertTrue(pool_policy.user_own_only(self.user["id"], "grok"))

        pool_policy.set_admin_policy("grok", pool_shared=False, open_access=False)
        page = self.client.get("/sources")
        self.assertIn("disabled globally", page.text)
        csrf = self._csrf(page.text)
        ignored = self.client.post(
            "/sources/pool/grok/prefs",
            data={"csrf_token": csrf, "own_only": "0"},
            follow_redirects=False,
        )
        self.assertEqual(ignored.status_code, 303)
        self.assertFalse(pool_policy.uses_shared_pool(self.user["id"], "grok"))

    def test_sources_tables_split_openai_and_sort_mine_first(self):
        grok_sid = grok_mod.ensure_grok_source()
        chatgpt_sid = chatgpt_mod.ensure_chatgpt_source()
        accounts.add_credential(
            grok_sid,
            "other grok",
            "g1",
            extra=GROK_EXTRA,
            donated_by=self.admin["id"],
        )
        accounts.add_credential(
            grok_sid,
            "my grok",
            "g2",
            extra=GROK_EXTRA,
            donated_by=self.user["id"],
        )
        accounts.add_credential(
            chatgpt_sid,
            "paid",
            "c1",
            extra={
                "refresh_token": "rt",
                "access_token": "tok",
                "billing": {"plan_type": "plus", "windows": [{"percent": 12, "seconds": 18000}]},
            },
            donated_by=self.admin["id"],
        )
        accounts.add_credential(
            chatgpt_sid,
            "free",
            "c2",
            extra={
                "refresh_token": "rt",
                "access_token": "tok",
                "billing": {"plan_type": "free", "windows": [{"percent": 4, "seconds": 30 * 86400}]},
            },
            donated_by=self.user["id"],
        )
        self._login("user@test.invalid")
        page = self.client.get("/sources")
        self.assertEqual(page.status_code, 200)
        self.assertIn("source-table", page.text)
        self.assertIn("Show only my accounts", page.text)
        self.assertIn("OpenAI paid", page.text)
        self.assertIn("OpenAI Free / Go", page.text)
        self.assertNotIn("source-card", page.text)
        grok_pos = page.text.find("grok-owner@test.invalid")
        other_pos = page.text.find("Grok plan ")
        self.assertGreater(grok_pos, 0)
        self.assertGreater(other_pos, grok_pos)
        self.assertIn('data-mine-group="chatgpt"', page.text)
        self.assertIn(">Plus<", page.text)
        self.assertIn(">Free<", page.text)
        self.assertIn("grok-owner@test.invalid", page.text)


if __name__ == "__main__":
    unittest.main()
