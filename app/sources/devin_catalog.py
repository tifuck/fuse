"""Devin model identity, catalog state, and SKU health.

Cascade wire uids append effort/speed flags (`claude-opus-5-medium`); Fuse ids
collapse one family to `devin/{base}` with the thinking mode carried on
`reasoning_effort`. This module owns the id/label/effort translation, the live
catalog state (`_models_cache` and the `_model_*` family), and the per-wire-uid
runtime circuit breaker for SKUs the chat backend does not actually serve.
"""
from __future__ import annotations

import re
import threading
import time

SESSION_TOKEN_PREFIX = "devin-session-token$"
FUSE_PREFIX = "devin/"

# Fuse ids stay `devin/{base}`. Cascade wire uids append effort
# (`claude-opus-5-medium`, `gpt-6-astra-medium`). GPT SKUs listed here
# belong to the Devin pool — they must not fall through to ChatGPT.
# Seeds only backfill when the live catalog omits a still-served SKU, so
# this list must stay conservative: claude-fable-5-1, gpt-6-astra, gpt-5-5,
# and gpt-5-6-{sol,terra,luna} were retired by Cognition (absent from both
# the account's CLI model list and the Windsurf catalog as of 2026-09-15)
# and must not be seeded. If a SKU returns to the catalog it lists again.
DEVIN_MODELS = [
    "devin/swe-2",
    "devin/swe-1-6-fast",
    "devin/claude-opus-5",
    "devin/claude-sonnet-5",
    "devin/gpt-5-4",
    "devin/gpt-5-4-mini",
]

_WIRE_EFFORTS = ("none", "minimal", "low", "medium", "high", "xhigh", "max")
_LABEL_TAIL = re.compile(
    r"(?:\s+No Thinking|\s+(?:Low|Medium|High|X-?High|Max|Minimal|None)(?:\s+Thinking)?)+$",
    re.I,
)
_LABEL_EFFORT = re.compile(
    r"\b(?:No\s+Thinking|X-?High|Minimal|Low|Medium|High|Max|None)(?:\s+Thinking)?\b",
    re.I,
)
# Slider aliases: GLM lists `none` where some clients still send `low`.
_EFFORT_ALIASES = {
    "low": ("none", "minimal"),
    "none": ("low", "minimal"),
    "minimal": ("low", "none"),
}
_DEFAULT_EFFORTS = ("medium", "high", "low", "max", "xhigh", "minimal", "none")

SEEDED_EFFORTS = ("low", "medium", "high")

_models_cache: tuple[float, list[str]] | None = None
_model_labels: dict[str, str] = {}
_model_efforts: dict[str, tuple[str, ...]] = {}
_model_wire_uids: dict[str, dict[str, str]] = {}
_model_plain_uids: dict[str, str] = {}
_model_promo_until: dict[str, int] = {}

# The Windsurf catalog lists every Cascade SKU the editor may show; the chat
# backend only reliably serves a subset of them. Newly shipped or preview SKUs
# (e.g. `glm-5-2-none`) fast-fail with a Connect trailer "internal error",
# and some internal `MODEL_*` uids are version-gated ("out of date"). The
# catalog never marks these, so runtime health is tracked per wire uid: a
# matching upstream failure hides that SKU (effort) from pickers and fails
# fast with a clear message until the cooldown expires.
_SKU_INTERNAL_FAIL_S = 30 * 60.0
_SKU_VERSION_FAIL_S = 6 * 3600.0
_sku_lock = threading.Lock()
_sku_dead_until: dict[str, float] = {}
_SKU_ERROR_INTERNAL = re.compile(
    r"internal error|not (?:a )?(?:valid|available|supported)|"
    r"unavailable|unknown model|no longer available",
    re.I,
)
_SKU_ERROR_VERSION = re.compile(r"out of date|update.*version|version.*update", re.I)


def sku_error_kind(message: str) -> str:
    """Classify an upstream error as model-unavailable (''), 'internal', or 'version'."""
    text = str(message or "")
    if not text:
        return ""
    if _SKU_ERROR_VERSION.search(text):
        return "version"
    if _SKU_ERROR_INTERNAL.search(text):
        return "internal"
    return ""


def note_sku_failure(uid: str, kind: str = "internal") -> None:
    uid = (uid or "").strip()
    if not uid:
        return
    ttl = _SKU_VERSION_FAIL_S if kind == "version" else _SKU_INTERNAL_FAIL_S
    until = time.time() + ttl
    with _sku_lock:
        prev = _sku_dead_until.get(uid, 0.0)
        _sku_dead_until[uid] = max(prev, until)


def sku_dead(uid: str) -> bool:
    uid = (uid or "").strip()
    if not uid:
        return False
    with _sku_lock:
        until = _sku_dead_until.get(uid, 0.0)
    return until > time.time()


def reset_sku_health() -> None:
    with _sku_lock:
        _sku_dead_until.clear()


def _sku_unavailable_message(requested: str, effort: str | None) -> str:
    label = model_label(requested) or requested
    mode = f" ({effort} thinking)" if effort else ""
    return (
        f"{label}{mode} is temporarily unavailable on Devin. "
        "Try another thinking mode or model."
    )


def normalize_token(raw: str) -> str:
    key = (raw or "").strip()
    if not key:
        return ""
    if key.startswith(SESSION_TOKEN_PREFIX) or key.startswith("devin-"):
        return key
    return SESSION_TOKEN_PREFIX + key


def fuse_id(upstream: str) -> str:
    uid = (upstream or "").strip()
    if not uid:
        return ""
    if uid.startswith(FUSE_PREFIX):
        return uid
    return FUSE_PREFIX + uid


def upstream_id(model: str) -> str:
    mid = (model or "").strip()
    if mid.startswith(FUSE_PREFIX):
        return mid[len(FUSE_PREFIX) :]
    if mid.startswith("devin-") and not mid.startswith(SESSION_TOKEN_PREFIX):
        return mid[len("devin-") :]
    return mid


def is_devin_model(model: str) -> bool:
    """True for Fuse Devin ids (`devin/claude-opus-5`, `devin/gpt-6-astra`).

    Unprefixed ChatGPT/Grok/Z.AI ids stay on those pools. A Devin-prefixed
    GPT/Claude id must never look like OpenRouter `author/slug`.
    """
    mid = (model or "").strip()
    if not mid:
        return False
    if mid.startswith(SESSION_TOKEN_PREFIX):
        return False
    if mid.startswith(FUSE_PREFIX):
        return bool(mid[len(FUSE_PREFIX) :].strip())
    return mid.startswith("devin-")


def _split_wire_uid(uid: str) -> tuple[str, str]:
    """Strip Cascade effort/speed flags: claude-opus-5-medium-fast → (claude-opus-5, medium).

    `swe-1-6-fast` keeps `fast` — it is the SKU name, not an effort flag.
    `glm-5-2-max-1m` keeps `1m` on the family: (glm-5-2-1m, max).
    """
    parts = [p for p in (uid or "").split("-") if p]
    if not parts:
        return "", ""
    while parts and parts[-1] in ("usa", "priority"):
        parts.pop()
    family_suffix = ""
    if parts and parts[-1] == "1m":
        family_suffix = parts.pop()
    if len(parts) >= 2 and parts[-1] == "fast" and parts[-2] in _WIRE_EFFORTS:
        parts.pop()
    effort = ""
    if parts and parts[-1] in _WIRE_EFFORTS:
        effort = parts.pop()
    if family_suffix:
        parts.append(family_suffix)
    return "-".join(parts), effort


def canonical_upstream(model: str) -> str:
    base, _effort = _split_wire_uid(upstream_id(model))
    return base.replace(".", "-") if base else base


def canonical_fuse_id(model: str) -> str:
    base = canonical_upstream(model)
    return fuse_id(base) if base else ""


def _takes_effort(base: str) -> bool:
    return bool(base) and (
        base.startswith(("claude-", "gpt-", "glm-", "grok", "swe-", "hera-", "yeti"))
        or base.startswith("metis")
    )


def _normalize_effort(effort: str | None) -> str:
    if not isinstance(effort, str):
        return ""
    key = effort.strip().lower()
    return key if key in _WIRE_EFFORTS else ""


def _effort_token(label: str | None) -> str:
    """Catalog effort-dimension label → wire effort token.

    The family metadata spells efforts as they appear in the UI:
    "High", "No Thinking", "X-High", "Minimal", "None".
    """
    key = re.sub(r"\s+", " ", str(label or "")).strip().lower()
    if key == "no thinking":
        return "none"
    if key in ("x-high", "xhigh"):
        return "xhigh"
    return key if key in _WIRE_EFFORTS else ""


def _is_speed_variant(uid: str) -> bool:
    parts = [p for p in (uid or "").split("-") if p]
    if not parts:
        return False
    if parts[-1] in ("usa", "priority"):
        return True
    return len(parts) >= 2 and parts[-1] == "fast" and parts[-2] in _WIRE_EFFORTS


def _effort_from_label(name: str) -> str:
    """Read High/Max/No Thinking off an unsuffixed catalog label."""
    matches = list(_LABEL_EFFORT.finditer(name or ""))
    if not matches:
        return ""
    raw = matches[-1].group(0).lower().strip()
    if raw == "no thinking" or raw.startswith("no thinking"):
        return "none"
    token = re.sub(r"\s+thinking$", "", raw).strip()
    if token in ("x-high", "xhigh"):
        return "xhigh"
    return token if token in _WIRE_EFFORTS else ""


def _seeded_wire_uid(base: str, effort: str) -> str:
    chosen = effort
    if base.startswith("swe-") and chosen == "medium":
        chosen = "high"
    return f"{base}-{chosen}"


def resolve_wire_uid(model: str, effort: str | None = None) -> str:
    """Cascade `model_uid` for a Fuse id plus optional `reasoning_effort`.

    When the Windsurf catalog is loaded, this returns the exact SKU for that
    family's thinking mode (`deepseek-v4-1-flash-high`, unsuffixed `glm-5-2`
    for GLM-5.2 High). A request that already names a Cascade variant
    (`claude-opus-5-high`, `claude-opus-5-medium-fast`) keeps that exact uid.
    """
    raw = listed_upstream(model)
    base, existing = _split_wire_uid(raw)
    base = base.replace(".", "-") if base else base
    if not base:
        return raw
    if existing:
        return raw
    fid = fuse_id(base)
    wires = _model_wire_uids.get(fid) or {}
    if wires:
        chosen = _normalize_effort(effort)
        if chosen and chosen in wires:
            return wires[chosen]
        for alt in _EFFORT_ALIASES.get(chosen, ()):
            if alt in wires:
                return wires[alt]
        for pref in _DEFAULT_EFFORTS:
            if pref in wires:
                return wires[pref]
        return next(iter(wires.values()))
    plain = _model_plain_uids.get(fid)
    if plain:
        return plain
    chosen = _normalize_effort(effort)
    if chosen:
        return _seeded_wire_uid(base, chosen)
    if _takes_effort(base):
        return _seeded_wire_uid(base, "medium")
    return base


_INTERNAL_EFFORT_TAILS = {
    "NONE": "none",
    "MINIMAL": "minimal",
    "LOW": "low",
    "MEDIUM": "medium",
    "HIGH": "high",
    "XHIGH": "xhigh",
    "MAX": "max",
}
_NAME_WORDS = {
    "astra": "Astra",
    "claude": "Claude",
    "codex": "Codex",
    "deepseek": "DeepSeek",
    "fable": "Fable",
    "fast": "Fast",
    "flash": "Flash",
    "gemini": "Gemini",
    "glm": "GLM",
    "gpt": "GPT",
    "grok": "Grok",
    "haiku": "Haiku",
    "inkling": "Inkling",
    "kimi": "Kimi",
    "lightning": "Lightning",
    "luna": "Luna",
    "mini": "Mini",
    "nemotron": "Nemotron",
    "opus": "Opus",
    "pro": "Pro",
    "sol": "Sol",
    "sonnet": "Sonnet",
    "swe": "SWE",
    "terra": "Terra",
    "thinking": "Thinking",
    "ultra": "Ultra",
}


def is_internal_uid(uid: str) -> bool:
    u = (uid or "").strip()
    if not u:
        return True
    if u.startswith("MODEL_") or u.startswith("MODEL"):
        return True
    return u.isupper() and "_" in u


def _split_internal_uid(uid: str) -> tuple[str, str]:
    """MODEL_GPT_5_2_HIGH → (MODEL_GPT_5_2, high)."""
    u = (uid or "").strip()
    if not is_internal_uid(u):
        return u, ""
    parts = [p for p in u.split("_") if p]
    if parts and parts[-1] in _INTERNAL_EFFORT_TAILS:
        effort = _INTERNAL_EFFORT_TAILS[parts.pop()]
        return "_".join(parts), effort
    return u, ""


def _label_slug(name: str) -> str:
    text = (name or "").strip().lower().replace(".", "-")
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return text


def family_label(name: str, base: str = "") -> str:
    label = (name or "").strip()
    work = re.sub(r"\s+1M$", "", label, flags=re.I).strip()
    # "Medium Thinking Fast" is a speed flag. "SWE-1.6 Fast" is the SKU name.
    if not (base or "").endswith("-fast"):
        work = re.sub(r"\s+Fast$", "", work, flags=re.I).strip()
    stripped = _LABEL_TAIL.sub("", work).strip() or work or label
    if (base or "").endswith("-1m") and not re.search(r"1M$", stripped, re.I):
        stripped = f"{stripped} 1M"
    return stripped


def listed_upstream(model: str) -> str:
    """Keep catalog effort/speed suffixes; only normalize dots (gpt-5.6 → gpt-5-6)."""
    return upstream_id(model).replace(".", "-")


def model_label(model: str) -> str:
    fid = fuse_id(listed_upstream(model) or model)
    if fid in _model_labels:
        return _model_labels[fid]
    return _model_labels.get(canonical_fuse_id(model) or "") or ""


def efforts_for(model: str) -> tuple[str, ...]:
    """Thinking modes this Devin family actually lists as Cascade SKUs.

    Modes whose wire uid is currently failing upstream (runtime circuit
    breaker) are hidden so pickers stop offering dead SKUs; they return
    automatically once the cooldown lapses or the catalog changes.
    """
    fid = canonical_fuse_id(model) or fuse_id(listed_upstream(model) or model)
    if fid in _model_efforts:
        listed = _model_efforts[fid]
        wires = _model_wire_uids.get(fid) or {}
        live = tuple(e for e in listed if not sku_dead(wires.get(e, "")))
        return live or listed
    uid = listed_upstream(model) or upstream_id(model)
    if is_internal_uid(uid) or not _takes_effort(canonical_upstream(model)):
        return ()
    return SEEDED_EFFORTS


def _ordered_efforts(seen: set[str]) -> tuple[str, ...]:
    return tuple(name for name in _WIRE_EFFORTS if name in seen)


def _record_wire(wires: dict[str, dict[str, str]], fid: str, effort: str, uid: str) -> None:
    if not effort:
        return
    bucket = wires.setdefault(fid, {})
    prev = bucket.get(effort)
    if prev is None or (_is_speed_variant(prev) and not _is_speed_variant(uid)):
        bucket[effort] = uid


def catalog_ids_from_records(recs: list[dict]) -> tuple[list[str], dict[str, str]]:
    """One Fuse id per Cascade family; effort lives on `reasoning_effort`.

    Windsurf lists `claude-opus-5-medium` and `claude-opus-5-high` as
    separate SKUs. Fuse collapses those to `devin/claude-opus-5` and
    records the exact wire uid for each thinking mode so chat sends a
    SKU Cognition actually lists. Internal `MODEL_*` thinking rows
    collapse the same way (`MODEL_GPT_5_2_HIGH` → `devin/gpt-5-2`).

    Each catalog row also carries its family metadata with the row's own
    effort dimension (`glm-5-2` → Effort="High"); that label is preferred
    over uid/label parsing, which only approximates it.
    """
    global _model_efforts, _model_wire_uids, _model_plain_uids, _model_promo_until
    names: list[str] = []
    labels: dict[str, str] = {}
    efforts: dict[str, set[str]] = {}
    wires: dict[str, dict[str, str]] = {}
    plains: dict[str, str] = {}
    promos: dict[str, int] = {}
    label_fids: dict[str, str] = {}
    for rec in recs or []:
        if not isinstance(rec, dict):
            continue
        uid = listed_upstream(str(rec.get("id") or "").strip())
        if not uid:
            continue
        raw_name = str(rec.get("name") or "").strip()
        meta_effort = _effort_token(str(rec.get("effort") or ""))
        promo_until = int(rec.get("promo_until") or 0)
        if promo_until and promo_until < int(time.time()):
            # Promo window already lapsed upstream: do not list or route the
            # SKU. It returns automatically if the catalog re-lists it.
            continue
        if is_internal_uid(uid):
            _base, effort = _split_internal_uid(uid)
            if meta_effort:
                effort = meta_effort
            if not effort:
                effort = _effort_from_label(raw_name)
            fam = family_label(raw_name, _base) or _humanize_fallback(uid)
            slug = _label_slug(fam)
            label_key = re.sub(r"\s+", " ", fam).strip().lower()
            fid = label_fids.get(label_key) if effort else ""
            if not fid:
                fid = fuse_id(slug) if slug else fuse_id(_base or uid)
            if fid not in labels:
                names.append(fid)
                labels[fid] = fam
                efforts[fid] = set()
            if effort:
                label_fids[label_key] = fid
                efforts[fid].add(effort)
                _record_wire(wires, fid, effort, uid)
            elif fid not in plains:
                plains[fid] = uid
            if promo_until:
                promos[fid] = min(promos.get(fid, promo_until), promo_until)
            continue
        base, effort = _split_wire_uid(uid)
        base = base.replace(".", "-") if base else base
        if not base:
            continue
        if meta_effort:
            effort = meta_effort
        fid = fuse_id(base)
        if fid not in labels:
            names.append(fid)
            labels[fid] = family_label(raw_name, base) or _humanize_fallback(fid)
            efforts[fid] = set()
        elif not labels[fid]:
            labels[fid] = family_label(raw_name, base) or _humanize_fallback(fid)
        if not effort:
            effort = _effort_from_label(raw_name)
        if effort:
            efforts[fid].add(effort)
            _record_wire(wires, fid, effort, uid)
        elif fid not in plains:
            plains[fid] = uid
        if promo_until:
            promos[fid] = min(promos.get(fid, promo_until), promo_until)
    for seeded in DEVIN_MODELS:
        fid = fuse_id(listed_upstream(seeded) or seeded)
        if fid in labels:
            continue
        names.append(fid)
        labels[fid] = _humanize_fallback(fid)
        base = canonical_upstream(fid)
        if _takes_effort(base):
            efforts[fid] = set(SEEDED_EFFORTS)
            for item in SEEDED_EFFORTS:
                _record_wire(wires, fid, item, _seeded_wire_uid(base, item))
        else:
            efforts[fid] = set()
            plains[fid] = base
    _model_efforts = {fid: _ordered_efforts(seen) for fid, seen in efforts.items()}
    _model_wire_uids = wires
    _model_plain_uids = plains
    _model_promo_until = promos
    return names, labels


def _humanize_fallback(fid: str) -> str:
    uid = upstream_id(fid)
    parts = [p for p in uid.replace(".", "-").split("-") if p]
    words: list[str] = []
    for part in parts:
        low = part.lower()
        if low in _NAME_WORDS:
            words.append(_NAME_WORDS[low])
        elif re.fullmatch(r"\d+", part):
            words.append(part)
        else:
            words.append(part if any(ch.isupper() for ch in part) else part.capitalize())
    merged: list[str] = []
    for word in words:
        prev = merged[-1] if merged else ""
        if prev and re.fullmatch(r"\d+(?:\.\d+)*", prev) and re.fullmatch(r"\d+", word):
            merged[-1] = f"{prev}.{word}"
        elif prev and re.fullmatch(r"[vV]\d+(?:\.\d+)*", prev) and re.fullmatch(r"\d+", word):
            merged[-1] = f"{prev}.{word}"
        else:
            merged.append(word)
    text = " ".join(merged)
    text = re.sub(r"\bSWE (\d)", r"SWE-\1", text)
    return text or fid


def cached_models(ttl: float) -> list[str] | None:
    """Cached catalog names when younger than `ttl` seconds; None when a fetch is due."""
    if _models_cache and time.time() - _models_cache[0] < ttl and _models_cache[1]:
        return list(_models_cache[1])
    return None


def catalog_empty() -> bool:
    """True before any catalog (or seed backfill) has been recorded."""
    return not _model_wire_uids and not _model_plain_uids


def _remember_models(names: list[str], labels: dict[str, str]) -> list[str]:
    """Keep every id already listed. A short CLI catalog must not drop a model
    the picker showed on the previous refresh; new SKUs are appended."""
    global _models_cache, _model_labels
    if not names and _models_cache and _models_cache[1]:
        return list(_models_cache[1])
    # Upstream order first, then ids an earlier catalog listed and this omits.
    merged = []
    for name in names + (list(_models_cache[1]) if _models_cache and _models_cache[1] else []):
        if name and name not in merged:
            merged.append(name)
    if labels:
        kept = dict(_model_labels)
        kept.update(labels)
        _model_labels = kept
    if merged:
        _models_cache = (time.time(), merged)
    return merged or names
