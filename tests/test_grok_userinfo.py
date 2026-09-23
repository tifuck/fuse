from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
_TMP = tempfile.mkdtemp(prefix="fuse-grok-userinfo-")
os.environ["DATABASE_PATH"] = str(Path(_TMP) / "grok-userinfo.db")
os.environ.setdefault("APP_SECRET", "x" * 32)

from app import accounts, db  # noqa: E402
from app.sources import grok as grok_mod  # noqa: E402


class GrokUserinfoTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        db.execute("DELETE FROM credentials")
        db.execute("DELETE FROM api_keys")
        db.execute("DELETE FROM users")
        self.user = accounts.create_user("owner@test.invalid", "password12", admin=False)

    def test_parse_userinfo(self):
        profile = grok_mod.parse_userinfo_payload(
            {"email": "a@example.com", "name": "Ada", "sub": "p1"}
        )
        self.assertEqual(profile["email"], "a@example.com")
        self.assertEqual(profile["name"], "Ada")
        self.assertEqual(grok_mod.account_email({"email": "a@example.com"}), "a@example.com")
        self.assertEqual(grok_mod.parse_userinfo_payload({"email": "not-an-email"}), {})

    def test_store_tokens_keeps_email(self):
        sid = grok_mod.ensure_grok_source()
        cred_id = grok_mod.store_tokens(
            sid,
            {"access_token": "tok", "refresh_token": "rt", "expires_in": 3600},
            donated_by=self.user["id"],
            profile={"email": "linked@example.com", "name": "Linked"},
        )
        row = db.fetchone("SELECT * FROM credentials WHERE id=?", (cred_id,))
        extra = accounts.cred_extra(row)
        self.assertEqual(extra["email"], "linked@example.com")
        self.assertEqual(row["label"], "linked@example.com")

    async def test_refresh_billing_fetches_missing_email(self):
        sid = grok_mod.ensure_grok_source()
        cred_id = grok_mod.store_tokens(
            sid,
            {"access_token": "tok", "refresh_token": "rt", "expires_in": 3600},
            donated_by=self.user["id"],
        )
        row = db.fetchone("SELECT * FROM credentials WHERE id=?", (cred_id,))

        class _Resp:
            status_code = 200

            def json(self):
                return {"config": {"creditUsagePercent": 12, "currentPeriod": {"type": "MONTH"}}}

        class _Client:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def get(self, url, headers=None):
                return _Resp()

        with (
            patch("app.sources.grok.httpx.AsyncClient", return_value=_Client()),
            patch.object(grok_mod, "fetch_userinfo", new=AsyncMock(return_value={"email": "fetched@example.com"})),
        ):
            await grok_mod.refresh_billing(row, force=True)
        extra = accounts.cred_extra(db.fetchone("SELECT * FROM credentials WHERE id=?", (cred_id,)))
        self.assertEqual(extra["email"], "fetched@example.com")


if __name__ == "__main__":
    unittest.main()
