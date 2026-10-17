"""Cursor pool routes: browser sign-in (start, poll, cancel), API key add, revoke."""

from __future__ import annotations

import time
from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from ... import db
from ...security import limiter
from ...sources import invalidate_model_cache
from ...sources import cursor as cursor_mod
from ..deps import _cursor_pending, _pool_flash, logger, require_user
from .context import _cursor_pending_for

router = APIRouter()


@router.post("/sources/cursor/start")
@router.post("/pool/cursor/start")
async def cursor_start(request: Request):
    user, redir = require_user(request)
    if redir:
        return redir
    if not limiter.allow(f"cursor-start:{user['id']}", 5, 60):
        _pool_flash[user["id"]] = ("err", "Too many attempts. Try again in a minute.", "cursor")
        return RedirectResponse("/sources#pool-cursor", status_code=303)
    data = cursor_mod.start_login()
    data["_started"] = time.time()
    _cursor_pending[user["id"]] = data
    return RedirectResponse("/sources#pool-cursor", status_code=303)


@router.post("/sources/cursor/poll")
@router.post("/pool/cursor/poll")
async def cursor_poll(request: Request):
    user, redir = require_user(request)
    if redir:
        return JSONResponse({"error": "auth"}, status_code=401)
    pending = _cursor_pending_for(user["id"])
    if not pending:
        return JSONResponse({"error": "No Cursor sign-in in progress."}, status_code=400)
    interval = pending.get("interval", 3)
    try:
        data = await cursor_mod.poll_login(str(pending["uuid"]), str(pending["verifier"]))
    except Exception:
        logger.warning("Cursor sign-in poll failed transiently", exc_info=True)
        return JSONResponse({"pending": True, "error": "Cursor could not be reached; retrying.", "retry_after": interval}, status_code=502)
    if data.get("error"):
        _cursor_pending.pop(user["id"], None)
        return JSONResponse({"error": "Cursor sign-in was denied or expired. Start again."}, status_code=400)
    if data.get("pending"):
        return JSONResponse({"pending": True, "retry_after": interval})
    # Cancelled or restarted while Cursor answered: do not donate it.
    if _cursor_pending.get(user["id"]) is not pending:
        return JSONResponse({"error": "That Cursor sign-in was cancelled or replaced."}, status_code=409)
    _cursor_pending.pop(user["id"], None)
    sid = cursor_mod.ensure_cursor_source()
    try:
        cred_id = cursor_mod.store_tokens(sid, data["access_token"], data["refresh_token"], user["id"])
    except ValueError:
        return JSONResponse({"error": "Cursor returned an invalid token. Start again."}, status_code=502)
    row = db.fetchone("SELECT * FROM credentials WHERE id=?", (cred_id,))
    if row:
        try:
            await cursor_mod.refresh_quota(row, force=True)
        except Exception:
            logger.warning("Cursor usage refresh failed after sign-in", exc_info=True)
    invalidate_model_cache("cursor")
    _pool_flash[user["id"]] = ("ok", "Cursor account added to the pool.", "cursor")
    return JSONResponse({"ok": True})


@router.post("/sources/cursor/cancel")
@router.post("/pool/cursor/cancel")
async def cursor_cancel(request: Request):
    user, redir = require_user(request)
    if redir:
        return redir
    _cursor_pending.pop(user["id"], None)
    return RedirectResponse("/sources#pool-cursor", status_code=303)


@router.post("/sources/cursor/add")
@router.post("/pool/cursor/add")
async def cursor_add(request: Request, api_key: str = Form(""), label: str = Form("")):
    user, redir = require_user(request)
    if redir:
        return redir
    if not limiter.allow(f"cursor-add:{user['id']}", 5, 60):
        _pool_flash[user["id"]] = ("err", "Too many attempts. Try again in a minute.", "cursor")
        return RedirectResponse("/sources#pool-cursor", status_code=303)
    sid = cursor_mod.ensure_cursor_source()
    cred_id, err = await cursor_mod.add_api_key(sid, api_key, user["id"], label=label)
    if err or not cred_id:
        _pool_flash[user["id"]] = ("err", err or "Could not add that key.", "cursor")
        return RedirectResponse("/sources#pool-cursor", status_code=303)
    _cursor_pending.pop(user["id"], None)
    invalidate_model_cache("cursor")
    _pool_flash[user["id"]] = ("ok", "Cursor account added to the pool.", "cursor")
    return RedirectResponse("/sources#pool-cursor", status_code=303)


@router.post("/sources/cursor/{cred_id}/revoke")
@router.post("/pool/cursor/{cred_id}/revoke")
async def cursor_revoke(request: Request, cred_id: int):
    user, redir = require_user(request)
    if redir:
        return redir
    sid = cursor_mod.ensure_cursor_source()
    row = db.fetchone("SELECT id, donated_by, source_id FROM credentials WHERE id=?", (cred_id,))
    if not row or row["source_id"] != sid:
        raise HTTPException(404, "Not found")
    if row["donated_by"] != user["id"] and not user["is_admin"]:
        raise HTTPException(403, "Forbidden")
    cursor_mod.disable_credential(cred_id)
    invalidate_model_cache("cursor")
    _pool_flash[user["id"]] = ("ok", "Cursor account removed.", "cursor")
    return RedirectResponse("/sources", status_code=303)
