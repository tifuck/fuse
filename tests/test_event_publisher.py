"""Publisher lifecycle checks without database or provider traffic."""
import asyncio
import unittest
from unittest.mock import patch

from app import chat_store as store


class PublisherTests(unittest.IsolatedAsyncioTestCase):
    async def asyncTearDown(self):
        await store.stop_publisher()

    async def test_shutdown_drains_all_accepted_events(self):
        accepted = []

        def commit(batch):
            accepted.extend(item[3]["sequence"] for item in batch)
            return [item[3]["sequence"] for item in batch]

        with patch.object(store, "_commit_publications", side_effect=commit):
            store.start_publisher()
            futures = [asyncio.get_running_loop().create_future() for _ in range(300)]
            for sequence, future in enumerate(futures):
                store.publish(1, "run.progress", payload={"sequence": sequence}, future=future)
            await store.stop_publisher()
            self.assertEqual(accepted, list(range(300)))
            self.assertEqual([future.result() for future in futures], list(range(300)))

    async def test_failed_commit_rejects_every_waiter(self):
        with patch.object(store, "_commit_publications", side_effect=ValueError("storage unavailable")):
            store.start_publisher()
            future = asyncio.get_running_loop().create_future()
            store.publish(1, "run.progress", future=future)
            with self.assertRaisesRegex(RuntimeError, "publication failed"):
                await future

    async def test_full_queue_explicitly_rejects_new_work(self):
        with patch.object(store, "_PUB_MAX_DEPTH", 1), patch.object(store, "_commit_publications", return_value=[1]):
            store.start_publisher()
            first = asyncio.get_running_loop().create_future()
            store.publish(1, "run.progress", future=first)
            with self.assertRaisesRegex(RuntimeError, "queue is full"):
                store.publish(1, "run.progress")
            self.assertEqual(await first, 1)
