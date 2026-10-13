"""Shared pool-account selection for Grok, ChatGPT, Z.AI, and Kimi."""

from __future__ import annotations

import logging
import threading
import time
import math

from .. import accounts, db
from ..crypto import DecryptionError

logger = logging.getLogger(__name__)
_unreadable_logged: set[int] = set()

# Leave a little headroom so a long request doesn't start on a nearly spent plan
# when another account is still under this. A pool whose every candidate is at
# or above this still serves; otherwise the only 98% Z.AI key 503s.
USAGE_BUSY = 98.0

# Per-account concurrent request ceiling. Concurrent first turns otherwise all
# pick the same lowest-usage account before any usage lands, spiking one plan.
MAX_IN_FLIGHT = 8

_failure_lock = threading.Lock()
# (until, affinity_safe): an affinity_safe cooldown (dead-stream blip,
# provider outage) may be bypassed to keep a pinned conversation on the
# account that holds its warm cache; a hard one (auth, quota, 429) must
# never be bypassed by affinity.
_cooldown_until: dict[int, tuple[float, bool]] = {}
_in_flight: dict[int, int] = {}


def note_failure(cred_id: int, *, cooldown_seconds: float = 30.0, affinity_safe: bool = False) -> None:
    """Short-lived cooldown so a failing account isn't re-picked first.

    Hard (default) cooldowns also displace a session's pinned affinity;
    affinity_safe ones only deprioritize the account for unpinned picks.
    """
    if cooldown_seconds <= 0:
        return
    until = time.time() + cooldown_seconds
    with _failure_lock:
        prev_until, prev_safe = _cooldown_until.get(int(cred_id), (0.0, True))
        if prev_until <= time.time():
            prev_safe = True
        _cooldown_until[int(cred_id)] = (max(prev_until, until), prev_safe and affinity_safe)


# A 401 disables a credential only when it repeats within this window, after
# the first one cooled the account for AUTH_RECHECK_SECONDS.
AUTH_CONFIRM_WINDOW = 3600.0
AUTH_RECHECK_SECONDS = 120.0
_auth_strikes: dict[int, float] = {}


def auth_failure_confirmed(cred_id: int) -> bool:
    """True when a 401 should permanently disable the credential.

    One 401 can be a transient upstream auth fault; disabling on it removed
    donated credentials for good. The first strike only cools the account.
    """
    cid = int(cred_id)
    now = time.time()
    with _failure_lock:
        first = _auth_strikes.get(cid)
        if first is not None and now - first <= AUTH_CONFIRM_WINDOW:
            _auth_strikes.pop(cid, None)
            return True
        _auth_strikes[cid] = now
    note_failure(cid, cooldown_seconds=AUTH_RECHECK_SECONDS)
    return False


# A request that draws a 5xx from this many accounts is treated as the cause.
FAILOVER_SERVER_ERRORS = 2


class Failover:
    """Per-request 5xx bookkeeping for pool failover.

    A 5xx says nothing about whether the account or the request is at fault.
    Cooling each account as it fails let one poisonous request walk (and
    bill) every account and leave the pool empty for everyone. Cooldowns are
    deferred until another account serves the same request, and failover
    stops once the request has failed on FAILOVER_SERVER_ERRORS accounts.
    A 504 (read timeout) never fails over: upstream may already have run it.
    """

    def __init__(self) -> None:
        self._suspect: list[tuple[int, float]] = []

    def server_error(self, cid: int, status: int, cooldown_seconds: float) -> bool:
        """Record a 5xx; True when another account may be tried."""
        if status == 504:
            return False
        self._suspect.append((int(cid), cooldown_seconds))
        return len(self._suspect) < FAILOVER_SERVER_ERRORS

    def served(self) -> None:
        """Another account served the request: the earlier 5xx were the accounts'."""
        for cid, seconds in self._suspect:
            # A provider-side 5xx says nothing about the account's cache;
            # keep pinned conversations sticky through the cooldown.
            note_failure(cid, cooldown_seconds=seconds, affinity_safe=True)
        self._suspect.clear()


def reset_health() -> None:
    """Clear cooldowns and in-flight reservations. Tests only."""
    with _failure_lock:
        _cooldown_until.clear()
        _in_flight.clear()
        _auth_strikes.clear()


def _cooling_down(cid: int) -> bool:
    with _failure_lock:
        until = _cooldown_until.get(cid, (0.0, True))[0]
    return until > time.time()


def cooling_down(cid: int) -> bool:
    """Whether a credential row is in a failure cooldown."""
    return _cooling_down(int(cid))


def _hard_cooldown(cid: int) -> bool:
    with _failure_lock:
        until, safe = _cooldown_until.get(cid, (0.0, True))
    return until > time.time() and not safe


def reserve(cid: int) -> None:
    with _failure_lock:
        _in_flight[cid] = _in_flight.get(cid, 0) + 1


def release(cid: int) -> None:
    with _failure_lock:
        left = _in_flight.get(cid, 0) - 1
        if left > 0:
            _in_flight[cid] = left
        else:
            _in_flight.pop(cid, None)


def in_flight(cid: int) -> int:
    with _failure_lock:
        return _in_flight.get(cid, 0)

# Codex model docs: Free and Go get GPT-6 Luna; Plus and above get GPT-6 Sol.
# Go is treated like Free. Astra stays paid-only.
CHATGPT_FREE_PLANS = frozenset({"free", "go"})
CHATGPT_FREE_MODELS = frozenset(
    {
        "gpt-6-luna",
        "gpt-5.5",
        "gpt-5.4-mini",
        "gpt-5.6-terra",
        "gpt-5.6-luna",
    }
)
# Observed unsupported on both Free and Plus ChatGPT OAuth.
CHATGPT_BLOCKED_MODELS = frozenset({"gpt-5.3-codex-spark"})
# Slugs a Free/Go account's live Codex catalog lists, so a new free model
# routes to Free/Go without a code change.
_live_free_models: frozenset[str] = frozenset()


def note_free_models(names) -> None:
    global _live_free_models
    _live_free_models = _live_free_models | frozenset(names)


def chatgpt_free_model(model: str) -> bool:
    return model in CHATGPT_FREE_MODELS or model in _live_free_models


def _as_float(v) -> float | None:
    if v is None or isinstance(v, bool):
        return None
    try:
        value = float(v)
        return value if math.isfinite(value) else None
    except (TypeError, ValueError, OverflowError):
        return None


def chatgpt_allows_model(plan: str | None, model: str) -> bool:
    from .chatgpt import base_slug

    model = base_slug(model or "")
    if model in CHATGPT_BLOCKED_MODELS:
        return False
    plan = (plan or "").strip().lower()
    if plan in CHATGPT_FREE_PLANS:
        return chatgpt_free_model(model)
    return True


def chatgpt_plan_rank(plan: str | None, model: str) -> int:
    """0 = pick first, 1 = fallback.

    Free-capable models prefer Free/Go; paid is only used when no Free/Go
    account is eligible. Paid-only models never rank Free/Go (those plans
    are filtered out by chatgpt_allows_model).
    """
    from .chatgpt import base_slug

    if not chatgpt_free_model(base_slug(model or "")):
        return 0
    return 0 if (plan or "").strip().lower() in CHATGPT_FREE_PLANS else 1


def chatgpt_plan_type(extra: dict | None) -> str:
    extra = extra if isinstance(extra, dict) else {}
    billing = extra.get("billing") if isinstance(extra.get("billing"), dict) else {}
    return str(billing.get("plan_type") or "").strip().lower()


def chatgpt_usage_percent(extra: dict | None) -> float | None:
    extra = extra if isinstance(extra, dict) else {}
    billing = extra.get("billing") if isinstance(extra.get("billing"), dict) else {}
    plan = str(billing.get("plan_type") or "").strip().lower()
    pcts: list[float] = []
    for win in billing.get("windows") or []:
        if not isinstance(win, dict):
            continue
        pct = _as_float(win.get("percent", win.get("usage_percent")))
        if pct is None:
            continue
        try:
            secs = int(win.get("seconds") or win.get("window_seconds") or 0)
        except (TypeError, ValueError):
            secs = 0
        # Free only has a monthly window; ignore a short window if one appears.
        if plan == "free" and secs and secs <= 8 * 86400:
            continue
        pcts.append(pct)
    if pcts:
        return max(pcts)
    return _as_float(billing.get("credit_usage_percent"))


def zai_usage_percent(extra: dict | None) -> float | None:
    extra = extra if isinstance(extra, dict) else {}
    billing = extra.get("billing") if isinstance(extra.get("billing"), dict) else {}
    pcts: list[float] = []
    for win in billing.get("windows") or []:
        if not isinstance(win, dict):
            continue
        pct = _as_float(win.get("percent", win.get("usage_percent")))
        if pct is not None:
            pcts.append(pct)
    if pcts:
        return max(pcts)
    return _as_float(billing.get("credit_usage_percent"))


def mimo_usage_percent(extra: dict | None) -> float | None:
    extra = extra if isinstance(extra, dict) else {}
    billing = extra.get("billing") if isinstance(extra.get("billing"), dict) else {}
    pcts: list[float] = []
    for win in billing.get("windows") or []:
        if not isinstance(win, dict):
            continue
        pct = _as_float(win.get("percent", win.get("usage_percent")))
        if pct is not None:
            pcts.append(pct)
    if pcts:
        return max(pcts)
    return _as_float(billing.get("credit_usage_percent"))


def kimi_usage_percent(extra: dict | None) -> float | None:
    extra = extra if isinstance(extra, dict) else {}
    billing = extra.get("billing") if isinstance(extra.get("billing"), dict) else {}
    pcts: list[float] = []
    for win in billing.get("windows") or []:
        if not isinstance(win, dict):
            continue
        pct = _as_float(win.get("percent", win.get("usage_percent")))
        if pct is not None:
            pcts.append(pct)
    if pcts:
        return max(pcts)
    return _as_float(billing.get("credit_usage_percent"))


def grok_usage_percent(row, extra: dict | None) -> float | None:
    extra = extra if isinstance(extra, dict) else {}
    billing = extra.get("billing") if isinstance(extra.get("billing"), dict) else {}
    pcts = [_as_float(billing.get("credit_usage_percent"))]
    for product in billing.get("products") or []:
        if isinstance(product, dict):
            pcts.append(_as_float(product.get("usage_percent")))
    pcts = [max(0.0, min(100.0, pct)) for pct in pcts if pct is not None]
    if pcts:
        return max(pcts)
    # A successful current snapshot with no percentage is unknown. Do not pair
    # a prior period's saved percentage with the current period/reset caption.
    if billing.get("fetched_at"):
        return None
    try:
        return _as_float(row["last_usage_percent"])
    except Exception:
        return None


def pick_account(
    creds,
    *,
    kind: str,
    model: str = "",
    exclude: set[int] | None = None,
    is_held=None,
    is_authed=None,
    prefer_id: int | None = None,
):
    """Lowest usage first. Skip plans at USAGE_BUSY or above when another is under.

    kind is 'grok', 'chatgpt', 'zai', 'devin', 'kimi', 'mimo', or 'claude'. Returns (cred, extra)
    or None.

    ChatGPT: models Free/Go can serve (luna, terra, 5.5, 5.4-mini) pick those
    plans first, then paid. Paid-only models (sol, 5.4) never consider Free/Go.

    prefer_id, when still eligible, wins over plan tier and usage. That keeps
    a conversation on the account that already holds its prompt-cache prefix.

    An account at the in-flight ceiling is skipped unless every candidate is
    saturated, so load spreads instead of dog-piling the freshest account.

    Missing usage percent scores as 0, not week_tokens. Mixing those units
    buried a live SuperGrok plan with no x.ai % behind accounts at 69%.
    week_tokens is only the tiebreaker among equal scores.
    """
    exclude = exclude or set()
    week = db.iso_week_start()
    ready = []
    pinned = []
    for cred in creds:
        cid = int(cred["id"])
        if cid in exclude or cred["disabled"]:
            continue
        # A short blip (dead stream, provider 5xx) on the pinned account must
        # not move the conversation to a cold cache, so those cooldowns are
        # bypassable for the pin. A hard cooldown (auth, quota, 429) always
        # wins: affinity never overrides the pool's health signals.
        cooling = _cooling_down(cid)
        if cooling and (cid != prefer_id or _hard_cooldown(cid)):
            continue
        try:
            extra = accounts.cred_extra(cred)
        except (DecryptionError, accounts.CredentialDataError):
            # One unreadable row must not fail every request for the pool.
            if cid not in _unreadable_logged:
                _unreadable_logged.add(cid)
                logger.error("Pool credential %s cannot be decrypted; skipping it", cid)
            continue
        if is_held is not None and is_held(cred):
            continue
        if is_authed is not None and not is_authed(cred, extra):
            continue
        rank = 0
        if kind == "chatgpt":
            plan = chatgpt_plan_type(extra)
            if model and not chatgpt_allows_model(plan, model):
                continue
            pct = chatgpt_usage_percent(extra)
            rank = chatgpt_plan_rank(plan, model)
        elif kind == "zai":
            pct = zai_usage_percent(extra)
        elif kind == "kimi":
            pct = kimi_usage_percent(extra)
        elif kind == "mimo":
            pct = mimo_usage_percent(extra)
        elif kind == "claude":
            pct = kimi_usage_percent(extra)
        else:
            pct = grok_usage_percent(cred, extra)
        busy = pct is not None and pct >= USAGE_BUSY
        week_tok = int(cred["week_tokens"] or 0) if cred["week_start"] == week else 0
        score = pct if pct is not None else 0.0
        row = (rank, busy, score, week_tok, cid, cred, extra)
        if cooling:
            pinned.append(row)
        else:
            ready.append(row)
    if prefer_id is not None:
        want = int(prefer_id)
        preferred = [row for row in ready if row[4] == want] or pinned
        preferred = [row for row in preferred if in_flight(row[4]) < MAX_IN_FLIGHT] or preferred
        if preferred:
            return preferred[0][5], preferred[0][6]
    if not ready:
        return None
    primary = [row for row in ready if not row[1]] or ready
    available = [row for row in primary if in_flight(row[4]) < MAX_IN_FLIGHT] or primary
    available.sort(key=lambda row: (row[0], row[2], row[3], row[4]))
    return available[0][5], available[0][6]
