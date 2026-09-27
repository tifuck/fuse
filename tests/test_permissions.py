"""Permission gate: scope evaluation, grants, ask lifecycle (no live hosts)."""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
_TMP = tempfile.mkdtemp(prefix="fuse-perm-")
os.environ.setdefault("DATABASE_PATH", str(Path(_TMP) / "perm.db"))
os.environ.setdefault("APP_SECRET", "x" * 32)

from app import db, permissions
from app.chats import _parse_permissions


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

    def test_secret_file_inside_root_asks(self):
        res, events, payload = asyncio.run(self._ask_flow("allow_session", {"path": ".env"}))
        self.assertTrue(res.allowed)
        grants = permissions.grants_for(1, 1)
        self.assertEqual(grants[0]["kind"], "file")
        self.assertEqual(grants[0]["value"], "/srv/app/.env")
        self.assertEqual(events[0]["grant_kind"], "file")
        self.assertEqual(events[0]["grant_value"], "/srv/app/.env")

    def test_shell_cwd_and_paths_ask(self):
        res, events, payload = asyncio.run(
            self._ask_flow("allow_session", {"command": "cd /var/www && cat x.log", "cwd": ""}, tool="run_terminal_command")
        )
        self.assertTrue(res.allowed)
        grants = permissions.grants_for(1, 1)
        self.assertTrue(any(g["value"] == "/var/www" for g in grants), grants)

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

    def test_set_auto_leaves_destructive_and_secret_pending(self):
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
            status = {r["id"]: r["status"] for r in db.fetchall("SELECT id, status FROM chat_permission_asks")}
            self.assertEqual(status, {"pa_ok": "allowed", "pa_rm": "pending", "pa_sec": "pending"})
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
