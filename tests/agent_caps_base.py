"""Shared setup for the agent/subagent suites (test_agent_caps, test_agent_children):
a throwaway SQLite schema behind db.connect plus a user, a parent turn and live children."""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
_TMP = tempfile.mkdtemp(prefix="fuse-agent-")
os.environ.setdefault("DATABASE_PATH", str(Path(_TMP) / "agent.db"))
os.environ.setdefault("APP_SECRET", "x" * 32)

from app import chat_store as store, chats, db


class AgentCapsBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="fuse-agent-")
        self.addCleanup(self.tmp.cleanup)
        self.conn = sqlite3.connect(str(Path(self.tmp.name) / "test.db"), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
        db._init(self.conn)
        self.addCleanup(self.conn.close)
        self.connection_patch = patch.object(db, "connect", return_value=self.conn)
        self.connection_patch.start()
        self.addCleanup(self.connection_patch.stop)
        # command_jobs remembers its schema per process; this database is new,
        # so an earlier module's flag must not skip creating its table.
        from app import command_jobs

        schema_patch = patch.object(command_jobs, "_schema_ready", False)
        schema_patch.start()
        self.addCleanup(schema_patch.stop)
        db.execute("INSERT INTO users(id,email,password_hash,created_at) VALUES(1,'1@test.invalid','unused',?)", (db.now(),))
        store.init_schema()

    def _parent(self):
        thread = chats.create_thread(1, model="offline")
        result = store.start_turn(1, thread["id"], "Hello", thread["revision"], "turn")
        return thread, result["run"]

    def _live_child(self, thread, parent, prompt="Audit the parser and fix bugs", **kw):
        child = store.start_child_run(user_id=1, thread_id=thread["id"], parent_run_id=parent["id"],
                                      prompt=prompt, description="worker", background=True, **kw)
        db.execute("UPDATE chat_runs SET status='running' WHERE id=?", (child["id"],))
        return child
