import json
import logging
import sqlite3
import threading
from contextlib import contextmanager, nullcontext
from datetime import datetime, timezone

from .config import settings

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_conn: sqlite3.Connection | None = None
SQLITE_INT_MAX = 2**63 - 1


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def iso_week_start() -> str:
    d = datetime.now(timezone.utc).date()
    return d.fromisocalendar(d.isocalendar().year, d.isocalendar().week, 1).isoformat()


def connect():
    global _conn
    if _conn is not None:
        return _conn
    # Serialize creation: two threads racing an empty _conn would each build a
    # full pool (settings.database_pool_size connections against the shared
    # MariaDB max_connections) and leak one forever.
    with _lock:
        if _conn is not None:
            return _conn
        url = (getattr(settings, "database_url", "") or "").strip()
        if not settings.database_mariadb_enabled or not url.startswith("mysql://"):
            raise RuntimeError("Fuse requires MariaDB: configure DATABASE_URL and DATABASE_MARIADB_ENABLED=true")
        from .mysql_compat import connect as mysql_connect
        conn = mysql_connect(url, max_connections=settings.database_pool_size)
        try:
            from .mysql_compat import SCHEMA_VERSION
            if not conn.execute("SELECT value FROM schema_meta WHERE key=?", (SCHEMA_VERSION,)).fetchone():
                raise RuntimeError("MariaDB schema has not been migrated and verified")
            conn.commit()
        except Exception:
            conn.close()
            raise
        _conn = conn
        return _conn


def close() -> None:
    global _conn
    with _lock:
        conn, _conn = _conn, None
        if conn is not None:
            conn.close()


def _init(conn: sqlite3.Connection) -> None:
    from .mysql_compat import Conn, SCHEMA_VERSION
    if isinstance(conn, Conn):
        if not conn.execute("SELECT value FROM schema_meta WHERE key=?", (SCHEMA_VERSION,)).fetchone():
            raise RuntimeError("MariaDB schema migration required")
        return
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            is_admin INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            chat_instructions TEXT NOT NULL DEFAULT '',
            chat_profile TEXT NOT NULL DEFAULT '',
            pool_prefs TEXT NOT NULL DEFAULT '{}'
        );
        CREATE TABLE IF NOT EXISTS schema_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS registration_whitelist (
            id INTEGER PRIMARY KEY,
            email TEXT UNIQUE NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS api_keys (
            id INTEGER PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id),
            name TEXT NOT NULL DEFAULT 'default',
            prefix TEXT NOT NULL,
            key_hash TEXT UNIQUE NOT NULL,
            created_at TEXT NOT NULL,
            last_used_at TEXT,
            prompt_tokens INTEGER NOT NULL DEFAULT 0,
            completion_tokens INTEGER NOT NULL DEFAULT 0,
            output_tokens INTEGER NOT NULL DEFAULT 0,
            visible_tokens INTEGER NOT NULL DEFAULT 0,
            reasoning_tokens INTEGER NOT NULL DEFAULT 0,
            cached_tokens INTEGER NOT NULL DEFAULT 0,
            cache_write_tokens INTEGER NOT NULL DEFAULT 0,
            audio_tokens INTEGER NOT NULL DEFAULT 0,
            image_tokens INTEGER NOT NULL DEFAULT 0,
            total_tokens INTEGER NOT NULL DEFAULT 0,
            requests INTEGER NOT NULL DEFAULT 0,
            cost_micros INTEGER NOT NULL DEFAULT 0,
            last_model TEXT,
            usage_updated_at TEXT
        );
        CREATE TABLE IF NOT EXISTS provider_usage (
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            provider TEXT NOT NULL,
            prompt_tokens INTEGER NOT NULL DEFAULT 0,
            completion_tokens INTEGER NOT NULL DEFAULT 0,
            output_tokens INTEGER NOT NULL DEFAULT 0,
            visible_tokens INTEGER NOT NULL DEFAULT 0,
            reasoning_tokens INTEGER NOT NULL DEFAULT 0,
            cached_tokens INTEGER NOT NULL DEFAULT 0,
            cache_write_tokens INTEGER NOT NULL DEFAULT 0,
            audio_tokens INTEGER NOT NULL DEFAULT 0,
            image_tokens INTEGER NOT NULL DEFAULT 0,
            total_tokens INTEGER NOT NULL DEFAULT 0,
            requests INTEGER NOT NULL DEFAULT 0,
            cost_micros INTEGER NOT NULL DEFAULT 0,
            last_model TEXT,
            updated_at TEXT,
            PRIMARY KEY (user_id, provider)
        );
        CREATE TABLE IF NOT EXISTS sources (
            id INTEGER PRIMARY KEY,
            type TEXT NOT NULL,
            name TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1,
            config TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS credentials (
            id INTEGER PRIMARY KEY,
            source_id INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
            label TEXT NOT NULL,
            secret TEXT NOT NULL,
            extra TEXT NOT NULL DEFAULT '{}',
            week_start TEXT,
            week_tokens INTEGER NOT NULL DEFAULT 0,
            week_requests INTEGER NOT NULL DEFAULT 0,
            prompt_tokens INTEGER NOT NULL DEFAULT 0,
            completion_tokens INTEGER NOT NULL DEFAULT 0,
            output_tokens INTEGER NOT NULL DEFAULT 0,
            visible_tokens INTEGER NOT NULL DEFAULT 0,
            reasoning_tokens INTEGER NOT NULL DEFAULT 0,
            cached_tokens INTEGER NOT NULL DEFAULT 0,
            cache_write_tokens INTEGER NOT NULL DEFAULT 0,
            total_tokens INTEGER NOT NULL DEFAULT 0,
            requests INTEGER NOT NULL DEFAULT 0,
            cost_micros INTEGER NOT NULL DEFAULT 0,
            disabled INTEGER NOT NULL DEFAULT 0,
            donated_by INTEGER REFERENCES users(id),
            created_at TEXT NOT NULL,
            last_usage_percent REAL,
            usage_percent_at TEXT,
            pool_held_until TEXT,
            pool_invokes INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS usage_events (
            id INTEGER PRIMARY KEY,
            user_id INTEGER NOT NULL,
            api_key_id INTEGER,
            source_id INTEGER,
            source_type TEXT NOT NULL DEFAULT '',
            source_name TEXT NOT NULL DEFAULT '',
            credential_id INTEGER,
            model TEXT,
            requested_model TEXT NOT NULL DEFAULT '',
            prompt_tokens INTEGER NOT NULL DEFAULT 0,
            completion_tokens INTEGER NOT NULL DEFAULT 0,
            output_tokens INTEGER NOT NULL DEFAULT 0,
            visible_tokens INTEGER NOT NULL DEFAULT 0,
            reasoning_tokens INTEGER NOT NULL DEFAULT 0,
            cached_tokens INTEGER NOT NULL DEFAULT 0,
            cache_write_tokens INTEGER NOT NULL DEFAULT 0,
            audio_tokens INTEGER NOT NULL DEFAULT 0,
            image_tokens INTEGER NOT NULL DEFAULT 0,
            total_tokens INTEGER NOT NULL DEFAULT 0,
            reported_cost_micros INTEGER,
            estimated_cost_micros INTEGER NOT NULL DEFAULT 0,
            pricing_known INTEGER NOT NULL DEFAULT 0,
            usage_status TEXT NOT NULL DEFAULT 'unknown',
            request_status TEXT NOT NULL DEFAULT 'success',
            count_request INTEGER NOT NULL DEFAULT 1,
            provider_request_id TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS usage_hourly (
            user_id INTEGER NOT NULL,
            hour TEXT NOT NULL,
            source_id INTEGER NOT NULL DEFAULT 0,
            model TEXT NOT NULL DEFAULT '',
            prompt_tokens INTEGER NOT NULL DEFAULT 0,
            completion_tokens INTEGER NOT NULL DEFAULT 0,
            output_tokens INTEGER NOT NULL DEFAULT 0,
            visible_tokens INTEGER NOT NULL DEFAULT 0,
            reasoning_tokens INTEGER NOT NULL DEFAULT 0,
            cached_tokens INTEGER NOT NULL DEFAULT 0,
            cache_write_tokens INTEGER NOT NULL DEFAULT 0,
            total_tokens INTEGER NOT NULL DEFAULT 0,
            cost_micros INTEGER NOT NULL DEFAULT 0,
            requests INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (user_id, hour, source_id, model)
        );
        CREATE TABLE IF NOT EXISTS pool_sessions (
            kind TEXT NOT NULL,
            session_id TEXT NOT NULL,
            credential_id INTEGER NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (kind, session_id)
        );
        CREATE TABLE IF NOT EXISTS model_routes (
            model TEXT PRIMARY KEY,
            source_id INTEGER NOT NULL REFERENCES sources(id),
            upstream_model TEXT
        );
        CREATE TABLE IF NOT EXISTS chat_threads (
            id INTEGER PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            title TEXT NOT NULL DEFAULT '',
            model TEXT NOT NULL DEFAULT '',
            archived INTEGER NOT NULL DEFAULT 0,
            messages TEXT NOT NULL DEFAULT '[]',
            context TEXT NOT NULL DEFAULT '',
            tools TEXT NOT NULL DEFAULT '["web_search"]',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS chat_threads_user_updated ON chat_threads (user_id, archived, updated_at);
        CREATE TABLE IF NOT EXISTS artifacts (
            id INTEGER PRIMARY KEY,
            public_id TEXT UNIQUE NOT NULL,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            thread_id INTEGER REFERENCES chat_threads(id) ON DELETE SET NULL,
            title TEXT NOT NULL,
            visibility TEXT NOT NULL DEFAULT 'private',
            archived INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            current_version INTEGER NOT NULL DEFAULT 1
        );
        CREATE INDEX IF NOT EXISTS artifacts_user_updated ON artifacts (user_id, updated_at);
        CREATE INDEX IF NOT EXISTS artifacts_thread ON artifacts (thread_id);
        CREATE TABLE IF NOT EXISTS artifact_versions (
            id INTEGER PRIMARY KEY,
            artifact_id INTEGER NOT NULL REFERENCES artifacts(id) ON DELETE CASCADE,
            version INTEGER NOT NULL,
            html TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE (artifact_id, version)
        );
        CREATE TABLE IF NOT EXISTS artifact_threads (
            artifact_id INTEGER NOT NULL REFERENCES artifacts(id) ON DELETE CASCADE,
            thread_id INTEGER NOT NULL REFERENCES chat_threads(id) ON DELETE CASCADE,
            PRIMARY KEY (artifact_id, thread_id)
        );
        CREATE TABLE IF NOT EXISTS model_prices (
            provider TEXT NOT NULL,
            model TEXT NOT NULL,
            input_micros_per_million INTEGER NOT NULL DEFAULT 0,
            cached_input_micros_per_million INTEGER NOT NULL DEFAULT 0,
            cache_write_micros_per_million INTEGER NOT NULL DEFAULT 0,
            output_micros_per_million INTEGER NOT NULL DEFAULT 0,
            reasoning_micros_per_million INTEGER NOT NULL DEFAULT 0,
            cached_input_known INTEGER NOT NULL DEFAULT 0,
            cache_write_known INTEGER NOT NULL DEFAULT 0,
            reasoning_known INTEGER NOT NULL DEFAULT 0,
            long_context_threshold INTEGER,
            long_input_micros_per_million INTEGER,
            long_cached_input_micros_per_million INTEGER,
            long_output_micros_per_million INTEGER,
            long_cached_input_known INTEGER NOT NULL DEFAULT 0,
            origin TEXT NOT NULL DEFAULT 'manual',
            openrouter_id TEXT,
            synced_at TEXT,
            updated_at TEXT NOT NULL,
            tiers_json TEXT NOT NULL DEFAULT '[]',
            PRIMARY KEY (provider, model)
        );
        CREATE INDEX IF NOT EXISTS usage_events_user_created ON usage_events (user_id, created_at);
        CREATE INDEX IF NOT EXISTS usage_hourly_user_hour ON usage_hourly (user_id, hour);
        """
    )
    _ensure_usage_columns(conn)
    _ensure_provider_usage_columns(conn)
    _ensure_event_columns(conn)
    _migrate_logical_request_count(conn)
    _ensure_hourly_columns(conn)
    _ensure_price_columns(conn)
    _ensure_cred_guard_columns(conn)
    _ensure_cred_usage_columns(conn)
    _ensure_chat_columns(conn)
    _ensure_artifact_tables(conn)
    _ensure_push_table(conn)
    conn.execute("CREATE INDEX IF NOT EXISTS usage_events_created ON usage_events (created_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS usage_events_provider_created ON usage_events (source_type, created_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS usage_events_model_created ON usage_events (model, created_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS usage_events_credential ON usage_events (credential_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS pool_sessions_updated ON pool_sessions (updated_at)")
    _backfill_canonical_usage(conn)
    _backfill_hourly(conn)
    _backfill_current_usage(conn)
    _backfill_credential_usage(conn)
    _strip_openrouter_costs(conn)
    conn.commit()
    from . import chat_store
    chat_store.init_schema(conn)
    _migrate_users_autoincrement(conn)


_API_KEY_USAGE_COLS = (
    ("prompt_tokens", "INTEGER NOT NULL DEFAULT 0"),
    ("completion_tokens", "INTEGER NOT NULL DEFAULT 0"),
    ("output_tokens", "INTEGER NOT NULL DEFAULT 0"),
    ("visible_tokens", "INTEGER NOT NULL DEFAULT 0"),
    ("reasoning_tokens", "INTEGER NOT NULL DEFAULT 0"),
    ("cached_tokens", "INTEGER NOT NULL DEFAULT 0"),
    ("cache_write_tokens", "INTEGER NOT NULL DEFAULT 0"),
    ("audio_tokens", "INTEGER NOT NULL DEFAULT 0"),
    ("image_tokens", "INTEGER NOT NULL DEFAULT 0"),
    ("total_tokens", "INTEGER NOT NULL DEFAULT 0"),
    ("requests", "INTEGER NOT NULL DEFAULT 0"),
    ("cost_micros", "INTEGER NOT NULL DEFAULT 0"),
    ("last_model", "TEXT"),
    ("usage_updated_at", "TEXT"),
)


def _ensure_column(conn, table: str, name: str, spec: str) -> None:
    cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if name not in cols:
        spec = spec.replace("AUTOINCREMENT", "AUTO_INCREMENT")
        if (getattr(settings, "database_url", "") or "").startswith("mysql://") and spec.strip().upper().startswith("INTEGER"):
            spec = "BIGINT" + spec.strip()[7:]
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {spec}")


def _ensure_usage_columns(conn: sqlite3.Connection) -> None:
    for name, spec in _API_KEY_USAGE_COLS:
        _ensure_column(conn, "api_keys", name, spec)


def _ensure_provider_usage_columns(conn: sqlite3.Connection) -> None:
    for name, spec in _API_KEY_USAGE_COLS:
        if name == "usage_updated_at":
            continue
        _ensure_column(conn, "provider_usage", name, spec)


_EVENT_COLS = (
    ("api_key_id", "INTEGER"),
    ("source_type", "TEXT NOT NULL DEFAULT ''"),
    ("source_name", "TEXT NOT NULL DEFAULT ''"),
    ("requested_model", "TEXT NOT NULL DEFAULT ''"),
    ("cached_tokens", "INTEGER NOT NULL DEFAULT 0"),
    ("cache_write_tokens", "INTEGER NOT NULL DEFAULT 0"),
    ("output_tokens", "INTEGER NOT NULL DEFAULT 0"),
    ("visible_tokens", "INTEGER NOT NULL DEFAULT 0"),
    ("audio_tokens", "INTEGER NOT NULL DEFAULT 0"),
    ("image_tokens", "INTEGER NOT NULL DEFAULT 0"),
    ("total_tokens", "INTEGER NOT NULL DEFAULT 0"),
    ("reported_cost_micros", "INTEGER"),
    ("estimated_cost_micros", "INTEGER NOT NULL DEFAULT 0"),
    ("pricing_known", "INTEGER NOT NULL DEFAULT 0"),
    ("usage_status", "TEXT NOT NULL DEFAULT 'unknown'"),
    ("request_status", "TEXT NOT NULL DEFAULT 'success'"),
    ("count_request", "INTEGER NOT NULL DEFAULT 1"),
    ("provider_request_id", "TEXT NOT NULL DEFAULT ''"),
)


def _ensure_event_columns(conn: sqlite3.Connection) -> None:
    for name, spec in _EVENT_COLS:
        _ensure_column(conn, "usage_events", name, spec)


def _migrate_logical_request_count(conn: sqlite3.Connection) -> None:
    if conn.execute("SELECT 1 FROM schema_meta WHERE key='logical_request_count_v2'").fetchone():
        return
    v1_applied = conn.execute(
        "SELECT 1 FROM schema_meta WHERE key='logical_request_count_v1'"
    ).fetchone()
    if v1_applied:
        # v1 defaulted old events to one. Recover each provider's known request
        # total, preferring successful rows and then the latest failed row.
        for row in conn.execute("SELECT user_id, provider, requests FROM provider_usage").fetchall():
            where = """user_id=? AND COALESCE(NULLIF(source_type, ''),
                       (SELECT type FROM sources WHERE id=usage_events.source_id), 'unknown')=?"""
            args = (row["user_id"], row["provider"])
            conn.execute(f"UPDATE usage_events SET count_request=0 WHERE {where}", args)
            ids = conn.execute(
                f"""SELECT id FROM usage_events WHERE {where}
                    ORDER BY (request_status='success') DESC, id DESC LIMIT ?""",
                (*args, max(0, int(row["requests"] or 0))),
            ).fetchall()
            conn.executemany(
                "UPDATE usage_events SET count_request=1 WHERE id=?",
                [(item["id"],) for item in ids],
            )
    else:
        # Existing hourly counters were maintained with count_request at write
        # time, so they are the most precise migration source for old rows.
        for row in conn.execute("SELECT * FROM usage_hourly").fetchall():
            args = (row["user_id"], row["hour"], row["source_id"], row["model"])
            where = """user_id=? AND substr(created_at, 1, 13) || ':00:00Z'=?
                       AND COALESCE(source_id, 0)=? AND COALESCE(model, '')=?"""
            conn.execute(f"UPDATE usage_events SET count_request=0 WHERE {where}", args)
            ids = conn.execute(
                f"""SELECT id FROM usage_events WHERE {where}
                    ORDER BY (request_status='success') DESC, id DESC LIMIT ?""",
                (*args, max(0, int(row["requests"] or 0))),
            ).fetchall()
            conn.executemany(
                "UPDATE usage_events SET count_request=1 WHERE id=?",
                [(item["id"],) for item in ids],
            )
    conn.execute("DELETE FROM usage_hourly")
    conn.execute(
        "INSERT OR IGNORE INTO schema_meta(key,value) VALUES('logical_request_count_v1',?)",
        (now(),),
    )
    conn.execute(
        "INSERT INTO schema_meta(key,value) VALUES('logical_request_count_v2',?)",
        (now(),),
    )


_HOURLY_COLS = (
    ("output_tokens", "INTEGER NOT NULL DEFAULT 0"),
    ("visible_tokens", "INTEGER NOT NULL DEFAULT 0"),
    ("cached_tokens", "INTEGER NOT NULL DEFAULT 0"),
    ("cache_write_tokens", "INTEGER NOT NULL DEFAULT 0"),
    ("total_tokens", "INTEGER NOT NULL DEFAULT 0"),
    ("cost_micros", "INTEGER NOT NULL DEFAULT 0"),
)


def _ensure_hourly_columns(conn: sqlite3.Connection) -> None:
    for name, spec in _HOURLY_COLS:
        _ensure_column(conn, "usage_hourly", name, spec)


_PRICE_COLS = (
    ("cached_input_known", "INTEGER NOT NULL DEFAULT 0"),
    ("cache_write_micros_per_million", "INTEGER NOT NULL DEFAULT 0"),
    ("cache_write_known", "INTEGER NOT NULL DEFAULT 0"),
    ("reasoning_known", "INTEGER NOT NULL DEFAULT 0"),
    ("long_context_threshold", "INTEGER"),
    ("long_input_micros_per_million", "INTEGER"),
    ("long_cached_input_micros_per_million", "INTEGER"),
    ("long_output_micros_per_million", "INTEGER"),
    ("long_cached_input_known", "INTEGER NOT NULL DEFAULT 0"),
    ("origin", "TEXT NOT NULL DEFAULT 'manual'"),
    ("openrouter_id", "TEXT"),
    ("synced_at", "TEXT"),
    ("tiers_json", "TEXT NOT NULL DEFAULT '[]'"),
)


def _ensure_price_columns(conn: sqlite3.Connection) -> None:
    for name, spec in _PRICE_COLS:
        _ensure_column(conn, "model_prices", name, spec)


_CRED_GUARD_COLS = (
    ("last_usage_percent", "REAL"),
    ("usage_percent_at", "TEXT"),
    ("pool_held_until", "TEXT"),
    ("pool_invokes", "INTEGER NOT NULL DEFAULT 0"),
)


def _ensure_cred_guard_columns(conn: sqlite3.Connection) -> None:
    for name, spec in _CRED_GUARD_COLS:
        _ensure_column(conn, "credentials", name, spec)


_CRED_USAGE_COLS = (
    ("prompt_tokens", "INTEGER NOT NULL DEFAULT 0"),
    ("completion_tokens", "INTEGER NOT NULL DEFAULT 0"),
    ("output_tokens", "INTEGER NOT NULL DEFAULT 0"),
    ("visible_tokens", "INTEGER NOT NULL DEFAULT 0"),
    ("reasoning_tokens", "INTEGER NOT NULL DEFAULT 0"),
    ("cached_tokens", "INTEGER NOT NULL DEFAULT 0"),
    ("cache_write_tokens", "INTEGER NOT NULL DEFAULT 0"),
    ("total_tokens", "INTEGER NOT NULL DEFAULT 0"),
    ("requests", "INTEGER NOT NULL DEFAULT 0"),
    ("cost_micros", "INTEGER NOT NULL DEFAULT 0"),
)


def _ensure_cred_usage_columns(conn: sqlite3.Connection) -> None:
    for name, spec in _CRED_USAGE_COLS:
        _ensure_column(conn, "credentials", name, spec)


def _ensure_chat_columns(conn: sqlite3.Connection) -> None:
    _ensure_column(conn, "users", "chat_instructions", "TEXT NOT NULL DEFAULT ''")
    _ensure_column(conn, "users", "chat_profile", "TEXT NOT NULL DEFAULT ''")
    _ensure_column(conn, "users", "pool_prefs", "TEXT NOT NULL DEFAULT '{}'")
    _ensure_column(conn, "chat_threads", "context", "TEXT NOT NULL DEFAULT ''")
    _ensure_column(conn, "chat_threads", "tools", "TEXT NOT NULL DEFAULT '[\"web_search\"]'")
    _ensure_column(conn, "users", "vault_salt", "BLOB")
    _ensure_column(conn, "users", "wrapped_dek", "TEXT")
    _ensure_column(conn, "users", "vault_kdf", "TEXT")
    _ensure_column(conn, "chat_threads", "kind", "TEXT NOT NULL DEFAULT 'chat'")
    _ensure_column(conn, "chat_threads", "host_id", "INTEGER")
    _ensure_column(conn, "chat_threads", "project_id", "INTEGER")
    _ensure_column(conn, "chat_threads", "public_id", "TEXT")
    _ensure_column(conn, "chat_threads", "compaction", "TEXT NOT NULL DEFAULT '{}'")
    _ensure_column(conn, "chat_threads", "title_state", "TEXT NOT NULL DEFAULT ''")
    _ensure_column(conn, "chat_threads", "title_attempts", "INTEGER NOT NULL DEFAULT 0")
    _ensure_column(conn, "chat_threads", "title_attempted_at", "TEXT NOT NULL DEFAULT ''")
    _ensure_column(conn, "chat_threads", "title_job_token", "TEXT NOT NULL DEFAULT ''")
    _ensure_column(conn, "chat_threads", "title_job_until", "TEXT NOT NULL DEFAULT ''")
    from . import chat_recaps
    chat_recaps.init_schema(conn)
    _ensure_host_tables(conn)
    from . import retention
    retention.init_schema(conn)
    _ensure_skill_tables(conn)
    _ensure_schedule_tables(conn)
    _ensure_column(conn, "chat_threads", "schedule_id", "INTEGER")
    _ensure_column(conn, "chat_threads", "pinned", "INTEGER NOT NULL DEFAULT 0")
    _ensure_column(conn, "chat_threads", "pin_order", "INTEGER NOT NULL DEFAULT 0")
    _ensure_column(conn, "chat_threads", "unread", "INTEGER NOT NULL DEFAULT 0")


def _ensure_host_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS user_hosts (
            id INTEGER PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            alias TEXT NOT NULL,
            label TEXT NOT NULL DEFAULT '',
            hostname TEXT NOT NULL,
            port INTEGER NOT NULL DEFAULT 22,
            username TEXT NOT NULL,
            auth_type TEXT NOT NULL,
            secret TEXT NOT NULL,
            passphrase TEXT NOT NULL DEFAULT '',
            default_cwd TEXT NOT NULL DEFAULT '',
            key_algo TEXT NOT NULL DEFAULT '',
            fingerprint TEXT NOT NULL DEFAULT '',
            disabled INTEGER NOT NULL DEFAULT 0,
            last_ok_at TEXT,
            last_error TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(user_id, alias)
        );
        CREATE INDEX IF NOT EXISTS user_hosts_user ON user_hosts (user_id);
        CREATE TABLE IF NOT EXISTS user_projects (
            id INTEGER PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            host_id INTEGER NOT NULL REFERENCES user_hosts(id) ON DELETE CASCADE,
            name TEXT NOT NULL,
            remote_path TEXT NOT NULL,
            notes TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            UNIQUE(user_id, host_id, name)
        );
        CREATE INDEX IF NOT EXISTS user_projects_host ON user_projects (user_id, host_id);
        CREATE TABLE IF NOT EXISTS user_agent_settings (
            user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
            agent_coding INTEGER NOT NULL DEFAULT 0,
            allow_nested_spawn INTEGER NOT NULL DEFAULT 0,
            permission_mode TEXT NOT NULL DEFAULT 'project',
            local_workspace_enabled INTEGER NOT NULL DEFAULT 0
        );
        """
    )
    _ensure_column(conn, "user_projects", "default_model", "TEXT NOT NULL DEFAULT ''")
    _ensure_column(conn, "user_projects", "permission_mode", "TEXT NOT NULL DEFAULT 'project'")
    _ensure_column(conn, "user_projects", "updated_at", "TEXT NOT NULL DEFAULT ''")
    _ensure_column(conn, "user_hosts", "device_token_hash", "TEXT NOT NULL DEFAULT ''")
    _ensure_column(conn, "user_hosts", "agent_version", "TEXT NOT NULL DEFAULT ''")
    _ensure_column(conn, "user_hosts", "agent_os", "TEXT NOT NULL DEFAULT ''")
    _ensure_column(conn, "user_hosts", "agent_arch", "TEXT NOT NULL DEFAULT ''")
    _ensure_column(conn, "user_hosts", "last_seen_at", "TEXT")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS host_pairings (
            id INTEGER PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            code_hash TEXT NOT NULL UNIQUE,
            code TEXT NOT NULL DEFAULT '',
            prefix TEXT NOT NULL DEFAULT '',
            alias_hint TEXT NOT NULL DEFAULT '',
            expires_at TEXT NOT NULL,
            used_at TEXT,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS host_pairings_user ON host_pairings (user_id, expires_at);
        """
    )
    # Codes are reveal-once (returned by mint, never stored). Clear legacy rows.
    conn.execute("UPDATE host_pairings SET code='' WHERE code != ''")


def _ensure_skill_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS user_skills (
            id INTEGER PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            slug TEXT NOT NULL,
            display_name TEXT NOT NULL DEFAULT '',
            description TEXT NOT NULL DEFAULT '',
            origin TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT '',
            source_id TEXT NOT NULL DEFAULT '',
            enabled INTEGER NOT NULL DEFAULT 1,
            user_invocable INTEGER NOT NULL DEFAULT 1,
            disable_model_invocation INTEGER NOT NULL DEFAULT 0,
            argument_hint TEXT NOT NULL DEFAULT '',
            content_hash TEXT NOT NULL DEFAULT '',
            installed_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(user_id, slug)
        );
        CREATE INDEX IF NOT EXISTS user_skills_user ON user_skills (user_id, enabled);
        CREATE TABLE IF NOT EXISTS skill_proposals (
            id TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            thread_id INTEGER NOT NULL REFERENCES chat_threads(id) ON DELETE CASCADE,
            run_id TEXT,
            message_id TEXT,
            slug TEXT NOT NULL,
            description TEXT NOT NULL,
            body TEXT NOT NULL,
            files TEXT NOT NULL DEFAULT '{}',
            enable INTEGER NOT NULL DEFAULT 1,
            argument_hint TEXT NOT NULL DEFAULT '',
            overwrite INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL,
            resolved_at TEXT
        );
        CREATE INDEX IF NOT EXISTS skill_proposals_user
            ON skill_proposals (user_id, status, created_at);
        """
    )


def _ensure_schedule_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS chat_schedules (
            id INTEGER PRIMARY KEY,
            public_id TEXT UNIQUE NOT NULL,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            name TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1,
            paused INTEGER NOT NULL DEFAULT 0,
            target TEXT NOT NULL,
            project_id INTEGER REFERENCES user_projects(id) ON DELETE SET NULL,
            thread_id INTEGER REFERENCES chat_threads(id) ON DELETE SET NULL,
            fresh_each_fire INTEGER NOT NULL DEFAULT 0,
            allow_coding INTEGER NOT NULL DEFAULT 0,
            prompt TEXT NOT NULL,
            variables TEXT NOT NULL DEFAULT '{}',
            model TEXT NOT NULL,
            tools TEXT NOT NULL DEFAULT '["web_search"]',
            reasoning_effort TEXT NOT NULL DEFAULT '',
            timezone TEXT NOT NULL,
            recur_kind TEXT NOT NULL,
            cron_expr TEXT NOT NULL DEFAULT '',
            every_seconds INTEGER,
            weekdays INTEGER NOT NULL DEFAULT 0,
            at_local TEXT NOT NULL DEFAULT '',
            once_at TEXT,
            next_fire_at TEXT,
            last_fire_at TEXT,
            last_status TEXT NOT NULL DEFAULT '',
            last_error TEXT NOT NULL DEFAULT '',
            last_run_id TEXT,
            firing_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS chat_schedules_due
            ON chat_schedules (enabled, paused, next_fire_at);
        CREATE INDEX IF NOT EXISTS chat_schedules_user
            ON chat_schedules (user_id);
        CREATE TABLE IF NOT EXISTS schedule_fires (
            id INTEGER PRIMARY KEY,
            schedule_id INTEGER NOT NULL REFERENCES chat_schedules(id) ON DELETE CASCADE,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            trigger TEXT NOT NULL,
            status TEXT NOT NULL,
            slot_key TEXT NOT NULL,
            fire_id TEXT NOT NULL,
            thread_id INTEGER,
            run_id TEXT,
            error TEXT NOT NULL DEFAULT '',
            started_at TEXT NOT NULL,
            finished_at TEXT,
            UNIQUE(schedule_id, slot_key)
        );
        CREATE INDEX IF NOT EXISTS schedule_fires_sched
            ON schedule_fires (schedule_id, id DESC);
        CREATE INDEX IF NOT EXISTS schedule_fires_run
            ON schedule_fires (run_id);
        """
    )


def _ensure_artifact_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS artifacts (
            id INTEGER PRIMARY KEY,
            public_id TEXT UNIQUE NOT NULL,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            thread_id INTEGER REFERENCES chat_threads(id) ON DELETE SET NULL,
            title TEXT NOT NULL,
            visibility TEXT NOT NULL DEFAULT 'private',
            archived INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            current_version INTEGER NOT NULL DEFAULT 1
        );
        CREATE INDEX IF NOT EXISTS artifacts_user_updated ON artifacts (user_id, updated_at);
        CREATE INDEX IF NOT EXISTS artifacts_thread ON artifacts (thread_id);
        CREATE TABLE IF NOT EXISTS artifact_versions (
            id INTEGER PRIMARY KEY,
            artifact_id INTEGER NOT NULL REFERENCES artifacts(id) ON DELETE CASCADE,
            version INTEGER NOT NULL,
            html TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE (artifact_id, version)
        );
        CREATE TABLE IF NOT EXISTS artifact_threads (
            artifact_id INTEGER NOT NULL REFERENCES artifacts(id) ON DELETE CASCADE,
            thread_id INTEGER NOT NULL REFERENCES chat_threads(id) ON DELETE CASCADE,
            PRIMARY KEY (artifact_id, thread_id)
        );
        """
    )
    _ensure_column(conn, "artifacts", "archived", "INTEGER NOT NULL DEFAULT 0")


def _migrate_users_autoincrement(conn: sqlite3.Connection) -> None:
    """Rebuild users with AUTOINCREMENT so a deleted user's id is never reused.

    Rowid reuse let a recycled uid inherit another account's still-valid
    session cookie and vault keep-cookie. The rebuild follows SQLite's
    documented 12-step procedure; guarded by schema_meta so it runs once.
    """
    if conn.execute("SELECT 1 FROM schema_meta WHERE key='users_autoincrement_v1'").fetchone():
        return
    conn.commit()
    conn.execute("PRAGMA foreign_keys=OFF")
    try:
        info = conn.execute("PRAGMA table_info(users)").fetchall()
        have_cols = [r[1] for r in info]
        base_defs = {
            "id": "id INTEGER PRIMARY KEY AUTOINCREMENT",
            "email": "email TEXT UNIQUE NOT NULL",
            "password_hash": "password_hash TEXT NOT NULL",
            "is_admin": "is_admin INTEGER NOT NULL DEFAULT 0",
            "created_at": "created_at TEXT NOT NULL",
            "chat_instructions": "chat_instructions TEXT NOT NULL DEFAULT ''",
            "chat_profile": "chat_profile TEXT NOT NULL DEFAULT ''",
            "pool_prefs": "pool_prefs TEXT NOT NULL DEFAULT '{}'",
            "vault_salt": "vault_salt BLOB",
            "wrapped_dek": "wrapped_dek TEXT",
            "vault_kdf": "vault_kdf TEXT",
            "chat_context_revision": "chat_context_revision INTEGER NOT NULL DEFAULT 0",
        }
        defs = list(base_defs.values())
        # Columns other modules added before this one-time rebuild (recap and
        # cleanup preferences, ...) keep their declared type and default.
        for _cid, name, ctype, notnull, default, _pk in info:
            if name in base_defs:
                continue
            spec = f"{name} {ctype or ''}".rstrip()
            if notnull:
                spec += " NOT NULL"
            if default is not None:
                spec += f" DEFAULT {default}"
            defs.append(spec)
        col_list = ", ".join(have_cols)
        conn.executescript(
            "BEGIN IMMEDIATE;\n"
            "CREATE TABLE users_v2 (\n    " + ",\n    ".join(defs) + "\n);\n"
            f"INSERT INTO users_v2 ({col_list})\n"
            f"     SELECT {col_list} FROM users;\n"
            "DROP TABLE users;\n"
            "ALTER TABLE users_v2 RENAME TO users;\n"
        )
        # Pin the sequence at least past every id ever used; sqlite_sequence
        # already holds max(id), but keep the floor explicit against restored
        # backups with higher historical ids.
        floor = conn.execute(
            "SELECT COALESCE(MAX(id), 0) FROM users"
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO schema_meta(key, value) VALUES('users_id_floor', ?)",
            (str(int(floor)),),
        )
        conn.execute(
            "INSERT INTO schema_meta(key, value) VALUES('users_autoincrement_v1', ?)",
            (now(),),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.execute("PRAGMA foreign_keys=ON")
    broke = conn.execute("PRAGMA foreign_key_check").fetchall()
    if broke:
        raise RuntimeError(f"foreign key check failed after users rebuild: {broke[:3]}")


def _ensure_push_table(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS push_subscriptions (
            id INTEGER PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            endpoint TEXT UNIQUE NOT NULL,
            p256dh TEXT NOT NULL,
            auth TEXT NOT NULL,
            user_agent TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            last_push_at TEXT
        );
        CREATE INDEX IF NOT EXISTS push_subscriptions_user ON push_subscriptions (user_id);
        """
    )


def _pget(price, key: str, default=None):
    """Read a pricing field from either an sqlite Row or a plain dict."""
    try:
        return price[key]
    except (KeyError, IndexError, TypeError):
        return default


def _canonical_output(row) -> tuple[int, int]:
    """(output, visible) with fallbacks for rows predating canonical fields."""
    output = int(row["output_tokens"] or 0)
    visible = int(row["visible_tokens"] or 0)
    if output <= 0:
        completion = int(row["completion_tokens"] or 0)
        reasoning = int(row["reasoning_tokens"] or 0)
        output = max(completion, reasoning)
        if visible <= 0:
            visible = max(completion - reasoning, 0)
    visible = min(visible, output)
    return output, visible


def _reprice_open_events(conn: sqlite3.Connection) -> None:
    """Recompute estimated_cost_micros after the pricing formula changes."""
    prices = {
        (row["provider"], row["model"]): row
        for row in conn.execute("SELECT * FROM model_prices").fetchall()
    }
    if not prices:
        return
    rows = conn.execute(
        """SELECT e.id, e.user_id, e.api_key_id, e.credential_id, e.reported_cost_micros,
                  e.estimated_cost_micros, e.prompt_tokens, e.completion_tokens,
                  e.output_tokens, e.visible_tokens, e.reasoning_tokens, e.cached_tokens,
                  e.cache_write_tokens, e.model,
                  COALESCE(NULLIF(e.source_type, ''), s.type, 'unknown') AS provider
           FROM usage_events e LEFT JOIN sources s ON s.id=e.source_id
           WHERE e.reported_cost_micros IS NULL"""
    ).fetchall()
    updates = []
    key_deltas: dict[int, int] = {}
    user_deltas: dict[tuple[int, str], int] = {}
    for row in rows:
        if row["provider"] == "openrouter":
            continue
        price = prices.get((row["provider"], row["model"]))
        output, visible = _canonical_output(row)
        estimate = (
            _estimated_cost(
                price,
                int(row["prompt_tokens"] or 0),
                output,
                visible,
                int(row["reasoning_tokens"] or 0),
                int(row["cached_tokens"] or 0),
                int(row["cache_write_tokens"] or 0),
            )
            if price
            else 0
        )
        old = int(row["estimated_cost_micros"] or 0)
        if estimate == old:
            continue
        updates.append((estimate, 1 if price else 0, row["id"]))
        delta = estimate - old
        if row["api_key_id"]:
            key_deltas[row["api_key_id"]] = key_deltas.get(row["api_key_id"], 0) + delta
        user_deltas[(row["user_id"], row["provider"])] = (
            user_deltas.get((row["user_id"], row["provider"]), 0) + delta
        )
    if not updates:
        return
    conn.executemany(
        "UPDATE usage_events SET estimated_cost_micros=?, pricing_known=? WHERE id=?",
        updates,
    )
    conn.executemany(
        "UPDATE api_keys SET cost_micros=MAX(0, cost_micros+?) WHERE id=?",
        [(delta, key_id) for key_id, delta in key_deltas.items()],
    )
    conn.executemany(
        """UPDATE provider_usage SET cost_micros=MAX(0, cost_micros+?)
           WHERE user_id=? AND provider=?""",
        [(delta, user_id, provider) for (user_id, provider), delta in user_deltas.items()],
    )


def _backfill_credential_usage(conn: sqlite3.Connection) -> None:
    n = conn.execute("SELECT COALESCE(SUM(requests), 0) FROM credentials").fetchone()[0]
    if n:
        return
    events = conn.execute(
        "SELECT COUNT(*) FROM usage_events WHERE credential_id IS NOT NULL"
    ).fetchone()[0]
    if not events:
        return
    _reprice_open_events(conn)
    conn.execute(
        """UPDATE credentials SET
             prompt_tokens = (
               SELECT COALESCE(SUM(e.prompt_tokens), 0) FROM usage_events e
               WHERE e.credential_id = credentials.id),
             completion_tokens = (
               SELECT COALESCE(SUM(e.completion_tokens), 0) FROM usage_events e
               WHERE e.credential_id = credentials.id),
             output_tokens = (
               SELECT COALESCE(SUM(e.output_tokens), 0) FROM usage_events e
               WHERE e.credential_id = credentials.id),
             visible_tokens = (
               SELECT COALESCE(SUM(e.visible_tokens), 0) FROM usage_events e
               WHERE e.credential_id = credentials.id),
             reasoning_tokens = (
               SELECT COALESCE(SUM(e.reasoning_tokens), 0) FROM usage_events e
               WHERE e.credential_id = credentials.id),
             cached_tokens = (
               SELECT COALESCE(SUM(e.cached_tokens), 0) FROM usage_events e
               WHERE e.credential_id = credentials.id),
             cache_write_tokens = (
               SELECT COALESCE(SUM(e.cache_write_tokens), 0) FROM usage_events e
               WHERE e.credential_id = credentials.id),
             total_tokens = (
               SELECT COALESCE(SUM(CASE WHEN e.total_tokens>0 THEN e.total_tokens
                                        ELSE e.prompt_tokens + e.output_tokens END), 0)
               FROM usage_events e WHERE e.credential_id = credentials.id),
              requests = (
                SELECT COALESCE(SUM(e.count_request), 0) FROM usage_events e
                WHERE e.credential_id = credentials.id),
             cost_micros = (
               SELECT COALESCE(SUM(COALESCE(e.reported_cost_micros, e.estimated_cost_micros, 0)), 0)
               FROM usage_events e WHERE e.credential_id = credentials.id)
           WHERE EXISTS (SELECT 1 FROM usage_events e WHERE e.credential_id = credentials.id)"""
    )


def _backfill_current_usage(conn: sqlite3.Connection) -> None:
    n = conn.execute("SELECT COUNT(*) FROM provider_usage").fetchone()[0]
    if n:
        return
    events = conn.execute("SELECT COUNT(*) FROM usage_events").fetchone()[0]
    if not events:
        return
    ts = now()
    conn.execute(
        """INSERT INTO provider_usage
           (user_id, provider, prompt_tokens, completion_tokens, reasoning_tokens,
            cached_tokens, audio_tokens, image_tokens, total_tokens, requests,
            cost_micros, last_model, updated_at)
           SELECT e.user_id,
                  COALESCE(s.type, 'unknown'),
                  COALESCE(SUM(e.prompt_tokens), 0),
                  COALESCE(SUM(e.completion_tokens), 0),
                  COALESCE(SUM(e.reasoning_tokens), 0),
                  0, 0, 0,
                   COALESCE(SUM(CASE WHEN e.total_tokens>0 THEN e.total_tokens
                                     ELSE e.prompt_tokens + e.completion_tokens END), 0),
                   COALESCE(SUM(e.count_request), 0),
                  0,
                  NULL,
                  ?
           FROM usage_events e
           LEFT JOIN sources s ON s.id = e.source_id
           GROUP BY e.user_id, COALESCE(s.type, 'unknown')""",
        (ts,),
    )


def _backfill_canonical_usage(conn: sqlite3.Connection) -> None:
    if conn.execute("SELECT 1 FROM schema_meta WHERE key='canonical_usage_v2'").fetchone():
        return
    conn.execute(
        """UPDATE usage_events SET
             output_tokens = CASE
               WHEN source_type='grok' AND total_tokens>prompt_tokens THEN total_tokens-prompt_tokens
               WHEN completion_tokens>=reasoning_tokens THEN completion_tokens
               ELSE completion_tokens+reasoning_tokens END,
             visible_tokens = CASE
               WHEN source_type='grok' THEN completion_tokens
               ELSE MAX(completion_tokens-reasoning_tokens, 0) END,
             usage_status = CASE
               WHEN prompt_tokens>0 OR completion_tokens>0 OR reasoning_tokens>0 THEN 'complete'
               ELSE 'unknown' END
           WHERE output_tokens=0 AND (completion_tokens>0 OR reasoning_tokens>0)"""
    )
    for table, key in (("api_keys", "api_key_id"), ("credentials", "credential_id")):
        conn.execute(
            f"""UPDATE {table} SET
                 output_tokens=COALESCE((SELECT SUM(output_tokens) FROM usage_events WHERE {key}={table}.id), 0),
                 visible_tokens=COALESCE((SELECT SUM(visible_tokens) FROM usage_events WHERE {key}={table}.id), 0),
                 cache_write_tokens=COALESCE((SELECT SUM(cache_write_tokens) FROM usage_events WHERE {key}={table}.id), 0)"""
        )
    conn.execute("DELETE FROM provider_usage")
    conn.execute(
        """INSERT INTO provider_usage
           (user_id, provider, prompt_tokens, completion_tokens, output_tokens, visible_tokens,
            reasoning_tokens, cached_tokens, cache_write_tokens, audio_tokens, image_tokens,
            total_tokens, requests, cost_micros, last_model, updated_at)
           SELECT user_id, COALESCE(NULLIF(source_type,''),'unknown'), SUM(prompt_tokens),
                  SUM(completion_tokens), SUM(output_tokens), SUM(visible_tokens), SUM(reasoning_tokens),
                  SUM(cached_tokens), SUM(cache_write_tokens), SUM(audio_tokens), SUM(image_tokens),
                  SUM(CASE WHEN total_tokens>0 THEN total_tokens ELSE prompt_tokens+output_tokens END),
                   SUM(count_request), SUM(COALESCE(reported_cost_micros, estimated_cost_micros, 0)), NULL, ?
           FROM usage_events GROUP BY user_id, COALESCE(NULLIF(source_type,''),'unknown')""",
        (now(),),
    )
    conn.execute("DELETE FROM usage_hourly")
    conn.execute(
        "INSERT INTO schema_meta(key,value) VALUES('canonical_usage_v2',?)",
        (now(),),
    )


def _strip_openrouter_costs(conn: sqlite3.Connection) -> None:
    """One-shot: OpenRouter is per-account only and the user pays 1:1 on their
    own key, so its events keep tokens but never carry cost. Zeroes cost on
    openrouter events (direct and pool-fallback alike) and rebuilds the cost
    rollups that were inflated by them."""
    if conn.execute("SELECT 1 FROM schema_meta WHERE key='openrouter_uncosted_v1'").fetchone():
        return
    conn.execute(
        """UPDATE usage_events SET
             reported_cost_micros=NULL, estimated_cost_micros=0, pricing_known=0
           WHERE COALESCE(NULLIF(source_type, ''),
                          (SELECT type FROM sources WHERE id=usage_events.source_id),
                          'unknown')='openrouter'"""
    )
    conn.execute(
        """UPDATE api_keys SET cost_micros = COALESCE((
                SELECT SUM(COALESCE(e.reported_cost_micros, e.estimated_cost_micros, 0))
                FROM usage_events e WHERE e.api_key_id = api_keys.id), 0)"""
    )
    conn.execute(
        """UPDATE credentials SET cost_micros = COALESCE((
                SELECT SUM(COALESCE(e.reported_cost_micros, e.estimated_cost_micros, 0))
                FROM usage_events e WHERE e.credential_id = credentials.id), 0)"""
    )
    conn.execute("DELETE FROM provider_usage")
    conn.execute(
        """INSERT INTO provider_usage
           (user_id, provider, prompt_tokens, completion_tokens, output_tokens, visible_tokens,
            reasoning_tokens, cached_tokens, cache_write_tokens, audio_tokens, image_tokens,
            total_tokens, requests, cost_micros, last_model, updated_at)
           SELECT e.user_id,
                  COALESCE(NULLIF(e.source_type, ''), s.type, 'unknown'),
                  SUM(e.prompt_tokens), SUM(e.completion_tokens), SUM(e.output_tokens),
                  SUM(e.visible_tokens), SUM(e.reasoning_tokens), SUM(e.cached_tokens),
                  SUM(e.cache_write_tokens), SUM(e.audio_tokens), SUM(e.image_tokens),
                  SUM(CASE WHEN e.total_tokens>0 THEN e.total_tokens
                           ELSE e.prompt_tokens+e.output_tokens END),
                  SUM(e.count_request),
                  SUM(COALESCE(e.reported_cost_micros, e.estimated_cost_micros, 0)),
                  NULL, ?
           FROM usage_events e LEFT JOIN sources s ON s.id=e.source_id
           GROUP BY e.user_id, COALESCE(NULLIF(e.source_type, ''), s.type, 'unknown')""",
        (now(),),
    )
    conn.execute("DELETE FROM usage_hourly")
    conn.execute(
        """INSERT INTO usage_hourly
           (user_id, hour, source_id, model, prompt_tokens, completion_tokens, output_tokens,
            visible_tokens, reasoning_tokens, cached_tokens, cache_write_tokens, total_tokens,
            cost_micros, requests)
           SELECT user_id,
                  substr(created_at, 1, 13) || ':00:00Z',
                  COALESCE(source_id, 0),
                  COALESCE(model, ''),
                  SUM(prompt_tokens),
                  SUM(completion_tokens),
                  SUM(output_tokens),
                  SUM(visible_tokens),
                  SUM(reasoning_tokens),
                  SUM(cached_tokens),
                  SUM(cache_write_tokens),
                  SUM(CASE WHEN total_tokens>0 THEN total_tokens ELSE prompt_tokens+output_tokens END),
                  SUM(COALESCE(reported_cost_micros, estimated_cost_micros, 0)),
                  SUM(count_request)
           FROM usage_events
           GROUP BY 1, 2, 3, 4"""
    )
    conn.execute("INSERT INTO schema_meta(key,value) VALUES('openrouter_uncosted_v1',?)", (now(),))


def _backfill_hourly(conn: sqlite3.Connection) -> None:
    n = conn.execute("SELECT COUNT(*) FROM usage_hourly").fetchone()[0]
    if n:
        return
    conn.execute(
        """INSERT INTO usage_hourly
           (user_id, hour, source_id, model, prompt_tokens, completion_tokens, output_tokens,
            visible_tokens, reasoning_tokens, cached_tokens, cache_write_tokens, total_tokens,
            cost_micros, requests)
           SELECT user_id,
                  substr(created_at, 1, 13) || ':00:00Z',
                  COALESCE(source_id, 0),
                  COALESCE(model, ''),
                   SUM(prompt_tokens),
                   SUM(completion_tokens),
                   SUM(output_tokens),
                   SUM(visible_tokens),
                   SUM(reasoning_tokens),
                   SUM(cached_tokens),
                   SUM(cache_write_tokens),
                   SUM(CASE WHEN total_tokens>0 THEN total_tokens ELSE prompt_tokens+output_tokens END),
                   SUM(COALESCE(reported_cost_micros, estimated_cost_micros, 0)),
                    SUM(count_request)
           FROM usage_events
           GROUP BY 1, 2, 3, 4"""
    )


def _rollback(conn) -> None:
    """Roll back without masking the caller's exception.

    If the rollback itself fails the session state is unknown; close it so the
    next checkout reconnects instead of continuing an open transaction.
    """
    try:
        conn.rollback()
    except Exception:
        logger.exception("Database rollback failed; discarding the connection")
        try:
            conn.close()
        except Exception:
            pass


@contextmanager
def tx():
    conn = connect()
    pooled = hasattr(conn, "checkout")
    if pooled:
        borrowed = conn.checkout()
        try:
            yield borrowed
            borrowed.commit()
        except BaseException:
            # BaseException too: a GeneratorExit/KeyboardInterrupt must not
            # return a connection mid-transaction for the next borrower to
            # commit.
            _rollback(borrowed)
            raise
        finally:
            conn.release(borrowed)
        return
    with _lock:
        try:
            if hasattr(conn, "ensure_connected"):
                conn.ensure_connected()
            yield conn
            conn.commit()
        except BaseException:
            _rollback(conn)
            raise


def execute(sql: str, args=()):
    with tx() as conn:
        return conn.execute(sql, args)


def fetchone(sql: str, args=()):
    with tx() as conn:
        return conn.execute(sql, args).fetchone()


def fetchall(sql: str, args=()):
    with tx() as conn:
        return conn.execute(sql, args).fetchall()


def last_id(conn) -> int:
    sql = "SELECT last_insert_rowid()" if isinstance(conn, sqlite3.Connection) else "SELECT LAST_INSERT_ID()"
    row = conn.execute(sql).fetchone()
    return int(row[0]) if row and row[0] else 0


def record_usage(
    user_id: int,
    source_id: int | None,
    credential_id: int | None,
    model: str,
    usage: dict,
    at: str | None = None,
    *,
    key_id: int | None = None,
    provider: str = "",
    requested_model: str = "",
    request_status: str = "success",
    count_request: bool = True,
) -> None:
    def bounded(value) -> int:
        try:
            parsed = int(value or 0)
        except OverflowError:
            try:
                return SQLITE_INT_MAX if value > 0 else 0
            except (TypeError, ValueError):
                return 0
        except (TypeError, ValueError):
            return 0
        return min(max(parsed, 0), SQLITE_INT_MAX)

    def token(name: str) -> int:
        return bounded(usage.get(name))

    pt = token("prompt_tokens")
    ct = token("completion_tokens")
    rt = token("reasoning_tokens")
    output = token("output_tokens") or max(ct, rt)
    visible = min(token("visible_tokens") if "visible_tokens" in usage else max(output - rt, 0), output)
    cached = min(token("cached_tokens"), pt)
    cache_write = min(token("cache_write_tokens"), max(pt - cached, 0))
    audio = token("audio_tokens")
    image = token("image_tokens")
    total = _event_total(pt, output, token("total_tokens"))
    reported_raw = usage.get("cost_micros")
    reported_cost = bounded(reported_raw) if reported_raw is not None else None
    usage_status = str(usage.get("usage_status") or "unknown")
    if usage_status not in ("complete", "partial", "unknown"):
        usage_status = "unknown"
    request_status = request_status if request_status in ("success", "failed", "cancelled") else "failed"
    request_inc = 1 if count_request else 0
    ts = at or now()
    week = iso_week_start()
    model = model or ""
    provider = (provider or "")[:32]
    with tx() as conn:
        source = conn.execute("SELECT type, name FROM sources WHERE id=?", (source_id,)).fetchone() if source_id else None
        provider = provider or (source["type"] if source else "unknown")
        source_name = source["name"] if source else provider
        # OpenRouter is per-account only: every call is billed 1:1 on the
        # caller's own key (direct openrouter/... ids and pool-model fallback
        # alike), so tokens are counted but cost is not. Cost views must only
        # reflect what the shared pool/site accounts actually burn.
        track_cost = provider != "openrouter"
        price = (
            conn.execute(
                "SELECT * FROM model_prices WHERE provider=? AND model=?",
                (provider, model),
            ).fetchone()
            if track_cost
            else None
        )
        if reported_cost is not None and not track_cost:
            reported_cost = None
        estimated_cost = _estimated_cost(price, pt, output, visible, rt, cached, cache_write) if price else 0
        effective_cost = reported_cost if reported_cost is not None else estimated_cost
        conn.execute(
            """INSERT INTO usage_events
               (user_id, api_key_id, source_id, source_type, source_name, credential_id,
                 model, requested_model, prompt_tokens, completion_tokens, output_tokens, visible_tokens,
                 reasoning_tokens, cached_tokens, cache_write_tokens, audio_tokens, image_tokens, total_tokens,
                  reported_cost_micros, estimated_cost_micros, pricing_known, usage_status, request_status,
                  count_request, provider_request_id, created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                user_id, key_id, source_id, provider, source_name, credential_id,
                model, requested_model or model, pt, ct, output, visible, rt, cached, cache_write,
                 audio, image, total, reported_cost, estimated_cost, 1 if price else 0,
                 usage_status, request_status, request_inc,
                 str(usage.get("provider_request_id") or "")[:128], ts,
            ),
        )
        hour = ts[:13] + ":00:00Z"
        conn.execute(
            """INSERT INTO usage_hourly
               (user_id, hour, source_id, model, prompt_tokens, completion_tokens, output_tokens,
                visible_tokens, reasoning_tokens, cached_tokens, cache_write_tokens, total_tokens,
                cost_micros, requests)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(user_id, hour, source_id, model) DO UPDATE SET
                  prompt_tokens=MIN(9223372036854775807, prompt_tokens+excluded.prompt_tokens),
                   completion_tokens=MIN(9223372036854775807, completion_tokens+excluded.completion_tokens),
                   output_tokens=MIN(9223372036854775807, output_tokens+excluded.output_tokens),
                   visible_tokens=MIN(9223372036854775807, visible_tokens+excluded.visible_tokens),
                   reasoning_tokens=MIN(9223372036854775807, reasoning_tokens+excluded.reasoning_tokens),
                   cached_tokens=MIN(9223372036854775807, cached_tokens+excluded.cached_tokens),
                   cache_write_tokens=MIN(9223372036854775807, cache_write_tokens+excluded.cache_write_tokens),
                   total_tokens=MIN(9223372036854775807, total_tokens+excluded.total_tokens),
                   cost_micros=MIN(9223372036854775807, cost_micros+excluded.cost_micros),
                   requests=MIN(9223372036854775807, requests+excluded.requests)""",
            (user_id, hour, source_id or 0, model, pt, ct, output, visible, rt, cached,
             cache_write, total, effective_cost, request_inc),
        )
        if key_id:
            conn.execute(
                """UPDATE api_keys SET
                     prompt_tokens = MIN(9223372036854775807, prompt_tokens + ?),
                     completion_tokens = MIN(9223372036854775807, completion_tokens + ?),
                     output_tokens = MIN(9223372036854775807, output_tokens + ?),
                     visible_tokens = MIN(9223372036854775807, visible_tokens + ?),
                     reasoning_tokens = MIN(9223372036854775807, reasoning_tokens + ?),
                     cached_tokens = MIN(9223372036854775807, cached_tokens + ?),
                     cache_write_tokens = MIN(9223372036854775807, cache_write_tokens + ?),
                     audio_tokens = MIN(9223372036854775807, audio_tokens + ?),
                     image_tokens = MIN(9223372036854775807, image_tokens + ?),
                     total_tokens = MIN(9223372036854775807, total_tokens + ?),
                     requests = MIN(9223372036854775807, requests + ?),
                     cost_micros = MIN(9223372036854775807, cost_micros + ?),
                     last_model = ?,
                     usage_updated_at = ?
                   WHERE id=?""",
                (pt, ct, output, visible, rt, cached, cache_write, audio, image, total,
                 request_inc, effective_cost, model, ts, key_id),
            )
        if provider:
            conn.execute(
                """INSERT INTO provider_usage
                    (user_id, provider, prompt_tokens, completion_tokens, output_tokens, visible_tokens,
                     reasoning_tokens, cached_tokens, cache_write_tokens, audio_tokens, image_tokens, total_tokens, requests,
                     cost_micros, last_model, updated_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(user_id, provider) DO UPDATE SET
                      prompt_tokens = MIN(9223372036854775807, prompt_tokens + excluded.prompt_tokens),
                       completion_tokens = MIN(9223372036854775807, completion_tokens + excluded.completion_tokens),
                       output_tokens = MIN(9223372036854775807, output_tokens + excluded.output_tokens),
                       visible_tokens = MIN(9223372036854775807, visible_tokens + excluded.visible_tokens),
                       reasoning_tokens = MIN(9223372036854775807, reasoning_tokens + excluded.reasoning_tokens),
                       cached_tokens = MIN(9223372036854775807, cached_tokens + excluded.cached_tokens),
                       cache_write_tokens = MIN(9223372036854775807, cache_write_tokens + excluded.cache_write_tokens),
                      audio_tokens = MIN(9223372036854775807, audio_tokens + excluded.audio_tokens),
                      image_tokens = MIN(9223372036854775807, image_tokens + excluded.image_tokens),
                      total_tokens = MIN(9223372036854775807, total_tokens + excluded.total_tokens),
                       requests = MIN(9223372036854775807, requests + excluded.requests),
                      cost_micros = MIN(9223372036854775807, cost_micros + excluded.cost_micros),
                     last_model = excluded.last_model,
                     updated_at = excluded.updated_at""",
                (user_id, provider, pt, ct, output, visible, rt, cached, cache_write, audio,
                 image, total, request_inc, effective_cost, model, ts),
            )
        if credential_id:
            # One conditional UPDATE instead of a read-then-write: two requests
            # finishing on the same credential concurrently would otherwise lose
            # one increment (SQLite's writer lock had masked the race).
            # week_start is assigned last: MariaDB evaluates SET left to right,
            # so the CASEs must still see the previous week.
            conn.execute(
                """UPDATE credentials SET
                     week_tokens=CASE WHEN week_start=? THEN MIN(9223372036854775807, week_tokens+?) ELSE ? END,
                     week_requests=CASE WHEN week_start=? THEN MIN(9223372036854775807, week_requests+?) ELSE ? END,
                     week_start=?,
                     prompt_tokens = MIN(9223372036854775807, prompt_tokens + ?),
                     completion_tokens = MIN(9223372036854775807, completion_tokens + ?),
                     output_tokens = MIN(9223372036854775807, output_tokens + ?),
                     visible_tokens = MIN(9223372036854775807, visible_tokens + ?),
                     reasoning_tokens = MIN(9223372036854775807, reasoning_tokens + ?),
                     cached_tokens = MIN(9223372036854775807, cached_tokens + ?),
                     cache_write_tokens = MIN(9223372036854775807, cache_write_tokens + ?),
                     total_tokens = MIN(9223372036854775807, total_tokens + ?),
                     requests = MIN(9223372036854775807, requests + ?),
                     cost_micros = MIN(9223372036854775807, cost_micros + ?)
                   WHERE id=?""",
                (
                    week, total, total, week, request_inc, request_inc, week,
                    pt, ct, output, visible, rt, cached, cache_write, total, request_inc,
                    effective_cost, credential_id,
                ),
            )


def _event_total(prompt: int, output: int, total: int) -> int:
    # A provider total that leaves out reasoning (or anything else already in
    # prompt/output) would make totals disagree with the token breakdown.
    return min(max(total, prompt + output), SQLITE_INT_MAX)


def _estimated_cost(
    price, prompt: int, output: int, visible: int, reasoning: int, cached: int, cache_write: int
) -> int:
    cached = min(max(cached, 0), max(prompt, 0))
    cache_write = min(max(cache_write, 0), max(prompt - cached, 0))
    normal_input = max(prompt - cached - cache_write, 0)
    tiers = []
    raw_tiers = _pget(price, "tiers_json")
    if raw_tiers:
        try:
            tiers = json.loads(raw_tiers)
        except (TypeError, json.JSONDecodeError):
            tiers = []
    tier = None
    for candidate in tiers if isinstance(tiers, list) else []:
        if isinstance(candidate, dict) and int(candidate.get("min_prompt_tokens") or 0) <= prompt:
            if tier is None or int(candidate.get("min_prompt_tokens") or 0) > int(tier.get("min_prompt_tokens") or 0):
                tier = candidate
    threshold = _pget(price, "long_context_threshold")
    long_context = tier is None and bool(threshold and prompt >= int(threshold))
    input_rate = int(
        _pget(price, "long_input_micros_per_million")
        if long_context and _pget(price, "long_input_micros_per_million") is not None
        else _pget(price, "input_micros_per_million") or 0
    )
    output_rate = int(
        _pget(price, "long_output_micros_per_million")
        if long_context and _pget(price, "long_output_micros_per_million") is not None
        else _pget(price, "output_micros_per_million") or 0
    )
    cache_known = bool(_pget(price, "long_cached_input_known" if long_context else "cached_input_known"))
    cached_rate = int(
        _pget(price, "long_cached_input_micros_per_million")
        if long_context and cache_known
        else _pget(price, "cached_input_micros_per_million") if cache_known
        else input_rate
    )
    write_known = bool(_pget(price, "cache_write_known"))
    write_rate = int(_pget(price, "cache_write_micros_per_million") or 0) if write_known else input_rate
    if tier is not None:
        input_rate = int(tier.get("input_rate") if tier.get("input_rate") is not None else input_rate)
        output_rate = int(tier.get("output_rate") if tier.get("output_rate") is not None else output_rate)
        cached_rate = int(tier.get("cached_rate") if tier.get("cached_rate") is not None else cached_rate)
        write_rate = int(tier.get("cache_write_rate") if tier.get("cache_write_rate") is not None else write_rate)
    reasoning_known = bool(_pget(price, "reasoning_known"))
    reasoning_rate = int(_pget(price, "reasoning_micros_per_million") or 0)
    output = max(int(output or 0), 0)
    visible = min(max(int(visible or 0), 0), output)
    reasoning = min(max(int(reasoning or 0), 0), output)
    other_output = max(output - visible - reasoning, 0)
    numerator = (
        normal_input * input_rate
        + cached * cached_rate
        + cache_write * write_rate
        + (output * output_rate if not reasoning_known else (visible + other_output) * output_rate + reasoning * reasoning_rate)
    )
    return min(SQLITE_INT_MAX, max(0, (numerator + 500_000) // 1_000_000))


def save_model_price(
    provider: str,
    model: str,
    input_rate: int,
    cached_rate: int | None,
    output_rate: int,
    reasoning_rate: int | None,
    *,
    cache_write_rate: int | None = None,
    tiers: list[dict] | None = None,
    long_context_threshold: int | None = None,
    long_input_rate: int | None = None,
    long_cached_rate: int | None = None,
    long_output_rate: int | None = None,
    origin: str = "manual",
    openrouter_id: str | None = None,
    conn: sqlite3.Connection | None = None,
) -> None:
    """Upsert a price row and reprice matching estimated history.

    Manual rows win: an 'openrouter'-origin sync never overwrites a row whose
    current origin is 'manual'. The dashboard documents that contract.
    """
    ts = now()
    tiers_json = json.dumps(
        [
            {
                "min_prompt_tokens": int(t.get("min_prompt_tokens") or 0),
                "input_rate": t.get("input_rate"),
                "cached_rate": t.get("cached_rate"),
                "cache_write_rate": t.get("cache_write_rate"),
                "output_rate": t.get("output_rate"),
            }
            for t in (tiers or [])
            if isinstance(t, dict) and int(t.get("min_prompt_tokens") or 0) > 0
        ]
    )
    transaction = tx() if conn is None else nullcontext(conn)
    with transaction as write_conn:
        write_conn.execute(
            """INSERT INTO model_prices
               (provider, model, input_micros_per_million, cached_input_micros_per_million,
                cache_write_micros_per_million, output_micros_per_million, reasoning_micros_per_million,
                cached_input_known, cache_write_known, reasoning_known, long_context_threshold,
                long_input_micros_per_million, long_cached_input_micros_per_million,
                long_output_micros_per_million, long_cached_input_known, tiers_json,
                origin, openrouter_id, synced_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(provider, model) DO UPDATE SET
                  input_micros_per_million=excluded.input_micros_per_million,
                  cached_input_micros_per_million=excluded.cached_input_micros_per_million,
                  cache_write_micros_per_million=excluded.cache_write_micros_per_million,
                  output_micros_per_million=excluded.output_micros_per_million,
                  reasoning_micros_per_million=excluded.reasoning_micros_per_million,
                  cached_input_known=excluded.cached_input_known,
                  cache_write_known=excluded.cache_write_known,
                  reasoning_known=excluded.reasoning_known,
                  long_context_threshold=excluded.long_context_threshold,
                  long_input_micros_per_million=excluded.long_input_micros_per_million,
                  long_cached_input_micros_per_million=excluded.long_cached_input_micros_per_million,
                  long_output_micros_per_million=excluded.long_output_micros_per_million,
                  long_cached_input_known=excluded.long_cached_input_known,
                  tiers_json=excluded.tiers_json,
                  origin=excluded.origin,
                  openrouter_id=excluded.openrouter_id,
                  synced_at=excluded.synced_at,
                  updated_at=excluded.updated_at
               WHERE model_prices.origin != 'manual' OR excluded.origin = 'manual'""",
            (
                provider, model, input_rate, cached_rate or 0, cache_write_rate or 0,
                output_rate, reasoning_rate or 0,
                1 if cached_rate is not None else 0, 1 if cache_write_rate is not None else 0,
                1 if reasoning_rate is not None else 0,
                long_context_threshold, long_input_rate, long_cached_rate, long_output_rate,
                1 if long_cached_rate is not None else 0, tiers_json,
                origin[:32], openrouter_id, ts if origin == "openrouter" else None, ts,
            ),
        )
        persisted_price = write_conn.execute(
            "SELECT * FROM model_prices WHERE provider=? AND model=?",
            (provider, model),
        ).fetchone()
        if persisted_price is None:
            return
        if provider == "openrouter":
            # The price row is kept for reference, but OpenRouter events are
            # personal (per-account, user pays 1:1): repricing history must
            # never attach estimated cost to them.
            return
        rows = write_conn.execute(
            """SELECT e.id, e.user_id, e.api_key_id, e.credential_id, e.reported_cost_micros,
                      e.estimated_cost_micros, e.prompt_tokens, e.completion_tokens,
                      e.output_tokens, e.visible_tokens, e.reasoning_tokens, e.cached_tokens,
                      e.cache_write_tokens
               FROM usage_events e LEFT JOIN sources s ON s.id=e.source_id
               WHERE COALESCE(NULLIF(e.source_type, ''), s.type, 'unknown')=? AND e.model=?""",
            (provider, model),
        ).fetchall()
        updates = []
        key_deltas: dict[int, int] = {}
        user_deltas: dict[int, int] = {}
        cred_deltas: dict[int, int] = {}
        for row in rows:
            output, visible = _canonical_output(row)
            estimate = _estimated_cost(
                persisted_price,
                int(row["prompt_tokens"] or 0),
                output,
                visible,
                int(row["reasoning_tokens"] or 0),
                int(row["cached_tokens"] or 0),
                int(row["cache_write_tokens"] or 0),
            )
            old_effective = row["reported_cost_micros"] if row["reported_cost_micros"] is not None else int(row["estimated_cost_micros"] or 0)
            new_effective = row["reported_cost_micros"] if row["reported_cost_micros"] is not None else estimate
            delta = int(new_effective) - int(old_effective)
            updates.append((estimate, row["id"]))
            user_deltas[row["user_id"]] = user_deltas.get(row["user_id"], 0) + delta
            if row["api_key_id"]:
                key_deltas[row["api_key_id"]] = key_deltas.get(row["api_key_id"], 0) + delta
            if row["credential_id"]:
                cred_deltas[row["credential_id"]] = cred_deltas.get(row["credential_id"], 0) + delta
        write_conn.executemany(
            "UPDATE usage_events SET estimated_cost_micros=?, pricing_known=1 WHERE id=?",
            updates,
        )
        write_conn.executemany(
            "UPDATE api_keys SET cost_micros=MAX(0, cost_micros+?) WHERE id=?",
            [(delta, key_id) for key_id, delta in key_deltas.items()],
        )
        write_conn.executemany(
            """UPDATE provider_usage SET cost_micros=MAX(0, cost_micros+?)
               WHERE user_id=? AND provider=?""",
            [(delta, user_id, provider) for user_id, delta in user_deltas.items()],
        )
        write_conn.executemany(
            "UPDATE credentials SET cost_micros=MAX(0, cost_micros+?) WHERE id=?",
            [(delta, cred_id) for cred_id, delta in cred_deltas.items()],
        )


def delete_model_price(provider: str, model: str) -> None:
    with tx() as conn:
        conn.execute("DELETE FROM model_prices WHERE provider=? AND model=?", (provider, model))
        rows = conn.execute(
            """SELECT e.id, e.user_id, e.api_key_id, e.credential_id, e.reported_cost_micros, e.estimated_cost_micros
               FROM usage_events e LEFT JOIN sources s ON s.id=e.source_id
               WHERE COALESCE(NULLIF(e.source_type, ''), s.type, 'unknown')=? AND e.model=?""",
            (provider, model),
        ).fetchall()
        conn.executemany(
            "UPDATE usage_events SET estimated_cost_micros=0, pricing_known=0 WHERE id=?",
            [(row["id"],) for row in rows],
        )
        key_deltas: dict[int, int] = {}
        user_deltas: dict[int, int] = {}
        cred_deltas: dict[int, int] = {}
        for row in rows:
            if row["reported_cost_micros"] is not None:
                continue
            delta = -int(row["estimated_cost_micros"] or 0)
            user_deltas[row["user_id"]] = user_deltas.get(row["user_id"], 0) + delta
            if row["api_key_id"]:
                key_deltas[row["api_key_id"]] = key_deltas.get(row["api_key_id"], 0) + delta
            if row["credential_id"]:
                cred_deltas[row["credential_id"]] = cred_deltas.get(row["credential_id"], 0) + delta
        conn.executemany(
            "UPDATE api_keys SET cost_micros=MAX(0, cost_micros+?) WHERE id=?",
            [(delta, key_id) for key_id, delta in key_deltas.items()],
        )
        conn.executemany(
            """UPDATE provider_usage SET cost_micros=MAX(0, cost_micros+?)
               WHERE user_id=? AND provider=?""",
            [(delta, user_id, provider) for user_id, delta in user_deltas.items()],
        )
        conn.executemany(
            "UPDATE credentials SET cost_micros=MAX(0, cost_micros+?) WHERE id=?",
            [(delta, cred_id) for cred_id, delta in cred_deltas.items()],
        )


def provider_usage_for(user_id: int):
    return fetchall(
        "SELECT * FROM provider_usage WHERE user_id=? ORDER BY total_tokens DESC, provider",
        (user_id,),
    )
