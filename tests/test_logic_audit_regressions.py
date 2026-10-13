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
from app.workspace_ops import file_ledger
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


def test_failed_out_of_range_sftp_read_does_not_count_as_read():
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
                patch.object(workspace, "_sftp_read_window", AsyncMock(return_value=(b"", False))), \
                patch.object(file_ledger, "note_read") as noted:
            with pytest.raises(WorkspaceError, match="beyond the end"):
                await workspace._read(1, 1, "box", {"path": "/srv/app/f.txt", "offset": 99}, "/srv/app")
            return noted.call_count

    assert asyncio.run(run()) == 0


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
