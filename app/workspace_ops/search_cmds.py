"""Shell command builders for remote grep/glob/list searches, with their
size and timeout caps. Pure string construction: no transport, no state.

Moved verbatim from app/workspace.py (see sourcemap in the work dir); the
facade imports these back as its stable surface.
"""
from __future__ import annotations

import posixpath
import re
import shlex

from .common import WorkspaceError, _int_arg, _quote_host_path


_GREP_DEFAULT_MATCHES = 50
_GREP_MAX_MATCHES = 200
_GREP_MAX_PATTERN = 2000
_GREP_TIMEOUT = 20  # enforced on the host by coreutils timeout when present
_GREP_WALK_MAX_FILESIZE = "1M"  # unfiltered directory walks only


_GLOB_MAX_PATHS = 400


_SEARCH_MAX_COLUMNS = 1000


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


_GREP_MAX_CONTEXT = 10
_GREP_MAX_LINES = 4000


_LIST_SCAN_MAX = 1500  # entries collected before sorting; shown list is _LIST_MAX_ENTRIES


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
    parts = include.replace("\\", "/").split("/")
    # The literal-file narrowing turns include into './<include>' after cd'ing
    # into the searched directory, so a `..` or absolute spelling would read a
    # file outside it.
    if ".." in parts or include.startswith(("/", "~")) or include == ".":
        raise WorkspaceError("include must stay inside the searched path; use a glob like *.py")
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
            # POSIX: ']' as the first class member (after ! or ^) is literal.
            start = i + 1 + (pattern[i + 1:i + 2] in ("!", "^"))
            if pattern[start:start + 1] == "]":
                start += 1
            end = pattern.find("]", start)
            if end < 0:
                out.append("\\[")
            else:
                body = pattern[i + 1:end]
                neg = body[:1] in ("!", "^")
                body = body[1:] if neg else body
                body = body.replace("\\", "\\\\")
                # ']' must lead the class body; move it first.
                if "]" in body:
                    body = "]" + body.replace("]", "")
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


_XARGS_GREP = (
    'l=$((FUSE_DL - $(date +%s))); [ "$l" -gt 0 ] || exit 255; '
    'if [ -n "$FUSE_TO" ]; then timeout -k 2 "$l" grep "$@"; else grep "$@"; fi; '
    'r=$?; case $r in 0|1) exit 0;; 124) exit 255;; esac; [ "$r" -ge 128 ] && exit 0; exit "$r"'
)


# rg reports a missing path as an error; without rg a missing target must not
# fall through to walking the current directory (the project root).
_MISSING_TARGET_SH = 'else printf \'%s: No such file or directory\\n\' "$TGT" >&2; exit 2; fi'


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
        f'if [ -f "$TGT" ]; then {one_file}; elif [ -e "$TGT" ]; then {walk}; '
        + _MISSING_TARGET_SH
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
        f'elif [ -e "$TGT" ]; then printf \'%s\\n\' "$TGT"; '
        + _MISSING_TARGET_SH
    )


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
