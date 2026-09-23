"""Devin proto decoder: split-string fields must concatenate, not last-win.

Devin streams long tool-call argument JSON in chunks: the same ToolCall field
occurs multiple times inside one message. The decoder used to assign
(last-wins), so long arguments arrived as tail fragments like '100"}' —
which the chat loop then flagged as truncated tool calls.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_TMP = tempfile.mkdtemp(prefix="fuse-devinproto-")
os.environ.setdefault("DATABASE_PATH", str(Path(_TMP) / "devinproto.db"))

from app.sources import devin_proto as proto  # noqa: E402


def _wrap_tool_call(data: bytes) -> bytes:
    outer = proto.ProtoEncoder()
    outer._tag(6, 2)
    outer._varint(len(data))
    outer._buf.extend(data)
    return outer.finish()


def _tool_call_message(*chunks: str, name: str = "run_terminal_command", tid: str = "t1") -> bytes:
    e = proto.ProtoEncoder()
    e.string(1, tid)
    e.string(2, name)
    for c in chunks:
        e.string(3, c)
    return _wrap_tool_call(e.finish())


class SplitToolArgsTest(unittest.TestCase):
    def test_split_arguments_json_concatenates(self):
        msg = proto.decode_get_chat_message(
            _tool_call_message('{"command": "sed -n 100,', '150p file.rs"}')
        )
        self.assertEqual(len(msg["tool_calls"]), 1)
        self.assertEqual(
            msg["tool_calls"][0]["arguments_json"],
            '{"command": "sed -n 100,150p file.rs"}',
        )

    def test_single_occurrence_unchanged(self):
        msg = proto.decode_get_chat_message(_tool_call_message('{"command": "ls"}'))
        self.assertEqual(msg["tool_calls"][0]["arguments_json"], '{"command": "ls"}')

    def test_split_invalid_json_concatenates(self):
        e = proto.ProtoEncoder()
        e.string(1, "t2")
        e.string(4, '{"command": "echo ')
        e.string(4, 'hi"}')
        msg = proto.decode_get_chat_message(_wrap_tool_call(e.finish()))
        self.assertEqual(msg["tool_calls"][0]["arguments_json"], '{"command": "echo hi"}')

    def test_split_delta_text_concatenates(self):
        e = proto.ProtoEncoder()
        e.string(3, "hel")
        e.string(3, "lo world")
        msg = proto.decode_get_chat_message(e.finish())
        self.assertEqual(msg["delta_text"], "hello world")


class DevinRequestCapsTest(unittest.TestCase):
    def test_zero_caps_are_omitted_from_the_wire(self):
        enc = proto.ProtoEncoder()
        proto._encode_configuration(
            enc,
            {
                "num_completions": 1,
                "max_tokens": 0,
                "max_newlines": 0,
                "temperature": 0.4,
                "first_temperature": 0.4,
                "top_k": 50,
                "top_p": 1,
                "stop_patterns": [],
                "fim_eot": 1,
            },
        )
        cfg = proto.decode_configuration(enc.finish())
        self.assertEqual(cfg["max_tokens"], 0)
        self.assertEqual(cfg["max_newlines"], 0)

    def test_explicit_max_tokens_is_encoded(self):
        enc = proto.ProtoEncoder()
        proto._encode_configuration(enc, {"num_completions": 1, "max_tokens": 4096, "max_newlines": 0})
        cfg = proto.decode_configuration(enc.finish())
        self.assertEqual(cfg["max_tokens"], 4096)
        self.assertEqual(cfg["max_newlines"], 0)


if __name__ == "__main__":
    unittest.main()
