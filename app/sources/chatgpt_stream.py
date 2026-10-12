"""ChatGPT stream protocol and translation: Codex `/responses` SSE → OpenAI chunks.

Parses `data:` JSON events of the Responses stream and translates output
items (text, reasoning, function_call) into OpenAI-style `chat.completion.chunk`
SSE, plus non-stream error-body translation for the chat loop.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time

import httpx

from ..proxy import usage_from_obj

logger = logging.getLogger(__name__)


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


# One SSE line; the final response event carries the whole output.
SSE_LINE_MAX = 32 * 1024 * 1024


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
        # A peer that never ends a line would otherwise grow buf forever.
        if len(buf) > SSE_LINE_MAX:
            raise UpstreamProtocolError("upstream sent an oversized event")
    if buf.strip():
        obj = parse(buf)
        if obj is not None:
            yield obj


class _CallBook:
    def __init__(self):
        self.order: list[str] = []
        self.calls: dict[str, dict] = {}

    def add(self, item_id: str, call_id: str, name: str, arguments: str, output_index=None) -> dict:
        rec = {
            "index": len(self.order),
            "id": call_id,
            "name": name,
            "arguments": arguments,
            "output_index": output_index,
        }
        # Two items without an id must not share one record.
        key = item_id or f"_call{len(self.order)}"
        self.calls[key] = rec
        self.order.append(key)
        return rec

    def get(self, item_id: str) -> dict | None:
        return self.calls.get(item_id)

    def find(self, event: dict) -> dict | None:
        """The call an argument or item event belongs to: by item id, else by
        the call id or output index the event carries. Parallel calls whose
        events named another id used to land nowhere, and the model's calls
        ran with empty arguments."""
        item = event.get("item") if isinstance(event.get("item"), dict) else {}
        rec = self.calls.get(str(event.get("item_id") or item.get("id") or ""))
        if rec:
            return rec
        call_id = str(item.get("call_id") or event.get("call_id") or "")
        index = event.get("output_index")
        for rec in self.calls.values():
            if call_id and rec["id"] == call_id:
                return rec
        for rec in self.calls.values():
            if index is not None and rec["output_index"] == index:
                return rec
        return None

    def tool_calls(self) -> list[dict]:
        return [
            {
                "id": self.calls[i]["id"],
                "type": "function",
                "function": {
                    "name": self.calls[i]["name"],
                    "arguments": self.calls[i]["arguments"] or "{}",
                },
            }
            for i in self.order
            if self.calls[i]["name"]
        ]


def reasoning_block(item, account=None) -> dict | None:
    """A finished Responses reasoning item as a replayable thinking block.

    store=false keeps no server state, so the model's reasoning between tool
    calls survives only if its encrypted item is sent back. It decrypts only
    for the account that produced it; the block records that account.
    """
    if not isinstance(item, dict) or item.get("type") != "reasoning":
        return None
    encrypted = item.get("encrypted_content")
    if not isinstance(encrypted, str) or not encrypted:
        return None
    summary = [
        {"type": "summary_text", "text": str(part.get("text") or "")}
        for part in item.get("summary") or []
        if isinstance(part, dict) and part.get("text")
    ]
    block = {"encrypted_content": encrypted, "summary": summary}
    if account is not None:
        block["account"] = int(account)
    return {"openai_reasoning": block}


class _Translator:
    def __init__(self, model: str, account=None):
        self.model = model
        self.account = account
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

    def _settle(self, rec: dict, final) -> bytes | None:
        """Make the streamed arguments match the call's final arguments.

        Deltas are not the only carrier: the final text also arrives on
        function_call_arguments.done and output_item.done, and some parallel
        calls stream no deltas at all. Whatever the deltas left out is sent
        now, as one more delta."""
        if final is None:
            return None
        final = str(final)
        sent = rec["arguments"]
        if not final:
            if sent:
                return None
            # Zero-argument call: clients still need valid JSON.
            rec["arguments"] = "{}"
            return self._chunk({"tool_calls": [{"index": rec["index"], "function": {"arguments": "{}"}}]})
        if final == sent:
            return None
        if not final.startswith(sent):
            logger.warning("chatgpt call %s: streamed arguments disagree with the final ones; keeping the streamed text", rec["id"])
            return None
        if sent == "" and rec["name"]:
            logger.info("chatgpt call %s (%s): %d argument chars arrived only with the finished call", rec["id"], rec["name"], len(final))
        rec["arguments"] = final
        return self._chunk({"tool_calls": [{"index": rec["index"], "function": {"arguments": final[len(sent):]}}]})

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
                    event.get("output_index"),
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
        if etype == "response.output_item.done":
            item = event.get("item") if isinstance(event.get("item"), dict) else {}
            if item.get("type") == "function_call":
                rec = self.book.find(event)
                return self._settle(rec, item.get("arguments")) if rec else None
            block = reasoning_block(item, self.account)
            return self._chunk({"claude_thinking": [block]}) if block else None
        if etype == "response.function_call_arguments.delta":
            rec = self.book.find(event)
            if rec and event.get("delta"):
                rec["arguments"] += str(event.get("delta"))
                return self._chunk(
                    {"tool_calls": [{"index": rec["index"], "function": {"arguments": str(event.get("delta"))}}]}
                )
            return None
        if etype == "response.function_call_arguments.done":
            rec = self.book.find(event)
            return self._settle(rec, event.get("arguments")) if rec else None
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
    resp: httpx.Response, client: httpx.AsyncClient, model: str, collected: dict | None = None, account=None
):
    translator = _Translator(model, account)
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
            # A cancelled stream is neither complete nor failed: it must not
            # cooldown the credential (see _bind_completed_stream).
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


async def _aggregate(resp: httpx.Response, client: httpx.AsyncClient, model: str, account=None) -> dict:
    translator = _Translator(model, account)
    text_parts: list[str] = []
    reasoning_parts: list[str] = []
    thinking: list[dict] = []
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
                        event.get("output_index"),
                    )
            elif etype == "response.output_item.done":
                item = event.get("item") if isinstance(event.get("item"), dict) else {}
                if item.get("type") == "function_call":
                    rec = translator.book.find(event)
                    if rec and item.get("arguments"):
                        rec["arguments"] = str(item.get("arguments"))
                    continue
                block = reasoning_block(item, account)
                if block:
                    thinking.append(block)
            elif etype == "response.function_call_arguments.delta":
                rec = translator.book.find(event)
                if rec and event.get("delta"):
                    rec["arguments"] += str(event.get("delta"))
            elif etype == "response.function_call_arguments.done":
                rec = translator.book.find(event)
                if rec and event.get("arguments") is not None:
                    rec["arguments"] = str(event.get("arguments") or "") or rec["arguments"] or "{}"
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
    if thinking:
        message["claude_thinking"] = thinking
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
