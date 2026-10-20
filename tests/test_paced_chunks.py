"""Provider-wait pacing: split idle windows + liveness beats.

Incident: a Devin child stalled silently mid-round for ~10 minutes. The flat
600s idle window could not distinguish "thinking on a huge prompt" (needs
patience before the first byte) from "stream dropped after tokens flowed"
(needs a fast abort), and provider waits emitted no events at all — so the
run looked hung while it said "running".
"""

from __future__ import annotations

import asyncio
import os
import sys
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_TMP = Path(os.environ.get("TMPDIR", "/tmp")) / "fuse-paced"
os.environ.setdefault("DATABASE_PATH", str(_TMP / "paced.db"))
os.environ.setdefault("APP_SECRET", "x" * 32)

from app.chat_runtime import (  # noqa: E402
    CHUNK_GAP_TIMEOUT_S,
    IDLE_TIMEOUT_S,
    PROVIDER_BEAT_S,
    _paced_chunks,
)

import pytest

# Uses the database: a disposable MariaDB schema shared by this module.
pytestmark = pytest.mark.usefixtures("isolated_db")


def _stream(chunks, delays=None):
    """Async byte stream; delays[i] sleeps before yielding chunks[i]."""

    async def gen():
        for i, c in enumerate(chunks):
            if delays and delays[i]:
                await asyncio.sleep(delays[i])
            yield c

    return gen()


class PacedChunksTest(unittest.IsolatedAsyncioTestCase):
    async def test_normal_stream_passes_through_without_beats(self):
        out = []
        async for kind, payload in _paced_chunks(_stream([b"a", b"b", b"c"]), 5.0, 5.0, 5.0):
            out.append((kind, payload))
        self.assertEqual([p for k, p in out if k == "chunk"], [b"a", b"b", b"c"])
        self.assertFalse([p for k, p in out if k == "beat"])

    async def test_first_chunk_timeout_emits_beats_then_aborts(self):
        out = []
        with self.assertRaises(TimeoutError):
            async for item in _paced_chunks(_stream([b"x"], delays=[1.0]), 0.2, 5.0, 0.05):
                out.append(item)
        kinds = [k for k, _ in out]
        self.assertNotIn("chunk", kinds)
        self.assertGreater(kinds.count("beat"), 1)
        # Beats report accumulated wait, monotonically increasing.
        waits = [p for k, p in out if k == "beat"]
        self.assertEqual(waits, sorted(waits))
        self.assertLessEqual(waits[-1], 0.2 + 0.05)

    async def test_midstream_gap_aborts_on_gap_budget_not_first(self):
        out = []
        first = b'data: {"x":1}\n\n'
        with self.assertRaises(TimeoutError):
            async for item in _paced_chunks(_stream([first, b'data: {"x":2}\n\n'], delays=[0.0, 5.0]), 30.0, 0.15, 30.0):
                out.append(item)
        self.assertEqual([p for k, p in out if k == "chunk"], [first])
        # The abort came from the mid-stream gap, not the first-chunk budget.
        self.assertFalse([p for k, p in out if k == "beat"])

    async def test_sse_comments_do_not_reset_idle_timer(self):
        first = b'data: {"x":1}\n\n'
        pings = [b": ping\n\n", b": ping\n\n", b": ping\n\n", b": ping\n\n"]
        out = []
        with self.assertRaises(TimeoutError):
            async for item in _paced_chunks(
                _stream([first, *pings], delays=[0.0, 0.08, 0.08, 0.08, 0.08]),
                30.0,
                0.2,
                30.0,
            ):
                out.append(item)
        self.assertEqual([p for k, p in out if k == "chunk"][0], first)
        self.assertTrue(all(p.startswith(b":") or p == first for k, p in out if k == "chunk"))

    async def test_comments_only_use_first_byte_budget(self):
        out = []
        with self.assertRaises(TimeoutError):
            async for item in _paced_chunks(_stream([b": ping\n\n"], delays=[1.0]), 0.2, 5.0, 0.05):
                out.append(item)
        self.assertFalse([p for k, p in out if k == "chunk"])
        self.assertGreater([k for k, _ in out].count("beat"), 1)

    async def test_slow_first_chunk_within_budget_is_kept(self):
        out = []
        async for item in _paced_chunks(_stream([b"a"], delays=[0.12]), 30.0, 0.05, 0.03):
            out.append(item)
        self.assertIn(("chunk", b"a"), out)
        self.assertTrue(any(k == "beat" for k, _ in out))

    async def test_string_chunks_are_encoded(self):
        out = []
        async for item in _paced_chunks(_stream(["data: hi\n\n"]), 5.0, 5.0, 5.0):
            out.append(item)
        self.assertIn(("chunk", b"data: hi\n\n"), out)

    async def test_long_data_line_split_across_chunks_resets_idle(self):
        # A tool-call frame can be one SSE line well past the 64KB carry.
        # Its `data:` prefix arrives in the first chunk and its newline in a
        # later one; that completed line is progress, not silence.
        line = b"data: " + b"x" * 100_000 + b"\n"
        frames = [b"data: a\n\n", line[:70_000], line[70_000:], b'data: {"ok":1}\n\n']
        out = []
        async for item in _paced_chunks(_stream(frames, delays=[0.0, 0.3, 0.0, 0.3]), 30.0, 0.5, 30.0):
            out.append(item)
        self.assertEqual([p for k, p in out if k == "chunk"][-1], b'data: {"ok":1}\n\n')


    async def test_role_envelope_keeps_first_output_budget(self):
        # Incident: Claude's message_start became a role-only chunk, which
        # switched a 600s think to the 120s gap and killed it as dead.
        role = b'data: {"choices":[{"index":0,"delta":{"role":"assistant","content":""},"finish_reason":null}]}\n\n'
        text = b'data: {"choices":[{"delta":{"content":"hi"}}]}\n\n'
        out = []
        async for item in _paced_chunks(_stream([role, text], delays=[0.0, 0.3]), 30.0, 0.1, 30.0):
            out.append(item)
        self.assertEqual([p for k, p in out if k == "chunk"], [role, text])

    async def test_role_envelope_still_bounded_by_first_budget(self):
        role = b'data: {"choices":[{"delta":{"role":"assistant"}}]}\n\n'
        with self.assertRaises(TimeoutError):
            async for _item in _paced_chunks(_stream([role, b"data: x\n\n"], delays=[0.0, 1.0]), 0.2, 0.05, 30.0):
                pass

    async def test_thinking_mark_keeps_patience_until_answer_output(self):
        mark = b": fuse-thinking\n\n"
        reason = b'data: {"choices":[{"delta":{"reasoning_content":"plan"}}]}\n\n'
        text = b'data: {"choices":[{"delta":{"content":"hi"}}]}\n\n'
        late = b'data: {"choices":[{"delta":{"content":"!"}}]}\n\n'
        out = []
        with self.assertRaises(TimeoutError):
            # Silence after the mark and after a summary chunk outlasts the
            # gap budget but not the first one; after answer text it aborts.
            async for item in _paced_chunks(
                _stream([text, mark, reason, text, late], delays=[0.0, 0.0, 0.25, 0.25, 0.5]), 30.0, 0.15, 30.0
            ):
                out.append(item)
        self.assertEqual([p for k, p in out if k == "chunk"], [text, mark, reason, text])

    async def test_reasoning_without_mark_uses_gap_budget(self):
        reason = b'data: {"choices":[{"delta":{"reasoning_content":"plan"}}]}\n\n'
        with self.assertRaises(TimeoutError):
            async for _item in _paced_chunks(_stream([reason, reason], delays=[0.0, 0.5]), 30.0, 0.15, 30.0):
                pass

    def test_sse_progress_kinds(self):
        from app.chat_generation.stream import _sse_progress

        carry = bytearray()
        frames = (
            b": ping\n\n"
            b": fuse-thinking\n\n"
            b'data: {"choices":[{"delta":{"role":"assistant","content":""}}]}\n'
            b'data: {"choices":[],"usage":{"prompt_tokens":3}}\n'
            b'data: {"choices":[{"delta":{"reasoning_content":"r"}}]}\n'
            b'data: {"choices":[{"delta":{"content":"c"}}]}\n'
            b'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"{"}}]}}]}\n'
            b": fuse-alive\n\n"
            b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n'
            b'data: {"x":1}\n'
            b"data: [DONE]\n"
        )
        self.assertEqual(
            _sse_progress(carry, frames),
            ["thinking", "envelope", "envelope", "reasoning", "output", "tool", "alive", "output", "output", "output"],
        )
        # A tool-call line past the carry bound keeps its head: still a tool frame.
        huge = b'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"' + b"x" * 100_000 + b'"}}]}}]}\n'
        carry = bytearray()
        self.assertEqual(_sse_progress(carry, huge[:70_000]) + _sse_progress(carry, huge[70_000:]), ["tool"])

    async def test_open_tool_call_keeps_patience(self):
        # Incident (thread 596): Claude wrote "Now writing the scene", opened a
        # write_file call, and Anthropic held the file body until it was
        # complete. The 120s mid-reply gap killed the live call five times.
        text = b'data: {"choices":[{"delta":{"content":"Writing the scene."}}]}\n\n'
        head = b'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"t","function":{"name":"write_file","arguments":""}}]}}]}\n\n'
        body = b'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"{}"}}]}}]}\n\n'
        done = b'data: {"choices":[{"delta":{},"finish_reason":"tool_calls"}]}\n\n'
        out = [item async for item in _paced_chunks(
            _stream([text, head, body, done], delays=[0.0, 0.0, 0.35, 0.0]), 30.0, 0.15, 30.0)]
        self.assertEqual([p for k, p in out if k == "chunk"], [text, head, body, done])

    async def test_tool_call_silence_still_bounded_by_first_budget(self):
        from app.chat_generation.stream import StreamStalled

        head = b'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"name":"write_file"}}]}}]}\n\n'
        with self.assertRaises(StreamStalled) as caught:
            async for _item in _paced_chunks(_stream([head, b"data: x\n\n"], delays=[0.0, 1.0]), 0.2, 0.05, 30.0):
                pass
        self.assertEqual(caught.exception.phase, "tool")

    async def test_reply_text_after_tool_call_returns_to_gap_budget(self):
        from app.chat_generation.stream import StreamStalled

        head = b'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"name":"x"}}]}}]}\n\n'
        text = b'data: {"choices":[{"delta":{"content":"hi"}}]}\n\n'
        with self.assertRaises(StreamStalled) as caught:
            async for _item in _paced_chunks(_stream([head, text, text], delays=[0.0, 0.0, 0.5]), 30.0, 0.15, 30.0):
                pass
        self.assertEqual(caught.exception.phase, "text")

    async def test_alive_mark_restarts_budget_without_counting_as_output(self):
        # Anthropic's ping only comes while the message is still in progress:
        # a mid-reply silence longer than the gap survives while it arrives.
        text = b'data: {"choices":[{"delta":{"content":"hi"}}]}\n\n'
        alive = b": fuse-alive\n\n"
        out = [item async for item in _paced_chunks(
            _stream([text, alive, alive, alive, text], delays=[0.0, 0.1, 0.1, 0.1, 0.1]), 30.0, 0.15, 0.05)]
        self.assertEqual([p for k, p in out if k == "chunk"][-1], text)
        # Beats count from the last output, not from the last keep-alive.
        beats = [p for k, p in out if k == "beat"]
        self.assertTrue(beats and max(beats) > 0.15)

    async def test_alive_marks_cannot_outlast_the_ceiling(self):
        from app.chat_generation.stream import StreamStalled

        text = b'data: {"choices":[{"delta":{"content":"hi"}}]}\n\n'
        alive = b": fuse-alive\n\n"
        with self.assertRaises(StreamStalled) as caught:
            async for _item in _paced_chunks(
                _stream([text, *[alive] * 20], delays=[0.0, *[0.05] * 20]), 30.0, 0.15, 30.0, ceiling_s=0.3
            ):
                pass
        self.assertGreaterEqual(caught.exception.quiet_s, 0.3)
        self.assertLess(caught.exception.unheard_s, 0.15)

    async def test_alive_marks_stopping_aborts_one_budget_after_the_last(self):
        from app.chat_generation.stream import StreamStalled

        text = b'data: {"choices":[{"delta":{"content":"hi"}}]}\n\n'
        alive = b": fuse-alive\n\n"
        with self.assertRaises(StreamStalled) as caught:
            async for _item in _paced_chunks(
                _stream([text, alive, alive, text], delays=[0.0, 0.1, 0.1, 2.0]), 30.0, 0.15, 30.0
            ):
                pass
        self.assertEqual(caught.exception.phase, "text")
        self.assertGreaterEqual(caught.exception.unheard_s, 0.15)
        self.assertGreater(caught.exception.quiet_s, 0.3)

    def test_budgets(self):
        # Mid-reply silence was 120s, which killed live tool calls; the
        # providers' socket read timeout (300s) already catches a dead wire.
        self.assertEqual(IDLE_TIMEOUT_S, 600.0)
        self.assertEqual(CHUNK_GAP_TIMEOUT_S, 300.0)


class StallMessageTest(unittest.TestCase):
    def test_stall_names_phase_and_span(self):
        from app.chat_generation.consume import _stall_message
        from app.chat_generation.stream import StreamStalled
        from app.chat_runtime import _is_idle_timeout

        msg = _stall_message(StreamStalled("tool", 600.4, 600.4))
        self.assertEqual(msg, "No output for 10m while writing a tool call (idle timeout).")
        self.assertTrue(_is_idle_timeout(msg))
        kept = _stall_message(StreamStalled("thinking", 1805.0, 3.0))
        self.assertEqual(kept, "No output for 30m 5s while thinking, though keep-alives kept arriving (idle timeout).")
        self.assertEqual(_stall_message(TimeoutError()), "upstream timeout (idle)")


class ConsumeRoundBeatTest(unittest.IsolatedAsyncioTestCase):
    async def test_beats_and_frames_flow_through_consume_round(self):
        from types import SimpleNamespace

        from app import chat_runtime as rt

        async def gen():
            yield b'data: {"choices":[{"delta":{"content":"par"}}]}\n\n'
            await asyncio.sleep(0.12)
            yield b'data: {"choices":[{"delta":{"content":"tial"}}]}\n\n'
            yield b"data: [DONE]\n\n"

        result = SimpleNamespace(
            status=200, body={}, stream=gen(), usage={}, credential_id=None, attempts=[]
        )
        src = SimpleNamespace(source_id=1, type_id="openai")
        user = {"id": 1, "key_id": None}

        with unittest.mock.patch("app.chat_generation.consume.PROVIDER_BEAT_S", 0.03), unittest.mock.patch.object(
            rt.api, "_safe_record_usage", lambda *a, **k: None
        ), unittest.mock.patch.object(rt.api, "_tap", side_effect=lambda s, *a, **k: s):
            events = []
            async for kind, payload in rt._consume_round(result, user, src, "m", "m"):
                events.append((kind, payload))
        kinds = [k for k, _ in events]
        self.assertIn("beat", kinds)
        self.assertEqual(kinds[-1], "done")
        done = events[-1][1]
        self.assertEqual(done["content"], "partial")
        self.assertIsNone(done["error"])

    async def test_thinking_mark_becomes_a_thinking_activity(self):
        from types import SimpleNamespace

        from app import chat_runtime as rt

        async def gen():
            yield b'data: {"choices":[{"delta":{"role":"assistant","content":""}}]}\n\n'
            yield b": fuse-thinking\n\n"
            yield b'data: {"choices":[{"delta":{"content":"done"},"finish_reason":"stop"}]}\n\n'
            yield b"data: [DONE]\n\n"

        result = SimpleNamespace(
            status=200, body={}, stream=gen(), usage={}, credential_id=None, attempts=[]
        )
        src = SimpleNamespace(source_id=1, type_id="claude")
        with unittest.mock.patch.object(rt.api, "_safe_record_usage", lambda *a, **k: None), unittest.mock.patch.object(
            rt.api, "_tap", side_effect=lambda s, *a, **k: s
        ):
            events = [item async for item in rt._consume_round(result, {"id": 1, "key_id": None}, src, "m", "m")]
        fuse = [p["fuse"] for k, p in events if k == "out" and "fuse" in p]
        self.assertEqual(fuse, [{"event": "activity", "phase": "model", "thinking": True}])
        self.assertEqual(events[-1][1]["content"], "done")


class ConsumeRoundStallTest(unittest.IsolatedAsyncioTestCase):
    async def test_stall_is_reported_as_stalled_not_server_error(self):
        from types import SimpleNamespace

        from app import chat_runtime as rt, provider_errors

        async def gen():
            yield b'data: {"choices":[{"delta":{"content":"Writing."}}]}\n\n'
            yield b'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"t","function":{"name":"write_file","arguments":""}}]}}]}\n\n'
            await asyncio.sleep(5)

        result = SimpleNamespace(status=200, body={}, stream=gen(), usage={}, credential_id=None, attempts=[])
        src = SimpleNamespace(source_id=1, type_id="claude")
        with unittest.mock.patch("app.chat_generation.consume.IDLE_TIMEOUT_S", 0.2), unittest.mock.patch.object(
            rt.api, "_safe_record_usage", lambda *a, **k: None
        ), unittest.mock.patch.object(rt.api, "_tap", side_effect=lambda s, *a, **k: s):
            events = [item async for item in rt._consume_round(result, {"id": 1, "key_id": None}, src, "m", "m")]
        error = events[-1][1]["error"]
        self.assertEqual(provider_errors.kind_of(error), "stalled")
        self.assertIn("while writing a tool call (idle timeout)", error["error"]["message"])
        self.assertEqual(provider_errors.retry_reason("claude", 502, error), "Claude: stopped responding")
        self.assertTrue(rt._should_resume(502, error["error"]["message"], resumes_left=4, idle_stalls=1,
                                          kind=provider_errors.kind_of(error)))


class IdleResumeCapTest(unittest.TestCase):
    def test_idle_timeout_resumes_twice_then_fails(self):
        from app.chat_runtime import IDLE_STALL_RETRIES, _should_resume

        # idle_stalls counts the stall being handled: resume the first two.
        self.assertEqual(IDLE_STALL_RETRIES, 2)
        self.assertTrue(_should_resume(502, "upstream timeout (idle)", resumes_left=4, idle_stalls=1))
        self.assertTrue(_should_resume(502, "upstream timeout (idle)", resumes_left=4, idle_stalls=2))
        self.assertFalse(_should_resume(502, "upstream timeout (idle)", resumes_left=4, idle_stalls=3))
        self.assertTrue(_should_resume(503, "overloaded", resumes_left=4, idle_stalls=5))
        self.assertFalse(_should_resume(502, "upstream timeout (idle)", resumes_left=0, idle_stalls=1))


if __name__ == "__main__":
    unittest.main()
