"""Explicit opt-in integration checks in a disposable MariaDB schema."""
import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from app import agents, chat_store as store, chats, db
from tests.mariadb_fixture import isolated_database


@pytest.mark.skipif(os.environ.get("FUSE_CHAT_TEST_MODE") != "1", reason="requires disposable MariaDB fixture")
def test_concurrent_receipt_and_recompletion():
    with isolated_database():
        store.invalidate_committed_cursors()
        db.execute("INSERT INTO users(id,email,password_hash,created_at) VALUES(1,'receipt@test.invalid','unused',?)", (db.now(),))
        thread = chats.create_thread(1, model="offline")
        parent = store.start_turn(1, thread["id"], "go", thread["revision"], "receipt-parent")["run"]
        child = store.start_child_run(user_id=1, thread_id=thread["id"], parent_run_id=parent["id"], prompt="inspect", description="worker", background=True)
        store.finish_run(child["id"], {"content": "same report"}, "completed")
        old = agents._envelope(store.get_run(1, child["id"]))
        store.queue_child_result(parent["id"], old, child_id=child["id"])
        barrier = Barrier(2)

        def persist():
            barrier.wait()
            store.save_turn_progress(parent["id"], [{"role": "user", "content": old}])

        def resume():
            barrier.wait()
            store.start_child_run(user_id=1, thread_id=thread["id"], parent_run_id=parent["id"], prompt="again", description="worker", resume_from_id=child["id"], background=True)
            store.finish_run(child["id"], {"content": "same report"}, "completed")

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(persist), pool.submit(resume)]
            for future in futures:
                future.result(timeout=20)
        assert not store.get_run(1, child["id"])["request"]["agent"].get("wake_sent")
        assert not store.queue_child_result(parent["id"], old, child_id=child["id"])
        fresh = agents._envelope(store.get_run(1, child["id"]))
        assert store.queue_child_result(parent["id"], fresh, child_id=child["id"])
        store.save_turn_progress(parent["id"], [{"role": "user", "content": fresh}])
        assert store.get_run(1, child["id"])["request"]["agent"]["wake_sent"]
        live = store.start_child_run(user_id=1, thread_id=thread["id"], parent_run_id=parent["id"], prompt="live", description="live")
        rows = store.list_thread_agents(1, parent["thread_id"], limit=1)
        assert {row["id"] for row in rows} == {child["id"], live["id"]}
