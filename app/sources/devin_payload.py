"""Devin request shaping: OpenAI chat bodies → Cascade `GetChatMessage` payloads.

The metadata identifies Fuse as the Devin CLI (`chisel` agent gate), prompts
carry retained thinking/signatures, and tools map to Cascade tool defs. The
wire encoding itself lives in `devin_proto`.
"""
from __future__ import annotations

import json
import uuid

from ..config import settings
from . import devin_proto as proto

DEFAULT_STOP = ["\n\nUSER:", "\n\nASSISTANT:", "<|context_request|>", "<|end_of_turn|>"]


def _meta(token: str, user_jwt: str = "") -> dict:
    # Identify as Devin CLI / Local so Local-only GPT SKUs stay on this pool.
    # `agent_name` (metadata field 28) is required for newer Cascade SKUs —
    # see encode_metadata.
    return {
        "ide_name": settings.devin_ide_name or "devin",
        "ide_version": settings.devin_ide_version,
        "extension_name": settings.devin_extension_name or "cli",
        "extension_version": settings.devin_extension_version,
        "api_key": token,
        "locale": "en",
        "user_jwt": user_jwt,
        "agent_name": "chisel",
    }


def _catalog_meta(token: str) -> dict:
    """Windsurf identity — GetCliModelConfigs returns the Cascade catalog.

    CLI identity (`ide_name=devin`) only yields a stub SKU (GPT-4.1).
    """
    return {
        "ide_name": settings.devin_catalog_ide_name or "windsurf",
        "ide_version": settings.devin_catalog_ide_version,
        "extension_name": settings.devin_catalog_extension_name or "windsurf",
        "extension_version": settings.devin_catalog_extension_version,
        "api_key": token,
        "locale": "en",
    }


def _has_remote_image(messages) -> bool:
    for msg in messages or []:
        content = msg.get("content") if isinstance(msg, dict) else None
        for part in content if isinstance(content, list) else []:
            img = part.get("image_url") if isinstance(part, dict) else None
            url = img.get("url") if isinstance(img, dict) else img
            if isinstance(url, str) and not url.startswith("data:"):
                return True
    return False


def _cascade_id_for(session_id: str | None) -> str:
    if session_id:
        return str(uuid.uuid5(uuid.NAMESPACE_URL, f"devin-cascade:{session_id}"))
    return str(uuid.uuid4())


def _messages_to_prompts(messages: list, cascade_id: str) -> tuple[str, list[dict]]:
    system = []
    prompts = []
    index = 0
    for msg in messages or []:
        if not isinstance(msg, dict):
            continue
        role = str(msg.get("role") or "")
        content = msg.get("content")
        text = ""
        images = []
        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            parts = []
            for part in content:
                if not isinstance(part, dict):
                    continue
                if part.get("type") == "text":
                    parts.append(str(part.get("text") or ""))
                img = part.get("image_url")
                url = img.get("url") if isinstance(img, dict) else img
                if isinstance(url, str) and url.startswith("data:") and ";base64," in url:
                    header, _, b64 = url.partition(",")
                    mime = header[5:].split(";")[0] or "image/png"
                    images.append({"base64": b64, "mime": mime})
            text = "\n".join(parts)
        if role in ("system", "developer"):
            if text.strip():
                system.append(text)
            continue
        source = proto.CHAT_SOURCE_USER
        tool_calls = []
        tool_call_id = ""
        if role == "assistant":
            source = proto.CHAT_SOURCE_SYSTEM
            for call in msg.get("tool_calls") or []:
                if not isinstance(call, dict):
                    continue
                fn = call.get("function") if isinstance(call.get("function"), dict) else {}
                args = fn.get("arguments")
                if isinstance(args, dict):
                    args = json.dumps(args)
                tool_calls.append(
                    {
                        "id": str(call.get("id") or ""),
                        "name": str(fn.get("name") or ""),
                        "arguments_json": str(args or "{}"),
                        "is_custom_tool_call": True,
                    }
                )
        elif role == "tool":
            source = proto.CHAT_SOURCE_TOOL
            tool_call_id = str(msg.get("tool_call_id") or "")
        thinking = ""
        signature = str(msg.get("devin_signature") or "")
        signature_type = str(msg.get("devin_signature_type") or "")
        if isinstance(msg.get("reasoning_content"), str):
            thinking = msg["reasoning_content"]
        elif isinstance(msg.get("thinking"), str):
            thinking = msg["thinking"]
        elif isinstance(msg.get("claude_thinking"), list):
            # Retained turns store Devin's thinking as signed blocks, the same
            # shape Claude uses, so one transcript field serves both.
            blocks = [block for block in msg["claude_thinking"] if isinstance(block, dict)]
            thinking = "\n".join(str(block.get("thinking") or "") for block in blocks)
            signed = next((block for block in blocks if block.get("signature")), {})
            signature = signature or str(signed.get("signature") or "")
            signature_type = signature_type or str(signed.get("signature_type") or "")
        prompts.append(
            {
                "message_id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"{cascade_id}:{index}:{role}")),
                "source": source,
                "prompt": text,
                "tool_calls": tool_calls,
                "tool_call_id": tool_call_id,
                "images": images,
                "thinking": thinking,
                "signature": signature,
                "signature_type": signature_type,
            }
        )
        index += 1
    return "\n\n".join(system), prompts


def _tools_from_body(body: dict) -> list[dict]:
    out = []
    for tool in body.get("tools") or []:
        if not isinstance(tool, dict):
            continue
        fn = tool.get("function") if isinstance(tool.get("function"), dict) else tool
        name = fn.get("name")
        if not isinstance(name, str) or not name:
            continue
        params = fn.get("parameters") if isinstance(fn.get("parameters"), dict) else {"type": "object"}
        out.append(
            {
                "name": name,
                "description": str(fn.get("description") or ""),
                "json_schema": json.dumps(params),
                "strict": bool(fn.get("strict")),
            }
        )
    return out


def build_chat_request(
    body: dict,
    *,
    token: str,
    user_jwt: str,
    model_uid: str,
    cascade_id: str,
) -> dict:
    """Assemble the GetChatMessage request from an OpenAI chat body.

    `model_uid` is the resolved Cascade SKU (see devin_catalog.resolve_wire_uid).
    """
    system, prompts = _messages_to_prompts(body.get("messages") or [], cascade_id)
    temperature = body.get("temperature")
    try:
        temperature = 0.4 if temperature is None else float(temperature)
    except (TypeError, ValueError):
        temperature = 0.4
    if temperature == 0:
        temperature = 0.01
    # Dashboard chat does not send max_tokens. A silent 16k default was
    # Fuse's own per-round budget and cut long thinking streams mid-turn.
    # Only encode a cap when the client asked for one.
    max_tokens = body.get("max_tokens")
    if max_tokens is not None:
        try:
            max_tokens = max(1, min(int(max_tokens), 64000))
        except (TypeError, ValueError):
            max_tokens = None
    tools = _tools_from_body(body)
    parallel = body.get("parallel_tool_calls")
    disable_parallel = False if parallel is None else not bool(parallel)
    choice = body.get("tool_choice")
    option = "auto"
    tool_name = ""
    if isinstance(choice, str) and choice in ("none", "auto", "required"):
        option = "none" if choice == "none" else "auto"
    elif isinstance(choice, dict):
        fn = choice.get("function") if isinstance(choice.get("function"), dict) else {}
        tool_name = str(fn.get("name") or choice.get("name") or "")
        if tool_name:
            option = "function"
    return {
        "metadata": _meta(token, user_jwt),
        "prompt": system,
        "prompts": prompts,
        "model_uid": model_uid,
        "configuration": {
            "num_completions": 1,
            "max_tokens": max_tokens or 0,
            "max_newlines": 0,
            "temperature": temperature,
            "first_temperature": temperature,
            "top_k": 50,
            "top_p": float(body.get("top_p") or 1) or 1,
            "stop_patterns": [] if tools else list(DEFAULT_STOP),
            "fim_eot": 1,
        },
        "tools": tools,
        "disable_parallel_tool_calls": disable_parallel,
        "tool_choice": {"option_name": option, "tool_name": tool_name},
        "cascade_id": cascade_id,
        "execution_id": str(uuid.uuid4()),
    }
