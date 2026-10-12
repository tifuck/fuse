from __future__ import annotations

import base64
import html as html_lib
import logging
import re
from urllib.parse import urlparse

import httpx

from ..client import read_limited
from ..ssrf import host_of, public_http_url
from .base import ProviderResponse, SearchHit, empty_reason, safe_get

logger = logging.getLogger(__name__)


def _bing_host_ok(url: str) -> bool:
    host = host_of(url)
    return host == "bing.com" or host.endswith(".bing.com")

TIMEOUT_BING = httpx.Timeout(3.5, connect=1.5)
_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)
_TAG = re.compile(r"(?is)<[^>]+>")
_BLOCK = re.compile(r'class="b_algo"(.*?)(?=class="b_algo"|$)', re.I | re.S)
_H2 = re.compile(r"<h2[^>]*>(.*?)</h2>", re.I | re.S)
_CITE = re.compile(r"<cite[^>]*>(.*?)</cite>", re.I | re.S)
_SNIP = re.compile(r'class="b_lineclamp\d*"[^>]*>(.*?)</p>', re.I | re.S)
_U = re.compile(r"[?&]u=([A-Za-z0-9_-]+)", re.I)
_H2_HREF = re.compile(r"<h2[^>]*>.*?<a[^>]+href=\"([^\"]+)\"", re.I | re.S)
MAX_HITS = 8


def _strip(text: str) -> str:
    text = html_lib.unescape(_TAG.sub(" ", text or ""))
    return re.sub(r"\s+", " ", text).strip()


def _decode_u(raw: str) -> str:
    raw = html_lib.unescape(raw or "")
    if raw.startswith("a1"):
        raw = raw[2:]
    pad = "=" * (-len(raw) % 4)
    for decoder in (base64.urlsafe_b64decode, base64.b64decode):
        try:
            text = decoder(raw + pad).decode("utf-8", "replace")
        except Exception:
            continue
        if text.startswith("http"):
            return text
    return ""


def _target_url(block: str) -> str:
    """The result's real URL from Bing's click-tracking link.

    Bing writes it as base64 in the ck/a link's u= parameter, HTML-escaped
    ("&amp;u=a1aHR0cHM6..."): unescape before matching, or every hit falls
    back to the <cite> text, which is only a breadcrumb ("host › path").
    """
    href = _H2_HREF.search(block)
    candidates = [html_lib.unescape(href.group(1))] if href else []
    candidates.append(html_lib.unescape(block))
    for text in candidates:
        if text.startswith("http") and "bing.com" not in (urlparse(text).hostname or ""):
            direct = public_http_url(text)
            if direct:
                return direct
        match = _U.search(text)
        if match:
            decoded = public_http_url(_decode_u(match.group(1)))
            if decoded:
                return decoded
    return ""


def _cite_url(block: str) -> str:
    cite = _CITE.search(block)
    if not cite:
        return ""
    text = _strip(cite.group(1))
    if "›" in text:
        # Breadcrumb ("https://host › a › b"): only the host is real.
        return ""
    # An empty <cite> must not raise and drop every result.
    host = (text.split() or [""])[0]
    if host.startswith("http"):
        return public_http_url(host)
    if "." in host:
        return public_http_url("https://" + host.strip("/"))
    return ""


def parse_bing_html(raw: str) -> list[SearchHit]:
    hits: list[SearchHit] = []
    seen: set[str] = set()
    for block in _BLOCK.findall(raw or ""):
        title = ""
        h2 = _H2.search(block)
        if h2:
            title = _strip(h2.group(1))
        url = _target_url(block)
        cite_url = _cite_url(block)
        if cite_url and (not url or len(cite_url.rstrip("/")) > len(url.rstrip("/"))):
            url = cite_url
        if not url or not title or url in seen:
            continue
        if "bing.com" in (urlparse(url).hostname or ""):
            continue
        seen.add(url)
        snip_m = _SNIP.search(block)
        snippet = _strip(snip_m.group(1))[:300] if snip_m else ""
        hits.append(SearchHit(title=title[:200], url=url, snippet=snippet, provider="bing", rank=len(hits)))
        if len(hits) >= MAX_HITS:
            break
    return hits


class BingProvider:
    name = "bing"
    timeout = TIMEOUT_BING

    async def search(
        self,
        client: httpx.AsyncClient,
        query: str,
        *,
        recency_days: int | None,
        timeout: httpx.Timeout,
    ) -> ProviderResponse:
        params = {"q": query, "setlang": "en", "cc": "US"}
        if recency_days is not None and recency_days <= 1:
            params["filters"] = "ex1:\"ez1\""
        elif recency_days is not None and recency_days <= 7:
            params["filters"] = "ex1:\"ez2\""
        elif recency_days is not None:
            params["filters"] = "ex1:\"ez3\""
        try:
            resp = await safe_get(
                client,
                "https://www.bing.com/search",
                host_ok=_bing_host_ok,
                params=params,
                headers={
                    "User-Agent": _UA,
                    "Accept": "text/html",
                    "Accept-Language": "en-US,en;q=0.9",
                },
                timeout=timeout,
            )
            if resp.status_code != 200:
                logger.error("fuse.search provider=bing status=%s", resp.status_code)
                return ProviderResponse(hits=[], status=resp.status_code, error="http", last_timeout=timeout)
            body = await read_limited(resp)
        except httpx.TimeoutException:
            logger.error("fuse.search provider=bing error=timeout")
            return ProviderResponse(hits=[], status=0, error="timeout", last_timeout=timeout)
        except (httpx.HTTPError, ValueError) as exc:
            logger.error("fuse.search provider=bing error=%s", type(exc).__name__)
            return ProviderResponse(hits=[], status=0, error="http", last_timeout=timeout)
        hits = parse_bing_html(body)
        error = None if hits else empty_reason(body, 'class="b_algo"')
        if error:
            logger.warning("fuse.search provider=bing status=200 error=%s body_len=%s", error, len(body or ""))
        return ProviderResponse(hits=hits, status=200, error=error, last_timeout=timeout)
