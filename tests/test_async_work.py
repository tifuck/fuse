import asyncio
import threading
import unittest
from unittest.mock import AsyncMock, patch

from app.async_work import durable_call


class DurableCallTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancellation_drains_write_without_blocking_loop(self):
        entered = threading.Event()
        release = threading.Event()
        finished = threading.Event()

        def write():
            entered.set()
            release.wait(2)
            finished.set()

        task = asyncio.create_task(durable_call(write))
        try:
            while not entered.is_set():
                await asyncio.sleep(0)
            task.cancel()
            await asyncio.sleep(0.01)
            self.assertFalse(task.done())
            task.cancel()
            await asyncio.sleep(0.01)
            self.assertFalse(task.done())
        finally:
            release.set()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(finished.is_set())

    async def test_result_and_error_propagation(self):
        self.assertEqual(await durable_call(lambda: 42), 42)
        def fail():
            raise ValueError("write failed")
        with self.assertRaisesRegex(ValueError, "write failed"):
            await durable_call(fail)

    async def test_cancel_during_spawn_hands_off_committed_child(self):
        from app import agents, chat_runs, chat_store
        entered = threading.Event()
        release = threading.Event()
        child = {"id": "child", "status": "queued"}
        def admit(**kwargs):
            entered.set()
            release.wait(2)
            return child
        parent = {"id": "parent", "thread_id": 1, "request": {"model": "offline", "agent": {}}}
        with patch.object(chat_store, "get_run", return_value=parent), \
                patch.object(agents, "resolve_spawn_model", AsyncMock(return_value=("offline", None))), \
                patch.object(chat_store, "start_child_run", side_effect=admit), \
                patch.object(chat_runs, "start", new=AsyncMock(return_value=None)) as start:
            task = asyncio.create_task(agents.spawn(user={"id": 1}, request=None,
                parent_run_id="parent", arguments='{"prompt":"inspect"}', thread_id=1))
            try:
                while not entered.is_set():
                    await asyncio.sleep(0)
                task.cancel()
            finally:
                release.set()
            with self.assertRaises(asyncio.CancelledError):
                await task
            start.assert_awaited_once()
            self.assertEqual(start.call_args.args[2]["id"], "child")
