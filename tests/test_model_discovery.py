"""New provider models: listed from live catalogs, routed to their pool, auto-priced."""
import asyncio
import os
from unittest.mock import patch

import pytest

from app import db, pricing
from app.sources import chatgpt, openrouter
from tests.mariadb_fixture import isolated_database

CODEX_CATALOG = {
    "models": [
        {"slug": "gpt-6.1-sol", "visibility": "list"},
        {"slug": "gpt-6-sol", "visibility": "list"},
        {"slug": "gpt-reserve", "visibility": "hide"},
        {"slug": "codex-auto-review", "visibility": "hide"},
    ]
}


@pytest.fixture
def fresh_chatgpt_cache():
    saved = chatgpt._models_cache
    chatgpt._models_cache = None
    try:
        yield
    finally:
        chatgpt._models_cache = saved


def test_codex_catalog_lists_visible_slugs_only():
    assert chatgpt.parse_catalog_payload(CODEX_CATALOG) == ["gpt-6.1-sol", "gpt-6-sol"]
    assert chatgpt.parse_catalog_payload({"detail": "nope"}) == []


def test_codex_catalog_url_is_the_codex_backend():
    assert chatgpt._models_url() == chatgpt.settings.openai_chatgpt_base.rstrip("/") + "/models"


def test_new_codex_model_is_a_pool_model_once_listed(fresh_chatgpt_cache):
    assert not chatgpt.is_chatgpt_model("gpt-6.1-sol")
    chatgpt._remember_models(chatgpt.parse_catalog_payload(CODEX_CATALOG))
    assert chatgpt.is_chatgpt_model("gpt-6.1-sol")
    # Seeds stay listed even when the live catalog omits them.
    assert chatgpt.is_chatgpt_model("gpt-5.4")


def test_openrouter_alias_includes_live_pool_models(fresh_chatgpt_cache):
    catalog = {"by_id": {"openai/gpt-6.1-sol": {"id": "openai/gpt-6.1-sol"}}}
    assert "gpt-6.1-sol" not in openrouter.pool_aliases_from_catalog(catalog)["chatgpt"]
    chatgpt._remember_models(["gpt-6.1-sol"])
    assert "gpt-6.1-sol" in openrouter.pool_aliases_from_catalog(catalog)["chatgpt"]


def test_pick_keeps_new_codex_model_on_the_pool(fresh_chatgpt_cache):
    from app import sources

    class Src:
        type_id = "chatgpt"

    chatgpt._remember_models(["gpt-6.1-sol"])
    with patch.object(sources.db, "fetchone", return_value=None), patch.object(
        sources, "all_enabled", return_value=[Src()]
    ):
        picked = asyncio.run(sources.pick("gpt-6.1-sol", allow_chatgpt=True, user_id=7))
    assert picked is not None and picked[0].type_id == "chatgpt" and picked[1] == "gpt-6.1-sol"


def test_price_keys_unify_spellings_and_strip_sku_tails():
    assert pricing.price_key("devin/gpt-5-6-sol") == pricing.price_key("gpt-5.6-sol")
    assert pricing.price_key("openrouter/anthropic/claude-sonnet-5.5") == "claude-sonnet-5-5"
    assert pricing.price_key("openai/gpt-oss-120b:free") == "gpt-oss-120b"
    assert pricing.price_keys("gpt-5-6-sol-none-priority") == [
        "gpt-5-6-sol-none-priority",
        "gpt-5-6-sol-none",
        "gpt-5-6-sol",
    ]
    # A real SKU that ends in a tail word is still tried whole first.
    assert pricing.price_keys("swe-1-6-fast")[0] == "swe-1-6-fast"


def _price(provider, model):
    return db.fetchone("SELECT * FROM model_prices WHERE provider=? AND model=?", (provider, model))


@pytest.mark.skipif(os.environ.get("FUSE_CHAT_TEST_MODE") != "1", reason="requires disposable MariaDB fixture")
def test_auto_price_fills_gaps_and_reprices_history(fresh_chatgpt_cache):
    with isolated_database():
        db.execute(
            "INSERT INTO users(id,email,password_hash,created_at) VALUES(1,'price@test.invalid','unused',?)",
            (db.now(),),
        )
        pricing.ensure_default_prices()
        db.save_model_price("devin", "claude-opus-5", 1_000_000, None, 2_000_000, None, origin="manual")
        usage = {"prompt_tokens": 100_000, "completion_tokens": 0, "total_tokens": 100_000}
        db.record_usage(1, None, None, "gpt-5-6-sol-low", usage, provider="devin")
        event = db.fetchone("SELECT * FROM usage_events WHERE model='gpt-5-6-sol-low'")
        assert event["pricing_known"] == 0

        chatgpt._remember_models(["gpt-6.1-sol"])
        catalog = {
            "by_id": {
                "openai/gpt-6.1-sol": {
                    "id": "openai/gpt-6.1-sol",
                    "free": False,
                    "pricing": {"prompt": "0.000003", "completion": "0.000012"},
                }
            }
        }
        priced = pricing.auto_price(catalog)
        assert "devin/gpt-5-6-sol-low" in priced
        assert "chatgpt/gpt-6.1-sol" in priced

        # Devin's own gpt-5.6-sol default ($4/M input) wins over ChatGPT's row.
        event = db.fetchone("SELECT * FROM usage_events WHERE id=?", (event["id"],))
        assert event["pricing_known"] == 1
        assert event["estimated_cost_micros"] == 400_000

        row = _price("chatgpt", "gpt-6.1-sol")
        assert row["origin"] == "auto"
        assert row["input_micros_per_million"] == 3_000_000
        assert row["openrouter_id"] == "openai/gpt-6.1-sol"

        # Idempotent: nothing changes on a second pass.
        assert pricing.auto_price(catalog) == []
        # A manual row is never touched, and a sync replaces an auto row.
        db.save_model_price("chatgpt", "gpt-6.1-sol", 5_000_000, None, 6_000_000, None, origin="openrouter")
        assert pricing.auto_price(catalog) == []
        assert _price("chatgpt", "gpt-6.1-sol")["origin"] == "openrouter"
        assert _price("devin", "claude-opus-5")["input_micros_per_million"] == 1_000_000
