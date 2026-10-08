"""Devin _ToolBook: token-delta argument assembly.

Wire truth (captured from live Devin streams): every tool call opens with one
frame carrying {id, name, arguments_json=""}; the arguments then arrive as
token-sized deltas in id-less/name-less frames; every call of the same tool
REUSES the same id. A delta can be valid standalone JSON (a bare digit), so
the accumulator must never treat validity as replacement.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("FUSE_DEVIN_DEBUG", "0")
_TMP = tempfile.mkdtemp(prefix="fuse-devinbook-")
os.environ.setdefault("DATABASE_PATH", str(Path(_TMP) / "devinbook.db"))

from app.sources.devin import _ToolBook, _merge_tool_args  # noqa: E402


def _frame(tid="", name="", args=""):
    return {"id": tid, "name": name, "arguments_json": args}


class MergeToolArgsTest(unittest.TestCase):
    def test_bare_digit_delta_appends_never_replaces(self):
        # The wipe: prev ends with "-maxdepth ", delta "2" is valid JSON.
        prev = '{"command": "find /usr/share -maxdepth '
        self.assertEqual(_merge_tool_args(prev, "2"), prev + "2")

    def test_snapshot_supersedes(self):
        self.assertEqual(_merge_tool_args('{"command": "ls', '{"command": "ls"}'), '{"command": "ls"}')

    def test_short_suffix_repeat_appends(self):
        # A suffix repeat is NOT treated as redelivery: dropping it would
        # corrupt commands containing repeated braces/quotes (e.g. "}}").
        self.assertEqual(_merge_tool_args('{"command": "ls"}', 'ls"}'), '{"command": "ls"}ls"}')

    def test_equal_fragments_are_not_assumed_to_be_redelivery(self):
        self.assertEqual(_merge_tool_args("{", "{"), "{{")
        self.assertEqual(_merge_tool_args("abc", "abc"), "abcabc")

    def test_complete_object_redelivery_and_replacement(self):
        self.assertEqual(_merge_tool_args('{"x":1}', '{"x":1}'), '{"x":1}')
        self.assertEqual(_merge_tool_args('{"x":1}', '{"x":2}'), '{"x":2}')

    def test_prefix_delta_inside_command_is_preserved(self):
        book = _ToolBook()
        book.add(_frame(tid="run", name="run_terminal_command"))
        for chunk in ['{"command":"echo ', '{', 'x}"}']:
            book.add(_frame(args=chunk))
        args = book.openai_calls()[0]["function"]["arguments"]
        self.assertEqual(json.loads(args), {"command": "echo {x}"})

    def test_plain_deltas_concatenate(self):
        self.assertEqual(_merge_tool_args('{"command": "echo ', 'hi"}'), '{"command": "echo hi"}')


class ToolBookStreamTest(unittest.TestCase):
    def _feed(self, book, chunks, tid="run_terminal", name="run_terminal_command"):
        for c in chunks:
            book.add(_frame(tid=tid, name=name, args=c) if c == "OPEN" else _frame(args=c))

    def test_single_call_with_digit_deltas(self):
        book = _ToolBook()
        book.add(_frame(tid="run_terminal", name="run_terminal_command"))
        for c in ["{", '"command": "', "find", " /", "usr", " -", "max", "depth", " ",
                  "2",  # bare digit: valid standalone JSON, used to wipe the accumulator
                  ">/", "dev", "/null", " |", " head", " -", "20", "\"}"]:
            book.add(_frame(args=c))
        calls = book.openai_calls()
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["function"]["name"], "run_terminal_command")
        self.assertEqual(calls[0]["function"]["arguments"], '{"command": "find /usr -maxdepth 2>/dev/null | head -20"}')
        json.loads(calls[0]["function"]["arguments"])  # must parse

    def test_two_calls_reusing_same_tid(self):
        book = _ToolBook()
        book.add(_frame(tid="run_terminal", name="run_terminal_command"))
        for c in ['{"command": "echo FIRST_DONE"}']:
            book.add(_frame(args=c))
        # Second call opens with the SAME tid and empty args.
        book.add(_frame(tid="run_terminal", name="run_terminal_command"))
        for c in ["{", '"command": "', "grep -R x | head -", "30", " && echo SECOND", "\"}"]:
            book.add(_frame(args=c))
        calls = book.openai_calls()
        self.assertEqual(len(calls), 2)
        self.assertEqual(json.loads(calls[0]["function"]["arguments"])["command"], "echo FIRST_DONE")
        self.assertEqual(json.loads(calls[1]["function"]["arguments"])["command"], "grep -R x | head -30 && echo SECOND")

    def test_delta_chunks_of_incomplete_call_still_merge_by_tid(self):
        book = _ToolBook()
        book.add(_frame(tid="run_terminal", name="run_terminal_command"))
        book.add(_frame(tid="run_terminal", args='{"command": "se'))
        book.add(_frame(tid="run_terminal", args='d -n 5"}'))
        calls = book.openai_calls()
        self.assertEqual(len(calls), 1)
        self.assertEqual(json.loads(calls[0]["function"]["arguments"])["command"], "sed -n 5")


if __name__ == "__main__":
    unittest.main()
