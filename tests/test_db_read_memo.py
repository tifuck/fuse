"""Request-scoped read memo (db.read_memo) and the pool's ping window."""
import contextvars
import time
from unittest.mock import patch

import pytest

from app import db, mysql_compat

pytestmark = pytest.mark.usefixtures("isolated_db")

SQL = "SELECT config FROM sources WHERE id=?"


@pytest.fixture
def source():
    db.execute("DELETE FROM sources WHERE type='memo-test'")
    with db.tx() as conn:
        conn.execute("INSERT INTO sources (type, name, enabled, config, created_at) VALUES (?,?,?,?,?)",
                     ("memo-test", "Memo", 1, '{"v": 1}', db.now()))
        sid = db.last_id(conn)
    yield sid
    db.execute("DELETE FROM sources WHERE id=?", (sid,))


def test_reads_repeat_from_the_memo_until_this_context_writes(source):
    with patch.object(db, "fetchone", wraps=db.fetchone) as read:
        with db.read_memo():
            first = db.fetchone_memo(SQL, (source,))
            first["config"] = "mutated by a caller"
            assert db.fetchone_memo(SQL, (source,))["config"] == '{"v": 1}'
            assert read.call_count == 1
            db.execute("UPDATE sources SET config=? WHERE id=?", ('{"v": 2}', source))
            assert db.fetchone_memo(SQL, (source,))["config"] == '{"v": 2}'
            assert read.call_count == 2
            # A plain read is not a write: the memo stays.
            db.fetchone("SELECT 1")
            db.fetchone_memo(SQL, (source,))
            assert read.call_count == 3
        # Outside a memo every call reads.
        db.fetchone_memo(SQL, (source,))
        db.fetchone_memo(SQL, (source,))
        assert read.call_count == 5


def test_missing_rows_and_lists_are_handled(source):
    with db.read_memo():
        assert db.fetchone_memo(SQL, (source + 1000,)) is None
        db.execute("INSERT INTO sources (id, type, name, enabled, config, created_at) VALUES (?,?,?,?,?,?)",
                   (source + 1000, "memo-test", "Late", 1, "{}", db.now()))
        assert db.fetchone_memo(SQL, (source + 1000,)) is not None
        rows = db.fetchall_memo("SELECT id FROM sources WHERE type=? ORDER BY id", ("memo-test",))
        rows.append("caller junk")
        assert len(db.fetchall_memo("SELECT id FROM sources WHERE type=? ORDER BY id", ("memo-test",))) == 2
    db.execute("DELETE FROM sources WHERE id=?", (source + 1000,))


def test_memo_closes_with_its_block_and_lapses(source):
    with db.read_memo():
        db.fetchone_memo(SQL, (source,))
        spawned = contextvars.copy_context()  # what a task started here inherits
    db.execute("UPDATE sources SET config=? WHERE id=?", ('{"v": 3}', source))
    assert spawned.run(db.fetchone_memo, SQL, (source,))["config"] == '{"v": 3}'
    with db.read_memo(ttl=0.05):
        db.fetchone_memo(SQL, (source,))
        db.execute("UPDATE sources SET config=? WHERE id=?", ('{"v": 4}', source))
        db.fetchone_memo(SQL, (source,))
        # Another request's write: this one keeps its row until the memo lapses.
        contextvars.Context().run(db.execute, "UPDATE sources SET config=? WHERE id=?", ('{"v": 5}', source))
        assert db.fetchone_memo(SQL, (source,))["config"] == '{"v": 4}'
        time.sleep(0.06)
        assert db.fetchone_memo(SQL, (source,))["config"] == '{"v": 5}'


def test_a_recently_released_connection_skips_the_ping():
    pool = db.connect()
    conn = pool.checkout()
    pool.release(conn)
    with patch.object(mysql_compat.Conn, "ensure_connected") as ping:
        again = pool.checkout()
        pool.release(again)
        assert ping.call_count == 0
        for held in pool._all:
            held.released_at = time.monotonic() - mysql_compat.PING_AFTER
        again = pool.checkout()
        pool.release(again)
        assert ping.call_count == 1
