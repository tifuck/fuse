"""MariaDB adapter for the existing repository interface.

SQL is parsed, not rewritten inside literals. Schema installation is an explicit
offline operation; runtime schema probes may only verify existing objects.
"""
from __future__ import annotations

import re
from .database_errors import IntegrityError, TransactionAborted
import threading
import time
from collections import deque
from functools import lru_cache
from urllib.parse import unquote, urlsplit

import pymysql
import sqlglot
from pymysql.constants import CLIENT
from sqlglot import exp

SCHEMA_VERSION = "fuse_mariadb_v1"


class Row(dict):
    def __getitem__(self, key):
        return list(self.values())[key] if isinstance(key, int) else super().__getitem__(key)


class Cursor:
    def __init__(self, rows=(), rowcount=0, lastrowid=0):
        self._rows = deque(rows)
        self.rowcount = rowcount
        self.lastrowid = lastrowid

    def fetchone(self):
        return self._rows.popleft() if self._rows else None

    def fetchall(self):
        rows = list(self._rows)
        self._rows.clear()
        return rows

    def __iter__(self):
        while self._rows:
            yield self._rows.popleft()


def _node(node):
    if isinstance(node, exp.Is) and isinstance(node.expression, exp.Placeholder):
        return exp.NullSafeEQ(this=node.this, expression=exp.Var(this="__fuse_parameter__"))
    if isinstance(node, exp.Div) and isinstance(node.this, exp.Cast):
        # SQLite integer division truncates; sqlglot's IntDiv renders as
        # CAST(a / b AS SIGNED), which MariaDB rounds.
        quotient = exp.Div(this=node.this.transform(_node), expression=node.expression.transform(_node))
        return exp.cast(exp.Floor(this=quotient), "BIGINT")
    if isinstance(node, exp.Placeholder):
        return exp.Var(this="__fuse_parameter__")
    if isinstance(node, exp.Column) and node.table.lower() == "excluded":
        return exp.Anonymous(this="VALUES", expressions=[exp.column(node.name)])
    if isinstance(node, exp.JSONExtract):
        # SQLite extracts scalars, whereas MariaDB JSON_EXTRACT returns JSON.
        return exp.Anonymous(this="JSON_VALUE", expressions=[node.this, node.expression])
    if isinstance(node, exp.Anonymous) and node.name.lower() == "last_insert_rowid":
        return exp.Anonymous(this="LAST_INSERT_ID")
    if isinstance(node, exp.TimeToStr):
        if node.args.get("format") == exp.Literal.string("%s"):
            value = node.this.this if isinstance(node.this, exp.TsOrDsToTimestamp) else node.this
            return exp.Anonymous(this="UNIX_TIMESTAMP", expressions=[
                exp.Anonymous(this="STR_TO_DATE", expressions=[value, exp.Literal.string("%Y-%m-%dT%H:%i:%sZ")])])
        raise ValueError("Unported SQLite date format")
    return node


@lru_cache(maxsize=4096)
def _rewrite(sql: str, lock_rows: bool = False) -> str:
    if "__fuse_parameter__" in sql:
        # The marker is reserved for placeholder substitution after rendering;
        # a literal collision would silently consume a bound argument and
        # shift the parameter list.
        raise ValueError("SQL contains the reserved parameter marker")
    statement = sqlglot.parse_one(sql, read="sqlite")
    if lock_rows and isinstance(statement, exp.Select) and not statement.args.get("group") and not any(statement.find_all(exp.AggFunc)):
        # Run-state repositories perform read/modify/write operations.
        # MariaDB needs an explicit lock on the run; BEGIN alone does not
        # retain SQLite's writer exclusion. Do not also lock preliminary
        # thread/user lookups: that would invert finalization's run->thread
        # write order and introduce deadlocks with a concurrent turn submit.
        if any(table.name.lower() == "chat_runs" for table in statement.find_all(exp.Table)):
            statement.set("locks", [exp.Lock(update=True)])
    statement = statement.transform(_node)
    conflict = statement.args.get("conflict") if isinstance(statement, exp.Insert) else None
    alternative = statement.args.get("alternative") if isinstance(statement, exp.Insert) else None
    if alternative not in (None, "IGNORE", "REPLACE"):
        raise ValueError(f"Unported insert alternative: {alternative}")
    if isinstance(statement, exp.Insert):
        statement.set("conflict", None)
        statement.set("alternative", None)
        statement.set("ignore", alternative == "IGNORE")
    rendered = statement.sql(dialect="mysql", identify=True, unsupported_level=sqlglot.ErrorLevel.RAISE)
    if rendered.endswith(" FOR SHARE"):
        # sqlglot renders a shared lock in MySQL 8 syntax; MariaDB rejects it.
        rendered = rendered[: -len(" FOR SHARE")] + " LOCK IN SHARE MODE"
    if alternative == "REPLACE":
        rendered = "REPLACE" + rendered[len("INSERT"):]
    if conflict:
        if str(conflict.args.get("action")).upper() != "DO UPDATE":
            raise ValueError("Unported conflict action")
        # MariaDB cannot qualify the left side of assignments; nor should
        # current-row references in an upsert use SQLite's table qualification.
        assignments = []
        for assignment in conflict.expressions:
            assignment = assignment.copy()
            if conflict.args.get("where"):
                predicate = conflict.args["where"].this.copy()
                assignment.set("expression", exp.If(this=predicate, true=assignment.expression, false=assignment.this.copy()))
            for column in assignment.find_all(exp.Column):
                column.set("table", None)
            assignments.append(assignment.sql(dialect="mysql", identify=True))
        rendered += " ON DUPLICATE KEY UPDATE " + ", ".join(assignments)
    if isinstance(statement, exp.Insert) and statement.args.get("default"):
        rendered = rendered.replace("DEFAULT VALUES", "() VALUES ()")
    # Escape literal percentages for PyMySQL, then restore bound parameters.
    return rendered.replace("%", "%%").replace("__fuse_parameter__", "%s")


# PyMySQL escapes str parameters in pure Python (~14 ms/MB, holding the GIL);
# checkpoints bind megabyte transcripts several times a second per run. Large
# strings are sent as introducer hex literals instead: encoded in C, and
# _utf8mb4 X'..' keeps a plain literal's coercibility, so comparisons and
# collation are unchanged. Hex doubles the bytes, so a statement that would
# approach max_allowed_packet (16 MiB) keeps ordinary escaping.
_HEX_MIN_CHARS = 65536
_HEX_STATEMENT_MAX = 12 * 1024 * 1024
# MariaDB grows a connection's packet buffer to the largest statement it
# receives and keeps it until the connection closes, so a pool that once sent
# a 30 MB transcript holds that much per connection for good. The pool
# reconnects a connection that sent a statement this large instead.
_RECYCLE_STATEMENT_CHARS = 4 * 1024 * 1024


class _Utf8Hex:
    __slots__ = ("data",)

    def __init__(self, data):
        self.data = data


def _escape_hex(value, mapping=None):
    return "_utf8mb4 X'" + value.data.hex() + "'"


def _hex_args(sql, args):
    if not isinstance(args, (list, tuple)):
        return args
    if not any(type(arg) is str and len(arg) >= _HEX_MIN_CHARS for arg in args):
        return args
    # Same encoding PyMySQL applies to the rendered query.
    out = [_Utf8Hex(arg.encode("utf-8", "surrogateescape")) if type(arg) is str and len(arg) >= _HEX_MIN_CHARS else arg
           for arg in args]
    size = len(sql) + sum(2 * len(arg.data) + 12 if isinstance(arg, _Utf8Hex) else len(arg) if type(arg) is str else 24
                          for arg in out)
    return args if size > _HEX_STATEMENT_MAX else out


_CONTROL_STATEMENT_RE = re.compile(r"(?:BEGIN|COMMIT|ROLLBACK|PRAGMA|CREATE)\b", re.I)
_READ_STATEMENT_RE = re.compile(r"\s*(?:SELECT|SHOW)\b", re.I)


def _pymysql_errno(exc: Exception) -> int | None:
    code = exc.args[0] if exc.args else None
    return code if isinstance(code, int) else None


class Conn:
    def __init__(self, raw):
        self._raw = raw
        self._write_transaction = False
        # A deadlock rolls back the whole transaction and later statements
        # start a new one; a lock wait timeout rolls back one statement. Code
        # that catches the error and carries on would commit a fragment, so
        # the transaction refuses further statements and its commit.
        self._aborted = None
        # Sent a statement past _RECYCLE_STATEMENT_CHARS: Pool.release reconnects.
        self.oversized = False
        # Ran anything but a read since db.tx() reset it (drops the read memo).
        self.wrote = False
        # When Pool.release took it back healthy; None forces a ping.
        self.released_at = None
        raw.encoders[_Utf8Hex] = _escape_hex

    def _check_open(self):
        if self._aborted is not None:
            raise TransactionAborted("transaction aborted by an earlier database error") from self._aborted

    def _note_size(self, args):
        if not self.oversized and isinstance(args, (list, tuple)) and sum(
                len(arg) for arg in args if isinstance(arg, (str, bytes))) >= _RECYCLE_STATEMENT_CHARS:
            self.oversized = True

    def native(self, sql, args=()):
        self._check_open()
        self._note_size(args)
        if not self.wrote and not _READ_STATEMENT_RE.match(sql):
            self.wrote = True
        try:
            with self._raw.cursor() as cursor:
                cursor.execute(sql, _hex_args(sql, args))
                names = [column[0] for column in cursor.description or ()]
                rows = [Row(zip(names, values)) for values in cursor.fetchall()] if names else []
                return Cursor(rows, cursor.rowcount, cursor.lastrowid or 0)
        except pymysql.IntegrityError as exc:
            # Preserve callers' established duplicate/constraint error contract.
            raise IntegrityError(str(exc), errno=_pymysql_errno(exc)) from exc
        except pymysql.OperationalError as exc:
            self._aborted = exc
            raise

    def execute(self, sql, args=()):
        text = sql.strip().rstrip(";")
        if re.fullmatch(r"BEGIN(?: IMMEDIATE)?", text, re.I):
            # BEGIN implicitly commits whatever is open.
            self._check_open()
            self._raw.begin()
            self._write_transaction = text.upper() == "BEGIN IMMEDIATE"
            return Cursor()
        if text.upper() in ("COMMIT", "ROLLBACK"):
            getattr(self, text.lower())()
            return Cursor()
        if text.upper() == "PRAGMA FOREIGN_KEYS=ON":
            if self.native("SELECT @@foreign_key_checks").fetchone()[0] != 1:
                raise RuntimeError("MariaDB foreign keys must be enabled")
            return Cursor()
        match = re.fullmatch(r"PRAGMA table_info\((\w+)\)", text, re.I)
        if match:
            rows = self.native("SELECT ORDINAL_POSITION, COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, COLUMN_DEFAULT, COLUMN_KEY FROM information_schema.columns WHERE table_schema=DATABASE() AND table_name=%s ORDER BY ORDINAL_POSITION", (match[1],)).fetchall()
            return Cursor([(r[0]-1, r[1], r[2], int(r[3] == "NO"), r[4], int(r[5] == "PRI")) for r in rows])
        match = re.match(r"CREATE TABLE IF NOT EXISTS [\"`]?([\w]+)", text, re.I)
        if match:
            if not self.native("SELECT 1 FROM information_schema.tables WHERE table_schema=DATABASE() AND table_name=%s", (match[1],)).fetchone():
                raise RuntimeError(f"Missing MariaDB table {match[1]}; run schema migration")
            return Cursor()
        match = re.match(r"CREATE (?:UNIQUE )?INDEX IF NOT EXISTS (\w+)\s+ON (\w+)", text, re.I)
        if match:
            if not self.native("SELECT 1 FROM information_schema.statistics WHERE table_schema=DATABASE() AND table_name=%s AND index_name=%s", (match[2], match[1])).fetchone():
                raise RuntimeError(f"Missing MariaDB index {match[1]}; run schema migration")
            return Cursor()
        if "sqlite_master" in text:
            if text == "SELECT 1 FROM sqlite_master WHERE type='table' AND name='command_jobs'":
                return self.native("SELECT 1 FROM information_schema.tables WHERE table_schema=DATABASE() AND table_name='command_jobs'")
            raise ValueError("Unported sqlite_master query")
        return self.native(_rewrite(text, self._write_transaction), args)

    def executemany(self, sql, rows):
        rows = list(rows)
        if not rows:
            return Cursor()
        text = sql.strip().rstrip(";")
        if _CONTROL_STATEMENT_RE.match(text) or "sqlite_master" in text:
            total = 0
            last = 0
            for values in rows:
                result = self.execute(sql, values)
                total += result.rowcount
                last = result.lastrowid
            return Cursor(rowcount=total, lastrowid=last)
        rendered = _rewrite(text, self._write_transaction)
        self._check_open()
        if not _READ_STATEMENT_RE.match(rendered):
            self.wrote = True
        for values in rows:
            self._note_size(values)
        try:
            with self._raw.cursor() as cursor:
                if re.match(r"(?:INSERT|REPLACE)\b", rendered, re.I):
                    # One cursor for the batch; pymysql folds multi-row INSERTs
                    # into chunked statements instead of a round trip per row.
                    cursor.executemany(rendered, [_hex_args(rendered, values) for values in rows])
                    return Cursor(rowcount=cursor.rowcount or 0, lastrowid=cursor.lastrowid or 0)
                total = 0
                last = 0
                for values in rows:
                    cursor.execute(rendered, _hex_args(rendered, values))
                    total += cursor.rowcount or 0
                    last = cursor.lastrowid or 0
                return Cursor(rowcount=total, lastrowid=last)
        except pymysql.IntegrityError as exc:
            raise IntegrityError(str(exc), errno=_pymysql_errno(exc)) from exc
        except pymysql.OperationalError as exc:
            self._aborted = exc
            raise

    def executescript(self, script):
        # Parse statement boundaries, including semicolons inside strings.
        for statement in sqlglot.parse(script, read="sqlite"):
            if statement:
                self.execute(statement.sql(dialect="sqlite"))

    def commit(self):
        try:
            self._check_open()
            self._raw.commit()
        finally:
            self._write_transaction = False

    def ensure_connected(self):
        # Called only before a new transaction. Never replay a partially
        # executed transaction after a transport failure or uncertain commit.
        self._raw.ping(reconnect=True)

    def rollback(self):
        try:
            self._raw.rollback()
        finally:
            self._write_transaction = False
            self._aborted = None

    def close(self):
        # A connection a failed rollback already discarded is closed; closing
        # it again raised and masked the error that brought shutdown on.
        if self._raw.open:
            self._raw.close()
        self.oversized = False


def connect(url, *, max_connections=1):
    parsed = urlsplit(url)
    if parsed.scheme != "mysql" or not parsed.path.strip("/"):
        raise ValueError("Expected mysql://user:password@host/database")
    size = max(1, int(max_connections))
    if size == 1:
        return Conn(_raw_connect(parsed))
    return Pool(parsed, size)


def _raw_connect(parsed):
    return pymysql.connect(host=parsed.hostname or "127.0.0.1", port=parsed.port or 3306,
        user=unquote(parsed.username or ""), password=unquote(parsed.password or ""),
        database=parsed.path.strip("/"), charset="utf8mb4", autocommit=False,
        connect_timeout=5, read_timeout=120, write_timeout=120, client_flag=CLIENT.FOUND_ROWS,
        init_command="SET SESSION time_zone='+00:00', SESSION tx_isolation='REPEATABLE-READ', SESSION sql_mode='STRICT_ALL_TABLES,ERROR_FOR_DIVISION_BY_ZERO,NO_ENGINE_SUBSTITUTION'")


# A connection back from a clean transaction this recently is used without a
# ping (a round trip before every transaction otherwise). A dropped one fails
# its next statement, is closed by the rollback, and reconnects at checkout.
PING_AFTER = 1.0


class Pool:
    """One connection per concurrent transaction. Callers never share one."""

    def __init__(self, parsed, size):
        import queue
        self._parsed = parsed
        self._idle = queue.Queue(size)
        self._all = []
        self._lock = threading.Lock()
        try:
            for _ in range(size):
                conn = Conn(_raw_connect(parsed))
                self._all.append(conn)
                self._idle.put(conn)
        except BaseException:
            self.close()
            raise

    def checkout(self, timeout=30):
        try:
            conn = self._idle.get(timeout=timeout)
        except Exception as exc:
            raise TimeoutError("MariaDB connection pool exhausted") from exc
        released, conn.released_at = conn.released_at, None
        if released is None or time.monotonic() - released >= PING_AFTER or not conn._raw.open:
            try:
                conn.ensure_connected()
            except Exception:
                self._idle.put(conn)
                raise
        return conn

    def release(self, conn):
        if conn.oversized:
            # checkout's ping(reconnect=True) opens a fresh session, without
            # the packet buffer this one grew.
            try:
                conn.close()
            except Exception:
                pass
        elif conn._aborted is None and conn._raw.open:
            conn.released_at = time.monotonic()
        self._idle.put(conn)

    def execute(self, sql, args=()):
        # Schema probes during startup are not part of a caller transaction.
        conn = self.checkout()
        try:
            result = conn.execute(sql, args)
            conn.commit()
            return result
        except Exception:
            conn.rollback()
            raise
        finally:
            self.release(conn)

    def commit(self):
        return None

    def close(self):
        while self._all:
            self._all.pop().close()
