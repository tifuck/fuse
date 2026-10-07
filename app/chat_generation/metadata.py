"""Auxiliary metadata generation: compaction handoff summaries,
thread titles, and recaps.

The metadata loop takes its per-candidate attempt (`once`) as a narrow
explicit parameter, and each attempt takes the provider-round consumer
(`consume`) the same way. app.chat_runtime injects its module-level
names so existing `patch("app.chat_runtime._metadata_once")` and
`patch("app.chat_runtime._consume_round")` seams keep binding."""

from __future__ import annotations

import asyncio
import contextlib
import logging

from fastapi import HTTPException, Request

from .. import chats
from .. import context as ctxmod
from ..chat_prompts import TITLE_MAX_TOKENS
from ..config import settings

logger = logging.getLogger(__name__)


async def summarize_conversation(request: Request, user, model: str, head: list[dict], instructions: str = "", *, tools=None, limit: int | None = None, cache_key: str | None = None, consume) -> str:
    """Summarize with the configured cheap model, then the chat model.

    Either attempt failing degrades to the truncate strategy at the caller;
    a bad `context_summarizer_model` must not silently cost summary quality.
    """
    if request is not None:
        request.scope["fuse_retained_history"] = True
    primary = (model or "").strip()
    cheap = (settings.context_summarizer_model or "").strip()
    for attempt in ([cheap, primary] if cheap and cheap != primary else [primary]):
        text = await summarize_once(request, user, attempt, head, instructions, tools=tools, limit=limit, cache_key=cache_key, consume=consume)
        if text:
            return text
    return ""


async def summarize_once(request: Request, user, model: str, head: list[dict], instructions: str = "", *, tools=None, limit: int | None = None, cache_key: str | None = None, consume) -> str:
    from .. import api

    payload = {
        "model": (model or "").strip(),
        "messages": ctxmod.summarize_payload(head, instructions=instructions, limit=limit, tools=tools),
        "stream": False,
        # The handoff replaces the whole conversation, so it needs room to carry
        # the work. Reply caps are about chat answers, not internal handoffs.
        "max_tokens": max(16_000, min(32_000, settings.max_tokens)),
    }
    if tools:
        payload["tools"] = tools
    if cache_key:
        payload["prompt_cache_key"] = cache_key
    if not payload["model"] or len(payload["messages"]) < 2:
        return ""
    try:
        _user, src, upstream, requested, result = await api.invoke_chat(request, user, payload)
    except HTTPException:
        return ""
    except Exception:
        logger.exception("Context compaction request failed")
        return ""
    if result.status != 200:
        api._safe_record_usage(
            _user["id"],
            src.source_id,
            result.credential_id,
            upstream,
            result.usage,
            key_id=_user.get("key_id"),
            provider=src.type_id,
            requested_model=requested,
            request_status="failed",
            count_request=True,
        )
        return ""
    summary = None
    try:
        async with contextlib.aclosing(consume(result, _user, src, upstream, requested)) as provider:
            async for kind, payload_obj in provider:
                if kind == "done":
                    summary = payload_obj
    except Exception:
        logger.exception("Context compaction response failed")
        return ""
    if not summary or summary.get("error"):
        return ""
    return ctxmod.compact_result_body(summary.get("content") or "")[: ctxmod.MAX_SUMMARY_CHARS]


async def generate_metadata(request, user, model, text, system, limit, parser, *, once):
    primary = (settings.chat_metadata_model or "").strip()
    candidates = list(dict.fromkeys([m for m in (primary, model) if m]))
    for candidate in candidates:
        try:
            async with asyncio.timeout(max(1, min(30, settings.chat_metadata_timeout))):
                result = await once(request, user, candidate, text, system, limit, parser)
            if result:
                return result
        except TimeoutError:
            logger.warning("Metadata generation timed out model=%s", candidate)
    return ""


def _error_detail(body) -> str:
    error = body.get("error") if isinstance(body, dict) else None
    if isinstance(error, dict):
        error = error.get("message") or error
    if error is None and isinstance(body, dict):
        error = body.get("detail")
    return str(error if error is not None else body or "")[:300]


async def metadata_once(request, user, model, text, system, limit, parser, *, consume):
    from .. import api

    prompt = chats.clip_metadata_text(text or "", max(1, min(int(limit), chats.TITLE_REGEN_PROMPT_MAX)))
    if not prompt or not isinstance(model, str) or not model.strip():
        return ""
    body = {
        "model": model.strip(),
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        "stream": False,
        "max_tokens": TITLE_MAX_TOKENS,
    }
    try:
        _user, src, upstream, requested, result = await api.invoke_chat(request, user, body, cheap_reasoning=True)
    except HTTPException as exc:
        logger.warning(
            "Chat title request rejected model=%s status=%s detail=%s",
            model.strip()[:100], exc.status_code, exc.detail,
        )
        return ""
    except Exception:
        logger.exception("Chat title request failed")
        return ""
    if result.status != 200:
        api._safe_record_usage(
            _user["id"],
            src.source_id,
            result.credential_id,
            upstream,
            result.usage,
            key_id=_user.get("key_id"),
            provider=src.type_id,
            requested_model=requested,
            request_status="failed",
            count_request=True,
        )
        logger.warning(
            "Chat title request failed model=%s provider=%s status=%s detail=%s",
            requested[:100], src.type_id, result.status, _error_detail(result.body),
        )
        return ""
    summary = None
    try:
        async with contextlib.aclosing(consume(result, _user, src, upstream, requested)) as provider:
            async for kind, payload in provider:
                if kind == "done":
                    summary = payload
    except Exception:
        logger.exception("Chat title response failed")
        return ""
    if not summary or summary.get("error"):
        logger.warning(
            "Chat title response error model=%s provider=%s",
            requested[:100], src.type_id,
        )
        return ""
    raw = summary.get("content") or ""
    title = parser(raw)
    if not title:
        logger.info(
            "Chat title response had no usable content model=%s reasoning=%s finish=%s",
            requested[:100],
            len(str(summary.get("reasoning") or "")),
            summary.get("finish"),
        )
    return title
