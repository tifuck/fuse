"""Rendering and normalization of search results: exit classification,
connector-clip handling, secret filtering, and model-facing output packing.

Moved verbatim from app/workspace.py (see sourcemap in the work dir); the
facade imports these back as its stable surface.
"""
from __future__ import annotations

import fnmatch
import json
import ntpath
import posixpath
import re

from .common import WorkspaceError, _agent_error, _first_lines, _pipe_ok_exit
from .search_cmds import _GLOB_MAX_PATHS, _GREP_TIMEOUT


_CONNECTOR_CLIP = "[fuse-agent: output truncated]"


# Model-visible budget for grep/glob/list output. Stays under the runtime's
# per-result clip (chat_runtime.TOOL_RESULT_CHARS) so footers always survive.
_SEARCH_OUT_CHARS = 20_000


_SEARCH_LINE_CHARS = 500


_GREP_MODES = ("content", "files_with_matches", "count")


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

    The marker is not a result: left in place it reads as a match line. The
    connector only ever appends it, so the same text inside a matched line
    (a file that mentions it) is content, not a clip."""
    text = (out or "").rstrip("\n")
    if text.endswith(_CONNECTOR_CLIP):
        text = text[: -len(_CONNECTOR_CLIP)]
    elif not clipped:
        return out or "", False
    text = text.rstrip("\n")
    text = text.rsplit("\n", 1)[0] if "\n" in text else ""
    return text, True


def _clip_note(op: str) -> str:
    return (
        f"({op} output hit the connector's output limit; results are partial. Lower max_matches, "
        "use output_mode files_with_matches, or narrow the pattern or path.)"
        if op == "grep"
        else f"({op} output hit the connector's output limit; results are partial. Narrow the pattern or path.)"
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
    from ..permissions import is_secret_path

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


def _glob_order_arg(args: dict) -> str:
    raw = str(args.get("sort") or "path").strip().lower()
    order = {"mtime": "modified", "recent": "modified", "name": "path"}.get(raw, raw)
    if order not in ("path", "modified"):
        raise WorkspaceError("sort must be path or modified")
    return order


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
