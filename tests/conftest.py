"""Refuse to let any test connect to a real Fuse schema.

Legacy tests still point DATABASE_PATH at a temp SQLite file, but db.connect()
now always uses DATABASE_URL from .env, so without this guard they wipe users
and credentials in fuse_dev and write rows encrypted with a fake APP_SECRET,
which crash-loops fuse-dev at startup. Tests that need a database must use
tests/mariadb_fixture.isolated_database (schema fuse_fixture_*).
"""
import pymysql

_PROTECTED = {"fuse_dev", "fuse_live"}
_real_connect = pymysql.connect


def _guarded_connect(*args, **kwargs):
    schema = kwargs.get("database") or kwargs.get("db")
    if schema in _PROTECTED:
        raise RuntimeError(
            f"tests must not connect to the real {schema!r} schema; "
            "use tests/mariadb_fixture.isolated_database"
        )
    return _real_connect(*args, **kwargs)


pymysql.connect = _guarded_connect
pymysql.Connect = _guarded_connect
