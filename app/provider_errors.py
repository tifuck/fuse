"""Per-provider error classification.

Providers report the same few conditions (rate limit, plan usage spent, no
credits, model not on the plan, overloaded, context too long) in their own
shapes: OpenAI's nested error.code, Anthropic's error.type plus
anthropic-ratelimit-unified-* headers, Z.AI business codes, xAI's flat
{code, error}, OpenRouter's error.metadata, Connect trailers for Devin and
Cursor. classify() reads structured fields first, then the providers' known
sentences, then the HTTP status, and returns one kind from a shared taxonomy
plus the reset time when the provider gives one. annotate() rewrites the
error body so /v1 and the dashboard say what happened; the resume policy
reads the kind to stop retrying errors a retry cannot fix.

Sources for every code and sentence below: each provider's error reference
(and official CLI source where the docs are silent), inventoried 2026-10.
"""

from __future__ import annotations

import re
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

from .proxy import scrub_secrets

# A retry with the same context and account fails the same way.
TERMINAL = frozenset({
    "usage_exhausted", "insufficient_balance", "plan_required", "credentials", "permission",
    "invalid_request", "context_length", "content_filter", "model_not_found",
})
# stalled / interrupted are what Fuse saw on the provider's stream (no output
# for the pacer's budget, or the connection broke), not what it reported.
TRANSIENT = frozenset({"rate_limit", "overloaded", "server_error", "timeout", "stalled", "interrupted"})
KINDS = TERMINAL | TRANSIENT

# Upstream headers worth keeping for classification (reset times, which
# subscription window rejected). Never relayed to /v1 callers: on a pool they
# describe another user's account.
HEADER_PREFIXES = ("retry-after", "x-ratelimit-", "anthropic-ratelimit-", "x-codex-")

_LABELS = {
    "claude": "Claude", "chatgpt": "ChatGPT", "grok": "Grok", "zai": "Z.AI", "devin": "Devin",
    "kimi": "Kimi", "cursor": "Cursor", "ollama": "Ollama Cloud", "openrouter": "OpenRouter",
    "nanogpt": "NanoGPT", "mimo": "MiMo", "llama": "llama.cpp", "explabs": "Experiential Labs",
}

# Message headlines. The chat UI keys its card off these exact phrases
# (static/themes/console/provider-error.js SUMMARIES): change both together.
_SUMMARY = {
    "rate_limit": "rate limited",
    "usage_exhausted": "plan usage limit reached",
    "insufficient_balance": "out of credits",
    "plan_required": "not included in this plan",
    "credentials": "account sign-in rejected",
    "permission": "access denied",
    "invalid_request": "request rejected",
    "context_length": "conversation too long for this model",
    "content_filter": "blocked by content policy",
    "model_not_found": "model not available",
    "overloaded": "overloaded",
    "server_error": "server error",
    "timeout": "timed out",
    "stalled": "stopped responding",
    "interrupted": "connection lost",
}

# Provider-specific code/type values (error.code, error.type, top-level code,
# OpenRouter metadata.error_type, Cursor ErrorDetails enum). Specific enough
# to trust over any sentence.
_CODES = {
    # OpenAI platform + Codex (ChatGPT)
    "insufficient_quota": "insufficient_balance",
    "credit_balance_exhausted": "insufficient_balance",
    "organization_spend_limit_exceeded": "usage_exhausted",
    "project_spend_limit_exceeded": "usage_exhausted",
    "organization_usage_limit_exceeded": "usage_exhausted",
    "billing_hard_limit_reached": "usage_exhausted",
    "usage_limit_reached": "usage_exhausted",
    "usage_not_included": "plan_required",
    "rate_limit_exceeded": "rate_limit",
    "slow_down": "rate_limit",
    "server_is_overloaded": "overloaded",
    "service_unavailable_error": "overloaded",
    "flex_unavailable": "overloaded",
    "context_length_exceeded": "context_length",
    "invalid_prompt": "invalid_request",
    "invalid_api_key": "credentials",
    "model_not_found": "model_not_found",
    "cyber_policy": "content_filter",
    "bio_policy": "content_filter",
    "misalignment_policy_violation": "content_filter",
    "content_filter": "content_filter",
    # Anthropic
    "overloaded_error": "overloaded",
    "rate_limit_error": "rate_limit",
    "billing_error": "insufficient_balance",
    "request_too_large": "invalid_request",
    "timeout_error": "timeout",
    "enforced_spend_limit_reached": "usage_exhausted",
    # Moonshot / Kimi
    "exceeded_current_quota_error": "insufficient_balance",
    "rate_limit_reached_error": "rate_limit",
    "engine_overloaded_error": "overloaded",
    "resource_not_found_error": "model_not_found",
    "invalid_authentication_error": "credentials",
    "incorrect_api_key_error": "credentials",
    "permission_denied_error": "permission",
    # xAI (flat {code, error})
    "subscription:free-usage-exhausted": "usage_exhausted",
    "personal-team-blocked:spending-limit": "insufficient_balance",
    "permission-denied": "permission",
    # NanoGPT
    "daily_rpd_limit_exceeded": "usage_exhausted",
    "daily_usd_limit_exceeded": "usage_exhausted",
    "model_not_allowed": "plan_required",
    "memory_balance_required": "insufficient_balance",
    "websearch_balance_required": "insufficient_balance",
    "both_balance_required": "insufficient_balance",
    "content_policy_violation": "content_filter",
    "all_fallbacks_failed": "overloaded",
    "no_fallback_available": "overloaded",
    "fallback_blocked_for_cache_consistency": "overloaded",
    "service_unavailable": "overloaded",
    # OpenRouter metadata.error_type
    "payment_required": "insufficient_balance",
    "provider_overloaded": "overloaded",
    "provider_unavailable": "server_error",
    "refusal": "content_filter",
    "max_tokens_exceeded": "invalid_request",
    "token_limit_exceeded": "context_length",
    # Experiential Labs
    "invalid_key": "credentials",
    "model_not_granted": "model_not_found",
    "model_location_not_supported": "permission",
    "uncensored_requires_retention": "permission",
    "needs_subscription": "plan_required",
    "card_required": "plan_required",
    "free_limit_reached": "usage_exhausted",
    "org_under_review": "permission",
    "unavailable_route": "overloaded",
    "gateway_overloaded": "overloaded",
    "gateway_draining": "overloaded",
    "provider_internal": "server_error",
    "all_routes_failed": "server_error",
    "backend_unavailable": "server_error",
    "provider_output_too_large": "invalid_request",
    # Xiaomi MiMo (business codes 421/441 ride on HTTP 400; see _MIMO)
    "rate_limited": "rate_limit",
    "risk_control": "content_filter",
    "insufficient_balance": "insufficient_balance",
    # llama.cpp
    "exceed_context_size_error": "context_length",
    "unavailable_error": "overloaded",
    # Cursor aiserver.v1.ErrorDetails enum (debug.error)
    "error_free_user_usage_limit": "usage_exhausted",
    "error_pro_user_usage_limit": "usage_exhausted",
    "error_openai_account_limit_exceeded": "usage_exhausted",
    "error_free_user_rate_limit_exceeded": "rate_limit",
    "error_pro_user_rate_limit_exceeded": "rate_limit",
    "error_generic_rate_limit_exceeded": "rate_limit",
    "error_api_key_rate_limit": "rate_limit",
    "error_openai_rate_limit_exceeded": "rate_limit",
    "error_gpt_4_vision_preview_rate_limit": "rate_limit",
    "error_debounced": "rate_limit",
    "error_pro_user_only": "plan_required",
    "error_not_logged_in": "credentials",
    "error_auth_token_expired": "credentials",
    "error_auth_token_not_found": "credentials",
    "error_bad_api_key": "credentials",
    "error_bad_user_api_key": "credentials",
    "error_invalid_auth_id": "credentials",
    "error_unauthorized": "credentials",
    "error_not_high_enough_permissions": "permission",
    "error_max_tokens": "context_length",
    "error_bad_model_name": "model_not_found",
    "error_resource_exhausted": "overloaded",
}

# Generic types many providers share. They say less than a known sentence
# (Anthropic sends credit, usage and context errors all as
# invalid_request_error), so they rank below the message patterns.
_TYPES = {
    "invalid_request_error": "invalid_request",
    "authentication_error": "credentials",
    "authentication": "credentials",
    "permission_error": "permission",
    "permission_denied": "permission",
    "not_found_error": "model_not_found",
    "not_supported_error": "invalid_request",
    "server_error": "server_error",
    "timeout": "timeout",
}

# Z.AI business codes (docs.z.ai/api-reference/api-code). Every interesting
# one arrives as HTTP 429, so the status says nothing.
_ZAI = {
    "1000": "credentials", "1001": "credentials", "1003": "credentials", "1005": "credentials",
    "1113": "insufficient_balance",
    "1200": "server_error", "1230": "server_error", "1234": "server_error",
    "1210": "invalid_request", "1212": "invalid_request", "1213": "invalid_request",
    "1214": "invalid_request", "1215": "invalid_request", "1221": "invalid_request", "1222": "invalid_request",
    "1211": "model_not_found", "1220": "permission", "1261": "context_length", "1301": "content_filter",
    "1302": "rate_limit", "1305": "overloaded",
    "1308": "usage_exhausted", "1310": "usage_exhausted", "1316": "usage_exhausted", "1317": "usage_exhausted",
    "1318": "usage_exhausted", "1319": "usage_exhausted", "1320": "usage_exhausted", "1321": "usage_exhausted",
    "1309": "plan_required", "1311": "plan_required", "1314": "plan_required",
    "1313": "permission", "1315": "permission",
}

# MiMo puts its business code in error.code as a string.
_MIMO = {"421": "content_filter", "441": "content_filter", "402": "insufficient_balance", "401": "credentials"}

# Connect-RPC codes (Devin and Cursor trailers / unary errors).
_CONNECT = {
    "unavailable": "overloaded", "deadline_exceeded": "timeout", "unauthenticated": "credentials",
    "permission_denied": "permission", "invalid_argument": "invalid_request",
    "failed_precondition": "invalid_request", "out_of_range": "invalid_request",
    "unimplemented": "invalid_request", "not_found": "model_not_found",
    "internal": "server_error", "unknown": "server_error", "data_loss": "server_error",
    "aborted": "server_error",
}

# OpenRouter error.metadata.limit_source: whose limit tripped.
_LIMIT_SOURCES = {
    "openrouter_free_tier_daily": "usage_exhausted",
    "openrouter_free_tier_per_minute": "rate_limit",
    "openrouter_guardrail_budget": "usage_exhausted",
    "openrouter_workspace_budget": "usage_exhausted",
    "openrouter_key_limit": "usage_exhausted",
    "openrouter_in_flight_budget": "rate_limit",
    "openrouter_credits": "insufficient_balance",
}

# Experiential Labs puts the real reason first in an insufficient_quota message.
_EXPLABS_PREFIX = re.compile(
    r"\b(insufficient_credits|promo_byok_only|key_daily_cap|free_limit_reached|org_token_rate_limit\w*"
    r"|org_rate_limit|free_tier_requires_payment|model_requires_payment|model_requires_purchase)\b"
)
_EXPLABS_KINDS = {
    "insufficient_credits": "insufficient_balance", "promo_byok_only": "insufficient_balance",
    "key_daily_cap": "usage_exhausted", "free_limit_reached": "usage_exhausted",
    "org_rate_limit": "rate_limit", "free_tier_requires_payment": "plan_required",
    "model_requires_payment": "plan_required", "model_requires_purchase": "plan_required",
}

# Known provider sentences, first match wins. Order matters: Kimi's
# "reached your concurrent request limit" is a rate limit, not usage; Kimi's
# quota sentences also say "upgrade your plan".
_PATTERNS = [
    ("credentials", re.compile(r"rejected (?:the configured|this account)|invalid api key|incorrect api key|api key appears to be invalid", re.I)),
    ("context_length", re.compile(
        r"context (?:length|window|size)|prompt is too long|prompt too long|input token length too long"
        r"|exceeded model token limit|total message size \d+ exceeds|larger than the max context"
        r"|supports only 256k context|ran out of room", re.I)),
    ("rate_limit", re.compile(
        r"concurrent request|concurrency|too many requests|requests? per min|tokens? per min"
        r"|rate limit reached for|free-models-per-min", re.I)),
    ("insufficient_balance", re.compile(
        r"credit balance|insufficient (?:balance|credits?|funds)|out of credits|run out of credits"
        r"|balance (?:is )?(?:too low|insufficient|empty)|please recharge|account (?:is )?in arrears", re.I)),
    ("usage_exhausted", re.compile(
        r"usage limit|usage cap|limit exhausted|quota (?:exceeded|exhausted|reached)|daily (?:request|token|spend) limit"
        r"|free usage|free-models-per-day|budget limit|spend(?:ing)? (?:limit|cap)"
        r"|reached your [\w\s()-]{0,30}limit|out of usage|额度耗尽|使用上限", re.I)),
    ("plan_required", re.compile(
        r"does not (?:yet )?(?:have|include) access to|not included in (?:your|the|this)"
        r"|(?:plan|subscription) does not (?:include|support)|requires a (?:paid|subscription|higher)"
        r"|only use auto|need a [\w ]{0,20}subscription|supports only k3 up to|package has expired", re.I)),
    ("overloaded", re.compile(r"overloaded|at capacity|high demand|server.?busy|temporarily unavailable|loading model", re.I)),
    ("rate_limit", re.compile(r"rate.?limit", re.I)),
    ("content_filter", re.compile(
        r"content (?:policy|filter|management)|considered high risk|sensitive content|flagged for", re.I)),
    ("model_not_found", re.compile(
        r"model [`'\"]?[\w./:-]*[`'\"]? (?:does not exist|not found)|unknown model|no endpoints found for"
        r"|model id does not exist|not found the model", re.I)),
]

_FUSE_AUTH = re.compile(r"rejected (?:the configured|this account)", re.I)

# Fuse's own sentences for a provider stream it watched fail. Without these
# the 502 they ride on read as the provider's "server error".
_FUSE_STREAM = [
    ("stalled", re.compile(r"idle timeout|timeout \(idle\)|(?:stream failed|transport failure) \(\w*Timeout\)", re.I)),
    ("interrupted", re.compile(
        r"^(?:upstream|cursor|devin) (?:stream failed|transport failure) \(\w+\)$"
        r"|stream ended (?:without|before)", re.I)),
]

_STATUS = {
    400: "invalid_request", 401: "credentials", 402: "insufficient_balance", 403: "permission",
    404: "model_not_found", 405: "invalid_request", 408: "timeout", 413: "invalid_request",
    415: "invalid_request", 422: "invalid_request", 429: "rate_limit", 500: "server_error",
    502: "server_error", 503: "overloaded", 504: "timeout", 529: "overloaded",
}
# Status-only kinds whose generic sentence is still news to the reader. Other
# statuses keep the original text: Fuse's own transport messages (and the
# exact "upstream request failed (X)" the resume policy matches) live there.
_STATUS_REWRITE = frozenset({401, 402, 403, 429})


def _text(value) -> str:
    return value.strip() if isinstance(value, str) else ""


def _parts(body):
    """(error dict, code, type, message, metadata) from any provider shape."""
    if not isinstance(body, dict):
        return {}, "", "", _text(body), {}
    raw = body.get("error")
    err = raw if isinstance(raw, dict) else {}
    code = err.get("code") if err.get("code") is not None else body.get("code")
    code = "" if isinstance(code, bool) or code is None else str(code).strip().lower()
    etype = _text(err.get("type")).lower()
    message = (
        _text(err.get("message")) or _text(err.get("msg")) or _text(raw)
        or _text(body.get("message")) or _text(body.get("msg")) or _text(body.get("detail"))
    )
    meta = err.get("metadata") if isinstance(err.get("metadata"), dict) else {}
    return err, code, etype, message, meta


def _headers(headers, body) -> dict:
    out = {}
    for source in (headers, body.get("_fuse_headers") if isinstance(body, dict) else None):
        if isinstance(source, dict):
            out.update({str(k).lower(): str(v) for k, v in source.items()})
    return out


def keep_headers(headers) -> dict:
    """The subset of upstream response headers classify() reads."""
    try:
        items = headers.items()
    except AttributeError:
        return {}
    return {str(k).lower(): str(v)[:128] for k, v in items if str(k).lower().startswith(HEADER_PREFIXES)}


def _structured(provider, err, code, etype, meta, h, message) -> str | None:
    # Claude subscriptions: the 429 body is a generic rate-limit sentence;
    # the unified headers say a 5-hour or weekly window rejected it.
    if "rejected" in (h.get("anthropic-ratelimit-unified-status"), h.get("anthropic-ratelimit-unified-5h-status"),
                      h.get("anthropic-ratelimit-unified-7d-status")):
        return "usage_exhausted"
    reached = h.get("x-codex-rate-limit-reached-type", "")
    if reached:
        return "rate_limit" if reached == "rate_limit_reached" else (
            "insufficient_balance" if "credits" in reached else "usage_exhausted")
    details = err.get("details") if isinstance(err.get("details"), dict) else {}
    if _text(details.get("error_code")).lower() in _CODES:
        return _CODES[details["error_code"].strip().lower()]
    source = _text(meta.get("limit_source")).lower()
    if source in _LIMIT_SOURCES:
        return _LIMIT_SOURCES[source]
    if _text(meta.get("reason")) == "in_flight_budget_exhausted":
        return "rate_limit"
    if provider == "explabs" and code == "insufficient_quota":
        hit = _EXPLABS_PREFIX.search(message)
        if hit:
            name = hit.group(1)
            return "rate_limit" if name.startswith("org_token_rate_limit") else _EXPLABS_KINDS[name]
    for value in (code, etype, _text(meta.get("error_type")).lower()):
        if value in _CODES:
            return _CODES[value]
    if code in _ZAI:
        return _ZAI[code]
    if provider == "mimo" and code in _MIMO:
        return _MIMO[code]
    if provider in ("devin", "cursor"):
        # Connect code, as error.code (unary) or error.type (Fuse's trailer frame).
        connect = next((v for v in (code, etype) if v in _CONNECT or v == "resource_exhausted"), "")
        if connect == "resource_exhausted":
            # Devin reports a spent daily/weekly allowance this way; Cursor's
            # enum (checked above) refines it when present.
            return "usage_exhausted" if provider == "devin" else "rate_limit"
        # Devin's "an internal error occurred" is usually a dead SKU that fails
        # identically on retry (devin_catalog's breaker); leave it unclassified.
        if connect and not (provider == "devin" and connect in ("internal", "unknown")):
            return _CONNECT[connect]
    return None


def _from_message(provider, message, status) -> str | None:
    for kind, pattern in _PATTERNS:
        if pattern.search(message):
            if kind == "model_not_found" and provider == "openrouter" and re.search(r"tool use", message, re.I):
                return "invalid_request"
            return kind
    if provider == "openrouter" and re.match(r"no endpoints found", message, re.I):
        return "invalid_request" if re.search(r"tool use", message, re.I) else "permission"
    return None


def _epoch(value) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)) or (isinstance(value, str) and re.fullmatch(r"\d+(?:\.\d+)?", value.strip())):
        num = float(value)
        if num <= 0:
            return None
        return num / 1000.0 if num > 1e11 else num
    if isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    return None


_DURATION = re.compile(r"(\d+(?:\.\d+)?)(ms|h|m|s)")


def _duration(value: str) -> float | None:
    """OpenAI x-ratelimit-reset-* values: 1s, 6m0s, 20ms, 1h2m3.5s."""
    text = (value or "").strip()
    if not text or _DURATION.sub("", text):
        return None
    scale = {"ms": 0.001, "s": 1.0, "m": 60.0, "h": 3600.0}
    return sum(float(n) * scale[u] for n, u in _DURATION.findall(text))


def retry_after_seconds(value, now: float | None = None) -> float | None:
    """Retry-After as seconds: delta-seconds or an HTTP-date."""
    text = _text(str(value) if value is not None else "")
    if not text:
        return None
    try:
        return max(0.0, float(text))
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(text)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return max(0.0, when.timestamp() - (now if now is not None else time.time()))


_TRY_AGAIN = re.compile(r"try again in (\d+(?:\.\d+)?)\s*(ms|milliseconds?|s|secs?|seconds?|m|mins?|minutes?)\b", re.I)
_TRY_AGAIN_ZH = re.compile(r"请约\s*(\d+(?:\.\d+)?)\s*秒后重试")  # MiMo TPM limit


def _reset_at(err, meta, h, message, now) -> float | None:
    for value in (err.get("resets_at"), meta.get("resets_at")):
        at = _epoch(value)
        if at:
            return at
    seconds = err.get("resets_in_seconds")
    if isinstance(seconds, (int, float)) and not isinstance(seconds, bool) and seconds > 0:
        return now + float(seconds)
    meta_headers = meta.get("headers") if isinstance(meta.get("headers"), dict) else {}
    for key, value in meta_headers.items():
        if str(key).lower() == "x-ratelimit-reset":
            at = _epoch(value)
            if at:
                return at
    at = _epoch(h.get("anthropic-ratelimit-unified-reset"))
    if at:
        return at
    wait = retry_after_seconds(h.get("retry-after"), now)
    if wait:
        return now + wait
    waits = [_duration(h[k]) for k in ("x-ratelimit-reset-requests", "x-ratelimit-reset-tokens") if h.get(k)]
    waits = [w for w in waits if w]
    if waits:
        return now + max(waits)
    hit = _TRY_AGAIN_ZH.search(message)
    if hit:
        return now + float(hit.group(1))
    hit = _TRY_AGAIN.search(message)
    if hit:
        unit = hit.group(2).lower()
        scale = 0.001 if unit.startswith("ms") or unit.startswith("milli") else 60.0 if unit.startswith("m") else 1.0
        return now + float(hit.group(1)) * scale
    return None


def _window(err, h) -> str:
    claim = (h.get("anthropic-ratelimit-unified-representative-claim") or "").lower()
    if claim:
        return {"five_hour": "5-hour", "seven_day": "weekly"}.get(claim, claim.replace("_", " "))
    minutes = err.get("limit_window_minutes")
    if isinstance(minutes, (int, float)) and not isinstance(minutes, bool) and minutes > 0:
        return {300: "5-hour", 1440: "daily", 10080: "weekly"}.get(int(minutes), f"{int(minutes)}-minute")
    return ""


def _span(seconds: float) -> str:
    seconds = int(max(1, round(seconds)))
    if seconds < 60:
        return f"{seconds}s"
    minutes, _ = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    days, hours = divmod(hours, 24)
    if days:
        return f"{days}d {hours}h" if hours else f"{days}d"
    if hours:
        return f"{hours}h {minutes}m" if minutes else f"{hours}h"
    return f"{minutes}m"


def _when(reset_at: float, now: float) -> str:
    wait = reset_at - now
    if wait <= 0:
        return ""
    if wait < 3600:
        return f"retry in {_span(wait)}"
    stamp = datetime.fromtimestamp(reset_at, timezone.utc)
    clock = stamp.strftime("%H:%M UTC") if wait < 20 * 3600 else stamp.strftime("%b %-d %H:%M UTC")
    return f"resets in {_span(wait)} ({clock})"


def classify(provider: str, status: int, body, headers=None, *, now: float | None = None) -> dict | None:
    """{kind, message, reset_at, retry_after} for an upstream error, or None."""
    now = time.time() if now is None else now
    status = status if isinstance(status, int) and not isinstance(status, bool) else 0
    err, code, etype, message, meta = _parts(body)
    h = _headers(headers, body)
    kind = _structured(provider, err, code, etype, meta, h, message)
    if kind is None:
        kind = next((name for name, pattern in _FUSE_STREAM if pattern.search(message)), None)
    known = kind is not None
    if kind is None:
        kind = _from_message(provider, message, status)
        known = kind is not None
    if kind is None and etype in _TYPES:
        kind, known = _TYPES[etype], True
    if kind is None:
        kind = _STATUS.get(status) or ("server_error" if 500 <= status <= 599 else None)
        known = status in _STATUS_REWRITE
    if kind is None:
        return None
    reset_at = _reset_at(err, meta, h, message, now)
    retry_after = max(0.0, reset_at - now) if reset_at else None
    # A "rate limit" that clears in hours is a spent allowance.
    if kind == "rate_limit" and retry_after and retry_after > 3600:
        kind = "usage_exhausted"
    original = scrub_secrets(message)[:400]
    if not known or _FUSE_AUTH.search(original):
        # Unknown, or Fuse already wrote this sentence (pool credential rejected).
        text = original
    else:
        if original.lower() in ("error", "upstream error"):
            original = ""  # Cursor's placeholder; the summary says more.
        label = _LABELS.get(provider, "Provider")
        summary = _SUMMARY[kind]
        window = _window(err, h) if kind == "usage_exhausted" else ""
        if window:
            summary = f"{window} usage limit reached"
        when = _when(reset_at, now) if reset_at and kind != "context_length" else ""
        text = f"{label}: {summary}" + (f"; {when}" if when else "") + "."
        if original and original.lower() not in text.lower():
            text = f"{text} {original}"
        text = text[:600]
    return {"kind": kind, "message": text, "reset_at": reset_at, "retry_after": retry_after}


def annotate(provider: str, status: int, body, headers=None, *, now: float | None = None) -> tuple:
    """(body, info): the error body rewritten with the classified message and
    kind, or the body unchanged when nothing is known. Idempotent. Always
    drops the private `_fuse_headers`."""
    if isinstance(body, dict) and "_fuse_headers" in body:
        kept = body["_fuse_headers"] if isinstance(body["_fuse_headers"], dict) else {}
        body = {k: v for k, v in body.items() if k != "_fuse_headers"}
        headers = {**(headers or {}), **kept}
    if isinstance(body, dict) and isinstance(body.get("error"), dict) and body["error"].get("fuse_kind"):
        return body, None
    info = classify(provider, status, body, headers, now=now)
    if info is None:
        return body, None
    out = dict(body) if isinstance(body, dict) else {}
    inner = dict(body["error"]) if isinstance(body, dict) and isinstance(body.get("error"), dict) else {}
    inner.update({"type": info["kind"], "fuse_kind": info["kind"]})
    if info["message"]:
        inner["message"] = info["message"]
    if isinstance(status, int) and 400 <= status <= 599:
        # Downstream reads error.code as the HTTP status; Z.AI's 1308 there
        # turned into a bogus 502.
        if inner.get("code") not in (None, status):
            inner["provider_code"] = inner["code"]
        inner["code"] = status
    if info["reset_at"]:
        inner["resets_at"] = int(info["reset_at"])
    out["error"] = inner
    return out, info


def display_message(provider: str, status: int, message: str, kind: str = "") -> str:
    """A final failure in the "<Provider>: <summary>. <detail>" shape the chat
    card reads, so an unclassified provider failure looks like every other one
    instead of a bare "Internal Server Error". Already-shaped text passes."""
    label = _LABELS.get(provider, "Provider")
    text = (message or "").strip()
    if not provider or text.startswith(f"{label}: "):
        return text
    if kind not in _SUMMARY:
        kind = _STATUS.get(status) or "server_error"
    head = f"{label}: {_SUMMARY[kind]}."
    return f"{head} {text}" if text and text.lower() != "upstream error" else head


def retry_reason(provider: str, status: int, body) -> str:
    """What a retry notice names: "Claude: overloaded", or the status alone."""
    kind = kind_of(body)
    if kind in _SUMMARY:
        return f"{_LABELS.get(provider, 'Provider')}: {_SUMMARY[kind]}"
    return f"Provider error ({status})"


def kind_of(body) -> str:
    err = body.get("error") if isinstance(body, dict) else None
    return str(err.get("fuse_kind") or "") if isinstance(err, dict) else ""


def cooldown(info: dict | None, default: float) -> float:
    """Pool cooldown for an account that just failed with this error.

    A spent plan stays out until its window resets (capped, so a wrong reset
    costs at most one probe every 6 hours); a short rate limit waits its
    Retry-After. Everything else keeps the caller's default.
    """
    if not info:
        return default
    wait = info.get("retry_after") or 0.0
    if info["kind"] == "usage_exhausted":
        return min(max(wait, default), 6 * 3600.0) if wait else max(default, 1800.0)
    if info["kind"] == "insufficient_balance":
        return max(default, 1800.0)
    if info["kind"] == "rate_limit" and wait:
        return min(max(wait, default), 3600.0)
    return default
