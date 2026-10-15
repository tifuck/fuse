from __future__ import annotations

import json
import logging
import asyncio

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse

from .. import artifacts, chat_events, chat_runtime, chat_runs, chat_recaps, chat_store, chat_titles, chat_tools, chats, context_compact as ctxmod, db, hosts, model_caps, permissions as permissions_mod, skills, vault, context_meter
from ..config import settings
from ..security import limiter
from ..sources import openrouter as openrouter_mod
from .deps import (
    _chat_auth,
    _chat_validation_error,
    _json_object,
    provider_logo_url,
    render,
    require_user,
)
from .models import _models_page_families, _reasoning_default, _reasoning_efforts

router = APIRouter()
logger = logging.getLogger(__name__)

@router.get("/chat", response_class=HTMLResponse)
async def chat_page(request: Request):
    user, redir = require_user(request)
    if redir:
        return redir
    available, locked = await _models_page_families(user)
    agent_settings = hosts.settings_for(user["id"])
    families = [
        {
            "type": group["type"],
            "title": group["title"],
            "logo": provider_logo_url(group["type"]),
            "models": [
                {
                    "id": m["id"],
                    "name": m.get("name") or m["id"],
                    "reasoning": list(_reasoning_efforts(group["type"], m["id"], user_id=user["id"])),
                    "reasoning_default": _reasoning_default(group["type"], m["id"], user_id=user["id"]),
                    "context_window": m.get("context_window") or context_meter.window_for(m["id"], group["type"]),
                    "image": model_caps.image_input(group["type"], m["id"], user_id=user["id"]),
                }
                for m in group["models"]
            ],
        }
        for group in available
    ]
    return render(
        request,
        "chat.html",
        user,
        skills_bundle=skills.list_bundle(user["id"]),
        chat_config={
            "families": families,
            "locked": [group["title"] for group in locked],
            "preselect": (request.query_params.get("model") or "").strip()[:200],
            "tools": chat_tools.AVAILABLE,
            "context": chats.get_user_context(user["id"]),
            "vault": "unlocked" if vault.unlocked(user["id"]) else "locked",
            "uid": user["id"],
            "ssh_hosts": hosts.has_ssh_hosts(user["id"]),
            "limits": {
                "context": chats.CONTEXT_MAX,
                "attachments": chats.ATTACH_MAX,
                "attachment_chars": chats.ATTACH_DATA_MAX,
            },
            "compaction": context_meter.public_config(),
            "skills": skills.index_for_chat(user["id"]),
            "coding": bool(agent_settings.get("agent_coding")),
            "openrouter": bool(openrouter_mod.user_has_openrouter(user["id"])),
            "permission_defaults": {
                "chat": agent_settings["default_chat_permissions"],
                "project": agent_settings["default_project_permissions"],
            },
        },
    )



@router.get("/chat/sync")
async def chat_sync_snapshot(request: Request):
    user, err = _chat_auth(request)
    if err:
        return err

    def _snapshot():
        with db.tx() as conn:
            conn.execute("BEGIN")
            rows = conn.execute(
                f"""SELECT {chats._THREAD_LIST_COLS}
                   FROM chat_threads t
                   LEFT JOIN user_hosts h ON h.id=t.host_id
                   LEFT JOIN user_projects p ON p.id=t.project_id
                   LEFT JOIN chat_schedules s ON s.id=t.schedule_id
                   WHERE t.user_id=? ORDER BY t.updated_at DESC, t.id DESC""",
                (user["id"],),
            ).fetchall()
            account = conn.execute("SELECT * FROM users WHERE id=?", (user["id"],)).fetchone()
            high = conn.execute("SELECT cursor FROM chat_event_cursors WHERE user_id=?", (user["id"],)).fetchone()
            live_agents = conn.execute(
                """SELECT COUNT(*) FROM chat_runs
                   WHERE user_id=? AND parent_run_id IS NOT NULL AND status IN ('queued','running')""",
                (user["id"],),
            ).fetchone()[0]
            data = {
                "cursor": int(high[0]) if high else 0,
                "threads": [chats._view(row) for row in rows],
                "context": chats._context_view(account),
                "live_agents": int(live_agents),
            }
        data["projects"] = chats.list_sidebar_projects(user["id"])
        return data

    data = await asyncio.to_thread(_snapshot)
    await asyncio.to_thread(chat_runs.reap_orphans, user["id"])
    await chat_runs.resume_queued(user["id"])
    return JSONResponse(data, headers={"Cache-Control": "no-store"})


def _chat_revision(raw):
    revision = raw.get("expected_revision")
    if type(revision) is not int or revision < 0:
        raise HTTPException(409, "This page is out of date. Refresh before changing the chat.")
    return revision


def _chat_command(raw):
    command = raw.get("command_id")
    if not isinstance(command, str) or not command.strip() or len(command) > 200:
        raise HTTPException(422, "A command_id is required for safe retries.")
    return command


def _chat_public_run(run):
    if not run:
        return None
    thread_id = run.get("thread_id")
    public = chats.public_id_for(thread_id) if thread_id is not None else None
    return {
        "id": run["id"],
        "thread_id": public or thread_id,
        "message_id": run["message_id"],
        "status": run["status"],
        "created_at": run["created_at"],
        "updated_at": run.get("updated_at"),
        "cancel_requested": run["cancel_requested"],
        "resume_count": int(run.get("resume_count") or 0),
        "activity": ((run.get("request") or {}).get("agent") or {}).get("activity"),
    }


def _chat_thread_pk(user, thread_id):
    pk = chats.resolve_thread_pk(user["id"], thread_id)
    if pk is None:
        raise HTTPException(404, "Chat not found")
    return pk


def _chat_public_thread(thread):
    return {**thread, "run": _chat_public_run(thread.get("run"))}


@router.post("/chat/threads/{thread_id}/turns")
async def chat_turn(request: Request, thread_id: str):
    user, err = _chat_auth(request)
    if err:
        return err
    if not limiter.allow(f"api-user:{user['id']}", settings.api_rate_per_minute, 60):
        raise HTTPException(429, "Too many requests")
    raw = await _json_object(request)
    if set(raw) - {
        "text",
        "expected_revision",
        "command_id",
        "model",
        "tools",
        "context",
        "attachments",
        "reasoning_effort",
        "invoke_skill",
        "replace_from",
    }:
        raise HTTPException(422, "Unsupported turn fields")
    pk = await asyncio.to_thread(_chat_thread_pk, user, thread_id)
    try:
        result = await asyncio.to_thread(
            chat_store.start_turn,
            user["id"],
            pk,
            raw.get("text"),
            _chat_revision(raw),
            _chat_command(raw),
            model=raw.get("model"),
            tools=raw.get("tools"),
            context=raw.get("context"),
            attachments=raw.get("attachments"),
            reasoning_effort=raw.get("reasoning_effort"),
            auto_title=True,
            invoke_skill=raw.get("invoke_skill"),
            replace_from=raw.get("replace_from"),
        )
    except chats.ChatValidationError as exc:
        raise _chat_validation_error(exc) from None
    if result.get("thread") is None:
        raise HTTPException(404, "Chat not found")
    if result["run"]:
        await chat_runs.start(request, user, result["run"])
        # Claude Code names a session from its first prompt while the turn
        # runs; the sweep skips threads that already have a title.
        chat_titles.notify(pk)
    return JSONResponse({**result, "thread": _chat_public_thread(result["thread"]) if result["thread"] else None,
                         "run": _chat_public_run(result["run"])}, headers={"Cache-Control": "no-store"})


@router.get("/chat/runs/{run_id}/images/{event_id}")
async def chat_run_image(request: Request, run_id: str, event_id: str):
    user, err = _chat_auth(request)
    if err:
        return err
    image = await asyncio.to_thread(chat_store.read_file_image, user["id"], run_id, event_id)
    if image is None:
        raise HTTPException(404, "Image unavailable")
    mime, pixels = image
    return Response(pixels, media_type=mime, headers={
        "Cache-Control": "private, no-store",
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "default-src 'none'; sandbox",
        "Cross-Origin-Resource-Policy": "same-origin",
    })


@router.get("/chat/runs/{run_id}")
async def chat_run_get(request: Request, run_id: str):
    user, err = _chat_auth(request)
    if err:
        return err
    run = await asyncio.to_thread(chat_store.get_run_snapshot, user["id"], run_id)
    if not run:
        raise HTTPException(404, "Run not found")
    req = run.get("request") if isinstance(run.get("request"), dict) else {}
    agent = req.get("agent") if isinstance(req.get("agent"), dict) else {}
    parent_description = ""
    if run.get("parent_run_id"):
        parent = chat_store.get_run(user["id"], run["parent_run_id"])
        preq = (parent or {}).get("request") if isinstance((parent or {}).get("request"), dict) else {}
        pagent = preq.get("agent") if isinstance(preq.get("agent"), dict) else {}
        parent_description = str(pagent.get("description") or "")[:200]
    seed = []
    meter = None
    if run.get("parent_run_id"):
        seed = run["display_messages"]
        stored = agent.get("meter") if isinstance(agent.get("meter"), dict) else None
        same_model = stored and stored.get("model") == (req.get("model") or "") and stored.get("limit") and stored.get("used") is not None
        meter = stored if same_model else chat_store.child_context_meter(run)
    return JSONResponse(
        {
            "id": run["id"],
            "thread_id": chats.public_id_for(run["thread_id"]) or run["thread_id"],
            "message_id": run["message_id"],
            "event_cursor": run["event_cursor"],
            "status": run["status"],
            "parent_run_id": run.get("parent_run_id"),
            "root_run_id": run.get("root_run_id"),
            "agent_depth": run.get("agent_depth") or 0,
            "message": run.get("message"),
            "created_at": run["created_at"],
            "updated_at": run["updated_at"],
            "cancel_requested": run["cancel_requested"],
            "cancel_requested_at": str(run.get("cancel_requested_at") or ""),
            "resume_count": int(run.get("resume_count") or 0),
            "has_transcript": bool(run.get("provider_messages")),
            "resumable": bool(
                run["status"] in ("interrupted", "cancelled")
                and run.get("provider_messages")
                and int(run.get("resume_count") or 0) < chat_store.MAX_RESUME_ATTEMPTS
                and not (
                    not run.get("parent_run_id")
                    and db.fetchone(
                        "SELECT 1 FROM chat_runs WHERE thread_id=? AND status IN ('queued','running') AND parent_run_id IS NULL",
                        (run["thread_id"],),
                    )
                )
            ),
            "model": req.get("model") or "",
            "description": str(agent.get("description") or "")[:200],
            "subagent_type": str(agent.get("subagent_type") or ""),
            "activity": agent.get("activity"),
            "parent_description": parent_description,
            "messages": seed,
            "live_start": run.get("live_start"),
            "compaction": {"meter": meter} if meter else None,
            **chat_store.run_work_fields(run["id"], agent, run.get("message")),
        },
        headers={"Cache-Control": "no-store"},
    )


@router.post("/chat/runs/{run_id}/cancel")
async def chat_cancel(request: Request, run_id: str):
    user, err = _chat_auth(request)
    if err:
        return err
    run = chat_store.get_run(user["id"], run_id)
    if not run:
        raise HTTPException(404, "Run not found")
    if run["status"] in chat_store.ACTIVE:
        scope = (request.query_params.get("scope") or "").strip().lower()
        force = (request.query_params.get("force") or "").strip().lower() in ("1", "true", "yes")
        # Child runs default to self: stopping one subagent must not kill the
        # parent turn and its siblings. Root runs keep the tree default.
        tree = scope == "tree" if run.get("parent_run_id") else scope != "self"
        await chat_runs.cancel_wait(user["id"], run_id, tree=tree, force=force, by_user=True)
        run = chat_store.get_run(user["id"], run_id) or run
    return JSONResponse({"ok": True, "run": _chat_public_run(run)}, headers={"Cache-Control": "no-store"})


@router.post("/chat/runs/{run_id}/resume")
async def chat_run_resume(request: Request, run_id: str):
    """User-initiated resume of an interrupted/stopped run from its transcript."""
    user, err = _chat_auth(request)
    if err:
        return err
    try:
        run = chat_store.resume_run(user["id"], run_id)
    except chats.ChatValidationError as exc:
        raise _chat_validation_error(exc) from None
    if run and run["status"] == "queued":
        try:
            await chat_runs.start(chat_runs.internal_request(user["id"]), dict(user), run)
        except Exception:
            logger.exception("Resume start failed for %s", run_id)
        run = chat_store.get_run(user["id"], run_id) or run
    return JSONResponse({"ok": True, "run": _chat_public_run(run)}, headers={"Cache-Control": "no-store"})


@router.post("/chat/threads/{thread_id}/steer")
async def chat_steer(request: Request, thread_id: str):
    """Deliver a user message into a live turn at its next tool boundary.

    The message is a real thread message (visible bubble) and is queued on the
    active root run for mid-turn injection; command_id keeps retries idempotent.
    """
    user, err = _chat_auth(request)
    if err:
        return err
    if not limiter.allow(f"api-user:{user['id']}", settings.api_rate_per_minute, 60):
        raise HTTPException(429, "Too many requests")
    raw = await _json_object(request)
    if set(raw) - {"text", "command_id", "attachments"}:
        raise HTTPException(422, "Unsupported steer fields")
    text = raw.get("text")
    if text is not None and not isinstance(text, str):
        raise HTTPException(422, "text cannot be empty")
    pk = _chat_thread_pk(user, thread_id)
    try:
        result = chat_store.steer_turn(user["id"], pk, text, _chat_command(raw), attachments=raw.get("attachments"))
    except chats.ChatValidationError as exc:
        raise _chat_validation_error(exc) from None
    active = result["thread"].get("run")
    if active and active["status"] == "queued":
        await chat_runs.start(request, user, active)
    thread = _chat_public_thread(result["thread"])
    return JSONResponse(
        {
            "ok": True,
            "live": bool(result["thread"].get("run")),
            "injected": True,
            "idempotent": bool(result.get("idempotent")),
            "revision": thread["revision"],
            "run": thread["run"],
            "thread": thread,
        },
        headers={"Cache-Control": "no-store"},
    )


@router.get("/chat/threads")
async def chat_threads(request: Request):
    user, err = _chat_auth(request)
    if err:
        return err
    q = (request.query_params.get("q") or "").strip()

    def build():
        if q:
            return {
                "threads": chats.search_threads(user["id"], q, archived=False),
                "archived": chats.search_threads(user["id"], q, archived=True),
                "projects": chats.search_projects(user["id"], q),
            }
        return {
            "threads": chats.list_threads(user["id"], archived=False),
            "archived": chats.list_threads(user["id"], archived=True),
            "projects": chats.list_sidebar_projects(user["id"]),
        }

    # Full lists for a large account take real time; keep them off the event
    # loop that every live stream and socket shares.
    return JSONResponse(await asyncio.to_thread(build), headers={"Cache-Control": "no-store"})


@router.post("/chat/threads")
async def chat_thread_create(request: Request):
    user, err = _chat_auth(request)
    if err:
        return err
    raw = await _json_object(request)
    unknown = set(raw) - {"model", "messages", "title", "context", "tools", "command_id", "kind", "host_id", "project_id",
                          "bypass_permissions", "auto_permissions"}
    if unknown:
        raise HTTPException(422, "Unsupported chat fields")
    for key in ("bypass_permissions", "auto_permissions"):
        if key in raw and not isinstance(raw[key], bool):
            raise HTTPException(422, f"{key} must be true or false")
    if "messages" in raw and not isinstance(raw["messages"], list):
        raise HTTPException(422, "messages must be an array")
    if "tools" in raw:
        try:
            raw["tools"] = chats.parse_thread_tools(raw["tools"], strict=True)
        except chats.ChatValidationError as exc:
            raise _chat_validation_error(exc) from None
    try:
        thread = chats.create_thread(
            user["id"],
            model=raw.get("model", ""),
            messages=raw.get("messages", []),
            title=raw.get("title", ""),
            context=raw.get("context", ""),
            tools=raw.get("tools") if "tools" in raw else None,
            command_id=_chat_command(raw),
            kind=raw.get("kind"),
            host_id=raw.get("host_id"),
            project_id=raw.get("project_id"),
            bypass_permissions=raw.get("bypass_permissions"),
            auto_permissions=raw.get("auto_permissions"),
        )
    except chats.ChatValidationError as exc:
        raise _chat_validation_error(exc) from None
    return JSONResponse(_chat_public_thread(thread), status_code=201)


@router.post("/chat/threads/pin-order")
async def chat_threads_pin_order(request: Request):
    user, err = _chat_auth(request)
    if err:
        return err
    raw = await _json_object(request)
    if set(raw) - {"ids", "command_id"}:
        raise HTTPException(422, "Unsupported pin-order fields")
    ids = raw.get("ids")
    if not isinstance(ids, list) or not ids or len(ids) > 500 or not all(isinstance(item, str) for item in ids):
        raise HTTPException(422, "ids must be a non-empty list of chat ids")
    data = await asyncio.to_thread(chats.set_pin_order, user["id"], ids, command_id=_chat_command(raw))
    return JSONResponse(data, headers={"Cache-Control": "no-store"})


@router.get("/chat/threads/{thread_id}")
async def chat_thread_get(request: Request, thread_id: str):
    user, err = _chat_auth(request)
    if err:
        return err

    def _cursor_param(name):
        raw = request.query_params.get(name)
        try:
            value = int(raw) if raw is not None else None
            if value is not None and not 0 <= value <= 2**53 - 1:
                raise ValueError
            return value
        except ValueError:
            raise HTTPException(422, "Invalid event cursor") from None

    observed = _cursor_param("observed_cursor")
    observed_thread = _cursor_param("observed_thread_cursor")
    # Command ids the client still holds in its local send queue.
    queued = [item for item in (request.query_params.get("queued") or "").split(",") if item][:128]
    thread = await asyncio.to_thread(
        chats.get_thread, user["id"], thread_id,
        observed_cursor=observed, observed_thread_cursor=observed_thread, queued=queued,
    )
    if not thread:
        raise HTTPException(404, "Chat not found")
    if thread.get("unchanged"):
        return JSONResponse(thread, headers={"Cache-Control": "no-store"})
    # Opening a thread is what clears its finished-run badge. Only full views
    # reach here: an unchanged short-circuit means the finishing revision was
    # already delivered, so the flag was cleared by that earlier view.
    if thread.get("unread"):
        await asyncio.to_thread(chats.mark_thread_read, user["id"], thread_id)
        thread["unread"] = False
    public = _chat_public_thread(thread)
    if request.query_params.get("pack") == "1":
        public["messages"] = await asyncio.to_thread(chats.pack_segment_text, public.get("messages"))
        if isinstance(public.get("context_messages"), list):
            public["context_messages"] = await asyncio.to_thread(chats.pack_segment_text, public["context_messages"])
        public["segments_packed"] = True
    if request.query_params.get("stream") != "1":
        return JSONResponse(public, headers={"Cache-Control": "no-store"})
    return _thread_stream(public)


# Messages in the first line of a streamed thread snapshot. Enough to fill the
# viewport and the fully rendered tail; older turns follow in the second line.
THREAD_TAIL = 16


def _json_line(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8") + b"\n"


def _thread_stream(public):
    """NDJSON thread snapshot: a partial line with the last messages, then the
    complete snapshot. Both lines come from one build and share one revision
    and cursor, so the client can paint the tail while the rest downloads."""
    headers = {"Cache-Control": "no-store"}
    messages = public.get("messages") or []
    if len(messages) <= THREAD_TAIL:
        return Response(_json_line(public), media_type="application/x-ndjson", headers=headers)
    head = {**public, "messages": messages[-THREAD_TAIL:], "partial": True, "message_count": len(messages)}
    if isinstance(public.get("context_messages"), list):
        head["context_messages"] = public["context_messages"][-THREAD_TAIL:]

    async def body():
        yield _json_line(head)
        # Serializing a multi-megabyte transcript must not stall the loop.
        yield await asyncio.to_thread(_json_line, public)

    return StreamingResponse(body(), media_type="application/x-ndjson", headers=headers)


@router.get("/chat/threads/{thread_id}/project-agents")
async def chat_project_agents(request: Request, thread_id: str):
    user, err = _chat_auth(request)
    if err:
        return err
    pk = await asyncio.to_thread(_chat_thread_pk, user, thread_id)
    # Only the binding columns matter here; a full thread view would parse the
    # whole transcript, which must stay off the event loop.
    thread = await asyncio.to_thread(
        db.fetchone,
        "SELECT kind, host_id, project_id FROM chat_threads WHERE id=? AND user_id=?",
        (pk, user["id"]),
    )
    if thread is None:
        raise HTTPException(404, "Chat not found")
    if (thread["kind"] or "chat") != "project" or thread["host_id"] is None:
        return JSONResponse({"applicable": False}, headers={"Cache-Control": "no-store"})
    from .. import workspace

    host_id = int(thread["host_id"])
    cwd = ""
    if thread["project_id"] is not None:
        proj = hosts.resolve_project_id(user["id"], int(thread["project_id"]))
        if proj:
            cwd = proj.get("remote_path") or ""
    if not cwd:
        host_row = hosts.get_host(user["id"], host_id)
        cwd = (host_row or {}).get("default_cwd") or ""
    report: dict = {}
    try:
        name, body = await asyncio.wait_for(
            workspace.load_project_agents(user["id"], host_id, cwd, report=report),
            timeout=4.0,
        )
    except Exception:
        report = {"reason": "host"}
        name, body = "", ""
    reason = report.get("reason") or "missing"
    return JSONResponse(
        {
            "applicable": True,
            "state": "loaded" if body else ("missing" if reason in ("missing",) else "error"),
            "name": name or None,
            "reason": reason,
            "chars": len(body or ""),
        },
        headers={"Cache-Control": "no-store"},
    )


@router.get("/chat/threads/{thread_id}/agents")
async def chat_thread_agents(request: Request, thread_id: str):
    user, err = _chat_auth(request)
    if err:
        return err
    pk = await asyncio.to_thread(_chat_thread_pk, user, thread_id)
    agents = await asyncio.to_thread(chat_store.list_thread_agents, user["id"], pk)
    live = sum(1 for item in agents if item["status"] in chat_store.ACTIVE)
    return JSONResponse(
        {"agents": agents, "live_count": live},
        headers={"Cache-Control": "no-store"},
    )


@router.get("/chat/threads/{thread_id}/jobs")
async def chat_thread_jobs(request: Request, thread_id: str):
    user, err = _chat_auth(request)
    if err:
        return err
    from .. import command_jobs

    def _load():
        # Polled every few seconds while the agents pane is open: keep the
        # database reads off the event loop that also drives live streams.
        pk = _chat_thread_pk(user, thread_id)
        jobs = command_jobs.list_thread_jobs(user["id"], pk)
        by_id = {item["id"]: item for item in command_jobs.recent_terminal_jobs(user["id"], pk)}
        by_id.update((item["id"], item) for item in chat_store.historical_command_jobs(user["id"], pk))
        return jobs, list(by_id.values())

    jobs, recent = await asyncio.to_thread(_load)
    return JSONResponse(
        {"jobs": jobs, "live_count": len(jobs), "recent": recent},
        headers={"Cache-Control": "no-store"},
    )


@router.post("/chat/threads/{thread_id}/jobs/{job_id}/kill")
async def chat_thread_job_kill(request: Request, thread_id: str, job_id: str):
    user, err = _chat_auth(request)
    if err:
        return err
    pk = _chat_thread_pk(user, thread_id)
    from .. import command_jobs

    result = command_jobs.kill_thread_job(user["id"], pk, str(job_id))
    if result is None:
        raise HTTPException(404, "Job not found")
    _text, trace = result
    if trace.get("status") == "error" and trace.get("error") == "not found":
        raise HTTPException(404, "Job not found")
    return JSONResponse({"ok": True, "status": trace.get("status")}, headers={"Cache-Control": "no-store"})


@router.get("/chat/threads/{thread_id}/permissions")
async def chat_thread_permissions(request: Request, thread_id: str):
    user, err = _chat_auth(request)
    if err:
        return err
    pk = chats.resolve_thread_pk(user["id"], thread_id)
    if pk is None:
        raise HTTPException(404, "Chat not found")
    return JSONResponse(
        permissions_mod.panel_for(user["id"], pk),
        headers={"Cache-Control": "no-store"},
    )


@router.post("/chat/threads/{thread_id}/permissions/{ask_id}")
async def chat_permission_decide(request: Request, thread_id: str, ask_id: str):
    user, err = _chat_auth(request)
    if err:
        return err
    if not limiter.allow(f"chat-perm:{user['id']}", 60, 60):
        raise HTTPException(429, "Too many permission decisions")
    raw = await _json_object(request)
    action = str(raw.get("action") or "")
    pk = chats.resolve_thread_pk(user["id"], thread_id)
    if pk is None:
        raise HTTPException(404, "Chat not found")
    try:
        if action == "extend":
            payload = permissions_mod.extend(user["id"], pk, ask_id)
        else:
            payload = permissions_mod.resolve(user["id"], pk, ask_id, action)
    except ValueError:
        raise HTTPException(422, "Invalid action") from None
    except LookupError:
        raise HTTPException(404, "Permission request not found") from None
    return JSONResponse(payload, headers={"Cache-Control": "no-store"})


@router.delete("/chat/permissions/grants/{grant_id}")
async def chat_permission_grant_delete(request: Request, grant_id: str):
    user, err = _chat_auth(request)
    if err:
        return err
    try:
        gid = int(grant_id)
    except ValueError:
        raise HTTPException(404, "Grant not found") from None
    if not permissions_mod.revoke_grant(user["id"], gid):
        raise HTTPException(404, "Grant not found")
    return JSONResponse({"ok": True}, headers={"Cache-Control": "no-store"})


@router.patch("/chat/threads/{thread_id}")
async def chat_thread_patch(request: Request, thread_id: str):
    user, err = _chat_auth(request)
    if err:
        return err
    raw = await _json_object(request)
    if set(raw) - {"title", "model", "archived", "pinned", "messages", "context", "tools", "expected_revision", "command_id", "bypass_permissions", "auto_permissions"}:
        raise HTTPException(422, "Unsupported chat fields")
    kwargs = {"expected_revision": _chat_revision(raw), "command_id": raw.get("command_id")}
    if "bypass_permissions" in raw:
        if not isinstance(raw["bypass_permissions"], bool):
            raise HTTPException(422, "bypass_permissions must be true or false")
        kwargs["bypass_permissions"] = raw["bypass_permissions"]
    if "auto_permissions" in raw:
        if not isinstance(raw["auto_permissions"], bool):
            raise HTTPException(422, "auto_permissions must be true or false")
        kwargs["auto_permissions"] = raw["auto_permissions"]
    if "title" in raw:
        if not isinstance(raw["title"], str):
            raise HTTPException(400, "Invalid title")
        kwargs["title"] = raw["title"]
    if "model" in raw:
        if not isinstance(raw["model"], str):
            raise HTTPException(400, "Invalid model")
        kwargs["model"] = raw["model"]
    if "archived" in raw:
        if not isinstance(raw["archived"], bool):
            raise HTTPException(422, "archived must be true or false")
        kwargs["archived"] = raw["archived"]
    if "pinned" in raw:
        if not isinstance(raw["pinned"], bool):
            raise HTTPException(422, "pinned must be true or false")
        kwargs["pinned"] = raw["pinned"]
    if "messages" in raw:
        if not isinstance(raw["messages"], list):
            raise HTTPException(400, "Invalid messages")
        kwargs["messages"] = raw["messages"]
    if "context" in raw:
        if not isinstance(raw["context"], str):
            raise HTTPException(400, "Invalid context")
        kwargs["context"] = raw["context"]
    if "tools" in raw:
        kwargs["tools"] = raw["tools"]
    try:
        thread = await asyncio.to_thread(chats.update_thread, user["id"], thread_id, **kwargs)
    except chats.ChatValidationError as exc:
        raise _chat_validation_error(exc) from None
    if not thread:
        raise HTTPException(404, "Chat not found")
    if "auto_permissions" in kwargs:
        logger.info(
            "auto_permissions user=%s thread=%s wanted=%s stored=%s",
            user["id"], thread_id, kwargs["auto_permissions"], thread.get("auto_permissions"),
        )
    return JSONResponse(_chat_public_thread(thread))


@router.post("/chat/threads/{thread_id}/title")
async def chat_thread_title_regen(request: Request, thread_id: str):
    """Regenerate the chat title from the whole conversation.

    Runs in the background as a standalone upstream request; the thread's
    messages are untouched so the conversation's prompt cache stays valid.
    """
    user, err = _chat_auth(request)
    if err:
        return err
    def _load():
        # Parsing a multi-megabyte transcript is the cost here: keep it off
        # the event loop.
        pk = chats.resolve_thread_pk(user["id"], thread_id)
        if pk is None:
            return None
        row = db.fetchone("SELECT messages,title_job_until FROM chat_threads WHERE id=? AND user_id=?", (pk, user["id"]))
        if row is None:
            return None
        try:
            messages = chats.parse_messages(row["messages"])
        except Exception:
            messages = []
        return pk, row, chats.first_user_text(messages).strip()

    loaded = await asyncio.to_thread(_load)
    if loaded is None:
        raise HTTPException(404, "Chat not found")
    pk, row, first_user_text = loaded
    if not first_user_text:
        raise HTTPException(400, "Nothing to title yet")
    if row["title_job_until"] and row["title_job_until"] > db.now():
        return JSONResponse({"started": False, "busy": True}, headers={"Cache-Control": "no-store"})
    if not limiter.allow(f"chat-title-thread:{user['id']}:{pk}", settings.chat_title_per_thread_per_hour, 3600):
        raise HTTPException(429, "Title regeneration limit reached for this chat")
    if not limiter.allow(f"chat-title-user:{user['id']}", settings.chat_title_per_user_per_hour, 3600):
        raise HTTPException(429, "Title regeneration limit reached for your account")
    chat_titles.spawn_regen(user["id"], pk)
    return JSONResponse({"started": True}, headers={"Cache-Control": "no-store"})


@router.post("/chat/threads/{thread_id}/recap")
async def chat_thread_recap(request: Request, thread_id: str):
    user, err = _chat_auth(request)
    if err:
        return err
    pk = _chat_thread_pk(user, thread_id)
    if not limiter.allow(f"chat-recap:{user['id']}", 20, 3600):
        raise HTTPException(429, "Recap request limit reached")
    try:
        await asyncio.to_thread(chat_recaps.request_manual, user["id"], pk)
    except chats.ChatValidationError as exc:
        raise _chat_validation_error(exc) from None
    return JSONResponse({"started": True}, headers={"Cache-Control": "no-store"})


@router.delete("/chat/threads/{thread_id}")
async def chat_thread_delete(request: Request, thread_id: str):
    user, err = _chat_auth(request)
    if err:
        return err
    raw = await _json_object(request)
    try:
        if not chats.delete_thread(user["id"], thread_id, expected_revision=_chat_revision(raw), command_id=raw.get("command_id")):
            if chats.get_thread(user["id"], thread_id) is not None:
                raise HTTPException(409, "Chat changed; retry with the current revision")
            raise HTTPException(404, "Chat not found")
    except chats.ChatValidationError as exc:
        raise _chat_validation_error(exc) from None
    return JSONResponse({"ok": True})


@router.post("/chat/threads/{thread_id}/compact")
async def chat_compact(request: Request, thread_id: str):
    user, err = _chat_auth(request)
    if err:
        return err
    if not limiter.allow(f"api-user:{user['id']}", settings.api_rate_per_minute, 60):
        raise HTTPException(429, "Too many requests")
    raw = await _json_object(request)
    if set(raw) - {"expected_revision", "command_id", "instructions", "strategy"}:
        raise HTTPException(422, "Unsupported compact fields")
    instructions = raw.get("instructions") or ""
    if instructions is None:
        instructions = ""
    if not isinstance(instructions, str) or len(instructions) > 2000:
        raise HTTPException(400, "Invalid instructions")
    strategy = raw.get("strategy")
    if strategy is not None and (not isinstance(strategy, str) or strategy not in ctxmod.STRATEGIES):
        raise HTTPException(400, "Invalid strategy")
    pk = _chat_thread_pk(user, thread_id)
    revision = _chat_revision(raw)
    try:
        prepared = chat_store.prepare_compact(
            user["id"],
            pk,
            revision,
            _chat_command(raw),
        )
    except chats.ChatValidationError as exc:
        raise _chat_validation_error(exc) from None
    if prepared.get("idempotent"):
        return JSONResponse(
            {
                "thread": _chat_public_thread(prepared["thread"]),
                "compacted": prepared.get("compacted", True),
                "idempotent": True,
            },
            headers={"Cache-Control": "no-store"},
        )
    thread = prepared["thread"]
    messages = prepared["history"]
    prompt_snapshot = (prepared.get("history_info") or {}).pop("prompt_snapshot", None)
    if not ctxmod.compactable_transcript(messages, keep_tail=(thread.get("compaction") or {}).get("keep_tail")):
        chat_store.remember_compact(user["id"], _chat_command(raw), prepared["fingerprint"], thread, compacted=False)
        return JSONResponse(
            {"thread": _chat_public_thread(thread), "compacted": False, "reason": "not_enough"},
            headers={"Cache-Control": "no-store"},
        )

    limit = ((thread.get("compaction") or {}).get("meter") or {}).get("limit")

    async def _sum(head, instructions=""):
        rows = [{"role": "system", "content": prompt_snapshot["system"]}, *head] if prompt_snapshot else head
        return await chat_runtime.summarize_conversation(
            request, user, thread.get("model") or "", rows, instructions, limit=limit,
            tools=prompt_snapshot.get("tools") if prompt_snapshot else None,
            cache_key=f"thread:{pk}",
        )

    checkpoint = await ctxmod.build_checkpoint(
        messages,
        strategy=strategy,
        instructions=instructions.strip(),
        trigger="manual",
        summarize=_sum,
        tokens_before=((thread.get("compaction") or {}).get("meter") or {}).get("used") or 0,
        keep_tail=(thread.get("compaction") or {}).get("keep_tail"),
        provider=True,
    )
    if checkpoint and ctxmod.summary_fell_back(strategy, checkpoint.get("strategy")):
        # A manual compact asked for a summary; silently keeping a clipped
        # handoff instead would lose detail the user never agreed to drop.
        raise HTTPException(502, "The model could not write a summary, so nothing was compacted. "
                                 "Try again, or choose the truncate strategy.")
    if not checkpoint:
        chat_store.remember_compact(user["id"], _chat_command(raw), prepared["fingerprint"], thread, compacted=False)
        return JSONResponse(
            {"thread": _chat_public_thread(thread), "compacted": False, "reason": "not_enough"},
            headers={"Cache-Control": "no-store"},
        )
    try:
        view = chat_store.finish_compact(
            user["id"],
            pk,
            checkpoint,
            command_id=_chat_command(raw),
            fingerprint=prepared["fingerprint"],
            expected_revision=revision,
            replay=messages,
            history_info=prepared.get("history_info"),
        )
    except chats.ChatValidationError as exc:
        raise _chat_validation_error(exc) from None
    return JSONResponse(
        {"thread": _chat_public_thread(view), "compacted": True},
        headers={"Cache-Control": "no-store"},
    )


@router.post("/chat/threads/{thread_id}/fork")
@router.post("/chat/threads/{thread_id}/duplicate")
async def chat_thread_fork(request: Request, thread_id: str):
    user, err = _chat_auth(request)
    if err:
        return err
    raw = await _json_object(request)
    try:
        thread = chats.fork_thread(user["id"], thread_id, expected_revision=_chat_revision(raw), command_id=_chat_command(raw))
    except chats.ChatValidationError as exc:
        raise _chat_validation_error(exc) from None
    if not thread:
        raise HTTPException(404, "Chat not found")
    return JSONResponse(_chat_public_thread(thread), status_code=201)


@router.post("/chat/threads/{thread_id}/project")
async def chat_thread_move_project(request: Request, thread_id: str):
    user, err = _chat_auth(request)
    if err:
        return err
    raw = await _json_object(request)
    try:
        thread = chats.move_thread_to_project(
            user["id"], thread_id, raw.get("host_id"), raw.get("project_id"),
            expected_revision=_chat_revision(raw), command_id=_chat_command(raw),
        )
    except chats.ChatValidationError as exc:
        raise _chat_validation_error(exc) from None
    if not thread:
        raise HTTPException(404, "Chat not found")
    return JSONResponse(_chat_public_thread(thread))


@router.get("/chat/context")
async def chat_context_get(request: Request):
    user, err = _chat_auth(request)
    if err:
        return err
    data = chats.get_user_context(user["id"])
    thread_id = request.query_params.get("thread_id")
    thread_context = ""
    tools = list(chat_tools.DEFAULT_TOOLS)
    if thread_id:
        # get_thread builds the full view (transcript parse + meter); keep it
        # off the event loop.
        thread = await asyncio.to_thread(chats.get_thread, user["id"], thread_id)
        if thread:
            thread_context = thread.get("context") or ""
            if "tools" in thread and thread["tools"] is not None:
                tools = thread["tools"]
    return JSONResponse(
        {
            **data,
            "thread_context": thread_context,
            "tools": tools,
            "limits": {"context": chats.CONTEXT_MAX},
        },
        headers={"Cache-Control": "no-store"},
    )


@router.put("/chat/context")
async def chat_context_put(request: Request):
    user, err = _chat_auth(request)
    if err:
        return err
    raw = await _json_object(request)
    if set(raw) - {"instructions", "profile", "recaps", "expected_revision", "command_id"}:
        raise HTTPException(422, "Unsupported context fields")
    kwargs = {"expected_revision": _chat_revision(raw), "command_id": raw.get("command_id")}
    if "instructions" in raw:
        if not isinstance(raw["instructions"], str):
            raise HTTPException(400, "Invalid instructions")
        kwargs["instructions"] = raw["instructions"]
    if "profile" in raw:
        if not isinstance(raw["profile"], str):
            raise HTTPException(400, "Invalid profile")
        kwargs["profile"] = raw["profile"]
    if "recaps" in raw:
        if not isinstance(raw["recaps"], bool):
            raise HTTPException(400, "Invalid recaps")
        kwargs["recaps"] = raw["recaps"]
    try:
        data = chats.set_user_context(user["id"], **kwargs, strict=True) if kwargs else chats.get_user_context(user["id"])
    except chats.ChatValidationError as exc:
        raise _chat_validation_error(exc) from None
    return JSONResponse(data)


@router.post("/chat/completions")
async def chat_web_completions(request: Request):
    user, err = _chat_auth(request)
    if err:
        return err
    # Old open pages must not retain a second, browser-owned transcript writer.
    raise HTTPException(409, "Chat has been upgraded. Refresh this page to use synchronized conversations.")
