"""Dashboard HTTP package wiring. No live providers."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
_TMP = tempfile.mkdtemp(prefix="fuse-web-routes-")
os.environ.setdefault("DATABASE_PATH", str(Path(_TMP) / "web.db"))
os.environ.setdefault("APP_SECRET", "x" * 32)

from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware

from app import web
from app.config import settings
from app.web import _opencode_model_config, _reasoning_default, _reasoning_efforts, current_user, render


class WebPackageExportsTest(unittest.TestCase):
    def test_router_and_helpers_are_exported(self):
        self.assertTrue(hasattr(web, "router"))
        self.assertTrue(callable(current_user))
        self.assertTrue(callable(render))
        self.assertEqual(_reasoning_default("grok"), "medium")
        self.assertEqual(_reasoning_efforts("zai"), ("none", "high"))
        self.assertIn("variants", _opencode_model_config("Grok", "grok"))

    def test_chat_and_auth_routes_are_registered(self):
        app = FastAPI()
        app.add_middleware(
            SessionMiddleware,
            secret_key=settings.app_secret,
            https_only=True,
            same_site="strict",
        )
        app.include_router(web.router)
        client = TestClient(app, base_url="https://testserver")
        login = client.get("/login")
        self.assertEqual(login.status_code, 200)
        self.assertIn(b"Fuse", login.content)
        chat = client.get("/chat", follow_redirects=False)
        self.assertEqual(chat.status_code, 303)
        self.assertEqual(chat.headers.get("location"), "/login")
        hosts = client.get("/hosts", follow_redirects=False)
        self.assertEqual(hosts.status_code, 303)

    def test_artifact_routes_require_sign_in(self):
        app = FastAPI()
        app.add_middleware(
            SessionMiddleware,
            secret_key=settings.app_secret,
            https_only=True,
            same_site="strict",
        )
        app.include_router(web.router)
        client = TestClient(app, base_url="https://testserver")
        page = client.get("/artifacts", follow_redirects=False)
        self.assertEqual(page.status_code, 303)
        self.assertEqual(page.headers.get("location"), "/login")
        # Signed-out writes are stopped by the CSRF guard before auth.
        archive = client.post("/a/abcd1234/archive")
        self.assertEqual(archive.status_code, 403)
        restore = client.post("/a/abcd1234/restore")
        self.assertEqual(restore.status_code, 403)
        deleted = client.post("/a/abcd1234/delete")
        self.assertEqual(deleted.status_code, 403)


if __name__ == "__main__":
    unittest.main()
