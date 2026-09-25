import asyncio
import json
import logging
import math

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from . import accounts, db, opencode as opencode_mod
from .config import settings
from .proxy import merge_usage, usage_from_obj
from .security import client_ip, limiter
from .sources import (
    POOL_RETRYABLE,
    POOL_TYPES,
    catalog_unavailable_for,
    models_union,
    openrouter_pool_fallback,
    pick,
    user_has_chatgpt,
    user_has_devin,
    user_has_grok,
    user_has_kimi,
    user_has_mimo,
    user_has_claude,
    user_has_zai,
)
from .sources import pool_policy
from .sources.base import ChatResult
from .sources import openrouter as openrouter_mod
from .sources import devin as devin_mod
from .sources import kimi as kimi_mod
from .sources import mimo as mimo_mod
from .sources import claude as claude_mod
from .sources.pool_session import session_id_from

router = APIRouter()
_LINE_CAP = 1_048_576
_MAX_MESSAGES = 1_000
_MAX_MESSAGE_CHARS = 1_500_000
_MAX_RETAINED_MESSAGES = 32_768
_MAX_RETAINED_CHARS = 48_000_000
_REASONING_EFFORTS = {"none", "minimal", "low", "medium", "high", "xhigh", "max"}
logger = logging.getLogger(__name__)


def _bearer(request: Request) -> str:
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return auth.strip()


def require_key(request: Request):
    ip = client_ip(request)
    if not limiter.allow(f"api-ip:{ip}", settings.api_rate_per_minute * 4, 60):
        raise HTTPException(429, "Too many requests")
    token = _bearer(request)
    if not token:
        raise HTTPException(401, "Missing API key")
    user = accounts.user_by_api_key(token)
    if not user:
        raise HTTPException(401, "Invalid API key")
    if not limiter.allow(f"api-user:{user['id']}", settings.api_rate_per_minute, 60):
        raise HTTPException(429, "Too many requests")
    return user


def _error_message(body) -> str:
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, str) and err.strip():
            return err.strip()[:400]
        if isinstance(err, dict):
            msg = err.get("message") or err.get("msg") or ""
            if isinstance(msg, str) and msg.strip():
                return msg.strip()[:400]
        for key in ("message", "msg", "detail"):
            val = body.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()[:400]
    return ""


def _upstream_error(status: int, body=None):
    code = 502 if status == 401 else status if 400 <= status <= 599 else 502
    msg = _error_message(body) or "upstream error"
    err_type = "access_denied" if code in (401, 402, 403) else "api_error"
    return JSONResponse(
        {"error": {"message": msg, "type": err_type, "code": code}},
        status_code=code,
    )


def _safe_record_usage(*args, **kwargs) -> bool:
    try:
        provider = kwargs.get("provider") or ""
        if provider == "devin" and len(args) >= 4 and isinstance(args[3], str):
            args = list(args)
            args[3] = devin_mod.canonical_upstream(args[3]) or args[3]
        db.record_usage(*args, **kwargs)
        return True
    except Exception:
        logger.exception("Usage accounting degraded after an upstream result")
        return False


def _validate_messages(messages, *, retained=False) -> None:
    max_messages = _MAX_RETAINED_MESSAGES if retained else _MAX_MESSAGES
    max_chars = _MAX_RETAINED_CHARS if retained else _MAX_MESSAGE_CHARS
    if not isinstance(messages, list) or not messages:
        raise HTTPException(400, "messages must be a non-empty array")
    if len(messages) > max_messages:
        raise HTTPException(
            400,
            f"too many messages ({len(messages)}; max {max_messages}) - "
            "compact the conversation or start a new session",
        )
    roles = {"system", "developer", "user", "assistant", "tool"}
    total = 0
    for index, message in enumerate(messages):
        if not isinstance(message, dict):
            raise HTTPException(400, f"messages[{index}] must be an object")
        if message.get("role") not in roles:
            raise HTTPException(400, f"messages[{index}] has an unsupported role")
        content = message.get("content")
        if content is not None and not isinstance(content, (str, list)):
            raise HTTPException(400, f"messages[{index}].content must be a string or array")
        if isinstance(content, str):
            total += len(content)
        elif isinstance(content, list):
            if len(content) > 100:
                raise HTTPException(400, f"messages[{index}].content has too many parts (max 100)")
            for part_index, part in enumerate(content):
                if not isinstance(part, dict) or not isinstance(part.get("type"), str):
                    raise HTTPException(
                        400, f"messages[{index}].content[{part_index}] must be an object with a type"
                    )
                text = part.get("text")
                if text is not None and not isinstance(text, str):
                    raise HTTPException(
                        400, f"messages[{index}].content[{part_index}].text must be a string"
                    )
                total += len(text or "")
                image = part.get("image_url")
                if image is not None and not isinstance(image, (str, dict)):
                    raise HTTPException(
                        400,
                        f"messages[{index}].content[{part_index}].image_url must be a string or object",
                    )
                if isinstance(image, dict) and not isinstance(image.get("url"), str):
                    raise HTTPException(
                        400, f"messages[{index}].content[{part_index}].image_url.url must be a string"
                    )
                if isinstance(image, str):
                    total += len(image)
                elif isinstance(image, dict):
                    total += len(image["url"])
                if part.get("type") in ("file", "input_file"):
                    file = part.get("file")
                    file = file if isinstance(file, dict) else part
                    if not any(isinstance(file.get(key), str) and file[key] for key in ("file_id", "file_data")):
                        raise HTTPException(
                            400,
                            f"messages[{index}].content[{part_index}] must include file_id or file_data",
                        )
                    for key in ("file_id", "file_data", "filename"):
                        value = file.get(key)
                        if value is not None and not isinstance(value, str):
                            raise HTTPException(
                                400,
                                f"messages[{index}].content[{part_index}].{key} must be a string",
                            )
                        if isinstance(value, str):
                            total += len(value)
        calls = message.get("tool_calls")
        reasoning = message.get("reasoning_content")
        if reasoning is not None:
            if not isinstance(reasoning, str):
                raise HTTPException(400, f"messages[{index}].reasoning_content must be a string")
            total += len(reasoning)
        if calls is not None:
            if not isinstance(calls, list) or len(calls) > 100:
                raise HTTPException(400, f"messages[{index}].tool_calls must be an array (max 100)")
            for call_index, call in enumerate(calls):
                fn = call.get("function") if isinstance(call, dict) else None
                if not isinstance(fn, dict) or not isinstance(fn.get("name"), str):
                    raise HTTPException(
                        400, f"messages[{index}].tool_calls[{call_index}] must include function.name"
                    )
                if fn.get("arguments") is not None and not isinstance(fn.get("arguments"), (str, dict)):
                    raise HTTPException(
                        400,
                        f"messages[{index}].tool_calls[{call_index}].function.arguments "
                        "must be a string or object",
                    )
                arguments = fn.get("arguments") or ""
                total += len(arguments) if isinstance(arguments, str) else len(json.dumps(arguments))
        if total > max_chars:
            break
    if total > max_chars:
        raise HTTPException(
            400,
            f"messages too large ({total} chars; max {max_chars}) - "
            "compact the conversation or start a new session",
        )


def _validate_reasoning(body: dict) -> None:
    effort = body.get("reasoning_effort")
    if effort is not None and (not isinstance(effort, str) or effort not in _REASONING_EFFORTS):
        raise HTTPException(400, "Invalid reasoning_effort")
    reasoning = body.get("reasoning")
    if reasoning is not None and not isinstance(reasoning, dict):
        raise HTTPException(400, "reasoning must be an object")
    nested_effort = reasoning.get("effort") if isinstance(reasoning, dict) else None
    if nested_effort is not None and (
        not isinstance(nested_effort, str) or nested_effort not in _REASONING_EFFORTS
    ):
        raise HTTPException(400, "Invalid reasoning.effort")
    if "thinking" not in body:
        return
    thinking = body["thinking"]
    if not isinstance(thinking, dict) or thinking.get("type") not in ("enabled", "disabled"):
        raise HTTPException(400, "thinking.type must be enabled or disabled")
    if "clear_thinking" in thinking and not isinstance(thinking["clear_thinking"], bool):
        raise HTTPException(400, "thinking.clear_thinking must be a boolean")


async def _run_chat(src, body: dict, user_id: int, session_id: str | None = None):
    if src.type_id == "openrouter":
        return await src.chat(body, user_id=user_id, session_id=session_id)
    if src.type_id in POOL_TYPES:
        return await src.chat(body, session_id=session_id, user_id=user_id)
    if src.type_id in ("openai", "ollama"):
        return await src.chat(body, session_id=session_id)
    return await src.chat(body)


def _record_attempts(result, user, src, upstream: str, requested_model: str) -> None:
    """Persist retried upstream attempts: quota may have been consumed even
    though the attempt failed and no tokens were reported."""
    for attempt in getattr(result, "attempts", None) or []:
        if not isinstance(attempt, dict):
            continue
        usage = attempt.get("usage") if isinstance(attempt.get("usage"), dict) else {}
        if not usage and not attempt.get("status"):
            continue
        _safe_record_usage(
            user["id"],
            src.source_id,
            attempt.get("credential_id"),
            upstream,
            usage,
            key_id=user.get("key_id"),
            provider=src.type_id,
            requested_model=requested_model,
            request_status="failed",
            count_request=False,
        )


async def _tap(
    stream,
    user_id: int,
    source_id: int,
    cred_id,
    model: str,
    *,
    key_id=None,
    provider: str = "",
    requested_model: str = "",
):
    usage = {}
    buf = b""
    saw_done = False
    request_status = "success"

    def inspect(line: bytes) -> None:
        nonlocal usage, saw_done, request_status
        text = line.decode(errors="replace").strip()
        if not text.startswith("data:"):
            return
        data = text[5:].strip()
        if not data:
            return
        if data == "[DONE]":
            saw_done = True
            return
        try:
            obj = json.loads(data)
        except json.JSONDecodeError:
            request_status = "failed"
            return
        if not isinstance(obj, dict):
            request_status = "failed"
            return
        if obj.get("error"):
            request_status = "failed"
        got = usage_from_obj(obj, reasoning_separate=(provider == "grok"))
        if got:
            usage = got

    try:
        async for chunk in stream:
            buf += chunk
            if len(buf) > _LINE_CAP:
                buf = b""
                yield chunk
                continue
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                inspect(line)
            yield chunk
        if buf.strip():
            inspect(buf)
    except asyncio.CancelledError:
        # Client disconnected mid-stream. The provider may still have consumed
        # quota; record whatever usage we saw as a cancelled request instead of
        # a silent zero-token success.
        request_status = "cancelled"
        raise
    except Exception as exc:
        request_status = "failed"
        if not saw_done:
            error = {
                "error": {
                    "message": f"upstream stream failed ({type(exc).__name__})",
                    "type": "api_error",
                    "code": 502,
                }
            }
            yield f"data: {json.dumps(error, separators=(',', ':'))}\n\n".encode()
            yield b"data: [DONE]\n\n"
    finally:
        if request_status == "success" and not saw_done:
            # A stream that never reached [DONE] was truncated or errored
            # inline; it is not a successful request even at HTTP 200.
            request_status = "failed"
        extra = getattr(stream, "usage", None)
        usage = merge_usage(usage, extra if isinstance(extra, dict) else None)
        usage.pop("stream_status", None)
        # usage_status stays "complete" when a usage frame arrived (tokens are
        # known even if framing truncated); without one it is unknown.
        if usage and not any(v for k, v in usage.items() if k.endswith("tokens") and v):
            usage["usage_status"] = "unknown"
        elif usage:
            usage.setdefault("usage_status", "complete")
        _safe_record_usage(
            user_id,
            source_id,
            cred_id,
            model,
            usage,
            key_id=key_id,
            provider=provider,
            requested_model=requested_model,
            request_status=request_status,
        )
        close = getattr(stream, "aclose", None)
        if close:
            try:
                await close()
            except Exception:
                logger.warning("Could not close upstream stream", exc_info=True)


@router.get("/v1/models")
async def list_models(request: Request):
    user = require_key(request)
    names = await models_union(
        allow_grok=user_has_grok(user["id"]),
        allow_chatgpt=user_has_chatgpt(user["id"]),
        allow_zai=user_has_zai(user["id"]),
        allow_devin=user_has_devin(user["id"]),
        allow_kimi=user_has_kimi(user["id"]),
        allow_mimo=user_has_mimo(user["id"]),
        allow_claude=user_has_claude(user["id"]),
        user_id=user["id"],
    )
    if not names and catalog_unavailable_for(""):
        raise HTTPException(503, "Model catalog is temporarily unavailable")
    return {
        "object": "list",
        "data": [{"id": n, "object": "model", "owned_by": "fuse"} for n in names],
    }


@router.get("/v1/opencode")
async def opencode_provider(request: Request):
    user = require_key(request)
    entries = await opencode_mod.model_options(user["id"])
    if not entries and catalog_unavailable_for(""):
        raise HTTPException(503, "Model catalog is temporarily unavailable")
    origin = (settings.public_base_url or "").rstrip("/")
    api_base = (origin + "/v1") if origin else str(request.base_url).rstrip("/") + "/v1"
    return {
        "object": "opencode.provider",
        "provider": {"fuse": opencode_mod.fuse_provider(entries, base_url=api_base)},
    }


async def invoke_chat(request: Request, user, body: dict, *, prefer_openrouter: bool = False):
    """Pick a source and run one completion.

    Returns (user, src, upstream, requested_model, result). Failed pool
    attempts are recorded. The caller records successful usage.
    prefer_openrouter: after a mid-stream pool death, skip the pool and
    map the same Fuse model onto the user's OpenRouter key when one exists.
    """
    try:
        user = dict(user)
        return await _invoke_chat(request, user, body, prefer_openrouter=prefer_openrouter)
    except HTTPException as exc:
        model = body.get("model") if isinstance(body, dict) else ""
        messages = body.get("messages") if isinstance(body, dict) else None
        logger.warning(
            "Rejected chat request user=%s key=%s model=%r status=%s messages=%s detail=%s",
            user.get("id"),
            user.get("key_id"),
            (model or "")[:100] if isinstance(model, str) else model,
            exc.status_code,
            len(messages) if isinstance(messages, list) else "invalid",
            exc.detail,
        )
        raise


async def _invoke_chat(request: Request, user, body: dict, *, prefer_openrouter: bool = False):
    user = dict(user)
    if not isinstance(body, dict):
        raise HTTPException(400, "Invalid JSON")
    model = body.get("model") or ""
    if not isinstance(model, str) or len(model) > 200:
        raise HTTPException(400, "Invalid model")
    _validate_messages(body.get("messages"), retained=bool(request.scope.get("fuse_retained_history")))
    body = dict(body)
    if "reasoning_effort" not in body and "reasoningEffort" in body:
        body["reasoning_effort"] = body.pop("reasoningEffort")
    _validate_reasoning(body)
    if "stream" in body and not isinstance(body["stream"], bool):
        raise HTTPException(400, "Invalid stream")
    if body.get("max_tokens") is not None:
        try:
            value = body["max_tokens"]
            if isinstance(value, bool):
                raise ValueError
            number = float(value)
            if not math.isfinite(number) or not number.is_integer():
                raise ValueError
            body["max_tokens"] = max(1, min(int(number), settings.max_tokens))
        except (TypeError, ValueError, OverflowError):
            raise HTTPException(400, "Invalid max_tokens")
    grok_ok = user_has_grok(user["id"])
    chatgpt_ok = user_has_chatgpt(user["id"])
    zai_ok = user_has_zai(user["id"])
    devin_ok = user_has_devin(user["id"])
    kimi_ok = user_has_kimi(user["id"])
    mimo_ok = user_has_mimo(user["id"])
    claude_ok = user_has_claude(user["id"])
    picked = None
    if (
        prefer_openrouter
        and not devin_mod.is_devin_model(model)
        and not kimi_mod.is_kimi_model(model)
        and not mimo_mod.is_mimo_model(model)
        and not claude_mod.is_claude_model(model)
    ):
        picked = await openrouter_pool_fallback(model, user["id"])
    if picked is None:
        picked = await pick(
            model,
            allow_grok=grok_ok,
            allow_chatgpt=chatgpt_ok,
            allow_zai=zai_ok,
            allow_devin=devin_ok,
            allow_kimi=kimi_ok,
            allow_mimo=mimo_ok,
            allow_claude=claude_ok,
            user_id=user["id"],
        )
    if not picked:
        denied = openrouter_mod.denied_reason(model, user["id"])
        if denied:
            raise HTTPException(403, denied)
        if catalog_unavailable_for(model):
            raise HTTPException(503, "Model catalog is temporarily unavailable")
        raise HTTPException(404, "Unknown model")
    src, upstream = picked
    if src.type_id == "grok" and not grok_ok:
        raise HTTPException(403, "Connect a Grok account with an active subscription to use Grok models")
    if src.type_id == "chatgpt" and not chatgpt_ok:
        raise HTTPException(403, "Connect a ChatGPT account to use ChatGPT models")
    if src.type_id == "zai" and not zai_ok:
        if pool_policy.uses_shared_pool(user["id"], "zai"):
            raise HTTPException(403, "Connect an x.ai, OpenAI, or Z.AI subscription to use GLM models")
        raise HTTPException(403, "Connect a Z.AI key to use GLM models")
    if src.type_id == "devin" and not devin_ok:
        raise HTTPException(403, "Connect a Devin account to use Devin models")
    if src.type_id == "kimi" and not kimi_ok:
        raise HTTPException(403, "Connect a Kimi membership to use Kimi models")
    if src.type_id == "mimo" and not mimo_ok:
        raise HTTPException(403, "Connect a Xiaomi Token Plan to use MiMo models")
    if src.type_id == "claude" and not claude_ok:
        raise HTTPException(403, "Connect a Claude account to use Claude models")
    requested = model
    body["model"] = upstream
    session_id = None
    if src.type_id in POOL_TYPES or src.type_id in ("openai", "ollama", "openrouter"):
        session_id = session_id_from(request.headers, body, user_id=user["id"], model=upstream)
    try:
        result = await _run_chat(src, body, user["id"], session_id=session_id)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.exception("Source invocation failed provider=%s", src.type_id)
        result = ChatResult(
            status=502,
            body={"error": {"message": f"upstream request failed ({type(exc).__name__})"}},
            model=upstream,
        )
    _record_attempts(result, user, src, upstream, requested)
    if (
        result.status != 200
        and result.stream is None
        and src.type_id in POOL_TYPES
        and result.status in POOL_RETRYABLE
    ):
        fallback = None
        requested_id = str(requested)
        if not devin_mod.is_devin_model(requested_id) and not kimi_mod.is_kimi_model(requested_id) and not mimo_mod.is_mimo_model(requested_id) and not claude_mod.is_claude_model(requested_id):
            fallback = await openrouter_pool_fallback(requested_id, user["id"])
        if fallback:
            # The pool attempt may still have burned quota, so its tokens/cost
            # are recorded; but the fallback response carries the single
            # request count so one client request is never counted twice.
            _safe_record_usage(
                user["id"],
                src.source_id,
                result.credential_id,
                upstream,
                result.usage,
                key_id=user.get("key_id"),
                provider=src.type_id,
                requested_model=requested,
                request_status="failed",
                count_request=False,
            )
            or_src, or_upstream = fallback
            retry_body = dict(body)
            retry_body["model"] = or_upstream
            retry_session = session_id_from(
                request.headers, retry_body, user_id=user["id"], model=or_upstream
            )
            try:
                retry = await _run_chat(or_src, retry_body, user["id"], session_id=retry_session)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception("OpenRouter fallback failed provider=openrouter")
                retry = ChatResult(
                    status=502,
                    body={"error": {"message": f"upstream request failed ({type(exc).__name__})"}},
                    model=or_upstream,
                )
            _record_attempts(retry, user, or_src, or_upstream, requested)
            src, upstream, result = or_src, or_upstream, retry
    return user, src, upstream, requested, result


async def dispatch_chat(request: Request, user, body: dict):
    user, src, upstream, requested, result = await invoke_chat(request, user, body)
    if result.status != 200:
        _safe_record_usage(
            user["id"],
            src.source_id,
            result.credential_id,
            upstream,
            result.usage,
            key_id=user.get("key_id"),
            provider=src.type_id,
            requested_model=requested,
            request_status="failed",
            count_request=True,
        )
        if result.status in (402, 403) and isinstance(result.body, dict):
            msg = _error_message(result.body)
            if msg:
                return JSONResponse(
                    {"error": {"message": msg, "type": "access_denied", "code": result.status}},
                    status_code=result.status,
                    headers=result.response_headers,
                )
        response = _upstream_error(result.status, result.body)
        response.headers.update(result.response_headers)
        return response
    key_id = user.get("key_id")
    if result.stream is not None:
        return StreamingResponse(
            _tap(
                result.stream,
                user["id"],
                src.source_id,
                result.credential_id,
                upstream,
                key_id=key_id,
                provider=src.type_id,
                requested_model=requested,
            ),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )
    _safe_record_usage(
        user["id"],
        src.source_id,
        result.credential_id,
        upstream,
        result.usage,
        key_id=key_id,
        provider=src.type_id,
        requested_model=requested,
    )
    return JSONResponse(result.body or {})


@router.post("/v1/chat/completions")
async def chat_completions(request: Request):
    user = require_key(request)
    try:
        body = await request.json()
    except json.JSONDecodeError:
        logger.warning(
            "Rejected chat request user=%s status=400 detail=unparseable JSON body", user["id"]
        )
        raise HTTPException(400, "Invalid JSON")
    return await dispatch_chat(request, user, body)
