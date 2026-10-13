"""Claude stream protocol and translation: Anthropic SSE → OpenAI chunks.

Parses the `event:`/`data:` frames of the Messages stream and translates
content blocks (text, thinking, signed thinking, tool_use) into OpenAI-style
`chat.completion.chunk` SSE. Malformed frames surface as protocol errors
instead of silent truncation.
"""
from __future__ import annotations

import asyncio
import json
import time

import httpx

from .base import THINKING_MARK
from .claude_catalog import fuse_id


def _sse(obj: dict) -> bytes:
    return f"data: {json.dumps(obj, separators=(',', ':'))}\n\n".encode()


class UpstreamProtocolError(ValueError):
    pass


SSE_EVENT_MAX = 32 * 1024 * 1024


async def _upstream_events(resp: httpx.Response):
    buf = ""

    def parse(block: str):
        event_name = ""
        data_lines = []
        for line in block.split("\n"):
            if line.startswith("event:"):
                event_name = line[6:].strip()
            elif line.startswith("data:"):
                data_lines.append(line[5:].strip())
        if not data_lines:
            return None
        raw = "\n".join(data_lines)
        if not raw or raw == "[DONE]":
            return None
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise UpstreamProtocolError("upstream sent malformed JSON") from exc
        if not isinstance(obj, dict):
            raise UpstreamProtocolError("upstream sent an invalid event")
        if event_name and "type" not in obj:
            obj["type"] = event_name
        return obj

    async for chunk in resp.aiter_text():
        # SSE allows CRLF and CR line endings. Normalize to LF, holding back a
        # trailing CR whose LF may arrive with the next chunk.
        buf += chunk
        carry = "\r" if buf.endswith("\r") else ""
        buf = buf[: len(buf) - len(carry)].replace("\r\n", "\n").replace("\r", "\n")
        while "\n\n" in buf:
            block, buf = buf.split("\n\n", 1)
            obj = parse(block)
            if obj is not None:
                yield obj
        buf += carry
        # A peer that never ends an event would otherwise grow buf forever.
        if len(buf) > SSE_EVENT_MAX:
            raise UpstreamProtocolError("upstream sent an oversized event")
    if buf.strip():
        obj = parse(buf)
        if obj is not None:
            yield obj


class _Translator:
    def __init__(self, model: str):
        self.model = model
        self.created = int(time.time())
        self.completion_id = "chatcmpl-claude"
        self.usage: dict = {}
        self.error = ""
        self.blocks: dict[int, dict] = {}
        self.tool_order: list[int] = []
        self.thinking: list[dict] = []
        self.stop_reason = ""
        self.input_tokens = 0
        self.output_tokens = 0
        self.cache_read = 0
        self.cache_write = 0

    def _chunk(self, delta: dict | None = None, finish: str | None = None, *, usage: dict | None = None) -> bytes:
        obj = {
            "id": self.completion_id,
            "object": "chat.completion.chunk",
            "created": self.created,
            "model": self.model,
            "choices": [{"index": 0, "delta": delta or {}, "finish_reason": finish}],
        }
        if usage:
            obj["usage"] = usage
        return _sse(obj)

    def _error_chunk(self, message: str) -> bytes:
        return _sse(
            {
                "id": self.completion_id,
                "object": "chat.completion.chunk",
                "created": self.created,
                "model": self.model,
                "choices": [],
                "error": {"message": message, "type": "api_error"},
            }
        )

    def _note_usage(self, raw) -> None:
        if not isinstance(raw, dict):
            return
        if raw.get("input_tokens") is not None:
            self.input_tokens = int(raw.get("input_tokens") or 0)
        if raw.get("output_tokens") is not None:
            self.output_tokens = int(raw.get("output_tokens") or 0)
        self.cache_read = int(raw.get("cache_read_input_tokens") or self.cache_read or 0)
        self.cache_write = int(raw.get("cache_creation_input_tokens") or self.cache_write or 0)
        prompt = self.input_tokens + self.cache_read + self.cache_write
        self.usage = {
            "prompt_tokens": prompt,
            "completion_tokens": self.output_tokens,
            "total_tokens": prompt + self.output_tokens,
            "cached_tokens": self.cache_read,
            "cache_write_tokens": self.cache_write,
            "reasoning_tokens": 0,
        }

    def _openai_usage(self) -> dict:
        u = self.usage or {}
        return {
            "prompt_tokens": u.get("prompt_tokens") or 0,
            "completion_tokens": u.get("completion_tokens") or 0,
            "total_tokens": u.get("total_tokens") or 0,
            "prompt_tokens_details": {
                "cached_tokens": u.get("cached_tokens") or 0,
                "cache_write_tokens": u.get("cache_write_tokens") or 0,
            },
            "completion_tokens_details": {"reasoning_tokens": 0},
        }

    def _handle(self, event: dict) -> bytes | None:
        etype = str(event.get("type") or "")
        if etype == "message_start":
            message = event.get("message") if isinstance(event.get("message"), dict) else {}
            if message.get("id"):
                self.completion_id = "chatcmpl-" + str(message["id"])
            self._note_usage(message.get("usage"))
            return self._chunk({"role": "assistant", "content": ""})
        if etype == "content_block_start":
            try:
                index = int(event.get("index") or 0)
            except (TypeError, ValueError):
                index = 0
            block = event.get("content_block") if isinstance(event.get("content_block"), dict) else {}
            kind = str(block.get("type") or "")
            self.blocks[index] = {"type": kind, "id": str(block.get("id") or ""), "name": str(block.get("name") or ""), "arguments": "", "thinking": "", "signature": "", "data": str(block.get("data") or "")}
            if kind in ("thinking", "redacted_thinking"):
                # Thinking may run for minutes with no delta; tell the pacer
                # the silence is expected.
                return THINKING_MARK + b"\n\n"
            if kind == "tool_use":
                self.tool_order.append(index)
                return self._chunk(
                    {
                        "tool_calls": [
                            {
                                "index": len(self.tool_order) - 1,
                                "id": self.blocks[index]["id"] or f"toolu_{index}",
                                "type": "function",
                                "function": {"name": self.blocks[index]["name"], "arguments": ""},
                            }
                        ]
                    }
                )
            return None
        if etype == "content_block_delta":
            try:
                index = int(event.get("index") or 0)
            except (TypeError, ValueError):
                index = 0
            delta = event.get("delta") if isinstance(event.get("delta"), dict) else {}
            kind = str(delta.get("type") or "")
            rec = self.blocks.get(index)
            if kind == "text_delta" and delta.get("text"):
                return self._chunk({"content": str(delta["text"])})
            if kind == "thinking_delta" and delta.get("thinking"):
                if rec is not None:
                    rec["thinking"] += str(delta["thinking"])
                return self._chunk({"reasoning_content": str(delta["thinking"])})
            if kind == "signature_delta" and delta.get("signature"):
                if rec is not None:
                    rec["signature"] += str(delta["signature"])
                return None
            if kind == "input_json_delta" and delta.get("partial_json") is not None:
                if rec is None:
                    # A delta for a block that never started has no call to
                    # belong to; attributing it to tool 0 corrupts that call.
                    return None
                piece = str(delta.get("partial_json") or "")
                rec["arguments"] += piece
                tool_index = self.tool_order.index(index) if index in self.tool_order else 0
                if not piece:
                    return None
                return self._chunk({"tool_calls": [{"index": tool_index, "function": {"arguments": piece}}]})
            return None
        if etype == "content_block_stop":
            try:
                index = int(event.get("index") or 0)
            except (TypeError, ValueError):
                index = 0
            rec = self.blocks.get(index)
            if rec and rec.get("type") == "redacted_thinking" and rec.get("data"):
                self.thinking.append({"redacted_thinking": rec["data"]})
            if rec and rec.get("type") == "thinking" and rec.get("signature"):
                # Empty thinking text with a signature is a valid
                # omitted-display block and must replay like any other.
                self.thinking.append({"thinking": rec.get("thinking") or "", "signature": rec["signature"]})
            if rec and rec.get("type") == "tool_use" and not rec.get("arguments"):
                rec["arguments"] = "{}"
                tool_index = self.tool_order.index(index) if index in self.tool_order else 0
                return self._chunk({"tool_calls": [{"index": tool_index, "function": {"arguments": "{}"}}]})
            return None
        if etype == "message_delta":
            delta = event.get("delta") if isinstance(event.get("delta"), dict) else {}
            if delta.get("stop_reason"):
                self.stop_reason = str(delta["stop_reason"])
            self._note_usage(event.get("usage"))
            return None
        if etype == "message_stop":
            return b"__FINAL__"
        if etype == "error":
            err = event.get("error") if isinstance(event.get("error"), dict) else event
            self.error = str((err or {}).get("message") or event.get("message") or "upstream error")
            return b"__ERROR__"
        if etype == "ping":
            return None
        return None

    def finish_reason(self) -> str:
        # A call cut off by max_tokens has incomplete arguments: never runnable.
        if self.stop_reason == "max_tokens":
            return "length"
        if self.tool_order or self.stop_reason == "tool_use":
            return "tool_calls"
        return "stop"

    def tool_calls(self) -> list[dict]:
        calls = []
        for index in self.tool_order:
            rec = self.blocks.get(index) or {}
            if not rec.get("name"):
                continue
            calls.append(
                {
                    "id": rec.get("id") or f"toolu_{index}",
                    "type": "function",
                    "function": {"name": rec["name"], "arguments": rec.get("arguments") or "{}"},
                }
            )
        return calls

    def _final_chunks(self) -> list[bytes]:
        delta: dict = {}
        if self.thinking:
            delta["claude_thinking"] = self.thinking
        out = [self._chunk(delta, self.finish_reason(), usage=self._openai_usage() if self.usage else None)]
        out.append(b"data: [DONE]\n\n")
        return out


def _signature_retryable(message: str) -> bool:
    text = (message or "").lower()
    return "signature" in text and "thinking" in text


async def _translated_stream(resp: httpx.Response, client: httpx.AsyncClient, model: str, collected: dict | None = None):
    translator = _Translator(fuse_id(model))
    stream_status = "failed"
    try:
        try:
            async for event in _upstream_events(resp):
                out = translator._handle(event)
                if out is None:
                    continue
                if out == b"__FINAL__":
                    stream_status = "complete"
                    break
                if out == b"__ERROR__":
                    yield translator._error_chunk(translator.error)
                    yield b"data: [DONE]\n\n"
                    return
                yield out
        except (UpstreamProtocolError, httpx.HTTPError) as exc:
            translator.error = f"upstream stream failed ({type(exc).__name__})"
        except (GeneratorExit, asyncio.CancelledError):
            stream_status = "cancelled"
            raise
        if stream_status != "complete":
            yield translator._error_chunk(translator.error or "upstream stream ended before a terminal response")
            yield b"data: [DONE]\n\n"
            return
        for piece in translator._final_chunks():
            yield piece
    finally:
        await resp.aclose()
        await client.aclose()
        if collected is not None:
            collected.clear()
            collected.update(translator.usage or {})
            collected["stream_status"] = stream_status
            if translator.usage:
                collected["usage_status"] = "complete" if stream_status == "complete" else "partial"
            if translator.thinking:
                collected["claude_thinking"] = translator.thinking


async def _aggregate(resp: httpx.Response, client: httpx.AsyncClient, model: str) -> dict:
    translator = _Translator(fuse_id(model))
    text_parts: list[str] = []
    reasoning_parts: list[str] = []
    terminal = False
    try:
        async for event in _upstream_events(resp):
            etype = str(event.get("type") or "")
            if etype == "content_block_delta":
                delta = event.get("delta") if isinstance(event.get("delta"), dict) else {}
                if delta.get("type") == "text_delta" and delta.get("text"):
                    text_parts.append(str(delta["text"]))
                elif delta.get("type") == "thinking_delta" and delta.get("thinking"):
                    reasoning_parts.append(str(delta["thinking"]))
            out = translator._handle(event)
            if out == b"__FINAL__":
                terminal = True
                break
            if out == b"__ERROR__":
                break
    except (UpstreamProtocolError, httpx.HTTPError) as exc:
        translator.error = f"upstream stream failed ({type(exc).__name__})"
    finally:
        await resp.aclose()
        await client.aclose()
    if not terminal and not translator.error:
        translator.error = "upstream stream ended before a terminal response"
    if translator.error:
        return {"error": {"message": translator.error, "type": "api_error", "code": 502}, "_fuse_usage": translator.usage}
    message: dict = {"role": "assistant", "content": "".join(text_parts)}
    if reasoning_parts:
        message["reasoning_content"] = "".join(reasoning_parts)
    if translator.thinking:
        message["claude_thinking"] = translator.thinking
    calls = translator.tool_calls()
    if calls:
        message["tool_calls"] = calls
    body = {
        "id": translator.completion_id,
        "object": "chat.completion",
        "created": translator.created,
        "model": fuse_id(model),
        "choices": [{"index": 0, "message": message, "finish_reason": translator.finish_reason()}],
    }
    if translator.usage:
        body["usage"] = dict(translator.usage)
    return body


def _error_message(raw: str) -> str:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return ""
    if not isinstance(data, dict):
        return ""
    err = data.get("error")
    if isinstance(err, dict):
        return str(err.get("message") or "")[:400]
    return str(data.get("message") or "")[:400]
