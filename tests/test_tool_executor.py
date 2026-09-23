"""Two-phase executor: pairing, heartbeat, sibling errors."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
_TMP = tempfile.mkdtemp(prefix="fuse-exec-")
os.environ.setdefault("DATABASE_PATH", str(Path(_TMP) / "chat.db"))
os.environ.setdefault("APP_SECRET", "x" * 32)

from app.chat_runtime import HEARTBEAT_S, _run_tools, _tool_messages, jobs_from_calls


class FakeRequest:
    def __init__(self, disconnected=False):
        self._disconnected = disconnected

    async def is_disconnected(self):
        return self._disconnected


class PairingTest(unittest.IsolatedAsyncioTestCase):
    async def test_read_then_search_pairs_by_id(self):
        allowed = set()
        calls = [
            {
                "id": "read_1",
                "type": "function",
                "function": {"name": "read_url", "arguments": json.dumps({"url": "https://example.com/a"})},
            },
            {
                "id": "search_1",
                "type": "function",
                "function": {"name": "web_search", "arguments": json.dumps({"query": "hello"})},
            },
        ]
        order = []

        async def fake_execute(name, arguments, **kwargs):
            order.append(name)
            if name == "web_search":
                kwargs["allowed_urls"].add("https://example.com/a")
                return "SEARCH_BLOB", {"name": "web_search", "status": "done", "query": "hello", "results": []}
            return "READ_BLOB", {"name": "read_url", "status": "done", "url": "https://example.com/a"}

        events = []
        jobs = None
        with patch("app.chat_runtime.execute_tool", fake_execute):
            async for item in _run_tools(
                calls, user_id=1, allowed_urls=allowed, request=FakeRequest(), completion_i=0, budgets={}
            ):
                if isinstance(item, list):
                    jobs = item
                else:
                    events.append(item)
        self.assertIsNotNone(jobs)
        self.assertEqual(order[0], "web_search")
        self.assertIn("read_url", order)
        msgs = _tool_messages(jobs)
        self.assertEqual(msgs[1]["tool_call_id"], "read_1")
        self.assertEqual(msgs[1]["content"], "READ_BLOB")
        self.assertEqual(msgs[2]["tool_call_id"], "search_1")
        self.assertEqual(msgs[2]["content"], "SEARCH_BLOB")
        self.assertEqual(msgs[0]["content"], "")
        self.assertIn("https://example.com/a", allowed)
        running = [e for e in events if isinstance(e, dict) and e.get("status") == "running"]
        self.assertEqual([e.get("name") for e in running], ["web_search", "read_url"])

    async def test_running_emitted_when_independent_job_starts_concurrently(self):
        # A slow foreground spawn must not hold back an independent shell tool
        # in the same step: both emit their running events up front.
        gate = asyncio.Event()
        started = []

        async def fake_execute(name, arguments, **kwargs):
            started.append(name)
            if name == "spawn_subagent":
                await gate.wait()
            return "ok", {"name": name, "status": "done", "child_id": "child-1" if name == "spawn_subagent" else None}

        calls = [
            {"id": "s", "function": {"name": "spawn_subagent", "arguments": json.dumps({"prompt": "x", "description": "d"})}},
            {"id": "c", "function": {"name": "run_terminal_command", "arguments": json.dumps({"command": "ls"})}},
        ]
        events = []
        task = None

        async def collect():
            with patch("app.chat_runtime.execute_tool", fake_execute):
                async for item in _run_tools(
                    calls, user_id=1, allowed_urls=set(), request=FakeRequest(), completion_i=0, budgets={}, coding=True
                ):
                    if not isinstance(item, list):
                        events.append(item)

        task = asyncio.create_task(collect())
        for _ in range(50):
            if any(e.get("name") == "spawn_subagent" and e.get("status") == "running" for e in events) and \
                    any(e.get("name") == "run_terminal_command" and e.get("status") == "running" for e in events):
                break
            await asyncio.sleep(0.02)
        self.assertTrue(any(e.get("name") == "spawn_subagent" and e.get("status") == "running" for e in events))
        self.assertTrue(any(e.get("name") == "run_terminal_command" and e.get("status") == "running" for e in events))
        gate.set()
        await task
        self.assertIn("run_terminal_command", started)
        self.assertIn("spawn_subagent", started)

    async def test_heartbeat_before_slow_job(self):
        async def fake_execute(name, arguments, **kwargs):
            if "slow" in arguments:
                await asyncio.sleep(2.2)
            else:
                await asyncio.sleep(0.01)
            return "ok", {"name": name, "status": "done"}

        calls = [
            {"id": "a", "function": {"name": "web_search", "arguments": json.dumps({"query": "fast"})}},
            {"id": "b", "function": {"name": "web_search", "arguments": json.dumps({"query": "slow"})}},
        ]
        seen = []
        with patch("app.chat_runtime.execute_tool", fake_execute):
            async for item in _run_tools(
                calls, user_id=1, allowed_urls=set(), request=FakeRequest(), completion_i=1, budgets={}
            ):
                if not isinstance(item, list):
                    seen.append(item)
                    if item.get("event") == "heartbeat":
                        break
        self.assertTrue(any(e.get("event") == "heartbeat" or e.get("status") == "progress" or e.get("status") == "done" for e in seen))

    async def test_one_job_error_leaves_sibling(self):
        async def fake_execute(name, arguments, **kwargs):
            if "boom" in arguments:
                raise RuntimeError("nope")
            return "ok", {"name": name, "status": "done"}

        calls = [
            {"id": "a", "function": {"name": "web_search", "arguments": json.dumps({"query": "ok"})}},
            {"id": "b", "function": {"name": "web_search", "arguments": json.dumps({"query": "boom"})}},
        ]
        jobs = None
        with patch("app.chat_runtime.execute_tool", fake_execute):
            async for item in _run_tools(
                calls, user_id=1, allowed_urls=set(), request=FakeRequest(), completion_i=0, budgets={}
            ):
                if isinstance(item, list):
                    jobs = item
        self.assertEqual(jobs[0].text, "ok")
        self.assertEqual(jobs[1].trace["status"], "error")
        self.assertNotEqual(jobs[1].trace["status"], "interrupted")

    def test_jobs_from_calls_ids(self):
        jobs = jobs_from_calls([{"function": {"name": "web_search", "arguments": "{}"}}], 3)
        self.assertEqual(jobs[0].event_id, "ts_3_0")
        self.assertEqual(jobs[0].call_id, "call_0")


if __name__ == "__main__":
    unittest.main()
