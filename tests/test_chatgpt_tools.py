"""ChatGPT Responses tool mapping fixtures (no network)."""

from __future__ import annotations

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


if __name__ == "__main__":
    unittest.main()
