"""Project instruction (AGENTS.md) cache: recognized file names, the
per-(user, host, cwd) cache with its last-good fallback, and invalidation.
The loader itself stays in the facade (it drives the list/read tools).

Moved verbatim from app/workspace.py (see sourcemap in the work dir); the
facade imports these back as its stable surface.
"""
from __future__ import annotations

import posixpath


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
