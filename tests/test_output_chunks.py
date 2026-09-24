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
