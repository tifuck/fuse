"""Usage page analytics (/usage.json) in a disposable MariaDB schema."""
import os
from datetime import datetime, timedelta, timezone

import pytest

from app import db, stats
from tests.mariadb_fixture import isolated_database

pytestmark = pytest.mark.skipif(os.environ.get("FUSE_CHAT_TEST_MODE") != "1", reason="requires disposable MariaDB fixture")


def _ago(**delta):
    return (datetime.now(timezone.utc) - timedelta(**delta)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _event(at, model="glm-5.3", provider="zai", key=None, status="success", prompt=1000, cached=600, output=200, cost=50):
    db.execute(
        """INSERT INTO usage_events (user_id, api_key_id, source_type, source_name, model, requested_model,
               prompt_tokens, completion_tokens, output_tokens, visible_tokens, reasoning_tokens, cached_tokens,
               total_tokens, estimated_cost_micros, pricing_known, request_status, count_request, created_at)
           VALUES (1,?,?,?,?,?,?,?,?,?,?,?,?,?,1,?,1,?)""",
        (key, provider, provider, model, model, prompt, output, output, output - 50, 50, cached, prompt + output, cost, status, at),
    )


def _seed():
    ts = db.now()
    db.execute("INSERT INTO users(id,email,password_hash,created_at) VALUES(1,'usage@test.invalid','unused',?)", (ts,))
    db.execute("INSERT INTO api_keys(id,user_id,name,prefix,key_hash,created_at) VALUES(7,1,'Laptop','sk-fuse-ab',?,?)", ("h", ts))
    db.execute(
        """INSERT INTO model_prices(provider, model, input_micros_per_million, cached_input_micros_per_million,
               cached_input_known, output_micros_per_million, updated_at) VALUES('zai','glm-5.3',1000000,100000,1,2000000,?)""",
        (ts,),
    )


def test_summary_reliability_sources_and_models():
    with isolated_database():
        _seed()
        _event(_ago(hours=1))
        _event(_ago(hours=2), key=7, status="failed")
        _event(_ago(hours=3), key=7, status="cancelled")
        _event(_ago(hours=30))  # previous window only
        data = stats.usage_analytics(1, range_id="24h")
        s = data["summary"]
        assert (s["requests"], s["ok_requests"], s["failed"], s["cancelled"]) == (3, 1, 1, 1)
        assert s["success_rate"] == 33.3
        assert s["per_request"] == 1200
        # 1800 cached tokens at (1.00 - 0.10) $/M.
        assert s["cache_saved"] == 1620 and s["cache_saved_known"]
        sources = {row["kind"]: row for row in data["keys"]}
        assert sources["chat"]["requests"] == 1 and sources["chat"]["name"] == "Fuse chat & agents"
        assert sources["key"]["name"] == "Laptop" and sources["key"]["failed"] == 1
        model = data["models"][0]
        assert model["failed"] == 1 and model["error_rate"] == 33.3 and model["cache_hit"] == 60.0
        assert len(model["spark"]) == len(data["series"])
        assert data["recent"][0]["via_kind"] == "chat" and data["recent"][1]["via"] == "Laptop"
        assert data["series_models"][0]["provider_label"]


def test_heatmap_spans_a_week_even_for_short_ranges():
    with isolated_database():
        _seed()
        _event(_ago(days=5))
        _event(_ago(minutes=5))
        short = stats.usage_analytics(1, range_id="6h")
        assert short["summary"]["requests"] == 1
        assert short["activity_days"] == 7
        assert sum(requests for _, _, requests in short["activity"]) == 2
        for hour, _, _ in short["activity"]:
            assert hour % 3600 == 0
        assert stats.usage_analytics(1, range_id="30d")["activity_days"] == 30


def test_provider_filter_and_empty_range():
    with isolated_database():
        _seed()
        _event(_ago(hours=1))
        _event(_ago(hours=1), model="grok-4.5", provider="grok")
        data = stats.usage_analytics(1, range_id="24h", provider="grok")
        assert [m["name"] for m in data["models"]] == ["grok-4.5"]
        empty = stats.usage_analytics(1, range_id="30m", provider="kimi")
        assert empty["summary"]["empty"] and empty["summary"]["success_rate"] is None
        assert empty["models"] == [] and empty["recent"] == []


def test_rates_use_priced_traffic_and_openrouter_is_not_unpriced():
    with isolated_database():
        _seed()
        _event(_ago(hours=1), prompt=1000, cached=0, output=0, cost=2000)  # priced: 1000 tokens, $0.002
        _event(_ago(hours=1), model="qwen3", provider="llama", prompt=9000, cached=0, output=0, cost=0)
        db.execute("UPDATE usage_events SET pricing_known=0 WHERE source_type='llama'")
        _event(_ago(hours=1), model="openrouter/x/y", provider="openrouter", prompt=5000, cached=0, output=0, cost=0)
        db.execute("UPDATE usage_events SET pricing_known=0 WHERE source_type='openrouter'")
        s = stats.usage_analytics(1, range_id="24h")["summary"]
        # $2000 micros over the 1000 priced tokens, not all 15,000.
        assert s["blended"] == 2.0
        assert s["cost_per_request"] == 2000
        # OpenRouter bills the user's own key: 1 of 2 costable requests priced.
        assert s["pricing_coverage"] == 50
        models = {m["provider"]: m for m in stats.usage_analytics(1, range_id="24h")["models"]}
        assert models["openrouter"]["cost_tracked"] is False and models["zai"]["cost_tracked"] is True


def test_events_without_a_user_row_stay_in_series_and_summary():
    with isolated_database():
        _seed()
        _event(_ago(hours=1))
        db.execute("DELETE FROM users WHERE id=1")
        data = stats.usage_analytics(1, range_id="24h")
        assert data["summary"]["requests"] == 1
        assert data["models"][0]["requests"] == 1
        assert any(point["requests"] for point in data["series"])
        everyone = stats.usage_analytics(1, range_id="24h", all_users=True)
        assert everyone["summary"]["requests"] == 1
        assert everyone["users"][0]["requests"] == 1
        assert everyone["users"][0]["email"] == "user 1"
