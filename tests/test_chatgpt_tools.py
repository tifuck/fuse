"""ChatGPT Responses tool mapping fixtures (no network)."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
_TMP = tempfile.mkdtemp(prefix="fuse-cg-")
os.environ.setdefault("DATABASE_PATH", str(Path(_TMP) / "chat.db"))
os.environ.setdefault("APP_SECRET", "x" * 32)

from app.sources.chatgpt import build_responses_payload
from app.sources.chatgpt_stream import _aggregate, _translated_stream


class ChatgptToolsTest(unittest.TestCase):
    def test_function_call_pairing(self):
        payload = build_responses_payload(
            {
                "messages": [
                    {"role": "system", "content": "sys"},
                    {"role": "user", "content": "search this"},
                    {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {
                                "id": "call_abc",
                                "type": "function",
                                "function": {"name": "web_search", "arguments": '{"query":"x"}'},
                            }
                        ],
                    },
                    {"role": "tool", "tool_call_id": "call_abc", "content": "hits"},
                ],
                "tools": [
                    {
                        "type": "function",
                        "function": {"name": "web_search", "description": "s", "parameters": {"type": "object"}},
                    }
                ],
                "tool_choice": "none",
                "parallel_tool_calls": True,
            },
            "gpt-5",
            session_id="sess",
        )
        kinds = [i.get("type") or i.get("role") for i in payload["input"]]
        self.assertIn("function_call", kinds)
        self.assertIn("function_call_output", kinds)
        call = next(i for i in payload["input"] if i.get("type") == "function_call")
        out = next(i for i in payload["input"] if i.get("type") == "function_call_output")
        self.assertEqual(call["call_id"], "call_abc")
        self.assertEqual(out["call_id"], "call_abc")
        self.assertEqual(payload.get("tool_choice"), "none")


class _StreamResp:
    def __init__(self, frames):
        self._frames = frames
        self.closed = False

    async def aiter_text(self):
        for frame in self._frames:
            yield frame

    async def aclose(self):
        self.closed = True


class _FakeClient:
    def __init__(self):
        self.closed = False

    async def aclose(self):
        self.closed = True


def _frame(obj: dict) -> str:
    return "data: " + json.dumps(obj) + "\n\n"


class ChatgptStreamEdgeTest(unittest.IsolatedAsyncioTestCase):
    async def test_zero_argument_call_gets_object_arguments(self):
        resp = _StreamResp(
            [
                _frame({"type": "response.created", "response": {"id": "r1"}}),
                _frame(
                    {
                        "type": "response.output_item.added",
                        "item": {"type": "function_call", "id": "i1", "call_id": "call_1", "name": "ping", "arguments": ""},
                    }
                ),
                _frame({"type": "response.function_call_arguments.done", "item_id": "i1", "arguments": ""}),
                _frame({"type": "response.completed", "response": {"id": "r1", "usage": {}}}),
            ]
        )
        body = await _aggregate(resp, _FakeClient(), "gpt-5")
        call = body["choices"][0]["message"]["tool_calls"][0]
        self.assertEqual(call["function"]["arguments"], "{}")

    async def test_disconnect_mid_stream_is_cancelled_not_failed(self):
        resp = _StreamResp(
            [
                _frame({"type": "response.created", "response": {"id": "r2"}}),
                _frame({"type": "response.output_text.delta", "delta": "partial"}),
            ]
        )
        collected = {}
        stream = _translated_stream(resp, _FakeClient(), "gpt-5", collected)
        await stream.__anext__()
        await stream.__anext__()
        await stream.aclose()
        # A cancelled stream must not cooldown the credential: only a failed
        # one may (chatgpt._bind_completed_stream).
        self.assertEqual(collected["stream_status"], "cancelled")


if __name__ == "__main__":
    unittest.main()
