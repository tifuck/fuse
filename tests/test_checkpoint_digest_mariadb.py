"""Checkpoint request-digest guard and request cache in a disposable MariaDB schema."""
import json
import os
from unittest.mock import patch

import pytest

from app import chat_checkpoint, chat_tx
from app import chat_store as store, chats, db
from tests.mariadb_fixture import isolated_database

pytestmark = pytest.mark.skipif(os.environ.get("FUSE_CHAT_TEST_MODE") != "1", reason="requires disposable MariaDB fixture")


def _run():
    store.invalidate_committed_cursors()
    db.execute("INSERT INTO users(id,email,password_hash,created_at) VALUES(1,'digest@test.invalid','unused',?)", (db.now(),))
    thread = chats.create_thread(1, model="offline")
    return store.start_turn(1, thread["id"], "go", thread["revision"], "digest-turn")["run"]


def _row(run_id):
    row = db.fetchone("SELECT message,request,request_digest FROM chat_runs WHERE id=?", (run_id,))
    return json.loads(row["message"]), json.loads(row["request"]), row["request_digest"]


def test_token_checkpoint_writes_message_under_digest_guard():
    with isolated_database():
        run = _run()
        assert store._has_request_digest()
        store.checkpoint(run["id"], {"content": "partial"}, events=[], durable=False)
        message, _, _ = _row(run["id"])
        assert message["content"] == "partial"
        # Wildcard reads return the generated column; runs must stay JSON-safe.
        assert json.dumps(store.get_run(1, run["id"]), default=None)


def test_foreign_request_write_refreshes_cache_and_is_not_clobbered():
    with isolated_database():
        run = _run()
        store.checkpoint(run["id"], {"content": "a"}, events=[], durable=True)
        _, request, _ = _row(run["id"])
        request["foreign_marker"] = 7
        db.execute("UPDATE chat_runs SET request=? WHERE id=?", (store.dumps(request), run["id"]))
        store.checkpoint(run["id"], {"content": "ab"}, events=[{"fuse": {"event": "activity", "phase": "model"}}], durable=True)
        message, request, _ = _row(run["id"])
        assert message["content"] == "ab"
        assert request["foreign_marker"] == 7
        assert request["agent"]["activity"]["phase"] == "model"


def test_stale_guard_skips_state_write_and_drops_cache():
    with isolated_database():
        run = _run()
        store.checkpoint(run["id"], {"content": "kept"}, events=[], durable=True)
        stale = store._checkpoint_request
        with patch.object(chat_checkpoint, "_checkpoint_request", lambda rid, row: (stale(rid, row)[0], "0" * 32)):
            store.checkpoint(run["id"], {"content": "stale"}, events=[], durable=True)
        message, _, _ = _row(run["id"])
        assert message["content"] == "kept"
        assert run["id"] not in store._request_cache


def test_repeated_heartbeat_does_not_rewrite_request():
    with isolated_database():
        run = _run()
        beat = [{"fuse": {"event": "heartbeat"}}]
        store.checkpoint(run["id"], {"content": ""}, events=beat, durable=True)
        _, first, digest = _row(run["id"])
        writes = []
        real = store.transaction

        def spy():
            ctx = real()
            conn = ctx.__enter__()
            original = conn.execute

            def execute(sql, args=()):
                if sql.lstrip().upper().startswith("UPDATE CHAT_RUNS"):
                    writes.append(sql)
                return original(sql, args)
            conn.execute = execute
            try:
                yield conn
            except BaseException as exc:
                if not ctx.__exit__(type(exc), exc, exc.__traceback__):
                    raise
            else:
                ctx.__exit__(None, None, None)
            finally:
                conn.execute = original

        from contextlib import contextmanager
        with patch.object(chat_tx, "transaction", contextmanager(spy)):
            store.checkpoint(run["id"], {"content": ""}, events=beat, durable=True)
        _, second, digest_after = _row(run["id"])
        assert second == first and digest_after == digest
        assert writes and all("request=?" not in sql for sql in writes)


def test_request_cache_is_mutation_isolated():
    with isolated_database():
        run = _run()
        store.checkpoint(run["id"], {"content": "x"}, events=[], durable=True)
        cached = store._request_cache[run["id"]][1]
        before = json.loads(store.dumps(cached))
        with patch.object(chat_checkpoint, "_checkpoint_request", lambda rid, row: (cached, "0" * 32)):
            store.checkpoint(run["id"], {"content": "y"}, events=[{"fuse": {"event": "activity", "phase": "tools"}}], durable=True)
        assert json.loads(store.dumps(cached)) == before
