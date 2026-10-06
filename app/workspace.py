"""Workspace tools on user SSH hosts. Fuse-local is denied unless all three flags (later PR)."""
from __future__ import annotations

import asyncio
import base64
import binascii
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
from collections import OrderedDict
from uuid import uuid4

from . import hosts
from .hosts import HostError
from .vault import VaultLocked

logger = logging.getLogger(__name__)

# A command starts at the beginning of a line, after a separator (; & | or a
# newline), or inside a subshell/substitution. The denylist targets the named
# catastrophic forms only; it is not a sandbox (the permission gate is).
# With a gate these forms always raise the ask card (never Jev, never a grant)
# and run on the user's own Allow or on bypass; gateless callers are blocked.
_CMD_START = r"(?:^|[;&|(`{]\s*|\$\(\s*|\b(?:then|do|else|exec|xargs|env|nohup|time)\s+)"
_DENY = re.compile(
    _CMD_START
    + r"(?:"
    # rm of the filesystem root itself: rm -rf /, rm -rf /*, --no-preserve-root
    r"rm\s+(?:-{1,2}[\w-]+\s+)*(?:/\*?|--no-preserve-root\b)(?=\s|$|[;&|)])"
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
# Last successful read per (user, host, cwd). A transient host/vault failure
# serves this instead of blanking the system-prompt block. Only a confirmed
# different read (missing name or empty body) drops it; a write does not,
# because the pre-write body beats no instructions until the next read lands.
_agents_last_good: dict[tuple, tuple[str, str]] = {}
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
_READ_MAX_LINE = 2000
_READ_MAX_BYTES = 50 * 1024
_READ_MAX_CHARS = _READ_MAX_BYTES  # rendered bytes, including "N: " prefixes
_LIST_MAX_ENTRIES = 400
_GREP_DEFAULT_MATCHES = 50
_GREP_MAX_MATCHES = 200
_GREP_MAX_PATTERN = 2000
_GREP_TIMEOUT = 20  # enforced on the host by coreutils timeout when present
_GREP_WALK_MAX_FILESIZE = "1M"  # unfiltered directory walks only
_CONNECTOR_CLIP = "[fuse-agent: output truncated]"
_GLOB_MAX_PATHS = 400
# Model-visible budget for grep/glob/list output. Stays under the runtime's
# per-result clip (chat_runtime.TOOL_RESULT_CHARS) so footers always survive.
_SEARCH_OUT_CHARS = 20_000
_SEARCH_MAX_COLUMNS = 1000
_SEARCH_LINE_CHARS = 500
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
    if val < lo and lo >= 1:
        # 0 from a model that fills every property means "unset", not
        # "one line" or "one match".
        return default
    return max(lo, min(val, hi))


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


def _grep_include_arg(args: dict) -> str:
    raw = args.get("include")
    if raw in (None, ""):
        raw = args.get("glob")
    include = str(raw).strip() if isinstance(raw, (str, int, float)) else ""
    if include and (len(include) > 200 or "\n" in include or "\x00" in include):
        raise WorkspaceError("include exceeds 200 characters or contains a newline; use one glob like *.py")
    return include


def _scope_glob(pattern: str, path: str, target: str, *, windows: bool = False) -> str:
    """Make an include/glob relative to the searched directory, the one
    meaning every host shares. Tolerates the spellings models use for the
    same thing: a leading ./, or the searched directory repeated in front
    (path app + include app/web/*.py)."""
    text = (pattern or "").strip()
    if not text:
        return ""
    # Only Windows uses \ as a separator; on POSIX it escapes a glob character.
    sep_text = text.replace("\\", "/") if windows else text
    while sep_text.startswith("./"):
        sep_text = sep_text[2:]
    for base in (path, target):
        base = str(base or "")
        base = (base.replace("\\", "/") if windows else base).rstrip("/")
        if not base or base == ".":
            continue
        head = base + "/"
        if (sep_text.lower() if windows else sep_text).startswith(head.lower() if windows else head) and sep_text != head:
            sep_text = sep_text[len(head):]
            break
    if windows:
        return sep_text.replace("/", "\\") if "\\" in text else sep_text
    return sep_text


def _literal_glob(pattern: str) -> bool:
    return bool(pattern) and not any(c in pattern for c in "*?[]{}")


def _ere_char(c: str) -> str:
    # Escape only ERE metacharacters: GNU grep warns about a stray \ before
    # anything else (re.escape also escapes &, ~, # and spaces).
    return "\\" + c if c in ".^$+()|{}[]*?\\" else c


def _glob_to_ere(pattern: str) -> str:
    """Translate one rg/gitignore-style glob to a POSIX ERE over a path
    relative to the searched directory: * and ? stop at /, **/ spans zero or
    more directories, [..] classes and nested {a,b} alternatives work."""
    out: list[str] = []
    i, n = 0, len(pattern)
    depth = 0
    while i < n:
        c = pattern[i]
        if pattern.startswith("**/", i) and (i == 0 or pattern[i - 1] == "/"):
            out.append("(.*/)?")
            i += 3
            continue
        if pattern.startswith("**", i):
            out.append(".*")
            i += 2
            continue
        if c == "*":
            out.append("[^/]*")
        elif c == "?":
            out.append("[^/]")
        elif c == "[":
            end = pattern.find("]", i + 2 if pattern[i + 1:i + 2] in ("!", "^") else i + 1)
            if end < 0:
                out.append("\\[")
            else:
                body = pattern[i + 1:end]
                neg = body[:1] in ("!", "^")
                body = body[1:] if neg else body
                body = body.replace("\\", "\\\\")
                out.append("[" + ("^" if neg else "") + body + "]")
                i = end + 1
                continue
        elif c == "{":
            depth += 1
            out.append("(")
        elif c == "}" and depth:
            depth -= 1
            out.append(")")
        elif c == "," and depth:
            out.append("|")
        elif c == "\\" and i + 1 < n:
            i += 1
            out.append(_ere_char(pattern[i]))
        else:
            out.append(_ere_char(c))
        i += 1
    out.append(")" * depth)
    return "".join(out)


def _include_ere(pattern: str) -> str:
    """ERE for a listed path ('./rel'): a glob without / matches the file
    name at any depth, one with / matches the whole path under the searched
    directory."""
    body = _glob_to_ere(pattern)
    if "/" in pattern:
        return "^\\./" + body + "$"
    return "(^|/)" + body + "$"


def _grep_max_matches(args: dict) -> int:
    if args.get("max_matches") not in (None, ""):
        return _int_arg(args, "max_matches", _GREP_DEFAULT_MATCHES, 1, _GREP_MAX_MATCHES)
    return _GREP_DEFAULT_MATCHES


def _quote_host_path(path: str) -> str:
    for home in ("~", "$HOME"):
        if path == home or path.startswith(home + "/"):
            return '"$HOME"' + shlex.quote(path[len(home):])
    return shlex.quote(path)


def _remote_timeout_prefix(seconds: int | None) -> str:
    """Bound a search on the host itself so a timeout still returns the
    matches printed so far (exit 124) instead of losing the whole call.
    FUSE_DL/FUSE_TO carry the same deadline into xargs children."""
    limit = int(seconds) if seconds else 3600
    head = (
        'T=""; FUSE_TO=""; if command -v timeout >/dev/null 2>&1; then '
        + (f'T="timeout -k 2 {int(seconds)}"; ' if seconds else "")
        + 'FUSE_TO=1; fi; '
    )
    return head + f"FUSE_DL=$(( $(date +%s) + {limit} )); export FUSE_TO FUSE_DL; "


_GREP_MODES = ("content", "files_with_matches", "count")
_GREP_MAX_CONTEXT = 10
_GREP_MAX_LINES = 4000


def _grep_line_cap(mode: str, max_matches: int, context: int) -> int:
    """head -n bound: result lines, not matches, when context lines are shown."""
    if mode != "content" or not context:
        return max_matches
    return min(_GREP_MAX_LINES, max_matches * (2 * context + 2))


def _enter_dir_sh(target: str, include: str = "") -> str:
    """Search a directory from inside it, so include/glob patterns are
    relative to the searched path on every host (the Windows connector's
    rule) and results print as ./rel. A literal include naming a file right
    there narrows the search to that one file."""
    literal = ""
    if include and _literal_glob(include):
        literal = f"if [ -f {shlex.quote('./' + include)} ]; then TGT={shlex.quote('./' + include)}; fi; "
    return (
        f"TGT={_quote_host_path(target)}; "
        f'if [ -d "$TGT" ]; then cd -- "$TGT" || exit 2; TGT=.; {literal}fi; '
    )


def _list_files_sh(skip_base: str, *, no_ignore: bool, size_cap: bool, include: str) -> str:
    """NUL-separated './rel' file list for a no-rg search of the current
    directory. Inside a git work tree `git ls-files` honors .gitignore the
    way rg does (no_ignore walks with find instead). Vendor/build dirs are
    skipped unless they are the searched directory itself; symlinks and
    deleted-but-tracked files are dropped, like rg's walk."""
    names = [d for d in _SEARCH_SKIP_DIRS if d != skip_base]
    size = f" -size -{1024 + 1}k" if size_cap else ""
    prunes = " -o ".join(f"-name {shlex.quote(d)}" for d in names)
    find = f"find -P . \\( -type d \\( {prunes} \\) -prune \\) -o -type f{size} -print0"
    skip_re = "(^|/)(" + "|".join(re.escape(d) for d in names) + ")/"
    git = (
        "git ls-files -z -co --exclude-standard 2>/dev/null | sed -z 's|^|./|' "
        f"| grep -zvE {shlex.quote(skip_re)} "
        f"| xargs -0 -r sh -c 'find -P \"$@\" -maxdepth 0 -type f{size} -print0 2>/dev/null; exit 0' _"
    )
    listing = (
        f"{{ if {'false' if no_ignore else 'git rev-parse --is-inside-work-tree >/dev/null 2>&1'}; "
        f"then {git}; else {find}; fi; }}"
    )
    if include:
        listing += f" | grep -zE {shlex.quote(_include_ere(include))}"
    return listing + " | LC_ALL=C sort -z"


# Runs grep over one xargs batch under the shared deadline. No-match (1) and
# a closed pipe (SIGPIPE) are not failures; a spent deadline stops xargs
# (255 -> xargs exit 124, which reads as a timeout).
_XARGS_GREP = (
    'l=$((FUSE_DL - $(date +%s))); [ "$l" -gt 0 ] || exit 255; '
    'if [ -n "$FUSE_TO" ]; then timeout -k 2 "$l" grep "$@"; else grep "$@"; fi; '
    'r=$?; case $r in 0|1) exit 0;; 124) exit 255;; esac; [ "$r" -ge 128 ] && exit 0; exit "$r"'
)


def _grep_search_cmd(
    pattern: str,
    path: str,
    include: str = "",
    max_matches: int = _GREP_DEFAULT_MATCHES,
    *,
    ignore_case: bool = False,
    no_ignore: bool = False,
    timeout_s: int | None = None,
    context: int = 0,
    mode: str = "content",
    skip_base: str | None = None,
) -> str:
    """rg if present, else grep. Never `rg || grep`: rg exits 1 on no match
    and that would walk node_modules/venv with no ignore file.

    A directory is searched from inside it, so matches print as ./rel and
    include is relative to the searched directory. Hidden files are searched
    (dot-config, .github, .env.example); the skip list still prunes .git and
    vendor trees. .gitignore is honored unless no_ignore (include uses an rg
    type filter for that reason: a positive --glob would override it). rg
    sorts by path so the first N results are the same on every call. The
    grep fallback prefers PCRE (-P) so \\d, \\s, \\b and (?i) mean the same
    thing they do in rg."""
    n = max(1, min(int(max_matches or _GREP_DEFAULT_MATCHES), _GREP_MAX_MATCHES))
    context = max(0, min(int(context or 0), _GREP_MAX_CONTEXT))
    cap = _grep_line_cap(mode, n, context)
    base = _search_skip_name(path) if skip_base is None else skip_base
    skip = _rg_skip_globs(base)
    if not include:
        rg_filter = ""
    elif "/" in include:
        rg_filter = " --glob " + shlex.quote(include)
    else:
        rg_filter = " --type-add " + shlex.quote("fuseinc:" + include) + " -t fuseinc"
    case = " -i" if ignore_case else ""
    ignore = " --no-ignore" if no_ignore else ""
    # Only an unfiltered directory walk skips large files (minified bundles,
    # logs). A search narrowed by include names the files it wants, so they
    # are searched whatever their size; rg never size-filters a file named
    # as the path itself.
    size_cap = "" if include else f" --max-filesize {_GREP_WALK_MAX_FILESIZE}"
    if mode == "files_with_matches":
        rg_mode, grep_mode, grep_tail = " -l", " -l", ""
    elif mode == "count":
        rg_mode, grep_mode, grep_tail = " -c", " -c", " | grep -v ':0$'"
    else:
        ctx = f" -C {context}" if context else ""
        rg_mode, grep_mode, grep_tail = f" -n{ctx} -m {n}", f" -n{ctx}", ""
    rg = (
        f"$T rg -H --no-heading --color=never --sort path --hidden{ignore}{case}{rg_mode}{size_cap} "
        f"--max-columns {_SEARCH_MAX_COLUMNS} "
        f'{skip}{rg_filter} -- {shlex.quote(pattern)} "$TGT"'
    )
    grep_flags = f"-H $GF{case}{grep_mode} --binary-files=without-match -e {shlex.quote(pattern)} --"
    one_file = f'$T grep {grep_flags} "$TGT"{grep_tail} | head -n {cap}'
    walk = (
        _list_files_sh(base, no_ignore=no_ignore, size_cap=not include, include=include)
        + f" | xargs -0 -r sh -c {shlex.quote(_XARGS_GREP)} _ {grep_flags}{grep_tail} | head -n {cap}; "
        # xargs 123 = some grep batch failed (unreadable file): grep's own 2.
        'rc=$?; [ "$rc" -eq 123 ] && rc=2; exit "$rc"'
    )
    fallback = (
        "GF=-E; if printf 'a1\\n' | grep -qP '\\d' 2>/dev/null; then GF=-P; fi; "
        f'if [ -f "$TGT" ]; then {one_file}; else {walk}; fi'
    )
    return (
        "set -o pipefail; "
        + _remote_timeout_prefix(timeout_s)
        + _enter_dir_sh(path, include)
        + f"if command -v rg >/dev/null 2>&1; then {rg} | head -n {cap}; "
        f"else {fallback}; fi"
    )


def _glob_search_cmd(
    pattern: str,
    path: str,
    *,
    no_ignore: bool = False,
    timeout_s: int | None = None,
    order: str = "path",
    skip_base: str | None = None,
) -> str:
    """Files under a directory matching one glob, relative to that
    directory. A pattern without / matches file names at any depth (an rg
    type filter, so .gitignore still applies); one with / matches the whole
    relative path. Without rg the same semantics come from a git/find
    listing filtered by an ERE translation of the glob."""
    base = _search_skip_name(path) if skip_base is None else skip_base
    skip = _rg_skip_globs(base)
    ignore = " --no-ignore" if no_ignore else ""
    sort = " --sortr modified" if order == "modified" else " --sort path"
    if "/" in pattern:
        rg_filter = "-g " + shlex.quote(pattern)
    else:
        rg_filter = "--type-add " + shlex.quote("fuseglob:" + pattern) + " -t fuseglob"
    rg = f'$T rg --files --hidden{ignore}{sort} --color=never {skip} {rg_filter} -- "$TGT"'
    listing = _list_files_sh(base, no_ignore=no_ignore, size_cap=False, include=pattern)
    if order == "modified":
        fallback = (
            f"{listing} | xargs -0 -r sh -c 'find -P \"$@\" -maxdepth 0 -printf \"%T@ %p\\n\"' _ "
            "| sort -rn | cut -d' ' -f2-"
        )
    else:
        fallback = f"{listing} | tr '\\0' '\\n'"
    return (
        "set -o pipefail; "
        + _remote_timeout_prefix(timeout_s)
        + f"TGT={_quote_host_path(path)}; "
        + 'if [ -d "$TGT" ]; then cd -- "$TGT" || exit 2; TGT=.; fi; '
        + f"if command -v rg >/dev/null 2>&1; then {rg} | head -n {_GLOB_MAX_PATHS}; "
        f'elif [ -d "$TGT" ]; then {fallback} | head -n {_GLOB_MAX_PATHS}; '
        f'else printf \'%s\\n\' "$TGT"; fi'
    )


def _reroot_lines(out: str, prefix: str) -> str:
    """Results of a directory search print as ./rel; put the searched
    directory back in front so paths stay relative to the project root (or
    absolute outside it), the form read_file accepts."""
    if not out or prefix in ("", "."):
        return out
    head = prefix.rstrip("/") + "/"
    return "\n".join(head + line[2:] if line.startswith("./") else line for line in out.split("\n"))


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


def _diff_sides(path: str) -> tuple[str, str]:
    """a/ b/ labels for relative paths; absolute paths are their own labels."""
    if path.startswith(("/", "\\", "~")) or re.match(r"^[A-Za-z]:[\\/]", path):
        return path, path
    return "a/" + path, "b/" + path


_HUNK_LINE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@$")


def _replace_preview(path: str, old: str, new: str, count: int, start: int | None = None) -> str:
    """Diff of one edit. With `start` (1-based file line of old_string) the
    hunk headers carry real file line numbers; without it there is no honest
    header, so only the +/- body is shown — never a fabricated @@ -1."""
    old_lines = str(old or "").splitlines()
    new_lines = str(new or "").splitlines()
    a_side, b_side = _diff_sides(path)
    diff = list(
        difflib.unified_diff(
            old_lines,
            new_lines,
            fromfile=a_side,
            tofile=b_side,
            lineterm="",
            n=2,
        )
    )
    if not diff:
        return f"{count} replacement(s)"
    if start is None:
        body = "\n".join(ln for ln in diff if not ln.startswith(("---", "+++", "@@")))
    else:
        rows = []
        for ln in diff:
            m = _HUNK_LINE.match(ln)
            if m:
                o = f"{int(m.group(1)) + start - 1}" + (f",{m.group(2)}" if m.group(2) else "")
                n = f"{int(m.group(3)) + start - 1}" + (f",{m.group(4)}" if m.group(4) else "")
                ln = f"@@ -{o} +{n} @@"
            rows.append(ln)
        body = "\n".join(rows)
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
        at = _match_lines(text, old)
        where = ", ".join(str(n) for n in at) + (", …" if count > len(at) else "")
        raise WorkspaceError(
            f"old_string is not unique ({count} occurrences, at lines {where}). Include more surrounding "
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
        raise WorkspaceError("path is outside the project root")
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
        return await _job_output(user_id, args, thread_id=thread_id)
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
    if mode == "read_only" and name in ("write_file", "search_replace", "run_terminal_command", "monitor"):
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
            text, rec = await _read(user_id, host_id, alias, args, root, windows=windows, run_id=run_id)
        elif name == "list_dir":
            text, rec = await _list(user_id, host_id, alias, args, root, windows=windows)
        elif name == "grep":
            text, rec = await _grep(user_id, host_id, alias, args, root, windows=windows)
        elif name == "glob":
            text, rec = await _glob(user_id, host_id, alias, args, root, windows=windows)
        elif name == "write_file":
            text, rec = await _write(user_id, host_id, alias, args, root, windows=windows, run_id=run_id)
        elif name == "search_replace":
            text, rec = await _replace(user_id, host_id, alias, args, root, windows=windows, run_id=run_id)
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
        # Popped by chat_tools: only an in-scope write may bind the chat.
        rec["promote_path"] = promote_path
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


_SLEEP_CMD = re.compile(r"^\s*(?:setsid\s+)?sleep\s+\d+(?:\.\d+)?\s*$")


def _is_sleep_command(command: str) -> bool:
    """A command that is only `sleep` stops at its foreground timeout instead of
    moving to the background (a wait must not outlive the wait)."""
    return bool(_SLEEP_CMD.match(command or ""))


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


_PGID_DIR = "$HOME/.cache/fuse/jobs"
_background_kills: set = set()


def _ssh_job_script(cwd: str, command: str, token: str = "") -> str:
    """bash script for an SSH command: stdin closed (a prompt fails fast
    instead of hanging until the deadline), non-interactive env, and a cwd
    failure that stops the whole command instead of running the rest of a
    `a; b` list in $HOME. With a token, the session's process group id is
    recorded so a timeout or kill can terminate the whole tree."""
    lines = [
        "exec </dev/null",
        "export GIT_TERMINAL_PROMPT=0 DEBIAN_FRONTEND=noninteractive PAGER=cat GIT_PAGER=cat",
    ]
    if token:
        lines += [
            f'__fuse_pgf="{_PGID_DIR}/{token}"',
            # Only record a group this session leads (sshd setsid()s the
            # exec'd shell), never a foreign one.
            '__fuse_pg=$(ps -o pgid= -p $$ 2>/dev/null | tr -dc 0-9)',
            'if [ -n "$__fuse_pg" ] && { [ "$__fuse_pg" = "$$" ] || [ "$__fuse_pg" = "$PPID" ]; }; then',
            '  { mkdir -p "${__fuse_pgf%/*}" && printf \'%s\\n\' "$__fuse_pg" > "$__fuse_pgf"; } 2>/dev/null',
            "fi",
            "trap 'rm -f \"$__fuse_pgf\" 2>/dev/null' EXIT",
        ]
    lines.append(f"{_shell_prefix(cwd)} || exit 1")
    lines.append(command)
    return "\n".join(lines) + "\n"


def _ssh_kill_script(token: str) -> str:
    return (
        f'f="{_PGID_DIR}/{token}"; pg=$(tr -dc 0-9 < "$f" 2>/dev/null); '
        'if [ -n "$pg" ] && [ "$pg" -gt 1 ]; then kill -TERM -- -"$pg" 2>/dev/null; '
        'sleep 2; kill -KILL -- -"$pg" 2>/dev/null; fi; rm -f "$f"; exit 0'
    )


def _schedule_group_kill(user_id, host_id, token: str) -> None:
    """Closing the SSH channel signals only the top shell; children (make,
    test runners, servers) survive. Kill the recorded process group from a
    second session. Detached so a cancelled job never blocks on it."""

    async def run():
        from . import ssh_pool

        try:
            conn = await ssh_pool.connection(user_id, host_id)
            await conn.run("bash -c " + shlex.quote(_ssh_kill_script(token)), check=False, timeout=10)
        except Exception:
            logger.debug("process-group kill for %s failed", token, exc_info=True)

    task = asyncio.get_running_loop().create_task(run())
    _background_kills.add(task)
    task.add_done_callback(_background_kills.discard)


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


async def _agent_call(user_id, host_id, op: str, args: dict, seconds: int = 30, on_chunk=None) -> dict:
    from . import agent_hub

    try:
        data = await agent_hub.call(user_id, host_id, op, args, timeout=seconds, on_chunk=on_chunk)
    except agent_hub.AgentError as exc:
        raise WorkspaceError(exc.detail) from exc
    return _enrich_agent_data(data, op=op)


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
        streamed = {"out": False, "nl": True}

        def on_chunk(stream: str, data: str) -> None:
            # exec_stream: stdout is sunk live so a running job can be peeked
            # and a deadline kill keeps the output already delivered. stderr is
            # buffered locally; the final result merges it like the plain path.
            if not data:
                return
            if stream == "err":
                err_parts.append(data)
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


def _make_ssh_runner(user_id, host_id, command: str, cwd: str, timeout_ms: int):
    """Streaming SSH exec: incremental sink, deadline kill, continued drain."""

    async def runner(sink) -> _ExecResult:
        from . import command_jobs, ssh_pool

        _rate_ok(user_id, host_id)
        conn = await ssh_pool.connection(user_id, host_id)
        token = uuid4().hex
        proc = await conn.create_process("bash -lc " + shlex.quote(_ssh_job_script(cwd, command, token)))
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
            _schedule_group_kill(user_id, host_id, token)
            raise
        except TimeoutError:
            with contextlib.suppress(Exception):
                proc.kill()
            _schedule_group_kill(user_id, host_id, token)
            raise command_jobs.CommandTimedOut() from None
        finally:
            for dt in dtasks:
                dt.cancel()
            await asyncio.gather(*dtasks, return_exceptions=True)
        code = proc.exit_status
        return _ExecResult(code, "")

    return runner


# —— Files a shell command changed ——
# Claude Code snapshots the git working tree around a foreground Bash call and
# prints a call that changed files on its own row, with the diff, instead of
# folding it into "ran N shell commands". The same here: a throwaway index
# (the repository's own index is never touched) records the working tree
# before and after; the diff between the two trees is the edit.
_EDIT_SNAPSHOT_S = 6
_EDIT_SLOW_S = 3.0
_EDIT_SKIP_S = 600
_EDIT_DIFF_CHARS = 12_000
_EDIT_FILES_MAX = 20
_EDIT_TREE = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?")
# (user, host, cwd) -> monotonic deadline: not a repository, or too slow to snapshot.
_edit_skip: dict[tuple[int, int, str], float] = {}


def _edit_tree_script(before: str = "") -> str:
    lines = [
        "top=$(git rev-parse --show-toplevel 2>/dev/null) || exit 3",
        "gd=$(git rev-parse --absolute-git-dir 2>/dev/null) || exit 3",
        'tmp=$(mktemp "${TMPDIR:-/tmp}/fuse-edit.XXXXXX") || exit 4',
        "trap 'rm -f \"$tmp\"' EXIT",
        'if [ -f "$gd/index" ]; then cp "$gd/index" "$tmp" || exit 4; else rm -f "$tmp"; fi',
        'cd "$top" || exit 3',
        'GIT_INDEX_FILE="$tmp" git add -A . >/dev/null 2>&1 || exit 5',
        'tree=$(GIT_INDEX_FILE="$tmp" git write-tree 2>/dev/null) || exit 5',
        'printf "@@tree %s\\n" "$tree"',
    ]
    if before:
        base = shlex.quote(before)
        lines += [
            f'[ "$tree" = {base} ] && exit 0',
            'printf "@@numstat\\n"',
            f'git diff --no-color --no-ext-diff --no-renames --numstat {base} "$tree" | head -n 200',
            'printf "@@diff\\n"',
            f'git diff --no-color --no-ext-diff --no-renames -U3 {base} "$tree" | head -c {_EDIT_DIFF_CHARS + 1}',
        ]
    return "\n".join(lines) + "\n"


async def _edit_exec(user_id, host_id, cwd: str, script: str) -> tuple[str | None, float]:
    started = time.monotonic()
    try:
        res = await _run_remote(user_id, host_id, script, cwd, _EDIT_SNAPSHOT_S, rate=False)
    except asyncio.CancelledError:
        raise
    except Exception:
        return None, time.monotonic() - started
    took = time.monotonic() - started
    if res.exit_status != 0:
        return None, took
    return str(res.stdout or ""), took


def _edit_skip_set(key, seconds: float = _EDIT_SKIP_S) -> None:
    if len(_edit_skip) > 2048:
        _edit_skip.clear()
    _edit_skip[key] = time.monotonic() + seconds


async def _edit_baseline(user_id, host_id, cwd: str) -> str:
    """Tree id of the working tree before a command; '' when not tracked."""
    key = (int(user_id), int(host_id), cwd)
    if _edit_skip.get(key, 0) > time.monotonic():
        return ""
    out, took = await _edit_exec(user_id, host_id, cwd, _edit_tree_script())
    match = re.search(r"^@@tree (\S+)$", out or "", re.M)
    tree = match.group(1) if match and _EDIT_TREE.fullmatch(match.group(1)) else ""
    if not tree or took > _EDIT_SLOW_S:
        _edit_skip_set(key)
    return tree


async def _edit_changes(user_id, host_id, cwd: str, before: str) -> dict:
    """Trace fields for the files a command changed: edited_files, lines_added,
    lines_removed and the unified diff (edit_diff). Empty when nothing changed."""
    if not _EDIT_TREE.fullmatch(before or ""):
        return {}
    out, took = await _edit_exec(user_id, host_id, cwd, _edit_tree_script(before))
    if took > _EDIT_SLOW_S:
        _edit_skip_set((int(user_id), int(host_id), cwd))
    if not out or "@@numstat\n" not in out:
        return {}
    numstat, _, diff = out.split("@@numstat\n", 1)[1].partition("@@diff\n")
    files, added, removed = [], 0, 0
    for line in numstat.splitlines():
        parts = line.split("\t", 2)
        if len(parts) != 3 or not parts[2]:
            continue
        added += int(parts[0]) if parts[0].isdigit() else 0
        removed += int(parts[1]) if parts[1].isdigit() else 0
        files.append(parts[2][:400])
    if not files:
        return {}
    if len(diff) > _EDIT_DIFF_CHARS:
        diff = diff[:_EDIT_DIFF_CHARS]
        diff = diff[: diff.rfind("\n") + 1] if "\n" in diff else diff
    return {
        "edited_files": files[:_EDIT_FILES_MAX],
        "lines_added": added,
        "lines_removed": removed,
        "edit_diff": diff.rstrip("\n"),
    }


async def _shell(user_id, host_id, alias, args, root, *, windows: bool = False, run_id: str = "", thread_id: int | None = None, on_started=None):
    from . import command_jobs

    command = str(args.get("command") or "")
    if not command.strip() or len(command) > 8000:
        raise WorkspaceError("invalid command")
    cwd = _shell_cwd(args, root, windows=windows)
    background = _as_bool(args.get("background"))
    timeout_ms, wait_ms = _command_timeouts(args, background)
    # Only a foreground call is tracked, as in Claude Code: a background
    # command finishes long after its row was printed.
    edit_base = "" if background or windows else await _edit_baseline(user_id, host_id, cwd)
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
        text, trace = await command_jobs.join(rec["id"], user_id, wait_ms / 1000)
    except asyncio.CancelledError:
        command_jobs.kill(rec["id"], user_id)
        raise
    if trace.get("status") == "running":
        # Moved, not killed: the background limit is 30 minutes from now, and
        # the result arrives on its own. A bare `sleep` stops instead.
        if _is_sleep_command(command):
            command_jobs.kill(rec["id"], user_id)
            return command_jobs.render(rec)
        command_jobs.arm_background(rec)
        await asyncio.to_thread(command_jobs.mark_notified, rec)
        text, trace = command_jobs.render(rec)
        moved = (
            f"Command did not complete within its {wait_ms // 1000}s timeout "
            "and was moved to the background."
        )
        return f"{moved}\n{text}", trace
    # Completed inside the foreground wait: report it as the shell call itself
    # (join() renders both paths as get_command_output, which the dashboard
    # would display as a joined background job).
    text, trace = command_jobs.render(rec)
    if edit_base and rec.get("status") == "completed" and rec.get("exit_code") == 0:
        trace.update(await _edit_changes(user_id, host_id, cwd, edit_base))
    return text, trace


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
        token = uuid4().hex
        proc = await conn.create_process("bash -lc " + shlex.quote(_ssh_job_script(cwd, command, token)))
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
            _schedule_group_kill(user_id, host_id, token)
            raise
        except TimeoutError:
            with contextlib.suppress(Exception):
                proc.kill()
            _schedule_group_kill(user_id, host_id, token)
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
    if not str(args.get("description") or "").strip():
        raise WorkspaceError("description is required: say in a few words what you are monitoring (the user sees it instead of the command)")
    if command:
        if len(command) > 8000:
            raise WorkspaceError("invalid command")
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


async def _job_output(user_id: int, args: dict, *, thread_id: int | None = None) -> tuple[str, dict]:
    from . import command_jobs

    job_id = str(args.get("id") or args.get("child_id") or "")
    timeout_ms = args.get("timeout_ms")
    try:
        # One wait covers a build. The old 30s/120s cap made the model re-poll.
        wait = min(max(int(timeout_ms), 0), 600_000) / 1000 if timeout_ms is not None else 600
    except (TypeError, ValueError, OverflowError):
        wait = 600
    return await command_jobs.join(job_id, user_id, wait, thread_id=thread_id)


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
    """size + mtime: what a read-before-write check compares."""
    if attrs is None:
        return None
    ver = tuple(getattr(attrs, field, None) for field in ("size", "mtime", "mtime_ns"))
    return None if all(v is None for v in ver) else ver


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
        raise WorkspaceError("file changed during edit; read it again before retrying")
    try:
        async with sftp.open(path, "wb") as handle:
            await handle.write(content)
    except Exception as exc:
        raise _sftp_error(exc, path, "write") from exc


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


# Per-run record of which files the agent has read (and at what version), so
# write_file never blindly overwrites a file it has not seen or that changed
# underneath it (another subagent, the user, a build). Bounded; a service
# restart only means "read it again".
_LEDGER_RUNS = 512
_LEDGER_PATHS = 4096
_read_ledger: "OrderedDict[str, dict]" = OrderedDict()


def _ledger_key(host_id, path: str, windows: bool) -> tuple:
    return (int(host_id), ntpath.normcase(path) if windows else path)


def ledger_note(run_id: str, host_id, path: str, version, *, windows: bool = False) -> None:
    if not run_id or not path:
        return
    book = _read_ledger.get(run_id)
    if book is None:
        book = {}
        _read_ledger[run_id] = book
        while len(_read_ledger) > _LEDGER_RUNS:
            _read_ledger.popitem(last=False)
    else:
        _read_ledger.move_to_end(run_id)
    key = _ledger_key(host_id, path, windows)
    book.pop(key, None)
    book[key] = version
    while len(book) > _LEDGER_PATHS:
        book.pop(next(iter(book)))


def ledger_lookup(run_id: str, host_id, path: str, *, windows: bool = False) -> tuple[bool, object]:
    book = _read_ledger.get(run_id or "")
    if not book:
        return False, None
    key = _ledger_key(host_id, path, windows)
    if key not in book:
        return False, None
    return True, book[key]


def _not_read_error(path: str) -> WorkspaceError:
    return WorkspaceError(
        f"{path} already exists and has not been read in this session. read_file it first "
        "(to keep unrelated content), then write, or use search_replace for a targeted change"
    )


def _changed_since_read_error(path: str) -> WorkspaceError:
    return WorkspaceError(
        f"{path} changed since you last read it (another agent, the user, or a command modified it). "
        "read_file it again and redo the change against the current content"
    )


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


async def _read(user_id, host_id, alias, args, root, *, windows: bool = False, max_chars: int | None = None, raw: bool = False, run_id: str = ""):
    path = _confine(str(args.get("path") or ""), root, windows=windows)
    ledger_path = path
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
        ledger_note(run_id, host_id, _canonical_path(host_id, ledger_path, windows=windows), None, windows=windows)
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
    version = None
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
                    version = _content_version(attrs)
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
    ledger_note(run_id, host_id, _canonical_path(host_id, ledger_path, windows=windows), version, windows=windows)
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


_LIST_SCAN_MAX = 1500  # entries collected before sorting; shown list is _LIST_MAX_ENTRIES


_LIST_MAX_DEPTH = 5


def _tree_cmd_posix(depth: int, path: str) -> str:
    """find to `depth` levels; vendor/build dirs are listed but not entered.
    Directories get a trailing slash; paths are relative to `path`."""
    names = " -o ".join(f"-name {shlex.quote(d)}" for d in _SEARCH_SKIP_DIRS if d != _search_skip_name(path))
    return (
        "set -o pipefail; "
        f"find . -mindepth 1 -maxdepth {int(depth)} "
        f"\\( -type d \\( {names} \\) -prune -exec printf '%s/\\n' {{}} + \\) "
        "-o \\( -type d -exec printf '%s/\\n' {} + \\) -o -print "
        f"| head -n {_LIST_SCAN_MAX + 1}"
    )


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


_SSH_BEGIN = "\x1efuse-begin\x1e"


def _strip_begin(out: str) -> str:
    """Drop anything a login profile printed before the tool's own output."""
    text = out or ""
    at = text.find(_SSH_BEGIN)
    if at < 0:
        return text
    rest = text[at + len(_SSH_BEGIN):]
    return rest[1:] if rest.startswith("\n") else rest


def _ssh_tool_script(cwd: str, cmd: str) -> str:
    """bash -lc script for bounded tool commands: no stdin, fail closed on a
    bad cwd, and a sentinel so login-profile chatter never reaches parsers."""
    return (
        "exec </dev/null\n"
        f"{_shell_prefix(cwd or '~')} || exit 1\n"
        f"printf '%s\\n' {shlex.quote(_SSH_BEGIN)}\n"
        f"{cmd}\n"
    )


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
        except (TypeError, ValueError):
            code = None
        if code is None and data.get("ok"):
            code = 0
        timed = _agent_timed_out(data) or code == 124
        return {"exit": code, "out": str(data.get("out") or ""), "err": str(data.get("err") or ""),
                "error": str(data.get("error") or ""), "timed_out": timed,
                "clipped": bool(data.get("truncated")) or _CONNECTOR_CLIP in str(data.get("out") or "")}
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


def _as_text(value) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return str(value or "")


def _search_status(data: dict, op: str) -> list[str]:
    """Classify a grep/glob exit. Returns notes for a usable (possibly partial)
    result; raises for a real failure. rg and grep exit 2 whenever any file is
    unreadable even though they printed valid matches — that is a partial
    success, not an error."""
    code = data.get("exit")
    out = str(data.get("out") or "")
    err = str(data.get("err") or "")
    detail = f"{err}\n{data.get('error') or ''}\n{out}"
    if data.get("timed_out"):
        return [
            f"({op} timed out after {_GREP_TIMEOUT}s; results are partial. Narrow path, include, or pattern.)"
        ]
    if _pipe_ok_exit(code, detail):
        return []
    if code == 2 and out.strip():
        first = _first_lines(err, n=1, cap=200)
        return [f"(some files could not be searched{': ' + first if first else ''})"]
    raise _agent_error(
        {"exit": code, "err": err, "out": out, "error": data.get("error") or "exit"},
        f"{op} failed",
    )


def _strip_connector_clip(out: str, clipped: bool) -> tuple[str, bool]:
    """Drop a connector's truncation marker and the partial line before it.

    The marker is not a result: left in place it reads as a match line."""
    text = out or ""
    if _CONNECTOR_CLIP not in text and not clipped:
        return text, False
    text = text.replace("\n" + _CONNECTOR_CLIP + "\n", "\n").replace(_CONNECTOR_CLIP, "")
    text = text.rstrip("\n")
    text = text.rsplit("\n", 1)[0] if "\n" in text else ""
    return text, True


def _clip_note(op: str) -> str:
    return (
        f"({op} output hit the connector's 64 KB limit; results are partial. Lower max_matches, "
        "use output_mode files_with_matches, or narrow the pattern or path.)"
        if op == "grep"
        else f"({op} output hit the connector's 64 KB limit; results are partial. Narrow the pattern or path.)"
    )


def _search_target(path: str, root: str, *, windows: bool) -> tuple[str, str]:
    """(cwd, target) so the host prints paths relative to the project root,
    which read_file and search_replace accept directly. A target outside the
    root is searched by its own path and printed as such."""
    if not root:
        return "", path
    sep = "\\" if windows else "/"
    norm = (lambda p: p.lower()) if windows else (lambda p: p)
    base = root.rstrip(sep) if root.rstrip(sep) else root
    if norm(path.rstrip(sep) or path) == norm(base):
        return root, "."
    prefix = base + sep if not base.endswith(sep) else base
    if norm(path).startswith(norm(prefix)):
        return root, path[len(prefix):] or "."
    return root, path


def _relative_line(line: str, root: str, *, windows: bool) -> str:
    """Strip './' and a root prefix from a result line (native Windows output)."""
    if line.startswith("./"):
        line = line[2:]
    elif line.startswith(".\\"):
        line = line[2:]
    if root:
        sep = "\\" if windows else "/"
        prefix = root.rstrip(sep) + sep
        if (line.lower() if windows else line).startswith(prefix.lower() if windows else prefix):
            line = line[len(prefix):]
    return line


_MATCH_LINE = re.compile(r"^(.*?):(\d+):")
_CONTEXT_LINE = re.compile(r"^(.*?)-(\d+)-")


def _grep_line_path(line: str, mode: str = "content") -> str:
    if mode == "files_with_matches":
        return line
    if mode == "count":
        return line.rsplit(":", 1)[0]
    match = _MATCH_LINE.match(line) or _CONTEXT_LINE.match(line)
    if match:
        return match.group(1)
    return line.split(":", 1)[0]


def _format_search_lines(
    out: str,
    *,
    root: str,
    windows: bool,
    grep: bool,
    hide_secrets: bool,
    cap_lines: int,
    mode: str = "content",
) -> tuple[list[str], int, bool]:
    """Normalize raw result lines. Returns (lines, hidden_secret_count, more).

    For grep content output with context, only match lines (path:N:) count
    toward cap_lines; context (path-N-) and '--' separators ride along."""
    from .permissions import is_secret_path

    rows: list[str] = []
    hidden = 0
    matches = 0
    for raw in (out or "").splitlines():
        if not raw.strip():
            continue
        line = _relative_line(raw.rstrip("\r"), root, windows=windows)
        if grep and line == "--":
            if rows and rows[-1] != "--":
                rows.append(line)
            continue
        if hide_secrets:
            loc = _grep_line_path(line, mode) if grep else line
            full_path = ntpath.join(root, loc) if windows else posixpath.join(root, loc)
            if is_secret_path(full_path):
                if not grep or mode != "content" or _MATCH_LINE.match(line):
                    hidden += 1
                continue
        if len(line) > _SEARCH_LINE_CHARS:
            line = line[:_SEARCH_LINE_CHARS] + f"… [line clipped at {_SEARCH_LINE_CHARS} chars]"
        if not grep or mode != "content" or _MATCH_LINE.match(line):
            matches += 1
        rows.append(line)
    while rows and rows[-1] == "--":
        rows.pop()
    more = matches + hidden >= cap_lines
    return rows, hidden, more


def _pack_search_rows(rows: list[str], notes: list[str], *, budget: int = _SEARCH_OUT_CHARS) -> tuple[str, bool]:
    """Join whole lines up to the budget; never cut a line in half."""
    kept: list[str] = []
    used = 0
    clipped = False
    for row in rows:
        cost = len(row) + 1
        if used + cost > budget:
            clipped = True
            break
        kept.append(row)
        used += cost
    if clipped:
        notes = [f"(output clipped at {len(kept)} of {len(rows)} lines; narrow the pattern or path)", *notes]
    body = "\n".join(kept)
    if notes:
        body = (body + "\n" if body else "") + "\n".join(notes)
    return body, clipped


def _grep_mode_arg(args: dict) -> str:
    raw = str(args.get("output_mode") or "content").strip().lower()
    aliases = {"files": "files_with_matches", "files_with_match": "files_with_matches", "l": "files_with_matches", "c": "count"}
    mode = aliases.get(raw, raw)
    if mode not in _GREP_MODES:
        raise WorkspaceError("output_mode must be content, files_with_matches, or count")
    return mode


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


def _glob_order_arg(args: dict) -> str:
    raw = str(args.get("sort") or "path").strip().lower()
    order = {"mtime": "modified", "recent": "modified", "name": "path"}.get(raw, raw)
    if order not in ("path", "modified"):
        raise WorkspaceError("sort must be path or modified")
    return order


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


def _glob_result(out: str, *, root: str = "", windows: bool = False, notes: list[str] | None = None, order: str = "path") -> str:
    rows, _hidden, more = _format_search_lines(
        out, root=root, windows=windows, grep=False, hide_secrets=False, cap_lines=_GLOB_MAX_PATHS,
    )
    rows = rows[:_GLOB_MAX_PATHS]
    if order == "path":
        rows = sorted(rows)
    notes = list(notes or [])
    if more and not any("timed out" in n for n in notes):
        which = "most recently modified" if order == "modified" else "first"
        notes.insert(0, f"(showing the {which} {_GLOB_MAX_PATHS} paths; narrow the pattern or path)")
    text, _ = _pack_search_rows(rows, notes)
    return text


_mutation_locks: weakref.WeakValueDictionary = weakref.WeakValueDictionary()


def _canonical_path(host_id, path: str, *, windows: bool) -> str:
    """Same file, same key: ~/x and /home/u/x collide once the home is known."""
    if windows:
        return ntpath.normcase(path)
    home = _host_homes.get(int(host_id))
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


@_serialize_file_mutation
async def _write(user_id, host_id, alias, args, root, *, windows: bool = False, run_id: str = ""):
    _require(args, "path", "content")
    if not isinstance(args["path"], str) or not args["path"]:
        raise WorkspaceError("path must be a nonempty string")
    path = _confine(str(args.get("path") or ""), root, windows=windows)
    ledger_path = _canonical_path(host_id, path, windows=windows)
    if not isinstance(args.get("content"), str):
        raise WorkspaceError("content must be a string")
    content = args.get("content") or ""
    if "\x00" in content:
        raise WorkspaceError("content must be text without NUL bytes")
    size = len(content.encode("utf-8"))
    if size > 400_000:
        raise WorkspaceError("content exceeds 400000 UTF-8 bytes; split the write")
    if _is_agent(user_id, host_id):
        existed = None
        seen, _version = ledger_lookup(run_id, host_id, ledger_path, windows=windows)
        if not seen:
            # One read-only stat: refuse to clobber an unread file, and create
            # a missing parent directory up front instead of failing.
            info = await _agent_probe_file(user_id, host_id, path, windows=windows)
            if info is not None:
                if info.get("kind") == "dir":
                    raise WorkspaceError("write target is a directory, not a file")
                existed = info.get("kind") == "file"
                if existed and run_id:
                    raise _not_read_error(path)
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
        ledger_note(run_id, host_id, ledger_path, None, windows=windows)
        verb = "created" if existed is False else "wrote"
        return _label(alias, f"{verb} {path} ({size} bytes)"), {"name": "write_file", "status": "done", "host": alias, "path": path}
    lease = await _sftp(user_id, host_id)
    async with lease as sftp:
        path = await _sftp_expand_home(sftp, path, int(host_id))
        ledger_path = _canonical_path(host_id, ledger_path, windows=windows)
        try:
            attrs = await sftp.lstat(path)
        except Exception as exc:
            if _sftp_transport_error(exc):
                raise
            if not _sftp_missing(exc):
                raise _sftp_error(exc, path, "write") from exc
            attrs = None
        if attrs is not None and stat.S_ISREG(getattr(attrs, "permissions", 0) or 0) and run_id:
            seen, version = ledger_lookup(run_id, host_id, ledger_path, windows=windows)
            if not seen:
                raise _not_read_error(path)
            if version is not None and _content_version(attrs) != version:
                raise _changed_since_read_error(path)
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
        version = None
        with contextlib.suppress(Exception):
            version = _content_version(await sftp.lstat(path))
    ledger_note(run_id, host_id, ledger_path, version, windows=windows)
    verb = "created" if attrs is None else "overwrote"
    return _label(alias, f"{verb} {path} ({size} bytes)"), {"name": "write_file", "status": "done", "host": alias, "path": path}


def _match_lines(text: str, needle: str, limit: int = 10) -> list[int]:
    out: list[int] = []
    at = text.find(needle)
    while at >= 0 and len(out) < limit:
        out.append(text.count("\n", 0, at) + 1)
        at = text.find(needle, at + max(1, len(needle)))
    return out


def _numbered(lines: list[str], first: int, *, max_lines: int = 40, width: int = 300) -> str:
    rows = []
    for i, line in enumerate(lines[:max_lines]):
        clip = line if len(line) <= width else line[:width] + "…"
        rows.append(f"{first + i}: {clip}")
    if len(lines) > max_lines:
        rows.append(f"… ({len(lines) - max_lines} more lines)")
    return "\n".join(rows)


def _closest_match(text: str, old: str) -> str:
    """Where the model probably meant: the window most similar to old_string,
    anchored on lines that match ignoring surrounding whitespace. Bounded."""
    olines = old.replace("\r\n", "\n").split("\n")
    k = len(olines)
    lines = text.replace("\r\n", "\n").split("\n")
    if k > 80 or len(lines) > 100_000:
        return ""
    stripped_old = [ln.strip() for ln in olines]
    anchors: dict[str, int] = {}
    for idx, s in enumerate(stripped_old):
        if s and s not in anchors:
            anchors[s] = idx
    if not anchors:
        return ""
    starts: set[int] = set()
    for i, line in enumerate(lines):
        idx = anchors.get(line.strip())
        if idx is not None:
            starts.add(max(0, i - idx))
            if len(starts) >= 300:
                break
    if not starts:
        return ""
    target = "\n".join(olines)
    best, best_start = 0.0, -1
    for start in starts:
        window = "\n".join(lines[start:start + k])
        matcher = difflib.SequenceMatcher(None, window, target, autojunk=False)
        if matcher.real_quick_ratio() <= best or matcher.quick_ratio() <= best:
            continue
        ratio = matcher.ratio()
        if ratio > best:
            best, best_start = ratio, start
    if best_start < 0 or best < 0.5:
        return ""
    return (
        f"\nClosest text in the file (lines {best_start + 1}-{best_start + k}, {int(best * 100)}% similar); "
        "compare indentation, whitespace and punctuation:\n"
        + _numbered(lines[best_start:best_start + k], best_start + 1, max_lines=30)
    )


def _edit_snippet(text: str, updated: str, old: str, new: str) -> str:
    """The edited region with line numbers, so the model can verify the result
    without re-reading the file."""
    used_old, used_new = old, new
    if text.find(old) < 0:
        normalized = _crlf_normalize(text, old, new)
        if normalized is not None:
            used_old, used_new = normalized
    at = text.find(used_old)
    if at < 0:
        return ""
    first = text.count("\n", 0, at) + 1
    lines = updated.replace("\r\n", "\n").split("\n")
    span = used_new.replace("\r\n", "\n").count("\n") + 1
    lo = max(1, first - 3)
    hi = min(len(lines), first + span - 1 + 3)
    return _numbered(lines[lo - 1:hi], lo)


def _edit_first_line(text: str, old: str, new: str) -> int | None:
    """1-based file line of old_string, or None when it cannot be located."""
    used_old, used_new = old, new
    if text.find(old) < 0:
        normalized = _crlf_normalize(text, old, new)
        if normalized is not None:
            used_old, used_new = normalized
    at = text.find(used_old)
    return None if at < 0 else text.count("\n", 0, at) + 1


def _post_edit_snippet(text: str, new: str) -> tuple[str, int | None]:
    """Numbered edited region from post-edit file text (the connector path
    never sees the pre-edit bytes). Returns (snippet, first line of the edit)."""
    if not new:
        return "", None  # a deletion leaves no anchor in the edited file
    used = new
    at = text.find(used)
    if at < 0 and "\r\n" in text and "\r" not in used:
        used = used.replace("\n", "\r\n")
        at = text.find(used)
    if at < 0:
        return "", None
    first = text.count("\n", 0, at) + 1
    lines = text.replace("\r\n", "\n").split("\n")
    span = used.replace("\r\n", "\n").count("\n") + 1
    lo = max(1, first - 3)
    hi = min(len(lines), first + span - 1 + 3)
    return _numbered(lines[lo - 1:hi], lo), first


_EDITS_MAX = 100


def _parse_edits(args: dict) -> list[tuple[str, str, bool]]:
    """One edit (old_string/new_string/replace_all) or an ordered edits list."""
    raw = args.get("edits")
    single = args.get("old_string") not in (None, "")
    # Schema-filling models send every property: an empty edits list next to
    # a real old_string, or empty old_string/new_string next to real edits.
    if raw in ([], "") and single:
        raw = None
    if raw is not None:
        if single:
            first = raw[0] if isinstance(raw, list) and raw and isinstance(raw[0], dict) else {}
            same = (
                len(raw) == 1
                and first.get("old_string") == args.get("old_string")
                and first.get("new_string") == args.get("new_string")
            )
            if not same:
                raise WorkspaceError("pass either edits or old_string/new_string, not both")
        if not isinstance(raw, list) or not 1 <= len(raw) <= _EDITS_MAX:
            raise WorkspaceError(f"edits must be a list of 1 to {_EDITS_MAX} objects")
        items = raw
    else:
        _require(args, "old_string", "new_string")
        items = [args]
    edits: list[tuple[str, str, bool]] = []
    total = 0
    for index, item in enumerate(items, 1):
        where = f"edits[{index - 1}]: " if raw is not None else ""
        if not isinstance(item, dict):
            raise WorkspaceError(f"{where}each edit must be an object")
        old, new = item.get("old_string"), item.get("new_string")
        if not isinstance(old, str) or not isinstance(new, str):
            raise WorkspaceError(f"{where}old_string and new_string must be strings")
        if not old:
            raise WorkspaceError(f"{where}old_string required")
        if old == new:
            raise WorkspaceError(f"{where}old_string and new_string are identical; nothing to change")
        if "\x00" in old or "\x00" in new:
            raise WorkspaceError(f"{where}replacement strings must not contain NUL bytes")
        size = len(old.encode("utf-8")) + len(new.encode("utf-8"))
        if len(old.encode("utf-8")) > 200_000 or len(new.encode("utf-8")) > 200_000:
            raise WorkspaceError(f"{where}old_string or new_string exceeds 200000 UTF-8 bytes; edit a smaller span")
        total += size
        edits.append((old, new, _as_bool(item.get("replace_all"))))
    if total > 800_000:
        raise WorkspaceError("edits exceed 800000 UTF-8 bytes in total; split them across calls")
    return edits


def _multi_diff(path: str, before: str, after: str, limit: int = 4000) -> str:
    """Unified diff of the whole change set with real line numbers."""
    a_side, b_side = _diff_sides(path)
    diff = difflib.unified_diff(
        before.replace("\r\n", "\n").split("\n"),
        after.replace("\r\n", "\n").split("\n"),
        fromfile=a_side, tofile=b_side, lineterm="", n=2,
    )
    body = "\n".join(list(diff)[2:])
    return body if len(body) <= limit else body[:limit] + "\n… (diff clipped)"


@_serialize_file_mutation
async def _replace(user_id, host_id, alias, args, root, *, windows: bool = False, run_id: str = ""):
    _require(args, "path")
    if not isinstance(args["path"], str) or not args["path"]:
        raise WorkspaceError("path must be a nonempty string")
    path = _confine(str(args.get("path") or ""), root, windows=windows)
    ledger_path = _canonical_path(host_id, path, windows=windows)
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
            preview = "\n".join(
                _replace_preview(path, o, n, 1, start=s)
                for (o, n, _a), s in zip(edits, starts)
            )[:800]
            ledger_note(run_id, host_id, ledger_path, None, windows=windows)
            head = f"updated {path} ({count} replacement{'s' if count != 1 else ''}"
            head += f" from {len(edits)} edits)" if multi and len(edits) > 1 else ")"
            if snippet:
                head += ("; first edit:" if count > 1 else ":") + "\n" + snippet
            return _label(alias, f"{head}\n{preview}"), {
                "name": "search_replace",
                "status": "done",
                "host": alias,
                "path": path,
                "preview": preview,
            }
        # A failed/uncertain mutation must never be retried as a whole-file write.
        base = _agent_error(data, "replace failed")
        if "not found" in error and "edit " not in error and not any("\r" in o for o, _n, _a in edits):
            base = WorkspaceError(
                _NOT_FOUND_HINT + " This file may use CRLF line endings that read_file hides.",
                extra=dict(getattr(base, "extra", None) or {}),
            )
        raise await _refine_mutation_error(data, base, user_id, host_id, path, windows=windows, write=False)
    lease = await _sftp(user_id, host_id)
    async with lease as sftp:
        path = await _sftp_expand_home(sftp, path, int(host_id))
        ledger_path = _canonical_path(host_id, ledger_path, windows=windows)
        try:
            attrs = await sftp.lstat(path)
        except Exception as exc:
            if _sftp_transport_error(exc):
                raise
            raise _sftp_error(exc, path, "edit") from exc
        if not stat.S_ISREG(getattr(attrs, "permissions", 0) or 0) and getattr(attrs, "permissions", None) is not None:
            if stat.S_ISDIR(attrs.permissions):
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
        version = None
        with contextlib.suppress(Exception):
            version = _content_version(await sftp.lstat(path))
    ledger_note(run_id, host_id, ledger_path, version, windows=windows)
    if len(edits) == 1:
        old, new, _a = edits[0]
        snippet = _edit_snippet(text, updated, old, new)
        preview = _replace_preview(path, old, new, count, start=_edit_first_line(text, old, new))
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


def _agents_fail(key, now, reason) -> tuple[str, str]:
    """Record a failed load, serving the last good body when one exists."""
    last = _agents_last_good.get(key)
    if last:
        name, body = str(last[0] or ""), str(last[1] or "")
        _agents_cache_put(key, now + 15, name, body, reason)
        return name, body
    _agents_cache_put(key, now + 15, "", "", reason)
    return "", ""


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
        marker = "\n[Project instructions truncated]"
        body = body[:AGENTS_MAX - len(marker)] + marker
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


def last_project_agents(user_id, bound_host_id, bound_cwd: str = "") -> tuple[str, str]:
    """Last confirmed (name, body) for a bound project, or ("", ""): the
    fallback for a load_project_agents call that timed out or raised at the
    caller. A confirmed missing/empty read drops the entry, so deleted
    instructions are never served."""
    if not bound_host_id:
        return "", ""
    last = _agents_last_good.get((int(user_id), int(bound_host_id), posixpath.normpath(bound_cwd or "")))
    if last:
        return str(last[0] or ""), str(last[1] or "")
    return "", ""
