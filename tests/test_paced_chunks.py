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
