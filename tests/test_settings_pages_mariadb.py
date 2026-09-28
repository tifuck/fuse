"""Reorganized settings pages: navigation, moved sections, and the saves they
carry, in a disposable MariaDB schema."""
import os
import re

import pytest

from app import accounts, db
from app.sources import pool_policy
from tests.mariadb_fixture import isolated_database

pytestmark = pytest.mark.skipif(
    os.environ.get("FUSE_CHAT_TEST_MODE") != "1", reason="requires disposable MariaDB fixture"
)


def _csrf(html: str) -> str:
    match = re.search(r'name="csrf-token" content="([^"]+)"', html)
    assert match, "csrf token missing"
    return match.group(1)


@pytest.fixture()
def client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from starlette.middleware.sessions import SessionMiddleware

    from app import web
    from app.config import settings

    with isolated_database():
        accounts.create_user("admin@test.invalid", "password12", admin=True)
        accounts.create_user("user@test.invalid", "password12", admin=False)
        app = FastAPI()
        app.add_middleware(SessionMiddleware, secret_key=settings.app_secret, https_only=True, same_site="strict")
        app.include_router(web.router)
        with TestClient(app, base_url="https://testserver") as c:
            yield c


def _login(client, email):
    page = client.get("/login").text
    token = re.search(r'name="csrf_token" value="([^"]+)"', page).group(1)
    posted = client.post("/login", data={"email": email, "password": "password12", "csrf_token": token}, follow_redirects=False)
    assert posted.status_code == 303


def test_nav_groups_and_admin_pages(client):
    _login(client, "admin@test.invalid")
    page = client.get("/settings")
    assert page.status_code == 200
    for label in ("Workspace", "Models &amp; usage", "Developer", "Account", "Admin"):
        assert f">{label}</p>" in page.text
    for href in ("/keys", "/settings", "/admin/pools", "/admin/pricing", "/admin/web"):
        assert f'href="{href}"' in page.text
    assert 'href="/settings" class="on" aria-current="page"' in page.text
    for path, marker in (
        ("/admin/pools", "Who can use donated accounts"),
        ("/admin/pricing", "Sync catalog rates"),
        ("/admin/web", "Page fetch domains"),
        ("/admin/sources", "Add OpenAI-compatible"),
        ("/keys", "Your keys"),
    ):
        resp = client.get(path)
        assert resp.status_code == 200, path
        assert marker in resp.text, path
    providers = client.get("/admin/sources").text
    assert "Who can use donated accounts" not in providers
    assert "fetch-domains" not in providers
    usage = client.get("/usage").text
    assert "/admin/pricing" not in usage.split('<main id="content"')[1]
    assert 'action="/keys"' not in usage


def test_pool_access_saves_every_kind_including_claude(client):
    _login(client, "admin@test.invalid")
    csrf = _csrf(client.get("/admin/pools").text)
    saved = client.post(
        "/admin/pools",
        data={"csrf_token": csrf, "claude_pool_shared": "1", "claude_open_access": "1", "grok_pool_shared": "1"},
        follow_redirects=False,
    )
    assert saved.status_code == 303
    assert saved.headers["location"] == "/admin/pools"
    claude = pool_policy.admin_policy("claude")
    assert claude["pool_shared"] is True and claude["open_access"] is True
    assert pool_policy.admin_policy("grok")["pool_shared"] is True
    assert pool_policy.admin_policy("zai")["pool_shared"] is False


def test_created_api_key_is_revealed_once(client):
    _login(client, "user@test.invalid")
    csrf = _csrf(client.get("/keys").text)
    created = client.post("/keys", data={"csrf_token": csrf, "name": "Laptop"}, follow_redirects=False)
    assert created.status_code == 303
    assert created.headers["location"] == "/keys?created=1"
    shown = client.get("/keys?created=1").text
    assert "API key created" in shown
    assert re.search(r'data-copy="sk-[^"]+"', shown)
    assert "API key created" not in client.get("/keys?created=1").text


def test_members_cannot_open_admin_pages_and_see_settings(client):
    _login(client, "user@test.invalid")
    for path in ("/admin/pools", "/admin/pricing", "/admin/web"):
        resp = client.get(path, follow_redirects=False)
        assert resp.status_code in (303, 403), path
    page = client.get("/settings").text
    assert ">Admin</p>" not in page
    assert 'name="agent_coding"' in page
    assert "data-password-form" in page
    db.execute("UPDATE users SET chat_profile='Berlin' WHERE email='user@test.invalid'")
    assert "Berlin" in client.get("/settings").text
