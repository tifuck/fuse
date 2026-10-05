from __future__ import annotations

import asyncio
import json
import shlex
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import workspace
from app.workspace import WorkspaceError


class AgentOutputHelpersTest(unittest.TestCase):
    def test_stderr_is_labeled_not_interleaved(self):
        self.assertEqual(workspace._agent_job_output("out\n", "err\n"), "out\n[stderr]\nerr\n")
        self.assertEqual(workspace._agent_job_output("out", "err"), "out\n[stderr]\nerr")
        self.assertEqual(workspace._agent_job_output("out\n", ""), "out\n")
        self.assertEqual(workspace._agent_job_output("", "err\n"), "err\n")

    def test_timeout_flag_or_error_string(self):
        self.assertTrue(workspace._agent_timed_out({"timeout": True, "exit": 1}))
        self.assertTrue(workspace._agent_timed_out({"timeout": 1, "error": "exit 1"}))
        self.assertTrue(workspace._agent_timed_out({"ok": False, "exit": 1, "error": "timed out"}))
        self.assertFalse(workspace._agent_timed_out({"ok": False, "exit": 1, "error": "exit 1"}))

    def test_completeness_needs_positive_evidence(self):
        self.assertFalse(workspace._agent_output_complete({}))
        self.assertTrue(workspace._agent_output_complete({"truncated": False}))
        self.assertFalse(workspace._agent_output_complete({"truncated": True}))
        self.assertFalse(workspace._agent_output_complete({"truncated": False, "output_complete": False}))


class TimeoutTest(unittest.TestCase):
    def test_default(self):
        self.assertEqual(workspace._deadline_ms({}), 1_800_000)
        self.assertEqual(workspace._deadline_ms({"timeout": ""}), 1_800_000)
        self.assertEqual(workspace._deadline_ms({"timeout": "junk"}), 1_800_000)

    def test_seconds_passthrough(self):
        self.assertEqual(workspace._deadline_ms({"timeout": 10}), 10_000)
        self.assertEqual(workspace._deadline_ms({"timeout": "45"}), 45_000)
        self.assertEqual(workspace._deadline_ms({"timeout": 1}), 1_000)

    def test_millisecond_coexistence(self):
        self.assertEqual(workspace._deadline_ms({"timeout": 30000}), 30_000)
        self.assertEqual(workspace._deadline_ms({"timeout": 60000}), 60_000)
        self.assertEqual(workspace._deadline_ms({"timeout": 5000}), 5_000)
        self.assertEqual(workspace._deadline_ms({"timeout": 3_600_000}), 3_600_000)
        self.assertEqual(workspace._deadline_ms({"timeout": 9_999_999}), 3_600_000)


class ReadRenderTest(unittest.TestCase):
    def test_line_numbers_and_continue_footer(self):
        out = workspace._read_render(["a", "b"], 1, total=5, more=True, max_chars=12000)
        self.assertEqual(out, "1: a\n2: b\n\n(Showing lines 1-2 of 5. Use offset=3 to continue.)")

    def test_end_of_file_footer(self):
        out = workspace._read_render(["a", "b"], 1, total=2, more=False, max_chars=12000)
        self.assertEqual(out, "1: a\n2: b\n\n(End of file — 2 lines.)")

    def test_unknown_total_continue(self):
        out = workspace._read_render(["a"] * 400, 1, total=None, more=True, max_chars=12000)
        self.assertIn("(Showing lines 1-400. Use offset=401 to continue.)", out)

    def test_empty_file(self):
        out = workspace._read_render([], 1, total=0, more=False, max_chars=12000)
        self.assertIn("(End of file — 0 lines.)", out)

    def test_offset_past_end(self):
        out = workspace._read_render([], 401, total=None, more=False, max_chars=12000)
        self.assertIn("(No lines at offset 401.)", out)

    def test_long_line_truncated_in_place(self):
        out = workspace._read_render(["z" * 5000, "tail"], 1, total=2, more=False, max_chars=60000)
        self.assertIn("... (line truncated to 2000 chars)", out)
        self.assertIn("2: tail", out)
        self.assertIn("(End of file — 2 lines.)", out)

    def test_char_clip_note(self):
        lines = ["x" * 200 for _ in range(200)]
        out = workspace._read_render(lines, 1, total=200, more=False, max_chars=5000)
        self.assertTrue(out.startswith("1: xxx"))
        self.assertIn("(Output capped at 4KB. Showing lines 1-", out)
        self.assertNotIn("... (line truncated", out)


class GrepRenderTest(unittest.TestCase):
    def test_rg_json_rerendered(self):
        events = "\n".join(
            [
                json.dumps({"type": "begin", "data": {"path": {"text": "x.py"}}}),
                json.dumps(
                    {
                        "type": "match",
                        "data": {
                            "path": {"text": "src/x.py"},
                            "lines": {"text": "hello\n"},
                            "line_number": 3,
                        },
                    }
                ),
                json.dumps({"type": "end", "data": {"elapsed": 1}}),
            ]
        )
        self.assertEqual(workspace._render_grep_output(events), "src/x.py:3:hello")

    def test_human_output_passthrough(self):
        self.assertEqual(workspace._render_grep_output("a.py:4:hi"), "a.py:4:hi")
        self.assertEqual(workspace._render_grep_output(""), "")


class GrepSearchCmdTest(unittest.TestCase):
    def test_never_ors_rg_into_grep(self):
        cmd = workspace._grep_search_cmd("TODO", ".", include="", max_matches=50)
        self.assertNotIn("|| grep", cmd)
        self.assertIn("command -v rg", cmd)
        self.assertIn("head -n 50", cmd)
        self.assertIn("-m 50", cmd)
        self.assertNotIn("-m 20", cmd)
        self.assertIn("--max-filesize 1M", cmd)
        self.assertIn("!node_modules", cmd)
        # The no-rg walk prunes the same vendor dirs.
        self.assertIn("-name venv", cmd)

    def test_size_cap_only_for_unfiltered_walks(self):
        # A file picked out by include must be searched even when it is large:
        # the cap silently turned "grep this one file" into "(no matches)".
        self.assertIn("--max-filesize 1M", workspace._grep_search_cmd("x", "."))
        self.assertNotIn("--max-filesize", workspace._grep_search_cmd("x", ".", include="big.py"))
        self.assertNotIn("--max-filesize", workspace._grep_search_cmd("x", ".", include="*.py"))

    def test_connector_clip_marker_is_not_a_result(self):
        raw = "a.py:1:one\na.py:2:tw\n[fuse-agent: output truncated]\n"
        out, clipped = workspace._strip_connector_clip(raw, False)
        self.assertTrue(clipped)
        self.assertEqual(out, "a.py:1:one")
        self.assertEqual(workspace._strip_connector_clip("a.py:1:x", False), ("a.py:1:x", False))

    def test_include_over_cap_names_the_limit(self):
        with self.assertRaises(WorkspaceError) as ctx:
            workspace._grep_include_arg({"include": "a" * 201})
        self.assertIn("200", ctx.exception.detail)

    def test_include_and_glob_alias(self):
        cmd = workspace._grep_search_cmd("x", ".", include="*.py", max_matches=20)
        # A file-name include is an rg type filter, not --glob: a positive
        # --glob overrides .gitignore.
        self.assertIn("--type-add 'fuseinc:*.py' -t fuseinc", cmd)
        self.assertIn("--glob 'web/*.py'", workspace._grep_search_cmd("x", ".", include="web/*.py"))
        self.assertIn("head -n 20", cmd)
        self.assertEqual(workspace._grep_include_arg({"include": "*.ts"}), "*.ts")
        self.assertEqual(workspace._grep_include_arg({"glob": "*.go"}), "*.go")
        self.assertEqual(workspace._grep_include_arg({"include": "*.ts", "glob": "*.go"}), "*.ts")

    def test_max_matches_aliases_and_clamp(self):
        self.assertEqual(workspace._grep_max_matches({}), 50)
        self.assertEqual(workspace._grep_max_matches({"max_matches": 12}), 12)
        self.assertEqual(workspace._grep_max_matches({"head_limit": 8}), 50)
        self.assertEqual(workspace._grep_max_matches({"max_matches": 999}), 200)
        # 0 from a model that fills every property means unset, not one match.
        self.assertEqual(workspace._grep_max_matches({"max_matches": 0}), 50)

    def test_long_alternation_is_quoted_not_rejected(self):
        pattern = "def download_file_logic|def download_folder_logic|def check_folder_access|" + ("def _can_access|" * 40)
        self.assertGreater(len(pattern), 200)
        self.assertLessEqual(len(pattern), workspace._GREP_MAX_PATTERN)
        cmd = workspace._grep_search_cmd(pattern, ".")
        self.assertIn(shlex.quote(pattern), cmd)

    def test_skip_globs_keep_explicit_vendor_path(self):
        cmd = workspace._grep_search_cmd("x", "/repo/node_modules")
        self.assertNotIn("!node_modules", cmd)
        self.assertIn("!venv", cmd)

    def test_filter_include(self):
        out = "a.py:1:x\nb.ts:2:x\n"
        self.assertEqual(workspace._filter_grep_include(out, "*.py"), "a.py:1:x")

    def test_exec_fail_prefers_stderr_over_exit(self):
        msg = workspace._exec_fail_detail(
            {
                "ok": False,
                "exit": 2,
                "out": "",
                "err": "rg: regex parse error:\n    print(\nerror: unclosed group\n",
                "error": "exit",
            },
            "grep failed",
        )
        self.assertIn("unclosed group", msg)
        self.assertIn("exit 2", msg)
        self.assertNotEqual(msg, "exit")
        self.assertEqual(
            workspace._exec_fail_detail({"ok": False, "exit": 2, "err": "", "error": "exit"}, "grep failed"),
            "grep failed (exit 2)",
        )

    def test_exec_fail_uses_stdout_when_stderr_empty(self):
        msg = workspace._exec_fail_detail(
            {"ok": False, "exit": 2, "out": "fatal: not a git repository\n", "err": "", "error": "exit"},
            "failed",
        )
        self.assertIn("not a git repository", msg)
        self.assertIn("exit 2", msg)

    def test_enrich_rewrites_bare_exit(self):
        data = workspace._enrich_agent_data(
            {"ok": False, "exit": 2, "out": "", "err": "unclosed group\n", "error": "exit"},
            op="grep",
        )
        self.assertIn("unclosed group", data["error"])
        self.assertNotEqual(data["error"], "exit")
        kept = workspace._enrich_agent_data(
            {"ok": False, "exit": 1, "out": "", "err": "", "error": "read failed"},
            op="read",
        )
        self.assertEqual(kept["error"], "read failed")

    def test_glob_cmd_uses_rg_files_and_head(self):
        cmd = workspace._glob_search_cmd("**/*.py", ".")
        self.assertIn("rg --files", cmd)
        self.assertIn("head -n 400", cmd)
        self.assertNotIn("|| grep", cmd)
        self.assertNotIn("globstar", cmd)
        self.assertIn("find ", cmd)
        self.assertIn("-prune", cmd)


class ApplyReplaceTest(unittest.TestCase):
    def test_exact_unique(self):
        updated, count = workspace._apply_replace("abc", "b", "X", False)
        self.assertEqual(updated, "aXc")
        self.assertEqual(count, 1)

    def test_not_unique_without_replace_all(self):
        with self.assertRaises(WorkspaceError) as ctx:
            workspace._apply_replace("a\na\n", "a", "x", False)
        self.assertIn("not unique", ctx.exception.detail)

    def test_replace_all(self):
        updated, count = workspace._apply_replace("a\na\n", "a", "x", True)
        self.assertEqual(updated, "x\nx\n")
        self.assertEqual(count, 2)

    def test_not_found_teaching_error(self):
        with self.assertRaises(WorkspaceError) as ctx:
            workspace._apply_replace("a\nb\n", "zzz", "x", False)
        self.assertIn("not found", ctx.exception.detail)
        self.assertIn("line-number prefix", ctx.exception.detail)

    def test_line_trimmed_fallback(self):
        text = "def f():\n    pass\n"
        with self.assertRaises(WorkspaceError):
            workspace._apply_replace(text, "def f():\n\tpass\n", "X", False)

    def test_whitespace_normalized_fallback(self):
        text = "alpha   beta\n"
        with self.assertRaises(WorkspaceError):
            workspace._apply_replace(text, "alpha beta", "X", False)

    def test_ambiguous_fallback_rejected(self):
        text = "  a\nb\n  a\n"
        # "a" occurs as an exact substring twice → explicit not-unique error.
        with self.assertRaises(WorkspaceError) as ctx:
            workspace._apply_replace(text, "a", "x", False)
        self.assertIn("not unique", ctx.exception.detail)

    def test_crlf_file_matches_lf_old_string(self):
        text = "alpha\r\nbeta\r\n"
        updated, count = workspace._apply_replace(text, "alpha\nbeta", "one\ntwo", False)
        self.assertEqual(updated, "one\r\ntwo\r\n")
        self.assertEqual(count, 1)

    def test_crlf_fallback_respects_replace_all(self):
        text = "a\r\nb\r\na\r\n"
        updated, count = workspace._apply_replace(text, "a\n", "X\n", True)
        self.assertEqual(updated, "X\r\nb\r\nX\r\n")
        self.assertEqual(count, 2)

    def test_crlf_fallback_uniqueness_still_enforced(self):
        text = "a\r\nb\r\na\r\n"
        with self.assertRaises(WorkspaceError) as ctx:
            workspace._apply_replace(text, "a\n", "x", False)
        self.assertIn("not unique", ctx.exception.detail)

    def test_crlf_fallback_not_used_when_old_has_cr(self):
        # An old_string that already carries \r is used verbatim; no double conversion.
        updated, count = workspace._apply_replace("alpha\r\nbeta\r\n", "alpha\r\nbeta", "x\r\ny", False)
        self.assertEqual(updated, "x\r\ny\r\n")
        # LF file with a CR-carrying old_string: plain not-found, no CRLF hint.
        with self.assertRaises(WorkspaceError) as ctx:
            workspace._apply_replace("alpha\nbeta\n", "alpha\r\nbeta", "x", False)
        self.assertIn("not found", ctx.exception.detail)
        self.assertNotIn("CRLF", ctx.exception.detail)

    def test_not_found_on_crlf_file_mentions_line_endings(self):
        with self.assertRaises(WorkspaceError) as ctx:
            workspace._apply_replace("alpha\r\nbeta\r\n", "zzz\nqqq", "x", False)
        self.assertIn("CRLF", ctx.exception.detail)

class FakeAgent:
    """Scripted _agent_call stand-in for connector-path tests."""

    def __init__(self, responses):
        # responses: list of (op_match, result dict or callable(args)->dict)
        self.responses = list(responses)
        self.calls = []

    async def __call__(self, user_id, host_id, op, args, seconds=30):
        self.calls.append((op, dict(args)))
        for i, (match, result) in enumerate(self.responses):
            if op == match:
                if callable(result):
                    return result(args)
                return result
        return {"ok": True, "exit": 0, "out": "", "err": "", "error": ""}


class AgentPathTest(unittest.TestCase):
    def _run(self, coro):
        return asyncio.run(coro)

    def test_read_renders_line_numbers_and_footer(self):
        fake = FakeAgent([("read", {"ok": True, "exit": 0, "out": "alpha\nbeta", "err": "", "error": ""})])
        with patch.object(workspace, "_is_agent", return_value=True), patch.object(
            workspace, "_agent_call", fake
        ):
            text, rec = self._run(
                workspace._read(1, 2, "box", {"path": "/tmp/x.py"}, "/tmp")
            )
        self.assertIn("1: alpha", text)
        self.assertIn("2: beta", text)
        self.assertIn("(End of file — 2 lines.)", text)
        self.assertTrue(text.startswith("From host box:"))

    def test_read_continue_footer_at_connector_cap(self):
        body = "\n".join(f"line{i}" for i in range(1, 2001))
        fake = FakeAgent([("read", {"ok": True, "exit": 0, "out": body, "err": "", "error": ""})])
        with patch.object(workspace, "_is_agent", return_value=True), patch.object(
            workspace, "_agent_call", fake
        ):
            text, rec = self._run(
                workspace._read(1, 2, "box", {"path": "/tmp/x.py"}, "/tmp")
            )
        self.assertIn("(Showing lines 1-2000. Use offset=2001 to continue.)", text)

    def test_read_directory_hint(self):
        fake = FakeAgent(
            [
                ("read", {"ok": False, "exit": 1, "out": "", "err": "", "error": "read failed"}),
                ("list", {"ok": True, "exit": 0, "out": "a\nb", "err": "", "error": ""}),
            ]
        )
        with patch.object(workspace, "_is_agent", return_value=True), patch.object(
            workspace, "_agent_call", fake
        ):
            with self.assertRaises(WorkspaceError) as ctx:
                self._run(workspace._read(1, 2, "box", {"path": "/tmp"}, "/tmp"))
        self.assertIn("directory", ctx.exception.detail)

    def test_grep_json_events_re_rendered(self):
        events = json.dumps(
            {
                "type": "match",
                "data": {"path": {"text": "src/x.py"}, "lines": {"text": "hi\n"}, "line_number": 7},
            }
        )
        fake = FakeAgent([("grep", {"ok": True, "exit": 0, "out": events, "err": "", "error": ""})])
        with patch.object(workspace, "_is_agent", return_value=True), patch.object(
            workspace, "_agent_call", fake
        ):
            text, rec = self._run(
                workspace._grep(1, 2, "box", {"pattern": "hi", "path": "."}, "/tmp", windows=True)
            )
        self.assertIn("src/x.py:7:hi", text)

    def test_pattern_over_cap_names_the_limit(self):
        with self.assertRaises(WorkspaceError) as ctx:
            self._run(workspace._grep(1, 2, "box", {"pattern": "a|" * 2001}, "/tmp"))
        self.assertIn("2000", ctx.exception.detail)
        self.assertNotEqual(ctx.exception.detail, "invalid pattern")

    def test_grep_linux_uses_exec_not_grep_op(self):
        fake = FakeAgent([("exec", {"ok": True, "exit": 0, "out": "a.py:1:x", "err": "", "error": ""})])
        with patch.object(workspace, "_is_agent", return_value=True), patch.object(
            workspace, "_agent_call", fake
        ):
            text, rec = self._run(
                workspace._grep(1, 2, "box", {"pattern": "x", "path": "."}, "/tmp")
            )
        self.assertEqual([c[0] for c in fake.calls], ["exec"])
        cmd = fake.calls[0][1]["cmd"]
        self.assertNotIn("|| grep", cmd)
        self.assertIn("head -n 50", cmd)
        self.assertIn("a.py:1:x", text)

    def test_grep_include_routes_through_exec(self):
        fake = FakeAgent([("exec", {"ok": True, "exit": 0, "out": "a.py:1:x", "err": "", "error": ""})])
        with patch.object(workspace, "_is_agent", return_value=True), patch.object(
            workspace, "_agent_call", fake
        ):
            text, rec = self._run(
                workspace._grep(1, 2, "box", {"pattern": "x", "path": ".", "include": "*.py"}, "/tmp")
            )
        ops = [c[0] for c in fake.calls]
        self.assertEqual(ops, ["exec"])
        cmd = fake.calls[0][1]["cmd"]
        self.assertIn("-t fuseinc", cmd)
        self.assertIn("command -v rg", cmd)
        self.assertNotIn("|| grep", cmd)
        self.assertIn("a.py:1:x", text)

    def test_grep_exec_failure_surfaces_stderr(self):
        fake = FakeAgent(
            [
                (
                    "exec",
                    {
                        "ok": False,
                        "exit": 2,
                        "out": "",
                        "err": "rg: regex parse error:\nerror: unclosed group\n",
                        "error": "exit",
                    },
                )
            ]
        )
        with patch.object(workspace, "_is_agent", return_value=True), patch.object(
            workspace, "_agent_call", fake
        ):
            with self.assertRaises(WorkspaceError) as ctx:
                self._run(workspace._grep(1, 2, "box", {"pattern": "print("}, "/tmp"))
        self.assertIn("unclosed group", ctx.exception.detail)
        self.assertNotEqual(ctx.exception.detail, "exit")
        self.assertEqual(ctx.exception.extra.get("exit_code"), 2)
        self.assertIn("unclosed group", ctx.exception.extra.get("stderr", ""))

    def test_grep_tolerates_broken_pipe_write_error(self):
        # Connector execs run with SIGPIPE ignored, so grep | head ends on
        # EPIPE with exit 2 instead of SIGPIPE 141; matches already collected.
        fake = FakeAgent(
            [
                (
                    "exec",
                    {
                        "ok": False,
                        "exit": 2,
                        "out": "a.py:1:x\nb.py:2:y\n",
                        "err": "grep: write error: Broken pipe\n",
                        "error": "grep: write error: Broken pipe",
                    },
                )
            ]
        )
        with patch.object(workspace, "_is_agent", return_value=True), patch.object(
            workspace, "_agent_call", fake
        ):
            text, rec = self._run(
                workspace._grep(1, 2, "box", {"pattern": ".", "path": "."}, "/tmp")
            )
        self.assertIn("a.py:1:x", text)
        self.assertEqual(rec["status"], "done")
        self.assertEqual([c[0] for c in fake.calls], ["exec"])

    def test_grep_exit_2_without_broken_pipe_still_fails(self):
        fake = FakeAgent(
            [
                (
                    "exec",
                    {
                        "ok": False,
                        "exit": 2,
                        "out": "",
                        "err": "grep: /tmp/x: No such file or directory\n",
                        "error": "exit",
                    },
                )
            ]
        )
        with patch.object(workspace, "_is_agent", return_value=True), patch.object(
            workspace, "_agent_call", fake
        ):
            with self.assertRaises(WorkspaceError) as ctx:
                self._run(workspace._grep(1, 2, "box", {"pattern": "x", "path": "."}, "/tmp"))
        self.assertIn("No such file or directory", ctx.exception.detail)

    def test_list_broken_pipe_uses_exec_output_without_fallback(self):
        fake = FakeAgent(
            [
                (
                    "exec",
                    {
                        "ok": False,
                        "exit": 2,
                        "out": "src/\nmain.py\n",
                        "err": "ls: write error: Broken pipe\n",
                        "error": "exit",
                    },
                )
            ]
        )
        with patch.object(workspace, "_is_agent", return_value=True), patch.object(
            workspace, "_agent_call", fake
        ):
            text, rec = self._run(workspace._list(1, 2, "box", {"path": "/tmp"}, "/tmp"))
        self.assertIn("main.py", text)
        self.assertEqual([c[0] for c in fake.calls], ["exec"])

    def test_list_uses_ls_markers(self):
        fake = FakeAgent(
            [("exec", {"ok": True, "exit": 0, "out": "./\n../\nsrc/\nmain.py\n", "err": "", "error": ""})]
        )
        with patch.object(workspace, "_is_agent", return_value=True), patch.object(
            workspace, "_agent_call", fake
        ):
            text, rec = self._run(workspace._list(1, 2, "box", {"path": "/tmp"}, "/tmp"))
        cmd = fake.calls[0][1]["cmd"]
        self.assertIn("ls -1ApU", cmd)
        self.assertIn(f"head -n {workspace._LIST_SCAN_MAX + 1}", cmd)
        self.assertIn("src/", text)
        self.assertIn("main.py", text)
        self.assertNotIn("..\n", text.replace("From host box:\n", "", 1))
        self.assertNotIn("(no entries)", text)
        self.assertNotIn("showing 400", text)

    def test_list_exactly_400_is_complete(self):
        listing = "\n".join(f"f{i}" for i in range(400)) + "\n"
        fake = FakeAgent([("exec", {"ok": True, "exit": 0, "out": listing, "err": "", "error": ""})])
        with patch.object(workspace, "_is_agent", return_value=True), patch.object(
            workspace, "_agent_call", fake
        ):
            text, rec = self._run(workspace._list(1, 2, "box", {"path": "/tmp"}, "/tmp"))
        self.assertNotIn("showing", text)

    def test_list_sorts_before_truncating(self):
        # readdir order: the alphabetical first entries arrive last.
        names = [f"z{i:04d}" for i in range(500)] + ["a-first", "b-second"]
        listing = "\n".join(names) + "\n"
        fake = FakeAgent([("exec", {"ok": True, "exit": 0, "out": listing, "err": "", "error": ""})])
        with patch.object(workspace, "_is_agent", return_value=True), patch.object(
            workspace, "_agent_call", fake
        ):
            text, rec = self._run(workspace._list(1, 2, "box", {"path": "/tmp"}, "/tmp"))
        body = text.split("\n")
        self.assertEqual(body[1:3], ["a-first", "b-second"])
        self.assertIn("showing 400 of 502 entries", text)

    def test_list_notes_when_capped(self):
        listing = "\n".join(f"f{i}" for i in range(401)) + "\n"
        fake = FakeAgent([("exec", {"ok": True, "exit": 0, "out": listing, "err": "", "error": ""})])
        with patch.object(workspace, "_is_agent", return_value=True), patch.object(
            workspace, "_agent_call", fake
        ):
            text, rec = self._run(workspace._list(1, 2, "box", {"path": "/tmp"}, "/tmp"))
        self.assertIn("showing 400", text)

    def test_grep_notes_when_capped(self):
        hits = "\n".join(f"a.py:{i}:x" for i in range(1, 51))
        fake = FakeAgent([("exec", {"ok": True, "exit": 0, "out": hits, "err": "", "error": ""})])
        with patch.object(workspace, "_is_agent", return_value=True), patch.object(
            workspace, "_agent_call", fake
        ):
            text, rec = self._run(
                workspace._grep(1, 2, "box", {"pattern": "x", "path": "."}, "/tmp")
            )
        self.assertIn("showing the first 50 matches", text)

    def test_list_falls_back_to_list_op(self):
        fake = FakeAgent(
            [
                ("exec", {"ok": False, "exit": 1, "out": "", "err": "boom", "error": "boom"}),
                ("list", {"ok": True, "exit": 0, "out": "plain\n", "err": "", "error": ""}),
            ]
        )
        with patch.object(workspace, "_is_agent", return_value=True), patch.object(
            workspace, "_agent_call", fake
        ):
            text, rec = self._run(workspace._list(1, 2, "box", {"path": "/tmp"}, "/tmp"))
        self.assertIn("plain", text)

    def test_list_falls_back_when_ls_times_out(self):
        async def call(_uid, _hid, op, args, seconds=30):
            if op == "exec":
                raise WorkspaceError("agent timed out")
            return {"ok": True, "exit": 0, "out": "src/\n", "err": "", "error": ""}

        with patch.object(workspace, "_is_agent", return_value=True), patch.object(
            workspace, "_agent_call", call
        ):
            text, rec = self._run(workspace._list(1, 2, "box", {"path": "/tmp"}, "/tmp"))
        self.assertIn("src/", text)

    def test_list_offline_does_not_fallback(self):
        async def call(_uid, _hid, op, args, seconds=30):
            raise WorkspaceError("agent is offline")

        with patch.object(workspace, "_is_agent", return_value=True), patch.object(
            workspace, "_agent_call", call
        ):
            with self.assertRaises(WorkspaceError) as ctx:
                self._run(workspace._list(1, 2, "box", {"path": "/tmp"}, "/tmp"))
        self.assertIn("offline", ctx.exception.detail)

    def test_execute_sets_kind_for_host_errors(self):
        async def boom(*_a, **_k):
            raise WorkspaceError("agent timed out")

        with patch.object(workspace, "resolve_host_row", return_value={"id": 2, "alias": "box", "auth_type": "agent"}), patch.object(
            workspace, "host_root", return_value="/tmp"
        ), patch.object(workspace, "_is_windows_row", return_value=False), patch.object(
            workspace, "_grep", boom
        ), patch.object(
            workspace.hosts, "settings_for", return_value={"permission_mode": "project"}
        ), patch.object(
            workspace.hosts, "effective_permission", return_value="allow"
        ):
            text, rec = self._run(
                workspace.execute("grep", '{"pattern":"x"}', user_id=1, bound_host_id=2, bound_cwd="/tmp")
            )
        self.assertEqual(rec["status"], "error")
        self.assertEqual(rec["kind"], "upstream_error")
        self.assertEqual(rec["error"], "agent timed out")
        self.assertIn("timed out", text)

    def test_replace_recovery_with_fuzzy_match(self):
        file_body = "def f():\n    pass\n"

        def replace_op(args):
            self.assertEqual(args["old"], "def f():\n\tpass\n")
            return {"ok": False, "exit": 1, "out": "", "err": "", "error": "replace failed"}

        def read_op(args):
            self.assertEqual(args["limit"], 2000)
            return {"ok": True, "exit": 0, "out": file_body, "err": "", "error": ""}

        def write_op(args):
            written["content"] = args["content"]
            return {"ok": True, "exit": 0, "out": "wrote", "err": "", "error": ""}

        written = {}
        fake = FakeAgent([("replace", replace_op), ("read", read_op), ("write", write_op)])
        with patch.object(workspace, "_is_agent", return_value=True), patch.object(
            workspace, "_agent_call", fake
        ):
            with self.assertRaises(WorkspaceError):
                self._run(workspace._replace(
                    1,
                    2,
                    "box",
                    {"path": "/tmp/x.py", "old_string": "def f():\n\tpass\n", "new_string": "X"},
                    "/tmp",
                ))
        # Generic failure: one read-only stat probe (exec), never a read/write
        # mutation fallback.
        self.assertEqual([op for op, _ in fake.calls], ["replace", "exec"])
        self.assertEqual(written, {})

    def test_replace_precise_error_after_recovery(self):
        fake = FakeAgent(
            [
                ("replace", {"ok": False, "exit": 1, "out": "", "err": "", "error": "replace failed"}),
                ("read", {"ok": True, "exit": 0, "out": "alpha\n", "err": "", "error": ""}),
            ]
        )
        with patch.object(workspace, "_is_agent", return_value=True), patch.object(
            workspace, "_agent_call", fake
        ):
            with self.assertRaises(WorkspaceError) as ctx:
                self._run(
                    workspace._replace(
                        1, 2, "box",
                        {"path": "/tmp/x.py", "old_string": "zzz", "new_string": "x"},
                        "/tmp",
                    )
                )
        self.assertIn("replace failed", ctx.exception.detail)
        self.assertEqual([op for op, _ in fake.calls], ["replace", "exec"])

    def test_replace_generic_failure_maps_too_large(self):
        fake = FakeAgent(
            [
                ("replace", {"ok": False, "exit": 1, "out": "", "err": "", "error": "replace failed"}),
                ("exec", {"ok": True, "exit": 0, "out": "67108900\nw\ndw\n", "err": "", "error": ""}),
            ]
        )
        with patch.object(workspace, "_is_agent", return_value=True), patch.object(
            workspace, "_agent_call", fake
        ):
            with self.assertRaises(WorkspaceError) as ctx:
                self._run(
                    workspace._replace(
                        1, 2, "box",
                        {"path": "/tmp/big.py", "old_string": "a\nb", "new_string": "x"},
                        "/tmp",
                    )
                )
        self.assertIn("file too large to edit", ctx.exception.detail)
        self.assertIn("67108864", ctx.exception.detail)
        self.assertIn("run_terminal_command", ctx.exception.detail)
        probe_cmd = fake.calls[1][1]["cmd"]
        self.assertIn("wc -c", probe_cmd)

    def test_replace_generic_failure_maps_missing(self):
        fake = FakeAgent(
            [
                ("replace", {"ok": False, "exit": 1, "out": "", "err": "", "error": "replace failed"}),
                ("exec", {"ok": True, "exit": 0, "out": "missing\nna\ndmiss\n", "err": "", "error": ""}),
            ]
        )
        with patch.object(workspace, "_is_agent", return_value=True), patch.object(
            workspace, "_agent_call", fake
        ):
            with self.assertRaises(WorkspaceError) as ctx:
                self._run(
                    workspace._replace(
                        1, 2, "box",
                        {"path": "/tmp/gone.py", "old_string": "a\nb", "new_string": "x"},
                        "/tmp",
                    )
                )
        self.assertIn("does not exist", ctx.exception.detail)

    def test_replace_generic_failure_maps_legacy_cap_window(self):
        # Pre-1.3.2 connectors cap edits at 400000 bytes and fail generically;
        # a 400000..4000000-byte file in that failure is the legacy cap.
        fake = FakeAgent(
            [
                ("replace", {"ok": False, "exit": 1, "out": "", "err": "", "error": "replace failed"}),
                ("exec", {"ok": True, "exit": 0, "out": "500000\nw\ndw\n", "err": "", "error": ""}),
            ]
        )
        with patch.object(workspace, "_is_agent", return_value=True), patch.object(
            workspace, "_agent_call", fake
        ):
            with self.assertRaises(WorkspaceError) as ctx:
                self._run(
                    workspace._replace(
                        1, 2, "box",
                        {"path": "/tmp/mid.py", "old_string": "a\nb", "new_string": "x"},
                        "/tmp",
                    )
                )
        self.assertIn("connector's edit limit", ctx.exception.detail)
        self.assertIn("run_terminal_command", ctx.exception.detail)

    def test_replace_specific_error_not_probed(self):
        fake = FakeAgent(
            [("replace", {"ok": False, "exit": 1, "out": "", "err": "", "error": "file changed during edit; read it again"})]
        )
        with patch.object(workspace, "_is_agent", return_value=True), patch.object(
            workspace, "_agent_call", fake
        ):
            with self.assertRaises(WorkspaceError) as ctx:
                self._run(
                    workspace._replace(
                        1, 2, "box",
                        {"path": "/tmp/x.py", "old_string": "a\nb", "new_string": "x"},
                        "/tmp",
                    )
                )
        self.assertIn("changed during edit", ctx.exception.detail)
        self.assertEqual([op for op, _ in fake.calls], ["replace"])

    def test_replace_crlf_retry_on_legacy_connector(self):
        states = {"replace_calls": 0}

        def replace_op(args):
            states["replace_calls"] += 1
            if "\r\n" in args["old"]:
                return {"ok": True, "exit": 0, "out": "1 replacement(s)", "err": "", "error": ""}
            return {"ok": False, "exit": 1, "out": "", "err": "", "error": "old_string not found"}

        fake = FakeAgent(
            [
                ("replace", replace_op),
                ("read", {"ok": True, "exit": 0, "out": "alpha\r\nbeta\r\n", "err": "", "error": ""}),
            ]
        )
        with patch.object(workspace, "_is_agent", return_value=True), patch.object(
            workspace, "_agent_call", fake
        ):
            text, rec = self._run(
                workspace._replace(
                    1, 2, "box",
                    {"path": "/tmp/x.py", "old_string": "alpha\nbeta", "new_string": "one\ntwo"},
                    "/tmp",
                )
            )
        self.assertEqual(states["replace_calls"], 2)
        self.assertEqual(rec["status"], "done")
        retry_args = fake.calls[2][1]
        self.assertEqual(retry_args["old"], "alpha\r\nbeta")
        self.assertEqual(retry_args["new"], "one\r\ntwo")

    def test_replace_returns_real_line_numbers(self):
        def replace_op(args):
            return {"ok": True, "exit": 0, "out": "1 replacement(s)", "err": "", "error": ""}

        fake = FakeAgent(
            [
                ("replace", replace_op),
                ("read", {"ok": True, "exit": 0, "out": "one\ntwo\nthree\nretries = 5\nsix\n", "err": "", "error": ""}),
            ]
        )
        with patch.object(workspace, "_is_agent", return_value=True), patch.object(
            workspace, "_agent_call", fake
        ):
            text, rec = self._run(
                workspace._replace(
                    1, 2, "box",
                    {"path": "/tmp/x.py", "old_string": "retries = 3", "new_string": "retries = 5"},
                    "/tmp",
                )
            )
        self.assertEqual([op for op, _ in fake.calls], ["replace", "read"])
        self.assertIn("4: retries = 5", text)
        self.assertIn("@@ -4 +4 @@", rec["preview"])
        self.assertNotIn("a//", rec["preview"])

    def test_replace_without_sample_omits_hunk_header(self):
        def replace_op(args):
            return {"ok": True, "exit": 0, "out": "1 replacement(s)", "err": "", "error": ""}

        fake = FakeAgent(
            [
                ("replace", replace_op),
                ("read", {"ok": False, "exit": 1, "out": "", "err": "", "error": "file does not exist"}),
            ]
        )
        with patch.object(workspace, "_is_agent", return_value=True), patch.object(
            workspace, "_agent_call", fake
        ):
            text, rec = self._run(
                workspace._replace(
                    1, 2, "box",
                    {"path": "/tmp/x.py", "old_string": "retries = 3", "new_string": "retries = 5"},
                    "/tmp",
                )
            )
        self.assertIn("updated /tmp/x.py (1 replacement)", text)
        self.assertNotIn("@@", rec["preview"])
        self.assertIn("-retries = 3", rec["preview"])

    def test_replace_not_found_mentions_crlf(self):
        def replace_op(args):
            return {"ok": False, "exit": 1, "out": "", "err": "", "error": "old_string not found"}

        fake = FakeAgent(
            [
                ("replace", replace_op),
                ("read", {"ok": True, "exit": 0, "out": "alpha\r\nbeta\r\n", "err": "", "error": ""}),
            ]
        )
        with patch.object(workspace, "_is_agent", return_value=True), patch.object(
            workspace, "_agent_call", fake
        ):
            with self.assertRaises(WorkspaceError) as ctx:
                self._run(
                    workspace._replace(
                        1, 2, "box",
                        {"path": "/tmp/x.py", "old_string": "zzz\nqqq", "new_string": "x"},
                        "/tmp",
                    )
                )
        self.assertIn("not found", ctx.exception.detail)
        self.assertIn("CRLF", ctx.exception.detail)

    def test_replace_payload_too_large_hint(self):
        async def call(_uid, _hid, op, args, seconds=30):
            raise WorkspaceError("payload too large")

        with patch.object(workspace, "_is_agent", return_value=True), patch.object(
            workspace, "_agent_call", call
        ):
            with self.assertRaises(WorkspaceError) as ctx:
                self._run(
                    workspace._replace(
                        1, 2, "box",
                        {"path": "/tmp/x.py", "old_string": "a" * 200, "new_string": "b"},
                        "/tmp",
                    )
                )
        self.assertIn("transport limit", ctx.exception.detail)
        self.assertIn("run_terminal_command", ctx.exception.detail)

    def test_write_generic_failure_maps_parent_missing(self):
        fake = FakeAgent(
            [
                ("write", {"ok": False, "exit": 1, "out": "", "err": "", "error": "write failed"}),
                ("exec", {"ok": True, "exit": 0, "out": "missing\nna\ndmiss\n", "err": "", "error": ""}),
            ]
        )
        with patch.object(workspace, "_is_agent", return_value=True), patch.object(
            workspace, "_agent_call", fake
        ):
            with self.assertRaises(WorkspaceError) as ctx:
                self._run(
                    workspace._write(
                        1, 2, "box",
                        {"path": "/tmp/newdir/x.py", "content": "hi"},
                        "/tmp",
                    )
                )
        self.assertIn("parent directory does not exist", ctx.exception.detail)

    def test_write_payload_too_large_hint(self):
        async def call(_uid, _hid, op, args, seconds=30):
            raise WorkspaceError("payload too large")

        with patch.object(workspace, "_is_agent", return_value=True), patch.object(
            workspace, "_agent_call", call
        ):
            with self.assertRaises(WorkspaceError) as ctx:
                self._run(
                    workspace._write(
                        1, 2, "box",
                        {"path": "/tmp/x.py", "content": "a" * 4000},
                        "/tmp",
                    )
                )
        self.assertIn("transport limit", ctx.exception.detail)

    def test_probe_parses_posix_and_windows_output(self):
        fake = FakeAgent(
            [
                ("exec", {"ok": True, "exit": 0, "out": "12345\nnw\ndw\n", "err": "", "error": ""}),
            ]
        )
        with patch.object(workspace, "_agent_call", fake):
            info = self._run(workspace._agent_probe_file(1, 2, "/tmp/x.py", windows=False))
        self.assertEqual(info["kind"], "file")
        self.assertEqual(info["size"], 12345)
        self.assertFalse(info["writable"])
        self.assertEqual(info["parent"], "dw")

        fake = FakeAgent(
            [
                ("exec", {"ok": True, "exit": 0, "out": "dir\ndmiss\n", "err": "", "error": ""}),
            ]
        )
        with patch.object(workspace, "_agent_call", fake):
            info = self._run(workspace._agent_probe_file(1, 2, r"C:\x", windows=True))
        self.assertEqual(info["kind"], "dir")
        self.assertEqual(info["parent"], "dmiss")
        cmd = fake.calls[0][1]["cmd"]
        self.assertIn("Test-Path -LiteralPath", cmd)


class FakeSFTPEntry:
    def __init__(self, name, perms):
        self.filename = name
        self.attrs = type("A", (), {"permissions": perms})()


class FakeSFTPAttrsClient:
    def __init__(self, files: dict[str, bytes], entries: list):
        self.files = files
        self.entries = entries

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def open(self, path, mode):
        if mode == "rb":
            if path not in self.files:
                raise FileNotFoundError(path)

            class H:
                def __init__(h, data):
                    h.data = data

                async def __aenter__(h):
                    return h

                async def __aexit__(h, *exc):
                    return False

                async def read(h, n):
                    result, h.data = h.data[:n], h.data[n:]
                    return result

            return H(self.files[path])
        raise IsADirectoryError(path)

    async def listdir(self, path):
        return [e.filename for e in self.entries]

    async def listdirattr(self, path):
        return self.entries

    async def scandir(self, path):
        for entry in self.entries:
            yield entry


class FakeSFTPReplaceClient(FakeSFTPAttrsClient):
    """Adds lstat / atomic-write support so _replace's SSH path can run."""

    def __init__(self, files: dict[str, bytes]):
        super().__init__(files, [])
        self.written: dict[str, bytes] = {}

    async def realpath(self, path):
        return path

    async def lstat(self, path):
        if path not in self.files:
            raise FileNotFoundError(path)
        data = self.files[path]
        return type(
            "A", (), {"size": len(data), "mtime": 1, "mtime_ns": 1, "permissions": 0o100644}
        )()

    def open(self, path, mode, attrs=None):
        if mode == "rb":
            return super().open(path, mode)

        client = self

        class W:
            async def __aenter__(h):
                return h

            async def __aexit__(h, *exc):
                return False

            async def write(h, data):
                client.written[path] = client.written.get(path, b"") + data

        return W()

    async def posix_rename(self, src, dst):
        self.files[dst] = self.written.pop(src)

    async def rename(self, src, dst):
        self.files[dst] = self.written.pop(src)

    async def remove(self, path):
        self.written.pop(path, None)


def make_sftp_pool(sftp):
    """Return an async connection() replacement handing out a fake SFTP client."""
    conn = type("C", (), {})()

    async def start_sftp_client():
        return sftp

    conn.start_sftp_client = start_sftp_client

    async def connection(*a, **k):
        return conn

    return connection


class SFTPPathTest(unittest.TestCase):
    def _run(self, coro):
        return asyncio.run(coro)

    def test_read_numbering_and_eof(self):
        sftp = FakeSFTPAttrsClient({"/tmp/x.py": b"alpha\nbeta\n"}, [])
        with patch.object(workspace, "_is_agent", return_value=False), patch(
            "app.ssh_pool.connection", make_sftp_pool(sftp)
        ):
            text, rec = self._run(workspace._read(1, 2, "srv", {"path": "x.py"}, "/tmp"))
        self.assertIn("1: alpha", text)
        self.assertIn("2: beta", text)
        self.assertIn("(End of file — 2 lines.)", text)

    def test_read_offset_past_end(self):
        sftp = FakeSFTPAttrsClient({"/tmp/x.py": b"alpha\n"}, [])
        with patch.object(workspace, "_is_agent", return_value=False), patch(
            "app.ssh_pool.connection", make_sftp_pool(sftp)
        ):
            with self.assertRaises(WorkspaceError) as ctx:
                self._run(workspace._read(1, 2, "srv", {"path": "x.py", "offset": 99}, "/tmp"))
        self.assertIn("beyond the end", ctx.exception.detail)

    def test_list_directory_markers(self):
        entries = [
            FakeSFTPEntry("src", 0o040755),
            FakeSFTPEntry("main.py", 0o100644),
        ]
        sftp = FakeSFTPAttrsClient({}, entries)
        with patch.object(workspace, "_is_agent", return_value=False), patch(
            "app.ssh_pool.connection", make_sftp_pool(sftp)
        ):
            text, rec = self._run(workspace._list(1, 2, "srv", {"path": "."}, "/tmp"))
        self.assertIn("src/", text)
        self.assertIn("main.py", text)

    def test_replace_half_megabyte_file_succeeds(self):
        body = "HEAD\n" + "line\n" * 99999  # 500000 bytes, above the old 400000 cap
        sftp = FakeSFTPReplaceClient({"/tmp/big.py": body.encode()})
        with patch.object(workspace, "_is_agent", return_value=False), patch(
            "app.ssh_pool.connection", make_sftp_pool(sftp)
        ):
            text, rec = self._run(
                workspace._replace(
                    1,
                    2,
                    "srv",
                    {"path": "big.py", "old_string": "HEAD\nline\n", "new_string": "TOPLINE\nrow\n"},
                    "/tmp",
                )
            )
        self.assertIn("updated", text)
        self.assertEqual(rec["status"], "done")
        self.assertEqual(sftp.files["/tmp/big.py"], b"TOPLINE\nrow\n" + b"line\n" * 99998)

    def test_replace_over_cap_names_the_limit(self):
        body = "x" * 1001  # cap patched down so the test stays small
        sftp = FakeSFTPReplaceClient({"/tmp/big.py": body.encode()})
        with patch.object(workspace, "_is_agent", return_value=False), patch(
            "app.ssh_pool.connection", make_sftp_pool(sftp)
        ), patch.object(workspace, "_REPLACE_FILE_MAX", 1000):
            with self.assertRaises(WorkspaceError) as ctx:
                self._run(
                    workspace._replace(
                        1, 2, "srv",
                        {"path": "big.py", "old_string": "x", "new_string": "y"},
                        "/tmp",
                    )
                )
        self.assertIn("file too large to edit", ctx.exception.detail)
        self.assertIn("1000", ctx.exception.detail)
        self.assertEqual(sftp.files["/tmp/big.py"], body.encode())


if __name__ == "__main__":
    unittest.main()
