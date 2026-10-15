"""Isolated browser-test server. Never loads app.main or calls a real provider.

Run only through the optional browser regression harness or explicitly with
FUSE_CHAT_TEST_MODE=1. Uses a temporary database and loopback port 8099.
"""
import asyncio
import json
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

if __name__ != "__main__" or os.environ.get("FUSE_CHAT_TEST_MODE") != "1":
    raise RuntimeError("This fixture is only for explicit isolated browser tests")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from app import chat_events, chat_runs, chat_runtime, chat_store, chats, db, web
from app.config import ROOT
from tests.mariadb_fixture import isolated_database


queue_gates = {}


async def fake_model(request, user, raw, **kwargs):
    async def frames():
        last = raw["messages"][-1]["content"]
        if isinstance(last, list):
            text = " ".join(
                part.get("text") or ""
                for part in last
                if isinstance(part, dict)
            )
        else:
            text = last
        if text.startswith("Queue fixture"):
            run_id = raw["run_id"]
            gate = queue_gates.setdefault(run_id, asyncio.Event())
            transcript = [{"role": "user", "content": text}]
            tool_step = "tool-step" in text
            thinking_step = "thinking-step" in text
            try:
                if tool_step:
                    yield b'data: {"fuse":{"event":"tool","id":"offline-tool","name":"read_file","status":"running","path":"fixture.txt"}}\n\n'
                    yield b'data: {"fuse":{"event":"activity","phase":"tools","tool_id":"offline-tool"}}\n\n'
                for index in range(600):
                    pending = chat_store.take_steers(run_id)
                    output = "Working before the queued instruction. " if index == 0 else ". "
                    if pending:
                        # Deliberately flush leftover tokens after HTTP acceptance
                        # but before the next model request consumes the steer.
                        yield b'data: {"choices":[{"delta":{"content":"PREVIOUS_TAIL"}}]}\n\n'
                        # A held steer models a long-running command: the run is
                        # quiet but still heartbeats (and so checkpoints), like
                        # the tool-wait loop in production.
                        hold = 6.0 if "hold-steer" in text else 1.0 if "slow-steer" in text else 0.2
                        for _ in range(int(hold / 0.5)):
                            await asyncio.sleep(0.5)
                            yield b'data: {"fuse":{"event":"heartbeat"}}\n\n'
                        for item in pending:
                            transcript.append({"role": "user", "content": chat_store.steer_envelope(item["id"], item["text"])})
                        chat_store.save_turn_progress(run_id, transcript)
                        yield ("data: " + json.dumps({"fuse": {"event": "steer.delivered", "ids": [item["id"] for item in pending]}}) + "\n\n").encode()
                        yield b'data: {"fuse":{"event":"activity","phase":"model"}}\n\n'
                        output = "\nAcknowledged: " + " / ".join(item["text"] for item in pending) + ". Continuing work.\n"
                    if tool_step and not pending:
                        yield b'data: {"fuse":{"event":"heartbeat"}}\n\n'
                    else:
                        field = "reasoning_content" if thinking_step else "content"
                        yield ("data: " + json.dumps({"choices": [{"delta": {field: output}}]}) + "\n\n").encode()
                    yield ("data: " + json.dumps({"usage": {"prompt_tokens": 100, "completion_tokens": (index + 1) * 10}, "choices": []}) + "\n\n").encode()
                    if gate.is_set():
                        break
                    await asyncio.sleep(0.1)
                if tool_step:
                    yield b'data: {"fuse":{"event":"tool","id":"offline-tool","name":"read_file","status":"done","output":"Offline fixture result"}}\n\n'
                yield b"data: [DONE]\n\n"
            finally:
                queue_gates.pop(run_id, None)
            return
        if text.startswith(("Cache fixture miss", "Cache fixture effort")):
            # What chat_runtime emits when a round loses a warm cache.
            why = {"reason": "effort", "effort_from": "", "effort_to": "xhigh"} if "effort" in text else {"reason": ""}
            yield ("data: " + json.dumps({"fuse": {"event": "cache", "state": "miss", "prompt": 52000,
                "cached": 1000, "idle_s": 40, **why, "run_id": raw.get("run_id")}}) + "\n\n").encode()
        response = "Synchronized answer: " + text + ". " + "Live output across both devices. " * 16
        for chunk in ["<think>", "Shared reasoning ", "is preserved.", "</think>"] + [response[i:i + 12] for i in range(0, len(response), 12)]:
            yield ("data: " + json.dumps({"choices": [{"delta": {"content": chunk}}]}) + "\n\n").encode()
            await asyncio.sleep(0.08)
        yield b'data: {"usage":{"total_tokens":42},"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n'
        yield b"data: [DONE]\n\n"
    return StreamingResponse(frames())


async def fake_title(request, user, model, text):
    words = (text or "New chat").split()[:5]
    return " ".join(words) or "New chat"


@asynccontextmanager
async def lifespan(app):
    with isolated_database():
        db.execute("INSERT INTO users(id,email,password_hash,created_at) VALUES(1,'browser@test.invalid','unused',?)", (db.now(),))
        chat_runtime.stream_dashboard_chat = fake_model
        chat_runtime.generate_thread_title = fake_title
        chat_runs._stopping = False
        chat_events.start()
        chat_store.start_publisher()
        try:
            yield
        finally:
            await chat_events.stop()
            await chat_runs.stop()
            await chat_store.stop_publisher()


app = FastAPI(lifespan=lifespan)
app.add_middleware(SessionMiddleware, secret_key="isolated-browser-test-only", https_only=False)


@app.post("/__fixture/finish/{run_id}")
async def finish_fixture(run_id: str):
    gate = queue_gates.get(run_id)
    if gate:
        gate.set()
    return {"ok": bool(gate)}


def _open_thread(conn):
    row = conn.execute(
        "SELECT id FROM chat_threads WHERE user_id=1 ORDER BY updated_at DESC, id DESC LIMIT 1"
    ).fetchone()
    if row is None:
        raise HTTPException(404, "no thread")
    return row["id"]


@app.post("/__fixture/sidebar/workspace")
async def sidebar_workspace():
    """One archived project plus a schedule, neither of which the page can create.

    Host creation needs an unlocked vault, and schedules have no browser route,
    so both rows are seeded here. The ciphertext is never a real secret.
    """

    def seed():
        now = db.now()
        with db.tx() as conn:
            conn.execute(
                """INSERT INTO user_hosts(user_id,alias,label,hostname,port,username,auth_type,secret,created_at,updated_at)
                   VALUES(1,'box','','127.0.0.1',22,'root','password','unused',?,?)""",
                (now, now),
            )
            host_id = conn.execute("SELECT id FROM user_hosts WHERE user_id=1 AND alias='box'").fetchone()["id"]
            conn.execute(
                """INSERT INTO user_projects(user_id,host_id,name,remote_path,notes,created_at,updated_at,archived,archived_at)
                   VALUES(1,?,'Fixture project','/srv/fixture','',?,?,1,?)""",
                (host_id, now, now, now),
            )
            project_id = conn.execute("SELECT id FROM user_projects WHERE user_id=1 AND name='Fixture project'").fetchone()["id"]
            public = chats.new_public_id()
            thread_id = _open_thread(conn)
            conn.execute(
                """INSERT INTO chat_schedules(public_id,user_id,name,target,thread_id,prompt,model,timezone,recur_kind,created_at,updated_at)
                   VALUES(?,1,'Sidebar schedule','chat',?,'Scheduled sidebar check','offline','UTC','once',?,?)""",
                (public, thread_id, now, now),
            )
            conn.execute("UPDATE chat_threads SET schedule_id=? WHERE id=?", (
                conn.execute("SELECT id FROM chat_schedules WHERE public_id=?", (public,)).fetchone()["id"], thread_id))
            schedule_id = conn.execute("SELECT id FROM chat_schedules WHERE public_id=?", (public,)).fetchone()["id"]
        return {"host_id": host_id, "project_id": project_id, "schedule_id": schedule_id}

    return await asyncio.to_thread(seed)


@app.post("/__fixture/sidebar/scheduled/{thread_id}")
async def sidebar_scheduled(thread_id: str):
    """Fire one scheduled turn the way the scheduler does, without waiting for its clock."""

    def fire():
        pk = chats.resolve_thread_pk(1, thread_id)
        if pk is None:
            raise HTTPException(404, "no thread")
        row = db.fetchone("SELECT revision,schedule_id FROM chat_threads WHERE id=? AND user_id=1", (pk,))
        # schedule_id as app/schedules.py passes it: a schedule fire is gated
        # by its own enabled/paused state, not by an earlier Stop in the chat.
        return chat_store.start_turn(
            1, pk, "Scheduled sidebar check", row["revision"], "scheduled-sidebar",
            auto_title=False, auto=True, scheduled=True, schedule_id=row["schedule_id"],
        )

    result = await asyncio.to_thread(fire)
    # The unarchive commits with the turn. Starting the run needs a request
    # scope the scheduler builds itself; the sidebar only needs the committed row.
    return {"archived": bool(result["thread"]["archived"]), "running": bool(result["thread"]["running"])}


@app.post("/__fixture/sidebar/child")
async def sidebar_child():
    """Leave one subagent running after the newest thread's root turn ends."""
    from uuid import uuid4

    child = str(uuid4())

    def insert():
        with db.tx() as conn:
            thread_id = _open_thread(conn)
            root = conn.execute(
                """SELECT id FROM chat_runs
                   WHERE thread_id=? AND parent_run_id IS NULL ORDER BY created_at DESC LIMIT 1""",
                (thread_id,),
            ).fetchone()
            if root is None:
                raise HTTPException(409, "no root run")
            now = db.now()
            conn.execute(
                """INSERT INTO chat_runs(id,thread_id,user_id,status,request,message_id,message,created_at,updated_at,
                       parent_run_id,root_run_id,agent_depth)
                   VALUES(?,? ,1,'running',?,?,?,?,?,?,?,1)""",
                (child, thread_id, json.dumps({"agent": {"description": "sidebar fixture child"}}),
                 "child-message", "{}", now, now, root["id"], root["id"]),
            )
            return child

    return {"child_id": await asyncio.to_thread(insert)}


@app.delete("/__fixture/sidebar/child/{child_id}")
async def sidebar_child_done(child_id: str):
    # The real finish path emits agent.completed and thread.updated, which is
    # what lets the open sidebar drop the spinner. A bare status update would not.
    def settle():
        chat_store.finish_run(child_id, {"content": "sidebar fixture child done"}, "completed")
        return {"ok": True}

    return await asyncio.to_thread(settle)


@app.post("/__fixture/sidebar/runs/{run_id}/transcript")
async def sidebar_transcript(run_id: str):
    """Give a stopped run the saved transcript resume_run requires."""

    def stamp():
        with db.tx() as conn:
            row = conn.execute(
                "SELECT id, status FROM chat_runs WHERE id=? AND user_id=1 AND parent_run_id IS NULL",
                (run_id,),
            ).fetchone()
            if row is None:
                raise HTTPException(404, "no run")
            conn.execute(
                "UPDATE chat_runs SET provider_messages=? WHERE id=?",
                (json.dumps([{"role": "user", "content": "resume me"}]), run_id),
            )
        return {"ok": True, "status": row["status"]}

    return await asyncio.to_thread(stamp)


@app.get("/__fixture/sidebar/runs/{thread_id}")
async def sidebar_runs(thread_id: str):
    """Root runs of one thread, so a test can resume the one the server allows."""

    def load():
        pk = chats.resolve_thread_pk(1, thread_id)
        if pk is None:
            raise HTTPException(404, "no thread")
        rows = db.fetchall(
            """SELECT id, status, resume_count, (provider_messages IS NOT NULL) AS has_transcript
               FROM chat_runs WHERE thread_id=? AND user_id=1 AND parent_run_id IS NULL
               ORDER BY created_at DESC""",
            (pk,),
        )
        active = db.fetchone(
            "SELECT 1 AS ok FROM chat_runs WHERE thread_id=? AND status IN ('queued','running') AND parent_run_id IS NULL",
            (pk,),
        )
        return [
            {"id": row["id"], "status": row["status"],
             "resumable": row["status"] in ("interrupted", "cancelled") and bool(row["has_transcript"])
             and int(row["resume_count"] or 0) < chat_store.MAX_RESUME_ATTEMPTS and active is None}
            for row in rows
        ]

    return await asyncio.to_thread(load)


@app.get("/")
async def login(request: Request):
    from app import accounts

    # Stamped like a real login: unstamped sessions are refused.
    request.session.update(uid=1, ph=accounts.password_fingerprint(accounts.get_user(1)), csrf="browser-test-csrf")
    return RedirectResponse("/chat")


@app.get("/chat")
async def page(request: Request):
    if not request.session.get("uid"):
        return RedirectResponse("/")
    from app import hosts

    user = web.current_user(request)
    agent = hosts.settings_for(1)
    config = {"families": [{"type": "llama", "title": "Test", "models": [{"id": "offline", "name": "Offline fixture", "reasoning": ["none", "low", "medium", "high"], "reasoning_default": "medium"}]}],
              "locked": [], "preselect": "offline", "tools": [], "context": chats.get_user_context(1), "limits": {"context": 4000, "attachments": 4, "attachment_chars": 1500000},
              "permission_defaults": {"chat": agent["default_chat_permissions"], "project": agent["default_project_permissions"]}}
    return web.render(request, "chat.html", user=user, chat_config=config, skills_bundle=[])


@app.get("/static/chat.js")
async def instrumented_chat():
    # Expose real renderer/state functions only on this isolated test server.
    # Production assets have no fixture API or test data.
    source = (ROOT / "static" / "chat.js").read_text()
    head, closing = source.rsplit("\n})();", 1)
    hooks = (ROOT / "tests" / "tool_ui_hooks.js").read_text()
    # Formatting helpers live inside theme modules; pull them from the theme's
    # public surface here so the hooks can reach them without widening the
    # production runtime's own wiring.
    seam = "  displayCommand,\n  shortToolPath,\n  toolRowLabel,\n"
    anchor = "  } = createConsoleTheme("
    if anchor not in head:
        raise RuntimeError("chat.js theme destructuring seam not found")
    head = head.replace(anchor, seam + anchor, 1)
    return Response(head + "\n" + hooks + "\n})();" + closing, media_type="text/javascript")


app.mount("/static", StaticFiles(directory=str(ROOT / "static")), name="static")
app.include_router(web.router)
app.include_router(chat_events.router)
uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("FUSE_BROWSER_PORT", "8099")), log_level="warning")
