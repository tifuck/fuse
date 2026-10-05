"""Todo traces, markdown-adjacent persistence, and replace diffs."""

from __future__ import annotations

import unittest

from app import chats
from app.chat_tools import _normalize_todos
from app.workspace import _replace_preview


class TodoTraceTest(unittest.TestCase):
    def test_normalize_caps_and_statuses(self):
        from app.chat_tools import TodoWriteError

        todos = _normalize_todos(
            [
                {"id": 1, "content": "  Ship  ", "status": "in_progress"},
                {"content": "Skip empty status"},
            ]
        )
        self.assertEqual(todos[0], {"id": "1", "content": "Ship", "status": "in_progress"})
        self.assertEqual(todos[1]["status"], "pending")
        self.assertEqual(len(todos), 2)
        with self.assertRaises(TodoWriteError):
            _normalize_todos([{"id": "x", "content": "", "status": "completed"}])
        with self.assertRaises(TodoWriteError):
            _normalize_todos([{"id": "bad", "content": "Nope", "status": "unknown"}])

    def test_strict_parse_keeps_todos(self):
        saved = chats._parse_tool_traces(
            [
                {
                    "name": "todo_write",
                    "status": "done",
                    "preview": "2 todos",
                    "todos": [
                        {"id": "a", "content": "One", "status": "completed"},
                        {"id": 2, "content": "Two", "status": "pending"},
                    ],
                }
            ],
            strict=True,
        )[0]
        self.assertEqual(saved["todos"][0]["id"], "a")
        self.assertEqual(saved["todos"][1]["id"], "2")
        self.assertEqual(saved["todos"][1]["content"], "Two")


class ReplacePreviewTest(unittest.TestCase):
    def test_unified_diff_preview_with_start_line(self):
        preview = _replace_preview("src/main.py", "old = 1", "new = 2", 1, start=48)
        self.assertIn("--- a/src/main.py", preview)
        self.assertIn("+++ b/src/main.py", preview)
        self.assertIn("@@ -48 +48 @@", preview)
        self.assertIn("-old = 1", preview)
        self.assertIn("+new = 2", preview)

    def test_preview_absolute_path_is_its_own_label(self):
        preview = _replace_preview("/srv/app/main.py", "old = 1", "new = 2", 1, start=3)
        self.assertIn("--- /srv/app/main.py", preview)
        self.assertNotIn("a//", preview)

    def test_preview_without_start_omits_hunk_header(self):
        preview = _replace_preview("src/main.py", "old = 1", "new = 2", 1)
        self.assertNotIn("@@", preview)
        self.assertNotIn("---", preview)
        self.assertIn("-old = 1", preview)
        self.assertIn("+new = 2", preview)


if __name__ == "__main__":
    unittest.main()
