"""Devin stop_reason handling.

Devin streams one Fuse completion as several internal message segments. A
segment boundary can carry its own stop_reason (including STOP_MAX_TOKENS)
while the completion continues. Only a stop reason with NO output after it is
terminal and may map to finish_reason="length"; otherwise the chat loop
injects a bogus "Output hit the token limit" continuation.
"""

from __future__ import annotations

import asyncio
import json
import os
import struct
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_TMP = tempfile.mkdtemp(prefix="fuse-devin-")
os.environ.setdefault("DATABASE_PATH", str(Path(_TMP) / "devin.db"))

from app.sources import devin_proto as proto  # noqa: E402
from app.sources.devin import DevinSource  # noqa: E402

STOP_MAX_TOKENS = proto.STOP_MAX_TOKENS  # 3
STOP_FUNCTION_CALL = proto.STOP_FUNCTION_CALL  # 10


def _frame(text: str = "", stop: int = 0) -> bytes:
    e = proto.ProtoEncoder()
    if text:
        e.string(3, text)
    if stop:
        e._tag(5, 0)
        e._varint(stop)
    return proto.connect_frame(e.finish(), compressed=False)


def _trailer(message: str) -> bytes:
    body = json.dumps({"error": {"code": "internal", "message": message}}).encode()
    return bytes([proto.CONNECT_END_STREAM]) + struct.pack(">I", len(body)) + body


class _FakeResp:
    def __init__(self, chunks: list[bytes]):
        self._chunks = chunks

    async def aiter_bytes(self):
        for c in self._chunks:
            yield c

    async def aclose(self):
        pass


class _FakeClient:
    async def aclose(self):
        pass


def _finishes(chunks: list[bytes]) -> list[str]:
    out = []
    for chunk in chunks:
        for line in chunk.decode(errors="replace").splitlines():
            text = line.strip()
            if not text.startswith("data:"):
                continue
            data = text[5:].strip()
            if not data or data == "[DONE]":
                continue
            try:
                obj = json.loads(data)
            except json.JSONDecodeError:
                continue
            choice = (obj.get("choices") or [{}])[0]
            if choice.get("finish_reason"):
                out.append(choice["finish_reason"])
    return out


class DevinStopReasonTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.src = DevinSource({"id": 1, "name": "Devin"}, {}, [])

    async def _stream_finish(self, frames: list[bytes]) -> str:
        collected: dict = {}
        chunks = []
        async for chunk in self.src._stream_sse(_FakeResp(frames), _FakeClient(), collected, "test-model"):
            chunks.append(chunk)
        finishes = _finishes(chunks)
        self.assertTrue(finishes, "no finish_reason chunk emitted")
        return finishes[-1]

    async def test_segment_max_tokens_then_more_text_is_not_length(self):
        finish = await self._stream_finish(
            [
                _frame(text="Part one."),
                _frame(text="segment cut", stop=STOP_MAX_TOKENS),
                _frame(text="Part two continues the answer."),
            ]
        )
        self.assertEqual(finish, "stop")

    async def test_terminal_max_tokens_is_still_a_normal_stop(self):
        # Devin ends complete turns terminally with STOP_MAX_TOKENS (observed
        # at 98-3000 completion tokens with complete content), so it must map
        # to "stop", never "length" — otherwise the chat loop injects a bogus
        # "Output hit the token limit" continuation.
        finish = await self._stream_finish(
            [
                _frame(text="Part one."),
                _frame(text="and the turn ends here", stop=STOP_MAX_TOKENS),
            ]
        )
        self.assertEqual(finish, "stop")

    async def test_plain_stop(self):
        finish = await self._stream_finish([_frame(text="done"), _frame(stop=1)])
        self.assertEqual(finish, "stop")

    async def test_function_call_stop(self):
        finish = await self._stream_finish([_frame(text="calling"), _frame(stop=STOP_FUNCTION_CALL)])
        self.assertEqual(finish, "tool_calls")

    async def test_max_tokens_then_thinking_then_text(self):
        finish = await self._stream_finish(
            [
                _frame(text="answer"),
                _frame(stop=STOP_MAX_TOKENS),
                _frame(text=" more"),
                _frame(text="even more"),
            ]
        )
        self.assertEqual(finish, "stop")

    async def _collect_finish(self, frames: list[bytes]) -> str:
        obj, _usage, _trailer = await self.src._collect_completion(_FakeResp(frames), "test-model")
        return obj["choices"][0]["finish_reason"]

    async def test_collect_segment_max_tokens_not_length(self):
        finish = await self._collect_finish(
            [
                _frame(text="Part one."),
                _frame(text="cut", stop=STOP_MAX_TOKENS),
                _frame(text="Part two."),
            ]
        )
        self.assertEqual(finish, "stop")

    async def test_collect_terminal_max_tokens_is_stop(self):
        finish = await self._collect_finish([_frame(text="all"), _frame(stop=STOP_MAX_TOKENS)])
        self.assertEqual(finish, "stop")

    async def test_collect_surfaces_trailer_error(self):
        # Connect end-stream trailers carry the upstream failure; previously
        # they were swallowed and a failed completion looked like an empty 200.
        obj, _usage, trailer = await self.src._collect_completion(
            _FakeResp([_frame(text="partial"), _trailer("an internal error occurred (trace ID: t)")]),
            "test-model",
        )
        self.assertEqual(trailer, "an internal error occurred (trace ID: t)")

    async def test_collect_clean_trailer_is_empty(self):
        obj, _usage, trailer = await self.src._collect_completion(
            _FakeResp([_frame(text="done"), _frame(stop=1)]),
            "test-model",
        )
        self.assertEqual(trailer, "")
        self.assertIsNotNone(obj["choices"][0]["message"]["content"])


class DevinSkuTrailerHealthTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        from app.sources import devin as devin_mod

        devin_mod.reset_sku_health()
        self.src = DevinSource({"id": 1, "name": "Devin"}, {}, [])

    def tearDown(self):
        from app.sources import devin as devin_mod

        devin_mod.reset_sku_health()

    async def test_stream_trailer_marks_sku_dead(self):
        from app.sources import devin as devin_mod

        collected: dict = {}
        chunks = []
        async for chunk in self.src._stream_sse(
            _FakeResp([_frame(text="hi"), _trailer("an internal error occurred (trace ID: t)")]),
            _FakeClient(),
            collected,
            "devin/glm-5-2",
            "glm-5-2-none",
        ):
            chunks.append(chunk)
        self.assertEqual(collected.get("stream_status"), "failed")
        self.assertTrue(devin_mod.sku_dead("glm-5-2-none"))
        joined = b"".join(chunks).decode(errors="replace")
        self.assertIn("internal error", joined)

    async def test_stream_unrelated_trailer_does_not_mark(self):
        from app.sources import devin as devin_mod

        collected: dict = {}
        async for _chunk in self.src._stream_sse(
            _FakeResp([_frame(text="hi"), _trailer("deadline exceeded")]),
            _FakeClient(),
            collected,
            "devin/glm-5-2",
            "glm-5-2",
        ):
            pass
        self.assertFalse(devin_mod.sku_dead("glm-5-2"))


if __name__ == "__main__":
    unittest.main()
