"""Z.AI Coding Plan pool routes: add and revoke plan keys."""

from __future__ import annotations

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from ... import db
from ...security import limiter
from ...sources import zai as zai_mod
from ..deps import _pool_flash, require_user

router = APIRouter()

@router.post("/sources/zai/add")
@router.post("/pool/zai/add")
async def zai_add(request: Request, api_key: str = Form(""), label: str = Form("")):
    user, redir = require_user(request)
    if redir:
        return redir
    if not limiter.allow(f"zai-add:{user['id']}", 5, 60):
        _pool_flash[user["id"]] = ("err", "Too many attempts. Try again in a minute.", "zai")
        return RedirectResponse("/sources", status_code=303)
    sid = zai_mod.ensure_zai_source()
    cred_id, err = await zai_mod.add_plan_key(sid, api_key, user["id"], label=label)
    if err or not cred_id:
        _pool_flash[user["id"]] = ("err", err or "Could not add that key.", "zai")
        return RedirectResponse("/sources", status_code=303)
    _pool_flash[user["id"]] = ("ok", "Z.AI Coding Plan key added to the pool.", "zai")
    return RedirectResponse("/sources", status_code=303)


@router.post("/sources/zai/{cred_id}/revoke")
@router.post("/pool/zai/{cred_id}/revoke")
async def zai_revoke(request: Request, cred_id: int):
    user, redir = require_user(request)
    if redir:
        return redir
    sid = zai_mod.ensure_zai_source()
    row = db.fetchone("SELECT id, donated_by, source_id FROM credentials WHERE id=?", (cred_id,))
    if not row or row["source_id"] != sid:
        raise HTTPException(404, "Not found")
    if row["donated_by"] != user["id"] and not user["is_admin"]:
        raise HTTPException(403, "Forbidden")
    zai_mod.disable_credential(cred_id)
    return RedirectResponse("/sources", status_code=303)
