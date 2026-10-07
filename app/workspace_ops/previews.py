"""Read pages, diff previews, and edit application for read_file and
search_replace: numbered page rendering, unified diffs, edit parsing, and
exact text replacement. Pure text work: no transport, no state.

Moved verbatim from app/workspace.py (see sourcemap in the work dir); the
facade imports these back as its stable surface.
"""
from __future__ import annotations

import difflib
import re

from .common import WorkspaceError, _as_bool, _require


_READ_MAX_LINE = 2000


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


_NOT_FOUND_HINT = (
    "old_string not found. It must match the file exactly, including whitespace and "
    "indentation; do not include the 'N:' line-number prefix or the 'From host X:' "
    "label from read_file output."
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
