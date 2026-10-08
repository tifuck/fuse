"""Which agent has read which file, and whether it changed since.

Every agent (the thread's main agent, each subagent) keeps its own book: a
read by one never lets another edit. A mutation of an existing file needs a
read by the same agent, and the file must not have changed since:

- Fuse's own writes (write_file / search_replace by any agent) bump a
  per-file generation. A read records the generation it started at, so a
  write by another agent that lands after (or during) the read is caught
  exactly, on every host type and regardless of mtime granularity.
- Anything else (shell commands, editors, builds) is caught by the caller
  comparing the stat version and, when the read covered the whole file, a
  content digest.

In memory and bounded; a service restart only means "read it again".
"""
from __future__ import annotations

import hashlib
import ntpath
import threading
from collections import OrderedDict
from dataclasses import dataclass

from .common import WorkspaceError

_READERS_MAX = 512
_PATHS_PER_READER = 4096
_WRITES_MAX = 50_000


@dataclass(frozen=True)
class Seen:
    gen: int
    version: object = None  # host stat tuple, when the transport has one
    digest: str | None = None  # whole-file content digest, when the read covered it all


_lock = threading.Lock()
_clock = 0
# (host_id, path) -> (generation, writer) of the last Fuse write.
_writes: "OrderedDict[tuple, tuple[int, str]]" = OrderedDict()
# Generation of the newest write evicted from _writes: an unknown file is
# treated as written then, so an eviction can only force a re-read.
_evicted_floor = 0
_books: "OrderedDict[str, OrderedDict[tuple, Seen]]" = OrderedDict()


def _key(host_id, path: str, windows: bool) -> tuple:
    return (int(host_id), ntpath.normcase(path) if windows else path)


def digest(data: bytes | str) -> str:
    raw = data.encode("utf-8", "surrogatepass") if isinstance(data, str) else bytes(data)
    return hashlib.sha256(raw).hexdigest()


def _current(key) -> tuple[int, str]:
    return _writes.get(key) or (_evicted_floor, "")


def generation(host_id, path: str, *, windows: bool = False) -> int:
    """Call before a read's I/O: a write that lands during the read is newer."""
    with _lock:
        return _current(_key(host_id, path, windows))[0]


def _put(reader: str, key, seen: Seen) -> None:
    book = _books.get(reader)
    if book is None:
        book = _books[reader] = OrderedDict()
        while len(_books) > _READERS_MAX:
            _books.popitem(last=False)
    else:
        _books.move_to_end(reader)
    book.pop(key, None)
    book[key] = seen
    while len(book) > _PATHS_PER_READER:
        book.popitem(last=False)


def note_read(reader: str, host_id, path: str, *, gen: int, version=None, digest: str | None = None,
              windows: bool = False) -> None:
    if not reader or not path:
        return
    with _lock:
        _put(reader, _key(host_id, path, windows), Seen(gen, version, digest))


def note_write(reader: str, host_id, path: str, *, version=None, digest: str | None = None,
               windows: bool = False) -> None:
    """A completed Fuse write: newer for every other agent, read for the writer."""
    global _clock
    if not path:
        return
    key = _key(host_id, path, windows)
    with _lock:
        _clock += 1
        _writes.pop(key, None)
        _writes[key] = (_clock, reader)
        _evict()
        if reader:
            _put(reader, key, Seen(_clock, version, digest))


def forget(reader: str, host_id, path: str, *, windows: bool = False) -> None:
    """This reader must read the file again before editing it."""
    with _lock:
        book = _books.get(reader or "")
        if book is not None:
            book.pop(_key(host_id, path, windows), None)


def invalidate(reader: str, host_id, path: str, *, windows: bool = False) -> None:
    """A write whose outcome is unknown (timeout, dropped transport): everyone re-reads."""
    note_write(reader, host_id, path, windows=windows)
    forget(reader, host_id, path, windows=windows)


def _evict() -> None:
    global _evicted_floor
    while len(_writes) > _WRITES_MAX:
        _, (gen, _w) = _writes.popitem(last=False)
        _evicted_floor = max(_evicted_floor, gen)


def lookup(reader: str, host_id, path: str, *, windows: bool = False) -> Seen | None:
    with _lock:
        book = _books.get(reader or "")
        return book.get(_key(host_id, path, windows)) if book else None


def require_read(reader: str, host_id, path: str, shown: str, *, windows: bool = False, write: bool = False) -> Seen:
    """The reader's record of an existing file, or the error that tells it what to do."""
    key = _key(host_id, path, windows)
    with _lock:
        book = _books.get(reader or "")
        seen = book.get(key) if book else None
        if seen is None:
            raise not_read_error(shown, write=write)
        gen, writer = _current(key)
    if gen > seen.gen:
        raise changed_error(shown, by_other=bool(writer) and writer != reader)
    return seen


def not_read_error(path: str, *, write: bool = False) -> WorkspaceError:
    if write:
        return WorkspaceError(
            f"{path} already exists and has not been read. Call read_file on it first, "
            "or use search_replace for a targeted change."
        )
    return WorkspaceError(f"{path} has not been read. Call read_file on it first, then retry the edit.")


def changed_error(path: str, *, by_other: bool = False) -> WorkspaceError:
    who = " (another agent edited it)" if by_other else ""
    return WorkspaceError(
        f"{path} changed since you last read it{who}. Call read_file on it again, "
        "then redo the edit against the current content."
    )


def clear() -> None:
    global _clock, _evicted_floor
    with _lock:
        _books.clear()
        _writes.clear()
        _clock = 0
        _evicted_floor = 0
