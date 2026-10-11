from __future__ import annotations

import http.cookiejar
import logging
import zlib

import httpx

logger = logging.getLogger(__name__)

CLIENT_DEFAULT_TIMEOUT = httpx.Timeout(10.0, connect=1.5)
TIMEOUT_BRAVE = httpx.Timeout(2.5, connect=1.5)
TIMEOUT_WIKI = httpx.Timeout(2.0, connect=1.5)
TIMEOUT_DDG = httpx.Timeout(5.0, connect=1.5)
TIMEOUT_FETCH = httpx.Timeout(6.0, connect=1.5)
PROVIDER_BODY_MAX = 262_144
# Hard ceiling on what one response may put in memory, decoded. Callers keep
# their own smaller caps; this one guards the paths that buffer (client.get)
# and compressed bodies, which httpx would otherwise inflate without limit.
TRANSPORT_BODY_MAX = 1_048_576

_search_client: httpx.AsyncClient | None = None
_fetch_client: httpx.AsyncClient | None = None


class _NoCookies(http.cookiejar.CookieJar):
    # httpx wraps whatever it is given in a fresh Cookies object, so only the
    # underlying jar's behaviour survives: never store, never send.
    def extract_cookies(self, response, request) -> None:
        return

    def set_cookie(self, cookie) -> None:
        return

    def add_cookie_header(self, request) -> None:
        return


class _CappedStream(httpx.AsyncByteStream):
    def __init__(self, inner, max_bytes: int, decoder, *, deflate: bool = False):
        self._inner = inner
        self._max = max_bytes
        self._decoder = decoder
        self._deflate = deflate

    async def __aiter__(self):
        sent = received = 0
        first = True
        async for chunk in self._inner:
            received += len(chunk)
            if self._decoder is not None and received > self._max:
                raise httpx.DecodingError("compressed response exceeds byte limit")
            if self._decoder is not None:
                try:
                    # max_length bounds the inflated output of this chunk.
                    chunk = self._decoder.decompress(chunk, self._max - sent + 1)
                except zlib.error as exc:
                    if not (first and self._deflate):
                        raise httpx.DecodingError("invalid compressed response") from exc
                    self._decoder = zlib.decompressobj(-zlib.MAX_WBITS)
                    try:
                        chunk = self._decoder.decompress(chunk, self._max - sent + 1)
                    except zlib.error as exc:
                        raise httpx.DecodingError("invalid deflate response") from exc
                first = False
            if chunk:
                chunk = chunk[: self._max - sent]
                sent += len(chunk)
                yield chunk
            if sent >= self._max or (self._decoder is not None and self._decoder.unconsumed_tail):
                return

    async def aclose(self) -> None:
        await self._inner.aclose()


class CappedTransport(httpx.AsyncBaseTransport):
    """Stops every response body at max_bytes and inflates gzip/deflate
    itself, bounded, so a decompression bomb never reaches httpx's decoder.
    Other encodings are refused."""

    def __init__(self, max_bytes: int = TRANSPORT_BODY_MAX, inner: httpx.AsyncBaseTransport | None = None):
        self._inner = inner or httpx.AsyncHTTPTransport()
        self._max = max_bytes

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        resp = await self._inner.handle_async_request(request)
        enc = resp.headers.get("content-encoding", "").strip().lower()
        if enc in ("", "identity"):
            decoder = None
        elif enc in ("gzip", "x-gzip", "deflate"):
            decoder = zlib.decompressobj(zlib.MAX_WBITS | 32)
        else:
            await resp.aclose()
            raise httpx.DecodingError(f"unsupported content-encoding {enc[:20]}", request=request)
        headers = [(k, v) for k, v in resp.headers.raw if k.lower() not in (b"content-encoding", b"content-length")]
        return httpx.Response(
            resp.status_code,
            headers=headers,
            stream=_CappedStream(resp.stream, self._max, decoder, deflate=enc == "deflate"),
            extensions=resp.extensions,
            request=request,
        )

    async def aclose(self) -> None:
        await self._inner.aclose()


def _new_client(*, cookies) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=CLIENT_DEFAULT_TIMEOUT,
        follow_redirects=False,
        http2=False,
        cookies=cookies,
        headers={"Accept-Encoding": "identity"},
        transport=CappedTransport(),
    )


def start_client() -> httpx.AsyncClient:
    global _search_client, _fetch_client
    if _search_client is None or _search_client.is_closed:
        _search_client = _new_client(cookies=httpx.Cookies())
    if _fetch_client is None or _fetch_client.is_closed:
        _fetch_client = _new_client(cookies=_NoCookies())
    return _search_client


async def stop_client() -> None:
    global _search_client, _fetch_client
    clients = [_search_client, _fetch_client]
    _search_client = None
    _fetch_client = None
    for client in clients:
        if client is not None and not client.is_closed:
            await client.aclose()


def get_client() -> httpx.AsyncClient:
    global _search_client
    if _search_client is None or _search_client.is_closed:
        _search_client = _new_client(cookies=httpx.Cookies())
    return _search_client


def get_fetch_client() -> httpx.AsyncClient:
    global _fetch_client
    if _fetch_client is None or _fetch_client.is_closed:
        _fetch_client = _new_client(cookies=_NoCookies())
    return _fetch_client


async def read_limited(resp, *, max_bytes: int = PROVIDER_BODY_MAX) -> str:
    aiter = getattr(resp, "aiter_bytes", None)
    if callable(aiter):
        buf = bytearray()
        async for chunk in resp.aiter_bytes(chunk_size=8192):
            room = max_bytes - len(buf)
            if room <= 0:
                break
            buf.extend(chunk[:room])
            if len(chunk) > room:
                break
        return bytes(buf).decode("utf-8", errors="replace")
    text = getattr(resp, "text", None)
    if isinstance(text, str):
        return text[:max_bytes]
    raw = getattr(resp, "content", b"") or b""
    if isinstance(raw, str):
        return raw[:max_bytes]
    return bytes(raw)[:max_bytes].decode("utf-8", errors="replace")


def decompress_limited(raw: bytes, *, max_plain: int) -> tuple[bytes, bool]:
    if not raw:
        return b"", False
    dec = zlib.decompressobj(16 + zlib.MAX_WBITS)
    try:
        out = dec.decompress(raw, max_plain + 1)
    except zlib.error:
        return raw[:max_plain], len(raw) > max_plain
    truncated = len(out) > max_plain or bool(dec.unconsumed_tail)
    return out[:max_plain], truncated
