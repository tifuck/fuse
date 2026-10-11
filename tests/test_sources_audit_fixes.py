"""Source-layer regressions from the audit (no network, no pooled models)."""
import asyncio
import json
from unittest.mock import patch

import httpx
import pytest

from app import accounts
from app.proxy import openai_chat
from app.sources import chatgpt_stream, claude_catalog, ollama, openrouter, usage_walk


def test_schema_failure_keeps_reported_usage():
    def handler(request):
        return httpx.Response(200, json={"choices": [], "usage": {"prompt_tokens": 11, "completion_tokens": 3}})

    async def run():
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        return await openai_chat(client, "https://up.test/v1/chat/completions", {}, {"model": "m"}, close_client=True)

    status, _obj, _stream, usage, _headers = asyncio.run(run())
    assert status == 502
    assert usage.get("prompt_tokens") == 11 and usage.get("failure_phase") == "response_received"


class _Lines:
    def __init__(self, lines):
        self._lines = lines

    async def aiter_lines(self):
        for line in self._lines:
            yield line

    async def aiter_text(self):
        for line in self._lines:
            yield line

    async def aclose(self):
        pass


def test_ollama_length_stop_is_reported_as_length():
    lines = [json.dumps({"message": {"content": "cut"}, "done": False}),
             json.dumps({"message": {"content": ""}, "done": True, "done_reason": "length", "eval_count": 5})]

    async def run():
        return [chunk async for chunk in ollama._stream(_Lines(lines), "m")]

    frames = [json.loads(c.decode()[6:]) for c in asyncio.run(run()) if c.startswith(b"data: {")]
    finishes = [ch["finish_reason"] for f in frames for ch in f.get("choices", []) if ch.get("finish_reason")]
    assert finishes == ["length"]


def test_sse_line_without_newline_is_bounded():
    async def run():
        with patch.object(chatgpt_stream, "SSE_LINE_MAX", 1000):
            async for _ in chatgpt_stream._upstream_events(_Lines(["x" * 600, "y" * 600])):
                pass

    with pytest.raises(chatgpt_stream.UpstreamProtocolError):
        asyncio.run(run())


def test_claude_partial_catalog_keeps_discovered_models():
    saved = (claude_catalog._models_cache, claude_catalog._catalog_meta)
    try:
        claude_catalog._models_cache, claude_catalog._catalog_meta = None, {}
        first = claude_catalog._remember_models([{"id": "claude-test-9"}, {"id": "claude-test-10"}])
        second = claude_catalog._remember_models([{"id": "claude-test-10"}])
        assert any("claude-test-9" in name for name in first)
        assert any("claude-test-9" in name for name in second)
    finally:
        claude_catalog._models_cache, claude_catalog._catalog_meta = saved


def test_usage_walk_skips_an_unreadable_credential():
    rows = [{"id": 1, "source_type": "zai"}, {"id": 2, "source_type": "zai"}]

    def extra(row):
        if row["id"] == 1:
            raise ValueError("corrupt")
        return {}

    with patch.object(usage_walk.db, "fetchall", return_value=rows), patch.object(accounts, "cred_extra", side_effect=extra), \
            patch.object(usage_walk, "_is_paid", return_value=True):
        assert [row["id"] for _kind, row in usage_walk.usage_targets()] == [2]


def test_model_route_onto_openrouter_obeys_account_enablement():
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    import app.sources as sources

    src = SimpleNamespace(type_id="openrouter")
    route = {"source_id": 5, "upstream_model": "vendor/paid-model"}
    with patch.object(sources.db, "fetchone", return_value=route), patch.object(sources, "get", return_value=src), \
            patch.object(openrouter, "resolve", AsyncMock(return_value=None)) as resolve:
        assert asyncio.run(sources.pick("alias", user_id=7)) is None
    resolve.assert_awaited_once_with("openrouter/vendor/paid-model", 7)
    with patch.object(sources.db, "fetchone", return_value=route), patch.object(sources, "get", return_value=src), \
            patch.object(openrouter, "resolve", AsyncMock(return_value=("vendor/paid-model", False))):
        assert asyncio.run(sources.pick("alias", user_id=7)) == (src, "vendor/paid-model")


@pytest.mark.usefixtures("isolated_db")
def test_stale_openrouter_rejection_does_not_disable_replacement_key():
    from app import db

    sid = openrouter.ensure_openrouter_source()
    user = accounts.create_user("orkey@test.invalid", "password12")
    cid = accounts.add_credential(sid, "mine", "sk-or-old", extra={}, donated_by=user["id"])
    dispatched = db.fetchone("SELECT secret FROM credentials WHERE id=?", (cid,))["secret"]
    db.execute("UPDATE credentials SET secret=? WHERE id=?", (openrouter.encrypt("sk-or-new"), cid))
    openrouter.disable_credential(cid, secret=dispatched)
    assert db.fetchone("SELECT disabled FROM credentials WHERE id=?", (cid,))["disabled"] == 0
    current = db.fetchone("SELECT secret FROM credentials WHERE id=?", (cid,))["secret"]
    openrouter.disable_credential(cid, secret=current)
    assert db.fetchone("SELECT disabled FROM credentials WHERE id=?", (cid,))["disabled"] == 1
