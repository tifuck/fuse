"""Regression tests for data preservation and bounded workspace operations."""
import asyncio
import io
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import asyncssh

from app import workspace as ws


class Handle:
    def __init__(self, data=b""):
        self.data = io.BytesIO(data)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def read(self, n):
        return self.data.read(n)

    async def write(self, data):
        return self.data.write(data)


class SFTP:
    def __init__(self, data, rename_error=None):
        self.original = data
        self.rename_error = rename_error
        self.removed = []
        self.written = None
        self.mode = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def lstat(self, path):
        return SimpleNamespace(permissions=0o100751)

    def open(self, path, mode, attrs=None):
        if mode == "rb":
            return Handle(self.original)
        self.mode = attrs.permissions
        self.written = Handle()
        return self.written

    async def posix_rename(self, src, dst):
        if self.rename_error:
            raise self.rename_error
        self.original = self.written.data.getvalue()

    async def remove(self, path):
        self.removed.append(path)


class FileReliabilityTest(unittest.IsolatedAsyncioTestCase):
    async def test_failed_publish_never_deletes_original(self):
        for failure in (OSError("disk error"), asyncssh.SFTPOpUnsupported("unsupported")):
            sftp = SFTP(b"original", failure)
            with self.assertRaises((OSError, ws.WorkspaceError)):
                await ws._sftp_atomic_write(sftp, "/repo/file", b"new")
            self.assertEqual(sftp.original, b"original")
            self.assertTrue(sftp.removed)
            self.assertNotIn("/repo/file", sftp.removed)

    async def test_atomic_publish_preserves_executable_mode(self):
        sftp = SFTP(b"original")
        await ws._sftp_atomic_write(sftp, "/repo/file", b"new")
        self.assertEqual(sftp.original, b"new")
        self.assertEqual(sftp.mode, 0o751)

    async def test_large_edit_is_refused_before_any_write(self):
        sftp = SFTP(b"old\n" + b"x" * (64 * 1024 * 1024))
        with patch.object(ws, "_is_agent", return_value=False), patch.object(ws, "_sftp", AsyncMock(return_value=sftp)):
            with self.assertRaisesRegex(ws.WorkspaceError, "too large"):
                await ws._replace(1, 2, "test", {"path": "file", "old_string": "old", "new_string": "new"}, "/repo")
        self.assertIsNone(sftp.written)
        self.assertTrue(sftp.original.startswith(b"old"))

    async def test_read_can_page_beyond_first_64k(self):
        data = b"prefix\n" * 20000 + b"wanted\nlast\n"
        window, more = await ws._sftp_read_window(Handle(data), 20001, 2)
        self.assertEqual(window, b"wanted\nlast\n")
        self.assertFalse(more)

    async def test_skip_long_line_uses_bounded_memory(self):
        window, more = await ws._sftp_read_window(Handle(b"x" * 2000000 + b"\nwanted\n"), 2, 1)
        self.assertEqual(window, b"wanted\n")
        self.assertFalse(more)

    async def test_utf8_write_limit_is_bytes_and_nul_is_rejected(self):
        for content in ("\u20ac" * 140000, "before\x00after"):
            with self.assertRaises(ws.WorkspaceError):
                await ws._write(1, 2, "test", {"path": "file", "content": content}, "/repo")

    def test_read_clipping_never_skips_unseen_lines(self):
        out = ws._read_render(["a" * 20] * 100, 1, total=100, more=False, max_chars=50)
        self.assertIn("Use offset=3 to continue", out)
        self.assertNotIn("End of file", out)

    def test_control_characters_do_not_create_fake_line_numbers(self):
        # The transport splits on newline, not Python's broader splitlines set.
        source = "one\vpart\ntwo\n"
        async def read():
            with patch.object(ws, "_is_agent", return_value=True), patch.object(ws, "_agent_call", AsyncMock(return_value={"ok": True, "out": source})):
                return await ws._read(1, 2, "test", {"path": "file"}, "/repo")
        out, _ = asyncio.run(read())
        self.assertIn("2: two", out)


class SearchReliabilityTest(unittest.TestCase):
    def test_fallback_regex_and_single_file_path(self):
        with tempfile.TemporaryDirectory() as root:
            p = Path(root) / "a.txt"
            p.write_text("alpha\nbeta\n")
            cmd = "command() { return 1; }; " + ws._grep_search_cmd("alpha|beta", str(p))
            proc = subprocess.run(["bash", "-c", cmd], capture_output=True, text=True, timeout=5)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(proc.stdout.splitlines(), [f"{p}:1:alpha", f"{p}:2:beta"])

    def test_complex_glob_without_rg_is_exact(self):
        # Hosts without rg used to refuse ** and brace globs outright, so
        # models dropped the filter and searched everything.
        with tempfile.TemporaryDirectory() as root:
            for rel in ("src/a.py", "src/x/b.py", "src/d.js", "other/c.py", "srcx/e.py"):
                Path(root, rel).parent.mkdir(parents=True, exist_ok=True)
                Path(root, rel).write_text("x\n")

            def run(pattern):
                cmd = "command() { return 1; }; " + ws._glob_search_cmd(pattern, ".")
                proc = subprocess.run(["bash", "-c", cmd], capture_output=True, text=True, timeout=5, cwd=root)
                self.assertEqual(proc.returncode, 0, proc.stderr)
                return sorted(line.removeprefix("./") for line in proc.stdout.split())

            self.assertEqual(run("src/**/*.py"), ["src/a.py", "src/x/b.py"])
            self.assertEqual(run("src/*.{py,js}"), ["src/a.py", "src/d.js"])
            self.assertEqual(run("*.py"), ["other/c.py", "src/a.py", "src/x/b.py", "srcx/e.py"])

    def test_grep_pipeline_survives_head_close_with_sigpipe_ignored(self):
        # Connectors exec with SIGPIPE ignored, so head closing the pipe leaves
        # grep dying on EPIPE ("write error: Broken pipe", exit 2) instead of
        # SIGPIPE 141. The truncated matches must still be tolerated and kept.
        with tempfile.TemporaryDirectory() as root:
            Path(root, "a.txt").write_text("hit\n" * 50_000)
            cmd = "trap '' PIPE; command() { return 1; }; " + ws._grep_search_cmd("hit", str(root), max_matches=5)
            proc = subprocess.run(["bash", "-c", cmd], capture_output=True, text=True, timeout=5)
            self.assertEqual(proc.returncode, 2)
            self.assertIn("Broken pipe", proc.stderr)
            self.assertEqual(len(proc.stdout.splitlines()), 5)
            self.assertTrue(ws._pipe_ok_exit(proc.returncode, proc.stderr))
            self.assertFalse(ws._pipe_ok_exit(proc.returncode, "grep: unbalanced parenthesis"))

    def test_windows_include_parses_drive_letter(self):
        self.assertEqual(ws._filter_grep_include("C:\\repo\\a.py:2:hit", "*.py"), "C:\\repo\\a.py:2:hit")

    def test_glob_reports_byte_clipping(self):
        self.assertIn("output clipped", ws._glob_result(("a" * 200 + "\n") * 100))
