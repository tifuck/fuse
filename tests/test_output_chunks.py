import unittest

from app.chat_runs import Output


class OutputChunkTests(unittest.TestCase):
    def test_tool_markup_is_hidden_at_every_split_boundary(self):
        source = 'Visible <tool_call>hidden argument</tool_call> answer'
        reference = Output({"message": {}})
        reference.apply({"choices": [{"delta": {"content": source}}]})
        expected = reference.stored()["content"]
        self.assertNotIn("hidden argument", expected)
        for split in range(1, len(source)):
            with self.subTest(split=split):
                output = Output({"message": {}})
                for text in (source[:split], source[split:]):
                    output.apply({"choices": [{"delta": {"content": text}}]})
                self.assertEqual(output.stored()["content"], expected)

    def test_tool_markup_quoted_in_markdown_code_stays_visible(self):
        # extract_text_tool_calls never runs a call shown in markdown code, so
        # the provider transcript keeps it; the display must keep it too.
        source = ("Findings:\n- `<tool_call>` examples in prose are executed.\n"
                  "```xml\n<tool_call>{\"name\": \"grep\"}</tool_call>\n```\nEnd of report.")
        for split in range(1, len(source)):
            with self.subTest(split=split):
                output = Output({"message": {}})
                for text in (source[:split], source[split:]):
                    output.apply({"choices": [{"delta": {"content": text}}]})
                output.settle_tool_xml()
                self.assertEqual(output.stored()["content"], source)

    def test_prose_opener_is_released_when_the_round_ends(self):
        # An unclosed <tool_call> in prose is held while it might still be a
        # call; once the round ends without one, the reader sees all of it.
        source = "The parser treats <tool_call> in prose as a call. More findings follow."
        output = Output({"message": {}})
        output.apply({"choices": [{"delta": {"content": source}}]})
        self.assertEqual(output.stored()["content"], "The parser treats ")
        output.apply({"fuse": {"event": "activity", "phase": "model"}})
        self.assertEqual(output.stored()["content"], source)
        output.apply({"choices": [{"delta": {"content": " Next round."}}]})
        self.assertEqual(output.stored()["content"], source + " Next round.")
        for split in range(1, len(source)):
            with self.subTest(split=split):
                output = Output({"message": {}})
                for text in (source[:split], source[split:]):
                    output.apply({"choices": [{"delta": {"content": text}}]})
                output.settle_tool_xml()
                self.assertEqual(output.stored()["content"], source)

    def test_unclosed_text_format_call_is_not_released(self):
        output = Output({"message": {}})
        output.apply({"choices": [{"delta": {"content": "Checking.\n<tool_call>\n<function=grep>\n"
                                                       "<parameter=pattern>x</parameter>\n</function>"}}]})
        output.settle_tool_xml()
        self.assertEqual(output.stored()["content"], "Checking.\n")
