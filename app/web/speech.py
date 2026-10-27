from __future__ import annotations

import logging

import httpx
from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse

from .. import speech
from ..config import settings
from ..security import allowed_base_url, limiter
from .deps import _chat_auth, _notices, render, require_admin

router = APIRouter()
logger = logging.getLogger(__name__)


def _fail(message: str, status: int) -> JSONResponse:
    return JSONResponse(
        {"error": {"message": message, "type": "speech", "code": status}},
        status_code=status,
        headers={"Cache-Control": "no-store"},
    )


@router.post("/speech/transcribe")
async def speech_transcribe(request: Request):
    user, err = _chat_auth(request)
    if err:
        return err
    if not speech.available():
        return _fail("Speech to text is off.", 404)
    # Live dictation re-sends the open phrase about once a second.
    if not limiter.allow(f"speech:{user['id']}", 240, 60):
        return _fail("Too many dictation requests. Wait a moment.", 429)
    body = await request.body()
    try:
        speech.check_wav(body)
    except ValueError as exc:
        return _fail(str(exc), 400)
    try:
        text = await speech.transcribe(body)
    except (speech.SpeechError, httpx.HTTPError) as exc:
        logger.warning("Speech transcription failed: %s", str(exc) or type(exc).__name__)
        return _fail("The speech server did not answer.", 502)
    return JSONResponse({"text": text}, headers={"Cache-Control": "no-store"})


@router.post("/admin/speech")
async def admin_speech_save(
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
    if not settings.speech_enabled:
        raise HTTPException(404, "Speech to text is off (SPEECH_ENABLED).")
    from .source_routes.admin_pages import _admin_sources_ctx

    url, name, key = base_url.strip(), model.strip()[:200], api_key.strip()
    on = enabled in ("1", "on", "true")
    error = None
    if url and not allowed_base_url(url):
        error = "Base URL must be http(s) and not a metadata host."
    elif len(key) > 4096 or any(ord(c) < 32 or 0xD800 <= ord(c) <= 0xDFFF for c in key):
        error = "API key must be at most 4096 valid characters."
    elif on and not (url and name):
        error = "Set a base URL and a model before turning speech to text on."
    if error:
        return render(request, "admin_sources.html", user, **_admin_sources_ctx(error=error), status_code=400)
    speech.save(base_url=url, model=name, api_key=key, verify=insecure not in ("1", "on", "true"), enabled=on)
    _notices[user["id"]] = "Speech to text saved."
    return RedirectResponse("/admin/sources#speech", status_code=303)
