"""Image input per resolved (provider, model), and the outgoing-request filter.

True / False come from the provider's own catalog when it reports them
(OpenRouter, Codex, Kimi, llama.cpp /props), else from the rules below.
None means unknown: images are sent. False strips them only from the outgoing
copy, so saved chats keep every image and a later switch back to an image
model sees them again. Fuse sets no image budget of its own; images leave a
request only when the provider itself refuses it (see `shed`).
"""
from __future__ import annotations

import hashlib
import json
import re

OMITTED = "[Image omitted: this model cannot view images. It stays in the chat for image-capable models.]"
LIMIT_OMITTED = ("[Image omitted: the provider refused a request carrying this many images. "
                 "Newer images were kept. Pixels remain in saved history.]")
# Provider refusals that name images or the request size. Dropping the oldest
# images is the only change that can make such a request acceptable.
_IMAGE_REFUSAL = re.compile(
    r"too many (images|image|media|attachments)|images?\b.{0,80}\b(exceed|limit|maximum|max\b|too (large|big|many))"
    r"|(exceed|limit|maximum|max\b).{0,80}\bimages?\b|request.{0,20}too large|request_too_large"
    r"|payload too large|entity too large",
    re.I | re.S,
)

_seen: dict[tuple[str, str], bool] = {}

# First match wins. Sources: Anthropic, xAI and Codex model docs; Z.AI marks
# GLM text models text-only and ships vision as `glm-…v…`; Devin carries
# images natively to its Claude/GPT SKUs.
_RULES = (
    ("claude", re.compile(r"^claude-"), True),
    ("grok", re.compile(r"^grok-4"), True),
    ("zai", re.compile(r"^glm-[\d.]+v"), True),
    ("zai", re.compile(r"^glm-"), False),
    ("chatgpt", re.compile(r"^gpt-"), True),
    ("devin", re.compile(r"^(claude|gpt)-"), True),
    ("kimi", re.compile(r"^k3"), True),
    ("mimo", re.compile(r"^mimo-v2\.5$"), True),
)


def remember(type_id: str, model: str, value) -> None:
    if isinstance(value, bool) and model:
        _seen[(type_id, model)] = value


def _bare(type_id: str, model: str) -> str:
    mid = (model or "").strip()
    prefix = {"devin": "devin/", "claude": "claude/", "cursor": "cursor/", "openrouter": "openrouter/", "explabs": "explabs/", "nanogpt": "nanogpt/"}.get(type_id)
    if prefix and mid.startswith(prefix):
        mid = mid[len(prefix):]
    return mid.replace(".", "-") if type_id == "devin" else mid


def image_input(type_id: str, model: str, *, user_id: int | None = None) -> bool | None:
    mid = _bare(type_id, model)
    if type_id == "explabs":
        from .sources.explabs import cached_catalog

        rec = ((cached_catalog(user_id) or {}).get("by_id") or {}).get(mid, {})
        return rec.get("image")
    if type_id == "nanogpt":
        from .sources.nanogpt import catalog_record

        return (catalog_record(mid) or {}).get("image")
    if type_id == "chatgpt" and mid.endswith("-fast"):
        # `{slug}-fast` is the same model at the fast service tier.
        mid = mid[:-5]
    hit = _seen.get((type_id, mid))
    if hit is not None:
        return hit
    if type_id == "openrouter":
        from .sources import openrouter

        rec = ((openrouter.cached_catalog() or {}).get("by_id") or {}).get(mid)
        return rec.get("image") if rec else None
    if type_id == "kimi":
        from .sources import kimi

        value = kimi._meta_for(mid).get("image")
        if isinstance(value, bool):
            return value
    if type_id == "cursor":
        from .sources import cursor

        return cursor.image_support(mid)
    for kind, pattern, value in _RULES:
        if kind == type_id and pattern.search(mid):
            return value
    return None


def _image_part(part) -> bool:
    return isinstance(part, dict) and part.get("type") in ("image_url", "input_image")


def prepare(messages, type_id: str, model: str, *, user_id: int | None = None, snapshot: dict | None = None) -> tuple[list, int]:
    """Outgoing copy: Responses `input_image` becomes `image_url` (what every
    adapter reads); images a text-only model cannot read, or that `shed`
    dropped after a provider refusal, become explicit notes.

    A server-owned snapshot freezes each image's admission per resolved route.
    Persist it with the original transcript; discard it at compaction. It must
    never come from a client request or be forwarded to a provider.
    """
    if not isinstance(messages, list):
        return messages, 0
    strip = image_input(type_id, model, user_id=user_id) is False
    # Admit images in transcript order so appending new evidence cannot evict
    # pixels that were already part of a cached prefix.
    decisions = {}
    if snapshot is not None:
        route = json.dumps([type_id, model], separators=(",", ":"))
        decisions = snapshot.setdefault(route, {})
    admitted = {}
    for mi in range(len(messages)):
        content = messages[mi].get("content") if isinstance(messages[mi], dict) else None
        if not isinstance(content, list):
            continue
        for pi in range(len(content)):
            part = content[pi]
            if not _image_part(part):
                continue
            digest = hashlib.sha256(json.dumps(part, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
            key = f"{mi}:{pi}:{digest}"
            decision = decisions.get(key)
            # "budget" was the retired Fuse-side image cap: re-admit it.
            if decision is None or decision == "budget":
                decision = "capability" if strip else "admit"
                decisions[key] = decision
            admitted[mi, pi] = decision
    out, omitted = [], 0
    for mi, msg in enumerate(messages):
        content = msg.get("content") if isinstance(msg, dict) else None
        if not isinstance(content, list) or not any(_image_part(p) for p in content):
            out.append(msg)
            continue
        parts = []
        for pi, part in enumerate(content):
            if not _image_part(part):
                parts.append(part)
            elif admitted[mi, pi] != "admit":
                omitted += 1
                parts.append({"type": "text", "text": OMITTED if admitted[mi, pi] == "capability" else LIMIT_OMITTED})
            elif part["type"] == "input_image":
                image = part.get("image_url")
                image = image if isinstance(image, dict) else {"url": image}
                if isinstance(part.get("detail"), str):
                    image = {**image, "detail": part["detail"]}
                parts.append({"type": "image_url", "image_url": image})
            else:
                parts.append(part)
        if all(p.get("type") == "text" for p in parts if isinstance(p, dict)):
            parts = "\n\n".join(
                part if isinstance(part, str) else str(part.get("text") or "") for part in parts
            )
        out.append({**msg, "content": parts})
    return out, omitted


def image_refusal(status: int, message: str) -> bool:
    """A provider rejected the request for its images or its size."""
    return status == 413 or (400 <= status < 500 and bool(_IMAGE_REFUSAL.search(message or "")))


def shed(snapshot: dict, type_id: str, model: str) -> int:
    """After a provider refusal, drop the older half of the images this route
    admitted. The decision is frozen in the snapshot like any other, so later
    rounds keep the same prefix. Returns how many images were dropped."""
    route = json.dumps([type_id, model], separators=(",", ":"))
    decisions = snapshot.get(route) or {}

    def order(key):
        mi, pi, _ = key.split(":", 2)
        return int(mi), int(pi)

    kept = sorted((k for k, v in decisions.items() if v == "admit"), key=order)
    drop = kept[:(len(kept) + 1) // 2]
    for key in drop:
        decisions[key] = "limit"
    return len(drop)
