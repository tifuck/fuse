from __future__ import annotations

import base64
import html as html_lib
import logging
import re

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


def parse_bing_html(raw: str) -> list[SearchHit]:
    hits: list[SearchHit] = []
    seen: set[str] = set()
    for block in _BLOCK.findall(raw or ""):
        title = ""
        h2 = _H2.search(block)
        if h2:
            title = _strip(h2.group(1))
        cite_url = ""
        cite = _CITE.search(block)
        if cite:
            host = _strip(cite.group(1)).split()[0]
            if host.startswith("http"):
                cite_url = public_http_url(host)
            elif "." in host:
                cite_url = public_http_url("https://" + host.strip("/"))
        u_url = ""
        u = _U.search(block)
        if u:
            u_url = public_http_url(_decode_u(u.group(1)))
        if u_url and (not cite_url or len(u_url.rstrip("/")) > len(cite_url.rstrip("/"))):
            url = u_url
        else:
            url = cite_url or u_url
        if not url or not title or url in seen:
            continue
        if "bing.com" in (url or ""):
            continue
        seen.add(url)
        snip_m = _SNIP.search(block)
        snippet = _strip(snip_m.group(1))[:280] if snip_m else ""
        hits.append(SearchHit(title=title[:200], url=url, snippet=snippet, provider="bing", rank=len(hits)))
        if len(hits) >= 6:
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
