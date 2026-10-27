"""Devin Cascade pool: proto codecs, ids, and routing. No live Cognition calls."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_TMP = tempfile.mkdtemp(prefix="fuse-devin-")
os.environ.setdefault("DATABASE_PATH", str(Path(_TMP) / "devin.db"))
os.environ.setdefault("APP_SECRET", "x" * 32)

from app import db  # noqa: E402
from app.sources import devin as devin_mod  # noqa: E402
from app.sources import devin_catalog  # noqa: E402
from app.sources import devin_proto as proto  # noqa: E402
from app.sources import devin_stream  # noqa: E402
from app.sources.devin import (  # noqa: E402
    canonical_fuse_id,
    canonical_upstream,
    catalog_ids_from_records,
    efforts_for,
    fuse_id,
    is_devin_model,
    is_internal_uid,
    normalize_token,
    resolve_wire_uid,
    upstream_id,
)
from app.sources.devin import DevinSource  # noqa: E402

import pytest

# Uses the database: a disposable MariaDB schema shared by this module.
pytestmark = pytest.mark.usefixtures("isolated_db")

db._conn = None


class PkceLoginTest(unittest.TestCase):
    def test_start_cli_login_url(self):
        data = devin_mod.start_cli_login()
        self.assertTrue(data["auth_url"].startswith("https://app.devin.ai/auth/cli/continue?"))
        self.assertIn("code_challenge_method=S256", data["auth_url"])
        self.assertIn("prompt=select_account", data["auth_url"])
        self.assertNotIn("redirect_uri=", data["auth_url"])
        self.assertEqual(len(data["verifier"]), 43)
        self.assertEqual(len(data["challenge"]), 43)
        self.assertTrue(data["state"])

    def test_token_from_exchange_shapes(self):
        self.assertEqual(devin_mod._token_from_exchange({"token": "abc"}), "abc")
        self.assertEqual(devin_mod._token_from_exchange({"api_key": "k"}), "k")
        self.assertEqual(devin_mod._token_from_exchange({"data": {"session_token": "s"}}), "s")
        self.assertEqual(devin_mod._token_from_exchange({}), "")

    def test_exchange_cli_code_reads_token(self):
        class _Resp:
            status_code = 200

            def json(self):
                return {"token": "devin-session-token$ok"}

        class _Client:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def post(self, *args, **kwargs):
                return _Resp()

        with unittest.mock.patch("app.sources.devin.httpx.AsyncClient", return_value=_Client()):
            token, err = asyncio.run(devin_mod.exchange_cli_code("code", "verifier", "https://example/callback"))
        self.assertEqual(token, "devin-session-token$ok")
        self.assertEqual(err, "")


def _clear_catalog():
    devin_catalog._model_wire_uids = {}
    devin_catalog._model_plain_uids = {}
    devin_catalog._model_efforts = {}
    devin_catalog._model_labels = {}
    devin_catalog._model_promo_until = {}
    devin_catalog._models_cache = None
    devin_mod.reset_sku_health()


class JwtOutageTest(unittest.TestCase):
    def test_only_an_auth_rejection_reads_as_401(self):
        # A 401 strikes (and can disable) the donated account; an outage must not.
        src = DevinSource.__new__(DevinSource)
        for err, status in (("Devin auth http 503", 502), ("Devin auth returned an unreadable response.", 502),
                            (devin_mod._JWT_REJECTED, 401)):
            with unittest.mock.patch.object(devin_mod, "get_user_jwt", unittest.mock.AsyncMock(return_value=(None, err))):
                result = asyncio.run(src._chat_one("tok", {}, "m", {"id": 1}, "s", []))
            self.assertEqual(result.status, status, err)


class TokenAndIdTest(unittest.TestCase):
    def setUp(self):
        _clear_catalog()

    def test_normalize_adds_prefix(self):
        self.assertTrue(normalize_token("abc123").startswith("devin-session-token$"))
        raw = "devin-session-token$abc"
        self.assertEqual(normalize_token(raw), raw)

    def test_fuse_and_upstream_ids(self):
        self.assertEqual(fuse_id("claude-fable-5-1"), "devin/claude-fable-5-1")
        self.assertEqual(fuse_id("devin/swe-2"), "devin/swe-2")
        self.assertEqual(upstream_id("devin/claude-fable-5-1"), "claude-fable-5-1")
        self.assertEqual(upstream_id("swe-2"), "swe-2")

    def test_is_devin_model_prefix_only(self):
        self.assertTrue(is_devin_model("devin/claude-opus-5"))
        self.assertTrue(is_devin_model("devin/gpt-6-astra"))
        self.assertTrue(is_devin_model("devin/claude-sonnet-5"))
        self.assertFalse(is_devin_model("gpt-6-astra"))
        self.assertFalse(is_devin_model("claude-opus-5"))
        self.assertFalse(is_devin_model("openrouter/openai/gpt-6-astra"))
        self.assertFalse(is_devin_model("devin-session-token$abc"))
        self.assertFalse(is_devin_model(""))

    def test_canonical_strips_effort_not_sku_fast(self):
        self.assertEqual(canonical_upstream("devin/claude-opus-5-medium"), "claude-opus-5")
        self.assertEqual(canonical_upstream("claude-opus-5-medium-fast"), "claude-opus-5")
        self.assertEqual(canonical_upstream("gpt-6-astra-high"), "gpt-6-astra")
        self.assertEqual(canonical_upstream("swe-1-6-fast"), "swe-1-6-fast")
        self.assertEqual(canonical_fuse_id("claude-sonnet-5-high"), "devin/claude-sonnet-5")
        self.assertEqual(canonical_upstream("devin/gpt-5.6-sol"), "gpt-5-6-sol")
        self.assertEqual(canonical_fuse_id("gpt-5.6-sol-medium"), "devin/gpt-5-6-sol")
        self.assertEqual(canonical_upstream("deepseek-v4-1-flash-high"), "deepseek-v4-1-flash")
        self.assertEqual(canonical_upstream("glm-5-2-max-1m"), "glm-5-2-1m")
        self.assertEqual(canonical_upstream("glm-5-2-1m"), "glm-5-2-1m")
        self.assertEqual(canonical_upstream("gpt-5-4-high-priority"), "gpt-5-4")

    def test_resolve_wire_uid_appends_effort(self):
        self.assertEqual(resolve_wire_uid("devin/claude-opus-5", "medium"), "claude-opus-5-medium")
        self.assertEqual(resolve_wire_uid("devin/claude-sonnet-5", "high"), "claude-sonnet-5-high")
        self.assertEqual(resolve_wire_uid("devin/gpt-6-astra", "medium"), "gpt-6-astra-medium")
        self.assertEqual(resolve_wire_uid("devin/claude-fable-5-1", None), "claude-fable-5-1-medium")
        self.assertEqual(resolve_wire_uid("claude-opus-5-high", "low"), "claude-opus-5-high")
        self.assertEqual(resolve_wire_uid("devin/claude-opus-5-medium-fast", "high"), "claude-opus-5-medium-fast")
        self.assertEqual(resolve_wire_uid("devin/swe-2", "medium"), "swe-2-high")
        self.assertEqual(resolve_wire_uid("devin/swe-1-6-fast", "low"), "swe-1-6-fast-low")
        self.assertEqual(resolve_wire_uid("devin/deepseek-v4-1-flash", "high"), "deepseek-v4-1-flash-high")
        self.assertEqual(resolve_wire_uid("devin/deepseek-v4-1-flash", "max"), "deepseek-v4-1-flash-max")


class ToolMergeTest(unittest.TestCase):
    def test_invalid_json_str_is_used_as_arguments(self):
        enc = proto.ProtoEncoder()
        enc.string(1, "call_1")
        enc.string(2, "run_shell")
        enc.string(4, '{"host":"edge","command":"ls"}')
        decoded = proto._decode_tool_call(proto.ProtoDecoder(enc.finish()))
        self.assertEqual(decoded["name"], "run_shell")
        self.assertEqual(decoded["arguments_json"], '{"host":"edge","command":"ls"}')

    def test_book_merges_split_host_json(self):
        book = devin_mod._ToolBook()
        book.add({"id": "c1", "name": "run_shell", "arguments_json": '{"host":'})
        book.add({"id": "c1", "name": "run_shell", "invalid_json": '"edge","command":"uname -a"}'})
        calls = book.openai_calls()
        self.assertEqual(len(calls), 1)
        args = json.loads(calls[0]["function"]["arguments"])
        self.assertEqual(args["host"], "edge")
        self.assertEqual(args["command"], "uname -a")

    def test_book_merges_name_only_then_other_id(self):
        book = devin_mod._ToolBook()
        book.add({"id": "toolu_a", "name": "run_terminal_command", "arguments_json": ""})
        book.add(
            {
                "id": "toolu_b",
                "name": "run_terminal_command",
                "invalid_json": '{"host":"edge","command":"ls"}',
            }
        )
        calls = book.openai_calls()
        self.assertEqual(len(calls), 1)
        self.assertEqual(json.loads(calls[0]["function"]["arguments"])["host"], "edge")

    def test_book_snapshot_does_not_duplicate(self):
        book = devin_mod._ToolBook()
        book.add({"id": "c1", "name": "run_shell", "arguments_json": '{"host":"a"}'})
        book.add({"id": "c1", "arguments_json": '{"host":"a","command":"ls"}'})
        calls = book.openai_calls()
        self.assertEqual(len(calls), 1)
        self.assertEqual(json.loads(calls[0]["function"]["arguments"])["command"], "ls")

    def test_function_call_stop_reason(self):
        enc = proto.ProtoEncoder()
        enc.uint32(5, proto.STOP_FUNCTION_CALL)
        decoded = proto.decode_get_chat_message(enc.finish())
        self.assertEqual(decoded["stop_reason"], 10)

    def test_roundtrip_tool_call_fields(self):
        inner = proto.ProtoEncoder()
        proto._encode_tool_call(
            inner,
            {
                "id": "call_host",
                "name": "run_shell",
                "arguments_json": '{"host":"edge"}',
                "is_custom_tool_call": True,
            },
        )
        decoded = proto._decode_tool_call(proto.ProtoDecoder(inner.finish()))
        self.assertEqual(decoded["id"], "call_host")
        self.assertEqual(decoded["name"], "run_shell")
        self.assertEqual(json.loads(decoded["arguments_json"])["host"], "edge")
        self.assertTrue(decoded["is_custom_tool_call"])


class ProtoTest(unittest.TestCase):
    def test_jwt_roundtrip(self):
        enc = proto.ProtoEncoder()
        enc.string(1, "jwt-value")
        enc.string(2, "https://server.codeium.com")
        decoded = proto.decode_get_user_jwt(enc.finish())
        self.assertEqual(decoded["user_jwt"], "jwt-value")
        self.assertEqual(decoded["custom_api_server_url"], "https://server.codeium.com")

    def test_meta_cli_identity_encodes(self):
        raw = proto.encode_get_chat_message(
            {
                "metadata": devin_mod._meta("devin-session-token$x"),
                "prompt": "sys",
                "prompts": [{"message_id": "m1", "source": proto.CHAT_SOURCE_USER, "prompt": "Hi"}],
                "model_uid": resolve_wire_uid("devin/claude-sonnet-5", "medium"),
                "cascade_id": "c",
                "execution_id": "e",
            }
        )
        self.assertIn(b"claude-sonnet-5-medium", raw)
        self.assertIn(b"devin", raw)
        self.assertIn(b"cli", raw)
        self.assertNotIn(b"windsurf", raw)

    def test_chat_meta_carries_agent_name_gate_field(self):
        # Metadata field 28 ("chisel") is what the live CLI sends; the chat
        # backend version-gates newer Cascade SKUs without it.
        enc = proto.ProtoEncoder()
        proto.encode_metadata(enc, devin_mod._meta("devin-session-token$x"))
        raw = enc.finish()
        d = proto.ProtoDecoder(raw)
        saw_28 = False
        while not d.done:
            f, w = d.tag()
            if f == 28 and w == 2:
                self.assertEqual(d.string(), "chisel")
                saw_28 = True
            else:
                d.skip(w)
        self.assertTrue(saw_28)
        # The windsurf catalog identity must keep its own shape (no field 28).
        enc2 = proto.ProtoEncoder()
        proto.encode_metadata(enc2, devin_mod._catalog_meta("devin-session-token$x"))
        d2 = proto.ProtoDecoder(enc2.finish())
        while not d2.done:
            f, w = d2.tag()
            self.assertNotEqual(f, 28)
            d2.skip(w)

    def test_chat_request_encodes(self):
        raw = proto.encode_get_chat_message(
            {
                "metadata": {"api_key": "devin-session-token$x", "ide_name": "windsurf"},
                "prompt": "You are helpful.",
                "prompts": [
                    {
                        "message_id": "m1",
                        "source": proto.CHAT_SOURCE_USER,
                        "prompt": "Hello",
                    }
                ],
                "model_uid": "claude-fable-5-1",
                "configuration": {
                    "num_completions": 1,
                    "max_tokens": 128,
                    "temperature": 0.4,
                    "first_temperature": 0.4,
                    "top_p": 1,
                    "stop_patterns": ["\n\nUSER:"],
                    "fim_eot": 1,
                },
                "cascade_id": "cascade",
                "execution_id": "exec",
            }
        )
        self.assertGreater(len(raw), 20)
        self.assertIn(b"claude-fable-5-1", raw)
        self.assertIn(b"Hello", raw)

    def test_chat_response_decode(self):
        enc = proto.ProtoEncoder()
        enc.string(1, "msg-1")
        enc.string(3, "Hi there")
        enc.uint32(5, 0)
        usage = proto.ProtoEncoder()
        usage.uint64(2, 11)
        usage.uint64(3, 4)
        enc.message(7, lambda e: e._buf.extend(usage.finish()) if False else (
            e.uint64(2, 11),
            e.uint64(3, 4),
        ))
        decoded = proto.decode_get_chat_message(enc.finish())
        self.assertEqual(decoded["delta_text"], "Hi there")
        self.assertEqual(decoded["usage"]["prompt_tokens"], 11)
        self.assertEqual(decoded["usage"]["completion_tokens"], 4)

    def test_connect_frame_roundtrip(self):
        payload = b"hello-proto"
        frame = proto.connect_frame(payload)
        frames, rest = proto.iter_connect_frames(frame)
        self.assertEqual(rest, b"")
        self.assertEqual(len(frames), 1)
        self.assertEqual(frames[0][1], payload)

    def test_cli_model_configs(self):
        rec = proto.ProtoEncoder()
        rec.string(1, "Claude Fable 5.1")
        rec.string(22, "claude-fable-5-1")
        rec.uint64(18, 64000)
        outer = proto.ProtoEncoder()
        outer.message(1, lambda e: e._buf.extend(rec.finish()))
        models = proto.decode_cli_model_configs(outer.finish())
        self.assertEqual(models[0]["id"], "claude-fable-5-1")
        self.assertEqual(models[0]["name"], "Claude Fable 5.1")

    def test_messages_to_prompts_splits_system(self):
        system, prompts = devin_mod._messages_to_prompts(
            [
                {"role": "system", "content": "Be brief."},
                {"role": "user", "content": "Hi"},
                {"role": "assistant", "content": "Hello"},
            ],
            "cid",
        )
        self.assertEqual(system, "Be brief.")
        self.assertEqual(prompts[0]["source"], proto.CHAT_SOURCE_USER)
        self.assertEqual(prompts[0]["prompt"], "Hi")
        self.assertEqual(prompts[1]["source"], proto.CHAT_SOURCE_SYSTEM)
        self.assertEqual(prompts[1]["prompt"], "Hello")


class AccessAndPickTest(unittest.TestCase):
    def setUp(self):
        from app.sources.chatgpt import ensure_chatgpt_source
        from app.sources.devin import ensure_devin_source

        ensure_devin_source()
        ensure_chatgpt_source()

    def test_probe_rejects_empty(self):
        info, err = asyncio.run(devin_mod.probe_token(""))
        self.assertIsNone(info)
        self.assertTrue(err)

    def test_pick_routes_prefixed_id(self):
        from app.sources import pick

        picked = asyncio.run(pick("devin/claude-sonnet-5", allow_devin=True))
        self.assertIsNotNone(picked)
        src, upstream = picked
        self.assertEqual(src.type_id, "devin")
        self.assertEqual(upstream, "claude-sonnet-5")

    def test_pick_hides_without_allow(self):
        from app.sources import pick

        picked = asyncio.run(pick("devin/claude-sonnet-5", allow_devin=False))
        self.assertIsNone(picked)

    def test_pick_opus_stays_on_devin(self):
        from app.sources import pick

        picked = asyncio.run(pick("devin/claude-opus-5", allow_devin=True, allow_chatgpt=True))
        self.assertIsNotNone(picked)
        src, upstream = picked
        self.assertEqual(src.type_id, "devin")
        self.assertEqual(upstream, "claude-opus-5")

    def test_pick_devin_gpts_stay_on_devin(self):
        from app.sources import pick

        for model in ("devin/gpt-5.4", "devin/gpt-5-4-mini", "devin/gpt-5-4"):
            picked = asyncio.run(pick(model, allow_devin=True, allow_chatgpt=True))
            self.assertIsNotNone(picked, model)
            src, upstream = picked
            self.assertEqual(src.type_id, "devin", model)
            self.assertFalse(upstream.startswith("devin/"), model)
            self.assertTrue(upstream.startswith("gpt-"), model)

    def test_pick_unprefixed_gpt_stays_chatgpt(self):
        from app.sources import pick

        picked = asyncio.run(pick("gpt-6-astra", allow_devin=True, allow_chatgpt=True))
        self.assertIsNotNone(picked)
        src, upstream = picked
        self.assertEqual(src.type_id, "chatgpt")
        self.assertEqual(upstream, "gpt-6-astra")

    def test_pick_devin_gpt_does_not_fall_through_when_denied(self):
        from app.sources import pick

        picked = asyncio.run(pick("devin/gpt-5.4", allow_devin=False, allow_chatgpt=True))
        self.assertIsNone(picked)

    def test_meta_identifies_as_cli(self):
        meta = devin_mod._meta("tok")
        self.assertEqual(meta["ide_name"], "devin")
        self.assertEqual(meta["extension_name"], "cli")
        self.assertNotEqual(meta["ide_name"], "windsurf")
        self.assertNotEqual(meta["extension_name"], "windsurf")

    def test_catalog_meta_is_windsurf(self):
        meta = devin_mod._catalog_meta("tok")
        self.assertEqual(meta["ide_name"], "windsurf")
        self.assertEqual(meta["extension_name"], "windsurf")
        self.assertNotEqual(meta["ide_name"], "devin")

    def test_pick_keeps_catalog_variant(self):
        from app.sources import pick

        picked = asyncio.run(pick("devin/claude-opus-5-high", allow_devin=True))
        self.assertIsNotNone(picked)
        src, upstream = picked
        self.assertEqual(src.type_id, "devin")
        self.assertEqual(upstream, "claude-opus-5-high")

    def test_catalog_collapses_effort_variants(self):
        names, labels = catalog_ids_from_records(
            [
                {"id": "MODEL_CHAT_GPT_4_1_2025_04_14", "name": "GPT-4.1"},
                {"id": "claude-opus-5-medium", "name": "Claude Opus 5 Medium"},
                {"id": "claude-sonnet-5-medium", "name": "Claude Sonnet 5 Medium"},
                {"id": "gpt-6-astra-medium", "name": "GPT-6 Astra Medium Thinking"},
                {"id": "claude-opus-5-high", "name": "Claude Opus 5 High"},
            ]
        )
        self.assertIn("devin/gpt-4-1", names)
        self.assertIn("devin/claude-opus-5", names)
        self.assertIn("devin/claude-sonnet-5", names)
        self.assertIn("devin/gpt-6-astra", names)
        self.assertNotIn("devin/claude-opus-5-medium", names)
        self.assertNotIn("devin/claude-opus-5-high", names)
        self.assertNotIn("devin/MODEL_CHAT_GPT_4_1_2025_04_14", names)
        self.assertEqual(labels["devin/claude-opus-5"], "Claude Opus 5")
        self.assertEqual(labels["devin/gpt-6-astra"], "GPT-6 Astra")
        self.assertEqual(labels["devin/gpt-4-1"], "GPT-4.1")
        self.assertLess(names.index("devin/claude-opus-5"), names.index("devin/gpt-6-astra"))
        self.assertEqual(efforts_for("devin/claude-opus-5"), ("medium", "high"))
        self.assertEqual(efforts_for("devin/gpt-6-astra"), ("medium",))
        self.assertEqual(efforts_for("devin/gpt-4-1"), ())
        self.assertEqual(resolve_wire_uid("devin/gpt-4-1", None), "MODEL_CHAT_GPT_4_1_2025_04_14")

    def test_empty_catalog_falls_back_to_seeded(self):
        names, _labels = catalog_ids_from_records([])
        self.assertIn("devin/claude-opus-5", names)
        self.assertIn("devin/claude-sonnet-5", names)
        self.assertIn("devin/gpt-5-4", names)
        self.assertIn("devin/swe-2", names)
        self.assertEqual(efforts_for("devin/claude-sonnet-5"), ("low", "medium", "high"))

    def test_seed_list_only_contains_served_skus(self):
        # claude-fable-5-1, gpt-6-astra, gpt-5-5, and gpt-5-6-{sol,terra,luna}
        # were retired by Cognition (absent from the account's CLI model list
        # AND the Windsurf catalog as of 2026-09-15). Seeding them offered
        # models the backend no longer routes.
        names, _labels = catalog_ids_from_records([])
        for retired in (
            "devin/claude-fable-5-1",
            "devin/gpt-6-astra",
            "devin/gpt-5-5",
            "devin/gpt-5-6-sol",
            "devin/gpt-5-6-terra",
            "devin/gpt-5-6-luna",
        ):
            self.assertNotIn(retired, names)

    def test_catalog_keeps_seeded_local_skus(self):
        names, _labels = catalog_ids_from_records(
            [{"id": "swe-2-high", "name": "SWE-2 High"}]
        )
        self.assertIn("devin/swe-2", names)
        self.assertIn("devin/gpt-5-4-mini", names)
        self.assertIn("devin/swe-1-6-fast", names)
        self.assertEqual(resolve_wire_uid("devin/gpt-5-4", "medium"), "gpt-5-4-medium")
        names, labels = catalog_ids_from_records([])
        self.assertEqual(labels["devin/gpt-5-4"], "GPT 5.4")
        self.assertEqual(labels["devin/gpt-5-4-mini"], "GPT 5.4 Mini")
        self.assertEqual(labels["devin/swe-2"], "SWE-2")
        self.assertEqual(labels["devin/swe-1-6-fast"], "SWE-1.6 Fast")
        self.assertEqual(labels["devin/claude-opus-5"], "Claude Opus 5")

    def test_catalog_collapses_internal_thinking_skus(self):
        names, labels = catalog_ids_from_records(
            [
                {"id": "MODEL_GPT_5_2_NONE", "name": "GPT-5.2 No Thinking"},
                {"id": "MODEL_GPT_5_2_LOW", "name": "GPT-5.2 Low Thinking"},
                {"id": "MODEL_GPT_5_2_MEDIUM", "name": "GPT-5.2 Medium Thinking"},
                {"id": "MODEL_GPT_5_2_HIGH", "name": "GPT-5.2 High Thinking"},
                {"id": "MODEL_GPT_5_2_XHIGH", "name": "GPT-5.2 XHigh Thinking"},
                {"id": "MODEL_PRIVATE_12", "name": "GPT-5.1 No Thinking"},
                {"id": "MODEL_PRIVATE_13", "name": "GPT-5.1 Low Thinking"},
                {"id": "MODEL_PRIVATE_14", "name": "GPT-5.1 Medium Thinking"},
                {"id": "MODEL_GOOGLE_GEMINI_3_0_FLASH_MINIMAL", "name": "Gemini 3 Flash Minimal"},
                {"id": "MODEL_GOOGLE_GEMINI_3_0_FLASH_HIGH", "name": "Gemini 3 Flash High"},
            ]
        )
        self.assertIn("devin/gpt-5-2", names)
        self.assertIn("devin/gpt-5-1", names)
        self.assertIn("devin/gemini-3-flash", names)
        self.assertNotIn("devin/MODEL_GPT_5_2_HIGH", names)
        self.assertNotIn("devin/MODEL_PRIVATE_12", names)
        self.assertEqual(labels["devin/gpt-5-2"], "GPT-5.2")
        self.assertEqual(efforts_for("devin/gpt-5-2"), ("none", "low", "medium", "high", "xhigh"))
        self.assertEqual(efforts_for("devin/gpt-5-1"), ("none", "low", "medium"))
        self.assertEqual(efforts_for("devin/gemini-3-flash"), ("minimal", "high"))
        self.assertEqual(resolve_wire_uid("devin/gpt-5-2", "high"), "MODEL_GPT_5_2_HIGH")
        self.assertEqual(resolve_wire_uid("devin/gpt-5-2", "none"), "MODEL_GPT_5_2_NONE")
        self.assertEqual(resolve_wire_uid("devin/gpt-5-1", "medium"), "MODEL_PRIVATE_14")
        self.assertEqual(resolve_wire_uid("devin/gemini-3-flash", "minimal"), "MODEL_GOOGLE_GEMINI_3_0_FLASH_MINIMAL")

    def test_catalog_uses_exact_wire_uids(self):
        names, labels = catalog_ids_from_records(
            [
                {"id": "deepseek-v4-1-flash-high", "name": "DeepSeek V4.1 Flash High"},
                {"id": "deepseek-v4-1-flash-max", "name": "DeepSeek V4.1 Flash Max"},
                {"id": "deepseek-v4-flash-high", "name": "DeepSeek V4 Flash High"},
                {"id": "deepseek-v4-flash-max", "name": "DeepSeek V4 Flash Max"},
                {"id": "glm-5-2", "name": "GLM-5.2 High"},
                {"id": "glm-5-2-max", "name": "GLM-5.2 Max"},
                {"id": "glm-5-2-none", "name": "GLM-5.2 No Thinking"},
                {"id": "glm-5-2-1m", "name": "GLM-5.2 High 1M"},
                {"id": "glm-5-2-max-1m", "name": "GLM-5.2 Max 1M"},
                {"id": "glm-5-2-none-1m", "name": "GLM-5.2 No Thinking 1M"},
                {"id": "gemini-3-5-flash-minimal", "name": "Gemini 3.5 Flash Minimal"},
                {"id": "gemini-3-5-flash-low", "name": "Gemini 3.5 Flash Low"},
                {"id": "gemini-3-5-flash-medium", "name": "Gemini 3.5 Flash Medium"},
                {"id": "gemini-3-5-flash-high", "name": "Gemini 3.5 Flash High"},
                {"id": "kimi-k3-high", "name": "Kimi K3 High"},
                {"id": "kimi-k3-low", "name": "Kimi K3 Low"},
                {"id": "kimi-k3-max", "name": "Kimi K3 Max"},
                {"id": "swe-2-high", "name": "SWE-2 High"},
                {"id": "swe-2-medium", "name": "SWE-2 Medium"},
                {"id": "swe-2-max", "name": "SWE-2 Max"},
                {"id": "swe-1-7", "name": "SWE-1.7 Max"},
                {"id": "swe-1-7-medium", "name": "SWE-1.7 Medium"},
                {"id": "claude-opus-4-6", "name": "Claude Opus 4.6"},
                {"id": "claude-opus-4-6-thinking", "name": "Claude Opus 4.6 Thinking"},
                {"id": "swe-1-6", "name": "SWE-1.6"},
                {"id": "gpt-5-4-none", "name": "GPT-5.4 No Thinking"},
                {"id": "gpt-5-4-medium", "name": "GPT-5.4 Medium Thinking"},
                {"id": "claude-opus-5-medium-fast", "name": "Claude Opus 5 Medium Fast"},
                {"id": "claude-opus-5-medium", "name": "Claude Opus 5 Medium"},
                {"id": "claude-opus-5-high", "name": "Claude Opus 5 High"},
            ]
        )
        self.assertIn("devin/deepseek-v4-1-flash", names)
        self.assertIn("devin/glm-5-2", names)
        self.assertIn("devin/glm-5-2-1m", names)
        self.assertNotIn("devin/deepseek-v4-1-flash-high", names)
        self.assertEqual(labels["devin/deepseek-v4-1-flash"], "DeepSeek V4.1 Flash")
        self.assertEqual(labels["devin/glm-5-2"], "GLM-5.2")
        self.assertEqual(labels["devin/glm-5-2-1m"], "GLM-5.2 1M")
        self.assertEqual(efforts_for("devin/deepseek-v4-1-flash"), ("high", "max"))
        self.assertEqual(efforts_for("devin/glm-5-2"), ("none", "high", "max"))
        self.assertEqual(efforts_for("devin/glm-5-2-1m"), ("none", "high", "max"))
        self.assertEqual(efforts_for("devin/gemini-3-5-flash"), ("minimal", "low", "medium", "high"))
        self.assertEqual(efforts_for("devin/kimi-k3"), ("low", "high", "max"))
        self.assertEqual(efforts_for("devin/swe-2"), ("medium", "high", "max"))
        self.assertEqual(efforts_for("devin/swe-1-7"), ("medium", "max"))
        self.assertEqual(efforts_for("devin/claude-opus-4-6"), ())
        self.assertEqual(efforts_for("devin/swe-1-6"), ())
        self.assertEqual(resolve_wire_uid("devin/deepseek-v4-1-flash", "high"), "deepseek-v4-1-flash-high")
        self.assertEqual(resolve_wire_uid("devin/deepseek-v4-1-flash", "max"), "deepseek-v4-1-flash-max")
        self.assertEqual(resolve_wire_uid("devin/deepseek-v4-flash", "high"), "deepseek-v4-flash-high")
        self.assertEqual(resolve_wire_uid("devin/glm-5-2", "high"), "glm-5-2")
        self.assertEqual(resolve_wire_uid("devin/glm-5-2", "max"), "glm-5-2-max")
        self.assertEqual(resolve_wire_uid("devin/glm-5-2", "none"), "glm-5-2-none")
        self.assertEqual(resolve_wire_uid("devin/glm-5-2", "low"), "glm-5-2-none")
        self.assertEqual(resolve_wire_uid("devin/glm-5-2-1m", "high"), "glm-5-2-1m")
        self.assertEqual(resolve_wire_uid("devin/glm-5-2-1m", "max"), "glm-5-2-max-1m")
        self.assertEqual(resolve_wire_uid("devin/gemini-3-5-flash", "minimal"), "gemini-3-5-flash-minimal")
        self.assertEqual(resolve_wire_uid("devin/gemini-3-5-flash", "low"), "gemini-3-5-flash-low")
        self.assertEqual(resolve_wire_uid("devin/kimi-k3", "high"), "kimi-k3-high")
        self.assertEqual(resolve_wire_uid("devin/swe-2", "medium"), "swe-2-medium")
        self.assertEqual(resolve_wire_uid("devin/swe-1-7", "max"), "swe-1-7")
        self.assertEqual(resolve_wire_uid("devin/swe-1-7", "medium"), "swe-1-7-medium")
        self.assertEqual(resolve_wire_uid("devin/claude-opus-4-6", "medium"), "claude-opus-4-6")
        self.assertEqual(resolve_wire_uid("devin/swe-1-6", "high"), "swe-1-6")
        self.assertEqual(resolve_wire_uid("devin/gpt-5-4", "none"), "gpt-5-4-none")
        self.assertEqual(resolve_wire_uid("devin/claude-opus-5", "medium"), "claude-opus-5-medium")


class SkuHealthTest(unittest.TestCase):
    """The catalog lists SKUs the chat backend may not serve.

    Windsurf's GetCliModelConfigs is a UI catalog; GetChatMessage fast-fails
    some SKUs ("an internal error occurred", "version out of date") without
    any catalog-side marker. Runtime failures must hide the SKU until the
    cooldown lapses.
    """

    def setUp(self):
        _clear_catalog()

    def test_error_classification(self):
        self.assertEqual(
            devin_mod.sku_error_kind("an internal error occurred (trace ID: 064a7ba5)"),
            "internal",
        )
        self.assertEqual(
            devin_mod.sku_error_kind("Your Windsurf version is out of date. Please update to the latest version to continue."),
            "version",
        )
        self.assertEqual(devin_mod.sku_error_kind("Devin timed out"), "")
        self.assertEqual(devin_mod.sku_error_kind(""), "")

    def test_dead_uid_lifecycle(self):
        devin_mod.note_sku_failure("glm-5-2-none")
        self.assertTrue(devin_mod.sku_dead("glm-5-2-none"))
        self.assertFalse(devin_mod.sku_dead("glm-5-2"))
        devin_mod.reset_sku_health()
        self.assertFalse(devin_mod.sku_dead("glm-5-2-none"))

    def _load_glm_catalog(self):
        catalog_ids_from_records(
            [
                {"id": "glm-5-2", "name": "GLM-5.2 High"},
                {"id": "glm-5-2-max", "name": "GLM-5.2 Max"},
                {"id": "glm-5-2-none", "name": "GLM-5.2 No Thinking"},
            ]
        )

    def test_efforts_hide_dead_skus(self):
        self._load_glm_catalog()
        self.assertEqual(efforts_for("devin/glm-5-2"), ("none", "high", "max"))
        devin_mod.note_sku_failure("glm-5-2-none")
        self.assertEqual(efforts_for("devin/glm-5-2"), ("high", "max"))
        devin_mod.reset_sku_health()
        self.assertEqual(efforts_for("devin/glm-5-2"), ("none", "high", "max"))

    def test_chat_fails_fast_on_dead_sku(self):
        from app.sources.devin import ensure_devin_source

        ensure_devin_source()
        self._load_glm_catalog()
        devin_mod.note_sku_failure("glm-5-2-none")
        src = DevinSource({"id": ensure_devin_source(), "name": "Devin"}, {}, [])
        result = asyncio.run(
            src.chat(
                {
                    "model": "devin/glm-5-2",
                    "messages": [{"role": "user", "content": "hi"}],
                    "reasoning_effort": "none",
                }
            )
        )
        self.assertEqual(result.status, 503)
        self.assertIn("unavailable", result.body["error"]["message"])
        self.assertIn("glm-5-2", result.body["error"]["message"])

    def test_live_sku_still_routes(self):
        from app.sources.devin import ensure_devin_source

        ensure_devin_source()
        self._load_glm_catalog()
        devin_mod.note_sku_failure("glm-5-2-none")
        src = DevinSource({"id": ensure_devin_source(), "name": "Devin"}, {}, [])
        # No credentials in the pool: the dead-SKU gate must not fire for a
        # healthy uid; the request proceeds to the (empty) pool pick.
        result = asyncio.run(
            src.chat(
                {
                    "model": "devin/glm-5-2",
                    "messages": [{"role": "user", "content": "hi"}],
                    "reasoning_effort": "high",
                }
            )
        )
        # The empty pool is a refusal naming the pool (pool_pick.explain_empty),
        # not the old transient-looking "No Devin accounts in the pool" 503.
        self.assertEqual(result.status, 403)
        self.assertEqual(result.body["error"]["message"], "No Devin account in the pool is available to you.")


class CatalogMetadataTest(unittest.TestCase):
    """Family metadata (field 30) is authoritative for effort mapping."""

    def setUp(self):
        _clear_catalog()

    def test_proto_decodes_family_metadata_and_promo(self):
        rec = proto.ProtoEncoder()
        rec.string(1, "GLM-5.2 No Thinking")
        rec.string(22, "glm-5-2-none")
        rec.uint64(18, 200000)

        def _family(e):
            e.string(1, "GLM-5.2")

            def _dim(se):
                se.string(1, "Effort")

                def _val(sse):
                    sse.uint32(1, 1)
                    sse.string(2, "No Thinking")

                se.message(2, _val)

            e.message(2, _dim)

        rec.message(30, _family)

        def _promo(e):
            e.uint32(1, 1)
            e.message(2, lambda se: se.uint64(1, 1789545540))

        rec.message(19, _promo)
        outer = proto.ProtoEncoder()
        outer.message(1, lambda e: e._buf.extend(rec.finish()))
        models = proto.decode_cli_model_configs(outer.finish())
        self.assertEqual(models[0]["id"], "glm-5-2-none")
        self.assertEqual(models[0]["name"], "GLM-5.2 No Thinking")
        self.assertEqual(models[0]["family"], "GLM-5.2")
        self.assertEqual(models[0]["effort"], "No Thinking")
        self.assertEqual(models[0]["promo_until"], 1789545540)

    def test_proto_keeps_minimal_records(self):
        rec = proto.ProtoEncoder()
        rec.string(1, "Claude Fable 5.1")
        rec.string(22, "claude-fable-5-1")
        outer = proto.ProtoEncoder()
        outer.message(1, lambda e: e._buf.extend(rec.finish()))
        models = proto.decode_cli_model_configs(outer.finish())
        self.assertEqual(models[0]["family"], "")
        self.assertEqual(models[0]["effort"], "")
        self.assertEqual(models[0]["promo_until"], 0)

    def test_catalog_prefers_metadata_effort(self):
        names, _labels = catalog_ids_from_records(
            [
                {"id": "swe-1-7", "name": "SWE-1.7 Max", "family": "SWE-1.7", "effort": "Max"},
                {"id": "swe-1-7-medium", "name": "SWE-1.7 Medium", "family": "SWE-1.7", "effort": "Medium"},
                {"id": "MODEL_PRIVATE_11", "name": "Claude Haiku 4.5", "effort": "High"},
            ]
        )
        self.assertIn("devin/swe-1-7", names)
        self.assertEqual(efforts_for("devin/swe-1-7"), ("medium", "max"))
        self.assertEqual(resolve_wire_uid("devin/swe-1-7", "max"), "swe-1-7")
        self.assertEqual(resolve_wire_uid("devin/swe-1-7", "medium"), "swe-1-7-medium")
        # Internal uids with metadata gain an effort instead of staying plain.
        self.assertIn("devin/claude-haiku-4-5", names)
        self.assertEqual(efforts_for("devin/claude-haiku-4-5"), ("high",))
        self.assertEqual(resolve_wire_uid("devin/claude-haiku-4-5", "high"), "MODEL_PRIVATE_11")

    def test_catalog_normalizes_metadata_effort_labels(self):
        names, _labels = catalog_ids_from_records(
            [
                {"id": "glm-5-2", "name": "GLM-5.2 High", "family": "GLM-5.2", "effort": "High"},
                {"id": "glm-5-2-none", "name": "GLM-5.2 No Thinking", "family": "GLM-5.2", "effort": "No Thinking"},
                {"id": "opus-9-xhigh", "name": "Opus 9 X-High", "family": "Opus 9", "effort": "X-High"},
            ]
        )
        self.assertIn("devin/glm-5-2", names)
        self.assertEqual(efforts_for("devin/glm-5-2"), ("none", "high"))
        self.assertEqual(resolve_wire_uid("devin/glm-5-2", "none"), "glm-5-2-none")
        self.assertEqual(efforts_for("devin/opus-9"), ("xhigh",))
        self.assertEqual(resolve_wire_uid("devin/opus-9", "xhigh"), "opus-9-xhigh")

    def test_catalog_records_promo_expiry_and_hides_lapsed_promos(self):
        import time

        live = int(time.time()) + 86400
        lapsed = int(time.time()) - 86400
        names, _labels = catalog_ids_from_records(
            [
                {"id": "glm-5-2", "name": "GLM-5.2 High", "effort": "High", "promo_until": live},
                {"id": "glm-5-2-none", "name": "GLM-5.2 No Thinking", "effort": "No Thinking", "promo_until": live},
                {"id": "kimi-k3-high", "name": "Kimi K3 High", "promo_until": lapsed},
                {"id": "swe-2-high", "name": "SWE-2 High"},
            ]
        )
        self.assertEqual(devin_catalog._model_promo_until.get("devin/glm-5-2"), live)
        self.assertNotIn("devin/swe-2", devin_catalog._model_promo_until)
        # A lapsed promo is neither listed nor routable; it returns if the
        # catalog re-lists it.
        self.assertNotIn("devin/kimi-k3", names)
        self.assertNotIn("devin/kimi-k3", devin_catalog._model_labels)
        self.assertNotIn("devin/kimi-k3", devin_catalog._model_wire_uids)


class StreamBindTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        _clear_catalog()
        unittest.mock.patch("app.sources.pool_pick.accounts.cred_extra", lambda c: c.get("_extra") or {}).start()
        self.addCleanup(unittest.mock.patch.stopall)

    async def test_completion_binds_pin_through_the_chat_path(self):
        # The producer mutates the same empty dict _chat_one hands back; a
        # fresh dict at wrap time (`usage or {}`) would hide completion from
        # the bind hook. Drive chat(), not the wrapper.
        collected: dict = {}

        async def stream():
            collected["stream_status"] = "complete"
            yield b'data: {"choices": [{"delta": {}}]}\n\n'

        async def fake_chat_one(_self, token, body, model, cred, session_id, attempts):
            from app.sources.base import ChatResult

            return ChatResult(
                status=200,
                stream=stream(),
                usage=collected,
                credential_id=cred["id"],
                model=model,
            )

        cred = {"id": 9, "disabled": 0, "week_tokens": 0, "week_start": db.iso_week_start(), "last_usage_percent": 0, "_extra": {"api_key": "tok"}}
        source = object.__new__(DevinSource)
        source.source_id = 5
        source.name = "t"
        source.cfg = {}
        source.creds = [cred]
        with (
            unittest.mock.patch.object(devin_mod, "refresh_pool_status", new_callable=unittest.mock.AsyncMock),
            unittest.mock.patch.object(devin_mod.pool_policy, "filter_credentials", return_value=[cred]),
            unittest.mock.patch.object(devin_mod, "note_pool_invoke"),
            unittest.mock.patch.object(devin_mod, "reserve"),
            unittest.mock.patch.object(devin_mod, "release"),
            unittest.mock.patch.object(devin_mod, "bind") as bind,
            unittest.mock.patch.object(DevinSource, "_chat_one", fake_chat_one),
        ):
            result = await source.chat(
                {"model": "devin/swe-2", "stream": True, "messages": [{"role": "user", "content": "hi"}]},
                session_id="sess-9",
            )
            self.assertEqual(result.status, 200)
            async for _chunk in result.stream:
                pass
        bind.assert_called_once_with("devin", "sess-9", 9)


class PoolExhaustAttemptsTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        _clear_catalog()
        unittest.mock.patch("app.sources.pool_pick.accounts.cred_extra", lambda c: c.get("_extra") or {}).start()
        self.addCleanup(unittest.mock.patch.stopall)

    async def test_pool_exhaustion_records_final_attempt_once(self):
        # Parity with grok/chatgpt/kimi/zai/mimo: when the pool is exhausted
        # the final failed attempt rides on the returned result, not also in
        # attempts (its usage would be counted twice).
        async def fake_chat_one(_self, token, body, model, cred, session_id, attempts):
            from app.sources.base import ChatResult

            return ChatResult(
                status=429,
                body={"error": {"message": "quota"}},
                usage={"prompt_tokens": 5},
                credential_id=cred["id"],
                model=model,
            )

        cred = {"id": 9, "disabled": 0, "week_tokens": 0, "week_start": db.iso_week_start(), "last_usage_percent": 0, "_extra": {"api_key": "tok"}}
        source = object.__new__(DevinSource)
        source.source_id = 5
        source.name = "t"
        source.cfg = {}
        source.creds = [cred]
        with (
            unittest.mock.patch.object(devin_mod, "refresh_pool_status", new_callable=unittest.mock.AsyncMock),
            unittest.mock.patch.object(devin_mod.pool_policy, "filter_credentials", return_value=[cred]),
            unittest.mock.patch.object(devin_mod, "note_pool_invoke"),
            unittest.mock.patch.object(devin_mod, "reserve"),
            unittest.mock.patch.object(devin_mod, "release"),
            unittest.mock.patch.object(devin_mod, "note_failure"),
            unittest.mock.patch.object(DevinSource, "_chat_one", fake_chat_one),
        ):
            result = await source.chat(
                {"model": "devin/swe-2", "messages": [{"role": "user", "content": "hi"}]},
            )
        self.assertEqual(result.status, 429)
        self.assertEqual(result.credential_id, 9)
        self.assertEqual(result.attempts, [])


class _FrameResp:
    def __init__(self, frames):
        self._frames = frames

    async def aiter_bytes(self):
        for frame in self._frames:
            yield frame

    async def aclose(self):
        return None


class StreamUsageDetailsTest(unittest.IsolatedAsyncioTestCase):
    async def test_final_sse_reports_cached_and_write_tokens(self):
        usage_msg = proto.ProtoEncoder()
        usage_msg.message(
            7,
            lambda e: (e.uint32(2, 10), e.uint32(3, 2), e.uint32(4, 6), e.uint32(5, 4)),
        )
        stop_msg = proto.ProtoEncoder()
        stop_msg.uint32(5, proto.STOP_MAX_TOKENS)
        end = bytes([proto.CONNECT_END_STREAM]) + (2).to_bytes(4, "big") + b"{}"
        frames = [proto.connect_frame(usage_msg.finish()), proto.connect_frame(stop_msg.finish()), end]
        collected: dict = {}
        client = unittest.mock.AsyncMock()
        chunks = []
        async for chunk in devin_stream._stream_sse(_FrameResp(frames), client, collected, "swe-2", "uid-1"):
            chunks.append(chunk.decode())
        joined = "".join(chunks)
        # Field 2 (10) is the uncached tail; reads (4) and writes (6) add to it.
        self.assertIn('"prompt_tokens":20', joined)
        self.assertIn('"prompt_tokens_details":{"cached_tokens":4,"cache_write_tokens":6}', joined)
        self.assertEqual(collected["prompt_tokens"], 20)
        self.assertEqual(collected["cached_tokens"], 4)
        self.assertEqual(collected["cache_write_tokens"], 6)


class StreamTrailerFailoverTest(unittest.IsolatedAsyncioTestCase):
    async def _run(self, error: dict) -> tuple[str, dict]:
        body = json.dumps({"error": error}).encode()
        end = bytes([proto.CONNECT_END_STREAM]) + len(body).to_bytes(4, "big") + body
        collected: dict = {}
        chunks = []
        async for chunk in devin_stream._stream_sse(_FrameResp([end]), unittest.mock.AsyncMock(), collected, "swe-2", "uid-1"):
            chunks.append(chunk.decode())
        return "".join(chunks), collected

    async def test_quota_trailer_is_a_resumable_429(self):
        # An exhausted account answers 200 and reports quota in the trailer:
        # the chat loop must retry (onto another account), not end the turn.
        from app.chat_generation.policy import _error_code_and_message, _should_resume

        text, collected = await self._run({"code": "resource_exhausted", "message": "daily quota exceeded"})
        self.assertEqual(collected["trailer_status"], 429)
        frame = next(json.loads(line[6:]) for line in text.splitlines() if line.startswith("data: {") and '"error"' in line)
        code, msg = _error_code_and_message(frame)
        self.assertEqual(code, 429)
        self.assertTrue(_should_resume(code, msg, resumes_left=1, idle_stalls=0))

    async def test_request_trailer_stays_terminal(self):
        text, collected = await self._run({"code": "invalid_argument", "message": "bad request"})
        self.assertNotIn("trailer_status", collected)
        self.assertNotIn('"code"', text)


if __name__ == "__main__":
    unittest.main()
