"""Content-versioned /static URLs.

Every stylesheet and script is served with its relative references (JS
``import``/``export … from``/``import()``, CSS ``@import`` and ``url()``)
rewritten to ``?v=<version>``, where a file's version hashes its own bytes and
the bytes of everything it transitively loads. A changed leaf therefore
re-versions every importer up to the template entry, so a versioned URL can be
cached as immutable: a browser can never pair a new entry with a stale module,
and a warm page load makes no /static requests at all. Templates get the same
versions through ``asset_url`` and the preload lists through
``asset_preloads``.
"""
from __future__ import annotations

import gzip
import hashlib
import posixpath
import re
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "static"
_ROOT_REAL = ROOT.resolve()

_TEXT = (".js", ".mjs", ".css")
# Specifier captured with its quote. Comments and unrelated strings are left
# alone because only references that resolve to a file under static/ change.
_JS_REFS = re.compile(
    r"""(\bfrom\s*|\bimport\s*\(\s*|(?<![\w$.])import\s*)(["'])([^"'\n]+)\2"""
)
_CSS_IMPORT = re.compile(r"""(@import\s+)(["'])([^"'\n]+)\2""")
# A plain "@import url(x);" (no media or layer list) is inlined in place: the
# cascade is identical, and the stylesheet entries are long ordered import
# lists, so this saves one request per chunk (dozens per page on a cold cache
# over HTTP/1.1, where only six run at a time).
_CSS_ANY_IMPORT = re.compile(r"@import\b")
_CSS_INLINE = re.compile(r"""@import\s+(?:url\(\s*(["']?)([^"')\s]+)\1\s*\)|(["'])([^"'\n]+)\3)\s*;""")
_CSS_URL = re.compile(r"""(url\(\s*)(["']?)([^"')\s]+)\2(\s*\))""")

# Static files only change on deploy in production; dev edits them in place,
# so stat results are trusted for this long before a file is re-read.
STAT_TTL = 1.0
# Bodies at least this long are served gzipped to clients that accept it.
GZIP_MIN = 1024

_lock = threading.RLock()
_files: dict[str, dict] = {}
_versions: dict[str, str] = {}
_bodies: dict[str, tuple[str, bytes]] = {}
_gzipped: dict[str, tuple[str, bytes]] = {}
_preload_lists: dict[str, list[str]] = {}
_inlining: set[str] = set()
# A production process serves the tree it started with (a deploy restarts
# it): once frozen, nothing is re-stat'ed, so a page render no longer stats
# every file its entries load. Dev never freezes.
_frozen = False


def freeze() -> None:
    global _frozen
    _frozen = True


def _norm(rel: str) -> str:
    return posixpath.normpath(rel.lstrip("/"))


def _resolve(base: str, spec: str) -> str | None:
    """static-relative path a reference from ``base`` points at, if it is a
    local file; None for bare specifiers, other origins, data: and fragments."""
    if "?" in spec or "#" in spec or ":" in spec.split("/", 1)[0]:
        return None
    if spec.startswith("/static/"):
        rel = spec[len("/static/"):]
    elif spec.startswith(("./", "../")) or (base.endswith(".css") and not spec.startswith("/")):
        rel = posixpath.normpath(posixpath.join(posixpath.dirname(base), spec))
    else:
        return None
    if rel.startswith("../") or rel == "..":
        return None
    path = ROOT / rel
    try:
        return rel if path.is_file() else None
    except OSError:
        return None


def _patterns(rel: str):
    if rel.endswith(".css"):
        return (_CSS_IMPORT, _CSS_URL)
    if rel.endswith((".js", ".mjs")):
        return (_JS_REFS,)
    return ()


def _forget(rel: str) -> None:
    # A removed file moves the version of every importer, like a change.
    if _files.pop(rel, None) is not None:
        _versions.clear()
        _bodies.clear()


def _entry(rel: str) -> dict | None:
    """Content hash and resolved references of one file, re-read when its
    stat changes (checked at most once per STAT_TTL)."""
    now = time.monotonic()
    entry = _files.get(rel)
    if entry and (_frozen or now - entry["checked"] < STAT_TTL):
        return entry
    try:
        # Request paths reach here before StaticFiles' own lookup; never read
        # outside static/.
        if not (ROOT / rel).resolve().is_relative_to(_ROOT_REAL):
            return None
        st = (ROOT / rel).stat()
    except OSError:
        _forget(rel)
        return None
    stamp = (st.st_mtime_ns, st.st_size)
    if entry and entry["stamp"] == stamp:
        entry["checked"] = now
        return entry
    try:
        data = (ROOT / rel).read_bytes()
    except OSError:
        # Removed or replaced since the stat (a deploy), or a directory:
        # treat it as missing, never a 500.
        _forget(rel)
        return None
    refs = []
    if rel.endswith(_TEXT):
        text = data.decode("utf-8", "replace")
        for pattern in _patterns(rel):
            for match in pattern.finditer(text):
                target = _resolve(rel, match.group(3))
                if target and target != rel:
                    refs.append(target)
    entry = {"stamp": stamp, "checked": now, "hash": hashlib.sha256(data).hexdigest(),
             "refs": sorted(set(refs)), "data": data if rel.endswith(_TEXT) else None}
    if _files.get(rel, {}).get("hash") != entry["hash"]:
        # Any content change can move the version of every importer.
        _versions.clear()
        _bodies.clear()
    _files[rel] = entry
    return entry


def _closure(rel: str) -> list[str]:
    seen, stack, order = {rel}, [rel], []
    while stack:
        current = stack.pop()
        entry = _entry(current)
        if not entry:
            continue
        order.append(current)
        for ref in entry["refs"]:
            if ref not in seen:
                seen.add(ref)
                stack.append(ref)
    return order


def version(rel: str) -> str | None:
    """Version of a static-relative path: a hash over the file and everything
    it loads (import cycles are fine: it is a set, not a tree)."""
    rel = _norm(rel)
    with _lock:
        cached = _versions.get(rel)
        if cached and _frozen:
            return cached
        if _entry(rel) is None:
            return None
        # Refresh stats of the whole closure (cheap within STAT_TTL); a change
        # clears _versions, so a stale cached value is never returned.
        members = _closure(rel)
        cached = _versions.get(rel)
        if cached:
            return cached
        digest = hashlib.sha256()
        for member in sorted(members):
            digest.update(member.encode())
            digest.update(_files[member]["hash"].encode())
        value = digest.hexdigest()[:12]
        _versions[rel] = value
        return value


def asset_url(rel: str) -> str:
    rel = _norm(rel)
    value = version(rel)
    return f"/static/{rel}?v={value}" if value else f"/static/{rel}"


def rewritten(rel: str) -> tuple[str, bytes] | None:
    """(version, body) of a script or stylesheet with every local reference
    pointing at its versioned URL. None for other files or missing ones."""
    rel = _norm(rel)
    if not rel.endswith(_TEXT):
        return None
    with _lock:
        value = version(rel)
        if value is None:
            return None
        cached = _bodies.get(rel)
        if cached and cached[0] == value:
            return cached
        text = _files[rel]["data"].decode("utf-8", "replace")

        def swap(match):
            target = _resolve(rel, match.group(3))
            target_version = version(target) if target else None
            if not target_version:
                return match.group(0)
            quote = match.group(2)
            if match.re is _CSS_URL:
                # Absolute, so the reference survives being inlined elsewhere.
                return f"{match.group(1)}{quote}/static/{target}?v={target_version}{quote}{match.group(4)}"
            return f"{match.group(1)}{quote}{match.group(3)}?v={target_version}{quote}"

        if rel.endswith(".css"):
            def inline(match):
                target = _resolve(rel, match.group(2) or match.group(4))
                if not target or not target.endswith(".css") or target in _inlining:
                    return match.group(0)
                child = rewritten(target)
                if not child:
                    return match.group(0)
                return f"/* {target} */\n" + child[1].decode("utf-8") + "\n"

            # All or nothing: an @import left behind after inlined rules would
            # no longer lead the sheet, and browsers drop a late @import.
            plain = [m for m in _CSS_INLINE.finditer(text)]
            local = all((t := _resolve(rel, m.group(2) or m.group(4))) and t.endswith(".css") and t not in _inlining
                        for m in plain)
            if local and len(plain) == len(_CSS_ANY_IMPORT.findall(text)):
                _inlining.add(rel)
                try:
                    text = _CSS_INLINE.sub(inline, text)
                finally:
                    _inlining.discard(rel)
        for pattern in _patterns(rel):
            text = pattern.sub(swap, text)
        body = (value, text.encode("utf-8"))
        _bodies[rel] = body
        return body


def served(rel: str, gzip_ok: bool) -> tuple[str, bytes, str | None] | None:
    """(version, body, content encoding) to send for a script or stylesheet.
    The gzip body is compressed once per version at the highest level;
    GZipMiddleware would redo it at level 3 on every request."""
    found = rewritten(rel)
    if found is None:
        return None
    value, body = found
    if not gzip_ok or len(body) < GZIP_MIN:
        return value, body, None
    rel = _norm(rel)
    with _lock:
        hit = _gzipped.get(rel)
    if not hit or hit[0] != value:
        hit = (value, gzip.compress(body, compresslevel=9, mtime=0))
        with _lock:
            _gzipped[rel] = hit
    return value, hit[1], "gzip"


def preloads(entry: str) -> list[str]:
    """Versioned URLs of everything ``entry`` loads (not the entry itself) of
    the same kind, for <link rel=modulepreload|preload> hints that fetch the
    whole graph in parallel instead of one import level per round trip."""
    entry = _norm(entry)
    if _frozen and entry in _preload_lists:
        return list(_preload_lists[entry])
    kind = ".css" if entry.endswith(".css") else (".js", ".mjs")
    with _lock:
        if _entry(entry) is None:
            return []
        members = [rel for rel in _closure(entry) if rel != entry and rel.endswith(kind)]
    urls = [asset_url(rel) for rel in sorted(members)]
    if _frozen:
        _preload_lists[entry] = urls
    return list(urls)


def warm(*, compress: bool = False) -> None:
    """Hash every script and stylesheet once, off the request path (and
    gzip it, for a process that will freeze)."""
    for path in ROOT.rglob("*"):
        if path.suffix in _TEXT and path.is_file():
            rel = path.relative_to(ROOT).as_posix()
            try:
                served(rel, compress)
            except OSError:
                pass
