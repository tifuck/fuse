"""Token accounting, cache-cost, and affinity invariants (no live traffic)."""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import unittest
import unittest.mock as mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_TMP = tempfile.mkdtemp(prefix="fuse-usage-")
os.environ.setdefault("DATABASE_PATH", str(Path(_TMP) / "usage.db"))

from app import db  # noqa: E402
from app.proxy import merge_usage, usage_from_obj  # noqa: E402
from app.sources import openai_compat  # noqa: E402
from app.sources import pool_session  # noqa: E402

db.settings.database_path = str(Path(_TMP) / "usage.db")
db._conn = None


def _price_row(**kw):
    base = {
        "input_micros_per_million": 2_000_000,
        "cached_input_micros_per_million": 500_000,
        "cache_write_micros_per_million": 0,
        "output_micros_per_million": 6_000_000,
        "reasoning_micros_per_million": 0,
        "cached_input_known": 1,
        "cache_write_known": 0,
        "reasoning_known": 0,
        "long_context_threshold": None,
        "long_input_micros_per_million": None,
        "long_cached_input_micros_per_million": None,
        "long_output_micros_per_million": None,
        "long_cached_input_known": 0,
        "tiers_json": "[]",
    }
    base.update(kw)
    return base


class UsageParsingTest(unittest.TestCase):
    def test_openai_reasoning_inside_completion(self):
        u = usage_from_obj(
            {"usage": {
                "prompt_tokens": 1000,
                "completion_tokens": 500,
                "total_tokens": 1500,
                "prompt_tokens_details": {"cached_tokens": 400},
                "completion_tokens_details": {"reasoning_tokens": 200},
            }}
        )
        self.assertEqual(u["prompt_tokens"], 1000)
        self.assertEqual(u["cached_tokens"], 400)
        self.assertEqual(u["completion_tokens"], 500)
        self.assertEqual(u["output_tokens"], 500)      # reasoning included
        self.assertEqual(u["visible_tokens"], 300)     # 500 - 200
        self.assertEqual(u["reasoning_tokens"], 200)
        self.assertEqual(u["total_tokens"], 1500)

    def test_responses_style_keys(self):
        u = usage_from_obj(
            {"usage": {
                "input_tokens": 300,
                "output_tokens": 40,
                "total_tokens": 340,
                "input_tokens_details": {"cached_tokens": 100, "cache_write_tokens": 50},
                "output_tokens_details": {"reasoning_tokens": 10},
            }}
        )
        self.assertEqual(u["prompt_tokens"], 300)
        self.assertEqual(u["cached_tokens"], 100)
        self.assertEqual(u["cache_write_tokens"], 50)
        self.assertEqual(u["output_tokens"], 40)
        self.assertEqual(u["visible_tokens"], 30)
        self.assertEqual(u["usage_status"], "complete")

    def test_grok_disjoint_reasoning(self):
        u = usage_from_obj(
            {"usage": {
                "prompt_tokens": 1000,
                "completion_tokens": 5,
                "reasoning_tokens": 700,
                "total_tokens": 1705,
                "prompt_tokens_details": {"cached_tokens": 128},
            }},
            reasoning_separate=True,
        )
        self.assertEqual(u["output_tokens"], 705)   # visible + reasoning
        self.assertEqual(u["visible_tokens"], 5)
        self.assertEqual(u["total_tokens"], 1705)   # authoritative total kept

    def test_grok_disjoint_detected_without_flag(self):
        # Stream taps call usage_from_obj without reasoning_separate. xAI
        # still has to land as visible=completion, output=completion+reasoning.
        u = usage_from_obj(
            {"usage": {
                "prompt_tokens": 34054,
                "completion_tokens": 51,
                "reasoning_tokens": 82,
                "total_tokens": 34187,
            }}
        )
        self.assertEqual(u["output_tokens"], 133)
        self.assertEqual(u["visible_tokens"], 51)
        self.assertEqual(u["reasoning_tokens"], 82)
        self.assertEqual(u["total_tokens"], 34187)

    def test_grok_disjoint_when_visible_exceeds_reasoning(self):
        u = usage_from_obj(
            {"usage": {
                "prompt_tokens": 100,
                "completion_tokens": 40,
                "reasoning_tokens": 10,
                "total_tokens": 150,
            }}
        )
        self.assertEqual(u["output_tokens"], 50)
        self.assertEqual(u["visible_tokens"], 40)

    def test_grok_total_authoritative_adds_missing_output(self):
        u = usage_from_obj(
            {"usage": {
                "prompt_tokens": 1000,
                "completion_tokens": 5,
                "reasoning_tokens": 700,
                "total_tokens": 2000,
            }},
            reasoning_separate=True,
        )
        self.assertEqual(u["output_tokens"], 1000)  # 2000 - 1000
        self.assertEqual(u["total_tokens"], 2000)

    def test_total_overrides_forced_disjoint_hint(self):
        # Kimi Code sets reasoning_separate but reports total = prompt +
        # completion with reasoning inside completion (live data, all rows).
        u = usage_from_obj(
            {"usage": {
                "prompt_tokens": 77642,
                "completion_tokens": 173,
                "total_tokens": 77815,
                "completion_tokens_details": {"reasoning_tokens": 87},
            }},
            reasoning_separate=True,
        )
        self.assertEqual(u["output_tokens"], 173)
        self.assertEqual(u["visible_tokens"], 86)
        self.assertEqual(u["total_tokens"], 77815)

    def test_total_beats_reasoning_exceeds_completion_guess(self):
        # OpenRouter occasionally reports reasoning > completion while its
        # total still says prompt + completion; never add reasoning on top.
        u = usage_from_obj(
            {"usage": {
                "prompt_tokens": 257904,
                "completion_tokens": 484,
                "total_tokens": 258388,
                "completion_tokens_details": {"reasoning_tokens": 538},
            }}
        )
        self.assertEqual(u["output_tokens"], 538)
        self.assertEqual(u["total_tokens"], 257904 + 538)

    def test_total_never_below_prompt_plus_output(self):
        u = usage_from_obj(
            {"usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 120}}
        )
        self.assertEqual(u["total_tokens"], 150)

    def test_cache_fields_clamped_to_prompt(self):
        u = usage_from_obj(
            {"usage": {
                "prompt_tokens": 100,
                "completion_tokens": 1,
                "prompt_tokens_details": {"cached_tokens": 500, "cache_write_tokens": 900},
            }}
        )
        self.assertEqual(u["cached_tokens"], 100)
        self.assertEqual(u["cache_write_tokens"], 0)

    def test_xai_cost_fields(self):
        ticks = usage_from_obj({"usage": {"prompt_tokens": 1, "completion_tokens": 1, "cost_in_usd_ticks": 125_000_000}})
        self.assertEqual(ticks["cost_micros"], 12_500)  # 1e10 ticks per USD
        nano = usage_from_obj({"usage": {"prompt_tokens": 1, "completion_tokens": 1, "cost_in_nano_usd": 12_500_000}})
        self.assertEqual(nano["cost_micros"], 12_500)

    def test_openrouter_cache_write_and_reported_cost(self):
        u = usage_from_obj(
            {"usage": {
                "prompt_tokens": 10339,
                "completion_tokens": 60,
                "total_tokens": 10399,
                "cost": 0.95,
                "prompt_tokens_details": {"cached_tokens": 10318, "cache_write_tokens": 0},
            }}
        )
        self.assertEqual(u["cached_tokens"], 10318)
        self.assertEqual(u["cache_write_tokens"], 0)
        self.assertEqual(u["cost_micros"], 950_000)

    def test_no_usage_means_unknown(self):
        u = usage_from_obj({"choices": []})
        self.assertEqual(u["usage_status"], "unknown")
        self.assertEqual(u["prompt_tokens"], 0)

    def test_merge_keeps_reported_cost_and_fills_detail(self):
        parsed = {"prompt_tokens": 10, "completion_tokens": 2, "output_tokens": 2,
                  "visible_tokens": 2, "cached_tokens": 0, "cache_write_tokens": 0,
                  "cost_micros": 500, "usage_status": "complete"}
        extra = {"prompt_tokens": 10, "completion_tokens": 2, "cached_tokens": 6,
                 "cache_write_tokens": 1, "usage_status": "complete"}
        merged = merge_usage(parsed, extra)
        self.assertEqual(merged["cached_tokens"], 6)      # missing detail filled in
        self.assertEqual(merged["cache_write_tokens"], 1)
        self.assertEqual(merged["cost_micros"], 500)      # reported cost never dropped
        self.assertEqual(merged["usage_status"], "complete")
        richer = merge_usage({"prompt_tokens": 3, "cached_tokens": 7, "usage_status": "complete"}, extra)
        self.assertEqual(richer["cached_tokens"], 7)      # existing detail kept

    def test_later_cumulative_stream_counts_replace_early_counts_without_summing(self):
        early = {"prompt_tokens": 1000, "completion_tokens": 2, "total_tokens": 1002}
        final = {"prompt_tokens": 60000, "completion_tokens": 50, "total_tokens": 60050}
        self.assertEqual(merge_usage(early, final), final)
        self.assertEqual(merge_usage(final, final), final)


class EstimatedCostTest(unittest.TestCase):
    def test_cache_read_and_write_not_double_counted(self):
        price = _price_row(
            input_micros_per_million=4_000_000,
            cached_input_micros_per_million=400_000,
            cache_write_micros_per_million=5_000_000,
            cache_write_known=1,
        )
        # 1M prompt: 400k ordinary, 600k cached-read; no write.
        cost = db._estimated_cost(price, 1_000_000, 0, 0, 0, 600_000, 0)
        self.assertEqual(cost, 400_000 * 4 + 600_000 * 400 // 1_000 // 1_000 * 1_000)
        # Exact: (400_000*4_000_000 + 600_000*400_000)//1_000_000 = 1_600_000+240_000
        self.assertEqual(cost, 400_000 * 4 + 600_000 * 0.4)
        # 1M prompt: 400k ordinary, 0 cached, 600k cache-write at $5.
        cost = db._estimated_cost(price, 1_000_000, 0, 0, 0, 0, 600_000)
        self.assertEqual(cost, 400_000 * 4 + 600_000 * 5)

    def test_unknown_cache_write_rate_bills_at_input_rate(self):
        price = _price_row(cache_write_known=0)
        cost = db._estimated_cost(price, 500_000, 0, 0, 0, 0, 100_000)
        self.assertEqual(cost, 400_000 * 2 + 100_000 * 2)

    def test_long_context_tier_inclusive_boundary(self):
        price = _price_row(
            tiers_json='[{"min_prompt_tokens": 200000, "input_rate": 4000000, "cached_rate": 1000000, "output_rate": 12000000}]'
        )
        below = db._estimated_cost(price, 199_999, 0, 0, 0, 0, 0)
        at = db._estimated_cost(price, 200_000, 0, 0, 0, 0, 0)
        self.assertEqual(below, 399_998)      # $2/M short-context rate
        self.assertEqual(at, 800_000)         # inclusive tier: $4/M from 200k

    def test_reasoning_known_splits_output(self):
        price = _price_row(
            output_micros_per_million=20_000_000,
            reasoning_micros_per_million=30_000_000,
            reasoning_known=1,
        )
        # output 100 (visible 40 + reasoning 60): 40*20 + 60*30
        cost = db._estimated_cost(price, 0, 100, 40, 60, 0, 0)
        self.assertEqual(cost, 40 * 20 + 60 * 30)
        # Reasoning is a subset: 100 output with 100 reasoning is not 200.
        cost = db._estimated_cost(price, 0, 100, 0, 100, 0, 0)
        self.assertEqual(cost, 100 * 30)

    def test_reasoning_unknown_bills_output_once(self):
        price = _price_row(output_micros_per_million=6_000_000)
        cost = db._estimated_cost(price, 0, 50, 20, 30, 0, 0)
        self.assertEqual(cost, 50 * 6)


class RecordUsageTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db.connect()
        with db.tx() as conn:
            for uid in (97, 98, 99):
                conn.execute(
                    "INSERT OR IGNORE INTO users (id, email, password_hash, is_admin, created_at) VALUES (?,?,?,0,?)",
                    (uid, f"u{uid}@test.local", "x", "2026-08-30T00:00:00Z"),
                )

    def test_canonical_persist_and_aggregate(self):
        with mock.patch.object(db, "now", return_value="2026-08-30T10:00:00Z"), \
             mock.patch.object(db, "iso_week_start", return_value="2026-08-31"):
            db.record_usage(
                99, None, None, "grok-4.6",
                {"prompt_tokens": 1000, "completion_tokens": 5, "reasoning_tokens": 700,
                 "output_tokens": 705, "visible_tokens": 5, "cached_tokens": 128,
                 "total_tokens": 1705, "usage_status": "complete"},
                key_id=None, provider="grok", request_status="success",
            )
        row = db.fetchone(
            "SELECT * FROM usage_events WHERE user_id=99 AND source_type='grok'"
        )
        self.assertEqual(row["output_tokens"], 705)
        self.assertEqual(row["visible_tokens"], 5)
        self.assertEqual(row["cached_tokens"], 128)
        self.assertEqual(row["total_tokens"], 1705)
        self.assertEqual(row["request_status"], "success")
        self.assertEqual(row["usage_status"], "complete")
        prov = db.fetchone("SELECT * FROM provider_usage WHERE user_id=99 AND provider='grok'")
        self.assertEqual(prov["output_tokens"], 705)
        self.assertEqual(prov["total_tokens"], 1705)

    def test_validation_clamps_and_counts(self):
        with mock.patch.object(db, "now", return_value="2026-08-30T10:00:01Z"), \
             mock.patch.object(db, "iso_week_start", return_value="2026-08-31"):
            db.record_usage(
                98, None, None, "grok-4.6",
                {"prompt_tokens": 50, "cached_tokens": 999, "output_tokens": -5,
                 "cost_micros": -100, "usage_status": "weird"},
                provider="grok", request_status="cancelled", count_request=False,
            )
        row = db.fetchone("SELECT * FROM usage_events WHERE user_id=98")
        self.assertEqual(row["cached_tokens"], 50)          # clamped to prompt
        self.assertEqual(row["output_tokens"], 0)
        self.assertEqual(row["reported_cost_micros"], 0)    # negative clamped, never negative
        self.assertEqual(row["request_status"], "cancelled")
        self.assertEqual(row["usage_status"], "unknown")
        prov = db.fetchone("SELECT * FROM provider_usage WHERE user_id=98 AND provider='grok'")
        self.assertEqual(prov["requests"], 0)               # attempt not counted

    def test_price_row_manual_not_overwritten_by_sync(self):
        db.save_model_price("grok", "grok-test", 2_000_000, 500_000, 6_000_000, None, origin="manual")
        db.save_model_price("grok", "grok-test", 9_999_999, None, 9_999_999, None, origin="openrouter")
        row = db.fetchone("SELECT * FROM model_prices WHERE provider='grok' AND model='grok-test'")
        self.assertEqual(row["input_micros_per_million"], 2_000_000)
        self.assertEqual(row["origin"], "manual")
        # A manual save still wins over an existing synced row.
        db.save_model_price("grok", "grok-test2", 1_000_000, None, 2_000_000, None, origin="openrouter")
        db.save_model_price("grok", "grok-test2", 3_000_000, None, 4_000_000, None, origin="manual")
        row = db.fetchone("SELECT * FROM model_prices WHERE provider='grok' AND model='grok-test2'")
        self.assertEqual(row["input_micros_per_million"], 3_000_000)

    def test_write_rate_reprices_history(self):
        db.save_model_price("chatgpt", "gpt-w", 4_000_000, 400_000, 20_000_000, None,
                            cache_write_rate=5_000_000, origin="manual")
        with mock.patch.object(db, "now", return_value="2026-08-30T10:00:02Z"), \
             mock.patch.object(db, "iso_week_start", return_value="2026-08-31"):
            db.record_usage(
                97, None, None, "gpt-w",
                {"prompt_tokens": 1_000_000, "output_tokens": 0,
                 "cache_write_tokens": 600_000, "usage_status": "complete"},
                provider="chatgpt",
            )
        row = db.fetchone("SELECT estimated_cost_micros FROM usage_events WHERE user_id=97")
        self.assertEqual(row["estimated_cost_micros"], 400_000 * 4 + 600_000 * 5)

    def test_source_card_api_equivalent_uses_estimate_not_reported_cost(self):
        from app import stats

        with db.tx() as conn:
            conn.execute(
                "INSERT INTO sources (id,type,name,enabled,config,created_at) VALUES (901,'grok','Grok',1,'{}',?)",
                (db.now(),),
            )
            conn.execute(
                """INSERT INTO credentials
                   (id,source_id,label,secret,extra,week_start,created_at,prompt_tokens,
                    output_tokens,total_tokens,requests,cost_micros)
                   VALUES (901,901,'Grok account','x','x',?,?,1000,100,1100,1,100)""",
                (db.iso_week_start(), db.now()),
            )
            conn.execute(
                """INSERT INTO usage_events
                   (user_id,source_id,source_type,source_name,credential_id,model,
                    prompt_tokens,output_tokens,total_tokens,reported_cost_micros,
                    estimated_cost_micros,pricing_known,created_at)
                   VALUES (97,901,'grok','Grok',901,'grok-4.6',1000,100,1100,100,2600,1,?)""",
                (db.now(),),
            )
        row = db.fetchone("SELECT * FROM credentials WHERE id=901")
        view = stats.fuse_view(row)
        self.assertEqual(view["cost_micros"], 2600)
        self.assertEqual(view["cost_label"], "$0.0026")


class SessionScopingTest(unittest.TestCase):
    def setUp(self):
        pool_session.persist = False
        pool_session.reset_store()

    def tearDown(self):
        pool_session.reset_store()
        pool_session.persist = True

    def test_same_raw_key_different_users_cannot_share_pin(self):
        from app.sources.pool_session import bind, lookup, session_id_from

        body = {"model": "grok-4.5", "messages": [{"role": "user", "content": "hi"}],
                "prompt_cache_key": "shared-key"}
        a = session_id_from(None, body, user_id=1, model="grok-4.5")
        b = session_id_from(None, body, user_id=2, model="grok-4.5")
        self.assertNotEqual(a, b)
        bind("grok", a, 10)
        bind("grok", b, 20)
        self.assertEqual(lookup("grok", a), 10)
        self.assertEqual(lookup("grok", b), 20)

    def test_fallback_prefix_collision_is_scoped(self):
        from app.sources.pool_session import session_id_from

        body = {"model": "grok-4.5",
                "messages": [{"role": "system", "content": "You are a coding assistant."},
                             {"role": "user", "content": "Open app/api.py"}]}
        a = session_id_from(None, body, user_id=1, model="grok-4.5")
        b = session_id_from(None, body, user_id=2, model="grok-4.5")
        self.assertNotEqual(a, b)


class RotationCursorTest(unittest.TestCase):
    def test_cursor_survives_source_reconstruction(self):
        openai_compat.reset_cursors()
        cred_a = {"id": 1, "disabled": 0}
        cred_b = {"id": 2, "disabled": 0}
        row = {"id": 5, "name": "t"}
        first = openai_compat.OpenAICompatSource(row, {"rotation": "round_robin"}, [cred_a, cred_b])
        second = openai_compat.OpenAICompatSource(row, {"rotation": "round_robin"}, [cred_a, cred_b])
        self.assertEqual([c["id"] for c in first._next_creds()], [1, 2])
        self.assertEqual([c["id"] for c in second._next_creds()], [2, 1])
        self.assertEqual([c["id"] for c in first._next_creds()], [1, 2])

    def test_sticky_prefers_pinned_credential(self):
        openai_compat.reset_cursors()
        pool_session.persist = False
        pool_session.reset_store()
        try:
            cred_a = {"id": 1, "disabled": 0}
            cred_b = {"id": 2, "disabled": 0}
            src = openai_compat.OpenAICompatSource(
                {"id": 6, "name": "t"}, {"rotation": "sticky"}, [cred_a, cred_b]
            )
            self.assertEqual([c["id"] for c in src._next_creds()], [1, 2])
            pool_session.bind("openai", "sess", 2)
            self.assertEqual([c["id"] for c in src._next_creds(prefer_id=2)], [2, 1])
        finally:
            pool_session.reset_store()
            pool_session.persist = True


def _sse(obj) -> bytes:
    import json
    return f"data: {json.dumps(obj)}\n\n".encode()


class TapAccountingTest(unittest.IsolatedAsyncioTestCase):
    async def test_complete_stream_recorded_success(self):
        from app.api import _tap

        chunks = [
            _sse({"choices": [{"delta": {"content": "hi"}}]}),
            _sse({"choices": [], "usage": {"prompt_tokens": 10, "completion_tokens": 2,
                                           "prompt_tokens_details": {"cached_tokens": 4}}}),
            b"data: [DONE]\n\n",
        ]

        async def gen():
            for c in chunks:
                yield c

        with mock.patch("app.api.db.record_usage") as rec:
            async for _ in _tap(gen(), 1, 1, None, "m", provider="grok"):
                pass
            rec.assert_called_once()
            usage = rec.call_args.kwargs if "usage" in rec.call_args.kwargs else None
            args, kwargs = rec.call_args
            self.assertEqual(kwargs.get("request_status"), "success")
            self.assertEqual(kwargs.get("provider"), "grok")
            self.assertEqual(args[4]["cached_tokens"], 4)
            self.assertEqual(args[4]["usage_status"], "complete")

    async def test_grok_stream_records_disjoint_visible(self):
        from app.api import _tap

        chunks = [
            _sse({"choices": [{"delta": {"content": "hi"}}]}),
            _sse({"choices": [], "usage": {
                "prompt_tokens": 1000,
                "completion_tokens": 5,
                "reasoning_tokens": 700,
                "total_tokens": 1705,
                "prompt_tokens_details": {"cached_tokens": 128},
            }}),
            b"data: [DONE]\n\n",
        ]

        async def gen():
            for c in chunks:
                yield c

        with mock.patch("app.api.db.record_usage") as rec:
            async for _ in _tap(gen(), 1, 1, None, "grok-4.6", provider="grok"):
                pass
            usage = rec.call_args[0][4]
            self.assertEqual(usage["prompt_tokens"], 1000)
            self.assertEqual(usage["completion_tokens"], 5)
            self.assertEqual(usage["output_tokens"], 705)
            self.assertEqual(usage["visible_tokens"], 5)
            self.assertEqual(usage["reasoning_tokens"], 700)
            self.assertEqual(usage["cached_tokens"], 128)
            self.assertEqual(usage["total_tokens"], 1705)

    async def test_truncated_stream_is_failed_partial(self):
        from app.api import _tap

        async def gen():
            yield _sse({"choices": [{"delta": {"content": "hi"}}]})
            yield _sse({"choices": [], "usage": {"prompt_tokens": 10, "completion_tokens": 2}})

        with mock.patch("app.api.db.record_usage") as rec:
            async for _ in _tap(gen(), 1, 1, None, "m", provider="grok"):
                pass
            _, kwargs = rec.call_args
            self.assertEqual(kwargs.get("request_status"), "failed")
            self.assertNotIn("count_request", kwargs)  # default True: counted
            # Usage frame arrived, so tokens are known even though [DONE] never did.
            self.assertEqual(rec.call_args[0][4]["usage_status"], "complete")

    async def test_cancelled_stream_marks_cancelled(self):
        from app.api import _tap

        async def gen():
            yield _sse({"choices": [{"delta": {"content": "hi"}}]})
            raise asyncio.CancelledError()

        with mock.patch("app.api.db.record_usage") as rec:
            with self.assertRaises(asyncio.CancelledError):
                async for _ in _tap(gen(), 1, 1, None, "m", provider="grok"):
                    pass
            _, kwargs = rec.call_args
            self.assertEqual(kwargs.get("request_status"), "cancelled")
            self.assertEqual(rec.call_args[0][4]["usage_status"], "unknown")


if __name__ == "__main__":
    unittest.main()
