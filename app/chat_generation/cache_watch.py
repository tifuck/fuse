"""Notice when a request's prompt should have been cached and was not.

After each model round the runtime records a probe of the request: when it
was sent (cache lifetimes count from the request that used the prefix, not
from the end of a long reply), its inclusive prompt size, how much the provider served from cache, and
which model, provider and account served it. The newest probe is kept on the
thread (compaction state "cache") so it survives across turns, undo and edits.

A round is a cache miss only when all of these hold:
- the provider reported the cached split (cache_usage_status "known");
- an earlier request in this chat, on the same model and provider, already
  got a cache hit (so a backend that never caches stays silent, and the first
  turn, which has nothing to reuse, never warns);
- that earlier request ran within the provider's cache lifetime;
- the prefix both requests share is big enough to matter, and the provider
  served less than half of it from cache.

A miss is reported once per turn as a `cache` fuse event; the chat shows it
as a dismissible toast. Devin reports billing deltas instead of an inclusive
prompt, so it is never judged.
"""
from __future__ import annotations

import time

# Cache lifetime by source type. Anthropic's 5-minute default is raised to
# 1 hour by Fuse's Claude payload (ttl "1h"). Everyone else documents or
# behaves like ~5 minutes of inactivity; a longer guess would call ordinary
# expiry a "miss".
TTL_S = {"claude": 3600}
DEFAULT_TTL_S = 300
# A miss below this many uncached prompt tokens is not worth a toast.
MIN_TOKENS = 4096
SKIP_SOURCES = frozenset({"devin"})


def ttl_for(source: str | None) -> int:
    return TTL_S.get(str(source or ""), DEFAULT_TTL_S)


def _int(value) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def parse(raw) -> dict | None:
    """The stored probe, or None when it is missing or malformed."""
    if not isinstance(raw, dict):
        return None
    try:
        at = float(raw.get("at"))
    except (TypeError, ValueError):
        return None
    if at <= 0:
        return None
    return {
        "at": at,
        "prompt": _int(raw.get("prompt")),
        "cached": _int(raw.get("cached")),
        "hit": bool(raw.get("hit")),
        "model": str(raw.get("model") or "")[:200],
        "source": str(raw.get("source") or "")[:40],
        "cred": _int(raw.get("cred")) or None,
    }


def public(raw) -> dict | None:
    """What the chat page may see: enough to tell whether the cache is warm."""
    probe = parse(raw)
    if probe is None:
        return None
    return {"at": probe["at"], "prompt": probe["prompt"], "model": probe["model"],
            "ttl": ttl_for(probe["source"]), "hit": probe["hit"]}


def probe(usage, *, model: str, source: str | None, cred=None, prior=None, now: float | None = None) -> dict | None:
    """Record of this request, or None when the provider gave no cache split."""
    usage = usage if isinstance(usage, dict) else {}
    if usage.get("cache_usage_status") != "known" or source in SKIP_SOURCES:
        return None
    prompt = _int(usage.get("prompt_tokens"))
    if not prompt:
        return None
    cached = min(_int(usage.get("cached_tokens")), prompt)
    prior = parse(prior)
    same = prior is not None and prior["model"] == model and prior["source"] == (source or "")
    return {
        "at": time.time() if now is None else now,
        "prompt": prompt,
        "cached": cached,
        # Sticky per model/provider: has this chat ever been served from cache?
        "hit": bool(cached) or bool(same and prior["hit"]),
        "model": model,
        "source": source or "",
        "cred": _int(cred) or None,
    }


def miss(prior, current, *, settings_changed: bool = False) -> dict | None:
    """The `cache` event for `current`, or None when it is not a notable miss."""
    prior, current = parse(prior), parse(current)
    if prior is None or current is None or not prior["hit"]:
        return None
    if prior["model"] != current["model"] or prior["source"] != current["source"]:
        return None
    idle = current["at"] - prior["at"]
    if idle < 0 or idle > ttl_for(current["source"]):
        return None
    shared = min(prior["prompt"], current["prompt"])
    uncached = current["prompt"] - current["cached"]
    if shared < MIN_TOKENS or uncached < MIN_TOKENS or current["cached"] * 2 >= shared:
        return None
    reason = ""
    if settings_changed:
        reason = "settings"
    elif prior["cred"] and current["cred"] and prior["cred"] != current["cred"]:
        reason = "account"
    return {"event": "cache", "state": "miss", "prompt": current["prompt"],
            "cached": current["cached"], "idle_s": int(idle), "reason": reason}
