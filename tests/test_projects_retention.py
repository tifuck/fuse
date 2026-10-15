"""Project colors/logos/archive, deep chat forks, and automatic cleanup."""
import json
import re
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from app import chat_store as store, chats, db, hosts, retention
from tests.mariadb_fixture import copy_foreign_keys, isolated_database

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def ago(**delta) -> str:
    return (datetime.now(timezone.utc) - timedelta(**delta)).strftime("%Y-%m-%dT%H:%M:%SZ")


class ProjectsRetentionTest(unittest.TestCase):
    def setUp(self):
        fixture = isolated_database()
        fixture.__enter__()
        self.addCleanup(fixture.__exit__, None, None, None)
        copy_foreign_keys("chat_recaps", "project_logos")
        store.invalidate_committed_cursors()
        db.execute("INSERT INTO users(id,email,password_hash,created_at) VALUES(1,'test@invalid','unused',?)", (db.now(),))
        db.execute(
            """INSERT INTO user_hosts(id,user_id,alias,hostname,username,auth_type,secret,created_at,updated_at)
               VALUES(7,1,'box','box.local','dev','agent','',?,?)""",
            (db.now(), db.now()),
        )

    def project(self, name="api", **extra):
        return hosts.add_project(1, 7, {"name": name, "remote_path": "/srv/" + name, **extra})

    def session(self, project):
        thread = chats.create_thread(1, model="offline", kind="project", host_id=7, project_id=project["id"])
        return thread, chats.resolve_thread_pk(1, thread["id"])

    # -- colors and logos -------------------------------------------------

    def test_new_projects_pick_a_palette_color_and_keep_an_edit(self):
        made = self.project()
        self.assertIn(made["color"], hosts.PROJECT_COLORS)
        self.assertEqual(hosts.patch_project(1, 7, made["id"], {"color": "#12ab34"})["color"], "#12AB34")
        with self.assertRaises(hosts.HostError):
            hosts.patch_project(1, 7, made["id"], {"color": "red"})
        chosen = self.project("web", color="#000000")
        self.assertEqual(chosen["color"], "#000000")
        thread, _ = self.session(made)
        listed = next(t for t in chats.list_threads(1) if t["id"] == thread["id"])
        self.assertEqual(listed["project_color"], "#12AB34")
        sidebar = next(p for p in chats.list_sidebar_projects(1) if p["id"] == made["id"])
        self.assertEqual((sidebar["color"], sidebar["sessions"]), ("#12AB34", 1))

    def test_legacy_projects_get_a_stable_color(self):
        made = self.project()
        db.execute("UPDATE user_projects SET color='' WHERE id=?", (made["id"],))
        first = hosts.get_project(1, 7, made["id"])["color"]
        self.assertEqual(first, hosts.get_project(1, 7, made["id"])["color"])
        self.assertIn(first, hosts.PROJECT_COLORS)

    def test_logo_validates_type_and_size_and_bumps_revision(self):
        made = self.project()
        with self.assertRaises(hosts.HostError):
            hosts.set_project_logo(1, made["id"], "image/png", b"GIF89a....")
        with self.assertRaises(hosts.HostError):
            hosts.set_project_logo(1, made["id"], "image/svg+xml", b"<svg/>")
        with self.assertRaises(hosts.HostError):
            hosts.set_project_logo(1, made["id"], "image/png", PNG + b"\x00" * hosts.LOGO_MAX_BYTES)
        first = hosts.set_project_logo(1, made["id"], "image/png", PNG)["logo_url"]
        second = hosts.set_project_logo(1, made["id"], "image/png", PNG)["logo_url"]
        self.assertNotEqual(first, second)
        self.assertEqual(hosts.project_logo(1, made["id"]), ("image/png", PNG))
        self.assertIsNone(hosts.project_logo(2, made["id"]))
        thread, _ = self.session(made)
        listed = next(t for t in chats.list_threads(1) if t["id"] == thread["id"])
        self.assertEqual(listed["project_logo_url"], second)
        self.assertEqual(hosts.clear_project_logo(1, made["id"])["logo_url"], "")
        self.assertIsNone(hosts.project_logo(1, made["id"]))

    # -- archive and delete -----------------------------------------------

    def test_archiving_a_project_takes_and_returns_only_its_own_sessions(self):
        made = self.project()
        kept, kept_pk = self.session(made)
        earlier, _ = self.session(made)
        chats.update_thread(1, earlier["id"], archived=True)
        hosts.patch_project(1, 7, made["id"], {"archived": True})
        self.assertTrue(hosts.get_project(1, 7, made["id"])["archived"])
        self.assertTrue(chats.get_thread(1, kept["id"])["archived"])
        hosts.patch_project(1, 7, made["id"], {"archived": False})
        self.assertFalse(chats.get_thread(1, kept["id"])["archived"])
        self.assertTrue(chats.get_thread(1, earlier["id"])["archived"])  # archived by hand before

        hosts.patch_project(1, 7, made["id"], {"archived": True})
        chats.update_thread(1, kept["id"], archived=False)
        self.assertFalse(hosts.get_project(1, 7, made["id"])["archived"])  # a restored session restores its project

    def test_activity_unarchives_but_settings_changes_do_not(self):
        thread = chats.create_thread(1, model="offline")
        chats.update_thread(1, thread["id"], archived=True)
        chats.update_thread(1, thread["id"], model="other")
        self.assertTrue(chats.get_thread(1, thread["id"])["archived"])
        current = chats.get_thread(1, thread["id"])
        store.start_turn(1, thread["id"], "hello", current["revision"], "send-archived")
        self.assertFalse(chats.get_thread(1, thread["id"])["archived"])

        edited = chats.create_thread(1, model="offline")
        chats.update_thread(1, edited["id"], messages=[{"role": "user", "content": "hi"}])
        chats.update_thread(1, edited["id"], archived=True)
        same = chats.get_thread(1, edited["id"])["messages"]
        chats.update_thread(1, edited["id"], messages=same)
        self.assertTrue(chats.get_thread(1, edited["id"])["archived"])  # no-op save
        chats.update_thread(1, edited["id"], messages=[])
        self.assertFalse(chats.get_thread(1, edited["id"])["archived"])  # delete/undo/edit

        made = self.project()
        session, _ = self.session(made)
        hosts.patch_project(1, 7, made["id"], {"archived": True})
        current = chats.get_thread(1, session["id"])
        store.start_turn(1, session["id"], "hello", current["revision"], "send-project", scheduled=True)
        self.assertFalse(chats.get_thread(1, session["id"])["archived"])
        self.assertFalse(hosts.get_project(1, 7, made["id"])["archived"])

    def test_delete_project_deletes_or_detaches_sessions(self):
        doomed = self.project("doomed")
        thread, _ = self.session(doomed)
        self.assertTrue(hosts.delete_project(1, 7, doomed["id"]))
        self.assertIsNone(chats.get_thread(1, thread["id"]))
        kept = self.project("kept")
        thread, _ = self.session(kept)
        self.assertTrue(hosts.delete_project(1, 7, kept["id"], sessions="keep"))
        after = chats.get_thread(1, thread["id"])
        self.assertEqual((after["kind"], after["project_id"]), ("chat", None))

    def test_delete_project_is_all_or_nothing(self):
        live = self.project("live")
        first, _ = self.session(live)
        second, _ = self.session(live)
        real_event = store.event
        deleted = []

        def failing_event(conn, user_id, kind, *args, **kwargs):
            if kind == "thread.deleted":
                deleted.append(kind)
                if len(deleted) == 2:
                    raise RuntimeError("event write failed")
            return real_event(conn, user_id, kind, *args, **kwargs)

        # A failure after the first session is gone must not leave it deleted
        # while the project and the other session survive.
        with patch.object(store, "event", failing_event), self.assertRaises(RuntimeError):
            hosts.delete_project(1, 7, live["id"])
        self.assertIsNotNone(hosts.get_project(1, 7, live["id"]))
        self.assertIsNotNone(chats.get_thread(1, first["id"]))
        self.assertIsNotNone(chats.get_thread(1, second["id"]))
        # A running session is cancelled and deleted with the project.
        current = chats.get_thread(1, first["id"])
        run = store.start_turn(1, first["id"], "hello", current["revision"], "send-live")["run"]
        with patch("app.chat_runs.cancel") as cancel:
            self.assertTrue(hosts.delete_project(1, 7, live["id"]))
        cancel.assert_called_once_with(run["id"])
        self.assertIsNone(chats.get_thread(1, first["id"]))
        self.assertIsNone(hosts.get_project(1, 7, live["id"]))

    # -- fork -------------------------------------------------------------

    def _thread_with_subagent_and_command(self):
        thread = chats.create_thread(1, model="offline")
        pk = chats.resolve_thread_pk(1, thread["id"])
        turn = store.start_turn(1, pk, "Run the tests in a subagent", thread["revision"], "first")
        root = turn["run"]["id"]
        child = "11111111-2222-4333-8444-555555555555"
        job = "abcdef123456"
        db.execute(
            """INSERT INTO chat_runs(id,user_id,thread_id,status,request,message_id,message,created_at,updated_at,
                   parent_run_id,root_run_id,agent_depth,provider_messages)
               VALUES(?,1,?,'completed',?,?,?,?,?,?,?,1,?)""",
            (child, pk, json.dumps({"thread_id": pk, "run_id": child, "agent": {"description": "tests"}}),
             "99999999-2222-4333-8444-555555555555", json.dumps({"content": "All green", "tools": []}),
             db.now(), db.now(), root, root, json.dumps([{"role": "user", "content": f"task_id: {child}"}])),
        )
        db.execute(
            """INSERT INTO command_jobs(id,user_id,host_id,thread_id,run_id,command,cwd,background,timeout_ms,status,created_at,out)
               VALUES(?,1,7,?,?,'pytest','/srv',1,1000,'completed',?,'3 passed')""",
            (job, pk, child, db.now()),
        )
        store.finish_run(root, {"content": f"Spawned. task_id: {child}", "tools": [
            {"id": "call-1", "name": "spawn_subagent", "status": "done", "child_id": child},
            {"id": "call-2", "name": "run_terminal_command", "status": "done", "job_id": job, "background": True},
        ]}, "completed")
        return thread, pk, root, child, job

    def test_fork_copies_runs_and_commands_under_new_ids(self):
        thread, pk, root, child, job = self._thread_with_subagent_and_command()
        source = chats.get_thread(1, thread["id"])
        fork = chats.fork_thread(1, thread["id"], expected_revision=source["revision"])
        fork_pk = chats.resolve_thread_pk(1, fork["id"])
        self.assertNotEqual(fork_pk, pk)
        self.assertTrue(fork["title"].endswith("(fork)"))
        self.assertEqual([m["content"] for m in fork["messages"]][:1], [m["content"] for m in source["messages"]][:1])
        self.assertTrue(set(m["id"] for m in fork["messages"]).isdisjoint(m["id"] for m in source["messages"]))

        runs = db.fetchall("SELECT * FROM chat_runs WHERE thread_id=?", (fork_pk,))
        self.assertEqual(len(runs), 2)
        blob = json.dumps([dict(r) for r in runs]) + json.dumps(fork["messages"])
        for old in (root, child, job):
            self.assertNotIn(old, blob)
        new_child = next(r for r in runs if r["parent_run_id"])
        new_root = next(r for r in runs if not r["parent_run_id"])
        self.assertEqual(new_child["parent_run_id"], new_root["id"])
        self.assertEqual(json.loads(new_child["request"])["thread_id"], fork_pk)
        self.assertIn(new_child["id"], new_child["provider_messages"])
        spawn = next(t for t in fork["messages"][-1]["tools"] if t["name"] == "spawn_subagent")
        self.assertEqual(spawn["child_id"], new_child["id"])
        jobs = db.fetchall("SELECT * FROM command_jobs WHERE thread_id=?", (fork_pk,))
        self.assertEqual([(j["out"], j["run_id"]) for j in jobs], [("3 passed", new_child["id"])])
        agents = store.list_thread_agents(1, fork_pk)
        self.assertEqual([a["id"] for a in agents], [new_child["id"]])
        # The source is untouched.
        self.assertEqual(len(db.fetchall("SELECT id FROM chat_runs WHERE thread_id=?", (pk,))), 2)
        self.assertEqual(db.fetchone("SELECT thread_id FROM command_jobs WHERE id=?", (job,))["thread_id"], pk)

    def test_fork_keeps_tool_history_after_the_source_is_deleted(self):
        thread = chats.create_thread(1, model="glm-5.3", tools=[])
        pk = chats.resolve_thread_pk(1, thread["id"])
        run = store.start_turn(1, pk, "read it", thread["revision"], None)["run"]
        rows = list(run["request"]["messages"]) + [
            {"role": "assistant", "content": "", "tool_calls": [{"id": "call-0", "type": "function",
                "function": {"name": "read_file", "arguments": '{"path":"example.txt"}'}}]},
            {"role": "tool", "tool_call_id": "call-0", "content": "COPIED_TOOL"},
            {"role": "assistant", "content": "done"},
        ]
        store.save_provider_messages(run["id"], rows)
        store.finish_run(run["id"], {"content": "done", "tools": [{"id": "call-0", "name": "read_file", "status": "done"}]}, "completed")
        fork = chats.fork_thread(1, thread["id"])
        chats.delete_thread(1, thread["id"])
        fork = chats.get_thread(1, fork["id"])
        fork_pk = chats.resolve_thread_pk(1, fork["id"])
        replay = store.start_turn(1, fork_pk, "continue", fork["revision"], None)["run"]["request"]["messages"]
        self.assertIn("COPIED_TOOL", json.dumps(replay))

    def test_fork_waits_for_running_work(self):
        thread = chats.create_thread(1, model="offline")
        pk = chats.resolve_thread_pk(1, thread["id"])
        store.start_turn(1, pk, "hello", thread["revision"], "first")
        with self.assertRaises(chats.ChatValidationError) as caught:
            chats.fork_thread(1, thread["id"])
        self.assertEqual(caught.exception.status_code, 409)

    def test_fork_keeps_project_binding(self):
        made = self.project()
        thread, _ = self.session(made)
        fork = chats.fork_thread(1, thread["id"])
        self.assertEqual((fork["kind"], fork["project_id"]), ("project", made["id"]))

    # -- retention --------------------------------------------------------

    def test_settings_validate(self):
        with self.assertRaises(retention.RetentionError):
            retention.set_settings(1, {"archive_hours": -1})
        with self.assertRaises(retention.RetentionError):
            retention.set_settings(1, {"delete_days": "30"})
        with self.assertRaises(retention.RetentionError):
            retention.set_settings(1, {"unknown": 1})
        saved = retention.set_settings(1, {"archive_hours": 72, "delete_days": 30, "archive_projects": False})
        self.assertEqual((saved["archive_hours"], saved["delete_days"], saved["archive_chats"], saved["archive_projects"]),
                         (72, 30, True, False))

    def test_sweep_archives_inactive_chats_but_not_running_or_scheduled_ones(self):
        retention.set_settings(1, {"archive_hours": 72})
        stale = chats.create_thread(1, model="offline")
        fresh = chats.create_thread(1, model="offline")
        busy = chats.create_thread(1, model="offline")
        busy_pk = chats.resolve_thread_pk(1, busy["id"])
        store.start_turn(1, busy_pk, "still working", busy["revision"], "busy")
        for item in (stale, busy):
            db.execute("UPDATE chat_threads SET active_at=?, updated_at=?, created_at=? WHERE public_id=?",
                       (ago(days=5), ago(days=5), ago(days=5), item["id"]))
        db.execute("UPDATE chat_runs SET updated_at=? WHERE thread_id=?", (ago(days=5), busy_pk))
        stats = retention.sweep_user(1)
        self.assertEqual(stats["archived_chats"], 1)
        self.assertTrue(chats.get_thread(1, stale["id"])["archived"])
        self.assertFalse(chats.get_thread(1, fresh["id"])["archived"])
        self.assertFalse(chats.get_thread(1, busy["id"])["archived"])
        # Restoring counts as activity; the next sweep leaves it alone.
        chats.update_thread(1, stale["id"], archived=False)
        self.assertEqual(retention.sweep_user(1)["archived_chats"], 0)

    def test_sweep_archives_idle_projects_and_deletes_old_archives(self):
        retention.set_settings(1, {"archive_hours": 24, "delete_days": 30})
        made = self.project()
        thread, pk = self.session(made)
        db.execute("UPDATE user_projects SET created_at=?, updated_at=? WHERE id=?", (ago(days=3), ago(days=3), made["id"]))
        db.execute("UPDATE chat_threads SET active_at=?, updated_at=?, created_at=? WHERE id=?", (ago(days=3), ago(days=3), ago(days=3), pk))
        stats = retention.sweep_user(1)
        self.assertEqual(stats["archived_projects"], 1)
        self.assertTrue(chats.get_thread(1, thread["id"])["archived"])

        old = chats.create_thread(1, model="offline")
        chats.update_thread(1, old["id"], archived=True)
        db.execute("UPDATE chat_threads SET archived_at=? WHERE public_id=?", (ago(days=31), old["id"]))
        db.execute("UPDATE user_projects SET archived_at=? WHERE id=?", (ago(days=31), made["id"]))
        db.execute("UPDATE chat_threads SET archived_at=? WHERE id=?", (ago(days=31), pk))
        stats = retention.sweep_user(1)
        self.assertEqual((stats["deleted_projects"], stats["deleted_chats"]), (1, 1))
        self.assertIsNone(chats.get_thread(1, old["id"]))
        self.assertIsNone(hosts.get_project(1, 7, made["id"]))

    def test_sweep_never_deletes_what_it_archived_in_the_same_pass(self):
        retention.set_settings(1, {"archive_hours": 24, "delete_days": 30})
        made = self.project()
        thread, pk = self.session(made)
        db.execute("UPDATE user_projects SET created_at=?, updated_at=? WHERE id=?", (ago(days=40), ago(days=40), made["id"]))
        db.execute("UPDATE chat_threads SET active_at=?, updated_at=?, created_at=? WHERE id=?", (ago(days=40), ago(days=40), ago(days=40), pk))
        stats = retention.sweep_user(1)
        self.assertEqual((stats["archived_projects"], stats["deleted_projects"], stats["deleted_chats"]), (1, 0, 0))
        self.assertIsNotNone(hosts.get_project(1, 7, made["id"]))
        self.assertIsNotNone(chats.get_thread(1, thread["id"]))

    def test_sweep_skips_projects_with_scheduled_sessions(self):
        retention.set_settings(1, {"archive_hours": 24, "delete_days": 30})
        made = self.project()
        thread, pk = self.session(made)
        db.execute("UPDATE user_projects SET created_at=?, updated_at=? WHERE id=?", (ago(days=3), ago(days=3), made["id"]))
        db.execute("UPDATE chat_threads SET active_at=?, updated_at=?, created_at=?, schedule_id=99 WHERE id=?",
                   (ago(days=3), ago(days=3), ago(days=3), pk))
        self.assertEqual(retention.sweep_user(1)["archived_projects"], 0)
        self.assertFalse(chats.get_thread(1, thread["id"])["archived"])

    def test_sweep_never_deletes_an_archived_scheduled_chat(self):
        retention.set_settings(1, {"archive_hours": 24, "delete_days": 30})
        owned = chats.create_thread(1, model="offline")
        chats.update_thread(1, owned["id"], archived=True)
        db.execute("UPDATE chat_threads SET archived_at=?, schedule_id=99 WHERE public_id=?", (ago(days=31), owned["id"]))
        self.assertEqual(retention.sweep_user(1)["deleted_chats"], 0)
        self.assertIsNotNone(chats.get_thread(1, owned["id"]))

    def test_sweep_does_not_delete_a_project_restored_mid_sweep(self):
        retention.set_settings(1, {"archive_hours": 24, "delete_days": 30})
        made = self.project()
        self.session(made)
        with store.transaction() as conn:
            hosts.set_project_archived(conn, 1, int(made["id"]), True, skip_running=True)
        db.execute("UPDATE user_projects SET archived_at=? WHERE id=?", (ago(days=31), made["id"]))
        db.execute("UPDATE chat_threads SET archived_at=? WHERE project_id=?", (ago(days=31), made["id"]))
        real = retention.db.fetchone

        def restore_then_read(sql, *args, **kwargs):
            if "FROM user_projects" in sql:
                db.execute("UPDATE user_projects SET archived=0, archived_at='' WHERE id=?", (made["id"],))
            return real(sql, *args, **kwargs)

        with patch.object(retention.db, "fetchone", side_effect=restore_then_read):
            stats = retention.sweep_user(1)
        self.assertEqual(stats["deleted_projects"], 0)
        self.assertIsNotNone(hosts.get_project(1, 7, made["id"]))

    def test_sweep_does_not_delete_sessions_of_a_project_restored_during_the_delete(self):
        retention.set_settings(1, {"archive_hours": 24, "delete_days": 30})
        made = self.project()
        thread, _pk = self.session(made)
        with store.transaction() as conn:
            hosts.set_project_archived(conn, 1, int(made["id"]), True, skip_running=True)
        db.execute("UPDATE user_projects SET archived_at=? WHERE id=?", (ago(days=31), made["id"]))
        db.execute("UPDATE chat_threads SET archived_at=? WHERE project_id=?", (ago(days=31), made["id"]))
        real = hosts._delete_archived_project

        def restore_then_delete(*args):
            # The user restores the project after the sweep's last re-read.
            with store.transaction() as conn:
                hosts.set_project_archived(conn, 1, int(made["id"]), False)
            return real(*args)

        with patch.object(hosts, "_delete_archived_project", side_effect=restore_then_delete):
            stats = retention.sweep_user(1)
        self.assertEqual(stats["deleted_projects"], 0)
        self.assertIsNotNone(hosts.get_project(1, 7, made["id"]))
        self.assertFalse(chats.get_thread(1, thread["id"])["archived"])

    def test_sweep_does_not_archive_a_chat_used_after_its_snapshot(self):
        retention.set_settings(1, {"archive_hours": 72})
        stale = chats.create_thread(1, model="offline")
        db.execute("UPDATE chat_threads SET active_at=?, updated_at=?, created_at=? WHERE public_id=?",
                   (ago(days=5), ago(days=5), ago(days=5), stale["id"]))
        real = retention._thread_activity

        def snapshot_then_activity(conn, user_id):
            result = real(conn, user_id)
            chats.update_thread(1, stale["id"], title="renamed just now")
            return result

        with patch.object(retention, "_thread_activity", side_effect=snapshot_then_activity):
            self.assertEqual(retention.sweep_user(1)["archived_chats"], 0)
        self.assertFalse(chats.get_thread(1, stale["id"])["archived"])

    def test_instance_ceiling_applies_when_user_has_none(self):
        prefs = retention.get_settings(1)
        prefs["limits"] = {"archive_days": 90, "delete_days": 0}
        self.assertEqual(retention.effective(prefs), (90 * 24, 0))
        prefs["archive_hours"] = 24
        self.assertEqual(retention.effective(prefs), (24, 0))
        prefs["archive_hours"] = 24 * 400
        self.assertEqual(retention.effective(prefs), (90 * 24, 0))

    def test_prune_history_ages_out_fires_and_command_jobs(self):
        old = ago(days=31)
        recent = ago(days=1)
        for tag, when in (("old", old), ("new", recent)):
            db.execute(
                """INSERT INTO schedule_fires(schedule_id, user_id, trigger, status, slot_key, fire_id, started_at, finished_at)
                   VALUES(1, 1, 'scheduled', 'completed', ?, ?, ?, ?)""",
                (tag, "fire-" + tag, when, when),
            )
            db.execute(
                """INSERT INTO command_jobs(id, user_id, host_id, command, timeout_ms, status, created_at, started_at, finished_at)
                   VALUES(?, 1, 0, 'ls', 1000, 'completed', ?, ?, ?)""",
                ("job-" + tag, when, when, when),
            )
        counts = retention.prune_history()
        self.assertEqual(counts, {"fires": 1, "jobs": 1})
        self.assertIsNone(db.fetchone("SELECT id FROM schedule_fires WHERE fire_id='fire-old'"))
        self.assertIsNotNone(db.fetchone("SELECT id FROM schedule_fires WHERE fire_id='fire-new'"))
        self.assertIsNone(db.fetchone("SELECT id FROM command_jobs WHERE id='job-old'"))
        self.assertIsNotNone(db.fetchone("SELECT id FROM command_jobs WHERE id='job-new'"))

    def test_trace_of_a_pruned_command_settles_as_lost(self):
        thread = chats.create_thread(1, model="offline")
        pk = chats.resolve_thread_pk(1, thread["id"])
        tool = {"id": "call-1", "name": "run_terminal_command", "status": "running", "job_id": "job-pruned"}
        db.execute("UPDATE chat_threads SET messages=? WHERE id=?", (json.dumps([
            {"id": "u1", "role": "user", "content": "run it"},
            {"id": "a1", "role": "assistant", "content": "", "tools": [tool]},
        ]), pk))
        settled = chats.get_thread(1, thread["id"])["messages"][1]["tools"][0]
        self.assertEqual((settled["status"], settled["execution_status"]), ("interrupted", "lost"))


if __name__ == "__main__":
    unittest.main()
