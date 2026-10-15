from __future__ import annotations

import os
import asyncio
import json
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
_TMP = tempfile.mkdtemp(prefix="fuse-agent-")
os.environ.setdefault("DATABASE_PATH", str(Path(_TMP) / "agent.db"))
os.environ.setdefault("APP_SECRET", "x" * 32)

from app import chat_store as store, chats, db
from tests.agent_caps_base import AgentCapsBase


class AgentChildrenTest(AgentCapsBase):
    """Child display, follow-ups, writeups, resume and hand-off (split from test_agent_caps)."""

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

    def test_child_writeup_never_fabricates_report_from_tools(self):
        self.assertEqual(store.child_writeup({"status": "completed", "message": {}, "request": {}}), "")
        self.assertEqual(
            store.child_writeup(
                {
                    "status": "completed",
                    "message": {
                        "content": "",
                        "tools": [{"name": "read_file", "preview": "/tmp/x.py", "status": "done"}],
                    },
                    "request": {},
                }
            ), "",
        )

    def test_child_writeup_suppresses_real_partial_on_failure(self):
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
        self.assertNotIn("patched foo.py", text)

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
        self.assertEqual(rows[0]["current"], "waiting")  # no reasoning has arrived yet

    def test_spawn_start_failure_fails_queued_child(self):
        from app import agents

        thread, parent = self._parent()
        with patch.object(agents.chat_runs, "start", new=AsyncMock(side_effect=RuntimeError("worker boom"))):
            text, trace = asyncio.run(agents.spawn(
                user={"id": 1},
                request=None,
                parent_run_id=parent["id"],
                thread_id=thread["id"],
                arguments=json.dumps({"prompt": "Inspect", "description": "Inspect"}),
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
        # A delivered parent follow-up is a message from the main agent, not
        # an automated-event chip: bubble text only, marker and preamble gone.
        followup = next(item for item in hist if item.get("fromParent"))
        self.assertFalse(followup.get("auto"))
        self.assertEqual(followup["content"], "also tests")
        self.assertNotIn("[Fuse]", followup["content"])
        self.assertNotEqual((hist[-1].get("content") or "").strip(), "tests look fine")
        meter = store.child_context_meter(view)
        self.assertIsNotNone(meter)
        self.assertGreater(meter["used"], 0)
        self.assertIn("tools", meter.get("parts") or {})
        self.assertGreater(meter["limit"], 0)
        rows = store.list_thread_agents(1, parent["thread_id"])
        self.assertTrue(rows)

    def test_child_display_places_followup_where_it_was_delivered(self):
        from app import chat_views

        mark = {"content": 9, "reasoning": 0, "tools": 1, "permissions": 0, "segments": 2, "tail": 9}
        view = {
            "id": "r",
            "status": "running",
            "message_id": "m",
            "message": {
                "content": "read it. now tests",
                "reasoning": "",
                "tools": [{"id": "c1", "name": "read_file", "status": "done"},
                          {"id": "c2", "name": "grep", "status": "running"}],
                "segments": [{"type": "tool", "id": "c1"}, {"type": "content", "text": "read it. "},
                             {"type": "tool", "id": "c2"}, {"type": "content", "text": "now tests"}],
            },
            "request": {
                "model": "t",
                "display_seed": [{"role": "user", "content": "inspect"}],
                "agent": {"followup_marks": [{"ids": ["abc123"], "end": mark}]},
            },
            "provider_messages": [
                {"role": "user", "content": "inspect"},
                {"role": "assistant", "content": "", "tool_calls": [
                    {"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": "{}"}}]},
                {"role": "tool", "tool_call_id": "c1", "content": "x"},
                {"role": "user", "content": "[Fuse] Follow-up from the parent — not written by the end user.\n\n(followup:abc123) also tests"},
            ],
        }
        hist, live_start = chat_views._child_display(view)
        # The leg's output before delivery sits above the follow-up, not below it.
        self.assertEqual([item["role"] for item in hist], ["user", "assistant", "user"])
        self.assertEqual(hist[1]["content"], "read it. ")
        self.assertEqual([tool["id"] for tool in hist[1]["tools"]], ["c1"])
        self.assertTrue(hist[2]["fromParent"])
        self.assertEqual(hist[2]["content"], "also tests")
        self.assertEqual(live_start, mark)
        tail = chat_views._display_slice(view["message"], live_start)
        self.assertEqual(tail["content"], "now tests")
        self.assertEqual([tool["id"] for tool in tail["tools"]], ["c2"])
        self.assertEqual(chat_views._utf16_mark({"content": "😀ab", "segments": []}, {"content": 2})["content"], 3)

    def test_followup_display_recovery_and_dedupe(self):
        thread, parent = self._parent()
        child = store.start_child_run(
            user_id=1,
            thread_id=thread["id"],
            parent_run_id=parent["id"],
            prompt="inspect",
            description="d",
        )
        store.save_turn_progress(child["id"], [
            {"role": "user", "content": "inspect"},
            {"role": "user", "content": "[Fuse] Follow-up from the parent — not written by the end user. "
                                        "Stay in the assigned task and host/cwd unless this follow-up changes them:\n\n"
                                        "(followup:abc123def456) also tests"},
        ])
        # Envelope durable, mark lost (crash between persist and seal): the
        # scan must re-offer it so the next boundary re-emits the event.
        self.assertEqual(store.followup_display_deliveries(child["id"]), ["abc123def456"])
        store.mark_followup_display(child["id"], ["abc123def456"], {"content": "x", "tools": []})
        self.assertEqual(store.followup_display_deliveries(child["id"]), [])
        # A duplicate seal (recovery racing the original frame) adds nothing.
        store.mark_followup_display(child["id"], ["abc123def456"], {"content": "y", "tools": []})
        view = store.get_run(1, child["id"])
        marks = ((view.get("request") or {}).get("agent") or {}).get("followup_marks") or []
        self.assertEqual(len(marks), 1)

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
        from app import context_compact as context

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
        from app import chat_runs, context_compact as context

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
        from app import context_compact as context

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

    def test_terminal_child_delivery_matrix(self):
        from app import agents
        thread, parent = self._parent()
        cases = [
            ("completed", {}),
            ("cancelled", {"stopped": True}),
            ("interrupted", {"notice": "Generation interrupted by server shutdown."}),
            ("failed", {"error": "Provider unavailable after retries"}),
            ("failed", {"error": "Unhandled tool exception"}),
        ]
        for index, (status, extra) in enumerate(cases):
            with self.subTest(status=status, extra=extra):
                child = store.start_child_run(user_id=1, thread_id=thread["id"],
                    parent_run_id=parent["id"], prompt="fixture work", description=f"case {index}", background=True)
                store.save_turn_progress(child["id"], [
                    {"role": "assistant", "content": "PRIVATE_PARTIAL_TRANSCRIPT"},
                    {"role": "assistant", "tool_calls": [{"id": "read", "type": "function", "function": {"name": "read_file", "arguments": "{}"}}]},
                    {"role": "tool", "tool_call_id": "read", "content": "PRIVATE_TOOL_OUTPUT"},
                ])
                store.finish_run(child["id"], {"content": "FINAL_REPORT" if status == "completed" else "PRIVATE_PARTIAL",
                    "tools": [{"name": "read_file", "preview": "PRIVATE_TOOL_OUTPUT"}], **extra}, status)
                row = store.get_run(1, child["id"])
                candidate = next(item for item in store.wake_candidates(1, parent["thread_id"]) if item["id"] == child["id"])
                for text in [agents._envelope(row), candidate["summary"]]:
                    if status == "completed":
                        self.assertIn("FINAL_REPORT", text)
                    else:
                        self.assertIn("No completed report", text)
                        self.assertNotIn("PRIVATE_", text)
                        self.assertNotIn("read_file", text)
                        if extra.get("error") or extra.get("notice"):
                            self.assertIn(extra.get("error") or extra["notice"], text)

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

    def test_report_is_final_answer_only_and_capped(self):
        thread, parent = self._parent()
        child = self._live_child(thread, parent)
        store.finish_run(child["id"], {
            "content": "Let me look at the parser.Now checking tests.FINAL: 2 bugs fixed.",
            "segments": [
                {"type": "content", "text": "Let me look at the parser."},
                {"type": "tool", "id": "t1"},
                {"type": "content", "text": "Now checking tests."},
                {"type": "tool", "id": "t2"},
                {"type": "content", "text": "FINAL: 2 bugs fixed."},
            ],
            "tools": [{"id": "t1", "name": "read_file", "status": "done"},
                      {"id": "t2", "name": "grep", "status": "done"}],
        }, "completed")
        self.assertEqual(store.child_writeup(store.get_run(1, child["id"])), "FINAL: 2 bugs fixed.")
        huge = "x" * (store.REPORT_MAX + 5000)
        capped = store._cap_report(huge)
        self.assertLessEqual(len(capped), store.REPORT_MAX + 200)
        self.assertIn("characters of the report omitted", capped)
        self.assertEqual(capped, store._cap_report(huge))

    def test_followups_are_acknowledged_by_id_not_text(self):
        thread, parent = self._parent()
        child = self._live_child(thread, parent)
        store.save_turn_progress(child["id"], [
            {"role": "user", "content": "Audit the parser and fix bugs"},
            {"role": "assistant", "content": "Found 2 bugs. Continue with the tests next? yes"},
        ])
        for text in ("yes", "fix bugs", "yes"):
            self.assertTrue(store.queue_followup(child["id"], text))
        seen = store.take_followups(child["id"])
        self.assertEqual(len(seen), 3)
        self.assertEqual([t.split(") ", 1)[1] for t in seen], ["yes", "fix bugs", "yes"])
        # Delivering the first two leaves only the third pending.
        store.save_turn_progress(child["id"], [{"role": "user", "content": "\n\n".join(seen[:2])}])
        self.assertEqual(store.take_followups(child["id"]), [seen[2]])

    def test_followup_continuation_carries_the_undelivered_report(self):
        thread, parent = self._parent()
        child = self._live_child(thread, parent)
        store.save_turn_progress(child["id"], [{"role": "user", "content": "Audit the parser and fix bugs"}])
        store.queue_followup(child["id"], "also add a test")
        store.finish_run(child["id"], {"content": "REPORT ONE", "tools": []}, "completed")
        leftover = store.take_followups(child["id"])
        self.assertTrue(store.continue_child_with_followups(child["id"], leftover))
        resumed = store.get_run(1, child["id"])
        self.assertEqual(resumed["request"]["agent"]["carried_report"], "REPORT ONE")
        store.finish_run(child["id"], {"content": "TEST ADDED", "tools": []}, "completed")
        report = store.child_writeup(store.get_run(1, child["id"]))
        self.assertIn("REPORT ONE", report)
        self.assertIn("TEST ADDED", report)
        self.assertLess(report.index("REPORT ONE"), report.index("TEST ADDED"))

    def test_stopped_child_reports_unconsumed_followups(self):
        thread, parent = self._parent()
        child = self._live_child(thread, parent)
        store.queue_followup(child["id"], "check the lexer too")
        store.finish_run(child["id"], {"content": "", "tools": []}, "cancelled")
        store.flag_unconsumed_followups(child["id"], store.take_followups(child["id"]))
        report = store.child_writeup(store.get_run(1, child["id"]))
        self.assertIn("stopped by cancellation", report)
        self.assertIn("check the lexer too", report)

    def test_main_agent_follows_up_a_live_child_from_an_earlier_turn(self):
        from app import agents

        thread, root1 = self._parent()
        child = self._live_child(thread, root1)
        store.finish_run(root1["id"], {"content": "launched", "tools": []}, "completed")
        rev = chats.get_thread(1, thread["id"])["revision"]
        root2 = store.start_turn(1, thread["id"], "tell the worker to check Y", rev, "turn-2")["run"]
        text, rec = asyncio.run(agents.spawn(user={"id": 1}, request=None, parent_run_id=root2["id"], thread_id=thread["id"],
                                             arguments=json.dumps({"prompt": "also check Y", "task_id": child["id"]})))
        self.assertEqual(rec["status"], "running", rec)
        self.assertTrue(any("also check Y" in t for t in store.take_followups(child["id"])))

    def test_subagent_cannot_join_kill_or_list_a_sibling(self):
        from app import agents

        thread, parent = self._parent()
        a = self._live_child(thread, parent, prompt="a", allow_nested_spawn=True)
        b = self._live_child(thread, parent, prompt="b")
        grandchild = self._live_child(thread, a, prompt="a1", allow_nested_spawn=True)
        args = json.dumps({"id": b["id"], "timeout_ms": 0})
        _, rec = asyncio.run(agents.get_output(user_id=1, arguments=args, thread_id=thread["id"],
                                               parent_run_id=a["id"], scope_run_id=a["id"]))
        self.assertEqual(rec["status"], "error")
        _, rec = asyncio.run(agents.kill(user_id=1, arguments=args, thread_id=thread["id"], scope_run_id=a["id"]))
        self.assertEqual(rec["status"], "error")
        self.assertIn(store.get_run(1, b["id"])["status"], store.ACTIVE)
        text, _ = asyncio.run(agents.list_agents(user_id=1, thread_id=parent["thread_id"], scope_run_id=a["id"]))
        listed = [item["id"] for item in json.loads(text)["live"]]
        self.assertEqual(listed, [grandchild["id"]])
        # Its own descendant stays reachable.
        _, rec = asyncio.run(agents.get_output(user_id=1, arguments=json.dumps({"id": grandchild["id"], "timeout_ms": 0}),
                                               thread_id=thread["id"], parent_run_id=a["id"], scope_run_id=a["id"]))
        self.assertEqual(rec["status"], "running")

    def test_hand_off_delivers_a_child_that_finished_in_the_race(self):
        from app import agents

        thread, parent = self._parent()
        child = store.start_child_run(user_id=1, thread_id=thread["id"], parent_run_id=parent["id"],
                                      prompt="p", description="racer")
        self.assertFalse(child["request"]["agent"].get("background"))
        store.finish_run(child["id"], {"content": "RACE REPORT", "tools": []}, "completed")
        asyncio.run(agents._hand_off(1, parent["id"], child["id"], "racer"))
        self.assertTrue(store.get_run(1, child["id"])["request"]["agent"]["background"])
        self.assertTrue(any("RACE REPORT" in t for t in store.take_child_results(parent["id"])))

    def test_child_on_other_host_does_not_inherit_parent_cwd(self):
        thread, parent = self._parent()
        req = parent["request"]
        req.setdefault("agent", {})["bound_host_id"] = 3
        req["agent"]["bound_cwd"] = "/srv/app"
        db.execute("UPDATE chat_runs SET request=? WHERE id=?", (store.dumps(req), parent["id"]))
        other = store.start_child_run(user_id=1, thread_id=thread["id"], parent_run_id=parent["id"],
                                      prompt="p", description="d", host_id=9)
        self.assertEqual(other["request"]["agent"]["bound_host_id"], 9)
        self.assertEqual(other["request"]["agent"]["bound_cwd"], "")
        same = store.start_child_run(user_id=1, thread_id=thread["id"], parent_run_id=parent["id"],
                                     prompt="p", description="d", host_id=3)
        self.assertEqual(same["request"]["agent"]["bound_cwd"], "/srv/app")

    def test_spawn_cwd_is_reviewed_against_parent_scope(self):
        from app import agents, chat_runs, permissions

        with db.tx() as conn:
            permissions.init_schema(conn)
        thread, parent = self._parent()
        req = parent["request"]
        req.setdefault("agent", {})["bound_host_id"] = 3
        req["agent"]["bound_cwd"] = "/srv/app"
        db.execute("UPDATE chat_runs SET request=? WHERE id=?", (store.dumps(req), parent["id"]))
        host = {"id": 3, "alias": "prod", "default_cwd": "", "agent_os": "linux", "disabled": False}
        gate = permissions.Gate(user_id=1, thread_id=parent["thread_id"], run_id=parent["id"], requester={"role": "main"},
                                unattended=True, notify=lambda event: None, cancel=None)

        async def finish(_request, _user, child):
            store.finish_run(child["id"], {"content": "ok"}, "completed")
            return None

        # No real host to probe: every path resolves to itself (no symlinks).
        # An unresolvable path now fails closed, which would deny "lib" too.
        resolve = AsyncMock(side_effect=lambda _u, _h, paths: list(paths))
        with patch("app.hosts.get_host", return_value=host), patch.object(chat_runs, "start", side_effect=finish), \
                patch("app.workspace._realpaths", resolve):
            for cwd in ("/", "/etc", "~"):
                text, trace = asyncio.run(agents.spawn(
                    user={"id": 1}, request=None, parent_run_id=parent["id"], thread_id=thread["id"],
                    arguments=json.dumps({"prompt": "p", "description": "Check", "cwd": cwd}), gate=gate,
                ))
                self.assertEqual(trace["status"], "error", cwd)
                self.assertEqual(trace["kind"], "permission_denied", cwd)
            text, trace = asyncio.run(agents.spawn(
                user={"id": 1}, request=None, parent_run_id=parent["id"], thread_id=thread["id"],
                arguments=json.dumps({"prompt": "p", "description": "Check", "cwd": "lib"}), gate=gate,
            ))
        self.assertNotEqual(trace["status"], "error")
        child = store.get_run(1, trace["child_id"])
        self.assertEqual(child["request"]["agent"]["bound_cwd"], "/srv/app/lib")

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
        self.assertEqual(len(leftover), 1)
        self.assertTrue(leftover[0].endswith(") also tests"))
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

    def test_followup_continuation_replays_the_childs_tool_transcript(self):
        from types import SimpleNamespace
        from app import chat_runs, chat_runtime

        thread, parent = self._parent()
        child = store.start_child_run(
            user_id=1, thread_id=thread["id"], parent_run_id=parent["id"],
            prompt="inspect", description="inspect",
        )
        transcript = [
            {"role": "user", "content": "inspect"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "type": "function",
                "function": {"name": "read_file", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "c1", "content": "FILE_BODY"},
            {"role": "assistant", "content": "looked around"},
        ]
        store.save_turn_progress(child["id"], transcript)
        self.assertTrue(store.queue_followup(child["id"], "also tests"))
        store.finish_run(child["id"], {"content": "looked around", "tools": []}, "completed")
        self.assertTrue(store.continue_child_with_followups(child["id"], store.take_followups(child["id"])))
        row = store.get_run(1, child["id"])
        sent = []

        async def invoke(request, user, payload, **_kwargs):
            sent.append(payload["messages"])
            result = SimpleNamespace(status=200, body={"choices": [{"message": {"content": "tests pass"}, "finish_reason": "stop"}]},
                                     usage={}, stream=None, credential_id=1)
            return user, SimpleNamespace(type_id="llama", source_id=1), "offline", "offline", result

        async def go():
            with patch.object(chat_runtime.api, "invoke_chat", invoke), patch.object(chat_runtime.api, "_safe_record_usage"):
                response = await chat_runtime.stream_dashboard_chat(
                    chat_runs.internal_request(1), {"id": 1}, row["request"], frozen_context=row["request"])
                return b"".join([chunk async for chunk in response.body_iterator])

        asyncio.run(go())
        # The continuation is the sealed transcript plus the follow-up, sent
        # as-is: tool calls and results intact, nothing re-sanitized away.
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0][1:len(transcript) + 1], transcript)
        self.assertIn("also tests", sent[0][len(transcript) + 1]["content"])

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
