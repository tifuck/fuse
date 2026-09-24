"""Disposable MariaDB schema for offline integration and browser fixtures."""
from contextlib import contextmanager
from urllib.parse import urlsplit, urlunsplit, unquote
from uuid import uuid4
import re

import pymysql

from app import db
from app.config import settings
from app.mysql_compat import connect


@contextmanager
def isolated_database(pool_size=8):
    url = urlsplit(settings.database_url)
    source = url.path.strip("/")
    if url.scheme != "mysql" or not re.fullmatch(r"[A-Za-z0-9_]+", source):
        raise RuntimeError("A configured MariaDB schema is required for offline fixtures")
    if db._conn is not None:
        raise RuntimeError("Fixture isolation must be established before opening the application database")
    schema = "fuse_fixture_" + uuid4().hex
    admin = pymysql.connect(user="root", unix_socket="/run/mysqld/mysqld.sock", autocommit=True)
    pool = None
    granted = []
    old_url = settings.database_url
    old_enabled = settings.database_mariadb_enabled
    try:
        with admin.cursor() as cursor:
            cursor.execute(f"CREATE DATABASE `{schema}`")
            cursor.execute("SELECT table_name FROM information_schema.tables WHERE table_schema=%s AND table_type='BASE TABLE'", (source,))
            for (table,) in cursor.fetchall():
                if not re.fullmatch(r"[A-Za-z0-9_]+", table):
                    raise RuntimeError("Unexpected schema identifier")
                cursor.execute(f"CREATE TABLE `{schema}`.`{table}` LIKE `{source}`.`{table}`")
            cursor.execute(f"INSERT INTO `{schema}`.schema_meta SELECT * FROM `{source}`.schema_meta")
            for host in ("localhost", "127.0.0.1"):
                cursor.execute(f"GRANT ALL ON `{schema}`.* TO %s@%s", (unquote(url.username), host))
                granted.append(host)
        settings.database_url = urlunsplit(url._replace(path="/" + schema))
        settings.database_mariadb_enabled = True
        pool = connect(settings.database_url, max_connections=pool_size)
        db._conn = pool
        yield schema
    finally:
        db._conn = None
        if pool is not None:
            pool.close()
        settings.database_url = old_url
        settings.database_mariadb_enabled = old_enabled
        try:
            with admin.cursor() as cursor:
                cursor.execute(f"DROP DATABASE IF EXISTS `{schema}`")
                for host in granted:
                    cursor.execute(f"REVOKE ALL ON `{schema}`.* FROM %s@%s", (unquote(url.username), host))
        finally:
            admin.close()
