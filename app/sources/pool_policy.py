"""Admin and per-user policy for donated Grok, ChatGPT, Z.AI, Devin, Kimi, Xiaomi, and Claude pools.

Two admin switches per pool, stored on sources.config:

* pool_shared (default True): pick from every donated account. When False,
  each user is routed only to accounts they linked.
* open_access (default False): any registered user may use the pool without
  linking an account of their own. Only applies while sharing is in effect
  for that user.

Users may opt out of sharing (own_only) when the admin has sharing on.
That override cannot turn sharing back on when the admin has turned it off.
Opting out is two-way: the user is routed only to accounts they linked, and
accounts they donated are withheld from every other user.
"""

from __future__ import annotations

import json
import time

from .. import db

POOL_KINDS = ("grok", "chatgpt", "zai", "devin", "kimi", "mimo", "claude")
POOL_KIND_SET = frozenset(POOL_KINDS)
POOL_LABELS = {
    "grok": "x.ai",
    "chatgpt": "OpenAI",
    "zai": "Z.AI",
    "devin": "Devin",
    "kimi": "Kimi",
    "mimo": "Xiaomi",
    "claude": "Claude",
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
    if kind == "claude":
        from .claude import ensure_claude_source

        return ensure_claude_source()
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
    sid = source_id(kind)
    # Read-modify-write inside one transaction: concurrent admin saves must
    # not silently drop each other's switch.
    with db.tx() as conn:
        row = conn.execute("SELECT config FROM sources WHERE id=? FOR UPDATE", (sid,)).fetchone()
        try:
            data = json.loads((row["config"] if row else None) or "{}")
            cfg = data if isinstance(data, dict) else {}
        except (json.JSONDecodeError, TypeError):
            cfg = {}
        cfg["pool_shared"] = bool(pool_shared)
        cfg["open_access"] = bool(open_access)
        conn.execute("UPDATE sources SET config=? WHERE id=?", (json.dumps(cfg), sid))


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
    uid = int(user_id)
    with db.tx() as conn:
        row = conn.execute("SELECT pool_prefs FROM users WHERE id=? FOR UPDATE", (uid,)).fetchone()
        try:
            data = json.loads((row["pool_prefs"] if row else None) or "{}")
            prefs = data if isinstance(data, dict) else {}
        except (json.JSONDecodeError, TypeError):
            prefs = {}
        item = prefs.get(kind)
        item = dict(item) if isinstance(item, dict) else {}
        item["own_only"] = bool(own_only)
        prefs[kind] = item
        conn.execute("UPDATE users SET pool_prefs=? WHERE id=?", (json.dumps(prefs), uid))
    _private_cache.pop(kind, None)


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


_PRIVATE_TTL = 30.0
_private_cache: dict[str, tuple[float, set[int]]] = {}


def _private_donors(kind: str) -> set[int]:
    """Donors who opted out. Their accounts stay off the shared pool.

    Cached briefly: this used to parse every users.pool_prefs row on every
    pooled request. Writes through set_user_own_only invalidate the entry.
    """
    if kind not in POOL_KIND_SET:
        return set()
    hit = _private_cache.get(kind)
    now = time.time()
    if hit and now - hit[0] < _PRIVATE_TTL:
        return set(hit[1])
    out: set[int] = set()
    for row in db.fetchall("SELECT id, pool_prefs FROM users"):
        try:
            data = json.loads(row["pool_prefs"] or "{}")
        except (json.JSONDecodeError, TypeError, KeyError):
            continue
        if not isinstance(data, dict):
            continue
        item = data.get(kind)
        if isinstance(item, dict) and item.get("own_only") is True:
            try:
                out.add(int(row["id"]))
            except (TypeError, ValueError):
                continue
    _private_cache[kind] = (now, out)
    return out


def filter_credentials(rows, kind: str, user_id: int | None):
    """Keep only the donated accounts this user may be routed onto.

    A donor who opted out is never routed onto anyone else's account, and
    nobody else — including an anonymous caller — is routed onto theirs.
    """
    private = _private_donors(kind)
    if user_id is None:
        if not private:
            return list(rows)
        return [row for row in rows if _donated_by(row) not in private]
    uid = int(user_id)
    if uses_shared_pool(uid, kind):
        return [row for row in rows if _donated_by(row) not in private or _donated_by(row) == uid]
    return [row for row in rows if _donated_by(row) == uid]


def load_credentials(source_id: int, kind: str, user_id: int | None):
    """Enabled credentials this user may be routed onto."""
    rows = db.fetchall(
        "SELECT * FROM credentials WHERE source_id=? AND disabled=0 ORDER BY id",
        (source_id,),
    )
    return filter_credentials(rows, kind, user_id)
