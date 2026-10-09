"""read_transcript: durable thread paging for post-compaction recovery."""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_TMP = tempfile.mkdtemp(prefix="fuse-xcript-")
os.environ.setdefault("DATABASE_PATH", str(Path(_TMP) / "xcript.db"))
os.environ.setdefault("APP_SECRET", "x" * 32)

from app import chats, db  # noqa: E402
from app.chat_tools import execute_tool  # noqa: E402

db.settings.database_path = str(Path(_TMP) / "xcript.db")
db._conn = None

AGENTS_RULE = "run tests with `make test`"


class ReadTranscriptTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="fuse-xscript-run-")
        self.addCleanup(self.tmp.cleanup)
        self.conn = sqlite3.connect(str(Path(self.tmp.name) / "test.db"), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        db._init(self.conn)
        self.addCleanup(self.conn.close)
        started = patch.object(db, "connect", return_value=self.conn)
        started.start()
        self.addCleanup(started.stop)
        for uid in (1, 2):
            db.execute(
                "INSERT INTO users(id,email,password_hash,created_at) VALUES(?,?,?,?)",
                (uid, f"{uid}@invalid", "unused", db.now()),
            )
        self.thread = chats.create_thread(1, model="glm-5.3", tools=[])
        rows = [
            {"id": "m1", "role": "user", "content": "please " + AGENTS_RULE},
            {"id": "m2", "role": "assistant", "content": "on it",
             "tools": [{"name": "grep", "preview": "found AGENTS_RULE", "status": "done"}]},
            {"id": "m3", "role": "assistant", "content": "x" * 2500},
            {"id": "m4", "role": "user", "content": "thanks"},
        ]
        db.execute(
            "UPDATE chat_threads SET messages=? WHERE id=?",
            (json.dumps(rows), chats.resolve_thread_pk(1, self.thread["id"])),
        )

    async def run_tool(self, arguments, **kwargs):
        return await execute_tool(
            "read_transcript", arguments,
            user_id=1, allowed_urls=set(), coding=True,
            thread_id=self.thread["id"], **kwargs,
        )

    async def test_pages_full_transcript(self):
        body, rec = await self.run_tool("{}")
        self.assertEqual(rec["status"], "done")
        self.assertIn("Messages 1-4 of 4", body)
        self.assertIn("1. user: please run tests with `make test`", body)
        self.assertIn("[grep]", body)
        self.assertNotIn("Use offset=", body)
        self.assertIn("untrusted record of this thread", body)

    async def test_limit_pages_with_continue_footer(self):
        body, rec = await self.run_tool('{"offset": 2, "limit": 2}')
        self.assertIn("Messages 2-3 of 4", body)
        self.assertIn("Use offset=4 to continue.", body)
        self.assertNotIn("1. user", body)

    async def test_clips_long_bodies(self):
        body, _ = await self.run_tool('{"offset": 3, "limit": 1}')
        self.assertIn("[clipped, 2500 chars total]", body)

    async def test_offset_past_end_errors(self):
        body, rec = await self.run_tool('{"offset": 9}')
        self.assertEqual(rec["status"], "error")
        self.assertEqual(rec["kind"], "out_of_range")
        self.assertIn("past the end of the transcript (4 messages)", body)

    async def test_other_users_thread_reads_empty(self):
        body, rec = await execute_tool(
            "read_transcript", "{}",
            user_id=2, allowed_urls=set(), coding=True,
            thread_id=self.thread["id"],
        )
        self.assertEqual(rec["status"], "error")
        self.assertEqual(rec["kind"], "empty")

    async def test_no_thread_id_errors(self):
        body, rec = await execute_tool(
            "read_transcript", "{}",
            user_id=1, allowed_urls=set(), coding=True, thread_id=None,
        )
        self.assertEqual(rec["status"], "error")
        self.assertEqual(rec["kind"], "no_thread")

    async def test_not_coding_is_unknown_tool(self):
        body, rec = await execute_tool(
            "read_transcript", "{}",
            user_id=1, allowed_urls=set(), coding=False,
            thread_id=self.thread["id"],
        )
        self.assertIn("Unknown tool", body)
        self.assertEqual(rec["status"], "error")
