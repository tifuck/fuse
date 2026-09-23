from __future__ import annotations

import json

_BLACKLIST_KEY = "fetch_domain_blacklist"
_WHITELIST_KEY = "fetch_domain_whitelist"


def domain_lists() -> tuple[list[str], list[str]]:
    from .. import db
    from .ssrf import parse_domain_list

    rows = db.fetchall(
        "SELECT key, value FROM schema_meta WHERE key IN (?, ?)",
        (_BLACKLIST_KEY, _WHITELIST_KEY),
    )
    found = {row["key"]: row["value"] for row in rows}
    return parse_domain_list(found.get(_BLACKLIST_KEY, "")), parse_domain_list(found.get(_WHITELIST_KEY, ""))


def save_domain_lists(blacklist: list[str], whitelist: list[str]) -> None:
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


def domain_lists_text() -> tuple[str, str]:
    black, white = domain_lists()
    return "\n".join(black), "\n".join(white)


def lists_json() -> str:
    black, white = domain_lists()
    return json.dumps({"blacklist": black, "whitelist": white})
