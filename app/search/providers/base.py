from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx


_TRACKING_PARAMS = {
    "dclid",
    "fbclid",
    "gclid",
    "igshid",
    "mc_cid",
    "mc_eid",
    "msclkid",
    "ref_src",
}


def canonical_url(url: str) -> str:
    """Return a conservative identity key without common tracking parameters."""
    try:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower().rstrip(".")
        port = parts.port
    except (TypeError, ValueError):
        return ""
    scheme = parts.scheme.lower()
    if scheme not in {"http", "https"} or not host:
        return ""
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    default_port = (scheme == "http" and port == 80) or (scheme == "https" and port == 443)
    netloc = host if port is None or default_port else f"{host}:{port}"
    query = urlencode(
        [
            (name, value)
            for name, value in parse_qsl(parts.query, keep_blank_values=True)
            if not name.lower().startswith("utm_") and name.lower() not in _TRACKING_PARAMS
        ],
        doseq=True,
    )
    return urlunsplit((scheme, netloc, parts.path or "/", query, ""))


_CHALLENGE_MARKERS = (
    "captcha",
    "unusual traffic",
    "verify you are human",
    "are you a robot",
    "cf-chl",
    "challenge-platform",
    "/sorry/",
)


def empty_reason(body: str | None, result_marker: str) -> str:
    """Why a 200 HTML page yielded no hits.

    Only a page that looks like a genuine results page is "empty". A bot
    challenge is "blocked" and a results page the parser missed is "parse";
    both are provider failures, never "No results." for the model.
    """
    text = (body or "").lower()
    if any(marker in text for marker in _CHALLENGE_MARKERS):
        return "blocked"
    if result_marker and result_marker.lower() in text:
        return "parse"
    return "empty"


def source_id(url: str) -> str:
    """Stable public identifier shared by model evidence, traces, and UI."""
    import hashlib

    key = canonical_url(url) or str(url or "")
    return "web-" + hashlib.sha256(key.encode("utf-8", errors="replace")).hexdigest()[:12]


@dataclass
class SearchHit:
    title: str
    url: str
    snippet: str
    published: str | None = None
    provider: str = ""
    rank: int = 0
    query: str = ""
    providers: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.providers and self.provider:
            self.providers = (self.provider,)


@dataclass
class ProviderResponse:
    hits: list[SearchHit]
    status: int
    error: str | None = None
    last_timeout: httpx.Timeout | None = None


@dataclass
class QueryResult:
    query: str
    hits: list[SearchHit]
    error: str | None
    kind: str
    provider: str
    latency_ms: int
    cached: bool
    status: int
    last_timeout: httpx.Timeout | None = field(default=None, repr=False)


class SearchProvider(Protocol):
    name: str
    timeout: httpx.Timeout

    async def search(
        self,
        client: httpx.AsyncClient,
        query: str,
        *,
        recency_days: int | None,
        timeout: httpx.Timeout,
    ) -> ProviderResponse: ...
