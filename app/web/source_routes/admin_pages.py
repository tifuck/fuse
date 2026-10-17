"""Admin sources/pools/web pages and the render contexts behind them,
including the Ollama Cloud key views."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from ... import db, stats
from ...sources import TYPES
from ...sources import chatgpt as chatgpt_mod
from ...sources import devin as devin_mod
from ...sources import openrouter as openrouter_mod
from ...sources import explabs as explabs_mod
from ...sources import kimi as kimi_mod
from ...sources import mimo as mimo_mod
from ...sources import claude as claude_mod
from ...sources import cursor as cursor_mod
from ...sources import zai as zai_mod
from ...sources import ollama_access
from ...sources.grok import ensure_grok_source
from ...sources.ollama import OllamaSource
from ...sources import pool_policy
from ..deps import _notices, render, require_admin
from .context import _source_cfg

router = APIRouter()

_OLLAMA_PAGE = 10


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
    from ...sources import ensure_llama_source, ensure_ollama_source

    llama_id = ensure_llama_source()
    ollama_id = ensure_ollama_source()
    openrouter_id = openrouter_mod.ensure_openrouter_source()
    explabs_id = explabs_mod.ensure_explabs_source()
    explabs = db.fetchone("SELECT * FROM sources WHERE id=?", (explabs_id,))
    explabs_user_n = db.fetchone(
        "SELECT COUNT(*) AS n FROM credentials WHERE source_id=? AND disabled=0 AND donated_by IS NOT NULL",
        (explabs_id,),
    )
    llama = db.fetchone("SELECT * FROM sources WHERE id=?", (llama_id,))
    ollama = db.fetchone("SELECT * FROM sources WHERE id=?", (ollama_id,))
    openrouter = db.fetchone("SELECT * FROM sources WHERE id=?", (openrouter_id,))
    llama_cfg = _source_cfg(llama)
    ollama_cfg = _source_cfg(ollama)
    llama_key = db.fetchone("SELECT id FROM credentials WHERE source_id=? AND disabled=0", (llama_id,))
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
        "explabs": explabs,
        "explabs_user_count": int(explabs_user_n["n"] if explabs_user_n else 0),
        "openrouter": openrouter,
        "openrouter_user_count": int(openrouter_user_n["n"] if openrouter_user_n else 0),
        "extras": extras,
        "extra_counts": extra_counts,
        "error": error,
        "notice": notice,
        "types": TYPES,
    }


def _admin_pools_ctx(notice: str | None = None) -> dict:
    ensure = {
        "grok": ensure_grok_source,
        "chatgpt": chatgpt_mod.ensure_chatgpt_source,
        "zai": zai_mod.ensure_zai_source,
        "devin": devin_mod.ensure_devin_source,
        "kimi": kimi_mod.ensure_kimi_source,
        "mimo": mimo_mod.ensure_mimo_source,
        "claude": claude_mod.ensure_claude_source,
        "cursor": cursor_mod.ensure_cursor_source,
    }
    counts = {}
    for kind in pool_policy.POOL_KINDS:
        row = db.fetchone(
            "SELECT COUNT(*) AS n FROM credentials WHERE source_id=? AND disabled=0", (ensure[kind](),)
        )
        counts[kind] = int(row["n"] if row else 0)
    return {
        "pool_kinds": pool_policy.POOL_KINDS,
        "pool_policies": {kind: pool_policy.admin_policy(kind) for kind in pool_policy.POOL_KINDS},
        "pool_labels": pool_policy.POOL_LABELS,
        "pool_counts": counts,
        "guard_kinds": pool_policy.GUARD_KINDS,
        "guards": {kind: pool_policy.guard_enabled(kind) for kind in pool_policy.GUARD_KINDS},
        "notice": notice,
    }


def _admin_web_ctx(error: str | None = None, notice: str | None = None) -> dict:
    from ...search.policy import domain_lists_text

    black, white = domain_lists_text()
    return {"fetch_blacklist": black, "fetch_whitelist": white, "error": error, "notice": notice}


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


@router.get("/admin/pools", response_class=HTMLResponse)
async def admin_pools(request: Request):
    user, redir = require_admin(request)
    if redir:
        return redir
    notice = _notices.pop(user["id"], None)
    return render(request, "admin_pools.html", user, **_admin_pools_ctx(notice=notice))


@router.get("/admin/web", response_class=HTMLResponse)
async def admin_web(request: Request):
    user, redir = require_admin(request)
    if redir:
        return redir
    notice = _notices.pop(user["id"], None)
    return render(request, "admin_web.html", user, **_admin_web_ctx(notice=notice))
