"""Cursor agent wire protocol: one turn over RunSSE + BidiAppend.

This is cursor-agent's own HTTP/1.1 path (the CLI falls back to it whenever
HTTP/2 is unavailable): `agent.v1.AgentService/RunSSE` streams
AgentServerMessage frames as Connect JSON, and every client message goes up as
a unary `aiserver.v1.BidiService/BidiAppend` carrying a protobuf
AgentClientMessage. Field numbers are agent.proto as extracted from the
cursor-agent bundle.

Cursor's server writes the model prompt. History rides as JSON blobs the
server pulls back by sha256 id (root_prompt_messages_json), so a system
message becomes an opening exchange. The caller's tools are declared as MCP
tools and `x-cursor-agent-allowed-tools` hides Cursor's native ones. A tool
call ends the turn (finish_reason tool_calls): the caller runs the tool and
replays its result as history on the next request.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import re
import struct
import uuid
from typing import AsyncIterator

import httpx

from .devin_proto import CONNECT_END_STREAM, ProtoDecoder, ProtoEncoder, connect_frame, iter_connect_frames

SYSTEM_LEAD = "Configuration for this session, set by the operator of this API gateway:"
SYSTEM_ACK = "Understood. I will follow that configuration for the rest of this conversation."
# Sent as the user turn when a request ends on tool results: there is no
# server-side conversation to resume, the results are already in history.
CONTINUE_TEXT = "Continue, using the tool results above."
TOOL_PROVIDER = "fuse"
HEARTBEAT_S = 5.0
# Parallel tool calls arrive as one mcp_args each; after the first, wait this
# long for the rest before handing the turn back.
TOOL_GRACE_S = 1.0

_CODE_STATUS = {
    "unauthenticated": 401,
    "permission_denied": 403,
    "resource_exhausted": 429,
    "unavailable": 503,
    "deadline_exceeded": 504,
    "invalid_argument": 400,
    "not_found": 404,
}


class TurnError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


# --- OpenAI messages -> Cursor history ---------------------------------------

def _text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict) and isinstance(part.get("text"), str):
                parts.append(part["text"])
        return "\n".join(p for p in parts if p)
    return ""


def _args(raw) -> dict:
    if isinstance(raw, dict):
        return raw
    try:
        value = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {"_raw": raw}
    return value if isinstance(value, dict) else {"_raw": raw}


def build_turn(messages) -> tuple[list[dict], str]:
    """(history entries, active user text). The trailing user message is the
    action; everything before it is history."""
    msgs = [m for m in (messages or []) if isinstance(m, dict)]
    active = -1
    for i in range(len(msgs) - 1, -1, -1):
        role = msgs[i].get("role")
        if role == "user":
            active = i
            break
        if role in ("assistant", "tool"):
            break
    names: dict[str, str] = {}
    history: list[dict] = []
    for i, msg in enumerate(msgs):
        if i == active:
            continue
        role = msg.get("role")
        text = _text(msg.get("content"))
        if role in ("system", "developer"):
            if text:
                history.append({"role": "user", "content": [{"type": "text", "text": f"{SYSTEM_LEAD}\n\n{text}"}]})
                history.append({"role": "assistant", "content": [{"type": "text", "text": SYSTEM_ACK}]})
        elif role == "user":
            if text:
                history.append({"role": "user", "content": [{"type": "text", "text": text}]})
        elif role == "assistant":
            parts: list[dict] = [{"type": "text", "text": text}] if text else []
            for call in msg.get("tool_calls") or []:
                fn = (call.get("function") or {}) if isinstance(call, dict) else {}
                cid = str(call.get("id") or "") if isinstance(call, dict) else ""
                name = str(fn.get("name") or "")
                names[cid] = name
                parts.append({"type": "tool-call", "toolCallId": cid, "toolName": name, "args": _args(fn.get("arguments"))})
            if parts:
                history.append({"role": "assistant", "content": parts})
        elif role == "tool":
            # Kept even when empty: dropping it would orphan the call above.
            cid = str(msg.get("tool_call_id") or "")
            history.append({
                "role": "tool",
                "id": cid,
                "content": [{
                    "type": "tool-result",
                    "toolName": str(msg.get("name") or names.get(cid, "")),
                    "toolCallId": cid,
                    "result": text,
                }],
            })
    user_text = _text(msgs[active].get("content")) if active >= 0 else ""
    return history, user_text or CONTINUE_TEXT


def mcp_tools(tools) -> list[dict]:
    out = []
    for tool in tools or []:
        fn = tool.get("function") if isinstance(tool, dict) else None
        if not isinstance(fn, dict) or not fn.get("name"):
            continue
        out.append({
            "name": str(fn["name"]),
            "description": str(fn.get("description") or ""),
            "schema": json.dumps(fn.get("parameters") or {"type": "object", "properties": {}}),
        })
    return out


# --- AgentClientMessage encoding ----------------------------------------------

def _tool_def(e: ProtoEncoder, tool: dict) -> None:
    e.string(1, tool["name"])
    e.string(2, tool["description"])
    e.string(4, TOOL_PROVIDER)
    e.string(5, tool["name"])
    e.string(6, tool["schema"])


def encode_run_request(blob_ids: list[bytes], user_text: str, model: str, tools: list[dict], conversation_id: str) -> bytes:
    state = ProtoEncoder()
    for blob_id in blob_ids:
        state.blob(1, blob_id)
    user = ProtoEncoder()
    user.string(1, user_text)
    user.string(2, str(uuid.uuid4()))
    user_action = ProtoEncoder()
    user_action.blob(1, user.finish())
    action = ProtoEncoder()
    action.blob(1, user_action.finish())
    details = ProtoEncoder()
    details.string(1, model)
    details.string(3, model)
    details.string(4, model)
    requested = ProtoEncoder()
    requested.string(1, model)
    run = ProtoEncoder()
    run.blob(1, state.finish())
    run.blob(2, action.finish())
    run.blob(3, details.finish())
    if tools:
        mcp = ProtoEncoder()
        for tool in tools:
            mcp.message(1, lambda e, t=tool: _tool_def(e, t))
        run.blob(4, mcp.finish())
    run.string(5, conversation_id)
    run.blob(9, requested.finish())
    out = ProtoEncoder()
    out.blob(1, run.finish())
    return out.finish()


def encode_get_blob_result(kv_id: int, data: bytes | None) -> bytes:
    result = ProtoEncoder()
    if data is not None:
        result.blob(1, data)
    kv = ProtoEncoder()
    kv.uint32(1, kv_id)
    kv.blob(2, result.finish())
    out = ProtoEncoder()
    out.blob(3, kv.finish())
    return out.finish()


def encode_set_blob_ack(kv_id: int) -> bytes:
    kv = ProtoEncoder()
    kv.uint32(1, kv_id)
    kv.blob(3, b"")
    out = ProtoEncoder()
    out.blob(3, kv.finish())
    return out.finish()


def encode_request_context(exec_num: int, exec_id: str, tools: list[dict]) -> bytes:
    env = ProtoEncoder()
    env.string(1, "linux")
    env.string(3, "bash")
    env.string(10, "UTC")
    ctx = ProtoEncoder()
    ctx.blob(4, env.finish())
    for tool in tools:
        ctx.message(7, lambda e, t=tool: _tool_def(e, t))
    success = ProtoEncoder()
    success.blob(1, ctx.finish())
    result = ProtoEncoder()
    result.blob(1, success.finish())
    ex = ProtoEncoder()
    ex.uint32(1, exec_num)
    ex.string(15, exec_id)
    ex.blob(10, result.finish())
    out = ProtoEncoder()
    out.blob(2, ex.finish())
    return out.finish()


def encode_heartbeat() -> bytes:
    out = ProtoEncoder()
    out.blob(7, b"")
    return out.finish()


# --- McpArgs values: serialized google.protobuf.Value ------------------------

def decode_value(data: bytes):
    d = ProtoDecoder(data, strict=True)
    value = None
    while not d.done:
        field, wire = d.tag()
        if field == 2 and wire == 1:
            start = d._advance(8)
            value = struct.unpack("<d", d.bytes[start:d.pos])[0]
            if value.is_integer() and abs(value) < 2**53:
                value = int(value)
        elif field == 3 and wire == 2:
            value = d.string()
        elif field == 4 and wire == 0:
            value = bool(d._varint())
        elif field == 5 and wire == 2:
            value = _decode_struct(d.raw())
        elif field == 6 and wire == 2:
            value = [decode_value(item) for item in _repeated(d.raw())]
        elif field == 1 and wire == 0:
            d._varint()
            value = None
        else:
            d.skip(wire)
    return value


def _repeated(data: bytes) -> list[bytes]:
    d = ProtoDecoder(data, strict=True)
    out = []
    while not d.done:
        field, wire = d.tag()
        if field == 1 and wire == 2:
            out.append(d.raw())
        else:
            d.skip(wire)
    return out


def _decode_struct(data: bytes) -> dict:
    out = {}
    for entry in _repeated(data):
        d = ProtoDecoder(entry, strict=True)
        key, value = "", None
        while not d.done:
            field, wire = d.tag()
            if field == 1 and wire == 2:
                key = d.string()
            elif field == 2 and wire == 2:
                value = decode_value(d.raw())
            else:
                d.skip(wire)
        out[key] = value
    return out


def decode_arg(raw):
    try:
        data = base64.b64decode(raw or "")
    except (TypeError, ValueError):
        return raw
    if not data:
        return None
    try:
        return decode_value(data)
    except (ValueError, RecursionError, struct.error):
        text = data.decode("utf-8", errors="replace")
        try:
            return json.loads(text)
        except ValueError:
            return text


# --- One turn -------------------------------------------------------------------

def _error_info(obj) -> tuple[str, str]:
    """(connect code, readable message) from a unary error body or a trailer."""
    if not isinstance(obj, dict):
        return "", ""
    err = obj.get("error") if isinstance(obj.get("error"), dict) else obj
    code = str(err.get("code") or "").lower()
    message = str(err.get("message") or "")
    for detail in err.get("details") or []:
        debug = detail.get("debug") if isinstance(detail, dict) else None
        if not isinstance(debug, dict):
            continue
        info = debug.get("details") if isinstance(debug.get("details"), dict) else {}
        text = str(info.get("detail") or info.get("title") or debug.get("error") or "")
        if text and message in ("", "Error"):
            message = text
        if debug.get("error") == "ERROR_NOT_LOGGED_IN":
            code = "unauthenticated"
    return code, message[:500]


def _status_for(code: str, fallback: int = 502) -> int:
    return _CODE_STATUS.get(code, fallback)


def _safe_call_id(raw) -> str:
    cid = re.sub(r"[^A-Za-z0-9_-]", "_", str(raw or ""))
    return cid or "call_" + uuid.uuid4().hex[:24]


def _pick_name(args: dict, declared: set[str]) -> str:
    for key in ("toolName", "name"):
        value = str(args.get(key) or "")
        if value in declared:
            return value
    return str(args.get("toolName") or args.get("name") or "")


async def _frames(resp) -> AsyncIterator[tuple[int, bytes]]:
    buf = b""
    async for chunk in resp.aiter_bytes():
        buf += chunk
        frames, buf = iter_connect_frames(buf)
        for frame in frames:
            yield frame


async def run_turn(
    *,
    base: str,
    headers: dict,
    model: str,
    messages,
    tools,
    timeout: httpx.Timeout,
) -> AsyncIterator[tuple]:
    """Yield ("text", s), ("thinking", s), ("tool_call", call), ("usage", dict)
    and finally ("end", finish_reason). Failures raise TurnError."""
    history, user_text = build_turn(messages)
    declared = mcp_tools(tools)
    names = {t["name"] for t in declared}
    blobs: dict[bytes, bytes] = {}
    blob_ids = []
    for entry in history:
        data = json.dumps(entry, ensure_ascii=False, separators=(",", ":")).encode()
        digest = hashlib.sha256(data).digest()
        blobs[digest] = data
        blob_ids.append(digest)
    request_id = str(uuid.uuid4())
    client = httpx.AsyncClient(timeout=timeout)
    lock = asyncio.Lock()
    seq = 0

    async def append(payload: bytes) -> None:
        nonlocal seq
        async with lock:
            body = {
                "requestId": {"requestId": request_id},
                "appendSeqno": str(seq),
                "dataBinary": base64.b64encode(payload).decode("ascii"),
            }
            seq += 1
            r = await client.post(
                base + "/aiserver.v1.BidiService/BidiAppend",
                json=body,
                headers={**headers, "Content-Type": "application/json", "x-request-id": str(uuid.uuid4())},
            )
        if r.status_code != 200:
            try:
                code, message = _error_info(r.json())
            except ValueError:
                code, message = "", ""
            raise TurnError(_status_for(code, r.status_code if r.status_code >= 400 else 502), message or f"Cursor HTTP {r.status_code}")

    async def beat() -> None:
        while True:
            await asyncio.sleep(HEARTBEAT_S)
            try:
                await append(encode_heartbeat())
            except (TurnError, httpx.HTTPError):
                pass

    req = client.build_request(
        "POST",
        base + "/agent.v1.AgentService/RunSSE",
        content=connect_frame(json.dumps({"requestId": request_id}).encode(), compressed=False),
        headers={
            **headers,
            "Content-Type": "application/connect+json",
            "Accept": "application/connect+json",
            "x-request-id": request_id,
        },
    )
    resp = None
    heartbeat = None
    try:
        try:
            resp = await client.send(req, stream=True)
            if resp.status_code != 200:
                raw = await resp.aread()
                try:
                    code, message = _error_info(json.loads(raw))
                except ValueError:
                    code, message = "", ""
                raise TurnError(_status_for(code, resp.status_code), message or f"Cursor HTTP {resp.status_code}")
            await append(encode_run_request(blob_ids, user_text, model, declared, str(uuid.uuid4())))
        except httpx.TimeoutException as exc:
            raise TurnError(504, f"Cursor timed out ({type(exc).__name__})") from None
        except httpx.HTTPError as exc:
            raise TurnError(502, f"Cursor transport failure ({type(exc).__name__})") from None
        heartbeat = asyncio.create_task(beat())
        frames = _frames(resp)
        calls: list[dict] = []
        used = completion = 0
        ended = False
        while not ended:
            try:
                if calls:
                    flag, payload = await asyncio.wait_for(frames.__anext__(), TOOL_GRACE_S)
                else:
                    flag, payload = await frames.__anext__()
            except (StopAsyncIteration, asyncio.TimeoutError):
                break
            except httpx.TimeoutException:
                raise TurnError(504, "Cursor stopped sending") from None
            except httpx.HTTPError as exc:
                raise TurnError(502, f"Cursor transport failure ({type(exc).__name__})") from None
            if flag & CONNECT_END_STREAM:
                try:
                    code, message = _error_info(json.loads(payload or b"{}"))
                except ValueError:
                    code, message = "unknown", "invalid Cursor trailer"
                if code:
                    raise TurnError(_status_for(code), message or code)
                ended = True
                break
            try:
                msg = json.loads(payload)
            except ValueError:
                continue
            kv = msg.get("kvServerMessage")
            if isinstance(kv, dict):
                kid = int(kv.get("id") or 0)
                if isinstance(kv.get("getBlobArgs"), dict):
                    blob_id = base64.b64decode(kv["getBlobArgs"].get("blobId") or "")
                    await append(encode_get_blob_result(kid, blobs.get(blob_id)))
                elif isinstance(kv.get("setBlobArgs"), dict):
                    args = kv["setBlobArgs"]
                    blobs[base64.b64decode(args.get("blobId") or "")] = base64.b64decode(args.get("blobData") or "")
                    await append(encode_set_blob_ack(kid))
                continue
            ex = msg.get("execServerMessage")
            if isinstance(ex, dict):
                if isinstance(ex.get("requestContextArgs"), dict):
                    await append(encode_request_context(int(ex.get("id") or 0), str(ex.get("execId") or ""), declared))
                elif isinstance(ex.get("mcpArgs"), dict):
                    args = ex["mcpArgs"]
                    values = args.get("args") if isinstance(args.get("args"), dict) else {}
                    calls.append({
                        "id": _safe_call_id(args.get("toolCallId")),
                        "name": _pick_name(args, names),
                        "arguments": json.dumps({k: decode_arg(v) for k, v in values.items()}, ensure_ascii=False),
                    })
                continue
            checkpoint = msg.get("conversationCheckpointUpdate")
            if isinstance(checkpoint, dict):
                details = checkpoint.get("tokenDetails") if isinstance(checkpoint.get("tokenDetails"), dict) else {}
                used = int(details.get("usedTokens") or used)
                continue
            update = msg.get("interactionUpdate")
            if not isinstance(update, dict):
                continue
            if isinstance(update.get("textDelta"), dict) and update["textDelta"].get("text"):
                yield ("text", str(update["textDelta"]["text"]))
            elif isinstance(update.get("thinkingDelta"), dict) and update["thinkingDelta"].get("text"):
                yield ("thinking", str(update["thinkingDelta"]["text"]))
            elif isinstance(update.get("tokenDelta"), dict):
                completion += int(update["tokenDelta"].get("tokens") or 0)
            elif "turnEnded" in update:
                ended = True
        if used or completion:
            yield ("usage", {"prompt_tokens": max(used - completion, 0), "completion_tokens": completion})
        if calls:
            for call in calls:
                yield ("tool_call", call)
            yield ("end", "tool_calls")
        elif ended:
            yield ("end", "stop")
        else:
            raise TurnError(502, "Cursor stream ended before the turn finished")
    finally:
        if heartbeat is not None:
            heartbeat.cancel()
        if resp is not None:
            await resp.aclose()
        await client.aclose()
