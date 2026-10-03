"""OpenRouter is per-account only; pool ids can map onto a personal key."""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

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
    pool_aliases_from_catalog,
    pool_openrouter_id,
    resolve,
    resolve_pool_alias,
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
    def test_pool_ids_map_to_openrouter_upstream(self):
        self.assertEqual(pool_openrouter_id("glm-5.3-flash"), "z-ai/glm-5.3-flash")
        self.assertEqual(pool_openrouter_id("grok-4.6"), "x-ai/grok-4.6")
        self.assertEqual(pool_openrouter_id("gpt-5.4"), "openai/gpt-5.4")
        self.assertIsNone(pool_openrouter_id("openrouter/z-ai/glm-5.3-flash"))
        self.assertIsNone(pool_openrouter_id("z-ai/glm-5.3-flash"))
        self.assertIsNone(pool_openrouter_id("llama3"))
        self.assertIsNone(pool_openrouter_id("devin/gpt-6-astra"))
        self.assertIsNone(pool_openrouter_id("devin/claude-opus-5"))

    def test_devin_ids_are_not_openrouter(self):
        self.assertFalse(looks_like_openrouter_id("devin/gpt-6-astra"))
        self.assertFalse(looks_like_openrouter_id("devin/claude-opus-5"))
        self.assertFalse(looks_like_openrouter_id("devin/claude-sonnet-5"))
        self.assertTrue(looks_like_openrouter_id("openai/gpt-6-astra"))

    def test_aliases_skip_free_skus_and_missing(self):
        aliases = pool_aliases_from_catalog(_catalog())
        self.assertIn("glm-5.3-flash", aliases["zai"])
        self.assertIn("glm-5.3", aliases["zai"])
        self.assertNotIn("glm-5.2", aliases["zai"])
        self.assertIn("grok-4.6", aliases["grok"])
        self.assertEqual(aliases["chatgpt"], [])

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
    """Direct `openrouter/...` selection is free-only; paid SKUs only run as
    the automatic pool fallback (resolve_pool_alias)."""

    def _run(self, model: str, *, has_key: bool, free_on: bool):
        async def run():
            with (
                patch.object(or_mod, "user_has_openrouter", return_value=has_key),
                patch.object(or_mod, "free_models_enabled", return_value=free_on),
                patch.object(or_mod, "fetch_catalog", return_value=_catalog()),
            ):
                return await resolve(model, 7)

        return asyncio.run(run())

    def test_free_direct_resolves(self):
        self.assertEqual(
            self._run("openrouter/z-ai/glm-5.2:free", has_key=True, free_on=True),
            ("z-ai/glm-5.2:free", True),
        )

    def test_paid_direct_never_resolves(self):
        self.assertIsNone(self._run("openrouter/z-ai/glm-5.3-flash", has_key=True, free_on=True))

    def test_free_direct_blocked_when_toggle_off(self):
        self.assertIsNone(self._run("openrouter/z-ai/glm-5.2:free", has_key=True, free_on=False))

    def test_no_key_never_resolves(self):
        self.assertIsNone(self._run("openrouter/z-ai/glm-5.2:free", has_key=False, free_on=False))


class AliasResolveTest(unittest.TestCase):
    def test_maps_when_user_has_key_and_catalog_hit(self):
        async def run():
            with (
                patch.object(or_mod, "user_has_openrouter", return_value=True),
                patch.object(or_mod, "fetch_catalog", return_value=_catalog()),
            ):
                return await resolve_pool_alias("glm-5.3-flash", 7)

        self.assertEqual(asyncio.run(run()), "z-ai/glm-5.3-flash")

    def test_no_map_without_key(self):
        async def run():
            with patch.object(or_mod, "user_has_openrouter", return_value=False):
                return await resolve_pool_alias("glm-5.3-flash", 7, _catalog())

        self.assertIsNone(asyncio.run(run()))

    def test_no_map_for_missing_catalog_id(self):
        async def run():
            with (
                patch.object(or_mod, "user_has_openrouter", return_value=True),
                patch.object(or_mod, "fetch_catalog", return_value=_catalog()),
            ):
                return await resolve_pool_alias("glm-5.3-highspeed", 7)

        self.assertIsNone(asyncio.run(run()))


class BusyPoolListingTest(unittest.IsolatedAsyncioTestCase):
    async def test_empty_zai_group_still_gets_openrouter_aliases(self):
        from types import SimpleNamespace

        from app.sources import models_by_source

        zai = SimpleNamespace(type_id="zai", name="Z.AI", source_id=1)
        with (
            patch("app.sources._sources", return_value=[zai]),
            patch("app.sources._source_models", new=AsyncMock(return_value=[])),
            patch("app.sources.user_has_openrouter", return_value=True),
            patch("app.sources.openrouter.fetch_catalog", new=AsyncMock(return_value=_catalog())),
        ):
            groups = await models_by_source(allow_zai=True, user_id=7)
        row = next(g for g in groups if g["type"] == "zai")
        self.assertIn("glm-5.3-flash", row["models"])
        self.assertIn("glm-5.3", row["models"])


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


if __name__ == "__main__":
    unittest.main()
