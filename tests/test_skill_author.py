"""In-chat skill proposals: gate, pending row, confirm."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
_TMP = tempfile.mkdtemp(prefix="fuse-skill-author-")
os.environ.setdefault("DATABASE_PATH", str(Path(_TMP) / "author.db"))
os.environ.setdefault("APP_SECRET", "x" * 32)

from app import accounts, agent_catalog, chats, db, skill_proposals, skills
from app.chat_tools import execute_tool, openai_tools
from app.skill_parse import SkillError


def _user():
    db.connect()
    row = db.fetchone("SELECT * FROM users")
    if row:
        return row
    accounts.create_user("author@test.invalid", "password12")
    return db.fetchone("SELECT * FROM users")


def _thread(user_id):
    return chats.create_thread(user_id, title="t", model="glm-5.3-flash")


class ProposeSkillGateTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.user = _user()
        self.thread = _thread(self.user["id"])
        pk = chats.resolve_thread_pk(self.user["id"], self.thread["id"])
        self.tid = pk

    def test_always_on_unchanged(self):
        names = [
            s["function"]["name"]
            for s in openai_tools(["web_search"], coding=True, project_session=True)
        ]
        self.assertEqual(names, ["web_search", "read_url", "search_tools", "load_skill", "todo_write", "scratch"])
        self.assertNotIn("propose_skill", names)

    def test_skills_author_pack(self):
        found = agent_catalog.search("skills-author")
        self.assertEqual([s["function"]["name"] for s in found], ["propose_skill"])

    def test_description_clip(self):
        bundled = skills.read_bundled("create-skill")
        self.assertIsNotNone(bundled)
        self.assertLessEqual(len(bundled["description"]), skills.INDEX_DESC)
        self.assertIn("create a skill", bundled["description"])
        self.assertIn("turn this into a skill", bundled["description"])
        self.assertFalse(bundled["enabled"])

    def test_reserved_includes_title(self):
        self.assertIn("title", skills.RESERVED)
        self.assertIn("retry", skills.RESERVED)

    async def test_parent_can_propose(self):
        args = json.dumps({
            "name": "deploy-checklist",
            "description": "Use when deploying the API.",
            "body": "Run the deploy script.\n",
        })
        text, rec = await execute_tool(
            "propose_skill",
            args,
            user_id=self.user["id"],
            allowed_urls=set(),
            coding=True,
            thread_id=self.tid,
            session={"allow_skill_author": True, "run_id": "run-parent"},
        )
        self.assertEqual(rec["status"], "done")
        self.assertIn("not saved", text)
        row = skill_proposals.get_proposal(self.user["id"], rec["proposal_id"])
        self.assertEqual(row["status"], "pending")
        self.assertFalse(skills.get_row(self.user["id"], "deploy-checklist"))

    async def test_child_cannot_propose(self):
        text, rec = await execute_tool(
            "propose_skill",
            json.dumps({"name": "x", "description": "Use when x.", "body": "x"}),
            user_id=self.user["id"],
            allowed_urls=set(),
            coding=True,
            thread_id=self.tid,
            session={"allow_skill_author": False, "run_id": "run-child"},
        )
        self.assertEqual(rec["status"], "error")
        self.assertIn("disabled", text.lower())

    async def test_flag_off_unknown(self):
        text, rec = await execute_tool(
            "propose_skill",
            "{}",
            user_id=self.user["id"],
            allowed_urls=set(),
            coding=False,
            thread_id=self.tid,
            session={"allow_skill_author": True},
        )
        self.assertIn("Unknown tool", text)

    def test_confirm_saves(self):
        item = skill_proposals.create(
            self.user["id"],
            thread_id=self.tid,
            run_id="",
            name="ship-notes",
            description="Use when writing ship notes.",
            body="Keep them short.\n",
        )
        saved = skill_proposals.confirm_proposal(self.user["id"], item["id"], {"enable": True, "replace": True})
        self.assertEqual(saved["slug"], "ship-notes")
        self.assertEqual(saved["origin"], "chat")
        row = skills.get_row(self.user["id"], "ship-notes")
        self.assertTrue(row["enabled"])

    def test_reserved_slug_rejected(self):
        with self.assertRaises(SkillError):
            skill_proposals.create(
                self.user["id"],
                thread_id=self.tid,
                name="compact",
                description="Use when compacting.",
                body="nope",
            )
