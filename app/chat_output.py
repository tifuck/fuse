"""Output: folds one run's provider stream into the stored assistant message
(content, reasoning, tool traces, segments, usage) for chat_runs to flush."""
from __future__ import annotations

import copy
import json
import re
import time

from . import chat_schema, chats


_THINK = re.compile(r"</?(?:think|thinking|thought|reasoning)>", re.I)
# Artifact writes whose streaming arguments the chat shows as "Writing…"
# before the call runs (the page itself can take minutes to stream).
_DRAFT_TOOLS = ("create_artifact", "update_artifact", "edit_artifact")
_DRAFT_TITLE = re.compile(r'"title"\s*:\s*"((?:[^"\\]|\\.){1,160})"')
_DRAFT_IDENT = re.compile(r'"identifier"\s*:\s*"([A-Za-z0-9_\-/:.]{8,200})"')


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
        self._draft = None
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
        merged, pending = chat_runtime.drop_tool_xml(text, stable)
        self._xml_pending = pending
        if pending and self._think_pending and pending.endswith(self._think_pending):
            # The held tail already carries the partial tag; restoring both on
            # the next chunk doubled it ("<tool_call<tool_call>").
            self._think_pending = ""
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
        self._draft = None

    def settle_tool_xml(self):
        """Release text held back as a possible text-format tool call once its
        round is over and it did not parse as one (prose that names
        <tool_call>). The provider transcript keeps that text; dropping it here
        cut the visible reply short, and finalize then replayed the short copy
        as a second assistant message."""
        pending, self._xml_pending = self._xml_pending, ""
        if not pending or chat_runtime.extract_text_tool_calls(pending)[1]:
            return
        self._append_text("content", pending, "tagged")
        self._scrub_stable = len(self.message["segments"][-1].get("text") or "")

    def _reset_stream_round(self):
        self.settle_tool_xml()
        self._ensure_aggregates()
        self._round_content = len(self.message.get("content") or "")
        self._round_reasoning = len(self.message.get("reasoning") or "")
        self._round_tool_chars = 0
        self._stream_out = 0
        self._draft = None

    def drafting(self) -> dict | None:
        """The artifact write being streamed this round: name, title, size."""
        draft = self._draft
        if not draft:
            return None
        head = draft["head"]
        title = _DRAFT_TITLE.search(head)
        ident = _DRAFT_IDENT.search(head)
        out = {"tool": draft["tool"], "chars": draft["chars"]}
        if title:
            try:
                out["title"] = json.loads('"' + title.group(1) + '"')[:80]
            except ValueError:
                pass
        if ident:
            from . import artifacts

            out["identifier"] = artifacts.normalize_identifier(ident.group(1))[:64]
        return out

    def _note_draft(self, name, args) -> None:
        # Some providers repeat the name on every delta: only a new name starts over.
        if isinstance(name, str) and name and not (self._draft and self._draft["tool"] == name):
            self._draft = {"tool": name, "chars": 0, "head": ""} if name in _DRAFT_TOOLS else None
        if self._draft is not None and isinstance(args, str) and args:
            self._draft["chars"] += len(args)
            if len(self._draft["head"]) < 2000:
                self._draft["head"] += args[: 2000 - len(self._draft["head"])]

    def _tool_call_delta_chars(self, obj) -> int:
        choice = (obj.get("choices") or [{}])[0] if isinstance(obj.get("choices"), list) else {}
        if not isinstance(choice, dict):
            return 0
        delta = choice.get("delta") if isinstance(choice.get("delta"), dict) else {}
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
            self._note_draft(name, args)
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
                elif line:
                    msg["segments"] = _with_notice(msg.get("segments") or [], line)
            if kind == "delivery" and isinstance(event.get("text"), str) and event["text"].strip():
                # Background results handed to the model at this point of the
                # reply (chat_generation.inbox). The next text or call starts
                # its own segment below it.
                segments = msg.setdefault("segments", [])
                text = event["text"]
                last = segments[-1] if segments else None
                count = sum(1 for part in segments if part.get("type") == "delivery")
                if last is None or last.get("type") != "delivery":
                    last = next((part for part in reversed(segments) if part.get("type") == "delivery"), None) \
                        if count >= chat_schema.DELIVERY_SEGMENTS_MAX else None
                if last is not None:
                    # Back-to-back arrivals (or a reply already holding the
                    # cap) share one segment; whole items only, never cut.
                    if len(last["text"]) + 1 + len(text) <= chat_schema.DELIVERY_TEXT_MAX:
                        last["text"] = last["text"] + "\n" + text
                elif len(segments) < chats.SEGMENT_MAX - 1:
                    segments.append({"type": "delivery", "text": text[: chat_schema.DELIVERY_TEXT_MAX]})
            if kind == "activity" and event.get("phase") == "model" and not event.get("thinking"):
                # A round starts; the mid-round thinking marker is not one.
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
                        perms = msg.setdefault("permissions", [])
                        old = next((p for p in perms if p.get("id") == ident), None)
                        event = {**(old or {}), **event}
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
                        description = event.get("description")
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
            text = error if isinstance(error, str) else error.get("message", "Upstream error") if isinstance(error, dict) else "Upstream error"
            msg["error"] = chats._legacy_text(text, chats.ERROR_MAX)
            if chat_runtime._provider_error_content(msg.get("content") or ""):
                msg["content"] = ""
                msg["segments"] = [
                    part for part in (msg.get("segments") or [])
                    if part.get("type") != "content" or not chat_runtime._provider_error_content(part.get("text") or "")
                ]
            return
        # Provider frames are untrusted: a non-dict choice/delta/message is
        # treated as empty instead of failing the whole run.
        choices = obj.get("choices")
        choice = choices[0] if isinstance(choices, list) and choices and isinstance(choices[0], dict) else {}
        delta = choice.get("delta") if isinstance(choice.get("delta"), dict) else {}
        message = choice.get("message") if isinstance(choice.get("message"), dict) else {}
        content = _take(delta.get("content")) or ("" if choice.get("delta") is not None else _take(message.get("content")))
        reasoning = [_take(delta.get(key)) or _take(message.get(key)) or _take(obj.get(key))
                     for key in ("reasoning_content", "reasoning", "thinking", "reasoning_text")]
        if content or any(reasoning):
            # Output supersedes a retry notice. Dropping it before appending
            # lets a resumed stream continue the same text segment.
            while msg["segments"] and _is_notice(msg["segments"][-1]):
                msg["segments"].pop()
        pending = self._remove_pending()
        for text in reasoning:
            self._append_text("reasoning", text, "native")
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


def _is_notice(part):
    return part.get("type") == "marker" and part.get("kind") == "notice"


def _with_notice(segments, line):
    """Append a run notice where it happened in the turn.

    Only the trailing run of notices is ever shown; earlier ones were
    superseded by output, so they are dropped here to keep a long turn's
    retries from filling SEGMENT_MAX.
    """
    tail = len(segments)
    while tail and _is_notice(segments[tail - 1]):
        tail -= 1
    if any(str(part.get("text") or "").lower() == line.lower() for part in segments[tail:]):
        return segments
    return [part for part in segments[:tail] if not _is_notice(part)] + segments[tail:] + [
        {"type": "marker", "kind": "notice", "text": line}
    ]


# Imported last: chat_runtime's import chain reaches chat_runs, which imports
# Output from this module, so Output must exist before that chain runs.
from . import chat_runtime  # noqa: E402
