"""Context windows, token estimation, and live usage meters.

Window sizes come from catalogs, name prefixes, and a learned per-(type, model)
cache; thresholds come from Settings so they can be tuned without a code
change. Compaction (checkpoints, projection, strategies) lives in
`app.context_compact`.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass

from .config import settings

CHARS_PER_TOKEN = 4
IMAGE_TOKENS = 1_500
ROLE_OVERHEAD = 4
MIN_WINDOW = 1_024
MAX_WINDOW = 8_000_000

# Pool models we list ourselves. OpenRouter catalog context_length wins when
# present; these are the fallbacks for first-party / pool ids.
KNOWN_WINDOWS = {
    "grok-4.7": 500_000,
    "grok-4.6": 500_000,
    "grok-4.5": 500_000,
    "gpt-6-astra": 1_050_000,
    "gpt-6-sol": 1_050_000,
    "gpt-6-luna": 1_050_000,
    "gpt-5.5": 1_050_000,
    "gpt-5.4": 1_000_000,
    "gpt-5.4-mini": 400_000,
    "gpt-5.3-codex-spark": 400_000,
    "gpt-5.6-sol": 1_050_000,
    "gpt-5.6-terra": 1_050_000,
    "gpt-5.6-luna": 1_050_000,
    "glm-5.3": 1_000_000,
    "glm-5.3-flash": 1_000_000,
    "glm-5.3-highspeed": 1_000_000,
    "glm-5.2": 1_000_000,
    "glm-5.2-highspeed": 1_000_000,
    "glm-5-2": 200_000,
    "glm-5-2-1m": 1_000_000,
    "glm-5-turbo": 1_000_000,
    "glm-4.7": 200_000,
    "k3": 262_144,
    "k3-256k": 262_144,
    "kimi-for-coding": 1_048_576,
    "kimi-for-coding-highspeed": 262_144,
    "mimo-v2.6-pro": 1_048_576,
    "mimo-v2.6-flash": 1_048_576,
    "mimo-v2.5-pro": 1_048_576,
    "mimo-v2.5": 1_048_576,
    "claude/claude-opus-5-5": 1_000_000,
    "claude/claude-sonnet-5": 1_000_000,
    "claude/claude-fable-5-1": 1_000_000,
    "claude/claude-haiku-4-5-20251001": 200_000,
}

TYPE_WINDOWS = {
    "grok": 500_000,
    "chatgpt": 1_050_000,
    "zai": 1_000_000,
    "devin": 200_000,
    "kimi": 262_144,
    "mimo": 1_048_576,
    "claude": 1_000_000,
    "openrouter": 128_000,
    "explabs": 128_000,
    "openai": 128_000,
    "ollama": 128_000,
    "llama": 8_192,
}

PREFIX_WINDOWS = (
    ("grok-", 500_000),
    ("gpt-5.4-mini", 400_000),
    ("gpt-", 1_050_000),
    ("glm-5-2-1m", 1_000_000),
    ("glm-5-2", 200_000),
    ("glm-4.7", 200_000),
    ("glm-", 1_000_000),
    ("kimi-for-coding", 1_048_576),
    ("k3-", 262_144),
    ("mimo-", 1_048_576),
    ("claude/", 200_000),
)

_WINDOW_CACHE: dict[tuple[str, str], tuple[float, int]] = {}
_WINDOW_TTL = 600.0

@dataclass(frozen=True)
class CompactionConfig:
    default_window: int
    warn_ratio: float
    auto_ratio: float
    auto: bool
    keep_tail: int
    prune_protect: int
    prune_minimum: int
    output_reserve: int
    strategy: str


def config() -> CompactionConfig:
    warn = _clamp_ratio(settings.context_warn_ratio, 0.80)
    auto = _clamp_ratio(settings.context_auto_ratio, 0.90)
    if auto < warn:
        auto = warn
    strategy = (settings.context_strategy or "summarize").strip().lower()
    from .context_compact import STRATEGIES  # lazy: registered in context_compact

    if strategy not in STRATEGIES:
        strategy = "summarize"
    return CompactionConfig(
        default_window=_clamp_window(settings.context_default_window, 128_000),
        warn_ratio=warn,
        auto_ratio=auto,
        auto=bool(settings.context_auto_compact),
        keep_tail=max(2, min(int(settings.context_keep_tail or 6), 40)),
        prune_protect=max(1_000, int(settings.context_prune_protect or 40_000)),
        prune_minimum=max(1_000, int(settings.context_prune_minimum or 20_000)),
        output_reserve=max(512, int(settings.context_output_reserve or 16_384)),
        strategy=strategy,
    )

def public_config() -> dict:
    cfg = config()
    return {
        "warn_ratio": cfg.warn_ratio,
        "auto_ratio": cfg.auto_ratio,
        "auto": cfg.auto,
        "keep_tail": cfg.keep_tail,
        "strategy": cfg.strategy,
        "default_window": cfg.default_window,
    }

def _clamp_ratio(value, fallback: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return fallback
    if number != number:  # NaN
        return fallback
    return max(0.05, min(number, 0.99))


def _clamp_window(value, fallback: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        # Provider-reported windows can arrive as "8192.0" or 8192.0.
        try:
            number = int(float(value))
        except (TypeError, ValueError, OverflowError):
            return fallback
    return max(MIN_WINDOW, min(number, MAX_WINDOW))

def remember_window(type_id: str, model: str, tokens: int) -> None:
    window = _clamp_window(tokens, 0)
    if window < MIN_WINDOW or not type_id or not model:
        return
    _WINDOW_CACHE[(str(type_id), str(model))] = (time.time(), window)


def cached_window(type_id: str, model: str) -> int | None:
    row = _WINDOW_CACHE.get((str(type_id), str(model)))
    if not row:
        return None
    seen, window = row
    if time.time() - seen > _WINDOW_TTL:
        return None
    return window


def learned_window(model: str) -> int | None:
    """Most recently learned window for a model id with no known source type.

    A fresh thread on a local/generic model id (e.g. a llama.cpp name) has no
    type prefix to resolve a window from, so its view would show the 128k
    default until the first run persists a meter. If some source already
    served this exact model name and reported its window, use that.
    """
    now = time.time()
    best: tuple[float, int] | None = None
    for (_ctype, cmodel), (seen, window) in _WINDOW_CACHE.items():
        if cmodel == model and now - seen <= _WINDOW_TTL:
            if best is None or seen > best[0]:
                best = (seen, window)
    return best[1] if best else None

def type_id_for_model(model: str) -> str | None:
    mid = (model or "").strip()
    if mid.startswith("explabs/"):
        return "explabs"
    if mid.startswith("openrouter/"):
        return "openrouter"
    if mid.startswith("devin/"):
        return "devin"
    if mid.startswith("grok"):
        return "grok"
    if mid.startswith("gpt-"):
        return "chatgpt"
    if mid.startswith("glm-"):
        return "zai"
    if mid.startswith("kimi-") or mid == "k3" or mid.startswith("k3-"):
        return "kimi"
    if mid.startswith("mimo-"):
        return "mimo"
    if mid.startswith("claude/"):
        return "claude"
    return None


def source_type_for_model(model: str, prior: dict | None = None) -> str | None:
    """A resolved backend beats name-prefix guesses (e.g. Ollama gpt-oss)."""
    if (prior or {}).get("model") == model:
        kind = (prior or {}).get("source_type") or ((prior or {}).get("calibration") or {}).get("type_id")
        if kind in TYPE_WINDOWS:
            return kind
    return type_id_for_model(model)

def _cached_openrouter_catalog() -> dict | None:
    try:
        from .sources.openrouter import cached_catalog
    except Exception:
        return None
    try:
        return cached_catalog()
    except Exception:
        return None

def window_for(
    model: str,
    type_id: str | None = None,
    *,
    catalog: dict | None = None,
    override: int | None = None,
) -> int:
    if override:
        return _clamp_window(override, config().default_window)
    mid = (model or "").strip()
    cfg = config()
    kind = type_id or type_id_for_model(mid)
    if catalog is None and (kind == "openrouter" or mid.startswith("openrouter/")):
        catalog = _cached_openrouter_catalog()
    if catalog:
        rec = _catalog_record(mid, catalog)
        if rec:
            for key in ("context_length", "context_window", "max_context"):
                got = _positive_int(rec.get(key))
                if got:
                    return _clamp_window(got, cfg.default_window)
    if kind:
        cached = cached_window(kind, mid)
        if cached:
            return cached
    elif learned := learned_window(mid):
        # Unknown backend: a window learned from whichever source served this
        # exact model name beats the generic default.
        return learned
    # Local / generic OpenAI-compat backends must not inherit pool prefixes
    # (gpt-oss on Ollama is not GPT-5.6).
    if kind in ("llama", "ollama", "openai"):
        return TYPE_WINDOWS.get(kind, cfg.default_window)
    if kind == "kimi":
        try:
            from .sources.kimi import model_window

            got = model_window(mid)
            if got:
                return _clamp_window(got, cfg.default_window)
        except Exception:
            pass
    if kind == "claude":
        try:
            from .sources.claude import model_window as claude_window

            got = claude_window(mid)
            if got:
                return _clamp_window(got, cfg.default_window)
        except Exception:
            pass
    if mid in KNOWN_WINDOWS:
        return KNOWN_WINDOWS[mid]
    probe = mid[6:] if mid.startswith("devin/") else mid
    candidates = [probe]
    if kind == "devin" or mid.startswith("devin/"):
        try:
            from .sources.devin import canonical_upstream

            canon = canonical_upstream(mid)
            if canon:
                candidates.append(canon)
        except Exception:
            pass
    for candidate in candidates:
        if candidate and candidate in KNOWN_WINDOWS:
            return KNOWN_WINDOWS[candidate]
    if mid.startswith("openrouter/"):
        upstream = mid[len("openrouter/") :]
        if catalog:
            rec = (catalog.get("by_id") or {}).get(upstream)
            got = _positive_int((rec or {}).get("context_length"))
            if got:
                return _clamp_window(got, cfg.default_window)
    if kind == "explabs":
        # Never inherit a pool's window from the prefix heuristics below (an
        # explabs model named glm-… or grok-… is not that pool's model); the
        # remembered catalog window above already carried the real value.
        return TYPE_WINDOWS[kind]
    for candidate in candidates:
        for prefix, tokens in PREFIX_WINDOWS:
            if candidate.startswith(prefix):
                return tokens
    if kind and kind in TYPE_WINDOWS:
        return TYPE_WINDOWS[kind]
    return cfg.default_window

def _catalog_record(model: str, catalog: dict) -> dict | None:
    by_id = catalog.get("by_id") if isinstance(catalog, dict) else None
    if not isinstance(by_id, dict):
        return None
    mid = (model or "").strip()
    if mid in by_id and isinstance(by_id[mid], dict):
        return by_id[mid]
    for prefix in ("openrouter/", "explabs/"):
        if mid.startswith(prefix) and mid[len(prefix):] in by_id:
            rec = by_id[mid[len(prefix):]]
            return rec if isinstance(rec, dict) else None
    return None


def _positive_int(value) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number >= MIN_WINDOW else None

# CJK/fullwidth/emoji runs are ~1 token per char; a flat 4-char divide
# undercounts them about 4x.
_WIDE_CHARS = re.compile(r"[^\x00-\u2e7f]")


def estimate_text(text: str) -> int:
    if not text:
        return 0
    wide = len(_WIDE_CHARS.findall(text))
    narrow = len(text) - wide
    return max(1, wide + (narrow + CHARS_PER_TOKEN - 1) // CHARS_PER_TOKEN)


def estimate_content(content) -> int:
    if content is None:
        return 0
    if isinstance(content, str):
        return estimate_text(content)
    if isinstance(content, list):
        total = 0
        for part in content:
            if isinstance(part, str):
                total += estimate_text(part)
            elif isinstance(part, dict):
                kind = part.get("type")
                if kind in ("image_url", "input_image"):
                    total += IMAGE_TOKENS
                else:
                    total += estimate_text(str(part.get("text") or part.get("content") or ""))
        return total
    if isinstance(content, dict):
        return estimate_content(content.get("text") or content.get("content") or "")
    return estimate_text(str(content))


def _reasoning_text(msg: dict) -> str:
    """Replayed reasoning: text fields, or Claude's signed thinking blocks."""
    reasoning = msg.get("reasoning_content") or msg.get("reasoning")
    if isinstance(reasoning, str) and reasoning:
        return reasoning
    blocks = msg.get("claude_thinking")
    if not isinstance(blocks, list):
        return ""
    return "\n".join(str(block.get("thinking") or block.get("text") or "")
                     for block in blocks if isinstance(block, dict))


def estimate_messages(messages: list | None) -> int:
    total = 0
    for msg in messages or []:
        if not isinstance(msg, dict):
            continue
        total += ROLE_OVERHEAD
        total += estimate_content(msg.get("content"))
        total += estimate_text(_reasoning_text(msg))
        for call in msg.get("tool_calls") or []:
            if not isinstance(call, dict):
                continue
            fn = call.get("function") if isinstance(call.get("function"), dict) else {}
            total += estimate_text(str(fn.get("name") or "") + str(fn.get("arguments") or ""))
    # Attachments are not counted separately: durable rows already embed them
    # in content (chats.model_content), so summing both would double-count.
    return total

_TOOL_FAMILY = {
    "web_search": "web search",
    "read_url": "web search",
    "run_terminal_command": "shell",
    "get_command_output": "shell",
    "kill_command": "shell",
    "monitor": "shell",
    "read_file": "files",
    "list_dir": "files",
    "grep": "files",
    "glob": "files",
    "write_file": "files",
    "search_replace": "files",
    "create_artifact": "artifacts",
    "update_artifact": "artifacts",
    "spawn_subagent": "subagents",
    "get_subagent_output": "subagents",
    "kill_subagent": "subagents",
    "list_subagents": "subagents",
    "search_models": "models",
    "search_tools": "catalog",
    "load_skill": "skills",
    "todo_write": "todos",
    "scratch": "scratch",
    "read_transcript": "transcript",
}


def tool_family(name: str) -> str:
    key = str(name or "").strip()
    return _TOOL_FAMILY.get(key) or (key.replace("_", " ") if key else "tools")

def _trace_chars(trace: dict) -> int:
    bits = [
        str(trace.get("name") or ""),
        str(trace.get("command") or ""),
        str(trace.get("path") or ""),
        str(trace.get("query") or ""),
        str(trace.get("url") or ""),
        str(trace.get("pattern") or ""),
        str(trace.get("preview") or ""),
        str(trace.get("summary") or ""),
        str(trace.get("error") or ""),
        str(trace.get("description") or ""),
    ]
    queries = trace.get("queries")
    if isinstance(queries, list):
        bits.extend(str(item) for item in queries if isinstance(item, str))
    return estimate_text(" ".join(bit for bit in bits if bit))

def occupancy_parts(messages: list | None, *, tools=None) -> dict:
    """Char-estimate breakdown: system, messages, thinking, tool traces by family."""
    system = messages_n = thinking = 0
    families: dict[str, int] = {}
    names = {str(call.get("id") or ""): str((call.get("function") or {}).get("name") or "")
             for msg in messages or [] if isinstance(msg, dict)
             for call in msg.get("tool_calls") or [] if isinstance(call, dict)}
    for msg in messages or []:
        if not isinstance(msg, dict):
            continue
        role = msg.get("role")
        if role == "system":
            system += estimate_messages([msg])
            continue
        if role == "tool":
            family = tool_family((msg.get("name") or msg.get("tool_name") or names.get(str(msg.get("tool_call_id") or "")) or "tools"))
            families[family] = families.get(family, 0) + estimate_messages([msg])
            continue
        if role not in ("user", "assistant"):
            continue
        body = dict(msg)
        traces = list(body.pop("tools", None) or [])
        calls = list(body.pop("tool_calls", None) or [])
        reasoning = _reasoning_text(body)
        if reasoning:
            thinking += estimate_text(reasoning)
            body = {**body, "reasoning": "", "reasoning_content": "", "claude_thinking": []}
        messages_n += estimate_messages([body])
        for call in calls:
            if not isinstance(call, dict):
                continue
            fn = call.get("function") if isinstance(call.get("function"), dict) else {}
            name = str(fn.get("name") or call.get("name") or "tools")
            family = tool_family(name)
            families[family] = families.get(family, 0) + estimate_text(
                name + str(fn.get("arguments") or "")
            )
        for trace in traces:
            if not isinstance(trace, dict):
                continue
            family = tool_family(trace.get("name"))
            families[family] = families.get(family, 0) + _trace_chars(trace)
    schema = estimate_tools(tools)
    tool_total = sum(families.values())
    history = messages_n + thinking + tool_total
    return {
        "system": system,
        "history": history,
        "messages": messages_n,
        "thinking": thinking,
        "tool_calls": tool_total,
        "tool_families": families,
        "tools": schema,
        "draft": 0,
        "total": system + history + schema,
    }


def estimate_tools(tools) -> int:
    """Token estimate for the request `tools` array (function schemas)."""
    if not tools:
        return 0
    if isinstance(tools, (list, dict)):
        try:
            raw = json.dumps(tools, ensure_ascii=False, default=str)
        except (TypeError, ValueError, RecursionError):
            raw = str(tools)
        return estimate_text(raw)
    return estimate_text(str(tools))

def prompt_token_count(usage: dict | None) -> int:
    """Provider input size of one request. Not completion, and not turn billing."""
    if not isinstance(usage, dict):
        return 0
    return _nonneg(usage.get("prompt_tokens") or usage.get("input_tokens"))


def used_from_usage(usage: dict | None) -> int:
    """Context occupancy of a single model request, not billed-turn totals.

    Prompt + completion is the size of that request. `total_tokens` on a
    dashboard usage object is often the *sum* of every tool-loop round, which
    is billing, not how full the window is. Reasoning that is not sent back
    on the next request (Grok's disjoint `reasoning_tokens`) is excluded.
    """
    if not isinstance(usage, dict):
        return 0
    prompt = _nonneg(usage.get("prompt_tokens") or usage.get("input_tokens"))
    completion = _nonneg(usage.get("completion_tokens") or usage.get("output_tokens"))
    if prompt or completion:
        return prompt + completion
    return _nonneg(usage.get("total_tokens"))


def _nonneg(value) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return 0
    return max(0, number)

def trigger_used(limit: int, *, auto_ratio: float | None = None) -> int:
    cfg = config()
    window = _clamp_window(limit, cfg.default_window)
    ratio = cfg.auto_ratio if auto_ratio is None else _clamp_ratio(auto_ratio, cfg.auto_ratio)
    by_ratio = int(window * ratio)
    # Leave a small buffer for the compact call itself when the ratio is set
    # very high; at the default 90% this does not move the trigger.
    reserve = min(cfg.output_reserve, max(256, window // 10))
    by_reserve = window - reserve
    # Never raise the trigger above window-1: a MIN_WINDOW floor there would
    # arm compaction only once the window is already full.
    return max(1, min(by_ratio, by_reserve, window - 1))


def should_warn(used: int, limit: int, *, warn_ratio: float | None = None) -> bool:
    cfg = config()
    window = _clamp_window(limit, cfg.default_window)
    ratio = cfg.warn_ratio if warn_ratio is None else _clamp_ratio(warn_ratio, cfg.warn_ratio)
    return used >= int(window * ratio)


def should_compact(used: int, limit: int, *, auto_ratio: float | None = None) -> bool:
    return used >= trigger_used(limit, auto_ratio=auto_ratio)


def should_auto_compact(
    used: int,
    limit: int,
    *,
    estimated: bool = False,
    prior_used: int = 0,
    auto_ratio: float | None = None,
) -> bool:
    """Whether to run auto-compact now.

    Provider occupancy can trigger immediately. A 4-char estimate is allowed
    to warn, but must not compact unless the last standing snapshot was
    already over the line — otherwise a send flashes 150%+ and compacts a
    4k-token chat.
    """
    if not config().auto:
        return False
    if not should_compact(used, limit, auto_ratio=auto_ratio):
        return False
    if estimated and int(prior_used or 0) < trigger_used(limit, auto_ratio=auto_ratio):
        return False
    return True

def meter(
    *,
    used: int,
    limit: int,
    model: str = "",
    estimated: bool = False,
    extra: dict | None = None,
) -> dict:
    cfg = config()
    window = _clamp_window(limit, cfg.default_window)
    tokens = max(0, int(used or 0))
    ratio = tokens / window if window else 0.0
    out = {
        "used": tokens,
        "limit": window,
        "model": model or "",
        "estimated": bool(estimated),
        "ratio": round(ratio, 4),
        "warn": should_warn(tokens, window),
        "auto": should_compact(tokens, window),
        "warn_ratio": cfg.warn_ratio,
        "auto_ratio": cfg.auto_ratio,
        "auto_at": trigger_used(window),
        "strategy": cfg.strategy,
        "auto_enabled": cfg.auto,
    }
    if extra:
        out.update(extra)
    return out

def _attach_parts(snap: dict, parts: dict | None) -> dict:
    if not isinstance(parts, dict) or not parts:
        return snap
    families = {}
    raw_families = parts.get("tool_families")
    if isinstance(raw_families, dict):
        for key, value in raw_families.items():
            name = str(key or "").strip()[:40]
            n = max(0, int(value or 0))
            if name and n:
                families[name] = n
    snap["parts"] = {
        "system": max(0, int(parts.get("system") or 0)),
        "history": max(0, int(parts.get("history") or 0)),
        "messages": max(0, int(parts.get("messages") or 0)),
        "thinking": max(0, int(parts.get("thinking") or 0)),
        "tool_calls": max(0, int(parts.get("tool_calls") or 0)),
        "tool_families": families,
        "tools": max(0, int(parts.get("tools") or 0)),
        "draft": max(0, int(parts.get("draft") or 0)),
        "total": max(0, int(parts.get("total") or 0)),
    }
    return snap

def meter_from_messages(
    messages: list | None,
    *,
    model: str,
    type_id: str | None = None,
    usage: dict | None = None,
    extra_tokens: int = 0,
    catalog: dict | None = None,
    override: int | None = None,
    tools=None,
    calibration: dict | None = None,
) -> dict:
    limit = window_for(model, type_id, catalog=catalog, override=override)
    extra = max(0, extra_tokens)
    from_usage = used_from_usage(usage)
    if type_id:
        from . import model_caps

        messages, _ = model_caps.prepare(messages, type_id, model)
    parts = occupancy_parts(messages, tools=tools)
    if extra:
        parts["draft"] = extra
        parts["total"] = parts["total"] + extra
    if from_usage:
        return _attach_parts(
            meter(used=from_usage + extra, limit=limit, model=model, estimated=False),
            parts,
        )
    return calibrate_meter(_attach_parts(
        meter(used=parts["total"], limit=limit, model=model, estimated=True),
        parts,
    ), calibration)

def prompt_calibration(usage: dict | None, request_estimate: int, *, model: str, type_id: str) -> dict | None:
    """Learn only from one request's inclusive input count, never turn billing.

    Normalized prompt_tokens includes cached tokens. Adding cached_tokens again
    would double count; output/reasoning must not calibrate retained history.
    """
    # Devin's RPC reports billing/cache deltas, not a guaranteed inclusive
    # prompt size. Do not turn those deltas into an occupancy measurement.
    if type_id == "devin":
        return None
    usage = usage or {}
    prompt = _nonneg(usage.get("prompt_tokens") or usage.get("input_tokens"))
    if not prompt or request_estimate <= 0:
        return None
    return {"model": model, "type_id": type_id, "prompt_tokens": prompt,
            "estimated_prompt_tokens": request_estimate}

def _fit_parts(parts: dict | None, target: int) -> dict:
    keys = ("system", "messages", "thinking", "tool_calls", "tools", "draft")
    raw = parts if isinstance(parts, dict) else {}
    weights = {key: _nonneg(raw.get(key)) for key in keys}
    weight = sum(weights.values())
    total = max(0, int(target or 0))
    if not weight:
        fitted = {key: 0 for key in keys}
        fitted["messages"] = total
    else:
        fitted = {key: value * total // weight for key, value in weights.items()}
        order = sorted(weights, key=lambda key: weights[key] * total % weight, reverse=True)
        for key in order[: total - sum(fitted.values())]:
            fitted[key] += 1
    raw_families = raw.get("tool_families") if isinstance(raw.get("tool_families"), dict) else {}
    fam_weights = {str(key): _nonneg(value) for key, value in raw_families.items() if str(key)}
    fam_weight = sum(fam_weights.values())
    fam_target = fitted["tool_calls"]
    if not fam_weight or not fam_target:
        families = {key: 0 for key in fam_weights}
    else:
        families = {key: value * fam_target // fam_weight for key, value in fam_weights.items()}
        order = sorted(fam_weights, key=lambda key: fam_weights[key] * fam_target % fam_weight, reverse=True)
        for key in order[: fam_target - sum(families.values())]:
            families[key] += 1
    fitted["tool_families"] = families
    fitted["history"] = fitted["messages"] + fitted["thinking"] + fitted["tool_calls"]
    fitted["total"] = total
    return fitted

def next_request_meter(
    messages: list | None,
    *,
    model: str,
    type_id: str | None = None,
    tools=None,
    override: int | None = None,
    catalog: dict | None = None,
    anchor_prompt: int,
    added: list | None = None,
    calibration: dict | None = None,
) -> dict:
    """Tokens in the next request: last provider prompt plus new text only.

    The calibration ratio is not applied. New tool text is a char estimate of
    the suffix, so a shell result cannot inflate the whole history.
    """
    limit = window_for(model, type_id, catalog=catalog, override=override)
    extra = estimate_messages(added or [])
    target = max(0, int(anchor_prompt or 0)) + extra
    parts = _fit_parts(occupancy_parts(messages, tools=tools), target)
    snap = meter(
        used=target,
        limit=limit,
        model=model,
        estimated=bool(extra),
        extra={"parts": parts, "basis": "provider_prompt", "source_type": type_id or ""},
    )
    snap["basis"] = "provider_prompt"
    if calibration:
        snap["calibration"] = dict(calibration)
    return snap

def calibrate_meter(snap: dict, calibration: dict | None, *, measured_used: int = 0) -> dict:
    """Scale a fresh payload estimate using this model's last measured input.

    Rebuilt/edited/compacted payloads remain estimates, not stale high-water
    marks. Raw parts are retained so inherited overhead is never scaled twice.
    """
    snap["basis"] = "payload_chars_v1"
    if not calibration or calibration.get("model") != snap.get("model"):
        return snap
    prompt = _nonneg(calibration.get("prompt_tokens"))
    baseline = _nonneg(calibration.get("estimated_prompt_tokens"))
    if not prompt or not baseline:
        return snap
    raw = dict(snap.get("parts") or {})
    raw_total = _nonneg(raw.get("total"))
    target = measured_used or round(raw_total * prompt / baseline)
    def allocate(values, total):
        weights = {key: _nonneg(value) for key, value in values.items()}
        weight = sum(weights.values())
        if not weight:
            return {key: 0 for key in weights}
        scaled = {key: value * total // weight for key, value in weights.items()}
        # Largest remainders preserve nonnegative integers and exact totals.
        order = sorted(weights, key=lambda key: weights[key] * total % weight, reverse=True)
        for key in order[:total - sum(scaled.values())]:
            scaled[key] += 1
        return scaled

    parts = allocate({key: raw.get(key) for key in
                      ("system", "messages", "thinking", "tool_calls", "tools", "draft")}, target)
    parts["tool_families"] = allocate(raw.get("tool_families") or {}, parts["tool_calls"])
    parts["history"] = parts["messages"] + parts["thinking"] + parts["tool_calls"]
    parts["total"] = target
    updated = meter(used=target, limit=snap["limit"], model=snap["model"], estimated=not bool(measured_used))
    return {**snap, **updated, "parts": parts, "raw_parts": raw,
            "calibration": dict(calibration),
            "basis": "provider_usage" if measured_used else "provider_calibrated"}

def standing_meter(
    messages: list | None,
    *,
    model: str,
    type_id: str | None = None,
    extra_tokens: int = 0,
    catalog: dict | None = None,
    override: int | None = None,
    tools=None,
    prior: dict | None = None,
) -> dict:
    """Measure retained replay history, never summed billing or a stale floor.

    A previous snapshot may supply overhead and model calibration. Tool results
    in the supplied replay projection remain part of the next request.
    """
    # Callers supply the actual replay projection, including retained tool
    # pairs. Dropping tool rows here would recreate the cross-turn undercount.
    rows = [msg for msg in messages or [] if isinstance(msg, dict)]
    type_id = type_id or source_type_for_model(model, prior)
    if not override and type_id in ("llama", "ollama", "openai") and (prior or {}).get("model") == model:
        override = cached_window(type_id, model) or (prior or {}).get("limit")
    snap = meter_from_messages(
        rows,
        model=model,
        type_id=type_id,
        extra_tokens=extra_tokens,
        catalog=catalog,
        override=override,
        tools=tools,
    )
    snap["estimated"] = True
    snap["standing"] = True
    parts = dict(snap["parts"])
    previous = (prior or {}).get("raw_parts") or (prior or {}).get("parts") or {}
    if not any(m.get("role") == "system" for m in rows):
        parts["system"] = _nonneg(previous.get("system"))
    # An empty tools list still means "schemas absent from durable rows"
    # (e.g. web search toggled off) — inherit the overhead either way.
    if not tools:
        parts["tools"] = _nonneg(previous.get("tools"))
    parts["total"] = parts["system"] + parts["history"] + parts["tools"] + parts["draft"]
    return calibrate_meter(meter(used=parts["total"], limit=snap["limit"], model=model,
                 estimated=True, extra={"standing": True, "scope": "transcript", "parts": parts, "source_type": type_id}),
                 (prior or {}).get("calibration"))

def context_event(meter_obj: dict, *, phase: str = "update") -> dict:
    return {"fuse": {**meter_obj, "event": "context", "phase": phase}}
