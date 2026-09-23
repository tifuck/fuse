from __future__ import annotations

import threading
import time
from collections import OrderedDict
from typing import Any


class TtlLru:
    def __init__(self, max_entries: int = 512) -> None:
        self.max_entries = max_entries
        self._lock = threading.Lock()
        self._data: OrderedDict[str, tuple[float, Any]] = OrderedDict()

    def get(self, key: str):
        now = time.monotonic()
        with self._lock:
            item = self._data.get(key)
            if item is None:
                return None
            expires, value = item
            if expires <= now:
                self._data.pop(key, None)
                return None
            self._data.move_to_end(key)
            return value

    def set(self, key: str, value, ttl: float) -> None:
        now = time.monotonic()
        with self._lock:
            self._data[key] = (now + ttl, value)
            self._data.move_to_end(key)
            while len(self._data) > self.max_entries:
                self._data.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()
