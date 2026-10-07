"""ChatGPT stream protocol and translation: Codex `/responses` SSE → OpenAI chunks.

Parses `data:` JSON events of the Responses stream and translates output
items (text, reasoning, function_call) into OpenAI-style `chat.completion.chunk`
SSE, plus non-stream error-body translation for the chat loop.
"""
from __future__ import annotations

import json
import time

import httpx

from ..proxy import usage_from_obj


def _sse(obj: dict) -> bytes:
    return f"data: {json.dumps(obj, separators=(',', ':'))}\n\n".encode()


class UpstreamProtocolError(ValueError):
    pass


async def _error_body(resp: httpx.Response) -> dict:
    """Read a streamed error response. httpx.json() raises ResponseNotRead
    until the body has been pulled."""
    try:
        await resp.aread()
    except Exception:
        return {"error": {"message": f"upstream {resp.status_code}"}}
    text = resp.text or ""
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        return {"error": {"message": text[:500]}}
    if isinstance(obj, dict):
        return obj
    return {"error": {"message": str(obj)[:500]}}


def _unsupported_param(obj) -> str | None:
    """Codex /responses 400: {'detail': 'Unsupported parameter: max_output_tokens'}."""
    if not isinstance(obj, dict):
        return None
    texts: list[str] = []
    detail = obj.get("detail")
    if isinstance(detail, str):
        texts.append(detail)
    err = obj.get("error")
    if isinstance(err, dict):
        param = err.get("param")
        if isinstance(param, str) and param.strip():
            return param.strip()
        for key in ("message", "msg"):
            val = err.get(key)
            if isinstance(val, str):
                texts.append(val)
    elif isinstance(err, str):
        texts.append(err)
    for text in texts:
        lower = text.lower()
        if "unsupported parameter:" in lower:
            tail = text.split(":", 1)[1].strip()
            name = tail.split()[0].strip(".,;\"'`") if tail else ""
            return name or None
        marker = " is not supported"
        idx = lower.find(marker)
        if idx > 0:
            name = text[:idx].strip().split()[-1].strip(".,;\"'`")
            return name or None
    return None


def _usage_from_response(u: dict) -> dict:
    return usage_from_obj({"usage": u}) if isinstance(u, dict) else {}


async def _upstream_events(resp: httpx.Response):
    buf = ""

    def parse(line: str):
        text = line.strip()
        if not text.startswith("data:"):
            return None
        data = text[5:].strip()
        if not data or data == "[DONE]":
            return None
        try:
            obj = json.loads(data)
        except json.JSONDecodeError as exc:
            raise UpstreamProtocolError("upstream sent malformed terminal JSON") from exc
        if not isinstance(obj, dict):
            raise UpstreamProtocolError("upstream sent an invalid event object")
        return obj

    async for chunk in resp.aiter_text():
        buf += chunk
        while "\n" in buf:
            line, buf = buf.split("\n", 1)
            obj = parse(line)
            if obj is not None:
                yield obj
    if buf.strip():
        obj = parse(buf)
        if obj is not None:
            yield obj


class _CallBook:
    def __init__(self):
        self.order: list[str] = []
        self.calls: dict[str, dict] = {}

    def add(self, item_id: str, call_id: str, name: str, arguments: str) -> dict:
        rec = {
            "index": len(self.order),
            "id": call_id,
            "name": name,
            "arguments": arguments,
        }
        self.calls[item_id] = rec
        self.order.append(item_id)
        return rec

    def get(self, item_id: str) -> dict | None:
        return self.calls.get(item_id)

    def tool_calls(self) -> list[dict]:
        return [
            {
                "id": self.calls[i]["id"],
                "type": "function",
                "function": {
                    "name": self.calls[i]["name"],
                    "arguments": self.calls[i]["arguments"],
                },
            }
            for i in self.order
            if self.calls[i]["name"]
        ]


class _Translator:
    def __init__(self, model: str):
        self.model = model
        self.created = int(time.time())
        self.completion_id = "chatcmpl-chatgpt"
        self.book = _CallBook()
        self.usage: dict = {}
        self.error: str = ""

    def _chunk(self, delta: dict | None = None, finish: str | None = None) -> bytes:
        obj = {
            "id": self.completion_id,
            "object": "chat.completion.chunk",
            "created": self.created,
            "model": self.model,
            "choices": [{"index": 0, "delta": delta or {}, "finish_reason": finish}],
        }
        return _sse(obj)

    def _error_chunk(self, message: str) -> bytes:
        obj = {
            "id": self.completion_id,
            "object": "chat.completion.chunk",
            "created": self.created,
            "model": self.model,
            "choices": [],
            "error": {"message": message, "type": "api_error"},
        }
        return _sse(obj)

    def _handle(self, event: dict) -> bytes | None:
        etype = event.get("type") or ""
        if etype == "response.created":
            response = event.get("response") if isinstance(event.get("response"), dict) else {}
            self.completion_id = "chatcmpl-" + str(response.get("id") or "chatgpt")
            return self._chunk({"role": "assistant", "content": ""})
        if etype == "response.output_text.delta":
            delta = event.get("delta")
            if delta:
                return self._chunk({"content": str(delta)})
            return None
        if etype in ("response.reasoning_summary_text.delta", "response.reasoning_text.delta"):
            delta = event.get("delta")
            if delta:
                return self._chunk({"reasoning_content": str(delta)})
            return None
        if etype == "response.output_item.added":
            item = event.get("item") if isinstance(event.get("item"), dict) else {}
            if item.get("type") == "function_call":
                item_id = str(item.get("id") or "")
                rec = self.book.add(
                    item_id,
                    str(item.get("call_id") or item.get("id") or f"call_{len(self.book.order)}"),
                    str(item.get("name") or ""),
                    str(item.get("arguments") or ""),
                )
                return self._chunk(
                    {
                        "tool_calls": [
                            {
                                "index": rec["index"],
                                "id": rec["id"],
                                "type": "function",
                                "function": {"name": rec["name"], "arguments": rec["arguments"]},
                            }
                        ]
                    }
                )
            return None
        if etype == "response.function_call_arguments.delta":
            rec = self.book.get(str(event.get("item_id") or ""))
            if rec and event.get("delta"):
                rec["arguments"] += str(event.get("delta"))
                return self._chunk(
                    {"tool_calls": [{"index": rec["index"], "function": {"arguments": str(event.get("delta"))}}]}
                )
            return None
        if etype == "response.function_call_arguments.done":
            rec = self.book.get(str(event.get("item_id") or ""))
            if rec and event.get("arguments") is not None:
                rec["arguments"] = str(event.get("arguments") or "")
            return None
        if etype == "response.completed":
            response = event.get("response") if isinstance(event.get("response"), dict) else {}
            if not response:
                self.error = "upstream terminal response is malformed"
                return b"__ERROR__"
            usage = response.get("usage") if isinstance(response.get("usage"), dict) else {}
            if usage:
                self.usage = _usage_from_response(usage)
            return b"__FINAL__"
        if etype in ("response.failed", "response.incomplete"):
            response = event.get("response") if isinstance(event.get("response"), dict) else {}
            usage = response.get("usage") if isinstance(response.get("usage"), dict) else {}
            if usage:
                self.usage = _usage_from_response(usage)
            err = response.get("error") if isinstance(response.get("error"), dict) else {}
            self.error = str(err.get("message") or ("upstream response incomplete" if etype == "response.incomplete" else "upstream response failed"))
            return b"__ERROR__"
        if etype == "error":
            err = event.get("error") if isinstance(event.get("error"), dict) else {}
            self.error = str(err.get("message") or event.get("message") or "upstream error")
            return b"__ERROR__"
        return None

    def _final_chunks(self) -> list[bytes]:
        finish = "tool_calls" if self.book.order else "stop"
        out = [self._chunk({}, finish)]
        if self.usage:
            obj = {
                "id": self.completion_id,
                "object": "chat.completion.chunk",
                "created": self.created,
                "model": self.model,
                "choices": [],
                "usage": {
                    "prompt_tokens": self.usage["prompt_tokens"],
                    "completion_tokens": self.usage["completion_tokens"],
                    "total_tokens": self.usage["total_tokens"],
                    "prompt_tokens_details": {
                        "cached_tokens": self.usage.get("cached_tokens") or 0,
                        "cache_write_tokens": self.usage.get("cache_write_tokens") or 0,
                    },
                    "completion_tokens_details": {
                        "reasoning_tokens": self.usage.get("reasoning_tokens") or 0,
                    },
                },
            }
            out.append(_sse(obj))
        out.append(b"data: [DONE]\n\n")
        return out


async def _translated_stream(
    resp: httpx.Response, client: httpx.AsyncClient, model: str, collected: dict | None = None
):
    translator = _Translator(model)
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


async def _aggregate(resp: httpx.Response, client: httpx.AsyncClient, model: str) -> dict:
    translator = _Translator(model)
    text_parts: list[str] = []
    reasoning_parts: list[str] = []
    terminal = False
    try:
        async for event in _upstream_events(resp):
            etype = event.get("type") or ""
            if etype == "response.created":
                response = event.get("response") if isinstance(event.get("response"), dict) else {}
                translator.completion_id = "chatcmpl-" + str(response.get("id") or "chatgpt")
            elif etype == "response.output_text.delta":
                if event.get("delta"):
                    text_parts.append(str(event.get("delta")))
            elif etype in ("response.reasoning_summary_text.delta", "response.reasoning_text.delta"):
                if event.get("delta"):
                    reasoning_parts.append(str(event.get("delta")))
            elif etype == "response.output_item.added":
                item = event.get("item") if isinstance(event.get("item"), dict) else {}
                if item.get("type") == "function_call":
                    translator.book.add(
                        str(item.get("id") or ""),
                        str(item.get("call_id") or item.get("id") or f"call_{len(translator.book.order)}"),
                        str(item.get("name") or ""),
                        str(item.get("arguments") or ""),
                    )
            elif etype == "response.function_call_arguments.delta":
                rec = translator.book.get(str(event.get("item_id") or ""))
                if rec and event.get("delta"):
                    rec["arguments"] += str(event.get("delta"))
            elif etype == "response.function_call_arguments.done":
                rec = translator.book.get(str(event.get("item_id") or ""))
                if rec and event.get("arguments") is not None:
                    rec["arguments"] = str(event.get("arguments") or "")
            elif etype == "response.completed":
                response = event.get("response") if isinstance(event.get("response"), dict) else {}
                if not response:
                    translator.error = "upstream terminal response is malformed"
                    break
                usage = response.get("usage") if isinstance(response.get("usage"), dict) else {}
                if usage:
                    translator.usage = _usage_from_response(usage)
                terminal = True
                break
            elif etype in ("response.failed", "response.incomplete"):
                response = event.get("response") if isinstance(event.get("response"), dict) else {}
                usage = response.get("usage") if isinstance(response.get("usage"), dict) else {}
                if usage:
                    translator.usage = _usage_from_response(usage)
                err = response.get("error") if isinstance(response.get("error"), dict) else {}
                translator.error = str(err.get("message") or ("upstream response incomplete" if etype == "response.incomplete" else "upstream response failed"))
                break
            elif etype == "error":
                err = event.get("error") if isinstance(event.get("error"), dict) else {}
                translator.error = str(err.get("message") or "upstream error")
                break
    except (UpstreamProtocolError, httpx.HTTPError) as exc:
        translator.error = f"upstream stream failed ({type(exc).__name__})"
    finally:
        await resp.aclose()
        await client.aclose()
    if not terminal and not translator.error:
        translator.error = "upstream stream ended before a terminal response"
    if translator.error:
        return {
            "error": {"message": translator.error, "type": "api_error", "code": 502},
            "_fuse_usage": translator.usage,
        }
    message: dict = {"role": "assistant", "content": "".join(text_parts)}
    if reasoning_parts:
        message["reasoning_content"] = "".join(reasoning_parts)
    calls = translator.book.tool_calls()
    if calls:
        message["tool_calls"] = calls
    finish = "tool_calls" if calls else "stop"
    body = {
        "id": translator.completion_id,
        "object": "chat.completion",
        "created": translator.created,
        "model": model,
        "choices": [{"index": 0, "message": message, "finish_reason": finish}],
    }
    if translator.usage:
        body["usage"] = dict(translator.usage)
    return body
