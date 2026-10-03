"""Per-user automatic archiving and cleanup of chats and projects.

A chat (project sessions included) is inactive once nothing has happened in
it for the user's window: no message, no run, no restore. Running work,
live background commands and scheduled-task threads are never touched.
Projects archive when neither they nor any of their sessions changed in the
window, and take their open sessions with them (restoring brings those back).
Deletion only ever removes items that are already archived, counted from
when they were archived. Instance-wide ceilings (RETENTION_FORCE_*) apply
on top of what users choose and can only shorten it.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone

from . import db
from .config import settings

logger = logging.getLogger(__name__)
SWEEP_SECONDS = 600
MAX_ARCHIVE_HOURS = 24 * 365
MAX_DELETE_DAYS = 3650
_last_sweep = 0.0


def init_schema(conn):
    db._ensure_column(conn, "user_projects", "color", "TEXT NOT NULL DEFAULT ''")
    db._ensure_column(conn, "user_projects", "logo_rev", "INTEGER NOT NULL DEFAULT 0")
    db._ensure_column(conn, "user_projects", "archived", "INTEGER NOT NULL DEFAULT 0")
    db._ensure_column(conn, "user_projects", "archived_at", "TEXT NOT NULL DEFAULT ''")
    db._ensure_column(conn, "chat_threads", "archived_at", "TEXT NOT NULL DEFAULT ''")
    db._ensure_column(conn, "chat_threads", "active_at", "TEXT NOT NULL DEFAULT ''")
    db._ensure_column(conn, "chat_threads", "archived_by_project", "INTEGER NOT NULL DEFAULT 0")
    db._ensure_column(conn, "users", "retention_archive_hours", "INTEGER NOT NULL DEFAULT 0")
    db._ensure_column(conn, "users", "retention_archive_chats", "INTEGER NOT NULL DEFAULT 1")
    db._ensure_column(conn, "users", "retention_archive_projects", "INTEGER NOT NULL DEFAULT 1")
    db._ensure_column(conn, "users", "retention_delete_days", "INTEGER NOT NULL DEFAULT 0")
    # On MariaDB this only verifies the table: scripts.migrate_projects creates it.
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS project_logos (
            project_id INTEGER PRIMARY KEY REFERENCES user_projects(id) ON DELETE CASCADE,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            mime TEXT NOT NULL,
            data BLOB NOT NULL,
            updated_at TEXT NOT NULL
        );
    """)


class RetentionError(ValueError):
    pass


def _limits() -> dict:
    return {
        "archive_days": max(0, int(getattr(settings, "retention_force_archive_days", 0) or 0)),
        "delete_days": max(0, int(getattr(settings, "retention_force_delete_days", 0) or 0)),
    }


def get_settings(user_id: int) -> dict:
    row = db.fetchone(
        """SELECT retention_archive_hours, retention_archive_chats, retention_archive_projects, retention_delete_days
           FROM users WHERE id=?""",
        (user_id,),
    )
    return {
        "archive_hours": int(row["retention_archive_hours"] or 0) if row else 0,
        "archive_chats": bool(row["retention_archive_chats"]) if row else True,
        "archive_projects": bool(row["retention_archive_projects"]) if row else True,
        "delete_days": int(row["retention_delete_days"] or 0) if row else 0,
        "limits": _limits(),
    }


def _bounded_int(raw, field: str, maximum: int) -> int:
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 0 or raw > maximum:
        raise RetentionError(f"{field} must be a whole number from 0 to {maximum}")
    return raw


def set_settings(user_id: int, raw: dict) -> dict:
    allowed = {"archive_hours", "archive_chats", "archive_projects", "delete_days"}
    if not isinstance(raw, dict) or set(raw) - allowed:
        raise RetentionError("unsupported cleanup fields")
    current = get_settings(user_id)
    hours = _bounded_int(raw["archive_hours"], "archive_hours", MAX_ARCHIVE_HOURS) if "archive_hours" in raw else current["archive_hours"]
    days = _bounded_int(raw["delete_days"], "delete_days", MAX_DELETE_DAYS) if "delete_days" in raw else current["delete_days"]
    flags = {}
    for key in ("archive_chats", "archive_projects"):
        value = raw.get(key, current[key])
        if not isinstance(value, bool):
            raise RetentionError(f"{key} must be true or false")
        flags[key] = value
    db.execute(
        """UPDATE users SET retention_archive_hours=?, retention_archive_chats=?, retention_archive_projects=?,
           retention_delete_days=? WHERE id=?""",
        (hours, int(flags["archive_chats"]), int(flags["archive_projects"]), days, user_id),
    )
    return get_settings(user_id)


def effective(prefs: dict) -> tuple[int, int]:
    """(archive window in hours, delete window in days); 0 means off."""
    limits = prefs.get("limits") or _limits()
    hours = int(prefs.get("archive_hours") or 0)
    if limits["archive_days"]:
        hours = min(hours, limits["archive_days"] * 24) if hours else limits["archive_days"] * 24
    days = int(prefs.get("delete_days") or 0)
    if limits["delete_days"]:
        days = min(days, limits["delete_days"]) if days else limits["delete_days"]
    return hours, days


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _latest(*values) -> str:
    return max((v for v in values if isinstance(v, str) and v), default="")


def _thread_activity(conn, user_id: int) -> tuple[list[dict], set[int]]:
    rows = conn.execute(
        """SELECT id, kind, project_id, archived, archived_at, archived_by_project, schedule_id,
                  created_at, updated_at, active_at
           FROM chat_threads WHERE user_id=?""",
        (user_id,),
    ).fetchall()
    runs = {int(r["thread_id"]): r for r in conn.execute(
        """SELECT thread_id, MAX(updated_at) AS last_at,
                  SUM(CASE WHEN status IN ('queued','running') THEN 1 ELSE 0 END) AS live
           FROM chat_runs WHERE user_id=? GROUP BY thread_id""",
        (user_id,),
    ).fetchall()}
    busy = {tid for tid, r in runs.items() if int(r["live"] or 0)}
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='command_jobs'").fetchone():
        for r in conn.execute(
            """SELECT thread_id, COUNT(*) AS n FROM command_jobs
               WHERE user_id=? AND thread_id IS NOT NULL AND status IN ('queued','running','cancelling') GROUP BY thread_id""",
            (user_id,),
        ).fetchall():
            busy.add(int(r["thread_id"]))
    out = []
    for row in rows:
        tid = int(row["id"])
        run = runs.get(tid)
        # active_at is only written since cleanup existed; older rows fall back
        # to updated_at, which also moves on renames and compaction (later, so
        # never archives early).
        last = _latest(row["active_at"] or row["updated_at"], row["created_at"], run["last_at"] if run else "")
        out.append({**dict(row), "id": tid, "last_active": last})
    return out, busy


def sweep_user(user_id: int, prefs: dict | None = None, *, now: datetime | None = None) -> dict:
    """Apply one user's policy once. Returns counts of what changed."""
    from . import chat_store as store, chats, hosts

    prefs = prefs or get_settings(user_id)
    hours, days = effective(prefs)
    limits = prefs.get("limits") or _limits()
    forced_archive = bool(limits["archive_days"])
    archive_chats = prefs.get("archive_chats", True) or forced_archive
    archive_projects = prefs.get("archive_projects", True) or forced_archive
    now = now or datetime.now(timezone.utc)
    stats = {"archived_chats": 0, "archived_projects": 0, "deleted_chats": 0, "deleted_projects": 0}
    if not hours and not days:
        return stats
    cutoff = _iso(now - timedelta(hours=hours)) if hours else ""
    delete_cutoff = _iso(now - timedelta(days=days)) if days else ""

    with db.tx() as conn:
        conn.execute("BEGIN")
        threads, busy = _thread_activity(conn, user_id)
        projects = [dict(r) for r in conn.execute(
            "SELECT id, host_id, archived, archived_at, created_at, updated_at FROM user_projects WHERE user_id=?",
            (user_id,),
        ).fetchall()]

    if cutoff:
        if archive_projects:
            by_project: dict[int, list[dict]] = {}
            for t in threads:
                if t["kind"] == "project" and t["project_id"] is not None:
                    by_project.setdefault(int(t["project_id"]), []).append(t)
            for p in projects:
                if p["archived"]:
                    continue
                sessions = by_project.get(int(p["id"]), [])
                if any(t["id"] in busy for t in sessions):
                    continue
                last = _latest(p["updated_at"], p["created_at"], *(t["last_active"] for t in sessions))
                if last and last < cutoff:
                    with store.transaction() as conn:
                        if hosts.set_project_archived(conn, user_id, int(p["id"]), True, skip_running=True) is not None:
                            stats["archived_projects"] += 1
                            p["archived"] = 1
                    for t in sessions:
                        t["archived"] = 1
        if archive_chats:
            for t in threads:
                if t["archived"] or t["schedule_id"] is not None or t["id"] in busy:
                    continue
                if not t["last_active"] or t["last_active"] >= cutoff:
                    continue
                ts = db.now()
                with store.transaction() as conn:
                    cur = conn.execute(
                        """UPDATE chat_threads SET archived=1, archived_at=?, archived_by_project=0, revision=revision+1
                           WHERE id=? AND user_id=? AND archived=0""",
                        (ts, t["id"], user_id),
                    )
                    if cur.rowcount:
                        store.thread_event(conn, user_id, t["id"])
                        stats["archived_chats"] += 1

    if delete_cutoff:
        for p in projects:
            if not p["archived"]:
                continue
            since = p["archived_at"] or p["updated_at"] or p["created_at"]
            if not since or since >= delete_cutoff:
                continue
            open_sessions = db.fetchone(
                "SELECT COUNT(*) AS n FROM chat_threads WHERE user_id=? AND project_id=? AND archived=0",
                (user_id, p["id"]),
            )
            if int(open_sessions["n"] if open_sessions else 0):
                continue
            if hosts.delete_project(user_id, int(p["host_id"]), int(p["id"]), sessions="delete"):
                stats["deleted_projects"] += 1
        for t in threads:
            if not t["archived"] or t["id"] in busy:
                continue
            since = t["archived_at"] or t["updated_at"]
            if not since or since >= delete_cutoff:
                continue
            fresh = db.fetchone("SELECT archived FROM chat_threads WHERE id=? AND user_id=?", (t["id"], user_id))
            if fresh is None or not fresh["archived"]:
                continue
            if chats.delete_thread(user_id, t["id"]):
                stats["deleted_chats"] += 1
    if any(stats.values()):
        logger.info("retention user=%s %s", user_id, stats)
    return stats


def sweep(*, now: datetime | None = None) -> dict:
    limits = _limits()
    if limits["archive_days"] or limits["delete_days"]:
        rows = db.fetchall("SELECT id FROM users")
    else:
        rows = db.fetchall("SELECT id FROM users WHERE retention_archive_hours>0 OR retention_delete_days>0")
    total = {"archived_chats": 0, "archived_projects": 0, "deleted_chats": 0, "deleted_projects": 0}
    for row in rows:
        try:
            stats = sweep_user(int(row["id"]), now=now)
        except Exception:
            logger.exception("retention sweep failed for user %s", row["id"])
            continue
        for key, value in stats.items():
            total[key] += value
    return total


async def tick() -> None:
    """Called from the maintenance loop every minute; sweeps every 10."""
    global _last_sweep
    if time.monotonic() - _last_sweep < SWEEP_SECONDS:
        return
    _last_sweep = time.monotonic()
    await asyncio.to_thread(sweep)
