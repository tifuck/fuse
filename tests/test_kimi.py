"""Kimi Code membership pool: OAuth tokens, quota parse, routing, key paste."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
_TMP = tempfile.mkdtemp(prefix="fuse-kimi-")
os.environ.setdefault("DATABASE_PATH", str(Path(_TMP) / "kimi.db"))
os.environ.setdefault("APP_SECRET", "x" * 32)

from app import accounts, db  # noqa: E402
from app.sources import kimi as kimi_mod  # noqa: E402
from app.sources import pick  # noqa: E402
from app.sources.pool_pick import kimi_usage_percent, reset_health  # noqa: E402

import pytest

# Uses the database: a disposable MariaDB schema shared by this module.
pytestmark = pytest.mark.usefixtures("isolated_db")


class KimiParseTest(unittest.TestCase):
    def test_device_verify_url_allows_kimi_hosts(self):
        url = kimi_mod.device_verify_url(
            {
                "user_code": "ABCD-1234",
                "verification_uri_complete": "https://www.kimi.ai/code/authorize_device?user_code=ABCD-1234",
                "_region": "global",
            }
        )
        self.assertEqual(url, "https://www.kimi.ai/code/authorize_device?user_code=ABCD-1234")

    def test_device_verify_url_falls_back_by_region(self):
        url = kimi_mod.device_verify_url({"user_code": "ABCD-1234", "_region": "global"})
        self.assertEqual(url, "https://www.kimi.ai/code/authorize_device?user_code=ABCD-1234")
        cn = kimi_mod.device_verify_url({"user_code": "WXYZ-9", "_region": "cn"})
        self.assertEqual(cn, "https://www.kimi.com/code/authorize_device?user_code=WXYZ-9")

    def test_is_kimi_model(self):
        self.assertTrue(kimi_mod.is_kimi_model("k3"))
        self.assertTrue(kimi_mod.is_kimi_model("k3-256k"))
        self.assertTrue(kimi_mod.is_kimi_model("kimi-for-coding"))
        self.assertTrue(kimi_mod.is_kimi_model("kimi-for-coding-highspeed"))
        self.assertFalse(kimi_mod.is_kimi_model("kimi-k3"))
        self.assertFalse(kimi_mod.is_kimi_model("gpt-5.4"))
        self.assertFalse(kimi_mod.is_kimi_model("glm-5.3"))

    def test_parse_usage_payload(self):
        parsed = kimi_mod.parse_usage_payload(
            {
                "usages": {
                    "limit_5h": {"used_ratio": 0.2, "reset_time": "2026-09-20T00:00:00Z"},
                    "limit_7d": {"used_ratio": 0.5},
                    "limit_month_total": {"used_ratio": 0.1},
                }
            }
        )
        self.assertEqual(parsed["five_hour_percent"], 20.0)
        self.assertEqual(parsed["weekly_percent"], 50.0)
        self.assertEqual(parsed["credit_usage_percent"], 50.0)
        self.assertEqual(len(parsed["windows"]), 3)
        self.assertTrue(kimi_mod.billing_has_subscription(parsed))

    def test_parse_userinfo(self):
        profile = kimi_mod.parse_userinfo_payload(
            {
                "user_id": "u_1",
                "email": "a@example.com",
                "nickname": "Moon",
                "user_level_name": "Plus",
            }
        )
        self.assertEqual(profile["account_id"], "u_1")
        self.assertEqual(profile["email"], "a@example.com")
        self.assertEqual(profile["plan_type"], "Plus")

    def test_effort_mapping(self):
        payload = kimi_mod._chat_payload({"model": "k3", "reasoning_effort": "medium", "messages": []})
        self.assertEqual(payload["reasoning_effort"], "high")
        off = kimi_mod._chat_payload({"model": "k3", "reasoning_effort": "none", "messages": []})
        self.assertNotIn("reasoning_effort", off)
        self.assertEqual(off["thinking"], {"type": "disabled"})

    def test_usage_percent_from_windows(self):
        extra = {"billing": {"windows": [{"percent": 12}, {"percent": 40}], "credit_usage_percent": 5}}
        self.assertEqual(kimi_usage_percent(extra), 40.0)

    def test_catalog_payload(self):
        rows = kimi_mod.parse_catalog_payload(
            {
                "data": [
                    {
                        "id": "k3",
                        "display_name": "K3",
                        "context_length": 262144,
                        "supports_reasoning": True,
                        "think_efforts": {"support": True, "valid_efforts": ["low", "high", "max"], "default_effort": "high"},
                    },
                    {
                        "id": "kimi-for-coding-highspeed",
                        "display_name": "K2.7 Code Highspeed",
                        "context_length": 262144,
                        "supports_thinking_type": "only",
                    },
                ]
            }
        )
        by_id = {row["id"]: row for row in rows}
        self.assertEqual(by_id["k3"]["efforts"], ("low", "high", "max"))
        self.assertEqual(by_id["k3"]["default_effort"], "high")
        self.assertEqual(by_id["kimi-for-coding-highspeed"]["efforts"], ())


class KimiPoolTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        reset_health()
        db.execute("DELETE FROM credentials")
        db.execute("DELETE FROM api_keys")
        db.execute("DELETE FROM users")
        self.alice = accounts.create_user("kimi-a@test.invalid", "password12", admin=False)
        self.bob = accounts.create_user("kimi-b@test.invalid", "password12", admin=False)
        self.sid = kimi_mod.ensure_kimi_source()

    def test_own_access_unlocks(self):
        extra = {
            "access_token": "tok",
            "refresh_token": "rt",
            "billing": {"plan_type": "Plus", "fetched_at": "2026-01-01T00:00:00Z", "windows": [{"percent": 1}]},
        }
        accounts.add_credential(self.sid, "mine", "tok", extra=extra, donated_by=self.alice["id"])
        self.assertTrue(kimi_mod.user_has_kimi(self.alice["id"]))
        self.assertFalse(kimi_mod.user_has_kimi(self.bob["id"]))

    async def test_pick_routes_k3(self):
        extra = {
            "access_token": "tok",
            "refresh_token": "rt",
            "billing": {"plan_type": "Plus", "fetched_at": "2026-01-01T00:00:00Z"},
        }
        accounts.add_credential(self.sid, "mine", "tok", extra=extra, donated_by=self.alice["id"])
        picked = await pick("k3", allow_kimi=True)
        self.assertIsNotNone(picked)
        src, upstream = picked
        self.assertEqual(src.type_id, "kimi")
        self.assertEqual(upstream, "k3")
        blocked = await pick("k3", allow_kimi=False)
        self.assertIsNone(blocked)
        or_hit = await pick("k3", allow_kimi=False, user_id=self.alice["id"])
        self.assertIsNone(or_hit)

    @patch("app.sources.kimi.note_pool_invoke")
    @patch("app.sources.kimi.openai_chat", new_callable=AsyncMock)
    @patch("app.sources.kimi.refresh_pool_quota", new_callable=AsyncMock)
    @patch("app.sources.kimi.refresh_due", new_callable=AsyncMock)
    async def test_chat_uses_coding_endpoint(self, _due, _quota, chat, _invoke):
        chat.return_value = (
            200,
            {"id": "c", "usage": {"prompt_tokens": 1, "completion_tokens": 1}},
            None,
            {"prompt_tokens": 1, "completion_tokens": 1, "cached_tokens": 0},
            {},
        )
        extra = {
            "access_token": "tok",
            "refresh_token": "rt",
            "region": "global",
            "billing": {"plan_type": "Plus", "fetched_at": "2026-01-01T00:00:00Z"},
        }
        cred_id = accounts.add_credential(self.sid, "mine", "tok", extra=extra, donated_by=self.alice["id"])
        src = kimi_mod.KimiSource({"id": self.sid, "name": "Kimi"}, {}, [])
        result = await src.chat(
            {"model": "k3", "messages": [{"role": "user", "content": "hi"}]},
            user_id=self.alice["id"],
        )
        self.assertEqual(result.status, 200)
        self.assertEqual(result.credential_id, cred_id)
        url = chat.await_args.args[1]
        self.assertTrue(url.endswith("/coding/v1/chat/completions"))
        self.assertIn("api.kimi.ai", url)

    @staticmethod
    def _entitled_extra():
        return {
            "access_token": "tok",
            "refresh_token": "rt",
            "region": "global",
            "billing": {"plan_type": "Plus", "fetched_at": "2026-01-01T00:00:00Z"},
        }

    @patch("app.sources.kimi.note_pool_invoke")
    @patch("app.sources.kimi.refresh_one", new_callable=AsyncMock)
    @patch("app.sources.kimi.openai_chat", new_callable=AsyncMock)
    @patch("app.sources.kimi.refresh_pool_quota", new_callable=AsyncMock)
    @patch("app.sources.kimi.refresh_due", new_callable=AsyncMock)
    async def test_capability_401_fails_over_without_auth_strike_or_refresh(
        self, _due, _quota, chat, refresh, _invoke
    ):
        denial = (
            401,
            {"error": {"message": "Your membership does not include the K3 capability"}},
            None,
            {},
            {},
        )
        ok = (
            200,
            {"id": "c", "usage": {"prompt_tokens": 3, "completion_tokens": 1}},
            None,
            {"prompt_tokens": 3, "completion_tokens": 1, "cached_tokens": 0},
            {},
        )
        chat.side_effect = [denial, ok]
        first = accounts.add_credential(
            self.sid, "a", "tok", extra=self._entitled_extra(), donated_by=self.alice["id"]
        )
        second = accounts.add_credential(
            self.sid, "b", "tok2", extra=self._entitled_extra(), donated_by=self.alice["id"]
        )
        src = kimi_mod.KimiSource({"id": self.sid, "name": "Kimi"}, {}, [])
        result = await src.chat(
            {"model": "k3", "messages": [{"role": "user", "content": "hi"}]},
            user_id=self.alice["id"],
        )
        self.assertEqual(result.status, 200)
        self.assertEqual(result.credential_id, second)
        # No wasted OAuth round trip for a tier denial, and no auth strike on
        # the valid first credential.
        refresh.assert_not_awaited()
        row = db.fetchone("SELECT disabled FROM credentials WHERE id=?", (first,))
        self.assertEqual(row["disabled"], 0)

    @patch("app.sources.kimi.note_pool_invoke")
    @patch("app.sources.kimi.refresh_one", new_callable=AsyncMock)
    @patch("app.sources.kimi.openai_chat", new_callable=AsyncMock)
    @patch("app.sources.kimi.refresh_pool_quota", new_callable=AsyncMock)
    @patch("app.sources.kimi.refresh_due", new_callable=AsyncMock)
    async def test_repeated_capability_401_never_disables_credentials(
        self, _due, _quota, chat, refresh, _invoke
    ):
        # Two 401s inside the auth-confirm window would disable the first
        # credential if they were treated as auth failures.
        denial = (
            401,
            {"error": {"message": "requested context length exceeds your plan entitlements"}},
            None,
            {},
            {},
        )
        chat.side_effect = [denial, denial, denial, denial]
        first = accounts.add_credential(
            self.sid, "a", "tok", extra=self._entitled_extra(), donated_by=self.alice["id"]
        )
        second = accounts.add_credential(
            self.sid, "b", "tok2", extra=self._entitled_extra(), donated_by=self.alice["id"]
        )
        src = kimi_mod.KimiSource({"id": self.sid, "name": "Kimi"}, {}, [])
        result = await src.chat(
            {"model": "k3", "messages": [{"role": "user", "content": "hi"}]},
            user_id=self.alice["id"],
        )
        # The entitlement denial surfaces as-is (401 + upstream message);
        # what must NOT happen is disabling either valid credential.
        self.assertEqual(result.status, 401)
        for cid in (first, second):
            row = db.fetchone("SELECT disabled FROM credentials WHERE id=?", (cid,))
            self.assertEqual(row["disabled"], 0, cid)
        refresh.assert_not_awaited()


class KimiPricingTest(unittest.TestCase):
    def test_k3_api_equivalent_uses_official_rates(self):
        from app.pricing import DEFAULT_KIMI_PRICING, ensure_default_prices

        ensure_default_prices()
        inp, cached, write, out = DEFAULT_KIMI_PRICING["k3"][0]
        self.assertEqual((inp, cached, write, out), ("3.00", "0.30", "3.00", "15.00"))
        user = accounts.create_user("kimi-price@test.invalid", "password12", admin=False)
        db.record_usage(
            user["id"],
            None,
            None,
            "k3",
            {"prompt_tokens": 1_000_000, "output_tokens": 1_000_000, "usage_status": "complete"},
            provider="kimi",
        )
        row = db.fetchone("SELECT estimated_cost_micros, pricing_known FROM usage_events WHERE model='k3' ORDER BY id DESC")
        self.assertEqual(row["pricing_known"], 1)
        self.assertEqual(row["estimated_cost_micros"], 18_000_000)


if __name__ == "__main__":
    unittest.main()
