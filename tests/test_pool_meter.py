"""Pooled plan meters: mean of routing percents, free/paid split, eligibility."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_TMP = tempfile.mkdtemp(prefix="fuse-pool-meter-")
os.environ["DATABASE_PATH"] = str(Path(_TMP) / "pool-meter.db")

from app import stats  # noqa: E402
from app import web  # noqa: E402
from app.sources.grok import account_from_tokens, apply_usage_guard, parse_billing_payload  # noqa: E402
from app.sources.pool_pick import USAGE_BUSY, grok_usage_percent  # noqa: E402


def _row(**kw):
    base = {
        "id": 1,
        "disabled": 0,
        "last_usage_percent": None,
        "pool_held_until": None,
        "extra": "{}",
    }
    base.update(kw)
    return base


def _future_hold():
    return (datetime.now(timezone.utc) + timedelta(hours=12)).strftime("%Y-%m-%dT%H:%M:%SZ")


class PoolMeterMathTest(unittest.TestCase):
    def test_two_fifties_are_fifty(self):
        m = stats.pool_meter([50.0, 50.0])
        self.assertTrue(m["available"])
        self.assertEqual(m["percent"], 50.0)
        self.assertEqual(m["percent_label"], "50%")
        self.assertEqual(m["meter"], 50)
        self.assertEqual(m["n"], 2)
        self.assertEqual(m["busy"], 0)
        self.assertEqual(m["count_label"], "2 plans")

    def test_empty_is_dash_not_zero(self):
        m = stats.pool_meter([])
        self.assertFalse(m["available"])
        self.assertIsNone(m["percent"])
        self.assertEqual(m["percent_label"], "—")
        self.assertEqual(m["meter"], 0)
        self.assertEqual(m["count_label"], "No plans")

    def test_zero_counts_and_busy_is_captioned(self):
        m = stats.pool_meter([0.0, 100.0, 98.0])
        self.assertEqual(m["percent"], 66.0)
        self.assertEqual(m["busy"], 2)
        self.assertEqual(m["count_label"], "3 plans · 2 at cap")

    def test_plan_mix_when_every_sample_is_named(self):
        m = stats.pool_meter([10.0, 20.0, 30.0], plans=["plus", "plus", "pro"])
        self.assertEqual(m["count_label"], "2 Plus · 1 Pro")

    def test_mixed_named_and_blank_falls_back_to_plans(self):
        m = stats.pool_meter([10.0, 20.0], plans=["plus", ""])
        self.assertEqual(m["count_label"], "2 plans")


class GrokPoolMeterTest(unittest.TestCase):
    def test_sources_route_has_grok_usage_helper(self):
        self.assertIs(web.grok_usage_percent, grok_usage_percent)

    def _extra(self, pct, *, token="tok", fetched=True, products=True):
        billing = {"credit_usage_percent": pct}
        if fetched:
            billing["fetched_at"] = "2026-09-01T00:00:00Z"
        if products:
            billing["products"] = [{"product": "GrokBuild", "usage_percent": pct}]
        return {"access_token": token, "billing": billing}

    @patch("app.stats.accounts.cred_extra")
    def test_mean_of_live_plans(self, cred_extra):
        rows = [
            _row(id=1, last_usage_percent=98.0),
            _row(id=2, last_usage_percent=57.0),
            _row(id=3, last_usage_percent=11.0),
        ]
        extras = {
            1: self._extra(98.0),
            2: self._extra(57.0),
            3: self._extra(11.0),
        }
        cred_extra.side_effect = lambda r: extras[r["id"]]
        m = stats.grok_pool_meter(rows)
        self.assertAlmostEqual(m["percent"], 55.3333333333)
        self.assertEqual(m["percent_label"], "55.3%")
        self.assertEqual(m["meter"], 55)
        self.assertEqual(m["busy"], 1)
        self.assertEqual(m["count_label"], "3 plans · 1 at cap")

    @patch("app.stats.accounts.cred_extra")
    def test_held_unauthed_and_no_sub_excluded(self, cred_extra):
        rows = [
            _row(id=1, last_usage_percent=10.0, pool_held_until=_future_hold()),
            _row(id=2, last_usage_percent=20.0),
            _row(id=3, last_usage_percent=90.0),
            _row(id=4, last_usage_percent=40.0),
        ]
        extras = {
            1: self._extra(10.0),
            2: self._extra(20.0, token=""),
            3: self._extra(90.0, fetched=False, products=False),
            4: self._extra(40.0),
        }
        cred_extra.side_effect = lambda r: extras[r["id"]]
        m = stats.grok_pool_meter(rows)
        self.assertEqual(m["percent"], 40.0)
        self.assertEqual(m["n"], 1)
        self.assertEqual(m["count_label"], "1 plan")

    @patch("app.stats.accounts.cred_extra")
    def test_authed_without_reading_is_not_zero(self, cred_extra):
        row = _row(id=1, last_usage_percent=None)
        cred_extra.return_value = {
            "access_token": "tok",
            "billing": {"fetched_at": "2026-09-01T00:00:00Z", "period_type": "WEEKLY"},
        }
        m = stats.grok_pool_meter([row])
        self.assertFalse(m["available"])
        self.assertEqual(m["percent_label"], "—")
        self.assertEqual(m["count_label"], "No reading yet")

    def test_product_cap_controls_routing_when_aggregate_is_lower(self):
        row = _row(last_usage_percent=10.0)
        extra = {"billing": {
            "fetched_at": "2026-09-01T00:00:00Z",
            "credit_usage_percent": 10.0,
            "products": [{"product": "GrokBuild", "usage_percent": 100.0}],
        }}
        self.assertEqual(grok_usage_percent(row, extra), 100.0)

    def test_current_snapshot_does_not_reuse_prior_period_percent(self):
        row = _row(last_usage_percent=98.0)
        extra = {"billing": {"fetched_at": "2026-09-01T00:00:00Z", "products": []}}
        self.assertIsNone(grok_usage_percent(row, extra))

    def test_billing_parser_clamps_percentages(self):
        parsed = parse_billing_payload({"config": {
            "creditUsagePercent": 130,
            "productUsage": [{"product": "GrokBuild", "usagePercent": -4}],
        }})
        self.assertEqual(parsed["credit_usage_percent"], 100.0)
        self.assertEqual(parsed["products"][0]["usage_percent"], 0.0)

    def test_account_identity_comes_from_access_token(self):
        import base64
        import json

        payload = base64.urlsafe_b64encode(json.dumps({"principal_id": "acct-1"}).encode()).decode().rstrip("=")
        self.assertEqual(account_from_tokens({"access_token": f"x.{payload}.y"}), "acct-1")

    def test_new_period_clears_stale_percent_and_hold(self):
        row = _row(
            last_usage_percent=98.0,
            pool_held_until="2026-09-05T00:00:00Z",
            pool_invokes=1,
        )
        extra = {"guard": {"last_percent": 98.0, "period_start": "old", "seen_invokes": 1}}
        _, held, percent = apply_usage_guard(
            row,
            extra,
            {"period_start": "new", "credit_usage_percent": None, "products": []},
            now=datetime(2026, 9, 4, tzinfo=timezone.utc),
        )
        self.assertIsNone(held)
        self.assertIsNone(percent)


class ChatGPTPoolMeterTest(unittest.TestCase):
    def _extra(self, plan, windows, credit=None):
        billing = {"plan_type": plan, "windows": windows}
        if credit is not None:
            billing["credit_usage_percent"] = credit
        return {"refresh_token": "r", "billing": billing}

    @patch("app.stats.accounts.cred_extra")
    def test_paid_uses_max_window_not_weekly_hero(self, cred_extra):
        rows = [_row(id=1), _row(id=2)]
        extras = {
            1: self._extra(
                "plus",
                [
                    {"percent": 100.0, "seconds": 18000},
                    {"percent": 16.0, "seconds": 604800},
                ],
                credit=16.0,
            ),
            2: self._extra(
                "plus",
                [
                    {"percent": 56.0, "seconds": 18000},
                    {"percent": 9.0, "seconds": 604800},
                ],
                credit=9.0,
            ),
        }
        cred_extra.side_effect = lambda r: extras[r["id"]]
        paid, free = stats.chatgpt_pool_meters(rows)
        self.assertEqual(paid["percent"], 78.0)
        self.assertEqual(paid["count_label"], "2 Plus · 1 at cap")
        self.assertEqual(free["percent_label"], "—")
        self.assertEqual(free["count_label"], "No plans")

    @patch("app.stats.accounts.cred_extra")
    def test_free_and_go_split_from_paid(self, cred_extra):
        rows = [_row(id=1), _row(id=2), _row(id=3), _row(id=4)]
        extras = {
            1: self._extra("free", [{"percent": 10.0, "seconds": 30 * 86400}]),
            2: self._extra("go", [{"percent": 30.0, "seconds": 30 * 86400}]),
            3: self._extra("plus", [{"percent": 80.0, "seconds": 18000}]),
            4: self._extra("", [{"percent": 20.0, "seconds": 18000}]),
        }
        cred_extra.side_effect = lambda r: extras[r["id"]]
        paid, free = stats.chatgpt_pool_meters(rows)
        self.assertEqual(paid["percent"], 50.0)
        self.assertEqual(paid["n"], 2)
        self.assertEqual(free["percent"], 20.0)
        self.assertEqual(free["count_label"], "1 Free · 1 Go")

    @patch("app.stats.accounts.cred_extra")
    def test_unauthed_excluded_zero_included(self, cred_extra):
        rows = [_row(id=1), _row(id=2)]
        extras = {
            1: {"refresh_token": "", "billing": {"plan_type": "plus", "windows": [{"percent": 90, "seconds": 18000}]}},
            2: self._extra("free", [{"percent": 0.0, "seconds": 30 * 86400}]),
        }
        cred_extra.side_effect = lambda r: extras[r["id"]]
        paid, free = stats.chatgpt_pool_meters(rows)
        self.assertEqual(paid["count_label"], "No plans")
        self.assertEqual(free["percent"], 0.0)
        self.assertEqual(free["percent_label"], "0%")
        self.assertEqual(free["count_label"], "1 Free")


class ZaiPoolMeterTest(unittest.TestCase):
    @patch("app.stats.accounts.cred_extra")
    def test_max_window_and_plan_label(self, cred_extra):
        row = _row(id=1, last_usage_percent=66.0)
        cred_extra.return_value = {
            "api_key": "k",
            "billing": {
                "plan_type": "lite",
                "credit_usage_percent": 66.0,
                "windows": [
                    {"percent": 8.0, "seconds": 18000},
                    {"percent": 66.0, "seconds": 604800},
                ],
            },
        }
        m = stats.zai_pool_meter([row])
        self.assertEqual(m["percent"], 66.0)
        self.assertEqual(m["count_label"], "1 Lite")

    @patch("app.stats.accounts.cred_extra")
    def test_held_excluded(self, cred_extra):
        row = _row(id=1, last_usage_percent=10.0, pool_held_until=_future_hold())
        cred_extra.return_value = {
            "api_key": "k",
            "billing": {"plan_type": "lite", "windows": [{"percent": 10.0, "seconds": 604800}]},
        }
        m = stats.zai_pool_meter([row])
        self.assertEqual(m["count_label"], "No plans")


class BillingViewOverrideTest(unittest.TestCase):
    def test_percent_override_beats_weekly_hero(self):
        view = stats.billing_view(
            {"credit_usage_percent": 16.0, "products": [{"product": "5-hour", "usage_percent": 100.0}]},
            percent=100.0,
        )
        self.assertEqual(view["percent"], 100.0)
        self.assertEqual(view["meter"], 100)
        self.assertEqual(view["percent_label"], "100%")
        self.assertEqual(view["products"][0]["percent"], 100.0)

    def test_fresh_snapshot_without_percent_is_unavailable(self):
        view = stats.billing_view(
            {"fetched_at": "2026-09-01T00:00:00Z", "period_type": "WEEKLY"},
            last_percent=98.0,
        )
        self.assertFalse(view["available"])
        self.assertEqual(view["percent_label"], "—")


class PoolCardTest(unittest.TestCase):
    def _chatgpt_extra(self, email="ada@example.com"):
        import base64
        import json

        claims = {"https://api.openai.com/profile": {"email": email, "name": "Ada"}}
        payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
        return {"access_token": f"x.{payload}.y", "refresh_token": "rt"}

    @patch("app.web.sources.stats.fuse_view", return_value={})
    def test_owner_sees_chatgpt_email(self, _fuse):
        extra = self._chatgpt_extra()
        card = web._pool_card(
            {"id": 5, "is_admin": 0},
            {"id": 140, "donated_by": 5, "label": "ChatGPT account"},
            kind="ChatGPT",
            index=1,
            extra=extra,
        )
        self.assertEqual(card["email"], "ada@example.com")
        self.assertTrue(card["mine"])

    @patch("app.web.sources.stats.fuse_view", return_value={})
    def test_other_contributor_does_not_see_chatgpt_email(self, _fuse):
        extra = self._chatgpt_extra()
        card = web._pool_card(
            {"id": 4, "is_admin": 0},
            {"id": 140, "donated_by": 5, "label": "ChatGPT account"},
            kind="ChatGPT",
            index=3,
            extra=extra,
        )
        self.assertEqual(card["email"], "")
        self.assertEqual(card["label"], "ChatGPT plan 3")
        self.assertFalse(card["mine"])

    @patch("app.web.sources.stats.fuse_view", return_value={})
    @patch("app.web.sources.accounts.get_user")
    def test_admin_sees_donor_email_when_provider_has_none(self, get_user, _fuse):
        class _Row:
            def __getitem__(self, key):
                if key != "email":
                    raise KeyError(key)
                return "donor@example.com"

        get_user.return_value = _Row()
        card = web._pool_card(
            {"id": 1, "is_admin": 1},
            {"id": 110, "donated_by": 5, "label": "Grok account"},
            kind="Grok",
            index=1,
            extra={"access_token": "tok"},
        )
        self.assertEqual(card["email"], "donor@example.com")


if __name__ == "__main__":
    unittest.main()
