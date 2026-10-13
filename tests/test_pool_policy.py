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

import pytest

# Uses the database: a disposable MariaDB schema shared by this module.
pytestmark = pytest.mark.usefixtures("isolated_db")


def teardown_module():
    # Share levels are cached by user id; the next module's schema reuses ids.
    pool_policy._share_cache.clear()


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
        pool_policy._share_cache.clear()
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
            self.assertEqual(pool_policy.user_pref(self.alice["id"], kind), {"share": "everyone", "use": "pool"}, kind)

    def test_admin_off_forces_own_keys_and_locks_override(self):
        pool_policy.set_admin_policy("grok", pool_shared=False, open_access=False)
        pool_policy.set_user_pref(self.alice["id"], "grok", share="everyone", use="pool")
        self.assertFalse(pool_policy.uses_shared_pool(self.alice["id"], "grok"))
        ui = pool_policy.ui_state(self.alice["id"], "grok")
        self.assertFalse(ui["can_override"])
        self.assertFalse(ui["uses_shared"])
        self.assertFalse(ui["pool_shared"])

    def test_user_own_only_overrides_shared_admin(self):
        pool_policy.set_admin_policy("chatgpt", pool_shared=True, open_access=False)
        pool_policy.set_user_pref(self.alice["id"], "chatgpt", share="private")
        self.assertFalse(pool_policy.uses_shared_pool(self.alice["id"], "chatgpt"))
        self.assertTrue(pool_policy.uses_shared_pool(self.bob["id"], "chatgpt"))

    def test_filter_credentials_own_only(self):
        sid = pool_policy.source_id("grok")
        mine = accounts.add_credential(sid, "mine", "a", extra=GROK_EXTRA, donated_by=self.alice["id"])
        other = accounts.add_credential(sid, "other", "b", extra=GROK_EXTRA, donated_by=self.donor["id"])
        rows = db.fetchall("SELECT * FROM credentials WHERE source_id=?", (sid,))
        shared = pool_policy.filter_credentials(rows, "grok", self.alice["id"])
        self.assertEqual({r["id"] for r in shared}, {mine, other})
        pool_policy.set_user_pref(self.alice["id"], "grok", use="mine")
        own = pool_policy.filter_credentials(rows, "grok", self.alice["id"])
        self.assertEqual([r["id"] for r in own], [mine])

    def test_own_only_withholds_donor_from_everyone_else(self):
        sid = pool_policy.source_id("grok")
        mine = accounts.add_credential(sid, "mine", "a", extra=GROK_EXTRA, donated_by=self.alice["id"])
        other = accounts.add_credential(sid, "other", "b", extra=GROK_EXTRA, donated_by=self.donor["id"])
        rows = db.fetchall("SELECT * FROM credentials WHERE source_id=?", (sid,))
        pool_policy.set_user_pref(self.alice["id"], "grok", share="private")
        for caller in (self.bob["id"], self.donor["id"], None):
            visible = {r["id"] for r in pool_policy.filter_credentials(rows, "grok", caller)}
            self.assertNotIn(mine, visible, caller)
            self.assertIn(other, visible, caller)
        other_pool = pool_policy.filter_credentials(rows, "chatgpt", self.bob["id"])
        self.assertEqual({r["id"] for r in other_pool}, {mine, other})

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
        pool_policy.set_user_pref(self.alice["id"], "grok", use="mine")
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
        pool_policy.set_user_pref(self.alice["id"], "zai", use="mine")
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


    def _grok_rows(self):
        sid = pool_policy.source_id("grok")
        return db.fetchall("SELECT * FROM credentials WHERE source_id=? ORDER BY id", (sid,))

    def _visible(self, caller):
        return {r["id"] for r in pool_policy.filter_credentials(self._grok_rows(), "grok", caller)}

    def test_share_levels_decide_who_reaches_a_donors_accounts(self):
        donor_acct = self._add("grok", self.donor["id"], GROK_EXTRA)
        alice_acct = self._add("grok", self.alice["id"], GROK_EXTRA)
        pool_policy.set_admin_policy("grok", pool_shared=True, open_access=True)

        pool_policy.set_user_pref(self.donor["id"], "grok", share="contributors")
        # Alice shares a live account here, so she counts; Bob (open access
        # only) and a system caller do not.
        self.assertIn(donor_acct, self._visible(self.alice["id"]))
        self.assertNotIn(donor_acct, self._visible(self.bob["id"]))
        self.assertNotIn(donor_acct, self._visible(None))
        # Alice keeping hers private stops her counting as a sharer, and a
        # private donor uses only their own accounts anyway.
        pool_policy.set_user_pref(self.alice["id"], "grok", share="private")
        self.assertFalse(pool_policy.contributes(self.alice["id"], "grok"))
        self.assertEqual(self._visible(self.alice["id"]), {alice_acct})
        # Sharing with everyone, but routing only onto her own accounts, still
        # counts as sharing and still keeps her requests on her own plan.
        pool_policy.set_user_pref(self.alice["id"], "grok", share="everyone", use="mine")
        self.assertTrue(pool_policy.contributes(self.alice["id"], "grok"))
        self.assertEqual(self._visible(self.alice["id"]), {alice_acct})
        self.assertIn(alice_acct, self._visible(self.bob["id"]))

        pool_policy.set_user_pref(self.donor["id"], "grok", share="private")
        for caller in (self.alice["id"], self.bob["id"], None):
            self.assertNotIn(donor_acct, self._visible(caller), caller)
        self.assertEqual(self._visible(self.donor["id"]), {donor_acct})

    def test_a_dead_account_does_not_count_as_sharing(self):
        donor_acct = self._add("grok", self.donor["id"], GROK_EXTRA)
        self._add("grok", self.alice["id"], {"access_token": "", "email": "dead@test.invalid"})
        pool_policy.set_user_pref(self.donor["id"], "grok", share="contributors")
        self.assertFalse(pool_policy.contributes(self.alice["id"], "grok"))
        self.assertNotIn(donor_acct, self._visible(self.alice["id"]))

    def test_admin_off_shares_nothing_whatever_users_chose(self):
        donor_acct = self._add("grok", self.donor["id"], GROK_EXTRA)
        alice_acct = self._add("grok", self.alice["id"], GROK_EXTRA)
        pool_policy.set_admin_policy("grok", pool_shared=False, open_access=True)
        self.assertEqual(pool_policy.effective_pref(self.alice["id"], "grok"), {"share": "private", "use": "mine"})
        self.assertEqual(self._visible(self.alice["id"]), {alice_acct})
        self.assertEqual(self._visible(self.bob["id"]), set())
        self.assertEqual(self._visible(None), set())
        self.assertEqual(pool_policy.share_level("grok", self.donor["id"]), "private")
        self.assertNotIn(donor_acct, self._visible(self.alice["id"]))

    def test_legacy_own_only_reads_as_private_and_writes_keep_a_mirror(self):
        import json

        db.execute(
            "UPDATE users SET pool_prefs=? WHERE id=?",
            (json.dumps({"grok": {"own_only": True}, "chatgpt": {"own_only": False}}), self.alice["id"]),
        )
        pool_policy._share_cache.clear()
        self.assertEqual(pool_policy.user_pref(self.alice["id"], "grok"), {"share": "private", "use": "mine"})
        self.assertEqual(pool_policy.user_pref(self.alice["id"], "chatgpt"), {"share": "everyone", "use": "pool"})
        self.assertEqual(pool_policy.share_level("grok", self.alice["id"]), "private")

        def stored(kind):
            return pool_policy.user_prefs(self.alice["id"])[kind]

        pool_policy.set_user_pref(self.alice["id"], "grok", share="everyone", use="pool")
        self.assertEqual(stored("grok"), {"own_only": False, "share": "everyone", "use": "pool"})
        # Code rolled back to the own_only reader must never share more than
        # the user chose: anything short of "everyone, pool" reads as private.
        pool_policy.set_user_pref(self.alice["id"], "grok", share="contributors")
        self.assertTrue(stored("grok")["own_only"])
        pool_policy.set_user_pref(self.alice["id"], "grok", share="everyone", use="mine")
        self.assertTrue(stored("grok")["own_only"])
        self.assertEqual(stored("chatgpt"), {"own_only": False})
        with self.assertRaises(ValueError):
            pool_policy.set_user_pref(self.alice["id"], "grok", share="friends")

    def test_open_access_needs_an_account_the_user_can_reach(self):
        self._add("devin", self.donor["id"], DEVIN_EXTRA)
        pool_policy.set_admin_policy("devin", pool_shared=True, open_access=True)
        self.assertTrue(devin_mod.user_has_devin(self.alice["id"]))
        # The only live account is private or sharers-only: open access would
        # list Devin models and then 503 every request.
        pool_policy.set_user_pref(self.donor["id"], "devin", share="private")
        self.assertFalse(devin_mod.user_has_devin(self.alice["id"]))
        pool_policy.set_user_pref(self.donor["id"], "devin", share="contributors")
        self.assertFalse(devin_mod.user_has_devin(self.alice["id"]))
        pool_policy.set_user_pref(self.donor["id"], "devin", share="everyone")
        self.assertTrue(devin_mod.user_has_devin(self.alice["id"]))

    def test_open_access_ignores_accounts_held_from_the_pool(self):
        acct = self._add("grok", self.donor["id"], GROK_EXTRA)
        pool_policy.set_admin_policy("grok", pool_shared=True, open_access=True)
        self.assertTrue(grok_mod.user_has_grok(self.alice["id"]))
        db.execute("UPDATE credentials SET pool_held_until=? WHERE id=?", ("2999-01-01T00:00:00Z", acct))
        self.assertFalse(grok_mod.user_has_grok(self.alice["id"]))
        self.assertTrue(grok_mod.user_has_grok(self.donor["id"]))

    def test_zai_cross_unlock_needs_a_shared_account(self):
        self._add("grok", self.alice["id"], GROK_EXTRA)
        self._add("zai", self.donor["id"], ZAI_EXTRA)
        self.assertEqual(zai_mod.zai_access_state(self.alice["id"])["reason"], "pool")
        # A private x.ai account gives the pools nothing back.
        pool_policy.set_user_pref(self.alice["id"], "grok", share="private")
        self.assertFalse(zai_mod.user_has_zai(self.alice["id"]))
        pool_policy.set_user_pref(self.alice["id"], "grok", share="contributors")
        self.assertTrue(zai_mod.user_has_zai(self.alice["id"]))
        pool_policy.set_admin_policy("grok", pool_shared=False, open_access=False)
        self.assertFalse(zai_mod.user_has_zai(self.alice["id"]))


class PoolChatFilterTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        reset_health()
        db.execute("DELETE FROM credentials")
        db.execute("DELETE FROM api_keys")
        db.execute("DELETE FROM users")
        self.alice = accounts.create_user("chat-a@test.invalid", "password12", admin=False)
        self.donor = accounts.create_user("chat-d@test.invalid", "password12", admin=False)
        pool_policy._share_cache.clear()
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
        pool_policy.set_user_pref(self.alice["id"], "grok", use="mine")
        src = grok_mod.GrokSource({"id": grok_mod.ensure_grok_source(), "name": "Grok"}, {}, [])
        result = await src.chat(
            {"model": "grok-4.6", "messages": [{"role": "user", "content": "hi"}]},
            user_id=self.alice["id"],
        )
        self.assertEqual(result.status, 200)
        self.assertEqual(result.credential_id, self.mine)
        self.assertEqual([c["id"] for c in src.creds], [self.mine])


class PoolChatOwnFirstTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        reset_health()
        db.execute("DELETE FROM credentials")
        db.execute("DELETE FROM api_keys")
        db.execute("DELETE FROM users")
        pool_policy._share_cache.clear()
        self.alice = accounts.create_user("own-a@test.invalid", "password12", admin=False)
        self.donor = accounts.create_user("own-d@test.invalid", "password12", admin=False)
        self.shy = accounts.create_user("own-s@test.invalid", "password12", admin=False)
        sid = grok_mod.ensure_grok_source()

        def extra(pct):
            return dict(GROK_EXTRA, billing=dict(GROK_EXTRA["billing"], credit_usage_percent=pct))

        self.mine = accounts.add_credential(sid, "mine", "a", extra=extra(80), donated_by=self.alice["id"])
        self.other = accounts.add_credential(sid, "other", "b", extra=extra(5), donated_by=self.donor["id"])
        self.secret = accounts.add_credential(sid, "secret", "c", extra=extra(1), donated_by=self.shy["id"])
        pool_policy.set_admin_policy("grok", pool_shared=True, open_access=False)
        pool_policy.set_user_pref(self.shy["id"], "grok", share="private")
        self.src = grok_mod.GrokSource({"id": sid, "name": "Grok"}, {}, [])
        self.body = {"model": "grok-4.6", "messages": [{"role": "user", "content": "hi"}]}

    @staticmethod
    def _reply(status):
        usage = {"prompt_tokens": 1, "completion_tokens": 1, "cached_tokens": 0}
        return (status, {"id": "c", "usage": usage} if status == 200 else {"error": "x"}, None, usage, {})

    @patch("app.sources.grok.note_pool_invoke")
    @patch("app.sources.grok.openai_chat", new_callable=AsyncMock)
    @patch("app.sources.grok.refresh_pool_billing", new_callable=AsyncMock)
    @patch("app.sources.grok.refresh_due", new_callable=AsyncMock)
    async def test_own_account_first_then_the_pool_never_a_private_one(self, _due, _bill, chat, _invoke):
        chat.return_value = self._reply(200)
        result = await self.src.chat(self.body, user_id=self.alice["id"])
        self.assertEqual(result.credential_id, self.mine)
        self.assertNotIn(self.secret, {c["id"] for c in self.src.creds})
        # The private account at 1% is the least used, and still never reached.
        outsider = accounts.create_user("own-o@test.invalid", "password12", admin=False)
        result = await self.src.chat(self.body, user_id=outsider["id"])
        self.assertEqual(result.credential_id, self.other)

        # Own account rate-limited: the same request falls back to the pool.
        chat.reset_mock()
        chat.side_effect = [self._reply(429), self._reply(200)]
        result = await self.src.chat(self.body, user_id=self.alice["id"])
        self.assertEqual(result.status, 200)
        self.assertEqual(result.credential_id, self.other)
        self.assertEqual(chat.call_count, 2)

    @patch("app.sources.grok.note_pool_invoke")
    @patch("app.sources.grok.openai_chat", new_callable=AsyncMock)
    @patch("app.sources.grok.refresh_pool_billing", new_callable=AsyncMock)
    @patch("app.sources.grok.refresh_due", new_callable=AsyncMock)
    async def test_owner_keeps_an_account_held_for_off_site_use(self, _due, _bill, chat, _invoke):
        chat.return_value = self._reply(200)
        db.execute("UPDATE credentials SET pool_held_until=? WHERE id=?", ("2999-01-01T00:00:00Z", self.mine))
        pool_policy.set_user_pref(self.alice["id"], "grok", use="mine")
        result = await self.src.chat(self.body, user_id=self.alice["id"])
        self.assertEqual(result.status, 200)
        self.assertEqual(result.credential_id, self.mine)
        pool_policy.set_user_pref(self.alice["id"], "grok", use="pool")
        result = await self.src.chat(self.body, user_id=self.donor["id"])
        self.assertEqual(result.credential_id, self.other)


class PoolChatCacheAffinityTest(unittest.IsolatedAsyncioTestCase):
    """Shared-pool routing keeps conversations on the account holding their
    prompt cache, and starts a subagent where its parent's prefix lives."""

    def setUp(self):
        from app.sources import pool_session

        reset_health()
        pool_session.reset_store()
        self.addCleanup(pool_session.reset_store)
        db.execute("DELETE FROM credentials")
        db.execute("DELETE FROM api_keys")
        db.execute("DELETE FROM users")
        db.execute("DELETE FROM pool_sessions")
        pool_policy._share_cache.clear()
        self.alice = accounts.create_user("aff-a@test.invalid", "password12", admin=False)
        self.donor = accounts.create_user("aff-d@test.invalid", "password12", admin=False)
        self.outsider = accounts.create_user("aff-o@test.invalid", "password12", admin=False)
        sid = grok_mod.ensure_grok_source()

        def extra(pct):
            return dict(GROK_EXTRA, billing=dict(GROK_EXTRA["billing"], credit_usage_percent=pct))

        self.cool = accounts.add_credential(sid, "cool", "a", extra=extra(5), donated_by=self.donor["id"])
        self.warm = accounts.add_credential(sid, "warm", "b", extra=extra(40), donated_by=self.donor["id"])
        pool_policy.set_admin_policy("grok", pool_shared=True, open_access=False)
        self.src = grok_mod.GrokSource({"id": sid, "name": "Grok"}, {}, [])
        self.body = {"model": "grok-4.6", "messages": [{"role": "user", "content": "hi"}]}

    def _session(self, key, user):
        from app.sources.pool_session import session_id_from

        return session_id_from({}, {"prompt_cache_key": key}, user_id=user["id"], model="grok-4.6")

    @staticmethod
    def _ok():
        usage = {"prompt_tokens": 1, "completion_tokens": 1, "cached_tokens": 0}
        return (200, {"id": "c", "usage": usage}, None, usage, {})

    @patch("app.sources.grok.note_pool_invoke")
    @patch("app.sources.grok.openai_chat", new_callable=AsyncMock)
    @patch("app.sources.grok.refresh_pool_billing", new_callable=AsyncMock)
    @patch("app.sources.grok.refresh_due", new_callable=AsyncMock)
    async def test_subagent_starts_on_its_parents_account_then_keeps_its_own_pin(self, _due, _bill, chat, _invoke):
        from app.sources import pool_session

        chat.return_value = self._ok()
        parent = self._session("thread:9", self.outsider)
        child = self._session("run:child", self.outsider)
        pool_session.bind("grok", parent, self.warm)
        result = await self.src.chat(self.body, user_id=self.outsider["id"], session_id=child, affinity_id=parent)
        self.assertEqual(result.credential_id, self.warm)
        self.assertEqual(pool_session.lookup("grok", child), self.warm)
        # Without the parent, a new session takes the least-used account.
        other = self._session("run:other", self.outsider)
        result = await self.src.chat(self.body, user_id=self.outsider["id"], session_id=other)
        self.assertEqual(result.credential_id, self.cool)
        # Once pinned, the child follows its own pin, not the parent's.
        pool_session.bind("grok", parent, self.cool)
        result = await self.src.chat(self.body, user_id=self.outsider["id"], session_id=child, affinity_id=parent)
        self.assertEqual(result.credential_id, self.warm)

    @patch("app.sources.grok.note_pool_invoke")
    @patch("app.sources.grok.openai_chat", new_callable=AsyncMock)
    @patch("app.sources.grok.refresh_pool_billing", new_callable=AsyncMock)
    @patch("app.sources.grok.refresh_due", new_callable=AsyncMock)
    async def test_linking_an_own_account_does_not_move_a_warm_conversation(self, _due, _bill, chat, _invoke):
        chat.return_value = self._ok()
        convo = self._session("thread:1", self.alice)
        pool_policy.set_admin_policy("grok", pool_shared=True, open_access=True)
        result = await self.src.chat(self.body, user_id=self.alice["id"], session_id=convo)
        self.assertEqual(result.credential_id, self.cool)
        mine = accounts.add_credential(
            grok_mod.ensure_grok_source(), "mine", "m", extra=GROK_EXTRA, donated_by=self.alice["id"]
        )
        result = await self.src.chat(self.body, user_id=self.alice["id"], session_id=convo)
        self.assertEqual(result.credential_id, self.cool)
        fresh = self._session("thread:2", self.alice)
        result = await self.src.chat(self.body, user_id=self.alice["id"], session_id=fresh)
        self.assertEqual(result.credential_id, mine)


class PoolPolicyHttpTest(unittest.TestCase):
    def setUp(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from starlette.middleware.sessions import SessionMiddleware

        from app import web
        from app.config import settings
        from app.security import limiter

        # Several logins per test: start and leave the login limiter empty so
        # neither these tests nor the next module trip it.
        limiter._hits.clear()
        self.addCleanup(limiter._hits.clear)
        db.execute("DELETE FROM credentials")
        db.execute("DELETE FROM api_keys")
        db.execute("DELETE FROM users")
        self.admin = accounts.create_user("admin@test.invalid", "password12", admin=True)
        self.user = accounts.create_user("user@test.invalid", "password12", admin=False)
        pool_policy._share_cache.clear()
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
        page = self.client.get("/admin/pools")
        self.assertEqual(page.status_code, 200)
        self.assertIn("Who can use donated accounts", page.text)
        self.assertIn("devin_open_access", page.text)
        self.assertIn("kimi_open_access", page.text)
        csrf = self._csrf(page.text)
        saved = self.client.post(
            "/admin/pools",
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

    def test_sources_hides_pool_accounts_until_the_user_has_access(self):
        grok_sid = grok_mod.ensure_grok_source()
        extra = dict(GROK_EXTRA, billing=dict(GROK_EXTRA["billing"], credit_usage_percent=10))
        accounts.add_credential(grok_sid, "open", "g1", extra=extra, donated_by=self.admin["id"])
        self._login("user@test.invalid")
        page = self.client.get("/sources")
        self.assertNotIn("Grok plan 1", page.text)
        # The gauge previews what linking an account would reach.
        self.assertIn("1 plan", page.text)
        pool_policy.set_admin_policy("grok", pool_shared=True, open_access=True)
        page = self.client.get("/sources")
        self.assertIn("Grok plan 1", page.text)

    def test_admin_guard_toggles_cover_every_guarded_pool(self):
        self._login("admin@test.invalid")
        page = self.client.get("/admin/pools")
        for kind in pool_policy.GUARD_KINDS:
            self.assertIn(f'action="/admin/pools/guard/{kind}"', page.text)
        csrf = self._csrf(page.text)
        sid = pool_policy.source_id("claude")
        acct = accounts.add_credential(sid, "claude", "s", extra={"access_token": "t"}, donated_by=self.user["id"])
        db.execute("UPDATE credentials SET pool_held_until=? WHERE id=?", ("2999-01-01T00:00:00Z", acct))
        saved = self.client.post("/admin/pools/guard/claude", data={"csrf_token": csrf}, follow_redirects=False)
        self.assertEqual(saved.status_code, 303)
        self.assertFalse(pool_policy.guard_enabled("claude"))
        self.assertIsNone(db.fetchone("SELECT pool_held_until FROM credentials WHERE id=?", (acct,))["pool_held_until"])
        saved = self.client.post(
            "/admin/pools/guard/claude", data={"csrf_token": csrf, "offpool_usage_guard": "1"}, follow_redirects=False
        )
        self.assertTrue(pool_policy.guard_enabled("claude"))
        missing = self.client.post("/admin/pools/guard/chatgpt", data={"csrf_token": csrf}, follow_redirects=False)
        self.assertEqual(missing.status_code, 404)

    def test_user_sharing_choices_and_global_disable(self):
        self._login("user@test.invalid")
        page = self.client.get("/sources")
        self.assertEqual(page.status_code, 200)
        self.assertIn("pool-controls", page.text)
        self.assertIn('data-dialog-open="share-grok-dialog"', page.text)
        self.assertIn('id="share-grok-dialog"', page.text)
        self.assertRegex(page.text, r'id="share-grok-dialog"[\s\S]*name="share" value="everyone" checked')
        self.assertNotIn("Use only my accounts", page.text)
        csrf = self._csrf(page.text)
        saved = self.client.post(
            "/sources/pool/grok/prefs",
            data={"csrf_token": csrf, "share": "contributors", "use": "mine"},
            follow_redirects=False,
        )
        self.assertEqual(saved.status_code, 303)
        self.assertEqual(saved.headers.get("location"), "/sources#pool-grok")
        self.assertEqual(pool_policy.user_pref(self.user["id"], "grok"), {"share": "contributors", "use": "mine"})
        self.assertFalse(pool_policy.uses_shared_pool(self.user["id"], "grok"))
        page = self.client.get("/sources")
        self.assertRegex(page.text, r'id="share-grok-dialog"[\s\S]*name="share" value="contributors" checked')
        self.assertIn("x.ai: Your requests use only your accounts; only people who also share x.ai accounts may use yours.", page.text)

        # A page rendered before share/use existed still posts own_only.
        legacy = self.client.post(
            "/sources/pool/chatgpt/prefs",
            data={"csrf_token": csrf, "own_only": "1"},
            follow_redirects=False,
        )
        self.assertEqual(legacy.status_code, 303)
        self.assertEqual(pool_policy.user_pref(self.user["id"], "chatgpt"), {"share": "private", "use": "mine"})
        bad = self.client.post(
            "/sources/pool/chatgpt/prefs",
            data={"csrf_token": csrf, "share": "friends"},
            follow_redirects=False,
        )
        self.assertEqual(bad.status_code, 400)

        pool_policy.set_admin_policy("grok", pool_shared=False, open_access=False)
        page = self.client.get("/sources")
        self.assertIn("Off site-wide", page.text)
        self.assertNotIn('id="share-grok-dialog"', page.text)
        self.assertIn('action="/sources/pool/chatgpt/prefs"', page.text)
        csrf = self._csrf(page.text)
        ignored = self.client.post(
            "/sources/pool/grok/prefs",
            data={"csrf_token": csrf, "share": "everyone", "use": "pool"},
            follow_redirects=False,
        )
        self.assertEqual(ignored.status_code, 303)
        self.assertFalse(pool_policy.uses_shared_pool(self.user["id"], "grok"))
        self.assertEqual(pool_policy.user_pref(self.user["id"], "grok"), {"share": "contributors", "use": "mine"})

    def test_sources_lists_only_accounts_the_user_is_routed_onto(self):
        grok_sid = grok_mod.ensure_grok_source()
        donor = accounts.create_user("donor-page@test.invalid", "password12", admin=False)
        shy = accounts.create_user("shy-page@test.invalid", "password12", admin=False)
        extra = dict(GROK_EXTRA, billing=dict(GROK_EXTRA["billing"], credit_usage_percent=10))
        accounts.add_credential(grok_sid, "open", "g1", extra=extra, donated_by=donor["id"])
        accounts.add_credential(grok_sid, "secret", "g2", extra=extra, donated_by=shy["id"])
        pool_policy.set_user_pref(shy["id"], "grok", share="private")
        accounts.add_credential(grok_sid, "mine", "g3", extra=extra, donated_by=self.user["id"])
        self._login("user@test.invalid")
        page = self.client.get("/sources")
        self.assertEqual(page.status_code, 200)
        self.assertIn("Grok plan 1", page.text)
        self.assertNotIn("Grok plan 2", page.text)
        self.assertIn("yours first, then the pool", page.text)
        self.assertIn("2 plans", page.text)

        self.client.get("/logout")
        self._login("admin@test.invalid")
        page = self.client.get("/sources")
        self.assertIn(">Private<", page.text)
        self.assertIn("is-unrouted", page.text)
        self.assertIn("2 plans", page.text)

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
        self.assertIn("Show only mine", page.text)
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
