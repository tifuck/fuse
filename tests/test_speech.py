"""Dictation backend: off by default, WAV-only, forwarded to one OpenAI-style
/v1/audio/transcriptions server. No database or upstream access."""
import asyncio
import io
import json
import wave
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request

from app import security, speech
from app.web import speech as speech_routes
from app.web.source_routes import admin_actions


@pytest.fixture(autouse=True)
def no_db(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Unexpected database access")
    for name in ("fetchone", "fetchall", "execute", "tx"):
        monkeypatch.setattr(speech.db, name, forbidden)


def _wav(seconds=0.5, channels=1, rate=16000):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\0\0" * channels * int(rate * seconds))
    return buf.getvalue()


def _request(body: bytes) -> Request:
    sent = False

    async def receive():
        nonlocal sent
        if sent:
            return {"type": "http.disconnect"}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    return Request({"type": "http", "method": "POST", "path": "/speech/transcribe", "headers": []}, receive)


def test_check_wav():
    speech.check_wav(speech.silence_wav())
    speech.check_wav(_wav(rate=48000))
    for bad in (b"not audio", _wav(channels=2), _wav(seconds=speech.MAX_SECONDS + 1), _wav(rate=96000)):
        with pytest.raises(ValueError):
            speech.check_wav(bad)


def test_off_by_default_without_touching_the_database(monkeypatch):
    monkeypatch.setattr(speech.settings, "speech_enabled", False)
    assert speech.available() is False


def test_config_needs_an_enabled_row_with_url_and_model(monkeypatch):
    monkeypatch.setattr(speech.settings, "speech_enabled", True)
    row = {"id": 5, "enabled": 1, "config": json.dumps({"base_url": "https://stt.test/v1", "model": "m"})}
    rows = {"sources": row, "credentials": {"secret": "enc"}}
    monkeypatch.setattr(speech.db, "fetchone", lambda sql, args=(): rows["credentials" if "credentials" in sql else "sources"])
    monkeypatch.setattr(speech.accounts, "cred_secret", lambda cred: "sk-test")
    assert speech._config() == {"base_url": "https://stt.test/v1", "model": "m", "key": "sk-test"}
    row["enabled"] = 0
    assert speech._config() is None
    row["enabled"], row["config"] = 1, json.dumps({"base_url": "https://stt.test/v1"})
    assert speech._config() is None


def test_transcribe_posts_multipart_with_bearer(monkeypatch):
    seen = {}

    def handler(request: httpx.Request):
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = request.content
        return httpx.Response(200, json={"text": "  hello there "})

    monkeypatch.setattr(speech, "_config", lambda: {"base_url": "https://stt.test:8000", "model": "parakeet", "verify": 0, "key": "sk-test"})
    monkeypatch.setattr(speech, "_client", lambda cfg, timeout: httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    assert asyncio.run(speech.transcribe(speech.silence_wav())) == "hello there"
    assert seen["url"] == "https://stt.test:8000/v1/audio/transcriptions"
    assert seen["auth"] == "Bearer sk-test"
    assert b'name="model"\r\n\r\nparakeet' in seen["body"]
    assert b'filename="audio.wav"' in seen["body"]


def test_transcribe_surfaces_upstream_errors(monkeypatch):
    monkeypatch.setattr(speech, "_config", lambda: {"base_url": "https://stt.test", "model": "m", "key": ""})
    monkeypatch.setattr(speech, "_client", lambda cfg, timeout: httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(403, json={"detail": "no"}))))
    with pytest.raises(speech.SpeechError):
        asyncio.run(speech.transcribe(speech.silence_wav()))


@pytest.fixture
def signed_in(monkeypatch):
    monkeypatch.setattr(speech_routes, "_chat_auth", lambda request: ({"id": 7}, None))
    monkeypatch.setattr(speech_routes.limiter, "allow", lambda *args: True)


def test_route_is_404_while_off(monkeypatch, signed_in):
    monkeypatch.setattr(speech_routes.speech, "available", lambda: False)
    response = asyncio.run(speech_routes.speech_transcribe(_request(speech.silence_wav())))
    assert response.status_code == 404


def test_route_forwards_valid_audio(monkeypatch, signed_in):
    monkeypatch.setattr(speech_routes.speech, "available", lambda: True)
    forward = AsyncMock(return_value="hello")
    monkeypatch.setattr(speech_routes.speech, "transcribe", forward)
    audio = speech.silence_wav()
    response = asyncio.run(speech_routes.speech_transcribe(_request(audio)))
    assert response.status_code == 200
    assert json.loads(response.body) == {"text": "hello"}
    assert response.headers["cache-control"] == "no-store"
    forward.assert_awaited_once_with(audio)

    forward.reset_mock()
    assert asyncio.run(speech_routes.speech_transcribe(_request(b"RIFF junk"))).status_code == 400
    forward.assert_not_awaited()

    forward.side_effect = speech.SpeechError("down")
    assert asyncio.run(speech_routes.speech_transcribe(_request(audio))).status_code == 502


def test_route_rate_limits_per_user(monkeypatch, signed_in):
    monkeypatch.setattr(speech_routes.speech, "available", lambda: True)
    keys = []
    monkeypatch.setattr(speech_routes.limiter, "allow", lambda key, limit, window: keys.append(key) or False)
    response = asyncio.run(speech_routes.speech_transcribe(_request(speech.silence_wav())))
    assert response.status_code == 429
    assert keys == ["speech:7"]


@pytest.mark.parametrize("enabled,mic", [(False, "microphone=()"), (True, "microphone=(self)")])
def test_microphone_permission_follows_the_flag(monkeypatch, enabled, mic):
    monkeypatch.setattr(security.settings, "speech_enabled", enabled)
    app = FastAPI()
    app.add_middleware(security.SecurityHeadersMiddleware)
    app.get("/")(lambda: {})
    assert mic in TestClient(app).get("/").headers["permissions-policy"]


def test_admin_test_button_probes_the_speech_server(monkeypatch):
    monkeypatch.setattr(admin_actions, "current_user", lambda request: {"id": 1, "is_admin": True})
    monkeypatch.setattr(admin_actions.limiter, "allow", lambda *args: True)
    monkeypatch.setattr(admin_actions.db, "fetchone", lambda *args: {"id": 9, "type": "speech"})
    probe = AsyncMock(return_value={"ok": True, "checks": []})
    other = AsyncMock()
    monkeypatch.setattr(admin_actions.speech, "probe", probe)
    monkeypatch.setattr(admin_actions, "probe_source", other)
    response = asyncio.run(admin_actions.admin_source_test(Request({"type": "http"}), 9))
    assert response.status_code == 200
    probe.assert_awaited_once()
    other.assert_not_awaited()
