"""Admin and per-user policy for donated Grok, ChatGPT, Z.AI, Devin, Kimi, and Xiaomi pools.

Two admin switches per pool, stored on sources.config:

* pool_shared (default True): pick from every donated account. When False,
  each user is routed only to accounts they linked.
* open_access (default False): any registered user may use the pool without
  linking an account of their own. Only applies while sharing is in effect
  for that user.

Users may opt out of sharing (own_only) when the admin has sharing on.
That override cannot turn sharing back on when the admin has turned it off.
"""

from __future__ import annotations

import json

from .. import db

POOL_KINDS = ("grok", "chatgpt", "zai", "devin", "kimi", "mimo")
POOL_KIND_SET = frozenset(POOL_KINDS)
POOL_LABELS = {
    "grok": "x.ai",
    "chatgpt": "OpenAI",
    "zai": "Z.AI",
    "devin": "Devin",
    "kimi": "Kimi",
    "mimo": "Xiaomi",
}


def _source_cfg(kind: str) -> dict:
    sid = source_id(kind)
    row = db.fetchone("SELECT config FROM sources WHERE id=?", (sid,))
    try:
        data = json.loads((row["config"] if row else None) or "{}")
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}


def source_id(kind: str) -> int:
    if kind == "grok":
        from .grok import ensure_grok_source

        return ensure_grok_source()
    if kind == "chatgpt":
        from .chatgpt import ensure_chatgpt_source

        return ensure_chatgpt_source()
    if kind == "zai":
        from .zai import ensure_zai_source

        return ensure_zai_source()
    if kind == "devin":
        from .devin import ensure_devin_source

        return ensure_devin_source()
    if kind == "kimi":
        from .kimi import ensure_kimi_source

        return ensure_kimi_source()
    if kind == "mimo":
        from .mimo import ensure_mimo_source

        return ensure_mimo_source()
    raise ValueError(f"unknown pool kind {kind!r}")


def admin_policy(kind: str) -> dict:
    """Site-wide switches. Missing keys keep today's defaults."""
    if kind not in POOL_KIND_SET:
        raise ValueError(f"unknown pool kind {kind!r}")
    cfg = _source_cfg(kind)
    return {
        "pool_shared": cfg.get("pool_shared", True) is not False,
        "open_access": cfg.get("open_access", False) is True,
    }


def set_admin_policy(kind: str, *, pool_shared: bool, open_access: bool) -> None:
    if kind not in POOL_KIND_SET:
        raise ValueError(f"unknown pool kind {kind!r}")
    cfg = _source_cfg(kind)
    cfg["pool_shared"] = bool(pool_shared)
    cfg["open_access"] = bool(open_access)
    db.execute("UPDATE sources SET config=? WHERE id=?", (json.dumps(cfg), source_id(kind)))


def user_prefs(user_id: int) -> dict:
    row = db.fetchone("SELECT pool_prefs FROM users WHERE id=?", (int(user_id),))
    if row is None:
        return {}
    try:
        data = json.loads(row["pool_prefs"] or "{}")
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, TypeError, KeyError):
        return {}


def user_own_only(user_id: int, kind: str) -> bool:
    if kind not in POOL_KIND_SET:
        return False
    item = user_prefs(user_id).get(kind)
    if not isinstance(item, dict):
        return False
    return item.get("own_only") is True


def set_user_own_only(user_id: int, kind: str, own_only: bool) -> None:
    if kind not in POOL_KIND_SET:
        raise ValueError(f"unknown pool kind {kind!r}")
    prefs = user_prefs(user_id)
    item = prefs.get(kind)
    item = dict(item) if isinstance(item, dict) else {}
    item["own_only"] = bool(own_only)
    prefs[kind] = item
    db.execute(
        "UPDATE users SET pool_prefs=? WHERE id=?",
        (json.dumps(prefs), int(user_id)),
    )


def uses_shared_pool(user_id: int | None, kind: str) -> bool:
    """Whether this user is routed across the whole donated pool."""
    if user_id is None:
        return True
    if kind not in POOL_KIND_SET:
        return True
    if not admin_policy(kind)["pool_shared"]:
        return False
    return not user_own_only(int(user_id), kind)


def ui_state(user_id: int, kind: str) -> dict:
    admin = admin_policy(kind)
    own_only = user_own_only(user_id, kind)
    shared = bool(admin["pool_shared"])
    return {
        "kind": kind,
        "label": POOL_LABELS.get(kind, kind),
        "pool_shared": shared,
        "open_access": bool(admin["open_access"]),
        "own_only": own_only,
        "uses_shared": shared and not own_only,
        "can_override": shared,
    }


def grant_open_access(user_id: int, kind: str, pool_ready: bool) -> bool:
    if not pool_ready:
        return False
    if not uses_shared_pool(user_id, kind):
        return False
    return admin_policy(kind)["open_access"]


def with_open_access(user_id: int, kind: str, own: dict, pool_ready: bool) -> dict:
    if own.get("ok"):
        return own
    if grant_open_access(user_id, kind, pool_ready):
        return {"ok": True, "reason": "open"}
    return own


def _donated_by(row) -> int | None:
    try:
        val = row["donated_by"]
    except (KeyError, IndexError, TypeError):
        return None
    if val is None:
        return None
    try:
        return int(val)
    except (TypeError, ValueError):
        return None


def filter_credentials(rows, kind: str, user_id: int | None):
    """Keep only the donated accounts this user may be routed onto."""
    if uses_shared_pool(user_id, kind):
        return list(rows)
    uid = int(user_id)
    return [row for row in rows if _donated_by(row) == uid]


def load_credentials(source_id: int, kind: str, user_id: int | None):
    """Enabled credentials this user may be routed onto."""
    rows = db.fetchall(
        "SELECT * FROM credentials WHERE source_id=? AND disabled=0 ORDER BY id",
        (source_id,),
    )
    return filter_credentials(rows, kind, user_id)
