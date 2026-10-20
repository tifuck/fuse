"""Cursor agent wire protocol, driven the way cursor-agent drives it.

Transport is the CLI's HTTP/1.1 path: `agent.v1.AgentService/RunSSE` streams
protobuf AgentServerMessage frames down, and each client message goes up as a
unary `aiserver.v1.BidiService/BidiAppend`. Field numbers are taken from the
cursor-agent 2026.10.16 bundle.

One Cursor run spans a whole user turn, as in the CLI: when the model calls a
tool the run is parked, the caller executes the tool, and its result is sent
back into the same run as an `mcp_result`. When the turn ends, the server's
conversation checkpoint and the blobs it stored are kept, so the next user
message continues that conversation (same conversation_id) instead of
replaying history. That keeps Cursor's prompt cache warm. Anything that does
not line up with the kept state falls back to a full replay: history as
root_prompt_messages_json blobs the server pulls back by sha256 id.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import re
import struct
import time
import uuid

import httpx

from .devin_proto import CONNECT_END_STREAM, ProtoDecoder, ProtoEncoder, connect_frame, iter_connect_frames

SYSTEM_LEAD = "Configuration for this session, set by the operator of this API gateway:"
SYSTEM_ACK = "Understood. I will follow that configuration for the rest of this conversation."
# Sent as the user turn when a replayed request ends on tool results: there is
# no live run to hand them to, and the results are already in history.
CONTINUE_TEXT = "Continue, using the tool results above."
TOOL_PROVIDER = "fuse"
AGENT_MODE_AGENT = 1
HEARTBEAT_S = 5.0
# Parallel tool calls arrive as one mcp_args each; after the first, wait this
# long for the rest before handing the round back.
TOOL_GRACE_S = 1.0
# A parked run waits this long for its tool results before it is closed.
PARK_TTL_S = 900.0
# A finished turn's checkpoint stays resumable this long.
CONVERSATION_TTL_S = 3600.0
MAX_CONVERSATIONS = 256
MAX_BLOB_BYTES = 64 * 1024 * 1024

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
    def __init__(self, status: int, message: str, code: str = ""):
        super().__init__(message)
        self.status = status
        self.message = message
        # ErrorDetails enum or Connect code, for provider_errors.
        self.code = code


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


def _active_user(msgs: list[dict]) -> int:
    for i in range(len(msgs) - 1, -1, -1):
        role = msgs[i].get("role")
        if role == "user":
            return i
        if role in ("assistant", "tool"):
            break
    return -1


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def build_turn(messages) -> tuple[list[dict], str, list[tuple[str, bytes]]]:
    """(history entries, active user text, active user images). The trailing
    user message is the action; everything before it is history, as AI SDK
    messages: images inline as base64, a tool result's images in
    experimental_content (the forms Cursor's root prompt blobs accept)."""
    msgs = [m for m in (messages or []) if isinstance(m, dict)]
    active = _active_user(msgs)
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
            parts = [{"type": "text", "text": text}] if text else []
            parts += [{"type": "image", "image": _b64(data), "mediaType": mime} for mime, data in images_of(msg.get("content"))]
            if parts:
                history.append({"role": "user", "content": parts})
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
            result = {
                "type": "tool-result",
                "toolName": str(msg.get("name") or names.get(cid, "")),
                "toolCallId": cid,
                "result": text,
            }
            images = images_of(msg.get("content"))
            if images:
                result["experimental_content"] = [{"type": "text", "text": text}] + [
                    {"type": "image", "data": _b64(data), "mimeType": mime} for mime, data in images
                ]
            history.append({"role": "tool", "id": cid, "content": [result]})
    if active < 0:
        return history, CONTINUE_TEXT, []
    content = msgs[active].get("content")
    return history, _text(content), images_of(content)


def images_of(content) -> list[tuple[str, bytes]]:
    """(mime, bytes) of the images the source resolved into this content."""
    if not isinstance(content, list):
        return []
    return [(p["mime"], p["data"]) for p in content if isinstance(p, dict) and p.get("type") == "image_bytes"]


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

def _wrap(field: int, payload: bytes) -> bytes:
    out = ProtoEncoder()
    out.blob(field, payload)
    return out.finish()


def _tool_def(e: ProtoEncoder, tool: dict) -> None:
    e.string(1, tool["name"])
    e.string(2, tool["description"])
    e.string(4, TOOL_PROVIDER)
    e.string(5, tool["name"])
    e.string(6, tool["schema"])


def encode_selected_context(images: list[tuple[str, bytes]]) -> bytes:
    """SelectedContext with the user's images inline, as the CLI's ACP mode
    and the SDK attach them."""
    ctx = ProtoEncoder()
    for mime, data in images:
        image = ProtoEncoder()
        image.blob(8, data)
        image.string(2, str(uuid.uuid4()))
        image.string(7, mime)
        ctx.blob(1, image.finish())
    return ctx.finish()


def encode_state(blob_ids: list[bytes]) -> bytes:
    """A first-turn ConversationStateStructure. Never empty: the server
    rejects a zero-length state."""
    state = ProtoEncoder()
    for blob_id in blob_ids:
        state.blob(1, blob_id)
    state.uint32(10, AGENT_MODE_AGENT)
    return state.finish()


def encode_run_request(
    *,
    state: bytes,
    user_text: str,
    model_id: str,
    params: list[tuple[str, str]],
    max_mode: bool,
    display_name: str,
    tools: list[dict],
    conversation_id: str,
    images: list[tuple[str, bytes]] = (),
) -> bytes:
    user = ProtoEncoder()
    user.string(1, user_text)
    user.string(2, str(uuid.uuid4()))
    if images:
        user.blob(3, encode_selected_context(images))
    user.uint32(4, AGENT_MODE_AGENT)
    user_action = ProtoEncoder()
    user_action.blob(1, user.finish())
    action = _wrap(1, user_action.finish())
    details = ProtoEncoder()
    details.string(1, model_id)
    details.string(3, model_id)
    details.string(4, display_name or model_id)
    if max_mode:
        details.bool(7, True)
    requested = ProtoEncoder()
    requested.string(1, model_id)
    requested.bool(2, max_mode)
    for pid, value in params:
        param = ProtoEncoder()
        param.string(1, pid)
        param.string(2, value)
        requested.blob(3, param.finish())
    run = ProtoEncoder()
    run.blob(1, state)
    run.blob(2, action)
    run.blob(3, details.finish())
    if tools:
        mcp = ProtoEncoder()
        for tool in tools:
            mcp.message(1, lambda e, t=tool: _tool_def(e, t))
        run.blob(4, mcp.finish())
    run.string(5, conversation_id)
    run.blob(9, requested.finish())
    run.string(16, conversation_id)
    return _wrap(1, run.finish())


def encode_get_blob_result(kv_id: int, data: bytes | None) -> bytes:
    kv = ProtoEncoder()
    kv.uint32(1, kv_id)
    kv.blob(2, _wrap(1, data) if data is not None else b"")
    return _wrap(3, kv.finish())


def encode_set_blob_ack(kv_id: int) -> bytes:
    kv = ProtoEncoder()
    kv.uint32(1, kv_id)
    kv.blob(3, b"")
    return _wrap(3, kv.finish())


def encode_request_context(exec_num: int, exec_id: str, tools: list[dict]) -> bytes:
    env = ProtoEncoder()
    env.string(1, "linux")
    env.string(3, "bash")
    env.string(10, "UTC")
    ctx = ProtoEncoder()
    ctx.blob(4, env.finish())
    for tool in tools:
        ctx.message(7, lambda e, t=tool: _tool_def(e, t))
    ex = ProtoEncoder()
    ex.uint32(1, exec_num)
    ex.string(15, exec_id)
    ex.blob(10, _wrap(1, _wrap(1, ctx.finish())))
    return _wrap(2, ex.finish())


def encode_mcp_state(exec_num: int, exec_id: str, tools: list[dict]) -> bytes:
    """ExecClientMessage.mcp_state_exec_result: the caller's tools as one MCP
    server, the way the CLI answers when its model calls GET_MCP_TOOLS."""
    server = ProtoEncoder()
    server.string(1, TOOL_PROVIDER)
    server.string(2, TOOL_PROVIDER)
    for tool in tools:
        server.message(5, lambda e, t=tool: _tool_def(e, t))
    success = ProtoEncoder()
    success.blob(1, server.finish())
    ex = ProtoEncoder()
    ex.uint32(1, exec_num)
    ex.string(15, exec_id)
    ex.blob(36, _wrap(1, success.finish()))
    return _wrap(2, ex.finish())


def encode_mcp_result(exec_num: int, exec_id: str, text: str, images: list[tuple[str, bytes]] = (), is_error: bool = False) -> bytes:
    """ExecClientMessage.mcp_result: the caller's tool output (text, then
    images as McpImageContent), sent back into the live run the way the CLI
    answers an MCP exec."""
    success = ProtoEncoder()
    if text or not images:
        success.blob(1, _wrap(1, _wrap(1, text.encode("utf-8"))))
    for mime, data in images:
        image = ProtoEncoder()
        image.blob(1, data)
        image.string(2, mime)
        success.blob(1, _wrap(2, image.finish()))
    success.bool(2, is_error)
    ex = ProtoEncoder()
    ex.uint32(1, exec_num)
    ex.string(15, exec_id)
    ex.blob(11, _wrap(1, success.finish()))
    return _wrap(2, ex.finish())


def encode_heartbeat() -> bytes:
    return _wrap(7, b"")


# --- AgentServerMessage decoding ------------------------------------------------

def _fields(data: bytes) -> dict[int, list]:
    """field -> values (bytes for length-delimited, int for varints)."""
    out: dict[int, list] = {}
    d = ProtoDecoder(data, strict=True)
    while not d.done:
        field, wire = d.tag()
        if wire == 0:
            value = d._varint()
        elif wire == 2:
            value = d.raw()
        else:
            d.skip(wire)
            continue
        out.setdefault(field, []).append(value)
    return out


def _one(fields: dict, field: int, default=None):
    values = fields.get(field)
    return values[-1] if values else default


def _str(fields: dict, field: int) -> str:
    value = _one(fields, field, b"")
    return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else ""


def _int(fields: dict, field: int) -> int:
    value = _one(fields, field, 0)
    if not isinstance(value, int):
        return 0
    return value - (1 << 64) if value >= 1 << 63 else value


def decode_value(data: bytes):
    """A serialized google.protobuf.Value (McpArgs.args values)."""
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
            value = {}
            for entry in _fields(d.raw()).get(1, []):
                pair = _fields(entry)
                value[_str(pair, 1)] = decode_value(_one(pair, 2, b""))
        elif field == 6 and wire == 2:
            value = [decode_value(item) for item in _fields(d.raw()).get(1, [])]
        elif field == 1 and wire == 0:
            d._varint()
            value = None
        else:
            d.skip(wire)
    return value


def decode_arg(data: bytes):
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


def decode_server_message(data: bytes) -> tuple:
    """One AgentServerMessage as a small event tuple; ("other",) when Fuse
    has nothing to do with it."""
    top = _fields(data)
    if 1 in top:
        update = _fields(_one(top, 1))
        if 1 in update:
            return ("text", _str(_fields(_one(update, 1)), 1))
        if 4 in update:
            return ("thinking", _str(_fields(_one(update, 4)), 1))
        if 8 in update:
            return ("tokens", _int(_fields(_one(update, 8)), 1))
        if 14 in update:
            ended = _fields(_one(update, 14))
            keys = ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens", "reasoning_tokens")
            usage = {key: _int(ended, i + 1) for i, key in enumerate(keys) if i + 1 in ended}
            return ("turn_end", usage)
        return ("other",)
    if 2 in top:
        ex = _fields(_one(top, 2))
        exec_num, exec_id = _int(ex, 1), _str(ex, 15)
        if 10 in ex:
            return ("request_context", exec_num, exec_id)
        if 36 in ex:
            return ("mcp_state", exec_num, exec_id)
        if 11 in ex:
            mcp = _fields(_one(ex, 11))
            args = {}
            for entry in mcp.get(2, []):
                pair = _fields(entry)
                args[_str(pair, 1)] = decode_arg(_one(pair, 2, b""))
            return ("mcp", exec_num, exec_id, {
                "name": _str(mcp, 1),
                "tool_name": _str(mcp, 5),
                "tool_call_id": _str(mcp, 3),
                "args": args,
            })
        return ("other",)
    if 3 in top:
        raw = _one(top, 3)
        details = _fields(_one(_fields(raw), 5, b""))
        return ("checkpoint", raw, _int(details, 1))
    if 4 in top:
        kv = _fields(_one(top, 4))
        kv_id = _int(kv, 1)
        if 2 in kv:
            return ("get_blob", kv_id, _one(_fields(_one(kv, 2)), 1, b""))
        if 3 in kv:
            blob = _fields(_one(kv, 3))
            return ("set_blob", kv_id, _one(blob, 1, b""), _one(blob, 2, b""))
    return ("other",)


def error_info(obj) -> tuple[str, str]:
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


def error_code(obj) -> str:
    """The ErrorDetails enum (ERROR_PRO_USER_USAGE_LIMIT...) when Cursor sent
    one, else the Connect code."""
    if not isinstance(obj, dict):
        return ""
    err = obj.get("error") if isinstance(obj.get("error"), dict) else obj
    for detail in err.get("details") or []:
        debug = detail.get("debug") if isinstance(detail, dict) else None
        if isinstance(debug, dict) and isinstance(debug.get("error"), str) and debug["error"]:
            return debug["error"].lower()
    return str(err.get("code") or "").lower()


def status_for(code: str, fallback: int = 502) -> int:
    return _CODE_STATUS.get(code, fallback)


def safe_call_id(raw) -> str:
    cid = re.sub(r"[^A-Za-z0-9_-]", "_", str(raw or ""))
    return cid or "call_" + uuid.uuid4().hex[:24]


# --- run: RunSSE + BidiAppend ---------------------------------------------------

class CursorRun:
    """One live agent.v1 run. A background reader answers the server's KV
    and request-context calls and queues model events; each Fuse request
    drains one round of them. Between rounds the run stays open (parked)
    with heartbeats, waiting for the caller's tool results."""

    def __init__(self, *, base: str, headers: dict, timeout: httpx.Timeout, blobs: dict, tools: list[dict]):
        self.base = base
        self.headers = headers
        self.blobs = blobs
        self.tools = tools
        self.names = {t["name"] for t in tools}
        self.request_id = str(uuid.uuid4())
        self.client = httpx.AsyncClient(timeout=timeout)
        self.queue: asyncio.Queue = asyncio.Queue()
        self.pending: dict[str, tuple[int, str]] = {}
        self.checkpoint: bytes | None = None
        self.used_tokens = 0
        self.turn_usage: dict = {}
        self.ended = False
        self.closed = False
        self.reported_prompt = 0
        self.reported_output = 0
        self._seq = 0
        self._lock = asyncio.Lock()
        self._resp = None
        self._tasks: list[asyncio.Task] = []
        self._expiry: asyncio.TimerHandle | None = None
        self._shut = False

    async def append(self, payload: bytes) -> None:
        async with self._lock:
            body = {
                "requestId": {"requestId": self.request_id},
                "appendSeqno": str(self._seq),
                "dataBinary": base64.b64encode(payload).decode("ascii"),
            }
            self._seq += 1
            try:
                r = await self.client.post(
                    self.base + "/aiserver.v1.BidiService/BidiAppend",
                    json=body,
                    headers={**self.headers, "Content-Type": "application/json", "x-request-id": str(uuid.uuid4())},
                )
            except httpx.TimeoutException as exc:
                raise TurnError(504, f"Cursor timed out ({type(exc).__name__})") from None
            except httpx.HTTPError as exc:
                raise TurnError(502, f"Cursor transport failure ({type(exc).__name__})") from None
        if r.status_code != 200:
            try:
                obj = r.json()
            except ValueError:
                obj = None
            code, message = error_info(obj)
            raise TurnError(status_for(code, r.status_code if r.status_code >= 400 else 502),
                            message or f"Cursor HTTP {r.status_code}", error_code(obj))

    async def start(self, run_request: bytes) -> None:
        bidi_id = ProtoEncoder()
        bidi_id.string(1, self.request_id)
        req = self.client.build_request(
            "POST",
            self.base + "/agent.v1.AgentService/RunSSE",
            content=connect_frame(bidi_id.finish(), compressed=False),
            headers={
                **self.headers,
                "Content-Type": "application/connect+proto",
                "x-cursor-streaming": "true",
                "x-request-id": self.request_id,
            },
        )
        try:
            try:
                self._resp = await self.client.send(req, stream=True)
            except httpx.TimeoutException as exc:
                raise TurnError(504, f"Cursor timed out ({type(exc).__name__})") from None
            except httpx.HTTPError as exc:
                raise TurnError(502, f"Cursor transport failure ({type(exc).__name__})") from None
            if self._resp.status_code != 200:
                raw = await self._resp.aread()
                try:
                    obj = json.loads(raw)
                except ValueError:
                    obj = None
                code, message = error_info(obj)
                raise TurnError(status_for(code, self._resp.status_code),
                                message or f"Cursor HTTP {self._resp.status_code}", error_code(obj))
            await self.append(run_request)
        except BaseException:
            await self.aclose()
            raise
        self._tasks = [asyncio.create_task(self._read()), asyncio.create_task(self._beat())]

    async def _beat(self) -> None:
        while True:
            await asyncio.sleep(HEARTBEAT_S)
            try:
                await self.append(encode_heartbeat())
            except TurnError:
                pass

    async def _read(self) -> None:
        buf = b""
        try:
            async for chunk in self._resp.aiter_bytes():
                buf += chunk
                frames, buf = iter_connect_frames(buf)
                for flag, payload in frames:
                    if flag & CONNECT_END_STREAM:
                        try:
                            obj = json.loads(payload or b"{}")
                            code, message = error_info(obj)
                        except ValueError:
                            obj, code, message = None, "unknown", "invalid Cursor trailer"
                        self.queue.put_nowait(("error", TurnError(status_for(code), message or code, error_code(obj) or code))
                                              if code else ("closed",))
                        return
                    try:
                        event = decode_server_message(payload)
                    except (ValueError, struct.error):
                        continue
                    await self._handle(event)
            self.queue.put_nowait(("closed",))
        except asyncio.CancelledError:
            raise
        except TurnError as exc:
            self.queue.put_nowait(("error", exc))
        except httpx.TimeoutException:
            self.queue.put_nowait(("error", TurnError(504, "Cursor stopped sending")))
        except (httpx.HTTPError, ValueError) as exc:
            self.queue.put_nowait(("error", TurnError(502, f"Cursor transport failure ({type(exc).__name__})")))
        finally:
            self.closed = True

    async def _handle(self, event: tuple) -> None:
        kind = event[0]
        if kind == "get_blob":
            await self.append(encode_get_blob_result(event[1], self.blobs.get(event[2])))
        elif kind == "set_blob":
            self.blobs[event[2]] = event[3]
            await self.append(encode_set_blob_ack(event[1]))
        elif kind == "request_context":
            await self.append(encode_request_context(event[1], event[2], self.tools))
        elif kind == "mcp_state":
            await self.append(encode_mcp_state(event[1], event[2], self.tools))
        elif kind == "mcp":
            _, exec_num, exec_id, args = event
            call_id = safe_call_id(args["tool_call_id"])
            name = next((n for n in (args["tool_name"], args["name"]) if n in self.names), args["tool_name"] or args["name"])
            self.pending[call_id] = (exec_num, exec_id)
            self.queue.put_nowait(("tool_call", {
                "id": call_id,
                "name": name,
                "arguments": json.dumps(args["args"], ensure_ascii=False),
            }))
        elif kind == "checkpoint":
            self.checkpoint = event[1]
            self.used_tokens = event[2] or self.used_tokens
        elif kind in ("text", "thinking"):
            if event[1]:
                self.queue.put_nowait(event)
        elif kind in ("tokens", "turn_end"):
            self.queue.put_nowait(event)

    async def round(self):
        """One Fuse round: text/thinking/tool_call events, then ("usage", u)
        and ("end", finish_reason). Ends at the turn's end or, after tool
        calls, once no more arrive within TOOL_GRACE_S (the run is parked)."""
        self._disarm()
        calls = output = 0
        while True:
            try:
                event = await (asyncio.wait_for(self.queue.get(), TOOL_GRACE_S) if calls else self.queue.get())
            except asyncio.TimeoutError:
                break
            kind = event[0]
            if kind in ("text", "thinking"):
                yield event
            elif kind == "tool_call":
                calls += 1
                yield event
            elif kind == "tokens":
                output += max(int(event[1]), 0)
            elif kind == "turn_end":
                self.ended = True
                self.turn_usage = event[1]
                break
            elif kind == "closed":
                if calls:
                    break
                raise TurnError(502, "Cursor stream ended before the turn finished")
            elif kind == "error":
                raise event[1]
        yield ("usage", self._round_usage(output))
        yield ("end", "tool_calls" if calls and not self.ended else "stop")

    def _round_usage(self, output: int) -> dict:
        """Per-call usage for one round. Rounds that end on a tool call only
        know the streamed output and the context size; the turn's end carries
        exact totals (input includes cache reads and writes), and the last
        round reports whatever earlier rounds have not."""
        estimate = max(self.used_tokens - output, 0)
        totals = self.turn_usage if self.ended else {}
        if "input_tokens" in totals or "output_tokens" in totals:
            prompt = max(int(totals["input_tokens"]) - self.reported_prompt, 0) if "input_tokens" in totals else estimate
            completion = max(int(totals["output_tokens"]) - self.reported_output, 0) if "output_tokens" in totals else output
            cached = min(max(int(totals.get("cache_read_tokens", 0)), 0), prompt)
            written = min(max(int(totals.get("cache_write_tokens", 0)), 0), prompt - cached)
            usage = {
                "prompt_tokens": prompt,
                "completion_tokens": completion,
                "total_tokens": prompt + completion,
                "prompt_tokens_details": {"cached_tokens": cached, "cache_write_tokens": written},
                "completion_tokens_details": {"reasoning_tokens": min(max(int(totals.get("reasoning_tokens", 0)), 0), completion)},
            }
        else:
            prompt, completion = estimate, output
            usage = {"prompt_tokens": prompt, "completion_tokens": completion, "total_tokens": prompt + completion}
        self.reported_prompt += prompt
        self.reported_output += completion
        return usage

    async def resume(self, results: list[tuple[str, str, list]]) -> None:
        """Hand the caller's tool results (text, images) to the parked run."""
        if self.closed or self._shut:
            raise TurnError(502, "the parked Cursor run is gone")
        for call_id, text, images in results:
            exec_num, exec_id = self.pending.pop(call_id)
            await self.append(encode_mcp_result(exec_num, exec_id, text, images))

    def park(self) -> None:
        self._disarm()
        self._expiry = asyncio.get_running_loop().call_later(PARK_TTL_S, lambda: asyncio.ensure_future(self.aclose()))

    def _disarm(self) -> None:
        if self._expiry is not None:
            self._expiry.cancel()
            self._expiry = None

    async def aclose(self) -> None:
        if self._shut:
            return
        self._shut = True
        self.closed = True
        self._disarm()
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        if self._resp is not None:
            await self._resp.aclose()
        await self.client.aclose()


# --- kept conversations -----------------------------------------------------------

def _session_tail_mark() -> str:
    from ..chat_generation.messages import SESSION_TAIL_MARK

    return SESSION_TAIL_MARK


def canon(msg: dict) -> tuple:
    """What a message contributes to the conversation, for prefix matching.
    The per-turn session tail Fuse appends to the newest user message is not
    part of the stored history, so it is ignored."""
    role = msg.get("role")
    role = "system" if role == "developer" else role
    text = _text(msg.get("content"))
    if role == "user":
        cut = text.rfind(_session_tail_mark())
        if cut >= 0:
            text = text[:cut]
    calls = tuple(
        (str(c.get("id") or ""), str((c.get("function") or {}).get("name") or ""),
         json.dumps(_args((c.get("function") or {}).get("arguments")), sort_keys=True, ensure_ascii=False))
        for c in (msg.get("tool_calls") or [])
        if isinstance(c, dict)
    )
    images = []
    content = msg.get("content")
    for part in content if isinstance(content, list) else []:
        if not isinstance(part, dict):
            continue
        if part.get("type") == "image_url":
            images.append(hashlib.sha256(json.dumps(part, sort_keys=True, ensure_ascii=False).encode()).hexdigest())
        elif part.get("type") == "image_bytes":
            images.append((part.get("mime"), hashlib.sha256(part["data"]).hexdigest()))
    return (role, text.strip(), calls, str(msg.get("tool_call_id") or ""), tuple(images))


class Conversation:
    """A Cursor conversation Fuse can continue: the history it represents,
    the blobs the server stored with the client, and either a parked run
    (mid-turn) or the checkpoint of the finished turn."""

    def __init__(self, *, user_id, cred_id: int, blobs: dict | None = None, conversation_id: str = ""):
        self.user_id = user_id
        self.cred_id = int(cred_id)
        self.conversation_id = conversation_id or str(uuid.uuid4())
        self.blobs: dict[bytes, bytes] = blobs if blobs is not None else {}
        self.transcript: list[tuple] = []
        self.checkpoint: bytes | None = None
        self.run: CursorRun | None = None
        # (model_id, params, max_mode) the conversation's run was opened with.
        self.selection: tuple | None = None
        self.busy = False
        self.touched = time.monotonic()


_conversations: dict[str, Conversation] = {}


def _blob_bytes(conv: Conversation) -> int:
    return sum(len(v) for v in conv.blobs.values())


def drop(conv: Conversation) -> None:
    _conversations.pop(conv.conversation_id, None)
    run, conv.run = conv.run, None
    if run is not None:
        asyncio.ensure_future(run.aclose())


def _evict() -> None:
    now = time.monotonic()
    for conv in list(_conversations.values()):
        if conv.busy:
            continue
        if conv.run is not None:
            if conv.run.closed or now - conv.touched > PARK_TTL_S:
                drop(conv)
        elif now - conv.touched > CONVERSATION_TTL_S:
            drop(conv)
    idle = sorted((c for c in _conversations.values() if not c.busy), key=lambda c: c.touched)
    for conv in idle[: max(0, len(_conversations) - MAX_CONVERSATIONS)]:
        drop(conv)


def match(user_id, messages) -> tuple[Conversation | None, str, list[dict]]:
    """The kept conversation this request continues, as (conversation, how,
    new messages). how is "resume" (tool results for a parked run) or
    "followup" (new user messages after a finished turn)."""
    _evict()
    msgs = [m for m in (messages or []) if isinstance(m, dict)]
    request = [canon(m) for m in msgs]
    best = None
    for conv in _conversations.values():
        n = len(conv.transcript)
        if conv.user_id != user_id or conv.busy or not n or n >= len(request) or request[:n] != conv.transcript:
            continue
        if best is None or n > len(best.transcript):
            best = conv
    if best is None:
        return None, "", []
    tail = msgs[len(best.transcript):]
    if best.run is not None:
        ids = [str(m.get("tool_call_id") or "") for m in tail if m.get("role") == "tool"]
        if len(ids) == len(tail) and len(set(ids)) == len(ids) and set(ids) == set(best.run.pending):
            return best, "resume", tail
    elif best.checkpoint and all(m.get("role") == "user" for m in tail):
        return best, "followup", tail
    # The conversation moved on in a way this state cannot follow (a steer
    # between tool results, an edit): it will be replayed, so let it go.
    drop(best)
    return None, "", []


def keep(conv: Conversation, messages, reply: dict, run: CursorRun) -> None:
    """Remember where a round left the conversation: parked on tool calls,
    or at the end of the turn with its checkpoint."""
    msgs = [m for m in (messages or []) if isinstance(m, dict)]
    conv.transcript = [canon(m) for m in msgs] + [canon(reply)]
    conv.touched = time.monotonic()
    conv.busy = False
    if run.ended or run.closed:
        conv.run = None
        conv.checkpoint = run.checkpoint if run.ended else None
        asyncio.ensure_future(run.aclose())
    else:
        conv.run = run
        run.park()
    if (conv.run is None and not conv.checkpoint) or _blob_bytes(conv) > MAX_BLOB_BYTES:
        drop(conv)
        return
    _conversations[conv.conversation_id] = conv
