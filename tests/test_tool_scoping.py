"""Tool-argument scoping: grep/glob include semantics on every host shape,
schema-filling models (every optional property sent), and recovered history
keeping the arguments that scoped a call."""

import asyncio
import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import workspace as ws
from app.chat_history import _trace_rows
from app.sources.chatgpt import build_responses_payload


def _tree(root: str) -> None:
    files = {
        "app/main.py": "needle app\n",
        "app/web/main.py": "needle web\n",
        "app/web/ui.js": "needle js\n",
        "tests/main.py": "needle tests\n",
        "ignored.py": "needle ignored\n",
        "node_modules/pkg/main.py": "needle vendor\n",
        "big.py": "x = 1\n" * 200_000 + "needle big\n",  # > 1 MB
    }
    for rel, body in files.items():
        path = Path(root, rel)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body)
    Path(root, ".gitignore").write_text("ignored.py\n")
    if shutil.which("git"):
        subprocess.run(["git", "init", "-q", root], check=True)


class _HostSearch:
    """Run _grep/_glob against a real directory: _tool_exec executes the
    generated command in bash, optionally with rg hidden (the fallback)."""

    def __init__(self, root: str, *, rg: bool):
        self.root = root
        self.rg = rg

    async def _exec(self, user_id, host_id, cmd, cwd, seconds):
        prefix = "" if self.rg else "command() { if [ \"$2\" = rg ]; then return 1; fi; builtin command \"$@\"; }; "
        proc = subprocess.run(["bash", "-c", prefix + cmd], cwd=cwd, capture_output=True, text=True, timeout=30)
        return {"exit": proc.returncode, "out": proc.stdout, "err": proc.stderr, "error": "", "timed_out": proc.returncode == 124}

    def grep(self, **args):
        args = {"pattern": "needle", "output_mode": "files_with_matches", **args}
        with patch.object(ws, "_is_agent", return_value=False), patch.object(ws, "_tool_exec", self._exec):
            text, rec = asyncio.run(ws._grep(1, 1, "h", args, self.root))
        self._assert_ok(rec)
        return [line for line in ws.unlabel_host("h", text).splitlines() if line and not line.startswith("(")]

    def glob(self, **args):
        with patch.object(ws, "_is_agent", return_value=False), patch.object(ws, "_tool_exec", self._exec):
            text, rec = asyncio.run(ws._glob(1, 1, "h", args, self.root))
        self._assert_ok(rec)
        return [line for line in ws.unlabel_host("h", text).splitlines() if line and not line.startswith("(")]

    @staticmethod
    def _assert_ok(rec):
        if rec.get("status") != "done":
            raise AssertionError(rec)


class IncludeScopingTest(unittest.TestCase):
    """Same answers with and without ripgrep on the host."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        _tree(self.root)
        self.hosts = [_HostSearch(self.root, rg=False)]
        if shutil.which("rg"):
            self.hosts.append(_HostSearch(self.root, rg=True))

    def tearDown(self):
        self.tmp.cleanup()

    def check(self, fn, args, expected):
        for host in self.hosts:
            with self.subTest(rg=host.rg, args=args):
                self.assertEqual(sorted(getattr(host, fn)(**args)), expected)

    def test_file_path_searches_only_that_file(self):
        self.check("grep", {"path": "app/main.py"}, ["app/main.py"])

    def test_literal_include_in_path_narrows_to_that_file(self):
        self.check("grep", {"path": "app", "include": "main.py"}, ["app/main.py"])

    def test_literal_include_not_in_path_matches_by_name_below(self):
        self.check("grep", {"path": ".", "include": "main.py"}, ["app/main.py", "app/web/main.py", "tests/main.py"])

    def test_slash_include_is_relative_to_path(self):
        self.check("grep", {"path": "app", "include": "web/*.py"}, ["app/web/main.py"])
        self.check("grep", {"path": "app", "include": "**/*.js"}, ["app/web/ui.js"])

    def test_include_tolerates_dot_slash_and_repeated_path(self):
        self.check("grep", {"path": ".", "include": "./app/main.py"}, ["app/main.py"])
        self.check("grep", {"path": "app", "include": "app/web/main.py"}, ["app/web/main.py"])

    def test_brace_include(self):
        self.check("grep", {"path": "app", "include": "*.{py,js}"}, ["app/main.py", "app/web/main.py", "app/web/ui.js"])

    def test_gitignore_vendor_and_size_rules(self):
        # Directory walk: .gitignore, vendor dirs and the 1 MB cap all apply.
        self.check("grep", {"path": "."}, ["app/main.py", "app/web/main.py", "app/web/ui.js", "tests/main.py"])
        self.check(
            "grep", {"path": ".", "no_ignore": True},
            ["app/main.py", "app/web/main.py", "app/web/ui.js", "ignored.py", "tests/main.py"],
        )
        # An include names the files it wants: no size cap, still no vendor.
        self.check("grep", {"path": ".", "include": "*.py"}, ["app/main.py", "app/web/main.py", "big.py", "tests/main.py"])
        self.check("grep", {"path": "node_modules"}, ["node_modules/pkg/main.py"])

    def test_glob_pattern_is_relative_to_path(self):
        self.check("glob", {"pattern": "web/*.py", "path": "app"}, ["app/web/main.py"])
        self.check("glob", {"pattern": "app/web/*.py", "path": "app"}, ["app/web/main.py"])
        self.check("glob", {"pattern": "**/*.{py,js}", "path": "app"}, ["app/main.py", "app/web/main.py", "app/web/ui.js"])
        self.check("glob", {"pattern": "*.py"}, ["app/main.py", "app/web/main.py", "big.py", "tests/main.py"])


class GlobTranslationTest(unittest.TestCase):
    def test_glob_to_ere(self):
        cases = {
            "*.py": (["a.py"], ["a/b.py", "a.pyc"]),
            "src/**/*.py": (["src/a.py", "src/x/y/b.py"], ["srcx/a.py", "other/src/a.py"]),
            "*.{py,js}": (["a.py", "b.js"], ["c.ts"]),
            "t?st.[ch]": (["test.c", "tast.h"], ["test.o", "teest.c"]),
            "a+b(1).txt": (["a+b(1).txt"], ["aab1.txt"]),
            "my file&co~.txt": (["my file&co~.txt"], ["my_file&co~.txt"]),
        }
        for glob, (good, bad) in cases.items():
            rx = re.compile(ws._glob_to_ere(glob))
            for path in good:
                self.assertTrue(rx.fullmatch(path), (glob, path))
            for path in bad:
                self.assertFalse(rx.fullmatch(path), (glob, path))

    def test_ere_is_clean_for_gnu_grep(self):
        # GNU grep warns about "stray \\" before non-metacharacters.
        ere = ws._glob_to_ere("my file&co~#.txt")
        self.assertEqual(ere, "my file&co~#\\.txt")
        proc = subprocess.run(["grep", "-E", ere], input="my file&co~#.txt\n", capture_output=True, text=True)
        self.assertEqual((proc.returncode, proc.stderr), (0, ""))

    def test_scope_glob(self):
        self.assertEqual(ws._scope_glob("./app/main.py", ".", "."), "app/main.py")
        self.assertEqual(ws._scope_glob("app/web/*.py", "/repo/app", "app"), "web/*.py")
        self.assertEqual(ws._scope_glob("/repo/app/web/*.py", "/repo/app", "app"), "web/*.py")
        self.assertEqual(ws._scope_glob("*.py", "/repo/app", "app"), "*.py")
        self.assertEqual(ws._scope_glob("a\\*.py", "/repo/app", "app"), "a\\*.py")
        self.assertEqual(ws._scope_glob("app\\web\\x.cs", "C:\\r\\app", "app", windows=True), "web\\x.cs")

    def test_reroot_lines(self):
        out = "./a.py:1:x\n--\n./b.py-2-y"
        self.assertEqual(ws._reroot_lines(out, "src"), "src/a.py:1:x\n--\nsrc/b.py-2-y")
        self.assertEqual(ws._reroot_lines(out, "."), out)


class SchemaFillingModelTest(unittest.TestCase):
    """Models that send every optional property (strict-mode habits)."""

    def test_edits_with_empty_single_fields(self):
        edits = ws._parse_edits({"old_string": "", "new_string": "", "edits": [{"old_string": "a", "new_string": "b"}]})
        self.assertEqual(edits, [("a", "b", False)])

    def test_single_with_empty_edits(self):
        self.assertEqual(ws._parse_edits({"old_string": "a", "new_string": "b", "edits": []}), [("a", "b", False)])

    def test_single_duplicated_into_edits(self):
        args = {"old_string": "a", "new_string": "b", "edits": [{"old_string": "a", "new_string": "b"}]}
        self.assertEqual(ws._parse_edits(args), [("a", "b", False)])

    def test_conflicting_forms_still_refused(self):
        args = {"old_string": "a", "new_string": "b", "edits": [{"old_string": "x", "new_string": "y"}]}
        with self.assertRaises(ws.WorkspaceError):
            ws._parse_edits(args)

    def test_zero_means_default(self):
        self.assertEqual(ws._grep_max_matches({"max_matches": 0}), ws._GREP_DEFAULT_MATCHES)
        self.assertEqual(ws._int_arg({"limit": 0}, "limit", 2000, 1, 2000), 2000)
        self.assertEqual(ws._int_arg({"offset": 0}, "offset", 1, 1, 10), 1)
        self.assertEqual(ws._int_arg({"context": 0}, "context", 0, 0, 10), 0)

    def test_chatgpt_tools_are_not_strict(self):
        body = {
            "messages": [{"role": "user", "content": "hi"}],
            "tools": [{"type": "function", "function": {"name": "grep", "parameters": {"type": "object", "properties": {}}}}],
        }
        payload = build_responses_payload(body, "gpt-6-astra")
        self.assertIs(payload["tools"][0]["strict"], False)


class RecoveredHistoryTest(unittest.TestCase):
    def test_trace_rows_keep_scoping_arguments(self):
        message = {
            "id": "m1",
            "tools": [
                {"name": "read_file", "status": "done", "path": "/repo/a.py", "offset": 500, "limit": 80, "output": "500: x"},
                {"name": "grep", "status": "done", "path": "/repo/app", "pattern": "x", "include": "main.py", "max_matches": 20},
            ],
        }
        rows = _trace_rows(message)
        calls = [json.loads(r["tool_calls"][0]["function"]["arguments"])["recorded_details"] for r in rows if r.get("tool_calls")]
        self.assertEqual(calls[0], {"path": "/repo/a.py", "offset": 500, "limit": 80})
        self.assertEqual(calls[1], {"path": "/repo/app", "pattern": "x", "include": "main.py", "max_matches": 20})


if __name__ == "__main__":
    unittest.main()
