"""Dashboard HTTP package wiring. No live providers."""
from __future__ import annotations

import os
import re
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

import pytest

from app import accounts, db, vault, web
from app.config import settings
from app.security import limiter
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


def _login(client, email, password):
    # The auth limiter is process-global and every test client shares an IP.
    page = client.get("/login").text
    token = re.search(r'name="csrf_token" value="([^"]+)"', page).group(1)
    posted = client.post(
        "/login",
        data={"email": email, "password": password, "csrf_token": token},
        follow_redirects=False,
    )
    assert posted.status_code == 303, posted.text
    meta = re.search(r'name="csrf-token" content="([^"]+)"', client.get("/chat").text)
    client.headers["x-csrf-token"] = meta.group(1)


class AccountSecurityTest(unittest.TestCase):
    """Password-change session binding, shared auth rate limit, admin guards."""

    pytestmark = pytest.mark.usefixtures("isolated_db")

    def setUp(self):
        limiter._hits.clear()
        db.execute("DELETE FROM api_keys")
        db.execute("DELETE FROM users")
        db.execute("DELETE FROM schema_meta WHERE key='first_user_claimed'")

    def _client(self):
        app = FastAPI()
        app.add_middleware(
            SessionMiddleware,
            secret_key=settings.app_secret,
            https_only=True,
            same_site="strict",
        )
        app.include_router(web.router)
        client = TestClient(app, base_url="https://testserver")
        client.__enter__()
        self.addCleanup(client.__exit__, None, None, None)
        return client

    def test_password_endpoints_share_the_login_rate_limit(self):
        accounts.create_user("rate@test.invalid", "password12", admin=False)
        client = self._client()
        _login(client, "rate@test.invalid", "password12")
        for _ in range(settings.auth_rate_per_minute - 1):
            wrong = client.post("/account/vault/unlock", json={"password": "wrong-password"})
            self.assertEqual(wrong.status_code, 401)
        limited = client.post("/account/vault/unlock", json={"password": "wrong-password"})
        self.assertEqual(limited.status_code, 429)
        blocked = client.post(
            "/account/password",
            json={"old_password": "password12", "new_password": "newpass12345"},
        )
        self.assertEqual(blocked.status_code, 429)

    def test_password_change_invalidates_other_sessions(self):
        accounts.create_user("rotate@test.invalid", "password12", admin=False)
        actor = self._client()
        other = self._client()
        _login(actor, "rotate@test.invalid", "password12")
        _login(other, "rotate@test.invalid", "password12")
        changed = actor.post(
            "/account/password",
            json={"old_password": "password12", "new_password": "newpass12345"},
        )
        self.assertEqual(changed.status_code, 200, changed.text)
        # The browser that changed it stays signed in (re-bound to the new
        # fingerprint); every cookie minted before the change is logged out.
        self.assertEqual(actor.get("/chat", follow_redirects=False).status_code, 200)
        kicked = other.get("/chat", follow_redirects=False)
        self.assertEqual(kicked.status_code, 303)
        self.assertEqual(kicked.headers.get("location"), "/login")

    def test_first_admin_registration_serializes(self):
        # The schema sentinel marks the first-admin claim: a registration that
        # lost the race becomes a normal (invited) user, not a second admin.
        first = accounts.create_user("first@test.invalid", "password12", require_invite=True)
        self.assertEqual(first["is_admin"], 1)
        with db.tx() as conn:
            conn.execute("DELETE FROM api_keys")
            conn.execute("DELETE FROM users")
            conn.execute("DELETE FROM schema_meta WHERE key='first_user_claimed'")
            conn.execute(
                "INSERT INTO schema_meta(key, value) VALUES('first_user_claimed', ?)",
                (db.now(),),
            )
        _email, token = accounts.add_whitelist("late@test.invalid")
        late = accounts.create_user("late@test.invalid", "password12", require_invite=True, invite=token)
        self.assertEqual(late["is_admin"], 0)

    def test_invite_requires_the_link_token_not_just_the_email(self):
        accounts.create_user("first@test.invalid", "password12", require_invite=True)
        _email, token = accounts.add_whitelist("invited@test.invalid")
        for wrong in ("", "guess", token + "x"):
            with self.assertRaises(ValueError) as ctx:
                accounts.register("invited@test.invalid", "attacker-pw1", wrong)
            self.assertEqual(str(ctx.exception), "closed")
        user = accounts.register("invited@test.invalid", "password12", token)
        self.assertEqual(user["email"], "invited@test.invalid")
        self.assertEqual(accounts.list_whitelist(), [])

    def test_last_admin_guard_and_delete_locks_vault(self):
        admin = accounts.create_user("root@test.invalid", "password12")
        other = accounts.create_user("other@test.invalid", "password12")
        with self.assertRaises(ValueError) as ctx:
            accounts.set_admin(admin["id"], False, actor_id=other["id"])
        self.assertEqual(str(ctx.exception), "last_admin")
        accounts.set_admin(other["id"], True, actor_id=admin["id"])
        accounts.set_admin(admin["id"], False, actor_id=other["id"])
        with self.assertRaises(ValueError):
            accounts.delete_user(other["id"], actor_id=admin["id"])
        vault.unlock(admin["id"], "password12")
        self.assertTrue(vault.unlocked(admin["id"]))
        accounts.delete_user(admin["id"], actor_id=other["id"])
        self.assertFalse(vault.unlocked(admin["id"]))


if __name__ == "__main__":
    unittest.main()
