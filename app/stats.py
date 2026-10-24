from __future__ import annotations

import json
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from . import accounts, db
from .sources import PROVIDER_LABELS
from .sources import chatgpt as chatgpt_mod
from .sources import grok as grok_mod
from .sources import kimi as kimi_mod
from .sources import mimo as mimo_mod
from .sources import claude as claude_mod
from .sources import cursor as cursor_mod
from .sources import nanogpt as nanogpt_mod
from .sources import zai as zai_mod
from .sources.pool_pick import (
    CHATGPT_FREE_PLANS,
    USAGE_BUSY,
    chatgpt_plan_type,
    chatgpt_usage_percent,
    grok_usage_percent,
    kimi_usage_percent,
    mimo_usage_percent,
    zai_usage_percent,
)

PRODUCT_NAMES = {
    "GrokBuild": "Build",
    "GrokChat": "Chat",
    "GrokVoice": "Voice",
    "GrokImagine": "Imagine",
    "GrokAPI": "API",
}


def format_usd_cents(cents: int | None) -> str:
    n = int(cents or 0)
    sign = "-" if n < 0 else ""
    n = abs(n)
    return f"{sign}${n / 100:,.2f}"


def format_usd_micros(micros: int | None) -> str:
    n = int(micros or 0)
    if n == 0:
        return "$0.00"
    dollars = n / 1_000_000
    sign = "-" if dollars < 0 else ""
    dollars = abs(dollars)
    if dollars >= 1:
        return f"{sign}${dollars:,.2f}"
    text = f"{sign}${dollars:,.6f}".rstrip("0").rstrip(".")
    return text if text not in ("$", "-$", "") else f"{sign}$0.00"


def format_duration(seconds) -> str:
    try:
        s = int(max(0, float(seconds)))
    except (TypeError, ValueError):
        return ""
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m"
    hours, rem = divmod(s, 3600)
    if hours < 48:
        mins = rem // 60
        return f"{hours}h {mins}m" if mins else f"{hours}h"
    days, rem_h = divmod(hours, 24)
    return f"{days}d {rem_h}h" if rem_h else f"{days}d"


def format_pct(n) -> str | None:
    if n is None:
        return None
    try:
        v = float(n)
    except (TypeError, ValueError):
        return None
    text = f"{v:.1f}".rstrip("0").rstrip(".")
    return text + "%"


def format_when(ts: str | None) -> str:
    if not ts:
        return ""
    raw = str(ts).replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        dt = dt.astimezone(timezone.utc)
    except ValueError:
        return str(ts)
    return dt.strftime("%d %b %Y %H:%M UTC").lstrip("0")


def period_label(kind: str | None) -> str:
    t = (kind or "").upper()
    if "WEEK" in t:
        return "Weekly"
    if "MONTH" in t:
        return "Monthly"
    if "DAY" in t:
        return "Daily"
    if "HOUR" in t:
        return "5-hour"
    if "CREDIT" in t:
        return "Credits"
    return "Usage"


def _as_float(v) -> float | None:
    if v is None or isinstance(v, bool):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def format_usd_dollars(micros: int | None) -> str:
    return f"${int(micros or 0) / 1_000_000:,.2f}"


def plan_type_label(value: str | None) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    return raw[:1].upper() + raw[1:].lower()


def _usage_row(row) -> dict:
    prompt = int(row["prompt_tokens"] or 0)
    completion = int(row["completion_tokens"] or 0)
    output = int(row["output_tokens"] or 0) or max(completion, int(row["reasoning_tokens"] or 0))
    reasoning = min(int(row["reasoning_tokens"] or 0), output)
    visible = min(int(row["visible_tokens"] or 0) or max(output - reasoning, 0), output)
    cached = min(int(row["cached_tokens"] or 0), prompt)
    cache_write = min(int(row["cache_write_tokens"] or 0), max(prompt - cached, 0))
    audio = int(row["audio_tokens"] or 0)
    image = int(row["image_tokens"] or 0)
    total = int(row["total_tokens"] or 0) or (prompt + output)
    requests = int(row["requests"] or 0)
    cost = int(row["cost_micros"] or 0)
    return {
        "prompt_tokens": prompt,
        "uncached_tokens": max(prompt - cached - cache_write, 0),
        "completion_tokens": completion,
        "output_tokens": output,
        "visible_tokens": visible,
        "reasoning_tokens": reasoning,
        "cached_tokens": cached,
        "cache_write_tokens": cache_write,
        "audio_tokens": audio,
        "image_tokens": image,
        "total_tokens": total,
        "requests": requests,
        "cost_micros": cost,
        "cost_label": format_usd_micros(cost) if cost else "",
        "last_model": row["last_model"] or "",
    }


def _empty_totals() -> dict:
    return _usage_row(
        {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "output_tokens": 0,
            "visible_tokens": 0,
            "reasoning_tokens": 0,
            "cached_tokens": 0,
            "cache_write_tokens": 0,
            "audio_tokens": 0,
            "image_tokens": 0,
            "total_tokens": 0,
            "requests": 0,
            "cost_micros": 0,
            "last_model": "",
        }
    )


def _add(dst: dict, src: dict) -> None:
    for k in (
        "prompt_tokens",
        "uncached_tokens",
        "completion_tokens",
        "output_tokens",
        "visible_tokens",
        "reasoning_tokens",
        "cached_tokens",
        "cache_write_tokens",
        "audio_tokens",
        "image_tokens",
        "total_tokens",
        "requests",
        "cost_micros",
    ):
        dst[k] = dst.get(k, 0) + src[k]
    if src.get("last_model"):
        dst["last_model"] = src["last_model"]


def current_usage(user_id: int) -> dict:
    keys = []
    totals = _empty_totals()
    for row in accounts.keys_for(user_id):
        item = _usage_row(row)
        item.update(
            {
                "id": row["id"],
                "name": row["name"],
                "prefix": row["prefix"],
                "created_at": row["created_at"],
                "last_used_at": row["last_used_at"] or "",
                "updated_at": row["usage_updated_at"] or "",
            }
        )
        keys.append(item)
        _add(totals, item)
    totals["cost_label"] = format_usd_micros(totals["cost_micros"]) if totals["cost_micros"] else ""

    peak = max((p["total_tokens"] for p in keys), default=0)
    providers = []
    for row in db.provider_usage_for(user_id):
        item = _usage_row(row)
        pid = row["provider"]
        item.update(
            {
                "id": pid,
                "name": PROVIDER_LABELS.get(pid, pid or "Unknown"),
                "updated_at": row["updated_at"] or "",
            }
        )
        providers.append(item)
    pmax = max((p["total_tokens"] for p in providers), default=0)
    for p in providers:
        p["fill"] = min(100, round((p["total_tokens"] * 100) / pmax)) if pmax else 0

    if providers:
        totals = _empty_totals()
        for item in providers:
            _add(totals, item)
        totals["cost_label"] = format_usd_micros(totals["cost_micros"]) if totals["cost_micros"] else ""

    mix_parts = [
        # Non-overlapping categories: cached reads are part of prompt tokens,
        # reasoning is part of generated output. Summing these never
        # double-counts a token.
        ("uncached input", totals["uncached_tokens"]),
        ("cached read", totals["cached_tokens"]),
        ("visible output", totals["visible_tokens"]),
        ("reasoning", totals["reasoning_tokens"]),
    ]
    mix_sum = sum(v for _, v in mix_parts) or 0
    mix = []
    for name, val in mix_parts:
        mix.append(
            {
                "name": name,
                "tokens": val,
                "share": round((val * 100) / mix_sum) if mix_sum else 0,
                "flex": max(val, 0) or (0.0001 if mix_sum == 0 else 0),
            }
        )

    return {
        "api_keys": keys,
        "providers": providers,
        "totals": totals,
        "mix": mix,
        "has_usage": bool(totals["requests"] or totals["total_tokens"]),
        "peak_key": peak,
    }


_RANGES = {
    "30m": (timedelta(minutes=30), 300, "5-minute"),
    "6h": (timedelta(hours=6), 900, "15-minute"),
    "24h": (timedelta(hours=24), 3600, "hourly"),
    "3d": (timedelta(days=3), 10800, "3-hour"),
    "7d": (timedelta(days=7), 21600, "6-hour"),
    "30d": (timedelta(days=30), 86400, "daily"),
}


def _change(current, previous) -> float | None:
    try:
        prev = float(previous or 0)
        cur = float(current or 0)
    except (TypeError, ValueError):
        return None
    if prev == 0:
        return None
    return round(((cur - prev) * 100) / prev, 1)


def _window_filters(
    user_id: int, start: str, end: str, provider: str, all_users: bool, *, subject_id: int | None = None
) -> tuple[str, list]:
    clauses = ["e.created_at>=?", "e.created_at<?"]
    args: list = [start, end]
    if not all_users:
        clauses.append("e.user_id=?")
        args.append(subject_id if subject_id is not None else user_id)
    if provider:
        clauses.append("COALESCE(NULLIF(e.source_type, ''), s.type, 'unknown')=?")
        args.append(provider)
    return " AND ".join(clauses), args


def _event_summary(
    user_id: int, start: str, end: str, provider: str, all_users: bool, *, subject_id: int | None = None,
    model_tokens: dict | None = None,
) -> dict:
    """Window totals. With model_tokens, the same scan also fills it with
    tokens per provider and model (one pass instead of two)."""
    where, args = _window_filters(user_id, start, end, provider, all_users, subject_id=subject_id)
    by_model = model_tokens is not None
    group_cols = ("""COALESCE(NULLIF(e.source_type, ''), s.type, 'unknown') AS provider,
                    COALESCE(NULLIF(e.model, ''), 'Unknown') AS model, """ if by_model else "")
    rows = db.fetchall(
        f"""SELECT {group_cols}COALESCE(SUM(CASE WHEN e.total_tokens>0 THEN e.total_tokens
                                      ELSE e.prompt_tokens+e.output_tokens END), 0) AS tokens,
                    COALESCE(SUM(e.count_request), 0) AS requests,
                    COALESCE(SUM(e.prompt_tokens), 0) AS prompt,
                    COALESCE(SUM(e.completion_tokens), 0) AS completion,
                    COALESCE(SUM(e.output_tokens), 0) AS output,
                    COALESCE(SUM(e.reasoning_tokens), 0) AS reasoning,
                    COALESCE(SUM(e.cached_tokens), 0) AS cached,
                    COALESCE(SUM(e.cache_write_tokens), 0) AS cache_write,
                    COALESCE(SUM(CASE WHEN e.count_request=1 AND e.request_status='success' THEN 1 ELSE 0 END), 0) AS ok_requests,
                    COALESCE(SUM(CASE WHEN e.count_request=1 AND e.request_status='failed' THEN 1 ELSE 0 END), 0) AS failed,
                    COALESCE(SUM(CASE WHEN e.count_request=1 AND e.request_status='cancelled' THEN 1 ELSE 0 END), 0) AS cancelled,
                    COALESCE(SUM(COALESCE(e.reported_cost_micros, e.estimated_cost_micros, 0)), 0) AS cost,
                    COALESCE(SUM(e.estimated_cost_micros), 0) AS estimated_cost,
                    COALESCE(SUM(CASE WHEN e.count_request=1 AND
                                           (e.reported_cost_micros IS NOT NULL OR e.pricing_known=1)
                                      THEN 1 ELSE 0 END), 0) AS priced,
                    COALESCE(SUM(CASE WHEN e.reported_cost_micros IS NOT NULL OR e.pricing_known=1
                                      THEN CASE WHEN e.total_tokens>0 THEN e.total_tokens
                                                ELSE e.prompt_tokens+e.output_tokens END
                                      ELSE 0 END), 0) AS priced_tokens,
                    COALESCE(SUM(CASE WHEN e.count_request=1 AND
                                           COALESCE(NULLIF(e.source_type, ''), s.type, 'unknown')='openrouter'
                                      THEN 1 ELSE 0 END), 0) AS untracked
             FROM usage_events e LEFT JOIN sources s ON s.id=e.source_id
             WHERE {where}{" GROUP BY provider, model" if by_model else ""}""",
        tuple(args),
    )
    totals = dict.fromkeys(_SUMMARY_KEYS, 0)
    for row in rows:
        for key in _SUMMARY_KEYS:
            totals[key] += int(row[key] or 0)
        if by_model:
            model_tokens[f"{row['provider']}\x1f{row['model']}"] = int(row["tokens"] or 0)
    return totals


_SUMMARY_KEYS = ("tokens", "requests", "prompt", "completion", "output", "reasoning", "cached", "cache_write",
                 "ok_requests", "failed", "cancelled", "cost", "estimated_cost", "priced", "priced_tokens",
                 "untracked")


def _hit_rate(cached: int, prompt: int) -> float | None:
    if not prompt:
        return None
    return round((cached * 100) / prompt, 1)


def _blended(cost: int, tokens: int) -> float | None:
    if not tokens:
        return None
    return cost / tokens


def _token_mix(summary: dict) -> list[dict]:
    prompt = int(summary.get("prompt") or 0)
    cached = min(int(summary.get("cached") or 0), prompt)
    cache_write = min(int(summary.get("cache_write") or 0), max(prompt - cached, 0))
    output = int(summary.get("output") or 0)
    reasoning = min(int(summary.get("reasoning") or 0), output)
    parts = [
        ("uncached", "Uncached input", max(prompt - cached - cache_write, 0)),
        ("cached", "Cached input", cached),
        ("write", "Cache write", cache_write),
        ("visible", "Visible output", max(output - reasoning, 0)),
        ("reasoning", "Reasoning", reasoning),
    ]
    total = sum(v for _, _, v in parts) or 0
    return [
        {
            "id": pid,
            "name": name,
            "tokens": val,
            "share": round((val * 100) / total, 1) if total else 0,
        }
        for pid, name, val in parts
    ]


def _rate(part: int, whole: int) -> float | None:
    return round((part * 100) / whole, 1) if whole else None


def _cache_savings(models: list[dict]) -> tuple[int, bool]:
    """Approximate cost avoided by cache reads: cached tokens at (input - cached) base rates.

    Only models with a known cached-input rate count; tiered and long-context
    prices are ignored, so this is an estimate, labelled as such in the UI.
    """
    keys = [(m["provider"], m["name"]) for m in models if m["cached"] and m["provider"] != "openrouter"]
    if not keys:
        return 0, False
    rows = db.fetchall(
        "SELECT provider, model, input_micros_per_million, cached_input_micros_per_million, cached_input_known "
        "FROM model_prices WHERE provider IN ({}) ".format(",".join("?" * len({k[0] for k in keys}))),
        tuple(sorted({k[0] for k in keys})),
    )
    rates = {(r["provider"], r["model"]): r for r in rows}
    saved = 0
    known = False
    for model in models:
        rate = rates.get((model["provider"], model["name"]))
        if not rate or not rate["cached_input_known"] or not model["cached"]:
            continue
        diff = int(rate["input_micros_per_million"] or 0) - int(rate["cached_input_micros_per_million"] or 0)
        if diff > 0:
            saved += (model["cached"] * diff) // 1_000_000
            known = True
    return saved, known


_METRICS = ("tokens", "requests", "prompt", "completion", "output", "reasoning", "cached", "cache_write",
            "failed", "cancelled", "cost", "estimated_cost", "priced", "unpriced_events")
_EVENT_SUMS = """COALESCE(SUM(CASE WHEN e.total_tokens>0 THEN e.total_tokens
                                     ELSE e.prompt_tokens+e.output_tokens END), 0) AS tokens,
                   COALESCE(SUM(e.count_request), 0) AS requests,
                   COALESCE(SUM(e.prompt_tokens), 0) AS prompt,
                   COALESCE(SUM(e.completion_tokens), 0) AS completion,
                   COALESCE(SUM(e.output_tokens), 0) AS output,
                   COALESCE(SUM(e.reasoning_tokens), 0) AS reasoning,
                   COALESCE(SUM(e.cached_tokens), 0) AS cached,
                   COALESCE(SUM(e.cache_write_tokens), 0) AS cache_write,
                   COALESCE(SUM(CASE WHEN e.count_request=1 AND e.request_status='failed' THEN 1 ELSE 0 END), 0) AS failed,
                   COALESCE(SUM(CASE WHEN e.count_request=1 AND e.request_status='cancelled' THEN 1 ELSE 0 END), 0) AS cancelled,
                   COALESCE(SUM(COALESCE(e.reported_cost_micros, e.estimated_cost_micros, 0)), 0) AS cost,
                   COALESCE(SUM(e.estimated_cost_micros), 0) AS estimated_cost,
                   COALESCE(SUM(CASE WHEN e.count_request=1 AND
                                          (e.reported_cost_micros IS NOT NULL OR e.pricing_known=1)
                                     THEN 1 ELSE 0 END), 0) AS priced,
                   COALESCE(SUM(CASE WHEN (e.total_tokens>0 OR e.prompt_tokens+e.output_tokens>0)
                                           AND e.reported_cost_micros IS NULL AND e.pricing_known=0
                                     THEN 1 ELSE 0 END), 0) AS unpriced_events"""
_HEATMAP_MIN = timedelta(days=7)


def usage_analytics(
    user_id: int,
    *,
    range_id: str = "24h",
    provider: str = "",
    all_users: bool = False,
    subject_id: int | None = None,
    include_accounts: bool = False,
) -> dict:
    range_id = range_id if range_id in _RANGES else "24h"
    duration, bucket_seconds, bucket_label = _RANGES[range_id]
    # Stored timestamps have second precision; include events written in the current second.
    end_dt = datetime.now(timezone.utc) + timedelta(seconds=1)
    start_dt = end_dt - duration
    previous_start = start_dt - duration
    start = start_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    end = end_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    prev_start = previous_start.strftime("%Y-%m-%dT%H:%M:%SZ")
    where, args = _window_filters(user_id, start, end, provider, all_users, subject_id=subject_id)
    # A weekday x hour pattern needs a week of history even when the chosen
    # range is shorter, so the heatmap window is never under seven days.
    heat_start_dt = min(start_dt, end_dt - _HEATMAP_MIN)
    heat_where, heat_args = _window_filters(
        user_id, heat_start_dt.strftime("%Y-%m-%dT%H:%M:%SZ"), end, provider, all_users, subject_id=subject_id,
    )
    prev_model_tokens: dict[str, int] = {}
    # Five independent scans of usage_events. Each takes its own pool
    # connection, so the request waits for the slowest instead of their sum.
    with ThreadPoolExecutor(max_workers=5) as scans:
        # users is a LEFT JOIN: usage_events can outlive their user row, and
        # _event_summary counts those rows, so the series and tables must not
        # drop them (an INNER JOIN made the charts disagree with the summary).
        rows_job = scans.submit(
            db.fetchall,
            f"""SELECT (CAST(strftime('%s', e.created_at) AS INTEGER) / ?) * ? AS bucket,
                       e.user_id, u.email,
                       COALESCE(NULLIF(e.source_type, ''), s.type, 'unknown') AS provider,
                       COALESCE(NULLIF(e.model, ''), 'Unknown') AS model,
                       {_EVENT_SUMS}
                FROM usage_events e
                LEFT JOIN sources s ON s.id=e.source_id
                LEFT JOIN users u ON u.id=e.user_id
                WHERE {where}
                GROUP BY bucket, e.user_id, provider, model
                ORDER BY bucket""",
            (bucket_seconds, bucket_seconds, *args),
        )
        summary_job = scans.submit(_event_summary, user_id, start, end, provider, all_users, subject_id=subject_id)
        previous_job = scans.submit(
            _event_summary, user_id, prev_start, start, provider, all_users,
            subject_id=subject_id, model_tokens=prev_model_tokens,
        )
        # NULL api_key_id is traffic from Fuse itself (dashboard chat, agents, tasks).
        keys_job = scans.submit(
            db.fetchall,
            f"""SELECT e.api_key_id AS id,
                       MAX(k.name) AS name,
                       MAX(k.prefix) AS prefix,
                       COALESCE(SUM(CASE WHEN e.total_tokens>0 THEN e.total_tokens
                                         ELSE e.prompt_tokens+e.output_tokens END), 0) AS tokens,
                       COALESCE(SUM(e.count_request), 0) AS requests,
                       COALESCE(SUM(CASE WHEN e.count_request=1 AND e.request_status='failed' THEN 1 ELSE 0 END), 0) AS failed,
                       COALESCE(SUM(COALESCE(e.reported_cost_micros, e.estimated_cost_micros, 0)), 0) AS cost,
                       MAX(e.created_at) AS last_at
                FROM usage_events e
                LEFT JOIN api_keys k ON k.id=e.api_key_id
                LEFT JOIN sources s ON s.id=e.source_id
                WHERE {where}
                GROUP BY e.api_key_id
                ORDER BY tokens DESC
                LIMIT 10""",
            tuple(args),
        )
        heat_job = scans.submit(
            db.fetchall,
            f"""SELECT (CAST(strftime('%s', e.created_at) AS INTEGER) / 3600) * 3600 AS hour,
                       COALESCE(SUM(CASE WHEN e.total_tokens>0 THEN e.total_tokens
                                         ELSE e.prompt_tokens+e.output_tokens END), 0) AS tokens,
                       COALESCE(SUM(e.count_request), 0) AS requests
                FROM usage_events e LEFT JOIN sources s ON s.id=e.source_id
                WHERE {heat_where}
                GROUP BY hour""",
            tuple(heat_args),
        )
    rows = rows_job.result()
    summary = summary_job.result()
    previous = previous_job.result()
    model_totals: dict[str, dict] = {}
    provider_totals: dict[str, dict] = {}
    user_totals: dict[int, dict] = {}
    by_bucket: dict[int, list] = {}
    for row in rows:
        item = {key: row[key] for key in row.keys()}
        item.update({key: int(item[key] or 0) for key in _METRICS})
        by_bucket.setdefault(int(item["bucket"]), []).append(item)
        model_key = f"{item['provider']}\x1f{item['model']}"
        model_item = model_totals.setdefault(
            model_key,
            {
                "id": model_key,
                "name": item["model"],
                "provider": item["provider"],
                "provider_label": PROVIDER_LABELS.get(item["provider"], item["provider"]),
                **{metric: 0 for metric in _METRICS if metric != "estimated_cost"},
            },
        )
        provider_item = provider_totals.setdefault(
            item["provider"],
            {
                "id": item["provider"],
                "name": PROVIDER_LABELS.get(item["provider"], item["provider"]),
                "tokens": 0,
                "requests": 0,
                "cost": 0,
                "prompt": 0,
                "cached": 0,
                "failed": 0,
                "priced": 0,
                "unpriced_events": 0,
            },
        )
        user_item = user_totals.setdefault(
            int(item["user_id"]),
            {"id": int(item["user_id"]), "email": item["email"] or f"user {int(item['user_id'])}", "tokens": 0, "requests": 0, "cost": 0, "providers": {}},
        )
        for target in (model_item, provider_item, user_item):
            for metric in ("tokens", "requests", "cost"):
                target[metric] += item[metric]
        for metric in ("prompt", "completion", "output", "reasoning", "cached", "cache_write", "failed", "cancelled", "priced", "unpriced_events"):
            model_item[metric] += item[metric]
        for metric in ("prompt", "cached", "failed", "priced", "unpriced_events"):
            provider_item[metric] += item[metric]
        user_item["providers"][item["provider"]] = user_item["providers"].get(item["provider"], 0) + item["tokens"]

    ranked_models = sorted(model_totals.values(), key=lambda item: item["tokens"], reverse=True)
    top_model_keys = [item["id"] for item in ranked_models[:6]]
    table_models = ranked_models[:15]
    first_bucket = (int(start_dt.timestamp()) // bucket_seconds) * bucket_seconds
    last_bucket = (int(end_dt.timestamp()) // bucket_seconds) * bucket_seconds
    points = []
    model_sparks: dict[str, list[int]] = {item["id"]: [] for item in table_models}
    bucket = first_bucket
    while bucket <= last_bucket:
        bucket_rows = by_bucket.get(bucket, [])
        segments = {key: 0 for key in top_model_keys}
        cost_segments = {key: 0 for key in top_model_keys}
        request_segments = {key: 0 for key in top_model_keys}
        other = other_cost = other_requests = 0
        point = {
            "at": datetime.fromtimestamp(bucket, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "tokens": 0,
            "requests": 0,
            "failed": 0,
            "cancelled": 0,
            "cost": 0,
            "prompt": 0,
            "cached": 0,
            "segments": segments,
            "cost_segments": cost_segments,
            "request_segments": request_segments,
        }
        bucket_models: dict[str, int] = {}
        for item in bucket_rows:
            key = f"{item['provider']}\x1f{item['model']}"
            for metric in ("tokens", "requests", "failed", "cancelled", "cost", "prompt", "cached"):
                point[metric] += item[metric]
            bucket_models[key] = bucket_models.get(key, 0) + item["tokens"]
            if key in segments:
                segments[key] += item["tokens"]
                cost_segments[key] += item["cost"]
                request_segments[key] += item["requests"]
            else:
                other += item["tokens"]
                other_cost += item["cost"]
                other_requests += item["requests"]
        if other:
            segments["other"] = other
        if other_cost:
            cost_segments["other"] = other_cost
        if other_requests:
            request_segments["other"] = other_requests
        for key, spark in model_sparks.items():
            spark.append(bucket_models.get(key, 0))
        points.append(point)
        bucket += bucket_seconds

    providers = sorted(provider_totals.values(), key=lambda item: item["tokens"], reverse=True)
    users = sorted(user_totals.values(), key=lambda item: item["tokens"], reverse=True) if all_users else []
    account_options = (
        [{"id": int(row["id"]), "email": row["email"] or f"user {int(row['id'])}"} for row in db.fetchall("SELECT id, email FROM users ORDER BY email")]
        if include_accounts
        else []
    )
    prev_provider_tokens: dict[str, int] = {}
    for key, tokens in prev_model_tokens.items():
        pid = key.split("\x1f", 1)[0]
        prev_provider_tokens[pid] = prev_provider_tokens.get(pid, 0) + tokens
    for model in ranked_models:
        model["cost_known"] = model["priced"] >= model["requests"] and model["requests"] > 0 and not model["unpriced_events"]
        model["cost_tracked"] = model["provider"] != "openrouter"
        prev_tokens = prev_model_tokens.get(model["id"], 0)
        model["token_change"] = _change(model["tokens"], prev_tokens)
        model["is_new"] = bool(model["tokens"] and not prev_tokens)
        model["cache_hit"] = _hit_rate(model["cached"], model["prompt"])
        model["error_rate"] = _rate(model["failed"], model["requests"])
        model["per_million"] = (model["cost"] * 1_000_000 // model["tokens"]) if model["tokens"] and model["cost_known"] else None
        model["per_request"] = model["tokens"] // model["requests"] if model["requests"] else None
    for model in table_models:
        model["spark"] = model_sparks.get(model["id"], [])
    for row in providers:
        prev_tokens = prev_provider_tokens.get(row["id"], 0)
        row["token_change"] = _change(row["tokens"], prev_tokens)
        row["is_new"] = bool(row["tokens"] and not prev_tokens)
        row["cache_hit"] = _hit_rate(row["cached"], row["prompt"])
        row["error_rate"] = _rate(row["failed"], row["requests"])
        row["cost_known"] = row["priced"] >= row["requests"] and row["requests"] > 0 and not row["unpriced_events"]
    # provider_usage gains a row for every provider an event records, so it
    # lists the same providers as usage_events without scanning the events.
    available = db.fetchall(
        """SELECT DISTINCT provider FROM (
               SELECT type AS provider FROM sources
               UNION SELECT provider FROM provider_usage
           ) AS available_providers WHERE provider<>'' ORDER BY provider"""
    )
    key_rows = keys_job.result()
    keys = [
        {
            "id": int(row["id"] or 0),
            "kind": "chat" if row["id"] is None else "key",
            "name": "Fuse chat & agents" if row["id"] is None else (row["name"] or "Deleted key"),
            "prefix": row["prefix"] or "",
            "tokens": int(row["tokens"] or 0),
            "requests": int(row["requests"] or 0),
            "failed": int(row["failed"] or 0),
            "cost": int(row["cost"] or 0),
            "last_at": row["last_at"] or "",
        }
        for row in key_rows
    ]
    recent_rows = db.fetchall(
        f"""SELECT e.created_at, e.model, e.api_key_id,
                   COALESCE(NULLIF(e.source_type, ''), s.type, 'unknown') AS provider,
                   k.name AS key_name,
                   e.prompt_tokens, e.cached_tokens, e.output_tokens, e.reasoning_tokens, e.total_tokens,
                   COALESCE(e.reported_cost_micros, e.estimated_cost_micros, 0) AS cost,
                   e.pricing_known, e.reported_cost_micros,
                   e.request_status
            FROM usage_events e
            LEFT JOIN sources s ON s.id=e.source_id
            LEFT JOIN api_keys k ON k.id=e.api_key_id
            WHERE {where}
            ORDER BY e.created_at DESC
            LIMIT 25""",
        tuple(args),
    )
    recent = [
        {
            "at": row["created_at"] or "",
            "model": row["model"] or "Unknown",
            "provider": row["provider"] or "unknown",
            "provider_label": PROVIDER_LABELS.get(row["provider"], row["provider"] or "Unknown"),
            "via": "Fuse chat" if row["api_key_id"] is None else (row["key_name"] or "Deleted key"),
            "via_kind": "chat" if row["api_key_id"] is None else "key",
            "prompt": int(row["prompt_tokens"] or 0),
            "cached": int(row["cached_tokens"] or 0),
            "output": int(row["output_tokens"] or 0),
            "reasoning": int(row["reasoning_tokens"] or 0),
            "tokens": int(row["total_tokens"] or 0),
            "cost": int(row["cost"] or 0),
            "cost_known": bool(row["pricing_known"]) or row["reported_cost_micros"] is not None,
            "status": row["request_status"] or "success",
        }
        for row in recent_rows
    ]
    activity = [
        [int(row["hour"]), int(row["tokens"] or 0), int(row["requests"] or 0)]
        for row in heat_job.result()
    ]
    cache_hit = _hit_rate(summary["cached"], summary["prompt"])
    prev_hit = _hit_rate(previous["cached"], previous["prompt"])
    # Rates only describe priced traffic: dividing by unpriced tokens or
    # requests (llama, Ollama, OpenRouter on the user's own key) understates them.
    blended = _blended(summary["cost"], summary["priced_tokens"])
    prev_blended = _blended(previous["cost"], previous["priced_tokens"])
    trackable = summary["requests"] - summary["untracked"]
    success = _rate(summary["ok_requests"], summary["requests"])
    prev_success = _rate(previous["ok_requests"], previous["requests"])
    per_request = summary["tokens"] / summary["requests"] if summary["requests"] else None
    prev_per_request = previous["tokens"] / previous["requests"] if previous["requests"] else None
    saved, saved_known = _cache_savings(ranked_models)
    peak = max(points, key=lambda point: point["tokens"], default=None)
    return {
        "range": {"id": range_id, "from": start, "to": end, "bucket": bucket_label, "bucket_seconds": bucket_seconds},
        "scope": "all" if all_users else "account",
        "subject_id": subject_id,
        "provider": provider,
        "provider_options": [
            {"id": row["provider"], "name": PROVIDER_LABELS.get(row["provider"], row["provider"])}
            for row in available
        ],
        "summary": {
            **summary,
            "cost_label": format_usd_micros(summary["cost"]),
            "estimated_cost_label": format_usd_micros(summary["estimated_cost"]),
            # OpenRouter is billed on the caller's own key and deliberately not
            # costed, so it is neither priced nor missing a rate.
            "pricing_coverage": round((summary["priced"] * 100) / trackable) if trackable > 0 else 0,
            "token_change": _change(summary["tokens"], previous["tokens"]),
            "request_change": _change(summary["requests"], previous["requests"]),
            "cost_change": _change(summary["cost"], previous["cost"]),
            "cache_hit": cache_hit,
            "cache_hit_change": round(cache_hit - prev_hit, 1) if cache_hit is not None and prev_hit is not None else None,
            "blended": blended,
            "blended_change": _change(blended, prev_blended) if blended is not None else None,
            "success_rate": success,
            "success_change": round(success - prev_success, 1) if success is not None and prev_success is not None else None,
            "per_request": per_request,
            "per_request_change": _change(per_request, prev_per_request) if per_request is not None else None,
            "cost_per_request": summary["cost"] / summary["priced"] if summary["priced"] else None,
            "cache_saved": saved,
            "cache_saved_known": saved_known,
            "peak_at": peak["at"] if peak and peak["tokens"] else None,
            "peak_tokens": peak["tokens"] if peak else 0,
            "models_used": len(ranked_models),
            "empty": not (summary["requests"] or summary["tokens"]),
        },
        "previous": {key: previous[key] for key in ("tokens", "requests", "cost")},
        "mix": _token_mix(summary),
        "series": points,
        "series_models": [
            {
                "id": key,
                "name": model_totals[key]["name"],
                "provider": model_totals[key]["provider"],
                "provider_label": model_totals[key]["provider_label"],
            }
            for key in top_model_keys
        ],
        "models": table_models,
        "providers": providers,
        "keys": keys,
        "recent": recent,
        "activity": activity,
        "activity_days": max(duration, _HEATMAP_MIN).days,
        "users": users[:20],
        "account_options": account_options,
    }


def _rate_label(micros) -> str:
    value = int(micros or 0)
    return f"${value / 1_000_000:g}" if value else "$0"


_ORIGIN_LABELS = {"openrouter": "OpenRouter", "default": "Built-in", "auto": "Auto"}


def pricing_views() -> list[dict]:
    rows = db.fetchall("SELECT * FROM model_prices ORDER BY provider, model")
    out = []
    for row in rows:
        item = dict(row)
        item["input_usd"] = _rate_label(row["input_micros_per_million"])
        item["output_usd"] = _rate_label(row["output_micros_per_million"])
        item["cached_usd"] = _rate_label(row["cached_input_micros_per_million"]) if row["cached_input_known"] else "—"
        item["write_usd"] = _rate_label(row["cache_write_micros_per_million"]) if row["cache_write_known"] else "—"
        item["reasoning_usd"] = _rate_label(row["reasoning_micros_per_million"]) if row["reasoning_known"] else "—"
        tiers = []
        try:
            tiers = [
                t
                for t in json.loads(row["tiers_json"] or "[]")
                if isinstance(t, dict) and int(t.get("min_prompt_tokens") or 0) > 0
            ]
        except (TypeError, ValueError):
            tiers = []
        tiers.sort(key=lambda t: int(t.get("min_prompt_tokens") or 0))
        if tiers:
            parts = []
            for t in tiers:
                thr = f">={'{:,}'.format(int(t['min_prompt_tokens']))}"
                parts.append(
                    f"{thr} · in {_rate_label(t.get('input_rate'))} / out {_rate_label(t.get('output_rate'))}"
                )
            item["long_label"] = " / ".join(parts)
        elif row["long_context_threshold"]:
            parts = [
                f">{'{:,}'.format(int(row['long_context_threshold']))}",
                _rate_label(row["long_input_micros_per_million"] or row["input_micros_per_million"]),
                _rate_label(row["long_output_micros_per_million"] or row["output_micros_per_million"]),
            ]
            item["long_label"] = " / ".join(parts)
        else:
            item["long_label"] = "—"
        item["origin_label"] = _ORIGIN_LABELS.get(row["origin"], "Manual")
        item["synced_label"] = format_when(row["synced_at"]) if row["synced_at"] else ""
        item["provider_label"] = PROVIDER_LABELS.get(row["provider"], row["provider"])
        out.append(item)
    return out


def fuse_view(row, *, week: str | None = None) -> dict:
    """Lifetime Fuse traffic through a pool credential, plus this ISO week."""
    week = week or db.iso_week_start()
    prompt = int(row["prompt_tokens"] or 0)
    completion = int(row["completion_tokens"] or 0)
    output = int(row["output_tokens"] or 0) or max(completion, int(row["reasoning_tokens"] or 0))
    reasoning = min(int(row["reasoning_tokens"] or 0), output)
    cached = min(int(row["cached_tokens"] or 0), prompt)
    total = int(row["total_tokens"] or 0) or (prompt + output)
    requests = int(row["requests"] or 0)
    this_week = str(row["week_start"] or "") == week
    week_tokens = int(row["week_tokens"] or 0) if this_week else 0
    week_requests = int(row["week_requests"] or 0) if this_week else 0
    has = bool(requests or total)
    coverage = db.fetchone(
        """SELECT
             COALESCE(SUM(estimated_cost_micros), 0) AS api_equiv,
             COALESCE(SUM(CASE WHEN total_tokens > 0 AND pricing_known=0
                               THEN 1 ELSE 0 END), 0) AS unpriced,
             COALESCE(SUM(CASE WHEN total_tokens > 0 AND pricing_known=1
                               THEN 1 ELSE 0 END), 0) AS priced
           FROM usage_events WHERE credential_id=?""",
        (row["id"],),
    )
    api_equiv = int(coverage["api_equiv"] or 0) if coverage else 0
    unpriced = int(coverage["unpriced"] or 0) if coverage else 0
    priced = int(coverage["priced"] or 0) if coverage else 0
    return {
        "prompt_tokens": prompt,
        "uncached_tokens": max(prompt - cached, 0),
        "cached_tokens": cached,
        "completion_tokens": completion,
        "output_tokens": output,
        "visible_tokens": max(output - reasoning, 0),
        "reasoning_tokens": reasoning,
        "total_tokens": total,
        "display_tokens": total if total else week_tokens,
        "requests": requests,
        "cost_micros": api_equiv,
        "cost_label": format_usd_micros(api_equiv) if has and (priced or not unpriced) else "",
        "cost_partial": bool(unpriced and priced),
        "cost_unavailable": bool(unpriced and not priced),
        "has": has,
        "week_only": bool(week_tokens and not has),
        "week_tokens": week_tokens,
        "week_requests": week_requests,
        "since": format_when(row["created_at"]),
        "fill": 0,
    }


def hold_view(held_until: str | None, guard: dict | None, last_percent) -> dict | None:
    if not held_until:
        return None
    guard = guard if isinstance(guard, dict) else {}
    return {
        "until": format_when(held_until),
        "from_label": format_pct(guard.get("held_from")),
        "to_label": format_pct(guard.get("held_to")),
        "last_label": format_pct(last_percent),
    }


def billing_view(billing: dict | None, last_percent=None, percent=None) -> dict:
    billing = billing if isinstance(billing, dict) else {}
    pct_v = _as_float(percent)
    if pct_v is None:
        pct_v = _as_float(billing.get("credit_usage_percent"))
    if pct_v is None and not billing.get("fetched_at"):
        pct_v = _as_float(last_percent)
    products = []
    for p in billing.get("products") or []:
        if not isinstance(p, dict):
            continue
        raw_name = p.get("product") or p.get("name") or ""
        label = PRODUCT_NAMES.get(raw_name, raw_name) or "Other"
        pp_v = _as_float(p.get("usage_percent"))
        if pp_v is None:
            continue
        products.append({"name": label, "percent": pp_v, "percent_label": format_pct(pp_v)})
    money = []
    prepaid = int(billing.get("prepaid_cents") or 0)
    od_used = int(billing.get("on_demand_used_cents") or 0)
    od_cap = int(billing.get("on_demand_cap_cents") or 0)
    used = int(billing.get("used_cents") or 0)
    if prepaid:
        money.append({"label": "Prepaid", "text": format_usd_cents(prepaid)})
    if od_used or od_cap:
        text = format_usd_cents(od_used)
        if od_cap:
            text += " / " + format_usd_cents(od_cap)
        money.append({"label": "On-demand", "text": text})
    if used and billing.get("credit_usage_percent") is None:
        money.append({"label": "Used", "text": format_usd_cents(used)})
    available = pct_v is not None
    meter = max(0, min(100, int(round(pct_v)))) if available else 0
    fetched = bool(billing.get("fetched_at"))
    return {
        "available": available,
        "percent": pct_v,
        "percent_label": format_pct(pct_v) or "—",
        "meter": meter,
        "period": period_label(billing.get("period_type")),
        "period_start": format_when(billing.get("period_start")),
        "period_end": format_when(billing.get("period_end")),
        "products": products,
        "money": money,
        "plan_label": plan_type_label(billing.get("plan_type")),
        "unified": bool(billing.get("unified")),
        "fetched": fetched,
        "fetched_at": format_when(billing.get("fetched_at")),
        "checked_at": format_when(billing.get("checked_at")),
        "error": billing.get("last_error") or "",
    }


def _empty_pool_meter(count_label: str) -> dict:
    return {
        "available": False,
        "percent": None,
        "percent_label": "—",
        "meter": 0,
        "n": 0,
        "busy": 0,
        "count_label": count_label,
    }


def pool_meter(percents: list[float], *, plans: list[str] | None = None) -> dict:
    """Unweighted mean of routing percents. Empty list is no reading, not 0%."""
    if not percents:
        return _empty_pool_meter("No plans")
    mean = sum(percents) / len(percents)
    n = len(percents)
    busy = sum(1 for p in percents if p >= USAGE_BUSY)
    labels = [plan_type_label(p) for p in (plans or [])[:n]]
    labels = [label for label in labels if label]
    counts = Counter(labels)
    if labels and sum(counts.values()) == n:
        order = list(dict.fromkeys(labels))
        count_label = " · ".join(f"{counts[name]} {name}" for name in order)
    else:
        count_label = f"{n} plan" if n == 1 else f"{n} plans"
    if busy:
        count_label += f" · {busy} at cap"
    return {
        "available": True,
        "percent": mean,
        "percent_label": format_pct(mean) or "0%",
        "meter": max(0, min(100, int(round(mean)))),
        "n": n,
        "busy": busy,
        "count_label": count_label,
    }


def _finish_pool_meter(percents: list[float], plans: list[str], unread: int) -> dict:
    if percents:
        meter = pool_meter(percents, plans=plans)
        # The mean covers measured plans only; say how many it left out, or
        # "2 Go" reads as the whole pool when two Free plans had no reading.
        if unread:
            meter["count_label"] += f" · {unread} no reading"
        return meter
    if unread:
        return _empty_pool_meter("No reading yet")
    return _empty_pool_meter("No plans")


def _held_from(row, is_held, owner_id: int | None) -> bool:
    """An off-site hold withholds an account from everyone but its owner."""
    if owner_id is not None and row["donated_by"] == owner_id:
        return False
    return is_held(row)


def grok_pool_meter(rows, *, owner_id: int | None = None) -> dict:
    percents: list[float] = []
    unread = 0
    for row in rows:
        if row["disabled"]:
            continue
        extra = accounts.cred_extra(row)
        if _held_from(row, grok_mod.is_held, owner_id) or not grok_mod.credential_is_authed(row, extra):
            continue
        billing = extra.get("billing") if isinstance(extra.get("billing"), dict) else {}
        if not grok_mod.billing_has_subscription(billing):
            continue
        pct = grok_usage_percent(row, extra)
        if pct is None:
            unread += 1
            continue
        percents.append(pct)
    return _finish_pool_meter(percents, [], unread)


def devin_pool_meter(rows) -> dict:
    from .sources import devin as devin_mod

    percents: list[float] = []
    plans: list[str] = []
    unread = 0
    for row in rows:
        if row["disabled"]:
            continue
        extra = accounts.cred_extra(row)
        if not devin_mod.credential_is_authed(row, extra):
            continue
        billing = extra.get("billing") if isinstance(extra.get("billing"), dict) else {}
        if not devin_mod.billing_has_subscription(billing):
            unread += 1
            continue
        pct = _as_float(billing.get("credit_usage_percent"))
        if pct is None:
            unread += 1
            continue
        percents.append(pct)
        plans.append(str(billing.get("plan_type") or "Pro"))
    return _finish_pool_meter(percents, plans, unread)


def kimi_pool_meter(rows, *, owner_id: int | None = None) -> dict:
    percents: list[float] = []
    plans: list[str] = []
    unread = 0
    for row in rows:
        if row["disabled"]:
            continue
        extra = accounts.cred_extra(row)
        if _held_from(row, kimi_mod.is_held, owner_id) or not kimi_mod.credential_is_authed(row, extra):
            continue
        billing = extra.get("billing") if isinstance(extra.get("billing"), dict) else {}
        if not kimi_mod.billing_has_subscription(billing):
            unread += 1
            continue
        pct = kimi_usage_percent(extra)
        if pct is None:
            unread += 1
            continue
        percents.append(pct)
        plans.append(str(billing.get("plan_type") or extra.get("plan_type") or ""))
    return _finish_pool_meter(percents, plans, unread)


def claude_pool_meter(rows, *, owner_id: int | None = None) -> dict:
    percents: list[float] = []
    plans: list[str] = []
    unread = 0
    for row in rows:
        if row["disabled"]:
            continue
        extra = accounts.cred_extra(row)
        if _held_from(row, claude_mod.is_held, owner_id) or not claude_mod.credential_is_authed(row, extra):
            continue
        billing = extra.get("billing") if isinstance(extra.get("billing"), dict) else {}
        if not claude_mod.billing_has_subscription(billing):
            unread += 1
            continue
        pct = _as_float(billing.get("credit_usage_percent"))
        if pct is None:
            for win in billing.get("windows") or []:
                if isinstance(win, dict):
                    pct = _as_float(win.get("percent"))
                    if pct is not None:
                        break
        if pct is None:
            unread += 1
            continue
        percents.append(pct)
        # Only measured accounts: pool_meter pairs plans with readings by index.
        plans.append(str(billing.get("plan_type") or extra.get("subscription_type") or "Claude"))
    return _finish_pool_meter(percents, plans, unread)


def cursor_pool_meter(rows, *, owner_id: int | None = None) -> dict:
    percents: list[float] = []
    plans: list[str] = []
    unread = 0
    for row in rows:
        if row["disabled"]:
            continue
        extra = accounts.cred_extra(row)
        if _held_from(row, cursor_mod.is_held, owner_id) or not cursor_mod.credential_is_authed(row, extra):
            continue
        billing = extra.get("billing") if isinstance(extra.get("billing"), dict) else {}
        pct = kimi_usage_percent(extra) if cursor_mod.billing_has_subscription(billing) else None
        if pct is None:
            unread += 1
            continue
        percents.append(pct)
        plans.append(str(billing.get("plan_type") or ""))
    return _finish_pool_meter(percents, plans, unread)


def nanogpt_pool_meter(rows, *, owner_id: int | None = None) -> dict:
    percents: list[float] = []
    plans: list[str] = []
    unread = 0
    for row in rows:
        if row["disabled"]:
            continue
        extra = accounts.cred_extra(row)
        if _held_from(row, nanogpt_mod.is_held, owner_id) or not nanogpt_mod.credential_is_authed(row, extra):
            continue
        billing = extra.get("billing") if isinstance(extra.get("billing"), dict) else {}
        if not nanogpt_mod.billing_has_subscription(billing):
            continue
        pct = kimi_usage_percent(extra)
        if pct is None:
            unread += 1
            continue
        percents.append(pct)
        plans.append("Subscription")
    return _finish_pool_meter(percents, plans, unread)


def mimo_pool_meter(rows, *, owner_id: int | None = None) -> dict:
    percents: list[float] = []
    plans: list[str] = []
    unread = 0
    for row in rows:
        if row["disabled"]:
            continue
        extra = accounts.cred_extra(row)
        if _held_from(row, mimo_mod.is_held, owner_id) or not mimo_mod.credential_is_authed(row, extra):
            continue
        billing = extra.get("billing") if isinstance(extra.get("billing"), dict) else {}
        if not mimo_mod.billing_has_subscription(billing):
            unread += 1
            continue
        pct = mimo_usage_percent(extra)
        if pct is None:
            unread += 1
            continue
        percents.append(pct)
        plans.append(str(billing.get("plan_type") or "Token Plan"))
    return _finish_pool_meter(percents, plans, unread)


def zai_pool_meter(rows, *, owner_id: int | None = None) -> dict:
    percents: list[float] = []
    plans: list[str] = []
    unread = 0
    for row in rows:
        if row["disabled"]:
            continue
        extra = accounts.cred_extra(row)
        if _held_from(row, zai_mod.is_held, owner_id) or not zai_mod.credential_is_authed(row, extra):
            continue
        billing = extra.get("billing") if isinstance(extra.get("billing"), dict) else {}
        if not zai_mod.billing_has_subscription(billing):
            continue
        pct = zai_usage_percent(extra)
        if pct is None:
            unread += 1
            continue
        percents.append(pct)
        plans.append(str(billing.get("plan_type") or ""))
    return _finish_pool_meter(percents, plans, unread)


def chatgpt_pool_meters(rows) -> tuple[dict, dict]:
    paid_pcts: list[float] = []
    paid_plans: list[str] = []
    free_pcts: list[float] = []
    free_plans: list[str] = []
    paid_unread = 0
    free_unread = 0
    for row in rows:
        if row["disabled"]:
            continue
        extra = accounts.cred_extra(row)
        if not chatgpt_mod.credential_is_authed(row, extra):
            continue
        plan = chatgpt_plan_type(extra)
        pct = chatgpt_usage_percent(extra)
        free = plan in CHATGPT_FREE_PLANS
        if pct is None:
            if free:
                free_unread += 1
            else:
                paid_unread += 1
            continue
        if free:
            free_pcts.append(pct)
            free_plans.append(plan)
        else:
            paid_pcts.append(pct)
            paid_plans.append(plan)
    return (
        _finish_pool_meter(paid_pcts, paid_plans, paid_unread),
        _finish_pool_meter(free_pcts, free_plans, free_unread),
    )
