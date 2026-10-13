"""Admin and per-user policy for donated Grok, ChatGPT, Z.AI, Devin, Kimi, Xiaomi, and Claude pools.

Two admin switches per pool, stored on sources.config:

* pool_shared (default True): accounts may be shared across users. When
  False, every user is routed only onto accounts they linked and nobody's
  accounts are shared.
* open_access (default False): any registered user may use the pool without
  linking an account of their own. They reach only accounts shared with
  everyone.

Two settings per user and pool, stored on users.pool_prefs, while the admin
has sharing on:

* share: who else may be routed onto this user's accounts.
  - "everyone": anyone with access to the pool (the default).
  - "contributors": only users who share a live account in this pool too.
  - "private": nobody. A private donor also uses only their own accounts,
    so keeping accounts back never buys a seat on everyone else's.
* use: which accounts this user's requests go to.
  - "pool": their own accounts first, then the accounts shared with them
    when theirs are busy, at capacity, cooling down, or out of plan.
  - "mine": only their own accounts.

Before share/use existed the only setting was own_only, a two-way opt-out.
A row without share/use still reads that way, and every write keeps an
own_only mirror that is at least as private as the new settings, so code
rolled back to the old reader never shares more than the user chose.
"""

from __future__ import annotations

import json
import time

from .. import accounts, db
from ..crypto import DecryptionError

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

SHARE_LEVELS = ("everyone", "contributors", "private")
USE_MODES = ("pool", "mine")
DEFAULT_PREF = {"share": "everyone", "use": "pool"}


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


def own_access(kind: str, user_id: int) -> dict:
    """The provider's verdict on this user's own linked accounts alone."""
    if kind == "grok":
        from .grok import grok_own_access

        return grok_own_access(user_id)
    if kind == "chatgpt":
        from .chatgpt import chatgpt_own_access

        return chatgpt_own_access(user_id)
    if kind == "zai":
        from .zai import zai_own_access

        return zai_own_access(user_id)
    if kind == "devin":
        from .devin import devin_own_access

        return devin_own_access(user_id)
    if kind == "kimi":
        from .kimi import kimi_own_access

        return kimi_own_access(user_id)
    if kind == "mimo":
        from .mimo import mimo_own_access

        return mimo_own_access(user_id)
    if kind == "claude":
        from .claude import claude_own_access

        return claude_own_access(user_id)
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


# Pools that hold an account out for HOLD_HOURS when its plan usage rises with
# no Fuse traffic (its owner is using it elsewhere). The hold withholds the
# account from other users only; pool_pick never applies it to the owner.
GUARD_KINDS = ("grok", "zai", "kimi", "mimo", "claude")


def guard_enabled(kind: str) -> bool:
    if kind not in GUARD_KINDS:
        return False
    return _source_cfg(kind).get("offpool_usage_guard", True) is not False


def set_guard(kind: str, enabled: bool) -> None:
    """Turning a guard off releases every account it is holding."""
    if kind not in GUARD_KINDS:
        raise ValueError(f"no usage guard for {kind!r}")
    sid = source_id(kind)
    with db.tx() as conn:
        row = conn.execute("SELECT config FROM sources WHERE id=? FOR UPDATE", (sid,)).fetchone()
        try:
            data = json.loads((row["config"] if row else None) or "{}")
            cfg = data if isinstance(data, dict) else {}
        except (json.JSONDecodeError, TypeError):
            cfg = {}
        cfg["offpool_usage_guard"] = bool(enabled)
        conn.execute("UPDATE sources SET config=? WHERE id=?", (json.dumps(cfg), sid))
        if not enabled:
            conn.execute("UPDATE credentials SET pool_held_until=NULL WHERE source_id=?", (sid,))


def _parse_prefs(raw) -> dict:
    try:
        data = json.loads(raw or "{}")
    except (json.JSONDecodeError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def user_prefs(user_id: int) -> dict:
    row = db.fetchone("SELECT pool_prefs FROM users WHERE id=?", (int(user_id),))
    if row is None:
        return {}
    try:
        return _parse_prefs(row["pool_prefs"])
    except (KeyError, IndexError):
        return {}


def _normalize(item) -> dict:
    item = item if isinstance(item, dict) else {}
    legacy_private = item.get("own_only") is True
    share = item.get("share")
    if share not in SHARE_LEVELS:
        share = "private" if legacy_private else DEFAULT_PREF["share"]
    use = item.get("use")
    if use not in USE_MODES:
        use = "mine" if legacy_private else DEFAULT_PREF["use"]
    return {"share": share, "use": use}


def user_pref(user_id: int, kind: str) -> dict:
    """What the user saved for this pool, before the admin switch applies."""
    if kind not in POOL_KIND_SET:
        return dict(DEFAULT_PREF)
    return _normalize(user_prefs(user_id).get(kind))


def effective_pref(user_id: int, kind: str) -> dict:
    """The sharing and routing actually in force for this user."""
    if not admin_policy(kind)["pool_shared"]:
        return {"share": "private", "use": "mine"}
    saved = user_pref(user_id, kind)
    use = "mine" if saved["share"] == "private" else saved["use"]
    return {"share": saved["share"], "use": use}


def set_user_pref(user_id: int, kind: str, *, share: str | None = None, use: str | None = None) -> dict:
    if kind not in POOL_KIND_SET:
        raise ValueError(f"unknown pool kind {kind!r}")
    if share is not None and share not in SHARE_LEVELS:
        raise ValueError(f"unknown share level {share!r}")
    if use is not None and use not in USE_MODES:
        raise ValueError(f"unknown routing mode {use!r}")
    uid = int(user_id)
    with db.tx() as conn:
        row = conn.execute("SELECT pool_prefs FROM users WHERE id=? FOR UPDATE", (uid,)).fetchone()
        prefs = _parse_prefs(row["pool_prefs"] if row else None)
        old = prefs.get(kind)
        pref = _normalize(old)
        if share is not None:
            pref["share"] = share
        if use is not None:
            pref["use"] = use
        item = dict(old) if isinstance(old, dict) else {}
        item.update(pref)
        # Rollback mirror: the old reader treats own_only as fully private.
        item["own_only"] = pref["share"] != "everyone" or pref["use"] == "mine"
        prefs[kind] = item
        conn.execute("UPDATE users SET pool_prefs=? WHERE id=?", (json.dumps(prefs), uid))
    _share_cache.pop(kind, None)
    return pref


def uses_shared_pool(user_id: int | None, kind: str) -> bool:
    """Whether this user's requests may fall back onto other users' accounts."""
    if user_id is None:
        return True
    if kind not in POOL_KIND_SET:
        return True
    return effective_pref(int(user_id), kind)["use"] == "pool"


def contributes(user_id: int | None, kind: str) -> bool:
    """A live account of this user's is shared in this pool.

    That is what "contributors" sharing asks of a caller, and what the Z.AI
    cross-unlock asks of an x.ai or OpenAI donor. A dead or private account
    does not count.
    """
    if user_id is None or kind not in POOL_KIND_SET:
        return False
    uid = int(user_id)
    if effective_pref(uid, kind)["share"] == "private":
        return False
    return bool(own_access(kind, uid).get("ok"))


def ui_state(user_id: int, kind: str) -> dict:
    admin = admin_policy(kind)
    saved = user_pref(user_id, kind)
    eff = effective_pref(user_id, kind)
    shared = bool(admin["pool_shared"])
    return {
        "kind": kind,
        "label": POOL_LABELS.get(kind, kind),
        "pool_shared": shared,
        "open_access": bool(admin["open_access"]),
        "share": eff["share"],
        "use": eff["use"],
        "saved_share": saved["share"],
        "saved_use": saved["use"],
        "own_only": eff["use"] == "mine",
        "uses_shared": eff["use"] == "pool",
        "can_override": shared,
    }


def _live(row, live) -> bool:
    try:
        return bool(live(row))
    except (DecryptionError, accounts.CredentialDataError):
        return False


def pool_has_live(kind: str, user_id: int | None, live) -> bool:
    """Some account this user would be routed onto passes `live`."""
    rows = load_credentials(source_id(kind), kind, user_id)
    return any(_live(row, live) for row in rows)


def grant_open_access(user_id: int, kind: str, live) -> bool:
    """Open access admits a user without an account of their own, but only
    onto accounts their routing can actually reach: a pool whose only live
    accounts are private or contributor-only must not read as unlocked."""
    if not uses_shared_pool(user_id, kind):
        return False
    if not admin_policy(kind)["open_access"]:
        return False
    return pool_has_live(kind, user_id, live)


def with_open_access(user_id: int, kind: str, own: dict, live) -> dict:
    """`live(row)` says whether one credential row can serve requests."""
    if own.get("ok"):
        return own
    if grant_open_access(user_id, kind, live):
        return {"ok": True, "reason": "open"}
    return own


def donated_by(row) -> int | None:
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


_SHARE_TTL = 30.0
_share_cache: dict[str, tuple[float, dict[int, str]]] = {}


def _share_levels(kind: str) -> dict[int, str]:
    """Donors whose accounts are not shared with everyone, by level.

    Cached briefly: this would otherwise parse every users.pool_prefs row on
    every pooled request. Writes through set_user_pref invalidate the entry.
    """
    if kind not in POOL_KIND_SET:
        return {}
    hit = _share_cache.get(kind)
    now = time.time()
    if hit and now - hit[0] < _SHARE_TTL:
        return dict(hit[1])
    out: dict[int, str] = {}
    for row in db.fetchall("SELECT id, pool_prefs FROM users"):
        try:
            data = _parse_prefs(row["pool_prefs"])
            uid = int(row["id"])
        except (KeyError, IndexError, TypeError, ValueError):
            continue
        share = _normalize(data.get(kind))["share"]
        if share != "everyone":
            out[uid] = share
    _share_cache[kind] = (now, out)
    return dict(out)


def share_level(kind: str, donor_id: int | None) -> str:
    """How widely this donor's accounts are shared ("everyone" for none)."""
    if donor_id is None:
        return "everyone"
    if not admin_policy(kind)["pool_shared"]:
        return "private"
    return _share_levels(kind).get(int(donor_id), "everyone")


def filter_credentials(rows, kind: str, user_id: int | None):
    """Keep only the donated accounts this user may be routed onto.

    The caller's own accounts always stay. Everyone else's stay only when
    the caller routes through the shared pool and the donor shares with the
    caller: "everyone" admits any caller, "contributors" admits a caller who
    shares a live account here too, and "private" admits nobody. A caller
    with no user (system work) reaches only accounts shared with everyone.
    """
    rows = list(rows)
    uid = int(user_id) if user_id is not None else None
    if uid is not None and not uses_shared_pool(uid, kind):
        return [row for row in rows if donated_by(row) == uid]
    if not admin_policy(kind)["pool_shared"]:
        return []
    levels = _share_levels(kind)
    contributor: bool | None = None
    out = []
    for row in rows:
        donor = donated_by(row)
        if uid is not None and donor == uid:
            out.append(row)
            continue
        level = levels.get(donor, "everyone") if donor is not None else "everyone"
        if level == "private":
            continue
        if level == "contributors":
            if uid is None:
                continue
            if contributor is None:
                contributor = contributes(uid, kind)
            if not contributor:
                continue
        out.append(row)
    return out


def load_credentials(source_id: int, kind: str, user_id: int | None):
    """Enabled credentials this user may be routed onto."""
    rows = db.fetchall(
        "SELECT * FROM credentials WHERE source_id=? AND disabled=0 ORDER BY id",
        (source_id,),
    )
    return filter_credentials(rows, kind, user_id)
