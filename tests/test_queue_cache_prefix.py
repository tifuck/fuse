"""Queue/Send-now requeue and forced wrap-up provider-prefix regressions. Offline."""
import asyncio
import copy
import json
import os
import sqlite3
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

_TMP = tempfile.TemporaryDirectory(prefix="fuse-queue-prefix-")
os.environ.setdefault("DATABASE_PATH", _TMP.name + "/test.db")
os.environ.setdefault("APP_SECRET", "offline-queue-prefix-secret" * 2)

from fastapi import HTTPException
from app import chat_runs as runs, chat_runtime as runtime, chat_store as store, chats, db

TAIL = "[Fuse session context"
NUDGE = runtime._NUDGE


def ok_result(text, calls=None):
    message = {"content": text}
    if calls:
        message["tool_calls"] = calls
    return SimpleNamespace(status=200, body={"choices": [{"message": message, "finish_reason": "tool_calls" if calls else "stop"}]},
                           usage={"prompt_tokens": 100, "completion_tokens": 5}, stream=None, credential_id=1)


def err_result(status=500):
    return SimpleNamespace(status=status, body={"error": {"message": "upstream exploded"}},
                           usage=None, stream=None, credential_id=1)


SEARCH_CALL = [{"id": "c1", "function": {"name": "web_search", "arguments": '{"query":"offline"}'}}]


class QueueCachePrefixTest(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:", check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        db._init(self.conn)
        self.addCleanup(self.conn.close)
        p = patch.object(db, "connect", return_value=self.conn)
        p.start()
        self.addCleanup(p.stop)
        db.execute("INSERT INTO users(id,email,password_hash,created_at) VALUES(1,'prefix@test.invalid','unused',?)", (db.now(),))
        self.thread = chats.create_thread(1, model="offline")
        self.run = store.start_turn(1, self.thread["id"], "Start", self.thread["revision"], "start")["run"]

    def view(self):
        return store.thread_view(self.conn, 1, self.run["thread_id"])

    def worker(self, run_row, invoke):
        """Drive a run through the real worker; capture a requeued start, if any."""
        start = AsyncMock()

        async def go():
            with patch.object(runtime.api, "invoke_chat", invoke), patch.object(runtime.api, "_safe_record_usage"), \
                 patch.object(runs, "start", start), patch.object(runs, "schedule_wake"), \
                 patch("app.push.notify_run_done", new=AsyncMock()):
                await runs._run(runs.internal_request(1), {"id": 1}, run_row)

        asyncio.run(go())
        return start

    def dispatch(self, run_row, invoke):
        """Run stream_dashboard_chat directly; invoke captures payloads; return the SSE body."""
        body = {"b": b""}

        async def go():
            with patch.object(runtime.api, "invoke_chat", invoke), patch.object(runtime.api, "_safe_record_usage"):
                response = await runtime.stream_dashboard_chat(
                    runs.internal_request(1), {"id": 1}, run_row["request"], frozen_context=run_row["request"])
                body["b"] = b"".join([chunk async for chunk in response.body_iterator])

        asyncio.run(go())
        return body["b"]

    @staticmethod
    def capturing(payloads, behavior):
        async def invoke(request, user, payload, **_kw):
            payloads.append(copy.deepcopy(payload))
            return behavior(len(payloads), user)

        return invoke

    def provider_messages(self):
        return json.loads(db.fetchone("SELECT provider_messages FROM chat_runs WHERE id=?", (self.run["id"],))["provider_messages"] or "[]")

    def test_live_child_followup_appends_after_the_latest_exchange(self):
        child = store.start_child_run(
            user_id=1, thread_id=self.thread["id"], parent_run_id=self.run["id"],
            prompt="Inspect the parser", description="Parser audit",
        )
        payloads = []

        def behavior(n, user):
            if n == 1:
                self.assertTrue(store.queue_followup(child["id"], "Check its tests too"))
                result = ok_result("Before steer", SEARCH_CALL)
            else:
                result = ok_result("After steer")
            return user, SimpleNamespace(type_id="llama", source_id=1), "offline", "offline", result

        with patch.object(runtime, "execute_tool", AsyncMock(return_value=("hits", {"name": "web_search", "status": "done"}))):
            self.worker(child, self.capturing(payloads, behavior))
        self.assertEqual(len(payloads), 2)
        first, second = [payload["messages"] for payload in payloads]
        self.assertEqual(second[:len(first)], first)
        suffix = second[len(first):]
        self.assertEqual([row["role"] for row in suffix], ["assistant", "tool", "user"])
        self.assertEqual(suffix[0]["content"], "Before steer")
        self.assertIn("Follow-up from the parent", suffix[-1]["content"])
        self.assertIn("Check its tests too", suffix[-1]["content"])
        finished = store.get_run(1, child["id"])
        self.assertEqual(finished["status"], "completed")
        self.assertEqual(len(finished["request"]["agent"]["followup_marks"]), 1)
        self.assertEqual(finished["request"]["agent"]["followup_marks"][0]["end"]["content"], len("Before steer"))

    def test_steer_requeue_failure_race_replays_sent_prefix(self):
        failed_round, accepted = [], {"done": False}

        async def failing(request, user, payload, **_kw):
            failed_round.append(copy.deepcopy(payload["messages"]))
            if not accepted["done"]:
                accepted["done"] = True
                # Send now accepted after this round's take_steers, mid-request.
                store.steer_turn(1, self.thread["id"], "New task", "late")
                raise HTTPException(status_code=401, detail="credential rejected")
            raise HTTPException(status_code=401, detail="credential rejected")

        start = self.worker(self.run, failing)
        requeued = start.call_args.args[2]
        self.assertEqual(requeued["status"], "queued")
        sent_user_row = failed_round[0][1]
        self.assertEqual(sent_user_row["role"], "user")
        self.assertEqual(sent_user_row["content"].count(TAIL), 1)

        payloads = []

        def ok(n, user):
            return user, SimpleNamespace(type_id="llama", source_id=1), "offline", "offline", ok_result("new answer")

        body = self.dispatch(requeued, self.capturing(payloads, ok))
        first = payloads[0]
        # Fresh budget, not a text-only wrap round and no restart banner.
        self.assertNotEqual(first.get("tool_choice"), "none")
        self.assertNotIn(b"Resumed after server restart", body)
        # The already-sent user row replays byte-identical: one session tail,
        # and the steer envelope is appended exactly once after the prefix.
        self.assertEqual(first["messages"][1], sent_user_row)
        self.assertEqual(first["messages"][1]["content"].count(TAIL), 1)
        self.assertEqual(sum(str(m.get("content") or "").count(TAIL) for m in first["messages"]), 1)
        steer_rows = [m for m in first["messages"] if "(steer:late)" in str(m.get("content"))]
        self.assertEqual(len(steer_rows), 1)
        self.assertEqual(first["messages"][-1], steer_rows[0])
        self.assertEqual(store.take_steers(self.run["id"]), [])
        self.assertEqual(self.provider_messages()[0], sent_user_row)

    def test_steer_requeue_completed_race_appends_without_new_tail(self):
        seeded = [{"role": "user", "content": "Start\n\n" + TAIL + " block one.]"},
                  {"role": "assistant", "content": "Done"}]
        store.save_turn_progress(self.run["id"], seeded)
        store.steer_turn(1, self.thread["id"], "New task", "late")
        store.finish_run(self.run["id"], {"content": "Done", "segments": [{"type": "content", "text": "Done"}]}, "completed")
        requeued = store.get_run(1, self.run["id"])
        self.assertEqual(requeued["status"], "queued")

        payloads = []

        def ok(n, user):
            return user, SimpleNamespace(type_id="llama", source_id=1), "offline", "offline", ok_result("new answer")

        _body = self.dispatch(requeued, self.capturing(payloads, ok))
        first = payloads[0]["messages"]
        self.assertEqual(first[0]["role"], "system")
        self.assertEqual(first[1], seeded[0])
        self.assertEqual(first[2], seeded[1])
        # No re-injected session tail anywhere; the steer is the only new row.
        self.assertEqual(sum(str(m.get("content") or "").count(TAIL) for m in first), 1)
        steer_rows = [m for m in first if "(steer:late)" in str(m.get("content"))]
        self.assertEqual(len(steer_rows), 1)
        self.assertEqual(first[-1], steer_rows[0])

    def test_forced_wrap_nudge_is_durable_and_retained_next_turn(self):
        payloads = []

        def behavior(n, user):
            result = ok_result("hits summary", SEARCH_CALL) if n == 1 else ok_result("wrapped up")
            return user, SimpleNamespace(type_id="llama", source_id=1), "offline", "offline", result

        invoke = self.capturing(payloads, behavior)
        with patch.object(runtime, "MAX_COMPLETIONS", 2), \
             patch.object(runtime, "execute_tool", AsyncMock(return_value=("hits", {"name": "web_search", "status": "done"}))):
            self.worker(self.run, invoke)
        self.assertEqual(store.get_run(1, self.run["id"])["status"], "completed")
        wrap = payloads[1]
        # The wrap-up request carries the nudge as a durable transcript row.
        self.assertEqual(wrap["messages"][-1], {"role": "user", "content": NUDGE})
        self.assertEqual(wrap["tool_choice"], "none")
        durable = self.provider_messages()
        self.assertEqual([m for m in durable if m.get("content") == NUDGE], [{"role": "user", "content": NUDGE}])
        retained = json.loads(db.fetchone(
            "SELECT retained_history FROM chat_threads WHERE id=?", (self.run["thread_id"],))["retained_history"])
        self.assertIn({"role": "user", "content": NUDGE}, [m for m in retained["messages"] if m.get("role") == "user"])

        # Next turn replays the wrap-up request as its byte-identical prefix.
        second = store.start_turn(1, self.thread["id"], "Second", self.view()["revision"], "second")["run"]
        payloads2 = []

        def ok(n, user):
            return user, SimpleNamespace(type_id="llama", source_id=1), "offline", "offline", ok_result("second answer")

        _body = self.dispatch(second, self.capturing(payloads2, ok))
        replay = payloads2[0]["messages"]
        self.assertEqual(replay[:len(wrap["messages"])], wrap["messages"])
        self.assertEqual(sum(str(m.get("content") or "").count(NUDGE) for m in replay), 1)
        self.assertTrue(str(replay[-1].get("content") or "").startswith("Second"))

    def test_model_switch_keeps_the_other_models_reasoning_durable(self):
        request = copy.deepcopy(self.run["request"])
        earlier = [{"role": "user", "content": "earlier"},
                   {"role": "assistant", "content": "answer", "reasoning_content": "A-THOUGHT",
                    "_fuse_reasoning_model": "model-a"}]
        request["messages"] = [*earlier, *request["messages"]]
        request["model"] = "model-b"
        payloads = []

        def ok(n, user):
            return user, SimpleNamespace(type_id="llama", source_id=1), "model-b", "model-b", ok_result("b answer")

        self.dispatch({**self.run, "request": request}, self.capturing(payloads, ok))
        # model-b is not sent model-a's reasoning ...
        self.assertNotIn("A-THOUGHT", json.dumps(payloads[0]["messages"]))
        # ... but the retained transcript keeps it, so switching back to
        # model-a replays the bytes it was sent before.
        durable = self.provider_messages()
        self.assertEqual(durable[1], earlier[1])

    def test_forced_wrap_retry_sends_same_bytes_with_single_nudge(self):
        payloads = []

        def behavior(n, user):
            if n == 1:
                result = ok_result("hits summary", SEARCH_CALL)
            elif n == 2:
                result = err_result(500)
            else:
                result = ok_result("wrapped up")
            return user, SimpleNamespace(type_id="llama", source_id=1), "offline", "offline", result

        invoke = self.capturing(payloads, behavior)
        with patch.object(runtime, "MAX_COMPLETIONS", 2), patch.object(runtime, "_RESUME_BACKOFF", (0.0, 0.0, 0.0, 0.0)), \
             patch.object(runtime, "execute_tool", AsyncMock(return_value=("hits", {"name": "web_search", "status": "done"}))):
            self.worker(self.run, invoke)
        self.assertEqual(store.get_run(1, self.run["id"])["status"], "completed")
        retried, first_wrap = payloads[2]["messages"], payloads[1]["messages"]
        # The retry sends the identical payload — no second nudge row appended.
        self.assertEqual(retried, first_wrap)
        self.assertEqual(first_wrap[-1], {"role": "user", "content": NUDGE})
        self.assertEqual(sum(str(m.get("content") or "").count(NUDGE) for m in first_wrap), 1)
        self.assertEqual([m for m in self.provider_messages() if m.get("content") == NUDGE],
                         [{"role": "user", "content": NUDGE}])

    def test_tools_unsupported_forced_retry_keeps_nudge(self):
        payloads = []

        def behavior(n, user):
            if n == 1:
                result = SimpleNamespace(status=400, body={"error": {"message": "this model does not support tools"}},
                                         usage=None, stream=None, credential_id=1)
            else:
                result = ok_result("final answer")
            return user, SimpleNamespace(type_id="llama", source_id=1), "offline", "offline", result

        async def no_retrieval(*_a, **_k):
            return
            yield

        with patch.object(runtime, "_retrieval_first", no_retrieval):
            self.worker(self.run, self.capturing(payloads, behavior))
        self.assertEqual(store.get_run(1, self.run["id"])["status"], "completed")
        # The 400 arrived before any force transition: tools offered, no nudge.
        self.assertTrue(payloads[0].get("tools"))
        self.assertNotIn(NUDGE, [str(m.get("content") or "") for m in payloads[0]["messages"]])
        # The in-loop fallback flips force: the retry keeps the nudge row.
        self.assertFalse(payloads[1].get("tools"))
        self.assertEqual(payloads[1]["messages"][-1], {"role": "user", "content": NUDGE})
        durable = json.loads(db.fetchone("SELECT provider_messages FROM chat_runs WHERE id=?", (self.run["id"],))["provider_messages"])
        self.assertEqual([m for m in durable if m.get("content") == NUDGE], [{"role": "user", "content": NUDGE}])

    def test_provider_resume_partial_keeps_old_nudge_appends_suffix(self):
        payloads = []

        def behavior(n, user):
            if n == 1:
                result = ok_result("hits summary", SEARCH_CALL)
            elif n == 2:
                result = SimpleNamespace(status=200, usage={"prompt_tokens": 100, "completion_tokens": 5}, stream=None,
                                         credential_id=1,
                                         body={"choices": [{"message": {"content": "partial answer"}, "finish_reason": None}],
                                               "error": {"message": "upstream exploded", "code": 500}})
            else:
                result = ok_result("final answer")
            return user, SimpleNamespace(type_id="llama", source_id=1), "offline", "offline", result

        invoke = self.capturing(payloads, behavior)
        with patch.object(runtime, "MAX_COMPLETIONS", 2), patch.object(runtime, "_RESUME_BACKOFF", (0.0, 0.0, 0.0, 0.0)), \
             patch.object(runtime, "execute_tool", AsyncMock(return_value=("hits", {"name": "web_search", "status": "done"}))):
            self.worker(self.run, invoke)
        self.assertEqual(store.get_run(1, self.run["id"])["status"], "completed")
        failed = payloads[1]["messages"]
        self.assertEqual(failed[-1], {"role": "user", "content": NUDGE})
        retried = payloads[2]["messages"]
        # Old nudge stays in place byte-identical; only the suffix grows.
        self.assertEqual(retried[:len(failed)], failed)
        self.assertEqual(retried[len(failed):], [
            {"role": "assistant", "content": "partial answer"},
            {"role": "user", "content": runtime._RESUME_NOTE},
            {"role": "user", "content": NUDGE},
        ])
        durable = self.provider_messages()
        self.assertEqual([m for m in durable if m.get("content") == NUDGE],
                         [{"role": "user", "content": NUDGE}] * 2)


if __name__ == "__main__":
    unittest.main()
