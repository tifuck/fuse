from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path

from ..config import ROOT

logger = logging.getLogger(__name__)

ACCESS_TTL = 12 * 3600
FREE = "free"
GATED = "gated"
UNKNOWN = "unknown"

GATED_MARKERS = (
    "requires a subscription",
    "this model requires",
    "upgrade for access",
    "upgrade for higher",
    "add extra usage",
)
WEEKLY_MARKERS = (
    "weekly limit",
    "weekly quota",
    "weekly usage",
    "per week",
    "per-week",
    "week limit",
    "reached your weekly",
    "out of usage",
    "usage has been exhausted",
)
SESSION_MARKERS = (
    "session usage",
    "session limit",
    "session quota",
    "5 hour",
    "5-hour",
    "five hour",
)
HOURLY_MARKERS = ("hourly limit", "hourly quota", "per hour", "per-hour", "hour limit")
ACCOUNT_MARKERS = (
    "account is currently unavailable",
    "contact support@ollama.com",
)

KIND_LABELS = {
    "model_gated": "Paid model",
    "weekly_quota": "Out of usage",
    "session_quota": "Session limit",
    "hourly_quota": "Hourly limit",
    "rate_limit": "Rate limited",
    "auth": "Auth failed",
    "auth_401": "Auth failed",
    "auth_403": "Auth failed",
    "account_unavailable": "Account unavailable",
    "bad_request": "Bad request",
    "server": "Upstream error",
}

_lock = threading.Lock()
_cache: dict[int, dict[str, dict]] = {}


def _path(source_id: int) -> Path:
    return ROOT / "data" / f"ollama-{source_id}-access.json"


def _now() -> float:
    return time.time()


def load(source_id: int) -> dict[str, dict]:
    with _lock:
        if source_id in _cache:
            return _cache[source_id]
        path = _path(source_id)
        data: dict[str, dict] = {}
        if path.exists():
            try:
                raw = json.loads(path.read_text())
                if isinstance(raw, dict):
                    data = {str(k): v for k, v in raw.items() if isinstance(v, dict)}
            except Exception as e:
                logger.warning("Failed to load ollama access cache: %s", e)
        _cache[source_id] = data
        return data


def save(source_id: int) -> None:
    with _lock:
        data = _cache.get(source_id) or {}
        path = _path(source_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=0, sort_keys=True))
        tmp.replace(path)


def note(source_id: int, model: str, access: str, *, status: int = 0, error: str = "") -> None:
    model = (model or "").strip()
    if not model or access not in (FREE, GATED, UNKNOWN):
        return
    data = load(source_id)
    data[model] = {
        "access": access,
        "status": int(status or 0),
        "error": (error or "")[:200],
        "checked_at": _now(),
    }
    save(source_id)


def of(source_id: int, model: str) -> str | None:
    row = load(source_id).get((model or "").strip())
    if not row:
        return None
    return row.get("access")


def stale(source_id: int, model: str) -> bool:
    row = load(source_id).get((model or "").strip())
    if not row:
        return True
    try:
        return _now() - float(row.get("checked_at") or 0) > ACCESS_TTL
    except (TypeError, ValueError):
        return True


def classify_cloud_error(status: int, body_text: str) -> str:
    """Map an Ollama Cloud HTTP status + body to a stable kind.

    402/403 “this model requires a subscription or extra usage” is a *paid
    model*, not an exhausted free-tier key. Weekly/session quota lives on 429.
    """
    lower = (body_text or "")[:500].lower()
    gated = any(m in lower for m in GATED_MARKERS)
    if status in (402, 403) and gated:
        return "model_gated"
    # Ollama account-level flag ("Your account is currently unavailable").
    # Arrives as 403 today but is not a credential problem — match by body on
    # any status so a status-code change upstream can't regress the handling.
    if any(m in lower for m in ACCOUNT_MARKERS):
        return "account_unavailable"
    if status == 402:
        return "model_gated"
    if status == 400:
        return "bad_request"
    if status == 429:
        if any(m in lower for m in WEEKLY_MARKERS):
            return "weekly_quota"
        if any(m in lower for m in SESSION_MARKERS):
            return "session_quota"
        if any(m in lower for m in HOURLY_MARKERS):
            return "hourly_quota"
        if "usage limit" in lower or "quota" in lower:
            return "weekly_quota"
        return "rate_limit"
    if status in (401, 403):
        return "auth"
    if 500 <= status <= 599:
        return "server"
    return "other"


def is_gated_error(status: int, body_text: str) -> bool:
    return classify_cloud_error(status, body_text) == "model_gated"


def is_account_unavailable_error(status: int, body_text: str) -> bool:
    return classify_cloud_error(status, body_text) == "account_unavailable"


def is_credential_auth_error(status: int, body_text: str) -> bool:
    if status == 401:
        return True
    if status != 403:
        return False
    lower = (body_text or "")[:500].lower()
    return any(marker in lower for marker in ("invalid token", "invalid api key", "unauthorized", "token expired"))


def label_for(kind: str | None) -> str:
    if not kind:
        return ""
    if kind in KIND_LABELS:
        return KIND_LABELS[kind]
    if kind.startswith("auth_"):
        return "Auth failed"
    if kind.startswith("server_"):
        return "Upstream error"
    if kind.startswith("http_"):
        return "HTTP error"
    return kind.replace("_", " ")


def catalog_summary(source_id: int) -> dict:
    data = load(source_id)
    free, gated = [], []
    for name, row in sorted(data.items()):
        access = row.get("access")
        if access == FREE:
            free.append(name)
        elif access == GATED:
            gated.append(name)
    return {"free": free, "gated": gated}


def filter_free(source_id: int, names: list[str]) -> list[str]:
    """Keep listed models that unpaid keys can actually call.

    Untested names are omitted until a probe or a live 200 records them as free.
    """
    out = []
    for name in names:
        if of(source_id, name) == FREE:
            out.append(name)
    return out


def pending(source_id: int, names: list[str]) -> list[str]:
    return [n for n in names if of(source_id, n) is None or stale(source_id, n)]
