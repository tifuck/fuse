"""Create Fuse's tables in an empty MariaDB schema (fresh install).

Usage: python -m scripts.init_db        (DATABASE_URL from .env or the environment)

Idempotent: an initialized schema is left alone. A schema holding any other
tables, including a half-finished load, is refused rather than touched. The
version marker is written last, so a load that dies midway never looks done.
The new schema owns its pooled OAuth refresh tokens (schema_meta
oauth_token_owner); see app/sources/token_store.claim_refresh_ownership.
"""
import json
import sys
from pathlib import Path
from urllib.parse import urlsplit

from app.config import settings
from app.mysql_compat import SCHEMA_VERSION, _raw_connect

SCHEMA_SQL = Path(__file__).resolve().parent / "sql" / "schema.sql"


def statements(text):
    body = "\n".join(line for line in text.splitlines() if not line.startswith("--"))
    return [s.strip().rstrip(";") for s in body.split(";\n") if s.strip()]


def init(url):
    """Return "created" or "exists"; raise RuntimeError for a foreign schema."""
    conn = _raw_connect(urlsplit(url))
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema=DATABASE()")
            tables = {row[0] for row in cur.fetchall()}
            if tables:
                if "schema_meta" in tables:
                    cur.execute("SELECT 1 FROM schema_meta WHERE `key`=%s", (SCHEMA_VERSION,))
                    if cur.fetchone():
                        return "exists"
                raise RuntimeError(
                    f"database already holds {len(tables)} tables but is not an initialized Fuse schema; "
                    "point DATABASE_URL at an empty database (drop a half-finished load first)"
                )
            sql = statements(SCHEMA_SQL.read_text(encoding="utf-8"))
            for stmt in sql:
                cur.execute(stmt)
            created = sum(stmt.startswith("CREATE TABLE") for stmt in sql)
            cur.execute(
                "INSERT INTO schema_meta(`key`, value) VALUES (%s, %s), ('oauth_token_owner', DATABASE())",
                (SCHEMA_VERSION, json.dumps({"tables": created, "fresh": True})),
            )
        conn.commit()
        return "created"
    finally:
        conn.close()


def main():
    url = settings.database_url.strip()
    if not url.startswith("mysql://"):
        sys.exit("DATABASE_URL must be mysql://user:password@host:port/database")
    try:
        result = init(url)
    except RuntimeError as exc:
        sys.exit(f"init_db: {exc}")
    schema = urlsplit(url).path.strip("/")
    print(f"init_db: schema {schema!r} " + ("created" if result == "created" else "already initialized"))


if __name__ == "__main__":
    main()
