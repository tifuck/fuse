"""Paced background usage walk: paid accounts only, oldest first, one hop."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
_TMP = tempfile.mkdtemp(prefix="fuse-usage-walk-")
os.environ.setdefault("DATABASE_PATH", str(Path(_TMP) / "usage-walk.db"))
os.environ.setdefault("APP_SECRET", "x" * 32)

from app import accounts, db  # noqa: E402
from app.sources import chatgpt as chatgpt_mod  # noqa: E402
from app.sources import grok as grok_mod  # noqa: E402
from app.sources import usage_walk  # noqa: E402
from app.sources import zai as zai_mod  # noqa: E402

import pytest

# Uses the database: a disposable MariaDB schema shared by this module.
pytestmark = pytest.mark.usefixtures("isolated_db")


class ParseTsTest(unittest.TestCase):
    def test_parse_ts_accepts_offsets_and_z(self):
        from datetime import datetime, timedelta, timezone

        self.assertIsNone(usage_walk._parse_ts(""))
        self.assertIsNone(usage_walk._parse_ts("not-a-time"))
        utc = usage_walk._parse_ts("2026-03-01T00:00:00Z")
        self.assertEqual(utc, datetime(2026, 3, 1, tzinfo=timezone.utc))
        naive = usage_walk._parse_ts("2026-03-01T00:00:00")
        self.assertEqual(naive.utcoffset(), timedelta(0))
        # A negative offset must parse (not sort as oldest-forever).
        west = usage_walk._parse_ts("2026-03-01T05:00:00-05:00")
        self.assertIsNotNone(west)
        self.assertEqual(west.astimezone(timezone.utc), datetime(2026, 3, 1, 10, 0, tzinfo=timezone.utc))


class UsageWalkTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        db.execute("DELETE FROM credentials")
        db.execute("DELETE FROM api_keys")
        db.execute("DELETE FROM users")
        self.user = accounts.create_user("walk@test.invalid", "password12", admin=False)
        self.grok_sid = grok_mod.ensure_grok_source()
        self.chatgpt_sid = chatgpt_mod.ensure_chatgpt_source()
        self.zai_sid = zai_mod.ensure_zai_source()

    def _add(self, sid, label, extra):
        return accounts.add_credential(sid, label, "tok", extra=extra, donated_by=self.user["id"])

    def test_skips_free_chatgpt(self):
        self._add(
            self.chatgpt_sid,
            "free",
            {
                "access_token": "tok",
                "billing": {"plan_type": "free", "fetched_at": "2026-01-01T00:00:00Z", "windows": []},
            },
        )
        self._add(
            self.grok_sid,
            "paid",
            {
                "access_token": "tok",
                "billing": {"fetched_at": "2026-01-02T00:00:00Z", "period_type": "month"},
            },
        )
        kinds = [kind for kind, _ in usage_walk.usage_targets()]
        self.assertEqual(kinds, ["grok"])

    def test_oldest_checked_is_first(self):
        newer = self._add(
            self.grok_sid,
            "newer",
            {
                "access_token": "tok",
                "billing": {"checked_at": "2026-03-01T00:00:00Z", "fetched_at": "2026-03-01T00:00:00Z", "period_type": "month"},
            },
        )
        older = self._add(
            self.zai_sid,
            "older",
            {
                "api_key": "zk",
                "billing": {"checked_at": "2026-01-01T00:00:00Z", "fetched_at": "2026-01-01T00:00:00Z", "plan_type": "pro"},
            },
        )
        targets = usage_walk.usage_targets()
        self.assertEqual([int(row["id"]) for _, row in targets], [older, newer])

    async def test_walk_next_refreshes_only_the_stalest(self):
        grok_id = self._add(
            self.grok_sid,
            "g",
            {
                "access_token": "tok",
                "billing": {"checked_at": "2026-01-01T00:00:00Z", "fetched_at": "2026-01-01T00:00:00Z", "period_type": "month"},
            },
        )
        self._add(
            self.zai_sid,
            "z",
            {
                "api_key": "zk",
                "billing": {"checked_at": "2026-02-01T00:00:00Z", "fetched_at": "2026-02-01T00:00:00Z", "plan_type": "pro"},
            },
        )
        with (
            patch.object(usage_walk.grok_mod, "refresh_billing", new=AsyncMock()) as grok,
            patch.object(usage_walk.zai_mod, "refresh_quota", new=AsyncMock()) as zai,
            patch.object(usage_walk.chatgpt_mod, "refresh_usage", new=AsyncMock()) as chatgpt,
        ):
            hit = await usage_walk.walk_next()
        self.assertEqual(hit, f"grok:{grok_id}")
        grok.assert_awaited_once()
        self.assertTrue(grok.await_args.kwargs.get("force"))
        zai.assert_not_awaited()
        chatgpt.assert_not_awaited()

    async def test_walk_next_noop_without_accounts(self):
        self.assertIsNone(await usage_walk.walk_next())


if __name__ == "__main__":
    unittest.main()
