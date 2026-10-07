from __future__ import annotations

import html as html_lib
import logging
import re
from urllib.parse import unquote

import httpx

from ..client import read_limited
from ..ssrf import host_of, public_http_url
from .base import ProviderResponse, SearchHit, empty_reason, safe_get

logger = logging.getLogger(__name__)


def _yahoo_host_ok(url: str) -> bool:
    host = host_of(url)
    return host == "yahoo.com" or host.endswith(".yahoo.com")

TIMEOUT_YAHOO = httpx.Timeout(3.5, connect=1.5)
_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)
_TAG = re.compile(r"(?is)<[^>]+>")
_BLOCK = re.compile(r'class="[^"]*algo-sr[^"]*"(.*?)(?=class="[^"]*algo-sr|$)', re.I | re.S)
_RU = re.compile(r"RU=(https%3[aA]%2[fF]%2[fF][^/\s\"']+)")
_TITLE = re.compile(r"<h3[^>]*>(.*?)</h3>", re.I | re.S)
_SNIP = re.compile(r"<p class=\"fc-dustygray[^\"]*\"[^>]*>(.*?)</p>", re.I | re.S)


def _strip(text: str) -> str:
    text = html_lib.unescape(_TAG.sub(" ", text or ""))
    return re.sub(r"\s+", " ", text).strip()


def parse_yahoo_html(raw: str) -> list[SearchHit]:
    hits: list[SearchHit] = []
    seen: set[str] = set()
    for block in _BLOCK.findall(raw or ""):
        title = ""
        t = _TITLE.search(block)
        if t:
            title = _strip(t.group(1))
        url = ""
        ru = _RU.search(block)
        if ru:
            url = public_http_url(unquote(ru.group(1)))
        if not url or not title or url in seen:
            continue
        if "yahoo.com" in url:
            continue
        seen.add(url)
        sn = _SNIP.search(block)
        snippet = _strip(sn.group(1))[:280] if sn else ""
        hits.append(SearchHit(title=title[:200], url=url, snippet=snippet, provider="yahoo", rank=len(hits)))
        if len(hits) >= 6:
            break
    return hits


class YahooProvider:
    name = "yahoo"
    timeout = TIMEOUT_YAHOO

    async def search(
        self,
        client: httpx.AsyncClient,
        query: str,
        *,
        recency_days: int | None,
        timeout: httpx.Timeout,
    ) -> ProviderResponse:
        params = {"p": query}
        if recency_days is not None and recency_days <= 1:
            params["fr2"] = "time"
            params["age"] = "1d"
        elif recency_days is not None and recency_days <= 7:
            params["fr2"] = "time"
            params["age"] = "1w"
        elif recency_days is not None:
            params["fr2"] = "time"
            params["age"] = "1m"
        try:
            resp = await safe_get(
                client,
                "https://search.yahoo.com/search",
                host_ok=_yahoo_host_ok,
                params=params,
                headers={"User-Agent": _UA, "Accept": "text/html", "Accept-Language": "en-US,en;q=0.9"},
                timeout=timeout,
            )
            if resp.status_code != 200:
                logger.error("fuse.search provider=yahoo status=%s", resp.status_code)
                return ProviderResponse(hits=[], status=resp.status_code, error="http", last_timeout=timeout)
            body = await read_limited(resp)
        except httpx.TimeoutException:
            logger.error("fuse.search provider=yahoo error=timeout")
            return ProviderResponse(hits=[], status=0, error="timeout", last_timeout=timeout)
        except (httpx.HTTPError, ValueError) as exc:
            logger.error("fuse.search provider=yahoo error=%s", type(exc).__name__)
            return ProviderResponse(hits=[], status=0, error="http", last_timeout=timeout)
        hits = parse_yahoo_html(body)
        error = None if hits else empty_reason(body, "algo-sr")
        if error:
            logger.warning("fuse.search provider=yahoo status=200 error=%s body_len=%s", error, len(body or ""))
        return ProviderResponse(hits=hits, status=200, error=error, last_timeout=timeout)
