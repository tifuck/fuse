"""ChatGPT OAuth uses OPENAI_AUTH_PROXY; chat/usage and other providers do not."""

from __future__ import annotations

import asyncio
import inspect
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
_TMP = tempfile.mkdtemp(prefix="fuse-cg-auth-")
os.environ.setdefault("DATABASE_PATH", str(Path(_TMP) / "chat.db"))
os.environ.setdefault("APP_SECRET", "x" * 32)

from app.sources import chatgpt as chatgpt_mod  # noqa: E402
from app.sources import grok as grok_mod  # noqa: E402
from app.sources import devin as devin_mod  # noqa: E402
from app.sources import zai as zai_mod  # noqa: E402
from app.sources import openrouter as openrouter_mod  # noqa: E402

_SOCKS = "socks5h://socksproxy:secret@127.0.0.1:1080"
_SOURCES = Path(__file__).resolve().parents[1] / "app" / "sources"


def _capture_client(module, fn, *args, **kwargs):
    captured: list[dict] = []
    real = httpx.AsyncClient

    def fake(*a, **k):
        captured.append(k)
        return real(*a, **k)

    with patch(f"{module}.httpx.AsyncClient", side_effect=fake):
        client = fn(*args, **kwargs)
    return captured, client


class _JsonResp:
    def __init__(self, status=200, payload=None):
        self.status_code = status
        self._payload = payload if payload is not None else {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("err", request=None, response=None)

    def json(self):
        return self._payload


class _FakeAuthClient:
    def __init__(self, resp: _JsonResp):
        self.resp = resp
        self.posts: list[tuple[str, dict]] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    async def post(self, url, **kwargs):
        self.posts.append((url, kwargs))
        return self.resp


class AuthClientProxyTest(unittest.TestCase):
    def test_auth_client_passes_socks_proxy(self):
        with patch.object(chatgpt_mod.settings, "openai_auth_proxy", _SOCKS):
            captured, client = _capture_client("app.sources.chatgpt", chatgpt_mod._auth_client, timeout=20)
        asyncio.run(client.aclose())
        self.assertEqual(captured[0].get("proxy"), _SOCKS)
        self.assertEqual(captured[0].get("timeout"), 20)

    def test_auth_client_omits_proxy_when_unset(self):
        with patch.object(chatgpt_mod.settings, "openai_auth_proxy", ""):
            captured, client = _capture_client("app.sources.chatgpt", chatgpt_mod._auth_client)
        asyncio.run(client.aclose())
        self.assertNotIn("proxy", captured[0])

    def test_auth_client_strips_whitespace(self):
        with patch.object(chatgpt_mod.settings, "openai_auth_proxy", "  \n"):
            captured, client = _capture_client("app.sources.chatgpt", chatgpt_mod._auth_client)
        asyncio.run(client.aclose())
        self.assertNotIn("proxy", captured[0])

    def test_chat_client_never_uses_proxy(self):
        with patch.object(chatgpt_mod.settings, "openai_auth_proxy", _SOCKS):
            captured, client = _capture_client("app.sources.chatgpt", chatgpt_mod._client)
        asyncio.run(client.aclose())
        self.assertNotIn("proxy", captured[0])


class OauthFlowUsesAuthClientTest(unittest.TestCase):
    def test_start_device_uses_auth_client(self):
        fake = _FakeAuthClient(_JsonResp(200, {"device_auth_id": "d1", "user_code": "AB"}))
        with patch.object(chatgpt_mod, "_auth_client", return_value=fake):
            data = asyncio.run(chatgpt_mod.start_device())
        self.assertEqual(data["user_code"], "AB")
        self.assertTrue(fake.posts[0][0].endswith("/api/accounts/deviceauth/usercode"))

    def test_poll_device_uses_auth_client(self):
        fake = _FakeAuthClient(_JsonResp(404, {}))
        with patch.object(chatgpt_mod, "_auth_client", return_value=fake):
            data = asyncio.run(chatgpt_mod.poll_device("d1", "AB"))
        self.assertEqual(data, {"status": "pending"})
        self.assertTrue(fake.posts[0][0].endswith("/api/accounts/deviceauth/token"))

    def test_exchange_code_uses_auth_client(self):
        fake = _FakeAuthClient(_JsonResp(200, {"access_token": "tok"}))
        with patch.object(chatgpt_mod, "_auth_client", return_value=fake):
            data = asyncio.run(chatgpt_mod.exchange_code("code", "ver"))
        self.assertEqual(data["access_token"], "tok")
        self.assertTrue(fake.posts[0][0].endswith("/oauth/token"))

    def test_refresh_posts_via_auth_client(self):
        fake = _FakeAuthClient(_JsonResp(401, {"error": "invalid_grant"}))
        cred = {"id": 1, "disabled": 0}
        extra = {"refresh_token": "rt"}
        with (
            patch.object(chatgpt_mod, "_auth_client", return_value=fake),
            patch.object(chatgpt_mod.db, "fetchone", return_value=cred),
            patch.object(chatgpt_mod.accounts, "cred_extra", return_value=extra),
            patch.object(chatgpt_mod, "disable_credential"),
            patch.object(chatgpt_mod, "note_failure"),
        ):
            ok = asyncio.run(chatgpt_mod._refresh_one_unlocked(cred))
        self.assertFalse(ok)
        self.assertTrue(fake.posts[0][0].endswith("/oauth/token"))
        self.assertEqual(fake.posts[0][1]["data"]["grant_type"], "refresh_token")


class OtherProvidersUnproxiedTest(unittest.TestCase):
    def test_only_chatgpt_auth_client_sets_proxy(self):
        for name in (
            "grok.py",
            "devin.py",
            "zai.py",
            "openrouter.py",
            "openai_compat.py",
            "ollama.py",
            "llama.py",
        ):
            text = (_SOURCES / name).read_text()
            self.assertNotIn("proxy=", text, msg=f"{name} must not set an httpx proxy")
            self.assertNotIn("openai_auth_proxy", text)

    def test_chatgpt_chat_and_usage_stay_direct(self):
        source = inspect.getsource(chatgpt_mod.refresh_usage)
        self.assertIn("httpx.AsyncClient(timeout=20)", source)
        self.assertNotIn("_auth_client", source)
        chat_source = inspect.getsource(chatgpt_mod._client)
        self.assertNotIn("proxy", chat_source)

    def test_other_provider_oauth_helpers_exist_unproxied(self):
        self.assertTrue(callable(grok_mod.start_device))
        self.assertTrue(callable(devin_mod.start_cli_login))
        self.assertTrue(callable(zai_mod.ensure_zai_source))
        self.assertTrue(callable(openrouter_mod.ensure_openrouter_source))


if __name__ == "__main__":
    unittest.main()
