"""Thought durations: measured as a run streams, kept on reasoning segments."""
import unittest
from unittest.mock import patch

from app import chat_output, chat_runs, chats


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def reasoning(text):
    return {"choices": [{"delta": {"reasoning_content": text}}]}


class OutputTiming(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        patcher = patch.object(chat_output, "_now", self.clock)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.out = chat_runs.Output({"message": {}})

    def test_thought_runs_from_the_previous_event_to_its_last_delta(self):
        self.out.apply({"fuse": {"event": "activity", "phase": "model"}})
        self.clock.now += 1.5  # time to first token counts, as in Claude Code
        self.out.apply({"fuse": {"event": "heartbeat"}})  # not an event
        self.clock.now += 1.0
        self.out.apply(reasoning("Plan the change."))
        self.clock.now += 2.0
        self.out.apply(reasoning(" Then test it."))
        seg = self.out.message["segments"][-1]
        self.assertEqual(seg["type"], "reasoning")
        self.assertEqual(seg["ms"], 4500)

        self.clock.now += 3.0
        self.out.apply({"fuse": {"event": "tool", "id": "t1", "name": "read_file", "status": "done"}})
        self.clock.now += 0.5
        self.out.apply(reasoning("Looks right."))
        self.assertEqual([s.get("ms") for s in self.out.message["segments"] if s["type"] == "reasoning"], [4500, 500])

    def test_duration_is_capped_like_one_thinking_block(self):
        self.out.apply(reasoning("start"))
        self.clock.now += 3600
        self.out.apply(reasoning(" still thinking"))
        self.assertEqual(self.out.message["segments"][-1]["ms"], chats.THOUGHT_MS_MAX)


class LivePatch(unittest.TestCase):
    def test_patch_carries_the_growing_duration(self):
        old = [{"type": "reasoning", "text": "a", "ms": 10}]
        self.assertEqual(chat_runs._segments_patch(old, [{"type": "reasoning", "text": "ab", "ms": 250}]),
                         {"texts": [{"index": 0, "from": 1, "text": "b", "ms": 250}]})
        self.assertEqual(chat_runs._segments_patch(old, [{"type": "reasoning", "text": "a", "ms": 300}]),
                         {"texts": [{"index": 0, "from": 1, "text": "", "ms": 300}]})
        self.assertEqual(chat_runs._segments_patch(old, [{"type": "reasoning", "text": "a", "ms": 10}]), {})


class SegmentPersistence(unittest.TestCase):
    def test_reasoning_ms_survives_strict_and_lenient_parsing(self):
        raw = [{"type": "reasoning", "text": "think", "ms": 4200}, {"type": "content", "text": "done"}]
        self.assertEqual(chats._parse_segments(raw, [], strict=True)[0], {"type": "reasoning", "text": "think", "ms": 4200})
        with self.assertRaises(chats.ChatValidationError):
            chats._parse_segments([{"type": "reasoning", "text": "think", "ms": -1}], [], strict=True)
        with self.assertRaises(chats.ChatValidationError):
            chats._parse_segments([{"type": "content", "text": "x", "ms": 5}], [], strict=True)
        self.assertNotIn("ms", chats._parse_segments([{"type": "reasoning", "text": "think", "ms": "4s"}], [])[0])

    def test_align_keeps_the_duration(self):
        aligned = chats.align_segments([{"type": "reasoning", "text": "think", "ms": 900}], "", "think")
        self.assertEqual(aligned, [{"type": "reasoning", "text": "think", "ms": 900}])


if __name__ == "__main__":
    unittest.main()
