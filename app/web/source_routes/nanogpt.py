"""NanoGPT pool routes: Sign in with NanoGPT (OAuth PKCE redirect and
callback), device login, pasted keys, revoke, and the owner's model list
and provider pins."""

from __future__ import annotations

import secrets
import time

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse

from ... import db
from ...config import settings
from ...security import limiter
from ...sources import invalidate_model_cache
from ...sources import nanogpt as nanogpt_mod
from ..deps import _nanogpt_pending, _pool_flash, logger, require_user
from .context import _nanogpt_pending_for

router = APIRouter()
_BACK = "/sources#pool-nanogpt"


def _back(user, kind: str, message: str) -> RedirectResponse:
    _pool_flash[user["id"]] = (kind, message, "nanogpt")
    return RedirectResponse(_BACK, status_code=303)


def _redirect_uri(request: Request) -> str:
    """The exact callback NanoGPT returns to, or '' when this address cannot
    receive one (it needs HTTPS, or loopback HTTP with a port)."""
    base = (settings.public_base_url or "").rstrip("/") or str(request.base_url).rstrip("/")
    uri = base + "/sources/nanogpt/callback"
    return uri if nanogpt_mod.redirect_ok(uri) else ""


async def _link(user, key: str, *, auth_kind: str, account_id: str = "") -> tuple[int | None, str]:
    sid = nanogpt_mod.ensure_nanogpt_source()
    try:
        cred_id = nanogpt_mod.save_key(sid, key, user["id"], auth_kind=auth_kind, account_id=account_id)
    except ValueError:
        return None, "That NanoGPT account is already linked by another Fuse user."
    row = db.fetchone("SELECT * FROM credentials WHERE id=?", (cred_id,))
    if row:
        try:
            await nanogpt_mod.refresh_usage(row, force=True)
        except Exception:
            logger.warning("NanoGPT usage refresh failed after sign-in", exc_info=True)
    invalidate_model_cache("nanogpt")
    return cred_id, ""


def _linked_message(cred_id: int) -> str:
    row = db.fetchone("SELECT * FROM credentials WHERE id=?", (cred_id,))
    try:
        lendable = bool(row) and nanogpt_mod.lendable_row(row)
    except Exception:
        lendable = False
    if lendable:
        return "NanoGPT account linked. Its subscription is shared as your Sharing setting allows."
    return (
        "NanoGPT account linked. Only you use it: it can spend your NanoGPT balance. To share its "
        "subscription, set the key's billing to Subscription only on nano-gpt.com/api."
    )


@router.get("/sources/nanogpt/start")
async def nanogpt_start(request: Request):
    """A link, not a form: the CSP's form-action 'self' blocks a form whose
    redirect leaves the site. It only opens a PKCE round; nothing is linked
    until the user approves on NanoGPT and the callback's state matches."""
    user, redir = require_user(request)
    if redir:
        return redir
    if not limiter.allow(f"nanogpt-start:{user['id']}", 5, 60):
        return _back(user, "err", "Too many attempts. Try again in a minute.")
    uri = _redirect_uri(request)
    if not uri:
        return _back(user, "err", "This Fuse address cannot receive NanoGPT's sign-in redirect (it needs HTTPS). Use device sign-in instead.")
    data = nanogpt_mod.start_login(uri)
    data["_started"] = time.time()
    _nanogpt_pending[user["id"]] = data
    return RedirectResponse(data["auth_url"], status_code=303)


@router.get("/sources/nanogpt/callback")
async def nanogpt_callback(request: Request):
    user, redir = require_user(request)
    if redir:
        return redir
    pending = _nanogpt_pending_for(user["id"])
    state = str(request.query_params.get("state") or "")
    if not pending or pending.get("kind") != "oauth" or not secrets.compare_digest(state, str(pending.get("state") or "")):
        # Keep a pending sign-in on a mismatch: a forged callback must not cancel it.
        return _back(user, "err", "NanoGPT sign-in expired or did not match this session. Start again.")
    _nanogpt_pending.pop(user["id"], None)
    if request.query_params.get("error"):
        return _back(user, "err", "NanoGPT sign-in was cancelled.")
    code = str(request.query_params.get("code") or "").strip()
    if not code or len(code) > 4096:
        return _back(user, "err", "NanoGPT did not return a sign-in code. Start again.")
    try:
        key, account_id = await nanogpt_mod.exchange_code(code, str(pending["verifier"]))
    except ValueError as exc:
        return _back(user, "err", str(exc))
    cred_id, err = await _link(user, key, auth_kind="oauth", account_id=account_id)
    if err or not cred_id:
        return _back(user, "err", err or "Could not link this NanoGPT account.")
    return _back(user, "ok", _linked_message(cred_id))


@router.post("/sources/nanogpt/device")
async def nanogpt_device_start(request: Request):
    user, redir = require_user(request)
    if redir:
        return redir
    if not limiter.allow(f"nanogpt-start:{user['id']}", 5, 60):
        return _back(user, "err", "Too many attempts. Try again in a minute.")
    try:
        data = await nanogpt_mod.start_device()
    except Exception:
        logger.warning("Could not start NanoGPT device login", exc_info=True)
        return _back(user, "err", "Could not start NanoGPT sign-in. Try again shortly.")
    data["_started"] = time.time()
    _nanogpt_pending[user["id"]] = data
    return RedirectResponse(_BACK, status_code=303)


@router.post("/sources/nanogpt/poll")
async def nanogpt_poll(request: Request):
    user, redir = require_user(request)
    if redir:
        return JSONResponse({"error": "auth"}, status_code=401)
    pending = _nanogpt_pending_for(user["id"])
    if not pending or pending.get("kind") != "device":
        return JSONResponse({"error": "No NanoGPT sign-in in progress."}, status_code=400)
    interval = pending.get("interval", 5)
    try:
        data = await nanogpt_mod.poll_device(str(pending["device_code"]))
    except Exception:
        logger.warning("NanoGPT device poll failed transiently", exc_info=True)
        return JSONResponse({"pending": True, "error": "NanoGPT could not be reached; retrying.", "retry_after": interval}, status_code=502)
    if data.get("pending"):
        return JSONResponse({"pending": True, "retry_after": interval})
    if data.get("error") or not data.get("key"):
        _nanogpt_pending.pop(user["id"], None)
        return JSONResponse({"error": "NanoGPT sign-in expired or was already used. Start again."}, status_code=400)
    # Cancelled or restarted while NanoGPT answered: do not link it.
    if _nanogpt_pending.get(user["id"]) is not pending:
        return JSONResponse({"error": "That NanoGPT sign-in was cancelled or replaced."}, status_code=409)
    _nanogpt_pending.pop(user["id"], None)
    cred_id, err = await _link(user, data["key"], auth_kind="device")
    if err or not cred_id:
        return JSONResponse({"error": err or "Could not link this NanoGPT account."}, status_code=400)
    _pool_flash[user["id"]] = ("ok", _linked_message(cred_id), "nanogpt")
    return JSONResponse({"ok": True})


@router.post("/sources/nanogpt/cancel")
async def nanogpt_cancel(request: Request):
    user, redir = require_user(request)
    if redir:
        return redir
    _nanogpt_pending.pop(user["id"], None)
    return RedirectResponse(_BACK, status_code=303)


@router.post("/sources/nanogpt/add")
async def nanogpt_add(request: Request, api_key: str = Form(""), label: str = Form("")):
    user, redir = require_user(request)
    if redir:
        return redir
    if not limiter.allow(f"nanogpt-add:{user['id']}", 5, 60):
        return _back(user, "err", "Too many attempts. Try again in a minute.")
    cred_id, err = await nanogpt_mod.add_key(nanogpt_mod.ensure_nanogpt_source(), api_key, user["id"], label=label)
    if err or not cred_id:
        return _back(user, "err", err or "Could not add that key.")
    _nanogpt_pending.pop(user["id"], None)
    invalidate_model_cache("nanogpt")
    return _back(user, "ok", _linked_message(cred_id))


@router.post("/sources/nanogpt/{cred_id}/revoke")
async def nanogpt_revoke(request: Request, cred_id: int):
    user, redir = require_user(request)
    if redir:
        return redir
    row = db.fetchone("SELECT id, donated_by, source_id FROM credentials WHERE id=?", (cred_id,))
    if not row or row["source_id"] != nanogpt_mod.ensure_nanogpt_source():
        raise HTTPException(404, "Not found")
    if row["donated_by"] != user["id"] and not user["is_admin"]:
        raise HTTPException(403, "Forbidden")
    nanogpt_mod.disable_credential(cred_id)
    invalidate_model_cache("nanogpt")
    return _back(
        user,
        "ok",
        "NanoGPT account removed from Fuse. NanoGPT has no revoke API: delete the \"OAuth: Fuse\" key on nano-gpt.com/api to end its access.",
    )


# --- The owner's model list (same JSON contract as OpenRouter's) -------------

async def _owner_json(request: Request):
    user, redir = require_user(request)
    if redir:
        return None, None, JSONResponse({"error": "auth"}, status_code=401)
    if not nanogpt_mod.nanogpt_own_access(user["id"])["ok"]:
        return None, None, JSONResponse({"error": "Sign in with NanoGPT first."}, status_code=400)
    if request.method == "GET":
        return user, {}, None
    try:
        body = await request.json()
    except Exception:
        body = None
    if not isinstance(body, dict):
        return None, None, JSONResponse({"error": "Invalid JSON"}, status_code=400)
    return user, body, None


def _by_id() -> dict:
    return (nanogpt_mod.cached_catalog() or {}).get("by_id") or {}


@router.post("/sources/nanogpt/settings")
async def nanogpt_settings(request: Request):
    """free_models is the subscription-models switch (the shared list UI's name)."""
    user, body, err = await _owner_json(request)
    if err:
        return err
    sub = body.get("free_models")
    paid_all = body.get("paid_all")
    prefs = nanogpt_mod.set_model_switches(
        user["id"],
        subscription=sub if isinstance(sub, bool) else None,
        paid_all=paid_all if isinstance(paid_all, bool) else None,
    )
    invalidate_model_cache("nanogpt")
    return JSONResponse({"free_models": prefs["subscription"], "paid_all": prefs["paid_all"]})


@router.post("/sources/nanogpt/model")
async def nanogpt_model_toggle(request: Request):
    user, body, err = await _owner_json(request)
    if err:
        return err
    model = str(body.get("model") or "").strip()
    rec = _by_id().get(model)
    if not rec or rec["subscription"]:
        return JSONResponse({"error": "Unknown paid model."}, status_code=400)
    if nanogpt_mod.model_prefs(user["id"])["paid_all"]:
        return JSONResponse({"error": "Every paid model is already on."}, status_code=409)
    if not nanogpt_mod.set_paid_model(user["id"], model, bool(body.get("enabled"))):
        return JSONResponse({"error": f"You can enable up to {nanogpt_mod.MAX_PAID_MODELS} paid models."}, status_code=400)
    invalidate_model_cache("nanogpt")
    return JSONResponse({"enabled": model in nanogpt_mod.model_prefs(user["id"])["paid"]})


@router.get("/sources/nanogpt/providers")
async def nanogpt_model_providers(request: Request, model: str = ""):
    user, _body, err = await _owner_json(request)
    if err:
        return err
    model = model.strip()
    if model not in _by_id():
        return JSONResponse({"error": "Unknown model."}, status_code=404)
    if not limiter.allow(f"nanogpt-providers:{user['id']}", 60, 60):
        return JSONResponse({"error": "Too many requests. Try again in a minute."}, status_code=429)
    rows = await nanogpt_mod.fetch_model_providers(model)
    if rows is None:
        return JSONResponse({"error": "NanoGPT is not reachable right now."}, status_code=503)
    selected = nanogpt_mod.model_prefs(user["id"])["providers"].get(model) or []
    return JSONResponse({"providers": rows, "selected": selected})


@router.post("/sources/nanogpt/providers")
async def nanogpt_model_providers_save(request: Request):
    user, body, err = await _owner_json(request)
    if err:
        return err
    model = str(body.get("model") or "").strip()
    slugs = body.get("providers")
    if model not in _by_id() or not isinstance(slugs, list):
        return JSONResponse({"error": "Invalid request."}, status_code=400)
    if not nanogpt_mod.set_model_providers(user["id"], model, [s for s in slugs if isinstance(s, str)]):
        return JSONResponse(
            {"error": f"You can pin providers on up to {nanogpt_mod.MAX_PINNED_MODELS} models."}, status_code=400
        )
    return JSONResponse({"selected": nanogpt_mod.model_prefs(user["id"])["providers"].get(model) or []})


def model_rows(user_id: int) -> list[dict]:
    """Every catalog model with this account's switch state, list price and
    pinned-provider count, shaped like OpenRouter's rows for the shared list UI
    (`free` marks a subscription model)."""
    prefs = nanogpt_mod.model_prefs(user_id)
    paid = set(prefs["paid"])
    rows = []
    for mid, rec in _by_id().items():
        pricing = rec.get("pricing") or {}
        sub = bool(rec["subscription"])
        rows.append(
            {
                "id": mid,
                "name": rec.get("name") or mid,
                "free": sub,
                "enabled": prefs["subscription"] if sub else (prefs["paid_all"] or mid in paid),
                "input": nanogpt_mod._as_float(pricing.get("prompt")),
                "output": nanogpt_mod._as_float(pricing.get("completion")),
                "context": rec.get("context_length"),
                "pinned": len(prefs["providers"].get(mid) or []),
            }
        )
    rows.sort(key=lambda row: (not row["enabled"], row["name"].lower()))
    return rows
