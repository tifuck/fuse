"""Sources and pool routes, split by provider under source_routes/.

This module composes the provider routers and keeps the Experiential Labs key
routes, which tests drive and patch directly on this module.
"""

from __future__ import annotations

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from .. import accounts, db, stats  # accounts/stats re-exported: tests patch app.web.sources.stats / .accounts
from ..security import limiter
from ..sources import explabs as explabs_mod, invalidate_model_cache
from .deps import _notices, _pool_flash, require_admin, require_user
from .source_routes import (
    pool_page,
    grok,
    chatgpt,
    zai,
    devin,
    kimi,
    mimo,
    claude,
    cursor,
    nanogpt,
    openrouter,
    admin_pages,
    admin_actions,
)
from .source_routes.admin_pages import _ollama_key_views
from .source_routes.context import _pool_card  # public: app.web re-exports _pool_card from here

router = APIRouter()


@router.post("/sources/explabs/add")
async def explabs_add(request: Request, api_key: str = Form(""), label: str = Form("")):
    user, redir = require_user(request)
    if redir:
        return redir
    if not limiter.allow(f"explabs-add:{user['id']}", 5, 60):
        _pool_flash[user["id"]] = ("err", "Too many attempts. Try again in a minute.", "explabs")
        return RedirectResponse("/sources", status_code=303)
    sid = explabs_mod.ensure_explabs_source()
    cred_id, err = await explabs_mod.add_user_key(sid, api_key, user["id"], label=label)
    if err or not cred_id:
        _pool_flash[user["id"]] = ("err", err or "Could not add that key.", "explabs")
        return RedirectResponse("/sources", status_code=303)
    invalidate_model_cache("explabs")
    _pool_flash[user["id"]] = ("ok", "Experiential Labs key saved for your account.", "explabs")
    return RedirectResponse("/sources", status_code=303)


@router.post("/sources/explabs/{cred_id}/revoke")
async def explabs_revoke(request: Request, cred_id: int):
    user, redir = require_user(request)
    if redir:
        return redir
    sid = explabs_mod.ensure_explabs_source()
    row = db.fetchone("SELECT id, donated_by, source_id FROM credentials WHERE id=?", (cred_id,))
    if not row or row["source_id"] != sid:
        raise HTTPException(404, "Not found")
    if row["donated_by"] != user["id"] and not user["is_admin"]:
        raise HTTPException(403, "Forbidden")
    explabs_mod.disable_credential(cred_id)
    invalidate_model_cache("explabs")
    _pool_flash[user["id"]] = ("ok", "Experiential Labs key removed.", "explabs")
    return RedirectResponse("/sources", status_code=303)


@router.post("/admin/sources/explabs")
async def admin_explabs_save(request: Request, enabled: str = Form("0")):
    user, redir = require_admin(request)
    if redir:
        return redir
    sid = explabs_mod.ensure_explabs_source()
    on = 1 if enabled in ("1", "on", "true") else 0
    db.execute("UPDATE sources SET enabled=? WHERE id=?", (on, sid))
    invalidate_model_cache("explabs")
    _notices[user["id"]] = "Experiential Labs settings saved."
    return RedirectResponse("/admin/sources", status_code=303)



router.include_router(pool_page.router)
router.include_router(grok.router)
router.include_router(chatgpt.router)
router.include_router(zai.router)
router.include_router(devin.router)
router.include_router(kimi.router)
router.include_router(mimo.router)
router.include_router(claude.router)
router.include_router(cursor.router)
router.include_router(nanogpt.router)
router.include_router(openrouter.router)
router.include_router(admin_pages.router)
router.include_router(admin_actions.router)
