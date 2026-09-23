"""User-reported bug reports and the triage queue behind them.

The chat page exposes a deliberately quiet "report an issue" affordance. A
submission stores what the user typed plus a small non-identifying client
snapshot (page path, view state, user agent, viewport, locale, theme, recent
JS errors). No IP address and no user identity is stored — reports are meant
to be judged on their technical content.

Each report starts in the 'new' status and waits in the queue for the triage
pipeline (app/issues_pipeline.py, disabled by default). Triage output —
category, severity, verdict, follow-up questions, implementation plan — lands
in the `triage` JSON column. Consumed via GET /admin/issues, whose top-level
shape is analysis_pack(), mirroring app/tool_failures.py.
"""
from __future__ import annotations

import json
import logging
import secrets

from . import db

logger = logging.getLogger(__name__)

RETENTION_ROWS = 2_000

SUMMARY_MAX = 4_000
EXPECTED_MAX = 2_000
PAGE_MAX = 300
VIEW_MAX = 40
UA_MAX = 400
CONTEXT_MAX = 6_000
TRIAGE_MAX = 16_000

# Queue lifecycle. 'new' rows are what the triage agent picks up; the rest is
# the pipeline's path from verdict to release. need_info/duplicate/rejected are
# terminal unless a human moves them back.
STATUSES = (
    "new",
    "triaging",
    "triaged",
    "need_info",
    "confirmed",
    "duplicate",
    "rejected",
    "planned",
    "fixing",
    "fixed",
    "released",
)

REF_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"


def init_schema(connection=None) -> None:
    sql = """
        CREATE TABLE IF NOT EXISTS bug_reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            ref TEXT NOT NULL UNIQUE,
            status TEXT NOT NULL DEFAULT 'new',
            attempts INTEGER NOT NULL DEFAULT 0,
            summary TEXT NOT NULL DEFAULT '',
            expected TEXT NOT NULL DEFAULT '',
            page TEXT NOT NULL DEFAULT '',
            view TEXT NOT NULL DEFAULT '',
            user_agent TEXT NOT NULL DEFAULT '',
            context TEXT NOT NULL DEFAULT '',
            triage TEXT NOT NULL DEFAULT ''
        )
    """
    indexes = (
        "CREATE INDEX IF NOT EXISTS bug_reports_status ON bug_reports (status, created_at)",
        "CREATE INDEX IF NOT EXISTS bug_reports_created ON bug_reports (created_at)",
    )
    if connection is not None:
        connection.execute(sql)
        for item in indexes:
            connection.execute(item)
        return
    with db.tx() as conn:
        conn.execute(sql)
        for item in indexes:
            conn.execute(item)


def _clip(value, limit: int) -> str:
    if value is None:
        return ""
    return str(value).strip()[:limit]


def _new_ref() -> str:
    return "F-" + "".join(secrets.choice(REF_ALPHABET) for _ in range(6))


def submit(
    *,
    summary: str,
    expected: str = "",
    page: str = "",
    view: str = "",
    user_agent: str = "",
    context: dict | None = None,
) -> dict | None:
    """Insert one report. Returns {id, ref} or None when rejected as empty."""
    text = _clip(summary, SUMMARY_MAX)
    if len(text) < 4:
        return None
    try:
        context_json = json.dumps(context or {}, ensure_ascii=False, default=str)[:CONTEXT_MAX]
    except (TypeError, ValueError):
        context_json = ""
    ref = _new_ref()
    with db.tx() as conn:
        for _ in range(4):
            try:
                cur = conn.execute(
                    """INSERT INTO bug_reports (
                        created_at, updated_at, ref, status, attempts, summary, expected,
                        page, view, user_agent, context, triage
                    ) VALUES (?,?,?,'new',0,?,?,?,?,?,?,'')""",
                    (
                        db.now(),
                        db.now(),
                        ref,
                        text,
                        _clip(expected, EXPECTED_MAX),
                        _clip(page, PAGE_MAX),
                        _clip(view, VIEW_MAX),
                        _clip(user_agent, UA_MAX),
                        context_json,
                    ),
                )
                return {"id": cur.lastrowid, "ref": ref}
            except Exception:
                ref = _new_ref()
    logger.debug("bug report insert failed")
    return None


def _row_view(row) -> dict:
    out = dict(row)
    for column in ("context", "triage"):
        raw = out.get(column)
        if isinstance(raw, str) and raw:
            try:
                out[column] = json.loads(raw)
            except (json.JSONDecodeError, ValueError):
                pass
    return out


def get(report_id: int) -> dict | None:
    row = db.fetchone("SELECT * FROM bug_reports WHERE id=?", (int(report_id),))
    return _row_view(row) if row else None


def get_by_ref(ref: str) -> dict | None:
    row = db.fetchone("SELECT * FROM bug_reports WHERE ref=?", (_clip(ref, 16),))
    return _row_view(row) if row else None


def recent(
    *,
    limit: int = 200,
    offset: int = 0,
    status: str = "",
) -> list[dict]:
    limit = max(1, min(int(limit), 500))
    offset = max(0, int(offset))
    clauses = []
    args: list = []
    if status:
        clauses.append("status=?")
        args.append(status)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    args.extend([limit, offset])
    rows = db.fetchall(
        f"SELECT * FROM bug_reports {where} ORDER BY id DESC LIMIT ? OFFSET ?",
        tuple(args),
    )
    return [_row_view(row) for row in rows]


def pending(limit: int = 3, *, max_attempts: int = 5) -> list[dict]:
    """Oldest unclaimed reports for the triage agent."""
    limit = max(1, min(int(limit), 10))
    rows = db.fetchall(
        "SELECT * FROM bug_reports WHERE status='new' AND attempts < ? ORDER BY id ASC LIMIT ?",
        (max(1, int(max_attempts)), limit),
    )
    return [_row_view(row) for row in rows]


def update_status(report_id: int, status: str) -> bool:
    if status not in STATUSES:
        return False
    with db.tx() as conn:
        cur = conn.execute(
            "UPDATE bug_reports SET status=?, updated_at=? WHERE id=?",
            (status, db.now(), int(report_id)),
        )
        return cur.rowcount > 0


def set_triage(report_id: int, triage: dict, *, status: str = "triaged") -> bool:
    try:
        triage_json = json.dumps(triage, ensure_ascii=False, default=str)[:TRIAGE_MAX]
    except (TypeError, ValueError):
        return False
    if status not in STATUSES:
        status = "triaged"
    with db.tx() as conn:
        cur = conn.execute(
            # CAS on 'triaging': a concurrent admin decision (any other
            # status) wins and a late pipeline verdict cannot overwrite it.
            "UPDATE bug_reports SET triage=?, status=?, updated_at=? WHERE id=? AND status='triaging'",
            (triage_json, status, db.now(), int(report_id)),
        )
        return cur.rowcount > 0


def claim(report_id: int) -> bool:
    """new -> triaging, so a crash mid-triage is visible in the queue."""
    with db.tx() as conn:
        cur = conn.execute(
            "UPDATE bug_reports SET status='triaging', updated_at=? WHERE id=? AND status='new'",
            (db.now(), int(report_id)),
        )
        return cur.rowcount > 0


def release(report_id: int, *, failed_attempt: bool = False) -> None:
    """Triage could not run — back to the queue, optionally counting an attempt."""
    with db.tx() as conn:
        conn.execute(
            "UPDATE bug_reports SET status='new', attempts=attempts+?, updated_at=? WHERE id=? AND status='triaging'",
            (1 if failed_attempt else 0, db.now(), int(report_id)),
        )


def summary(*, hours: int = 168) -> dict:
    out: dict = {"hours": hours, "total": 0, "by_status": {}, "by_view": {}}
    row = db.fetchone("SELECT COUNT(*) AS n FROM bug_reports")
    out["total"] = int(row["n"]) if row else 0
    for key, column in (("by_status", "status"), ("by_view", "view")):
        rows = db.fetchall(
            f"SELECT {column} AS k, COUNT(*) AS n FROM bug_reports "
            f"GROUP BY {column} ORDER BY n DESC LIMIT 50",
            (),
        )
        out[key] = {str(r["k"] or "unknown"): int(r["n"]) for r in rows}
    return out


GUIDE = (
    "User-reported bug queue for the Fuse dashboard. Each report carries only what "
    "the user typed (summary, optional expected behavior) and a non-identifying "
    "client snapshot: page is the URL path where it happened, view is the UI surface "
    "(chat / projects / settings / artifact), user_agent and context.viewport hint at "
    "the device, context.recent_errors holds the last client-side JS errors before "
    "submission, context.ui_state records sidebar/tab visibility, and context refers "
    "to nothing about who sent it — no IP and no account is stored by design. "
    "status walks the pipeline: new -> triaging -> triaged/need_info/duplicate/rejected "
    "-> confirmed -> planned -> fixing -> fixed -> released. The triage JSON column "
    "holds the agent verdict (category, severity, confidence, hypothesis, "
    "followup questions for the user, and an implementation plan). "
    "context.thread_ref, when present, is the chat thread id the user was on — "
    "admin-side, chat threads can be inspected directly for reproduction."
)


def analysis_pack(*, limit: int = 100, status: str = "") -> dict:
    return {
        "guide": GUIDE,
        "generated_at": db.now(),
        "summary": summary(),
        "recent": recent(limit=limit, status=status),
    }
