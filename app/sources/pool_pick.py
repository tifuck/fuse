"""Shared pool-account selection for Grok, ChatGPT, Z.AI, and Kimi."""

from __future__ import annotations

import threading
import time
import math

from .. import accounts, db

# Leave a little headroom so a long request doesn't start on a nearly spent plan
# when another account is still under this. A pool whose every candidate is at
# or above this still serves; otherwise the only 98% Z.AI key 503s.
USAGE_BUSY = 98.0

# Per-account concurrent request ceiling. Concurrent first turns otherwise all
# pick the same lowest-usage account before any usage lands, spiking one plan.
MAX_IN_FLIGHT = 8

_failure_lock = threading.Lock()
_cooldown_until: dict[int, float] = {}
_in_flight: dict[int, int] = {}


def note_failure(cred_id: int, *, cooldown_seconds: float = 30.0) -> None:
    """Short-lived soft cooldown so a failing account isn't re-picked first."""
    if cooldown_seconds <= 0:
        return
    until = time.time() + cooldown_seconds
    with _failure_lock:
        prev = _cooldown_until.get(int(cred_id), 0.0)
        _cooldown_until[int(cred_id)] = max(prev, until)


def reset_health() -> None:
    """Clear cooldowns and in-flight reservations. Tests only."""
    with _failure_lock:
        _cooldown_until.clear()
        _in_flight.clear()


def _cooling_down(cid: int) -> bool:
    with _failure_lock:
        until = _cooldown_until.get(cid, 0.0)
    return until > time.time()


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


def _as_float(v) -> float | None:
    if v is None or isinstance(v, bool):
        return None
    try:
        value = float(v)
        return value if math.isfinite(value) else None
    except (TypeError, ValueError, OverflowError):
        return None


def chatgpt_allows_model(plan: str | None, model: str) -> bool:
    model = model or ""
    if model in CHATGPT_BLOCKED_MODELS:
        return False
    plan = (plan or "").strip().lower()
    if plan in CHATGPT_FREE_PLANS:
        return model in CHATGPT_FREE_MODELS
    return True


def chatgpt_plan_rank(plan: str | None, model: str) -> int:
    """0 = pick first, 1 = fallback.

    Free-capable models prefer Free/Go; paid is only used when no Free/Go
    account is eligible. Paid-only models never rank Free/Go (those plans
    are filtered out by chatgpt_allows_model).
    """
    if (model or "") not in CHATGPT_FREE_MODELS:
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
    for cred in creds:
        cid = int(cred["id"])
        if cid in exclude or cred["disabled"]:
            continue
        if _cooling_down(cid):
            continue
        extra = accounts.cred_extra(cred)
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
        ready.append((rank, busy, score, week_tok, cid, cred, extra))
    if not ready:
        return None
    if prefer_id is not None:
        want = int(prefer_id)
        preferred = [row for row in ready if row[4] == want]
        preferred = [row for row in preferred if in_flight(row[4]) < MAX_IN_FLIGHT] or preferred
        if preferred:
            return preferred[0][5], preferred[0][6]
    primary = [row for row in ready if not row[1]] or ready
    available = [row for row in primary if in_flight(row[4]) < MAX_IN_FLIGHT] or primary
    available.sort(key=lambda row: (row[0], row[2], row[3], row[4]))
    return available[0][5], available[0][6]
