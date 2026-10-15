"""How a command job reads to the model and the UI: output head/tail, trace
fields and the tool result text (render). Lifecycle lives in command_jobs."""
from __future__ import annotations

import time


# Model-visible body of a finished command: head + tail, under the runtime's
# per-result clip (chat_runtime.TOOL_RESULT_CHARS).
MODEL_TAIL_CHARS = 12_000


# The head/tail cut moves to a line boundary when one is this close.
_SNAP_CHARS = 1_000


RUNNING_TAIL_CHARS = 1_200


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


# Imported last, as command_jobs imports this module last: either module can
# load first, and each finds the other's names defined by the time it binds them.
from .command_jobs import BACKGROUND_DEFAULT_MS, TERMINAL, _is_monitor, _label_fields  # noqa: E402
