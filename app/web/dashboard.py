from __future__ import annotations

from decimal import Decimal, InvalidOperation
from pathlib import Path

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse

from .. import accounts, db, pricing as pricing_mod, stats, tool_failures
from .. import opencode as opencode_mod
from ..config import settings
from ..sources import TYPES, models_by_source
from ..sources import chatgpt as chatgpt_mod
from ..sources import devin as devin_mod
from ..sources import openrouter as openrouter_mod
from ..sources import kimi as kimi_mod
from ..sources import mimo as mimo_mod
from ..sources import claude as claude_mod
from ..sources import zai as zai_mod
from ..sources.grok import grok_access_state
from .deps import (
    _chat_auth,
    _clear_vault_cookie,
    _json_object,
    _notices,
    _peek_key_reveal,
    _public_base,
    _set_vault_cookie,
    _stash_key_reveal,
    current_user,
    render,
    require_admin,
    require_user,
)

router = APIRouter()

def _dashboard_response(
    request: Request,
    user,
    *,
    new_key: str | None = None,
    key_error: str | None = None,
    key_name: str = "",
    status_code: int = 200,
):
    return render(
        request,
        "dashboard.html",
        user,
        status_code=status_code,
        usage=stats.current_usage(user["id"]),
        pricing=stats.pricing_views() if user["is_admin"] else [],
        provider_types=TYPES,
        pricing_synced=request.query_params.get("pricing_synced", ""),
        pricing_sync_error=request.query_params.get("pricing_error") == "1",
        new_key=new_key,
        key_error=key_error,
        key_name=key_name,
        base_url=_public_base(request),
    )



@router.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return RedirectResponse("/chat", status_code=303)


@router.get("/usage", response_class=HTMLResponse)
async def dashboard(request: Request):
    user, redir = require_user(request)
    if redir:
        return redir
    new_key = _peek_key_reveal(request, user["id"])
    response = _dashboard_response(request, user, new_key=new_key)
    if new_key:
        request.session.pop("key_reveal", None)
    return response


@router.get("/usage.json")
async def usage_json(request: Request):
    user, redir = require_user(request)
    if redir:
        return JSONResponse({"error": "auth"}, status_code=401)
    range_id = request.query_params.get("range", "24h")
    provider = request.query_params.get("provider", "").strip()[:32]
    all_users = bool(user["is_admin"] and request.query_params.get("scope") == "all")
    return JSONResponse(
        stats.usage_analytics(
            user["id"],
            range_id=range_id,
            provider=provider,
            all_users=all_users,
        ),
        headers={"Cache-Control": "no-store"},
    )


@router.get("/admin/tool-failures")
async def admin_tool_failures(request: Request):
    """Analysis pack of every logged failed tool call, shaped for LLM triage."""
    user, redir = require_admin(request)
    if redir:
        return JSONResponse({"error": "auth"}, status_code=401)
    try:
        limit = int(request.query_params.get("limit", "200"))
    except (TypeError, ValueError):
        limit = 200
    try:
        hours = int(request.query_params.get("hours", "168"))
    except (TypeError, ValueError):
        hours = 168
    kind = request.query_params.get("kind", "").strip()[:64]
    tool = request.query_params.get("tool", "").strip()[:64]
    model = request.query_params.get("model", "").strip()[:200]
    return JSONResponse(
        tool_failures.analysis_pack(limit=limit, hours=hours, kind=kind, tool=tool, model=model),
        headers={"Cache-Control": "no-store"},
    )


def _price_rate(value: str, *, optional: bool = False) -> int | None:
    if optional and not value.strip():
        return None
    try:
        rate = Decimal(value or "0")
    except InvalidOperation:
        raise ValueError("rate") from None
    if not rate.is_finite() or rate < 0 or rate > 1_000_000:
        raise ValueError("rate")
    return int((rate * 1_000_000).quantize(Decimal("1")))


@router.post("/admin/pricing")
async def save_pricing(
    request: Request,
    provider: str = Form(),
    model: str = Form(),
    input_rate: str = Form("0"),
    cached_rate: str = Form(""),
    cache_write_rate: str = Form(""),
    output_rate: str = Form("0"),
    reasoning_rate: str = Form(""),
):
    user, redir = require_admin(request)
    if redir:
        return redir
    provider = provider.strip()
    model = model.strip()
    if provider not in TYPES or not model or len(model) > 200:
        raise HTTPException(400, "Invalid provider or model")
    try:
        input_v = _price_rate(input_rate)
        output_v = _price_rate(output_rate)
        cached_v = _price_rate(cached_rate, optional=True)
        write_v = _price_rate(cache_write_rate, optional=True)
        reasoning_v = _price_rate(reasoning_rate, optional=True)
    except ValueError:
        raise HTTPException(400, "Rates must be non-negative USD amounts per million tokens") from None
    if input_v is None or output_v is None:
        raise HTTPException(400, "Input and output rates are required")
    db.save_model_price(provider, model, input_v, cached_v, output_v, reasoning_v, cache_write_rate=write_v)
    return RedirectResponse("/usage#pricing", status_code=303)


@router.post("/admin/pricing/openrouter")
async def sync_openrouter_pricing(request: Request):
    user, redir = require_admin(request)
    if redir:
        return redir
    try:
        result = await pricing_mod.sync_openrouter_prices()
    except Exception:
        return RedirectResponse("/usage?pricing_error=1#pricing", status_code=303)
    return RedirectResponse(f"/usage?pricing_synced={result['count']}#pricing", status_code=303)


@router.post("/admin/pricing/delete")
async def remove_pricing(request: Request, provider: str = Form(), model: str = Form()):
    user, redir = require_admin(request)
    if redir:
        return redir
    if provider in TYPES and model:
        db.delete_model_price(provider, model[:200])
    return RedirectResponse("/usage#pricing", status_code=303)


@router.post("/keys")
async def create_key(request: Request, name: str = Form()):
    user, redir = require_user(request)
    if redir:
        return redir
    try:
        raw = accounts.create_api_key(user["id"], name)
    except ValueError:
        return _dashboard_response(
            request,
            user,
            key_error="Name must be 1–80 characters and cannot contain control characters.",
            key_name=name,
            status_code=400,
        )
    _stash_key_reveal(request, user["id"], raw)
    return RedirectResponse("/usage", status_code=303)


@router.post("/keys/{key_id}/delete")
async def delete_key(request: Request, key_id: int):
    user, redir = require_user(request)
    if redir:
        return redir
    accounts.delete_api_key(user["id"], key_id)
    return RedirectResponse("/usage", status_code=303)


def _latest_release(suffix: str) -> str | None:
    release_dir = Path(__file__).resolve().parents[2] / "data" / "desktop-releases"
    matches = sorted(p.name for p in release_dir.glob(f"*{suffix}") if not p.name.endswith(".blockmap"))
    return f"/desktop/{matches[-1]}" if matches else None


@router.get("/download", response_class=HTMLResponse)
async def desktop_app(request: Request):
    user, redir = require_user(request)
    if redir:
        return redir
    return render(
        request,
        "desktop.html",
        user,
        win_url=_latest_release(".exe"),
        mac_url=_latest_release(".dmg"),
        appimage_url=_latest_release(".AppImage"),
        deb_url=_latest_release(".deb"),
    )


@router.get("/docs", response_class=HTMLResponse)
async def api_docs(request: Request):
    user, redir = require_user(request)
    if redir:
        return redir
    access = grok_access_state(user["id"])
    chatgpt_access = chatgpt_mod.chatgpt_access_state(user["id"])
    zai_access = zai_mod.zai_access_state(user["id"])
    devin_access = devin_mod.devin_access_state(user["id"])
    kimi_access = kimi_mod.kimi_access_state(user["id"])
    mimo_access = mimo_mod.mimo_access_state(user["id"])
    claude_access = claude_mod.claude_access_state(user["id"])
    or_access = openrouter_mod.openrouter_access_state(user["id"])
    groups = await models_by_source(
        allow_grok=access["ok"],
        allow_chatgpt=chatgpt_access["ok"],
        allow_zai=zai_access["ok"],
        allow_devin=devin_access["ok"],
        allow_kimi=kimi_access["ok"],
        allow_mimo=mimo_access["ok"],
        allow_claude=claude_access["ok"],
        user_id=user["id"],
    )
    groups = [group for group in groups if group.get("models")]
    model_options = await opencode_mod.model_options(user["id"], groups)
    models = [item["id"] for item in model_options]
    site_url = (settings.public_base_url or "").rstrip("/") or str(request.base_url).rstrip("/")
    return render(
        request,
        "docs.html",
        user,
        base_url=_public_base(request),
        site_url=site_url,
        groups=groups,
        models=models,
        model_options=model_options,
        grok_ok=access["ok"],
        grok_reason=access["reason"],
        zai_ok=zai_access["ok"],
        zai_reason=zai_access["reason"],
        openrouter_ok=or_access["ok"],
        openrouter_reason=or_access["reason"],
        openrouter_user_key=or_access["user_key"],
        openrouter_free_models=or_access["free_models"],
        grok_listed=access["ok"] or any(g["type"] == "grok" for g in groups),
        zai_listed=zai_access["ok"] or any(g["type"] == "zai" for g in groups),
        devin_ok=devin_access["ok"],
        devin_listed=devin_access["ok"] or any(g["type"] == "devin" for g in groups),
        kimi_ok=kimi_access["ok"],
        kimi_reason=kimi_access["reason"],
        kimi_listed=kimi_access["ok"] or any(g["type"] == "kimi" for g in groups),
        mimo_ok=mimo_access["ok"],
        mimo_reason=mimo_access["reason"],
        mimo_listed=mimo_access["ok"] or any(g["type"] == "mimo" for g in groups),
        claude_ok=claude_access["ok"],
        claude_reason=claude_access["reason"],
        claude_listed=claude_access["ok"] or any(g["type"] == "claude" for g in groups),
    )


@router.get("/install-opencode.sh")
async def install_opencode_sh(request: Request):
    site_url = (settings.public_base_url or "").rstrip("/") or str(request.base_url).rstrip("/")
    script = opencode_mod.install_script(origin=site_url, api_base=_public_base(request))
    return PlainTextResponse(
        script,
        media_type="text/x-shellscript; charset=utf-8",
        headers={
            "Content-Disposition": "inline; filename=\"install-opencode.sh\"",
            "Cache-Control": "no-store",
        },
    )


_USER_ROLE_ERRORS = {
    "self": "You cannot change your own role.",
    "last_admin": "That is the last admin.",
    "missing": "Account not found.",
}
_USER_DELETE_ERRORS = {
    "self": "You cannot delete your own account.",
    "last_admin": "That is the last admin.",
    "missing": "Account not found.",
}


def _admin_users_page(request: Request, user, *, error: str | None = None, notice: str | None = None, status_code: int = 200):
    users = []
    for row in accounts.list_users():
        item = dict(row)
        item["cost_label"] = stats.format_usd_micros(item["cost_micros"]) if item["cost_micros"] else "—"
        users.append(item)
    return render(
        request,
        "admin_users.html",
        user,
        users=users,
        invites=accounts.list_whitelist(),
        admin_count=accounts.admin_count(),
        error=error,
        notice=notice,
        status_code=status_code,
    )


@router.get("/admin/users", response_class=HTMLResponse)
async def admin_users(request: Request):
    user, redir = require_admin(request)
    if redir:
        return redir
    notice = _notices.pop(user["id"], None)
    return _admin_users_page(request, user, notice=notice)


@router.post("/admin/users")
async def admin_users_invite(request: Request, email: str = Form()):
    user, redir = require_admin(request)
    if redir:
        return redir
    try:
        listed = accounts.add_whitelist(email)
    except ValueError as exc:
        msg = {
            "email": "Enter a valid email.",
            "exists": "That email already has an account.",
            "listed": "That email is already listed.",
        }.get(str(exc), "Could not add that email.")
        return _admin_users_page(request, user, error=msg, status_code=400)
    _notices[user["id"]] = f"Listed {listed}. They can register with their own password."
    return RedirectResponse("/admin/users", status_code=303)


@router.post("/admin/users/invites/{invite_id}/delete")
async def admin_users_invite_delete(request: Request, invite_id: int):
    user, redir = require_admin(request)
    if redir:
        return redir
    if accounts.remove_whitelist(invite_id):
        _notices[user["id"]] = "Removed that email from the list."
    return RedirectResponse("/admin/users", status_code=303)


@router.post("/admin/users/{user_id}/role")
async def admin_users_role(request: Request, user_id: int, admin: str = Form("0")):
    user, redir = require_admin(request)
    if redir:
        return redir
    make_admin = admin in ("1", "on", "true")
    try:
        accounts.set_admin(user_id, make_admin, actor_id=user["id"])
    except ValueError as exc:
        return _admin_users_page(
            request,
            user,
            error=_USER_ROLE_ERRORS.get(str(exc), "Could not update that account."),
            status_code=400,
        )
    _notices[user["id"]] = "Granted admin." if make_admin else "Removed admin."
    return RedirectResponse("/admin/users", status_code=303)


@router.post("/admin/users/{user_id}/delete")
async def admin_users_delete(request: Request, user_id: int):
    user, redir = require_admin(request)
    if redir:
        return redir
    try:
        email = accounts.delete_user(user_id, actor_id=user["id"])
    except ValueError as exc:
        return _admin_users_page(
            request,
            user,
            error=_USER_DELETE_ERRORS.get(str(exc), "Could not delete that account."),
            status_code=400,
        )
    _notices[user["id"]] = f"Deleted {email}."
    return RedirectResponse("/admin/users", status_code=303)


@router.get("/preview/spinners", response_class=HTMLResponse)
async def spinner_preview(request: Request):
    user, redir = require_user(request)
    if redir:
        return redir
    return render(request, "spin_preview.html", user)
