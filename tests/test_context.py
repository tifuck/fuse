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

from app import context as ctx  # noqa: E402


class WindowTest(unittest.TestCase):
    def test_resolved_local_backend_and_window_survive_reload(self):
        prior = {"model": "gpt-oss:20b", "source_type": "ollama", "limit": 32768}
        self.assertEqual(ctx.source_type_for_model("gpt-oss:20b", prior), "ollama")
        snap = ctx.standing_meter([{"role": "user", "content": "hi"}], model="gpt-oss:20b", prior=prior)
        self.assertEqual(snap["limit"], 32768)
        self.assertEqual(snap["source_type"], "ollama")
        self.assertEqual(ctx.source_type_for_model("gpt-5.5", prior), "chatgpt")

    def test_known_pool_models(self):
        self.assertEqual(ctx.window_for("grok-4.6", "grok"), 500_000)
        self.assertEqual(ctx.window_for("glm-5.3-flash", "zai"), 1_000_000)
        self.assertEqual(ctx.window_for("gpt-5.4-mini", "chatgpt"), 400_000)
        self.assertEqual(ctx.window_for("devin/gpt-6-astra", "devin"), 1_050_000)
        self.assertEqual(ctx.window_for("devin/gpt-6-astra-medium", "devin"), 1_050_000)
        self.assertEqual(ctx.window_for("devin/claude-sonnet-5", "devin"), 200_000)
        self.assertEqual(ctx.window_for("devin/glm-5-2", "devin"), 200_000)
        self.assertEqual(ctx.window_for("devin/glm-5-2-1m", "devin"), 1_000_000)
        self.assertEqual(ctx.window_for("glm-5-2", "devin"), 200_000)
        self.assertEqual(ctx.window_for("k3", "kimi"), 262_144)
        self.assertEqual(ctx.window_for("kimi-for-coding", "kimi"), 1_048_576)
        self.assertEqual(ctx.type_id_for_model("devin/gpt-6-astra"), "devin")
        self.assertEqual(ctx.type_id_for_model("k3"), "kimi")

    def test_openrouter_catalog_wins(self):
        catalog = {"by_id": {"acme/fast": {"context_length": 32_768}}}
        self.assertEqual(ctx.window_for("openrouter/acme/fast", "openrouter", catalog=catalog), 32_768)

    def test_openrouter_cached_catalog_without_caller_catalog(self):
        catalog = {"by_id": {"acme/huge": {"context_length": 1_000_000}}}
        with mock.patch.object(ctx, "_cached_openrouter_catalog", return_value=catalog):
            self.assertEqual(ctx.window_for("openrouter/acme/huge", "openrouter"), 1_000_000)

    def test_type_id_inferred_from_model_id(self):
        self.assertEqual(ctx.type_id_for_model("grok-4.6"), "grok")
        self.assertEqual(ctx.window_for("grok-4.6"), 500_000)

    def test_llama_cache_and_default(self):
        ctx.remember_window("llama", "local-7b", 8192)
        self.assertEqual(ctx.window_for("local-7b", "llama"), 8192)
        self.assertEqual(ctx.window_for("mystery", "llama"), 8_192)

    def test_ollama_does_not_inherit_gpt_prefix(self):
        self.assertEqual(ctx.window_for("gpt-oss:20b", "ollama"), 128_000)
        self.assertEqual(ctx.window_for("gpt-5.5", "chatgpt"), 1_050_000)

    def test_override(self):
        self.assertEqual(ctx.window_for("grok-4.6", "grok", override=12_000), 12_000)

    def test_unknown_model_uses_learned_window(self):
        # No type prefix and no cached meter: a window learned from whichever
        # source served this exact model name beats the 128k default.
        self.assertEqual(ctx.window_for("mystery-model"), ctx.config().default_window)
        ctx.remember_window("llama", "mystery-model", 16_384)
        self.assertEqual(ctx.window_for("mystery-model"), 16_384)
        self.assertEqual(ctx.window_for("mystery-model", None), 16_384)
        # A stale entry must not win.
        ctx._WINDOW_CACHE[("llama", "mystery-model")] = (time.time() - ctx._WINDOW_TTL - 1, 16_384)
        self.assertEqual(ctx.window_for("mystery-model"), ctx.config().default_window)

    def test_known_type_beats_learned_window_of_same_name(self):
        ctx.remember_window("llama", "grok-4.6", 8_192)
        self.assertEqual(ctx.window_for("grok-4.6", "grok"), 500_000)
        self.assertEqual(ctx.window_for("grok-4.6"), 500_000)


class EstimateTest(unittest.TestCase):
    def test_text_and_usage(self):
        self.assertEqual(ctx.estimate_text("abcd"), 1)
        self.assertEqual(ctx.used_from_usage({"prompt_tokens": 10, "completion_tokens": 5}), 15)
        # Occupancy is prompt+completion of one request. total_tokens on a
        # turn can be the sum of every tool round (billing), so it must not win.
        self.assertEqual(ctx.used_from_usage({"total_tokens": 99, "prompt_tokens": 10, "completion_tokens": 5}), 15)
        self.assertEqual(ctx.used_from_usage({"total_tokens": 99}), 99)

    def test_claude_thinking_blocks_count_as_thinking(self):
        text = "think " * 20
        messages = [{"role": "assistant", "content": "ok",
                     "claude_thinking": [{"thinking": text, "signature": "sig"}]}]
        parts = ctx.occupancy_parts(messages)
        self.assertEqual(parts["thinking"], ctx.estimate_text(text))
        self.assertEqual(ctx.estimate_messages(messages), ctx.estimate_messages(
            [{"role": "assistant", "content": "ok", "reasoning_content": text}]))
        # Counted once: blocks must not also land in the messages row.
        self.assertEqual(parts["messages"], ctx.occupancy_parts(
            [{"role": "assistant", "content": "ok"}])["messages"])

    def test_reasoning_pin_keeps_unreplayed_thinking(self):
        # Prompt includes 700 reasoning tokens the transcript does not echo.
        parts = ctx._fit_parts(
            {"system": 100, "messages": 200, "thinking": 0, "tool_calls": 50, "tools": 50, "draft": 0},
            1000, pin={"thinking": 700})
        self.assertEqual(parts["thinking"], 700)
        self.assertEqual(parts["total"], 1000)
        self.assertEqual(sum(parts[k] for k in ("system", "messages", "thinking", "tool_calls", "tools")), 1000)

    def test_reasoning_pin_skips_disjoint_reasoning(self):
        # xAI reports reasoning outside the prompt; pinning it would invent context.
        self.assertIsNone(ctx.reasoning_pin({
            "prompt_tokens": 1000, "completion_tokens": 5, "reasoning_tokens": 700, "total_tokens": 1705}))
        self.assertEqual(ctx.reasoning_pin({
            "prompt_tokens": 1000, "completion_tokens": 705, "reasoning_tokens": 700, "total_tokens": 1705}),
            {"thinking": 700})

    def test_grok_reasoning_is_not_occupancy(self):
        self.assertEqual(
            ctx.used_from_usage({
                "prompt_tokens": 1000,
                "completion_tokens": 5,
                "reasoning_tokens": 700,
                "total_tokens": 1705,
            }),
            1005,
        )

    def test_messages_include_images(self):
        n = ctx.estimate_messages(
            [
                {"role": "user", "content": [{"type": "text", "text": "hi"}, {"type": "image_url", "image_url": {"url": "x"}}]},
            ]
        )
        self.assertGreaterEqual(n, ctx.IMAGE_TOKENS)

    def test_tools_add_to_estimate(self):
        messages = [{"role": "user", "content": "hi"}]
        tools = [{"type": "function", "function": {"name": "web_search", "parameters": {"type": "object", "properties": {"q": {"type": "string"}}}}}]
        with_tools = ctx.meter_from_messages(messages, model="grok-4.6", type_id="grok", tools=tools)
        without = ctx.meter_from_messages(messages, model="grok-4.6", type_id="grok")
        self.assertGreater(with_tools["used"], without["used"])
        self.assertEqual(with_tools["limit"], 500_000)

    def test_standing_preserves_retained_tool_pairs(self):
        rows = ctx.standing_messages(
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
        full = ctx.estimate_messages([
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "go"},
            {"role": "tool", "content": "file.txt " * 5000},
            {"role": "assistant", "content": "done"},
        ])
        standing = ctx.estimate_messages(rows)
        self.assertGreaterEqual(standing, full)

    def test_standing_meter_ignores_last_round_usage(self):
        messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "go"},
            {"role": "assistant", "content": "done"},
        ]
        live = ctx.meter_from_messages(
            messages,
            model="grok-4.6",
            type_id="grok",
            usage={"prompt_tokens": 80_000, "completion_tokens": 20_000},
        )
        standing = ctx.standing_meter(messages, model="grok-4.6", type_id="grok")
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
        prior = ctx.standing_meter(first, model="grok-4.6", type_id="grok")
        # Simulate a stale high-water total on the prior snapshot.
        prior["used"] = 99_000
        # Durable rows carry no system prompt; the recompute must inherit the
        # overhead parts but never the stale total.
        dropped = ctx.standing_meter(
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
        grown = ctx.standing_meter(later, model="grok-4.6", type_id="grok", prior=prior)
        self.assertGreater(grown["parts"]["history"], dropped["parts"]["history"])

    def test_devin_delta_usage_is_not_occupancy(self):
        messages = [
            {"role": "system", "content": "sys " * 400},
            {"role": "user", "content": "hello " * 40},
            {"role": "assistant", "content": "ok"},
        ]
        estimated = ctx.estimate_messages(messages)
        self.assertGreater(estimated, 400)
        # Last-round Devin usage is a cache/delta, not the full window. The
        # standing meter must keep the transcript estimate.
        standing = ctx.standing_meter(messages, model="devin/glm-5-2", type_id="devin")
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
        parts = ctx.occupancy_parts(messages, tools=tools)
        self.assertGreater(parts["system"], 0)
        self.assertGreater(parts["messages"], 0)
        self.assertGreater(parts["thinking"], 0)
        self.assertGreater(parts["tool_calls"], 0)
        self.assertIn("shell", parts["tool_families"])
        self.assertIn("web search", parts["tool_families"])
        self.assertEqual(parts["history"], parts["messages"] + parts["thinking"] + parts["tool_calls"])
        self.assertEqual(parts["total"], parts["system"] + parts["history"] + parts["tools"])
        snap = ctx.standing_meter(messages, model="grok-4.6", type_id="grok", tools=tools)
        self.assertEqual(snap["parts"]["thinking"], parts["thinking"])
        self.assertEqual(snap["parts"]["tool_families"]["shell"], parts["tool_families"]["shell"])

    def test_standing_meter_inherits_schema_overhead_with_empty_tools(self):
        # Web search toggled off still means "schemas absent from durable
        # rows" — the overhead must be inherited, not zeroed.
        msgs = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "yo"}]
        prior = {"model": "grok-4.6", "limit": 500_000,
                 "parts": {"system": 1_879, "tools": 3_119}}
        snap = ctx.standing_meter(msgs, model="grok-4.6", type_id="grok", tools=[], prior=prior)
        self.assertEqual(snap["parts"]["tools"], 3_119)
        self.assertEqual(snap["parts"]["system"], 1_879)
        self.assertEqual(snap["used"], 1_879 + snap["parts"]["history"] + 3_119)


class ThresholdTest(unittest.TestCase):
    def test_auto_at_90_percent(self):
        limit = 100_000
        self.assertFalse(ctx.should_compact(89_000, limit))
        self.assertTrue(ctx.should_compact(90_000, limit))
        self.assertTrue(ctx.should_warn(80_000, limit))
        self.assertFalse(ctx.should_warn(79_000, limit))

    def test_estimated_spike_does_not_auto_compact(self):
        limit = 128_000
        # 4-char estimate can report 157% while the last standing snapshot was 4.3k.
        self.assertFalse(
            ctx.should_auto_compact(201_000, limit, estimated=True, prior_used=4_300)
        )
        self.assertTrue(
            ctx.should_auto_compact(201_000, limit, estimated=False, prior_used=4_300)
        )
        self.assertTrue(
            ctx.should_auto_compact(120_000, limit, estimated=True, prior_used=120_000)
        )

    def test_usage_not_raised_to_estimate(self):
        huge = [{"role": "system", "content": "x" * 80_000}]
        snap = ctx.meter_from_messages(
            huge,
            model="offline",
            usage={"prompt_tokens": 4_000, "completion_tokens": 300},
            tools=[{"type": "function", "function": {"name": "web_search", "parameters": {}}}],
        )
        self.assertEqual(snap["used"], 4_300)
        self.assertFalse(snap["estimated"])

    def test_small_window_stays_near_ratio(self):
        trigger = ctx.trigger_used(8_192)
        self.assertLess(trigger, 8_192)
        self.assertGreaterEqual(trigger, int(8_192 * 0.85))


class ProjectTest(unittest.TestCase):
    def test_checkpoint_replaces_prefix(self):
        messages = [
            {"id": "a", "role": "user", "content": "one"},
            {"id": "b", "role": "assistant", "content": "ok"},
            {"id": "c", "role": "user", "content": "two"},
            {"id": "d", "role": "assistant", "content": "done"},
            {"id": "e", "role": "user", "content": "three"},
        ]
        state = ctx.parse_state({})
        cp = ctx.make_checkpoint(
            summary="Did one and two.",
            through_message_id="d",
            strategy="truncate",
            trigger="manual",
        )
        state = ctx.append_checkpoint(state, cp)
        out = ctx.project_transcript(messages, state)
        self.assertEqual(out[0]["role"], "user")
        self.assertIn("Did one and two.", out[0]["content"])
        self.assertEqual(out[1]["content"], ctx.COMPACT_ACK)
        self.assertEqual([m["id"] for m in out[2:]], ["e"])

    def test_rewind_drops_stale_checkpoint(self):
        messages = [
            {"id": "a", "role": "user", "content": "one"},
            {"id": "b", "role": "assistant", "content": "ok"},
        ]
        cp = ctx.make_checkpoint(
            summary="gone",
            through_message_id="missing",
            strategy="truncate",
            trigger="auto",
        )
        out = ctx.project_transcript(messages, {"checkpoints": [cp]})
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
        out, ids, freed = ctx.prune_tool_outputs(messages, protect=1_000, minimum=100)
        self.assertIn("c1", ids)
        self.assertGreater(freed, 100)
        stubbed = [m for m in out if m.get("tool_call_id") == "c1"][0]
        self.assertTrue(stubbed["content"].startswith(ctx.TOOL_STUB))
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
        head, tail = ctx.split_transcript(messages, keep_tail=4)
        self.assertEqual([m["id"] for m in tail], ["u6", "a6", "u7", "a7"])
        self.assertEqual([m["id"] for m in head], ["u0", "a0", "u1", "a1", "u2", "a2", "u3", "a3", "u4", "a4", "u5", "a5"])

    def test_keeps_tool_results_with_tail_turns(self):
        messages = []
        for i in range(8):
            messages.append({"id": f"u{i}", "role": "user", "content": f"q{i}"})
            messages.append({"id": f"a{i}", "role": "assistant", "content": "", "tool_calls": [{"id": f"c{i}"}]})
            messages.append({"role": "tool", "tool_call_id": f"c{i}", "content": f"LIVE-{i}"})
        head, tail = ctx.split_transcript(messages, keep_tail=4)
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
        self.assertTrue(ctx.compactable_transcript(messages, keep_tail=6, provider=True))
        self.assertFalse(ctx.compactable_transcript(messages, keep_tail=6, provider=False))
        head, tail = ctx.split_transcript(messages, keep_tail=6)
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
        self.assertEqual(meter["auto_at"], ctx.trigger_used(64_000))


class StrategyTest(unittest.IsolatedAsyncioTestCase):
    async def test_truncate_fallback_without_summarizer(self):
        messages = []
        for i in range(12):
            messages.append({"id": f"u{i}", "role": "user", "content": f"ask {i} " + "word " * 200})
            messages.append({"id": f"a{i}", "role": "assistant", "content": f"ans {i} " + "word " * 200})
        cp = await ctx.build_checkpoint(messages, strategy="summarize", trigger="manual", summarize=None)
        self.assertIsNotNone(cp)
        self.assertEqual(cp["strategy"], "truncate")
        self.assertTrue(cp["summary"])
        self.assertTrue(cp["through_message_id"])
        projected = ctx.project_transcript(messages, ctx.append_checkpoint({}, cp))
        self.assertLess(ctx.estimate_messages(projected), ctx.estimate_messages(messages))

    async def test_summarize_strategy_uses_callback(self):
        messages = []
        for i in range(10):
            messages.append({"id": f"u{i}", "role": "user", "content": f"q{i}"})
            messages.append({"id": f"a{i}", "role": "assistant", "content": f"a{i}"})

        async def fake(head, instructions=""):
            self.assertGreater(len(head), 0)
            return "HANDOFF:" + instructions

        cp = await ctx.build_checkpoint(
            messages, strategy="summarize", instructions="keep the plan", trigger="manual", summarize=fake
        )
        self.assertEqual(cp["strategy"], "summarize")
        self.assertEqual(cp["summary"], "HANDOFF:keep the plan")

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
        payload = ctx.summarize_payload(head, limit=200_000)
        tool = next(msg for msg in payload if msg.get("role") == "tool")
        self.assertEqual(tool["content"], body)
        self.assertGreater(len(tool["content"]), 2_000)
        self.assertIn("app/auth.py", payload[2]["tool_calls"][0]["function"]["arguments"])
        self.assertIn("Write the handoff now.", payload[-1]["content"])
        self.assertIn("re-read", ctx.SUMMARIZE_PROMPT)
        self.assertIn("lossy", ctx.COMPACT_PREAMBLE)
        self.assertEqual(payload[0]["role"], "system")

    def test_summarize_payload_merges_prior_handoff(self):
        payload = ctx.summarize_payload([
            {"role": "user", "content": ctx.COMPACT_PREAMBLE + "keep foo.py and the exit code 2"},
            {"role": "assistant", "content": ctx.COMPACT_ACK},
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
        self.assertIsNone(await ctx.build_checkpoint(messages, trigger="manual"))


class MeterTest(unittest.TestCase):
    def test_provider_input_contracts(self):
        for provider in ("grok", "chatgpt", "zai", "openrouter", "openai", "llama", "ollama"):
            with self.subTest(provider=provider):
                cal = ctx.prompt_calibration({"input_tokens": 5000}, 2500, model="model", type_id=provider)
                self.assertEqual(cal["prompt_tokens"], 5000)
        self.assertIsNone(ctx.prompt_calibration({"prompt_tokens": 50, "cached_tokens": 5000}, 2500,
            model="devin/model", type_id="devin"))

    def test_calibration_uses_inclusive_input_not_billing_or_cache_again(self):
        rows = [{"role": "user", "content": "x" * 400}]
        raw = ctx.meter_from_messages(rows, model="glm-5.3")
        calibration = ctx.prompt_calibration({"prompt_tokens": 208, "cached_tokens": 200,
            "completion_tokens": 5000, "total_tokens": 999999}, raw["used"], model="glm-5.3", type_id="zai")
        snap = ctx.meter_from_messages(rows, model="glm-5.3", calibration=calibration)
        self.assertEqual(snap["used"], 208)
        self.assertTrue(snap["estimated"])
        self.assertEqual(snap["basis"], "provider_calibrated")
        changed = ctx.meter_from_messages(rows, model="grok-4.6", calibration=calibration)
        self.assertEqual(changed["used"], raw["used"])
        self.assertIsNone(ctx.prompt_calibration({"total_tokens": 999999}, 104, model="glm-5.3", type_id="zai"))

    def test_standing_calibration_survives_reload_without_compounding_overhead(self):
        rows = [{"role": "system", "content": "s" * 400}, {"role": "user", "content": "x" * 400}]
        raw = ctx.meter_from_messages(rows, model="glm-5.3")
        cal = ctx.prompt_calibration({"prompt_tokens": raw["used"] * 2}, raw["used"], model="glm-5.3", type_id="zai")
        prior = ctx.calibrate_meter(raw, cal)
        for _ in range(4):
            prior = ctx.standing_meter(rows[1:], model="glm-5.3", prior=prior)
            self.assertEqual(prior["used"], raw["used"] * 2)
        smaller = ctx.standing_meter([{"role": "user", "content": "short handoff"}], model="glm-5.3", prior=prior)
        self.assertLess(smaller["used"], prior["used"])

    def test_public_config_and_meter(self):
        cfg = ctx.public_config()
        self.assertEqual(cfg["auto_ratio"], 0.9)
        self.assertEqual(cfg["warn_ratio"], 0.8)
        snap = ctx.meter(used=90_000, limit=100_000, model="grok-4.6")
        self.assertTrue(snap["auto"])
        self.assertTrue(snap["warn"])
        self.assertEqual(snap["limit"], 100_000)


class ClipToFitTest(unittest.TestCase):
    def test_clipping_uses_calibrated_budget(self):
        rows = [{"role": "user", "content": "x" * 40000}]
        cal = ctx.prompt_calibration({"prompt_tokens": 20008}, 10004, model="m", type_id="zai")
        out, clipped = ctx.clip_to_fit(rows, limit=16000, reserve=2000, calibration=cal)
        self.assertGreater(clipped, 0)
        self.assertLessEqual(ctx.estimate_messages(out) * 2, 14000)

    def test_passthrough_when_small(self):
        rows = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
        ]
        out, clipped = ctx.clip_to_fit(rows, limit=128_000)
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
        out, clipped = ctx.clip_to_fit(rows, limit=20_000, reserve=4_000)
        self.assertGreaterEqual(clipped, 1)
        self.assertEqual(len(out), len(rows))
        user = out[1]
        self.assertLess(len(user["content"]), 120_000)
        self.assertIn("truncated to fit", user["content"])
        self.assertTrue(user["content"].startswith("xxx"))
        self.assertLess(ctx.estimate_messages(out), 20_000)
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
        out, clipped = ctx.clip_to_fit(rows, limit=20_000, reserve=4_000)
        self.assertGreaterEqual(clipped, 1)
        self.assertLessEqual(ctx.estimate_messages(out), 16_000)
        for i, row in enumerate(out):
            if row.get("role") == "user":
                self.assertLessEqual(len(row["content"]), 40_100)

    def test_never_clips_system_or_toolcall_rows(self):
        rows = [
            {"role": "system", "content": "s" * 200_000},
            {
                "role": "assistant",
                "content": "a" * 200_000,
                "tool_calls": [{"id": "c1", "function": {"name": "t", "arguments": "{}"}}],
            },
        ]
        out, clipped = ctx.clip_to_fit(rows, limit=20_000)
        self.assertEqual(clipped, 0)
        self.assertIs(out, rows)


class DurableCheckpointTest(unittest.TestCase):
    def _durable(self, n=12):
        rows = [{"id": f"u{i}", "role": "user", "content": f"ask {i} " + "w" * 500} for i in range(n)]
        rows += [{"id": f"a{i}", "role": "assistant", "content": f"ans {i} " + "w" * 500} for i in range(n)]
        return rows

    def test_twin_anchors_durable_projection(self):
        live = {"summary": "the handoff", "strategy": "summarize", "trigger": "auto", "tokens_before": 9_000}
        cp = ctx.durable_checkpoint(self._durable(), live)
        self.assertIsNotNone(cp)
        self.assertFalse(cp["provider"])
        self.assertTrue(cp["through_message_id"].startswith(("u", "a")))
        projected = ctx.project_transcript(self._durable(), ctx.append_checkpoint({}, cp))
        self.assertEqual(len(projected), 2)
        self.assertIn("the handoff", projected[0]["content"])
        self.assertLess(ctx.estimate_messages(projected), ctx.estimate_messages(self._durable()))

    def test_twin_needs_ids_and_enough_rows(self):
        no_ids = [{"role": "user", "content": "q"} for _ in range(20)]
        self.assertIsNone(ctx.durable_checkpoint(no_ids, {"summary": "s"}))
        short = [{"id": "u0", "role": "user", "content": "q"}]
        self.assertIsNone(ctx.durable_checkpoint(short, {"summary": "s"}))
        self.assertIsNone(ctx.durable_checkpoint(self._durable(), {"summary": "  "}))

    def test_state_round_trip_drops_legacy_prune_ids(self):
        state = ctx.parse_state({"pruned_tool_ids": ["a", "b"], "keep_tail": 6})
        self.assertNotIn("pruned_tool_ids", state)
        cp = ctx.durable_checkpoint(self._durable(), {"summary": "s"})
        state = ctx.append_checkpoint(state, cp)
        self.assertIn("checkpoints", ctx.dump_state(state))


class DurableCompactCurrentTest(unittest.IsolatedAsyncioTestCase):
    async def test_auto_compact_recheck_uses_calibrated_input(self):
        from app.chat_runtime import _compact_current
        rows = [{"role": "user", "content": "x" * 400} for _ in range(20)]
        raw = ctx.estimate_messages(rows)
        calibration = ctx.prompt_calibration({"prompt_tokens": raw * 4}, raw, model="offline", type_id="openai")
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
        state = ctx.append_checkpoint({}, persisted)
        projected = ctx.project_transcript(durable, state)
        self.assertLess(ctx.estimate_messages(projected), ctx.estimate_messages(durable))

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
        dropped, kept = ctx.split_recent(src, ctx.preserve_budget(128_000))
        first = ctx.continuation("## Objective\n- audit", kept, pinned=ctx.pinned_assignment(dropped, kept))
        self.assertIn(task, first[0]["content"])
        self.assertEqual(ctx.compact_summary_body(first[0]["content"]), "## Objective\n- audit")
        self.assertEqual(ctx.prior_handoff(first), "## Objective\n- audit")

        src = [*first, {"role": "user", "content": "[Fuse] Follow-up from the parent — x:\n\nAlso check bar.py"},
               {"role": "user", "content": "[Fuse] AUTOMATED EVENT — not written by the user. Background results"}]
        for i in range(8, 14):
            src += _tool_round(i, 30_000)
        dropped, kept = ctx.split_recent(src, ctx.preserve_budget(128_000))
        pin = ctx.pinned_assignment(dropped, kept)
        self.assertEqual(pin.count(task), 1)
        self.assertIn("Also check bar.py", pin)
        self.assertNotIn("AUTOMATED EVENT", pin)

    def test_preamble_says_compaction_is_not_completion(self):
        self.assertIn("Compaction is not completion", ctx.COMPACT_PREAMBLE)
        self.assertNotIn("\n\n", ctx.COMPACT_PREAMBLE.strip())

    def test_handoff_is_described_as_the_entire_conversation(self):
        self.assertIn("This handoff is the entire conversation.", ctx.COMPACT_PREAMBLE)
        self.assertIn("nothing was kept verbatim", ctx.COMPACT_PREAMBLE)
        self.assertIn("handoff alone", ctx.COMPACT_ACK)
        self.assertIn("receives this handoff and nothing else", ctx.SUMMARIZE_PROMPT)
        self.assertNotIn("remaining messages", ctx.COMPACT_PREAMBLE + ctx.COMPACT_ACK + ctx.SUMMARIZE_PROMPT)

    async def test_truncate_fallback_describes_the_actual_dropped_span(self):
        head = [{"role": "user", "content": "first ask"}]
        head += [{"role": "assistant", "content": f"step {i}"} for i in range(60)]
        tail = [{"role": "user", "content": "latest"}]
        cp = await ctx.build_checkpoint([*head, *tail], strategy="truncate", trigger="auto",
                                        provider=True, head=head, tail=tail)
        self.assertIn("first ask", cp["summary"])
        self.assertIn("step 59", cp["summary"])
        self.assertNotIn("step 30", cp["summary"])

    def test_summary_budget_counts_tool_schemas(self):
        tools = [{"type": "function", "function": {"name": f"t{i}", "description": "d" * 4000, "parameters": {}}}
                 for i in range(40)]
        rows = [{"role": "user", "content": "q"}, {"role": "tool", "tool_call_id": "a", "content": "z" * 400_000}]
        fit = ctx.fit_summary_messages(rows, 128_000, tools=tools)
        budget = 128_000 - ctx.config().output_reserve - 8_000
        self.assertLessEqual(ctx.estimate_messages(fit) + ctx.estimate_tools(tools), budget + 64)


if __name__ == "__main__":
    unittest.main()
