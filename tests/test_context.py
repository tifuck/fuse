"""Context windows, meters, and compaction (no live traffic)."""
from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_TMP = tempfile.mkdtemp(prefix="fuse-context-")
os.environ.setdefault("DATABASE_PATH", str(Path(_TMP) / "ctx.db"))
os.environ.setdefault("APP_SECRET", "x" * 32)

from app import context_compact, context_meter  # noqa: E402


class WindowTest(unittest.TestCase):
    def test_resolved_local_backend_and_window_survive_reload(self):
        prior = {"model": "gpt-oss:20b", "source_type": "ollama", "limit": 32768}
        self.assertEqual(context_meter.source_type_for_model("gpt-oss:20b", prior), "ollama")
        snap = context_meter.standing_meter([{"role": "user", "content": "hi"}], model="gpt-oss:20b", prior=prior)
        self.assertEqual(snap["limit"], 32768)
        self.assertEqual(snap["source_type"], "ollama")
        self.assertEqual(context_meter.source_type_for_model("gpt-5.5", prior), "chatgpt")

    def test_known_pool_models(self):
        self.assertEqual(context_meter.window_for("grok-4.6", "grok"), 500_000)
        self.assertEqual(context_meter.window_for("glm-5.3-flash", "zai"), 1_000_000)
        self.assertEqual(context_meter.window_for("gpt-5.4-mini", "chatgpt"), 400_000)
        self.assertEqual(context_meter.window_for("devin/gpt-6-astra", "devin"), 1_050_000)
        self.assertEqual(context_meter.window_for("devin/gpt-6-astra-medium", "devin"), 1_050_000)
        self.assertEqual(context_meter.window_for("devin/claude-sonnet-5", "devin"), 200_000)
        self.assertEqual(context_meter.window_for("devin/glm-5-2", "devin"), 200_000)
        self.assertEqual(context_meter.window_for("devin/glm-5-2-1m", "devin"), 1_000_000)
        self.assertEqual(context_meter.window_for("glm-5-2", "devin"), 200_000)
        self.assertEqual(context_meter.window_for("k3", "kimi"), 262_144)
        self.assertEqual(context_meter.window_for("kimi-for-coding", "kimi"), 1_048_576)
        self.assertEqual(context_meter.type_id_for_model("devin/gpt-6-astra"), "devin")
        self.assertEqual(context_meter.type_id_for_model("k3"), "kimi")

    def test_openrouter_catalog_wins(self):
        catalog = {"by_id": {"acme/fast": {"context_length": 32_768}}}
        self.assertEqual(context_meter.window_for("openrouter/acme/fast", "openrouter", catalog=catalog), 32_768)

    def test_openrouter_cached_catalog_without_caller_catalog(self):
        catalog = {"by_id": {"acme/huge": {"context_length": 1_000_000}}}
        with mock.patch.object(context_meter, "_cached_openrouter_catalog", return_value=catalog):
            self.assertEqual(context_meter.window_for("openrouter/acme/huge", "openrouter"), 1_000_000)

    def test_type_id_inferred_from_model_id(self):
        self.assertEqual(context_meter.type_id_for_model("grok-4.6"), "grok")
        self.assertEqual(context_meter.window_for("grok-4.6"), 500_000)

    def test_llama_cache_and_default(self):
        context_meter.remember_window("llama", "local-7b", 8192)
        self.assertEqual(context_meter.window_for("local-7b", "llama"), 8192)
        self.assertEqual(context_meter.window_for("mystery", "llama"), 8_192)

    def test_ollama_does_not_inherit_gpt_prefix(self):
        self.assertEqual(context_meter.window_for("gpt-oss:20b", "ollama"), 128_000)
        self.assertEqual(context_meter.window_for("gpt-5.5", "chatgpt"), 1_050_000)

    def test_override(self):
        self.assertEqual(context_meter.window_for("grok-4.6", "grok", override=12_000), 12_000)

    def test_unknown_model_uses_learned_window(self):
        # No type prefix and no cached meter: a window learned from whichever
        # source served this exact model name beats the 128k default.
        self.assertEqual(context_meter.window_for("mystery-model"), context_meter.config().default_window)
        context_meter.remember_window("llama", "mystery-model", 16_384)
        self.assertEqual(context_meter.window_for("mystery-model"), 16_384)
        self.assertEqual(context_meter.window_for("mystery-model", None), 16_384)
        # A stale entry must not win.
        context_meter._WINDOW_CACHE[("llama", "mystery-model")] = (time.time() - context_meter._WINDOW_TTL - 1, 16_384)
        self.assertEqual(context_meter.window_for("mystery-model"), context_meter.config().default_window)

    def test_known_type_beats_learned_window_of_same_name(self):
        context_meter.remember_window("llama", "grok-4.6", 8_192)
        self.assertEqual(context_meter.window_for("grok-4.6", "grok"), 500_000)
        self.assertEqual(context_meter.window_for("grok-4.6"), 500_000)


class EstimateTest(unittest.TestCase):
    def test_text_and_usage(self):
        self.assertEqual(context_meter.estimate_text("abcd"), 1)
        self.assertEqual(context_meter.used_from_usage({"prompt_tokens": 10, "completion_tokens": 5}), 15)
        # Occupancy is prompt+completion of one request. total_tokens on a
        # turn can be the sum of every tool round (billing), so it must not win.
        self.assertEqual(context_meter.used_from_usage({"total_tokens": 99, "prompt_tokens": 10, "completion_tokens": 5}), 15)
        self.assertEqual(context_meter.used_from_usage({"total_tokens": 99}), 99)

    def test_cjk_counts_about_one_token_per_char(self):
        # A flat 4-char divide undercounts CJK ~4x and delays compaction.
        self.assertEqual(context_meter.estimate_text("abcd"), 1)
        self.assertEqual(context_meter.estimate_text("你好世界" * 4), 16)
        self.assertEqual(context_meter.estimate_text("hello 你好"), 4)

    def test_attachments_not_counted_twice(self):
        # Durable rows embed attachments in content (chats.model_content);
        # the attachments key must not add a second copy of the same image.
        msg = {
            "role": "user",
            "content": [
                {"type": "text", "text": "look"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,AA"}},
            ],
            "attachments": [{"kind": "image", "data": "data:image/png;base64,AA"}],
        }
        without = {k: v for k, v in msg.items() if k != "attachments"}
        self.assertEqual(context_meter.estimate_messages([msg]), context_meter.estimate_messages([without]))

    def test_claude_thinking_blocks_count_as_thinking(self):
        text = "think " * 20
        messages = [{"role": "assistant", "content": "ok",
                     "claude_thinking": [{"thinking": text, "signature": "sig"}]}]
        parts = context_meter.occupancy_parts(messages)
        self.assertEqual(parts["thinking"], context_meter.estimate_text(text))
        self.assertEqual(context_meter.estimate_messages(messages), context_meter.estimate_messages(
            [{"role": "assistant", "content": "ok", "reasoning_content": text}]))
        # Counted once: blocks must not also land in the messages row.
        self.assertEqual(parts["messages"], context_meter.occupancy_parts(
            [{"role": "assistant", "content": "ok"}])["messages"])

    def test_grok_reasoning_is_not_occupancy(self):
        self.assertEqual(
            context_meter.used_from_usage({
                "prompt_tokens": 1000,
                "completion_tokens": 5,
                "reasoning_tokens": 700,
                "total_tokens": 1705,
            }),
            1005,
        )

    def test_messages_include_images(self):
        n = context_meter.estimate_messages(
            [
                {"role": "user", "content": [{"type": "text", "text": "hi"}, {"type": "image_url", "image_url": {"url": "x"}}]},
            ]
        )
        self.assertGreaterEqual(n, context_meter.IMAGE_TOKENS)

    def test_tools_add_to_estimate(self):
        messages = [{"role": "user", "content": "hi"}]
        tools = [{"type": "function", "function": {"name": "web_search", "parameters": {"type": "object", "properties": {"q": {"type": "string"}}}}}]
        with_tools = context_meter.meter_from_messages(messages, model="grok-4.6", type_id="grok", tools=tools)
        without = context_meter.meter_from_messages(messages, model="grok-4.6", type_id="grok")
        self.assertGreater(with_tools["used"], without["used"])
        self.assertEqual(with_tools["limit"], 500_000)

    def test_standing_preserves_retained_tool_pairs(self):
        rows = context_compact.standing_messages(
            [
                {"role": "system", "content": "sys"},
                {"role": "user", "content": "go"},
                {"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "function": {"name": "ls", "arguments": "{}"}}]},
                {"role": "tool", "tool_call_id": "c1", "content": "file.txt " * 5000},
                {"role": "assistant", "content": "done"},
            ]
        )
        self.assertEqual([m["role"] for m in rows], ["system", "user", "assistant", "tool", "assistant"])
        self.assertEqual(rows[-1]["content"], "done")
        full = context_meter.estimate_messages([
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "go"},
            {"role": "tool", "content": "file.txt " * 5000},
            {"role": "assistant", "content": "done"},
        ])
        standing = context_meter.estimate_messages(rows)
        self.assertGreaterEqual(standing, full)

    def test_standing_meter_ignores_last_round_usage(self):
        messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "go"},
            {"role": "assistant", "content": "done"},
        ]
        live = context_meter.meter_from_messages(
            messages,
            model="grok-4.6",
            type_id="grok",
            usage={"prompt_tokens": 80_000, "completion_tokens": 20_000},
        )
        standing = context_meter.standing_meter(messages, model="grok-4.6", type_id="grok")
        self.assertGreater(live["used"], 90_000)
        self.assertLess(standing["used"], 100)
        self.assertTrue(standing["standing"])
        self.assertTrue(standing["estimated"])

    def test_standing_meter_recomputes_without_inheriting_floor(self):
        first = [
            {"role": "system", "content": "sys " * 200},
            {"role": "user", "content": "one"},
            {"role": "assistant", "content": "ok"},
        ]
        prior = context_meter.standing_meter(first, model="grok-4.6", type_id="grok")
        # Simulate a stale high-water total on the prior snapshot.
        prior["used"] = 99_000
        # Durable rows carry no system prompt; the recompute must inherit the
        # overhead parts but never the stale total.
        dropped = context_meter.standing_meter(
            [{"role": "user", "content": "one"}, {"role": "assistant", "content": "ok"}, {"role": "user", "content": "two"}],
            model="grok-4.6",
            type_id="grok",
            prior=prior,
        )
        parts = dropped["parts"]
        self.assertEqual(dropped["used"], parts["system"] + parts["history"] + parts["tools"])
        self.assertLess(dropped["used"], 99_000)
        self.assertEqual(parts["system"], prior["parts"]["system"])
        # With the transcript present, the number only grows as history grows.
        later = first + [{"role": "user", "content": "two " * 40}]
        grown = context_meter.standing_meter(later, model="grok-4.6", type_id="grok", prior=prior)
        self.assertGreater(grown["parts"]["history"], dropped["parts"]["history"])

    def test_devin_delta_usage_is_not_occupancy(self):
        messages = [
            {"role": "system", "content": "sys " * 400},
            {"role": "user", "content": "hello " * 40},
            {"role": "assistant", "content": "ok"},
        ]
        estimated = context_meter.estimate_messages(messages)
        self.assertGreater(estimated, 400)
        # Last-round Devin usage is a cache/delta, not the full window. The
        # standing meter must keep the transcript estimate.
        standing = context_meter.standing_meter(messages, model="devin/glm-5-2", type_id="devin")
        self.assertGreaterEqual(standing["used"], estimated)
        self.assertEqual(standing["limit"], 200_000)
        self.assertGreater(standing["parts"]["system"], 0)

    def test_occupancy_parts_split_system_history_tools(self):
        messages = [
            {"role": "system", "content": "sys " * 40},
            {"role": "user", "content": "hello world"},
            {
                "role": "assistant",
                "content": "ok",
                "reasoning": "think " * 20,
                "tools": [
                    {"name": "run_terminal_command", "command": "ls", "preview": "a.txt b.txt"},
                    {"name": "web_search", "query": "fuse", "preview": "result"},
                ],
            },
        ]
        tools = [{"type": "function", "function": {"name": "web_search", "parameters": {"type": "object"}}}]
        parts = context_meter.occupancy_parts(messages, tools=tools)
        self.assertGreater(parts["system"], 0)
        self.assertGreater(parts["messages"], 0)
        self.assertGreater(parts["thinking"], 0)
        self.assertGreater(parts["tool_calls"], 0)
        self.assertIn("shell", parts["tool_families"])
        self.assertIn("web search", parts["tool_families"])
        self.assertEqual(parts["history"], parts["messages"] + parts["thinking"] + parts["tool_calls"])
        self.assertEqual(parts["total"], parts["system"] + parts["history"] + parts["tools"])
        snap = context_meter.standing_meter(messages, model="grok-4.6", type_id="grok", tools=tools)
        self.assertEqual(snap["parts"]["thinking"], parts["thinking"])
        self.assertEqual(snap["parts"]["tool_families"]["shell"], parts["tool_families"]["shell"])

    def test_standing_meter_inherits_schema_overhead_with_empty_tools(self):
        # Web search toggled off still means "schemas absent from durable
        # rows" — the overhead must be inherited, not zeroed.
        msgs = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "yo"}]
        prior = {"model": "grok-4.6", "limit": 500_000,
                 "parts": {"system": 1_879, "tools": 3_119}}
        snap = context_meter.standing_meter(msgs, model="grok-4.6", type_id="grok", tools=[], prior=prior)
        self.assertEqual(snap["parts"]["tools"], 3_119)
        self.assertEqual(snap["parts"]["system"], 1_879)
        self.assertEqual(snap["used"], 1_879 + snap["parts"]["history"] + 3_119)


class ThresholdTest(unittest.TestCase):
    def test_auto_at_90_percent(self):
        limit = 100_000
        self.assertFalse(context_meter.should_compact(89_000, limit))
        self.assertTrue(context_meter.should_compact(90_000, limit))
        self.assertTrue(context_meter.should_warn(80_000, limit))
        self.assertFalse(context_meter.should_warn(79_000, limit))

    def test_estimated_spike_does_not_auto_compact(self):
        limit = 128_000
        # 4-char estimate can report 157% while the last standing snapshot was 4.3k.
        self.assertFalse(
            context_meter.should_auto_compact(201_000, limit, estimated=True, prior_used=4_300)
        )
        self.assertTrue(
            context_meter.should_auto_compact(201_000, limit, estimated=False, prior_used=4_300)
        )
        self.assertTrue(
            context_meter.should_auto_compact(120_000, limit, estimated=True, prior_used=120_000)
        )

    def test_usage_not_raised_to_estimate(self):
        huge = [{"role": "system", "content": "x" * 80_000}]
        snap = context_meter.meter_from_messages(
            huge,
            model="offline",
            usage={"prompt_tokens": 4_000, "completion_tokens": 300},
            tools=[{"type": "function", "function": {"name": "web_search", "parameters": {}}}],
        )
        self.assertEqual(snap["used"], 4_300)
        self.assertFalse(snap["estimated"])

    def test_small_window_stays_near_ratio(self):
        trigger = context_meter.trigger_used(8_192)
        self.assertLess(trigger, 8_192)
        self.assertGreaterEqual(trigger, int(8_192 * 0.85))

    def test_min_window_trigger_keeps_the_reserve(self):
        # The trigger must never be raised to 100% of a MIN_WINDOW-sized
        # window; the output reserve has to stay effective there too.
        self.assertEqual(context_meter.trigger_used(1_024), 1_024 - 256)


class ProjectTest(unittest.TestCase):
    def test_checkpoint_replaces_prefix(self):
        messages = [
            {"id": "a", "role": "user", "content": "one"},
            {"id": "b", "role": "assistant", "content": "ok"},
            {"id": "c", "role": "user", "content": "two"},
            {"id": "d", "role": "assistant", "content": "done"},
            {"id": "e", "role": "user", "content": "three"},
        ]
        state = context_compact.parse_state({})
        cp = context_compact.make_checkpoint(
            summary="Did one and two.",
            through_message_id="d",
            strategy="truncate",
            trigger="manual",
        )
        state = context_compact.append_checkpoint(state, cp)
        out = context_compact.project_transcript(messages, state)
        self.assertEqual(out[0]["role"], "user")
        self.assertIn("Did one and two.", out[0]["content"])
        self.assertEqual(out[1]["content"], context_compact.COMPACT_ACK)
        self.assertEqual([m["id"] for m in out[2:]], ["e"])

    def test_rewind_drops_stale_checkpoint(self):
        messages = [
            {"id": "a", "role": "user", "content": "one"},
            {"id": "b", "role": "assistant", "content": "ok"},
        ]
        cp = context_compact.make_checkpoint(
            summary="gone",
            through_message_id="missing",
            strategy="truncate",
            trigger="auto",
        )
        out = context_compact.project_transcript(messages, {"checkpoints": [cp]})
        self.assertEqual([m["id"] for m in out], ["a", "b"])


class PruneTest(unittest.TestCase):
    def test_prunes_old_tool_output(self):
        messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "go"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "c1"}]},
            {"role": "tool", "tool_call_id": "c1", "content": "x" * 80_000},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "c2"}]},
            {"role": "tool", "tool_call_id": "c2", "content": "y" * 4_000},
        ]
        out, ids, freed = context_compact.prune_tool_outputs(messages, protect=1_000, minimum=100)
        self.assertIn("c1", ids)
        self.assertGreater(freed, 100)
        stubbed = [m for m in out if m.get("tool_call_id") == "c1"][0]
        self.assertTrue(stubbed["content"].startswith(context_compact.TOOL_STUB))
        self.assertIn("x" * 40, stubbed["content"])
        self.assertLess(len(stubbed["content"]), 80_000)
        kept = [m for m in out if m.get("tool_call_id") == "c2"][0]
        self.assertEqual(kept["content"], "y" * 4_000)


class SplitLiveTranscriptTest(unittest.TestCase):
    def test_user_assistant_only_matches_old_cut(self):
        messages = []
        for i in range(8):
            messages.append({"id": f"u{i}", "role": "user", "content": f"q{i}"})
            messages.append({"id": f"a{i}", "role": "assistant", "content": f"a{i}"})
        head, tail = context_compact.split_transcript(messages, keep_tail=4)
        self.assertEqual([m["id"] for m in tail], ["u6", "a6", "u7", "a7"])
        self.assertEqual([m["id"] for m in head], ["u0", "a0", "u1", "a1", "u2", "a2", "u3", "a3", "u4", "a4", "u5", "a5"])

    def test_keeps_tool_results_with_tail_turns(self):
        messages = []
        for i in range(8):
            messages.append({"id": f"u{i}", "role": "user", "content": f"q{i}"})
            messages.append({"id": f"a{i}", "role": "assistant", "content": "", "tool_calls": [{"id": f"c{i}"}]})
            messages.append({"role": "tool", "tool_call_id": f"c{i}", "content": f"LIVE-{i}"})
        head, tail = context_compact.split_transcript(messages, keep_tail=4)
        self.assertTrue(any(m.get("role") == "tool" for m in head))
        tail_tools = [m.get("content") for m in tail if m.get("role") == "tool"]
        self.assertIn("LIVE-7", tail_tools)
        self.assertNotIn("LIVE-0", tail_tools)
        self.assertEqual(tail[0]["role"], "user")

    def test_tool_heavy_short_transcript_is_compactable(self):
        messages = [{"role": "user", "content": "inspect"}]
        for i in range(4):
            messages.append({"role": "assistant", "content": "", "tool_calls": [{"id": f"c{i}"}]})
            messages.append({"role": "tool", "tool_call_id": f"c{i}", "content": f"LIVE-{i}"})
        self.assertTrue(context_compact.compactable_transcript(messages, keep_tail=6, provider=True))
        self.assertFalse(context_compact.compactable_transcript(messages, keep_tail=6, provider=False))
        head, tail = context_compact.split_transcript(messages, keep_tail=6)
        self.assertTrue(head)
        self.assertTrue(any(m.get("role") == "tool" for m in tail))
        self.assertIn("LIVE-3", [m.get("content") for m in tail if m.get("role") == "tool"])
        self.assertNotIn("LIVE-0", [m.get("content") for m in tail if m.get("role") == "tool"])


class LiveCompactTest(unittest.IsolatedAsyncioTestCase):
    def _live_transcript(self):
        current = [{"role": "system", "content": "sys"}]
        for i in range(8):
            current.append({"id": f"u{i}", "role": "user", "content": f"q{i}"})
            current.append(
                {
                    "id": f"a{i}",
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [{"id": f"c{i}", "function": {"name": "read_file"}}],
                }
            )
            current.append({"role": "tool", "tool_call_id": f"c{i}", "content": f"SECRET_LIVE_{i}_" + ("x" * 20_000)})
        return current

    async def test_compact_current_keeps_live_tool_results(self):
        from unittest.mock import patch

        from starlette.requests import Request as StarletteRequest

        from app.chat_runtime import _compact_current

        current = self._live_transcript()
        thread = {
            "messages": [
                {"id": f"tu{i}", "role": "user", "content": "persisted no tools"}
                for i in range(8)
            ]
            + [{"id": f"ta{i}", "role": "assistant", "content": "persisted answer"} for i in range(8)]
        }

        async def fake_sum(head, instructions="", **_kwargs):
            blob = str(head)
            self.assertIn("SECRET_LIVE_0_" + ("x" * 20_000), blob)
            return "earlier work summarized"

        async def receive():
            return {"type": "http.disconnect"}

        request = StarletteRequest(
            {"type": "http", "method": "POST", "path": "/", "headers": [], "client": ("127.0.0.1", 1)},
            receive,
        )
        with patch("app.chat_runtime.summarize_conversation", fake_sum):
            rebuilt, _meter, kind = await _compact_current(
                current,
                request=request,
                user={"id": 1},
                model="offline",
                type_id="openai",
                trigger="manual",
                thread_id=None,
            )
        self.assertIn(kind, ("summarize", "truncate"))
        self.assertFalse([m for m in rebuilt if m.get("role") == "tool"])
        self.assertFalse(any(m.get("role") != "user" and "SECRET_LIVE_" in str(m.get("content") or "") for m in rebuilt))
        self.assertFalse(any("persisted no tools" in str(m.get("content") or "") for m in rebuilt))

    async def test_compact_current_child_never_writes_thread_state(self):
        from unittest.mock import patch

        from starlette.requests import Request as StarletteRequest

        from app.chat_runtime import _compact_current

        async def fake_sum(head, instructions="", **_kwargs):
            return "child handoff"

        request = StarletteRequest(
            {"type": "http", "method": "POST", "path": "/", "headers": [], "client": ("127.0.0.1", 1)},
            lambda: None,
        )
        current = self._live_transcript()
        with (
            patch("app.chat_runtime.summarize_conversation", fake_sum),
            patch("app.chat_store.apply_compaction_checkpoint") as apply_cp,
            patch("app.chat_store.save_compaction") as save_comp,
        ):
            _rebuilt, _meter, kind = await _compact_current(
                current,
                request=request,
                user={"id": 1},
                model="offline",
                type_id="openai",
                trigger="manual",
                thread_id=42,
                is_child=True,
            )
        self.assertIn(kind, ("summarize", "truncate"))
        apply_cp.assert_not_called()
        save_comp.assert_not_called()

    async def test_compact_current_parent_writes_thread_state(self):
        from unittest.mock import patch

        from starlette.requests import Request as StarletteRequest

        from app.chat_runtime import _compact_current

        async def fake_sum(head, instructions="", **_kwargs):
            return "parent handoff"

        request = StarletteRequest(
            {"type": "http", "method": "POST", "path": "/", "headers": [], "client": ("127.0.0.1", 1)},
            lambda: None,
        )
        current = self._live_transcript()
        with (
            patch("app.chat_runtime.summarize_conversation", fake_sum),
            patch("app.chat_store.apply_compaction_checkpoint") as apply_cp,
        ):
            _rebuilt, meter, kind = await _compact_current(
                current,
                request=request,
                user={"id": 1},
                model="offline",
                type_id="openai",
                trigger="manual",
                thread_id=42,
            )
        self.assertIn(kind, ("summarize", "truncate"))
        apply_cp.assert_called_once()
        self.assertEqual(apply_cp.call_args.args[1], 42)

    async def test_compact_current_retries_unpersisted_checkpoint_later(self):
        import asyncio
        from unittest.mock import patch

        from starlette.requests import Request as StarletteRequest

        from app import chat_runtime
        from app.chat_runtime import _compact_current

        async def fake_sum(head, instructions="", **_kwargs):
            return "paid handoff"

        request = StarletteRequest(
            {"type": "http", "method": "POST", "path": "/", "headers": [], "client": ("127.0.0.1", 1)},
            lambda: None,
        )
        calls = []

        def flaky_apply(*args, **kwargs):
            calls.append(args)
            if len(calls) == 1:
                raise RuntimeError("revision conflict")

        with (
            patch("app.chat_runtime.summarize_conversation", fake_sum),
            patch("app.chat_store.apply_compaction_checkpoint", flaky_apply),
            patch.object(chat_runtime, "CHECKPOINT_LATER_DELAYS", (0, 0)),
        ):
            await _compact_current(
                self._live_transcript(), request=request, user={"id": 1}, model="offline",
                type_id="openai", trigger="manual", thread_id=42,
            )
            for _ in range(20):
                if len(calls) == 2:
                    break
                await asyncio.sleep(0.01)
        # The paid summary is applied again after the first write failed.
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[1], calls[0])
        self.assertNotIn((1, 42), chat_runtime._PENDING_CHECKPOINTS)

    async def test_compact_current_honors_window_override(self):
        from unittest.mock import patch

        from starlette.requests import Request as StarletteRequest

        from app.chat_runtime import _compact_current

        async def fake_sum(head, instructions="", **_kwargs):
            return "handoff"

        request = StarletteRequest(
            {"type": "http", "method": "POST", "path": "/", "headers": [], "client": ("127.0.0.1", 1)},
            lambda: None,
        )
        current = self._live_transcript()
        with patch("app.chat_runtime.summarize_conversation", fake_sum):
            _rebuilt, meter, _kind = await _compact_current(
                current,
                request=request,
                user={"id": 1},
                model="offline",
                type_id=None,
                trigger="manual",
                thread_id=None,
                override=64_000,
            )
        self.assertEqual(meter["limit"], 64_000)
        self.assertEqual(meter["auto_at"], context_meter.trigger_used(64_000))


class StrategyTest(unittest.IsolatedAsyncioTestCase):
    async def test_truncate_fallback_without_summarizer(self):
        messages = []
        for i in range(12):
            messages.append({"id": f"u{i}", "role": "user", "content": f"ask {i} " + "word " * 200})
            messages.append({"id": f"a{i}", "role": "assistant", "content": f"ans {i} " + "word " * 200})
        cp = await context_compact.build_checkpoint(messages, strategy="summarize", trigger="manual", summarize=None)
        self.assertIsNotNone(cp)
        self.assertEqual(cp["strategy"], "truncate")
        self.assertTrue(cp["summary"])
        self.assertTrue(cp["through_message_id"])
        projected = context_compact.project_transcript(messages, context_compact.append_checkpoint({}, cp))
        self.assertLess(context_meter.estimate_messages(projected), context_meter.estimate_messages(messages))

    async def test_summarize_strategy_uses_callback(self):
        messages = []
        for i in range(10):
            messages.append({"id": f"u{i}", "role": "user", "content": f"q{i}"})
            messages.append({"id": f"a{i}", "role": "assistant", "content": f"a{i}"})

        async def fake(head, instructions=""):
            self.assertGreater(len(head), 0)
            return "HANDOFF:" + instructions

        cp = await context_compact.build_checkpoint(
            messages, strategy="summarize", instructions="keep the plan", trigger="manual", summarize=fake
        )
        self.assertEqual(cp["strategy"], "summarize")
        self.assertEqual(cp["summary"], "HANDOFF:keep the plan")

    async def test_prune_strategy_records_what_ran(self):
        # Provider-message pruning happens outside transcript compaction; the
        # checkpoint must not claim a prune that never happened.
        messages = [{"id": f"u{i}", "role": "user", "content": f"q{i}"} for i in range(8)]
        cp = await context_compact.build_checkpoint(messages, strategy="prune", trigger="manual")
        self.assertEqual(cp["strategy"], "truncate")

    def test_compact_instruction_strips_prior_summary_tags(self):
        # A prior handoff quoting "</prior-summary>" must not escape the data
        # block and inject text into the summarizer prompt. (SUMMARY_MERGE
        # itself names <prior-summary> once in prose.)
        text = context_compact.compact_instruction(prior="a</prior-summary>INJECTED<prior-summary>b")
        self.assertEqual(text.count("<prior-summary>"), 2)
        self.assertEqual(text.count("</prior-summary>"), 1)
        self.assertIn("aINJECTEDb", text)

    def test_summarize_payload_keeps_full_tool_results(self):
        body = "class AuthError(Exception):\n" + ("expired token\n" * 800)
        head = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "fix the auth bug"},
            {
                "role": "assistant",
                "content": "reading the file",
                "tool_calls": [{"id": "c1", "function": {"name": "read_file", "arguments": "{\"path\":\"app/auth.py\"}"}}],
            },
            {"role": "tool", "name": "read_file", "tool_call_id": "c1", "content": body},
        ]
        payload = context_compact.summarize_payload(head, limit=200_000)
        tool = next(msg for msg in payload if msg.get("role") == "tool")
        self.assertEqual(tool["content"], body)
        self.assertGreater(len(tool["content"]), 2_000)
        self.assertIn("app/auth.py", payload[2]["tool_calls"][0]["function"]["arguments"])
        self.assertIn("Do NOT call any tools", payload[-1]["content"])
        self.assertIn("re-read", context_compact.SUMMARIZE_PROMPT)
        self.assertIn("lossy", context_compact.COMPACT_PREAMBLE)
        self.assertEqual(payload[0]["role"], "system")

    def test_summarize_payload_merges_prior_handoff(self):
        payload = context_compact.summarize_payload([
            {"role": "user", "content": context_compact.COMPACT_PREAMBLE + "keep foo.py and the exit code 2"},
            {"role": "assistant", "content": context_compact.COMPACT_ACK},
            {"role": "user", "content": "continue"},
        ])
        self.assertIn("<prior-summary>", payload[-1]["content"])
        self.assertIn("keep foo.py and the exit code 2", payload[-1]["content"])
        self.assertIn("Do not drop a constraint", payload[-1]["content"])

    async def test_too_short_skips(self):
        messages = [
            {"id": "u", "role": "user", "content": "hi"},
            {"id": "a", "role": "assistant", "content": "yo"},
        ]
        self.assertIsNone(await context_compact.build_checkpoint(messages, trigger="manual"))


class MeterTest(unittest.TestCase):
    def test_provider_input_contracts(self):
        for provider in ("grok", "chatgpt", "zai", "openrouter", "openai", "llama", "ollama"):
            with self.subTest(provider=provider):
                cal = context_meter.prompt_calibration({"input_tokens": 5000}, 2500, model="model", type_id=provider)
                self.assertEqual(cal["prompt_tokens"], 5000)
        self.assertIsNone(context_meter.prompt_calibration({"prompt_tokens": 50, "cached_tokens": 5000}, 2500,
            model="devin/model", type_id="devin"))

    def test_calibration_uses_inclusive_input_not_billing_or_cache_again(self):
        rows = [{"role": "user", "content": "x" * 400}]
        raw = context_meter.meter_from_messages(rows, model="glm-5.3")
        calibration = context_meter.prompt_calibration({"prompt_tokens": 208, "cached_tokens": 200,
            "completion_tokens": 5000, "total_tokens": 999999}, raw["used"], model="glm-5.3", type_id="zai")
        snap = context_meter.meter_from_messages(rows, model="glm-5.3", calibration=calibration)
        self.assertEqual(snap["used"], 208)
        self.assertTrue(snap["estimated"])
        self.assertEqual(snap["basis"], "provider_calibrated")
        changed = context_meter.meter_from_messages(rows, model="grok-4.6", calibration=calibration)
        self.assertEqual(changed["used"], raw["used"])
        self.assertIsNone(context_meter.prompt_calibration({"total_tokens": 999999}, 104, model="glm-5.3", type_id="zai"))

    def test_standing_calibration_survives_reload_without_compounding_overhead(self):
        rows = [{"role": "system", "content": "s" * 400}, {"role": "user", "content": "x" * 400}]
        raw = context_meter.meter_from_messages(rows, model="glm-5.3")
        cal = context_meter.prompt_calibration({"prompt_tokens": raw["used"] * 2}, raw["used"], model="glm-5.3", type_id="zai")
        prior = context_meter.calibrate_meter(raw, cal)
        for _ in range(4):
            prior = context_meter.standing_meter(rows[1:], model="glm-5.3", prior=prior)
            self.assertEqual(prior["used"], raw["used"] * 2)
        smaller = context_meter.standing_meter([{"role": "user", "content": "short handoff"}], model="glm-5.3", prior=prior)
        self.assertLess(smaller["used"], prior["used"])

    def test_public_config_and_meter(self):
        cfg = context_meter.public_config()
        self.assertEqual(cfg["auto_ratio"], 0.9)
        self.assertEqual(cfg["warn_ratio"], 0.8)
        snap = context_meter.meter(used=90_000, limit=100_000, model="grok-4.6")
        self.assertTrue(snap["auto"])
        self.assertTrue(snap["warn"])
        self.assertEqual(snap["limit"], 100_000)


class ClipToFitTest(unittest.TestCase):
    def test_clipping_uses_calibrated_budget(self):
        rows = [{"role": "user", "content": "x" * 40000}]
        cal = context_meter.prompt_calibration({"prompt_tokens": 20008}, 10004, model="m", type_id="zai")
        out, clipped = context_compact.clip_to_fit(rows, limit=16000, reserve=2000, calibration=cal)
        self.assertGreater(clipped, 0)
        self.assertLessEqual(context_meter.estimate_messages(out) * 2, 14000)

    def test_passthrough_when_small(self):
        rows = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
        ]
        out, clipped = context_compact.clip_to_fit(rows, limit=128_000)
        self.assertEqual(clipped, 0)
        self.assertIs(out, rows)

    def test_clips_single_oversized_message_with_marker(self):
        huge = "x" * 400_000
        rows = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": huge},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"id": "c1", "function": {"name": "read_file", "arguments": "{}"}}],
            },
            {"role": "tool", "tool_call_id": "c1", "content": "ok"},
        ]
        out, clipped = context_compact.clip_to_fit(rows, limit=20_000, reserve=4_000)
        self.assertGreaterEqual(clipped, 1)
        self.assertEqual(len(out), len(rows))
        user = out[1]
        self.assertLess(len(user["content"]), 120_000)
        self.assertIn("truncated to fit", user["content"])
        self.assertTrue(user["content"].startswith("xxx"))
        self.assertLess(context_meter.estimate_messages(out), 20_000)
        # Structural rows untouched, pairing intact, input not mutated.
        self.assertEqual(out[2]["tool_calls"][0]["id"], "c1")
        self.assertEqual(out[3]["content"], "ok")
        self.assertEqual(rows[1]["content"], huge)

    def test_halves_largest_until_fits(self):
        rows = [
            {"role": "system", "content": "sys"},
        ]
        for i in range(4):
            rows.append({"role": "user", "content": f"msg{i} " + "y" * 40_000})
        out, clipped = context_compact.clip_to_fit(rows, limit=20_000, reserve=4_000)
        self.assertGreaterEqual(clipped, 1)
        self.assertLessEqual(context_meter.estimate_messages(out), 16_000)
        for i, row in enumerate(out):
            if row.get("role") == "user":
                self.assertLessEqual(len(row["content"]), 40_100)

    def test_clips_toolcall_text_but_not_system_or_the_calls(self):
        # A long reply that also carries a tool call used to be skipped, so the
        # summary request sent it whole and overflowed. The text is clipped;
        # the call list and system rows stay byte-identical.
        calls = [{"id": "c1", "function": {"name": "t", "arguments": "{}"}}]
        rows = [
            {"role": "system", "content": "s" * 200_000},
            {"role": "assistant", "content": "a" * 200_000, "tool_calls": calls},
        ]
        out, clipped = context_compact.clip_to_fit(rows, limit=20_000)
        self.assertGreaterEqual(clipped, 1)
        self.assertIs(out[0], rows[0])
        self.assertLess(len(out[1]["content"]), 120_000)
        self.assertIn("truncated to fit", out[1]["content"])
        self.assertEqual(out[1]["tool_calls"], calls)
        self.assertEqual(rows[1]["content"], "a" * 200_000)
        self.assertLess(context_meter.estimate_messages(out[1:]), 20_000)

    def test_frozen_prefix_is_never_reclipped(self):
        old = "O" * 50_000
        rows = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "go"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"id": "c1", "function": {"name": "t", "arguments": "{}"}}],
            },
            {"role": "tool", "tool_call_id": "c1", "content": old},
        ]
        grown = [*rows, {"role": "user", "content": "N" * 40_000}]
        out, clipped = context_compact.clip_to_fit(grown, limit=20_000, reserve=4_000, frozen=len(rows))
        self.assertGreater(clipped, 0)
        # The already-sent tool body replays byte-identical; only the new row shrank.
        self.assertEqual(out[3]["content"], old)
        self.assertLess(len(out[4]["content"]), 40_000)
        # Nothing clippable left: over-budget frozen history is compaction's job.
        out2, clipped2 = context_compact.clip_to_fit(rows, limit=8_000, reserve=1_000, frozen=len(rows))
        self.assertEqual(clipped2, 0)
        self.assertIs(out2, rows)


class DurableCheckpointTest(unittest.TestCase):
    def _durable(self, n=12):
        rows = [{"id": f"u{i}", "role": "user", "content": f"ask {i} " + "w" * 500} for i in range(n)]
        rows += [{"id": f"a{i}", "role": "assistant", "content": f"ans {i} " + "w" * 500} for i in range(n)]
        return rows

    def test_twin_anchors_durable_projection(self):
        live = {"summary": "the handoff", "strategy": "summarize", "trigger": "auto", "tokens_before": 9_000}
        cp = context_compact.durable_checkpoint(self._durable(), live)
        self.assertIsNotNone(cp)
        self.assertFalse(cp["provider"])
        self.assertTrue(cp["through_message_id"].startswith(("u", "a")))
        projected = context_compact.project_transcript(self._durable(), context_compact.append_checkpoint({}, cp))
        self.assertEqual(len(projected), 2)
        self.assertIn("the handoff", projected[0]["content"])
        self.assertLess(context_meter.estimate_messages(projected), context_meter.estimate_messages(self._durable()))

    def test_twin_anchors_before_held_user_rows(self):
        durable = self._durable()
        durable.append({"id": "new", "role": "user", "content": "the new ask"})
        cp = context_compact.durable_checkpoint(durable, {"summary": "the handoff"}, held=1)
        self.assertEqual(cp["through_message_id"], "a11")
        projected = context_compact.project_transcript(durable, context_compact.append_checkpoint({}, cp))
        self.assertEqual(projected[-1]["id"], "new")

    def test_twin_needs_ids_and_enough_rows(self):
        no_ids = [{"role": "user", "content": "q"} for _ in range(20)]
        self.assertIsNone(context_compact.durable_checkpoint(no_ids, {"summary": "s"}))
        short = [{"id": "u0", "role": "user", "content": "q"}]
        self.assertIsNone(context_compact.durable_checkpoint(short, {"summary": "s"}))
        self.assertIsNone(context_compact.durable_checkpoint(self._durable(), {"summary": "  "}))

    def test_state_round_trip_drops_legacy_prune_ids(self):
        state = context_compact.parse_state({"pruned_tool_ids": ["a", "b"], "keep_tail": 6})
        self.assertNotIn("pruned_tool_ids", state)
        cp = context_compact.durable_checkpoint(self._durable(), {"summary": "s"})
        state = context_compact.append_checkpoint(state, cp)
        self.assertIn("checkpoints", context_compact.dump_state(state))


class DurableCompactCurrentTest(unittest.IsolatedAsyncioTestCase):
    async def test_auto_compact_recheck_uses_calibrated_input(self):
        from app.chat_runtime import _compact_current
        rows = [{"role": "user", "content": "x" * 400} for _ in range(20)]
        raw = context_meter.estimate_messages(rows)
        calibration = context_meter.prompt_calibration({"prompt_tokens": raw * 4}, raw, model="offline", type_id="openai")
        with mock.patch("app.chat_runtime.summarize_conversation", new_callable=mock.AsyncMock, return_value="handoff") as summarize:
            _, snap, kind = await _compact_current(rows, request=None, user={"id": 1},
                model="offline", type_id="openai", trigger="auto", thread_id=None,
                override=8192, calibration=calibration)
        summarize.assert_awaited_once()
        self.assertIn(kind, ("summarize", "truncate"))
        self.assertEqual(snap["basis"], "provider_calibrated")

    async def test_boot_compact_persists_durable_twin(self):
        from unittest.mock import patch

        from starlette.requests import Request as StarletteRequest

        from app.chat_runtime import _compact_current

        current = [{"role": "system", "content": "sys"}]
        for i in range(10):
            current.append({"role": "user", "content": f"q{i} " + "w" * 400})
            current.append({"role": "assistant", "content": f"a{i} " + "w" * 400})
        durable = [
            {"id": f"du{i}", "role": "user", "content": f"q{i} " + "w" * 400} for i in range(10)
        ] + [{"id": f"da{i}", "role": "assistant", "content": f"a{i} " + "w" * 400} for i in range(10)]

        async def fake_sum(request, user, model, head, instructions="", **_kwargs):
            return "durable handoff"

        request = StarletteRequest(
            {"type": "http", "method": "POST", "path": "/", "headers": [], "client": ("127.0.0.1", 1)},
            lambda: None,
        )
        with (
            patch("app.chat_runtime.summarize_conversation", fake_sum),
            patch("app.chat_store.apply_compaction_checkpoint") as apply_cp,
        ):
            _rebuilt, _meter, kind = await _compact_current(
                current,
                request=request,
                user={"id": 1},
                model="offline",
                type_id="openai",
                trigger="auto",
                thread_id=42,
                override=2048,
                durable_messages=durable,
            )
        self.assertIn(kind, ("summarize", "truncate"))
        apply_cp.assert_called_once()
        persisted = apply_cp.call_args.args[2]
        self.assertFalse(persisted["provider"])
        self.assertTrue(persisted["through_message_id"])
        self.assertIn("durable handoff", persisted["summary"])
        # The durable twin really projects the next turn.
        state = context_compact.append_checkpoint({}, persisted)
        projected = context_compact.project_transcript(durable, state)
        self.assertLess(context_meter.estimate_messages(projected), context_meter.estimate_messages(durable))

    async def test_without_durable_rows_keeps_provider_checkpoint(self):
        from unittest.mock import patch

        from starlette.requests import Request as StarletteRequest

        from app.chat_runtime import _compact_current

        current = [{"role": "system", "content": "sys"}]
        for i in range(10):
            current.append({"role": "user", "content": f"q{i} " + "w" * 400})
            current.append({"role": "assistant", "content": f"a{i} " + "w" * 400})

        async def fake_sum(request, user, model, head, instructions="", **_kwargs):
            return "live handoff"

        request = StarletteRequest(
            {"type": "http", "method": "POST", "path": "/", "headers": [], "client": ("127.0.0.1", 1)},
            lambda: None,
        )
        with (
            patch("app.chat_runtime.summarize_conversation", fake_sum),
            patch("app.chat_store.apply_compaction_checkpoint") as apply_cp,
        ):
            _rebuilt, _meter, kind = await _compact_current(
                current,
                request=request,
                user={"id": 1},
                model="offline",
                type_id="openai",
                trigger="auto",
                thread_id=42,
                override=2048,
            )
        self.assertIn(kind, ("summarize", "truncate"))
        persisted = apply_cp.call_args.args[2]
        self.assertTrue(persisted["provider"])

    async def test_compaction_never_drops_loaded_skill_rows(self):
        from unittest.mock import patch

        from starlette.requests import Request as StarletteRequest

        from app.chat_runtime import _compact_current

        skill_pair = [
            {"role": "assistant", "content": "", "tool_calls": [
                {"id": "call_s1", "type": "function",
                 "function": {"name": "load_skill", "arguments": '{"name":"ponytail"}'}}]},
            {"role": "tool", "tool_call_id": "call_s1", "content": "Skill body stays."},
        ]
        current = [{"role": "system", "content": "sys"}, {"role": "user", "content": "go"}, *skill_pair]
        for i in range(10):
            current.append({"role": "user", "content": f"q{i} " + "w" * 400})
            current.append({"role": "assistant", "content": f"a{i} " + "w" * 400})

        async def fake_sum(request, user, model, head, instructions="", **_kwargs):
            return "handoff"

        request = StarletteRequest(
            {"type": "http", "method": "POST", "path": "/", "headers": [], "client": ("127.0.0.1", 1)},
            lambda: None,
        )
        with (
            patch("app.chat_runtime.summarize_conversation", fake_sum),
            patch("app.chat_store.apply_compaction_checkpoint"),
        ):
            rebuilt, _meter, kind = await _compact_current(
                current, request=request, user={"id": 1}, model="offline", type_id="openai",
                trigger="auto", thread_id=42, override=2048)
        self.assertIn(kind, ("summarize", "truncate"))
        calls = [c for m in rebuilt if m.get("role") == "assistant"
                 for c in m.get("tool_calls") or []]
        self.assertEqual([c["function"]["name"] for c in calls], ["load_skill"])
        results = [m for m in rebuilt if m.get("role") == "tool"]
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["tool_call_id"], calls[0]["id"])
        self.assertEqual(results[0]["content"], "Skill body stays.")
        # Nothing followed the ACK, so the request still ends on a user row.
        self.assertEqual(rebuilt[-1], {"role": "user", "content": context_compact.COMPACT_CONTINUE})

    async def _compact(self, current, **kwargs):
        from unittest.mock import patch

        from starlette.requests import Request as StarletteRequest

        from app.chat_runtime import _compact_current

        seen = []

        async def fake_sum(request, user, model, head, instructions="", **_kwargs):
            seen.append(head)
            return "handoff"

        request = StarletteRequest(
            {"type": "http", "method": "POST", "path": "/", "headers": [], "client": ("127.0.0.1", 1)},
            lambda: None,
        )
        with (
            patch("app.chat_runtime.summarize_conversation", fake_sum),
            patch("app.chat_store.apply_compaction_checkpoint") as apply_cp,
        ):
            rebuilt, _meter, kind = await _compact_current(
                current, request=request, user={"id": 1}, model="offline", type_id="openai",
                trigger="auto", thread_id=42, override=2048, **kwargs)
        self.assertEqual(kind, "summarize")
        return rebuilt, seen[0], apply_cp

    async def test_boot_compact_resends_the_new_user_turn_verbatim(self):
        current = [{"role": "system", "content": "sys"}]
        for i in range(10):
            current.append({"role": "user", "content": f"q{i} " + "w" * 400})
            current.append({"role": "assistant", "content": f"a{i} " + "w" * 400})
        ask = {"role": "user", "content": [
            {"type": "text", "text": "what is in this picture?"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}]}
        current.append(ask)
        durable = [{"id": f"d{i}", "role": row["role"], "content": row["content"]}
                   for i, row in enumerate(current[1:])]
        rebuilt, summarized, apply_cp = await self._compact(current, durable_messages=durable)
        self.assertNotIn(ask, summarized)
        self.assertEqual([m["role"] for m in rebuilt], ["system", "user", "assistant", "user"])
        self.assertTrue(rebuilt[1]["content"].startswith(context_compact.COMPACT_PREAMBLE))
        self.assertEqual(rebuilt[-1], ask)
        persisted = apply_cp.call_args.args[2]
        self.assertEqual(persisted["through_message_id"], durable[-2]["id"])

    async def test_mid_run_compact_ends_on_a_user_row(self):
        current = [{"role": "system", "content": "sys"}, {"role": "user", "content": "go"}]
        for i in range(6):
            current += _tool_round(i, 2_000)
        rebuilt, _summarized, _apply = await self._compact(current)
        self.assertEqual([m["role"] for m in rebuilt], ["system", "user", "assistant", "user"])
        self.assertEqual(rebuilt[-1]["content"], context_compact.COMPACT_CONTINUE)


class RetainedSkillRowsTest(unittest.TestCase):
    @staticmethod
    def _pair(cid, name, body, file=""):
        args = '{"name": "%s"%s}' % (name, f', "file": "{file}"' if file else "")
        return [
            {"role": "assistant", "content": "", "tool_calls": [
                {"id": cid, "type": "function", "function": {"name": "load_skill", "arguments": args}}]},
            {"role": "tool", "tool_call_id": cid, "content": body},
        ]

    def test_keeps_one_pair_per_skill_newest_wins(self):
        messages = [
            {"role": "user", "content": "go"},
            *self._pair("c1", "ponytail", "old body"),
            {"role": "assistant", "content": "mid"},
            *self._pair("c2", "ponytail", "new body"),
            *self._pair("c3", "debug", "debug body", file="references/a.md"),
            *self._pair("c4", "debug", "root body"),
            {"role": "assistant", "content": "", "tool_calls": [
                {"id": "c5", "type": "function", "function": {"name": "grep", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "c5", "content": "grep output"},
        ]
        rows = context_compact.retained_skill_rows(messages)
        self.assertEqual(len(rows), 6)  # ponytail + debug root + debug file=, one pair each
        for i in range(0, len(rows), 2):
            call = rows[i]["tool_calls"][0]
            self.assertEqual(rows[i]["role"], "assistant")
            self.assertEqual(rows[i + 1]["tool_call_id"], call["id"])
            self.assertEqual(call["function"]["name"], "load_skill")
            self.assertTrue(call["id"].startswith("skill_"))
        bodies = {rows[i + 1]["content"] for i in range(0, len(rows), 2)}
        self.assertEqual(bodies, {"new body", "debug body", "root body"})

    def test_ignores_unpaired_and_foreign_rows(self):
        self.assertEqual(context_compact.retained_skill_rows([]), [])
        self.assertEqual(context_compact.retained_skill_rows([
            {"role": "assistant", "content": "", "tool_calls": [
                {"id": "c1", "type": "function", "function": {"name": "grep", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "c1", "content": "out"},
        ]), [])
        self.assertEqual(context_compact.retained_skill_rows([
            {"role": "assistant", "content": "", "tool_calls": [
                {"id": "c1", "type": "function", "function": {"name": "load_skill", "arguments": '{"name":"x"}'}}]},
        ]), [])


class ContinuationSystemRowTest(unittest.TestCase):
    """Compaction must not carry (or need to carry) a system row: the request
    path re-prepends the system prompt, which is what keeps project AGENTS.md
    in the conversation after a handoff."""

    def test_continuation_has_no_system_row(self):
        rows = context_compact.continuation("did the work")
        self.assertEqual([r["role"] for r in rows], ["user", "assistant"])
        self.assertTrue(rows[0]["content"].startswith(context_compact.COMPACT_PREAMBLE))

    def test_project_instructions_survive_compaction_via_system_prompt(self):
        from app.chat_prompts import build_system_prompt

        agents = "# rules\n- run tests with `make test`"
        rebuilt = context_compact.continuation("did the work")
        system = build_system_prompt(
            instructions="", profile="", thread_context="", tools=["web_search"],
            coding=True, project_session=True,
            project_agents=agents, project_agents_name="AGENTS.md",
        )
        work = [{"role": "system", "content": system}, *rebuilt]
        self.assertIn("make test", work[0]["content"])
        self.assertIn("AGENTS.md", work[0]["content"])
        self.assertTrue(work[1]["content"].startswith(context_compact.COMPACT_PREAMBLE))

    def test_preamble_does_not_point_at_disabled_transcript_tool(self):
        self.assertNotIn("read_transcript", context_compact.COMPACT_PREAMBLE)
        self.assertNotIn("Fuse thread", context_compact.COMPACT_PREAMBLE)

    def test_saved_retired_notices_replay_as_the_current_one(self):
        from app import chat_history

        current = context_compact.COMPACT_PREAMBLE
        cut = current.index("The handoff is lossy")
        for sentence in context_compact._RETIRED_PREAMBLE_SENTENCES:
            saved = current[:cut] + sentence + current[cut:] + "did the work"
            sent = chat_history.for_model([{"role": "user", "content": saved}], "m")[0]["content"]
            self.assertEqual(sent, current + "did the work")
            payload = context_compact.summarize_payload([{"role": "user", "content": saved},
                                                         {"role": "assistant", "content": context_compact.COMPACT_ACK}])
            self.assertEqual(payload[0]["content"], current + "did the work")
        # Only compaction notices are rewritten, and saved rows are left as they are.
        quoted = {"role": "user", "content": "why does it say: " + context_compact._RETIRED_PREAMBLE_SENTENCES[1]}
        self.assertEqual(chat_history.for_model([quoted], "m")[0], quoted)
        saved_row = {"role": "user", "content": current[:cut] + context_compact._RETIRED_PREAMBLE_SENTENCES[0] + current[cut:]}
        before = dict(saved_row)
        chat_history.for_model([saved_row], "m")
        self.assertEqual(saved_row, before)


def _tool_round(i: int, size: int) -> list[dict]:
    return [
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": f"c{i}", "type": "function", "function": {"name": "read", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": f"c{i}", "content": "x" * size},
    ]


class SubagentHandoffTest(unittest.IsolatedAsyncioTestCase):
    def test_assignment_is_pinned_verbatim_across_compactions(self):
        task = "Audit app/foo.py. Report bugs with line numbers. Do not edit files."
        src = [{"role": "user", "content": task}]
        for i in range(8):
            src += _tool_round(i, 30_000)
        dropped, kept = context_compact.split_recent(src, context_compact.preserve_budget(128_000))
        first = context_compact.continuation("## Objective\n- audit", kept, pinned=context_compact.pinned_assignment(dropped, kept))
        self.assertIn(task, first[0]["content"])
        self.assertEqual(context_compact.compact_summary_body(first[0]["content"]), "## Objective\n- audit")
        self.assertEqual(context_compact.prior_handoff(first), "## Objective\n- audit")

        src = [*first, {"role": "user", "content": "[Fuse] Follow-up from the parent — x:\n\nAlso check bar.py"},
               {"role": "user", "content": "[Fuse] AUTOMATED EVENT — not written by the user. Background results"}]
        for i in range(8, 14):
            src += _tool_round(i, 30_000)
        dropped, kept = context_compact.split_recent(src, context_compact.preserve_budget(128_000))
        pin = context_compact.pinned_assignment(dropped, kept)
        self.assertEqual(pin.count(task), 1)
        self.assertIn("Also check bar.py", pin)
        self.assertNotIn("AUTOMATED EVENT", pin)

    def test_preamble_says_compaction_is_not_completion(self):
        self.assertIn("Compaction is not completion", context_compact.COMPACT_PREAMBLE)
        self.assertNotIn("\n\n", context_compact.COMPACT_PREAMBLE.strip())

    def test_handoff_is_described_as_the_entire_conversation(self):
        self.assertIn("covers the entire earlier conversation", context_compact.COMPACT_PREAMBLE)
        self.assertIn("was kept verbatim", context_compact.COMPACT_PREAMBLE)
        self.assertIn("handoff alone", context_compact.COMPACT_ACK)
        self.assertIn("receives this handoff and nothing else", context_compact.SUMMARIZE_PROMPT)
        self.assertNotIn("remaining messages", context_compact.COMPACT_PREAMBLE + context_compact.COMPACT_ACK + context_compact.SUMMARIZE_PROMPT)

    def test_summary_prompt_follows_the_claude_code_structure(self):
        for heading in ("1. Primary Request and Intent", "6. All user messages", "9. Optional Next Step"):
            self.assertIn(heading, context_compact.SUMMARIZE_PROMPT)
        self.assertIn("<analysis>", context_compact.SUMMARIZE_PROMPT)
        self.assertIn("<summary>", context_compact.SUMMARIZE_PROMPT)
        self.assertIn("preserved verbatim in the summary", context_compact.SUMMARIZE_PROMPT)

    def test_compact_result_body_keeps_only_the_summary_block(self):
        reply = "<analysis>\nthinking out loud\n</analysis>\n\n<summary>\n## Objective\n- ship it\n</summary>"
        self.assertEqual(context_compact.compact_result_body(reply), "## Objective\n- ship it")
        self.assertNotIn("thinking out loud", context_compact.compact_result_body(reply))
        self.assertEqual(context_compact.compact_result_body("plain fallback"), "plain fallback")
        self.assertEqual(context_compact.compact_result_body("<analysis>\nplan\n</analysis>\nbody only"), "body only")

    def test_compact_result_body_keeps_quoted_details_summary(self):
        reply = ("<analysis>\nThe user wants <summary> tags kept.\n</analysis>\n<summary>\n## Current Work\n"
                 "<details><summary>Log</summary>\nfail\n</details>\n9. Optional Next Step: rerun\n</summary>")
        body = context_compact.compact_result_body(reply)
        self.assertTrue(body.startswith("## Current Work"), body)
        self.assertIn("<details><summary>Log</summary>", body)
        self.assertTrue(body.endswith("9. Optional Next Step: rerun"), body)
        self.assertNotIn("tags kept", body)

    async def test_truncate_fallback_describes_the_actual_dropped_span(self):
        head = [{"role": "user", "content": "first ask"}]
        head += [{"role": "assistant", "content": f"step {i}"} for i in range(60)]
        tail = [{"role": "user", "content": "latest"}]
        cp = await context_compact.build_checkpoint([*head, *tail], strategy="truncate", trigger="auto",
                                        provider=True, head=head, tail=tail)
        self.assertIn("first ask", cp["summary"])
        self.assertIn("step 59", cp["summary"])
        self.assertNotIn("step 30", cp["summary"])

    def test_summary_budget_counts_tool_schemas(self):
        tools = [{"type": "function", "function": {"name": f"t{i}", "description": "d" * 4000, "parameters": {}}}
                 for i in range(40)]
        rows = [{"role": "user", "content": "q"}, {"role": "tool", "tool_call_id": "a", "content": "z" * 400_000}]
        fit = context_compact.fit_summary_messages(rows, 128_000, tools=tools)
        budget = 128_000 - context_meter.config().output_reserve - 8_000
        self.assertLessEqual(context_meter.estimate_messages(fit) + context_meter.estimate_tools(tools), budget + 64)

    def test_summary_request_clips_assistant_text_that_also_has_tool_calls(self):
        limit = 128_000
        tools = [{"type": "function", "function": {"name": "read_file", "description": "read", "parameters": {}}}]
        prose = "Here is the full file I rewrote:\n" + ("line\n" * 300_000)
        calls = [{"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": "{\"path\":\"a.py\"}"}}]
        rows = [
            {"role": "user", "content": "refactor it"},
            {"role": "assistant", "content": prose, "tool_calls": calls},
            {"role": "tool", "tool_call_id": "c1", "content": "file has 12 lines"},
        ]
        payload = context_compact.summarize_payload(rows, instructions="focus on the refactor", limit=limit, tools=tools)
        sent = context_meter.estimate_messages(payload) + context_meter.estimate_tools(tools)
        self.assertLessEqual(sent, limit)
        assistant = payload[1]
        self.assertLess(len(assistant["content"]), len(prose))
        self.assertIn("truncated to fit", assistant["content"])
        self.assertEqual(assistant["tool_calls"], calls)
        self.assertEqual(payload[2]["tool_call_id"], "c1")
        self.assertEqual(rows[1]["content"], prose)


if __name__ == "__main__":
    unittest.main()
