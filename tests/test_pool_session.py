import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
_TMP = tempfile.mkdtemp(prefix="fuse-pool-session-")
os.environ["DATABASE_PATH"] = str(Path(_TMP) / "pool-session.db")
os.environ.setdefault("APP_SECRET", "x" * 32)

from app import db  # noqa: E402
from app.sources import chatgpt as chatgpt_mod
from app.sources import grok as grok_mod
from app.sources import ollama as ollama_mod
from app.sources import pool_session
from app.sources import zai as zai_mod
from app.sources.pool_pick import USAGE_BUSY, chatgpt_plan_type, pick_account, reset_health
from app.sources.pool_session import (
    TTL_SECONDS,
    bind,
    drop_cache_routing_fields,
    fallback_session_id,
    lookup,
    reset_store,
    session_id_from,
)


def _cred(cid, pct, week=0, extra=None, disabled=0, week_start=None):
    return {
        "id": cid,
        "disabled": disabled,
        "week_tokens": week,
        "week_start": week_start if week_start is not None else db.iso_week_start(),
        "last_usage_percent": pct,
        "_extra": extra or {},
    }


class PoolSessionTest(unittest.TestCase):
    def setUp(self):
        reset_health()
        pool_session.persist = False
        reset_store()

    def tearDown(self):
        reset_store()
        pool_session.persist = True

    def test_prompt_cache_key_wins_over_header_and_hash(self):
        body = {
            "prompt_cache_key": "client-key",
            "model": "grok-4.5",
            "messages": [{"role": "user", "content": "hello"}],
        }
        sid = session_id_from({"x-grok-conv-id": "header-id"}, body, user_id=1, model="grok-4.5")
        self.assertTrue(sid.startswith("fx:"))
        # Same client key, same scope -> same id; never the raw value.
        again = session_id_from(None, body, user_id=1, model="grok-4.5")
        self.assertEqual(sid, again)
        self.assertNotIn("client-key", sid)
        # Different tenant or model scopes apart.
        self.assertNotEqual(sid, session_id_from(None, body, user_id=2, model="grok-4.5"))
        self.assertNotEqual(sid, session_id_from(None, body, user_id=1, model="grok-4.6"))

    def test_x_grok_conv_id_used_when_no_body_key(self):
        body = {"model": "grok-4.5", "messages": [{"role": "user", "content": "hello"}]}
        sid = session_id_from({"x-grok-conv-id": "conv-abc"}, body, user_id=1, model="grok-4.5")
        self.assertTrue(sid.startswith("fx:"))
        self.assertNotIn("conv-abc", sid)
        body_key = session_id_from(None, {"model": "grok-4.5", "session_id": "conv-abc"}, user_id=1, model="grok-4.5")
        self.assertEqual(sid, body_key)

    def test_fallback_stable_when_turns_append(self):
        base = {
            "messages": [
                {"role": "system", "content": "You are a coding assistant."},
                {"role": "user", "content": "Open app/api.py"},
            ],
            "tools": [{"type": "function", "function": {"name": "read_file"}}],
        }
        first = fallback_session_id(user_id=9, model="grok-4.5", body=base)
        follow = {
            "messages": base["messages"]
            + [
                {"role": "assistant", "content": "Here is the file."},
                {"role": "user", "content": "Now edit it."},
            ],
            "tools": base["tools"],
        }
        second = fallback_session_id(user_id=9, model="grok-4.5", body=follow)
        self.assertEqual(first, second)
        self.assertTrue(first.startswith("fuse:"))

    def test_fallback_tool_order_does_not_change_id(self):
        a = {
            "messages": [{"role": "user", "content": "hi"}],
            "tools": [
                {"type": "function", "function": {"name": "read_file"}},
                {"type": "function", "function": {"name": "write_file"}},
            ],
        }
        b = {
            "messages": a["messages"],
            "tools": list(reversed(a["tools"])),
        }
        self.assertEqual(
            fallback_session_id(user_id=1, model="grok-4.5", body=a),
            fallback_session_id(user_id=1, model="grok-4.5", body=b),
        )
    def test_fallback_changes_with_first_user_or_model_or_user(self):
        body = {"messages": [{"role": "user", "content": "Open app/api.py"}]}
        a = fallback_session_id(user_id=1, model="grok-4.5", body=body)
        b = fallback_session_id(user_id=1, model="grok-4.5", body={"messages": [{"role": "user", "content": "other"}]})
        c = fallback_session_id(user_id=1, model="grok-4.6", body=body)
        d = fallback_session_id(user_id=2, model="grok-4.5", body=body)
        self.assertNotEqual(a, b)
        self.assertNotEqual(a, c)
        self.assertNotEqual(a, d)

    def test_drop_cache_fields_copies_and_strips(self):
        body = {"model": "glm-5.3", "prompt_cache_key": "x", "prompt_cache_retention": "24h", "n": 1}
        out = drop_cache_routing_fields(body)
        self.assertEqual(body["prompt_cache_key"], "x")
        self.assertNotIn("prompt_cache_key", out)
        self.assertNotIn("prompt_cache_retention", out)
        self.assertEqual(out["n"], 1)

    def test_bind_lookup_and_ttl_expiry(self):
        t0 = datetime(2026, 8, 29, 12, 0, 0, tzinfo=timezone.utc)
        bind("grok", "sess-1", 105, now=t0)
        self.assertEqual(lookup("grok", "sess-1", now=t0), 105)
        self.assertIsNone(lookup("chatgpt", "sess-1", now=t0))
        still = t0 + timedelta(seconds=TTL_SECONDS - 1)
        self.assertEqual(lookup("grok", "sess-1", now=still), 105)
        expired = t0 + timedelta(seconds=TTL_SECONDS + 1)
        self.assertIsNone(lookup("grok", "sess-1", now=expired))

    def test_bind_replaces_credential_on_failover(self):
        t0 = datetime(2026, 8, 29, 12, 0, 0, tzinfo=timezone.utc)
        bind("grok", "sess-1", 104, now=t0)
        bind("grok", "sess-1", 105, now=t0)
        self.assertEqual(lookup("grok", "sess-1", now=t0), 105)

    def test_lookup_empty_is_none(self):
        self.assertIsNone(lookup("grok", None))
        self.assertIsNone(lookup("grok", ""))

    @patch("app.sources.pool_pick.accounts.cred_extra", side_effect=lambda c: c.get("_extra") or {})
    def test_prefer_id_beats_lower_usage(self, _extra):
        low = _cred(104, pct=10.0, week=1)
        high = _cred(105, pct=90.0, week=999)
        picked, extra = pick_account([low, high], kind="grok", prefer_id=105)
        self.assertEqual(picked["id"], 105)
        picked, extra = pick_account([low, high], kind="grok")
        self.assertEqual(picked["id"], 104)

    @patch("app.sources.pool_pick.accounts.cred_extra", side_effect=lambda c: c.get("_extra") or {})
    def test_unknown_percent_scores_as_zero_not_week_tokens(self, _extra):
        known = _cred(110, pct=69.0, week=1)
        unknown = _cred(
            119,
            pct=None,
            week=24_949_597,
            extra={"billing": {"fetched_at": "2026-09-20T00:00:00Z", "products": []}},
        )
        picked, _ = pick_account([known, unknown], kind="grok")
        self.assertEqual(picked["id"], 119)
        picked, _ = pick_account([unknown], kind="grok")
        self.assertEqual(picked["id"], 119)

    @patch("app.sources.pool_pick.accounts.cred_extra", side_effect=lambda c: c.get("_extra") or {})
    def test_busy_account_serves_when_it_is_the_only_candidate(self, _extra):
        busy = _cred(
            108,
            pct=98.0,
            extra={"billing": {"credit_usage_percent": 98.0, "windows": [{"percent": 98.0}]}},
        )
        fresh = _cred(
            109,
            pct=10.0,
            extra={"billing": {"credit_usage_percent": 10.0, "windows": [{"percent": 10.0}]}},
        )
        picked, _ = pick_account([busy], kind="zai")
        self.assertEqual(picked["id"], 108)
        picked, _ = pick_account([busy, fresh], kind="zai")
        self.assertEqual(picked["id"], 109)
        picked, _ = pick_account([busy, fresh], kind="zai", prefer_id=108)
        self.assertEqual(picked["id"], 108)

    @patch("app.sources.pool_pick.accounts.cred_extra", side_effect=lambda c: c.get("_extra") or {})
    def test_prefer_id_skipped_when_excluded_or_busy_or_unauthed(self, _extra):
        a = _cred(104, pct=20.0)
        b = _cred(105, pct=40.0)
        picked, _ = pick_account([a, b], kind="grok", prefer_id=104, exclude={104})
        self.assertEqual(picked["id"], 105)

        busy = _cred(104, pct=USAGE_BUSY)
        ok = _cred(105, pct=40.0)
        picked, _ = pick_account([busy, ok], kind="grok", prefer_id=104)
        self.assertEqual(picked["id"], 104)
        picked, _ = pick_account([busy, ok], kind="grok")
        self.assertEqual(picked["id"], 105)

        dead = _cred(104, pct=5.0)
        live = _cred(105, pct=40.0)
        picked, _ = pick_account(
            [dead, live],
            kind="grok",
            prefer_id=104,
            is_authed=lambda cred, extra: cred["id"] != 104,
        )
        self.assertEqual(picked["id"], 105)

    @patch("app.sources.pool_pick.accounts.cred_extra", side_effect=lambda c: c.get("_extra") or {})
    def test_chatgpt_prefer_id_skipped_when_plan_blocks_model(self, _extra):
        free = _cred(
            106,
            pct=1.0,
            extra={"billing": {"plan_type": "free", "windows": [{"percent": 1, "seconds": 30 * 86400}]}},
        )
        plus = _cred(
            107,
            pct=80.0,
            extra={"billing": {"plan_type": "plus", "windows": [{"percent": 80, "seconds": 5 * 3600}]}},
        )
        picked, extra = pick_account([free, plus], kind="chatgpt", model="gpt-5.4", prefer_id=106)
        self.assertEqual(picked["id"], 107)
        self.assertEqual(chatgpt_plan_type(extra), "plus")

    @patch("app.sources.pool_pick.accounts.cred_extra", side_effect=lambda c: c.get("_extra") or {})
    def test_chatgpt_free_models_prefer_free_over_lower_usage_plus(self, _extra):
        free = _cred(
            106,
            pct=40.0,
            extra={"billing": {"plan_type": "free", "windows": [{"percent": 40, "seconds": 30 * 86400}]}},
        )
        go = _cred(
            108,
            pct=50.0,
            extra={"billing": {"plan_type": "go", "windows": [{"percent": 50, "seconds": 30 * 86400}]}},
        )
        plus = _cred(
            107,
            pct=1.0,
            extra={"billing": {"plan_type": "plus", "windows": [{"percent": 1, "seconds": 5 * 3600}]}},
        )
        for model in ("gpt-5.6-luna", "gpt-5.6-terra", "gpt-5.5", "gpt-5.4-mini"):
            picked, extra = pick_account([free, go, plus], kind="chatgpt", model=model)
            self.assertEqual(picked["id"], 106, model)
            self.assertEqual(chatgpt_plan_type(extra), "free")

    @patch("app.sources.pool_pick.accounts.cred_extra", side_effect=lambda c: c.get("_extra") or {})
    def test_chatgpt_free_models_fall_back_to_plus_when_free_busy(self, _extra):
        free = _cred(
            106,
            pct=USAGE_BUSY,
            extra={"billing": {"plan_type": "free", "windows": [{"percent": USAGE_BUSY, "seconds": 30 * 86400}]}},
        )
        plus = _cred(
            107,
            pct=1.0,
            extra={"billing": {"plan_type": "plus", "windows": [{"percent": 1, "seconds": 5 * 3600}]}},
        )
        picked, extra = pick_account([free, plus], kind="chatgpt", model="gpt-5.6-luna")
        self.assertEqual(picked["id"], 107)
        self.assertEqual(chatgpt_plan_type(extra), "plus")

    @patch("app.sources.pool_pick.accounts.cred_extra", side_effect=lambda c: c.get("_extra") or {})
    def test_chatgpt_sol_skips_free_even_when_plus_is_higher_usage(self, _extra):
        free = _cred(
            106,
            pct=1.0,
            extra={"billing": {"plan_type": "free", "windows": [{"percent": 1, "seconds": 30 * 86400}]}},
        )
        plus = _cred(
            107,
            pct=80.0,
            extra={"billing": {"plan_type": "plus", "windows": [{"percent": 80, "seconds": 5 * 3600}]}},
        )
        picked, extra = pick_account([free, plus], kind="chatgpt", model="gpt-5.6-sol")
        self.assertEqual(picked["id"], 107)
        self.assertEqual(chatgpt_plan_type(extra), "plus")

    @patch("app.sources.pool_pick.accounts.cred_extra", side_effect=lambda c: c.get("_extra") or {})
    def test_chatgpt_luna_sticky_plus_stays_for_cache(self, _extra):
        free = _cred(
            106,
            pct=1.0,
            extra={"billing": {"plan_type": "free", "windows": [{"percent": 1, "seconds": 30 * 86400}]}},
        )
        plus = _cred(
            107,
            pct=80.0,
            extra={"billing": {"plan_type": "plus", "windows": [{"percent": 80, "seconds": 5 * 3600}]}},
        )
        picked, extra = pick_account([free, plus], kind="chatgpt", model="gpt-5.6-luna", prefer_id=107)
        self.assertEqual(picked["id"], 107)
        self.assertEqual(chatgpt_plan_type(extra), "plus")

    @patch("app.sources.pool_pick.accounts.cred_extra", side_effect=lambda c: c.get("_extra") or {})
    def test_chatgpt_lowest_usage_among_free_then_among_plus(self, _extra):
        free_high = _cred(
            106,
            pct=40.0,
            extra={"billing": {"plan_type": "free", "windows": [{"percent": 40, "seconds": 30 * 86400}]}},
        )
        free_low = _cred(
            108,
            pct=10.0,
            extra={"billing": {"plan_type": "free", "windows": [{"percent": 10, "seconds": 30 * 86400}]}},
        )
        plus_high = _cred(
            107,
            pct=50.0,
            extra={"billing": {"plan_type": "plus", "windows": [{"percent": 50, "seconds": 5 * 3600}]}},
        )
        plus_low = _cred(
            109,
            pct=5.0,
            extra={"billing": {"plan_type": "plus", "windows": [{"percent": 5, "seconds": 5 * 3600}]}},
        )
        picked, extra = pick_account(
            [free_high, free_low, plus_high, plus_low], kind="chatgpt", model="gpt-5.6-luna"
        )
        self.assertEqual(picked["id"], 108)
        picked, extra = pick_account(
            [plus_high, plus_low], kind="chatgpt", model="gpt-5.6-sol"
        )
        self.assertEqual(picked["id"], 109)

    def test_chatgpt_profile_email_from_access_token(self):
        import base64
        import json

        claims = {"https://api.openai.com/profile": {"email": "ada@example.com", "name": "Ada"}}
        payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
        extra = chatgpt_mod.apply_profile({"access_token": f"x.{payload}.y"})
        self.assertEqual(extra["email"], "ada@example.com")
        self.assertEqual(extra["name"], "Ada")
        self.assertEqual(chatgpt_mod.account_email(extra), "ada@example.com")
        self.assertEqual(chatgpt_mod.account_email({"email": "stored@example.com"}), "stored@example.com")
        self.assertEqual(chatgpt_mod.account_email({"email": "not-an-email"}), "")

    @patch("app.sources.pool_pick.accounts.cred_extra", side_effect=lambda c: c.get("_extra") or {})
    def test_chatgpt_skips_accounts_whose_usage_probe_got_401(self, _extra):
        dead = _cred(
            140,
            pct=0.0,
            extra={
                "refresh_token": "rt",
                "billing": {
                    "plan_type": "free",
                    "last_error": "http 401",
                    "windows": [{"percent": 0, "seconds": 30 * 86400}],
                },
            },
        )
        live = _cred(
            139,
            pct=1.0,
            extra={
                "refresh_token": "rt",
                "billing": {
                    "plan_type": "free",
                    "windows": [{"percent": 1, "seconds": 30 * 86400}],
                },
            },
        )
        picked, extra = pick_account(
            [dead, live],
            kind="chatgpt",
            model="gpt-5.6-luna",
            is_authed=chatgpt_mod.credential_is_authed,
        )
        self.assertEqual(picked["id"], 139)
        self.assertFalse(chatgpt_mod.credential_is_authed(dead, dead["_extra"]))
        self.assertTrue(chatgpt_mod.credential_is_authed(live, live["_extra"]))

    def test_grok_sends_conv_id_header(self):
        headers = grok_mod._auth_headers("tok", "grok-4.5", conv_id="conv-1")
        self.assertEqual(headers["x-grok-conv-id"], "conv-1")
        bare = grok_mod._auth_headers("tok", "grok-4.5")
        self.assertNotIn("x-grok-conv-id", bare)

    def test_chatgpt_payload_includes_prompt_cache_key(self):
        body = {"messages": [{"role": "user", "content": "hi"}]}
        payload = chatgpt_mod.build_responses_payload(body, "gpt-5.6-luna", session_id="sess-9")
        self.assertEqual(payload["prompt_cache_key"], "sess-9")
        payload = chatgpt_mod.build_responses_payload(
            {"messages": [{"role": "user", "content": "hi"}], "prompt_cache_key": "from-body"},
            "gpt-5.6-luna",
            session_id="sess-9",
        )
        self.assertEqual(payload["prompt_cache_key"], "sess-9")
        self.assertNotEqual(payload["prompt_cache_key"], "from-body")
        bare = chatgpt_mod.build_responses_payload(body, "gpt-5.6-luna", session_id=None)
        self.assertNotIn("prompt_cache_key", bare)

    @patch("app.sources.chatgpt.note_failure")
    @patch("app.sources.chatgpt.bind")
    def test_chatgpt_partial_stream_keeps_pin_without_cooldown(self, bind, fail):
        src = chatgpt_mod.ChatGPTSource({"id": 1, "name": "ChatGPT"}, {}, [])
        src._bind_completed_stream("sess-9", 116, {"usage_status": "partial", "prompt_tokens": 8000})
        bind.assert_not_called()
        fail.assert_not_called()
        src._bind_completed_stream("sess-9", 116, {"stream_status": "complete", "prompt_tokens": 8000})
        bind.assert_called_once_with("chatgpt", "sess-9", 116)
        fail.assert_not_called()

    def test_chatgpt_payload_omits_codex_unsupported_fields(self):
        payload = chatgpt_mod.build_responses_payload(
            {
                "messages": [{"role": "user", "content": "hi"}],
                "max_tokens": 64,
                "temperature": 0.2,
                "top_p": 0.9,
                "prompt_cache_retention": "24h",
                "prompt_cache_options": {"mode": "explicit", "ttl": "30m"},
                "reasoning_effort": "low",
                "reasoning": {"mode": "pro"},
            },
            "gpt-5.6-luna",
            session_id="sess-9",
        )
        for key in (
            "max_output_tokens",
            "temperature",
            "top_p",
            "prompt_cache_retention",
            "prompt_cache_options",
        ):
            self.assertNotIn(key, payload)
        self.assertEqual(payload["prompt_cache_key"], "sess-9")
        self.assertEqual(payload["reasoning"], {"mode": "pro", "effort": "low", "summary": "auto"})

    def test_chatgpt_unsupported_param_parse(self):
        self.assertEqual(
            chatgpt_mod._unsupported_param({"detail": "Unsupported parameter: max_output_tokens"}),
            "max_output_tokens",
        )
        self.assertEqual(
            chatgpt_mod._unsupported_param(
                {"error": {"message": "prompt_cache_retention is not supported on this model", "param": "prompt_cache_retention"}}
            ),
            "prompt_cache_retention",
        )
        self.assertIsNone(chatgpt_mod._unsupported_param({"error": {"message": "model not found"}}))

    def test_zai_strips_prompt_cache_key(self):
        payload = zai_mod._chat_payload({"model": "glm-5.3-flash", "prompt_cache_key": "x", "messages": []})
        self.assertNotIn("prompt_cache_key", payload)
        self.assertNotIn("thinking", payload)

    def test_zai_maps_standard_reasoning_effort_to_thinking(self):
        enabled = zai_mod._chat_payload({"messages": [], "reasoning_effort": "high"})
        self.assertEqual(enabled["thinking"], {"type": "enabled"})
        disabled = zai_mod._chat_payload({"messages": [], "reasoning_effort": "none"})
        self.assertEqual(disabled["thinking"], {"type": "disabled"})
        native = zai_mod._chat_payload(
            {"messages": [], "reasoning_effort": "high", "thinking": {"type": "disabled"}}
        )
        self.assertEqual(native["thinking"], {"type": "disabled"})
        self.assertNotIn("reasoning_effort", native)

    def test_zai_maps_structured_reasoning_without_forwarding_it(self):
        payload = zai_mod._chat_payload({"messages": [], "reasoning": {"effort": "low"}})
        self.assertEqual(payload["thinking"], {"type": "enabled"})
        self.assertNotIn("reasoning", payload)

    def test_ollama_maps_reasoning_and_returns_thinking(self):
        src = ollama_mod.OllamaSource(
            {"id": 1, "name": "Ollama"}, {"model": "qwen3"}, []
        )
        self.assertEqual(src._payload({"messages": [], "reasoning_effort": "none"})["think"], False)
        self.assertEqual(src._payload({"messages": [], "reasoning_effort": "high"})["think"], "high")
        self.assertEqual(src._payload({"messages": [], "reasoning": {"effort": "xhigh"}})["think"], "max")

    def test_chatgpt_payload_preserves_files_and_image_detail(self):
        payload = chatgpt_mod.build_responses_payload(
            {
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "image_url", "image_url": {"url": "https://example.test/a.png", "detail": "high"}},
                            {
                                "type": "input_file",
                                "file_data": "data:application/pdf;base64,cGRm",
                                "filename": "report.pdf",
                            },
                        ],
                    }
                ]
            },
            "gpt-5.6-luna",
        )
        self.assertEqual(
            payload["input"][0]["content"],
            [
                {"type": "input_image", "image_url": "https://example.test/a.png", "detail": "high"},
                {
                    "type": "input_file",
                    "file_data": "data:application/pdf;base64,cGRm",
                    "filename": "report.pdf",
                },
            ],
        )

    def test_ollama_rejects_unrepresentable_attachments(self):
        with self.assertRaises(ollama_mod.UnsupportedAttachment):
            ollama_mod.to_ollama(
                [{"role": "user", "content": [{"type": "input_file", "file_data": "data:text/plain;base64,eA=="}]}]
            )
        with self.assertRaises(ollama_mod.UnsupportedAttachment):
            ollama_mod.to_ollama(
                [{"role": "user", "content": [{"type": "image_url", "image_url": "https://example.test/a.png"}]}]
            )


class GrokChatStickyTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        reset_health()
        pool_session.persist = False
        reset_store()

    def tearDown(self):
        reset_store()
        pool_session.persist = True

    @patch("app.sources.grok.note_pool_invoke")
    @patch("app.sources.grok.accounts.cred_secret", return_value="tok")
    @patch(
        "app.sources.grok.accounts.cred_extra",
        side_effect=lambda c: c.get("_extra") or {"access_token": "tok"},
    )
    @patch("app.sources.grok.openai_chat", new_callable=AsyncMock)
    @patch("app.sources.grok.db.fetchall")
    @patch("app.sources.grok.refresh_pool_billing", new_callable=AsyncMock)
    @patch("app.sources.grok.refresh_due", new_callable=AsyncMock)
    async def test_chat_stays_on_bound_account_after_usage_shifts(
        self, _due, _bill, fetchall, chat, _extra, _secret, _invoke
    ):
        low_then_busy = _cred(105, pct=5.0, extra={"access_token": "tok"})
        other = _cred(104, pct=40.0, extra={"access_token": "tok"})
        fetchall.return_value = [low_then_busy, other]
        chat.return_value = (
            200,
            {"id": "c", "usage": {"prompt_tokens": 10, "completion_tokens": 1}},
            None,
            {"prompt_tokens": 10, "completion_tokens": 1, "cached_tokens": 0},
            {},
        )
        src = grok_mod.GrokSource({"id": 1, "name": "Grok"}, {}, [low_then_busy, other])
        body = {"model": "grok-4.5", "messages": [{"role": "user", "content": "hi"}]}
        first = await src.chat(body, session_id="sess-sticky")
        self.assertEqual(first.status, 200)
        self.assertEqual(first.credential_id, 105)
        self.assertEqual(lookup("grok", "sess-sticky"), 105)

        low_then_busy = _cred(105, pct=90.0, extra={"access_token": "tok"})
        other = _cred(104, pct=1.0, extra={"access_token": "tok"})
        fetchall.return_value = [low_then_busy, other]
        src.creds = [low_then_busy, other]
        second = await src.chat(body, session_id="sess-sticky")
        self.assertEqual(second.credential_id, 105)
        self.assertEqual(chat.call_count, 2)
        headers = chat.call_args.kwargs.get("headers") or chat.call_args[0][2]
        self.assertEqual(headers["x-grok-conv-id"], "sess-sticky")

    @patch("app.sources.grok.note_pool_invoke")
    @patch("app.sources.grok.accounts.cred_secret", return_value="tok")
    @patch(
        "app.sources.grok.accounts.cred_extra",
        side_effect=lambda c: c.get("_extra") or {"access_token": "tok"},
    )
    @patch("app.sources.grok.openai_chat", new_callable=AsyncMock)
    @patch("app.sources.grok.db.fetchall")
    @patch("app.sources.grok.refresh_pool_billing", new_callable=AsyncMock)
    @patch("app.sources.grok.refresh_due", new_callable=AsyncMock)
    async def test_chat_rebinds_after_retryable_failover(
        self, _due, _bill, fetchall, chat, _extra, _secret, _invoke
    ):
        a = _cred(104, pct=1.0, extra={"access_token": "tok"})
        b = _cred(105, pct=2.0, extra={"access_token": "tok"})
        fetchall.return_value = [a, b]
        chat.side_effect = [
            (429, {"error": "rate"}, None, {}, {}),
            (
                200,
                {"id": "c", "usage": {"prompt_tokens": 10, "completion_tokens": 1}},
                None,
                {"prompt_tokens": 10, "completion_tokens": 1, "cached_tokens": 0},
                {},
            ),
        ]
        src = grok_mod.GrokSource({"id": 1, "name": "Grok"}, {}, [a, b])
        result = await src.chat(
            {"model": "grok-4.5", "messages": [{"role": "user", "content": "hi"}]},
            session_id="sess-fail",
        )
        self.assertEqual(result.status, 200)
        self.assertEqual(result.credential_id, 105)
        self.assertEqual(lookup("grok", "sess-fail"), 105)

    @patch("app.sources.grok.note_pool_invoke")
    @patch("app.sources.grok.accounts.cred_secret", return_value="tok")
    @patch(
        "app.sources.grok.accounts.cred_extra",
        side_effect=lambda c: c.get("_extra") or {"access_token": "tok"},
    )
    @patch("app.sources.grok.openai_chat", new_callable=AsyncMock)
    @patch("app.sources.grok.db.fetchall")
    @patch("app.sources.grok.refresh_pool_billing", new_callable=AsyncMock)
    @patch("app.sources.grok.refresh_due", new_callable=AsyncMock)
    async def test_terminal_retry_is_not_duplicated_in_attempts(
        self, _due, _bill, fetchall, chat, _extra, _secret, _invoke
    ):
        a = _cred(104, pct=1.0, extra={"access_token": "tok"})
        b = _cred(105, pct=2.0, extra={"access_token": "tok"})
        fetchall.return_value = [a, b]
        chat.side_effect = [
            (429, {"error": "rate"}, None, {"total_tokens": 10}, {}),
            (429, {"error": "rate"}, None, {"total_tokens": 20}, {}),
        ]
        src = grok_mod.GrokSource({"id": 1, "name": "Grok"}, {}, [a, b])
        result = await src.chat(
            {"model": "grok-4.5", "messages": [{"role": "user", "content": "hi"}]},
            session_id="sess-exhausted",
        )
        self.assertEqual(result.credential_id, 105)
        self.assertEqual([attempt["credential_id"] for attempt in result.attempts], [104])


class _FakeChatGPTResp:
    def __init__(self, status):
        self.status_code = status

    async def aclose(self):
        return None


class ChatGPTChatFailoverTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        reset_health()
        pool_session.persist = False
        reset_store()

    def tearDown(self):
        reset_store()
        pool_session.persist = True

    def _free_cred(self, cid, pct=0.0):
        return _cred(
            cid,
            pct=pct,
            extra={
                "access_token": "tok",
                "refresh_token": "rt",
                "account_id": "acct",
                "billing": {
                    "plan_type": "free",
                    "windows": [{"percent": pct, "seconds": 30 * 86400}],
                },
            },
        )

    @patch("app.sources.chatgpt._note_invoke")
    @patch("app.sources.chatgpt.accounts.cred_secret", return_value="tok")
    @patch(
        "app.sources.chatgpt.accounts.cred_extra",
        side_effect=lambda c: c.get("_extra") or {"access_token": "tok", "refresh_token": "rt"},
    )
    @patch("app.sources.chatgpt._aggregate", new_callable=AsyncMock)
    @patch("app.sources.chatgpt._error_body", new_callable=AsyncMock)
    @patch("app.sources.chatgpt.ChatGPTSource._post", new_callable=AsyncMock)
    @patch("app.sources.chatgpt.db.fetchall")
    @patch("app.sources.chatgpt.refresh_pool_usage", new_callable=AsyncMock)
    @patch("app.sources.chatgpt.refresh_due", new_callable=AsyncMock)
    async def test_chat_tries_next_account_after_retryable_failure(
        self, _due, _usage, fetchall, post, error_body, aggregate, _extra, _secret, _invoke
    ):
        dead = self._free_cred(140, pct=0.0)
        live = self._free_cred(139, pct=1.0)
        fetchall.return_value = [dead, live]
        post.side_effect = [_FakeChatGPTResp(429), _FakeChatGPTResp(200)]
        error_body.return_value = {"error": {"message": "rate limited"}}
        aggregate.return_value = {
            "id": "c",
            "usage": {"prompt_tokens": 10, "completion_tokens": 1},
        }
        src = chatgpt_mod.ChatGPTSource({"id": 4, "name": "ChatGPT"}, {}, [dead, live])
        result = await src.chat(
            {"model": "gpt-5.6-luna", "messages": [{"role": "user", "content": "hi"}]},
            session_id="sess-luna",
        )
        self.assertEqual(result.status, 200)
        self.assertEqual(result.credential_id, 139)
        self.assertEqual([attempt["credential_id"] for attempt in result.attempts], [140])
        self.assertEqual(lookup("chatgpt", "sess-luna"), 139)

    @patch("app.sources.chatgpt._note_invoke")
    @patch("app.sources.chatgpt.accounts.cred_secret", return_value="tok")
    @patch(
        "app.sources.chatgpt.accounts.cred_extra",
        side_effect=lambda c: c.get("_extra") or {"access_token": "tok", "refresh_token": "rt"},
    )
    @patch("app.sources.chatgpt._aggregate", new_callable=AsyncMock)
    @patch("app.sources.chatgpt._error_body", new_callable=AsyncMock)
    @patch("app.sources.chatgpt.ChatGPTSource._post", new_callable=AsyncMock)
    @patch("app.sources.chatgpt.db.fetchall")
    @patch("app.sources.chatgpt.refresh_pool_usage", new_callable=AsyncMock)
    @patch("app.sources.chatgpt.refresh_due", new_callable=AsyncMock)
    async def test_chat_does_not_failover_on_nonretryable_400(
        self, _due, _usage, fetchall, post, error_body, aggregate, _extra, _secret, _invoke
    ):
        first = self._free_cred(140, pct=0.0)
        second = self._free_cred(139, pct=1.0)
        fetchall.return_value = [first, second]
        post.return_value = _FakeChatGPTResp(400)
        error_body.return_value = {"error": {"message": "model not found"}}
        src = chatgpt_mod.ChatGPTSource({"id": 4, "name": "ChatGPT"}, {}, [first, second])
        result = await src.chat(
            {"model": "gpt-5.6-luna", "messages": [{"role": "user", "content": "hi"}]},
        )
        self.assertEqual(result.status, 400)
        self.assertEqual(result.credential_id, 140)
        self.assertEqual(post.call_count, 1)
        aggregate.assert_not_called()


if __name__ == "__main__":
    unittest.main()
