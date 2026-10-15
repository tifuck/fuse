from __future__ import annotations

import os
import asyncio
import json
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
from tests.agent_caps_base import AgentCapsBase


class AgentCapsTest(AgentCapsBase):
    def test_old_receipt_cannot_ack_recompleted_generation(self):
        from app import agents
        thread, parent = self._parent()
        child = store.start_child_run(user_id=1, thread_id=thread["id"], parent_run_id=parent["id"], prompt="first", description="worker", background=True)
        store.finish_run(child["id"], {"content": "same report"}, "completed")
        old = agents._envelope(store.get_run(1, child["id"]))
        store.queue_child_result(parent["id"], old, child_id=child["id"])
        store.mark_run_wake(parent["id"], [child["id"]], receipts={child["id"]: old})
        resumed = store.start_child_run(user_id=1, thread_id=thread["id"], parent_run_id=parent["id"], prompt="again", description="worker", background=True, resume_from_id=child["id"])
        store.finish_run(child["id"], {"content": "same report"}, "completed")
        self.assertTrue(resumed["request"]["agent"]["delivery_generation"])
        store.save_turn_progress(parent["id"], [{"role": "user", "content": old}])
        store.mark_wake_sent([child["id"]], pending_guard_run=parent["id"])
        store.settle_wake_children(parent["id"], [child["id"]])
        self.assertFalse(store.get_run(1, child["id"])["request"]["agent"]["wake_sent"])
        self.assertFalse(store.queue_child_result(parent["id"], old, child_id=child["id"]))
        fresh = agents._envelope(store.get_run(1, child["id"]))
        self.assertTrue(store.queue_child_result(parent["id"], fresh, child_id=child["id"]))
        store.save_turn_progress(parent["id"], [{"role": "user", "content": old}, {"role": "user", "content": fresh}])
        self.assertTrue(store.get_run(1, child["id"])["request"]["agent"]["wake_sent"])

    def test_agent_history_limit_never_hides_active_work(self):
        thread, parent = self._parent()
        live = store.start_child_run(user_id=1, thread_id=thread["id"], parent_run_id=parent["id"], prompt="live", description="live")
        for i in range(3):
            child = store.start_child_run(user_id=1, thread_id=thread["id"], parent_run_id=parent["id"], prompt=str(i), description=str(i))
            store.finish_run(child["id"], {"content": "done"}, "completed")
        rows = store.list_thread_agents(1, parent["thread_id"], limit=1)
        self.assertEqual(len(rows), 2)
        self.assertIn(live["id"], [row["id"] for row in rows])

    def test_join_report_acknowledged_only_with_durable_parent_transcript(self):
        from app import agents

        thread, parent = self._parent()
        child = store.start_child_run(user_id=1, thread_id=thread["id"], parent_run_id=parent["id"],
                                      prompt="inspect", description="worker")
        store.finish_run(child["id"], {"content": "Final report"}, "completed")
        text, trace = asyncio.run(agents.get_output(user_id=1, arguments=json.dumps({"id": child["id"]}),
                                                    thread_id=thread["id"], parent_run_id=parent["id"]))
        self.assertEqual(trace["status"], "done")
        self.assertFalse(store.get_run(1, child["id"])["request"]["agent"].get("wake_sent"))
        self.assertEqual(store.take_child_results(parent["id"]), [text])
        # A second retrieval neither acknowledges nor duplicates the receipt.
        asyncio.run(agents.get_output(user_id=1, arguments=json.dumps({"id": child["id"]}),
                                     thread_id=thread["id"], parent_run_id=parent["id"]))
        self.assertEqual(store.take_child_results(parent["id"]), [text])
        store.save_turn_progress(parent["id"], [{"role": "tool", "tool_call_id": "join", "content": text}])
        self.assertTrue(store.get_run(1, child["id"])["request"]["agent"]["wake_sent"])
        self.assertEqual(store.take_child_results(parent["id"]), [])

    def test_foreground_report_survives_parent_interruption_before_persistence(self):
        from app import agents, chat_runs

        thread, parent = self._parent()
        async def finish(_request, _user, child):
            store.finish_run(child["id"], {"content": "Final report"}, "completed")
            return None
        with patch.object(chat_runs, "start", side_effect=finish):
            text, trace = asyncio.run(agents.spawn(user={"id": 1}, request=None, parent_run_id=parent["id"],
                                                  thread_id=thread["id"], arguments='{"prompt":"inspect","description":"Inspect"}'))
        self.assertIn("Final report", text)
        store.abort_active(parent["id"], "interrupted")
        candidates = store.wake_candidates(1, parent["thread_id"])
        self.assertEqual([item["id"] for item in candidates], [trace["child_id"]])
        self.assertEqual(candidates[0]["summary"], "Final report")

    def test_agent_activity_agrees_between_snapshots_and_compact_events(self):
        from app.chat_events import _downgrade_progress

        thread, parent = self._parent()
        child = store.start_child_run(user_id=1, thread_id=thread["id"], parent_run_id=parent["id"],
                                      prompt="inspect", description="worker")
        message = {"content": "Now writing", "tools": [
            {"id": "spawn", "name": "spawn_subagent", "child_id": "grandchild", "status": "running", "background": True}],
            "segments": [{"type": "tool", "id": "spawn"}, {"type": "content", "text": "Now writing"}]}
        store.checkpoint(child["id"], message, events=[{"fuse": {"event": "activity", "phase": "model"}}])
        row = store.list_thread_agents(1, parent["thread_id"])[0]
        self.assertEqual(row["current"], "writing")
        event = next(e for e in reversed(store.events_after(1, 0)) if e["type"] == "agent.progress")
        self.assertEqual(_downgrade_progress(event, None)["payload"]["current"], "writing")
        self.assertEqual(store.agent_current_activity(message, status="completed"), "")
        # A tool call being written is named at once, not "thinking".
        thought = {"segments": [{"type": "reasoning", "text": "plan"}]}
        self.assertEqual(store.agent_current_activity(thought, {"phase": "calling", "tool": "grep"}), "grep")
        self.assertEqual(store.agent_current_activity(thought, {"phase": "calling"}), "calling")
        self.assertEqual(store.agent_current_activity(thought, {"phase": "model"}), "thinking")
        # A marked think that streams no text is thinking, not waiting.
        ran = {"segments": [{"type": "tool", "id": "t"}]}
        self.assertEqual(store.agent_current_activity(ran, {"phase": "model"}), "waiting")
        self.assertEqual(store.agent_current_activity(ran, {"phase": "model", "thinking": True}), "thinking")

    def test_empty_completed_leg_does_not_reuse_old_report_or_tool_preamble(self):
        old = {"role": "assistant", "content": "Report from previous leg"}
        for tail in (
            [{"role": "user", "content": "Continue with a new task"}],
            [{"role": "assistant", "content": "I will inspect", "tool_calls": [{"id": "call"}]}],
            [{"role": "assistant", "content": "I will inspect", "tool_calls": [{"id": "call"}]},
             {"role": "tool", "content": "tool result"}],
            [{"role": "assistant", "content": ""}],
        ):
            self.assertEqual(store.child_writeup({"status": "completed", "message": {},
                                                  "request": {"messages": [old, *tail]}}), "")
        self.assertEqual(store.last_assistant_text([old, {"role": "assistant", "content": [
            {"type": "text", "text": "Final report"}]}]), "Final report")

    def test_root_does_not_steal_live_nested_parents_report(self):
        thread, root = self._parent()
        parent = store.start_child_run(user_id=1, thread_id=thread["id"], parent_run_id=root["id"],
                                       prompt="coordinate", description="parent", allow_nested_spawn=True, background=True)
        child = store.start_child_run(user_id=1, thread_id=thread["id"], parent_run_id=parent["id"],
                                      prompt="inspect", description="child", depth=2, background=True, allow_nested_spawn=True)
        store.finish_run(child["id"], {"content": "Nested report"}, "completed")
        envelope = store.render_task_output(child_id=child["id"], state="completed", text="Nested report")
        store.queue_child_result(parent["id"], envelope, child_id=child["id"])
        self.assertEqual(store.wake_candidates(1, root["thread_id"]), [])
        self.assertEqual(store.take_child_results(parent["id"]), [envelope])
        store.abort_active(parent["id"], "interrupted")
        self.assertIn(child["id"], [row["id"] for row in store.wake_candidates(1, root["thread_id"])])

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
        for message in messages:
            trace = message["tools"][0]
            self.assertEqual(trace["status"], "error" if trace["name"] == "spawn_subagent" else "running")
            if trace["name"] == "spawn_subagent":
                self.assertEqual(trace["error"], "Child execution failed")

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

    def test_scratch_label_names_the_operation(self):
        from app.chat_tools import scratch_label

        self.assertEqual(scratch_label({}), "List slots")
        self.assertEqual(scratch_label({"name": "brief"}), "Read brief")
        self.assertEqual(scratch_label({"ops": [{"op": "put", "name": "brief"}]}), "Write brief")
        self.assertEqual(scratch_label({"ops": json.dumps({"op": "delete", "name": "x"})}), "Clear x")
        ops = [{"op": op, "name": f"s{i}"} for i, op in enumerate(("append", "edit", "put", "clear"))]
        self.assertEqual(scratch_label({"ops": ops}), "Append to s0, Edit s1, +2 more")

    def test_scratch_slot_fits_one_read_and_ops_are_atomic(self):
        from app.chat_runtime import TOOL_RESULT_CHARS, _tool_result_clip
        from app.chat_tools import execute_tool

        _, parent = self._parent()
        session = {"run_id": parent["id"]}

        def call(args):
            return asyncio.run(execute_tool(
                "scratch", json.dumps(args), user_id=1, allowed_urls=set(), coding=True, session=session,
            ))

        # A full slot reads back whole: the cap sits under the tool-result clip.
        self.assertLess(store.SCRATCH_VALUE_MAX, TOOL_RESULT_CHARS)
        full = "x" * store.SCRATCH_VALUE_MAX
        _, rec = call({"ops": [{"op": "put", "name": "big", "text": full}]})
        self.assertEqual(rec["status"], "done")
        text, _ = call({"name": "big"})
        self.assertEqual(_tool_result_clip("scratch", text), full)
        _, rec = call({"ops": [{"op": "append", "name": "big", "text": "y"}]})
        self.assertEqual(rec["status"], "error")
        # A failing op rolls back earlier ops in the same call.
        _, rec = call({"ops": [{"op": "put", "name": "a", "text": "1"}, {"op": "bogus", "name": "b"}]})
        self.assertEqual(rec["status"], "error")
        self.assertIsNone(store.scratch_read(parent["id"], "a"))
        # ops sent as a JSON string or a single object still apply.
        _, rec = call({"ops": json.dumps([{"op": "put", "name": "s", "text": "from string"}])})
        self.assertEqual(rec["status"], "done")
        _, rec = call({"ops": {"op": "append", "name": "s", "text": "!"}})
        self.assertEqual(rec["status"], "done")
        self.assertEqual(store.scratch_read(parent["id"], "s"), "from string!")
        msg, rec = call({"ops": 5})
        self.assertEqual(rec["kind"], "invalid_arguments")
        self.assertIn("array", msg)

    def test_scratch_edit_and_delete_ops(self):
        _, parent = self._parent()
        rid = parent["id"]
        store.scratch_apply(rid, [{"op": "put", "name": "plan", "text": "step one; step two"}])
        store.scratch_apply(rid, [{"op": "edit", "name": "plan", "old": "two", "new": "2"}])
        self.assertEqual(store.scratch_read(rid, "plan"), "step one; step 2")
        with self.assertRaisesRegex(store.ScratchError, "exactly once"):
            store.scratch_apply(rid, [{"op": "edit", "name": "plan", "old": "step", "new": "s"}])
        with self.assertRaisesRegex(store.ScratchError, "not found"):
            store.scratch_apply(rid, [{"op": "edit", "name": "nope", "old": "a", "new": "b"}])
        store.scratch_apply(rid, [{"op": "put", "name": "plan", "text": "overwritten"}])
        self.assertEqual(store.scratch_read(rid, "plan"), "overwritten")
        store.scratch_apply(rid, [{"op": "delete", "name": "plan"}])
        self.assertIsNone(store.scratch_read(rid, "plan"))

    def test_scratch_carries_into_next_turn_and_wake(self):
        from app import hosts

        coding = patch.object(hosts, "settings_for", return_value={"agent_coding": True})
        coding.start()
        self.addCleanup(coding.stop)
        thread, first = self._parent()
        self.assertTrue(first["request"]["agent"]["coding"])
        store.scratch_apply(first["id"], [{"op": "put", "name": "w2", "text": "next wave"}])
        store.finish_run(first["id"], {"content": "done"}, "completed")
        rev = chats.get_thread(1, thread["id"])["revision"]
        second = store.start_turn(1, thread["id"], "wake", rev, "wake-1", auto=True)["run"]
        self.assertEqual(store.scratch_read(second["id"], "w2"), "next wave")
        # Edits and deletes in the new turn are what the following turn sees.
        store.scratch_apply(second["id"], [{"op": "clear", "name": "w2"}, {"op": "put", "name": "plan", "text": "p"}])
        store.finish_run(second["id"], {"content": "done"}, "completed")
        rev = chats.get_thread(1, thread["id"])["revision"]
        third = store.start_turn(1, thread["id"], "again", rev, "turn-3")["run"]
        self.assertEqual(store.scratch_list(third["id"]), {"plan": "p"})

    def test_scratch_survives_subagent_resume(self):
        thread, parent = self._parent()
        child = store.start_child_run(user_id=1, thread_id=thread["id"], parent_run_id=parent["id"],
                                      prompt="first", description="worker", background=True)
        store.scratch_apply(child["id"], [{"op": "put", "name": "notes", "text": "child notes"}])
        # The child's pad is its own.
        self.assertIsNone(store.scratch_read(parent["id"], "notes"))
        store.save_turn_progress(child["id"], [{"role": "user", "content": "first"}, {"role": "assistant", "content": "ok"}])
        store.finish_run(child["id"], {"content": "report"}, "completed")
        store.start_child_run(user_id=1, thread_id=thread["id"], parent_run_id=parent["id"], prompt="again",
                              description="worker", background=True, resume_from_id=child["id"])
        self.assertEqual(store.scratch_read(child["id"], "notes"), "child notes")

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
        self.assertEqual(messages[0]["tools"][0]["status"], "interrupted")

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
        # A background grandchild outlives its finished parent: still found.
        db.execute("UPDATE chat_runs SET status='completed' WHERE id=?", (a["id"],))
        self.assertCountEqual(store.descendant_ids(parent["id"]), [b["id"], grand["id"]])

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

    def test_child_runs_at_the_parents_effort_on_the_same_model(self):
        # Without it a Claude subagent fell back to low while the user had
        # picked xhigh. Another model keeps its default: the level may not exist.
        thread = chats.create_thread(1, model="offline")
        parent = store.start_turn(1, thread["id"], "Hello", thread["revision"], "turn", reasoning_effort="xhigh")["run"]
        child = store.start_child_run(user_id=1, thread_id=thread["id"], parent_run_id=parent["id"],
                                      prompt="look", description="explore")
        self.assertEqual(child["request"]["reasoning_effort"], "xhigh")
        other = store.start_child_run(user_id=1, thread_id=thread["id"], parent_run_id=parent["id"],
                                      prompt="look", description="other", model="other-model")
        self.assertNotIn("reasoning_effort", other["request"])
        store.finish_run(child["id"], {"content": "done"}, "completed")
        resumed = store.start_child_run(user_id=1, thread_id=thread["id"], parent_run_id=parent["id"],
                                        prompt="again", description="explore", resume_from_id=child["id"])
        self.assertEqual(resumed["request"]["reasoning_effort"], "xhigh")

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

    def test_followup_rejected_on_stopping_child(self):
        thread, parent = self._parent()
        child = self._live_child(thread, parent)
        db.execute("UPDATE chat_runs SET cancel_requested=1 WHERE id=?", (child["id"],))
        self.assertFalse(store.queue_followup(child["id"], "too late, it is stopping"))

    def test_abandoned_child_reports_unconsumed_followups(self):
        thread, parent = self._parent()
        child = self._live_child(thread, parent)
        self.assertTrue(store.queue_followup(child["id"], "check the lexer too"))
        self.assertTrue(store.abort_active(child["id"], "interrupted"))
        view = store.get_run(1, child["id"])
        agent = view["request"]["agent"]
        self.assertEqual(agent["pending_followups"], [])
        self.assertTrue(any("check the lexer too" in t for t in agent["unconsumed_followups"]))
        self.assertTrue(agent["background"])
        self.assertFalse(agent["wake_sent"])
        self.assertIn("check the lexer too", store.child_writeup(view))
        self.assertTrue(any(c["id"] == child["id"] for c in store.wake_candidates(1, child["thread_id"])))

    def test_child_display_renders_followups_as_parent_messages(self):
        envelope = (
            "[Fuse] Follow-up from the parent — not written by the end user. "
            "Stay in the assigned task and host/cwd unless this follow-up changes them:\n\n"
            "(followup:111222333444) earlier steer"
        )
        view = {
            "status": "running",
            "message_id": "m",
            "message": {"content": "", "tools": []},
            "request": {"messages": [], "model": "t",
                        "agent": {"pending_followups": [{"id": "abc123def456", "text": "also check Y"}]}},
            "provider_messages": [
                {"role": "user", "content": "initial task"},
                {"role": "user", "content": envelope},
                {"role": "assistant", "content": "ok"},
            ],
        }
        rows = store.child_display_messages(view)
        followups = [r for r in rows if r.get("fromParent")]
        self.assertEqual(len(followups), 2)
        delivered, pending = followups
        self.assertEqual(delivered["role"], "user")
        self.assertFalse(delivered.get("auto"))
        self.assertFalse(delivered.get("pending"))
        self.assertEqual(delivered["content"], "earlier steer")
        self.assertTrue(pending.get("pending"))
        self.assertEqual(pending["content"], "also check Y")
        self.assertEqual(rows[-1], pending)  # queued rows land after history

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
        first = store.take_followups(child["id"])
        self.assertEqual(len(first), 1)
        self.assertRegex(first[0], r"^\(followup:[0-9a-f]{12}\) also check tests$")
        self.assertEqual(store.take_followups(child["id"]), first)
        # The text alone is not a receipt; the id marker is.
        store.save_turn_progress(child["id"], [{"role": "user", "content": "also check tests"}])
        self.assertEqual(store.take_followups(child["id"]), first)
        store.save_turn_progress(child["id"], [
            {"role": "user", "content": "look"},
            {"role": "user", "content": first[0]},
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

    def test_wake_turn_is_flagged_in_the_same_transaction_as_its_run(self):
        thread, parent = self._parent()
        child = store.start_child_run(user_id=1, thread_id=thread["id"], parent_run_id=parent["id"],
                                      prompt="bg", description="bg", background=True)
        store.finish_run(child["id"], {"content": "report", "tools": []}, "completed")
        store.finish_run(parent["id"], {"content": "ok", "tools": []}, "completed")
        rev = chats.get_thread(1, thread["id"])["revision"]
        with patch.object(store, "mark_run_wake", side_effect=AssertionError("separate write")):
            wake = store.start_turn(1, thread["id"], "AUTOMATED EVENT - background report", rev, "wake-same-tx",
                                    auto=True, wake=([child["id"]], {child["id"]: "env"}))["run"]
        agent = store.get_run(1, wake["id"])["request"]["agent"]
        self.assertTrue(agent["wake"])
        self.assertEqual(agent["wake_ids"], [child["id"]])
        self.assertEqual(agent["wake_receipts"], {child["id"]: "env"})

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
        env2 = store.render_task_output(child_id=child["id"], state="completed", text="report two", description="bg",
                                        generation=resumed["request"]["agent"]["delivery_generation"])
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

    def test_user_stopped_subagent_never_wakes_an_idle_thread(self):
        from app import chat_runs as runs
        from app import command_jobs

        thread, parent = self._parent()
        child = store.start_child_run(user_id=1, thread_id=thread["id"], parent_run_id=parent["id"],
                                      prompt="bg", description="bg", background=True)
        store.finish_run(parent["id"], {"content": "ok", "tools": []}, "completed")
        # Agents pane Stop on the subagent alone, after the main turn ended.
        with patch.object(runs, "cancel"):
            asyncio.run(runs.cancel_wait(1, child["id"], tree=False, force=True, by_user=True))
        self.assertEqual(store.get_run(1, child["id"])["status"], "cancelled")
        with patch.object(runs, "start", new=AsyncMock(return_value=None)), \
                patch.object(command_jobs, "pending_envelopes", return_value=[]):
            asyncio.run(runs._wake_parent(1, parent["thread_id"]))
        roots = db.fetchall("SELECT id FROM chat_runs WHERE thread_id=? AND parent_run_id IS NULL", (parent["thread_id"],))
        self.assertEqual(len(roots), 1)
        with patch.object(runs, "schedule_wake") as woken:
            runs.sweep_wakes()
        woken.assert_not_called()
        agents = store.list_thread_agents(1, parent["thread_id"])
        self.assertEqual(agents[0]["wake_state"], "held")
        # The report still rides with the user's next message.
        nxt = store.start_turn(1, thread["id"], "next", chats.get_thread(1, thread["id"])["revision"], "turn-next")["run"]
        self.assertTrue(store.queue_stranded_reports(1, parent["thread_id"], nxt["id"]))

    def test_kill_subagent_by_the_model_still_reports_to_an_idle_thread(self):
        from app import chat_runs as runs
        from app import command_jobs

        thread, parent = self._parent()
        child = store.start_child_run(user_id=1, thread_id=thread["id"], parent_run_id=parent["id"],
                                      prompt="bg", description="bg", background=True)
        store.finish_run(parent["id"], {"content": "ok", "tools": []}, "completed")
        store.request_cancel_self(1, child["id"])
        store.abort_active(child["id"], "cancelled")
        with patch.object(runs, "start", new=AsyncMock(return_value=None)), \
                patch.object(command_jobs, "pending_envelopes", return_value=[]):
            asyncio.run(runs._wake_parent(1, parent["thread_id"]))
        roots = db.fetchall("SELECT id FROM chat_runs WHERE thread_id=? AND parent_run_id IS NULL", (parent["thread_id"],))
        self.assertEqual(len(roots), 2)

    def test_main_stop_also_stops_earlier_turns_live_subagents(self):
        from app import chat_runs as runs

        thread, first = self._parent()
        child = store.start_child_run(user_id=1, thread_id=thread["id"], parent_run_id=first["id"],
                                      prompt="bg", description="bg", background=True)
        other = store.start_child_run(user_id=1, thread_id=thread["id"], parent_run_id=first["id"],
                                      prompt="bg2", description="bg2", background=True)
        store.finish_run(first["id"], {"content": "ok", "tools": []}, "completed")
        second = store.start_turn(1, thread["id"], "next", chats.get_thread(1, thread["id"])["revision"], "turn-2")["run"]
        # A tree cancel without the user (internal callers) leaves other turns alone.
        with patch.object(runs, "cancel"):
            asyncio.run(runs.cancel_wait(1, second["id"], force=True))
        self.assertIn(store.get_run(1, child["id"])["status"], ("queued", "running"))
        third = store.start_turn(1, thread["id"], "again", chats.get_thread(1, thread["id"])["revision"], "turn-3")["run"]
        cancelled = []
        with patch.object(runs, "cancel", side_effect=cancelled.append):
            asyncio.run(runs.cancel_wait(1, third["id"], force=True, by_user=True))
        self.assertTrue({child["id"], other["id"]} <= set(cancelled))
        for ident in (child["id"], other["id"], third["id"]):
            self.assertEqual(store.get_run(1, ident)["status"], "cancelled")

    def test_stop_gate_reads_the_latest_root_within_one_second(self):
        """Two roots started in the same second (a turn failing at once, then
        the queue or a wake starting the next): created_at cannot order them
        and the random ids must not decide which one was stopped."""
        from app import chat_runs as runs
        from app import command_jobs

        thread, first = self._parent()
        child = store.start_child_run(user_id=1, thread_id=thread["id"], parent_run_id=first["id"],
                                      prompt="bg", description="bg", background=True)
        store.finish_run(first["id"], {"content": "", "error": "boom", "tools": []}, "failed")
        store.finish_run(child["id"], {"content": "report", "tools": []}, "completed")
        current = chats.get_thread(1, thread["id"])
        second = store.start_turn(1, thread["id"], "Again", current["revision"], "turn-again")["run"]
        store.finish_run(second["id"], {"content": "", "tools": []}, "cancelled")
        # Same second, and the stopped run's id sorts first: the old
        # (created_at DESC, id DESC) order picked the failed run.
        db.execute("UPDATE chat_runs SET id='0-stopped' WHERE id=?", (second["id"],))
        db.execute("UPDATE chat_runs SET id='z-failed', root_run_id='z-failed' WHERE id=?", (first["id"],))
        db.execute("UPDATE chat_runs SET parent_run_id='z-failed', root_run_id='z-failed' WHERE id=?", (child["id"],))
        db.execute("UPDATE chat_runs SET created_at='2026-10-08T10:00:00Z' WHERE parent_run_id IS NULL AND thread_id=?",
                   (first["thread_id"],))
        self.assertEqual(store.thread_root_run(1, first["thread_id"])["id"], "0-stopped")
        with patch.object(runs, "start", new=AsyncMock(return_value=None)), \
                patch.object(command_jobs, "pending_envelopes", return_value=[]):
            asyncio.run(runs._wake_parent(1, first["thread_id"]))
        roots = db.fetchall("SELECT id FROM chat_runs WHERE thread_id=? AND parent_run_id IS NULL", (first["thread_id"],))
        self.assertEqual(len(roots), 2, "no wake turn after the user's Stop")

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
        store.mark_run_wake(wake["id"], [child_a["id"], child_b["id"]], receipts={child_a["id"]: env_a})
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


class ListAgentsJsonFitTest(unittest.TestCase):
    """The list_subagents tool result must stay parseable JSON at any size."""

    def test_fit_list_json_never_slices_mid_token(self):
        from app import agents

        payload = {
            "live": [
                {"id": f"live-{i}", "description": "d" * 200, "status": "running", "summary": "s" * 300}
                for i in range(8)
            ],
            "finished": [
                {"id": f"done-{i}", "description": "e" * 200, "status": "completed", "summary": "t" * 300}
                for i in range(40)
            ],
            "omitted_finished": 0,
        }
        text = agents._fit_list_json(payload)
        self.assertLessEqual(len(text), 6000)
        parsed = json.loads(text)
        self.assertEqual([item["id"] for item in parsed["live"]], [f"live-{i}" for i in range(8)])
        self.assertGreaterEqual(payload["omitted_finished"], 8)

    def test_fit_list_json_bare_fallback(self):
        from app import agents

        huge = [
            {"id": f"live-{i}", "description": "x" * 2000, "status": "running", "summary": "y" * 2000}
            for i in range(8)
        ]
        text = agents._fit_list_json({"live": huge, "finished": [], "omitted_finished": 0})
        parsed = json.loads(text)
        self.assertIn("note", parsed)
        self.assertEqual(parsed["live"], [f"live-{i}" for i in range(8)])


class CatalogSearchCapTest(unittest.TestCase):
    """search() must cap loaded extras, not raw name slots."""

    def test_always_on_names_do_not_consume_extra_slots(self):
        from app import agent_catalog

        real = list(agent_catalog.WORKSPACE_NAMES) + ["create_artifact", "update_artifact", "propose_skill"]
        names = list(agent_catalog.ALWAYS_ON_CODING) + list(agent_catalog.SPAWN_NAMES) + real
        found = agent_catalog.search(names=names)
        loaded = [s["function"]["name"] for s in found]
        # The old code sliced wanted[:MAX_EXTRA] before dropping always-on
        # names, so the 9 placeholders starved all but 4 of the real tools.
        self.assertEqual(loaded, real)
        self.assertLessEqual(len(found), agent_catalog.MAX_EXTRA)
