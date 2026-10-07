"""The /v1 API-key boundary: only a live key of the requesting user
authenticates, on both OpenAI-compatible endpoints.

Pins app/api.require_key: a missing or malformed Authorization header is a
401 before any body parsing, a revoked (deleted-row) or owner-deleted key
stops authenticating immediately, and a valid key maps to exactly one user —
another account's traffic can neither borrow the key owner's identity nor
exhaust their per-user API budget (the `api-user:{id}` limiter bucket).
"""
import os

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import accounts, api
from app.config import settings
from app.security import limiter
from tests.mariadb_fixture import isolated_database

pytestmark = pytest.mark.skipif(
    os.environ.get("FUSE_CHAT_TEST_MODE") != "1", reason="requires disposable MariaDB fixture"
)

_PASSWORD = "password12"


@pytest.fixture()
def client():
    with isolated_database():
        accounts.create_user("owner@keys.invalid", _PASSWORD, admin=True)
        app = FastAPI()
        app.include_router(api.router)
        with TestClient(app, base_url="https://testserver") as http:
            limiter._hits.clear()
            yield http


def _user(email, *, admin=False):
    created = accounts.create_user(email, _PASSWORD, admin=admin)
    return created["id"], created["new_key"]


def _get(client, raw_key):
    return client.get("/v1/models", headers={"Authorization": f"Bearer {raw_key}"})


def test_missing_or_malformed_bearer_keys_are_rejected_on_both_endpoints(client):
    _, key = _user("boundary@keys.invalid")
    for headers in (None, {"Authorization": f"Basic {key}"}, {"Authorization": "Bearer not-a-key"}):
        models = client.get("/v1/models", headers=headers)
        assert models.status_code == 401, models.text
        # Auth runs before body parsing: an unauthenticated POST never reaches
        # the JSON validator, so even an empty body gets the key error.
        chat = client.post("/v1/chat/completions", headers=headers)
        assert chat.status_code == 401, chat.text
    missing = client.get("/v1/models")
    assert missing.json()["detail"] == "Missing API key"
    wrong_scheme = client.get("/v1/models", headers={"Authorization": f"Basic {key}"})
    assert wrong_scheme.json()["detail"] == "Missing API key"
    garbage = client.get("/v1/models", headers={"Authorization": "Bearer not-a-key"})
    assert garbage.json()["detail"] == "Invalid API key"
    # The same key under the required scheme is accepted.
    assert _get(client, key).json()["object"] == "list"


def test_revoked_and_deleted_keys_stop_authenticating(client):
    admin = accounts.user_by_email("owner@keys.invalid")
    uid, revoked = _user("rotate@keys.invalid")
    assert _get(client, revoked).status_code == 200

    # Revoking one key row leaves the account's other keys working.
    key_id = accounts.keys_for(uid)[0]["id"]
    assert accounts.delete_api_key(uid, key_id) is True
    gone = _get(client, revoked)
    assert gone.status_code == 401
    assert gone.json()["detail"] == "Invalid API key"
    survivor = accounts.create_api_key(uid, "second")
    assert _get(client, survivor).status_code == 200

    # Deleting the owning user removes their keys with them; nobody else is
    # affected by either revocation.
    bystander_id, bystander = _user("bystander@keys.invalid")
    accounts.delete_user(uid, actor_id=admin["id"])
    assert _get(client, survivor).status_code == 401
    assert _get(client, bystander).status_code == 200


def test_a_key_maps_only_to_its_own_user(client):
    """A key resolves to one account: exhausting its owner's per-user API
    budget neither blocks nor is absorbed by any other account."""
    _, alice = _user("alice@keys.invalid")
    _, bob = _user("bob@keys.invalid")
    assert _get(client, alice).status_code == 200

    cap = settings.api_rate_per_minute
    for _ in range(cap):
        assert _get(client, bob).status_code == 200
    # Bob's own bucket is exhausted…
    assert _get(client, bob).status_code == 429
    # …while Alice's key, on the same IP, still authenticates and serves.
    assert _get(client, alice).status_code == 200
