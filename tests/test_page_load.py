"""Dashboard pages must not wait on live provider HTTP."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
_TMP = tempfile.mkdtemp(prefix="fuse-page-load-")
os.environ.setdefault("DATABASE_PATH", str(Path(_TMP) / "page-load.db"))
os.environ.setdefault("APP_SECRET", "x" * 32)

from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware

from app import accounts, db, sources as sources_mod
from app.config import settings
from app.security import SecurityHeadersMiddleware
from app.sources import grok as grok_mod
from app.sources import openrouter as or_mod
from app.web import router as web_router

import pytest

# Uses the database: a disposable MariaDB schema shared by this module.
pytestmark = pytest.mark.usefixtures("isolated_db")


class SourcesPageDoesNotProbeTest(unittest.TestCase):
    def setUp(self):
        db.execute("DELETE FROM credentials")
        db.execute("DELETE FROM api_keys")
        db.execute("DELETE FROM users")
        self.user = accounts.create_user("page@test.invalid", "password12", admin=False)
        sid = grok_mod.ensure_grok_source()
        accounts.add_credential(
            sid,
            "mine",
            "tok",
            extra={
                "access_token": "tok",
                "billing": {"fetched_at": "2026-01-01T00:00:00Z", "period_type": "month"},
            },
            donated_by=self.user["id"],
        )
        app = FastAPI()
        app.add_middleware(SecurityHeadersMiddleware)
        app.add_middleware(
            SessionMiddleware,
            secret_key=settings.app_secret,
            https_only=True,
            same_site="strict",
        )
        app.include_router(web_router)
        self.client = TestClient(app, base_url="https://testserver")
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        page = self.client.get("/login")
        token = page.text.split('name="csrf-token" content="', 1)[1].split('"', 1)[0]
        posted = self.client.post(
            "/login",
            data={"email": "page@test.invalid", "password": "password12", "csrf_token": token},
            follow_redirects=False,
        )
        self.assertEqual(posted.status_code, 303)

    def test_sources_renders_without_pool_refresh(self):
        with (
            patch("app.sources.grok.refresh_pool_billing", new_callable=AsyncMock) as grok,
            patch("app.sources.chatgpt.refresh_pool_usage", new_callable=AsyncMock) as chatgpt,
            patch("app.sources.zai.refresh_pool_quota", new_callable=AsyncMock) as zai,
            patch("app.sources.devin.refresh_pool_status", new_callable=AsyncMock) as devin,
            patch("app.sources.kimi.refresh_pool_quota", new_callable=AsyncMock) as kimi,
            patch("app.sources.openrouter.refresh_key_info", new_callable=AsyncMock) as or_key,
            patch("app.sources.openrouter.fetch_catalog", new_callable=AsyncMock) as catalog,
        ):
            page = self.client.get("/sources")
        self.assertEqual(page.status_code, 200)
        self.assertIn("Sources", page.text)
        grok.assert_not_called()
        chatgpt.assert_not_called()
        zai.assert_not_called()
        devin.assert_not_called()
        kimi.assert_not_called()
        or_key.assert_not_called()
        catalog.assert_not_called()

    def test_signed_in_pages_prefetch_nav_links(self):
        page = self.client.get("/usage")
        self.assertEqual(page.status_code, 200)
        raw = page.text.split('<script type="speculationrules">', 1)[1].split("</script>", 1)[0]
        rule = json.loads(raw)["prefetch"][0]
        self.assertIn(".app-rail-nav a[href]", rule["where"]["selector_matches"])
        self.assertIn(".user-dock-nav a[href]", rule["where"]["selector_matches"])
        self.assertEqual(rule["eagerness"], "moderate")
        # Without the keyword, CSP silently drops the inline rules.
        self.assertIn("'inline-speculation-rules'", page.headers["content-security-policy"])


class StaleModelCacheTest(unittest.IsolatedAsyncioTestCase):
    async def test_stale_hit_returns_immediately_and_schedules_refresh(self):
        from types import SimpleNamespace

        src = SimpleNamespace(type_id="llama", name="Local", source_id=99)
        key = (99, None)
        sources_mod._model_cache[key] = (time.time() - sources_mod._MODEL_TTL - 1, ["cached-model"])
        sources_mod._cache[99] = sources_mod._model_cache[key]
        refresh = AsyncMock(return_value=["fresh-model"])
        with (
            patch.object(sources_mod, "_refresh_source_models", new=refresh),
            patch.object(sources_mod, "_schedule_model_refresh") as schedule,
        ):
            names = await sources_mod._source_models(src, None)
        self.assertEqual(names, ["cached-model"])
        schedule.assert_called_once()
        refresh.assert_not_awaited()
        sources_mod._model_cache.pop(key, None)
        sources_mod._cache.pop(99, None)

    async def test_llama_miss_fetches_live_catalog(self):
        from types import SimpleNamespace

        src = SimpleNamespace(type_id="llama", name="Local", source_id=97, cfg={"model": "qwen3"})
        sources_mod._model_cache.pop((97, None), None)
        refresh = AsyncMock(return_value=["rotated-model"])
        with patch.object(sources_mod, "_refresh_source_models", new=refresh):
            names = await sources_mod._source_models(src, None)
        self.assertEqual(names, ["rotated-model"])
        refresh.assert_awaited_once()
        self.assertNotIn((97, None), sources_mod._model_cache)

    async def test_warm_catalogs_starts_every_shared_catalog(self):
        from types import SimpleNamespace

        pool = SimpleNamespace(type_id="chatgpt", source_id=11)
        local = SimpleNamespace(type_id="llama", source_id=12)
        cloud = SimpleNamespace(type_id="ollama", source_id=13)
        per_user = SimpleNamespace(type_id="openrouter", source_id=14)
        private = SimpleNamespace(type_id="explabs", source_id=15)
        with (
            patch.object(sources_mod, "all_enabled", return_value=[pool, local, cloud, per_user, private]),
            patch.object(sources_mod, "_schedule_model_refresh") as schedule,
        ):
            sources_mod.warm_catalogs()
        self.assertEqual([call.args for call in schedule.call_args_list], [(pool, None), (local, None), (cloud, None)])

    async def test_fresh_hit_does_not_refresh(self):
        from types import SimpleNamespace

        src = SimpleNamespace(type_id="llama", name="Local", source_id=98)
        key = (98, None)
        sources_mod._model_cache[key] = (time.time(), ["fresh"])
        with patch.object(sources_mod, "_schedule_model_refresh") as schedule:
            names = await sources_mod._source_models(src, None)
        self.assertEqual(names, ["fresh"])
        schedule.assert_not_called()
        sources_mod._model_cache.pop(key, None)


class OpenRouterCachedCatalogTest(unittest.IsolatedAsyncioTestCase):
    async def test_models_uses_cached_catalog(self):
        src = or_mod.OpenRouterSource({"id": 1, "name": "OpenRouter"}, {}, [])
        cached = {
            "by_id": {},
            "free_ids": [],
            "paid_ids": [],
            "all_ids": [],
        }
        with (
            patch.object(or_mod, "cached_catalog", return_value=cached),
            patch.object(or_mod, "fetch_catalog", new=AsyncMock()) as fetch,
            patch.object(or_mod, "models_for_user", return_value=["openrouter/x/y"]),
        ):
            names = await src.models(user_id=7)
        self.assertEqual(names, ["openrouter/x/y"])
        fetch.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
