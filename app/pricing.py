from decimal import Decimal, InvalidOperation

import httpx
import json

from . import db

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
    long_rates = (tiers or [{}])[0]
    want["long_context_threshold"] = threshold if tiers else None
    want["long_input_micros_per_million"] = long_rates.get("input_rate") if tiers else None
    want["long_cached_input_micros_per_million"] = long_rates.get("cached_rate") if tiers else None
    want["long_output_micros_per_million"] = long_rates.get("output_rate") if tiers else None
    want["long_cached_input_known"] = 1 if (tiers and long_rates.get("cached_rate") is not None) else 0
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
            if existing and existing["origin"] != "default":
                continue
            tiers = [_tier(threshold, long_rates)] if long_rates else None
            if existing and _default_row_matches(
                existing, inp=inp, cached=cached, write=write, out=out, tiers=tiers, threshold=threshold
            ):
                continue
            db.save_model_price(
                provider,
                model,
                _usd_per_million(inp),
                _usd_per_million(cached),
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
    from .sources.chatgpt import CHATGPT_MODELS

    from .sources.zai import ZAI_MODELS

    author_providers = (
        ("x-ai", "grok", None),
        ("openai", "openai", None),
        ("openai", CHATGPT_PROVIDER, set(CHATGPT_MODELS)),
        ("z-ai", "zai", set(ZAI_MODELS)),
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
    models = sorted(imported - manual_ids)
    return {"count": len(models), "models": models}
