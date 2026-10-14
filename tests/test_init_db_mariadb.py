"""scripts/init_db.py builds a bootable schema from scripts/sql/schema.sql.

A schema change applied to fuse_dev without `scripts/dump_schema.sh` fails
test_fresh_schema_matches_source: fresh installs would get the old tables.
"""
import os
import re
from contextlib import contextmanager
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import pytest

from app import accounts, db
from app.config import settings
from app.sources import token_store
from scripts import init_db
from tests.mariadb_fixture import _admin_connect

pytestmark = pytest.mark.skipif(os.environ.get("FUSE_CHAT_TEST_MODE") != "1", reason="requires disposable MariaDB fixture")


@contextmanager
def empty_schema():
    url = urlsplit(settings.database_url)
    source = os.environ.get("FUSE_FIXTURE_SCHEMA_SOURCE") or url.path.strip("/")
    name = "fuse_fixture_" + uuid4().hex
    admin = _admin_connect(source)
    try:
        with admin.cursor() as cur:
            cur.execute(f"CREATE DATABASE `{name}`")
        yield urlunsplit(url._replace(path="/" + name)), name, source, admin
    finally:
        with admin.cursor() as cur:
            cur.execute(f"DROP DATABASE IF EXISTS `{name}`")
        admin.close()


def table_definitions(admin, schema):
    with admin.cursor() as cur:
        cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema=%s", (schema,))
        names = sorted(row[0] for row in cur.fetchall())
        out = {}
        for name in names:
            cur.execute(f"SHOW CREATE TABLE `{schema}`.`{name}`")
            out[name] = re.sub(r" AUTO_INCREMENT=\d+", "", cur.fetchone()[1])
    return out


def test_fresh_schema_matches_source_and_boots():
    with empty_schema() as (url, name, source, admin):
        assert init_db.init(url) == "created"
        assert table_definitions(admin, name) == table_definitions(admin, source), (
            "scripts/sql/schema.sql is stale; run scripts/dump_schema.sh"
        )
        assert init_db.init(url) == "exists"

        saved = settings.database_url, settings.database_mariadb_enabled
        assert db._conn is None
        settings.database_url, settings.database_mariadb_enabled = url, True
        try:
            db.connect()
            assert token_store.claim_refresh_ownership() is True
            accounts.validate_credential_encryption()
            user = accounts.create_user("first@fresh.test", "correct horse battery")
            assert user["is_admin"]
        finally:
            db.close()
            settings.database_url, settings.database_mariadb_enabled = saved


def test_refuses_a_schema_with_foreign_tables():
    with empty_schema() as (url, name, _source, admin):
        with admin.cursor() as cur:
            cur.execute(f"CREATE TABLE `{name}`.`other` (id INT PRIMARY KEY)")
        with pytest.raises(RuntimeError, match="not an initialized Fuse schema"):
            init_db.init(url)
        assert table_definitions(admin, name).keys() == {"other"}
