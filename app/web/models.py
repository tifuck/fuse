from __future__ import annotations

import re

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from .. import context as ctxmod, db
from ..sources import TYPES, grok as grok_mod, models_by_source, reasoning_efforts
from ..sources import chatgpt as chatgpt_mod
from ..sources import chatgpt_catalog as chatgpt_catalog_mod
from ..sources import devin as devin_mod
from ..sources import devin_catalog as devin_catalog_mod
from ..sources import openrouter as openrouter_mod
from ..sources import explabs as explabs_mod
from ..sources import kimi as kimi_mod
from ..sources import mimo as mimo_mod
from ..sources import claude as claude_mod
from ..sources import claude_catalog as claude_catalog_mod
from ..sources import zai as zai_mod
from ..sources.grok import grok_access_state
from ..sources import pool_policy
from .deps import render, require_user

router = APIRouter()

def _reasoning_efforts(type_id: str, model: str | None = None, user_id: int | None = None) -> tuple[str, ...]:
    return reasoning_efforts(type_id, model, user_id)


def _reasoning_default(type_id: str, model: str | None = None, user_id: int | None = None) -> str:
    if type_id == "kimi":
        return kimi_mod.default_effort(model)
    if type_id == "claude":
        return claude_mod.default_effort(model)
    efforts = _reasoning_efforts(type_id, model, user_id)
    if not efforts:
        return ""
    for pref in ("medium", "high", "low"):
        if pref in efforts:
            return pref
    return efforts[0]


def _opencode_model_config(name: str, type_id: str, model: str | None = None, user_id: int | None = None) -> dict:
    efforts = _reasoning_efforts(type_id, model, user_id)
    return {
        "name": name,
        "reasoning": bool(efforts),
        "interleaved": {"field": "reasoning" if type_id == "openrouter" else "reasoning_content"},
        "variants": {effort: {"reasoningEffort": effort} for effort in efforts},
    }


_FAMILY_TITLES = {
    "grok": "Grok",
    "chatgpt": "ChatGPT",
    "zai": "Z.AI",
    "devin": "Devin",
    "kimi": "Kimi",
    "mimo": "Xiaomi",
    "claude": "Claude",
    "llama": "Local inference",
    "ollama": "Ollama Cloud",
    "openrouter": "OpenRouter",
    "explabs": "Experiential Labs",
}

_LOCK_COPY = {
    "grok": {
        "none": "You need to link a Grok account.",
        "no_sub": "The linked Grok account needs an active SuperGrok subscription.",
        "unauthed": "The linked Grok sign-in is no longer valid.",
    },
    "chatgpt": {
        "none": "You need to link a ChatGPT account.",
        "unauthed": "The linked ChatGPT sign-in is no longer valid.",
    },
    "zai": {
        "none": "You need to link an x.ai, OpenAI, or Z.AI subscription.",
        "no_sub": "The linked Z.AI key needs an active GLM Coding Plan.",
        "unauthed": "The linked Z.AI key is no longer valid.",
    },
    "openrouter": {
        "none": "Add your OpenRouter API key to use free OpenRouter models.",
    },
    "devin": {
        "none": "You need to link a Devin account.",
        "no_sub": "The linked Devin account needs an active Pro plan.",
        "unauthed": "The linked Devin sign-in is no longer valid.",
    },
    "kimi": {
        "none": "You need to link a Kimi membership.",
        "no_sub": "The linked Kimi account needs an active membership.",
        "unauthed": "The linked Kimi sign-in is no longer valid.",
    },
    "mimo": {
        "none": "You need to link a Xiaomi Token Plan.",
        "no_sub": "The linked Xiaomi key is not showing a Token Plan.",
        "unauthed": "The linked Xiaomi key is no longer valid.",
    },
    "claude": {
        "none": "You need to link a Claude account.",
        "no_sub": "The linked Claude account needs an active subscription.",
        "unauthed": "The linked Claude sign-in is no longer valid.",
    },
}

_POOL_FALLBACKS = {
    "grok": grok_mod.GROK_MODELS,
    "chatgpt": chatgpt_mod.CHATGPT_MODELS,
    "zai": zai_mod.ZAI_MODELS,
    "devin": devin_mod.DEVIN_MODELS,
    "kimi": kimi_mod.KIMI_MODELS,
    "mimo": mimo_mod.MIMO_MODELS,
    "claude": claude_mod.CLAUDE_MODELS,
}

_MODEL_NAMES = {
    "grok-4.7": "Grok 4.7",
    "grok-4.6": "Grok 4.6",
    "grok-4.5": "Grok 4.5",
    "gpt-5.5": "GPT 5.5",
    "gpt-5.4": "GPT 5.4",
    "gpt-5.4-mini": "GPT 5.4 Mini",
    "gpt-5.3-codex-spark": "GPT 5.3 Codex Spark",
    "gpt-6-astra": "GPT 6 Astra",
    "gpt-6-sol": "GPT 6 Sol",
    "gpt-6-luna": "GPT 6 Luna",
    "gpt-5.6-sol": "GPT 5.6 Sol",
    "gpt-5.6-terra": "GPT 5.6 Terra",
    "gpt-5.6-luna": "GPT 5.6 Luna",
    "claude-opus-5": "Claude Opus 5",
    "claude-sonnet-5": "Claude Sonnet 5",
    "claude-fable-5-1": "Claude Fable 5.1",
    "swe-2": "SWE-2",
    "mimo-v2.6-pro": "MiMo V2.6 Pro",
    "mimo-v2.6-flash": "MiMo V2.6 Flash",
    "mimo-v2.5-pro": "MiMo V2.5 Pro",
    "mimo-v2.5": "MiMo V2.5",
    "swe-1-6-fast": "SWE-1.6 Fast",
    "gpt-5-6-sol": "GPT 5.6 Sol",
    "gpt-5-6-terra": "GPT 5.6 Terra",
    "gpt-5-6-luna": "GPT 5.6 Luna",
    "gpt-5-5": "GPT 5.5",
    "gpt-5-4": "GPT 5.4",
    "gpt-5-4-mini": "GPT 5.4 Mini",
    "glm-5.3": "GLM 5.3",
    "glm-5.3-flash": "GLM 5.3 Flash",
    "glm-5.3-highspeed": "GLM 5.3 High-Speed",
    "glm-5.2": "GLM 5.2",
    "glm-5-2": "GLM 5.2",
    "glm-5-2-1m": "GLM 5.2 1M",
    "glm-5-3": "GLM 5.3",
    "glm-5-3-flash": "GLM 5.3 Flash",
    "glm-5.2-highspeed": "GLM 5.2 High-Speed",
    "glm-5-turbo": "GLM 5 Turbo",
    "glm-4.7": "GLM 4.7",
    "gpt-5-3-codex": "GPT 5.3 Codex",
    "gpt-4-1": "GPT 4.1",
    "gpt-5-1": "GPT 5.1",
    "gpt-5-2": "GPT 5.2",
    "kimi-k3": "Kimi K3",
    "kimi-k2-6": "Kimi K2.6",
    "kimi-k2-7": "Kimi K2.7",
    "k3": "Kimi K3",
    "k3-256k": "Kimi K3 256K",
    "kimi-for-coding": "K2.8 Preview",
    "kimi-for-coding-highspeed": "K2.7 Code HighSpeed",
    "claude/claude-opus-5-5": "Opus 5.5",
    "claude/claude-sonnet-5": "Sonnet 5",
    "claude/claude-fable-5-1": "Fable 5.1",
    "claude/claude-haiku-4-5-20251001": "Haiku 4.5",
    "qwen3.8-27b-abliterated": "Qwen 3.8 27B Obliterated",
}

_MODEL_WORDS = {
    "ai": "AI",
    "api": "API",
    "codex": "Codex",
    "coder": "Coder",
    "deepseek": "DeepSeek",
    "flash": "Flash",
    "gemini": "Gemini",
    "gemma": "Gemma",
    "glm": "GLM",
    "gpt": "GPT",
    "grok": "Grok",
    "highspeed": "High-Speed",
    "instruct": "Instruct",
    "kimi": "Kimi",
    "fable": "Fable",
    "swe": "SWE",
    "devin": "Devin",
    "claude": "Claude",
    "opus": "Opus",
    "sonnet": "Sonnet",
    "haiku": "Haiku",
    "llama": "Llama",
    "minimax": "MiniMax",
    "mistral": "Mistral",
    "mixtral": "Mixtral",
    "nano": "Nano",
    "nemotron": "Nemotron",
    "oss": "OSS",
    "qwen": "Qwen",
    "turbo": "Turbo",
}


def _humanize_model_id(model: str) -> str:
    raw = model[6:] if model.startswith("devin/") else model
    parts = [part for part in re.split(r"[-_/:]+", raw) if part]
    words = []
    for part in parts:
        lower = part.lower()
        if lower in _MODEL_WORDS:
            words.append(_MODEL_WORDS[lower])
        elif re.fullmatch(r"\d+(?:\.\d+)?[bk]", lower):
            words.append(lower[:-1] + lower[-1].upper())
        elif re.fullmatch(r"[a-z]\d+(?:\.\d+)?[a-z]?", lower):
            words.append(lower.upper())
        elif re.fullmatch(r"\d+(?:\.\d+)?", lower):
            words.append(lower)
        else:
            words.append(part if any(char.isupper() for char in part) else part.capitalize())
    merged: list[str] = []
    for word in words:
        prev = merged[-1] if merged else ""
        if prev and re.fullmatch(r"\d+(?:\.\d+)*", prev) and re.fullmatch(r"\d+", word):
            merged[-1] = f"{prev}.{word}"
        elif prev and re.fullmatch(r"[vV]\d+(?:\.\d+)*", prev) and re.fullmatch(r"\d+", word):
            merged[-1] = f"{prev}.{word}"
        else:
            merged.append(word)
    text = " ".join(merged) or model
    text = re.sub(r"\bSWE (\d)", r"SWE-\1", text)
    return _standardize_display_name(text)


def _standardize_display_name(name: str) -> str:
    """One display form for every provider.

    GPT-6 Astra, GPT-5.4, and Grok-4.7 all become GPT 6 Astra, GPT 5.4, and
    Grok 4.7. Vendor prefixes are dropped so ChatGPT, Devin, and OpenRouter
    show the same name for the same model.
    """
    text = (name or "").strip()
    if not text:
        return text
    text = re.sub(r"\s*\(free\)\s*$", " (Free)", text, flags=re.I)
    text = re.sub(
        r"^(?:OpenAI|Anthropic|xAI|SpaceXAI|Z\.?\s?AI|MoonshotAI|Xiaomi|Google|DeepSeek)\s*:\s*",
        "",
        text,
        flags=re.I,
    )
    text = re.sub(r"\bGpt\b", "GPT", text)
    text = re.sub(r"\bGlm\b", "GLM", text)
    text = re.sub(r"\bSwe\b", "SWE", text)
    text = re.sub(r"\bGPT-(\d)", r"GPT \1", text)
    text = re.sub(r"\bGLM-(\d)", r"GLM \1", text)
    text = re.sub(r"\bGrok-(\d)", r"Grok \1", text)
    text = re.sub(r"\bClaude-(\d)", r"Claude \1", text)
    text = re.sub(r"\bGPT (\d+(?:\.\d+)?)-Codex\b", r"GPT \1 Codex", text)
    text = re.sub(r"\b([vV]\d+)\s+(\d+)\b", r"\1.\2", text)
    text = re.sub(r"\b(\d+)\s+(\d+)\b", r"\1.\2", text)
    text = re.sub(r"\bSWE (\d)", r"SWE-\1", text)
    text = re.sub(r"\s*\((?:batch|extended)\)\s*$", "", text, flags=re.I)
    return text.strip()


def _named_model(model: str, *alts: str) -> str:
    for key in (model, *alts):
        if key and key in _MODEL_NAMES:
            return _MODEL_NAMES[key]
        if key and key.startswith("devin/"):
            uid = key[6:]
            if uid in _MODEL_NAMES:
                return _MODEL_NAMES[uid]
    return ""


def _model_display_name(model: str, type_id: str, openrouter_catalog: dict | None = None) -> str:
    if type_id == "explabs":
        upstream = explabs_mod.upstream_id(model)
        record = (openrouter_catalog or {}).get("by_id", {}).get(upstream) or {}
        return _standardize_display_name(str(record.get("name") or _humanize_model_id(upstream)))
    if type_id == "openrouter":
        upstream = openrouter_mod.upstream_id(model)
        slug = upstream.split("/")[-1]
        named = _named_model(slug, slug.replace(".", "-"))
        if named:
            return named
        record = (openrouter_catalog or {}).get("by_id", {}).get(upstream)
        if record and record.get("name"):
            return _standardize_display_name(str(record["name"]))
        return _humanize_model_id(slug)
    if type_id == "devin":
        uid = devin_mod.listed_upstream(model) or devin_mod.upstream_id(model)
        canon = devin_mod.canonical_upstream(model) or uid
        dotted = canon.replace("-", ".", 1) if canon else ""
        named = _named_model(model, uid, canon, dotted)
        if named:
            return named
        # Devin's own label wins only when Fuse has no name for this SKU.
        # GPT and Grok then match ChatGPT and xAI instead of the catalog string.
        if not (canon.startswith("gpt-") or canon.startswith("grok")):
            label = devin_mod.model_label(model)
            if label:
                return _standardize_display_name(label)
        return _humanize_model_id(canon or uid)
    if type_id == "kimi":
        named = _named_model(model) or kimi_mod.model_label(model)
        if named:
            return _standardize_display_name(named)
    if type_id == "claude":
        named = _named_model(model) or claude_mod.model_label(model)
        if named:
            return _standardize_display_name(named)
    return _MODEL_NAMES.get(model) or _humanize_model_id(model)


def _model_rows(type_id: str, names: list[str], catalog: dict | None = None) -> list[dict]:
    return [
        {
            "id": name,
            "display_id": (
                explabs_mod.upstream_id(name) if type_id == "explabs"
                else openrouter_mod.upstream_id(name) if type_id == "openrouter" else name
            ),
            "name": _model_display_name(name, type_id, catalog),
            "context_window": ctxmod.window_for(name, type_id, catalog=catalog),
        }
        for name in names
    ]


def _family_title(group: dict) -> str:
    return _FAMILY_TITLES.get(group["type"], group.get("name") or group["type"])


def _pool_catalog(type_id: str) -> list[str]:
    cache = {
        "grok": grok_mod._models_cache,
        "chatgpt": chatgpt_catalog_mod._models_cache,
        "zai": zai_mod._models_cache,
        "devin": devin_catalog_mod._models_cache,
        "kimi": kimi_mod._models_cache,
        "mimo": mimo_mod._models_cache,
        "claude": claude_catalog_mod._models_cache,
    }.get(type_id)
    if cache and cache[1]:
        return list(cache[1])
    return list(_POOL_FALLBACKS.get(type_id, []))


def _lock_copy(type_id: str, reason: str, user_id: int | None = None) -> str:
    if type_id == "zai" and reason == "none" and user_id is not None and not pool_policy.uses_shared_pool(user_id, "zai"):
        return "You need to link a Z.AI key."
    copy = _LOCK_COPY.get(type_id) or {}
    return copy.get(reason) or copy.get("none") or "You need to link an account."


def _openrouter_locked_models(catalog: dict) -> list[str]:
    """Preview a handful of free SKUs on the locked OpenRouter panel."""
    ids = catalog.get("free_ids") or []
    return [openrouter_mod.public_id(mid) for mid in ids][:24]


async def _models_page_families(user) -> tuple[list[dict], list[dict]]:
    grok = grok_access_state(user["id"])
    chatgpt = chatgpt_mod.chatgpt_access_state(user["id"])
    zai = zai_mod.zai_access_state(user["id"])
    devin = devin_mod.devin_access_state(user["id"])
    kimi = kimi_mod.kimi_access_state(user["id"])
    mimo = mimo_mod.mimo_access_state(user["id"])
    claude = claude_mod.claude_access_state(user["id"])
    or_state = openrouter_mod.openrouter_access_state(user["id"])
    access = {"grok": grok, "chatgpt": chatgpt, "zai": zai, "devin": devin, "kimi": kimi, "mimo": mimo, "claude": claude}
    groups = await models_by_source(
        allow_grok=grok["ok"],
        allow_chatgpt=chatgpt["ok"],
        allow_zai=zai["ok"],
        allow_devin=devin["ok"],
        allow_kimi=kimi["ok"],
        allow_mimo=mimo["ok"],
        allow_claude=claude["ok"],
        user_id=user["id"],
    )
    catalog = openrouter_mod.cached_catalog()
    if catalog is None and or_state["ok"]:
        catalog = await openrouter_mod.fetch_catalog()
    explabs_catalog = explabs_mod.cached_catalog(user["id"])
    if explabs_catalog is None and any(g["type"] == "explabs" and g.get("models") for g in groups):
        explabs_catalog = await explabs_mod.fetch_catalog(user["id"])
    available: list[dict] = []
    for group in groups:
        names = list(group.get("models") or [])
        if not names and group["type"] in _POOL_FALLBACKS:
            names = _pool_catalog(group["type"])
        if not names:
            continue
        available.append(
            {
                "type": group["type"],
                "title": _family_title(group),
                "models": _model_rows(group["type"], names, explabs_catalog if group["type"] == "explabs" else catalog),
            }
        )
    have = {g["type"] for g in available}
    locked: list[dict] = []
    for type_id in ("grok", "chatgpt", "zai", "devin", "kimi", "mimo", "claude"):
        state = access[type_id]
        if state["ok"] or type_id in have:
            continue
        row = db.fetchone("SELECT id, name FROM sources WHERE type=? AND enabled=1", (type_id,))
        if not row:
            continue
        names = _pool_catalog(type_id)
        if not names:
            continue
        locked.append(
            {
                "type": type_id,
                "title": _FAMILY_TITLES.get(type_id, row["name"]),
                "models": _model_rows(type_id, names, catalog),
                "why": _lock_copy(type_id, state["reason"], user["id"]),
                "href": "/sources",
                "link_label": "Open Sources",
            }
        )
    or_row = db.fetchone("SELECT id, name FROM sources WHERE type='openrouter' AND enabled=1")
    if or_row and not or_state["user_key"]:
        catalog = catalog or {}
        names = _openrouter_locked_models(catalog)
        why = _lock_copy("openrouter", "none")
        if names:
            locked.append(
                {
                    "type": "openrouter",
                    "title": "OpenRouter",
                    "models": _model_rows("openrouter", names, catalog),
                    "why": why,
                    "href": "/sources",
                    "link_label": "Add your OpenRouter key",
                }
            )
    elif or_row and not or_state["free_models"]:
        catalog = catalog or {}
        names = _openrouter_locked_models(catalog)
        if names:
            locked.append(
                {
                    "type": "openrouter",
                    "title": "OpenRouter",
                    "models": _model_rows("openrouter", names, catalog),
                    "why": "Free OpenRouter models are turned off for your account.",
                    "href": "/sources",
                    "link_label": "Open Sources",
                }
            )
    return available, locked


@router.get("/models", response_class=HTMLResponse)
async def models_page(request: Request):
    user, redir = require_user(request)
    if redir:
        return redir
    available, locked = await _models_page_families(user)
    return render(
        request,
        "models.html",
        user,
        available=available,
        locked=locked,
        available_count=sum(len(g["models"]) for g in available),
        locked_count=sum(len(g["models"]) for g in locked),
    )




