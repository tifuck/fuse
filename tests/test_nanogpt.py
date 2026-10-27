"""NanoGPT pool: sign-in, catalog, two billing lanes, payloads and usage."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import re
import time
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from starlette.requests import Request

from app import accounts, db, sources
from app.proxy import usage_from_obj
from app.sources import nanogpt, pool_pick, pool_policy, pool_session
from app.web.source_routes import nanogpt as routes

pytestmark = pytest.mark.usefixtures("isolated_db")
_REAL_CLIENT = httpx.AsyncClient
SUB = "z-ai/glm-5.3"
PAID = "anthropic/claude-opus-5.5"
CATALOG = {
    "data": [
        {
            "id": SUB, "name": "GLM 5.3", "subscription": {"included": True},
            "reasoning_efforts": ["high", "low", "max"], "context_length": 1048576, "max_output_tokens": 131072,
            "capabilities": {"vision": False}, "pricing": {"prompt": 0.6, "completion": 1.1, "cacheReadInputPer1kTokens": 0.0002},
        },
        {
            "id": PAID, "name": "Claude Opus 5.5", "subscription": {"included": False},
            "reasoning_efforts": ["low", "medium", "high", "xhigh", "max"], "max_output_tokens": 128000,
            "capabilities": {"vision": True},
            "pricing": {"prompt": 4, "completion": 20, "cacheReadInputPer1kTokens": 0.0002, "cacheWriteInputPer1kTokens": 0.005},
        },
        {"id": "image/only", "architecture": {"output_modalities": ["image"]}},
    ]
}


def usage_payload(*, allows_balance: bool, active: bool = True) -> dict:
    return {
        "active": active,
        "state": "active" if active else "inactive",
        "dailyInputTokens": {"used": 1, "remaining": 9, "percentUsed": 0.1, "resetAt": 1790000000000},
        "weeklyInputTokens": {"used": 3, "remaining": 1, "percentUsed": 1.4, "resetAt": 1790500000000},
        "period": {"currentPeriodEnd": None},
        "routing": {"recommendedMode": "subscription", "paidSpendPolicyAllowsBalance": allows_balance},
    }


def completion(content="ok", cost=0.0021):
    return {
        "id": "chatcmpl-1", "object": "chat.completion", "model": SUB,
        "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 5, "total_tokens": 105, "cache_read_input_tokens": 40},
        "x_nanogpt_pricing": {"cost": cost, "paymentSource": "USD"},
    }


@pytest.fixture(scope="module")
def users(isolated_db):
    return [accounts.create_user(f"nanogpt-{n}@fixture.test", "synthetic-password", admin=False)["id"] for n in range(3)]


@pytest.fixture
def backend(users, monkeypatch):
    pool_pick.reset_health()
    monkeypatch.setattr(pool_session, "persist", False)
    pool_session.reset_store()
    sid = nanogpt.ensure_nanogpt_source()
    db.execute("DELETE FROM credentials WHERE source_id=?", (sid,))
    db.execute("UPDATE users SET pool_prefs='{}'")
    pool_policy._share_cache.clear()
    pool_policy.set_admin_policy("nanogpt", pool_shared=True, open_access=False)
    monkeypatch.setattr(nanogpt, "_catalog", (time.time(), nanogpt.parse_catalog(CATALOG)))
    monkeypatch.setattr(nanogpt, "_providers", {})
    monkeypatch.setattr(nanogpt, "_refresh_locks", {})
    for name in ("_cache", "_model_cache", "_refreshing"):
        monkeypatch.setattr(sources, name, {})
    monkeypatch.setattr(sources, "_live", set())
    routes._nanogpt_pending.clear()
    routes._pool_flash.clear()
    src = nanogpt.NanoGPTSource({"id": sid, "name": "NanoGPT"}, {}, [])
    return src, sid, users


@pytest.fixture
def network(monkeypatch):
    requests: list[httpx.Request] = []

    def install(handler):
        async def dispatch(request):
            requests.append(request)
            return handler(request)

        monkeypatch.setattr(
            nanogpt.httpx, "AsyncClient", lambda **kwargs: _REAL_CLIENT(transport=httpx.MockTransport(dispatch), **kwargs)
        )
        return requests

    return install


def link(sid, uid, key, *, allows_balance, active=True):
    cid = nanogpt.save_key(sid, key, uid, auth_kind="oauth")
    nanogpt._store_billing(cid, nanogpt.parse_usage(usage_payload(allows_balance=allows_balance, active=active)))
    return cid


def make_request(path="/sources/nanogpt/callback", query=""):
    return Request({
        "type": "http", "method": "GET", "path": path, "query_string": query.encode(), "headers": [],
        "scheme": "https", "server": ("fuse.test", 443), "root_path": "",
    })


# --- Pure helpers -----------------------------------------------------------------

def test_catalog_keeps_text_models_with_lane_and_capabilities():
    by_id = nanogpt.parse_catalog(CATALOG)["by_id"]
    assert set(by_id) == {SUB, PAID}
    assert by_id[SUB]["subscription"] is True and by_id[PAID]["subscription"] is False
    assert by_id[SUB]["reasoning_efforts"] == ["low", "high", "max"]
    assert by_id[PAID]["image"] is True and by_id[SUB]["image"] is False
    assert by_id[SUB]["context_length"] == 1048576


@pytest.mark.parametrize("effort,efforts,want", [
    ("high", ("low", "high", "max"), "high"),
    ("medium", ("low", "high", "max"), "high"),
    ("xhigh", ("low", "high", "max"), "max"),
    ("minimal", ("none", "high"), "high"),
    ("none", ("low", "high"), None),
    ("none", ("none", "high"), "none"),
    ("high", (), None),
    ("bogus", ("high",), None),
])
def test_effort_maps_to_the_models_nearest_level(effort, efforts, want):
    assert nanogpt.map_effort(effort, efforts) == want


@pytest.mark.parametrize("raw,ok", [
    ("sk-nano-0123456789abcdef", True),
    ("  sk-nano-0123456789abcdef  ", True),
    ("123e4567-e89b-12d3-a456-426614174000", True),
    ("sk-nano-mgmt-0123456789", False),
    ("sk-or-v1-abc", False),
    ("sk-nano-with space", False),
    ("", False),
])
def test_key_shape(raw, ok):
    assert bool(nanogpt.key_shape(raw)) is ok


@pytest.mark.parametrize("uri,ok", [
    ("https://fuse.example/sources/nanogpt/callback", True),
    ("http://127.0.0.1:8089/sources/nanogpt/callback", True),
    ("http://127.0.0.1/sources/nanogpt/callback", False),
    ("http://fuse.example/sources/nanogpt/callback", False),
    ("https://fuse.example/cb#frag", False),
    ("https://user:pw@fuse.example/cb", False),
])
def test_redirect_rules_match_nanogpt(uri, ok):
    assert nanogpt.redirect_ok(uri) is ok


def test_start_login_is_s256_pkce():
    data = nanogpt.start_login("https://fuse.example/sources/nanogpt/callback")
    verifier = data["verifier"]
    assert 43 <= len(verifier) <= 128 and re.fullmatch(r"[A-Za-z0-9\-._~]+", verifier)
    url = urlparse(data["auth_url"])
    assert f"{url.scheme}://{url.netloc}{url.path}" == "https://nano-gpt.com/auth"
    q = {k: v[0] for k, v in parse_qs(url.query).items()}
    digest = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    assert q["code_challenge"] == digest and q["code_challenge_method"] == "S256"
    assert q["state"] == data["state"] and q["callback_url"].endswith("/sources/nanogpt/callback")
    assert "api.use" in q["scope"].split() and "invitation_code" not in q


def test_usage_reads_fractions_resets_and_billing_policy():
    parsed = nanogpt.parse_usage(usage_payload(allows_balance=False))
    assert [w["percent"] for w in parsed["windows"]] == [10.0, 100.0]  # 1.4 caps at 100
    assert parsed["credit_usage_percent"] == 100.0
    assert parsed["windows"][0]["reset_at"] == "2026-09-21T14:13:20Z"  # 1790000000000 ms
    assert parsed["allows_balance"] is False and parsed["active"] is True
    degraded = nanogpt.parse_usage({"active": True, "dailyInputTokens": {"percentUsed": None, "degraded": True}})
    assert degraded["windows"] == [] and degraded["credit_usage_percent"] is None
    assert degraded["allows_balance"] is None


def test_borrowed_payload_keeps_only_plain_chat_fields():
    rec = nanogpt.parse_catalog(CATALOG)["by_id"][SUB]
    body = {
        "model": "nanogpt/" + SUB, "stream": True, "max_tokens": 999_999, "reasoning_effort": "medium",
        "provider": {"only": ["x"]}, "billing_mode": "paygo", "prompt_caching": {"enabled": True},
        "caching": True, "service_tier": "fast", "web_search": {"enabled": True}, "session_id": "s", "_fuse_x": 1,
        "messages": [{"role": "user", "content": [{"type": "text", "text": "hi", "cache_control": {"type": "ephemeral"}}],
                      "claude_thinking": "x"}],
    }
    out = nanogpt.chat_payload(body, SUB, rec, owner=False)
    assert set(out) == {"model", "stream", "max_tokens", "reasoning_effort", "messages"}
    assert out["model"] == SUB and out["max_tokens"] == 131072 and out["reasoning_effort"] == "high"
    assert out["messages"] == [{"role": "user", "content": [{"type": "text", "text": "hi"}]}]
    assert body["messages"][0]["content"][0]["cache_control"]  # the caller's copy is untouched


def test_owner_payload_caches_paid_claude_and_applies_pins():
    rec = nanogpt.parse_catalog(CATALOG)["by_id"][PAID]
    body = {"model": PAID, "messages": [], "reasoning": {"effort": "none", "exclude": True}}
    out = nanogpt.chat_payload(body, PAID, rec, owner=True, prefs={"providers": {PAID: ["anthropic"]}})
    assert out["prompt_caching"] == {"enabled": True, "ttl": "1h"}
    assert out["provider"] == {"only": ["anthropic"], "allow_fallbacks": True}
    assert "reasoning_effort" not in out  # Opus has no "none"; the model keeps its default
    assert out["reasoning"] == {"exclude": True}


def test_usage_reads_nanogpt_cache_fields_and_cost():
    usage = usage_from_obj(completion())
    assert usage["cached_tokens"] == 40 and usage["cache_usage_status"] == "known"
    assert usage["cost_micros"] == 2100
    write = usage_from_obj({"usage": {"prompt_tokens": 100, "completion_tokens": 1, "cache_creation_input_tokens": 60}})
    assert write["cache_write_tokens"] == 60
    # A subscription-covered call reports 0: the estimate applies instead.
    assert usage_from_obj(completion(cost=0))["cost_micros"] is None
    plain = usage_from_obj({"usage": {"prompt_tokens": 5, "completion_tokens": 1, "cost": 0}})
    assert plain["cost_micros"] == 0


# --- Accounts, lanes and listing ------------------------------------------------------

def test_lent_keys_must_be_unable_to_spend_balance(backend):
    _src, sid, (a, b, c) = backend
    ka = link(sid, a, "sk-nano-aaaaaaaaaaaa", allows_balance=True)
    kb = link(sid, b, "sk-nano-bbbbbbbbbbbb", allows_balance=False)
    rows = db.fetchall("SELECT * FROM credentials WHERE source_id=? AND disabled=0", (sid,))
    reach = lambda uid: {int(r["id"]) for r in pool_policy.filter_credentials(rows, "nanogpt", uid)}
    assert reach(c) == {kb}
    assert reach(a) == {ka, kb}
    assert reach(b) == {kb}
    assert nanogpt.user_has_nanogpt(a) and nanogpt.user_has_nanogpt(b)
    assert not nanogpt.user_has_nanogpt(c)
    pool_policy.set_admin_policy("nanogpt", pool_shared=True, open_access=True)
    assert nanogpt.nanogpt_access_state(c) == {"ok": True, "reason": "open"}


def test_listing_follows_lane_and_the_users_switches(backend):
    _src, sid, (a, _b, c) = backend
    link(sid, a, "sk-nano-aaaaaaaaaaaa", allows_balance=True)
    names = ["nanogpt/" + SUB, "nanogpt/" + PAID, "nanogpt/gone"]
    assert nanogpt.listable_models(names, a) == ["nanogpt/" + SUB]
    nanogpt.set_paid_model(a, PAID, True)
    assert nanogpt.listable_models(names, a) == ["nanogpt/" + SUB, "nanogpt/" + PAID]
    # No own key: paid models are never listed, even when switched on.
    nanogpt._save_model_prefs(c, {"paid_all": True})
    assert nanogpt.listable_models(names, c) == ["nanogpt/" + SUB]
    nanogpt.set_model_switches(a, subscription=False, paid_all=True)
    assert nanogpt.listable_models(names, a) == ["nanogpt/" + PAID]
    nanogpt.set_model_switches(a, paid_all=False)
    assert nanogpt.model_prefs(a)["paid"] == []
    assert asyncio.run(nanogpt.resolve("nanogpt/" + PAID, a)) is None
    assert nanogpt.denied_reason("nanogpt/" + PAID, a).startswith("That paid NanoGPT model")
    # pool_policy's own settings survive model-list writes.
    pool_policy.set_user_pref(a, "nanogpt", share="private")
    nanogpt.set_paid_model(a, PAID, True)
    assert pool_policy.user_pref(a, "nanogpt")["share"] == "private"
    assert nanogpt.model_prefs(a)["paid"] == [PAID]


def test_same_key_stays_with_its_donor(backend):
    _src, sid, (a, b, _c) = backend
    cid = nanogpt.save_key(sid, "sk-nano-shared-key-1", a)
    with pytest.raises(ValueError):
        nanogpt.save_key(sid, "sk-nano-shared-key-1", b)
    nanogpt.disable_credential(cid)
    assert nanogpt.save_key(sid, "sk-nano-shared-key-1", a) == cid  # re-sign-in re-enables
    row = db.fetchone("SELECT * FROM credentials WHERE id=?", (cid,))
    assert not row["disabled"] and accounts.cred_secret(row) == "sk-nano-shared-key-1"
    assert "sk-nano" not in row["secret"]


# --- Chat routing ---------------------------------------------------------------------

def _chat(src, model, uid, **extra):
    body = {"model": model, "messages": [{"role": "user", "content": "hi"}], **extra}
    return asyncio.run(src.chat(body, user_id=uid, session_id=f"s-{uid}-{model}"))


def test_subscription_model_borrows_only_a_safe_key(backend, network):
    src, sid, (a, b, c) = backend
    link(sid, a, "sk-nano-aaaaaaaaaaaa", allows_balance=True)
    kb = link(sid, b, "sk-nano-bbbbbbbbbbbb", allows_balance=False)
    requests = network(lambda r: httpx.Response(200, json=completion(), headers={"x-request-id": "req-1"}))
    result = _chat(src, SUB, c, provider={"only": ["x"]}, billing_mode="paygo")
    assert result.status == 200 and result.credential_id == kb
    sent = json.loads(requests[-1].content)
    assert requests[-1].headers["authorization"] == "Bearer sk-nano-bbbbbbbbbbbb"
    assert str(requests[-1].url) == "https://api.nano-gpt.com/api/v1/chat/completions"
    assert "provider" not in sent and "billing_mode" not in sent
    assert result.usage["provider_request_id"] == "req-1"
    # A paid model never borrows: no key of the caller's own, no route. A
    # refusal (403), not an outage the chat would retry.
    assert _chat(src, PAID, c).status == 403


def test_owner_runs_paid_models_on_their_own_key_first(backend, network):
    src, sid, (a, b, _c) = backend
    ka = link(sid, a, "sk-nano-aaaaaaaaaaaa", allows_balance=True)
    link(sid, b, "sk-nano-bbbbbbbbbbbb", allows_balance=False)
    requests = network(lambda r: httpx.Response(200, json=completion()))
    result = _chat(src, PAID, a)
    assert result.credential_id == ka
    assert json.loads(requests[-1].content)["prompt_caching"] == {"enabled": True, "ttl": "1h"}
    assert _chat(src, SUB, a).credential_id == ka


def test_failures_move_to_the_next_safe_key(backend, network):
    src, sid, (a, b, c) = backend
    kb = link(sid, b, "sk-nano-bbbbbbbbbbbb", allows_balance=False)
    kc = link(sid, c, "sk-nano-cccccccccccc", allows_balance=False)

    def handler(request):
        if request.headers["authorization"].endswith("b" * 12):
            return httpx.Response(429, json={"error": {"code": "daily_rpd_limit_exceeded"}}, headers={"retry-after": "3600"})
        return httpx.Response(200, json=completion())

    network(handler)
    result = _chat(src, SUB, a)
    assert result.status == 200 and result.credential_id == kc
    assert [att["credential_id"] for att in result.attempts] == [kb]
    assert pool_pick.cooling_down(kb)
    network(lambda r: httpx.Response(401, json={"error": {"code": "invalid_api_key"}}))
    pool_pick.reset_health()
    first = _chat(src, SUB, a)
    assert first.status == 502 and "sign in again" in first.body["error"]["message"]
    pool_pick.reset_health()
    for cid in (kb, kc):
        pool_pick._auth_strikes[cid] = time.time()  # second strike disables
    _chat(src, SUB, a)
    assert db.fetchone("SELECT disabled FROM credentials WHERE id=?", (kb,))["disabled"] == 1


def test_gif_images_are_reencoded(monkeypatch):
    async def decode(data, **kwargs):
        assert data == b"GIF89a"
        return {"mime": "image/png", "data": "UE5H"}

    monkeypatch.setattr(nanogpt.file_images, "decode_image", decode)
    gif = "data:image/gif;base64," + base64.b64encode(b"GIF89a").decode()
    messages = [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": gif, "detail": "high"}}]}]
    out = asyncio.run(nanogpt.png_gifs(messages))
    assert out[0]["content"][0]["image_url"] == {"url": "data:image/png;base64,UE5H", "detail": "high"}
    assert messages[0]["content"][0]["image_url"]["url"] == gif


# --- Sign-in routes -------------------------------------------------------------------

@pytest.fixture
def signed_in(backend, monkeypatch):
    _src, _sid, (a, _b, _c) = backend
    user = {"id": a, "is_admin": False}
    monkeypatch.setattr(routes, "require_user", lambda request: (user, None))
    monkeypatch.setattr(routes.limiter, "allow", lambda *args: True)
    monkeypatch.setattr(routes.settings, "public_base_url", "https://fuse.example")
    return user


def test_oauth_round_trip_links_the_key(signed_in, network):
    def handler(request):
        if request.url.path == "/api/v1/auth/keys":
            body = json.loads(request.content)
            assert body["grant_type"] == "authorization_code" and body["code"] == "the-code"
            assert body["code_verifier"] == pending["verifier"]
            return httpx.Response(200, json={"key": "sk-nano-from-oauth-1", "user_id": "ngpt-user"})
        if request.url.path.endswith("/subscription/v1/usage"):
            return httpx.Response(200, json=usage_payload(allows_balance=False))
        if request.url.path.endswith("/check-balance"):
            assert request.headers["x-api-key"] == "sk-nano-from-oauth-1"
            return httpx.Response(200, json={"usd_balance": "12.5"})
        raise AssertionError(request.url)

    requests = network(handler)
    start = asyncio.run(routes.nanogpt_start(make_request("/sources/nanogpt/start")))
    assert start.status_code == 303 and start.headers["location"].startswith("https://nano-gpt.com/auth?")
    assert "callback_url=https%3A%2F%2Ffuse.example%2Fsources%2Fnanogpt%2Fcallback" in start.headers["location"]
    pending = dict(routes._nanogpt_pending[signed_in["id"]])
    # A forged callback neither links nor cancels the real sign-in.
    forged = asyncio.run(routes.nanogpt_callback(make_request(query="code=x&state=wrong")))
    assert forged.headers["location"] == "/sources#pool-nanogpt" and routes._nanogpt_pending.get(signed_in["id"])
    assert not requests
    done = asyncio.run(routes.nanogpt_callback(make_request(query=f"code=the-code&state={pending['state']}")))
    assert done.status_code == 303 and signed_in["id"] not in routes._nanogpt_pending
    assert routes._pool_flash[signed_in["id"]][0] == "ok"
    cred = db.fetchone("SELECT * FROM credentials WHERE donated_by=? AND disabled=0", (signed_in["id"],))
    assert accounts.cred_secret(cred) == "sk-nano-from-oauth-1"
    extra = accounts.cred_extra(cred)
    assert extra["account_id"] == "ngpt-user" and extra["billing"]["prepaid_cents"] == 1250
    assert nanogpt.shareable(extra)
    # The state is single use.
    again = asyncio.run(routes.nanogpt_callback(make_request(query=f"code=the-code&state={pending['state']}")))
    assert routes._pool_flash[signed_in["id"]][0] == "err" and again.status_code == 303


def test_denied_consent_clears_the_sign_in(signed_in, network):
    requests = network(lambda r: httpx.Response(500))
    asyncio.run(routes.nanogpt_start(make_request("/sources/nanogpt/start")))
    state = routes._nanogpt_pending[signed_in["id"]]["state"]
    asyncio.run(routes.nanogpt_callback(make_request(query=f"error=access_denied&state={state}")))
    assert signed_in["id"] not in routes._nanogpt_pending
    assert routes._pool_flash[signed_in["id"]] == ("err", "NanoGPT sign-in was cancelled.", "nanogpt")
    assert not requests


def test_plain_http_address_cannot_start_oauth(signed_in, monkeypatch):
    monkeypatch.setattr(routes.settings, "public_base_url", "http://fuse.example")
    response = asyncio.run(routes.nanogpt_start(make_request("/sources/nanogpt/start")))
    assert response.headers["location"] == "/sources#pool-nanogpt"
    assert "device sign-in" in routes._pool_flash[signed_in["id"]][1]


def test_device_login_polls_until_approved(signed_in, network):
    polls = iter([httpx.Response(202, json={"status": "authorization_pending"}),
                  httpx.Response(200, json={"status": "approved", "key": "sk-nano-from-device-1"})])

    def handler(request):
        if request.url.path == "/api/cli-login/start":
            return httpx.Response(200, json={"device_code": "dev", "user_code": "AB-CD",
                                             "verification_uri_complete": "https://nano-gpt.com/cli-login/verify?code=AB-CD",
                                             "interval": 2})
        if request.url.path == "/api/cli-login/poll":
            return next(polls)
        if request.url.path.endswith("/subscription/v1/usage"):
            return httpx.Response(200, json=usage_payload(allows_balance=True))
        return httpx.Response(404)

    network(handler)
    asyncio.run(routes.nanogpt_device_start(make_request("/sources/nanogpt/device")))
    assert routes._nanogpt_pending[signed_in["id"]]["kind"] == "device"
    first = asyncio.run(routes.nanogpt_poll(make_request("/sources/nanogpt/poll")))
    assert json.loads(first.body) == {"pending": True, "retry_after": 3}
    second = asyncio.run(routes.nanogpt_poll(make_request("/sources/nanogpt/poll")))
    assert json.loads(second.body) == {"ok": True}
    cred = db.fetchone("SELECT * FROM credentials WHERE donated_by=? AND disabled=0", (signed_in["id"],))
    assert accounts.cred_secret(cred) == "sk-nano-from-device-1"
    assert "Only you use it" in routes._pool_flash[signed_in["id"]][1]


def test_pasted_key_is_validated_without_a_completion(backend, network):
    _src, sid, (a, _b, _c) = backend
    requests = network(lambda r: httpx.Response(401, json={"error": "invalid_api_key"}))
    cid, err = asyncio.run(nanogpt.add_key(sid, "sk-nano-rejected-key", a))
    assert cid is None and "rejected" in err
    assert all("/chat/completions" not in str(r.url) for r in requests)
    assert asyncio.run(nanogpt.add_key(sid, "sk-nano-mgmt-abc", a))[0] is None
