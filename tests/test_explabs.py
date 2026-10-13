"""Experiential Labs keys, catalogs and explicit routes stay private to their owner."""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from app import accounts, context_meter as context, db, model_caps, sources
from app.crypto import encrypt
from app.sources import explabs, pool_pick

pytestmark = pytest.mark.usefixtures("isolated_db")
_REAL_CLIENT = httpx.AsyncClient
MODEL = "vendor/synthetic-model"
KEY = "synthetic-explabs-key"


@pytest.fixture(scope="module")
def users(isolated_db):
    return [accounts.create_user(f"explabs-{n}@fixture.test", "synthetic-password", admin=False)["id"] for n in range(2)]


@pytest.fixture
def backend(users, monkeypatch):
    pool_pick.reset_health()  # 401 strikes are per credential id
    sid = explabs.ensure_explabs_source()
    db.execute("DELETE FROM credentials WHERE source_id=?", (sid,))
    db.execute("DELETE FROM model_routes")
    monkeypatch.setattr(explabs, "_catalogs", {})
    monkeypatch.setattr(explabs, "_locks", {})
    monkeypatch.setattr(explabs, "_errors", {})
    for name in ("_cache", "_model_cache", "_refreshing"):
        monkeypatch.setattr(sources, name, {})
    monkeypatch.setattr(sources, "_live", set())
    src = explabs.ExperientialLabsSource({"id": sid, "name": "Experiential Labs"}, {}, [])
    return src, users[0], users[1]


@pytest.fixture
def network(monkeypatch):
    requests = []
    clients = []

    def install(handler):
        async def dispatch(request):
            requests.append(request)
            response = handler(request)
            return await response if hasattr(response, "__await__") else response

        def client(**kwargs):
            instance = _REAL_CLIENT(transport=httpx.MockTransport(dispatch), **kwargs)
            clients.append(instance)
            return instance

        monkeypatch.setattr(explabs.httpx, "AsyncClient", client)
        return requests, clients

    return install


def catalog_response(request, models=(MODEL,)):
    if str(request.url) == explabs.BASE_URL + "/models":
        return httpx.Response(200, json={"data": [{"id": mid} for mid in models]})
    assert str(request.url) == explabs.CATALOG_URL
    return httpx.Response(200, json={"models": []})


async def connect(src, uid, key=KEY):
    cred_id, error = await explabs.add_user_key(src.source_id, key, uid)
    assert cred_id is not None and error == ""
    return cred_id


def test_personal_key_add_replace_remove_is_encrypted(backend, network):
    src, uid, other = backend
    requests, _ = network(catalog_response)

    async def run():
        cid = await connect(src, uid, "  " + KEY + "  ")
        row = explabs.user_credential(uid)
        assert row["secret"] != KEY and KEY not in row["secret"]
        assert accounts.cred_secret(row) == KEY
        assert accounts.cred_extra(row) == {"role": "user"}
        assert row["extra"] != json.dumps({"role": "user"})
        assert explabs.user_credential(other) is None
        assert explabs.user_credential(None) is None
        assert await src.models(user_id=other) == []
        assert await src.models(user_id=uid) == ["explabs/" + MODEL]
        replacement, error = await explabs.add_user_key(src.source_id, "synthetic-replacement", uid, label="Replacement")
        assert replacement == cid and error == ""
        row = explabs.user_credential(uid)
        assert accounts.cred_secret(row) == "synthetic-replacement"
        assert row["label"] == "Replacement"
        assert len(db.fetchall("SELECT id FROM credentials WHERE source_id=? AND donated_by=? AND disabled=0", (src.source_id, uid))) == 1
        explabs.disable_credential(cid)
        assert explabs.user_credential(uid) is None
        assert explabs.cached_catalog(uid) is None
        assert await src.models(user_id=uid) == []
        assert (await src.chat({"model": "explabs/" + MODEL, "messages": []}, user_id=uid)).status == 403

    asyncio.run(run())
    assert all(request.method == "GET" for request in requests)
    assert {request.headers["authorization"] for request in requests} == {"Bearer " + KEY, "Bearer synthetic-replacement"}


@pytest.mark.parametrize("status", [401, 402, 429, 503])
def test_failed_replacement_preserves_working_key_and_catalog(backend, network, status):
    src, uid, _ = backend
    network(catalog_response)

    async def run():
        cid = await connect(src, uid)
        before = dict(explabs.user_credential(uid))
        cached = explabs.cached_catalog(uid)
        network(lambda request: httpx.Response(status, json={"error": {"message": "synthetic refusal"}}))
        replacement, error = await explabs.add_user_key(src.source_id, "synthetic-bad-replacement", uid)
        assert replacement is None and error
        assert dict(explabs.user_credential(uid)) == before
        assert explabs.user_credential(uid)["id"] == cid
        assert explabs.cached_catalog(uid) == cached

    asyncio.run(run())


def test_catalogs_are_authenticated_private_and_metadata_cannot_add_models(backend, network, monkeypatch):
    src, uid, other = backend

    def handler(request):
        key = request.headers["authorization"]
        if str(request.url) == explabs.CATALOG_URL:
            return httpx.Response(200, json={"models": [
                {"slug": MODEL, "display_name": "Synthetic model", "context_window": 12345},
                {"slug": "metadata-only", "display_name": "Not callable"},
            ]})
        return catalog_response(request, (MODEL,) if key == "Bearer " + KEY else ("other-private",))

    requests, _ = network(handler)
    monkeypatch.setattr(sources, "_sources", lambda **kwargs: [src])

    async def run():
        await connect(src, uid)
        await connect(src, other, "synthetic-other-key")
        assert await sources.models_union(user_id=uid) == ["explabs/" + MODEL]
        assert await sources.models_union(user_id=other) == ["explabs/other-private"]
        assert await sources.models_union(user_id=None) == []
        assert await sources.models_union(user_id=uid) == ["explabs/" + MODEL]
        assert explabs.cached_catalog(uid)["by_id"][MODEL]["context_length"] == 12345
        assert src.source_id not in sources._cache
        assert len(requests) == 4

    asyncio.run(run())


@pytest.mark.parametrize("mutation", ["replace", "remove"])
@pytest.mark.parametrize("status", [200, 401, 429])
def test_async_refresh_discards_results_from_changed_key(backend, network, mutation, status):
    src, uid, _ = backend
    network(catalog_response)

    async def run():
        cid = await connect(src, uid)
        started, release = asyncio.Event(), asyncio.Event()

        async def delayed(request):
            if str(request.url) == explabs.CATALOG_URL:
                return httpx.Response(200, json={"models": []})
            started.set()
            await release.wait()
            return httpx.Response(status, json={"data": [{"id": "stale-private"}]})

        network(delayed)
        refresh = asyncio.create_task(explabs.fetch_catalog(uid, force=True))
        await asyncio.wait_for(started.wait(), 2)
        if mutation == "replace":
            db.execute("UPDATE credentials SET secret=? WHERE id=?", (encrypt("synthetic-new-key"), cid))
        else:
            explabs.disable_credential(cid)
        release.set()
        assert await asyncio.wait_for(refresh, 2) == {"by_id": {}}
        assert explabs.cached_catalog(uid) is None
        assert not explabs.access_state(uid)["last_error"]
        if mutation == "replace":
            assert accounts.cred_secret(explabs.user_credential(uid)) == "synthetic-new-key"

    asyncio.run(run())


@pytest.mark.parametrize("status", [401, 402, 429])
def test_catalog_invalid_auth_disables_key_but_quota_keeps_it(backend, network, status):
    src, uid, _ = backend
    network(catalog_response)

    async def run():
        await connect(src, uid)
        network(lambda request: httpx.Response(status, json={"error": {"message": "synthetic refusal"}}))
        result = await explabs.fetch_catalog(uid, force=True)
        if status == 401:
            assert result == {"by_id": {}}
            # One 401 may be transient: the key survives until a second one.
            assert accounts.cred_secret(explabs.user_credential(uid)) == KEY
            await explabs.fetch_catalog(uid, force=True)
            assert explabs.user_credential(uid) is None
            assert explabs.cached_catalog(uid) is None
        else:
            assert MODEL in result["by_id"]
            assert accounts.cred_secret(explabs.user_credential(uid)) == KEY
        assert explabs.access_state(uid)["last_error"]

    asyncio.run(run())


def test_resolve_and_chat_degrade_when_first_catalog_fetch_fails(backend, network):
    """An unreachable catalog on the request path degrades instead of raising out of pick()."""
    src, uid, _ = backend
    network(catalog_response)

    async def run():
        await connect(src, uid)
        explabs.clear_catalog(uid)
        network(lambda request: httpx.Response(503, json={"error": {"message": "synthetic outage"}}))
        assert await explabs.resolve("explabs/" + MODEL, uid) is None
        result = await src.chat({"model": "explabs/" + MODEL, "messages": []}, user_id=uid)
        assert result.status == 502
        assert "HTTP 503" in result.body["error"]["message"]
        assert explabs.user_has_explabs(uid)

    asyncio.run(run())


def test_minimal_chat_body_has_no_provider_defaults_or_internal_fields(backend, network):
    src, uid, _ = backend
    messages = [{"role": "user", "content": "Synthetic hello"}]
    requests, clients = network(lambda request: catalog_response(request) if request.method == "GET" else httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": "hello"}}], "usage": {"prompt_tokens": 3, "completion_tokens": 2, "cost": 0.001}}))

    async def run():
        cid = await connect(src, uid)
        body = {"model": "explabs/" + MODEL, "messages": messages, "_fuse_private": "hidden", "session_id": "synthetic-session", "models": ["hidden"]}
        result = await src.chat(body, user_id=uid)
        assert result.status == 200 and result.credential_id == cid and result.model == MODEL
        assert result.usage["cost_micros"] == 1000
        assert body["model"] == "explabs/" + MODEL

    asyncio.run(run())
    posted = requests[-1]
    assert str(posted.url) == explabs.BASE_URL + "/chat/completions"
    assert posted.headers["authorization"] == "Bearer " + KEY
    assert json.loads(posted.content) == {"model": MODEL, "messages": messages}
    assert all(client.is_closed for client in clients)


@pytest.mark.parametrize("status", [401, 402, 429])
@pytest.mark.parametrize("stream", [False, True])
def test_chat_preserves_quota_errors_and_retry_after_not_invalid_auth(backend, network, status, stream):
    src, uid, _ = backend
    error = {"error": {"message": "Synthetic quota exhausted", "code": "quota_exceeded"}}
    requests, clients = network(lambda request: catalog_response(request) if request.method == "GET" else httpx.Response(status, json=error, headers={"Retry-After": "17"}))

    async def run():
        await connect(src, uid)
        result = await src.chat({"model": "explabs/" + MODEL, "messages": [], "stream": stream}, user_id=uid)
        assert result.stream is None
        assert result.response_headers == {"Retry-After": "17"}
        if status == 401:
            assert result.status == 502 and result.body["error"]["code"] == "invalid_key"
            # One 401 may be transient: the key survives until a second one.
            assert accounts.cred_secret(explabs.user_credential(uid)) == KEY
            again = await src.chat({"model": "explabs/" + MODEL, "messages": [], "stream": stream}, user_id=uid)
            assert again.status == 502
            assert explabs.user_credential(uid) is None
        else:
            assert result.status == status and result.body == error
            assert accounts.cred_secret(explabs.user_credential(uid)) == KEY

    asyncio.run(run())
    assert len([request for request in requests if request.method == "POST"]) == (2 if status == 401 else 1)
    assert all(client.is_closed for client in clients)


@pytest.mark.parametrize("error_frame", [False, True])
def test_stream_uses_real_transport_preserving_frames_cost_and_errors(backend, network, error_frame):
    src, uid, _ = backend
    frames = [{"choices": [{"delta": {"content": "synthetic"}}]}]
    if error_frame:
        frames.append({"error": {"message": "Synthetic streaming failure", "code": "quota_exceeded"}})
    frames.append({"choices": [], "usage": {"prompt_tokens": 7, "completion_tokens": 4, "total_tokens": 11, "cost": "0.0025"}})
    raw = b"".join(b"data: " + json.dumps(frame).encode() + b"\n\n" for frame in frames) + b"data: [DONE]\n\n"
    requests, clients = network(lambda request: catalog_response(request) if request.method == "GET" else httpx.Response(200, content=raw, headers={"Content-Type": "text/event-stream"}))

    async def run():
        cid = await connect(src, uid)
        result = await src.chat({"model": "explabs/" + MODEL, "messages": [], "stream": True}, user_id=uid)
        assert result.status == 200 and result.body is None and result.credential_id == cid
        assert b"".join([chunk async for chunk in result.stream]) == raw
        assert result.usage["cost_micros"] == 2500
        assert result.usage["total_tokens"] == 11
        assert result.usage["stream_status"] == ("failed" if error_frame else "complete")
        assert explabs.user_credential(uid) is not None

    asyncio.run(run())
    assert json.loads(requests[-1].content) == {"model": MODEL, "messages": [], "stream": True, "stream_options": {"include_usage": True}}
    assert all(client.is_closed for client in clients)


def test_explicit_namespace_cannot_be_overridden_by_generic_route_or_fallback(backend, network, monkeypatch):
    src, uid, other = backend
    network(catalog_response)
    generic = SimpleNamespace(source_id=999999, type_id="llama", cfg={"model": "local"})
    monkeypatch.setattr(sources, "all_enabled", lambda: [src, generic])
    monkeypatch.setattr(sources, "get", lambda sid: generic)
    monkeypatch.setattr(sources, "_sources", lambda **kwargs: [src, generic])
    fallback = AsyncMock(return_value=(generic, "wrong-upstream"))
    monkeypatch.setattr(sources.openrouter, "resolve", fallback)
    monkeypatch.setattr(sources, "_openrouter_source", lambda: generic)
    db.execute("INSERT INTO model_routes (model, source_id, upstream_model) VALUES (?,?,?)", ("explabs/" + MODEL, src.source_id, "wrong-upstream"))

    async def run():
        await connect(src, uid)
        assert await sources.pick("explabs/" + MODEL, user_id=uid) == (src, MODEL)
        assert await sources.pick("explabs/" + MODEL, user_id=other) is None
        assert await sources.pick("explabs/missing", user_id=uid) is None
        assert await sources.pick("explabs/" + MODEL, user_id=None) is None
        fallback.assert_not_awaited()

    asyncio.run(run())


@pytest.mark.parametrize("model", [MODEL, "unrelated-alias"])
def test_generic_routes_and_shared_cache_never_select_explabs(backend, network, monkeypatch, model):
    src, uid, _ = backend
    network(catalog_response)
    monkeypatch.setattr(sources, "all_enabled", lambda: [src])
    monkeypatch.setattr(sources, "get", lambda sid: src)
    monkeypatch.setattr(sources, "_sources", lambda **kwargs: [src])
    monkeypatch.setattr(sources, "_openrouter_source", lambda: None)
    sources._cache[src.source_id] = (float("inf"), [model])
    db.execute("INSERT INTO model_routes (model, source_id, upstream_model) VALUES (?,?,?)", (model, src.source_id, MODEL))

    async def run():
        await connect(src, uid)
        assert await explabs.resolve(model, uid) is None
        assert await sources.pick(model, user_id=uid) is None
        db.execute("DELETE FROM model_routes WHERE model=?", (model,))
        assert await sources.pick(model, user_id=uid) is None
        assert await explabs.resolve("explabs/" + MODEL, uid) == MODEL

    asyncio.run(run())


def test_nested_metadata_filters_outputs_and_uses_routable_reasoning_capabilities(backend, network):
    src, uid, _ = backend
    metadata = {"models": [
        {"model": {"slug": MODEL, "display_name": "Nested synthetic", "context_window": 32768,
                   "input_modalities": ["text", "image"], "output_modalities": ["text"],
                   "supported_params": {"temperature": True}},
         "providers": [
             {"capabilities": {"supported_reasoning_efforts": ["low", "high"]}},
             {"capabilities": {"supported_reasoning_efforts": ["high", "medium"]}},
             {"routable": False, "capabilities": {"supported_reasoning_efforts": ["blocked"]}},
             {"available_to_org": False, "capabilities": {"supported_reasoning_efforts": ["private"]}},
         ]},
        {"model": {"slug": "image-output", "output_modalities": ["image"]}, "providers": []},
        {"model": {"slug": "plain-text", "input_modalities": ["text"], "output_modalities": ["text"]},
         "providers": [{"capabilities": {"supported_reasoning_efforts": []}}]},
        {"model": {"slug": "metadata-only", "output_modalities": ["text"]}, "providers": []},
    ]}
    network(lambda request: httpx.Response(200, json=metadata) if str(request.url) == explabs.CATALOG_URL else catalog_response(request, (MODEL, "image-output", "plain-text")))

    async def run():
        await connect(src, uid)
        records = explabs.cached_catalog(uid)["by_id"]
        assert set(records) == {MODEL, "plain-text"}
        assert records[MODEL]["name"] == "Nested synthetic"
        assert records[MODEL]["context_length"] == 32768
        assert records[MODEL]["image"] is True
        assert records[MODEL]["supported_parameters"] == {"temperature": True}
        assert records[MODEL]["reasoning_efforts"] == ["low", "high", "medium"]
        assert records["plain-text"]["reasoning_efforts"] == []
        assert records["plain-text"]["image"] is False
        assert await explabs.resolve("explabs/image-output", uid) is None
        assert (await src.chat({"model": "explabs/image-output", "messages": []}, user_id=uid)).status == 404

    asyncio.run(run())


def test_empty_callable_catalog_replaces_previous_list_without_sticky_models(backend, network):
    src, uid, _ = backend
    network(catalog_response)

    async def run():
        await connect(src, uid)
        network(lambda request: catalog_response(request, ()))
        assert await explabs.fetch_catalog(uid, force=True) == {"by_id": {}}
        assert await src.models(user_id=uid) == []
        assert explabs.user_has_explabs(uid)
        assert await explabs.resolve("explabs/" + MODEL, uid) is None

    asyncio.run(run())


@pytest.mark.parametrize("payload", [None, [], {}, {"data": {}}, {"data": [None]}, {"data": [{"id": 3}]}, {"data": [{"id": " "}]}])
def test_malformed_callable_catalog_rejects_replacement_preserving_key(backend, network, payload):
    src, uid, _ = backend
    network(catalog_response)

    async def run():
        await connect(src, uid)
        before = dict(explabs.user_credential(uid))
        cached = explabs.cached_catalog(uid)
        network(lambda request: httpx.Response(200, content=json.dumps(payload).encode()))
        cid, error = await explabs.add_user_key(src.source_id, "synthetic-rejected-key", uid)
        assert cid is None and "invalid model catalog" in error
        assert dict(explabs.user_credential(uid)) == before
        assert explabs.cached_catalog(uid) == cached
        assert await explabs.fetch_catalog(uid, force=True) == cached
        assert "invalid model catalog" in explabs.access_state(uid)["last_error"]

    asyncio.run(run())


def test_message_internal_secrets_are_removed_without_mutating_history(backend, network):
    src, uid, _ = backend
    requests, _ = network(lambda request: catalog_response(request) if request.method == "GET" else httpx.Response(200, json={"choices": [{"message": {"content": "synthetic"}}]}))
    messages = [{"role": "user", "content": "Synthetic content", "_fuse_secret": "synthetic-message-secret", "_fuse_metadata": {"secret": "synthetic-nested-secret"}}]

    async def run():
        await connect(src, uid)
        result = await src.chat({"model": "explabs/" + MODEL, "messages": messages, "_fuse_secret": "synthetic-body-secret", "session_id": "synthetic-private-session"}, user_id=uid)
        assert result.status == 200

    asyncio.run(run())
    payload = json.loads(requests[-1].content)
    assert payload == {"model": MODEL, "messages": [{"role": "user", "content": "Synthetic content"}]}
    assert messages[0]["_fuse_secret"] == "synthetic-message-secret"
    assert b"synthetic-message-secret" not in requests[-1].content
    assert b"synthetic-body-secret" not in requests[-1].content
    assert b"synthetic-private-session" not in requests[-1].content


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("cost_fields, expected", [({}, None), ({"cost": 0}, 0)])
def test_zero_cost_is_not_absent_and_gateway_headers_are_captured(backend, network, stream, cost_fields, expected):
    src, uid, _ = backend
    usage = {"prompt_tokens": 2, "completion_tokens": 1, **cost_fields}
    headers = {"x-request-id": "synthetic-request", "x-gateway-provider": "synthetic-provider", "authorization": "synthetic-response-secret", "set-cookie": "synthetic-private-cookie"}

    def handler(request):
        if request.method == "GET":
            return catalog_response(request)
        if stream:
            raw = b"data: " + json.dumps({"choices": [], "usage": usage}).encode() + b"\n\ndata: [DONE]\n\n"
            return httpx.Response(200, content=raw, headers={**headers, "content-type": "text/event-stream"})
        return httpx.Response(200, json={"choices": [{"message": {"content": "synthetic"}}], "usage": usage}, headers=headers)

    _, clients = network(handler)

    async def run():
        await connect(src, uid)
        result = await src.chat({"model": "explabs/" + MODEL, "messages": [], "stream": stream}, user_id=uid)
        assert result.status == 200
        assert result.response_headers == {"x-request-id": "synthetic-request", "x-gateway-provider": "synthetic-provider"}
        if stream:
            assert b"[DONE]" in b"".join([chunk async for chunk in result.stream])
        assert result.usage["cost_micros"] == expected

    asyncio.run(run())
    assert all(client.is_closed for client in clients)


def test_image_capabilities_and_context_windows_use_only_owner_catalog(backend, network, monkeypatch):
    src, uid, other = backend

    def handler(request):
        if str(request.url) != explabs.CATALOG_URL:
            return catalog_response(request)
        owner = request.headers["authorization"] == "Bearer " + KEY
        return httpx.Response(200, json={"models": [{"model": {
            "slug": MODEL, "context_window": 32768 if owner else 65536,
            "input_modalities": ["text", "image"] if owner else ["text"], "output_modalities": ["text"],
        }, "providers": []}]})

    network(handler)
    monkeypatch.setattr(model_caps, "_seen", {("explabs", MODEL): True})
    monkeypatch.setattr(context, "_WINDOW_CACHE", {})
    messages = [{"role": "user", "content": [{"type": "text", "text": "Synthetic image"}, {"type": "image_url", "image_url": {"url": "data:image/png;base64,c3ludGhldGlj"}}]}]

    async def run():
        cid = await connect(src, uid)
        await connect(src, other, "synthetic-other-key")
        for model in (MODEL, "explabs/" + MODEL):
            assert model_caps.image_input("explabs", model, user_id=uid) is True
            assert model_caps.image_input("explabs", model, user_id=other) is False
            assert model_caps.image_input("explabs", model, user_id=None) is None
            owner_messages, owner_omitted = model_caps.prepare(messages, "explabs", model, user_id=uid)
            other_messages, other_omitted = model_caps.prepare(messages, "explabs", model, user_id=other)
            assert owner_messages == messages and owner_omitted == 0
            assert other_omitted == 1 and model_caps.OMITTED in other_messages[0]["content"]
            context.remember_window("explabs", model, 999999)
            assert context.window_for(model, "explabs", catalog=explabs.cached_catalog(uid)) == 32768
            assert context.window_for(model, "explabs", catalog=explabs.cached_catalog(other)) == 65536
            # Without a live catalog the remembered window beats the type default.
            assert context.window_for(model, "explabs") == 999999
        assert context.window_for("explabs/" + MODEL, catalog=explabs.cached_catalog(uid)) == 32768
        explabs.disable_credential(cid)
        assert model_caps.image_input("explabs", MODEL, user_id=uid) is None
        # The remembered window outlives the catalog (display metadata only).
        assert context.window_for("explabs/" + MODEL, catalog=explabs.cached_catalog(uid)) == 999999
        assert model_caps.image_input("explabs", MODEL, user_id=other) is False
        assert messages[0]["content"][1]["type"] == "image_url"

    asyncio.run(run())


def test_context_window_survives_cold_meter_recompute(backend, network, monkeypatch):
    """The chat meter recomputes windows without a live catalog (cold process,
    or the post-success recompute that uses the bare upstream id). Both id
    forms must return the gateway's real window, never the 128k type default,
    and an unknown slug must not inherit a pool's prefix window."""
    src, uid, _ = backend

    def handler(request):
        if str(request.url) == explabs.CATALOG_URL:
            return httpx.Response(200, json={"models": [{"model": {
                "slug": MODEL, "display_name": "Synthetic model", "context_window": 131072,
            }, "providers": []}]})
        return catalog_response(request)

    network(handler)
    monkeypatch.setattr(context, "_WINDOW_CACHE", {})

    async def run():
        await connect(src, uid)
        for model in (MODEL, "explabs/" + MODEL):
            assert context.window_for(model, "explabs") == 131072
            assert context.window_for(model, "explabs", catalog=None) == 131072
        assert context.window_for("glm-9-synthetic", "explabs") == context.TYPE_WINDOWS["explabs"]

    asyncio.run(run())


def test_api_run_chat_passes_owner_and_session_to_explabs():
    from app.api import _run_chat
    from app.sources.base import ChatResult

    result = ChatResult(status=200, body={"synthetic": True})
    src = SimpleNamespace(type_id="explabs", chat=AsyncMock(return_value=result))
    body = {"model": "explabs/" + MODEL, "messages": []}
    assert asyncio.run(_run_chat(src, body, 123, session_id="synthetic-session")) is result
    src.chat.assert_awaited_once_with(body, user_id=123, session_id="synthetic-session")


@pytest.mark.parametrize("started", [False, True])
def test_stream_early_close_releases_response_and_client_even_before_iteration(backend, network, started):
    src, uid, _ = backend

    class SyntheticStream(httpx.AsyncByteStream):
        def __init__(self):
            self.closed = False
            self.yielded = 0

        async def __aiter__(self):
            self.yielded += 1
            yield b'data: {"choices":[{"delta":{"content":"synthetic"}}]}\n\n'
            self.yielded += 1
            yield b"data: [DONE]\n\n"

        async def aclose(self):
            self.closed = True

    upstream_stream = SyntheticStream()
    response = httpx.Response(200, stream=upstream_stream, headers={"content-type": "text/event-stream"})
    _, clients = network(lambda request: catalog_response(request) if request.method == "GET" else response)

    async def run():
        await connect(src, uid)
        result = await src.chat({"model": "explabs/" + MODEL, "messages": [], "stream": True}, user_id=uid)
        assert result.status == 200 and result.stream is not None
        assert not response.is_closed and not clients[-1].is_closed
        if started:
            assert b"synthetic" in await anext(result.stream)
        assert upstream_stream.yielded == int(started)
        await result.stream.aclose()
        assert response.is_closed and upstream_stream.closed and clients[-1].is_closed
        await result.stream.aclose()
        assert upstream_stream.yielded == int(started)
        assert explabs.user_has_explabs(uid)

    asyncio.run(run())
    assert all(client.is_closed for client in clients)


@pytest.mark.parametrize("has_key", [False, True])
def test_api_invoke_explicit_explabs_requires_key(backend, monkeypatch, has_key):
    from fastapi import HTTPException
    from starlette.requests import Request
    from app import api
    from app.sources.base import ChatResult

    _, uid, _ = backend
    for kind in ("grok", "chatgpt", "zai", "devin", "kimi", "mimo", "claude"):
        monkeypatch.setattr(api, "user_has_" + kind, lambda user_id: False)
    monkeypatch.setattr(explabs, "user_has_explabs", lambda user_id: has_key)
    result = ChatResult(status=429, body={"error": {"message": "Synthetic quota exhausted"}})
    src = SimpleNamespace(type_id="explabs", source_id=123, chat=AsyncMock(return_value=result))
    pick = AsyncMock(return_value=(src, MODEL))
    monkeypatch.setattr(api, "pick", pick)
    monkeypatch.setattr(api, "_record_attempts", lambda *args: None)
    request = Request({"type": "http", "method": "POST", "path": "/v1/chat/completions", "headers": []})
    body = {"model": "explabs/" + MODEL, "messages": [{"role": "user", "content": "Synthetic hello"}]}

    async def run():
        if not has_key:
            with pytest.raises(HTTPException) as exc:
                await api.invoke_chat(request, {"id": uid}, body)
            assert exc.value.status_code == 403
            assert "Experiential Labs API key" in exc.value.detail
            pick.assert_not_awaited()
            src.chat.assert_not_awaited()
        else:
            invoked = await api.invoke_chat(request, {"id": uid}, body)
            assert invoked[-1] is result
            assert invoked[1] is src and invoked[2] == MODEL
            assert pick.await_args.kwargs["user_id"] == uid
            src.chat.assert_awaited_once_with({**body, "model": MODEL}, user_id=uid, session_id=None)

    asyncio.run(run())
