from __future__ import annotations

from urllib.parse import quote_plus

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from .. import artifacts, chats
from .deps import _chat_auth, _chat_validation_error, _json_object, current_user, render, require_user

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


def _viewer(request: Request, ref: str):
    user = current_user(request)
    row, owner = artifacts.resolve(ref, user["id"] if user else None)
    return user, row, owner


def _owned(user, ref: str):
    row = artifacts.get_owned(user["id"], ref)
    if not row:
        raise HTTPException(404, "Artifact not found")
    return row


def _wants_json(request: Request) -> bool:
    return "application/json" in (request.headers.get("content-type") or "").lower()


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
        limit=artifacts.MAX_PER_USER,
        error=error,
        notice=notice,
    )


@router.get("/artifacts/list")
async def artifacts_list(request: Request):
    """The chat's /artifacts list: this chat's artifacts first, then the rest."""
    user, err = _chat_auth(request)
    if err:
        return err
    thread = (request.query_params.get("thread") or "").strip()
    here = artifacts.thread_artifact_ids(user["id"], thread) if thread else set()
    items = [
        {
            "identifier": item["identifier"],
            "title": item["title"],
            "icon": item["icon"],
            "visibility": item["visibility"],
            "version": item["version"],
            "updated_at": item["updated_at"],
            "url": item["url"],
            "share_url": item["share_url"],
            "in_thread": item["identifier"] in here,
        }
        for item in artifacts.list_for_user(user["id"])
        if not item["archived"]
    ]
    items.sort(key=lambda item: not item["in_thread"])
    return JSONResponse({"items": items}, headers={"Cache-Control": "no-store"})


@router.get("/a/{public_id}", response_class=HTMLResponse)
async def artifact_page(request: Request, public_id: str):
    user, row, owner = _viewer(request, public_id)
    if not row:
        return render(request, "artifact_missing.html", user, status_code=404)
    version = _artifact_version(request.query_params.get("v")) if owner else None
    try:
        item = artifacts.client_payload(row, owner=owner, version=version)
    except artifacts.ArtifactError as exc:
        raise _artifact_http_error(exc) from None
    if version:
        item["content_url"] += f"?v={version}"
    error = (request.query_params.get("error") or "")[:200]
    return render(
        request,
        "artifact.html",
        user,
        item=item,
        owner=owner,
        pinned=version,
        public=row["visibility"] == "public",
        sandbox=artifacts.SANDBOX,
        error=error,
    )


@router.get("/a/{public_id}/content")
async def artifact_content(request: Request, public_id: str):
    version = _artifact_version(request.query_params.get("v"))
    if (request.headers.get("sec-fetch-dest") or "").lower() == "document":
        # Opened as a page of its own: show it inside the viewer, under its
        # banner, never as a bare model-written page on the Fuse origin.
        suffix = f"?v={version}" if version else ""
        return RedirectResponse(f"/a/{quote_plus(public_id)}{suffix}", status_code=303)
    _, row, owner = _viewer(request, public_id)
    if not row:
        raise HTTPException(404, "Artifact not found")
    try:
        item = artifacts.client_payload(row, owner=owner, include_html=True, version=version if owner else None)
    except artifacts.ArtifactError as exc:
        raise _artifact_http_error(exc) from None
    return artifacts.content_response(item["html"], item.get("format") or "html", item["title"])


@router.get("/a/{public_id}/source")
async def artifact_source(request: Request, public_id: str):
    user, err = _chat_auth(request)
    if err:
        return err
    row = _owned(user, public_id)
    try:
        text, _ = artifacts.source_of(row, _artifact_version(request.query_params.get("v")))
    except artifacts.ArtifactError as exc:
        raise _artifact_http_error(exc) from None
    return artifacts.source_response(text)


@router.get("/a/{public_id}/diff")
async def artifact_diff(request: Request, public_id: str):
    user, err = _chat_auth(request)
    if err:
        return err
    row = _owned(user, public_id)
    old = _artifact_version(request.query_params.get("from"))
    new = _artifact_version(request.query_params.get("to")) or int(row["current_version"])
    if old is None:
        raise HTTPException(400, "Invalid version")
    try:
        text = artifacts.diff(row, old, new)
    except artifacts.ArtifactError as exc:
        raise _artifact_http_error(exc) from None
    return artifacts.source_response(text)


@router.get("/a/{public_id}/download")
async def artifact_download(request: Request, public_id: str):
    user, err = _chat_auth(request)
    if err:
        return err
    row = _owned(user, public_id)
    try:
        text, fmt = artifacts.source_of(row, _artifact_version(request.query_params.get("v")))
    except artifacts.ArtifactError as exc:
        raise _artifact_http_error(exc) from None
    return artifacts.download_response(text, row["title"], fmt)


@router.get("/a/{public_id}/meta")
async def artifact_meta(request: Request, public_id: str):
    _, row, owner = _viewer(request, public_id)
    if not row:
        raise HTTPException(404, "Artifact not found")
    # Open views poll this to update in place; the tag spares them the body.
    tag = f'W/"{row["current_version"]}-{row["shared_version"] or 0}-{row["updated_at"]}-{int(owner)}"'
    if request.headers.get("if-none-match") == tag:
        return Response(status_code=304, headers={"ETag": tag, "Cache-Control": "no-cache"})
    try:
        item = artifacts.client_payload(row, owner=owner)
    except artifacts.ArtifactError as exc:
        raise _artifact_http_error(exc) from None
    payload = {
        "identifier": item["identifier"],
        "title": item["title"],
        "icon": item.get("icon") or "",
        "url": item["url"],
        "content_url": item["content_url"],
        "share_url": item["share_url"],
        "visibility": item["visibility"],
        "version": item["version"],
        "versions": item["versions"],
        "owner": owner,
    }
    if owner:
        payload.update(
            thread_id=item.get("thread_id"),
            history=item.get("history") or [],
            shared_version=item.get("shared_version"),
            share_reset=item.get("share_reset", False),
            updated_at=item.get("updated_at"),
        )
    return JSONResponse(payload, headers={"Cache-Control": "no-cache", "ETag": tag})


async def _artifact_form_or_json(request: Request) -> dict:
    if _wants_json(request):
        return await _json_object(request)
    form = await request.form()
    return {str(key): form.get(key) for key in form.keys()}


async def _owner_action(request: Request, public_id: str, act, result):
    """Run act(user, raw) for the owner: JSON in, JSON out; a form posts back
    to the artifact's page."""
    user, err = _chat_auth(request)
    if err:
        return err
    raw = await _artifact_form_or_json(request)
    try:
        item = act(user, raw)
    except artifacts.ArtifactError as exc:
        if _wants_json(request):
            raise _artifact_http_error(exc) from None
        return RedirectResponse(f"/a/{public_id}?error={quote_plus(exc.detail)}", status_code=303)
    if _wants_json(request):
        return JSONResponse({"ok": True, **result(item)})
    back = str(raw.get("next") or "")
    return RedirectResponse(back if back in ("/artifacts",) else f"/a/{public_id}", status_code=303)


def _summary(item: dict) -> dict:
    return {
        "identifier": item["identifier"],
        "title": item["title"],
        "visibility": item["visibility"],
        "version": item["version"],
        "shared_version": item.get("shared_version"),
        "url": item["url"],
        "share_url": item["share_url"],
        "history": item.get("history") or [],
    }


@router.post("/a/{public_id}/visibility")
async def artifact_visibility(request: Request, public_id: str):
    return await _owner_action(
        request, public_id,
        lambda user, raw: artifacts.set_visibility(user["id"], public_id, str(raw.get("visibility") or "")),
        _summary,
    )


@router.post("/a/{public_id}/title")
async def artifact_title(request: Request, public_id: str):
    return await _owner_action(
        request, public_id,
        lambda user, raw: artifacts.set_title(user["id"], public_id, str(raw.get("title") or "")),
        _summary,
    )


@router.post("/a/{public_id}/pin")
async def artifact_pin(request: Request, public_id: str):
    def act(user, raw):
        value = raw.get("version")
        version = None if value in (None, "", "latest") else _artifact_version(value)
        return artifacts.set_shared_version(user["id"], public_id, version)

    return await _owner_action(request, public_id, act, _summary)


@router.post("/a/{public_id}/rotate-link")
async def artifact_rotate(request: Request, public_id: str):
    return await _owner_action(
        request, public_id, lambda user, raw: artifacts.rotate_share(user["id"], public_id), _summary,
    )


@router.post("/a/{public_id}/revert")
async def artifact_revert(request: Request, public_id: str):
    def act(user, raw):
        version = _artifact_version(raw.get("version"))
        if version is None:
            raise artifacts.ArtifactError("version is required")
        return artifacts.revert(user["id"], public_id, version)

    return await _owner_action(request, public_id, act, _summary)


@router.post("/a/{public_id}/attach")
async def artifact_attach(request: Request, public_id: str):
    user, err = _chat_auth(request)
    if err:
        return err
    raw = await _json_object(request)
    item = artifacts.attach_thread(user["id"], public_id, raw.get("thread_id"))
    if item is None:
        raise HTTPException(404, "Artifact or chat not found")
    return JSONResponse({"ok": True, **_summary(item)})


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
    json_request = _wants_json(request)
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
    json_request = _wants_json(request)
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
