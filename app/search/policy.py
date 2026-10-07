from __future__ import annotations

import json
import threading
import time

_BLACKLIST_KEY = "fetch_domain_blacklist"
_WHITELIST_KEY = "fetch_domain_whitelist"
_CACHE_TTL = 10.0

_lock = threading.Lock()
_cached: tuple[float, tuple[list[str], list[str]]] | None = None


def domain_lists() -> tuple[list[str], list[str]]:
    global _cached
    now = time.monotonic()
    with _lock:
        if _cached is not None and _cached[0] > now:
            return _cached[1]
    from .. import db
    from .ssrf import parse_domain_list

    rows = db.fetchall(
        "SELECT key, value FROM schema_meta WHERE key IN (?, ?)",
        (_BLACKLIST_KEY, _WHITELIST_KEY),
    )
    found = {row["key"]: row["value"] for row in rows}
    lists = (
        parse_domain_list(found.get(_BLACKLIST_KEY, "")),
        parse_domain_list(found.get(_WHITELIST_KEY, "")),
    )
    with _lock:
        _cached = (now + _CACHE_TTL, lists)
    return lists


def save_domain_lists(blacklist: list[str], whitelist: list[str]) -> None:
    global _cached
    from .. import db

    black = "\n".join(blacklist)
    white = "\n".join(whitelist)
    with db.tx() as conn:
        for key, value in ((_BLACKLIST_KEY, black), (_WHITELIST_KEY, white)):
            conn.execute(
                "INSERT INTO schema_meta(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )
    with _lock:
        _cached = None


def domain_lists_text() -> tuple[str, str]:
    black, white = domain_lists()
    return "\n".join(black), "\n".join(white)


def lists_json() -> str:
    black, white = domain_lists()
    return json.dumps({"blacklist": black, "whitelist": white})
