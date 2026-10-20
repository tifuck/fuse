"""Per-provider error classification (app/provider_errors.py).

Bodies are the documented or observed shapes from each provider's error
reference; the kinds drive the user message, pool cooldowns and whether the
dashboard resumes a failed round.
"""

import pytest

from app import provider_errors as pe
from app.chat_generation import policy

NOW = 1_800_000_000.0

CASES = [
    # Z.AI business codes: everything interesting is HTTP 429.
    ("zai", 429, {"error": {"code": "1308", "message": "Usage limit reached for 5 hour. Your limit will reset at 2026-10-20 14:00:00"}}, None, "usage_exhausted"),
    ("zai", 429, {"error": {"code": 1113, "message": "Insufficient balance or no resource package. Please recharge."}}, None, "insufficient_balance"),
    ("zai", 429, {"error": {"code": "1302", "message": "Rate limit reached for requests"}}, None, "rate_limit"),
    ("zai", 429, {"error": {"code": "1305", "message": "The service may be temporarily overloaded, please try again later"}}, None, "overloaded"),
    ("zai", 429, {"error": {"code": "1311", "message": "Your current subscription plan does not yet include access to glm-5"}}, None, "plan_required"),
    ("zai", 400, {"error": {"code": "1261", "message": "Prompt too long"}}, None, "context_length"),
    # ChatGPT (Codex backend)
    ("chatgpt", 429, {"error": {"type": "usage_limit_reached", "message": "The usage limit has been reached",
                                "resets_at": NOW + 7800, "limit_window_minutes": 300}}, None, "usage_exhausted"),
    ("chatgpt", 429, {"error": {"type": "usage_not_included"}}, None, "plan_required"),
    ("chatgpt", 0, {"error": {"message": "x", "type": "api_error", "code": "context_length_exceeded"}}, None, "context_length"),
    ("chatgpt", 429, {"error": {"message": "x"}}, {"x-codex-rate-limit-reached-type": "workspace_owner_credits_depleted"}, "insufficient_balance"),
    # Claude: the unified headers turn a generic rate-limit 429 into a spent window.
    ("claude", 429, {"error": {"type": "rate_limit_error", "message": "This request would exceed your account's rate limit."}},
     {"anthropic-ratelimit-unified-status": "rejected", "anthropic-ratelimit-unified-reset": str(int(NOW + 86400)),
      "anthropic-ratelimit-unified-representative-claim": "seven_day"}, "usage_exhausted"),
    ("claude", 429, {"error": {"type": "rate_limit_error", "message": "Rate limited"}}, {"retry-after": "12"}, "rate_limit"),
    ("claude", 429, {"error": {"type": "rate_limit_error", "message": "x", "details": {"error_code": "enforced_spend_limit_reached"}}}, None, "usage_exhausted"),
    ("claude", 529, {"error": {"type": "overloaded_error", "message": "Overloaded"}}, None, "overloaded"),
    ("claude", 400, {"error": {"type": "invalid_request_error", "message": "prompt is too long: 209375 tokens > 200000 maximum"}}, None, "context_length"),
    ("claude", 400, {"error": {"type": "invalid_request_error", "message": "Your credit balance is too low to access the Anthropic API."}}, None, "insufficient_balance"),
    # OpenAI platform / generic compat
    ("openai", 429, {"error": {"code": "insufficient_quota", "message": "You exceeded your current quota, please check your plan and billing details."}}, None, "insufficient_balance"),
    ("openai", 429, {"error": {"code": "rate_limit_exceeded", "message": "Please try again in 20ms."}}, None, "rate_limit"),
    ("openai", 429, {"error": {"code": "organization_spend_limit_exceeded", "message": "x"}}, None, "usage_exhausted"),
    ("openai", 503, {"error": {"code": "server_is_overloaded", "message": "x"}}, None, "overloaded"),
    # xAI flat {code, error}
    ("grok", 429, {"code": "subscription:free-usage-exhausted", "error": "You've used all the included free usage for model grok-4 for now."}, None, "usage_exhausted"),
    ("grok", 403, {"code": "personal-team-blocked:spending-limit", "error": "You have run out of credits or need a Grok subscription."}, None, "insufficient_balance"),
    # Kimi Code: 403 is both quota and concurrency; 401 is also plan gating.
    ("kimi", 403, {"error": {"message": "You've reached your weekly (7-day) usage limit. To continue now, purchase extra usage or upgrade your plan"}}, None, "usage_exhausted"),
    ("kimi", 403, {"error": {"message": "You've reached your concurrent request limit. Please wait for your ongoing requests to finish"}}, None, "rate_limit"),
    ("kimi", 401, {"error": {"message": "Your current subscription does not have access to k3.  Upgrade to higher-tier Kimi Code plans."}}, None, "plan_required"),
    ("kimi", 429, {"error": {"message": "The engine is currently overloaded, please try again later"}}, None, "overloaded"),
    # OpenRouter metadata
    ("openrouter", 429, {"error": {"code": 429, "message": "Rate limit exceeded: free-models-per-day.",
                                   "metadata": {"limit_source": "openrouter_free_tier_daily"}}}, None, "usage_exhausted"),
    ("openrouter", 429, {"error": {"code": 429, "message": "Rate limit exceeded: free-models-per-min.",
                                   "metadata": {"limit_source": "openrouter_free_tier_per_minute"}}}, None, "rate_limit"),
    ("openrouter", 404, {"error": {"code": 404, "message": "No endpoints found that support tool use."}}, None, "invalid_request"),
    # NanoGPT, Ollama, llama.cpp, Experiential Labs
    ("nanogpt", 402, {"error": "Insufficient balance", "status": 402}, None, "insufficient_balance"),
    ("nanogpt", 429, {"error": {"code": "daily_rpd_limit_exceeded", "message": "Daily request limit exceeded (1000/1000). Resets at midnight UTC."}}, None, "usage_exhausted"),
    ("ollama", 429, {"error": "you (bob) have reached your session usage limit, upgrade for higher limits"}, None, "usage_exhausted"),
    ("ollama", 429, {"error": "429 Too Many Requests: timed out waiting for a concurrent request slot"}, None, "rate_limit"),
    ("ollama", 403, {"error": "this model requires a subscription, upgrade for access: https://ollama.com/upgrade"}, None, "plan_required"),
    ("llama", 400, {"error": {"code": 400, "type": "exceed_context_size_error", "message": "request (9000 tokens) exceeds the available context size (8192 tokens)"}}, None, "context_length"),
    ("llama", 503, {"error": {"code": 503, "type": "unavailable_error", "message": "Loading model"}}, None, "overloaded"),
    ("explabs", 429, {"error": {"code": "insufficient_quota", "message": "key_daily_cap: this key spent its daily cap"}}, None, "usage_exhausted"),
    ("explabs", 429, {"error": {"code": "insufficient_quota", "message": "insufficient_credits: balance 0"}}, None, "insufficient_balance"),
    # Xiaomi MiMo: moderation codes ride on HTTP 400
    ("mimo", 400, {"error": {"code": "421", "type": "content_filter", "message": "Moderation Block"}}, None, "content_filter"),
    ("mimo", 400, {"error": {"code": "441", "type": "risk_control", "message": "Severe Violation"}}, None, "content_filter"),
    ("mimo", 429, {"error": {"code": "429", "type": "limitation", "message": "Too many requests"}}, None, "rate_limit"),
    # Connect trailers (Fuse puts the code in error.type)
    ("devin", 0, {"error": {"message": "daily quota exceeded", "type": "resource_exhausted", "code": 429}}, None, "usage_exhausted"),
    ("devin", 0, {"error": {"message": "Permission denied: internal server error: error number 12", "type": "permission_denied"}}, None, "permission"),
    ("cursor", 429, {"error": {"message": "Error", "type": "error_pro_user_usage_limit"}}, None, "usage_exhausted"),
    ("cursor", 429, {"error": {"message": "Error", "type": "error_free_user_rate_limit_exceeded"}}, None, "rate_limit"),
]


@pytest.mark.parametrize("provider,status,body,headers,kind", CASES)
def test_classify_kind(provider, status, body, headers, kind):
    info = pe.classify(provider, status, body, headers, now=NOW)
    assert info and info["kind"] == kind


def test_usage_message_names_window_and_reset():
    body = {"error": {"type": "usage_limit_reached", "message": "The usage limit has been reached",
                      "resets_at": NOW + 7800, "limit_window_minutes": 300}}
    out, info = pe.annotate("chatgpt", 429, body, now=NOW)
    assert out["error"]["message"].startswith("ChatGPT: 5-hour usage limit reached; resets in 2h 10m")
    assert "The usage limit has been reached" in out["error"]["message"]
    assert out["error"]["type"] == "usage_exhausted" and out["error"]["resets_at"] == int(NOW + 7800)


def test_reset_sources():
    assert pe.classify("openai", 429, {"error": {"message": "x"}}, {"x-ratelimit-reset-tokens": "6m0s"}, now=NOW)["retry_after"] == 360
    assert pe.classify("openai", 429, {"error": {"message": "Please try again in 1.5s."}}, now=NOW)["retry_after"] == 1.5
    reset = pe.classify("openrouter", 429, {"error": {"message": "x", "metadata": {"headers": {"X-RateLimit-Reset": str(int((NOW + 90) * 1000))}}}}, now=NOW)
    assert reset["retry_after"] == pytest.approx(90)
    assert pe.retry_after_seconds("Wed, 21 Oct 2015 07:28:00 GMT", now=0) > 0


def test_long_rate_limit_is_spent_allowance():
    info = pe.classify("nanogpt", 429, {"error": {"code": "rate_limit_exceeded", "message": "x"}}, {"retry-after": "30000"}, now=NOW)
    assert info["kind"] == "usage_exhausted"


def test_fuse_transport_messages_stay_verbatim():
    # The resume policy matches "upstream request failed (X)" exactly.
    body, info = pe.annotate("zai", 502, {"error": {"message": "upstream request failed (TypeError)"}})
    assert body["error"]["message"] == "upstream request failed (TypeError)"
    assert info["kind"] == "server_error"
    body, _ = pe.annotate("zai", 502, {"error": {"message": "Z.AI rejected the configured API key"}})
    assert body["error"]["message"] == "Z.AI rejected the configured API key"


def test_annotate_drops_private_headers_and_normalizes_code():
    body = {"error": {"code": "1308", "message": "Usage limit reached"}, "_fuse_headers": {"retry-after": "5"}}
    out, _ = pe.annotate("zai", 429, body)
    assert "_fuse_headers" not in out
    assert out["error"]["code"] == 429 and out["error"]["provider_code"] == "1308"
    unknown, info = pe.annotate("zai", 0, {"error": {"message": "?"}, "_fuse_headers": {"retry-after": "5"}})
    assert info is None and "_fuse_headers" not in unknown


def test_cooldown():
    assert pe.cooldown(None, 60) == 60
    assert pe.cooldown({"kind": "usage_exhausted", "retry_after": 7200.0}, 60) == 7200
    assert pe.cooldown({"kind": "usage_exhausted", "retry_after": None}, 60) == 1800
    assert pe.cooldown({"kind": "usage_exhausted", "retry_after": 10 * 86400.0}, 60) == 6 * 3600
    assert pe.cooldown({"kind": "rate_limit", "retry_after": 5.0}, 60) == 60


def test_policy_stops_resuming_terminal_kinds():
    spent, _ = pe.annotate("zai", 429, {"error": {"code": "1310", "message": "Weekly/Monthly Limit Exhausted."}})
    assert not policy._should_resume(429, spent["error"]["message"], resumes_left=4, idle_stalls=0,
                                     kind=pe.kind_of(spent))
    busy, _ = pe.annotate("kimi", 403, {"error": {"message": "You've reached your concurrent request limit."}})
    assert policy._should_resume(403, busy["error"]["message"], resumes_left=4, idle_stalls=0, kind=pe.kind_of(busy))
    # Unclassified errors keep the old status rule.
    assert policy._should_resume(429, "", resumes_left=4, idle_stalls=0)


def test_kimi_live_plan_denials_are_not_auth_strikes():
    from app.sources import kimi

    for message in ("Your current subscription does not have access to k3.  Upgrade to higher-tier Kimi Code plans.",
                    "Your current plan supports only k3 up to 256K context. 1M context is available on higher-tier plans."):
        assert kimi._capability_denial({"error": {"message": message}})
    assert not kimi._capability_denial({"error": {"message": "The API Key appears to be invalid or may have expired."}})


def test_error_frames_keep_kind():
    spent, _ = pe.annotate("grok", 429, {"code": "subscription:free-usage-exhausted", "error": "free usage spent"})
    frame = policy._exhausted_provider_error(False, spent, 429, attempts=0)
    assert frame["error"]["type"] == "usage_exhausted"
    child = policy._exhausted_provider_error(True, spent, 429, attempts=0)
    assert child["error"]["type"] == "usage_exhausted" and "Grok: usage limit" in child["error"]["message"]
