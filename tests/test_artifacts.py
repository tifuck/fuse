"""Artifact storage, ACL, CSP, and tool wiring (no live model traffic)."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_TMP = tempfile.mkdtemp(prefix="fuse-art-")
os.environ.setdefault("DATABASE_PATH", str(Path(_TMP) / "art.db"))
os.environ.setdefault("APP_SECRET", "x" * 32)

from app import accounts, artifacts, chats, db, push  # noqa: E402
from app.security import limiter  # noqa: E402
from app.chat_runtime import (  # noqa: E402
    _ARG_LIMIT_ARTIFACT,
    _artifact_lines,
    _redact_tool_args,
    _session_tail_block,
    _take_calls,
    _tool_messages,
    build_system_prompt,
    jobs_from_calls,
)
from app.chat_tools import openai_tools, parse_tool_names  # noqa: E402

import pytest

# Uses the database: a disposable MariaDB schema shared by this module.
pytestmark = pytest.mark.usefixtures("isolated_db")

db._conn = None


class ArtifactStoreTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db.connect()
        accounts.create_user("owner@example.test", "password12", admin=False, require_invite=False)
        accounts.create_user("other@example.test", "password12", admin=False, require_invite=False)
        accounts.create_user("arch@example.test", "password12", admin=False, require_invite=False)
        cls.owner = int(accounts.user_by_email("owner@example.test")["id"])
        cls.other = int(accounts.user_by_email("other@example.test")["id"])
        cls.archiver = int(accounts.user_by_email("arch@example.test")["id"])

    def setUp(self):
        limiter._hits.clear()

    def test_create_default_private_and_version(self):
        html = "<!doctype html><html><body><h1>Hi</h1></body></html>"
        art = artifacts.create(self.owner, title="  Chart  ", html=html)
        self.assertEqual(art["title"], "Chart")
        self.assertEqual(art["visibility"], "private")
        self.assertEqual(art["version"], 1)
        self.assertTrue(art["url"].startswith("/a/"))
        row = artifacts.get_for_view(art["identifier"], None)
        self.assertIsNone(row)
        owned = artifacts.get_for_view(art["identifier"], self.owner)
        self.assertIsNotNone(owned)
        other = artifacts.get_for_view(art["identifier"], self.other)
        self.assertIsNone(other)

    def test_public_visible_without_user(self):
        html = "<!doctype html><html><body>pub</body></html>"
        art = artifacts.create(self.owner, title="Pub", html=html, visibility="public")
        row = artifacts.get_for_view(art["identifier"], None)
        self.assertIsNotNone(row)
        self.assertEqual(row["visibility"], "public")
        self.assertRegex(art["identifier"], r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")

    def test_public_not_listed_for_other_user(self):
        html = "<!doctype html><html><body>pub</body></html>"
        art = artifacts.create(self.owner, title="Shared", html=html, visibility="public")
        mine = {item["identifier"] for item in artifacts.list_for_user(self.owner)}
        theirs = {item["identifier"] for item in artifacts.list_for_user(self.other)}
        self.assertIn(art["identifier"], mine)
        self.assertNotIn(art["identifier"], theirs)
        public_row = artifacts.get_for_view(art["identifier"], None)
        other_row = artifacts.get_for_view(art["identifier"], self.other)
        self.assertIsNotNone(public_row)
        self.assertIsNotNone(other_row)
        public_payload = artifacts.client_payload(other_row, owner=False)
        self.assertNotIn("user_id", public_payload)
        self.assertNotIn("thread_id", public_payload)
        self.assertNotIn("id", public_payload)
        owner_payload = artifacts.client_payload(public_row, owner=True)
        self.assertEqual(owner_payload["user_id"], self.owner)

    def test_other_user_cannot_continue_or_mutate_public(self):
        art = artifacts.create(self.owner, title="Shared2", html="<html><body>x</body></html>", visibility="public")
        with self.assertRaises(artifacts.ArtifactError):
            artifacts.continue_in_chat(self.other, art["identifier"])
        with self.assertRaises(artifacts.ArtifactError):
            artifacts.set_visibility(self.other, art["identifier"], "private")
        self.assertFalse(artifacts.delete(self.other, art["identifier"]))

    def test_update_increments_version(self):
        html = "<!doctype html><html><body>v1</body></html>"
        art = artifacts.create(self.owner, title="V", html=html)
        updated = artifacts.update(self.owner, art["identifier"], html="<!doctype html><html><body>v2</body></html>")
        self.assertEqual(updated["version"], 2)
        self.assertEqual(updated["versions"], [1, 2])
        v1 = artifacts.view(artifacts.get_owned(self.owner, art["identifier"]), include_html=True, version=1)
        self.assertIn("v1", v1["html"])

    def test_visibility_change_survives_a_concurrent_html_update(self):
        owner = self.owner
        art = artifacts.create(owner, title="Race", html="<html><body>v1</body></html>")
        real_tx = db.tx
        worker = []

        @contextmanager
        def tx():
            with real_tx() as conn:
                if worker:
                    yield conn
                    return

                class Interleave:
                    def __getattr__(_, name):
                        return getattr(conn, name)

                    def execute(_, sql, args=()):
                        cur = conn.execute(sql, args)
                        if sql.lstrip().startswith("SELECT title, visibility") and not worker:
                            # The model's HTML update lands between this read
                            # and the write (it waits instead once the read locks).
                            thread = threading.Thread(target=artifacts.update, args=(owner, art["identifier"]),
                                                      kwargs={"html": "<html><body>v2</body></html>"})
                            worker.append(thread)
                            thread.start()
                            thread.join(1)
                        return cur

                yield Interleave()

        with patch.object(artifacts.db, "tx", tx):
            artifacts.set_visibility(self.owner, art["identifier"], "public")
        worker[0].join(10)
        row = artifacts.get_owned(self.owner, art["identifier"])
        self.assertEqual(row["visibility"], "public")
        self.assertEqual(int(row["current_version"]), 2)

    def test_size_reject(self):
        with self.assertRaises(artifacts.ArtifactError) as ctx:
            artifacts.create(self.owner, title="big", html="x" * (artifacts.HTML_MAX + 10))
        self.assertEqual(ctx.exception.status_code, 413)

    def test_other_user_cannot_update_or_delete(self):
        art = artifacts.create(self.owner, title="Mine", html="<html><body>x</body></html>")
        with self.assertRaises(artifacts.ArtifactError):
            artifacts.update(self.other, art["identifier"], title="Stolen")
        self.assertFalse(artifacts.delete(self.other, art["identifier"]))
        self.assertTrue(artifacts.delete(self.owner, art["identifier"]))

    def test_continue_links_thread(self):
        art = artifacts.create(self.owner, title="Dash", html="<html><body>d</body></html>")
        result = artifacts.continue_in_chat(self.owner, art["identifier"])
        self.assertIn("thread_id", result)
        listed = artifacts.list_for_prompt(self.owner, result["thread_id"])
        self.assertEqual(listed[0]["identifier"], art["identifier"])
        thread = chats.get_thread(self.owner, result["thread_id"])
        self.assertIn(art["identifier"], thread["context"])

    def test_archive_hides_from_everyone(self):
        art = artifacts.create(self.archiver, title="Arc", html="<html><body>a</body></html>", visibility="public")
        artifacts.set_archived(self.archiver, art["identifier"], True)
        self.assertIsNone(artifacts.get_for_view(art["identifier"], self.archiver))
        self.assertIsNone(artifacts.get_for_view(art["identifier"], self.other))
        self.assertIsNone(artifacts.get_for_view(art["identifier"], None))
        listed = {item["identifier"]: item for item in artifacts.list_for_user(self.archiver)}
        self.assertTrue(listed[art["identifier"]]["archived"])
        with self.assertRaises(artifacts.ArtifactError):
            artifacts.update(self.archiver, art["identifier"], title="Nope")
        with self.assertRaises(artifacts.ArtifactError):
            artifacts.set_visibility(self.archiver, art["identifier"], "private")
        with self.assertRaises(artifacts.ArtifactError):
            artifacts.set_title(self.archiver, art["identifier"], "Nope")
        with self.assertRaises(artifacts.ArtifactError):
            artifacts.continue_in_chat(self.archiver, art["identifier"])

    def test_restore_makes_artifact_visible_again(self):
        art = artifacts.create(self.archiver, title="Back", html="<html><body>b</body></html>")
        artifacts.set_archived(self.archiver, art["identifier"], True)
        artifacts.set_archived(self.archiver, art["identifier"], False)
        self.assertIsNotNone(artifacts.get_for_view(art["identifier"], self.archiver))
        self.assertIsNone(artifacts.get_for_view(art["identifier"], self.other))
        listed = {item["identifier"]: item for item in artifacts.list_for_user(self.archiver)}
        self.assertFalse(listed[art["identifier"]]["archived"])

    def test_archived_excluded_from_prompt_and_deletable(self):
        art = artifacts.create(self.archiver, title="Prompt", html="<html><body>p</body></html>")
        thread = chats.create_thread(self.archiver, title="t")
        pk = chats.resolve_thread_pk(self.archiver, thread["id"])
        artifacts.attach_thread(self.archiver, art["identifier"], pk)
        self.assertEqual(artifacts.list_for_prompt(self.archiver, thread["id"])[0]["identifier"], art["identifier"])
        artifacts.set_archived(self.archiver, art["identifier"], True)
        self.assertEqual(artifacts.list_for_prompt(self.archiver, thread["id"]), [])
        self.assertTrue(artifacts.delete(self.archiver, art["identifier"]))

    def test_other_user_cannot_archive(self):
        art = artifacts.create(self.owner, title="Guard", html="<html><body>g</body></html>", visibility="public")
        with self.assertRaises(artifacts.ArtifactError):
            artifacts.set_archived(self.other, art["identifier"], True)
        self.assertFalse(artifacts.get_owned(self.owner, art["identifier"])["archived"])

    def test_html_from_line_array(self):
        text, trace = artifacts.execute_from_tool(
            "create_artifact",
            {"title": "Lines", "html": ["<html>", "<body>ok</body>", "</html>"]},
            user_id=self.owner,
            thread_id=None,
        )
        self.assertEqual(trace["status"], "done")
        self.assertIn("identifier=", text)
        row = artifacts.get_owned(self.owner, trace["identifier"])
        html = artifacts._html_at(row["id"], 1)
        self.assertIn("<body>ok</body>", html)

    def test_content_csp(self):
        resp = artifacts.content_response("<html><body>x</body></html>")
        csp = resp.headers["content-security-policy"]
        self.assertIn("frame-ancestors 'self'", csp)
        self.assertIn("sandbox allow-scripts allow-forms allow-modals", csp)
        self.assertNotIn("allow-same-origin", csp)
        self.assertIn("connect-src https://cdn.jsdelivr.net", csp)
        self.assertNotIn("'unsafe-eval'", csp)
        self.assertNotIn("connect-src 'self'", csp)
        self.assertEqual(resp.headers["x-frame-options"], "SAMEORIGIN")
        self.assertEqual(resp.headers["x-robots-tag"], "noindex, nofollow")

    def test_delete_is_rate_limited(self):
        art = artifacts.create(self.owner, title="Rate", html="<html><body>r</body></html>")
        for _ in range(artifacts.WRITES_PER_MIN - 1):
            self.assertFalse(artifacts.delete(self.owner, "missing-artifact-xx"))
        with self.assertRaises(artifacts.ArtifactError) as ctx:
            artifacts.delete(self.owner, art["identifier"])
        self.assertEqual(ctx.exception.status_code, 429)


class ArtifactToolWiringTest(unittest.TestCase):
    def test_search_chip_does_not_include_artifacts(self):
        self.assertEqual(parse_tool_names([]), [])
        self.assertEqual(parse_tool_names(["web_search"]), ["web_search"])
        names = [s["function"]["name"] for s in openai_tools([])]
        self.assertEqual(names, ["create_artifact", "update_artifact"])
        names = [s["function"]["name"] for s in openai_tools(["web_search"])]
        self.assertIn("web_search", names)
        self.assertIn("create_artifact", names)

    def test_model_visible_artifact_schema_and_session_context(self):
        text = build_system_prompt(instructions="", profile="", thread_context="", tools=[])
        # Artifact availability and usage policy are supplied in the tool
        # schemas, not repeated in regular-chat system prose.
        schemas = {s["function"]["name"]: s["function"] for s in openai_tools([])}
        self.assertEqual(set(schemas), {"create_artifact", "update_artifact"})
        create = schemas["create_artifact"]
        self.assertEqual(create["parameters"]["required"], ["title", "html"])
        self.assertIn("complete HTML document", create["description"])
        self.assertIn("markdown link to the url", create["description"])
        self.assertIn("Never put secrets", create["description"])
        self.assertIn("Web search is off", text)
        block = _session_tail_block(
            _artifact_lines(
                [{"identifier": "abc12345", "title": "Chart", "visibility": "private", "version": 1, "url": "/a/abc12345"}]
            )
        )
        self.assertIn("abc12345", block)
        self.assertIn("Chart", block)
        self.assertEqual(_artifact_lines([]), [])
        self.assertNotIn("Artifacts attached", _session_tail_block([]))

    def test_take_calls_keeps_large_artifact_html(self):
        html = "<html>" + ("a" * 20_000) + "</html>"
        payload = json.dumps({"title": "Big", "html": html})
        bucket = {}
        _take_calls(
            bucket,
            [{"index": 0, "id": "c1", "function": {"name": "create_artifact", "arguments": payload}}],
        )
        rec = bucket[0]["function"]
        self.assertEqual(rec["name"], "create_artifact")
        parsed = json.loads(rec["arguments"])
        self.assertEqual(len(parsed["html"]), len(html))
        self.assertLessEqual(len(rec["arguments"]), _ARG_LIMIT_ARTIFACT)

    def test_search_args_are_not_silently_clipped(self):
        bucket = {}
        _take_calls(
            bucket,
            [{"index": 0, "id": "c1", "function": {"name": "web_search", "arguments": "x" * 20_000}}],
        )
        # The assembler cannot assume the streamed function name is complete.
        # All calls share a hard cap and overflow is rejected, never sliced.
        self.assertEqual(bucket[0]["function"]["arguments"], "x" * 20_000)

    def test_tool_messages_redact_html(self):
        html = "<html>" + ("z" * 5000) + "</html>"
        jobs = jobs_from_calls(
            [{"id": "c1", "function": {"name": "create_artifact", "arguments": json.dumps({"title": "T", "html": html})}}],
            0,
        )
        jobs[0].text = "created"
        msgs = _tool_messages(jobs)
        args = json.loads(msgs[0]["tool_calls"][0]["function"]["arguments"])
        self.assertIn("stored", args["html"])
        self.assertNotIn("zzzz", json.dumps(msgs))

    def test_redact_helper(self):
        raw = json.dumps({"title": "T", "html": "<html>secret</html>", "visibility": "private"})
        out = _redact_tool_args("create_artifact", raw)
        self.assertNotIn("secret", out)
        self.assertIn("T", out)


class PushSafetyTest(unittest.TestCase):
    """app/push.py subscription and payload safety (app/web/notifications.py + push)."""

    @classmethod
    def setUpClass(cls):
        accounts.create_user("push-owner@example.test", "password12", admin=False, require_invite=False)
        accounts.create_user("push-other@example.test", "password12", admin=False, require_invite=False)
        cls.owner = int(accounts.user_by_email("push-owner@example.test")["id"])
        cls.other = int(accounts.user_by_email("push-other@example.test")["id"])

    def test_endpoint_allowlist(self):
        good = (
            "https://fcm.googleapis.com/fcm/send/abc",
            "https://updates.push.services.mozilla.com/push/v1/x",
            "https://web.push.apple.com/x",
            "https://db5p.notify.windows.com/?token=t",
        )
        bad = (
            "http://fcm.googleapis.com/x",
            "https://evil.test/push",
            "https://fcm.googleapis.com.evil.test/x",
            "https://fcm.googleapis.com:8443/x",
            "https://127.0.0.1/x",
            "https://internal.test:8443/x",
            "https://evil.test@fcm.googleapis.com.evil.test/x",
            "ftp://fcm.googleapis.com/x",
        )
        for endpoint in good:
            self.assertTrue(push.valid_endpoint(endpoint), endpoint)
        for endpoint in bad:
            self.assertFalse(push.valid_endpoint(endpoint), endpoint)

    def test_invalid_endpoint_rejected_at_save(self):
        before = push.count_for(self.owner)
        with self.assertRaises(ValueError):
            push.save_subscription(self.owner, "https://evil.test/push", "k", "a")
        self.assertEqual(push.count_for(self.owner), before)

    def test_subscription_stays_with_its_account(self):
        endpoint = "https://fcm.googleapis.com/fcm/send/take1"
        push.save_subscription(self.owner, endpoint, "k1", "a1")
        with self.assertRaises(ValueError):
            push.save_subscription(self.other, endpoint, "k2", "a2")
        rows = {r["endpoint"]: r["p256dh"] for r in push.subscriptions_for(self.owner)}
        self.assertEqual(rows.get(endpoint), "k1")
        self.assertEqual(push.count_for(self.other), 0)
        push.save_subscription(self.owner, endpoint, "k3", "a3")
        rows = {r["endpoint"]: r["p256dh"] for r in push.subscriptions_for(self.owner)}
        self.assertEqual(rows.get(endpoint), "k3")

    def test_subscription_cap_evicts_oldest(self):
        for i in range(push.MAX_SUBSCRIPTIONS + 5):
            push.save_subscription(self.owner, f"https://fcm.googleapis.com/fcm/send/cap{i}", "k", "a")
        self.assertEqual(push.count_for(self.owner), push.MAX_SUBSCRIPTIONS)
        kept = {row["endpoint"] for row in push.subscriptions_for(self.owner)}
        self.assertIn(f"https://fcm.googleapis.com/fcm/send/cap{push.MAX_SUBSCRIPTIONS + 4}", kept)
        self.assertNotIn("https://fcm.googleapis.com/fcm/send/cap0", kept)

    def test_push_payload_redacts_secrets(self):
        raw = (
            "password=hunter2 sk-abc123def456ghi789 "
            "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NSJ9.abcdefghij "
            "ghp_0123456789abcdef0123456789abcdef0123 " + "A" * 45
        )
        redacted = push._redact_secrets(raw)
        for secret in (
            "hunter2",
            "sk-abc123def456ghi789",
            "eyJhbGciOiJIUzI1NiJ9",
            "ghp_0123456789abcdef0123456789abcdef0123",
        ):
            self.assertNotIn(secret, redacted)
        self.assertIn("[redacted]", redacted)

    def test_notify_user_never_raises_on_a_storage_error(self):
        import asyncio
        from unittest.mock import patch

        # It runs in a bare create_task after a turn finishes.
        with patch.object(push, "ready", return_value=True), \
                patch.object(push, "subscriptions_for", side_effect=RuntimeError("pool exhausted")):
            self.assertEqual(asyncio.run(push.notify_user(self.owner, {"title": "t"})), 0)


class ArtifactTracePersistTest(unittest.TestCase):
    def test_parse_keeps_artifact_traces(self):
        msgs = chats.parse_messages(
            [
                {"role": "user", "content": "chart"},
                {
                    "role": "assistant",
                    "content": "done",
                    "tools": [
                        {
                            "name": "create_artifact",
                            "status": "done",
                            "title": "Burn",
                            "identifier": "abcDEF12_xx",
                            "url": "/a/abcDEF12_xx",
                            "visibility": "private",
                            "version": 1,
                        },
                    ],
                },
            ],
            strict=True,
        )
        self.assertEqual(len(msgs[1]["tools"]), 1)
        rec = msgs[1]["tools"][0]
        self.assertEqual(rec["name"], "create_artifact")
        self.assertEqual(rec["identifier"], "abcDEF12_xx")
        self.assertEqual(rec["version"], 1)


if __name__ == "__main__":
    unittest.main()
