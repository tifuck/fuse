"""Claude request shaping: OpenAI chat bodies → Anthropic Messages payloads.

Subscription OAuth is only accepted for a Claude Code shaped request, so the
payload carries the CLI identity line, signed thinking blocks replay verbatim,
and prompt-cache breakpoints ride on the newest eligible block each turn.
"""
from __future__ import annotations

import hashlib
import json
import re

from .base import map_effort
from .claude_catalog import _meta_for, default_effort, efforts_for

_THINKING_OFF = frozenset({"none", "off", "disabled"})
# Per-model rules the catalog does not carry (platform.claude.com thinking
# page): these reject forced tool use on every request; these can never turn
# thinking off; Sonnet 5.5's lowest setting is between_tools, not disabled.
_NO_FORCED_TOOL = frozenset({"claude-opus-5-5", "claude-sonnet-5-5", "claude-fable-5-2", "claude-mythos-5-2", "claude-fable-5-1", "claude-mythos-5-1"})
_ALWAYS_THINKS = frozenset({"claude-opus-5-5", "claude-fable-5-2", "claude-mythos-5-2", "claude-fable-5-1", "claude-mythos-5-1", "claude-fable-5", "claude-mythos-5", "claude-mythos-preview"})
_BETWEEN_TOOLS = frozenset({"claude-sonnet-5-5"})
# Mid-conversation role:"system" messages are accepted only on these.
_MID_SYSTEM = frozenset({
    "claude-fable-5-2", "claude-mythos-5-2", "claude-fable-5-1", "claude-mythos-5-1", "claude-fable-5", "claude-mythos-5", "claude-mythos-preview",
    "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5", "claude-haiku-5-5", "claude-opus-4-8",
})
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
            if kind in ("text", "input_text", "output_text"):
                text = str(part.get("text") or "")
                if text:
                    blocks.append({"type": "text", "text": text})
            elif kind == "image_url":
                image = _image_block(part)
                if image:
                    blocks.append(image)
    return blocks


def _thinking_block(block) -> dict | None:
    if not isinstance(block, dict):
        return None
    if block.get("redacted_thinking"):
        return {"type": "redacted_thinking", "data": str(block["redacted_thinking"])}
    text = str(block.get("thinking") or "")
    signature = str(block.get("signature") or "")
    # A signed block with empty text is a valid omitted-display block;
    # unsigned text can never be replayed.
    if signature:
        return {"type": "thinking", "thinking": text, "signature": signature}
    return None


def _thinking_blocks(msg: dict) -> list[dict]:
    raw = msg.get("claude_thinking")
    if not isinstance(raw, list):
        return []
    return [out for out in map(_thinking_block, raw) if out]


_TOOL_ID = re.compile(r"[A-Za-z0-9_-]+")


def _tool_id(raw) -> str:
    """An id Anthropic accepts (^[a-zA-Z0-9_-]+$) for a call from any model.

    History from other backends carries ids like Kimi's `functions.grep:0` or
    Devin's `call_…#…`; sent as-is after a model switch, Anthropic refuses the
    whole request. The mapping is a pure function of the id, so the call and
    its result stay paired and every later request replays the same bytes.
    """
    ident = str(raw or "")
    if not ident or (len(ident) <= 128 and _TOOL_ID.fullmatch(ident)):
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
    elif choice is None and parallel is False:
        # Omitted tool_choice is auto; the parallel limit still applies.
        mapped = {"type": "auto"}
    if mapped is None:
        return None
    if parallel is False and mapped["type"] in ("auto", "any"):
        mapped["disable_parallel_tool_use"] = True
    return mapped


def _assistant_blocks(msg: dict) -> list[dict]:
    """The assistant turn as Anthropic sent it.

    Preserved thinking needs the blocks back in received order (5.x models
    put a signed progress-update block right before each tool_use). Each
    stored thinking block records how many tool calls preceded it and whether
    text did; rows without that keep the old thinking/text/tools layout.
    """
    stored = msg.get("claude_thinking") if isinstance(msg.get("claude_thinking"), list) else []
    raw = [block for block in stored if _thinking_block(block)]
    text = _text_of(msg.get("content"))
    text_block = [{"type": "text", "text": text}] if text else []
    tools = [block for block in map(_tool_use_block, msg.get("tool_calls") or []) if block]
    if not raw or not all(isinstance(block.get("tool_index"), int) for block in raw):
        return [*map(_thinking_block, raw), *text_block, *tools]
    # ponytail: text is one merged string, so it goes back as one block at
    # its first position (before that gap's tools); text split around
    # thinking or after a tool_use in the same gap is not reproduced.
    out: list[dict] = []
    used = 0
    for block in raw:
        if block.get("after_text") and text_block:
            out.extend(text_block)
            text_block = []
        out.extend(tools[used:block["tool_index"]])
        used = max(used, block["tool_index"])
        out.append(_thinking_block(block))
    return [*out, *text_block, *tools[used:]]


def _raw_effort(body: dict) -> str:
    effort = body.get("reasoning_effort")
    if effort is None and isinstance(body.get("reasoning"), dict):
        effort = body["reasoning"].get("effort")
    return effort.strip().lower() if isinstance(effort, str) else ""


def _requested_effort(key: str, model: str) -> str | None:
    """output_config.effort for the request, or None to send none."""
    allowed = efforts_for(model)
    if not allowed:
        # Haiku 4.5 and older reject the effort parameter.
        return None
    if not key:
        return default_effort(model) or None
    if key in _THINKING_OFF:
        # Thinking off still takes an effort; the lowest one is accepted
        # wherever thinking can be turned off.
        return allowed[0]
    return map_effort(key, allowed) or allowed[0]


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

    rows = [msg for msg in body.get("messages") or [] if isinstance(msg, dict)]
    for at, msg in enumerate(rows):
        role = msg.get("role")
        if role in ("system", "developer"):
            flush_results()
            text = _text_of(msg.get("content"))
            if not text:
                continue
            # Left where the caller put it, a later instruction keeps the
            # cached prefix and earlier thinking valid. Anthropic takes it only
            # right after a user turn (tool results count) and before an
            # assistant turn or the end; anywhere else it joins the top-level
            # system prompt as before.
            following = next((row.get("role") for row in rows[at + 1:] if row.get("role") not in ("system", "developer")), None)
            if (model in _MID_SYSTEM and messages and messages[-1]["role"] in ("user", "system")
                    and following in (None, "assistant")):
                messages.append({"role": "system", "content": text})
            else:
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
            blocks = _assistant_blocks(msg)
            if blocks:
                messages.append({"role": "assistant", "content": blocks})
            continue
        if role == "tool":
            call_id = str(msg.get("tool_call_id") or "").strip()
            if not call_id:
                continue
            content = msg.get("content")
            pending_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": _tool_id(call_id),
                    "content": _user_blocks(content) if isinstance(content, list) else _text_of(content),
                }
            )
    flush_results()
    if not messages:
        messages = [{"role": "user", "content": [{"type": "text", "text": ""}]}]

    # Anthropic requires max_tokens: default to the model's own output
    # ceiling from the catalog, so Fuse never cuts a reply short itself.
    cap = int(_meta_for(model).get("max_output") or 32_000)
    try:
        max_tokens = int(body.get("max_tokens") or cap)
    except (TypeError, ValueError):
        max_tokens = cap
    max_tokens = max(1, min(max_tokens, cap))

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
                # Without it Anthropic holds each argument until it is
                # complete: a large write_file body was minutes of silence
                # on the stream. Claude Code sends it too. Arguments are no
                # longer validated upstream; calls_incomplete catches
                # invalid JSON.
                "eager_input_streaming": True,
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
    # row that started this turn). Anthropic looks back only 20 positions
    # from a breakpoint. A run of tool_use or tool_result blocks counts as
    # one, but 5.x models put a thinking block before each call, so one
    # parallel round can still push the previous entry out of reach; reading
    # it here keeps the conversation cached instead of re-writing it. It is
    # already cached, so marking it costs a read, never a second write.
    last_assistant = next((i for i in range(len(messages) - 1, -1, -1) if messages[i].get("role") == "assistant"), None)
    if newest is not None and last_assistant:
        _mark_newest_eligible(messages[:last_assistant], skip=newest)
    key = _raw_effort(body)
    effort = _requested_effort(key, model)
    if effort:
        payload["output_config"] = {"effort": effort}
    parallel = body.get("parallel_tool_calls")
    choice = _tool_choice(body.get("tool_choice"), parallel)
    forced = bool(choice) and choice["type"] in ("any", "tool")
    if forced and model in _NO_FORCED_TOOL:
        choice, forced = _tool_choice("auto", parallel), False
    thinking_type = str(_meta_for(model).get("thinking_type") or "")
    if thinking_type in ("effort", "adaptive"):
        # The catalog's "effort" models are adaptive-thinking models. 5.x
        # models think by default but omit the text, so a long think looked
        # like a dead stream; Opus 4.6-4.8 and Sonnet 4.6 do not think at
        # all without this field. Summaries stream as they go (Claude Code's
        # showThinkingSummaries sends the same field).
        if key in _THINKING_OFF and model not in _ALWAYS_THINKS:
            payload["thinking"] = {"type": "between_tools" if model in _BETWEEN_TOOLS else "disabled"}
        else:
            payload["thinking"] = {"type": "adaptive", "display": "summarized"}
    elif thinking_type == "enabled" and key not in _THINKING_OFF:
        # Thinking must fit under max_tokens; below the 1024 floor there
        # is no room, and clamping the budget up would make Anthropic
        # reject the whole request.
        budget = min(max_tokens - 1, 8192)
        # Manual thinking rejects a forced tool choice: an explicit thinking
        # request demotes the choice, otherwise the default thinking yields.
        if budget >= 1024 and (key or not forced):
            payload["thinking"] = {"type": "enabled", "budget_tokens": budget}
            if forced:
                choice = _tool_choice("auto", parallel)
    if choice and tools:
        payload["tool_choice"] = choice
    # Models that take effort reject non-default sampling (4.7 and later,
    # 5.x on every request; 4.6 while thinking).
    if not efforts_for(model):
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


def _chat_headers(token: str, beta: str = _BETA) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
        "anthropic-version": _ANTHROPIC_VERSION,
        "anthropic-beta": beta,
    }
