"""The user-facing /sources page: every provider's pooled-account board
plus the per-user shared-pool preference toggle."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
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
from ...sources import cursor as cursor_mod
from ...sources import nanogpt as nanogpt_mod
from ...sources import zai as zai_mod
from ...sources.grok import ensure_grok_source, grok_access_state, is_held
from ...sources.pool_pick import CHATGPT_FREE_PLANS, chatgpt_plan_type, chatgpt_usage_percent, grok_usage_percent
from ...sources import pool_policy
from ..deps import _pool_flash, render, require_user
from .context import _chatgpt_pending_for, _claude_pending_for, _cursor_pending_for, _devin_pending_for, _kimi_pending_for, _mimo_pending_for, _nanogpt_pending_for, _pending_for, _pool_card
from .nanogpt import _redirect_uri as _nanogpt_redirect_uri, model_rows as _nanogpt_model_rows
from .openrouter import _openrouter_model_rows

router = APIRouter()

_SHARE_LABELS = {"everyone": "Everyone", "contributors": "Contributors", "private": "Private"}


def _pool_scope(user, kind: str, rows, ok: bool) -> tuple[dict, set[int], list]:
    """This user's sharing state for one pool and the accounts they are
    routed onto. The gauge counts exactly those, so it never includes an
    account the backend would not use for them; without access it previews
    what linking one would reach. The table lists them only once the user
    has access (plus their own; admins also see the rest)."""
    uid = user["id"]
    ui = pool_policy.ui_state(uid, kind)
    reachable = pool_policy.filter_credentials(rows, kind, uid, chosen=False)
    choice = pool_policy.account_choice(uid, kind)
    routable = [row for row in reachable if int(row["id"]) not in choice["skip"]]
    ids = {int(row["id"]) for row in reachable} if ok else set()
    used = {int(row["id"]) for row in routable}
    ui["reachable"] = ids
    ui["skip"] = choice["skip"]
    ui["primary"] = choice["primary"] if ok and choice["primary"] in used else None
    ui["n_mine"] = sum(1 for row in rows if row["donated_by"] == uid)
    ui["n_shared"] = sum(1 for row in routable if row["donated_by"] != uid)
    ui["contributor"] = pool_policy.contributes(uid, kind) if ui["pool_shared"] else False
    ui["share_label"] = _SHARE_LABELS[ui["share"]]
    return ui, ids, routable


def _pool_account_visible(user, row, routable_ids: set[int]) -> bool:
    if row["donated_by"] == user["id"] or user["is_admin"]:
        return True
    return int(row["id"]) in routable_ids


def _pool_card_scope(card: dict, kind: str, row, user, routable_ids: set[int]) -> None:
    """Admin view: say why an account in the table is not in their routing."""
    card["routable"] = card["mine"] or int(row["id"]) in routable_ids
    card["share"] = "everyone" if card["mine"] else pool_policy.share_level(kind, row["donated_by"])


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
    grok_ui, grok_ids, grok_routable = _pool_scope(user, "grok", rows, access["ok"])
    visible = []
    for a in rows:
        if not _pool_account_visible(user, a, grok_ids):
            continue
        extra = accounts.cred_extra(a)
        billing = extra.get("billing") if isinstance(extra.get("billing"), dict) else None
        guard = extra.get("guard") if isinstance(extra.get("guard"), dict) else None
        held = is_held(a)
        authed = grok_mod.credential_is_authed(a, extra)
        subscribed = grok_mod.billing_has_subscription(billing if isinstance(billing, dict) else extra.get("billing"))
        card = _pool_card(user, a, kind="Grok", index=len(visible) + 1, extra=extra)
        _pool_card_scope(card, "grok", a, user, grok_ids)
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
    grok_pool = stats.grok_pool_meter(grok_routable, owner_id=user["id"])
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
    chatgpt_ui, chatgpt_ids, chatgpt_routable = _pool_scope(user, "chatgpt", chatgpt_rows, chatgpt_access["ok"])
    chatgpt_visible = []
    for a in chatgpt_rows:
        if not _pool_account_visible(user, a, chatgpt_ids):
            continue
        extra = accounts.cred_extra(a)
        billing = extra.get("billing") if isinstance(extra.get("billing"), dict) else None
        plan = chatgpt_plan_type(extra)
        card = _pool_card(user, a, kind="ChatGPT", index=len(chatgpt_visible) + 1, extra=extra)
        _pool_card_scope(card, "chatgpt", a, user, chatgpt_ids)
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
        chatgpt_routable
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
    zai_ui, zai_ids, zai_routable = _pool_scope(user, "zai", zai_rows, zai_access["ok"])
    zai_visible = []
    for a in zai_rows:
        if not _pool_account_visible(user, a, zai_ids):
            continue
        extra = accounts.cred_extra(a)
        billing = extra.get("billing") if isinstance(extra.get("billing"), dict) else None
        guard = extra.get("guard") if isinstance(extra.get("guard"), dict) else None
        held = zai_mod.is_held(a)
        authed = zai_mod.credential_is_authed(a, extra)
        subscribed = zai_mod.billing_has_subscription(billing if isinstance(billing, dict) else extra.get("billing"))
        card = _pool_card(user, a, kind="Z.AI", index=len(zai_visible) + 1, extra=extra)
        _pool_card_scope(card, "zai", a, user, zai_ids)
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
    zai_pool = stats.zai_pool_meter(zai_routable, owner_id=user["id"])
    _fuse_fill(zai_visible)

    devin_sid = devin_mod.ensure_devin_source()
    devin_rows = db.fetchall(
        "SELECT * FROM credentials WHERE source_id=? AND disabled=0 ORDER BY id",
        (devin_sid,),
    )
    devin_access = devin_mod.devin_access_state(user["id"])
    devin_ui, devin_ids, devin_routable = _pool_scope(user, "devin", devin_rows, devin_access["ok"])
    devin_visible = []
    for a in devin_rows:
        if not _pool_account_visible(user, a, devin_ids):
            continue
        extra = accounts.cred_extra(a)
        billing = extra.get("billing") if isinstance(extra.get("billing"), dict) else None
        card = _pool_card(user, a, kind="Devin", index=len(devin_visible) + 1, extra=extra)
        _pool_card_scope(card, "devin", a, user, devin_ids)
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
    devin_pool = stats.devin_pool_meter(devin_routable)
    _fuse_fill(devin_visible)

    kimi_sid = kimi_mod.ensure_kimi_source()
    kimi_rows = db.fetchall(
        "SELECT * FROM credentials WHERE source_id=? AND disabled=0 ORDER BY id",
        (kimi_sid,),
    )
    kimi_access = kimi_mod.kimi_access_state(user["id"])
    kimi_ui, kimi_ids, kimi_routable = _pool_scope(user, "kimi", kimi_rows, kimi_access["ok"])
    kimi_visible = []
    for a in kimi_rows:
        if not _pool_account_visible(user, a, kimi_ids):
            continue
        extra = accounts.cred_extra(a)
        billing = extra.get("billing") if isinstance(extra.get("billing"), dict) else None
        guard = extra.get("guard") if isinstance(extra.get("guard"), dict) else None
        held = kimi_mod.is_held(a)
        authed = kimi_mod.credential_is_authed(a, extra)
        subscribed = kimi_mod.billing_has_subscription(billing if isinstance(billing, dict) else extra.get("billing"))
        card = _pool_card(user, a, kind="Kimi", index=len(kimi_visible) + 1, extra=extra)
        _pool_card_scope(card, "kimi", a, user, kimi_ids)
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
    kimi_pool = stats.kimi_pool_meter(kimi_routable, owner_id=user["id"])
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
    mimo_ui, mimo_ids, mimo_routable = _pool_scope(user, "mimo", mimo_rows, mimo_access["ok"])
    mimo_visible = []
    for a in mimo_rows:
        if not _pool_account_visible(user, a, mimo_ids):
            continue
        extra = accounts.cred_extra(a)
        billing = extra.get("billing") if isinstance(extra.get("billing"), dict) else None
        guard = extra.get("guard") if isinstance(extra.get("guard"), dict) else None
        held = mimo_mod.is_held(a)
        authed = mimo_mod.credential_is_authed(a, extra)
        subscribed = mimo_mod.billing_has_subscription(billing if isinstance(billing, dict) else extra.get("billing"))
        card = _pool_card(user, a, kind="Xiaomi", index=len(mimo_visible) + 1, extra=extra)
        _pool_card_scope(card, "mimo", a, user, mimo_ids)
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
    mimo_pool = stats.mimo_pool_meter(mimo_routable, owner_id=user["id"])
    _fuse_fill(mimo_visible)
    mimo_pending = _mimo_pending_for(user["id"])

    claude_sid = claude_mod.ensure_claude_source()
    claude_rows = db.fetchall(
        "SELECT * FROM credentials WHERE source_id=? AND disabled=0 ORDER BY id",
        (claude_sid,),
    )
    claude_access = claude_mod.claude_access_state(user["id"])
    claude_ui, claude_ids, claude_routable = _pool_scope(user, "claude", claude_rows, claude_access["ok"])
    claude_visible = []
    for a in claude_rows:
        if not _pool_account_visible(user, a, claude_ids):
            continue
        extra = accounts.cred_extra(a)
        billing = extra.get("billing") if isinstance(extra.get("billing"), dict) else None
        guard = extra.get("guard") if isinstance(extra.get("guard"), dict) else None
        held = claude_mod.is_held(a)
        authed = claude_mod.credential_is_authed(a, extra)
        subscribed = claude_mod.billing_has_subscription(billing if isinstance(billing, dict) else extra.get("billing"))
        card = _pool_card(user, a, kind="Claude", index=len(claude_visible) + 1, extra=extra)
        _pool_card_scope(card, "claude", a, user, claude_ids)
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
    claude_pool = stats.claude_pool_meter(claude_routable, owner_id=user["id"])
    _fuse_fill(claude_visible)
    claude_pending = _claude_pending_for(user["id"])

    cursor_sid = cursor_mod.ensure_cursor_source()
    cursor_rows = db.fetchall(
        "SELECT * FROM credentials WHERE source_id=? AND disabled=0 ORDER BY id",
        (cursor_sid,),
    )
    cursor_access = cursor_mod.cursor_access_state(user["id"])
    cursor_ui, cursor_ids, cursor_routable = _pool_scope(user, "cursor", cursor_rows, cursor_access["ok"])
    cursor_visible = []
    for a in cursor_rows:
        if not _pool_account_visible(user, a, cursor_ids):
            continue
        extra = accounts.cred_extra(a)
        billing = extra.get("billing") if isinstance(extra.get("billing"), dict) else None
        guard = extra.get("guard") if isinstance(extra.get("guard"), dict) else None
        held = cursor_mod.is_held(a)
        card = _pool_card(user, a, kind="Cursor", index=len(cursor_visible) + 1, extra=extra)
        _pool_card_scope(card, "cursor", a, user, cursor_ids)
        card.update(
            {
                "billing": stats.billing_view(billing, last_percent=a["last_usage_percent"]),
                "held": held,
                "hold": stats.hold_view(a["pool_held_until"] if held else None, guard, a["last_usage_percent"]),
                "authed": cursor_mod.credential_is_authed(a, extra),
                "subscribed": cursor_mod.billing_has_subscription(billing),
            }
        )
        cursor_visible.append(card)
    _sort_mine_first(cursor_visible)
    cursor_pool = stats.cursor_pool_meter(cursor_routable, owner_id=user["id"])
    _fuse_fill(cursor_visible)
    cursor_pending = _cursor_pending_for(user["id"])

    nanogpt_sid = nanogpt_mod.ensure_nanogpt_source()
    nanogpt_rows = db.fetchall(
        "SELECT * FROM credentials WHERE source_id=? AND disabled=0 ORDER BY id",
        (nanogpt_sid,),
    )
    nanogpt_access = nanogpt_mod.nanogpt_access_state(user["id"])
    nanogpt_ui, nanogpt_ids, nanogpt_routable = _pool_scope(user, "nanogpt", nanogpt_rows, nanogpt_access["ok"])
    nanogpt_visible = []
    for a in nanogpt_rows:
        if not _pool_account_visible(user, a, nanogpt_ids):
            continue
        extra = accounts.cred_extra(a)
        billing = extra.get("billing") if isinstance(extra.get("billing"), dict) else {}
        guard = extra.get("guard") if isinstance(extra.get("guard"), dict) else None
        held = nanogpt_mod.is_held(a)
        card = _pool_card(user, a, kind="NanoGPT", index=len(nanogpt_visible) + 1, extra=extra)
        _pool_card_scope(card, "nanogpt", a, user, nanogpt_ids)
        # The balance is the owner's business; everyone else sees quota only.
        shown = billing if card["show_billing"] else {k: v for k, v in billing.items() if k != "prepaid_cents"}
        card.update(
            {
                "billing": stats.billing_view(shown, last_percent=a["last_usage_percent"]),
                "held": held,
                "hold": stats.hold_view(a["pool_held_until"] if held else None, guard, a["last_usage_percent"]),
                "authed": nanogpt_mod.credential_is_authed(a, extra),
                "subscribed": nanogpt_mod.billing_has_subscription(billing),
                "owner_only": not nanogpt_mod.shareable(extra),
            }
        )
        nanogpt_visible.append(card)
    _sort_mine_first(nanogpt_visible)
    nanogpt_pool = stats.nanogpt_pool_meter(nanogpt_routable, owner_id=user["id"])
    _fuse_fill(nanogpt_visible)
    nanogpt_pending = _nanogpt_pending_for(user["id"])
    nanogpt_owner = nanogpt_mod.nanogpt_own_access(user["id"])["ok"]

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
        cursor_ui=cursor_ui,
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
        grok_flash_msg=flash[1] if flash and flash[2] == "grok" else "",
        chatgpt_flash_msg=flash[1] if flash and flash[2] == "chatgpt" else "",
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
        cursor_accounts=cursor_visible,
        cursor_ok=cursor_access["ok"],
        cursor_reason=cursor_access["reason"],
        cursor_pool=cursor_pool,
        cursor_pending=cursor_pending,
        cursor_interval=(cursor_pending or {}).get("interval", 3),
        cursor_auth_url=safe_verify_url((cursor_pending or {}).get("auth_url") or ""),
        cursor_flash_kind=flash[0] if flash and flash[2] == "cursor" else "",
        cursor_flash_msg=flash[1] if flash and flash[2] == "cursor" else "",
        nanogpt_ui=nanogpt_ui,
        nanogpt_accounts=nanogpt_visible,
        nanogpt_ok=nanogpt_access["ok"],
        nanogpt_reason=nanogpt_access["reason"],
        nanogpt_pool=nanogpt_pool,
        nanogpt_pending=nanogpt_pending if (nanogpt_pending or {}).get("kind") == "device" else None,
        nanogpt_interval=(nanogpt_pending or {}).get("interval", 3),
        nanogpt_verify=safe_verify_url((nanogpt_pending or {}).get("verify") or ""),
        nanogpt_oauth=bool(_nanogpt_redirect_uri(request)),
        nanogpt_owner=nanogpt_owner,
        nanogpt_models=_nanogpt_model_rows(user["id"]) if nanogpt_owner else [],
        nanogpt_prefs=nanogpt_mod.model_prefs(user["id"]),
        nanogpt_flash_kind=flash[0] if flash and flash[2] == "nanogpt" else "",
        nanogpt_flash_msg=flash[1] if flash and flash[2] == "nanogpt" else "",
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


_SHARE_COPY = {
    "everyone": "anyone with access to the {label} pool may use your accounts",
    "contributors": "only people who also share {label} accounts may use yours",
    "private": "nobody else may use your accounts",
}


@router.post("/sources/pool/{kind}/prefs")
async def user_pool_prefs(request: Request, kind: str):
    user, redir = require_user(request)
    if redir:
        return redir
    if kind not in pool_policy.POOL_KIND_SET:
        raise HTTPException(404, "Unknown pool")
    label = pool_policy.POOL_LABELS[kind]
    if not pool_policy.admin_policy(kind)["pool_shared"]:
        _pool_flash[user["id"]] = (
            "ok",
            f"{label} is limited to linked accounts site-wide.",
            "pool",
        )
        return RedirectResponse(f"/sources#pool-{kind}", status_code=303)
    form = await request.form()
    share = str(form.get("share") or "")
    use = str(form.get("use") or "")
    if not share and "own_only" in form:
        # A page rendered before share/use existed posts the old switch.
        private = str(form.get("own_only")) in ("1", "on", "true")
        share, use = ("private", "mine") if private else ("everyone", "pool")
    if share not in pool_policy.SHARE_LEVELS or (use and use not in pool_policy.USE_MODES):
        raise HTTPException(400, "Unknown sharing choice")
    pref = pool_policy.set_user_pref(user["id"], kind, share=share, use=use or None)
    eff = pool_policy.effective_pref(user["id"], kind)
    shared = _SHARE_COPY[pref["share"]].format(label=label)
    choice = pool_policy.account_choice(user["id"], kind)
    if "accounts_listed" in form:
        choice, refused = _save_account_choice(user["id"], kind, form)
        if refused:
            _pool_flash[user["id"]] = ("fault", f"{label}: sharing saved, but {refused}", "pool")
            return RedirectResponse(f"/sources#pool-{kind}", status_code=303)
    if eff["use"] == "mine":
        routing = "Your requests use only your accounts"
    elif choice["primary"] is not None:
        routing = "New conversations start on the account you picked, then fall back to your other accounts"
    else:
        routing = "Your requests use your accounts first, then accounts shared with you"
    left_out = len(choice["skip"])
    skipped = f" {left_out} account{'s' if left_out != 1 else ''} left out of your rotation." if left_out else ""
    _pool_flash[user["id"]] = ("ok", f"{label}: {routing}; {shared}.{skipped}", "pool")
    return RedirectResponse(f"/sources#pool-{kind}", status_code=303)


def _form_ids(values) -> set[int]:
    return {int(v) for v in values if str(v).strip().isdigit()}


def _save_account_choice(uid: int, kind: str, form) -> tuple[dict, str]:
    """Apply the dialog's account list. Only the accounts the dialog listed
    change, so an account that appears after the page rendered stays in the
    rotation. Returns (choice, refusal message)."""
    listed = _form_ids(str(form.get("accounts_listed") or "").split(",")[: pool_policy.MAX_SKIP])
    checked = _form_ids(form.getlist("account")) & listed
    old = pool_policy.account_choice(uid, kind)
    skip = (set(old["skip"]) - listed) | (listed - checked)
    raw_primary = str(form.get("primary") or "")
    primary = int(raw_primary) if raw_primary.isdigit() and int(raw_primary) in checked else None
    rows = db.fetchall(
        "SELECT * FROM credentials WHERE source_id=? AND disabled=0", (pool_policy.source_id(kind),)
    )
    reachable = {int(row["id"]) for row in pool_policy.filter_credentials(rows, kind, uid, chosen=False)}
    skip &= {int(row["id"]) for row in rows} | set(old["skip"])
    if reachable and not reachable - skip:
        return old, "keep at least one account in your rotation."
    if len(skip) > pool_policy.MAX_SKIP:
        return old, "too many accounts left out."
    return pool_policy.set_account_choice(uid, kind, skip=skip, primary=primary), ""
