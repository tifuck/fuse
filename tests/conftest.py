"""Refuse to let any test connect to a real Fuse schema.

Legacy tests still point DATABASE_PATH at a temp SQLite file, but db.connect()
now always uses DATABASE_URL from .env, so without this guard they wipe users
and credentials in fuse_dev and write rows encrypted with a fake APP_SECRET,
which crash-loops fuse-dev. Tests that need a database must use
tests/mariadb_fixture.isolated_database (schema fuse_fixture_*), which runs on
the fixture-limited database account instead of root.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests.mariadb_fixture import install_guard, sweep_stale_schemas

install_guard()

import pytest


def _close_db_handle(db):
    """Drop the application database handle, closing whatever it referenced."""
    conn, db._conn = db._conn, None
    close = getattr(conn, "close", None)
    if close is not None:
        try:
            close()
        except Exception:
            pass


@pytest.fixture(autouse=True, scope="session")
def _sweep_stale_fixture_schemas():
    """Drop fuse_fixture_* schemas left behind by killed runs (best effort)."""
    sweep_stale_schemas()


@pytest.fixture(scope="module")
def isolated_db():
    """One disposable MariaDB schema (fuse_fixture_*) for a whole test module.

    For modules whose tests share state through module or class setup (they
    used to share one temporary SQLite file). Opt in with
    ``pytestmark = pytest.mark.usefixtures("isolated_db")``.
    """
    from app import chat_store, db
    from tests.mariadb_fixture import isolated_database

    _close_db_handle(db)  # a module may have reset or replaced it at import time
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
    from app import chat_runs, chat_titles, db

    _close_db_handle(db)
    # A module that stops the run supervisor leaves it marked as shutting down.
    chat_runs._stopping = False
    # Title queues hold thread ids from the previous module's dropped schema;
    # ids restart at 1 in each fresh schema (see tests/test_chat_titles.py).
    chat_titles._due.clear()
    chat_titles._inflight.clear()
    yield
    _close_db_handle(db)
    chat_runs._stopping = False
    chat_titles._due.clear()
    chat_titles._inflight.clear()
