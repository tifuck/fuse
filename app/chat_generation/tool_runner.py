"""Executing one tool round: call caps, same-step ordering domains,
concurrent workers, per-job validation/execution/trace shaping and
best-effort failure logging.

run_tools takes its executor as a narrow explicit parameter; the
app.chat_runtime wrapper injects `execute_tool` at call time so
`patch("app.chat_runtime.execute_tool")` keeps intercepting calls."""

from __future__ import annotations

import asyncio
import base64
import binascii
import contextlib
import json
import logging
import re
import time
import traceback

from fastapi import Request

from .. import chats
from ..chat_tools import ARTIFACT_TOOL_IDS

from .parsing import _ARG_LIMIT_FILE, _ARG_OVERFLOW
from .stream import _SENTINEL, HEARTBEAT_S
from .tool_jobs import ToolJob, _redact_tool_args, _running_event, jobs_from_calls

logger = logging.getLogger(__name__)


MAX_CALLS_PER_ROUND = 8
MAX_CALLS_CODING = 16


FETCH_CONCURRENCY = 4


# Tools whose results get a compact [duration Nms] line when they run slow.
# Shell-family tools already embed timing in their result envelope.
_DURATION_SUFFIX_TOOLS = frozenset(
    {
        "read_file",
        "list_dir",
        "grep",
        "glob",
        "write_file",
        "search_replace",
        "web_search",
        "read_url",
        "load_skill",
        "search_tools",
        "search_models",
    }
)


# Same-step ordering domains (see _run_tools.worker).
_HOST_MUTATING_TOOLS = frozenset(
    {"write_file", "search_replace", "run_terminal_command", "monitor", "kill_command"}
)
_HOST_READ_TOOLS = frozenset({"read_file", "list_dir", "grep", "glob"})


async def _record_failure(
    *,
    job: ToolJob | None,
    trace: dict | None,
    diag: dict,
    user_id: int | None,
    thread_id: int | None,
    phase: str = "",
    latency_ms: int | None = None,
    exception: str = "",
    name: str = "",
    arguments: str = "",
    call_id: str = "",
    kind: str = "",
    error: str = "",
) -> None:
    """Log one failed tool call. Best effort; must never break the stream."""
    try:
        from .. import tool_failures

        trace = trace or {}
        if job is not None:
            name = name or job.name
            arguments = arguments if arguments else job.arguments
            call_id = call_id or job.call_id
        if name in ARTIFACT_TOOL_IDS or not name:
            arguments = _redact_tool_args(name, arguments)
        await asyncio.to_thread(
            tool_failures.record,
            tool_name=name or "unknown",
            error=error or str(trace.get("error") or ""),
            kind=kind or str(trace.get("kind") or ""),
            arguments=arguments,
            call_id=call_id,
            user_id=user_id,
            thread_id=thread_id,
            run_id=str(diag.get("run_id") or ""),
            model=str(diag.get("model") or ""),
            source_type=str(diag.get("source_type") or ""),
            upstream_model=str(diag.get("upstream_model") or ""),
            round_index=diag.get("round"),
            phase=phase,
            latency_ms=latency_ms if latency_ms is not None else trace.get("latency_ms"),
            trace=trace,
            exception=exception or str(trace.get("exception") or ""),
            reasoning=str(diag.get("reasoning") or ""),
            user_text=str(diag.get("user_text") or ""),
        )
    except Exception:
        logger.debug("tool failure log skipped", exc_info=True)


async def run_tools(
    calls: list[dict],
    *,
    user_id: int,
    allowed_urls: set[str],
    request: Request,
    completion_i: int,
    budgets: dict,
    thread_id: int | None = None,
    bound_host_id: int | None = None,
    bound_cwd: str = "",
    coding: bool = False,
    loaded: list[str] | None = None,
    parent_run_id: str | None = None,
    user=None,
    session: dict | None = None,
    persist=None,
    diag: dict | None = None,
    execute_tool,
):
    session = session or {
        "coding": coding,
        "project_session": False,
        "bound_host_id": bound_host_id,
        "bound_cwd": bound_cwd,
    }
    diag = diag or {}
    jobs = jobs_from_calls(calls, completion_i)
    queue: asyncio.Queue = asyncio.Queue()
    cancel = asyncio.Event()
    persist_lock = asyncio.Lock()

    def emit(obj) -> None:
        queue.put_nowait(obj)

    from .. import permissions as permissions_mod

    perm_gate = permissions_mod.Gate(
        user_id=user_id,
        thread_id=thread_id,
        run_id=str((session or {}).get("run_id") or ""),
        requester=(session or {}).get("requester") or {"role": "main"},
        unattended=bool((session or {}).get("unattended")),
        notify=emit,
        cancel=cancel,
    )

    async def persist_now() -> None:
        if persist is None:
            return
        async with persist_lock:
            result = persist(jobs)
            if hasattr(result, "__await__"):
                await result

    async def heartbeat():
        while not cancel.is_set():
            try:
                await asyncio.wait_for(cancel.wait(), timeout=HEARTBEAT_S)
            except asyncio.TimeoutError:
                emit({"event": "heartbeat"})

    async def run_job(job: ToolJob, phase: str):
        started = time.monotonic()
        if cancel.is_set():
            job.text = "Tool interrupted."
            job.trace = {"name": job.name, "status": "interrupted", "kind": "interrupted", "id": job.event_id}
            job.done = True
            await _record_failure(
                job=job,
                trace=job.trace,
                diag=diag,
                user_id=user_id,
                thread_id=thread_id,
                phase=phase,
            )
            await persist_now()
            emit({"event": "tool", **job.trace})
            return
        emit(_running_event(job))
        await persist_now()
        raw_args = (job.arguments or "").strip()
        if raw_args:
            try:
                if len(raw_args) > _ARG_LIMIT_FILE or not isinstance(json.loads(raw_args), dict):
                    raise ValueError("arguments must be a bounded JSON object")
            except (json.JSONDecodeError, ValueError, RecursionError):
                # Truncated/invalid arguments used to fall through to the tool
                # as an empty dict, so e.g. get_subagent_output ran with no id
                # and the model kept retrying mid-JSON fragments forever.
                # Refuse execution and hand the model a corrective signal.
                if raw_args == _ARG_OVERFLOW:
                    job.text = (
                        "[Fuse] This tool call was NOT executed: its arguments exceeded the "
                        f"{_ARG_LIMIT_FILE}-character limit. Do not re-send it unchanged; split the "
                        "work into smaller calls (for example, write the file in parts)."
                    )
                else:
                    job.text = (
                        "[Fuse] This tool call was NOT executed: its JSON arguments were truncated "
                        "or invalid. Re-issue the complete tool call with the full arguments object."
                    )
                job.trace = {
                    "name": job.name,
                    "status": "error",
                    "kind": "invalid_arguments",
                    "error": job.text,
                    "id": job.event_id,
                }
                job.done = True
                await _record_failure(
                    job=job,
                    trace=job.trace,
                    diag=diag,
                    user_id=user_id,
                    thread_id=thread_id,
                    phase=phase,
                    kind="invalid_arguments",
                    error="tool call arguments were not valid JSON",
                )
                await persist_now()
                emit({"event": "tool", **job.trace})
                return
        try:

            def on_progress(qr):
                emit(
                    {
                        "event": "tool",
                        "id": job.event_id,
                        "status": "progress",
                        "query": qr.query,
                        "n": len(qr.hits),
                        "latency_ms": qr.latency_ms,
                    }
                )

            def on_started(trace):
                job.trace = {**job.trace, **trace, "id": job.event_id}
                emit({"event": "tool", "id": job.event_id, **trace})

            text, trace = await execute_tool(
                job.name,
                job.arguments,
                user_id=user_id,
                allowed_urls=allowed_urls,
                on_query_done=on_progress if phase == "search" else None,
                cancel=cancel,
                budgets=budgets,
                thread_id=thread_id,
                bound_host_id=session.get("bound_host_id") if session else bound_host_id,
                bound_cwd=(session.get("bound_cwd") if session else bound_cwd) or "",
                coding=bool(session.get("coding")) if session else coding,
                loaded=loaded,
                parent_run_id=parent_run_id,
                http_request=request,
                user=user,
                session=session,
                enabled=session.get("enabled_tools") if session else None,
                on_started=on_started,
                gate=perm_gate,
            )
            latency_ms = int((time.monotonic() - started) * 1000)
            trace = dict(trace)
            image = trace.pop("image", None)
            if job.name == "read_file" and isinstance(image, dict) and trace.get("status") not in ("error", "interrupted"):
                data = image.get("data")
                if (image.get("mime") in ("image/png", "image/jpeg", "image/webp")
                        and isinstance(data, str) and 0 < len(data) <= 4 * ((5 * 1024 * 1024 + 2) // 3)):
                    try:
                        decoded = base64.b64decode(data, validate=True)
                        if 0 < len(decoded) <= 5 * 1024 * 1024:
                            job.image = image
                    except (ValueError, binascii.Error):
                        pass
                if job.image:
                    trace["image"] = chats.tool_image_metadata(image)
                    run_id = str(session.get("run_id") or "")
                    if re.fullmatch(r"[A-Za-z0-9_-]{1,80}", run_id):
                        trace["image"]["preview_url"] = f"/chat/runs/{run_id}/images/{job.event_id}"
                else:
                    text = (text or "") + "\n[Image unavailable: invalid or oversized normalized image payload; no pixels delivered.]"
            if job.name in ("get_command_output", "kill_command"):
                args = json.loads(job.arguments or "{}")
                trace.setdefault("child_id", str(args.get("id") or args.get("child_id") or ""))
            if job.name in ("run_terminal_command", "get_command_output", "kill_command", "monitor") and trace.get("child_id"):
                from .. import chat_store as store
                trace["job_id"] = trace["child_id"]
                if thread_id:
                    with store.db.tx() as conn:
                        row = store.command_rows(conn, user_id, thread_id, [trace["child_id"]]).get(trace["child_id"])
                    if row:
                        stored_trace = store.command_trace(row, job.name)
                        trace = {**stored_trace, **trace} if row["status"] in ("running", "cancelling") else {**trace, **stored_trace}
            if job.name == "kill_command":
                from .. import chat_store as store
                store.settle_kill_receipt(trace)
            if trace.get("status") == "running" and job.name in ("get_command_output", "get_subagent_output"):
                trace.update(status="done", execution_status="running", background=True)
            output = trace.get("output") if isinstance(trace.get("output"), str) else (text or "")
            trace["output"] = output[-chats.TOOL_OUTPUT_MAX:] if trace.get("output_mode") == "tail" else output[:chats.TOOL_OUTPUT_MAX]
            trace["output_retained_chars"] = len(trace["output"])
            if trace.get("output_mode") == "tail" and trace.get("output_start") is not None:
                trace["output_start"] += len(output) - len(trace["output"])
            trace["output_chars"] = max(len(output), int(trace.get("output_chars") or 0))
            trace["truncated"] = bool(trace.get("truncated") or trace["output_chars"] > len(trace["output"]))
            # Every executed tool reports its wall-clock duration; shell jobs
            # carry timing inside their result envelope already.
            if latency_ms >= 1000 and job.name in _DURATION_SUFFIX_TOOLS:
                text = f"{text or ''}\n[duration {latency_ms}ms]" if (text or "").strip() else (text or "")
            job.text, job.trace = text, {**job.trace, **trace, "id": job.event_id,
                                      "duration_ms": trace.get("duration_ms", latency_ms), "call_duration_ms": latency_ms}
            job.done = True
            if trace.get("status") in ("error", "interrupted"):
                await _record_failure(
                    job=job,
                    trace=job.trace,
                    diag=diag,
                    user_id=user_id,
                    thread_id=thread_id,
                    phase=phase,
                    latency_ms=latency_ms,
                )
        except asyncio.CancelledError:
            job.text = "Tool interrupted."
            job.trace = {**job.trace, "name": job.name, "status": "interrupted", "kind": "interrupted", "id": job.event_id}
            job.done = True
            await _record_failure(
                job=job,
                trace=job.trace,
                diag=diag,
                user_id=user_id,
                thread_id=thread_id,
                phase=phase,
                latency_ms=int((time.monotonic() - started) * 1000),
            )
            await persist_now()
            emit({"event": "tool", **job.trace})
            raise
        except Exception:
            logger.exception("Tool %s failed", job.name or "unknown")
            job.text = "Tool failed."
            job.trace = {
                **job.trace,
                "name": job.name or "unknown",
                "status": "error",
                "kind": "internal_exception",
                "error": "Tool failed.",
                "id": job.event_id,
            }
            job.done = True
            await _record_failure(
                job=job,
                trace=job.trace,
                diag=diag,
                user_id=user_id,
                thread_id=thread_id,
                phase=phase,
                latency_ms=int((time.monotonic() - started) * 1000),
                exception=traceback.format_exc(limit=30),
            )
        await persist_now()
        tool_trace = dict(job.trace)
        extra_perm = None
        if job.name == "propose_skill" and tool_trace.get("status") == "done" and tool_trace.get("proposal_id"):
            extra_perm = {
                "event": "permission",
                "kind": "skill_save",
                "status": "pending",
                "id": str(tool_trace.get("proposal_id") or "")[:64],
                "slug": str(tool_trace.get("slug") or tool_trace.get("preview") or "")[:64],
                "description": str(tool_trace.get("description") or "")[:1024],
                "overwrite": bool(tool_trace.get("overwrite")),
                "enable": bool(tool_trace.get("enable", True)),
                "files": list(tool_trace.get("files") or [])[:50],
                "origin_existing": str(tool_trace.get("origin_existing") or "")[:40],
            }
        for key in ("proposal_id", "overwrite", "enable", "files", "origin_existing", "slug"):
            tool_trace.pop(key, None)
        emit({"event": "tool", "id": job.event_id, **tool_trace})
        if extra_perm:
            emit(extra_perm)

    def _job_phase(name: str) -> str:
        if name == "web_search":
            return "search"
        if name == "read_url":
            return "fetch"
        if name in ARTIFACT_TOOL_IDS:
            return "artifact"
        if name in ("search_tools", "load_skill", "todo_write", "scratch", "propose_skill", "search_models"):
            return "meta"
        if name == "spawn_subagent":
            return "spawn"
        if name in ("get_subagent_output", "kill_subagent"):
            return "join"
        return "other"

    async def worker():
        try:
            call_cap = MAX_CALLS_CODING if coding else MAX_CALLS_PER_ROUND
            for j in jobs:
                if j.orig_index >= call_cap:
                    extra = "Too many tool calls in one step; extra calls were ignored."
                    j.text = extra
                    j.trace = {
                        "name": j.name,
                        "status": "error",
                        "kind": "over_call_cap",
                        "error": extra,
                        "id": j.event_id,
                    }
                    j.done = True
                    await _record_failure(
                        job=j,
                        trace=j.trace,
                        diag=diag,
                        user_id=user_id,
                        thread_id=thread_id,
                        phase="queue",
                    )
                    emit({"event": "tool", **j.trace})
                    continue
            await persist_now()
            runnable = [j for j in jobs if j.orig_index < call_cap]
            # Independent calls run concurrently: the old phase chain (spawn →
            # join → shell → search) made a 75s foreground spawn or a long
            # join freeze every file/shell/search call in the same step.
            # Host effects keep call order, though: a write, edit, shell
            # command, or kill waits for every earlier host call, and a file
            # read waits for earlier host mutations, so "edit A; edit A; run
            # tests" in one step behaves as written. Artifact writes keep
            # their own order. Fetches run after this step's searches so a
            # page read sees their result URLs in allowed_urls, bounded by
            # the semaphore. Transcript order comes from jobs[], not
            # completion order.
            sem = asyncio.Semaphore(FETCH_CONCURRENCY)

            async def run_one(j):
                if j.name == "read_url":
                    async with sem:
                        await run_job(j, "fetch")
                else:
                    await run_job(j, _job_phase(j.name))

            async def run_after(deps, j):
                if deps:
                    await asyncio.gather(*deps, return_exceptions=True)
                await run_one(j)

            search_tasks: list[asyncio.Task] = []
            host_seen: list[tuple[asyncio.Task, bool]] = []
            artifact_seen: list[asyncio.Task] = []
            ordered = [j for j in runnable if j.name != "read_url"] + [j for j in runnable if j.name == "read_url"]
            async with asyncio.TaskGroup() as tg:
                for j in ordered:
                    if j.name == "read_url":
                        deps = list(search_tasks)
                    elif j.name in _HOST_MUTATING_TOOLS:
                        deps = [t for t, _ in host_seen]
                    elif j.name in _HOST_READ_TOOLS:
                        deps = [t for t, mutating in host_seen if mutating]
                    elif j.name in ARTIFACT_TOOL_IDS:
                        deps = list(artifact_seen)
                    else:
                        deps = []
                    task = tg.create_task(run_after(deps, j))
                    if j.name == "web_search":
                        search_tasks.append(task)
                    elif j.name in _HOST_MUTATING_TOOLS or j.name in _HOST_READ_TOOLS:
                        host_seen.append((task, j.name in _HOST_MUTATING_TOOLS))
                    elif j.name in ARTIFACT_TOOL_IDS:
                        artifact_seen.append(task)
        except asyncio.CancelledError:
            raise
        finally:
            cancel.set()
            emit(_SENTINEL)

    wt = asyncio.create_task(worker())
    ht = asyncio.create_task(heartbeat())
    try:
        while True:
            if await request.is_disconnected():
                cancel.set()
                wt.cancel()
                break
            item = await queue.get()
            if item is _SENTINEL:
                break
            yield item
    finally:
        cancel.set()
        for t in (wt, ht):
            t.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await t
        for j in jobs:
            if not j.done:
                j.text = j.text or "Tool interrupted."
                j.trace = {**j.trace, "name": j.name, "status": "interrupted", "id": j.event_id}
                j.done = True
        if persist is not None:
            result = persist(jobs)
            if hasattr(result, "__await__"):
                await result
    yield jobs
