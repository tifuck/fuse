from __future__ import annotations

import html as html_lib
import logging
import re
import time
from urllib.parse import urljoin

import httpx

from ..client import TIMEOUT_DDG, PROVIDER_BODY_MAX
from ..ssrf import decode_ddg_url, host_of, public_http_url
from .base import ProviderResponse, SearchHit, empty_reason

logger = logging.getLogger(__name__)

_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)
_SEARCH_HOSTS = {
    "html.duckduckgo.com",
    "lite.duckduckgo.com",
    "duckduckgo.com",
    "www.duckduckgo.com",
}
_DDG_LINK = re.compile(
    r'href="([^"]+)"[^>]*class="[^"]*result__a[^"]*"[^>]*>(.*?)</a>|'
    r'class="[^"]*result__a[^"]*"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
    re.I | re.S,
)
_DDG_SNIP = re.compile(r'class="[^"]*result__snippet[^"]*"[^>]*>(.*?)</(?:a|span|td|div)', re.I | re.S)
_LITE_LINK = re.compile(
    r'<a[^>]*rel=["\']nofollow["\'][^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>|'
    r'<a[^>]*href=["\']([^"\']+)["\'][^>]*rel=["\']nofollow["\'][^>]*>(.*?)</a>',
    re.I | re.S,
)
_LITE_SNIP = re.compile(r"class=['\"]result-snippet['\"][^>]*>(.*?)</td>", re.I | re.S)
_TAG = re.compile(r"(?is)<[^>]+>")

_fail_streak = 0
_break_until = 0.0
_warmed = False
# DuckDuckGo answers flagged server IPs with a bot challenge (HTTP 202/403,
# "anomaly" page) on nearly every request; retrying each query twice only
# added load and latency. One challenge pauses the provider for a while.
CHALLENGE_BACKOFF = 600.0


def ddg_circuit_open() -> bool:
    return time.monotonic() < _break_until


def reset_circuit() -> None:
    global _fail_streak, _break_until
    _fail_streak = 0
    _break_until = 0.0


def _record_challenge() -> None:
    global _fail_streak, _break_until
    _fail_streak = 0
    _break_until = time.monotonic() + CHALLENGE_BACKOFF


def _record_timeout() -> None:
    global _fail_streak, _break_until
    _fail_streak += 1
    if _fail_streak >= 5:
        _break_until = time.monotonic() + 30
        _fail_streak = 0


def _record_ok() -> None:
    global _fail_streak
    _fail_streak = 0


def _strip(text: str) -> str:
    text = html_lib.unescape(_TAG.sub(" ", text or ""))
    return re.sub(r"\s+", " ", text).strip()


def _search_host_ok(url: str) -> bool:
    host = host_of(url)
    return host in _SEARCH_HOSTS or host.endswith(".duckduckgo.com")


def _with_next_start(pattern, raw: str):
    found = list(pattern.finditer(raw))
    for n, match in enumerate(found):
        yield match, found[n + 1].start() if n + 1 < len(found) else len(raw)


def parse_ddg_html(raw: str) -> list[SearchHit]:
    if "anomaly" in (raw or "").lower() and "unfortunate" in (raw or "").lower():
        return []
    snips = [(m.start(), _strip(m.group(1))) for m in _DDG_SNIP.finditer(raw)]
    if not snips:
        snips = [(m.start(), _strip(m.group(1))) for m in _LITE_SNIP.finditer(raw)]
    results: list[SearchHit] = []
    seen: set[str] = set()

    def _snippet_after(pos: int, end: int) -> str:
        # Only before the next result's link: a result without a snippet
        # must not take its neighbour's.
        for start, text in snips:
            if pos <= start < end:
                return text
        return ""

    for match, end in _with_next_start(_DDG_LINK, raw):
        href = match.group(1) or match.group(3) or ""
        title = _strip(match.group(2) or match.group(4) or "")
        url = decode_ddg_url(href)
        if not url or url in seen or not title:
            continue
        seen.add(url)
        snippet = _snippet_after(match.start(), end)
        results.append(SearchHit(title=title[:200], url=url, snippet=snippet[:280], provider="ddg", rank=len(results)))
        if len(results) >= 8:
            break
    if not results:
        for match, end in _with_next_start(_LITE_LINK, raw):
            href = match.group(1) or match.group(3) or ""
            title = _strip(match.group(2) or match.group(4) or "")
            url = decode_ddg_url(href) or public_http_url(html_lib.unescape(href))
            if not url or url in seen or not title:
                continue
            seen.add(url)
            snippet = _snippet_after(match.start(), end)
            results.append(
                SearchHit(title=title[:200], url=url, snippet=snippet[:280], provider="ddg", rank=len(results))
            )
            if len(results) >= 8:
                break
    return results


async def _warm(client: httpx.AsyncClient, timeout: httpx.Timeout) -> None:
    global _warmed
    if _warmed:
        return
    try:
        await _request(client, "GET", "https://duckduckgo.com/", timeout, headers={"User-Agent": _UA, "Accept": "text/html"})
        _warmed = True
    except Exception:
        pass


async def _request(client: httpx.AsyncClient, method: str, url: str, timeout: httpx.Timeout, **kwargs) -> httpx.Response:
    current = url
    for _ in range(4):
        if not _search_host_ok(current):
            raise ValueError("blocked")
        resp = await client.request(method, current, follow_redirects=False, timeout=timeout, **kwargs)
        if resp.status_code in (301, 302, 303, 307, 308) or (
            resp.status_code == 202 and resp.headers.get("location")
        ):
            loc = resp.headers.get("location") or ""
            current = urljoin(current, loc)
            kwargs.pop("data", None)
            method = "GET"
            continue
        text = getattr(resp, "text", None)
        if isinstance(text, str):
            raw = text.encode("utf-8", "replace")[:PROVIDER_BODY_MAX]
        else:
            raw = bytes(getattr(resp, "content", b"") or b"")[:PROVIDER_BODY_MAX]
        return httpx.Response(
            status_code=resp.status_code,
            headers=resp.headers,
            content=raw,
            request=getattr(resp, "request", None),
        )
    raise ValueError("redirect")


class DdgProvider:
    name = "ddg"
    timeout = TIMEOUT_DDG

    async def search(
        self,
        client: httpx.AsyncClient,
        query: str,
        *,
        recency_days: int | None,
        timeout: httpx.Timeout,
    ) -> ProviderResponse:
        last_timeout = timeout
        if ddg_circuit_open():
            logger.error("fuse.search provider=ddg error=circuit")
            return ProviderResponse(hits=[], status=0, error="circuit", last_timeout=last_timeout)
        headers = {"User-Agent": _UA, "Accept": "text/html"}
        await _warm(client, timeout)
        df = ""
        if recency_days is not None and recency_days <= 1:
            df = "d"
        elif recency_days is not None and recency_days <= 7:
            df = "w"
        elif recency_days is not None and recency_days <= 31:
            df = "m"
        elif recency_days is not None:
            df = "y"
        params = {"q": query}
        if df:
            params["df"] = df
        attempts = (
            ("GET", "https://lite.duckduckgo.com/lite/", {"params": params, "headers": headers}),
            (
                "POST",
                "https://html.duckduckgo.com/html/",
                {
                    "data": {"q": query, "kl": "us-en", "df": df},
                    "headers": {**headers, "Content-Type": "application/x-www-form-urlencoded"},
                },
            ),
        )
        last_status = 0
        last_error = "http"
        for method, url, kw in attempts:
            try:
                resp = await _request(client, method, url, timeout, **kw)
            except httpx.TimeoutException:
                last_status, last_error = 0, "timeout"
                logger.error("fuse.search provider=ddg error=timeout method=%s", method)
                _record_timeout()
                continue
            except Exception as exc:
                last_status, last_error = 0, "http"
                logger.error("fuse.search provider=ddg error=%s method=%s", type(exc).__name__, method)
                continue
            last_status = resp.status_code
            if resp.status_code in (403, 429):
                last_error = "challenge"
                _record_challenge()
                logger.warning("fuse.search provider=ddg status=%s method=%s backoff=%ss", resp.status_code, method, int(CHALLENGE_BACKOFF))
                break
            if resp.status_code not in (200, 202):
                last_error = f"http_{resp.status_code}"
                logger.error("fuse.search provider=ddg status=%s method=%s", resp.status_code, method)
                continue
            found = parse_ddg_html(resp.text)
            if found:
                _record_ok()
                return ProviderResponse(hits=found, status=200, error=None, last_timeout=last_timeout)
            last_error = empty_reason(resp.text, "result__a") if resp.status_code == 200 else "challenge"
            if last_error == "empty" and "anomaly" in (resp.text or "").lower():
                last_error = "blocked"
            if last_error in ("challenge", "blocked"):
                _record_challenge()
                logger.warning(
                    "fuse.search provider=ddg status=%s method=%s error=%s backoff=%ss",
                    resp.status_code, method, last_error, int(CHALLENGE_BACKOFF),
                )
                break
            logger.warning(
                "fuse.search provider=ddg status=%s method=%s error=%s body_len=%s",
                resp.status_code,
                method,
                last_error,
                len(resp.text or ""),
            )
        return ProviderResponse(hits=[], status=last_status, error=last_error, last_timeout=last_timeout)
