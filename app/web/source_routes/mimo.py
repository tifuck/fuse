"""Xiaomi (Mimo) pool routes: key sign-in, callback, complete, cancel,
add and revoke."""

from __future__ import annotations

import time
from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from ... import db
from ...security import limiter
from ...config import settings
from ...sources import invalidate_model_cache
from ...sources import mimo as mimo_mod
from ..deps import _mimo_pending, _pool_flash, logger, require_user
from .context import _mimo_pending_for

router = APIRouter()

def _mimo_redirect_uri(request: Request) -> str:
    base = (settings.public_base_url or "").rstrip("/")
    if not base:
        base = str(request.base_url).rstrip("/")
    return base + "/sources/mimo/callback"


@router.post("/sources/mimo/start")
@router.post("/pool/mimo/start")
async def mimo_start(request: Request):
    user, redir = require_user(request)
    if redir:
        return redir
    if not limiter.allow(f"mimo-start:{user['id']}", 5, 60):
        _pool_flash[user["id"]] = ("err", "Too many attempts. Try again in a minute.", "mimo")
        return RedirectResponse("/sources", status_code=303)
    try:
        data = mimo_mod.start_login()
    except Exception:
        logger.warning("Could not start Xiaomi sign-in", exc_info=True)
        _pool_flash[user["id"]] = ("err", "Could not start Xiaomi sign-in. Try again shortly.", "mimo")
        return RedirectResponse("/sources", status_code=303)
    redirect = _mimo_redirect_uri(request)
    data["manual_url"] = mimo_mod.authorize_url(data["public_key"], manual=True)
    data["auto_url"] = mimo_mod.authorize_url(data["public_key"], redirect_uri=redirect)
    data["_started"] = time.time()
    data["user_id"] = user["id"]
    _mimo_pending[user["id"]] = data
    return RedirectResponse("/sources", status_code=303)


async def _mimo_finish(user: dict, pending: dict, blob: str):
    try:
        payload = mimo_mod.decrypt_payload(str(pending.get("private_key") or ""), blob)
    except Exception:
        return None, "That Xiaomi code could not be read. Start the sign-in again."
    sid = mimo_mod.ensure_mimo_source()
    try:
        cred_id = mimo_mod.store_login(sid, payload, user["id"])
    except ValueError:
        return None, "Xiaomi did not return a Token Plan key. Pick Token Plan in the authorize page and try again."
    row = db.fetchone("SELECT * FROM credentials WHERE id=?", (cred_id,))
    if row:
        try:
            await mimo_mod.refresh_quota(row, force=True)
        except Exception:
            logger.warning("Xiaomi catalog refresh failed after sign-in", exc_info=True)
    invalidate_model_cache("mimo")
    return cred_id, ""


@router.get("/sources/mimo/callback")
@router.get("/pool/mimo/callback")
async def mimo_callback(request: Request):
    user, redir = require_user(request)
    if redir:
        return redir
    pending = _mimo_pending_for(user["id"])
    state = str(request.query_params.get("state") or "").strip()
    blob = str(request.query_params.get("u") or "").strip()
    if not pending or (state and state != str(pending.get("state") or "")):
        _pool_flash[user["id"]] = ("err", "Xiaomi sign-in expired or did not match this session. Start again.", "mimo")
        return RedirectResponse("/sources", status_code=303)
    if not blob:
        _pool_flash[user["id"]] = ("err", "Xiaomi did not return a sign-in code. Paste the code from the authorize page.", "mimo")
        return RedirectResponse("/sources", status_code=303)
    cred_id, err = await _mimo_finish(user, pending, blob)
    _mimo_pending.pop(user["id"], None)
    if err or not cred_id:
        _pool_flash[user["id"]] = ("err", err or "Could not link this Xiaomi account.", "mimo")
        return RedirectResponse("/sources", status_code=303)
    _pool_flash[user["id"]] = ("ok", "Xiaomi Token Plan added to the pool.", "mimo")
    return RedirectResponse("/sources", status_code=303)


@router.post("/sources/mimo/complete")
@router.post("/pool/mimo/complete")
async def mimo_complete(request: Request, code: str = Form("")):
    user, redir = require_user(request)
    if redir:
        return redir
    pending = _mimo_pending_for(user["id"])
    if not pending:
        _pool_flash[user["id"]] = ("err", "Xiaomi sign-in expired. Start again.", "mimo")
        return RedirectResponse("/sources", status_code=303)
    blob = (code or "").strip()
    if "u=" in blob:
        blob = blob.split("u=", 1)[1].split("&", 1)[0]
    cred_id, err = await _mimo_finish(user, pending, blob)
    if err or not cred_id:
        _pool_flash[user["id"]] = ("err", err or "Could not link this Xiaomi account.", "mimo")
        return RedirectResponse("/sources", status_code=303)
    _mimo_pending.pop(user["id"], None)
    _pool_flash[user["id"]] = ("ok", "Xiaomi Token Plan added to the pool.", "mimo")
    return RedirectResponse("/sources", status_code=303)


@router.post("/sources/mimo/cancel")
@router.post("/pool/mimo/cancel")
async def mimo_cancel(request: Request):
    user, redir = require_user(request)
    if redir:
        return redir
    _mimo_pending.pop(user["id"], None)
    return RedirectResponse("/sources", status_code=303)


@router.post("/sources/mimo/add")
@router.post("/pool/mimo/add")
async def mimo_add(request: Request, api_key: str = Form(""), label: str = Form(""), region: str = Form("cn")):
    user, redir = require_user(request)
    if redir:
        return redir
    if not limiter.allow(f"mimo-add:{user['id']}", 5, 60):
        _pool_flash[user["id"]] = ("err", "Too many attempts. Try again in a minute.", "mimo")
        return RedirectResponse("/sources", status_code=303)
    sid = mimo_mod.ensure_mimo_source()
    chosen = region if region in ("cn", "sgp", "ams") else "cn"
    cred_id, err = await mimo_mod.add_plan_key(sid, api_key, user["id"], label=label, region=chosen)
    if err or not cred_id:
        _pool_flash[user["id"]] = ("err", err or "Could not add that key.", "mimo")
        return RedirectResponse("/sources", status_code=303)
    invalidate_model_cache("mimo")
    _pool_flash[user["id"]] = ("ok", "Xiaomi Token Plan key added to the pool.", "mimo")
    return RedirectResponse("/sources", status_code=303)


@router.post("/sources/mimo/{cred_id}/revoke")
@router.post("/pool/mimo/{cred_id}/revoke")
async def mimo_revoke(request: Request, cred_id: int):
    user, redir = require_user(request)
    if redir:
        return redir
    sid = mimo_mod.ensure_mimo_source()
    row = db.fetchone("SELECT id, donated_by, source_id FROM credentials WHERE id=?", (cred_id,))
    if not row or row["source_id"] != sid:
        raise HTTPException(404, "Not found")
    if row["donated_by"] != user["id"] and not user["is_admin"]:
        raise HTTPException(403, "Forbidden")
    mimo_mod.disable_credential(cred_id)
    invalidate_model_cache("mimo")
    _pool_flash[user["id"]] = ("ok", "Xiaomi account removed.", "mimo")
    return RedirectResponse("/sources", status_code=303)
