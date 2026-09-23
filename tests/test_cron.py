from datetime import datetime, timezone
import unittest

from app import cron


class CronParseTest(unittest.TestCase):
    def test_daily_compiles(self):
        self.assertEqual(cron.compile_daily(9, 0), "0 9 * * *")

    def test_rejects_six_fields(self):
        with self.assertRaises(cron.CronError):
            cron.parse_cron("0 0 9 * * *")

    def test_next_fire_daily(self):
        after = datetime(2026, 9, 14, 8, 0, tzinfo=timezone.utc)
        nxt = cron.next_fire("0 9 * * *", "UTC", after)
        self.assertEqual(nxt.hour, 9)
        self.assertEqual(nxt.minute, 0)

    def test_star_every_minute_gap(self):
        gap = cron.implied_min_gap("* * * * *", "UTC", datetime(2026, 9, 14, tzinfo=timezone.utc), 5)
        self.assertEqual(gap, 60)

    def test_weekly_monday_bit(self):
        expr = cron.compile_weekly(9, 30, 1)  # Monday
        self.assertEqual(expr, "30 9 * * 1")
