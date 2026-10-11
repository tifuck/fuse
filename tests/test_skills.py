"""Agent Skills library: parser, store, load_skill, slash preload, catalog."""
from __future__ import annotations

import io
import json
import os
import re
import sqlite3
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
_TMP = tempfile.mkdtemp(prefix="fuse-skills-")
os.environ.setdefault("DATABASE_PATH", str(Path(_TMP) / "skills.db"))
os.environ.setdefault("APP_SECRET", "s" * 32)

from app import accounts, agent_catalog, chats, db, skills, vault
from app.chat_runtime import (
    _provider_snapshot,
    _tool_messages,
    _tool_result_clip,
    apply_skill_preload,
    build_system_prompt,
    fold_preload_skill,
)
from app.chat_tools import execute_tool, openai_tools
from app.skill_install import (
    MAX_ZIPBALL,
    extract_skill_files,
    files_from_zip,
    parse_upload,
    valid_github_source,
)
from app.skill_parse import SkillError, parse_skill_md


def _md(name="demo", desc="Use when testing skills.", extra="", body="Do the thing.\n"):
    fm = f"---\nname: {name}\ndescription: {desc}\n{extra}---\n{body}"
    return fm


class ParserTest(unittest.TestCase):
    def test_valid(self):
        parsed = parse_skill_md(_md(), slug="demo")
        self.assertEqual(parsed["name"], "demo")
        self.assertIn("Use when testing", parsed["description"])
        self.assertEqual(parsed["body"].strip(), "Do the thing.")
        self.assertNotIn("---", parsed["body"])

    def test_unusable_name(self):
        with self.assertRaises(SkillError):
            parse_skill_md("---\nname: ---\ndescription: Helps with x. Use when testing.\n---\nbody\n")

    def test_uppercase_name_normalizes(self):
        parsed = parse_skill_md(
            "---\nname: PDF\ndescription: Helps with PDFs. Use when handling PDFs.\n---\nbody\n"
        )
        self.assertEqual(parsed["name"], "pdf")

    def test_missing_description_uses_body(self):
        parsed = parse_skill_md("---\nname: demo\n---\nDo the thing.\n")
        self.assertEqual(parsed["name"], "demo")
        self.assertIn("Do the thing.", parsed["description"])

    def test_slug_wins_over_frontmatter_name(self):
        parsed = parse_skill_md(_md(name="demo"), slug="other")
        self.assertEqual(parsed["name"], "other")

    def test_folded_description(self):
        text = (
            "---\n"
            "name: ponytail\n"
            "description: >\n"
            "  Forces the laziest solution that actually works, simplest, shortest.\n"
            "  Use on ANY coding task. Do NOT use for non-coding requests.\n"
            "argument-hint: \"[lite|full|ultra]\"\n"
            "license: MIT\n"
            "---\n"
            "# Ponytail\n"
        )
        parsed = parse_skill_md(text, slug="ponytail")
        self.assertEqual(parsed["name"], "ponytail")
        self.assertIn("laziest solution", parsed["description"])
        self.assertIn("non-coding", parsed["description"])
        self.assertNotIn(">", parsed["description"])

    def test_flags_round_trip(self):
        parsed = parse_skill_md(
            _md(extra="disable-model-invocation: true\nuser-invocable: false\nargument-hint: topic\n")
        )
        self.assertTrue(parsed["disable_model_invocation"])
        self.assertFalse(parsed["user_invocable"])
        self.assertEqual(parsed["argument_hint"], "topic")

    def test_frontmatter_close_must_own_its_line(self):
        text = "---\nname: demo\n--- not a close\ndescription: Use when testing.\n---\nDo the thing.\n"
        parsed = parse_skill_md(text)
        self.assertEqual(parsed["body"].strip(), "Do the thing.")
        self.assertIn("Use when testing", parsed["description"])

    def test_frontmatter_close_at_eof(self):
        parsed = parse_skill_md("---\nname: demo\ndescription: Use when testing.\n---")
        self.assertEqual(parsed["name"], "demo")
        self.assertEqual(parsed["body"], "")

    def test_deeply_nested_frontmatter_stays_small(self):
        # Each level used to str() the level below, re-escaping it: size
        # doubled per level, so ~35 levels exhausted memory.
        nested = "\n".join(" " * (i + 1) + "a:" for i in range(60)) + "\n" + " " * 61 + '"x": "y"'
        parsed = parse_skill_md(f"---\nname: demo\ndescription: Use when testing.\njunk:\n{nested}\n---\nbody\n")
        self.assertEqual(parsed["name"], "demo")
        self.assertLess(len(str(parsed)), 4096)


class SkillsDbTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="fuse-skills-db-")
        self.addCleanup(self.tmp.cleanup)
        self.conn = sqlite3.connect(str(Path(self.tmp.name) / "test.db"), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
        db._init(self.conn)
        self.addCleanup(self.conn.close)
        self.connection_patch = patch.object(db, "connect", return_value=self.conn)
        self.connection_patch.start()
        self.addCleanup(self.connection_patch.stop)
        self.data_patch = patch("app.skill_install.data_dir", return_value=Path(self.tmp.name) / "skills")
        self.data_patch.start()
        self.addCleanup(self.data_patch.stop)
        vault.lock_all()
        accounts.create_user("s@test.invalid", "password12", admin=False)

    def _install(self, slug="demo", body="Body text.\n", extra="", desc="Use when testing skills.", origin="upload"):
        text = _md(name=slug, desc=desc, extra=extra, body=body)
        files = {"SKILL.md": text.encode()}
        return skills.install_files(1, files, origin=origin, slug=slug)

    def test_web_research_unknown(self):
        self.assertEqual(agent_catalog.load_skill("web_research", user_id=1), "Unknown skill.")

    def test_index_omits_disabled_and_disable_model(self):
        self._install("alpha", extra="disable-model-invocation: true\n")
        self._install("bravo")
        skills.patch_skill(1, "bravo", {"enabled": False})
        self._install("charlie")
        index = skills.catalog_for_prompt(1)
        self.assertNotIn("`alpha`", index)
        self.assertNotIn("`bravo`", index)
        self.assertIn("`charlie`", index)

    def test_disable_model_still_loadable(self):
        self._install("quiet", extra="disable-model-invocation: true\n", body="Quiet body.\n")
        text = skills.load(1, "quiet")
        self.assertIn("Quiet body.", text)
        self.assertIn("skill://quiet", text)
        self.assertNotIn("data/skills", text)

    def test_load_unknown_and_disabled(self):
        self.assertEqual(skills.load(1, "nope"), "Unknown skill.")
        self._install("off")
        skills.patch_skill(1, "off", {"enabled": False})
        self.assertEqual(skills.load(1, "off"), "Unknown skill.")

    def test_file_and_traversal(self):
        files = {
            "SKILL.md": _md(name="pack", body="Root body.\n").encode(),
            "references/foo.md": b"ref-contents\n",
        }
        skills.install_files(1, files, origin="upload", slug="pack")
        self.assertIn("Root body.", skills.load(1, "pack", file="SKILL.md"))
        self.assertNotIn("Base:", skills.load(1, "pack", file="./SKILL.md"))
        self.assertIn("ref-contents", skills.load(1, "pack", file="references/foo.md"))
        self.assertIn("invalid skill path", skills.load(1, "pack", file="../etc/passwd"))
        self.assertIn("invalid skill path", skills.load(1, "pack", file="/etc/passwd"))
        self.assertIn("invalid skill path", skills.load(1, "pack", file="..\\x"))

    def test_install_refuses_overwrite_without_replace(self):
        self._install("demo", body="Original body.\n")
        evil = {"SKILL.md": _md(name="demo", desc="Evil twin.", body="Replaced body.\n").encode()}
        with self.assertRaises(SkillError) as ctx:
            skills.install_files(1, evil, origin="github", replace=False)
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertIn("Original body.", skills.load(1, "demo"))

    def test_wipe_on_reupload(self):
        first = {
            "SKILL.md": _md(name="wipe", body="v1\n").encode(),
            "scripts/evil.sh": b"echo evil\n",
        }
        skills.install_files(1, first, origin="upload", slug="wipe")
        self.assertIn("echo evil", skills.load(1, "wipe", file="scripts/evil.sh"))
        second = {"SKILL.md": _md(name="wipe", body="v2\n").encode()}
        skills.install_files(1, second, origin="upload", slug="wipe")
        again = skills.load(1, "wipe", file="scripts/evil.sh")
        self.assertTrue("unknown skill file" in again or "invalid" in again)
        self.assertIn("v2", skills.load(1, "wipe"))

    def test_user_overrides_bundled(self):
        text = _md(name="git", desc="User git override. Use when committing.", body="User git body.\n")
        skills.install_files(1, {"SKILL.md": text.encode()}, origin="upload", slug="git")
        loaded = skills.load(1, "git")
        self.assertIn("User git body.", loaded)
        self.assertNotIn("Conventional Commits", loaded)
        skills.delete_skill(1, "git")
        self.assertEqual(skills.load(1, "git"), "Unknown skill.")
        bundled = [s for s in skills.list_bundle(1)["skills"] if s["slug"] == "git"][0]
        self.assertFalse(bundled["enabled"])
        self.assertEqual(bundled["origin"], "bundled")

    def test_orchestrator_dispatch_default_on(self):
        slug = "orchestrator-dispatch"
        bundled = skills.read_bundled(slug)
        self.assertIsNotNone(bundled)
        self.assertTrue(bundled["enabled"])
        self.assertLessEqual(len(bundled["description"]), skills.INDEX_DESC)
        self.assertIn("subagents", bundled["description"])
        self.assertIn("only when the user asks", bundled["description"])
        listed = [s for s in skills.list_bundle(1)["skills"] if s["slug"] == slug][0]
        self.assertTrue(listed["enabled"])
        self.assertTrue(listed["user_invocable"])
        self.assertEqual(listed["origin"], "bundled")
        self.assertIn(f"`{slug}`", skills.catalog_for_prompt(1))
        self.assertTrue(any(s["slug"] == slug for s in skills.index_for_chat(1)))
        loaded = skills.load(1, slug)
        self.assertNotEqual(loaded, "Unknown skill.")
        self.assertIn("Orchestrator Dispatch", loaded)
        self.assertIn("YOU ARE THE WORKER", loaded)
        self.assertIn("Peer/clone", loaded)
        self.assertIn("same model as lead", loaded)
        self.assertIn("Sequential vs parallel", loaded)
        self.assertIn("DONE:", loaded)
        self.assertIn("Orient, then confirm once", loaded)
        self.assertIn("Do not spawn until the user says yes", loaded)
        self.assertIn("~10+ tool calls", loaded)
        self.assertIn("fragile or high-stakes work", loaded)
        self.assertIn("Never tell the worker you stopped spot-checking", loaded)
        self.assertIn("unless the user asked to use subagents", loaded)
        invoke = skills.parse_invoke(1, None, f"/{slug} audit app/", coding=True)
        self.assertEqual(invoke, {"name": slug, "arguments": "audit app/"})
        skills.patch_skill(1, slug, {"enabled": False})
        self.assertFalse([s for s in skills.list_bundle(1)["skills"] if s["slug"] == slug][0]["enabled"])
        self.assertNotIn(f"`{slug}`", skills.catalog_for_prompt(1))
        self.assertEqual(skills.load(1, slug), "Unknown skill.")
        skills.delete_skill(1, slug)
        restored = [s for s in skills.list_bundle(1)["skills"] if s["slug"] == slug][0]
        self.assertTrue(restored["enabled"])
        self.assertIn("Orchestrator Dispatch", skills.load(1, slug))

    def test_reserved_patch(self):
        skills.install_files(1, {"SKILL.md": _md(name="compact", body="nope\n").encode()}, origin="upload", slug="compact")
        row = skills.patch_skill(1, "compact", {"user_invocable": True, "enabled": True})
        self.assertFalse(row["user_invocable"])

    def test_prompt_index_and_clip(self):
        self._install("one", desc="A" * 200)
        text = skills.catalog_for_prompt(1)
        self.assertIn("`one`", text)
        self.assertLessEqual(len(text.split("`one` — ", 1)[1].split("\n")[0]), 160)
        coding = build_system_prompt(
            instructions="",
            profile="",
            thread_context="",
            tools=["web_search"],
            coding=True,
            skills_index=text,
        )
        self.assertIn("load_skill", coding)
        self.assertIn("ONCE at the beginning of the", coding)
        self.assertIn("never call", coding)
        self.assertIn("`one`", coding)
        self.assertIn("skill bodies and supporting files from `load_skill`", coding)
        self.assertNotIn("Do the thing", coding)
        off = build_system_prompt(instructions="", profile="", thread_context="", tools=[])
        self.assertNotIn("skill bodies", off)
        empty = build_system_prompt(
            instructions="", profile="", thread_context="", tools=[], coding=True, skills_index=""
        )
        self.assertIn("No skills enabled", empty)

    def test_body_clip_on_disk_accepted(self):
        body = "x" * 9000
        self._install("long", body=body + "\n")
        loaded = skills.load(1, "long")
        self.assertIn(body, loaded)
        self.assertIn("do not call `load_skill` for this skill again", loaded)
        huge = "y" * (skills.SKILL_BODY_CHARS + 5_000)
        self._install("huge", body=huge + "\n")
        clipped = skills.load(1, "huge")
        self.assertIn("[Fuse: skill content truncated;", clipped)
        self.assertLessEqual(len(clipped), skills.SKILL_BODY_CHARS + 600)
        self.assertIn("y" * 100, clipped)
        self.assertNotIn(huge, clipped)
        # The full load reaches the model uncut through the runtime clip.
        self.assertEqual(_tool_result_clip("load_skill", clipped), clipped)

    def test_tool_result_clip_both_writers(self):
        from app.chat_runtime import ToolJob

        body = "S" * 7500
        clipped = _tool_result_clip("load_skill", body)
        self.assertGreaterEqual(len(clipped), 7500)
        job = ToolJob(orig_index=0, call_id="c1", name="load_skill", arguments="{}", event_id="e1", text=body, done=True)
        msgs = _tool_messages([job])
        self.assertGreaterEqual(len(msgs[1]["content"]), 7500)
        snap = _provider_snapshot([{"role": "user", "content": "hi"}, {"role": "system", "content": "sys"}], [job])
        tool = [m for m in snap if m.get("role") == "tool"][0]
        self.assertGreaterEqual(len(tool["content"]), 7500)
        self.assertFalse(any(m.get("role") == "system" for m in snap))

    def test_preload_injects_and_resume_shape(self):
        self._install("ponytail", body="Ponytail body here.\n")
        messages = [{"role": "system", "content": "sys"}, {"role": "user", "content": "/ponytail restyle"}]
        out, event = apply_skill_preload(
            messages, user_id=1, run_id="abcd1234ffff", invoke={"name": "ponytail", "arguments": "restyle"}
        )
        self.assertIsNotNone(event)
        self.assertEqual(event["fuse"]["name"], "load_skill")
        self.assertTrue(event["fuse"]["id"].startswith("skill_"))
        user = [m for m in out if m["role"] == "user"][-1]
        self.assertEqual(user["content"], "restyle")
        snap = _provider_snapshot(out)
        self.assertFalse(any(m.get("role") == "system" for m in snap))
        pairs = [m for m in snap if m.get("role") == "assistant" and m.get("tool_calls")]
        self.assertEqual(len(pairs), 1)
        again, event2 = apply_skill_preload(
            out, user_id=1, run_id="abcd1234ffff", invoke={"name": "ponytail", "arguments": "restyle"}
        )
        self.assertIsNone(event2)
        self.assertEqual(len([m for m in again if m.get("role") == "tool"]), 1)

    def test_preload_keeps_the_session_tail(self):
        from app.chat_generation.messages import _inject_session_tail, _session_tail_block

        self._install("ponytail", body="Ponytail body here.\n")
        tail = _session_tail_block(["- art_1: Report (private, v1) https://x"], vault_locked=True)
        messages = _inject_session_tail(
            [{"role": "system", "content": "sys"}, {"role": "user", "content": "/ponytail restyle"}], tail)
        out, _event = apply_skill_preload(
            messages, user_id=1, run_id="abcd1234ffff", invoke={"name": "ponytail", "arguments": "restyle"}
        )
        user = [m for m in out if m["role"] == "user"][-1]
        # The command becomes the task; date, artifacts and vault state stay.
        self.assertEqual(user["content"], "restyle" + tail)

    def test_preload_keeps_non_text_parts(self):
        self._install("ponytail", body="Ponytail body here.\n")
        png = (
            "data:image/png;base64,"
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+ip1sAAAAASUVORK5CYII="
        )
        messages = [
            {"role": "system", "content": "sys"},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "/ponytail restyle"},
                    {"type": "image_url", "image_url": {"url": png}},
                ],
            },
        ]
        out, event = apply_skill_preload(
            messages, user_id=1, run_id="abcd1234ffff", invoke={"name": "ponytail", "arguments": "restyle"}
        )
        self.assertIsNotNone(event)
        user = [m for m in out if m["role"] == "user"][-1]
        self.assertEqual(user["content"][0]["text"], "restyle")
        self.assertEqual(user["content"][1]["image_url"]["url"], png)

    def test_fold_preload(self):
        messages = [
            {"role": "user", "content": "task"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "skill_abcd1234",
                        "type": "function",
                        "function": {"name": "load_skill", "arguments": '{"name":"demo"}'},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "skill_abcd1234", "content": "BODY"},
        ]
        folded = fold_preload_skill(messages)
        self.assertIn("BODY", folded[0]["content"])
        self.assertIn("demo", folded[0]["content"])

    def test_fold_preload_keeps_every_skill(self):
        messages = [
            {"role": "user", "content": "task"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"id": "skill_aaa", "type": "function",
                     "function": {"name": "load_skill", "arguments": '{"name":"alpha"}'}},
                    {"id": "skill_bbb", "type": "function",
                     "function": {"name": "load_skill", "arguments": '{"name":"beta"}'}},
                ],
            },
            {"role": "tool", "tool_call_id": "skill_aaa", "content": "ALPHA BODY"},
            {"role": "tool", "tool_call_id": "skill_bbb", "content": "BETA BODY"},
        ]
        folded = fold_preload_skill(messages)
        content = folded[0]["content"]
        self.assertIn("`alpha` instructions", content)
        self.assertIn("ALPHA BODY", content)
        self.assertIn("`beta` instructions", content)
        self.assertIn("BETA BODY", content)

    def test_reserved_not_invoke(self):
        parsed = skills.parse_invoke(1, None, "/compact please", coding=True)
        self.assertIsNone(parsed)
        parsed = skills.parse_invoke(1, None, "/undo", coding=True)
        self.assertIsNone(parsed)
        parsed = skills.parse_invoke(1, None, "/retry", coding=True)
        self.assertIsNone(parsed)

    def test_explicit_invoke_requires_coding(self):
        self._install("demo")
        with self.assertRaises(SkillError) as ctx:
            skills.parse_invoke(1, {"name": "demo"}, "/demo", coding=False)
        self.assertEqual(ctx.exception.status_code, 400)

    def test_slash_skill_turn_does_not_deadlock(self):
        """parse_invoke used to call get_row while start_turn held db._lock."""
        from concurrent.futures import ThreadPoolExecutor

        from app import chat_store as store

        self._install("demo")
        db.execute(
            "INSERT INTO user_agent_settings(user_id, agent_coding) VALUES(?,1)",
            (1,),
        )
        store.init_schema()
        thread = chats.create_thread(1, model="offline")

        def start():
            return store.start_turn(
                1,
                thread["id"],
                "/demo do the thing",
                thread["revision"],
                "skill-turn",
                invoke_skill={"name": "demo", "arguments": "do the thing"},
            )

        with ThreadPoolExecutor(max_workers=1) as pool:
            result = pool.submit(start).result(timeout=5)
        self.assertEqual(result["run"]["request"]["agent"]["invoke_skill"]["name"], "demo")

    def test_slash_skill_text_parse_inside_turn_does_not_deadlock(self):
        from concurrent.futures import ThreadPoolExecutor

        from app import chat_store as store

        self._install("demo")
        store.init_schema()
        thread = chats.create_thread(1, model="offline")

        def start():
            return store.start_turn(
                1,
                thread["id"],
                "/demo from text",
                thread["revision"],
                "skill-text-turn",
            )

        with ThreadPoolExecutor(max_workers=1) as pool:
            # coding off: lookup still happens; must 400 instead of freezing
            with self.assertRaises(chats.ChatValidationError) as raised:
                pool.submit(start).result(timeout=5)
        self.assertEqual(raised.exception.status_code, 400)


class AsyncSkillTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="fuse-skills-async-")
        self.conn = sqlite3.connect(str(Path(self.tmp.name) / "test.db"), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
        db._init(self.conn)
        self.connection_patch = patch.object(db, "connect", return_value=self.conn)
        self.connection_patch.start()
        vault.lock_all()
        accounts.create_user("a@test.invalid", "password12", admin=False)

    async def asyncTearDown(self):
        self.connection_patch.stop()
        self.conn.close()
        self.tmp.cleanup()

    async def test_load_skill_flag_off(self):
        text, rec = await execute_tool("load_skill", '{"name":"demo"}', user_id=1, allowed_urls=set(), coding=False)
        self.assertIn("Unknown tool", text)

    async def test_search_tools_skills(self):
        files = {"SKILL.md": _md(name="demo", body="hi\n").encode()}
        with patch("app.skill_install.data_dir", return_value=Path(self.tmp.name) / "skills"):
            skills.install_files(1, files, origin="upload", slug="demo")
            packed, rec = await execute_tool(
                "search_tools", '{"query":"skills"}', user_id=1, allowed_urls=set(), coding=True
            )
        self.assertEqual(rec["preview"], "skills")
        data = json.loads(packed)
        self.assertTrue(any(s["slug"] == "demo" for s in data["skills"]))

    async def test_load_skill_file_trace_keeps_target(self):
        files = {
            "SKILL.md": _md(name="demo", body="Read the reference.\n").encode(),
            "references/review.md": b"Review instructions\n",
        }
        with patch("app.skill_install.data_dir", return_value=Path(self.tmp.name) / "skills"):
            skills.install_files(1, files, origin="upload", slug="demo")
            text, rec = await execute_tool(
                "load_skill",
                '{"name":"demo","file":"references/review.md"}',
                user_id=1,
                allowed_urls=set(),
                coding=True,
            )
        self.assertIn("Review instructions", text)
        self.assertEqual(rec["preview"], "demo")
        self.assertEqual(rec["path"], "references/review.md")


class ValidSourceTest(unittest.TestCase):
    def test_rejects_dotdot(self):
        self.assertFalse(valid_github_source("../passwd"))
        self.assertFalse(valid_github_source("foo/.."))
        self.assertFalse(valid_github_source("foo/.bar"))
        self.assertFalse(valid_github_source("http://127.0.0.1/foo"))
        self.assertTrue(valid_github_source("dietrichgebert/ponytail"))


class CatalogMockTest(unittest.IsolatedAsyncioTestCase):
    async def test_ponytail_search(self):
        class Resp:
            status_code = 200

            def json(self):
                return {
                    "query": "ponytail",
                    "searchType": "fuzzy",
                    "searchVersion": "legacy",
                    "skills": [
                        {
                            "id": "dietrichgebert/ponytail/ponytail",
                            "skillId": "ponytail",
                            "name": "ponytail",
                            "installs": 53780,
                            "source": "dietrichgebert/ponytail",
                        }
                    ],
                    "count": 100,
                }

        with patch("app.skill_install.ips_public", return_value=True), patch(
            "app.skill_install._http_get", new=AsyncMock(return_value=Resp())
        ):
            from app.skill_install import catalog_search

            data = await catalog_search("ponytail")
        self.assertEqual(data["skills"][0]["skillId"], "ponytail")
        self.assertEqual(data["skills"][0]["installs"], 53780)
        self.assertEqual(
            data["skills"][0]["url"],
            "https://skills.sh/dietrichgebert/ponytail/ponytail",
        )

    async def test_install_zipball(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr(
                "repo-hash/skills/ponytail/SKILL.md",
                _md(name="ponytail", desc="Ponytail workflow.", body="Pony steps.\n"),
            )
        archive = buf.getvalue()

        class Meta:
            status_code = 200

            def json(self):
                return {"default_branch": "main"}

        class ZipResp:
            status_code = 200
            content = archive
            headers = {}

        self.tmp = tempfile.TemporaryDirectory(prefix="fuse-gh-")
        self.conn = sqlite3.connect(str(Path(self.tmp.name) / "t.db"), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        db._init(self.conn)
        patcher = patch.object(db, "connect", return_value=self.conn)
        patcher.start()
        vault.lock_all()
        accounts.create_user("g@test.invalid", "password12", admin=False)
        async def github_get(url, accept="application/vnd.github+json", timeout=12.0):
            if "/repos/" in url and "zipball" not in url and "git/trees" not in url:
                return Meta()
            return ZipResp()

        with patch("app.skill_install.ips_public", return_value=True), patch(
            "app.skill_install._github_get", new=github_get
        ), patch("app.skill_install._follow_github", new=AsyncMock(return_value=archive)), patch(
            "app.skill_install.data_dir", return_value=Path(self.tmp.name) / "skills"
        ), patch("app.skill_install.limiter.allow", return_value=True):
            from app.skill_install import fetch_github_skill

            files = await fetch_github_skill("dietrichgebert/ponytail", "ponytail")
            row = skills.install_files(
                1, files, origin="skills_sh", source="dietrichgebert/ponytail", source_id="dietrichgebert/ponytail/ponytail"
            )
            self.assertEqual(row["origin"], "skills_sh")
            self.assertTrue(row["enabled"])
            self.assertIn("Pony steps.", skills.load(1, "ponytail"))
        patcher.stop()
        self.conn.close()
        self.tmp.cleanup()

    async def test_install_idempotent_fast_path(self):
        """Already-installed source_id resolves to a row without GitHub calls."""
        self.tmp = tempfile.TemporaryDirectory(prefix="fuse-gh-")
        self.conn = sqlite3.connect(str(Path(self.tmp.name) / "t.db"), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        db._init(self.conn)
        patcher = patch.object(db, "connect", return_value=self.conn)
        patcher.start()
        vault.lock_all()
        accounts.create_user("h@test.invalid", "password12", admin=False)
        try:
            md = _md(name="handoff", desc="Handoff workflow. Use when handing off.", body="Hand off.\n")
            with patch("app.skill_install.data_dir", return_value=Path(self.tmp.name) / "skills"):
                row = skills.install_files(
                    1,
                    {"SKILL.md": md.encode("utf-8")},
                    origin="skills_sh",
                    source="mattpocock/skills",
                    source_id="mattpocock/skills/handoff",
                )
            self.assertEqual(row["slug"], "handoff")
            found = skills.find_by_source_id(1, "mattpocock/skills/handoff")
            self.assertIsNotNone(found)
            self.assertEqual(found["slug"], "handoff")
            pub = skills._public(found)
            self.assertEqual(pub["source_id"], "mattpocock/skills/handoff")
            self.assertTrue(pub["can_remove"])
            self.assertIsNone(skills.find_by_source_id(1, "other/repo/skill"))
            self.assertIsNone(skills.find_by_source_id(1, ""))
        finally:
            patcher.stop()
            self.conn.close()
            self.tmp.cleanup()


class ZipUploadTest(unittest.TestCase):
    def test_rejects_dotdot_member(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("../evil.md", b"nope")

        with self.assertRaises(SkillError):
            files_from_zip(buf.getvalue())

    def test_upload_md_invalid_utf8(self):
        with self.assertRaises(SkillError):
            parse_upload("SKILL.md", b"\xff\xfe\x00not utf8")

    def test_loose_zipball_total_bound(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for i in range(2000):
                zf.writestr(f"repo-h/{i}.md", b"\0" * 20 * 1024)
        with self.assertRaises(SkillError):
            files_from_zip(buf.getvalue(), max_compressed=MAX_ZIPBALL, loose=True)

    def test_loose_zipball_entry_count_bound(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            for i in range(4001):
                zf.writestr(f"repo-h/{i}.txt", b"x")
        with self.assertRaises(SkillError):
            files_from_zip(buf.getvalue(), max_compressed=MAX_ZIPBALL, loose=True)

    def test_loose_zipball_extracts_folded_skill(self):
        md = (
            "---\n"
            "name: ponytail\n"
            "description: >\n"
            "  Forces the laziest solution that actually works.\n"
            "  Use when the user says ponytail.\n"
            "---\n"
            "# Ponytail\n"
        )
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("repo-hash/README.md", b"# repo\n")
            zf.writestr("repo-hash/LICENSE", b"MIT\n")
            zf.writestr("repo-hash/.gitignore", b"*\n")
            zf.writestr("repo-hash/skills/ponytail/SKILL.md", md)
            zf.writestr("repo-hash/skills/ponytail/references/guide.md", b"more\n")
            zf.writestr("repo-hash/.claude/skills/ponytail/SKILL.md", md)
        raw = files_from_zip(buf.getvalue(), max_compressed=8 * 1024 * 1024, loose=True)
        stripped = {}
        for path, blob in raw.items():
            parts = path.split("/", 1)
            stripped[parts[1] if len(parts) == 2 else path] = blob
        slug, files = extract_skill_files(stripped, skill_id="ponytail")
        self.assertEqual(slug, "ponytail")
        self.assertIn("SKILL.md", files)
        parsed = parse_skill_md(files["SKILL.md"].decode("utf-8"), slug="ponytail")
        self.assertIn("laziest", parsed["description"])


class FlagOffLockTest(unittest.TestCase):
    def test_openai_tools_unchanged(self):
        names = [s["function"]["name"] for s in openai_tools([])]
        self.assertEqual(names, ["create_artifact", "update_artifact"])
        names = [s["function"]["name"] for s in openai_tools(["web_search"], coding=True, project_session=True)]
        self.assertEqual(
            names,
            ["web_search", "read_url", "search_tools", "load_skill", "todo_write", "scratch"],
        )


class SkillsHttpTest(unittest.TestCase):
    def setUp(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from starlette.middleware.sessions import SessionMiddleware

        from app import web
        from app.config import settings

        self.tmp = tempfile.TemporaryDirectory(prefix="fuse-skills-http-")
        self.addCleanup(self.tmp.cleanup)
        self.conn = sqlite3.connect(str(Path(self.tmp.name) / "test.db"), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
        db._init(self.conn)
        self.addCleanup(self.conn.close)
        self.connection_patch = patch.object(db, "connect", return_value=self.conn)
        self.connection_patch.start()
        self.addCleanup(self.connection_patch.stop)
        self.data_patch = patch("app.skill_install.data_dir", return_value=Path(self.tmp.name) / "skills")
        self.data_patch.start()
        self.addCleanup(self.data_patch.stop)
        vault.lock_all()
        accounts.create_user("http-s@test.invalid", "password12", admin=False)
        app = FastAPI()
        app.add_middleware(
            SessionMiddleware,
            secret_key=settings.app_secret,
            https_only=True,
            same_site="strict",
        )
        app.include_router(web.router)
        self.client = TestClient(app, base_url="https://testserver")
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)

    def _csrf(self, html: str) -> str:
        match = re.search(r'name="csrf-token" content="([^"]+)"', html)
        if match:
            return match.group(1)
        match = re.search(r'name="csrf_token" value="([^"]+)"', html)
        if not match:
            self.fail("csrf token missing")
        return match.group(1)

    def _login(self):
        page = self.client.get("/login")
        token = self._csrf(page.text)
        posted = self.client.post(
            "/login",
            data={"email": "http-s@test.invalid", "password": "password12", "csrf_token": token},
            follow_redirects=False,
        )
        self.assertEqual(posted.status_code, 303)
        return self._csrf(self.client.get("/skills").text)

    def test_signed_out_redirect(self):
        res = self.client.get("/skills", follow_redirects=False)
        self.assertEqual(res.status_code, 303)
        self.assertEqual(res.headers.get("location"), "/login")

    def test_signed_in_page_and_api(self):
        csrf = self._login()
        page = self.client.get("/skills")
        self.assertEqual(page.status_code, 200)
        self.assertIn("data-skills", page.text)
        api = self.client.get("/skills/api")
        self.assertEqual(api.status_code, 200)
        slugs = [s["slug"] for s in api.json()["skills"]]
        self.assertIn("git", slugs)
        self.assertIn("debug", slugs)
        self.assertIn("orchestrator-dispatch", slugs)
        dispatch = next(s for s in api.json()["skills"] if s["slug"] == "orchestrator-dispatch")
        self.assertTrue(dispatch["enabled"])
        enable = self.client.patch(
            "/skills/git",
            headers={"x-csrf-token": csrf},
            json={"enabled": True},
        )
        self.assertEqual(enable.status_code, 200)
        self.assertTrue(enable.json()["enabled"])

    def test_upload_md(self):
        csrf = self._login()
        files = {"file": ("SKILL.md", _md(name="uploaded", body="From upload.\n"), "text/markdown")}
        res = self.client.post("/skills/upload", headers={"x-csrf-token": csrf}, files=files)
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.json()["slug"], "uploaded")
        self.assertTrue(res.json()["enabled"])
        self.assertIn("From upload.", skills.load(1, "uploaded"))

    def test_json_401_logged_out(self):
        res = self.client.get("/skills/api")
        self.assertEqual(res.status_code, 401)


if __name__ == "__main__":
    unittest.main()
