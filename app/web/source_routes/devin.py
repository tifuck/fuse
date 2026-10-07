"""Devin pool routes: PKCE sign-in, callback, complete, cancel, add and revoke."""

from __future__ import annotations

import time
from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from ... import db
from ...security import limiter
from ...config import settings
from ...sources import invalidate_model_cache
from ...sources import devin as devin_mod
from ..deps import _devin_pending, _pool_flash, logger, require_user
from .context import _devin_pending_for

router = APIRouter()

def _devin_redirect_uri(request: Request) -> str:
    base = (settings.public_base_url or "").rstrip("/")
    if not base:
        base = str(request.base_url).rstrip("/")
    return base + "/sources/devin/callback"


@router.post("/sources/devin/start")
@router.post("/pool/devin/start")
async def devin_start(request: Request):
    user, redir = require_user(request)
    if redir:
        return redir
    if not limiter.allow(f"devin-start:{user['id']}", 5, 60):
        _pool_flash[user["id"]] = ("err", "Too many attempts. Try again in a minute.", "devin")
        return RedirectResponse("/sources", status_code=303)
    try:
        data = devin_mod.start_cli_login()
    except Exception:
        logger.warning("Could not start Devin PKCE login", exc_info=True)
        _pool_flash[user["id"]] = ("err", "Could not start Devin sign-in. Try again shortly.", "devin")
        return RedirectResponse("/sources", status_code=303)
    if not isinstance(data.get("auth_url"), str) or not data.get("verifier") or not data.get("state"):
        _pool_flash[user["id"]] = ("err", "Devin sign-in could not be prepared. Try again shortly.", "devin")
        return RedirectResponse("/sources", status_code=303)
    data["_started"] = time.time()
    data["user_id"] = user["id"]
    _devin_pending[user["id"]] = data
    return RedirectResponse("/sources", status_code=303)


@router.get("/sources/devin/callback")
@router.get("/pool/devin/callback")
async def devin_callback(request: Request):
    user, redir = require_user(request)
    if redir:
        return redir
    pending = _devin_pending_for(user["id"])
    error = str(request.query_params.get("error") or "").strip()
    if error:
        _devin_pending.pop(user["id"], None)
        denied = error.lower() in ("access_denied", "denied", "cancelled", "canceled")
        msg = "Devin sign-in was cancelled." if denied else "Devin sign-in was denied or expired. Start again."
        _pool_flash[user["id"]] = ("err", msg, "devin")
        return RedirectResponse("/sources", status_code=303)
    code = str(request.query_params.get("code") or "").strip()
    state = str(request.query_params.get("state") or "").strip()
    if not pending or not state or state != str(pending.get("state") or ""):
        _pool_flash[user["id"]] = ("err", "Devin sign-in expired or did not match this session. Start again.", "devin")
        return RedirectResponse("/sources", status_code=303)
    if not code:
        _devin_pending.pop(user["id"], None)
        _pool_flash[user["id"]] = ("err", "Devin did not return an authorization code. Start again.", "devin")
        return RedirectResponse("/sources", status_code=303)
    cred_id, err = await devin_mod.complete_cli_login(
        code,
        str(pending.get("verifier") or ""),
        user["id"],
        redirect_uri=str(pending.get("redirect_uri") or _devin_redirect_uri(request)),
    )
    _devin_pending.pop(user["id"], None)
    if err or not cred_id:
        _pool_flash[user["id"]] = ("err", err or "Could not link this Devin account.", "devin")
        return RedirectResponse("/sources", status_code=303)
    invalidate_model_cache("devin")
    _pool_flash[user["id"]] = ("ok", "Devin account added to the pool.", "devin")
    return RedirectResponse("/sources", status_code=303)


@router.post("/sources/devin/complete")
@router.post("/pool/devin/complete")
async def devin_complete(request: Request, code: str = Form("")):
    user, redir = require_user(request)
    if redir:
        return redir
    pending = _devin_pending_for(user["id"])
    if not pending:
        _pool_flash[user["id"]] = ("err", "Devin sign-in expired. Start again.", "devin")
        return RedirectResponse("/sources", status_code=303)
    cred_id, err = await devin_mod.complete_cli_login(
        code,
        str(pending.get("verifier") or ""),
        user["id"],
    )
    if err or not cred_id:
        _pool_flash[user["id"]] = ("err", err or "Could not link this Devin account.", "devin")
        return RedirectResponse("/sources", status_code=303)
    _devin_pending.pop(user["id"], None)
    invalidate_model_cache("devin")
    _pool_flash[user["id"]] = ("ok", "Devin account added to the pool.", "devin")
    return RedirectResponse("/sources", status_code=303)


@router.post("/sources/devin/cancel")
@router.post("/pool/devin/cancel")
async def devin_cancel(request: Request):
    user, redir = require_user(request)
    if redir:
        return redir
    _devin_pending.pop(user["id"], None)
    return RedirectResponse("/sources", status_code=303)


@router.post("/sources/devin/add")
@router.post("/pool/devin/add")
async def devin_add(request: Request, api_key: str = Form(""), label: str = Form("")):
    user, redir = require_user(request)
    if redir:
        return redir
    if not limiter.allow(f"devin-add:{user['id']}", 5, 60):
        _pool_flash[user["id"]] = ("err", "Too many attempts. Try again in a minute.", "devin")
        return RedirectResponse("/sources", status_code=303)
    sid = devin_mod.ensure_devin_source()
    cred_id, err = await devin_mod.add_session_token(sid, api_key, user["id"], label=label)
    if err or not cred_id:
        _pool_flash[user["id"]] = ("err", err or "Could not add that token.", "devin")
        return RedirectResponse("/sources", status_code=303)
    invalidate_model_cache("devin")
    _pool_flash[user["id"]] = ("ok", "Devin account added to the pool.", "devin")
    return RedirectResponse("/sources", status_code=303)


@router.post("/sources/devin/{cred_id}/revoke")
@router.post("/pool/devin/{cred_id}/revoke")
async def devin_revoke(request: Request, cred_id: int):
    user, redir = require_user(request)
    if redir:
        return redir
    sid = devin_mod.ensure_devin_source()
    row = db.fetchone("SELECT id, donated_by, source_id FROM credentials WHERE id=?", (cred_id,))
    if not row or row["source_id"] != sid:
        raise HTTPException(404, "Not found")
    if row["donated_by"] != user["id"] and not user["is_admin"]:
        raise HTTPException(403, "Forbidden")
    devin_mod.disable_credential(cred_id)
    invalidate_model_cache("devin")
    _pool_flash[user["id"]] = ("ok", "Devin account removed.", "devin")
    return RedirectResponse("/sources", status_code=303)
