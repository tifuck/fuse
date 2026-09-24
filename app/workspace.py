"""Workspace tools on user SSH hosts. Fuse-local is denied unless all three flags (later PR)."""
from __future__ import annotations

import asyncio
import codecs
import contextlib
import difflib
import fnmatch
import functools
import json
import logging
import ntpath
import posixpath
import re
import shlex
import stat
import time
import traceback
import weakref
from uuid import uuid4

from . import hosts
from .hosts import HostError
from .vault import VaultLocked

logger = logging.getLogger(__name__)

_DENY = re.compile(
    r"(?:^|[;&|]\s*)(?:rm\s+-rf\s+/|mkfs|shutdown|reboot|sudo\b|chmod\s+777)",
    re.I,
)
RESERVED = frozenset({"local", "localhost", "127.0.0.1", ""})
AGENTS_MAX = 12_000
AGENTS_FILENAMES = (
    "AGENTS.md",
    "agents.md",
    "AGENT.md",
    "agent.md",
    "CLAUDE.md",
    "claude.md",
)
_AGENTS_BASENAMES = frozenset(name.lower() for name in AGENTS_FILENAMES)
_agents_cache: dict[tuple, tuple] = {}
_GLOB_OK = re.compile(r"^[A-Za-z0-9_.*?/\[\]{}!,-]+$")
_GLOB_OK_WIN = re.compile(r"^[A-Za-z0-9_.*?/\\\[\]{}!,-]+$")
_DENY_WIN = re.compile(
    r"(?:Stop-Computer|Restart-Computer|(?:^|[;&|]\s*)shutdown\b|Format-Volume|Clear-Disk)",
    re.I,
)
_exec_times: dict[tuple[int, int], list[float]] = {}
_SSH_OUT_MAX = 65_536
# OpenCode read tool: 2000 lines, 50KB, lines longer than 2000 chars truncated in place.
_READ_DEFAULT_LIMIT = 2000
_READ_MAX_LINES = 2000
_READ_MAX_LINE = 2000
_READ_MAX_BYTES = 50 * 1024
_READ_MAX_CHARS = _READ_MAX_BYTES  # rendered bytes, including "N: " prefixes
_LIST_MAX_ENTRIES = 400
_GREP_DEFAULT_MATCHES = 50
_GREP_MAX_MATCHES = 200
_GREP_MAX_PATTERN = 2000
_GREP_TIMEOUT = 12
_GLOB_MAX_PATHS = 400
# Vendor/build trees rg would still walk when there is no .gitignore, and that
# grep -R always walks. Same names the Windows connector skips in WalkDir.
_SEARCH_SKIP_DIRS = (
    "node_modules",
    ".git",
    ".hg",
    ".svn",
    "venv",
    ".venv",
    "env",
    ".tox",
    "dist",
    "build",
    "out",
    "target",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "vendor",
    ".next",
    "coverage",
    ".gradle",
    ".idea",
    ".cache",
)


class WorkspaceError(HostError):
    def __init__(self, detail: str, status_code: int = 400, *, extra: dict | None = None):
        super().__init__(detail, status_code)
        self.extra = extra or {}


def _args(raw: str) -> dict:
    try:
        data = json.loads(raw or "{}")
    except (json.JSONDecodeError, TypeError, ValueError):
        raise WorkspaceError("arguments must be a complete JSON object") from None
    if not isinstance(data, dict):
        raise WorkspaceError("arguments must be a JSON object")
    return data


def _require(args: dict, *names: str) -> None:
    for name in names:
        if name not in args:
            raise WorkspaceError(f"{name} is required")


def _as_bool(value, default=False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        low = value.strip().lower()
        if low in ("true", "1", "yes"):
            return True
        if low in ("false", "0", "no"):
            return False
    if value is None:
        return default
    return bool(value)


def _int_arg(args: dict, key: str, default: int, lo: int, hi: int) -> int:
    raw = args.get(key)
    if raw is None or raw == "":
        return default
    try:
        val = int(float(raw))
    except (TypeError, ValueError, OverflowError):
        return default
    return max(lo, min(val, hi))


def _deadline_ms(args: dict) -> int:
    """Execution deadline in milliseconds.

    Contract: values >= 1000 are milliseconds (the documented unit); smaller
    positive ints are legacy seconds from the old schema. Default 30 min,
    ceiling 1 h (zombie guard, not a protocol limit).
    """
    from .command_jobs import DEFAULT_TIMEOUT_MS, MAX_TIMEOUT_MS

    raw = args.get("timeout")
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


def _clip_read_line(line: str) -> str:
    if len(line) <= _READ_MAX_LINE:
        return line
    return line[:_READ_MAX_LINE] + f"... (line truncated to {_READ_MAX_LINE} chars)"


def _read_render(chunk: list[str], offset: int, *, total: int | None, more: bool, max_chars: int) -> str:
    """Render read output with 'N: ' line prefixes and continuation footers
    (the OpenCode/Claude Code convention). Long lines are truncated in place;
    the page stops at max_chars without cutting a line in half."""
    rows = []
    used = 0
    byte_cut = False
    for i, line in enumerate(chunk):
        row = f"{offset + i}: {_clip_read_line(line)}"
        cost = len(row.encode()) + bool(rows)
        if used + cost > max_chars:
            byte_cut = True
            break
        rows.append(row)
        used += cost
    body = "\n".join(rows)
    notes: list[str] = []
    if not chunk:
        notes.append("(End of file — 0 lines.)" if offset <= 1 else f"(No lines at offset {offset}.)")
    else:
        first, last = offset, offset + len(rows) - 1
        if byte_cut:
            if not rows:
                notes.append(f"(Output capped at {max_chars // 1024}KB. Use grep to inspect this long line.)")
            else:
                notes.append(
                    f"(Output capped at {max_chars // 1024}KB. Showing lines {first}-{last}. "
                    f"Use offset={last + 1} to continue.)"
                )
        elif more:
            of = f" of {total}" if total is not None else ""
            notes.append(f"(Showing lines {first}-{last}{of}. Use offset={last + 1} to continue.)")
        else:
            notes.append(f"(End of file — {total if total is not None else last} lines.)")
    return body + ("\n\n" + "\n".join(notes) if notes else "")


def _render_grep_output(out: str) -> str:
    """Older connectors return raw `rg --json` events. Re-render as path:line:text."""
    text = out or ""
    if not text.lstrip().startswith('{"type"'):
        return text
    rows: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict) or event.get("type") != "match":
            continue
        data = event.get("data") if isinstance(event.get("data"), dict) else {}
        path_obj = data.get("path")
        path_text = ""
        if isinstance(path_obj, dict):
            path_text = str(path_obj.get("text") or "")
        elif isinstance(path_obj, str):
            path_text = path_obj
        lines_obj = data.get("lines")
        body = ""
        if isinstance(lines_obj, dict):
            body = str(lines_obj.get("text") or "")
        elif isinstance(lines_obj, str):
            body = lines_obj
        body = body.rstrip("\n")
        line_number = data.get("line_number")
        if line_number:
            rows.append(f"{path_text}:{line_number}:{body}")
        else:
            rows.append(f"{path_text}:{body}")
    return "\n".join(rows) if rows else text


def _search_skip_name(path: str) -> str:
    return posixpath.basename(str(path or "").replace("\\", "/").rstrip("/"))


def _rg_skip_globs(path: str) -> str:
    base = _search_skip_name(path)
    parts: list[str] = []
    for name in _SEARCH_SKIP_DIRS:
        if name == base:
            continue
        parts.append("--glob " + shlex.quote(f"!{name}"))
        parts.append("--glob " + shlex.quote(f"!{name}/**"))
    return " ".join(parts)


def _grep_exclude_dirs(path: str) -> str:
    base = _search_skip_name(path)
    return " ".join(
        "--exclude-dir=" + shlex.quote(name)
        for name in _SEARCH_SKIP_DIRS
        if name != base
    )


def _grep_include_arg(args: dict) -> str:
    raw = args.get("include")
    if raw in (None, ""):
        raw = args.get("glob")
    include = str(raw).strip() if isinstance(raw, (str, int, float)) else ""
    if include and (len(include) > 200 or "\n" in include or "\x00" in include):
        raise WorkspaceError("include exceeds 200 characters or contains a newline; use one glob like *.py")
    return include


def _grep_max_matches(args: dict) -> int:
    if args.get("max_matches") not in (None, ""):
        return _int_arg(args, "max_matches", _GREP_DEFAULT_MATCHES, 1, _GREP_MAX_MATCHES)
    return _GREP_DEFAULT_MATCHES


def _quote_host_path(path: str) -> str:
    for home in ("~", "$HOME"):
        if path == home or path.startswith(home + "/"):
            return '"$HOME"' + shlex.quote(path[len(home):])
    return shlex.quote(path)


def _grep_search_cmd(pattern: str, path: str, include: str = "", max_matches: int = _GREP_DEFAULT_MATCHES) -> str:
    """rg if present, else grep -R. Never `rg || grep`: rg exits 1 on no match
    and that would walk node_modules/venv with no ignore file."""
    n = max(1, min(int(max_matches or _GREP_DEFAULT_MATCHES), _GREP_MAX_MATCHES))
    skip = _rg_skip_globs(path)
    excludes = _grep_exclude_dirs(path)
    glob = (" --glob " + shlex.quote(include)) if include else ""
    grep_include = (" --include=" + shlex.quote(include)) if include else ""
    rg = (
        "rg -n -H --no-heading --color=never --max-filesize 1M "
        f"-m {n} {skip}{glob} -- {shlex.quote(pattern)} {_quote_host_path(path)}"
    )
    fallback = (
        "grep -r -n -H -E --binary-files=without-match "
        f"{excludes}{grep_include} -- {shlex.quote(pattern)} {_quote_host_path(path)}"
    )
    if any(c in include for c in "{}/"):
        fallback = (
            "{ printf '%s\\n' "
            + shlex.quote(
                f"include {include[:80]} needs ripgrep (rg), which is not installed. "
                "Use one suffix glob like *.py, or omit include."
            )
            + " >&2; exit 2; }"
        )
    return (
        "set -o pipefail; "
        f"if command -v rg >/dev/null 2>&1; then {rg} | head -n {n}; "
        f"else {fallback} | head -n {n}; fi"
    )


def _glob_find_fallback(pattern: str, path: str) -> str:
    """Simple filename globs without rg; reject unsupported semantics explicitly."""
    name = pattern.removeprefix("**/")
    if "/" in name or "**" in name or any(c in name for c in "{}"):
        return (
            "{ printf '%s\\n' "
            + shlex.quote(
                f"glob {pattern[:80]} needs ripgrep (rg), which is not installed. "
                "Use one filename glob like *.py."
            )
            + " >&2; exit 2; }"
        )
    prunes = " -o ".join(f"-name {shlex.quote(d)}" for d in _SEARCH_SKIP_DIRS if d != _search_skip_name(path))
    return (
        f"find {_quote_host_path(path)} \\( {prunes} \\) -prune -o -type f "
        f"-name {shlex.quote(name)} -print"
    )


def _glob_search_cmd(pattern: str, path: str) -> str:
    skip = _rg_skip_globs(path)
    rg = f"rg --files --color=never {skip} -g {shlex.quote(pattern)} -- {_quote_host_path(path)}"
    fallback = _glob_find_fallback(pattern, path)
    return (
        "set -o pipefail; "
        f"if command -v rg >/dev/null 2>&1; then {rg} | head -n {_GLOB_MAX_PATHS}; "
        f"else {fallback} | head -n {_GLOB_MAX_PATHS}; fi"
    )


def _filter_grep_include(out: str, include: str) -> str:
    if not include or not out:
        return out
    rows: list[str] = []
    for line in out.splitlines():
        loc = re.split(r":\d+:", line, maxsplit=1)[0].replace("\\", "/")
        name = loc.rsplit("/", 1)[-1]
        if fnmatch.fnmatch(name, include) or fnmatch.fnmatch(loc, include):
            rows.append(line)
    return "\n".join(rows)


def _error_rec(name: str, msg: str, *, host: str = "", kind: str = "", extra: dict | None = None) -> dict:
    rec = {"name": name, "status": "error", "error": msg, "kind": kind or _error_kind(msg)}
    if host:
        rec["host"] = host
    if extra:
        rec.update(extra)
    return rec


def _error_kind(msg: str) -> str:
    from . import tool_failures

    return tool_failures.classify(msg)


_BARE_EXEC_ERRORS = frozenset({"", "exit", "grep failed", "glob failed", "list failed", "exec failed"})


def _first_lines(text: str, n: int = 8, cap: int = 500) -> str:
    lines = [ln for ln in str(text or "").splitlines() if ln.strip()][:n]
    return "\n".join(lines)[:cap]


def _exec_fail_detail(data: dict, fallback: str = "failed") -> str:
    """Never a bare 'exit'. Prefer stderr, then stdout, then a named fallback + code."""
    err = _first_lines(data.get("err") or "")
    out = _first_lines(data.get("out") or "")
    error = (data.get("error") or "").strip()
    if err:
        body = err
    elif out:
        body = out
    elif error.lower() not in _BARE_EXEC_ERRORS and not error.lower().startswith("exit"):
        body = error
    else:
        body = fallback
    try:
        code = int(data.get("exit"))
    except (TypeError, ValueError):
        code = None
    if code is not None and f"exit {code}" not in body.lower():
        body = f"{body} (exit {code})"
    if body.strip().lower() in ("exit", ""):
        body = f"{fallback}" + (f" (exit {code})" if code is not None else "")
    return body


def _agent_error(data: dict, fallback: str) -> WorkspaceError:
    extra: dict = {}
    code = data.get("exit")
    if code is not None and code != "":
        extra["exit_code"] = code
    err = (data.get("err") or "").strip()
    if err:
        extra["stderr"] = err[:500]
    out = (data.get("out") or "").strip()
    if out:
        extra["stdout"] = out[:500]
    return WorkspaceError(_exec_fail_detail(data, fallback), extra=extra)


def _enrich_agent_data(data: dict, *, op: str = "") -> dict:
    """Rewrite the connector's generic exec error 'exit' before any caller sees it."""
    if not isinstance(data, dict) or data.get("ok"):
        return data
    error = (data.get("error") or "").strip()
    if error.lower() not in _BARE_EXEC_ERRORS and not error.lower().startswith("exit"):
        return data
    detail = _exec_fail_detail(data, fallback=(f"{op} failed" if op else "failed"))
    if detail == error:
        return data
    data = dict(data)
    data["error"] = detail
    return data


def _exec_text(data: dict) -> str:
    return " ".join(str(data.get(key) or "") for key in ("err", "out", "error"))


_GENERIC_MUTATION_ERRORS = frozenset({"", "exit", "replace failed", "write failed"}) | _BARE_EXEC_ERRORS


def _is_generic_mutation_error(data: dict) -> bool:
    error = str(data.get("error") or "").strip().lower()
    return error in _GENERIC_MUTATION_ERRORS or error.startswith("exit")


def _ps_literal(text: str) -> str:
    return "'" + str(text).replace("'", "''") + "'"


def _ps_path_expr(path: str) -> str:
    raw = str(path or "").replace("/", "\\")
    if raw.startswith("~\\"):
        return f"Join-Path $env:USERPROFILE {_ps_literal(raw[2:])}"
    return _ps_literal(raw)


def _probe_cmd(path: str, *, windows: bool) -> str:
    """Read-only stat probe: kind, size, writability, parent state. Never mutates."""
    if windows:
        return (
            f"$p = {_ps_path_expr(path)}\n"
            "$d = Split-Path -Parent $p\n"
            "if (Test-Path -LiteralPath $p -PathType Container) { 'dir' } "
            "elseif (Test-Path -LiteralPath $p -PathType Leaf) { (Get-Item -LiteralPath $p).Length } "
            "else { 'missing' }\n"
            "if (-not (Test-Path -LiteralPath $d -PathType Container)) { 'dmiss' } else { 'dw' }\n"
        )
    q = _quote_host_path(path)
    return (
        f"p={q}\n"
        'd=$(dirname "$p")\n'
        'if [ -d "$p" ]; then echo dir; echo na\n'
        'elif [ -f "$p" ]; then wc -c < "$p"\n'
        'if [ -w "$p" ]; then echo w; else echo nw; fi\n'
        "else echo missing; echo na; fi\n"
        'if [ -d "$d" ] && [ -w "$d" ]; then echo dw\n'
        'elif [ -d "$d" ]; then echo dnw\n'
        "else echo dmiss; fi\n"
    )


async def _agent_probe_file(user_id, host_id, path: str, *, windows: bool) -> dict | None:
    """Stat a file on a connector to explain a generic mutation failure.

    Best effort: any transport problem returns None and the original error
    stands. Read-only; never part of a retry.
    """
    try:
        data = await _agent_call(
            user_id,
            host_id,
            "exec",
            {"cwd": "~", "cmd": _probe_cmd(path, windows=windows), "timeout_ms": 8000},
            12,
        )
    except WorkspaceError:
        return None
    if not data.get("ok") and not _pipe_ok_exit(data.get("exit"), _exec_text(data), no_match_ok=False):
        return None
    lines = [ln.strip() for ln in str(data.get("out") or "").splitlines() if ln.strip()]
    if not lines:
        return None
    info: dict = {"kind": "", "size": None, "writable": None, "parent": ""}
    head = lines[0].lower()
    if head in ("dir", "missing"):
        info["kind"] = head
    elif head.isdigit():
        info["kind"] = "file"
        info["size"] = int(head)
        info["writable"] = (lines[1].lower() == "w") if len(lines) > 1 and lines[1].lower() in ("w", "nw") else None
    else:
        return None
    parent = lines[-1].lower() if len(lines) > 1 else ""
    info["parent"] = parent if parent in ("dw", "dnw", "dmiss") else ""
    return info


async def _agent_read_sample(user_id, host_id, path: str) -> str:
    """First raw page via the connector read op: carriage returns intact."""
    try:
        data = await _agent_call(user_id, host_id, "read", {"path": path, "offset": 1, "limit": _READ_MAX_LINES}, 20)
    except WorkspaceError:
        return ""
    if not data.get("ok"):
        return ""
    return str(data.get("out") or "")


async def _refine_mutation_error(data: dict, exc: WorkspaceError, user_id, host_id, path: str, *, windows: bool, write: bool) -> WorkspaceError:
    """Explain a connector's generic write/replace failure with a read-only probe.

    A failed mutation is never retried as a write; this only replaces the
    error text when the probe proves the cause.
    """
    if not _is_generic_mutation_error(data):
        return exc
    info = await _agent_probe_file(user_id, host_id, path, windows=windows)
    if info is None:
        return exc
    kind, size, parent = info.get("kind"), info.get("size"), info.get("parent")
    detail = ""
    if write:
        # writeFileAtomic/replace publish via temp+rename: what matters is the
        # target type and the parent directory, not an existing file's size.
        if kind == "dir":
            detail = "write target is a directory, not a file"
        elif parent == "dmiss":
            detail = "parent directory does not exist; create it first (mkdir -p)"
        elif parent == "dnw":
            detail = "directory is not writable by the connector user"
    else:
        if kind == "dir":
            detail = "target is a directory, not a file"
        elif kind == "missing":
            detail = "file does not exist"
        elif size is not None and size > _REPLACE_FILE_MAX:
            detail = _too_large_edit()
        elif size is not None and size > 400_000:
            # Legacy connectors (pre-1.3.2) cap whole-file edits at 400000
            # bytes and fail generically; current connectors edit up to
            # _REPLACE_FILE_MAX. Auto-update closes this window.
            detail = (
                f"file too large for this connector's edit limit (400000 bytes; its "
                f"update to {_REPLACE_FILE_MAX} is automatic). Split the change or apply it "
                "with run_terminal_command"
            )
        elif parent == "dnw":
            detail = "directory is not writable by the connector user"
    if not detail:
        return exc
    extra = dict(getattr(exc, "extra", None) or {})
    extra["probed"] = True
    return WorkspaceError(detail, extra=extra)


def _pipe_ok_exit(code, detail: str = "", *, no_match_ok: bool = True) -> bool:
    if code is None or code == "":
        return False
    try:
        val = int(code or 0)
    except (TypeError, ValueError):
        return False
    # 0 = matches, 1 = no matches (rg/grep) or a find write error,
    # 141 = SIGPIPE from head closing the pipe. ls exits 1 on real trouble,
    # so _list passes no_match_ok=False to keep its native fallback.
    if val in (0, 141):
        return True
    if val == 1:
        return no_match_ok
    # Connectors exec with SIGPIPE ignored (their socket writes must survive
    # closed pipes), so head instead leaves the producer dying on EPIPE with
    # a "write error: Broken pipe" message and exit 2. Same cause; only
    # tolerate exit 2 when that message is actually present.
    return val == 2 and "broken pipe" in (detail or "").lower()


def _replace_preview(path: str, old: str, new: str, count: int) -> str:
    old_lines = str(old or "").splitlines()
    new_lines = str(new or "").splitlines()
    diff = list(
        difflib.unified_diff(
            old_lines,
            new_lines,
            fromfile="a/" + path,
            tofile="b/" + path,
            lineterm="",
            n=2,
        )
    )
    if not diff:
        return f"{count} replacement(s)"
    body = "\n".join(diff)
    if len(body) > 800:
        body = body[:797] + "…"
    return body


_REPLACE_FILE_MAX = 64 * 1024 * 1024  # whole-file edit ceiling (SSH + connector parity)
_NOT_FOUND_HINT = (
    "old_string not found. It must match the file exactly, including whitespace and "
    "indentation; do not include the 'N:' line-number prefix or the 'From host X:' "
    "label from read_file output."
)


def _too_large_edit() -> str:
    return (
        f"file too large to edit (maximum {_REPLACE_FILE_MAX} bytes). Split the change "
        "or apply it with run_terminal_command instead"
    )


def _crlf_normalize(text: str, old: str, new: str) -> tuple[str, str] | None:
    """LF-only edit strings against a CRLF file.

    read_file strips carriage returns from the lines it shows, so the model
    cannot reproduce a CRLF old_string byte-for-byte. When the file uses CRLF
    and both arguments are CR-free, convert LF to CRLF — deterministic, never
    a fuzzy match.
    """
    if "\r" in old or "\r" in new or "\r\n" not in text:
        return None
    return old.replace("\n", "\r\n"), new.replace("\n", "\r\n")


def _apply_replace(text: str, old: str, new: str, replace_all: bool) -> tuple[str, int]:
    """Exact replacement only; never guess at whitespace-sensitive source edits."""
    if not old:
        raise WorkspaceError("old_string required")
    count = text.count(old)
    if count == 0:
        normalized = _crlf_normalize(text, old, new)
        if normalized is not None:
            old, new = normalized
            count = text.count(old)
    if count == 0:
        hint = " This file uses CRLF line endings; match them or edit single lines." if "\r\n" in text else ""
        raise WorkspaceError(_NOT_FOUND_HINT + hint)
    if count > 1 and not replace_all:
        raise WorkspaceError(
            f"old_string is not unique ({count} occurrences). Include more surrounding "
            "context lines, or pass replace_all=true."
        )
    updated = text.replace(old, new) if replace_all else text.replace(old, new, 1)
    return updated, (count if replace_all else 1)


def _label(alias: str, body: str) -> str:
    text = body if isinstance(body, str) else str(body)
    return f"From host {alias}:\n{text}"


def resolve_host_row(user_id: int, alias: str | None, bound_host_id: int | None):
    if alias:
        key = alias.strip().lower()
        if key in RESERVED:
            raise WorkspaceError("host alias is not allowed")
        row = hosts.host_by_alias(user_id, key)
        if row is None:
            raise WorkspaceError("unknown host alias")
        return row
    if bound_host_id:
        row = hosts.get_host(user_id, bound_host_id)
        if not row:
            raise WorkspaceError("bound host missing")
        if row.get("disabled"):
            raise WorkspaceError("host is disabled")
        return row
    raise WorkspaceError("host is required unless a project is bound")


def _row_get(row, key, default=""):
    try:
        val = row[key]
    except (KeyError, IndexError, TypeError):
        return default
    return default if val is None else val


def _row_id(row) -> int:
    return int(row["id"])


def _row_alias(row) -> str:
    return str(row["alias"])


def _row_default_cwd(row) -> str:
    return str(_row_get(row, "default_cwd") or "")


def host_root(row, bound_host_id: int | None, bound_cwd: str) -> str:
    """Project cwd only applies to the bound host. A named other host uses its own default_cwd."""
    hid = _row_id(row)
    default = _row_default_cwd(row)
    if bound_host_id and int(bound_host_id) == hid and bound_cwd:
        return bound_cwd
    return default


def _is_windows_row(row) -> bool:
    osn = str(_row_get(row, "agent_os") or "").strip().lower()
    return osn.startswith("win")


def _confine(path: str, root: str, *, windows: bool = False) -> str:
    if any(ord(ch) < 32 for ch in path):
        raise WorkspaceError("invalid path: control characters are not allowed")
    if windows:
        return _confine_win(path, root)
    raw = path or "."
    if raw.startswith("~"):
        rest = raw[1:]
        if rest and not rest.startswith("/"):
            raise WorkspaceError("invalid path")
        if ".." in rest.split("/"):
            raise WorkspaceError("path is outside the project root")
        return raw
    base = root or ""
    if not base:
        if ".." in raw.split("/"):
            raise WorkspaceError("path is outside the project root")
        if raw.startswith("/"):
            return posixpath.normpath(raw)
        return posixpath.normpath(raw)
    if raw.startswith("/"):
        full = posixpath.normpath(raw)
    else:
        full = posixpath.normpath(posixpath.join(base, raw))
    root_n = posixpath.normpath(base)
    if root_n != "/" and full != root_n and not full.startswith(root_n.rstrip("/") + "/"):
        raise WorkspaceError("path is outside the project root")
    if ".." in posixpath.normpath(full).split("/"):
        raise WorkspaceError("path is outside the project root")
    return full


def _confine_win(path: str, root: str) -> str:
    raw = (path or ".").replace("/", "\\")
    if raw.startswith("~"):
        rest = raw[1:]
        if rest and not rest.startswith("\\"):
            raise WorkspaceError("invalid path")
        if ".." in [p for p in rest.split("\\") if p]:
            raise WorkspaceError("path is outside the project root")
        return "~" + rest
    base = (root or "").replace("/", "\\")
    if not base:
        if ".." in [p for p in raw.replace("/", "\\").split("\\") if p]:
            raise WorkspaceError("path is outside the project root")
        return ntpath.normpath(raw)
    if re.match(r"^[A-Za-z]:", raw) or raw.startswith("\\\\"):
        full = ntpath.normpath(raw)
    else:
        full = ntpath.normpath(ntpath.join(base, raw))
    root_n = ntpath.normpath(base)
    root_key = root_n.rstrip("\\")
    full_key = ntpath.normpath(full)
    if full_key.lower() != root_key.lower() and not full_key.lower().startswith(root_key.lower() + "\\"):
        raise WorkspaceError("path is outside the project root")
    if ".." in [p for p in full_key.split("\\") if p]:
        raise WorkspaceError("path is outside the project root")
    return full_key


def _confine_glob(pattern: str, root: str, *, windows: bool = False) -> str:
    ok = _GLOB_OK_WIN if windows else _GLOB_OK
    if not pattern:
        raise WorkspaceError("glob pattern is empty")
    if len(pattern) > 200:
        raise WorkspaceError("glob pattern exceeds 200 characters; use a shorter glob like **/*.py")
    if not ok.match(pattern):
        raise WorkspaceError(
            "glob pattern has unsupported characters. Use *, ?, and ** only, e.g. **/*.py; "
            "not a regex and not a comma-separated list."
        )
    if pattern.startswith("/") or pattern.startswith("~") or pattern.startswith(".."):
        raise WorkspaceError("path is outside the project root")
    if windows and (pattern.startswith("\\") or re.match(r"^[A-Za-z]:", pattern)):
        raise WorkspaceError("path is outside the project root")
    if "/.." in pattern or pattern.endswith("/..") or "\\.." in pattern or pattern.endswith("\\.."):
        raise WorkspaceError("path is outside the project root")
    return pattern


def denied(command: str, *, windows: bool = False) -> str | None:
    text = command or ""
    if _DENY.search(text):
        return "command blocked by denylist"
    if re.search(r"(^|[;&|]\s*)(ssh|scp|sftp)\b", text, re.I):
        return "command blocked by denylist"
    if windows and _DENY_WIN.search(text):
        return "command blocked by denylist"
    return None


def _rate_ok(user_id: int, host_id: int) -> None:
    now = time.monotonic()
    key = (int(user_id), int(host_id))
    q = _exec_times.setdefault(key, [])
    q[:] = [t for t in q if now - t < 60]
    if len(q) >= 180:
        raise WorkspaceError("exec rate limit (180/min on this host)")
    q.append(now)


def cancel_run_jobs(run_id: str) -> None:
    """Cancel background shell jobs owned by a chat run (Stop/cancel path)."""
    from . import command_jobs

    command_jobs.cancel_run(run_id)


def _relax_paths(args: dict, base: str, windows: bool) -> None:
    """After an approved out-of-scope call, rewrite relative targets absolute so
    empty-root confinement accepts them (it rejects leftover '..' segments)."""
    for key in ("path", "cwd"):
        raw = args.get(key)
        if not isinstance(raw, str) or not raw:
            continue
        if raw.startswith(("/", "~", "$HOME", "%USERPROFILE%")):
            continue
        if windows:
            raw2 = raw.replace("/", "\\")
            if re.match(r"^[A-Za-z]:", raw2) or raw2.startswith("\\\\"):
                continue
            b = (base or "~").replace("/", "\\")
            if b in ("~", "$HOME", "%USERPROFILE%", ""):
                b = "~"
            args[key] = ntpath.normpath(ntpath.join(b, raw2))
        else:
            b = base if base and base not in ("~", "$HOME") else "~"
            args[key] = posixpath.normpath(posixpath.join(b, raw))


async def execute(name: str, arguments: str, *, user_id: int, bound_host_id=None, bound_cwd="", run_id: str = "", thread_id: int | None = None, on_started=None, gate=None) -> tuple[str, dict]:
    try:
        args = _args(arguments)
    except WorkspaceError as exc:
        return exc.detail, _error_rec(name, exc.detail, kind="invalid_arguments")
    if name == "get_command_output":
        return await _job_output(user_id, args)
    if name == "kill_command":
        return _job_kill(user_id, args)
    mode = hosts.settings_for(user_id).get("permission_mode") or "project"
    if mode == "read_only" and name in ("write_file", "search_replace", "run_terminal_command", "monitor"):
        msg = "permission mode is read_only"
        return msg, _error_rec(name, msg, kind="not_allowed")
    try:
        row = resolve_host_row(user_id, args.get("host") if isinstance(args.get("host"), str) else None, bound_host_id)
    except WorkspaceError as exc:
        host = args.get("host") if isinstance(args.get("host"), str) else ""
        from . import monitor as monitor_mod

        url_only = (
            name == "monitor"
            and monitor_mod.public_ws_url(str(args.get("url") or ""))
            and not str(args.get("command") or "").strip()
            and not host
        )
        if not url_only:
            return exc.detail, _error_rec(name, exc.detail, host=host)
        row = None
    except VaultLocked:
        return "vault locked", _error_rec(name, "vault locked", kind="vault_locked")
    if row is None:
        alias, host_id, root, windows = "", 0, "", False
        context = {"host": "", "cwd": "", "project_root": "", "platform": "posix"}
        if on_started:
            on_started({"name": name, "status": "running", **context})
        try:
            text, rec = await _monitor(user_id, host_id, alias, args, root, windows=windows, run_id=run_id, thread_id=thread_id, on_started=on_started)
        except WorkspaceError as exc:
            extra = dict(getattr(exc, "extra", None) or {})
            return exc.detail, _error_rec(name, exc.detail, host=alias, extra=extra)
        rec.setdefault("host", "")
        rec.setdefault("cwd", "")
        return text, rec
    alias = _row_alias(row)
    host_id = _row_id(row)
    root = host_root(row, bound_host_id, bound_cwd)
    windows = _is_windows_row(row)
    context = {"host": alias, "cwd": root, "project_root": root, "platform": "windows" if windows else "posix"}
    if on_started:
        on_started({"name": name, "status": "running", **context})
    mode = hosts.effective_permission(user_id, bound_host_id=host_id, bound_cwd=root)
    if mode == "read_only" and name in ("write_file", "search_replace", "run_terminal_command", "monitor"):
        detail = "project is read-only"
        return detail, _error_rec(name, detail, host=alias, kind="not_allowed")
    if gate is not None:
        from . import permissions as permissions_mod

        verdict = await permissions_mod.review(
            tool=name, args=args, user_id=user_id, host_id=host_id, host_alias=alias,
            root=root, windows=windows, gate=gate,
        )
        if not verdict.allowed:
            return verdict.detail, _error_rec(name, verdict.detail, host=alias, kind="permission_denied")
        if verdict.root is not None:
            _relax_paths(args, root, windows)
            root = verdict.root
    try:
        if name == "run_terminal_command":
            text, rec = await _shell(user_id, host_id, alias, args, root, windows=windows, run_id=run_id, thread_id=thread_id, on_started=on_started)
        elif name == "monitor":
            text, rec = await _monitor(user_id, host_id, alias, args, root, windows=windows, run_id=run_id, thread_id=thread_id, on_started=on_started)
        elif name == "read_file":
            text, rec = await _read(user_id, host_id, alias, args, root, windows=windows)
        elif name == "list_dir":
            text, rec = await _list(user_id, host_id, alias, args, root, windows=windows)
        elif name == "grep":
            text, rec = await _grep(user_id, host_id, alias, args, root, windows=windows)
        elif name == "glob":
            text, rec = await _glob(user_id, host_id, alias, args, root, windows=windows)
        elif name == "write_file":
            text, rec = await _write(user_id, host_id, alias, args, root, windows=windows)
        elif name == "search_replace":
            text, rec = await _replace(user_id, host_id, alias, args, root, windows=windows)
        else:
            return "Unknown tool.", _error_rec(name, "unknown tool", host=alias, kind="unknown_tool")
    except WorkspaceError as exc:
        extra = dict(getattr(exc, "extra", None) or {})
        return exc.detail, _error_rec(name, exc.detail, host=alias, extra=extra)
    except VaultLocked:
        return "vault locked", _error_rec(name, "vault locked", host=alias, kind="vault_locked")
    except Exception:
        logger.exception("workspace %s failed", name)
        return "Tool failed.", _error_rec(
            name,
            "Tool failed.",
            host=alias,
            kind="internal_exception",
            extra={"exception": traceback.format_exc(limit=30)},
        )
    for key, value in context.items():
        rec.setdefault(key, value)
    if name == "read_file":
        rec.update(offset=_int_arg(args, "offset", 1, 1, 10_000_000), limit=_int_arg(args, "limit", _READ_DEFAULT_LIMIT, 1, _READ_MAX_LINES))
    if rec.get("status") == "error":
        rec.setdefault("kind", _error_kind(str(rec.get("error") or text or "")))
        rec.setdefault("host", alias)
    else:
        rec["host_id"] = int(host_id)
        rec.setdefault("cwd", root)
        rec.setdefault("host", alias)
        if name in ("write_file", "search_replace"):
            invalidate_agents_cache(user_id, host_id, rec.get("path") or "")
    return text, rec


def _agent_cwd(cwd: str) -> str:
    """Connector chdir()s the cwd string as-is. $HOME is not a path."""
    if cwd in ("~", "$HOME", "", "%USERPROFILE%"):
        return "~"
    if cwd.startswith("$HOME/"):
        return "~/" + cwd[6:]
    if cwd.startswith("$HOME\\"):
        return "~\\" + cwd[6:]
    if cwd.startswith("%USERPROFILE%"):
        rest = cwd[13:]
        if rest.startswith("\\") or rest.startswith("/"):
            return "~" + rest.replace("/", "\\")
        return "~"
    return cwd


def _shell_cwd(args: dict, root: str, *, windows: bool = False) -> str:
    raw = str(args.get("cwd") or "")
    if raw in ("$HOME", "%USERPROFILE%"):
        return "~"
    if raw.startswith("~"):
        return raw
    if raw.startswith("$HOME/"):
        return "~/" + raw[6:]
    if raw.startswith("$HOME\\"):
        return "~\\" + raw[6:]
    if raw.startswith("%USERPROFILE%"):
        rest = raw[13:]
        if rest.startswith("\\") or rest.startswith("/"):
            return "~" + rest.replace("/", "\\")
        return "~"
    if raw:
        return _confine(raw, root, windows=windows)
    if root:
        return root
    return "~"


def _shell_prefix(cwd: str) -> str:
    if cwd in ("~", "$HOME"):
        return 'cd "$HOME"'
    if cwd.startswith("~/"):
        rest = shlex.quote(cwd[2:])
        return f'cd "$HOME"/{rest}'
    if cwd.startswith("$HOME/"):
        rest = shlex.quote(cwd[6:])
        return f'cd "$HOME"/{rest}'
    return f"cd {shlex.quote(cwd)}"


def _is_agent(user_id, host_id) -> bool:
    from . import db

    row = db.fetchone("SELECT auth_type FROM user_hosts WHERE id=? AND user_id=?", (host_id, user_id))
    return bool(row) and row["auth_type"] == "agent"


def _agent_output_complete(data: dict) -> bool:
    if not isinstance(data, dict):
        return False
    if "output_complete" in data:
        return data.get("output_complete") is True
    truncated = data.get("truncated")
    return truncated is False or truncated == 0


def _agent_timed_out(data: dict) -> bool:
    if not isinstance(data, dict):
        return False
    if data.get("timeout") is True or data.get("timeout") == 1:
        return True
    error = str(data.get("error") or "").strip().lower()
    return error == "timed out" or error.startswith("timed out")


def _agent_job_output(stdout: str, stderr: str) -> str:
    stdout = stdout or ""
    stderr = stderr or ""
    if stdout and stderr:
        sep = "" if stdout.endswith("\n") else "\n"
        return f"{stdout}{sep}[stderr]\n{stderr}"
    return stdout or stderr


class _AgentExec:
    __slots__ = ("stdout", "stderr", "exit_status")

    def __init__(self, data: dict):
        self.stdout = data.get("out") or ""
        self.stderr = data.get("err") or ""
        code = data.get("exit")
        if data.get("error") and code is None:
            code = 1
        self.exit_status = int(code) if isinstance(code, int) else (0 if data.get("ok") else 1)


async def _agent_call(user_id, host_id, op: str, args: dict, seconds: int = 30) -> dict:
    from . import agent_hub

    try:
        data = await agent_hub.call(user_id, host_id, op, args, timeout=seconds)
    except agent_hub.AgentError as exc:
        raise WorkspaceError(exc.detail) from exc
    return _enrich_agent_data(data, op=op)


async def _run_remote(user_id, host_id, command: str, cwd: str, seconds: int):
    """Short synchronous exec used by internal helpers (browse fallback etc.)."""
    if _is_agent(user_id, host_id):
        _rate_ok(user_id, host_id)
        data = await _agent_call(
            user_id,
            host_id,
            "exec",
            {"cwd": _agent_cwd(cwd), "cmd": command, "timeout_ms": int(seconds * 1000)},
            seconds + 2,
        )
        return _AgentExec(data)
    from . import ssh_pool

    _rate_ok(user_id, host_id)
    conn = await ssh_pool.connection(user_id, host_id)
    wrapped = f"{_shell_prefix(cwd)} && {command}"
    proc = await conn.create_process(wrapped)
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=seconds)
    except asyncio.CancelledError:
        with contextlib.suppress(Exception):
            proc.kill()
        raise
    except TimeoutError:
        with contextlib.suppress(Exception):
            proc.kill()
        raise WorkspaceError("command timed out")
    class _Res:
        pass
    res = _Res()
    out = stdout if isinstance(stdout, str) else (stdout or b"").decode("utf-8", "replace")
    err = stderr if isinstance(stderr, str) else (stderr or b"").decode("utf-8", "replace")
    res.stdout = out[:_SSH_OUT_MAX]
    res.stderr = err[:_SSH_OUT_MAX]
    res.exit_status = proc.exit_status
    res.process = proc
    return res


class _ExecResult:
    """Runner result for command jobs: exit status plus combined output."""

    __slots__ = ("exit_status", "output")

    def __init__(self, exit_status, output: str):
        self.exit_status = exit_status
        self.output = output or ""


def _make_agent_runner(user_id, host_id, command: str, cwd: str, timeout_ms: int):
    from . import command_jobs

    async def runner(sink) -> _ExecResult:
        _rate_ok(user_id, host_id)
        incomplete = getattr(sink, "incomplete", lambda value=True: None)
        incomplete()  # connectors buffer output remotely until their final result
        data = await _agent_call(
            user_id,
            host_id,
            "exec",
            {"cwd": _agent_cwd(cwd), "cmd": command, "timeout_ms": int(timeout_ms)},
            timeout_ms / 1000 + 10,
        )
        res = _AgentExec(data)
        combined = _agent_job_output(res.stdout or "", res.stderr or "")
        sink(combined)
        incomplete(not _agent_output_complete(data))
        if _agent_timed_out(data):
            # Connectors flag deadline kills so a killed command is not
            # misreported as a completed exit-1 run (parity with SSH).
            raise command_jobs.CommandTimedOut(termination_confirmed=data.get("termination_confirmed") is True)
        if not data.get("ok") and not combined and data.get("error") in ("agent is busy", "exec failed", "cancelled"):
            raise WorkspaceError("connector could not execute command: " + data["error"])
        return _ExecResult(res.exit_status, "")

    return runner


def _make_ssh_runner(user_id, host_id, command: str, cwd: str, timeout_ms: int):
    """Streaming SSH exec: incremental sink, deadline kill, continued drain."""

    async def runner(sink) -> _ExecResult:
        from . import command_jobs, ssh_pool

        _rate_ok(user_id, host_id)
        conn = await ssh_pool.connection(user_id, host_id)
        wrapped = f"{_shell_prefix(cwd)} && {command}"
        proc = await conn.create_process("bash -lc " + shlex.quote(wrapped))
        async def drain(reader):
            decoder = codecs.getincrementaldecoder("utf-8")("replace")
            while True:
                chunk = await reader.read(65_536)
                if not chunk:
                    final = decoder.decode(b"", final=True)
                    if final:
                        sink(final)
                    return
                text = chunk if isinstance(chunk, str) else decoder.decode(chunk)
                # Keep draining past the retained-tail cap so a verbose process
                # cannot block on a full pipe; _append_out retains only the
                # rolling tail, so the full chunk is always sunk.
                if text:
                    sink(text)

        readers = [r for r in (proc.stdout, proc.stderr) if r is not None]
        dtasks = [asyncio.create_task(drain(r)) for r in readers]
        try:
            async with asyncio.timeout(timeout_ms / 1000):
                await proc.wait()
                # Exit status can arrive before readers consume buffered EOF.
                await asyncio.gather(*dtasks)
        except asyncio.CancelledError:
            with contextlib.suppress(Exception):
                proc.kill()
            raise
        except TimeoutError:
            with contextlib.suppress(Exception):
                proc.kill()
            raise command_jobs.CommandTimedOut() from None
        finally:
            for dt in dtasks:
                dt.cancel()
            await asyncio.gather(*dtasks, return_exceptions=True)
        code = proc.exit_status
        return _ExecResult(code, "")

    return runner


async def _shell(user_id, host_id, alias, args, root, *, windows: bool = False, run_id: str = "", thread_id: int | None = None, on_started=None):
    from . import command_jobs

    command = str(args.get("command") or "")
    if not command.strip() or len(command) > 8000:
        raise WorkspaceError("invalid command")
    blocked = denied(command, windows=windows)
    if blocked:
        raise WorkspaceError(blocked)
    cwd = _shell_cwd(args, root, windows=windows)
    timeout_ms = _deadline_ms(args)
    background = _as_bool(args.get("background"))
    if _is_agent(user_id, host_id):
        runner = _make_agent_runner(user_id, host_id, command, cwd, timeout_ms)
    else:
        runner = _make_ssh_runner(user_id, host_id, command, cwd, timeout_ms)
    try:
        rec = await command_jobs.launch_async(
            user_id=user_id,
            host_id=host_id,
            host=alias,
            thread_id=thread_id,
            run_id=run_id or None,
            command=command,
            cwd=cwd,
            background=background,
            timeout_ms=timeout_ms,
            runner=runner,
        )
    except ValueError as exc:
        raise WorkspaceError(str(exc)) from exc
    if on_started:
        _, trace = command_jobs.render(rec)
        # The job id is execution identity, never the tool-call event id.
        trace.pop("id", None)
        on_started(trace)
    if background:
        await asyncio.to_thread(command_jobs.mark_notified, rec)
        return command_jobs.render(rec)
    wait_s = min(command_jobs.FOREGROUND_WAIT_S, timeout_ms / 1000)
    try:
        text, trace = await command_jobs.join(rec["id"], user_id, wait_s)
    except asyncio.CancelledError:
        command_jobs.kill(rec["id"], user_id)
        raise
    if trace.get("status") == "running":
        # Hand off: the process keeps running under its execution deadline and
        # its terminal result will be delivered automatically.
        await asyncio.to_thread(command_jobs.mark_notified, rec)
        await asyncio.to_thread(command_jobs.mark_background, rec)
        return command_jobs.render(rec)
    # Completed inside the foreground wait: report it as the shell call itself
    # (join() renders both paths as get_command_output, which the dashboard
    # would display as a joined background job).
    return command_jobs.render(rec)


def _make_ws_runner(url: str, timeout_ms: int):
    async def runner(sink):
        import websockets
        from . import command_jobs

        async with asyncio.timeout(timeout_ms / 1000):
            try:
                async with websockets.connect(url, open_timeout=15, max_size=65_536) as ws:
                    async for message in ws:
                        if isinstance(message, bytes):
                            text = message.decode("utf-8", "replace")
                        else:
                            text = str(message)
                        if text and not text.endswith("\n"):
                            text += "\n"
                        if text:
                            sink(text)
            except asyncio.CancelledError:
                raise
            except TimeoutError:
                raise command_jobs.CommandTimedOut() from None
            except Exception as exc:
                raise WorkspaceError(f"websocket monitor failed: {exc}") from exc
        return _ExecResult(0, "")

    return runner


def _make_monitor_agent_runner(user_id, host_id, command: str, cwd: str, timeout_ms: int):
    """Connector exec_stream: live stdout chunks become events; stderr is retained."""

    async def runner(sink) -> _ExecResult:
        from . import agent_hub, command_jobs

        _rate_ok(user_id, host_id)
        err_sink = getattr(sink, "err", None)
        incomplete = getattr(sink, "incomplete", lambda value=True: None)

        def on_chunk(stream: str, data: str) -> None:
            if not data:
                return
            if stream == "err":
                if err_sink is not None:
                    err_sink(data)
                return
            sink(data)

        try:
            data = await agent_hub.call(
                user_id,
                host_id,
                "exec_stream",
                {"cwd": _agent_cwd(cwd), "cmd": command, "timeout_ms": int(timeout_ms)},
                timeout_ms / 1000 + 10,
                on_chunk=on_chunk,
            )
        except agent_hub.AgentError as exc:
            raise WorkspaceError(exc.detail) from exc
        res = _AgentExec(data)
        leftover_out = res.stdout or ""
        leftover_err = res.stderr or ""
        if leftover_out:
            sink(leftover_out if leftover_out.endswith("\n") else leftover_out + "\n")
        if leftover_err and err_sink is not None:
            err_sink(leftover_err)
        incomplete(not _agent_output_complete(data))
        if _agent_timed_out(data):
            raise command_jobs.CommandTimedOut(termination_confirmed=data.get("termination_confirmed") is True)
        if not data.get("ok") and not (leftover_out or leftover_err) and data.get("error") in ("agent is busy", "exec failed", "cancelled", "unknown op"):
            raise WorkspaceError("connector could not execute command: " + data["error"])
        return _ExecResult(res.exit_status, "")

    return runner


def _make_monitor_ssh_runner(user_id, host_id, command: str, cwd: str, timeout_ms: int):
    """SSH exec that tees stdout to the event sink and stderr to rec.err only."""

    async def runner(sink) -> _ExecResult:
        from . import command_jobs, ssh_pool

        _rate_ok(user_id, host_id)
        conn = await ssh_pool.connection(user_id, host_id)
        wrapped = f"{_shell_prefix(cwd)} && {command}"
        proc = await conn.create_process("bash -lc " + shlex.quote(wrapped))
        err_sink = getattr(sink, "err", None)

        async def drain(reader, dest):
            decoder = codecs.getincrementaldecoder("utf-8")("replace")
            while True:
                chunk = await reader.read(65_536)
                if not chunk:
                    final = decoder.decode(b"", final=True)
                    if final and dest is not None:
                        dest(final)
                    return
                text = chunk if isinstance(chunk, str) else decoder.decode(chunk)
                if text and dest is not None:
                    dest(text)

        tasks = []
        if proc.stdout is not None:
            tasks.append(asyncio.create_task(drain(proc.stdout, sink)))
        if proc.stderr is not None:
            tasks.append(asyncio.create_task(drain(proc.stderr, err_sink)))
        try:
            async with asyncio.timeout(timeout_ms / 1000):
                await proc.wait()
                await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            with contextlib.suppress(Exception):
                proc.kill()
            raise
        except TimeoutError:
            with contextlib.suppress(Exception):
                proc.kill()
            raise command_jobs.CommandTimedOut() from None
        finally:
            for dt in tasks:
                dt.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        return _ExecResult(proc.exit_status, "")

    return runner


async def _monitor(user_id, host_id, alias, args, root, *, windows: bool = False, run_id: str = "", thread_id: int | None = None, on_started=None):
    from . import command_jobs, monitor as monitor_mod

    command = str(args.get("command") or "").strip()
    raw_url = str(args.get("url") or "").strip()
    url = monitor_mod.public_ws_url(raw_url)
    if command and raw_url:
        raise WorkspaceError("pass command or url, not both")
    if not command and not raw_url:
        raise WorkspaceError("command or url is required")
    if raw_url and not url:
        raise WorkspaceError("url must be a public ws:// or wss:// address")
    if command:
        if len(command) > 8000:
            raise WorkspaceError("invalid command")
        blocked = denied(command, windows=windows)
        if blocked:
            raise WorkspaceError(blocked)
    pattern = str(args.get("pattern") or "")
    try:
        monitor_mod.compile_pattern(pattern)
    except ValueError as exc:
        raise WorkspaceError(str(exc)) from exc
    cwd = _shell_cwd(args, root, windows=windows) if command else ""
    timeout_ms = monitor_mod.deadline_ms(args.get("timeout"))
    max_events = monitor_mod.max_events(args.get("max_events"))
    if url:
        runner = _make_ws_runner(url, timeout_ms)
    elif _is_agent(user_id, host_id):
        runner = _make_monitor_agent_runner(user_id, host_id, command, cwd, timeout_ms)
    else:
        runner = _make_monitor_ssh_runner(user_id, host_id, command, cwd, timeout_ms)
    try:
        rec = await command_jobs.launch_async(
            user_id=user_id,
            host_id=host_id,
            host=alias,
            thread_id=thread_id,
            run_id=run_id or None,
            command=command,
            cwd=cwd,
            background=True,
            timeout_ms=timeout_ms,
            runner=runner,
            kind="monitor",
            url=url,
            pattern=pattern,
            max_events=max_events,
        )
    except ValueError as exc:
        raise WorkspaceError(str(exc)) from exc
    if on_started:
        _, trace = command_jobs.render(rec, tool="monitor")
        trace.pop("id", None)
        on_started(trace)
    return command_jobs.render(rec, tool="monitor")


async def _job_output(user_id: int, args: dict) -> tuple[str, dict]:
    from . import command_jobs

    job_id = str(args.get("id") or args.get("child_id") or "")
    timeout_ms = args.get("timeout_ms")
    try:
        wait = min(max(int(timeout_ms), 0), 120_000) / 1000 if timeout_ms is not None else 30
    except (TypeError, ValueError, OverflowError):
        wait = 30
    return await command_jobs.join(job_id, user_id, wait)


def _job_kill(user_id: int, args: dict) -> tuple[str, dict]:
    from . import command_jobs

    job_id = str(args.get("id") or args.get("child_id") or "")
    return command_jobs.kill(job_id, user_id)


def _browse_view(path: str, parent: str, home: str, entries: list) -> dict:
    clean = []
    for item in entries[:400]:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "")
        if not name or name in (".", "..") or "/" in name or "\\" in name:
            continue
        kind = "dir" if item.get("kind") == "dir" else "file"
        clean.append({"name": name[:255], "kind": kind})
    if path != "/" and not parent:
        if "\\" in path and not path.startswith("/"):
            parent = path.rsplit("\\", 1)[0]
            if len(parent) == 2 and parent[1] == ":":
                parent += "\\"
        else:
            parent = path.rsplit("/", 1)[0] or "/"
            if path == "/":
                parent = ""
    return {
        "path": (path or "")[:400],
        "parent": (parent or "")[:400],
        "home": (home or "")[:400],
        "entries": clean,
    }


def _browse_from_ls(path: str, home: str, text: str) -> dict:
    entries = []
    for line in (text or "").splitlines():
        name = line.strip()
        if not name or name in (".", "..", "./", "../"):
            continue
        if name.endswith("/"):
            entries.append({"name": name[:-1], "kind": "dir"})
        else:
            entries.append({"name": name, "kind": "file"})
    parent = ""
    if path and path != "/":
        parent = path.rsplit("/", 1)[0] or "/"
    return _browse_view(path, parent, home, entries)


async def browse(user_id: int, host_id: int, path: str = "") -> dict:
    raw = path if isinstance(path, str) else ""
    if len(raw) > 400 or any(ord(ch) < 32 for ch in raw):
        raise WorkspaceError("invalid path")
    needle = raw.strip() or "~"
    if _is_agent(user_id, host_id):
        data = await _agent_call(user_id, host_id, "browse", {"path": needle}, 20)
        if data.get("ok") and (data.get("out") or "").lstrip().startswith("{"):
            try:
                payload = json.loads(data["out"])
            except json.JSONDecodeError as exc:
                raise WorkspaceError("browse failed") from exc
            if isinstance(payload, dict):
                return _browse_view(
                    str(payload.get("path") or needle),
                    str(payload.get("parent") or ""),
                    str(payload.get("home") or ""),
                    payload.get("entries") if isinstance(payload.get("entries"), list) else [],
                )
        err = (data.get("error") or "").lower()
        if "unknown" not in err and not data.get("ok"):
            raise _agent_error(data, "browse failed")
        row = None
        try:
            from . import db as _db

            row = _db.fetchone("SELECT * FROM user_hosts WHERE id=? AND user_id=?", (host_id, user_id))
        except Exception:
            row = None
        if _is_windows_row(row):
            raise _agent_error(data, "browse failed")
        listing = await _agent_call(
            user_id,
            host_id,
            "exec",
            {"cwd": needle, "cmd": "pwd; ls -1p", "timeout_ms": 15000},
            18,
        )
        lines = (listing.get("out") or "").splitlines()
        cwd = lines[0].strip() if lines else needle
        return _browse_from_ls(cwd, "", "\n".join(lines[1:]))
    from . import ssh_pool

    try:
        conn = await ssh_pool.connection(user_id, host_id)
    except VaultLocked as exc:
        raise WorkspaceError("vault locked") from exc
    quoted = shlex.quote(needle)
    prefix = _shell_prefix(needle)
    result = await conn.run(f"bash -lc {shlex.quote(prefix + ' && pwd && ls -1p')}", check=False, timeout=20)
    out = (result.stdout or "").strip()
    if result.exit_status not in (0, None) or not out:
        raise WorkspaceError("browse failed")
    lines = out.splitlines()
    cwd = lines[0].strip() if lines else needle
    return _browse_from_ls(cwd, "", "\n".join(lines[1:]))


async def _sftp(user_id, host_id):
    from . import ssh_pool

    conn = await ssh_pool.connection(user_id, host_id)
    return await conn.start_sftp_client()


async def _sftp_expand_home(sftp, path: str) -> str:
    for prefix in ("~", "$HOME"):
        if path == prefix or path.startswith(prefix + "/"):
            home = await sftp.realpath(".")
            return str(home).rstrip("/") + path[len(prefix):]
    return path


def _sftp_version(attrs) -> tuple:
    return tuple(getattr(attrs, field, None) for field in
                 ("size", "mtime", "mtime_ns", "permissions"))


async def _sftp_atomic_write(sftp, path: str, content: bytes, *, expected=None):
    """Publish a complete sibling temp file, never unlink the destination first."""
    import asyncssh

    try:
        attrs = await sftp.lstat(path)
    except (asyncssh.SFTPNoSuchFile, asyncssh.SFTPNoSuchPath):
        attrs = None
    if attrs is not None and not stat.S_ISREG(attrs.permissions or 0):
        raise WorkspaceError("write target must be a regular file (not a symlink or directory)")
    mode = (attrs.permissions & 0o777) if attrs is not None else 0o600
    tmp = path + f".fuse-tmp.{uuid4().hex}"
    try:
        async with sftp.open(tmp, "xb", attrs=asyncssh.SFTPAttrs(permissions=mode)) as handle:
            await handle.write(content)
        if expected is not None and _sftp_version(await sftp.lstat(path)) != expected:
            raise WorkspaceError("file changed during edit; read it again before retrying")
        try:
            await sftp.posix_rename(tmp, path)
        except asyncssh.SFTPOpUnsupported:
            if attrs is not None:
                raise WorkspaceError("server does not support atomic replacement; original file preserved")
            await sftp.rename(tmp, path)
    finally:
        with contextlib.suppress(Exception):
            await sftp.remove(tmp)


async def _sftp_read_window(handle, offset: int, limit: int) -> tuple[bytes, bool]:
    """Skip preceding lines in bounded blocks; retain at most one output window.

    The scan ceiling is explicit, rather than misreporting a byte cap as EOF.
    """
    skipped = 0
    scanned = 0
    result = bytearray()
    async with asyncio.timeout(30):
        while scanned < 64 * 1024 * 1024:
            block = await handle.read(_SSH_OUT_MAX)
            if not block:
                return bytes(result), False
            scanned += len(block)
            if skipped < offset - 1:
                needed = offset - 1 - skipped
                count = block.count(b"\n")
                if count < needed:
                    skipped += count
                    continue
                at = 0
                for _ in range(needed):
                    at = block.index(b"\n", at) + 1
                skipped = offset - 1
                block = block[at:]
            room = _SSH_OUT_MAX - len(result)
            result.extend(block[:room])
            if result.count(b"\n") > limit or len(result) >= _SSH_OUT_MAX:
                return bytes(result), True
    raise WorkspaceError("read scan exceeds 64 MiB; narrow the file or use grep")


async def _read(user_id, host_id, alias, args, root, *, windows: bool = False, max_chars: int | None = None, raw: bool = False):
    path = _confine(str(args.get("path") or ""), root, windows=windows)
    offset = _int_arg(args, "offset", 1, 1, 10_000_000)
    limit = _int_arg(args, "limit", _READ_DEFAULT_LIMIT, 1, _READ_MAX_LINES)
    chars = _READ_MAX_CHARS if max_chars is None else max_chars
    if _is_agent(user_id, host_id):
        limit = min(limit, _READ_MAX_LINES)
        data = await _agent_call(
            user_id, host_id, "read", {"path": path, "offset": offset, "limit": limit}, 30
        )
        if not data.get("ok"):
            probe = await _agent_call(user_id, host_id, "list", {"path": path}, 15)
            if probe.get("ok"):
                raise WorkspaceError("path is a directory; use list_dir to list it")
            raise _agent_error(data, "read failed")
        source = data.get("out") or ""
        if data.get("truncated") or source.endswith("\n[fuse-agent: output truncated]\n"):
            raise WorkspaceError("connector read output was truncated; use a smaller limit or grep")
        if raw:
            body = source[:chars]
            return _label(alias, body), {
                "name": "read_file",
                "status": "done",
                "host": alias,
                "path": path,
                "preview": body[:800],
            }
        chunk = source.split("\n")
        if chunk and chunk[-1] == "":
            chunk.pop()
        chunk = [line.removesuffix("\r") for line in chunk]
        transport_clipped = len(source.encode()) >= _SSH_OUT_MAX - 4
        if transport_clipped:
            raise WorkspaceError("connector read output reached its byte limit; use a smaller limit or grep")
        body = _read_render(chunk, offset, total=None, more=len(chunk) >= limit, max_chars=chars)
        return _label(alias, body), {
            "name": "read_file",
            "status": "done",
            "host": alias,
            "path": path,
            "preview": "\n".join(chunk[:24])[:800],
        }
    sftp = await _sftp(user_id, host_id)
    async with sftp:
        try:
            path = await _sftp_expand_home(sftp, path)
            async with sftp.open(path, "rb") as handle:
                data, bytes_truncated = await _sftp_read_window(handle, offset, limit)
        except WorkspaceError:
            raise
        except Exception:
            try:
                await sftp.listdir(path)
            except Exception:
                raise WorkspaceError("read failed")
            raise WorkspaceError("path is a directory; use list_dir to list it")
    if isinstance(data, bytes):
        try:
            text = data.decode()
        except UnicodeDecodeError:
            text = data.decode("utf-8", "replace")
    else:
        text = str(data)
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    lines = [line.removesuffix("\r") for line in lines]
    if not lines and offset > 1:
        raise WorkspaceError(f"offset {offset} is beyond the end of the file")
    if bytes_truncated and len(lines) <= limit:
        if len(lines) == 1:
            raise WorkspaceError("line exceeds the read byte limit; use grep")
        lines.pop()  # never present a byte-clipped final line as complete
    chunk = lines[:limit]
    total = offset - 1 + len(lines) if not bytes_truncated else None
    more = len(chunk) < len(lines) or bytes_truncated
    if raw:
        body = "\n".join(chunk)[:chars]
        return _label(alias, body), {
            "name": "read_file",
            "status": "done",
            "host": alias,
            "path": path,
            "preview": body[:800],
        }
    body = _read_render(chunk, offset, total=total, more=more, max_chars=chars)
    return _label(alias, body), {
        "name": "read_file",
        "status": "done",
        "host": alias,
        "path": path,
        "preview": "\n".join(chunk[:24])[:800],
    }


async def _list(user_id, host_id, alias, args, root, *, windows: bool = False):
    path = _confine(str(args.get("path") or "."), root, windows=windows)
    names: list[str] = []
    if _is_agent(user_id, host_id):
        out = ""
        got = False
        if not windows:
            # -p marks dirs; -f prints as readdir so head can SIGPIPE instead of
            # waiting for GNU ls to sort a huge directory. Cap matches the native op.
            try:
                data = await _agent_call(
                    user_id,
                    host_id,
                    "exec",
                    {
                        "cwd": _agent_cwd(path),
                        "cmd": f"set -o pipefail; ls -1ApU -- | head -n {_LIST_MAX_ENTRIES}",
                        "timeout_ms": 8000,
                    },
                    10,
                )
                if data.get("ok") or _pipe_ok_exit(
                    data.get("exit"), _exec_text(data), no_match_ok=False
                ):
                    out = data.get("out") or ""
                    got = True
            except WorkspaceError as exc:
                if "offline" in exc.detail:
                    raise
        if not got:
            data = await _agent_call(user_id, host_id, "list", {"path": path}, 10)
            if not data.get("ok"):
                raise _agent_error(data, "list failed")
            out = data.get("out") or ""
        if data.get("truncated"):
            raise WorkspaceError("directory output exceeded connector limit; narrow the path or use glob")
        names = [
            ln
            for ln in out.splitlines()
            if ln.strip() and ln.strip().rstrip("/") not in (".", "..")
        ]
    else:
        sftp = await _sftp(user_id, host_id)
        async with sftp:
            try:
                path = await _sftp_expand_home(sftp, path)
                async for entry in sftp.scandir(path):
                    name = str(entry.filename or "")
                    if not name or name in (".", ".."):
                        continue
                    try:
                        perms = int(entry.attrs.permissions or 0)
                    except Exception:
                        perms = 0
                    if stat.S_ISDIR(perms):
                        name += "/"
                    names.append(name)
                    if len(names) >= _LIST_MAX_ENTRIES:
                        break
            except Exception as exc:
                raise WorkspaceError(f"list failed: {exc}") from exc
    names.sort()
    total = len(names)
    shown = names[:_LIST_MAX_ENTRIES]
    listing = "\n".join(shown) if shown else "(no entries)"
    if total >= _LIST_MAX_ENTRIES:
        listing += f"\n(showing {_LIST_MAX_ENTRIES} entries; directory may contain more)"
    return _label(alias, listing), {"name": "list_dir", "status": "done", "host": alias, "path": path, "preview": listing[:800]}


async def _grep(user_id, host_id, alias, args, root, *, windows: bool = False):
    pattern = str(args.get("pattern") or "")
    if not pattern:
        raise WorkspaceError("invalid pattern")
    if len(pattern) > _GREP_MAX_PATTERN:
        raise WorkspaceError(f"pattern exceeds {_GREP_MAX_PATTERN} characters; split the search")
    include = _grep_include_arg(args)
    max_matches = _grep_max_matches(args)
    path = _confine(str(args.get("path") or "."), root, windows=windows)
    cmd = _grep_search_cmd(pattern, path, include=include, max_matches=max_matches)
    if _is_agent(user_id, host_id):
        if windows:
            # Windows connector grep is native WalkDir; bash `command -v rg` is not.
            data = await _agent_call(
                user_id, host_id, "grep", {"path": path, "pattern": pattern, "include": include, "max_matches": max_matches}, _GREP_TIMEOUT + 2
            )
            if not data.get("ok"):
                raise _agent_error(data, "grep failed")
            out = _render_grep_output(data.get("out") or "")
            # The native connector already applies path-aware ** semantics.
            if max_matches:
                out = "\n".join(out.splitlines()[:max_matches])
        else:
            data = await _agent_call(
                user_id,
                host_id,
                "exec",
                {"cwd": ".", "cmd": cmd, "timeout_ms": _GREP_TIMEOUT * 1000},
                _GREP_TIMEOUT + 2,
            )
            if not data.get("ok") and not _pipe_ok_exit(data.get("exit"), _exec_text(data)):
                raise _agent_error(data, "grep failed")
            out = data.get("out") or ""
    else:
        from . import ssh_pool

        conn = await ssh_pool.connection(user_id, host_id)
        result = await conn.run("bash -lc " + shlex.quote(cmd), check=False, timeout=_GREP_TIMEOUT)
        out = result.stdout or ""
        if not _pipe_ok_exit(result.exit_status, f"{result.stderr or ''}\n{out}"):
            raise WorkspaceError(
                _exec_fail_detail(
                    {
                        "exit": result.exit_status,
                        "err": result.stderr or "",
                        "out": out,
                        "error": "exit",
                    },
                    "grep failed",
                ),
                extra={"exit_code": result.exit_status, "stderr": (result.stderr or "")[:500]},
            )
    lines = [ln for ln in out.splitlines() if ln.strip()]
    clipped = len(out) > 6000
    out = out[:6000]
    notes: list[str] = []
    if clipped:
        notes.append("(output clipped; narrow the pattern or path)")
    elif len(lines) >= max_matches:
        notes.append(
            f"(showing {max_matches} matches; pass max_matches up to {_GREP_MAX_MATCHES} or narrow path/include)"
        )
    if notes:
        out = (out.rstrip() + "\n" + "\n".join(notes)) if out.strip() else "\n".join(notes)
    preview = "\n".join(out.splitlines()[:20])[:800]
    return _label(alias, out or "(no matches)"), {
        "name": "grep",
        "status": "done",
        "host": alias,
        "path": path,
        "pattern": pattern[:200],
        "preview": preview,
    }


async def _glob(user_id, host_id, alias, args, root, *, windows: bool = False):
    pattern = _confine_glob(str(args.get("pattern") or ""), root, windows=windows)
    path = _confine(str(args.get("path") or "."), root, windows=windows)
    cmd = _glob_search_cmd(pattern, path)
    if _is_agent(user_id, host_id):
        if windows:
            data = await _agent_call(user_id, host_id, "glob", {"path": path, "pattern": pattern}, 14)
            if not data.get("ok"):
                raise _agent_error(data, "glob failed")
            out = data.get("out") or ""
        else:
            data = await _agent_call(
                user_id,
                host_id,
                "exec",
                {"cwd": ".", "cmd": cmd, "timeout_ms": 12000},
                14,
            )
            if not data.get("ok") and not _pipe_ok_exit(data.get("exit"), _exec_text(data)):
                raise _agent_error(data, "glob failed")
            out = data.get("out") or ""
        out = _glob_result(out)
        return _label(alias, out or "(no matches)"), {
            "name": "glob",
            "status": "done",
            "host": alias,
            "path": path,
            "pattern": pattern[:200],
            "preview": out[:800],
        }
    from . import ssh_pool

    conn = await ssh_pool.connection(user_id, host_id)
    result = await conn.run("bash -lc " + shlex.quote(cmd), check=False, timeout=12)
    out = result.stdout or ""
    if not _pipe_ok_exit(result.exit_status, f"{result.stderr or ''}\n{out}"):
        raise WorkspaceError(
            _exec_fail_detail(
                {
                    "exit": result.exit_status,
                    "err": result.stderr or "",
                    "out": out,
                    "error": "exit",
                },
                "glob failed",
            ),
            extra={"exit_code": result.exit_status, "stderr": (result.stderr or "")[:500]},
        )
    out = _glob_result(out)
    return _label(alias, out or "(no matches)"), {
        "name": "glob",
        "status": "done",
        "host": alias,
        "path": path,
        "pattern": pattern[:200],
        "preview": out[:800],
    }


def _glob_result(out: str) -> str:
    text = (out or "")[:6000]
    n = len([ln for ln in text.splitlines() if ln.strip()])
    if len(out or "") > 6000:
        text = text.rsplit("\n", 1)[0] if "\n" in text else text
        text += "\n(output clipped; narrow the pattern or path)"
    elif n >= _GLOB_MAX_PATHS:
        text = text.rstrip() + f"\n(showing {_GLOB_MAX_PATHS} paths; narrow the pattern or path)"
    return text


_mutation_locks: weakref.WeakValueDictionary = weakref.WeakValueDictionary()


def _serialize_file_mutation(fn):
    @functools.wraps(fn)
    async def wrapped(user_id, host_id, alias, args, root, *, windows=False):
        path = _confine(str(args.get("path") or ""), root, windows=windows)
        key = (user_id, host_id, ntpath.normcase(path) if windows else path)
        lock = _mutation_locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            _mutation_locks[key] = lock
        async with lock:
            return await fn(user_id, host_id, alias, args, root, windows=windows)
    return wrapped


@_serialize_file_mutation
async def _write(user_id, host_id, alias, args, root, *, windows: bool = False):
    _require(args, "path", "content")
    if not isinstance(args["path"], str) or not args["path"]:
        raise WorkspaceError("path must be a nonempty string")
    path = _confine(str(args.get("path") or ""), root, windows=windows)
    if not isinstance(args.get("content"), str):
        raise WorkspaceError("content must be a string")
    content = args.get("content") or ""
    if "\x00" in content:
        raise WorkspaceError("content must be text without NUL bytes")
    if len(content.encode("utf-8")) > 400_000:
        raise WorkspaceError("content exceeds 400000 UTF-8 bytes; split the write")
    if _is_agent(user_id, host_id):
        try:
            data = await _agent_call(user_id, host_id, "write", {"path": path, "content": content}, 45)
        except WorkspaceError as exc:
            if exc.detail == "payload too large":
                raise WorkspaceError(
                    "content exceeds the connector transport limit (900000 bytes); "
                    "split the write or apply it with run_terminal_command"
                ) from exc
            raise
        if not data.get("ok"):
            base = _agent_error(data, "write failed")
            raise await _refine_mutation_error(data, base, user_id, host_id, path, windows=windows, write=True)
        return _label(alias, "wrote " + path), {"name": "write_file", "status": "done", "host": alias, "path": path}
    sftp = await _sftp(user_id, host_id)
    async with sftp:
        path = await _sftp_expand_home(sftp, path)
        await _sftp_atomic_write(sftp, path, content.encode())
    return _label(alias, "wrote " + path), {"name": "write_file", "status": "done", "host": alias, "path": path}


@_serialize_file_mutation
async def _replace(user_id, host_id, alias, args, root, *, windows: bool = False):
    _require(args, "path", "old_string", "new_string")
    if not isinstance(args["path"], str) or not args["path"]:
        raise WorkspaceError("path must be a nonempty string")
    path = _confine(str(args.get("path") or ""), root, windows=windows)
    if not isinstance(args.get("old_string"), str) or not isinstance(args.get("new_string"), str):
        raise WorkspaceError("old_string and new_string must be strings")
    old = args.get("old_string") or ""
    new = args.get("new_string") or ""
    if not old:
        raise WorkspaceError("old_string required")
    if "\x00" in old or "\x00" in new:
        raise WorkspaceError("replacement strings must not contain NUL bytes")
    if len(old.encode("utf-8")) > 200_000 or len(new.encode("utf-8")) > 200_000:
        raise WorkspaceError("old_string or new_string exceeds 200000 UTF-8 bytes; edit a smaller span")
    replace_all = _as_bool(args.get("replace_all"))
    if _is_agent(user_id, host_id):
        try:
            data = await _agent_call(
                user_id,
                host_id,
                "replace",
                {"path": path, "old": old, "new": new, "replace_all": replace_all},
                45,
            )
        except WorkspaceError as exc:
            if exc.detail == "payload too large":
                raise WorkspaceError(
                    "old_string/new_string exceeds the connector transport limit (900000 bytes); "
                    "use a smaller old_string with less context or apply the change "
                    "with run_terminal_command"
                ) from exc
            raise
        error = str(data.get("error") or "").strip().lower()
        if (
            not data.get("ok")
            and "not found" in error
            and "\r" not in old
            and "\n" in old
        ):
            # Legacy connectors match bytes exactly while read_file strips \r,
            # so a CRLF file can never match an LF-only old_string built from
            # read output. Confirm the file's line endings from a raw page and
            # retry the same atomic replace once, CRs restored.
            sample = await _agent_read_sample(user_id, host_id, path)
            if "\r\n" in sample:
                data = await _agent_call(
                    user_id,
                    host_id,
                    "replace",
                    {
                        "path": path,
                        "old": old.replace("\n", "\r\n"),
                        "new": new.replace("\n", "\r\n"),
                        "replace_all": replace_all,
                    },
                    45,
                )
        if data.get("ok"):
            count = 1
            out = str(data.get("out") or "")
            match = re.search(r"(\d+)\s+replacement", out)
            if match:
                count = int(match.group(1))
            preview = _replace_preview(path, old, new, count)
            return _label(alias, f"updated {path}"), {
                "name": "search_replace",
                "status": "done",
                "host": alias,
                "path": path,
                "preview": preview,
            }
        # A failed/uncertain mutation must never be retried as a whole-file write.
        base = _agent_error(data, "replace failed")
        if "not found" in str(data.get("error") or "").strip().lower() and "\r" not in old:
            base = WorkspaceError(
                _NOT_FOUND_HINT + " This file may use CRLF line endings that read_file hides.",
                extra=dict(getattr(base, "extra", None) or {}),
            )
        raise await _refine_mutation_error(data, base, user_id, host_id, path, windows=windows, write=False)
    sftp = await _sftp(user_id, host_id)
    async with sftp:
        path = await _sftp_expand_home(sftp, path)
        attrs = await sftp.lstat(path)
        expected = _sftp_version(attrs)
        if getattr(attrs, "size", 0) and attrs.size > _REPLACE_FILE_MAX:
            raise WorkspaceError(_too_large_edit())
        async with sftp.open(path, "rb") as handle:
            data = await handle.read(_REPLACE_FILE_MAX + 1)
        if len(data) > _REPLACE_FILE_MAX:
            raise WorkspaceError(_too_large_edit())
        try:
            text = data.decode() if isinstance(data, bytes) else str(data)
        except UnicodeDecodeError:
            raise WorkspaceError("file is not UTF-8 text; apply the change with run_terminal_command") from None
        updated, count = _apply_replace(text, old, new, replace_all)
        if len(updated.encode()) > _REPLACE_FILE_MAX:
            raise WorkspaceError(
                f"replacement too large (maximum {_REPLACE_FILE_MAX} bytes); apply the change "
                "with run_terminal_command instead"
            )
        await _sftp_atomic_write(sftp, path, updated.encode(), expected=expected)
    return _label(alias, f"updated {path}"), {
        "name": "search_replace",
        "status": "done",
        "host": alias,
        "path": path,
        "preview": _replace_preview(path, old, new, count),
    }


def listing_names(listing: str, rec: dict | None = None) -> set[str]:
    names: set[str] = set()
    preview = (rec or {}).get("preview") or ""
    for blob in (listing or "", preview):
        for line in str(blob).splitlines():
            item = line.strip().rstrip("/")
            if not item or item.lower().startswith("from host "):
                continue
            names.add(posixpath.basename(item))
    return names


def pick_agents_name(names) -> str:
    found = {str(item) for item in (names or []) if item}
    lower = {item.lower(): item for item in found}
    for candidate in AGENTS_FILENAMES:
        if candidate in found:
            return candidate
        hit = lower.get(candidate.lower())
        if hit:
            return hit
    return ""


def unlabel_host(alias: str, text: str) -> str:
    body = text if isinstance(text, str) else str(text or "")
    prefix = f"From host {alias}:\n"
    if body.startswith(prefix):
        return body[len(prefix):]
    return body


def invalidate_agents_cache(user_id, host_id, path: str = "") -> None:
    base = posixpath.basename(path or "").lower()
    if path and base not in _AGENTS_BASENAMES:
        return
    uid, hid = int(user_id), int(host_id)
    for key in [item for item in _agents_cache if item[0] == uid and item[1] == hid]:
        _agents_cache.pop(key, None)


def _agents_cache_put(key, expires, name, body, reason) -> None:
    if len(_agents_cache) > 256:
        _agents_cache.clear()
    _agents_cache[key] = (expires, name, body, reason)


async def load_project_agents(user_id, bound_host_id, bound_cwd: str = "", report: dict | None = None) -> tuple[str, str]:
    """Read AGENTS.md (or agent.md / CLAUDE.md) from a bound project root. Never raises.

    report, when given, receives a UI-facing reason: loaded / missing / locked / host.
    """
    if not bound_host_id:
        return "", ""
    key = (int(user_id), int(bound_host_id), posixpath.normpath(bound_cwd or ""))
    now = time.monotonic()
    hit = _agents_cache.get(key)
    if hit and hit[0] > now:
        if report is not None:
            report["reason"] = hit[3]
        return str(hit[1] or ""), str(hit[2] or "")
    try:
        row = resolve_host_row(user_id, None, bound_host_id)
    except (WorkspaceError, VaultLocked) as exc:
        reason = "locked" if isinstance(exc, VaultLocked) or "vault" in str(exc).lower() else "host"
        _agents_cache_put(key, now + 15, "", "", reason)
        if report is not None:
            report["reason"] = reason
        return "", ""
    alias = _row_alias(row)
    host_id = _row_id(row)
    root = host_root(row, bound_host_id, bound_cwd)
    windows = _is_windows_row(row)
    try:
        listing, rec = await _list(user_id, host_id, alias, {"path": "."}, root, windows=windows)
    except VaultLocked:
        _agents_cache_put(key, now + 15, "", "", "locked")
        if report is not None:
            report["reason"] = "locked"
        return "", ""
    except (WorkspaceError, Exception) as exc:
        reason = "locked" if "vault" in str(exc).lower() else "host"
        _agents_cache_put(key, now + 15, "", "", reason)
        if report is not None:
            report["reason"] = reason
        return "", ""
    chosen = pick_agents_name(listing_names(listing, rec if isinstance(rec, dict) else None))
    if not chosen:
        _agents_cache_put(key, now + 45, "", "", "missing")
        if report is not None:
            report["reason"] = "missing"
        return "", ""
    body = ""
    offset = 1
    try:
        # Read one extra character to detect truncation.
        while len(body) <= AGENTS_MAX:
            labeled, rec = await _read(
                user_id, host_id, alias, {"path": chosen, "offset": offset, "limit": _READ_MAX_LINES}, root,
                windows=windows, max_chars=AGENTS_MAX + 1, raw=True,
            )
            if isinstance(rec, dict) and rec.get("status") == "error":
                raise WorkspaceError(rec.get("error") or "read failed")
            chunk = unlabel_host(alias, labeled)
            if body and chunk and not body.endswith("\n"):
                body += "\n"
            body += chunk
            if len(chunk.splitlines()) < _READ_MAX_LINES:
                break
            offset += _READ_MAX_LINES
    except Exception as exc:
        reason = "locked" if isinstance(exc, VaultLocked) or "vault" in str(exc).lower() else "host"
        _agents_cache_put(key, now + 15, "", "", reason)
        if report is not None:
            report["reason"] = reason
        return "", ""
    if len(body) > AGENTS_MAX:
        marker = "\n[Project instructions truncated]"
        body = body[:AGENTS_MAX - len(marker)] + marker
    else:
        body = body.strip()
    if not body:
        _agents_cache_put(key, now + 45, "", "", "missing")
        if report is not None:
            report["reason"] = "missing"
        return "", ""
    _agents_cache_put(key, now + 90, chosen, body, "loaded")
    if report is not None:
        report["reason"] = "loaded"
    return chosen, body
