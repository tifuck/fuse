"""OpenAI-compatible session affinity without credentials, network, or a database."""
import asyncio
import json

import httpx
import pytest

from app.sources import openai_compat as compat


@pytest.fixture(params=compat.ROTATION_MODES)
def upstream(request, monkeypatch):
    pins = {}
    seen = []
    replies = []
    client_type = httpx.AsyncClient

    def handler(req):
        seen.append((req.headers["Authorization"], json.loads(req.content)))
        if replies:
            return replies.pop(0)
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    monkeypatch.setattr(compat, "_cursors", {})
    monkeypatch.setattr(compat.accounts, "cred_secret", lambda cred: f"test-key-{cred['id']}")
    monkeypatch.setattr(compat, "lookup", lambda kind, sid: pins.get((kind, sid)))
    monkeypatch.setattr(compat, "bind", lambda kind, sid, cid: pins.__setitem__((kind, sid), cid))
    monkeypatch.setattr(
        compat.httpx, "AsyncClient",
        lambda **kwargs: client_type(transport=httpx.MockTransport(handler), **kwargs),
    )

    def source(source_id=71):
        return compat.OpenAICompatSource(
            {"id": source_id, "name": "test"},
            {"rotation": request.param, "base_url": "https://upstream.test/v1"},
            [{"id": 1}, {"id": 2}],
        )

    return source, pins, seen, replies, request.param


def test_successful_calls_reuse_session_credential(upstream):
    source, pins, seen, _, mode = upstream

    async def run():
        first = await source().chat({"model": "m"}, session_id="fx:session-a")
        second = await source().chat({"model": "m"}, session_id="fx:session-a")
        third = await source().chat({"model": "m"}, session_id="fx:session-b")
        assert first.status == second.status == third.status == 200
        assert first.credential_id == second.credential_id == 1
        assert third.credential_id == (2 if mode in ("every_request", "round_robin") else 1)

    asyncio.run(run())
    assert pins[("openai:71", "fx:session-a")] == 1
    assert seen[0][0] == seen[1][0] == "Bearer test-key-1"
    assert [body["prompt_cache_key"] for _, body in seen] == [
        "fx:session-a", "fx:session-a", "fx:session-b",
    ]


def test_existing_pin_wins_in_every_mode_and_is_source_scoped(upstream):
    source, pins, _, _, _ = upstream
    pins[("openai:71", "fx:session-a")] = 2

    async def run():
        pinned = await source().chat({"model": "m"}, session_id="fx:session-a")
        other = await source(72).chat({"model": "m"}, session_id="fx:session-a")
        assert pinned.credential_id == 2
        assert other.credential_id == 1

    asyncio.run(run())
    assert pins[("openai:71", "fx:session-a")] == 2
    assert pins[("openai:72", "fx:session-a")] == 1


def test_unscoped_calls_keep_rotation_without_pins(upstream):
    source, pins, seen, _, mode = upstream

    async def run():
        first = await source().chat({"model": "m"})
        second = await source().chat({"model": "m"})
        assert first.credential_id == 1
        assert second.credential_id == (2 if mode in ("every_request", "round_robin") else 1)

    asyncio.run(run())
    assert pins == {}
    assert all("prompt_cache_key" not in body for _, body in seen)


@pytest.mark.parametrize("ending", ["complete", "truncated", "error", "malformed"])
def test_stream_binds_only_after_successful_completion(upstream, ending):
    source, pins, seen, replies, mode = upstream
    chunk = b'data: {"choices":[{"delta":{"content":"ok"}}]}\n\n'
    tails = {
        "complete": b"data: [DONE]\n\n",
        "truncated": b"",
        "error": b'data: {"error":{"message":"failed"}}\n\ndata: [DONE]\n\n',
        "malformed": b"data: {invalid}\n\n",
    }
    replies.append(httpx.Response(200, content=chunk + tails[ending]))

    async def run():
        result = await source().chat({"model": "m", "stream": True}, session_id="fx:session-a")
        assert result.status == 200
        assert pins == {}
        if ending == "malformed":
            with pytest.raises(ValueError, match="malformed SSE JSON"):
                async for _ in result.stream:
                    pass
        else:
            assert b"".join([part async for part in result.stream]) == chunk + tails[ending]
        assert result.stream.usage["stream_status"] == ("complete" if ending == "complete" else "failed")
        assert pins == ({("openai:71", "fx:session-a"): 1} if ending == "complete" else {})
        followup = await source().chat({"model": "m"}, session_id="fx:session-a")
        expected = 2 if ending != "complete" and mode in ("every_request", "round_robin") else 1
        assert followup.credential_id == expected

    asyncio.run(run())
    assert len(seen) == 2


def test_failed_response_does_not_bind_or_fail_over(upstream):
    source, pins, seen, replies, _ = upstream
    replies.append(httpx.Response(429, json={"error": "busy"}))

    result = asyncio.run(source().chat({"model": "m"}, session_id="fx:session-a"))
    assert result.status == 429
    assert result.credential_id == 1
    assert pins == {}
    assert len(seen) == 1
