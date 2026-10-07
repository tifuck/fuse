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


class CronDstAndMatchTest(unittest.TestCase):
    def test_gap_time_does_not_fire_that_day(self):
        # 2026-03-08 02:30 never appears on the America/New_York clock.
        nxt = cron.next_fire("30 2 * * *", "America/New_York", datetime(2026, 3, 8, tzinfo=timezone.utc))
        self.assertEqual(nxt, datetime(2026, 3, 9, 6, 30, tzinfo=timezone.utc))

    def test_ambiguous_time_fires_at_both_occurrences(self):
        # 2026-11-01 01:30 happens twice in America/New_York.
        times = cron.next_n("30 1 * * *", "America/New_York", datetime(2026, 11, 1, tzinfo=timezone.utc), 2)
        self.assertEqual(times, [
            datetime(2026, 11, 1, 5, 30, tzinfo=timezone.utc),
            datetime(2026, 11, 1, 6, 30, tzinfo=timezone.utc),
        ])

    def test_every_minute_covers_the_repeated_hour(self):
        times = cron.next_n("* * * * *", "America/New_York", datetime(2026, 11, 1, 5, 58, tzinfo=timezone.utc), 3)
        self.assertEqual(times, [
            datetime(2026, 11, 1, 5, 59, tzinfo=timezone.utc),
            datetime(2026, 11, 1, 6, 0, tzinfo=timezone.utc),
            datetime(2026, 11, 1, 6, 1, tzinfo=timezone.utc),
        ])

    def test_leap_day_fires(self):
        nxt = cron.next_fire("0 0 29 2 *", "UTC", datetime(2026, 3, 1, tzinfo=timezone.utc))
        self.assertEqual(nxt, datetime(2028, 2, 29, tzinfo=timezone.utc))

    def test_dom_dow_or_when_both_restricted(self):
        # POSIX: with both dom and dow restricted, either may match.
        nxt = cron.next_fire("0 9 1 * 1", "UTC", datetime(2026, 9, 14, 10, 0, tzinfo=timezone.utc))
        self.assertEqual(nxt, datetime(2026, 9, 21, 9, 0, tzinfo=timezone.utc))
        nxt = cron.next_fire("0 9 1 * 1", "UTC", datetime(2026, 9, 30, tzinfo=timezone.utc))
        self.assertEqual(nxt, datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc))
