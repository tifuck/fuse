"""HTTP contracts for the payloads the chat page reads but no other test pins.

static/chat.js destructures these fields to render the sidebar (historyRow /
projectRow), the memory drawer (acceptContext), the Console transcript
(acceptThread / unpackThread / readThreadStream), the right pane (agentRowHtml /
jobRowHtml) and the error banners (api()). test_chat_status_api.py owns the run
and tool lifecycle receipts; this module pins the wire shape around them: list
rows and ordering, search snippets and caps, the sync context block, packed and
streamed snapshots, queue receipts and cursors, agent work rows, job rows, and
the error bodies the UI branches on.
"""
import json
import os
import re
from contextlib import contextmanager
from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware

from app import accounts, chat_store, chats, command_jobs, db
from app.security import limiter
from app.config import settings
from tests.mariadb_fixture import isolated_database

pytestmark = pytest.mark.skipif(
    os.environ.get("FUSE_CHAT_TEST_MODE") != "1",
    reason="requires disposable MariaDB fixture",
)

# historyRow/projectRow read these on every /chat/threads and /chat/sync row.
_SIDEBAR_FIELDS = ("id", "title", "model", "archived", "created_at", "updated_at", "revision",
                   "kind", "host_id", "project_id", "host_alias", "project_name", "running",
                   "schedule_id", "schedule_name", "bypass_permissions", "auto_permissions",
                   "archived_at", "pinned", "pin_order", "unread")
# _chat_public_run: the run receipt embedded in thread payloads (acceptThread).
_RUN_RECEIPT_KEYS = {"id", "thread_id", "message_id", "status", "created_at", "updated_at",
                     "cancel_requested", "resume_count", "activity"}
# agentRowHtml/agentStatusText/agentActivitySummary reads beyond the status fields,
# and the Stop prompt counts background rows.
_AGENT_WORK_FIELDS = ("children", "tool_counts", "recent", "current", "quiet_s", "wake_state",
                      "context", "work_ms", "work_live", "created_at", "updated_at",
                      "description", "subagent_type", "depth", "model", "background")
_WAKE_STATES = {"", "delivered", "held", "dropped", "retrying"}
# chats.search_threads/search_projects bound every search at 50 rows.
_SEARCH_CAP = 50


@contextmanager
def _quiet_sync():
    """GET /chat/sync reaps and relaunches queued runs; pin those for these tests."""
    with patch("app.chat_runs.reap_orphans"), patch("app.chat_runs.resume_queued", new=AsyncMock()):
        yield


@pytest.fixture()
def client():
    with isolated_database():
        accounts.create_user("status@test.invalid", "password12", admin=False)
        accounts.create_user("other@test.invalid", "password12", admin=False)
        app = FastAPI()
        app.add_middleware(
            SessionMiddleware, secret_key=settings.app_secret, https_only=True, same_site="strict"
        )
        from app.web import router

        app.include_router(router)
        with TestClient(app, base_url="https://testserver") as http:
            yield http


def _login(client, email="status@test.invalid"):
    # The auth limiter is process-global (10/min) and every test logs in twice.
    limiter._hits.clear()
    # csrf_guard prefers the x-csrf-token header over the form token: drop the
    # previous session's token so a re-login cannot send a stale one.
    client.headers.pop("x-csrf-token", None)
    page = client.get("/login").text
    token = re.search(r'name="csrf_token" value="([^"]+)"', page).group(1)
    posted = client.post(
        "/login",
        data={"email": email, "password": "password12", "csrf_token": token},
        follow_redirects=False,
    )
    assert posted.status_code == 303
    meta = re.search(r'name="csrf-token" content="([^"]+)"', client.get("/chat").text)
    client.headers["x-csrf-token"] = meta.group(1)


def _uid():
    return accounts.user_by_email("status@test.invalid")["id"]


def _create(client, command, **fields):
    response = client.post("/chat/threads", json={"model": "offline", "command_id": command, **fields})
    assert response.status_code == 201, response.text
    body = response.json()
    return body, chats.resolve_thread_pk(_uid(), body["id"])


def _json(value):
    # ensure_ascii: the MariaDB translator doubles literal '%', which an emoji
    # inside a bound parameter would otherwise be read as.
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"))


def _save_messages(thread_pk, messages):
    db.execute(
        "UPDATE chat_threads SET messages=?, revision=revision+1 WHERE id=?",
        (_json(messages), thread_pk),
    )


def _set_time(thread_pk, updated):
    db.execute("UPDATE chat_threads SET updated_at=? WHERE id=?", (updated, thread_pk))


def _parse_iso(stamp):
    try:
        return datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except ValueError:
        return None


def _assert_sidebar_row(row):
    """Every field historyRow/projectRow reads, correctly typed."""
    assert set(row) >= set(_SIDEBAR_FIELDS)
    assert isinstance(row["id"], str) and row["id"]
    assert isinstance(row["title"], str) and row["title"]  # never ""; renders "New chat"
    assert isinstance(row["model"], str)
    assert isinstance(row["archived"], bool) and isinstance(row["running"], bool)
    assert isinstance(row["revision"], int)
    assert row["kind"] in ("chat", "project")
    assert _parse_iso(row["created_at"]) and _parse_iso(row["updated_at"])
    assert row["host_id"] is None or isinstance(row["host_id"], int)
    assert row["project_id"] is None or isinstance(row["project_id"], int)
    assert row["host_alias"] is None or isinstance(row["host_alias"], str)
    assert row["project_name"] is None or isinstance(row["project_name"], str)
    assert row["schedule_id"] is None or isinstance(row["schedule_id"], int)
    assert isinstance(row["schedule_name"], str)
    assert isinstance(row["archived_at"], str)
    assert isinstance(row["bypass_permissions"], bool)
    assert isinstance(row["auto_permissions"], bool)
    assert isinstance(row["pinned"], bool) and isinstance(row["unread"], bool)
    assert isinstance(row["pin_order"], int)
    for heavy in ("messages", "context", "tools"):
        assert heavy not in row  # the sidebar poll must not carry transcripts


def _insert_job(job_id, user_id, thread_pk, status, *, background=1, out="", out_chars=None,
                incomplete=0, error="", exit_code=None, finished_at=None, monitor=None):
    command_jobs.ensure_schema()
    db.execute(
        """INSERT INTO command_jobs(id,user_id,host_id,host,thread_id,command,cwd,background,timeout_ms,
               status,exit_code,error,out,out_chars,output_incomplete,created_at,started_at,finished_at)
           VALUES(?,?,0,'box',?,'echo hi','',?,?,?,?,?,?,?,?,?,?,?)""",
        (job_id, user_id, thread_pk, background, 1000, status, exit_code, error, out,
         out_chars, incomplete, "2026-10-05T00:00:00Z", "2026-10-05T00:00:00Z", finished_at),
    )
    if monitor:
        db.execute(
            "UPDATE command_jobs SET kind='monitor', source=?, pattern=?, event_count=?, description=? WHERE id=?",
            (monitor.get("source", "command"), monitor.get("pattern", ""),
             monitor.get("events", 0), monitor.get("description", ""), job_id),
        )


def _insert_child(child_id, thread_pk, uid, status, *, parent, root=None, depth=1,
                  request=None, message=None, created="2026-10-04T10:00:00Z", updated=None):
    db.execute(
        """INSERT INTO chat_runs(id,thread_id,user_id,status,request,message_id,message,created_at,updated_at,
               parent_run_id,root_run_id,agent_depth)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
        (child_id, thread_pk, uid, status, _json(request or {}), "m-" + child_id, _json(message or {}),
         created, updated or created, parent, root or parent, depth),
    )


def _tool(name, **fields):
    return {"name": name, "status": "done", **fields}


def _shell_trace(job_id, **extra):
    base = dict(
        job_id=job_id, child_id=job_id, command_id=job_id, command="echo hi",
        status="running", execution_status="running", state="running", output="",
        output_chars=0, output_mode="full", preview="", error="",
    )
    base.update(extra)
    return _tool("run_terminal_command", **base)


def _listed(client, public_id):
    rows = [row for row in client.get("/chat/threads").json()["threads"] if row["id"] == public_id]
    assert len(rows) == 1
    return rows[0]


# --- GET /chat/threads: sidebar rows ---------------------------------------


def test_sidebar_rows_carry_the_fields_the_history_list_renders(client):
    _login(client)
    first, _ = _create(client, "side-1", title="Sidebar one")
    second, _ = _create(client, "side-2")
    listed = client.get("/chat/threads")
    assert listed.status_code == 200 and listed.headers["cache-control"] == "no-store"
    body = listed.json()
    assert set(body) == {"threads", "archived", "projects"}
    assert isinstance(body["projects"], list)
    rows = {row["id"]: row for row in body["threads"]}
    assert set(rows) == {first["id"], second["id"]}
    for row in rows.values():
        _assert_sidebar_row(row)
    assert rows[first["id"]]["title"] == "Sidebar one"
    assert rows[second["id"]]["title"] == "New chat"  # an empty title renders a default, never ""
    assert rows[first["id"]]["model"] == "offline"

    # /chat/sync feeds the same renderer (upsertLocal -> historyRow) row shape.
    with _quiet_sync():
        sync = client.get("/chat/sync").json()
    assert {row["id"] for row in sync["threads"]} == set(rows)
    for row in sync["threads"]:
        _assert_sidebar_row(row)

    archived = client.patch(
        f"/chat/threads/{first['id']}",
        json={"archived": True, "expected_revision": first["revision"], "command_id": "side-arch"},
    )
    assert archived.status_code == 200, archived.text
    body = client.get("/chat/threads").json()
    assert [row["id"] for row in body["threads"]] == [second["id"]]
    assert [row["id"] for row in body["archived"]] == [first["id"]]
    assert body["archived"][0]["archived"] is True
    assert _parse_iso(body["archived"][0]["archived_at"])
    with _quiet_sync():
        sync = client.get("/chat/sync").json()
    # sync carries both buckets in one list; the UI splits by the archived flag.
    assert {row["id"]: row["archived"] for row in sync["threads"]} == {first["id"]: True, second["id"]: False}


def test_sidebar_order_is_recency_desc_with_a_stable_id_tiebreak(client):
    _login(client)
    ids, pks = {}, {}
    for i in (1, 2, 3):
        body, pk = _create(client, f"order-{i}", title=f"Order {i}")
        ids[i], pks[i] = body["id"], pk
    _set_time(pks[1], "2026-10-01T10:00:00Z")
    _set_time(pks[2], "2026-10-02T10:00:00Z")
    _set_time(pks[3], "2026-10-03T10:00:00Z")

    def order():
        return [row["id"] for row in client.get("/chat/threads").json()["threads"]]

    assert order() == [ids[3], ids[2], ids[1]]
    # Equal stamps fall back to id desc, so a poll can never reshuffle the list.
    _set_time(pks[1], "2026-10-02T10:00:00Z")
    assert pks[2] > pks[1]
    assert order() == [ids[3], ids[2], ids[1]]
    assert order() == order()

    # A real turn bumps recency: the touched thread leads the list again.
    revision = chats.get_thread(_uid(), ids[1])["revision"]
    chat_store.start_turn(_uid(), pks[1], "hello", revision, "order-turn")
    assert order() == [ids[1], ids[3], ids[2]]


def test_sidebar_pins_and_unread_round_trip(client):
    """The pinned section's wire contract: PATCH pins, pin-order reorders,
    a finished root run badges the row, and the thread GET clears it."""
    _login(client)
    first, _ = _create(client, "pin-1", title="Pin one")
    second, second_pk = _create(client, "pin-2", title="Pin two")

    def pin(public_id, revision, command, want):
        response = client.patch(
            f"/chat/threads/{public_id}",
            json={"pinned": want, "expected_revision": revision, "command_id": command},
        )
        assert response.status_code == 200, response.text
        return response.json()

    pinned_first = pin(first["id"], first["revision"], "pin-a", True)
    assert pinned_first["pinned"] is True and pinned_first["pin_order"] == 0
    pinned_second = pin(second["id"], second["revision"], "pin-b", True)
    assert pinned_second["pin_order"] < pinned_first["pin_order"]  # newest pin takes the top
    # pinned must be a bool, not truthy.
    bad = client.patch(
        f"/chat/threads/{first['id']}",
        json={"pinned": "yes", "expected_revision": first["revision"], "command_id": "pin-a-bad"},
    )
    assert bad.status_code == 422

    reordered = client.post(
        "/chat/threads/pin-order",
        json={"ids": [first["id"], second["id"]], "command_id": "pin-drag"},
    )
    assert reordered.status_code == 200, reordered.text
    rows = {row["id"]: row for row in client.get("/chat/threads").json()["threads"]}
    assert rows[first["id"]]["pin_order"] < rows[second["id"]]["pin_order"]
    # Unknown fields and empty lists are rejected.
    assert client.post("/chat/threads/pin-order", json={"ids": [], "command_id": "x"}).status_code == 422
    assert client.post("/chat/threads/pin-order", json={"ids": [first["id"]], "command_id": "x", "order": 1}).status_code == 422

    # A finished conversation badges the row until the thread is viewed.
    revision = chats.get_thread(_uid(), second["id"])["revision"]
    run = chat_store.start_turn(_uid(), second_pk, "hello", revision, "pin-turn")["run"]
    chat_store.finish_run(run["id"], {"content": "done"}, "completed")
    assert _listed(client, second["id"])["unread"] is True
    viewed = client.get(f"/chat/threads/{second['id']}")
    assert viewed.status_code == 200 and viewed.json()["unread"] is False
    assert _listed(client, second["id"])["unread"] is False  # cleared for every sidebar
    # Unpinning takes the row back out of the section without losing its slot.
    unpinned = pin(second["id"], pinned_second["revision"] + 2, "pin-c", False)
    assert unpinned["pinned"] is False


def test_sidebar_running_flag_settles_once_and_stays_settled(client):
    _login(client)
    body, pk = _create(client, "run-flag")
    assert _listed(client, body["id"])["running"] is False
    run = chat_store.start_turn(_uid(), pk, "hello", body["revision"], "run-flag-turn")["run"]
    assert _listed(client, body["id"])["running"] is True
    with _quiet_sync():
        sync_row = [row for row in client.get("/chat/sync").json()["threads"] if row["id"] == body["id"]][0]
    assert sync_row["running"] is True

    chat_store.finish_run(run["id"], {"content": "done"}, "completed")
    first, second = _listed(client, body["id"]), _listed(client, body["id"])
    assert first["running"] is False and second["running"] is False  # terminal stays terminal
    assert first == second  # a settled thread cannot reorder between polls
    with _quiet_sync():
        assert client.get("/chat/sync").json()["threads"][0]["running"] is False


# --- GET /chat/threads?q=: search ------------------------------------------


def test_search_rows_carry_snippets_and_split_archived_from_active(client):
    _login(client)
    head, _ = _create(client, "snip-1", title="Alpha notes",
                      messages=[{"role": "user", "content": "the gateway retry token needs review"}])
    deep, _ = _create(client, "snip-2", title="Beta notes",
                      messages=[{"role": "user", "content": "preamble " + "z" * 80 + " gateway deep"}])
    titled, _ = _create(client, "snip-3", title="Gamma gateway")
    shelved, _ = _create(client, "snip-4", title="Delta notes",
                         messages=[{"role": "user", "content": "gateway in the archived thread"}])
    client.patch(f"/chat/threads/{shelved['id']}",
                 json={"archived": True, "expected_revision": shelved["revision"], "command_id": "snip-arch"})

    body = client.get("/chat/threads", params={"q": "gateway"}).json()
    assert set(body) == {"threads", "archived", "projects"}
    hits = {row["id"]: row for row in body["threads"]}
    assert set(hits) == {head["id"], deep["id"], titled["id"]}
    for row in hits.values():
        _assert_sidebar_row(row)
        assert isinstance(row["snippet"], str)  # always present; "" renders no second line
        assert "messages" not in row
    assert [row["id"] for row in body["archived"]] == [shelved["id"]]
    assert isinstance(body["archived"][0]["snippet"], str)

    # The snippet must agree with the content it previews: an ellipsis only
    # when the match is cut off, and never more than the window it claims.
    assert "gateway" in hits[head["id"]]["snippet"].lower()
    assert not hits[head["id"]]["snippet"].startswith("…")
    assert hits[deep["id"]]["snippet"].startswith("…")
    assert "gateway" in hits[deep["id"]]["snippet"].lower()
    assert len(hits[deep["id"]]["snippet"]) <= 1 + 42 + len("gateway") + 64
    assert hits[titled["id"]]["snippet"] == ""  # title-only match: nothing to preview


def test_search_caps_results_but_the_sidebar_list_is_unbounded(client):
    _login(client)
    created = []
    for i in range(55):
        body, pk = _create(client, f"cap-{i}", title=f"cap check {i:02d}")
        created.append((body["id"], pk))
        _set_time(pk, f"2026-09-01T{i // 60:02d}:{i % 60:02d}:00Z")

    listed = client.get("/chat/threads").json()["threads"]
    assert [row["id"] for row in listed] == [item[0] for item in reversed(created)]

    hits = client.get("/chat/threads", params={"q": "cap check"}).json()["threads"]
    assert len(hits) == _SEARCH_CAP  # bounded results, newest first; the list itself is not paginated
    assert [row["id"] for row in hits] == [item[0] for item in reversed(created)][:_SEARCH_CAP]
    for row in hits:
        _assert_sidebar_row(row)
        assert "cap check" in row["title"].lower()


# --- project grouping ------------------------------------------------------


def test_project_rows_carry_the_grouping_fields_the_projects_tab_renders(client):
    _login(client)
    uid = _uid()
    db.execute(
        """INSERT INTO user_hosts(user_id,alias,label,hostname,port,username,auth_type,secret,default_cwd,created_at,updated_at)
           VALUES(?,'box','','10.0.0.8',22,'root','agent','x','/work',?,?)""",
        (uid, db.now(), db.now()),
    )
    host_id = db.fetchone("SELECT id FROM user_hosts WHERE user_id=?", (uid,))["id"]
    db.execute(
        """INSERT INTO user_projects(user_id,host_id,name,remote_path,notes,created_at,updated_at)
           VALUES(?,?,'billing-service','/srv/billing','',?,?)""",
        (uid, host_id, db.now(), db.now()),
    )
    project_id = db.fetchone("SELECT id FROM user_projects WHERE user_id=?", (uid,))["id"]

    session = client.post("/chat/threads", json={
        "kind": "project", "host_id": host_id, "project_id": project_id, "command_id": "proj-1",
    })
    assert session.status_code == 201, session.text
    body = session.json()
    assert body["kind"] == "project"
    assert body["host_id"] == host_id and body["project_id"] == project_id
    assert body["project_name"] == "billing-service" and body["host_alias"] == "box"
    assert isinstance(body["project_color"], str) and body["project_color"]
    assert isinstance(body["project_logo_url"], str)

    listed = client.get("/chat/threads").json()
    row = listed["threads"][0]
    _assert_sidebar_row(row)
    assert row["kind"] == "project" and row["project_id"] == project_id
    assert len(listed["projects"]) == 1
    proj = listed["projects"][0]
    assert set(proj) == {"id", "host_id", "host_alias", "name", "remote_path", "default_model",
                         "permission_mode", "default_permissions", "thread_id", "created_at", "updated_at", "running",
                         "color", "logo_url", "archived", "archived_at", "sessions", "last_active_at"}
    assert proj["id"] == project_id and proj["host_id"] == host_id
    assert proj["name"] == "billing-service" and proj["host_alias"] == "box"
    assert proj["remote_path"] == "/srv/billing"
    assert proj["thread_id"] == body["id"]  # the folder row opens its newest session
    assert proj["sessions"] == 1 and proj["running"] is False
    assert proj["archived"] is False
    assert _parse_iso(proj["updated_at"]) and _parse_iso(proj["last_active_at"])

    hits = client.get("/chat/threads", params={"q": "billing"}).json()
    assert [p["id"] for p in hits["projects"]] == [project_id]
    # The session matches through its project name; there is no message to preview.
    assert [row["id"] for row in hits["threads"]] == [body["id"]]
    assert hits["threads"][0]["snippet"] == ""


# --- /chat/context and the sync context block ------------------------------


def test_context_block_matches_what_the_memory_drawer_reads(client):
    _login(client)
    thread, _ = _create(client, "ctx-thread", context="thread notes")
    got = client.get("/chat/context")
    assert got.status_code == 200 and got.headers["cache-control"] == "no-store"
    body = got.json()
    assert set(body) >= {"instructions", "profile", "available", "default_instructions",
                         "revision", "recaps", "thread_context", "tools", "limits"}
    assert body["revision"] == 0 and body["recaps"] is True
    assert isinstance(body["instructions"], str) and isinstance(body["profile"], str)
    assert isinstance(body["available"], list)
    assert body["thread_context"] == ""  # per-thread values arrive only with ?thread_id
    assert body["tools"] == ["web_search"]
    assert body["limits"] == {"context": chats.CONTEXT_MAX}

    saved = client.put("/chat/context", json={
        "profile": "shared", "recaps": False, "expected_revision": 0, "command_id": "ctx-1",
    })
    assert saved.status_code == 200, saved.text
    data = saved.json()
    assert data["revision"] == 1  # acceptContext re-bases its edit cursor on this
    assert data["profile"] == "shared" and data["recaps"] is False
    assert isinstance(data["instructions"], str)

    with _quiet_sync():
        sync = client.get("/chat/sync").json()
    assert set(sync["context"]) >= {"instructions", "profile", "available",
                                    "default_instructions", "revision", "recaps"}
    assert sync["context"]["revision"] == 1
    assert sync["context"]["profile"] == "shared" and sync["context"]["recaps"] is False

    # Thread settings the composer restores on open (searchOn, threadContext, model).
    snap = client.get(f"/chat/threads/{thread['id']}").json()
    assert snap["tools"] == ["web_search"] and snap["context"] == "thread notes"
    assert snap["model"] == "offline"
    per_thread = client.get("/chat/context", params={"thread_id": thread["id"]}).json()
    assert per_thread["thread_context"] == "thread notes"
    assert per_thread["tools"] == ["web_search"]
    search_off, _ = _create(client, "ctx-tools-off", tools=[])
    assert client.get(f"/chat/threads/{search_off['id']}").json()["tools"] == []


# --- thread snapshot: packing and streaming --------------------------------


def _pack_message():
    return {
        "id": "m-pack", "role": "assistant", "content": "done 🙂 ok", "reasoning": "a🙂bcdef",
        "tools": [_tool("read_file", id="t1", path="x.txt")],
        "segments": [
            {"type": "reasoning", "text": "a🙂bcdef"},
            {"type": "tool", "id": "t1"},
            {"type": "content", "text": "done 🙂 ok"},
        ],
    }


def test_pack_flag_replaces_segment_text_with_lengths_on_the_wire(client):
    _login(client)
    thread, pk = _create(client, "pack-create")
    _save_messages(pk, [_pack_message()])

    packed = client.get(f"/chat/threads/{thread['id']}?pack=1").json()
    assert packed["segments_packed"] is True
    msg = packed["messages"][0]
    assert msg["content"] == "done 🙂 ok" and msg["reasoning"] == "a🙂bcdef"
    # Lengths are JS string lengths (UTF-16 code units); unpackThread slices
    # content/reasoning by them. The tool segment keeps its trace binding.
    assert msg["segments"] == [
        {"type": "reasoning", "n": 8},
        {"type": "tool", "id": "t1"},
        {"type": "content", "n": 10},
    ]

    plain = client.get(f"/chat/threads/{thread['id']}").json()
    assert "segments_packed" not in plain
    assert plain["messages"][0]["segments"] == [
        {"type": "reasoning", "text": "a🙂bcdef"},
        {"type": "tool", "id": "t1"},
        {"type": "content", "text": "done 🙂 ok"},
    ]


def test_streamed_snapshot_paints_the_tail_then_the_full_snapshot(client):
    _login(client)
    thread, pk = _create(client, "stream-create")
    messages = [{"id": f"m{i:02d}", "role": "user" if i % 2 == 0 else "assistant", "content": f"line {i}"}
                for i in range(20)]
    _save_messages(pk, messages)

    res = client.get(f"/chat/threads/{thread['id']}?pack=1&stream=1")
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("application/x-ndjson")
    assert res.headers["cache-control"] == "no-store"
    lines = [json.loads(line) for line in res.text.splitlines() if line.strip()]
    assert len(lines) == 2
    head, full = lines
    assert head["partial"] is True
    assert head["message_count"] == 20
    assert [m["id"] for m in head["messages"]] == [f"m{i:02d}" for i in range(4, 20)]
    assert [m["id"] for m in full["messages"]] == [f"m{i:02d}" for i in range(20)]
    assert "partial" not in full
    # Both lines come from one build: the tail paints against the same snapshot.
    assert head["revision"] == full["revision"] and head["event_cursor"] == full["event_cursor"]
    assert head["id"] == full["id"] == thread["id"]

    _save_messages(pk, messages[:3])
    short = client.get(f"/chat/threads/{thread['id']}?stream=1")
    lines = [json.loads(line) for line in short.text.splitlines() if line.strip()]
    assert len(lines) == 1 and "partial" not in lines[0]
    assert [m["id"] for m in lines[0]["messages"]] == ["m00", "m01", "m02"]


# --- thread snapshot: queue receipts, cursors, run receipt -----------------


def test_snapshot_echoes_queue_receipts_and_cursors_the_reconciler_trusts(client):
    _login(client)
    thread, pk = _create(client, "queue-create")
    other, other_pk = _create(client, "queue-other")
    run = chat_store.start_turn(_uid(), pk, "hello", thread["revision"], "q1:turn")["run"]
    chat_store.start_turn(_uid(), other_pk, "hello", other["revision"], "q2:turn")

    body = client.get(f"/chat/threads/{thread['id']}", params={"queued": "q1"}).json()
    # retireAcceptedQueue matches either bare id or the queue id + ':turn' suffix.
    assert body["accepted_queue_ids"] == ["q1:turn"]
    scoped = client.get(f"/chat/threads/{thread['id']}", params={"queued": "q2"}).json()
    assert scoped["accepted_queue_ids"] == []  # another thread's send is never retired here

    assert body["id"] == thread["id"]
    assert isinstance(body["revision"], int)
    assert isinstance(body["event_cursor"], int) and isinstance(body["thread_cursor"], int)
    assert body["thread_cursor"] <= body["event_cursor"]  # a watermark ahead would skip needed patches
    assert set(body["run"]) == _RUN_RECEIPT_KEYS
    assert body["run"]["id"] == run["id"] and body["run"]["thread_id"] == thread["id"]
    assert body["run"]["message_id"] == run["message_id"]
    assert body["run"]["status"] in ("queued", "running")
    assert body["run"]["cancel_requested"] is False and body["run"]["resume_count"] == 0
    assert "activity" in body["run"]

    chat_store.finish_run(run["id"], {"content": "done"}, "completed")
    settled = client.get(f"/chat/threads/{thread['id']}").json()
    assert settled["run"] is None and settled["running"] is False


# --- thread snapshot: message rows -----------------------------------------


def test_message_rows_keep_unique_ids_and_bind_segments_to_tools(client):
    _login(client)
    thread, pk = _create(client, "rows-create")
    _save_messages(pk, [
        {"id": "u1", "role": "user", "content": "question one"},
        {"id": "a1", "role": "assistant", "content": "final answer", "reasoning": "thinking part",
         "tools": [_tool("read_file", id="t1"), _tool("grep", id="t2")],
         "segments": [
             {"type": "reasoning", "text": "thinking part"},
             {"type": "tool", "id": "t1"},
             {"type": "tool", "id": "t2"},
             {"type": "content", "text": "final answer"},
         ]},
        {"id": "dup", "role": "user", "content": "first dup"},
        {"id": "dup", "role": "user", "content": "second dup"},
    ])
    messages = client.get(f"/chat/threads/{thread['id']}").json()["messages"]
    ids = [msg["id"] for msg in messages]
    assert len(set(ids)) == len(ids)  # the DOM keys rows by id; collisions drop messages
    for msg in messages:
        assert isinstance(msg["id"], str) and msg["id"]
        assert msg["role"] in ("user", "assistant")
        for field in ("content", "reasoning", "error"):
            assert isinstance(msg[field], str)

    assistant = messages[1]
    assert assistant["content"] == "final answer" and assistant["reasoning"] == "thinking part"
    tool_ids = {trace["id"] for trace in assistant["tools"]}
    assert tool_ids == {"t1", "t2"}
    segments = assistant["segments"]
    assert [seg["type"] for seg in segments] == ["reasoning", "tool", "tool", "content"]
    bound = [seg["id"] for seg in segments if seg["type"] == "tool"]
    assert sorted(bound) == ["t1", "t2"] and len(set(bound)) == len(bound)
    assert set(bound) <= tool_ids  # every tool segment resolves to a trace in the same message
    assert "".join(seg["text"] for seg in segments if seg["type"] == "content") == assistant["content"]
    assert "".join(seg["text"] for seg in segments if seg["type"] == "reasoning") == assistant["reasoning"]


# --- GET /chat/threads/{id}/agents -----------------------------------------


def test_agents_rows_carry_work_fields_and_wake_states_the_pane_maps(client):
    _login(client)
    thread, pk = _create(client, "agents-fields")
    uid = _uid()
    root = chat_store.start_turn(uid, pk, "hello", thread["revision"], "agents-fields-turn")["run"]
    now = db.now()
    _insert_child("ag-done", pk, uid, "completed", parent=root["id"],
                  request={"model": "offline", "agent": {
                      "description": "Index the docs", "subagent_type": "explore", "background": True,
                      "wake_sent": True, "meter": {"used": 32100, "limit": 200000, "model": "offline"}}},
                  message={"content": "report text", "elapsedMs": 2000,
                           "tools": [_tool("read_file", id="r1"), _tool("read_file", id="r2"),
                                    _tool("grep", id="g1")]},
                  created="2026-10-04T10:00:00Z")
    _insert_child("ag-drop", pk, uid, "completed", parent=root["id"],
                  request={"agent": {"description": "Dropped report", "background": True, "wake_attempts": 2}},
                  message={"content": "ignored"}, created="2026-10-04T11:00:00Z")
    _insert_child("ag-hold", pk, uid, "completed", parent=root["id"],
                  request={"agent": {"description": "Held report", "background": True}},
                  message={"content": "ignored"}, created="2026-10-04T12:00:00Z")
    _insert_child("ag-run", pk, uid, "running", parent=root["id"],
                  request={"agent": {"description": "Still working"}},
                  message={"tools": [_tool("grep", id="x1", status="running")]},
                  created="2026-10-04T13:00:00Z", updated=now)
    _insert_child("ag-queue", pk, uid, "queued", parent=root["id"],
                  request={"agent": {"description": "Queued child"}}, message={},
                  created="2026-10-04T14:00:00Z", updated=now)
    _insert_child("ag-fg", pk, uid, "completed", parent=root["id"],
                  request={"agent": {"description": "Foreground report"}},
                  message={"content": "fg report"}, created="2026-10-04T15:00:00Z")
    _insert_child("ag-grand", pk, uid, "completed", parent="ag-done", root=root["id"], depth=2,
                  request={"agent": {"description": "Grandchild"}},
                  message={"content": "grand"}, created="2026-10-04T10:30:00Z")

    got = client.get(f"/chat/threads/{thread['id']}/agents")
    assert got.status_code == 200 and got.headers["cache-control"] == "no-store"
    rows = got.json()["agents"]
    # newest first; the pane renders the finished block in exactly this order
    assert [row["id"] for row in rows] == [
        "ag-fg", "ag-queue", "ag-run", "ag-hold", "ag-drop", "ag-grand", "ag-done",
    ]
    by = {row["id"]: row for row in rows}
    assert len(by) == len(rows)  # never duplicated rows
    for row in rows:
        assert set(row) >= set(_AGENT_WORK_FIELDS)
        assert isinstance(row["children"], int) and row["children"] >= 0
        assert isinstance(row["tool_counts"], dict)
        assert isinstance(row["recent"], list)
        assert isinstance(row["current"], str)
        assert isinstance(row["quiet_s"], (int, float)) and row["quiet_s"] >= 0
        assert row["wake_state"] in _WAKE_STATES
        assert row["context"] is None or (isinstance(row["context"], dict) and row["context"]["limit"] > 0)
        assert row["work_ms"] is None or (isinstance(row["work_ms"], int) and row["work_ms"] >= 0)
        assert isinstance(row["work_live"], bool)
        assert isinstance(row["description"], str) and len(row["description"]) <= 200
        assert isinstance(row["subagent_type"], str) and row["subagent_type"]
        assert isinstance(row["depth"], int) and row["depth"] >= 0
        assert isinstance(row["model"], str)
        assert _parse_iso(row["created_at"]) and _parse_iso(row["updated_at"])

    done = by["ag-done"]
    assert done["tool_counts"] == {"read_file": 2, "grep": 1}  # a lying count hides calls in the pane
    assert done["children"] == 1
    assert done["wake_state"] == "delivered"
    assert done["context"] == {"used": 32100, "limit": 200000, "ratio": round(32100 / 200000, 4)}
    assert done["work_ms"] == 2000 and done["work_live"] is False
    assert done["summary"] == "report text"
    assert done["current"] == ""
    assert {call["name"] for call in done["recent"]} <= {"read_file", "grep"}
    assert all(isinstance(call["status"], str) and call["status"] for call in done["recent"])

    assert by["ag-drop"]["wake_state"] == "dropped"
    assert by["ag-hold"]["wake_state"] == "retrying"
    assert by["ag-fg"]["wake_state"] == ""  # a foreground report never wakes the thread
    assert by["ag-run"]["current"] == "grep" and by["ag-run"]["wake_state"] == ""
    assert by["ag-run"]["work_ms"] is None  # unknown timing is unknown, not a zero clock
    assert by["ag-queue"]["current"] == "queued"
    assert by["ag-grand"]["depth"] == 2 and by["ag-grand"]["parent_run_id"] == "ag-done"

    # Stopping the main agent parks undelivered reports on the next message.
    assert client.post(f"/chat/runs/{root['id']}/cancel?scope=self").status_code == 200
    rows = {row["id"]: row for row in client.get(f"/chat/threads/{thread['id']}/agents").json()["agents"]}
    assert rows["ag-hold"]["wake_state"] == "held"
    assert rows["ag-done"]["wake_state"] == "delivered"
    # held wins over dropped: with the main agent stopped no wake will run at all
    assert rows["ag-drop"]["wake_state"] == "held"


# --- GET /chat/threads/{id}/jobs -------------------------------------------


def test_jobs_rows_carry_work_list_fields_and_recent_has_no_duplicates(client):
    _login(client)
    thread, pk = _create(client, "jobs-fields")
    uid = _uid()
    _insert_job("job-live", uid, pk, "running", background=1, out="so far\n")
    _insert_job("job-mon", uid, pk, "running", background=1,
                monitor={"pattern": "ERROR", "events": 3, "description": "watch the log"})
    _insert_job("job-both", uid, pk, "completed", background=1, out="both output\n",
                out_chars=12, exit_code=0, finished_at=db.now())
    _insert_job("job-done", uid, pk, "completed", background=1, out="finished\n",
                out_chars=9, exit_code=0, finished_at=db.now())
    # job-both is referenced by an unsettled trace, so both feeds return it.
    _save_messages(pk, [{"role": "assistant", "content": "", "tools": [
        _shell_trace("job-both", status="running", execution_status="running"),
    ]}])

    body = client.get(f"/chat/threads/{thread['id']}/jobs").json()
    assert set(body) == {"jobs", "live_count", "recent"}
    live = {job["id"]: job for job in body["jobs"]}
    assert set(live) == {"job-live", "job-mon"}
    for job in live.values():
        assert set(job) >= {"id", "command_id", "host", "command", "state", "background",
                            "started_ms", "elapsed_ms", "status", "agent_run_id", "output"}
        assert job["command_id"] == job["id"]  # jobRowHtml builds its buttons from command_id
        assert job["state"] in ("running", "cancelling")
        assert job["started_ms"] is None or isinstance(job["started_ms"], int)
        assert isinstance(job["elapsed_ms"], int) and job["elapsed_ms"] >= 0
        assert job["agent_run_id"] is None
    mon = live["job-mon"]
    assert mon["kind"] == "monitor" and mon["name"] == "monitor"
    assert mon["event_count"] == 3 and mon["pattern"] == "ERROR"
    assert mon["description"] == "watch the log"

    recent = body["recent"]
    ids = [job["id"] for job in recent]
    assert len(ids) == len(set(ids))  # two feeds may see one job; the payload must not double it
    assert ids.count("job-both") == 1
    assert set(ids) == {"job-both", "job-done"}
    by = {job["id"]: job for job in recent}
    for job in recent:
        assert isinstance(job["output"], str)
        assert job["state"] not in ("running", "cancelling")
        assert job["command_id"] == job["id"]
        if "out" in job:
            assert job["out"] == job["output"]  # the compatibility alias never clips
    assert by["job-done"]["out"] == "finished\n" and by["job-done"]["output"] == "finished\n"
    assert by["job-both"]["output"] == "both output\n"


# --- error payloads --------------------------------------------------------


def test_error_bodies_are_shaped_for_the_ui_error_paths(client):
    _login(client)
    client.cookies.clear()
    unauth = client.get("/chat/threads")
    assert unauth.status_code == 401
    err = unauth.json()["error"]
    # api() redirects to /login on error.type == 'auth'.
    assert err["type"] == "auth" and err["code"] == 401
    assert isinstance(err["message"], str) and err["message"]

    _login(client)
    missing = client.get("/chat/threads/nope")
    assert missing.status_code == 404
    # api() surfaces detail verbatim as the error message; it must be a string.
    assert isinstance(missing.json()["detail"], str) and missing.json()["detail"]

    thread, _ = _create(client, "err-create")
    stale = client.patch(f"/chat/threads/{thread['id']}", json={
        "title": "Nope", "expected_revision": 99, "command_id": "err-stale",
    })
    assert stale.status_code == 409
    assert isinstance(stale.json()["detail"], str) and stale.json()["detail"]

    unknown = client.post("/chat/threads", json={"bogus": 1, "command_id": "err-unknown"})
    assert unknown.status_code == 422
    assert isinstance(unknown.json()["detail"], str) and unknown.json()["detail"]
