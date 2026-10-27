"""Admin mutations on sources and pools: create OpenAI-compatible sources,
save llama/ollama/grok/zai/explabs/openrouter settings, probe, toggle, delete."""

from __future__ import annotations

import json
from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from ... import accounts, db, speech
from ...crypto import encrypt
from ...security import allowed_base_url, limiter
from ...sources import TYPES, invalidate_model_cache
from ...sources import openrouter as openrouter_mod
from ...sources import pool_policy
from ...sources.ollama import DEFAULT_BASE as OLLAMA_BASE
from ...sources.probe import probe_source
from ..deps import _notices, current_user, render, require_admin
from .admin_pages import _admin_sources_ctx, _admin_web_ctx
from .context import _source_cfg

router = APIRouter()

_BUILTINS = {"grok", "llama", "ollama", "zai", "chatgpt", "devin", "kimi", "mimo", "claude", "cursor", "nanogpt", "openrouter", "explabs"}


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
    from ...sources import ensure_llama_source

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
    invalidate_model_cache("llama")
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
    from ...search.policy import save_domain_lists
    from ...search.ssrf import parse_domain_list

    if len(blacklist) > 20_000 or len(whitelist) > 20_000:
        return render(
            request,
            "admin_web.html",
            user,
            **_admin_web_ctx(error="Domain lists must be at most 20,000 characters."),
            status_code=400,
        )
    black = parse_domain_list(blacklist)
    white = parse_domain_list(whitelist)
    save_domain_lists(black, white)
    _notices[user["id"]] = (
        f"Fetch domains saved. {len(black)} blocked"
        + (f", {len(white)} allowed." if white else ". Whitelist empty — every other public domain is allowed.")
    )
    return RedirectResponse("/admin/web", status_code=303)


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
    from ...sources import ensure_ollama_source

    sid = ensure_ollama_source()
    url = base_url.strip() or OLLAMA_BASE
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
    invalidate_model_cache("ollama")
    _notices[user["id"]] = f"Ollama Cloud saved.{f' Added {added} key(s).' if added else ''}"
    return RedirectResponse("/admin/sources", status_code=303)


@router.post("/admin/sources/pools")
@router.post("/admin/pools")
async def admin_pools_save(request: Request):
    user, redir = require_admin(request)
    if redir:
        return redir
    form = await request.form()
    on = ("1", "on", "true")
    for kind in pool_policy.POOL_KINDS:
        pool_policy.set_admin_policy(
            kind,
            pool_shared=form.get(f"{kind}_pool_shared", "0") in on,
            open_access=form.get(f"{kind}_open_access", "0") in on,
        )
    _notices[user["id"]] = "Shared pool access saved."
    return RedirectResponse("/admin/pools", status_code=303)


_GUARD_SAVED = {
    "grok": "Grok outside-usage guard saved.",
    "zai": "Z.AI outside-usage guard saved.",
}


def _save_guard(user, kind: str, raw: str) -> RedirectResponse:
    pool_policy.set_guard(kind, raw in ("1", "on", "true"))
    _notices[user["id"]] = _GUARD_SAVED.get(kind) or f"{pool_policy.POOL_LABELS[kind]} outside-usage guard saved."
    return RedirectResponse("/admin/pools#guards", status_code=303)


@router.post("/admin/sources/grok")
async def admin_grok_save(
    request: Request,
    offpool_usage_guard: str = Form("0"),
):
    user, redir = require_admin(request)
    if redir:
        return redir
    return _save_guard(user, "grok", offpool_usage_guard)


@router.post("/admin/sources/zai")
async def admin_zai_save(
    request: Request,
    offpool_usage_guard: str = Form("0"),
):
    user, redir = require_admin(request)
    if redir:
        return redir
    return _save_guard(user, "zai", offpool_usage_guard)


@router.post("/admin/pools/guard/{kind}")
async def admin_pool_guard_save(
    request: Request,
    kind: str,
    offpool_usage_guard: str = Form("0"),
):
    user, redir = require_admin(request)
    if redir:
        return redir
    if kind not in pool_policy.GUARD_KINDS:
        raise HTTPException(404, "No usage guard for this pool")
    return _save_guard(user, kind, offpool_usage_guard)


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
    row = db.fetchone("SELECT id, type FROM sources WHERE id=?", (source_id,))
    if not row:
        return JSONResponse({"ok": False, "error": "Unknown source", "checks": []}, status_code=404)
    if row.get("type") == speech.TYPE:
        result = await speech.probe()
    else:
        result = await probe_source(source_id)
    return JSONResponse(result, headers={"Cache-Control": "no-store"})


@router.post("/admin/sources/ollama/keys/{cred_id}/delete")
async def admin_ollama_key_delete(request: Request, cred_id: int):
    user, redir = require_admin(request)
    if redir:
        return redir
    from ...sources import ensure_ollama_source

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
