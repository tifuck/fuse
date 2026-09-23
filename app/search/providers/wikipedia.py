from __future__ import annotations

import html as html_lib
import re
from urllib.parse import urljoin

import httpx

from ..client import TIMEOUT_WIKI
from ..ssrf import public_http_url, wiki_page_url
from .base import ProviderResponse, SearchHit

_UA = "FuseChat/1.0 (dashboard search for signed-in users)"
_SEARCH_HOSTS = {"en.wikipedia.org"}
_TAG = re.compile(r"(?is)<[^>]+>")


def _strip(text: str) -> str:
    text = html_lib.unescape(_TAG.sub(" ", text or ""))
    return re.sub(r"\s+", " ", text).strip()


class WikipediaProvider:
    name = "wikipedia"
    timeout = TIMEOUT_WIKI

    async def search(
        self,
        client: httpx.AsyncClient,
        query: str,
        *,
        recency_days: int | None,
        timeout: httpx.Timeout,
    ) -> ProviderResponse:
        del recency_days
        last_timeout = timeout
        url = "https://en.wikipedia.org/w/api.php"
        try:
            resp = await client.get(
                url,
                params={
                    "action": "query",
                    "list": "search",
                    "srsearch": query,
                    "srlimit": 3,
                    "srprop": "snippet",
                    "format": "json",
                    "utf8": 1,
                },
                headers={"User-Agent": _UA, "Accept": "application/json"},
                timeout=timeout,
            )
        except httpx.TimeoutException:
            return ProviderResponse(hits=[], status=0, error="timeout", last_timeout=last_timeout)
        except httpx.HTTPError:
            return ProviderResponse(hits=[], status=0, error="http", last_timeout=last_timeout)
        if resp.status_code in (301, 302, 303, 307, 308):
            loc = resp.headers.get("location") or ""
            nxt = urljoin(url, loc)
            if "wikipedia.org" not in (httpx.URL(nxt).host or ""):
                return ProviderResponse(hits=[], status=resp.status_code, error="redirect", last_timeout=last_timeout)
        if resp.status_code != 200:
            return ProviderResponse(hits=[], status=resp.status_code, error="http", last_timeout=last_timeout)
        try:
            data = resp.json()
        except ValueError:
            return ProviderResponse(hits=[], status=resp.status_code, error="parse", last_timeout=last_timeout)
        rows = ((data.get("query") or {}).get("search") or []) if isinstance(data, dict) else []
        hits: list[SearchHit] = []
        for i, row in enumerate(rows):
            if not isinstance(row, dict) or not row.get("title"):
                continue
            title = str(row["title"])[:200]
            page = wiki_page_url(title)
            if not page:
                continue
            hits.append(
                SearchHit(
                    title=title,
                    url=page,
                    snippet=_strip(str(row.get("snippet") or ""))[:280],
                    provider="wikipedia",
                    rank=i,
                    query=query,
                )
            )
        return ProviderResponse(hits=hits, status=200, error=None, last_timeout=last_timeout)
