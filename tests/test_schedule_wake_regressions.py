"""Schedule/wake regressions: Stop pauses continuity schedules and the
scheduler admission guard refuses stopped ones; a vault-locked one-shot retry
keeps its grace exemption; sweep_wakes covers command-only threads.
Offline temporary SQLite DB."""
import sqlite3
import sys
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import chat_runs as runs, chat_store as store, chats, command_jobs, db, schedules
from app.cron import iso_utc, utcnow


class ScheduleStopTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="fuse-schedstop-")
        self.addCleanup(self.tmp.cleanup)
        self.conn = sqlite3.connect(str(Path(self.tmp.name) / "test.db"), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        db._init(self.conn)
        self.addCleanup(self.conn.close)
        patcher = patch.object(db, "connect", return_value=self.conn)
        patcher.start()
        self.addCleanup(patcher.stop)
        db.execute("INSERT INTO users(id,email,password_hash,created_at) VALUES(1,'test@invalid','unused',?)", (db.now(),))
        store.init_schema()
        with patch.object(command_jobs, "_schema_ready", False):
            command_jobs.ensure_schema()
        runs._stopping = False

    def tearDown(self):
        db._conn = None

    def add_schedule(self, *, fresh=0, thread_id=None, public="pub", **extra):
        now = db.now()
        row = {
            "public_id": public,
            "user_id": 1,
            "name": public,
            "enabled": 1,
            "paused": 0,
            "target": "chat",
            "thread_id": thread_id,
            "fresh_each_fire": fresh,
            "prompt": "hi",
            "model": "offline",
            "timezone": "UTC",
            "recur_kind": "every",
            "every_seconds": 3600,
            "next_fire_at": iso_utc(utcnow() + timedelta(hours=1)),
            "created_at": now,
            "updated_at": now,
        }
        row.update(extra)
        fields = ",".join(row)
        db.execute(
            f"INSERT INTO chat_schedules({fields}) VALUES({','.join('?' for _ in row)})",
            list(row.values()),
        )
        return db.fetchone("SELECT * FROM chat_schedules WHERE public_id=?", (public,))

    def test_root_stop_pauses_only_continuity_schedules(self):
        thread = chats.create_thread(1, model="offline", tools=[])
        tid = chats.resolve_thread_pk(1, thread["id"])
        run = store.start_turn(1, tid, "hello", thread["revision"], None)["run"]
        continuity = self.add_schedule(fresh=0, thread_id=tid, public="cont")
        fresh = self.add_schedule(fresh=1, thread_id=tid, public="fresh")
        other = chats.create_thread(1, model="offline", tools=[])
        elsewhere = self.add_schedule(fresh=0, thread_id=chats.resolve_thread_pk(1, other["id"]), public="other")

        self.assertTrue(store.request_cancel(1, run["id"]))
        self.assertEqual(db.fetchone("SELECT paused FROM chat_schedules WHERE id=?", (continuity["id"],))["paused"], 1)
        self.assertEqual(db.fetchone("SELECT paused FROM chat_schedules WHERE id=?", (fresh["id"],))["paused"], 0)
        self.assertEqual(db.fetchone("SELECT paused FROM chat_schedules WHERE id=?", (elsewhere["id"],))["paused"], 0)

    def test_scheduled_admission_guard(self):
        thread = chats.create_thread(1, model="offline", tools=[])
        tid = chats.resolve_thread_pk(1, thread["id"])
        schedule = self.add_schedule(fresh=0, thread_id=tid, public="guard")
        prompt = "scheduled prompt"

        # Enabled + unpaused admits.
        result = store.start_turn(1, tid, prompt, None, None, auto=True, scheduled=True, schedule_id=schedule["id"])
        self.assertFalse(result["idempotent"])
        store.finish_run(result["run"]["id"], {"content": "done"}, "completed")

        # Paused (e.g. by a Stop) refuses until explicit intent.
        db.execute("UPDATE chat_schedules SET paused=1 WHERE id=?", (schedule["id"],))
        thread = chats.get_thread(1, tid)
        with self.assertRaises(chats.ChatValidationError) as ctx:
            store.start_turn(1, tid, prompt, thread["revision"], None, auto=True, scheduled=True, schedule_id=schedule["id"])
        self.assertEqual(ctx.exception.status_code, 409)
        # A manual run-now is explicit intent and still admits.
        result = store.start_turn(
            1, tid, prompt, thread["revision"], None, auto=True, scheduled=True,
            schedule_id=schedule["id"], schedule_manual=True,
        )
        self.assertFalse(result["idempotent"])
        store.finish_run(result["run"]["id"], {"content": "done"}, "completed")

        # Disabled schedules never admit an automatic fire.
        db.execute("UPDATE chat_schedules SET enabled=0,paused=0 WHERE id=?", (schedule["id"],))
        thread = chats.get_thread(1, tid)
        with self.assertRaises(chats.ChatValidationError):
            store.start_turn(1, tid, prompt, thread["revision"], None, auto=True, scheduled=True, schedule_id=schedule["id"])
        # A deleted schedule admits nothing, manual included.
        thread = chats.get_thread(1, tid)
        with self.assertRaises(chats.ChatValidationError):
            store.start_turn(
                1, tid, prompt, thread["revision"], None, auto=True, scheduled=True,
                schedule_id=999, schedule_manual=True,
            )


class VaultLockedRetryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="fuse-vault-")
        self.addCleanup(self.tmp.cleanup)
        self.conn = sqlite3.connect(str(Path(self.tmp.name) / "test.db"), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        db._init(self.conn)
        self.addCleanup(self.conn.close)
        patcher = patch.object(db, "connect", return_value=self.conn)
        patcher.start()
        self.addCleanup(patcher.stop)
        db.execute("INSERT INTO users(id,email,password_hash,created_at) VALUES(1,'test@invalid','unused',?)", (db.now(),))
        store.init_schema()

    def tearDown(self):
        db._conn = None

    def test_tick_retry_keeps_vault_locked_grace_exemption(self):
        slot = iso_utc(utcnow() - timedelta(hours=7))  # past the 6h once-grace
        now = db.now()
        db.execute(
            """INSERT INTO chat_schedules(public_id,user_id,name,target,prompt,model,timezone,
                                         recur_kind,once_at,next_fire_at,created_at,updated_at)
               VALUES('vault',1,'vault','chat','retry the vault','offline','UTC','once',?,?,?,?)""",
            (slot, slot, now, now),
        )
        schedule_id = db.fetchone("SELECT id FROM chat_schedules WHERE public_id='vault'")["id"]
        db.execute(
            """INSERT INTO schedule_fires(schedule_id,user_id,trigger,status,slot_key,fire_id,started_at)
               VALUES(?,1,'scheduled','vault_locked',?,'fire-vault','2020-01-01T00:00:00Z')""",
            (schedule_id, slot),
        )
        item = schedules._row(db.fetchone("SELECT * FROM chat_schedules WHERE id=?", (schedule_id,)))
        fire_row = dict(db.fetchone("SELECT * FROM schedule_fires WHERE schedule_id=?", (schedule_id,)))
        self.assertTrue(schedules.fire_row_retryable(fire_row, item))
        self.assertTrue(schedules._past_grace(item, slot))

        with patch.object(schedules, "_fire", new_callable=AsyncMock) as fire:
            delay = runs.asyncio.run(schedules.tick())
        fire.assert_called_once()
        claimed = fire.call_args[0][1]
        self.assertEqual(claimed["status"], "vault_locked")
        # The stored row was requeued (existing behavior).
        self.assertEqual(
            db.fetchone("SELECT status FROM schedule_fires WHERE schedule_id=?", (schedule_id,))["status"],
            "queued",
        )

    def test_cooling_rows_do_not_starve_other_due_schedules(self):
        now = db.now()
        for i in range(10):
            slot = iso_utc(utcnow() - timedelta(hours=2, minutes=i))
            db.execute(
                """INSERT INTO chat_schedules(public_id,user_id,name,target,prompt,model,timezone,
                                             recur_kind,once_at,next_fire_at,created_at,updated_at)
                   VALUES(?,1,?,'chat','locked','offline','UTC','once',?,?,?,?)""",
                (f"locked{i}", f"locked{i}", slot, slot, now, now),
            )
            locked_id = db.fetchone("SELECT id FROM chat_schedules WHERE public_id=?", (f"locked{i}",))["id"]
            # Just retried: still inside RETRY_COOLDOWN_S.
            db.execute(
                """INSERT INTO schedule_fires(schedule_id,user_id,trigger,status,slot_key,fire_id,started_at)
                   VALUES(?,1,'scheduled','vault_locked',?,?,?)""",
                (locked_id, slot, f"fire-locked{i}", now),
            )
        due = iso_utc(utcnow() - timedelta(minutes=1))
        db.execute(
            """INSERT INTO chat_schedules(public_id,user_id,name,target,prompt,model,timezone,
                                         recur_kind,every_seconds,next_fire_at,created_at,updated_at)
               VALUES('normal',1,'normal','chat','hi','offline','UTC','every',3600,?,?,?)""",
            (due, now, now),
        )

        with patch.object(schedules, "_fire", new_callable=AsyncMock) as fire:
            delay = runs.asyncio.run(schedules.tick())
        fire.assert_called_once()
        self.assertEqual(fire.call_args[0][0]["public_id"], "normal")
        # Rows only waiting out their cooldown do not pin the 0.5s spin.
        self.assertEqual(delay, 5.0)


class SweepWakeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="fuse-sweep-")
        self.addCleanup(self.tmp.cleanup)
        self.conn = sqlite3.connect(str(Path(self.tmp.name) / "test.db"), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        db._init(self.conn)
        self.addCleanup(self.conn.close)
        patcher = patch.object(db, "connect", return_value=self.conn)
        patcher.start()
        self.addCleanup(patcher.stop)
        db.execute("INSERT INTO users(id,email,password_hash,created_at) VALUES(1,'test@invalid','unused',?)", (db.now(),))
        store.init_schema()
        with patch.object(command_jobs, "_schema_ready", False):
            command_jobs.ensure_schema()
        runs._stopping = False

    def tearDown(self):
        db._conn = None

    def finished_thread(self, *, stopped=False):
        thread = chats.create_thread(1, model="offline", tools=[])
        tid = chats.resolve_thread_pk(1, thread["id"])
        run = store.start_turn(1, tid, "hello", thread["revision"], None)["run"]
        if stopped:
            store.request_cancel(1, run["id"])
            store.finish_run(run["id"], {"content": "Stopped"}, "cancelled")
        else:
            store.finish_run(run["id"], {"content": "Done"}, "completed")
        return tid

    def command_job(self, tid, *, delivered=0):
        db.execute(
            """INSERT INTO command_jobs(id,user_id,host_id,thread_id,command,timeout_ms,status,
                                        out,out_bytes,notify,delivered,created_at,finished_at)
               VALUES(?,1,1,?,'ls',1000,'completed','',0,1,?,?,?)""",
            (f"job-{tid}-{delivered}", tid, delivered, db.now(), db.now()),
        )

    def test_sweep_covers_command_only_wakes(self):
        tid = self.finished_thread()
        self.command_job(tid)
        with patch.object(runs, "schedule_wake") as wake:
            runs.sweep_wakes()
        wake.assert_called_once_with(1, tid)

    def test_sweep_skips_stopped_threads(self):
        tid = self.finished_thread(stopped=True)
        self.command_job(tid)
        with patch.object(runs, "schedule_wake") as wake:
            runs.sweep_wakes()
        wake.assert_not_called()

    def test_sweep_ignores_delivered_jobs(self):
        tid = self.finished_thread()
        self.command_job(tid, delivered=1)
        with patch.object(runs, "schedule_wake") as wake:
            runs.sweep_wakes()
        wake.assert_not_called()


if __name__ == "__main__":
    unittest.main()
