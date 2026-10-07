from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from .. import issues, issues_pipeline
from ..config import settings
from ..security import client_ip, limiter
from .deps import _chat_auth, _json_object, require_admin

router = APIRouter()


def _clean_context_value(value, depth: int = 0):
    """Bounded, JSON-safe capture of one client-snapshot value.

    The snapshot's most useful fields are nested (ui_state is an object,
    recent_errors a list of error records), so scalars alone silently drop
    exactly what triage is promised — clip recursively instead."""
    if isinstance(value, (str, int, float, bool)):
        return str(value)[:600]
    if depth >= 3:
        return None
    if isinstance(value, list):
        return [
            cleaned
            for item in value[:20]
            if (cleaned := _clean_context_value(item, depth + 1)) is not None
        ]
    if isinstance(value, dict):
        out = {}
        for key, item in list(value.items())[:20]:
            if not isinstance(key, str):
                continue
            cleaned = _clean_context_value(item, depth + 1)
            if cleaned is not None:
                out[key[:40]] = cleaned
        return out
    return None


@router.post("/report-issue")
async def report_issue(request: Request):
    user, auth_fail = _chat_auth(request)
    if auth_fail:
        return auth_fail
    if not limiter.allow(f"issue:{user['id']}", 5, 3600):
        raise HTTPException(429, "Too many reports; try again later")
    if not limiter.allow(f"issue-ip:{client_ip(request)}", 20, 3600):
        raise HTTPException(429, "Too many reports; try again later")
    body = await _json_object(request)
    context = body.get("context")
    if not isinstance(context, dict):
        context = {}
    clean_context = {}
    for key, value in list(context.items())[:20]:
        if not isinstance(key, str):
            continue
        cleaned = _clean_context_value(value)
        if cleaned is not None:
            clean_context[key[:40]] = cleaned
    record = issues.submit(
        summary=str(body.get("summary") or ""),
        expected=str(body.get("expected") or ""),
        page=str(body.get("page") or request.url.path),
        view=str(body.get("view") or ""),
        user_agent=str(request.headers.get("user-agent") or ""),
        context=clean_context,
    )
    if record is None:
        raise HTTPException(400, "Describe the issue in a few words first")
    return JSONResponse(
        {"ok": True, "ref": record["ref"], "pipeline": bool(settings.issues_pipeline_enabled)},
        headers={"Cache-Control": "no-store"},
    )


@router.get("/admin/issues")
async def admin_issues(request: Request):
    """Analysis pack of every bug report, shaped for triage by an agent."""
    user, redir = require_admin(request)
    if redir:
        return JSONResponse({"error": "auth"}, status_code=401)
    try:
        limit = int(request.query_params.get("limit", "100"))
    except (TypeError, ValueError):
        limit = 100
    status = request.query_params.get("status", "").strip()[:32]
    return JSONResponse(
        issues.analysis_pack(limit=limit, status=status),
        headers={"Cache-Control": "no-store"},
    )


@router.post("/admin/issues/status")
async def admin_issue_status(request: Request):
    """Manual override for the queue lifecycle (mostly used by the pipeline)."""
    user, redir = require_admin(request)
    if redir:
        return JSONResponse({"error": "auth"}, status_code=401)
    body = await _json_object(request)
    try:
        report_id = int(body.get("id"))
    except (TypeError, ValueError):
        raise HTTPException(400, "id") from None
    status = str(body.get("status") or "")
    if status not in issues.MANUAL_STATUSES:
        raise HTTPException(400, "status")
    if not issues.update_status(report_id, status):
        raise HTTPException(404, "Unknown report")
    return JSONResponse({"ok": True, "status": status}, headers={"Cache-Control": "no-store"})
