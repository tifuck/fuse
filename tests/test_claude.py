"""Claude subscription pool: PKCE paste, Messages translation, routing."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
_TMP = tempfile.mkdtemp(prefix="fuse-claude-")
os.environ.setdefault("DATABASE_PATH", str(Path(_TMP) / "claude.db"))
os.environ.setdefault("APP_SECRET", "x" * 32)

from app import accounts, db  # noqa: E402
from app.sources import claude as claude_mod  # noqa: E402
from app.sources import pick  # noqa: E402
from app.sources.pool_pick import reset_health  # noqa: E402

import pytest

# Uses the database: a disposable MariaDB schema shared by this module.
pytestmark = pytest.mark.usefixtures("isolated_db")


def _events(*items):
    async def gen():
        for item in items:
            yield item

    return gen()


class _FakeResp:
    def __init__(self, events, status=200):
        self.status_code = status
        self._events = events
        self.closed = False

    async def aiter_text(self):
        for event in self._events:
            yield "event: " + event["type"] + "\ndata: " + json.dumps(event) + "\n\n"

    async def aclose(self):
        self.closed = True

    async def aread(self):
        return b""


class ClaudeParseTest(unittest.TestCase):
    def test_manual_code_splits_on_last_hash(self):
        code, state = claude_mod.parse_manual_code("abc#def#state")
        self.assertEqual(code, "abc#def")
        self.assertEqual(state, "state")
        self.assertEqual(claude_mod.parse_manual_code("nostate"), ("", ""))
        self.assertEqual(claude_mod.parse_manual_code("  code # state "), ("code", "state"))

    def test_authorize_url_is_manual_pkce(self):
        url = claude_mod.authorize_url(challenge="chal", state="st")
        self.assertTrue(url.startswith("https://claude.ai/oauth/authorize?"))
        self.assertLessEqual(len(url), 500)
        self.assertIn("code_challenge=chal", url)
        self.assertIn("code_challenge_method=S256", url)
        self.assertIn("redirect_uri=https%3A%2F%2Fplatform.claude.com%2Foauth%2Fcode%2Fcallback", url)
        self.assertIn("user%3Ainference", url)
        console = claude_mod.authorize_url(challenge="c", state="s", console=True)
        self.assertTrue(console.startswith("https://platform.claude.com/oauth/authorize?"))

    def test_is_claude_model_does_not_match_devin(self):
        self.assertTrue(claude_mod.is_claude_model("claude/claude-sonnet-4-6"))
        self.assertFalse(claude_mod.is_claude_model("devin/claude-sonnet-5"))
        self.assertFalse(claude_mod.is_claude_model("claude-sonnet-4-6"))
        self.assertEqual(claude_mod.upstream_id("claude/claude-opus-4-6"), "claude-opus-4-6")

    def test_payload_maps_tools_and_results(self):
        payload = claude_mod.build_messages_payload(
            {
                "model": "claude/claude-sonnet-4-6",
                "max_tokens": 1000,
                "reasoning_effort": "high",
                "parallel_tool_calls": False,
                "messages": [
                    {"role": "system", "content": "be brief"},
                    {"role": "user", "content": [{"type": "text", "text": "hi"}, {"type": "image_url", "image_url": {"url": "data:image/png;base64,QQ=="}}]},
                    {
                        "role": "assistant",
                        "content": "calling",
                        "claude_thinking": [{"thinking": "hmm", "signature": "sig"}],
                        "tool_calls": [{"id": "toolu_1", "type": "function", "function": {"name": "lookup", "arguments": "{\"q\":1}"}}],
                    },
                    {"role": "tool", "tool_call_id": "toolu_1", "content": "ok"},
                    {"role": "tool", "tool_call_id": "", "content": "orphan"},
                ],
                "tools": [{"type": "function", "function": {"name": "lookup", "description": "find", "parameters": {"type": "object"}}}],
                "tool_choice": "required",
            },
            "claude-sonnet-4-6",
        )
        self.assertEqual(payload["model"], "claude-sonnet-4-6")
        self.assertEqual(payload["max_tokens"], 1000)
        self.assertEqual(payload["system"][0]["text"], "You are Claude Code, Anthropic's official CLI for Claude.")
        self.assertEqual(payload["system"][1]["text"], "be brief")
        self.assertEqual(payload["system"][1]["cache_control"], {"type": "ephemeral", "ttl": "1h"})
        self.assertEqual(payload["tools"][-1]["cache_control"], {"type": "ephemeral", "ttl": "1h"})
        self.assertNotIn("cache_control", payload["messages"][-1]["content"][-1])
        self.assertEqual(payload["messages"][-2]["content"][-1]["cache_control"], {"type": "ephemeral", "ttl": "1h"})
        self.assertEqual(payload["output_config"], {"effort": "high"})
        self.assertNotIn("thinking", payload)
        self.assertNotIn("temperature", payload)
        self.assertEqual(payload["tool_choice"]["type"], "any")
        self.assertTrue(payload["tool_choice"]["disable_parallel_tool_use"])
        assistant = payload["messages"][1]
        self.assertEqual(assistant["content"][0]["type"], "thinking")
        self.assertEqual(assistant["content"][0]["signature"], "sig")
        self.assertEqual(assistant["content"][2]["type"], "tool_use")
        self.assertEqual(assistant["content"][2]["input"], {"q": 1})
        result = payload["messages"][2]
        self.assertEqual(result["role"], "user")
        self.assertEqual(result["content"][0]["tool_use_id"], "toolu_1")
        self.assertEqual(len(result["content"]), 1)
        image = payload["messages"][0]["content"][1]
        self.assertEqual(image["source"]["type"], "base64")

    def test_thinking_off_keeps_sampling(self):
        payload = claude_mod.build_messages_payload(
            {"reasoning_effort": "none", "temperature": 0.2, "messages": [{"role": "user", "content": "hi"}]},
            "claude-haiku-4-5",
        )
        self.assertNotIn("thinking", payload)
        self.assertEqual(payload["temperature"], 0.2)

    def test_usage_windows_become_percent(self):
        parsed = claude_mod.parse_usage_payload(
            {
                "five_hour": {"utilization": 0.2, "resets_at": "2026-09-26T00:00:00Z"},
                "seven_day": {"utilization": 55},
            }
        )
        self.assertEqual(parsed["five_hour_percent"], 20.0)
        self.assertEqual(parsed["weekly_percent"], 55.0)
        self.assertEqual(parsed["credit_usage_percent"], 55.0)
        self.assertEqual(len(parsed["windows"]), 2)

    def test_catalog_keeps_first_party_models(self):
        rows = claude_mod.parse_catalog_payload(
            {
                "surfaces": {
                    "cc": {
                        "model_selector_config": [
                            {
                                "models": [
                                    {
                                        "id": "claude-opus-5-5",
                                        "name": "Opus 5.5",
                                        "offered_on": ["first_party"],
                                        "thinking": {"type": "effort"},
                                        "runtime": {"max_input_tokens": 1000000, "effort_levels": ["low", "medium"], "default_effort": "medium"},
                                    },
                                    {"id": "claude-opus-4-1-20250805", "name": "Opus 4.1", "offered_on": ["bedrock"]},
                                ]
                            }
                        ]
                    }
                }
            }
        )
        self.assertEqual([row["id"] for row in rows], ["claude-opus-5-5"])
        self.assertEqual(rows[0]["window"], 1_000_000)
        self.assertEqual(rows[0]["default_effort"], "medium")

    def test_missing_signature_is_dropped(self):
        payload = claude_mod.build_messages_payload(
            {
                "messages": [
                    {
                        "role": "assistant",
                        "content": "plain",
                        "claude_thinking": [{"thinking": "no sig", "signature": ""}],
                    }
                ]
            },
            "claude-sonnet-4-6",
        )
        kinds = [block["type"] for block in payload["messages"][0]["content"]]
        self.assertEqual(kinds, ["text"])


class ClaudeStreamTest(unittest.IsolatedAsyncioTestCase):
    async def test_sse_translates_text_thinking_and_tools(self):
        events = [
            {"type": "message_start", "message": {"id": "msg_1", "usage": {"input_tokens": 10, "cache_read_input_tokens": 4, "cache_creation_input_tokens": 2}}},
            {"type": "content_block_start", "index": 0, "content_block": {"type": "thinking", "thinking": ""}},
            {"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": "plan"}},
            {"type": "content_block_delta", "index": 0, "delta": {"type": "signature_delta", "signature": "SIG"}},
            {"type": "content_block_stop", "index": 0},
            {"type": "content_block_start", "index": 1, "content_block": {"type": "text", "text": ""}},
            {"type": "content_block_delta", "index": 1, "delta": {"type": "text_delta", "text": "Hello"}},
            {"type": "content_block_stop", "index": 1},
            {"type": "content_block_start", "index": 2, "content_block": {"type": "tool_use", "id": "toolu_9", "name": "lookup"}},
            {"type": "content_block_delta", "index": 2, "delta": {"type": "input_json_delta", "partial_json": "{\"q\":"}},
            {"type": "content_block_delta", "index": 2, "delta": {"type": "input_json_delta", "partial_json": "1}"}},
            {"type": "content_block_stop", "index": 2},
            {"type": "message_delta", "delta": {"stop_reason": "tool_use"}, "usage": {"output_tokens": 6}},
            {"type": "message_stop"},
        ]
        resp = _FakeResp(events)
        client = AsyncMock()
        collected = {}
        chunks = []
        async for piece in claude_mod._translated_stream(resp, client, "claude-sonnet-4-6", collected):
            chunks.append(piece.decode())
        joined = "".join(chunks)
        self.assertIn('"reasoning_content":"plan"', joined)
        self.assertIn('"content":"Hello"', joined)
        self.assertIn('"name":"lookup"', joined)
        self.assertIn('{\\"q\\":', joined)
        self.assertIn('"arguments":"1}"', joined)
        self.assertIn('"finish_reason":"tool_calls"', joined)
        self.assertIn('"cached_tokens":4', joined)
        self.assertIn('"cache_write_tokens":2', joined)
        self.assertTrue(joined.rstrip().endswith("data: [DONE]"))
        self.assertEqual(collected["stream_status"], "complete")
        self.assertEqual(collected["claude_thinking"], [{"thinking": "plan", "signature": "SIG"}])
        self.assertEqual(collected["prompt_tokens"], 16)
        self.assertEqual(collected["completion_tokens"], 6)

    async def test_empty_tool_arguments_become_object(self):
        events = [
            {"type": "message_start", "message": {"id": "msg_2", "usage": {"input_tokens": 1}}},
            {"type": "content_block_start", "index": 0, "content_block": {"type": "tool_use", "id": "toolu_0", "name": "ping"}},
            {"type": "content_block_stop", "index": 0},
            {"type": "message_delta", "delta": {"stop_reason": "tool_use"}, "usage": {"output_tokens": 1}},
            {"type": "message_stop"},
        ]
        resp = _FakeResp(events)
        body = await claude_mod._aggregate(resp, AsyncMock(), "claude-haiku-4-5")
        call = body["choices"][0]["message"]["tool_calls"][0]
        self.assertEqual(call["function"]["arguments"], "{}")
        self.assertEqual(body["choices"][0]["finish_reason"], "tool_calls")

    async def test_max_tokens_finish_is_length(self):
        events = [
            {"type": "message_start", "message": {"id": "msg_3", "usage": {"input_tokens": 1}}},
            {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "cut"}},
            {"type": "message_delta", "delta": {"stop_reason": "max_tokens"}, "usage": {"output_tokens": 2}},
            {"type": "message_stop"},
        ]
        body = await claude_mod._aggregate(_FakeResp(events), AsyncMock(), "claude-opus-4-6")
        self.assertEqual(body["choices"][0]["finish_reason"], "length")
        self.assertEqual(body["choices"][0]["message"]["content"], "cut")


class ClaudePoolTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        reset_health()
        db.execute("DELETE FROM credentials")
        db.execute("DELETE FROM api_keys")
        db.execute("DELETE FROM users")
        self.alice = accounts.create_user("claude-a@test.invalid", "password12", admin=False)
        self.bob = accounts.create_user("claude-b@test.invalid", "password12", admin=False)
        self.sid = claude_mod.ensure_claude_source()

    def test_own_access_unlocks(self):
        extra = {"access_token": "tok", "refresh_token": "rt", "account_id": "acct"}
        accounts.add_credential(self.sid, "mine", "tok", extra=extra, donated_by=self.alice["id"])
        self.assertTrue(claude_mod.user_has_claude(self.alice["id"]))
        self.assertFalse(claude_mod.user_has_claude(self.bob["id"]))

    async def test_pick_routes_claude_prefix_only(self):
        extra = {"access_token": "tok", "refresh_token": "rt"}
        accounts.add_credential(self.sid, "mine", "tok", extra=extra, donated_by=self.alice["id"])
        picked = await pick("claude/claude-sonnet-4-6", allow_claude=True)
        self.assertIsNotNone(picked)
        src, upstream = picked
        self.assertEqual(src.type_id, "claude")
        self.assertEqual(upstream, "claude-sonnet-4-6")
        self.assertIsNone(await pick("claude/claude-sonnet-4-6", allow_claude=False))
        self.assertIsNone(await pick("devin/claude-sonnet-5", allow_claude=True))

    async def test_complete_login_rejects_bad_state(self):
        cred, err = await claude_mod.complete_login("code#other", "ver", "expected", self.alice["id"])
        self.assertIsNone(cred)
        self.assertIn("match", err)
        cred, err = await claude_mod.complete_login("nocode", "ver", "expected", self.alice["id"])
        self.assertIsNone(cred)
        self.assertIn("#", err)

    @patch("app.sources.claude.fetch_profile", new_callable=AsyncMock)
    @patch("app.sources.claude.exchange_code", new_callable=AsyncMock)
    async def test_complete_login_stores_tokens(self, exchange, profile):
        exchange.return_value = {
            "access_token": "at",
            "refresh_token": "rt",
            "expires_in": 3600,
            "account": {"uuid": "acct-1", "email_address": "a@claude.test"},
            "organization": {"uuid": "org-1"},
        }
        profile.return_value = {
            "account": {"uuid": "acct-1", "email": "a@claude.test"},
            "organization": {"uuid": "org-1", "organization_type": "claude_pro"},
        }
        cred_id, err = await claude_mod.complete_login("thecode#thestate", "ver", "thestate", self.alice["id"])
        self.assertEqual(err, "")
        row = db.fetchone("SELECT * FROM credentials WHERE id=?", (cred_id,))
        extra = accounts.cred_extra(row)
        self.assertEqual(extra["account_id"], "acct-1")
        self.assertEqual(extra["email"], "a@claude.test")
        self.assertEqual(extra["refresh_token"], "rt")
        self.assertEqual(extra["subscription_type"], "pro")
        exchange.assert_awaited()
        sent = exchange.await_args.args
        self.assertEqual(sent[0], "thecode")
        self.assertEqual(sent[2], "thestate")

    @patch("app.sources.claude.note_pool_invoke")
    @patch("app.sources.claude.refresh_due", new_callable=AsyncMock)
    async def test_chat_posts_messages_and_does_not_retry_after_stream(self, _due, _invoke):
        events = [
            {"type": "message_start", "message": {"id": "msg_9", "usage": {"input_tokens": 3}}},
            {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "yo"}},
            {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 1}},
            {"type": "message_stop"},
        ]

        class Client:
            def __init__(self, *args, **kwargs):
                self.posts = []

            async def post(self, url, headers=None, json=None):
                self.posts.append((url, headers, json))
                return _FakeResp(events)

            async def aclose(self):
                return None

        extra = {"access_token": "tok", "refresh_token": "rt"}
        accounts.add_credential(self.sid, "mine", "tok", extra=extra, donated_by=self.alice["id"])
        src = claude_mod.ClaudeSource({"id": self.sid, "name": "Claude"}, {}, [])
        with patch("app.sources.claude.httpx.AsyncClient", Client):
            result = await src.chat(
                {"model": "claude/claude-sonnet-4-6", "stream": True, "messages": [{"role": "user", "content": "hi"}]},
                user_id=self.alice["id"],
            )
        self.assertEqual(result.status, 200)
        self.assertIsNotNone(result.stream)
        raw = b""
        async for chunk in result.stream:
            raw += chunk
        text = raw.decode()
        self.assertIn('"content":"yo"', text)
        self.assertIn("data: [DONE]", text)


if __name__ == "__main__":
    unittest.main()
