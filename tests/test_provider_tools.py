"""Tool-call contract across providers.

Devin Cascade is not OpenAI Chat Completions. It streams function arguments as
protobuf `delta_tool_calls`, and incomplete JSON lives in field 4
(`invalid_json_str`) instead of field 3 (`arguments_json`). Treating each
frame as a new OpenAI `index` splits `{"host":` and `"box",...}` into two
calls, so the executor sees no `host` even though Claude's thinking said it
passed one.

OpenAI-compat providers (Grok, Z.AI, llama, openai) forward Chat Completions
tool deltas as-is. ChatGPT maps Responses events onto the same index-stable
shape. The dashboard runtime (`_take_calls`) only works if every source emits
that shape.

No live upstream calls.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_TMP = tempfile.mkdtemp(prefix="fuse-ptools-")
os.environ.setdefault("DATABASE_PATH", str(Path(_TMP) / "tools.db"))
os.environ.setdefault("APP_SECRET", "x" * 32)

from app.chat_runtime import _take_calls  # noqa: E402
from app.sources import chatgpt as chatgpt_mod  # noqa: E402
from app.sources import devin as devin_mod  # noqa: E402
from app.sources import devin_proto as proto  # noqa: E402
from app.sources import ollama as ollama_mod  # noqa: E402
from app.sources import zai as zai_mod  # noqa: E402
from app.sources.pool_session import drop_cache_routing_fields  # noqa: E402


HOST_TOOL = {
    "type": "function",
    "function": {
        "name": "run_shell",
        "description": "Run a command on a host",
        "parameters": {
            "type": "object",
            "properties": {
                "host": {"type": "string"},
                "command": {"type": "string"},
            },
            "required": ["host", "command"],
        },
    },
}

# Cascade-style stream: same call id, arguments split across frames. Field 4
# holds the partial JSON Devin labels "invalid".
DEVIN_HOST_FRAMES = (
    {"id": "call_host", "name": "run_shell", "arguments_json": "", "invalid_json": '{"host":'},
    {"id": "call_host", "name": "run_shell", "arguments_json": "", "invalid_json": '"edge","command":"uname -a"}'},
)


def _runtime_from_openai_deltas(deltas: list[dict]) -> dict:
    bucket: dict = {}
    for delta in deltas:
        _take_calls(bucket, delta.get("tool_calls") or [])
    return bucket


def _runtime_from_devin_frames(frames) -> dict:
    """Same merge-then-one-chunk path `_stream_sse` uses."""
    book = devin_mod._ToolBook()
    for tc in frames:
        book.add(tc)
    bucket: dict = {}
    _take_calls(
        bucket,
        [
            {
                "index": call["index"],
                "id": call["id"],
                "function": call["function"],
            }
            for call in book.openai_calls()
        ],
    )
    return bucket


def _args(bucket, index=0) -> dict:
    return json.loads(bucket[index]["function"]["arguments"])


class DevinParseDiffersTest(unittest.TestCase):
    """Devin is the provider that does not speak OpenAI tool deltas natively."""

    def test_field_4_not_field_3_holds_partial_json(self):
        enc = proto.ProtoEncoder()
        enc.string(1, "call_host")
        enc.string(2, "run_shell")
        enc.string(4, '{"host":"edge"}')
        decoded = proto._decode_tool_call(proto.ProtoDecoder(enc.finish()))
        self.assertEqual(decoded["arguments_json"], '{"host":"edge"}')
        self.assertEqual(decoded["invalid_json"], '{"host":"edge"}')

    def test_response_frame_carries_tool_call_on_field_6(self):
        call = proto.ProtoEncoder()
        call.string(1, "call_host")
        call.string(2, "run_shell")
        call.string(4, '{"host":"edge","command":"ls"}')
        outer = proto.ProtoEncoder()
        outer.uint32(5, proto.STOP_FUNCTION_CALL)
        outer.message(6, lambda e: e._buf.extend(call.finish()))
        msg = proto.decode_get_chat_message(outer.finish())
        self.assertEqual(msg["stop_reason"], proto.STOP_FUNCTION_CALL)
        self.assertEqual(len(msg["tool_calls"]), 1)
        self.assertEqual(msg["tool_calls"][0]["name"], "run_shell")
        self.assertIn("host", json.loads(msg["tool_calls"][0]["arguments_json"]))

    def test_naive_new_index_per_frame_drops_host(self):
        """The pre-fix bug: each protobuf frame became OpenAI index 0, 1, 2."""
        bucket: dict = {}
        for i, tc in enumerate(DEVIN_HOST_FRAMES):
            _take_calls(
                bucket,
                [
                    {
                        "index": i,
                        "id": tc["id"],
                        "function": {
                            "name": tc["name"],
                            "arguments": tc["invalid_json"],
                        },
                    }
                ],
            )
        hosts = []
        for rec in bucket.values():
            try:
                hosts.append(json.loads(rec["function"]["arguments"]).get("host"))
            except json.JSONDecodeError:
                hosts.append(None)
        self.assertTrue(any(h is None for h in hosts) or "edge" not in hosts)
        self.assertNotEqual(len(bucket), 1)

    def test_merged_frames_keep_host_for_runtime(self):
        bucket = _runtime_from_devin_frames(DEVIN_HOST_FRAMES)
        self.assertEqual(len(bucket), 1)
        args = _args(bucket)
        self.assertEqual(args["host"], "edge")
        self.assertEqual(args["command"], "uname -a")

    def test_sonnet_unique_ids_do_not_emit_empty_host_call(self):
        """Sonnet 5 via Cascade uses the same id-split as Opus."""
        frames = (
            {"id": "toolu_sonnet_a", "name": "run_terminal_command", "arguments_json": ""},
            {
                "id": "toolu_sonnet_b",
                "name": "run_terminal_command",
                "invalid_json": '{"command":"pwd"',
            },
            {
                "id": "toolu_sonnet_c",
                "name": "run_terminal_command",
                "invalid_json": ',"host":"edge"}',
            },
        )
        bucket = _runtime_from_devin_frames(frames)
        self.assertEqual(len(bucket), 1)
        args = _args(bucket)
        self.assertEqual(args.get("host"), "edge")
        self.assertEqual(args.get("command"), "pwd")
        from app.workspace import _args as parse_ws

        parsed = parse_ws(bucket[0]["function"]["arguments"])
        self.assertTrue(parsed.get("host"))

    def test_opus_unique_ids_do_not_emit_empty_host_call(self):
        """Opus/Cascade often names the call on id A and streams JSON on id B.

        The empty first call used to execute as `{}` and raise
        `host is required unless a project is bound` on every command.
        """
        frames = (
            {"id": "toolu_a", "name": "run_terminal_command", "arguments_json": ""},
            {
                "id": "toolu_b",
                "name": "run_terminal_command",
                "invalid_json": '{"command":"ls"',
            },
            {
                "id": "toolu_c",
                "name": "run_terminal_command",
                "invalid_json": ',"host":"edge"}',
            },
        )
        bucket = _runtime_from_devin_frames(frames)
        self.assertEqual(len(bucket), 1)
        args = _args(bucket)
        self.assertEqual(args.get("host"), "edge")
        self.assertEqual(args.get("command"), "ls")
        from app.workspace import _args as parse_ws

        parsed = parse_ws(bucket[0]["function"]["arguments"])
        self.assertTrue(parsed.get("host"))

    def test_parallel_complete_calls_do_not_merge(self):
        book = devin_mod._ToolBook()
        book.add(
            {
                "id": "a",
                "name": "run_terminal_command",
                "arguments_json": '{"host":"one","command":"a"}',
            }
        )
        book.add(
            {
                "id": "b",
                "name": "run_terminal_command",
                "arguments_json": '{"host":"two","command":"b"}',
            }
        )
        calls = book.openai_calls()
        self.assertEqual(len(calls), 2)
        self.assertEqual(json.loads(calls[0]["function"]["arguments"])["host"], "one")
        self.assertEqual(json.loads(calls[1]["function"]["arguments"])["host"], "two")

    def test_encode_request_sends_tools_and_history(self):
        raw = proto.encode_get_chat_message(
            {
                "metadata": {"api_key": "k", "ide_name": "windsurf"},
                "prompt": "sys",
                "prompts": [
                    {
                        "message_id": "a1",
                        "source": proto.CHAT_SOURCE_SYSTEM,
                        "prompt": "",
                        "tool_calls": [
                            {
                                "id": "call_host",
                                "name": "run_shell",
                                "arguments_json": '{"host":"edge","command":"ls"}',
                                "is_custom_tool_call": True,
                            }
                        ],
                    },
                    {
                        "message_id": "t1",
                        "source": proto.CHAT_SOURCE_TOOL,
                        "prompt": "ok",
                        "tool_call_id": "call_host",
                    },
                ],
                "model_uid": "claude-opus-5-medium",
                "tools": [
                    {
                        "name": "run_shell",
                        "description": "run",
                        "json_schema": json.dumps(HOST_TOOL["function"]["parameters"]),
                    }
                ],
                "cascade_id": "cid",
                "execution_id": "eid",
            }
        )
        self.assertIn(b"run_shell", raw)
        self.assertIn(b"call_host", raw)
        self.assertIn(b"edge", raw)
        self.assertIn(b"host", raw)

    def test_messages_to_prompts_keeps_tool_pair(self):
        system, prompts = devin_mod._messages_to_prompts(
            [
                {"role": "system", "content": "sys"},
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "call_host",
                            "type": "function",
                            "function": {
                                "name": "run_shell",
                                "arguments": '{"host":"edge","command":"ls"}',
                            },
                        }
                    ],
                },
                {"role": "tool", "tool_call_id": "call_host", "content": "Linux"},
            ],
            "cid",
        )
        self.assertEqual(system, "sys")
        self.assertEqual(prompts[0]["tool_calls"][0]["arguments_json"], '{"host":"edge","command":"ls"}')
        self.assertTrue(prompts[0]["tool_calls"][0]["is_custom_tool_call"])
        self.assertEqual(prompts[1]["source"], proto.CHAT_SOURCE_TOOL)
        self.assertEqual(prompts[1]["tool_call_id"], "call_host")


class OpenAICompatContractTest(unittest.TestCase):
    """Grok, Z.AI, and generic OpenAI-compat already speak Chat Completions."""

    def test_same_index_deltas_keep_host(self):
        bucket = _runtime_from_openai_deltas(
            [
                {
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "c1",
                            "function": {"name": "run_shell", "arguments": '{"host":'},
                        }
                    ]
                },
                {"tool_calls": [{"index": 0, "function": {"arguments": '"edge","command":"ls"}'}}]},
            ]
        )
        self.assertEqual(_args(bucket)["host"], "edge")
        self.assertEqual(_args(bucket)["command"], "ls")

    def test_complete_json_resend_does_not_duplicate_host(self):
        bucket = _runtime_from_openai_deltas(
            [
                {
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "c1",
                            "function": {"name": "run_shell", "arguments": '{"host":"edge"}'},
                        }
                    ]
                },
                {
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "c1",
                            "function": {
                                "name": "run_shell",
                                "arguments": '{"host":"edge","command":"ls"}',
                            },
                        }
                    ]
                },
            ]
        )
        args = _args(bucket)
        self.assertEqual(args["host"], "edge")
        self.assertEqual(args["command"], "ls")

    def test_zai_and_grok_payloads_keep_tools(self):
        body = {
            "model": "glm-5.3",
            "messages": [{"role": "user", "content": "hi"}],
            "tools": [HOST_TOOL],
            "tool_choice": "auto",
            "prompt_cache_key": "drop-me",
        }
        zai = zai_mod._chat_payload(body)
        self.assertEqual(zai["tools"], [HOST_TOOL])
        self.assertNotIn("prompt_cache_key", zai)
        grok = drop_cache_routing_fields(body)
        self.assertEqual(grok["tools"], [HOST_TOOL])
        self.assertNotIn("prompt_cache_key", grok)

    def test_ollama_native_calls_become_openai(self):
        mapped = ollama_mod.to_openai_tools(
            [
                {
                    "function": {
                        "name": "run_shell",
                        "arguments": {"host": "edge", "command": "ls"},
                    }
                }
            ],
            include_index=True,
        )
        self.assertEqual(mapped[0]["index"], 0)
        bucket = _runtime_from_openai_deltas([{"tool_calls": mapped}])
        self.assertEqual(_args(bucket)["host"], "edge")


class ChatgptResponsesContractTest(unittest.TestCase):
    def test_argument_deltas_keep_host(self):
        tr = chatgpt_mod._Translator("gpt-5.4")
        tr._handle(
            {
                "type": "response.output_item.added",
                "item": {
                    "id": "item1",
                    "type": "function_call",
                    "call_id": "c1",
                    "name": "run_shell",
                    "arguments": "",
                },
            }
        )
        tr._handle(
            {
                "type": "response.function_call_arguments.delta",
                "item_id": "item1",
                "delta": '{"host":"edge"',
            }
        )
        tr._handle(
            {
                "type": "response.function_call_arguments.delta",
                "item_id": "item1",
                "delta": ',"command":"ls"}',
            }
        )
        tr._handle(
            {
                "type": "response.function_call_arguments.done",
                "item_id": "item1",
                "arguments": '{"host":"edge","command":"ls"}',
            }
        )
        calls = tr.book.tool_calls()
        self.assertEqual(len(calls), 1)
        args = json.loads(calls[0]["function"]["arguments"])
        self.assertEqual(args["host"], "edge")
        self.assertEqual(args["command"], "ls")

    def test_translator_chunks_are_index_stable_for_runtime(self):
        tr = chatgpt_mod._Translator("gpt-5.4")
        chunks = []
        for ev in (
            {
                "type": "response.output_item.added",
                "item": {
                    "id": "item1",
                    "type": "function_call",
                    "call_id": "c1",
                    "name": "run_shell",
                    "arguments": "",
                },
            },
            {
                "type": "response.function_call_arguments.delta",
                "item_id": "item1",
                "delta": '{"host":"edge","command":"ls"}',
            },
        ):
            raw = tr._handle(ev)
            if not raw or raw in (b"__FINAL__", b"__ERROR__"):
                continue
            line = raw.decode().split("data:", 1)[-1].strip()
            obj = json.loads(line)
            chunks.append(obj["choices"][0]["delta"])
        bucket = _runtime_from_openai_deltas(chunks)
        self.assertEqual(_args(bucket)["host"], "edge")


if __name__ == "__main__":
    unittest.main()
