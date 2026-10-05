#!/usr/bin/env python3
"""Permanent QA login and fixture chats for visual checks of fuse-dev.

    ./venv/bin/python -m scripts.dev_qa_account          # ensure login + reseed fixtures
    ./venv/bin/python -m scripts.dev_qa_account --show   # print the login

Run from /root/fuse-dev. Refuses any schema but fuse_dev. The login lives in
data/dev-qa-login.json (0600, not in git) and is reused across runs, so the
password stays stable after scripts.clone_db_to_dev recreates fuse_dev (that
script runs this one afterwards). Fixture threads are synthetic and owned
only by the QA account; every run deletes and recreates them. "Heavy
transcript" is a ~5 MB, 200-message thread with subagents for load-time checks.
"Summary lines" exercises Console's Claude Code summary lines. A fake
connector machine "qa-box" carries projects for the Projects page and chooser.
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


def _command(ident, job_id, command, output, *, exit_code=0, background=False, state="completed", status="done", error="",
             description=""):
    extra = {"description": description} if description else {}
    return _tool(
        ident, "run_terminal_command", **extra, command=command, cwd="/srv/fuse-qa", host="qa-box", platform="posix",
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
        _command("ts_1_0", "qa0000000001", "npm run lint -- --max-warnings=0", "> lint\n> eslint static\n\n✔ no problems\n",
                 description="Lint the static assets"),
        _command("ts_1_1", "qa0000000002", "pytest -q tests/test_layout.py", "F..\nFAILED tests/test_layout.py::test_dock_height - assert 44 == 48\n1 failed, 2 passed\n",
                 exit_code=1, status="error", error="Command exited with code 1", description="Run the layout tests"),
        _command("ts_1_2", "qa0000000003", "tail -f /var/log/fuse-qa/app.log | grep --line-buffered -E 'WARN|ERROR'",
                 "WARN dock height mismatch\n", background=True, exit_code=None, state="cancelled", status="interrupted",
                 error="cancellation requested", description="Follow the app log for warnings"),
        # Legacy stop receipt: it copied its target's in-flight state and was
        # saved mid-cancel. It must render as a finished stop, not spin.
        _tool("ts_1_3", "kill_command", child_id="qa0000000003", command_id="qa0000000003", job_id="qa0000000003",
              command="tail -f /var/log/fuse-qa/app.log | grep --line-buffered -E 'WARN|ERROR'", host="qa-box",
              background=True, state="cancelling", execution_status="cancelling", status="interrupted",
              termination_confirmed=False, output="status: cancelling\nCancellation requested; remote termination is not yet confirmed."),
        _tool("ts_2_0", "search_replace", path=LONG_PATH, cwd="/srv/fuse-qa", host="qa-box", platform="posix",
              project_root="/srv/fuse-qa", status="error", kind="tool_error", error="old_string not found in file",
              output="old_string not found in file"),
    ]
    for i, ((desc, _status), cid) in enumerate(zip(subs, child_ids)):
        tools.append(_tool(f"ts_3_{i}", "spawn_subagent", description=desc, preview=desc, child_id=cid,
                           model="qa/fixture-model", background=True, summary="", output=f"spawned {cid}"))
    tools.append(_tool("ts_3_k", "kill_subagent", child_id=child_ids[2], output="cancelled"))
    tools.append(_tool("ts_4_0", "get_subagent_output", child_id=child_ids[0], background=True,
                       execution_status="completed", preview="completed", output="Schema looks consistent."))
    segments = [
        {"type": "reasoning", "text": "Plan the audit: read the CSS, then fan out reviewers per pane."},
        *[{"type": "tool", "id": t["id"]} for t in tools[:3]],
        {"type": "content", "text": "Running the checks now.\n\n"},
        *[{"type": "tool", "id": t["id"]} for t in tools[3:8]],
        {"type": "reasoning", "text": "\n\nThe layout test fails on dock height. Spawning reviewers for each pane."},
        *[{"type": "tool", "id": t["id"]} for t in tools[8:]],
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


EDIT_PATH = "/srv/fuse-qa/app/services/notifications/dispatcher.py"

_BEFORE = """class Dispatcher:
    def __init__(self, queue, transport, *, retries=3):
        self.queue = queue
        self.transport = transport
        self.retries = retries

    async def deliver(self, event):
        for attempt in range(self.retries):
            try:
                return await self.transport.send(event.recipient, event.payload, timeout=10)
            except TransportError:
                continue
        raise DeliveryFailed(event.id)"""

_AFTER = """class Dispatcher:
    def __init__(self, queue, transport, *, retries=3, backoff=0.5):
        self.queue = queue
        self.transport = transport
        self.retries = retries
        self.backoff = backoff

    async def deliver(self, event):
        for attempt in range(self.retries):
            try:
                return await self.transport.send(event.recipient, event.payload, timeout=self.timeout_for(event, attempt))
            except TransportError as exc:
                logger.warning("delivery attempt %d/%d for %s failed: %s", attempt + 1, self.retries, event.id, exc)
                await asyncio.sleep(self.backoff * (2 ** attempt))
        raise DeliveryFailed(event.id)"""

_NEW_FILE = """\"\"\"Exponential backoff helpers shared by every outbound transport.\"\"\"
from __future__ import annotations

import random
from dataclasses import dataclass


@dataclass(frozen=True)
class Backoff:
    base: float = 0.5
    cap: float = 30.0
    jitter: float = 0.1

    def delay(self, attempt: int) -> float:
        raw = min(self.cap, self.base * (2 ** attempt))
        return raw + random.uniform(0, raw * self.jitter)  # jitter keeps a thundering herd of retries from lining up again


DEFAULT = Backoff()
"""

_PYTEST_FAIL = """============================= test session starts ==============================
platform linux -- Python 3.12.3, pytest-8.3.2, pluggy-1.5.0
rootdir: /srv/fuse-qa
collected 14 items

tests/test_dispatcher.py ......F.......                                   [100%]

=================================== FAILURES ===================================
__________________ test_deliver_backs_off_between_attempts ___________________

    async def test_deliver_backs_off_between_attempts(monkeypatch):
        sleeps = []
        monkeypatch.setattr(asyncio, "sleep", lambda s: sleeps.append(s) or _done())
>       await Dispatcher(queue, FlakyTransport(fail=2), retries=3, backoff=0.5).deliver(event)
E       AttributeError: 'Dispatcher' object has no attribute 'timeout_for'

tests/test_dispatcher.py:88: AttributeError
=========================== short test summary info ============================
FAILED tests/test_dispatcher.py::test_deliver_backs_off_between_attempts - AttributeError: 'Dispatcher' object has no attribute 'timeout_for'
========================= 1 failed, 13 passed in 0.42s =========================
"""


def _unified(path, before, after, n=2, strip_header=False):
    import difflib

    lines = list(difflib.unified_diff(before.split("\n"), after.split("\n"), fromfile="a/" + path,
                                      tofile="b/" + path, lineterm="", n=n))
    return "\n".join(lines[2:] if strip_header else lines)


def _edits_thread(now):
    """Every tool that shows text: edits (diffs), writes, reads, shell output,
    grep and listings, with long and deeply indented lines for wrap checks."""
    old_sig = "    def __init__(self, queue, transport, *, retries=3):\n        self.queue = queue\n        self.transport = transport\n        self.retries = retries"
    new_sig = "    def __init__(self, queue, transport, *, retries=3, backoff=0.5):\n        self.queue = queue\n        self.transport = transport\n        self.retries = retries\n        self.backoff = backoff"
    read_out = "\n".join(f"{i}: {line}" for i, line in enumerate(_BEFORE.split("\n"), 1))
    git_diff = ("diff --git a/app/services/notifications/dispatcher.py b/app/services/notifications/dispatcher.py\n"
                "index 3f1c2aa..9be41d0 100644\n" + _unified("app/services/notifications/dispatcher.py", _BEFORE, _AFTER, n=3))
    grep_out = "\n".join([
        "app/services/notifications/dispatcher.py:12:                return await self.transport.send(event.recipient, event.payload, timeout=10)",
        "app/services/notifications/webhooks.py:48:        response = await self.transport.send(hook.url, body, timeout=settings.webhook_timeout_seconds)",
        "app/services/email/smtp_relay.py:131:            await self.transport.send(message.to, message.render(), timeout=self.connect_timeout + self.write_timeout)",
        "tests/test_dispatcher.py:40:    transport.send.assert_awaited_once_with(event.recipient, event.payload, timeout=10)",
    ])
    tools = [
        _tool("te_0", "read_file", path=EDIT_PATH, cwd="/srv/fuse-qa", host="qa-box", platform="posix",
              project_root="/srv/fuse-qa", offset=1, limit=200, output=read_out, preview=read_out[:120]),
        _tool("te_1", "grep", pattern=r"transport\.send\(", path="/srv/fuse-qa", cwd="/srv/fuse-qa", host="qa-box",
              platform="posix", project_root="/srv/fuse-qa", max_matches=50, output=grep_out, preview="4 matches"),
        _tool("te_2", "search_replace", path=EDIT_PATH, cwd="/srv/fuse-qa", host="qa-box", platform="posix",
              project_root="/srv/fuse-qa", preview=_unified(EDIT_PATH, old_sig, new_sig),
              output=f"updated {EDIT_PATH} (1 replacement)"),
        _tool("te_3", "search_replace", path=EDIT_PATH, cwd="/srv/fuse-qa", host="qa-box", platform="posix",
              project_root="/srv/fuse-qa", preview=_unified(EDIT_PATH, _BEFORE, _AFTER, strip_header=True)[:800],
              output=f"updated {EDIT_PATH} (2 replacements from 2 edits)"),
        _tool("te_4", "write_file", path="/srv/fuse-qa/app/services/notifications/backoff.py", cwd="/srv/fuse-qa",
              host="qa-box", platform="posix", project_root="/srv/fuse-qa",
              output=f"created /srv/fuse-qa/app/services/notifications/backoff.py ({len(_NEW_FILE)} bytes)"),
        _command("te_5", "qae000000001", "cd /srv/fuse-qa && python -m pytest -q tests/test_dispatcher.py -k 'deliver and not slow' --maxfail=1 -p no:cacheprovider",
                 _PYTEST_FAIL, exit_code=1, status="error", error="Command exited with code 1"),
        _command("te_6", "qae000000002", "git diff -- app/services/notifications/dispatcher.py", git_diff + "\n"),
        _tool("te_7", "list_dir", path="/srv/fuse-qa/app/services/notifications", cwd="/srv/fuse-qa", host="qa-box",
              platform="posix", project_root="/srv/fuse-qa",
              preview="__init__.py\nbackoff.py\ndispatcher.py\nwebhooks.py\ntemplates/\nvery_long_module_name_for_the_push_notification_adapter.py"),
        _command("te_8", "qae000000003", "ls -la", "total 8\ndrwxr-xr-x 2 qa qa 4096 Sep 30 10:02 .\n"),
    ]
    say = lambda text: {"type": "content", "text": text}  # noqa: E731
    segments = [
        {"type": "reasoning", "text": "Read the dispatcher, then add backoff."},
        {"type": "tool", "id": "te_0"}, {"type": "tool", "id": "te_1"},
        say("Adding a backoff parameter first.\n\n"),
        {"type": "tool", "id": "te_2"},
        say("Now the retry loop.\n\n"),
        {"type": "tool", "id": "te_3"},
        say("And a shared helper.\n\n"),
        {"type": "tool", "id": "te_4"},
        say("Running the tests.\n\n"),
        {"type": "tool", "id": "te_5"},
        say("`timeout_for` is missing. Checking the diff so far.\n\n"),
        {"type": "tool", "id": "te_6"}, {"type": "tool", "id": "te_7"}, {"type": "tool", "id": "te_8"},
        say("The dispatcher now backs off exponentially; one test still needs `timeout_for`."),
    ]
    assistant = {
        "id": _mid(), "role": "assistant", "content": "".join(s["text"] for s in segments if s["type"] == "content"),
        "reasoning": "".join(s["text"] for s in segments if s["type"] == "reasoning"),
        "error": "", "model": "qa/fixture-model", "elapsedMs": 96000, "stopped": False,
        "usage": {"prompt_tokens": 22000, "completion_tokens": 1400, "total_tokens": 23400},
        "tools": tools, "segments": segments,
    }
    return [
        {"id": _mid(), "role": "user", "content": "Add exponential backoff to the notification dispatcher.", "reasoning": "", "error": ""},
        assistant,
    ], tools


SUMMARY_TEST_OUT = "\n".join(
    [f"tests/test_{name}.py ........ [{pct:3d}%]" for name, pct in
     [("dispatcher", 14), ("backoff", 28), ("webhooks", 42), ("smtp_relay", 57), ("templates", 71), ("queue", 85), ("api", 100)]]
    + ["", "56 passed in 4.21s"])
SUMMARY_HEREDOC = (
    "cat > /tmp/fuse/deploy.sh <<'EOF'\n"
    "#!/usr/bin/env bash\n"
    "set -euo pipefail\n"
    "rsync -az --delete build/ deploy@qa-box:/srv/fuse-qa/current/\n"
    "ssh deploy@qa-box 'systemctl restart fuse-qa'\n"
    "EOF\n"
    "bash /tmp/fuse/deploy.sh"
)


def _summary_thread(now):
    """Claude Code summary lines in Console: thinking folded between reads,
    searches and shell runs; git work in the line; narration and edits ending
    a line; a described monitor; a multi-line command for the live hint."""
    root = dict(cwd="/srv/fuse-qa", host="qa-box", platform="posix", project_root="/srv/fuse-qa")
    tools = [
        _tool("tl_0", "read_file", path="/srv/fuse-qa/app/services/notifications/dispatcher.py", offset=1, limit=200,
              output="1: class Dispatcher:\n2:     pass\n", preview="class Dispatcher:", **root),
        _tool("tl_1", "read_file", path="/srv/fuse-qa/app/services/notifications/backoff.py", offset=1, limit=200,
              output="1: def delay(attempt):\n2:     return 0.5 * 2 ** attempt\n", preview="def delay(attempt):", **root),
        _tool("tl_2", "grep", pattern="timeout_for", path="/srv/fuse-qa/app", max_matches=50,
              output="app/services/notifications/backoff.py:9:def timeout_for(attempt):\n", preview="1 match", **root),
        _tool("tl_3", "list_dir", path="/srv/fuse-qa/tests", preview="test_api.py\ntest_backoff.py\ntest_dispatcher.py", **root),
        _command("tl_4", "qal000000001", "cat package.json | jq .scripts", '{\n  "test": "pytest -q"\n}\n',
                 description="Show the npm scripts"),
        _command("tl_5", "qal000000002", "python -m pytest -q tests/", SUMMARY_TEST_OUT, description="Run the test suite"),
        _command("tl_6", "qal000000003", 'git add -A && git commit -m "Add exponential backoff to the dispatcher"',
                 "[dev 1a2b3c4] Add exponential backoff to the dispatcher\n 2 files changed, 31 insertions(+), 4 deletions(-)\n",
                 description="Commit the backoff change"),
        _command("tl_7", "qal000000004", "git push origin dev",
                 "To github.com:qa/fuse-qa.git\n   9f8e7d6..1a2b3c4  dev -> dev\n", description="Push dev to origin"),
        _command("tl_8", "qal000000005", "gh pr create --fill", "https://github.com/qa/fuse-qa/pull/42\n",
                 description="Open a pull request"),
        _tool("tl_9", "search_replace", path="/srv/fuse-qa/app/services/notifications/backoff.py",
              preview=_unified("app/services/notifications/backoff.py", "def delay(attempt):\n    return 0.5 * 2 ** attempt",
                               "def delay(attempt, cap=30.0):\n    return min(cap, 0.5 * 2 ** attempt)", strip_header=True),
              output="updated backoff.py (1 replacement)", **root),
        _tool("tl_10", "monitor", description="errors in deploy.log", command="tail -f /var/log/fuse-qa/deploy.log",
              pattern="ERROR|Traceback", source="command", event_count=2, background=True, child_id="qal000000006",
              command_id="qal000000006", execution_status="completed", state="completed", preview="2 events",
              output="ERROR rsync: connection unexpectedly closed (retrying)\nERROR health check timed out after 5s (retrying)\n",
              **root),
        _command("tl_11", "qal000000007", SUMMARY_HEREDOC, "sending incremental file list\ncurrent/app.js\n\nsent 18.4K bytes\n",
                 description="Write the deploy script and run it"),
        _command("tl_12", "qal000000008", "systemctl is-active fuse-qa", "active\n"),
        _command("tl_13", "qal000000009", "python -m pytest -q tests/integration", "14 passed in 82.4s\n", background=True,
                 description="Run the integration suite"),
    ]
    say = lambda text: {"type": "content", "text": text}  # noqa: E731
    tool = lambda ident: {"type": "tool", "id": ident}  # noqa: E731
    segments = [
        {"type": "reasoning", "text": "Read the dispatcher and the backoff helper, then find where timeout_for is used.", "ms": 4200},
        tool("tl_0"), tool("tl_1"), tool("tl_2"),
        {"type": "reasoning", "text": "Both files look right. Check the test layout and the npm scripts before running anything.", "ms": 6800},
        tool("tl_3"), tool("tl_4"), tool("tl_5"),
        {"type": "reasoning", "text": "All 56 tests pass. Commit, push and open the PR.", "ms": 2100},
        tool("tl_6"), tool("tl_7"), tool("tl_8"),
        say("Committed and opened PR #42. Capping the delay before deploying.\n\n"),
        tool("tl_9"),
        say("Watching the deploy log while the deploy runs.\n\n"),
        tool("tl_10"), tool("tl_11"), tool("tl_12"), tool("tl_13"),
        say("Deployed; the service is active and the log shows no errors. The integration suite runs in the background."),
    ]
    assistant = {
        "id": _mid(), "role": "assistant", "content": "".join(s["text"] for s in segments if s["type"] == "content"),
        "reasoning": "".join(s["text"] for s in segments if s["type"] == "reasoning"),
        "error": "", "model": "qa/fixture-model", "elapsedMs": 142000, "stopped": False,
        "usage": {"prompt_tokens": 31000, "completion_tokens": 1900, "total_tokens": 32900},
        "tools": tools, "segments": segments,
    }
    wake = ('<command_result id="qal000000009" state="completed">\n'
            "host: qa-box command: python -m pytest -q tests/integration\ndescription: Run the integration suite\n"
            "exit_code: 0\nduration_ms: 83000\n"
            "output_chars: 18\noutput_chars_exact: true\n\n14 passed in 82.4s\n</command_result>")
    return [
        {"id": _mid(), "role": "user", "content": "Cap the backoff delay, ship it, and deploy.", "reasoning": "", "error": ""},
        assistant,
        {"id": _mid(), "role": "user", "content": wake, "reasoning": "", "error": "", "auto": True},
        {"id": _mid(), "role": "assistant", "content": "The integration suite passed.", "reasoning": "", "error": "",
         "model": "qa/fixture-model", "elapsedMs": 1800},
    ], tools


_ROWS_DIFF = """diff --git a/app/config.py b/app/config.py
index 3d2f2c6..dde7137 100644
--- a/app/config.py
+++ b/app/config.py
@@ -12,3 +12,3 @@ class Settings:
-    retries: int = 3
+    retries: int = 5
     backoff: float = 0.5
     timeout_s: int = 10
diff --git a/package-lock.json b/package-lock.json
index 1111111..2222222 100644
--- a/package-lock.json
+++ b/package-lock.json
@@ -40,2 +40,3 @@
     "left-pad": "1.3.0",
+    "nanoid": "5.0.7",
     "zod": "3.23.8"
"""


def _rows_child_message(i, desc, status):
    """A subagent with real recent calls, for Claude Code's agent rows."""
    root = dict(cwd="/srv/fuse-qa", host="qa-box", platform="posix", project_root="/srv/fuse-qa")
    tools = [
        _tool(f"tr_c{i}_0", "grep", pattern="def deliver", path="/srv/fuse-qa/app", output="app/x.py:3:def deliver\n", **root),
        _tool(f"tr_c{i}_1", "read_file", path="/srv/fuse-qa/app/services/notifications/dispatcher.py", output="1: x\n", **root),
        _command(f"tr_c{i}_2", f"qar00000000c{i}", "python -m pytest -q tests/test_dispatcher.py", "14 passed in 0.4s\n",
                 description="Run the dispatcher tests"),
        _tool(f"tr_c{i}_3", "read_file", path="/srv/fuse-qa/app/services/notifications/backoff.py", output="1: x\n", **root),
        _tool(f"tr_c{i}_4", "grep", pattern="timeout_for", path="/srv/fuse-qa/app", output="app/b.py:9:timeout_for\n", **root),
    ][: 5 if i % 2 == 0 else 3]
    content = f"Finished: {desc}." if status == "completed" else ""
    return {
        "id": _mid(), "role": "assistant", "content": content, "reasoning": "", "model": "qa/fixture-model",
        "error": "", "elapsedMs": 65000 + i * 4000, "tools": tools,
        "segments": [*[{"type": "tool", "id": t["id"]} for t in tools], *([{"type": "content", "text": content}] if content else [])],
        "usage": {"prompt_tokens": 30000, "completion_tokens": 900, "total_tokens": 30900},
    }


def _rows_thread(now):
    """Claude Code's per-call rows in Console: a thought ending a summary
    line, a shell call that changed files, sed as Update, Search(...) headers,
    a fetch, a monitor, a background command, an interrupted call, subagents
    started together, and the turn's closing line. The last turn has running
    calls for --live screenshots."""
    root = dict(cwd="/srv/fuse-qa", host="qa-box", platform="posix", project_root="/srv/fuse-qa")
    agents = [("Audit retry settings", "completed"), ("Check the transport timeouts", "completed"),
              ("Review the dispatcher tests", "completed"), ("Trace queue backpressure", "completed")]
    cids = [_mid() for _ in agents]
    tools = [
        _tool("tr_0", "grep", pattern="retries", path="/srv/fuse-qa/app",
              output="app/config.py:12:    retries: int = 3\napp/services/notifications/dispatcher.py:2:    retries=3\n"
                     "app/services/notifications/dispatcher.py:8:        for attempt in range(self.retries):\n", **root),
        _tool("tr_1", "read_file", path="/srv/fuse-qa/app/config.py", output="1: class Settings:\n", **root),
        _command("tr_2", "qar000000001", "grep -rn 'max_retries' app/", "", exit_code=1, description="Look for max_retries"),
        _tool("tr_3", "read_file", path="/srv/fuse-qa/app/services/notifications/dispatcher.py", output="1: class Dispatcher:\n", **root),
        _command("tr_4", "qar000000002", "sed -i 's/retries: int = 3/retries: int = 5/' app/config.py", "",
                 description="Raise the retry count"),
        _command("tr_5", "qar000000003", "npm install nanoid@5", "added 1 package, audited 214 packages in 2s\n\nfound 0 vulnerabilities\n",
                 description="Install nanoid"),
        _tool("tr_6", "read_url", url="https://docs.python.org/3/library/asyncio-task.html", bytes=12595, http_status=200,
              results=[{"title": "Coroutines and Tasks", "url": "https://docs.python.org/3/library/asyncio-task.html",
                        "snippet": "This section outlines high-level asyncio APIs to work with coroutines and Tasks.", "query": ""}]),
        _tool("tr_7", "monitor", description="errors in worker.log", command="tail -f /var/log/fuse-qa/worker.log",
              pattern="ERROR", source="command", event_count=1, background=True, child_id="qar000000004",
              command_id="qar000000004", execution_status="running", state="running", timeout_ms=300000, **root),
        _command("tr_8", "qar000000005", "npm run build -- --watch", "watching for changes…\n", background=True,
                 description="Start the watch build"),
        _command("tr_9", "qar000000006", "npm test", "> test\n> jest\n\nFAIL src/retry.test.ts\n  ● retries five times\n\n"
                 "    expect(received).toBe(expected)\n\n    Expected: 5\n    Received: 3\n\nTests: 1 failed, 41 passed\n",
                 exit_code=1, status="error", error="Command exited with code 1", description="Run the unit tests"),
        _tool("tr_10", "search_replace", path="/srv/fuse-qa/app/services/notifications/retry.py", status="interrupted",
              error="Tool interrupted.", **root),
        *[_tool(f"tr_11_{i}", "spawn_subagent", description=desc, preview=desc, child_id=cid, subagent_type="explore",
                model="qa/fixture-model", summary=f"Finished: {desc}.", agent_tool_uses=5 if i % 2 == 0 else 3,
                agent_tokens=18300 + i * 2100, agent_work_ms=65000 + i * 4000) for i, ((desc, _), cid) in enumerate(zip(agents[:3], cids))],
        _tool("tr_12", "spawn_subagent", description=agents[3][0], preview=agents[3][0], child_id=cids[3],
              subagent_type="general-purpose", model="qa/fixture-model", summary="Finished.", agent_tool_uses=5,
              agent_tokens=24100, agent_work_ms=77000),
    ]
    tools[4].update(edited_files=["app/config.py"], lines_added=1, lines_removed=1,
                    edit_diff="\n".join(_ROWS_DIFF.split("\n")[:9]))
    tools[5].update(edited_files=["package-lock.json"], lines_added=1, lines_removed=0,
                    edit_diff="\n".join(_ROWS_DIFF.split("\n")[9:]))
    say = lambda text: {"type": "content", "text": text}  # noqa: E731
    tool = lambda ident: {"type": "tool", "id": ident}  # noqa: E731
    segments = [
        {"type": "reasoning", "text": "Find where the retry count is set.", "ms": 3100},
        tool("tr_0"), tool("tr_1"), tool("tr_2"),
        {"type": "reasoning", "text": "Settings owns it; the dispatcher only reads it. Check the dispatcher before changing it.", "ms": 2600},
        tool("tr_3"), tool("tr_4"), tool("tr_5"),
        say("Raised the retry count and added nanoid. Reading the asyncio docs on timeouts.\n\n"),
        tool("tr_6"), tool("tr_7"), tool("tr_8"), tool("tr_9"), tool("tr_10"),
        say("Fanning out reviewers.\n\n"),
        tool("tr_11_0"), tool("tr_11_1"), tool("tr_11_2"),
        say("One more pass on backpressure.\n\n"),
        tool("tr_12"),
        say("Retries are now 5; the unit test that pinned 3 still needs updating."),
    ]
    settled = {
        "id": _mid(), "role": "assistant", "content": "".join(s["text"] for s in segments if s["type"] == "content"),
        "reasoning": "".join(s["text"] for s in segments if s["type"] == "reasoning"),
        "error": "", "model": "qa/fixture-model", "elapsedMs": 142000, "endedAt": _iso(now - timedelta(minutes=3)),
        "stopped": False, "usage": {"prompt_tokens": 31000, "completion_tokens": 1900, "total_tokens": 32900},
        "tools": tools, "segments": segments,
    }
    wake = ('<command_result id="qar000000007" state="completed">\nhost: qa-box command: npm run lint\n'
            "description: Lint the sources\nexit_code: 0\nduration_ms: 12000\noutput_chars: 3\noutput_chars_exact: true\n\nok\n</command_result>\n"
            '<command_result id="qar000000008" state="completed">\nhost: qa-box command: npm run typecheck\n'
            "description: Typecheck the project\nexit_code: 0\nduration_ms: 21000\noutput_chars: 3\noutput_chars_exact: true\n\nok\n</command_result>\n"
            '<monitor_event id="qar000000004" seq="1">\nhost: qa-box command: tail -f /var/log/fuse-qa/worker.log\n'
            "description: errors in worker.log\n\nERROR queue depth 5120 exceeds the 4096 limit\n</monitor_event>")
    live_cids = [_mid() for _ in range(2)]
    live_tools = [
        _command("tr_l0", "qar000000009", "python -m pytest -q tests/", "........................................\n"
                 "........................................\n.............F....\n", status="running", state="running",
                 exit_code=None, description="Run the test suite"),
        _tool("tr_l1", "read_url", url="https://peps.python.org/pep-0654/", status="running"),
        _tool("tr_l2", "spawn_subagent", description="Audit retry settings", preview="Audit retry settings",
              child_id=cids[0], subagent_type="explore", status="running", model="qa/fixture-model"),
        _tool("tr_l3", "spawn_subagent", description="Check the transport timeouts", preview="Check the transport timeouts",
              child_id=cids[1], subagent_type="explore", status="running", model="qa/fixture-model"),
        _tool("tr_l4", "spawn_subagent", description="Map the queue consumers", preview="Map the queue consumers",
              child_id=live_cids[0], subagent_type="general-purpose", status="running", model="qa/fixture-model"),
    ]
    live = {
        "id": _mid(), "role": "assistant",
        "content": "Checking the remaining pieces.\n\nReviewers are running.\n\nAnd one more.\n\n", "reasoning": "",
        "error": "", "model": "qa/fixture-model", "elapsedMs": 41000, "stopped": False, "tools": live_tools,
        "segments": [say("Checking the remaining pieces.\n\n"), tool("tr_l0"), tool("tr_l1"),
                     say("Reviewers are running.\n\n"), tool("tr_l2"), tool("tr_l3"),
                     say("And one more.\n\n"), tool("tr_l4")],
    }
    messages = [
        {"id": _mid(), "role": "user", "content": "Raise the dispatcher retries to 5 and check what else depends on it.", "reasoning": "", "error": ""},
        settled,
        {"id": _mid(), "role": "user", "content": wake, "reasoning": "", "error": "", "auto": True},
        {"id": _mid(), "role": "user", "content": "Finish the review.", "reasoning": "", "error": ""},
        live,
    ]
    subs = list(zip(agents, cids)) + [(("Map the queue consumers", "completed"), live_cids[0])]
    return messages, subs, tools + live_tools


def _child_message(desc, status):
    tools = [
        _tool("ts_0_0", "read_file", path="/srv/fuse-qa/static/chat.css", cwd="/srv/fuse-qa", host="qa-box",
              platform="posix", project_root="/srv/fuse-qa", offset=1, limit=80, output="1: .chat {\n", preview=".chat {"),
        _command("ts_0_1", "qa00000000c" + str(abs(hash(desc)) % 10), "rg -n 'min-height' static/", "static/chat.css:88: min-height: 44px;\n"),
        # Each child keeps its own list; none of it may appear in the main chat's
        # section or in a sibling's.
        _tool("ts_0_2", "todo_write", preview="2 todos", output='{"todos": 2}', todos=[
            {"id": "1", "content": f"Inspect: {desc[:48]}", "status": "completed"},
            {"id": "2", "content": "Report what changed", "status": "completed" if status == "completed" else "in_progress"},
        ] + ([{"id": "3", "content": "Re-run the checks", "status": "pending"}] if status != "completed" else [])),
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
                     {"type": "tool", "id": "ts_0_0"}, {"type": "tool", "id": "ts_0_1"}, {"type": "tool", "id": "ts_0_2"},
                     *([{"type": "content", "text": content}] if content else [])],
        "usage": {"prompt_tokens": 9000, "completion_tokens": 400, "total_tokens": 9400},
    }


_WORDS = ("the dock pane layout reconcile cursor thread render message tool output schema migration "
          "subagent stream patch event socket snapshot height scroll markdown code block reasoning "
          "watchdog queue compaction meter context window provider replay trace job command host").split()


def _prose(rng, words):
    out, line = [], []
    for _ in range(words):
        line.append(rng.choice(_WORDS))
        if len(line) > rng.randint(9, 16):
            out.append(" ".join(line).capitalize() + ".")
            line = []
    return " ".join(out + [" ".join(line)]).strip()


def _heavy_thread(turns=100, subagent_every=10):
    """A long transcript shaped like the heaviest real threads (~5 MB): long
    reasoning, many tool traces with output, code blocks, and subagents."""
    import random

    rng = random.Random(7)
    messages, spawns = [], []
    for turn in range(turns):
        messages.append({"id": _mid(), "role": "user", "content": f"Step {turn + 1}: " + _prose(rng, 40), "reasoning": "", "error": ""})
        tools = []
        for k in range(rng.randint(3, 7)):
            ident = f"th_{turn}_{k}"
            if k % 3 == 0:
                out = "\n".join(f"{n}: " + _prose(rng, 12) for n in range(1, 60))
                tools.append(_command(ident, f"qah{turn:04d}{k:04d}", f"pytest -q tests/test_step_{turn}.py -k case_{k}", out))
            elif k % 3 == 1:
                out = "\n".join(f"{n}: " + _prose(rng, 10) for n in range(1, 80))
                tools.append(_tool(ident, "read_file", path=f"/srv/fuse-qa/app/module_{turn}_{k}.py", cwd="/srv/fuse-qa",
                                   host="qa-box", platform="posix", project_root="/srv/fuse-qa", offset=1, limit=200,
                                   output=out, preview=out[:120]))
            else:
                out = "\n".join(f"app/f{n}.py:{n * 7}: " + _prose(rng, 8) for n in range(30))
                tools.append(_tool(ident, "grep", pattern="reconcile|snapshot", path="/srv/fuse-qa/app", cwd="/srv/fuse-qa",
                                   host="qa-box", platform="posix", project_root="/srv/fuse-qa", max_matches=50,
                                   output=out, preview="30 matches"))
        if turn % subagent_every == subagent_every - 1:
            for j in range(2):
                cid = _mid()
                desc = f"Review area {turn}-{j}: " + _prose(rng, 8)
                spawns.append((desc, cid))
                tools.append(_tool(f"th_{turn}_s{j}", "spawn_subagent", description=desc, preview=desc, child_id=cid,
                                   model="qa/fixture-model", background=True, summary="", output=f"spawned {cid}"))
        code = "```python\n" + "\n".join(f"def step_{turn}_{n}(rows):\n    return [r for r in rows if r.id > {n}]  # " + _prose(rng, 6)
                                          for n in range(12)) + "\n```"
        content = f"## Result {turn + 1}\n\n" + _prose(rng, 180) + "\n\n" + code + "\n\n- " + "\n- ".join(_prose(rng, 14) for _ in range(5))
        reasoning = [_prose(rng, 400), _prose(rng, 300)]
        segments = [{"type": "reasoning", "text": reasoning[0]},
                    *[{"type": "tool", "id": t["id"]} for t in tools[:3]],
                    {"type": "reasoning", "text": "\n\n" + reasoning[1]},
                    *[{"type": "tool", "id": t["id"]} for t in tools[3:]],
                    {"type": "content", "text": content}]
        messages.append({
            "id": _mid(), "role": "assistant", "content": content,
            "reasoning": "".join(s["text"] for s in segments if s["type"] == "reasoning"),
            "error": "", "model": "qa/fixture-model", "elapsedMs": 60000 + turn,
            "usage": {"prompt_tokens": 40000 + turn * 900, "completion_tokens": 1800, "total_tokens": 41800 + turn * 900},
            "tools": tools, "segments": segments,
        })
    return messages, spawns


def _heavy_child_message(rng_seed, desc):
    import random

    rng = random.Random(rng_seed)
    msg = _child_message(desc, "completed")
    msg["content"] = f"Finished: {desc}.\n\n" + "\n\n".join(_prose(rng, 120) for _ in range(12))
    msg["reasoning"] = _prose(rng, 1500)
    msg["segments"][0]["text"] = msg["reasoning"]
    msg["segments"][-1]["text"] = msg["content"]
    return msg


def _heavy_child_transcript(rng_seed, desc, rounds=80):
    """Provider history for a subagent with a lot of context: many tool rounds
    with sizeable results, across two legs (a parent follow-up in between)."""
    import random

    rng = random.Random(1000 + rng_seed)
    rows = [{"role": "user", "content": f"Task: {desc}"}]
    for leg in range(2):
        for k in range(rounds // 2):
            call = f"call_{leg}_{k}"
            rows.append({"role": "assistant", "content": _prose(rng, 30) if k % 3 == 0 else "", "tool_calls": [
                {"id": call, "type": "function", "function": {"name": "read_file", "arguments": json.dumps(
                    {"path": f"/srv/fuse-qa/app/part_{leg}_{k}.py", "offset": 1, "limit": 200})}}]})
            rows.append({"role": "tool", "tool_call_id": call,
                         "content": "\n".join(f"{n}: " + _prose(rng, 12) for n in range(1, 70))})
        rows.append({"role": "assistant", "content": "## Leg summary\n\n" + _prose(rng, 200)})
        if leg == 0:
            rows.append({"role": "user", "content": (
                "[Fuse] Follow-up from the parent — not written by the end user. "
                "Stay in the assigned task and host/cwd unless this follow-up changes them:\n\n"
                + f"(followup:{rng_seed:012x}) " + _prose(rng, 30))})
    return rows


def _insert_children(conn, uid, tid, root_id, subs, now, dumps, make_message, make_transcript=None):
    for i, ((desc, status), cid) in enumerate(subs):
        msg = make_message(i, desc, status)
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
             dumps(make_transcript(i, desc)) if make_transcript
             else dumps(req["messages"]) if status == "interrupted" else None),
        )


def _insert_root(conn, uid, tid, root_id, message, ts, dumps):
    conn.execute(
        """INSERT INTO chat_runs(id,thread_id,user_id,status,request,message_id,message,created_at,updated_at,
           parent_run_id,root_run_id,agent_depth) VALUES(?,?,?,?,?,?,?,?,?,NULL,?,0)""",
        (root_id, tid, uid, "completed", dumps({"model": "qa/fixture-model", "run_id": root_id, "thread_id": tid,
         "agent": {"depth": 0, "run_id": root_id, "root_run_id": root_id}}),
         message["id"], dumps(message), ts, ts, root_id),
    )


def _insert_commands(conn, uid, tid, root_id, tools, ts):
    for tool in tools:
        if tool["name"] != "run_terminal_command":
            continue
        conn.execute(
            """INSERT INTO command_jobs(id,user_id,host_id,host,thread_id,run_id,command,cwd,background,timeout_ms,
               status,exit_code,error,out,out_bytes,duration_ms,created_at,started_at,finished_at,out_chars,
               termination_confirmed,description) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (tool["job_id"], uid, 0, "qa-box", tid, root_id, tool["command"], tool["cwd"], int(tool["background"]),
             tool["timeout_ms"], tool["state"], tool["exit_code"], tool["error"], tool["output"], len(tool["output"]),
             900, ts, ts, ts, len(tool["output"]), 1, tool.get("description", "")),
        )


def _seed(uid: int):
    from app import chats, command_jobs, db

    command_jobs.ensure_schema()  # fixture rows use every current column

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
        _insert_root(conn, uid, tid, root_id, messages[1], ts, dumps)
        _insert_children(conn, uid, tid, root_id, subs, now, dumps, lambda i, desc, status: _child_message(desc, status))
        _insert_commands(conn, uid, tid, root_id, tools, ts)

    edits, edit_tools = _edits_thread(now)
    edits_view = chats.create_thread(uid, title=FIXTURE_PREFIX + "File edits and output", model="qa/fixture-model", messages=edits)
    etid = chats.resolve_thread_pk(uid, edits_view["id"])
    eroot = _mid()
    with db.tx() as conn:
        _insert_root(conn, uid, etid, eroot, edits[1], ts, dumps)
        _insert_commands(conn, uid, etid, eroot, edit_tools, ts)
    print(f"edits thread: https://127.0.0.1:8089/chat?t={edits_view['id']}")

    summary, summary_tools = _summary_thread(now)
    summary_view = chats.create_thread(uid, title=FIXTURE_PREFIX + "Summary lines", model="qa/fixture-model", messages=summary)
    stid = chats.resolve_thread_pk(uid, summary_view["id"])
    sroot = _mid()
    with db.tx() as conn:
        _insert_root(conn, uid, stid, sroot, summary[1], ts, dumps)
        _insert_commands(conn, uid, stid, sroot, summary_tools, ts)
    print(f"summary lines thread: https://127.0.0.1:8089/chat?t={summary_view['id']}")

    rows, row_subs, row_tools = _rows_thread(now)
    rows_view = chats.create_thread(uid, title=FIXTURE_PREFIX + "Claude Code rows", model="qa/fixture-model", messages=rows)
    rtid = chats.resolve_thread_pk(uid, rows_view["id"])
    rroot = _mid()
    with db.tx() as conn:
        _insert_root(conn, uid, rtid, rroot, rows[-1], ts, dumps)
        _insert_children(conn, uid, rtid, rroot, row_subs, now, dumps, _rows_child_message)
        # Running calls are only drawn running by --live; no job row backs them.
        _insert_commands(conn, uid, rtid, rroot, [t for t in row_tools if t.get("state") != "running"], ts)
    print(f"claude code rows thread: https://127.0.0.1:8089/chat?t={rows_view['id']}")

    heavy, spawns = _heavy_thread()
    heavy_view = chats.create_thread(uid, title=FIXTURE_PREFIX + "Heavy transcript", model="qa/fixture-model", messages=heavy)
    htid = chats.resolve_thread_pk(uid, heavy_view["id"])
    hroot = _mid()
    with db.tx() as conn:
        _insert_root(conn, uid, htid, hroot, heavy[-1], ts, dumps)
        _insert_children(conn, uid, htid, hroot, [((d, "completed"), c) for d, c in spawns], now, dumps,
                         lambda i, desc, status: _heavy_child_message(i, desc), _heavy_child_transcript)
        _insert_commands(conn, uid, htid, hroot, [t for m in heavy for t in m.get("tools") or []], ts)
    print(f"heavy thread: https://127.0.0.1:8089/chat?t={heavy_view['id']}")
    _seed_projects(uid)
    return view["id"]


QA_MACHINE = "qa-box"


def _seed_projects(uid: int):
    """A connector machine that never connects, with projects in each state
    the Projects page, chooser and sidebar draw: picked color, custom color,
    read-only, archived, plus one project session."""
    from app import chats, db, hosts

    db.execute("DELETE FROM user_hosts WHERE user_id=? AND alias=?", (uid, QA_MACHINE))
    ts = db.now()
    db.execute(
        """INSERT INTO user_hosts(user_id, alias, label, hostname, username, auth_type, secret, default_cwd, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (uid, QA_MACHINE, "QA fixture machine", "qa-box.invalid", "qa", "agent", "", "/srv", ts, ts),
    )
    hid = int(db.fetchone("SELECT id FROM user_hosts WHERE user_id=? AND alias=?", (uid, QA_MACHINE))["id"])
    api = hosts.add_project(uid, hid, {"name": "fuse-api", "remote_path": "/srv/fuse-api", "color": "#0090FF"})
    hosts.add_project(uid, hid, {"name": "marketing-site", "remote_path": "/srv/marketing-site", "color": "#D6409F"})
    hosts.add_project(uid, hid, {"name": "infra", "remote_path": "/srv/infra", "color": "#2BB673", "permission_mode": "read_only"})
    old = hosts.add_project(uid, hid, {"name": "legacy-dashboard", "remote_path": "/srv/legacy-dashboard", "color": "#A18072"})
    hosts.patch_project(uid, hid, old["id"], {"archived": True})
    chats.create_thread(uid, title=FIXTURE_PREFIX + "Project session", model="qa/fixture-model", kind="project",
                        host_id=hid, project_id=api["id"], messages=[
        {"id": _mid(), "role": "user", "content": "Where is the retry policy defined?", "reasoning": "", "error": ""},
        {"id": _mid(), "role": "assistant", "content": "In `app/retry.py`, `delay()` doubles from 0.5s.", "reasoning": "", "error": "", "model": "qa/fixture-model"},
    ])
    print(f"projects: machine {QA_MACHINE} with 4 projects (1 archived)")


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
