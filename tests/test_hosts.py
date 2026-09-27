from __future__ import annotations

import asyncio
import json
import os
import re
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
_TMP = tempfile.mkdtemp(prefix="fuse-hosts-")
os.environ.setdefault("DATABASE_PATH", str(Path(_TMP) / "hosts.db"))
os.environ.setdefault("APP_SECRET", "y" * 32)

from app import accounts, chats, db, hosts, vault, workspace


class HostsProjectsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="fuse-hosts-")
        self.addCleanup(self.tmp.cleanup)
        self.conn = sqlite3.connect(str(Path(self.tmp.name) / "test.db"), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
        db._init(self.conn)
        self.addCleanup(self.conn.close)
        self.connection_patch = patch.object(db, "connect", return_value=self.conn)
        self.connection_patch.start()
        self.addCleanup(self.connection_patch.stop)
        vault.lock_all()
        accounts.create_user("1@test.invalid", "password12", admin=False)
        vault.unlock(1, "password12")

    def test_project_fields_and_resolve_by_id(self):
        a = hosts.add_host(
            1,
            {"alias": "alpha", "hostname": "a.example.com", "username": "u", "auth_type": "key", "secret": "k"},
        )
        b = hosts.add_host(
            1,
            {"alias": "bravo", "hostname": "b.example.com", "username": "u", "auth_type": "key", "secret": "k"},
        )
        p1 = hosts.add_project(
            1,
            a["id"],
            {"name": "api", "remote_path": "/srv/a", "default_model": "grok-4", "permission_mode": "read_only"},
        )
        hosts.add_project(1, b["id"], {"name": "api", "remote_path": "/srv/b"})
        self.assertIsNone(hosts.resolve_project(1, "api"))
        found = hosts.resolve_project_id(1, p1["id"])
        self.assertEqual(found["alias"], "alpha")
        self.assertEqual(found["remote_path"], "/srv/a")
        self.assertEqual(found["default_model"], "grok-4")
        self.assertEqual(found["permission_mode"], "read_only")
        patched = hosts.patch_project(1, a["id"], p1["id"], {"permission_mode": "project", "default_model": ""})
        self.assertEqual(patched["permission_mode"], "project")
        self.assertEqual(patched["default_model"], "")

    def test_catalog_includes_cwd_and_model(self):
        h = hosts.add_host(
            1,
            {
                "alias": "box",
                "hostname": "box.example.com",
                "username": "u",
                "auth_type": "key",
                "secret": "k",
                "default_cwd": "/srv",
            },
        )
        hosts.add_project(
            1,
            h["id"],
            {"name": "web", "remote_path": "/srv/web", "default_model": "glm-5", "permission_mode": "read_only"},
        )
        text = hosts.catalog_for_prompt(1)
        self.assertIn("roots: /tmp, /srv", text)
        self.assertNotIn("cwd: /srv", text)
        self.assertIn("web (/srv/web) [glm-5, read-only]", text)
        proj = hosts.list_bundle(1)["hosts"][0]["projects"][0]
        text2 = hosts.catalog_for_prompt(1, bound_host_id=h["id"], bound_project_id=proj["id"])
        self.assertIn("bound to box / web", text2)

    def test_catalog_roots_follow_session_work_dir(self):
        linux = hosts.add_host(
            1,
            {
                "alias": "box",
                "hostname": "box.example.com",
                "username": "u",
                "auth_type": "key",
                "secret": "k",
                "default_cwd": "/srv",
            },
        )
        hosts.add_host(
            1,
            {
                "alias": "homebox",
                "hostname": "home.example.com",
                "username": "u",
                "auth_type": "key",
                "secret": "k",
            },
        )
        pairing = hosts.mint_pairing(1)
        win, _token = hosts.redeem_pairing(
            pairing["code"],
            {"hostname": "pc", "username": "ada", "ver": "1.0.0", "os": "windows", "arch": "windows-amd64"},
        )
        hosts.patch_host(1, win["id"], {"default_cwd": r"C:\work"})
        unbound = hosts.catalog_for_prompt(1)
        self.assertIn("roots: /tmp, /srv", unbound)
        self.assertIn("roots: /tmp, ~", unbound)
        self.assertIn(r"roots: %TEMP%, C:\work", unbound)
        bound = hosts.catalog_for_prompt(1, bound_host_id=linux["id"], bound_cwd="/srv/web")
        box_line = next(line for line in bound.splitlines() if line.startswith("- box "))
        home_line = next(line for line in bound.splitlines() if line.startswith("- homebox "))
        self.assertIn("roots: /tmp, /srv/web", box_line)
        self.assertNotIn("roots: /tmp, /srv,", box_line)
        self.assertIn("roots: /tmp, ~", home_line)
        self.assertLessEqual(len(unbound), hosts.CATALOG_CHARS)

    def test_open_reuses_latest_thread(self):
        h = hosts.add_host(
            1,
            {"alias": "box", "hostname": "box.example.com", "username": "u", "auth_type": "key", "secret": "k"},
        )
        p = hosts.add_project(1, h["id"], {"name": "api", "remote_path": "/srv/api", "default_model": "grok-4"})
        first = chats.create_thread(1, kind="project", host_id=h["id"], project_id=p["id"], model="")
        self.assertEqual(first["model"], "grok-4")
        self.assertEqual(hosts.latest_project_thread_id(1, h["id"], p["id"]), first["id"])
        self.assertRegex(first["id"], r"^[A-Za-z0-9]{22}$")
        second = chats.create_thread(1, kind="project", host_id=h["id"], project_id=p["id"], title="later")
        self.assertEqual(hosts.latest_project_thread_id(1, h["id"], p["id"]), second["id"])

    def test_project_session_flags_auto_title(self):
        from app import chat_store as store

        h = hosts.add_host(
            1,
            {"alias": "box", "hostname": "box.example.com", "username": "u", "auth_type": "key", "secret": "k"},
        )
        p = hosts.add_project(1, h["id"], {"name": "api", "remote_path": "/srv/api"})
        thread = chats.create_thread(1, kind="project", host_id=h["id"], project_id=p["id"])
        self.assertEqual(thread["title"], "New chat")
        self.assertTrue(chats.is_placeholder_title(thread["title"], project_name="api"))
        self.assertTrue(chats.is_placeholder_title("api", project_name="api"))
        self.assertFalse(chats.is_placeholder_title("Disk space", project_name="api"))
        result = store.start_turn(
            1,
            thread["id"],
            "How much disk is free on all hosts",
            thread["revision"],
            "title-1",
            auto_title=True,
        )
        self.assertTrue(result["run"]["request"]["generate_title"])
        self.assertTrue(result["thread"]["running"])
        self.assertTrue(chats.list_threads(1)[0]["running"])
        self.assertTrue(chats.list_sidebar_projects(1)[0]["running"])
        store.finish_run(result["run"]["id"], {"content": "ok"}, "completed")
        self.assertFalse(chats.get_thread(1, thread["id"])["running"])
        self.assertFalse(chats.list_sidebar_projects(1)[0]["running"])
        named = chats.create_thread(1, kind="project", host_id=h["id"], project_id=p["id"], title="api")
        flagged = store.start_turn(
            1, named["id"], "Fix the handler", named["revision"], "title-2", auto_title=True
        )
        self.assertTrue(flagged["run"]["request"]["generate_title"])
        applied = store.apply_generated_title(
            1, named["id"], "Handler fix", flagged["run"]["request"]["title_placeholder"]
        )
        self.assertIsNotNone(applied)
        self.assertEqual(applied["title"], "Handler fix")

    def test_sidebar_lists_and_searches_native_projects(self):
        h = hosts.add_host(
            1,
            {"alias": "box", "hostname": "box.example.com", "username": "u", "auth_type": "key", "secret": "k"},
        )
        p = hosts.add_project(1, h["id"], {"name": "billing", "remote_path": "/srv/billing"})
        chats.create_thread(1, title="grocery list", messages=[{"role": "user", "content": "buy milk"}])
        listed = chats.list_sidebar_projects(1)
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]["name"], "billing")
        self.assertEqual(listed[0]["host_alias"], "box")
        self.assertIsNone(listed[0]["thread_id"])
        self.assertFalse(listed[0]["running"])
        chats.create_thread(1, kind="project", host_id=h["id"], project_id=p["id"], title="billing work")
        listed = chats.list_sidebar_projects(1)
        self.assertRegex(listed[0]["thread_id"], r"^[A-Za-z0-9]{22}$")
        hits = chats.search_threads(1, "billing")
        self.assertTrue(any(item["kind"] == "project" for item in hits))
        projects = chats.search_projects(1, "bill")
        self.assertEqual(projects[0]["name"], "billing")
        milk = chats.search_threads(1, "milk")
        self.assertTrue(any("grocery" in (item["title"] or "").lower() for item in milk))

    def test_working_session_promotes_chat_to_project(self):
        h = hosts.add_host(
            1,
            {"alias": "box", "hostname": "box.example.com", "username": "u", "auth_type": "key", "secret": "k"},
        )
        p = hosts.add_project(1, h["id"], {"name": "api", "remote_path": "/srv/api"})
        thread = chats.create_thread(
            1,
            title="debug the handler",
            context="notes about the handler",
            messages=[{"role": "user", "content": "please inspect the handler"}],
        )
        self.assertEqual(thread["kind"], "chat")
        self.assertEqual(thread["context"], "notes about the handler")
        promoted = chats.promote_working_session(1, thread["id"], host_id=h["id"], cwd="/srv/api")
        self.assertIsNotNone(promoted)
        self.assertEqual(promoted["kind"], "project")
        self.assertEqual(promoted["context"], "notes about the handler")
        self.assertEqual(promoted["title"], "debug the handler")
        self.assertEqual(promoted["messages"][0]["content"], "please inspect the handler")
        self.assertEqual(promoted["project_id"], p["id"])
        self.assertEqual(promoted["host_alias"], "box")
        self.assertEqual(promoted["project_name"], "api")
        self.assertRegex(promoted["id"], r"^[A-Za-z0-9]{22}$")
        self.assertIsNone(chats.promote_working_session(1, thread["id"], host_id=h["id"], cwd="/srv/api"))
        orphan = chats.create_thread(1, title="scratch")
        # Reads and temp folders never bind a chat to a project.
        self.assertIsNone(chats.promote_working_session(1, orphan["id"], host_id=h["id"], cwd="/srv/scratch-app", tool="list_dir"))
        self.assertIsNone(chats.promote_working_session(1, orphan["id"], host_id=h["id"], cwd="/tmp/scratch-app", tool="write_file"))
        self.assertIsNone(chats.promote_working_session(1, orphan["id"], host_id=h["id"], cwd="/srv/api", tool="read_file"))
        created = chats.promote_working_session(
            1, orphan["id"], host_id=h["id"], cwd="/srv/scratch-app", tool="write_file"
        )
        self.assertEqual(created["kind"], "project")
        self.assertEqual(created["project_name"], "scratch-app")
        self.assertEqual(created["host_id"], h["id"])
        listed = chats.list_sidebar_projects(1)
        self.assertEqual({item["name"] for item in listed}, {"api", "scratch-app"})
        from app import chat_store as store

        proj_thread = chats.create_thread(1, kind="project", host_id=h["id"], project_id=p["id"])
        turned = store.start_turn(1, proj_thread["id"], "Hello", proj_thread["revision"], "proj-turn")
        self.assertTrue(turned["run"]["request"]["agent"]["project_session"])
        fresh = chats.create_thread(1, title="still a chat")
        db.execute("INSERT OR REPLACE INTO user_agent_settings(user_id, agent_coding) VALUES(1, 1)")
        gated = store.start_turn(1, fresh["id"], "Hi", fresh["revision"], "chat-gate")
        self.assertTrue(gated["run"]["request"]["agent"]["coding"])
        self.assertFalse(gated["run"]["request"]["agent"]["project_session"])
        self.assertEqual(gated["thread"]["kind"], "chat")
        self.assertEqual(gated["thread"]["context"] or "", "")

    def test_fleet_shell_does_not_bind_a_project(self):
        h = hosts.add_host(
            1,
            {"alias": "box", "hostname": "box.example.com", "username": "u", "auth_type": "key", "secret": "k", "default_cwd": "/srv/api"},
        )
        hosts.add_project(1, h["id"], {"name": "api", "remote_path": "/srv/api"})
        thread = chats.create_thread(1, title="disk space")
        self.assertIsNone(
            chats.promote_working_session(1, thread["id"], host_id=h["id"], cwd="/", tool="run_terminal_command")
        )
        self.assertIsNone(
            chats.promote_working_session(1, thread["id"], host_id=h["id"], cwd="", tool="run_terminal_command")
        )
        self.assertIsNone(
            chats.promote_working_session(1, thread["id"], host_id=h["id"], cwd="/srv/api", tool="run_terminal_command")
        )
        self.assertEqual(chats.get_thread(1, thread["id"])["kind"], "chat")
        first = chats.create_thread(1, kind="project", host_id=h["id"], project_id=hosts.list_bundle(1)["hosts"][0]["projects"][0]["id"], title="old session")
        second = chats.create_thread(1, kind="project", host_id=h["id"], project_id=first["project_id"], title="later session")
        listed = chats.list_threads(1)
        project_threads = [item for item in listed if item["kind"] == "project"]
        self.assertGreaterEqual(len(project_threads), 2)
        self.assertEqual(project_threads[0]["id"], second["id"])
        hits = chats.search_threads(1, "old session")
        self.assertTrue(any(item["id"] == first["id"] for item in hits))

    def test_read_only_blocks_writes(self):
        h = hosts.add_host(
            1,
            {"alias": "box", "hostname": "box.example.com", "username": "u", "auth_type": "key", "secret": "k"},
        )
        hosts.add_project(
            1,
            h["id"],
            {"name": "docs", "remote_path": "/srv/docs", "permission_mode": "read_only"},
        )
        self.assertEqual(hosts.effective_permission(1, bound_host_id=h["id"], bound_cwd="/srv/docs"), "read_only")
        # A subfolder (subagent cwd or write target) stays read-only; a
        # sibling with a shared prefix does not.
        self.assertEqual(hosts.effective_permission(1, bound_host_id=h["id"], bound_cwd="/srv/docs/sub"), "read_only")
        self.assertEqual(hosts.effective_permission(1, bound_host_id=h["id"], bound_cwd="/srv/docsx"), "project")
        text, rec = asyncio.run(
            workspace.execute(
                "write_file",
                json.dumps({"path": "README.md", "content": "x"}),
                user_id=1,
                bound_host_id=h["id"],
                bound_cwd="/srv/docs",
            )
        )
        self.assertEqual(rec["status"], "error")
        self.assertIn("read-only", rec["error"])
        self.assertIn("read-only", text)


class PromotionBindingTest(unittest.TestCase):
    def test_active_run_uses_selected_project_path(self):
        from app import chat_store as store

        for project_path, candidate in (("/srv/api", "/srv/api/src"), (r"C:\repo", "C:/repo/src")):
            with self.subTest(project_path=project_path):
                conn = MagicMock()
                conn.execute.return_value.fetchone.side_effect = [
                    {"kind": "chat", "project_id": None},
                    {"id": 4, "alias": "prod", "default_cwd": ""},
                    {"id": 8, "request": json.dumps({"agent": {"keep": True}})},
                ]
                with patch.object(store, "transaction") as transaction, patch.object(
                    chats, "resolve_thread_pk", return_value=7
                ), patch.object(chats, "_match_project_row", return_value={"id": 5, "remote_path": project_path}), patch.object(
                    store, "thread_event"
                ), patch.object(store, "thread_view", return_value={"project_id": 5}):
                    transaction.return_value.__enter__.return_value = conn
                    result = chats.promote_working_session(1, "thread", host_id=4, cwd=candidate, tool="write_file")
                self.assertEqual(result["project_id"], 5)
                updates = [call for call in conn.execute.call_args_list if call.args[0].startswith("UPDATE chat_runs")]
                self.assertEqual(len(updates), 1)
                agent = json.loads(updates[0].args[1][0])["agent"]
                self.assertEqual(agent["bound_cwd"], project_path)
                self.assertEqual(agent["bound_host_id"], 4)
                self.assertTrue(agent["project_session"])
                self.assertTrue(agent["keep"])


class HostsHttpTest(unittest.TestCase):
    def setUp(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from starlette.middleware.sessions import SessionMiddleware

        from app import web
        from app.config import settings

        self.tmp = tempfile.TemporaryDirectory(prefix="fuse-hosts-http-")
        self.addCleanup(self.tmp.cleanup)
        self.conn = sqlite3.connect(str(Path(self.tmp.name) / "test.db"), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
        db._init(self.conn)
        self.addCleanup(self.conn.close)
        self.connection_patch = patch.object(db, "connect", return_value=self.conn)
        self.connection_patch.start()
        self.addCleanup(self.connection_patch.stop)
        vault.lock_all()
        accounts.create_user("http@test.invalid", "password12", admin=False)
        app = FastAPI()
        app.add_middleware(
            SessionMiddleware,
            secret_key=settings.app_secret,
            https_only=True,
            same_site="strict",
        )
        app.include_router(web.router)
        self.client = TestClient(app, base_url="https://testserver")
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)

    def _csrf(self, html: str) -> str:
        match = re.search(r'name="csrf-token" content="([^"]+)"', html)
        if match:
            return match.group(1)
        match = re.search(r'name="csrf_token" value="([^"]+)"', html)
        if not match:
            self.fail("csrf token missing")
        return match.group(1)

    def test_signed_out_hosts_and_projects_redirect(self):
        hosts_res = self.client.get("/hosts", follow_redirects=False)
        self.assertEqual(hosts_res.status_code, 303)
        self.assertEqual(hosts_res.headers.get("location"), "/login")
        projects_res = self.client.get("/projects", follow_redirects=False)
        self.assertEqual(projects_res.status_code, 303)

    def test_login_sets_keep_cookie_and_page_lock(self):
        login_page = self.client.get("/login")
        self.assertEqual(login_page.status_code, 200)
        token = self._csrf(login_page.text)
        posted = self.client.post(
            "/login",
            data={"email": "http@test.invalid", "password": "password12", "csrf_token": token},
            follow_redirects=False,
        )
        self.assertEqual(posted.status_code, 303)
        cookies = ",".join(posted.headers.get_list("set-cookie")).lower()
        self.assertIn("fuse_vault", cookies)
        page = self.client.get("/hosts")
        self.assertEqual(page.status_code, 200)
        self.assertIn('data-vault="unlocked"', page.text)
        self.assertNotIn("Unlock your machines", page.text)
        vault.lock_all()
        self.assertFalse(vault.unlocked(1))
        again = self.client.get("/hosts/api")
        self.assertEqual(again.status_code, 200)
        self.assertEqual(again.json()["vault"], "unlocked")
        self.client.cookies.delete("fuse_vault")
        vault.lock_all()
        locked = self.client.get("/hosts")
        self.assertEqual(locked.status_code, 200)
        self.assertIn('data-vault="locked"', locked.text)
        csrf = self._csrf(locked.text)
        added = self.client.post(
            "/hosts",
            headers={"x-csrf-token": csrf},
            json={
                "alias": "box",
                "hostname": "example.com",
                "username": "u",
                "auth_type": "key",
                "secret": "k",
            },
        )
        self.assertEqual(added.status_code, 401)
        self.assertEqual(added.json()["error"]["type"], "vault_locked")
        minted = self.client.post("/hosts/pairings", headers={"x-csrf-token": csrf}, json={})
        self.assertEqual(minted.status_code, 201)
        body = minted.json()
        self.assertTrue(body["code"].startswith("fuse_"))
        self.assertIn("/i/", body["install"])

    def _login(self) -> str:
        login_page = self.client.get("/login")
        self.assertEqual(login_page.status_code, 200)
        posted = self.client.post(
            "/login",
            data={
                "email": "http@test.invalid",
                "password": "password12",
                "csrf_token": self._csrf(login_page.text),
            },
            follow_redirects=False,
        )
        self.assertEqual(posted.status_code, 303)
        page = self.client.get("/hosts")
        self.assertEqual(page.status_code, 200)
        return self._csrf(page.text)

    def test_chat_page_has_project_picker(self):
        html = Path(__file__).resolve().parents[1].joinpath("templates", "chat.html").read_text()
        self.assertIn("data-chat-new", html)
        self.assertIn("data-chat-context", html)
        self.assertIn("data-context-alert", html)
        self.assertIn("data-project-picker", html)
        self.assertIn("data-project-picker-host", html)
        self.assertIn('data-history-tab="projects"', html)
        js = Path(__file__).resolve().parents[1].joinpath("static", "chat.js").read_text()
        self.assertIn("openProjectPicker", js)
        self.assertIn("runCompact", js)
        self.assertIn('name: "compact"', js)
        self.assertIn("data-project-picker-add", js)
        self.assertIn("startProjectThread(hostId, projectId", js)
        self.assertIn("pendingProject", js)
        self.assertIn("bindCreatePayload", js)
        start = js.index("async function startProjectThread")
        end = js.index("async function createPickerProject")
        self.assertNotIn('/chat/threads', js[start:end])
        self.assertIn('historyTab === "projects"', js)

    def test_create_project_and_thread_from_chat_apis(self):
        csrf = self._login()
        added = self.client.post(
            "/hosts",
            headers={"x-csrf-token": csrf},
            json={
                "alias": "box",
                "hostname": "example.com",
                "username": "u",
                "auth_type": "key",
                "secret": "k",
                "default_cwd": "/srv",
            },
        )
        self.assertEqual(added.status_code, 201)
        host_id = added.json()["id"]
        bundle = self.client.get("/hosts/api")
        self.assertEqual(bundle.status_code, 200)
        self.assertEqual(bundle.json()["hosts"][0]["projects"], [])
        created = self.client.post(
            f"/hosts/{host_id}/projects",
            headers={"x-csrf-token": csrf},
            json={"name": "api", "remote_path": "/srv/api"},
        )
        self.assertEqual(created.status_code, 201)
        project_id = created.json()["id"]
        thread = self.client.post(
            "/chat/threads",
            headers={"x-csrf-token": csrf},
            json={
                "kind": "project",
                "host_id": host_id,
                "project_id": project_id,
                "command_id": "new-project-thread",
            },
        )
        self.assertEqual(thread.status_code, 201, thread.text)
        body = thread.json()
        self.assertEqual(body["kind"], "project")
        self.assertEqual(body["host_id"], host_id)
        self.assertEqual(body["project_id"], project_id)
        self.assertEqual(body["project_name"], "api")
        listed = self.client.get("/chat/threads")
        self.assertEqual(listed.status_code, 200)
        projects = listed.json()["projects"]
        self.assertEqual(len(projects), 1)
        self.assertEqual(projects[0]["name"], "api")
        self.assertEqual(projects[0]["thread_id"], body["id"])
        again = self.client.post(
            "/chat/threads",
            headers={"x-csrf-token": csrf},
            json={
                "kind": "project",
                "host_id": host_id,
                "project_id": project_id,
                "command_id": "new-project-thread-2",
            },
        )
        self.assertEqual(again.status_code, 201, again.text)
        self.assertNotEqual(again.json()["id"], body["id"])
        self.assertEqual(again.json()["messages"], [])

    def test_new_project_thread_without_existing_session(self):
        csrf = self._login()
        added = self.client.post(
            "/hosts",
            headers={"x-csrf-token": csrf},
            json={
                "alias": "box",
                "hostname": "example.com",
                "username": "u",
                "auth_type": "key",
                "secret": "k",
            },
        )
        self.assertEqual(added.status_code, 201)
        host_id = added.json()["id"]
        created = self.client.post(
            f"/hosts/{host_id}/projects",
            headers={"x-csrf-token": csrf},
            json={"name": "api", "remote_path": "/srv/api"},
        )
        self.assertEqual(created.status_code, 201)
        project_id = created.json()["id"]
        fresh = self.client.post(
            "/chat/threads",
            headers={"x-csrf-token": csrf},
            json={
                "kind": "project",
                "host_id": host_id,
                "project_id": project_id,
                "title": "api",
                "command_id": "open-empty",
            },
        )
        self.assertEqual(fresh.status_code, 201, fresh.text)
        listed = self.client.get("/chat/threads")
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(
            [t["id"] for t in listed.json()["threads"]],
            [fresh.json()["id"]],
        )
        self.assertEqual(fresh.json()["kind"], "project")


if __name__ == "__main__":
    unittest.main()
