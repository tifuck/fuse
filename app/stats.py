from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timedelta, timezone

from . import accounts, db
from .sources import PROVIDER_LABELS
from .sources import chatgpt as chatgpt_mod
from .sources import grok as grok_mod
from .sources import kimi as kimi_mod
from .sources import mimo as mimo_mod
from .sources import claude as claude_mod
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


def _window_filters(user_id: int, start: str, end: str, provider: str, all_users: bool) -> tuple[str, list]:
    clauses = ["e.created_at>=?", "e.created_at<?"]
    args: list = [start, end]
    if not all_users:
        clauses.append("e.user_id=?")
        args.append(user_id)
    if provider:
        clauses.append("COALESCE(NULLIF(e.source_type, ''), s.type, 'unknown')=?")
        args.append(provider)
    return " AND ".join(clauses), args


def _event_summary(user_id: int, start: str, end: str, provider: str, all_users: bool) -> dict:
    where, args = _window_filters(user_id, start, end, provider, all_users)
    row = db.fetchone(
        f"""SELECT COALESCE(SUM(CASE WHEN e.total_tokens>0 THEN e.total_tokens
                                      ELSE e.prompt_tokens+e.output_tokens END), 0) AS tokens,
                    COALESCE(SUM(e.count_request), 0) AS requests,
                    COALESCE(SUM(e.prompt_tokens), 0) AS prompt,
                    COALESCE(SUM(e.completion_tokens), 0) AS completion,
                    COALESCE(SUM(e.output_tokens), 0) AS output,
                    COALESCE(SUM(e.reasoning_tokens), 0) AS reasoning,
                    COALESCE(SUM(e.cached_tokens), 0) AS cached,
                    COALESCE(SUM(CASE WHEN e.count_request=1 AND e.request_status='success' THEN 1 ELSE 0 END), 0) AS ok_requests,
                    COALESCE(SUM(COALESCE(e.reported_cost_micros, e.estimated_cost_micros, 0)), 0) AS cost,
                    COALESCE(SUM(e.estimated_cost_micros), 0) AS estimated_cost,
                    COALESCE(SUM(CASE WHEN e.count_request=1 AND
                                           (e.reported_cost_micros IS NOT NULL OR e.pricing_known=1)
                                      THEN 1 ELSE 0 END), 0) AS priced
             FROM usage_events e LEFT JOIN sources s ON s.id=e.source_id
             WHERE {where}""",
        tuple(args),
    )
    return {key: int(row[key] or 0) for key in row.keys()}


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
    output = int(summary.get("output") or 0)
    reasoning = min(int(summary.get("reasoning") or 0), output)
    parts = [
        ("uncached", "Uncached input", max(prompt - cached, 0)),
        ("cached", "Cached input", cached),
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


def usage_analytics(
    user_id: int,
    *,
    range_id: str = "24h",
    provider: str = "",
    all_users: bool = False,
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
    where, args = _window_filters(user_id, start, end, provider, all_users)
    rows = db.fetchall(
        f"""SELECT (CAST(strftime('%s', e.created_at) AS INTEGER) / ?) * ? AS bucket,
                   e.user_id, u.email,
                   COALESCE(NULLIF(e.source_type, ''), s.type, 'unknown') AS provider,
                   COALESCE(NULLIF(e.model, ''), 'Unknown') AS model,
                   COALESCE(SUM(CASE WHEN e.total_tokens>0 THEN e.total_tokens
                                     ELSE e.prompt_tokens+e.output_tokens END), 0) AS tokens,
                    COALESCE(SUM(e.count_request), 0) AS requests,
                   COALESCE(SUM(e.prompt_tokens), 0) AS prompt,
                   COALESCE(SUM(e.completion_tokens), 0) AS completion,
                   COALESCE(SUM(e.output_tokens), 0) AS output,
                   COALESCE(SUM(e.reasoning_tokens), 0) AS reasoning,
                   COALESCE(SUM(e.cached_tokens), 0) AS cached,
                   COALESCE(SUM(COALESCE(e.reported_cost_micros, e.estimated_cost_micros, 0)), 0) AS cost,
                   COALESCE(SUM(e.estimated_cost_micros), 0) AS estimated_cost,
                    COALESCE(SUM(CASE WHEN e.count_request=1 AND
                                           (e.reported_cost_micros IS NOT NULL OR e.pricing_known=1)
                                      THEN 1 ELSE 0 END), 0) AS priced
            FROM usage_events e
            LEFT JOIN sources s ON s.id=e.source_id
            JOIN users u ON u.id=e.user_id
            WHERE {where}
            GROUP BY bucket, e.user_id, provider, model
            ORDER BY bucket""",
        (bucket_seconds, bucket_seconds, *args),
    )

    summary = _event_summary(user_id, start, end, provider, all_users)
    previous = _event_summary(user_id, prev_start, start, provider, all_users)
    prev_where, prev_args = _window_filters(user_id, prev_start, start, provider, all_users)
    prev_model_rows = db.fetchall(
        f"""SELECT COALESCE(NULLIF(e.source_type, ''), s.type, 'unknown') AS provider,
                   COALESCE(NULLIF(e.model, ''), 'Unknown') AS model,
                   COALESCE(SUM(CASE WHEN e.total_tokens>0 THEN e.total_tokens
                                     ELSE e.prompt_tokens+e.output_tokens END), 0) AS tokens
            FROM usage_events e LEFT JOIN sources s ON s.id=e.source_id
            WHERE {prev_where}
            GROUP BY provider, model""",
        tuple(prev_args),
    )
    prev_model_tokens = {
        f"{row['provider']}\x1f{row['model']}": int(row["tokens"] or 0) for row in prev_model_rows
    }
    model_totals: dict[str, dict] = {}
    provider_totals: dict[str, dict] = {}
    user_totals: dict[int, dict] = {}
    by_bucket: dict[int, list] = {}
    for row in rows:
        item = {key: row[key] for key in row.keys()}
        item.update(
            {
                key: int(item[key] or 0)
                for key in (
                    "tokens",
                    "requests",
                    "prompt",
                    "completion",
                    "output",
                    "reasoning",
                    "cached",
                    "cost",
                    "estimated_cost",
                    "priced",
                )
            }
        )
        by_bucket.setdefault(int(item["bucket"]), []).append(item)
        model_key = f"{item['provider']}\x1f{item['model']}"
        model_item = model_totals.setdefault(
            model_key,
            {
                "id": model_key,
                "name": item["model"],
                "provider": item["provider"],
                "tokens": 0,
                "requests": 0,
                "cost": 0,
                "prompt": 0,
                "completion": 0,
                "output": 0,
                "reasoning": 0,
                "cached": 0,
                "priced": 0,
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
            },
        )
        user_item = user_totals.setdefault(
            int(item["user_id"]),
            {"id": int(item["user_id"]), "email": item["email"], "tokens": 0, "requests": 0, "cost": 0, "providers": {}},
        )
        for target in (model_item, provider_item, user_item):
            for metric in ("tokens", "requests", "cost"):
                target[metric] += item[metric]
        model_item["priced"] += item["priced"]
        for metric in ("prompt", "completion", "output", "reasoning", "cached"):
            model_item[metric] += item[metric]
        user_item["providers"][item["provider"]] = user_item["providers"].get(item["provider"], 0) + item["tokens"]

    top_model_keys = [key for key, _ in sorted(model_totals.items(), key=lambda pair: pair[1]["tokens"], reverse=True)[:6]]
    first_bucket = (int(start_dt.timestamp()) // bucket_seconds) * bucket_seconds
    last_bucket = (int(end_dt.timestamp()) // bucket_seconds) * bucket_seconds
    points = []
    bucket = first_bucket
    while bucket <= last_bucket:
        bucket_rows = by_bucket.get(bucket, [])
        segments = {key: 0 for key in top_model_keys}
        cost_segments = {key: 0 for key in top_model_keys}
        other = 0
        other_cost = 0
        point = {
            "at": datetime.fromtimestamp(bucket, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "tokens": 0,
            "requests": 0,
            "cost": 0,
            "prompt": 0,
            "cached": 0,
            "segments": segments,
            "cost_segments": cost_segments,
        }
        for item in bucket_rows:
            key = f"{item['provider']}\x1f{item['model']}"
            point["tokens"] += item["tokens"]
            point["requests"] += item["requests"]
            point["cost"] += item["cost"]
            point["prompt"] += item["prompt"]
            point["cached"] += item["cached"]
            if key in segments:
                segments[key] += item["tokens"]
                cost_segments[key] += item["cost"]
            else:
                other += item["tokens"]
                other_cost += item["cost"]
        if other:
            segments["other"] = other
        if other_cost:
            cost_segments["other"] = other_cost
        points.append(point)
        bucket += bucket_seconds

    models = sorted(model_totals.values(), key=lambda item: item["tokens"], reverse=True)
    providers = sorted(provider_totals.values(), key=lambda item: item["tokens"], reverse=True)
    users = sorted(user_totals.values(), key=lambda item: item["tokens"], reverse=True) if all_users else []
    prev_provider_tokens: dict[str, int] = {}
    for key, tokens in prev_model_tokens.items():
        pid = key.split("\x1f", 1)[0]
        prev_provider_tokens[pid] = prev_provider_tokens.get(pid, 0) + tokens
    for model in models:
        model["cost_known"] = model["priced"] >= model["requests"] and model["requests"] > 0
        prev_tokens = prev_model_tokens.get(model["id"], 0)
        model["token_change"] = _change(model["tokens"], prev_tokens)
        model["is_new"] = bool(model["tokens"] and not prev_tokens)
    for row in providers:
        prev_tokens = prev_provider_tokens.get(row["id"], 0)
        row["token_change"] = _change(row["tokens"], prev_tokens)
        row["is_new"] = bool(row["tokens"] and not prev_tokens)
    available = db.fetchall(
        """SELECT DISTINCT provider FROM (
               SELECT type AS provider FROM sources
               UNION SELECT source_type AS provider FROM usage_events WHERE source_type<>''
           ) AS available_providers WHERE provider<>'' ORDER BY provider"""
    )
    key_rows = db.fetchall(
        f"""SELECT e.api_key_id AS id,
                   COALESCE(k.name, 'Deleted key') AS name,
                   COALESCE(k.prefix, '') AS prefix,
                   COALESCE(SUM(CASE WHEN e.total_tokens>0 THEN e.total_tokens
                                     ELSE e.prompt_tokens+e.output_tokens END), 0) AS tokens,
                    COALESCE(SUM(e.count_request), 0) AS requests,
                   COALESCE(SUM(COALESCE(e.reported_cost_micros, e.estimated_cost_micros, 0)), 0) AS cost
            FROM usage_events e
            LEFT JOIN api_keys k ON k.id=e.api_key_id
            LEFT JOIN sources s ON s.id=e.source_id
            WHERE {where} AND e.api_key_id IS NOT NULL
            GROUP BY e.api_key_id
            ORDER BY tokens DESC
            LIMIT 8""",
        tuple(args),
    )
    keys = [
        {
            "id": int(row["id"] or 0),
            "name": row["name"] or "Key",
            "prefix": row["prefix"] or "",
            "tokens": int(row["tokens"] or 0),
            "requests": int(row["requests"] or 0),
            "cost": int(row["cost"] or 0),
        }
        for row in key_rows
    ]
    recent_rows = db.fetchall(
        f"""SELECT e.created_at, e.model,
                   COALESCE(NULLIF(e.source_type, ''), s.type, 'unknown') AS provider,
                   e.prompt_tokens, e.output_tokens, e.total_tokens,
                   COALESCE(e.reported_cost_micros, e.estimated_cost_micros, 0) AS cost,
                   e.request_status
            FROM usage_events e LEFT JOIN sources s ON s.id=e.source_id
            WHERE {where}
            ORDER BY e.created_at DESC
            LIMIT 20""",
        tuple(args),
    )
    recent = [
        {
            "at": row["created_at"] or "",
            "model": row["model"] or "Unknown",
            "provider": row["provider"] or "unknown",
            "provider_label": PROVIDER_LABELS.get(row["provider"], row["provider"] or "Unknown"),
            "prompt": int(row["prompt_tokens"] or 0),
            "output": int(row["output_tokens"] or 0),
            "tokens": int(row["total_tokens"] or 0),
            "cost": int(row["cost"] or 0),
            "status": row["request_status"] or "success",
        }
        for row in recent_rows
    ]
    cache_hit = _hit_rate(summary["cached"], summary["prompt"])
    prev_hit = _hit_rate(previous["cached"], previous["prompt"])
    blended = _blended(summary["cost"], summary["tokens"])
    prev_blended = _blended(previous["cost"], previous["tokens"])
    return {
        "range": {"id": range_id, "from": start, "to": end, "bucket": bucket_label},
        "scope": "all" if all_users else "account",
        "provider": provider,
        "provider_options": [
            {"id": row["provider"], "name": PROVIDER_LABELS.get(row["provider"], row["provider"])}
            for row in available
        ],
        "summary": {
            **summary,
            "cost_label": format_usd_micros(summary["cost"]),
            "estimated_cost_label": format_usd_micros(summary["estimated_cost"]),
            "pricing_coverage": round((summary["priced"] * 100) / summary["requests"]) if summary["requests"] else 0,
            "token_change": _change(summary["tokens"], previous["tokens"]),
            "request_change": _change(summary["requests"], previous["requests"]),
            "cost_change": _change(summary["cost"], previous["cost"]),
            "cache_hit": cache_hit,
            "cache_hit_change": _change(cache_hit, prev_hit) if cache_hit is not None else None,
            "blended": blended,
            "blended_change": _change(blended, prev_blended) if blended is not None else None,
            "empty": not (summary["requests"] or summary["tokens"]),
        },
        "mix": _token_mix(summary),
        "series": points,
        "series_models": [
            {"id": key, "name": model_totals[key]["name"], "provider": model_totals[key]["provider"]}
            for key in top_model_keys
        ],
        "models": models[:12],
        "providers": providers,
        "keys": keys,
        "recent": recent,
        "users": users[:20],
    }


def _rate_label(micros) -> str:
    value = int(micros or 0)
    return f"${value / 1_000_000:g}" if value else "$0"


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
        item["origin_label"] = "OpenRouter" if row["origin"] == "openrouter" else "Manual"
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
                               AND reported_cost_micros IS NULL THEN 1 ELSE 0 END), 0) AS unpriced,
             COALESCE(SUM(CASE WHEN total_tokens > 0 AND (pricing_known=1
                               OR reported_cost_micros IS NOT NULL) THEN 1 ELSE 0 END), 0) AS priced
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
        return pool_meter(percents, plans=plans)
    if unread:
        return _empty_pool_meter("No reading yet")
    return _empty_pool_meter("No plans")


def grok_pool_meter(rows) -> dict:
    percents: list[float] = []
    unread = 0
    for row in rows:
        if row["disabled"]:
            continue
        extra = accounts.cred_extra(row)
        if grok_mod.is_held(row) or not grok_mod.credential_is_authed(row, extra):
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


def kimi_pool_meter(rows) -> dict:
    percents: list[float] = []
    plans: list[str] = []
    unread = 0
    for row in rows:
        if row["disabled"]:
            continue
        extra = accounts.cred_extra(row)
        if kimi_mod.is_held(row) or not kimi_mod.credential_is_authed(row, extra):
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


def claude_pool_meter(rows) -> dict:
    percents: list[float] = []
    plans: list[str] = []
    unread = 0
    for row in rows:
        if row["disabled"]:
            continue
        extra = accounts.cred_extra(row)
        if claude_mod.is_held(row) or not claude_mod.credential_is_authed(row, extra):
            continue
        billing = extra.get("billing") if isinstance(extra.get("billing"), dict) else {}
        if not claude_mod.billing_has_subscription(billing):
            unread += 1
            continue
        plans.append(str(billing.get("plan_type") or extra.get("subscription_type") or "Claude"))
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
    return _finish_pool_meter(percents, plans, unread)


def mimo_pool_meter(rows) -> dict:
    percents: list[float] = []
    plans: list[str] = []
    unread = 0
    for row in rows:
        if row["disabled"]:
            continue
        extra = accounts.cred_extra(row)
        if mimo_mod.is_held(row) or not mimo_mod.credential_is_authed(row, extra):
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


def zai_pool_meter(rows) -> dict:
    percents: list[float] = []
    plans: list[str] = []
    unread = 0
    for row in rows:
        if row["disabled"]:
            continue
        extra = accounts.cred_extra(row)
        if zai_mod.is_held(row) or not zai_mod.credential_is_authed(row, extra):
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
