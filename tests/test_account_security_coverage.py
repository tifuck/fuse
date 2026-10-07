"""Account-security coverage around /account/password and /account/vault/unlock.

- a wrong old password is rejected and changes nothing;
- a successful change rotates the password fingerprint (app/web/deps.py
  stamps it into every session as "ph"), logging out every cookie minted
  before the change while the browser that made the change stays signed in,
  and a session with no stamp (minted before the stamp existed) passes;
- both endpoints share the login limiter and answer 429 once it is
  exhausted — and only then: the same request succeeds after the window is
  cleared.
"""
import base64
import json
import os
import re

import itsdangerous
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware

from app import accounts, web
from app.config import settings
from app.security import limiter
from tests.mariadb_fixture import isolated_database

pytestmark = pytest.mark.skipif(
    os.environ.get("FUSE_CHAT_TEST_MODE") != "1", reason="requires disposable MariaDB fixture"
)

_PASSWORD = "password12"
_NEW_PASSWORD = "newpass12345"


@pytest.fixture()
def app():
    with isolated_database():
        accounts.create_user("owner@account.invalid", _PASSWORD, admin=False)
        app = FastAPI()
        app.add_middleware(
            SessionMiddleware, secret_key=settings.app_secret, https_only=True, same_site="strict"
        )
        app.include_router(web.router)
        yield app


@pytest.fixture()
def client(app):
    with TestClient(app, base_url="https://testserver") as http:
        yield http


def _login(client, email="owner@account.invalid"):
    # The auth limiter is process-global (10/min) and every test client
    # shares an IP; a stale CSRF header would also fail the next session.
    limiter._hits.clear()
    client.headers.pop("x-csrf-token", None)
    page = client.get("/login").text
    token = re.search(r'name="csrf_token" value="([^"]+)"', page).group(1)
    posted = client.post(
        "/login",
        data={"email": email, "password": _PASSWORD, "csrf_token": token},
        follow_redirects=False,
    )
    assert posted.status_code == 303, posted.text
    meta = re.search(r'name="csrf-token" content="([^"]+)"', client.get("/chat").text)
    client.headers["x-csrf-token"] = meta.group(1)


def _uid():
    return accounts.user_by_email("owner@account.invalid")["id"]


def test_wrong_old_password_is_rejected_and_changes_nothing(client):
    _login(client)
    before = accounts.password_fingerprint(accounts.get_user(_uid()))
    wrong = client.post(
        "/account/password",
        json={"old_password": "not-the-password", "new_password": _NEW_PASSWORD},
    )
    assert wrong.status_code == 401
    assert wrong.json()["detail"] == "Current password is wrong."
    # No change: the old password still authenticates, the new one does not,
    # and the fingerprint the sessions are stamped with is untouched.
    user = accounts.get_user(_uid())
    assert accounts.login("owner@account.invalid", _PASSWORD)["id"] == user["id"]
    assert accounts.login("owner@account.invalid", _NEW_PASSWORD) is None
    assert accounts.password_fingerprint(user) == before


def test_password_change_invalidates_other_sessions_but_not_the_changer(app, client):
    with TestClient(app, base_url="https://testserver") as bystander:
        _login(client)
        _login(bystander)
        user = accounts.get_user(_uid())
        stale_stamp = accounts.password_fingerprint(user)

        changed = client.post(
            "/account/password",
            json={"old_password": _PASSWORD, "new_password": _NEW_PASSWORD},
        )
        assert changed.status_code == 200, changed.text
        # The stored hash rotated, so the stamp every session carries moved.
        fresh = accounts.get_user(_uid())
        assert accounts.password_fingerprint(fresh) != stale_stamp

        # The browser that made the change stays signed in (re-stamped);
        # every cookie minted before it is logged out.
        assert client.get("/chat", follow_redirects=False).status_code == 200
        kicked = bystander.get("/chat", follow_redirects=False)
        assert kicked.status_code == 303
        assert kicked.headers["location"] == "/login"

        # A session with no stamp predates the stamp mechanism and passes
        # unchanged (deps.current_user: `stamped is None`).
        signer = itsdangerous.TimestampSigner(settings.app_secret)
        legacy = signer.sign(base64.b64encode(json.dumps({"uid": _uid()}).encode())).decode()
        bystander.cookies.clear()
        bystander.cookies.set("session", legacy)
        assert bystander.get("/chat", follow_redirects=False).status_code == 200


def test_password_and_vault_unlock_share_the_login_rate_limit(client):
    _login(client)  # the login itself consumes one auth-window hit
    for _ in range(settings.auth_rate_per_minute - 1):
        wrong = client.post("/account/vault/unlock", json={"password": "wrong-password"})
        assert wrong.status_code == 401
    # The window is exhausted: both endpoints answer 429, even with the
    # correct password.
    limited = client.post("/account/vault/unlock", json={"password": _PASSWORD})
    assert limited.status_code == 429
    blocked = client.post(
        "/account/password",
        json={"old_password": _PASSWORD, "new_password": _NEW_PASSWORD},
    )
    assert blocked.status_code == 429

    # The same requests succeed once the window is managed clear, proving
    # the 429 was the shared limiter and not an account lockout.
    limiter._hits.clear()
    unlocked = client.post("/account/vault/unlock", json={"password": _PASSWORD})
    assert unlocked.status_code == 200
    changed = client.post(
        "/account/password",
        json={"old_password": _PASSWORD, "new_password": _NEW_PASSWORD},
    )
    assert changed.status_code == 200
