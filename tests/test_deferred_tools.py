"""Deferred catalog: coding vs flag-off, packs, extra cap, prompt size."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
_TMP = tempfile.mkdtemp(prefix="fuse-defer-")
os.environ.setdefault("DATABASE_PATH", str(Path(_TMP) / "defer.db"))
os.environ.setdefault("APP_SECRET", "x" * 32)

from app import agent_catalog
from app.chat_runtime import _session_tail_block, build_system_prompt
from app.chat_tools import execute_tool, openai_tools


class OpenaiToolsLockTest(unittest.TestCase):
    def test_flag_off_still_injects_artifacts(self):
        names = [s["function"]["name"] for s in openai_tools([])]
        self.assertEqual(names, ["create_artifact", "update_artifact"])
        names = [s["function"]["name"] for s in openai_tools(["web_search"])]
        self.assertEqual(names[:2], ["web_search", "read_url"])
        self.assertIn("create_artifact", names)
        self.assertIn("update_artifact", names)

    def test_coding_always_on_is_small(self):
        names = [
            s["function"]["name"]
            for s in openai_tools(["web_search"], coding=True, project_session=True)
        ]
        self.assertEqual(
            names,
            ["web_search", "read_url", "search_tools", "load_skill", "todo_write", "scratch"],
        )
        self.assertNotIn("create_artifact", names)
        self.assertNotIn("read_file", names)
        self.assertNotIn("run_terminal_command", names)

    def test_chat_gateway_keeps_artifacts_without_hosts(self):
        names = [s["function"]["name"] for s in openai_tools(["web_search"], coding=True)]
        self.assertIn("search_tools", names)
        self.assertIn("create_artifact", names)
        self.assertNotIn("read_file", names)
        self.assertNotIn("spawn_subagent", names)

    def test_coding_spawn_and_extra(self):
        names = [
            s["function"]["name"]
            for s in openai_tools(
                ["web_search"],
                coding=True,
                extra=["run_terminal_command", "create_artifact"],
                allow_spawn=True,
                project_session=True,
            )
        ]
        self.assertIn("spawn_subagent", names)
        self.assertIn("search_models", names)
        self.assertIn("run_terminal_command", names)
        self.assertIn("create_artifact", names)
        self.assertLessEqual(len(names), 17)

    def test_spawn_available_without_project_session(self):
        names = [
            s["function"]["name"]
            for s in openai_tools(["web_search"], coding=True, allow_spawn=True, project_session=False)
        ]
        self.assertIn("spawn_subagent", names)
        self.assertIn("get_subagent_output", names)
        self.assertIn("search_models", names)

    def test_extra_cap_preserves_advertised_packs(self):
        extra = list(agent_catalog.WORKSPACE_NAMES) + ["create_artifact", "update_artifact", "propose_skill"]
        self.assertEqual(len(extra), agent_catalog.MAX_EXTRA)
        overflow = {"type": "function", "function": {"name": "audit_extra", "parameters": {"type": "object", "properties": {}}}}
        with patch.dict(agent_catalog.SCHEMAS, {"audit_extra": overflow}):
            names = [s["function"]["name"] for s in openai_tools([], coding=True, extra=[*extra, "audit_extra"])]
        always = {"search_tools", "load_skill", "todo_write", "scratch"}
        extras = [n for n in names if n not in always]
        self.assertEqual(extras, extra)
        self.assertNotIn("audit_extra", names)


class SearchToolsTest(unittest.IsolatedAsyncioTestCase):
    async def test_empty_query_returns_index_not_schemas(self):
        packed, trace = await execute_tool(
            "search_tools",
            "{}",
            user_id=1,
            allowed_urls=set(),
            coding=True,
            loaded=[],
        )
        data = json.loads(packed)
        self.assertIn("index", data)
        self.assertNotIn("tools", data)
        self.assertEqual(trace["preview"], "index")

    async def test_workspace_pack_loads_and_caps(self):
        loaded: list[str] = []
        packed, trace = await execute_tool(
            "search_tools",
            json.dumps({"query": "workspace"}),
            user_id=1,
            allowed_urls=set(),
            coding=True,
            loaded=loaded,
        )
        data = json.loads(packed)
        self.assertEqual(data["loaded"], list(agent_catalog.PACKS["workspace"]))
        self.assertEqual(data["already_loaded"], [])
        self.assertNotIn("tools", data)  # Schemas belong in the next request, not duplicated in results.
        self.assertIn("run_terminal_command", loaded)
        self.assertIn("read_file", loaded)
        self.assertIn("kill_command", loaded)
        self.assertNotIn("create_artifact", loaded)
        self.assertEqual(len(loaded), len(agent_catalog.PACKS["workspace"]))
        self.assertLessEqual(len(loaded), agent_catalog.MAX_EXTRA)
        self.assertEqual(trace["status"], "done")
        advertised = {s["function"]["name"]: s for s in openai_tools([], coding=True, extra=loaded, project_session=True)}
        for name in loaded:
            self.assertEqual(advertised[name], agent_catalog.schema(name))
        again, _ = await execute_tool("search_tools", '{"query":"workspace"}', user_id=1,
                                      allowed_urls=set(), coding=True, loaded=loaded)
        self.assertEqual(json.loads(again)["loaded"], [])
        self.assertEqual(json.loads(again)["already_loaded"], list(agent_catalog.PACKS["workspace"]))
        self.assertEqual(loaded, list(agent_catalog.PACKS["workspace"]))

    async def test_artifact_pack(self):
        loaded: list[str] = []
        packed, trace = await execute_tool(
            "search_tools",
            json.dumps({"query": "artifact"}),
            user_id=1,
            allowed_urls=set(),
            coding=True,
            loaded=loaded,
        )
        data = json.loads(packed)
        self.assertEqual(data["loaded"], ["create_artifact", "update_artifact"])
        self.assertEqual(data["already_loaded"], [])
        self.assertEqual(trace["status"], "done")
        self.assertEqual(loaded, ["create_artifact", "update_artifact"])
        advertised = {s["function"]["name"]: s for s in openai_tools([], coding=True, extra=loaded, project_session=True)}
        for name in loaded:
            self.assertEqual(advertised[name], agent_catalog.schema(name))

    async def test_extra_beyond_current_cap_refused_without_eviction(self):
        loaded = list(agent_catalog.PACKS["workspace"]) + ["create_artifact", "update_artifact", "propose_skill"]
        self.assertEqual(len(loaded), agent_catalog.MAX_EXTRA)
        before = list(loaded)
        # Exercise an actual overflow even though all currently advertised
        # packs now fit. A future catalog extension must not evict loaded tools.
        overflow = {"type": "function", "function": {"name": "audit_extra", "parameters": {"type": "object", "properties": {}}}}
        with patch.dict(agent_catalog.SCHEMAS, {"audit_extra": overflow}):
            packed, trace = await execute_tool(
                "search_tools", json.dumps({"names": ["audit_extra"]}),
                user_id=1, allowed_urls=set(), coding=True, loaded=loaded,
            )
        self.assertIn("cap", json.loads(packed).get("note", "").lower() + (trace.get("error") or "").lower())
        self.assertEqual(json.loads(packed)["loaded"], [])
        self.assertEqual(loaded, before)
        self.assertNotIn("audit_extra", loaded)
        self.assertEqual(trace["status"], "error")

    async def test_explore_pack_filter_names_dropped_tools(self):
        # An explore child asking for the workspace pack must hear which tools
        # were filtered and why, instead of a silent loaded:[] that reads as
        # "nothing extra exists" and pushes edits onto the shell.
        loaded: list[str] = []
        packed, trace = await execute_tool(
            "search_tools",
            json.dumps({"query": "workspace"}),
            user_id=1,
            allowed_urls=set(),
            coding=True,
            loaded=loaded,
            session={"subagent_type": "explore", "depth": 1, "parent_run_id": "p1"},
        )
        data = json.loads(packed)
        self.assertIn("read_file", data["loaded"])
        self.assertIn("run_terminal_command", data["loaded"])
        self.assertNotIn("write_file", data["loaded"])
        note = data.get("note", "") + (trace.get("error") or "")
        self.assertIn("read-only", note)
        self.assertIn("write_file", note)
        self.assertIn("search_replace", note)
        self.assertIn("run_terminal_command", note)
        self.assertNotIn("write_file", loaded)

    async def test_explore_write_names_refused_with_note(self):
        loaded: list[str] = []
        packed, trace = await execute_tool(
            "search_tools",
            json.dumps({"names": ["write_file", "search_replace"]}),
            user_id=1,
            allowed_urls=set(),
            coding=True,
            loaded=loaded,
            session={"subagent_type": "explore", "depth": 1, "parent_run_id": "p1"},
        )
        data = json.loads(packed)
        self.assertEqual(data["loaded"], [])
        self.assertEqual(trace["status"], "error")
        self.assertEqual(trace["kind"], "refused")
        self.assertIn("read-only", trace["error"])
        self.assertIn("write_file", trace["error"])
        self.assertEqual(loaded, [])

    async def test_general_purpose_load_is_unrestricted(self):
        loaded: list[str] = []
        packed, trace = await execute_tool(
            "search_tools",
            json.dumps({"query": "workspace"}),
            user_id=1,
            allowed_urls=set(),
            coding=True,
            loaded=loaded,
            session={"subagent_type": "general-purpose", "depth": 1, "parent_run_id": "p1"},
        )
        data = json.loads(packed)
        self.assertEqual(data["loaded"], list(agent_catalog.PACKS["workspace"]))
        self.assertEqual(trace["status"], "done")
        self.assertNotIn("read-only", data.get("note", ""))

    async def test_hosts_query(self):
        with patch("app.hosts.catalog_for_prompt", return_value="- box  u@h:22") as catalog:
            packed, trace = await execute_tool(
                "search_tools",
                json.dumps({"query": "hosts"}),
                user_id=1,
                allowed_urls=set(),
                coding=True,
                loaded=[],
                bound_host_id=9,
                bound_cwd="/srv/web",
                session={"depth": 1, "parent_run_id": "run_parent"},
            )
        self.assertIn("box", packed)
        self.assertEqual(trace["preview"], "hosts")
        catalog.assert_called_once_with(
            1, clip=False, bound_host_id=9, bound_cwd="/srv/web", only_bound=True
        )

    async def test_nested_spawn_denied_when_session_disallows(self):
        text, trace = await execute_tool(
            "spawn_subagent",
            json.dumps({"prompt": "look around", "description": "explore"}),
            user_id=1,
            allowed_urls=set(),
            coding=True,
            parent_run_id="parent-1",
            http_request=object(),
            user={"id": 1},
            session={"allow_spawn": False},
        )
        self.assertEqual(trace["status"], "error")
        self.assertEqual(trace["kind"], "denied")
        self.assertIn("Nested subagents are off", text)

    async def test_workspace_refused_until_loaded(self):
        text, trace = await execute_tool(
            "run_terminal_command",
            json.dumps({"command": "uptime", "host": "box"}),
            user_id=1,
            allowed_urls=set(),
            coding=True,
            loaded=[],
        )
        self.assertEqual(trace["status"], "error")
        self.assertIn("search_tools", text)

    async def test_workspace_refused_when_not_coding(self):
        text, trace = await execute_tool(
            "read_file",
            json.dumps({"path": "/tmp/x"}),
            user_id=1,
            allowed_urls=set(),
            coding=False,
            loaded=["read_file"],
        )
        self.assertIn("Unknown tool", text)
        self.assertIn("coding tool", text)
        self.assertEqual(trace["status"], "error")
        self.assertEqual(trace["kind"], "unknown_tool")

    async def test_search_tools_off_when_not_coding(self):
        text, trace = await execute_tool(
            "search_tools",
            json.dumps({"query": "workspace"}),
            user_id=1,
            allowed_urls=set(),
            coding=False,
            loaded=[],
        )
        self.assertIn("Unknown tool", text)
        self.assertEqual(trace["status"], "error")


class CodingPromptTest(unittest.TestCase):
    def test_coding_skips_artifact_playbook_and_shortens_search(self):
        text = build_system_prompt(
            instructions="",
            profile="",
            thread_context="",
            tools=["web_search"],
            coding=True,
            project_session=True,
            host_catalog="",
        )
        self.assertIn("search_tools", text)
        self.assertIn("No connected hosts", text)
        self.assertIn("File and shell tools are unavailable until the user adds a host", text)
        self.assertNotIn("cdn.jsdelivr.net", text)
        self.assertNotIn("LIVE QUOTE", text)
        self.assertIn("Web search is on", text)
        self.assertIn("No skills enabled", text)
        self.assertIn("skill bodies and supporting files from `load_skill`", text)

    def test_chat_gateway_lists_hosts_without_binding(self):
        text = build_system_prompt(
            instructions="",
            profile="",
            thread_context="keep these notes",
            tools=["web_search"],
            coding=True,
            project_session=False,
            host_catalog="- prod  deploy@a.example:22\n- laptop  me@b.example:22",
        )
        self.assertIn("keep these notes", text)
        self.assertIn("regular chat", text)
        self.assertIn("not a project session", text)
        self.assertIn("prod", text)
        self.assertIn("laptop", text)
        self.assertIn("Pass host=<alias> on every file or shell call", text)
        self.assertIn("work only on hosts the user names", text)
        self.assertIn("run the same check on every host in parallel", text)
        self.assertIn("Working in a specific project folder binds this chat to that project", text)
        self.assertIn("`roots:`", text)
        self.assertIn("/tmp/fuse", text)
        names = [s["function"]["name"] for s in openai_tools(["web_search"], coding=True, project_session=False)]
        self.assertIn("create_artifact", names)
        self.assertNotIn("continues it as a project session", text)

    def test_coding_with_hosts_explains_unbound_parallel(self):
        text = build_system_prompt(
            instructions="",
            profile="",
            thread_context="",
            tools=[],
            coding=True,
            project_session=True,
            host_catalog="- prod  deploy@a.example:22\n- laptop  me@b.example:22",
            bound_label="",
        )
        self.assertIn("prod", text)
        self.assertIn("once per alias", text)
        self.assertNotIn("cdn.jsdelivr.net", text)
        self.assertNotIn("(none)", text)

    def test_bound_project_and_vault_locked(self):
        text = build_system_prompt(
            instructions="",
            profile="",
            thread_context="",
            tools=[],
            coding=True,
            project_session=True,
            host_catalog="- prod  deploy@a.example:22",
            bound_label="prod cwd=/srv/api",
        )
        self.assertIn("bound to prod", text)
        # Lock state is per-turn volatile: it rides the session tail so a vault
        # flip never rewrites the cached system prompt.
        self.assertNotIn("Vault is locked", text)
        self.assertIn("Vault is locked", _session_tail_block([], vault_locked=True))
        self.assertNotIn("Vault is locked", _session_tail_block([], vault_locked=False))

    def test_flag_off_schemas_carry_artifact_policy_without_coding_prompt(self):
        text = build_system_prompt(instructions="", profile="", thread_context="", tools=[])
        schemas = {s["function"]["name"]: s["function"] for s in openai_tools([])}
        self.assertEqual(set(schemas), {"create_artifact", "update_artifact"})
        self.assertIn("Default visibility is private", schemas["create_artifact"]["description"])
        self.assertIn("Use public only when the user asked", schemas["create_artifact"]["description"])
        self.assertIn("Never put secrets", schemas["create_artifact"]["description"])
        self.assertIn("Publish only when the user asked", schemas["update_artifact"]["description"])
        self.assertNotIn("search_tools", text)
        self.assertNotIn("skill bodies and supporting files", text)
        self.assertNotIn("No skills enabled", text)

    def test_subagent_prompt_requires_final_report(self):
        text = build_system_prompt(
            instructions="",
            profile="",
            thread_context="",
            tools=["web_search"],
            coding=True,
            project_session=True,
            loaded_tools=["read_file", "run_terminal_command"],
            subagent=True,
            subagent_type="general-purpose",
        )
        self.assertIn("subagent", text.lower())
        self.assertIn("final", text.lower())
        self.assertNotIn("You are a chat assistant inside Fuse", text)

    def test_parent_spawn_prompt_matches_opencode_contract(self):
        text = build_system_prompt(
            instructions="",
            profile="",
            thread_context="",
            tools=["web_search"],
            coding=True,
            project_session=True,
            spawn_tools=True,
        )
        self.assertIn("task_id", text)
        self.assertIn("task_result", text)
        self.assertIn("do not sleep or poll", text.lower())
        self.assertIn("search_models", text)
        self.assertIn("clone this agent", text)

    def test_coding_prefers_file_tools_over_shell(self):
        for kwargs in (
            {"project_session": True, "loaded_tools": None},
            {"project_session": True, "loaded_tools": ["run_terminal_command"]},
            {"project_session": False},
        ):
            text = build_system_prompt(
                instructions="",
                profile="",
                thread_context="",
                tools=["web_search"],
                coding=True,
                **kwargs,
            )
            for name in ("list_dir", "grep", "glob", "read_file", "search_replace", "write_file"):
                self.assertIn(f"`{name}`", text)
            self.assertIn("not shell with ls/find/rg/grep/cat/sed", text)
            self.assertIn("Reserve the shell for git, tests, builds", text)
            self.assertIn("Prefer `search_replace` for targeted edits", text)
            self.assertIn("`monitor`", text)
            self.assertIn("never sleep or poll", text)
            self.assertIn("<monitor_event>", text)
            # Waiting on a condition is a monitor on a marker line, never sleep.
            self.assertIn("echo READY` with pattern `^READY$`", text)
            # A process-name check matches the watcher's own `bash -c` line.
            self.assertIn("Process-name checks match themselves", text)
            self.assertIn("pgrep -f '[c]url.*Q6_K'", text)
            self.assertIn("echo $! > /tmp/fuse/job.pid", text)
            self.assertIn("with pattern `^DONE`", text)
            self.assertIn("run the check once by hand", text)
            # A job you start reports its own exit; a bare `cmd &` dies of SIGPIPE.
            self.assertIn("`<command_result>` brings its exit code", text)
            self.assertIn("dies of SIGPIPE", text)
            # Job output must not reach the status file (curl's meter fires it at once).
            self.assertIn('echo "exit=$?" > /tmp/fuse/job.status', text)
            self.assertIn("Silence is not success", text)
            self.assertIn("the event wakes you", text)
            if kwargs.get("loaded_tools"):
                self.assertIn("Loaded tools: run_terminal_command", text)
                self.assertIn("Call them directly without `search_tools`", text)
            else:
                self.assertIn("`search_tools` call (query `workspace`", text)
        explore = build_system_prompt(
            instructions="",
            profile="",
            thread_context="",
            tools=["web_search"],
            coding=True,
            subagent=True,
            subagent_type="explore",
        )
        self.assertIn("Inspect with list_dir/grep/glob/read_file", explore)
        self.assertIn("shell only for non-mutating commands those tools cannot run", explore)
        self.assertIn("Do not edit files or run mutating commands", explore)
        # Explore runs shell and must wait correctly, but cannot load monitor
        # (agent_catalog.EXPLORE_TOOLS), so it is not offered one.
        self.assertIn("never sleep or poll", explore)
        self.assertNotIn("`monitor`", explore)
        self.assertNotIn("echo READY", explore)
        # Explore still runs foreground waits, so it gets the self-match rule.
        self.assertIn("Process-name checks match themselves", explore)
        plan = build_system_prompt(
            instructions="", profile="", thread_context="", tools=["web_search"],
            coding=True, subagent=True, subagent_type="plan",
        )
        self.assertNotIn("`monitor`", plan)
        from app.agent_catalog import SCHEMAS

        monitor = SCHEMAS["monitor"]["function"]["description"]
        self.assertIn("echo READY`, pattern ^READY$", monitor)
        self.assertIn("the watcher's own shell matches it", monitor)
        self.assertIn("pgrep -f '[c]url'", monitor)
        self.assertIn("kill -0 <pid>", monitor)
        self.assertIn("Run the check once by hand right after arming", monitor)
        self.assertIn("Silence is not success", monitor)
        self.assertIn("its <command_result> carries the exit code", monitor)
        self.assertIn("(8 within 2 s) is auto-stopped", monitor)
        self.assertNotIn("echo OK || echo FAIL) > /tmp/fuse/job.status 2>&1", monitor)
        self.assertIn("state=\"completed\" is the normal end", monitor)
        self.assertIn("find out why before re-arming", monitor)
        self.assertNotIn("reached max_events`", monitor)
        chat = build_system_prompt(instructions="", profile="", thread_context="", tools=["web_search"])
        self.assertNotIn("not shell with ls/find/rg/grep/cat/sed", chat)


class ReadOnlyChildPromptTest(unittest.TestCase):
    def test_read_only_children_get_no_editing_shell_or_loading_promises(self):
        from app import agent_catalog

        common = dict(instructions="", profile="", thread_context="", tools=["web_search"], coding=True,
                      project_session=True, bound_label="box")
        for kind in ("explore", "plan"):
            text = build_system_prompt(**common, subagent=True, subagent_type=kind,
                                       loaded_tools=agent_catalog.default_loaded_for_child(kind))
            self.assertNotIn("Before editing in a subdirectory", text)
            self.assertNotIn("still loads other packs", text)
            self.assertIn("cannot add tools beyond your role's", text)
            # Only plan has no shell: no cwd or throwaway-script guidance.
            self.assertEqual("To run a command elsewhere" in text, kind == "explore")
            self.assertEqual("Throwaway scripts" in text, kind == "explore")
        main = build_system_prompt(**common, loaded_tools=["read_file"])
        self.assertIn("Before editing in a subdirectory", main)
        self.assertIn("To run a command elsewhere", main)
        self.assertIn("still loads other packs", main)


class SuggestToolTest(unittest.TestCase):
    def test_synonyms(self):
        from app.chat_tools import suggest_tool

        cases = {
            "shell": "run_terminal_command",
            "run_command": "run_terminal_command",
            "websearch": "web_search",
            "fetch_url": "read_url",
            "edit_file": "search_replace",
            "find_files": "glob",
            "make_artifact": "create_artifact",
            "todos": "todo_write",
        }
        for bad, want in cases.items():
            self.assertEqual(suggest_tool(bad, coding=True), want, bad)

    def test_name_normalization(self):
        from app.chat_tools import suggest_tool

        self.assertEqual(suggest_tool("Shell", coding=True), "run_terminal_command")
        self.assertEqual(suggest_tool("functions.shell", coding=True), "run_terminal_command")
        self.assertEqual(suggest_tool("run-terminal-command", coding=True), "run_terminal_command")
        self.assertEqual(suggest_tool("web_search_0", coding=True), "web_search")

    def test_argument_shape_fallback(self):
        from app.chat_tools import suggest_tool

        # The exact gpt-oss failure: invented name, real tool's arg names.
        args = json.dumps({"cmd": ["bash", "-lc", "uptime"], "host": "box", "timeout": 120000})
        self.assertEqual(suggest_tool("do_thing", args, coding=True), "run_terminal_command")
        self.assertEqual(suggest_tool("whatever", json.dumps({"url": "https://x", "find": "y"}), coding=True), "read_url")
        # Single overlapping generic key is not enough.
        self.assertIsNone(suggest_tool("mystery", json.dumps({"query": "x"}), coding=True))

    def test_non_coding_universe_is_base_tools_only(self):
        from app.chat_tools import suggest_tool

        self.assertIsNone(suggest_tool("shell", json.dumps({"host": "a", "timeout": 5}), coding=False))
        self.assertEqual(suggest_tool("websearch", coding=False), "web_search")

    def test_no_confident_guess(self):
        from app.chat_tools import suggest_tool

        self.assertIsNone(suggest_tool("", coding=True))
        self.assertIsNone(suggest_tool("zzzzqqq", "{}", coding=True))


class UnknownToolReplyTest(unittest.IsolatedAsyncioTestCase):
    async def test_coding_suggests_and_names_the_load_call(self):
        text, trace = await execute_tool(
            "shell",
            json.dumps({"cmd": ["bash", "-lc", "uptime"], "host": "box", "timeout": 120000}),
            user_id=1,
            allowed_urls=set(),
            coding=True,
            loaded=[],
        )
        self.assertIn("Did you mean 'run_terminal_command'", text)
        self.assertIn("search_tools", text)
        self.assertIn('{"names": ["run_terminal_command"]}', text)
        self.assertIn("command (string required)", text)
        self.assertEqual(trace["kind"], "unknown_tool")
        self.assertEqual(trace["suggestion"], "run_terminal_command")
        self.assertEqual(trace["status"], "error")

    async def test_coding_loaded_tool_skips_load_hint(self):
        text, trace = await execute_tool(
            "shell",
            json.dumps({"cmd": "uptime", "host": "a", "timeout": 5}),
            user_id=1,
            allowed_urls=set(),
            coding=True,
            loaded=["run_terminal_command"],
        )
        self.assertIn("Did you mean 'run_terminal_command'", text)
        self.assertNotIn("not loaded yet", text)

    async def test_non_coding_names_the_base_tools(self):
        text, trace = await execute_tool(
            "shell",
            json.dumps({"cmd": "uptime", "host": "a"}),
            user_id=1,
            allowed_urls=set(),
            coding=False,
            loaded=[],
        )
        self.assertIn("Unknown tool 'shell'", text)
        self.assertIn("web_search, read_url, create_artifact, update_artifact", text)
        self.assertNotIn("run_terminal_command", text)
        self.assertEqual(trace["kind"], "unknown_tool")

    async def test_non_coding_real_coding_tool_is_named(self):
        text, trace = await execute_tool(
            "run_terminal_command",
            json.dumps({"command": "uptime"}),
            user_id=1,
            allowed_urls=set(),
            coding=False,
            loaded=[],
        )
        self.assertIn("'run_terminal_command' is a coding tool", text)
        self.assertEqual(trace["kind"], "unknown_tool")

    async def test_coding_no_guess_points_at_catalog(self):
        text, trace = await execute_tool(
            "zzzzqqq",
            "{}",
            user_id=1,
            allowed_urls=set(),
            coding=True,
            loaded=[],
        )
        self.assertIn("query 'index'", text)
        self.assertEqual(trace["kind"], "unknown_tool")
        self.assertNotIn("suggestion", trace)


class NotLoadedReplyTest(unittest.IsolatedAsyncioTestCase):
    async def test_names_the_tool_and_pack(self):
        text, trace = await execute_tool(
            "grep",
            json.dumps({"pattern": "x"}),
            user_id=1,
            allowed_urls=set(),
            coding=True,
            loaded=[],
        )
        self.assertIn("Tool 'grep' is not loaded", text)
        self.assertIn('{"names": ["grep"]}', text)
        self.assertIn("workspace", text)
        self.assertEqual(trace["kind"], "tool_not_loaded")
        self.assertEqual(trace["suggestion"], "grep")

    async def test_shell_pack_hint(self):
        text, _ = await execute_tool(
            "run_terminal_command",
            json.dumps({"command": "uptime"}),
            user_id=1,
            allowed_urls=set(),
            coding=True,
            loaded=[],
        )
        self.assertIn("query 'shell'", text)

    async def test_artifact_pack_hint(self):
        text, _ = await execute_tool(
            "create_artifact",
            json.dumps({"title": "t", "html": "<p>x</p>"}),
            user_id=1,
            allowed_urls=set(),
            coding=True,
            loaded=[],
        )
        self.assertIn("query 'artifact'", text)
        self.assertIn('{"names": ["create_artifact"]}', text)


class SearchModelsTest(unittest.IsolatedAsyncioTestCase):
    CATALOG = [
        {"id": "grok-4.6", "name": "Grok 4.6", "type": "grok"},
        {"id": "grok-4.5", "name": "Grok 4.5", "type": "grok"},
        {"id": "gpt-5.4", "name": "GPT 5.4", "type": "chatgpt"},
        {"id": "openrouter/x-ai/grok-4.6", "name": "Grok 4.6", "type": "openrouter"},
        {"id": "glm-5.3-flash", "name": "GLM 5.3 Flash", "type": "zai"},
        {"id": "openrouter/z-ai/glm-5.3-flash", "name": "GLM 5.3 Flash", "type": "openrouter"},
    ]

    async def test_search_models_off_when_not_coding(self):
        text, trace = await execute_tool(
            "search_models",
            json.dumps({"query": "grok"}),
            user_id=1,
            allowed_urls=set(),
            coding=False,
        )
        self.assertEqual(trace["status"], "error")
        self.assertIn("Unknown tool", text)

    async def test_empty_query_lists_providers(self):
        with patch("app.opencode.model_options", new=AsyncMock(return_value=self.CATALOG)):
            packed, trace = await execute_tool(
                "search_models",
                "{}",
                user_id=1,
                allowed_urls=set(),
                coding=True,
            )
        data = json.loads(packed)
        self.assertEqual(trace["preview"], "providers")
        ids = {row["id"] for row in data["providers"]}
        self.assertEqual(ids, {"chatgpt", "grok", "openrouter", "zai"})

    async def test_grok_query_lists_all_providers(self):
        with patch("app.opencode.model_options", new=AsyncMock(return_value=self.CATALOG)):
            packed, trace = await execute_tool(
                "search_models",
                json.dumps({"query": "grok"}),
                user_id=1,
                allowed_urls=set(),
                coding=True,
            )
        data = json.loads(packed)
        ids = [row["id"] for row in data["models"]]
        self.assertIn("grok-4.6", ids)
        self.assertIn("openrouter/x-ai/grok-4.6", ids)
        self.assertIn("conflict", data)
        self.assertEqual(trace["kind"], "conflict")

    async def test_exact_slug_spawns_that_model(self):
        from app import agents as agents_mod

        with patch("app.opencode.model_options", new=AsyncMock(return_value=self.CATALOG)):
            model, err = await agents_mod.resolve_spawn_model(user_id=1, slug="grok-4.6", parent_model="gpt-5.4")
        self.assertEqual(model, "grok-4.6")
        self.assertEqual(err, "")

    async def test_omitted_model_clones_parent(self):
        from app import agents as agents_mod

        model, err = await agents_mod.resolve_spawn_model(user_id=1, slug="", parent_model="gpt-5.4")
        self.assertEqual(model, "gpt-5.4")
        self.assertEqual(err, "")

    async def test_ambiguous_slug_asks(self):
        from app import agents as agents_mod

        with patch("app.opencode.model_options", new=AsyncMock(return_value=self.CATALOG)):
            model, err = await agents_mod.resolve_spawn_model(user_id=1, slug="grok", parent_model="gpt-5.4")
        self.assertEqual(model, "")
        self.assertIn("Ambiguous", err)
        self.assertIn("Ask the user", err)

    async def test_unique_family_resolves(self):
        from app import agents as agents_mod

        with patch("app.opencode.model_options", new=AsyncMock(return_value=self.CATALOG)):
            model, err = await agents_mod.resolve_spawn_model(user_id=1, slug="gpt-5.4", parent_model="grok-4.6")
        self.assertEqual(model, "gpt-5.4")
        self.assertEqual(err, "")

    async def test_glm_flash_matches_versioned_slug(self):
        with patch("app.opencode.model_options", new=AsyncMock(return_value=self.CATALOG)):
            packed, _ = await execute_tool(
                "search_models",
                json.dumps({"query": "glm flash"}),
                user_id=1,
                allowed_urls=set(),
                coding=True,
            )
        ids = [row["id"] for row in json.loads(packed)["models"]]
        self.assertIn("glm-5.3-flash", ids)
        self.assertIn("openrouter/z-ai/glm-5.3-flash", ids)

    async def test_glm_flash_with_version_at_end(self):
        with patch("app.opencode.model_options", new=AsyncMock(return_value=self.CATALOG)):
            packed, _ = await execute_tool(
                "search_models",
                json.dumps({"query": "glm flash 5.3"}),
                user_id=1,
                allowed_urls=set(),
                coding=True,
            )
        ids = [row["id"] for row in json.loads(packed)["models"]]
        self.assertIn("glm-5.3-flash", ids)
        self.assertNotIn("grok-4.6", ids)

    async def test_glm_flash_zai_query_matches_pool_and_openrouter(self):
        with patch("app.opencode.model_options", new=AsyncMock(return_value=self.CATALOG)):
            packed, _ = await execute_tool(
                "search_models",
                json.dumps({"query": "glm 5.3 flash z.ai"}),
                user_id=1,
                allowed_urls=set(),
                coding=True,
            )
        ids = [row["id"] for row in json.loads(packed)["models"]]
        self.assertIn("glm-5.3-flash", ids)
        self.assertIn("openrouter/z-ai/glm-5.3-flash", ids)
        self.assertNotIn("grok-4.6", ids)

    async def test_zai_provider_query_lists_glm(self):
        with patch("app.opencode.model_options", new=AsyncMock(return_value=self.CATALOG)):
            packed, _ = await execute_tool(
                "search_models",
                json.dumps({"query": "zai"}),
                user_id=1,
                allowed_urls=set(),
                coding=True,
            )
        ids = [row["id"] for row in json.loads(packed)["models"]]
        self.assertIn("glm-5.3-flash", ids)
        self.assertIn("openrouter/z-ai/glm-5.3-flash", ids)
        self.assertNotIn("grok-4.6", ids)

    async def test_zai_query_finds_openrouter_glm_without_pool_row(self):
        catalog = [
            {"id": "grok-4.6", "name": "Grok 4.6", "type": "grok"},
            {"id": "openrouter/z-ai/glm-5.3-flash", "name": "GLM 5.3 Flash", "type": "openrouter"},
            {"id": "devin/glm-5-3-flash", "name": "Devin GLM 5.3 Flash", "type": "devin"},
        ]
        with patch("app.opencode.model_options", new=AsyncMock(return_value=catalog)):
            packed, _ = await execute_tool(
                "search_models",
                json.dumps({"query": "zai"}),
                user_id=1,
                allowed_urls=set(),
                coding=True,
            )
        ids = [row["id"] for row in json.loads(packed)["models"]]
        self.assertEqual(ids, ["openrouter/z-ai/glm-5.3-flash"])

    async def test_glm_flash_zai_query_skips_devin_without_devin_token(self):
        catalog = [
            {"id": "glm-5.3-flash", "name": "GLM 5.3 Flash", "type": "zai"},
            {"id": "openrouter/z-ai/glm-5.3-flash", "name": "GLM 5.3 Flash", "type": "openrouter"},
            {"id": "devin/glm-5-3-flash", "name": "Devin GLM 5.3 Flash", "type": "devin"},
        ]
        with patch("app.opencode.model_options", new=AsyncMock(return_value=catalog)):
            packed, _ = await execute_tool(
                "search_models",
                json.dumps({"query": "glm 5.3 flash z.ai"}),
                user_id=1,
                allowed_urls=set(),
                coding=True,
            )
        ids = [row["id"] for row in json.loads(packed)["models"]]
        self.assertIn("glm-5.3-flash", ids)
        self.assertIn("openrouter/z-ai/glm-5.3-flash", ids)
        self.assertNotIn("devin/glm-5-3-flash", ids)


if __name__ == "__main__":
    unittest.main()
