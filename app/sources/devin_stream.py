"""Devin response translation: Cascade stream frames → OpenAI chunks/completions.

Splits Connect frames into decoded messages, assembles token-delta tool calls
(`_ToolBook`), folds streamed usage frames, and emits OpenAI-style SSE. The
stop_reason handling follows the live backend: several internal segments can
share one completion, so only a stop with no output after it is terminal, and
STOP_MAX_TOKENS is a normal end of turn (never `length`).
"""
from __future__ import annotations

import json
import os as _os
import time
import uuid

from ..proxy import usage_from_obj
from . import devin_proto as proto
from .devin_catalog import canonical_fuse_id, fuse_id, note_sku_failure, sku_error_kind

_USAGE_FIELDS = ("prompt_tokens", "completion_tokens", "cached_tokens", "cache_write_tokens")


def _merge_usage_frame(acc: dict, frame: dict) -> dict:
    """Fold one streamed usage frame into the running per-request usage.

    Devin can send usage on more than one frame, and a later frame may carry
    only part of the counts (e.g. output with a zero or partial prompt).
    Replacing the running value with the last frame dropped the prompt and
    cache reads; keeping each field's maximum is exact for cumulative
    snapshots and never smaller than last-frame-wins for anything else.
    """
    merged = dict(acc)
    for key in _USAGE_FIELDS:
        merged[key] = max(int(merged.get(key) or 0), int(frame.get(key) or 0))
    if frame.get("model_uid"):
        merged["model_uid"] = frame["model_uid"]
    return merged


def _openai_usage(u: dict | None) -> dict:
    if not u:
        return {}
    prompt = int(u.get("prompt_tokens") or 0)
    completion = int(u.get("completion_tokens") or 0)
    cached = int(u.get("cached_tokens") or 0)
    write = int(u.get("cache_write_tokens") or 0)
    obj = {
        "usage": {
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "total_tokens": prompt + completion,
            "prompt_tokens_details": {"cached_tokens": cached, "cache_write_tokens": write},
        }
    }
    return usage_from_obj(obj)


def _sse(obj: dict) -> bytes:
    return f"data: {json.dumps(obj, separators=(',', ':'))}\n\n".encode()


def _completion_id() -> str:
    return "chatcmpl-" + uuid.uuid4().hex[:24]


def _devin_debug() -> bool:
    return _os.environ.get("FUSE_DEVIN_DEBUG", "0") == "1"


def _debug_tool_call(cid: str, msg: dict, tc: dict) -> None:
    """Stream capture: one line per decoded tool_call chunk, in arrival order."""
    try:
        args = str(tc.get("arguments_json") or "")
        line = json.dumps(
            {
                "cid": cid[-8:],
                "mid": str(msg.get("message_id") or "")[-8:],
                "tid": str(tc.get("id") or "")[:12],
                "name": tc.get("name") or "",
                "len": len(args),
                "head": args[:48],
                "tail": args[-32:],
                "stop": msg.get("stop_reason") or 0,
                "text": len(str(msg.get("delta_text") or "")),
            },
            ensure_ascii=False,
        )
        with open("/root/fuse/data/devin-stream-debug.log", "a") as fh:
            fh.write(line + "\n")
    except Exception:
        pass


def _merge_tool_args(prev: str, incoming: str) -> str:
    """Append token deltas; only complete object snapshots can supersede them.

    Prefix overlap alone is not evidence of redelivery: braces and repeated
    tokens inside executable strings must survive unchanged.
    """
    if not incoming:
        return prev
    if not prev:
        return incoming
    if _json_object(incoming) is not None and (
        _json_object(prev) is not None or incoming.startswith(prev)
    ):
        return incoming
    return prev + incoming


def _json_object(raw: str) -> dict | None:
    try:
        parsed = json.loads(raw or "")
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    return parsed if isinstance(parsed, dict) else None


class _ToolBook:
    def __init__(self) -> None:
        self.order: list[str] = []
        self.calls: dict[str, dict] = {}
        # Wire id -> key of the call it currently names. A reused id moves to
        # the newest call; the earlier call keeps its own key in calls/order.
        self.alias: dict[str, str] = {}

    def _new(self, tid: str, name: str, incoming: str) -> dict:
        key = tid or name or f"call_{len(self.order)}"
        rec = {
            "id": tid or key,
            "name": name,
            "arguments": incoming,
            "index": len(self.order),
            "key": key,
        }
        self.calls[key] = rec
        if tid and tid != key:
            self.alias[tid] = key
        self.order.append(key)
        return rec

    def _merge_into(self, rec: dict, tid: str, name: str, incoming: str) -> tuple[dict, str]:
        if tid:
            self.alias[tid] = rec["key"]
            if not rec["id"]:
                rec["id"] = tid
        if name and not rec["name"]:
            rec["name"] = name
        merged = _merge_tool_args(rec["arguments"], incoming)
        delta = merged[len(rec["arguments"]) :] if merged.startswith(rec["arguments"]) else merged
        rec["arguments"] = merged
        return rec, delta

    def add(self, tc: dict) -> tuple[dict, str]:
        tid = str(tc.get("id") or "").strip()
        name = str(tc.get("name") or "").strip()
        incoming = str(tc.get("arguments_json") or tc.get("invalid_json") or "")
        if _devin_debug():
            self._debug_decision("in", tid, name, incoming)
        current = self.alias.get(tid, tid)
        if tid and current in self.calls:
            existing = self.calls[current]
            # The server reuses one tool_call id for every call of that tool.
            # A tid'd frame with empty args after a COMPLETED record is the
            # opening frame of the NEXT call — not a redelivery of this one.
            if not incoming and _json_object(existing["arguments"]) is not None:
                rec = self._new(self._next_key(tid), name, incoming)
                # Later argument frames carrying this id belong to the new call.
                self.alias[tid] = rec["key"]
                if _devin_debug():
                    self._debug_decision("newcall", tid, name, rec["arguments"])
                return rec, incoming
            rec, delta = self._merge_into(existing, tid, name, incoming)
            if _devin_debug():
                self._debug_decision("bytid", tid, name, rec["arguments"])
            return rec, delta
        if self.order:
            last = self.calls[self.order[-1]]
            last_done = _json_object(last["arguments"]) is not None
            same_name = bool(name) and bool(last["name"]) and name == last["name"]
            can_fill = (not last["name"] or not name or same_name) and not last_done
            if can_fill:
                rec, delta = self._merge_into(last, tid, name, incoming)
                if _devin_debug():
                    self._debug_decision("fill", tid, name, rec["arguments"])
                return rec, delta
        rec = self._new(tid, name, incoming)
        if _devin_debug():
            self._debug_decision("new", tid, name, rec["arguments"])
        return rec, incoming

    def _next_key(self, base: str) -> str:
        key = base
        n = 1
        while key in self.calls:
            key = f"{base}#{n}"
            n += 1
        return key

    def _debug_decision(self, action: str, tid: str, name: str, args: str) -> None:
        try:
            line = json.dumps(
                {"act": action, "tid": tid[:12], "name": name[:24], "len": len(args),
                 "head": args[:40], "tail": args[-24:]},
                ensure_ascii=False,
            )
            with open("/root/fuse/data/devin-stream-debug.log", "a") as fh:
                fh.write(line + "\n")
        except Exception:
            pass

    def openai_calls(self) -> list[dict]:
        seen: set[int] = set()
        out = []
        for key in self.order:
            rec = self.calls[key]
            if rec["index"] in seen or not rec["name"]:
                continue
            seen.add(rec["index"])
            args = rec["arguments"] or "{}"
            out.append(
                {
                    "index": len(out),
                    "id": rec["id"],
                    "type": "function",
                    "function": {"name": rec["name"], "arguments": args},
                }
            )
        return out


def _chunk(cid: str, created: int, model: str, delta: dict, finish=None) -> bytes:
    return _sse(
        {
            "id": cid,
            "object": "chat.completion.chunk",
            "created": created,
            "model": canonical_fuse_id(model) or fuse_id(model),
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
        }
    )


def _connect_error(payload: bytes) -> str:
    if not payload.strip():
        return ""
    try:
        trailer = json.loads(payload)
    except (ValueError, UnicodeDecodeError):
        return "invalid Devin Connect trailer"
    if not isinstance(trailer, dict):
        return "invalid Devin Connect trailer"
    error = trailer.get("error")
    if error is not None:
        if not isinstance(error, dict) or not error.get("code"):
            return "invalid Devin Connect trailer error"
        return str(error.get("message") or error["code"])
    return ""


async def _stream_sse(resp, client, collected: dict, model: str, uid: str = "", hints: dict | None = None):
    cid = _completion_id()
    created = int(time.time())
    pending = b""
    thinking = ""
    signature = ""
    signature_type = ""
    output_id = ""
    book = _ToolBook()
    stop = 0
    stop_terminal = False
    ended = False
    raw_usage: dict = {}
    try:
        async for chunk in resp.aiter_bytes():
            pending += chunk
            frames, pending = proto.iter_connect_frames(pending)
            for flag, payload in frames:
                if flag & proto.CONNECT_END_STREAM:
                    ended = True
                    error = _connect_error(payload)
                    if error:
                        collected["stream_status"] = "failed"
                        collected["trailer_error"] = error
                        kind = sku_error_kind(error)
                        if kind:
                            note_sku_failure(uid, kind)
                        yield _sse({"error": {"message": error}})
                        return
                    continue
                msg = proto.decode_get_chat_message(payload)
                if msg.get("delta_thinking"):
                    if stop:
                        # Devin streams several internal message segments
                        # per completion; a stop_reason that is followed
                        # by more output was a segment boundary, not the
                        # end of the response.
                        stop_terminal = False
                    thinking += msg["delta_thinking"]
                    yield _chunk(
                        cid, created, model, {"reasoning_content": msg["delta_thinking"]}
                    )
                if msg.get("delta_signature"):
                    signature += msg["delta_signature"]
                if msg.get("delta_signature_type"):
                    signature_type = msg["delta_signature_type"]
                if msg.get("output_id"):
                    output_id = msg["output_id"]
                if msg.get("delta_text"):
                    if stop:
                        stop_terminal = False
                    yield _chunk(cid, created, model, {"content": msg["delta_text"]})
                for tc in msg.get("tool_calls") or []:
                    if stop:
                        stop_terminal = False
                    if _devin_debug():
                        _debug_tool_call(cid, msg, tc)
                    rec, _ = book.add(tc)
                    if hints is not None and rec.get("name") and hints.get("tool_call") != rec["name"]:
                        # Calls are only emitted whole at the end; tell the
                        # consumer which tool is being written now, and wake
                        # it with an empty delta (valid for /v1 clients too).
                        hints["tool_call"] = rec["name"]
                        yield _chunk(cid, created, model, {})
                if msg.get("usage"):
                    raw_usage = _merge_usage_frame(raw_usage, msg["usage"])
                    collected.update(_openai_usage(raw_usage))
                if msg.get("stop_reason"):
                    stop = msg["stop_reason"]
                    stop_terminal = True
        if pending or not (ended or stop_terminal):
            collected["stream_status"] = "failed"
            yield _sse({"error": {"message": "incomplete Devin Connect response"}})
            return
        calls = book.openai_calls()
        terminal_stop = stop if stop_terminal else 0
        # STOP_MAX_TOKENS is NOT a truncation signal on this backend:
        # production turns end terminally with stop=3 at 98-3000
        # completion tokens and complete content. Only a function-call
        # stop carries meaning; everything else is a normal stop.
        finish = (
            "tool_calls"
            if calls or terminal_stop == proto.STOP_FUNCTION_CALL
            else "stop"
        )
        if calls:
            yield _chunk(
                cid,
                created,
                model,
                {
                    "tool_calls": [
                        {
                            "index": call["index"],
                            "id": call["id"],
                            "type": "function",
                            "function": {
                                "name": call["function"]["name"],
                                "arguments": call["function"]["arguments"],
                            },
                        }
                        for call in calls
                    ]
                },
            )
        usage_obj = {
            "prompt_tokens": collected.get("prompt_tokens") or 0,
            "completion_tokens": collected.get("completion_tokens") or 0,
            "total_tokens": collected.get("total_tokens")
            or (collected.get("prompt_tokens") or 0) + (collected.get("completion_tokens") or 0),
            "prompt_tokens_details": {
                "cached_tokens": collected.get("cached_tokens") or 0,
                "cache_write_tokens": collected.get("cache_write_tokens") or 0,
            },
        }
        # Only mark usage complete when Devin actually reported usage: a
        # stream that never sent a usage frame is unknown, not a complete zero.
        if raw_usage:
            collected.setdefault("usage_status", "complete")
        collected["stream_status"] = "complete"
        final_delta: dict = {}
        if signature:
            final_delta["devin_signature"] = signature
        if signature_type:
            final_delta["devin_signature_type"] = signature_type
        if output_id:
            final_delta["devin_output_id"] = output_id
        yield _sse(
            {
                "id": cid,
                "object": "chat.completion.chunk",
                "created": created,
                "model": canonical_fuse_id(model) or fuse_id(model),
                "choices": [{"index": 0, "delta": final_delta, "finish_reason": finish}],
                "usage": usage_obj,
            }
        )
        yield b"data: [DONE]\n\n"
    except Exception:
        collected["stream_status"] = "failed"
        raise
    finally:
        await resp.aclose()
        await client.aclose()


async def _collect_completion(resp, model: str) -> tuple[dict, dict, str]:
    pending = b""
    text = ""
    thinking = ""
    signature = ""
    signature_type = ""
    output_id = ""
    book = _ToolBook()
    stop = 0
    stop_terminal = False
    ended = False
    usage: dict = {}
    raw_usage: dict = {}
    trailer_error = ""
    async for chunk in resp.aiter_bytes():
        pending += chunk
        frames, pending = proto.iter_connect_frames(pending)
        for flag, payload in frames:
            if flag & proto.CONNECT_END_STREAM:
                ended = True
                # Connect end-stream trailers carry structured errors
                # ("an internal error occurred (trace ID: ...)", version
                # gates). Swallowing them made failed completions look
                # like empty 200s.
                trailer_error = trailer_error or _connect_error(payload)
                continue
            msg = proto.decode_get_chat_message(payload)
            if msg.get("delta_text"):
                if stop:
                    # Intermediate segment stop — see _stream_sse.
                    stop_terminal = False
                text += msg["delta_text"]
            if msg.get("delta_thinking"):
                if stop:
                    stop_terminal = False
                thinking += msg["delta_thinking"]
            signature += msg.get("delta_signature") or ""
            if msg.get("delta_signature_type"):
                signature_type = msg["delta_signature_type"]
            if msg.get("output_id"):
                output_id = msg["output_id"]
            for tc in msg.get("tool_calls") or []:
                if stop:
                    stop_terminal = False
                book.add(tc)
            if msg.get("usage"):
                raw_usage = _merge_usage_frame(raw_usage, msg["usage"])
                usage = _openai_usage(raw_usage)
            if msg.get("stop_reason"):
                stop = msg["stop_reason"]
                stop_terminal = True
    if pending or not (ended or stop_terminal):
        trailer_error = trailer_error or "incomplete Devin Connect response"
    if trailer_error:
        return {"error": {"message": trailer_error}}, usage, trailer_error
    terminal_stop = stop if stop_terminal else 0
    # See _stream_sse: STOP_MAX_TOKENS terminally is a normal end of turn.
    finish = (
        "tool_calls"
        if book.order or terminal_stop == proto.STOP_FUNCTION_CALL
        else "stop"
    )
    message = {"role": "assistant", "content": text or None}
    if thinking:
        message["reasoning_content"] = thinking
    if signature:
        message["devin_signature"] = signature
    if signature_type:
        message["devin_signature_type"] = signature_type
    if output_id:
        message["devin_output_id"] = output_id
    calls = book.openai_calls()
    if calls:
        message["tool_calls"] = calls
    obj = {
        "id": _completion_id(),
        "object": "chat.completion",
        "created": int(time.time()),
        "model": canonical_fuse_id(model) or fuse_id(model),
        "choices": [{"index": 0, "message": message, "finish_reason": finish}],
        "usage": {
            "prompt_tokens": usage.get("prompt_tokens") or 0,
            "completion_tokens": usage.get("completion_tokens") or 0,
            "total_tokens": usage.get("total_tokens")
            or (usage.get("prompt_tokens") or 0) + (usage.get("completion_tokens") or 0),
        },
    }
    return obj, usage or usage_from_obj(obj), trailer_error
