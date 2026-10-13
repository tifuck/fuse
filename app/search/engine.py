from __future__ import annotations

import asyncio
import hashlib
import contextlib
import sys
from pathlib import Path
import logging
import re
import time
from collections.abc import Callable
from dataclasses import replace
from urllib.parse import urljoin, urlparse

import httpx

from ..config import settings
from ..security import limiter
from .cache import TtlLru
from .client import (
    TIMEOUT_BRAVE,
    TIMEOUT_DDG,
    TIMEOUT_FETCH,
    TIMEOUT_WIKI,
    get_client,
    get_fetch_client,
)
from . import adapters
from .extract import (
    FETCH_CHARS,
    FETCH_MAX_BYTES,
    PDF_MAX_BYTES,
    PageExtract,
    extract_page,
    find_windows,
    select_passages,
)
from .pack import MODEL_SEARCH_FAILED, flatten_hits, pack_page, pack_search
from .providers.base import QueryResult, SearchHit, canonical_url
from .text import coverage, terms
from .providers.brave import BraveProvider, brave_auth_failed, freshness_bucket
from .providers.bing import BingProvider
from .providers.ddg import DdgProvider
from .providers.hn import HnProvider
from .providers.quotes import QuotesProvider, is_live_query, quote_ids
from .providers.wikipedia import WikipediaProvider
from .providers.yahoo import YahooProvider
from .ssrf import (
    allowed_fetch_url,
    domain_allowed,
    host_of,
    is_blocked_host,
    normalize_url,
    pin_https_url,
    public_http_url,
    public_ips,
)

logger = logging.getLogger(__name__)

MAX_QUERY = 400
SEARCH_PER_MIN = 30
SEARCH_PER_HOUR = 200
FETCH_PER_MIN = 30  # per user, shared by a run and all of its subagents
# Search-time page reads (excerpts for the top results) have their own
# budget so they never use up the reads the model asks for itself.
PREFETCH_PER_MIN = 60
SEARCH_CONCURRENCY = 8
MAX_QUERIES_PER_BULK = 8
QUERY_DEADLINE = 6.0
QUERY_SOFT_DEADLINE = 1.5
FETCH_DEADLINE = 10.0
PDF_DEADLINE = 15.0
FETCH_MAX_REDIRECTS = 5
# Browser-shaped request. Markdown is preferred: docs platforms (Mintlify,
# ReadMe, Cloudflare, Vercel, Stripe, Anthropic, xAI ...) answer
# "Accept: text/markdown" with the page as clean markdown; every other site
# ignores it and sends HTML. gzip cuts transfer ~10x (decoded under a cap by
# client.CappedTransport); some servers stall "identity" requests outright.
FETCH_ACCEPT = (
    "text/markdown, text/html;q=0.9, application/xhtml+xml;q=0.9, text/plain;q=0.8, "
    "application/json;q=0.8, application/pdf;q=0.7, */*;q=0.5"
)
# Sent where markdown negotiation misfires: a URL that already names a file
# (docs.x.ai answers ".../models.md" + "Accept: text/markdown" with a 404),
# and the one retry after a 404/406 to a markdown-first request.
BROWSER_ACCEPT = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
_FILE_SUFFIXES = (".md", ".markdown", ".txt", ".json", ".csv", ".xml", ".pdf", ".rst", ".yaml", ".yml", ".toml")
FETCH_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
}
API_HEADERS = {
    "User-Agent": "FuseReader/1.0 (dashboard page reader)",
    "Accept-Encoding": "gzip, deflate",
}
# Statuses bot walls answer with; the host is remembered so search-time
# excerpts stop trying it for a while.
BLOCK_STATUSES = frozenset({401, 403, 418, 429, 451, 503, 999})
BLOCKED_HOST_TTL = 900.0
_TEXT_TYPES = frozenset(
    {
        "application/json", "application/xml", "application/javascript", "application/x-javascript",
        "application/ecmascript", "application/yaml", "application/x-yaml", "application/toml",
        "application/x-sh", "application/x-ndjson", "application/x-tex", "application/sql",
        "application/graphql",
    }
)
_SNIFF_TYPES = ("", "application/octet-stream", "binary/octet-stream", "application/x-download", "application/force-download")

_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_FILLER = re.compile(
    r"\b(can you|could you|please|search the web|look(?:ing)? up|tell me|what is|what's|find(?: me)?|google|lookup)\b",
    re.I,
)
_LIVE = re.compile(r"\b(price|quote|spot|ticker)\b", re.I)
_CRYPTO = re.compile(r"\b(btc|eth|bitcoin|ethereum)\b", re.I)
_FIAT = re.compile(r"\b(usd|eur|gbp)\b", re.I)
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_ENTITY_START = (
    "how",
    "why",
    "what",
    "when",
    "where",
    "who",
    "which",
    "compare",
    "vs",
    "versus",
)
_ENTITY_TOKENS = {"today", "latest", "news", "vs", "versus"}
_RECENCY_WORDS = re.compile(r"\b(today|latest|news|now)\b", re.I)

_cache = TtlLru(512)
_page_cache = TtlLru(256)
_blocked_hosts = TtlLru(512)
_search_sem = asyncio.Semaphore(SEARCH_CONCURRENCY)
_wiki = WikipediaProvider()
_ddg = DdgProvider()
_bing = BingProvider()
_yahoo = YahooProvider()
_hn = HnProvider()
_quotes = QuotesProvider()
MAX_HITS_MERGED = 8
PAGE_CACHE_TTL = 900.0


def _parse_page(
    body: str, ctype: str, url: str, download_truncated: bool, received: int
) -> PageExtract:
    """Main-content extraction; CPU-bound, run off the loop."""
    base_extract = extract_page(body, content_type=ctype, url=url)
    if download_truncated:
        base_extract.truncated = True
        base_extract.download_truncated = True
    base_extract.bytes_in = received
    return base_extract


def _safe_links(extract: PageExtract) -> set[str]:
    out: set[str] = set()
    for link in extract.links:
        clean = public_http_url(str(link.get("url") or ""), https_only=True)
        if clean:
            out.add(clean)
    return out


def _with_find(
    extract: PageExtract,
    find: str | None,
    *,
    query: str | None = None,
    offset: int = 0,
) -> PageExtract:
    """Re-window the full extracted text for find/query/offset reads."""
    source = extract.full_text or extract.text
    literal = re.sub(r"\s+", " ", find or "").strip()
    if len(literal) >= 2:
        text = find_windows(source, find or "")
        return replace(
            extract, text=text, used_find=True, chars_out=len(text),
            paged=False, next_offset=None,
        )
    if query:
        text = select_passages(source, query=query)
        return replace(
            extract, text=text, used_find=False, chars_out=len(text),
            paged=False, next_offset=None,
        )
    if offset:
        start = min(max(0, offset), len(source))
        end = start + FETCH_CHARS
        text = "" if start >= len(source) else source[start:end]
        return replace(
            extract, text=text, used_find=False, chars_out=len(text),
            offset=start, truncated=len(source) > end, paged=True,
            next_offset=end if end < len(source) else None,
        )
    return replace(extract, used_find=False)


def _host_header(hostname: str, port: int) -> str:
    host = f"[{hostname}]" if ":" in hostname else hostname
    return host if port == 443 else f"{host}:{port}"


async def _pdf_text(raw_bytes: bytes) -> str:
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-I", str(Path(__file__).with_name("pdf_worker.py")), str(PDF_MAX_BYTES),
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(raw_bytes), timeout=PDF_DEADLINE)
        if proc.returncode:
            raise ValueError("PDF extraction failed or exceeded its resource limit")
        return out.decode("utf-8", errors="replace")
    finally:
        if proc.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
        await proc.wait()


def is_fetch_cached(url: str, allowed: set[str] | None = None) -> bool:
    clean = allowed_fetch_url(url)
    return bool(clean and _page_cache.get(normalize_url(clean)) is not None)


def is_host_blocked(url: str) -> bool:
    """True while the host recently answered with a bot-wall status."""
    return _blocked_hosts.get(host_of(url)) is not None


_URL_WORDS = re.compile(r"[/._\-?=&+]+")
# Query-term coverage (title + snippet + URL words) scales the fused score;
# with enough on-topic hits, hits that match almost none of the query are
# dropped. Scraped engines answer flagged server IPs with degraded rankings
# (Bing returned dictionary pages for "won" to "who won the 2026 world cup").
MIN_COVERAGE = 0.3
COVERAGE_FLOOR = 0.4


def _hit_coverage(qterms: set[str], hit: SearchHit) -> float:
    words = _URL_WORDS.sub(" ", urlparse(hit.url).path or "")
    return coverage(qterms, f"{hit.title} {hit.snippet} {words}")


def _merge_hits(groups: list[list[SearchHit]], query: str) -> list[SearchHit]:
    # Reciprocal-rank fusion rewards corroboration without assuming comparable
    # provider-specific scores. Input order remains the stable tie-breaker.
    fused: dict[str, dict] = {}
    order = 0
    for group in groups:
        for position, hit in enumerate(group):
            key = canonical_url(hit.url)
            if not key:
                continue
            provider = hit.provider or "unknown"
            entry = fused.get(key)
            if entry is None:
                entry = {"hit": hit, "scores": {}, "providers": [], "order": order}
                fused[key] = entry
                order += 1
            score = 1.0 / (60 + max(0, hit.rank, position) + 1)
            entry["scores"][provider] = max(score, entry["scores"].get(provider, 0.0))
            if provider not in entry["providers"]:
                entry["providers"].append(provider)
            current = entry["hit"]
            if (not current.snippet and hit.snippet) or (not current.published and hit.published):
                entry["hit"] = replace(
                    current,
                    snippet=current.snippet or hit.snippet,
                    published=current.published or hit.published,
                )

    qterms = set(terms(query))
    scored = len(qterms) >= 2
    for entry in fused.values():
        base = sum(entry["scores"].values())
        if scored and "quotes" not in entry["providers"]:
            entry["cov"] = _hit_coverage(qterms, entry["hit"])
            entry["score"] = base * (COVERAGE_FLOOR + entry["cov"])
        else:
            entry["cov"] = None
            entry["score"] = base
    candidates = list(fused.values())
    on_topic = sum(1 for e in candidates if e["cov"] is not None and e["cov"] >= 0.5)
    if on_topic >= 4:
        candidates = [e for e in candidates if e["cov"] is None or e["cov"] >= MIN_COVERAGE]
    ranked = sorted(candidates, key=lambda item: (-item["score"], item["order"]))
    selected: list[dict] = []
    deferred: list[dict] = []
    domains: dict[str, int] = {}
    for entry in ranked:
        host = (urlparse(entry["hit"].url).hostname or "").lower()
        if domains.get(host, 0) >= 2:
            deferred.append(entry)
            continue
        selected.append(entry)
        domains[host] = domains.get(host, 0) + 1
        if len(selected) == MAX_HITS_MERGED:
            break
    if len(selected) < MAX_HITS_MERGED:
        selected.extend(deferred[: MAX_HITS_MERGED - len(selected)])

    return [
        replace(entry["hit"], query=query, providers=tuple(entry["providers"]))
        for entry in selected[:MAX_HITS_MERGED]
    ]


def _copy_hits(hits, query: str) -> list[SearchHit]:
    return [replace(h, query=query) for h in hits]


def _log(level: int, *, user_id: int, provider: str, status: int, latency_ms: int, cached: int, qlen: int, hits: int, error: str = "", detail: str = "") -> None:
    logger.log(
        level,
        "fuse.search user=%s provider=%s status=%s error=%s latency_ms=%s cached=%s qlen=%s hits=%s detail=%s",
        user_id,
        provider or "-",
        status,
        error or "-",
        latency_ms,
        cached,
        qlen,
        hits,
        detail or "-",
    )


def sanitize_query(raw: str) -> str:
    text = _CTRL.sub(" ", str(raw or ""))
    text = re.sub(r"\s+", " ", text).strip()
    stripped = _FILLER.sub(" ", text)
    stripped = re.sub(r"\s+", " ", stripped).strip(" ?!.,;:")
    if len(stripped) >= 2:
        text = stripped
    return text[:MAX_QUERY]


def normalize_query(raw: str) -> str:
    text = sanitize_query(raw).lower().rstrip(" ?")
    return re.sub(r"\s+", " ", text).strip()


def is_entity_query(q: str) -> bool:
    text = sanitize_query(q)
    if not text or len(text) > 80:
        return False
    if "site:" in text.lower() or "://" in text:
        return False
    tokens = text.split()
    if not (1 <= len(tokens) <= 6):
        return False
    if _DATE.match(tokens[0] if tokens else ""):
        return False
    if any(_DATE.match(t) for t in tokens):
        return False
    first = tokens[0].lower().rstrip(".,")
    if first in _ENTITY_START:
        return False
    if any(t.lower().rstrip(".,") in _ENTITY_TOKENS for t in tokens):
        return False
    return True


def infer_recency(query: str, recency_days: int | None) -> int | None:
    if recency_days is not None:
        try:
            days = int(recency_days)
        except (TypeError, ValueError):
            days = None
        else:
            if 1 <= days <= 365:
                return days
    q = query or ""
    if _LIVE.search(q) or (_CRYPTO.search(q) and _FIAT.search(q)):
        return 1
    if _RECENCY_WORDS.search(q):
        return 7
    return None


def cache_ttl(bucket: str | None, kind: str) -> float | None:
    if kind == "empty":
        return 20.0
    if kind != "ok":
        return None
    if bucket == "pd":
        return 45.0
    return 600.0


def _allow(user_id: int, kind: str) -> bool:
    per_min = {"fetch": FETCH_PER_MIN, "prefetch": PREFETCH_PER_MIN}.get(kind, SEARCH_PER_MIN)
    if kind == "search" and not limiter.allow(f"chat-{kind}-h:{user_id}", SEARCH_PER_HOUR, 3600):
        return False
    return limiter.allow(f"chat-{kind}:{user_id}", per_min, 60)


def _cache_key(normalized: str, bucket: str | None, provider: str) -> str:
    raw = f"{normalized}|{bucket or '-'}|{provider}"
    return hashlib.sha256(raw.encode()).hexdigest()


def _brave_key() -> str:
    return (getattr(settings, "brave_search_api_key", "") or "").strip()


def warn_if_unkeyed() -> None:
    if not _brave_key():
        logger.info("search_provider=keyless ddg+bing+yahoo+hn+wikipedia+quotes")


async def _one_query(
    query: str,
    *,
    user_id: int,
    recency_days: int | None,
    cancel: asyncio.Event | None,
) -> QueryResult:
    started = time.monotonic()
    original = query
    if cancel and cancel.is_set():
        return QueryResult(
            query=original, hits=[], error="Tool interrupted.", kind="provider_error",
            provider="", latency_ms=0, cached=False, status=0,
        )
    if len(sanitize_query(query)) < 2:
        return QueryResult(
            query=original, hits=[], error="Query too short.", kind="provider_error",
            provider="", latency_ms=0, cached=False, status=0,
        )
    recency = infer_recency(query, recency_days)
    bucket = freshness_bucket(recency)
    key = _brave_key()
    use_brave = bool(key) and not brave_auth_failed()
    ck = _cache_key(normalize_query(query), bucket, "mix-b" if use_brave else "mix")
    cached = _cache.get(ck)
    if isinstance(cached, QueryResult):
        hit = QueryResult(
            query=original,
            hits=[replace(hit, query=original) for hit in cached.hits],
            error=cached.error,
            kind=cached.kind,
            provider=cached.provider,
            latency_ms=int((time.monotonic() - started) * 1000),
            cached=True,
            status=cached.status,
            last_timeout=cached.last_timeout,
        )
        _log(
            logging.INFO,
            user_id=user_id,
            provider=hit.provider,
            status=hit.status,
            latency_ms=hit.latency_ms,
            cached=1,
            qlen=len(original),
            hits=len(hit.hits),
        )
        return hit
    if not _allow(user_id, "search"):
        ms = int((time.monotonic() - started) * 1000)
        _log(logging.WARNING, user_id=user_id, provider="", status=429, latency_ms=ms, cached=0, qlen=len(original), hits=0, error="rate_limit")
        return QueryResult(
            query=original, hits=[], error="Search rate limit reached. Try again shortly.",
            kind="rate_limit", provider="", latency_ms=ms, cached=False, status=429,
        )
    client = get_client()
    last_timeout: httpx.Timeout | None = None
    failures: list[str] = []
    try:
        async with _search_sem:
            if cancel and cancel.is_set():
                raise asyncio.CancelledError

            async def finish(hits: list[SearchHit], provider: str, status: int, timeout) -> QueryResult:
                kind = "ok" if hits else "empty"
                ms = int((time.monotonic() - started) * 1000)
                result = QueryResult(
                    query=original,
                    hits=hits,
                    error=None if hits else "No results.",
                    kind=kind,
                    provider=provider,
                    latency_ms=ms,
                    cached=False,
                    status=status,
                    last_timeout=timeout,
                )
                ttl = cache_ttl(bucket, kind)
                if ttl:
                    _cache.set(_cache_key(normalize_query(query), bucket, "mix-b" if use_brave else "mix"), result, ttl)
                _log(
                    logging.INFO if hits else logging.WARNING,
                    user_id=user_id,
                    provider=provider,
                    status=status,
                    latency_ms=ms,
                    cached=0,
                    qlen=len(original),
                    hits=len(hits),
                    error="" if hits else "empty",
                )
                return result

            async def _safe(name, coro):
                try:
                    return name, await coro
                except Exception as exc:
                    logger.error("fuse.search provider=%s error=%s", name, type(exc).__name__)
                    return name, None

            live = is_live_query(query)
            entity = is_entity_query(query)
            jobs = [
                ("ddg", _ddg.search(client, query, recency_days=recency, timeout=TIMEOUT_DDG)),
                ("bing", _bing.search(client, query, recency_days=recency, timeout=_bing.timeout)),
                ("yahoo", _yahoo.search(client, query, recency_days=recency, timeout=_yahoo.timeout)),
            ]
            if not live and not entity:
                jobs.append(("hn", _hn.search(client, query, recency_days=recency, timeout=_hn.timeout)))
                jobs.append(("wikipedia", _wiki.search(client, query, recency_days=None, timeout=TIMEOUT_WIKI)))
            if use_brave:
                jobs.append(
                    (
                        "brave",
                        BraveProvider(key).search(client, query, recency_days=recency, timeout=TIMEOUT_BRAVE),
                    )
                )
            if quote_ids(query) and live:
                jobs.insert(
                    0,
                    ("quotes", _quotes.search(client, query, recency_days=recency, timeout=_quotes.timeout)),
                )

            tasks = {
                asyncio.create_task(_safe(name, coro), name=f"search-{name}"): name
                for name, coro in jobs
            }
            pending = set(tasks)
            gathered = []
            collect_started = time.monotonic()
            soft_at = collect_started + min(QUERY_SOFT_DEADLINE, QUERY_DEADLINE)
            hard_at = collect_started + QUERY_DEADLINE
            try:
                while pending:
                    now = time.monotonic()
                    have_hits = any(resp is not None and resp.hits for _, resp in gathered)
                    if now >= hard_at or (have_hits and now >= soft_at):
                        break
                    wake_at = hard_at if now >= soft_at else min(soft_at, hard_at)
                    done, pending = await asyncio.wait(
                        pending,
                        timeout=max(0, wake_at - now),
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    for task in done:
                        gathered.append(task.result())
            finally:
                for task in pending:
                    task.cancel()
                if pending:
                    await asyncio.gather(*pending, return_exceptions=True)
            completed_names = {name for name, _ in gathered}
            cutoff = "soft_deadline" if any(resp is not None and resp.hits for _, resp in gathered) else "hard_deadline"
            failures.extend(f"{name}:0:{cutoff}" for name in tasks.values() if name not in completed_names)
            provider_order = {name: index for index, (name, _) in enumerate(jobs)}
            gathered.sort(key=lambda item: provider_order[item[0]])
            priority: list[list[SearchHit]] = []
            rest: list[list[SearchHit]] = []
            used: list[str] = []
            # Only a provider that parsed a genuine results page counts as
            # "no results"; the quotes provider reports error="empty" with
            # status=0 when no coin API answered, which is not a page.
            saw_empty = False
            saw_failure = bool(pending)
            for name, resp in gathered:
                if resp is None:
                    failures.append(f"{name}:0:exc")
                    saw_failure = True
                    continue
                last_timeout = resp.last_timeout
                if resp.hits:
                    used.append(name)
                    bucket_hits = _copy_hits(resp.hits, original)
                    if name == "quotes":
                        priority.append(bucket_hits)
                    else:
                        rest.append(bucket_hits)
                    continue
                failures.append(f"{name}:{resp.status}:{resp.error or 'empty'}")
                if resp.status == 200 and (resp.error or "") == "empty" and name != "quotes":
                    saw_empty = True
                else:
                    saw_failure = True
            merged = _merge_hits(priority + rest, original)
            if merged:
                label = "+".join(used) if used else "mix"
                return await finish(merged, label, 200, last_timeout)
            if saw_empty and not saw_failure:
                return await finish([], "mix", 200, last_timeout)

            ms = int((time.monotonic() - started) * 1000)
            detail = ",".join(failures)[:400]
            _log(
                logging.ERROR,
                user_id=user_id,
                provider="keyless",
                status=0,
                latency_ms=ms,
                cached=0,
                qlen=len(original),
                hits=0,
                error="exhausted",
                detail=detail,
            )
            return QueryResult(
                query=original, hits=[], error=MODEL_SEARCH_FAILED, kind="provider_error", provider="",
                latency_ms=ms, cached=False, status=0, last_timeout=last_timeout,
            )
    except asyncio.CancelledError:
        raise
    except Exception:
        ms = int((time.monotonic() - started) * 1000)
        logger.exception("fuse.search failed user=%s qlen=%s detail=%s", user_id, len(original), ",".join(failures)[:400])
        return QueryResult(
            query=original, hits=[], error=MODEL_SEARCH_FAILED, kind="provider_error", provider="",
            latency_ms=ms, cached=False, status=0, last_timeout=last_timeout,
        )


async def search_bulk(
    queries: list[str],
    *,
    user_id: int,
    recency_days: int | None = None,
    cancel: asyncio.Event | None = None,
    on_query_done: Callable[[QueryResult], None] | None = None,
) -> list[QueryResult]:
    cleaned: list[str] = []
    seen_norm: dict[str, int] = {}
    slots: list[str] = []
    for raw in queries:
        q = sanitize_query(raw)
        if not q:
            slots.append("")
            continue
        norm = normalize_query(q)
        if norm in seen_norm:
            slots.append(cleaned[seen_norm[norm]])
            continue
        seen_norm[norm] = len(cleaned)
        cleaned.append(q)
        slots.append(q)
    unique = cleaned[:MAX_QUERIES_PER_BULK]
    by_query: dict[str, QueryResult] = {}

    async def run(q: str) -> None:
        try:
            local = recency_days if recency_days is not None else infer_recency(q, None)
            result = await _one_query(q, user_id=user_id, recency_days=local, cancel=cancel)
        except asyncio.CancelledError:
            result = QueryResult(
                query=q, hits=[], error="Tool interrupted.", kind="provider_error",
                provider="", latency_ms=0, cached=False, status=0,
            )
            raise
        except Exception:
            logger.exception("fuse.search bulk-item failed qlen=%s", len(q))
            result = QueryResult(
                query=q, hits=[], error=MODEL_SEARCH_FAILED,
                kind="provider_error", provider="", latency_ms=0, cached=False, status=0,
            )
        by_query[q] = result
        if on_query_done:
            try:
                on_query_done(result)
            except Exception:
                pass

    if unique:
        async with asyncio.TaskGroup() as tg:
            for q in unique:
                tg.create_task(run(q))
    out: list[QueryResult] = []
    for slot in slots:
        if not slot:
            out.append(
                QueryResult(
                    query="", hits=[], error="Query too short.", kind="provider_error",
                    provider="", latency_ms=0, cached=False, status=0,
                )
            )
            continue
        got = by_query.get(slot)
        if got is None:
            out.append(
                QueryResult(
                    query=slot,
                    hits=[],
                    error=f"Only {MAX_QUERIES_PER_BULK} queries run per call; this one was dropped.",
                    kind="provider_error", provider="", latency_ms=0, cached=False, status=0,
                )
            )
        else:
            out.append(
                QueryResult(
                    query=slot if slot == got.query else slot,
                    hits=[replace(h, query=slot) for h in got.hits],
                    error=got.error,
                    kind=got.kind,
                    provider=got.provider,
                    latency_ms=got.latency_ms,
                    cached=got.cached,
                    status=got.status,
                    last_timeout=got.last_timeout,
                )
            )
    return out


class FetchError(Exception):
    """A page read that failed for a reason the model should be told."""

    def __init__(self, message: str, *, blocked: bool = False, status: int = 0) -> None:
        super().__init__(message)
        self.blocked = blocked
        self.status = status


def _status_message(status: int, host: str) -> str:
    if status in (404, 410):
        return f"Page returned HTTP {status} (not found)."
    if status in BLOCK_STATUSES:
        return (
            f"Page returned HTTP {status}: {host} blocks automated readers. "
            "Use another source for this information."
        )
    return f"Page returned HTTP {status}."


def _content_kind(ctype: str) -> str | None:
    """'pdf', 'text', 'sniff' (decide from the first bytes) or None (not readable)."""
    base = ctype.split(";", 1)[0].strip()
    if base == "application/pdf":
        return "pdf"
    if base.startswith("text/") or base in _TEXT_TYPES or base.endswith(("+json", "+xml")):
        return "text"
    if base in _SNIFF_TYPES:
        return "sniff"
    # Office formats ("...openxmlformats..."), archives, images, media, fonts.
    return None


def _decode(raw: bytes, ctype: str) -> str:
    match = re.search(r"charset=([^;\s]+)", ctype, re.I)
    charset = match.group(1).strip("\"'")[:40] if match else ""
    if not charset:
        if raw.startswith(b"\xef\xbb\xbf"):
            charset = "utf-8-sig"
        elif raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
            charset = "utf-16"
        else:
            head = raw[:4096].decode("ascii", "ignore")
            declared = re.search(r"""<meta[^>]+charset\s*=\s*["']?([\w-]+)""", head, re.I) or re.search(
                r"""<\?xml[^>]+encoding\s*=\s*["']([\w-]+)""", head
            )
            charset = declared.group(1) if declared else "utf-8"
    try:
        text = raw.decode(charset, errors="replace")
    except (LookupError, UnicodeError):
        # Unknown charset, or one that cannot decode with errors="replace"
        # (idna, punycode): read it as UTF-8.
        charset = "utf-8"
        text = raw.decode("utf-8", errors="replace")
    if charset.lower() in ("utf-8", "utf8") and text.count("�") > max(20, len(text) // 200):
        # Undeclared legacy encoding: Windows-1252 is what browsers assume.
        text = raw.decode("cp1252", errors="replace")
    return text


async def _download(
    url: str,
    *,
    cancel: asyncio.Event | None,
    accept: str = FETCH_ACCEPT,
    api: bool = False,
) -> tuple[str, str, bytes, bool]:
    """GET one public HTTPS document: (final_url, content_type, body, truncated).

    Every hop is resolved, pinned to a public IP and checked against the
    domain policy; redirects are followed by hand so none skips the check.
    """
    client = get_fetch_client()
    headers = dict(API_HEADERS if api else FETCH_HEADERS)
    headers["Accept"] = accept
    current = url
    for _ in range(FETCH_MAX_REDIRECTS):
        if cancel and cancel.is_set():
            raise asyncio.CancelledError
        parsed = urlparse(current)
        hostname = parsed.hostname or ""
        if parsed.scheme != "https" or is_blocked_host(hostname) or not domain_allowed(hostname):
            raise FetchError("Refused a non-HTTPS or blocked redirect.")
        port = parsed.port or 443
        addrs = await asyncio.to_thread(public_ips, hostname, port)
        if not addrs:
            raise FetchError("Refused to fetch a non-public address.")
        target = pin_https_url(current, addrs[0])
        req_headers = dict(headers)
        req_headers["Host"] = _host_header(hostname, port)
        async with client.stream(
            "GET",
            target,
            headers=req_headers,
            follow_redirects=False,
            timeout=TIMEOUT_FETCH,
            extensions={"sni_hostname": hostname},
        ) as resp:
            if resp.status_code in (301, 302, 303, 307, 308):
                loc = resp.headers.get("location") or ""
                nxt = public_http_url(urljoin_safe(current, loc), https_only=True)
                if not nxt:
                    raise FetchError("Refused an unsafe redirect.")
                current = nxt
                continue
            if resp.status_code not in (200, 203):
                raise FetchError(
                    _status_message(resp.status_code, hostname),
                    blocked=resp.status_code in BLOCK_STATUSES,
                    status=resp.status_code,
                )
            ctype = (resp.headers.get("content-type") or "").lower()
            kind = _content_kind(ctype)
            if kind is None:
                shown = ctype.split(";", 1)[0] or "unknown type"
                raise FetchError(f"That page is not readable text ({shown}).")
            limit = FETCH_MAX_BYTES if kind == "text" else PDF_MAX_BYTES
            body = bytearray()
            truncated = False
            async for chunk in resp.aiter_bytes(chunk_size=65536):
                if cancel and cancel.is_set():
                    raise asyncio.CancelledError
                if kind == "sniff" and chunk:
                    # Untyped download: PDF, text, or a binary to refuse
                    # before reading megabytes of it.
                    if chunk.startswith(b"%PDF-"):
                        kind, ctype = "pdf", "application/pdf"
                    elif b"\x00" in chunk[:4096]:
                        raise FetchError("That page is not readable text (binary file).")
                    else:
                        kind, ctype, limit = "text", "text/plain", FETCH_MAX_BYTES
                take = chunk[: limit - len(body)]
                body.extend(take)
                if len(take) < len(chunk) or len(body) >= limit:
                    truncated = True
                    break
            return current, ctype, bytes(body), truncated
    raise FetchError("Too many redirects.")


async def _fetch_document(
    clean: str, cancel: asyncio.Event | None
) -> tuple[str, str, str, bool, int]:
    """(final_url, content_type, text, download_truncated, bytes_received)."""
    plan = adapters.plan_for(clean)
    if plan is not None:

        async def download_text(url: str, accept: str) -> tuple[str, str, str]:
            final, ctype, raw, _ = await _download(url, cancel=cancel, accept=accept, api=True)
            return final, ctype, _decode(raw, ctype)

        try:
            async with asyncio.timeout(FETCH_DEADLINE):
                got = await adapters.run(plan, download_text)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.info("fuse.fetch adapter=%s failed: %s", plan.kind, type(exc).__name__)
            got = None
        if got:
            final, ctype, text = got
            return final, ctype, text, False, len(text.encode("utf-8", "replace"))
    path = urlparse(clean).path.lower()
    accept = BROWSER_ACCEPT if path.endswith(_FILE_SUFFIXES) else FETCH_ACCEPT
    async with asyncio.timeout(FETCH_DEADLINE):
        try:
            final, ctype, raw, truncated = await _download(clean, cancel=cancel, accept=accept)
        except FetchError as exc:
            if exc.status not in (404, 406) or accept != FETCH_ACCEPT:
                raise
            final, ctype, raw, truncated = await _download(clean, cancel=cancel, accept=BROWSER_ACCEPT)
    if ctype.startswith("application/pdf"):
        if truncated:
            raise FetchError(f"The PDF is larger than {PDF_MAX_BYTES // 1_000_000} MB and was not read.")
        try:
            text = await _pdf_text(raw)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("fuse.fetch pdf parse failed url_host=%s", host_of(final))
            raise FetchError("The PDF could not be extracted as text.") from None
        return final, "text/plain; source=pdf", text, False, len(raw)
    return final, ctype, _decode(raw, ctype), truncated, len(raw)


async def fetch_url(
    url: str,
    *,
    user_id: int,
    allowed: set[str],
    find: str | None = None,
    query: str | None = None,
    cancel: asyncio.Event | None = None,
    offset: int = 0,
    rate_kind: str = "fetch",
) -> tuple[PageExtract | None, str, str]:
    """Return (extract, wrapped_text, error). extract is None on hard failure."""
    clean = allowed_fetch_url(url)
    if not clean:
        clean_host = host_of(url)
        if public_http_url(url, https_only=True) and clean_host and not domain_allowed(clean_host):
            msg = "That domain is not allowed."
        else:
            msg = "That URL is not an allowed public HTTPS address."
        return None, msg, msg
    cached = _page_cache.get(normalize_url(clean))
    if cached is not None and not allowed_fetch_url(cached[0]):
        # The redirect target fell under the domain policy since it was cached.
        cached = None
    if cached is not None:
        final_url, base_extract = cached
        allowed.add(final_url)
        allowed.update(_safe_links(base_extract))
        extract = _with_find(base_extract, find, query=query, offset=offset)
        extract.bytes_in = base_extract.bytes_in
        return extract, pack_page(final_url, extract), ""
    if not _allow(user_id, rate_kind):
        msg = "Page-read rate limit reached. Try again shortly."
        return None, msg, msg
    host = host_of(clean)
    try:
        final_url, ctype, body, truncated, received = await _fetch_document(clean, cancel)
        base_extract = await asyncio.to_thread(_parse_page, body, ctype, final_url, truncated, received)
        del body
    except asyncio.CancelledError:
        raise
    except FetchError as exc:
        if exc.blocked:
            _blocked_hosts.set(host, True, BLOCKED_HOST_TTL)
        msg = str(exc)
        return None, msg, msg
    except (TimeoutError, httpx.TimeoutException):
        msg = f"Page fetch timed out: {host} did not finish sending the page within {int(FETCH_DEADLINE)}s."
        return None, msg, msg
    except httpx.ConnectError:
        msg = f"Could not connect to {host} (DNS, TLS or network failure)."
        return None, msg, msg
    except httpx.RemoteProtocolError:
        msg = f"{host} dropped the connection before the page finished."
        return None, msg, msg
    except httpx.DecodingError:
        msg = "The page's compression could not be decoded."
        return None, msg, msg
    except httpx.HTTPError as exc:
        logger.warning("fuse.fetch http error host=%s error=%s", host, type(exc).__name__)
        msg = "Network error while reading the page."
        return None, msg, msg
    except Exception:
        logger.exception("fuse.fetch failed")
        msg = "Tool failed."
        return None, msg, msg
    if base_extract.kind == "empty" or (not base_extract.text and base_extract.kind != "spa"):
        msg = "The page had no extractable text."
        return None, msg, msg
    allowed.add(final_url)
    allowed.update(_safe_links(base_extract))
    cached_value = (final_url, base_extract)
    _page_cache.set(normalize_url(clean), cached_value, PAGE_CACHE_TTL)
    _page_cache.set(normalize_url(final_url), cached_value, PAGE_CACHE_TTL)
    extract = _with_find(base_extract, find, query=query, offset=offset)
    extract.bytes_in = received
    return extract, pack_page(final_url, extract), ""


def urljoin_safe(current: str, loc: str) -> str:
    """'' for an unparsable Location (e.g. https://[bad): refused, not a crash."""
    try:
        return urljoin(current, loc)
    except ValueError:
        return ""
