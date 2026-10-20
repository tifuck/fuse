"""Large inline images live once per thread; providers still get the original bytes."""
import base64
import copy
import hashlib
import json
import re
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit

import pymysql
import pytest

from app import chat_history, chat_images, chat_runtime, chat_store as store, chats, db, model_caps, mysql_compat
from tests.test_image_cache_replay import invoke, routed  # noqa: F401  (fixture)
from tests.test_read_file_images_runtime import IMAGE, pixels

BIG = base64.b64encode(bytes(range(256)) * 64).decode()  # 16 KiB of pixels
URL = f"data:image/png;base64,{BIG}"
SHA = hashlib.sha256(URL.encode()).hexdigest()
REF = chat_images.PREFIX + SHA


def shot(url=URL, label="screenshot"):
    return {"role": "user", "content": [{"type": "text", "text": label},
                                        {"type": "image_url", "image_url": {"url": url}}]}


def big_job(index=0):
    image = {**IMAGE, "data": BIG}
    return chat_runtime.ToolJob(index, f"call-{index}", "read_file", '{"path":"shot.png"}', f"event-{index}",
                                text="Image read: shot.png", done=True,
                                trace={"path": "shot.png", "image": chats.tool_image_metadata(image)}, image=image)


def test_canonical_swaps_only_large_inline_images_without_mutating():
    small = "data:image/png;base64,QQ=="
    remote = "https://example.invalid/shot.png"
    rows = [shot(), shot(small), shot(remote), {"role": "tool", "tool_call_id": "c", "content": "x" * 20000},
            {"role": "user", "content": [{"type": "input_image", "image_url": URL}]}]
    original = copy.deepcopy(rows)
    out, images = chat_images.canonical(rows)
    assert pixels(out) == [REF, small, remote]
    assert out[-1]["content"][0]["image_url"] == REF
    assert images == {SHA: URL}
    assert rows == original
    assert out[1] is rows[1] and out[3] is rows[3]  # untouched rows are shared, not copied
    assert chat_images.canonical(out) == (out, {})  # idempotent
    assert BIG not in json.dumps(out)


def test_hashes_and_user_matching_ignore_the_stored_form():
    inline, stored = [shot()], chat_images.canonical([shot()])[0]
    assert chat_history.input_hashes(inline) == chat_history.input_hashes(stored)
    assert chat_history._matches_user(stored[0]["content"], inline[0]["content"])
    assert not chat_history._matches_user(stored[0]["content"], shot(label="other")["content"])


def test_cache_fingerprint_is_the_same_for_both_forms():
    from app.chat_generation import cache_watch

    inline = [{"role": "system", "content": "frozen"}, shot()]
    assert cache_watch.fingerprint(inline) == cache_watch.fingerprint(chat_images.canonical(inline)[0])


def test_provider_receives_the_inline_bytes_for_a_reference(routed, monkeypatch):
    _, sent = routed
    looked_up = []

    def lookup(user_id, shas):
        looked_up.append((user_id, list(shas)))
        return {SHA: URL}

    monkeypatch.setattr(chat_images, "lookup", lookup)
    invoke([shot()], {})
    inline = sent[-1]
    invoke(chat_images.canonical([shot()])[0], {})
    assert sent[-1] == inline  # same bytes, same image admission key, same cache prefix
    assert looked_up == [(1, [SHA])]  # only the referenced request looked anything up
    monkeypatch.setattr(chat_images, "lookup", lambda user_id, shas: {})
    invoke(chat_images.canonical([shot()])[0], {})
    assert chat_images.MISSING in json.dumps(sent[-1])
    assert chat_images.PREFIX not in json.dumps(sent)


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="fuse-images-")
        self.addCleanup(self.tmp.cleanup)
        self.conn = sqlite3.connect(str(Path(self.tmp.name) / "test.db"), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        db._init(self.conn)
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.addCleanup(self.conn.close)
        p = patch.object(db, "connect", return_value=self.conn)
        p.start()
        self.addCleanup(p.stop)
        chat_images._cache.clear()
        self.addCleanup(chat_images._cache.clear)
        for uid in (1, 2):
            db.execute("INSERT INTO users(id,email,password_hash,created_at) VALUES(?,?,?,?)",
                       (uid, f"{uid}@invalid", "unused", db.now()))
        self.thread = chats.create_thread(1, model="glm-5.3", tools=[])

    def start(self, text, thread=None):
        thread = chats.get_thread(1, (thread or self.thread)["id"])
        return store.start_turn(1, thread["id"], text, thread["revision"], None)["run"]

    def stored(self, thread=None):
        pk = chats.resolve_thread_pk(1, (thread or self.thread)["id"])
        return db.fetchall("SELECT sha, user_id, url FROM chat_images WHERE thread_id=?", (pk,))

    def test_saves_keep_one_copy_and_every_reader_gets_the_pixels(self):
        run = self.start("inspect screenshot")
        rows = [*run["request"]["messages"], *chat_runtime._tool_messages([big_job()]),
                {"role": "assistant", "content": "read complete"}]
        store.save_turn_progress(run["id"], rows)
        store.save_turn_progress(run["id"], rows)
        raw = db.fetchone("SELECT provider_messages FROM chat_runs WHERE id=?", (run["id"],))[0]
        self.assertNotIn(BIG, raw)
        self.assertEqual(pixels(json.loads(raw)), [REF])
        self.assertEqual([(r["sha"], r["user_id"], r["url"]) for r in self.stored()], [(SHA, 1, URL)])
        self.assertEqual(store.read_file_image(1, run["id"], "event-0"), ("image/png", base64.b64decode(BIG)))
        self.assertIsNone(store.read_file_image(2, run["id"], "event-0"))
        store.finish_run(run["id"], {"content": "read complete", "reasoning": "",
            "tools": [{"id": "event-0", "name": "read_file", "status": "done", **big_job().trace}]}, "completed")
        retained = db.fetchone("SELECT retained_history FROM chat_threads WHERE id=?",
                               (chats.resolve_thread_pk(1, self.thread["id"]),))[0]
        self.assertNotIn(BIG, retained)
        replies = [m for m in json.loads(retained)["messages"] if m.get("content") == "read complete"]
        self.assertEqual(len(replies), 1)  # the finalize boundary matched the stored rows
        next_run = self.start("inspect again")
        self.assertEqual(next_run["request"]["history_info"]["mode"], "full")
        replay = next_run["request"]["messages"]
        self.assertNotIn(BIG, json.dumps(next_run["request"]))
        self.assertEqual(pixels(replay), [REF])
        vision, omitted = model_caps.prepare(chat_images.rehydrate(1, replay), "claude", "claude-sonnet-5")
        self.assertEqual(omitted, 0)
        self.assertEqual(pixels(vision), pixels(rows))
        self.assertIn(chat_images.MISSING, json.dumps(chat_images.rehydrate(2, replay)))  # never another user's

    def test_fork_keeps_its_images_after_the_source_is_deleted(self):
        run = self.start("inspect screenshot")
        rows = [*run["request"]["messages"], *chat_runtime._tool_messages([big_job()]),
                {"role": "assistant", "content": "read complete"}]
        store.save_turn_progress(run["id"], rows)
        store.finish_run(run["id"], {"content": "read complete", "reasoning": ""}, "completed")
        fork = chats.fork_thread(1, self.thread["id"])
        self.assertEqual(len(self.stored(fork)), 1)
        self.assertTrue(chats.delete_thread(1, self.thread["id"]))
        self.assertEqual(db.fetchall("SELECT sha FROM chat_images WHERE thread_id NOT IN (SELECT id FROM chat_threads)"), [])
        chat_images._cache.clear()
        replay = self.start("inspect the fork", thread=fork)["request"]["messages"]
        self.assertEqual(pixels(chat_images.rehydrate(1, replay)), [URL])

    def test_meter_writes_do_not_read_the_retained_history(self):
        run = self.start("inspect screenshot")
        statements = []
        self.conn.set_trace_callback(statements.append)
        self.addCleanup(self.conn.set_trace_callback, None)
        store.save_meter(1, self.thread["id"], {"scope": "prompt", "run_id": run["id"], "model": "glm-5.3",
                                                "used": 10, "limit": 100})
        reads = [s for s in statements if re.match(r"\s*SELECT", s) and "chat_threads" in s]
        self.assertTrue(reads)
        self.assertFalse([s for s in reads if re.search(r"SELECT \* FROM chat_threads|retained_history", s)])

    def test_startup_recovery_reads_ids_before_any_transcript(self):
        done = self.start("old question")
        store.finish_run(done["id"], {"content": "x", "reasoning": ""}, "completed")
        run = self.start("inspect screenshot")
        store.save_turn_progress(run["id"], [*run["request"]["messages"], *chat_runtime._tool_messages([big_job()])])
        db.execute("UPDATE chat_runs SET status='running' WHERE id=?", (run["id"],))
        scans = []
        real = db.fetchall

        def spy(sql, args=()):
            scans.append(sql)
            return real(sql, args)

        with patch.object(db, "fetchall", spy):
            store.recover_runs()
        status_scans = [s for s in scans if "status IN" in s]
        self.assertTrue(status_scans)
        self.assertTrue(all(s.startswith("SELECT id FROM chat_runs") for s in status_scans))
        resumed = db.fetchone("SELECT status, resume_count FROM chat_runs WHERE id=?", (run["id"],))
        self.assertEqual((resumed["status"], resumed["resume_count"]), ("queued", 1))
        self.assertEqual(db.fetchone("SELECT status FROM chat_runs WHERE id=?", (done["id"],))[0], "completed")


@pytest.mark.skipif(__import__("os").environ.get("FUSE_CHAT_TEST_MODE") != "1", reason="requires disposable MariaDB fixture")
def test_mariadb_store_backfill_cascade_and_recycle():
    from scripts import dehydrate_chat_images as backfill
    from app.config import settings
    from tests.mariadb_fixture import copy_foreign_keys, isolated_database

    with isolated_database(pool_size=3):
        copy_foreign_keys("chat_images")
        store.invalidate_committed_cursors()
        chat_images._cache.clear()
        db.execute("INSERT INTO users(id,email,password_hash,created_at) VALUES(1,'images@test.invalid','unused',?)", (db.now(),))
        thread = chats.create_thread(1, model="offline")
        pk = chats.resolve_thread_pk(1, thread["id"])
        run = store.start_turn(1, thread["id"], "inspect", thread["revision"], None)["run"]
        rows = [*run["request"]["messages"], *chat_runtime._tool_messages([big_job()]),
                {"role": "assistant", "content": "read complete"}]
        store.save_turn_progress(run["id"], rows)
        assert pixels(json.loads(db.fetchone("SELECT provider_messages FROM chat_runs WHERE id=?", (run["id"],))[0])) == [REF]
        assert store.read_file_image(1, run["id"], "event-0") == ("image/png", base64.b64decode(BIG))
        store.finish_run(run["id"], {"content": "read complete", "reasoning": ""}, "completed")

        # A row written before references: inline in the run and the history.
        legacy = json.dumps(rows)
        db.execute(f"UPDATE chat_runs SET {db.set_provider_sql()} WHERE id=?", (legacy, run["id"]))
        db.execute("DELETE FROM chat_images WHERE thread_id=?", (pk,))
        retained = json.loads(db.fetchone("SELECT retained_history FROM chat_threads WHERE id=?", (pk,))[0])
        retained["messages"] = rows
        db.execute("UPDATE chat_threads SET retained_history=? WHERE id=?", (json.dumps(retained), pk))
        conn = mysql_compat.connect(settings.database_url, max_connections=1)
        try:
            totals = {"runs": 0, "threads": 0, "images": 0, "before": 0, "after": 0, "skipped": 0}
            backfill._runs(conn, False, 0, totals)
            assert totals["runs"] == 1 and BIG in db.fetchone("SELECT provider_messages FROM chat_runs WHERE id=?", (run["id"],))[0]
            backfill._runs(conn, True, 0, totals)
            backfill._threads(conn, True, 0, totals)
        finally:
            conn.close()
        assert BIG not in db.fetchone("SELECT provider_messages FROM chat_runs WHERE id=?", (run["id"],))[0]
        assert BIG not in db.fetchone("SELECT retained_history FROM chat_threads WHERE id=?", (pk,))[0]
        assert [r["sha"] for r in db.fetchall("SELECT sha FROM chat_images WHERE thread_id=?", (pk,))] == [SHA]
        chat_images._cache.clear()
        assert store.read_file_image(1, run["id"], "event-0") == ("image/png", base64.b64decode(BIG))

        pool = db.connect()
        first = pool.checkout()
        session = first.execute("SELECT CONNECTION_ID() AS id").fetchone()["id"]
        first.execute("SELECT LENGTH(?) AS n", ("x" * mysql_compat._RECYCLE_STATEMENT_CHARS,))
        first.commit()
        pool.release(first)
        held = []
        while not held or held[-1] is not first:
            held.append(pool.checkout())
        assert first.execute("SELECT CONNECTION_ID() AS id").fetchone()["id"] != session
        first.commit()
        for conn in held:
            pool.release(conn)

        assert chats.delete_thread(1, thread["id"])
        assert db.fetchall("SELECT sha FROM chat_images WHERE thread_id=?", (pk,)) == []


class FakeCursor:
    def __init__(self, raw):
        self.raw, self.description, self.rowcount, self.lastrowid = raw, None, 1, 0

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, args=None):
        if not self.raw.open:
            raise pymysql.err.InterfaceError(0, "")
        self.raw.statements.append(sql)

    def fetchall(self):
        return []


class FakeRaw:
    def __init__(self):
        self.open, self.encoders, self.statements, self.connects = True, {}, [], 1

    def cursor(self):
        return FakeCursor(self)

    def ping(self, reconnect=True):
        if not self.open and reconnect:
            self.open, self.connects = True, self.connects + 1

    def close(self):
        if not self.open:
            raise pymysql.err.Error("Already closed")
        self.open = False

    def commit(self):
        pass

    def rollback(self):
        pass


def test_pool_reconnects_a_connection_that_sent_a_huge_statement(monkeypatch):
    raw = FakeRaw()
    monkeypatch.setattr(mysql_compat, "_raw_connect", lambda parsed: raw)
    pool = mysql_compat.Pool(urlsplit("mysql://u:p@127.0.0.1:3306/x"), 1)
    conn = pool.checkout()
    conn.execute("UPDATE chat_runs SET message=? WHERE id=?", ("small", "r"))
    pool.release(conn)
    assert raw.open and not conn.oversized
    conn = pool.checkout()
    conn.execute("UPDATE chat_runs SET provider_messages=? WHERE id=?", ("x" * mysql_compat._RECYCLE_STATEMENT_CHARS, "r"))
    assert conn.oversized
    pool.release(conn)
    assert not raw.open  # MariaDB frees the grown packet buffer with the session
    conn = pool.checkout()
    assert raw.open and raw.connects == 2 and not conn.oversized
    conn.close()
    conn.close()  # already closed (e.g. by a failed rollback): no "Already closed" on shutdown
    pool.close()
