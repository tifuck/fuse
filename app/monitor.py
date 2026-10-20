"""Line-oriented watch helpers for the monitor tool.

A monitor is a background job that turns stdout (or WebSocket text) into
batched <monitor_event> envelopes. Stderr is retained and never emitted as
events. Callers still own process lifecycle in command_jobs.
"""
from __future__ import annotations

import asyncio
import ipaddress
import logging
import re
import socket
import time
from urllib.parse import urlparse, urlunparse

import regex

logger = logging.getLogger(__name__)

BATCH_S = 0.2
MATCH_TIMEOUT_S = 0.05
# Total matching time one feed() may spend on the event loop. The per-line
# timeout alone let N slow lines stall the loop for N x MATCH_TIMEOUT_S.
FEED_MATCH_BUDGET_S = 0.1
DEFAULT_TIMEOUT_MS = 300_000
MAX_TIMEOUT_MS = 1_800_000
DEFAULT_MAX_EVENTS = 1
MAX_MAX_EVENTS = 50
MAX_PENDING_EVENTS = 64
MAX_BATCH_LINES = 40
FIREHOSE_WINDOW_S = 2.0
FIREHOSE_MAX = 8
ERR_TAIL_CHARS = 4_000
# Displayed lines and buffered logical lines have separate bounds.
MAX_LINE_CHARS = 2_000
MAX_MATCH_LINE_CHARS = 65_536
_WS_SCHEMES = ("ws", "wss")
EVENT_ID_RE = re.compile(r'<monitor_event id="([^"]+)" seq="(\d+)"')
RESULT_ID_RE = re.compile(r'<(?:command_result|monitor_result) id="([^"]+)"')


def deadline_ms(raw) -> int:
    if raw is None or raw == "":
        return DEFAULT_TIMEOUT_MS
    try:
        val = int(float(raw))
    except (TypeError, ValueError, OverflowError):
        return DEFAULT_TIMEOUT_MS
    if val <= 0:
        return DEFAULT_TIMEOUT_MS
    if val < 1000:
        val = val * 1000
    return max(1_000, min(val, MAX_TIMEOUT_MS))


def max_events(raw) -> int:
    if raw is None or raw == "":
        return DEFAULT_MAX_EVENTS
    try:
        val = int(float(raw))
    except (TypeError, ValueError, OverflowError):
        return DEFAULT_MAX_EVENTS
    if val <= 0:
        return DEFAULT_MAX_EVENTS
    return max(1, min(val, MAX_MAX_EVENTS))


_BATCH_NOTE = re.compile(
    r"… \d+ (?:more lines in this batch omitted|lines not checked: (?:the pattern was too slow for this output|line or pattern exceeded processing limits))"
)


def is_batch_note(line: str) -> bool:
    """A note LineBatcher appends to a batch, not a matched output line."""
    return _BATCH_NOTE.fullmatch(str(line)) is not None


def compile_pattern(raw: str):
    text = str(raw or "")
    if not text.strip():
        return None
    try:
        return regex.compile(text)
    except regex.error:
        raise ValueError("invalid monitor pattern") from None


def match_line(line: str, pattern) -> bool:
    text = str(line or "")
    if not text.strip():
        return False
    if pattern is None:
        return True
    # Model-supplied patterns run on the event loop; a backtracking blowup
    # must not stall every other request.
    try:
        return bool(pattern.search(text, timeout=MATCH_TIMEOUT_S))
    except TimeoutError:
        return False


def _resolves_public(host: str, port: int | None) -> bool:
    """True only when every DNS answer for host is globally routable.

    Name-based checks alone let a hostname pointing at a private address
    reach internal services from the server; the answers must be checked too.
    """
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError:
        return False
    seen = False
    for info in infos:
        addr = str(info[4][0]).split("%", 1)[0]
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            return False
        seen = True
        # Unwrap ::ffff:a.b.c.d: Python 3.12 calls mapped CGNAT global.
        if not (getattr(ip, "ipv4_mapped", None) or ip).is_global:
            return False
    return seen


def public_ws_url(raw: str) -> str:
    from .search.ssrf import _netloc, is_blocked_host

    url = str(raw or "").strip()
    if not url or len(url) > 2000:
        return ""
    try:
        parsed = urlparse(url)
        port = parsed.port
    except Exception:
        return ""
    if parsed.scheme not in _WS_SCHEMES:
        return ""
    if parsed.username or parsed.password:
        return ""
    host = (parsed.hostname or "").lower().rstrip(".")
    if not host or is_blocked_host(host):
        return ""
    if port not in (None, 80, 443):
        return ""
    if not _resolves_public(host, 443 if parsed.scheme == "wss" else 80):
        return ""
    path = parsed.path or "/"
    if parsed.params:
        path += ";" + parsed.params
    query = (parsed.query or "")[:800]
    http_scheme = "https" if parsed.scheme == "wss" else "http"
    netloc = _netloc(host, port, http_scheme)
    return urlunparse((parsed.scheme, netloc, path, "", query, ""))


def event_receipt_id(job_id: str, seq: int) -> str:
    return f"{job_id}:e{int(seq)}"


def parse_event_receipt(ident: str) -> tuple[str, int] | None:
    text = str(ident or "")
    if ":e" not in text:
        return None
    job_id, _, rest = text.rpartition(":e")
    if not job_id or not rest.isdigit():
        return None
    return job_id, int(rest)


def envelope_receipts(envelopes: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for text in envelopes or []:
        chunk = str(text)[:8000]
        for job_id, seq in EVENT_ID_RE.findall(chunk):
            ident = event_receipt_id(job_id, int(seq))
            if ident not in seen:
                seen.add(ident)
                out.append(ident)
        for job_id in RESULT_ID_RE.findall(chunk):
            if job_id not in seen:
                seen.add(job_id)
                out.append(job_id)
    return out


class LineBatcher:
    """Split a byte/text stream into 200ms line batches that match the filter."""

    def __init__(self, rec: dict, on_batch):
        self.rec = rec
        self.on_batch = on_batch
        self.buf = ""
        self.overlong = False
        self.nonempty = False
        self.pending: list[str] = []
        self.task: asyncio.Task | None = None
        self.pattern = rec.get("_pattern")
        self.closed = False
        self.stamps: list[float] = []
        self.overflow = 0
        self.skipped = 0

    def feed(self, text: str) -> None:
        if self.closed or not text:
            return
        budget_end = time.monotonic() + FEED_MATCH_BUDGET_S
        parts = text.split("\n")
        for i, part in enumerate(parts):
            room = MAX_MATCH_LINE_CHARS - len(self.buf)
            self.buf += part[:room]
            self.overlong |= len(part) > room
            self.nonempty |= bool(part.strip())
            if i < len(parts) - 1:
                if self.nonempty:
                    self._add(self.buf.rstrip("\r"), budget_end, overlong=self.overlong)
                self.buf = ""
                self.overlong = self.nonempty = False
        if (self.pending or self.skipped) and self.task is None and not self.closed:
            self.task = asyncio.create_task(self._flush())

    def _add(self, line: str, budget_end: float | None = None, *, overlong: bool = False) -> None:
        if self.pattern is not None and (overlong or (budget_end is not None and time.monotonic() > budget_end)):
            self.skipped += 1
            return
        if overlong:
            line += "…"
        # Match the whole line (match_line has its own timeout); only the
        # kept copy is cut, or a match past the cut is never seen.
        if match_line(line, self.pattern):
            if len(line) > MAX_LINE_CHARS:
                line = line[:MAX_LINE_CHARS] + "…"
            # Only the shown lines are kept; the rest is a count, so a
            # firehose cannot grow pending without limit.
            if len(self.pending) < MAX_BATCH_LINES:
                self.pending.append(line)
            else:
                self.overflow += 1

    def firehose(self) -> bool:
        now = time.monotonic()
        self.stamps = [t for t in self.stamps if now - t <= FIREHOSE_WINDOW_S]
        return len(self.stamps) >= FIREHOSE_MAX

    async def _flush(self) -> None:
        try:
            await asyncio.sleep(BATCH_S)
        except asyncio.CancelledError:
            return
        finally:
            self.task = None
        self._emit()

    def _emit(self) -> None:
        if not self.pending and not self.skipped:
            return
        lines = self.pending
        overflow, skipped = self.overflow, self.skipped
        self.pending, self.overflow, self.skipped = [], 0, 0
        if overflow > 0:
            lines.append(f"… {overflow} more lines in this batch omitted")
        if skipped > 0:
            lines.append(f"… {skipped} lines not checked: line or pattern exceeded processing limits")
        self.stamps.append(time.monotonic())
        try:
            self.on_batch(lines, overflow)
        except TypeError:
            try:
                self.on_batch(lines)
            except Exception:
                logger.exception("monitor batch delivery failed")
        except Exception:
            logger.exception("monitor batch delivery failed")

    async def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        task = self.task
        self.task = None
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        if self.nonempty:
            self._add(self.buf.rstrip("\r"), overlong=self.overlong)
        self.buf = ""
        self._emit()


def _description_line(rec: dict) -> list[str]:
    from .command_jobs import clean_description

    description = clean_description(rec.get("description"))
    return [f"description: {description}"] if description else []


def event_envelope(rec: dict, lines: list[str], seq: int) -> str:
    host = rec.get("host") or ""
    source = rec.get("source") or ("ws" if rec.get("url") else "command")
    target = (rec.get("url") or rec.get("command") or "")[:200]
    elapsed = 0
    if rec.get("started"):
        elapsed = int((time.monotonic() - float(rec["started"])) * 1000)
    body = "\n".join(lines) if lines else "(no lines)"
    rows = [
        f'<monitor_event id="{rec["id"]}" seq="{seq}" count="{int(rec.get("event_count") or 0)}">',
        f"host: {host} source: {source} target: {target}",
        *_description_line(rec),
        f"events: {int(rec.get('event_count') or 0)}",
        f"elapsed_ms: {elapsed}",
        "",
        body,
        "</monitor_event>",
    ]
    return "\n".join(rows)


def result_envelope(rec: dict) -> str:
    status = str(rec.get("status") or "")
    source = rec.get("source") or ("ws" if rec.get("url") else "command")
    target = (rec.get("url") or rec.get("command") or "")[:200]
    lines = [
        f'<monitor_result id="{rec["id"]}" state="{status}">',
        f"host: {rec.get('host') or ''} source: {source} target: {target}",
        *_description_line(rec),
        f"events: {int(rec.get('event_count') or 0)}",
    ]
    if rec.get("max_events"):
        lines.append(f"max_events: {int(rec['max_events'])}")
        if status == "completed" and int(rec.get("event_count") or 0) >= int(rec["max_events"]):
            lines.append("ended: max_events reached (normal stop)")
    if rec.get("timeout_ms"):
        lines.append(f"timeout_ms: {int(rec['timeout_ms'])}")
    if status == "completed" and rec.get("exit_code") is not None:
        lines.append(f"exit_code: {rec.get('exit_code')}")
    if rec.get("duration_ms") is not None:
        lines.append(f"duration_ms: {rec.get('duration_ms')}")
    if rec.get("error"):
        lines.append(f"error: {str(rec['error'])[:200]}")
    err = str(rec.get("err") or "").strip()
    if err:
        lines.append("stderr: retained (not used as events)")
    out = str(rec.get("out") or "")
    lines.append("")
    lines.append(out[-1500:] if out else "(no output)")
    if err:
        lines.append("")
        lines.append("[stderr]")
        lines.append(err[-800:])
    lines.append("</monitor_result>")
    return "\n".join(lines)
