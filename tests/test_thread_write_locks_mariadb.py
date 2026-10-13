"""Thread writers rebuild the transcript from the locked row, not a snapshot.

On MariaDB a plain SELECT in REPEATABLE READ returns the transaction's
snapshot. A writer that computed the messages blob from it silently reverted
anything committed after the snapshot was taken. Each test commits a change
from another connection after the writer's snapshot exists and checks that
the writer keeps it.
"""
import json
import os
import threading
from unittest.mock import patch

import pytest

from app import chat_tx
from app import chat_store as store, chats, db
from tests.mariadb_fixture import isolated_database

pytestmark = pytest.mark.skipif(os.environ.get("FUSE_CHAT_TEST_MODE") != "1", reason="requires disposable MariaDB fixture")


def _setup():
    store.invalidate_committed_cursors()
    db.execute("INSERT INTO users(id,email,password_hash,created_at) VALUES(1,'locks@test.invalid','unused',?)", (db.now(),))
    thread = chats.create_thread(1, model="offline")
    started = store.start_turn(1, thread["id"], "go", thread["revision"], "locks-turn")
    pk = chats.resolve_thread_pk(1, thread["id"])
    return thread, pk, started["run"]


def _messages(pk):
    return json.loads(db.fetchone("SELECT messages FROM chat_threads WHERE id=?", (pk,))["messages"])


def _revision(pk):
    return int(db.fetchone("SELECT revision FROM chat_threads WHERE id=?", (pk,))["revision"])


def _after_snapshot(action):
    """Run `action` on another connection right after the caller's first
    reads (chat_tx.command), i.e. once its snapshot exists."""
    real = chat_tx.command

    def command(*args, **kwargs):
        result = real(*args, **kwargs)
        worker = threading.Thread(target=action)
        worker.start()
        worker.join()
        return result

    return patch.object(chat_tx, "command", command)


def _edit_first_user_message(pk, text):
    def action():
        messages = _messages(pk)
        messages[0]["content"] = text
        db.execute("UPDATE chat_threads SET messages=?, revision=revision+1 WHERE id=?", (chat_tx.dumps(messages), pk))
    return action


def test_lock_thread_reads_past_the_snapshot():
    with isolated_database():
        _thread, pk, _run = _setup()
        with chat_tx.transaction() as conn:
            stale = conn.execute("SELECT revision FROM chat_threads WHERE id=?", (pk,)).fetchone()["revision"]
            worker = threading.Thread(target=lambda: db.execute("UPDATE chat_threads SET revision=revision+5 WHERE id=?", (pk,)))
            worker.start()
            worker.join()
            assert conn.execute("SELECT revision FROM chat_threads WHERE id=?", (pk,)).fetchone()["revision"] == stale
            assert chat_tx.lock_thread(conn, pk, 1)["revision"] == stale + 5


def test_steer_keeps_a_transcript_change_committed_after_its_snapshot():
    with isolated_database():
        thread, pk, _run = _setup()
        before = _revision(pk)
        with _after_snapshot(_edit_first_user_message(pk, "edited elsewhere")):
            store.steer_turn(1, thread["id"], "also this", "locks-steer")
        messages = _messages(pk)
        assert messages[0]["content"] == "edited elsewhere"
        assert any(msg.get("content") == "also this" for msg in messages)
        assert _revision(pk) == before + 2


def test_finish_run_keeps_a_transcript_change_committed_after_its_snapshot():
    with isolated_database():
        _thread, pk, run = _setup()
        before = _revision(pk)
        real = chat_tx.lock_thread

        def lock_thread(conn, thread_id, user_id):
            # finish_run's first consistent read happens inside lock_thread's
            # caller; take a snapshot now, then commit elsewhere.
            conn.execute("SELECT revision FROM chat_threads WHERE id=?", (thread_id,)).fetchone()
            worker = threading.Thread(target=_edit_first_user_message(pk, "edited during finish"))
            worker.start()
            worker.join()
            return real(conn, thread_id, user_id)

        with patch.object(chat_tx, "lock_thread", lock_thread):
            store.finish_run(run["id"], {"content": "final answer"}, "completed")
        messages = _messages(pk)
        assert messages[0]["content"] == "edited during finish"
        assert messages[-1]["content"] == "final answer"
        assert _revision(pk) == before + 2


def test_turn_started_while_the_last_run_finalized_keeps_the_final_reply():
    with isolated_database():
        thread, pk, run = _setup()

        def finalize():
            store.finish_run(run["id"], {"content": "final answer"}, "completed")

        with _after_snapshot(finalize):
            store.start_turn(1, thread["id"], "next", None, "locks-turn-2")
        messages = _messages(pk)
        assert [msg["role"] for msg in messages] == ["user", "assistant", "user", "assistant"]
        assert messages[1]["content"] == "final answer"


def test_turn_with_a_stale_revision_conflicts_instead_of_reverting():
    with isolated_database():
        thread, pk, run = _setup()
        seen = _revision(pk)

        def finalize():
            store.finish_run(run["id"], {"content": "final answer"}, "completed")

        with _after_snapshot(finalize), pytest.raises(chats.ChatValidationError) as raised:
            store.start_turn(1, thread["id"], "next", seen, "locks-turn-3")
        assert raised.value.status_code == 409
        assert _messages(pk)[1]["content"] == "final answer"
