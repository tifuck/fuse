"""Schema bootstrap and in-place upgrades behind db._init (chat_store.init_schema).

Production schemas come from scripts/sql/schema.sql (scripts/init_db.py); this
creates and upgrades the base tables on fresh test schemas and keeps old
columns and rollups consistent.
"""
from __future__ import annotations

import sqlite3

from .db import _canonical_output, _ensure_column, _estimated_cost, now


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
            created_at TEXT NOT NULL,
            token_hash TEXT NOT NULL DEFAULT ''
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
    # Covers the per-account totals /sources sums (scripts/sql/2026-10-24-usage-credential-cover.sql).
    conn.execute(
        "CREATE INDEX IF NOT EXISTS usage_events_credential"
        " ON usage_events (credential_id, pricing_known, total_tokens, estimated_cost_micros)"
    )
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
    _ensure_column(conn, "registration_whitelist", "token_hash", "TEXT NOT NULL DEFAULT ''")
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
    # Permission mode new chats start in: ask / auto / bypass. A project's own
    # default is '' (use the account's default for project sessions).
    _ensure_column(conn, "user_projects", "default_permissions", "VARCHAR(16) NOT NULL DEFAULT ''")
    _ensure_column(conn, "user_agent_settings", "default_chat_permissions", "VARCHAR(16) NOT NULL DEFAULT 'ask'")
    _ensure_column(conn, "user_agent_settings", "default_project_permissions", "VARCHAR(16) NOT NULL DEFAULT 'ask'")
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
        CREATE TABLE IF NOT EXISTS chat_drafts (
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            draft_key TEXT NOT NULL,
            text TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (user_id, draft_key)
        );
        CREATE TABLE IF NOT EXISTS chat_images (
            thread_id INTEGER NOT NULL REFERENCES chat_threads(id) ON DELETE CASCADE,
            sha TEXT NOT NULL,
            user_id INTEGER NOT NULL,
            url TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (thread_id, sha)
        );
        CREATE INDEX IF NOT EXISTS chat_images_user_sha ON chat_images (user_id, sha);
        CREATE TABLE IF NOT EXISTS chat_queue (
            thread_id INTEGER NOT NULL REFERENCES chat_threads(id) ON DELETE CASCADE,
            id TEXT NOT NULL,
            user_id INTEGER NOT NULL,
            body TEXT NOT NULL,
            priority INTEGER NOT NULL DEFAULT 0,
            created INTEGER NOT NULL DEFAULT 0,
            held INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (thread_id, id)
        );
        CREATE INDEX IF NOT EXISTS chat_queue_held ON chat_queue (held, thread_id);
        """
    )


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
