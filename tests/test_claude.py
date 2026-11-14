"""Claude subscription pool: PKCE paste, Messages translation, routing."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
_TMP = tempfile.mkdtemp(prefix="fuse-claude-")
os.environ.setdefault("DATABASE_PATH", str(Path(_TMP) / "claude.db"))
os.environ.setdefault("APP_SECRET", "x" * 32)

from app import accounts, api, db  # noqa: E402
from app.sources import claude as claude_mod  # noqa: E402
from app.sources import claude_thread  # noqa: E402
from app.sources import pick, pool_denial  # noqa: E402
from app.sources.pool_pick import reset_health  # noqa: E402

import httpx
import pytest
from fastapi import HTTPException

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


def _final_thinking(chunks):
    """The claude_thinking blocks the translated stream hands the chat loop."""
    found = []
    for piece in chunks:
        for line in piece.splitlines():
            if not line.startswith("data: {"):
                continue
            for choice in json.loads(line[6:]).get("choices") or []:
                found.extend((choice.get("delta") or {}).get("claude_thinking") or [])
    return found


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

    def test_moving_breakpoint_skips_ineligible_blocks(self):
        payload = claude_mod.build_messages_payload(
            {
                "messages": [
                    {"role": "user", "content": "hi"},
                    {
                        "role": "assistant",
                        "content": "",
                        "claude_thinking": [{"thinking": "hmm", "signature": "sig"}],
                    },
                    {"role": "user", "content": "and now?"},
                ]
            },
            "claude-sonnet-4-6",
        )
        thinking = payload["messages"][1]["content"][0]
        self.assertEqual(thinking["type"], "thinking")
        self.assertNotIn("cache_control", thinking)
        # The rolling marker rides on the newest eligible block — here the
        # new final user text — skipping the thinking-only assistant turn, so
        # the next turn reads this request's cache entry instead of lagging
        # one round behind.
        self.assertEqual(
            payload["messages"][2]["content"][-1]["cache_control"],
            {"type": "ephemeral", "ttl": "1h"},
        )
        # The previous request ended at "hi": it carries the read-back marker.
        self.assertEqual(payload["messages"][0]["content"][-1]["cache_control"], {"type": "ephemeral", "ttl": "1h"})

    def test_parallel_round_keeps_the_previous_request_end_marked(self):
        calls = [{"id": f"toolu_{i}", "type": "function", "function": {"name": "lookup", "arguments": "{}"}} for i in range(12)]
        payload = claude_mod.build_messages_payload(
            {
                "messages": [
                    {"role": "system", "content": "sys"},
                    {"role": "user", "content": "go"},
                    {"role": "assistant", "content": "working", "claude_thinking": [{"thinking": "t", "signature": "s"}],
                     "tool_calls": calls},
                    *({"role": "tool", "tool_call_id": f"toolu_{i}", "content": f"r{i}"} for i in range(12)),
                ],
                "tools": [{"type": "function", "function": {"name": "lookup", "parameters": {"type": "object"}}}],
            },
            "claude-sonnet-4-6",
        )
        marked = [part for msg in payload["messages"] for part in msg["content"] if "cache_control" in part]
        # More than 20 blocks were added since "go", the previous request's
        # end, so it stays a breakpoint next to the newest block.
        self.assertEqual([part.get("text") or part.get("tool_use_id") for part in marked], ["go", "toolu_11"])
        total = len(marked) + sum("cache_control" in b for b in payload["system"]) + sum("cache_control" in t for t in payload["tools"])
        self.assertLessEqual(total, 4)

    def test_foreign_tool_ids_map_to_ids_anthropic_accepts(self):
        # History from Kimi/Devin/gpt-oss carries ids Anthropic rejects
        # (^[a-zA-Z0-9_-]+$); a switch to Claude must still replay it, with
        # each call paired to its result and the same bytes every request.
        ids = ["functions.grep:0", "call_ab#cd", "toolu_ok-1", "a" * 128 + "X", "a" * 128 + "Y", "a" * 128, "a" * 128 + ":"]
        body = {
            "messages": [
                {"role": "user", "content": "go"},
                {"role": "assistant", "content": "", "tool_calls": [
                    {"id": ident, "type": "function", "function": {"name": "grep", "arguments": "{}"}} for ident in ids]},
                *({"role": "tool", "tool_call_id": ident, "content": "r"} for ident in ids),
            ],
        }
        first = claude_mod.build_messages_payload(body, "claude-sonnet-4-6")
        second = claude_mod.build_messages_payload(body, "claude-sonnet-4-6")
        uses = [block["id"] for block in first["messages"][1]["content"] if block["type"] == "tool_use"]
        results = [block["tool_use_id"] for block in first["messages"][2]["content"]]
        self.assertEqual(uses, results)
        self.assertEqual(uses[2], "toolu_ok-1")
        self.assertEqual(len(set(uses)), len(ids))
        self.assertEqual(uses[5], ids[5])
        for ident in uses:
            self.assertRegex(ident, r"^[a-zA-Z0-9_-]+$")
            self.assertLessEqual(len(ident), 128)
        self.assertEqual(first["messages"], second["messages"])

    def test_tool_results_preserve_text_and_images(self):
        contents = [
            [{"type": "text", "text": "screenshot"},
             {"type": "image_url", "image_url": {"url": "data:image/png;base64,QQ=="}},
             {"type": "output_text", "text": "caption"}],
            [{"type": "image_url", "image_url": "https://example.com/shot.png"}],
            "plain text",
            None,
        ]
        body = {"messages": [
            {"role": "assistant", "tool_calls": [
                {"id": f"c{i}", "function": {"name": "shot", "arguments": "{}"}} for i in range(len(contents))
            ]},
            *({"role": "tool", "tool_call_id": f"c{i}", "content": content} for i, content in enumerate(contents)),
        ]}
        original = json.dumps(body)
        payload = claude_mod.build_messages_payload(body, "claude-sonnet-4-6")
        results = payload["messages"][-1]["content"]
        self.assertEqual([r["tool_use_id"] for r in results], [f"c{i}" for i in range(len(contents))])
        self.assertEqual(results[0]["content"], [
            {"type": "text", "text": "screenshot"},
            {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "QQ=="}},
            {"type": "text", "text": "caption"},
        ])
        self.assertEqual(results[1]["content"], [
            {"type": "image", "source": {"type": "url", "url": "https://example.com/shot.png"}},
        ])
        self.assertEqual(results[2]["content"], "plain text")
        self.assertEqual(results[3]["content"], "")
        self.assertEqual(json.dumps(body), original)

    def test_breakpoint_walks_back_inside_a_block_array(self):
        payload = claude_mod.build_messages_payload(
            {
                "messages": [
                    {
                        "role": "assistant",
                        "content": "",
                        "claude_thinking": [{"thinking": "hmm", "signature": "sig"}],
                        "tool_calls": [
                            {
                                "id": "toolu_1",
                                "type": "function",
                                "function": {"name": "lookup", "arguments": "{}"},
                            }
                        ],
                    },
                    {"role": "tool", "tool_call_id": "toolu_1", "content": ""},
                ]
            },
            "claude-sonnet-4-6",
        )
        # The final user turn is a tool result array whose only block carries
        # an empty string payload; it is still an eligible breakpoint.
        self.assertEqual(
            payload["messages"][1]["content"][-1]["cache_control"],
            {"type": "ephemeral", "ttl": "1h"},
        )

    def test_redacted_and_signed_empty_thinking_replay(self):
        payload = claude_mod.build_messages_payload(
            {
                "messages": [
                    {
                        "role": "assistant",
                        "content": "answer",
                        "claude_thinking": [
                            {"redacted_thinking": "enc-blob"},
                            {"thinking": "", "signature": "sig"},
                        ],
                    },
                    {"role": "user", "content": "next"},
                ]
            },
            "claude-sonnet-4-6",
        )
        blocks = payload["messages"][0]["content"]
        self.assertEqual(blocks[0], {"type": "redacted_thinking", "data": "enc-blob"})
        self.assertEqual(blocks[1]["type"], "thinking")
        self.assertEqual(blocks[1]["thinking"], "")
        self.assertEqual(blocks[1]["signature"], "sig")

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
        # Arguments stream as written, as in Claude Code; buffered, a large
        # write_file body was minutes of silence the pacer called a stall.
        self.assertTrue(all(tool["eager_input_streaming"] is True for tool in payload["tools"]))
        self.assertEqual(payload["messages"][-1]["content"][-1]["cache_control"], {"type": "ephemeral", "ttl": "1h"})
        self.assertNotIn("cache_control", payload["messages"][-2]["content"][-1])
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
            "claude-haiku-4-5-20251001",
        )
        self.assertNotIn("thinking", payload)
        self.assertEqual(payload["temperature"], 0.2)

    def test_effort_models_drop_sampling(self):
        # 4.7+ and 5.x reject non-default temperature/top_p on every request.
        payload = claude_mod.build_messages_payload(
            {"temperature": 0.2, "top_p": 0.9, "messages": [{"role": "user", "content": "hi"}]},
            "claude-opus-5-5",
        )
        self.assertNotIn("temperature", payload)
        self.assertNotIn("top_p", payload)

    def test_catalog_effort_models_get_adaptive_thinking_and_catalog_default(self):
        # The catalog's thinking.type is "effort": 5.x thinks but omits the
        # text without display, and 4.6-4.8 do not think without the field.
        payload = claude_mod.build_messages_payload({"messages": [{"role": "user", "content": "hi"}]}, "claude-opus-5-5")
        self.assertEqual(payload["thinking"], {"type": "adaptive", "display": "summarized"})
        self.assertEqual(payload["output_config"], {"effort": "medium"})
        fable = claude_mod.build_messages_payload({"messages": [{"role": "user", "content": "hi"}]}, "claude-fable-5-1")
        self.assertEqual(fable["output_config"], {"effort": "high"})

    def test_model_without_effort_gets_no_effort_or_thinking(self):
        # Haiku 4.5 rejects the effort parameter ("thinking": {"type": "none"}).
        for effort in (None, "high", "none"):
            body = {"messages": [{"role": "user", "content": "hi"}]}
            if effort:
                body["reasoning_effort"] = effort
            payload = claude_mod.build_messages_payload(body, "claude-haiku-4-5-20251001")
            self.assertNotIn("output_config", payload, effort)
            self.assertNotIn("thinking", payload, effort)

    def test_unlisted_effort_maps_to_nearest_level(self):
        from unittest.mock import patch

        from app.sources import claude_catalog

        meta = {"claude-opus-4-6": {"efforts": ("low", "medium", "high", "max"), "thinking_type": "effort"}}
        with patch.dict(claude_catalog._catalog_meta, meta):
            payload = claude_mod.build_messages_payload(
                {"reasoning_effort": "xhigh", "messages": [{"role": "user", "content": "hi"}]}, "claude-opus-4-6")
        self.assertEqual(payload["output_config"], {"effort": "max"})

    def test_thinking_off_maps_per_model(self):
        from app.sources import claude_catalog

        body = {"reasoning_effort": "none", "messages": [{"role": "user", "content": "hi"}]}
        meta = {"efforts": ("low", "medium", "high"), "thinking_type": "effort"}
        with patch.dict(claude_catalog.CLAUDE_MODEL_META, {"claude-opus-4-8": meta}):
            opus48 = claude_mod.build_messages_payload(dict(body), "claude-opus-4-8")
        self.assertEqual(opus48["thinking"], {"type": "disabled"})
        self.assertEqual(opus48["output_config"], {"effort": "low"})
        self.assertEqual(claude_mod.build_messages_payload(dict(body), "claude-sonnet-5")["thinking"], {"type": "disabled"})
        self.assertEqual(claude_mod.build_messages_payload(dict(body), "claude-sonnet-5-5")["thinking"], {"type": "between_tools"})
        # Opus 5.5 and Fable 5.1 reject disabled: lowest effort instead.
        opus = claude_mod.build_messages_payload(dict(body), "claude-opus-5-5")
        self.assertEqual(opus["thinking"], {"type": "adaptive", "display": "summarized"})
        self.assertEqual(opus["output_config"], {"effort": "low"})

    def test_forced_tool_choice_kept_where_adaptive_allows_it(self):
        payload = claude_mod.build_messages_payload(
            {
                "messages": [{"role": "user", "content": "hi"}],
                "tools": [{"type": "function", "function": {"name": "lookup", "parameters": {"type": "object"}}}],
                "tool_choice": {"type": "function", "function": {"name": "lookup"}},
                "reasoning_effort": "high",
            },
            "claude-sonnet-5",
        )
        self.assertEqual(payload["tool_choice"], {"type": "tool", "name": "lookup"})
        self.assertEqual(payload["thinking"]["type"], "adaptive")

    def test_forced_tool_choice_demoted_where_rejected(self):
        # Opus 5.5, Sonnet 5.5, Fable/Mythos 5.1 and 5.2 reject forced tool
        # use on every request, with or without a requested effort.
        for choice in ({"type": "function", "function": {"name": "lookup"}}, "required"):
            for model in ("claude-opus-5-5", "claude-sonnet-5-5", "claude-fable-5-1", "claude-fable-5-2"):
                payload = claude_mod.build_messages_payload(
                    {
                        "messages": [{"role": "user", "content": "hi"}],
                        "tools": [{"type": "function", "function": {"name": "lookup", "parameters": {"type": "object"}}}],
                        "tool_choice": choice,
                        "parallel_tool_calls": False,
                    },
                    model,
                )
                self.assertEqual(payload["tool_choice"], {"type": "auto", "disable_parallel_tool_use": True}, (model, choice))

    def test_small_max_tokens_drops_enabled_thinking(self):
        meta = {"thinking_type": "enabled"}
        with patch("app.sources.claude_payload._meta_for", return_value=meta):
            payload = claude_mod.build_messages_payload(
                {"messages": [{"role": "user", "content": "hi"}], "max_tokens": 512},
                "claude-sonnet-4-6",
            )
        # A budget clamped up to 1024 would exceed max_tokens and be rejected.
        self.assertNotIn("thinking", payload)
        with patch("app.sources.claude_payload._meta_for", return_value=meta):
            payload = claude_mod.build_messages_payload(
                {"messages": [{"role": "user", "content": "hi"}], "max_tokens": 8000},
                "claude-sonnet-4-6",
            )
        self.assertEqual(payload["thinking"], {"type": "enabled", "budget_tokens": 7999})

    def test_adaptive_thinking_streams_summaries(self):
        # Omitted display streams nothing until the think ends; a long one
        # looked dead and was cut by the gap timeout.
        with patch("app.sources.claude_payload._meta_for", return_value={"thinking_type": "adaptive"}):
            payload = claude_mod.build_messages_payload(
                {"messages": [{"role": "user", "content": "hi"}], "max_tokens": 8000, "reasoning_effort": "xhigh"},
                "claude-opus-5-5",
            )
        self.assertEqual(payload["thinking"], {"type": "adaptive", "display": "summarized"})
        self.assertEqual(payload["output_config"], {"effort": "xhigh"})

    def test_required_tool_choice_never_ships_with_manual_thinking(self):
        # Manual (budget) thinking rejects tool_choice "any": an explicit
        # thinking request demotes the choice, otherwise thinking yields.
        base = {"messages": [{"role": "user", "content": "hi"}], "max_tokens": 8000, "tool_choice": "required",
                "tools": [{"type": "function", "function": {"name": "lookup", "parameters": {"type": "object"}}}]}
        with patch("app.sources.claude_payload._meta_for", return_value={"thinking_type": "enabled"}):
            default = claude_mod.build_messages_payload(dict(base), "claude-sonnet-4-6")
            explicit = claude_mod.build_messages_payload({**base, "reasoning_effort": "high"}, "claude-sonnet-4-6")
        self.assertEqual(default["tool_choice"]["type"], "any")
        self.assertNotIn("thinking", default)
        self.assertIn("thinking", explicit)
        self.assertEqual(explicit["tool_choice"]["type"], "auto")

    def test_assistant_turn_replays_in_received_order(self):
        # 5.x models put a signed thinking block right before each tool_use;
        # preserved thinking rejects the turn if the blocks are reordered.
        calls = [{"id": f"toolu_{i}", "type": "function", "function": {"name": "lookup", "arguments": "{}"}} for i in range(2)]
        assistant = {
            "role": "assistant",
            "content": "Checking.",
            "tool_calls": calls,
            "claude_thinking": [
                {"thinking": "plan", "signature": "s0", "tool_index": 0},
                {"thinking": "", "signature": "s1", "tool_index": 0, "after_text": True},
                {"redacted_thinking": "r2", "tool_index": 1, "after_text": True},
            ],
        }
        body = {"messages": [{"role": "user", "content": "go"}, assistant,
                             *({"role": "tool", "tool_call_id": c["id"], "content": "r"} for c in calls)]}
        blocks = claude_mod.build_messages_payload(body, "claude-opus-5-5")["messages"][1]["content"]
        self.assertEqual(
            [b.get("signature") or b.get("data") or b.get("text") or b.get("id") for b in blocks],
            ["s0", "Checking.", "s1", "toolu_0", "r2", "toolu_1"],
        )
        # Rows stored before positions were recorded keep the old layout.
        for block in assistant["claude_thinking"]:
            block.pop("tool_index")
        blocks = claude_mod.build_messages_payload(body, "claude-opus-5-5")["messages"][1]["content"]
        self.assertEqual([b["type"] for b in blocks], ["thinking", "thinking", "redacted_thinking", "text", "tool_use", "tool_use"])

    def test_chat_loop_keeps_thinking_positions(self):
        from app.chat_generation.parsing import _native_reasoning, _take_thinking

        bucket: list = []
        _take_thinking(bucket, [{"thinking": "", "signature": "s", "tool_index": 1, "after_text": True},
                                {"redacted_thinking": "r", "tool_index": 2}])
        stored = _native_reasoning("", "claude/claude-opus-5-5", bucket)["claude_thinking"]
        self.assertEqual(stored, [{"thinking": "", "signature": "s", "tool_index": 1, "after_text": True},
                                  {"redacted_thinking": "r", "tool_index": 2}])

    def test_default_prices_use_1h_writes_and_haiku_long_tier(self):
        from app import pricing

        pricing.ensure_default_prices()
        opus = db.fetchone("SELECT * FROM model_prices WHERE provider='claude' AND model='claude-opus-5-5'")
        self.assertEqual((opus["input_micros_per_million"], opus["cached_input_micros_per_million"],
                          opus["cache_write_micros_per_million"], opus["output_micros_per_million"]),
                         (4_000_000, 200_000, 8_000_000, 20_000_000))
        haiku = db.fetchone("SELECT * FROM model_prices WHERE provider='claude' AND model='claude/claude-haiku-5-5'")
        tier = json.loads(haiku["tiers_json"])[0]
        self.assertEqual((tier["min_prompt_tokens"], tier["input_rate"], tier["cache_write_rate"]), (100_001, 500_000, 1_000_000))

    def test_mid_conversation_system_message_stays_in_place(self):
        body = {"messages": [
            {"role": "system", "content": "base"},
            {"role": "user", "content": "go"},
            {"role": "system", "content": "now read-only"},
            {"role": "assistant", "content": "ok"},
            {"role": "user", "content": "next"},
        ]}
        payload = claude_mod.build_messages_payload(body, "claude-opus-5-5")
        self.assertEqual([b["text"] for b in payload["system"]][1:], ["base"])
        self.assertEqual(payload["messages"][1], {"role": "system", "content": "now read-only"})
        # Sonnet 5 has no mid-conversation system messages: it is hoisted.
        hoisted = claude_mod.build_messages_payload(body, "claude-sonnet-5")
        self.assertEqual([b["text"] for b in hoisted["system"]][1:], ["base", "now read-only"])
        self.assertEqual([m["role"] for m in hoisted["messages"]], ["user", "assistant", "user"])
        # Between a tool_use and its result is not a valid position either.
        body = {"messages": [
            {"role": "user", "content": "go"},
            {"role": "assistant", "tool_calls": [{"id": "t1", "function": {"name": "x", "arguments": "{}"}}]},
            {"role": "system", "content": "late"},
            {"role": "tool", "tool_call_id": "t1", "content": "r"},
        ]}
        payload = claude_mod.build_messages_payload(body, "claude-opus-5-5")
        self.assertEqual(payload["system"][-1]["text"], "late")
        self.assertEqual([m["role"] for m in payload["messages"]], ["user", "assistant", "user"])

    def test_data_url_media_type_must_be_image(self):
        payload = claude_mod.build_messages_payload(
            {
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "image_url", "image_url": {"url": "data:text/html;base64,QQ=="}}
                        ],
                    }
                ]
            },
            "claude-sonnet-4-6",
        )
        # A non-image data URL is dropped, not forwarded as its media type.
        self.assertEqual(payload["messages"][0]["content"][0]["type"], "text")

    def test_failed_catalog_fetch_keeps_known_models(self):
        from app.sources import claude_catalog

        saved = (claude_catalog._models_cache, dict(claude_catalog._catalog_meta))
        try:
            claude_catalog._remember_models([{"id": "claude-future-9", "name": "Future 9"}])
            self.assertIn("claude/claude-future-9", claude_catalog._remember_models([]))
        finally:
            claude_catalog._models_cache, claude_catalog._catalog_meta = saved
            claude_catalog._catalog_meta.clear()
            claude_catalog._catalog_meta.update(saved[1])

    def test_usage_windows_become_percent(self):
        parsed = claude_mod.parse_usage_payload(
            {
                "five_hour": {"utilization": 0.2, "resets_at": "2026-09-26T00:00:00Z"},
                "seven_day": {"utilization": 55},
                "seven_day_fable": {"utilization": 90, "resets_at": "2026-09-30T00:00:00Z"},
                "seven_day_opus": None,
            }
        )
        self.assertEqual(parsed["five_hour_percent"], 20.0)
        self.assertEqual(parsed["weekly_percent"], 55.0)
        # A per-model cap is shown but never raises the routing peak.
        self.assertEqual(parsed["credit_usage_percent"], 55.0)
        self.assertEqual(len(parsed["windows"]), 2)
        self.assertEqual(
            parsed["model_windows"],
            [{"id": "seven_day_fable", "label": "Fable weekly", "percent": 90.0, "resets_at": "2026-09-30T00:00:00Z"}],
        )

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
                                        "runtime": {"max_input_tokens": 1000000, "max_output_tokens": 128000, "effort_levels": ["low", "medium"], "default_effort": "medium"},
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
        self.assertEqual(rows[0]["max_output"], 128_000)
        self.assertEqual(rows[0]["default_effort"], "medium")

    def test_max_tokens_defaults_to_model_output_ceiling(self):
        from app.sources import claude_payload

        with patch.object(claude_payload, "_meta_for", return_value={"max_output": 128_000}):
            default = claude_mod.build_messages_payload({"messages": [{"role": "user", "content": "hi"}]}, "claude-opus-5-5")
            over = claude_mod.build_messages_payload({"max_tokens": 500_000, "messages": [{"role": "user", "content": "hi"}]}, "claude-opus-5-5")
        self.assertEqual(default["max_tokens"], 128_000)
        self.assertEqual(over["max_tokens"], 128_000)

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
    async def test_crlf_framed_events_split_anywhere(self):
        from app.sources.claude_stream import _upstream_events

        class Resp:
            def __init__(self, chunks):
                self.chunks = chunks

            async def aiter_text(self):
                for chunk in self.chunks:
                    yield chunk

        data = 'event: a\r\ndata: {"x":1}\r\n\r\nevent: b\r\ndata: {"x":2}\r\n\r\n'
        for cut in range(1, len(data)):
            got = [obj async for obj in _upstream_events(Resp([data[:cut], data[cut:]]))]
            self.assertEqual(got, [{"x": 1, "type": "a"}, {"x": 2, "type": "b"}], cut)

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
        # The thinking block opens with the pacer's marker, before its text.
        self.assertLess(joined.index(": fuse-thinking\n\n"), joined.index('"reasoning_content":"plan"'))
        self.assertIn('"content":"Hello"', joined)
        self.assertIn('"name":"lookup"', joined)
        self.assertIn('{\\"q\\":', joined)
        self.assertIn('"arguments":"1}"', joined)
        self.assertIn('"finish_reason":"tool_calls"', joined)
        self.assertIn('"cached_tokens":4', joined)
        self.assertIn('"cache_write_tokens":2', joined)
        self.assertTrue(joined.rstrip().endswith("data: [DONE]"))
        self.assertEqual(collected["stream_status"], "complete")
        self.assertEqual(_final_thinking(chunks), [{"thinking": "plan", "signature": "SIG", "tool_index": 0}])
        # Thinking is not usage: it must stay out of the usage holder.
        self.assertNotIn("claude_thinking", collected)
        self.assertEqual(collected["prompt_tokens"], 16)
        self.assertEqual(collected["completion_tokens"], 6)

    async def test_redacted_and_signed_empty_thinking_survive_translation(self):
        events = [
            {"type": "message_start", "message": {"id": "msg_r", "usage": {"input_tokens": 5}}},
            {"type": "content_block_start", "index": 0, "content_block": {"type": "redacted_thinking", "data": "enc-blob"}},
            {"type": "content_block_stop", "index": 0},
            {"type": "content_block_start", "index": 1, "content_block": {"type": "thinking", "thinking": ""}},
            {"type": "content_block_delta", "index": 1, "delta": {"type": "signature_delta", "signature": "SIG2"}},
            {"type": "content_block_stop", "index": 1},
            {"type": "content_block_delta", "index": 2, "delta": {"type": "text_delta", "text": "answer"}},
            {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 1}},
            {"type": "message_stop"},
        ]
        resp = _FakeResp(events)
        collected = {}
        chunks = []
        async for piece in claude_mod._translated_stream(resp, AsyncMock(), "claude-sonnet-4-6", collected):
            chunks.append(piece.decode())
        joined = "".join(chunks)
        # Both replayable forms must come through untouched: the redacted
        # blob and the signed-empty (omitted-display) block.
        self.assertEqual(
            _final_thinking(chunks),
            [{"redacted_thinking": "enc-blob", "tool_index": 0}, {"thinking": "", "signature": "SIG2", "tool_index": 0}],
        )
        self.assertNotIn("claude_thinking", collected)
        self.assertIn("redacted_thinking", joined)
        # Each silent block marks the think so the pacer keeps its patience.
        self.assertEqual(joined.count(": fuse-thinking\n\n"), 2)
        self.assertEqual(collected["stream_status"], "complete")

    async def test_long_thought_signature_stays_out_of_usage(self):
        # A long think carries a signature bigger than the run's usage cap.
        # Merged into usage it failed the turn with "Generation usage limit
        # reached."; it must arrive as thinking and leave usage small.
        from app.chat_schema import USAGE_MAX

        signature = "S" * (USAGE_MAX + 8_000)
        events = [
            {"type": "message_start", "message": {"id": "msg_l", "usage": {"input_tokens": 5}}},
            {"type": "content_block_start", "index": 0, "content_block": {"type": "thinking", "thinking": ""}},
            {"type": "content_block_delta", "index": 0, "delta": {"type": "signature_delta", "signature": signature}},
            {"type": "content_block_stop", "index": 0},
            {"type": "content_block_delta", "index": 1, "delta": {"type": "text_delta", "text": "ok"}},
            {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 15000}},
            {"type": "message_stop"},
        ]
        collected = {}
        chunks = []
        async for piece in claude_mod._translated_stream(_FakeResp(events), AsyncMock(), "claude-opus-4-6", collected):
            chunks.append(piece.decode())
        self.assertEqual(_final_thinking(chunks), [{"thinking": "", "signature": signature, "tool_index": 0}])
        self.assertEqual(collected["completion_tokens"], 15000)
        self.assertLess(len(json.dumps(collected, separators=(",", ":"))), USAGE_MAX)
        for piece in chunks:
            for line in piece.splitlines():
                if line.startswith("data: {") and "usage" in json.loads(line[6:]):
                    usage = json.loads(line[6:])["usage"]
                    self.assertLess(len(json.dumps(usage, separators=(",", ":"))), USAGE_MAX)

    async def test_ping_becomes_alive_mark(self):
        # Anthropic pings only while the message is in progress; the pacer
        # reads the mark as "silent, not dead".
        events = [
            {"type": "message_start", "message": {"id": "msg_p", "usage": {"input_tokens": 1}}},
            {"type": "content_block_start", "index": 0, "content_block": {"type": "tool_use", "id": "toolu_0", "name": "write_file"}},
            {"type": "ping"},
            {"type": "content_block_delta", "index": 0, "delta": {"type": "input_json_delta", "partial_json": "{}"}},
            {"type": "content_block_stop", "index": 0},
            {"type": "message_delta", "delta": {"stop_reason": "tool_use"}, "usage": {"output_tokens": 1}},
            {"type": "message_stop"},
        ]
        chunks = [piece async for piece in claude_mod._translated_stream(_FakeResp(events), AsyncMock(), "claude-opus-5-5", {})]
        self.assertIn(b": fuse-alive\n\n", chunks)
        self.assertLess(chunks.index(b": fuse-alive\n\n"), next(i for i, c in enumerate(chunks) if b'"arguments":"{}"' in c))

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

    async def test_tool_call_cut_by_max_tokens_finishes_length(self):
        events = [
            {"type": "message_start", "message": {"id": "msg_4", "usage": {"input_tokens": 1}}},
            {"type": "content_block_start", "index": 0, "content_block": {"type": "tool_use", "id": "toolu_0", "name": "run"}},
            {"type": "content_block_delta", "index": 0, "delta": {"type": "input_json_delta", "partial_json": '{"command":"echo'}},
            {"type": "message_delta", "delta": {"stop_reason": "max_tokens"}, "usage": {"output_tokens": 2}},
            {"type": "message_stop"},
        ]
        body = await claude_mod._aggregate(_FakeResp(events), AsyncMock(), "claude-opus-4-6")
        self.assertEqual(body["choices"][0]["finish_reason"], "length")

    async def test_refusal_and_context_window_stop_reasons(self):
        for stop, finish in (("refusal", "content_filter"), ("model_context_window_exceeded", "length")):
            events = [
                {"type": "message_start", "message": {"id": "msg_5", "usage": {"input_tokens": 1}}},
                {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "x"}},
                {"type": "message_delta", "delta": {"stop_reason": stop}, "usage": {"output_tokens": 1}},
                {"type": "message_stop"},
            ]
            body = await claude_mod._aggregate(_FakeResp(events), AsyncMock(), "claude-opus-5-5")
            self.assertEqual(body["choices"][0]["finish_reason"], finish, stop)

    async def test_thinking_records_its_position_among_text_and_tools(self):
        events = [
            {"type": "message_start", "message": {"id": "msg_6", "usage": {"input_tokens": 1}}},
            {"type": "content_block_start", "index": 0, "content_block": {"type": "thinking", "thinking": ""}},
            {"type": "content_block_delta", "index": 0, "delta": {"type": "signature_delta", "signature": "s0"}},
            {"type": "content_block_stop", "index": 0},
            {"type": "content_block_start", "index": 1, "content_block": {"type": "tool_use", "id": "toolu_a", "name": "x"}},
            {"type": "content_block_stop", "index": 1},
            {"type": "content_block_delta", "index": 2, "delta": {"type": "text_delta", "text": "then"}},
            {"type": "content_block_start", "index": 3, "content_block": {"type": "thinking", "thinking": ""}},
            {"type": "content_block_delta", "index": 3, "delta": {"type": "signature_delta", "signature": "s1"}},
            {"type": "content_block_stop", "index": 3},
            {"type": "content_block_start", "index": 4, "content_block": {"type": "tool_use", "id": "toolu_b", "name": "x"}},
            {"type": "content_block_stop", "index": 4},
            {"type": "message_delta", "delta": {"stop_reason": "tool_use"}, "usage": {"output_tokens": 1}},
            {"type": "message_stop"},
        ]
        body = await claude_mod._aggregate(_FakeResp(events), AsyncMock(), "claude-opus-5-5")
        self.assertEqual(body["choices"][0]["message"]["claude_thinking"], [
            {"thinking": "", "signature": "s0", "tool_index": 0},
            {"thinking": "", "signature": "s1", "tool_index": 1, "after_text": True},
        ])


class _RevokeClient:
    """httpx.AsyncClient whose token endpoint rejects every refresh as revoked.
    `posts` counts token requests across instances (each refresh builds one)."""

    posts = 0

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, *args, **kwargs):
        type(self).posts += 1
        return self

    @property
    def status_code(self):
        return 400

    def json(self):
        return {"error": "invalid_grant", "error_description": "Refresh token expired"}


_TOOLS = [{"type": "function", "function": {"name": "grep", "parameters": {"type": "object", "properties": {}}}}]
_TURN1 = [{"role": "system", "content": "sys"}, {"role": "user", "content": "hi"}]
_CALL = {"role": "assistant", "content": "", "tool_calls": [{"id": "toolu_1", "type": "function", "function": {"name": "grep", "arguments": "{}"}}]}
_RESULT = {"role": "tool", "tool_call_id": "toolu_1", "content": "found"}
_REPLY1 = {"id": "msg_1", "text": "", "tool_ids": ["toolu_1"], "stop_reason": "tool_use"}


def _thread_payload(messages, tools=_TOOLS):
    return claude_mod.build_messages_payload({"messages": messages, "tools": tools}, "claude-sonnet-4-6")


def _breakpoints(value) -> int:
    return json.dumps(value).count('"cache_control"')


class ClaudeThreadTest(unittest.TestCase):
    def setUp(self):
        claude_thread.reset()

    def _first(self):
        payload = _thread_payload(_TURN1)
        send, mode = claude_thread.plan("s1", 7, payload)
        self.assertEqual(mode, "create")
        self.assertEqual(send["thread"], {"type": "create"})
        self.assertEqual(send["messages"], payload["messages"])
        claude_thread.record("s1", 7, payload, mode, _REPLY1)
        return payload

    def test_next_turn_sends_only_new_messages(self):
        first = self._first()
        payload = _thread_payload([*_TURN1, _CALL, _RESULT])
        send, mode = claude_thread.plan("s1", 7, payload)
        self.assertEqual(mode, "continue")
        self.assertEqual(send["thread"], {"type": "continue", "previous_message_id": "msg_1"})
        self.assertNotIn("system", send)
        self.assertNotIn("tools", send)
        self.assertEqual(send["messages"], payload["messages"][2:])
        self.assertEqual(send["messages"][0]["content"][0]["type"], "tool_result")
        # Fixed fields go again; create-time system/tools breakpoints count
        # against the limit of 4.
        self.assertEqual(send["model"], first["model"])
        self.assertLessEqual(_breakpoints(send["messages"]) + _breakpoints(first["system"]) + _breakpoints(first["tools"]), 4)
        self.assertLessEqual(_breakpoints(payload), 4)
        # A response id is continued once: the state is consumed.
        self.assertEqual(claude_thread.plan("s1", 7, payload)[1], "create")

    def test_anything_the_thread_does_not_hold_creates_a_new_thread(self):
        edited = [{"role": "system", "content": "sys"}, {"role": "user", "content": "hello"}, _CALL, _RESULT]
        other_reply = {**_CALL, "tool_calls": [{**_CALL["tool_calls"][0], "id": "toolu_2"}]}
        cases = {
            "other account": (8, _thread_payload([*_TURN1, _CALL, _RESULT])),
            "tools changed": (7, _thread_payload([*_TURN1, _CALL, _RESULT], tools=[])),
            "history edited": (7, _thread_payload(edited)),
            "other reply": (7, _thread_payload([*_TURN1, other_reply, {**_RESULT, "tool_call_id": "toolu_2"}])),
            "nothing new": (7, _thread_payload([*_TURN1, _CALL])),
        }
        for name, (cred, payload) in cases.items():
            with self.subTest(name):
                self._first()
                send, mode = claude_thread.plan("s1", cred, payload)
                self.assertEqual(mode, "create")
                self.assertEqual(send["messages"], payload["messages"])

    def test_agent_loop_continues_every_round(self):
        history = [{"role": "system", "content": "sys"}, {"role": "user", "content": "go"}]
        for round_no in range(5):
            payload = claude_mod.build_messages_payload({"messages": history, "tools": _TOOLS, "reasoning_effort": "high"},
                                                        "claude-opus-5-5")
            send, mode = claude_thread.plan("loop", 7, payload)
            self.assertEqual(mode, "create" if round_no == 0 else "continue", round_no)
            self.assertLessEqual(_breakpoints(send.get("messages")) + 2, 4)
            ids = [f"toolu_{round_no}_{i}" for i in range(2)]
            claude_thread.record("loop", 7, payload, mode, {"id": f"msg_{round_no}", "text": "Checking.", "tool_ids": ids,
                                                             "stop_reason": "tool_use"})
            history += [
                {"role": "assistant", "content": "Checking.", "claude_thinking": [
                    {"thinking": "t", "signature": f"sig{round_no}", "tool_index": 0},
                    {"thinking": "", "signature": f"sig{round_no}b", "tool_index": 1, "after_text": True}],
                 "tool_calls": [{"id": i, "type": "function", "function": {"name": "grep", "arguments": "{}"}} for i in ids]},
                *({"role": "tool", "tool_call_id": i, "content": "ok"} for i in ids),
            ]
            if round_no == 2:
                history.append({"role": "system", "content": "steer"})

    def test_idle_thread_and_unstored_stop_create(self):
        self._first()
        with patch("app.sources.claude_thread.time.monotonic", return_value=claude_thread.time.monotonic() + claude_thread.IDLE_S + 1):
            self.assertEqual(claude_thread.plan("s1", 7, _thread_payload([*_TURN1, _CALL, _RESULT]))[1], "create")
        claude_thread.record("s1", 7, _thread_payload(_TURN1), "create", {**_REPLY1, "stop_reason": "refusal"})
        self.assertEqual(claude_thread.plan("s1", 7, _thread_payload([*_TURN1, _CALL, _RESULT]))[1], "create")
        self.assertIsNone(claude_thread.plan(None, 7, _thread_payload(_TURN1))[1])

    def test_fallback_classifies_thread_errors(self):
        def err(code="", message="x"):
            return json.dumps({"type": "error", "error": {"type": "invalid_request_error", "message": message,
                                                          **({"details": {"error_code": code}} if code else {})}})

        self.assertEqual(claude_thread.fallback("continue", 404, err("thread_not_found"), 7), "create")
        self.assertEqual(claude_thread.fallback("continue", 400, err("thread_already_continued"), 7), "create")
        self.assertEqual(claude_thread.fallback("continue", 400, err("thread_fingerprint_mismatch"), 7), "create")
        self.assertIs(claude_thread.fallback("continue", 400, err(message="signature verification failed"), 7), False)
        self.assertIs(claude_thread.fallback("create", 404, err("thread_not_found"), 7), False)
        self.assertTrue(claude_thread.enabled(7))
        self.assertIsNone(claude_thread.fallback("create", 400, err("thread_unsupported_request"), 7))
        self.assertFalse(claude_thread.enabled(7))
        self.assertIsNone(claude_thread.plan("s1", 7, _thread_payload(_TURN1))[1])


class ClaudePoolTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        reset_health()
        claude_thread.reset()
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

    async def test_pool_denial_names_the_locked_pool(self):
        self.assertIsNone(pool_denial("claude/claude-opus-5-5", self.bob["id"], {"claude": True}))
        self.assertEqual(
            pool_denial("claude/claude-opus-5-5", self.bob["id"], {"claude": False}),
            "Claude: access denied. Connect a Claude account to use Claude models.",
        )
        self.assertIsNone(pool_denial("definitely-not-a-model", self.bob["id"], {"claude": False, "grok": False}))

    async def test_locked_pool_reports_reconnect_not_unknown_model(self):
        req = unittest.mock.Mock(scope={})
        body = {"model": "claude/claude-opus-5-5", "messages": [{"role": "user", "content": "hi"}]}
        user = {"id": self.bob["id"], "key_id": None}
        with pytest.raises(HTTPException) as caught:
            await api._invoke_chat(req, user, body)
        self.assertEqual(caught.value.status_code, 403)
        self.assertTrue(str(caught.value.detail).startswith("Claude: access denied. Connect a Claude account"))
        body["model"] = "definitely-not-a-model"
        with pytest.raises(HTTPException) as caught:
            await api._invoke_chat(req, user, body)
        self.assertEqual(caught.value.status_code, 404)

    async def test_provider_rejected_account_reads_as_sign_in_rejected(self):
        accounts.add_credential(self.sid, "mine", "tok", extra={"access_token": "tok", "refresh_token": "rt"},
                                donated_by=self.alice["id"])
        row = db.fetchone("SELECT * FROM credentials")
        claude_mod.disable_credential(row["id"], extra=row["extra"])
        req = unittest.mock.Mock(scope={})
        body = {"model": "claude/claude-opus-5-5", "messages": [{"role": "user", "content": "hi"}]}
        with pytest.raises(HTTPException) as caught:
            await api._invoke_chat(req, {"id": self.alice["id"], "key_id": None}, body)
        self.assertEqual(caught.value.status_code, 403)
        self.assertIn("Claude: account sign-in rejected.", str(caught.value.detail))
        # A user's own Remove is not a rejection: back to "connect an account".
        db.execute("DELETE FROM credentials")
        accounts.add_credential(self.sid, "mine", "tok", extra={"access_token": "tok", "refresh_token": "rt"},
                                donated_by=self.alice["id"])
        claude_mod.disable_credential(db.fetchone("SELECT id FROM credentials")["id"])
        with pytest.raises(HTTPException) as caught:
            await api._invoke_chat(req, {"id": self.alice["id"], "key_id": None}, body)
        self.assertIn("Claude: access denied.", str(caught.value.detail))

    @patch("app.sources.claude.httpx.AsyncClient", _RevokeClient)
    async def test_revoked_refresh_keeps_live_token_and_stops_reposting(self):
        expires = (datetime.now(timezone.utc) + timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M:%SZ")
        extra = {"access_token": "tok", "refresh_token": "rt-live", "expires_at": expires}
        accounts.add_credential(self.sid, "mine", "tok", extra=extra, donated_by=self.alice["id"])
        row = db.fetchone("SELECT * FROM credentials")
        _RevokeClient.posts = 0
        with self.assertLogs("app.sources.token_store", level="WARNING") as site:
            self.assertFalse(await claude_mod.refresh_one(row))
        self.assertIn("keeping it until its access token expires", "\n".join(site.output))
        # Every chat refreshes first once a token is due: the dead grant must
        # not be posted again.
        self.assertFalse(await claude_mod.refresh_one(row))
        self.assertEqual(_RevokeClient.posts, 1)
        self.assertFalse(db.fetchone("SELECT * FROM credentials")["disabled"])
        self.assertTrue(claude_mod.user_has_claude(self.alice["id"]))

    @patch("app.sources.claude.httpx.AsyncClient", _RevokeClient)
    async def test_revoked_refresh_disables_expired_token_and_logs(self):
        expires = (datetime.now(timezone.utc) - timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        extra = {"access_token": "tok", "refresh_token": "rt-expired", "expires_at": expires}
        accounts.add_credential(self.sid, "mine", "tok", extra=extra, donated_by=self.alice["id"])
        row = db.fetchone("SELECT * FROM credentials")
        with self.assertLogs("app.sources.token_store", level="WARNING") as site, \
             self.assertLogs("app.accounts", level="WARNING") as funnel:
            self.assertFalse(await claude_mod.refresh_one(row))
        self.assertTrue(db.fetchone("SELECT * FROM credentials")["disabled"])
        self.assertFalse(claude_mod.user_has_claude(self.alice["id"]))
        self.assertIn("Refresh token expired", "\n".join(site.output))
        self.assertIn("Disabled claude credential", "\n".join(funnel.output))
        self.assertEqual(accounts.cred_extra(db.fetchone("SELECT * FROM credentials"))["disabled_reason"], "rejected")

    @patch("app.sources.claude.note_pool_invoke")
    @patch("app.sources.claude.refresh_pool_quota", new_callable=AsyncMock)
    @patch("app.sources.claude.refresh_due", new_callable=AsyncMock)
    async def test_benched_pool_asks_claude_again_instead_of_replaying_the_limit(self, _due, _quota, _invoke):
        from app import provider_errors
        from app.sources import pool_pick

        cid = accounts.add_credential(self.sid, "mine", "tok", extra={"access_token": "tok", "refresh_token": "rt"},
                                      donated_by=self.alice["id"])
        now = int(datetime.now(timezone.utc).timestamp())
        pool_pick.note_failure(cid, cooldown_seconds=2 * 3600, limit=True)
        reset = now + 3 * 3600
        headers = {"anthropic-ratelimit-unified-status": "rejected",
                   "anthropic-ratelimit-unified-representative-claim": "five_hour",
                   "anthropic-ratelimit-unified-reset": str(reset)}
        sent = []

        class Client:
            def __init__(self, *args, **kwargs):
                pass

            def build_request(self, method, url, headers=None, json=None):
                return url

            async def send(self, req, stream=False):
                sent.append(req)
                return httpx.Response(429, headers=headers, json={"type": "error", "error": {
                    "type": "rate_limit_error", "message": "This request would exceed your account's rate limit."}})

            async def aclose(self):
                return None

        src = claude_mod.ClaudeSource({"id": self.sid, "name": "Claude"}, {}, [])
        with patch("app.sources.claude.httpx.AsyncClient", Client):
            result = await src.chat({"model": "claude/claude-opus-5-5", "messages": [{"role": "user", "content": "hi"}]},
                                    user_id=self.alice["id"])
        # The benched account was asked once, and Claude's live answer is reported.
        self.assertEqual(len(sent), 1)
        self.assertEqual(result.status, 429)
        body, info = provider_errors.annotate("claude", result.status, result.body)
        self.assertEqual(info["kind"], "usage_exhausted")
        self.assertTrue(body["error"]["message"].startswith("Claude: 5-hour usage limit reached; resets in "))
        self.assertEqual(body["error"]["resets_at"], reset)
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

            def build_request(self, method, url, headers=None, json=None):
                self.posts.append((url, headers, json))
                return url

            async def send(self, req, stream=False):
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

    @patch("app.sources.claude.note_pool_invoke")
    @patch("app.sources.claude.refresh_pool_quota", new_callable=AsyncMock)
    @patch("app.sources.claude.refresh_due", new_callable=AsyncMock)
    async def test_chat_without_stream_key_is_non_streaming(self, _due, _quota, _invoke):
        events = [
            {"type": "message_start", "message": {"id": "msg_n", "usage": {"input_tokens": 3}}},
            {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "yo"}},
            {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 1}},
            {"type": "message_stop"},
        ]

        class Client:
            def __init__(self, *args, **kwargs):
                pass

            def build_request(self, method, url, headers=None, json=None):
                return url

            async def send(self, req, stream=False):
                return _FakeResp(events)

            async def aclose(self):
                return None

        extra = {"access_token": "tok", "refresh_token": "rt"}
        accounts.add_credential(self.sid, "mine", "tok", extra=extra, donated_by=self.alice["id"])
        src = claude_mod.ClaudeSource({"id": self.sid, "name": "Claude"}, {}, [])
        with patch("app.sources.claude.httpx.AsyncClient", Client):
            result = await src.chat(
                {"model": "claude/claude-sonnet-4-6", "messages": [{"role": "user", "content": "hi"}]},
                user_id=self.alice["id"],
            )
        # Omitting "stream" is the OpenAI default: a JSON body, not SSE.
        self.assertEqual(result.status, 200)
        self.assertIsNone(result.stream)
        self.assertEqual(result.body["choices"][0]["message"]["content"], "yo")

    @patch("app.sources.claude.note_pool_invoke")
    @patch("app.sources.claude.refresh_due", new_callable=AsyncMock)
    async def test_signature_400_returns_actionable_error_without_rewriting_history(
        self, _due, _invoke
    ):
        err_body = json.dumps(
            {
                "error": {
                    "type": "invalid_request_error",
                    "message": "Expected thinking but signature verification failed for this conversation",
                }
            }
        ).encode()
        posts: list = []

        class Client:
            def __init__(self, *args, **kwargs):
                pass

            def build_request(self, method, url, headers=None, json=None):
                posts.append(json)
                return url

            async def send(self, req, stream=False):
                resp = AsyncMock()
                resp.status_code = 400
                resp.aread.return_value = err_body
                return resp

            async def aclose(self):
                return None

        extra = {"access_token": "tok", "refresh_token": "rt"}
        accounts.add_credential(self.sid, "mine", "tok", extra=extra, donated_by=self.alice["id"])
        src = claude_mod.ClaudeSource({"id": self.sid, "name": "Claude"}, {}, [])
        with patch("app.sources.claude.httpx.AsyncClient", Client):
            result = await src.chat(
                {
                    "model": "claude/claude-sonnet-4-6",
                    "stream": True,
                    "messages": [
                        {
                            "role": "assistant",
                            "content": "old",
                            "claude_thinking": [{"thinking": "hmm", "signature": "stale"}],
                        },
                        {"role": "user", "content": "continue"},
                    ],
                },
                user_id=self.alice["id"],
            )
        self.assertEqual(result.status, 400)
        self.assertIn("thinking signatures", result.body["error"]["message"])
        self.assertIn("new conversation", result.body["error"]["message"])
        # History is never silently rewritten: exactly one post, with the
        # replayed thinking blocks intact.
        self.assertEqual(len(posts), 1)
        first_block = posts[0]["messages"][0]["content"][0]
        self.assertEqual(first_block["type"], "thinking")
        self.assertEqual(first_block["signature"], "stale")

    async def _threaded_chat(self, replies, bodies):
        """Run each body through chat() in one session; returns the posts."""
        posts: list = []
        queue = list(replies)

        class Client:
            def __init__(self, *args, **kwargs):
                pass

            def build_request(self, method, url, headers=None, json=None):
                posts.append((headers, json))
                return url

            async def send(self, req, stream=False):
                status, payload = queue.pop(0)
                if status == 200:
                    return _FakeResp(payload)
                return httpx.Response(status, json=payload)

            async def aclose(self):
                return None

        accounts.add_credential(self.sid, "mine", "tok", extra={"access_token": "tok", "refresh_token": "rt"},
                                donated_by=self.alice["id"])
        src = claude_mod.ClaudeSource({"id": self.sid, "name": "Claude"}, {}, [])
        with patch("app.sources.claude.httpx.AsyncClient", Client), \
             patch("app.sources.claude.refresh_pool_quota", new_callable=AsyncMock):
            for body in bodies:
                result = await src.chat({"model": "claude/claude-sonnet-4-6", "stream": True, "tools": _TOOLS, **body},
                                        user_id=self.alice["id"], session_id="sess-thread")
                self.assertEqual(result.status, 200)
                async for _ in result.stream:
                    pass
        self.assertEqual(queue, [])
        return posts

    _TOOL_REPLY = [
        {"type": "message_start", "message": {"id": "msg_1", "usage": {"input_tokens": 3}}},
        {"type": "content_block_start", "index": 0, "content_block": {"type": "tool_use", "id": "toolu_1", "name": "grep"}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "input_json_delta", "partial_json": "{}"}},
        {"type": "content_block_stop", "index": 0},
        {"type": "message_delta", "delta": {"stop_reason": "tool_use"}, "usage": {"output_tokens": 1}},
        {"type": "message_stop"},
    ]
    _TEXT_REPLY = [
        {"type": "message_start", "message": {"id": "msg_2", "usage": {"input_tokens": 3}}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "done"}},
        {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 1}},
        {"type": "message_stop"},
    ]

    @patch("app.sources.claude.note_pool_invoke")
    @patch("app.sources.claude.refresh_due", new_callable=AsyncMock)
    async def test_tool_round_continues_the_thread_with_only_new_messages(self, _due, _invoke):
        posts = await self._threaded_chat(
            [(200, self._TOOL_REPLY), (200, self._TEXT_REPLY)],
            [{"messages": _TURN1}, {"messages": [*_TURN1, _CALL, _RESULT]}],
        )
        (h1, create), (h2, cont) = posts
        self.assertIn(claude_thread.BETA, h1["anthropic-beta"])
        self.assertEqual(h1["anthropic-beta"], h2["anthropic-beta"])
        self.assertEqual(create["thread"], {"type": "create"})
        self.assertIn("tools", create)
        self.assertEqual(cont["thread"], {"type": "continue", "previous_message_id": "msg_1"})
        self.assertNotIn("system", cont)
        self.assertNotIn("tools", cont)
        self.assertEqual([m["role"] for m in cont["messages"]], ["user"])
        self.assertEqual(cont["messages"][0]["content"][0]["tool_use_id"], "toolu_1")

    @patch("app.sources.claude.note_pool_invoke")
    @patch("app.sources.claude.refresh_due", new_callable=AsyncMock)
    async def test_lost_thread_replays_the_full_conversation(self, _due, _invoke):
        lost = {"type": "error", "error": {"type": "not_found_error", "message": "thread not found",
                                          "details": {"error_code": "thread_not_found"}}}
        posts = await self._threaded_chat(
            [(200, self._TOOL_REPLY), (404, lost), (200, self._TEXT_REPLY)],
            [{"messages": _TURN1}, {"messages": [*_TURN1, _CALL, _RESULT]}],
        )
        self.assertEqual(posts[1][1]["thread"]["type"], "continue")
        replay = posts[2][1]
        self.assertEqual(replay["thread"], {"type": "create"})
        self.assertEqual([m["role"] for m in replay["messages"]], ["user", "assistant", "user"])
        self.assertIn("tools", replay)

    @patch("app.sources.claude.note_pool_invoke")
    @patch("app.sources.claude.refresh_due", new_callable=AsyncMock)
    async def test_unsupported_thread_falls_back_to_plain_messages(self, _due, _invoke):
        refused = {"type": "error", "error": {"type": "invalid_request_error", "message": "threads unavailable",
                                             "details": {"error_code": "thread_unsupported_request", "unsupported": ["hipaa"]}}}
        posts = await self._threaded_chat(
            [(400, refused), (200, self._TOOL_REPLY), (200, self._TEXT_REPLY)],
            [{"messages": _TURN1}, {"messages": [*_TURN1, _CALL, _RESULT]}],
        )
        self.assertEqual(posts[0][1]["thread"], {"type": "create"})
        for headers, sent in posts[1:]:
            self.assertNotIn("thread", sent)
            self.assertNotIn(claude_thread.BETA, headers["anthropic-beta"])
        self.assertEqual(len(posts[2][1]["messages"]), 3)


if __name__ == "__main__":
    unittest.main()
