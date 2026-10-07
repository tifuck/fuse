from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse

from .. import push
from ..config import ROOT
from ..security import limiter
from .deps import _chat_auth, _json_object

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/sw.js")
async def service_worker():
    return FileResponse(
        ROOT / "static" / "sw.js",
        media_type="text/javascript",
        headers={"Cache-Control": "no-store"},
    )


@router.get("/notifications/status")
async def notifications_status(request: Request):
    user, err = _chat_auth(request)
    if err:
        return err
    return JSONResponse(
        {
            "enabled": push.ready(),
            "vapid": push.vapid_public_key(),
            "count": push.count_for(user["id"]),
        },
        headers={"Cache-Control": "no-store"},
    )


@router.post("/notifications/subscribe")
async def notifications_subscribe(request: Request):
    user, err = _chat_auth(request)
    if err:
        return err
    raw = await _json_object(request)
    if set(raw) - {"endpoint", "keys", "user_agent"}:
        raise HTTPException(422, "Unsupported subscription fields")
    endpoint = raw.get("endpoint")
    keys = raw.get("keys")
    if not isinstance(endpoint, str) or not endpoint.startswith("https://") or len(endpoint) > 2048:
        raise HTTPException(422, "Invalid endpoint")
    if not isinstance(keys, dict) or set(keys) - {"p256dh", "auth"}:
        raise HTTPException(422, "Invalid keys")
    p256dh, auth = keys.get("p256dh"), keys.get("auth")
    for value in (p256dh, auth):
        if not isinstance(value, str) or not value or len(value) > 512:
            raise HTTPException(422, "Invalid keys")
    agent = raw.get("user_agent") if isinstance(raw.get("user_agent"), str) else ""
    if not limiter.allow(f"push-subscribe:{user['id']}", 20, 60):
        raise HTTPException(429, "Too many subscription updates")
    try:
        push.save_subscription(user["id"], endpoint, p256dh, auth, agent[:300])
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    return JSONResponse({"ok": True, "count": push.count_for(user["id"])})


@router.post("/notifications/unsubscribe")
async def notifications_unsubscribe(request: Request):
    user, err = _chat_auth(request)
    if err:
        return err
    raw = await _json_object(request)
    if set(raw) - {"endpoint"} or not isinstance(raw.get("endpoint"), str) or len(raw["endpoint"]) > 2048:
        raise HTTPException(422, "Invalid endpoint")
    push.remove_subscription(user["id"], raw["endpoint"])
    return JSONResponse({"ok": True, "count": push.count_for(user["id"])})


@router.post("/notifications/test")
async def notifications_test(request: Request):
    user, err = _chat_auth(request)
    if err:
        return err
    if not limiter.allow(f"push-test:{user['id']}", 4, 300):
        raise HTTPException(429, "Too many test notifications")
    sent = await push.notify_user(
        user["id"],
        {
            "title": "Fuse",
            "body": "Test notification — finished chats will look like this.",
            "url": "/chat",
        },
    )
    return JSONResponse({"ok": True, "sent": sent}, headers={"Cache-Control": "no-store"})
