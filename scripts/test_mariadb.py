"""Run existing offline domain/HTTP tests against an isolated MariaDB schema.

Create fuse_test from fuse_stage's schema first. Never targets the live database.
Usage: python -m scripts.test_mariadb tests.test_chat_store tests.test_chat_sync
"""
import importlib
import sys
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit, urlunsplit

from app.config import settings
from app import db
from app.mysql_compat import connect, SCHEMA_VERSION


def main():
    url = urlsplit(settings.database_url)
    settings.database_url = urlunsplit(url._replace(path="/fuse_test"))
    settings.database_mariadb_enabled = True
    settings.database_pool_size = 1  # compatibility fixtures; pooled races have a separate runner
    db._conn = None

    def fixture(*args, **kwargs):
        conn = connect(settings.database_url)
        assert conn.native("SELECT DATABASE()").fetchone()[0] == "fuse_test"
        tables = conn.native("SELECT table_name FROM information_schema.tables WHERE table_schema=DATABASE()").fetchall()
        conn.native("SET FOREIGN_KEY_CHECKS=0")
        for row in tables:
            conn.native(f"DELETE FROM `{row[0]}`")
        conn.native("ALTER TABLE users AUTO_INCREMENT=1")
        conn.native("SET FOREIGN_KEY_CHECKS=1")
        conn.native("INSERT INTO schema_meta (`key`,`value`) VALUES (%s,'1')", (SCHEMA_VERSION,))
        conn.commit()
        return conn

    names = sys.argv[1:] or ["tests.test_chat_store", "tests.test_chat_sync"]
    suite = unittest.TestSuite()
    for name in names:
        module = importlib.import_module(name)
        # This tests SQLite's old in-process schema upgrader. MariaDB installs
        # its schema offline; importer checksum/constraint tests cover that path.
        cls = getattr(module, "ChatStoreTest", None)
        if cls:
            cls.test_migration_assigns_ids_once_and_seeds_sequence = unittest.skip("SQLite-only schema upgrade; MariaDB uses the offline importer")(cls.test_migration_assigns_ids_once_and_seeds_sequence)
        suite.addTests(unittest.defaultTestLoader.loadTestsFromModule(module))
    original_run = unittest.TestCase.run

    def isolated_run(case, result=None):
        db.close()
        db._conn = fixture()
        try:
            type(case).setUpClass()
            return original_run(case, result)
        finally:
            db.close()

    with patch("sqlite3.connect", side_effect=fixture), patch.object(unittest.TestCase, "run", isolated_run):
        result = unittest.TextTestRunner(verbosity=2).run(suite)
    return not result.wasSuccessful()


if __name__ == "__main__":
    raise SystemExit(main())
