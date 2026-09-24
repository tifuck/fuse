"""Storage selection must never silently create a local database file."""
import unittest
from unittest.mock import patch

from app import db


class MariaDBOnlyTests(unittest.TestCase):
    def test_disabled_backend_fails_before_connecting(self):
        with patch.object(db, "_conn", None), patch.object(db.settings, "database_mariadb_enabled", False):
            with self.assertRaisesRegex(RuntimeError, "requires MariaDB"):
                db.connect()

    def test_missing_url_fails_before_connecting(self):
        with patch.object(db, "_conn", None), patch.object(db.settings, "database_mariadb_enabled", True), patch.object(db.settings, "database_url", ""):
            with self.assertRaisesRegex(RuntimeError, "requires MariaDB"):
                db.connect()
