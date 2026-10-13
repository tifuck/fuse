"""The authorization matrix around per-user and admin-only resources.

Three boundaries, each asserted as "blocked AND no effect" with a positive
control proving the route itself works for its intended caller:

- non-admins on admin mutations (/admin/users role/delete/invite-delete,
  the pricing mutation routes, /admin/issues*): rejected with no state
  change. Committed behavior: the form-backed dashboard routes redirect a
  non-admin to "/" (303), the JSON admin routes answer 401.
- cross-user isolation: user B gets 404/403 on user A's hosts, artifacts,
  threads and runs, and cannot remove A's push subscription or API key.
- serialized host payloads never carry the stored secret/passphrase.
"""
import base64
import os
import re

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware

from app import accounts, artifacts, chats, chat_store, db, hosts, issues, push, vault, web
from app.config import settings
from app.security import limiter
from tests.mariadb_fixture import isolated_database

pytestmark = pytest.mark.skipif(
    os.environ.get("FUSE_CHAT_TEST_MODE") != "1", reason="requires disposable MariaDB fixture"
)

_PASSWORD = "password12"
# Committed rejection codes: the dashboard's admin form routes redirect a
# signed-in non-admin home; the JSON admin routes answer 401.
_ADMIN_BLOCKED_FORM = 303
_ADMIN_BLOCKED_JSON = 401


@pytest.fixture()
def app():
    with isolated_database():
        accounts.create_user("root@authz.invalid", _PASSWORD, admin=True)
        accounts.create_user("alice@authz.invalid", _PASSWORD, admin=False)
        accounts.create_user("bob@authz.invalid", _PASSWORD, admin=False)
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


def _login(client, email):
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


def _uid(email):
    return accounts.user_by_email(email)["id"]


# --- (a) non-admin vs admin mutations --------------------------------------


def test_non_admin_cannot_invite_retarget_or_delete_users(client):
    admin_id, alice_id = _uid("root@authz.invalid"), _uid("alice@authz.invalid")
    listed, _token = accounts.add_whitelist("invited@authz.invalid")
    invite_id = accounts.list_whitelist()[0]["id"]

    _login(client, "root@authz.invalid")
    assert accounts.get_user(alice_id)["is_admin"] == 0
    # Positive controls: the admin page opens and the invite route works.
    assert client.get("/admin/users").status_code == 200
    promoted = client.post(
        f"/admin/users/{alice_id}/role", data={"admin": "1"}, follow_redirects=False
    )
    assert promoted.status_code == 303
    assert accounts.get_user(alice_id)["is_admin"] == 1

    _login(client, "bob@authz.invalid")
    page = client.get("/admin/users", follow_redirects=False)
    assert page.status_code == _ADMIN_BLOCKED_FORM
    invite = client.post("/admin/users", data={"email": "evil@authz.invalid"}, follow_redirects=False)
    assert invite.status_code == _ADMIN_BLOCKED_FORM
    demote = client.post(f"/admin/users/{alice_id}/role", data={"admin": "0"}, follow_redirects=False)
    assert demote.status_code == _ADMIN_BLOCKED_FORM
    delete = client.post(f"/admin/users/{alice_id}/delete", follow_redirects=False)
    assert delete.status_code == _ADMIN_BLOCKED_FORM
    invite_delete = client.post(
        f"/admin/users/invites/{invite_id}/delete", follow_redirects=False
    )
    assert invite_delete.status_code == _ADMIN_BLOCKED_FORM
    # Nothing changed: no invite was added or removed, no role flipped, no
    # account disappeared.
    assert [row["email"] for row in accounts.list_whitelist()] == [listed]
    assert accounts.get_user(alice_id)["is_admin"] == 1
    assert accounts.get_user(admin_id) is not None


def test_non_admin_cannot_mutate_pricing(client):
    _login(client, "root@authz.invalid")
    saved = client.post(
        "/admin/pricing",
        data={"provider": "grok", "model": "test-model", "input_rate": "1.5", "output_rate": "3"},
        follow_redirects=False,
    )
    assert saved.status_code == 303
    assert saved.headers["location"] == "/admin/pricing"

    _login(client, "bob@authz.invalid")
    for path, payload in (
        ("/admin/pricing", {"provider": "grok", "model": "evil-model", "input_rate": "9", "output_rate": "9"}),
        ("/admin/pricing/delete", {"provider": "grok", "model": "test-model"}),
        ("/admin/pricing/openrouter", {}),
    ):
        page = client.get("/admin/pricing", follow_redirects=False)
        assert page.status_code == _ADMIN_BLOCKED_FORM
        res = client.post(path, data=payload, follow_redirects=False)
        assert res.status_code == _ADMIN_BLOCKED_FORM, path
    rows = db.fetchall("SELECT provider, model FROM model_prices ORDER BY provider, model")
    assert rows == [{"provider": "grok", "model": "test-model"}]


def test_non_admin_cannot_read_or_triage_issues(client):
    report = issues.submit(summary="Something broke on /usage")
    assert report is not None

    _login(client, "bob@authz.invalid")
    pack = client.get("/admin/issues")
    assert pack.status_code == _ADMIN_BLOCKED_JSON
    assert pack.json() == {"error": "auth"}
    triage = client.post("/admin/issues/status", json={"id": report["id"], "status": "rejected"})
    assert triage.status_code == _ADMIN_BLOCKED_JSON
    status = db.fetchone("SELECT status FROM bug_reports WHERE id=?", (report["id"],))["status"]
    assert status == "new"

    # Control: an admin reads the pack and the mutation lands.
    _login(client, "root@authz.invalid")
    assert client.get("/admin/issues").status_code == 200
    rejected = client.post("/admin/issues/status", json={"id": report["id"], "status": "rejected"})
    assert rejected.status_code == 200
    assert rejected.json() == {"ok": True, "status": "rejected"}


# --- (b) cross-user isolation ----------------------------------------------


def test_hosts_are_private_to_their_owner(client):
    alice_id, bob_id = _uid("alice@authz.invalid"), _uid("bob@authz.invalid")
    _login(client, "alice@authz.invalid")
    vault.unlock(alice_id, _PASSWORD)
    created = client.post("/hosts", json={
        "alias": "alpha", "hostname": "a.example.com", "username": "ada",
        "auth_type": "key", "secret": "SECRET-KEY-MATERIAL", "passphrase": "PASSPHRASE-x",
    })
    assert created.status_code == 201, created.text
    host_id = created.json()["id"]
    # bob owns a host of his own with a different id, so a miss on alice's id
    # cannot be an id-encoding accident.
    own = hosts.add_host(bob_id, {
        "alias": "bravo", "hostname": "b.example.com", "username": "bob",
        "auth_type": "key", "secret": "k",
    })

    _login(client, "bob@authz.invalid")
    bundle = client.get("/hosts/api").json()
    assert [h["id"] for h in bundle["hosts"]] == [own["id"]]
    patched = client.patch(f"/hosts/{host_id}", json={"label": "pwned"})
    assert patched.status_code == 404
    deleted = client.delete(f"/hosts/{host_id}")
    assert deleted.status_code == 404
    probed = client.post(f"/hosts/{host_id}/probe")
    # Not his host: the same 404 as patch/delete (the probe used to flatten
    # this into a generic 400 "probe failed").
    assert probed.status_code == 404
    # Alice's host is untouched — not renamed, not deleted, and bob's failed
    # probe (which records an error against *his* copy of the id) never
    # wrote an error onto her row.
    row = db.fetchone("SELECT label, last_error FROM user_hosts WHERE id=?", (host_id,))
    assert row["label"] == "" and row["last_error"] == ""
    # His own host still patches fine: the 404s were authorization, not a
    # broken route.
    assert client.patch(f"/hosts/{own['id']}", json={"label": "mine"}).status_code == 200


def test_serialized_host_payloads_never_carry_the_secret_or_passphrase(client):
    alice_id = _uid("alice@authz.invalid")
    _login(client, "alice@authz.invalid")
    vault.unlock(alice_id, _PASSWORD)
    created = client.post("/hosts", json={
        "alias": "alpha", "hostname": "a.example.com", "username": "ada",
        "auth_type": "key", "secret": "SECRET-KEY-MATERIAL", "passphrase": "PASSPHRASE-x",
    })
    assert created.status_code == 201, created.text
    for leak in ("SECRET-KEY-MATERIAL", "PASSPHRASE-x"):
        assert leak not in created.text
    bundle = client.get("/hosts/api")
    assert bundle.status_code == 200
    assert "SECRET-KEY-MATERIAL" not in bundle.text and "PASSPHRASE-x" not in bundle.text
    for host in bundle.json()["hosts"]:
        assert "secret" not in host and "passphrase" not in host
    page = client.get("/hosts")
    assert page.status_code == 200
    assert "SECRET-KEY-MATERIAL" not in page.text and "PASSPHRASE-x" not in page.text


def test_artifacts_are_private_to_their_owner(client):
    alice_id = _uid("alice@authz.invalid")
    _login(client, "alice@authz.invalid")
    private = artifacts.create(alice_id, title="Private", html="<p>secret sauce</p>")
    public = artifacts.create(alice_id, title="Public", html="<p>shared</p>", visibility="public")

    _login(client, "bob@authz.invalid")
    ident = private["identifier"]
    blocked = [
        ("get", f"/a/{ident}", 404),                 # page
        ("get", f"/a/{ident}/content", 404),
        ("get", f"/a/{ident}/download", 404),
        ("get", f"/a/{ident}/meta", 404),
        ("post", f"/a/{ident}/visibility", 404),     # json={"visibility": "public"}
        ("post", f"/a/{ident}/title", 404),          # json={"title": "pwned"}
        ("post", f"/a/{ident}/archive", 404),
        ("post", f"/a/{ident}/continue", 404),
    ]
    for method, path, expected in blocked:
        json = (
            {"visibility": "public"} if path.endswith("/visibility")
            else {"title": "pwned"} if path.endswith("/title")
            else {}
        )
        res = client.request(method.upper(), path, json=json)
        assert res.status_code == expected, (method, path, res.status_code)
    # A public artifact is intentionally viewable by others, but its
    # owner-only operations stay owner-only.
    assert client.get(f"/a/{public['identifier']}").status_code == 200
    assert client.get(f"/a/{public['identifier']}/content").status_code == 200
    assert client.get(f"/a/{public['identifier']}/download").status_code == 404
    locked = client.post(f"/a/{public['identifier']}/visibility", json={"visibility": "private"})
    assert locked.status_code == 404

    # Nothing bob did reached alice's rows.
    _login(client, "alice@authz.invalid")
    meta = client.get(f"/a/{ident}/meta").json()
    assert meta["visibility"] == "private" and meta["title"] == "Private" and meta["owner"] is True
    assert client.get(f"/a/{public['identifier']}/meta").json()["visibility"] == "public"


def test_threads_and_runs_are_private_to_their_owner(client):
    alice_id = _uid("alice@authz.invalid")
    _login(client, "alice@authz.invalid")
    created = client.post("/chat/threads", json={
        "model": "offline", "command_id": "authz-1", "title": "Alice thread",
    })
    assert created.status_code == 201, created.text
    thread = created.json()
    pk = chats.resolve_thread_pk(alice_id, thread["id"])
    run = chat_store.start_turn(alice_id, pk, "hello", thread["revision"], "authz-run")["run"]

    _login(client, "bob@authz.invalid")
    attempts = [
        ("GET", f"/chat/threads/{thread['id']}", None, 404),
        ("PATCH", f"/chat/threads/{thread['id']}", {"title": "pwned", "expected_revision": 1, "command_id": "x"}, 404),
        ("DELETE", f"/chat/threads/{thread['id']}", {"expected_revision": 1}, 404),
        ("GET", f"/chat/runs/{run['id']}", None, 404),
        ("POST", f"/chat/runs/{run['id']}/cancel", None, 404),
        ("POST", f"/chat/runs/{run['id']}/resume", None, 404),
    ]
    for method, path, body, expected in attempts:
        res = client.request(method, path, json=body)
        assert res.status_code == expected, (method, path, res.status_code)
    # Alice's thread and run are intact and still hers.
    _login(client, "alice@authz.invalid")
    snapshot = client.get(f"/chat/threads/{thread['id']}")
    assert snapshot.status_code == 200
    assert snapshot.json()["title"] == "Alice thread"
    assert client.get(f"/chat/runs/{run['id']}").status_code == 200


def test_cannot_remove_another_users_push_subscription(client):
    alice_id = _uid("alice@authz.invalid")
    endpoint = "https://updates.push.services.mozilla.com/push/v1/alice-device"
    _login(client, "alice@authz.invalid")
    subscribed = client.post("/notifications/subscribe", json={
        "endpoint": endpoint, "keys": {"p256dh": "p256dh-value", "auth": "auth-value"},
    })
    assert subscribed.status_code == 200, subscribed.text
    assert push.count_for(alice_id) == 1

    # Bob POSTs alice's endpoint to /notifications/unsubscribe: the route
    # answers ok but is scoped to his account, so it removes nothing.
    _login(client, "bob@authz.invalid")
    gone = client.post("/notifications/unsubscribe", json={"endpoint": endpoint})
    assert gone.status_code == 200
    assert gone.json() == {"ok": True, "count": 0}
    assert push.count_for(alice_id) == 1


def test_cannot_revoke_another_users_api_key(client):
    _login(client, "alice@authz.invalid")
    bob_id = _uid("bob@authz.invalid")
    raw = accounts.create_api_key(bob_id, "Laptop")
    key_id = accounts.keys_for(bob_id)[-1]["id"]

    deleted = client.post(f"/keys/{key_id}/delete", follow_redirects=False)
    assert deleted.status_code == 303
    assert deleted.headers["location"] == "/keys"
    # The delete is scoped to the caller's own keys: bob's key still works.
    assert accounts.user_by_api_key(raw)["id"] == bob_id
