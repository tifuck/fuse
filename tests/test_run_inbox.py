"""A live run's inbox: arrival signals cut blocked waits short, one parser
reads delivered envelopes, and deliveries become a reply segment."""
from __future__ import annotations

import asyncio
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("APP_SECRET", "x" * 32)

from app import chat_runs, chats, command_jobs, run_inbox  # noqa: E402
from app.chat_generation import inbox  # noqa: E402
from app.chat_views import final_report_text, render_task_output  # noqa: E402


def _report(cid="kid-1", text="All good.", state="completed", description="Audit"):
    return render_task_output(child_id=cid, state=state, text=text, description=description)


class SignalTest(unittest.IsolatedAsyncioTestCase):
    async def test_arrival_wakes_listener_with_item_id(self):
        with run_inbox.listen("run-1") as fut:
            run_inbox.arrived("run-1", "kid-1")
            self.assertEqual(await asyncio.wait_for(fut, 1), "kid-1")

    async def test_other_runs_and_ignored_ids_do_not_wake(self):
        with run_inbox.listen("run-1", ignore={"kid-1"}) as fut:
            run_inbox.arrived("run-2", "kid-9")
            run_inbox.arrived("run-1", "kid-1")
            await asyncio.sleep(0.05)
            self.assertFalse(fut.done())

    async def test_arrival_from_worker_thread(self):
        # Queues commit in asyncio.to_thread; the signal must cross threads.
        with run_inbox.listen("run-1") as fut:
            await asyncio.to_thread(run_inbox.arrived, "run-1", "job-1")
            self.assertEqual(await asyncio.wait_for(fut, 1), "job-1")

    async def test_listener_is_removed_on_exit(self):
        with run_inbox.listen("run-1"):
            pass
        self.assertNotIn("run-1", run_inbox._waiters)

    async def test_wait_or_arrival_outcomes(self):
        done = asyncio.get_running_loop().create_future()
        done.set_result(None)
        self.assertEqual(await run_inbox.wait_or_arrival(done, 1, "run-1"), (True, ""))
        never = asyncio.get_running_loop().create_future()
        self.assertEqual(await run_inbox.wait_or_arrival(never, 0.05, "run-1"), (False, ""))
        asyncio.get_running_loop().call_later(0.05, run_inbox.arrived, "run-1", "kid-2")
        self.assertEqual(await run_inbox.wait_or_arrival(never, 5, "run-1"), (False, "kid-2"))
        # The work being waited on is never cancelled by an early return.
        self.assertFalse(never.cancelled())
        never.cancel()

    async def test_no_run_id_waits_for_the_work_only(self):
        never = asyncio.get_running_loop().create_future()
        run_inbox.arrived("", "x")
        self.assertEqual(await run_inbox.wait_or_arrival(never, 0.05, None), (False, ""))
        never.cancel()


class CommandJoinTest(unittest.IsolatedAsyncioTestCase):
    async def test_join_returns_early_when_results_arrive(self):
        fut = asyncio.get_running_loop().create_future()
        rec = {"id": "job-1", "status": "running", "future": fut}
        with patch.object(command_jobs, "_get_scoped", return_value=rec), \
             patch.object(command_jobs, "render", return_value=("running", {"status": "running"})):
            asyncio.get_running_loop().call_later(0.05, run_inbox.arrived, "run-1", "kid-1")
            text, trace = await asyncio.wait_for(command_jobs.join("job-1", 1, 30, run_id="run-1"), 2)
        self.assertTrue(trace.get("early_return"))
        self.assertFalse(fut.done())
        fut.cancel()

    async def test_join_ignores_its_own_job_and_plain_timeouts(self):
        fut = asyncio.get_running_loop().create_future()
        rec = {"id": "job-1", "status": "running", "future": fut}
        with patch.object(command_jobs, "_get_scoped", return_value=rec), \
             patch.object(command_jobs, "render", return_value=("running", {"status": "running"})):
            asyncio.get_running_loop().call_later(0.02, run_inbox.arrived, "run-1", "job-1")
            _text, trace = await command_jobs.join("job-1", 1, 0.1, run_id="run-1")
        self.assertNotIn("early_return", trace)
        fut.cancel()


class ForegroundCommandTest(unittest.IsolatedAsyncioTestCase):
    async def _shell(self, command):
        from unittest.mock import AsyncMock
        from app import workspace

        rec = {"id": "job-1", "status": "running"}
        calls = {}
        with patch.object(workspace, "_is_agent", return_value=True), \
             patch.object(workspace, "_make_agent_runner", return_value=None), \
             patch.object(command_jobs, "launch_async", AsyncMock(return_value=rec)), \
             patch.object(command_jobs, "join", AsyncMock(return_value=("running", {"status": "running", "early_return": True}))) as join, \
             patch.object(command_jobs, "arm_background", side_effect=lambda r: calls.setdefault("armed", True)), \
             patch.object(command_jobs, "mark_notified"), \
             patch.object(command_jobs, "kill", side_effect=lambda *a: calls.setdefault("killed", True)), \
             patch.object(command_jobs, "render", return_value=("status: running", {"status": "running"})):
            text, _trace = await workspace._shell(1, 7, "box", {"command": command}, "/srv", run_id="run-1")
        self.assertEqual(join.call_args.kwargs.get("run_id"), "run-1")
        return text, calls

    async def test_arrival_moves_command_to_background(self):
        text, calls = await self._shell("make test")
        self.assertTrue(calls.get("armed"))
        self.assertIn("moved to the background before its timeout", text)

    async def test_arrival_stops_a_bare_sleep(self):
        text, calls = await self._shell("sleep 600")
        self.assertTrue(calls.get("killed"))
        self.assertTrue(text.startswith("Sleep stopped early."))


class SubagentJoinTest(unittest.IsolatedAsyncioTestCase):
    async def test_get_output_returns_running_with_note_on_other_arrival(self):
        from app import agents

        row = {"id": "kid-1", "status": "running", "parent_run_id": "run-1", "request": {"agent": {"description": "Audit"}}}

        async def slow(_run_id, timeout):
            await asyncio.sleep(timeout)

        with patch.object(agents.chat_store, "resolve_child_run", return_value=row), \
             patch.object(agents.chat_runs, "wait_for_run", slow):
            asyncio.get_running_loop().call_later(0.05, run_inbox.arrived, "run-1", "kid-2")
            text, rec = await asyncio.wait_for(agents.get_output(
                user_id=1, arguments='{"id": "kid-1"}', parent_run_id="run-1"), 2)
        self.assertEqual(rec["status"], "running")
        self.assertTrue(text.startswith(run_inbox.EARLY_RETURN_NOTE))


class EnvelopeTest(unittest.TestCase):
    def test_items_in_order_and_quoted_close_tag(self):
        report = _report(text="The parser stops at </task> too early.")
        command = '<command_result id="job-1" state="completed">\nhost: h command: ls\n\nok\n</command_result>'
        items = inbox.envelope_items("preamble\n" + report + "\n\n" + command)
        self.assertEqual(len(items), 2)
        self.assertIn("too early.", items[0])
        self.assertTrue(items[0].endswith("(pass task_id to continue this subagent)"))
        self.assertEqual(items[1], command)

    def test_display_text_drops_preamble_and_clips_whole_items(self):
        text = inbox.display_text([_report(text="x" * 20_000)])
        self.assertTrue(text.startswith('<task id="kid-1"'))
        self.assertLessEqual(len(text), inbox.DISPLAY_ITEM_MAX + 40)
        self.assertIn("[clipped for display]", text)
        self.assertIn("</task_result>\n</task>", text)

    def test_results_message_frames_every_envelope(self):
        msg = inbox.results_message(["A", "B"])
        self.assertEqual(msg["role"], "user")
        self.assertTrue(msg["content"].startswith(inbox.RESULTS_HEAD))
        self.assertIn("A\n\nB", msg["content"])


class DeliverySegmentTest(unittest.TestCase):
    def _output(self):
        return chat_runs.Output({"message": {"content": "", "reasoning": "", "tools": [], "segments": []}})

    def test_delivery_lands_between_text_and_validates(self):
        out = self._output()
        out.apply({"choices": [{"delta": {"content": "Checking."}}]})
        out.apply({"fuse": {"event": "delivery", "text": inbox.display_text([_report()])}})
        out.apply({"choices": [{"delta": {"content": "Read it."}}]})
        stored = out.stored()
        self.assertEqual([p["type"] for p in stored["segments"]], ["content", "delivery", "content"])
        parsed = chats.parse_messages([{**stored, "id": "m1", "role": "assistant"}], strict=True)[0]
        self.assertEqual(parsed["segments"][1]["type"], "delivery")

    def test_back_to_back_deliveries_share_a_segment(self):
        out = self._output()
        out.apply({"fuse": {"event": "delivery", "text": "<a>"}})
        out.apply({"fuse": {"event": "delivery", "text": "<b>"}})
        self.assertEqual(out.message["segments"], [{"type": "delivery", "text": "<a>\n<b>"}])

    def test_segment_patch_is_structural(self):
        before = [{"type": "delivery", "text": "<a>"}]
        after = [{"type": "delivery", "text": "<a>\n<b>"}]
        self.assertEqual(chat_runs._segments_patch(before, after), {"from": 0, "items": after})

    def test_final_report_ignores_prose_before_a_delivery(self):
        message = {"content": "Waiting.Final answer.", "segments": [
            {"type": "content", "text": "Waiting."}, {"type": "delivery", "text": "<x>"},
            {"type": "content", "text": "Final answer."}]}
        self.assertEqual(final_report_text(message), "Final answer.")


if __name__ == "__main__":
    unittest.main()
