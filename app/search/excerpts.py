"""Search-time page reads: a query-relevant excerpt for the top results.

A snippet is a couple of lines the engine chose; the answer is usually a
paragraph further down the page. Reading the top few result pages while the
search call is still open hands the model that paragraph in the same round,
instead of a search -> read_url -> read_url chain of model round-trips.

Reads run in parallel under a short deadline. Pages that miss it keep loading
in the background into the page cache, so a follow-up read_url is instant.
They use their own rate-limit bucket ("prefetch") and never touch the
per-turn read budgets. Hosts that recently answered with a bot wall, PDFs and
other binaries are skipped.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import replace

from .engine import fetch_url, is_host_blocked
from .extract import best_excerpt
from .providers.base import QueryResult, canonical_url

logger = logging.getLogger(__name__)

PREFETCH_PAGES = 5
PREFETCH_DEADLINE = 2.5
EXCERPT_CHARS = 1_600
EXCERPT_CHARS_MANY = 1_200
_SKIP_SUFFIXES = (".pdf", ".zip", ".gz", ".tar", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".mp4", ".mp3", ".exe", ".dmg", ".iso")
_background: set[asyncio.Task] = set()


def _pick(results: list[QueryResult], max_pages: int) -> list[tuple[str, str]]:
    """(url, query) to read: each query's best hit first, then second-best."""
    depth = 3 if len(results) == 1 else 2 if len(results) <= 3 else 1
    picks: list[tuple[str, str]] = []
    seen: set[str] = set()
    for rank in range(depth):
        for item in results:
            if rank >= len(item.hits):
                continue
            hit = item.hits[rank]
            if "quotes" in (hit.providers or (hit.provider,)):
                continue
            key = canonical_url(hit.url)
            path = key.split("?", 1)[0].lower()
            if not key or key in seen or path.endswith(_SKIP_SUFFIXES) or is_host_blocked(hit.url):
                continue
            seen.add(key)
            picks.append((hit.url, item.query))
            if len(picks) >= max_pages:
                return picks
    return picks


def _settle(task: asyncio.Task) -> None:
    _background.discard(task)
    if not task.cancelled():
        task.exception()  # retrieved: a failed warm-up is not worth a log line


def _excerpts(pages: list[tuple[str, str, str]], budget: int) -> dict[str, str]:
    out: dict[str, str] = {}
    for url, query, text in pages:
        excerpt = best_excerpt(text, query, budget)
        if excerpt:
            out[canonical_url(url)] = excerpt
    return out


async def attach_excerpts(
    results: list[QueryResult],
    *,
    user_id: int,
    max_pages: int = PREFETCH_PAGES,
    deadline: float = PREFETCH_DEADLINE,
) -> list[QueryResult]:
    """Return results whose top hits carry a page excerpt where one was read."""
    if max_pages <= 0:
        return results
    picks = _pick(results, max_pages)
    if not picks:
        return results
    tasks: dict[asyncio.Task, tuple[str, str]] = {}
    for url, query in picks:
        task = asyncio.create_task(
            fetch_url(url, user_id=user_id, allowed=set(), rate_kind="prefetch"),
            name="search-prefetch",
        )
        tasks[task] = (url, query)
    try:
        done, pending = await asyncio.wait(tasks, timeout=deadline)
    except asyncio.CancelledError:
        for task in tasks:
            task.cancel()
        raise
    for task in pending:
        _background.add(task)
        task.add_done_callback(_settle)
    pages: list[tuple[str, str, str]] = []
    for task in done:
        if task.cancelled() or task.exception() is not None:
            continue
        extract, _, _ = task.result()
        if extract is None or extract.kind not in ("html", "text", "json"):
            continue
        url, query = tasks[task]
        pages.append((url, query, extract.full_text or extract.text))
    if not pages:
        return results
    budget = EXCERPT_CHARS if len(picks) <= 3 else EXCERPT_CHARS_MANY
    excerpts = await asyncio.to_thread(_excerpts, pages, budget)
    logger.info(
        "fuse.search excerpts user=%s picked=%s read=%s late=%s", user_id, len(picks), len(excerpts), len(pending)
    )
    return [
        replace(item, hits=[replace(hit, excerpt=excerpts.get(canonical_url(hit.url), "")) for hit in item.hits])
        for item in results
    ]
