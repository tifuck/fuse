"""Race regression checks. Run with scripts.test_stream_consistency, not live DB."""
import asyncio
import copy
import json
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from fastapi.responses import StreamingResponse
from app import db, chats, chat_runs, chat_runtime, chat_store as store


class ConsistencyTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(hasattr(db.connect(), 'checkout'), 'This suite must use the real pooled path')
        self.uid = int(time.time_ns() % 1_000_000_000)
        db.execute('INSERT INTO users(id,email,password_hash,created_at) VALUES(?,?,?,?)', (self.uid, f'{self.uid}@test.invalid', 'unused', db.now()))

    def run_fixture(self):
        thread = chats.create_thread(self.uid, model='offline')
        return store.start_turn(self.uid, thread['id'], 'Test', 0, 'turn-'+thread['id'], auto_title=False)['run']

    def test_event_ids_are_commit_ordered_per_user(self):
        ready, release, second_started = threading.Event(), threading.Event(), threading.Event()
        def first():
            with store.transaction() as conn:
                ident = store.event(conn, self.uid, 'audit.first')
                ready.set()
                if not release.wait(5): raise TimeoutError('test release')
                return ident
        def second():
            second_started.set()
            with store.transaction() as conn:
                return store.event(conn, self.uid, 'audit.second')
        with ThreadPoolExecutor(2) as workers:
            a = workers.submit(first)
            self.assertTrue(ready.wait(5))
            b = workers.submit(second)
            self.assertTrue(second_started.wait(5))
            try:
                time.sleep(.1)
                self.assertFalse(b.done(), 'second event must wait for publication order')
                self.assertEqual(store.events_after(self.uid, 0), [])
            finally:
                release.set()
            aid, bid = a.result(5), b.result(5)
        self.assertLess(aid, bid)
        self.assertEqual([e['id'] for e in store.events_after(self.uid, 0)], [aid, bid])
        self.assertEqual(store.cursor(self.uid), bid)

    def test_checkpoint_cannot_resurrect_finished_run_or_lose_metadata(self):
        run = self.run_fixture()
        pool = db.connect()
        ready, release, attempted = threading.Event(), threading.Event(), threading.Event()
        original = pool.checkout
        class Delayed:
            def __init__(self, conn): self.conn = conn
            def __getattr__(self, name): return getattr(self.conn, name)
            def execute(self, sql, args=()):
                result = self.conn.execute(sql, args)
                if threading.current_thread().name.startswith('checkpoint') and sql.startswith('SELECT id,status,message_id'):
                    ready.set()
                    if not release.wait(5): raise TimeoutError('test release')
                return result
        old_release = pool.release
        def finish():
            attempted.set()
            store.save_turn_progress(run['id'], [{'role':'user','content':'Test'}], loaded_tools=['grep'])
            store.finish_run(run['id'], {'content':'final answer'}, 'completed')
        with patch.object(pool, 'checkout', side_effect=lambda:Delayed(original())), patch.object(pool, 'release', side_effect=lambda c:old_release(c.conn)):
            with ThreadPoolExecutor(1, thread_name_prefix='checkpoint') as a, ThreadPoolExecutor(1) as b:
                one = a.submit(store.checkpoint, run['id'], {'content':'partial'}, [])
                self.assertTrue(ready.wait(5))
                two = b.submit(finish)
                self.assertTrue(attempted.wait(5))
                try:
                    time.sleep(.1)
                    self.assertFalse(two.done())
                finally:
                    release.set()
                one.result(5); two.result(5)
        store.checkpoint(run['id'], {'content':'late stale write'}, [])
        final = store.get_run(self.uid, run['id'])
        self.assertEqual(final['status'], 'completed')
        self.assertEqual(final['message']['content'], 'final answer')
        self.assertEqual(final['request']['agent']['loaded_tools'], ['grep'])

    def test_snapshot_seed_never_overlaps_live_leg(self):
        run = self.run_fixture()
        child = store.start_child_run(user_id=self.uid, thread_id=run['thread_id'], parent_run_id=run['id'], prompt='Inspect', description='Inspect', subagent_type='general-purpose', depth=0)
        calls = [{'id':f'call{i}','type':'function','function':{'name':'read_file','arguments':'{}'}} for i in range(5)]
        store.save_turn_progress(child['id'], [{'role':'user','content':'Inspect'},{'role':'assistant','content':'','tool_calls':calls}])
        before = store.get_run_snapshot(self.uid, child['id'])
        self.assertFalse(any(m.get('tools') for m in before['display_messages']))
        tools = [{'id':f't{i}','name':'read_file','status':'running'} for i in range(5)]
        store.checkpoint(child['id'], {'content':'','tools':tools})
        after = store.get_run_snapshot(self.uid, child['id'])
        self.assertFalse(any(m.get('tools') for m in after['display_messages']))
        self.assertEqual(len(after['message']['tools']), 5)
        self.assertGreater(after['event_cursor'], before['event_cursor'])

    def test_stream_flush_retains_new_events_and_drains_on_cancel(self):
        async def scenario(cancel):
            run = self.run_fixture()
            entered, release = threading.Event(), threading.Event()
            late = asyncio.Event()
            writes = []
            real = store.checkpoint
            once = False
            def checkpoint(ident, message, events=None):
                nonlocal once
                if events and not once:
                    once = True
                    entered.set()
                    if not release.wait(5): raise TimeoutError('test release')
                writes.append(copy.deepcopy(events))
                return real(ident, message, events=events)
            async def model(*args, **kwargs):
                async def chunks():
                    yield b'data: {"choices":[{"delta":{"content":"Hello"}}]}\n\n'
                    while not entered.is_set(): await asyncio.sleep(.001)
                    late.set()
                    yield b'data: {"fuse":{"event":"tool","id":"late","name":"read_file","status":"done","output":"saved"}}\n\n'
                    yield b'data: [DONE]\n\n'
                return StreamingResponse(chunks())
            chat_runs._stopping = False
            user = {'id':self.uid}
            with patch.object(store,'checkpoint',side_effect=checkpoint), patch.object(chat_runtime,'stream_dashboard_chat',side_effect=model):
                task = await chat_runs.start(chat_runs.internal_request(self.uid), user, run)
                try:
                    await asyncio.wait_for(late.wait(),3)
                    await asyncio.sleep(.03)
                    if cancel:
                        task.cancel()
                        await asyncio.sleep(.05)
                        self.assertFalse(task.done(), 'cancel must drain an in-flight persistence thread')
                finally:
                    release.set()
                await asyncio.wait_for(task,5)
            final = store.get_run(self.uid,run['id'])
            self.assertIn(final['status'], ('completed','interrupted','cancelled'))
            self.assertEqual(sum(1 for batch in writes for event in batch or [] if event.get('fuse',{}).get('id')=='late'),1)
            self.assertEqual(len([t for t in final['message']['tools'] if t['id']=='late']),1)
        asyncio.run(scenario(False))
        asyncio.run(scenario(True))
