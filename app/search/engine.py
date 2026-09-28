from __future__ import annotations

import asyncio
import hashlib
import io
import logging
import re
import time
from collections.abc import Callable
from dataclasses import replace
from html.parser import HTMLParser
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
from .extract import FETCH_MAX_BYTES, PageExtract, extract_page
from .pack import MODEL_SEARCH_FAILED, flatten_hits, pack_page, pack_search
from .providers.base import QueryResult, SearchHit, canonical_url
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
SEARCH_CONCURRENCY = 8
MAX_QUERIES_PER_BULK = 8
QUERY_DEADLINE = 6.0
QUERY_SOFT_DEADLINE = 1.5
FETCH_DEADLINE = 8.0
FETCH_MAX_REDIRECTS = 5

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
_page_cache = TtlLru(128)
_search_sem = asyncio.Semaphore(SEARCH_CONCURRENCY)
_wiki = WikipediaProvider()
_ddg = DdgProvider()
_bing = BingProvider()
_yahoo = YahooProvider()
_hn = HnProvider()
_quotes = QuotesProvider()
MAX_HITS_MERGED = 8
PAGE_CACHE_TTL = 300.0


class _PageLinks(HTMLParser):
    def __init__(self, base_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.urls: set[str] = set()

    def handle_starttag(self, tag, attrs):
        if tag.lower() not in ("a", "area"):
            return
        href = next((value for key, value in attrs if key.lower() == "href"), None)
        if not href:
            return
        clean = public_http_url(urljoin(self.base_url, href), https_only=True)
        if clean:
            self.urls.add(clean)


def _discovered_links(raw: str, content_type: str, base_url: str) -> set[str]:
    if "html" not in content_type:
        return set()
    parser = _PageLinks(base_url)
    try:
        parser.feed(raw)
        parser.close()
    except Exception:
        pass
    return parser.urls


def _with_find(
    extract: PageExtract,
    find: str | None,
    *,
    raw: str = "",
    content_type: str = "",
    url: str = "",
    query: str | None = None,
    offset: int = 0,
) -> PageExtract:
    if not find or len(re.sub(r"\s+", " ", find).strip()) < 2:
        if query and raw:
            return extract_page(raw, content_type=content_type, url=url, query=query)
        if offset and raw:
            return extract_page(raw, content_type=content_type, url=url, offset=offset)
        return replace(extract, used_find=False)
    if raw:
        return extract_page(raw, content_type=content_type, url=url, find=find, query=query)
    from .extract import find_windows

    text = find_windows(extract.text, find)
    return replace(extract, text=text, used_find=True, chars_out=len(text))


def _host_header(hostname: str, port: int) -> str:
    host = f"[{hostname}]" if ":" in hostname else hostname
    return host if port == 443 else f"{host}:{port}"


def is_fetch_cached(url: str, allowed: set[str] | None = None) -> bool:
    clean = allowed_fetch_url(url)
    return bool(clean and _page_cache.get(normalize_url(clean)) is not None)


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

    ranked = sorted(fused.values(), key=lambda item: (-sum(item["scores"].values()), item["order"]))
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
    per_min = FETCH_PER_MIN if kind == "fetch" else SEARCH_PER_MIN
    if not limiter.allow(f"chat-{kind}:{user_id}", per_min, 60):
        return False
    if kind != "fetch" and not limiter.allow(f"chat-{kind}-h:{user_id}", SEARCH_PER_HOUR, 3600):
        return False
    return True


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
    ck = _cache_key(normalize_query(query), bucket, "mix")
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
                    _cache.set(_cache_key(normalize_query(query), bucket, "mix"), result, ttl)
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
            for name, resp in gathered:
                if resp is None:
                    failures.append(f"{name}:0:exc")
                    continue
                last_timeout = resp.last_timeout
                if resp.hits:
                    used.append(name)
                    bucket_hits = _copy_hits(resp.hits, original)
                    if name == "quotes":
                        priority.append(bucket_hits)
                    else:
                        rest.append(bucket_hits)
                else:
                    failures.append(f"{name}:{resp.status}:{resp.error or 'empty'}")
            merged = _merge_hits(priority + rest, original)
            if merged:
                label = "+".join(used) if used else "mix"
                return await finish(merged, label, 200, last_timeout)
            # Only providers that parsed a genuine results page count as "no
            # results"; blocked/parse/http failures must surface as a failure.
            if failures and all(item.endswith(":empty") for item in failures):
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
                    query=slot, hits=[], error=MODEL_SEARCH_FAILED,
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


async def fetch_url(
    url: str,
    *,
    user_id: int,
    allowed: set[str],
    find: str | None = None,
    query: str | None = None,
    cancel: asyncio.Event | None = None,
    offset: int = 0,
) -> tuple[PageExtract | None, str, str]:
    """Return (extract, wrapped_text, error). extract is None on hard failure."""
    clean = allowed_fetch_url(url)
    if not clean:
        if public_http_url(url, https_only=True) and not domain_allowed(host_of(url)):
            msg = "That domain is not allowed."
        else:
            msg = "That URL is not an allowed public HTTPS address."
        return None, msg, msg
    cached = _page_cache.get(normalize_url(clean))
    if cached is not None:
        final_url, base_extract, links, raw, content_type = cached
        allowed.add(final_url)
        allowed.update(links)
        extract = _with_find(
            base_extract,
            find,
            raw=raw,
            content_type=content_type,
            url=final_url,
            query=query,
            offset=offset,
        )
        return extract, pack_page(final_url, extract), ""
    if not _allow(user_id, "fetch"):
        msg = "Page-read rate limit reached. Try again shortly."
        return None, msg, msg
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html, text/plain, application/json, application/pdf;q=0.8, */*;q=0.1",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "identity",
    }
    client = get_fetch_client()
    current = clean
    try:
        async with asyncio.timeout(FETCH_DEADLINE):
            for _ in range(FETCH_MAX_REDIRECTS):
                if cancel and cancel.is_set():
                    raise asyncio.CancelledError
                parsed = urlparse(current)
                hostname = parsed.hostname or ""
                if parsed.scheme != "https" or is_blocked_host(hostname) or not domain_allowed(hostname):
                    msg = "Refused a non-HTTPS or blocked redirect."
                    return None, msg, msg
                port = parsed.port or 443
                addrs = await asyncio.to_thread(public_ips, hostname, port)
                if not addrs:
                    msg = "Refused to fetch a non-public address."
                    return None, msg, msg
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
                            msg = "Refused an unsafe redirect."
                            return None, msg, msg
                        current = nxt
                        continue
                    if resp.status_code != 200:
                        msg = f"Page returned HTTP {resp.status_code}."
                        return None, msg, msg
                    ctype = (resp.headers.get("content-type") or "").lower()
                    if not any(t in ctype for t in ("text/html", "text/plain", "application/json", "xml", "application/pdf")):
                        msg = "That page is not readable text."
                        return None, msg, msg
                    body_bytes = bytearray()
                    truncated = False
                    async for chunk in resp.aiter_bytes(chunk_size=8192):
                        if cancel and cancel.is_set():
                            raise asyncio.CancelledError
                        if len(body_bytes) >= FETCH_MAX_BYTES:
                            truncated = True
                            break
                        take = chunk[: FETCH_MAX_BYTES - len(body_bytes)]
                        body_bytes.extend(take)
                        if len(take) < len(chunk):
                            truncated = True
                            break
                    raw_bytes = bytes(body_bytes)
                    del body_bytes
                    if "application/pdf" in ctype:
                        try:
                            from pypdf import PdfReader

                            reader = PdfReader(io.BytesIO(raw_bytes), strict=False)
                            body = "\n\n".join((page.extract_text() or "") for page in reader.pages[:100])
                        except Exception:
                            logger.warning("fuse.fetch pdf parse failed url_host=%s", hostname)
                            msg = "The PDF could not be extracted as text."
                            return None, msg, msg
                        ctype = "text/plain; source=pdf"
                    else:
                        match = re.search(r"charset=([^;\s]+)", ctype, re.I)
                        charset = match.group(1).strip("\"'")[:40] if match else "utf-8"
                        try:
                            body = raw_bytes.decode(charset, errors="replace")
                        except LookupError:
                            body = raw_bytes.decode("utf-8", errors="replace")
                    del raw_bytes
                    links = _discovered_links(body, ctype, current)
                    base_extract = extract_page(body, content_type=ctype, url=current)
                    if truncated:
                        base_extract.truncated = True
                    if base_extract.kind == "empty" or (not base_extract.text and base_extract.kind != "spa"):
                        msg = "The page had no extractable text."
                        return None, msg, msg
                    allowed.add(current)
                    allowed.update(links)
                    cached_value = (current, base_extract, links, body, ctype)
                    _page_cache.set(normalize_url(clean), cached_value, PAGE_CACHE_TTL)
                    _page_cache.set(normalize_url(current), cached_value, PAGE_CACHE_TTL)
                    extract = _with_find(
                        base_extract,
                        find,
                        raw=body,
                        content_type=ctype,
                        url=current,
                        query=query,
                        offset=offset,
                    )
                    wrapped = pack_page(current, extract)
                    return extract, wrapped, ""
            msg = "Too many redirects."
            return None, msg, msg
    except asyncio.CancelledError:
        raise
    except TimeoutError:
        msg = "Page fetch timed out."
        return None, msg, msg
    except Exception:
        logger.exception("fuse.fetch failed")
        msg = "Tool failed."
        return None, msg, msg


def urljoin_safe(current: str, loc: str) -> str:
    return urljoin(current, loc)
