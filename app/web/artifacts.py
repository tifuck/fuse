from __future__ import annotations

from urllib.parse import quote_plus

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from .. import artifacts, chats
from .deps import _chat_auth, _json_object, current_user, render, require_user

router = APIRouter()

def _artifact_http_error(exc: artifacts.ArtifactError) -> HTTPException:
    return HTTPException(exc.status_code, exc.detail)


def _artifact_version(raw) -> int | None:
    if raw in (None, ""):
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise HTTPException(400, "Invalid version") from None
    if value < 1:
        raise HTTPException(400, "Invalid version")
    return value


@router.get("/artifacts", response_class=HTMLResponse)
async def artifacts_gallery(request: Request):
    user, redir = require_user(request)
    if redir:
        return redir
    items = artifacts.list_for_user(user["id"])
    active = [item for item in items if not item["archived"]]
    archived = [item for item in items if item["archived"]]
    error = (request.query_params.get("error") or "")[:200]
    notice = (request.query_params.get("notice") or "")[:200]
    return render(
        request,
        "artifacts.html",
        user,
        items=active,
        archived=archived,
        error=error,
        notice=notice,
    )


@router.get("/a/{public_id}", response_class=HTMLResponse)
async def artifact_page(request: Request, public_id: str):
    user = current_user(request)
    row = artifacts.get_for_view(public_id, user["id"] if user else None)
    if not row:
        return render(request, "artifact_missing.html", user, status_code=404)
    owner = artifacts.is_owner(row, user["id"] if user else None)
    version = _artifact_version(request.query_params.get("v"))
    try:
        item = artifacts.client_payload(row, owner=owner, version=version)
    except artifacts.ArtifactError as exc:
        raise _artifact_http_error(exc) from None
    return render(
        request,
        "artifact.html",
        user,
        item=item,
        owner=owner,
        public=row["visibility"] == "public",
    )


@router.get("/a/{public_id}/content")
async def artifact_content(request: Request, public_id: str):
    user = current_user(request)
    row = artifacts.get_for_view(public_id, user["id"] if user else None)
    if not row:
        raise HTTPException(404, "Artifact not found")
    version = _artifact_version(request.query_params.get("v"))
    try:
        item = artifacts.view(row, include_html=True, version=version)
    except artifacts.ArtifactError as exc:
        raise _artifact_http_error(exc) from None
    return artifacts.content_response(item["html"])


@router.get("/a/{public_id}/download")
async def artifact_download(request: Request, public_id: str):
    user, err = _chat_auth(request)
    if err:
        return err
    row = artifacts.get_owned(user["id"], public_id)
    if not row:
        raise HTTPException(404, "Artifact not found")
    version = _artifact_version(request.query_params.get("v"))
    try:
        item = artifacts.view(row, include_html=True, version=version)
    except artifacts.ArtifactError as exc:
        raise _artifact_http_error(exc) from None
    return artifacts.download_response(item["html"], item["title"])


@router.get("/a/{public_id}/meta")
async def artifact_meta(request: Request, public_id: str):
    user = current_user(request)
    row = artifacts.get_for_view(public_id, user["id"] if user else None)
    if not row:
        raise HTTPException(404, "Artifact not found")
    version = _artifact_version(request.query_params.get("v"))
    owner = artifacts.is_owner(row, user["id"] if user else None)
    try:
        item = artifacts.client_payload(row, owner=owner, version=version)
    except artifacts.ArtifactError as exc:
        raise _artifact_http_error(exc) from None
    payload = {
        "identifier": item["identifier"],
        "title": item["title"],
        "url": item["url"],
        "content_url": item["content_url"],
        "share_url": item["share_url"],
        "visibility": item["visibility"],
        "version": item["version"],
        "versions": item["versions"],
        "owner": owner,
    }
    if owner:
        payload["thread_id"] = item.get("thread_id")
    return JSONResponse(payload, headers={"Cache-Control": "no-store"})


async def _artifact_form_or_json(request: Request) -> dict:
    ctype = (request.headers.get("content-type") or "").lower()
    if "application/json" in ctype:
        return await _json_object(request)
    form = await request.form()
    return {str(key): form.get(key) for key in form.keys()}


@router.post("/a/{public_id}/visibility")
async def artifact_visibility(request: Request, public_id: str):
    user, err = _chat_auth(request)
    if err:
        return err
    raw = await _artifact_form_or_json(request)
    vis = raw.get("visibility")
    try:
        item = artifacts.set_visibility(user["id"], public_id, str(vis or ""))
    except artifacts.ArtifactError as exc:
        if "application/json" in (request.headers.get("content-type") or "").lower():
            raise _artifact_http_error(exc) from None
        return RedirectResponse(f"/a/{public_id}?error={quote_plus(exc.detail)}", status_code=303)
    if "application/json" in (request.headers.get("content-type") or "").lower():
        return JSONResponse({"ok": True, "visibility": item["visibility"], "url": item["url"]})
    return RedirectResponse(f"/a/{public_id}", status_code=303)


@router.post("/a/{public_id}/title")
async def artifact_title(request: Request, public_id: str):
    user, err = _chat_auth(request)
    if err:
        return err
    raw = await _artifact_form_or_json(request)
    try:
        item = artifacts.set_title(user["id"], public_id, str(raw.get("title") or ""))
    except artifacts.ArtifactError as exc:
        if "application/json" in (request.headers.get("content-type") or "").lower():
            raise _artifact_http_error(exc) from None
        return RedirectResponse(f"/a/{public_id}?error={quote_plus(exc.detail)}", status_code=303)
    if "application/json" in (request.headers.get("content-type") or "").lower():
        return JSONResponse({"ok": True, "title": item["title"], "url": item["url"]})
    return RedirectResponse(f"/a/{public_id}", status_code=303)


@router.post("/a/{public_id}/archive")
async def artifact_archive(request: Request, public_id: str):
    return await _artifact_set_archived(request, public_id, True)


@router.post("/a/{public_id}/restore")
async def artifact_restore(request: Request, public_id: str):
    return await _artifact_set_archived(request, public_id, False)


async def _artifact_set_archived(request: Request, public_id: str, archived: bool):
    user, err = _chat_auth(request)
    if err:
        return err
    json_request = "application/json" in (request.headers.get("content-type") or "").lower()
    try:
        artifacts.set_archived(user["id"], public_id, archived)
    except artifacts.ArtifactError as exc:
        if json_request:
            raise _artifact_http_error(exc) from None
        return RedirectResponse(f"/artifacts?error={quote_plus(exc.detail)}", status_code=303)
    if json_request:
        return JSONResponse({"ok": True, "archived": archived})
    notice = "Artifact archived." if archived else "Artifact restored."
    return RedirectResponse(f"/artifacts?notice={quote_plus(notice)}", status_code=303)


@router.post("/a/{public_id}/delete")
async def artifact_delete(request: Request, public_id: str):
    user, err = _chat_auth(request)
    if err:
        return err
    json_request = "application/json" in (request.headers.get("content-type") or "").lower()
    try:
        deleted = artifacts.delete(user["id"], public_id)
    except artifacts.ArtifactError as exc:
        if json_request:
            raise _artifact_http_error(exc) from None
        return RedirectResponse(f"/artifacts?error={quote_plus(exc.detail)}", status_code=303)
    if not deleted:
        if json_request:
            raise HTTPException(404, "Artifact not found")
        return RedirectResponse(f"/artifacts?error={quote_plus('Artifact not found')}", status_code=303)
    if json_request:
        return JSONResponse({"ok": True})
    return RedirectResponse(f"/artifacts?notice={quote_plus('Artifact deleted.')}", status_code=303)


@router.post("/a/{public_id}/continue")
async def artifact_continue(request: Request, public_id: str):
    user, err = _chat_auth(request)
    if err:
        return err
    raw = {}
    ctype = (request.headers.get("content-type") or "").lower()
    if "application/json" in ctype:
        raw = await _json_object(request)
    try:
        result = artifacts.continue_in_chat(user["id"], public_id, model=str(raw.get("model") or ""))
    except artifacts.ArtifactError as exc:
        if "application/json" in ctype:
            raise _artifact_http_error(exc) from None
        return RedirectResponse("/chat", status_code=303)
    except chats.ChatValidationError as exc:
        if "application/json" in ctype:
            raise _chat_validation_error(exc) from None
        return RedirectResponse("/chat", status_code=303)
    loc = f"/chat?t={result['thread_id']}&a={result['identifier']}"
    if "application/json" in ctype:
        return JSONResponse({"ok": True, **result, "chat_url": loc})
    return RedirectResponse(loc, status_code=303)

