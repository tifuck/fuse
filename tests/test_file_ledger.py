"""Read-before-edit: each agent's own reads gate its write_file/search_replace,
and a file changed since that read (by another agent, a command, the user) is
refused with an error that says to read it again."""
from __future__ import annotations

import unittest
from unittest.mock import patch

from tests.test_tool_audit_fixes import FakeSFTP, sftp_patch, ws

L = ws.file_ledger


def edit(old, new, path="a.py"):
    return {"path": path, "old_string": old, "new_string": new}


class SFTPLedgerTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        L.clear()
        self.sftp = FakeSFTP({"/repo/a.py": b"one\ntwo\n"})
        for p in (patch.object(ws, "_is_agent", return_value=False), sftp_patch(self.sftp)):
            p.start()
            self.addCleanup(p.stop)

    async def read(self, reader, path="a.py", **kw):
        return await ws._read(1, 2, "h", {"path": path, **kw}, "/repo", run_id=reader)

    async def test_edit_without_read_is_refused_with_instructions(self):
        with self.assertRaises(ws.WorkspaceError) as ctx:
            await ws._replace(1, 2, "h", edit("one", "1"), "/repo", run_id="A")
        self.assertEqual(ctx.exception.detail,
                         "/repo/a.py has not been read. Call read_file on it first, then retry the edit.")
        self.assertEqual(self.sftp.files["/repo/a.py"], b"one\ntwo\n")

    async def test_another_agents_read_does_not_count(self):
        await self.read("A")
        with self.assertRaisesRegex(ws.WorkspaceError, "has not been read"):
            await ws._replace(1, 2, "h", edit("one", "1"), "/repo", run_id="B")
        await ws._replace(1, 2, "h", edit("one", "1"), "/repo", run_id="A")

    async def test_other_agents_edit_after_my_read_is_refused(self):
        await self.read("A")
        await self.read("B")
        await ws._replace(1, 2, "h", edit("one", "1"), "/repo", run_id="A")
        with self.assertRaises(ws.WorkspaceError) as ctx:
            await ws._replace(1, 2, "h", edit("two", "2"), "/repo", run_id="B")
        self.assertIn("changed since you last read it (another agent edited it)", ctx.exception.detail)
        self.assertIn("Call read_file on it again", ctx.exception.detail)
        self.assertEqual(self.sftp.files["/repo/a.py"], b"1\ntwo\n")
        # Reading again clears it.
        await self.read("B")
        await ws._replace(1, 2, "h", edit("two", "2"), "/repo", run_id="B")
        self.assertEqual(self.sftp.files["/repo/a.py"], b"1\n2\n")

    async def test_own_edits_chain_without_rereading(self):
        await self.read("A")
        await ws._replace(1, 2, "h", edit("one", "1"), "/repo", run_id="A")
        await ws._replace(1, 2, "h", edit("two", "2"), "/repo", run_id="A")
        await ws._write(1, 2, "h", {"path": "a.py", "content": "z\n"}, "/repo", run_id="A")
        await ws._replace(1, 2, "h", edit("z", "y"), "/repo", run_id="A")
        self.assertEqual(self.sftp.files["/repo/a.py"], b"y\n")

    async def test_external_change_same_size_and_mtime_is_caught_by_content(self):
        # A whole-file read pins the content, so even a change the stat cannot
        # see (same size, same second) is refused.
        await self.read("A")
        self.sftp.files["/repo/a.py"] = b"ONE\ntwo\n"
        with self.assertRaisesRegex(ws.WorkspaceError, r"changed since you last read it\. Call read_file"):
            await ws._replace(1, 2, "h", edit("two", "2"), "/repo", run_id="A")
        await self.read("A")
        self.sftp.files["/repo/a.py"] = b"ONE\nTWO\n"
        with self.assertRaisesRegex(ws.WorkspaceError, "changed since you last read it"):
            await ws._write(1, 2, "h", {"path": "a.py", "content": "x\n"}, "/repo", run_id="A")
        self.assertEqual(self.sftp.files["/repo/a.py"], b"ONE\nTWO\n")

    async def test_other_agents_edit_refuses_a_whole_file_write(self):
        await self.read("A")
        await self.read("B")
        await ws._replace(1, 2, "h", edit("one", "1"), "/repo", run_id="A")
        with self.assertRaisesRegex(ws.WorkspaceError, "another agent edited it"):
            await ws._write(1, 2, "h", {"path": "a.py", "content": "x\n"}, "/repo", run_id="B")

    async def test_changed_refusal_requires_a_real_reread(self):
        await self.read("A", limit=1)  # stat-only record
        self.sftp.files["/repo/a.py"] = b"one\ntwo\nthree\n"
        with self.assertRaisesRegex(ws.WorkspaceError, "changed since you last read it"):
            await ws._replace(1, 2, "h", edit("one", "1"), "/repo", run_id="A")
        # A blind retry is refused as unread, not let through.
        with self.assertRaisesRegex(ws.WorkspaceError, "has not been read"):
            await ws._replace(1, 2, "h", edit("one", "1"), "/repo", run_id="A")

    async def test_partial_read_falls_back_to_stat(self):
        await self.read("A", limit=1)
        self.assertIsNone(L.lookup("A", 2, "/repo/a.py").digest)
        self.sftp.files["/repo/a.py"] = b"one\ntwo\nthree\n"
        with self.assertRaisesRegex(ws.WorkspaceError, "changed since you last read it"):
            await ws._replace(1, 2, "h", edit("one", "1"), "/repo", run_id="A")

    async def test_touch_without_content_change_is_allowed(self):
        await self.read("A")
        self.sftp.mtimes["/repo/a.py"] = 50
        await ws._replace(1, 2, "h", edit("one", "1"), "/repo", run_id="A")

    async def test_write_landing_during_a_read_forces_a_reread(self):
        # The generation is taken before the read's I/O: a write that lands
        # while the read is in flight leaves the read stale.
        gen = L.generation(2, "/repo/a.py")
        L.note_write("B", 2, "/repo/a.py")
        L.note_read("A", 2, "/repo/a.py", gen=gen)
        with self.assertRaisesRegex(ws.WorkspaceError, "another agent edited it"):
            await ws._replace(1, 2, "h", edit("one", "1"), "/repo", run_id="A")

    async def test_unknown_outcome_invalidates_every_reader(self):
        await self.read("A")
        await self.read("B")

        async def boom(*_a, **_k):
            raise TimeoutError

        with patch.object(ws, "_sftp_publish", boom):
            with self.assertRaises(TimeoutError):
                await ws._replace(1, 2, "h", edit("one", "1"), "/repo", run_id="A")
        for reader in ("A", "B"):
            with self.assertRaises(ws.WorkspaceError):
                await ws._replace(1, 2, "h", edit("one", "1"), "/repo", run_id=reader)

    async def test_new_file_needs_no_read(self):
        await ws._write(1, 2, "h", {"path": "new.py", "content": "x\n"}, "/repo", run_id="A")
        await ws._replace(1, 2, "h", edit("x", "y", "new.py"), "/repo", run_id="A")
        self.assertEqual(self.sftp.files["/repo/new.py"], b"y\n")

    async def test_missing_file_edit_says_to_create_it(self):
        with self.assertRaisesRegex(ws.WorkspaceError, "no such file or directory.*write_file to create it"):
            await ws._replace(1, 2, "h", edit("a", "b", "nope.py"), "/repo", run_id="A")


class ConnectorLedgerTest(unittest.IsolatedAsyncioTestCase):
    """Connectors report no stat: Fuse writes by generation, other changes by
    a whole-file content digest re-read just before the edit."""

    def setUp(self):
        L.clear()
        self.text = "one\ntwo\n"
        self.calls = []
        for p in (patch.object(ws, "_is_agent", return_value=True), patch.object(ws, "_agent_call", self.call)):
            p.start()
            self.addCleanup(p.stop)

    async def call(self, _u, _h, op, args, seconds=30):
        self.calls.append(op)
        if op == "read":
            lines = self.text.splitlines(keepends=True)
            start = args["offset"] - 1
            return {"ok": True, "out": "".join(lines[start:start + args["limit"]])}
        if op == "replace":
            if args["old"] not in self.text:
                return {"ok": False, "error": "old_string not found"}
            self.text = self.text.replace(args["old"], args["new"], 1)
            return {"ok": True, "out": "1 replacement"}
        if op == "write":
            self.text = args["content"]
            return {"ok": True, "out": "wrote"}
        if op == "exec":
            return {"ok": True, "exit": 0, "out": f"{len(self.text)}\nw\ndw\n"}
        raise AssertionError(op)

    async def test_unread_edit_is_refused(self):
        with self.assertRaisesRegex(ws.WorkspaceError, "has not been read"):
            await ws._replace(1, 2, "h", edit("one", "1", "/w/a.py"), "/w", run_id="A")
        self.assertNotIn("replace", self.calls)

    async def test_external_change_is_refused_and_reread_fixes_it(self):
        await ws._read(1, 2, "h", {"path": "/w/a.py"}, "/w", run_id="A")
        self.text = "ONE\ntwo\n"
        with self.assertRaisesRegex(ws.WorkspaceError, "changed since you last read it"):
            await ws._replace(1, 2, "h", edit("two", "2", "/w/a.py"), "/w", run_id="A")
        self.assertNotIn("replace", self.calls)
        await ws._read(1, 2, "h", {"path": "/w/a.py"}, "/w", run_id="A")
        await ws._replace(1, 2, "h", edit("two", "2", "/w/a.py"), "/w", run_id="A")
        self.assertEqual(self.text, "ONE\n2\n")

    async def test_other_agent_edit_then_own_chain(self):
        await ws._read(1, 2, "h", {"path": "/w/a.py"}, "/w", run_id="A")
        await ws._read(1, 2, "h", {"path": "/w/a.py"}, "/w", run_id="B")
        await ws._replace(1, 2, "h", edit("one", "1", "/w/a.py"), "/w", run_id="A")
        await ws._write(1, 2, "h", {"path": "/w/a.py", "content": "1\nTWO\n"}, "/w", run_id="A")
        await ws._replace(1, 2, "h", edit("TWO", "2", "/w/a.py"), "/w", run_id="A")
        with self.assertRaisesRegex(ws.WorkspaceError, "another agent edited it"):
            await ws._replace(1, 2, "h", edit("two", "2", "/w/a.py"), "/w", run_id="B")

    async def test_connector_errors_are_actionable(self):
        await ws._read(1, 2, "h", {"path": "/w/a.py"}, "/w", run_id="A")

        async def not_unique(_u, _h, op, args, seconds=30):
            if op == "replace":
                return {"ok": False, "error": "old_string is not unique (2 occurrences)"}
            return await self.call(_u, _h, op, args, seconds)

        with patch.object(ws, "_agent_call", not_unique):
            with self.assertRaisesRegex(ws.WorkspaceError, r"not unique \(2 occurrences\)\. Include more surrounding context lines, or pass replace_all=true"):
                await ws._replace(1, 2, "h", edit("o", "0", "/w/a.py"), "/w", run_id="A")


class IdentityTest(unittest.TestCase):
    def test_parse_errors_say_what_to_do(self):
        with self.assertRaisesRegex(ws.WorkspaceError, "old_string is empty.*write_file to create a file"):
            ws._apply_replace("abc", "", "x", False)
        with self.assertRaisesRegex(ws.WorkspaceError, "outside the project root /repo; use a path inside it"):
            ws._confine("/etc/passwd", "/repo")


if __name__ == "__main__":
    unittest.main()
