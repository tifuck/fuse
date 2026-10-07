from __future__ import annotations

import asyncio
import re

import httpx

from ..ssrf import public_http_url
from .base import ProviderResponse, SearchHit, read_json

TIMEOUT_QUOTES = httpx.Timeout(2.0, connect=1.5)

_COINS = {
    "btc": "bitcoin",
    "bitcoin": "bitcoin",
    "eth": "ethereum",
    "ethereum": "ethereum",
    "sol": "solana",
    "solana": "solana",
    "xrp": "ripple",
    "ripple": "ripple",
    "doge": "dogecoin",
    "dogecoin": "dogecoin",
}
_BINANCE = {
    "bitcoin": "BTCUSDT",
    "ethereum": "ETHUSDT",
    "solana": "SOLUSDT",
    "ripple": "XRPUSDT",
    "dogecoin": "DOGEUSDT",
}
_FIAT = {"usd", "eur", "gbp"}
_WORD = re.compile(r"[a-z0-9]+")
_LIVE = re.compile(r"\b(price|quote|spot|ticker|how much)\b", re.I)


def quote_ids(query: str) -> tuple[str, str] | None:
    tokens = _WORD.findall((query or "").lower())
    if not tokens:
        return None
    coin = next((_COINS[t] for t in tokens if t in _COINS), None)
    if not coin:
        return None
    fiat = next((t for t in tokens if t in _FIAT), "usd")
    return coin, fiat


_HIST = re.compile(r"\b(history|historical|in \d{4}|as of|on \d{4})\b", re.I)


def is_live_query(query: str) -> bool:
    q = query or ""
    if _HIST.search(q):
        return bool(_LIVE.search(q))
    tokens = _WORD.findall(q.lower())
    coin = any(t in _COINS for t in tokens)
    fiat = any(t in _FIAT for t in tokens)
    if coin and (fiat or _LIVE.search(q)):
        return True
    return bool(_LIVE.search(q))


def _hit(title: str, url: str, snippet: str, rank: int, query: str) -> SearchHit:
    return SearchHit(
        title=title[:200],
        url=public_http_url(url) or url,
        snippet=snippet[:280],
        provider="quotes",
        rank=rank,
        query=query,
    )


async def _coingecko(client: httpx.AsyncClient, coin: str, fiat: str, timeout: httpx.Timeout, query: str) -> list[SearchHit]:
    resp = await client.get(
        "https://api.coingecko.com/api/v3/simple/price",
        params={"ids": coin, "vs_currencies": fiat, "include_last_updated_at": "true"},
        headers={"Accept": "application/json", "User-Agent": "FuseChat/1.0"},
        timeout=timeout,
    )
    if resp.status_code != 200:
        return []
    data = await read_json(resp)
    row = data.get(coin) if isinstance(data, dict) else None
    if not isinstance(row, dict) or fiat not in row:
        return []
    price = row.get(fiat)
    try:
        if price is None or float(price) <= 0:
            return []
    except (TypeError, ValueError):
        return []
    updated = row.get("last_updated_at")
    age = ""
    try:
        import time as _time

        if updated:
            secs = max(0, int(_time.time()) - int(updated))
            age = f" as of {secs}s ago"
    except (TypeError, ValueError):
        age = ""
    return [
        _hit(
            f"LIVE: {coin} ({fiat.upper()})",
            f"https://www.coingecko.com/en/coins/{coin}",
            f"LIVE QUOTE: {coin} = {price} {fiat.upper()} (CoinGecko{age})",
            0,
            query,
        )
    ]


async def _binance(client: httpx.AsyncClient, coin: str, fiat: str, timeout: httpx.Timeout, query: str) -> list[SearchHit]:
    if fiat != "usd":
        return []
    symbol = _BINANCE.get(coin)
    if not symbol:
        return []
    resp = await client.get(
        "https://api.binance.com/api/v3/ticker/price",
        params={"symbol": symbol},
        headers={"Accept": "application/json", "User-Agent": "FuseChat/1.0"},
        timeout=timeout,
    )
    if resp.status_code != 200:
        return []
    data = await read_json(resp)
    price = data.get("price") if isinstance(data, dict) else None
    if not price:
        return []
    return [
        _hit(
            f"LIVE: {symbol}",
            "https://www.binance.com/en/trade/" + symbol.replace("USDT", "_USDT"),
            f"LIVE QUOTE: {coin} = {price} USDT (Binance {symbol})",
            1,
            query,
        )
    ]


async def _coincap(client: httpx.AsyncClient, coin: str, fiat: str, timeout: httpx.Timeout, query: str) -> list[SearchHit]:
    if fiat != "usd":
        return []
    resp = await client.get(
        f"https://api.coincap.io/v2/assets/{coin}",
        headers={"Accept": "application/json", "User-Agent": "FuseChat/1.0"},
        timeout=timeout,
    )
    if resp.status_code != 200:
        return []
    data = await read_json(resp)
    row = data.get("data") if isinstance(data, dict) else None
    if not isinstance(row, dict) or not row.get("priceUsd"):
        return []
    price = row.get("priceUsd")
    try:
        price = f"{float(price):.2f}"
    except (TypeError, ValueError):
        pass
    return [
        _hit(
            f"LIVE: {coin} (CoinCap)",
            f"https://coincap.io/assets/{coin}",
            f"LIVE QUOTE: {coin} = {price} USD (CoinCap)",
            2,
            query,
        )
    ]


class QuotesProvider:
    name = "quotes"
    timeout = TIMEOUT_QUOTES

    async def search(
        self,
        client: httpx.AsyncClient,
        query: str,
        *,
        recency_days: int | None,
        timeout: httpx.Timeout,
    ) -> ProviderResponse:
        del recency_days
        ids = quote_ids(query)
        if not ids:
            return ProviderResponse(hits=[], status=0, error="skip", last_timeout=timeout)
        coin, fiat = ids

        async def one(fn):
            try:
                return await fn(client, coin, fiat, timeout, query)
            except Exception:
                return []

        gecko, bine, cap = await asyncio.gather(
            one(_coingecko),
            one(_binance),
            one(_coincap),
        )
        hits = gecko + bine + cap
        return ProviderResponse(
            hits=hits,
            status=200 if hits else 0,
            error=None if hits else "empty",
            last_timeout=timeout,
        )
