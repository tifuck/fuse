from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Callable

from .. import db
from ..config import settings
from .base import ChatResult, Source
from .chatgpt import CHATGPT_MODELS, ChatGPTSource, ensure_chatgpt_source, refresh_due as chatgpt_refresh_due, user_has_chatgpt
from .grok import GROK_MODELS, GrokSource, ensure_grok_source, grok_access_state, refresh_due, user_has_grok
from .llama import LlamaSource
from .openai_compat import OpenAICompatSource
from .ollama import OllamaSource
from .openrouter import OpenRouterSource, ensure_openrouter_source, user_has_openrouter
from .explabs import ExperientialLabsSource, ensure_explabs_source
from .zai import ZAI_MODELS, ZaiSource, ensure_zai_source, user_has_zai
from .devin import DEVIN_MODELS, DevinSource, ensure_devin_source, user_has_devin
from .kimi import KIMI_MODELS, KimiSource, ensure_kimi_source, user_has_kimi
from .mimo import MIMO_MODELS, MimoSource, ensure_mimo_source, user_has_mimo
from .claude import CLAUDE_MODELS, ClaudeSource, ensure_claude_source, user_has_claude

logger = logging.getLogger(__name__)

_BUILDERS: dict[str, Callable] = {
    "grok": GrokSource,
    "llama": LlamaSource,
    "openai": OpenAICompatSource,
    "ollama": OllamaSource,
    "chatgpt": ChatGPTSource,
    "zai": ZaiSource,
    "devin": DevinSource,
    "kimi": KimiSource,
    "mimo": MimoSource,
    "claude": ClaudeSource,
    "openrouter": OpenRouterSource,
    "explabs": ExperientialLabsSource,
}

TYPES = {
    "llama": "Local llama.cpp",
    "openai": "OpenAI-compatible",
    "ollama": "Ollama Cloud",
    "grok": "Grok pool",
    "chatgpt": "ChatGPT pool",
    "zai": "Z.AI pool",
    "devin": "Devin pool",
    "kimi": "Kimi pool",
    "mimo": "Xiaomi pool",
    "claude": "Claude pool",
    "openrouter": "OpenRouter",
    "explabs": "Experiential Labs",
}

PROVIDER_LABELS = {
    "grok": "x.ai",
    "llama": "llama.cpp",
    "ollama": "Ollama Cloud",
    "openai": "OpenAI-compatible",
    "chatgpt": "ChatGPT",
    "zai": "Z.AI",
    "devin": "Devin",
    "kimi": "Kimi",
    "mimo": "Xiaomi",
    "claude": "Anthropic",
    "openrouter": "OpenRouter",
    "explabs": "Experiential Labs",
    "unknown": "Unknown",
}

POOL_TYPES = frozenset({"grok", "chatgpt", "zai", "devin", "kimi", "mimo", "claude"})
_SEEDED_MODELS = {
    "grok": GROK_MODELS,
    "chatgpt": CHATGPT_MODELS,
    "zai": ZAI_MODELS,
    "devin": DEVIN_MODELS,
    "kimi": KIMI_MODELS,
    "mimo": MIMO_MODELS,
    "claude": CLAUDE_MODELS,
}


def _row_source(row) -> Source:
    cfg = json.loads(row["config"] or "{}")
    if not isinstance(cfg, dict):
        raise ValueError("source config must be a JSON object")
    creds = db.fetchall(
        "SELECT * FROM credentials WHERE source_id=? AND disabled=0", (row["id"],)
    )
    return _BUILDERS[row["type"]](row, cfg, creds)


def all_enabled() -> list[Source]:
    rows = db.fetchall("SELECT * FROM sources WHERE enabled=1 ORDER BY id")
    out = []
    for row in rows:
        if row["type"] in _BUILDERS:
            try:
                out.append(_row_source(row))
            except Exception as exc:
                logger.warning(
                    "Skipping malformed source id=%s type=%s: %s",
                    row["id"],
                    row["type"],
                    type(exc).__name__,
                )
    return out


def get(source_id: int) -> Source | None:
    row = db.fetchone("SELECT * FROM sources WHERE id=?", (source_id,))
    if not row or row["type"] not in _BUILDERS or not row["enabled"]:
        return None
    try:
        return _row_source(row)
    except Exception as exc:
        logger.warning("Could not load source id=%s: %s", source_id, type(exc).__name__)
        return None


_cache: dict[int, tuple[float, list[str]]] = {}
_MODEL_TTL = 300.0
_model_cache: dict[tuple[int, int | None], tuple[float, list[str]]] = {}
_unavailable_types: set[str] = set()
_refreshing: dict[tuple[int, int | None], asyncio.Task] = {}
# Keys whose live catalog has loaded, or whose first load was already waited
# on once. A failing provider must not stall every page for the full wait.
_live: set[tuple[int, int | None]] = set()
# How long a page waits for a pool's first live catalog before showing seeds.
_FIRST_FETCH_WAIT = 4.0


def _cache_key(s: Source, user_id: int | None) -> tuple[int, int | None]:
    return (s.source_id, user_id) if s.type_id == "openrouter" else (s.source_id, None)


def _sticky_names(key: tuple[int, int | None], names: list[str]) -> list[str]:
    """Pool catalogs only grow. A short or failed upstream list must not drop a
    model the picker already showed. The upstream order leads, so a new model
    sits where the provider lists it rather than after every older id."""
    prev = _model_cache.get(key)
    if not prev:
        return names
    merged = list(names)
    for name in prev[1]:
        if name not in merged:
            merged.append(name)
    return merged


def _store_models(key: tuple[int, int | None], names: list[str], *, sticky: bool = False) -> None:
    if sticky:
        names = _sticky_names(key, names)
    now = time.time()
    _model_cache[key] = (now, names)
    _cache[key[0]] = (now, names)


async def _refresh_source_models(s: Source, user_id: int | None) -> list[str]:
    key = _cache_key(s, user_id)
    hit = _model_cache.get(key)
    try:
        empty_ok = False
        if s.type_id == "openrouter":
            m = await s.models(user_id=user_id)
            from . import openrouter as openrouter_mod

            stale_only = not openrouter_mod.catalog_available()
            empty_ok = not (user_id and openrouter_mod.user_has_openrouter(user_id))
        else:
            m = await s.models()
            stale_only = False
            empty_ok = s.type_id in POOL_TYPES
        if not isinstance(m, list) or (not m and not empty_ok):
            raise ValueError("empty or invalid model catalog")
        m = [name for name in m if isinstance(name, str) and name]
        if not m and not empty_ok:
            raise ValueError("empty model catalog")
    except Exception as exc:
        _unavailable_types.add(s.type_id)
        logger.warning("Model catalog unavailable for source id=%s: %s", s.source_id, type(exc).__name__)
        return hit[1] if hit else []
    if stale_only:
        _unavailable_types.add(s.type_id)
    else:
        _unavailable_types.discard(s.type_id)
    _store_models(key, m, sticky=s.type_id in POOL_TYPES)
    _live.add(key)
    return _model_cache[key][1]


def _schedule_model_refresh(s: Source, user_id: int | None) -> asyncio.Task | None:
    key = _cache_key(s, user_id)
    running = _refreshing.get(key)
    if running is not None:
        return running
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return None

    async def _run():
        try:
            await _refresh_source_models(s, user_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("Background model catalog refresh failed for source id=%s", s.source_id, exc_info=True)
        finally:
            _refreshing.pop(key, None)

    task = loop.create_task(_run())
    _refreshing[key] = task
    return task


async def _await_first_fetch(s: Source, user_id: int | None) -> None:
    """Give a pool still on its seed list a moment to load the live catalog.

    Without this, the first page after every restart showed only the seeds
    (no Sonnet 5.5, no gpt-6.1-sol) and the live list appeared on reload.
    """
    task = _schedule_model_refresh(s, user_id)
    if task is None:
        return
    try:
        await asyncio.wait_for(asyncio.shield(task), _FIRST_FETCH_WAIT)
    except (asyncio.TimeoutError, Exception):
        pass
    _live.add(_cache_key(s, user_id))


def _fallback_models(s: Source) -> list[str]:
    seeded = _SEEDED_MODELS.get(s.type_id)
    if seeded:
        return list(seeded)
    cfg = getattr(s, "cfg", None) or {}
    named = str(cfg.get("model") or "").strip()
    if named and named not in ("local", "llama"):
        return [named]
    return []


async def _source_models(s: Source, user_id: int | None) -> list[str]:
    if s.type_id == "explabs":
        # Private catalogs have credential revisions and never enter shared caches.
        from .explabs import CatalogError

        try:
            return await s.models(user_id=user_id)
        except CatalogError:
            return []
    key = _cache_key(s, user_id)
    hit = _model_cache.get(key)
    if hit and key not in _live and s.type_id in POOL_TYPES:
        await _await_first_fetch(s, user_id)
        hit = _model_cache.get(key) or hit
    if hit:
        if time.time() - hit[0] >= _MODEL_TTL:
            _schedule_model_refresh(s, user_id)
        return hit[1]
    fallback = _fallback_models(s)
    if fallback:
        await _await_first_fetch(s, user_id)
        hit = _model_cache.get(key)
        return hit[1] if hit else fallback
    return await _refresh_source_models(s, user_id)


async def _user_source_models(s: Source, user_id: int | None) -> list[str]:
    names = await _source_models(s, user_id)
    if s.type_id == "chatgpt":
        from . import chatgpt as chatgpt_mod

        return chatgpt_mod.listable_models(names, user_id)
    return names


# Picker efforts per provider. Providers without their own list get every
# OpenAI-style level; GENERIC_EFFORTS marks that as a guess, not a capability.
GENERIC_EFFORTS = ("none", "minimal", "low", "medium", "high", "xhigh", "max")


def reasoning_efforts(type_id: str, model: str | None = None, user_id: int | None = None) -> tuple[str, ...]:
    from . import claude as claude_mod, devin as devin_mod, explabs as explabs_mod, kimi as kimi_mod

    if type_id == "explabs":
        catalog = explabs_mod.cached_catalog(user_id) or {}
        record = catalog.get("by_id", {}).get(explabs_mod.upstream_id(model or "")) or {}
        return tuple(record.get("reasoning_efforts") or [])
    if type_id == "devin":
        return devin_mod.efforts_for(model) if model else devin_mod.SEEDED_EFFORTS
    if type_id == "grok":
        return ("low", "medium", "high", "xhigh")
    if type_id == "zai":
        return ("none", "high")
    if type_id == "kimi":
        return kimi_mod.efforts_for(model)
    if type_id == "mimo":
        return ("none", "high")
    if type_id == "claude":
        return claude_mod.efforts_for(model)
    if type_id == "ollama":
        return ("none", "low", "medium", "high", "max")
    return GENERIC_EFFORTS


def metadata_effort(type_id: str, model: str | None = None, user_id: int | None = None) -> str | None:
    """Cheapest effort a provider accepts, for short title/recap calls.

    None means send no effort: the provider's levels are unknown (the generic
    list) and a guess like "none" is a 400 on Codex and Grok."""
    efforts = reasoning_efforts(type_id, model, user_id)
    if not efforts or efforts == GENERIC_EFFORTS:
        return None
    return "none" if "none" in efforts else efforts[0]


def invalidate_model_cache(source_type: str | None = None) -> None:
    """Drop cached model lists, e.g. after a per-user key or preference change."""
    if source_type is None:
        _model_cache.clear()
        _cache.clear()
        _live.clear()
        return
    sids = [s.source_id for s in all_enabled() if s.type_id == source_type]
    for sid in sids:
        _cache.pop(sid, None)
    for key in [k for k in _model_cache if k[0] in sids]:
        _model_cache.pop(key, None)
        _live.discard(key)


def catalog_unavailable_for(model: str) -> bool:
    if model.startswith("explabs/"):
        return False
    if model.startswith("openrouter/") or ("/" in model and not model.startswith("/")):
        return "openrouter" in _unavailable_types
    if model.startswith("grok"):
        return "grok" in _unavailable_types
    if model.startswith("glm-"):
        return "zai" in _unavailable_types
    if model.startswith("gpt-"):
        return "chatgpt" in _unavailable_types
    if model.startswith("devin/") or model.startswith("devin-"):
        return "devin" in _unavailable_types
    if model in KIMI_MODELS or model.startswith("kimi-for-coding") or model == "k3" or model.startswith("k3-"):
        return "kimi" in _unavailable_types
    if model in MIMO_MODELS or model.startswith("mimo-"):
        return "mimo" in _unavailable_types
    if model.startswith("claude/"):
        return "claude" in _unavailable_types
    return bool(_unavailable_types)


def _sources(
    *,
    allow_grok: bool,
    allow_chatgpt: bool = False,
    allow_zai: bool = False,
    allow_devin: bool = False,
    allow_kimi: bool = False,
    allow_mimo: bool = False,
    allow_claude: bool = False,
) -> list[Source]:
    return [
        s
        for s in all_enabled()
        if (allow_grok or s.type_id != "grok")
        and (allow_chatgpt or s.type_id != "chatgpt")
        and (allow_zai or s.type_id != "zai")
        and (allow_devin or s.type_id != "devin")
        and (allow_kimi or s.type_id != "kimi")
        and (allow_mimo or s.type_id != "mimo")
        and (allow_claude or s.type_id != "claude")
    ]


def _openrouter_source() -> Source | None:
    for s in all_enabled():
        if s.type_id == "openrouter":
            return s
    return None


def _seed_pool_catalogs() -> None:
    stale = time.time() - _MODEL_TTL - 1
    for s in all_enabled():
        names = _SEEDED_MODELS.get(s.type_id)
        if not names:
            continue
        key = _cache_key(s, None)
        if key not in _model_cache:
            _model_cache[key] = (stale, list(names))
            _cache[s.source_id] = (stale, list(names))


async def models_union(
    *,
    allow_grok: bool = False,
    allow_chatgpt: bool = False,
    allow_zai: bool = False,
    allow_devin: bool = False,
    allow_kimi: bool = False,
    allow_mimo: bool = False,
    allow_claude: bool = False,
    user_id: int | None = None,
) -> list[str]:
    sources = _sources(
        allow_grok=allow_grok,
        allow_chatgpt=allow_chatgpt,
        allow_zai=allow_zai,
        allow_devin=allow_devin,
        allow_kimi=allow_kimi,
        allow_mimo=allow_mimo,
        allow_claude=allow_claude,
    )
    results = await asyncio.gather(*(_user_source_models(s, user_id) for s in sources))
    names: list[str] = []
    for m in results:
        for n in m:
            if n not in names:
                names.append(n)
    return names


async def pick(
    model: str,
    *,
    allow_grok: bool = False,
    allow_chatgpt: bool = False,
    allow_zai: bool = False,
    allow_devin: bool = False,
    allow_kimi: bool = False,
    allow_mimo: bool = False,
    allow_claude: bool = False,
    user_id: int | None = None,
) -> tuple[Source, str] | None:
    from . import openrouter as openrouter_mod
    from . import chatgpt as chatgpt_mod
    from . import devin as devin_mod
    from . import kimi as kimi_mod
    from . import mimo as mimo_mod
    from . import claude as claude_mod

    from . import explabs as explabs_mod

    if model.startswith(explabs_mod.PUBLIC_PREFIX):
        for src in all_enabled():
            if src.type_id == "explabs":
                resolved = await explabs_mod.resolve(model, user_id)
                return (src, resolved) if resolved else None
        return None
    route = db.fetchone("SELECT * FROM model_routes WHERE model=?", (model,))
    if route:
        src = get(route["source_id"])
        if src and src.type_id != "explabs" and (allow_grok or src.type_id != "grok") and (allow_chatgpt or src.type_id != "chatgpt") and (allow_zai or src.type_id != "zai") and (allow_devin or src.type_id != "devin") and (allow_kimi or src.type_id != "kimi") and (allow_mimo or src.type_id != "mimo") and (allow_claude or src.type_id != "claude"):
            return src, route["upstream_model"] or model
    # Devin-prefixed ids never leave this pool — including GPT/Astra/Claude
    # slugs that would otherwise match ChatGPT or look like OpenRouter.
    if devin_mod.is_devin_model(model):
        if allow_devin:
            for s in all_enabled():
                if s.type_id == "devin":
                    return s, devin_mod.listed_upstream(model)
        return None
    if kimi_mod.is_kimi_model(model):
        if allow_kimi:
            for s in all_enabled():
                if s.type_id == "kimi":
                    return s, model
        return None
    if mimo_mod.is_mimo_model(model):
        if allow_mimo:
            for s in all_enabled():
                if s.type_id == "mimo":
                    return s, model
        return None
    if claude_mod.is_claude_model(model):
        if allow_claude:
            for s in all_enabled():
                if s.type_id == "claude":
                    return s, claude_mod.upstream_id(model)
        return None
    if allow_grok and model.startswith("grok"):
        for s in all_enabled():
            if s.type_id == "grok":
                return s, model
    # Live-listed slugs count too: a new Codex model (gpt-6.1-sol) must stay
    # on the pool, not fall through to the user's OpenRouter key below.
    if allow_chatgpt and chatgpt_mod.is_chatgpt_model(model):
        for s in all_enabled():
            if s.type_id == "chatgpt":
                # `{slug}-fast` is the same Codex slug at service_tier fast.
                return s, chatgpt_mod.base_slug(model)
    if allow_zai and (model in ZAI_MODELS or model.startswith("glm-")):
        for s in all_enabled():
            if s.type_id == "zai":
                return s, model
    or_src = _openrouter_source()
    if or_src:
        resolved = await openrouter_mod.resolve(model, user_id)
        if resolved:
            return or_src, resolved[0]
    await models_union(
        allow_grok=allow_grok,
        allow_chatgpt=allow_chatgpt,
        allow_zai=allow_zai,
        allow_devin=allow_devin,
        allow_kimi=allow_kimi,
        allow_mimo=allow_mimo,
        allow_claude=allow_claude,
        user_id=user_id,
    )
    for s in _sources(
        allow_grok=allow_grok,
        allow_chatgpt=allow_chatgpt,
        allow_zai=allow_zai,
        allow_devin=allow_devin,
        allow_kimi=allow_kimi,
        allow_mimo=allow_mimo,
        allow_claude=allow_claude,
    ):
        if s.type_id == "explabs":
            continue
        hit = _cache.get(s.source_id)
        if hit and model in hit[1]:
            if s.type_id == "devin":
                return s, devin_mod.listed_upstream(model)
            if s.type_id == "claude":
                return s, claude_mod.upstream_id(model)
            return s, model
    for s in _sources(allow_grok=False):
        if s.type_id != "llama":
            continue
        cfg = getattr(s, "cfg", None) or {}
        named = str(cfg.get("model") or "")
        if named and named not in ("local", "llama") and model == named:
            return s, named
    others = [s for s in _sources(allow_grok=False) if s.type_id == "llama"]
    if len(others) == 1 and len(_sources(allow_grok=False)) == 1:
        return others[0], model
    return None


def _ensure_typed_source(type_id: str, name: str, config: dict) -> int:
    row = db.fetchone("SELECT id FROM sources WHERE type=?", (type_id,))
    if row:
        return row["id"]
    with db.tx() as conn:
        conn.execute(
            "INSERT INTO sources (type, name, enabled, config, created_at) VALUES (?,?,?,?,?)",
            (type_id, name, 1, json.dumps(config), db.now()),
        )
        return db.last_id(conn)


def ensure_llama_source() -> int:
    return _ensure_typed_source(
        "llama",
        "Local llama",
        {"base_url": settings.llama_base_url or "http://127.0.0.1:8080/v1"},
    )


def ensure_ollama_source() -> int:
    return _ensure_typed_source("ollama", "Ollama Cloud", {"base_url": "https://ollama.com/api"})


def ensure_chatgpt_source_row() -> int:
    return ensure_chatgpt_source()


async def models_by_source(
    *,
    allow_grok: bool = False,
    allow_chatgpt: bool = False,
    allow_zai: bool = False,
    allow_devin: bool = False,
    allow_kimi: bool = False,
    allow_mimo: bool = False,
    allow_claude: bool = False,
    user_id: int | None = None,
) -> list[dict]:
    sources = _sources(
        allow_grok=allow_grok,
        allow_chatgpt=allow_chatgpt,
        allow_zai=allow_zai,
        allow_devin=allow_devin,
        allow_kimi=allow_kimi,
        allow_mimo=allow_mimo,
        allow_claude=allow_claude,
    )
    results = await asyncio.gather(*(_user_source_models(s, user_id) for s in sources))
    groups = [
        {
            "type": s.type_id,
            "name": s.name,
            "label": PROVIDER_LABELS.get(s.type_id, s.name),
            "models": names,
        }
        for s, names in zip(sources, results)
    ]
    return groups


def start_pools() -> None:
    for s in all_enabled():
        pool = getattr(s, "pool", None)
        if pool is not None:
            pool.start_sweeper()


async def stop_pools() -> None:
    from .ollama import stop_pools as stop_ollama_pools

    await stop_ollama_pools()


async def bootstrap() -> None:
    from ..accounts import add_credential
    from ..pricing import ensure_default_prices

    ensure_grok_source()
    ensure_chatgpt_source()
    ensure_zai_source()
    ensure_devin_source()
    ensure_kimi_source()
    ensure_mimo_source()
    ensure_claude_source()
    ensure_explabs_source()
    or_id = ensure_openrouter_source()
    from .openrouter import clear_global_key

    clear_global_key(or_id)
    ensure_default_prices()
    llama_id = ensure_llama_source()
    if settings.llama_api_key:
        existing = db.fetchone("SELECT id FROM credentials WHERE source_id=?", (llama_id,))
        if not existing:
            add_credential(llama_id, "llama", settings.llama_api_key)
    if settings.llama_base_url:
        row = db.fetchone("SELECT id, config FROM sources WHERE id=?", (llama_id,))
        try:
            cfg = json.loads(row["config"] or "{}") if row else {}
            if not isinstance(cfg, dict):
                raise ValueError
        except (json.JSONDecodeError, ValueError, TypeError):
            logger.warning("Resetting malformed llama source config during bootstrap")
            cfg = {}
        if not cfg.get("base_url"):
            cfg["base_url"] = settings.llama_base_url
            db.execute("UPDATE sources SET config=? WHERE id=?", (json.dumps(cfg), llama_id))
    ensure_ollama_source()
    _seed_pool_catalogs()
    from . import openrouter as openrouter_mod

    if openrouter_mod.cached_catalog() is None:
        try:
            asyncio.get_running_loop().create_task(openrouter_mod.fetch_catalog())
        except RuntimeError:
            pass
