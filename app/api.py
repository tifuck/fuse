import asyncio
import base64
import binascii
import json
import logging
import math

import anyio
from fastapi import APIRouter, HTTPException, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import JSONResponse, StreamingResponse

from . import accounts, db, model_caps, opencode as opencode_mod
from .config import settings
from .proxy import merge_usage, scrub_secrets, usage_from_obj
from .security import client_ip, limiter
from .sources import (
    POOL_TYPES,
    catalog_unavailable_for,
    metadata_effort,
    models_union,
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
from .sources import explabs as explabs_mod
from .sources import devin as devin_mod
from .sources.pool_session import session_id_from

router = APIRouter()
_LINE_CAP = 1_048_576
_CLOSE_SECONDS = 5
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
    return ""


async def http_errors(request: Request, exc):
    """App-wide HTTPException handler (registered in main). OpenAI SDKs read
    error.message; Fuse's own /v1 rejections carry it beside FastAPI's detail,
    like the upstream errors /v1 relays."""
    if request.url.path.startswith("/v1/") and isinstance(exc.detail, str):
        kind = "invalid_request_error" if exc.status_code < 500 else "api_error"
        return JSONResponse(
            {"detail": exc.detail, "error": {"message": exc.detail, "type": kind, "code": exc.status_code}},
            status_code=exc.status_code,
            headers=getattr(exc, "headers", None),
        )
    return await http_exception_handler(request, exc)


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
            return scrub_secrets(err.strip())[:400]
        if isinstance(err, dict):
            msg = err.get("message") or err.get("msg") or ""
            if isinstance(msg, str) and msg.strip():
                return scrub_secrets(msg.strip())[:400]
        for key in ("message", "msg", "detail"):
            val = body.get(key)
            if isinstance(val, str) and val.strip():
                return scrub_secrets(val.strip())[:400]
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
        if not isinstance(message.get("role"), str) or message["role"] not in roles:
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
                if part.get("type") in ("image_url", "input_image"):
                    url = image.get("url") if isinstance(image, dict) else image
                    if not isinstance(url, str) or not url:
                        raise HTTPException(400, "Image parts must include image_url")
                    if url.startswith("data:"):
                        header, sep, data = url.partition(",")
                        if not sep or not header.endswith(";base64") or not data:
                            raise HTTPException(400, "Images must use a base64 data URI")
                        if header[5:].split(";", 1)[0] not in {"image/png", "image/jpeg", "image/gif", "image/webp"}:
                            raise HTTPException(400, "Unsupported image MIME type")
                        try:
                            base64.b64decode(data, validate=True)
                        except (ValueError, binascii.Error):
                            raise HTTPException(400, "Invalid base64 image data") from None
                    elif not url.startswith(("https://", "http://")):
                        raise HTTPException(400, "Images must use an HTTP(S) URL or base64 data URI")
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


def _ensure_encodable(body: dict) -> None:
    """httpx encodes with allow_nan=False and strict UTF-8. A body it cannot
    encode fails identically on every account, so reject it up front rather
    than letting pools blame (and cool down) each credential in turn."""
    try:
        json.dumps(body, ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, RecursionError):
        raise HTTPException(400, "Request body contains values that cannot be encoded as JSON") from None


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
    if src.type_id in ("openrouter", "explabs"):
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
    saw_error = False
    request_status = "success"

    def inspect(line: bytes) -> bytes:
        """Account for one SSE line and return it as it may reach the client."""
        nonlocal usage, saw_done, saw_error, request_status
        text = line.decode(errors="replace").strip()
        if not text.startswith("data:"):
            return line
        data = text[5:].strip()
        if not data:
            return line
        if data == "[DONE]":
            saw_done = True
            return line
        try:
            obj = json.loads(data)
        except json.JSONDecodeError:
            request_status = "failed"
            return line
        if not isinstance(obj, dict):
            request_status = "failed"
            return line
        # Only frames that carry a usage dict update the counts; a later
        # delta frame must not zero what an earlier usage frame reported.
        if isinstance(obj.get("usage"), dict) and obj["usage"]:
            usage = usage_from_obj(obj, reasoning_separate=(provider == "grok"))
        if obj.get("error"):
            request_status = "failed"
            saw_error = True
            # Inline errors get the same scrub as non-stream error bodies.
            return scrub_secrets(line.decode(errors="replace")).encode()
        return line

    try:
        async for chunk in stream:
            buf += chunk
            if len(buf) > _LINE_CAP:
                out, buf = buf, b""
                yield out
                continue
            out = b""
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                out += inspect(line) + b"\n"
            if out:
                yield out
        if buf:
            yield inspect(buf)
            buf = b""
        if not saw_done:
            # Upstream closed cleanly but early: without a terminal frame the
            # client would read a cut-off answer as a normal end.
            if not saw_error:
                error = {
                    "error": {
                        "message": "upstream stream ended before completion",
                        "type": "api_error",
                        "code": 502,
                    }
                }
                yield f"data: {json.dumps(error, separators=(',', ':'))}\n\n".encode()
            yield b"data: [DONE]\n\n"
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
        if not usage:
            # No usage frame anywhere: record the explicit unknown/zero shape.
            usage = usage_from_obj({})
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
                # After a client disconnect the enclosing anyio scope is
                # cancelled and every further await would be re-cancelled,
                # skipping the upstream close. Shield it, bounded.
                with anyio.move_on_after(_CLOSE_SECONDS, shield=True):
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
    image = {e["id"]: e["image"] for e in await opencode_mod.model_options(user["id"])}
    data = []
    for n in names:
        row = {"id": n, "object": "model", "owned_by": "fuse"}
        if image.get(n) is not None:
            row["input_modalities"] = ["text", "image"] if image[n] else ["text"]
        data.append(row)
    return {"object": "list", "data": data}


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


async def invoke_chat(request: Request, user, body: dict, *, cheap_reasoning: bool = False,
                      image_snapshot: dict | None = None, prepared=None):
    """Pick a source and run one completion.

    Returns (user, src, upstream, requested_model, result). Failed pool
    attempts are recorded. The caller records successful usage.
    cheap_reasoning: replace any reasoning_effort with the cheapest one the
    resolved provider accepts (title/recap calls).
    image_snapshot/prepared are server-only replay state and its durable
    checkpoint hook, run after admission and before dispatch.
    """
    try:
        user = dict(user)
        return await _invoke_chat(request, user, body, cheap_reasoning=cheap_reasoning,
                                  image_snapshot=image_snapshot, prepared=prepared)
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


def _set_cheap_reasoning(body: dict, type_id: str, model: str, user_id: int) -> None:
    body.pop("reasoning_effort", None)
    effort = metadata_effort(type_id, model, user_id)
    if effort:
        body["reasoning_effort"] = effort


async def _invoke_chat(request: Request, user, body: dict, *, cheap_reasoning: bool = False,
                       image_snapshot: dict | None = None, prepared=None):
    user = dict(user)
    if not isinstance(body, dict):
        raise HTTPException(400, "Invalid JSON")
    model = body.get("model") or ""
    if not isinstance(model, str) or len(model) > 200:
        raise HTTPException(400, "Invalid model")
    _validate_messages(body.get("messages"), retained=bool(request.scope.get("fuse_retained_history")))
    body = dict(body)
    body.pop("prompt_snapshot", None)
    body.pop("image_snapshot", None)
    if "reasoning_effort" not in body and "reasoningEffort" in body:
        body["reasoning_effort"] = body.pop("reasoningEffort")
    _validate_reasoning(body)
    if "stream" in body and not isinstance(body["stream"], bool):
        raise HTTPException(400, "Invalid stream")
    # Both spellings reach upstreams; each gets the same ceiling.
    for field in ("max_tokens", "max_completion_tokens"):
        if body.get(field) is None:
            continue
        try:
            value = body[field]
            if isinstance(value, bool):
                raise ValueError
            number = float(value)
            if not math.isfinite(number) or not number.is_integer():
                raise ValueError
            body[field] = max(1, min(int(number), settings.max_tokens))
        except (TypeError, ValueError, OverflowError):
            raise HTTPException(400, f"Invalid {field}")
    grok_ok = user_has_grok(user["id"])
    chatgpt_ok = user_has_chatgpt(user["id"])
    zai_ok = user_has_zai(user["id"])
    devin_ok = user_has_devin(user["id"])
    kimi_ok = user_has_kimi(user["id"])
    mimo_ok = user_has_mimo(user["id"])
    claude_ok = user_has_claude(user["id"])
    if model.startswith("explabs/") and not explabs_mod.user_has_explabs(user["id"]):
        raise HTTPException(403, "Connect an Experiential Labs API key to use its models")
    try:
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
    except explabs_mod.CatalogError:
        raise HTTPException(503, "Experiential Labs model catalog is temporarily unavailable") from None
    if not picked:
        if model.startswith("explabs/") and not explabs_mod.user_has_explabs(user["id"]):
            raise HTTPException(403, "Reconnect your Experiential Labs API key to use its models")
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
    _ensure_encodable(body)
    requested = model
    if cheap_reasoning:
        _set_cheap_reasoning(body, src.type_id, model, user["id"])
    body["model"] = upstream
    original = body["messages"]
    images = image_snapshot if image_snapshot is not None else {}
    body["messages"], omitted = model_caps.prepare(original, src.type_id, upstream,
                                                   user_id=user["id"], snapshot=images)
    if prepared is not None:
        await prepared()
    session_id = None
    if src.type_id in POOL_TYPES or src.type_id in ("openai", "ollama", "openrouter"):
        session_id = session_id_from(request.headers, body, user_id=user["id"], model=upstream)
    shed_total = 0
    while True:
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
        if result.status == 200 or not model_caps.image_refusal(result.status, _error_message(result.body)):
            break
        # The provider refused this many images (Fuse sets no cap): drop the
        # older half, freeze that in the snapshot, and retry until it fits.
        shed = model_caps.shed(images, src.type_id, upstream)
        if not shed:
            break
        shed_total += shed
        logger.warning("Provider refused images provider=%s model=%s status=%s; dropped %s older image(s)",
                       src.type_id, upstream, result.status, shed)
        _safe_record_usage(user["id"], src.source_id, result.credential_id, upstream, result.usage,
                           key_id=user.get("key_id"), provider=src.type_id, requested_model=requested,
                           request_status="failed", count_request=False)
        body["messages"], omitted = model_caps.prepare(original, src.type_id, upstream,
                                                       user_id=user["id"], snapshot=images)
        if prepared is not None:
            await prepared()
    headers = {}
    if omitted:
        headers["X-Fuse-Images-Omitted"] = str(omitted)
    if shed_total:
        headers["X-Fuse-Images-Shed"] = str(shed_total)
    if headers:
        result.response_headers = {**result.response_headers, **headers}
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
    omitted = {k: v for k, v in result.response_headers.items() if k in ("X-Fuse-Images-Omitted", "X-Fuse-Images-Shed")}
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
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", **omitted},
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
    return JSONResponse(result.body or {}, headers=omitted)


@router.post("/v1/chat/completions")
async def chat_completions(request: Request):
    from .web.deps import _json_object

    user = require_key(request)
    try:
        # Same strict parser as the dashboard: invalid UTF-8, lone surrogates,
        # NaN/Infinity and pathological nesting are client errors, not 500s or
        # upstream encode failures blamed on a pool account.
        body = await _json_object(request)
    except HTTPException as exc:
        logger.warning(
            "Rejected chat request user=%s status=%s detail=%s", user["id"], exc.status_code, exc.detail
        )
        raise
    return await dispatch_chat(request, user, body)
