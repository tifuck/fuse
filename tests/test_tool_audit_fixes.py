"""Regression tests for the chat tool audit: read/list/grep/glob/write/edit,
shell wrapping, job output, read_url paging, search budgets, and same-step
ordering. No live SSH, connectors, or network."""

from __future__ import annotations

import asyncio
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
_TMP = tempfile.mkdtemp(prefix="fuse-audit-")
os.environ.setdefault("DATABASE_PATH", str(Path(_TMP) / "chat.db"))
os.environ.setdefault("APP_SECRET", "x" * 32)

import asyncssh  # noqa: E402

from app import chat_runtime as runtime  # noqa: E402
from app import command_jobs as jobs  # noqa: E402
from app import permissions  # noqa: E402
from app import workspace as ws  # noqa: E402
from app.search import extract as extract_mod  # noqa: E402
from app.search.pack import pack_page  # noqa: E402


# --- fakes -------------------------------------------------------------------

class _Attrs(SimpleNamespace):
    pass


class FakeSFTP:
    """In-memory SFTP client covering what workspace uses."""

    def __init__(self, files=None, dirs=None, *, deny_create_in=()):
        self.files: dict[str, bytes] = dict(files or {})
        self.modes: dict[str, int] = {p: 0o644 for p in self.files}
        self.mtimes: dict[str, int] = {p: 1 for p in self.files}
        self.dirs: set[str] = set(dirs or {"/repo"})
        self.deny_create_in = set(deny_create_in)
        self.created_modes: dict[str, int] = {}
        self.chowned: list = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def realpath(self, path):
        return "/home/u"

    def _attrs(self, path):
        if path in self.files:
            return _Attrs(size=len(self.files[path]), mtime=self.mtimes[path], mtime_ns=None,
                          permissions=0o100000 | self.modes[path], uid=1000, gid=1000)
        if path in self.dirs:
            return _Attrs(size=0, mtime=1, mtime_ns=None, permissions=0o040755, uid=1000, gid=1000)
        raise asyncssh.SFTPNoSuchFile("No such file")

    async def lstat(self, path):
        return self._attrs(path)

    async def stat(self, path):
        return self._attrs(path)

    def open(self, path, mode, attrs=None):
        sftp = self

        class Handle:
            def __init__(self):
                self.buf = io.BytesIO(sftp.files.get(path, b"")) if "r" in mode else io.BytesIO()

            async def __aenter__(self):
                parent = path.rsplit("/", 1)[0] or "/"
                if "r" in mode and path in sftp.dirs:
                    return self
                if "r" in mode and path not in sftp.files:
                    raise asyncssh.SFTPNoSuchFile("No such file")
                if "x" in mode or "w" in mode:
                    if parent not in sftp.dirs:
                        raise asyncssh.SFTPNoSuchFile("No such file")
                    if "x" in mode and parent in sftp.deny_create_in:
                        raise asyncssh.SFTPPermissionDenied("Permission denied")
                    if attrs is not None:
                        sftp.created_modes[path] = attrs.permissions
                return self

            async def __aexit__(self, *exc):
                if ("x" in mode or "w" in mode) and not exc[0]:
                    sftp.files[path] = self.buf.getvalue()
                    sftp.modes.setdefault(path, sftp.created_modes.get(path, 0o644) & 0o777)
                    sftp.mtimes[path] = sftp.mtimes.get(path, 1) + 1
                return False

            async def stat(self):
                return sftp._attrs(path)

            async def read(self, n=-1):
                return self.buf.read(n)

            async def write(self, data):
                self.buf.write(data)

        return Handle()

    async def chmod(self, path, mode):
        self.modes[path] = mode

    async def chown(self, path, uid, gid):
        self.chowned.append((path, uid, gid))

    async def posix_rename(self, src, dst):
        self.files[dst] = self.files.pop(src)
        self.modes[dst] = self.modes.pop(src, 0o644)
        self.mtimes[dst] = self.mtimes.get(dst, 1) + 1
        self.mtimes.pop(src, None)

    async def remove(self, path):
        if path not in self.files:
            raise asyncssh.SFTPNoSuchFile("gone")
        self.files.pop(path)

    async def listdir(self, path):
        if path in self.dirs:
            return []
        raise asyncssh.SFTPNoSuchFile("No such file")

    async def makedirs(self, path, exist_ok=False):
        parts = path.strip("/").split("/")
        for i in range(1, len(parts) + 1):
            self.dirs.add("/" + "/".join(parts[:i]))


def sftp_patch(sftp):
    return patch.object(ws, "_sftp", AsyncMock(return_value=sftp))


# --- read_file ---------------------------------------------------------------

class ReadResultClipTest(unittest.TestCase):
    def test_full_read_page_keeps_continuation_footer_through_runtime_clip(self):
        lines = ["x" * 60] * 2000
        body = ws._label("h", ws._read_render(lines, 1, total=5000, more=True, max_chars=ws._READ_MAX_CHARS))
        clipped = runtime._tool_result_clip("read_file", body)
        self.assertEqual(clipped, body)
        self.assertRegex(clipped, r"Use offset=\d+ to continue\.\)$")

    def test_worst_case_read_page_fits_read_clip(self):
        lines = ["€" * ws._READ_MAX_LINE] * 2000
        body = ws._label("h" * 64, ws._read_render(lines, 9_999_000, total=None, more=True, max_chars=ws._READ_MAX_CHARS))
        self.assertLessEqual(len(body), runtime.READ_RESULT_CHARS)

    def test_other_tool_budgets_fit_runtime_clip(self):
        self.assertLess(ws._SEARCH_OUT_CHARS + 2000, runtime.TOOL_RESULT_CHARS)
        self.assertLess(jobs.OUT_HEAD_CHARS + jobs.MODEL_TAIL_CHARS + 1000, runtime.TOOL_RESULT_CHARS)
        from app.chat_generation import tool_jobs
        from app.search.pack import PAGE_BUDGET

        # read_url has its own clip: a page window is larger than other tools'
        # results and its continue-footer must survive.
        self.assertLess(PAGE_BUDGET, tool_jobs.WEB_PAGE_RESULT_CHARS)
        self.assertEqual(tool_jobs._tool_result_clip("read_url", "x" * PAGE_BUDGET), "x" * PAGE_BUDGET)


class SftpReadTest(unittest.IsolatedAsyncioTestCase):
    async def test_missing_file_names_the_problem(self):
        with patch.object(ws, "_is_agent", return_value=False), sftp_patch(FakeSFTP()):
            with self.assertRaisesRegex(ws.WorkspaceError, "no such file or directory"):
                await ws._read(1, 2, "h", {"path": "nope.py"}, "/repo")

    async def test_binary_file_refused(self):
        sftp = FakeSFTP({"/repo/a.bin": b"\x7fELF\x00\x01\x02"})
        with patch.object(ws, "_is_agent", return_value=False), sftp_patch(sftp):
            # Non-image binaries are named as binary, not as broken images.
            with self.assertRaisesRegex(ws.WorkspaceError, "file is binary"):
                await ws._read(1, 2, "h", {"path": "a.bin"}, "/repo")

    async def test_directory_read_points_at_list_dir(self):
        sftp = FakeSFTP(dirs={"/repo", "/repo/src"})
        with patch.object(ws, "_is_agent", return_value=False), sftp_patch(sftp):
            with self.assertRaisesRegex(ws.WorkspaceError, "list_dir"):
                await ws._read(1, 2, "h", {"path": "src"}, "/repo")


class ConnectorReadTest(unittest.IsolatedAsyncioTestCase):
    async def test_exact_limit_file_is_not_reported_as_continuing(self):
        async def call(_u, _h, op, args, seconds=30):
            if args["offset"] == 1:
                return {"ok": True, "out": "a\nb\n"}
            return {"ok": True, "out": ""}

        with patch.object(ws, "_is_agent", return_value=True), patch.object(ws, "_agent_call", call):
            text, _ = await ws._read(1, 2, "h", {"path": "f", "limit": 2}, "/repo")
        self.assertIn("(End of file — 2 lines.)", text)
        self.assertNotIn("continue", text)

    async def test_offset_past_end_is_an_error_like_sftp(self):
        with patch.object(ws, "_is_agent", return_value=True), patch.object(
            ws, "_agent_call", AsyncMock(return_value={"ok": True, "out": ""})
        ):
            with self.assertRaisesRegex(ws.WorkspaceError, "beyond the end"):
                await ws._read(1, 2, "h", {"path": "f", "offset": 50}, "/repo")



def assume_read(case):
    """Edits in this class are not about read-before-edit: every file counts as
    read and unchanged (tests/test_file_ledger.py covers the ledger itself)."""
    for name in ("lookup", "require_read"):
        patcher = patch.object(ws.file_ledger, name, return_value=ws.file_ledger.Seen(0))
        patcher.start()
        case.addCleanup(patcher.stop)


# --- write_file / search_replace ----------------------------------------------

class WriteTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        ws.file_ledger.clear()

    async def test_new_file_uses_umask_mode_and_reports_created(self):
        sftp = FakeSFTP()
        with patch.object(ws, "_is_agent", return_value=False), sftp_patch(sftp):
            text, _ = await ws._write(1, 2, "h", {"path": "new.py", "content": "x"}, "/repo", run_id="r")
        self.assertIn("created /repo/new.py (1 bytes)", text)
        self.assertEqual(max(sftp.created_modes.values()), 0o666)

    async def test_missing_parent_is_created(self):
        sftp = FakeSFTP()
        with patch.object(ws, "_is_agent", return_value=False), sftp_patch(sftp):
            await ws._write(1, 2, "h", {"path": "a/b/c.py", "content": "x"}, "/repo", run_id="r")
        self.assertEqual(sftp.files["/repo/a/b/c.py"], b"x")

    async def test_unread_existing_file_is_not_overwritten(self):
        sftp = FakeSFTP({"/repo/keep.py": b"precious"})
        with patch.object(ws, "_is_agent", return_value=False), sftp_patch(sftp):
            with self.assertRaisesRegex(ws.WorkspaceError, "has not been read"):
                await ws._write(1, 2, "h", {"path": "keep.py", "content": "x"}, "/repo", run_id="r")
        self.assertEqual(sftp.files["/repo/keep.py"], b"precious")

    async def test_read_then_write_succeeds_and_consecutive_writes_chain(self):
        sftp = FakeSFTP({"/repo/a.py": b"old\n"})
        with patch.object(ws, "_is_agent", return_value=False), sftp_patch(sftp):
            await ws._read(1, 2, "h", {"path": "a.py"}, "/repo", run_id="r")
            await ws._write(1, 2, "h", {"path": "a.py", "content": "v1\n"}, "/repo", run_id="r")
            await ws._write(1, 2, "h", {"path": "a.py", "content": "v2\n"}, "/repo", run_id="r")
        self.assertEqual(sftp.files["/repo/a.py"], b"v2\n")

    async def test_file_changed_since_read_is_refused(self):
        sftp = FakeSFTP({"/repo/a.py": b"old\n"})
        with patch.object(ws, "_is_agent", return_value=False), sftp_patch(sftp):
            await ws._read(1, 2, "h", {"path": "a.py"}, "/repo", run_id="r")
            sftp.files["/repo/a.py"] = b"someone else\n"
            sftp.mtimes["/repo/a.py"] = 99
            with self.assertRaisesRegex(ws.WorkspaceError, "changed since you last read"):
                await ws._write(1, 2, "h", {"path": "a.py", "content": "mine\n"}, "/repo", run_id="r")
        self.assertEqual(sftp.files["/repo/a.py"], b"someone else\n")

    async def test_other_run_must_read_first(self):
        sftp = FakeSFTP({"/repo/a.py": b"old\n"})
        with patch.object(ws, "_is_agent", return_value=False), sftp_patch(sftp):
            await ws._read(1, 2, "h", {"path": "a.py"}, "/repo", run_id="parent")
            with self.assertRaisesRegex(ws.WorkspaceError, "has not been read"):
                await ws._write(1, 2, "h", {"path": "a.py", "content": "x"}, "/repo", run_id="child")

    async def test_home_and_absolute_paths_share_a_ledger_entry(self):
        sftp = FakeSFTP({"/home/u/a.py": b"old\n"}, dirs={"/home/u"})
        with patch.object(ws, "_is_agent", return_value=False), sftp_patch(sftp):
            await ws._read(1, 7, "h", {"path": "~/a.py"}, "", run_id="r")
            await ws._write(1, 7, "h", {"path": "/home/u/a.py", "content": "new\n"}, "", run_id="r")
        self.assertEqual(sftp.files["/home/u/a.py"], b"new\n")

    async def test_unwritable_directory_falls_back_to_in_place_write(self):
        sftp = FakeSFTP({"/etc/app.conf": b"a=1\n"}, dirs={"/etc"}, deny_create_in={"/etc"})
        sftp.modes["/etc/app.conf"] = 0o664
        with patch.object(ws, "_is_agent", return_value=False), sftp_patch(sftp):
            await ws._read(1, 2, "h", {"path": "/etc/app.conf"}, "", run_id="r")
            await ws._write(1, 2, "h", {"path": "/etc/app.conf", "content": "a=2\n"}, "", run_id="r")
        self.assertEqual(sftp.files["/etc/app.conf"], b"a=2\n")
        self.assertEqual(sftp.modes["/etc/app.conf"], 0o664)

    async def test_existing_mode_reapplied_and_owner_preserved(self):
        sftp = FakeSFTP({"/repo/run.sh": b"#!/bin/sh\n"})
        sftp.modes["/repo/run.sh"] = 0o775
        with patch.object(ws, "_is_agent", return_value=False), sftp_patch(sftp):
            await ws._read(1, 2, "h", {"path": "run.sh"}, "/repo", run_id="r")
            await ws._write(1, 2, "h", {"path": "run.sh", "content": "#!/bin/sh\necho\n"}, "/repo", run_id="r")
        self.assertEqual(sftp.modes["/repo/run.sh"], 0o775)
        self.assertTrue(sftp.chowned and sftp.chowned[0][1:] == (1000, 1000))

    async def test_connector_write_creates_parent_then_writes(self):
        calls = []

        async def call(_u, _h, op, args, seconds=30):
            calls.append(op if op != "exec" else ("mkdir" if "mkdir" in args["cmd"] else "probe"))
            if op == "exec" and "mkdir" not in args["cmd"]:
                return {"ok": True, "exit": 0, "out": "missing\nna\ndmiss\n"}
            return {"ok": True, "exit": 0, "out": ""}

        with patch.object(ws, "_is_agent", return_value=True), patch.object(ws, "_agent_call", call):
            text, _ = await ws._write(1, 2, "h", {"path": "/tmp/new/x.py", "content": "hi"}, "/tmp", run_id="r")
        self.assertEqual(calls, ["probe", "mkdir", "write"])
        self.assertIn("created", text)

    async def test_connector_refuses_unread_existing_file(self):
        async def call(_u, _h, op, args, seconds=30):
            if op == "exec":
                return {"ok": True, "exit": 0, "out": "12\nw\ndw\n"}
            raise AssertionError("must not write")

        with patch.object(ws, "_is_agent", return_value=True), patch.object(ws, "_agent_call", call):
            with self.assertRaisesRegex(ws.WorkspaceError, "has not been read"):
                await ws._write(1, 2, "h", {"path": "/tmp/x.py", "content": "hi"}, "/tmp", run_id="r")


class ReplaceTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        ws.file_ledger.clear()
        assume_read(self)

    async def test_identical_strings_refused_before_transport(self):
        with patch.object(ws, "_agent_call", new_callable=AsyncMock) as call:
            with self.assertRaisesRegex(ws.WorkspaceError, "identical"):
                await ws._replace(1, 2, "h", {"path": "a", "old_string": "x", "new_string": "x"}, "/repo")
            call.assert_not_awaited()

    async def test_missing_file_is_named_not_tool_failed(self):
        with patch.object(ws, "_is_agent", return_value=False), sftp_patch(FakeSFTP()):
            with self.assertRaisesRegex(ws.WorkspaceError, "no such file"):
                await ws._replace(1, 2, "h", {"path": "a.py", "old_string": "x", "new_string": "y"}, "/repo")

    async def test_not_found_shows_closest_lines(self):
        body = b"def f():\n    if x:\n        return 1\n    return 2\n"
        sftp = FakeSFTP({"/repo/a.py": body})
        with patch.object(ws, "_is_agent", return_value=False), sftp_patch(sftp):
            with self.assertRaises(ws.WorkspaceError) as ctx:
                await ws._replace(1, 2, "h", {"path": "a.py", "old_string": "  if x:\n      return 1", "new_string": "z"}, "/repo")
        self.assertIn("Closest text in the file (lines 2-3", ctx.exception.detail)
        self.assertIn("3:         return 1", ctx.exception.detail)

    async def test_non_unique_lists_line_numbers(self):
        with self.assertRaisesRegex(ws.WorkspaceError, r"lines 1, 3"):
            ws._apply_replace("a\nb\na\n", "a", "x", False)

    async def test_connector_multi_edit_diff_shows_whole_lines(self):
        fresh = "".join(f"line {i:03d} xxxx\n" for i in range(1, 61))
        fresh = fresh.replace("line 050 ", "LINE 050 (edited) ").replace("line 060 ", "LINE 060 (last) ")
        edits = [{"old_string": "line 050 ", "new_string": "LINE 050 (edited) "},
                 {"old_string": "line 060 ", "new_string": "LINE 060 (last) "}]
        with patch.object(ws, "_is_agent", return_value=True), patch.object(ws, "_agent_version", return_value=(1, 7, 1)), \
                patch.object(ws, "_agent_call", AsyncMock(return_value={"ok": True, "out": "2 replacements"})), \
                patch.object(ws, "_agent_read_sample", AsyncMock(return_value=fresh)):
            text, _ = await ws._replace(1, 2, "h", {"path": "a.txt", "edits": edits}, "/repo")
        body = ws.unlabel_host("h", text)
        self.assertIn("@@ -50 +50 @@\n-line 050 xxxx\n+LINE 050 (edited) xxxx", body)
        self.assertIn("@@ -60 +60 @@\n-line 060 xxxx\n+LINE 060 (last) xxxx", body)

    async def test_success_returns_numbered_snippet(self):
        body = "".join(f"line{i}\n" for i in range(1, 21)).encode()
        sftp = FakeSFTP({"/repo/a.py": body})
        with patch.object(ws, "_is_agent", return_value=False), sftp_patch(sftp):
            text, _ = await ws._replace(1, 2, "h", {"path": "a.py", "old_string": "line10\n", "new_string": "TEN\n"}, "/repo")
        self.assertIn("updated /repo/a.py (1 replacement):", text)
        self.assertIn("10: TEN", text)
        self.assertIn("7: line7", text)
        self.assertNotIn("6: line6", text)


# --- grep / glob / list ---------------------------------------------------------

class SearchTest(unittest.IsolatedAsyncioTestCase):
    def test_grep_cmd_searches_hidden_and_bounds_time(self):
        cmd = ws._grep_search_cmd("x", ".", ignore_case=True, no_ignore=True, timeout_s=20)
        self.assertIn("--hidden", cmd)
        self.assertIn("--no-ignore", cmd)
        self.assertIn(" -i ", cmd)
        self.assertIn("timeout -k 2 20", cmd)
        self.assertIn("--max-columns", cmd)

    def test_fallback_grep_uses_pcre_when_available(self):
        if not shutil.which("grep"):
            self.skipTest("no grep")
        with tempfile.TemporaryDirectory() as root:
            Path(root, "a.txt").write_text("id 42\nnone\n")
            cmd = "command() { return 1; }; " + ws._grep_search_cmd(r"\d+", ".")
            proc = subprocess.run(["bash", "-c", cmd], capture_output=True, text=True, timeout=5, cwd=root)
        self.assertEqual(proc.stdout.strip(), "./a.txt:1:id 42")

    def test_search_target_is_relative_to_root(self):
        self.assertEqual(ws._search_target("/repo", "/repo", windows=False), ("/repo", "."))
        self.assertEqual(ws._search_target("/repo/src", "/repo", windows=False), ("/repo", "src"))
        self.assertEqual(ws._search_target("/etc", "/repo", windows=False), ("/repo", "/etc"))
        self.assertEqual(ws._search_target("~/p/src", "~/p", windows=False), ("~/p", "src"))

    def test_exit_2_with_matches_is_partial_success(self):
        notes = ws._search_status({"exit": 2, "out": "a.py:1:x\n", "err": "rg: b: Permission denied (os error 13)\n"}, "grep")
        self.assertIn("some files could not be searched", notes[0])
        with self.assertRaises(ws.WorkspaceError):
            ws._search_status({"exit": 2, "out": "", "err": "regex parse error"}, "grep")

    def test_timeout_is_partial_success(self):
        notes = ws._search_status({"exit": 124, "out": "a.py:1:x\n", "err": "", "timed_out": True}, "grep")
        self.assertIn("timed out", notes[0])

    async def test_grep_withholds_secret_files_and_strips_dot_slash(self):
        data = {"exit": 0, "out": "./app.py:3:KEY = env('X')\n./.env:1:KEY=hunter2\n", "err": "", "timed_out": False}
        with patch.object(ws, "_is_agent", return_value=False), patch.object(ws, "_tool_exec", AsyncMock(return_value=data)):
            text, _ = await ws._grep(1, 2, "h", {"pattern": "KEY"}, "/repo")
        self.assertIn("\napp.py:3:", text)
        self.assertNotIn("hunter2", text)
        self.assertIn("withheld", text)

    async def test_grep_explicit_secret_target_is_shown(self):
        data = {"exit": 0, "out": ".env:1:KEY=hunter2\n", "err": "", "timed_out": False}
        with patch.object(ws, "_is_agent", return_value=False), patch.object(ws, "_tool_exec", AsyncMock(return_value=data)):
            text, _ = await ws._grep(1, 2, "h", {"pattern": "KEY", "path": ".env"}, "/repo")
        self.assertIn("hunter2", text)

    async def test_grep_clips_long_lines_and_whole_lines_only(self):
        rows = "".join(f"a.py:{i}:{'y' * 900}\n" for i in range(1, 150))
        data = {"exit": 0, "out": rows, "err": "", "timed_out": False}
        with patch.object(ws, "_is_agent", return_value=False), patch.object(ws, "_tool_exec", AsyncMock(return_value=data)):
            text, _ = await ws._grep(1, 2, "h", {"pattern": "y", "max_matches": 200}, "/repo")
        body = ws.unlabel_host("h", text)
        self.assertLess(len(body), ws._SEARCH_OUT_CHARS + 400)
        self.assertIn("output clipped at", body)
        for line in body.splitlines():
            if line.startswith("a.py:"):
                self.assertTrue(line.endswith("chars]"))

    async def test_ssh_timeout_returns_partial_not_tool_failed(self):
        exc = asyncssh.TimeoutError(None, None, None, None, None, None, "./a.py:1:hit\n", "", "timed out")
        conn = SimpleNamespace(run=AsyncMock(side_effect=exc))
        with patch.object(ws, "_is_agent", return_value=False), patch("app.ssh_pool.connection", AsyncMock(return_value=conn)):
            text, rec = await ws._grep(1, 2, "h", {"pattern": "hit"}, "/repo")
        self.assertEqual(rec["status"], "done")
        self.assertIn("a.py:1:hit", text)
        self.assertIn("timed out", text)

    def test_ssh_output_drops_login_profile_noise(self):
        self.assertEqual(ws._strip_begin("Welcome!\n" + ws._SSH_BEGIN + "\na.py:1:x\n"), "a.py:1:x\n")

    def test_glob_accepts_real_file_names(self):
        for pattern in ("src/routes/**/+page.svelte", "node_modules/@scope/**", "docs/My File.md"):
            self.assertEqual(ws._confine_glob(pattern, "/r"), pattern)
        for pattern in ("a | wc", "*.py, *.js", "$(id)"):
            with self.assertRaises(ws.WorkspaceError):
                ws._confine_glob(pattern, "/r")

    def test_glob_result_is_sorted_relative_and_line_clipped(self):
        out = "".join(f"./src/f{i:03d}_{'n' * 60}.py\n" for i in range(399, -1, -1))
        text = ws._glob_result(out)
        lines = text.splitlines()
        self.assertTrue(lines[0].startswith("src/f000_"))
        self.assertNotIn("./", text)
        self.assertIn("showing the first 400 paths", text)


# --- shell ------------------------------------------------------------------------

class ShellScriptTest(unittest.TestCase):
    def run_script(self, script, **kw):
        return subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=10, **kw)

    def test_failed_cd_stops_the_whole_command_list(self):
        proc = self.run_script(ws._ssh_job_script("/nonexistent-dir", "echo A; echo B"))
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(proc.stdout, "")

    def test_stdin_is_closed(self):
        start = time.monotonic()
        proc = self.run_script(ws._ssh_job_script("/tmp", "read line; echo rc=$?"), stdin=subprocess.PIPE)
        self.assertEqual(proc.stdout.strip(), "rc=1")
        self.assertLess(time.monotonic() - start, 5)

    def test_noninteractive_env(self):
        proc = self.run_script(ws._ssh_job_script("/tmp", "echo $GIT_TERMINAL_PROMPT:$PAGER"))
        self.assertEqual(proc.stdout.strip(), "0:cat")

    @unittest.skipUnless(shutil.which("setsid") and shutil.which("ps"), "needs setsid and ps")
    def test_group_kill_terminates_children(self):
        home = tempfile.mkdtemp()
        env = {**os.environ, "HOME": home}
        marker = f"sleep 31{os.getpid() % 10}7"
        proc = subprocess.Popen(
            ["setsid", "bash", "-c", ws._ssh_job_script("/tmp", f"{marker} & {marker}; wait", "tok")],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env,
        )
        try:
            deadline = time.monotonic() + 5
            pgf = Path(home, ".cache/fuse/jobs/tok")
            while not pgf.exists() and time.monotonic() < deadline:
                time.sleep(0.05)
            proc.kill()
            proc.wait()
            alive = subprocess.run(["pgrep", "-f", marker], capture_output=True, text=True).stdout.split()
            self.assertTrue(alive, "children should survive a top-shell kill")
            subprocess.run(["bash", "-c", ws._ssh_kill_script("tok")], env=env, timeout=10)
            time.sleep(0.3)
            alive = subprocess.run(["pgrep", "-f", marker], capture_output=True, text=True).stdout.split()
            self.assertEqual(alive, [])
            self.assertFalse(pgf.exists())
        finally:
            subprocess.run(["pkill", "-f", marker])


class DenylistTest(unittest.TestCase):
    def test_blocks_named_catastrophes_and_bypasses(self):
        for cmd in ("rm -rf /", "rm -rf /*", "ls\nsudo reboot", "echo $(sudo id)", "x && shutdown -h now",
                    "mkfs.ext4 /dev/sdb", "ssh host ls", "chmod -R 777 /"):
            self.assertIsNotNone(ws.denied(cmd), cmd)

    def test_allows_ordinary_commands(self):
        for cmd in ("rm -rf /tmp/fuse/build", "rm -rf ./dist /tmp/x", "chmod 777 ./run.sh", "ssh-keygen -l -f k.pub",
                    "ssh-add -l", "git commit -m 'reboot flow'", "grep -r shutdown app/", "pip install sshtunnel"):
            self.assertIsNone(ws.denied(cmd), cmd)


class JobOutputTest(unittest.TestCase):
    def test_head_and_tail_survive(self):
        rec = {"out": ""}
        text = "FIRST ERROR\n" + ("noise\n" * 10000) + "LAST LINE\n"
        for i in range(0, len(text), 777):
            jobs._append_out(rec, text[i:i + 777])
        body = jobs._model_body(rec, rec["out"])
        self.assertTrue(body.startswith("FIRST ERROR"))
        self.assertTrue(body.rstrip().endswith("LAST LINE"))
        self.assertIn("characters of output omitted", body)

    def test_head_and_tail_cut_on_line_boundaries_with_exact_gap(self):
        rec = {"out": ""}
        text = "".join(f"{i} abcdefghij\n" for i in range(1, 60001))
        for i in range(0, len(text), 777):
            jobs._append_out(rec, text[i:i + 777])
        body = jobs._model_body(rec, rec["out"])
        head, rest = body.split("\n[... ", 1)
        count, tail = rest.split(" characters of output omitted ...]\n", 1)
        # Both sides are whole lines, and the gap rejoined with its two line
        # breaks is exactly the original length.
        self.assertTrue(text.startswith(head + "\n"), head[-40:])
        self.assertTrue(text.endswith("\n" + tail), tail[:40])
        self.assertEqual(len(head) + 1 + int(count) + 1 + len(tail), len(text))

    def test_head_survives_when_host_dropped_output(self):
        # A connector that dropped chunks makes the total inexact; the head
        # Fuse did receive must still be shown, with the label.
        rec = {"out": "", "output_incomplete": True}
        text = "FIRST ERROR\n" + ("noise\n" * 10000) + "LAST LINE\n"
        for i in range(0, len(text), 777):
            jobs._append_out(rec, text[i:i + 777])
        body = jobs._model_body(rec, rec["out"])
        self.assertTrue(body.startswith("[Output truncated; total unknown]\nFIRST ERROR"))
        self.assertIn("characters of output omitted", body)
        self.assertTrue(body.rstrip().endswith("LAST LINE"))

    def test_short_output_is_verbatim(self):
        rec = {"out": ""}
        jobs._append_out(rec, "hello\n")
        self.assertEqual(jobs._model_body(rec, rec["out"]), "hello\n")


# --- web --------------------------------------------------------------------------

class ReadUrlPagingTest(unittest.TestCase):
    def test_offset_pages_through_long_text(self):
        full = "".join(f"word{i} " for i in range(8000))
        first = extract_mod.extract_page(full, content_type="text/plain")
        self.assertEqual(first.offset, 0)
        self.assertIsNotNone(first.next_offset)
        packed = pack_page("https://example.com/a", first)
        self.assertIn(f"offset={first.next_offset}", packed)
        second = extract_mod.extract_page(full, content_type="text/plain", offset=first.next_offset)
        self.assertEqual(second.offset, first.next_offset)
        self.assertTrue(second.text.startswith(full[first.next_offset:first.next_offset + 20].strip()))
        last = extract_mod.extract_page(full, content_type="text/plain", offset=len(full) - 10)
        self.assertIsNone(last.next_offset)
        past = extract_mod.extract_page(full, content_type="text/plain", offset=len(full) + 5)
        self.assertIn("past the end", pack_page("https://example.com/a", past))

    def test_query_mode_is_not_paged(self):
        full = "".join(f"word{i} " for i in range(8000))
        got = extract_mod.extract_page(full, content_type="text/plain", query="word7000")
        self.assertFalse(got.paged)
        self.assertIsNone(got.next_offset)


class SearchBudgetTest(unittest.IsolatedAsyncioTestCase):
    async def test_dropped_queries_are_reported(self):
        from app import chat_tools
        from app.search import QueryResult

        async def fake_bulk(queries, **kw):
            return [QueryResult(query=q, hits=[], error="", kind="ok", provider="x", latency_ms=1, cached=False, status=200) for q in queries]

        budgets = {"search_queries": 31}
        with patch.object(chat_tools, "search_bulk", fake_bulk), patch.object(chat_tools.settings, "web_search_queries_per_turn", 32):
            text, _ = await chat_tools.execute_tool(
                "web_search", json.dumps({"queries": ["alpha one", "beta two", "gamma three"]}),
                user_id=1, allowed_urls=set(), budgets=budgets,
            )
        self.assertIn("2 query(s) were NOT run", text)
        self.assertIn("'beta two'", text)


# --- permissions --------------------------------------------------------------------

class SecretGrepGateTest(unittest.IsolatedAsyncioTestCase):
    async def test_grep_secret_outside_root_asks_inside_is_silent(self):
        gate = permissions.Gate(user_id=1, thread_id=5, run_id="r", requester={"role": "main"},
                                unattended=True, notify=lambda _e: None, cancel=asyncio.Event())
        with patch.object(permissions, "bypass_enabled", return_value=False), patch.object(
            permissions, "grant_covers", return_value=False
        ), patch.object(permissions, "auto_enabled", return_value=False):
            # Secret-named files inside an approved dir are ordinary in-scope
            # reads; outside the roots they still gate (grep prints contents).
            inside = await permissions.review(tool="grep", args={"pattern": "K", "path": ".env"}, user_id=1,
                                              host_id=2, host_alias="h", root="/repo", windows=False, gate=gate)
            outside = await permissions.review(tool="grep", args={"pattern": "K", "path": "/home/other/.env"}, user_id=1,
                                               host_id=2, host_alias="h", root="/repo", windows=False, gate=gate)
            plain = await permissions.review(tool="grep", args={"pattern": "K", "path": "src"}, user_id=1,
                                             host_id=2, host_alias="h", root="/repo", windows=False, gate=gate)
        self.assertTrue(inside.allowed)
        self.assertFalse(outside.allowed)
        self.assertTrue(plain.allowed)


# --- runtime ordering ----------------------------------------------------------------

class FakeRequest:
    async def is_disconnected(self):
        return False


class SameStepOrderingTest(unittest.IsolatedAsyncioTestCase):
    async def test_host_mutations_keep_call_order_and_reads_wait(self):
        log = []

        async def fake_execute(name, arguments, **kwargs):
            args = json.loads(arguments)
            tag = args.get("tag")
            log.append(("start", tag))
            # Earlier calls are slower: without ordering they would finish last.
            await asyncio.sleep({"e1": 0.15, "e2": 0.05}.get(tag, 0.01))
            log.append(("end", tag))
            return "ok", {"name": name, "status": "done"}

        def call(i, name, tag):
            return {"id": f"c{i}", "function": {"name": name, "arguments": json.dumps({"tag": tag})}}

        calls = [
            call(0, "search_replace", "e1"),
            call(1, "search_replace", "e2"),
            call(2, "read_file", "r1"),
            call(3, "run_terminal_command", "t1"),
            call(4, "todo_write", "free"),
        ]
        with patch("app.chat_runtime.execute_tool", fake_execute):
            async for _ in runtime._run_tools(calls, user_id=1, allowed_urls=set(), request=FakeRequest(),
                                              completion_i=0, budgets={}, coding=True):
                pass
        ends = [tag for kind, tag in log if kind == "end"]
        self.assertLess(ends.index("e1"), ends.index("e2"))
        self.assertLess(ends.index("e2"), ends.index("r1"))
        self.assertLess(ends.index("r1"), ends.index("t1"))
        # Independent tools are not held back by host ordering.
        self.assertLess(log.index(("start", "free")), log.index(("end", "e1")))

    async def test_host_ordering_is_per_host(self):
        log = []

        async def fake_execute(name, arguments, **kwargs):
            tag = json.loads(arguments)["tag"]
            log.append(("start", tag))
            await asyncio.sleep(0.05)
            log.append(("end", tag))
            return "ok", {"name": name, "status": "done"}

        def call(i, tag, host=None):
            args = {"command": "true", "tag": tag}
            if host is not None:
                args["host"] = host
            return {"id": f"c{i}", "function": {"name": "run_terminal_command", "arguments": json.dumps(args)}}

        calls = [call(0, "a1", "a"), call(1, "b1", "b"), call(2, "a2", "A "), call(3, "bound")]
        with patch("app.chat_runtime.execute_tool", fake_execute):
            async for _ in runtime._run_tools(calls, user_id=1, allowed_urls=set(), request=FakeRequest(),
                                              completion_i=0, budgets={}, coding=True):
                pass
        # Different hosts run together; the same host (any case) keeps call order;
        # an omitted host may be any of them, so it waits for all.
        self.assertLess(log.index(("start", "b1")), log.index(("end", "a1")))
        self.assertLess(log.index(("end", "a1")), log.index(("start", "a2")))
        self.assertLess(log.index(("end", "a2")), log.index(("start", "bound")))
        self.assertLess(log.index(("end", "b1")), log.index(("start", "bound")))

    async def test_reads_still_run_concurrently(self):
        running = 0
        peak = 0

        async def fake_execute(name, arguments, **kwargs):
            nonlocal running, peak
            running += 1
            peak = max(peak, running)
            await asyncio.sleep(0.05)
            running -= 1
            return "ok", {"name": name, "status": "done"}

        calls = [{"id": f"c{i}", "function": {"name": "grep", "arguments": json.dumps({"pattern": str(i)})}} for i in range(4)]
        with patch("app.chat_runtime.execute_tool", fake_execute):
            async for _ in runtime._run_tools(calls, user_id=1, allowed_urls=set(), request=FakeRequest(),
                                              completion_i=0, budgets={}, coding=True):
                pass
        self.assertEqual(peak, 4)


# --- SFTP session reuse ------------------------------------------------------------

class SftpPoolTest(unittest.IsolatedAsyncioTestCase):
    async def test_one_subsystem_per_connection(self):
        client = FakeSFTP()
        conn = type("Conn", (), {})()  # weakref-able, like SSHClientConnection
        conn.start_sftp_client = AsyncMock(return_value=client)
        with patch("app.ssh_pool.connection", AsyncMock(return_value=conn)):
            for _ in range(3):
                async with await ws._sftp(1, 2) as sftp:
                    self.assertIs(sftp, client)
        self.assertEqual(conn.start_sftp_client.await_count, 1)

    async def test_transport_error_evicts_client(self):
        conn = type("Conn", (), {})()
        first, second = FakeSFTP(), FakeSFTP()
        first.exit = lambda: None
        conn.start_sftp_client = AsyncMock(side_effect=[first, second])
        with patch("app.ssh_pool.connection", AsyncMock(return_value=conn)):
            with self.assertRaises(asyncssh.SFTPConnectionLost):
                async with await ws._sftp(1, 2):
                    raise asyncssh.SFTPConnectionLost("lost")
            async with await ws._sftp(1, 2) as sftp:
                self.assertIs(sftp, second)


if __name__ == "__main__":
    unittest.main()


# --- follow-up: multi-edit, grep modes, glob sort, list depth, budgets -------------

class MultiEditTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        ws.file_ledger.clear()
        assume_read(self)

    async def test_edits_apply_in_order_and_publish_once(self):
        sftp = FakeSFTP({"/repo/a.py": b"alpha\nbeta\ngamma\n"})
        with patch.object(ws, "_is_agent", return_value=False), sftp_patch(sftp):
            text, _ = await ws._replace(1, 2, "h", {"path": "a.py", "edits": [
                {"old_string": "alpha", "new_string": "A"},
                {"old_string": "A\nbeta", "new_string": "A\nB"},
            ]}, "/repo")
        self.assertEqual(sftp.files["/repo/a.py"], b"A\nB\ngamma\n")
        self.assertIn("2 replacements from 2 edits", text)
        self.assertIn("-alpha", text)
        self.assertIn("+A", text)

    async def test_failing_edit_applies_nothing_and_names_it(self):
        sftp = FakeSFTP({"/repo/a.py": b"alpha\nbeta\n"})
        with patch.object(ws, "_is_agent", return_value=False), sftp_patch(sftp):
            with self.assertRaisesRegex(ws.WorkspaceError, r"edit 2 of 2: old_string not found(.|\n)*No edits were applied"):
                await ws._replace(1, 2, "h", {"path": "a.py", "edits": [
                    {"old_string": "alpha", "new_string": "A"},
                    {"old_string": "nope", "new_string": "x"},
                ]}, "/repo")
        self.assertEqual(sftp.files["/repo/a.py"], b"alpha\nbeta\n")

    def test_edit_validation(self):
        with self.assertRaisesRegex(ws.WorkspaceError, "either edits"):
            ws._parse_edits({"edits": [{"old_string": "a", "new_string": "b"}], "old_string": "a"})
        with self.assertRaisesRegex(ws.WorkspaceError, r"edits\[1\]: .*identical"):
            ws._parse_edits({"edits": [{"old_string": "a", "new_string": "b"}, {"old_string": "c", "new_string": "c"}]})
        with self.assertRaisesRegex(ws.WorkspaceError, "1 to 100"):
            ws._parse_edits({"edits": []})

    async def test_connector_multi_edit_is_version_gated(self):
        calls = []

        async def call(_u, _h, op, args, seconds=30):
            calls.append((op, args))
            return {"ok": True, "out": "3 replacement(s)"}

        edits = {"path": "/r/a.py", "edits": [{"old_string": "a", "new_string": "b"}, {"old_string": "c", "new_string": "d"}]}
        with patch.object(ws, "_is_agent", return_value=True), patch.object(ws, "_agent_call", call), \
                patch.object(ws, "_agent_version", return_value=(1, 3, 3)):
            with self.assertRaisesRegex(ws.WorkspaceError, "needs connector 1.4.0"):
                await ws._replace(1, 2, "h", dict(edits), "/r")
        self.assertEqual(calls, [])
        with patch.object(ws, "_is_agent", return_value=True), patch.object(ws, "_agent_call", call), \
                patch.object(ws, "_agent_version", return_value=(1, 4, 0)):
            text, _ = await ws._replace(1, 2, "h", dict(edits), "/r")
        self.assertEqual(calls[0][1]["edits"][1], {"old_string": "c", "new_string": "d", "replace_all": False})
        self.assertIn("3 replacements from 2 edits", text)


class HardLinkTest(unittest.IsolatedAsyncioTestCase):
    async def test_linked_file_is_rewritten_in_place(self):
        sftp = FakeSFTP({"/repo/a.py": b"old\n"})
        calls = []
        orig_atomic = ws._sftp_atomic_write

        async def spy_atomic(*a, **k):
            calls.append("atomic")
            return await orig_atomic(*a, **k)

        with patch.object(ws, "_ssh_link_count", AsyncMock(return_value=2)), patch.object(ws, "_sftp_atomic_write", spy_atomic):
            await ws._sftp_publish(1, 2, sftp, "/repo/a.py", b"new\n", attrs=await sftp.lstat("/repo/a.py"))
        self.assertEqual(calls, [])
        self.assertEqual(sftp.files["/repo/a.py"], b"new\n")
        with patch.object(ws, "_ssh_link_count", AsyncMock(return_value=1)), patch.object(ws, "_sftp_atomic_write", spy_atomic):
            await ws._sftp_publish(1, 2, sftp, "/repo/a.py", b"newer\n", attrs=await sftp.lstat("/repo/a.py"))
        self.assertEqual(calls, ["atomic"])


class GrepModeTest(unittest.IsolatedAsyncioTestCase):
    def test_cmd_flags_per_mode(self):
        files = ws._grep_search_cmd("x", ".", mode="files_with_matches", max_matches=30)
        self.assertIn(" -l ", files)
        self.assertIn("--sort path", files)
        self.assertIn("head -n 30", files)
        count = ws._grep_search_cmd("x", ".", mode="count")
        self.assertIn(" -c ", count)
        self.assertIn("grep -v ':0$'", count)
        ctx = ws._grep_search_cmd("x", ".", context=2, max_matches=10)
        self.assertIn("-C 2", ctx)
        self.assertIn("head -n 60", ctx)

    def test_fallback_modes_run(self):
        with tempfile.TemporaryDirectory() as root:
            Path(root, "a.txt").write_text("one\ntwo\nthree\nfour\n")
            Path(root, "b.txt").write_text("none\n")
            def run(**kw):
                cmd = "command() { return 1; }; " + ws._grep_search_cmd("two|four", ".", **kw)
                return subprocess.run(["bash", "-c", cmd], capture_output=True, text=True, timeout=5, cwd=root).stdout
            self.assertEqual(run(mode="files_with_matches").split(), ["./a.txt"])
            self.assertEqual(run(mode="count").split(), ["./a.txt:2"])
            self.assertIn("./a.txt-3-three", run(context=1))

    def test_fallback_missing_target_is_an_error(self):
        # Without rg, a path that does not exist must fail like rg does, not
        # walk the current directory (the project root) under a bogus prefix.
        with tempfile.TemporaryDirectory() as root:
            Path(root, "a.txt").write_text("two\n")
            for cmd in (ws._grep_search_cmd("two", "nope/x.py"), ws._glob_search_cmd("*.txt", "nope/x.py")):
                run = subprocess.run(["bash", "-c", "command() { return 1; }; " + cmd],
                                     capture_output=True, text=True, timeout=5, cwd=root)
                self.assertEqual((run.returncode, run.stdout), (2, ""))
                self.assertIn("nope/x.py: No such file or directory", run.stderr)

    async def test_context_output_counts_matches_and_filters_secrets(self):
        out = "src/a.py-1-before\nsrc/a.py:2:KEY\nsrc/a.py-3-after\n--\n.env-1-x\n.env:2:KEY=1\n.env-3-y\n"
        data = {"exit": 0, "out": out, "err": "", "timed_out": False}
        with patch.object(ws, "_is_agent", return_value=False), patch.object(ws, "_tool_exec", AsyncMock(return_value=data)):
            text, _ = await ws._grep(1, 2, "h", {"pattern": "KEY", "context": 1}, "/repo")
        body = ws.unlabel_host("h", text)
        self.assertTrue(body.startswith("src/a.py-1-before\nsrc/a.py:2:KEY\nsrc/a.py-3-after\n"))
        self.assertNotIn(".env", body.split("(")[0])
        self.assertIn("1 result(s) in secret-named files", body)
        self.assertNotIn("\n--\n(", body)

    async def test_files_mode_sorted_and_capped(self):
        data = {"exit": 0, "out": "c.py\na.py\nb.py\n", "err": "", "timed_out": False}
        with patch.object(ws, "_is_agent", return_value=False), patch.object(ws, "_tool_exec", AsyncMock(return_value=data)):
            text, _ = await ws._grep(1, 2, "h", {"pattern": "x", "output_mode": "files_with_matches", "max_matches": 3}, "/repo")
        self.assertIn("a.py\nb.py\nc.py\n(showing the first 3 files", ws.unlabel_host("h", text))

    async def test_connector_marker_inside_a_match_is_content(self):
        # Through the real connector path: only the operation's truncated flag
        # (or a trailing marker) is a clip, never the marker text in a match.
        line = 'agent/src/exec.c:164:    const char *mark = "\\n[fuse-agent: output truncated]\\n";\n'
        reply = {"ok": True, "exit": 0, "out": line, "err": "", "truncated": False}
        with patch.object(ws, "_is_agent", return_value=True), patch.object(ws, "_agent_call", AsyncMock(return_value=reply)):
            text, _ = await ws._grep(1, 2, "h", {"pattern": "output truncated"}, "/repo")
        body = ws.unlabel_host("h", text)
        self.assertIn("agent/src/exec.c:164:", body)
        self.assertNotIn("output limit", body)

    async def test_windows_modes_need_new_connector(self):
        with patch.object(ws, "_is_agent", return_value=True), patch.object(ws, "_agent_version", return_value=(1, 3, 3)):
            with self.assertRaisesRegex(ws.WorkspaceError, "needs connector"):
                await ws._grep(1, 2, "h", {"pattern": "x", "output_mode": "count"}, "C:\\r", windows=True)

    def test_bad_mode_rejected(self):
        with self.assertRaisesRegex(ws.WorkspaceError, "output_mode"):
            ws._grep_mode_arg({"output_mode": "lines"})


class GlobSortTest(unittest.TestCase):
    def test_modified_uses_rg_sortr_and_keeps_order(self):
        cmd = ws._glob_search_cmd("**/*.py", ".", order="modified")
        self.assertIn("--sortr modified", cmd)
        self.assertIn("-printf \"%T@ %p\\n\"' _ | sort -rn", cmd)
        self.assertEqual(ws._glob_result("z.py\na.py\n", order="modified").split(), ["z.py", "a.py"])
        self.assertEqual(ws._glob_result("z.py\na.py\n").split(), ["a.py", "z.py"])

    def test_fallback_modified_order_runs(self):
        with tempfile.TemporaryDirectory() as root:
            for i, name in enumerate(("old.py", "new.py")):
                p = Path(root, name)
                p.write_text("x")
                os.utime(p, (1_000_000 + i * 1000, 1_000_000 + i * 1000))
            cmd = "command() { return 1; }; " + ws._glob_search_cmd("*.py", ".", order="modified")
            out = subprocess.run(["bash", "-c", cmd], capture_output=True, text=True, timeout=5, cwd=root).stdout
        self.assertEqual([Path(p).name for p in out.split()], ["new.py", "old.py"])


class ListDepthTest(unittest.IsolatedAsyncioTestCase):
    async def test_sftp_tree_lists_vendor_dirs_without_expanding(self):
        class Entry:
            def __init__(self, name, mode):
                self.filename = name
                self.attrs = SimpleNamespace(permissions=mode)

        tree = {
            "/repo": [Entry("src", 0o040755), Entry("node_modules", 0o040755), Entry("README", 0o100644)],
            "/repo/src": [Entry("a.py", 0o100644), Entry("deep", 0o040755)],
            "/repo/src/deep": [Entry("b.py", 0o100644)],
            "/repo/node_modules": [Entry("pkg", 0o040755)],
        }

        class TreeSFTP(FakeSFTP):
            async def scandir(self, path):
                for entry in tree.get(path, []):
                    yield entry

        with patch.object(ws, "_is_agent", return_value=False), sftp_patch(TreeSFTP()):
            text, _ = await ws._list(1, 2, "h", {"path": ".", "depth": 2}, "/repo")
        self.assertEqual(ws.unlabel_host("h", text).split("\n"),
                         ["README", "node_modules/", "src/", "src/a.py", "src/deep/"])

    def test_posix_tree_command_runs(self):
        with tempfile.TemporaryDirectory() as root:
            for d in ("src/deep", "node_modules/pkg"):
                Path(root, d).mkdir(parents=True)
            Path(root, "src/deep/b.py").write_text("x")
            out = subprocess.run(["bash", "-c", ws._tree_cmd_posix(3, ".")], capture_output=True, text=True, timeout=5, cwd=root).stdout
        self.assertEqual(sorted(line.removeprefix("./") for line in out.split()),
                         ["node_modules/", "src/", "src/deep/", "src/deep/b.py"])

    async def test_windows_depth_needs_new_connector(self):
        with patch.object(ws, "_is_agent", return_value=True), patch.object(ws, "_agent_version", return_value=(1, 3, 3)):
            with self.assertRaisesRegex(ws.WorkspaceError, "needs connector"):
                await ws._list(1, 2, "h", {"path": ".", "depth": 2}, "C:\\r", windows=True)


class CodingBudgetTest(unittest.IsolatedAsyncioTestCase):
    async def test_coding_runs_get_a_larger_search_budget(self):
        from app import chat_tools
        from app.search import QueryResult

        async def fake_bulk(queries, **kw):
            return [QueryResult(query=q, hits=[], error="", kind="ok", provider="x", latency_ms=1, cached=False, status=200) for q in queries]

        with patch.object(chat_tools, "search_bulk", fake_bulk), patch.object(chat_tools.settings, "web_search_queries_per_turn", 32):
            budgets = {"search_queries": 40}
            text, _ = await chat_tools.execute_tool("web_search", json.dumps({"query": "alpha"}), user_id=1,
                                                    allowed_urls=set(), budgets=budgets, coding=False)
            self.assertIn("exhausted", text)
            text, _ = await chat_tools.execute_tool("web_search", json.dumps({"query": "alpha"}), user_id=1,
                                                    allowed_urls=set(), budgets=budgets, coding=True)
            self.assertNotIn("exhausted", text)
