"""Provider-stream plumbing: SSE framing, client frame shaping, and
wait pacing with liveness beats.

_paced_chunks gives the model a generous idle budget until it produces
output, and while a source says it is thinking; once output has flowed it
aborts fast. Keep-alives do not reset it.
_with_beats / _heartbeat_sleep keep a silent wait observable."""

from __future__ import annotations

import asyncio
import contextlib
import json
import time

from ..sources.base import THINKING_MARK
from .parsing import strip_special_tokens


HEARTBEAT_S = 2.0


# No output from the provider for this long before the first output, or
# inside a think a source marked (THINKING_MARK) → treat as a resumable
# stall. Matches Grok CLI's inference idle timeout; slow reasoning models
# legitimately think for minutes on large prompts, so this budget stays
# generous. Tool execution is not gated.
IDLE_TIMEOUT_S = 600.0
# Once output has flowed, a silent gap this long means the stream is dead —
# abort early and let the resume machinery retry instead of burning minutes.
CHUNK_GAP_TIMEOUT_S = 120.0
# While waiting on the provider longer than this, emit liveness beats so a
# live-but-silent round is distinguishable from a hung one in the UI.
PROVIDER_BEAT_S = 5.0


_SENTINEL = object()


def _sse(obj: dict) -> bytes:
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n".encode()


def _client_obj(obj: dict, *, hide_tool_calls: bool = True) -> dict | None:
    if obj.get("error") or obj.get("fuse"):
        return obj
    choices = []
    for ch in obj.get("choices") or []:
        if not isinstance(ch, dict):
            continue
        ch = dict(ch)
        delta = ch.get("delta")
        message = ch.get("message")
        if isinstance(delta, dict):
            if hide_tool_calls:
                delta = {k: v for k, v in delta.items() if k != "tool_calls"}
            else:
                delta = dict(delta)
            if isinstance(delta.get("content"), str):
                delta["content"] = strip_special_tokens(delta["content"])
            ch["delta"] = delta
        if isinstance(message, dict):
            if hide_tool_calls:
                message = {k: v for k, v in message.items() if k != "tool_calls"}
            else:
                message = dict(message)
            if isinstance(message.get("content"), str):
                message["content"] = strip_special_tokens(message["content"])
            ch["message"] = message
        if hide_tool_calls and ch.get("finish_reason") == "tool_calls":
            ch["finish_reason"] = None
        choices.append(ch)
    out = {k: v for k, v in obj.items() if k != "choices"}
    out["choices"] = choices
    if not choices and not out.get("usage") and "error" not in out:
        return None
    return out


def _parse_sse_line(text: str):
    text = text.strip()
    if not text.startswith("data:"):
        return None
    data = text[5:].strip()
    if not data or data == "[DONE]":
        return data
    try:
        obj = json.loads(data)
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


def _chunk_output(payload: bytes, classify: bool) -> str:
    """Kind of one `data:` payload: "envelope", "reasoning" or "output".

    An envelope is a chat chunk with no model output (the opening role delta,
    empty deltas, usage alone): bytes, not progress. Anything unparseable or
    not a chat chunk counts as output. Without classify every payload is
    output (no parse once it no longer matters).
    """
    if not classify:
        return "output"
    try:
        obj = json.loads(payload)
    except (ValueError, UnicodeDecodeError):
        return "output"
    if not isinstance(obj, dict) or obj.get("error") or obj.get("fuse"):
        return "output"
    choices = obj.get("choices")
    if not isinstance(choices, list):
        return "output"
    kind = "envelope"
    for choice in choices:
        if not isinstance(choice, dict) or choice.get("finish_reason") or choice.get("message"):
            return "output"
        delta = choice.get("delta") if choice.get("delta") is not None else {}
        if not isinstance(delta, dict):
            return "output"
        for key, value in delta.items():
            if key == "role" or value in (None, "", [], {}):
                continue
            if key in ("reasoning_content", "reasoning"):
                kind = "reasoning"
                continue
            return "output"
    return kind


def _sse_progress(carry: bytearray, chunk: bytes, classify: bool = True) -> list[str]:
    """Kinds of the SSE lines chunk completes, in order.

    "thinking" is a source's THINKING_MARK comment; data frames are
    "envelope", "reasoning" or "output" (see _chunk_output). Other comments
    (`: ping`), blank lines and keep-alives are skipped. A split `data:`
    line only counts once its newline arrives.
    """
    carry.extend(chunk)
    kinds: list[str] = []
    while True:
        nl = carry.find(b"\n")
        if nl < 0:
            if len(carry) > 65_536:
                # Keep the head too: its `data:` prefix decides the line.
                del carry[4_096: len(carry) - 4_096]
            return kinds
        line = bytes(carry[:nl]).strip()
        del carry[: nl + 1]
        if line == THINKING_MARK:
            kinds.append("thinking")
        elif line.startswith(b"data:") and line[5:].strip():
            kinds.append(_chunk_output(line[5:].strip(), classify))


async def _paced_chunks(stream, first_s: float, gap_s: float, beat_s: float):
    """Yield ("chunk", bytes) and ("beat", waited_s) from a provider stream.

    Until the model produces output (envelope frames do not count), and while
    a source marks it as thinking, silence gets first_s of patience:
    legitimate slow thinking on large prompts. Once output has flowed, a gap
    of gap_s without another data frame means the stream is dead — abort
    early and let the resume retries recover. Reasoning text inside a marked
    think keeps the patient budget; answer text or a tool call ends it. SSE
    comments / keep-alives do not reset the idle timer. While waiting longer
    than beat_s, emit beats so the run row and UI show liveness.
    """
    ait = stream.__aiter__()
    nxt: asyncio.Future = asyncio.ensure_future(ait.__anext__())
    waited = 0.0
    seen_any = False
    thinking = False
    carry = bytearray()
    try:
        while True:
            limit = first_s if not seen_any or thinking else gap_s
            budget = limit - waited
            if budget <= 0:
                raise TimeoutError("upstream idle timeout")
            slice_s = min(budget, beat_s) if beat_s > 0 else budget
            t0 = time.monotonic()
            done, _pending = await asyncio.wait({nxt}, timeout=slice_s)
            elapsed = max(0.0, time.monotonic() - t0)
            if done:
                try:
                    chunk = nxt.result()
                except StopAsyncIteration:
                    return
                raw = chunk.encode() if isinstance(chunk, str) else chunk
                yield ("chunk", raw)
                progressed = False
                for kind in _sse_progress(carry, raw, classify=not seen_any or thinking):
                    if kind == "thinking":
                        thinking = progressed = True
                    elif kind == "reasoning":
                        seen_any = progressed = True
                    elif kind == "output":
                        seen_any = progressed = True
                        thinking = False
                if progressed:
                    waited = 0.0
                else:
                    waited += elapsed
                    limit = first_s if not seen_any or thinking else gap_s
                    if waited >= limit - 1e-9:
                        raise TimeoutError("upstream idle timeout")
                nxt = asyncio.ensure_future(ait.__anext__())
            else:
                waited += elapsed if elapsed else slice_s
                if waited >= limit - 1e-9:
                    raise TimeoutError("upstream idle timeout")
                yield ("beat", waited)
    finally:
        if not nxt.done():
            # Cancel without awaiting: awaiting a cancelled task here would
            # raise CancelledError out of the generator and mask the
            # TimeoutError the caller needs to see.
            nxt.cancel()


async def _with_beats(awaitable):
    """Yield ("beat", None) while awaitable runs; then ("ok", result) or ("err", exc)."""
    task = asyncio.ensure_future(awaitable)
    try:
        while True:
            done, _pending = await asyncio.wait({task}, timeout=PROVIDER_BEAT_S)
            if not done:
                yield ("beat", None)
                continue
            try:
                yield ("ok", task.result())
            except Exception as exc:
                yield ("err", exc)
            return
    finally:
        if not task.done():
            task.cancel()
        with contextlib.suppress(Exception, asyncio.CancelledError):
            await task


async def _heartbeat_sleep(delay: float):
    """Sleep, yielding heartbeat SSE frames so a retry wait is not a silent stall."""
    deadline = time.monotonic() + max(0.0, float(delay))
    while True:
        left = deadline - time.monotonic()
        if left <= 0:
            return
        slice_s = min(left, PROVIDER_BEAT_S)
        await asyncio.sleep(slice_s)
        if deadline - time.monotonic() > 1e-9:
            yield _sse({"fuse": {"event": "heartbeat"}})


async def _terminated_chunks(stream):
    """Process an EOF-terminated SSE line just like a newline-terminated line."""
    async for chunk in stream:
        yield chunk.encode() if isinstance(chunk, str) else chunk
    yield b"\n"
