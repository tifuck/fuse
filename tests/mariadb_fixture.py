"""Disposable MariaDB schema for offline integration and browser fixtures.

Importing this module installs the pymysql guard (``install_guard``): no test
or harness may open a connection whose default schema is one of the real Fuse
schemas (``fuse_dev``/``fuse_live``), including via ``pymysql.Connection``,
``read_default_file`` option files, ``select_db`` or ``USE``. Standalone
harnesses such as tests/chat_browser_fixture.py get the same protection by
importing here.

DDL runs through the fixture-limited database account (default
'fuse_fixture'@'localhost'), never root: it holds ALL on the ``fuse_fixture_%``
schema pattern and SELECT on the clone source schema only. The application
database user holds the same fixture-pattern grant (provisioned once), so the
fixture needs no per-schema GRANT/REVOKE.
"""
from contextlib import contextmanager
from configparser import ConfigParser
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4
import os
import re
import sys
import warnings

import pymysql

PROTECTED_SCHEMAS = {"fuse_dev", "fuse_live"}

_GUARD_MESSAGE = (
    "tests must not connect to the real {schema!r} schema; "
    "use tests/mariadb_fixture.isolated_database"
)
_USE = re.compile(r"^\s*USE\s+[`'\"]?([A-Za-z0-9_]+)", re.I)
_SCHEMA_NAME = re.compile(r"[A-Za-z0-9_]+")
_FIXTURE_SCHEMA = re.compile(r"fuse_fixture_[A-Za-z0-9_]+")


def _as_text(value):
    return value.decode("utf-8", "replace") if isinstance(value, (bytes, bytearray)) else value


def _refuse(schema):
    name = _as_text(schema)
    if name in PROTECTED_SCHEMAS:
        raise RuntimeError(_GUARD_MESSAGE.format(schema=name))


def _option_file_database(kwargs):
    """Database resolved from read_default_file/read_default_group, if any."""
    read_file = kwargs.get("read_default_file")
    group = kwargs.get("read_default_group")
    if not read_file and group:
        read_file = "c:\\my.ini" if sys.platform.startswith("win") else "/etc/my.cnf"
    if not read_file:
        return None
    try:
        cfg = ConfigParser()
        cfg.read(os.path.expanduser(read_file))
        return cfg.get(group or "client", "database")
    except Exception:
        return None


def install_guard():
    """Refuse protected schemas at construction, USE and COM_INIT_DB."""
    conn = pymysql.connections.Connection
    if getattr(conn.__init__, "_fuse_guarded", False):
        return
    original_init = conn.__init__
    original_select_db = conn.select_db
    original_query = conn.query

    def guarded_init(self, *args, **kwargs):
        _refuse(kwargs.get("database") or kwargs.get("db"))
        # Refuse before the socket opens when an option file names the schema.
        _refuse(_option_file_database(kwargs))
        original_init(self, *args, **kwargs)
        try:
            _refuse(self.db)
        except RuntimeError:
            force_close = getattr(self, "_force_close", None)
            if force_close is not None:
                force_close()
            raise

    def guarded_select_db(self, db):
        _refuse(db)
        return original_select_db(self, db)

    def guarded_query(self, sql, *args, **kwargs):
        match = _USE.match(_as_text(sql) or "")
        if match:
            _refuse(match.group(1))
        return original_query(self, sql, *args, **kwargs)

    for wrapped in (guarded_init, guarded_select_db, guarded_query):
        wrapped._fuse_guarded = True
    conn.__init__ = guarded_init
    conn.select_db = guarded_select_db
    conn.query = guarded_query


install_guard()


def _fixture_account():
    return (
        os.environ.get("FUSE_FIXTURE_DB_USER", "fuse_fixture"),
        os.environ.get("FUSE_FIXTURE_DB_PASSWORD", ""),
        os.environ.get("FUSE_FIXTURE_DB_SOCKET", "/run/mysqld/mysqld.sock"),
    )


def _provisioning_sql(source):
    user, _, _ = _fixture_account()
    return (
        f"CREATE USER '{user}'@'localhost' IDENTIFIED BY '<password>';\n"
        f"GRANT ALL ON `fuse\\_fixture\\_%`.* TO '{user}'@'localhost';\n"
        f"GRANT SELECT ON `{source}`.* TO '{user}'@'localhost';\n"
        "GRANT ALL ON `fuse\\_fixture\\_%`.* TO '<app-user>'@'localhost', '<app-user>'@'127.0.0.1';\n"
        "(set FUSE_FIXTURE_DB_PASSWORD to <password>; '' is fine for socket-local test hosts)"
    )


def _admin_connect(source):
    user, password, socket_path = _fixture_account()
    try:
        return pymysql.connect(user=user, password=password, unix_socket=socket_path, autocommit=True)
    except pymysql.MySQLError as exc:
        raise RuntimeError(
            f"fixture database account {user!r} is missing or unusable ({exc}).\n"
            "Tests must not hold root database access; provision the limited account once:\n"
            + _provisioning_sql(source)
        ) from exc


def sweep_stale_schemas(max_age_seconds=3600):
    """Best-effort drop of fuse_fixture_* schemas left behind by killed runs."""
    try:
        admin = _admin_connect("fuse_dev")
        try:
            with admin.cursor() as cursor:
                cursor.execute(
                    "SELECT table_schema FROM information_schema.tables"
                    " WHERE table_schema LIKE 'fuse\\_fixture\\_%'"
                    f" AND create_time < NOW() - INTERVAL {int(max_age_seconds)} SECOND"
                    " GROUP BY table_schema"
                )
                for (name,) in cursor.fetchall():
                    if _FIXTURE_SCHEMA.fullmatch(name):
                        cursor.execute(f"DROP DATABASE IF EXISTS `{name}`")
        finally:
            admin.close()
    except Exception as exc:
        warnings.warn(f"stale fuse_fixture_* schema sweep skipped: {exc}", stacklevel=2)


@contextmanager
def isolated_database(pool_size=8):
    from app import db
    from app.config import settings
    from app.mysql_compat import connect

    url = urlsplit(settings.database_url)
    source = os.environ.get("FUSE_FIXTURE_SCHEMA_SOURCE") or url.path.strip("/")
    if url.scheme != "mysql" or not _SCHEMA_NAME.fullmatch(source):
        raise RuntimeError("A configured MariaDB schema is required for offline fixtures")
    if source == "fuse_live":
        raise RuntimeError(
            "refusing to build fixtures from the production 'fuse_live' schema; "
            "set FUSE_FIXTURE_SCHEMA_SOURCE to a dev/test schema"
        )
    if not url.username:
        raise RuntimeError("DATABASE_URL must include a database user")
    if db._conn is not None:
        raise RuntimeError("Fixture isolation must be established before opening the application database")
    schema = "fuse_fixture_" + uuid4().hex
    admin = _admin_connect(source)
    pool = None
    old_url = settings.database_url
    try:
        with admin.cursor() as cursor:
            cursor.execute(f"CREATE DATABASE `{schema}`")
            cursor.execute("SELECT table_name FROM information_schema.tables WHERE table_schema=%s AND table_type='BASE TABLE'", (source,))
            for (table,) in cursor.fetchall():
                if not _SCHEMA_NAME.fullmatch(table):
                    raise RuntimeError("Unexpected schema identifier")
                cursor.execute(f"CREATE TABLE `{schema}`.`{table}` LIKE `{source}`.`{table}`")
            cursor.execute(f"INSERT INTO `{schema}`.schema_meta SELECT * FROM `{source}`.schema_meta")
        settings.database_url = urlunsplit(url._replace(path="/" + schema))
        pool = connect(settings.database_url, max_connections=pool_size)
        db._conn = pool
        yield schema
    finally:
        db._conn = None
        if pool is not None:
            pool.close()
        settings.database_url = old_url
        try:
            with admin.cursor() as cursor:
                cursor.execute(f"DROP DATABASE IF EXISTS `{schema}`")
        finally:
            admin.close()
