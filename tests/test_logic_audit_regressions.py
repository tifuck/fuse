"""Logic-audit regressions (2026-10): wrong results that slipped past tests.

Pure functions with I/O mocked; no live database or providers.
"""
import asyncio
import json
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import quote
from unittest import mock
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app import api, command_jobs, cron, monitor, permissions, schedules, workspace
from app.chat_generation import parsing
from app.chat_run_state import resumed_after_stop
from app.context_compact import clip_to_fit
from app.context_meter import estimate_messages
from app.search import engine, extract, ssrf
from app.sources import devin_payload, ollama, pool_session
from app.sources.key_pool import KeyPool, NoAvailableKeyError
from app.workspace_ops.common import WorkspaceError
from app.workspace_ops.previews import _apply_replace


# -- permissions --------------------------------------------------------------

@pytest.mark.parametrize("args", [
    {"command": "cat /approved/a.txt /unapproved/b.txt"},
    {"command": "cat /unapproved/b.txt", "cwd": "/approved"},
])
def test_one_dir_grant_does_not_cover_other_shell_targets(args):
    gate = SimpleNamespace(thread_id=1, user_id=1, unattended=True, requester={})
    covers = lambda _u, _t, _h, kind, value, _w: value == "/approved" or value.startswith("/approved/")
    with patch.object(permissions, "bypass_enabled", return_value=False), \
            patch.object(permissions, "grant_covers", side_effect=covers), \
            patch.object(permissions, "auto_enabled", return_value=False):
        result = asyncio.run(permissions.review(
            tool="run_terminal_command", args=args, user_id=1,
            host_id=1, host_alias="test", root="/srv/app", windows=False, gate=gate,
        ))
    assert not result.allowed


def test_grant_still_covers_a_command_whose_targets_are_all_granted():
    gate = SimpleNamespace(thread_id=1, user_id=1, unattended=True, requester={})
    covers = lambda _u, _t, _h, kind, value, _w: value == "/approved" or value.startswith("/approved/")
    with patch.object(permissions, "bypass_enabled", return_value=False), \
            patch.object(permissions, "grant_covers", side_effect=covers), \
            patch.object(permissions, "auto_enabled", return_value=False):
        result = asyncio.run(permissions.review(
            tool="run_terminal_command", args={"command": "cat /approved/a.txt /approved/b.txt"}, user_id=1,
            host_id=1, host_alias="test", root="/srv/app", windows=False, gate=gate,
        ))
    assert result.allowed


# -- key pool -----------------------------------------------------------------

def test_resting_fallback_key_is_reused_not_refused():
    async def run():
        with tempfile.TemporaryDirectory() as tmp:
            pool = KeyPool(state_file=str(Path(tmp) / "state.json"))
            pool.load_from_list(["only"])
            ok = lambda r: httpx.Response(200, content=b'{"done":true}')
            async with httpx.AsyncClient(transport=httpx.MockTransport(ok)) as client:
                await pool.send_request(client, "https://ollama.com/api/chat", {"model": "m"})
                assert pool.states["only"].state == "resting"
                response, state, _ = await pool.send_request(client, "https://ollama.com/api/chat", {"model": "m"})
            return response.status_code, state.key

    assert asyncio.run(run()) == (200, "only")


def test_cooling_key_is_still_handed_back():
    async def run():
        with tempfile.TemporaryDirectory() as tmp:
            pool = KeyPool(state_file=str(Path(tmp) / "state.json"))
            pool.load_from_list(["only"])
            pool.states["only"].mark_error("rate_limit", 100)
            async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200))) as client:
                await pool.send_request(client, "https://ollama.com/api/chat", {"model": "m"})

    with pytest.raises(NoAvailableKeyError):
        asyncio.run(run())


# -- text tool-call parsing ---------------------------------------------------

def test_tool_call_quoted_inside_a_longer_fence_never_runs():
    content = (
        "Example:\n````markdown\n```xml\n"
        '<tool_call>{"name":"run_terminal_command","arguments":{"command":"rm -rf x"}}</tool_call>\n'
        "```\n````\nDone."
    )
    _, calls = parsing.extract_text_tool_calls(content, coding=True)
    assert calls == []
    _, live = parsing.extract_text_tool_calls(
        '<tool_call>{"name":"run_terminal_command","arguments":{"command":"ls"}}</tool_call>', coding=True)
    assert len(live) == 1


def test_nested_function_arguments_are_kept():
    content = '<tool_call>{"function":{"name":"read_file","arguments":{"path":"README.md"}}}</tool_call>'
    _, calls = parsing.extract_text_tool_calls(content, coding=True)
    assert json.loads(calls[0]["function"]["arguments"]) == {"path": "README.md"}


def test_quoted_xml_scalar_stays_a_string():
    assert parsing._xml_param_value('"123"') == "123"
    assert parsing._xml_param_value("'true'") == "true"
    assert parsing._xml_param_value("123") == 123
    assert parsing._xml_param_value('"[1, 2]"') == [1, 2]


# -- upstream translators -----------------------------------------------------

def test_ollama_rebound_native_index_gets_a_fresh_index():
    state: dict = {}
    out = []
    for path in "abc":
        call = {"function": {"index": 0, "name": "read_file", "arguments": {"path": path}}}
        out += ollama.to_openai_tools([call], include_index=True, state=state)
    assert [c["index"] for c in out] == [0, 1, 2]
    assert len({c["id"] for c in out}) == 3


def test_devin_keeps_client_stop_sequences():
    req = devin_payload.build_chat_request(
        {"messages": [{"role": "user", "content": "hi"}], "stop": ["END"]},
        token="t", user_jwt="j", model_uid="m", cascade_id="c",
    )
    patterns = req["configuration"]["stop_patterns"]
    assert "END" in patterns and set(devin_payload.DEFAULT_STOP) <= set(patterns)


def test_image_only_first_turn_keeps_the_session_id():
    image = [{"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}]
    first = {"messages": [{"role": "user", "content": image}]}
    later = {"messages": [*first["messages"], {"role": "assistant", "content": "ok"},
                          {"role": "user", "content": "and now?"}]}
    sid = lambda body: pool_session.fallback_session_id(user_id=1, model="m", body=body)
    assert sid(first) == sid(later)


# -- schedules and cron -------------------------------------------------------

def _schedule(**extra):
    base = {"name": "s", "prompt": "p", "model": "m", "target": "chat", "timezone": "UTC"}
    return {**base, **extra}


def test_custom_cron_patch_replaces_a_weekly_builder():
    weekly = schedules._normalize(1, _schedule(recur_kind="cron", builder="weekly", at_local="09:00", weekdays=1))
    assert weekly["cron_expr"] == "0 9 * * 1"
    custom = schedules._normalize(1, {"cron_expr": "0 10 * * *"}, existing=weekly)
    assert (custom["cron_expr"], custom["weekdays"], custom["at_local"]) == ("0 10 * * *", 0, "")


def test_daily_switch_clears_weekdays_so_a_rename_stays_daily():
    weekly = schedules._normalize(1, _schedule(recur_kind="cron", builder="weekly", at_local="09:00", weekdays=1))
    daily = schedules._normalize(1, {"builder": "daily", "at_local": "10:00"}, existing=weekly)
    assert (daily["cron_expr"], daily["weekdays"]) == ("0 10 * * *", 0)
    renamed = schedules._normalize(1, {"name": "renamed"}, existing=daily)
    assert renamed["cron_expr"] == "0 10 * * *"


def test_numeric_step_runs_to_the_field_maximum():
    assert cron.parse_cron("5/15 * * * *")[0] == {5, 20, 35, 50}
    assert cron.parse_cron("5 * * * *")[0] == {5}


def test_two_hour_fall_back_fires_the_repeated_slot():
    after = datetime(2026, 10, 25, 0, 10, tzinfo=timezone.utc)
    assert cron.next_fire("0 1 * * *", "Antarctica/Troll", after) == datetime(2026, 10, 25, 1, 0, tzinfo=timezone.utc)


# -- run state ----------------------------------------------------------------

def test_resume_in_the_stops_own_second_counts():
    stamp = "2026-10-13T12:00:00Z"
    assert resumed_after_stop({"user_resumed_at": stamp}, stamp)
    assert not resumed_after_stop({"user_resumed_at": "2026-10-13T11:59:59Z"}, stamp)


# -- workspace and jobs -------------------------------------------------------

def test_crlf_file_gets_crlf_in_a_multiline_replacement():
    assert _apply_replace("alpha\r\nbeta\r\n", "alpha", "one\ntwo", False) == ("one\r\ntwo\r\nbeta\r\n", 1)


def test_overlapping_matches_are_not_unique():
    with pytest.raises(WorkspaceError, match="not unique"):
        _apply_replace("aaa", "aa", "X", False)
    assert _apply_replace("aXa", "a", "b", True) == ("bXb", 2)


def test_monitor_pattern_keeps_its_spaces():
    assert monitor.compile_pattern(" READY ").search("NOTREADY") is None
    assert monitor.compile_pattern(" READY ").search("is READY now")
    assert monitor.compile_pattern("   ") is None


def test_output_starting_with_an_ellipsis_counts_as_an_event():
    rec = {"id": "j1", "status": "running", "max_events": 1}
    command_jobs._on_monitor_batch(rec, ["… ready"])
    assert rec["event_count"] == 1 and rec.get("_max_events_hit")
    rec = {"id": "j2", "status": "running", "max_events": 5}
    command_jobs._on_monitor_batch(rec, ["x", "… 3 more lines in this batch omitted"], 3)
    assert rec["event_count"] == 4


def test_cancel_during_insert_closes_the_job_row():
    async def run():
        started, release = threading.Event(), threading.Event()

        def slow_insert(**_row):
            started.set()
            release.wait(5)

        with patch.object(command_jobs, "ensure_schema"), patch.object(command_jobs, "_admit"), \
                patch.object(command_jobs, "_insert_job_row", side_effect=slow_insert), \
                patch.object(command_jobs, "_abandon_launch") as abandon:
            task = asyncio.create_task(command_jobs.launch_async(
                user_id=1, host_id=1, host="h", command="true", timeout_ms=1000, runner=None))
            await asyncio.to_thread(started.wait, 5)
            task.cancel()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
            return abandon.call_count

    assert asyncio.run(run()) == 1


def test_out_of_range_sftp_read_is_an_error():
    handle = MagicMock()
    handle.__aenter__.return_value = handle
    del handle.stat
    sftp = MagicMock()
    sftp.__aenter__.return_value = sftp
    sftp.open.return_value = handle

    async def run():
        with patch.object(workspace, "_is_agent", return_value=False), \
                patch.object(workspace, "_sftp", AsyncMock(return_value=sftp)), \
                patch.object(workspace, "_sftp_expand_home", AsyncMock(return_value="/srv/app/f.txt")), \
                patch.object(workspace, "_sftp_read_window", AsyncMock(return_value=(b"", False))):
            with pytest.raises(WorkspaceError, match="beyond the end"):
                await workspace._read(1, 1, "box", {"path": "/srv/app/f.txt", "offset": 99}, "/srv/app")

    asyncio.run(run())


# -- context ------------------------------------------------------------------

def test_cjk_message_is_clipped_to_the_budget():
    rows, clipped = clip_to_fit([{"role": "user", "content": "漢" * 10000}], limit=1024, reserve=0)
    assert clipped and estimate_messages(rows) <= 1024


# -- search -------------------------------------------------------------------

def test_search_cache_separates_exact_recency():
    assert engine._cache_key("q", "pw", "mix", 2) != engine._cache_key("q", "pw", "mix", 7)


def test_ddg_target_is_decoded_once():
    target = "https://example.com/a%2Fb?q=x%26y"
    assert ssrf.decode_ddg_url("https://duckduckgo.com/l/?uddg=" + quote(target, safe="")) == target


def test_find_matches_heading_text_and_survives_case_expansion():
    assert "Needle heading" in extract.select_passages("# Needle heading\n\nBody text.", find="Needle heading")
    text = "İ" * 1300 + "\n\nneedle HERE"
    assert "No literal match" not in extract.select_passages(text, find="needle")


def test_rowspan_keeps_later_cells_in_their_column():
    html = ("<table><tr><th>Region</th><th>Revenue</th></tr>"
            "<tr><td rowspan='2'>US</td><td>100</td></tr><tr><td>200</td></tr></table>")
    out = extract._html_extract(html, "https://example.com/")[1]
    assert "| US | 200 |" in out


# -- /v1 SSE accounting -------------------------------------------------------

class _Stream:
    def __init__(self, chunks):
        self._chunks = chunks

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._chunks:
            raise StopAsyncIteration
        return self._chunks.pop(0)

    async def aclose(self):
        pass


def test_usage_after_a_huge_frame_in_the_same_chunk_is_recorded():
    def sse(obj):
        return b"data: " + json.dumps(obj).encode() + b"\n\n"

    chunk = (sse({"choices": [{"delta": {"content": "x" * (api._LINE_CAP + 10)}}]})
             + sse({"choices": [], "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30}})
             + b"data: [DONE]\n\n")

    async def run():
        with mock.patch("app.api.db.record_usage") as rec:
            out = b"".join([c async for c in api._tap(_Stream([chunk]), 1, 1, None, "m")])
            return rec, out

    rec, out = asyncio.run(run())
    assert rec.call_args[0][4]["prompt_tokens"] == 10
    assert rec.call_args.kwargs["request_status"] == "success"
    assert out.count(b"[DONE]") == 1


# -- 2026-10-15 audit ---------------------------------------------------------

def _review(command, root="/srv/app"):
    gate = SimpleNamespace(thread_id=1, user_id=1, unattended=True, requester={})
    with patch.object(permissions, "bypass_enabled", return_value=False), \
            patch.object(permissions, "grant_covers", return_value=False), \
            patch.object(permissions, "auto_enabled", return_value=False):
        return asyncio.run(permissions.review(
            tool="run_terminal_command", args={"command": command}, user_id=1,
            host_id=1, host_alias="test", root=root, windows=False, gate=gate,
        ))


def test_long_option_value_is_judged_as_its_own_path():
    paths = permissions.extract_shell_paths("grep --file=/etc/passwd README.md", "/srv/app", False)
    assert ("/etc/passwd", False) in paths
    assert not _review("grep --file=/etc/passwd README.md").allowed
    assert _review("grep --file=src/p.txt README.md").allowed


def test_home_relative_root_holds_its_children():
    roots = permissions.compute_roots("~/work", False)
    assert permissions.scope_allows("src/main.py", "~/work", roots, False) == (True, "~/work/src/main.py")
    assert not permissions.scope_allows("~/workx/a", "~/work", roots, False)[0]
    assert not permissions.scope_allows("~/other", "~/work", roots, False)[0]
    assert permissions.under_work_root("~/work/src/main.py", "~/work", False)
    win = permissions.compute_roots("~\\work", True)
    assert permissions.scope_allows("src\\main.py", "~\\work", win, True)[0]


def test_tilde_user_is_not_the_connected_users_home():
    roots = permissions.compute_roots("", False)
    assert permissions.scope_allows("~/notes.txt", "", roots, False)[0]
    assert not permissions.scope_allows("~other/public.txt", "", roots, False)[0]


def test_mapped_cgnat_address_is_not_public():
    assert ssrf.is_blocked_host("::ffff:100.64.0.1")
    assert not ssrf.is_blocked_host("8.8.8.8")
    infos = [(None, None, None, None, ("::ffff:100.64.0.1", 443, 0, 0))]
    with patch.object(ssrf.socket, "getaddrinfo", return_value=infos):
        assert ssrf.public_ips("example.com") == []
    with patch.object(monitor.socket, "getaddrinfo", return_value=infos):
        assert not monitor._resolves_public("example.com", 443)


def test_block_scalar_indicator_may_carry_a_comment():
    from app import skill_parse

    doc = ("---\nname: demo\ndescription: > # folded\n  Does a\n  thing.\n"
           "disable-model-invocation: | # flag\n  false\n---\nBody\n")
    parsed = skill_parse.parse_skill_md(doc)
    assert parsed["description"] == "Does a thing."
    assert parsed["disable_model_invocation"] is False


def test_report_opening_with_server_overloaded_is_not_a_provider_error():
    from app.chat_generation.policy import _provider_error_content

    assert not _provider_error_content(
        "The server was overloaded yesterday, so I switched to a smaller batch size and reran the job. "
        "Here is the report of what I found in the logs."
    )
    for text in (
        "The server had an error while processing your request. Sorry about that!",
        "The engine is currently overloaded, please try again later",
        "The server is overloaded or not ready yet.",
        "The service is temporarily unable to process your request. Please try again later.",
    ):
        assert _provider_error_content(text), text


def test_daily_time_patch_recompiles_the_cron():
    daily = schedules._normalize(1, _schedule(recur_kind="cron", builder="daily", at_local="09:00"))
    existing = {**daily, "id": 7, "public_id": "s7"}
    conn = MagicMock()
    tx = MagicMock()
    tx.__enter__.return_value = conn
    with patch.object(schedules.limiter, "allow", return_value=True), \
            patch.object(schedules.db, "tx", return_value=tx), \
            patch.object(schedules.db, "now", return_value="2026-10-15T00:00:00Z"), \
            patch.object(schedules, "get_schedule", side_effect=[existing, existing]):
        schedules.patch_schedule(1, "s7", {"at_local": "10:00"})
    params = next(c.args[1] for c in conn.execute.call_args_list if c.args[0].lstrip().startswith("UPDATE"))
    assert (params[14], params[17]) == ("0 10 * * *", "10:00")


def test_rotation_keeps_cooling_keys_last():
    from app.sources import openai_compat

    for mode in ("every_request", "round_robin"):
        openai_compat.reset_cursors()
        src = openai_compat.OpenAICompatSource({"id": 991, "name": "t"}, {"rotation": mode}, [{"id": 1}, {"id": 2}])
        with patch.object(openai_compat, "cooling_down", return_value=False):
            src._next_creds()
        with patch.object(openai_compat, "cooling_down", side_effect=lambda cid: cid == 1):
            assert [c["id"] for c in src._next_creds()] == [2, 1]
    openai_compat.reset_cursors()


def test_backoff_never_overflows():
    assert KeyPool._escalate(None, 1.0, 120.0, 1025) == 120.0


def test_auto_rows_follow_their_reference_after_usage_is_priced():
    from app import pricing

    rows = [{"provider": "devin", "model": "gpt-5-6-sol-low", "origin": pricing.AUTO_ORIGIN}]
    with patch.object(pricing.db, "fetchall", side_effect=[[], rows]), \
            patch.object(pricing, "_listed_pool_models", return_value=set()), \
            patch.object(pricing, "_reference_index", return_value={}), \
            patch.object(pricing, "_pick_reference", return_value={"input_rate": 8}), \
            patch.object(pricing, "_same_price", return_value=False), \
            patch.object(pricing.db, "save_model_price") as save:
        assert pricing.auto_price() == ["devin/gpt-5-6-sol-low"]
    assert save.call_args.args[:2] == ("devin", "gpt-5-6-sol-low")


def test_auto_row_matching_a_new_default_becomes_the_default():
    from app import pricing

    target = (pricing.KIMI_PROVIDER, next(iter(pricing.DEFAULT_KIMI_PRICING)))
    row = lambda _sql, key: {"origin": pricing.AUTO_ORIGIN if key == target else "default"}
    with patch.object(pricing.db, "fetchone", side_effect=row), \
            patch.object(pricing, "_default_row_matches", return_value=True), \
            patch.object(pricing.db, "save_model_price") as save:
        assert pricing.ensure_default_prices() == ["/".join(target)]
    assert save.call_args.args[:2] == target and save.call_args.kwargs["origin"] == "default"


def test_summary_budget_counts_the_repeated_prior_handoff():
    from app.context_compact import COMPACT_PREAMBLE, config, summarize_payload

    history = [{"role": "user", "content": COMPACT_PREAMBLE + "漢" * 30_000}]
    history += [{"role": "user" if i % 2 else "assistant", "content": "x" * 5_000} for i in range(20)]
    body = summarize_payload(history, limit=64_000)
    assert estimate_messages(body) <= 64_000 - config().output_reserve


def test_clipping_continues_past_a_body_already_at_its_floor():
    rows = [{"role": "user", "content": "a" * 2_000}]
    rows += [{"role": "assistant" if i % 2 else "user", "content": "漢" * 900} for i in range(2)]
    out, clipped = clip_to_fit(rows, limit=2_000, reserve=0)
    assert clipped and estimate_messages(out) <= 2_000


def test_search_verbs_are_only_stripped_when_they_lead():
    assert engine.sanitize_query("Google Cloud pricing") == "Google Cloud pricing"
    assert engine.sanitize_query("Google vs Bing") == "Google vs Bing"
    assert engine.sanitize_query("linux find command") == "linux find command"
    assert engine.sanitize_query("can you google the weather in Paris") == "the weather in Paris"
    assert engine.sanitize_query("please find me cheap flights") == "cheap flights"


def test_ddg_result_without_a_snippet_keeps_none():
    from app.search.providers.ddg import parse_ddg_html

    link = '<a rel="nofollow" class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2F{0}.example%2F">{1}</a>'
    html = (link.format("alpha", "Alpha") + link.format("beta", "Beta")
            + '<a class="result__snippet" href="#">Beta won the contract.</a>')
    assert [(h.title, h.snippet) for h in parse_ddg_html(html)] == [("Alpha", ""), ("Beta", "Beta won the contract.")]


def test_reversed_ordered_list_counts_down():
    html = "<ol reversed><li>Bronze</li><li>Silver</li><li>Gold</li></ol>"
    assert "3. Bronze\n2. Silver\n1. Gold" in extract._html_extract(html, "https://example.com/")[1]
    html = "<ol reversed start='10'><li>a</li><li>b</li></ol>"
    assert "10. a\n9. b" in extract._html_extract(html, "https://example.com/")[1]


def test_claude_keeps_parallel_false_without_a_tool_choice():
    from app.sources.claude_payload import _tool_choice

    assert _tool_choice(None, False) == {"type": "auto", "disable_parallel_tool_use": True}
    assert _tool_choice(None, None) is None


def test_monitor_batch_stops_at_max_events():
    rec = {"id": "j3", "status": "running", "max_events": 1}
    command_jobs._on_monitor_batch(rec, ["a", "b", "c"])
    assert rec["event_count"] == 1 and rec["_last_event_lines"] == ["a"] and rec.get("_max_events_hit")


def test_monitor_matches_past_the_kept_line_length():
    async def run():
        got = []
        on_batch = lambda lines, _overflow=0: got.append(lines)
        batcher = monitor.LineBatcher({"_pattern": monitor.compile_pattern("ERROR")}, on_batch)
        batcher.feed("x" * (monitor.MAX_LINE_CHARS + 1) + "ERROR\n")
        await batcher.close()
        return got

    got = asyncio.run(run())
    assert len(got) == 1 and len(got[0]) == 1 and got[0][0].endswith("…")


def test_context_grep_stops_at_max_matches():
    from app.workspace_ops.search_render import _format_search_lines

    out = "a.py:1:x\na.py-2-after\n--\nb.py-1-before\nb.py:2:x\n"
    rows, hidden, more = _format_search_lines(out, root="", windows=False, grep=True, hide_secrets=False, cap_lines=1)
    assert (rows, hidden, more) == (["a.py:1:x", "a.py-2-after"], 0, True)


# -- logic audit 2026-10-18 ---------------------------------------------------

def test_skill_description_continues_on_indented_lines():
    from app import skill_parse

    doc = ("---\nname: demo\ndescription: Use this when the user asks to deploy\n"
           "  the service or roll it back.\nlicense: MIT\n---\nBody\n")
    parsed = skill_parse.parse_skill_md(doc)
    assert parsed["description"] == "Use this when the user asks to deploy the service or roll it back."
    assert parsed["license"] == "MIT"


def test_empty_frontmatter_keeps_the_whole_body():
    from app import skill_parse

    parsed = skill_parse.parse_skill_md("---\n---\n# Deploy\nStep one.\n\n---\n\nStep two.\n", slug="deploy")
    assert parsed["body"] == "# Deploy\nStep one.\n\n---\n\nStep two.\n"


@pytest.mark.parametrize("field", ["prompt", "model"])
def test_null_schedule_text_field_is_missing_not_the_string_none(field):
    with pytest.raises(schedules.ScheduleError):
        schedules._normalize(1, _schedule(recur_kind="every", every_seconds=3600, **{field: None}))
    item = schedules._normalize(1, _schedule(recur_kind="every", every_seconds=3600, timezone=None))
    assert item["timezone"] == "UTC"


def test_every_preview_steps_from_the_stored_next_fire():
    item = {"recur_kind": "every", "every_seconds": 3600, "enabled": True, "paused": False,
            "next_fire_at": "2026-10-18T10:00:00Z"}
    assert schedules.preview_fires(item, 3) == [
        "2026-10-18T10:00:00Z", "2026-10-18T11:00:00Z", "2026-10-18T12:00:00Z"]
    assert schedules.preview_fires({**item, "paused": True}) == []
    assert schedules.preview_fires({**item, "enabled": False}) == []


def test_star_step_dom_is_unrestricted_so_dow_must_match_too():
    after = datetime(2026, 10, 20, 1, tzinfo=timezone.utc)  # a Tuesday
    fires = cron.next_n("0 0 */2 * 1", "UTC", after, 3)
    assert [(d.day, d.isoweekday()) for d in fires] == [(9, 1), (23, 1), (7, 1)]


def test_xml_string_parameters_stay_text():
    def args(params: str) -> dict:
        _, calls = parsing.extract_text_tool_calls(
            f"<tool_call>\n<function=write_file>\n{params}</function>\n</tool_call>", coding=True)
        return json.loads(calls[0]["function"]["arguments"])

    got = args('<parameter=path>\npackage.json\n</parameter>\n<parameter=content>\n{"name": "x"}\n</parameter>\n')
    assert got == {"path": "package.json", "content": '{"name": "x"}'}
    assert args("<parameter=path>n.txt</parameter><parameter=content>42</parameter>")["content"] == "42"
    assert args("<parameter=path>a.py</parameter><parameter=content>\n    pass\n\n</parameter>")["content"] == "    pass\n"
    assert args('<parameter=path>"README.md"</parameter><parameter=content>x</parameter>')["path"] == "README.md"
    _, calls = parsing.extract_text_tool_calls(
        "<tool_call><function=run_terminal_command><parameter=command>ls</parameter>"
        "<parameter=timeout>\n600\n</parameter></function></tool_call>", coding=True)
    assert json.loads(calls[0]["function"]["arguments"]) == {"command": "ls", "timeout": 600}


def test_devin_dead_sku_is_not_resumed():
    from app.chat_generation.policy import _should_resume
    from app.sources.devin_catalog import _sku_unavailable_message

    assert not _should_resume(503, _sku_unavailable_message("devin/swe-2", "high"), resumes_left=4, idle_stalls=0)
    assert _should_resume(503, "upstream error", resumes_left=4, idle_stalls=0)
