from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from .. import chats, hosts, retention
from .deps import _chat_auth, _json_object, render, require_user

router = APIRouter()


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    """Personal settings that used to be scattered: chat defaults lived only
    in the chat Context sheet, coding-agent switches under Machines ›
    Advanced, and password change had an endpoint but no page."""
    user, redir = require_user(request)
    if redir:
        return redir
    return render(
        request,
        "settings.html",
        user,
        context=chats.get_user_context(user["id"]),
        agent=hosts.settings_for(user["id"]),
        cleanup=retention.get_settings(user["id"]),
    )


@router.get("/settings/cleanup")
async def cleanup_get(request: Request):
    user, err = _chat_auth(request)
    if err:
        return err
    return JSONResponse(retention.get_settings(user["id"]), headers={"Cache-Control": "no-store"})


@router.post("/settings/cleanup")
async def cleanup_set(request: Request):
    user, err = _chat_auth(request)
    if err:
        return err
    raw = await _json_object(request)
    try:
        data = retention.set_settings(user["id"], raw)
    except retention.RetentionError as exc:
        raise HTTPException(422, str(exc)) from None
    return JSONResponse(data)
