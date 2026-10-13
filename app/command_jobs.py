"""Supervised shell command jobs: lifecycle, persistence, completion delivery.

Every run_terminal_command becomes a job. Foreground calls wait a short window
for completion, then hand off a running job id; background:true returns the id
immediately. Terminal results are delivered to the owning run through the same
mechanism as background subagent results (pending_child_results while the owner
is generating, thread-scoped wake injection while it is idle). Thread-scoped
delivery is the main agent's: a subagent's job goes only to that subagent,
and is settled undelivered if the subagent has already finished.

Monitor jobs are thread-owned background watches: stdout (or WebSocket text)
is split into 200ms line batches and delivered as <monitor_event> envelopes
while running; stderr is retained and never emitted as events. Stop of the
spawning turn does not cancel them.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from uuid import uuid4

from . import db
from .async_work import durable_call

logger = logging.getLogger(__name__)

TERMINAL = ("completed", "timed_out", "cancelled", "interrupted", "lost")
JOB_CAP = 64
PER_HOST_CAP = 8
WS_MONITOR_CAP = 8
WAKE_COMMAND_LIMIT = 4
WAKE_MONITOR_LIMIT = 4
OUT_TAIL_CHARS = 16_000
# The first error of a build or test run is usually near the top; keep the
# start of the output as well as the rolling tail (in memory only).
OUT_HEAD_CHARS = 4_000
# Model-visible body of a finished command: head + tail, under the runtime's
# per-result clip (chat_runtime.TOOL_RESULT_CHARS).
MODEL_TAIL_CHARS = 12_000
# The head/tail cut moves to a line boundary when one is this close.
_SNAP_CHARS = 1_000
RESULT_CHARS = 6_000
RUNNING_TAIL_CHARS = 1_200
# Foreground: a command with no timeout gets 2 minutes and may ask for up to
# 10. Reaching it moves the command to the background instead of killing it
# (a command that is only `sleep` is killed). Background: 30 minutes from the
# moment it enters the background, or the requested timeout, up to 2 hours.
FOREGROUND_DEFAULT_MS = 120_000
FOREGROUND_MAX_MS = 600_000
BACKGROUND_DEFAULT_MS = 1_800_000
BACKGROUND_MAX_MS = 7_200_000
# Kept for callers that still ask for "the" command deadline.
DEFAULT_TIMEOUT_MS = FOREGROUND_DEFAULT_MS
MAX_TIMEOUT_MS = BACKGROUND_MAX_MS
# Past the deadline, how long the supervisor still waits for the runner's
# terminal result (connectors return their buffered output right after their
# deadline kill; the agent-call itself allows timeout_ms + 10s).
_RUNNER_GRACE_S = 12.0

_live: dict[str, dict] = {}
_schema_ready = False


class CommandTimedOut(Exception):
    """Raised by executors when the execution deadline expires."""

    def __init__(self, *, termination_confirmed=False):
        self.termination_confirmed = termination_confirmed


def ensure_schema() -> None:
    global _schema_ready
    if _schema_ready:
        return
    with db.tx() as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS command_jobs (
                id TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                host_id INTEGER NOT NULL,
                host TEXT NOT NULL DEFAULT '',
                thread_id INTEGER,
                run_id TEXT,
                command TEXT NOT NULL,
                cwd TEXT NOT NULL DEFAULT '',
                background INTEGER NOT NULL DEFAULT 0,
                timeout_ms INTEGER NOT NULL,
                status TEXT NOT NULL,
                exit_code INTEGER,
                error TEXT NOT NULL DEFAULT '',
                out TEXT NOT NULL DEFAULT '',
                out_bytes INTEGER NOT NULL DEFAULT 0,
                duration_ms INTEGER,
                notify INTEGER NOT NULL DEFAULT 0,
                delivered INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                started_at TEXT,
                finished_at TEXT
            )"""
        )
        columns = {row[1] for row in conn.execute("PRAGMA table_info(command_jobs)")}
        for name, declaration in (
            ("out_chars", "INTEGER"),
            ("output_incomplete", "INTEGER NOT NULL DEFAULT 0"),
            ("termination_confirmed", "INTEGER"),
            ("kind", "TEXT NOT NULL DEFAULT 'command'"),
            ("source", "TEXT NOT NULL DEFAULT ''"),
            ("url", "TEXT NOT NULL DEFAULT ''"),
            ("pattern", "TEXT NOT NULL DEFAULT ''"),
            ("event_count", "INTEGER NOT NULL DEFAULT 0"),
            ("max_events", "INTEGER NOT NULL DEFAULT 1"),
            ("delivered_event_seq", "INTEGER NOT NULL DEFAULT 0"),
            ("description", "TEXT NOT NULL DEFAULT ''"),
        ):
            if name not in columns:
                conn.execute(f"ALTER TABLE command_jobs ADD COLUMN {name} {declaration}")
    _schema_ready = True


def recover() -> None:
    """Startup honesty pass: executors died with the process; never claim running."""
    ensure_schema()
    with db.tx() as conn:
        conn.execute(
            "UPDATE command_jobs SET status='interrupted', error='service restarted',"
            " termination_confirmed=0, output_incomplete=1, finished_at=?"
            " WHERE status IN ('running','cancelling')",
            (db.now(),),
        )


def _prune() -> None:
    if len(_live) < JOB_CAP:
        return
    done = [k for k, v in _live.items() if v.get("status") in TERMINAL]
    for key in done[: max(1, len(_live) - JOB_CAP + 1)]:
        _live.pop(key, None)


def _append_out(rec: dict, text: str) -> None:
    if not text:
        return
    rec["out_bytes"] = int(rec.get("out_bytes") or 0) + len(text.encode("utf-8"))
    seen = int(rec.get("out_chars") or 0)
    if seen < OUT_HEAD_CHARS:
        rec["out_head"] = (rec.get("out_head") or "") + text[: OUT_HEAD_CHARS - seen]
    rec["out_chars"] = seen + len(text)
    # Retain a true rolling tail: the newest text always lands in the buffer,
    # trimmed from the front once it passes the cap.
    buf = (rec.get("out") or "") + text
    rec["out"] = buf if len(buf) <= OUT_TAIL_CHARS else buf[-OUT_TAIL_CHARS:]
    rec["last_out"] = time.monotonic()


def _append_err(rec: dict, text: str) -> None:
    if not text:
        return
    from . import monitor as monitor_mod

    buf = (rec.get("err") or "") + text
    rec["err"] = buf if len(buf) <= monitor_mod.ERR_TAIL_CHARS else buf[-monitor_mod.ERR_TAIL_CHARS:]


def elapsed_ms(rec: dict) -> int:
    return int((time.monotonic() - rec["started"]) * 1000)


def row_start_ms(row) -> int | None:
    """Wall-clock start of a persisted job, epoch ms.

    A reload rebuilds the command row from the database, where the live
    elapsed_ms (a monotonic delta) was never stored, so the ticking clock
    started over. started_at is written when the process starts."""
    try:
        keys = set(row.keys())
    except AttributeError:
        keys = set(row)
    stamp = str((row["started_at"] if "started_at" in keys else "") or (row["created_at"] if "created_at" in keys else "") or "")
    if not stamp:
        return None
    try:
        dt = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def row_elapsed_ms(row) -> int | None:
    started = row_start_ms(row)
    if started is None:
        return None
    return max(0, int(datetime.now(timezone.utc).timestamp() * 1000) - started)


# Slots admitted by launch_async but not yet in _live (its INSERT awaits):
# counted by _admit so concurrent launches cannot all pass the same last slot.
_reserved: dict[str, dict] = {}


def _admit(kind: str, host_id: int, source: str) -> None:
    """In-memory admission caps. Raises ValueError when a cap is hit."""
    _prune()
    live = [*_live.values(), *_reserved.values()]
    running = sum(1 for v in live if v.get("status") not in TERMINAL)
    if running >= JOB_CAP:
        raise ValueError("too many running commands")
    if source == "ws":
        ws_running = sum(
            1
            for v in live
            if v.get("status") not in TERMINAL and v.get("source") == "ws"
        )
        if ws_running >= WS_MONITOR_CAP:
            raise ValueError("too many running websocket monitors")
    elif int(host_id):
        per_host = sum(
            1
            for v in live
            if v.get("host_id") == int(host_id)
            and v.get("status") not in TERMINAL
            and v.get("source") != "ws"
        )
        if per_host >= PER_HOST_CAP:
            raise ValueError("too many running commands on this host")


def clean_description(value) -> str:
    """The model's short label for a command: shown to the user, never run."""
    text = " ".join(str(value or "").split())
    return "".join(ch for ch in text if ch.isprintable())[:200]


def _label_fields(rec: dict) -> dict:
    description = clean_description(rec.get("description"))
    return {"description": description} if description else {}


def _abandon_launch(job_id: str) -> None:
    with db.tx() as conn:
        conn.execute(
            "UPDATE command_jobs SET status='cancelled', error='cancelled before start', finished_at=?,"
            " termination_confirmed=1 WHERE id=? AND status='running'",
            (db.now(), job_id),
        )


def _insert_job_row(
    *, job_id: str, user_id: int, host_id: int, host: str, thread_id, run_id,
    command: str, cwd: str, background: bool, timeout_ms: int, now: str,
    kind: str, source: str, url: str, pattern: str, max_events: int, description: str = "",
) -> None:
    with db.tx() as conn:
        conn.execute(
            "INSERT INTO command_jobs (id,user_id,host_id,host,thread_id,run_id,command,cwd,"
            "background,timeout_ms,status,created_at,started_at,kind,source,url,pattern,event_count,"
            "max_events,delivered_event_seq,description)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                job_id,
                int(user_id),
                int(host_id),
                str(host or ""),
                thread_id,
                str(run_id or "") or None,
                str(command),
                str(cwd or ""),
                1 if background else 0,
                int(timeout_ms),
                "running",
                now,
                now,
                kind,
                source,
                str(url or ""),
                str(pattern or ""),
                0,
                int(max_events),
                0,
                clean_description(description),
            ),
        )
        if thread_id:
            from . import chat_recaps
            chat_recaps.activity(conn, int(user_id), int(thread_id))


def _start_rec(
    *, runner, job_id: str, user_id: int, host_id: int, host: str, thread_id, run_id,
    command: str, cwd: str, background: bool, timeout_ms: int, kind: str,
    source: str, url: str, pattern: str, max_events: int, description: str = "",
) -> dict:
    rec = {
        "id": job_id,
        "user_id": int(user_id),
        "host_id": int(host_id),
        "host": str(host or ""),
        "thread_id": thread_id,
        "run_id": str(run_id or "") or None,
        "command": str(command),
        "description": clean_description(description),
        "cwd": str(cwd or ""),
        "background": bool(background),
        "timeout_ms": int(timeout_ms),
        "status": "running",
        "exit_code": None,
        "error": "",
        "out": "",
        "out_bytes": 0,
        "out_chars": 0,
        "output_incomplete": False,
        "termination_confirmed": None,
        "duration_ms": None,
        "notify": False,
        "started": time.monotonic(),
        "last_out": 0.0,
        "kind": kind,
        "source": source,
        "url": str(url or ""),
        "pattern": str(pattern or ""),
        "event_count": 0,
        "max_events": int(max_events),
        "delivered_event_seq": 0,
        "err": "",
        "pending_events": [],
        "event_seq": 0,
    }
    if kind == "monitor":
        # Monitor event persistence/delivery is ordered through one writer
        # task; _supervise drains it in its finally so a terminal state is
        # never observed before its queued events are durable.
        rec["_write_queue"] = asyncio.Queue()
        rec["notify"] = True
    loop = asyncio.get_running_loop()
    rec["future"] = loop.create_future()
    rec["task"] = asyncio.create_task(_supervise(rec, runner))
    rec["task"].add_done_callback(lambda task: _cancelled_before_start(rec, task))
    _live[job_id] = rec
    return rec


def _set_monitor_notify(job_id: str) -> None:
    with db.tx() as conn:
        conn.execute("UPDATE command_jobs SET notify=1 WHERE id=?", (job_id,))


def _normalize_launch(
    *, kind: str, url: str, command: str, background: bool, max_events: int,
) -> tuple[str, str, bool, int]:
    from . import monitor as monitor_mod

    kind = "monitor" if str(kind or "") == "monitor" else "command"
    if kind == "monitor":
        background = True
    source = "ws" if (kind == "monitor" and url and not command) else "command"
    return kind, source, background, monitor_mod.max_events(max_events)


def launch(
    *,
    user_id: int,
    host_id: int,
    host: str,
    thread_id: int | None,
    run_id: str | None,
    command: str,
    cwd: str,
    background: bool,
    timeout_ms: int,
    runner,
    kind: str = "command",
    url: str = "",
    pattern: str = "",
    max_events: int = 1,
    description: str = "",
) -> dict:
    """Create the job row and start the executor task. Returns the live rec."""
    ensure_schema()
    kind, source, background, max_events = _normalize_launch(
        kind=kind, url=url, command=command, background=background, max_events=max_events
    )
    _admit(kind, host_id, source)
    job_id = uuid4().hex[:12]
    now = db.now()
    _insert_job_row(
        job_id=job_id, user_id=user_id, host_id=host_id, host=host, thread_id=thread_id,
        run_id=run_id, command=command, cwd=cwd, background=background, timeout_ms=timeout_ms,
        now=now, kind=kind, source=source, url=url, pattern=pattern, max_events=max_events,
        description=description,
    )
    if kind == "monitor":
        _set_monitor_notify(job_id)
    return _start_rec(
        runner=runner, job_id=job_id, user_id=user_id, host_id=host_id, host=host,
        thread_id=thread_id, run_id=run_id, command=command, cwd=cwd, background=background,
        timeout_ms=timeout_ms, kind=kind, source=source, url=url, pattern=pattern,
        max_events=max_events, description=description,
    )


async def launch_async(**kwargs) -> dict:
    """Off-loop twin of launch(): the job INSERT runs on a worker thread.

    Same admission order (caps, then the durable row, then the supervisor
    task); the INSERT is an admitted write, so it drains on cancellation.
    """
    ensure_schema()
    kind, source, background, max_events = _normalize_launch(
        kind=kwargs.get("kind", "command"), url=kwargs.get("url", ""),
        command=kwargs.get("command", ""), background=kwargs.get("background", False),
        max_events=kwargs.get("max_events", 1),
    )
    kwargs["background"] = background
    host_id = kwargs.get("host_id") or 0
    _admit(kind, host_id, source)
    job_id = uuid4().hex[:12]
    now = db.now()
    _reserved[job_id] = {"status": "starting", "host_id": int(host_id), "source": source}
    inserted = False
    try:
        await durable_call(
            _insert_job_row,
            job_id=job_id, user_id=kwargs["user_id"], host_id=host_id, host=kwargs["host"],
            thread_id=kwargs.get("thread_id"), run_id=kwargs.get("run_id"),
            command=kwargs["command"], cwd=kwargs.get("cwd", ""), background=background,
            timeout_ms=kwargs["timeout_ms"], now=now, kind=kind, source=source,
            url=kwargs.get("url", ""), pattern=kwargs.get("pattern", ""), max_events=max_events,
            description=kwargs.get("description", ""),
        )
        inserted = True
        if kind == "monitor":
            await durable_call(_set_monitor_notify, job_id)
    except BaseException:
        if inserted:
            # Cancelled between the INSERT and the supervisor: nothing will
            # ever finish this row, so close it instead of leaving a phantom
            # "running" job that cannot be joined or stopped.
            await durable_call(_abandon_launch, job_id)
        raise
    finally:
        _reserved.pop(job_id, None)
    return _start_rec(
        runner=kwargs["runner"], job_id=job_id, user_id=kwargs["user_id"], host_id=host_id,
        host=kwargs["host"], thread_id=kwargs.get("thread_id"), run_id=kwargs.get("run_id"),
        command=kwargs["command"], cwd=kwargs.get("cwd", ""), background=background,
        timeout_ms=kwargs["timeout_ms"], kind=kind, source=source, url=kwargs.get("url", ""),
        pattern=kwargs.get("pattern", ""), max_events=max_events, description=kwargs.get("description", ""),
    )


def _cancelled_before_start(rec: dict, task) -> None:
    # Cancellation before the first step never enters _supervise's finally.
    if task.cancelled() and rec.get("status") not in TERMINAL:
        rec["termination_confirmed"] = True  # the executor never started
        # In-memory state lands synchronously so observers of the finished
        # task already see a terminal status; only the writes are deferred.
        _apply_finish(rec, status="cancelled", exit_code=None, error="cancelled by request")

        async def _finish_cancelled():
            await _persist_terminal(rec, status="cancelled", exit_code=None, error=rec["error"])
            if rec.get("notify"):
                try:
                    await _deliver(rec)
                except Exception:
                    logger.exception("Failed to deliver command job %s", rec["id"])
            fut = rec["future"]
            if not fut.done():
                fut.set_result(rec["status"])

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(_finish_cancelled())


def _is_monitor(rec: dict) -> bool:
    return str(rec.get("kind") or "") == "monitor"


def _persist_monitor_count(rec: dict) -> None:
    ensure_schema()
    try:
        with db.tx() as conn:
            conn.execute(
                "UPDATE command_jobs SET event_count=? WHERE id=?",
                (int(rec.get("event_count") or 0), rec["id"]),
            )
    except Exception:
        logger.exception("Failed to persist monitor event count %s", rec["id"])


def _on_monitor_batch(rec: dict, lines: list[str], overflow: int = 0) -> None:
    from . import monitor as monitor_mod

    if rec.get("status") in TERMINAL or rec.get("status") == "cancelling":
        return
    matched = [ln for ln in lines if not str(ln).startswith("… ")]
    added = len(matched) + max(0, int(overflow or 0))
    rec["event_count"] = int(rec.get("event_count") or 0) + added
    rec["event_seq"] = int(rec.get("event_seq") or 0) + 1
    seq = int(rec["event_seq"])
    envelope = monitor_mod.event_envelope(rec, lines, seq)
    rec["_last_event"] = envelope
    rec["_last_event_lines"] = list(lines)
    rec["_last_event_at"] = time.monotonic()
    # Sink callbacks run synchronously on the event loop (SSH drain tasks,
    # connector chunk frames). Persistence and delivery are ordered through
    # the per-job writer task instead of blocking the loop here.
    queue = rec.get("_write_queue")
    if queue is not None:
        queue.put_nowait((envelope, seq))
    cap = int(rec.get("max_events") or monitor_mod.DEFAULT_MAX_EVENTS)
    if int(rec.get("event_count") or 0) >= cap:
        rec["_max_events_hit"] = True
        task = rec.get("task")
        if task is not None and not task.done():
            task.cancel()
        return
    batcher = rec.get("batcher")
    if batcher is not None and batcher.firehose():
        rec["_firehose"] = True
        rec["error"] = "monitor auto-stopped: too many events"
        task = rec.get("task")
        if task is not None and not task.done():
            task.cancel()


def _wrap_monitor_sink(rec: dict, sink):
    from . import monitor as monitor_mod

    rec["_pattern"] = monitor_mod.compile_pattern(rec.get("pattern") or "")
    batcher = monitor_mod.LineBatcher(rec, lambda lines, overflow=0: _on_monitor_batch(rec, lines, overflow))
    rec["batcher"] = batcher
    rec["watch"] = None

    def wrapped(text: str) -> None:
        _append_out(rec, text)
        batcher.feed(text)

    def err(text: str) -> None:
        _append_err(rec, text)

    def incomplete(value=True) -> None:
        rec["output_incomplete"] = bool(value)

    wrapped.incomplete = incomplete
    wrapped.err = err
    original_incomplete = getattr(sink, "incomplete", None)
    if original_incomplete:
        wrapped.incomplete = incomplete
    return wrapped, batcher


async def _monitor_writer(rec: dict) -> None:
    """Ordered, off-loop persistence/delivery for one monitor's events."""
    queue: asyncio.Queue = rec["_write_queue"]
    while True:
        item = await queue.get()
        if item is None:
            return
        envelope, seq = item
        try:
            await asyncio.to_thread(_persist_monitor_count, rec)
            await _deliver_monitor_event(rec, envelope, seq)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Monitor event write failed for job %s", rec["id"])


async def _supervise(rec: dict, runner) -> None:
    job_id = rec["id"]

    def sink(text: str) -> None:
        _append_out(rec, text)

    def incomplete(value=True) -> None:
        # A connector supplied only a prefix/partial output, not a stream tail.
        rec["output_incomplete"] = bool(value)

    sink.incomplete = incomplete
    batcher = None
    writer = None
    if _is_monitor(rec):
        sink, batcher = _wrap_monitor_sink(rec, sink)
        writer = asyncio.create_task(_monitor_writer(rec))
        rec["_writer"] = writer

    async def drain_writer() -> None:
        # Deliver queued monitor events (and nothing more), then stop the
        # writer. Must run before the terminal state is set: delivery drops
        # events for a finished job, which would lose the final match.
        if writer is None or writer.done():
            return
        rec["_write_queue"].put_nowait(None)
        try:
            await writer
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("Monitor writer failed to drain for job %s", rec["id"])

    async def finish(**kwargs) -> None:
        await drain_writer()
        await _finish(rec, **kwargs)

    try:
        # Command runners enforce their own kill — the connector kills at the
        # deadline it was given and returns its buffered output — so this waits
        # a grace window for that result instead of cancelling at the exact
        # deadline and losing the output already captured. A command moved to
        # the background gets a second, shorter limit counted from the move.
        # Monitors are cut at their deadline: their events stream live.
        grace = 0.0 if _is_monitor(rec) else _RUNNER_GRACE_S
        # The runner enforces its own kill and needs the grace window to return
        # the output it captured. A moved command's background limit, armed
        # before this wait, ends it sooner.
        limit = rec.get("background_deadline")
        outer = rec["timeout_ms"] / 1000 + grace
        if limit is not None:
            outer = min(outer, max(0.0, limit - time.monotonic()))
        async with asyncio.timeout(outer) as deadline:
            rec["_deadline"] = deadline  # arm_background may move it earlier
            result = await runner(sink)
        if batcher is not None:
            await batcher.close()
        code = getattr(result, "exit_status", None)
        code = int(code) if isinstance(code, int) else None
        combined = str(getattr(result, "output", "") or "")
        if combined and not rec.get("out"):
            _append_out(rec, combined)
            if batcher is not None:
                batcher.feed(combined)
                await batcher.close()
        if rec.get("_firehose"):
            rec["termination_confirmed"] = True
            await finish(status="cancelled", exit_code=None, error=str(rec.get("error") or "monitor auto-stopped: too many events"))
            return
        if rec.get("_max_events_hit"):
            rec["termination_confirmed"] = True
            await finish(status="completed", exit_code=0, error=str(rec.get("error") or ""))
            return
        if _is_monitor(rec) and rec.get("source") == "ws":
            rec["termination_confirmed"] = True
            await finish(status="completed", exit_code=0, error="")
            return
        if code is None or code < 0:
            raise RuntimeError("command ended without a normal exit status; remote outcome is unknown")
        rec["termination_confirmed"] = code is not None
        await finish(
            status="completed",
            exit_code=code,
            error="",
        )
    except asyncio.CancelledError:
        # Uncancel before the terminal writes: a cancellation landing on one
        # of the awaited persists must not skip the job's final state.
        current = asyncio.current_task()
        if current is not None and hasattr(current, "uncancel"):
            while current.uncancel():
                pass
        if batcher is not None:
            await batcher.close()
        rec["termination_confirmed"] = False
        rec["output_incomplete"] = True
        error = str(rec.get("error") or "")
        if rec.get("_firehose"):
            rec["termination_confirmed"] = True
            await finish(status="cancelled", exit_code=None, error=error or "monitor auto-stopped: too many events")
        elif rec.get("_max_events_hit"):
            rec["termination_confirmed"] = True
            await finish(status="completed", exit_code=0, error=error)
        else:
            await finish(status="cancelled", exit_code=None,
                          error="cancellation requested; remote termination is not confirmed")
        return
    except (CommandTimedOut, TimeoutError) as exc:
        if batcher is not None:
            await batcher.close()
        rec["termination_confirmed"] = bool(getattr(exc, "termination_confirmed", False))
        if not rec["termination_confirmed"]:
            rec["output_incomplete"] = True
        extra = ""
        if _is_monitor(rec):
            extra = f"; {int(rec.get('event_count') or 0)} events"
        label = "monitor" if _is_monitor(rec) else "command"
        if rec.get("background_deadline") is not None and not _is_monitor(rec):
            description = rec.get("description") or str(rec.get("command") or "")[:80]
            error = f'Background command "{description}" was stopped after reaching its background time limit'
        else:
            error = f"{label} exceeded its {int(rec['timeout_ms'])}ms execution deadline; termination requested{extra}"
        await finish(status="timed_out", exit_code=None, error=error)
    except Exception as exc:
        if batcher is not None:
            await batcher.close()
        rec["output_incomplete"] = True
        text = str(exc) or exc.__class__.__name__
        low = text.lower()
        status = "timed_out" if "timed out" in low else "interrupted"
        await finish(status=status, exit_code=None, error=text[:400])
    finally:
        if batcher is not None:
            try:
                await batcher.close()
            except Exception:
                pass
        await drain_writer()
        fut: asyncio.Future = rec["future"]
        if not fut.done():
            fut.set_result(rec["status"])
        task = rec.get("watch")
        if task is not None and not task.done():
            task.cancel()


def _apply_finish(rec: dict, *, status: str, exit_code: int | None, error: str) -> None:
    if rec.get("termination_confirmed") is None:
        rec["termination_confirmed"] = False
    duration = int((time.monotonic() - rec["started"]) * 1000)
    if status == "cancelled" and rec.get("user_cancelled"):
        error = "Stopped by the user. " + error
    rec.update(
        {
            "status": status,
            "exit_code": exit_code,
            "error": error,
            "duration_ms": duration,
            "out": (rec.get("out") or "")[-OUT_TAIL_CHARS:],
        }
    )


def _persist_finish(rec: dict, *, status: str, exit_code: int | None, error: str, duration: int) -> bool:
    ensure_schema()
    try:
        with db.tx() as conn:
            # Cancellation is persisted as nonterminal until executor cleanup.
            # Accept legacy 'cancelled' rows too; retain produced output.
            conn.execute(
                "UPDATE command_jobs SET status=?, exit_code=?, error=?, `out`=?, out_bytes=?,"
                " duration_ms=?, finished_at=?, out_chars=?, output_incomplete=?, termination_confirmed=?,"
                " event_count=?, max_events=?"
                " WHERE id=? AND status IN ('running','cancelling','cancelled')",
                (
                    status,
                    exit_code,
                    error[:400],
                    rec["out"],
                    int(rec.get("out_bytes") or 0),
                    duration,
                    db.now(),
                    rec.get("out_chars"),
                    int(bool(rec.get("output_incomplete"))),
                    rec.get("termination_confirmed"),
                    int(rec.get("event_count") or 0),
                    int(rec.get("max_events") or 1),
                    rec["id"],
                ),
            )
            if rec.get("thread_id"):
                from . import chat_recaps
                chat_recaps.terminal_activity(conn, int(rec["user_id"]), int(rec["thread_id"]))
        return True
    except Exception:
        logger.exception("Failed to persist command job %s", rec["id"])
        return False


# A lost terminal write leaves the row 'running'; the next restart's recover()
# would then record a finished command as interrupted and drop its result.
PERSIST_RETRY_DELAYS = (1.0, 3.0, 10.0, 30.0)


async def _persist_terminal(rec: dict, *, status: str, exit_code: int | None, error: str) -> None:
    # Through a worker thread so the loop never blocks on the pool.
    for delay in (*PERSIST_RETRY_DELAYS, None):
        if await asyncio.to_thread(
            _persist_finish, rec, status=status, exit_code=exit_code, error=error,
            duration=int(rec.get("duration_ms") or 0),
        ) or delay is None:
            return
        await asyncio.sleep(delay)


async def _finish(rec: dict, *, status: str, exit_code: int | None, error: str) -> None:
    _apply_finish(rec, status=status, exit_code=exit_code, error=error)
    error = rec["error"]
    # In-memory state lands first (render/job_status read it immediately).
    await _persist_terminal(rec, status=status, exit_code=exit_code, error=error)
    if rec.get("notify"):
        try:
            await _deliver(rec)
        except Exception:
            logger.exception("Failed to deliver command job %s", rec["id"])
    if status == "cancelled" and not _is_monitor(rec) and not rec.get("user_cancelled"):
        await asyncio.to_thread(_mark_delivered, [str(rec["id"])])


def _child_run_state(run_id) -> str | None:
    """'active' / 'finished' for a job a subagent started; None for the main
    agent's (or an unowned) job. Thread-scoped delivery (wake turns, the next
    user turn) belongs to the main agent only: a subagent's command result
    must never surface there as if the main agent had started it."""
    if not run_id:
        return None
    row = db.fetchone("SELECT parent_run_id, status FROM chat_runs WHERE id=?", (str(run_id),))
    if row is None or not row["parent_run_id"]:
        return None
    return "active" if row["status"] in ("queued", "running") else "finished"


async def _deliver(rec: dict) -> None:
    """Hand a terminal result to the owning run (active) or thread (idle).

    Delivery is acknowledged only when the envelope becomes durable: either
    the parent run's transcript receipt (save_turn_progress needle check,
    via the ids the runtime attaches to the injection) or a committed wake
    turn. Queueing into a live run marks nothing — if that run dies before
    consuming it, the job stays delivered=0 and the wake path still delivers.
    """
    if rec.get("status") not in TERMINAL:
        return
    # kill_command already answered the model; a cancelled receipt is noise.
    # Monitor stop still reports the event count so the model can re-arm.
    if rec.get("status") == "cancelled" and not _is_monitor(rec) and not rec.get("user_cancelled"):
        return
    run_id = rec.get("run_id")
    queued = False
    if run_id:
        from . import chat_store

        envelope = notification(rec)
        try:
            queued = await asyncio.to_thread(
                chat_store.queue_child_result, str(run_id), envelope, child_id=str(rec["id"])
            )
        except Exception:
            logger.exception("Queue command result failed for job %s", rec["id"])
    if await asyncio.to_thread(_child_run_state, run_id) is not None:
        if not queued:
            # The subagent that started it has finished: nobody is waiting.
            # The result stays readable in the jobs panel and by id.
            logger.info("Command job %s outlived its subagent %s; not delivered", rec["id"], run_id)
            await asyncio.to_thread(_mark_delivered, [str(rec["id"])])
        return
    thread_id = rec.get("thread_id")
    if thread_id:
        try:
            from .chat_runs import schedule_wake

            schedule_wake(int(rec["user_id"]), thread_id)
        except Exception:
            logger.exception("Schedule command wake failed for job %s", rec["id"])


async def _deliver_monitor_event(rec: dict, envelope: str, seq: int) -> None:
    """Running-state, multi-fire delivery: each batched stdout event can wake the model.

    Live parent: stash on pending_child_results. Idle parent: keep the envelope
    on the job until a wake turn embeds it. Never both — that would duplicate.
    """
    if rec.get("status") in TERMINAL:
        return
    from . import monitor as monitor_mod

    run_id = rec.get("run_id")
    queued = False
    if run_id:
        from . import chat_store

        try:
            queued = await asyncio.to_thread(
                chat_store.queue_child_result,
                str(run_id), envelope, child_id=monitor_mod.event_receipt_id(rec["id"], seq),
            )
        except Exception:
            logger.exception("Queue monitor event failed for job %s", rec["id"])
    if queued:
        return
    if await asyncio.to_thread(_child_run_state, run_id) is not None:
        # A finished subagent's watch has no reader; never wake the main agent.
        return
    pending = rec.setdefault("pending_events", [])
    pending.append({"seq": int(seq), "envelope": envelope})
    if len(pending) > monitor_mod.MAX_PENDING_EVENTS:
        rec["pending_events"] = pending[-monitor_mod.MAX_PENDING_EVENTS:]
    thread_id = rec.get("thread_id")
    if thread_id:
        try:
            from .chat_runs import schedule_wake

            schedule_wake(int(rec["user_id"]), thread_id)
        except Exception:
            logger.exception("Schedule monitor wake failed for job %s", rec["id"])


def _apply_delivery(conn, ids: list[str]):
    """Mark ids delivered in conn's transaction. Returns the matching prune of
    live jobs' pending monitor events, to run only once that commits: pruned
    early, a rolled-back receipt would drop events no wake ever delivers."""
    from . import monitor as monitor_mod

    jobs: list[str] = []
    events: dict[str, int] = {}
    for raw in ids or []:
        ident = str(raw or "")
        parsed = monitor_mod.parse_event_receipt(ident)
        if parsed:
            job_id, seq = parsed
            events[job_id] = max(events.get(job_id, 0), seq)
        elif ident:
            jobs.append(ident)
    if jobs:
        conn.executemany(
            "UPDATE command_jobs SET delivered=1 WHERE id=?", [(i,) for i in jobs]
        )
    for job_id, seq in events.items():
        conn.execute(
            "UPDATE command_jobs SET delivered_event_seq=? WHERE id=? AND delivered_event_seq<?",
            (int(seq), job_id, int(seq)),
        )

    def settle():
        for job_id, seq in events.items():
            rec = _live.get(job_id)
            if rec is None:
                continue
            rec["delivered_event_seq"] = max(int(rec.get("delivered_event_seq") or 0), int(seq))
            rec["pending_events"] = [
                item for item in rec.get("pending_events") or []
                if not (isinstance(item, dict) and int(item.get("seq") or 0) <= seq)
            ]

    return settle


def _mark_delivered(ids: list[str]) -> None:
    if not ids:
        return
    ensure_schema()
    with db.tx() as conn:
        settle = _apply_delivery(conn, ids)
    settle()


def mark_delivered(ids: list[str], conn=None) -> None:
    """Public delivery receipt for wake turns that embed envelopes in their prompt.
    With conn (an open chat_tx.transaction) the receipt joins that transaction."""
    if conn is not None:
        if ids:
            from . import chat_tx

            chat_tx.after_commit(_apply_delivery(conn, ids))
        return
    _mark_delivered(ids)


def notification(rec: dict) -> str:
    if _is_monitor(rec):
        from . import monitor as monitor_mod

        return monitor_mod.result_envelope(rec)
    status = str(rec.get("status") or "")
    lines = [f'<command_result id="{rec["id"]}" state="{status}">']
    lines.append(f'host: {rec.get("host") or ""} command: {str(rec.get("command") or "")[:200]}')
    if _label_fields(rec):
        lines.append(f"description: {_label_fields(rec)['description']}")
    if status == "completed":
        code = rec.get("exit_code")
        lines.append(f"exit_code: {code if code is not None else 'unknown'}")
    lines.append(f"duration_ms: {rec.get('duration_ms')}")
    if rec.get("error"):
        lines.append(f"error: {str(rec['error'])[:200]}")
    fields = output_fields(rec)
    out = fields["output"]
    lines.append(f"output_chars: {fields['output_chars']}")
    lines.append(f"output_chars_exact: {str(fields['output_chars_exact']).lower()}")
    if fields["output_mode"] == "tail" or len(out) > 1500:
        lines.append("output: partial (total unknown)" if fields["output_mode"] == "partial" else "output: retained tail")
    lines.append("")
    lines.append(out[-1500:] if out else "(no output)")
    lines.append("</command_result>")
    return "\n".join(lines)


def pending_wake_threads() -> list[dict]:
    """Lightweight discovery for command-only capacity-deferred wakes."""
    ensure_schema()
    threads = {(row["user_id"], row["thread_id"]) for row in db.fetchall(
        "SELECT DISTINCT user_id,thread_id FROM command_jobs WHERE thread_id IS NOT NULL AND notify=1"
        " AND delivered=0 AND status IN ('completed','timed_out','cancelled','interrupted','lost')"
        " AND NOT EXISTS (SELECT 1 FROM chat_runs r WHERE r.id=command_jobs.run_id AND r.parent_run_id IS NOT NULL)"
    )}
    for rec in list(_live.values()):
        if not rec.get("thread_id") or not _is_monitor(rec) or rec.get("status") in TERMINAL:
            continue
        if _child_run_state(rec.get("run_id")) is not None:
            continue
        acked = int(rec.get("delivered_event_seq") or 0)
        if any(not isinstance(item, dict) or int(item.get("seq") or 0) > acked for item in rec.get("pending_events") or []):
            threads.add((rec["user_id"], rec["thread_id"]))
    return [{"user_id": uid, "thread_id": tid, "resumed_at": ""} for uid, tid in threads]


def pending_envelopes(user_id: int, thread_id: int) -> list[dict]:
    """Undelivered terminal results and live monitor events (peek; wake builds text from these)."""
    ensure_schema()
    rows = db.fetchall(
        "SELECT * FROM command_jobs WHERE user_id=? AND thread_id=? AND notify=1"
        " AND delivered=0 AND status IN ('completed','timed_out','cancelled','interrupted','lost')"
        " AND NOT EXISTS (SELECT 1 FROM chat_runs r WHERE r.id=command_jobs.run_id AND r.parent_run_id IS NOT NULL)"
        " ORDER BY finished_at",
        (int(user_id), int(thread_id)),
    )
    commands: list[dict] = []
    monitors: list[dict] = []
    for row in rows:
        rec = dict(row)
        item = {"id": rec["id"], "envelope": notification(rec), "result": render(rec, tool="get_command_output")[1]}
        if _is_monitor(rec):
            monitors.append(item)
        else:
            commands.append(item)
    for rec in _live.values():
        if rec.get("user_id") != int(user_id) or rec.get("thread_id") != int(thread_id):
            continue
        if not _is_monitor(rec) or rec.get("status") in TERMINAL:
            continue
        if _child_run_state(rec.get("run_id")) is not None:
            continue
        acked = int(rec.get("delivered_event_seq") or 0)
        pending = list(rec.get("pending_events") or [])
        for item in pending:
            if isinstance(item, dict):
                seq = int(item.get("seq") or 0)
                envelope = str(item.get("envelope") or "")
            else:
                envelope = str(item)
                seq = 0
            if not envelope or seq <= acked:
                continue
            from . import monitor as monitor_mod

            monitors.append({
                "id": monitor_mod.event_receipt_id(rec["id"], seq),
                "envelope": envelope,
                "result": render(rec, tool="monitor")[1],
            })
    return commands[:WAKE_COMMAND_LIMIT] + monitors[:WAKE_MONITOR_LIMIT]


def take_thread_results(user_id: int, thread_id: int) -> list[str]:
    """Peek undelivered terminal results for injection into a live parent turn.

    Purely non-destructive: the caller must persist the envelopes and pass
    their ids via save_turn_progress(extra={"command_delivery_ids": ...});
    the needle-checked receipt there is the only thing that marks delivery.
    """
    items = pending_envelopes(user_id, thread_id)
    return [item["envelope"] for item in items]


def _row_rec(row) -> dict | None:
    if row is None:
        return None
    return dict(row)


def _get(job_id: str, user_id: int) -> dict | None:
    rec = _live.get(job_id)
    if rec is not None:
        if rec.get("user_id") != int(user_id):
            return None
        return rec
    ensure_schema()
    row = db.fetchone(
        "SELECT * FROM command_jobs WHERE id=? AND user_id=?", (str(job_id), int(user_id))
    )
    return _row_rec(row)


def _get_scoped(job_id: str, user_id: int, thread_id=None) -> dict | None:
    """A job as seen from one chat: agents cannot join or kill another
    thread's commands by id."""
    rec = _get(job_id, user_id)
    if rec is None or thread_id is None:
        return rec
    owner = rec.get("thread_id")
    if owner is not None and str(owner) != str(thread_id):
        return None
    return rec


def mark_notified(rec: dict) -> None:
    """The running envelope reached the model: terminal results must be delivered.

    If the job completed between the join timeout and this handoff, the terminal
    result is returned inline instead; suppress the automatic notification.
    """
    if rec.get("status") in TERMINAL:
        _mark_delivered([str(rec["id"])])
        return
    rec["notify"] = True
    ensure_schema()
    with db.tx() as conn:
        conn.execute("UPDATE command_jobs SET notify=1 WHERE id=?", (str(rec["id"]),))


def mark_background(rec: dict) -> None:
    """A foreground command handed off past the wait window runs as background."""
    rec["background"] = True
    ensure_schema()
    with db.tx() as conn:
        conn.execute("UPDATE command_jobs SET background=1 WHERE id=?", (str(rec["id"]),))


def arm_background(rec: dict, limit_ms: int = BACKGROUND_DEFAULT_MS) -> None:
    """Start the background limit now. A moved command's 30 minutes count from
    the move, not from when it was launched."""
    rec["background"] = True
    rec["background_deadline"] = time.monotonic() + max(1, int(limit_ms)) / 1000
    deadline = rec.get("_deadline")
    if deadline is not None:
        # The supervisor's wait already started with the launch budget: pull
        # it in so the job ends at the limit counted from this move.
        try:
            when = asyncio.get_running_loop().time() + max(1, int(limit_ms)) / 1000
        except RuntimeError:
            when = None
        if when is not None and (deadline.when() is None or when < deadline.when()):
            deadline.reschedule(when)
    mark_background(rec)


def call_timeout_ms(rec: dict) -> int | None:
    """The deadline the row should name, not the kill budget stored on the job.

    A foreground command's stored timeout is its wait plus the 30 minutes it is
    handed when it moves to the background; the row shows the wait, which is
    what expires next. Once it has moved, or it started in the background, the
    stored timeout is the deadline. A monitor's stored timeout is its watch.
    """
    timeout = int(rec.get("timeout_ms") or 0)
    if not timeout:
        return None
    if rec.get("background") or _is_monitor(rec):
        return timeout
    return max(1_000, timeout - BACKGROUND_DEFAULT_MS)


def output_fields(rec: dict) -> dict:
    """Authoritative retained output, independent of model/preview clipping.

    Counts are Unicode characters, not bytes. Unknown remote/legacy totals are
    lower bounds (output_chars_exact=false); that is not the same as clipping.
    Partial output is NEVER called a stream tail. output_start is a zero-based
    character offset when known.
    """
    output = str(rec.get("out") or "")
    known = rec.get("out_chars") is not None and not rec.get("output_incomplete")
    total = max(len(output), int(rec.get("out_chars") or 0))
    truncated = total > len(output)
    mode = "partial" if not known else ("tail" if truncated else "full")
    return {
        "output": output,
        "output_chars": total,
        "output_chars_exact": bool(known),
        "output_retained_chars": len(output),
        "truncated": truncated,
        "output_mode": mode,
        "output_start": total - len(output) if known else None,
    }


def _identity_fields(rec: dict) -> dict:
    ident = str(rec["id"])
    confirmed = rec.get("termination_confirmed")
    return {
        "id": ident, "command_id": ident, "child_id": ident,
        "state": str(rec.get("status") or "running"),
        "background": bool(rec.get("background")),
        "termination_confirmed": None if confirmed is None else bool(confirmed),
        **output_fields(rec),
    }


def _monitor_trace_fields(rec: dict) -> dict:
    source = str(rec.get("source") or ("ws" if rec.get("url") else "command"))
    url = str(rec.get("url") or "")[:2000]
    pattern = str(rec.get("pattern") or "")[:200]
    extra = {
        "source": source,
        "event_count": int(rec.get("event_count") or 0),
        "url": url,
        "pattern": pattern,
        "max_events": int(rec.get("max_events") or 1),
    }
    if rec.get("err"):
        extra["summary"] = f"{extra['event_count']} events · stderr retained"
    return extra


def _model_body(rec: dict, out: str) -> str:
    """Head + tail of a finished command's output, with an explicit gap marker.

    Only the tail is persisted; a job reloaded from the database (no head in
    memory) shows its retained tail as before.
    """
    if not out:
        return "(no output)"
    fields = output_fields(rec)
    total = int(fields["output_chars"])
    head = str(rec.get("out_head") or "")
    tail = out[-MODEL_TAIL_CHARS:]
    tail_start = total - len(tail)
    # Counts cover the text Fuse received, so head and tail line up even when
    # the host dropped some of it (inexact total); that only adds the label.
    if head and tail_start > len(head):
        # Cut on line boundaries so neither side ends or starts mid-line; the
        # two newlines become the gap marker's own line breaks. A long line
        # (no newline nearby) keeps the character cut.
        head_end = len(head)
        cut = head.rfind("\n")
        if cut >= 0 and len(head) - cut <= _SNAP_CHARS:
            head, head_end = head[:cut], cut + 1
        nl = tail.find("\n")
        if 0 <= nl < _SNAP_CHARS:
            tail, tail_start = tail[nl + 1:], tail_start + nl
        omitted = tail_start - head_end
        label = "" if fields["output_chars_exact"] else "[Output truncated; total unknown]\n"
        return f"{label}{head}\n[... {omitted} characters of output omitted ...]\n{tail}"
    if head and tail_start >= 0:
        # Head and tail overlap or touch: the whole output is known.
        return head + tail[len(head) - tail_start:] if len(head) > tail_start else head + tail
    # Label only when text is actually missing from what is shown: the
    # retained buffer is a trimmed tail, or the display cut it further. A
    # short connector result flagged incomplete is shown as-is.
    if fields["output_mode"] == "tail" or len(out) > len(tail):
        label = "showing the last part" if fields["output_chars_exact"] else "total unknown"
        return f"[Output truncated; {label}]\n" + tail
    return tail


def render(rec: dict, *, tool: str = "run_terminal_command", host_label: bool = True) -> tuple[str, dict]:
    """Model-visible envelope + UI trace for a job in its current state."""
    status = str(rec.get("status") or "running")
    host = str(rec.get("host") or "")
    timeout_ms = int(rec.get("timeout_ms") or 0)
    out = str(rec.get("out") or "")
    name = "monitor" if _is_monitor(rec) and tool in ("run_terminal_command", "monitor") else tool
    head = f"From host {host}:\n" if host_label and host else ""
    if status in TERMINAL:
        # Only what the model can act on: an id it was handed earlier (a
        # background job or monitor), and the limit only when it caused the end.
        meta = [f"status: {status}"]
        if rec.get("background") or _is_monitor(rec):
            meta.append(f"id: {rec['id']}")
        if status == "completed":
            code = rec.get("exit_code")
            meta.append(f"exit_code: {code if code is not None else 'unknown'}")
        if rec.get("duration_ms") is not None:
            meta.append(f"duration_ms: {int(rec['duration_ms'])}")
        if timeout_ms and status == "timed_out":
            meta.append(f"timeout_ms: {timeout_ms}")
        if rec.get("error"):
            meta.append(f"error: {str(rec['error'])[:300]}")
        if _is_monitor(rec):
            meta.append(f"events: {int(rec.get('event_count') or 0)}")
            source = rec.get("source") or "command"
            meta.append(f"source: {source}")
            if rec.get("url"):
                meta.append(f"url: {str(rec.get('url') or '')[:200]}")
        body = _model_body(rec, out)
        err = str(rec.get("err") or "").strip()
        if _is_monitor(rec) and err:
            body = body + "\n\n[stderr]\n" + err[-800:]
        text = f"{head}{chr(10).join(meta)}\n\n{body}"
        trace_status = "done" if status == "completed" else ("interrupted" if status in ("cancelled", "interrupted", "lost") else "error")
        preview = out.strip()[:800] or status
        if _is_monitor(rec):
            preview = f"{int(rec.get('event_count') or 0)} events" + (f" · {status}" if status != "completed" else "")
        trace = {
            "name": name,
            "status": trace_status,
            "host": host,
            "command": str(rec.get("command") or "")[:8000],
            "cwd": str(rec.get("cwd") or "")[:400],
            "exit_code": rec.get("exit_code") if isinstance(rec.get("exit_code"), int) else None,
            "duration_ms": rec.get("duration_ms") if isinstance(rec.get("duration_ms"), int) else None,
            "execution_duration_ms": rec.get("duration_ms") if isinstance(rec.get("duration_ms"), int) else None,
            "timeout_ms": timeout_ms or None,
            "preview": preview,
            "error": "" if status == "completed" else str(rec.get("error") or "")[:400],
        }
        if _is_monitor(rec):
            trace.update(_monitor_trace_fields(rec))
        if trace_status == "error" and status == "timed_out":
            trace["error"] = trace["error"] or f"timed out after {timeout_ms}ms"
        trace.update(_identity_fields(rec))
        trace.update(_label_fields(rec))
        return text, trace
    elapsed = int((time.monotonic() - float(rec["started"])) * 1000) if rec.get("started") else 0
    tail = out.strip()[-RUNNING_TAIL_CHARS:]
    lines = [f"status: {status}", f"id: {rec['id']}", f"elapsed_ms: {elapsed}", f"timeout_ms: {timeout_ms}"]
    if rec.get("background"):
        lines.insert(1, "background: true")
    if _is_monitor(rec):
        lines.append(f"events: {int(rec.get('event_count') or 0)}")
        lines.append(f"source: {rec.get('source') or 'command'}")
        if rec.get("url"):
            lines.append(f"url: {str(rec.get('url') or '')[:200]}")
        if rec.get("pattern"):
            lines.append(f"pattern: {str(rec.get('pattern') or '')[:120]}")
    text = f"{head}{chr(10).join(lines)}"
    if tail:
        text += f"\n\nrecent output:\n{tail}"
    err = str(rec.get("err") or "").strip()
    if _is_monitor(rec) and err:
        text += f"\n\n[stderr]\n{err[-800:]}"
    if status == "cancelling":
        text += "\nCancellation requested; remote termination is not yet confirmed."
    elif _is_monitor(rec):
        text += (
            "\nMatching stdout lines are injected as <monitor_event>. "
            "stderr is retained here and is not an event. Do not sleep, poll, or re-run this monitor."
        )
    else:
        text += (
            "\nCompletion is reported automatically; join with get_command_output only"
            " when you need the result now. Do not re-run this command."
        )
    trace = {
        "name": name,
        "status": "running",
        "host": host,
        "command": str(rec.get("command") or "")[:8000],
        "cwd": str(rec.get("cwd") or "")[:400],
        "child_id": str(rec.get("id") or ""),
        "elapsed_ms": elapsed,
        "timeout_ms": call_timeout_ms(rec),
        "background": bool(rec.get("background")),
        "preview": tail[:800],
        "error": "",
    }
    if _is_monitor(rec):
        n = int(rec.get("event_count") or 0)
        trace["preview"] = f"{n} events" + (f" · {tail[:200]}" if tail else "")
        trace.update(_monitor_trace_fields(rec))
    trace.update(_identity_fields(rec))
    trace.update(_label_fields(rec))
    return text, trace


_NOT_FOUND = "job not found (command jobs only; if this is a subagent id, use get_subagent_output)"


def _acknowledge(rec: dict) -> None:
    """An inline terminal receipt supersedes automatic delivery."""
    run_id = rec.get("run_id")
    if run_id:
        try:
            from . import chat_store

            chat_store.drop_pending_child_result(str(run_id), str(rec["id"]))
        except Exception:
            pass
    _mark_delivered([str(rec["id"])])


async def join(job_id: str, user_id: int, wait_s: float, *, thread_id=None, run_id=None) -> tuple[str, dict]:
    """Wait for a job up to wait_s; returns a terminal or running envelope.

    With run_id, the wait also ends when anything else arrives in that run's
    inbox (run_inbox): the trace then carries early_return so the caller can
    say why it is still running.
    """
    rec = _get_scoped(str(job_id or ""), int(user_id), thread_id)
    if rec is None:
        return _NOT_FOUND, {
            "name": "get_command_output",
            "status": "error",
            "error": "not found",
            "child_id": str(job_id or ""),
        }
    inspect_only = _is_monitor(rec) and rec.get("status") not in TERMINAL
    wait_for = 0.0 if inspect_only else wait_s
    arrival = ""
    if rec.get("status") not in TERMINAL and wait_for > 0:
        fut = rec.get("future")
        if fut is not None and not fut.done():
            from . import run_inbox

            # Never cancels the job; CancelledError propagates: Stop must
            # interrupt the join.
            _finished, arrival = await run_inbox.wait_or_arrival(
                asyncio.shield(fut), wait_for, run_id, ignore={str(rec["id"])}
            )
    if rec.get("status") in TERMINAL:
        # An explicit join is a delivery; suppress the automatic notification.
        _acknowledge(rec)
        return render(rec, tool="get_command_output")
    text, trace = render(rec, tool="get_command_output")
    if inspect_only and wait_s > 0:
        text += "\nEvents are injected automatically; waiting does not apply to a live monitor."
    # A join's running trace otherwise names the command's own deadline. The
    # row shows how long this wait itself will sit before it gives up.
    if not inspect_only and wait_for > 0:
        trace["timeout_ms"] = int(wait_for * 1000)
    if arrival:
        trace["early_return"] = True
    return text, trace


def kill(job_id: str, user_id: int, *, thread_id=None) -> tuple[str, dict]:
    rec = _get_scoped(str(job_id or ""), int(user_id), thread_id)
    if rec is None:
        return _NOT_FOUND, {
            "name": "kill_command",
            "status": "error",
            "error": "not found",
            "child_id": str(job_id or ""),
        }
    if rec.get("status") in TERMINAL:
        _acknowledge(rec)
        return render(rec, tool="kill_command")
    if rec.get("status") == "cancelling":
        return render(rec, tool="kill_command")
    task = rec.get("task")
    rec["status"] = "cancelling"
    if task is not None and not task.done():
        task.cancel()
    ensure_schema()
    with db.tx() as conn:
        conn.execute(
            "UPDATE command_jobs SET status='cancelling', error='cancellation requested'"
            " WHERE id=? AND status='running'",
            (str(rec["id"]),),
        )
    return render(rec, tool="kill_command")


def list_thread_jobs(user_id: int, thread_id: int) -> list[dict]:
    """Running background jobs for a thread (dashboard job list)."""
    ensure_schema()
    rows = db.fetchall(
        "SELECT * FROM command_jobs"
        " WHERE user_id=? AND thread_id=? AND background=1 AND status IN ('running','cancelling')"
        " ORDER BY created_at",
        (int(user_id), int(thread_id)),
    )
    from datetime import datetime, timezone

    # Jobs started by a subagent belong in that subagent's view, not the root's.
    run_ids = sorted({str(row["run_id"]) for row in rows if row["run_id"]})
    child_runs: set[str] = set()
    if run_ids:
        placeholders = ",".join("?" for _ in run_ids)
        child_runs = {
            str(item["id"]) for item in db.fetchall(
                f"SELECT id FROM chat_runs WHERE id IN ({placeholders}) AND parent_run_id IS NOT NULL",
                tuple(run_ids),
            )
        }

    out: list[dict] = []
    for row in rows:
        rec = _live.get(row["id"])
        elapsed = None
        if rec is not None and rec.get("started"):
            elapsed = int((time.monotonic() - float(rec["started"])) * 1000)
        started_ms = row_start_ms(row)
        if elapsed is None:
            elapsed = row_elapsed_ms(row)
        item = {
            "id": row["id"],
            "host": row["host"] or "",
            "command": str(row["command"] or row.get("url") or "")[:200],
            "started_ms": started_ms,
            "elapsed_ms": elapsed or 0,
            "status": "running",
            "agent_run_id": str(row["run_id"]) if row["run_id"] and str(row["run_id"]) in child_runs else None,
            **_identity_fields(rec if rec is not None else dict(row)),
            **_label_fields(rec if rec is not None else dict(row)),
        }
        live_rec = rec if rec is not None else dict(row)
        if str(live_rec.get("kind") or row.get("kind") or "") == "monitor":
            item.update(_monitor_trace_fields(live_rec))
            item["kind"] = "monitor"
            item["name"] = "monitor"
        out.append(item)
    return out


def reconcile_jobs(user_id: int, thread_id: int, job_ids: list[str]) -> dict:
    """Resolve ALL referenced IDs, account+thread scoped, with no age/count cutoff.

    Read-only: does not join, cancel, consume notifications, or mark delivery.
    SQL is batched below SQLite's parameter limit. Missing/foreign IDs are
    indistinguishable and returned in `missing`, never invented as terminal.
    """
    ids = list(dict.fromkeys(i for i in job_ids if isinstance(i, str) and 0 < len(i) <= 128))
    ensure_schema()
    found = {}
    for start in range(0, len(ids), 400):
        batch = ids[start:start + 400]
        placeholders = ",".join("?" for _ in batch)
        rows = db.fetchall(
            f"SELECT * FROM command_jobs WHERE user_id=? AND thread_id=? AND id IN ({placeholders})",
            (int(user_id), int(thread_id), *batch),
        )
        for row in rows:
            rec = dict(row)
            live = _live.get(rec["id"])
            if live is not None and live.get("user_id") == int(user_id) and live.get("thread_id") == thread_id:
                rec = live
            _, view = render(rec, tool="get_command_output")
            found[rec["id"]] = view
    return {"jobs": {i: found[i] for i in ids if i in found}, "missing": [i for i in ids if i not in found]}


def recent_terminal_jobs(user_id: int, thread_id: int, window_s: int = 300) -> list[dict]:
    """Jobs of a thread that finished recently (trace finalization, dashboard)."""
    ensure_schema()
    from datetime import datetime, timedelta, timezone

    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=int(window_s))).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = db.fetchall(
        "SELECT * FROM command_jobs WHERE user_id=? AND thread_id=? AND background=1"
        " AND status IN ('completed','timed_out','cancelled','interrupted','lost') AND finished_at>=?"
        " ORDER BY finished_at DESC",
        (int(user_id), int(thread_id), cutoff),
    )
    result = []
    for row in rows:
        _, view = render(dict(row), tool="get_command_output")
        view["out"] = view["output"]  # compatibility alias; no hidden 400-char clipping
        result.append(view)
    return result


def kill_thread_job(user_id: int, thread_id: int, job_id: str) -> tuple[str, dict] | None:
    """Stop only a background job belonging to this account and thread."""
    rec = _get(job_id, user_id)
    if rec is None or rec.get("thread_id") != thread_id or not rec.get("background"):
        return None
    if rec.get("status") not in TERMINAL and rec.get("status") != "cancelling":
        rec["user_cancelled"] = True
        mark_notified(rec)
    return kill(job_id, user_id)


def job_status(user_id: int, job_id: str) -> str | None:
    """Terminal/live state for transcript finalization. None when unknown."""
    rec = _live.get(str(job_id))
    if rec is not None:
        if rec.get("user_id") != int(user_id):
            return None
        state = str(rec.get("status") or "")
        return state or "running"
    ensure_schema()
    row = db.fetchone(
        "SELECT status FROM command_jobs WHERE id=? AND user_id=?", (str(job_id), int(user_id))
    )
    return str(row["status"]) if row else None


def cancel_run(run_id: str) -> None:
    """Stop/cancel path: cancel shell jobs owned by a chat run. Monitors stay."""
    if not run_id:
        return
    for rec in list(_live.values()):
        if rec.get("run_id") != str(run_id):
            continue
        if _is_monitor(rec):
            continue
        if rec.get("status") in TERMINAL or rec.get("status") == "cancelling":
            continue
        try:
            kill(str(rec["id"]), int(rec["user_id"]))
        except Exception:
            logger.exception("Failed to cancel command job %s", rec["id"])


def cancel_thread(user_id: int, thread_id: int) -> None:
    """Thread delete: stop every live job on the thread, including monitors."""
    if not thread_id:
        return
    for rec in list(_live.values()):
        if rec.get("user_id") != int(user_id) or rec.get("thread_id") != int(thread_id):
            continue
        if rec.get("status") in TERMINAL or rec.get("status") == "cancelling":
            continue
        try:
            kill(str(rec["id"]), int(rec["user_id"]))
        except Exception:
            logger.exception("Failed to cancel thread job %s", rec["id"])
