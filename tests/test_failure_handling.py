"""Failure-path regressions: pool cooldowns, error propagation, durable finish."""
import asyncio
import json
import unittest
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import HTTPException

from app import api, chat_runs, db, proxy
from app.search import engine
from app.search.providers.base import ProviderResponse, empty_reason
from app.sources import chatgpt as chatgpt_mod
from app.sources import pool_pick, token_store


def _creds(n: int) -> list[dict]:
    week = db.iso_week_start()
    return [
        {"id": i, "disabled": 0, "week_tokens": 0, "week_start": week, "extra": "", "secret": ""}
        for i in range(1, n + 1)
    ]


class _ChatGPTHarness:
    """Drive ChatGPTSource.chat with mocked DB and an httpx MockTransport."""

    def __init__(self, n: int, handler):
        self.creds = _creds(n)
        self.handler = handler
        self.seen: list[int] = []

    def _client(self):
        def route(request: httpx.Request) -> httpx.Response:
            cid = int(request.headers["Authorization"].split("-")[-1])
            self.seen.append(cid)
            return self.handler(cid, request)

        return httpx.AsyncClient(transport=httpx.MockTransport(route))

    def run(self, body: dict | None = None):
        extras = {c["id"]: {"refresh_token": "rt", "access_token": f"tok-{c['id']}"} for c in self.creds}
        source = chatgpt_mod.ChatGPTSource({"id": 9, "name": "chatgpt"}, {}, self.creds)
        with (
            patch.object(chatgpt_mod, "refresh_due", AsyncMock()),
            patch.object(chatgpt_mod, "refresh_pool_usage", AsyncMock()),
            patch.object(chatgpt_mod.db, "fetchall", return_value=self.creds),
            patch.object(chatgpt_mod.pool_policy, "filter_credentials", side_effect=lambda rows, *_: rows),
            patch.object(chatgpt_mod.accounts, "cred_extra", side_effect=lambda row: dict(extras[row["id"]])),
            patch.object(pool_pick.accounts, "cred_extra", side_effect=lambda row: dict(extras[row["id"]])),
            patch.object(chatgpt_mod, "_note_invoke"),
            patch.object(chatgpt_mod, "lookup", return_value=None),
            patch.object(chatgpt_mod, "bind"),
            patch.object(chatgpt_mod, "_client", side_effect=self._client),
        ):
            return asyncio.run(
                source.chat(body or {"model": "gpt-6-sol", "messages": [{"role": "user", "content": "hi"}]})
            )


def _responses_completed() -> bytes:
    events = [
        {"type": "response.created", "response": {"id": "r1"}},
        {"type": "response.output_text.delta", "delta": "ok"},
        {"type": "response.completed", "response": {"id": "r1", "usage": {"input_tokens": 1, "output_tokens": 1}}},
    ]
    return "".join(f"data: {json.dumps(e)}\n\n" for e in events).encode()


class ChatGPTPoolCooldownTest(unittest.TestCase):
    def setUp(self):
        pool_pick.reset_health()

    def tearDown(self):
        pool_pick.reset_health()

    def _cooling(self, n: int) -> list[int]:
        return [cid for cid in range(1, n + 1) if pool_pick._cooling_down(cid)]

    def test_request_error_does_not_cool_account(self):
        harness = _ChatGPTHarness(2, lambda cid, req: httpx.Response(400, json={"error": {"message": "context too long"}}))
        result = harness.run()
        self.assertEqual(result.status, 400)
        self.assertEqual(harness.seen, [1])
        self.assertEqual(self._cooling(2), [])

    def test_local_exception_does_not_cool_or_fail_over(self):
        harness = _ChatGPTHarness(3, lambda cid, req: httpx.Response(200))
        with patch.object(chatgpt_mod.ChatGPTSource, "_post", AsyncMock(side_effect=UnicodeEncodeError("utf-8", "x", 0, 1, "surrogates"))):
            result = harness.run()
        self.assertEqual(result.status, 500)
        self.assertEqual(self._cooling(3), [])

    def test_server_errors_stop_after_two_accounts_without_cooldown(self):
        harness = _ChatGPTHarness(4, lambda cid, req: httpx.Response(500, json={"error": {"message": "boom"}}))
        result = harness.run()
        self.assertEqual(result.status, 500)
        self.assertEqual(harness.seen, [1, 2])
        self.assertEqual(self._cooling(4), [])

    def test_server_error_cools_account_once_another_serves(self):
        def handler(cid, req):
            if cid == 1:
                return httpx.Response(503, json={"error": {"message": "sick"}})
            return httpx.Response(200, content=_responses_completed(), headers={"content-type": "text/event-stream"})

        harness = _ChatGPTHarness(3, handler)
        result = harness.run()
        self.assertEqual(result.status, 200)
        self.assertEqual(self._cooling(3), [1])


class FailoverGuardTest(unittest.TestCase):
    def setUp(self):
        pool_pick.reset_health()

    def tearDown(self):
        pool_pick.reset_health()

    def test_timeout_never_fails_over(self):
        self.assertFalse(pool_pick.Failover().server_error(1, 504, 30))

    def test_auth_failure_needs_a_repeat(self):
        self.assertFalse(pool_pick.auth_failure_confirmed(7))
        self.assertTrue(pool_pick._cooling_down(7))
        self.assertTrue(pool_pick.auth_failure_confirmed(7))

    def test_unreadable_credential_is_skipped(self):
        from app.crypto import DecryptionError

        creds = _creds(2)

        def extra(row):
            if row["id"] == 1:
                raise DecryptionError("bad")
            return {}

        with patch.object(pool_pick.accounts, "cred_extra", side_effect=extra):
            picked = pool_pick.pick_account(creds, kind="zai")
        self.assertEqual(picked[0]["id"], 2)


class ProxyErrorPropagationTest(unittest.TestCase):
    def _call(self, handler, stream: bool):
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        body = {"model": "m", "messages": [], "stream": stream}
        return asyncio.run(proxy.openai_chat(client, "http://up/v1/chat/completions", {}, body, close_client=True))

    def test_html_error_keeps_upstream_status(self):
        for stream in (False, True):
            status, obj, *_ = self._call(lambda req: httpx.Response(413, text="<html>Too Large</html>"), stream)
            self.assertEqual(status, 413)
            self.assertIn("413", obj["error"]["message"])

    def test_non_utf8_error_body_does_not_raise(self):
        status, obj, *_ = self._call(lambda req: httpx.Response(400, content=b"\xff\xfe\xff"), False)
        self.assertEqual(status, 400)
        self.assertIsInstance(obj, dict)

    def test_non_utf8_success_body_is_502(self):
        status, *_ = self._call(lambda req: httpx.Response(200, content=b"\xff\xfe\xff"), False)
        self.assertEqual(status, 502)

    def test_decoding_error_is_502(self):
        def handler(req):
            return httpx.Response(200, content=b"not gzip", headers={"content-encoding": "gzip"})

        status, *_ = self._call(handler, False)
        self.assertEqual(status, 502)


class ApiInputValidationTest(unittest.TestCase):
    def test_unhashable_role_is_400(self):
        with self.assertRaises(HTTPException) as caught:
            api._validate_messages([{"role": [], "content": "x"}])
        self.assertEqual(caught.exception.status_code, 400)

    def test_unencodable_body_is_400(self):
        for body in ({"content": "\ud800"}, {"temperature": float("nan")}):
            with self.assertRaises(HTTPException) as caught:
                api._ensure_encodable(body)
            self.assertEqual(caught.exception.status_code, 400)
        api._ensure_encodable({"content": "ok ✓"})


class TokenStoreTest(unittest.TestCase):
    def test_expires_at_tolerates_garbage(self):
        for value in ("soon", None, "", float("inf"), 10**30):
            self.assertRegex(token_store.expires_at(value, 3600), r"^\d{4}-\d\d-\d\dT")

    def test_lost_race_is_retried_onto_newest_row(self):
        calls = []

        def cas(cid, old, secret, extra):
            calls.append((old, dict(extra)))
            return old == "v2"

        with (
            patch.object(token_store.db, "fetchone", return_value={"id": 1, "extra": "v2", "disabled": 0}),
            patch.object(token_store.accounts, "cred_extra", return_value={"label": "kept"}),
        ):
            ok = token_store.persist_rotated(cas, 1, "v1", "s", {"x": 1}, {"refresh_token": "new"})
        self.assertTrue(ok)
        self.assertEqual(calls[-1][1], {"label": "kept", "refresh_token": "new"})

    def test_removed_row_reports_failure(self):
        with patch.object(token_store.db, "fetchone", return_value=None), patch.object(token_store.accounts, "cred_extra", return_value={}):
            ok = token_store.persist_rotated(lambda *a: False, 1, "v1", "s", {}, {"refresh_token": "new"})
        self.assertFalse(ok)


class SearchEmptyClassificationTest(unittest.TestCase):
    def test_empty_reason(self):
        self.assertEqual(empty_reason("<html>Please solve this CAPTCHA</html>", "b_algo"), "blocked")
        self.assertEqual(empty_reason('<li class="b_algo">broken</li>', "b_algo"), "parse")
        self.assertEqual(empty_reason("<html>No results</html>", "b_algo"), "empty")

    def test_blocked_engines_are_a_failure_not_no_results(self):
        blocked = AsyncMock(return_value=ProviderResponse(hits=[], status=200, error="blocked"))
        empty = AsyncMock(return_value=ProviderResponse(hits=[], status=200, error="empty"))

        async def run():
            with (
                patch("app.search.engine._brave_key", return_value=""),
                patch.object(engine._ddg, "search", blocked),
                patch.object(engine._bing, "search", blocked),
                patch.object(engine._yahoo, "search", blocked),
                patch.object(engine._hn, "search", empty),
                patch.object(engine._wiki, "search", empty),
                patch("app.search.engine._allow", return_value=True),
            ):
                return await engine.search_bulk(["zzqx unusual failure probe"], user_id=10)

        out = asyncio.run(run())
        self.assertEqual(out[0].kind, "provider_error")


class FinishRunDurabilityTest(unittest.TestCase):
    def setUp(self):
        chat_runs._unsettled.clear()

    def tearDown(self):
        chat_runs._unsettled.clear()

    def test_failed_finish_is_parked(self):
        failing = AsyncMock(side_effect=RuntimeError("db down"))
        with (
            patch.object(chat_runs, "durable_call", failing),
            patch.object(chat_runs, "_FINISH_BACKOFF", (0.0, 0.0)),
        ):
            ok = asyncio.run(chat_runs._finish_durably("run1", {"content": "done"}, "completed"))
        self.assertFalse(ok)
        self.assertEqual(failing.await_count, 2)
        self.assertEqual(chat_runs._unsettled["run1"][:2], ({"content": "done"}, "completed"))

    def test_reaper_settles_instead_of_resuming(self):
        chat_runs._unsettled["run1"] = ({"content": "done"}, "completed", 0)
        row = {"id": "run1", "user_id": 1, "thread_id": 2}
        with (
            patch.object(chat_runs.chat_store, "finish_run") as finish,
            patch.object(chat_runs.chat_store, "recover_one") as recover,
            patch.object(chat_runs, "schedule_wake") as wake,
        ):
            chat_runs._reap_one(row)
        finish.assert_called_once_with("run1", {"content": "done"}, "completed")
        recover.assert_not_called()
        wake.assert_called_once_with(1, 2)
        self.assertNotIn("run1", chat_runs._unsettled)

    def test_reaper_gives_up_to_abort_after_repeated_failures(self):
        chat_runs._unsettled["run1"] = ({}, "completed", chat_runs.UNSETTLED_REAP_ATTEMPTS - 1)
        row = {"id": "run1", "user_id": 1, "thread_id": 2}
        with (
            patch.object(chat_runs.chat_store, "finish_run", side_effect=RuntimeError("bad")),
            patch.object(chat_runs.chat_store, "abort_active") as abort,
        ):
            chat_runs._reap_one(row)
        abort.assert_called_once_with("run1", "completed")
        self.assertNotIn("run1", chat_runs._unsettled)


class TxRollbackTest(unittest.TestCase):
    def test_base_exception_rolls_back_and_failed_rollback_closes(self):
        events = []

        class Conn:
            def commit(self):
                events.append("commit")

            def rollback(self):
                events.append("rollback")
                raise RuntimeError("socket gone")

            def close(self):
                events.append("close")

        class Pool:
            def checkout(self):
                return Conn()

            def release(self, conn):
                events.append("release")

        with patch.object(db, "connect", return_value=Pool()):
            with self.assertRaises(KeyboardInterrupt):
                with db.tx():
                    raise KeyboardInterrupt
        self.assertEqual(events, ["rollback", "close", "release"])


if __name__ == "__main__":
    unittest.main()
