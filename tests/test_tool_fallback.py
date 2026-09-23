"""Retrieval-first injects onto the last user message."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
_TMP = tempfile.mkdtemp(prefix="fuse-fb-")
os.environ.setdefault("DATABASE_PATH", str(Path(_TMP) / "chat.db"))
os.environ.setdefault("APP_SECRET", "x" * 32)

from app.chat_runtime import inject_web_context
from app.search.expand import expand_user_question


class FallbackTest(unittest.TestCase):
    def test_role_alternation(self):
        msgs = [
            {"role": "system", "content": "policy"},
            {"role": "user", "content": "what is btc"},
        ]
        out = inject_web_context(msgs, "UNTRUSTED WEB SEARCH RESULTS")
        roles = [m["role"] for m in out]
        for a, b in zip(roles, roles[1:]):
            self.assertNotEqual(a, b)
        self.assertEqual(out[-1]["role"], "user")
        self.assertIn("UNTRUSTED", out[-1]["content"])

    def test_expand_compare(self):
        queries, recency = expand_user_question("compare glm and grok")
        self.assertGreaterEqual(len(queries), 1)
        joined = " ".join(queries).lower()
        self.assertTrue("glm" in joined or "grok" in joined)


if __name__ == "__main__":
    unittest.main()
