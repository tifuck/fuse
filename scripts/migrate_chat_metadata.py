"""Add completion-recap jobs and title leases to a selected MariaDB schema.

Run while fuse-dev is stopped: ./venv/bin/python -m scripts.migrate_chat_metadata
Production requires an explicit --database fuse_live --allow-production deploy.
No application pool, transcript backfill, or credential writes are performed.
"""
from __future__ import annotations

import argparse
from urllib.parse import urlsplit

import pymysql

from app.config import settings
from app.mysql_compat import Conn


def migrate(conn):
    schema = conn.native("SELECT DATABASE()").fetchone()[0]
    if schema not in ("fuse_dev", "fuse_live") and not schema.startswith("fuse_fixture_"):
        raise RuntimeError("Refusing unexpected schema")
    for table, name, spec in (
        ("users", "chat_recaps", "BIGINT NOT NULL DEFAULT 1"),
        ("chat_threads", "recap_epoch", "BIGINT NOT NULL DEFAULT 0"),
        ("chat_threads", "recap_start_id", "VARCHAR(128) NOT NULL DEFAULT ''"),
        ("chat_threads", "title_job_token", "VARCHAR(64) NOT NULL DEFAULT ''"),
        ("chat_threads", "title_job_until", "VARCHAR(32) NOT NULL DEFAULT ''"),
    ):
        conn.native(f"ALTER TABLE `{table}` ADD COLUMN IF NOT EXISTS `{name}` {spec}")
    conn.native("""CREATE TABLE IF NOT EXISTS chat_recaps (
        id BIGINT PRIMARY KEY AUTO_INCREMENT,
        user_id BIGINT NOT NULL,
        thread_id BIGINT NOT NULL,
        epoch BIGINT NOT NULL,
        episode_id VARCHAR(128) COLLATE utf8mb4_nopad_bin NOT NULL DEFAULT '',
        anchor_id VARCHAR(128) COLLATE utf8mb4_nopad_bin NOT NULL DEFAULT '',
        fingerprint VARCHAR(64) NOT NULL DEFAULT '',
        text VARCHAR(400) NOT NULL DEFAULT '',
        state VARCHAR(32) NOT NULL DEFAULT 'pending',
        manual BIGINT NOT NULL DEFAULT 0,
        attempts BIGINT NOT NULL DEFAULT 0,
        lease_token VARCHAR(64) NOT NULL DEFAULT '',
        next_at VARCHAR(32) NOT NULL DEFAULT '',
        quiet_since VARCHAR(32) NOT NULL DEFAULT '',
        model VARCHAR(200) NOT NULL DEFAULT '',
        updated_at VARCHAR(32) NOT NULL,
        UNIQUE KEY chat_recaps_generation(thread_id,epoch),
        KEY chat_recaps_pending(state,next_at),
        KEY chat_recaps_user(user_id,thread_id),
        FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
        FOREIGN KEY (thread_id) REFERENCES chat_threads(id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_nopad_bin""")
    # CREATE TABLE LIKE (used for isolated fixtures) omits foreign keys.
    for column, parent in (("user_id", "users"), ("thread_id", "chat_threads")):
        exists = conn.native("""SELECT 1 FROM information_schema.key_column_usage WHERE table_schema=DATABASE()
                                AND table_name='chat_recaps' AND column_name=%s AND referenced_table_name=%s""",
                             (column, parent)).fetchone()
        if not exists:
            conn.native(f"ALTER TABLE chat_recaps ADD FOREIGN KEY (`{column}`) REFERENCES `{parent}`(id) ON DELETE CASCADE")
    conn.native("INSERT IGNORE INTO schema_meta(`key`,value) VALUES('chat_metadata_v1','installed')")
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
        print(f"Chat metadata schema installed in {args.database}")
    finally:
        conn.close()
