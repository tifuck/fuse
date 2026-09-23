"""Ollama tool id stability (no network)."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
_TMP = tempfile.mkdtemp(prefix="fuse-ollama-")
os.environ.setdefault("DATABASE_PATH", str(Path(_TMP) / "chat.db"))
os.environ.setdefault("APP_SECRET", "x" * 32)

from app.sources.ollama import to_ollama, to_openai_tools


class OllamaToolsTest(unittest.TestCase):
    def test_stable_ids_without_upstream(self):
        a = to_openai_tools([{"function": {"name": "web_search", "arguments": {"query": "x"}}}], include_index=True)
        b = to_openai_tools([{"function": {"name": "web_search", "arguments": {"query": "x"}}}], include_index=True)
        self.assertEqual(a[0]["id"], "ollama_0")
        self.assertEqual(a[0]["id"], b[0]["id"])

    def test_keeps_upstream_id(self):
        out = to_openai_tools([{"id": "keep-me", "function": {"name": "web_search", "arguments": "{}"}}])
        self.assertEqual(out[0]["id"], "keep-me")

    def test_tool_call_id_roundtrip(self):
        msgs = to_ollama(
            [
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {"id": "call_9", "function": {"name": "web_search", "arguments": '{"query":"a"}'}}
                    ],
                },
                {"role": "tool", "tool_call_id": "call_9", "content": "hits"},
            ]
        )
        self.assertEqual(msgs[0]["tool_calls"][0]["id"], "call_9")
        self.assertEqual(msgs[1]["tool_call_id"], "call_9")


if __name__ == "__main__":
    unittest.main()
