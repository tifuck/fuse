from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from .. import chats, hosts
from .deps import render, require_user

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
    )
