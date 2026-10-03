"""Add project colors/logos/archive, chat archive timestamps and per-user
cleanup settings to a selected MariaDB schema.

Run while fuse-dev is stopped: ./venv/bin/python -m scripts.migrate_projects
Production requires an explicit --database fuse_live --allow-production deploy.
Only adds columns and one table; no rows are rewritten.
"""
from __future__ import annotations

import argparse
from urllib.parse import urlsplit

import pymysql

from app.config import settings
from app.mysql_compat import Conn

COLUMNS = (
    ("user_projects", "color", "VARCHAR(16) NOT NULL DEFAULT ''"),
    ("user_projects", "logo_rev", "BIGINT NOT NULL DEFAULT 0"),
    ("user_projects", "archived", "BIGINT NOT NULL DEFAULT 0"),
    ("user_projects", "archived_at", "VARCHAR(32) NOT NULL DEFAULT ''"),
    ("chat_threads", "archived_at", "VARCHAR(32) NOT NULL DEFAULT ''"),
    ("chat_threads", "active_at", "VARCHAR(32) NOT NULL DEFAULT ''"),
    ("chat_threads", "archived_by_project", "BIGINT NOT NULL DEFAULT 0"),
    ("users", "retention_archive_hours", "BIGINT NOT NULL DEFAULT 0"),
    ("users", "retention_archive_chats", "BIGINT NOT NULL DEFAULT 1"),
    ("users", "retention_archive_projects", "BIGINT NOT NULL DEFAULT 1"),
    ("users", "retention_delete_days", "BIGINT NOT NULL DEFAULT 0"),
)


def migrate(conn):
    schema = conn.native("SELECT DATABASE()").fetchone()[0]
    if schema not in ("fuse_dev", "fuse_live") and not schema.startswith("fuse_fixture_"):
        raise RuntimeError("Refusing unexpected schema")
    for table, name, spec in COLUMNS:
        conn.native(f"ALTER TABLE `{table}` ADD COLUMN IF NOT EXISTS `{name}` {spec}")
    conn.native("""CREATE TABLE IF NOT EXISTS project_logos (
        project_id BIGINT PRIMARY KEY,
        user_id BIGINT NOT NULL,
        mime VARCHAR(32) NOT NULL,
        data MEDIUMBLOB NOT NULL,
        updated_at VARCHAR(32) NOT NULL,
        KEY project_logos_user(user_id),
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
        FOREIGN KEY (project_id) REFERENCES user_projects(id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_nopad_bin""")
    # CREATE TABLE LIKE (used for isolated fixtures) omits foreign keys.
    for column, parent in (("user_id", "users"), ("project_id", "user_projects")):
        exists = conn.native("""SELECT 1 FROM information_schema.key_column_usage WHERE table_schema=DATABASE()
                                AND table_name='project_logos' AND column_name=%s AND referenced_table_name=%s""",
                             (column, parent)).fetchone()
        if not exists:
            conn.native(f"ALTER TABLE project_logos ADD FOREIGN KEY (`{column}`) REFERENCES `{parent}`(id) ON DELETE CASCADE")
    conn.native("CREATE INDEX IF NOT EXISTS chat_threads_user_archived_at ON chat_threads (user_id, archived, archived_at)")
    conn.native("INSERT IGNORE INTO schema_meta(`key`,value) VALUES('projects_v1','installed')")
    conn.commit()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", default=urlsplit(settings.database_url).path.strip("/"))
    parser.add_argument("--allow-production", action="store_true")
    args = parser.parse_args()
    if args.database not in ("fuse_dev", "fuse_live"):
        parser.error("Select fuse_dev or fuse_live")
    if args.database == "fuse_live" and not args.allow_production:
        parser.error("Production migration requires --allow-production")
    conn = Conn(pymysql.connect(user="root", unix_socket="/run/mysqld/mysqld.sock", database=args.database))
    try:
        migrate(conn)
        print(f"Project and retention schema installed in {args.database}")
    finally:
        conn.close()
