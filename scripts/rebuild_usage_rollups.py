"""Rebuild usage rollups from usage_events, their source of truth.

Usage: python -m scripts.rebuild_usage_rollups [--apply]

Run from the tree whose schema you mean (/root/fuse is fuse_live, /root/fuse-dev
is fuse_dev). Without --apply it only reports how many rollup rows disagree
with their events. --apply rewrites credentials, api_keys, provider_usage and
usage_hourly in one transaction; stop the service first, since every request
writes the same rows. Credential week counters (week_tokens/week_requests)
are left alone: they restart each ISO week. Uses one connection, not a pool.
"""
from __future__ import annotations

import argparse

from app import db
from app.config import settings
from app.mysql_compat import connect

COST = "SUM(COALESCE(e.reported_cost_micros, e.estimated_cost_micros))"
CRED_COLS = ("prompt_tokens", "completion_tokens", "output_tokens", "visible_tokens", "reasoning_tokens",
             "cached_tokens", "cache_write_tokens", "total_tokens")
KEY_COLS = CRED_COLS + ("audio_tokens", "image_tokens")
PROVIDER = "COALESCE(NULLIF(e.source_type, ''), s.type, 'unknown')"


def _sums(cols):
    return ", ".join(f"COALESCE(SUM(e.{col}), 0) AS {col}" for col in cols) + \
        f", COALESCE(SUM(e.count_request), 0) AS requests, COALESCE({COST}, 0) AS cost_micros"


def _all(cols):
    return (*cols, "requests", "cost_micros")


def _differs(cols, left, right):
    return " OR ".join(f"{left}.{col} <> COALESCE({right}.{col}, 0)" for col in _all(cols))


CRED_AGG = f"SELECT e.credential_id AS id, {_sums(CRED_COLS)} FROM usage_events e WHERE e.credential_id IS NOT NULL GROUP BY e.credential_id"
KEY_AGG = f"SELECT e.api_key_id AS id, {_sums(KEY_COLS)} FROM usage_events e WHERE e.api_key_id IS NOT NULL GROUP BY e.api_key_id"
PROVIDER_AGG = (f"SELECT e.user_id, {PROVIDER} AS provider, {_sums(KEY_COLS)} "
                f"FROM usage_events e LEFT JOIN sources s ON s.id=e.source_id GROUP BY e.user_id, {PROVIDER}")
HOUR = "CONCAT(LEFT(e.created_at, 13), ':00:00Z')"
HOURLY_AGG = (f"SELECT e.user_id, {HOUR} AS hour, COALESCE(e.source_id, 0) AS source_id, COALESCE(e.model, '') AS model, "
              f"{_sums(CRED_COLS)} FROM usage_events e GROUP BY 1, 2, 3, 4")

REPORT = {
    "credentials": f"SELECT COUNT(*) FROM credentials c LEFT JOIN ({CRED_AGG}) a ON a.id=c.id WHERE {_differs(CRED_COLS, 'c', 'a')}",
    "api_keys": f"SELECT COUNT(*) FROM api_keys k LEFT JOIN ({KEY_AGG}) a ON a.id=k.id WHERE {_differs(KEY_COLS, 'k', 'a')}",
    "provider_usage": (f"SELECT COUNT(*) FROM provider_usage p LEFT JOIN ({PROVIDER_AGG}) a "
                       f"ON a.user_id=p.user_id AND a.provider=p.provider WHERE {_differs(KEY_COLS, 'p', 'a')}"),
    "provider_usage missing": (f"SELECT COUNT(*) FROM ({PROVIDER_AGG}) a WHERE NOT EXISTS "
                               "(SELECT 1 FROM provider_usage p WHERE p.user_id=a.user_id AND p.provider=a.provider)"),
    "usage_hourly": (f"SELECT COUNT(*) FROM usage_hourly h LEFT JOIN ({HOURLY_AGG}) a ON a.user_id=h.user_id AND a.hour=h.hour "
                     f"AND a.source_id=h.source_id AND a.model=h.model WHERE {_differs(CRED_COLS, 'h', 'a')}"),
}


def _set(cols, target):
    return ", ".join(f"{target}.{col}=COALESCE(a.{col}, 0)" for col in _all(cols))


def apply(conn):
    conn.native(f"UPDATE credentials c LEFT JOIN ({CRED_AGG}) a ON a.id=c.id SET {_set(CRED_COLS, 'c')}")
    conn.native(f"UPDATE api_keys k LEFT JOIN ({KEY_AGG}) a ON a.id=k.id SET {_set(KEY_COLS, 'k')}")
    conn.native(f"UPDATE provider_usage p LEFT JOIN ({PROVIDER_AGG}) a "
                f"ON a.user_id=p.user_id AND a.provider=p.provider SET {_set(KEY_COLS, 'p')}")
    cols = ", ".join(_all(KEY_COLS))
    conn.native(
        f"INSERT INTO provider_usage (user_id, provider, {cols}, updated_at) "
        f"SELECT a.user_id, a.provider, {', '.join('a.' + col for col in _all(KEY_COLS))}, %s FROM ({PROVIDER_AGG}) a "
        "WHERE NOT EXISTS (SELECT 1 FROM provider_usage p WHERE p.user_id=a.user_id AND p.provider=a.provider)",
        (db.now(),),
    )
    cols = ", ".join(_all(CRED_COLS))
    conn.native("DELETE FROM usage_hourly")
    conn.native(f"INSERT INTO usage_hourly (user_id, hour, source_id, model, {cols}) "
                f"SELECT a.user_id, a.hour, a.source_id, a.model, {', '.join('a.' + col for col in _all(CRED_COLS))} "
                f"FROM ({HOURLY_AGG}) a")


def report(conn) -> dict[str, int]:
    return {name: int(conn.native(sql).fetchone()[0]) for name, sql in REPORT.items()}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="rewrite the rollups (stop the service first)")
    args = parser.parse_args()
    conn = connect(settings.database_url, max_connections=1)
    try:
        for name, count in report(conn).items():
            print(f"{name}: {count} row(s) disagree with usage_events")
        conn.commit()
        if not args.apply:
            return
        apply(conn)
        conn.commit()
        for name, count in report(conn).items():
            print(f"after: {name}: {count}")
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    main()
