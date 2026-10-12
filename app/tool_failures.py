"""Durable log of failed dashboard tool calls.

Every failed tool call in the dashboard chat loop lands here with the model,
arguments, error text, and a coarse root-cause kind, so failures can later be
batch-analyzed (by a human or another LLM) to tell apart model errors, prompt
gaps, Fuse bugs, and ordinary upstream outages. Consumed via the admin JSON
endpoint GET /admin/tool-failures, whose top-level shape is analysis_pack().
Writes are best effort and must never break a chat stream.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

from . import db

logger = logging.getLogger(__name__)

RETENTION_ROWS = 5_000
PRUNE_EVERY = 250

ARGUMENTS_MAX = 4_000
TRACE_MAX = 6_000
ERROR_MAX = 600
REASONING_MAX = 2_000
USER_TEXT_MAX = 400
EXCEPTION_MAX = 2_000

# Root-cause buckets. The runtime sets precise kinds at each failure site;
# classify() recovers them for traces that only carry an error string.
KINDS = (
    "internal_exception",  # Fuse bug: exception inside the executor
    "interrupted",  # client disconnect or run cancel mid-tool
    "over_call_cap",  # model emitted more calls than one round accepts
    "malformed_call",  # model emitted a call with no function name
    "unknown_tool",  # model hallucinated a tool name (prompt gap or model error)
    "tool_not_loaded",  # coding catalog: tool used before search_tools loaded it
    "invalid_arguments",  # arguments were not a valid JSON object
    "no_valid_query",  # web_search with no usable query after sanitizing
    "budget_exhausted",  # per-turn search/fetch budget spent
    "not_allowed",  # private/blocked address or non-HTTPS URL
    "permission_denied",  # user, Jev or an unattended run refused a location
    "rate_limited",  # Fuse-side search/read rate limit
    "upstream_error",  # provider or target site failed; not the model's fault
    "not_found",  # job / subagent / child id no longer exists
    "unknown_skill",  # load_skill for a skill the user does not have
    "refused",  # catalog refused to add the tool (load cap)
    "unavailable",  # feature unavailable in this context (e.g. spawn)
    "vault_locked",  # host tools while the vault is locked
    "tool_error",  # anything else
)

_TEXT_HINTS = (
    # Before "timed out": a timed-out permission ask is a denial, not an outage.
    ("permission denied", "permission_denied"),
    ("permission request timed out", "permission_denied"),
    ("permission cannot be requested", "permission_denied"),
    ("unknown tool", "unknown_tool"),
    ("not loaded", "tool_not_loaded"),
    ("invalid json", "invalid_arguments"),
    ("invalid pattern", "invalid_arguments"),
    ("is required", "invalid_arguments"),
    ("invalid include", "invalid_arguments"),
    ("regex parse", "invalid_arguments"),
    ("unclosed group", "invalid_arguments"),
    ("no valid query", "no_valid_query"),
    ("budget", "budget_exhausted"),
    ("rate limit", "rate_limited"),
    ("not an allowed public", "not_allowed"),
    ("domain is not allowed", "not_allowed"),
    ("refused", "not_allowed"),
    ("non-public", "not_allowed"),
    ("outside the project", "not_allowed"),
    ("vault locked", "vault_locked"),
    ("not found", "not_found"),
    ("job not found", "not_found"),
    ("subagent not found", "not_found"),
    ("unknown host", "not_found"),
    ("spawn is not available", "unavailable"),
    ("agent is offline", "unavailable"),
    ("unknown skill", "unknown_skill"),
    ("timed out", "upstream_error"),
    ("timeout", "upstream_error"),
    ("agent is busy", "upstream_error"),
)


def _clip(value, limit: int) -> str:
    if value is None:
        return ""
    return str(value)[:limit]


def _hours_ago(hours: int) -> str:
    stamp = datetime.now(timezone.utc) - timedelta(hours=max(1, hours))
    return stamp.strftime("%Y-%m-%dT%H:%M:%SZ")


def classify(error: str, kind: str = "", name: str = "") -> str:
    kind = (kind or "").strip()
    if kind in KINDS:
        return kind
    low = (error or "").lower()
    if not low:
        return "tool_error"
    for needle, bucket in _TEXT_HINTS:
        if needle in low:
            return bucket
    if "http " in low or "search failed" in low or "timeout" in low:
        return "upstream_error"
    return "tool_error"


def init_schema(connection=None) -> None:
    sql = """
        CREATE TABLE IF NOT EXISTS tool_failures (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            user_id INTEGER,
            thread_id INTEGER,
            run_id TEXT NOT NULL DEFAULT '',
            model TEXT NOT NULL DEFAULT '',
            source_type TEXT NOT NULL DEFAULT '',
            upstream_model TEXT NOT NULL DEFAULT '',
            round_index INTEGER,
            phase TEXT NOT NULL DEFAULT '',
            tool_name TEXT NOT NULL DEFAULT '',
            call_id TEXT NOT NULL DEFAULT '',
            arguments TEXT NOT NULL DEFAULT '',
            error_kind TEXT NOT NULL DEFAULT 'tool_error',
            error TEXT NOT NULL DEFAULT '',
            trace TEXT NOT NULL DEFAULT '',
            exception TEXT NOT NULL DEFAULT '',
            latency_ms INTEGER,
            reasoning TEXT NOT NULL DEFAULT '',
            user_text TEXT NOT NULL DEFAULT ''
        )
    """
    indexes = (
        "CREATE INDEX IF NOT EXISTS tool_failures_created ON tool_failures (created_at)",
        "CREATE INDEX IF NOT EXISTS tool_failures_kind ON tool_failures (error_kind, created_at)",
        "CREATE INDEX IF NOT EXISTS tool_failures_tool ON tool_failures (tool_name, created_at)",
        "CREATE INDEX IF NOT EXISTS tool_failures_model ON tool_failures (model, created_at)",
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


def record(
    *,
    tool_name: str,
    error: str = "",
    kind: str = "",
    arguments: str = "",
    call_id: str = "",
    user_id: int | None = None,
    thread_id: int | None = None,
    run_id: str = "",
    model: str = "",
    source_type: str = "",
    upstream_model: str = "",
    round_index: int | None = None,
    phase: str = "",
    latency_ms: int | None = None,
    trace: dict | None = None,
    exception: str = "",
    reasoning: str = "",
    user_text: str = "",
) -> int | None:
    """Best-effort insert. Never raises."""
    try:
        try:
            trace_json = json.dumps(trace or {}, ensure_ascii=False, default=str)
            if len(trace_json) > TRACE_MAX:
                # Keep a JSON envelope rather than an unparsable sliced object.
                original_chars = len(trace_json)
                trace_json = json.dumps({
                    "truncated": True,
                    "original_chars": original_chars,
                    "preview": trace_json[:1000],
                    **{key: _clip((trace or {}).get(key), 400) for key in ("name", "status", "kind", "error")},
                }, ensure_ascii=False)
        except (TypeError, ValueError):
            trace_json = ""
        row = (
            db.now(),
            int(user_id) if user_id is not None else None,
            int(thread_id) if thread_id is not None else None,
            _clip(run_id, 128),
            _clip(model, 200),
            _clip(source_type, 32),
            _clip(upstream_model, 200),
            int(round_index) if round_index is not None else None,
            _clip(phase, 16),
            _clip(tool_name, 64) or "unknown",
            _clip(call_id, 128),
            _clip(arguments, ARGUMENTS_MAX),
            classify(error, kind=kind, name=tool_name),
            _clip(error, ERROR_MAX),
            trace_json,
            # A traceback's last lines name the exception; keep those.
            str(exception or "")[-EXCEPTION_MAX:],
            int(latency_ms) if latency_ms is not None else None,
            _clip(reasoning, REASONING_MAX),
            _clip(user_text, USER_TEXT_MAX),
        )
        with db.tx() as conn:
            cur = conn.execute(
                """INSERT INTO tool_failures (
                    created_at, user_id, thread_id, run_id, model, source_type, upstream_model,
                    round_index, phase, tool_name, call_id, arguments, error_kind, error,
                    trace, exception, latency_ms, reasoning, user_text
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                row,
            )
            new_id = cur.lastrowid
            if new_id and new_id % PRUNE_EVERY == 0:
                conn.execute(
                    "DELETE FROM tool_failures WHERE id <= "
                    "(SELECT id FROM tool_failures ORDER BY id DESC LIMIT 1 OFFSET ?)",
                    (RETENTION_ROWS,),
                )
        return new_id
    except Exception:
        logger.debug("tool failure log skipped", exc_info=True)
        return None


def _row_view(row) -> dict:
    out = dict(row)
    raw = out.get("trace")
    if isinstance(raw, str) and raw:
        try:
            out["trace"] = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            pass
    return out


def recent(
    *,
    limit: int = 200,
    offset: int = 0,
    kind: str = "",
    tool: str = "",
    model: str = "",
    user_id: int | None = None,
) -> list[dict]:
    limit = max(1, min(int(limit), 500))
    offset = max(0, int(offset))
    clauses = []
    args: list = []
    if kind:
        clauses.append("error_kind=?")
        args.append(kind)
    if tool:
        clauses.append("tool_name=?")
        args.append(tool)
    if model:
        clauses.append("model=?")
        args.append(model)
    if user_id is not None:
        clauses.append("user_id=?")
        args.append(int(user_id))
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    args.extend([limit, offset])
    rows = db.fetchall(
        f"SELECT * FROM tool_failures {where} ORDER BY id DESC LIMIT ? OFFSET ?",
        tuple(args),
    )
    return [_row_view(row) for row in rows]


def summary(*, hours: int = 168) -> dict:
    hours = max(1, min(int(hours), 24 * 365))
    window = _hours_ago(hours)
    out: dict = {"hours": hours, "total": 0, "by_kind": {}, "by_tool": {}, "by_model": {}}
    row = db.fetchone("SELECT COUNT(*) AS n FROM tool_failures WHERE created_at >= ?", (window,))
    out["total"] = int(row["n"]) if row else 0
    for key, column in (("by_kind", "error_kind"), ("by_tool", "tool_name"), ("by_model", "model")):
        rows = db.fetchall(
            f"SELECT {column} AS k, COUNT(*) AS n FROM tool_failures "
            f"WHERE created_at >= ? GROUP BY {column} ORDER BY n DESC LIMIT 50",
            (window,),
        )
        out[key] = {str(r["k"] or "unknown"): int(r["n"]) for r in rows}
    return out


def fingerprints(*, hours: int = 168, limit: int = 40) -> list[dict]:
    """Distinct (tool, kind, error) clusters so common failures surface once."""
    hours = max(1, min(int(hours), 24 * 365))
    window = _hours_ago(hours)
    limit = max(1, min(int(limit), 200))
    rows = db.fetchall(
        """SELECT tool_name, error_kind, error, COUNT(*) AS n,
                  MIN(created_at) AS first_seen, MAX(created_at) AS last_seen,
                  COUNT(DISTINCT model) AS models
           FROM tool_failures WHERE created_at >= ?
           GROUP BY tool_name, error_kind, substr(error, 1, 160)
           ORDER BY n DESC LIMIT ?""",
        (window, limit),
    )
    return [dict(r) for r in rows]


GUIDE = (
    "Failed tool-call log for the Fuse dashboard chat loop. Each failure is one row: "
    "tool_name is the tool the model called; arguments are the JSON arguments it sent "
    "(artifact HTML redacted); error is what the model was told; error_kind is the "
    "root-cause bucket. Reading the buckets: internal_exception = bug in Fuse itself; "
    "malformed_call / unknown_tool / invalid_arguments / no_valid_query / over_call_cap = "
    "model behavior problems (fixable with prompting or schema changes); "
    "tool_not_loaded / refused = coding-catalog usage mistakes; not_allowed = the model "
    "used a URL it was not given, a private address, or a path outside the project; "
    "permission_denied = the user, Jev (Auto mode) or an unattended run refused access to a "
    "location (frequent denials of the same path mean the model keeps reaching outside its grant); "
    "budget_exhausted / rate_limited = Fuse-side limits the model ran into; "
    "upstream_error / not_found = environment or provider problems, not the model "
    "(agent timed out / agent is busy are upstream_error; unknown host alias is not_found); "
    "interrupted = client cancelled; vault_locked / unavailable = context problems "
    "(agent is offline is unavailable). Workspace grep/list errors should include the "
    "connector stderr and exit code, not a bare 'exit'. reasoning holds the model's reasoning right before "
    "the failed call; user_text is the user request that started the turn; model and "
    "upstream_model identify the route. For unknown_tool rows, trace.suggestion (when present) "
    "is the real tool Fuse proposed back to the model, and error contains the exact corrective "
    "reply — repeated failures where suggestion is set mean the model ignored the correction. "
    "Group by fingerprint to find common failures."
)


def analysis_pack(
    *,
    limit: int = 200,
    hours: int = 168,
    kind: str = "",
    tool: str = "",
    model: str = "",
) -> dict:
    return {
        "guide": GUIDE,
        "generated_at": db.now(),
        "summary": summary(hours=hours),
        "fingerprints": fingerprints(hours=hours),
        "recent": recent(limit=limit, kind=kind, tool=tool, model=model),
    }
