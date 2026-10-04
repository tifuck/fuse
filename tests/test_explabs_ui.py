"""Synthetic personal-key UI/discovery contracts; no database or upstream calls."""
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from jinja2 import Environment, FileSystemLoader
from starlette.middleware.sessions import SessionMiddleware

from app import agents, opencode
from app.web import models, sources, router as web_router


@pytest.fixture(autouse=True)
def no_io(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Unexpected database/upstream access")
    for name in ("fetchone", "fetchall", "execute", "connect", "tx"):
        monkeypatch.setattr(sources.db, name, forbidden)
    monkeypatch.setattr(sources.explabs_mod, "fetch_catalog", AsyncMock(side_effect=forbidden))
    sources._pool_flash.clear()
    yield
    sources._pool_flash.clear()


@pytest.fixture
def owner(monkeypatch):
    user = {"id": 7, "is_admin": False}
    monkeypatch.setattr(sources, "require_user", lambda request: (user, None))
    monkeypatch.setattr(sources.explabs_mod, "ensure_explabs_source", lambda: 42)
    monkeypatch.setattr(sources, "invalidate_model_cache", Mock())
    return user


def test_add_replace_and_rate_limit(monkeypatch, owner):
    add = AsyncMock(return_value=(11, ""))
    monkeypatch.setattr(sources.explabs_mod, "add_user_key", add)
    allow = Mock(return_value=True)
    monkeypatch.setattr(sources.limiter, "allow", allow)
    response = asyncio.run(sources.explabs_add(None, "synthetic-key", "My key"))
    assert response.status_code == 303
    add.assert_awaited_once_with(42, "synthetic-key", 7, label="My key")
    allow.assert_called_once_with("explabs-add:7", 5, 60)
    sources.invalidate_model_cache.assert_called_once_with("explabs")
    allow.return_value = False
    asyncio.run(sources.explabs_add(None, "another-key", ""))
    assert add.await_count == 1
    assert sources._pool_flash[7][0] == "err"


def test_validation_error_is_not_saved(monkeypatch, owner):
    monkeypatch.setattr(sources.limiter, "allow", lambda *args: True)
    monkeypatch.setattr(sources.explabs_mod, "add_user_key", AsyncMock(return_value=(None, "Invalid key")))
    asyncio.run(sources.explabs_add(None, "bad", ""))
    assert sources._pool_flash[7] == ("err", "Invalid key", "explabs")
    sources.invalidate_model_cache.assert_not_called()


@pytest.mark.parametrize("row,status", [(None, 404), ({"source_id": 99, "donated_by": 7}, 404), ({"source_id": 42, "donated_by": 8}, 403)])
def test_revoke_checks_source_and_owner(monkeypatch, owner, row, status):
    monkeypatch.setattr(sources.db, "fetchone", lambda *args: row)
    disable = Mock()
    monkeypatch.setattr(sources.explabs_mod, "disable_credential", disable)
    owner["is_admin"] = True
    with pytest.raises(HTTPException) as error:
        asyncio.run(sources.explabs_revoke(None, 11))
    assert error.value.status_code == status
    disable.assert_not_called()


def test_revoke_own_key(monkeypatch, owner):
    monkeypatch.setattr(sources.db, "fetchone", lambda *args: {"source_id": 42, "donated_by": 7})
    disable = Mock()
    monkeypatch.setattr(sources.explabs_mod, "disable_credential", disable)
    assert asyncio.run(sources.explabs_revoke(None, 11)).status_code == 303
    disable.assert_called_once_with(11)
    sources.invalidate_model_cache.assert_called_once_with("explabs")


def test_routes_require_csrf(monkeypatch, owner):
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="synthetic-session-secret")
    app.include_router(web_router)
    with TestClient(app) as client:
        for path in ("/sources/explabs/add", "/sources/explabs/11/revoke", "/admin/sources/explabs"):
            assert client.post(path, data={"api_key": "synthetic-key"}).status_code == 403


def test_admin_toggle(monkeypatch):
    monkeypatch.setattr(sources, "require_admin", lambda request: ({"id": 1}, None))
    monkeypatch.setattr(sources.explabs_mod, "ensure_explabs_source", lambda: 42)
    execute = Mock()
    monkeypatch.setattr(sources.db, "execute", execute)
    monkeypatch.setattr(sources, "invalidate_model_cache", Mock())
    assert asyncio.run(sources.admin_explabs_save(None, "1")).status_code == 303
    execute.assert_called_once_with("UPDATE sources SET enabled=? WHERE id=?", (1, 42))
    sources.invalidate_model_cache.assert_called_once_with("explabs")


def test_scoped_model_metadata_and_efforts(monkeypatch):
    catalog = {"by_id": {"vendor/model": {"name": "Friendly model", "context_length": 262144, "reasoning_efforts": ["low", "high"]}}}
    monkeypatch.setattr(models.explabs_mod, "cached_catalog", lambda uid: catalog if uid == 7 else None)
    rows = models._model_rows("explabs", ["explabs/vendor/model"], catalog)
    assert rows == [{"id": "explabs/vendor/model", "display_id": "vendor/model", "name": "Friendly model", "context_window": 262144}]
    assert models._family_title({"type": "explabs"}) == "Experiential Labs"
    assert models._reasoning_efforts("explabs", "explabs/vendor/model", 7) == ("low", "high")
    assert models._reasoning_efforts("explabs", "explabs/vendor/model", 8) == ()
    assert models._reasoning_default("explabs", "explabs/vendor/model", 7) == "high"


def test_models_page_passes_owner_catalog(monkeypatch):
    denied = {"ok": False, "reason": "none"}
    monkeypatch.setattr(models, "grok_access_state", lambda uid: denied)
    for module, name in ((models.chatgpt_mod, "chatgpt_access_state"), (models.zai_mod, "zai_access_state"), (models.devin_mod, "devin_access_state"), (models.kimi_mod, "kimi_access_state"), (models.mimo_mod, "mimo_access_state"), (models.claude_mod, "claude_access_state")):
        monkeypatch.setattr(module, name, lambda uid: denied)
    monkeypatch.setattr(models.openrouter_mod, "openrouter_access_state", lambda uid: {"ok": False, "user_key": False})
    monkeypatch.setattr(models.openrouter_mod, "cached_catalog", lambda: None)
    catalog = {"by_id": {"model": {"name": "Owner model", "context_length": 262144}}}
    cache = Mock(return_value=catalog)
    monkeypatch.setattr(models.explabs_mod, "cached_catalog", cache)
    groups = AsyncMock(return_value=[{"type": "explabs", "models": ["explabs/model"]}])
    monkeypatch.setattr(models, "models_by_source", groups)
    monkeypatch.setattr(models.db, "fetchone", lambda *args: None)
    available, locked = asyncio.run(models._models_page_families({"id": 7}))
    cache.assert_called_once_with(7)
    assert groups.await_args.kwargs["user_id"] == 7
    assert available[0]["models"][0]["name"] == "Owner model"
    assert available[0]["models"][0]["context_window"] == 262144
    assert not locked


def test_opencode_uses_only_owners_catalog(monkeypatch):
    catalog = {"by_id": {"vendor/model": {"name": "Friendly model", "context_length": 262144, "image": True, "supported_parameters": ["reasoning_effort"], "reasoning_efforts": ["high"]}}}
    monkeypatch.setattr(opencode.explabs_mod, "user_has_explabs", lambda uid: uid == 7)
    cache = Mock(return_value=catalog)
    monkeypatch.setattr(opencode.explabs_mod, "cached_catalog", cache)
    monkeypatch.setattr(opencode.model_caps, "image_input", lambda *args, **kwargs: None)
    groups = [{"type": "explabs", "models": ["explabs/vendor/model", "model", "explabs/other"]}]
    entries = asyncio.run(opencode.model_options(7, groups))
    assert len(entries) == 1
    assert entries[0]["id"] == "explabs/vendor/model"
    assert entries[0]["name"] == "Experiential Labs Friendly model"
    assert entries[0]["config"]["limit"]["context"] == 262144
    assert entries[0]["config"]["variants"] == {"high": {"reasoningEffort": "high"}}
    assert entries[0]["config"]["modalities"]["input"] == ["text", "image"]
    cache.assert_called_with(7)
    assert asyncio.run(opencode.model_options(8, groups)) == []


def test_agent_aliases_and_explicit_selection(monkeypatch):
    entries = [{"id": "explabs/vendor/glm-5.3", "name": "GLM 5.3", "type": "explabs"}]
    monkeypatch.setattr(agents, "_catalog_entries", AsyncMock(return_value=entries))
    for query in ("explabs", "experiential labs", "experiential labs glm-5.3"):
        assert agents._match_entries(entries, query)[0]["provider"] == "explabs"
    assert agents._item_providers(entries[0]) == {"explabs"}
    assert agents._match_entries(entries, "zai") == []
    model, error = asyncio.run(agents.resolve_spawn_model(user_id=7, slug="glm-5.3", parent_model=""))
    assert not model and error
    model, error = asyncio.run(agents.resolve_spawn_model(user_id=7, slug="experiential labs glm-5.3", parent_model=""))
    assert model == entries[0]["id"] and not error


def test_templates_personal_key_forms():
    root = Path(__file__).resolve().parents[1] / "templates"
    env = Environment(loader=FileSystemLoader(root), autoescape=True)
    for filename in ("sources.html", "admin_sources.html"):
        env.get_template(filename)
    text = (root / "sources.html").read_text()
    panel = text.split('<div data-tab-panel="explabs"', 1)[1].split('<dialog', 1)[0]
    html = env.from_string('<div' + panel).render(csrf_token="synthetic-csrf", explabs_account={"id": 11, "label": "<My key>"}, explabs_access={"ok": True, "model_count": 3}, explabs_flash_msg="")
    assert 'action="/sources/explabs/add"' in html
    assert 'action="/sources/explabs/11/revoke"' in html
    assert html.count('value="synthetic-csrf"') == 2
    assert 'type="password"' in html
    assert "&lt;My key&gt;" in html
    assert "subscription pools never fall back" in html
    assert "free-models" not in html and "paid-models" not in html
