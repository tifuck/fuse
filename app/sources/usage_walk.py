"""Paced background refresh of paid pool usage.

One live credential at a time, oldest-checked first, with a sleep between
hops. Free ChatGPT plans are skipped. Page loads never call this.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from .. import accounts, db
from . import chatgpt as chatgpt_mod
from . import devin as devin_mod
from . import grok as grok_mod
from . import kimi as kimi_mod
from . import mimo as mimo_mod
from . import openrouter as openrouter_mod
from . import zai as zai_mod
from .pool_pick import CHATGPT_FREE_PLANS, chatgpt_plan_type

logger = logging.getLogger(__name__)

WALK_PAUSE = 20.0
WALK_KINDS = ("grok", "chatgpt", "zai", "devin", "kimi", "mimo", "openrouter")


def _parse_ts(ts: str) -> datetime | None:
    if not ts:
        return None
    raw = str(ts).strip()
    if raw.endswith(("Z", "z")):
        raw = raw[:-1]
    parsed: datetime | None = None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f"):
            try:
                parsed = datetime.strptime(raw.split("+")[0], fmt)
                break
            except ValueError:
                continue
    if parsed is None:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _checked_at(extra: dict) -> str:
    billing = extra.get("billing") if isinstance(extra.get("billing"), dict) else {}
    info = extra.get("key_info") if isinstance(extra.get("key_info"), dict) else {}
    return str(
        billing.get("checked_at")
        or billing.get("fetched_at")
        or info.get("checked_at")
        or info.get("fetched_at")
        or ""
    )


def _is_paid(kind: str, extra: dict) -> bool:
    billing = extra.get("billing") if isinstance(extra.get("billing"), dict) else {}
    if kind == "chatgpt":
        plan = chatgpt_plan_type(extra)
        if not plan:
            return True
        return plan not in CHATGPT_FREE_PLANS
    if kind == "openrouter":
        info = extra.get("key_info") if isinstance(extra.get("key_info"), dict) else {}
        return not bool(info.get("is_free_tier"))
    if kind == "grok":
        return grok_mod.billing_has_subscription(billing) or not billing.get("fetched_at")
    if kind == "zai":
        return zai_mod.billing_has_subscription(billing) or not billing.get("fetched_at")
    if kind == "kimi":
        return kimi_mod.billing_has_subscription(billing) or not billing.get("fetched_at")
    if kind == "mimo":
        return mimo_mod.billing_has_subscription(billing) or not billing.get("fetched_at")
    if kind == "devin":
        return True
    return False


def usage_targets() -> list[tuple[str, object]]:
    placeholders = ",".join("?" * len(WALK_KINDS))
    rows = db.fetchall(
        f"""SELECT c.*, s.type AS source_type
            FROM credentials c JOIN sources s ON s.id=c.source_id
            WHERE c.disabled=0 AND s.type IN ({placeholders})
            ORDER BY c.id""",
        WALK_KINDS,
    )
    targets: list[tuple[str, object, datetime]] = []
    for row in rows:
        kind = str(row["source_type"] or "")
        try:
            extra = accounts.cred_extra(row)
        except Exception:
            # One unreadable row must not stall the walk for every account.
            logger.warning("Usage walk skipped unreadable %s credential %s", kind, row["id"], exc_info=True)
            continue
        if not _is_paid(kind, extra):
            continue
        when = _parse_ts(_checked_at(extra)) or datetime.min.replace(tzinfo=timezone.utc)
        targets.append((kind, row, when))
    targets.sort(key=lambda item: (item[2], int(item[1]["id"])))
    return [(kind, row) for kind, row, _ in targets]


async def refresh_one_usage(kind: str, cred) -> None:
    if kind == "grok":
        await grok_mod.refresh_billing(cred, force=True)
    elif kind == "chatgpt":
        await chatgpt_mod.refresh_usage(cred, force=True)
    elif kind == "zai":
        await zai_mod.refresh_quota(cred, force=True)
    elif kind == "devin":
        await devin_mod.refresh_status(cred, force=True)
    elif kind == "kimi":
        await kimi_mod.refresh_quota(cred, force=True)
    elif kind == "mimo":
        await mimo_mod.refresh_quota(cred, force=True)
    elif kind == "openrouter":
        await openrouter_mod.refresh_key_info(cred, force=True)


async def walk_next() -> str | None:
    """Refresh the stalest paid account. Returns 'kind:id' or None."""
    targets = usage_targets()
    if not targets:
        return None
    kind, cred = targets[0]
    try:
        await refresh_one_usage(kind, cred)
    except Exception:
        logger.warning("Usage walk failed for %s credential %s", kind, cred["id"], exc_info=True)
        return f"{kind}:{cred['id']}"
    return f"{kind}:{cred['id']}"
