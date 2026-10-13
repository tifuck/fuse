"""OpenRouter is per-account only; pool ids never fall back onto a personal key."""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_TMP = tempfile.mkdtemp(prefix="fuse-or-")
os.environ.setdefault("DATABASE_PATH", str(Path(_TMP) / "or.db"))
os.environ.setdefault("APP_SECRET", "x" * 32)

from app.sources import openrouter as or_mod  # noqa: E402
from app.sources.openrouter import (  # noqa: E402
    denied_reason,
    looks_like_openrouter_id,
    models_for_user,
    pick_key_for,
    resolve,
)

import pytest

# Uses the database: a disposable MariaDB schema shared by this module.
pytestmark = pytest.mark.usefixtures("isolated_db")


def _catalog() -> dict:
    return {
        "by_id": {
            "z-ai/glm-5.3-flash": {"id": "z-ai/glm-5.3-flash", "free": False},
            "z-ai/glm-5.3": {"id": "z-ai/glm-5.3", "free": False},
            "z-ai/glm-5.2:free": {"id": "z-ai/glm-5.2:free", "free": True},
            "x-ai/grok-4.6": {"id": "x-ai/grok-4.6", "free": False},
        },
        "free_ids": ["z-ai/glm-5.2:free"],
        "paid_ids": ["z-ai/glm-5.3-flash", "z-ai/glm-5.3", "x-ai/grok-4.6"],
        "all_ids": [
            "z-ai/glm-5.2:free",
            "z-ai/glm-5.3-flash",
            "z-ai/glm-5.3",
            "x-ai/grok-4.6",
        ],
    }


class MappingTest(unittest.TestCase):
    def test_devin_ids_are_not_openrouter(self):
        self.assertFalse(looks_like_openrouter_id("devin/gpt-6-astra"))
        self.assertFalse(looks_like_openrouter_id("devin/claude-opus-5"))
        self.assertFalse(looks_like_openrouter_id("devin/claude-sonnet-5"))
        self.assertTrue(looks_like_openrouter_id("openai/gpt-6-astra"))

    def test_reasoning_effort_maps_to_openrouter_reasoning(self):
        payload = or_mod._chat_payload(
            {"messages": [], "reasoning_effort": "high", "reasoning": {"exclude": False}},
            "x-ai/grok-4.6",
            free=False,
        )
        self.assertNotIn("reasoning_effort", payload)
        self.assertEqual(payload["reasoning"], {"exclude": False, "effort": "high"})


class AccessTest(unittest.TestCase):
    def test_no_user_key_lists_nothing(self):
        self.assertEqual(models_for_user(None, _catalog()), [])
        with patch.object(or_mod, "free_models_enabled", return_value=False):
            self.assertEqual(models_for_user(1, _catalog()), [])

    def test_user_key_lists_free_skus_only(self):
        with (
            patch.object(or_mod, "user_has_openrouter", return_value=True),
            patch.object(or_mod, "free_models_enabled", return_value=True),
            patch.object(or_mod, "paid_models_pref", return_value={"all": False, "enabled": []}),
        ):
            ids = models_for_user(1, _catalog())
        self.assertIn("openrouter/z-ai/glm-5.2:free", ids)
        self.assertNotIn("openrouter/z-ai/glm-5.3-flash", ids)
        self.assertNotIn("openrouter/z-ai/glm-5.3", ids)
        self.assertNotIn("openrouter/x-ai/grok-4.6", ids)

    def test_free_toggle_off_lists_nothing(self):
        with patch.object(or_mod, "free_models_enabled", return_value=False):
            self.assertEqual(models_for_user(1, _catalog()), [])

    def test_denied_without_personal_key(self):
        with patch.object(or_mod, "user_has_openrouter", return_value=False):
            self.assertIn(
                "Connect an OpenRouter API key",
                denied_reason("openrouter/z-ai/glm-5.3-flash", 1, _catalog()) or "",
            )
            self.assertIn(
                "Connect an OpenRouter API key",
                denied_reason("openrouter/z-ai/glm-5.2:free", 1, _catalog()) or "",
            )

    def test_paid_direct_denied_even_with_key(self):
        with (
            patch.object(or_mod, "user_has_openrouter", return_value=True),
            patch.object(or_mod, "free_models_enabled", return_value=True),
        ):
            reason = denied_reason("openrouter/z-ai/glm-5.3-flash", 1, _catalog()) or ""
        self.assertIn("not enabled for your account", reason)
        self.assertIn("Sources page", reason)

    def test_free_denied_when_toggle_off(self):
        with (
            patch.object(or_mod, "user_has_openrouter", return_value=True),
            patch.object(or_mod, "free_models_enabled", return_value=False),
        ):
            reason = denied_reason("openrouter/z-ai/glm-5.2:free", 1, _catalog()) or ""
        self.assertIn("turned off", reason)

    def test_free_allowed_with_key_and_toggle(self):
        with (
            patch.object(or_mod, "user_has_openrouter", return_value=True),
            patch.object(or_mod, "free_models_enabled", return_value=True),
        ):
            self.assertIsNone(denied_reason("openrouter/z-ai/glm-5.2:free", 1, _catalog()))

    def test_pick_key_ignores_global(self):
        self.assertIsNone(pick_key_for(None))
        with patch.object(or_mod, "user_credential", return_value=None):
            self.assertIsNone(pick_key_for(1))


class DirectResolveTest(unittest.TestCase):
    """Direct `openrouter/...` selection: free SKUs while the free toggle is
    on, paid SKUs only when the account's paid_models pref enables them."""

    def _run(self, model: str, *, has_key: bool, free_on: bool, paid: dict | None = None):
        async def run():
            with (
                patch.object(or_mod, "user_has_openrouter", return_value=has_key),
                patch.object(or_mod, "free_models_enabled", return_value=free_on),
                patch.object(or_mod, "paid_models_pref", return_value=paid or {"all": False, "enabled": []}),
                patch.object(or_mod, "fetch_catalog", return_value=_catalog()),
            ):
                return await resolve(model, 7)

        return asyncio.run(run())

    def test_free_direct_resolves(self):
        self.assertEqual(
            self._run("openrouter/z-ai/glm-5.2:free", has_key=True, free_on=True),
            ("z-ai/glm-5.2:free", True),
        )

    def test_paid_direct_blocked_when_not_enabled(self):
        self.assertIsNone(
            self._run(
                "openrouter/z-ai/glm-5.3-flash",
                has_key=True,
                free_on=True,
                paid={"all": False, "enabled": []},
            )
        )

    def test_paid_direct_resolves_when_enabled(self):
        self.assertEqual(
            self._run(
                "openrouter/z-ai/glm-5.3-flash",
                has_key=True,
                free_on=True,
                paid={"all": False, "enabled": ["z-ai/glm-5.3-flash"]},
            ),
            ("z-ai/glm-5.3-flash", False),
        )

    def test_free_direct_blocked_when_toggle_off(self):
        self.assertIsNone(self._run("openrouter/z-ai/glm-5.2:free", has_key=True, free_on=False))

    def test_no_key_never_resolves(self):
        self.assertIsNone(self._run("openrouter/z-ai/glm-5.2:free", has_key=False, free_on=False))


class SeededPoolCatalogTest(unittest.IsolatedAsyncioTestCase):
    async def test_zai_lists_seeded_models_when_pool_is_busy(self):
        from app.sources.zai import ZAI_MODELS, ZaiSource

        src = ZaiSource({"id": 1, "name": "Z.AI"}, {}, [])
        with patch.object(src, "_pick", return_value=None):
            names = await src.models()
        self.assertIn("glm-5.3-flash", names)
        self.assertEqual(names, list(ZAI_MODELS))

    async def test_grok_lists_seeded_models_when_pool_is_busy(self):
        from app.sources.grok import GROK_MODELS, GrokSource

        src = GrokSource({"id": 1, "name": "Grok"}, {}, [])
        with patch.object(src, "_pick", return_value=None):
            names = await src.models()
        self.assertIn("grok-4.6", names)
        self.assertEqual(names, list(GROK_MODELS))


class PoolIdDeniedTest(unittest.TestCase):
    def test_bare_pool_id_points_at_explicit_openrouter_sku(self):
        why = denied_reason("glm-5.3-flash", 7, _catalog())
        self.assertIn("openrouter/z-ai/glm-5.3-flash", why)
        self.assertIsNone(denied_reason("llama3", 7, _catalog()))


class ProviderRoutingTest(unittest.TestCase):
    PREFS = {"data_collection": "deny", "zdr": True, "models": {"z-ai/glm-5.3": ["relace", "morph"]}}

    def test_pinned_providers_and_privacy_reach_the_payload(self):
        payload = or_mod._chat_payload({"messages": []}, "z-ai/glm-5.3", free=False, prefs=self.PREFS)
        self.assertEqual(
            payload["provider"],
            {"only": ["relace", "morph"], "data_collection": "deny", "zdr": True, "allow_fallbacks": True},
        )
        pinned = or_mod._chat_payload(
            {"messages": []}, "z-ai/glm-5.3", free=False, session_id="thread-1", prefs=self.PREFS
        )
        # Stickiness comes from session_id; fallbacks stay enabled so a
        # provider outage is still survivable.
        self.assertTrue(pinned["provider"]["allow_fallbacks"])
        self.assertEqual(pinned["session_id"], "thread-1")
        # A caller may still opt out of fallbacks; the default never does.
        opted_out = or_mod._chat_payload(
            {"messages": [], "provider": {"allow_fallbacks": False}},
            "z-ai/glm-5.3",
            free=False,
            session_id="thread-1",
            prefs=self.PREFS,
        )
        self.assertFalse(opted_out["provider"]["allow_fallbacks"])

    def test_anthropic_models_get_caching_and_scoped_keys(self):
        payload = or_mod._chat_payload(
            {"messages": [], "prompt_cache_key": "raw-thread-7"},
            "anthropic/claude-sonnet-4-6",
            free=False,
            session_id="fx:scoped-1",
        )
        # Anthropic caching is opt-in via cache_control; a sticky session
        # alone only routes. Other providers must not see the global field.
        self.assertEqual(payload["cache_control"], {"type": "ephemeral", "ttl": "1h"})
        self.assertEqual(payload["session_id"], "fx:scoped-1")
        # The raw client key is overwritten with the scoped id, not forwarded.
        self.assertEqual(payload["prompt_cache_key"], "fx:scoped-1")

    def test_non_anthropic_models_get_no_global_cache_field(self):
        payload = or_mod._chat_payload(
            {"messages": [], "prompt_cache_key": "raw-thread-7"},
            "z-ai/glm-5.3", free=False, session_id="fx:scoped-1"
        )
        self.assertNotIn("cache_control", payload)
        self.assertEqual(payload["prompt_cache_key"], "fx:scoped-1")
        bare = or_mod._chat_payload({"messages": []}, "x-ai/grok-4.6", free=False, session_id=None)
        self.assertNotIn("cache_control", bare)
        self.assertNotIn("session_id", bare)

    def test_other_models_get_privacy_only(self):
        payload = or_mod._chat_payload({"messages": []}, "x-ai/grok-4.6", free=False, prefs=self.PREFS)
        self.assertNotIn("only", payload["provider"])
        self.assertEqual(payload["provider"]["data_collection"], "deny")

    def test_caller_provider_wins_and_free_cap_always_applies(self):
        payload = or_mod._chat_payload(
            {"messages": [], "provider": {"only": ["reka"]}}, "z-ai/glm-5.3", free=True, prefs=self.PREFS
        )
        self.assertEqual(payload["provider"]["only"], ["reka"])
        self.assertEqual(payload["provider"]["max_price"], {"prompt": 0, "completion": 0})

    def test_no_prefs_leaves_default_routing(self):
        payload = or_mod._chat_payload({"messages": []}, "z-ai/glm-5.3", free=False, prefs=None)
        self.assertEqual(payload["provider"], {"allow_fallbacks": True})


class ProviderPrefsStoreTest(unittest.TestCase):
    def _owned(self, extra):
        return patch.object(or_mod, "user_credential", return_value=({"id": 1}, extra))

    def test_set_and_clear_model_providers(self):
        extra: dict = {}
        with self._owned(extra), patch.object(or_mod.accounts, "update_extra"):
            self.assertTrue(or_mod.set_model_providers(1, "z-ai/glm-5.3", ["relace", " relace ", "", "morph"]))
            self.assertEqual(or_mod.provider_prefs(1)["models"], {"z-ai/glm-5.3": ["relace", "morph"]})
            self.assertTrue(or_mod.set_model_providers(1, "z-ai/glm-5.3", []))
            self.assertEqual(or_mod.provider_prefs(1)["models"], {})

    def test_model_cap(self):
        extra = {"provider_prefs": {"models": {f"a/m{i}": ["x"] for i in range(or_mod.MAX_PREF_MODELS)}}}
        with self._owned(extra), patch.object(or_mod.accounts, "update_extra"):
            self.assertFalse(or_mod.set_model_providers(1, "a/new", ["x"]))
            self.assertTrue(or_mod.set_model_providers(1, "a/m0", ["y"]))

    def test_privacy_round_trip(self):
        extra: dict = {}
        with self._owned(extra), patch.object(or_mod.accounts, "update_extra"):
            or_mod.set_privacy(1, data_collection_deny=True, zdr=False)
            prefs = or_mod.provider_prefs(1)
        self.assertEqual((prefs["data_collection"], prefs["zdr"]), ("deny", False))

    def test_set_paid_model_refuses_all_mode(self):
        with patch.object(or_mod, "paid_models_pref", return_value={"all": True, "enabled": []}):
            self.assertFalse(or_mod.set_paid_model(1, "z-ai/glm-5.3", True))


class EndpointParseTest(unittest.TestCase):
    def test_groups_by_provider_slug_and_sorts_by_price(self):
        items = [
            {"provider_name": "Google", "tag": "google-vertex/us", "pricing": {"prompt": "0.0000022", "completion": "0.00001"}, "status": 0},
            {"provider_name": "Google", "tag": "google-vertex/global", "pricing": {"prompt": "0.000002", "completion": "0.00001"}, "status": 0},
            {"provider_name": "Cheap", "tag": "cheap/fp8", "quantization": "fp8", "pricing": {"prompt": "0.0000001", "completion": "0.000001"}, "status": -2},
            {"provider_name": "Router", "tag": "router", "pricing": {"prompt": "-1", "completion": "-1"}},
        ]
        rows = or_mod._parse_endpoints(items, {"Google": {"slug": "google-vertex", "privacy_url": "", "headquarters": "US"}})
        self.assertEqual([r["slug"] for r in rows], ["cheap", "google-vertex", "router"])
        google = rows[1]
        self.assertEqual(len(google["variants"]), 2)
        self.assertEqual(google["input"], 2.0)
        self.assertFalse(rows[0]["up"])
        self.assertEqual(rows[0]["quantization"], "fp8")
        self.assertIsNone(rows[2]["input"])

    def test_unreachable_without_cache_is_none(self):
        class Boom:
            def __init__(self, *a, **k):
                pass

            async def __aenter__(self):
                raise RuntimeError("down")

            async def __aexit__(self, *a):
                return False

        or_mod._endpoints.pop("q/none", None)
        with patch.object(or_mod.httpx, "AsyncClient", Boom):
            self.assertIsNone(asyncio.run(or_mod.fetch_model_endpoints("q/none")))


if __name__ == "__main__":
    unittest.main()


class AuthFailureTest(unittest.TestCase):
    def test_moderation_metadata_is_not_an_auth_failure(self):
        body = {"error": {"code": 403, "message": "Input was flagged",
                          "metadata": {"flagged_input": "why is my api unauthorized"}}}
        self.assertFalse(or_mod._is_auth_failure(403, body))
        self.assertTrue(or_mod._is_auth_failure(403, {"error": {"message": "Invalid API key"}}))
