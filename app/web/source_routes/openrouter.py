"""OpenRouter per-user routes: own key, free/paid model switches, model
providers and the account settings endpoint."""

from __future__ import annotations

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from ... import accounts, db
from ...security import limiter
from ...sources import invalidate_model_cache
from ...sources import openrouter as openrouter_mod
from ..deps import _pool_flash, require_user

router = APIRouter()

@router.post("/sources/openrouter/add")
@router.post("/pool/openrouter/add")
async def openrouter_add(request: Request, api_key: str = Form(""), label: str = Form("")):
    user, redir = require_user(request)
    if redir:
        return redir
    if not limiter.allow(f"openrouter-add:{user['id']}", 5, 60):
        _pool_flash[user["id"]] = ("err", "Too many attempts. Try again in a minute.", "openrouter")
        return RedirectResponse("/sources", status_code=303)
    sid = openrouter_mod.ensure_openrouter_source()
    cred_id, err = await openrouter_mod.add_user_key(sid, api_key, user["id"], label=label)
    if err or not cred_id:
        _pool_flash[user["id"]] = ("err", err or "Could not add that key.", "openrouter")
        return RedirectResponse("/sources", status_code=303)
    invalidate_model_cache("openrouter")
    _pool_flash[user["id"]] = ("ok", "OpenRouter key saved for your account.", "openrouter")
    return RedirectResponse("/sources", status_code=303)


@router.post("/sources/openrouter/{cred_id}/revoke")
@router.post("/pool/openrouter/{cred_id}/revoke")
async def openrouter_revoke(request: Request, cred_id: int):
    user, redir = require_user(request)
    if redir:
        return redir
    sid = openrouter_mod.ensure_openrouter_source()
    row = db.fetchone("SELECT id, donated_by, source_id, extra FROM credentials WHERE id=?", (cred_id,))
    if not row or row["source_id"] != sid:
        raise HTTPException(404, "Not found")
    if row["donated_by"] != user["id"] and not user["is_admin"]:
        raise HTTPException(403, "Forbidden")
    extra = accounts.cred_extra(row)
    if extra.get("role") == openrouter_mod.ROLE_GLOBAL:
        raise HTTPException(403, "Forbidden")
    openrouter_mod.disable_credential(cred_id)
    invalidate_model_cache("openrouter")
    _pool_flash[user["id"]] = ("ok", "OpenRouter key removed.", "openrouter")
    return RedirectResponse("/sources", status_code=303)


@router.post("/sources/openrouter/free-models")
@router.post("/pool/openrouter/free-models")
async def openrouter_free_models_toggle(request: Request, enabled: str = Form("0")):
    user, redir = require_user(request)
    if redir:
        return redir
    if not openrouter_mod.user_has_openrouter(user["id"]):
        _pool_flash[user["id"]] = ("err", "Add your OpenRouter key before changing free models.", "openrouter")
        return RedirectResponse("/sources", status_code=303)
    on = enabled in ("1", "on", "true")
    if not openrouter_mod.set_free_models(user["id"], on):
        _pool_flash[user["id"]] = ("err", "Add your OpenRouter key before changing free models.", "openrouter")
        return RedirectResponse("/sources", status_code=303)
    invalidate_model_cache("openrouter")
    _pool_flash[user["id"]] = (
        "ok",
        "Free OpenRouter models are on."
        if on
        else "Free OpenRouter models are off.",
        "openrouter",
    )
    return RedirectResponse("/sources", status_code=303)


@router.post("/sources/openrouter/paid-models/all")
@router.post("/pool/openrouter/paid-models/all")
async def openrouter_paid_models_all(request: Request, enabled: str = Form("0")):
    user, redir = require_user(request)
    if redir:
        return redir
    if not openrouter_mod.user_has_openrouter(user["id"]):
        _pool_flash[user["id"]] = ("err", "Add your OpenRouter key before changing model access.", "openrouter")
        return RedirectResponse("/sources", status_code=303)
    on = enabled in ("1", "on", "true")
    openrouter_mod.set_paid_models_all(user["id"], on)
    invalidate_model_cache("openrouter")
    _pool_flash[user["id"]] = (
        "ok",
        "Every OpenRouter model is listed for your account."
        if on
        else "Paid OpenRouter models are hidden. Enable individual models below.",
        "openrouter",
    )
    return RedirectResponse("/sources", status_code=303)


@router.post("/sources/openrouter/paid-models/select")
@router.post("/pool/openrouter/paid-models/select")
async def openrouter_paid_models_select(request: Request):
    user, redir = require_user(request)
    if redir:
        return redir
    if not openrouter_mod.user_has_openrouter(user["id"]):
        _pool_flash[user["id"]] = ("err", "Add your OpenRouter key before changing model access.", "openrouter")
        return RedirectResponse("/sources", status_code=303)
    form = await request.form()
    ids = [str(value) for value in form.getlist("model")]
    openrouter_mod.set_paid_models_enabled(user["id"], ids)
    invalidate_model_cache("openrouter")
    _pool_flash[user["id"]] = (
        "ok",
        f"Saved. {len(ids)} paid OpenRouter model{'s' if len(ids) != 1 else ''} enabled for your account.",
        "openrouter",
    )
    return RedirectResponse("/sources", status_code=303)


def _price_m(value) -> float | None:
    try:
        rate = float(value)
    except (TypeError, ValueError):
        return None
    return round(rate * 1_000_000, 4) if rate >= 0 else None


def _openrouter_model_rows(user_id: int) -> list[dict]:
    """Every catalog model with this account's switch state, list price and
    pinned-provider count. Reads only the shared in-memory catalog."""
    catalog = openrouter_mod.cached_catalog() or {}
    by_id = catalog.get("by_id") or {}
    pref = openrouter_mod.paid_models_pref(user_id)
    free_on = openrouter_mod.free_models_enabled(user_id)
    allowed = set(pref["enabled"])
    pinned = openrouter_mod.provider_prefs(user_id)["models"]
    rows = []
    for mid, rec in by_id.items():
        free = bool(rec.get("free"))
        pricing = rec.get("pricing") or {}
        rows.append(
            {
                "id": mid,
                "name": str(rec.get("name") or mid),
                "author": str(rec.get("author") or ""),
                "free": free,
                "enabled": free_on if free else (pref["all"] or mid in allowed),
                "input": None if free else _price_m(pricing.get("prompt")),
                "output": None if free else _price_m(pricing.get("completion")),
                "context": rec.get("context_length"),
                "pinned": len(pinned.get(mid) or []),
            }
        )
    rows.sort(key=lambda row: (not row["enabled"], row["name"].lower()))
    return rows


async def _openrouter_json(request: Request):
    user, redir = require_user(request)
    if redir:
        return None, None, JSONResponse({"error": "auth"}, status_code=401)
    if not openrouter_mod.user_has_openrouter(user["id"]):
        return None, None, JSONResponse({"error": "Add your OpenRouter key first."}, status_code=400)
    if request.method == "GET":
        return user, {}, None
    try:
        body = await request.json()
    except Exception:
        body = None
    if not isinstance(body, dict):
        return None, None, JSONResponse({"error": "Invalid JSON"}, status_code=400)
    return user, body, None


@router.post("/sources/openrouter/settings")
async def openrouter_settings(request: Request):
    """Account switches: free models, every paid model, privacy routing."""
    user, body, err = await _openrouter_json(request)
    if err:
        return err
    uid = user["id"]
    if isinstance(body.get("free_models"), bool):
        openrouter_mod.set_free_models(uid, body["free_models"])
    if isinstance(body.get("paid_all"), bool):
        if body["paid_all"]:
            openrouter_mod.set_paid_models_all(uid, True)
        elif openrouter_mod.paid_models_pref(uid)["all"]:
            openrouter_mod.set_paid_models_enabled(uid, [])
    if isinstance(body.get("data_collection_deny"), bool) or isinstance(body.get("zdr"), bool):
        prefs = openrouter_mod.provider_prefs(uid)
        deny = body.get("data_collection_deny")
        zdr = body.get("zdr")
        openrouter_mod.set_privacy(
            uid,
            data_collection_deny=deny if isinstance(deny, bool) else prefs["data_collection"] == "deny",
            zdr=zdr if isinstance(zdr, bool) else prefs["zdr"],
        )
    invalidate_model_cache("openrouter")
    prefs = openrouter_mod.provider_prefs(uid)
    return JSONResponse(
        {
            "free_models": openrouter_mod.free_models_enabled(uid),
            "paid_all": openrouter_mod.paid_models_pref(uid)["all"],
            "data_collection_deny": prefs["data_collection"] == "deny",
            "zdr": prefs["zdr"],
        }
    )


@router.post("/sources/openrouter/model")
async def openrouter_model_toggle(request: Request):
    user, body, err = await _openrouter_json(request)
    if err:
        return err
    model = str(body.get("model") or "").strip()
    rec = ((openrouter_mod.cached_catalog() or {}).get("by_id") or {}).get(model)
    if not rec or rec.get("free"):
        return JSONResponse({"error": "Unknown paid model."}, status_code=400)
    if openrouter_mod.paid_models_pref(user["id"])["all"]:
        return JSONResponse({"error": "Every paid model is already on."}, status_code=409)
    if not openrouter_mod.set_paid_model(user["id"], model, bool(body.get("enabled"))):
        return JSONResponse({"error": "You can enable up to 500 paid models."}, status_code=400)
    invalidate_model_cache("openrouter")
    return JSONResponse({"enabled": openrouter_mod.paid_model_allowed(user["id"], model)})


@router.get("/sources/openrouter/providers")
async def openrouter_model_providers(request: Request, model: str = ""):
    user, _body, err = await _openrouter_json(request)
    if err:
        return err
    model = model.strip()
    if model not in ((openrouter_mod.cached_catalog() or {}).get("by_id") or {}):
        return JSONResponse({"error": "Unknown model."}, status_code=404)
    if not limiter.allow(f"openrouter-endpoints:{user['id']}", 60, 60):
        return JSONResponse({"error": "Too many requests. Try again in a minute."}, status_code=429)
    rows = await openrouter_mod.fetch_model_endpoints(model)
    if rows is None:
        return JSONResponse({"error": "OpenRouter is not reachable right now."}, status_code=503)
    selected = openrouter_mod.provider_prefs(user["id"])["models"].get(model) or []
    return JSONResponse({"providers": rows, "selected": selected})


@router.post("/sources/openrouter/providers")
async def openrouter_model_providers_save(request: Request):
    user, body, err = await _openrouter_json(request)
    if err:
        return err
    model = str(body.get("model") or "").strip()
    slugs = body.get("providers")
    if model not in ((openrouter_mod.cached_catalog() or {}).get("by_id") or {}) or not isinstance(slugs, list):
        return JSONResponse({"error": "Invalid request."}, status_code=400)
    if not openrouter_mod.set_model_providers(user["id"], model, [s for s in slugs if isinstance(s, str)]):
        return JSONResponse(
            {"error": f"You can pin providers on up to {openrouter_mod.MAX_PREF_MODELS} models."}, status_code=400
        )
    return JSONResponse({"selected": openrouter_mod.provider_prefs(user["id"])["models"].get(model) or []})
