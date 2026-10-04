"""Authenticated preview transport, with no host or provider access."""
import json

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from app import chat_store, chats
from app.web import chat
from tests.test_read_file_images_runtime import job
from app.chat_runtime import _tool_messages


def test_authenticated_preview_route_and_account_boundary(monkeypatch):
    rows = _tool_messages([job()])
    queries = []
    def fetch(sql, args):
        queries.append((sql, args))
        return {"provider_messages": json.dumps(rows)} if args == ("run-test", 1) else None
    monkeypatch.setattr(chat_store.db, "fetchone", fetch)
    monkeypatch.setattr(chat, "_chat_auth", lambda request: (
        ({"id": int(request.headers["x-test-user"])}, None) if "x-test-user" in request.headers
        else (None, JSONResponse({"error": "unauthorized"}, status_code=401))))
    app = FastAPI()
    app.include_router(chat.router)
    with TestClient(app) as client:
        url = "/chat/runs/run-test/images/event-0"
        assert client.get(url).status_code == 401
        assert not queries
        response = client.get(url, headers={"x-test-user": "1"})
        assert response.status_code == 200
        assert response.content == b"\x89PNG\r\n\x1a\n"
        assert response.headers["content-type"] == "image/png"
        assert response.headers["cache-control"] == "private, no-store"
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["cross-origin-resource-policy"] == "same-origin"
        assert client.get(url, headers={"x-test-user": "2"}).status_code == 404
        assert client.get(url + "-missing", headers={"x-test-user": "1"}).status_code == 404
    assert all("user_id=?" in sql for sql, _ in queries)


def test_preview_metadata_rejects_external_urls():
    for url in ("https://evil.test/a", "//evil.test/a", "/chat/runs/a/images/b?redirect=evil"):
        assert "preview_url" not in chats.tool_image_metadata({"preview_url": url})


def test_preview_rejects_bad_or_compacted_pixels(monkeypatch):
    rows = _tool_messages([job()])
    monkeypatch.setattr(chat_store.db, "fetchone", lambda *args: {"provider_messages": json.dumps(rows)})
    rows[-1]["content"][1]["image_url"]["url"] = "data:image/svg+xml;base64,PHN2Zz4="
    assert chat_store.read_file_image(1, "run", "event-0") is None
    rows[-1]["content"][1]["image_url"]["url"] = "data:image/png;base64,!!!"
    assert chat_store.read_file_image(1, "run", "event-0") is None
    rows.clear()
    assert chat_store.read_file_image(1, "run", "event-0") is None
