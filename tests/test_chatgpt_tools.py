"""ChatGPT Responses tool mapping fixtures (no network)."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
_TMP = tempfile.mkdtemp(prefix="fuse-cg-")
os.environ.setdefault("DATABASE_PATH", str(Path(_TMP) / "chat.db"))
os.environ.setdefault("APP_SECRET", "x" * 32)

from app.sources.chatgpt import build_responses_payload
from app.sources.chatgpt_stream import _aggregate, _translated_stream


class ChatgptToolsTest(unittest.TestCase):
    def test_function_call_pairing(self):
        payload = build_responses_payload(
            {
                "messages": [
                    {"role": "system", "content": "sys"},
                    {"role": "user", "content": "search this"},
                    {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {
                                "id": "call_abc",
                                "type": "function",
                                "function": {"name": "web_search", "arguments": '{"query":"x"}'},
                            }
                        ],
                    },
                    {"role": "tool", "tool_call_id": "call_abc", "content": "hits"},
                ],
                "tools": [
                    {
                        "type": "function",
                        "function": {"name": "web_search", "description": "s", "parameters": {"type": "object"}},
                    }
                ],
                "tool_choice": "none",
                "parallel_tool_calls": True,
            },
            "gpt-5",
            session_id="sess",
        )
        kinds = [i.get("type") or i.get("role") for i in payload["input"]]
        self.assertIn("function_call", kinds)
        self.assertIn("function_call_output", kinds)
        call = next(i for i in payload["input"] if i.get("type") == "function_call")
        out = next(i for i in payload["input"] if i.get("type") == "function_call_output")
        self.assertEqual(call["call_id"], "call_abc")
        self.assertEqual(out["call_id"], "call_abc")
        self.assertEqual(payload.get("tool_choice"), "none")


class _StreamResp:
    def __init__(self, frames):
        self._frames = frames
        self.closed = False

    async def aiter_text(self):
        for frame in self._frames:
            yield frame

    async def aclose(self):
        self.closed = True


class _FakeClient:
    def __init__(self):
        self.closed = False

    async def aclose(self):
        self.closed = True


def _frame(obj: dict) -> str:
    return "data: " + json.dumps(obj) + "\n\n"


class ChatgptStreamEdgeTest(unittest.IsolatedAsyncioTestCase):
    async def test_zero_argument_call_gets_object_arguments(self):
        resp = _StreamResp(
            [
                _frame({"type": "response.created", "response": {"id": "r1"}}),
                _frame(
                    {
                        "type": "response.output_item.added",
                        "item": {"type": "function_call", "id": "i1", "call_id": "call_1", "name": "ping", "arguments": ""},
                    }
                ),
                _frame({"type": "response.function_call_arguments.done", "item_id": "i1", "arguments": ""}),
                _frame({"type": "response.completed", "response": {"id": "r1", "usage": {}}}),
            ]
        )
        body = await _aggregate(resp, _FakeClient(), "gpt-5")
        call = body["choices"][0]["message"]["tool_calls"][0]
        self.assertEqual(call["function"]["arguments"], "{}")

    async def _streamed_calls(self, frames):
        """Arguments per call as a chat-completions client accumulates them."""
        chunks = [c async for c in _translated_stream(_StreamResp(frames), _FakeClient(), "gpt-5", {})]
        calls: dict[int, dict] = {}
        for chunk in chunks:
            text = chunk.decode()
            if not text.startswith("data: {"):
                continue
            for choice in json.loads(text[6:]).get("choices") or []:
                for call in (choice.get("delta") or {}).get("tool_calls") or []:
                    rec = calls.setdefault(call["index"], {"name": "", "arguments": ""})
                    fn = call.get("function") or {}
                    rec["name"] += fn.get("name") or ""
                    rec["arguments"] += fn.get("arguments") or ""
        return [calls[i] for i in sorted(calls)]

    @staticmethod
    def _added(n, item_id="", output_index=None):
        event = {"type": "response.output_item.added",
                 "item": {"type": "function_call", "id": item_id, "call_id": f"call_{n}", "name": "grep", "arguments": ""}}
        if output_index is not None:
            event["output_index"] = output_index
        return _frame(event)

    async def test_parallel_calls_keep_arguments_that_arrive_only_when_done(self):
        # Production (Oct 2026): rounds of parallel ChatGPT calls all ran
        # with "{}" while single calls kept their arguments.
        frames = [
            _frame({"type": "response.created", "response": {"id": "r5"}}),
            self._added(1, "fc_1", 0), self._added(2, "fc_2", 1), self._added(3, "fc_3", 2),
            _frame({"type": "response.function_call_arguments.done", "item_id": "fc_1", "output_index": 0, "arguments": '{"pattern":"a"}'}),
            _frame({"type": "response.output_item.done", "output_index": 1,
                    "item": {"type": "function_call", "id": "fc_2", "call_id": "call_2", "name": "grep", "arguments": '{"pattern":"b"}'}}),
            _frame({"type": "response.function_call_arguments.delta", "item_id": "other_3", "output_index": 2, "delta": '{"pattern":'}),
            _frame({"type": "response.function_call_arguments.delta", "item_id": "other_3", "output_index": 2, "delta": '"c"}'}),
            _frame({"type": "response.function_call_arguments.done", "item_id": "other_3", "output_index": 2, "arguments": '{"pattern":"c"}'}),
            _frame({"type": "response.output_item.done", "output_index": 2,
                    "item": {"type": "function_call", "id": "fc_3", "call_id": "call_3", "name": "grep", "arguments": '{"pattern":"c"}'}}),
            _frame({"type": "response.completed", "response": {"id": "r5", "usage": {}}}),
        ]
        calls = await self._streamed_calls(frames)
        self.assertEqual([c["arguments"] for c in calls], ['{"pattern":"a"}', '{"pattern":"b"}', '{"pattern":"c"}'])
        body = await _aggregate(_StreamResp(frames), _FakeClient(), "gpt-5")
        self.assertEqual([c["function"]["arguments"] for c in body["choices"][0]["message"]["tool_calls"]],
                         ['{"pattern":"a"}', '{"pattern":"b"}', '{"pattern":"c"}'])

    async def test_streamed_arguments_are_not_sent_twice(self):
        frames = [
            _frame({"type": "response.created", "response": {"id": "r6"}}),
            self._added(1, "fc_1", 0),
            _frame({"type": "response.function_call_arguments.delta", "item_id": "fc_1", "delta": '{"pattern":'}),
            _frame({"type": "response.function_call_arguments.delta", "item_id": "fc_1", "delta": '"a"}'}),
            _frame({"type": "response.function_call_arguments.done", "item_id": "fc_1", "arguments": '{"pattern":"a"}'}),
            _frame({"type": "response.output_item.done", "item": {"type": "function_call", "id": "fc_1", "call_id": "call_1", "name": "grep", "arguments": '{"pattern":"a"}'}}),
            _frame({"type": "response.completed", "response": {"id": "r6", "usage": {}}}),
        ]
        self.assertEqual(await self._streamed_calls(frames), [{"name": "grep", "arguments": '{"pattern":"a"}'}])

    async def test_reused_item_id_stays_two_calls(self):
        # A repeated item id must not collapse both calls onto one record:
        # the second add used to overwrite the first while order kept both keys.
        frames = [
            _frame({"type": "response.created", "response": {"id": "r8"}}),
            self._added(1, "fc", 0),
            _frame({"type": "response.function_call_arguments.delta", "item_id": "fc", "output_index": 0, "delta": '{"a":1}'}),
            self._added(2, "fc", 1),
            _frame({"type": "response.function_call_arguments.delta", "item_id": "fc", "output_index": 1, "delta": '{"b":2}'}),
            _frame({"type": "response.output_item.done", "output_index": 0,
                    "item": {"type": "function_call", "id": "fc", "call_id": "call_1", "name": "grep", "arguments": '{"a":1}'}}),
            _frame({"type": "response.completed", "response": {"id": "r8", "usage": {}}}),
        ]
        calls = await self._streamed_calls(frames)
        self.assertEqual(calls, [
            {"name": "grep", "arguments": '{"a":1}'},
            {"name": "grep", "arguments": '{"b":2}'},
        ])
        body = await _aggregate(_StreamResp(frames), _FakeClient(), "gpt-5")
        message_calls = body["choices"][0]["message"]["tool_calls"]
        self.assertEqual(
            [(c["id"], c["function"]["arguments"]) for c in message_calls],
            [("call_1", '{"a":1}'), ("call_2", '{"b":2}')],
        )

    async def test_items_without_ids_stay_separate_calls(self):
        frames = [
            _frame({"type": "response.created", "response": {"id": "r7"}}),
            self._added(1, "", 0), self._added(2, "", 1),
            _frame({"type": "response.function_call_arguments.done", "output_index": 0, "arguments": '{"pattern":"a"}'}),
            _frame({"type": "response.function_call_arguments.done", "output_index": 1, "arguments": '{"pattern":"b"}'}),
            _frame({"type": "response.completed", "response": {"id": "r7", "usage": {}}}),
        ]
        self.assertEqual([c["arguments"] for c in await self._streamed_calls(frames)], ['{"pattern":"a"}', '{"pattern":"b"}'])

    async def test_disconnect_mid_stream_is_cancelled_not_failed(self):
        resp = _StreamResp(
            [
                _frame({"type": "response.created", "response": {"id": "r2"}}),
                _frame({"type": "response.output_text.delta", "delta": "partial"}),
            ]
        )
        collected = {}
        stream = _translated_stream(resp, _FakeClient(), "gpt-5", collected)
        await stream.__anext__()
        await stream.__anext__()
        await stream.aclose()
        # A cancelled stream must not cooldown the credential: only a failed
        # one may (chatgpt._bind_completed_stream).
        self.assertEqual(collected["stream_status"], "cancelled")


ENCRYPTED = {"type": "reasoning", "id": "rs_1", "encrypted_content": "gAAAA-opaque",
             "summary": [{"type": "summary_text", "text": "Plan the search."}]}


class ChatgptReasoningReplayTest(unittest.IsolatedAsyncioTestCase):
    """store=false: encrypted reasoning is captured and sent back, per account."""

    async def test_stream_and_aggregate_capture_encrypted_reasoning_with_account(self):
        frames = [
            _frame({"type": "response.created", "response": {"id": "r3"}}),
            _frame({"type": "response.output_item.done", "item": ENCRYPTED}),
            _frame({"type": "response.output_item.done", "item": {"type": "reasoning", "summary": []}}),
            _frame({"type": "response.completed", "response": {"id": "r3", "usage": {}}}),
        ]
        chunks = [c async for c in _translated_stream(_StreamResp(frames), _FakeClient(), "gpt-5", {}, account=7)]
        blocks = [json.loads(c[5:])["choices"][0]["delta"]["claude_thinking"]
                  for c in chunks if b"claude_thinking" in c]
        want = {"openai_reasoning": {"encrypted_content": "gAAAA-opaque", "account": 7,
                                     "summary": [{"type": "summary_text", "text": "Plan the search."}]}}
        self.assertEqual(blocks, [[want]], "only the item with encrypted content is replayable")
        body = await _aggregate(_StreamResp(frames), _FakeClient(), "gpt-5", account=7)
        self.assertEqual(body["choices"][0]["message"]["claude_thinking"], [want])

    def test_runtime_keeps_blocks_for_chatgpt_only(self):
        from app.chat_generation.parsing import _native_reasoning, _take_thinking

        bucket = []
        _take_thinking(bucket, [{"openai_reasoning": {"encrypted_content": "e", "account": 7, "summary": []}}])
        self.assertEqual(_native_reasoning("visible", "gpt-5.5", bucket),
                         {"claude_thinking": bucket, "_fuse_reasoning_model": "gpt-5.5"})
        self.assertEqual(_native_reasoning("visible", "openrouter/openai/gpt-5", []), {})

    def test_payload_replays_reasoning_before_calls_for_its_account_only(self):
        from app.sources.chatgpt_payload import drop_reasoning, for_account

        block = {"openai_reasoning": {"encrypted_content": "e", "account": 7, "summary": []}}
        payload = build_responses_payload({"messages": [
            {"role": "user", "content": "go"},
            {"role": "assistant", "content": "", "claude_thinking": [block],
             "tool_calls": [{"id": "c1", "function": {"name": "web_search", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "c1", "content": "hits"},
        ]}, "gpt-5")
        self.assertEqual(payload["include"], ["reasoning.encrypted_content"])
        kinds = [i.get("type") or i.get("role") for i in payload["input"]]
        self.assertEqual(kinds, ["user", "reasoning", "function_call", "function_call_output"])
        same = for_account(payload, 7)
        self.assertEqual(same["input"][1], {"type": "reasoning", "summary": [], "encrypted_content": "e"})
        self.assertNotIn("reasoning", [i.get("type") for i in for_account(payload, 8)["input"]],
                         "another account cannot decrypt it")
        forged = {"input": [{"type": "reasoning", "_fuse_account": "abc", "encrypted_content": "e"}]}
        self.assertEqual(for_account(forged, 7)["input"], [], "a malformed replayed account id is dropped")
        self.assertIn("_fuse_account", payload["input"][1], "the shared payload is not mutated")
        self.assertTrue(drop_reasoning(payload))
        self.assertFalse(drop_reasoning(payload))

    async def test_undecryptable_reasoning_retries_once_without_it(self):
        from unittest.mock import AsyncMock, patch
        from app.sources import chatgpt

        class _ErrResp:
            status_code = 400
            text = json.dumps({"error": {"message": "The encrypted content could not be verified."}})

            async def aread(self):
                return b""

            async def aclose(self):
                pass

        ok = _StreamResp([
            _frame({"type": "response.created", "response": {"id": "r4"}}),
            _frame({"type": "response.output_text.delta", "delta": "done"}),
            _frame({"type": "response.completed", "response": {"id": "r4", "usage": {}}}),
        ])
        ok.status_code = 200
        sent = []

        async def post(token, account_id, payload, client):
            sent.append(payload)
            return _ErrResp() if len(sent) == 1 else ok

        cred = {"id": 7, "_extra": {"access_token": "t"}}
        source = chatgpt.ChatGPTSource({"id": 1, "name": "ChatGPT"}, {}, [])
        block = {"openai_reasoning": {"encrypted_content": "e", "account": 7, "summary": []}}
        body = {"model": "gpt-5", "messages": [
            {"role": "user", "content": "go"},
            {"role": "assistant", "content": "", "claude_thinking": [block],
             "tool_calls": [{"id": "c1", "function": {"name": "web_search", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "c1", "content": "hits"}]}
        with patch.object(chatgpt, "refresh_due", AsyncMock()), patch.object(chatgpt, "refresh_pool_usage", AsyncMock()), \
             patch.object(chatgpt.db, "fetchall", return_value=[]), \
             patch.object(chatgpt.pool_policy, "filter_credentials", return_value=[cred]), \
             patch.object(source, "_pick", side_effect=lambda *a, **k: (cred, cred["_extra"]) if 7 not in k["exclude"] else None), \
             patch.object(source, "_post", side_effect=post), patch.object(chatgpt, "_note_invoke"), \
             patch.object(chatgpt, "reserve"), patch.object(chatgpt, "release"), \
             patch.object(chatgpt, "_client", return_value=_FakeClient()), patch.object(chatgpt, "bind"), \
             patch.object(chatgpt, "lookup", return_value=None):
            result = await source.chat(body)
        self.assertEqual(result.status, 200)
        self.assertEqual(len(sent), 2)
        self.assertIn("reasoning", [i.get("type") for i in sent[0]["input"]])
        self.assertNotIn("reasoning", [i.get("type") for i in sent[1]["input"]])

    def test_openrouter_fallback_strips_pool_reasoning_blocks(self):
        from app.sources.openrouter import _chat_payload

        body = {"messages": [{"role": "assistant", "content": "x",
                              "claude_thinking": [{"openai_reasoning": {"encrypted_content": "e"}}]}]}
        payload = _chat_payload(body, "openai/gpt-5", free=False)
        self.assertEqual(payload["messages"], [{"role": "assistant", "content": "x"}])
        self.assertIn("claude_thinking", body["messages"][0], "the caller's body is not mutated")


if __name__ == "__main__":
    unittest.main()
