from __future__ import annotations

import asyncio
import logging
import time

import httpx

from ..client import TIMEOUT_BRAVE
from ..ssrf import public_http_url
from .base import ProviderResponse, SearchHit, read_json

logger = logging.getLogger(__name__)

_UA = "FuseChat/1.0 (dashboard search for signed-in users)"
_brave_auth_until = 0.0


def brave_auth_failed() -> bool:
    return time.monotonic() < _brave_auth_until


def mark_brave_auth_failed() -> None:
    global _brave_auth_until
    _brave_auth_until = time.monotonic() + 900


def freshness_bucket(recency_days: int | None) -> str | None:
    if recency_days is None:
        return None
    try:
        days = int(recency_days)
    except (TypeError, ValueError):
        return None
    if days <= 0:
        return None
    if days <= 1:
        return "pd"
    if days <= 7:
        return "pw"
    if days <= 31:
        return "pm"
    if days <= 365:
        return "py"
    return "py"


def _strip(text: str) -> str:
    import html as html_lib
    import re

    text = html_lib.unescape(re.sub(r"(?is)<[^>]+>", " ", text or ""))
    return re.sub(r"\s+", " ", text).strip()


class BraveProvider:
    name = "brave"
    timeout = TIMEOUT_BRAVE

    def __init__(self, key: str) -> None:
        self.key = key

    async def search(
        self,
        client: httpx.AsyncClient,
        query: str,
        *,
        recency_days: int | None,
        timeout: httpx.Timeout,
    ) -> ProviderResponse:
        params: dict[str, str | int] = {"q": query, "count": 6}
        bucket = freshness_bucket(recency_days)
        if bucket:
            params["freshness"] = bucket
        headers = {
            "X-Subscription-Token": self.key,
            "Accept": "application/json",
            "User-Agent": _UA,
        }
        last_timeout = timeout
        try:
            resp = await client.get(
                "https://api.search.brave.com/res/v1/web/search",
                params=params,
                headers=headers,
                timeout=timeout,
            )
        except httpx.TimeoutException:
            return ProviderResponse(hits=[], status=0, error="timeout", last_timeout=last_timeout)
        except httpx.HTTPError:
            return ProviderResponse(hits=[], status=0, error="http", last_timeout=last_timeout)

        if resp.status_code in (401, 403):
            mark_brave_auth_failed()
            logger.info("fuse.search provider=brave status=%s", resp.status_code)
            return ProviderResponse(hits=[], status=resp.status_code, error="auth", last_timeout=last_timeout)

        if resp.status_code == 429:
            wait = 0.2
            retry_after = resp.headers.get("Retry-After")
            if retry_after:
                try:
                    wait = min(1.0, float(retry_after))
                except ValueError:
                    wait = 0.2
            await asyncio.sleep(wait)
            try:
                resp = await client.get(
                    "https://api.search.brave.com/res/v1/web/search",
                    params=params,
                    headers=headers,
                    timeout=timeout,
                )
            except httpx.HTTPError:
                return ProviderResponse(hits=[], status=429, error="rate", last_timeout=last_timeout)
            if resp.status_code != 200:
                return ProviderResponse(hits=[], status=resp.status_code, error="rate", last_timeout=last_timeout)

        if resp.status_code != 200:
            return ProviderResponse(hits=[], status=resp.status_code, error="http", last_timeout=last_timeout)

        try:
            data = await read_json(resp)
        except ValueError:
            return ProviderResponse(hits=[], status=resp.status_code, error="parse", last_timeout=last_timeout)
        web = data.get("web") if isinstance(data, dict) else None
        rows = (web or {}).get("results") if isinstance(web, dict) else []
        hits: list[SearchHit] = []
        for i, row in enumerate(rows or []):
            if not isinstance(row, dict):
                continue
            url = public_http_url(str(row.get("url") or ""))
            title = _strip(str(row.get("title") or ""))
            if not url or not title:
                continue
            published = row.get("page_age") or row.get("age")
            hits.append(
                SearchHit(
                    title=title[:200],
                    url=url,
                    snippet=_strip(str(row.get("description") or ""))[:280],
                    published=str(published)[:40] if published else None,
                    provider="brave",
                    rank=i,
                    query=query,
                )
            )
            if len(hits) >= 6:
                break
        return ProviderResponse(hits=hits, status=200, error=None if hits else "empty", last_timeout=last_timeout)
