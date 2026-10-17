"""Cursor pool: cursor-agent login, agent protocol turn, routing, refresh."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import struct
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, urlparse

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
_TMP = tempfile.mkdtemp(prefix="fuse-cursor-")
os.environ.setdefault("DATABASE_PATH", str(Path(_TMP) / "cursor.db"))
os.environ.setdefault("APP_SECRET", "x" * 32)

from app import accounts, db  # noqa: E402
from app.crypto import encrypt  # noqa: E402
from app.sources import cursor as cursor_mod  # noqa: E402
from app.sources import cursor_agent as agent  # noqa: E402
from app.sources import pick  # noqa: E402
from app.sources.devin_proto import ProtoDecoder  # noqa: E402
from app.sources.pool_pick import reset_health  # noqa: E402

import pytest

# Uses the database: a disposable MariaDB schema shared by this module.
pytestmark = pytest.mark.usefixtures("isolated_db")

_RealClient = httpx.AsyncClient


def _jwt(sub: str = "auth0|user_1", exp: int | None = None) -> str:
    def part(obj):
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")

    return f"{part({'alg': 'HS256'})}.{part({'sub': sub, 'exp': exp or int(time.time()) + 86400 * 60})}.sig"


def _frame(obj: dict, flag: int = 0) -> bytes:
    body = json.dumps(obj).encode()
    return bytes([flag]) + struct.pack(">I", len(body)) + body


def _value_string(text: str) -> str:
    raw = text.encode()
    return base64.b64encode(b"\x1a" + bytes([len(raw)]) + raw).decode()


def _top_field(payload: bytes) -> int:
    return ProtoDecoder(payload).tag()[0]


class CursorUnitTest(unittest.TestCase):
    def test_login_is_cursor_agent_deep_link_with_s256_challenge(self):
        data = cursor_mod.start_login()
        url = urlparse(data["auth_url"])
        self.assertEqual(f"{url.scheme}://{url.netloc}{url.path}", "https://cursor.com/loginDeepControl")
        query = {k: v[0] for k, v in parse_qs(url.query).items()}
        self.assertEqual(query["uuid"], data["uuid"])
        self.assertEqual(query["mode"], "login")
        self.assertEqual(query["redirectTarget"], "cli")
        want = base64.urlsafe_b64encode(hashlib.sha256(data["verifier"].encode()).digest()).decode().rstrip("=")
        self.assertEqual(query["challenge"], want)
        self.assertNotIn(data["verifier"], data["auth_url"])

    def test_build_turn_history_and_continuation(self):
        msgs = [
            {"role": "system", "content": "be terse"},
            {"role": "user", "content": "weather?"},
            {"role": "assistant", "content": "", "tool_calls": [
                {"id": "c1", "type": "function", "function": {"name": "get_weather", "arguments": '{"city":"Osaka"}'}},
            ]},
            {"role": "tool", "tool_call_id": "c1", "content": "sunny"},
        ]
        history, text = agent.build_turn(msgs)
        self.assertEqual(text, agent.CONTINUE_TEXT)
        self.assertIn("be terse", history[0]["content"][0]["text"])
        self.assertEqual(history[1]["content"][0]["text"], agent.SYSTEM_ACK)
        self.assertEqual(history[3]["content"][0], {"type": "tool-call", "toolCallId": "c1", "toolName": "get_weather", "args": {"city": "Osaka"}})
        self.assertEqual(history[4]["content"][0]["toolName"], "get_weather")
        self.assertEqual(history[4]["content"][0]["result"], "sunny")
        history, text = agent.build_turn(msgs[:2])
        self.assertEqual(text, "weather?")
        self.assertEqual(len(history), 2)

    def test_decode_arg_values(self):
        self.assertEqual(agent.decode_arg(_value_string("Osaka")), "Osaka")
        number = base64.b64encode(b"\x11" + struct.pack("<d", 5.0)).decode()
        self.assertEqual(agent.decode_arg(number), 5)

    def test_parse_usage_payload(self):
        parsed = cursor_mod.parse_usage_payload(
            {
                "billingCycleStart": "1768399334000",
                "billingCycleEnd": "1771077734000",
                "planUsage": {"totalPercentUsed": 15.48, "autoPercentUsed": 0, "apiPercentUsed": 46.4},
                "spendLimitUsage": {"individualUsed": 250, "individualLimit": 10000},
            },
            {"planInfo": {"planName": "Ultra"}},
        )
        self.assertAlmostEqual(parsed["credit_usage_percent"], 15.48)
        self.assertEqual(parsed["plan_type"], "ultra")
        self.assertEqual([p["product"] for p in parsed["products"]], ["Auto + Composer", "API"])
        self.assertEqual(parsed["on_demand_cap_cents"], 10000)
        self.assertTrue(parsed["period_end"].startswith("2026-02-14"))

    def test_cursor_ids(self):
        self.assertTrue(cursor_mod.is_cursor_model("cursor/claude-opus-5-thinking-high"))
        self.assertFalse(cursor_mod.is_cursor_model("claude/claude-opus-5-5"))
        self.assertEqual(cursor_mod.upstream_id("cursor/auto"), "auto")


class CursorTurnTest(unittest.IsolatedAsyncioTestCase):
    """A scripted api2.cursor.sh: RunSSE frames that wait on BidiAppend calls."""

    async def test_turn_fetches_blobs_answers_context_and_ends_on_tool_call(self):
        appends: asyncio.Queue = asyncio.Queue()
        seen: dict = {"seq": []}
        history_entry = {"role": "user", "content": [{"type": "text", "text": f"{agent.SYSTEM_LEAD}\n\nsys"}]}
        blob = json.dumps(history_entry, ensure_ascii=False, separators=(",", ":")).encode()
        blob_id = base64.b64encode(hashlib.sha256(blob).digest()).decode()

        async def server():
            self.assertEqual(_top_field(await appends.get()), 1)  # run_request
            yield _frame({"kvServerMessage": {"id": 4, "getBlobArgs": {"blobId": blob_id}}})
            got = await appends.get()
            self.assertEqual(_top_field(got), 3)
            self.assertIn(blob, got)
            yield _frame({"execServerMessage": {"id": 1, "execId": "x1", "requestContextArgs": {}}})
            got = await appends.get()
            self.assertEqual(_top_field(got), 2)
            self.assertIn(b"get_weather", got)
            yield _frame({"interactionUpdate": {"thinkingDelta": {"text": "hmm"}}})
            yield _frame({"interactionUpdate": {"textDelta": {"text": "Checking"}}})
            yield _frame({"interactionUpdate": {"tokenDelta": {"tokens": 7}}})
            yield _frame({"conversationCheckpointUpdate": {"tokenDetails": {"usedTokens": 107}}})
            yield _frame({"execServerMessage": {"id": 2, "mcpArgs": {
                "name": "mcp_fuse_get_weather", "toolName": "get_weather",
                "toolCallId": "call-1\nfc_2", "args": {"city": _value_string("Osaka")},
            }}})
            await asyncio.sleep(5)

        def handler(request: httpx.Request):
            if request.url.path.endswith("/RunSSE"):
                self.assertEqual(request.headers["content-type"], "application/connect+json")
                self.assertEqual(request.headers["x-cursor-agent-allowed-tools"], "mcp_tool_call")
                return httpx.Response(200, content=server())
            body = json.loads(request.content)
            seen["seq"].append(body["appendSeqno"])
            appends.put_nowait(base64.b64decode(body["dataBinary"]))
            return httpx.Response(200, json={})

        def client(**kw):
            return _RealClient(transport=httpx.MockTransport(handler), **kw)

        events = []
        with patch.object(agent.httpx, "AsyncClient", client), patch.object(agent, "TOOL_GRACE_S", 0.05):
            async for event in agent.run_turn(
                base="https://api2.cursor.sh",
                headers=cursor_mod._headers("tok"),
                model="auto",
                messages=[{"role": "system", "content": "sys"}, {"role": "user", "content": "weather?"}],
                tools=[{"type": "function", "function": {"name": "get_weather", "parameters": {"type": "object"}}}],
                timeout=httpx.Timeout(5.0),
            ):
                events.append(event)
        self.assertEqual(events[0], ("thinking", "hmm"))
        self.assertEqual(events[1], ("text", "Checking"))
        self.assertEqual(events[2], ("usage", {"prompt_tokens": 100, "completion_tokens": 7}))
        kind, call = events[3]
        self.assertEqual(kind, "tool_call")
        self.assertEqual(call["name"], "get_weather")
        self.assertEqual(call["id"], "call-1_fc_2")
        self.assertEqual(json.loads(call["arguments"]), {"city": "Osaka"})
        self.assertEqual(events[4], ("end", "tool_calls"))
        self.assertEqual(seen["seq"][:3], ["0", "1", "2"])

    async def test_trailer_error_maps_to_status(self):
        async def server():
            yield _frame({"error": {"code": "resource_exhausted", "message": "usage limit"}}, flag=0x02)

        def handler(request):
            if request.url.path.endswith("/RunSSE"):
                return httpx.Response(200, content=server())
            return httpx.Response(200, json={})

        with patch.object(agent.httpx, "AsyncClient", lambda **kw: _RealClient(transport=httpx.MockTransport(handler), **kw)):
            with self.assertRaises(agent.TurnError) as ctx:
                async for _ in agent.run_turn(base="https://x", headers={}, model="auto",
                                              messages=[{"role": "user", "content": "hi"}], tools=[],
                                              timeout=httpx.Timeout(5.0)):
                    pass
        self.assertEqual(ctx.exception.status, 429)


class CursorPoolTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        reset_health()
        db.execute("DELETE FROM credentials")
        db.execute("DELETE FROM api_keys")
        db.execute("DELETE FROM users")
        self.alice = accounts.create_user("cursor-a@test.invalid", "password12", admin=False)
        self.bob = accounts.create_user("cursor-b@test.invalid", "password12", admin=False)
        self.sid = cursor_mod.ensure_cursor_source()

    def test_sign_in_unlocks_owner_and_relink_updates_same_row(self):
        first = cursor_mod.store_tokens(self.sid, _jwt(), "refresh-token-1234", self.alice["id"])
        self.assertTrue(cursor_mod.user_has_cursor(self.alice["id"]))
        self.assertFalse(cursor_mod.user_has_cursor(self.bob["id"]))
        again = cursor_mod.store_tokens(self.sid, _jwt(), "refresh-token-5678", self.alice["id"])
        self.assertEqual(first, again)
        extra = accounts.cred_extra(db.fetchone("SELECT * FROM credentials WHERE id=?", (first,)))
        self.assertEqual(extra["refresh_token"], "refresh-token-5678")
        self.assertEqual(extra["account_id"], "auth0|user_1")

    async def test_pick_routes_only_cursor_ids(self):
        cursor_mod.store_tokens(self.sid, _jwt(), "refresh-token-1234", self.alice["id"])
        src, upstream = await pick("cursor/claude-opus-5-thinking-high", allow_cursor=True)
        self.assertEqual(src.type_id, "cursor")
        self.assertEqual(upstream, "claude-opus-5-thinking-high")
        self.assertIsNone(await pick("cursor/auto", allow_cursor=False))

    async def test_poll_login(self):
        token = _jwt()

        def handler(request):
            self.assertEqual(request.url.path, "/auth/poll")
            if request.url.params["verifier"] == "wait":
                return httpx.Response(404)
            return httpx.Response(200, json={"accessToken": token, "refreshToken": "r" * 20, "authId": "auth0|user_1"})

        with patch.object(cursor_mod.httpx, "AsyncClient", lambda **kw: _RealClient(transport=httpx.MockTransport(handler), **kw)):
            self.assertEqual(await cursor_mod.poll_login("u", "wait"), {"pending": True})
            done = await cursor_mod.poll_login("u", "ok")
        self.assertEqual(done["access_token"], token)

    @patch("app.sources.cursor.refresh_pool_quota", new_callable=AsyncMock)
    @patch("app.sources.cursor.refresh_due", new_callable=AsyncMock)
    async def test_stream_translates_turn_and_retries_401_after_refresh(self, _due, _quota):
        cursor_mod.store_tokens(self.sid, _jwt(), "refresh-token-1234", self.alice["id"])
        calls = []

        async def fake_turn(**kw):
            calls.append(kw["headers"]["Authorization"])
            if len(calls) == 1:
                raise agent.TurnError(401, "not logged in")
            yield ("text", "Hi")
            yield ("tool_call", {"id": "c1", "name": "read_file", "arguments": "{}"})
            yield ("usage", {"prompt_tokens": 9, "completion_tokens": 3})
            yield ("end", "tool_calls")

        async def fake_refresh(cred):
            row = db.fetchone("SELECT * FROM credentials WHERE id=?", (cred["id"],))
            extra = accounts.cred_extra(row)
            extra["access_token"] = _jwt(sub="auth0|user_1") + "x"
            return cursor_mod._cas_update_tokens(row["id"], row["extra"], encrypt(extra["access_token"]), extra)

        with patch.object(cursor_mod, "run_turn", fake_turn), patch.object(cursor_mod, "refresh_one", side_effect=fake_refresh), \
                patch.object(cursor_mod, "note_pool_invoke"):
            src = cursor_mod.CursorSource({"id": self.sid, "name": "Cursor"}, {}, [])
            result = await src.chat({"model": "auto", "stream": True, "messages": [{"role": "user", "content": "hi"}]},
                                    user_id=self.alice["id"])
            self.assertEqual(result.status, 200)
            body = b"".join([chunk async for chunk in result.stream]).decode()
        self.assertEqual(len(calls), 2)
        self.assertNotEqual(calls[0], calls[1])
        frames = [json.loads(line[6:]) for line in body.splitlines() if line.startswith("data: {")]
        self.assertEqual(frames[1]["choices"][0]["delta"], {"content": "Hi"})
        self.assertEqual(frames[2]["choices"][0]["delta"]["tool_calls"][0]["function"]["name"], "read_file")
        self.assertEqual(frames[-1]["choices"][0]["finish_reason"], "tool_calls")
        self.assertEqual(frames[-1]["model"], "cursor/auto")
        self.assertTrue(body.endswith("data: [DONE]\n\n"))
        self.assertEqual(result.stream.usage["prompt_tokens"], 9)
        self.assertEqual(result.stream.usage["stream_status"], "complete")


if __name__ == "__main__":
    unittest.main()
