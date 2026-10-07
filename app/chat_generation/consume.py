"""Consume one provider round into a done-summary.

Handles both the non-streaming body and the streamed SSE case:
accumulates content/reasoning/tool calls/thinking blocks, merges
usage, holds back provider-failure sentences painted as content,
and reports liveness beats while the model is silent."""

from __future__ import annotations

import asyncio
import logging

from ..chat_prompts import _text_of
from ..proxy import merge_usage, usage_from_obj

from .parsing import (
    _finalize_assistant_payload,
    _take_calls,
    _take_thinking,
)
from .policy import _error_obj, _provider_error_content
from .stream import (
    CHUNK_GAP_TIMEOUT_S,
    IDLE_TIMEOUT_S,
    PROVIDER_BEAT_S,
    _client_obj,
    _paced_chunks,
    _parse_sse_line,
    _terminated_chunks,
)

logger = logging.getLogger(__name__)


def _add_usage(total: dict, extra: dict | None) -> dict:
    extra = extra or {}
    if not total:
        return dict(extra)
    merged = dict(total)
    for key, value in extra.items():
        if key == "cache_usage_status":
            prior = merged.get(key)
            merged[key] = value if prior is None else (prior if prior == value else "partial")
            continue
        if key in ("usage_status",):
            if value == "complete" or not merged.get(key):
                merged[key] = value
            continue
        if key == "cost_micros":
            a, b = merged.get(key), value
            if isinstance(a, int) and isinstance(b, int):
                merged[key] = a + b
            elif b is not None and a is None:
                merged[key] = b
            continue
        if isinstance(value, int) and isinstance(merged.get(key), int):
            merged[key] = min(merged[key] + value, 9_000_000_000_000_000)
        elif isinstance(value, int) and not merged.get(key):
            merged[key] = value
    return merged


async def consume_round(result, user, src, upstream: str, requested: str, *, coding: bool = False):
    from .. import api

    calls: dict[int, dict] = {}
    finish = None
    content = ""
    reasoning = ""
    thinking: list[dict] = []
    usage = {}
    error = None
    if result.stream is None:
        usage = result.usage or {}
        api._safe_record_usage(
            user["id"],
            src.source_id,
            result.credential_id,
            upstream,
            usage,
            key_id=user.get("key_id"),
            provider=src.type_id,
            requested_model=requested,
        )
        body = result.body if isinstance(result.body, dict) else {}
        choice = (body.get("choices") or [{}])[0] if isinstance(body.get("choices"), list) else {}
        message = choice.get("message") if isinstance(choice, dict) else {}
        if isinstance(message, dict):
            content = _text_of(message.get("content"))
            reasoning = _text_of(message.get("reasoning_content")) or _text_of(message.get("reasoning"))
            _take_calls(calls, message.get("tool_calls"))
            _take_thinking(thinking, message.get("claude_thinking"))
        finish = choice.get("finish_reason") if isinstance(choice, dict) else None
        content, reasoning, native, finish = _finalize_assistant_payload(
            content, reasoning, [calls[i] for i in sorted(calls)], finish, coding=coding
        )
        if isinstance(body, dict) and body.get("error"):
            error = body
        elif not native and (
            str(finish or "").lower() in ("error", "network_error") or _provider_error_content(content)
        ):
            error = _error_obj(503, {"error": {"message": (content or "upstream error").strip()}})
            content = ""
        if error:
            yield ("done", {"calls": native, "finish": finish, "content": content, "reasoning": reasoning, "thinking": thinking, "usage": usage, "error": error})
            return
        if body and isinstance(body.get("choices"), list) and body["choices"]:
            choice_out = dict(body["choices"][0]) if isinstance(body["choices"][0], dict) else {}
            message_out = dict(choice_out.get("message") or {}) if isinstance(choice_out.get("message"), dict) else {}
            message_out["content"] = content
            choice_out["message"] = message_out
            if finish:
                choice_out["finish_reason"] = finish
            body = {**body, "choices": [choice_out, *body["choices"][1:]]}
        safe = _client_obj(body) if body else None
        if safe:
            yield ("out", safe)
        yield ("done", {"calls": native, "finish": finish, "content": content, "reasoning": reasoning, "thinking": thinking, "usage": usage, "error": error})
        return

    tapped = api._tap(
        result.stream,
        user["id"],
        src.source_id,
        result.credential_id,
        upstream,
        key_id=user.get("key_id"),
        provider=src.type_id,
        requested_model=requested,
    )
    buf = b""
    terminal = False
    calling = False
    held_out: list[dict] = []
    try:
        async for kind, payload in _paced_chunks(
            _terminated_chunks(tapped), IDLE_TIMEOUT_S, CHUNK_GAP_TIMEOUT_S, PROVIDER_BEAT_S
        ):
            if kind == "beat":
                yield ("beat", payload)
                continue
            chunk = payload
            buf += chunk
            if len(buf) > 2_500_000:
                raise ValueError("provider stream frame limit exceeded")
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                parsed = _parse_sse_line(line.decode(errors="replace"))
                if parsed == "[DONE]":
                    terminal = True
                    continue
                if not isinstance(parsed, dict):
                    continue
                obj = parsed
                if obj.get("error"):
                    # Errors travel in the done summary so the caller can
                    # decide between failing the turn and resuming it; a
                    # resumed round must not have already painted an error
                    # into the client's run state.
                    error = obj
                    continue
                got = usage_from_obj(obj, reasoning_separate=(src.type_id == "grok"))
                if got:
                    usage = merge_usage(usage, got) if usage else dict(got)
                choice = (obj.get("choices") or [{}])[0] if isinstance(obj.get("choices"), list) else {}
                if isinstance(choice, dict):
                    delta = choice.get("delta") if isinstance(choice.get("delta"), dict) else {}
                    message = choice.get("message") if isinstance(choice.get("message"), dict) else {}
                    content += _text_of(delta.get("content")) or (
                        _text_of(message.get("content")) if not choice.get("delta") else ""
                    )
                    reasoning += _text_of(delta.get("reasoning_content")) or _text_of(delta.get("reasoning")) or (
                        _text_of(message.get("reasoning_content")) or _text_of(message.get("reasoning"))
                        if not choice.get("delta")
                        else ""
                    )
                    _take_calls(calls, delta.get("tool_calls") or message.get("tool_calls"))
                    _take_thinking(thinking, delta.get("claude_thinking") or message.get("claude_thinking"))
                    if choice.get("finish_reason"):
                        finish = choice.get("finish_reason")
                    if calls and not calling:
                        # The model has moved on to writing a tool call: any
                        # thought before it is finished, not still streaming.
                        calling = True
                        yield ("out", {"fuse": {"event": "activity", "phase": "calling"}})
                safe = _client_obj(obj, hide_tool_calls=False)
                if safe:
                    candidate = (content or "").strip()
                    if not calls and candidate and _provider_error_content(candidate):
                        held_out.append(safe)
                    else:
                        for pending in held_out:
                            yield ("out", pending)
                        held_out.clear()
                        yield ("out", safe)
            if terminal or error:
                # The round is over. A proxy that keeps the socket open after
                # [DONE] (or its terminal error frame) must not trip the gap
                # timeout and mask a completed reply as a resumable failure.
                break
    except asyncio.CancelledError:
        raise
    except TimeoutError:
        if not error:
            error = _error_obj(502, {"error": {"message": "upstream timeout (idle)"}})
    except Exception as exc:
        logger.warning("Upstream stream read failed: %s", type(exc).__name__)
        if not error:
            error = _error_obj(502, {"error": {"message": f"upstream stream failed ({type(exc).__name__})"}})
    finally:
        close = getattr(tapped, "aclose", None)
        if close:
            try:
                await close()
            except Exception:
                pass
        extra = getattr(result.stream, "usage", None)
        if isinstance(extra, dict):
            usage = merge_usage(usage, extra)
    if not terminal and not finish and not error:
        error = _error_obj(502, {"error": {"message": "Upstream stream ended without a terminal frame."}})
    content, reasoning, native, finish = _finalize_assistant_payload(
        content, reasoning, [calls[i] for i in sorted(calls)], finish, coding=coding
    )
    if not error and not native and (
        str(finish or "").lower() in ("error", "network_error") or _provider_error_content(content)
    ):
        error = _error_obj(503, {"error": {"message": (content or "upstream error").strip()}})
        content = ""
        held_out.clear()
    else:
        for pending in held_out:
            yield ("out", pending)
        held_out.clear()
    yield ("done", {"calls": native, "finish": finish, "content": content, "reasoning": reasoning, "thinking": thinking, "usage": usage, "error": error})
