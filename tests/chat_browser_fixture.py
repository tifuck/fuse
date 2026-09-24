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
from fastapi import FastAPI, Request
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
                        await asyncio.sleep(1.0 if "slow-steer" in text else 0.2)
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


@app.get("/")
async def login(request: Request):
    request.session.update(uid=1, csrf="browser-test-csrf")
    return RedirectResponse("/chat")


@app.get("/chat")
async def page(request: Request):
    if not request.session.get("uid"):
        return RedirectResponse("/")
    user = web.current_user(request)
    config = {"families": [{"type": "llama", "title": "Test", "models": [{"id": "offline", "name": "Offline fixture", "reasoning": ["none", "low", "medium", "high"], "reasoning_default": "medium"}]}],
              "locked": [], "preselect": "offline", "tools": [], "context": chats.get_user_context(1), "limits": {"context": 4000, "attachments": 4, "attachment_chars": 1500000}}
    return web.render(request, "chat.html", user=user, chat_config=config, skills_bundle=[])


@app.get("/static/chat.js")
async def instrumented_chat():
    # Expose real renderer/state functions only on this isolated test server.
    # Production assets have no fixture API or test data.
    source = (ROOT / "static" / "chat.js").read_text()
    head, closing = source.rsplit("\n})();", 1)
    hooks = (ROOT / "tests" / "tool_ui_hooks.js").read_text()
    return Response(head + "\n" + hooks + "\n})();" + closing, media_type="text/javascript")


app.mount("/static", StaticFiles(directory=str(ROOT / "static")), name="static")
app.include_router(web.router)
app.include_router(chat_events.router)
uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("FUSE_BROWSER_PORT", "8099")), log_level="warning")
