"""In-process scheduled chat fires. One worker, skip-on-overlap."""
from __future__ import annotations

import json
import logging
import re
import secrets
import time
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from . import accounts, chats, cron, db, hosts
from .cron import CronError, iso_utc, parse_iso_utc, utcnow
from .security import limiter
from .skill_parse import SkillError

logger = logging.getLogger("app.schedules")

MAX_SCHEDULES = 20
PROMPT_MAX = 8_000
VAR_MAX = 32
VAR_VALUE_MAX = 200
NAME_MAX = 80
EVERY_ALLOWED = frozenset({300, 600, 900, 1800, 3600, 7200, 10800, 21600, 43200, 86400})
MIN_CODING_INTERVAL = 900
MIN_SEARCH_INTERVAL = 300
MAX_CONCURRENT_USER = 2
MAX_CONCURRENT_GLOBAL = 4
FRESH_CAP = 20
CONTINUITY_KEEP = 5
GRACE_CAP = 6 * 3600
RETRY_COOLDOWN_S = 60
RECONCILE_SECONDS = 60
# Per-schedule count of in-flight _fire coroutines (tick claims + run_now).
# Keyed by schedule id; tick never claims a schedule with a nonzero count.
_INFLIGHT: dict[int, int] = {}
_last_reconcile = 0.0
_BANNED_EXACT = frozenset({"secret", "password", "token", "key", "dek", "cookie", "auth"})
_BANNED_SUFFIX = ("_secret", "_password", "_token", "_key", "_dek", "_cookie", "_auth")
_VAR_NAME = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
_VAR_TOKEN = re.compile(r"\{\{([a-z][a-z0-9_]{0,31})\}\}")
_SECRET_RE = re.compile(
    r"(?i)(sk-[a-z0-9]+|bearer\s+\S+|-----BEGIN [A-Z ]+-----[\s\S]+?-----END [A-Z ]+-----|fuse_vault=\S+)"
)
_BUILTINS = frozenset({
    "date", "time", "iso", "weekday", "timezone", "schedule_name",
    "project_name", "host_alias", "last_status", "last_error", "fire_id",
})
RECUR = frozenset({"once", "every", "cron"})
TARGETS = frozenset({"chat", "project"})
STATUSES_RETRYABLE = frozenset({"queued", "skipped_capacity", "vault_locked", "skipped_overlap"})
# Terminal chat-run statuses that must flow back onto schedule fires.
RUN_TERMINAL = frozenset({"completed", "failed", "cancelled", "interrupted"})


class ScheduleError(Exception):
    def __init__(self, detail: str, status_code: int = 400):
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


def banned_var(name: str) -> bool:
    n = (name or "").lower()
    return n in _BANNED_EXACT or n.endswith(_BANNED_SUFFIX)


def _flag(value, field: str) -> bool:
    """Strict boolean: JSON true/false (or 0/1), never truthy strings."""
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    raise ScheduleError(f"{field} must be true or false")


def sanitize_schedule_error(value: str) -> str:
    text = _SECRET_RE.sub("[redacted]", str(value or ""))
    return " ".join(text.split())[:200]


def _public_id(conn) -> str:
    alphabet = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
    for _ in range(12):
        token = "s" + "".join(secrets.choice(alphabet) for _ in range(21))
        if conn.execute("SELECT 1 FROM chat_schedules WHERE public_id=?", (token,)).fetchone() is None:
            return token
    raise ScheduleError("could not allocate schedule id", 500)


def _parse_vars(raw) -> dict[str, str]:
    if raw is None or raw == "":
        return {}
    if isinstance(raw, dict):
        data = raw
    else:
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            raise ScheduleError("variables must be an object") from exc
    if not isinstance(data, dict):
        raise ScheduleError("variables must be an object")
    if len(data) > VAR_MAX:
        raise ScheduleError("too many variables")
    out = {}
    for key, value in data.items():
        if not isinstance(key, str) or not _VAR_NAME.fullmatch(key):
            raise ScheduleError("invalid variable name")
        if key in _BUILTINS or banned_var(key):
            raise ScheduleError(f"reserved variable {key}")
        if not isinstance(value, str) or len(value) > VAR_VALUE_MAX:
            raise ScheduleError("invalid variable value")
        out[key] = value
    return out


def _int_field(value, name: str) -> int:
    if isinstance(value, bool):
        raise ScheduleError(f"{name} must be an integer")
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ScheduleError(f"{name} must be an integer") from exc


def _stored_vars(schedule_id, raw) -> dict[str, str]:
    """A stored row that fails today's rules must not roll back the tick for
    every user or 500 its owner's list; its fire fails on the unknown
    variable instead, and an edit re-validates."""
    try:
        return _parse_vars(raw)
    except ScheduleError as exc:
        logger.warning("Schedule %s has invalid stored variables (%s); ignoring them", schedule_id, exc.detail)
        return {}


def _row(row) -> dict:
    keys = row.keys() if hasattr(row, "keys") else []
    return {
        "id": row["id"],
        "public_id": row["public_id"],
        "user_id": row["user_id"],
        "name": row["name"],
        "enabled": bool(row["enabled"]),
        "paused": bool(row["paused"]),
        "target": row["target"],
        "project_id": row["project_id"],
        "thread_id": row["thread_id"],
        "fresh_each_fire": bool(row["fresh_each_fire"]),
        "allow_coding": bool(row["allow_coding"]),
        "prompt": row["prompt"],
        "variables": _stored_vars(row["id"], row["variables"] if "variables" in keys else "{}"),
        "model": row["model"],
        "tools": chats.parse_thread_tools(row["tools"]),
        "reasoning_effort": row["reasoning_effort"] or "",
        "timezone": row["timezone"],
        "recur_kind": row["recur_kind"],
        "cron_expr": row["cron_expr"] or "",
        "every_seconds": row["every_seconds"],
        "weekdays": int(row["weekdays"] or 0),
        "at_local": row["at_local"] or "",
        "once_at": row["once_at"],
        "next_fire_at": row["next_fire_at"],
        "last_fire_at": row["last_fire_at"],
        "last_status": row["last_status"] or "",
        "last_error": row["last_error"] or "",
        "last_run_id": row["last_run_id"] or "",
        "firing_at": row["firing_at"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "project_name": row["project_name"] if "project_name" in keys else "",
        "host_alias": row["host_alias"] if "host_alias" in keys else "",
        "thread_public_id": row["thread_public_id"] if "thread_public_id" in keys else "",
    }


def public(item: dict, *, fires: list | None = None) -> dict:
    data = {
        "id": item["public_id"],
        "name": item["name"],
        "enabled": item["enabled"],
        "paused": item["paused"],
        "target": item["target"],
        "project_id": item["project_id"],
        "project_name": item.get("project_name") or "",
        "host_alias": item.get("host_alias") or "",
        "thread_id": item.get("thread_public_id") or "",
        "fresh_each_fire": item["fresh_each_fire"],
        "allow_coding": item["allow_coding"],
        "prompt": item["prompt"],
        "variables": item.get("variables") or {},
        "model": item["model"],
        "tools": item["tools"],
        "reasoning_effort": item["reasoning_effort"],
        "timezone": item["timezone"],
        "recur_kind": item["recur_kind"],
        "cron_expr": item["cron_expr"],
        "every_seconds": item["every_seconds"],
        "weekdays": item["weekdays"],
        "at_local": item["at_local"],
        "once_at": item["once_at"],
        "next_fire_at": item["next_fire_at"],
        "last_fire_at": item["last_fire_at"],
        "last_status": item["last_status"],
        "last_error": item["last_error"],
        "created_at": item["created_at"],
        "updated_at": item["updated_at"],
        "when": _when_label(item),
    }
    if fires is not None:
        data["fires"] = fires
    return data


def _when_label(item: dict) -> str:
    kind = item.get("recur_kind")
    tz = item.get("timezone") or "UTC"
    if kind == "once":
        return f"Once at {item.get('once_at') or '—'} ({tz})"
    if kind == "every":
        sec = int(item.get("every_seconds") or 0)
        if sec >= 86400 and sec % 86400 == 0:
            n = sec // 86400
            return f"Every {n} day{'s' if n != 1 else ''}"
        if sec >= 3600 and sec % 3600 == 0:
            n = sec // 3600
            return f"Every {n} hour{'s' if n != 1 else ''}"
        n = max(1, sec // 60)
        return f"Every {n} min"
    if item.get("at_local") and item.get("weekdays"):
        return f"Weekly {item['at_local']} ({tz})"
    if item.get("at_local") and item.get("cron_expr", "").endswith("* * *"):
        return f"Daily {item['at_local']} ({tz})"
    return item.get("cron_expr") or "Cron"


def _interval_seconds(item: dict) -> int:
    if item.get("recur_kind") == "every":
        return int(item.get("every_seconds") or MIN_SEARCH_INTERVAL)
    if item.get("recur_kind") == "once":
        return GRACE_CAP
    expr = item.get("cron_expr") or ""
    if not expr:
        return MIN_CODING_INTERVAL
    gap = cron.implied_min_gap(expr, item.get("timezone") or "UTC", utcnow(), 12)
    return int(gap) if gap else MIN_CODING_INTERVAL


def _min_interval(item: dict) -> int:
    coding = item.get("target") == "project" or bool(item.get("allow_coding"))
    return MIN_CODING_INTERVAL if coding else MIN_SEARCH_INTERVAL


def _grace(item: dict) -> int:
    return min(_interval_seconds(item), GRACE_CAP)


def _past_grace(item: dict, slot_iso: str) -> bool:
    slot = parse_iso_utc(slot_iso)
    if slot is None:
        return True
    return (utcnow() - slot).total_seconds() > _grace(item)


def fire_row_retryable(fire: dict, schedule: dict) -> bool:
    slot = fire.get("slot_key") or ""
    if slot.startswith("manual:"):
        return False
    if fire.get("status") == "queued" and not fire.get("run_id"):
        return True
    if fire.get("status") == "skipped_capacity" and not _past_grace(schedule, slot):
        return True
    if fire.get("status") == "vault_locked" and schedule.get("recur_kind") == "once":
        return True
    if fire.get("status") == "skipped_overlap" and schedule.get("recur_kind") == "once" and not _past_grace(schedule, slot):
        return True
    return False


def _next_after(item: dict, after: datetime) -> datetime | None:
    kind = item.get("recur_kind")
    if kind == "once":
        return None
    if kind == "every":
        sec = int(item.get("every_seconds") or 0)
        if sec <= 0:
            return None
        return after + timedelta(seconds=sec)
    expr = item.get("cron_expr") or ""
    if not expr:
        return None
    return cron.next_fire(expr, item.get("timezone") or "UTC", after)


def _compute_first(item: dict) -> str | None:
    now = utcnow()
    kind = item["recur_kind"]
    if kind == "once":
        dt = parse_iso_utc(item.get("once_at") or "")
        return iso_utc(dt) if dt else None
    if kind == "every":
        return iso_utc(now + timedelta(seconds=int(item["every_seconds"])))
    nxt = cron.next_fire(item["cron_expr"], item["timezone"], now)
    return iso_utc(nxt) if nxt else None


def preview_fires(item: dict, n: int = 5) -> list[str]:
    out = []
    kind = item.get("recur_kind")
    now = utcnow()
    if kind == "once":
        dt = parse_iso_utc(item.get("once_at") or "")
        return [iso_utc(dt)] if dt else []
    cursor = now
    for _ in range(n):
        nxt = _next_after(item, cursor) if kind == "every" else cron.next_fire(
            item.get("cron_expr") or "", item.get("timezone") or "UTC", cursor
        )
        if kind == "every" and cursor == now:
            nxt = now + timedelta(seconds=int(item.get("every_seconds") or 0))
        if nxt is None:
            break
        out.append(iso_utc(nxt))
        cursor = nxt
    return out


def _assert_interval(item: dict) -> None:
    minimum = _min_interval(item)
    kind = item["recur_kind"]
    if kind == "every":
        sec = int(item.get("every_seconds") or 0)
        if sec not in EVERY_ALLOWED:
            raise ScheduleError("unsupported interval")
        if sec == 300 and minimum > 300:
            raise ScheduleError("interval too frequent")
        if sec < minimum:
            raise ScheduleError("interval too frequent")
        return
    if kind == "once":
        return
    expr = item.get("cron_expr") or ""
    gap = cron.implied_min_gap(expr, item["timezone"], utcnow(), 12)
    if gap is None:
        raise ScheduleError("could not project cron fires")
    if gap < minimum:
        raise ScheduleError("interval too frequent")


def _normalize(user_id: int, raw: dict, *, existing: dict | None = None) -> dict:
    name = str(raw.get("name") or (existing or {}).get("name") or "").strip()[:NAME_MAX]
    if not name:
        raise ScheduleError("name is required")
    target = str(raw.get("target") or (existing or {}).get("target") or "chat")
    if target not in TARGETS:
        raise ScheduleError("target must be chat or project")
    project_id = raw.get("project_id") if "project_id" in raw else (existing or {}).get("project_id")
    if target == "project":
        try:
            project_id = int(project_id)
        except (TypeError, ValueError):
            raise ScheduleError("project_id is required")
        proj = hosts.resolve_project_id(user_id, project_id)
        if not proj:
            raise ScheduleError("project not found", 404)
    else:
        project_id = None
    prompt = str(raw.get("prompt") if "prompt" in raw else (existing or {}).get("prompt") or "")
    if not prompt.strip() or len(prompt) > PROMPT_MAX:
        raise ScheduleError("prompt is required")
    variables = _parse_vars(raw.get("variables") if "variables" in raw else (existing or {}).get("variables"))
    model = str(raw.get("model") if "model" in raw else (existing or {}).get("model") or "").strip()[:200]
    if not model:
        raise ScheduleError("model is required")
    tools = chats.parse_thread_tools(raw.get("tools") if "tools" in raw else (existing or {}).get("tools"), strict=True)
    effort = raw.get("reasoning_effort") if "reasoning_effort" in raw else (existing or {}).get("reasoning_effort")
    effort = chats.parse_reasoning_effort(effort or None, strict=True) or ""
    tz = str(raw.get("timezone") if "timezone" in raw else (existing or {}).get("timezone") or "UTC").strip()
    cron.parse_tz(tz)
    recur_kind = str(raw.get("recur_kind") if "recur_kind" in raw else (existing or {}).get("recur_kind") or "")
    if recur_kind not in RECUR:
        raise ScheduleError("recur_kind must be once, every, or cron")
    if recur_kind == "monitor":
        raise ScheduleError("monitor schedules are not supported")
    cron_expr = ""
    every_seconds = None
    weekdays = _int_field(raw.get("weekdays") if "weekdays" in raw else (existing or {}).get("weekdays") or 0, "weekdays")
    at_local = str(raw.get("at_local") if "at_local" in raw else (existing or {}).get("at_local") or "")
    once_at = raw.get("once_at") if "once_at" in raw else (existing or {}).get("once_at")
    if recur_kind == "cron":
        builder = str(raw.get("builder") or "")
        if builder == "daily" or (at_local and not raw.get("cron_expr") and not weekdays):
            hour, minute = cron.parse_hhmm(at_local)
            cron_expr = cron.compile_daily(hour, minute)
        elif builder == "weekly" or weekdays:
            hour, minute = cron.parse_hhmm(at_local)
            cron_expr = cron.compile_weekly(hour, minute, int(weekdays))
        else:
            cron_expr = str(raw.get("cron_expr") if "cron_expr" in raw else (existing or {}).get("cron_expr") or "")
        cron.parse_cron(cron_expr)
    elif recur_kind == "every":
        every_seconds = _int_field(raw.get("every_seconds") if "every_seconds" in raw else (existing or {}).get("every_seconds") or 0, "every_seconds")
        if every_seconds not in EVERY_ALLOWED:
            raise ScheduleError("unsupported interval")
    else:
        if not once_at:
            raise ScheduleError("once_at is required")
        if not parse_iso_utc(str(once_at)):
            raise ScheduleError("once_at must be UTC ISO")
        once_at = iso_utc(parse_iso_utc(str(once_at)))
    allow_coding = _flag(raw.get("allow_coding") if "allow_coding" in raw else (existing or {}).get("allow_coding", 0), "allow_coding")
    if target == "project":
        allow_coding = True
    fresh = _flag(raw.get("fresh_each_fire") if "fresh_each_fire" in raw else (existing or {}).get("fresh_each_fire", 0), "fresh_each_fire")
    enabled = _flag(raw.get("enabled") if "enabled" in raw else (existing or {}).get("enabled", 1), "enabled")
    paused = _flag(raw.get("paused") if "paused" in raw else (existing or {}).get("paused", 0), "paused")
    item = {
        "name": name,
        "target": target,
        "project_id": project_id,
        "prompt": prompt,
        "variables": variables,
        "model": model,
        "tools": tools,
        "reasoning_effort": effort or "",
        "timezone": tz,
        "recur_kind": recur_kind,
        "cron_expr": cron_expr,
        "every_seconds": every_seconds,
        "weekdays": int(weekdays or 0),
        "at_local": at_local,
        "once_at": once_at,
        "allow_coding": allow_coding,
        "fresh_each_fire": fresh,
        "enabled": enabled,
        "paused": paused,
    }
    _assert_interval(item)
    unknown = set(_VAR_TOKEN.findall(prompt)) - _BUILTINS - set(variables)
    if unknown:
        raise ScheduleError("unknown variable " + sorted(unknown)[0])
    item["next_fire_at"] = _compute_first(item) if item["enabled"] and not item["paused"] else None
    return item


def _list_sql():
    return """SELECT s.*, p.name AS project_name, h.alias AS host_alias, t.public_id AS thread_public_id
              FROM chat_schedules s
              LEFT JOIN user_projects p ON p.id=s.project_id
              LEFT JOIN user_hosts h ON h.id=p.host_id
              LEFT JOIN chat_threads t ON t.id=s.thread_id"""


def list_schedules(user_id: int) -> list[dict]:
    rows = db.fetchall(_list_sql() + " WHERE s.user_id=? ORDER BY s.updated_at DESC", (user_id,))
    return [_row(r) for r in rows]


def get_schedule(user_id: int, token, conn=None) -> dict | None:
    q = _list_sql() + " WHERE s.user_id=? AND (s.public_id=? OR s.id=?)"
    try:
        pk = int(token)
    except (TypeError, ValueError):
        pk = -1
    row = (conn.execute(q, (user_id, str(token), pk)).fetchone() if conn is not None
           else db.fetchone(q, (user_id, str(token), pk)))
    return _row(row) if row else None


def failure_count(user_id: int) -> int:
    row = db.fetchone(
        """SELECT COUNT(*) AS n FROM chat_schedules
           WHERE user_id=? AND last_status IN ('failed','vault_locked') AND enabled=1""",
        (user_id,),
    )
    return int(row["n"] if row else 0)


def create_schedule(user_id: int, raw: dict) -> dict:
    if not limiter.allow(f"schedules:{user_id}", 30, 60):
        raise ScheduleError("too many schedule changes", 429)
    item = _normalize(user_id, raw)
    now = db.now()
    with db.tx() as conn:
        conn.execute("BEGIN IMMEDIATE")
        n = conn.execute("SELECT COUNT(*) AS n FROM chat_schedules WHERE user_id=?", (user_id,)).fetchone()["n"]
        if int(n) >= MAX_SCHEDULES:
            raise ScheduleError(f"schedule limit of {MAX_SCHEDULES} reached", 413)
        public = _public_id(conn)
        conn.execute(
            """INSERT INTO chat_schedules(
                   public_id, user_id, name, enabled, paused, target, project_id, fresh_each_fire,
                   allow_coding, prompt, variables, model, tools, reasoning_effort, timezone,
                   recur_kind, cron_expr, every_seconds, weekdays, at_local, once_at, next_fire_at,
                   created_at, updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                public, user_id, item["name"], 1 if item["enabled"] else 0, 1 if item["paused"] else 0,
                item["target"], item["project_id"], 1 if item["fresh_each_fire"] else 0,
                1 if item["allow_coding"] else 0, item["prompt"], json.dumps(item["variables"]),
                item["model"], json.dumps(item["tools"]), item["reasoning_effort"], item["timezone"],
                item["recur_kind"], item["cron_expr"], item["every_seconds"], item["weekdays"],
                item["at_local"], item["once_at"], item["next_fire_at"], now, now,
            ),
        )
    found = get_schedule(user_id, public)
    if found is None:
        raise ScheduleError("could not create schedule", 500)
    return found


_PATCH_INTERVAL_KEYS = {
    "allow_coding", "target", "project_id", "recur_kind", "cron_expr",
    "every_seconds", "timezone", "builder", "at_local", "weekdays", "once_at",
}


def patch_schedule(user_id: int, token, raw: dict) -> dict:
    if not limiter.allow(f"schedules:{user_id}", 30, 60):
        raise ScheduleError("too many schedule changes", 429)
    merged_raw = dict(raw)
    with db.tx() as conn:
        conn.execute("BEGIN IMMEDIATE")
        # Read and write in one transaction: a fire finalizing concurrently
        # (its _finish_fire advances next_fire_at/enabled) commits wholly
        # before or after this PATCH, so neither can clobber the other.
        existing = get_schedule(user_id, token, conn=conn)
        if existing is None:
            raise ScheduleError("schedule not found", 404)
        merged = {**existing, **merged_raw}
        item = _normalize(user_id, merged, existing=existing)
        if set(raw) & _PATCH_INTERVAL_KEYS:
            _assert_interval(item)
        timing = ("recur_kind", "cron_expr", "every_seconds", "timezone", "at_local", "weekdays", "once_at",
                  "enabled", "paused")
        if existing.get("next_fire_at") and item["next_fire_at"] and all(item[k] == existing.get(k) for k in timing):
            # Renaming or rewording must not push the next run back a period.
            item["next_fire_at"] = existing["next_fire_at"]
        retarget = (item["target"], item["project_id"]) != (existing.get("target"), existing.get("project_id"))
        now = db.now()
        conn.execute(
            """UPDATE chat_schedules SET name=?, enabled=?, paused=?, target=?, project_id=?, fresh_each_fire=?,
                   allow_coding=?, prompt=?, variables=?, model=?, tools=?, reasoning_effort=?, timezone=?,
                   recur_kind=?, cron_expr=?, every_seconds=?, weekdays=?, at_local=?, once_at=?, next_fire_at=?,
                   updated_at=?""" + (", thread_id=NULL" if retarget else "") + " WHERE id=? AND user_id=?",
            (
                item["name"], 1 if item["enabled"] else 0, 1 if item["paused"] else 0, item["target"],
                item["project_id"], 1 if item["fresh_each_fire"] else 0, 1 if item["allow_coding"] else 0,
                item["prompt"], json.dumps(item["variables"]), item["model"], json.dumps(item["tools"]),
                item["reasoning_effort"], item["timezone"], item["recur_kind"], item["cron_expr"],
                item["every_seconds"], item["weekdays"], item["at_local"], item["once_at"],
                item["next_fire_at"], now, existing["id"], user_id,
            ),
        )
        found = get_schedule(user_id, existing["public_id"], conn=conn)
    if found is None:
        raise ScheduleError("schedule not found", 404)
    return found


def delete_schedule(user_id: int, token) -> None:
    with db.tx() as conn:
        conn.execute("BEGIN IMMEDIATE")
        existing = get_schedule(user_id, token, conn=conn)
        if existing is None:
            raise ScheduleError("schedule not found", 404)
        # One transaction: threads can never keep a schedule_id that points
        # past a deleted schedule.
        conn.execute("UPDATE chat_threads SET schedule_id=NULL WHERE schedule_id=?", (existing["id"],))
        conn.execute("DELETE FROM chat_schedules WHERE id=? AND user_id=?", (existing["id"], user_id))


def list_fires(user_id: int, token, *, limit: int = 40) -> list[dict]:
    item = get_schedule(user_id, token)
    if item is None:
        raise ScheduleError("schedule not found", 404)
    rows = db.fetchall(
        """SELECT f.*, t.public_id AS thread_public_id
           FROM schedule_fires f
           LEFT JOIN chat_threads t ON t.id=f.thread_id
           WHERE f.schedule_id=? AND f.user_id=?
           ORDER BY f.id DESC LIMIT ?""",
        (item["id"], user_id, max(1, min(int(limit), 100))),
    )
    out = []
    for row in rows:
        out.append({
            "id": row["fire_id"],
            "trigger": row["trigger"],
            "status": row["status"],
            "started_at": row["started_at"],
            "finished_at": row["finished_at"] or "",
            "error": row["error"] or "",
            "thread_id": row["thread_public_id"] or "",
            "run_id": row["run_id"] or "",
        })
    return out


def expand_prompt(item: dict, *, fire_id: str, host_alias: str = "", project_name: str = "") -> str:
    tz = cron.parse_tz(item["timezone"])
    now = utcnow().astimezone(tz)
    values = {
        "date": now.strftime("%Y-%m-%d"),
        "time": now.strftime("%H:%M"),
        "iso": utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "weekday": now.strftime("%A"),
        "timezone": item["timezone"],
        "schedule_name": item["name"],
        "project_name": project_name or "",
        "host_alias": host_alias or "",
        "last_status": item.get("last_status") or "never",
        "last_error": sanitize_schedule_error(item.get("last_error") or ""),
        "fire_id": fire_id,
        **(item.get("variables") or {}),
    }
    def repl(match):
        key = match.group(1)
        if key not in values:
            raise ScheduleError(f"unknown variable {key}")
        return str(values[key])
    expanded = _VAR_TOKEN.sub(repl, item["prompt"])
    if len(expanded) > chats.CONTENT_MAX:
        raise ScheduleError("expanded prompt is too long")
    header = (
        "SCHEDULED RUN — not written interactively. Treat this as an automated instruction "
        "from the user who configured the schedule. Do not ask questions; do not wait. "
        "Skill authoring is disabled.\n\n"
    )
    return header + expanded


def fire_needs_vault(schedule: dict, host) -> bool:
    if schedule["target"] != "project":
        return False
    if not host:
        return False
    return (host.get("auth_type") or "") != "agent"


def sync_run_status(conn, user_id: int, run_id, status: str, error: str = "", finished_at: str | None = None) -> None:
    """Propagate a terminal chat-run status onto its fire row and schedule.

    Called inside chat_store write transactions so the Tasks page reflects
    completed/failed/cancelled/interrupted runs instead of a stale "running".
    """
    if status not in RUN_TERMINAL or not run_id:
        return
    err = sanitize_schedule_error(error)
    ts = finished_at or db.now()
    conn.execute(
        """UPDATE schedule_fires SET status=?, error=?, finished_at=?
           WHERE user_id=? AND run_id=? AND status IN ('queued','running')""",
        (status, err, ts, user_id, str(run_id)),
    )
    conn.execute(
        """UPDATE chat_schedules SET last_status=?, last_error=?, updated_at=?
           WHERE user_id=? AND last_run_id=? AND last_status IN ('queued','running')""",
        (status, err, ts, user_id, str(run_id)),
    )


def _run_error(run_id) -> str:
    if not run_id:
        return ""
    row = db.fetchone("SELECT message FROM chat_runs WHERE id=?", (str(run_id),))
    if row is None:
        return ""
    try:
        msg = json.loads(row["message"] or "{}")
    except (json.JSONDecodeError, TypeError, ValueError):
        return ""
    return sanitize_schedule_error(str((msg if isinstance(msg, dict) else {}).get("error") or ""))


def reconcile() -> None:
    """Close fires whose run finished without a sync (restart/crash windows).

    A fire that is 'running' with a live run keeps its status; a fire whose run
    is terminal inherits the run status; a fire whose run row vanished (or was
    never started) becomes 'interrupted' so no row stays stuck on Running.
    """
    now = db.now()
    fires = db.fetchall(
        """SELECT f.id AS fid, f.schedule_id, f.user_id, f.status AS fire_status, f.run_id,
                  r.status AS run_status, r.updated_at AS run_updated
           FROM schedule_fires f
           LEFT JOIN chat_runs r ON r.id=f.run_id
           WHERE f.status IN ('queued','running')"""
    )
    for row in fires:
        run_status = row["run_status"]
        if run_status in RUN_TERMINAL:
            status, err = run_status, _run_error(row["run_id"])
            finished = row["run_updated"] or now
        elif row["run_id"] and run_status is None:
            status, err, finished = "interrupted", "run lost", now
        elif row["fire_status"] == "running" and not row["run_id"]:
            status, err, finished = "interrupted", "no run started", now
        else:
            # queued without a run stays retryable; active runs stay live.
            continue
        db.execute(
            "UPDATE schedule_fires SET status=?, error=?, finished_at=? WHERE id=? AND status IN ('queued','running')",
            (status, err, finished, row["fid"]),
        )
        if row["run_id"]:
            db.execute(
                """UPDATE chat_schedules SET last_status=?, last_error=?, updated_at=?
                   WHERE id=? AND last_run_id=? AND last_status IN ('queued','running')""",
                (status, err, finished, row["schedule_id"], row["run_id"]),
            )
    for row in db.fetchall(
        """SELECT s.id AS sid, s.last_run_id, r.status AS run_status, r.updated_at AS run_updated
           FROM chat_schedules s
           LEFT JOIN chat_runs r ON r.id=s.last_run_id
           WHERE s.last_status IN ('queued','running')"""
    ):
        run_status = row["run_status"]
        if run_status in RUN_TERMINAL:
            db.execute(
                "UPDATE chat_schedules SET last_status=?, last_error=?, updated_at=? WHERE id=?",
                (run_status, _run_error(row["last_run_id"]), row["run_updated"] or now, row["sid"]),
            )
        elif row["last_run_id"] and run_status is None:
            db.execute(
                "UPDATE chat_schedules SET last_status='interrupted', last_error='run lost', updated_at=? WHERE id=?",
                (now, row["sid"]),
            )
        elif not row["last_run_id"]:
            # A fire that reached 'running' without a run id can never resolve
            # to a run; close it instead of leaving the badge on Running.
            db.execute(
                """UPDATE chat_schedules SET last_status='interrupted', last_error='no run started', updated_at=?
                   WHERE id=? AND last_status IN ('queued','running')
                     AND (last_run_id IS NULL OR last_run_id='')""",
                (now, row["sid"]),
            )


def _trim_schedule_threads(user_id: int, schedule_id: int, *, keep_id: int, cap: int) -> None:
    rows = db.fetchall(
        """SELECT t.id FROM chat_threads t
           WHERE t.user_id=? AND t.schedule_id=? AND t.id != ?
             AND NOT EXISTS (
                SELECT 1 FROM chat_runs r WHERE r.thread_id=t.id AND r.status IN ('queued','running')
                AND r.parent_run_id IS NULL
             )
           ORDER BY t.updated_at ASC""",
        (user_id, schedule_id, keep_id),
    )
    n = db.fetchone(
        "SELECT COUNT(*) AS n FROM chat_threads WHERE user_id=? AND schedule_id=?",
        (user_id, schedule_id),
    )["n"]
    extra = int(n) - cap
    for row in rows:
        if extra <= 0:
            break
        if chats.delete_schedule_owned_thread(user_id, row["id"], keep_thread_id=keep_id):
            extra -= 1


def _thread_public(thread_id) -> str:
    if thread_id is None:
        return ""
    row = db.fetchone("SELECT public_id FROM chat_threads WHERE id=?", (thread_id,))
    return str(row["public_id"]) if row and row["public_id"] else ""


async def _fire(schedule: dict, fire: dict, *, consume_slot: bool) -> None:
    from . import chat_runs, chat_store
    from .vault import VaultLocked, get_dek, pin_run

    user_id = schedule["user_id"]
    slot = fire["slot_key"]
    fire_id = fire["fire_id"]
    status = "failed"
    error = ""
    thread_pk = None
    run_id = None
    try:
        if not slot.startswith("manual:") and _past_grace(schedule, slot) and not (
            schedule["recur_kind"] == "once" and fire.get("status") == "vault_locked"
        ):
            status = "skipped_catchup"
            error = "missed fire window"
            _finish_fire(schedule, fire, status, error, None, None, consume=True)
            return
        host = None
        project = None
        if schedule["target"] == "project":
            project = hosts.resolve_project_id(user_id, schedule["project_id"])
            if not project:
                raise ScheduleError("project is gone", 404)
            host = hosts.get_host(user_id, project["host_id"])
            if not host or host.get("disabled"):
                raise ScheduleError("host is unavailable", 400)
            if fire_needs_vault(schedule, host):
                try:
                    get_dek(user_id)
                except VaultLocked:
                    status = "vault_locked"
                    error = "Vault locked"
                    _finish_fire(schedule, fire, status, error, None, None, consume=schedule["recur_kind"] != "once")
                    return
        from .sources import pick
        from .sources.chatgpt import user_has_chatgpt
        from .sources.devin import user_has_devin
        from .sources.grok import user_has_grok
        from .sources.kimi import user_has_kimi
        from .sources.mimo import user_has_mimo
        from .sources.claude import user_has_claude
        from .sources.zai import user_has_zai

        if schedule["fresh_each_fire"] == 0 and schedule.get("thread_id"):
            if db.fetchone(
                """SELECT 1 FROM chat_runs WHERE thread_id=? AND status IN ('queued','running')
                   AND parent_run_id IS NULL""",
                (schedule["thread_id"],),
            ):
                status = "skipped_overlap"
                _finish_fire(
                    schedule, fire, status, "already running", None, None,
                    consume=schedule["recur_kind"] != "once",
                )
                return
        def over_capacity() -> bool:
            n_sched = int(db.fetchone(
                """SELECT COUNT(*) AS n FROM chat_runs r
                   WHERE r.user_id=? AND r.status IN ('queued','running') AND r.parent_run_id IS NULL
                     AND instr(r.request, '"scheduled":true') > 0""",
                (user_id,),
            )["n"])
            n_user = int(db.fetchone(
                """SELECT COUNT(*) AS n FROM chat_runs WHERE user_id=? AND status IN ('queued','running')
                   AND parent_run_id IS NULL""",
                (user_id,),
            )["n"])
            n_global = int(db.fetchone(
                """SELECT COUNT(*) AS n FROM chat_runs WHERE status IN ('queued','running') AND parent_run_id IS NULL"""
            )["n"])
            n_sched_global = int(db.fetchone(
                """SELECT COUNT(*) AS n FROM chat_runs r
                   WHERE r.status IN ('queued','running') AND r.parent_run_id IS NULL
                     AND instr(r.request, '"scheduled":true') > 0"""
            )["n"])
            return n_sched >= MAX_CONCURRENT_USER or n_sched_global >= MAX_CONCURRENT_GLOBAL or n_user >= 4 or n_global >= 32

        if over_capacity():
            status = "skipped_capacity"
            _finish_fire(schedule, fire, status, "capacity", None, None, consume=False)
            return
        # Model resolution last: capacity/overlap skips must not pay for it
        # (and a capacity-skip retry loop would otherwise hammer pick).
        chosen = await pick(
            schedule["model"],
            allow_grok=user_has_grok(user_id),
            allow_chatgpt=user_has_chatgpt(user_id),
            allow_zai=user_has_zai(user_id),
            allow_devin=user_has_devin(user_id),
            allow_kimi=user_has_kimi(user_id),
            allow_mimo=user_has_mimo(user_id),
            allow_claude=user_has_claude(user_id),
            user_id=user_id,
        )
        if chosen is None:
            raise ScheduleError("model unavailable", 400)
        # Everything from here to start_turn runs without an await, so this
        # check cannot be raced on the event loop. A schedule deleted while
        # this fire was suspended (e.g. in pick) must not start its run.
        if db.fetchone("SELECT 1 FROM chat_schedules WHERE id=?", (schedule["id"],)) is None:
            _finish_fire(schedule, fire, "cancelled", "schedule deleted", None, None, consume=False)
            return
        # Recount after the awaited pick: other fires may have started runs
        # while this one was suspended there.
        if over_capacity():
            status = "skipped_capacity"
            _finish_fire(schedule, fire, status, "capacity", None, None, consume=False)
            return
        prompt = expand_prompt(
            schedule, fire_id=fire_id,
            host_alias=(host or {}).get("alias") or "",
            project_name=(project or {}).get("name") or "",
        )
        kind = "project" if schedule["target"] == "project" else "chat"
        hid = (project or {}).get("host_id") if project else None
        pid = schedule.get("project_id") if project else None
        thread = None
        if schedule["fresh_each_fire"]:
            n = db.fetchone(
                "SELECT COUNT(*) AS n FROM chat_threads WHERE user_id=? AND schedule_id=?",
                (user_id, schedule["id"]),
            )["n"]
            if int(n) >= FRESH_CAP:
                _trim_schedule_threads(user_id, schedule["id"], keep_id=schedule.get("thread_id") or 0, cap=FRESH_CAP - 1)
            title = f"Scheduled: {schedule['name']}"[: chats.TITLE_MAX]
            if schedule["fresh_each_fire"]:
                title = f"Scheduled: {schedule['name']} · {utcnow().strftime('%Y-%m-%d')}"[: chats.TITLE_MAX]
            thread = chats.create_thread(
                user_id, model=schedule["model"], title=title, tools=schedule["tools"],
                kind=kind, host_id=hid, project_id=pid, schedule_id=schedule["id"],
            )
        else:
            thread_pk = schedule.get("thread_id")
            if thread_pk:
                row = db.fetchone(
                    "SELECT id, messages, kind, host_id, project_id FROM chat_threads WHERE id=? AND user_id=?",
                    (thread_pk, user_id),
                )
                if row and kind == "project" and (row["host_id"], row["project_id"]) != (hid, pid):
                    # The schedule was retargeted since this thread was made;
                    # the thread's binding decides host and cwd, so start a
                    # new continuity thread rather than run on the old target.
                    # (Chat targets are left alone: a chat may legitimately
                    # have been promoted into a project by its own work.)
                    row = None
                if row:
                    msgs = chats.parse_messages(row["messages"])
                    if len(msgs) + 2 > chats.MSG_MAX - 8:
                        title = f"Scheduled: {schedule['name']}"[: chats.TITLE_MAX]
                        thread = chats.create_thread(
                            user_id, model=schedule["model"], title=title, tools=schedule["tools"],
                            kind=kind, host_id=hid, project_id=pid, schedule_id=schedule["id"],
                        )
                        _trim_schedule_threads(user_id, schedule["id"], keep_id=_thread_pk(thread), cap=CONTINUITY_KEEP)
                    else:
                        thread = chats.get_thread(user_id, thread_pk)
            if thread is None:
                title = f"Scheduled: {schedule['name']}"[: chats.TITLE_MAX]
                thread = chats.create_thread(
                    user_id, model=schedule["model"], title=title, tools=schedule["tools"],
                    kind=kind, host_id=hid, project_id=pid, schedule_id=schedule["id"],
                )
        thread_pk = _thread_pk(thread)
        db.execute("UPDATE chat_schedules SET thread_id=?, updated_at=? WHERE id=?", (thread_pk, db.now(), schedule["id"]))
        coding_override = schedule["target"] == "project" or bool(schedule["allow_coding"])
        # Claim the fire as running before the run row is committed. From here
        # on a crash must never let tick re-run this slot (the run may already
        # exist and be resumed): recovery closes it as interrupted instead.
        db.execute(
            "UPDATE schedule_fires SET status='running', thread_id=? WHERE schedule_id=? AND slot_key=?",
            (thread_pk, schedule["id"], fire["slot_key"]),
        )
        try:
            result = chat_store.start_turn(
                user_id, thread_pk, prompt, None, None,
                model=schedule["model"], tools=schedule["tools"],
                reasoning_effort=schedule["reasoning_effort"] or None,
                auto_title=False, auto=True, scheduled=True, coding_override=coding_override,
                schedule_id=schedule["id"], schedule_manual=slot.startswith("manual:"),
            )
        except chats.ChatValidationError as exc:
            if exc.status_code == 409:
                status = "skipped_overlap"
                _finish_fire(schedule, fire, status, str(exc.detail), thread_pk, None,
                             consume=schedule["recur_kind"] != "once")
                return
            if exc.status_code == 429:
                status = "skipped_capacity"
                _finish_fire(schedule, fire, status, str(exc.detail), thread_pk, None, consume=False)
                return
            raise ScheduleError(str(exc.detail), exc.status_code) from exc
        run = result.get("run") or {}
        run_id = run.get("id")
        # Record the run on the fire before anything else can interleave: a
        # queued fire with a run_id is never retryable, so a crash between the
        # run insert and the final _finish_fire cannot double-execute the slot.
        db.execute(
            "UPDATE schedule_fires SET run_id=? WHERE schedule_id=? AND slot_key=?",
            (str(run_id or ""), schedule["id"], fire["slot_key"]),
        )
        if coding_override:
            try:
                get_dek(user_id)
                if run_id:
                    pin_run(user_id, run_id)
            except VaultLocked:
                pass
        user = accounts.get_user(user_id)
        if user and run_id:
            await chat_runs.start(chat_runs.internal_request(user_id), dict(user), run)
        status = "running"
        _finish_fire(schedule, fire, status, "", thread_pk, run_id, consume=consume_slot and not slot.startswith("manual:"))
        return
    except ScheduleError as exc:
        status = "failed" if exc.status_code != 401 else "vault_locked"
        if "Vault" in exc.detail:
            status = "vault_locked"
        if "gone" in exc.detail:
            status = "failed"
            error = "project_gone"
        elif "unavailable" in exc.detail and "host" in exc.detail:
            error = "host_disabled"
        elif "model" in exc.detail:
            error = "model_unavailable"
        else:
            error = sanitize_schedule_error(exc.detail)
        consume = True
        if status == "vault_locked" and schedule["recur_kind"] == "once":
            consume = False
        if schedule["recur_kind"] == "once" and status == "failed":
            consume = True
        _finish_fire(schedule, fire, status, error, thread_pk, run_id, consume=consume)
    except chats.ChatValidationError as exc:
        status = "failed"
        error = sanitize_schedule_error(exc.detail)
        if exc.status_code == 413:
            error = "thread_limit" if "limit of" in exc.detail else "message_limit"
        _finish_fire(schedule, fire, status, error, thread_pk, run_id, consume=True)
    except Exception:
        logger.exception("schedule fire failed")
        _finish_fire(schedule, fire, "failed", "internal error", thread_pk, run_id, consume=True)


def _thread_pk(thread) -> int | None:
    if not thread:
        return None
    token = thread.get("id")
    row = db.fetchone("SELECT id FROM chat_threads WHERE public_id=? OR id=?", (str(token), token if isinstance(token, int) else -1))
    return int(row["id"]) if row else None


# Config fields that decide the fire cadence. If one of these changed while a
# fire was executing, the fire must not overwrite the editor's scheduling.
_FINISH_CONFIG_FIELDS = ("recur_kind", "cron_expr", "every_seconds", "timezone", "once_at", "enabled", "paused")


def _finish_fire(schedule, fire, status, error, thread_id, run_id, *, consume: bool) -> None:
    now = db.now()
    err = sanitize_schedule_error(error)
    slot = fire["slot_key"]
    manual = slot.startswith("manual:")
    with db.tx() as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            """UPDATE schedule_fires SET status=?, error=?, thread_id=?, run_id=?, finished_at=?
               WHERE schedule_id=? AND slot_key=?""",
            (status, err, thread_id, run_id, now if status not in ("queued", "running") else None, schedule["id"], slot),
        )
        row = conn.execute("SELECT * FROM chat_schedules WHERE id=?", (schedule["id"],)).fetchone()
        if row is None:
            return
        # `schedule` is the config captured when the fire was claimed. A PATCH
        # committing mid-fire rewrites next_fire_at/enabled from the new config;
        # advancing the cadence from the stale snapshot would clobber it, so
        # only consume the slot while the config is unchanged (commit ordering
        # alone cannot prevent that clobber).
        config_same = all(row[k] == schedule.get(k) for k in _FINISH_CONFIG_FIELDS)
        next_at = row["next_fire_at"]
        enabled = row["enabled"]
        if consume and not manual and config_same:
            if schedule["recur_kind"] == "once":
                enabled = 0
                next_at = None
            else:
                base = parse_iso_utc(slot) or utcnow()
                if status == "skipped_catchup":
                    # Skip every missed slot at once: stepping one period per
                    # tick wrote a junk fire row for each slot of the outage.
                    base = max(base, utcnow())
                nxt = _next_after(schedule, base)
                next_at = iso_utc(nxt) if nxt else None
        conn.execute(
            """UPDATE chat_schedules SET last_fire_at=?, last_status=?, last_error=?, last_run_id=?,
                   next_fire_at=?, enabled=?, firing_at=?, thread_id=COALESCE(?, thread_id), updated_at=?
               WHERE id=?""",
            # firing_at is the claim token of the scheduled fire only; a manual
            # fire never claims one and must not clear a concurrent claim's.
            (now, status, err, run_id or "", next_at, 1 if enabled else 0,
             None if not manual else row["firing_at"], thread_id, now, schedule["id"]),
        )


def _inflight_enter(schedule_id: int) -> None:
    _INFLIGHT[schedule_id] = _INFLIGHT.get(schedule_id, 0) + 1


def _inflight_exit(schedule_id: int) -> None:
    n = _INFLIGHT.get(schedule_id, 0) - 1
    if n > 0:
        _INFLIGHT[schedule_id] = n
        return
    _INFLIGHT.pop(schedule_id, None)


def _retry_due(fire: dict) -> bool:
    """A retryable fire is re-queued at most once per RETRY_COOLDOWN_S."""
    started = parse_iso_utc(str(fire.get("started_at") or ""))
    return started is None or (utcnow() - started).total_seconds() >= RETRY_COOLDOWN_S


def recover() -> None:
    now = utcnow()
    rows = db.fetchall("SELECT * FROM chat_schedules WHERE firing_at IS NOT NULL")
    for raw in rows:
        item = _row(raw)
        age = 0.0
        fired = parse_iso_utc(item.get("firing_at") or "")
        if fired:
            age = (now - fired).total_seconds()
        if item["id"] in _INFLIGHT:
            db.execute("UPDATE chat_schedules SET firing_at=? WHERE id=?", (iso_utc(now), item["id"]))
            continue
        if age <= 120:
            continue
        db.execute("UPDATE chat_schedules SET firing_at=NULL WHERE id=?", (item["id"],))
        slot = item.get("next_fire_at") or ""
        if not slot:
            continue
        fire = db.fetchone(
            "SELECT * FROM schedule_fires WHERE schedule_id=? AND slot_key=?",
            (item["id"], slot),
        )
        if fire is None:
            continue
        fire_d = dict(fire)
        if fire_row_retryable(fire_d, item):
            continue
        if item.get("next_fire_at") == slot:
            _finish_fire(item, {"slot_key": slot, "fire_id": fire_d.get("fire_id")}, fire_d.get("status") or "failed",
                         fire_d.get("error") or "", fire_d.get("thread_id"), fire_d.get("run_id"), consume=True)


async def tick() -> float:
    global _last_reconcile
    recover()
    # reconcile() full-scans schedule_fires; on a long-lived instance that is a
    # growing table, so it runs once a minute rather than on every 5s tick.
    if time.monotonic() - _last_reconcile >= RECONCILE_SECONDS:
        _last_reconcile = time.monotonic()
        reconcile()
    now = utcnow()
    claimed = []
    with db.tx() as conn:
        conn.execute("BEGIN IMMEDIATE")
        rows = conn.execute(
            """SELECT * FROM chat_schedules
               WHERE enabled=1 AND paused=0 AND firing_at IS NULL
                 AND next_fire_at IS NOT NULL AND next_fire_at <= ?
               ORDER BY next_fire_at LIMIT 8""",
            (iso_utc(now),),
        ).fetchall()
        for raw in rows:
            item = _row(raw)
            slot = item.get("next_fire_at") or ""
            if not slot or item["id"] in _INFLIGHT:
                continue
            existing = conn.execute(
                "SELECT * FROM schedule_fires WHERE schedule_id=? AND slot_key=?",
                (item["id"], slot),
            ).fetchone()
            fire_id = str(uuid4())
            fire_status = "queued"
            if existing:
                fire_d = dict(existing)
                if fire_row_retryable(fire_d, item):
                    if not _retry_due(fire_d):
                        continue
                    conn.execute(
                        """UPDATE schedule_fires SET status='queued', started_at=?, error=''
                           WHERE id=?""",
                        (db.now(), existing["id"]),
                    )
                    fire_id = fire_d.get("fire_id") or fire_id
                    # Keep the pre-retry status (e.g. vault_locked): _fire's
                    # grace exemption for a locked one-shot depends on seeing
                    # it, and the row was just overwritten to 'queued'.
                    fire_status = fire_d.get("status") or "queued"
                else:
                    if item.get("next_fire_at") == slot:
                        if item["recur_kind"] == "once":
                            conn.execute(
                                "UPDATE chat_schedules SET enabled=0, next_fire_at=NULL, firing_at=NULL WHERE id=?",
                                (item["id"],),
                            )
                        else:
                            nxt = _next_after(item, parse_iso_utc(slot) or now)
                            conn.execute(
                                "UPDATE chat_schedules SET next_fire_at=?, firing_at=NULL WHERE id=?",
                                (iso_utc(nxt) if nxt else None, item["id"]),
                            )
                    continue
            else:
                conn.execute(
                    """INSERT INTO schedule_fires(schedule_id, user_id, trigger, status, slot_key, fire_id, started_at)
                       VALUES(?,?, 'scheduled', 'queued', ?, ?, ?)""",
                    (item["id"], item["user_id"], slot, fire_id, db.now()),
                )
            conn.execute("UPDATE chat_schedules SET firing_at=? WHERE id=?", (iso_utc(now), item["id"]))
            claimed.append((item, {"slot_key": slot, "fire_id": fire_id, "status": fire_status, "run_id": None}))
    for item, fire in claimed:
        _inflight_enter(item["id"])
        try:
            await _fire(item, fire, consume_slot=True)
        finally:
            _inflight_exit(item["id"])
            db.execute("UPDATE chat_schedules SET firing_at=NULL WHERE id=? AND firing_at IS NOT NULL", (item["id"],))
    row = db.fetchone(
        """SELECT next_fire_at FROM chat_schedules
           WHERE enabled=1 AND paused=0 AND next_fire_at IS NOT NULL
           ORDER BY next_fire_at LIMIT 1"""
    )
    if not row or not row["next_fire_at"]:
        return 5.0
    nxt = parse_iso_utc(row["next_fire_at"])
    if nxt is None:
        return 5.0
    delay = (nxt - utcnow()).total_seconds()
    return max(0.5, min(delay, 5.0))


async def loop(app):
    while True:
        try:
            delay = await tick()
        except Exception:
            logger.exception("schedule tick failed")
            delay = 5.0
        import asyncio
        await asyncio.sleep(max(0.5, min(delay, 5.0)))


async def run_now(user_id: int, token) -> dict:
    if not limiter.allow(f"schedules:{user_id}", 30, 60):
        raise ScheduleError("too many schedule changes", 429)
    item = get_schedule(user_id, token)
    if item is None:
        raise ScheduleError("schedule not found", 404)
    fire_id = str(uuid4())
    slot = "manual:" + fire_id
    now = db.now()
    db.execute(
        """INSERT INTO schedule_fires(schedule_id, user_id, trigger, status, slot_key, fire_id, started_at)
           VALUES(?,?, 'manual', 'queued', ?, ?, ?)""",
        (item["id"], user_id, slot, fire_id, now),
    )
    fire = {"slot_key": slot, "fire_id": fire_id, "status": "queued", "run_id": None}
    _inflight_enter(item["id"])
    try:
        await _fire(item, fire, consume_slot=False)
    finally:
        # Manual fires never claim firing_at; leaving it alone keeps a
        # concurrent scheduled claim's marker intact.
        _inflight_exit(item["id"])
    return get_schedule(user_id, item["public_id"]) or item
