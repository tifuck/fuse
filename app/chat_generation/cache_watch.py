"""Notice when a request's prompt should have been cached and was not.

After each model round the runtime records a probe of the request: when it
was sent (cache lifetimes count from the request that used the prefix, not
from the end of a long reply), its inclusive prompt size, how much the
provider served from cache, which model, provider and account served it,
and a fingerprint of what was sent (one short hash per part: the tools and
reasoning-effort header, then each message). The newest probe is kept on
the thread (compaction state "cache") so it survives across turns, undo and
edits.

A round is a cache miss only when all of these hold:
- the provider reported the cached split (cache_usage_status "known");
- an earlier request in this chat, on the same model and provider, already
  got a real cache hit (at least HIT_MIN_TOKENS: Grok reports 128 cached
  tokens on a cold prompt, which says nothing about this chat), so a backend
  that never caches stays silent, and the first turn never warns;
- that earlier request ran within the provider's cache lifetime;
- the prefix both requests share is big enough to matter, and the provider
  served less than half of it from cache.

The reason says why the cache was lost: "settings" (instructions, tools or
reasoning effort changed), "effort" (the reasoning effort changed, named in
effort_from/effort_to; Anthropic keeps a message cache per effort),
"history" (an edit or undo changed earlier messages, and that alone explains
the loss), "account" (another pool account served it), "provider" (Fuse
resent the same prefix and the provider did not reuse it), or "" when there
is no fingerprint to compare.

A miss is reported once per run, also across a resume, as a `cache` fuse
event; the chat shows it as a dismissible toast. Devin reports billing deltas
instead of an inclusive prompt, so it is never judged.
"""
from __future__ import annotations

import hashlib
import json
import re
import time

from .. import chat_images

# Cache lifetime by source type. Anthropic's 5-minute default is raised to
# 1 hour by Fuse's Claude payload (ttl "1h"), and OpenRouter gets the same
# directive for anthropic/ models. OpenAI keeps GPT-5.6 and later prompts for
# 30 minutes by default; older GPT-5 models keep them in memory for 5-10
# minutes. Everyone else documents or behaves like ~5 minutes of inactivity
# (xAI: entries can be evicted at any time); a longer guess would call
# ordinary expiry a "miss".
TTL_S = {"claude": 3600, "chatgpt": 1800}
DEFAULT_TTL_S = 300
_IN_MEMORY_GPT = re.compile(r"^gpt-5(?:\.[0-5])?(?:-|$)")
# A miss below this many uncached prompt tokens is not worth a toast.
MIN_TOKENS = 4096
# Implicit-cache backends (all but Anthropic's explicit breakpoints) route a
# small context to whichever server is free, often one without its prefix:
# in production every turn-start cold miss on Grok, ChatGPT and Z.AI shared
# under ~80k tokens, with the same prefix resent. Fuse cannot prevent those,
# so a "provider" miss below this shared size is not worth a toast.
PROVIDER_MIN_SHARED = 65_536
# Fewer cached tokens than this is not evidence that the chat was ever
# served from cache (cold Grok prompts report 128).
HIT_MIN_TOKENS = 1024
SKIP_SOURCES = frozenset({"devin"})
# Fingerprint: 6 hex characters per part, at most this many parts.
_PART = 6
MAX_PARTS = 5000
# An attached image weighs about as much as this much JSON text, not its
# base64 length (only used to estimate where a changed prefix ends).
IMAGE_WEIGHT = 4000


def ttl_for(source: str | None, model: str | None = None) -> int:
    source, model = str(source or ""), str(model or "")
    if source == "openrouter" and "anthropic/" in model:
        return 3600
    if source == "chatgpt" and _IN_MEMORY_GPT.match(model):
        return DEFAULT_TTL_S
    return TTL_S.get(source, DEFAULT_TTL_S)


def _int(value) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _weight(message, text: str) -> int:
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, list):
        return len(text)
    images = [p for p in content if isinstance(p, dict) and p.get("type") in ("image_url", "image", "input_image")]
    if not images:
        return len(text)
    return len(text) - sum(len(_json(p)) for p in images) + IMAGE_WEIGHT * len(images)


def fingerprint(messages, tools=None, effort=None) -> dict:
    """What a request sent: a short hash and a size weight per part (the
    tools/effort header first, then each message, the system prompt included)."""
    parts = [{"tools": tools or [], "effort": effort or ""}, *(messages or [])][:MAX_PARTS]
    hashes, weights = [], []
    for part in parts:
        # An image read this turn is inline; replayed later it is a stored
        # reference. The provider gets the same bytes, so hash one form.
        part = chat_images.canonical_row(part) if isinstance(part, dict) else part
        text = _json(part)
        hashes.append(hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()[:_PART])
        weights.append(_weight(part, text))
    return {"parts": "".join(hashes), "weights": weights, "effort": str(effort or "")[:20]}


def _divergence(prior_parts: str, request: dict | None, prompt: int) -> tuple[str, int]:
    """("", prompt) when one request is a prefix of the other; else what
    changed ("settings" or "history") and about how many prompt tokens the
    two requests still share."""
    parts = str((request or {}).get("parts") or "")
    if not prior_parts or not parts:
        return "", prompt
    common = min(len(prior_parts), len(parts)) // _PART
    same = 0
    while same < common and prior_parts[same * _PART:(same + 1) * _PART] == parts[same * _PART:(same + 1) * _PART]:
        same += 1
    if same == common:
        return "", prompt
    # Part 0 is the tools/effort header and part 1 the system prompt.
    if same <= 1:
        return "settings", 0
    weights = list((request or {}).get("weights") or [])
    total = sum(weights) or 1
    return "history", int(prompt * sum(weights[:same]) / total)


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
    parts = str(raw.get("parts") or "")
    changed = str(raw.get("changed") or "")
    return {
        "at": at,
        "prompt": _int(raw.get("prompt")),
        "cached": _int(raw.get("cached")),
        "hit": bool(raw.get("hit")),
        "model": str(raw.get("model") or "")[:200],
        "source": str(raw.get("source") or "")[:40],
        "cred": _int(raw.get("cred")) or None,
        "run": str(raw.get("run") or "")[:80],
        "warned": bool(raw.get("warned")),
        "parts": parts if len(parts) <= MAX_PARTS * _PART and re.fullmatch(r"[0-9a-f]*", parts) else "",
        # The reasoning effort sent ("" for the provider default); None when
        # the probe predates recording it.
        "effort": raw["effort"][:20] if isinstance(raw.get("effort"), str) else None,
        # Set on the probe being judged: what changed since the prior request,
        # and roughly how many prompt tokens the two still share.
        "changed": changed if changed in ("settings", "history") else "",
        "shared": _int(raw.get("shared")),
    }


def public(raw) -> dict | None:
    """What the chat page may see: enough to tell whether the cache is warm."""
    probe = parse(raw)
    if probe is None:
        return None
    return {"at": probe["at"], "prompt": probe["prompt"], "model": probe["model"],
            "ttl": ttl_for(probe["source"], probe["model"]), "hit": probe["hit"], "effort": probe["effort"]}


def warned(prior, run_id) -> bool:
    """Has this run already reported a miss? Survives a resume."""
    prior = parse(prior)
    return bool(prior and run_id and prior["run"] == str(run_id) and prior["warned"])


def probe(usage, *, model: str, source: str | None, cred=None, prior=None, now: float | None = None,
          run=None, request: dict | None = None) -> dict | None:
    """Record of this request, or None when the provider gave no cache split.

    `request` is fingerprint() of what was sent; `run` the run that sent it.
    """
    usage = usage if isinstance(usage, dict) else {}
    if usage.get("cache_usage_status") != "known" or source in SKIP_SOURCES:
        return None
    prompt = _int(usage.get("prompt_tokens"))
    if not prompt:
        return None
    cached = min(_int(usage.get("cached_tokens")), prompt)
    prior = parse(prior)
    same = prior is not None and prior["model"] == model and prior["source"] == (source or "")
    changed, shared = _divergence(prior["parts"] if prior else "", request, prompt)
    run = str(run or "")[:80]
    return {
        "at": time.time() if now is None else now,
        "prompt": prompt,
        "cached": cached,
        # Sticky per model/provider: has this chat ever been served from cache?
        "hit": cached >= HIT_MIN_TOKENS or bool(same and prior["hit"]),
        "model": model,
        "source": source or "",
        "cred": _int(cred) or None,
        "run": run,
        "warned": bool(run and prior and prior["run"] == run and prior["warned"]),
        "parts": str((request or {}).get("parts") or ""),
        "effort": request["effort"][:20] if isinstance((request or {}).get("effort"), str) else None,
        "changed": changed,
        "shared": shared,
    }


def miss(prior, current, *, settings_changed: bool = False) -> dict | None:
    """The `cache` event for `current`, or None when it is not a notable miss."""
    prior, current = parse(prior), parse(current)
    if prior is None or current is None or not prior["hit"]:
        return None
    if prior["model"] != current["model"] or prior["source"] != current["source"]:
        return None
    idle = current["at"] - prior["at"]
    if idle < 0 or idle > ttl_for(current["source"], current["model"]):
        return None
    shared = min(prior["prompt"], current["prompt"])
    uncached = current["prompt"] - current["cached"]
    if shared < MIN_TOKENS or uncached < MIN_TOKENS or current["cached"] * 2 >= shared:
        return None
    effort = {}
    new_effort = None not in (prior["effort"], current["effort"]) and prior["effort"] != current["effort"]
    if settings_changed or current["changed"] == "settings":
        # A refreshed system prompt is the bigger change; otherwise a
        # different effort explains a changed header on its own.
        reason = "effort" if new_effort and not settings_changed else "settings"
        if reason == "effort":
            effort = {"effort_from": prior["effort"], "effort_to": current["effort"]}
    elif current["changed"] == "history" and current["cached"] * 2 >= current["shared"]:
        # The provider served what the edit left in place; the edit cost the rest.
        reason = "history"
    elif prior["cred"] and current["cred"] and prior["cred"] != current["cred"]:
        reason = "account"
    elif prior["parts"] and current["parts"]:
        # Fuse sent the prefix the provider had cached (or kept the part an
        # edit left in place) and the provider still did not reuse it.
        reason = "provider"
    else:
        reason = ""
    explicit = current["source"] == "claude" or (current["source"] == "openrouter" and "anthropic/" in current["model"])
    if reason == "provider" and shared < PROVIDER_MIN_SHARED and not explicit:
        return None
    return {"event": "cache", "state": "miss", "prompt": current["prompt"],
            "cached": current["cached"], "idle_s": int(idle), "reason": reason, **effort}
