"""Offline SQLite -> MariaDB import with exact row verification.

Usage: python -m scripts.migrate_mariadb SNAPSHOT --database fuse_stage
The target must be empty. Credentials come from DATABASE_URL; never from argv.
This tool is the only production component that reads the legacy SQLite file.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from app.config import settings
from app.mysql_compat import SCHEMA_VERSION, connect


def q(name):
    return "`" + name.replace("`", "``") + "`"


def digest(rows):
    result = hashlib.sha256()
    for row in rows:
        values = [("blob", v.hex()) if isinstance(v, bytes) else (type(v).__name__, v) for v in row]
        result.update(json.dumps(values, ensure_ascii=True, separators=(",", ":")).encode())
        result.update(b"\n")
    return result.hexdigest()


def migrate(source, database):
    if not re.fullmatch(r"[a-zA-Z0-9_]+", database):
        raise ValueError("Invalid target database")
    url = urlsplit(settings.database_url)
    dst = connect(urlunsplit(url._replace(path="/" + database)))
    if dst.native("SELECT COUNT(*) FROM information_schema.tables WHERE table_schema=DATABASE()").fetchone()[0]:
        raise RuntimeError("Target must be empty; refusing to overwrite any tables")
    src = sqlite3.connect(Path(source).resolve().as_uri() + "?mode=ro", uri=True)
    if src.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
        raise RuntimeError("SQLite integrity check failed")
    violations = src.execute("PRAGMA foreign_key_check").fetchall()
    if violations:
        raise RuntimeError(f"Source has {len(violations)} foreign-key violations")
    tables = [r[0] for r in src.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    indexes = []
    foreign_keys = []
    manifests = {}
    for table in tables:
        columns = src.execute(f"PRAGMA table_info({q(table)})").fetchall()
        manifests[table] = columns
        pk = [c[1] for c in sorted(columns, key=lambda c: c[5]) if c[5]]
        defs = []
        for _, name, kind, notnull, default, primary in columns:
            if kind.upper() == "INTEGER":
                sql_type = "BIGINT"
            elif kind.upper() == "REAL":
                sql_type = "DOUBLE"
            elif kind.upper() == "BLOB":
                sql_type = "LONGBLOB"
            elif primary:
                bounds = {"provider": 64, "kind": 64, "model": 512, "session_id": 191, "hour": 32, "command_id": 128}
                sql_type = f"VARCHAR({bounds.get(name, 191)}) COLLATE utf8mb4_nopad_bin"
            else:
                sql_type = "LONGTEXT COLLATE utf8mb4_nopad_bin"
            spec = q(name) + " " + sql_type
            if notnull or primary:
                spec += " NOT NULL"
            if name == "id" and kind.upper() == "INTEGER" and pk == ["id"]:
                spec += " AUTO_INCREMENT"
            if default is not None:
                spec += " DEFAULT " + default
            defs.append(spec)
        if pk:
            defs.append("PRIMARY KEY (" + ",".join(map(q, pk)) + ")")
        dst.native(f"CREATE TABLE {q(table)} (" + ",".join(defs) + ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_nopad_bin ROW_FORMAT=DYNAMIC")
        for index in src.execute(f"PRAGMA index_list({q(table)})"):
            _, name, unique, origin, partial = index[:5]
            if origin == "pk":
                continue
            parts = [r[2] for r in src.execute(f"PRAGMA index_info({q(name)})")]
            if any(part is None for part in parts):
                raise RuntimeError(f"Expression index needs explicit migration: {name}")
            if partial and unique:
                if name != "chat_runs_active_parent":
                    raise RuntimeError(f"Partial unique index needs explicit migration: {name}")
                dst.native("ALTER TABLE chat_runs ADD COLUMN active_parent_thread BIGINT GENERATED ALWAYS AS (CASE WHEN status IN ('queued','running') AND parent_run_id IS NULL THEN thread_id ELSE NULL END) PERSISTENT")
                indexes.append((table, name, True, ["active_parent_thread"]))
            else:
                # A full secondary index is equivalent for lookup correctness.
                indexes.append((table, name, bool(unique), parts))
        for fk in src.execute(f"PRAGMA foreign_key_list({q(table)})"):
            foreign_keys.append((table, *fk))
    for table, name, unique, parts in indexes:
        types = {c[1]: c[2].upper() for c in manifests[table]}
        primary_columns = {c[1] for c in manifests[table] if c[5]}
        names = []
        for part in parts:
            # InnoDB's LONGTEXT unique indexes use full-value hash enforcement,
            # NOT prefix uniqueness. Prefixes are only for nonunique lookups.
            suffix = "(191)" if not unique and types.get(part) == "TEXT" and part not in primary_columns else ""
            names.append(q(part) + suffix)
        dst.native(f"CREATE {'UNIQUE ' if unique else ''}INDEX {q(name)} ON {q(table)} ({','.join(names)})")
    report = {}
    for table in tables:
        columns = manifests[table]
        names = [c[1] for c in columns]
        primary = [c[1] for c in sorted(columns, key=lambda c: c[5]) if c[5]]
        ordering = ",".join(map(q, primary or names))
        select = f"SELECT {','.join(map(q, names))} FROM {q(table)} ORDER BY {ordering}"
        rows = src.execute(select).fetchall()
        query = f"INSERT INTO {q(table)} ({','.join(map(q, names))}) VALUES ({','.join(['%s'] * len(names))})"
        with dst._raw.cursor() as cursor:
            for start in range(0, len(rows), 200):
                cursor.executemany(query, rows[start:start+200])
        dst.commit()
        copied = [tuple(r.values()) for r in dst.native(select).fetchall()]
        checksum = digest(rows)
        if len(rows) != len(copied) or checksum != digest(copied):
            raise RuntimeError(f"Content verification failed for {table}")
        report[table] = {"rows": len(rows), "sha256": checksum}
        # Preserve sequence high-water marks, including deleted rows and the
        # empty thread-ID allocator. MAX(id) alone would reuse historical IDs.
        if primary == ["id"] and next(c for c in columns if c[1] == "id")[2].upper() == "INTEGER":
            seq = src.execute("SELECT seq FROM sqlite_sequence WHERE name=?", (table,)).fetchone()
            maximum = src.execute(f"SELECT MAX(id) FROM {q(table)}").fetchone()[0] or 0
            floor = max(maximum, seq[0] if seq else 0)
            if table == "chat_thread_ids":
                floor = max(floor, src.execute("SELECT COALESCE(MAX(id),0) FROM chat_threads").fetchone()[0])
            dst.native(f"ALTER TABLE {q(table)} AUTO_INCREMENT={floor + 1}")
        print(f"Verified {table}: {len(rows)} rows", flush=True)
    # Preserve SQLite's implicit command insertion order used on reconnect.
    dst.native("ALTER TABLE chat_commands ADD COLUMN rowid BIGINT NULL")
    with dst._raw.cursor() as cursor:
        cursor.executemany("UPDATE chat_commands SET rowid=%s WHERE user_id=%s AND command_id=%s", src.execute("SELECT rowid,user_id,command_id FROM chat_commands").fetchall())
    dst.commit()
    dst.native("ALTER TABLE chat_commands MODIFY rowid BIGINT NOT NULL AUTO_INCREMENT, ADD UNIQUE INDEX chat_commands_rowid(rowid)")
    # Checkpoint guard (scripts/sql/2026-09-25-chat-runs-request-digest.sql).
    dst.native("ALTER TABLE chat_runs ADD COLUMN request_digest CHAR(32) CHARACTER SET ascii COLLATE ascii_bin GENERATED ALWAYS AS (MD5(request)) STORED")
    # All data is now loaded. Add foreign keys with checks enabled so MariaDB
    # validates every reference rather than silently accepting orphaned rows.
    for table, ident, seq, target, column, refcolumn, on_update, on_delete, match in foreign_keys:
        if seq:
            raise RuntimeError("Composite FK requires explicit migration")
        dst.native(f"ALTER TABLE {q(table)} ADD CONSTRAINT {q('fk_' + table + '_' + str(ident))} FOREIGN KEY ({q(column)}) REFERENCES {q(target)} ({q(refcolumn)}) ON DELETE {on_delete} ON UPDATE {on_update}")
    dst.native("INSERT INTO schema_meta (`key`,`value`) VALUES (%s,%s)", (SCHEMA_VERSION, json.dumps({"tables": len(tables), "verified": True})))
    dst.commit()
    dst.close()
    src.close()
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("snapshot")
    parser.add_argument("--database", required=True)
    args = parser.parse_args()
    report = migrate(args.snapshot, args.database)
    print(json.dumps(report, indent=2))
