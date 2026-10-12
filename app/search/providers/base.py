from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import httpx

from ..client import PROVIDER_BODY_MAX, read_limited


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


async def safe_get(
    client: httpx.AsyncClient,
    url: str,
    *,
    host_ok,
    max_redirects: int = 4,
    **kwargs,
) -> httpx.Response:
    """GET that follows redirects manually, validating every hop's host.

    Never pass follow_redirects=True to a shared client: httpx would then
    send the request wherever the server's Location points, with no SSRF
    check. `host_ok` decides whether a URL may be fetched at all.
    """
    current = url
    for _ in range(max_redirects + 1):
        if not host_ok(current):
            raise ValueError("redirect to a blocked or foreign host")
        resp = await client.get(current, follow_redirects=False, **kwargs)
        if resp.status_code in (301, 302, 303, 307, 308) and resp.headers.get("location"):
            current = urljoin(current, resp.headers["location"])
            # The Location already carries its own query string; re-sending
            # the first request's params appended them to every hop (Yahoo's
            # /_bv/ cookie beacon then looped until "redirect loop").
            kwargs.pop("params", None)
            continue
        return resp
    raise ValueError("redirect loop")


async def read_json(resp) -> object:
    """Bounded JSON body parse; caps the payload like HTML reads do."""
    import json

    text = await read_limited(resp, max_bytes=PROVIDER_BODY_MAX)
    return json.loads(text)


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
    # Query-relevant passage read from the page itself at search time.
    excerpt: str = ""

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
