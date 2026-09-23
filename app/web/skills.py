from __future__ import annotations

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse

from .. import skill_proposals, skills
from ..security import limiter
from .deps import _chat_auth, _json_object, _skill_fail, render, require_user

router = APIRouter()

@router.get("/skills/proposals/{proposal_id}")
async def skills_proposal_get(request: Request, proposal_id: str):
    user, err = _chat_auth(request)
    if err:
        return err
    item = skill_proposals.get_proposal(user["id"], proposal_id)
    if item is None:
        raise HTTPException(404, "unknown proposal")
    return JSONResponse(skill_proposals.public_full(item), headers={"Cache-Control": "no-store"})


@router.post("/skills/proposals/{proposal_id}/confirm")
async def skills_proposal_confirm(request: Request, proposal_id: str):
    user, err = _chat_auth(request)
    if err:
        return err
    if not limiter.allow(f"skills:{user['id']}", 30, 60):
        raise HTTPException(429, "Search is busy. Try again shortly.")
    raw = await _json_object(request)
    try:
        row = skill_proposals.confirm_proposal(user["id"], proposal_id, raw)
    except skills.SkillError as exc:
        return _skill_fail(exc)
    return JSONResponse(row, headers={"Cache-Control": "no-store"})


@router.post("/skills/proposals/{proposal_id}/reject")
async def skills_proposal_reject(request: Request, proposal_id: str):
    user, err = _chat_auth(request)
    if err:
        return err
    try:
        row = skill_proposals.reject_proposal(user["id"], proposal_id)
    except skills.SkillError as exc:
        return _skill_fail(exc)
    return JSONResponse(row, headers={"Cache-Control": "no-store"})


@router.get("/skills", response_class=HTMLResponse)
async def skills_page(request: Request):
    user, redir = require_user(request)
    if redir:
        return redir
    return render(request, "skills.html", user, skills_bundle=skills.list_bundle(user["id"]))


@router.get("/skills/api")
async def skills_api(request: Request):
    user, err = _chat_auth(request)
    if err:
        return err
    return JSONResponse(skills.list_bundle(user["id"]), headers={"Cache-Control": "no-store"})


@router.get("/skills/index")
async def skills_index(request: Request):
    user, err = _chat_auth(request)
    if err:
        return err
    return JSONResponse(
        {"skills": skills.index_for_chat(user["id"])},
        headers={"Cache-Control": "no-store"},
    )


@router.patch("/skills/{slug}")
async def skills_patch(request: Request, slug: str):
    user, err = _chat_auth(request)
    if err:
        return err
    raw = await _json_object(request)
    try:
        data = skills.patch_skill(user["id"], slug, raw)
    except skills.SkillError as exc:
        return _skill_fail(exc)
    return JSONResponse(data, headers={"Cache-Control": "no-store"})


@router.delete("/skills/{slug}")
async def skills_delete(request: Request, slug: str):
    user, err = _chat_auth(request)
    if err:
        return err
    try:
        skills.delete_skill(user["id"], slug)
    except skills.SkillError as exc:
        return _skill_fail(exc)
    return JSONResponse({"ok": True}, headers={"Cache-Control": "no-store"})


@router.post("/skills/upload")
async def skills_upload(request: Request, file: UploadFile = File(...)):
    user, err = _chat_auth(request)
    if err:
        return err
    if not limiter.allow(f"skills:{user['id']}", 30, 60):
        raise HTTPException(429, "Search is busy. Try again shortly.")
    data = await file.read()
    from ..skill_install import MAX_ZIP, parse_upload

    if len(data) > MAX_ZIP:
        raise HTTPException(400, "zip too large")
    try:
        slug, files = parse_upload(file.filename or "", data)
        row = skills.install_files(user["id"], files, origin="upload", slug=slug)
    except skills.SkillError as exc:
        return _skill_fail(exc)
    return JSONResponse(row, headers={"Cache-Control": "no-store"})


@router.get("/skills/catalog")
async def skills_catalog(request: Request):
    user, err = _chat_auth(request)
    if err:
        return err
    if not limiter.allow(f"skills:{user['id']}", 30, 60):
        raise HTTPException(429, "Search is busy. Try again shortly.")
    q = (request.query_params.get("q") or "").strip()
    from ..skill_install import catalog_search

    try:
        data = await catalog_search(q)
    except skills.SkillError as exc:
        return _skill_fail(exc)
    return JSONResponse(data, headers={"Cache-Control": "no-store"})


@router.post("/skills/install")
async def skills_install(request: Request):
    user, err = _chat_auth(request)
    if err:
        return err
    raw = await _json_object(request)
    ident = str(raw.get("id") or "")
    source = str(raw.get("source") or "")
    skill_id = str(raw.get("skill_id") or raw.get("skillId") or "")
    origin = "github"
    if ident and ident.count("/") >= 2:
        parts = ident.split("/")
        source = f"{parts[0]}/{parts[1]}"
        skill_id = "/".join(parts[2:]) or skill_id
        origin = "skills_sh"
    elif ident:
        origin = "skills_sh"
    if not source or not skill_id:
        raise HTTPException(400, "source and skill_id are required")
    existing = skills.find_by_source_id(user["id"], ident or f"{source}/{skill_id}")
    if existing:
        # Already installed: return the row without touching GitHub or the
        # install budget, so repeat clicks are instant no-ops.
        return JSONResponse(skills._public(existing), headers={"Cache-Control": "no-store"})
    if not limiter.allow(f"skills-install:{user['id']}", 5, 3600):
        raise HTTPException(429, "Install rate limit reached. Try again shortly.")
    from ..skill_install import fetch_github_skill, valid_github_source

    if not valid_github_source(source):
        raise HTTPException(400, "invalid source")
    try:
        files = await fetch_github_skill(source, skill_id)
        row = skills.install_files(
            user["id"],
            files,
            origin=origin,
            source=source,
            source_id=ident or f"{source}/{skill_id}",
            slug=None,
        )
    except skills.SkillError as exc:
        return _skill_fail(exc)
    return JSONResponse(row, headers={"Cache-Control": "no-store"})

