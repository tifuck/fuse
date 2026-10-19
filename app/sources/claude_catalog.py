"""Claude model identity and catalog state.

Fuse ids are `claude/{slug}`; rows come from the published Claude Code
catalog (`surfaces.cc.model_selector_config`). This module owns the id
translation, the per-model metadata (window, thinking efforts) and the live
catalog state (`_models_cache`, `_catalog_meta`).
"""
from __future__ import annotations

import time

CLAUDE_MODELS = [
    "claude/claude-opus-5-5",
    "claude/claude-sonnet-5",
    "claude/claude-fable-5-1",
    "claude/claude-haiku-4-5-20251001",
]

CLAUDE_MODEL_META = {
    "claude-opus-5-5": {"name": "Opus 5.5", "window": 1_000_000, "max_output": 128_000, "efforts": ("low", "medium", "high", "xhigh", "max"), "default_effort": "medium"},
    "claude-sonnet-5": {"name": "Sonnet 5", "window": 1_000_000, "max_output": 128_000, "efforts": ("low", "medium", "high", "xhigh", "max"), "default_effort": "high"},
    "claude-fable-5-1": {"name": "Fable 5.1", "window": 1_000_000, "max_output": 128_000, "efforts": ("low", "medium", "high", "xhigh", "max"), "default_effort": "high"},
    "claude-haiku-4-5-20251001": {"name": "Haiku 4.5", "window": 200_000, "max_output": 64_000, "efforts": (), "default_effort": ""},
}
_catalog_meta: dict[str, dict] = {}
_models_cache: tuple[float, list[str]] | None = None


def is_claude_model(model: str) -> bool:
    mid = (model or "").strip()
    if not mid.startswith("claude/"):
        return False
    slug = mid[7:]
    if not slug or slug.startswith("claude/"):
        return False
    return True


def upstream_id(model: str) -> str:
    mid = (model or "").strip()
    if mid.startswith("claude/"):
        return mid[7:]
    return mid


def fuse_id(model: str) -> str:
    mid = (model or "").strip()
    if not mid:
        return ""
    if mid.startswith("claude/"):
        return mid
    return "claude/" + mid


def _meta_for(model: str) -> dict:
    slug = upstream_id(model)
    return _catalog_meta.get(slug) or CLAUDE_MODEL_META.get(slug) or {}


def model_label(model: str) -> str:
    return str(_meta_for(model).get("name") or "")


def model_window(model: str) -> int | None:
    raw = _meta_for(model).get("window")
    try:
        value = int(raw or 0)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def efforts_for(model: str | None = None) -> tuple[str, ...]:
    efforts = _meta_for(model or "").get("efforts")
    if efforts is None:
        return ("low", "medium", "high", "xhigh", "max")
    return tuple(efforts)


def default_effort(model: str | None = None) -> str:
    return str(_meta_for(model or "").get("default_effort") or "")


def parse_catalog_payload(data: dict) -> list[dict]:
    """First-party models from the published Claude Code catalog."""
    surfaces = data.get("surfaces") if isinstance(data, dict) else None
    cc = surfaces.get("cc") if isinstance(surfaces, dict) else None
    configs = cc.get("model_selector_config") if isinstance(cc, dict) else None
    rows = []
    seen = set()
    for cfg in configs or []:
        if not isinstance(cfg, dict):
            continue
        for model in cfg.get("models") or []:
            if not isinstance(model, dict):
                continue
            offered = model.get("offered_on")
            if isinstance(offered, list) and offered and "first_party" not in offered:
                continue
            mid = str(model.get("id") or "").strip()
            if not mid or mid in seen or not mid.startswith("claude-"):
                continue
            seen.add(mid)
            runtime = model.get("runtime") if isinstance(model.get("runtime"), dict) else {}
            thinking = model.get("thinking") if isinstance(model.get("thinking"), dict) else {}
            efforts = runtime.get("effort_levels")
            if not isinstance(efforts, list):
                options = thinking.get("effort_options") if isinstance(thinking.get("effort_options"), list) else []
                efforts = [str(opt.get("id")) for opt in options if isinstance(opt, dict) and opt.get("id")]
            efforts = tuple(str(item) for item in efforts if isinstance(item, str) and item)
            try:
                window = min(int(runtime.get("max_input_tokens") or 0), 1_000_000)
            except (TypeError, ValueError):
                window = 0
            try:
                max_output = int(runtime.get("max_output_tokens") or 0)
            except (TypeError, ValueError):
                max_output = 0
            rows.append(
                {
                    "id": mid,
                    "name": str(model.get("name") or mid),
                    "window": window or 200_000,
                    "max_output": max_output,
                    "efforts": efforts,
                    "default_effort": str(runtime.get("default_effort") or ""),
                    "thinking_type": str(thinking.get("type") or ""),
                }
            )
    return rows


def cached_models(ttl: float) -> list[str] | None:
    """Cached catalog names when younger than `ttl` seconds; None when a fetch is due."""
    if _models_cache and time.time() - _models_cache[0] < ttl and _models_cache[1]:
        return list(_models_cache[1])
    return None


def _remember_models(rows: list[dict]) -> list[str]:
    global _models_cache, _catalog_meta
    names = []
    meta = {}
    for row in rows:
        fid = fuse_id(str(row.get("id") or ""))
        if not fid or not is_claude_model(fid) or fid in names:
            continue
        names.append(fid)
        meta[upstream_id(fid)] = row
    if not names:
        if _models_cache and _models_cache[1]:
            # A failed or empty fetch must not drop models the picker already
            # showed; keep the prior list (and its age, so the next call
            # refetches) instead of resetting to the seed list.
            return list(_models_cache[1])
        names = list(CLAUDE_MODELS)
        meta = {upstream_id(mid): {"id": upstream_id(mid), **CLAUDE_MODEL_META[upstream_id(mid)]} for mid in names}
    else:
        # Catalogs only grow: a partial fetch keeps earlier-discovered models
        # (and their metadata) after the fresh entries, like the other pools.
        prior_meta = dict(_catalog_meta or {})
        for earlier in (_models_cache[1] if _models_cache else []):
            if earlier not in names:
                names.append(earlier)
                slug = upstream_id(earlier)
                if slug in prior_meta:
                    meta.setdefault(slug, prior_meta[slug])
        for seeded in CLAUDE_MODELS:
            if seeded not in names:
                names.append(seeded)
            slug = upstream_id(seeded)
            meta.setdefault(slug, {"id": slug, **CLAUDE_MODEL_META.get(slug, {})})
    _catalog_meta = meta
    _models_cache = (time.time(), names)
    return names
