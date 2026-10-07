"""Offline storage, migration, concurrency and replay tests."""
from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import chat_store as store, chats, db


class ChatStoreTest(unittest.TestCase):
    def setUp(self):
        store.invalidate_committed_cursors()
        self.tmp = tempfile.TemporaryDirectory(prefix="fuse-chat-store-")
        self.addCleanup(self.tmp.cleanup)
        self.conn = sqlite3.connect(str(Path(self.tmp.name) / "test.db"), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
        db._init(self.conn)
        self.addCleanup(self.conn.close)
        self.connection_patch = patch.object(db, "connect", return_value=self.conn)
        self.connection_patch.start()
        self.addCleanup(self.connection_patch.stop)
        for uid in (1, 2):
            db.execute("INSERT INTO users(id,email,password_hash,created_at) VALUES(?,?,?,?)",
                       (uid, f"{uid}@test.invalid", "unused", db.now()))
        store.init_schema()

    def turn(self, thread=None, command="turn", user=1, auto_title=False):
        thread = thread or chats.create_thread(user, model="offline")
        return store.start_turn(user, thread["id"], "Hello", thread["revision"], command, auto_title=auto_title)

    def assertConflict(self, call):
        with self.assertRaises(chats.ChatValidationError) as raised:
            call()
        self.assertEqual(raised.exception.status_code, 409)

    def test_migration_assigns_ids_once_and_seeds_sequence(self):
        db.execute("DELETE FROM schema_meta WHERE key='chat_message_ids_v1'")
        db.execute("INSERT INTO chat_threads(id,user_id,messages,created_at,updated_at) VALUES(80,1,?,?,?)",
                   (json.dumps([{"role": "user", "content": "legacy"},
                                {"id": "keep", "role": "assistant", "content": "answer"}]), db.now(), db.now()))
        store.init_schema()
        first = chats.get_thread(1, 80)
        store.init_schema()
        self.assertEqual(first, chats.get_thread(1, 80))
        self.assertEqual(first["messages"][1]["id"], "keep")
        self.assertTrue(first["messages"][0]["id"])
        chats.delete_thread(1, 80)
        store.init_schema()
        created = chats.create_thread(1)
        self.assertRegex(created["id"], r"^[A-Za-z0-9]{22}$")
        self.assertFalse(created["id"].isdigit())
        pk = db.fetchone("SELECT id FROM chat_threads WHERE public_id=?", (created["id"],))["id"]
        self.assertGreater(pk, 80)

    def test_message_id_validation(self):
        message = {"id": "stable", "role": "user", "content": "Hi"}
        for strict in (True, False):
            self.assertEqual(chats.parse_messages([message], strict=strict)[0]["id"], "stable")
        for bad in (None, "", 42, "x" * 201, "\ud800"):
            with self.assertRaises(chats.ChatValidationError):
                chats.parse_messages([{**message, "id": bad}], strict=True)
        with self.assertRaises(chats.ChatValidationError):
            chats.parse_messages([message, message], strict=True)
        repaired = chats.parse_messages([message, message])
        self.assertNotEqual(repaired[0]["id"], repaired[1]["id"])

    def test_spawn_snapshot_failure_is_status_only_and_waits_are_receipts(self):
        run = self.turn()["run"]
        for status in ("failed", "cancelled", "interrupted", "completed"):
            with self.subTest(status=status):
                db.execute("UPDATE chat_runs SET status=?,message=? WHERE id=?",
                           (status, json.dumps({"content": "private partial prose", "error": "provider stopped" if status != "completed" else ""}), run["id"]))
                spawn = {"name": "spawn_subagent", "child_id": run["id"], "status": "running", "state": "running"}
                waits = [{"name": "get_subagent_output", "child_id": run["id"], "status": s,
                          "execution_status": "running", "summary": "observed output"} for s in ("running", "done", "interrupted")]
                originals = [dict(t) for t in waits]
                store.settle_spawn_traces(self.conn, 1, [{"tools": [spawn, *waits]}])
                self.assertEqual(waits, originals)
                self.assertEqual(spawn["state"], status)
                if status == "completed":
                    self.assertEqual(spawn["summary"], "private partial prose")
                else:
                    self.assertNotIn("private partial prose", spawn["summary"])
                    self.assertIn("provider stopped", spawn["summary"])
                    self.assertIn("No completed report", spawn["preview"])

    def test_spawn_snapshot_follows_resumed_child_without_old_report(self):
        run = self.turn()["run"]
        spawn = {"name": "spawn_subagent", "child_id": run["id"], "status": "done",
                 "state": "completed", "execution_status": "completed", "summary": "Old report", "preview": "Old report"}
        store.settle_spawn_traces(self.conn, 1, [{"tools": [spawn]}])
        self.assertEqual(spawn["execution_status"], "queued")
        self.assertNotIn("summary", spawn)
        db.execute("UPDATE chat_runs SET status='completed',message=? WHERE id=?",
                   (json.dumps({"content": "New report"}), run["id"]))
        store.settle_spawn_traces(self.conn, 1, [{"tools": [spawn]}])
        self.assertEqual(spawn["summary"], "New report")

    def test_assistant_messages_carry_model(self):
        parsed = chats.parse_messages(
            [{"role": "assistant", "content": "hi", "model": "openrouter/z-ai/glm-5.3-flash"}], strict=True)
        self.assertEqual(parsed[0]["model"], "openrouter/z-ai/glm-5.3-flash")
        with self.assertRaises(chats.ChatValidationError):
            chats.parse_messages([{"role": "user", "content": "hi", "model": "glm"}], strict=True)
        with self.assertRaises(chats.ChatValidationError):
            chats.parse_messages([{"role": "assistant", "content": "hi", "bogus": 1}], strict=True)

        thread = chats.create_thread(1, model="offline")
        result = store.start_turn(1, thread["id"], "Hello", thread["revision"], "turn-model",
                                  model="openrouter/z-ai/glm-5.3-flash")
        run = result["run"]
        self.assertEqual(result["thread"]["messages"][-1]["model"], "openrouter/z-ai/glm-5.3-flash")
        store.checkpoint(run["id"], {"content": "partial", "model": "openrouter/z-ai/glm-5.3-flash"})
        self.assertEqual(chats.get_thread(1, thread["id"])["messages"][-1]["model"], "openrouter/z-ai/glm-5.3-flash")
        store.finish_run(run["id"], {"content": "final", "model": "openrouter/z-ai/glm-5.3-flash"}, "completed")
        final = chats.get_thread(1, thread["id"])
        self.assertEqual(final["messages"][-1]["model"], "openrouter/z-ai/glm-5.3-flash")
        self.assertEqual(final["run"], None)

    def test_turn_snapshot_overlay_and_finalize_once(self):
        chats.set_user_context(1, instructions="Be exact", profile="Reader")
        thread = chats.create_thread(1, model="offline", context="notes", tools=[])
        result = self.turn(thread)
        run = result["run"]
        self.assertEqual(run["status"], "queued")
        self.assertEqual(run["request"]["messages"], [{"role": "user", "content": "Hello"}])
        self.assertEqual(run["request"]["run_id"], run["id"])
        self.assertEqual(run["request"]["instructions"], "Be exact")
        self.assertEqual(run["request"]["thread_context"], "notes")
        self.assertEqual(result["thread"]["revision"], 1)
        raw = db.fetchone("SELECT messages FROM chat_threads WHERE public_id=?", (thread["id"],))[0]
        store.checkpoint(run["id"], {"content": "partial"})
        self.assertEqual(db.fetchone("SELECT messages FROM chat_threads WHERE public_id=?", (thread["id"],))[0], raw)
        current = chats.get_thread(1, thread["id"])
        self.assertEqual(current["messages"][-1]["content"], "partial")
        self.assertEqual(current["messages"][-1]["id"], run["message_id"])
        self.assertTrue(current["running"])
        self.assertTrue(chats.list_threads(1)[0]["running"])
        chats.update_thread(1, thread["id"], title="Renamed", context="new", expected_revision=1)
        self.assertEqual(store.get_run(1, run["id"])["request"]["thread_context"], "notes")
        store.finish_run(run["id"], {"content": "final"}, "completed")
        final = chats.get_thread(1, thread["id"])
        self.assertEqual(final["revision"], 3)
        self.assertEqual(final["title"], "Renamed")
        self.assertEqual(final["messages"][-1]["content"], "final")
        self.assertIsNone(final["run"])
        self.assertFalse(final["running"])
        self.assertFalse(chats.list_threads(1)[0]["running"])
        last = store.cursor(1)
        store.finish_run(run["id"], {"content": "wrong"}, "failed")
        store.checkpoint(run["id"], {"content": "late"})
        self.assertEqual(last, store.cursor(1))
        self.assertEqual(chats.get_thread(1, thread["id"]), final)

    def test_progress_raw_frames_and_full_snapshot(self):
        run = self.turn()["run"]
        frames = [{"type": "delta", "content": "hello"}]
        store.checkpoint(run["id"], {"content": "hello"}, events=frames)
        payload = store.events_after(1, 0)[-1]["payload"]
        self.assertEqual(payload["events"], frames)
        self.assertNotIn("message", payload)
        self.assertEqual(store.get_run(1, run["id"])["message"]["content"], "hello")
        store.checkpoint(run["id"], {"content": "hello again"})
        self.assertEqual(store.events_after(1, 0)[-1]["payload"]["message"]["content"], "hello again")

    def test_turn_idempotency_is_user_scoped_and_payload_bound(self):
        thread = chats.create_thread(1)
        first = self.turn(thread)
        retry = self.turn(thread)
        self.assertTrue(retry["idempotent"])
        self.assertEqual(first["run"]["id"], retry["run"]["id"])
        self.assertEqual(len(retry["thread"]["messages"]), 2)
        self.assertConflict(lambda: store.start_turn(1, thread["id"], "different", 0, "turn"))
        self.assertConflict(lambda: chats.create_thread(1, command_id="turn"))
        self.assertFalse(self.turn(user=2)["idempotent"])

    def test_revision_and_active_run_limits(self):
        result = self.turn()
        tid = result["thread"]["id"]
        self.assertConflict(lambda: chats.update_thread(1, tid, title="bad", expected_revision=0))
        self.assertConflict(lambda: chats.update_thread(1, tid, messages=[], expected_revision=1))
        self.assertConflict(lambda: store.start_turn(1, tid, "again", 1, "again"))
        for i in range(3):
            self.turn(command=f"extra{i}")
        self.assertConflict(lambda: self.turn(command="fifth"))
        self.assertEqual(len(store.active_runs(1)), 4)
        self.assertEqual(len(store.active_runs(2)), 0)

    def test_database_enforces_one_active_run(self):
        run = self.turn()["run"]
        with self.assertRaises(sqlite3.IntegrityError):
            with store.transaction() as conn:
                conn.execute(
                    """INSERT INTO chat_runs(id,thread_id,user_id,status,request,message_id,message,created_at,updated_at,cancel_requested,parent_run_id,root_run_id,agent_depth)
                       SELECT 'another',thread_id,user_id,status,request,message_id,message,created_at,updated_at,cancel_requested,NULL,root_run_id,agent_depth
                       FROM chat_runs WHERE id=?""",
                    (run["id"],),
                )

    def test_failed_event_rolls_back_entire_turn(self):
        thread = chats.create_thread(1)
        last = store.cursor(1)
        with patch.object(store, "event", side_effect=RuntimeError("write failed")):
            with self.assertRaises(RuntimeError):
                self.turn(thread)
        self.assertEqual(chats.get_thread(1, thread["id"]), thread)
        self.assertEqual(store.cursor(1), last)
        self.assertEqual(store.active_runs(1), [])
        self.assertFalse(self.turn(thread)["idempotent"])

    def test_cancel_is_durable_and_idempotent(self):
        run = self.turn()["run"]
        self.assertTrue(store.request_cancel(1, run["id"]))
        last = store.cursor(1)
        self.assertTrue(store.request_cancel(1, run["id"]))
        self.assertEqual(last, store.cursor(1))
        self.assertTrue(store.get_run(1, run["id"])["cancel_requested"])
        store.finish_run(run["id"], {"content": "", "stopped": True}, "cancelled")
        self.assertFalse(store.request_cancel(1, run["id"]))

    def test_bounded_history_rejects_turn_without_truncation(self):
        thread = chats.create_thread(1, messages=[{"role": "user", "content": str(i)} for i in range(chats.MSG_MAX)])
        with self.assertRaises(chats.ChatValidationError) as raised:
            self.turn(thread)
        self.assertEqual(raised.exception.status_code, 413)
        self.assertEqual(chats.get_thread(1, thread["id"]), thread)

    def test_racing_turns_have_one_winner(self):
        thread = chats.create_thread(1)
        def attempt(key):
            try:
                return self.turn(thread, command=key)["run"]["id"]
            except chats.ChatValidationError as error:
                return error.status_code
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(attempt, ["one", "two"]))
        self.assertEqual(results.count(409), 1)
        self.assertEqual(len(chats.get_thread(1, thread["id"])["messages"]), 2)

    def test_delete_cancels_without_resurrection_or_id_reuse(self):
        result = self.turn()
        tid, run = result["thread"]["id"], result["run"]
        self.assertFalse(chats.delete_thread(2, tid))
        self.assertFalse(store.request_cancel(2, run["id"]))
        self.assertIsNone(store.get_run(2, run["id"]))
        self.assertTrue(chats.delete_thread(1, tid, expected_revision=1, command_id="delete"))
        self.assertTrue(chats.delete_thread(1, tid, expected_revision=1, command_id="delete"))
        # Deleting a thread purges its runs, queued or running.
        self.assertIsNone(store.get_run(1, run["id"]))
        replacement = chats.create_thread(1)
        self.assertNotEqual(replacement["id"], tid)
        self.assertRegex(replacement["id"], r"^[A-Za-z0-9]{22}$")
        store.finish_run(run["id"], {"content": "late"}, "cancelled")
        self.assertIsNone(chats.get_thread(1, tid))
        self.assertEqual(chats.get_thread(1, replacement["id"])["messages"], [])
        self.assertIn("thread.deleted", [event["type"] for event in store.events_after(1, 0)])

    def test_recover_preserves_checkpoint_and_is_idempotent(self):
        running = self.turn()["run"]
        queued = self.turn(command="queued")["run"]
        store.checkpoint(running["id"], {"content": "saved"})
        store.recover_runs()
        recovered_running = store.get_run(1, running["id"])
        self.assertEqual(recovered_running["status"], "interrupted")
        self.assertTrue(recovered_running["message"]["stopped"])
        self.assertEqual(chats.get_thread(1, running["thread_id"])["messages"][-1]["content"], "saved")
        recovered_queued = store.get_run(1, queued["id"])
        self.assertEqual(recovered_queued["status"], "queued")
        self.assertFalse(recovered_queued["message"].get("stopped"))
        last = store.cursor(1)
        store.recover_runs()
        self.assertEqual(store.cursor(1), last)
        self.assertEqual(len(store.active_runs(1)), 1)
        self.assertEqual(store.active_runs(1)[0]["id"], queued["id"])

    def test_seal_provider_messages_fills_missing_tool_results(self):
        sealed = store.seal_provider_messages(
            [
                {"role": "user", "content": "go"},
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {"id": "c1", "type": "function", "function": {"name": "web_search", "arguments": "{}"}},
                        {"id": "c2", "type": "function", "function": {"name": "read_url", "arguments": "{}"}},
                    ],
                },
                {"role": "tool", "tool_call_id": "c1", "content": "hits"},
            ]
        )
        ids = [m.get("tool_call_id") for m in sealed if m.get("role") == "tool"]
        self.assertEqual(ids, ["c1", "c2"])
        self.assertIn("interrupted", sealed[-1]["content"])

    def test_recover_requeues_saved_tool_round(self):
        run = self.turn()["run"]
        store.checkpoint(
            run["id"],
            {
                "content": "partial next",
                "tools": [{"name": "web_search", "status": "done", "id": "ts_0_0", "query": "q"}],
            },
        )
        store.save_turn_progress(
            run["id"],
            [
                {"role": "user", "content": "Hello"},
                {
                    "role": "assistant",
                    "content": "I'll look",
                    "tool_calls": [
                        {"id": "c1", "type": "function", "function": {"name": "web_search", "arguments": "{}"}},
                    ],
                },
                {"role": "tool", "tool_call_id": "c1", "content": "hits"},
            ],
            loaded_tools=["read_file"],
        )
        store.recover_runs()
        recovered = store.get_run(1, run["id"])
        self.assertEqual(recovered["status"], "queued")
        self.assertTrue(recovered["request"]["agent"]["skip_sanitize"])
        self.assertTrue(recovered["request"]["agent"]["resumed"])
        self.assertEqual(recovered["request"]["messages"][-2]["role"], "tool")
        self.assertEqual(recovered["request"]["messages"][-2]["content"], "hits")
        self.assertEqual(recovered["request"]["messages"][-1]["role"], "user")
        self.assertIn(f"resume-note:{run['id']}:1", recovered["request"]["messages"][-1]["content"])
        self.assertEqual(recovered["request"]["agent"]["loaded_tools"], ["read_file"])
        self.assertEqual(recovered["message"]["content"], "partial next")
        self.assertEqual(recovered["message"]["tools"][0]["status"], "done")
        self.assertIn("Resumed after server restart", recovered["message"]["notice"])
        overlay = chats.get_thread(1, run["thread_id"])
        self.assertEqual(overlay["run"]["id"], run["id"])
        self.assertEqual(overlay["messages"][-1]["content"], "partial next")
        store.recover_runs()
        self.assertEqual(store.get_run(1, run["id"])["status"], "queued")

    def test_recover_cancel_requested_with_mismatched_segments(self):
        run = self.turn()["run"]
        store.checkpoint(run["id"], {"content": "saved", "reasoning": "think"})
        bad = {
            "id": run["message_id"],
            "role": "assistant",
            "content": "saved",
            "reasoning": "think",
            "segments": [{"type": "content", "text": "saved and more"}],
            "stopped": False,
            "tools": [],
        }
        db.execute(
            "UPDATE chat_runs SET cancel_requested=1, message=? WHERE id=?",
            (json.dumps(bad), run["id"]),
        )
        store.recover_runs()
        recovered = store.get_run(1, run["id"])
        self.assertEqual(recovered["status"], "cancelled")
        self.assertTrue(recovered["message"]["stopped"])
        overlay = chats.get_thread(1, run["thread_id"])
        self.assertIsNone(overlay["run"])
        self.assertTrue(overlay["messages"][-1]["stopped"])

    def test_context_crud_revisions_commands_and_summaries(self):
        context = chats.get_user_context(1)
        self.assertEqual(context["revision"], 0)
        changed = chats.set_user_context(1, profile="Me", expected_revision=0, command_id="context")
        self.assertEqual(changed["revision"], 1)
        self.assertEqual(changed, chats.set_user_context(1, profile="Me", expected_revision=0, command_id="context"))
        self.assertConflict(lambda: chats.set_user_context(1, profile="stale", expected_revision=0))
        thread = chats.create_thread(1, command_id="create", messages=[{"role": "user", "content": "find me"}])
        self.assertEqual(thread, chats.create_thread(1, command_id="create", messages=[{"role": "user", "content": "find me"}]))
        updated = chats.update_thread(1, thread["id"], title="New", expected_revision=0, command_id="edit")
        self.assertEqual(updated, chats.update_thread(1, thread["id"], title="New", expected_revision=0, command_id="edit"))
        copied = chats.duplicate_thread(1, thread["id"], expected_revision=1, command_id="copy")
        self.assertEqual(copied, chats.duplicate_thread(1, thread["id"], expected_revision=1, command_id="copy"))
        self.assertNotEqual(copied["messages"][0]["id"], thread["messages"][0]["id"])
        for summary in chats.list_threads(1) + chats.search_threads(1, "find me"):
            self.assertIn("revision", summary)
            self.assertIn("running", summary)
            self.assertIs(summary["running"], False)
            self.assertNotIn("context", summary)
            self.assertNotIn("tools", summary)

    def test_list_and_search_report_running_for_active_turns(self):
        idle = chats.create_thread(1, title="idle chat")
        live = chats.create_thread(1, title="live chat")
        self.assertFalse(chats.list_threads(1)[0]["running"])
        started = self.turn(live)
        listed = {item["id"]: item for item in chats.list_threads(1)}
        self.assertTrue(listed[live["id"]]["running"])
        self.assertFalse(listed[idle["id"]]["running"])
        self.assertTrue(chats.get_thread(1, live["id"])["running"])
        hits = {item["id"]: item for item in chats.search_threads(1, "live chat")}
        self.assertTrue(hits[live["id"]]["running"])
        event = store.events_after(1, 0)
        updated = [item for item in event if item["type"] == "thread.updated"][-1]
        self.assertTrue(updated["payload"]["running"])
        store.finish_run(started["run"]["id"], {"content": "done"}, "completed")
        listed = {item["id"]: item for item in chats.list_threads(1)}
        self.assertFalse(listed[live["id"]]["running"])
        self.assertFalse(chats.get_thread(1, live["id"])["running"])

    def test_active_subagent_keeps_thread_running_after_root_finishes(self):
        thread = chats.create_thread(1, title="agent chat")
        started = self.turn(thread)
        root = started["run"]
        child = store.start_child_run(
            user_id=1, thread_id=thread["id"], parent_run_id=root["id"],
            prompt="look around", description="explore",
        )
        store.finish_run(root["id"], {"content": "done"}, "completed")
        self.assertTrue(chats.list_threads(1)[0]["running"])
        self.assertTrue(chats.get_thread(1, thread["id"])["running"])
        self.assertTrue(chats.search_threads(1, "agent chat")[0]["running"])
        last = [e for e in store.events_after(1, 0) if e["type"] == "thread.updated"][-1]
        self.assertTrue(last["payload"]["running"])
        store.finish_run(child["id"], {"content": "report"}, "completed")
        self.assertFalse(chats.list_threads(1)[0]["running"])
        last = [e for e in store.events_after(1, 0) if e["type"] == "thread.updated"][-1]
        self.assertFalse(last["payload"]["running"])

    def test_events_are_transactional_scoped_ordered_and_pruned(self):
        with self.assertRaises(RuntimeError):
            with store.transaction() as conn:
                store.event(conn, 1, "rolled.back")
                raise RuntimeError("rollback")
        self.assertEqual(store.cursor(1), 0)
        self.assertEqual(store.events_after(1, 0), [])
        with store.transaction() as conn:
            a = store.event(conn, 1, "first", payload={"a": 1})
            store.event(conn, 2, "private")
            b = store.event(conn, 1, "second")
        self.assertEqual([e["id"] for e in store.events_after(1, 0)], [a, b])
        self.assertEqual(len(store.events_after(1, a, 1)), 1)
        db.execute("UPDATE chat_events SET created_at='2000-01-01T00:00:00Z'")
        store.prune_events()
        self.assertEqual(store.events_after(1, 0), [])
        self.assertEqual(store.cursor(1), b)
        with store.transaction() as conn:
            self.assertGreater(store.event(conn, 1, "new"), b)

    def test_turn_sends_images_as_multimodal_parts(self):
        png = (
            "data:image/png;base64,"
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+ip1sAAAAASUVORK5CYII="
        )
        thread = chats.create_thread(1, model="offline")
        result = store.start_turn(
            1,
            thread["id"],
            "What is this?",
            thread["revision"],
            "img",
            attachments=[{"name": "dot.png", "mime": "image/png", "kind": "image", "data": png}],
            reasoning_effort="high",
        )
        stored = result["thread"]["messages"][0]
        self.assertEqual(stored["content"], "What is this?")
        self.assertEqual(stored["attachments"][0]["kind"], "image")
        content = result["run"]["request"]["messages"][0]["content"]
        self.assertEqual(content[0], {"type": "text", "text": "What is this?"})
        self.assertEqual(content[1]["type"], "image_url")
        self.assertEqual(content[1]["image_url"]["url"], png)
        self.assertEqual(result["run"]["request"]["reasoning_effort"], "high")

    def test_image_only_turn_is_allowed(self):
        png = (
            "data:image/png;base64,"
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+ip1sAAAAASUVORK5CYII="
        )
        thread = chats.create_thread(1)
        result = store.start_turn(
            1,
            thread["id"],
            "",
            thread["revision"],
            "img-only",
            attachments=[{"name": "dot.png", "mime": "image/png", "kind": "image", "data": png}],
        )
        content = result["run"]["request"]["messages"][0]["content"]
        self.assertEqual(content[0]["type"], "text")
        self.assertEqual(content[1]["type"], "image_url")
        self.assertEqual(result["thread"]["title"], "New chat")

    def test_text_attachment_is_inlined_for_the_model(self):
        thread = chats.create_thread(1)
        result = store.start_turn(
            1,
            thread["id"],
            "Summarize",
            thread["revision"],
            "txt",
            attachments=[{"name": "notes.txt", "mime": "text/plain", "kind": "text", "data": "alpha beta"}],
        )
        content = result["run"]["request"]["messages"][0]["content"]
        self.assertEqual(content[0]["type"], "text")
        self.assertIn("alpha beta", content[0]["text"])
        self.assertIn("notes.txt", content[0]["text"])

    def test_first_chat_page_turn_shows_provisional_title(self):
        thread = chats.create_thread(1, model="offline")
        self.assertEqual(thread["title"], "New chat")
        skipped = self.turn(thread)
        self.assertEqual(skipped["thread"]["title"], "New chat")
        self.assertNotIn("generate_title", skipped["run"]["request"])
        store.finish_run(skipped["run"]["id"], {"content": "ok"}, "completed")
        other = chats.create_thread(1, model="offline")
        result = store.start_turn(1, other["id"], "Explain the scrape retry queue please", other["revision"],
                                  "titled", auto_title=True)
        self.assertNotIn("generate_title", result["run"]["request"])
        self.assertEqual(result["thread"]["title"], "Explain the scrape retry queue please")
        store.finish_run(result["run"]["id"], {"content": "ok"}, "completed")
        latest = chats.get_thread(1, other["id"])
        again = store.start_turn(1, other["id"], "Follow up", latest["revision"], "second", auto_title=True)
        self.assertEqual(again["thread"]["title"], "Explain the scrape retry queue please")
        named = chats.create_thread(1, model="offline", title="Already named")
        flagged = store.start_turn(1, named["id"], "Hello", named["revision"], "named", auto_title=True)
        self.assertEqual(flagged["thread"]["title"], "Already named")

    def test_image_only_turn_skips_title_generation(self):
        png = (
            "data:image/png;base64,"
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+ip1sAAAAASUVORK5CYII="
        )
        thread = chats.create_thread(1)
        result = store.start_turn(
            1,
            thread["id"],
            "",
            thread["revision"],
            "img-title",
            attachments=[{"name": "dot.png", "mime": "image/png", "kind": "image", "data": png}],
        )
        self.assertEqual(result["thread"]["title"], "New chat")

    def test_apply_generated_title_skips_renamed_threads(self):
        thread = chats.create_thread(1)
        result = self.turn(thread, auto_title=True)
        applied = store.apply_generated_title(1, thread["id"], "CSV to JSON", "Hello")
        self.assertEqual(applied["title"], "CSV to JSON")
        self.assertGreater(applied["revision"], result["thread"]["revision"])
        chats.update_thread(1, thread["id"], title="Keep me", expected_revision=applied["revision"])
        self.assertIsNone(store.apply_generated_title(1, thread["id"], "Should not win", "Hello"))
        self.assertEqual(chats.get_thread(1, thread["id"])["title"], "Keep me")
        self.assertIsNone(store.apply_generated_title(1, 999999, "Gone", "Hello"))

    def _thread_pk(self, public_id):
        return db.fetchone("SELECT id FROM chat_threads WHERE public_id=?", (public_id,))["id"]

    def test_title_state_tracks_generated_and_renamed(self):
        thread = chats.create_thread(1)
        pk = self._thread_pk(thread["id"])
        result = self.turn(thread, auto_title=True)
        store.mark_title_attempted(pk)
        applied = store.apply_generated_title(1, thread["id"], "CSV to JSON", "New chat")
        self.assertEqual(applied["title"], "CSV to JSON")
        row = db.fetchone("SELECT title_state, title_attempts FROM chat_threads WHERE id=?", (pk,))
        self.assertEqual(row["title_state"], "generated")
        self.assertEqual(row["title_attempts"], 0)
        chats.update_thread(1, thread["id"], title="Renamed", expected_revision=applied["revision"])
        row = db.fetchone("SELECT title_state FROM chat_threads WHERE id=?", (pk,))
        self.assertEqual(row["title_state"], "user")
        self.assertIsNone(store.apply_generated_title(1, thread["id"], "Overwrite", "Renamed"))

    def test_public_ids_are_stable_and_legacy_integers_resolve(self):
        thread = chats.create_thread(1, title="Alpha")
        self.assertRegex(thread["id"], r"^[A-Za-z0-9]{22}$")
        self.assertEqual(chats.get_thread(1, thread["id"])["id"], thread["id"])
        pk = db.fetchone("SELECT id FROM chat_threads WHERE public_id=?", (thread["id"],))["id"]
        self.assertEqual(chats.get_thread(1, pk)["id"], thread["id"])
        self.assertEqual(chats.get_thread(1, str(pk))["id"], thread["id"])
        other = chats.create_thread(1, title="Beta")
        self.assertNotEqual(thread["id"], other["id"])

    def test_compaction_projects_into_the_next_turn(self):
        from app import context_compact as ctxmod, context_meter

        thread = chats.create_thread(1, model="offline")
        messages = []
        for i in range(8):
            messages.append({"role": "user", "content": f"ask {i} " + "word " * 30})
            messages.append({"role": "assistant", "content": f"ans {i} " + "word " * 30})
        chats.update_thread(1, thread["id"], messages=messages, expected_revision=thread["revision"])
        thread = chats.get_thread(1, thread["id"])
        self.assertIn("compaction", thread)
        self.assertGreater(thread["compaction"]["meter"]["limit"], 0)
        prepared = store.prepare_compact(1, thread["id"], thread["revision"], "compact-1")
        self.assertFalse(prepared["idempotent"])
        checkpoint = ctxmod.make_checkpoint(
            summary="Earlier work on asks 0-4.",
            through_message_id=thread["messages"][-7]["id"],
            strategy="truncate",
            trigger="manual",
            tokens_before=9000,
            tokens_after=400,
        )
        view = store.finish_compact(
            1, thread["id"], checkpoint, command_id="compact-1", fingerprint=prepared["fingerprint"]
        )
        self.assertTrue(view["compaction"]["checkpoints"])
        replay = store.prepare_compact(1, thread["id"], thread["revision"], "compact-1")
        self.assertTrue(replay["idempotent"])
        result = self.turn(view, command="after-compact")
        sent = result["run"]["request"]["messages"]
        self.assertTrue(any("[Compacted conversation]" in (m.get("content") or "") for m in sent if m["role"] == "user"))

    def test_start_turn_recomputes_meter_without_floor(self):
        from app import context_compact as ctxmod, context_meter

        thread = chats.create_thread(1, model="grok-4.6")
        messages = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
        ]
        chats.update_thread(1, thread["id"], messages=messages, expected_revision=thread["revision"])
        snap = context_meter.meter(used=48_000, limit=500_000, model="grok-4.6", estimated=True)
        snap["standing"] = True
        snap["parts"] = {"system": 1_900, "history": 500, "messages": 500, "thinking": 0,
                         "tool_calls": 0, "tool_families": {}, "tools": 3_100, "draft": 0, "total": 48_000}
        store.save_meter(1, thread["id"], snap)
        thread = chats.get_thread(1, thread["id"])
        result = store.start_turn(1, thread["id"], "next", thread["revision"], "keep-meter")
        meter = result["thread"]["compaction"]["meter"]
        parts = meter["parts"]
        self.assertEqual(meter["scope"], "transcript")
        self.assertEqual(meter["used"], parts["system"] + parts["history"] + parts["tools"])
        # The stale 48k floor is gone; only overhead survives from the prior snapshot.
        self.assertLess(meter["used"], 48_000)
        self.assertEqual(parts["system"], 1_900)
        self.assertEqual(parts["tools"], 3_100)
        self.assertGreater(parts["history"], 0)

    def test_live_peak_does_not_land_in_stored_meter(self):
        from app import context_compact as ctxmod, context_meter

        thread = chats.create_thread(1, model="grok-4.6")
        peak = context_meter.meter(used=90_000, limit=500_000, model="grok-4.6", estimated=False)
        peak["scope"] = "prompt"
        peak["parts"] = {"system": 1_900, "history": 500, "messages": 500, "thinking": 0,
                         "tool_calls": 0, "tool_families": {}, "tools": 3_100, "draft": 0, "total": 90_500}
        store.save_meter(1, thread["id"], peak)
        got = chats.get_thread(1, thread["id"])
        meter = got["compaction"]["meter"]
        parts = meter["parts"]
        self.assertEqual(meter["scope"], "transcript")
        self.assertEqual(meter["used"], parts["system"] + parts["history"] + parts["tools"])
        self.assertLess(meter["used"], 90_000)
        self.assertEqual(parts["system"], 1_900)
        self.assertEqual(parts["tools"], 3_100)

    def test_thread_meter_rebuilds_stale_devin_delta(self):
        from app import context_compact as ctxmod, context_meter

        thread = chats.create_thread(1, model="devin/glm-5-2")
        messages = [
            {"role": "user", "content": "hello " * 400},
            {
                "role": "assistant",
                "content": "ok " * 200,
                "reasoning": "think " * 200,
                "tools": [
                    {"name": "run_terminal_command", "command": "ls -la /var/log", "preview": "file.txt " * 80},
                    {"name": "web_search", "query": "fuse router occupancy", "preview": "docs " * 80},
                ],
            },
        ]
        chats.update_thread(1, thread["id"], messages=messages, expected_revision=thread["revision"])
        snap = context_meter.meter(used=449, limit=1_000_000, model="devin/glm-5-2", estimated=False)
        snap["standing"] = True
        store.save_meter(1, thread["id"], snap)
        got = chats.get_thread(1, thread["id"])
        meter = got["compaction"]["meter"]
        parts = meter["parts"]
        self.assertGreater(meter["used"], 449)
        self.assertEqual(meter["limit"], 200_000)
        self.assertEqual(meter["used"], parts["system"] + parts["history"] + parts["tools"])
        self.assertGreater(parts["history"], 0)
        # UI thinking and tool cards are not replayed verbatim; measure the
        # actual new-turn projection rather than those incompatible totals.
        self.assertEqual(meter["parts"]["thinking"], 0)
        self.assertTrue(got["messages"][-1]["reasoning"])
        from app import chat_history
        replay, _info = chat_history.build(self.conn, 1, None, got["messages"], ctxmod.parse_state({}), retained={})
        expected = context_meter.estimate_messages(replay)
        self.assertEqual(meter["parts"]["history"], expected)

    def test_thread_meter_ignores_summed_turn_usage(self):
        from app import context_compact as ctxmod, context_meter

        thread = chats.create_thread(1, model="grok-4.6")
        messages = [
            {"role": "user", "content": "hi"},
            {
                "role": "assistant",
                "content": "hello",
                "usage": {
                    "prompt_tokens": 80_000,
                    "completion_tokens": 20_000,
                    "total_tokens": 400_000,
                },
            },
        ]
        chats.update_thread(1, thread["id"], messages=messages, expected_revision=thread["revision"])
        snap = context_meter.meter(used=12_000, limit=500_000, model="grok-4.6", estimated=True)
        snap["standing"] = True
        store.save_meter(1, thread["id"], snap)
        got = chats.get_thread(1, thread["id"])
        meter = got["compaction"]["meter"]
        parts = meter["parts"]
        self.assertEqual(meter["used"], parts["system"] + parts["history"] + parts["tools"])
        # Billed per-turn usage never becomes window occupancy.
        self.assertLess(meter["used"], 1_000)
        self.assertGreater(parts["messages"], 0)
        self.assertFalse(meter["auto"])

    def test_thread_meter_ignores_in_turn_peak(self):
        from app import context_compact as ctxmod, context_meter

        thread = chats.create_thread(1, model="grok-4.6")
        messages = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
        ]
        chats.update_thread(1, thread["id"], messages=messages, expected_revision=thread["revision"])
        store.save_meter(
            1,
            thread["id"],
            context_meter.meter(used=400_000, limit=500_000, model="grok-4.6", estimated=False),
        )
        got = chats.get_thread(1, thread["id"])
        meter = got["compaction"]["meter"]
        parts = meter["parts"]
        self.assertLess(meter["used"], 1_000)
        self.assertEqual(meter["used"], parts["system"] + parts["history"] + parts["tools"])
        self.assertFalse(meter["auto"])

    def test_update_thread_recomputes_meter_on_delete(self):
        from app import context_compact as ctxmod, context_meter

        thread = chats.create_thread(1, model="grok-4.6")
        messages = [
            {"role": "user", "content": "hello " * 300},
            {"role": "assistant", "content": "ok " * 300},
            {"role": "user", "content": "again " * 300},
            {"role": "assistant", "content": "sure " * 300},
        ]
        chats.update_thread(1, thread["id"], messages=messages, expected_revision=thread["revision"])
        thread = chats.get_thread(1, thread["id"])
        before = thread["compaction"]["meter"]
        kept = [dict(msg) for msg in messages[:2]]
        got = chats.update_thread(
            1, thread["id"], messages=kept, expected_revision=thread["revision"], command_id="del-tail"
        )
        after = got["compaction"]["meter"]
        parts = after["parts"]
        self.assertEqual(after["scope"], "transcript")
        self.assertEqual(after["used"], parts["system"] + parts["history"] + parts["tools"])
        self.assertLess(parts["history"], before["parts"]["history"])

    def test_update_thread_preserves_concurrent_compaction_state(self):
        from app import context_compact as ctxmod, context_meter

        thread = chats.create_thread(1, model="grok-4.6")
        messages = [
            {"id": "a", "role": "user", "content": "one"},
            {"id": "b", "role": "assistant", "content": "ok"},
        ]
        chats.update_thread(1, thread["id"], messages=messages, expected_revision=thread["revision"])
        # A concurrent prune (e.g. from an in-flight run) lands between the
        # update's SELECT and its write; the message replacement must not
        # clobber it.
        state = ctxmod.parse_state(chats.get_thread(1, thread["id"])["compaction"] and "{}")
        state = ctxmod.append_checkpoint(state, ctxmod.make_checkpoint(
            summary="kept", through_message_id="b", strategy="truncate", trigger="auto"))
        thread_pk = chats.resolve_thread_pk(1, thread["id"])
        with store.transaction() as conn:
            conn.execute("UPDATE chat_threads SET compaction=? WHERE id=?",
                         (ctxmod.dump_state(state), thread_pk))
        trimmed = [{"id": "a", "role": "user", "content": "one"}]
        got = chats.update_thread(
            1, thread["id"], messages=trimmed,
            expected_revision=chats.get_thread(1, thread["id"])["revision"], command_id="del-keep-cp",
        )
        stored = ctxmod.parse_state(
            db.fetchone("SELECT compaction FROM chat_threads WHERE id=?", (thread_pk,))["compaction"]
        )
        self.assertEqual(len(stored["checkpoints"]), 1)
        self.assertEqual(got["compaction"]["meter"]["scope"], "transcript")

    def test_thread_meter_estimates_transcript_not_billed_usage(self):
        thread = chats.create_thread(1, model="grok-4.6")
        messages = [
            {"role": "user", "content": "hi"},
            {
                "role": "assistant",
                "content": "hello",
                "usage": {
                    "prompt_tokens": 100_000,
                    "completion_tokens": 1_000,
                    "total_tokens": 400_000,
                },
            },
        ]
        chats.update_thread(1, thread["id"], messages=messages, expected_revision=thread["revision"])
        got = chats.get_thread(1, thread["id"])
        meter = got["compaction"]["meter"]
        self.assertLess(meter["used"], 1_000)
        self.assertEqual(meter["limit"], 500_000)
        self.assertFalse(meter["auto"])

    def test_prepare_compact_rejects_active_run(self):
        thread = chats.create_thread(1, model="offline")
        self.turn(thread, command="busy")
        with self.assertRaises(chats.ChatValidationError) as raised:
            store.prepare_compact(1, thread["id"], 1, "compact-busy")
        self.assertEqual(raised.exception.status_code, 409)

    def test_save_meter_window_override_wins_over_live_limit(self):
        from app import context_compact as ctxmod, context_meter

        thread = chats.create_thread(1, model="grok-4.6")
        messages = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
        ]
        chats.update_thread(1, thread["id"], messages=messages, expected_revision=thread["revision"])
        pk = chats.resolve_thread_pk(1, thread["id"])
        state = ctxmod.parse_state(db.fetchone("SELECT compaction FROM chat_threads WHERE id=?", (pk,))["compaction"])
        state["window_override"] = 64_000
        with store.transaction() as conn:
            conn.execute(
                "UPDATE chat_threads SET compaction=? WHERE id=?",
                (ctxmod.dump_state(state), pk),
            )
        snap = context_meter.meter(used=10_000, limit=500_000, model="grok-4.6", estimated=False)
        snap["standing"] = True
        store.save_meter(1, thread["id"], snap)
        got = chats.get_thread(1, thread["id"])
        self.assertEqual(got["compaction"]["meter"]["limit"], 64_000)
        self.assertEqual(got["compaction"]["window_override"], 64_000)
        # Without an override, the runtime-learned limit survives for generic
        # model ids whose name cannot identify the backend.
        generic = chats.create_thread(1, model="mystery-local")
        pk2 = chats.resolve_thread_pk(1, generic["id"])
        snap = context_meter.meter(used=10_000, limit=32_000, model="mystery-local", estimated=False)
        snap["standing"] = True
        store.save_meter(1, generic["id"], snap)
        got = chats.get_thread(1, generic["id"])
        self.assertEqual(got["compaction"]["meter"]["limit"], 32_000)

    def test_recover_one_resumes_child_with_durable_transcript(self):
        thread = chats.create_thread(1, model="offline")
        parent = self.turn(thread, command="parent")["run"]
        child = store.start_child_run(
            user_id=1,
            thread_id=thread["id"],
            parent_run_id=parent["id"],
            prompt="look around",
            description="explore",
        )
        store.save_turn_progress(
            child["id"],
            [
                {"role": "user", "content": "look around"},
                {"role": "assistant", "content": "", "tool_calls": [
                    {"id": "c1", "type": "function", "function": {"name": "list_dir", "arguments": "{}"}}
                ]},
                {"role": "tool", "tool_call_id": "c1", "content": "files"},
            ],
        )
        store.recover_one(db.fetchone("SELECT * FROM chat_runs WHERE id=?", (child["id"],)))
        got = store.get_run(1, child["id"])
        self.assertEqual(got["status"], "queued")
        agent = got["request"]["agent"]
        self.assertTrue(agent["resumed"])
        self.assertTrue(agent["background"])
        self.assertTrue(agent["skip_sanitize"])
        # The sealed transcript keeps the executed tool result.
        roles = [(m.get("role"), m.get("tool_call_id")) for m in got["request"]["messages"]]
        self.assertIn(("tool", "c1"), roles)

    def test_recover_one_interrupts_child_without_transcript(self):
        thread = chats.create_thread(1, model="offline")
        parent = self.turn(thread, command="parent")["run"]
        child = store.start_child_run(
            user_id=1,
            thread_id=thread["id"],
            parent_run_id=parent["id"],
            prompt="look around",
            description="explore",
        )
        # A queued child that never started stays queued (like root runs) so
        # resume_queued can start it fresh.
        store.recover_one(db.fetchone("SELECT * FROM chat_runs WHERE id=?", (child["id"],)))
        self.assertEqual(store.get_run(1, child["id"])["status"], "queued")
        # A running child with no durable transcript is interrupted: a
        # provider call with an uncertain outcome must not be repeated.
        db.execute("UPDATE chat_runs SET status='running' WHERE id=?", (child["id"],))
        store.recover_one(db.fetchone("SELECT * FROM chat_runs WHERE id=?", (child["id"],)))
        self.assertEqual(store.get_run(1, child["id"])["status"], "interrupted")


class SidebarPinsUnreadTest(unittest.TestCase):
    """The pinned section's order and the finished-run unread badge."""

    def setUp(self):
        store.invalidate_committed_cursors()
        self.tmp = tempfile.TemporaryDirectory(prefix="fuse-chat-pins-")
        self.addCleanup(self.tmp.cleanup)
        self.conn = sqlite3.connect(str(Path(self.tmp.name) / "test.db"), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
        db._init(self.conn)
        self.addCleanup(self.conn.close)
        self.connection_patch = patch.object(db, "connect", return_value=self.conn)
        self.connection_patch.start()
        self.addCleanup(self.connection_patch.stop)
        db.execute("INSERT INTO users(id,email,password_hash,created_at) VALUES(1,'1@test.invalid','unused',?)", (db.now(),))
        store.init_schema()

    def _row(self, user, public_id):
        return next(row for row in chats.list_threads(user) if row["id"] == public_id)

    def test_pin_lands_on_top_and_drag_order_persists(self):
        first = chats.create_thread(1, model="offline")
        second = chats.create_thread(1, model="offline")
        pinned_first = chats.update_thread(1, first["id"], pinned=True, expected_revision=first["revision"])
        self.assertTrue(pinned_first["pinned"])
        self.assertEqual(pinned_first["pin_order"], 0)
        # A later pin takes the top slot, so the section reads newest-pin-first.
        pinned_second = chats.update_thread(1, second["id"], pinned=True, expected_revision=second["revision"])
        self.assertLess(pinned_second["pin_order"], pinned_first["pin_order"])
        # A drop writes the whole section's order, top first; a literal retry
        # replays the remembered receipt, while reusing the command id for a
        # different drop is rejected.
        reordered = chats.set_pin_order(1, [first["id"], second["id"]], command_id="drag-1")
        self.assertEqual([row["id"] for row in reordered["threads"]], [first["id"], second["id"]])
        rows = {row["id"]: row for row in chats.list_threads(1)}
        self.assertLess(rows[first["id"]]["pin_order"], rows[second["id"]]["pin_order"])
        retry = chats.set_pin_order(1, [first["id"], second["id"]], command_id="drag-1")
        self.assertEqual(retry, reordered)
        with self.assertRaises(chats.ChatValidationError):
            chats.set_pin_order(1, [second["id"], first["id"]], command_id="drag-1")
        # Unpin keeps the slot so a re-pin restores the row's place, and an
        # unpinned id inside a drag list is ignored, not written.
        unpinned = chats.update_thread(1, second["id"], pinned=False, expected_revision=rows[second["id"]]["revision"])
        self.assertFalse(unpinned["pinned"])
        self.assertEqual(unpinned["pin_order"], rows[second["id"]]["pin_order"])
        chats.set_pin_order(1, [second["id"], first["id"]], command_id="drag-2")
        rows = {row["id"]: row for row in chats.list_threads(1)}
        self.assertEqual(rows[first["id"]]["pin_order"], 1)  # first moved under the unpinned entry
        self.assertEqual(rows[second["id"]]["pin_order"], unpinned["pin_order"])
        self.assertFalse(rows[second["id"]]["pinned"])

    def test_root_finish_badges_but_child_finish_does_not(self):
        thread = chats.create_thread(1, model="offline")
        run = store.start_turn(1, thread["id"], "Hello", thread["revision"], "turn-1")["run"]
        child = store.start_child_run(
            user_id=1, thread_id=thread["id"], parent_run_id=run["id"],
            prompt="look around", description="explore",
        )
        store.finish_run(child["id"], {"content": "report", "tools": []}, "completed")
        self.assertFalse(self._row(1, thread["id"])["unread"], "a subagent report is not a finished conversation")
        store.finish_run(run["id"], {"content": "done", "tools": []}, "completed")
        self.assertTrue(self._row(1, thread["id"])["unread"], "the root turn finishing badges the row")
        # Viewing clears it exactly once; a second view is a no-op.
        self.assertTrue(chats.mark_thread_read(1, thread["id"]))
        self.assertFalse(self._row(1, thread["id"])["unread"])
        self.assertFalse(chats.mark_thread_read(1, thread["id"]))


if __name__ == "__main__":
    unittest.main()
