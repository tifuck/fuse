import asyncio
import json

import httpx
import pytest

from app.sources import grok_version as version


@pytest.fixture
def state(monkeypatch, tmp_path):
    monkeypatch.setattr(version, "CACHE_PATH", tmp_path / "version.json")
    monkeypatch.setattr(version, "_loaded", False)
    monkeypatch.setattr(version, "_version", None)
    monkeypatch.setattr(version, "_next_check", 0)
    monkeypatch.setattr(version, "_lock", asyncio.Lock())
    monkeypatch.setattr(version.settings, "grok_client_version_auto_refresh", True)
    clock = [2_000_000_000.0]
    monkeypatch.setattr(version.time, "time", lambda: clock[0])
    calls = []
    responses = []
    real_client = httpx.AsyncClient

    def handle(request):
        calls.append(str(request.url))
        status, text = responses.pop(0)
        return httpx.Response(status, text=text)

    monkeypatch.setattr(version.httpx, "AsyncClient", lambda **kw: real_client(
        transport=httpx.MockTransport(handle), **kw,
    ))
    return clock, calls, responses


def test_weekly_refresh_survives_restart_and_updates_headers(state, monkeypatch):
    from app.sources.grok import _auth_headers, _cli_get_headers

    clock, calls, responses = state
    responses.append((200, "1.0.47\n"))
    asyncio.run(version.refresh_due())
    assert calls == [version.URLS[0]]
    assert _auth_headers("test")["x-grok-client-version"] == "1.0.47"
    assert _cli_get_headers("test")["x-grok-client-version"] == "1.0.47"
    monkeypatch.setattr(version, "_loaded", False)
    monkeypatch.setattr(version, "_version", None)
    monkeypatch.setattr(version, "_next_check", 0)
    clock[0] += version.WEEK - 1
    asyncio.run(version.refresh_due())
    assert version.current_version() == "1.0.47"
    assert len(calls) == 1
    clock[0] += 1
    responses.append((200, "1.0.48"))
    asyncio.run(version.refresh_due())
    assert version.current_version() == "1.0.48"
    assert len(calls) == 2


def test_invalid_primary_uses_official_fallback(state):
    _, calls, responses = state
    responses.extend([(200, "<html>blocked</html>"), (200, "1.0.49")])
    asyncio.run(version.refresh_due())
    assert calls == list(version.URLS)
    assert version.current_version() == "1.0.49"


def test_failure_preserves_cache_and_retries_after_hour(state):
    clock, calls, responses = state
    cached = {"version": "1.0.47", "checked_at": clock[0] - version.WEEK}
    version.CACHE_PATH.write_text(json.dumps(cached))
    responses.extend([(503, "unavailable"), (200, "1.0.50-alpha")])
    asyncio.run(version.refresh_due())
    assert version.current_version() == "1.0.47"
    assert json.loads(version.CACHE_PATH.read_text()) == cached
    clock[0] += version.RETRY - 1
    asyncio.run(version.refresh_due())
    assert len(calls) == 2
    clock[0] += 1
    responses.append((200, "1.0.50"))
    asyncio.run(version.refresh_due())
    assert version.current_version() == "1.0.50"


def test_explicit_pin_skips_network_and_cache(state, monkeypatch):
    _, calls, _ = state
    monkeypatch.setattr(version.settings, "grok_client_version_auto_refresh", False)
    monkeypatch.setattr(version.settings, "grok_client_version", "1.0.13")
    asyncio.run(version.refresh_due())
    assert version.current_version() == "1.0.13"
    assert not calls


@pytest.mark.parametrize("cache", ['broken', '[]', '{"version":"bad"}'])
def test_corrupt_cache_recovers(state, cache):
    _, calls, responses = state
    version.CACHE_PATH.write_text(cache)
    assert version.current_version() == version.settings.grok_client_version
    responses.append((200, "1.0.46"))
    asyncio.run(version.refresh_due())
    assert len(calls) == 1
    assert version.current_version() == "1.0.46"
