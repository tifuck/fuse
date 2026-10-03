"""Xiaomi MiMo Token Plan pool: browser login blob, console key, routing."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
_TMP = tempfile.mkdtemp(prefix="fuse-mimo-")
os.environ.setdefault("DATABASE_PATH", str(Path(_TMP) / "mimo.db"))
os.environ.setdefault("APP_SECRET", "x" * 32)

from app import accounts, db  # noqa: E402
from app.sources import mimo as mimo_mod  # noqa: E402
from app.sources import pick  # noqa: E402
from app.sources.pool_pick import mimo_usage_percent, reset_health  # noqa: E402

import pytest

# Uses the database: a disposable MariaDB schema shared by this module.
pytestmark = pytest.mark.usefixtures("isolated_db")


def _seal(private_b64: str, payload: dict) -> str:
    pad = "=" * (-len(private_b64) % 4)
    recip = X25519PrivateKey.from_private_bytes(base64.urlsafe_b64decode(private_b64 + pad))
    eph = X25519PrivateKey.generate()
    shared = eph.exchange(recip.public_key())
    key = hashlib.sha256(shared).digest()
    nonce = b"\x01" * 12
    ct = AESGCM(key).encrypt(nonce, json.dumps(payload).encode(), None)
    blob = eph.public_key().public_bytes_raw() + nonce + ct
    return base64.urlsafe_b64encode(blob).decode().rstrip("=")


class MimoParseTest(unittest.TestCase):
    def test_decrypt_login_blob(self):
        started = mimo_mod.start_login()
        blob = _seal(
            started["private_key"],
            {"sk": "tp-abc12345", "uid": "user-9", "url": "https://token-plan-ams.xiaomimimo.com/v1"},
        )
        data = mimo_mod.decrypt_payload(started["private_key"], blob)
        self.assertEqual(data["sk"], "tp-abc12345")
        self.assertEqual(mimo_mod.region_for_url(data["url"]), "ams")

    def test_is_mimo_model(self):
        self.assertTrue(mimo_mod.is_mimo_model("mimo-v2.6-pro"))
        self.assertTrue(mimo_mod.is_mimo_model("mimo-v2.5"))
        self.assertFalse(mimo_mod.is_mimo_model("gpt-5.4"))
        self.assertFalse(mimo_mod.is_mimo_model("kimi-for-coding"))

    def test_thinking_toggle(self):
        on = mimo_mod._chat_payload({"model": "mimo-v2.6-pro", "reasoning_effort": "high", "messages": []})
        self.assertEqual(on["thinking"], {"type": "enabled"})
        self.assertNotIn("reasoning_effort", on)
        off = mimo_mod._chat_payload({"model": "mimo-v2.6-pro", "reasoning_effort": "none", "messages": []})
        self.assertEqual(off["thinking"], {"type": "disabled"})

    def test_usage_percent(self):
        extra = {"billing": {"windows": [{"percent": 12}], "credit_usage_percent": 4}}
        self.assertEqual(mimo_usage_percent(extra), 12.0)

    def test_key_shape_rejects_payg(self):
        self.assertEqual(mimo_mod._key_shape("sk-not-a-plan"), "")
        self.assertTrue(mimo_mod._key_shape("tp-abc12345").startswith("tp-"))
        self.assertTrue(mimo_mod._key_shape("ttp-teamkey1").startswith("ttp-"))

    def test_authorize_url_is_xiaomi(self):
        url = mimo_mod.authorize_url("PUB", manual=True)
        self.assertTrue(url.startswith("https://platform.xiaomimimo.com/authorize?"))
        self.assertIn("pk=PUB", url)


class MimoPoolTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        reset_health()
        db.execute("DELETE FROM credentials")
        db.execute("DELETE FROM api_keys")
        db.execute("DELETE FROM users")
        self.alice = accounts.create_user("mimo-a@test.invalid", "password12", admin=False)
        self.bob = accounts.create_user("mimo-b@test.invalid", "password12", admin=False)
        self.sid = mimo_mod.ensure_mimo_source()

    def test_own_access_unlocks(self):
        extra = {
            "api_key": "tp-abc12345",
            "region": "cn",
            "billing": {"plan_type": "token plan", "fetched_at": "2026-01-01T00:00:00Z", "credit_usage_percent": 10},
        }
        accounts.add_credential(self.sid, "mine", "tp-abc12345", extra=extra, donated_by=self.alice["id"])
        self.assertTrue(mimo_mod.user_has_mimo(self.alice["id"]))
        self.assertFalse(mimo_mod.user_has_mimo(self.bob["id"]))

    async def test_pick_routes_mimo(self):
        extra = {
            "api_key": "tp-abc12345",
            "region": "sgp",
            "billing": {"plan_type": "token plan", "fetched_at": "2026-01-01T00:00:00Z"},
        }
        accounts.add_credential(self.sid, "mine", "tp-abc12345", extra=extra, donated_by=self.alice["id"])
        picked = await pick("mimo-v2.6-pro", allow_mimo=True)
        self.assertIsNotNone(picked)
        src, upstream = picked
        self.assertEqual(src.type_id, "mimo")
        self.assertEqual(upstream, "mimo-v2.6-pro")
        self.assertIsNone(await pick("mimo-v2.6-pro", allow_mimo=False))

    async def test_add_plan_key_uses_catalog_not_completion(self):
        async def fake_quota(key, region):
            self.assertEqual(key, "tp-abc12345")
            self.assertEqual(region, "sgp")
            return {"plan_type": "token plan", "credit_usage_percent": 3, "_models": ["mimo-v2.6-pro"]}, ""

        with patch("app.sources.mimo.fetch_quota", side_effect=fake_quota):
            cred_id, err = await mimo_mod.add_plan_key(self.sid, "tp-abc12345", self.alice["id"], region="sgp")
        self.assertEqual(err, "")
        self.assertTrue(cred_id)
        row = db.fetchone("SELECT * FROM credentials WHERE id=?", (cred_id,))
        extra = accounts.cred_extra(row)
        self.assertEqual(extra["region"], "sgp")
        self.assertEqual(extra["auth_kind"], "api")
        self.assertTrue(mimo_mod.billing_has_subscription(extra["billing"]))

    async def test_add_plan_key_rejects_sk(self):
        cred_id, err = await mimo_mod.add_plan_key(self.sid, "sk-paygkey", self.alice["id"], region="cn")
        self.assertIsNone(cred_id)
        self.assertIn("tp-", err)

    @patch("app.sources.mimo.note_pool_invoke")
    @patch("app.sources.mimo.openai_chat", new_callable=AsyncMock)
    @patch("app.sources.mimo.refresh_pool_quota", new_callable=AsyncMock)
    async def test_chat_uses_token_plan_host(self, _quota, chat, _invoke):
        chat.return_value = (
            200,
            {"id": "c", "usage": {"prompt_tokens": 8, "completion_tokens": 2, "prompt_tokens_details": {"cached_tokens": 5}}},
            None,
            {"prompt_tokens": 8, "completion_tokens": 2, "cached_tokens": 5},
            {},
        )
        extra = {
            "api_key": "tp-abc12345",
            "region": "cn",
            "base_url": "https://token-plan-cn.xiaomimimo.com/v1",
            "billing": {"plan_type": "token plan", "fetched_at": "2026-01-01T00:00:00Z"},
        }
        accounts.add_credential(self.sid, "mine", "tp-abc12345", extra=extra, donated_by=self.alice["id"])
        src = mimo_mod.MimoSource({"id": self.sid, "name": "Xiaomi"}, {}, [])
        result = await src.chat(
            {"model": "mimo-v2.6-flash", "messages": [{"role": "user", "content": "hi"}], "reasoning_effort": "high"},
            user_id=self.alice["id"],
        )
        self.assertEqual(result.status, 200)
        self.assertEqual(result.usage["cached_tokens"], 5)
        url = chat.await_args.args[1]
        headers = chat.await_args.args[2]
        body = chat.await_args.args[3]
        self.assertEqual(url, "https://token-plan-cn.xiaomimimo.com/v1/chat/completions")
        self.assertEqual(headers["X-Mimo-Source"], "mimocode-cli")
        self.assertEqual(body["thinking"], {"type": "enabled"})


if __name__ == "__main__":
    unittest.main()
