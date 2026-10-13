"""Claude request shaping: OpenAI chat bodies → Anthropic Messages payloads.

Subscription OAuth is only accepted for a Claude Code shaped request, so the
payload carries the CLI identity line, signed thinking blocks replay verbatim,
and prompt-cache breakpoints ride on the newest eligible block each turn.
"""
from __future__ import annotations

import hashlib
import json
import re

from ..config import settings
from .claude_catalog import _meta_for, efforts_for

_THINKING_OFF = frozenset({"none", "off", "disabled"})
_EFFORTS = ("low", "medium", "high", "xhigh", "max")
# Claude Code's first-party allowlist for current models is claude-code plus
# oauth-2025-04-20 when the token carries user:inference. The old adaptive
# thinking beta is not on that list and these models reject it.
_BETA = "claude-code-20250219,oauth-2025-04-20"
_ANTHROPIC_VERSION = "2023-06-01"


def _text_of(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, dict) and part.get("type") in ("text", "input_text", "output_text"):
                parts.append(str(part.get("text") or ""))
            elif isinstance(part, str):
                parts.append(part)
        return "\n".join(p for p in parts if p)
    return ""


def _image_block(part: dict) -> dict | None:
    url_obj = part.get("image_url")
    url = url_obj.get("url") if isinstance(url_obj, dict) else url_obj
    if not isinstance(url, str) or not url:
        return None
    if url.startswith("data:"):
        header, _, data = url.partition(",")
        if not header.endswith(";base64") or not data:
            return None
        media = header[5:].split(";", 1)[0] or "image/png"
        if not media.startswith("image/"):
            return None
        return {"type": "image", "source": {"type": "base64", "media_type": media, "data": data}}
    if url.startswith("https://") or url.startswith("http://"):
        return {"type": "image", "source": {"type": "url", "url": url}}
    return None


def _user_blocks(content) -> list[dict]:
    if isinstance(content, str):
        return [{"type": "text", "text": content}] if content else []
    blocks = []
    if isinstance(content, list):
        for part in content:
            if isinstance(part, str):
                if part:
                    blocks.append({"type": "text", "text": part})
                continue
            if not isinstance(part, dict):
                continue
            kind = part.get("type")
            if kind in ("text", "input_text"):
                text = str(part.get("text") or "")
                if text:
                    blocks.append({"type": "text", "text": text})
            elif kind == "image_url":
                image = _image_block(part)
                if image:
                    blocks.append(image)
    return blocks


def _thinking_blocks(msg: dict) -> list[dict]:
    raw = msg.get("claude_thinking")
    if not isinstance(raw, list):
        return []
    out = []
    for block in raw:
        if not isinstance(block, dict):
            continue
        if block.get("redacted_thinking"):
            out.append({"type": "redacted_thinking", "data": str(block["redacted_thinking"])})
            continue
        text = str(block.get("thinking") or "")
        signature = str(block.get("signature") or "")
        # A signed block with empty text is a valid omitted-display block;
        # unsigned text can never be replayed.
        if signature:
            out.append({"type": "thinking", "thinking": text, "signature": signature})
    return out


_TOOL_ID = re.compile(r"[A-Za-z0-9_-]+")


def _tool_id(raw) -> str:
    """An id Anthropic accepts (^[a-zA-Z0-9_-]+$) for a call from any model.

    History from other backends carries ids like Kimi's `functions.grep:0` or
    Devin's `call_…#…`; sent as-is after a model switch, Anthropic refuses the
    whole request. The mapping is a pure function of the id, so the call and
    its result stay paired and every later request replays the same bytes.
    """
    ident = str(raw or "")[:128]
    if not ident or _TOOL_ID.fullmatch(ident):
        return ident
    return "toolu_" + hashlib.sha256(str(raw).encode()).hexdigest()[:24]


def _tool_use_block(call: dict) -> dict | None:
    if not isinstance(call, dict):
        return None
    fn = call.get("function") if isinstance(call.get("function"), dict) else {}
    name = str(fn.get("name") or "").strip()
    if not name:
        return None
    raw = fn.get("arguments")
    parsed: dict
    if isinstance(raw, dict):
        parsed = raw
    else:
        try:
            loaded = json.loads(raw or "{}")
            parsed = loaded if isinstance(loaded, dict) else {"input": loaded}
        except (json.JSONDecodeError, TypeError, ValueError):
            parsed = {}
    return {
        "type": "tool_use",
        "id": _tool_id(call.get("id")) or f"toolu_{name[:24]}",
        "name": name[:64],
        "input": parsed,
    }


def _tool_choice(choice, parallel) -> dict | None:
    mapped = None
    if isinstance(choice, str) and choice in ("auto", "none", "any"):
        mapped = {"type": choice}
    elif choice == "required":
        mapped = {"type": "any"}
    elif isinstance(choice, dict) and choice.get("type") == "function":
        fn = choice.get("function") if isinstance(choice.get("function"), dict) else {}
        if fn.get("name"):
            mapped = {"type": "tool", "name": str(fn["name"])[:64]}
    if mapped is None:
        return None
    if parallel is False and mapped["type"] in ("auto", "any"):
        mapped["disable_parallel_tool_use"] = True
    return mapped


def _requested_effort(body: dict, model: str) -> str | None:
    effort = body.get("reasoning_effort")
    if effort is None and isinstance(body.get("reasoning"), dict):
        effort = body["reasoning"].get("effort")
    if not isinstance(effort, str) or not effort.strip():
        # The CLI's non-interactive default for Opus 5.5 is low, and that is
        # the effort a subscription token accepts. The catalog default (medium)
        # is for the interactive picker, not this request.
        effort = "low"
    key = str(effort or "").strip().lower()
    if not key or key in _THINKING_OFF:
        return None
    allowed = efforts_for(model) or _EFFORTS
    if key not in allowed and key not in _EFFORTS:
        return allowed[0] if allowed else "high"
    if key not in allowed:
        return allowed[0] if allowed else key
    return key


def _mark_cache_breakpoint(block: dict) -> None:
    block["cache_control"] = {"type": "ephemeral", "ttl": "1h"}


def _mark_newest_eligible(messages: list[dict], skip=None):
    """Mark the newest block Anthropic accepts as a breakpoint (never a
    thinking block, never empty text); returns it, or None."""
    for block in reversed(messages):
        content = block.get("content")
        if not (isinstance(content, list) and content):
            continue
        for part in reversed(content):
            if not isinstance(part, dict):
                continue
            kind = str(part.get("type") or "")
            if kind in ("thinking", "redacted_thinking"):
                continue
            if kind == "text" and not str(part.get("text") or "").strip():
                continue
            if part is skip:
                return None
            _mark_cache_breakpoint(part)
            return part
    return None


def build_messages_payload(body: dict, model: str) -> dict:
    system: list[dict] = []
    messages: list[dict] = []
    pending_results: list[dict] = []

    def flush_results():
        if pending_results:
            messages.append({"role": "user", "content": list(pending_results)})
            pending_results.clear()

    for msg in body.get("messages") or []:
        if not isinstance(msg, dict):
            continue
        role = msg.get("role")
        if role in ("system", "developer"):
            flush_results()
            text = _text_of(msg.get("content"))
            if text:
                system.append({"type": "text", "text": text})
            continue
        if role == "user":
            flush_results()
            blocks = _user_blocks(msg.get("content"))
            if blocks:
                messages.append({"role": "user", "content": blocks})
            continue
        if role == "assistant":
            flush_results()
            blocks: list[dict] = []
            blocks.extend(_thinking_blocks(msg))
            text = _text_of(msg.get("content"))
            if not text and isinstance(msg.get("reasoning_content"), str) and not blocks:
                text = ""
            if text:
                blocks.append({"type": "text", "text": text})
            for call in msg.get("tool_calls") or []:
                block = _tool_use_block(call)
                if block:
                    blocks.append(block)
            if blocks:
                messages.append({"role": "assistant", "content": blocks})
            continue
        if role == "tool":
            call_id = str(msg.get("tool_call_id") or "").strip()
            if not call_id:
                continue
            pending_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": _tool_id(call_id),
                    "content": _text_of(msg.get("content")) or "",
                }
            )
    flush_results()
    if not messages:
        messages = [{"role": "user", "content": [{"type": "text", "text": ""}]}]

    try:
        max_tokens = int(body.get("max_tokens") or settings.max_tokens)
    except (TypeError, ValueError):
        max_tokens = int(settings.max_tokens)
    max_tokens = max(1, min(max_tokens, int(settings.max_tokens)))

    payload: dict = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": messages,
        "stream": True,
    }
    # Subscription OAuth is only accepted for a Claude Code shaped request.
    # A bare Messages call with the same token is refused as rate_limit_error
    # even while the CLI on that account succeeds.
    identity = {
        "type": "text",
        "text": "You are Claude Code, Anthropic's official CLI for Claude.",
    }
    payload["system"] = [identity, *system]
    tools = []
    for tool in body.get("tools") or []:
        if not isinstance(tool, dict) or tool.get("type") not in (None, "function"):
            continue
        fn = tool.get("function") if isinstance(tool.get("function"), dict) else tool
        name = str(fn.get("name") or "").strip()
        if not name:
            continue
        schema = fn.get("parameters") if isinstance(fn.get("parameters"), dict) else {"type": "object", "properties": {}}
        tools.append(
            {
                "name": name[:64],
                "description": str(fn.get("description") or "")[:4096],
                "input_schema": schema,
            }
        )
    if tools:
        payload["tools"] = tools
    # Up to four breakpoints: the system prefix (the identity line when the
    # client sent no system prompt), the tool schemas, the newest history
    # block, and the end of the previous request (below).
    # Breakpoint TTLs are allowed to differ per breakpoint (Anthropic only
    # requires longer TTLs to come first); Fuse uses 1h everywhere.
    _mark_cache_breakpoint(payload["system"][-1])
    if tools:
        _mark_cache_breakpoint(tools[-1])
    # The rolling marker sits on the newest block Anthropic accepts as a
    # breakpoint (never a thinking block, never empty text), including the
    # final tool results or the new user turn: each request then reads the
    # previous request's cache entry as a prefix instead of lagging one
    # round behind and re-billing the last turn as fresh input.
    newest = _mark_newest_eligible(messages)
    # Fourth breakpoint: where the previous request ended, i.e. the message
    # before the newest assistant turn (the last tool results, or the user
    # row that started this turn). Anthropic looks back only ~20 blocks from a
    # breakpoint, and one parallel tool round (thinking, text, 12 calls, 12
    # results) adds more than that; reading the previous entry here keeps the
    # conversation cached instead of re-writing it. It is already cached, so
    # marking it costs a read, never a second write.
    last_assistant = next((i for i in range(len(messages) - 1, -1, -1) if messages[i].get("role") == "assistant"), None)
    if newest is not None and last_assistant:
        _mark_newest_eligible(messages[:last_assistant], skip=newest)
    choice = _tool_choice(body.get("tool_choice"), body.get("parallel_tool_calls"))
    effort = _requested_effort(body, model)
    # A forced tool choice is illegal while thinking is on. A client that
    # forces a tool without asking for thinking keeps its forced call (the
    # default effort yields); only an explicit thinking request demotes the
    # choice, as Claude Code does.
    raw_effort = body.get("reasoning_effort")
    if raw_effort is None and isinstance(body.get("reasoning"), dict):
        raw_effort = body["reasoning"].get("effort")
    explicit_effort = (
        isinstance(raw_effort, str)
        and bool(raw_effort.strip())
        and raw_effort.strip().lower() not in _THINKING_OFF
    )
    if choice and choice.get("type") == "tool" and effort and not explicit_effort:
        effort = None
    if choice and choice.get("type") == "tool" and effort:
        choice = {"type": "auto"}
    if choice and tools:
        payload["tool_choice"] = choice
    # Current Claude Code models take effort in output_config. thinking.type
    # stays adaptive only when the published catalog says so; older models
    # reject a thinking block they did not ask for.
    meta = _meta_for(model)
    thinking_type = str(meta.get("thinking_type") or "")
    if effort:
        payload["output_config"] = {"effort": effort}
        if thinking_type == "adaptive":
            # Adaptive models default to omitted display: no thinking text
            # until the block ends, so a long think looks like a dead
            # stream. Summaries stream as it goes (Claude Code's
            # showThinkingSummaries sends the same field).
            payload["thinking"] = {"type": "adaptive", "display": "summarized"}
        elif thinking_type == "enabled":
            # Thinking must fit under max_tokens; below the 1024 floor there
            # is no room, and clamping the budget up would make Anthropic
            # reject the whole request.
            budget = min(max_tokens - 1, 8192)
            if budget >= 1024:
                payload["thinking"] = {"type": "enabled", "budget_tokens": budget}
        # "any" (OpenAI "required") is a forced choice too, illegal with
        # thinking on. Same rule as a named tool: an explicit thinking
        # request demotes the choice; otherwise the default thinking yields.
        forced_any = (payload.get("tool_choice") or {}).get("type") == "any"
        if forced_any and "thinking" in payload:
            if explicit_effort:
                parallel_off = payload["tool_choice"].get("disable_parallel_tool_use")
                payload["tool_choice"] = {"type": "auto", **({"disable_parallel_tool_use": True} if parallel_off else {})}
            else:
                payload.pop("thinking")
    else:
        if isinstance(body.get("temperature"), (int, float)) and not isinstance(body.get("temperature"), bool):
            payload["temperature"] = body["temperature"]
        if isinstance(body.get("top_p"), (int, float)) and not isinstance(body.get("top_p"), bool):
            payload["top_p"] = body["top_p"]
    stop = body.get("stop")
    if isinstance(stop, str) and stop:
        payload["stop_sequences"] = [stop[:64]]
    elif isinstance(stop, list):
        seqs = [str(item)[:64] for item in stop if isinstance(item, str) and item][:4]
        if seqs:
            payload["stop_sequences"] = seqs
    return payload


def _chat_headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
        "anthropic-version": _ANTHROPIC_VERSION,
        "anthropic-beta": _BETA,
    }
