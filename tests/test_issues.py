"""Bug-report queue and triage pipeline; all database writes use a temporary DB."""
from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import db, issues, issues_pipeline


class IssuesQueueTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="fuse-issues-")
        self.addCleanup(self.tmp.cleanup)
        self.conn = sqlite3.connect(str(Path(self.tmp.name) / "test.db"), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        db._init(self.conn)
        self.addCleanup(self.conn.close)
        patcher = patch.object(db, "connect", return_value=self.conn)
        patcher.start()
        self.addCleanup(patcher.stop)
        issues.init_schema()

    def _submit(self, summary="Send button stops responding after switching models", **kwargs):
        return issues.submit(summary=summary, **kwargs)

    def test_submit_assigns_ref_and_status(self):
        record = self._submit(
            expected="It should switch",
            page="/chat",
            view="chat",
            user_agent="Mozilla/5.0 Test",
            context={"viewport": "1000x800", "recent_errors": []},
        )
        row = issues.get(record["id"])
        self.assertTrue(row["ref"].startswith("F-"))
        self.assertEqual(row["status"], "new")
        self.assertEqual(row["attempts"], 0)
        self.assertEqual(row["page"], "/chat")
        self.assertEqual(row["context"]["viewport"], "1000x800")
        # Refs are unique.
        other = self._submit(summary="Another report")
        self.assertNotEqual(other["ref"], record["ref"])

    def test_short_summaries_are_rejected(self):
        self.assertIsNone(issues.submit(summary="hi"))
        self.assertIsNone(issues.submit(summary="   "))

    def test_fields_are_clipped(self):
        record = self._submit(summary="x" * 9_000, expected="e" * 9_000, page="p" * 9_000, user_agent="u" * 9_000)
        row = issues.get(record["id"])
        self.assertEqual(len(row["summary"]), issues.SUMMARY_MAX)
        self.assertEqual(len(row["expected"]), issues.EXPECTED_MAX)
        self.assertEqual(len(row["page"]), issues.PAGE_MAX)
        self.assertEqual(len(row["user_agent"]), issues.UA_MAX)

    def test_claim_and_release_cycle(self):
        record = self._submit()
        self.assertTrue(issues.claim(record["id"]))
        self.assertEqual(issues.get(record["id"])["status"], "triaging")
        self.assertEqual(issues.get(record["id"])["attempts"], 0)
        # A second claim must not double-count.
        self.assertFalse(issues.claim(record["id"]))
        issues.release(record["id"])
        row = issues.get(record["id"])
        self.assertEqual(row["status"], "new")
        self.assertEqual(row["attempts"], 0)
        issues.claim(record["id"])
        issues.release(record["id"], failed_attempt=True)
        self.assertEqual(issues.get(record["id"])["attempts"], 1)

    def test_pending_skips_capped_attempts(self):
        record = self._submit()
        for _ in range(issues_pipeline.settings.issues_pipeline_max_attempts):
            issues.claim(record["id"])
            issues.release(record["id"], failed_attempt=True)
        self.assertEqual(issues.pending(5, max_attempts=issues_pipeline.settings.issues_pipeline_max_attempts), [])
        fresh = self._submit(summary="Fresh report from another surface")
        self.assertEqual([r["id"] for r in issues.pending(5)], [fresh["id"]])

    def test_triage_write_and_status_guards(self):
        record = self._submit()
        self.assertFalse(issues.update_status(record["id"], "bogus"))
        # set_triage is a CAS out of 'triaging': an unclaimed (or already
        # decided) report must not accept a pipeline verdict.
        self.assertFalse(issues.set_triage(record["id"], {"verdict": "bug"}, status="confirmed"))
        self.assertTrue(issues.claim(record["id"]))
        self.assertTrue(issues.set_triage(record["id"], {"verdict": "bug"}, status="confirmed"))
        row = issues.get(record["id"])
        self.assertEqual(row["status"], "confirmed")
        self.assertEqual(row["triage"]["verdict"], "bug")
        # A terminal row refuses further triage writes (admin decision wins).
        self.assertFalse(issues.set_triage(record["id"], {"verdict": "need_info"}, status="triaged"))
        # set_triage falls back when the status is not a lifecycle name.
        other = self._submit(summary="Another report")
        self.assertTrue(issues.claim(other["id"]))
        self.assertTrue(issues.set_triage(other["id"], {"verdict": "need_info"}, status="wat"))
        self.assertEqual(issues.get(other["id"])["status"], "triaged")

    def test_analysis_pack_shape(self):
        self._submit(page="/chat", view="chat")
        self._submit(summary="Second report from the settings page", page="/settings", view="settings")
        pack = issues.analysis_pack(limit=10)
        self.assertIn("guide", pack)
        self.assertEqual(pack["summary"]["total"], 2)
        self.assertEqual(len(pack["recent"]), 2)
        self.assertIn("context", pack["recent"][0])


class TriagePipelineTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="fuse-pipeline-")
        self.addCleanup(self.tmp.cleanup)
        self.conn = sqlite3.connect(str(Path(self.tmp.name) / "test.db"), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        db._init(self.conn)
        self.addCleanup(self.conn.close)
        patcher = patch.object(db, "connect", return_value=self.conn)
        patcher.start()
        self.addCleanup(patcher.stop)
        issues.init_schema()

    async def test_disabled_pipeline_is_a_noop(self):
        issues.submit(summary="Queue stays untouched while disabled")
        with patch.object(issues_pipeline.settings, "issues_pipeline_enabled", False):
            await issues_pipeline.tick()
        self.assertEqual(issues.get(1)["status"], "new")

    async def test_bug_verdict_confirms_with_plan(self):
        record = issues.submit(summary="Composer loses the draft when the model picker is used")
        verdict = {
            "verdict": "bug",
            "category": "chat",
            "severity": "medium",
            "confidence": 0.8,
            "duplicate_of": None,
            "summary": "Composer draft lost on model switch",
            "hypothesis": "state reset in picker handler",
            "followups": [],
            "plan": ["fix chat.js draft persistence"],
            "affected_files": ["static/chat.js"],
        }
        source = MagicMock()
        source.chat = AsyncMock(
            return_value=MagicMock(body={"choices": [{"message": {"content": json.dumps(verdict)}}]})
        )
        with patch.object(issues_pipeline.settings, "issues_pipeline_enabled", True), patch(
            "app.sources.pick", AsyncMock(return_value=(source, "test-model"))
        ):
            await issues_pipeline.tick()
        row = issues.get(record["id"])
        self.assertEqual(row["status"], "confirmed")
        self.assertEqual(row["triage"]["plan"], ["fix chat.js draft persistence"])
        self.assertEqual(source.chat.call_args[0][0]["model"], "test-model")

    async def test_duplicate_verdict_and_bad_json_releases(self):
        older = issues.submit(summary="Composer loses the draft when the model picker is used")
        newer = issues.submit(summary="Draft disappears after switching models in the picker")
        issues.update_status(older["id"], "confirmed")
        verdict = {"verdict": "duplicate", "duplicate_of": issues.get(older["id"])["ref"]}
        source = MagicMock()
        source.chat = AsyncMock(
            return_value=MagicMock(body={"choices": [{"message": {"content": json.dumps(verdict)}}]})
        )
        with patch.object(issues_pipeline.settings, "issues_pipeline_enabled", True), patch(
            "app.sources.pick", AsyncMock(return_value=(source, "test-model"))
        ):
            await issues_pipeline.tick()
        self.assertEqual(issues.get(newer["id"])["status"], "duplicate")
        self.assertEqual(issues.get(newer["id"])["triage"]["duplicate_of"], issues.get(older["id"])["ref"])

        third = issues.submit(summary="A report the model cannot answer in JSON")
        source.chat = AsyncMock(return_value=MagicMock(body={"choices": [{"message": {"content": "no json here"}}]}))
        with patch.object(issues_pipeline.settings, "issues_pipeline_enabled", True), patch(
            "app.sources.pick", AsyncMock(return_value=(source, "test-model"))
        ):
            await issues_pipeline.tick()
        row = issues.get(third["id"])
        self.assertEqual(row["status"], "new")
        self.assertEqual(row["attempts"], 1)

    async def test_missing_model_releases_report(self):
        record = issues.submit(summary="No model is available for triage")
        with patch.object(issues_pipeline.settings, "issues_pipeline_enabled", True), patch(
            "app.sources.pick", AsyncMock(return_value=None)
        ):
            await issues_pipeline.tick()
        row = issues.get(record["id"])
        self.assertEqual(row["status"], "new")
        self.assertEqual(row["attempts"], 0)


if __name__ == "__main__":
    unittest.main()
