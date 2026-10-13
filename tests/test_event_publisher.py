"""Publisher lifecycle checks without database or provider traffic."""
import asyncio
import unittest
from contextlib import nullcontext
from unittest.mock import patch

from app import chat_pub
from app import chat_store as store


class PublisherTests(unittest.IsolatedAsyncioTestCase):
    async def asyncTearDown(self):
        await store.stop_publisher()

    async def test_shutdown_drains_all_accepted_events(self):
        accepted = []

        def commit(batch):
            accepted.extend(item[3]["sequence"] for item in batch)
            return [item[3]["sequence"] for item in batch]

        with patch.object(chat_pub, "_commit_publications", side_effect=commit):
            store.start_publisher()
            futures = [asyncio.get_running_loop().create_future() for _ in range(300)]
            for sequence, future in enumerate(futures):
                store.publish(1, "run.progress", payload={"sequence": sequence}, future=future)
            await store.stop_publisher()
            self.assertEqual(accepted, list(range(300)))
            self.assertEqual([future.result() for future in futures], list(range(300)))

    async def test_failed_commit_rejects_every_waiter(self):
        with patch.object(chat_pub, "_commit_publications", side_effect=ValueError("storage unavailable")):
            store.start_publisher()
            future = asyncio.get_running_loop().create_future()
            store.publish(1, "run.progress", future=future)
            with self.assertRaisesRegex(RuntimeError, "publication failed"):
                await future

    async def test_one_bad_payload_fails_only_its_own_waiter(self):
        ids = iter(range(1, 100))

        def event(conn, user_id, type, thread_id, payload):
            chat_pub.chat_tx.dumps(payload)  # strict JSON: inf raises
            return next(ids)

        with patch.object(chat_pub.chat_tx, "transaction", side_effect=lambda: nullcontext(object())), \
                patch.object(chat_pub.chat_tx, "event", side_effect=event):
            store.start_publisher()
            loop = asyncio.get_running_loop()
            futures = [loop.create_future() for _ in range(3)]
            for user, payload, future in zip((1, 2, 3), ({"n": 1}, {"n": float("inf")}, {"n": 3}), futures):
                store.publish(user, "run.progress", payload=payload, future=future)
            self.assertIsInstance(await futures[0], int)
            with self.assertRaisesRegex(RuntimeError, "publication failed"):
                await futures[1]
            self.assertIsInstance(await futures[2], int)

    async def test_full_queue_explicitly_rejects_new_work(self):
        with patch.object(chat_pub, "_PUB_MAX_DEPTH", 1), patch.object(chat_pub, "_commit_publications", return_value=[1]):
            store.start_publisher()
            first = asyncio.get_running_loop().create_future()
            store.publish(1, "run.progress", future=first)
            with self.assertRaisesRegex(RuntimeError, "queue is full"):
                store.publish(1, "run.progress")
            self.assertEqual(await first, 1)

    async def test_fallback_fails_a_stranded_queue_instead_of_hanging(self):
        loop = asyncio.get_running_loop()
        stranded = loop.create_future()
        chat_pub._pub_queue.append((1, "run.progress", None, {"sequence": 0}, None, stranded))
        chat_pub._pub_task = None  # committer died with work still queued
        direct = loop.create_future()
        with patch.object(chat_pub.chat_tx, "transaction", return_value=nullcontext(object())), \
                patch.object(chat_pub.chat_tx, "event", return_value=7):
            chat_pub.publish(1, "run.progress", payload={"sequence": 1}, future=direct)
        self.assertEqual(await direct, 7)
        with self.assertRaisesRegex(RuntimeError, "publication failed"):
            await stranded
        self.assertEqual(list(chat_pub._pub_queue), [])
