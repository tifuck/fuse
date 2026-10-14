"""Workspace tools on user SSH hosts. Fuse-local is denied unless all three flags (later PR)."""
from __future__ import annotations

import asyncio
import base64
import binascii
import codecs
import contextlib
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

# workspace_ops: responsibility modules extracted from this file. These
# imports are also the stable surface callers and tests patch on app.workspace.
from .workspace_ops.common import (
    WorkspaceError,
    _agent_error,
    _args,
    _as_bool,
    _enrich_agent_data,
    _exec_fail_detail,
    _exec_text,
    _int_arg,
    _is_generic_mutation_error,
    _pipe_ok_exit,
    _quote_host_path,
    _require,
)
from .workspace_ops.instructions import (
    AGENTS_MAX,
    _agents_cache,
    _agents_cache_put,
    _agents_fail,
    _agents_last_good,
    invalidate_agents_cache,
    last_project_agents,
    listing_names,
    pick_agents_name,
    unlabel_host,
)
from .workspace_ops.previews import (
    _NOT_FOUND_HINT,
    _READ_MAX_LINE,
    _apply_replace,
    _closest_match,
    _edit_first_line,
    _edit_snippet,
    _multi_diff,
    _parse_edits,
    _post_edit_snippet,
    _read_render,
    _replace_preview,
    _whole_lines,
)
from .workspace_ops.search_cmds import (
    _GREP_DEFAULT_MATCHES,
    _GREP_MAX_CONTEXT,
    _GREP_MAX_MATCHES,
    _GREP_MAX_PATTERN,
    _GREP_TIMEOUT,
    _LIST_SCAN_MAX,
    _SEARCH_SKIP_DIRS,
    _glob_search_cmd,
    _glob_to_ere,
    _grep_include_arg,
    _grep_max_matches,
    _grep_search_cmd,
    _scope_glob,
    _search_skip_name,
    _tree_cmd_posix,
)
from .workspace_ops.search_render import (
    _SEARCH_OUT_CHARS,
    _clip_note,
    _filter_grep_include,
    _format_search_lines,
    _glob_order_arg,
    _glob_result,
    _grep_mode_arg,
    _pack_search_rows,
    _render_grep_output,
    _reroot_lines,
    _search_status,
    _search_target,
    _strip_connector_clip,
)
from .workspace_ops.transport import (
    _AgentExec,
    _ExecResult,
    _PGID_DIR,
    _SSH_BEGIN,
    _agent_cwd,
    _agent_job_output,
    _agent_output_complete,
    _agent_timed_out,
    _as_text,
    _is_sleep_command,
    _probe_cmd,
    _ps_path_expr,
    _shell_prefix,
    _ssh_job_script,
    _ssh_kill_script,
    _ssh_tool_script,
    _strip_begin,
)

logger = logging.getLogger(__name__)

# A command starts at the beginning of a line, after a separator (; & | or a
# newline), or inside a subshell/substitution. The denylist targets the named
# catastrophic forms only; it is not a sandbox (the permission gate is).
# With a gate these forms always raise the ask card (never Jev, never a grant)
# and run on the user's own Allow or on bypass; gateless callers are blocked.
_CMD_START = r"(?:^|[;&|(`{]\s*|\$\(\s*|\b(?:then|do|else|exec|command|builtin|xargs|env|nohup|time|nice|ionice|eval|setsid|busybox)\s+['\"]?|\b(?:(?:ba|z|k|c|da|fi|tc|a)?sh(?:\.exe)?|pwsh|powershell(?:\.exe)?)\s+-c\s+['\"]?)"
_DENY = re.compile(
    _CMD_START
    + r"(?:"
    # rm of the filesystem root itself: rm -rf /, rm -rf /*, --no-preserve-root
    r"rm\s+(?:-{1,2}[\w-]+\s*)*(?:/\*?|--no-preserve-root\b)(?=\s|$|[;&|)])"
    r"|mkfs(?:\.\w+)?\b"
    r"|(?:shutdown|reboot|poweroff|halt)\b"
    r"|sudo\b"
    # recursive world-writable on the root
    r"|chmod\s+(?:-\w+\s+)*-R\s+(?:-\w+\s+)*0?777\s+/(?=\s|$|[;&|)])"
    r"|chmod\s+(?:-\w+\s+)*0?777\s+(?:-\w+\s+)*-R\s+/(?=\s|$|[;&|)])"
    r")",
    re.I | re.M,
)
_DENY_REMOTE = re.compile(_CMD_START + r"(?:ssh|scp|sftp)(?![\w-])", re.I | re.M)
RESERVED = frozenset({"local", "localhost", "127.0.0.1", ""})
# Glob patterns are shell-quoted, so the only characters refused are those
# that signal a shell pipeline or a regex/comma list rather than a glob.
# Real names such as +page.svelte, @scope, "My File.md" are allowed.
_GLOB_BAD = re.compile(r"[|;<>`$\"\\]")
_GLOB_BAD_WIN = re.compile(r"[|;<>`$\"]")
_DENY_WIN = re.compile(
    r"(?:Stop-Computer|Restart-Computer|(?:^|[;&|(]\s*)shutdown(?:\.exe)?\b|Format-Volume|Clear-Disk)",
    re.I | re.M,
)
_exec_times: dict[tuple[int, int], list[float]] = {}
_SSH_OUT_MAX = 65_536
# OpenCode read tool: 2000 lines, 50KB, lines longer than 2000 chars truncated in place.
_READ_DEFAULT_LIMIT = 2000
_READ_MAX_LINES = 2000
_READ_MAX_BYTES = 50 * 1024
_READ_MAX_CHARS = _READ_MAX_BYTES  # rendered bytes, including "N: " prefixes
_LIST_MAX_ENTRIES = 400
# Vendor/build trees rg would still walk when there is no .gitignore, and that
# grep -R always walks. Same names the Windows connector skips in WalkDir.


def _parse_timeout_ms(raw, default: int) -> int:
    """Milliseconds. Values under 1000 are legacy seconds; blank or junk is default."""
    if raw is None or raw == "":
        return default
    try:
        val = int(float(raw))
    except (TypeError, ValueError, OverflowError):
        return default
    if val <= 0:
        return default
    if val < 1000:
        val = val * 1000
    return val


def _command_timeouts(args: dict, background: bool) -> tuple[int, int]:
    """(kill_after_ms, foreground_wait_ms) for one shell call.

    Foreground waits its own timeout (default 2 min, ceiling 10) and then moves
    to the background, which gets 30 minutes from the move. A command started
    in the background gets 30 minutes, or the requested timeout up to 2 hours,
    and no foreground wait.
    """
    from .command_jobs import (
        BACKGROUND_DEFAULT_MS, BACKGROUND_MAX_MS, FOREGROUND_DEFAULT_MS, FOREGROUND_MAX_MS,
    )

    raw = args.get("timeout")
    if background:
        requested = _parse_timeout_ms(raw, BACKGROUND_DEFAULT_MS)
        return max(1_000, min(requested, BACKGROUND_MAX_MS)), 0
    requested = _parse_timeout_ms(raw, FOREGROUND_DEFAULT_MS)
    wait = max(1_000, min(requested, FOREGROUND_MAX_MS))
    return wait + BACKGROUND_DEFAULT_MS, wait


# Runs grep over one xargs batch under the shared deadline. No-match (1) and
# a closed pipe (SIGPIPE) are not failures; a spent deadline stops xargs
# (255 -> xargs exit 124, which reads as a timeout).


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


_REPLACE_FILE_MAX = 64 * 1024 * 1024  # whole-file edit ceiling (SSH + connector parity)


def _too_large_edit() -> str:
    return (
        f"file too large to edit (maximum {_REPLACE_FILE_MAX} bytes). Split the change "
        "or apply it with run_terminal_command instead"
    )


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
        raise WorkspaceError(f"{full} is outside the project root {root_n}; use a path inside it")
    if ".." in posixpath.normpath(full).split("/"):
        raise WorkspaceError("path is outside the project root")
    return full


def _confine_win(path: str, root: str) -> str:
    from .permissions import expand_temp

    raw = expand_temp(path or ".", True).replace("/", "\\")
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
        raise WorkspaceError(f"{full_key} is outside the project root {root_n}; use a path inside it")
    if ".." in [p for p in full_key.split("\\") if p]:
        raise WorkspaceError("path is outside the project root")
    return full_key


def _confine_glob(pattern: str, root: str, *, windows: bool = False) -> str:
    bad = _GLOB_BAD_WIN if windows else _GLOB_BAD
    if not pattern:
        raise WorkspaceError("glob pattern is empty")
    if len(pattern) > 200:
        raise WorkspaceError("glob pattern exceeds 200 characters; use a shorter glob like **/*.py")
    if any(ord(ch) < 32 for ch in pattern) or bad.search(pattern) or re.search(r",\s", pattern.split("{")[0]):
        raise WorkspaceError(
            "glob pattern has unsupported characters. Use one glob with *, ?, ** and {a,b}, e.g. **/*.py; "
            "not a regex, a shell pipeline, or a comma-separated list."
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
    if _DENY_REMOTE.search(text):
        return "command blocked by denylist (no ssh/scp/sftp hops from a host; add that machine as its own host)"
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


def _relax_paths(args: dict, base: str, windows: bool, name: str = "") -> None:
    """After an allowed call outside the working root, rewrite relative targets
    absolute so empty-root confinement accepts them (it rejects leftover '..'
    segments). Omitted targets keep defaulting to the working root rather
    than to whatever the empty root means on the host (home)."""
    if base and base not in ("~", "$HOME", "%USERPROFILE%"):
        if name in ("run_terminal_command", "monitor") and not args.get("cwd"):
            args["cwd"] = base
        elif name in ("list_dir", "grep", "glob") and not args.get("path"):
            args["path"] = base
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
        return await _job_output(user_id, args, thread_id=thread_id, run_id=run_id)
    mode = hosts.settings_for(user_id).get("permission_mode") or "project"
    if mode == "read_only" and name in ("write_file", "search_replace", "run_terminal_command", "monitor", "kill_command"):
        msg = "permission mode is read_only"
        return msg, _error_rec(name, msg, kind="not_allowed")
    if name == "kill_command":
        return _job_kill(user_id, args, thread_id=thread_id)
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
    if mode == "read_only" and name in ("write_file", "search_replace", "run_terminal_command", "monitor", "kill_command"):
        detail = "project is read-only"
        return detail, _error_rec(name, detail, host=alias, kind="not_allowed")
    deny_reason = ""
    if name in ("run_terminal_command", "monitor"):
        deny_reason = denied(str(args.get("command") or ""), windows=windows) or ""
    if deny_reason and gate is None:
        return deny_reason, _error_rec(name, deny_reason, host=alias)
    if gate is not None:
        from . import permissions as permissions_mod

        verdict = await permissions_mod.review(
            tool=name, args=args, user_id=user_id, host_id=host_id, host_alias=alias,
            root=root, windows=windows, gate=gate, deny_reason=deny_reason,
            realpath=lambda paths: _realpaths(user_id, host_id, paths),
        )
        if not verdict.allowed:
            return verdict.detail, _error_rec(name, verdict.detail, host=alias, kind="permission_denied")
        if name in ("write_file", "search_replace") and verdict.resolved and hosts.effective_permission(
            user_id, bound_host_id=host_id, bound_cwd=verdict.resolved
        ) == "read_only":
            detail = "project is read-only"
            return detail, _error_rec(name, detail, host=alias, kind="not_allowed")
        if verdict.root is not None:
            _relax_paths(args, root, windows, name)
            root = verdict.root
        promote_path = verdict.resolved if verdict.promotable else ""
    else:
        promote_path = ""
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
    except Exception as exc:
        failure = _host_failure(exc)
        if failure:
            detail, kind = failure
            return detail, _error_rec(name, detail, host=alias, kind=kind)
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
        # Popped by chat_tools: only an in-scope write may bind the chat.
        rec["promote_path"] = promote_path
        if name in ("write_file", "search_replace"):
            invalidate_agents_cache(user_id, host_id, rec.get("path") or "")
    return text, rec


def _shell_cwd(args: dict, root: str, *, windows: bool = False) -> str:
    from .permissions import expand_temp

    raw = expand_temp(str(args.get("cwd") or ""), windows)
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


# Cap for a best-effort remote kill: the script itself waits 2s between TERM
# and KILL, and the supervisor's grace window for a command result is 12s.
_KILL_CONFIRM_TIMEOUT_S = 8.0


def _ssh_kill_confirm_script(token: str) -> str:
    """Kill script whose exit status is the proof: _ssh_kill_script always
    exits 0. Exit 0 means the TERM reached the process group the job script
    recorded (the only group a kill may ever target)."""
    return (
        f'f="{_PGID_DIR}/{token}"; pg=$(tr -dc 0-9 < "$f" 2>/dev/null); ok=1; '
        'if [ -z "$pg" ] || [ "$pg" -le 1 ] || ! kill -TERM -- -"$pg" 2>/dev/null; then ok=0; fi; '
        'if [ "$ok" -eq 1 ]; then sleep 2; kill -KILL -- -"$pg" 2>/dev/null; fi; '
        'rm -f "$f"; exit $((1 - ok))'
    )


async def _remote_group_kill(user_id, host_id, token: str) -> bool:
    """Kill the recorded process group from a second session and report
    whether the signal provably reached it. Closing the SSH channel signals
    only the top shell; children (make, test runners, servers) survive.
    True mirrors the connector's termination_confirmed: the kill path
    covered the tree, not just the transport."""
    from . import ssh_pool

    conn = await ssh_pool.connection(user_id, host_id)
    result = await conn.run(
        "bash -c " + shlex.quote(_ssh_kill_confirm_script(token)),
        check=False,
        timeout=_KILL_CONFIRM_TIMEOUT_S,
    )
    return result.exit_status == 0


def _is_agent(user_id, host_id) -> bool:
    from . import db

    row = db.fetchone("SELECT auth_type FROM user_hosts WHERE id=? AND user_id=?", (host_id, user_id))
    return bool(row) and row["auth_type"] == "agent"


# Connector protocol features by first version: multi-edit replace, native
# grep context/output modes, native glob sort (Windows), and the write/replace
# hardening (hard links, in-place fallback, owner, parents).
CONNECTOR_FEATURES_VERSION = (1, 4, 0)


def _parse_version(text: str) -> tuple[int, ...]:
    parts = re.findall(r"\d+", str(text or ""))[:3]
    return tuple(int(p) for p in parts) + (0,) * (3 - len(parts)) if parts else (0, 0, 0)


def _agent_version(user_id, host_id) -> tuple[int, ...]:
    from . import db

    row = db.fetchone("SELECT agent_version FROM user_hosts WHERE id=? AND user_id=?", (host_id, user_id))
    return _parse_version(row["agent_version"] if row else "")


def _connector_supports(user_id, host_id, feature: str) -> bool:
    return _agent_version(user_id, host_id) >= CONNECTOR_FEATURES_VERSION


def _outdated_connector(user_id, host_id, feature: str) -> WorkspaceError:
    have = ".".join(str(n) for n in _agent_version(user_id, host_id)) or "unknown"
    need = ".".join(str(n) for n in CONNECTOR_FEATURES_VERSION)
    return WorkspaceError(
        f"{feature} needs connector {need} or newer; this host runs {have}. The connector updates "
        "itself when idle; until then, use the single-call form"
    )


async def _agent_call(user_id, host_id, op: str, args: dict, seconds: int = 30, on_chunk=None) -> dict:
    from . import agent_hub

    try:
        data = await agent_hub.call(user_id, host_id, op, args, timeout=seconds, on_chunk=on_chunk)
    except agent_hub.AgentError as exc:
        raise WorkspaceError(exc.detail) from exc
    return _enrich_agent_data(data, op=op)


def _host_failure(exc: BaseException) -> tuple[str, str] | None:
    """(detail, kind) for a failure reaching the host itself (vault, host
    trust, SSH auth/key, network); None for anything else, which is a Fuse bug."""
    import asyncssh

    if isinstance(exc, HostError):  # SshError, _require_dek's vault-locked 401, ...
        return exc.detail, "vault_locked" if exc.status_code == 401 else "upstream_error"
    if isinstance(exc, (asyncssh.Error, OSError)):  # OSError: TimeoutError, refused/reset
        reason = getattr(exc, "reason", "") or str(exc) or type(exc).__name__
        return f"SSH connection failed: {reason}"[:400], "upstream_error"
    return None


def _shell_path_arg(path: str) -> str:
    # Bare ~ expands even when HOME is unset (the shell asks the passwd db).
    if path == "~":
        return "~"
    if path.startswith("~/"):
        rest = path[2:]
        return "~/" + shlex.quote(rest) if rest else "~/"
    return shlex.quote(path)


async def _realpaths(user_id, host_id, paths: list[str]) -> list[str | None]:
    """Where each path really points on the host (symlinks followed), one
    round trip; None where it cannot be resolved. Never raises."""
    marker = "__fuse_realpath__"
    script = f"printf '%s\\n' {marker}; " + "; ".join(
        # -m resolves through missing components (a write's new dirs) so a
        # symlinked parent is still followed; -f where -m is unsupported.
        f'printf "%s\\n" "$(readlink -m -- {_shell_path_arg(p)} 2>/dev/null || readlink -f -- {_shell_path_arg(p)} 2>/dev/null)"'
        for p in paths
    )
    try:
        res = await _run_remote(user_id, host_id, script, "", 10, rate=False)
        out = str(res.stdout or "")
        # Login shells may print banners first; only what follows the marker counts.
        lines = out.split(marker + "\n", 1)[1].split("\n") if marker + "\n" in out else []
    except Exception:
        return [None] * len(paths)
    if len(lines) < len(paths):
        return [None] * len(paths)
    return [line.strip() or None for line in lines[:len(paths)]]


async def _run_remote(user_id, host_id, command: str, cwd: str, seconds: int, *, rate: bool = True):
    """Short synchronous exec used by internal helpers (browse fallback etc.).
    rate=False for Fuse's own bookkeeping, which must not spend the model's
    per-host exec budget."""
    if _is_agent(user_id, host_id):
        if rate:
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

    if rate:
        _rate_ok(user_id, host_id)
    conn = await ssh_pool.connection(user_id, host_id)
    proc = await conn.create_process("bash -lc " + shlex.quote(_ssh_job_script(cwd, command)))

    async def drain(stream):
        # Keep at most _SSH_OUT_MAX but keep reading: communicate() buffered
        # everything first, so a chatty login profile or helper could grow
        # Fuse's memory without bound before the slice below.
        kept, size = [], 0
        while True:
            chunk = await stream.read(65536)
            if not chunk:
                break
            if size < _SSH_OUT_MAX:
                kept.append(chunk[: _SSH_OUT_MAX - size])
                size += len(kept[-1])
        return (b"" if kept and isinstance(kept[0], bytes) else "").join(kept)

    try:
        stdout, stderr, _ = await asyncio.wait_for(
            asyncio.gather(drain(proc.stdout), drain(proc.stderr), proc.wait()), timeout=seconds)
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


def _make_agent_runner(user_id, host_id, command: str, cwd: str, timeout_ms: int):
    from . import agent_hub, command_jobs

    async def runner(sink) -> _ExecResult:
        _rate_ok(user_id, host_id)
        incomplete = getattr(sink, "incomplete", lambda value=True: None)
        incomplete()  # completeness is re-checked against the final result
        # The runner owns the kill. A moved command is killed at the foreground
        # timeout plus the 30 minutes it is given from the move; the supervisor
        # cuts that short if the move happens late.
        kill_ms = int(timeout_ms)
        payload = {"cwd": _agent_cwd(cwd), "cmd": command, "timeout_ms": kill_ms}
        err_parts: list[str] = []
        streamed = {"out": False, "nl": True, "err_len": 0}

        def on_chunk(stream: str, data: str) -> None:
            # exec_stream: stdout is sunk live so a running job can be peeked
            # and a deadline kill keeps the output already delivered. stderr is
            # buffered locally; the final result merges it like the plain path.
            if not data:
                return
            if stream == "err":
                # Bounded: a chatty stderr must not grow server memory until exit.
                room = _SSH_OUT_MAX - streamed["err_len"]
                if room > 0:
                    err_parts.append(data[:room])
                    if len(data) > room:
                        err_parts.append("\n[stderr truncated]\n")
                streamed["err_len"] += len(data)
            else:
                streamed["out"] = True
                streamed["nl"] = data.endswith("\n")
                sink(data)

        data = await _agent_call(
            user_id, host_id, "exec_stream", payload, kill_ms / 1000 + 10, on_chunk=on_chunk,
        )
        if not data.get("ok") and str(data.get("error") or "").strip() == "unknown op":
            # Pre-exec_stream connector: buffered result only, no live chunks.
            data = await _agent_call(user_id, host_id, "exec", payload, kill_ms / 1000 + 10)
        res = _AgentExec(data)
        if res.stdout or not streamed["out"]:
            # Buffered result (or a stream that produced nothing): merge as before.
            sink(_agent_job_output(res.stdout or "", res.stderr or "".join(err_parts)))
        elif err_parts:
            sink(("" if streamed["nl"] else "\n") + "[stderr]\n" + "".join(err_parts))
        incomplete(not _agent_output_complete(data))
        if _agent_timed_out(data):
            # Connectors flag deadline kills so a killed command is not
            # misreported as a completed exit-1 run (parity with SSH).
            raise command_jobs.CommandTimedOut(termination_confirmed=data.get("termination_confirmed") is True)
        if not data.get("ok") and not (res.stdout or res.stderr or err_parts) and data.get("error") in ("agent is busy", "exec failed", "cancelled"):
            raise WorkspaceError("connector could not execute command: " + data["error"])
        return _ExecResult(res.exit_status, "")

    return runner


async def _start_ssh_process(user_id, host_id, script: str):
    """Connect and start `script`. A failure here means the command never
    ran: report it as such, never as the command's own deadline (an SSH
    connect timeout is a builtin TimeoutError, which the job supervisor reads
    as its execution deadline)."""
    from . import ssh_pool

    try:
        conn = await ssh_pool.connection(user_id, host_id)
        return await conn.create_process("bash -lc " + shlex.quote(script))
    except Exception as exc:
        failure = _host_failure(exc)
        if failure is None:
            raise
        raise WorkspaceError(f"command did not start: {failure[0]}") from exc


def _make_ssh_runner(user_id, host_id, command: str, cwd: str, timeout_ms: int):
    """Streaming SSH exec: incremental sink, deadline kill, continued drain."""

    async def runner(sink) -> _ExecResult:
        from . import command_jobs

        _rate_ok(user_id, host_id)
        token = uuid4().hex
        proc = await _start_ssh_process(user_id, host_id, _ssh_job_script(cwd, command, token))
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
            # Best-effort tree kill before the cancellation is reported; a
            # failure or a second cancellation never masks the raise.
            try:
                await asyncio.wait_for(_remote_group_kill(user_id, host_id, token), _KILL_CONFIRM_TIMEOUT_S)
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.debug("process-group kill for %s failed", token, exc_info=True)
            raise
        except TimeoutError:
            with contextlib.suppress(Exception):
                proc.kill()
            confirmed = False
            try:
                confirmed = await asyncio.wait_for(
                    _remote_group_kill(user_id, host_id, token), _KILL_CONFIRM_TIMEOUT_S
                )
            except Exception:
                logger.debug("process-group kill for %s failed", token, exc_info=True)
            # Confirmed only when the TERM provably reached the recorded
            # process group — never merely because the channel was dropped.
            raise command_jobs.CommandTimedOut(termination_confirmed=confirmed) from None
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
    if not command.strip():
        raise WorkspaceError("command is required: pass the shell command to run in `command`")
    if len(command) > 8000:
        raise WorkspaceError(f"command is {len(command)} characters; the limit is 8000. Put a long script in a file with write_file and run that file")
    cwd = _shell_cwd(args, root, windows=windows)
    background = _as_bool(args.get("background"))
    timeout_ms, wait_ms = _command_timeouts(args, background)
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
            description=args.get("description") or "",
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
    try:
        text, trace = await command_jobs.join(rec["id"], user_id, wait_ms / 1000, run_id=run_id or None)
    except asyncio.CancelledError:
        command_jobs.kill(rec["id"], user_id)
        raise
    if trace.get("status") == "running":
        early = bool(trace.get("early_return"))
        # Moved, not killed: the background limit is 30 minutes from now, and
        # the result arrives on its own. A bare `sleep` stops instead: it was
        # only a wait, and an arrival is what it was waiting for.
        if _is_sleep_command(command):
            command_jobs.kill(rec["id"], user_id)
            text, trace = command_jobs.render(rec)
            if early:
                from .run_inbox import EARLY_RETURN_NOTE

                text = f"Sleep stopped early. {EARLY_RETURN_NOTE}\n{text}"
            return text, trace
        command_jobs.arm_background(rec)
        await asyncio.to_thread(command_jobs.mark_notified, rec)
        text, trace = command_jobs.render(rec)
        if early:
            moved = (
                "Background results arrived for you (delivered in the next message), so this "
                "command was moved to the background before its timeout. Its result arrives on "
                "its own when it exits; do not re-run it."
            )
        else:
            moved = (
                f"Command did not complete within its {wait_ms // 1000}s timeout "
                "and was moved to the background."
            )
        return f"{moved}\n{text}", trace
    # Completed inside the foreground wait: report it as the shell call itself
    # (join() renders both paths as get_command_output, which the dashboard
    # would display as a joined background job).
    return command_jobs.render(rec)


def _make_ws_runner(url: str, timeout_ms: int):
    async def runner(sink):
        import websockets

        async with asyncio.timeout(timeout_ms / 1000):
            try:
                sock = await _pinned_public_sock(url)
                handed_off = False
                try:
                    # sock pins the verified IP; the URI keeps the hostname,
                    # so SNI and Host survive the pinning.
                    async with websockets.connect(url, sock=sock, open_timeout=15, max_size=65_536) as ws:
                        handed_off = True
                        async for message in ws:
                            if isinstance(message, bytes):
                                text = message.decode("utf-8", "replace")
                            else:
                                text = str(message)
                            if text and not text.endswith("\n"):
                                text += "\n"
                            if text:
                                sink(text)
                finally:
                    # websockets owns (and closes) the socket once connected.
                    if not handed_off:
                        sock.close()
            except asyncio.CancelledError:
                # The monitor's own deadline: asyncio.timeout turns this into
                # TimeoutError outside this block.
                raise
            except TimeoutError:
                # Only the 15 s handshake (open_timeout) times out in here.
                raise WorkspaceError("websocket monitor could not connect: no handshake within 15s") from None
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
        from . import command_jobs

        _rate_ok(user_id, host_id)
        token = uuid4().hex
        proc = await _start_ssh_process(user_id, host_id, _ssh_job_script(cwd, command, token))
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
            # Best-effort tree kill before the cancellation is reported; a
            # failure or a second cancellation never masks the raise.
            try:
                await asyncio.wait_for(_remote_group_kill(user_id, host_id, token), _KILL_CONFIRM_TIMEOUT_S)
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.debug("process-group kill for %s failed", token, exc_info=True)
            raise
        except TimeoutError:
            with contextlib.suppress(Exception):
                proc.kill()
            confirmed = False
            try:
                confirmed = await asyncio.wait_for(
                    _remote_group_kill(user_id, host_id, token), _KILL_CONFIRM_TIMEOUT_S
                )
            except Exception:
                logger.debug("process-group kill for %s failed", token, exc_info=True)
            # Confirmed only when the TERM provably reached the recorded
            # process group — never merely because the channel was dropped.
            raise command_jobs.CommandTimedOut(termination_confirmed=confirmed) from None
        finally:
            for dt in tasks:
                dt.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        return _ExecResult(proc.exit_status, "")

    return runner


async def _pinned_public_sock(url: str):
    """Pre-connected TCP socket pinned to a globally routable address for url.

    public_ws_url checked the DNS answers at validation time, but
    websockets.connect re-resolves, and a rebinding answer in that window
    would route the monitor to a private address. Resolve once here, require
    every answer globally routable, then connect to the pinned IP; the URI
    keeps its hostname, so TLS SNI and the Host header stay correct.
    """
    import ipaddress
    import socket
    from urllib.parse import urlparse

    parsed = urlparse(url)
    host = parsed.hostname or ""
    port = parsed.port or (443 if parsed.scheme == "wss" else 80)
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    target = None
    for family, type_, proto, _canon, sockaddr in infos:
        addr = str(sockaddr[0]).split("%", 1)[0]
        if not ipaddress.ip_address(addr).is_global:
            raise OSError(f"{host} resolves to the non-public address {addr}")
        if target is None:
            target = (family, type_, proto, sockaddr)
    if target is None:
        raise OSError(f"{host} does not resolve")
    family, type_, proto, sockaddr = target
    sock = socket.socket(family, type_, proto)
    sock.setblocking(False)
    try:
        await loop.sock_connect(sock, sockaddr)
    except BaseException:
        sock.close()
        raise
    return sock


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
    if not str(args.get("description") or "").strip():
        raise WorkspaceError("description is required: say in a few words what you are monitoring (the user sees it instead of the command)")
    if command:
        if len(command) > 8000:
            raise WorkspaceError(f"command is {len(command)} characters; the limit is 8000. Put a long script in a file with write_file and run that file")
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
            description=args.get("description") or "",
        )
    except ValueError as exc:
        raise WorkspaceError(str(exc)) from exc
    if on_started:
        _, trace = command_jobs.render(rec, tool="monitor")
        trace.pop("id", None)
        on_started(trace)
    return command_jobs.render(rec, tool="monitor")


async def _job_output(user_id: int, args: dict, *, thread_id: int | None = None, run_id: str = "") -> tuple[str, dict]:
    from . import command_jobs

    job_id = str(args.get("id") or args.get("child_id") or "")
    timeout_ms = args.get("timeout_ms")
    try:
        # One wait covers a build. The old 30s/120s cap made the model re-poll.
        wait = min(max(int(timeout_ms), 0), 600_000) / 1000 if timeout_ms is not None else 600
    except (TypeError, ValueError, OverflowError):
        wait = 600
    text, trace = await command_jobs.join(job_id, user_id, wait, thread_id=thread_id, run_id=run_id or None)
    if trace.pop("early_return", False):
        from .run_inbox import EARLY_RETURN_NOTE

        text = f"{EARLY_RETURN_NOTE}\n{text}"
    return text, trace


def _job_kill(user_id: int, args: dict, *, thread_id: int | None = None) -> tuple[str, dict]:
    from . import command_jobs

    job_id = str(args.get("id") or args.get("child_id") or "")
    return command_jobs.kill(job_id, user_id, thread_id=thread_id)


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
        prefix = _shell_prefix(needle)
        result = await conn.run(f"bash -lc {shlex.quote(prefix + ' && pwd && ls -1p')}", check=False, timeout=20)
    except VaultLocked as exc:
        raise WorkspaceError("vault locked") from exc
    except WorkspaceError:
        raise
    except Exception as exc:
        failure = _host_failure(exc)
        if failure is None:
            raise
        raise WorkspaceError(failure[0]) from exc
    out = (result.stdout or "").strip()
    if result.exit_status not in (0, None) or not out:
        raise WorkspaceError("browse failed")
    lines = out.splitlines()
    cwd = lines[0].strip() if lines else needle
    return _browse_from_ls(cwd, "", "\n".join(lines[1:]))


# One SFTP subsystem per pooled SSH connection instead of one per tool call:
# SFTP requests multiplex over a single channel, and opening a channel costs
# round trips on every read/list/write.
_sftp_clients: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()
_sftp_open_locks: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()
_host_homes: dict[int, str] = {}


class _SftpLease:
    """Borrowed pooled SFTP client. `async with` never closes it; a transport
    failure evicts it so the next call opens a fresh subsystem."""

    def __init__(self, conn, client):
        self._conn = conn
        self._client = client

    async def __aenter__(self):
        return self._client

    async def __aexit__(self, exc_type, exc, tb):
        if exc is not None and _sftp_transport_error(exc):
            if _sftp_clients.get(self._conn) is self._client:
                _sftp_clients.pop(self._conn, None)
                from . import ssh_pool

                ssh_pool.note_cached_channel(self._conn, -1)
            with contextlib.suppress(Exception):
                self._client.exit()
        return False

    def __getattr__(self, name):
        return getattr(self._client, name)


def _sftp_transport_error(exc) -> bool:
    try:
        import asyncssh
    except ImportError:  # pragma: no cover
        return False
    lost = tuple(
        cls for cls in (
            getattr(asyncssh, "SFTPConnectionLost", None),
            getattr(asyncssh, "ConnectionLost", None),
            getattr(asyncssh, "ChannelOpenError", None),
        ) if cls is not None
    )
    return isinstance(exc, (*lost, BrokenPipeError, ConnectionError, EOFError))


async def _sftp(user_id, host_id):
    from . import ssh_pool

    conn = await ssh_pool.connection(user_id, host_id)
    client = _sftp_clients.get(conn)
    if client is not None:
        return _SftpLease(conn, client)
    lock = _sftp_open_locks.get(conn)
    if lock is None:
        lock = asyncio.Lock()
        _sftp_open_locks[conn] = lock
    async with lock:
        client = _sftp_clients.get(conn)
        if client is None:
            client = await conn.start_sftp_client()
            _sftp_clients[conn] = client
            ssh_pool.note_cached_channel(conn)
    return _SftpLease(conn, client)


def _sftp_missing(exc) -> bool:
    import asyncssh

    kinds = tuple(
        cls for cls in (getattr(asyncssh, "SFTPNoSuchFile", None), getattr(asyncssh, "SFTPNoSuchPath", None))
        if cls is not None
    )
    return isinstance(exc, (*kinds, FileNotFoundError))


def _sftp_denied(exc) -> bool:
    import asyncssh

    cls = getattr(asyncssh, "SFTPPermissionDenied", None)
    return isinstance(exc, PermissionError) or (cls is not None and isinstance(exc, cls))


def _sftp_error(exc, path: str, op: str) -> WorkspaceError:
    """A precise, actionable message for an SFTP failure (never 'Tool failed.')."""
    if _sftp_missing(exc):
        if op == "write":
            return WorkspaceError(f"parent directory of {path} does not exist")
        if op == "edit":
            return WorkspaceError(f"{path}: no such file or directory. Check the path with glob, or use write_file to create it.")
        return WorkspaceError(f"{path}: no such file or directory. Check the path with glob or list_dir")
    if _sftp_denied(exc):
        return WorkspaceError(f"permission denied: {path} ({op})")
    reason = str(getattr(exc, "reason", "") or exc or exc.__class__.__name__).strip()
    return WorkspaceError(f"{op} failed for {path}: {reason[:300]}")


async def _sftp_expand_home(sftp, path: str, key: int | None = None) -> str:
    for prefix in ("~", "$HOME"):
        if path == prefix or path.startswith(prefix + "/"):
            home = str(await sftp.realpath(".")).rstrip("/")
            if key is not None and home:
                _host_homes[key] = home
            return home + path[len(prefix):]
    return path


def _sftp_version(attrs) -> tuple:
    return tuple(getattr(attrs, field, None) for field in
                 ("size", "mtime", "mtime_ns", "permissions"))


def _content_version(attrs) -> tuple | None:
    """size + mtime: whether a file changed while it was being read."""
    if attrs is None:
        return None
    ver = tuple(getattr(attrs, field, None) for field in ("size", "mtime", "mtime_ns"))
    return None if all(v is None for v in ver) else ver


def _changed_during_edit(path: str) -> WorkspaceError:
    """Something else wrote the file between this edit's own stat and its
    publish. Nothing was written; a retry starts from the current content."""
    return WorkspaceError(f"{path} changed on disk while this edit was being written, so nothing was "
                          "written. Retry the edit against its current content.")


async def _sftp_atomic_write(sftp, path: str, content: bytes, *, expected=None):
    """Publish a complete sibling temp file, never unlink the destination first.

    Existing files keep their mode (re-applied after create so the umask
    cannot strip bits) and, where the server allows it, their owner. New
    files are created 0666 filtered by the host umask, like any editor.
    When the directory is not writable but the file is, fall back to a
    verified in-place rewrite instead of failing.
    """
    import asyncssh

    try:
        attrs = await sftp.lstat(path)
    except Exception as exc:
        if not _sftp_missing(exc):
            raise _sftp_error(exc, path, "write") from exc
        attrs = None
    if attrs is not None and not stat.S_ISREG(attrs.permissions or 0):
        raise WorkspaceError("write target must be a regular file (not a symlink or directory)")
    mode = (attrs.permissions & 0o777) if attrs is not None else 0o666
    tmp = path + f".fuse-tmp.{uuid4().hex}"
    try:
        try:
            async with sftp.open(tmp, "xb", attrs=asyncssh.SFTPAttrs(permissions=mode)) as handle:
                await handle.write(content)
        except Exception as exc:
            if attrs is not None and _sftp_denied(exc):
                await _sftp_write_in_place(sftp, path, content, expected=expected)
                return
            if isinstance(exc, WorkspaceError):
                raise
            raise _sftp_error(exc, path, "write") from exc
        if attrs is not None:
            chmod = getattr(sftp, "chmod", None)
            if chmod is not None:
                with contextlib.suppress(Exception):
                    await chmod(tmp, mode)
            chown = getattr(sftp, "chown", None)
            uid, gid = getattr(attrs, "uid", None), getattr(attrs, "gid", None)
            if chown is not None and uid is not None and gid is not None:
                with contextlib.suppress(Exception):
                    await chown(tmp, uid, gid)
        if expected is not None and _sftp_version(await sftp.lstat(path)) != expected:
            raise _changed_during_edit(path)
        try:
            await sftp.posix_rename(tmp, path)
        except asyncssh.SFTPOpUnsupported:
            if attrs is not None:
                raise WorkspaceError("server does not support atomic replacement; original file preserved")
            await sftp.rename(tmp, path)
    finally:
        with contextlib.suppress(Exception):
            await sftp.remove(tmp)


async def _ssh_link_count(user_id, host_id, path: str) -> int:
    """Hard-link count of an existing file (SFTP v3 attributes omit it).
    Best effort: any failure reads as 1, the ordinary atomic path."""
    from . import ssh_pool

    quoted = _quote_host_path(path)
    cmd = f"stat -L -c %h -- {quoted} 2>/dev/null || stat -L -f %l -- {quoted} 2>/dev/null"
    try:
        async with asyncio.timeout(8):
            conn = await ssh_pool.connection(user_id, host_id)
            result = await conn.run("sh -c " + shlex.quote(cmd), check=False)
        text = _as_text(result.stdout).strip().splitlines()
        return int(text[-1]) if text and text[-1].isdigit() else 1
    except Exception:
        return 1


async def _sftp_publish(user_id, host_id, sftp, path: str, content: bytes, *, attrs=None, expected=None) -> None:
    """Write through SFTP. An existing hard-linked file is rewritten in place
    (a rename would split it into two copies); everything else is published
    atomically via a sibling temp file."""
    if attrs is not None and await _ssh_link_count(user_id, host_id, path) > 1:
        await _sftp_write_in_place(sftp, path, content, expected=expected)
        return
    await _sftp_atomic_write(sftp, path, content, expected=expected)


async def _sftp_write_in_place(sftp, path: str, content: bytes, *, expected=None) -> None:
    """Directory not writable, file writable: truncate-and-write the file itself.
    Only reached for an existing regular file; the version is re-checked first."""
    if expected is not None and _sftp_version(await sftp.lstat(path)) != expected:
        raise _changed_during_edit(path)
    # Truncation happens at open: keep the old bytes to put back if the write
    # then fails (ENOSPC, dropped connection). Unreadable file: no copy.
    original = None
    with contextlib.suppress(Exception):
        async with sftp.open(path, "rb") as handle:
            original = await handle.read()
    truncated = False
    try:
        async with sftp.open(path, "wb") as handle:
            truncated = True
            await handle.write(content)
    except Exception as exc:
        error = _sftp_error(exc, path, "write")
        if not truncated:
            raise error from exc
        try:
            if original is None:
                raise error
            async with sftp.open(path, "wb") as handle:
                await handle.write(original)
        except Exception:
            raise WorkspaceError(
                f"{error.detail}; {path} may now be truncated, so the outcome is unknown. Read it before retrying."
            ) from exc
        raise error from exc


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


def _decode_text(data: bytes) -> str:
    if b"\x00" in data:
        raise WorkspaceError("file is not UTF-8 text (contains NUL bytes); it looks binary. Inspect it with run_terminal_command (file, xxd, strings)")
    try:
        return data.decode()
    except UnicodeDecodeError:
        return data.decode("utf-8", "replace")


def _connector_read_too_big(data: dict) -> bool:
    text = _exec_text(data).lower()
    return "exceeds 65536" in text or "output exceeds" in text or "byte limit" in text


async def _agent_read_page(user_id, host_id, path: str, offset: int, limit: int) -> tuple[str, int]:
    """Connector read that shrinks the page instead of failing when the slice
    exceeds the connector's 64KB frame. Returns (source, lines_requested)."""
    attempt = limit
    while True:
        data = await _agent_call(user_id, host_id, "read", {"path": path, "offset": offset, "limit": attempt}, 30)
        if data.get("ok"):
            source = str(data.get("out") or "")
            clipped = (
                bool(data.get("truncated"))
                or source.endswith("\n[fuse-agent: output truncated]\n")
                or len(source.encode()) >= _SSH_OUT_MAX - 4
            )
            if not clipped:
                return source, attempt
        elif not _connector_read_too_big(data):
            probe = await _agent_call(user_id, host_id, "list", {"path": path}, 15)
            if probe.get("ok"):
                raise WorkspaceError("path is a directory; use list_dir to list it")
            raise _agent_error(data, "read failed")
        if attempt <= 1:
            raise WorkspaceError(
                f"line {offset} is longer than the connector's 64KB read frame; inspect it with grep"
            )
        attempt = max(1, attempt // 2)


def _image_read_error(data):
    detail = " ".join(str(data.get(key) or "") for key in ("err", "error")).strip()
    if any(word in detail.lower() for word in ("unknown op", "unsupported op", "not supported")):
        return WorkspaceError("image read requires read_binary support; update the connector")
    return WorkspaceError("connector image read failed: " + (detail[:1000] or "no error detail returned"))


def _image_arguments(args, magic):
    if magic and ("offset" in args or "limit" in args):
        raise WorkspaceError("offset and limit apply only to text files; omit them when reading an image")
    if not magic and "frame" in args:
        raise WorkspaceError("frame applies only to supported image files, not text")


async def _image_read(user_id, host_id, alias, path, args, *, probe=False):
    try:
        async with asyncio.timeout(120):
            return await _image_read_bounded(user_id, host_id, alias, path, args, probe=probe)
    except asyncio.TimeoutError:
        raise WorkspaceError("image read exceeded the time limit") from None


async def _image_read_bounded(user_id, host_id, alias, path, args, *, probe=False):
    from . import file_images

    payload = bytearray()
    if _is_agent(user_id, host_id):
        identity = None
        size = None
        while True:
            data = await _agent_call(user_id, host_id, "read_binary", {
                "path": path, "offset": len(payload), "limit": 32768,
            }, 30)
            if not data.get("ok"):
                raise _image_read_error(data)
            try:
                if "out" in data:
                    data = json.loads(data["out"])
                if not isinstance(data, dict):
                    raise ValueError
                if not data.get("ok"):
                    raise _image_read_error(data)
                if not isinstance(data["data"], str) or len(data["data"]) > 43692:
                    raise ValueError
                chunk = base64.b64decode(data["data"], validate=True)
                current_size = data["size"]
                current_identity = data["identity"]
                eof = data["eof"]
                if (type(current_size) is not int or current_size < 0 or not current_identity
                        or type(eof) is not bool or len(chunk) > 32768):
                    raise ValueError
            except (KeyError, TypeError, ValueError, binascii.Error):
                raise WorkspaceError("invalid connector binary response") from None
            if identity is None:
                _image_arguments(args, file_images.image_magic(chunk))
                if probe and not file_images.image_magic(chunk):
                    return None
                identity, size = current_identity, current_size
                if size > file_images.MAX_SOURCE:
                    raise WorkspaceError("image source exceeds 20 MiB")
            elif identity != current_identity or size != current_size:
                raise WorkspaceError("image changed while it was being read; retry read_file")
            payload.extend(chunk)
            if len(payload) > file_images.MAX_SOURCE or len(payload) > size:
                raise WorkspaceError("invalid connector image size")
            if eof:
                if len(payload) != size:
                    raise WorkspaceError("incomplete connector image read")
                break
            if not chunk or len(payload) == size:
                raise WorkspaceError("incomplete connector image read")
    else:
        lease = await _sftp(user_id, host_id)
        async with lease as sftp:
            try:
                path = await _sftp_expand_home(sftp, path, int(host_id))
                async with sftp.open(path, "rb") as handle:
                    before = await handle.stat()
                    if not stat.S_ISREG(getattr(before, "permissions", 0) or 0):
                        raise WorkspaceError("image source must be a regular file")
                    chunk = await handle.read(32768)
                    _image_arguments(args, file_images.image_magic(chunk))
                    if probe and not file_images.image_magic(chunk):
                        return None
                    if (getattr(before, "size", 0) or 0) > file_images.MAX_SOURCE:
                        raise WorkspaceError("image source exceeds 20 MiB")
                    while chunk:
                        payload.extend(chunk)
                        if len(payload) > file_images.MAX_SOURCE:
                            raise WorkspaceError("image source exceeds 20 MiB")
                        chunk = await handle.read(min(32768, file_images.MAX_SOURCE + 1 - len(payload)))
                    after = await handle.stat()
                    if _content_version(before) != _content_version(after) or len(payload) != before.size:
                        raise WorkspaceError("image changed while it was being read; retry read_file")
            except WorkspaceError:
                raise
            except Exception as exc:
                if _sftp_transport_error(exc):
                    raise
                raise _sftp_error(exc, path, "read") from exc
    blob = bytes(payload)
    if not file_images.image_magic(blob):
        if probe:
            return None
        # Routed here by a NUL-byte read, not by an image extension: the file
        # is binary, not a broken image.
        raise WorkspaceError(
            "file is binary (not UTF-8 text; contains NUL bytes) and not a supported image; "
            "use grep to search it or run_terminal_command (e.g. xxd, file) to inspect it"
        )
    try:
        image = await file_images.decode_image(blob, args.get("frame", 0))
    except asyncio.TimeoutError:
        raise WorkspaceError("image decoding exceeded the time limit") from None
    except ValueError as exc:
        raise WorkspaceError(str(exc)) from None
    body = (f"Image: {image['format']}, {image['original_width']}×{image['original_height']}; "
            f"displayed {image['width']}×{image['height']}. "
            f"Frame {image['frame']} (zero-based) of {image['frames']}; only this frame is shown.")
    return _label(alias, body), {"name": "read_file", "status": "done", "host": alias,
                                 "path": path, "preview": body, "image": image}


async def _read(user_id, host_id, alias, args, root, *, windows: bool = False, max_chars: int | None = None, raw: bool = False):
    path = _confine(str(args.get("path") or ""), root, windows=windows)
    from . import file_images

    if not raw and (posixpath.splitext(path)[1].lower() in file_images.EXTENSIONS or "frame" in args):
        result = await _image_read(user_id, host_id, alias, path, args, probe=True)
        if result is not None:
            return result
    offset = _int_arg(args, "offset", 1, 1, 10_000_000)
    limit = _int_arg(args, "limit", _READ_DEFAULT_LIMIT, 1, _READ_MAX_LINES)
    chars = _READ_MAX_CHARS if max_chars is None else max_chars
    if _is_agent(user_id, host_id):
        limit = min(limit, _READ_MAX_LINES)
        try:
            source, got_limit = await _agent_read_page(user_id, host_id, path, offset, limit)
        except WorkspaceError as exc:
            if not raw and any(word in exc.detail.lower() for word in ("binary", "nul", "utf-8", "utf8")):
                return await _image_read(user_id, host_id, alias, path, args)
            raise
        if not raw and ("\x00" in source or file_images.image_magic(source.encode("latin-1", "replace"))):
            return await _image_read(user_id, host_id, alias, path, args)
        if not raw and source.startswith(("\ufffd\ufffd\ufffd", r"\xff\xd8\xff", r"\u00ff\u00d8\u00ff")):
            result = await _image_read(user_id, host_id, alias, path, args, probe=True)
            if result is not None:
                return result
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
        if not chunk and offset > 1:
            raise WorkspaceError(f"offset {offset} is beyond the end of the file")
        more = False
        total = None
        if len(chunk) >= got_limit:
            # A full page: look one line past it rather than guessing, so a
            # file with exactly `limit` lines is not reported as continuing.
            nxt = await _agent_call(user_id, host_id, "read", {"path": path, "offset": offset + len(chunk), "limit": 1}, 15)
            more = not (nxt.get("ok") and not str(nxt.get("out") or ""))
            if not more:
                total = offset - 1 + len(chunk)
        else:
            total = offset - 1 + len(chunk)
        body = _read_render(chunk, offset, total=total, more=more, max_chars=chars)
        return _label(alias, body), {
            "name": "read_file",
            "status": "done",
            "host": alias,
            "path": path,
            "preview": "\n".join(chunk[:24])[:800],
        }
    lease = await _sftp(user_id, host_id)
    async with lease as sftp:
        try:
            path = await _sftp_expand_home(sftp, path, int(host_id))
            async with sftp.open(path, "rb") as handle:
                is_dir = False
                stat_fn = getattr(handle, "stat", None)
                if stat_fn is not None:
                    try:
                        attrs = await stat_fn()
                    except Exception:
                        attrs = None
                    is_dir = stat.S_ISDIR(getattr(attrs, "permissions", 0) or 0)
                if is_dir:
                    raise WorkspaceError("path is a directory; use list_dir to list it")
                data, bytes_truncated = await _sftp_read_window(handle, offset, limit)
        except WorkspaceError:
            raise
        except Exception as exc:
            if _sftp_transport_error(exc):
                raise
            try:
                await sftp.listdir(path)
                listable = True
            except Exception:
                listable = False
            if listable:
                raise WorkspaceError("path is a directory; use list_dir to list it") from exc
            raise _sftp_error(exc, path, "read") from exc
    if not raw and isinstance(data, bytes) and (file_images.image_magic(data) or b"\x00" in data):
        return await _image_read(user_id, host_id, alias, path, args)
    text = _decode_text(data) if isinstance(data, bytes) else str(data)
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


_LIST_MAX_DEPTH = 5


async def _list_tree(user_id, host_id, path: str, depth: int, *, windows: bool) -> tuple[list[str], bool]:
    """Recursive listing to `depth` levels. Returns (relative names, more)."""
    names: list[str] = []
    more = False
    if _is_agent(user_id, host_id):
        if windows:
            # Native walk (connector 1.4.0+): PowerShell recursion is not a
            # dependable listing primitive across Windows hosts.
            if not _connector_supports(user_id, host_id, "list_depth"):
                raise _outdated_connector(user_id, host_id, "list_dir depth")
            data = await _agent_call(user_id, host_id, "list", {"path": path, "depth": depth}, 18)
            if not data.get("ok"):
                raise _agent_error(data, "list failed")
        else:
            data = await _agent_call(
                user_id, host_id, "exec",
                {"cwd": _agent_cwd(path), "cmd": _tree_cmd_posix(depth, path), "timeout_ms": 15000}, 18,
            )
            if not data.get("ok") and not _pipe_ok_exit(data.get("exit"), _exec_text(data), no_match_ok=False):
                raise _agent_error(data, "list failed")
        out = str(data.get("out") or "")
        if data.get("truncated") or out.endswith("\n[fuse-agent: output truncated]\n"):
            out = out.replace("\n[fuse-agent: output truncated]\n", "")
            out = out.rsplit("\n", 1)[0] if "\n" in out else ""
            more = True
        for line in out.splitlines():
            line = line.strip().removeprefix("./")
            if line and line not in ("./", "."):
                names.append(line)
        if len(names) > _LIST_SCAN_MAX:
            names, more = names[:_LIST_SCAN_MAX], True
        return names, more
    lease = await _sftp(user_id, host_id)
    async with lease as sftp:
        try:
            base = await _sftp_expand_home(sftp, path, int(host_id))
            queue: list[tuple[str, str, int]] = [("", base, 1)]
            while queue and not more:
                rel, full, level = queue.pop(0)
                async for entry in sftp.scandir(full):
                    name = str(entry.filename or "")
                    if not name or name in (".", ".."):
                        continue
                    try:
                        perms = int(entry.attrs.permissions or 0)
                    except Exception:
                        perms = 0
                    child = f"{rel}{name}"
                    if stat.S_ISDIR(perms):
                        names.append(child + "/")
                        if level < depth and name not in _SEARCH_SKIP_DIRS:
                            queue.append((child + "/", posixpath.join(full, name), level + 1))
                    else:
                        names.append(child)
                    if len(names) > _LIST_SCAN_MAX:
                        more = True
                        break
        except WorkspaceError:
            raise
        except Exception as exc:
            if _sftp_transport_error(exc):
                raise
            raise _sftp_error(exc, path, "list") from exc
    return names, more


async def _list(user_id, host_id, alias, args, root, *, windows: bool = False, run_id: str = ""):
    path = _confine(str(args.get("path") or "."), root, windows=windows)
    depth = _int_arg(args, "depth", 1, 1, _LIST_MAX_DEPTH)
    if depth > 1:
        names, more = await _list_tree(user_id, host_id, path, depth, windows=windows)
        names.sort()
        shown = names[:_LIST_MAX_ENTRIES]
        listing = "\n".join(shown) if shown else "(no entries)"
        if more or len(names) > _LIST_MAX_ENTRIES:
            count = f"at least {len(names)}" if more else str(len(names))
            listing += (
                f"\n(showing {len(shown)} of {count} entries to depth {depth}; vendor/build directories are "
                "listed but not expanded. Lower depth, list a subdirectory, or use glob)"
            )
        return _label(alias, listing), {"name": "list_dir", "status": "done", "host": alias, "path": path, "preview": listing[:800]}
    names: list[str] = []
    more = False
    if _is_agent(user_id, host_id):
        out = ""
        got = False
        truncated = False
        if not windows:
            # -p marks dirs; -U prints as readdir so head can stop early instead
            # of waiting for ls to sort a huge directory. Sorting happens here,
            # after collection, so the shown page is the alphabetical start.
            try:
                data = await _agent_call(
                    user_id,
                    host_id,
                    "exec",
                    {
                        "cwd": _agent_cwd(path),
                        "cmd": f"set -o pipefail; ls -1ApU -- | head -n {_LIST_SCAN_MAX + 1}",
                        "timeout_ms": 8000,
                    },
                    10,
                )
                if data.get("ok") or _pipe_ok_exit(
                    data.get("exit"), _exec_text(data), no_match_ok=False
                ):
                    out = data.get("out") or ""
                    got = True
                    truncated = bool(data.get("truncated")) or out.endswith("\n[fuse-agent: output truncated]\n")
            except WorkspaceError as exc:
                if "offline" in exc.detail:
                    raise
        if not got:
            data = await _agent_call(user_id, host_id, "list", {"path": path}, 10)
            if not data.get("ok"):
                raise _agent_error(data, "list failed")
            out = data.get("out") or ""
            truncated = bool(data.get("truncated"))
        if truncated:
            # Keep only complete lines of a clipped frame.
            out = out.replace("\n[fuse-agent: output truncated]\n", "")
            out = out.rsplit("\n", 1)[0] if "\n" in out else ""
            more = True
        names = [
            ln
            for ln in out.splitlines()
            if ln.strip() and ln.strip().rstrip("/") not in (".", "..")
        ]
        if len(names) > _LIST_SCAN_MAX:
            names = names[:_LIST_SCAN_MAX]
            more = True
    else:
        lease = await _sftp(user_id, host_id)
        async with lease as sftp:
            try:
                path = await _sftp_expand_home(sftp, path, int(host_id))
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
                    if len(names) > _LIST_SCAN_MAX:
                        more = True
                        break
            except WorkspaceError:
                raise
            except Exception as exc:
                if _sftp_transport_error(exc):
                    raise
                raise _sftp_error(exc, path, "list") from exc
    names.sort()
    total = len(names)
    shown = names[:_LIST_MAX_ENTRIES]
    listing = "\n".join(shown) if shown else "(no entries)"
    if more or total > _LIST_MAX_ENTRIES:
        count = f"at least {total}" if more else str(total)
        listing += (
            f"\n(showing {len(shown)} of {count} entries, alphabetically; "
            "use glob with a pattern to find specific files)"
        )
    return _label(alias, listing), {"name": "list_dir", "status": "done", "host": alias, "path": path, "preview": listing[:800]}


async def _tool_exec(user_id, host_id, cmd: str, cwd: str, seconds: int) -> dict:
    """Run a bounded, read-only tool command (grep/glob/list) on either
    transport. Returns {exit, out, err, timed_out}; never raises on a
    timeout — partial stdout is kept."""
    if _is_agent(user_id, host_id):
        data = await _agent_call(
            user_id,
            host_id,
            "exec",
            {"cwd": _agent_cwd(cwd or "~"), "cmd": cmd, "timeout_ms": int((seconds + 3) * 1000)},
            seconds + 6,
        )
        code = data.get("exit")
        try:
            code = int(code) if code not in (None, "") else None
        except (TypeError, ValueError, OverflowError):
            code = None
        if code is None and data.get("ok"):
            code = 0
        timed = _agent_timed_out(data) or code == 124
        return {"exit": code, "out": str(data.get("out") or ""), "err": str(data.get("err") or ""),
                "error": str(data.get("error") or ""), "timed_out": timed,
                "clipped": bool(data.get("truncated"))}
    import asyncssh

    from . import ssh_pool

    conn = await ssh_pool.connection(user_id, host_id)
    try:
        result = await conn.run("bash -lc " + shlex.quote(_ssh_tool_script(cwd, cmd)), check=False, timeout=seconds + 5)
    except (asyncssh.TimeoutError, TimeoutError) as exc:
        out = getattr(exc, "stdout", "") or ""
        err = getattr(exc, "stderr", "") or ""
        return {"exit": 124, "out": _strip_begin(_as_text(out)), "err": _as_text(err), "error": "", "timed_out": True}
    code = result.exit_status
    return {
        "exit": code,
        "out": _strip_begin(_as_text(result.stdout)),
        "err": _as_text(result.stderr),
        "error": "",
        "timed_out": code == 124,
    }


async def _grep(user_id, host_id, alias, args, root, *, windows: bool = False):
    pattern = str(args.get("pattern") or "")
    if not pattern:
        raise WorkspaceError("invalid pattern")
    if len(pattern) > _GREP_MAX_PATTERN:
        raise WorkspaceError(f"pattern exceeds {_GREP_MAX_PATTERN} characters; split the search")
    include = _grep_include_arg(args)
    max_matches = _grep_max_matches(args)
    ignore_case = _as_bool(args.get("case_insensitive"))
    no_ignore = _as_bool(args.get("no_ignore"))
    mode = _grep_mode_arg(args)
    context = _int_arg(args, "context", 0, 0, _GREP_MAX_CONTEXT) if mode == "content" else 0
    path = _confine(str(args.get("path") or "."), root, windows=windows)
    from .permissions import is_secret_path

    # An explicitly named secret file went through the permission gate's
    # secret prompt; directory searches never print secret-file contents.
    target_secret = is_secret_path(path)
    notes: list[str] = []
    if _is_agent(user_id, host_id) and windows:
        # Windows connector grep is native WalkDir; bash `command -v rg` is not.
        include = _scope_glob(include, path, _search_target(path, root, windows=True)[1], windows=True)
        req = {"path": path, "pattern": ("(?i)" + pattern) if ignore_case else pattern, "include": include, "max_matches": max_matches}
        if mode != "content" or context:
            if not _connector_supports(user_id, host_id, "grep_modes"):
                raise _outdated_connector(user_id, host_id, "grep output_mode/context")
            req.update(mode={"files_with_matches": "files", "count": "count"}.get(mode, "content"), context=context)
        data = await _agent_call(user_id, host_id, "grep", req, _GREP_TIMEOUT + 2)
        if not data.get("ok"):
            raise _agent_error(data, "grep failed")
        out, clipped = _strip_connector_clip(str(data.get("out") or ""), bool(data.get("truncated")))
        out = _render_grep_output(out)
        if clipped:
            notes.append(_clip_note("grep"))
        strip_root = root
    else:
        cwd, target = _search_target(path, root, windows=windows)
        include = _scope_glob(include, path, target)
        cmd = _grep_search_cmd(
            pattern, target, include=include, max_matches=max_matches,
            ignore_case=ignore_case, no_ignore=no_ignore, timeout_s=_GREP_TIMEOUT,
            context=context, mode=mode, skip_base=_search_skip_name(path),
        )
        data = await _tool_exec(user_id, host_id, cmd, cwd, _GREP_TIMEOUT)
        notes.extend(_search_status(data, "grep"))
        out, clipped = _strip_connector_clip(str(data.get("out") or ""), bool(data.get("clipped")))
        out = _reroot_lines(out, target)
        if clipped:
            notes.append(_clip_note("grep"))
        strip_root = ""
    rows, hidden, more = _format_search_lines(
        out, root=strip_root, windows=windows, grep=True, hide_secrets=not target_secret,
        cap_lines=max_matches, mode=mode,
    )
    if mode != "content":
        rows = sorted(rows)[:max_matches]
    if hidden:
        notes.append(
            f"({hidden} result(s) in secret-named files such as .env were withheld; "
            "read_file that file to request the user's approval)"
        )
    if more and not any("timed out" in n for n in notes):
        unit = "files" if mode != "content" else "matches"
        notes.insert(0, f"(showing the first {max_matches} {unit}; pass max_matches up to {_GREP_MAX_MATCHES} or narrow path/include)")
    if not rows and include and not no_ignore and not windows:
        notes.append(
            f"(include {include} is relative to path; .gitignored files are skipped — pass no_ignore:true "
            "to search them, or set path to one file to search just that file)"
        )
    body, _ = _pack_search_rows(rows, notes)
    preview = "\n".join(body.splitlines()[:20])[:800]
    empty = "(no matches)" if mode != "files_with_matches" else "(no matching files)"
    return _label(alias, body or empty), {
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
    no_ignore = _as_bool(args.get("no_ignore"))
    order = _glob_order_arg(args)
    notes: list[str] = []
    if _is_agent(user_id, host_id) and windows:
        pattern = _scope_glob(pattern, path, _search_target(path, root, windows=True)[1], windows=True)
        req = {"path": path, "pattern": pattern}
        native_order = "path"
        if order == "modified":
            if _connector_supports(user_id, host_id, "glob_sort"):
                req["sort"] = native_order = "modified"
            else:
                notes.append("(sorted by path: this connector version cannot sort by modification time yet)")
        data = await _agent_call(user_id, host_id, "glob", req, 14)
        if not data.get("ok"):
            raise _agent_error(data, "glob failed")
        out, clipped = _strip_connector_clip(str(data.get("out") or ""), bool(data.get("truncated")))
        if clipped:
            notes.append(_clip_note("glob"))
        strip_root = root
        order = native_order
    else:
        cwd, target = _search_target(path, root, windows=windows)
        pattern = _scope_glob(pattern, path, target)
        cmd = _glob_search_cmd(
            pattern, target, no_ignore=no_ignore, timeout_s=_GREP_TIMEOUT, order=order,
            skip_base=_search_skip_name(path),
        )
        data = await _tool_exec(user_id, host_id, cmd, cwd, _GREP_TIMEOUT)
        notes.extend(_search_status(data, "glob"))
        out, clipped = _strip_connector_clip(str(data.get("out") or ""), bool(data.get("clipped")))
        out = _reroot_lines(out, target)
        if clipped:
            notes.append(_clip_note("glob"))
        strip_root = ""
    text = _glob_result(out, root=strip_root, windows=windows, notes=notes, order=order)
    return _label(alias, text or "(no matches)"), {
        "name": "glob",
        "status": "done",
        "host": alias,
        "path": path,
        "pattern": pattern[:200],
        "preview": text[:800],
    }


_mutation_locks: weakref.WeakValueDictionary = weakref.WeakValueDictionary()


def _canonical_path(host_id, path: str, *, windows: bool) -> str:
    """Same file, same key: ~/x and /home/u/x collide once the home is known."""
    home = _host_homes.get(int(host_id))
    if windows:
        if home and re.match(r"^[A-Za-z]:\\", home) and (path == "~" or path.startswith("~\\")):
            path = home.rstrip("\\") + path[1:]
        return ntpath.normcase(path)
    if home:
        for prefix in ("~", "$HOME"):
            if path == prefix or path.startswith(prefix + "/"):
                return home + path[len(prefix):]
    return path


def _serialize_file_mutation(fn):
    @functools.wraps(fn)
    async def wrapped(user_id, host_id, alias, args, root, *, windows=False, **kwargs):
        path = _confine(str(args.get("path") or ""), root, windows=windows)
        key = (user_id, host_id, _canonical_path(host_id, path, windows=windows))
        lock = _mutation_locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            _mutation_locks[key] = lock
        async with lock:
            return await fn(user_id, host_id, alias, args, root, windows=windows, **kwargs)
    return wrapped


async def _agent_mkdir_parent(user_id, host_id, path: str, *, windows: bool) -> bool:
    if windows:
        cmd = (
            f"$p = {_ps_path_expr(path)}\n"
            "New-Item -ItemType Directory -Force -Path (Split-Path -Parent $p) | Out-Null\n"
        )
    else:
        cmd = f"p={_quote_host_path(path)}\nmkdir -p -- \"$(dirname -- \"$p\")\"\n"
    try:
        data = await _agent_call(user_id, host_id, "exec", {"cwd": "~", "cmd": cmd, "timeout_ms": 8000}, 12)
    except WorkspaceError:
        return False
    return bool(data.get("ok"))


def _write_receipt(path: str, size: int, *, existed: bool | None, old_size=None) -> str:
    """No read is required before an overwrite, so the receipt says one
    happened and how big the replaced file was: a clobber is visible."""
    if existed is False:
        return f"created {path} ({size} bytes)"
    if existed and isinstance(old_size, int):
        return f"overwrote {path} ({size} bytes; was {old_size} bytes)"
    return f"{'overwrote' if existed else 'wrote'} {path} ({size} bytes)"


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
    size = len(content.encode("utf-8"))
    if size > 400_000:
        raise WorkspaceError("content exceeds 400000 UTF-8 bytes; split the write")
    if _is_agent(user_id, host_id):
        existed = old_size = None
        # One read-only stat: refuse a directory, report an overwrite, and
        # create a missing parent directory up front instead of failing.
        info = await _agent_probe_file(user_id, host_id, path, windows=windows)
        if info is not None:
            if info.get("kind") == "dir":
                raise WorkspaceError(f"{path} is a directory, not a file. Pass a file path.")
            existed = info.get("kind") == "file"
            old_size = info.get("size")
            if info.get("parent") == "dmiss":
                await _agent_mkdir_parent(user_id, host_id, path, windows=windows)

        async def attempt() -> dict:
            try:
                return await _agent_call(user_id, host_id, "write", {"path": path, "content": content}, 45)
            except WorkspaceError as exc:
                if exc.detail == "payload too large":
                    raise WorkspaceError(
                        "content exceeds the connector transport limit (900000 bytes); "
                        "split the write or apply it with run_terminal_command"
                    ) from exc
                raise

        data = await attempt()
        if not data.get("ok"):
            base = _agent_error(data, "write failed")
            refined = await _refine_mutation_error(data, base, user_id, host_id, path, windows=windows, write=True)
            # Nothing was written when the parent is missing; creating it and
            # writing once more is not a blind retry of an uncertain mutation.
            if refined.detail.startswith("parent directory does not exist") and await _agent_mkdir_parent(
                user_id, host_id, path, windows=windows
            ):
                data = await attempt()
                if not data.get("ok"):
                    raise await _refine_mutation_error(
                        data, _agent_error(data, "write failed"), user_id, host_id, path, windows=windows, write=True
                    )
            else:
                raise refined
        receipt = _write_receipt(path, size, existed=existed, old_size=old_size)
        return _label(alias, receipt), {"name": "write_file", "status": "done", "host": alias, "path": path}
    lease = await _sftp(user_id, host_id)
    async with lease as sftp:
        path = await _sftp_expand_home(sftp, path, int(host_id))
        try:
            attrs = await sftp.lstat(path)
        except Exception as exc:
            if _sftp_transport_error(exc):
                raise
            if not _sftp_missing(exc):
                raise _sftp_error(exc, path, "write") from exc
            attrs = None
        expected = _sftp_version(attrs) if attrs is not None else None
        data = content.encode()
        try:
            await _sftp_publish(user_id, host_id, sftp, path, data, attrs=attrs, expected=expected)
        except WorkspaceError as exc:
            if attrs is not None or not exc.detail.startswith("parent directory"):
                raise
            parent = posixpath.dirname(path)
            try:
                await sftp.makedirs(parent, exist_ok=True)
            except Exception as mk_exc:
                raise _sftp_error(mk_exc, parent, "create directory") from mk_exc
            await _sftp_atomic_write(sftp, path, data)
    receipt = _write_receipt(path, size, existed=attrs is not None, old_size=getattr(attrs, "size", None))
    return _label(alias, receipt), {"name": "write_file", "status": "done", "host": alias, "path": path}


@_serialize_file_mutation
async def _replace(user_id, host_id, alias, args, root, *, windows: bool = False):
    """No prior read is required: old_string is matched against the file as
    it is now, which is a stricter freshness check than any read record."""
    _require(args, "path")
    if not isinstance(args["path"], str) or not args["path"]:
        raise WorkspaceError("path must be a nonempty string")
    path = _confine(str(args.get("path") or ""), root, windows=windows)
    edits = _parse_edits(args)
    multi = "edits" in args
    if _is_agent(user_id, host_id):
        if multi and len(edits) > 1 and not _connector_supports(user_id, host_id, "multi_edit"):
            raise _outdated_connector(user_id, host_id, "Several edits in one call")
        payload: dict = {"path": path}
        if multi and len(edits) > 1:
            payload["edits"] = [{"old_string": o, "new_string": n, "replace_all": a} for o, n, a in edits]
        else:
            old, new, replace_all = edits[0]
            payload.update(old=old, new=new, replace_all=replace_all)
        try:
            data = await _agent_call(user_id, host_id, "replace", payload, 45)
        except WorkspaceError as exc:
            if exc.detail == "payload too large":
                raise WorkspaceError(
                    "old_string/new_string exceeds the connector transport limit (900000 bytes); "
                    "use a smaller old_string with less context or apply the change "
                    "with run_terminal_command"
                ) from exc
            raise
        error = str(data.get("error") or "").strip().lower()
        if "edits" not in payload:
            old, new, replace_all = edits[0]
            if not data.get("ok") and "not found" in error and "\r" not in old and "\n" in old:
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
            count = len(edits)
            out = str(data.get("out") or "")
            match = re.search(r"(\d+)\s+replacement", out)
            if match:
                count = int(match.group(1))
            # The connector applies the edit remotely; fetch the first page of
            # the edited file so the result carries real line numbers, like the
            # SSH path. A missing sample (large file, legacy connector) just
            # drops the numbered snippet and the @@ headers.
            fresh = await _agent_read_sample(user_id, host_id, path)
            starts: list[int | None] = []
            snippet = ""
            for _o, n, _a in edits:
                snip, start = _post_edit_snippet(fresh, n) if fresh else ("", None)
                starts.append(start)
                if snip and not snippet:
                    snippet = snip
            diff = "\n".join(
                _replace_preview(path, *reversed(_whole_lines(fresh, n, o)), 1, start=s)
                for (o, n, _a), s in zip(edits, starts)
            )
            preview = diff[:800]
            head = f"updated {path} ({count} replacement{'s' if count != 1 else ''}"
            head += f" from {len(edits)} edits)" if multi and len(edits) > 1 else ")"
            # Like the SSH path: one edit shows its numbered lines, several
            # show the whole diff. The 800-char preview is for the UI only.
            if snippet and len(edits) == 1:
                head += ("; first edit:" if count > 1 else ":") + "\n" + snippet
            else:
                head += ":\n" + (diff if len(diff) <= 4000 else diff[:4000] + "\n… (diff clipped)")
            return _label(alias, head), {
                "name": "search_replace",
                "status": "done",
                "host": alias,
                "path": path,
                "preview": preview,
            }
        # A failed/uncertain mutation must never be retried as a whole-file write.
        base = _agent_error(data, "replace failed")
        said = str(data.get("error") or "").strip().rstrip(".")
        # The connector's terse edit errors, told the way the SSH path tells them.
        if "file does not exist" in error:
            base = WorkspaceError(f"{path} does not exist. Use write_file to create it.")
        elif "changed during edit" in error:
            base = _changed_during_edit(path)
        elif "regular file" in error:
            base = WorkspaceError(f"{path} is not a regular file; search_replace edits text files only.")
        elif "not unique" in error:
            base = WorkspaceError(f"{said}. Include more surrounding context lines, or pass replace_all=true.",
                                  extra=dict(getattr(base, "extra", None) or {}))
        elif "not found" in error and "edit " not in error and not any("\r" in o for o, _n, _a in edits):
            base = WorkspaceError(
                _NOT_FOUND_HINT + " This file may use CRLF line endings that read_file hides.",
                extra=dict(getattr(base, "extra", None) or {}),
            )
        raise await _refine_mutation_error(data, base, user_id, host_id, path, windows=windows, write=False)
    lease = await _sftp(user_id, host_id)
    async with lease as sftp:
        path = await _sftp_expand_home(sftp, path, int(host_id))
        try:
            attrs = await sftp.lstat(path)
        except Exception as exc:
            if _sftp_transport_error(exc):
                raise
            raise _sftp_error(exc, path, "edit") from exc
        if not stat.S_ISREG(getattr(attrs, "permissions", 0) or 0):
            if stat.S_ISDIR(getattr(attrs, "permissions", 0) or 0):
                raise WorkspaceError("target is a directory, not a file")
        expected = _sftp_version(attrs)
        if getattr(attrs, "size", 0) and attrs.size > _REPLACE_FILE_MAX:
            raise WorkspaceError(_too_large_edit())
        try:
            async with sftp.open(path, "rb") as handle:
                data = await handle.read(_REPLACE_FILE_MAX + 1)
        except Exception as exc:
            if _sftp_transport_error(exc):
                raise
            raise _sftp_error(exc, path, "edit") from exc
        if len(data) > _REPLACE_FILE_MAX:
            raise WorkspaceError(_too_large_edit())
        if isinstance(data, bytes) and b"\x00" in data:
            raise WorkspaceError("file is not UTF-8 text (contains NUL bytes); apply the change with run_terminal_command")
        try:
            text = data.decode() if isinstance(data, bytes) else str(data)
        except UnicodeDecodeError:
            raise WorkspaceError("file is not UTF-8 text; apply the change with run_terminal_command") from None
        updated = text
        count = 0
        for index, (old, new, replace_all) in enumerate(edits, 1):
            try:
                step, n = _apply_replace(updated, old, new, replace_all)
            except WorkspaceError as exc:
                detail = exc.detail
                if detail.startswith("old_string not found"):
                    detail += _closest_match(updated, old)
                if len(edits) > 1:
                    detail = f"edit {index} of {len(edits)}: {detail}\nNo edits were applied."
                raise WorkspaceError(detail) from None
            updated = step
            count += n
        if len(updated.encode()) > _REPLACE_FILE_MAX:
            raise WorkspaceError(
                f"replacement too large (maximum {_REPLACE_FILE_MAX} bytes); apply the change "
                "with run_terminal_command instead"
            )
        await _sftp_publish(user_id, host_id, sftp, path, updated.encode(), attrs=attrs, expected=expected)
    if len(edits) == 1:
        old, new, _a = edits[0]
        snippet = _edit_snippet(text, updated, old, new)
        preview = _replace_preview(path, *_whole_lines(text, old, new), count, start=_edit_first_line(text, old, new))
    else:
        snippet = _multi_diff(path, text, updated)
        preview = snippet[:800]
    label = f"updated {path} ({count} replacement{'s' if count != 1 else ''}"
    label += f" from {len(edits)} edits)" if len(edits) > 1 else ")"
    if snippet:
        if len(edits) > 1:
            label += ":\n" + snippet
        else:
            label += ("; first edit:" if count > 1 else ":") + "\n" + snippet
    return _label(alias, label), {
        "name": "search_replace",
        "status": "done",
        "host": alias,
        "path": path,
        "preview": preview,
    }


async def load_project_agents(user_id, bound_host_id, bound_cwd: str = "", report: dict | None = None) -> tuple[str, str]:
    """Read AGENTS.md (or agent.md / CLAUDE.md) from a bound project root. Never raises.

    A transient failure serves the last confirmed body. report, when given,
    receives a UI-facing reason: loaded / missing / locked / host.
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
        if report is not None:
            report["reason"] = reason
        return _agents_fail(key, now, reason)
    alias = _row_alias(row)
    host_id = _row_id(row)
    root = host_root(row, bound_host_id, bound_cwd)
    windows = _is_windows_row(row)
    try:
        listing, rec = await _list(user_id, host_id, alias, {"path": "."}, root, windows=windows)
    except VaultLocked:
        if report is not None:
            report["reason"] = "locked"
        return _agents_fail(key, now, "locked")
    except (WorkspaceError, Exception) as exc:
        reason = "locked" if "vault" in str(exc).lower() else "host"
        if report is not None:
            report["reason"] = reason
        return _agents_fail(key, now, reason)
    listing_rec = rec if isinstance(rec, dict) else {}
    if listing_rec.get("status") == "error":
        # A failed listing is not evidence the file is gone.
        if report is not None:
            report["reason"] = "host"
        return _agents_fail(key, now, "host")
    chosen = pick_agents_name(listing_names(listing, listing_rec))
    if chosen and not windows:
        # This read bypasses the permission gate, so an instruction file that
        # is a symlink out of the project (e.g. to ~/.ssh/id_ed25519) is
        # treated as absent rather than loaded into the system prompt.
        real, real_root = await _realpaths(user_id, host_id, [posixpath.join(root or "~", chosen), root or "~"])
        if not real or not real_root:
            if report is not None:
                report["reason"] = "host"
            return _agents_fail(key, now, "host")
        if not (real == real_root or real.startswith(real_root.rstrip("/") + "/")):
            chosen = ""
    if not chosen:
        # A successful listing with no instruction file is a confirmed
        # absence, not a failure: drop any sticky body instead of resurrecting
        # deleted instructions.
        _agents_last_good.pop(key, None)
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
        if report is not None:
            report["reason"] = reason
        return _agents_fail(key, now, reason)
    if len(body) > AGENTS_MAX:
        # Whole lines, and where the rest starts, so the model can read it itself.
        head = body[:AGENTS_MAX - 120]
        cut = head.rfind("\n")
        head = head[:cut + 1] if cut > 0 else head
        line = head.count("\n") + 1
        body = head.rstrip("\n") + f"\n[Project instructions truncated: read_file {chosen} offset={line} for the rest]"
    else:
        body = body.strip()
    if not body:
        _agents_last_good.pop(key, None)
        _agents_cache_put(key, now + 45, "", "", "missing")
        if report is not None:
            report["reason"] = "missing"
        return "", ""
    _agents_cache_put(key, now + 90, chosen, body, "loaded")
    if len(_agents_last_good) > 256:
        _agents_last_good.clear()
    _agents_last_good[key] = (chosen, body)
    if report is not None:
        report["reason"] = "loaded"
    return chosen, body
