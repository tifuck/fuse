"""Background triage of user-reported bugs (disabled by default).

Every cycle this takes the oldest unclaimed bug_reports rows, hands each to a
model, and writes the verdict into the report's triage column. The model is
resolved through the site's own sources — llama, OpenAI-compat, or a site
OpenRouter key — never the pooled Grok/ChatGPT/Z.AI/Devin subscriptions.

Verdicts map onto the queue lifecycle:

    bug        -> confirmed   (plan is attached; a later stage can implement)
    duplicate  -> duplicate   (duplicate_of points at the older ref)
    not_a_bug  -> rejected    (reasoning kept in the triage JSON)
    need_info  -> need_info   (followups list what to ask the user)

A report that cannot be triaged (no model available, upstream failure) is
released back to 'new' and retried next cycle; attempts are capped so a
permanently unsuitable model cannot spin forever. Enable with
ISSUES_PIPELINE_ENABLED=1 and optionally ISSUES_PIPELINE_MODEL.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone

from . import db, issues
from .config import settings

logger = logging.getLogger(__name__)

VERDICT_STATUS = {
    "bug": "confirmed",
    "duplicate": "duplicate",
    "not_a_bug": "rejected",
    "need_info": "need_info",
}

SYSTEM_PROMPT = """You triage user-submitted bug reports for Fuse, a self-hosted AI gateway \
with a dashboard (chat UI, projects, artifacts, model sources, host management). \
For each report decide what it is and what should happen next. Reply with ONLY a JSON object:

{
  "verdict": "bug" | "not_a_bug" | "duplicate" | "need_info",
  "category": "ui" | "chat" | "projects" | "artifacts" | "sources" | "hosts" | "skills" | "search" | "auth" | "api" | "performance" | "other",
  "severity": "critical" | "high" | "medium" | "low",
  "confidence": 0.0-1.0,
  "duplicate_of": null or "F-xxxxxx" ref of the older report it duplicates,
  "summary": "one-sentence restatement of the problem",
  "hypothesis": "most likely cause, given the client context",
  "followups": ["question for the user when information is missing"],
  "plan": ["ordered implementation steps, only when verdict is bug"],
  "affected_files": ["guessed file paths in the Fuse codebase, only when verdict is bug"]
}

Rules: the client context is anonymous by design (no IP, no user id) — reason from the
page path, view, user agent, viewport, UI state, and recent_errors fields. Duplicate
detection runs against the other reports supplied; when in doubt prefer need_info over
a guess. Plans must be concrete file-level steps a coding agent could execute."""


def _model_name() -> str:
    return (settings.issues_pipeline_model or "local").strip()


def _other_reports(current_id: int, limit: int = 12) -> list[dict]:
    rows = issues.recent(limit=limit + 1)
    out = []
    for row in rows:
        if row["id"] == current_id:
            continue
        out.append(
            {
                "ref": row["ref"],
                "created_at": row["created_at"],
                "status": row["status"],
                "summary": row["summary"][:600],
                "page": row["page"],
                "view": row["view"],
            }
        )
    return out[:limit]


def _report_payload(report: dict) -> str:
    payload = {
        "ref": report["ref"],
        "submitted_at": report["created_at"],
        "summary": report["summary"],
        "expected": report["expected"],
        "page": report["page"],
        "view": report["view"],
        "user_agent": report["user_agent"],
        "context": report.get("context") or {},
        "other_recent_reports": _other_reports(report["id"]),
    }
    return json.dumps(payload, ensure_ascii=False)


def _parse_verdict(text: str) -> dict | None:
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.lower().startswith("json"):
            raw = raw[4:]
        raw = raw.strip()
    start = raw.find("{")
    end = raw.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        obj = json.loads(raw[start : end + 1])
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(obj, dict):
        return None
    return obj


async def _triage_one(report: dict) -> bool:
    from . import sources

    picked = await sources.pick(_model_name(), allow_grok=False, allow_chatgpt=False, allow_zai=False, allow_devin=False, allow_kimi=False, allow_mimo=False, allow_claude=False)
    if picked is None:
        issues.release(report["id"])
        return False
    src, upstream_model = picked
    body = {
        "model": upstream_model,
        "stream": False,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": _report_payload(report)},
        ],
    }
    try:
        result = await src.chat(body)
    except Exception:
        logger.exception("Bug triage call failed for %s", report["ref"])
        issues.release(report["id"], failed_attempt=True)
        return False
    obj = result.body if result and result.body else {}
    text = ""
    if isinstance(obj, dict):
        choices = obj.get("choices") or []
        if choices and isinstance(choices[0], dict):
            message = choices[0].get("message") or {}
            text = str(message.get("content") or "")
    verdict = _parse_verdict(text)
    if verdict is None:
        logger.warning("Bug triage returned no JSON for %s", report["ref"])
        issues.release(report["id"], failed_attempt=True)
        return False
    status = VERDICT_STATUS.get(str(verdict.get("verdict", "")).strip().lower(), "need_info")
    return bool(issues.set_triage(report["id"], verdict, status=status))


async def tick() -> None:
    """Refresh-loop job: triage a small batch of new reports. Never raises."""
    if not settings.issues_pipeline_enabled:
        return
    recover_stuck()
    batch = issues.pending(settings.issues_pipeline_batch, max_attempts=settings.issues_pipeline_max_attempts)
    for report in batch:
        if not issues.claim(report["id"]):
            continue
        try:
            await _triage_one(report)
        except asyncio.CancelledError:
            # wait_for timeout / shutdown: release the claim or the row is
            # wedged in 'triaging' (invisible to pending()) forever.
            issues.release(report["id"], failed_attempt=True)
            raise
        except Exception:
            logger.exception("Bug triage failed for %s", report["ref"])
            issues.release(report["id"], failed_attempt=True)


def recover_stuck(max_age_seconds: int = 600) -> int:
    """Return crash-orphaned 'triaging' rows to the queue. Called at boot and per tick."""
    cutoff = (
        datetime.now(timezone.utc) - timedelta(seconds=max_age_seconds)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    with db.tx() as conn:
        cur = conn.execute(
            "UPDATE bug_reports SET status='new', updated_at=? WHERE status='triaging' AND updated_at<?",
            (db.now(), cutoff),
        )
        return cur.rowcount
