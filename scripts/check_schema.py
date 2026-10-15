"""Refuse code whose tables or columns the connected schema lacks.

Usage: python -m scripts.check_schema [schema.sql]   (DATABASE_URL from .env or the environment)

schema.sql (default scripts/sql/schema.sql) is dumped from fuse_dev after every
schema change, so it lists every table and column the code at that commit may
read. Exits 1 and names each missing table or column; extra ones in the
database (a table a pending migration drops) are fine. Run the new commit's
copy against production before deploying it: on MariaDB db._init never adds
columns, they come only from scripts/sql migrations.
"""
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit

SCHEMA_SQL = Path(__file__).resolve().parent / "sql" / "schema.sql"

_TABLE = re.compile(r"^CREATE TABLE `([^`]+)` \($")
_COLUMN = re.compile(r"^\s+`([^`]+)` ")


def expected_columns(text):
    """{table: [column, ...]} from a dump_schema.sh file, in file order."""
    tables, current = {}, None
    for line in text.splitlines():
        match = _TABLE.match(line)
        if match:
            current = tables.setdefault(match.group(1), [])
        elif current is not None and line.startswith(")"):
            current = None
        elif current is not None and (match := _COLUMN.match(line)):
            current.append(match.group(1))
    return tables


def missing(expected, actual):
    """Expected tables absent from actual, then table.column for present tables."""
    gone = [table for table in expected if table not in actual]
    gone += [f"{table}.{column}" for table, columns in expected.items()
             if table in actual for column in columns if column not in actual[table]]
    return gone


def live_columns(url):
    from app.mysql_compat import _raw_connect

    conn = _raw_connect(urlsplit(url))
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT table_name, column_name FROM information_schema.columns "
                        "WHERE table_schema=DATABASE()")
            actual = {}
            for table, column in cur.fetchall():
                actual.setdefault(table, set()).add(column)
            return actual
    finally:
        conn.close()


def main(argv=None):
    from app.config import settings

    args = sys.argv[1:] if argv is None else argv
    path = Path(args[0]) if args else SCHEMA_SQL
    url = settings.database_url.strip()
    if not url.startswith("mysql://"):
        sys.exit("check_schema: DATABASE_URL must be mysql://user:password@host:port/database")
    expected = expected_columns(path.read_text(encoding="utf-8"))
    if not expected:
        sys.exit(f"check_schema: no CREATE TABLE in {path}")
    schema = urlsplit(url).path.strip("/")
    gone = missing(expected, live_columns(url))
    if gone:
        print(f"check_schema: {schema!r} is missing {len(gone)} of what the code expects:", file=sys.stderr)
        for name in gone:
            print(f"  {name}", file=sys.stderr)
        print("Apply the pending migration from scripts/sql/ to it first.", file=sys.stderr)
        return 1
    print(f"check_schema: {schema!r} has all {len(expected)} tables and their columns")
    return 0


if __name__ == "__main__":
    sys.exit(main())
