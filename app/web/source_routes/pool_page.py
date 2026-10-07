"""The user-facing /sources page: every provider's pooled-account board
plus the per-user shared-pool preference toggle."""

from __future__ import annotations

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from ... import accounts, db, stats
from ...security import safe_verify_url
from ...sources import grok as grok_mod
from ...sources import chatgpt as chatgpt_mod
from ...sources import devin as devin_mod
from ...sources import openrouter as openrouter_mod
from ...sources import explabs as explabs_mod
from ...sources import kimi as kimi_mod
from ...sources import mimo as mimo_mod
from ...sources import claude as claude_mod
from ...sources import zai as zai_mod
from ...sources.grok import ensure_grok_source, grok_access_state, is_held
from ...sources.pool_pick import CHATGPT_FREE_PLANS, chatgpt_plan_type, chatgpt_usage_percent, grok_usage_percent
from ...sources import pool_policy
from ..deps import _pool_flash, render, require_user
from .context import _chatgpt_pending_for, _claude_pending_for, _devin_pending_for, _kimi_pending_for, _mimo_pending_for, _pending_for, _pool_card
from .openrouter import _openrouter_model_rows

router = APIRouter()

def _donated_source(user_id: int, source_id: int) -> bool:
    row = db.fetchone(
        "SELECT 1 AS ok FROM credentials WHERE source_id=? AND donated_by=? AND disabled=0 LIMIT 1",
        (source_id, user_id),
    )
    return bool(row)


def _pool_account_visible(user, mine: bool, contributor: bool, ui: dict) -> bool:
    if mine or user["is_admin"]:
        return True
    if not ui["pool_shared"]:
        return False
    return bool(contributor or ui["open_access"])


def _pool_meter_rows(rows, user, ui: dict):
    if ui["uses_shared"] or user["is_admin"]:
        return rows
    uid = user["id"]
    return [row for row in rows if row["donated_by"] == uid]


def _fuse_fill(cards: list[dict]) -> None:
    peak = max((c["fuse"]["total_tokens"] for c in cards if c.get("fuse")), default=0)
    for card in cards:
        fuse = card.get("fuse")
        if not fuse:
            continue
        fuse["fill"] = min(100, round((fuse["total_tokens"] * 100) / peak)) if peak else 0


def _sort_mine_first(cards: list[dict]) -> list[dict]:
    cards.sort(key=lambda card: (0 if card.get("mine") else 1, int(card.get("id") or 0)))
    return cards


@router.get("/pool", response_class=HTMLResponse)
async def pool_redirect(request: Request):
    user, redir = require_user(request)
    if redir:
        return redir
    return RedirectResponse("/sources", status_code=303)


@router.get("/sources", response_class=HTMLResponse)
async def user_sources(request: Request):
    user, redir = require_user(request)
    if redir:
        return redir
    sid = ensure_grok_source()
    rows = db.fetchall(
        "SELECT * FROM credentials WHERE source_id=? AND disabled=0 ORDER BY id",
        (sid,),
    )
    access = grok_access_state(user["id"])
    grok_ui = pool_policy.ui_state(user["id"], "grok")
    grok_contributor = bool(user["is_admin"]) or _donated_source(user["id"], sid)
    visible = []
    for i, a in enumerate(rows, start=1):
        mine = a["donated_by"] == user["id"]
        if not _pool_account_visible(user, mine, grok_contributor, grok_ui):
            continue
        extra = accounts.cred_extra(a)
        billing = extra.get("billing") if isinstance(extra.get("billing"), dict) else None
        guard = extra.get("guard") if isinstance(extra.get("guard"), dict) else None
        held = is_held(a)
        authed = grok_mod.credential_is_authed(a, extra)
        subscribed = grok_mod.billing_has_subscription(billing if isinstance(billing, dict) else extra.get("billing"))
        card = _pool_card(user, a, kind="Grok", index=i, extra=extra)
        card.update(
            {
                "billing": stats.billing_view(billing, percent=grok_usage_percent(a, extra)),
                "held": held,
                "hold": stats.hold_view(a["pool_held_until"] if held else None, guard, a["last_usage_percent"]),
                "authed": authed,
                "subscribed": subscribed,
            }
        )
        visible.append(card)
    _sort_mine_first(visible)
    grok_pool = stats.grok_pool_meter(_pool_meter_rows(rows, user, grok_ui))
    _fuse_fill(visible)
    pending = _pending_for(user["id"])
    interval = 5
    verify = ""
    user_code = ""
    if pending:
        try:
            interval = max(3, min(int(pending.get("interval") or 5), 30))
        except (TypeError, ValueError):
            interval = 5
        verify = safe_verify_url(pending.get("verification_uri_complete") or "") or safe_verify_url(
            pending.get("verification_uri") or ""
        )
        user_code = str(pending.get("user_code") or "")[:32]

    chatgpt_sid = chatgpt_mod.ensure_chatgpt_source()
    chatgpt_rows = db.fetchall(
        "SELECT * FROM credentials WHERE source_id=? AND disabled=0 ORDER BY id",
        (chatgpt_sid,),
    )
    chatgpt_access = chatgpt_mod.chatgpt_access_state(user["id"])
    chatgpt_ui = pool_policy.ui_state(user["id"], "chatgpt")
    chatgpt_contributor = bool(user["is_admin"]) or _donated_source(user["id"], chatgpt_sid)
    chatgpt_visible = []
    for i, a in enumerate(chatgpt_rows, start=1):
        mine = a["donated_by"] == user["id"]
        if not _pool_account_visible(user, mine, chatgpt_contributor, chatgpt_ui):
            continue
        extra = accounts.cred_extra(a)
        billing = extra.get("billing") if isinstance(extra.get("billing"), dict) else None
        plan = chatgpt_plan_type(extra)
        card = _pool_card(user, a, kind="ChatGPT", index=i, extra=extra)
        card.update(
            {
                "authed": chatgpt_mod.credential_is_authed(a, extra),
                "billing": stats.billing_view(billing, percent=chatgpt_usage_percent(extra)),
                "free": plan in CHATGPT_FREE_PLANS,
            }
        )
        chatgpt_visible.append(card)
    _sort_mine_first(chatgpt_visible)
    chatgpt_paid_accounts = [a for a in chatgpt_visible if not a.get("free")]
    chatgpt_free_accounts = [a for a in chatgpt_visible if a.get("free")]
    chatgpt_own_failed = sum(1 for a in chatgpt_visible if a["mine"] and not a["authed"])
    chatgpt_paid_pool, chatgpt_free_pool = stats.chatgpt_pool_meters(
        _pool_meter_rows(chatgpt_rows, user, chatgpt_ui)
    )
    _fuse_fill(chatgpt_visible)
    chatgpt_pending = _chatgpt_pending_for(user["id"])
    chatgpt_interval = 5
    chatgpt_code = ""
    if chatgpt_pending:
        try:
            chatgpt_interval = max(3, min(int(chatgpt_pending.get("interval") or 5), 30))
        except (TypeError, ValueError):
            chatgpt_interval = 5
        chatgpt_code = str(chatgpt_pending.get("user_code") or "")[:32]

    zai_sid = zai_mod.ensure_zai_source()
    zai_rows = db.fetchall(
        "SELECT * FROM credentials WHERE source_id=? AND disabled=0 ORDER BY id",
        (zai_sid,),
    )
    zai_access = zai_mod.zai_access_state(user["id"])
    zai_ui = pool_policy.ui_state(user["id"], "zai")
    zai_contributor = bool(user["is_admin"]) or _donated_source(user["id"], zai_sid)
    zai_visible = []
    for i, a in enumerate(zai_rows, start=1):
        mine = a["donated_by"] == user["id"]
        if not _pool_account_visible(user, mine, zai_contributor, zai_ui):
            continue
        extra = accounts.cred_extra(a)
        billing = extra.get("billing") if isinstance(extra.get("billing"), dict) else None
        guard = extra.get("guard") if isinstance(extra.get("guard"), dict) else None
        held = zai_mod.is_held(a)
        authed = zai_mod.credential_is_authed(a, extra)
        subscribed = zai_mod.billing_has_subscription(billing if isinstance(billing, dict) else extra.get("billing"))
        card = _pool_card(user, a, kind="Z.AI", index=i, extra=extra)
        card.update(
            {
                "billing": stats.billing_view(billing, last_percent=a["last_usage_percent"]),
                "held": held,
                "hold": stats.hold_view(a["pool_held_until"] if held else None, guard, a["last_usage_percent"]),
                "authed": authed,
                "subscribed": subscribed,
                "region": extra.get("region") or "global",
            }
        )
        zai_visible.append(card)
    _sort_mine_first(zai_visible)
    zai_pool = stats.zai_pool_meter(_pool_meter_rows(zai_rows, user, zai_ui))
    _fuse_fill(zai_visible)

    devin_sid = devin_mod.ensure_devin_source()
    devin_rows = db.fetchall(
        "SELECT * FROM credentials WHERE source_id=? AND disabled=0 ORDER BY id",
        (devin_sid,),
    )
    devin_access = devin_mod.devin_access_state(user["id"])
    devin_ui = pool_policy.ui_state(user["id"], "devin")
    devin_contributor = bool(user["is_admin"]) or _donated_source(user["id"], devin_sid)
    devin_visible = []
    for i, a in enumerate(devin_rows, start=1):
        mine = a["donated_by"] == user["id"]
        if not _pool_account_visible(user, mine, devin_contributor, devin_ui):
            continue
        extra = accounts.cred_extra(a)
        billing = extra.get("billing") if isinstance(extra.get("billing"), dict) else None
        card = _pool_card(user, a, kind="Devin", index=i, extra=extra)
        card.update(
            {
                "authed": devin_mod.credential_is_authed(a, extra),
                "subscribed": devin_mod.billing_has_subscription(
                    billing if isinstance(billing, dict) else extra.get("billing")
                ),
                "billing": stats.billing_view(billing),
            }
        )
        devin_visible.append(card)
    _sort_mine_first(devin_visible)
    devin_pool = stats.devin_pool_meter(_pool_meter_rows(devin_rows, user, devin_ui))
    _fuse_fill(devin_visible)

    kimi_sid = kimi_mod.ensure_kimi_source()
    kimi_rows = db.fetchall(
        "SELECT * FROM credentials WHERE source_id=? AND disabled=0 ORDER BY id",
        (kimi_sid,),
    )
    kimi_access = kimi_mod.kimi_access_state(user["id"])
    kimi_ui = pool_policy.ui_state(user["id"], "kimi")
    kimi_contributor = bool(user["is_admin"]) or _donated_source(user["id"], kimi_sid)
    kimi_visible = []
    for i, a in enumerate(kimi_rows, start=1):
        mine = a["donated_by"] == user["id"]
        if not _pool_account_visible(user, mine, kimi_contributor, kimi_ui):
            continue
        extra = accounts.cred_extra(a)
        billing = extra.get("billing") if isinstance(extra.get("billing"), dict) else None
        guard = extra.get("guard") if isinstance(extra.get("guard"), dict) else None
        held = kimi_mod.is_held(a)
        authed = kimi_mod.credential_is_authed(a, extra)
        subscribed = kimi_mod.billing_has_subscription(billing if isinstance(billing, dict) else extra.get("billing"))
        card = _pool_card(user, a, kind="Kimi", index=i, extra=extra)
        card.update(
            {
                "billing": stats.billing_view(billing, last_percent=a["last_usage_percent"]),
                "held": held,
                "hold": stats.hold_view(a["pool_held_until"] if held else None, guard, a["last_usage_percent"]),
                "authed": authed,
                "subscribed": subscribed,
                "region": extra.get("region") or "global",
            }
        )
        kimi_visible.append(card)
    _sort_mine_first(kimi_visible)
    kimi_pool = stats.kimi_pool_meter(_pool_meter_rows(kimi_rows, user, kimi_ui))
    _fuse_fill(kimi_visible)
    kimi_pending = _kimi_pending_for(user["id"])
    kimi_interval = 5
    kimi_verify = ""
    kimi_code = ""
    if kimi_pending:
        try:
            kimi_interval = max(3, min(int(kimi_pending.get("interval") or 5), 30))
        except (TypeError, ValueError):
            kimi_interval = 5
        kimi_verify = kimi_mod.device_verify_url(kimi_pending)
        kimi_code = str(kimi_pending.get("user_code") or "")[:32]

    mimo_sid = mimo_mod.ensure_mimo_source()
    mimo_rows = db.fetchall(
        "SELECT * FROM credentials WHERE source_id=? AND disabled=0 ORDER BY id",
        (mimo_sid,),
    )
    mimo_access = mimo_mod.mimo_access_state(user["id"])
    mimo_ui = pool_policy.ui_state(user["id"], "mimo")
    mimo_contributor = bool(user["is_admin"]) or _donated_source(user["id"], mimo_sid)
    mimo_visible = []
    for i, a in enumerate(mimo_rows, start=1):
        mine = a["donated_by"] == user["id"]
        if not _pool_account_visible(user, mine, mimo_contributor, mimo_ui):
            continue
        extra = accounts.cred_extra(a)
        billing = extra.get("billing") if isinstance(extra.get("billing"), dict) else None
        guard = extra.get("guard") if isinstance(extra.get("guard"), dict) else None
        held = mimo_mod.is_held(a)
        authed = mimo_mod.credential_is_authed(a, extra)
        subscribed = mimo_mod.billing_has_subscription(billing if isinstance(billing, dict) else extra.get("billing"))
        card = _pool_card(user, a, kind="Xiaomi", index=i, extra=extra)
        card.update(
            {
                "billing": stats.billing_view(billing, last_percent=a["last_usage_percent"]),
                "held": held,
                "hold": stats.hold_view(a["pool_held_until"] if held else None, guard, a["last_usage_percent"]),
                "authed": authed,
                "subscribed": subscribed,
                "region": extra.get("region") or "cn",
            }
        )
        mimo_visible.append(card)
    _sort_mine_first(mimo_visible)
    mimo_pool = stats.mimo_pool_meter(_pool_meter_rows(mimo_rows, user, mimo_ui))
    _fuse_fill(mimo_visible)
    mimo_pending = _mimo_pending_for(user["id"])

    claude_sid = claude_mod.ensure_claude_source()
    claude_rows = db.fetchall(
        "SELECT * FROM credentials WHERE source_id=? AND disabled=0 ORDER BY id",
        (claude_sid,),
    )
    claude_access = claude_mod.claude_access_state(user["id"])
    claude_ui = pool_policy.ui_state(user["id"], "claude")
    claude_contributor = bool(user["is_admin"]) or _donated_source(user["id"], claude_sid)
    claude_visible = []
    for i, a in enumerate(claude_rows, start=1):
        mine = a["donated_by"] == user["id"]
        if not _pool_account_visible(user, mine, claude_contributor, claude_ui):
            continue
        extra = accounts.cred_extra(a)
        billing = extra.get("billing") if isinstance(extra.get("billing"), dict) else None
        guard = extra.get("guard") if isinstance(extra.get("guard"), dict) else None
        held = claude_mod.is_held(a)
        authed = claude_mod.credential_is_authed(a, extra)
        subscribed = claude_mod.billing_has_subscription(billing if isinstance(billing, dict) else extra.get("billing"))
        card = _pool_card(user, a, kind="Claude", index=i, extra=extra)
        card.update(
            {
                "billing": stats.billing_view(billing, last_percent=a["last_usage_percent"]),
                "held": held,
                "hold": stats.hold_view(a["pool_held_until"] if held else None, guard, a["last_usage_percent"]),
                "authed": authed,
                "subscribed": subscribed,
            }
        )
        claude_visible.append(card)
    _sort_mine_first(claude_visible)
    claude_pool = stats.claude_pool_meter(_pool_meter_rows(claude_rows, user, claude_ui))
    _fuse_fill(claude_visible)
    claude_pending = _claude_pending_for(user["id"])

    openrouter_sid = openrouter_mod.ensure_openrouter_source()
    or_access = openrouter_mod.openrouter_access_state(user["id"])
    or_owned = openrouter_mod.user_credential(user["id"], openrouter_sid)
    or_card = None
    if or_owned:
        cred, extra = or_owned
        info = extra.get("key_info") if isinstance(extra.get("key_info"), dict) else {}
        usage_usd = float(info.get("usage") or 0)
        remaining = info.get("limit_remaining")
        limit = info.get("limit")
        billing_raw = {
            "fetched_at": info.get("fetched_at") or "",
            "period_type": "Credits",
            "used_cents": int(round(usage_usd * 100)),
            "last_error": info.get("last_error") or "",
        }
        if remaining is not None:
            billing_raw["prepaid_cents"] = int(round(float(remaining) * 100))
        if remaining is not None and limit:
            try:
                lim = float(limit)
                if lim > 0:
                    billing_raw["credit_usage_percent"] = max(0.0, min(100.0, (1.0 - float(remaining) / lim) * 100.0))
            except (TypeError, ValueError):
                pass
        or_card = _pool_card(user, cred, kind="OpenRouter", index=1, extra=extra)
        or_card.update(
            {
                "authed": not bool(info.get("last_error")),
                "key_info": info,
                "free_tier": bool(info.get("is_free_tier")),
                "free_models": openrouter_mod.free_models_enabled(user["id"]),
                "paid_all": openrouter_mod.paid_models_pref(user["id"])["all"],
                "remaining": remaining,
                "usage": usage_usd,
                "billing": stats.billing_view(billing_raw),
            }
        )
        _fuse_fill([or_card])
    openrouter_models = _openrouter_model_rows(user["id"]) if or_card else []
    openrouter_privacy = openrouter_mod.provider_prefs(user["id"]) if or_card else {}
    explabs_sid = explabs_mod.ensure_explabs_source()
    explabs_access = explabs_mod.access_state(user["id"])
    explabs_owned = explabs_mod.user_credential(user["id"], explabs_sid)
    explabs_card = None
    if explabs_owned:
        explabs_card = {
            "id": explabs_owned["id"],
            "label": explabs_owned["label"] or "Experiential Labs key",
        }
    flash = _pool_flash.pop(user["id"], None)
    if flash and len(flash) < 3:
        flash = (flash[0], flash[1], "zai")
    return render(
        request,
        "sources.html",
        user,
        accounts=visible,
        pending=pending,
        pending_interval=interval,
        pending_verify=verify,
        pending_code=user_code,
        is_admin=user["is_admin"],
        grok_ok=access["ok"],
        grok_reason=access["reason"],
        grok_pool=grok_pool,
        grok_ui=grok_ui,
        chatgpt_ui=chatgpt_ui,
        zai_ui=zai_ui,
        devin_ui=devin_ui,
        kimi_ui=kimi_ui,
        mimo_ui=mimo_ui,
        claude_ui=claude_ui,
        chatgpt_accounts=chatgpt_visible,
        chatgpt_paid_accounts=chatgpt_paid_accounts,
        chatgpt_free_accounts=chatgpt_free_accounts,
        chatgpt_paid_pool=chatgpt_paid_pool,
        chatgpt_free_pool=chatgpt_free_pool,
        chatgpt_pending=chatgpt_pending,
        chatgpt_interval=chatgpt_interval,
        chatgpt_code=chatgpt_code,
        chatgpt_device_url=chatgpt_mod._auth_base() + "/codex/device",
        chatgpt_ok=chatgpt_access["ok"],
        chatgpt_reason=chatgpt_access["reason"],
        chatgpt_own_failed=chatgpt_own_failed,
        zai_accounts=zai_visible,
        zai_ok=zai_access["ok"],
        zai_reason=zai_access["reason"],
        zai_pool=zai_pool,
        devin_accounts=devin_visible,
        devin_ok=devin_access["ok"],
        devin_reason=devin_access["reason"],
        devin_pool=devin_pool,
        kimi_accounts=kimi_visible,
        kimi_ok=kimi_access["ok"],
        kimi_reason=kimi_access["reason"],
        kimi_pool=kimi_pool,
        kimi_pending=kimi_pending,
        kimi_interval=kimi_interval,
        kimi_verify=kimi_verify,
        kimi_code=kimi_code,
        kimi_flash_kind=flash[0] if flash and flash[2] == "kimi" else "",
        kimi_flash_msg=flash[1] if flash and flash[2] == "kimi" else "",
        mimo_accounts=mimo_visible,
        mimo_ok=mimo_access["ok"],
        mimo_reason=mimo_access["reason"],
        mimo_pool=mimo_pool,
        mimo_pending=mimo_pending,
        mimo_auth_url=safe_verify_url((mimo_pending or {}).get("manual_url") or ""),
        mimo_flash_kind=flash[0] if flash and flash[2] == "mimo" else "",
        mimo_flash_msg=flash[1] if flash and flash[2] == "mimo" else "",
        claude_accounts=claude_visible,
        claude_ok=claude_access["ok"],
        claude_reason=claude_access["reason"],
        claude_pool=claude_pool,
        claude_pending=claude_pending,
        claude_auth_url=safe_verify_url((claude_pending or {}).get("auth_url") or ""),
        claude_flash_kind=flash[0] if flash and flash[2] == "claude" else "",
        claude_flash_msg=flash[1] if flash and flash[2] == "claude" else "",
        zai_flash=flash,
        pool_flash_kind=flash[0] if flash and flash[2] == "pool" else "",
        pool_flash_msg=flash[1] if flash and flash[2] == "pool" else "",
        zai_flash_kind=flash[0] if flash and flash[2] == "zai" else "",
        zai_flash_msg=flash[1] if flash and flash[2] == "zai" else "",
        devin_flash_kind=flash[0] if flash and flash[2] == "devin" else "",
        devin_flash_msg=flash[1] if flash and flash[2] == "devin" else "",
        devin_pending=_devin_pending_for(user["id"]),
        devin_auth_url=safe_verify_url((_devin_pending_for(user["id"]) or {}).get("auth_url") or ""),
        explabs_access=explabs_access,
        explabs_account=explabs_card,
        explabs_flash_kind=flash[0] if flash and flash[2] == "explabs" else "",
        explabs_flash_msg=flash[1] if flash and flash[2] == "explabs" else "",
        openrouter_ok=or_access["ok"],
        openrouter_reason=or_access["reason"],
        openrouter_user_key=or_access["user_key"],
        openrouter_models=openrouter_models,
        openrouter_privacy=openrouter_privacy,
        openrouter_account=or_card,
        openrouter_flash_kind=flash[0] if flash and flash[2] == "openrouter" else "",
        openrouter_flash_msg=flash[1] if flash and flash[2] == "openrouter" else "",
    )


@router.post("/sources/pool/{kind}/prefs")
async def user_pool_prefs(
    request: Request,
    kind: str,
    own_only: str = Form("0"),
):
    user, redir = require_user(request)
    if redir:
        return redir
    if kind not in pool_policy.POOL_KIND_SET:
        raise HTTPException(404, "Unknown pool")
    if not pool_policy.admin_policy(kind)["pool_shared"]:
        _pool_flash[user["id"]] = (
            "ok",
            f"{pool_policy.POOL_LABELS[kind]} is limited to linked accounts site-wide.",
            "pool",
        )
        return RedirectResponse(f"/sources#pool-{kind}", status_code=303)
    pool_policy.set_user_own_only(user["id"], kind, own_only in ("1", "on", "true"))
    label = pool_policy.POOL_LABELS[kind]
    if own_only in ("1", "on", "true"):
        msg = f"{label} will use only the accounts you have linked. Other users cannot use them either."
    else:
        msg = f"{label} will use the shared pool, including accounts you linked."
    _pool_flash[user["id"]] = ("ok", msg, "pool")
    return RedirectResponse(f"/sources#pool-{kind}", status_code=303)
