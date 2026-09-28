#!/usr/bin/env python3
"""Permanent QA login and fixture chats for visual checks of fuse-dev.

    ./venv/bin/python -m scripts.dev_qa_account          # ensure login + reseed fixtures
    ./venv/bin/python -m scripts.dev_qa_account --show   # print the login

Run from /root/fuse-dev. Refuses any schema but fuse_dev. The login lives in
data/dev-qa-login.json (0600, not in git) and is reused across runs, so the
password stays stable after scripts.clone_db_to_dev recreates fuse_dev (that
script runs this one afterwards). Fixture threads are synthetic and owned
only by the QA account; every run deletes and recreates them.
"""
from __future__ import annotations

import json
import os
import secrets
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.environ.setdefault("DATABASE_POOL_SIZE", "2")

from app.config import settings  # noqa: E402

TARGET = "fuse_dev"
EMAIL = "qa@dev.fuse.test"
LOGIN_FILE = ROOT / "data" / "dev-qa-login.json"
FIXTURE_PREFIX = "QA · "


def _guard():
    parsed = urlsplit(settings.database_url or "")
    if parsed.scheme != "mysql" or parsed.path.strip("/") != TARGET:
        raise SystemExit(f"refusing: DATABASE_URL must point at {TARGET}")


def _login() -> dict:
    if LOGIN_FILE.exists():
        data = json.loads(LOGIN_FILE.read_text())
        if data.get("email") == EMAIL and len(data.get("password") or "") >= 8:
            return data
    data = {"email": EMAIL, "password": secrets.token_urlsafe(18), "url": "https://127.0.0.1:8089/login"}
    LOGIN_FILE.parent.mkdir(parents=True, exist_ok=True)
    LOGIN_FILE.write_text(json.dumps(data, indent=2) + "\n")
    os.chmod(LOGIN_FILE, 0o600)
    return data


def _ensure_user(login: dict) -> int:
    from app import accounts, db
    from app.crypto import hash_password, verify_password
    from app.vault import create_wrapped_dek

    row = accounts.user_by_email(EMAIL)
    if row is None:
        return int(accounts.create_user(EMAIL, login["password"], admin=False)["id"])
    if not verify_password(login["password"], row["password_hash"]):
        # Password changed out of band: reset it and the (empty) host vault together.
        salt, wrapped, params = create_wrapped_dek(login["password"])
        db.execute(
            "UPDATE users SET password_hash=?, vault_salt=?, wrapped_dek=?, vault_kdf=? WHERE id=?",
            (hash_password(login["password"]), salt, wrapped, params, row["id"]),
        )
        db.execute("DELETE FROM user_hosts WHERE user_id=?", (row["id"],))
    return int(row["id"])


# ---------------------------------------------------------------- fixtures

def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _mid() -> str:
    return str(uuid.uuid4())


def _tool(ident, name, **fields):
    base = {
        "id": ident, "name": name, "status": "done", "error": "", "results": [],
        "output": "", "output_chars": 0, "output_retained_chars": 0, "truncated": False,
        "duration_ms": 120, "call_duration_ms": 120,
    }
    base.update(fields)
    out = base.get("output") or ""
    base["output_chars"] = base["output_retained_chars"] = len(out)
    return base


def _command(ident, job_id, command, output, *, exit_code=0, background=False, state="completed", status="done", error=""):
    return _tool(
        ident, "run_terminal_command", command=command, cwd="/srv/fuse-qa", host="qa-box", platform="posix",
        project_root="/srv/fuse-qa", background=background, child_id=job_id, command_id=job_id, job_id=job_id,
        execution_status=state, state=state, status=status, exit_code=exit_code, error=error,
        output=output, preview=output[:120], output_bytes=len(output), output_chars_exact=True,
        output_mode="full", output_start=0, timeout_ms=600000, elapsed_ms=900, execution_duration_ms=900,
        termination_confirmed=True,
    )


LONG_PATH = "/srv/fuse-qa/services/ingest/pipelines/normalizers/very_long_directory_name_without_breaks/handler_implementation_v2.py"

MARKDOWN = """Here is the audit summary. Everything below is fixture text for layout checks.

## Findings

1. **Composer height drift** between panes when the live dock is visible.
2. A very long unbroken token: `""" + LONG_PATH + """`
3. Nested list:
   - child item one with `inline code`
   - child item two that is intentionally long enough to wrap onto a second line at typical desktop widths so wrapping can be checked

| Pane | Height | Notes |
|---|---:|---|
| Main composer | 48px | single-line input |
| Subagent dock | 44px | working indicator |
| A cell with a much longer description that should wrap | 0 | — |

```python
def reconcile(rows):
    for row in rows:
        if row.status in ("running", "cancelling"):
            yield row.id, row.started_at  # a long trailing comment to force horizontal scrolling inside the code block
```

> Blockquote: keep line length readable and the left rule aligned with list bullets.

That is all."""


def _main_thread(now):
    subs = [
        ("Audit schema and migrations", "completed"),
        ("Review the pane layout for the side panel across every chat view mode, including overflow and wrapping of really long descriptions", "failed"),
        ("Trace the stall watchdog", "interrupted"),
        ("Summarize findings", "completed"),
    ]
    child_ids = [_mid() for _ in subs]
    tools = [
        _tool("ts_0_0", "todo_write", preview="4 todos", todos=[
            {"id": "1", "content": "Read the layout code", "status": "completed"},
            {"id": "2", "content": "Spawn reviewers for each pane", "status": "in_progress"},
            {"id": "3", "content": "Collect and merge findings", "status": "pending"},
            {"id": "4", "content": "Write the summary with a deliberately long todo line that should wrap in narrow panes", "status": "pending"},
        ], output='{"todos": 4}'),
        _tool("ts_0_1", "read_file", path="/srv/fuse-qa/static/chat.css", cwd="/srv/fuse-qa", host="qa-box",
              platform="posix", project_root="/srv/fuse-qa", offset=1, limit=200,
              output="1: .chat-dock {\n2:   padding: 12px 16px;\n3: }\n", preview=".chat-dock {"),
        _tool("ts_0_2", "grep", pattern="chat-live-dock|chat-agent-live-dock", path="/srv/fuse-qa/static",
              cwd="/srv/fuse-qa", host="qa-box", platform="posix", project_root="/srv/fuse-qa", max_matches=50,
              output="static/chat.css:120:.chat-live-dock {\nstatic/chat.css:410:.chat-agent-live-dock {\n",
              preview="2 matches"),
        _command("ts_1_0", "qa0000000001", "npm run lint -- --max-warnings=0", "> lint\n> eslint static\n\n✔ no problems\n"),
        _command("ts_1_1", "qa0000000002", "pytest -q tests/test_layout.py", "F..\nFAILED tests/test_layout.py::test_dock_height - assert 44 == 48\n1 failed, 2 passed\n",
                 exit_code=1, status="error", error="Command exited with code 1"),
        _command("ts_1_2", "qa0000000003", "tail -f /var/log/fuse-qa/app.log | grep --line-buffered -E 'WARN|ERROR'",
                 "WARN dock height mismatch\n", background=True, exit_code=None, state="cancelled", status="interrupted",
                 error="cancellation requested"),
        _tool("ts_2_0", "search_replace", path=LONG_PATH, cwd="/srv/fuse-qa", host="qa-box", platform="posix",
              project_root="/srv/fuse-qa", status="error", kind="tool_error", error="old_string not found in file",
              output="old_string not found in file"),
    ]
    for i, ((desc, _status), cid) in enumerate(zip(subs, child_ids)):
        tools.append(_tool(f"ts_3_{i}", "spawn_subagent", description=desc, preview=desc, child_id=cid,
                           model="qa/fixture-model", background=True, summary="", output=f"spawned {cid}"))
    tools.append(_tool("ts_4_0", "get_subagent_output", child_id=child_ids[0], background=True,
                       execution_status="completed", preview="completed", output="Schema looks consistent."))
    segments = [
        {"type": "reasoning", "text": "Plan the audit: read the CSS, then fan out reviewers per pane."},
        *[{"type": "tool", "id": t["id"]} for t in tools[:3]],
        {"type": "content", "text": "Running the checks now.\n\n"},
        *[{"type": "tool", "id": t["id"]} for t in tools[3:7]],
        {"type": "reasoning", "text": "\n\nThe layout test fails on dock height. Spawning reviewers for each pane."},
        *[{"type": "tool", "id": t["id"]} for t in tools[7:]],
        {"type": "content", "text": MARKDOWN},
    ]
    assistant = {
        "id": _mid(), "role": "assistant", "content": "".join(s["text"] for s in segments if s["type"] == "content"),
        "reasoning": "".join(s["text"] for s in segments if s["type"] == "reasoning"),
        "error": "", "model": "qa/fixture-model", "elapsedMs": 184000, "stopped": False,
        "usage": {"prompt_tokens": 48213, "completion_tokens": 2210, "total_tokens": 50423},
        "tools": tools, "segments": segments,
    }
    messages = [
        {"id": _mid(), "role": "user", "content": "Audit the chat layout across every view and pane. Check alignment, padding, and wrapping.", "reasoning": "", "error": ""},
        assistant,
        {"id": _mid(), "role": "user", "content": "Short follow-up.", "reasoning": "", "error": ""},
        {"id": _mid(), "role": "assistant", "content": "Done. One test still fails; details above.", "reasoning": "", "error": "",
         "model": "qa/fixture-model", "elapsedMs": 2100, "usage": {"prompt_tokens": 50500, "completion_tokens": 20, "total_tokens": 50520}},
    ]
    return messages, list(zip(subs, child_ids)), tools


def _child_message(desc, status):
    tools = [
        _tool("ts_0_0", "read_file", path="/srv/fuse-qa/static/chat.css", cwd="/srv/fuse-qa", host="qa-box",
              platform="posix", project_root="/srv/fuse-qa", offset=1, limit=80, output="1: .chat {\n", preview=".chat {"),
        _command("ts_0_1", "qa00000000c" + str(abs(hash(desc)) % 10), "rg -n 'min-height' static/", "static/chat.css:88: min-height: 44px;\n"),
    ]
    content = {
        "completed": f"Finished: {desc}.\n\n- Checked three files\n- No regressions",
        "failed": "",
        "interrupted": "Partial notes before interruption.",
    }[status]
    return {
        "id": _mid(), "role": "assistant", "content": content,
        "reasoning": "Inspecting the relevant styles.", "model": "qa/fixture-model",
        "error": "Upstream returned 502 after 3 retries" if status == "failed" else "",
        "elapsedMs": 42000, "tools": tools,
        "segments": [{"type": "reasoning", "text": "Inspecting the relevant styles."},
                     {"type": "tool", "id": "ts_0_0"}, {"type": "tool", "id": "ts_0_1"},
                     *([{"type": "content", "text": content}] if content else [])],
        "usage": {"prompt_tokens": 9000, "completion_tokens": 400, "total_tokens": 9400},
    }


def _seed(uid: int):
    from app import chats, db

    with db.tx() as conn:
        old = [r["id"] for r in conn.execute("SELECT id FROM chat_threads WHERE user_id=?", (uid,)).fetchall()]
        for tid in old:
            conn.execute("DELETE FROM chat_runs WHERE user_id=? AND thread_id=?", (uid, tid))
            conn.execute("DELETE FROM command_jobs WHERE user_id=? AND thread_id=?", (uid, tid))
            conn.execute("DELETE FROM chat_threads WHERE user_id=? AND id=?", (uid, tid))

    now = datetime.now(timezone.utc)
    dumps = lambda v: json.dumps(v, ensure_ascii=False, separators=(",", ":"))  # noqa: E731

    chats.create_thread(uid, title=FIXTURE_PREFIX + "Long markdown", model="qa/fixture-model", messages=[
        {"id": _mid(), "role": "user", "content": "Show every markdown element.", "reasoning": "", "error": ""},
        {"id": _mid(), "role": "assistant", "content": MARKDOWN, "reasoning": "", "error": "", "model": "qa/fixture-model"},
    ])
    chats.create_thread(uid, title=FIXTURE_PREFIX + "Long title that must truncate in sidebar and header", model="qa/fixture-model", messages=[
        {"id": _mid(), "role": "user", "content": "Hi", "reasoning": "", "error": ""},
        {"id": _mid(), "role": "assistant", "content": "Hello. This thread exists to test title truncation.", "reasoning": "", "error": "", "model": "qa/fixture-model"},
    ])

    messages, subs, tools = _main_thread(now)
    view = chats.create_thread(uid, title=FIXTURE_PREFIX + "Tools, subagents and commands", model="qa/fixture-model", messages=messages)
    tid = chats.resolve_thread_pk(uid, view["id"])
    root_id = _mid()
    ts = _iso(now - timedelta(minutes=5))
    with db.tx() as conn:
        conn.execute(
            """INSERT INTO chat_runs(id,thread_id,user_id,status,request,message_id,message,created_at,updated_at,
               parent_run_id,root_run_id,agent_depth) VALUES(?,?,?,?,?,?,?,?,?,NULL,?,0)""",
            (root_id, tid, uid, "completed", dumps({"model": "qa/fixture-model", "run_id": root_id, "thread_id": tid,
             "agent": {"depth": 0, "run_id": root_id, "root_run_id": root_id}}),
             messages[1]["id"], dumps(messages[1]), ts, ts, root_id),
        )
        for i, ((desc, status), cid) in enumerate(subs):
            msg = _child_message(desc, status)
            req = {"model": "qa/fixture-model", "run_id": cid, "thread_id": tid,
                   "messages": [{"role": "user", "content": f"Task: {desc}"}],
                   "agent": {"depth": 1, "run_id": cid, "parent_run_id": root_id, "root_run_id": root_id,
                             "description": desc, "subagent_type": "general-purpose", "background": True,
                             "wake_sent": True}}
            cts = _iso(now - timedelta(minutes=4, seconds=-i * 10))
            conn.execute(
                """INSERT INTO chat_runs(id,thread_id,user_id,status,request,message_id,message,created_at,updated_at,
                   parent_run_id,root_run_id,agent_depth,provider_messages) VALUES(?,?,?,?,?,?,?,?,?,?,?,1,?)""",
                (cid, tid, uid, status, dumps(req), msg["id"], dumps(msg), cts, cts, root_id, root_id,
                 dumps(req["messages"]) if status == "interrupted" else None),
            )
        for tool in tools:
            if tool["name"] != "run_terminal_command":
                continue
            conn.execute(
                """INSERT INTO command_jobs(id,user_id,host_id,host,thread_id,run_id,command,cwd,background,timeout_ms,
                   status,exit_code,error,out,out_bytes,duration_ms,created_at,started_at,finished_at,out_chars,
                   termination_confirmed) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (tool["job_id"], uid, 0, "qa-box", tid, root_id, tool["command"], tool["cwd"], int(tool["background"]),
                 tool["timeout_ms"], tool["state"], tool["exit_code"], tool["error"], tool["output"], len(tool["output"]),
                 900, ts, ts, ts, len(tool["output"]), 1),
            )
    return view["id"]


def main(argv):
    _guard()
    login = _login()
    if "--show" in argv:
        print(json.dumps(login, indent=2))
        return
    uid = _ensure_user(login)
    public = _seed(uid)
    print(f"QA login {login['email']} (password in {LOGIN_FILE.relative_to(ROOT)})")
    print(f"fixture thread: https://127.0.0.1:8089/chat?t={public}")


if __name__ == "__main__":
    main(sys.argv[1:])
