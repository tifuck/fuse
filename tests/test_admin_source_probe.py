import asyncio
import json
from unittest.mock import AsyncMock, Mock

import pytest
from starlette.requests import Request

from app.web.source_routes import admin_actions


@pytest.mark.parametrize("user,status", [(None, 401), ({"id": 1, "is_admin": False}, 401), ({"id": 1, "is_admin": True}, 200)])
def test_source_probe_auth_and_dispatch(monkeypatch, user, status):
    monkeypatch.setattr(admin_actions, "current_user", lambda request: user)
    probe = AsyncMock(return_value={"ok": True, "checks": []})
    lookup = Mock(return_value={"id": 7})
    monkeypatch.setattr(admin_actions, "probe_source", probe)
    monkeypatch.setattr(admin_actions.db, "fetchone", lookup)
    monkeypatch.setattr(admin_actions.limiter, "allow", lambda *args: True)
    response = asyncio.run(admin_actions.admin_source_test(Request({"type": "http"}), 7))
    assert response.status_code == status
    if status == 200:
        probe.assert_awaited_once_with(7)
        assert json.loads(response.body) == {"ok": True, "checks": []}
        assert response.headers["cache-control"] == "no-store"
    else:
        probe.assert_not_awaited()
        lookup.assert_not_called()
