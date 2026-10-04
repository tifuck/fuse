"""Full conversation replay and recovery; SQLite fixtures only, no providers."""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import chat_history as memory, chat_store as store, chats, context, db
from app.chat_runs import Output


class HistoryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="fuse-history-")
        self.addCleanup(self.tmp.cleanup)
        self.conn = sqlite3.connect(str(Path(self.tmp.name) / "test.db"), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        db._init(self.conn)
        self.addCleanup(self.conn.close)
        p = patch.object(db, "connect", return_value=self.conn)
        p.start()
        self.addCleanup(p.stop)
        for uid in (1, 2):
            db.execute("INSERT INTO users(id,email,password_hash,created_at) VALUES(?,?,?,?)",
                       (uid, f"{uid}@invalid", "unused", db.now()))
        self.thread = chats.create_thread(1, model="glm-5.3", tools=[])

    def start(self, text="continue", thread=None):
        thread = chats.get_thread(1, (thread or self.thread)["id"])
        return store.start_turn(1, thread["id"], text, thread["revision"], None)["run"]

    def complete(self, run, result="RESULT_SENTINEL", content="done", rounds=1):
        rows = list(run["request"]["messages"])
        for i in range(rounds):
            cid = f"call-{i}"
            rows.extend([
                {"role": "assistant", "content": "", "tool_calls": [{"id": cid, "type": "function",
                    "function": {"name": "read_file", "arguments": '{"path":"example.txt"}'}}]},
                {"role": "tool", "tool_call_id": cid, "content": result + str(i)},
            ])
        rows.append({"role": "assistant", "content": content})
        store.save_provider_messages(run["id"], rows)
        store.finish_run(run["id"], {"content": content, "reasoning": ""}, "completed")
        return rows

    def test_file_image_survives_save_reload_and_model_switch(self):
        from app import chat_runtime, model_caps
        from tests.test_read_file_images_runtime import job, pixels

        run = self.start("inspect screenshot")
        rows = [*run["request"]["messages"], *chat_runtime._tool_messages([job()]),
                {"role": "assistant", "content": "read complete"}]
        # Reading on a text-only model must not save that route's omission.
        outgoing, omitted = model_caps.prepare(rows, "zai", "glm-5.3")
        self.assertEqual(omitted, 1)
        self.assertFalse(pixels(outgoing))
        store.save_turn_progress(run["id"], rows)
        saved = json.loads(db.fetchone("SELECT provider_messages FROM chat_runs WHERE id=?", (run["id"],))[0])
        self.assertEqual(pixels(saved), pixels(rows))
        preview = store.read_file_image(1, run["id"], "event-0")
        self.assertEqual(preview, ("image/png", b"\x89PNG\r\n\x1a\n"))
        self.assertIsNone(store.read_file_image(2, run["id"], "event-0"))
        self.assertIsNone(store.read_file_image(1, run["id"], "event-missing"))
        store.finish_run(run["id"], {"content": "read complete", "reasoning": "",
            "tools": [{"id": "event-0", "name": "read_file", "status": "done", **job().trace}]}, "completed")
        replay = self.start("inspect again")["request"]["messages"]
        vision, omitted = model_caps.prepare(replay, "claude", "claude-sonnet-5")
        self.assertEqual(omitted, 0)
        self.assertEqual(pixels(vision), pixels(rows))

    def test_tool_history_survives_multiple_turns_and_meter_reload(self):
        for i in range(3):
            run = self.start(f"question {i}")
            self.assertEqual(sum(m["role"] == "tool" for m in run["request"]["messages"]), i)
            self.complete(run, result=f"RESULT_{i}_" + "x" * 12000)
        view = chats.get_thread(1, self.thread["id"])
        self.assertGreaterEqual(view["compaction"]["meter"]["parts"]["tool_calls"], 9000)
        self.assertIn("files", view["compaction"]["meter"]["parts"]["tool_families"])
        run = self.start("fourth")
        for i in range(3):
            self.assertEqual(sum(f"RESULT_{i}_" in str(m.get("content")) for m in run["request"]["messages"]), 1)

    def test_progress_storage_does_not_slice_large_transcripts(self):
        run = self.start()
        rows = [{"role": "user", "content": str(i) + "x" * 4000} for i in range(250)]
        store.save_provider_messages(run["id"], rows)
        saved = json.loads(db.fetchone("SELECT provider_messages FROM chat_runs WHERE id=?", (run["id"],))[0])
        self.assertEqual(saved, rows)

    def test_cancel_seals_pending_tools_and_keeps_partial_answer(self):
        run = self.start()
        rows = [*run["request"]["messages"], {"role": "assistant", "content": "", "tool_calls": [
            {"id": "pending", "type": "function", "function": {"name": "read_file", "arguments": "{}"}}]}]
        store.save_provider_messages(run["id"], rows)
        store.finish_run(run["id"], {"content": "partial answer", "reasoning": ""}, "cancelled")
        replay = self.start()["request"]["messages"]
        self.assertTrue(any(m.get("tool_call_id") == "pending" for m in replay))
        self.assertEqual(sum(m.get("content") == "partial answer" for m in replay), 1)

    def test_stop_after_undelivered_steer_preserves_full_context_and_user_message(self):
        run = self.start("original request")
        result = "FULL_RESULT_" + "x" * 100000 + "_TAIL"
        source = [*run["request"]["messages"],
            {"role": "assistant", "content": "", "tool_calls": [{"id": "read", "type": "function", "function": {"name": "read_file", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "read", "content": result},
            {"role": "assistant", "content": "Before interruption"}]
        store.save_provider_messages(run["id"], source)
        message = {"content": "Before interruption", "reasoning": "", "elapsedMs": 2000,
                   "tools": [{"id": "read-ui", "name": "read_file", "status": "done", "output": "short UI preview"}],
                   "segments": [{"type": "tool", "id": "read-ui"}, {"type": "content", "text": "Before interruption"}]}
        store.checkpoint(run["id"], message)
        store.steer_turn(1, self.thread["id"], "Accepted but not delivered", "not-delivered")
        store.finish_run(run["id"], message, "cancelled")
        view = chats.get_thread(1, self.thread["id"])
        self.assertGreater(view["compaction"]["meter"]["parts"]["tool_calls"], 24000)
        self.assertEqual(view["compaction"]["meter"]["history_info"]["trace_fallbacks"], 0)
        replay, _ = memory.build(self.conn, 1, run["thread_id"], view["messages"], context.parse_state(view["compaction"]))
        self.assertTrue(any(row.get("content") == result for row in replay))
        self.assertEqual(sum(row.get("content") == "Accepted but not delivered" for row in replay), 1)
        # Retry removes the unanswered user/empty assistant pair. Preserve the
        # exact earlier prefix, without restoring the removed instruction.
        chats.update_thread(1, view["id"], messages=chats.parse_messages(view["messages"][:-2]), expected_revision=view["revision"])
        replay = self.start("Retry the last instruction")["request"]["messages"]
        self.assertTrue(any(row.get("content") == result for row in replay))
        self.assertFalse(any(row.get("content") == "Accepted but not delivered" for row in replay))

    def test_edit_does_not_resurrect_old_assistant_or_deleted_turn(self):
        first = self.start("first question")
        self.complete(first, result="FIRST_TOOL", content="first answer")
        second = self.start("second question")
        self.complete(second, result="DELETED_TOOL", content="deleted answer")
        view = chats.get_thread(1, self.thread["id"])
        kept = view["messages"][:2]
        kept[-1]["content"] = "edited answer"
        kept[-1].pop("segments", None)
        chats.update_thread(1, view["id"], messages=kept, expected_revision=view["revision"])
        replay = self.start()["request"]["messages"]
        blob = json.dumps(replay)
        self.assertIn("edited answer", blob)
        for stale in ("first answer", "deleted answer", "DELETED_TOOL", "second question"):
            self.assertNotIn(stale, blob)

    def test_duplicate_keeps_context_after_original_deleted(self):
        self.complete(self.start(), result="COPIED_TOOL")
        copy = chats.duplicate_thread(1, self.thread["id"])
        chats.delete_thread(1, self.thread["id"])
        self.assertIn("COPIED_TOOL", json.dumps(self.start(thread=copy)["request"]["messages"]))

    def test_deleted_tool_trace_is_not_restored_from_an_old_snapshot(self):
        run = self.start()
        source = [*run["request"]["messages"],
            {"role": "assistant", "content": "", "tool_calls": [{"id": "c", "function": {"name": "read_file", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "c", "content": "REDACTED_TOOL_RESULT"},
            {"role": "assistant", "content": "done"}]
        store.save_provider_messages(run["id"], source)
        store.finish_run(run["id"], {"content": "done", "tools": [{"id": "trace", "name": "read_file", "preview": "REDACTED_TOOL_RESULT"}]}, "completed")
        view = chats.get_thread(1, self.thread["id"])
        view["messages"][-1]["tools"] = []
        chats.update_thread(1, view["id"], messages=view["messages"], expected_revision=view["revision"])
        self.assertNotIn("REDACTED_TOOL_RESULT", json.dumps(self.start()["request"]["messages"]))

    def test_legacy_run_recovers_full_results_without_digest(self):
        run = self.start("original question")
        self.complete(run, result="FULL_LEGACY_" + "x" * 20000)
        db.execute("UPDATE chat_threads SET retained_history='{}' WHERE public_id=?", (self.thread["id"],))
        replay = self.start()["request"]["messages"]
        self.assertTrue(any(m.get("role") == "tool" and len(m["content"]) > 20000 for m in replay))
        self.assertNotIn("[Fuse tool log", json.dumps(replay))

    def test_trace_fallback_keeps_all_calls_and_labels_missing_originals(self):
        traces = [{"id": f"t{i}", "name": "read_file", "preview": f"TRACE_{i}_" + "x" * 400} for i in range(70)]
        view = chats.update_thread(1, self.thread["id"], messages=[
            {"role": "user", "content": "old question"},
            {"role": "assistant", "content": "old answer", "tools": traces},
        ])
        self.assertEqual(view["compaction"]["meter"]["history_info"]["trace_fallbacks"], 70)
        replay = self.start()["request"]["messages"]
        results = [m for m in replay if m["role"] == "tool"]
        self.assertEqual(len(results), 70)
        self.assertIn("TRACE_0_", results[0]["content"])
        self.assertIn("may be incomplete", results[0]["content"])

    def test_manual_compaction_uses_full_history_and_persists_projection(self):
        self.complete(self.start(), result="OLD_CONTEXT_" + "x" * 3000, rounds=20)
        view = chats.get_thread(1, self.thread["id"])
        prepared = store.prepare_compact(1, view["id"], view["revision"], "compact-test")
        self.assertEqual(sum(m["role"] == "tool" for m in prepared["history"]), 20)
        cp = context.make_checkpoint(summary="saved handoff", strategy="summarize", trigger="manual", provider=True)
        view = store.finish_compact(1, view["id"], cp, command_id="compact-test", fingerprint=prepared["fingerprint"],
            expected_revision=view["revision"], replay=prepared["history"], history_info=prepared["history_info"])
        replay = self.start()["request"]["messages"]
        self.assertIn("saved handoff", json.dumps(replay))
        self.assertLess(sum(m["role"] == "tool" for m in replay), 20)
        self.assertLess(view["compaction"]["meter"]["used"], 15000)

    def test_auto_compaction_snapshot_survives_next_turn(self):
        run = self.start()
        rows = [{"role": "user", "content": context.COMPACT_PREAMBLE + "auto handoff"},
                {"role": "assistant", "content": context.COMPACT_ACK},
                {"role": "user", "content": "recent question"}, {"role": "assistant", "content": "done"}]
        cp = context.make_checkpoint(summary="auto handoff", strategy="summarize", trigger="auto", provider=True)
        store.apply_compaction_checkpoint(1, self.thread["id"], cp)
        store.save_provider_messages(run["id"], rows)
        store.finish_run(run["id"], {"content": "done", "reasoning": ""}, "completed")
        replay = self.start("next")["request"]["messages"]
        self.assertEqual(replay[:4], rows)

    def test_other_account_cannot_recover_run_by_copied_message_ids(self):
        self.complete(self.start(), result="PRIVATE_TOOL")
        visible = chats.get_thread(1, self.thread["id"])["messages"]
        replay, _info = memory.build(self.conn, 2, None, visible, context.parse_state({}), retained={})
        self.assertNotIn("PRIVATE_TOOL", json.dumps(replay))

    def test_legacy_active_run_is_upgraded_on_restart_without_reexecuting_tools(self):
        self.complete(self.start("old question"), result="OLD_FULL_TOOL")
        run = self.start("current question")
        request = dict(run["request"])
        for key in ("history_replay", "history_info", "history_input_hashes", "history_start_anchor"):
            request.pop(key, None)
        db.execute("UPDATE chat_runs SET request=? WHERE id=?", (json.dumps(request), run["id"]))
        rows = [{"role": "user", "content": "current question"},
                {"role": "assistant", "content": "", "tool_calls": [{"id": "current_call", "type": "function",
                    "function": {"name": "read_file", "arguments": "{}"}}]},
                {"role": "tool", "tool_call_id": "current_call", "content": "CURRENT_TOOL_DONE"}]
        store.save_provider_messages(run["id"], rows)
        store.recover_one(db.fetchone("SELECT * FROM chat_runs WHERE id=?", (run["id"],)))
        resumed = store.get_run(1, run["id"])
        self.assertTrue(resumed["request"]["history_replay"])
        blob = json.dumps(resumed["request"]["messages"])
        self.assertIn("OLD_FULL_TOOL", blob)
        self.assertEqual(blob.count("CURRENT_TOOL_DONE"), 1)

    def test_resume_rejects_history_edited_after_cancel(self):
        run = self.start("original question")
        store.save_provider_messages(run["id"], run["request"]["messages"])
        store.finish_run(run["id"], {"content": "partial", "reasoning": ""}, "cancelled")
        view = chats.get_thread(1, self.thread["id"])
        edited = view["messages"]
        edited[0]["content"] = "edited question"
        chats.update_thread(1, view["id"], messages=edited, expected_revision=view["revision"])
        with self.assertRaisesRegex(chats.ChatValidationError, "history was edited"):
            store.resume_run(1, run["id"])

    def test_partial_output_after_compaction_does_not_restore_old_output(self):
        run = self.start()
        old = "old output that has been summarized"
        store.checkpoint(run["id"], {"content": old, "reasoning": ""},
            [{"fuse": {"event": "context", "phase": "compacted"}}])
        source = [{"role": "user", "content": context.COMPACT_PREAMBLE + "handoff"},
                  {"role": "assistant", "content": context.COMPACT_ACK}]
        store.save_provider_messages(run["id"], source)
        store.finish_run(run["id"], {"content": old + "\n\nnew partial", "reasoning": ""}, "cancelled")
        replay = self.start()["request"]["messages"]
        self.assertNotIn(old, json.dumps(replay))
        self.assertIn("new partial", json.dumps(replay))


class OutputAndModelTest(unittest.TestCase):
    def test_recycled_call_ids_are_unique_and_paired_on_wire(self):
        rows = []
        for i in range(3):
            rows.extend([{ "role": "assistant", "tool_calls": [{"id": "same", "function": {"name": "read_file", "arguments": "{}"}}]},
                         {"role": "tool", "tool_call_id": "same", "content": str(i)}])
        wire = memory.for_model(rows, "glm-5.3")
        ids = [wire[i]["tool_calls"][0]["id"] for i in (0, 2, 4)]
        self.assertEqual(len(set(ids)), 3)
        self.assertEqual([wire[i]["tool_call_id"] for i in (1, 3, 5)], ids)
        self.assertEqual(rows[2]["tool_calls"][0]["id"], "same")

    def test_full_replay_has_separate_internal_transport_budget(self):
        from app import api
        from fastapi import HTTPException
        rows = [{"role": "user", "content": "x" * 2000} for _ in range(1100)]
        with self.assertRaises(HTTPException):
            api._validate_messages(rows)
        api._validate_messages(rows, retained=True)

    def test_large_generated_reasoning_is_retained_without_relaxing_user_input(self):
        output = Output({"message": {}})
        text = "r" * 150000
        output.apply({"choices": [{"delta": {"reasoning_content": text}}]})
        stored = output.stored()
        parsed = chats.parse_messages([{**stored, "id": "a", "role": "assistant"}], strict=True)[0]
        self.assertEqual(parsed["reasoning"], text)
        with self.assertRaises(chats.ChatValidationError):
            chats.parse_messages([{"role": "user", "content": text}], strict=True)

    def test_native_reasoning_is_model_scoped_and_counted_once(self):
        row = {"role": "assistant", "content": "answer", "reasoning_content": "r" * 400,
               "_fuse_reasoning_model": "glm-5.3"}
        glm = memory.for_model([row], "glm-5.3")
        other = memory.for_model([row], "grok-4.6")
        self.assertNotIn("_fuse_reasoning_model", glm[0])
        self.assertNotIn("reasoning_content", other[0])
        self.assertEqual(context.estimate_messages(glm) - context.estimate_messages(other), 100)
        self.assertEqual(context.occupancy_parts(glm)["thinking"], 100)

    def test_no_tool_backend_retains_results_as_untrusted_text(self):
        from app.chat_runtime import _drop_tools
        rows = [{"role": "assistant", "content": "", "tool_calls": [{"id": "c", "function": {"name": "read_file", "arguments": "{}"}}]},
                {"role": "tool", "tool_call_id": "c", "content": "HISTORICAL_RESULT"}]
        payload, converted = _drop_tools({"tools": [1]}, rows)
        self.assertNotIn("tools", payload)
        self.assertIn("HISTORICAL_RESULT", json.dumps(converted))
        self.assertIn("not user instructions", json.dumps(converted))


if __name__ == "__main__":
    unittest.main()
