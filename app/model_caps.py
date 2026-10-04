"""Image input per resolved (provider, model), and the outgoing-request filter.

True / False come from the provider's own catalog when it reports them
(OpenRouter, Codex, Kimi, llama.cpp /props), else from the rules below.
None means unknown: images are sent as before. Only False strips them, and
only from the outgoing copy, so saved chats keep every image and a later
switch back to an image model sees them again.
"""
from __future__ import annotations

import re

OMITTED = "[Image omitted: this model cannot view images. It stays in the chat for image-capable models.]"

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
    prefix = {"devin": "devin/", "claude": "claude/", "openrouter": "openrouter/"}.get(type_id)
    if prefix and mid.startswith(prefix):
        mid = mid[len(prefix):]
    return mid.replace(".", "-") if type_id == "devin" else mid


def image_input(type_id: str, model: str) -> bool | None:
    mid = _bare(type_id, model)
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
    for kind, pattern, value in _RULES:
        if kind == type_id and pattern.search(mid):
            return value
    return None


def _image_part(part) -> bool:
    return isinstance(part, dict) and part.get("type") in ("image_url", "input_image")


def prepare(messages, type_id: str, model: str) -> tuple[list, int]:
    """Outgoing copy: Responses `input_image` becomes `image_url` (what every
    adapter reads); images are replaced by a note for text-only models."""
    if not isinstance(messages, list):
        return messages, 0
    strip = image_input(type_id, model) is False
    out, omitted = [], 0
    for msg in messages:
        content = msg.get("content") if isinstance(msg, dict) else None
        if not isinstance(content, list) or not any(_image_part(p) for p in content):
            out.append(msg)
            continue
        parts = []
        for part in content:
            if not _image_part(part):
                parts.append(part)
            elif strip:
                omitted += 1
                parts.append({"type": "text", "text": OMITTED})
            elif part["type"] == "input_image":
                image = part.get("image_url")
                image = image if isinstance(image, dict) else {"url": image}
                if isinstance(part.get("detail"), str):
                    image = {**image, "detail": part["detail"]}
                parts.append({"type": "image_url", "image_url": image})
            else:
                parts.append(part)
        if all(p.get("type") == "text" for p in parts if isinstance(p, dict)):
            parts = "\n\n".join(str(p.get("text") or "") for p in parts if isinstance(p, dict))
        out.append({**msg, "content": parts})
    return out, omitted
