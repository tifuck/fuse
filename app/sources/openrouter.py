from __future__ import annotations

import json
import math
import time
from datetime import datetime, timezone

import httpx

from .. import accounts, db
from ..config import settings
from ..crypto import encrypt
from ..proxy import openai_chat
from .base import ChatResult

BASE_URL = "https://openrouter.ai/api/v1"
CATALOG_TTL = 600
KEY_TTL = 300
ROLE_GLOBAL = "global"
ROLE_USER = "user"
PUBLIC_PREFIX = "openrouter/"

_catalog: tuple[float, dict] | None = None
_catalog_available = False


def ensure_openrouter_source() -> int:
    row = db.fetchone("SELECT id FROM sources WHERE type='openrouter'")
    if row:
        return row["id"]
    with db.tx() as conn:
        conn.execute(
            "INSERT INTO sources (type, name, enabled, config, created_at) VALUES (?,?,?,?,?)",
            ("openrouter", "OpenRouter", 1, json.dumps({"base_url": BASE_URL}), db.now()),
        )
        return db.last_id(conn)


def _row_get(row, key, default=None):
    try:
        if isinstance(row, dict):
            return row.get(key, default)
        keys = row.keys()
        if key in keys:
            val = row[key]
            return default if val is None else val
    except Exception:
        return default
    return default


def _is_global(cred, extra: dict | None = None) -> bool:
    extra = extra if extra is not None else accounts.cred_extra(cred)
    if extra.get("role") == ROLE_USER:
        return False
    if extra.get("role") == ROLE_GLOBAL:
        return True
    return _row_get(cred, "donated_by") is None


def _headers(key: str) -> dict:
    h = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "X-OpenRouter-Title": "Fuse",
    }
    referer = (settings.public_base_url or "").rstrip("/")
    if referer:
        h["HTTP-Referer"] = referer
    return h


def _as_float(v) -> float | None:
    if v is None or isinstance(v, bool):
        return None
    try:
        value = float(v)
        return value if math.isfinite(value) else None
    except (TypeError, ValueError, OverflowError):
        return None


def _price_positive(pricing: dict, *keys: str) -> bool:
    for key in keys:
        val = _as_float(pricing.get(key))
        if val is not None and val > 0:
            return True
    return False


def _keep_model(item: dict) -> bool:
    mid = str(item.get("id") or "")
    if not mid or "/" not in mid or mid.startswith("~"):
        return False
    if mid in ("openrouter/free", "openrouter/auto"):
        return False
    slug = mid.split("/", 1)[1]
    if ":" in slug and not mid.endswith(":free"):
        return False
    arch = item.get("architecture") if isinstance(item.get("architecture"), dict) else {}
    outputs = arch.get("output_modalities") or ["text"]
    if isinstance(outputs, list) and outputs and "text" not in outputs:
        return False
    return True


def _is_free_item(item: dict) -> bool:
    """OpenRouter's free SKUs: `:free` id or `(free)` name, and $0 prompt/completion/cache."""
    mid = str(item.get("id") or "")
    name = str(item.get("name") or "")
    tagged = mid.endswith(":free") or "(free)" in name.lower()
    if not tagged:
        return False
    pricing = item.get("pricing") if isinstance(item.get("pricing"), dict) else None
    if pricing is None:
        return False
    for key in ("prompt", "completion"):
        value = _as_float(pricing.get(key))
        if value is None or value != 0:
            return False
    for key in ("request", "input_cache_read", "input_cache_write", "cache_read", "cache_write"):
        if key in pricing and _as_float(pricing.get(key)) != 0:
            return False
    if _price_positive(pricing, "prompt", "completion", "request", "input_cache_read"):
        return False
    return True


def _context_length(value) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 1024 else None


def public_id(upstream: str) -> str:
    """Fuse id: openrouter/{author}/{slug} so pool glm-5.2 is never confused with OpenRouter."""
    mid = (upstream or "").strip()
    if mid.startswith(PUBLIC_PREFIX) and mid.count("/") >= 2:
        return mid
    return PUBLIC_PREFIX + mid


def upstream_id(model: str) -> str:
    mid = (model or "").strip()
    if mid.startswith(PUBLIC_PREFIX) and mid.count("/") >= 2:
        return mid[len(PUBLIC_PREFIX) :]
    return mid


def _empty_catalog() -> dict:
    return {"by_id": {}, "free_ids": [], "paid_ids": [], "all_ids": []}


async def fetch_catalog(*, force: bool = False) -> dict:
    global _catalog, _catalog_available
    now = time.time()
    if not force and _catalog and now - _catalog[0] < CATALOG_TTL:
        return _catalog[1]
    catalog = _empty_catalog()
    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            r = await client.get(BASE_URL + "/models", params={"output_modalities": "text"})
            r.raise_for_status()
            payload = r.json()
    except Exception:
        _catalog_available = False
        if _catalog:
            return _catalog[1]
        return catalog
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        _catalog_available = False
        return _catalog[1] if _catalog else catalog
    by_id: dict[str, dict] = {}
    free_ids: list[str] = []
    paid_ids: list[str] = []
    for item in payload.get("data") or []:
        if not isinstance(item, dict) or not _keep_model(item):
            continue
        mid = str(item.get("id") or "")
        free = _is_free_item(item)
        arch = item.get("architecture") if isinstance(item.get("architecture"), dict) else {}
        inputs = arch.get("input_modalities")
        rec = {
            "image": "image" in inputs if isinstance(inputs, list) else None,
            "id": mid,
            "name": str(item.get("name") or mid),
            "free": free,
            "author": mid.split("/", 1)[0],
            "pricing": item.get("pricing") if isinstance(item.get("pricing"), dict) else {},
            "context_length": _context_length(item.get("context_length")),
        }
        by_id[mid] = rec
        (free_ids if free else paid_ids).append(mid)
    catalog = {"by_id": by_id, "free_ids": free_ids, "paid_ids": paid_ids, "all_ids": free_ids + paid_ids}
    if by_id:
        _catalog = (now, catalog)
        _catalog_available = True
    else:
        _catalog_available = False
        if _catalog:
            return _catalog[1]
    return catalog


def catalog_available() -> bool:
    return _catalog_available


def cached_catalog() -> dict | None:
    """In-memory OpenRouter catalog, or None if it has never been fetched."""
    return _catalog[1] if _catalog else None


CATALOG_REFRESH_MIN = 1800.0


async def refresh_due() -> None:
    """Background job: force a catalog fetch when the cached one ages past
    CATALOG_REFRESH_MIN, so newly added or delisted OpenRouter models show up
    without waiting for traffic. Runs from the app refresh loop; no-op while
    the cache is fresh."""
    if _catalog and time.time() - _catalog[0] < CATALOG_REFRESH_MIN:
        return
    await fetch_catalog(force=True)


def model_is_free(model: str, catalog: dict | None = None) -> bool:
    upstream = upstream_id(model)
    if catalog:
        rec = catalog.get("by_id", {}).get(upstream)
        if rec:
            return bool(rec.get("free"))
    return upstream.endswith(":free")


def looks_like_openrouter_id(model: str) -> bool:
    mid = (model or "").strip()
    if not mid or " " in mid:
        return False
    if mid.startswith("explabs/") or mid.startswith("devin/") or mid.startswith("devin-"):
        return False
    if mid.startswith(PUBLIC_PREFIX) and mid.count("/") >= 2:
        return True
    if "/" not in mid or mid.startswith("/"):
        return False
    author, slug = mid.split("/", 1)
    return bool(author) and bool(slug) and author != "openrouter"


def global_credential(source_id: int | None = None):
    sid = source_id or ensure_openrouter_source()
    for row in db.fetchall(
        "SELECT * FROM credentials WHERE source_id=? AND disabled=0 ORDER BY id", (sid,)
    ):
        extra = accounts.cred_extra(row)
        if _is_global(row, extra):
            return row, extra
    return None


def user_credential(user_id: int, source_id: int | None = None):
    if not user_id:
        return None
    sid = source_id or ensure_openrouter_source()
    rows = db.fetchall(
        "SELECT * FROM credentials WHERE source_id=? AND donated_by=? AND disabled=0 ORDER BY id",
        (sid, user_id),
    )
    for row in rows:
        extra = accounts.cred_extra(row)
        if extra.get("role") == ROLE_GLOBAL:
            continue
        return row, extra
    return None


def has_global_key(source_id: int | None = None) -> bool:
    return global_credential(source_id) is not None


def user_has_openrouter(user_id: int) -> bool:
    return user_credential(user_id) is not None


def free_models_enabled(user_id: int | None) -> bool:
    """Free OpenRouter SKUs are listed and directly callable only when the
    user has a key and has not turned free models off."""
    if not user_id:
        return False
    owned = user_credential(user_id)
    return bool(owned) and owned[1].get("free_models") is not False


def set_free_models(user_id: int, enabled: bool) -> bool:
    owned = user_credential(user_id)
    if not owned:
        return False
    cred, extra = owned
    extra["free_models"] = bool(enabled)
    accounts.update_extra(cred["id"], extra)
    return True


def paid_models_pref(user_id: int | None) -> dict:
    """Paid-SKU selection for this account. Default is every paid SKU
    ({"all": True}); turning the bulk switch off lands on {"all": False,
    "enabled": [upstream ids]} where only the listed ids stay available."""
    owned = user_credential(user_id) if user_id else None
    if not owned:
        return {"all": False, "enabled": []}
    raw = owned[1].get("paid_models")
    if not isinstance(raw, dict):
        return {"all": True, "enabled": []}
    enabled = raw.get("enabled")
    clean = (
        [str(mid) for mid in enabled if isinstance(mid, str) and mid.strip()]
        if isinstance(enabled, list)
        else []
    )
    return {"all": bool(raw.get("all")), "enabled": clean}


def paid_model_allowed(user_id: int | None, upstream: str) -> bool:
    pref = paid_models_pref(user_id)
    return pref["all"] or upstream in set(pref["enabled"])


def set_paid_models_all(user_id: int, on: bool) -> bool:
    owned = user_credential(user_id)
    if not owned:
        return False
    cred, extra = owned
    extra["paid_models"] = {"all": bool(on), "enabled": []}
    accounts.update_extra(cred["id"], extra)
    return True


def set_paid_models_enabled(user_id: int, enabled: list) -> bool:
    """Switch the account to an explicit allowlist of paid upstream ids."""
    owned = user_credential(user_id)
    if not owned:
        return False
    clean: list[str] = []
    for mid in enabled or []:
        if not isinstance(mid, str):
            continue
        mid = mid.strip()
        if mid and mid not in clean:
            clean.append(mid)
    cred, extra = owned
    extra["paid_models"] = {"all": False, "enabled": clean[:500]}
    accounts.update_extra(cred["id"], extra)
    return True


def set_paid_model(user_id: int, upstream: str, on: bool) -> bool:
    """Enable or disable one paid SKU on the explicit allowlist."""
    pref = paid_models_pref(user_id)
    if pref["all"]:
        return False
    enabled = [mid for mid in pref["enabled"] if mid != upstream]
    if on:
        if len(enabled) >= 500:
            return False
        enabled.append(upstream)
    return set_paid_models_enabled(user_id, enabled)


# --- Provider routing preferences ------------------------------------------
#
# OpenRouter account settings cannot be changed from here, so Fuse applies the
# user's choices per request through the documented `provider` object:
# `only` (per-model provider allowlist), `data_collection` and `zdr`.
# Fallbacks stay on, so OpenRouter still moves between the allowed providers.

ENDPOINTS_TTL = 3600.0
PROVIDERS_TTL = 86400.0
_ENDPOINTS_MAX = 512
MAX_PREF_MODELS = 200
MAX_PREF_SLUGS = 50
_endpoints: dict[str, tuple[float, list[dict]]] = {}
_providers: tuple[float, dict[str, dict]] | None = None


def provider_prefs(user_id: int | None) -> dict:
    """{"data_collection": "deny"|None, "zdr": bool, "models": {upstream: [slugs]}}"""
    owned = user_credential(user_id) if user_id else None
    raw = owned[1].get("provider_prefs") if owned else None
    raw = raw if isinstance(raw, dict) else {}
    models = raw.get("models") if isinstance(raw.get("models"), dict) else {}
    clean = {
        str(mid): [str(s) for s in slugs if isinstance(s, str) and s][:MAX_PREF_SLUGS]
        for mid, slugs in models.items()
        if isinstance(slugs, list) and slugs
    }
    return {
        "data_collection": "deny" if raw.get("data_collection") == "deny" else None,
        "zdr": raw.get("zdr") is True,
        "models": clean,
    }


def _save_provider_prefs(user_id: int, prefs: dict) -> bool:
    owned = user_credential(user_id)
    if not owned:
        return False
    cred, extra = owned
    extra["provider_prefs"] = prefs
    accounts.update_extra(cred["id"], extra)
    return True


def set_privacy(user_id: int, *, data_collection_deny: bool, zdr: bool) -> bool:
    prefs = provider_prefs(user_id)
    prefs["data_collection"] = "deny" if data_collection_deny else None
    prefs["zdr"] = bool(zdr)
    return _save_provider_prefs(user_id, prefs)


def set_model_providers(user_id: int, upstream: str, slugs: list[str]) -> bool:
    """Empty slugs restores OpenRouter's default routing for that model."""
    prefs = provider_prefs(user_id)
    clean = list(dict.fromkeys(s.strip() for s in slugs if isinstance(s, str) and s.strip()))[:MAX_PREF_SLUGS]
    if clean:
        if upstream not in prefs["models"] and len(prefs["models"]) >= MAX_PREF_MODELS:
            return False
        prefs["models"][upstream] = clean
    else:
        prefs["models"].pop(upstream, None)
    return _save_provider_prefs(user_id, prefs)


def _provider_routing(prefs: dict | None, upstream: str) -> dict:
    if not prefs:
        return {}
    out: dict = {}
    only = (prefs.get("models") or {}).get(upstream)
    if only:
        out["only"] = list(only)
    if prefs.get("data_collection") == "deny":
        out["data_collection"] = "deny"
    if prefs.get("zdr"):
        out["zdr"] = True
    return out


async def fetch_providers() -> dict[str, dict]:
    """Provider directory keyed by display name: slug, privacy policy, HQ."""
    global _providers
    now = time.time()
    if _providers and now - _providers[0] < PROVIDERS_TTL:
        return _providers[1]
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            r = await client.get(BASE_URL + "/providers")
            r.raise_for_status()
            data = r.json().get("data")
    except Exception:
        return _providers[1] if _providers else {}
    out: dict[str, dict] = {}
    for item in data if isinstance(data, list) else []:
        if isinstance(item, dict) and item.get("slug") and item.get("name"):
            out[str(item["name"])] = {
                "slug": str(item["slug"]),
                "privacy_url": str(item.get("privacy_policy_url") or ""),
                "headquarters": str(item.get("headquarters") or ""),
            }
    if out:
        _providers = (now, out)
    return out


def _per_million(value) -> float | None:
    rate = _as_float(value)
    if rate is None or rate < 0:  # "-1" marks variable router pricing
        return None
    return round(rate * 1_000_000, 4)


def _parse_endpoints(items, directory: dict[str, dict]) -> list[dict]:
    """One row per routable provider slug; the cheapest endpoint leads."""
    by_slug: dict[str, dict] = {}
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("provider_name") or "")
        tag = str(item.get("tag") or "")
        known = directory.get(name) or {}
        slug = known.get("slug") or tag.split("/", 1)[0]
        if not slug:
            continue
        pricing = item.get("pricing") if isinstance(item.get("pricing"), dict) else {}
        variant = {
            "tag": tag,
            "quantization": str(item.get("quantization") or "") if item.get("quantization") not in (None, "unknown") else "",
            "context": _context_length(item.get("context_length")),
            "input": _per_million(pricing.get("prompt")),
            "output": _per_million(pricing.get("completion")),
            "cache_read": _per_million(pricing.get("input_cache_read")),
            "uptime": _as_float(item.get("uptime_last_30m")),
            "up": item.get("status") in (0, None),
        }
        row = by_slug.setdefault(
            slug,
            {
                "slug": slug,
                "name": name or slug,
                "privacy_url": known.get("privacy_url") or "",
                "headquarters": known.get("headquarters") or "",
                "variants": [],
            },
        )
        row["variants"].append(variant)
    rows = list(by_slug.values())
    for row in rows:
        row["variants"].sort(key=lambda v: (v["input"] is None, v["input"] or 0, v["output"] or 0))
        lead = row["variants"][0]
        row.update({k: lead[k] for k in ("input", "output", "cache_read", "context", "quantization")})
        row["up"] = any(v["up"] for v in row["variants"])
        uptimes = [v["uptime"] for v in row["variants"] if v["uptime"] is not None]
        row["uptime"] = round(max(uptimes), 1) if uptimes else None
    rows.sort(key=lambda r: (r["input"] is None, (r["input"] or 0) + (r["output"] or 0), r["name"].lower()))
    return rows


async def fetch_model_endpoints(upstream: str) -> list[dict] | None:
    """Providers serving one model with their own prices. Shared in-process
    cache, so a model is fetched at most once per ENDPOINTS_TTL for everyone.
    None means OpenRouter could not be reached and nothing is cached."""
    now = time.time()
    hit = _endpoints.get(upstream)
    if hit and now - hit[0] < ENDPOINTS_TTL:
        return hit[1]
    directory = await fetch_providers()
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            r = await client.get(f"{BASE_URL}/models/{upstream}/endpoints")
            r.raise_for_status()
            data = r.json().get("data")
    except Exception:
        return hit[1] if hit else None
    rows = _parse_endpoints((data or {}).get("endpoints") if isinstance(data, dict) else None, directory)
    if len(_endpoints) >= _ENDPOINTS_MAX and upstream not in _endpoints:
        _endpoints.pop(min(_endpoints, key=lambda k: _endpoints[k][0]), None)
    _endpoints[upstream] = (now, rows)
    return rows


def openrouter_access_state(user_id: int) -> dict:
    if user_has_openrouter(user_id):
        pref = paid_models_pref(user_id)
        return {
            "ok": True,
            "reason": "user",
            "user_key": True,
            "global_key": False,
            "free_models": free_models_enabled(user_id),
            "paid_models_all": pref["all"],
            "paid_models_count": 0 if pref["all"] else len(pref["enabled"]),
        }
    return {
        "ok": False,
        "reason": "none",
        "user_key": False,
        "global_key": False,
        "free_models": False,
        "paid_models_all": False,
        "paid_models_count": 0,
    }


def _key_from(cred, extra: dict) -> str:
    return extra.get("api_key") or accounts.cred_secret(cred)


def pick_key_for(user_id: int | None, source_id: int | None = None):
    """Return (cred, extra, key, via) or None. via is 'user'."""
    if not user_id:
        return None
    owned = user_credential(user_id, source_id)
    if not owned:
        return None
    cred, extra = owned
    key = _key_from(cred, extra)
    if not key:
        return None
    return cred, extra, key, "user"


async def fetch_key_info(key: str) -> tuple[dict | None, str]:
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            r = await client.get(BASE_URL + "/key", headers=_headers(key))
    except Exception:
        return None, "Could not reach OpenRouter."
    if r.status_code in (401, 403):
        return None, "Invalid API key."
    if r.status_code != 200:
        return None, f"http {r.status_code}"
    try:
        payload = r.json()
    except json.JSONDecodeError:
        return None, "invalid JSON"
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        return None, "invalid JSON"
    return {
        "label": str(data.get("label") or "")[:80],
        "limit": _as_float(data.get("limit")),
        "limit_remaining": _as_float(data.get("limit_remaining")),
        "usage": _as_float(data.get("usage")) or 0,
        "usage_daily": _as_float(data.get("usage_daily")) or 0,
        "is_free_tier": bool(data.get("is_free_tier")),
        "fetched_at": db.now(),
        "last_error": "",
    }, ""


def _key_info_fresh(info: dict) -> bool:
    fetched = info.get("fetched_at")
    if not fetched:
        return False
    raw = str(fetched).replace("Z", "")
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f"):
        try:
            when = datetime.strptime(raw.split("+")[0], fmt).replace(tzinfo=timezone.utc)
            return (datetime.now(timezone.utc) - when).total_seconds() < KEY_TTL
        except ValueError:
            continue
    return False


def _store_key_info(cred_id: int, info: dict, *, error: str = "") -> dict:
    row = db.fetchone("SELECT * FROM credentials WHERE id=?", (cred_id,))
    extra = accounts.cred_extra(row) if row else {}
    stored = extra.get("key_info") if isinstance(extra.get("key_info"), dict) else {}
    stored["checked_at"] = db.now()
    if error:
        stored["last_error"] = error[:200]
        extra["key_info"] = stored
        if row:
            accounts.update_extra(cred_id, extra)
        return stored
    stored.update(info)
    stored["last_error"] = ""
    extra["key_info"] = stored
    if row:
        accounts.update_extra(cred_id, extra)
    return stored


async def refresh_key_info(cred, *, force: bool = False) -> dict | None:
    row = db.fetchone("SELECT * FROM credentials WHERE id=?", (cred["id"],))
    if not row or row["disabled"]:
        return None
    extra = accounts.cred_extra(row)
    info = extra.get("key_info") if isinstance(extra.get("key_info"), dict) else {}
    if not force and _key_info_fresh(info) and info.get("fetched_at"):
        return info
    key = _key_from(row, extra)
    if not key:
        return _store_key_info(row["id"], {}, error="missing key")
    parsed, err = await fetch_key_info(key)
    if parsed is None:
        return _store_key_info(row["id"], {}, error=err or "fetch failed")
    return _store_key_info(row["id"], parsed)


def _find_key(source_id: int, key: str):
    for row in db.fetchall("SELECT * FROM credentials WHERE source_id=?", (source_id,)):
        try:
            extra = accounts.cred_extra(row)
            if _key_from(row, extra) == key:
                return row, extra
        except Exception:
            continue
    return None


def clear_global_key(source_id: int | None = None) -> None:
    """Disable leftover site-wide OpenRouter keys. OpenRouter is per-account only."""
    sid = source_id or ensure_openrouter_source()
    for row in db.fetchall("SELECT * FROM credentials WHERE source_id=? AND disabled=0", (sid,)):
        extra = accounts.cred_extra(row)
        if _is_global(row, extra):
            db.execute("UPDATE credentials SET disabled=1 WHERE id=?", (row["id"],))


async def add_user_key(source_id: int, raw: str, donated_by: int, *, label: str = "") -> tuple[int | None, str]:
    key = (raw or "").strip()
    if not key or len(key) < 8 or len(key) > 4096:
        return None, "Enter an OpenRouter API key."
    if any(ord(ch) < 32 for ch in key):
        return None, "Enter an OpenRouter API key."
    info, err = await fetch_key_info(key)
    if err or info is None:
        return None, err or "Could not validate this key."
    found = _find_key(source_id, key)
    if found:
        row, extra = found
        if extra.get("role") == ROLE_GLOBAL or _is_global(row, extra):
            found = None
        else:
            owner = _row_get(row, "donated_by")
            if owner and int(owner) != int(donated_by) and not row["disabled"]:
                return None, "That key is already linked to another account."
    name = (label or "").strip()[:80] or info.get("label") or "OpenRouter key"
    extra = {
        "api_key": key,
        "role": ROLE_USER,
        "key_info": info,
    }
    existing = user_credential(donated_by, source_id)
    if existing:
        cred, prev = existing
        prev.update(extra)
        db.execute(
            """UPDATE credentials SET disabled=0, donated_by=?, label=?, secret=?, extra=? WHERE id=?""",
            (donated_by, name, encrypt(key), encrypt(json.dumps(prev)), cred["id"]),
        )
        return int(cred["id"]), ""
    if found and found[0]["disabled"]:
        cred = found[0]
        db.execute(
            """UPDATE credentials SET disabled=0, donated_by=?, label=?, secret=?, extra=? WHERE id=?""",
            (donated_by, name, encrypt(key), encrypt(json.dumps(extra)), cred["id"]),
        )
        return int(cred["id"]), ""
    return accounts.add_credential(source_id, name, key, extra=extra, donated_by=donated_by), ""


def disable_credential(cred_id: int) -> None:
    db.execute("UPDATE credentials SET disabled=1 WHERE id=?", (cred_id,))


def _is_auth_failure(status: int, obj) -> bool:
    if status == 401:
        return True
    if status != 403 or not isinstance(obj, dict):
        return False
    err = obj.get("error")
    text = json.dumps(err if err is not None else obj, ensure_ascii=True).lower()[:1000]
    return any(marker in text for marker in ("invalid api key", "invalid_api_key", "authentication", "unauthorized"))


def models_for_user(user_id: int | None, catalog: dict | None = None) -> list[str]:
    """Only enabled SKUs are ever listed: free ones while the free toggle is
    on, paid ones per the account's paid-models selection (all by default)."""
    catalog = catalog or (_catalog[1] if _catalog else _empty_catalog())
    if not user_id or not user_has_openrouter(user_id):
        return []
    out: list[str] = []
    if free_models_enabled(user_id):
        out.extend(public_id(mid) for mid in catalog.get("free_ids") or [])
    pref = paid_models_pref(user_id)
    if pref["all"]:
        out.extend(public_id(mid) for mid in catalog.get("paid_ids") or [])
    else:
        want = set(pref["enabled"])
        out.extend(public_id(mid) for mid in catalog.get("paid_ids") or [] if mid in want)
    return out


def denied_reason(model: str, user_id: int | None, catalog: dict | None = None) -> str | None:
    """If this is a known OpenRouter model the user cannot call, say why."""
    catalog = catalog or (_catalog[1] if _catalog else None)
    if not catalog:
        return None
    if not looks_like_openrouter_id(model):
        # Pool ids never run on a personal key; point at the explicit SKU.
        for author in ("z-ai", "x-ai", "openai"):
            if f"{author}/{model}" in (catalog.get("by_id") or {}):
                return (
                    f"{model} runs on its subscription pool, which is not connected for your account. "
                    f"With an OpenRouter key, pick openrouter/{author}/{model} instead."
                )
        return None
    rec = (catalog.get("by_id") or {}).get(upstream_id(model))
    if not rec:
        return None
    if not (user_id and user_has_openrouter(user_id)):
        return "Connect an OpenRouter API key to use OpenRouter models."
    if rec.get("free"):
        if not free_models_enabled(user_id):
            return "Free OpenRouter models are turned off for this account."
    elif not paid_model_allowed(user_id, upstream_id(model)):
        return "That paid OpenRouter model is not enabled for your account. Manage the list on the Sources page."
    return None


def _chat_payload(
    body: dict, upstream: str, *, free: bool, session_id: str | None = None, prefs: dict | None = None
) -> dict:
    payload = dict(body)
    payload["model"] = upstream
    payload.pop("models", None)
    effort = payload.pop("reasoning_effort", None)
    if isinstance(effort, str):
        reasoning = dict(payload["reasoning"]) if isinstance(payload.get("reasoning"), dict) else {}
        reasoning["effort"] = effort
        payload["reasoning"] = reasoning
    # OpenRouter's own sticky-routing knob. The scoped Fuse id namespaces the
    # client's key per user+model so shared/global keys can't cross-group.
    payload.pop("session_id", None)
    if session_id:
        payload["session_id"] = session_id[:256]
    caller_provider = payload.get("provider")
    provider = dict(caller_provider) if isinstance(caller_provider, dict) else {}
    for key, value in _provider_routing(prefs, upstream).items():
        provider.setdefault(key, value)
    if free:
        max_price = dict(provider.get("max_price")) if isinstance(provider.get("max_price"), dict) else {}
        max_price["prompt"] = 0
        max_price["completion"] = 0
        provider["max_price"] = max_price
        provider["allow_fallbacks"] = True
    elif session_id:
        # A fallback provider has a cold cache. OpenRouter's own sticky routing
        # still moves off this provider if it is down.
        provider["allow_fallbacks"] = False
    else:
        provider.setdefault("allow_fallbacks", True)
    payload["provider"] = provider
    return payload


class OpenRouterSource:
    type_id = "openrouter"

    def __init__(self, row, cfg: dict, creds):
        self.source_id = row["id"]
        self.name = row["name"]
        self.cfg = cfg
        self.creds = list(creds)

    async def models(self, *, user_id: int | None = None) -> list[str]:
        catalog = cached_catalog()
        if catalog is None:
            catalog = await fetch_catalog()
        return models_for_user(user_id, catalog)

    async def chat(self, body: dict, *, user_id: int | None = None, session_id: str | None = None) -> ChatResult:
        catalog = await fetch_catalog()
        requested = str(body.get("model") or "")
        upstream = upstream_id(requested)
        rec = catalog.get("by_id", {}).get(upstream)
        free = bool(rec and rec.get("free")) or model_is_free(upstream, catalog)
        picked = pick_key_for(user_id, source_id=self.source_id)
        if picked is None:
            return ChatResult(
                status=403,
                body={"error": "Connect an OpenRouter API key to use OpenRouter models."},
                model=upstream,
            )
        cred, extra, key, _via = picked
        payload = _chat_payload(
            body, upstream, free=free, session_id=session_id, prefs=provider_prefs(user_id)
        )
        client = httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=15.0))
        status, obj, stream, usage, response_headers = await openai_chat(
            client,
            BASE_URL + "/chat/completions",
            _headers(key),
            payload,
            close_client=True,
        )
        if stream is not None:
            return ChatResult(status=200, stream=stream, credential_id=cred["id"], model=upstream)
        if _is_auth_failure(status, obj):
            disable_credential(cred["id"])
            status = 502
            obj = {"error": {"message": "OpenRouter rejected the configured API key"}}
        return ChatResult(
            status=status,
            body=obj,
            usage=usage,
            credential_id=cred["id"],
            model=upstream,
            response_headers=response_headers,
        )


async def resolve(model: str, user_id: int | None) -> tuple[str, bool] | None:
    """Return (upstream_id, free) for a directly callable OpenRouter slug.

    Free SKUs are callable while the user has free models enabled. Paid SKUs
    are callable per the account's paid-models selection (all by default,
    else the explicit allowlist) — usage bills to that user's key. Only
    enabled ids are listed (models_for_user)."""
    if not looks_like_openrouter_id(model):
        return None
    catalog = await fetch_catalog()
    upstream = upstream_id(model)
    rec = (catalog.get("by_id") or {}).get(upstream)
    if rec is None:
        return None
    if not (user_id and user_has_openrouter(user_id)):
        return None
    free = bool(rec.get("free"))
    if free and not free_models_enabled(user_id):
        return None
    if not free and not paid_model_allowed(user_id, upstream):
        return None
    return upstream, free
