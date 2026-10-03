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


import pytest


@pytest.fixture(scope="module")
def isolated_db():
    """One disposable MariaDB schema (fuse_fixture_*) for a whole test module.

    For modules whose tests share state through module or class setup (they
    used to share one temporary SQLite file). Opt in with
    ``pytestmark = pytest.mark.usefixtures("isolated_db")``.
    """
    from app import chat_store, db
    from tests.mariadb_fixture import isolated_database

    db._conn = None  # a module may have reset or replaced it at import time
    with isolated_database() as schema:
        chat_store.invalidate_committed_cursors()
        yield schema
    chat_store.invalidate_committed_cursors()


@pytest.fixture(autouse=True, scope="module")
def _fresh_db_handle():
    """Start and end every module with no open application database.

    Several legacy modules reset or swap ``db._conn`` at import time, which
    happens for all modules during collection; without this, whichever module
    imported last decides what the first test of the next module talks to.
    """
    from app import chat_runs, db

    db._conn = None
    # A module that stops the run supervisor leaves it marked as shutting down.
    chat_runs._stopping = False
    yield
    db._conn = None
    chat_runs._stopping = False
