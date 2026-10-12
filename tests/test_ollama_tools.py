"""Ollama tool id stability (no network)."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
_TMP = tempfile.mkdtemp(prefix="fuse-ollama-")
os.environ.setdefault("DATABASE_PATH", str(Path(_TMP) / "chat.db"))
os.environ.setdefault("APP_SECRET", "x" * 32)

import httpx

from app.sources import ollama as ollama_mod
from app.sources import ollama_access
from app.sources.ollama import to_ollama, to_openai_tools


class OllamaToolsTest(unittest.TestCase):
    def test_stable_ids_without_upstream(self):
        a = to_openai_tools([{"function": {"name": "web_search", "arguments": {"query": "x"}}}], include_index=True)
        b = to_openai_tools([{"function": {"name": "web_search", "arguments": {"query": "x"}}}], include_index=True)
        self.assertEqual(a[0]["id"], "ollama_0")
        self.assertEqual(a[0]["id"], b[0]["id"])

    def test_keeps_upstream_id(self):
        out = to_openai_tools([{"id": "keep-me", "function": {"name": "web_search", "arguments": "{}"}}])
        self.assertEqual(out[0]["id"], "keep-me")

    def test_tool_call_id_roundtrip(self):
        msgs = to_ollama(
            [
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {"id": "call_9", "function": {"name": "web_search", "arguments": '{"query":"a"}'}}
                    ],
                },
                {"role": "tool", "tool_call_id": "call_9", "content": "hits"},
            ]
        )
        self.assertEqual(msgs[0]["tool_calls"][0]["id"], "call_9")
        self.assertEqual(msgs[1]["tool_call_id"], "call_9")
        self.assertEqual(msgs[1]["tool_name"], "web_search")

    def test_tool_name_from_message_or_matching_call(self):
        msgs = to_ollama(
            [
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {"id": "c1", "function": {"name": "get_temperature", "arguments": "{}"}},
                        {"id": "c2", "function": {"name": "get_conditions", "arguments": "{}"}},
                    ],
                },
                {"role": "tool", "tool_call_id": "c1", "content": "22C"},
                {"role": "tool", "tool_call_id": "c2", "name": "overridden", "content": "sunny"},
            ]
        )
        self.assertEqual(msgs[1]["tool_name"], "get_temperature")
        self.assertEqual(msgs[2]["tool_name"], "overridden")
        self.assertEqual(msgs[1]["tool_call_id"], "c1")


class _FakeResp:
    def __init__(self, lines: list[str]):
        self._lines = lines

    async def aiter_lines(self):
        for line in self._lines:
            yield line

    async def aclose(self):
        pass


class OllamaStreamToolIdentityTest(unittest.IsolatedAsyncioTestCase):
    """Streamed tool calls keep stable OpenAI identities across native chunks."""

    async def _deltas(self, *native_chunks: dict) -> list[dict]:
        lines = [json.dumps(c) for c in native_chunks]
        out = []
        async for chunk in ollama_mod._stream(_FakeResp(lines), "m"):
            data = chunk.decode()[len("data: "):].strip()
            if not data or data == "[DONE]":
                continue
            obj = json.loads(data)
            if obj.get("choices"):
                delta = obj["choices"][0].get("delta") or {}
                if delta.get("tool_calls"):
                    out.extend(delta["tool_calls"])
        return out

    async def test_distinct_native_indexes_never_merge(self):
        deltas = await self._deltas(
            {"message": {"role": "assistant", "tool_calls": [
                {"function": {"name": "first", "index": 0, "arguments": {"a": 1}}}], "content": ""}},
            {"message": {"tool_calls": [
                {"function": {"name": "second", "index": 1, "arguments": {"b": 2}}}], "content": ""}},
            {"done": True, "message": {}},
        )
        self.assertEqual([d["index"] for d in deltas], [0, 1])
        self.assertNotEqual(deltas[0]["id"], deltas[1]["id"])
        self.assertEqual(deltas[0]["function"]["arguments"], '{"a": 1}')
        self.assertEqual(deltas[1]["function"]["arguments"], '{"b": 2}')

    async def test_same_native_index_is_one_call_across_chunks(self):
        deltas = await self._deltas(
            {"message": {"tool_calls": [
                {"function": {"name": "frag", "index": 0, "arguments": '{"q":'}}], "content": ""}},
            {"message": {"tool_calls": [
                {"function": {"index": 0, "arguments": '"x"}'}}], "content": ""}},
            {"done": True, "message": {}},
        )
        self.assertEqual([d["index"] for d in deltas], [0, 0])
        # Continuation fragments identify by index only (OpenAI delta shape);
        # a second id would look like a new call to index-keyed consumers.
        self.assertNotIn("id", deltas[1])
        self.assertIn("name", deltas[0]["function"])
        self.assertNotIn("name", deltas[1]["function"])

    async def test_restarted_native_index_is_a_new_call(self):
        # Ollama numbers each chunk's calls from 0, so a later chunk's
        # complete call at index 0 is not a fragment of the previous one.
        deltas = await self._deltas(
            {"message": {"tool_calls": [
                {"function": {"name": "get_temperature", "index": 0, "arguments": {"city": "NY"}}}], "content": ""}},
            {"message": {"tool_calls": [
                {"function": {"name": "get_conditions", "index": 0, "arguments": {"city": "London"}}}], "content": ""}},
            {"done": True, "message": {}},
        )
        self.assertEqual([d["index"] for d in deltas], [0, 1])
        self.assertEqual(deltas[0]["function"]["name"], "get_temperature")
        self.assertEqual(deltas[1]["function"]["name"], "get_conditions")
        self.assertEqual(json.loads(deltas[0]["function"]["arguments"])["city"], "NY")
        self.assertEqual(json.loads(deltas[1]["function"]["arguments"])["city"], "London")
        self.assertIn("id", deltas[1])
        self.assertNotEqual(deltas[0]["id"], deltas[1]["id"])

    async def test_restarted_native_index_with_same_name_is_a_new_call(self):
        deltas = await self._deltas(
            {"message": {"tool_calls": [
                {"function": {"name": "read_file", "index": 0, "arguments": {"path": "a"}}}], "content": ""}},
            {"message": {"tool_calls": [
                {"function": {"name": "read_file", "index": 0, "arguments": {"path": "b"}}}], "content": ""}},
            {"done": True, "message": {}},
        )
        self.assertEqual([d["index"] for d in deltas], [0, 1])
        self.assertNotEqual(deltas[0]["id"], deltas[1]["id"])
        self.assertEqual(json.loads(deltas[1]["function"]["arguments"])["path"], "b")

    async def test_indexless_calls_get_distinct_indexes(self):
        # Old behavior: every chunk restarted at 0, so downstream keyed on
        # index concatenated two different calls into "firstsecond".
        deltas = await self._deltas(
            {"message": {"tool_calls": [{"function": {"name": "first", "arguments": "first"}}], "content": ""}},
            {"message": {"tool_calls": [{"function": {"name": "second", "arguments": "second"}}], "content": ""}},
            {"done": True, "message": {}},
        )
        self.assertEqual([d["index"] for d in deltas], [0, 1])
        self.assertEqual([d["function"]["arguments"] for d in deltas], ["first", "second"])

    async def test_done_frame_tool_calls_are_not_dropped(self):
        lines = [
            json.dumps({"message": {"role": "assistant", "content": ""}}),
            json.dumps({"done": True, "message": {"tool_calls": [
                {"function": {"name": "last", "arguments": {"z": 9}}}]},
                "prompt_eval_count": 1, "eval_count": 2}),
        ]
        chunks = [json.loads(c.decode()[len("data: "):].strip())
                  async for c in ollama_mod._stream(_FakeResp(lines), "m")
                  if c.decode()[len("data: "):].strip() not in ("", "[DONE]")]
        finish = [c for c in chunks if c.get("choices") and c["choices"][0].get("finish_reason")]
        self.assertEqual(finish[0]["choices"][0]["finish_reason"], "tool_calls")
        tool_deltas = [d for c in chunks if c.get("choices")
                       for d in c["choices"][0].get("delta", {}).get("tool_calls") or []]
        self.assertEqual(tool_deltas[0]["function"]["name"], "last")


class OllamaAccessClassifyTest(unittest.TestCase):
    """A reworded 403 paywall must never strike auth on a valid key."""

    def test_gated_statuses(self):
        self.assertEqual(ollama_access.classify_cloud_error(402, "subscription required"), "model_gated")
        self.assertEqual(
            ollama_access.classify_cloud_error(403, "this model requires a subscription"), "model_gated"
        )

    def test_401_is_always_auth(self):
        self.assertEqual(ollama_access.classify_cloud_error(401, ""), "auth")
        self.assertTrue(ollama_access.is_credential_auth_error(401, ""))

    def test_403_with_credential_wording_is_auth(self):
        self.assertEqual(ollama_access.classify_cloud_error(403, "invalid api key"), "auth")
        self.assertTrue(ollama_access.is_credential_auth_error(403, "token expired"))

    def test_403_with_novel_wording_is_model_scoped(self):
        body = "model not included in your current plan tier"
        self.assertEqual(ollama_access.classify_cloud_error(403, body), "model_gated")
        self.assertFalse(ollama_access.is_credential_auth_error(403, body))

    def test_429_markers(self):
        self.assertEqual(
            ollama_access.classify_cloud_error(429, "you reached your weekly limit"), "weekly_quota"
        )
        self.assertEqual(ollama_access.classify_cloud_error(429, "slow down"), "rate_limit")


class _OllamaSourceTestMixin:
    SOURCE_ID = 987654

    def setUp(self):
        ollama_access._cache[self.SOURCE_ID] = {}
        self.addCleanup(ollama_access._cache.pop, self.SOURCE_ID, None)
        ollama_mod._tags_cache.pop(self.SOURCE_ID, None)
        self.addCleanup(ollama_mod._tags_cache.pop, self.SOURCE_ID, None)
        save = patch.object(ollama_access, "save", lambda source_id: None)
        save.start()
        self.addCleanup(save.stop)

    def _source(self, creds):
        src = object.__new__(ollama_mod.OllamaSource)
        src.source_id = self.SOURCE_ID
        src.base = "https://ollama.com/api"
        src.creds = creds
        return src

    def _network(self, handler, keys):
        real_client = httpx.AsyncClient
        client = patch.object(
            ollama_mod.httpx,
            "AsyncClient",
            lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw),
        )
        secret = patch.object(ollama_mod.accounts, "cred_secret", side_effect=lambda c: keys[c["id"]])
        client.start()
        secret.start()
        self.addCleanup(client.stop)
        self.addCleanup(secret.stop)


class OllamaRefreshAccessTest(_OllamaSourceTestMixin, unittest.IsolatedAsyncioTestCase):
    async def test_access_reflects_any_key_in_the_pool(self):
        def handler(request):
            auth = request.headers["authorization"]
            model = json.loads(request.content)["model"]
            if auth == "Bearer kg":
                if model == "m-broken":
                    return httpx.Response(500, json={"error": "boom"})
                return httpx.Response(403, json={"error": "this model requires a subscription"})
            if model == "m-locked":
                return httpx.Response(403, json={"error": "this model requires a subscription"})
            return httpx.Response(200, json={"message": {"role": "assistant", "content": ""}})

        self._network(handler, keys={1: "kg", 2: "kf"})
        src = self._source([{"id": 1}, {"id": 2}])
        await src._refresh_access(["m-free", "m-paid", "m-locked", "m-broken"])
        self.assertEqual(ollama_access.of(self.SOURCE_ID, "m-free"), "free")
        # Free on the second key even though the first is gated.
        self.assertEqual(ollama_access.of(self.SOURCE_ID, "m-paid"), "free")
        self.assertEqual(ollama_access.of(self.SOURCE_ID, "m-broken"), "free")
        self.assertEqual(ollama_access.of(self.SOURCE_ID, "m-locked"), "gated")

    async def test_all_keys_gated_marks_gated(self):
        def handler(request):
            return httpx.Response(403, json={"error": "this model requires a subscription"})

        self._network(handler, keys={1: "kg"})
        src = self._source([{"id": 1}])
        await src._refresh_access(["m-locked"])
        self.assertEqual(ollama_access.of(self.SOURCE_ID, "m-locked"), "gated")


class OllamaTagsGuardTest(_OllamaSourceTestMixin, unittest.IsolatedAsyncioTestCase):
    async def test_non_200_tags_raises_and_caches_nothing(self):
        self._network(
            lambda request: httpx.Response(401, json={"error": "unauthorized"}),
            keys={1: "kg"},
        )
        src = self._source([{"id": 1}])
        with self.assertRaises(ValueError):
            await src._tags()
        self.assertNotIn(self.SOURCE_ID, ollama_mod._tags_cache)

    async def test_empty_tags_catalog_is_not_cached(self):
        self._network(
            lambda request: httpx.Response(200, json={"models": []}),
            keys={1: "kg"},
        )
        src = self._source([{"id": 1}])
        with self.assertRaises(ValueError):
            await src._tags()
        self.assertNotIn(self.SOURCE_ID, ollama_mod._tags_cache)


if __name__ == "__main__":
    unittest.main()
