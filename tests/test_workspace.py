"""Workspace host resolution, confinement, denylist (no live SSH)."""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
_TMP = tempfile.mkdtemp(prefix="fuse-ws-")
os.environ.setdefault("DATABASE_PATH", str(Path(_TMP) / "ws.db"))
os.environ.setdefault("APP_SECRET", "x" * 32)

from app import workspace

import pytest

# Uses the database: a disposable MariaDB schema shared by this module.
pytestmark = pytest.mark.usefixtures("isolated_db")


def _patch_realpaths(case, resolve=None):
    """Instruction loading verifies where the file really points; by default
    every path resolves to itself (no symlinks)."""
    patcher = patch.object(workspace, "_realpaths", AsyncMock(side_effect=resolve or (lambda _u, _h, paths: list(paths))))
    patcher.start()
    case.addCleanup(patcher.stop)


class AgentsFileTest(unittest.TestCase):
    def setUp(self):
        workspace._agents_cache.clear()
        workspace._agents_last_good.clear()
        self.addCleanup(workspace._agents_cache.clear)
        self.addCleanup(workspace._agents_last_good.clear)
        _patch_realpaths(self)

    def test_prefers_agents_md_over_claude(self):
        names = {"README.md", "CLAUDE.md", "AGENTS.md", "src"}
        self.assertEqual(workspace.pick_agents_name(names), "AGENTS.md")
        self.assertEqual(workspace.pick_agents_name({"agent.md"}), "agent.md")
        self.assertEqual(workspace.pick_agents_name({"Agents.md"}), "Agents.md")
        self.assertEqual(workspace.pick_agents_name({"README.md"}), "")

    def test_listing_names_strip_host_prefix(self):
        listing = "From host prod:\nAGENTS.md\nREADME.md\nsrc/\n"
        names = workspace.listing_names(listing)
        self.assertIn("AGENTS.md", names)
        self.assertIn("README.md", names)
        self.assertIn("src", names)
        self.assertNotIn("From host prod:", names)

    def test_unlabel_host(self):
        body = workspace.unlabel_host("prod", "From host prod:\n# rules\n")
        self.assertEqual(body, "# rules\n")

    def test_cache_roundtrip_and_invalidate(self):
        async def fake_list(*_args, **_kwargs):
            return "From host prod:\nAGENTS.md\n", {"status": "done", "preview": "AGENTS.md"}

        async def fake_read(*_args, **_kwargs):
            return "From host prod:\n# api\nuse make test\n", {"status": "done"}

        row = {"id": 4, "alias": "prod", "default_cwd": "/srv/api"}
        with patch.object(workspace, "resolve_host_row", return_value=row), patch.object(
            workspace, "_list", fake_list
        ), patch.object(workspace, "_read", fake_read):
            import asyncio

            name, body = asyncio.run(workspace.load_project_agents(1, 4, "/srv/api"))
            self.assertEqual(name, "AGENTS.md")
            self.assertIn("make test", body)
            self.assertNotIn("From host", body)
            name2, body2 = asyncio.run(workspace.load_project_agents(1, 4, "/srv/api"))
            self.assertEqual((name2, body2), (name, body))
        workspace.invalidate_agents_cache(1, 4, "/srv/api/AGENTS.md")
        self.assertFalse(any(key[0] == 1 and key[1] == 4 for key in workspace._agents_cache))


class AgentsLoadingTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        workspace._agents_cache.clear()
        workspace._agents_last_good.clear()
        self.addCleanup(workspace._agents_cache.clear)
        self.addCleanup(workspace._agents_last_good.clear)
        self.row = {"id": 4, "alias": "prod", "default_cwd": "/srv/api"}
        _patch_realpaths(self)

    async def test_symlinked_instruction_file_out_of_project_is_not_loaded(self):
        def resolve(_u, _h, paths):
            return ["/home/me/.ssh/id_ed25519" if p.endswith("AGENTS.md") else p for p in paths]

        workspace._realpaths.side_effect = resolve
        reading = AsyncMock(side_effect=AssertionError("must not read a symlink out of the project"))
        with patch.object(workspace, "resolve_host_row", return_value=self.row), patch.object(
            workspace, "_list", AsyncMock(return_value=("AGENTS.md", {"status": "done"}))
        ), patch.object(workspace, "_read", reading):
            report = {}
            self.assertEqual(await workspace.load_project_agents(1, 4, "/srv/api", report), ("", ""))
        self.assertEqual(report, {"reason": "missing"})

    async def test_full_budget_and_paging_on_both_transports(self):
        for agent in (False, True):
            for source in ("x" * 9000, "rule\n" * 500, "x" * 12001, "rule\n" * 3000, "x" * 32000, "x" * 32001,
                           "rule\n" * 7000):
                with self.subTest(agent=agent, length=len(source)):
                    workspace._agents_cache.clear()
                    async def agent_read(_uid, _hid, _op, args, _timeout):
                        lines = source.splitlines(keepends=True)
                        start = args["offset"] - 1
                        return {"ok": True, "out": "".join(lines[start:start + min(args["limit"], 2000)])}

                    handle = MagicMock()
                    handle.__aenter__.return_value = handle
                    def reset_handle():
                        handle.read = AsyncMock(side_effect=[source.encode(), b""])
                        return handle
                    handle.__aenter__.side_effect = reset_handle
                    sftp = MagicMock()
                    sftp.__aenter__.return_value = sftp
                    sftp.open.return_value = handle
                    with patch.object(workspace, "resolve_host_row", return_value=self.row), patch.object(
                        workspace, "_list", AsyncMock(return_value=("AGENTS.md", {"status": "done"}))
                    ), patch.object(workspace, "_is_agent", return_value=agent), patch.object(
                        workspace, "_agent_call", side_effect=agent_read
                    ) as remote, patch.object(workspace, "_sftp", AsyncMock(return_value=sftp)):
                        report = {}
                        name, body = await workspace.load_project_agents(1, 4, "/srv/api", report)
                        self.assertEqual(name, "AGENTS.md")
                        self.assertEqual(report, {"reason": "loaded"})
                        if len(source.rstrip("\n")) > workspace.AGENTS_MAX:
                            self.assertLessEqual(len(body), workspace.AGENTS_MAX)
                            kept, marker = body.rsplit("\n", 1)
                            self.assertTrue(source.startswith(kept))
                            # The marker names the first line the model has not seen.
                            offset = 1 if "\n" not in kept else len(kept.splitlines()) + 1
                            self.assertEqual(
                                marker,
                                f"[Project instructions truncated: read_file AGENTS.md offset={offset} for the rest]",
                            )
                        else:
                            self.assertEqual(body, source.strip())
                        cached = {}
                        self.assertEqual(await workspace.load_project_agents(1, 4, "/srv/api", cached), (name, body))
                        self.assertEqual(cached, report)
                        if agent and source == "rule\n" * 3000:
                            self.assertEqual([call.args[3]["offset"] for call in remote.call_args_list], [1, 2001])

    async def test_cached_failure_reasons_at_each_stage(self):
        for stage in ("resolve_host_row", "_list", "_read"):
            for error, reason in ((workspace.VaultLocked(), "locked"), (workspace.WorkspaceError("offline"), "host")):
                with self.subTest(stage=stage, reason=reason):
                    workspace._agents_cache.clear()
                    workspace._agents_last_good.clear()
                    with patch.object(workspace, "resolve_host_row", return_value=self.row) as resolve, patch.object(
                        workspace, "_list", AsyncMock(return_value=("AGENTS.md", {"status": "done"}))
                    ) as listing, patch.object(workspace, "_read", AsyncMock()) as reading:
                        failing = {"resolve_host_row": resolve, "_list": listing, "_read": reading}[stage]
                        failing.side_effect = error
                        for _ in range(2):
                            report = {}
                            self.assertEqual(await workspace.load_project_agents(1, 4, "/srv/api", report), ("", ""))
                            self.assertEqual(report, {"reason": reason})
                        self.assertEqual(failing.call_count, 1)

    async def test_transient_failure_serves_last_good_body(self):
        def ok_list(names="AGENTS.md"):
            async def list_(*_a, **_k):
                return f"From host prod:\n{names}\n", {"status": "done", "preview": names}

            return list_

        def ok_read(body="RULES\n"):
            async def read(*_a, **_k):
                return f"From host prod:\n{body}", {"status": "done"}

            return read

        with patch.object(workspace, "resolve_host_row", return_value=self.row), patch.object(
            workspace, "_list", AsyncMock(side_effect=ok_list())
        ), patch.object(workspace, "_read", AsyncMock(side_effect=ok_read())):
            self.assertEqual(
                await workspace.load_project_agents(1, 4, "/srv/api"), ("AGENTS.md", "RULES")
            )
        self.assertEqual(workspace.last_project_agents(1, 4, "/srv/api"), ("AGENTS.md", "RULES"))

        # Resolve, list and read failures each serve the sticky body instead of
        # blanking the system-prompt block for a turn.
        for stage in ("resolve_host_row", "_list", "_read"):
            with self.subTest(stage=stage):
                workspace._agents_cache.clear()
                with patch.object(workspace, "resolve_host_row", return_value=self.row) as resolve, patch.object(
                    workspace, "_list", AsyncMock(side_effect=ok_list())
                ) as listing, patch.object(
                    workspace, "_read", AsyncMock(side_effect=ok_read())
                ) as reading:
                    {"resolve_host_row": resolve, "_list": listing, "_read": reading}[stage].side_effect = (
                        workspace.WorkspaceError("offline")
                    )
                    report = {}
                    self.assertEqual(
                        await workspace.load_project_agents(1, 4, "/srv/api", report),
                        ("AGENTS.md", "RULES"),
                    )
                    self.assertEqual(report, {"reason": "host"})

        # A listing rec that reports an error is a transient failure too, not
        # a confirmed deletion.
        workspace._agents_cache.clear()
        with patch.object(workspace, "resolve_host_row", return_value=self.row), patch.object(
            workspace, "_list",
            AsyncMock(return_value=("From host prod:\n", {"status": "error", "error": "host down"})),
        ):
            report = {}
            self.assertEqual(
                await workspace.load_project_agents(1, 4, "/srv/api", report), ("AGENTS.md", "RULES")
            )
            self.assertEqual(report, {"reason": "host"})
        self.assertEqual(workspace.last_project_agents(1, 4, "/srv/api"), ("AGENTS.md", "RULES"))

        # A runtime-side timeout (wait_for) falls back to the same body.
        workspace._agents_cache.clear()

        async def slow_list(*_a, **_k):
            await asyncio.sleep(0.5)
            return "From host prod:\nAGENTS.md\n", {"status": "done", "preview": "AGENTS.md"}

        with patch.object(workspace, "resolve_host_row", return_value=self.row), patch.object(
            workspace, "_list", AsyncMock(side_effect=slow_list)
        ):
            with self.assertRaises(asyncio.TimeoutError):
                await asyncio.wait_for(workspace.load_project_agents(1, 4, "/srv/api"), timeout=0.05)
        self.assertEqual(workspace.last_project_agents(1, 4, "/srv/api"), ("AGENTS.md", "RULES"))

        # A successful changed read replaces the sticky body.
        workspace._agents_cache.clear()
        with patch.object(workspace, "resolve_host_row", return_value=self.row), patch.object(
            workspace, "_list", AsyncMock(side_effect=ok_list())
        ), patch.object(workspace, "_read", AsyncMock(side_effect=ok_read("RULES-V2\n"))):
            self.assertEqual(
                await workspace.load_project_agents(1, 4, "/srv/api"), ("AGENTS.md", "RULES-V2")
            )
        self.assertEqual(workspace.last_project_agents(1, 4, "/srv/api"), ("AGENTS.md", "RULES-V2"))

        # A listing that succeeds with no instruction file is a confirmed
        # absence: the sticky body is dropped, not resurrected.
        workspace._agents_cache.clear()
        with patch.object(workspace, "resolve_host_row", return_value=self.row), patch.object(
            workspace, "_list", AsyncMock(side_effect=ok_list("README.md"))
        ):
            report = {}
            self.assertEqual(await workspace.load_project_agents(1, 4, "/srv/api", report), ("", ""))
            self.assertEqual(report, {"reason": "missing"})
        self.assertEqual(workspace.last_project_agents(1, 4, "/srv/api"), ("", ""))

    async def test_write_invalidates_cache_but_keeps_fallback(self):
        async def ok_list(*_a, **_k):
            return "From host prod:\nAGENTS.md\n", {"status": "done", "preview": "AGENTS.md"}

        async def ok_read(*_a, **_k):
            return "From host prod:\nRULES\n", {"status": "done"}

        with patch.object(workspace, "resolve_host_row", return_value=self.row), patch.object(
            workspace, "_list", AsyncMock(side_effect=ok_list)
        ), patch.object(workspace, "_read", AsyncMock(side_effect=ok_read)):
            self.assertEqual(
                await workspace.load_project_agents(1, 4, "/srv/api"), ("AGENTS.md", "RULES")
            )
        workspace.invalidate_agents_cache(1, 4, "/srv/api/AGENTS.md")
        self.assertFalse(any(key[0] == 1 and key[1] == 4 for key in workspace._agents_cache))
        # The pre-write body stays as the transient fallback: a read failure
        # right after the write must not blank the instructions block.
        self.assertEqual(workspace.last_project_agents(1, 4, "/srv/api"), ("AGENTS.md", "RULES"))
        with patch.object(workspace, "resolve_host_row", return_value=self.row), patch.object(
            workspace, "_list", AsyncMock(side_effect=workspace.WorkspaceError("offline"))
        ):
            self.assertEqual(
                await workspace.load_project_agents(1, 4, "/srv/api"), ("AGENTS.md", "RULES")
            )

    async def test_last_good_is_tenant_keyed_and_bounded(self):
        for i in range(300):
            workspace._agents_last_good[(i, 4, f"/p{i}")] = ("AGENTS.md", f"body{i}")

        async def ok_list(*_a, **_k):
            return "From host prod:\nAGENTS.md\n", {"status": "done", "preview": "AGENTS.md"}

        async def ok_read(*_a, **_k):
            return "From host prod:\nRULES\n", {"status": "done"}

        with patch.object(workspace, "resolve_host_row", return_value=self.row), patch.object(
            workspace, "_list", AsyncMock(side_effect=ok_list)
        ), patch.object(workspace, "_read", AsyncMock(side_effect=ok_read)):
            await workspace.load_project_agents(2, 4, "/srv/api")
        self.assertLessEqual(len(workspace._agents_last_good), 2)
        self.assertEqual(workspace.last_project_agents(2, 4, "/srv/api"), ("AGENTS.md", "RULES"))
        # Another user's or cwd's entry never answers for this project.
        self.assertEqual(workspace.last_project_agents(1, 4, "/srv/api"), ("", ""))
        self.assertEqual(workspace.last_project_agents(2, 4, "/srv/other"), ("", ""))

    async def test_generic_read_character_limit(self):
        with patch.object(workspace, "_is_agent", return_value=True), patch.object(
            workspace, "_agent_call", AsyncMock(return_value={"ok": True, "out": "x" * 20000})
        ):
            text, _ = await workspace._read(1, 4, "prod", {"path": "AGENTS.md"}, "/srv/api")
        body = workspace.unlabel_host("prod", text)
        self.assertTrue(body.startswith("1: xxx"))
        self.assertIn(f"... (line truncated to {workspace._READ_MAX_LINE} chars)", body)
        self.assertIn("(End of file — 1 lines.)", body)
        self.assertLess(len(body), workspace._READ_MAX_LINE + 200)

    async def test_windows_instruction_paths(self):
        row = {"id": 4, "alias": "prod", "agent_os": "windows", "default_cwd": r"C:\repo"}
        async def agent_call(_uid, _hid, op, args, _timeout):
            self.assertEqual(args["path"], r"C:\repo" if op == "list" else r"C:\repo\AGENTS.md")
            return {"ok": True, "out": "AGENTS.md" if op == "list" else "rules"}

        with patch.object(workspace, "resolve_host_row", return_value=row), patch.object(
            workspace, "_is_agent", return_value=True
        ), patch.object(workspace, "_agent_call", side_effect=agent_call):
            self.assertEqual(await workspace.load_project_agents(1, 4, r"C:\repo"), ("AGENTS.md", "rules"))

    async def test_missing_reason_survives_cache(self):
        with patch.object(workspace, "resolve_host_row", return_value=self.row), patch.object(
            workspace, "_list", AsyncMock(return_value=("README.md", {"status": "done"}))
        ) as listing:
            for _ in range(2):
                report = {}
                self.assertEqual(await workspace.load_project_agents(1, 4, "/srv/api", report), ("", ""))
                self.assertEqual(report, {"reason": "missing"})
            self.assertEqual(listing.call_count, 1)


class ExecutePromotionTest(unittest.IsolatedAsyncioTestCase):
    async def test_file_trace_does_not_invent_directory(self):
        from app import chats

        for name, handler in (("read_file", "_read"), ("write_file", "_write"), ("search_replace", "_replace")):
            for root in ("", "/srv/api"):
                with self.subTest(tool=name, root=root):
                    row = {"id": 4, "alias": "prod", "default_cwd": root}
                    with patch.object(workspace, "resolve_host_row", return_value=row), patch.object(
                        workspace.hosts, "settings_for", return_value={}
                    ), patch.object(workspace.hosts, "effective_permission", return_value="project"), patch.object(
                        workspace, handler, AsyncMock(return_value=("ok", {"status": "done", "path": "/srv/api/src/main.py"}))
                    ):
                        _, rec = await workspace.execute(name, json.dumps({"host": "prod"}), user_id=1)
                    self.assertEqual(rec["cwd"], root)
                    self.assertEqual(rec["host_id"], 4)
                    self.assertEqual(chats._session_root(rec["cwd"], rec["path"], tool=name), root or "/srv/api/src")

    def test_file_parent_edge_cases(self):
        from app import chats

        for path, expected in (("README.md", "."), ("/README.md", "/"), (r"C:\repo\main.py", "C:/repo")):
            self.assertEqual(chats._session_root(path=path, tool="read_file"), expected)


class ConfineTest(unittest.TestCase):
    def test_tilde_stays_home(self):
        self.assertEqual(workspace._confine("~", ""), "~")
        self.assertEqual(workspace._confine("~/src", "/srv"), "~/src")
        with self.assertRaises(workspace.WorkspaceError):
            workspace._confine("~/../etc", "")

    def test_unbound_relative_and_absolute(self):
        self.assertEqual(workspace._confine("uptime", ""), "uptime")
        self.assertEqual(workspace._confine("/proc/loadavg", ""), "/proc/loadavg")
        with self.assertRaises(workspace.WorkspaceError):
            workspace._confine("../etc/passwd", "")

    def test_project_root(self):
        self.assertEqual(workspace._confine("app.py", "/srv/api"), "/srv/api/app.py")
        with self.assertRaises(workspace.WorkspaceError):
            workspace._confine("/etc/passwd", "/srv/api")
        with self.assertRaises(workspace.WorkspaceError):
            workspace._confine("../../etc/passwd", "/srv/api")

    def test_windows_paths(self):
        self.assertEqual(workspace._confine("src", r"C:\proj", windows=True), r"C:\proj\src")
        self.assertEqual(workspace._confine(r"C:\proj\src\a.py", r"C:\proj", windows=True), r"C:\proj\src\a.py")
        self.assertEqual(workspace._confine(r"~\Documents", "", windows=True), r"~\Documents")
        with self.assertRaises(workspace.WorkspaceError):
            workspace._confine(r"C:\Windows", r"C:\proj", windows=True)
        with self.assertRaises(workspace.WorkspaceError):
            workspace._confine(r"..\secret", r"C:\proj", windows=True)
        self.assertEqual(workspace._confine_glob(r"src\*.c", r"C:\proj", windows=True), r"src\*.c")

    def test_glob_pattern_errors_name_the_cause(self):
        with self.assertRaises(workspace.WorkspaceError) as empty:
            workspace._confine_glob("", "")
        self.assertIn("empty", empty.exception.detail)
        with self.assertRaises(workspace.WorkspaceError) as long:
            workspace._confine_glob("a" * 201, "")
        self.assertIn("200", long.exception.detail)
        with self.assertRaises(workspace.WorkspaceError) as bad:
            workspace._confine_glob("src/**/*.py | wc", "")
        self.assertIn("unsupported characters", bad.exception.detail)


class WinDenylistTest(unittest.TestCase):
    def test_blocks_windows_destructive(self):
        self.assertTrue(workspace.denied("Stop-Computer", windows=True))
        self.assertTrue(workspace.denied("Restart-Computer -Force", windows=True))
        self.assertTrue(workspace.denied("shutdown /s /t 0", windows=True))
        self.assertFalse(workspace.denied("Get-ChildItem", windows=True))


class HostRootTest(unittest.TestCase):
    def test_named_host_ignores_other_project_cwd(self):
        other = {"id": 2, "alias": "laptop", "default_cwd": "/home/me"}
        self.assertEqual(workspace.host_root(other, bound_host_id=1, bound_cwd="/srv/api"), "/home/me")
        bound = {"id": 1, "alias": "prod", "default_cwd": "/opt"}
        self.assertEqual(workspace.host_root(bound, bound_host_id=1, bound_cwd="/srv/api"), "/srv/api")
        unbound = {"id": 3, "alias": "box", "default_cwd": ""}
        self.assertEqual(workspace.host_root(unbound, bound_host_id=None, bound_cwd="/srv/api"), "")

    def test_shell_cwd_defaults_home_when_unbound(self):
        self.assertEqual(workspace._shell_cwd({}, ""), "~")
        self.assertEqual(workspace._shell_cwd({"cwd": "~/bin"}, "/srv"), "~/bin")
        self.assertEqual(workspace._shell_cwd({"cwd": "$HOME"}, ""), "~")
        self.assertEqual(workspace._shell_cwd({"cwd": "$HOME/src"}, "/srv"), "~/src")
        self.assertEqual(workspace._shell_cwd({}, "/srv/api"), "/srv/api")

    def test_agent_cwd_never_sends_dollar_home(self):
        self.assertEqual(workspace._agent_cwd("$HOME"), "~")
        self.assertEqual(workspace._agent_cwd("~"), "~")
        self.assertEqual(workspace._agent_cwd(""), "~")
        self.assertEqual(workspace._agent_cwd("$HOME/src"), "~/src")
        self.assertEqual(workspace._agent_cwd("/srv/api"), "/srv/api")
        self.assertEqual(workspace._shell_prefix("$HOME"), 'cd "$HOME"')
        self.assertEqual(workspace._shell_prefix("~"), 'cd "$HOME"')

    def test_home_paths_resolve_without_home_env(self):
        # Connectors before ensure_home_env run commands with HOME unset: the
        # existence probe saw "$HOME"/x as /x and called an existing file
        # missing ("does not exist. Use write_file to create it.").
        import pwd
        import subprocess

        home = pwd.getpwuid(os.getuid()).pw_dir
        with tempfile.TemporaryDirectory(dir=home) as tmp:
            rel = os.path.relpath(tmp, home)
            Path(tmp, "index x.html").write_text("hi")
            env = {k: v for k, v in os.environ.items() if k != "HOME"}

            def run(cmd: str) -> list[str]:
                out = subprocess.run(["/bin/bash", "--noprofile", "--norc", "-c", cmd],
                                     env=env, capture_output=True, text=True, timeout=10).stdout
                return out.split()

            probe = workspace._probe_cmd(f"~/{rel}/index x.html", windows=False)
            self.assertEqual(run(probe)[0], "2")
            probe = workspace._probe_cmd(f"$HOME/{rel}/missing.html", windows=False)
            self.assertEqual(run(probe)[0], "missing")
            self.assertEqual(run(f"p={workspace._quote_host_path('~')}; echo \"$p\""), [home])
            self.assertEqual(workspace._quote_host_path("~/a b"), "~/'a b'")
            self.assertEqual(workspace._quote_host_path("/srv/a b"), "'/srv/a b'")


class ResolveTest(unittest.TestCase):
    def test_reserved_and_unbound(self):
        with self.assertRaises(workspace.WorkspaceError):
            workspace.resolve_host_row(1, "localhost", None)
        with self.assertRaises(workspace.WorkspaceError) as ctx:
            workspace.resolve_host_row(1, None, None)
        self.assertIn("host is required", str(ctx.exception))

    def test_alias_lookup(self):
        row = {"id": 9, "alias": "prod", "default_cwd": "/srv"}
        with patch("app.hosts.host_by_alias", return_value=row):
            self.assertEqual(workspace.resolve_host_row(1, "prod", None)["id"], 9)
        with patch("app.hosts.host_by_alias", return_value=None):
            with self.assertRaises(workspace.WorkspaceError):
                workspace.resolve_host_row(1, "missing", None)


class BrowseViewTest(unittest.TestCase):
    def test_from_ls_and_normalize(self):
        data = workspace._browse_from_ls("/root/fuse", "/root", "app/\nREADME.md\nstatic/\n")
        self.assertEqual(data["path"], "/root/fuse")
        self.assertEqual(data["parent"], "/root")
        kinds = {item["name"]: item["kind"] for item in data["entries"]}
        self.assertEqual(kinds["app"], "dir")
        self.assertEqual(kinds["static"], "dir")
        self.assertEqual(kinds["README.md"], "file")
        cleaned = workspace._browse_view("/x", "", "/home", [{"name": "../etc", "kind": "dir"}, {"name": "ok", "kind": "dir"}])
        self.assertEqual([item["name"] for item in cleaned["entries"]], ["ok"])


class DenylistTest(unittest.TestCase):
    def test_blocks_destructive(self):
        self.assertTrue(workspace.denied("rm -rf /"))
        self.assertTrue(workspace.denied("sudo reboot"))
        self.assertTrue(workspace.denied("ssh other"))
        self.assertIsNone(workspace.denied("uptime"))
        self.assertIsNone(workspace.denied("free -h && df -h"))


class LabelTest(unittest.TestCase):
    def test_from_host(self):
        self.assertTrue(workspace._label("prod", "load 0.1").startswith("From host prod:"))


class HostFailureMappingTest(unittest.IsolatedAsyncioTestCase):
    async def test_ssh_and_vault_failures_are_reported_not_filed_as_bugs(self):
        import asyncssh

        from app.hosts import HostError

        row = {"id": 4, "alias": "prod", "default_cwd": "/srv"}
        cases = (
            (HostError("vault locked", 401), "vault locked", "vault_locked"),
            (asyncssh.PermissionDenied("auth failed"), "SSH connection failed: auth failed", "upstream_error"),
            (TimeoutError(), "SSH connection failed: TimeoutError", "upstream_error"),
        )
        for exc, text, kind in cases:
            with self.subTest(exc=type(exc).__name__), patch.object(workspace, "resolve_host_row", return_value=row), \
                    patch.object(workspace.hosts, "settings_for", return_value={}), \
                    patch.object(workspace.hosts, "effective_permission", return_value="project"), \
                    patch.object(workspace, "_read", AsyncMock(side_effect=exc)):
                got, rec = await workspace.execute("read_file", json.dumps({"host": "prod", "path": "x"}), user_id=1)
            self.assertEqual(got, text)
            self.assertEqual(rec["kind"], kind)

    def test_non_list_edits_is_a_clear_error(self):
        from app.workspace_ops.previews import _parse_edits

        with self.assertRaises(workspace.WorkspaceError):
            _parse_edits({"old_string": "a", "new_string": "b", "edits": 5})

    async def test_failed_in_place_write_restores_the_original(self):
        files = {"/srv/f": b"original bytes"}

        class Handle:
            def __init__(self, path, mode):
                self.path, self.mode = path, mode

            async def __aenter__(self):
                if "w" in self.mode:
                    files[self.path] = b""  # truncated at open
                return self

            async def __aexit__(self, *exc):
                return False

            async def read(self):
                return files[self.path]

            async def write(self, data):
                if data != b"original bytes":
                    raise OSError(28, "No space left on device")
                files[self.path] = data

        sftp = type("Sftp", (), {"open": lambda self, path, mode: Handle(path, mode)})()
        with self.assertRaises(workspace.WorkspaceError) as caught:
            await workspace._sftp_write_in_place(sftp, "/srv/f", b"new bytes")
        self.assertNotIn("truncated", caught.exception.detail)
        self.assertEqual(files["/srv/f"], b"original bytes")


class ReadOnlyTest(unittest.IsolatedAsyncioTestCase):
    async def test_blocks_writes(self):
        with patch("app.hosts.settings_for", return_value={"permission_mode": "read_only"}):
            text, trace = await workspace.execute(
                "write_file",
                '{"path":"x","content":"y","host":"prod"}',
                user_id=1,
            )
        self.assertEqual(trace["status"], "error")
        self.assertIn("read_only", text)


class _FakeExec:
    def __init__(self, exit_status, stdout="", stderr=""):
        self.stdout = stdout
        self.stderr = stderr
        self.exit_status = exit_status


def _fake_runner_factory(result=None, exc=None, gate=None):
    """Stand-in for _make_ssh_runner: streams output to the sink, returns _ExecResult.

    With gate (an asyncio.Event), the command keeps running until it is set."""

    def make_runner(_uid, _hid, _command, _cwd, _timeout_ms):
        async def runner(sink):
            if gate is not None:
                await gate.wait()
            if exc is not None:
                raise exc
            combined = (result.stdout or "") + (("\n" + result.stderr) if result.stderr else "")
            if combined:
                sink(combined)
            return workspace._ExecResult(result.exit_status, combined)

        return runner

    return make_runner


class ShellExitCodeTest(unittest.IsolatedAsyncioTestCase):
    """A completed command is a successful tool call: the exit status is
    relayed (exit_code trace field + envelope meta), never a failure."""

    def setUp(self):
        self.row = {"id": 4, "alias": "prod", "default_cwd": "/srv"}
        from app import command_jobs

        schema_patch = patch.object(command_jobs, "_schema_ready", False)
        schema_patch.start()
        self.addCleanup(schema_patch.stop)
        command_jobs._live.clear()
        self.addCleanup(command_jobs._live.clear)

    async def _shell(self, args, result):
        with patch.object(workspace, "resolve_host_row", return_value=self.row), patch.object(
            workspace, "_make_ssh_runner", side_effect=_fake_runner_factory(result=result)
        ) as maker:
            text, rec = await workspace.execute("run_terminal_command", json.dumps(args), user_id=1)
        self.assertEqual(maker.call_count, 1)
        return text, rec

    async def test_nonzero_exit_relays_exit_code_without_error_status(self):
        text, rec = await self._shell({"command": "df -h"}, _FakeExec(1, stdout="Filesystem\n/dev/sda1"))
        self.assertEqual(rec["status"], "done")
        self.assertEqual(rec["exit_code"], 1)
        self.assertEqual(rec["error"], "")
        self.assertIn("Filesystem", rec["preview"])
        # The model still sees the command's own exit status.
        self.assertIn("exit_code: 1", text)
        self.assertIn("From host prod:", text)

    async def test_zero_exit_has_no_error_status(self):
        text, rec = await self._shell({"command": "uptime"}, _FakeExec(0, stdout="load 0.1"))
        self.assertEqual(rec["status"], "done")
        self.assertEqual(rec["exit_code"], 0)
        self.assertEqual(rec["error"], "")
        self.assertIn("exit_code: 0", text)
        # A command that completes inside the foreground wait is the shell call
        # itself, never a joined background job.
        self.assertEqual(rec["name"], "run_terminal_command")

    async def test_background_nonzero_exit_joins_as_done_with_exit_code(self):
        # The command keeps running until released, so the launch reports a
        # live job deterministically (an instant fake can finish first).
        gate = asyncio.Event()
        with patch.object(workspace, "resolve_host_row", return_value=self.row), patch.object(
            workspace, "_make_ssh_runner", side_effect=_fake_runner_factory(result=_FakeExec(128, stdout="fatal: not a git repository"), gate=gate)
        ):
            text, rec = await workspace.execute(
                "run_terminal_command", json.dumps({"command": "git log", "background": True}), user_id=1
            )
            self.assertEqual(rec["status"], "running")
            self.assertEqual(rec["name"], "run_terminal_command")
            job_id = rec["child_id"]
            gate.set()
            text, rec = await workspace.execute("get_command_output", json.dumps({"id": job_id}), user_id=1)
        self.assertEqual(rec["status"], "done")
        self.assertEqual(rec["exit_code"], 128)
        self.assertIn("exit_code: 128", text)
        # An explicit join is the one case that renders as get_command_output.
        self.assertEqual(rec["name"], "get_command_output")

    async def test_gateless_denylist_form_still_blocked(self):
        with patch.object(workspace, "resolve_host_row", return_value=self.row), patch.object(
            workspace, "_make_ssh_runner", side_effect=_fake_runner_factory(result=_FakeExec(0, stdout="ok"))
        ) as maker:
            text, rec = await workspace.execute(
                "run_terminal_command", json.dumps({"command": "sudo reboot"}), user_id=1
            )
        self.assertEqual(rec["status"], "error")
        self.assertIn("command blocked by denylist", text)
        self.assertEqual(maker.call_count, 0)

    async def test_accepted_denylist_form_runs(self):
        # The reported bug: the user hits Accept on the card and the command
        # still fails with "command blocked by denylist".
        from app import chats, db, permissions as permissions_mod

        thread = chats.create_thread(1, model="offline")
        tid = chats.resolve_thread_pk(1, thread["id"])
        self.addCleanup(db.execute, "DELETE FROM chat_threads WHERE id=?", (tid,))
        events: list = []
        gate = permissions_mod.Gate(
            user_id=1, thread_id=tid, run_id="run-deny", requester={"role": "main"},
            unattended=False, notify=events.append, cancel=None,
        )

        async def wait_pending():
            for _ in range(250):
                await asyncio.sleep(0.02)
                for event in events:
                    if event.get("event") == "permission" and event.get("status") == "pending":
                        return event
            raise AssertionError("no permission card was raised")

        async def flow():
            task = asyncio.create_task(workspace.execute(
                "run_terminal_command", json.dumps({"command": "sudo id", "host": "prod"}),
                user_id=1, gate=gate,
            ))
            card = await wait_pending()
            self.assertIn("denylist", " ".join(card.get("reasons") or []))
            permissions_mod.resolve(1, tid, card["id"], "allow")
            return await task

        with patch.object(workspace, "resolve_host_row", return_value=self.row), patch.object(
            workspace, "_make_ssh_runner", side_effect=_fake_runner_factory(result=_FakeExec(0, stdout="uid=0(root)"))
        ) as maker:
            text, rec = await flow()
        self.assertEqual(rec["status"], "done")
        self.assertEqual(rec["exit_code"], 0)
        self.assertIn("uid=0(root)", text)
        self.assertEqual(maker.call_count, 1)

    async def test_background_exception_is_still_an_error(self):
        with patch.object(workspace, "resolve_host_row", return_value=self.row), patch.object(
            workspace, "_make_ssh_runner", side_effect=_fake_runner_factory(exc=workspace.WorkspaceError("command timed out"))
        ):
            text, rec = await workspace.execute(
                "run_terminal_command", json.dumps({"command": "sleep 999", "background": True}), user_id=1
            )
            job_id = rec["child_id"]
            text, rec = await workspace.execute("get_command_output", json.dumps({"id": job_id}), user_id=1)
        self.assertEqual(rec["status"], "error")
        self.assertIn("timed out", text)


if __name__ == "__main__":
    unittest.main()
