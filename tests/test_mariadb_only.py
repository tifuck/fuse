"""Storage selection must never silently create a local database file."""
import unittest
from unittest.mock import patch

import pymysql

from app import db
from app.database_errors import IntegrityError
from app.mysql_compat import Conn, _rewrite


class MariaDBOnlyTests(unittest.TestCase):
    def test_disabled_backend_fails_before_connecting(self):
        with patch.object(db, "_conn", None), patch.object(db.settings, "database_mariadb_enabled", False):
            with self.assertRaisesRegex(RuntimeError, "requires MariaDB"):
                db.connect()

    def test_missing_url_fails_before_connecting(self):
        with patch.object(db, "_conn", None), patch.object(db.settings, "database_mariadb_enabled", True), patch.object(db.settings, "database_url", ""):
            with self.assertRaisesRegex(RuntimeError, "requires MariaDB"):
                db.connect()


class IntegrityErrorTests(unittest.TestCase):
    def test_carries_backend_errno(self):
        self.assertIsNone(IntegrityError("boom").errno)
        self.assertEqual(IntegrityError("boom", errno=1062).errno, 1062)


class _StubCursor:
    def __init__(self, log, fail=None):
        self._log = log
        self._fail = fail
        self.rowcount = 0
        self.lastrowid = 0
        self.description = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, args=()):
        if self._fail is not None:
            raise self._fail
        self._log.append(("execute", sql, args))
        self.rowcount = 1
        self.lastrowid = 7

    def executemany(self, sql, rows):
        if self._fail is not None:
            raise self._fail
        self._log.append(("executemany", sql, list(rows)))
        self.rowcount = len(rows)
        self.lastrowid = 7


class _StubRaw:
    def __init__(self, fail=None):
        self.log = []
        self.encoders = {}
        self._fail = fail

    def cursor(self):
        return _StubCursor(self.log, self._fail)

    def commit(self):
        pass

    def rollback(self):
        pass

    def close(self):
        pass


class ExecutemanyTests(unittest.TestCase):
    def test_insert_batch_uses_one_executemany_and_sums_rowcount(self):
        conn = Conn(_StubRaw())
        result = conn.executemany("INSERT INTO t(a) VALUES(?)", [[1], [2], [3]])
        self.assertEqual(result.rowcount, 3)
        self.assertEqual([kind for kind, _sql, _args in conn._raw.log], ["executemany"])

    def test_update_batch_keeps_one_cursor_and_per_row_rowcount(self):
        conn = Conn(_StubRaw())
        result = conn.executemany("UPDATE t SET a=? WHERE id=?", [[1, 1], [2, 2]])
        self.assertEqual(result.rowcount, 2)
        self.assertEqual([kind for kind, _sql, _args in conn._raw.log], ["execute", "execute"])

    def test_empty_batch_is_a_noop(self):
        conn = Conn(_StubRaw())
        result = conn.executemany("INSERT INTO t(a) VALUES(?)", [])
        self.assertEqual(result.rowcount, 0)
        self.assertEqual(conn._raw.log, [])

    def test_integrity_error_carries_errno(self):
        conn = Conn(_StubRaw(fail=pymysql.IntegrityError(1062, "Duplicate entry 'x' for key 'PRIMARY'")))
        with self.assertRaises(IntegrityError) as raised:
            conn.execute("INSERT INTO t(a) VALUES(?)", (1,))
        self.assertEqual(raised.exception.errno, 1062)
        with self.assertRaises(IntegrityError) as raised_many:
            conn.executemany("INSERT INTO t(a) VALUES(?)", [[1], [2]])
        self.assertEqual(raised_many.exception.errno, 1062)


class RewriteMarkerTests(unittest.TestCase):
    def test_rejects_sql_containing_the_parameter_marker(self):
        with self.assertRaises(ValueError):
            _rewrite("SELECT '__fuse_parameter__' AS marker")

    def test_integer_division_floors(self):
        # CAST(a / b AS SIGNED) rounds in MariaDB; SQLite's integer division
        # truncates, so the rewrite must floor before the cast.
        sql = "SELECT (CAST(strftime('%s', e.created_at) AS INTEGER) / ?) * ? AS bucket FROM t e"
        self.assertIn("CAST(FLOOR(", _rewrite(sql))
        heatmap = "SELECT (CAST(strftime('%s', e.created_at) AS INTEGER) / 3600) * 3600 AS hour FROM t e"
        self.assertIn("CAST(FLOOR(", _rewrite(heatmap))


if __name__ == "__main__":
    unittest.main()
