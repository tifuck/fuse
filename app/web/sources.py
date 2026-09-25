from __future__ import annotations

import json
import logging
import time

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from .. import accounts, db, stats
from ..crypto import encrypt
from ..security import allowed_base_url, client_ip, limiter, safe_verify_url
from ..config import settings
from ..sources import TYPES, grok as grok_mod, invalidate_model_cache
from ..sources import chatgpt as chatgpt_mod
from ..sources import devin as devin_mod
from ..sources import openrouter as openrouter_mod
from ..sources import kimi as kimi_mod
from ..sources import mimo as mimo_mod
from ..sources import claude as claude_mod
from ..sources import zai as zai_mod
from ..sources import ollama_access
from ..sources.grok import (
    disable_credential,
    ensure_grok_source,
    grok_access_state,
    is_held,
    refresh_billing,
)
from ..sources.ollama import OllamaSource
from ..sources.pool_pick import CHATGPT_FREE_PLANS, chatgpt_plan_type, chatgpt_usage_percent, grok_usage_percent
from ..sources import pool_policy
from ..sources.probe import probe_source
from .deps import (
    _chatgpt_pending,
    _devin_pending,
    _kimi_pending,
    _mimo_pending,
    _claude_pending,
    _notices,
    _pending,
    _pool_flash,
    _PENDING_TTL,
    logger,
    render,
    require_admin,
    require_user,
)

router = APIRouter()
_BUILTINS = {"grok", "llama", "ollama", "zai", "chatgpt", "devin", "kimi", "mimo", "claude", "openrouter"}
_OLLAMA_PAGE = 10

def _source_cfg(row) -> dict:
    try:
        data = json.loads((row["config"] if row else None) or "{}")
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


def _add_key_lines(conn, source_id: int, text: str) -> int:
    existing = {
        accounts.cred_secret(c)
        for c in conn.execute("SELECT secret FROM credentials WHERE source_id=?", (source_id,)).fetchall()
    }
    additions = []
    for line in text.splitlines():
        key = line.strip()
        if not key or key.startswith("#") or key in existing:
            continue
        if len(key) > 4096 or any(ord(char) < 32 or 0xD800 <= ord(char) <= 0xDFFF for char in key):
            raise ValueError("Each key must be at most 4096 valid characters.")
        additions.append(key)
        existing.add(key)
    if len(additions) > 100:
        raise ValueError("Add at most 100 keys at a time.")
    n = conn.execute("SELECT COUNT(*) AS n FROM credentials WHERE source_id=?", (source_id,)).fetchone()
    start = int(n["n"] if n else 0) + 1
    for index, key in enumerate(additions):
        conn.execute(
            """INSERT INTO credentials (source_id, label, secret, extra, week_start, donated_by, created_at)
               VALUES (?,?,?,?,?,?,?)""",
            (
                source_id,
                f"key-{start + index}",
                encrypt(key),
                encrypt("{}"),
                db.iso_week_start(),
                None,
                db.now(),
            ),
        )
    return len(additions)


def _ollama_key_views(source_id: int) -> list[dict]:
    row = db.fetchone("SELECT * FROM sources WHERE id=?", (source_id,))
    if not row:
        return []
    creds = db.fetchall("SELECT * FROM credentials WHERE source_id=? ORDER BY id", (source_id,))
    live = [c for c in creds if not c["disabled"]]
    src = OllamaSource(row, _source_cfg(row), live)
    views = []
    for v in src.key_views(creds):
        reason = v.get("disabled_reason") or ""
        last_kind = v.get("last_error") or ""
        if v["state"] == "disabled" and reason in ("weekly_quota", "session_quota"):
            label = "Out of usage"
            kind = "cool"
        elif v["state"] == "disabled" and reason == "account_unavailable":
            label = "Account unavailable"
            kind = "cool"
        elif v["state"] == "disabled":
            label = "Auth failed" if reason == "auth_failure" else "Disabled"
            kind = "off"
        elif last_kind == "model_gated":
            label = "Ready"
            kind = "on"
        elif v["state"] == "cooling":
            label = "Cooldown"
            kind = "cool"
        elif v["state"] == "resting":
            label = "Resting"
            kind = "on"
        else:
            label = "Ready"
            kind = "on"
        wait = v.get("available_in") or 0
        last_bits = []
        if v.get("last_status"):
            last_bits.append(str(v["last_status"]))
        if v.get("last_error_label"):
            last_bits.append(v["last_error_label"])
        if v.get("last_model") and last_kind == "model_gated":
            last_bits.append(v["last_model"])
        views.append(
            {
                **v,
                "state_label": label,
                "state_kind": kind,
                "available_label": stats.format_duration(wait) if wait else "",
                "last_label": " · ".join(last_bits),
            }
        )
    return views


def _ollama_rank(k: dict) -> tuple[int, int]:
    kind = k.get("state_kind") or ""
    if kind == "off":
        return (0, int(k.get("id") or 0))
    if kind == "cool":
        return (1, int(k.get("id") or 0))
    return (2, int(k.get("id") or 0))


def _ollama_page(keys: list[dict], page: int, size: int = _OLLAMA_PAGE) -> dict:
    ordered = sorted(keys, key=_ollama_rank)
    total = len(ordered)
    pages = max(1, (total + size - 1) // size) if total else 1
    page = max(1, min(int(page or 1), pages))
    start = (page - 1) * size
    chunk = ordered[start : start + size]
    problem = sum(1 for k in ordered if (k.get("state_kind") or "") in ("off", "cool"))
    return {
        "items": chunk,
        "page": page,
        "pages": pages,
        "total": total,
        "from": (start + 1) if total else 0,
        "to": min(start + size, total),
        "problem": problem,
        "has_prev": page > 1,
        "has_next": page < pages,
    }


def _admin_sources_ctx(error: str | None = None, notice: str | None = None, ollama_page: int = 1) -> dict:
    from ..search.policy import domain_lists_text
    from ..sources import ensure_llama_source, ensure_ollama_source

    fetch_lists = domain_lists_text()

    llama_id = ensure_llama_source()
    ollama_id = ensure_ollama_source()
    grok_id = ensure_grok_source()
    chatgpt_id = chatgpt_mod.ensure_chatgpt_source()
    zai_id = zai_mod.ensure_zai_source()
    devin_id = devin_mod.ensure_devin_source()
    kimi_id = kimi_mod.ensure_kimi_source()
    mimo_id = mimo_mod.ensure_mimo_source()
    claude_id = claude_mod.ensure_claude_source()
    openrouter_id = openrouter_mod.ensure_openrouter_source()
    llama = db.fetchone("SELECT * FROM sources WHERE id=?", (llama_id,))
    ollama = db.fetchone("SELECT * FROM sources WHERE id=?", (ollama_id,))
    grok = db.fetchone("SELECT * FROM sources WHERE id=?", (grok_id,))
    chatgpt = db.fetchone("SELECT * FROM sources WHERE id=?", (chatgpt_id,))
    zai = db.fetchone("SELECT * FROM sources WHERE id=?", (zai_id,))
    devin = db.fetchone("SELECT * FROM sources WHERE id=?", (devin_id,))
    kimi = db.fetchone("SELECT * FROM sources WHERE id=?", (kimi_id,))
    mimo = db.fetchone("SELECT * FROM sources WHERE id=?", (mimo_id,))
    claude = db.fetchone("SELECT * FROM sources WHERE id=?", (claude_id,))
    openrouter = db.fetchone("SELECT * FROM sources WHERE id=?", (openrouter_id,))
    llama_cfg = _source_cfg(llama)
    ollama_cfg = _source_cfg(ollama)
    grok_cfg = _source_cfg(grok)
    zai_cfg = _source_cfg(zai)
    llama_key = db.fetchone("SELECT id FROM credentials WHERE source_id=? AND disabled=0", (llama_id,))
    grok_n = db.fetchone(
        "SELECT COUNT(*) AS n FROM credentials WHERE source_id=? AND disabled=0", (grok_id,)
    )
    chatgpt_n = db.fetchone(
        "SELECT COUNT(*) AS n FROM credentials WHERE source_id=? AND disabled=0", (chatgpt_id,)
    )
    zai_n = db.fetchone(
        "SELECT COUNT(*) AS n FROM credentials WHERE source_id=? AND disabled=0", (zai_id,)
    )
    devin_n = db.fetchone(
        "SELECT COUNT(*) AS n FROM credentials WHERE source_id=? AND disabled=0", (devin_id,)
    )
    kimi_n = db.fetchone(
        "SELECT COUNT(*) AS n FROM credentials WHERE source_id=? AND disabled=0", (kimi_id,)
    )
    mimo_n = db.fetchone(
        "SELECT COUNT(*) AS n FROM credentials WHERE source_id=? AND disabled=0", (mimo_id,)
    )
    claude_n = db.fetchone(
        "SELECT COUNT(*) AS n FROM credentials WHERE source_id=? AND disabled=0", (claude_id,)
    )
    openrouter_user_n = db.fetchone(
        """SELECT COUNT(*) AS n FROM credentials
           WHERE source_id=? AND disabled=0 AND donated_by IS NOT NULL""",
        (openrouter_id,),
    )
    extras = db.fetchall("SELECT * FROM sources WHERE type='openai' ORDER BY id")
    extra_counts = {
        r["source_id"]: r["n"]
        for r in db.fetchall("SELECT source_id, COUNT(*) AS n FROM credentials GROUP BY source_id")
    }
    ollama_keys = _ollama_key_views(ollama_id)
    ready = sum(1 for k in ollama_keys if k["state"] in ("active", "resting"))
    cooling = sum(1 for k in ollama_keys if k["state_kind"] == "cool")
    paged = _ollama_page(ollama_keys, ollama_page)
    return {
        "llama": llama,
        "llama_cfg": llama_cfg,
        "llama_key_set": bool(llama_key),
        "llama_insecure": (
            not bool(llama_cfg["verify"])
            if "verify" in llama_cfg
            else str(llama_cfg.get("base_url") or "").startswith("https://")
        ),
        "ollama": ollama,
        "ollama_cfg": ollama_cfg,
        "ollama_keys": paged["items"],
        "ollama_ready": ready,
        "ollama_cooling": cooling,
        "ollama_problem": paged["problem"],
        "ollama_total": paged["total"],
        "ollama_page": paged["page"],
        "ollama_pages": paged["pages"],
        "ollama_from": paged["from"],
        "ollama_to": paged["to"],
        "ollama_has_prev": paged["has_prev"],
        "ollama_has_next": paged["has_next"],
        "ollama_catalog": ollama_access.catalog_summary(ollama_id) if ollama_id else {"free": [], "gated": []},
        "grok": grok,
        "grok_usage_guard": grok_cfg.get("offpool_usage_guard", True) is not False,
        "grok_count": int(grok_n["n"] if grok_n else 0),
        "chatgpt": chatgpt,
        "chatgpt_count": int(chatgpt_n["n"] if chatgpt_n else 0),
        "zai": zai,
        "zai_usage_guard": zai_cfg.get("offpool_usage_guard", True) is not False,
        "zai_count": int(zai_n["n"] if zai_n else 0),
        "devin": devin,
        "devin_count": int(devin_n["n"] if devin_n else 0),
        "kimi": kimi,
        "kimi_count": int(kimi_n["n"] if kimi_n else 0),
        "mimo": mimo,
        "mimo_count": int(mimo_n["n"] if mimo_n else 0),
        "claude": claude,
        "claude_count": int(claude_n["n"] if claude_n else 0),
        "pool_policies": {kind: pool_policy.admin_policy(kind) for kind in pool_policy.POOL_KINDS},
        "pool_labels": pool_policy.POOL_LABELS,
        "openrouter": openrouter,
        "openrouter_user_count": int(openrouter_user_n["n"] if openrouter_user_n else 0),
        "extras": extras,
        "extra_counts": extra_counts,
        "error": error,
        "notice": notice,
        "types": TYPES,
        "fetch_blacklist": fetch_lists[0],
        "fetch_whitelist": fetch_lists[1],
    }


def _pending_for(uid: int):
    data = _pending.get(uid)
    if not data:
        return None
    if time.time() - float(data.get("_started") or 0) > _PENDING_TTL:
        _pending.pop(uid, None)
        return None
    return data


def _chatgpt_pending_for(uid: int):
    data = _chatgpt_pending.get(uid)
    if not data:
        return None
    if time.time() - float(data.get("_started") or 0) > _PENDING_TTL:
        _chatgpt_pending.pop(uid, None)
        return None
    return data


def _devin_pending_for(uid: int):
    data = _devin_pending.get(uid)
    if not data:
        return None
    if time.time() - float(data.get("_started") or 0) > _PENDING_TTL:
        _devin_pending.pop(uid, None)
        return None
    return data


def _kimi_pending_for(uid: int):
    data = _kimi_pending.get(uid)
    if not data:
        return None
    if time.time() - float(data.get("_started") or 0) > _PENDING_TTL:
        _kimi_pending.pop(uid, None)
        return None
    return data


def _claude_pending_for(uid: int):
    data = _claude_pending.get(uid)
    if not data:
        return None
    if time.time() - float(data.get("_started") or 0) > _PENDING_TTL:
        _claude_pending.pop(uid, None)
        return None
    return data


def _mimo_pending_for(uid: int):
    data = _mimo_pending.get(uid)
    if not data:
        return None
    if time.time() - float(data.get("_started") or 0) > _PENDING_TTL:
        _mimo_pending.pop(uid, None)
        return None
    return data


def _mimo_redirect_uri(request: Request) -> str:
    base = (settings.public_base_url or "").rstrip("/")
    if not base:
        base = str(request.base_url).rstrip("/")
    return base + "/sources/mimo/callback"


def _devin_redirect_uri(request: Request) -> str:
    base = (settings.public_base_url or "").rstrip("/")
    if not base:
        base = str(request.base_url).rstrip("/")
    return base + "/sources/devin/callback"


def _oauth_tokens(raw) -> dict:
    if not isinstance(raw, dict):
        raise ValueError("Token response was not an object.")
    access = raw.get("access_token")
    refresh = raw.get("refresh_token", "")
    expires = raw.get("expires_in")
    if (
        not isinstance(access, str)
        or not access
        or len(access) > 16_384
        or any(ord(char) < 32 or 0xD800 <= ord(char) <= 0xDFFF for char in access)
    ):
        raise ValueError("Token response did not contain a valid access token.")
    if not isinstance(refresh, str) or len(refresh) > 16_384 or any(
        ord(char) < 32 or 0xD800 <= ord(char) <= 0xDFFF for char in refresh
    ):
        raise ValueError("Token response contained an invalid refresh token.")
    if isinstance(expires, bool):
        raise ValueError("Token response contained an invalid expiry.")
    try:
        expires = int(expires)
    except (TypeError, ValueError):
        raise ValueError("Token response contained an invalid expiry.") from None
    if expires <= 0 or expires > 31_536_000:
        raise ValueError("Token response contained an invalid expiry.")
    return {**raw, "access_token": access, "refresh_token": refresh, "expires_in": expires}


def _terminal_oauth_error(error: str) -> bool:
    value = (error or "").lower()
    return any(
        marker in value
        for marker in (
            "access_denied",
            "denied",
            "expired_token",
            "expired",
            "authorization_declined",
            "invalid_grant",
            "invalid_request",
            "unauthorized_client",
            "unsupported_grant_type",
            "cancelled",
        )
    )


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


def _pool_card(user, row, *, kind: str, index: int, extra: dict | None = None) -> dict:
    mine = row["donated_by"] == user["id"]
    admin = bool(user["is_admin"])
    extra = extra if isinstance(extra, dict) else {}
    label = row["label"] or f"{kind} account"
    if not mine and not admin:
        label = f"{kind} plan {index}"
    email = ""
    if mine or admin:
        if kind == "ChatGPT":
            email = chatgpt_mod.account_email(extra)
        elif kind == "Grok":
            email = grok_mod.account_email(extra)
        elif kind == "Devin":
            email = devin_mod.account_email(extra)
        elif kind == "Kimi":
            email = kimi_mod.account_email(extra)
        elif kind == "Claude":
            email = claude_mod.account_email(extra)
        if not email and admin and row["donated_by"] and row["donated_by"] != user["id"]:
            donor = accounts.get_user(row["donated_by"])
            try:
                email = str((donor["email"] if donor is not None else "") or "").strip()
            except (KeyError, IndexError, TypeError):
                email = ""
    return {
        "id": row["id"],
        "label": label,
        "email": email,
        "mine": mine,
        "can_revoke": mine or admin,
        "show_billing": mine or admin,
        "fuse": stats.fuse_view(row),
    }


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
    openrouter_paid_all = bool(or_access.get("paid_models_all"))
    openrouter_paid_models: list[dict] = []
    if or_card and not openrouter_paid_all:
        pref = openrouter_mod.paid_models_pref(user["id"])
        enabled = set(pref["enabled"])
        catalog = openrouter_mod.cached_catalog() or {}
        for mid in catalog.get("paid_ids") or []:
            rec = (catalog.get("by_id") or {}).get(mid) or {}
            openrouter_paid_models.append(
                {"id": mid, "name": str(rec.get("name") or mid), "enabled": mid in enabled}
            )
        openrouter_paid_models.sort(key=lambda row: row["name"].lower())
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
        openrouter_ok=or_access["ok"],
        openrouter_reason=or_access["reason"],
        openrouter_user_key=or_access["user_key"],
        openrouter_free_models=or_access["free_models"],
        openrouter_paid_all=openrouter_paid_all,
        openrouter_paid_models=openrouter_paid_models,
        openrouter_account=or_card,
        openrouter_flash_kind=flash[0] if flash and flash[2] == "openrouter" else "",
        openrouter_flash_msg=flash[1] if flash and flash[2] == "openrouter" else "",
    )


@router.post("/sources/grok/start")
@router.post("/pool/grok/start")
async def grok_start(request: Request):
    user, redir = require_user(request)
    if redir:
        return redir
    if not limiter.allow(f"grok-start:{user['id']}", 5, 60):
        _pool_flash[user["id"]] = ("ok", "x.ai sign-in was rate limited. Wait a minute and try again.", "zai")
        return RedirectResponse("/sources", status_code=303)
    try:
        data = await grok_mod.start_device()
    except Exception:
        logger.warning("Could not start Grok device authorization", exc_info=True)
        _pool_flash[user["id"]] = ("ok", "Could not start x.ai sign-in. Check the connection and try again.", "zai")
        return RedirectResponse("/sources", status_code=303)
    if (
        not isinstance(data, dict)
        or not isinstance(data.get("device_code"), str)
        or not data["device_code"]
        or not isinstance(data.get("user_code"), str)
        or not data["user_code"]
    ):
        _pool_flash[user["id"]] = ("ok", "x.ai returned an invalid sign-in response. Try again shortly.", "zai")
        return RedirectResponse("/sources", status_code=303)
    data["_started"] = time.time()
    _pending[user["id"]] = data
    return RedirectResponse("/sources", status_code=303)


@router.post("/sources/grok/poll")
@router.post("/pool/grok/poll")
async def grok_poll(request: Request):
    user, redir = require_user(request)
    if redir:
        return JSONResponse({"error": "auth"}, status_code=401)
    pending = _pending_for(user["id"])
    if not pending:
        return JSONResponse({"error": "No Grok sign-in in progress."}, status_code=400)
    try:
        data = await grok_mod.poll_device(pending["device_code"])
    except Exception:
        logger.warning("Grok device poll failed transiently", exc_info=True)
        return JSONResponse(
            {"pending": True, "error": "x.ai could not be reached; retrying.", "retry_after": pending.get("interval", 5)},
            status_code=502,
        )
    if not isinstance(data, dict):
        return JSONResponse(
            {"pending": True, "error": "x.ai returned an invalid response; retrying.", "retry_after": pending.get("interval", 5)},
            status_code=502,
        )
    if data.get("access_token") is not None:
        try:
            tokens = _oauth_tokens(data)
        except ValueError as exc:
            return JSONResponse(
                {"pending": True, "error": str(exc) + " Retrying.", "retry_after": pending.get("interval", 5)},
                status_code=502,
            )
        sid = ensure_grok_source()
        profile = await grok_mod.fetch_userinfo(tokens["access_token"])
        cred_id = grok_mod.store_tokens(
            sid, tokens, donated_by=user["id"], label=profile.get("email") or "Grok account", profile=profile
        )
        _pending.pop(user["id"], None)
        row = db.fetchone("SELECT * FROM credentials WHERE id=?", (cred_id,))
        if row:
            try:
                await refresh_billing(row, force=True)
            except Exception:
                logger.warning("Grok billing refresh failed after OAuth", exc_info=True)
        return JSONResponse({"ok": True})
    err = data.get("error")
    if err == "slow_down":
        try:
            current_interval = int(pending.get("interval") or 5)
        except (TypeError, ValueError):
            current_interval = 5
        pending["interval"] = min(30, max(3, current_interval) + 5)
        return JSONResponse({"pending": True, "error": "x.ai asked Fuse to poll more slowly.", "retry_after": pending["interval"]})
    if err == "authorization_pending":
        return JSONResponse({"pending": True, "retry_after": pending.get("interval", 5)})
    error_text = str(err or data.get("error_description") or "")
    if _terminal_oauth_error(error_text):
        _pending.pop(user["id"], None)
        return JSONResponse({"error": "x.ai sign-in was denied or expired. Start again."}, status_code=400)
    return JSONResponse(
        {"pending": True, "error": "x.ai returned an unexpected response; retrying.", "retry_after": pending.get("interval", 5)},
        status_code=502,
    )


@router.post("/sources/grok/cancel")
@router.post("/pool/grok/cancel")
async def grok_cancel(request: Request):
    user, redir = require_user(request)
    if redir:
        return redir
    _pending.pop(user["id"], None)
    return RedirectResponse("/sources", status_code=303)


@router.post("/sources/grok/{cred_id}/revoke")
@router.post("/pool/grok/{cred_id}/revoke")
async def grok_revoke(request: Request, cred_id: int):
    user, redir = require_user(request)
    if redir:
        return redir
    sid = ensure_grok_source()
    row = db.fetchone("SELECT id, donated_by, source_id FROM credentials WHERE id=?", (cred_id,))
    if not row or row["source_id"] != sid:
        raise HTTPException(404, "Not found")
    if row["donated_by"] != user["id"] and not user["is_admin"]:
        raise HTTPException(403, "Forbidden")
    disable_credential(cred_id)
    return RedirectResponse("/sources", status_code=303)


@router.post("/sources/chatgpt/start")
@router.post("/pool/chatgpt/start")
async def chatgpt_start(request: Request):
    user, redir = require_user(request)
    if redir:
        return redir
    if not limiter.allow(f"chatgpt-start:{user['id']}", 5, 60):
        _pool_flash[user["id"]] = ("ok", "OpenAI sign-in was rate limited. Wait a minute and try again.", "zai")
        return RedirectResponse("/sources", status_code=303)
    try:
        data = await chatgpt_mod.start_device()
    except Exception:
        logger.warning("Could not start ChatGPT device authorization", exc_info=True)
        _pool_flash[user["id"]] = ("ok", "Could not start OpenAI sign-in. Check the connection and try again.", "zai")
        return RedirectResponse("/sources", status_code=303)
    if (
        not isinstance(data, dict)
        or not isinstance(data.get("user_code"), str)
        or not data["user_code"]
        or not isinstance(data.get("device_auth_id"), str)
        or not data["device_auth_id"]
    ):
        _pool_flash[user["id"]] = ("ok", "OpenAI returned an invalid sign-in response. Try again shortly.", "zai")
        return RedirectResponse("/sources", status_code=303)
    data["_started"] = time.time()
    _chatgpt_pending[user["id"]] = data
    return RedirectResponse("/sources", status_code=303)


@router.post("/sources/chatgpt/poll")
@router.post("/pool/chatgpt/poll")
async def chatgpt_poll(request: Request):
    user, redir = require_user(request)
    if redir:
        return JSONResponse({"error": "auth"}, status_code=401)
    pending = _chatgpt_pending_for(user["id"])
    if not pending:
        return JSONResponse({"error": "No ChatGPT sign-in in progress."}, status_code=400)
    try:
        result = await chatgpt_mod.poll_device(pending["device_auth_id"], pending["user_code"])
    except Exception:
        logger.warning("ChatGPT device poll failed transiently", exc_info=True)
        return JSONResponse(
            {"pending": True, "error": "OpenAI could not be reached; retrying.", "retry_after": pending.get("interval", 5)},
            status_code=502,
        )
    if not isinstance(result, dict):
        return JSONResponse(
            {"pending": True, "error": "OpenAI returned an invalid response; retrying.", "retry_after": pending.get("interval", 5)},
            status_code=502,
        )
    if result.get("status") == "ok":
        try:
            tokens = _oauth_tokens(result.get("tokens"))
        except ValueError as exc:
            return JSONResponse(
                {"pending": True, "error": str(exc) + " Retrying.", "retry_after": pending.get("interval", 5)},
                status_code=502,
            )
        sid = chatgpt_mod.ensure_chatgpt_source()
        cred_id = chatgpt_mod.store_tokens(sid, tokens, donated_by=user["id"], label="ChatGPT account")
        _chatgpt_pending.pop(user["id"], None)
        row = db.fetchone("SELECT * FROM credentials WHERE id=?", (cred_id,))
        if row:
            try:
                await chatgpt_mod.refresh_usage(row, force=True)
            except Exception:
                logger.warning("ChatGPT usage refresh failed after OAuth", exc_info=True)
        return JSONResponse({"ok": True})
    if result.get("status") == "pending":
        return JSONResponse({"pending": True})
    error_text = str(result.get("error") or "")
    if _terminal_oauth_error(error_text):
        _chatgpt_pending.pop(user["id"], None)
        return JSONResponse({"error": "OpenAI sign-in was denied or expired. Start again."}, status_code=400)
    return JSONResponse(
        {"pending": True, "error": error_text or "OpenAI returned an unexpected response; retrying.", "retry_after": pending.get("interval", 5)},
        status_code=502,
    )


@router.post("/sources/chatgpt/cancel")
@router.post("/pool/chatgpt/cancel")
async def chatgpt_cancel(request: Request):
    user, redir = require_user(request)
    if redir:
        return redir
    _chatgpt_pending.pop(user["id"], None)
    return RedirectResponse("/sources", status_code=303)


@router.post("/sources/chatgpt/{cred_id}/revoke")
@router.post("/pool/chatgpt/{cred_id}/revoke")
async def chatgpt_revoke(request: Request, cred_id: int):
    user, redir = require_user(request)
    if redir:
        return redir
    sid = chatgpt_mod.ensure_chatgpt_source()
    row = db.fetchone("SELECT id, donated_by, source_id, extra FROM credentials WHERE id=?", (cred_id,))
    if not row or row["source_id"] != sid:
        raise HTTPException(404, "Not found")
    if row["donated_by"] != user["id"] and not user["is_admin"]:
        raise HTTPException(403, "Forbidden")
    chatgpt_mod.disable_credential(cred_id)
    return RedirectResponse("/sources", status_code=303)


@router.post("/sources/zai/add")
@router.post("/pool/zai/add")
async def zai_add(request: Request, api_key: str = Form(""), label: str = Form("")):
    user, redir = require_user(request)
    if redir:
        return redir
    if not limiter.allow(f"zai-add:{user['id']}", 5, 60):
        _pool_flash[user["id"]] = ("err", "Too many attempts. Try again in a minute.", "zai")
        return RedirectResponse("/sources", status_code=303)
    sid = zai_mod.ensure_zai_source()
    cred_id, err = await zai_mod.add_plan_key(sid, api_key, user["id"], label=label)
    if err or not cred_id:
        _pool_flash[user["id"]] = ("err", err or "Could not add that key.", "zai")
        return RedirectResponse("/sources", status_code=303)
    _pool_flash[user["id"]] = ("ok", "Z.AI Coding Plan key added to the pool.", "zai")
    return RedirectResponse("/sources", status_code=303)


@router.post("/sources/devin/start")
@router.post("/pool/devin/start")
async def devin_start(request: Request):
    user, redir = require_user(request)
    if redir:
        return redir
    if not limiter.allow(f"devin-start:{user['id']}", 5, 60):
        _pool_flash[user["id"]] = ("err", "Too many attempts. Try again in a minute.", "devin")
        return RedirectResponse("/sources", status_code=303)
    try:
        data = devin_mod.start_cli_login()
    except Exception:
        logger.warning("Could not start Devin PKCE login", exc_info=True)
        _pool_flash[user["id"]] = ("err", "Could not start Devin sign-in. Try again shortly.", "devin")
        return RedirectResponse("/sources", status_code=303)
    if not isinstance(data.get("auth_url"), str) or not data.get("verifier") or not data.get("state"):
        _pool_flash[user["id"]] = ("err", "Devin sign-in could not be prepared. Try again shortly.", "devin")
        return RedirectResponse("/sources", status_code=303)
    data["_started"] = time.time()
    data["user_id"] = user["id"]
    _devin_pending[user["id"]] = data
    return RedirectResponse("/sources", status_code=303)


@router.get("/sources/devin/callback")
@router.get("/pool/devin/callback")
async def devin_callback(request: Request):
    user, redir = require_user(request)
    if redir:
        return redir
    pending = _devin_pending_for(user["id"])
    error = str(request.query_params.get("error") or "").strip()
    if error:
        _devin_pending.pop(user["id"], None)
        denied = error.lower() in ("access_denied", "denied", "cancelled", "canceled")
        msg = "Devin sign-in was cancelled." if denied else "Devin sign-in was denied or expired. Start again."
        _pool_flash[user["id"]] = ("err", msg, "devin")
        return RedirectResponse("/sources", status_code=303)
    code = str(request.query_params.get("code") or "").strip()
    state = str(request.query_params.get("state") or "").strip()
    if not pending or not state or state != str(pending.get("state") or ""):
        _pool_flash[user["id"]] = ("err", "Devin sign-in expired or did not match this session. Start again.", "devin")
        return RedirectResponse("/sources", status_code=303)
    if not code:
        _devin_pending.pop(user["id"], None)
        _pool_flash[user["id"]] = ("err", "Devin did not return an authorization code. Start again.", "devin")
        return RedirectResponse("/sources", status_code=303)
    cred_id, err = await devin_mod.complete_cli_login(
        code,
        str(pending.get("verifier") or ""),
        user["id"],
        redirect_uri=str(pending.get("redirect_uri") or _devin_redirect_uri(request)),
    )
    _devin_pending.pop(user["id"], None)
    if err or not cred_id:
        _pool_flash[user["id"]] = ("err", err or "Could not link this Devin account.", "devin")
        return RedirectResponse("/sources", status_code=303)
    invalidate_model_cache("devin")
    _pool_flash[user["id"]] = ("ok", "Devin account added to the pool.", "devin")
    return RedirectResponse("/sources", status_code=303)


@router.post("/sources/devin/complete")
@router.post("/pool/devin/complete")
async def devin_complete(request: Request, code: str = Form("")):
    user, redir = require_user(request)
    if redir:
        return redir
    pending = _devin_pending_for(user["id"])
    if not pending:
        _pool_flash[user["id"]] = ("err", "Devin sign-in expired. Start again.", "devin")
        return RedirectResponse("/sources", status_code=303)
    cred_id, err = await devin_mod.complete_cli_login(
        code,
        str(pending.get("verifier") or ""),
        user["id"],
    )
    if err or not cred_id:
        _pool_flash[user["id"]] = ("err", err or "Could not link this Devin account.", "devin")
        return RedirectResponse("/sources", status_code=303)
    _devin_pending.pop(user["id"], None)
    invalidate_model_cache("devin")
    _pool_flash[user["id"]] = ("ok", "Devin account added to the pool.", "devin")
    return RedirectResponse("/sources", status_code=303)


@router.post("/sources/devin/cancel")
@router.post("/pool/devin/cancel")
async def devin_cancel(request: Request):
    user, redir = require_user(request)
    if redir:
        return redir
    _devin_pending.pop(user["id"], None)
    return RedirectResponse("/sources", status_code=303)


@router.post("/sources/devin/add")
@router.post("/pool/devin/add")
async def devin_add(request: Request, api_key: str = Form(""), label: str = Form("")):
    user, redir = require_user(request)
    if redir:
        return redir
    if not limiter.allow(f"devin-add:{user['id']}", 5, 60):
        _pool_flash[user["id"]] = ("err", "Too many attempts. Try again in a minute.", "devin")
        return RedirectResponse("/sources", status_code=303)
    sid = devin_mod.ensure_devin_source()
    cred_id, err = await devin_mod.add_session_token(sid, api_key, user["id"], label=label)
    if err or not cred_id:
        _pool_flash[user["id"]] = ("err", err or "Could not add that token.", "devin")
        return RedirectResponse("/sources", status_code=303)
    invalidate_model_cache("devin")
    _pool_flash[user["id"]] = ("ok", "Devin account added to the pool.", "devin")
    return RedirectResponse("/sources", status_code=303)


@router.post("/sources/devin/{cred_id}/revoke")
@router.post("/pool/devin/{cred_id}/revoke")
async def devin_revoke(request: Request, cred_id: int):
    user, redir = require_user(request)
    if redir:
        return redir
    sid = devin_mod.ensure_devin_source()
    row = db.fetchone("SELECT id, donated_by, source_id FROM credentials WHERE id=?", (cred_id,))
    if not row or row["source_id"] != sid:
        raise HTTPException(404, "Not found")
    if row["donated_by"] != user["id"] and not user["is_admin"]:
        raise HTTPException(403, "Forbidden")
    devin_mod.disable_credential(cred_id)
    invalidate_model_cache("devin")
    _pool_flash[user["id"]] = ("ok", "Devin account removed.", "devin")
    return RedirectResponse("/sources", status_code=303)


@router.post("/sources/kimi/start")
@router.post("/pool/kimi/start")
async def kimi_start(request: Request, region: str = Form("global")):
    user, redir = require_user(request)
    if redir:
        return redir
    if not limiter.allow(f"kimi-start:{user['id']}", 5, 60):
        _pool_flash[user["id"]] = ("ok", "Kimi sign-in was rate limited. Wait a minute and try again.", "kimi")
        return RedirectResponse("/sources", status_code=303)
    chosen = "cn" if str(region or "").strip().lower() in ("cn", "mainland-cn", "com") else "global"
    try:
        data = await kimi_mod.start_device(chosen)
    except Exception:
        logger.warning("Could not start Kimi device authorization", exc_info=True)
        _pool_flash[user["id"]] = ("ok", "Could not start Kimi sign-in. Check the connection and try again.", "kimi")
        return RedirectResponse("/sources", status_code=303)
    if (
        not isinstance(data, dict)
        or not isinstance(data.get("device_code"), str)
        or not data["device_code"]
        or not isinstance(data.get("user_code"), str)
        or not data["user_code"]
    ):
        _pool_flash[user["id"]] = ("ok", "Kimi returned an invalid sign-in response. Try again shortly.", "kimi")
        return RedirectResponse("/sources", status_code=303)
    data["_started"] = time.time()
    _kimi_pending[user["id"]] = data
    return RedirectResponse("/sources", status_code=303)


@router.post("/sources/kimi/poll")
@router.post("/pool/kimi/poll")
async def kimi_poll(request: Request):
    user, redir = require_user(request)
    if redir:
        return JSONResponse({"error": "auth"}, status_code=401)
    pending = _kimi_pending_for(user["id"])
    if not pending:
        return JSONResponse({"error": "No Kimi sign-in in progress."}, status_code=400)
    region = str(pending.get("_region") or "global")
    device_id = str(pending.get("_device_id") or "")
    try:
        data = await kimi_mod.poll_device(pending["device_code"], region=region, device_id=device_id)
    except Exception:
        logger.warning("Kimi device poll failed transiently", exc_info=True)
        return JSONResponse(
            {"pending": True, "error": "Kimi could not be reached; retrying.", "retry_after": pending.get("interval", 5)},
            status_code=502,
        )
    if not isinstance(data, dict):
        return JSONResponse(
            {"pending": True, "error": "Kimi returned an invalid response; retrying.", "retry_after": pending.get("interval", 5)},
            status_code=502,
        )
    if data.get("access_token") is not None:
        try:
            tokens = _oauth_tokens(data)
        except ValueError as exc:
            return JSONResponse(
                {"pending": True, "error": str(exc) + " Retrying.", "retry_after": pending.get("interval", 5)},
                status_code=502,
            )
        sid = kimi_mod.ensure_kimi_source()
        cred_id = kimi_mod.store_tokens(
            sid,
            tokens,
            donated_by=user["id"],
            label="Kimi account",
            region=region,
            device_id=device_id,
        )
        _kimi_pending.pop(user["id"], None)
        row = db.fetchone("SELECT * FROM credentials WHERE id=?", (cred_id,))
        if row:
            try:
                await kimi_mod.refresh_quota(row, force=True)
            except Exception:
                logger.warning("Kimi quota refresh failed after OAuth", exc_info=True)
        invalidate_model_cache("kimi")
        return JSONResponse({"ok": True})
    err = data.get("error")
    if err == "slow_down":
        try:
            current_interval = int(pending.get("interval") or 5)
        except (TypeError, ValueError):
            current_interval = 5
        pending["interval"] = min(30, max(3, current_interval) + 5)
        return JSONResponse({"pending": True, "error": "Kimi asked Fuse to poll more slowly.", "retry_after": pending["interval"]})
    if err == "authorization_pending":
        return JSONResponse({"pending": True, "retry_after": pending.get("interval", 5)})
    error_text = str(err or data.get("error_description") or "")
    if _terminal_oauth_error(error_text):
        _kimi_pending.pop(user["id"], None)
        return JSONResponse({"error": "Kimi sign-in was denied or expired. Start again."}, status_code=400)
    return JSONResponse(
        {"pending": True, "error": "Kimi returned an unexpected response; retrying.", "retry_after": pending.get("interval", 5)},
        status_code=502,
    )


@router.post("/sources/kimi/cancel")
@router.post("/pool/kimi/cancel")
async def kimi_cancel(request: Request):
    user, redir = require_user(request)
    if redir:
        return redir
    _kimi_pending.pop(user["id"], None)
    return RedirectResponse("/sources", status_code=303)


@router.post("/sources/kimi/add")
@router.post("/pool/kimi/add")
async def kimi_add(request: Request, api_key: str = Form(""), label: str = Form("")):
    user, redir = require_user(request)
    if redir:
        return redir
    if not limiter.allow(f"kimi-add:{user['id']}", 5, 60):
        _pool_flash[user["id"]] = ("err", "Too many attempts. Try again in a minute.", "kimi")
        return RedirectResponse("/sources", status_code=303)
    sid = kimi_mod.ensure_kimi_source()
    cred_id, err = await kimi_mod.add_plan_key(sid, api_key, user["id"], label=label)
    if err or not cred_id:
        _pool_flash[user["id"]] = ("err", err or "Could not add that key.", "kimi")
        return RedirectResponse("/sources", status_code=303)
    invalidate_model_cache("kimi")
    _pool_flash[user["id"]] = ("ok", "Kimi Code key added to the pool.", "kimi")
    return RedirectResponse("/sources", status_code=303)


@router.post("/sources/mimo/start")
@router.post("/pool/mimo/start")
async def mimo_start(request: Request):
    user, redir = require_user(request)
    if redir:
        return redir
    if not limiter.allow(f"mimo-start:{user['id']}", 5, 60):
        _pool_flash[user["id"]] = ("err", "Too many attempts. Try again in a minute.", "mimo")
        return RedirectResponse("/sources", status_code=303)
    try:
        data = mimo_mod.start_login()
    except Exception:
        logger.warning("Could not start Xiaomi sign-in", exc_info=True)
        _pool_flash[user["id"]] = ("err", "Could not start Xiaomi sign-in. Try again shortly.", "mimo")
        return RedirectResponse("/sources", status_code=303)
    redirect = _mimo_redirect_uri(request)
    data["manual_url"] = mimo_mod.authorize_url(data["public_key"], manual=True)
    data["auto_url"] = mimo_mod.authorize_url(data["public_key"], redirect_uri=redirect)
    data["_started"] = time.time()
    data["user_id"] = user["id"]
    _mimo_pending[user["id"]] = data
    return RedirectResponse("/sources", status_code=303)


async def _mimo_finish(user: dict, pending: dict, blob: str):
    try:
        payload = mimo_mod.decrypt_payload(str(pending.get("private_key") or ""), blob)
    except Exception:
        return None, "That Xiaomi code could not be read. Start the sign-in again."
    sid = mimo_mod.ensure_mimo_source()
    try:
        cred_id = mimo_mod.store_login(sid, payload, user["id"])
    except ValueError:
        return None, "Xiaomi did not return a Token Plan key. Pick Token Plan in the authorize page and try again."
    row = db.fetchone("SELECT * FROM credentials WHERE id=?", (cred_id,))
    if row:
        try:
            await mimo_mod.refresh_quota(row, force=True)
        except Exception:
            logger.warning("Xiaomi catalog refresh failed after sign-in", exc_info=True)
    invalidate_model_cache("mimo")
    return cred_id, ""


@router.get("/sources/mimo/callback")
@router.get("/pool/mimo/callback")
async def mimo_callback(request: Request):
    user, redir = require_user(request)
    if redir:
        return redir
    pending = _mimo_pending_for(user["id"])
    state = str(request.query_params.get("state") or "").strip()
    blob = str(request.query_params.get("u") or "").strip()
    if not pending or (state and state != str(pending.get("state") or "")):
        _pool_flash[user["id"]] = ("err", "Xiaomi sign-in expired or did not match this session. Start again.", "mimo")
        return RedirectResponse("/sources", status_code=303)
    if not blob:
        _pool_flash[user["id"]] = ("err", "Xiaomi did not return a sign-in code. Paste the code from the authorize page.", "mimo")
        return RedirectResponse("/sources", status_code=303)
    cred_id, err = await _mimo_finish(user, pending, blob)
    _mimo_pending.pop(user["id"], None)
    if err or not cred_id:
        _pool_flash[user["id"]] = ("err", err or "Could not link this Xiaomi account.", "mimo")
        return RedirectResponse("/sources", status_code=303)
    _pool_flash[user["id"]] = ("ok", "Xiaomi Token Plan added to the pool.", "mimo")
    return RedirectResponse("/sources", status_code=303)


@router.post("/sources/mimo/complete")
@router.post("/pool/mimo/complete")
async def mimo_complete(request: Request, code: str = Form("")):
    user, redir = require_user(request)
    if redir:
        return redir
    pending = _mimo_pending_for(user["id"])
    if not pending:
        _pool_flash[user["id"]] = ("err", "Xiaomi sign-in expired. Start again.", "mimo")
        return RedirectResponse("/sources", status_code=303)
    blob = (code or "").strip()
    if "u=" in blob:
        blob = blob.split("u=", 1)[1].split("&", 1)[0]
    cred_id, err = await _mimo_finish(user, pending, blob)
    if err or not cred_id:
        _pool_flash[user["id"]] = ("err", err or "Could not link this Xiaomi account.", "mimo")
        return RedirectResponse("/sources", status_code=303)
    _mimo_pending.pop(user["id"], None)
    _pool_flash[user["id"]] = ("ok", "Xiaomi Token Plan added to the pool.", "mimo")
    return RedirectResponse("/sources", status_code=303)


@router.post("/sources/mimo/cancel")
@router.post("/pool/mimo/cancel")
async def mimo_cancel(request: Request):
    user, redir = require_user(request)
    if redir:
        return redir
    _mimo_pending.pop(user["id"], None)
    return RedirectResponse("/sources", status_code=303)


@router.post("/sources/mimo/add")
@router.post("/pool/mimo/add")
async def mimo_add(request: Request, api_key: str = Form(""), label: str = Form(""), region: str = Form("cn")):
    user, redir = require_user(request)
    if redir:
        return redir
    if not limiter.allow(f"mimo-add:{user['id']}", 5, 60):
        _pool_flash[user["id"]] = ("err", "Too many attempts. Try again in a minute.", "mimo")
        return RedirectResponse("/sources", status_code=303)
    sid = mimo_mod.ensure_mimo_source()
    chosen = region if region in ("cn", "sgp", "ams") else "cn"
    cred_id, err = await mimo_mod.add_plan_key(sid, api_key, user["id"], label=label, region=chosen)
    if err or not cred_id:
        _pool_flash[user["id"]] = ("err", err or "Could not add that key.", "mimo")
        return RedirectResponse("/sources", status_code=303)
    invalidate_model_cache("mimo")
    _pool_flash[user["id"]] = ("ok", "Xiaomi Token Plan key added to the pool.", "mimo")
    return RedirectResponse("/sources", status_code=303)


@router.post("/sources/mimo/{cred_id}/revoke")
@router.post("/pool/mimo/{cred_id}/revoke")
async def mimo_revoke(request: Request, cred_id: int):
    user, redir = require_user(request)
    if redir:
        return redir
    sid = mimo_mod.ensure_mimo_source()
    row = db.fetchone("SELECT id, donated_by, source_id FROM credentials WHERE id=?", (cred_id,))
    if not row or row["source_id"] != sid:
        raise HTTPException(404, "Not found")
    if row["donated_by"] != user["id"] and not user["is_admin"]:
        raise HTTPException(403, "Forbidden")
    mimo_mod.disable_credential(cred_id)
    invalidate_model_cache("mimo")
    _pool_flash[user["id"]] = ("ok", "Xiaomi account removed.", "mimo")
    return RedirectResponse("/sources", status_code=303)


@router.post("/sources/claude/start")
@router.post("/pool/claude/start")
async def claude_start(request: Request, console: str = Form("")):
    user, redir = require_user(request)
    if redir:
        return redir
    if not limiter.allow(f"claude-start:{user['id']}", 5, 60):
        _pool_flash[user["id"]] = ("err", "Too many attempts. Try again in a minute.", "claude")
        return RedirectResponse("/sources", status_code=303)
    data = claude_mod.start_login(console=str(console).strip().lower() in ("1", "true", "console", "on"))
    if not data.get("auth_url") or not data.get("verifier") or not data.get("state"):
        _pool_flash[user["id"]] = ("err", "Claude sign-in could not be prepared. Try again shortly.", "claude")
        return RedirectResponse("/sources", status_code=303)
    data["_started"] = time.time()
    data["user_id"] = user["id"]
    _claude_pending[user["id"]] = data
    return RedirectResponse("/sources", status_code=303)


@router.post("/sources/claude/complete")
@router.post("/pool/claude/complete")
async def claude_complete(request: Request, code: str = Form("")):
    user, redir = require_user(request)
    if redir:
        return redir
    pending = _claude_pending_for(user["id"])
    if not pending:
        _pool_flash[user["id"]] = ("err", "Claude sign-in expired. Start again.", "claude")
        return RedirectResponse("/sources", status_code=303)
    cred_id, err = await claude_mod.complete_login(
        code,
        str(pending.get("verifier") or ""),
        str(pending.get("state") or ""),
        user["id"],
    )
    if err or not cred_id:
        _pool_flash[user["id"]] = ("err", err or "Could not link this Claude account.", "claude")
        return RedirectResponse("/sources", status_code=303)
    _claude_pending.pop(user["id"], None)
    invalidate_model_cache("claude")
    _pool_flash[user["id"]] = ("ok", "Claude account added to the pool.", "claude")
    return RedirectResponse("/sources", status_code=303)


@router.post("/sources/claude/cancel")
@router.post("/pool/claude/cancel")
async def claude_cancel(request: Request):
    user, redir = require_user(request)
    if redir:
        return redir
    _claude_pending.pop(user["id"], None)
    return RedirectResponse("/sources", status_code=303)


@router.post("/sources/claude/{cred_id}/revoke")
@router.post("/pool/claude/{cred_id}/revoke")
async def claude_revoke(request: Request, cred_id: int):
    user, redir = require_user(request)
    if redir:
        return redir
    sid = claude_mod.ensure_claude_source()
    row = db.fetchone("SELECT * FROM credentials WHERE id=?", (cred_id,))
    if not row or row["source_id"] != sid:
        raise HTTPException(404, "Not found")
    if row["donated_by"] != user["id"] and not user["is_admin"]:
        raise HTTPException(403, "Forbidden")
    await claude_mod.revoke_remote(row)
    claude_mod.disable_credential(cred_id)
    invalidate_model_cache("claude")
    _pool_flash[user["id"]] = ("ok", "Claude account removed.", "claude")
    return RedirectResponse("/sources", status_code=303)


@router.post("/sources/kimi/{cred_id}/revoke")
@router.post("/pool/kimi/{cred_id}/revoke")
async def kimi_revoke(request: Request, cred_id: int):
    user, redir = require_user(request)
    if redir:
        return redir
    sid = kimi_mod.ensure_kimi_source()
    row = db.fetchone("SELECT id, donated_by, source_id FROM credentials WHERE id=?", (cred_id,))
    if not row or row["source_id"] != sid:
        raise HTTPException(404, "Not found")
    if row["donated_by"] != user["id"] and not user["is_admin"]:
        raise HTTPException(403, "Forbidden")
    kimi_mod.disable_credential(cred_id)
    invalidate_model_cache("kimi")
    _pool_flash[user["id"]] = ("ok", "Kimi account removed.", "kimi")
    return RedirectResponse("/sources", status_code=303)


@router.post("/sources/zai/{cred_id}/revoke")
@router.post("/pool/zai/{cred_id}/revoke")
async def zai_revoke(request: Request, cred_id: int):
    user, redir = require_user(request)
    if redir:
        return redir
    sid = zai_mod.ensure_zai_source()
    row = db.fetchone("SELECT id, donated_by, source_id FROM credentials WHERE id=?", (cred_id,))
    if not row or row["source_id"] != sid:
        raise HTTPException(404, "Not found")
    if row["donated_by"] != user["id"] and not user["is_admin"]:
        raise HTTPException(403, "Forbidden")
    zai_mod.disable_credential(cred_id)
    return RedirectResponse("/sources", status_code=303)


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
        else "Free OpenRouter models are off. Pool models still fall back to your key automatically.",
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


@router.get("/admin/sources", response_class=HTMLResponse)
async def admin_sources(request: Request):
    user, redir = require_admin(request)
    if redir:
        return redir
    notice = _notices.pop(user["id"], None)
    try:
        keys_page = int(request.query_params.get("keys") or 1)
    except (TypeError, ValueError):
        keys_page = 1
    return render(
        request,
        "admin_sources.html",
        user,
        **_admin_sources_ctx(notice=notice, ollama_page=keys_page),
    )


@router.post("/admin/sources")
async def admin_sources_create(
    request: Request,
    type: str = Form(),
    name: str = Form(),
    base_url: str = Form(""),
    api_key: str = Form(""),
    keys: str = Form(""),
    rotation: str = Form("sticky"),
    model: str = Form(""),
):
    user, redir = require_admin(request)
    if redir:
        return redir
    if type != "openai":
        return RedirectResponse("/admin/sources", status_code=303)
    url = (base_url.strip() or "http://127.0.0.1:8080/v1")
    if not allowed_base_url(url):
        return render(
            request,
            "admin_sources.html",
            user,
            **_admin_sources_ctx(error="Base URL must be http(s) and not a metadata host."),
            status_code=400,
        )
    if rotation not in ("every_request", "round_robin", "none", "sticky"):
        rotation = "sticky"
    cfg = {"base_url": url, "rotation": rotation}
    try:
        with db.tx() as conn:
            conn.execute(
                "INSERT INTO sources (type, name, enabled, config, created_at) VALUES (?,?,?,?,?)",
                ("openai", (name.strip() or TYPES["openai"])[:80], 1, json.dumps(cfg), db.now()),
            )
            sid = db.last_id(conn)
            _add_key_lines(conn, sid, keys)
    except ValueError as exc:
        return render(
            request,
            "admin_sources.html",
            user,
            **_admin_sources_ctx(error=str(exc)),
            status_code=400,
        )
    _notices[user["id"]] = "OpenAI-compatible source added."
    return RedirectResponse("/admin/sources", status_code=303)


@router.post("/admin/sources/llama")
async def admin_llama_save(
    request: Request,
    base_url: str = Form(""),
    api_key: str = Form(""),
    model: str = Form(""),
    enabled: str = Form("0"),
    insecure: str = Form("0"),
):
    user, redir = require_admin(request)
    if redir:
        return redir
    from ..sources import ensure_llama_source

    sid = ensure_llama_source()
    url = base_url.strip() or "http://127.0.0.1:8080/v1"
    if not allowed_base_url(url):
        return render(
            request,
            "admin_sources.html",
            user,
            **_admin_sources_ctx(error="Base URL must be http(s) and not a metadata host."),
            status_code=400,
        )
    cfg = _source_cfg(db.fetchone("SELECT config FROM sources WHERE id=?", (sid,)))
    cfg["base_url"] = url
    if model.strip():
        cfg["model"] = model.strip()[:120]
    else:
        cfg.pop("model", None)
    cfg["verify"] = 0 if insecure in ("1", "on", "true") else 1
    on = 1 if enabled in ("1", "on", "true") else 0
    key = api_key.strip()
    if len(key) > 4096 or any(0xD800 <= ord(char) <= 0xDFFF for char in key):
        return render(
            request,
            "admin_sources.html",
            user,
            **_admin_sources_ctx(error="API key must be at most 4096 valid characters."),
            status_code=400,
        )
    with db.tx() as conn:
        conn.execute("UPDATE sources SET config=?, enabled=? WHERE id=?", (json.dumps(cfg), on, sid))
        if key:
            conn.execute("DELETE FROM credentials WHERE source_id=?", (sid,))
            conn.execute(
                """INSERT INTO credentials (source_id, label, secret, extra, week_start, donated_by, created_at)
                   VALUES (?,?,?,?,?,?,?)""",
                (sid, "llama", encrypt(key), encrypt("{}"), db.iso_week_start(), None, db.now()),
            )
    _notices[user["id"]] = "Local llama saved."
    return RedirectResponse("/admin/sources", status_code=303)


@router.post("/admin/sources/fetch-domains")
async def admin_fetch_domains(
    request: Request,
    blacklist: str = Form(""),
    whitelist: str = Form(""),
):
    user, redir = require_admin(request)
    if redir:
        return redir
    from ..search.policy import save_domain_lists
    from ..search.ssrf import parse_domain_list

    if len(blacklist) > 20_000 or len(whitelist) > 20_000:
        return render(
            request,
            "admin_sources.html",
            user,
            **_admin_sources_ctx(error="Domain lists must be at most 20,000 characters."),
            status_code=400,
        )
    black = parse_domain_list(blacklist)
    white = parse_domain_list(whitelist)
    save_domain_lists(black, white)
    _notices[user["id"]] = (
        f"Fetch domains saved. {len(black)} blocked"
        + (f", {len(white)} allowed." if white else ". Whitelist empty — every other public domain is allowed.")
    )
    return RedirectResponse("/admin/sources#fetch", status_code=303)


@router.post("/admin/sources/ollama")
async def admin_ollama_save(
    request: Request,
    base_url: str = Form(""),
    keys: str = Form(""),
    model: str = Form(""),
    enabled: str = Form("0"),
):
    user, redir = require_admin(request)
    if redir:
        return redir
    from ..sources import ensure_ollama_source

    sid = ensure_ollama_source()
    url = base_url.strip() or "https://ollama.com/api"
    if not allowed_base_url(url):
        return render(
            request,
            "admin_sources.html",
            user,
            **_admin_sources_ctx(error="Base URL must be http(s) and not a metadata host."),
            status_code=400,
        )
    cfg = _source_cfg(db.fetchone("SELECT config FROM sources WHERE id=?", (sid,)))
    cfg["base_url"] = url
    if model.strip():
        cfg["model"] = model.strip()[:120]
    else:
        cfg.pop("model", None)
    on = 1 if enabled in ("1", "on", "true") else 0
    try:
        with db.tx() as conn:
            conn.execute("UPDATE sources SET config=?, enabled=? WHERE id=?", (json.dumps(cfg), on, sid))
            added = _add_key_lines(conn, sid, keys)
    except ValueError as exc:
        return render(
            request,
            "admin_sources.html",
            user,
            **_admin_sources_ctx(error=str(exc)),
            status_code=400,
        )
    _notices[user["id"]] = f"Ollama Cloud saved.{f' Added {added} key(s).' if added else ''}"
    return RedirectResponse("/admin/sources", status_code=303)


@router.post("/admin/sources/pools")
async def admin_pools_save(
    request: Request,
    grok_pool_shared: str = Form("0"),
    grok_open_access: str = Form("0"),
    chatgpt_pool_shared: str = Form("0"),
    chatgpt_open_access: str = Form("0"),
    zai_pool_shared: str = Form("0"),
    zai_open_access: str = Form("0"),
    devin_pool_shared: str = Form("0"),
    devin_open_access: str = Form("0"),
    kimi_pool_shared: str = Form("0"),
    kimi_open_access: str = Form("0"),
    mimo_pool_shared: str = Form("0"),
    mimo_open_access: str = Form("0"),
):
    user, redir = require_admin(request)
    if redir:
        return redir
    flags = {
        "grok": (grok_pool_shared, grok_open_access),
        "chatgpt": (chatgpt_pool_shared, chatgpt_open_access),
        "zai": (zai_pool_shared, zai_open_access),
        "devin": (devin_pool_shared, devin_open_access),
        "kimi": (kimi_pool_shared, kimi_open_access),
        "mimo": (mimo_pool_shared, mimo_open_access),
    }
    for kind, (shared, open_access) in flags.items():
        pool_policy.set_admin_policy(
            kind,
            pool_shared=shared in ("1", "on", "true"),
            open_access=open_access in ("1", "on", "true"),
        )
    _notices[user["id"]] = "Shared pool access saved."
    return RedirectResponse("/admin/sources", status_code=303)


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


@router.post("/admin/sources/grok")
async def admin_grok_save(
    request: Request,
    offpool_usage_guard: str = Form("0"),
):
    user, redir = require_admin(request)
    if redir:
        return redir
    sid = ensure_grok_source()
    cfg = _source_cfg(db.fetchone("SELECT config FROM sources WHERE id=?", (sid,)))
    enabled = offpool_usage_guard in ("1", "on", "true")
    cfg["offpool_usage_guard"] = enabled
    with db.tx() as conn:
        conn.execute("UPDATE sources SET config=? WHERE id=?", (json.dumps(cfg), sid))
        if not enabled:
            conn.execute("UPDATE credentials SET pool_held_until=NULL WHERE source_id=?", (sid,))
    _notices[user["id"]] = "Grok outside-usage guard saved."
    return RedirectResponse("/admin/sources", status_code=303)


@router.post("/admin/sources/zai")
async def admin_zai_save(
    request: Request,
    offpool_usage_guard: str = Form("0"),
):
    user, redir = require_admin(request)
    if redir:
        return redir
    sid = zai_mod.ensure_zai_source()
    cfg = _source_cfg(db.fetchone("SELECT config FROM sources WHERE id=?", (sid,)))
    enabled = offpool_usage_guard in ("1", "on", "true")
    cfg["offpool_usage_guard"] = enabled
    with db.tx() as conn:
        conn.execute("UPDATE sources SET config=? WHERE id=?", (json.dumps(cfg), sid))
        if not enabled:
            conn.execute("UPDATE credentials SET pool_held_until=NULL WHERE source_id=?", (sid,))
    _notices[user["id"]] = "Z.AI outside-usage guard saved."
    return RedirectResponse("/admin/sources", status_code=303)


@router.post("/admin/sources/openrouter")
async def admin_openrouter_save(
    request: Request,
    enabled: str = Form("0"),
):
    user, redir = require_admin(request)
    if redir:
        return redir
    sid = openrouter_mod.ensure_openrouter_source()
    on = 1 if enabled in ("1", "on", "true") else 0
    openrouter_mod.clear_global_key(sid)
    db.execute("UPDATE sources SET enabled=? WHERE id=?", (on, sid))
    _notices[user["id"]] = "OpenRouter settings saved."
    return RedirectResponse("/admin/sources", status_code=303)


@router.post("/admin/sources/{source_id}/test")
async def admin_source_test(request: Request, source_id: int):
    user = current_user(request)
    if not user or not user["is_admin"]:
        return JSONResponse({"ok": False, "error": "auth"}, status_code=401)
    if not limiter.allow(f"probe:{user['id']}", 8, 60):
        return JSONResponse(
            {"ok": False, "error": "Too many tests. Wait a minute.", "checks": []},
            status_code=429,
        )
    row = db.fetchone("SELECT id FROM sources WHERE id=?", (source_id,))
    if not row:
        return JSONResponse({"ok": False, "error": "Unknown source", "checks": []}, status_code=404)
    result = await probe_source(source_id)
    return JSONResponse(result, headers={"Cache-Control": "no-store"})


@router.post("/admin/sources/ollama/keys/{cred_id}/delete")
async def admin_ollama_key_delete(request: Request, cred_id: int):
    user, redir = require_admin(request)
    if redir:
        return redir
    from ..sources import ensure_ollama_source

    sid = ensure_ollama_source()
    row = db.fetchone("SELECT id, source_id FROM credentials WHERE id=?", (cred_id,))
    if not row or row["source_id"] != sid:
        raise HTTPException(404, "Not found")
    with db.tx() as conn:
        conn.execute("DELETE FROM credentials WHERE id=? AND source_id=?", (cred_id, sid))
    _notices[user["id"]] = "Ollama key removed."
    return RedirectResponse("/admin/sources", status_code=303)


@router.post("/admin/sources/{source_id}/toggle")
async def admin_toggle(request: Request, source_id: int):
    user, redir = require_admin(request)
    if redir:
        return redir
    with db.tx() as conn:
        row = conn.execute("SELECT enabled FROM sources WHERE id=?", (source_id,)).fetchone()
        if row:
            conn.execute("UPDATE sources SET enabled=? WHERE id=?", (0 if row["enabled"] else 1, source_id))
    return RedirectResponse("/admin/sources", status_code=303)


@router.post("/admin/sources/{source_id}/delete")
async def admin_delete(request: Request, source_id: int):
    user, redir = require_admin(request)
    if redir:
        return redir
    with db.tx() as conn:
        row = conn.execute("SELECT type FROM sources WHERE id=?", (source_id,)).fetchone()
        if row and row["type"] not in _BUILTINS:
            conn.execute("DELETE FROM sources WHERE id=?", (source_id,))
    return RedirectResponse("/admin/sources", status_code=303)

