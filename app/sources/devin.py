"""Devin / Cascade pool source.

Talks to Cognition's unpublished Connect-RPC API (server.codeium.com) the same
way Devin CLI does. Fuse does not shell out to the CLI. Accounts are linked on
/sources with the same browser PKCE login the CLI uses (`/auth/cli/continue`).
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import re
import secrets
import threading
import time
import uuid
from datetime import datetime, timezone
from urllib.parse import urlencode

import httpx

from .. import accounts, db
from ..config import settings
from ..crypto import encrypt
from ..proxy import UsageStream, usage_from_obj
from .base import ChatResult
from . import devin_proto as proto
from .pool_pick import note_failure, pick_account, release, reserve
from . import pool_policy
from .pool_session import bind, drop_cache_routing_fields, lookup

logger = logging.getLogger(__name__)

USAGE_TTL = 300
SESSION_TOKEN_PREFIX = "devin-session-token$"
FUSE_PREFIX = "devin/"
AUTH_PATH = "/exa.auth_pb.AuthService/GetUserJwt"
CHAT_PATH = "/exa.api_server_pb.ApiServerService/GetChatMessage"
MODELS_PATH = "/exa.api_server_pb.ApiServerService/GetCliModelConfigs"
STATUS_PATH = "/exa.seat_management_pb.SeatManagementService/GetUserStatus"
DEFAULT_STOP = ["\n\nUSER:", "\n\nASSISTANT:", "<|context_request|>", "<|end_of_turn|>"]

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
_jwt_cache: dict[str, tuple[float, dict]] = {}
_JWT_TTL = 240.0

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


def ensure_devin_source() -> int:
    row = db.fetchone("SELECT id FROM sources WHERE type='devin'")
    if row:
        return row["id"]
    with db.tx() as conn:
        conn.execute(
            "INSERT INTO sources (type, name, enabled, config, created_at) VALUES (?,?,?,?,?)",
            ("devin", "Devin", 1, "{}", db.now()),
        )
        return db.last_id(conn)


def disable_credential(cred_id: int) -> None:
    db.execute("UPDATE credentials SET disabled=1 WHERE id=?", (cred_id,))


def _row_get(row, key, default=None):
    try:
        if isinstance(row, dict):
            return row.get(key, default)
        if key in row.keys():
            val = row[key]
            return default if val is None else val
    except Exception:
        return default
    return default


def is_held(cred, now: datetime | None = None) -> bool:
    until = str(_row_get(cred, "pool_held_until") or "")
    if not until:
        return False
    try:
        ts = datetime.fromisoformat(until.replace("Z", "+00:00"))
    except ValueError:
        return False
    return ts > (now or datetime.now(timezone.utc))


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


def _api_server() -> str:
    return (settings.devin_api_server or "https://server.codeium.com").rstrip("/")


def _meta(token: str, user_jwt: str = "") -> dict:
    # Identify as Devin CLI / Local so Local-only GPT SKUs stay on this pool.
    # `agent_name` (metadata field 28) is required for newer Cascade SKUs —
    # see encode_metadata.
    return {
        "ide_name": settings.devin_ide_name or "devin",
        "ide_version": settings.devin_ide_version,
        "extension_name": settings.devin_extension_name or "cli",
        "extension_version": settings.devin_extension_version,
        "api_key": token,
        "locale": "en",
        "user_jwt": user_jwt,
        "agent_name": "chisel",
    }


def _catalog_meta(token: str) -> dict:
    """Windsurf identity — GetCliModelConfigs returns the Cascade catalog.

    CLI identity (`ide_name=devin`) only yields a stub SKU (GPT-4.1).
    """
    return {
        "ide_name": settings.devin_catalog_ide_name or "windsurf",
        "ide_version": settings.devin_catalog_ide_version,
        "extension_name": settings.devin_catalog_extension_name or "windsurf",
        "extension_version": settings.devin_catalog_extension_version,
        "api_key": token,
        "locale": "en",
    }


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


def _headers(*, streaming: bool = False) -> dict:
    if streaming:
        return {
            "content-type": "application/connect+proto",
            "connect-protocol-version": "1",
            "connect-content-encoding": "gzip",
            "connect-accept-encoding": "gzip",
            "accept-encoding": "identity",
        }
    return {
        "content-type": "application/proto",
        "connect-protocol-version": "1",
        "accept": "*/*",
    }


async def _unary(client: httpx.AsyncClient, path: str, body: bytes) -> tuple[int, bytes]:
    url = _api_server() + path
    try:
        r = await client.post(url, content=body, headers=_headers())
    except httpx.TimeoutException:
        return 504, b""
    except httpx.TransportError:
        return 502, b""
    return r.status_code, r.content or b""


async def get_user_jwt(token: str) -> tuple[dict | None, str]:
    token = normalize_token(token)
    hit = _jwt_cache.get(token)
    if hit and time.time() - hit[0] < _JWT_TTL:
        return hit[1], ""
    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=10.0)) as client:
        status, raw = await _unary(client, AUTH_PATH, proto.encode_get_user_jwt(_meta(token)))
    if status in (401, 403):
        return None, "Invalid Devin session token."
    if status != 200:
        return None, f"Devin auth http {status}"
    try:
        decoded = proto.decode_get_user_jwt(raw)
    except Exception:
        return None, "Devin auth returned an unreadable response."
    if not decoded.get("user_jwt"):
        return None, "Devin auth returned an empty JWT."
    _jwt_cache[token] = (time.time(), decoded)
    return decoded, ""


async def fetch_status(token: str) -> dict:
    token = normalize_token(token)
    async with httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=10.0)) as client:
        status, raw = await _unary(client, STATUS_PATH, proto.encode_unary_metadata(_meta(token)))
    if status != 200 or not raw:
        return {}
    try:
        return proto.decode_user_status(raw)
    except Exception:
        return {}


def _remember_models(names: list[str], labels: dict[str, str]) -> list[str]:
    """Keep every id already listed. A short CLI catalog must not drop a model
    the picker showed on the previous refresh; new SKUs are appended."""
    global _models_cache, _model_labels
    if not names and _models_cache and _models_cache[1]:
        return list(_models_cache[1])
    merged = list(_models_cache[1]) if _models_cache and _models_cache[1] else []
    for name in names:
        if name and name not in merged:
            merged.append(name)
    if labels:
        kept = dict(_model_labels)
        kept.update(labels)
        _model_labels = kept
    if merged:
        _models_cache = (time.time(), merged)
    return merged or names


async def fetch_catalog(token: str) -> list[str]:
    now = time.time()
    if _models_cache and now - _models_cache[0] < 45:
        return list(_models_cache[1])
    token = normalize_token(token)
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=10.0)) as client:
            status, raw = await _unary(
                client, MODELS_PATH, proto.encode_unary_metadata(_catalog_meta(token))
            )
    except (httpx.HTTPError, ValueError):
        status, raw = 0, b""
    names: list[str] = []
    labels: dict[str, str] = {}
    if status == 200 and raw:
        try:
            names, labels = catalog_ids_from_records(proto.decode_cli_model_configs(raw))
        except Exception:
            logger.warning("Devin model catalog could not be parsed", exc_info=True)
    if not names:
        names, labels = catalog_ids_from_records([])
    return _remember_models(names, labels)


def billing_has_subscription(billing: dict | None) -> bool:
    if not isinstance(billing, dict):
        return False
    if billing.get("last_error") and not billing.get("fetched_at"):
        return False
    plan = str(billing.get("plan_type") or "").strip().lower()
    if plan in ("pro", "max", "teams", "enterprise", "team"):
        return True
    if billing.get("pro"):
        return True
    if billing.get("fetched_at") and not billing.get("last_error"):
        return True
    return False


def credential_is_authed(cred, extra: dict | None = None) -> bool:
    if _row_get(cred, "disabled"):
        return False
    extra = extra if extra is not None else accounts.cred_extra(cred)
    key = extra.get("api_key") or accounts.cred_secret(cred)
    if not key:
        return False
    billing = extra.get("billing") if isinstance(extra.get("billing"), dict) else {}
    err = str(billing.get("last_error") or "").lower()
    if "invalid" in err or "401" in err or "403" in err:
        return False
    return True


def user_has_devin(user_id: int) -> bool:
    return devin_access_state(user_id)["ok"]


def _devin_eligible(row, extra: dict) -> bool:
    if not credential_is_authed(row, extra):
        return False
    billing = extra.get("billing") if isinstance(extra.get("billing"), dict) else {}
    return bool(billing_has_subscription(billing) or extra.get("api_key") or accounts.cred_secret(row))


def devin_own_access(user_id: int) -> dict:
    """Access from this user's own donated Devin accounts only."""
    sid = ensure_devin_source()
    rows = db.fetchall(
        "SELECT * FROM credentials WHERE source_id=? AND donated_by=? AND disabled=0",
        (sid, user_id),
    )
    if not rows:
        return {"ok": False, "reason": "none"}
    saw_authed = False
    for row in rows:
        extra = accounts.cred_extra(row)
        if not credential_is_authed(row, extra):
            continue
        saw_authed = True
        if _devin_eligible(row, extra):
            return {"ok": True, "reason": "ok"}
    return {"ok": False, "reason": "unauthed" if not saw_authed else "no_sub"}


def _devin_pool_ready() -> bool:
    sid = ensure_devin_source()
    rows = db.fetchall(
        "SELECT * FROM credentials WHERE source_id=? AND disabled=0",
        (sid,),
    )
    return any(_devin_eligible(row, accounts.cred_extra(row)) for row in rows)


def devin_access_state(user_id: int) -> dict:
    return pool_policy.with_open_access(
        user_id, "devin", devin_own_access(user_id), _devin_pool_ready()
    )


def note_pool_invoke(cred_id: int) -> None:
    db.execute("UPDATE credentials SET pool_invokes = COALESCE(pool_invokes, 0) + 1 WHERE id=?", (cred_id,))


def _find_key(source_id: int, key: str):
    for row in db.fetchall("SELECT * FROM credentials WHERE source_id=?", (source_id,)):
        try:
            if accounts.cred_secret(row) == key:
                return row
        except Exception:
            continue
    return None


def _store_billing(cred_id: int, parsed: dict, *, error: str = "") -> dict:
    row = db.fetchone("SELECT * FROM credentials WHERE id=?", (cred_id,))
    extra = accounts.cred_extra(row) if row else {}
    billing = extra.get("billing") if isinstance(extra.get("billing"), dict) else {}
    now = db.now()
    billing["checked_at"] = now
    if error:
        billing["last_error"] = error[:200]
        extra["billing"] = billing
        if row:
            accounts.update_extra(cred_id, extra)
        return billing
    if parsed.get("email"):
        extra["email"] = parsed["email"]
    if parsed.get("name"):
        extra["account_name"] = parsed["name"]
    billing["plan_type"] = parsed.get("plan_type") or billing.get("plan_type") or "Pro"
    billing["pro"] = bool(parsed.get("pro") or billing.get("plan_type") == "Pro")
    billing["fetched_at"] = now
    billing["last_error"] = ""
    extra["billing"] = billing
    if row:
        accounts.update_extra(cred_id, extra)
        if parsed.get("email") or parsed.get("name"):
            label = parsed.get("email") or parsed.get("name") or row["label"]
            db.execute("UPDATE credentials SET label=? WHERE id=?", (str(label)[:80], cred_id))
    return billing


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def start_cli_login(redirect_uri: str = "") -> dict:
    """Browser PKCE, same page as `devin auth login`.

    Devin only accepts redirect_uri = http://127.0.0.1:<port>/callback (the
    local CLI). A hosted dashboard cannot use that, so we omit redirect_uri
    and complete with the one-time login code Devin shows after sign-in.
    """
    verifier = _b64url(secrets.token_bytes(32))
    challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    state = secrets.token_urlsafe(24)
    params = {
        "state": state,
        "prompt": "select_account",
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    if redirect_uri:
        params["redirect_uri"] = redirect_uri
    webapp = (settings.devin_webapp_url or "https://app.devin.ai").rstrip("/")
    return {
        "state": state,
        "verifier": verifier,
        "challenge": challenge,
        "redirect_uri": redirect_uri,
        "auth_url": f"{webapp}/auth/cli/continue?{urlencode(params)}",
    }


def _token_from_exchange(data) -> str:
    if not isinstance(data, dict):
        return ""
    for key in ("token", "api_key", "session_token", "windsurf_api_key", "access_token"):
        val = data.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    inner = data.get("data")
    if isinstance(inner, dict):
        return _token_from_exchange(inner)
    return ""


async def exchange_cli_code(code: str, verifier: str, redirect_uri: str = "") -> tuple[str, str]:
    code = "".join((code or "").split())
    verifier = (verifier or "").strip()
    if not code or not verifier:
        return "", "Devin sign-in did not return an authorization code."
    url = (settings.devin_cli_token_url or "https://api.devin.ai/auth/cli/token").strip()
    payload = {"code": code, "code_verifier": verifier}
    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=10.0), follow_redirects=False) as client:
        try:
            r = await client.post(url, json=payload, headers={"Accept": "application/json", "Content-Type": "application/json"})
        except httpx.HTTPError:
            return "", "Could not reach Devin to finish sign-in."
    if r.status_code in (401, 403):
        return "", "Devin rejected the sign-in. Try adding the account again."
    if r.status_code != 200:
        return "", f"Devin token exchange failed (HTTP {r.status_code})."
    try:
        data = r.json()
    except json.JSONDecodeError:
        return "", "Devin returned an unreadable sign-in response."
    token = _token_from_exchange(data)
    if not token:
        return "", "Devin sign-in did not return a session token."
    return token, ""


async def probe_token(raw: str) -> tuple[dict | None, str]:
    token = normalize_token(raw)
    if not token or len(token) < 12 or len(token) > 4096:
        return None, "Devin sign-in did not return a usable session."
    if any(ord(ch) < 32 for ch in token):
        return None, "Devin sign-in did not return a usable session."
    jwt, err = await get_user_jwt(token)
    if err or not jwt:
        return None, err or "Could not validate this Devin token."
    status = await fetch_status(token)
    info = {
        "api_key": token,
        "api_server_url": (jwt.get("custom_api_server_url") or "").strip() or _api_server(),
        "email": status.get("email") or "",
        "name": status.get("name") or "",
        "plan_type": status.get("plan_type") or ("Pro" if status.get("pro") else "Pro"),
        "pro": True,
    }
    return info, ""


async def complete_cli_login(
    code: str,
    verifier: str,
    donated_by: int,
    *,
    redirect_uri: str = "",
    label: str = "",
) -> tuple[int | None, str]:
    token, err = await exchange_cli_code(code, verifier, redirect_uri)
    if err or not token:
        return None, err or "Devin sign-in failed."
    return await add_session_token(ensure_devin_source(), token, donated_by, label=label)


async def add_session_token(source_id: int, raw: str, donated_by: int, *, label: str = "") -> tuple[int | None, str]:
    info, err = await probe_token(raw)
    if err or not info:
        return None, err or "Could not add that token."
    key = info["api_key"]
    existing = _find_key(source_id, key)
    if existing and not existing["disabled"]:
        return None, "That Devin account is already in the pool."
    name = (label or "").strip()[:80] or info.get("email") or info.get("name") or "Devin account"
    extra = {
        "api_key": key,
        "api_server_url": info.get("api_server_url") or _api_server(),
        "email": info.get("email") or "",
        "account_name": info.get("name") or "",
    }
    if existing:
        db.execute(
            """UPDATE credentials SET disabled=0, donated_by=?, label=?, secret=?, extra=? WHERE id=?""",
            (donated_by, name, encrypt(key), encrypt(json.dumps(extra)), existing["id"]),
        )
        cred_id = int(existing["id"])
    else:
        cred_id = accounts.add_credential(source_id, name, key, extra=extra, donated_by=donated_by)
    _store_billing(cred_id, info)
    return cred_id, ""


async def refresh_status(cred, *, force: bool = False) -> None:
    extra = accounts.cred_extra(cred)
    billing = extra.get("billing") if isinstance(extra.get("billing"), dict) else {}
    fetched = str(billing.get("fetched_at") or "")
    if not force and fetched:
        try:
            ts = datetime.fromisoformat(fetched.replace("Z", "+00:00"))
            if (datetime.now(timezone.utc) - ts).total_seconds() < USAGE_TTL:
                return
        except ValueError:
            pass
    token = extra.get("api_key") or accounts.cred_secret(cred)
    jwt, err = await get_user_jwt(token)
    if err or not jwt:
        _store_billing(int(cred["id"]), {}, error=err or "auth failed")
        return
    status = await fetch_status(token)
    status.setdefault("plan_type", billing.get("plan_type") or "Pro")
    status["pro"] = True
    _store_billing(int(cred["id"]), status)


async def refresh_pool_status() -> None:
    sid = ensure_devin_source()
    rows = db.fetchall("SELECT * FROM credentials WHERE source_id=? AND disabled=0", (sid,))
    if not rows:
        return
    results = await asyncio.gather(*(refresh_status(row) for row in rows), return_exceptions=True)
    for row, result in zip(rows, results):
        if isinstance(result, Exception):
            logger.warning(
                "Devin status refresh failed for credential %s",
                row["id"],
                exc_info=(type(result), result, result.__traceback__),
            )


def account_email(extra: dict) -> str:
    return str(extra.get("email") or extra.get("account_name") or "").strip()


def _openai_usage(u: dict | None) -> dict:
    if not u:
        return {}
    prompt = int(u.get("prompt_tokens") or 0)
    completion = int(u.get("completion_tokens") or 0)
    cached = int(u.get("cached_tokens") or 0)
    write = int(u.get("cache_write_tokens") or 0)
    obj = {
        "usage": {
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "total_tokens": prompt + completion,
            "prompt_tokens_details": {"cached_tokens": cached, "cache_write_tokens": write},
        }
    }
    return usage_from_obj(obj)


def _sse(obj: dict) -> bytes:
    return f"data: {json.dumps(obj, separators=(',', ':'))}\n\n".encode()


def _completion_id() -> str:
    return "chatcmpl-" + uuid.uuid4().hex[:24]


import os as _os


def _devin_debug() -> bool:
    return _os.environ.get("FUSE_DEVIN_DEBUG", "0") == "1"


def _debug_tool_call(cid: str, msg: dict, tc: dict) -> None:
    """Stream capture: one line per decoded tool_call chunk, in arrival order."""
    try:
        args = str(tc.get("arguments_json") or "")
        line = json.dumps(
            {
                "cid": cid[-8:],
                "mid": str(msg.get("message_id") or "")[-8:],
                "tid": str(tc.get("id") or "")[:12],
                "name": tc.get("name") or "",
                "len": len(args),
                "head": args[:48],
                "tail": args[-32:],
                "stop": msg.get("stop_reason") or 0,
                "text": len(str(msg.get("delta_text") or "")),
            },
            ensure_ascii=False,
        )
        with open("/root/fuse/data/devin-stream-debug.log", "a") as fh:
            fh.write(line + "\n")
    except Exception:
        pass


def _cascade_id_for(session_id: str | None) -> str:
    if session_id:
        return str(uuid.uuid5(uuid.NAMESPACE_URL, f"devin-cascade:{session_id}"))
    return str(uuid.uuid4())


def _merge_tool_args(prev: str, incoming: str) -> str:
    """Merge one streamed tool-arguments chunk into the accumulator.

    Devin streams arguments as token-sized append deltas. A delta can be
    valid standalone JSON (a bare digit like "2" parses as a JSON number!),
    so validity must NEVER trigger replacement — that wiped the accumulator
    mid-command and produced tail fragments like '100"}'. Only true
    snapshot/redelivery relations may replace; everything else appends.
    """
    if not incoming:
        return prev
    if not prev:
        return incoming
    if incoming == prev:
        return prev
    if _json_object(prev) is not None and _json_object(incoming) is not None:
        # Two standalone objects can only be a snapshot resend superseding
        # the accumulation; appending would corrupt ('}{}' is not JSON).
        # Streaming deltas are fragments or non-objects (digits), so they
        # still take the append paths below.
        return incoming
    if incoming.startswith(prev):
        return incoming
    if prev.startswith(incoming):
        return prev
    return prev + incoming


def _json_object(raw: str) -> dict | None:
    try:
        parsed = json.loads(raw or "")
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    return parsed if isinstance(parsed, dict) else None


class _ToolBook:
    def __init__(self) -> None:
        self.order: list[str] = []
        self.calls: dict[str, dict] = {}

    def _new(self, tid: str, name: str, incoming: str) -> dict:
        key = tid or name or f"call_{len(self.order)}"
        rec = {
            "id": tid or key,
            "name": name,
            "arguments": incoming,
            "index": len(self.order),
            "key": key,
        }
        self.calls[key] = rec
        if tid and tid != key:
            self.calls[tid] = rec
        self.order.append(key)
        return rec

    def _merge_into(self, rec: dict, tid: str, name: str, incoming: str) -> tuple[dict, str]:
        if tid:
            self.calls[tid] = rec
            if not rec["id"]:
                rec["id"] = tid
        if name and not rec["name"]:
            rec["name"] = name
        merged = _merge_tool_args(rec["arguments"], incoming)
        delta = merged[len(rec["arguments"]) :] if merged.startswith(rec["arguments"]) else merged
        rec["arguments"] = merged
        return rec, delta

    def add(self, tc: dict) -> tuple[dict, str]:
        tid = str(tc.get("id") or "").strip()
        name = str(tc.get("name") or "").strip()
        incoming = str(tc.get("arguments_json") or tc.get("invalid_json") or "")
        if _devin_debug():
            self._debug_decision("in", tid, name, incoming)
        if tid and tid in self.calls:
            existing = self.calls[tid]
            # The server reuses one tool_call id for every call of that tool.
            # A tid'd frame with empty args after a COMPLETED record is the
            # opening frame of the NEXT call — not a redelivery of this one.
            if not incoming and _json_object(existing["arguments"]) is not None:
                rec = self._new(self._next_key(tid), name, incoming)
                if _devin_debug():
                    self._debug_decision("newcall", tid, name, rec["arguments"])
                return rec, incoming
            rec, delta = self._merge_into(existing, tid, name, incoming)
            if _devin_debug():
                self._debug_decision("bytid", tid, name, rec["arguments"])
            return rec, delta
        if self.order:
            last = self.calls[self.order[-1]]
            last_done = _json_object(last["arguments"]) is not None
            same_name = bool(name) and bool(last["name"]) and name == last["name"]
            can_fill = (not last["name"] or not name or same_name) and not last_done
            if can_fill:
                rec, delta = self._merge_into(last, tid, name, incoming)
                if _devin_debug():
                    self._debug_decision("fill", tid, name, rec["arguments"])
                return rec, delta
        rec = self._new(tid, name, incoming)
        if _devin_debug():
            self._debug_decision("new", tid, name, rec["arguments"])
        return rec, incoming

    def _next_key(self, base: str) -> str:
        key = base
        n = 1
        while key in self.calls:
            key = f"{base}#{n}"
            n += 1
        return key

    def _debug_decision(self, action: str, tid: str, name: str, args: str) -> None:
        try:
            line = json.dumps(
                {"act": action, "tid": tid[:12], "name": name[:24], "len": len(args),
                 "head": args[:40], "tail": args[-24:]},
                ensure_ascii=False,
            )
            with open("/root/fuse/data/devin-stream-debug.log", "a") as fh:
                fh.write(line + "\n")
        except Exception:
            pass

    def openai_calls(self) -> list[dict]:
        seen: set[int] = set()
        out = []
        for key in self.order:
            rec = self.calls[key]
            if rec["index"] in seen or not rec["name"]:
                continue
            seen.add(rec["index"])
            args = rec["arguments"] or "{}"
            out.append(
                {
                    "index": len(out),
                    "id": rec["id"],
                    "type": "function",
                    "function": {"name": rec["name"], "arguments": args},
                }
            )
        return out


def _messages_to_prompts(messages: list, cascade_id: str) -> tuple[str, list[dict]]:
    system = []
    prompts = []
    index = 0
    for msg in messages or []:
        if not isinstance(msg, dict):
            continue
        role = str(msg.get("role") or "")
        content = msg.get("content")
        text = ""
        images = []
        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            parts = []
            for part in content:
                if not isinstance(part, dict):
                    continue
                if part.get("type") == "text":
                    parts.append(str(part.get("text") or ""))
                img = part.get("image_url")
                url = img.get("url") if isinstance(img, dict) else img
                if isinstance(url, str) and url.startswith("data:") and ";base64," in url:
                    header, _, b64 = url.partition(",")
                    mime = header[5:].split(";")[0] or "image/png"
                    images.append({"base64": b64, "mime": mime})
            text = "\n".join(parts)
        if role in ("system", "developer"):
            if text.strip():
                system.append(text)
            continue
        source = proto.CHAT_SOURCE_USER
        tool_calls = []
        tool_call_id = ""
        if role == "assistant":
            source = proto.CHAT_SOURCE_SYSTEM
            for call in msg.get("tool_calls") or []:
                if not isinstance(call, dict):
                    continue
                fn = call.get("function") if isinstance(call.get("function"), dict) else {}
                args = fn.get("arguments")
                if isinstance(args, dict):
                    args = json.dumps(args)
                tool_calls.append(
                    {
                        "id": str(call.get("id") or ""),
                        "name": str(fn.get("name") or ""),
                        "arguments_json": str(args or "{}"),
                        "is_custom_tool_call": True,
                    }
                )
        elif role == "tool":
            source = proto.CHAT_SOURCE_TOOL
            tool_call_id = str(msg.get("tool_call_id") or "")
        thinking = ""
        if isinstance(msg.get("reasoning_content"), str):
            thinking = msg["reasoning_content"]
        elif isinstance(msg.get("thinking"), str):
            thinking = msg["thinking"]
        prompts.append(
            {
                "message_id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"{cascade_id}:{index}:{role}")),
                "source": source,
                "prompt": text,
                "tool_calls": tool_calls,
                "tool_call_id": tool_call_id,
                "images": images,
                "thinking": thinking,
                "signature": str(msg.get("devin_signature") or ""),
                "signature_type": str(msg.get("devin_signature_type") or ""),
            }
        )
        index += 1
    return "\n\n".join(system), prompts


def _tools_from_body(body: dict) -> list[dict]:
    out = []
    for tool in body.get("tools") or []:
        if not isinstance(tool, dict):
            continue
        fn = tool.get("function") if isinstance(tool.get("function"), dict) else tool
        name = fn.get("name")
        if not isinstance(name, str) or not name:
            continue
        params = fn.get("parameters") if isinstance(fn.get("parameters"), dict) else {"type": "object"}
        out.append(
            {
                "name": name,
                "description": str(fn.get("description") or ""),
                "json_schema": json.dumps(params),
                "strict": bool(fn.get("strict")),
            }
        )
    return out


class DevinSource:
    type_id = "devin"

    def __init__(self, row, cfg: dict, creds):
        self.source_id = row["id"]
        self.name = row["name"]
        self.cfg = cfg
        self.creds = list(creds)

    def _pick(self, exclude: set[int] | None = None, prefer_id: int | None = None):
        now = datetime.now(timezone.utc)
        return pick_account(
            self.creds,
            kind="devin",
            exclude=exclude,
            is_held=lambda c: is_held(c, now),
            is_authed=credential_is_authed,
            prefer_id=prefer_id,
        )

    async def models(self) -> list[str]:
        picked = self._pick()
        token = ""
        if picked:
            cred, extra = picked
            token = extra.get("api_key") or accounts.cred_secret(cred)
        else:
            for cred in self.creds:
                extra = accounts.cred_extra(cred)
                token = extra.get("api_key") or accounts.cred_secret(cred)
                if token:
                    break
        if not token:
            return list(DEVIN_MODELS)
        return await fetch_catalog(token)

    async def chat(self, body: dict, *, session_id: str | None = None, user_id: int | None = None) -> ChatResult:
        await refresh_pool_status()
        self.creds = pool_policy.filter_credentials(
            db.fetchall(
                "SELECT * FROM credentials WHERE source_id=? AND disabled=0",
                (self.source_id,),
            ),
            "devin",
            user_id,
        )
        tried: set[int] = set()
        attempts: list[dict] = []
        last = ChatResult(status=503, body={"error": "No Devin accounts in the pool"})
        requested = body.get("model") or DEVIN_MODELS[0]
        model = listed_upstream(requested) or upstream_id(requested)
        payload = drop_cache_routing_fields(body)
        effort = payload.get("reasoning_effort")
        if not isinstance(effort, str):
            effort = None
        probe_uid = resolve_wire_uid(model, effort)
        if sku_dead(probe_uid):
            # This exact SKU failed upstream moments ago (circuit breaker).
            # Do not burn a pool attempt on it; fail fast with a clear reason.
            return ChatResult(
                status=503,
                body={"error": {"message": _sku_unavailable_message(requested, effort)}},
                model=model,
            )
        prefer_id = lookup("devin", session_id)
        while True:
            picked = self._pick(exclude=tried, prefer_id=prefer_id)
            if not picked:
                last.attempts = attempts
                return last
            cred, extra = picked
            tried.add(int(cred["id"]))
            note_pool_invoke(cred["id"])
            reserve(cred["id"])
            token = extra.get("api_key") or accounts.cred_secret(cred)
            stream_reserved = False
            try:
                result = await self._chat_one(token, payload, model, cred, session_id, attempts)
                if result.stream is not None:
                    stream_reserved = True
                    return self._stream_result_wrap(
                        result.stream, result.usage or {}, cred, session_id, model, attempts
                    )
                last = result
                if result.status == 401:
                    disable_credential(cred["id"])
                    last.status = 502
                    last.body = {"error": {"message": "Devin rejected the configured account"}}
                    attempts.append({"credential_id": cred["id"], "status": 401, "usage": result.usage or {}})
                    continue
                if result.status == 429:
                    note_failure(cred["id"], cooldown_seconds=60)
                    attempts.append({"credential_id": cred["id"], "status": 429, "usage": result.usage or {}})
                    continue
                if result.status >= 500:
                    note_failure(cred["id"], cooldown_seconds=15)
                    attempts.append({"credential_id": cred["id"], "status": result.status, "usage": result.usage or {}})
                    continue
                if result.status == 200:
                    bind("devin", session_id, cred["id"])
                last.attempts = attempts
                return last
            finally:
                if not stream_reserved:
                    release(cred["id"])

    async def _chat_one(self, token, body, model, cred, session_id, attempts) -> ChatResult:
        jwt, err = await get_user_jwt(token)
        if err or not jwt:
            return ChatResult(
                status=401,
                body={"error": {"message": err or "Devin auth failed"}},
                credential_id=cred["id"],
                model=model,
            )
        cascade_id = _cascade_id_for(session_id)
        system, prompts = _messages_to_prompts(body.get("messages") or [], cascade_id)
        temperature = body.get("temperature")
        try:
            temperature = 0.4 if temperature is None else float(temperature)
        except (TypeError, ValueError):
            temperature = 0.4
        if temperature == 0:
            temperature = 0.01
        # Dashboard chat does not send max_tokens. A silent 16k default was
        # Fuse's own per-round budget and cut long thinking streams mid-turn.
        # Only encode a cap when the client asked for one.
        max_tokens = body.get("max_tokens")
        if max_tokens is not None:
            try:
                max_tokens = max(1, min(int(max_tokens), 64000))
            except (TypeError, ValueError):
                max_tokens = None
        tools = _tools_from_body(body)
        effort = body.get("reasoning_effort")
        if not isinstance(effort, str):
            effort = None
        if not _model_wire_uids and not _model_plain_uids:
            await fetch_catalog(token)
        model_uid = resolve_wire_uid(model, effort)
        parallel = body.get("parallel_tool_calls")
        disable_parallel = False if parallel is None else not bool(parallel)
        choice = body.get("tool_choice")
        option = "auto"
        tool_name = ""
        if isinstance(choice, str) and choice in ("none", "auto", "required"):
            option = "none" if choice == "none" else "auto"
        elif isinstance(choice, dict):
            fn = choice.get("function") if isinstance(choice.get("function"), dict) else {}
            tool_name = str(fn.get("name") or choice.get("name") or "")
            if tool_name:
                option = "function"
        req = {
            "metadata": _meta(token, jwt.get("user_jwt") or ""),
            "prompt": system,
            "prompts": prompts,
            "model_uid": model_uid,
            "configuration": {
                "num_completions": 1,
                "max_tokens": max_tokens or 0,
                "max_newlines": 0,
                "temperature": temperature,
                "first_temperature": temperature,
                "top_k": 50,
                "top_p": float(body.get("top_p") or 1) or 1,
                "stop_patterns": [] if tools else list(DEFAULT_STOP),
                "fim_eot": 1,
            },
            "tools": tools,
            "disable_parallel_tool_calls": disable_parallel,
            "tool_choice": {"option_name": option, "tool_name": tool_name},
            "cascade_id": cascade_id,
            "execution_id": str(uuid.uuid4()),
        }
        frame = proto.connect_frame(proto.encode_get_chat_message(req))
        url = _api_server() + CHAT_PATH
        client = httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=15.0))
        want_stream = bool(body.get("stream"))
        try:
            req_http = client.build_request("POST", url, content=frame, headers=_headers(streaming=True))
            resp = await client.send(req_http, stream=True)
        except httpx.TimeoutException:
            await client.aclose()
            return ChatResult(status=504, body={"error": {"message": "Devin timed out"}}, credential_id=cred["id"], model=model)
        except httpx.TransportError as exc:
            await client.aclose()
            return ChatResult(
                status=502,
                body={"error": {"message": f"Devin transport failure ({type(exc).__name__})"}},
                credential_id=cred["id"],
                model=model,
            )
        if resp.status_code != 200:
            try:
                err_body = await resp.aread()
            except Exception:
                err_body = b""
            await resp.aclose()
            await client.aclose()
            status = resp.status_code
            msg = err_body.decode("utf-8", errors="replace")[:400] or f"http {status}"
            kind = sku_error_kind(msg)
            if kind:
                note_sku_failure(model_uid, kind)
            return ChatResult(
                status=status,
                body={"error": {"message": msg}},
                credential_id=cred["id"],
                model=model,
            )
        collected: dict = {}
        if want_stream:
            result = ChatResult(
                status=200,
                stream=self._stream_sse(resp, client, collected, model, model_uid),
                usage=collected,
                credential_id=cred["id"],
                model=model,
            )
            result.attempts = attempts
            return result
        try:
            obj, usage, trailer_error = await self._collect_completion(resp, model)
        finally:
            await resp.aclose()
            await client.aclose()
        if trailer_error:
            kind = sku_error_kind(trailer_error)
            if kind:
                note_sku_failure(model_uid, kind)
            return ChatResult(
                status=502,
                body={"error": {"message": trailer_error}},
                credential_id=cred["id"],
                model=model,
            )
        return ChatResult(status=200, body=obj, usage=usage, credential_id=cred["id"], model=model)

    def _stream_result_wrap(self, stream, collected, cred, session_id, model, attempts) -> ChatResult:
        cid = cred["id"]

        async def _bind_on_finish():
            try:
                async for chunk in stream:
                    yield chunk
            finally:
                close = getattr(stream, "aclose", None)
                if close is not None:
                    await close()
                status = (collected or {}).get("stream_status")
                if status == "complete":
                    bind("devin", session_id, cid)
                elif status == "failed":
                    # A quota-exhausted account is spent for the whole daily
                    # window; cool it well past the generic failure blip so
                    # the pool stops hammering it turn after turn.
                    quota_hit = "quota" in str((collected or {}).get("trailer_error") or "").lower()
                    note_failure(cid, cooldown_seconds=900 if quota_hit else 5)

        wrapped = UsageStream(_bind_on_finish(), collected, on_close=lambda: release(cid))
        result = ChatResult(status=200, stream=wrapped, credential_id=cid, model=model)
        result.attempts = attempts
        return result

    def _chunk(self, cid: str, created: int, model: str, delta: dict, finish=None) -> bytes:
        return _sse(
            {
                "id": cid,
                "object": "chat.completion.chunk",
                "created": created,
                "model": canonical_fuse_id(model) or fuse_id(model),
                "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
            }
        )

    async def _stream_sse(self, resp, client, collected: dict, model: str, uid: str = ""):
        cid = _completion_id()
        created = int(time.time())
        pending = b""
        thinking = ""
        signature = ""
        signature_type = ""
        output_id = ""
        book = _ToolBook()
        stop = 0
        stop_terminal = False
        try:
            async for chunk in resp.aiter_bytes():
                pending += chunk
                frames, pending = proto.iter_connect_frames(pending)
                for flag, payload in frames:
                    if flag & proto.CONNECT_END_STREAM:
                        trailer = payload.decode("utf-8", errors="replace").strip()
                        if trailer:
                            try:
                                parsed = json.loads(trailer)
                            except json.JSONDecodeError:
                                parsed = None
                            err = (parsed or {}).get("error") if isinstance(parsed, dict) else None
                            if isinstance(err, dict) and err.get("code"):
                                msg = str(err.get("message") or err.get("code"))
                                collected["stream_status"] = "failed"
                                collected["trailer_error"] = msg
                                kind = sku_error_kind(msg)
                                if kind:
                                    note_sku_failure(uid, kind)
                                yield _sse({"error": {"message": msg}})
                                return
                        continue
                    msg = proto.decode_get_chat_message(payload)
                    if msg.get("delta_thinking"):
                        if stop:
                            # Devin streams several internal message segments
                            # per completion; a stop_reason that is followed
                            # by more output was a segment boundary, not the
                            # end of the response.
                            stop_terminal = False
                        thinking += msg["delta_thinking"]
                        yield self._chunk(
                            cid, created, model, {"reasoning_content": msg["delta_thinking"]}
                        )
                    if msg.get("delta_signature"):
                        signature += msg["delta_signature"]
                    if msg.get("delta_signature_type"):
                        signature_type = msg["delta_signature_type"]
                    if msg.get("output_id"):
                        output_id = msg["output_id"]
                    if msg.get("delta_text"):
                        if stop:
                            stop_terminal = False
                        yield self._chunk(cid, created, model, {"content": msg["delta_text"]})
                    for tc in msg.get("tool_calls") or []:
                        if stop:
                            stop_terminal = False
                        if _devin_debug():
                            _debug_tool_call(cid, msg, tc)
                        book.add(tc)
                    if msg.get("usage"):
                        collected.update(_openai_usage(msg["usage"]))
                    if msg.get("stop_reason"):
                        stop = msg["stop_reason"]
                        stop_terminal = True
            calls = book.openai_calls()
            terminal_stop = stop if stop_terminal else 0
            # STOP_MAX_TOKENS is NOT a truncation signal on this backend:
            # production turns end terminally with stop=3 at 98-3000
            # completion tokens and complete content. Only a function-call
            # stop carries meaning; everything else is a normal stop.
            finish = (
                "tool_calls"
                if calls or terminal_stop == proto.STOP_FUNCTION_CALL
                else "stop"
            )
            if calls:
                yield self._chunk(
                    cid,
                    created,
                    model,
                    {
                        "tool_calls": [
                            {
                                "index": call["index"],
                                "id": call["id"],
                                "type": "function",
                                "function": {
                                    "name": call["function"]["name"],
                                    "arguments": call["function"]["arguments"],
                                },
                            }
                            for call in calls
                        ]
                    },
                )
            usage_obj = {
                "prompt_tokens": collected.get("prompt_tokens") or 0,
                "completion_tokens": collected.get("completion_tokens") or 0,
                "total_tokens": collected.get("total_tokens")
                or (collected.get("prompt_tokens") or 0) + (collected.get("completion_tokens") or 0),
            }
            collected.setdefault("usage_status", "complete")
            collected["stream_status"] = "complete"
            final_delta: dict = {}
            if signature:
                final_delta["devin_signature"] = signature
            if signature_type:
                final_delta["devin_signature_type"] = signature_type
            if output_id:
                final_delta["devin_output_id"] = output_id
            yield _sse(
                {
                    "id": cid,
                    "object": "chat.completion.chunk",
                    "created": created,
                    "model": canonical_fuse_id(model) or fuse_id(model),
                    "choices": [{"index": 0, "delta": final_delta, "finish_reason": finish}],
                    "usage": usage_obj,
                }
            )
            yield b"data: [DONE]\n\n"
        except Exception:
            collected["stream_status"] = "failed"
            raise
        finally:
            await resp.aclose()
            await client.aclose()

    async def _collect_completion(self, resp, model: str) -> tuple[dict, dict, str]:
        pending = b""
        text = ""
        thinking = ""
        signature = ""
        signature_type = ""
        output_id = ""
        book = _ToolBook()
        stop = 0
        stop_terminal = False
        usage: dict = {}
        trailer_error = ""
        async for chunk in resp.aiter_bytes():
            pending += chunk
            frames, pending = proto.iter_connect_frames(pending)
            for flag, payload in frames:
                if flag & proto.CONNECT_END_STREAM:
                    # Connect end-stream trailers carry structured errors
                    # ("an internal error occurred (trace ID: ...)", version
                    # gates). Swallowing them made failed completions look
                    # like empty 200s.
                    trailer = payload.decode("utf-8", errors="replace").strip()
                    if trailer:
                        try:
                            parsed = json.loads(trailer)
                        except json.JSONDecodeError:
                            parsed = None
                        err = (parsed or {}).get("error") if isinstance(parsed, dict) else None
                        if isinstance(err, dict) and err.get("code"):
                            trailer_error = str(err.get("message") or err.get("code"))
                    continue
                msg = proto.decode_get_chat_message(payload)
                if msg.get("delta_text"):
                    if stop:
                        # Intermediate segment stop — see _stream_sse.
                        stop_terminal = False
                    text += msg["delta_text"]
                if msg.get("delta_thinking"):
                    if stop:
                        stop_terminal = False
                    thinking += msg["delta_thinking"]
                signature += msg.get("delta_signature") or ""
                if msg.get("delta_signature_type"):
                    signature_type = msg["delta_signature_type"]
                if msg.get("output_id"):
                    output_id = msg["output_id"]
                for tc in msg.get("tool_calls") or []:
                    if stop:
                        stop_terminal = False
                    book.add(tc)
                if msg.get("usage"):
                    usage = _openai_usage(msg["usage"])
                if msg.get("stop_reason"):
                    stop = msg["stop_reason"]
                    stop_terminal = True
        terminal_stop = stop if stop_terminal else 0
        # See _stream_sse: STOP_MAX_TOKENS terminally is a normal end of turn.
        finish = (
            "tool_calls"
            if book.order or terminal_stop == proto.STOP_FUNCTION_CALL
            else "stop"
        )
        message = {"role": "assistant", "content": text or None}
        if thinking:
            message["reasoning_content"] = thinking
        if signature:
            message["devin_signature"] = signature
        if signature_type:
            message["devin_signature_type"] = signature_type
        if output_id:
            message["devin_output_id"] = output_id
        calls = book.openai_calls()
        if calls:
            message["tool_calls"] = calls
        obj = {
            "id": _completion_id(),
            "object": "chat.completion",
            "created": int(time.time()),
            "model": canonical_fuse_id(model) or fuse_id(model),
            "choices": [{"index": 0, "message": message, "finish_reason": finish}],
            "usage": {
                "prompt_tokens": usage.get("prompt_tokens") or 0,
                "completion_tokens": usage.get("completion_tokens") or 0,
                "total_tokens": usage.get("total_tokens")
                or (usage.get("prompt_tokens") or 0) + (usage.get("completion_tokens") or 0),
            },
        }
        return obj, usage or usage_from_obj(obj), trailer_error
