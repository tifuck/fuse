from __future__ import annotations

import logging

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from starlette.background import BackgroundTask

from .. import accounts, chat_events, vault
from ..security import ensure_csrf
from .deps import (
    _auth_limited,
    _chat_auth,
    _clear_vault_cookie,
    _json_object,
    _set_vault_cookie,
    _stash_key_reveal,
    current_user,
    logger,
    render,
    require_user,
)

router = APIRouter()

@router.get("/login", response_class=HTMLResponse)
async def login_get(request: Request):
    if current_user(request):
        return RedirectResponse("/chat", status_code=303)
    return render(request, "login.html", error=None)


@router.post("/login")
async def login_post(request: Request, email: str = Form(), password: str = Form()):
    if _auth_limited(request):
        return render(request, "login.html", error="Too many attempts. Try again in a minute.", status_code=429)
    user = accounts.login(email, password)
    if not user:
        return render(request, "login.html", error="Email or password is wrong.", status_code=401)
    request.session["uid"] = user["id"]
    request.session["ph"] = accounts.password_fingerprint(user)
    request.session.pop("csrf", None)
    ensure_csrf(request)
    resp = RedirectResponse("/chat", status_code=303)
    try:
        vault.unlock(user["id"], password)
        _set_vault_cookie(resp, user["id"])
    except Exception:
        logger.exception("vault unlock after login failed")
    return resp


_REGISTER_CLOSED = "User registration is disabled."


@router.get("/register", response_class=HTMLResponse)
async def register_get(request: Request):
    if current_user(request):
        return RedirectResponse("/chat", status_code=303)
    return render(request, "register.html", error=None, invite=request.query_params.get("invite", "")[:128])


@router.post("/register")
async def register_post(request: Request, email: str = Form(), password: str = Form(), invite: str = Form("")):
    if _auth_limited(request):
        return render(request, "register.html", error="Too many attempts. Try again in a minute.", status_code=429,
                      invite=invite[:128])
    try:
        user = accounts.register(email, password, invite[:128])
    except ValueError as exc:
        mapped = {
            "email": ("Enter a valid email address.", 400),
            "password": ("Password must be between 8 and 256 characters.", 400),
            "exists": ("An account with that email already exists.", 409),
            "closed": (_REGISTER_CLOSED, 403),
        }.get(str(exc))
        if mapped is None:
            raise
        error, status = mapped
        return render(request, "register.html", error=error, status_code=status, invite=invite[:128])
    request.session["uid"] = user["id"]
    request.session["ph"] = accounts.password_fingerprint(user)
    request.session.pop("csrf", None)
    ensure_csrf(request)
    resp = RedirectResponse("/chat", status_code=303)
    try:
        vault.unlock(user["id"], password)
        _set_vault_cookie(resp, user["id"])
    except Exception:
        logger.exception("vault unlock after register failed")
    _stash_key_reveal(request, user["id"], user["new_key"])
    return resp


@router.post("/logout")
async def logout(request: Request):
    uid = request.session.get("uid")
    await chat_events.close_session(uid, request.session.get("csrf"))
    try:
        from .. import host_events
        await host_events.close_user(uid)
    except Exception:
        logger.exception("host events close on logout failed")
    if uid:
        vault.lock(uid)
        try:
            from .. import ssh_pool, agent_hub
            await ssh_pool.close_user(uid)
            await agent_hub.close_user(uid)
        except Exception:
            logger.exception("ssh pool drain on logout failed")
    request.session.clear()
    resp = RedirectResponse("/login", status_code=303)
    _clear_vault_cookie(resp)
    return resp


@router.post("/account/password")
async def account_password(request: Request):
    user, err = _chat_auth(request)
    if err:
        return err
    if _auth_limited(request):
        raise HTTPException(429, "Too many attempts. Try again in a minute.")
    raw = await _json_object(request)
    old = raw.get("old_password")
    new = raw.get("new_password")
    if not isinstance(old, str) or not isinstance(new, str):
        raise HTTPException(400, "invalid password")
    try:
        accounts.change_password(user["id"], old, new)
    except ValueError as exc:
        code = str(exc)
        if code == "auth":
            raise HTTPException(401, "Current password is wrong.") from None
        if code == "password":
            raise HTTPException(400, "Password must be between 8 and 256 characters.") from None
        raise HTTPException(400, "Password could not be changed.") from None
    from .. import host_events

    async def _reauth_sockets():
        # After the response, so this browser already holds its re-stamped
        # cookie when its sockets reconnect; revoked cookies then get 4401.
        await chat_events.reauth_user(user["id"])
        await host_events.close_user(user["id"], code=1012)

    resp = JSONResponse({"ok": True}, background=BackgroundTask(_reauth_sockets))
    request.session["ph"] = accounts.password_fingerprint(accounts.get_user(user["id"]))
    _set_vault_cookie(resp, user["id"])
    return resp


@router.post("/account/vault/unlock")
async def account_vault_unlock(request: Request):
    user, err = _chat_auth(request)
    if err:
        return err
    if _auth_limited(request):
        raise HTTPException(429, "Too many attempts. Try again in a minute.")
    raw = await _json_object(request)
    password = raw.get("password")
    if not isinstance(password, str) or not password:
        raise HTTPException(400, "password required")
    try:
        accounts.unlock_vault(user["id"], password)
    except ValueError:
        raise HTTPException(401, "Password is wrong.") from None
    resp = JSONResponse({"ok": True, "vault": "unlocked"})
    _set_vault_cookie(resp, user["id"])
    return resp

