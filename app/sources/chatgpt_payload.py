"""ChatGPT request shaping: OpenAI chat bodies → Codex `/responses` payloads.

Codex rejects Chat Completions sampling and cache-lifetime fields, so the
payload carries only `input` items, tools (`strict: false` like Codex sends),
`prompt_cache_key` (the scoped session id, never a raw client string) and
`reasoning.effort`.
"""
from __future__ import annotations

import json

from .base import map_effort
from .chatgpt_catalog import base_slug, catalog_efforts, is_fast


def _text_of(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for p in content:
            if isinstance(p, dict) and p.get("type") == "text":
                parts.append(str(p.get("text") or ""))
        return "\n".join(x for x in parts if x)
    return ""


def _input_content(content) -> list:
    if isinstance(content, str):
        return [{"type": "input_text", "text": content}]
    out = []
    if isinstance(content, list):
        for p in content:
            if not isinstance(p, dict):
                continue
            kind = p.get("type")
            if kind == "text":
                out.append({"type": "input_text", "text": str(p.get("text") or "")})
            elif kind == "image_url":
                url_obj = p.get("image_url")
                url = url_obj.get("url") if isinstance(url_obj, dict) else url_obj
                if url:
                    image = {"type": "input_image", "image_url": str(url)}
                    if isinstance(url_obj, dict) and isinstance(url_obj.get("detail"), str):
                        image["detail"] = url_obj["detail"]
                    out.append(image)
            elif kind in ("file", "input_file"):
                file = p.get("file")
                file = file if isinstance(file, dict) else p
                item = {"type": "input_file"}
                for key in ("file_id", "file_data", "filename"):
                    if isinstance(file.get(key), str) and file[key]:
                        item[key] = file[key]
                if "file_id" in item or "file_data" in item:
                    out.append(item)
    return out or [{"type": "input_text", "text": ""}]


def build_responses_payload(body: dict, model: str, session_id: str | None = None) -> dict:
    instructions: list[str] = []
    input_items: list[dict] = []
    for msg in body.get("messages") or []:
        if not isinstance(msg, dict):
            continue
        role = msg.get("role")
        content = msg.get("content")
        if role in ("system", "developer"):
            text = _text_of(content)
            if text:
                instructions.append(text)
            continue
        if role == "user":
            input_items.append({"role": "user", "content": _input_content(content)})
        elif role == "assistant":
            # Reasoning comes first, as the model emitted it. Each item keeps
            # its producing account; ChatGPTSource drops it for any other.
            for block in msg.get("claude_thinking") or []:
                item = block.get("openai_reasoning") if isinstance(block, dict) else None
                if isinstance(item, dict) and item.get("encrypted_content"):
                    input_items.append({
                        "type": "reasoning",
                        "summary": list(item.get("summary") or []),
                        "encrypted_content": str(item["encrypted_content"]),
                        "_fuse_account": item.get("account"),
                    })
            text = _text_of(content)
            if text:
                input_items.append(
                    {"role": "assistant", "content": [{"type": "output_text", "text": text}]}
                )
            for tc in msg.get("tool_calls") or []:
                if not isinstance(tc, dict):
                    continue
                fn = tc.get("function") if isinstance(tc.get("function"), dict) else {}
                arguments = fn.get("arguments")
                if not isinstance(arguments, str):
                    arguments = json.dumps(arguments if arguments is not None else {})
                input_items.append(
                    {
                        "type": "function_call",
                        "call_id": str(tc.get("id") or fn.get("name") or ""),
                        "name": str(fn.get("name") or ""),
                        "arguments": arguments or "{}",
                    }
                )
        elif role == "tool":
            input_items.append(
                {
                    "type": "function_call_output",
                    "call_id": str(msg.get("tool_call_id") or ""),
                    "output": _text_of(content),
                }
            )
    payload: dict = {
        "model": base_slug(model),
        "input": input_items,
        "stream": True,
        "store": False,
        # Without stored state, the reasoning between tool calls survives only
        # as encrypted items sent back on the next request (as Codex does).
        "include": ["reasoning.encrypted_content"],
    }
    if is_fast(model):
        payload["service_tier"] = "fast"
    # Always the scoped session id (hmac of user+model+client key), never the
    # raw prompt_cache_key. Two tenants sending the same client string must
    # not share an upstream routing group on a pooled account.
    if session_id:
        payload["prompt_cache_key"] = str(session_id)[:256]
    if instructions:
        payload["instructions"] = "\n\n".join(instructions)
    tools = body.get("tools")
    if isinstance(tools, list) and tools:
        mapped = []
        for t in tools:
            if not isinstance(t, dict) or t.get("type") != "function":
                continue
            fn = t.get("function") if isinstance(t.get("function"), dict) else t
            if not fn.get("name"):
                continue
            # Responses function tools default to strict, which makes every
            # property required: the model then fills optional arguments with
            # placeholders (both edits and old_string, include and glob, a
            # dummy edit). Codex itself sends strict=false.
            mapped.append(
                {
                    "type": "function",
                    "name": str(fn.get("name")),
                    "description": str(fn.get("description") or ""),
                    "parameters": fn.get("parameters") or {"type": "object", "properties": {}},
                    "strict": False,
                }
            )
        if mapped:
            payload["tools"] = mapped
    choice = body.get("tool_choice")
    if isinstance(choice, str) and choice in ("none", "auto", "required"):
        payload["tool_choice"] = choice
    elif isinstance(choice, dict) and choice.get("type") == "function":
        fn = choice.get("function") if isinstance(choice.get("function"), dict) else {}
        if fn.get("name"):
            payload["tool_choice"] = {"type": "function", "name": str(fn["name"])}
    if body.get("parallel_tool_calls") is not None:
        payload["parallel_tool_calls"] = bool(body.get("parallel_tool_calls"))
    # Codex /responses now rejects Chat Completions sampling and cache-lifetime
    # fields (400 Unsupported parameter: max_output_tokens / temperature / top_p
    # / prompt_cache_retention / prompt_cache_options), including on GPT-5.6.
    # prompt_cache_key and reasoning.effort still work.
    reasoning = body.get("reasoning")
    if isinstance(reasoning, dict):
        payload["reasoning"] = dict(reasoning)
    effort = body.get("reasoning_effort")
    levels = catalog_efforts(model)
    if isinstance(effort, str) and levels is not None:
        # Only levels the model lists (Codex answered none with a 400): a chat
        # saved at Off, a wake reusing it or an API client gets the nearest
        # level, and off gets no effort at all (the model's default).
        effort = map_effort(effort, levels)
    if isinstance(effort, str):
        payload.setdefault("reasoning", {})["effort"] = effort
        payload["reasoning"].setdefault("summary", "auto")
    summary = body.get("reasoning_summary")
    if summary is None:
        summary = body.get("reasoningSummary")
    if isinstance(summary, str):
        payload.setdefault("reasoning", {})["summary"] = summary
    return payload


def _same_account(raw, account) -> bool:
    # raw is client-replayed history: anything but a matching id is dropped.
    if raw is None or account is None:
        return False
    try:
        return int(raw) == int(account)
    except (TypeError, ValueError, OverflowError):
        return False


def for_account(payload: dict, account) -> dict:
    """The payload one pooled account may receive.

    Encrypted reasoning decrypts only for the account that produced it, so
    other accounts get those items dropped (their cache is cold anyway).
    """
    items = []
    for item in payload.get("input") or []:
        if isinstance(item, dict) and item.get("type") == "reasoning":
            if not _same_account(item.get("_fuse_account"), account):
                continue
            item = {k: v for k, v in item.items() if k != "_fuse_account"}
        items.append(item)
    return {**payload, "input": items}


def drop_reasoning(payload: dict) -> bool:
    """Remove replayed reasoning items; True when any were present."""
    items = payload.get("input") or []
    kept = [item for item in items if not (isinstance(item, dict) and item.get("type") == "reasoning")]
    payload["input"] = kept
    return len(kept) != len(items)
