from __future__ import annotations

import threading
import time
from collections import OrderedDict
from typing import Any, Callable


class TtlLru:
    """LRU with per-entry TTL. With max_bytes, sizeof(value) also bounds the
    total: entries may be megabytes each, so a count alone bounds nothing."""

    def __init__(self, max_entries: int = 512, *, max_bytes: int = 0, sizeof: Callable[[Any], int] | None = None) -> None:
        self.max_entries = max_entries
        self.max_bytes = max_bytes
        self._sizeof = sizeof
        self._lock = threading.Lock()
        self._data: OrderedDict[str, tuple[float, Any, int]] = OrderedDict()
        self._bytes = 0

    def get(self, key: str):
        now = time.monotonic()
        with self._lock:
            item = self._data.get(key)
            if item is None:
                return None
            expires, value, _size = item
            if expires <= now:
                self._drop(key)
                return None
            self._data.move_to_end(key)
            return value

    def set(self, key: str, value, ttl: float) -> None:
        now = time.monotonic()
        size = self._sizeof(value) if self._sizeof is not None else 0
        with self._lock:
            self._drop(key)
            self._data[key] = (now + ttl, value, size)
            self._bytes += size
            # Expiry is otherwise only noticed when the same key is read
            # again: expired values would stay resident until displaced.
            for stale in [k for k, item in self._data.items() if item[0] <= now]:
                self._drop(stale)
            # The newest entry always stays, even over the byte budget.
            while len(self._data) > self.max_entries or (
                self.max_bytes and self._bytes > self.max_bytes and len(self._data) > 1
            ):
                self._drop(next(iter(self._data)))

    def _drop(self, key: str) -> None:
        item = self._data.pop(key, None)
        if item is not None:
            self._bytes -= item[2]

    def clear(self) -> None:
        with self._lock:
            self._data.clear()
            self._bytes = 0
