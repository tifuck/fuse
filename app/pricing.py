import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

import httpx
import json

from . import db

logger = logging.getLogger(__name__)

OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"

CHATGPT_PROVIDER = "chatgpt"
GROK_PROVIDER = "grok"
ZAI_PROVIDER = "zai"
DEVIN_PROVIDER = "devin"
KIMI_PROVIDER = "kimi"
MIMO_PROVIDER = "mimo"
CLAUDE_PROVIDER = "claude"

# OpenAI list prices (USD per million tokens) for the ChatGPT pool models:
# model -> (input, cached_input, cache_write, output). GPT-5.6 charges cache
# writes at 1.25x; earlier families have no separate write rate. Long-context
# tiers apply from 272K prompt tokens (inclusive). gpt-5.3-codex-spark has no
# separate public listing; it uses gpt-5.3-codex rates.
DEFAULT_CHATGPT_PRICING: dict[str, tuple[tuple, tuple | None]] = {
    "gpt-6-astra": (("10", "1", "12.50", "50"), ("20", "2", "25", "75")),
    "gpt-6-sol": (("2", "0.20", "2.50", "10"), ("4", "0.40", "5", "15")),
    "gpt-6-luna": (("0.10", "0.01", "0.125", "0.50"), ("0.20", "0.02", "0.25", "0.75")),
    "gpt-5.5": (("5", "0.50", None, "30"), ("10", "1.00", None, "45")),
    "gpt-5.4": (("2.50", "0.25", None, "15"), ("5", "0.50", None, "22.50")),
    "gpt-5.4-mini": (("0.75", "0.075", None, "4.50"), None),
    "gpt-5.3-codex-spark": (("1.75", "0.175", None, "14"), None),
    "gpt-5.6-sol": (("4", "0.40", "5", "20"), ("8", "0.80", "10", "30")),
    "gpt-5.6-terra": (("2", "0.20", "2.50", "12"), ("4", "0.40", "5", "18")),
    "gpt-5.6-luna": (("0.20", "0.02", "0.25", "1.20"), ("0.40", "0.04", "0.50", "1.80")),
}

# xAI list prices (USD per million tokens) for the Grok pool models. The
# long-context tier doubles rates at >= 200K prompt tokens (inclusive).
DEFAULT_GROK_PRICING: dict[str, tuple[tuple, tuple | None]] = {
    "grok-4.7": (("2", "0.50", None, "6"), ("4", "1.00", None, "12")),
    "grok-4.6": (("2", "0.50", None, "6"), ("4", "1.00", None, "12")),
    "grok-4.5": (("2", "0.30", None, "6"), ("4", "0.60", None, "12")),
}

# Z.AI list prices (USD per million tokens) for Coding Plan models, used as
# API-equivalent cost on the pool. The plan itself is a subscription.
# glm-5.3-flash uses the promotional rates (through 2026-09-09).
DEFAULT_ZAI_PRICING: dict[str, tuple[tuple, tuple | None]] = {
    "glm-5.3": (("1.40", "0.26", None, "4.40"), None),
    "glm-5.3-highspeed": (("1.40", "0.26", None, "4.40"), None),
    "glm-5.3-flash": (("0.075", "0.015", None, "0.25"), None),
    "glm-5.2": (("1.40", "0.26", None, "4.40"), None),
    "glm-5.2-highspeed": (("1.40", "0.26", None, "4.40"), None),
    "glm-5-turbo": (("1.40", "0.26", None, "4.40"), None),
    "glm-4.7": (("0.60", "0.11", None, "2.20"), None),
}

# Devin CLI / Cascade list prices (USD per million). Fable 5.1 Medium is the
# recommended pairing; SWE-2 is the Cognition in-house SKU. Seeded rows are
# API-equivalent estimates — Devin bills quota/credits, not these rates.
DEFAULT_DEVIN_PRICING: dict[str, tuple[tuple, tuple | None]] = {
    "claude-fable-5-1": (("10", "1.00", "12.50", "50"), None),
    "swe-2": (("3", "0.30", None, "15"), None),
    "swe-1-6-fast": (("0.80", "0.08", None, "2.50"), None),
    "claude-opus-5": (("15", "1.50", "18.75", "75"), None),
    "claude-sonnet-5": (("3", "0.30", "3.75", "15"), None),
    "devin/claude-fable-5-1": (("10", "1.00", "12.50", "50"), None),
    "devin/swe-2": (("3", "0.30", None, "15"), None),
    "devin/swe-1-6-fast": (("0.80", "0.08", None, "2.50"), None),
    "devin/claude-opus-5": (("15", "1.50", "18.75", "75"), None),
    "devin/claude-sonnet-5": (("3", "0.30", "3.75", "15"), None),
    "devin/gpt-6-astra": (("10", "1", "12.50", "50"), ("20", "2", "25", "75")),
    "devin/gpt-5.5": (("5", "0.50", None, "30"), ("10", "1.00", None, "45")),
    "devin/gpt-5.4": (("2.50", "0.25", None, "15"), ("5", "0.50", None, "22.50")),
    "devin/gpt-5.4-mini": (("0.75", "0.075", None, "4.50"), None),
    "devin/gpt-5.6-sol": (("4", "0.40", "5", "20"), ("8", "0.80", "10", "30")),
    "devin/gpt-5.6-terra": (("2", "0.20", "2.50", "12"), ("4", "0.40", "5", "18")),
    "devin/gpt-5.6-luna": (("0.20", "0.02", "0.25", "1.20"), ("0.40", "0.04", "0.50", "1.80")),
    "gpt-6-astra": (("10", "1", "12.50", "50"), ("20", "2", "25", "75")),
    "gpt-5.5": (("5", "0.50", None, "30"), ("10", "1.00", None, "45")),
    "gpt-5.4": (("2.50", "0.25", None, "15"), ("5", "0.50", None, "22.50")),
    "gpt-5.4-mini": (("0.75", "0.075", None, "4.50"), None),
    "gpt-5.6-sol": (("4", "0.40", "5", "20"), ("8", "0.80", "10", "30")),
    "gpt-5.6-terra": (("2", "0.20", "2.50", "12"), ("4", "0.40", "5", "18")),
    "gpt-5.6-luna": (("0.20", "0.02", "0.25", "1.20"), ("0.40", "0.04", "0.50", "1.80")),
}

# Kimi Open Platform list prices (USD per million) used as API-equivalent
# cost on the membership pool. The plan itself is a subscription.
# Cache write uses the 5-minute TTL tier when Fuse reports cache writes.
DEFAULT_KIMI_PRICING: dict[str, tuple[tuple, tuple | None]] = {
    "k3": (("3.00", "0.30", "3.00", "15.00"), None),
    "k3-256k": (("3.00", "0.30", "3.00", "15.00"), None),
    "kimi-for-coding": (("0.95", "0.19", None, "4.00"), None),
    "kimi-for-coding-highspeed": (("1.90", "0.38", None, "8.00"), None),
}

# Xiaomi pay-as-you-go list prices (USD per million), used as API-equivalent
# cost on the Token Plan pool. The plan itself bills credits. Cache reads are
# the published hit rate (~100x below a miss). No separate cache-write rate.
DEFAULT_MIMO_PRICING: dict[str, tuple[tuple, tuple | None]] = {
    "mimo-v2.6-pro": (("0.435", "0.0036", None, "0.87"), None),
    "mimo-v2.6-flash": (("0.14", "0.0028", None, "0.28"), None),
    "mimo-v2.5-pro": (("0.435", "0.0036", None, "0.87"), None),
    "mimo-v2.5": (("0.14", "0.0028", None, "0.28"), None),
}

# Anthropic list prices (USD per million) for the Claude subscription pool.
# input, cache read, cache write (1.25x), output. The plan itself is a subscription.
DEFAULT_CLAUDE_PRICING: dict[str, tuple[tuple, tuple | None]] = {
    "claude/claude-opus-5-5": (("5", "0.50", "6.25", "25"), None),
    "claude/claude-sonnet-5": (("3", "0.30", "3.75", "15"), None),
    "claude/claude-fable-5-1": (("10", "1.00", "12.50", "50"), None),
    "claude/claude-haiku-4-5-20251001": (("1", "0.10", "1.25", "5"), None),
    "claude-opus-5-5": (("5", "0.50", "6.25", "25"), None),
    "claude-sonnet-5": (("3", "0.30", "3.75", "15"), None),
    "claude-fable-5-1": (("10", "1.00", "12.50", "50"), None),
    "claude-haiku-4-5-20251001": (("1", "0.10", "1.25", "5"), None),
}

_LONG_CONTEXT_THRESHOLD = 272_000
_GROK_LONG_THRESHOLD = 200_000


def _usd_per_million(value: str) -> int:
    return int((Decimal(value) * Decimal("1000000")).quantize(Decimal("1")))


def _tier(threshold: int, rates: tuple) -> dict:
    inp, cached, write, out = rates
    return {
        "min_prompt_tokens": threshold,
        "input_rate": _usd_per_million(inp),
        "cached_rate": _usd_per_million(cached) if cached else None,
        "cache_write_rate": _usd_per_million(write) if write else None,
        "output_rate": _usd_per_million(out),
    }


def _default_row_matches(existing, *, inp: str, cached: str | None, write: str | None, out: str, tiers: list[dict] | None, threshold: int) -> bool:
    """Full-tuple comparison of a seeded row against the current defaults.

    Comparing only input+output let changes to cached, cache-write, or
    long-context tier rates persist stale values on origin='default' rows.
    """
    want = {
        "input_micros_per_million": _usd_per_million(inp),
        "cached_input_micros_per_million": _usd_per_million(cached) if cached else 0,
        "cache_write_micros_per_million": _usd_per_million(write) if write else 0,
        "output_micros_per_million": _usd_per_million(out),
        "cached_input_known": 1 if cached is not None else 0,
        "cache_write_known": 1 if write is not None else 0,
    }
    # Long-context rates are compared through tiers_json only: the seeding
    # call passes tiers alone, so the legacy long_* columns persist as NULL
    # and comparing them made every boot re-save (and reprice) these rows.
    try:
        want["tiers_json"] = json.dumps(
            [
                {
                    "min_prompt_tokens": int(t.get("min_prompt_tokens") or 0),
                    "input_rate": t.get("input_rate"),
                    "cached_rate": t.get("cached_rate"),
                    "cache_write_rate": t.get("cache_write_rate"),
                    "output_rate": t.get("output_rate"),
                }
                for t in (tiers or [])
                if isinstance(t, dict) and int(t.get("min_prompt_tokens") or 0) > 0
            ]
        )
    except (TypeError, ValueError):
        want["tiers_json"] = "[]"
    for column, expected in want.items():
        try:
            have = existing[column]
        except (KeyError, IndexError):
            return False
        if have != expected:
            return False
    return True


def ensure_default_prices() -> list[str]:
    """Seed missing pool prices and refresh stale seeded rows.

    Never touches manual or openrouter-synced rows: a manual edit must win
    over a changed built-in default.
    """
    seeded = []
    for provider, defaults in (
        (CHATGPT_PROVIDER, DEFAULT_CHATGPT_PRICING),
        (GROK_PROVIDER, DEFAULT_GROK_PRICING),
        (ZAI_PROVIDER, DEFAULT_ZAI_PRICING),
        (DEVIN_PROVIDER, DEFAULT_DEVIN_PRICING),
        (KIMI_PROVIDER, DEFAULT_KIMI_PRICING),
        (MIMO_PROVIDER, DEFAULT_MIMO_PRICING),
        (CLAUDE_PROVIDER, DEFAULT_CLAUDE_PRICING),
    ):
        threshold = _GROK_LONG_THRESHOLD if provider == GROK_PROVIDER else _LONG_CONTEXT_THRESHOLD
        for model, (base, long_rates) in defaults.items():
            inp, cached, write, out = base
            existing = db.fetchone(
                "SELECT * FROM model_prices WHERE provider=? AND model=?",
                (provider, model),
            )
            # An auto-priced row was only a stand-in until a built-in default existed.
            if existing and existing["origin"] not in ("default", AUTO_ORIGIN):
                continue
            tiers = [_tier(threshold, long_rates)] if long_rates else None
            # A matching auto row is still re-saved, so it becomes the default
            # and auto_price can no longer replace it.
            if existing and existing["origin"] == "default" and _default_row_matches(
                existing, inp=inp, cached=cached, write=write, out=out, tiers=tiers, threshold=threshold
            ):
                continue
            db.save_model_price(
                provider,
                model,
                _usd_per_million(inp),
                _usd_per_million(cached) if cached else None,
                _usd_per_million(out),
                None,
                cache_write_rate=_usd_per_million(write) if write else None,
                tiers=tiers,
                origin="default",
            )
            seeded.append(f"{provider}/{model}")
    return seeded


def _token_rate(value) -> int | None:
    if value is None:
        return None
    try:
        rate = Decimal(str(value))
        stored = int((rate * Decimal("1000000000000")).quantize(Decimal("1")))
    except (InvalidOperation, ValueError, OverflowError):
        return None
    if not rate.is_finite() or rate < 0 or stored > db.SQLITE_INT_MAX:
        return None
    # OpenRouter publishes USD per token. Fuse stores microdollars per million tokens.
    return stored


def _long_context_tiers(pricing: dict) -> list[dict]:
    """Every min_prompt_tokens override becomes an inclusive long-context tier."""
    tiers = []
    for override in pricing.get("overrides") or []:
        if not isinstance(override, dict) or not isinstance(override.get("min_prompt_tokens"), int):
            continue
        condition_keys = {key for key in override if key.endswith("_tokens")}
        if condition_keys != {"min_prompt_tokens"}:
            continue
        tiers.append(
            {
                "min_prompt_tokens": override["min_prompt_tokens"],
                "input_rate": _token_rate(override.get("prompt")),
                "cached_rate": _token_rate(override.get("input_cache_read")),
                "cache_write_rate": _token_rate(override.get("input_cache_write")),
                "output_rate": _token_rate(override.get("completion")),
            }
        )
    tiers.sort(key=lambda t: t["min_prompt_tokens"])
    return tiers


async def sync_openrouter_prices() -> dict:
    imported: set[str] = set()
    from .sources import chatgpt, chatgpt_catalog, zai

    def listed(seeded, cache) -> set[str]:
        return set(seeded) | set(cache[1] if cache else [])

    author_providers = (
        ("x-ai", "grok", None),
        ("openai", "openai", None),
        ("openai", CHATGPT_PROVIDER, listed(chatgpt.CHATGPT_MODELS, chatgpt_catalog._models_cache)),
        ("z-ai", "zai", listed(zai.ZAI_MODELS, zai._models_cache)),
    )
    catalogs = []
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        for author, provider, allow in author_providers:
            response = await client.get(OPENROUTER_MODELS_URL, params={"model_authors": author})
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
                raise ValueError(f"invalid OpenRouter catalog for {author}")
            catalogs.append((author, provider, allow, payload["data"]))

    staged: dict[tuple[str, str], dict] = {}
    for author, provider, allow, items in catalogs:
        for item in items:
            if not isinstance(item, dict):
                continue
            model_id = str(item.get("id") or "")
            if not model_id.startswith(author + "/"):
                continue
            pricing = item.get("pricing") if isinstance(item.get("pricing"), dict) else {}
            input_rate = _token_rate(pricing.get("prompt"))
            output_rate = _token_rate(pricing.get("completion"))
            if input_rate is None or output_rate is None:
                continue
            model = model_id.split("/", 1)[1]
            if ":" in model and not model.endswith(":free"):
                continue
            pool_model = model[: -len(":free")] if model.endswith(":free") else model
            if allow is not None and pool_model not in allow:
                continue
            values = {
                "input_rate": input_rate,
                "cached_rate": _token_rate(pricing.get("input_cache_read")),
                "output_rate": output_rate,
                "reasoning_rate": _token_rate(pricing.get("internal_reasoning")),
                "cache_write_rate": _token_rate(pricing.get("input_cache_write")),
                "tiers": _long_context_tiers(pricing),
                "origin": "openrouter",
                "openrouter_id": model_id,
            }
            if not model.endswith(":free"):
                staged[(provider, pool_model)] = values
            staged[("openrouter", model_id)] = values
            imported.add(model_id)

    def store() -> set[str]:
        manual_ids: set[str] = set()
        with db.tx() as conn:
            for (provider, model) in staged:
                row = conn.execute(
                    "SELECT origin FROM model_prices WHERE provider=? AND model=?", (provider, model)
                ).fetchone()
                if row is not None and row["origin"] == "manual":
                    # The upsert's manual guard will skip this row; do not count
                    # it as synced.
                    if provider == "openrouter":
                        manual_ids.add(model)
            for (provider, model), values in staged.items():
                db.save_model_price(provider, model, conn=conn, **values)
        return manual_ids

    # Every save reprices that model's history: run it off the event loop.
    manual_ids = await asyncio.to_thread(store)
    models = sorted(imported - manual_ids)
    return {"count": len(models), "models": models}


# --- Auto pricing -----------------------------------------------------------
#
# Usage is priced by an exact (provider, model) row. A new pool model
# (gpt-6.1-sol, claude-sonnet-5-5) or a Devin wire SKU (gpt-5-6-sol-low) has no
# row, so its usage records as unpriced. auto_price() writes an exact row for
# each such model, copied from the closest priced reference, and
# save_model_price then reprices that model's history. Manual, built-in and
# OpenRouter-synced rows are never overwritten; auto rows follow their
# reference and yield to a built-in default or a sync.

AUTO_ORIGIN = "auto"
AUTO_PRICE_INTERVAL = 1800.0
_AUTO_PROVIDERS = (
    CHATGPT_PROVIDER, GROK_PROVIDER, ZAI_PROVIDER, DEVIN_PROVIDER,
    KIMI_PROVIDER, MIMO_PROVIDER, CLAUDE_PROVIDER,
)
# Effort, speed and region tails appended to one priced model id. They are a
# service tier on ChatGPT/Devin ids only (gpt-5.6-sol-fast, gpt-5-6-sol-low);
# elsewhere such suffixes are distinct models (grok-4-fast) and must not be
# priced from the base id.
_SKU_TAILS = frozenset(
    {"none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra", "thinking", "priority", "fast", "usa"}
)
_TIER_TAIL_PROVIDERS = frozenset({CHATGPT_PROVIDER, DEVIN_PROVIDER})
_PRICE_COLUMNS = (
    "input_micros_per_million", "cached_input_micros_per_million", "cache_write_micros_per_million",
    "output_micros_per_million", "reasoning_micros_per_million", "cached_input_known",
    "cache_write_known", "reasoning_known", "long_context_threshold", "long_input_micros_per_million",
    "long_cached_input_micros_per_million", "long_output_micros_per_million", "long_cached_input_known",
    "tiers_json",
)
_auto_last_run = 0.0


def price_key(model: str) -> str:
    """One spelling per model: no provider/author prefix, no :free, dots as
    dashes (Devin's gpt-5-6-sol is ChatGPT's gpt-5.6-sol)."""
    mid = (model or "").strip().lower().rsplit("/", 1)[-1]
    if mid.endswith(":free"):
        mid = mid[: -len(":free")]
    return mid.replace(".", "-")


def price_keys(model: str, *, tiered: bool = True) -> list[str]:
    """The model's key, then the same key with SKU tails stripped one by one.

    `tiered=False` is an exact-key lookup for providers whose tails name
    distinct models."""
    parts = price_key(model).split("-")
    keys = ["-".join(parts)]
    if not tiered:
        return keys
    while len(parts) > 1 and parts[-1] in _SKU_TAILS:
        parts.pop()
        keys.append("-".join(parts))
    return keys


def _row_values(row) -> dict:
    """save_model_price kwargs that reproduce a stored price row."""
    try:
        tiers = json.loads(row["tiers_json"] or "[]")
    except (TypeError, ValueError):
        tiers = []
    return {
        "input_rate": int(row["input_micros_per_million"] or 0),
        "cached_rate": int(row["cached_input_micros_per_million"] or 0) if row["cached_input_known"] else None,
        "output_rate": int(row["output_micros_per_million"] or 0),
        "reasoning_rate": int(row["reasoning_micros_per_million"] or 0) if row["reasoning_known"] else None,
        "cache_write_rate": int(row["cache_write_micros_per_million"] or 0) if row["cache_write_known"] else None,
        "tiers": tiers if isinstance(tiers, list) else [],
        "long_context_threshold": row["long_context_threshold"],
        "long_input_rate": row["long_input_micros_per_million"],
        "long_cached_rate": row["long_cached_input_micros_per_million"] if row["long_cached_input_known"] else None,
        "long_output_rate": row["long_output_micros_per_million"],
        "openrouter_id": row["openrouter_id"],
    }


def _catalog_values(rec: dict) -> dict | None:
    pricing = rec.get("pricing") if isinstance(rec.get("pricing"), dict) else {}
    input_rate = _token_rate(pricing.get("prompt"))
    output_rate = _token_rate(pricing.get("completion"))
    if input_rate is None or output_rate is None or (input_rate == 0 and output_rate == 0):
        return None
    return {
        "input_rate": input_rate,
        "cached_rate": _token_rate(pricing.get("input_cache_read")),
        "output_rate": output_rate,
        "reasoning_rate": _token_rate(pricing.get("internal_reasoning")),
        "cache_write_rate": _token_rate(pricing.get("input_cache_write")),
        "tiers": _long_context_tiers(pricing),
        "openrouter_id": str(rec.get("id") or ""),
    }


def _reference_index(rows, catalog: dict | None) -> dict[str, list[tuple[int, str, dict]]]:
    """price key -> [(rank, provider, values)]. Lower rank wins: a pool row
    (built-in or manual) beats an OpenRouter reference row, which beats a
    live OpenRouter catalog record. Auto rows are never a reference."""
    index: dict[str, list[tuple[int, str, dict]]] = {}
    for row in rows:
        if row["origin"] == AUTO_ORIGIN:
            continue
        values = _row_values(row)
        if values["input_rate"] == 0 and values["output_rate"] == 0:
            continue
        rank = 1 if row["provider"] == "openrouter" else 0
        index.setdefault(price_key(row["model"]), []).append((rank, row["provider"], values))
    for mid, rec in ((catalog or {}).get("by_id") or {}).items():
        if not isinstance(rec, dict) or rec.get("free") or ":" in mid.split("/", 1)[-1]:
            continue
        values = _catalog_values(rec)
        if values:
            index.setdefault(price_key(mid), []).append((2, "openrouter", values))
    for refs in index.values():
        refs.sort(key=lambda ref: ref[0])
    return index


def _pick_reference(index: dict, provider: str, model: str) -> dict | None:
    for key in price_keys(model, tiered=provider in _TIER_TAIL_PROVIDERS):
        refs = index.get(key)
        if not refs:
            continue
        # Same provider first (Devin's own gpt-5.6-sol row over ChatGPT's).
        same = [ref for ref in refs if ref[1] == provider]
        return (same or refs)[0][2]
    return None


def _same_price(row, values: dict) -> bool:
    probe = {column: row[column] for column in _PRICE_COLUMNS}
    return all(probe[column] == expected for column, expected in _expected_columns(values).items())


def _expected_columns(values: dict) -> dict:
    tiers = [
        {
            "min_prompt_tokens": int(t.get("min_prompt_tokens") or 0),
            "input_rate": t.get("input_rate"),
            "cached_rate": t.get("cached_rate"),
            "cache_write_rate": t.get("cache_write_rate"),
            "output_rate": t.get("output_rate"),
        }
        for t in values.get("tiers") or []
        if isinstance(t, dict) and int(t.get("min_prompt_tokens") or 0) > 0
    ]
    return {
        "input_micros_per_million": values["input_rate"],
        "cached_input_micros_per_million": values["cached_rate"] or 0,
        "cache_write_micros_per_million": values["cache_write_rate"] or 0,
        "output_micros_per_million": values["output_rate"],
        "reasoning_micros_per_million": values["reasoning_rate"] or 0,
        "cached_input_known": 1 if values["cached_rate"] is not None else 0,
        "cache_write_known": 1 if values["cache_write_rate"] is not None else 0,
        "reasoning_known": 1 if values["reasoning_rate"] is not None else 0,
        "long_context_threshold": values.get("long_context_threshold"),
        "long_input_micros_per_million": values.get("long_input_rate"),
        "long_cached_input_micros_per_million": values.get("long_cached_rate"),
        "long_output_micros_per_million": values.get("long_output_rate"),
        "long_cached_input_known": 1 if values.get("long_cached_rate") is not None else 0,
        "tiers_json": json.dumps(tiers),
    }


def _listed_pool_models() -> set[tuple[str, str]]:
    """(provider, recorded model) for every id the pool catalogs list now."""
    from .sources import chatgpt, chatgpt_catalog, claude, claude_catalog, grok, kimi, mimo, zai

    out: set[tuple[str, str]] = set()
    for provider, mod in (
        (CHATGPT_PROVIDER, chatgpt), (GROK_PROVIDER, grok), (ZAI_PROVIDER, zai),
        (KIMI_PROVIDER, kimi), (MIMO_PROVIDER, mimo),
    ):
        cache = chatgpt_catalog._models_cache if mod is chatgpt else mod._models_cache
        out.update((provider, mid) for mid in (cache[1] if cache else []))
    cache = claude_catalog._models_cache
    # The Claude pool records the upstream slug, not the claude/ Fuse id.
    out.update((CLAUDE_PROVIDER, claude.upstream_id(mid)) for mid in (cache[1] if cache else []))
    return out


def auto_price(catalog: dict | None = None, *, since_days: int = 30) -> list[str]:
    """Price listed or recently used pool models that have no exact row."""
    since = (datetime.now(timezone.utc) - timedelta(days=since_days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    marks = ",".join("?" * len(_AUTO_PROVIDERS))
    used = db.fetchall(
        f"""SELECT DISTINCT source_type, model FROM usage_events
            WHERE created_at >= ? AND source_type IN ({marks}) AND pricing_known=0
              AND reported_cost_micros IS NULL AND model != ''""",
        (since, *_AUTO_PROVIDERS),
    )
    rows = db.fetchall("SELECT * FROM model_prices")
    existing = {(row["provider"], row["model"]): row for row in rows}
    # Existing auto rows too: a model that is neither listed nor unpriced
    # (a Devin wire SKU once priced) must still follow its reference.
    auto_rows = {key for key, row in existing.items() if row["origin"] == AUTO_ORIGIN}
    targets = {(row["source_type"], row["model"]) for row in used} | _listed_pool_models() | auto_rows
    index = _reference_index(rows, catalog)
    priced = []
    for provider, model in sorted(targets):
        row = existing.get((provider, model))
        if row is not None and row["origin"] != AUTO_ORIGIN:
            continue
        values = _pick_reference(index, provider, model)
        if values is None or (row is not None and _same_price(row, values)):
            continue
        db.save_model_price(provider, model, origin=AUTO_ORIGIN, **values)
        priced.append(f"{provider}/{model}")
    if priced:
        logger.info("Auto-priced %d model(s): %s", len(priced), ", ".join(priced))
    return priced


async def auto_price_due() -> None:
    """Background job, throttled to AUTO_PRICE_INTERVAL."""
    global _auto_last_run
    if time.time() - _auto_last_run < AUTO_PRICE_INTERVAL:
        return
    from .sources import openrouter as openrouter_mod

    catalog = openrouter_mod.cached_catalog()
    if catalog is None:
        catalog = await openrouter_mod.fetch_catalog()
    await asyncio.to_thread(auto_price, catalog)
    _auto_last_run = time.time()
