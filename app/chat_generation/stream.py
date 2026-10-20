"""Provider-stream plumbing: SSE framing, client frame shaping, and
wait pacing with liveness beats.

_paced_chunks decides when a silent provider stream is dead. It tracks what
the model is doing from the frames themselves (waiting for the first output,
thinking, writing a tool call, writing the reply) and gives each phase the
patience that phase legitimately needs; keep-alives a source vouches for
(ALIVE_MARK) stretch any phase up to a ceiling. Other keep-alives do not
count. _with_beats / _heartbeat_sleep keep a silent wait observable."""

from __future__ import annotations

import asyncio
import contextlib
import json
import time

from ..sources.base import ALIVE_MARK, THINKING_MARK
from .parsing import strip_special_tokens


HEARTBEAT_S = 2.0


# Silent-work phases: before the first output, inside a think a source
# marked (THINKING_MARK), and while a tool call is being written. Slow
# reasoning models think for minutes on large prompts, and providers may
# hold a tool argument until it is complete (a file body can take minutes),
# so no output for this long is the only sign of a stall there.
IDLE_TIMEOUT_S = 600.0
# The reply text was flowing and nothing is open. A gap this long means the
# stream is dead. Still generous: providers that hide reasoning or build a
# whole tool call server-side go quiet mid-reply too, and the providers'
# 300s socket read timeout covers a connection that went fully silent.
CHUNK_GAP_TIMEOUT_S = 300.0
# While a source keeps relaying keep-alives the provider only sends for a
# request it is still working on (ALIVE_MARK), the stream is alive in any
# phase; this caps how long it may go without output anyway.
ALIVE_CEILING_S = 1800.0
# While waiting on the provider longer than this, emit liveness beats so a
# live-but-silent round is distinguishable from a hung one in the UI.
PROVIDER_BEAT_S = 5.0


class StreamStalled(TimeoutError):
    """No output (and no vouched keep-alive) for the phase's budget."""

    def __init__(self, phase: str, quiet_s: float, unheard_s: float):
        super().__init__("upstream idle timeout")
        self.phase = phase
        # Since the last output, and since the last output or keep-alive.
        self.quiet_s = quiet_s
        self.unheard_s = unheard_s


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


def _chunk_output(payload: bytes) -> str:
    """Kind of one `data:` payload: "envelope", "reasoning", "tool" or "output".

    An envelope is a chat chunk with no model output (the opening role delta,
    empty deltas, usage alone): bytes, not progress. "tool" is a tool call
    being written (its arguments may still be open). Anything unparseable or
    not a chat chunk counts as output, except a bounded tool-call line.
    """
    # A huge line is bounded to its head and tail by _sse_progress, or not
    # worth parsing twice (consume parses it too); its head still says
    # whether it is a tool-call frame.
    head_only = "tool" if b'"tool_calls"' in payload[:4096] else "output"
    if len(payload) > 65_536:
        return head_only
    try:
        obj = json.loads(payload)
    except (ValueError, UnicodeDecodeError):
        return head_only
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
                kind = "reasoning" if kind == "envelope" else kind
                continue
            if key == "tool_calls":
                kind = "tool"
                continue
            return "output"
    return kind


def _sse_progress(carry: bytearray, chunk: bytes) -> list[str]:
    """Kinds of the SSE lines chunk completes, in order.

    "thinking" and "alive" are a source's THINKING_MARK and ALIVE_MARK
    comments; data frames are "envelope", "reasoning", "tool" or "output"
    (see _chunk_output). Other comments (`: ping`), blank lines and
    keep-alives are skipped. A split `data:` line only counts once its
    newline arrives.
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
        elif line == ALIVE_MARK:
            kinds.append("alive")
        elif line.startswith(b"data:") and line[5:].strip():
            kinds.append(_chunk_output(line[5:].strip()))


async def _paced_chunks(stream, first_s: float, gap_s: float, beat_s: float, ceiling_s: float = ALIVE_CEILING_S):
    """Yield ("chunk", bytes) and ("beat", waited_s) from a provider stream.

    The frames say what the model is doing, and each phase gets its own
    patience before a silence counts as a stall:

    - waiting (no output yet; envelope frames do not count), thinking (a
      source's THINKING_MARK until answer text or a tool call) and tool (a
      tool call is being written; providers may hold an argument until it
      is complete): first_s without output.
    - text (reply or unmarked reasoning text was flowing): gap_s.

    An ALIVE_MARK (a keep-alive the provider only sends while it is still
    working) restarts the phase's budget without counting as output; output
    must still arrive within ceiling_s. Raises StreamStalled. While waiting
    longer than beat_s, emit beats so the run row and UI show liveness.
    """
    ait = stream.__aiter__()
    nxt: asyncio.Future = asyncio.ensure_future(ait.__anext__())
    phase = "waiting"
    last_output = last_heard = time.monotonic()
    carry = bytearray()

    def stall_left(now: float) -> float:
        limit = gap_s if phase == "text" else first_s
        return min(last_heard + limit, last_output + ceiling_s) - now

    def stalled(now: float) -> StreamStalled:
        return StreamStalled(phase, now - last_output, now - last_heard)

    try:
        while True:
            now = time.monotonic()
            budget = stall_left(now)
            if budget <= 1e-9:
                raise stalled(now)
            slice_s = min(budget, beat_s) if beat_s > 0 else budget
            done, _pending = await asyncio.wait({nxt}, timeout=slice_s)
            now = time.monotonic()
            if done:
                try:
                    chunk = nxt.result()
                except StopAsyncIteration:
                    return
                raw = chunk.encode() if isinstance(chunk, str) else chunk
                yield ("chunk", raw)
                progressed = heard = False
                for kind in _sse_progress(carry, raw):
                    if kind == "alive":
                        heard = True
                    elif kind == "thinking":
                        phase, progressed = "thinking", True
                    elif kind == "reasoning":
                        phase = "thinking" if phase == "thinking" else "text"
                        progressed = True
                    elif kind == "tool":
                        phase, progressed = "tool", True
                    elif kind == "output":
                        phase, progressed = "text", True
                if progressed:
                    last_output = last_heard = now
                elif heard:
                    last_heard = now
                elif stall_left(now) <= 1e-9:
                    raise stalled(now)
                nxt = asyncio.ensure_future(ait.__anext__())
            else:
                if stall_left(now) <= 1e-9:
                    raise stalled(now)
                yield ("beat", now - last_output)
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
