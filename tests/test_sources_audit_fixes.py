"""Source-layer regressions from the audit (no network, no pooled models)."""
import asyncio
import json
from unittest.mock import patch

import httpx
import pytest

from app import accounts
from app.proxy import openai_chat
from app.sources import chatgpt_stream, claude_catalog, kimi, ollama, openrouter, usage_walk


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


@pytest.mark.usefixtures("isolated_db")
def test_stale_oauth_rejection_does_not_disable_a_relinked_credential():
    from app import db
    from app.sources import grok

    sid = grok.ensure_grok_source()
    user = accounts.create_user("grokkey@test.invalid", "password12")
    cid = accounts.add_credential(sid, "mine", "at-old", extra={"refresh_token": "rt-old"}, donated_by=user["id"])
    dispatched = db.fetchone("SELECT extra FROM credentials WHERE id=?", (cid,))["extra"]
    db.execute("UPDATE credentials SET extra=? WHERE id=?", (accounts.encrypt(json.dumps({"refresh_token": "rt-new"})), cid))
    grok.disable_credential(cid, extra=dispatched)
    assert db.fetchone("SELECT disabled FROM credentials WHERE id=?", (cid,))["disabled"] == 0
    current = db.fetchone("SELECT extra FROM credentials WHERE id=?", (cid,))["extra"]
    grok.disable_credential(cid, extra=current)
    assert db.fetchone("SELECT disabled FROM credentials WHERE id=?", (cid,))["disabled"] == 1


@pytest.mark.usefixtures("isolated_db")
def test_metadata_write_from_a_stale_copy_keeps_rotated_tokens():
    from app import db
    from app.sources import kimi

    sid = kimi.ensure_kimi_source()
    user = accounts.create_user("kimimeta@test.invalid", "password12")
    cid = accounts.add_credential(sid, "mine", "at-old", extra={"access_token": "at-old", "refresh_token": "rt-old"},
                                  donated_by=user["id"])
    stale = accounts.cred_extra(db.fetchone("SELECT extra FROM credentials WHERE id=?", (cid,)))
    # A refresh rotates the pair while the billing fetch is in flight.
    db.execute("UPDATE credentials SET extra=? WHERE id=?",
               (accounts.encrypt(json.dumps({"access_token": "at-new", "refresh_token": "rt-new"})), cid))
    stale["billing"] = {"checked_at": "now"}
    accounts.update_extra(cid, stale, last_usage_percent=12.5)
    row = db.fetchone("SELECT extra, last_usage_percent FROM credentials WHERE id=?", (cid,))
    extra = accounts.cred_extra(row)
    assert (extra["access_token"], extra["refresh_token"]) == ("at-new", "rt-new")
    assert extra["billing"] == {"checked_at": "now"}
    assert row["last_usage_percent"] == 12.5


@pytest.mark.usefixtures("isolated_db")
def test_only_the_database_that_owns_the_tokens_refreshes_them():
    from urllib.parse import urlsplit

    from app import db
    from app.config import settings
    from app.sources import token_store

    schema = urlsplit(settings.database_url).path.strip("/")
    for owner, expected in ((None, False), ("fuse_live", False), (schema, True)):
        db.execute("DELETE FROM schema_meta WHERE `key`=?", (token_store.OWNER_KEY,))
        if owner:
            db.execute("INSERT INTO schema_meta(`key`, value) VALUES(?, ?)", (token_store.OWNER_KEY, owner))
        with patch.object(settings, "oauth_refresh_enabled", True):
            assert token_store.claim_refresh_ownership() is expected
            assert settings.oauth_refresh_enabled is expected
    # The kill switch wins even for the owner.
    with patch.object(settings, "oauth_refresh_enabled", False):
        assert token_store.claim_refresh_ownership() is False


@pytest.mark.parametrize("module", ["chatgpt", "claude", "grok", "kimi"])
def test_oauth_refresh_is_off_where_the_instance_does_not_own_the_tokens(module):
    import importlib

    from app.config import settings

    mod = importlib.import_module(f"app.sources.{module}")
    with patch.object(settings, "oauth_refresh_enabled", False), \
            patch.object(mod.db, "fetchone", side_effect=AssertionError("must not read the credential")):
        assert asyncio.run(mod._refresh_one_unlocked({"id": 1})) is False


def test_prefixed_pool_outage_does_not_mark_a_bare_id_unavailable():
    import app.sources as sources

    saved = set(sources._unavailable_types)
    try:
        sources._unavailable_types.clear()
        sources._unavailable_types.add("grok")
        assert sources.catalog_unavailable_for("my-local-alias") is False
        # An empty listing is still explained by any outage (/v1/models 503).
        assert sources.catalog_unavailable_for("") is True
        assert sources.catalog_unavailable_for("grok-4.6") is True
        sources._unavailable_types.add("llama")
        assert sources.catalog_unavailable_for("my-local-alias") is True
    finally:
        sources._unavailable_types.clear()
        sources._unavailable_types.update(saved)


def test_pool_prefixes_win_over_the_openrouter_slash_test():
    import app.sources as sources

    saved = set(sources._unavailable_types)
    try:
        sources._unavailable_types.clear()
        sources._unavailable_types.add("openrouter")
        assert sources.catalog_unavailable_for("claude/claude-opus-4.6") is False
        assert sources.catalog_unavailable_for("devin/gpt-6-astra") is False
        assert sources.catalog_unavailable_for("author/slug") is True
        sources._unavailable_types.add("claude")
        assert sources.catalog_unavailable_for("claude/claude-opus-4.6") is True
    finally:
        sources._unavailable_types.clear()
        sources._unavailable_types.update(saved)


def test_kimi_catalog_keeps_metadata_for_retained_ids():
    saved = (kimi._models_cache, dict(kimi._catalog_meta))
    try:
        kimi._models_cache, kimi._catalog_meta = None, {}
        kimi._remember_catalog([{"id": "kimi-k2", "name": "Kimi K2", "window": 131072, "efforts": ("low", "high")}])
        assert kimi.model_window("kimi-k2") == 131072
        kimi._remember_catalog([{"id": "kimi-k3", "name": "Kimi K3", "window": 262144}])
        assert "kimi-k2" in (kimi._models_cache[1] if kimi._models_cache else [])
        assert kimi.model_window("kimi-k2") == 131072
        assert kimi.model_label("kimi-k2") == "Kimi K2"
    finally:
        kimi._models_cache, kimi._catalog_meta = saved


def test_openrouter_catalog_lists_a_duplicate_id_once():
    payload = {"data": [
        {"id": "vendor/model:free", "name": "M", "pricing": {"prompt": "0", "completion": "0"}},
        {"id": "vendor/model:free", "name": "M", "pricing": {"prompt": "0.000002", "completion": "0.00001"}},
    ]}

    class _Response:
        def raise_for_status(self):
            return None

        def json(self):
            return payload

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, *args, **kwargs):
            return _Response()

    saved = (openrouter._catalog, openrouter._catalog_available)

    async def run():
        with patch.object(openrouter.httpx, "AsyncClient", _Client):
            return await openrouter.fetch_catalog(force=True)

    try:
        openrouter._catalog = None
        catalog = asyncio.run(run())
    finally:
        openrouter._catalog, openrouter._catalog_available = saved
    assert catalog["free_ids"].count("vendor/model:free") + catalog["paid_ids"].count("vendor/model:free") == 1
    listed_paid = "vendor/model:free" in catalog["paid_ids"]
    assert listed_paid is (catalog["by_id"]["vendor/model:free"]["free"] is False)
