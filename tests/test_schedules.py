"""Scheduled runs: interval caps, skip overlap, coding_override, run-now keys."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
_TMP = tempfile.mkdtemp(prefix="fuse-sched-")
os.environ.setdefault("DATABASE_PATH", str(Path(_TMP) / "sched.db"))
os.environ.setdefault("APP_SECRET", "x" * 32)

from app import accounts, chat_store, chats, db, schedules
from app.chat_store import start_turn
from app.cron import iso_utc, utcnow

import pytest

# Uses the database: a disposable MariaDB schema shared by this module.
pytestmark = pytest.mark.usefixtures("isolated_db")


def _user():
    db.connect()
    row = db.fetchone("SELECT * FROM users")
    if row:
        return row
    accounts.create_user("sched@test.invalid", "password12")
    return db.fetchone("SELECT * FROM users")


class ScheduleNormalizeTest(unittest.TestCase):
    def setUp(self):
        self.user = _user()

    def test_star_cron_too_frequent(self):
        with self.assertRaises(schedules.ScheduleError) as ctx:
            schedules._normalize(self.user["id"], {
                "name": "spam",
                "target": "chat",
                "prompt": "ping {{date}}",
                "model": "glm-5.3-flash",
                "timezone": "UTC",
                "recur_kind": "cron",
                "cron_expr": "* * * * *",
            })
        self.assertIn("frequent", str(ctx.exception.detail))

    def test_five_min_search_ok(self):
        item = schedules._normalize(self.user["id"], {
            "name": "search",
            "target": "chat",
            "prompt": "headlines {{date}}",
            "model": "glm-5.3-flash",
            "timezone": "UTC",
            "recur_kind": "every",
            "every_seconds": 300,
            "allow_coding": False,
        })
        self.assertEqual(item["every_seconds"], 300)

    def test_non_integer_interval_is_a_schedule_error(self):
        for field, value in (("every_seconds", [300]), ("weekdays", {"mon": 1})):
            raw = {"name": "x", "target": "chat", "prompt": "hi", "model": "m", "timezone": "UTC",
                   "recur_kind": "every" if field == "every_seconds" else "cron", "every_seconds": 300,
                   "at_local": "09:00", field: value}
            with self.subTest(field=field), self.assertRaises(schedules.ScheduleError):
                schedules._normalize(self.user["id"], raw)

    def test_five_min_coding_rejected(self):
        with self.assertRaises(schedules.ScheduleError):
            schedules._normalize(self.user["id"], {
                "name": "code",
                "target": "chat",
                "prompt": "lint",
                "model": "glm-5.3-flash",
                "timezone": "UTC",
                "recur_kind": "every",
                "every_seconds": 300,
                "allow_coding": True,
            })

    def test_unknown_var_rejected(self):
        with self.assertRaises(schedules.ScheduleError):
            schedules._normalize(self.user["id"], {
                "name": "x",
                "target": "chat",
                "prompt": "hi {{nope}}",
                "model": "glm-5.3-flash",
                "timezone": "UTC",
                "recur_kind": "every",
                "every_seconds": 3600,
            })

    def test_api_key_var_banned(self):
        with self.assertRaises(schedules.ScheduleError):
            schedules._normalize(self.user["id"], {
                "name": "x",
                "target": "chat",
                "prompt": "hi {{date}}",
                "model": "glm-5.3-flash",
                "timezone": "UTC",
                "recur_kind": "every",
                "every_seconds": 3600,
                "variables": {"api_key": "nope"},
            })

    def test_keyboard_var_ok(self):
        item = schedules._normalize(self.user["id"], {
            "name": "x",
            "target": "chat",
            "prompt": "layout {{keyboard}} {{date}}",
            "model": "glm-5.3-flash",
            "timezone": "UTC",
            "recur_kind": "every",
            "every_seconds": 3600,
            "variables": {"keyboard": "us"},
        })
        self.assertEqual(item["variables"]["keyboard"], "us")


class StartTurnScheduledTest(unittest.TestCase):
    def setUp(self):
        self.user = _user()
        self.thread = chats.create_thread(self.user["id"], title="s", model="glm-5.3-flash")

    def test_scheduled_without_override_is_not_coding(self):
        result = start_turn(
            self.user["id"], self.thread["id"], "hello", None, None,
            auto=True, scheduled=True, auto_title=False,
        )
        agent = result["run"]["request"].get("agent") or {}
        self.assertTrue(agent.get("scheduled"))
        self.assertFalse(agent.get("coding"))
        self.assertTrue(agent.get("auto"))

    def test_scheduled_coding_override(self):
        result = start_turn(
            self.user["id"], self.thread["id"], "hello", None, None,
            auto=True, scheduled=True, coding_override=True, auto_title=False,
        )
        agent = result["run"]["request"]["agent"]
        self.assertTrue(agent.get("coding"))
        self.assertTrue(agent.get("scheduled"))


class FireRowClassTest(unittest.TestCase):
    def test_manual_not_retryable(self):
        self.assertFalse(schedules.fire_row_retryable(
            {"slot_key": "manual:abc", "status": "queued", "run_id": None},
            {"recur_kind": "cron"},
        ))

    def test_once_vault_retryable(self):
        self.assertTrue(schedules.fire_row_retryable(
            {"slot_key": "2026-09-14T09:00:00Z", "status": "vault_locked", "run_id": None},
            {"recur_kind": "once", "every_seconds": None},
        ))

    def test_sanitize_redacts(self):
        text = schedules.sanitize_schedule_error("token sk-abcdef BEGIN CERT")
        self.assertNotIn("sk-", text)


class FireStatusPropagationTest(unittest.TestCase):
    """Terminal run statuses must flow onto schedule fires and last_status."""

    def setUp(self):
        self.user = _user()
        self.uid = self.user["id"]

    def _schedule(self):
        return schedules.create_schedule(self.uid, {
            "name": "status",
            "target": "chat",
            "prompt": "hi {{date}}",
            "model": "glm-5.3-flash",
            "timezone": "UTC",
            "recur_kind": "every",
            "every_seconds": 3600,
        })

    def _running_fire(self, item, run_id, thread_pk):
        slot = iso_utc(utcnow())
        db.execute(
            """INSERT INTO schedule_fires(schedule_id, user_id, trigger, status, slot_key, fire_id, started_at, run_id, thread_id)
               VALUES(?,?, 'scheduled', 'running', ?, 'firex', ?, ?, ?)""",
            (item["id"], self.uid, slot, db.now(), run_id, thread_pk),
        )
        db.execute(
            "UPDATE chat_schedules SET last_status='running', last_run_id=? WHERE id=?",
            (run_id, item["id"]),
        )
        return slot

    def _scheduled_run(self):
        thread = chats.create_thread(self.uid, title="s", model="glm-5.3-flash")
        result = start_turn(self.uid, thread["id"], "hello", None, None, auto=True, scheduled=True)
        return result["run"]

    def test_invalid_stored_variables_do_not_break_list_or_tick(self):
        import asyncio

        item = self._schedule()
        db.execute("UPDATE chat_schedules SET variables=?, next_fire_at=? WHERE id=?",
                   ('{"api_key": "x", "1bad": "y"}', iso_utc(utcnow()), item["id"]))
        listed = next(s for s in schedules.list_schedules(self.uid) if s["id"] == item["id"])
        self.assertEqual(listed["variables"], {})
        with patch.object(schedules, "_fire", new=AsyncMock()) as fire:
            asyncio.run(schedules.tick())
        self.assertIn(item["id"], [call.args[0]["id"] for call in fire.await_args_list])

    def test_failure_after_the_run_exists_keeps_the_fire_tracking_it(self):
        import asyncio

        from app import chat_runs

        item = self._schedule()
        slot = iso_utc(utcnow())
        db.execute(
            """INSERT INTO schedule_fires(schedule_id, user_id, trigger, status, slot_key, fire_id, started_at)
               VALUES(?,?, 'scheduled', 'queued', ?, 'fire-late', ?)""",
            (item["id"], self.uid, slot, db.now()),
        )
        fire = {"slot_key": slot, "fire_id": "fire-late", "status": "queued", "run_id": None}
        with patch("app.sources.pick", new=AsyncMock(return_value=object())), \
                patch.object(chat_runs, "start", new=AsyncMock(side_effect=RuntimeError("launch failed"))):
            asyncio.run(schedules._fire(item, fire, consume_slot=True))
        row = db.fetchone("SELECT status, run_id FROM schedule_fires WHERE schedule_id=? AND slot_key=?", (item["id"], slot))
        self.assertEqual(row["status"], "running")
        self.assertTrue(row["run_id"])
        # The run still executes later; its real outcome lands on the fire.
        run = chat_store.get_run(self.uid, row["run_id"])
        chat_store.finish_run(run["id"], dict(run["message"]), "completed")
        self.assertEqual(db.fetchone("SELECT status FROM schedule_fires WHERE schedule_id=? AND slot_key=?",
                                     (item["id"], slot))["status"], "completed")

    def test_finish_run_updates_fire_and_schedule(self):
        item = self._schedule()
        run = self._scheduled_run()
        self._running_fire(item, run["id"], run["thread_id"])
        chat_store.finish_run(run["id"], dict(run["message"]), "interrupted")
        fire = db.fetchone("SELECT * FROM schedule_fires WHERE schedule_id=?", (item["id"],))
        self.assertEqual(fire["status"], "interrupted")
        self.assertIsNotNone(fire["finished_at"])
        row = db.fetchone("SELECT last_status,last_error FROM chat_schedules WHERE id=?", (item["id"],))
        self.assertEqual(row["last_status"], "interrupted")

    def test_abort_active_updates_fire_and_schedule(self):
        item = self._schedule()
        run = self._scheduled_run()
        self._running_fire(item, run["id"], run["thread_id"])
        self.assertTrue(chat_store.abort_active(run["id"], "cancelled"))
        fire = db.fetchone("SELECT status FROM schedule_fires WHERE schedule_id=?", (item["id"],))
        self.assertEqual(fire["status"], "cancelled")
        self.assertEqual(
            db.fetchone("SELECT last_status FROM chat_schedules WHERE id=?", (item["id"],))["last_status"],
            "cancelled",
        )

    def test_reconcile_marks_interrupted_after_restart(self):
        item = self._schedule()
        run = self._scheduled_run()
        self._running_fire(item, run["id"], run["thread_id"])
        # Restart recovery marks the run interrupted without touching fires.
        db.execute("UPDATE chat_runs SET status='interrupted' WHERE id=?", (run["id"],))
        schedules.reconcile()
        fire = db.fetchone("SELECT status,finished_at FROM schedule_fires WHERE schedule_id=?", (item["id"],))
        self.assertEqual(fire["status"], "interrupted")
        self.assertIsNotNone(fire["finished_at"])
        self.assertEqual(
            db.fetchone("SELECT last_status FROM chat_schedules WHERE id=?", (item["id"],))["last_status"],
            "interrupted",
        )

    def test_reconcile_leaves_active_runs_alone(self):
        item = self._schedule()
        run = self._scheduled_run()
        self._running_fire(item, run["id"], run["thread_id"])
        schedules.reconcile()
        fire = db.fetchone("SELECT status,finished_at FROM schedule_fires WHERE schedule_id=?", (item["id"],))
        self.assertEqual(fire["status"], "running")
        self.assertIsNone(fire["finished_at"])

    def test_reconcile_handles_lost_run(self):
        item = self._schedule()
        thread = chats.create_thread(self.uid, title="s", model="glm-5.3-flash")
        self._running_fire(item, "run-that-vanished", chats.resolve_thread_pk(self.uid, thread["id"]))
        schedules.reconcile()
        fire = db.fetchone("SELECT status,error FROM schedule_fires WHERE schedule_id=?", (item["id"],))
        self.assertEqual(fire["status"], "interrupted")
        self.assertEqual(fire["error"], "run lost")

    def test_reconcile_fixes_stale_schedule_last_status(self):
        item = self._schedule()
        run = self._scheduled_run()
        db.execute(
            "UPDATE chat_schedules SET last_status='running', last_run_id=? WHERE id=?",
            (run["id"], item["id"]),
        )
        db.execute("UPDATE chat_runs SET status='completed' WHERE id=?", (run["id"],))
        schedules.reconcile()
        self.assertEqual(
            db.fetchone("SELECT last_status FROM chat_schedules WHERE id=?", (item["id"],))["last_status"],
            "completed",
        )

    def test_sync_run_status_noop_for_unscheduled_run(self):
        run = self._scheduled_run()
        with db.tx() as conn:
            schedules.sync_run_status(conn, self.uid, run["id"], "completed", "")
        self.assertIsNone(db.fetchone("SELECT id FROM schedule_fires WHERE run_id=?", (run["id"],)))


class FinishFireConfigTest(unittest.TestCase):
    """A fire finishing after a PATCH must not revert the new cadence."""

    def setUp(self):
        self.user = _user()

    def _schedule(self):
        return schedules.create_schedule(self.user["id"], {
            "name": "clobber",
            "target": "chat",
            "prompt": "hi {{date}}",
            "model": "glm-5.3-flash",
            "timezone": "UTC",
            "recur_kind": "every",
            "every_seconds": 3600,
        })

    def test_finish_fire_keeps_concurrent_patch(self):
        item = self._schedule()
        slot = item["next_fire_at"]
        patched = schedules.patch_schedule(self.user["id"], item["public_id"], {"every_seconds": 600})
        schedules._finish_fire(item, {"slot_key": slot}, "completed", "", None, "run-c", consume=True)
        row = db.fetchone("SELECT next_fire_at, last_status FROM chat_schedules WHERE id=?", (item["id"],))
        self.assertEqual(row["next_fire_at"], patched["next_fire_at"])
        self.assertEqual(row["last_status"], "completed")

    def test_finish_fire_consumes_unmodified_slot(self):
        item = self._schedule()
        slot = item["next_fire_at"]
        schedules._finish_fire(item, {"slot_key": slot}, "completed", "", None, "run-c", consume=True)
        row = db.fetchone("SELECT next_fire_at, enabled FROM chat_schedules WHERE id=?", (item["id"],))
        self.assertNotEqual(row["next_fire_at"], slot)
        self.assertTrue(row["enabled"])

    def test_catchup_skip_jumps_past_every_missed_slot(self):
        item = self._schedule()
        stale = iso_utc(utcnow() - timedelta(hours=24))
        schedules._finish_fire(item, {"slot_key": stale}, "skipped_catchup", "missed fire window", None, None, consume=True)
        row = db.fetchone("SELECT next_fire_at FROM chat_schedules WHERE id=?", (item["id"],))
        self.assertGreater(row["next_fire_at"], iso_utc(utcnow()))

    def test_finish_fire_does_not_reenable_disabled_schedule(self):
        item = self._schedule()
        slot = item["next_fire_at"]
        schedules.patch_schedule(self.user["id"], item["public_id"], {"enabled": False})
        schedules._finish_fire(item, {"slot_key": slot}, "completed", "", None, "run-c", consume=True)
        row = db.fetchone("SELECT enabled, next_fire_at FROM chat_schedules WHERE id=?", (item["id"],))
        self.assertFalse(row["enabled"])
        self.assertIsNone(row["next_fire_at"])

    def test_retry_cooldown(self):
        now = utcnow()
        self.assertFalse(schedules._retry_due({"started_at": iso_utc(now)}))
        self.assertTrue(schedules._retry_due({"started_at": iso_utc(now - timedelta(seconds=120))}))
        self.assertTrue(schedules._retry_due({"started_at": ""}))

    def test_string_flag_rejected(self):
        with self.assertRaises(schedules.ScheduleError):
            schedules._normalize(self.user["id"], {
                "name": "x", "target": "chat", "prompt": "hi", "model": "m",
                "timezone": "UTC", "recur_kind": "every", "every_seconds": 3600,
                "enabled": "false",
            })

    def test_reconcile_closes_runless_running(self):
        item = self._schedule()
        db.execute("UPDATE chat_schedules SET last_status='running', last_run_id='' WHERE id=?", (item["id"],))
        schedules.reconcile()
        row = db.fetchone("SELECT last_status, last_error FROM chat_schedules WHERE id=?", (item["id"],))
        self.assertEqual((row["last_status"], row["last_error"]), ("interrupted", "no run started"))


class FireDeletedScheduleTest(unittest.IsolatedAsyncioTestCase):
    async def test_fire_does_not_start_after_schedule_deleted(self):
        user = _user()
        item = schedules.create_schedule(user["id"], {
            "name": "doomed", "target": "chat", "prompt": "hi {{date}}",
            "model": "glm-5.3-flash", "timezone": "UTC",
            "recur_kind": "every", "every_seconds": 3600,
        })
        fire = {"slot_key": item["next_fire_at"], "fire_id": "fire-d", "status": "queued", "run_id": None}
        with patch("app.sources.pick", new=AsyncMock(return_value=("src", "m"))):
            db.execute("DELETE FROM chat_schedules WHERE id=?", (item["id"],))
            await schedules._fire(item, fire, consume_slot=True)
        self.assertEqual(
            db.fetchone("SELECT COUNT(*) AS n FROM chat_threads WHERE schedule_id=?", (item["id"],))["n"], 0,
        )
