"""Refuseless pool: live catalog, private-by-default keys, payloads, failover."""
from __future__ import annotations

import asyncio
import json
import time

import httpx
import pytest

from app import accounts, db, sources
from app.proxy import usage_from_obj
from app.sources import pool_pick, pool_policy, pool_session, refuseless

pytestmark = pytest.mark.usefixtures("isolated_db")
_REAL_CLIENT = httpx.AsyncClient
GLM = "glm-5.3-abliterated"
FLASH = "glm-5.3-flash-abliterated"
CATALOG = {
    "object": "list",
    "data": [
        {
            "id": GLM, "object": "model", "owned_by": "refuseless", "abliterated": True, "context_length": 1048576,
            "reasoning_efforts": ["off", "low", "high", "max"], "reasoning_default": "high", "max_output_tokens": 131072,
            "input_modalities": ["text"],
            "pricing": {
                "currency": "USD", "input_per_m": 0.3, "cached_input_per_m": 0.06, "output_per_m": 1.2,
                "discounted": True, "regular": {"input_per_m": 0.4, "cached_input_per_m": 0.08, "output_per_m": 1.6},
                "long_context": {"threshold": 200000, "input_per_m": 0.6, "cached_input_per_m": 0.12, "output_per_m": 2.4},
            },
        },
        {"id": FLASH, "object": "model", "abliterated": True, "context_length": 1048576, "input_modalities": ["text", "image"]},
        {"id": "embed-1", "object": "model", "abliterated": False, "type": "embedding"},
    ],
}
CREDITS = {"object": "credits", "balance": 42.5, "reserved": 1.2, "available": 41.3, "key_limit": None, "unit": "usd"}


def completion(content="ok"):
    return {
        "id": "chatcmpl-1", "object": "chat.completion", "model": GLM,
        "choices": [{"index": 0, "message": {"role": "assistant", "content": content, "reasoning_content": "hm"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 5, "total_tokens": 105, "prompt_tokens_details": {"cached_tokens": 60}},
    }


@pytest.fixture(scope="module")
def users(isolated_db):
    return [accounts.create_user(f"refuseless-{n}@fixture.test", "synthetic-password", admin=False)["id"] for n in range(3)]


@pytest.fixture
def backend(users, monkeypatch):
    pool_pick.reset_health()
    monkeypatch.setattr(pool_session, "persist", False)
    pool_session.reset_store()
    sid = refuseless.ensure_refuseless_source()
    db.execute("DELETE FROM credentials WHERE source_id=?", (sid,))
    db.execute("UPDATE users SET pool_prefs='{}'")
    pool_policy._share_cache.clear()
    pool_policy.set_admin_policy("refuseless", pool_shared=True, open_access=False)
    monkeypatch.setattr(refuseless, "_catalog", (time.time(), refuseless.parse_catalog(CATALOG)))
    monkeypatch.setattr(refuseless, "_catalog_ok", True)
    monkeypatch.setattr(refuseless, "_catalog_lock", None)
    monkeypatch.setattr(refuseless, "_refresh_locks", {})
    for name in ("_cache", "_model_cache", "_refreshing"):
        monkeypatch.setattr(sources, name, {})
    monkeypatch.setattr(sources, "_live", set())
    src = refuseless.RefuselessSource({"id": sid, "name": "Refuseless"}, {}, [])
    return src, sid, users


@pytest.fixture
def network(monkeypatch):
    requests: list[httpx.Request] = []

    def install(handler):
        async def dispatch(request):
            requests.append(request)
            return handler(request)

        monkeypatch.setattr(
            refuseless.httpx, "AsyncClient", lambda **kwargs: _REAL_CLIENT(transport=httpx.MockTransport(dispatch), **kwargs)
        )
        return requests

    return install


def link(sid, uid, key, *, available=41.3):
    cid = refuseless.save_key(sid, key, uid)
    refuseless.store_billing(cid, refuseless.parse_credits({**CREDITS, "available": available}))
    return cid


def _chat(src, model, uid, **extra):
    body = {"model": model, "messages": [{"role": "user", "content": "hi"}], **extra}
    return asyncio.run(src.chat(body, user_id=uid, session_id=f"s-{uid}-{model}"))


def test_catalog_keeps_chat_models_with_efforts_windows_and_vision():
    by_id = refuseless.parse_catalog(CATALOG)["by_id"]
    assert list(by_id) == [GLM, FLASH]
    assert by_id[GLM]["reasoning_efforts"] == ["none", "low", "high", "max"]
    assert by_id[GLM]["reasoning_default"] == "high"
    assert by_id[GLM]["context_length"] == 1048576 and by_id[GLM]["max_output_tokens"] == 131072
    assert by_id[GLM]["image"] is False and by_id[FLASH]["image"] is True
    assert by_id[FLASH]["reasoning_efforts"] == []


def test_catalog_is_read_live_without_a_key(backend, network):
    src, sid, (a, _b, _c) = backend
    link(sid, a, "abl_live_key_a")
    refuseless._catalog = None
    newer = {"data": [*CATALOG["data"], {"id": "qwen-4-abliterated", "context_length": 262144}]}
    requests = network(lambda r: httpx.Response(200, json=newer))
    names = asyncio.run(src.models())
    assert "refuseless/qwen-4-abliterated" in names and f"refuseless/{GLM}" in names
    assert str(requests[-1].url) == "https://api.refuseless.com/v1/models"
    # A key would add its account's private models and custom prices to the
    # copy every user shares.
    assert "authorization" not in requests[-1].headers
    # A failed refresh keeps the last lineup instead of emptying the picker.
    network(lambda r: httpx.Response(503, json={"error": {"code": "gateway_down"}}))
    assert asyncio.run(refuseless.fetch_catalog(force=True))["by_id"].keys() == refuseless.parse_catalog(newer)["by_id"].keys()


def test_blocked_first_read_lists_the_documented_lineup(backend, network):
    src, sid, (a, _b, _c) = backend
    link(sid, a, "abl_live_key_a")
    refuseless._catalog = None
    network(lambda r: httpx.Response(403, text="<html>Attention Required! | Cloudflare</html>"))
    names = asyncio.run(src.models())
    assert f"refuseless/{GLM}" in names and "refuseless/minimax-m3.1-flash" in names
    assert refuseless.efforts_for(f"refuseless/{GLM}") == ("low", "high", "max")
    assert refuseless.catalog_record(GLM)["pricing"] == {}
    assert asyncio.run(refuseless.resolve(f"refuseless/{GLM}", a)) == GLM


def test_payload_maps_effort_clamps_output_and_keeps_tools():
    rec = refuseless.parse_catalog(CATALOG)["by_id"][GLM]
    tools = [{"type": "function", "function": {"name": "f", "parameters": {"type": "object"}}}]
    body = {
        "model": f"refuseless/{GLM}", "stream": True, "tools": tools, "tool_choice": "auto",
        "messages": [{"role": "user", "content": [{"type": "text", "text": "hi", "cache_control": {"type": "ephemeral"}}], "_fuse_x": 1}],
        "reasoning_effort": "medium", "max_tokens": 10_000_000, "prompt_cache_key": "thread:1", "_fuse_meta": {},
    }
    sent = refuseless.chat_payload(body, GLM, rec)
    assert sent["model"] == GLM and sent["reasoning_effort"] == "high"
    assert sent["max_tokens"] == 131072 and sent["tools"] == tools and sent["tool_choice"] == "auto"
    assert "prompt_cache_key" not in sent and "_fuse_meta" not in sent
    assert sent["messages"] == [{"role": "user", "content": [{"type": "text", "text": "hi"}]}]
    assert refuseless.chat_payload({**body, "reasoning_effort": "none"}, GLM, rec)["reasoning_effort"] == "off"
    assert refuseless.chat_payload({**body, "reasoning_effort": "xhigh"}, GLM, rec)["reasoning_effort"] == "max"
    # No caller limit: Refuseless defaults to the model's full output.
    assert "max_tokens" not in refuseless.chat_payload({k: v for k, v in body.items() if k != "max_tokens"}, GLM, rec)
    # A model with no listed efforts gets no effort field (a guess is a 400).
    flash = refuseless.parse_catalog(CATALOG)["by_id"][FLASH]
    assert "reasoning_effort" not in refuseless.chat_payload(body, FLASH, flash)


def test_catalog_prices_are_usd_per_million_with_a_long_context_tier(backend):
    from app import pricing

    values = pricing._pick_reference({}, "refuseless", GLM)
    assert values["input_rate"] == 300_000 and values["cached_rate"] == 60_000 and values["output_rate"] == 1_200_000
    assert values["tiers"] == [{
        "min_prompt_tokens": 200_001, "input_rate": 600_000, "cached_rate": 120_000,
        "cache_write_rate": None, "output_rate": 2_400_000,
    }]
    assert values["openrouter_id"] is None
    # An unpriced model (no pricing object) stays unpriced, never a guess.
    assert pricing._pick_reference({}, "refuseless", FLASH) is None
    price = {"input_micros_per_million": 300_000, "cached_input_micros_per_million": 60_000, "cached_input_known": 1,
             "output_micros_per_million": 1_200_000, "tiers_json": json.dumps(values["tiers"])}
    # The docs' formula: uncached input, cached input and output at the flat
    # rate, and the whole request at the long rate above the threshold.
    def cost(prompt, cached, output):
        return db._estimated_cost(price, prompt, output, output, 0, cached, 0)

    assert cost(100_000, 10_000, 1_000) == 27_000 + 600 + 1_200
    assert cost(200_000, 0, 0) == 60_000
    assert cost(200_001, 0, 1_000) == 120_001 + 2_400


def test_usage_reads_cached_tokens():
    usage = usage_from_obj(completion())
    assert usage["prompt_tokens"] == 100 and usage["cached_tokens"] == 60


def test_keys_are_private_until_their_owner_shares(backend):
    _src, sid, (a, b, c) = backend
    ka = link(sid, a, "abl_key_aaaaaaaa")
    kb = link(sid, b, "abl_key_bbbbbbbb")
    rows = db.fetchall("SELECT * FROM credentials WHERE source_id=?", (sid,))
    assert {r["id"] for r in pool_policy.filter_credentials(rows, "refuseless", a)} == {ka}
    assert pool_policy.filter_credentials(rows, "refuseless", c) == []
    assert refuseless.user_has_refuseless(a) and not refuseless.user_has_refuseless(c)
    pool_policy.set_user_pref(b, "refuseless", share="everyone")
    pool_policy.set_user_pref(a, "refuseless", share="everyone")
    assert {r["id"] for r in pool_policy.filter_credentials(rows, "refuseless", a)} == {ka, kb}
    # Open access reaches only shared keys, and only users who chose the pool.
    pool_policy.set_admin_policy("refuseless", pool_shared=True, open_access=True)
    assert not refuseless.user_has_refuseless(c)
    pool_policy.set_user_pref(c, "refuseless", share="everyone")
    assert refuseless.user_has_refuseless(c)


def test_listing_and_routing_follow_access(backend):
    _src, sid, (a, _b, c) = backend
    link(sid, a, "abl_key_aaaaaaaa")
    names = asyncio.run(sources.models_union(allow_refuseless=True, user_id=a))
    assert f"refuseless/{GLM}" in names
    assert asyncio.run(sources.models_union(allow_refuseless=False, user_id=c)) == []
    picked = asyncio.run(sources.pick(f"refuseless/{GLM}", allow_refuseless=True, user_id=a))
    assert picked and picked[0].type_id == "refuseless" and picked[1] == GLM
    # Never mistaken for a Z.AI glm- id or an OpenRouter author/slug.
    assert asyncio.run(sources.pick(f"refuseless/{GLM}", allow_zai=True, user_id=c)) is None
    assert sources.reasoning_efforts("refuseless", f"refuseless/{GLM}") == ("none", "low", "high", "max")


def test_owner_key_serves_and_conversation_stays_pinned(backend, network):
    src, sid, (a, _b, _c) = backend
    ka = link(sid, a, "abl_key_aaaaaaaa")
    requests = network(lambda r: httpx.Response(200, json=completion(), headers={"x-request-id": "rq-1"}))
    result = _chat(src, f"refuseless/{GLM}", a, reasoning_effort="low")
    assert result.status == 200 and result.credential_id == ka
    assert str(requests[-1].url) == "https://api.refuseless.com/v1/chat/completions"
    assert json.loads(requests[-1].content)["reasoning_effort"] == "low"
    assert result.usage["provider_request_id"] == "rq-1"
    assert pool_session.lookup("refuseless", f"s-{a}-refuseless/{GLM}") == ka


def test_borrowed_key_gets_only_plain_fields(backend, network):
    src, sid, (a, b, _c) = backend
    ka = link(sid, a, "abl_key_aaaaaaaa")
    pool_policy.set_user_pref(a, "refuseless", share="everyone")
    pool_policy.set_user_pref(b, "refuseless", share="everyone")  # a private owner uses only their own keys
    requests = network(lambda r: httpx.Response(200, json=completion()))
    fn = {"type": "function", "function": {"name": "f", "parameters": {"type": "object"}}}
    extra = {"n": 4, "service_tier": "priority", "tools": [fn, {"type": "web_search"}], "reasoning_effort": "high"}
    assert _chat(src, f"refuseless/{GLM}", b, **extra).credential_id == ka
    sent = json.loads(requests[-1].content)
    assert "n" not in sent and "service_tier" not in sent
    assert sent["tools"] == [fn] and sent["reasoning_effort"] == "high"
    # The owner's own key takes the request as sent.
    _chat(src, f"refuseless/{GLM}", a, **extra)
    sent = json.loads(requests[-1].content)
    assert sent["n"] == 4 and sent["service_tier"] == "priority" and len(sent["tools"]) == 2


def test_contributors_must_hold_a_key_with_credit(backend):
    _src, sid, (a, b, c) = backend
    ka = link(sid, a, "abl_key_aaaaaaaa")
    pool_policy.set_user_pref(a, "refuseless", share="contributors")
    kb = link(sid, b, "abl_key_bbbbbbbb", available=0)
    kc = link(sid, c, "abl_key_cccccccc")
    pool_policy.set_user_pref(b, "refuseless", share="everyone")
    pool_policy.set_user_pref(c, "refuseless", share="everyone")
    rows = db.fetchall("SELECT * FROM credentials WHERE source_id=?", (sid,))
    reach = lambda uid: {r["id"] for r in pool_policy.filter_credentials(rows, "refuseless", uid)}
    # b's key has no credit to lend, so b is no contributor.
    assert not pool_policy.contributes(b, "refuseless") and reach(b) == {kb, kc}
    assert pool_policy.contributes(c, "refuseless") and reach(c) == {ka, kb, kc}


def test_out_of_credits_moves_to_the_next_key(backend, network):
    src, sid, (a, b, _c) = backend
    ka = link(sid, a, "abl_key_aaaaaaaa")
    kb = link(sid, b, "abl_key_bbbbbbbb")
    pool_policy.set_user_pref(a, "refuseless", share="everyone")
    pool_policy.set_user_pref(b, "refuseless", share="everyone")

    def handler(request):
        if request.headers["authorization"].endswith("a" * 8):
            return httpx.Response(402, json={"error": {"code": "insufficient_credits", "message": "Out of credits."}})
        return httpx.Response(200, json=completion())

    network(handler)
    result = _chat(src, f"refuseless/{GLM}", a)
    assert result.status == 200 and result.credential_id == kb
    assert [att["credential_id"] for att in result.attempts] == [ka]
    assert refuseless.out_of_credits(accounts.cred_extra(db.fetchone("SELECT * FROM credentials WHERE id=?", (ka,))))


def test_credits_running_out_mid_stream_zero_the_key(backend, network):
    src, sid, (a, _b, _c) = backend
    ka = link(sid, a, "abl_key_aaaaaaaa")
    sse = (
        b'data: {"choices":[{"index":0,"delta":{"content":"insufficient_credits"}}]}\n\n'
        b'data: {"error":{"code":"insufficient_credits","message":"Credits ran out. Generation stopped."}}\n\n'
        b"data: [DONE]\n\n"
    )
    network(lambda r: httpx.Response(200, content=sse, headers={"content-type": "text/event-stream"}))

    async def drain():
        result = await src.chat({"model": f"refuseless/{GLM}", "messages": [{"role": "user", "content": "hi"}], "stream": True}, user_id=a)
        async for _chunk in result.stream:
            pass

    asyncio.run(drain())
    assert refuseless.out_of_credits(accounts.cred_extra(db.fetchone("SELECT * FROM credentials WHERE id=?", (ka,))))
    # A model merely writing the code in its answer is not the terminal event.
    assert not refuseless._ran_out_of_credits(sse.split(b"\n\n")[0])


def test_busy_model_is_not_blamed_on_the_key(backend, network):
    src, sid, (a, _b, _c) = backend
    ka = link(sid, a, "abl_key_aaaaaaaa")
    network(lambda r: httpx.Response(429, json={"error": {"code": "model_busy"}}, headers={"retry-after": "15"}))
    result = _chat(src, f"refuseless/{GLM}", a)
    assert result.status == 429 and result.credential_id == ka and not result.attempts
    assert not pool_pick.cooling_down(ka)


def test_rejected_key_is_disabled_on_the_second_strike(backend, network):
    src, sid, (a, _b, _c) = backend
    ka = link(sid, a, "abl_key_aaaaaaaa")
    network(lambda r: httpx.Response(401, json={"error": {"code": "invalid_key"}}))
    first = _chat(src, f"refuseless/{GLM}", a)
    assert first.status == 502 and "link a new one" in first.body["error"]["message"]
    pool_pick.reset_health()
    pool_pick._auth_strikes[ka] = time.time()
    _chat(src, f"refuseless/{GLM}", a)
    assert db.fetchone("SELECT disabled FROM credentials WHERE id=?", (ka,))["disabled"] == 1


def test_pasted_key_is_validated_with_credits_not_a_completion(backend, network):
    _src, sid, (a, b, _c) = backend
    requests = network(lambda r: httpx.Response(200, json=CREDITS))
    cid, err = asyncio.run(refuseless.add_key(sid, "  abl_new_key_1234  ", a, label="Main"))
    assert err == "" and cid
    assert all(r.url.path != "/v1/chat/completions" for r in requests)
    billing = accounts.cred_extra(db.fetchone("SELECT * FROM credentials WHERE id=?", (cid,)))["billing"]
    assert billing["available"] == 41.3 and billing["prepaid_cents"] == 4130
    _cid, err = asyncio.run(refuseless.add_key(sid, "abl_new_key_1234", b))
    assert "another account" in err
    network(lambda r: httpx.Response(401, json={"error": {"code": "invalid_key"}}))
    assert "rejected" in asyncio.run(refuseless.add_key(sid, "abl_bad_key_9999", b))[1]
