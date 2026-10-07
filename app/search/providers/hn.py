from __future__ import annotations

import logging

import httpx

from ..ssrf import public_http_url
from .base import ProviderResponse, SearchHit, read_json

logger = logging.getLogger(__name__)

TIMEOUT_HN = httpx.Timeout(2.5, connect=1.5)


class HnProvider:
    name = "hn"
    timeout = TIMEOUT_HN

    async def search(
        self,
        client: httpx.AsyncClient,
        query: str,
        *,
        recency_days: int | None,
        timeout: httpx.Timeout,
    ) -> ProviderResponse:
        params: dict[str, str | int] = {"query": query, "hitsPerPage": 6, "tags": "story"}
        if recency_days is not None and recency_days > 0:
            import time as _time

            params["numericFilters"] = f"created_at_i>{int(_time.time()) - recency_days * 86400}"
        try:
            resp = await client.get(
                "https://hn.algolia.com/api/v1/search",
                params=params,
                headers={"Accept": "application/json", "User-Agent": "FuseChat/1.0"},
                timeout=timeout,
            )
        except httpx.TimeoutException:
            logger.error("fuse.search provider=hn error=timeout")
            return ProviderResponse(hits=[], status=0, error="timeout", last_timeout=timeout)
        except httpx.HTTPError as exc:
            logger.error("fuse.search provider=hn error=%s", type(exc).__name__)
            return ProviderResponse(hits=[], status=0, error="http", last_timeout=timeout)
        if resp.status_code != 200:
            logger.error("fuse.search provider=hn status=%s", resp.status_code)
            return ProviderResponse(hits=[], status=resp.status_code, error="http", last_timeout=timeout)
        try:
            data = await read_json(resp)
        except ValueError:
            return ProviderResponse(hits=[], status=resp.status_code, error="parse", last_timeout=timeout)
        rows = data.get("hits") if isinstance(data, dict) else []
        hits: list[SearchHit] = []
        seen: set[str] = set()
        for i, row in enumerate(rows or []):
            if not isinstance(row, dict):
                continue
            title = str(row.get("title") or "").strip()
            url = public_http_url(str(row.get("url") or row.get("story_url") or ""))
            if not title or not url or url in seen:
                continue
            seen.add(url)
            snippet = str(row.get("story_text") or row.get("comment_text") or "")[:280]
            hits.append(
                SearchHit(title=title[:200], url=url, snippet=snippet, provider="hn", rank=i, query=query)
            )
            if len(hits) >= 6:
                break
        return ProviderResponse(hits=hits, status=200, error=None if hits else "empty", last_timeout=timeout)
