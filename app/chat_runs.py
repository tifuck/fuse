"""Single-worker, server-owned dashboard generation.

Tool results are durable as they complete. After a restart, a run with a saved
tool-round transcript is re-queued and continued; an in-flight provider stream
with no such transcript is interrupted rather than retried.
"""
from __future__ import annotations

import asyncio
import copy
import json
import logging
import re
import time
from datetime import datetime, timezone

from fastapi import Request

from . import chat_runtime, chat_store, chats, db
from .async_work import durable_call

FLUSH_SECONDS = 0.2
# Fallback cadence for the per-run status poll. Cancellation is delivered
# in-process (see _cancel_signals); the database re-check only guards against
# a lost worker or a run whose task handle vanished. With hundreds of
# concurrent subagents a 75ms-per-run poll would be thousands of queries/sec.
STATUS_POLL_SECONDS = 2.0
MAX_RUNTIME_SECONDS = chat_store.DEFAULT_REMAINING_SECONDS
CLEANUP_SECONDS = 2
MAX_FRAME_BYTES = 2_500_000
CANCEL_GRACE_SECONDS = 2.0
FORCE_CANCEL_SECONDS = 15.0
STALL_IDLE_SECONDS = chat_store.STALL_IDLE_SECONDS
logger = logging.getLogger(__name__)
_tasks: dict[str, asyncio.Task] = {}
_iterators: dict[str, object] = {}
_cancelled: set[str] = set()
# In-process cancellation wakeups, keyed by run id. The HTTP Stop path and
# the reaper set these directly; workers observe them within one flush tick
# instead of polling chat_runs for cancel_requested at streaming cadence.
_cancel_signals: dict[str, tuple[asyncio.Event, asyncio.AbstractEventLoop]] = {}
_stopping = False
# Runs whose worker ended but whose terminal finish_run never committed:
# run id -> (stored message, terminal status, reaper attempts). The reaper
# retries this exact finish instead of resuming a turn that already ended.
_unsettled: dict[str, tuple[dict, str, int]] = {}
UNSETTLED_REAP_ATTEMPTS = 5
_FINISH_BACKOFF = (0.0, 0.5, 2.0)
_THINK = re.compile(r"</?(?:think|thinking|thought|reasoning)>", re.I)


def _register_cancel_signal(ident: str):
    event = asyncio.Event()
    _cancel_signals[ident] = (event, asyncio.get_running_loop())
    return event


def _signal_cancel(ident: str):
    """Thread-safe: wake a run's cancel event from any thread."""
    entry = _cancel_signals.get(ident)
    if entry is None:
        return
    event, loop = entry
    try:
        loop.call_soon_threadsafe(event.set)
    except RuntimeError:
        pass  # loop closed during shutdown


def _take(value):
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "".join(v if isinstance(v, str) else str(v.get("text") or v.get("content") or "")
                       for v in value if isinstance(v, (str, dict)))
    if isinstance(value, dict):
        return next((value[k] for k in ("content", "text", "summary") if isinstance(value.get(k), str)), "")
    return ""


def _now() -> float:
    """Monotonic clock for thought timing (patchable without touching time)."""
    return time.monotonic()


class Output:
    def __init__(self, run):
        msg = copy.deepcopy(run["message"]) if isinstance(run.get("message"), dict) else {}
        msg.setdefault("content", "")
        msg.setdefault("reasoning", "")
        msg.setdefault("tools", [])
        if "segments" not in msg:
            msg["segments"] = []
            if msg.get("reasoning"):
                msg["segments"].append({"type": "reasoning", "text": msg["reasoning"]})
            msg["segments"].extend(
                {"type": "tool", "id": tool["id"]}
                for tool in msg["tools"] if tool.get("id")
            )
            if msg.get("content"):
                msg["segments"].append({"type": "content", "text": msg["content"]})
        msg["startedAt"] = int(time.time() * 1000)
        msg["status"] = "running"
        msg["stopped"] = False
        self.message = msg
        self._think_inside = False
        self._think_pending = ""
        self._xml_pending = ""
        self._last_source = None
        # Aggregates (content/reasoning) are maintained incrementally on the
        # append-only fast path; any structural rewrite (tool-xml scrub that
        # dropped text) marks them dirty for one full recompute. Per-token
        # cost stays O(chunk) instead of O(message length).
        self._agg_dirty = False
        # Prefix length of the current tail content segment already scanned
        # for tool-xml blocks. Complete blocks only ever form in the unstable
        # tail, so the committed prefix is never rescanned.
        self._scrub_stable = 0
        self._round_content = len(msg.get("content") or "")
        self._round_reasoning = len(msg.get("reasoning") or "")
        self._round_tool_chars = 0
        self._stream_out = 0
        # Thought timing, as Claude Code measures it: from the transcript's
        # previous event (a tool result, the round starting) to the thought's
        # last delta. Heartbeats and meters are not events.
        self._last_event_at = _now()
        self._think_since = None

    def _time_thought(self, segment):
        now = _now()
        if self._think_since is None:
            self._think_since = now - (segment.get("ms") or 0) / 1000
        segment["ms"] = min(chats.THOUGHT_MS_MAX, max(0, int((now - self._think_since) * 1000)))

    def _append_text(self, kind, text, source):
        if not text:
            return
        if kind in ("content", "reasoning"):
            text = chat_runtime.strip_special_tokens(text)
            if not text:
                return
        segments = self.message["segments"]
        added = text
        if segments and segments[-1].get("type") == kind:
            if kind == "reasoning" and self._last_source and self._last_source != source:
                segments[-1]["text"] += "\n"
                added = "\n" + text
            segments[-1]["text"] += text
        else:
            if kind == "content" and any(part.get("type") == "content" for part in segments):
                if not text.startswith("\n"):
                    added = "\n\n" + text
                    text = added
            segments.append({"type": kind, "text": text})
            if kind == "content":
                self._scrub_stable = 0
            if kind == "reasoning":
                self._think_since = self._last_event_at
        if kind == "reasoning":
            self._time_thought(segments[-1])
        self._last_source = source
        self.message[kind] = (self.message.get(kind) or "") + added

    def _remove_pending(self):
        if not self._think_pending:
            return ""
        pending = self._think_pending
        segments = self.message["segments"]
        if segments and segments[-1].get("type") in ("content", "reasoning") and segments[-1]["text"].endswith(pending):
            kind = segments[-1].get("type")
            segments[-1]["text"] = segments[-1]["text"][:-len(pending)]
            if kind == "content":
                self._scrub_stable = min(self._scrub_stable, len(segments[-1]["text"]))
            if not segments[-1]["text"]:
                segments.pop()
                if kind == "content":
                    self._scrub_stable = 0
            base = self.message.get(kind) or ""
            self.message[kind] = base[: max(0, len(base) - len(pending))]
        self._think_pending = ""
        return pending

    def _append_content(self, text, prefix=""):
        text = chat_runtime.strip_special_tokens(text or "")
        prefix = chat_runtime.strip_special_tokens(prefix or "")
        data = (self._xml_pending or "") + prefix + self._remove_pending() + text
        self._xml_pending = ""
        offset = 0
        for match in _THINK.finditer(data):
            self._append_text("reasoning" if self._think_inside else "content", data[offset:match.start()], "tagged")
            self._think_inside = not match[0].startswith("</")
            offset = match.end()
        rest = data[offset:]
        last_lt = rest.rfind("<")
        if last_lt >= 0 and ">" not in rest[last_lt:]:
            self._append_text("reasoning" if self._think_inside else "content", rest[:last_lt], "tagged")
            self._think_pending = rest[last_lt:]
            self._append_text("reasoning" if self._think_inside else "content", self._think_pending, "tagged")
        else:
            self._append_text("reasoning" if self._think_inside else "content", rest, "tagged")
        self._scrub_tool_xml()

    def _scrub_tool_xml(self):
        if self._think_inside:
            return
        segments = self.message["segments"]
        if not segments or segments[-1].get("type") != "content":
            self._scrub_stable = 0
            return
        text = segments[-1].get("text") or ""
        stable = min(self._scrub_stable, len(text))
        visible, pending = chat_runtime.drop_tool_xml(text[stable:])
        self._xml_pending = pending
        merged = text[:stable] + visible
        delta = len(merged) - len(text)
        if merged:
            segments[-1]["text"] = merged
            self._scrub_stable = len(merged)
        else:
            segments.pop()
            self._scrub_stable = 0
        if delta:
            # A dropped block is a middle removal, not a suffix trim; the
            # aggregates need one recompute from the segments.
            self._agg_dirty = True

    def _ensure_aggregates(self):
        if not self._agg_dirty:
            return
        self.message["content"] = "".join(
            part.get("text", "") for part in self.message["segments"] if part.get("type") == "content"
        )
        self.message["reasoning"] = "".join(
            part.get("text", "") for part in self.message["segments"] if part.get("type") == "reasoning"
        )
        self._agg_dirty = False

    def fold_handoff(self, summary: str, marker: str):
        """Drop the pre-handoff bubble. The summary is the session from here."""
        msg = self.message
        msg["content"] = ""
        msg["reasoning"] = ""
        msg["tools"] = []
        msg["error"] = ""
        msg["segments"] = [{
            "type": "marker",
            "kind": "compaction",
            "text": marker,
            "summary": str(summary or "")[:24_000],
        }]
        self._think_inside = False
        self._think_pending = ""
        self._xml_pending = ""
        self._last_source = None
        self._agg_dirty = False
        self._scrub_stable = 0
        self._round_content = 0
        self._round_reasoning = 0
        self._round_tool_chars = 0
        self._stream_out = 0

    def _reset_stream_round(self):
        self._ensure_aggregates()
        self._round_content = len(self.message.get("content") or "")
        self._round_reasoning = len(self.message.get("reasoning") or "")
        self._round_tool_chars = 0
        self._stream_out = 0

    def _tool_call_delta_chars(self, obj) -> int:
        choice = (obj.get("choices") or [{}])[0] if isinstance(obj.get("choices"), list) else {}
        if not isinstance(choice, dict):
            return 0
        delta = choice.get("delta") or {}
        calls = delta.get("tool_calls")
        if not isinstance(calls, list):
            calls = choice.get("message", {}).get("tool_calls") if isinstance(choice.get("message"), dict) else None
        if not isinstance(calls, list):
            return 0
        n = 0
        for call in calls:
            if not isinstance(call, dict):
                continue
            fn = call.get("function") if isinstance(call.get("function"), dict) else {}
            name = fn.get("name")
            args = fn.get("arguments")
            if isinstance(name, str):
                n += len(name)
            if isinstance(args, str):
                n += len(args)
            elif isinstance(args, dict):
                n += len(json.dumps(args, ensure_ascii=False, separators=(",", ":")))
        return n

    def _refresh_stream_out(self, obj=None):
        visible = max(0, len(self.message.get("content") or "") - self._round_content)
        visible += max(0, len(self.message.get("reasoning") or "") - self._round_reasoning)
        if obj is not None:
            self._round_tool_chars += self._tool_call_delta_chars(obj)
        chars = visible + self._round_tool_chars
        self._stream_out = (chars + 3) // 4 if chars else 0

    def apply(self, obj):
        msg = self.message
        event = obj.get("fuse")
        if isinstance(event, dict):
            kind = event.get("event")
            if kind not in ("heartbeat", "context"):
                self._last_event_at = _now()
            if kind == "notice" and event.get("message"):
                line = str(event["message"]).strip()
                from . import context_compact as ctxmod

                marker = ctxmod.compaction_marker_text(line)
                if marker:
                    handoff = event.get("handoff")
                    if isinstance(handoff, str) and handoff.strip() and marker.startswith("Context compacted"):
                        self.fold_handoff(handoff.strip(), marker)
                    elif not any(part.get("type") == "marker" and part.get("text") == marker for part in msg.get("segments") or []):
                        msg.setdefault("segments", []).append({"type": "marker", "kind": "prune" if marker.startswith("Context pruned") else "compaction", "text": marker})
                else:
                    lines = [s.strip() for s in msg.get("notice", "").splitlines() if s.strip()]
                    if line and not any(s.lower() == line.lower() or s.lower().startswith(line.lower() + " \u00d7") for s in lines):
                        lines.append(line)
                    msg["notice"] = "\n".join(lines)[:chats.NOTICE_MAX]
            if kind == "activity" and event.get("phase") == "model":
                self._reset_stream_round()
            if kind == "tool":
                tools = msg["tools"]
                old = next((t for t in tools if event.get("id") and t.get("id") == event["id"]), None)
                if old is None:
                    self._think_pending = ""
                    self._think_inside = False
                    self._last_source = None
                    self._reset_stream_round()
                if old and old.get("status") in ("done", "error", "interrupted") and event.get("status") in ("running", "progress"):
                    return
                if event.get("status") == "progress":
                    if old is not None:
                        if "latency_ms" in event:
                            old["latency_ms"] = event["latency_ms"]
                        for key in ("output", "preview", "summary", "event_count", "elapsed_ms"):
                            if key in event:
                                old[key] = event[key]
                else:
                    rec = {**(old or {}), **{k: v for k, v in event.items() if k != "event"}}
                    rec.setdefault("name", "web_search")
                    rec["results"] = event.get("results", rec.get("results") or [])
                    rec["error"] = event.get("error", rec.get("error") or "")
                    clean = chats._parse_tool_traces([rec])
                    if clean:
                        if old is not None:
                            tools[tools.index(old)] = clean[0]
                        elif len(tools) < chats.TOOL_TRACE_MAX:
                            tools.append(clean[0])
                            if clean[0].get("id") and not any(
                                part.get("type") == "tool" and part.get("id") == clean[0]["id"]
                                for part in msg["segments"]
                            ):
                                msg["segments"].append({"type": "tool", "id": clean[0]["id"]})
            if kind == "permission":
                ident = str(event.get("id") or "")[:64]
                if ident:
                    if str(event.get("kind") or "") == "tool_permission":
                        rec = {
                            "id": ident,
                            "kind": "tool_permission",
                            "status": event.get("status") if event.get("status") in ("pending", "allowed", "denied", "timeout", "cancelled") else "denied",
                            "tool": str(event.get("tool") or "")[:40],
                            "host": str(event.get("host") or "")[:80],
                            "path": str(event.get("path") or "")[:400],
                            "cwd": str(event.get("cwd") or "")[:400],
                            "command": str(event.get("command") or "")[:8000],
                            "secret": bool(event.get("secret")),
                            "requester": str(event.get("requester_label") or event.get("requester") or "Main agent")[:200],
                            "reasons": [str(r)[:120] for r in (event.get("reasons") or []) if isinstance(r, str)][:4],
                            "expires_at": str(event.get("expires_at") or "")[:40],
                        }
                        scope = event.get("grant_scope")
                        if scope in ("allow_session", "allow_host"):
                            rec["grant_scope"] = scope
                        if event.get("grant_kind") in ("dir", "file"):
                            rec["grant_kind"] = event.get("grant_kind")
                        if event.get("grant_value"):
                            rec["grant_value"] = str(event.get("grant_value"))[:400]
                        perms = msg.setdefault("permissions", [])
                        old = next((p for p in perms if p.get("id") == ident), None)
                        # Status updates carry only the id; keep the label.
                        description = event.get("description") or (old or {}).get("description")
                        if description:
                            rec["description"] = str(description)[:200]
                        clean = chats._parse_permissions([rec])
                        if clean:
                            if old is not None:
                                perms[perms.index(old)] = clean[0]
                            else:
                                perms.append(clean[0])
                        return
                    rec = {
                        "id": ident,
                        "kind": "skill_save",
                        "status": event.get("status") if event.get("status") in ("pending", "saved", "rejected", "expired", "error") else "pending",
                        "slug": str(event.get("slug") or "")[:64],
                        "description": str(event.get("description") or "")[:1024],
                        "overwrite": bool(event.get("overwrite")),
                        "enable": bool(event.get("enable", True)),
                        "files": [str(n)[:200] for n in (event.get("files") or []) if isinstance(n, str)][:50],
                        "origin_existing": str(event.get("origin_existing") or "")[:40],
                    }
                    if event.get("error"):
                        rec["error"] = str(event.get("error"))[:400]
                    perms = msg.setdefault("permissions", [])
                    old = next((p for p in perms if p.get("id") == ident), None)
                    clean = chats._parse_permissions([rec])
                    if clean:
                        if old is not None:
                            perms[perms.index(old)] = clean[0]
                        else:
                            perms.append(clean[0])
                        if not any(part.get("type") == "permission" and part.get("id") == ident for part in msg["segments"]):
                            msg["segments"].append({"type": "permission", "id": ident})
            return
        if obj.get("error"):
            error = obj["error"]
            msg["error"] = chats._legacy_text(error if isinstance(error, str) else error.get("message", "Upstream error"), chats.ERROR_MAX)
            if chat_runtime._provider_error_content(msg.get("content") or ""):
                msg["content"] = ""
                msg["segments"] = [
                    part for part in (msg.get("segments") or [])
                    if part.get("type") != "content" or not chat_runtime._provider_error_content(part.get("text") or "")
                ]
            return
        choice = (obj.get("choices") or [{}])[0]
        delta, message = choice.get("delta") or {}, choice.get("message") or {}
        content = _take(delta.get("content")) or ("" if choice.get("delta") is not None else _take(message.get("content")))
        pending = self._remove_pending()
        for key in ("reasoning_content", "reasoning", "thinking", "reasoning_text"):
            self._append_text("reasoning", _take(delta.get(key)) or _take(message.get(key)) or _take(obj.get(key)), "native")
        self._append_content(content, pending)
        self._ensure_aggregates()
        if max(len(msg["content"]), len(msg["reasoning"])) > chats.ASSISTANT_CONTENT_MAX:
            raise ValueError(f"Generation output safety limit reached ({chats.ASSISTANT_CONTENT_MAX:,} characters per reply field). Start a new turn to continue; this is separate from model context compaction.")
        if isinstance(obj.get("usage"), dict):
            chats._validate_json_value(obj["usage"], "usage")
            if len(json.dumps(obj["usage"], ensure_ascii=True, separators=(",", ":"))) > chats.USAGE_MAX:
                raise ValueError("Generation usage limit reached.")
            msg["usage"] = obj["usage"]
        self._refresh_stream_out(obj)
        self._last_event_at = _now()

    def stored(self, light=False):
        # Run status/created_at are durable separately; message schema excludes these.
        self._ensure_aggregates()
        msg = {k: v for k, v in self.message.items() if k not in ("startedAt", "status")}
        if light:
            # Live flushes run ~5x/sec per run: shallow-defensive copies are
            # enough (the stream loop only mutates top-level keys of tool and
            # segment dicts) and avoid a full deepcopy of the message.
            msg["tools"] = [dict(t) for t in (msg.get("tools") or []) if isinstance(t, dict)]
            msg["segments"] = [dict(s) for s in (msg.get("segments") or []) if isinstance(s, dict)]
            msg.setdefault("content", "")
            msg.setdefault("reasoning", "")
            return msg
        msg["tools"] = chats._parse_tool_traces(msg.get("tools") or [])
        # A new wait is a new tool call, even if it targets the same child.
        # Collapsing by child ID loses the current wait and its timeline slot.
        kept = msg["tools"]
        ids = {t.get("id") for t in kept if t.get("id")}
        msg["segments"] = chats._parse_segments(
            [part for part in (msg.get("segments") or []) if part.get("type") != "tool" or part.get("id") in ids],
            msg["tools"],
            permissions=msg.get("permissions"),
        )
        msg["content"] = "".join(
            part.get("text", "") for part in msg["segments"] if part.get("type") == "content"
        )
        msg["reasoning"] = "".join(
            part.get("text", "") for part in msg["segments"] if part.get("type") == "reasoning"
        )
        msg["content"] = chats._legacy_text(chat_runtime.strip_special_tokens(msg["content"]), chats.ASSISTANT_CONTENT_MAX)
        msg["reasoning"] = chats._legacy_text(chat_runtime.strip_special_tokens(msg["reasoning"]), chats.ASSISTANT_CONTENT_MAX)
        msg["segments"] = chats.align_segments(msg["segments"], msg["content"], msg["reasoning"])
        return msg


def _segments_patch(old, new):
    texts = []
    def replace_from(index):
        # Structural patches replace a suffix. Include earlier text edits too;
        # clients intentionally handle suffix replacement and text deltas separately.
        start = min([index, *[part["index"] for part in texts]])
        return {"from": start, "items": new[start:]}

    shared = min(len(old), len(new))
    for index in range(shared):
        before, after = old[index], new[index]
        if before.get("type") != after.get("type"):
            return replace_from(index)
        if before.get("type") == "marker":
            if (
                before.get("kind") != after.get("kind")
                or before.get("text") != after.get("text")
                or before.get("summary") != after.get("summary")
            ):
                return replace_from(index)
            continue
        if before.get("type") in ("tool", "permission"):
            if before.get("id") != after.get("id"):
                return replace_from(index)
            continue
        prior, current = before.get("text", ""), after.get("text", "")
        # A thought's duration grows with its text and rides on the same patch.
        timed = after.get("type") == "reasoning" and "ms" in after and before.get("ms") != after.get("ms")
        if prior == current and not timed:
            continue
        offset = 0
        while offset < min(len(prior), len(current)) and prior[offset] == current[offset]:
            offset += 1
        entry = {
            "index": index,
            "from": len(prior[:offset].encode("utf-16-le")) // 2,
            "text": current[offset:],
        }
        if timed:
            entry["ms"] = after["ms"]
        texts.append(entry)
    if len(new) < len(old):
        return replace_from(len(new))
    patch = {}
    if texts:
        patch["texts"] = texts
    if len(new) > len(old):
        patch["base"] = len(old)
        patch["append"] = new[len(old):]
    return patch


def internal_request(user_id):
    """ASGI request used to resume a run after process start (no browser)."""

    async def receive():
        await asyncio.Future()

    return Request(
        {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/chat",
            "raw_path": b"/chat",
            "query_string": b"",
            "headers": [],
            "client": ("127.0.0.1", 0),
            "server": ("127.0.0.1", 8088),
            "session": {"uid": user_id},
            "state": {},
        },
        receive,
    )


def _pin_coding_run(user, run):
    agent = run.get("request", {}).get("agent") if isinstance(run.get("request"), dict) else None
    if not isinstance(agent, dict) or not agent.get("coding"):
        return
    from .vault import VaultLocked, get_dek, pin_run

    try:
        get_dek(user["id"])
        pin_run(user["id"], run["id"])
        agent["vault_unlocked"] = True
    except VaultLocked:
        agent["vault_unlocked"] = False


async def start(request: Request, user, run):
    """Schedule once, returning the local task (or None for a terminal run)."""
    existing = _tasks.get(run["id"])
    if existing is not None:
        if not existing.done():
            return existing
        # A completed/cancelled registry entry is not a worker. Reclaim it
        # without propagating that old task's cancellation into this new start.
        try:
            existing.result()
        except (asyncio.CancelledError, Exception):
            pass
        if _tasks.get(run["id"]) is existing:
            _tasks.pop(run["id"], None)
    current = chat_store.get_run(user["id"], run["id"])
    if current is None or current["status"] != "queued":
        return None
    if _stopping:
        chat_store.finish_run(current["id"], {**current["message"], "stopped": True}, "interrupted")
        return None
    if not current["cancel_requested"]:
        _cancelled.discard(current["id"])
    _pin_coding_run(user, current)
    scope = dict(request.scope)
    scope["headers"] = list(scope.get("headers", []))
    scope["session"] = copy.deepcopy(scope.get("session", {}))
    scope["state"] = dict(scope.get("state", {}))

    async def receive():
        await asyncio.Future()

    detached = Request(scope, receive=receive)
    task = asyncio.create_task(_run(detached, dict(user), current), name=f"chat-run-{current['id']}")
    _tasks[current["id"]] = task
    return task


def _iso_age(ts) -> float:
    try:
        dt = datetime.fromisoformat(str(ts or "").replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return max(0.0, (datetime.now(timezone.utc) - dt).total_seconds())
    except Exception:
        return 10 ** 9


def _schedule_aclose(iterator):
    close = getattr(iterator, "aclose", None)
    if close is None:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return

    async def _close():
        try:
            async with asyncio.timeout(CLEANUP_SECONDS):
                await close()
        except (Exception, asyncio.CancelledError):
            pass

    loop.create_task(_close())


def cancel(run_id):
    """Wake a local run after the HTTP handler persists request_cancel.

    If no live worker owns the run, finish it immediately so Stop cannot hang
    on a phantom queued/running row.
    """
    _cancelled.add(run_id)
    _signal_cancel(run_id)
    try:
        from . import workspace

        workspace.cancel_run_jobs(run_id)
    except Exception:
        logger.exception("Failed to cancel workspace jobs for %s", run_id)
    iterator = _iterators.get(run_id)
    if iterator is not None:
        _schedule_aclose(iterator)
    task = _tasks.get(run_id)
    if task is not None and not task.done():
        # A not-yet-entered coroutine must get a chance to install its finally.
        if getattr(task, "_fuse_started", False):
            task.cancel()
        return
    chat_store.abort_active(run_id, "cancelled")
    if task is None:
        # No local worker will ever run the finally that discards this id.
        _cancelled.discard(run_id)


def cancel_tree(run_id):
    cancel(run_id)
    for ident in chat_store.descendant_ids(run_id):
        cancel(ident)


async def _await_tasks(ids, timeout):
    tasks = [_tasks[ident] for ident in ids if ident in _tasks and not _tasks[ident].done()]
    if not tasks:
        return
    # asyncio.wait never cancels on timeout and never propagates task
    # exceptions: a worker that suppresses CancelledError cannot stall the
    # caller past its deadline. Uncooperative workers are handled by the
    # force-finalize loop in cancel_wait and by the reaper.
    await asyncio.wait(tasks, timeout=max(0.0, timeout))


async def cancel_wait(user_id, run_id, *, tree=True, force=False, by_user=False):
    """Cancel and wait briefly; force-finish anything still active.

    force skips the graceful drain entirely (user-explicit "force stop" on a
    subagent wedged in stopping). by_user (the Stop button) marks a subagent
    stopped on its own so its report never wakes the main agent.
    """
    if tree:
        await durable_call(chat_store.request_cancel, user_id, run_id)
        ids = [run_id, *await asyncio.to_thread(chat_store.descendant_ids, run_id)]
        cancel_tree(run_id)
        if by_user:
            # Earlier turns' background subagents live in their own trees.
            others = await durable_call(chat_store.request_cancel_thread_agents, user_id, run_id)
            for ident in others:
                cancel(ident)
            ids.extend(ident for ident in others if ident not in ids)
    else:
        ids = await durable_call(chat_store.request_cancel_self, user_id, run_id, by_user)
        for ident in ids:
            cancel(ident)
    await _await_tasks(ids, 0.0 if force else CANCEL_GRACE_SECONDS)
    for ident in ids:
        row = await asyncio.to_thread(chat_store.get_run, user_id, ident)
        if row and row["status"] in chat_store.ACTIVE:
            await durable_call(chat_store.abort_active, ident, "cancelled")


async def _finish_durably(ident: str, stored: dict, status: str) -> bool:
    """Commit the terminal row, retrying transient failures.

    finish_run is a no-op once the row left ACTIVE, so replaying it after an
    uncertain commit is safe. On final failure the finish is parked in
    _unsettled for the reaper (never left for recover_one to resume).
    """
    finished = False
    try:
        for attempt, delay in enumerate(_FINISH_BACKOFF, 1):
            if delay:
                await asyncio.sleep(delay)
            try:
                await durable_call(chat_store.finish_run, ident, stored, status)
                finished = True
                return True
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Chat finish_run failed for %s (attempt %s)", ident, attempt)
        return False
    finally:
        if not finished:
            _unsettled[ident] = (stored, status, 0)


def _settle_unsettled(row) -> None:
    """Retry a parked terminal finish. Leaves the row ACTIVE on failure."""
    ident = row["id"]
    stored, status, tries = _unsettled[ident]
    try:
        chat_store.finish_run(ident, stored, status)
    except Exception:
        tries += 1
        logger.exception("Deferred finish_run failed for %s (reaper attempt %s)", ident, tries)
        if tries < UNSETTLED_REAP_ATTEMPTS:
            _unsettled[ident] = (stored, status, tries)
            return
        # Deterministic failure: settle the row terminal (last checkpoint)
        # rather than keep it ACTIVE forever or let recovery re-run it.
        _unsettled.pop(ident, None)
        chat_store.abort_active(ident, status)
        return
    _unsettled.pop(ident, None)
    try:
        schedule_wake(row["user_id"], row["thread_id"])
    except Exception:
        logger.exception("Failed to schedule wake after deferred finish for %s", ident)


def reap_orphans(user_id=None):
    """Finish phantom active runs that no local worker owns, or that are stuck."""
    if _stopping:
        return
    # Light columns only: this scan runs every 60 s over every active run and
    # would otherwise ship each run's request/message/provider blobs. Genuine
    # orphans (no live worker) re-fetch their full row before recovery.
    sql = """SELECT id,user_id,thread_id,parent_run_id,status,cancel_requested,
                    cancel_requested_at,created_at,updated_at
             FROM chat_runs WHERE status IN ('queued','running')"""
    args: tuple = ()
    if user_id is not None:
        sql += " AND user_id=?"
        args = (user_id,)
    rows = db.fetchall(sql, args)
    if user_id is None:
        # A parked finish whose row was settled elsewhere (Stop, abort) is done.
        active = {row["id"] for row in rows}
        for ident in [key for key in _unsettled if key not in active]:
            _unsettled.pop(ident, None)
    for row in rows:
        try:
            _reap_one(row)
        except Exception:
            logger.exception("Failed to reap chat run %s", row["id"])
            try:
                chat_store.abort_active(row["id"], "interrupted")
            except Exception:
                # Keep reaping the remaining rows; this one retries next tick.
                logger.exception("Failed to abort chat run %s after reap error", row["id"])


def _reap_one(row):
    ident = row["id"]
    task = _tasks.get(ident)
    live = task is not None and not task.done()
    if not live and ident in _unsettled:
        _settle_unsettled(row)
        return
    if row["cancel_requested"]:
        # cancel_requested_at is stamped at request time and never refreshed
        # by streaming checkpoints (unlike updated_at), so a worker that keeps
        # generating past a Stop cannot pin itself in "stopping" forever.
        stamp = (row["cancel_requested_at"] if "cancel_requested_at" in row.keys() else "") or row["updated_at"]
        if _iso_age(stamp) >= FORCE_CANCEL_SECONDS:
            chat_store.abort_active(ident, "cancelled")
        elif live:
            cancel(ident)
        else:
            chat_store.abort_active(ident, "cancelled")
        return
    if live:
        stamp = ""
        if "updated_at" in row.keys() and row["updated_at"]:
            stamp = row["updated_at"]
        quiet = _iso_age(stamp or row["created_at"])
        if quiet >= STALL_IDLE_SECONDS:
            cancel(ident)
            chat_store.abort_active(ident, "interrupted", chat_store.STALL_NOTICE)
            _wake_stalled_child(row)
        return
    # No local worker: recover from the durable row, which needs the heavy
    # blobs the scan no longer ships.
    full = db.fetchone("SELECT * FROM chat_runs WHERE id=?", (ident,))
    if full is not None:
        chat_store.recover_one(full)


def _wake_stalled_child(row) -> None:
    parent = row["parent_run_id"] if "parent_run_id" in row.keys() else None
    if not parent:
        return
    try:
        schedule_wake(row["user_id"], row["thread_id"])
    except Exception:
        logger.exception("Failed to schedule wake for stalled child %s", row["id"])


_wake_locks: dict[tuple[int, object], asyncio.Lock] = {}
_wake_tasks: dict[tuple[int, object], asyncio.Task] = {}
_wake_again: set[tuple[int, object]] = set()


def _thread_wake_lock(user_id: int, thread_id) -> asyncio.Lock:
    # Wake ordering only matters within one thread; a completion storm across
    # many threads must not queue behind a single process-wide lock.
    key = (user_id, thread_id)
    lock = _wake_locks.get(key)
    if lock is None:
        lock = _wake_locks[key] = asyncio.Lock()
    return lock


def _prune_wake_locks() -> None:
    if len(_wake_locks) <= 1024:
        return
    busy = set(_wake_tasks)
    for key, lock in list(_wake_locks.items()):
        if key not in busy and not lock.locked():
            _wake_locks.pop(key, None)


def schedule_wake(user_id: int, thread_id) -> None:
    """Maybe start a continuation turn delivering finished background subagents and commands."""
    if _stopping or thread_id is None:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    key = (user_id, thread_id)
    if key in _wake_tasks:
        _wake_again.add(key)
        return
    async def _run():
        try:
            while True:
                _wake_again.discard(key)
                await _wake_parent(user_id, thread_id)
                if key not in _wake_again or _stopping:
                    break
        except Exception:
            logger.exception("Wake parent failed for thread %s", thread_id)
        finally:
            _wake_tasks.pop(key, None)
            _wake_again.discard(key)
            _prune_wake_locks()

    _wake_tasks[key] = loop.create_task(_run(), name=f"chat-wake-{thread_id}")


def sweep_wakes() -> None:
    """Retry durable reports whose wake was deferred by generation capacity."""
    if _stopping:
        return
    for row in db.fetchall(
        """SELECT DISTINCT user_id,thread_id FROM chat_runs
           WHERE parent_run_id IS NOT NULL
             AND status IN ('completed','failed','cancelled','interrupted')
             AND json_extract(request, '$.agent.background') = 1
             AND COALESCE(json_extract(request, '$.agent.wake_sent'), 0) = 0
             AND COALESCE(json_extract(request, '$.agent.wake_attempts'), 0) < 2
             AND NOT (status='cancelled' AND cancel_requested=?)""",
        (chat_store.USER_STOPPED,),
    ):
        schedule_wake(row["user_id"], row["thread_id"])


async def wait_for_run(run_id: str, timeout: float) -> None:
    """Join a local worker without cancelling it when the caller stops waiting."""
    task = _tasks.get(run_id)
    if task is not None and not task.done():
        await asyncio.wait({task}, timeout=max(0.0, timeout))
    elif timeout > 0:
        # Orphan recovery may still be pending; avoid a busy loop.
        await asyncio.sleep(min(timeout, 2.0))


async def _wake_parent(user_id: int, thread_id) -> None:
    from . import accounts, chats
    from uuid import uuid4

    if _stopping:
        return
    async with _thread_wake_lock(user_id, thread_id):
        if _stopping:
            return
        root = await asyncio.to_thread(chat_store.thread_root_run, user_id, thread_id)
        if root is None or root["status"] in chat_store.ACTIVE:
            return
        candidates = await asyncio.to_thread(chat_store.wake_candidates, user_id, thread_id)
        if root["status"] == "cancelled" and not any(
            chat_store.resumed_after_stop(item, root.get("stopped_at") or "") for item in candidates
        ):
            # A hard user stop is final: no automated turn may restart the
            # thread, no matter which sweep or child finishing asks. Stranded
            # reports wait as wake candidates and ride with the user's next
            # message (chat_runtime injects them). System interruptions (a
            # service restart or stall) end as interrupted/failed and still
            # wake; only an explicit Stop suppresses delivery. The exception
            # is a subagent the user resumed after that Stop: resuming it is
            # an explicit request to carry on, so its report wakes the thread.
            return
        # A failed wake turn may retry each child once; after that the report
        # stays in the transcript and list_subagents instead of looping forever.
        # A subagent the user stopped by itself is the same hard stop for that
        # agent: its report rides with the user's next message (chat_runtime
        # injects stranded reports) and never starts a turn on its own.
        candidates = [item for item in candidates
                      if int(item.get("attempts") or 0) < 2 and not item.get("user_stopped")]
        from . import command_jobs

        cmd_results = await asyncio.to_thread(command_jobs.pending_envelopes, user_id, thread_id)
        if not candidates and not cmd_results:
            return
        owner = await asyncio.to_thread(accounts.get_user, user_id)
        if owner is None:
            return
        revision = await asyncio.to_thread(chat_store.thread_revision, user_id, thread_id)
        if revision is None:
            return
        included = candidates[:8]
        envelopes = [
            chat_store.render_task_output(
                child_id=item["id"],
                state=item["status"],
                text=(item["summary"] or item["status"]).strip() or "Subagent finished without a report.",
                description=item["description"],
                generation=item.get("generation", ""),
            )
            for item in included
        ]
        # Whole reports only: if the batch cannot fit a turn, deliver fewer
        # children now (the rest stay wake-pending) instead of cutting text.
        budget = chats.AUTO_CONTENT_MAX - 2048
        while len(included) > 1 and sum(len(env) for env in envelopes) > budget:
            included.pop()
            envelopes.pop()
        ids = [item["id"] for item in included]
        # Only whole envelopes included in the durable wake may be acknowledged.
        # Command results that do not fit stay pending for the next wake.
        used = sum(len(env) + 2 for env in envelopes)
        selected_commands = []
        for item in cmd_results:
            size = len(item["envelope"]) + 2
            if used + size <= budget:
                selected_commands.append(item)
                used += size
        cmd_results = selected_commands
        live_monitor = any("<monitor_event" in str(item.get("envelope") or "") for item in cmd_results)
        if live_monitor:
            opener = (
                "AUTOMATED EVENT - not written by the user. A watch fired or background work reported. "
                "These are not new user requests. A <monitor_event> means the condition matched; do not start another watch. "
                "A <monitor_result> means that watch ended."
            )
        else:
            opener = (
                "AUTOMATED EVENT - not written by the user. Background work you started earlier has finished. "
                "These are child reports, finished commands, or errors — not new user requests."
            )
        lines = [opener]
        lines.extend("\n" + env for env in envelopes)
        for item in cmd_results:
            lines.append("\n" + item["envelope"])
        if len(candidates) > len(included):
            lines.append(
                f"\n({len(candidates) - len(included)} more finished subagents are not shown; "
                "use list_subagents and get_subagent_output to read them.)"
            )
        closing = (
            "\nSummarize relevant findings without repeating full reports. Continue only work within the user's "
            "current scope; do not revive cancelled or superseded tasks."
        )
        if included:
            closing += " Use spawn_subagent with task_id=<id> to continue a child from its saved transcript."
        lines.append(closing)
        text = "\n".join(lines)
        try:
            result = await durable_call(
                chat_store.start_turn,
                user_id, thread_id, text, revision, f"wake-{uuid4().hex[:12]}",
                auto=True,
                command_receipt_ids=[item["id"] for item in cmd_results],
            )
        except chats.ChatValidationError as exc:
            logger.exception("Wake turn failed for thread %s", thread_id)
            # 413 is the message cap — deterministic, so count it. 409/429
            # are capacity races; leave attempts so sweep_wakes can retry.
            if int(getattr(exc, "status_code", 0) or 0) == 413:
                await durable_call(chat_store.bump_wake_attempts, ids)
            return
        except Exception:
            logger.exception("Wake turn failed for thread %s", thread_id)
            return
        run = result.get("run")
        if run is None:
            await durable_call(chat_store.bump_wake_attempts, ids)
            return
        await durable_call(chat_store.mark_run_wake, run["id"], ids, receipts=dict(zip(ids, envelopes)))
        try:
            await start(internal_request(user_id), dict(owner), result["run"])
        except Exception:
            logger.exception("Wake run failed to start for thread %s", thread_id)


async def _run(request, user, run):
    task = asyncio.current_task()
    task._fuse_started = True
    ident = run["id"]
    output = Output(run)
    pending = []
    pending_bytes = 0
    previous_text = {
        "content": output.message.get("content") or "",
        "reasoning": output.message.get("reasoning") or "",
    }
    previous_segments = copy.deepcopy(output.message.get("segments") or [])
    iterator = worker = None
    status = "failed"
    persist_failed = False
    started = time.monotonic()
    elapsed_before = max(0, int(output.message.get("elapsedMs") or 0))
    # Readers (Work list, run snapshots) add this leg's unflushed tail; the
    # leg ends with the task, so idle time after it is never counted.
    leg = chat_store.begin_leg(ident, elapsed_before)
    task.add_done_callback(lambda _task: chat_store.end_leg(ident, leg))
    agent = run.get("request", {}).get("agent") if isinstance(run.get("request"), dict) else {}
    agent = agent if isinstance(agent, dict) else {}

    flush_lock = asyncio.Lock()
    cancel_signal = _register_cancel_signal(ident)

    async def persist_checkpoint(stored, events, durable=True, state_only=False):
        # Cancelling an asyncio waiter does not cancel a running thread. Drain
        # the actual write before releasing the run's persistence lock.
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        if state_only:
            write = asyncio.create_task(
                asyncio.to_thread(chat_store.flush_pending_state, ident, stored, future=future)
            )
        else:
            write = asyncio.create_task(
                asyncio.to_thread(chat_store.checkpoint, ident, stored, events=events, durable=durable, future=future)
            )
        cancelled = False
        while not write.done():
            try:
                await asyncio.shield(write)
            except asyncio.CancelledError:
                cancelled = True
            except Exception:
                break
        wrote = None
        try:
            wrote = write.result()
        except Exception:
            if cancelled:
                raise asyncio.CancelledError
            raise
        # The publication may still be queued in the group committer. This
        # run's next flush and its terminal event must never commit ahead of
        # it, so wait for the event to land before releasing the flush lock.
        while not future.done():
            try:
                await asyncio.shield(future)
            except asyncio.CancelledError:
                cancelled = True
        # A failed publication must not advance the producer's patch base.
        # Inspect already-completed futures too (a fast failing batch can
        # finish before the state writer returns).
        future.result()
        if wrote:
            # checkpoint() wrote the durable row and its event has committed:
            # refine the run's replay stamp so snapshots advance past it.
            chat_store.stamp_state_cursor(ident, user["id"])
        return cancelled

    async def flush(durable=True, state_only=False):
        async with flush_lock:
            await flush_batch(durable=durable, state_only=state_only)

    async def flush_batch(durable=True, state_only=False):
        nonlocal pending_bytes, previous_segments
        output.message["elapsedMs"] = min(86_400_000, elapsed_before + int((time.monotonic() - started) * 1000))
        # When the turn last did work; the final flush leaves it at the end
        # ("✻ Cooked for 2m 13s · done 3:42 PM" in Console).
        output.message["endedAt"] = db.now()
        if not pending:
            if not state_only:
                return
            # Trailing snapshot owed by a throttled flush: its events already
            # published, so persist the newest output without republishing.
            cancelled = await persist_checkpoint(output.stored(light=True), [], state_only=True)
            if cancelled:
                raise asyncio.CancelledError
            return
        if pending:
            # Normalized suffix patches are replayable even across a checkpoint
            # taken halfway through an upstream <think> tag.
            stored = output.stored(light=True)
            batch_count, batch_bytes = len(pending), pending_bytes
            patch = {"event": "message.patch", "elapsedMs": stored["elapsedMs"], "endedAt": stored.get("endedAt")}
            for field in ("content", "reasoning"):
                old, new = previous_text[field], stored.get(field, "")
                offset = len(old)
                if not new.startswith(old):
                    offset = 0
                    while offset < min(len(old), len(new)) and old[offset] == new[offset]:
                        offset += 1
                # Offsets are UTF-16 code units, matching browser String.slice.
                patch[field] = {"from": len(old[:offset].encode("utf-16-le")) // 2, "text": new[offset:]}
            patch["usage"] = stored.get("usage")
            patch["error"] = stored.get("error", "")
            if output._stream_out:
                patch["streamOut"] = int(output._stream_out)
            segment_patch = _segments_patch(previous_segments, stored.get("segments") or [])
            if segment_patch:
                patch["segments"] = segment_patch
            # Raw SSE objects are owned exclusively by this loop once parsed;
            # no concurrent mutator exists, so no defensive copy is needed.
            events = [obj for obj in pending[:batch_count] if obj.get("fuse")]
            events.insert(0, {"fuse": patch})
            cancelled = await persist_checkpoint(stored, events, durable=durable)
            previous_text.update({field: stored.get(field, "") for field in previous_text})
            previous_segments = [dict(part) for part in (stored.get("segments") or [])]
            del pending[:batch_count]
            pending_bytes -= batch_bytes
            if cancelled:
                raise asyncio.CancelledError

    async def periodic():
        nonlocal persist_failed
        flush_failures = 0
        next_poll = time.monotonic()
        while True:
            now = time.monotonic()
            if now >= next_poll:
                next_poll = now + STATUS_POLL_SECONDS
                row = await asyncio.to_thread(db.fetchone, "SELECT status,cancel_requested FROM chat_runs WHERE id=? AND user_id=?", (ident, user["id"]))
                if row is None or row["cancel_requested"] or row["status"] not in chat_store.ACTIVE:
                    _cancelled.add(ident)
                    task.cancel()
                    return
            if cancel_signal is not None and cancel_signal.is_set():
                _cancelled.add(ident)
                task.cancel()
                return
            try:
                if cancel_signal is not None:
                    await asyncio.wait_for(cancel_signal.wait(), timeout=FLUSH_SECONDS)
                else:
                    await asyncio.sleep(FLUSH_SECONDS)
            except TimeoutError:
                pass
            if not pending or flush_lock.locked():
                # Nothing new, or an inline (tool/permission) flush is already
                # persisting under the lock; the next tick re-checks.
                if not pending and not flush_lock.locked() and chat_store.state_write_pending(ident):
                    # A throttled flush left the durable row behind the
                    # journal; catch it up so restart recovery never misses
                    # the tail of a burst that has gone quiet.
                    try:
                        await flush(state_only=True)
                        flush_failures = 0
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        flush_failures += 1
                        logger.exception("Chat checkpoint failed for %s", ident)
                        if flush_failures >= 3:
                            persist_failed = True
                            task.cancel()
                            return
                continue
            try:
                await flush(durable=False)
                flush_failures = 0
            except asyncio.CancelledError:
                raise
            except Exception:
                flush_failures += 1
                logger.exception("Chat checkpoint failed for %s", ident)
                if flush_failures >= 3:
                    persist_failed = True
                    task.cancel()
                    return

    try:
        if ident in _cancelled or run["cancel_requested"] or _stopping:
            if run["cancel_requested"]:
                _cancelled.add(ident)
            raise asyncio.CancelledError
        if await persist_checkpoint(output.stored(), []):
            raise asyncio.CancelledError
        worker = asyncio.create_task(periodic())
        try:
            configured = agent.get("remaining_seconds")
            seconds = float(configured if configured not in (None, "") else MAX_RUNTIME_SECONDS)
        except (TypeError, ValueError):
            configured = None
            seconds = MAX_RUNTIME_SECONDS
        seconds = min(seconds, float(MAX_RUNTIME_SECONDS))
        if configured not in (None, ""):
            seconds = max(30.0, seconds)
        else:
            seconds = max(0.001, seconds)
        async with asyncio.timeout(seconds):
            response = await chat_runtime.stream_dashboard_chat(request, user, run["request"], frozen_context=run["request"])
            iterator = response.body_iterator
            _iterators[ident] = iterator
            buf = b""
            done = False
            async for chunk in chat_runtime._terminated_chunks(iterator):
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    if len(line) > MAX_FRAME_BYTES:
                        raise ValueError("Generation frame limit reached.")
                    line = line.strip()
                    if not line.startswith(b"data:"):
                        continue
                    data = line[5:].strip()
                    if data == b"[DONE]":
                        done = True
                        break
                    if not data:
                        continue
                    try:
                        obj = json.loads(data)
                    except json.JSONDecodeError:
                        logger.warning("Skipping malformed generation frame for %s", ident)
                        continue
                    if not isinstance(obj, dict):
                        raise ValueError("Invalid generation frame.")
                    delivery = obj.get("fuse")
                    if isinstance(delivery, dict) and delivery.get("event") == "steer.delivered":
                        await flush()
                        await asyncio.to_thread(chat_store.seal_steer_display, ident, delivery.get("ids") or [])
                        continue
                    had_usage = isinstance(output.message.get("usage"), dict)
                    output.apply(obj)
                    pending.append(obj)
                    pending_bytes += len(data)
                    fuse = obj.get("fuse") if isinstance(obj.get("fuse"), dict) else None
                    fuse_event = fuse.get("event") if fuse else None
                    tool_status = fuse_event == "tool" and fuse.get("status")
                    got_usage = (not had_usage) and isinstance(output.message.get("usage"), dict)
                    if tool_status in ("running", "done", "error", "interrupted") or fuse_event in ("context", "activity", "permission", "heartbeat") or got_usage:
                        await flush()
                    elif len(pending) >= 128 or pending_bytes >= 64_000:
                        await flush()
                    await asyncio.sleep(0)
                if done:
                    break
                if len(buf) > MAX_FRAME_BYTES:
                    raise ValueError("Generation frame limit reached.")
            if not done:
                raise ValueError("Generation stream ended without [DONE].")
            if (
                not output.message.get("content")
                and not output.message.get("reasoning")
                and not output.message.get("error")
                and not output.message.get("tools")
            ):
                output.message["error"] = "The model returned an empty reply."
            is_child = bool(run.get("parent_run_id") or agent.get("parent_run_id"))
            if is_child and not output.message.get("content") and not output.message.get("error"):
                writeup = chat_store.child_writeup({"id": ident, "status": "completed", "message": output.message, "request": run.get("request")})
                if writeup:
                    output.message["content"] = writeup[:chats.ASSISTANT_CONTENT_MAX]
                    segs = list(output.message.get("segments") or [])
                    segs.append({"type": "content", "text": writeup[:chats.ASSISTANT_CONTENT_MAX]})
                    output.message["segments"] = segs
                else:
                    output.message["error"] = "The subagent finished without a report."
            status = "failed" if output.message.get("error") else "completed"
    except asyncio.CancelledError:
        if persist_failed and ident not in _cancelled:
            # periodic() stopped the run because progress could not be saved;
            # that is a failure, not a server shutdown.
            status = "failed"
            output.message["error"] = "Generation stopped: Fuse could not save its progress."
        else:
            status = "cancelled" if ident in _cancelled else "interrupted"
            output.message["stopped"] = True
    except Exception as exc:
        if isinstance(exc, TimeoutError):
            error = "Generation exceeded its time limit."
        elif getattr(exc, "detail", None) or isinstance(exc, ValueError):
            # HTTP/validation details and Fuse's own ValueErrors are written
            # for the user. Anything else (driver, OS, bugs) stays in the log.
            error = str(getattr(exc, "detail", None) or exc)
        else:
            logger.exception("Chat run %s failed", ident)
            error = ""
        output.message["error"] = (error or "Generation failed.")[:chats.ERROR_MAX]
    finally:
        if worker is not None:
            worker.cancel()
            try:
                await asyncio.gather(worker, return_exceptions=True)
            except asyncio.CancelledError:
                pass
        try:
            if _stopping and status == "interrupted":
                live_row = await asyncio.to_thread(chat_store.get_run, user["id"], ident)
                if (
                    live_row is not None
                    and live_row["status"] in chat_store.ACTIVE
                    and not live_row["cancel_requested"]
                    and live_row["provider_messages"]
                ):
                    # Graceful shutdown with durable progress: leave the row
                    # ACTIVE so the next boot's recover_runs() resumes it from
                    # the saved transcript instead of finalizing interrupted.
                    # The resume note is idempotent, so back-to-back restarts
                    # cannot stack "continue" prompts.
                    logger.info("Leaving run %s active for restart resume", ident)
                    return
            try:
                await flush()
            except Exception:
                logger.exception("Chat final flush failed for %s", ident)
            row = await asyncio.to_thread(chat_store.get_run, user["id"], ident)
            if row and row["cancel_requested"]:
                status = "cancelled"
                output.message["stopped"] = True
            output.message["status"] = status
            if status == "interrupted":
                output.message["notice"] = "Generation interrupted by server shutdown."
            kept = []
            for tool in output.message["tools"]:
                if (
                    tool.get("name") == "get_subagent_output"
                    and tool.get("status") in ("running", "progress", "interrupted")
                    and tool.get("preview") == "still running"
                ):
                    child = await asyncio.to_thread(chat_store.get_run, user["id"], str(tool.get("child_id") or ""))
                    if child and child["status"] == "completed":
                        summary = (child.get("message") or {}).get("content") or ""
                        tool["status"] = "done"
                        if summary:
                            tool["summary"] = str(summary)[:2000]
                            tool["preview"] = str(summary)[:800]
                        kept.append(tool)
                        continue
                    if child and child["status"] in chat_store.ACTIVE:
                        tool["status"] = "running"
                        kept.append(tool)
                        continue
                    tool["status"] = "interrupted" if not child or child["status"] in ("cancelled", "interrupted") else "error"
                    if child:
                        summary = chat_store.child_writeup(child)
                        tool["summary"] = summary[:2000]
                        tool["preview"] = summary[:800]
                        if tool["status"] == "error":
                            tool["error"] = str((child.get("message") or {}).get("error") or "Subagent failed")[:400]
                    kept.append(tool)
                    continue
                if tool.get("status") in ("running", "progress"):
                    if tool.get("name") in ("run_terminal_command", "get_command_output", "monitor") and tool.get("child_id"):
                        # Handed-off commands keep running past the turn; report
                        # their real state instead of labelling them interrupted.
                        from . import command_jobs

                        job_state = await asyncio.to_thread(command_jobs.job_status, user["id"], str(tool["child_id"]))
                        if job_state is None:
                            tool["status"] = "interrupted"
                        elif job_state == "completed":
                            tool["status"] = "done"
                        elif job_state in ("cancelled", "interrupted", "lost"):
                            tool["status"] = "interrupted"
                        elif job_state in command_jobs.TERMINAL:
                            tool["status"] = "error"
                        else:
                            tool["status"] = "running"
                    elif tool.get("name") == "spawn_subagent" and tool.get("child_id"):
                        child = await asyncio.to_thread(chat_store.get_run, user["id"], str(tool["child_id"]))
                        child_agent = (child.get("request") or {}).get("agent") if child else {}
                        child_agent = child_agent if isinstance(child_agent, dict) else {}
                        if child and child["status"] == "completed":
                            tool["status"] = "done"
                        elif child and child["status"] == "failed":
                            tool["status"] = "error"
                            summary = chat_store.child_writeup(child)
                            tool["error"] = summary[:400]
                            tool["summary"] = summary[:2000]
                            tool["preview"] = summary[:800]
                        elif child and child["status"] in chat_store.ACTIVE and child_agent.get("background"):
                            tool["status"] = "running"
                        else:
                            tool["status"] = "interrupted"
                    else:
                        tool["status"] = "interrupted"
                kept.append(tool)
            output.message["tools"] = kept

            def _settle_commands():
                with db.tx() as conn:
                    chat_store.settle_command_traces(conn, user["id"], run["thread_id"], [output.message])

            await durable_call(_settle_commands)
            ids = {t.get("id") for t in kept if t.get("id")}
            output.message["segments"] = [
                part
                for part in (output.message.get("segments") or [])
                if part.get("type") != "tool" or part.get("id") in ids
            ]
            parent = not bool(run.get("parent_run_id") or agent.get("parent_run_id"))
            # Every spawning agent owns its foreground descendants, including
            # nested agents that time out while awaiting a foreground child.
            doomed = await durable_call(chat_store.request_cancel_descendants, user["id"], ident, skip_background=True)
            if status != "cancelled":
                # User Stop stays quiet. A parent that completed, failed, or
                # was interrupted still owes the thread a report for any
                # foreground child it is about to kill.
                for did in doomed:
                    await durable_call(chat_store.mark_child_background, did)
            for did in doomed:
                cancel(did)
            from . import permissions as permissions_mod

            permissions_mod.cancel_run_asks(ident)
            chat_store.end_leg(ident, leg)
            if not await _finish_durably(ident, output.stored(), status):
                # The terminal row never committed. Leave it ACTIVE and parked
                # in _unsettled: the reaper retries this finish rather than
                # resuming a turn that already ended, and no push or wake
                # announces a state that is not durable.
                return
            current = await asyncio.to_thread(chat_store.get_run, user["id"], ident)
            if not parent and status in chat_store.TERMINAL and current is not None:
                # Follow-ups that raced the finish line were never consumed.
                # A completed child simply continues (same run id, seamless
                # from the parent's perspective); anything else is surfaced
                # to the parent through the wake envelope so it can continue
                # the child with spawn_subagent task_id. A user-cancelled child
                # is never auto-continued, but the pending prompts are still
                # reported.
                leftover = await asyncio.to_thread(chat_store.take_followups, ident)
                if leftover:
                    continued = False
                    if not current.get("cancel_requested"):
                        continued = await durable_call(chat_store.continue_child_with_followups, ident, leftover)
                    if not continued:
                        await durable_call(chat_store.flag_unconsumed_followups, ident, leftover)
                    current = await asyncio.to_thread(chat_store.get_run, user["id"], ident) or current
            continuing = False
            if current and current["status"] == "queued":
                _agent = (current.get("request") or {}).get("agent") or {}
                continuing = bool(_agent.get("pending_steers") or _agent.get("followup_continuation"))
            if current and current["status"] in chat_store.ACTIVE and not continuing:
                await durable_call(chat_store.abort_active, ident, status if status in chat_store.TERMINAL else "interrupted")
            if status in chat_store.TERMINAL and not continuing:
                child_agent = {}
                if current and isinstance(current.get("request"), dict):
                    child_agent = current["request"].get("agent") if isinstance(current["request"].get("agent"), dict) else {}
                if status in ("completed", "failed") and parent and not _stopping:
                    # Server-owned runs outlive the browser that sent them;
                    # every subscribed device learns the turn is done.
                    try:
                        from . import push

                        asyncio.create_task(
                            push.notify_run_done(
                                user["id"],
                                run["thread_id"],
                                status,
                                content=output.message.get("content") or "",
                                error=output.message.get("error") or "",
                            )
                        )
                    except Exception:
                        logger.exception("Chat push failed to schedule for %s", ident)
                if current and current.get("parent_run_id"):
                    # A cancelled/interrupted background child still owes the
                    # parent a report; so does one that finished with queued
                    # follow-ups it never consumed.
                    if status in ("cancelled", "interrupted") and not child_agent.get("background"):
                        await durable_call(chat_store.mark_child_background_if_pending, ident)
                        current = await asyncio.to_thread(chat_store.get_run, user["id"], ident) or current
                        child_agent = (
                            current["request"].get("agent")
                            if isinstance(current.get("request"), dict) and isinstance(current["request"].get("agent"), dict)
                            else {}
                        )
                    if child_agent.get("background") and not child_agent.get("wake_sent"):
                        # Follow-ups were already handled above: either the
                        # run was continued (not terminal anymore) or the note
                        # was folded into the child's message by
                        # flag_unconsumed_followups, so child_writeup includes it.
                        text = chat_store.child_writeup(current)
                        envelope = chat_store.render_task_output(
                            child_id=ident,
                            state="completed" if status == "completed" else status,
                            text=text,
                            description=str(child_agent.get("description") or ""),
                            generation=str(child_agent.get("delivery_generation") or ""),
                        )
                        if not await durable_call(chat_store.queue_child_result, str(current["parent_run_id"]), envelope, child_id=ident):
                            schedule_wake(user["id"], run["thread_id"])
                    else:
                        schedule_wake(user["id"], run["thread_id"])
                else:
                    if child_agent.get("wake"):
                        if status == "completed":
                            # The wake turn delivered its reports; only now mark
                            # the children sent so a failed wake can retry. A
                            # child re-spawned during this turn (new leg) or one
                            # whose envelope is still queued unconsumed stays
                            # unacknowledged so its own completion wakes again.
                            await durable_call(
                                chat_store.mark_wake_sent,
                                child_agent.get("wake_ids") or [], pending_guard_run=ident
                            )
                        else:
                            # Stopped or failed wake: acknowledge children whose
                            # report is already durable in this turn's seed or
                            # transcript (the user saw the bubble; a stopped
                            # turn must not be retried into a loop) and spend
                            # retry budget only on genuinely undelivered ones.
                            await durable_call(chat_store.settle_wake_children, ident, child_agent.get("wake_ids") or [])
                    schedule_wake(user["id"], run["thread_id"])
        except Exception:
            logger.exception("Chat run finalization failed for %s", ident)
            await durable_call(chat_store.abort_active, ident, status if status in chat_store.TERMINAL else "interrupted")
        finally:
            if iterator is not None and hasattr(iterator, "aclose"):
                try:
                    async with asyncio.timeout(CLEANUP_SECONDS):
                        await iterator.aclose()
                except (Exception, asyncio.CancelledError):
                    logger.warning("Chat iterator cleanup did not finish for %s", ident)
            if _iterators.get(ident) is iterator:
                _iterators.pop(ident, None)
            if _tasks.get(ident) is task:
                _tasks.pop(ident, None)
            _cancelled.discard(ident)
            _cancel_signals.pop(ident, None)
            current = await asyncio.to_thread(chat_store.get_run, user["id"], ident)
            if current and current["status"] == "queued" and not _stopping:
                await start(request, user, current)


async def resume_queued(user_id=None):
    """Launch queued runs (root and child, including those re-queued after recover_runs)."""
    if _stopping:
        return
    from . import accounts

    if user_id is None:
        rows = await asyncio.to_thread(db.fetchall,
            """SELECT id, user_id FROM chat_runs WHERE status='queued'
               ORDER BY created_at,id"""
        )
    else:
        rows = await asyncio.to_thread(db.fetchall,
            """SELECT id, user_id FROM chat_runs WHERE status='queued' AND user_id=?
               ORDER BY created_at,id""",
            (user_id,),
        )
    for row in rows:
        if row["id"] in _tasks and not _tasks[row["id"]].done():
            continue
        owner = await asyncio.to_thread(accounts.get_user, row["user_id"])
        if owner is None:
            continue
        # Queued runs about to launch need their full request; fetch it only
        # now so the periodic scan does not ship every blob every 60 s.
        full = await asyncio.to_thread(db.fetchone, "SELECT * FROM chat_runs WHERE id=?", (row["id"],))
        run = chat_store.run_view(full)
        if run is None:
            continue
        await start(internal_request(run["user_id"]), dict(owner), run)


async def stop():
    """Drain local generation as interrupted during application shutdown."""
    global _stopping
    _stopping = True
    tasks = list(_tasks.values())
    for task in tasks:
        if getattr(task, "_fuse_started", False):
            task.cancel()
    # Wake tasks query the database off-loop; left running they outlive the
    # pool (or a test's connection) once shutdown closes it. Their durable
    # writes are admission-guarded, and the next boot's sweep re-derives any
    # wake that was cut short.
    loop = asyncio.get_running_loop()
    wakes = [task for task in _wake_tasks.values() if not task.done() and task.get_loop() is loop]
    for task in wakes:
        task.cancel()
    tasks = [*tasks, *wakes]
    if tasks:
        # Bounded: workers still unwinding after the deadline are abandoned
        # (their rows keep the durable-transcript resume path), so shutdown
        # can never hang past the unit's stop timeout. Permission waits and
        # Jev HTTP must observe _stopping / CancelledError inside this window.
        await asyncio.wait(tasks, timeout=4.0)


shutdown = stop
