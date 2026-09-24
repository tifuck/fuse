from __future__ import annotations

import os
import asyncio
import json
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
_TMP = tempfile.mkdtemp(prefix="fuse-agent-")
os.environ.setdefault("DATABASE_PATH", str(Path(_TMP) / "agent.db"))
os.environ.setdefault("APP_SECRET", "x" * 32)

from app import chat_store as store, chats, db
from app.chat_store import SpawnCapError


class AgentCapsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="fuse-agent-")
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

    def _parent(self):
        thread = chats.create_thread(1, model="offline")
        result = store.start_turn(1, thread["id"], "Hello", thread["revision"], "turn")
        return thread, result["run"]

    def test_start_turn_omits_agent_when_coding_off(self):
        _thread, run = self._parent()
        self.assertNotIn("agent", run["request"])
        self.assertEqual(run.get("parent_run_id"), None)
        self.assertEqual(run.get("root_run_id"), run["id"])

    def test_coding_turn_does_not_401_when_vault_locked(self):
        from app import vault

        vault.lock_all()
        db.execute(
            "INSERT OR REPLACE INTO user_agent_settings(user_id, agent_coding) VALUES(1, 1)"
        )
        thread = chats.create_thread(1, model="offline")
        result = store.start_turn(1, thread["id"], "Hello", thread["revision"], "turn-coding")
        agent = result["run"]["request"]["agent"]
        self.assertTrue(agent["coding"])
        self.assertFalse(agent["project_session"])
        self.assertFalse(agent["vault_unlocked"])
        self.assertEqual(agent["max_completions"], 0)
        self.assertEqual(agent["remaining_seconds"], store.DEFAULT_REMAINING_SECONDS)

    def test_child_does_not_block_parent_unique_or_revision(self):
        thread, parent = self._parent()
        child = store.start_child_run(
            user_id=1,
            thread_id=thread["id"],
            parent_run_id=parent["id"],
            prompt="look around",
            description="explore",
        )
        self.assertTrue(child["parent_run_id"])
        self.assertEqual(child["root_run_id"], parent["id"])
        rev = chats.get_thread(1, thread["id"])["revision"]
        store.checkpoint(child["id"], {"content": "mid", "tools": []})
        store.finish_run(child["id"], {"content": "done", "tools": []}, "completed")
        again = chats.get_thread(1, thread["id"])
        self.assertEqual(again["revision"], rev)
        self.assertNotEqual(again["messages"][-1]["id"], child["message_id"])
        self.assertEqual(again["run"]["id"], parent["id"])

    def test_child_checkpoint_carries_permissions_key(self):
        # The dock permission queue is fed from agent.progress payload.permissions;
        # child asks never touch the thread transcript, so without this key a
        # subagent ask cannot render and always times out.
        thread, parent = self._parent()
        child = store.start_child_run(
            user_id=1,
            thread_id=thread["id"],
            parent_run_id=parent["id"],
            prompt="scan things",
            description="scanner",
        )
        expires = (datetime.now(timezone.utc) + timedelta(seconds=60)).strftime("%Y-%m-%dT%H:%M:%SZ")
        store.checkpoint(child["id"], {
            "content": "working",
            "tools": [],
            "permissions": [{
                "id": "pa_child1",
                "kind": "tool_permission",
                "status": "pending",
                "tool": "run_terminal_command",
                "host": "prod",
                "path": "",
                "cwd": "",
                "command": "cat /etc/shadow",
                "secret": False,
                "requester": "scanner (explore)",
                "reasons": ["outside the allowed roots"],
                "expires_at": expires,
            }],
        })
        row = db.fetchone(
            "SELECT payload FROM chat_events WHERE type='agent.progress' ORDER BY id DESC LIMIT 1"
        )
        payload = json.loads(row["payload"])
        self.assertIn("permissions", payload)
        perms = payload["permissions"]
        self.assertEqual(len(perms), 1)
        self.assertEqual(perms[0]["id"], "pa_child1")
        self.assertEqual(perms[0]["status"], "pending")
        self.assertEqual(perms[0]["requester"], "scanner (explore)")
        self.assertEqual(perms[0]["tool"], "run_terminal_command")

    def test_nested_off_rejects_grandchild(self):
        thread, parent = self._parent()
        child = store.start_child_run(
            user_id=1,
            thread_id=thread["id"],
            parent_run_id=parent["id"],
            prompt="child",
            description="c",
            allow_nested_spawn=False,
        )
        with self.assertRaises(SpawnCapError):
            store.start_child_run(
                user_id=1,
                thread_id=thread["id"],
                parent_run_id=child["id"],
                prompt="grand",
                description="g",
                depth=2,
                allow_nested_spawn=False,
            )

    def test_settle_all_pending_spawn_and_join_traces(self):
        thread, parent = self._parent()
        child = store.start_child_run(user_id=1, thread_id=thread["id"], parent_run_id=parent["id"],
                                      prompt="Inspect", description="Inspector")
        store.finish_run(child["id"], {"content": "", "error": "Child execution failed", "tools": []}, "failed")
        messages = [{"tools": [{"name": name, "child_id": child["id"], "status": "running"}]} for name in
                    ["spawn_subagent", "get_subagent_output"] * 16]
        store.settle_spawn_traces(self.conn, 1, messages)
        self.assertTrue(all(m["tools"][0]["status"] == "error" for m in messages))
        self.assertTrue(all(m["tools"][0]["error"] == "Child execution failed" for m in messages))

    def test_scratch_prompt_spawns_and_survives_a_failed_call(self):
        from app import agents
        from app.chat_tools import execute_tool

        thread, parent = self._parent()
        store.scratch_apply(parent["id"], [{"op": "put", "name": "review", "text": "Inspect the auth module."}])
        store.scratch_apply(parent["id"], [{"op": "append", "name": "review", "text": " Report findings only."}])
        with patch.object(agents.chat_runs, "start", new=AsyncMock(return_value=None)):
            text, trace = asyncio.run(agents.spawn(
                user={"id": 1}, request=None, parent_run_id=parent["id"], thread_id=thread["id"],
                arguments=json.dumps({"scratch": "review", "description": "Review auth", "background": True}),
            ))
        self.assertEqual(trace["status"], "running")
        child = store.get_run(1, trace["child_id"])
        self.assertIn("Inspect the auth module. Report findings only.", child["request"]["messages"][0]["content"])
        self.assertEqual(store.scratch_read(parent["id"], "review"), "Inspect the auth module. Report findings only.")
        missing, err = asyncio.run(agents.spawn(
            user={"id": 1}, request=None, parent_run_id=parent["id"], thread_id=thread["id"],
            arguments=json.dumps({"scratch": "missing", "description": "nope"}),
        ))
        self.assertEqual(err["status"], "error")
        self.assertIn("not found", missing)
        listed, rec = asyncio.run(execute_tool(
            "scratch", "{}", user_id=1, allowed_urls=set(), coding=True, session={"run_id": parent["id"]},
        ))
        self.assertEqual(rec["status"], "done")
        self.assertEqual(json.loads(listed)["slots"]["review"], len("Inspect the auth module. Report findings only."))

    def test_background_spawn_marks_progress_and_result(self):
        from app import agents

        thread, parent = self._parent()
        progress = []
        with patch.object(agents.chat_runs, "start", new=AsyncMock(return_value=None)):
            _, trace = asyncio.run(agents.spawn(user={"id": 1}, request=None, parent_run_id=parent["id"],
                                               thread_id=thread["id"], on_started=progress.append,
                                               arguments=json.dumps({"prompt": "Inspect", "description": "Inspect", "background": True})))
        self.assertEqual(trace["status"], "running")
        self.assertTrue(trace["background"])
        self.assertTrue(progress[0]["background"])

    def test_settle_active_child_survives_parent_interruption(self):
        thread, parent = self._parent()
        child = store.start_child_run(user_id=1, thread_id=thread["id"], parent_run_id=parent["id"],
                                      prompt="Inspect", description="Inspector", background=True)
        messages = [{"tools": [{"name": "get_subagent_output", "child_id": child["id"], "status": "interrupted"}]}]
        store.settle_spawn_traces(self.conn, 1, messages)
        self.assertEqual(messages[0]["tools"][0]["status"], "running")

    def test_nested_on_allows_depth_2(self):
        thread, parent = self._parent()
        child = store.start_child_run(
            user_id=1,
            thread_id=thread["id"],
            parent_run_id=parent["id"],
            prompt="child",
            description="c",
            allow_nested_spawn=True,
        )
        grand = store.start_child_run(
            user_id=1,
            thread_id=thread["id"],
            parent_run_id=child["id"],
            prompt="grand",
            description="g",
            depth=2,
            allow_nested_spawn=True,
        )
        self.assertEqual(grand["agent_depth"], 2)

    def test_failed_history_does_not_block_new_spawns(self):
        thread, parent = self._parent()
        for i in range(8):
            child = store.start_child_run(
                user_id=1,
                thread_id=thread["id"],
                parent_run_id=parent["id"],
                prompt=f"c{i}",
                description=f"c{i}",
            )
            store.finish_run(child["id"], {"content": "x", "tools": []}, "failed")
        again = store.start_child_run(
            user_id=1,
            thread_id=thread["id"],
            parent_run_id=parent["id"],
            prompt="next",
            description="next",
        )
        self.assertEqual(again["status"], "queued")

    def test_live_tree_cap(self):
        thread, parent = self._parent()
        for i in range(store.LIVE_TREE_CHILDREN):
            store.start_child_run(
                user_id=1,
                thread_id=thread["id"],
                parent_run_id=parent["id"],
                prompt=f"c{i}",
                description=f"c{i}",
            )
        with self.assertRaises(SpawnCapError):
            store.start_child_run(
                user_id=1,
                thread_id=thread["id"],
                parent_run_id=parent["id"],
                prompt="overflow",
                description="overflow",
            )

    def test_descendant_ids_are_strict_not_siblings(self):
        thread, parent = self._parent()
        a = store.start_child_run(
            user_id=1,
            thread_id=thread["id"],
            parent_run_id=parent["id"],
            prompt="a",
            description="a",
            allow_nested_spawn=True,
        )
        b = store.start_child_run(
            user_id=1,
            thread_id=thread["id"],
            parent_run_id=parent["id"],
            prompt="b",
            description="b",
            allow_nested_spawn=True,
        )
        grand = store.start_child_run(
            user_id=1,
            thread_id=thread["id"],
            parent_run_id=a["id"],
            prompt="g",
            description="g",
            depth=2,
            allow_nested_spawn=True,
        )
        self.assertEqual(store.descendant_ids(a["id"]), [grand["id"]])
        self.assertNotIn(b["id"], store.descendant_ids(a["id"]))
        self.assertCountEqual(store.descendant_ids(parent["id"]), [a["id"], b["id"], grand["id"]])

    def test_cancel_self_leaves_sibling_running(self):
        thread, parent = self._parent()
        a = store.start_child_run(
            user_id=1,
            thread_id=thread["id"],
            parent_run_id=parent["id"],
            prompt="a",
            description="a",
        )
        b = store.start_child_run(
            user_id=1,
            thread_id=thread["id"],
            parent_run_id=parent["id"],
            prompt="b",
            description="b",
        )
        ids = store.request_cancel_self(1, a["id"])
        self.assertEqual(ids, [a["id"]])
        self.assertTrue(store.get_run(1, a["id"])["cancel_requested"])
        self.assertFalse(store.get_run(1, b["id"])["cancel_requested"])
        self.assertFalse(store.get_run(1, parent["id"])["cancel_requested"])
        from app import chat_runs as runs

        with patch.object(runs, "cancel") as mocked:
            for ident in ids:
                runs.cancel(ident)
            mocked.assert_called_once_with(a["id"])
            self.assertEqual(store.descendant_ids(a["id"]), [])

    def test_purge_orphan_subagents_on_rewind(self):
        thread, parent = self._parent()
        child = store.start_child_run(
            user_id=1,
            thread_id=thread["id"],
            parent_run_id=parent["id"],
            prompt="look",
            description="explore",
        )
        store.finish_run(child["id"], {"content": "done", "tools": []}, "completed")
        store.finish_run(parent["id"], {
            "content": "ok",
            "tools": [{"name": "spawn_subagent", "status": "done", "child_id": child["id"], "id": "ts_0_0"}],
        }, "completed")
        view = chats.get_thread(1, thread["id"])
        kept = [m for m in view["messages"] if m["role"] == "user"]
        chats.update_thread(1, thread["id"], messages=kept, expected_revision=view["revision"])
        self.assertIsNone(store.get_run(1, child["id"]))
        self.assertIsNone(store.get_run(1, parent["id"]))

    def test_wake_auto_survives_parse(self):
        parsed = chats.parse_messages(
            [{"role": "user", "content": "AUTOMATED EVENT", "auto": True}],
            strict=True,
        )
        self.assertTrue(parsed[0]["auto"])

    def test_child_preloads_workspace_and_full_budget(self):
        thread, parent = self._parent()
        child = store.start_child_run(
            user_id=1,
            thread_id=thread["id"],
            parent_run_id=parent["id"],
            prompt="look around",
            description="explore",
        )
        agent = child["request"]["agent"]
        self.assertEqual(agent["max_completions"], 0)
        self.assertIn("read_file", agent["loaded_tools"])
        self.assertIn("run_terminal_command", agent["loaded_tools"])
        self.assertIn("write_file", agent["loaded_tools"])

    def test_explore_child_does_not_preload_writes(self):
        thread, parent = self._parent()
        child = store.start_child_run(
            user_id=1,
            thread_id=thread["id"],
            parent_run_id=parent["id"],
            prompt="look",
            description="explore",
            subagent_type="explore",
        )
        loaded = child["request"]["agent"]["loaded_tools"]
        self.assertIn("read_file", loaded)
        self.assertIn("grep", loaded)
        self.assertNotIn("write_file", loaded)
        self.assertNotIn("search_replace", loaded)

    def test_resume_skips_spawn_cap_and_copies_loaded(self):
        thread, parent = self._parent()
        children = []
        for i in range(store.PER_AGENT_PARENT):
            child = store.start_child_run(
                user_id=1,
                thread_id=thread["id"],
                parent_run_id=parent["id"],
                prompt=f"c{i}",
                description=f"c{i}",
            )
            children.append(child)
            store.finish_run(child["id"], {"content": f"done {i}", "tools": []}, "completed")
        with self.assertRaises(SpawnCapError):
            store.start_child_run(
                user_id=1,
                thread_id=thread["id"],
                parent_run_id=parent["id"],
                prompt="overflow",
                description="overflow",
            )
        resumed = store.start_child_run(
            user_id=1,
            thread_id=thread["id"],
            parent_run_id=parent["id"],
            prompt="continue",
            description="resume",
            resume_from_id=children[0]["id"],
        )
        self.assertEqual(resumed["id"], children[0]["id"])
        self.assertEqual(resumed["status"], "queued")
        self.assertTrue(resumed["request"]["agent"]["skip_sanitize"])
        self.assertEqual(resumed["request"]["agent"]["max_completions"], 0)
        self.assertIn("read_file", resumed["request"]["agent"]["loaded_tools"])
        seed = resumed["request"]["messages"]
        self.assertEqual(seed[0]["role"], "user")
        self.assertEqual(seed[-1]["content"], "continue")
        self.assertEqual(store.get_run(1, children[0]["id"])["status"], "queued")

    def test_task_envelope_and_parent_inject(self):
        env = store.render_task_output(
            child_id="abc",
            state="completed",
            text="findings here",
            description="Audit search",
        )
        self.assertIn('<task id="abc" state="completed">', env)
        self.assertIn("<task_result>", env)
        self.assertIn("findings here", env)
        self.assertIn("task_id: abc", env)
        thread, parent = self._parent()
        self.assertTrue(store.queue_child_result(parent["id"], env, child_id="abc"))
        got = store.take_child_results(parent["id"])
        self.assertEqual(got, [env])
        # Peek is non-destructive until the envelope is durable in the transcript.
        self.assertEqual(store.take_child_results(parent["id"]), [env])
        store.save_turn_progress(parent["id"], [{"role": "user", "content": env}])
        self.assertEqual(store.take_child_results(parent["id"]), [])

    def test_followup_on_live_child(self):
        thread, parent = self._parent()
        child = store.start_child_run(
            user_id=1,
            thread_id=thread["id"],
            parent_run_id=parent["id"],
            prompt="look",
            description="explore",
        )
        self.assertTrue(store.queue_followup(child["id"], "also check tests"))
        self.assertEqual(store.take_followups(child["id"]), ["also check tests"])
        self.assertEqual(store.take_followups(child["id"]), ["also check tests"])
        store.save_turn_progress(child["id"], [
            {"role": "user", "content": "look"},
            {"role": "user", "content": "also check tests"},
        ])
        self.assertEqual(store.take_followups(child["id"]), [])
        store.finish_run(child["id"], {"content": "done", "tools": []}, "completed")
        self.assertFalse(store.queue_followup(child["id"], "too late"))

    def test_queued_result_does_not_mark_wake_until_taken(self):
        thread, parent = self._parent()
        child = store.start_child_run(
            user_id=1,
            thread_id=thread["id"],
            parent_run_id=parent["id"],
            prompt="bg",
            description="bg",
            background=True,
        )
        store.finish_run(child["id"], {"content": "report", "tools": []}, "completed")
        env = store.render_task_output(child_id=child["id"], state="completed", text="report", description="bg")
        self.assertTrue(child["request"]["agent"].get("background"))
        self.assertTrue(store.queue_child_result(parent["id"], env, child_id=child["id"]))
        cands = store.wake_candidates(1, parent["thread_id"])
        self.assertTrue(any(item["id"] == child["id"] for item in cands))
        # Peeking alone does not acknowledge; wake_sent lands only when the
        # transcript persist delivers the envelope.
        store.take_child_results(parent["id"])
        cands = store.wake_candidates(1, parent["thread_id"])
        self.assertTrue(any(item["id"] == child["id"] for item in cands))
        store.save_turn_progress(parent["id"], [{"role": "user", "content": env}])
        self.assertEqual(store.take_child_results(parent["id"]), [])
        cands = store.wake_candidates(1, parent["thread_id"])
        self.assertFalse(any(item["id"] == child["id"] for item in cands))

    def _wake_turn(self, thread, parent):
        store.finish_run(parent["id"], {"content": "ok", "tools": []}, "completed")
        rev = chats.get_thread(1, thread["id"])["revision"]
        wake = store.start_turn(1, thread["id"], "AUTOMATED EVENT - background report", rev, "wake-t")["run"]
        return wake

    def test_wake_turn_completing_after_respawn_keeps_child_wakeable(self):
        # Production race: a wake turn resumed the child (spawn task_id) and
        # then completed; its finalize used to mark the child wake_sent even
        # though the new leg had not reported yet, stranding the next report.
        thread, parent = self._parent()
        child = store.start_child_run(
            user_id=1,
            thread_id=thread["id"],
            parent_run_id=parent["id"],
            prompt="bg",
            description="bg",
            background=True,
        )
        store.finish_run(child["id"], {"content": "first report", "tools": []}, "completed")
        wake = self._wake_turn(thread, parent)
        store.mark_run_wake(wake["id"], [child["id"]])
        resumed = store.start_child_run(
            user_id=1,
            thread_id=thread["id"],
            parent_run_id=wake["id"],
            prompt="fix the findings",
            description="bg",
            resume_from_id=child["id"],
        )
        self.assertEqual(resumed["status"], "queued")
        store.finish_run(wake["id"], {"content": "relayed", "tools": []}, "completed")
        store.mark_wake_sent([child["id"]], pending_guard_run=wake["id"])
        agent = store.get_run(1, child["id"])["request"]["agent"]
        self.assertFalse(agent.get("wake_sent"))
        # The new leg finishing must still wake the thread with its report.
        # (Outliving its spawning turn marks the child background, exactly as
        # the finalize path does for a foreground spawn whose parent ended.)
        store.mark_child_background(child["id"])
        store.finish_run(child["id"], {"content": "second report", "tools": []}, "completed")
        cands = store.wake_candidates(1, parent["thread_id"])
        self.assertTrue(any(item["id"] == child["id"] for item in cands))
        self.assertIn("second report", next(c for c in cands if c["id"] == child["id"])["summary"])

    def test_mark_wake_sent_skips_active_and_pending_guard_children(self):
        thread, parent = self._parent()
        child = store.start_child_run(
            user_id=1,
            thread_id=thread["id"],
            parent_run_id=parent["id"],
            prompt="bg",
            description="bg",
            background=True,
        )
        store.finish_run(child["id"], {"content": "report", "tools": []}, "completed")
        wake = self._wake_turn(thread, parent)
        env = store.render_task_output(child_id=child["id"], state="completed", text="report", description="bg")
        self.assertTrue(store.queue_child_result(wake["id"], env, child_id=child["id"]))
        # Unconsumed envelope on the finishing wake run: stays unacknowledged.
        store.mark_wake_sent([child["id"]], pending_guard_run=wake["id"])
        self.assertFalse(store.get_run(1, child["id"])["request"]["agent"].get("wake_sent"))
        # Consumed envelope: acknowledged now that the child is terminal.
        store.save_turn_progress(wake["id"], [{"role": "user", "content": env}])
        store.mark_wake_sent([child["id"]])
        self.assertTrue(store.get_run(1, child["id"])["request"]["agent"].get("wake_sent"))
        # A re-spawned (queued/running) child is never acknowledged.
        resumed = store.start_child_run(
            user_id=1,
            thread_id=thread["id"],
            parent_run_id=wake["id"],
            prompt="again",
            description="bg",
            resume_from_id=child["id"],
        )
        self.assertEqual(resumed["status"], "queued")
        store.mark_wake_sent([child["id"]])
        self.assertFalse(store.get_run(1, child["id"])["request"]["agent"].get("wake_sent"))

    def test_bump_wake_attempts_skips_respawned_child(self):
        thread, parent = self._parent()
        child = store.start_child_run(
            user_id=1,
            thread_id=thread["id"],
            parent_run_id=parent["id"],
            prompt="bg",
            description="bg",
            background=True,
        )
        store.finish_run(child["id"], {"content": "report", "tools": []}, "completed")
        resumed = store.start_child_run(
            user_id=1,
            thread_id=thread["id"],
            parent_run_id=parent["id"],
            prompt="again",
            description="bg",
            resume_from_id=child["id"],
        )
        store.bump_wake_attempts([resumed["id"]])
        self.assertEqual(store.get_run(1, resumed["id"])["request"]["agent"].get("wake_attempts") or 0, 0)

    def test_save_turn_progress_does_not_ack_respawned_child(self):
        # Injection ack path: the parent delivered the old envelope but has
        # already re-spawned the child; the new leg must stay wakeable.
        thread, parent = self._parent()
        child = store.start_child_run(
            user_id=1,
            thread_id=thread["id"],
            parent_run_id=parent["id"],
            prompt="bg",
            description="bg",
            background=True,
        )
        store.finish_run(child["id"], {"content": "report one", "tools": []}, "completed")
        env = store.render_task_output(child_id=child["id"], state="completed", text="report one", description="bg")
        self.assertTrue(store.queue_child_result(parent["id"], env, child_id=child["id"]))
        resumed = store.start_child_run(
            user_id=1,
            thread_id=thread["id"],
            parent_run_id=parent["id"],
            prompt="continue",
            description="bg",
            resume_from_id=child["id"],
        )
        self.assertEqual(resumed["status"], "queued")
        store.save_turn_progress(parent["id"], [{"role": "user", "content": env}])
        self.assertFalse(store.get_run(1, child["id"])["request"]["agent"].get("wake_sent"))
        # A newer unconsumed envelope also keeps the child unacknowledged.
        store.finish_run(child["id"], {"content": "report two", "tools": []}, "completed")
        env2 = store.render_task_output(child_id=child["id"], state="completed", text="report two", description="bg")
        self.assertTrue(store.queue_child_result(parent["id"], env2, child_id=child["id"]))
        store.save_turn_progress(parent["id"], [{"role": "user", "content": env}, {"role": "user", "content": env2[:20]}])
        self.assertFalse(store.get_run(1, child["id"])["request"]["agent"].get("wake_sent"))
        store.save_turn_progress(parent["id"], [{"role": "user", "content": env2}])
        self.assertTrue(store.get_run(1, child["id"])["request"]["agent"].get("wake_sent"))

    def test_user_stopped_root_blocks_wake_turns(self):
        from app import chat_runs as runs
        from app import command_jobs

        thread, parent = self._parent()
        child = store.start_child_run(
            user_id=1,
            thread_id=thread["id"],
            parent_run_id=parent["id"],
            prompt="bg",
            description="bg",
            background=True,
        )
        store.finish_run(child["id"], {"content": "report", "tools": []}, "completed")
        store.finish_run(parent["id"], {"content": "ok", "tools": []}, "cancelled")
        with patch.object(runs, "start", new=AsyncMock(return_value=None)), \
                patch.object(command_jobs, "pending_envelopes", return_value=[]):
            asyncio.run(runs._wake_parent(1, parent["thread_id"]))
        roots = db.fetchall(
            "SELECT id FROM chat_runs WHERE thread_id=? AND parent_run_id IS NULL",
            (parent["thread_id"],),
        )
        self.assertEqual(len(roots), 1)  # no automated wake after a hard user stop

        # A completed root still wakes: the loop only breaks on user Stop.
        thread2 = chats.create_thread(1, model="offline")
        parent2 = store.start_turn(1, thread2["id"], "Hello", thread2["revision"], "turn-2")["run"]
        child2 = store.start_child_run(
            user_id=1,
            thread_id=thread2["id"],
            parent_run_id=parent2["id"],
            prompt="bg",
            description="bg",
            background=True,
        )
        store.finish_run(child2["id"], {"content": "report", "tools": []}, "completed")
        store.finish_run(parent2["id"], {"content": "ok", "tools": []}, "completed")
        with patch.object(runs, "start", new=AsyncMock(return_value=None)), \
                patch.object(command_jobs, "pending_envelopes", return_value=[]):
            asyncio.run(runs._wake_parent(1, parent2["thread_id"]))
        roots = db.fetchall(
            "SELECT id FROM chat_runs WHERE thread_id=? AND parent_run_id IS NULL",
            (parent2["thread_id"],),
        )
        self.assertEqual(len(roots), 2)  # original + wake turn

    def test_settle_wake_children_acks_delivered_envelope_only(self):
        thread, parent = self._parent()
        child_a = store.start_child_run(
            user_id=1, thread_id=thread["id"], parent_run_id=parent["id"],
            prompt="a", description="a", background=True,
        )
        child_b = store.start_child_run(
            user_id=1, thread_id=thread["id"], parent_run_id=parent["id"],
            prompt="b", description="b", background=True,
        )
        store.finish_run(child_a["id"], {"content": "alpha", "tools": []}, "completed")
        store.finish_run(child_b["id"], {"content": "beta", "tools": []}, "completed")
        store.finish_run(parent["id"], {"content": "ok", "tools": []}, "completed")
        rev = chats.get_thread(1, thread["id"])["revision"]
        env_a = store.render_task_output(child_id=child_a["id"], state="completed", text="alpha", description="a")
        wake = store.start_turn(1, thread["id"], f"AUTOMATED EVENT\n\n{env_a}", rev, "wake-s", auto=True)["run"]
        store.mark_run_wake(wake["id"], [child_a["id"], child_b["id"]])
        # Stopped by the user: A's bubble was already in the turn seed, B's
        # report never made it into any transcript.
        store.finish_run(wake["id"], {"content": "", "tools": []}, "cancelled")
        store.settle_wake_children(wake["id"], [child_a["id"], child_b["id"]])
        a = store.get_run(1, child_a["id"])["request"]["agent"]
        b = store.get_run(1, child_b["id"])["request"]["agent"]
        self.assertTrue(a.get("wake_sent"))
        self.assertEqual(a.get("wake_attempts") or 0, 0)
        self.assertFalse(b.get("wake_sent"))
        self.assertEqual(b.get("wake_attempts") or 0, 1)

    def test_get_output_joins_until_child_finishes(self):
        # The join default must block for the full budget against a long
        # background child instead of returning "still running" after 60s and
        # forcing the model into a re-poll loop.
        from app import agents

        thread, parent = self._parent()
        child = store.start_child_run(
            user_id=1,
            thread_id=thread["id"],
            parent_run_id=parent["id"],
            prompt="bg",
            description="long audit",
            background=True,
        )
        waits = []

        async def fake_wait(run_id, timeout):
            waits.append(timeout)
            store.finish_run(run_id, {"content": "audit report", "tools": []}, "completed")

        with patch.object(agents.chat_runs, "wait_for_run", new=fake_wait):
            text, rec = asyncio.run(agents.get_output(
                user_id=1,
                arguments=json.dumps({"id": child["id"]}),
                thread_id=thread["id"],
                parent_run_id=parent["id"],
            ))
        self.assertEqual(rec["status"], "done")
        self.assertIn("audit report", text)
        self.assertGreaterEqual(waits[0], 595)

    def test_stranded_report_rides_with_next_user_turn(self):
        # Hard user stop strands the report; it must not start a turn, and it
        # must be delivered (and acknowledged) with the user's next message.
        thread, parent = self._parent()
        child = store.start_child_run(
            user_id=1,
            thread_id=thread["id"],
            parent_run_id=parent["id"],
            prompt="bg",
            description="bg",
            background=True,
        )
        store.finish_run(child["id"], {"content": "late report", "tools": []}, "completed")
        store.finish_run(parent["id"], {"content": "ok", "tools": []}, "cancelled")
        rev = chats.get_thread(1, thread["id"])["revision"]
        nxt = store.start_turn(1, thread["id"], "what happened while I was away?", rev, "next-t")["run"]
        cands = store.wake_candidates(1, parent["thread_id"])
        self.assertTrue(any(item["id"] == child["id"] for item in cands))
        item = next(c for c in cands if c["id"] == child["id"])
        envelope = store.render_task_output(
            child_id=item["id"],
            state=item["status"],
            text=item["summary"],
            description=item["description"],
        )
        self.assertTrue(store.queue_child_result(nxt["id"], envelope, child_id=child["id"]))
        self.assertEqual(store.take_child_results(nxt["id"]), [envelope])
        block = "[Fuse] AUTOMATED EVENT — not written by the user. Background results:\n\n" + envelope
        store.save_turn_progress(nxt["id"], [{"role": "user", "content": block}])
        self.assertTrue(store.get_run(1, child["id"])["request"]["agent"].get("wake_sent"))
        self.assertEqual(store.take_child_results(nxt["id"]), [])

    def test_child_writeup_never_returns_status_word(self):
        self.assertEqual(store.child_writeup({"message": {}, "request": {}}), "")
        self.assertIn(
            "read_file",
            store.child_writeup(
                {
                    "message": {
                        "content": "",
                        "tools": [{"name": "read_file", "preview": "/tmp/x.py", "status": "done"}],
                    },
                    "request": {},
                }
            ),
        )

    def test_child_writeup_combines_error_with_real_partial(self):
        text = store.child_writeup(
            {
                "message": {
                    "content": "patched foo.py and ran the tests",
                    "error": "The subagent experienced difficulties talking to the model after 4 retries.",
                },
                "request": {},
            }
        )
        self.assertIn("experienced difficulties", text)
        self.assertIn("patched foo.py", text)

    def test_child_writeup_prefers_error_over_content(self):
        text = store.child_writeup(
            {
                "message": {
                    "content": "Our servers are currently overloaded. Please try again later.",
                    "error": "The subagent experienced difficulties talking to the model after 4 retries.",
                    "tools": [{"name": "read_file", "preview": "/tmp/x.py", "status": "done"}],
                },
                "request": {},
            }
        )
        self.assertIn("experienced difficulties", text)
        self.assertNotIn("Our servers", text)

    def test_child_uses_passed_model(self):
        thread, parent = self._parent()
        child = store.start_child_run(
            user_id=1,
            thread_id=thread["id"],
            parent_run_id=parent["id"],
            prompt="look around",
            description="explore",
            model="grok-4.6",
        )
        self.assertEqual(child["request"]["model"], "grok-4.6")
        clone = store.start_child_run(
            user_id=1,
            thread_id=thread["id"],
            parent_run_id=parent["id"],
            prompt="look around",
            description="clone",
        )
        self.assertEqual(clone["request"]["model"], parent["request"]["model"])

    def test_cancel_requested_event_carries_status(self):
        thread, parent = self._parent()
        child = store.start_child_run(
            user_id=1,
            thread_id=thread["id"],
            parent_run_id=parent["id"],
            prompt="look",
            description="explore",
        )
        cursor = store.cursor(1)
        store.request_cancel_self(1, child["id"])
        events = [e for e in store.events_after(1, cursor) if e["type"] == "agent.cancel_requested"]
        self.assertEqual(len(events), 1)
        payload = events[0]["payload"]
        self.assertEqual(payload["run_id"], child["id"])
        self.assertEqual(payload["child_id"], child["id"])
        self.assertEqual(payload["status"], "queued")
        self.assertTrue(payload["cancel_requested"])

    def test_abandoned_parent_maps_failed_child_trace_to_error(self):
        thread, parent = self._parent()
        child = store.start_child_run(
            user_id=1,
            thread_id=thread["id"],
            parent_run_id=parent["id"],
            prompt="look",
            description="explore",
        )
        store.finish_run(child["id"], {"content": "", "error": "Child execution failed", "tools": []}, "failed")
        store.checkpoint(parent["id"], {
            "content": "working",
            "tools": [{"id": "t1", "name": "spawn_subagent", "child_id": child["id"], "status": "running"}],
        })
        self.assertTrue(store.abort_active(parent["id"], "cancelled"))
        tools = store.get_run(1, parent["id"])["message"]["tools"]
        self.assertEqual(tools[0]["status"], "error")
        self.assertEqual(tools[0]["execution_status"], "failed")

    def test_force_terminal_agent_event_includes_child_id(self):
        thread, parent = self._parent()
        child = store.start_child_run(
            user_id=1,
            thread_id=thread["id"],
            parent_run_id=parent["id"],
            prompt="look",
            description="explore",
        )
        cursor = store.cursor(1)
        store._force_terminal(child["id"], "failed", {"content": "", "error": "could not start"})
        events = [e for e in store.events_after(1, cursor) if e["type"] == "agent.failed"]
        self.assertEqual(events[0]["payload"]["child_id"], child["id"])
        self.assertEqual(events[0]["payload"]["status"], "failed")

    def test_list_thread_agents_reports_wake_state(self):
        thread, parent = self._parent()
        child = store.start_child_run(
            user_id=1,
            thread_id=thread["id"],
            parent_run_id=parent["id"],
            prompt="bg",
            description="bg",
            background=True,
        )
        store.finish_run(child["id"], {"content": "report", "tools": []}, "completed")
        rows = store.list_thread_agents(1, parent["thread_id"])
        self.assertEqual(rows[0]["wake_state"], "retrying")
        store.bump_wake_attempts([child["id"]])
        store.bump_wake_attempts([child["id"]])
        rows = store.list_thread_agents(1, parent["thread_id"])
        self.assertEqual(rows[0]["wake_state"], "dropped")
        store.mark_wake_sent([child["id"]])
        rows = store.list_thread_agents(1, parent["thread_id"])
        self.assertEqual(rows[0]["wake_state"], "delivered")

    def test_list_thread_agents_marks_stalled_and_waiting(self):
        thread, parent = self._parent()
        child = store.start_child_run(
            user_id=1,
            thread_id=thread["id"],
            parent_run_id=parent["id"],
            prompt="look",
            description="explore",
        )
        db.execute("UPDATE chat_runs SET status='running' WHERE id=?", (child["id"],))
        stale = (datetime.now(timezone.utc) - timedelta(seconds=200)).strftime("%Y-%m-%dT%H:%M:%SZ")
        db.execute("UPDATE chat_runs SET updated_at=? WHERE id=?", (stale, child["id"]))
        rows = store.list_thread_agents(1, parent["thread_id"])
        self.assertEqual(rows[0]["current"], "stalled")
        self.assertGreaterEqual(rows[0]["quiet_s"], 180)
        waiting = (datetime.now(timezone.utc) - timedelta(seconds=90)).strftime("%Y-%m-%dT%H:%M:%SZ")
        db.execute("UPDATE chat_runs SET updated_at=? WHERE id=?", (waiting, child["id"]))
        rows = store.list_thread_agents(1, parent["thread_id"])
        self.assertEqual(rows[0]["current"], "waiting")
        db.execute("UPDATE chat_runs SET updated_at=? WHERE id=?", (db.now(), child["id"]))
        rows = store.list_thread_agents(1, parent["thread_id"])
        self.assertEqual(rows[0]["current"], "thinking")

    def test_spawn_start_failure_fails_queued_child(self):
        from app import agents

        thread, parent = self._parent()
        with patch.object(agents.chat_runs, "start", new=AsyncMock(side_effect=RuntimeError("worker boom"))):
            text, trace = asyncio.run(agents.spawn(
                user={"id": 1},
                request=None,
                parent_run_id=parent["id"],
                thread_id=thread["id"],
                arguments=json.dumps({"prompt": "Inspect"}),
            ))
        self.assertEqual(trace["status"], "error")
        self.assertIn("worker boom", text)
        row = store.get_run(1, trace["child_id"])
        self.assertEqual(row["status"], "failed")

    def test_child_display_keeps_tools_and_followups(self):
        thread, parent = self._parent()
        child = store.start_child_run(
            user_id=1,
            thread_id=thread["id"],
            parent_run_id=parent["id"],
            prompt="inspect src",
            description="explore",
        )
        store.save_turn_progress(child["id"], [
            {"role": "user", "content": "inspect src"},
            {"role": "assistant", "content": "", "tool_calls": [
                {"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": '{"path":"/tmp/a.py"}'}}
            ]},
            {"role": "tool", "tool_call_id": "c1", "content": "print(1)"},
            {"role": "assistant", "content": "file has a print"},
            {"role": "user", "content": "[Fuse] Follow-up from the parent — not written by the end user.\n\nalso tests"},
            {"role": "assistant", "content": "tests look fine"},
        ])
        store.finish_run(child["id"], {"content": "tests look fine", "tools": [
            {"id": "c1", "name": "read_file", "status": "done", "path": "/tmp/a.py", "preview": "print(1)"},
        ]}, "completed")
        view = store.get_run(1, child["id"])
        hist = store.child_display_messages(view, include_current=False)
        roles = [item["role"] for item in hist]
        self.assertEqual(roles[0], "user")
        # Current-leg tools belong only to the live/current bubble. The old
        # assertion required the duplicate historical copy that caused flicker.
        self.assertFalse(any(item.get("tools") for item in hist))
        self.assertEqual([tool["id"] for tool in view["message"]["tools"]], ["c1"])
        self.assertTrue(any(item.get("auto") for item in hist if item["role"] == "user"))
        self.assertNotEqual((hist[-1].get("content") or "").strip(), "tests look fine")
        meter = store.child_context_meter(view)
        self.assertIsNotNone(meter)
        self.assertGreater(meter["used"], 0)
        self.assertIn("tools", meter.get("parts") or {})
        self.assertGreater(meter["limit"], 0)
        rows = store.list_thread_agents(1, parent["thread_id"])
        self.assertTrue(rows)

    def test_child_display_resume_keeps_prior_tools_before_prompt(self):
        view = {
            "status": "queued",
            "message_id": "m",
            "message": {"content": "", "tools": []},
            "request": {"messages": [], "model": "t"},
            "provider_messages": [
                {"role": "user", "content": "initial prompt"},
                {"role": "assistant", "content": "", "tool_calls": [
                    {"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": "{}"}},
                    {"id": "c2", "type": "function", "function": {"name": "grep", "arguments": "{}"}},
                ]},
                {"role": "tool", "tool_call_id": "c1", "content": "r1"},
                {"role": "tool", "tool_call_id": "c2", "content": "r2"},
                {"role": "user", "content": "resume where you left off"},
            ],
        }
        hist = store.child_display_messages(view, include_current=False)
        roles = [(item["role"], len(item.get("tools") or [])) for item in hist]
        self.assertEqual(roles, [("user", 0), ("assistant", 2), ("user", 0)])
        self.assertEqual(hist[2]["content"], "resume where you left off")

    def test_child_display_resume_live_keeps_prior_tools(self):
        view = {
            "status": "running",
            "message_id": "m",
            "message": {"content": "", "tools": [{"id": "live1", "name": "grep", "status": "running"}]},
            "request": {"messages": [], "model": "t"},
            "provider_messages": [
                {"role": "user", "content": "initial prompt"},
                {"role": "assistant", "content": "", "tool_calls": [
                    {"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": "{}"}},
                ]},
                {"role": "tool", "tool_call_id": "c1", "content": "r1"},
                {"role": "user", "content": "resume where you left off"},
            ],
        }
        hist = store.child_display_messages(view, include_current=True)
        self.assertEqual([item["role"] for item in hist], ["user", "assistant", "user", "assistant"])
        self.assertEqual(len(hist[1].get("tools") or []), 1)
        self.assertEqual((hist[1]["tools"][0] or {}).get("name"), "read_file")
        self.assertTrue(hist[-1].get("current"))
        self.assertEqual((hist[-1]["tools"][0] or {}).get("id"), "live1")

    def test_child_display_starts_at_latest_handoff(self):
        from app import context

        rows = store.child_display_messages({
            "status": "running",
            "message_id": "m",
            "message": {"content": "", "tools": [{"id": "old", "name": "read_file"}]},
            "request": {},
            "provider_messages": [
                {"role": "user", "content": "original task"},
                {"role": "assistant", "content": "", "tool_calls": [
                    {"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": "{}"}},
                ]},
                {"role": "tool", "tool_call_id": "c1", "content": "old evidence"},
                {"role": "user", "content": context.COMPACT_PREAMBLE + "kept the bug in foo.py"},
                {"role": "assistant", "content": context.COMPACT_ACK},
                {"role": "assistant", "content": "", "tool_calls": [
                    {"id": "c2", "type": "function", "function": {"name": "grep", "arguments": "{}"}},
                ]},
                {"role": "tool", "tool_call_id": "c2", "content": "new evidence"},
                {"role": "assistant", "content": "continuing"},
            ],
        })
        self.assertEqual([row.get("role") for row in rows], ["marker", "assistant"])
        self.assertEqual(rows[0]["summary"], "kept the bug in foo.py")
        self.assertEqual((rows[1]["tools"][0] or {}).get("name"), "grep")
        self.assertEqual(rows[1]["content"], "continuing")
        self.assertNotIn("old evidence", " ".join(str(row.get("content") or "") for row in rows))

    def test_fold_handoff_drops_precompact_tools(self):
        from app import chat_runs, context

        output = chat_runs.Output({
            "message": {
                "content": "looked around",
                "reasoning": "thinking",
                "tools": [{"id": "c1", "name": "read_file", "status": "done"}],
                "segments": [
                    {"type": "tool", "id": "c1"},
                    {"type": "content", "text": "looked around"},
                ],
            }
        })
        event = context.compact_handoff_event("kept foo.py")["fuse"]
        output.apply({"fuse": event})
        stored = output.stored()
        self.assertEqual(stored["tools"], [])
        self.assertEqual(stored["content"], "")
        self.assertEqual(stored["segments"][0]["kind"], "compaction")
        self.assertEqual(stored["segments"][0]["summary"], "kept foo.py")

    def test_child_display_hides_compact_ack(self):
        from app import context

        rows = store.child_display_messages({
            "status": "running",
            "message_id": "m",
            "message": {"content": ""},
            "request": {},
            "provider_messages": [
                {"role": "user", "content": "inspect"},
                {"role": "user", "content": context.COMPACT_PREAMBLE + "kept the bug in foo.py"},
                {"role": "assistant", "content": context.COMPACT_ACK},
                {"role": "assistant", "content": "still working"},
            ],
        })
        blob = " ".join(str(row.get("content") or "") for row in rows)
        self.assertNotIn(context.COMPACT_ACK, blob)
        self.assertNotIn("inspect", blob)
        handoff = next(row for row in rows if row.get("compaction"))
        self.assertEqual(handoff["role"], "marker")
        self.assertIn("foo.py", handoff["summary"])
        self.assertEqual([row.get("role") for row in rows], ["marker", "assistant"])
        self.assertTrue(any(row.get("role") == "assistant" and row.get("content") == "still working" for row in rows))
        self.assertEqual(store.last_assistant_text([
            {"role": "assistant", "content": context.COMPACT_ACK},
            {"role": "assistant", "content": "real report"},
        ]), "real report")

    def test_wake_uses_child_writeup_not_empty_content(self):
        thread, parent = self._parent()
        child = store.start_child_run(
            user_id=1,
            thread_id=thread["id"],
            parent_run_id=parent["id"],
            prompt="bg",
            description="bg",
            background=True,
        )
        store.save_turn_progress(child["id"], [
            {"role": "user", "content": "bg"},
            {"role": "assistant", "content": "found the bug in foo.py"},
        ])
        store.finish_run(child["id"], {"content": "", "tools": [
            {"name": "read_file", "preview": "foo.py", "status": "done"},
        ]}, "completed")
        cands = store.wake_candidates(1, parent["thread_id"])
        self.assertTrue(cands)
        self.assertIn("found the bug", cands[0]["summary"])

    def test_resume_keeps_prior_host_and_cwd(self):
        thread, parent = self._parent()
        child = store.start_child_run(
            user_id=1,
            thread_id=thread["id"],
            parent_run_id=parent["id"],
            prompt="look around",
            description="explore",
            host_id=9,
            cwd="/srv/app",
        )
        store.finish_run(child["id"], {"content": "done", "tools": []}, "completed")
        req = parent["request"]
        req.setdefault("agent", {})["bound_host_id"] = 3
        req["agent"]["bound_cwd"] = "/elsewhere"
        db.execute("UPDATE chat_runs SET request=? WHERE id=?", (store.dumps(req), parent["id"]))
        resumed = store.start_child_run(
            user_id=1,
            thread_id=thread["id"],
            parent_run_id=parent["id"],
            prompt="continue",
            description="explore",
            resume_from_id=child["id"],
        )
        agent = resumed["request"]["agent"]
        self.assertEqual(agent["bound_host_id"], 9)
        self.assertEqual(agent["bound_cwd"], "/srv/app")
        self.assertEqual(resumed["created_at"], child["created_at"])

    def test_resume_keeps_child_model(self):
        thread, parent = self._parent()
        child = store.start_child_run(
            user_id=1,
            thread_id=thread["id"],
            parent_run_id=parent["id"],
            prompt="look around",
            description="explore",
            model="grok-4.6",
        )
        store.finish_run(child["id"], {"content": "done", "tools": []}, "completed")
        resumed = store.start_child_run(
            user_id=1,
            thread_id=thread["id"],
            parent_run_id=parent["id"],
            prompt="continue",
            description="explore",
            resume_from_id=child["id"],
            model="grok-4.6",
        )
        self.assertEqual(resumed["id"], child["id"])
        self.assertEqual(resumed["request"]["model"], "grok-4.6")

    def test_resume_after_lifetime_and_any_terminal_status(self):
        for status in ("completed", "interrupted", "cancelled", "failed"):
            thread = chats.create_thread(1, model="offline")
            parent = store.start_turn(
                1, thread["id"], "Hello", thread["revision"], f"turn-old-{status}"
            )["run"]
            child = store.start_child_run(
                user_id=1,
                thread_id=thread["id"],
                parent_run_id=parent["id"],
                prompt="look around",
                description=f"old {status}",
            )
            store.finish_run(child["id"], {"content": "done", "tools": []}, status)
            old = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
            db.execute(
                "UPDATE chat_runs SET created_at=?, updated_at=? WHERE id=?",
                (old, old, child["id"]),
            )
            resumed = store.start_child_run(
                user_id=1,
                thread_id=thread["id"],
                parent_run_id=parent["id"],
                prompt="continue",
                description=f"resume {status}",
                resume_from_id=child["id"],
            )
            self.assertEqual(resumed["id"], child["id"])
            self.assertEqual(resumed["status"], "queued")
            self.assertEqual(
                resumed["request"]["agent"]["remaining_seconds"],
                store.DEFAULT_REMAINING_SECONDS,
            )

    def test_recovery_requeue_grants_fresh_budget_regardless_of_age(self):
        thread, parent = self._parent()
        child = store.start_child_run(
            user_id=1,
            thread_id=thread["id"],
            parent_run_id=parent["id"],
            prompt="look around",
            description="explore",
        )
        store.finish_run(child["id"], {"content": "mid", "tools": []}, "interrupted")
        old = (datetime.now(timezone.utc) - timedelta(days=9)).isoformat()
        db.execute(
            "UPDATE chat_runs SET created_at=?, updated_at=?, status='running' WHERE id=?",
            (old, old, child["id"]),
        )
        row = db.fetchone("SELECT * FROM chat_runs WHERE id=?", (child["id"],))
        messages = json.loads(row["provider_messages"] or "[]") or [{"role": "user", "content": "look around"}]
        store._requeue_for_resume(row, messages)
        saved = store.get_run(1, child["id"])
        self.assertEqual(saved["status"], "queued")
        self.assertEqual(saved["request"]["agent"]["remaining_seconds"], store.DEFAULT_REMAINING_SECONDS)

    def _finished_explore_child(self):
        thread, parent = self._parent()
        child = store.start_child_run(
            user_id=1,
            thread_id=thread["id"],
            parent_run_id=parent["id"],
            prompt="audit the module",
            description="audit",
            subagent_type="explore",
        )
        store.finish_run(child["id"], {"content": "found issues", "tools": []}, "completed")
        return thread, parent, child

    def test_resume_keeps_type_when_omitted(self):
        thread, parent, child = self._finished_explore_child()
        resumed = store.start_child_run(
            user_id=1,
            thread_id=thread["id"],
            parent_run_id=parent["id"],
            prompt="dig deeper",
            description="audit",
            resume_from_id=child["id"],
        )
        agent = resumed["request"]["agent"]
        self.assertEqual(agent["subagent_type"], "explore")
        self.assertNotIn("write_file", agent["loaded_tools"])
        self.assertNotIn("search_replace", agent["loaded_tools"])

    def test_resume_can_retype_explore_to_general_purpose(self):
        thread, parent, child = self._finished_explore_child()
        resumed = store.start_child_run(
            user_id=1,
            thread_id=thread["id"],
            parent_run_id=parent["id"],
            prompt="fix the issues you found",
            description="audit",
            subagent_type="general-purpose",
            resume_from_id=child["id"],
        )
        self.assertEqual(resumed["id"], child["id"])
        agent = resumed["request"]["agent"]
        self.assertEqual(agent["subagent_type"], "general-purpose")
        self.assertIn("write_file", agent["loaded_tools"])
        self.assertIn("search_replace", agent["loaded_tools"])
        seed = resumed["request"]["messages"]
        self.assertEqual(seed[-1]["content"], "fix the issues you found")

    def test_resolve_child_run_description_must_be_exact_and_unique(self):
        thread, parent = self._parent()
        a = store.start_child_run(
            user_id=1, thread_id=thread["id"], parent_run_id=parent["id"],
            prompt="a", description="explore api",
        )
        store.start_child_run(
            user_id=1, thread_id=thread["id"], parent_run_id=parent["id"],
            prompt="b", description="explore db",
        )
        self.assertIsNone(store.resolve_child_run(1, "explore", thread_id=thread["id"]))
        hit = store.resolve_child_run(1, "explore api", thread_id=thread["id"])
        self.assertEqual(hit["id"], a["id"])
        self.assertEqual(store.resolve_child_run(1, a["id"][:8], thread_id=thread["id"])["id"], a["id"])

    def test_continue_child_with_followups_requeues_same_id(self):
        thread, parent = self._parent()
        child = store.start_child_run(
            user_id=1, thread_id=thread["id"], parent_run_id=parent["id"],
            prompt="inspect", description="inspect",
        )
        store.save_turn_progress(child["id"], [
            {"role": "user", "content": "inspect"},
            {"role": "assistant", "content": "looked around"},
        ])
        self.assertTrue(store.queue_followup(child["id"], "also tests"))
        store.finish_run(child["id"], {"content": "looked around", "tools": []}, "completed")
        leftover = store.take_followups(child["id"])
        self.assertEqual(leftover, ["also tests"])
        self.assertTrue(store.continue_child_with_followups(child["id"], leftover))
        row = store.get_run(1, child["id"])
        self.assertEqual(row["status"], "queued")
        self.assertTrue(row["request"]["agent"]["followup_continuation"])
        self.assertTrue(row["request"]["agent"]["background"])
        self.assertEqual(row["message"].get("content") or "", "")
        msgs = row["request"]["messages"]
        self.assertEqual(msgs[-1]["role"], "user")
        self.assertIn("also tests", msgs[-1]["content"])
        self.assertIn("looked around", json.dumps(msgs))

    def test_continue_child_with_followups_skips_cancelled(self):
        thread, parent = self._parent()
        child = store.start_child_run(
            user_id=1, thread_id=thread["id"], parent_run_id=parent["id"],
            prompt="inspect", description="inspect",
        )
        store.save_turn_progress(child["id"], [
            {"role": "user", "content": "inspect"},
            {"role": "assistant", "content": "done"},
        ])
        store.finish_run(child["id"], {"content": "done", "tools": []}, "cancelled")
        self.assertFalse(store.continue_child_with_followups(child["id"], ["keep going"]))
        self.assertEqual(store.get_run(1, child["id"])["status"], "cancelled")
