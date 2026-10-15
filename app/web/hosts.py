from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
import base64
import re

from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from .. import chats, hosts
from ..security import limiter
from .deps import (
    _chat_auth,
    _host_fail,
    _host_http_error,
    _json_object,
    logger,
    render,
    require_user,
    vault_locked_json,
)

router = APIRouter()

@router.get("/hosts", response_class=HTMLResponse)
async def hosts_page(request: Request):
    user, redir = require_user(request)
    if redir:
        return redir
    return render(request, "hosts.html", user, hosts_bundle=hosts.list_bundle(user["id"]))


@router.get("/projects", response_class=HTMLResponse)
async def projects_page(request: Request):
    user, redir = require_user(request)
    if redir:
        return redir
    return render(request, "projects.html", user, projects_bundle=_projects_bundle(user["id"]))


def _projects_bundle(user_id: int) -> dict:
    bundle = hosts.list_bundle(user_id)
    return {
        "projects": chats.list_sidebar_projects(user_id),
        "hosts": [
            {"id": h["id"], "alias": h["alias"], "label": h.get("label") or "", "default_cwd": h.get("default_cwd") or "",
             "auth_type": h.get("auth_type") or "", "online": h.get("online")}
            for h in bundle["hosts"]
        ],
        "vault": bundle["vault"],
    }


@router.get("/projects/api")
async def projects_api(request: Request):
    user, err = _chat_auth(request)
    if err:
        return err
    return JSONResponse(_projects_bundle(user["id"]), headers={"Cache-Control": "no-store"})


@router.get("/projects/{project_id}/logo")
async def project_logo(request: Request, project_id: int):
    user, err = _chat_auth(request)
    if err:
        return err
    found = hosts.project_logo(user["id"], project_id)
    if not found:
        raise HTTPException(404, "no logo")
    mime, data = found
    # The URL carries ?v=<logo_rev>, so a cached copy is never stale.
    return Response(data, media_type=mime, headers={
        "Cache-Control": "private, max-age=31536000, immutable",
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "default-src 'none'",
    })


_DATA_URL = re.compile(r"^data:(image/(?:png|jpeg|webp));base64,([A-Za-z0-9+/=\s]+)$")


@router.put("/hosts/{host_id}/projects/{project_id}/logo")
async def hosts_project_logo_put(request: Request, host_id: int, project_id: int):
    user, err = _chat_auth(request)
    if err:
        return err
    raw = await _json_object(request)
    match = _DATA_URL.match(raw.get("data") or "") if isinstance(raw.get("data"), str) else None
    if not match or set(raw) - {"data"}:
        raise HTTPException(422, "data must be a PNG, JPEG or WebP data URL")
    try:
        blob = base64.b64decode(match.group(2), validate=False)
    except ValueError:
        raise HTTPException(422, "logo is not valid base64") from None
    if hosts.get_project(user["id"], host_id, project_id) is None:
        raise HTTPException(404, "project not found")
    try:
        item = hosts.set_project_logo(user["id"], project_id, match.group(1), blob)
    except hosts.HostError as exc:
        return _host_fail(exc)
    return JSONResponse(item)


@router.delete("/hosts/{host_id}/projects/{project_id}/logo")
async def hosts_project_logo_delete(request: Request, host_id: int, project_id: int):
    user, err = _chat_auth(request)
    if err:
        return err
    if hosts.get_project(user["id"], host_id, project_id) is None:
        raise HTTPException(404, "project not found")
    try:
        item = hosts.clear_project_logo(user["id"], project_id)
    except hosts.HostError as exc:
        return _host_fail(exc)
    return JSONResponse(item)


@router.get("/hosts/scheduled", response_class=HTMLResponse)
async def hosts_scheduled(request: Request):
    return RedirectResponse("/tasks", status_code=303)


@router.get("/projects/scheduled", response_class=HTMLResponse)
async def projects_scheduled(request: Request):
    return RedirectResponse("/tasks", status_code=303)


@router.get("/hosts/api")
async def hosts_api(request: Request):
    user, err = _chat_auth(request)
    if err:
        return err
    return JSONResponse(hosts.list_bundle(user["id"]), headers={"Cache-Control": "no-store"})


@router.post("/hosts")
async def hosts_create(request: Request):
    user, err = _chat_auth(request)
    if err:
        return err
    raw = await _json_object(request)
    if set(raw) - {"alias", "label", "hostname", "port", "username", "auth_type", "secret", "passphrase", "default_cwd"}:
        raise HTTPException(422, "Unsupported host fields")
    try:
        item = hosts.add_host(user["id"], raw)
    except hosts.HostError as exc:
        return _host_fail(exc)
    return JSONResponse(item, status_code=201)


@router.patch("/hosts/{host_id}")
async def hosts_patch(request: Request, host_id: int):
    user, err = _chat_auth(request)
    if err:
        return err
    raw = await _json_object(request)
    try:
        item = hosts.patch_host(user["id"], host_id, raw)
    except hosts.HostError as exc:
        return _host_fail(exc)
    if item.get("auth_type") == "agent" and item.get("disabled"):
        try:
            from .. import agent_hub

            await agent_hub.disconnect(user["id"], host_id, "disabled")
        except Exception:
            logger.exception("agent disconnect on host disable failed")
    return JSONResponse(item)


@router.delete("/hosts/{host_id}")
async def hosts_delete(request: Request, host_id: int):
    user, err = _chat_auth(request)
    if err:
        return err
    try:
        from .. import agent_hub
        await agent_hub.disconnect(user["id"], host_id, "disabled")
    except Exception:
        logger.exception("agent disconnect on host delete failed")
    if not hosts.delete_host(user["id"], host_id):
        raise HTTPException(404, "host not found")
    return JSONResponse({"ok": True})


@router.post("/hosts/{host_id}/projects")
async def hosts_project_create(request: Request, host_id: int):
    user, err = _chat_auth(request)
    if err:
        return err
    raw = await _json_object(request)
    if set(raw) - {"name", "remote_path", "notes", "default_model", "permission_mode", "default_permissions", "color"}:
        raise HTTPException(422, "Unsupported project fields")
    try:
        item = hosts.add_project(user["id"], host_id, raw)
    except hosts.HostError as exc:
        return _host_fail(exc)
    return JSONResponse(item, status_code=201)


@router.patch("/hosts/{host_id}/projects/{project_id}")
async def hosts_project_patch(request: Request, host_id: int, project_id: int):
    user, err = _chat_auth(request)
    if err:
        return err
    raw = await _json_object(request)
    try:
        item = hosts.patch_project(user["id"], host_id, project_id, raw)
    except hosts.HostError as exc:
        return _host_fail(exc)
    return JSONResponse(item)


@router.delete("/hosts/{host_id}/projects/{project_id}")
async def hosts_project_delete(request: Request, host_id: int, project_id: int):
    user, err = _chat_auth(request)
    if err:
        return err
    sessions = request.query_params.get("sessions") or "delete"
    try:
        found = hosts.delete_project(user["id"], host_id, project_id, sessions=sessions)
    except hosts.HostError as exc:
        return _host_fail(exc)
    if not found:
        raise HTTPException(404, "project not found")
    return JSONResponse({"ok": True})


@router.post("/hosts/pairings")
async def hosts_pairing_create(request: Request):
    user, err = _chat_auth(request)
    if err:
        return err
    if not limiter.allow(f"host-pair:{user['id']}", 6, 60):
        raise HTTPException(429, "Too many install codes")
    raw = await _json_object(request)
    try:
        item = hosts.mint_pairing(user["id"], raw.get("alias") or raw.get("alias_hint") or "")
    except hosts.HostError as exc:
        return _host_fail(exc)
    return JSONResponse(item, status_code=201)


@router.get("/hosts/pairings")
async def hosts_pairing_list(request: Request):
    user, err = _chat_auth(request)
    if err:
        return err
    return JSONResponse({"pending": hosts.list_pending_pairings(user["id"])}, headers={"Cache-Control": "no-store"})


@router.delete("/hosts/pairings/{pairing_id}")
async def hosts_pairing_delete(request: Request, pairing_id: int):
    user, err = _chat_auth(request)
    if err:
        return err
    if not hosts.cancel_pairing(user["id"], pairing_id):
        raise HTTPException(404, "code not found")
    return JSONResponse({"ok": True})


@router.post("/hosts/{host_id}/probe")
async def hosts_probe(request: Request, host_id: int):
    user, err = _chat_auth(request)
    if err:
        return err
    if not limiter.allow(f"ssh-probe:{user['id']}", 6, 60):
        raise HTTPException(429, "Too many probe attempts")
    row = hosts.get_host(user["id"], host_id)
    if row and row.get("auth_type") == "agent":
        return _host_fail(hosts.HostError("connector hosts are not SSH"))
    from .. import ssh_pool
    try:
        data = await ssh_pool.probe(user["id"], host_id)
    except hosts.HostError as exc:
        return _host_fail(exc)
    return JSONResponse(data)


@router.post("/hosts/{host_id}/trust")
async def hosts_trust(request: Request, host_id: int):
    user, err = _chat_auth(request)
    if err:
        return err
    raw = await _json_object(request)
    from .. import ssh_pool
    try:
        item = await ssh_pool.trust(user["id"], host_id, raw.get("algorithm") or "", raw.get("fingerprint") or "")
    except hosts.HostError as exc:
        return _host_fail(exc)
    return JSONResponse(item)


@router.post("/hosts/{host_id}/browse")
async def hosts_browse(request: Request, host_id: int):
    user, err = _chat_auth(request)
    if err:
        return err
    if not limiter.allow(f"host-browse:{user['id']}", 30, 60):
        raise HTTPException(429, "Too many folder listings")
    raw = await _json_object(request)
    path = raw.get("path") if isinstance(raw.get("path"), str) else ""
    from .. import workspace
    try:
        data = await workspace.browse(user["id"], host_id, path)
    except workspace.WorkspaceError as exc:
        return _host_fail(exc)
    except hosts.HostError as exc:
        return _host_fail(exc)
    return JSONResponse(data)


@router.post("/hosts/{host_id}/test")
async def hosts_test(request: Request, host_id: int):
    user, err = _chat_auth(request)
    if err:
        return err
    if not limiter.allow(f"ssh-test:{user['id']}", 6, 60):
        raise HTTPException(429, "Too many test attempts")
    row = hosts.get_host(user["id"], host_id)
    if row and row.get("auth_type") == "agent":
        from .. import agent_hub
        try:
            data = await agent_hub.call(user["id"], host_id, "exec", {"cwd": "~", "cmd": "echo ok", "timeout_ms": 10000}, 12)
        except hosts.HostError as exc:
            hosts.set_host_status(user["id"], host_id, ok=False, error=exc.detail)
            return _host_fail(exc)
        if "ok" not in (data.get("out") or "").lower() and not data.get("ok"):
            hosts.set_host_status(user["id"], host_id, ok=False, error="connector test failed")
            return _host_fail(hosts.HostError("connector test failed"))
        hosts.set_host_status(user["id"], host_id, ok=True)
        live = hosts.get_host(user["id"], host_id) or row
        return JSONResponse({"ok": True, "online": True, "agent_version": live.get("agent_version") or "", "host": live})
    from .. import ssh_pool
    try:
        data = await ssh_pool.test(user["id"], host_id)
    except hosts.HostError as exc:
        return _host_fail(exc)
    return JSONResponse(data)


@router.post("/hosts/settings")
async def hosts_settings(request: Request):
    user, err = _chat_auth(request)
    if err:
        return err
    raw = await _json_object(request)
    if set(raw) - {"agent_coding", "permission_mode", "allow_nested_spawn", "default_chat_permissions", "default_project_permissions"}:
        raise HTTPException(422, "Unsupported settings fields")
    try:
        data = hosts.set_settings(
            user["id"],
            agent_coding=raw.get("agent_coding"),
            allow_nested_spawn=raw.get("allow_nested_spawn"),
            permission_mode=raw.get("permission_mode"),
            default_chat_permissions=raw.get("default_chat_permissions"),
            default_project_permissions=raw.get("default_project_permissions"),
        )
    except hosts.HostError as exc:
        return _host_fail(exc)
    return JSONResponse(data)


