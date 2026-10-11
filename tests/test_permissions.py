"""Permission gate: scope evaluation, grants, ask lifecycle (no live hosts)."""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
_TMP = tempfile.mkdtemp(prefix="fuse-perm-")
os.environ.setdefault("DATABASE_PATH", str(Path(_TMP) / "perm.db"))
os.environ.setdefault("APP_SECRET", "x" * 32)

from app import db, permissions
from app.chats import _parse_permissions

import pytest

# Uses the database: a disposable MariaDB schema shared by this module.
pytestmark = pytest.mark.usefixtures("isolated_db")


def _gate(thread_id=1, run_id="run-1", requester=None, unattended=False, notify=None, cancel=None):
    return permissions.Gate(
        user_id=1,
        thread_id=thread_id,
        run_id=run_id,
        requester=requester or {"role": "main"},
        unattended=unattended,
        notify=notify or (lambda event: None),
        cancel=cancel,
    )


class PermSchemaTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with db.tx() as conn:
            permissions.init_schema(conn)
            ts = db.now()
            for uid in (1, 2):
                conn.execute(
                    "INSERT OR IGNORE INTO users(id, email, password_hash, created_at) VALUES (?, ?, 'x', ?)",
                    (uid, f"u{uid}@test.local", ts),
                )
            for tid, uid in ((1, 1), (2, 1), (3, 2)):
                conn.execute(
                    "INSERT OR IGNORE INTO chat_threads(id, user_id, created_at, updated_at) VALUES (?, ?, ?, ?)",
                    (tid, uid, ts, ts),
                )


class SecretNameTest(PermSchemaTest):
    def test_secret_names(self):
        yes = [".env", ".env.local", "prod.env", "id_rsa", "id_ed25519", "cert.pem", "server.key", "credentials.json", ".netrc"]
        no = [".env.example", ".env.sample", "id_rsa.pub", "main.py", "readme", "KeYBoard.txt"]
        for name in yes:
            self.assertTrue(permissions.is_secret_name(name), name)
        for name in no:
            self.assertFalse(permissions.is_secret_name(name), name)


class ScopeTest(PermSchemaTest):
    def test_roots(self):
        self.assertEqual(permissions.compute_roots("/srv/app", False), ["/tmp", "/srv/app"])
        self.assertEqual(permissions.compute_roots("", False), ["/tmp", "~"])
        self.assertEqual(permissions.compute_roots("", True), ["%TEMP%", "~"])

    def test_in_roots_posix(self):
        roots = ["/tmp", "/srv/app"]
        self.assertTrue(permissions._in_roots("/srv/app/src/x.py", roots, False))
        ok, resolved = permissions.scope_allows("src/x.py", "/srv/app", roots, False)
        self.assertTrue(ok)
        self.assertEqual(resolved, "/srv/app/src/x.py")
        ok, resolved = permissions.scope_allows("../outside.py", "/srv/app", roots, False)
        self.assertFalse(ok)
        self.assertEqual(resolved, "/srv/outside.py")
        self.assertTrue(permissions._in_roots("/tmp/build", roots, False))
        self.assertFalse(permissions._in_roots("/etc/passwd", roots, False))
        self.assertFalse(permissions._in_roots("~/.ssh/config", roots, False))
        home = ["/tmp", "~"]
        self.assertTrue(permissions._in_roots("~/notes", home, False))
        self.assertTrue(permissions._in_roots("~", home, False))

    def test_in_roots_windows(self):
        roots = ["%TEMP%", "C:\\work\\api"]
        self.assertTrue(permissions._in_roots("C:\\work\\api\\src", roots, True))
        self.assertTrue(permissions._in_roots("%TEMP%\\out", roots, True))
        self.assertFalse(permissions._in_roots("C:\\Windows\\system32", roots, True))

    def test_shell_path_extraction(self):
        paths = permissions.extract_shell_paths("cd /var/www && cat app.log", "/srv/app", False)
        self.assertIn(("/var/www", True), paths)
        paths = permissions.extract_shell_paths("echo hi > /var/log/app.log", "/srv/app", False)
        self.assertIn(("/var/log/app.log", False), paths)
        self.assertIsNone(permissions.extract_shell_paths("echo $HOME/secret", "~", False))
        paths = permissions.extract_shell_paths("make test", "/srv/app", False)
        self.assertEqual(paths, [])

    def test_glued_redirects_extract_targets(self):
        # Redirection operators fused to an operand must still yield the path.
        paths = permissions.extract_shell_paths("echo hi>/var/log/app.log", "/srv/app", False)
        self.assertIn(("/var/log/app.log", False), paths)
        paths = permissions.extract_shell_paths("make 2>/var/log/err.log", "/srv/app", False)
        self.assertIn(("/var/log/err.log", False), paths)
        paths = permissions.extract_shell_paths("cat</etc/passwd", "/srv/app", False)
        self.assertIn(("/etc/passwd", False), paths)

    def test_attached_flag_path_is_opaque(self):
        # tar -C/dir, gcc -I~: the path is fused to the flag, so the command
        # cannot be enumerated and must ask instead of silently allowing it.
        self.assertIsNone(permissions.extract_shell_paths("tar -C/etc -xf a.tar", "/srv/app", False))
        paths = permissions.extract_shell_paths("make -j4 CFLAGS=-O2", "/srv/app", False)
        self.assertEqual(paths, [])

    def test_interpreter_script_commands_are_opaque(self):
        # A script string to an interpreter (or eval/sshpass/expect/busybox)
        # hides its real targets: extraction must give up, not list partials.
        opaque = (
            "bash -c 'cat /etc/shadow'",
            "sh -c \"rm -rf /var/www\"",
            "zsh -c 'echo hi'",
            "dash -c 'true'",
            "ksh -c 'true'",
            "bash -lc 'make'",
            "pwsh -Command 'Remove-Item x'",
            "powershell -command 'gci'",
            "eval 'cat /etc/passwd'",
            "sshpass -p secret ssh host cat /etc/shadow",
            "expect -c 'spawn ssh host'",
            "busybox sh",
            "make && bash -c 'false'",
        )
        for command in opaque:
            self.assertIsNone(permissions.extract_shell_paths(command, "/srv/app", False), command)
        # Running a script file is still enumerable: only -c is opaque.
        paths = permissions.extract_shell_paths("bash scripts/run.sh", "/srv/app", False)
        self.assertIn("/srv/app/scripts/run.sh", [p for p, _ in paths])


class ReviewTest(PermSchemaTest):
    def _cleanup(self):
        with db.tx() as conn:
            conn.execute("DELETE FROM chat_permission_asks")
            conn.execute("DELETE FROM chat_permission_grants")
            conn.execute("UPDATE chat_threads SET bypass_permissions=0, auto_permissions=0")

    def setUp(self):
        self._cleanup()
        self.addCleanup(self._cleanup)

    async def _review(self, gate, tool="read_file", args=None, root="/srv/app"):
        return await permissions.review(
            tool=tool, args=args or {"path": "src/main.py"}, user_id=1,
            host_id=7, host_alias="prod", root=root, windows=False, gate=gate,
        )

    def test_in_root_allows_without_asking(self):
        res = asyncio.run(self._review(_gate()))
        self.assertTrue(res.allowed)
        self.assertIsNone(res.root)

    def test_tmp_allows(self):
        res = asyncio.run(self._review(_gate(), args={"path": "/tmp/build.log"}))
        self.assertTrue(res.allowed)

    def test_unattended_denies_out_of_root(self):
        res = asyncio.run(self._review(_gate(unattended=True), args={"path": "/etc/passwd"}))
        self.assertFalse(res.allowed)
        self.assertIn("Unattended run", res.detail)

    def test_no_thread_denies(self):
        res = asyncio.run(self._review(_gate(thread_id=None), args={"path": "/etc/passwd"}))
        self.assertFalse(res.allowed)

    def test_bypass_allows_and_relaxes(self):
        permissions.set_bypass(1, 1, True)
        res = asyncio.run(self._review(_gate(), args={"path": "/etc/passwd"}))
        self.assertTrue(res.allowed)
        self.assertEqual(res.root, "")

    def test_dotdot_escape_asks_then_relaxes(self):
        res = asyncio.run(self._review(_gate(unattended=True), args={"path": "../../etc/passwd"}))
        self.assertFalse(res.allowed)
        permissions.set_bypass(1, 1, True)
        res = asyncio.run(self._review(_gate(), args={"path": "../../etc/passwd"}))
        self.assertTrue(res.allowed)
        self.assertEqual(res.root, "")

    def test_grant_covers_out_of_root(self):
        permissions.add_grant(1, 1, 7, "dir", "/var/www")
        res = asyncio.run(self._review(_gate(), args={"path": "/var/www/app/config.yml"}))
        self.assertTrue(res.allowed)
        self.assertEqual(res.root, "")

    def test_grant_other_host_does_not_cover(self):
        permissions.add_grant(1, 1, 8, "dir", "/var/www")
        res = asyncio.run(self._review(_gate(unattended=True), args={"path": "/var/www/app/config.yml"}))
        self.assertFalse(res.allowed)

    async def _ask_flow(self, action, args, tool="read_file"):
        events = []
        gate = _gate(notify=events.append)
        if action == "timeout":
            permissions.ASK_TIMEOUT_S = 0.2
        try:
            task = asyncio.create_task(self._review(gate, tool=tool, args=args))
            for _ in range(200):
                await asyncio.sleep(0.02)
                if events and events[0].get("status") == "pending":
                    break
            self.assertTrue(events and events[0].get("status") == "pending")
            ask_id = events[0]["id"]
            if action == "timeout":
                return await task, events
            payload = permissions.resolve(1, 1, ask_id, action)
            return await task, events, payload
        finally:
            permissions.ASK_TIMEOUT_S = 60

    def test_allow_once(self):
        res, events = asyncio.run(self._ask_flow("allow", {"path": "/var/www/index.html"}))[:2]
        self.assertTrue(res.allowed)
        self.assertEqual(res.root, "")
        grants = permissions.grants_for(1, 1)
        self.assertEqual(grants, [])

    def test_allow_session_writes_grant(self):
        res, events, payload = asyncio.run(self._ask_flow("allow_session", {"path": "/var/www/index.html"}))
        self.assertTrue(res.allowed)
        grants = permissions.grants_for(1, 1)
        self.assertEqual(len(grants), 1)
        self.assertEqual(grants[0]["scope"], "session")
        self.assertEqual(grants[0]["kind"], "dir")
        self.assertTrue(grants[0]["value"].endswith("/var/www"))
        self.assertEqual(events[0]["grant_kind"], "dir")
        self.assertTrue(str(events[0]["grant_value"]).endswith("/var/www"))

    def test_allow_host_writes_host_grant(self):
        res, events, payload = asyncio.run(self._ask_flow("allow_host", {"path": "/var/www/index.html"}))
        self.assertTrue(res.allowed)
        grants = permissions.grants_for(1, 1)
        self.assertEqual(grants[0]["scope"], "host")

    def test_deny_message(self):
        res, events, payload = asyncio.run(self._ask_flow("deny", {"path": "/etc/shadow"}))
        self.assertFalse(res.allowed)
        self.assertIn("Permission denied by user", res.detail)
        self.assertIn("/etc/shadow", res.detail)
        self.assertEqual(payload["status"], "denied")
        row = db.fetchone("SELECT status, decision FROM chat_permission_asks WHERE id=?", (payload["id"],))
        self.assertEqual(row["status"], "denied")
        self.assertEqual(row["decision"], "deny")

    def test_timeout_message(self):
        res, events = asyncio.run(self._ask_flow("timeout", {"path": "/etc/shadow"}))[:2]
        self.assertFalse(res.allowed)
        self.assertIn("timed out", res.detail)
        self.assertIn("/etc/shadow", res.detail)

    def test_secret_file_inside_root_is_silent(self):
        # A secret-named file inside an approved dir never asks.
        res = asyncio.run(self._review(_gate(), args={"path": ".env"}))
        self.assertTrue(res.allowed)
        self.assertIsNone(res.root)
        self.assertEqual(permissions.grants_for(1, 1), [])

    def test_symlink_out_of_root_is_judged_by_its_target(self):
        async def realpath(paths):
            real = {"/srv/app/src/main.py": "/home/me/.ssh/id_ed25519", "/srv/app": "/srv/app", "/tmp": "/tmp"}
            return [real.get(p) for p in paths]

        async def run():
            return await permissions.review(
                tool="read_file", args={"path": "src/main.py"}, user_id=1, host_id=7, host_alias="prod",
                root="/srv/app", windows=False, gate=_gate(unattended=True), realpath=realpath,
            )

        res = asyncio.run(run())
        self.assertFalse(res.allowed)

    def test_symlinked_root_still_allows_its_own_files(self):
        async def realpath(paths):
            return [p.replace("/srv/app", "/data/app") for p in paths]

        async def run():
            return await permissions.review(
                tool="read_file", args={"path": "src/main.py"}, user_id=1, host_id=7, host_alias="prod",
                root="/srv/app", windows=False, gate=_gate(unattended=True), realpath=realpath,
            )

        self.assertTrue(asyncio.run(run()).allowed)

    def test_secret_file_outside_root_asks(self):
        res, events, payload = asyncio.run(self._ask_flow("allow_session", {"path": "/home/other/.env"}))
        self.assertTrue(res.allowed)
        grants = permissions.grants_for(1, 1)
        self.assertEqual(grants[0]["kind"], "file")
        self.assertEqual(grants[0]["value"], "/home/other/.env")
        self.assertEqual(events[0]["grant_kind"], "file")
        self.assertEqual(events[0]["grant_value"], "/home/other/.env")

    def test_shell_cwd_and_paths_ask(self):
        # A simple read of an out-of-root path asks, and allowing it for the
        # session grants that directory.
        res, events, payload = asyncio.run(
            self._ask_flow("allow_session", {"command": "cat /var/www/x.log", "cwd": ""}, tool="run_terminal_command")
        )
        self.assertTrue(res.allowed)
        grants = permissions.grants_for(1, 1)
        self.assertTrue(any(g["value"] == "/var/www" for g in grants), grants)

    def test_compound_shell_is_never_granted_for_the_session(self):
        # Shell programs can reach paths that are not operands, so a compound
        # command is approved once and never offered as allow-always.
        res, events, payload = asyncio.run(
            self._ask_flow("allow_session", {"command": "cd /srv/other && cat x.log", "cwd": ""}, tool="run_terminal_command")
        )
        self.assertTrue(res.allowed)
        self.assertEqual(events[0].get("grant_kind"), "")
        self.assertFalse(any(g["value"] == "/srv/other" for g in permissions.grants_for(1, 1)))

    def test_subagent_requester_label(self):
        events = []

        async def flow():
            gate = _gate(notify=events.append, requester={"role": "subagent", "type": "explore", "description": "research api"})
            task = asyncio.create_task(self._review(gate, args={"path": "/etc/hosts"}))
            for _ in range(200):
                await asyncio.sleep(0.02)
                if events and events[0].get("status") == "pending":
                    break
            permissions.resolve(1, 1, events[0]["id"], "deny")
            return await task

        res = asyncio.run(flow())
        self.assertFalse(res.allowed)
        self.assertEqual(events[0]["requester_label"], "research api (explore)")
        self.assertEqual(events[0]["requester"]["role"], "subagent")

    def test_tmp_outside_project_relaxes_confinement(self):
        # /tmp is always allowed; workspace confinement must honour that even
        # when the working root is a project (the subagent write_file bug).
        from app import workspace

        for root in ("/srv/app", "/tmp/work", "~"):
            res = asyncio.run(self._review(_gate(), tool="write_file", args={"path": "/tmp/fuse/dz.py"}, root=root))
            self.assertTrue(res.allowed, root)
            self.assertEqual(res.root, "", root)
            self.assertEqual(workspace._confine("/tmp/fuse/dz.py", res.root), "/tmp/fuse/dz.py")
            self.assertFalse(res.promotable, root)

    def test_tmp_shell_cwd_relaxes(self):
        res = asyncio.run(self._review(_gate(), tool="run_terminal_command", args={"command": "ls", "cwd": "/tmp/fuse"}))
        self.assertTrue(res.allowed)
        self.assertEqual(res.root, "")
        res = asyncio.run(self._review(_gate(), tool="run_terminal_command", args={"command": "ls"}))
        self.assertIsNone(res.root)

    def test_promotable_only_for_in_scope_writes(self):
        res = asyncio.run(self._review(_gate(), tool="write_file", args={"path": "src/x.py"}))
        self.assertTrue(res.promotable)
        self.assertEqual(res.resolved, "/srv/app/src/x.py")
        res = asyncio.run(self._review(_gate(), tool="read_file", args={"path": "src/x.py"}))
        self.assertFalse(res.promotable)
        permissions.add_grant(1, 1, 7, "dir", "/var/www")
        res = asyncio.run(self._review(_gate(), tool="write_file", args={"path": "/var/www/x"}))
        self.assertTrue(res.allowed)
        self.assertFalse(res.promotable)

    def test_spawn_cwd_outside_scope_is_reviewed(self):
        res = asyncio.run(self._review(_gate(unattended=True), tool="spawn_subagent", args={"cwd": "/"}))
        self.assertFalse(res.allowed)
        res = asyncio.run(self._review(_gate(unattended=True), tool="spawn_subagent", args={"cwd": "/etc"}))
        self.assertFalse(res.allowed)
        for cwd in ("src", "/srv/app/lib", "/tmp/work", ""):
            res = asyncio.run(self._review(_gate(unattended=True), tool="spawn_subagent", args={"cwd": cwd}))
            self.assertTrue(res.allowed, cwd)

    def test_windows_temp(self):
        self.assertEqual(permissions.expand_temp("%TEMP%\\out\\a.txt", True), "~\\AppData\\Local\\Temp\\out\\a.txt")
        self.assertEqual(permissions.expand_temp("%TEMP%\\a", False), "%TEMP%\\a")
        self.assertTrue(permissions.is_temp_path("C:\\Users\\me\\AppData\\Local\\Temp\\x", True))
        self.assertTrue(permissions.is_temp_path("%TEMP%", True))
        self.assertFalse(permissions.is_temp_path("C:\\Users\\me\\Documents", True))
        from app import workspace

        self.assertEqual(workspace._confine("%TEMP%\\x.txt", "C:\\proj", windows=True), "~\\AppData\\Local\\Temp\\x.txt")

        async def win():
            return await permissions.review(
                tool="write_file", args={"path": "C:\\Users\\me\\AppData\\Local\\Temp\\x.txt"}, user_id=1,
                host_id=7, host_alias="win", root="C:\\proj", windows=True, gate=_gate(unattended=True),
            )

        res = asyncio.run(win())
        self.assertTrue(res.allowed)
        self.assertEqual(res.root, "")


class ModeMatrixTest(PermSchemaTest):
    """The mode contract: ask is scope-based, auto lets Jev judge whatever
    would ask, bypass never asks. `judge` records whether Jev was called."""

    CASES = (
        ("read_file", {"path": "src/main.py"}, "silent", "silent", "silent"),
        ("grep", {"pattern": "x", "path": "src"}, "silent", "silent", "silent"),
        ("write_file", {"path": "sub/dir/new.py", "content": "x"}, "silent", "silent", "silent"),
        ("read_file", {"path": ".env"}, "silent", "silent", "silent"),
        ("read_file", {"path": "/etc/hosts"}, "card", "jev", "silent"),
        ("write_file", {"path": "/var/www/x", "content": "x"}, "card", "jev", "silent"),
        ("run_terminal_command", {"command": "pytest -q"}, "silent", "silent", "silent"),
        ("run_terminal_command", {"command": "git status && git diff"}, "silent", "silent", "silent"),
        ("run_terminal_command", {"command": "cat /var/log/syslog"}, "card", "jev", "silent"),
        ("run_terminal_command", {"command": "echo $HOME"}, "card", "jev", "silent"),
        ("run_terminal_command", {"command": "rm -rf build"}, "card", "jev", "silent"),
        ("run_terminal_command", {"command": "sudo reboot"}, "card", "jev", "silent"),
    )

    def setUp(self):
        with db.tx() as conn:
            conn.execute("DELETE FROM chat_permission_asks WHERE thread_id=1")
            conn.execute("DELETE FROM chat_permission_grants WHERE thread_id=1")
            conn.execute("UPDATE chat_threads SET bypass_permissions=0, auto_permissions=0 WHERE id=1")

    def _expect(self, mode, tool, args):
        """(result, jev_called, card_raised) for one case. A raised card is
        answered with Allow so ask-mode cases return instead of timing out."""
        from unittest.mock import AsyncMock, patch

        # Each case starts from ask mode: a bypass or auto case must not leak
        # its flag into the next one.
        with db.tx() as conn:
            conn.execute("UPDATE chat_threads SET bypass_permissions=0, auto_permissions=0 WHERE id=1")
        if mode == "auto":
            permissions.set_auto(1, 1, True)
        elif mode == "bypass":
            permissions.set_bypass(1, 1, True)
        judge = AsyncMock(return_value=True)
        events: list = []

        async def flow():
            async def wait_card():
                for _ in range(250):
                    await asyncio.sleep(0.02)
                    for event in events:
                        if event.get("event") == "permission" and event.get("status") == "pending":
                            return event
                return None

            # _auto_verdict refuses without an OpenRouter key before it ever
            # reaches jev.judge, so the verdict itself is the spy point.
            with patch.object(permissions, "_auto_verdict", judge):
                task = asyncio.create_task(permissions.review(
                    tool=tool, args=args, user_id=1, host_id=7, host_alias="prod",
                    root="/srv/app", windows=False,
                    gate=_gate(notify=events.append),
                    deny_reason="command blocked by denylist" if "sudo" in str(args.get("command") or "") else "",
                ))
                card = await wait_card()
                if card is not None:
                    permissions.resolve(1, 1, card["id"], "allow")
                return await task, card is not None

        res, card = asyncio.run(flow())
        return res, judge.await_count > 0, card

    def test_matrix(self):
        for tool, args, ask, auto, bypass in self.CASES:
            for mode, expect in (("ask", ask), ("auto", auto), ("bypass", bypass)):
                with self.subTest(tool=tool, target=args.get("path") or args.get("command"), mode=mode):
                    res, called, card = self._expect(mode, tool, args)
                    self.assertTrue(res.allowed)
                    self.assertEqual(called, expect == "jev")
                    self.assertEqual(card, expect == "card")


class ResolveTest(PermSchemaTest):
    def test_wrong_user_not_found(self):
        with db.tx() as conn:
            conn.execute("DELETE FROM chat_permission_asks")
            conn.execute(
                """INSERT INTO chat_permission_asks(
                       id,user_id,thread_id,run_id,status,decision,kind,tool,host_id,host,path,cwd,command,
                       secret,requester,grant_kind,grant_value,created_at,expires_at)
                   VALUES ('pa_x',1,1,'r','pending','','path','read_file',7,'prod','/etc','', '',0,'Main agent','','','2026-01-01T00:00:00Z','2099-01-01T00:00:00Z')"""
            )
        with self.assertRaises(LookupError):
            permissions.resolve(2, 3, "pa_x", "allow")

    def test_deny_sets_denied_status(self):
        with db.tx() as conn:
            conn.execute("DELETE FROM chat_permission_asks")
            conn.execute(
                """INSERT INTO chat_permission_asks(
                       id,user_id,thread_id,run_id,status,decision,kind,tool,host_id,host,path,cwd,command,
                       secret,requester,grant_kind,grant_value,created_at,expires_at)
                   VALUES ('pa_d',1,1,'r','pending','','path','read_file',7,'prod','/etc','', '',0,'Main agent','','','2026-01-01T00:00:00Z','2099-01-01T00:00:00Z')"""
            )
        payload = permissions.resolve(1, 1, "pa_d", "deny")
        self.assertEqual(payload["status"], "denied")
        row = db.fetchone("SELECT status, decision FROM chat_permission_asks WHERE id='pa_d'")
        self.assertEqual(row["status"], "denied")
        self.assertEqual(row["decision"], "deny")

    def _pending_race_ask(self, ask_id):
        with db.tx() as conn:
            conn.execute("DELETE FROM chat_permission_asks")
            conn.execute("DELETE FROM chat_permission_grants")
            conn.execute(
                """INSERT INTO chat_permission_asks(
                       id,user_id,thread_id,run_id,status,decision,kind,tool,host_id,host,path,cwd,command,
                       secret,requester,grant_kind,grant_value,created_at,expires_at)
                   VALUES (?,1,1,'r','pending','','path','read_file',7,'prod','/var/www/index.html','', '',0,
                       'Main agent','dir','/var/www','2026-01-01T00:00:00Z','2099-01-01T00:00:00Z')""",
                (ask_id,),
            )

    def test_resolve_after_resolution_returns_stored_outcome(self):
        self._pending_race_ask("pa_done")
        first = permissions.resolve(1, 1, "pa_done", "allow_session")
        self.assertEqual(first["status"], "allowed")
        self.assertEqual(len(permissions.grants_for(1, 1)), 1)
        again = permissions.resolve(1, 1, "pa_done", "deny")
        self.assertEqual(again["status"], "allowed")
        self.assertEqual(again["decision"], "allow_session")
        self.assertEqual(len(permissions.grants_for(1, 1)), 1)

    def test_concurrent_resolves_apply_one_decision(self):
        # The transition is conditional on status='pending' and checked via
        # rowcount, so racing resolvers (or a timeout) cannot both apply.
        self._pending_race_ask("pa_race")
        start = threading.Event()
        outcomes: list = []

        def decide(action):
            start.wait(5)
            try:
                outcomes.append(permissions.resolve(1, 1, "pa_race", action))
            except Exception as exc:  # surfaced through outcomes below
                outcomes.append(exc)

        threads = [threading.Thread(target=decide, args=(a,)) for a in ("allow_session", "deny", "allow")]
        for t in threads:
            t.start()
        start.set()
        for t in threads:
            t.join(15)
            self.assertFalse(t.is_alive())
        self.assertEqual(len(outcomes), 3)
        for item in outcomes:
            self.assertNotIsInstance(item, Exception)
        row = db.fetchone("SELECT status, decision FROM chat_permission_asks WHERE id='pa_race'")
        grants = permissions.grants_for(1, 1)
        if row["decision"] == "deny":
            self.assertEqual(row["status"], "denied")
            self.assertEqual(grants, [])
        else:
            self.assertEqual(row["status"], "allowed")
            self.assertLessEqual(len(grants), 1)

    def test_set_bypass_resolves_pending(self):
        with db.tx() as conn:
            conn.execute("DELETE FROM chat_permission_asks")
            conn.execute("UPDATE chat_threads SET bypass_permissions=0, auto_permissions=0")
            conn.execute(
                """INSERT INTO chat_permission_asks(
                       id,user_id,thread_id,run_id,status,decision,kind,tool,host_id,host,path,cwd,command,
                       secret,requester,grant_kind,grant_value,created_at,expires_at)
                   VALUES ('pa_b',1,1,'r','pending','','path','read_file',7,'prod','/etc','', '',0,'Main agent','','','2026-01-01T00:00:00Z','2099-01-01T00:00:00Z')"""
            )
        permissions.set_bypass(1, 1, True)
        row = db.fetchone("SELECT status, decision FROM chat_permission_asks WHERE id='pa_b'")
        self.assertEqual(row["status"], "allowed")
        self.assertEqual(row["decision"], "allow")
        permissions.set_bypass(1, 1, False)

    def test_set_auto_never_approves_waiting_asks_itself(self):
        # Turning auto on only flips the flag. Each waiting gate sees it on its
        # next poll and asks the reviewer for its own call (see test_jev).
        with db.tx() as conn:
            conn.execute("DELETE FROM chat_permission_asks")
            for ask_id, command, secret in (("pa_ok", "ls /etc", 0), ("pa_rm", "rm -rf /var/www", 0), ("pa_sec", "", 1)):
                conn.execute(
                    """INSERT INTO chat_permission_asks(
                           id,user_id,thread_id,run_id,status,decision,kind,tool,host_id,host,path,cwd,command,
                           secret,requester,grant_kind,grant_value,created_at,expires_at)
                       VALUES (?,1,1,'r','pending','','shell','run_terminal_command',7,'prod','','',?,?,'Main agent','','','2026-01-01T00:00:00Z','2099-01-01T00:00:00Z')""",
                    (ask_id, command, secret),
                )
        permissions.set_auto(1, 1, True)
        try:
            self.assertTrue(permissions.auto_enabled(1, 1))
            status = {r["id"]: r["status"] for r in db.fetchall("SELECT id, status FROM chat_permission_asks")}
            self.assertEqual(status, {"pa_ok": "pending", "pa_rm": "pending", "pa_sec": "pending"})
        finally:
            permissions.set_auto(1, 1, False)
            with db.tx() as conn:
                conn.execute("DELETE FROM chat_permission_asks")

    def test_revoke_grant(self):
        with db.tx() as conn:
            conn.execute("DELETE FROM chat_permission_grants")
        permissions.add_grant(1, None, 7, "dir", "/var/www")
        grants = permissions.grants_for(1, 1)
        self.assertEqual(len(grants), 1)
        self.assertTrue(permissions.revoke_grant(1, grants[0]["id"]))
        self.assertFalse(permissions.revoke_grant(1, grants[0]["id"]))
        self.assertEqual(permissions.grants_for(1, 1), [])

    def test_panel_groups_grants_by_host(self):
        ts = db.now()
        with db.tx() as conn:
            conn.execute("DELETE FROM chat_permission_grants")
            conn.execute("DELETE FROM user_projects")
            conn.execute("DELETE FROM user_hosts")
            conn.execute(
                """INSERT INTO user_hosts(
                       id, user_id, alias, label, hostname, port, username, auth_type, secret, passphrase,
                       default_cwd, created_at, updated_at, agent_os, disabled)
                   VALUES (7,1,'prod','','prod.local',22,'root','password','x','','/srv/app',?,?, 'linux',0),
                          (8,1,'winbox','','win.local',22,'root','password','x','','C:\\work',?,?, 'windows',0),
                          (9,1,'off','','off.local',22,'root','password','x','','/opt',?,?, 'linux',1)""",
                (ts, ts, ts, ts, ts, ts),
            )
            conn.execute("UPDATE chat_threads SET host_id=7, project_id=NULL WHERE id=1")
        permissions.add_grant(1, 1, 7, "dir", "/var/www")
        permissions.add_grant(1, None, 7, "file", "/srv/app/.env")
        permissions.add_grant(1, None, 8, "dir", "D:\\extra")
        permissions.add_grant(1, 1, 7, "dir", "/tmp")
        panel = permissions.panel_for(1, 1)
        aliases = [h["host"] for h in panel["hosts"]]
        self.assertEqual(aliases, ["prod", "winbox"])
        prod = panel["hosts"][0]
        self.assertTrue(prod["active"])
        self.assertEqual([r["path"] for r in prod["roots"]], ["/tmp", "/srv/app"])
        grant_vals = {g["value"] for g in prod["grants"]}
        self.assertEqual(grant_vals, {"/var/www", "/srv/app/.env"})
        win = panel["hosts"][1]
        self.assertEqual([r["path"] for r in win["roots"]], ["%TEMP%", "C:\\work"])
        self.assertEqual([g["value"] for g in win["grants"]], ["D:\\extra"])
        self.assertEqual(panel["thread_host_id"], 7)

    def test_panel_includes_auto_flags(self):
        permissions.set_auto(1, 1, False)
        panel = permissions.panel_for(1, 1)
        self.assertIn("auto", panel)
        self.assertIn("auto_available", panel)
        self.assertFalse(panel["auto"])

    def test_panel_home_root_is_tilde(self):
        ts = db.now()
        with db.tx() as conn:
            conn.execute("DELETE FROM chat_permission_grants")
            conn.execute("DELETE FROM user_projects")
            conn.execute("DELETE FROM user_hosts")
            conn.execute(
                """INSERT INTO user_hosts(
                       id, user_id, alias, label, hostname, port, username, auth_type, secret, passphrase,
                       default_cwd, created_at, updated_at, agent_os, disabled)
                   VALUES (11,1,'bare','','bare.local',22,'root','password','x','','',?,?, 'linux',0)""",
                (ts, ts),
            )
            conn.execute("UPDATE chat_threads SET host_id=11, project_id=NULL WHERE id=1")
        panel = permissions.panel_for(1, 1)
        self.assertEqual([r["path"] for r in panel["hosts"][0]["roots"]], ["/tmp", "~"])


class ParserTest(PermSchemaTest):
    def test_tool_permission_roundtrip(self):
        rec = {
            "id": "pa_abc",
            "kind": "tool_permission",
            "status": "allowed",
            "tool": "read_file",
            "host": "prod",
            "path": "/etc/hosts",
            "cwd": "",
            "command": "",
            "secret": False,
            "requester": "research api (explore)",
            "reasons": ["outside the allowed roots"],
            "grant_scope": "allow_session",
            "grant_kind": "dir",
            "grant_value": "/etc",
            "expires_at": "2099-01-01T00:00:00Z",
        }
        out = _parse_permissions([rec], strict=True)
        self.assertEqual(out[0]["kind"], "tool_permission")
        self.assertEqual(out[0]["status"], "allowed")
        self.assertEqual(out[0]["grant_scope"], "allow_session")
        self.assertEqual(out[0]["grant_kind"], "dir")
        self.assertEqual(out[0]["grant_value"], "/etc")
        bad = {"id": "pa_x", "kind": "tool_permission", "status": "weird"}
        out = _parse_permissions([bad])
        self.assertEqual(out[0]["status"], "denied")

    def test_expired_pending_is_timeout(self):
        rec = {
            "id": "pa_old",
            "kind": "tool_permission",
            "status": "pending",
            "tool": "list_dir",
            "expires_at": "2020-01-01T00:00:00Z",
        }
        out = _parse_permissions([rec])
        self.assertEqual(out[0]["status"], "timeout")
        live = {
            "id": "pa_live",
            "kind": "tool_permission",
            "status": "pending",
            "tool": "list_dir",
            "expires_at": "2099-01-01T00:00:00Z",
        }
        self.assertEqual(_parse_permissions([live])[0]["status"], "pending")


class RecoverTest(PermSchemaTest):
    def test_cancel_run_asks(self):
        with db.tx() as conn:
            conn.execute("DELETE FROM chat_permission_asks")
            conn.execute(
                """INSERT INTO chat_permission_asks(
                       id,user_id,thread_id,run_id,status,decision,kind,tool,host_id,host,path,cwd,command,
                       secret,requester,grant_kind,grant_value,created_at,expires_at)
                   VALUES ('pa_c',1,1,'run-9','pending','','path','read_file',7,'prod','/etc','', '',0,'Main agent','','','2026-01-01T00:00:00Z','2099-01-01T00:00:00Z')"""
            )
        permissions.cancel_run_asks("run-9")
        row = db.fetchone("SELECT status FROM chat_permission_asks WHERE id='pa_c'")
        self.assertEqual(row["status"], "cancelled")


class PendingAsksTest(PermSchemaTest):
    def _seed(self):
        future = "2099-01-01T00:00:00Z"
        past = "2020-01-01T00:00:00Z"
        with db.tx() as conn:
            conn.execute("DELETE FROM chat_permission_asks")
            for ident, tid, expires, requester in (
                ("pa_live_sub", 1, future, "scanner (explore)"),
                ("pa_live_main", 1, future, "Main agent"),
                ("pa_expired", 1, past, "Main agent"),
                ("pa_other_thread", 2, future, "Main agent"),
                ("pa_done", 1, future, "Main agent"),
            ):
                conn.execute(
                    """INSERT INTO chat_permission_asks(
                           id,user_id,thread_id,run_id,status,decision,kind,tool,host_id,host,path,cwd,command,
                           secret,requester,grant_kind,grant_value,created_at,expires_at)
                       VALUES (?,?,?,?,'pending','','path','run_terminal_command',7,'prod','','','cat /etc/shadow',
                           0,?,'dir','/etc','2026-01-01T00:00:00Z',?)""",
                    (ident, 1, tid, f"run-{ident}", requester, expires),
                )
            conn.execute("UPDATE chat_permission_asks SET status='allowed' WHERE id='pa_done'")

    def test_pending_asks_lists_live_thread_asks_only(self):
        self._seed()
        pend = permissions.pending_asks(1, 1)
        ids = [p["id"] for p in pend]
        self.assertIn("pa_live_sub", ids)
        self.assertIn("pa_live_main", ids)
        self.assertNotIn("pa_expired", ids)
        self.assertNotIn("pa_other_thread", ids)
        self.assertNotIn("pa_done", ids)
        sub = next(p for p in pend if p["id"] == "pa_live_sub")
        self.assertEqual(sub["kind"], "tool_permission")
        self.assertEqual(sub["status"], "pending")
        self.assertEqual(sub["requester"], "scanner (explore)")
        self.assertEqual(sub["tool"], "run_terminal_command")
        self.assertEqual(sub["command"], "cat /etc/shadow")

    def test_panel_exposes_pending_for_reconnect_seeding(self):
        self._seed()
        panel = permissions.panel_for(1, 1)
        ids = [p["id"] for p in panel["pending"]]
        self.assertEqual(ids, ["pa_live_sub", "pa_live_main"])


if __name__ == "__main__":
    unittest.main()
