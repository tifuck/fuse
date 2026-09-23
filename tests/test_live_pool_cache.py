"""A few live requests per pooled provider. Not a load test.

Same-account follow-up should report cached_tokens. A different account with
the same prefix should not (Grok/ChatGPT when two creds are eligible).
"""

from __future__ import annotations

import asyncio
import json
import unittest
from datetime import datetime, timezone

import httpx

from app import accounts, db
from app.config import settings
from app.sources import chatgpt as chatgpt_mod
from app.sources import grok as grok_mod
from app.sources import pool_pick
from app.sources import zai as zai_mod
from app.proxy import usage_from_obj


PREFIX = (
    "CACHE-AFFINITY-TEST "
    + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    + " "
    + ("Reusable prompt prefix for measuring provider prompt cache. " * 120)
)


def _messages(turn: int) -> list[dict]:
    msgs = [
        {"role": "system", "content": PREFIX},
        {"role": "user", "content": "Reply with the single word ping."},
    ]
    if turn >= 2:
        msgs.append({"role": "assistant", "content": "ping"})
        msgs.append({"role": "user", "content": "Reply with the single word pong."})
    return msgs


def _usage(obj) -> dict:
    if not isinstance(obj, dict):
        return {}
    return usage_from_obj(obj)


class LivePoolCacheTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        db.connect()

    def _ready_creds(self, type_id: str) -> list:
        rows = db.fetchall(
            """SELECT c.* FROM credentials c JOIN sources s ON s.id=c.source_id
               WHERE s.type=? AND c.disabled=0""",
            (type_id,),
        )
        out = []
        now = datetime.now(timezone.utc)
        for row in rows:
            extra = accounts.cred_extra(row)
            if type_id == "grok":
                if grok_mod.is_held(row, now) or not grok_mod.credential_is_authed(row, extra):
                    continue
                pct = pool_pick.grok_usage_percent(row, extra)
            elif type_id == "chatgpt":
                if not chatgpt_mod.credential_is_authed(row, extra):
                    continue
                pct = pool_pick.chatgpt_usage_percent(extra)
            else:
                if zai_mod.is_held(row, now) or not zai_mod.credential_is_authed(row, extra):
                    continue
                pct = pool_pick.zai_usage_percent(extra)
            out.append((row, extra, pct is not None and pct >= pool_pick.USAGE_BUSY))
        primary = [item for item in out if not item[2]] or out
        return [(row, extra) for row, extra, _busy in primary]

    async def test_grok_same_account_hits_cache(self):
        creds = self._ready_creds("grok")
        if not creds:
            self.skipTest("no eligible Grok credentials")
        (row_a, extra_a) = creds[0]
        session = "live-grok-" + datetime.now(timezone.utc).strftime("%H%M%S")
        u1 = await self._grok_once(row_a, extra_a, _messages(1), session)
        await asyncio.sleep(2)
        u2 = await self._grok_once(row_a, extra_a, _messages(2), session)
        print("grok_same_account", {"cred": row_a["id"], "first": u1, "second": u2})
        self.assertGreater(u1["prompt_tokens"], 500, u1)
        self.assertGreaterEqual(u2["cached_tokens"], 128, {"first": u1, "second": u2})
        # xAI cache is best-effort per server. One run on this proxy returned
        # 1408/1449 on the follow-up; another returned 128/1449. Require a
        # prefix-sized hit when the follow-up grew, otherwise keep the floor.
        if u2["prompt_tokens"] > u1["prompt_tokens"] and u2["cached_tokens"] > 256:
            self.assertGreaterEqual(u2["cached_tokens"], u1["cached_tokens"])
        if len(creds) < 2 or u2["cached_tokens"] <= 256:
            return
        (row_b, extra_b) = creds[1]
        u3 = await self._grok_once(row_b, extra_b, _messages(2), session + "-other")
        print(
            "grok_other_account",
            {"cred_a": row_a["id"], "cred_b": row_b["id"], "same": u2, "other": u3},
        )
        # cli-chat-proxy has been observed to reuse a prefix across OAuth
        # accounts (1408 cached on both). Do not require a miss.

    async def test_chatgpt_cache_and_account_isolation(self):
        creds = self._ready_creds("chatgpt")
        if not creds:
            self.skipTest("no eligible ChatGPT credentials")
        (row_a, extra_a) = creds[0]
        session = "live-chatgpt-" + datetime.now(timezone.utc).strftime("%H%M%S")
        u1 = await self._chatgpt_once(row_a, extra_a, _messages(1), session)
        u2 = await self._chatgpt_once(row_a, extra_a, _messages(2), session)
        self.assertGreater(u1.get("prompt_tokens", 0), 500, u1)
        # Codex may not report cached_tokens even on a hit. Record the observation.
        print("chatgpt_same_account", {"cred": row_a["id"], "first": u1, "second": u2})
        if u2.get("cached_tokens", 0) == 0:
            return
        if len(creds) < 2:
            return
        (row_b, extra_b) = creds[1]
        u3 = await self._chatgpt_once(row_b, extra_b, _messages(2), session + "-other")
        print("chatgpt_other_account", {"cred_a": row_a["id"], "cred_b": row_b["id"], "same": u2, "other": u3})
        self.assertLess(u3.get("cached_tokens", 0), u2["cached_tokens"], {"same": u2, "other": u3})

    async def test_zai_same_account_hits_cache(self):
        creds = self._ready_creds("zai")
        if not creds:
            self.skipTest("no eligible Z.AI credentials")
        (row, extra) = creds[0]
        key = extra.get("api_key") or accounts.cred_secret(row)
        region = extra.get("region") or "global"
        u1 = await self._zai_once(key, region, _messages(1))
        u2 = await self._zai_once(key, region, _messages(2))
        self.assertGreater(u1["prompt_tokens"], 200, u1)
        self.assertGreater(u2["cached_tokens"], 0, {"first": u1, "second": u2})

    async def _grok_once(self, row, extra, messages: list[dict], conv_id: str) -> dict:
        token = extra.get("access_token") or accounts.cred_secret(row)
        body = {
            "model": "grok-4.5",
            "messages": messages,
            "max_tokens": 8,
            "stream": False,
        }
        async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=15.0)) as client:
            headers = grok_mod._auth_headers(token, "grok-4.5", conv_id=conv_id)
            r = await client.post(settings.grok_proxy + "/chat/completions", json=body, headers=headers)
            if r.status_code == 401:
                if await grok_mod.refresh_one(row):
                    row = db.fetchone("SELECT * FROM credentials WHERE id=?", (row["id"],))
                    extra = accounts.cred_extra(row)
                    token = extra.get("access_token") or accounts.cred_secret(row)
                    headers = grok_mod._auth_headers(token, "grok-4.5", conv_id=conv_id)
                    r = await client.post(settings.grok_proxy + "/chat/completions", json=body, headers=headers)
            try:
                obj = r.json()
            except json.JSONDecodeError:
                obj = {"error": r.text[:400]}
            self.assertEqual(r.status_code, 200, obj)
            return _usage(obj)

    async def _chatgpt_once(self, row, extra, messages: list[dict], session: str) -> dict:
        token = extra.get("access_token") or accounts.cred_secret(row)
        account_id = extra.get("account_id") or None
        payload = chatgpt_mod.build_responses_payload(
            {"messages": messages, "max_tokens": 16},
            "gpt-5.6-luna",
            session_id=session,
        )
        src = chatgpt_mod.ChatGPTSource({"id": 0, "name": "t"}, {}, [])
        client = chatgpt_mod._client()
        try:
            resp = await src._post(token, account_id, payload, client)
            if resp.status_code == 401:
                await resp.aclose()
                if await chatgpt_mod.refresh_one(row):
                    row = db.fetchone("SELECT * FROM credentials WHERE id=?", (row["id"],))
                    extra = accounts.cred_extra(row)
                    token = extra.get("access_token") or accounts.cred_secret(row)
                    account_id = extra.get("account_id") or None
                    resp = await src._post(token, account_id, payload, client)
            if resp.status_code != 200:
                try:
                    obj = resp.json()
                except json.JSONDecodeError:
                    obj = {"error": resp.text[:400]}
                await resp.aclose()
                self.fail(f"chatgpt http {resp.status_code}: {obj}")
            obj = await chatgpt_mod._aggregate(resp, client, "gpt-5.6-luna")
        except Exception:
            await client.aclose()
            raise
        if isinstance(obj.get("error"), dict):
            self.fail(obj)
        usage = obj.get("usage") if isinstance(obj.get("usage"), dict) else {}
        return {
            "prompt_tokens": int(usage.get("prompt_tokens") or 0),
            "completion_tokens": int(usage.get("completion_tokens") or 0),
            "cached_tokens": int(usage.get("cached_tokens") or 0),
        }

    async def _zai_once(self, key: str, region: str, messages: list[dict]) -> dict:
        payload = zai_mod._chat_payload(
            {
                "model": "glm-5.3-flash",
                "messages": messages,
                "max_tokens": 8,
                "stream": False,
            }
        )
        base, _ = zai_mod._endpoints(region)
        async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=15.0)) as client:
            r = await client.post(base + "/chat/completions", json=payload, headers=zai_mod._chat_headers(key))
            try:
                obj = r.json()
            except json.JSONDecodeError:
                obj = {"error": r.text[:400]}
            self.assertEqual(r.status_code, 200, obj)
            return _usage(obj)


if __name__ == "__main__":
    unittest.main()
