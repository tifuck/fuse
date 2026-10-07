"""Ollama Cloud key-pool behavior: account-unavailable detection and rotation."""
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx

from app.sources.key_pool import (
    ACTIVE,
    COOLING,
    DISABLED,
    DISABLED_UNAVAILABLE,
    KeyPool,
    RESTING,
)
from app.sources.ollama_access import (
    classify_cloud_error,
    is_account_unavailable_error,
    label_for,
)
from app.web import sources as web_sources

UNAVAILABLE_BODY = (
    '{"error":"Your account is currently unavailable. '
    'If the problem persists, please contact support@ollama.com."}\n'
)


class ClassificationTest(unittest.TestCase):
    def test_account_unavailable_by_body_on_any_status(self):
        for status in (403, 401, 500, 429):
            with self.subTest(status=status):
                self.assertEqual(classify_cloud_error(status, UNAVAILABLE_BODY), "account_unavailable")
                self.assertTrue(is_account_unavailable_error(status, UNAVAILABLE_BODY))

    def test_plain_auth_still_classified_as_auth(self):
        self.assertEqual(classify_cloud_error(403, '{"error":"Invalid token"}'), "auth")
        self.assertFalse(is_account_unavailable_error(403, '{"error":"Invalid token"}'))

    def test_gated_wins_over_account_markers(self):
        self.assertEqual(classify_cloud_error(403, "this model requires a subscription"), "model_gated")

    def test_label(self):
        self.assertEqual(label_for("account_unavailable"), "Account unavailable")


def make_pool(tmp: str, keys: list[str]) -> KeyPool:
    pool = KeyPool(state_file=str(Path(tmp) / "state.json"))
    pool.load_from_list(keys)
    return pool


class RotationTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="fuse-pool-")
        self.addCleanup(self.tmp.cleanup)

    async def test_unavailable_rotates_to_next_key_and_disables(self):
        seen = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request.headers["Authorization"])
            if request.headers["Authorization"].endswith("keyA"):
                return httpx.Response(403, content=UNAVAILABLE_BODY.encode())
            return httpx.Response(
                200,
                content=b'{"message":{"role":"assistant","content":"ok"},"done":true}',
            )

        pool = make_pool(self.tmp.name, ["keyA", "keyB"])
        deterministic = patch(
            "app.sources.key_pool.random.choices",
            side_effect=lambda population, weights=None, k=1: [population[0]] * k,
        )
        deterministic.start()
        self.addCleanup(deterministic.stop)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            response, state, retried = await pool.send_request(
                client, "https://ollama.com/api/chat", {"model": "gpt-oss:120b", "messages": []}
            )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(retried)
        self.assertEqual(len(seen), 2)
        bad = pool.states["keyA"]
        self.assertEqual(bad.state, DISABLED)
        self.assertEqual(bad.disabled_reason, DISABLED_UNAVAILABLE)
        self.assertGreater(bad.available_at, time.time() + 60 * 60 * 12)
        self.assertEqual(pool.states["keyB"].state, RESTING)

    def test_unavailable_reenables_after_cooldown(self):
        pool = make_pool(self.tmp.name, ["keyA"])
        pool._handle_error_status(403, httpx.Headers(), UNAVAILABLE_BODY, pool.states["keyA"])
        state = pool.states["keyA"]
        self.assertEqual(state.state, DISABLED)
        self.assertEqual(state.disabled_reason, DISABLED_UNAVAILABLE)
        self.assertEqual(state.last_error_type, "account_unavailable")
        state.available_at = time.time() - 1
        state.refresh_state()
        self.assertEqual(state.state, ACTIVE)
        self.assertIsNone(state.disabled_reason)

    def test_recurrence_reapplies_disable(self):
        pool = make_pool(self.tmp.name, ["keyA"])
        state = pool.states["keyA"]
        for _ in range(3):
            pool._handle_error_status(403, httpx.Headers(), UNAVAILABLE_BODY, state)
            self.assertEqual(state.disabled_reason, DISABLED_UNAVAILABLE)
            self.assertGreater(state.available_in(), 0)
            # Ollama clears the flag; the key self-heals and is tried again.
            state.available_at = time.time() - 1
            state.refresh_state()
            self.assertEqual(state.state, ACTIVE)
        self.assertGreaterEqual(state.total_errors, 3)

    def test_auth_disable_unchanged(self):
        pool = make_pool(self.tmp.name, ["keyA"])
        for _ in range(3):
            pool._handle_error_status(403, httpx.Headers(), '{"error":"Invalid token"}', pool.states["keyA"])
        state = pool.states["keyA"]
        self.assertEqual(state.state, DISABLED)
        self.assertEqual(state.disabled_reason, "auth_failure")
        self.assertEqual(state.available_at, 0.0)

    def test_retry_after_extends_account_unavailable_cooldown(self):
        pool = make_pool(self.tmp.name, ["keyA"])
        state = pool.states["keyA"]
        pool._handle_error_status(
            403, httpx.Headers({"retry-after": "172800"}), UNAVAILABLE_BODY, state
        )
        self.assertGreater(state.available_in(), pool.account_unavailable)
        # A shorter Retry-After must not shrink the default window.
        pool2 = make_pool(self.tmp.name, ["keyB"])
        state2 = pool2.states["keyB"]
        pool2._handle_error_status(403, httpx.Headers({"retry-after": "30"}), UNAVAILABLE_BODY, state2)
        self.assertAlmostEqual(state2.available_in(), pool2.account_unavailable, delta=5)

    def test_account_unavailable_cooldown_is_configurable(self):
        pool = KeyPool(state_file=str(Path(self.tmp.name) / "s.json"), account_unavailable=90.0)
        pool.load_from_list(["keyA"])
        pool._handle_error_status(403, httpx.Headers(), UNAVAILABLE_BODY, pool.states["keyA"])
        self.assertGreater(pool.states["keyA"].available_in(), 85)
        self.assertLessEqual(pool.states["keyA"].available_in(), 90)

    async def test_unreachable_pool_still_fails_closed(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(403, content=UNAVAILABLE_BODY.encode())

        pool = make_pool(self.tmp.name, ["keyA"])
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            response, state, retried = await pool.send_request(
                client, "https://ollama.com/api/chat", {"model": "m", "messages": []}
            )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(retried)
        self.assertEqual(state.disabled_reason, DISABLED_UNAVAILABLE)


class PoolSingletonTest(unittest.TestCase):
    def test_get_pool_passes_account_unavailable_through(self):
        from app.sources import key_pool as kp

        kp.reset_pool()
        try:
            pool = kp.get_pool(account_unavailable=123.0)
            self.assertEqual(pool.account_unavailable, 123.0)
        finally:
            kp.reset_pool()


class AdminKeyViewLabelTest(unittest.TestCase):
    """The /admin/sources key-status mapping for the new disable reason."""

    def views(self, state, reason="", last_error=""):
        return [{"state": state, "disabled_reason": reason, "last_error": last_error}]

    def run_mapping(self, view):
        stub = type("StubSource", (), {"__init__": lambda self, row, cfg, creds: None,
                                       "key_views": lambda self, creds: view})
        row = {"config": "{}"}
        creds = [{"id": 1, "disabled": 0}]
        with patch.object(web_sources.db, "fetchone", return_value=row), \
             patch.object(web_sources.db, "fetchall", return_value=creds), \
             patch("app.web.source_routes.admin_pages.OllamaSource", stub):
            return web_sources._ollama_key_views(1)

    def test_account_unavailable_labelled_as_cooling(self):
        out = self.run_mapping(self.views("disabled", "account_unavailable"))
        self.assertEqual(out[0]["state_label"], "Account unavailable")
        self.assertEqual(out[0]["state_kind"], "cool")

    def test_existing_labels_unchanged(self):
        cases = (
            (self.views("disabled", "weekly_quota"), "Out of usage", "cool"),
            (self.views("disabled", "session_quota"), "Out of usage", "cool"),
            (self.views("disabled", "auth_failure"), "Auth failed", "off"),
            (self.views("disabled", "unknown"), "Disabled", "off"),
            (self.views("cooling"), "Cooldown", "cool"),
            (self.views("resting"), "Resting", "on"),
            (self.views("active"), "Ready", "on"),
            (self.views("active", last_error="model_gated"), "Ready", "on"),
        )
        for view, label, kind in cases:
            with self.subTest(label=label):
                out = self.run_mapping(view)
                self.assertEqual(out[0]["state_label"], label)
                self.assertEqual(out[0]["state_kind"], kind)


if __name__ == "__main__":
    unittest.main()
