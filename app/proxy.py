import json
import math
import re
from collections.abc import AsyncIterator

import httpx


_MAX_USAGE_VALUE = 9_000_000_000_000_000

_SECRET_PATTERNS = (
    (re.compile(r"sk-[A-Za-z0-9_-]{8,}"), "sk-***"),
    (re.compile(r"(?i:bearer)\s+[A-Za-z0-9._~+/=-]{8,}"), "Bearer ***"),
    (re.compile(r"://[^/\s:@]+:[^@\s]+@"), "://***@"),
)


def scrub_secrets(text: str) -> str:
    """Strip anything token-shaped from upstream text before it reaches a client."""
    out = text
    for pattern, repl in _SECRET_PATTERNS:
        out = pattern.sub(repl, out)
    return out


def _int(v) -> int:
    try:
        if v is None or isinstance(v, bool):
            return 0
        if isinstance(v, float) and not math.isfinite(v):
            return 0
        value = int(v)
        if abs(value) > _MAX_USAGE_VALUE:
            return 0
        return value
    except (TypeError, ValueError, OverflowError):
        return 0


def _cost_micros(u: dict) -> int | None:
    # xAI public responses use either nanodollars or 10 billion ticks/USD.
    if u.get("cost_in_nano_usd") is not None:
        return max(0, int(round(_int(u.get("cost_in_nano_usd")) / 1_000)))
    if u.get("cost_in_usd_ticks") is not None:
        return max(0, int(round(_int(u.get("cost_in_usd_ticks")) / 10_000)))
    if "cost_micros" in u and u.get("cost_micros") is not None:
        return max(0, _int(u.get("cost_micros")))
    for key in ("cost", "total_cost", "cost_usd"):
        if key not in u:
            continue
        v = u.get(key)
        if isinstance(v, bool):
            continue
        if isinstance(v, (int, float)):
            amount = float(v)
            if math.isfinite(amount) and 0 <= amount <= _MAX_USAGE_VALUE / 1_000_000:
                return int(round(amount * 1_000_000))
            continue
        if isinstance(v, str):
            try:
                amount = float(v)
                if math.isfinite(amount) and 0 <= amount <= _MAX_USAGE_VALUE / 1_000_000:
                    return int(round(amount * 1_000_000))
            except (ValueError, OverflowError):
                continue
        if not isinstance(v, dict):
            continue
        unit = str(v.get("currency") or v.get("unit") or "").lower()
        for k2 in ("total", "amount", "usd", "total_cost", "value", "val"):
            v2 = v.get(k2)
            if isinstance(v2, bool) or not isinstance(v2, (int, float)):
                continue
            if not math.isfinite(float(v2)) or float(v2) < 0:
                continue
            if float(v2) > _MAX_USAGE_VALUE / 10_000:
                continue
            if unit in ("cent", "cents"):
                return int(round(float(v2) * 10_000))
            return int(round(float(v2) * 1_000_000))
    return None


def usage_from_obj(obj: dict, *, reasoning_separate: bool = False) -> dict:
    """Normalize Chat Completions or Responses usage.

    prompt/input tokens include cache reads and writes. output_tokens is Fuse's
    canonical generated-token count and always includes reasoning_tokens.
    cache_usage_status records whether the upstream reported the cached /
    cache-write split at all: "known" (a reported zero is a real zero) versus
    "unknown" (no cache fields, so the zeros are placeholders). usage_status
    stays a total-token completeness signal.
    """
    if not isinstance(obj, dict) or not isinstance(obj.get("usage"), dict):
        return {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "output_tokens": 0,
            "visible_tokens": 0,
            "reasoning_tokens": 0,
            "cached_tokens": 0,
            "cache_write_tokens": 0,
            "audio_tokens": 0,
            "image_tokens": 0,
            "total_tokens": 0,
            "cost_micros": None,
            "usage_status": "unknown",
            "cache_usage_status": "unknown",
        }
    u = obj["usage"]
    details_c = u.get("completion_tokens_details") if isinstance(u.get("completion_tokens_details"), dict) else {}
    details_p = u.get("prompt_tokens_details") if isinstance(u.get("prompt_tokens_details"), dict) else {}
    if not details_c and isinstance(u.get("output_tokens_details"), dict):
        details_c = u["output_tokens_details"]
    if not details_p and isinstance(u.get("input_tokens_details"), dict):
        details_p = u["input_tokens_details"]
    prompt = max(0, _int(u.get("prompt_tokens") if u.get("prompt_tokens") is not None else u.get("input_tokens")))
    completion = max(0, _int(u.get("completion_tokens") if u.get("completion_tokens") is not None else u.get("output_tokens")))
    reasoning = max(0, _int(details_c.get("reasoning_tokens") or u.get("reasoning_tokens")))
    # Presence, not truthiness: a reported 0 is a known zero, a missing field
    # is an absent metric that must not be read as "no cache used".
    cache_status = u.get("cache_usage_status")
    if cache_status not in ("known", "unknown", "partial"):
        cache_status = "known" if any(
            details_p.get(key) is not None or u.get(key) is not None
            for key in ("cached_tokens", "cache_write_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")
        ) else "unknown"
    # Anthropic-style names are the fallback (NanoGPT sends them alongside or
    # instead of the OpenAI fields); prompt_tokens still includes both.
    cached = max(0, _int(
        details_p["cached_tokens"] if "cached_tokens" in details_p
        else u["cached_tokens"] if "cached_tokens" in u else u.get("cache_read_input_tokens")
    ))
    cache_write = max(0, _int(
        details_p["cache_write_tokens"] if "cache_write_tokens" in details_p
        else u["cache_write_tokens"] if "cache_write_tokens" in u else u.get("cache_creation_input_tokens")
    ))
    cached = min(cached, prompt)
    cache_write = min(cache_write, max(prompt - cached, 0))
    audio = min(
        max(0, _int(details_p.get("audio_tokens")))
        + max(0, _int(details_c.get("audio_tokens"))),
        _MAX_USAGE_VALUE,
    )
    image = max(0, _int(details_p.get("image_tokens") or u.get("image_tokens")))
    total = max(0, _int(u.get("total_tokens")))
    # OpenAI folds reasoning into completion_tokens. xAI reports them as
    # disjoint (visible completion + reasoning). Detect that even when the
    # caller did not pass reasoning_separate, so stream taps stay correct.
    # A reported total is the provider's own statement of which it is, so it
    # outranks both the caller's hint and the reasoning > completion guess
    # (Kimi, OpenRouter and MiMo report total = prompt + completion with
    # reasoning inside completion; adding it again double-counts output).
    disjoint = reasoning_separate
    if reasoning:
        if total and total == prompt + completion + reasoning:
            disjoint = True
        elif total and total == prompt + completion:
            disjoint = False
        elif not disjoint and reasoning > completion:
            disjoint = True
    output = min(completion + reasoning, _MAX_USAGE_VALUE) if disjoint else max(completion, reasoning)
    visible = completion if disjoint else max(completion - reasoning, 0)
    if not total:
        total = min(prompt + output, _MAX_USAGE_VALUE)
    # Some xAI schemas expose visible completion + separate reasoning while
    # documenting total_tokens as authoritative. Preserve any extra generated
    # tokens represented by that total.
    if total > prompt + output:
        output = total - prompt
    total = max(total, min(prompt + output, _MAX_USAGE_VALUE))
    reported_cost = _cost_micros(u)
    if reported_cost is None:
        reported_cost = _cost_micros(obj)
    nanogpt = obj.get("x_nanogpt_pricing")
    if isinstance(nanogpt, dict):
        if reported_cost is None:
            reported_cost = _cost_micros(nanogpt)
        # NanoGPT reports 0 for subscription-covered calls: leave those to the
        # price estimate (API equivalent), as for every other pool.
        if reported_cost == 0:
            reported_cost = None
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "output_tokens": output,
        "visible_tokens": visible,
        "reasoning_tokens": reasoning,
        "cached_tokens": cached,
        "cache_write_tokens": cache_write,
        "audio_tokens": audio,
        "image_tokens": image,
        "total_tokens": total,
        "cost_micros": reported_cost,
        "usage_status": "complete" if u else "unknown",
        "cache_usage_status": cache_status,
    }


class UsageStream:
    """Async iterator that carries a mutable usage dict. CPython async generators
    reject arbitrary attributes, so ChatResult.stream cannot hang usage on gen itself.
    """

    def __init__(self, agen, usage: dict, on_close=None, hints: dict | None = None):
        self._agen = agen
        self.usage = usage
        # Live non-usage signals from a translating source, e.g. the name of a
        # tool call it is still assembling ("tool_call"). Never billed.
        self.hints = hints if hints is not None else {}
        self._on_close = on_close
        self._closed = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return await self._agen.__anext__()
        except BaseException:
            await self._finish()
            raise

    async def _finish(self):
        if self._closed:
            return
        self._closed = True
        if self._on_close is not None:
            result = self._on_close()
            if hasattr(result, "__await__"):
                await result

    async def aclose(self):
        try:
            close = getattr(self._agen, "aclose", None)
            if close is not None:
                await close()
        finally:
            await self._finish()


def merge_usage(parsed: dict, extra: dict | None) -> dict:
    """Merge one request's cumulative stream usage without freezing early counts."""
    if not extra:
        return parsed or {}
    if not parsed or not any(v for v in parsed.values() if v):
        return dict(extra)
    merged = dict(parsed)
    for key, value in extra.items():
        if key.endswith("_tokens"):
            # Some compatible providers emit usage before the final frame.
            # These are cumulative snapshots, not additive billing deltas.
            merged[key] = max(0, _int(merged.get(key)), _int(value))
            continue
        if key == "cost_micros":
            if merged.get(key) is None and value is not None:
                merged[key] = value
            continue
        if key == "usage_status":
            if value == "complete" or not merged.get(key):
                merged[key] = value
            continue
        if key == "cache_usage_status":
            # Once one frame reported the cache split, later frames without
            # it must not downgrade the request back to unknown.
            if value == "known" or not merged.get(key):
                merged[key] = value
            continue
        if value and not merged.get(key):
            merged[key] = value
    return merged


def _error_object(status: int, raw: bytes) -> dict:
    """Upstream error body as an error object. Keeps the upstream status: an
    HTML 413/400 page is still the request's fault, not a retryable 502."""
    try:
        obj = json.loads(raw)
    except (ValueError, RecursionError):
        obj = None
    if isinstance(obj, dict):
        return obj
    text = raw.decode("utf-8", errors="replace").strip() if isinstance(raw, (bytes, bytearray)) else ""
    if text.lstrip().startswith("<"):
        text = ""
    return {"error": {"message": scrub_secrets(text)[:500] or f"upstream returned HTTP {status}"}}


def _chat_failure(status: int, message: str, phase: str, usage: dict | None = None) -> tuple:
    return status, {"error": {
        "message": message, "failure_phase": phase, "retryable": phase == "pre_generation",
    }}, None, {**(usage or {}), "failure_phase": phase}, {}


async def openai_chat(
    client: httpx.AsyncClient,
    url: str,
    headers: dict,
    body: dict,
    *,
    close_client: bool = False,
    reasoning_separate: bool = False,
) -> tuple[int, dict | None, AsyncIterator[bytes] | None, dict, dict[str, str]]:
    """POST chat/completions with normalized transport/protocol failures."""
    payload = dict(body)
    stream = bool(payload.get("stream"))
    if stream:
        opts = payload.get("stream_options")
        if not isinstance(opts, dict):
            opts = {}
        opts["include_usage"] = True
        payload["stream_options"] = opts

    if stream:
        req = client.build_request("POST", url, json=payload, headers=headers)
        try:
            resp = await client.send(req, stream=True)
        except httpx.TimeoutException as exc:
            if close_client:
                await client.aclose()
            return _chat_failure(504, f"upstream timeout ({type(exc).__name__})",
                                 "pre_generation" if isinstance(exc, (httpx.ConnectTimeout, httpx.PoolTimeout)) else "request_sent")
        except httpx.HTTPError as exc:
            if close_client:
                await client.aclose()
            return _chat_failure(502, f"upstream transport failure ({type(exc).__name__})",
                                 "pre_generation" if isinstance(exc, httpx.ConnectError) else "request_sent")
        if resp.status_code != 200:
            safe_headers = {}
            if resp.headers.get("retry-after"):
                safe_headers["Retry-After"] = resp.headers["retry-after"][:128]
            try:
                err = await resp.aread()
            except httpx.TimeoutException as exc:
                return 504, {"error": {"message": f"upstream timeout ({type(exc).__name__})"}}, None, {}, safe_headers
            except httpx.HTTPError as exc:
                return 502, {"error": {"message": f"upstream transport failure ({type(exc).__name__})"}}, None, {}, safe_headers
            finally:
                await resp.aclose()
                if close_client:
                    await client.aclose()
            return resp.status_code, _error_object(resp.status_code, err), None, {}, safe_headers

        collected: dict = {}

        async def gen() -> AsyncIterator[bytes]:
            buf = b""

            def inspect(line: bytes) -> None:
                text = line.decode(errors="replace").strip()
                if not text.startswith("data:"):
                    return
                data = text[5:].strip()
                if not data:
                    return
                if data == "[DONE]":
                    if collected.get("stream_status") != "failed":
                        collected["stream_status"] = "complete"
                    return
                try:
                    obj = json.loads(data)
                except json.JSONDecodeError as exc:
                    collected["stream_status"] = "failed"
                    raise ValueError("upstream sent malformed SSE JSON") from exc
                if not isinstance(obj, dict):
                    collected["stream_status"] = "failed"
                    raise ValueError("upstream sent an invalid SSE object")
                if obj.get("error"):
                    collected["stream_status"] = "failed"
                # Only frames that carry a usage dict update the counts; a
                # later delta frame must not zero what an earlier usage frame
                # reported.
                if isinstance(obj.get("usage"), dict) and obj["usage"]:
                    collected.update(usage_from_obj(obj, reasoning_separate=reasoning_separate))

            try:
                async for chunk in resp.aiter_bytes():
                    buf += chunk
                    # Inspect complete lines first: a capped chunk may still
                    # carry the usage and [DONE] frames after a huge one.
                    while b"\n" in buf:
                        line, buf = buf.split(b"\n", 1)
                        inspect(line)
                    if len(buf) > 1_048_576:
                        buf = b""
                    yield chunk
                if buf.strip():
                    inspect(buf)
                if collected.get("stream_status") != "complete":
                    collected["stream_status"] = "failed"
            finally:
                await resp.aclose()
                if close_client:
                    await client.aclose()

        # Same dict the generator mutates. Pool sources wrap this for sticky
        # cache routing; returning {} here dropped stream usage and skipped bind.
        return 200, None, UsageStream(gen(), collected), collected, {}

    try:
        try:
            resp = await client.post(url, json=payload, headers=headers)
        except httpx.TimeoutException as exc:
            return _chat_failure(504, f"upstream timeout ({type(exc).__name__})",
                                 "pre_generation" if isinstance(exc, (httpx.ConnectTimeout, httpx.PoolTimeout)) else "request_sent")
        except httpx.HTTPError as exc:
            return _chat_failure(502, f"upstream transport failure ({type(exc).__name__})",
                                 "pre_generation" if isinstance(exc, httpx.ConnectError) else "request_sent")
        if resp.status_code != 200:
            safe_headers = {}
            if resp.headers.get("retry-after"):
                safe_headers["Retry-After"] = resp.headers["retry-after"][:128]
            return resp.status_code, _error_object(resp.status_code, resp.content), None, {}, safe_headers
        try:
            obj = resp.json()
        except ValueError:
            return _chat_failure(502, "upstream returned non-JSON data", "response_received")
        if not isinstance(obj, dict):
            return _chat_failure(502, "upstream returned an invalid JSON schema", "response_received")
        # Before the shape checks: a malformed body can still report tokens
        # and cost the upstream billed, and those must be recorded.
        usage = usage_from_obj(obj, reasoning_separate=reasoning_separate)
        if resp.status_code == 200:
            choices = obj.get("choices")
            if not isinstance(choices, list) or not choices or not all(isinstance(choice, dict) for choice in choices):
                return _chat_failure(502, "upstream response has invalid choices", "response_received", usage)
            if any(
                not isinstance(choice.get("message"), dict)
                for choice in choices
            ):
                return _chat_failure(502, "upstream response has an invalid message", "response_received", usage)
        safe_headers = {}
        if resp.headers.get("retry-after"):
            safe_headers["Retry-After"] = resp.headers["retry-after"][:128]
        return resp.status_code, obj, None, usage, safe_headers
    finally:
        if close_client:
            await client.aclose()
