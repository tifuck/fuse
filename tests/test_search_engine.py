"""Search engine: providers, cache, recency, entity queries (httpx mocked)."""

from __future__ import annotations

import asyncio
import json
import os
import socket
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
_TMP = tempfile.mkdtemp(prefix="fuse-search-")
os.environ.setdefault("DATABASE_PATH", str(Path(_TMP) / "chat.db"))
os.environ.setdefault("APP_SECRET", "x" * 32)

from app.chat_tools import SCHEMAS, execute_tool, merge_queries
from app.search.client import TIMEOUT_BRAVE, TIMEOUT_DDG, TIMEOUT_FETCH, TIMEOUT_WIKI
from app.search.engine import (
    MAX_HITS_MERGED,
    cache_ttl,
    infer_recency,
    is_entity_query,
    search_bulk,
    sanitize_query,
)
from app.search.extract import FETCH_MAX_BYTES
from app.search.providers.brave import freshness_bucket
from app.search.providers.base import ProviderResponse, SearchHit, canonical_url

import pytest

# Uses the database: a disposable MariaDB schema shared by this module.
pytestmark = pytest.mark.usefixtures("isolated_db")


class FakeResp:
    def __init__(self, status=200, payload=None, text="", headers=None):
        self.status_code = status
        self._payload = payload
        # httpx Response.json() decodes .text; mirror that so read_json
        # (read_limited -> .text) sees the same bytes the provider parses.
        if not text and payload is not None:
            import json as _json

            text = _json.dumps(payload)
        self.text = text
        self.headers = headers or {}
        self.request = None

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class FakeStream:
    def __init__(self, status=200, body=b"", headers=None):
        self.status_code = status
        self.headers = headers or {"content-type": "text/html"}
        self._body = body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def aiter_bytes(self, chunk_size=8192):
        data = self._body
        for i in range(0, len(data), chunk_size):
            yield data[i : i + chunk_size]


class FakeClient:
    def __init__(self):
        self.calls = []
        self.get_impl = None
        self.request_impl = None
        self.stream_impl = None
        self.is_closed = False

    async def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        if self.get_impl is None:
            return FakeResp(200, text="")
        return await self.get_impl(url, kwargs)

    async def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        impl = self.request_impl
        if impl is None:
            return FakeResp(503, text="")
        return await impl(method, url, **kwargs)

    def stream(self, method, url, **kwargs):
        if self.stream_impl is None:
            raise AttributeError("stream")
        self.calls.append((method, url, kwargs))
        return self.stream_impl(method, url, kwargs)


class SsrfTest(unittest.TestCase):
    def test_public_ips_rejects_mixed_public_private_dns(self):
        from app.search.ssrf import public_ips

        answers = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443)),
        ]
        with patch("app.search.ssrf.socket.getaddrinfo", return_value=answers):
            self.assertEqual(public_ips("example.com"), [])


class EntityQueryTest(unittest.TestCase):
    def test_table(self):
        self.assertTrue(is_entity_query("Ada Lovelace"))
        self.assertTrue(is_entity_query("Tavily"))
        self.assertTrue(is_entity_query("GLM-5.3"))
        self.assertFalse(is_entity_query("how to install rust"))
        self.assertFalse(is_entity_query("site:arxiv.org grok"))
        self.assertFalse(is_entity_query("compare glm and grok"))
        self.assertFalse(is_entity_query("latest news today"))


LITE_HTML = """
<html><body>
<a rel="nofollow" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fcoinmarketcap.com%2Fcurrencies%2Fbitcoin%2F&amp;rut=abc" class='result-link'>Bitcoin price today</a>
<td class='result-snippet'>The live Bitcoin price is 79172 USD</td>
<a rel="nofollow" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fwww.coingecko.com%2Fen%2Fcoins%2Fbitcoin" class='result-link'>Bitcoin price, BTC price index</a>
<td class='result-snippet'>Bitcoin (BTC) price</td>
</body></html>
"""


BING_HTML = """
<li class="b_algo"><h2><a href="https://www.bing.com/ck/a?!&amp;u=a1aHR0cHM6Ly9naXRodWIuY29tL3VsYWI=">LLMRouter GitHub</a></h2>
<cite>https://github.com/ulab-uiuc/LLMRouter</cite>
<div class="b_caption"><p class="b_lineclamp2">An open-source library for LLM routing</p></div></li>
<li class="b_algo"><h2><a href="https://www.bing.com/ck/a?!&amp;u=a1aHR0cHM6Ly9vcGVucm91dGVyLmFpLw">OpenRouter</a></h2>
<cite>https://openrouter.ai</cite>
<p class="b_lineclamp2">A unified interface for LLMs</p></li>
"""


class MergeHitsTest(unittest.TestCase):
    def test_round_robin_dedup(self):
        from app.search.engine import _merge_hits
        from app.search.providers.base import SearchHit

        a = [
            SearchHit("A1", "https://a.example/1", "x", provider="ddg"),
            SearchHit("A2", "https://a.example/2", "x", provider="ddg"),
        ]
        b = [
            SearchHit("B1", "https://a.example/1", "dup", provider="bing"),
            SearchHit("B2", "https://b.example/2", "y", provider="bing"),
        ]
        out = _merge_hits([a, b], "q")
        urls = [h.url for h in out]
        self.assertEqual(urls[0], "https://a.example/1")
        self.assertEqual(set(urls), {"https://a.example/1", "https://a.example/2", "https://b.example/2"})
        self.assertEqual(len(out), 3)

    def test_canonical_dedup_fusion_provenance_diversity_and_cap(self):
        from app.search.engine import _merge_hits

        first = [
            SearchHit(f"A{i}", f"https://same.example/{i}?utm_source=x", "", provider="ddg", rank=i)
            for i in range(10)
        ]
        second = [
            SearchHit("Corroborated", "https://same.example/0?fbclid=abc", "better", provider="bing", rank=0),
            SearchHit("Other", "https://other.example/result#section", "other", provider="bing", rank=1),
        ]
        third = [
            SearchHit(f"D{i}", f"https://domain{i}.example/result", "x", provider="yahoo", rank=i)
            for i in range(8)
        ]

        out = _merge_hits([first, second, third], "query")

        self.assertEqual(len(out), MAX_HITS_MERGED)
        fused = next(hit for hit in out if canonical_url(hit.url) == "https://same.example/0")
        self.assertEqual(fused.providers, ("ddg", "bing"))
        self.assertEqual(fused.snippet, "better")
        self.assertIn("https://other.example/result", [canonical_url(hit.url) for hit in out])
        self.assertLessEqual(sum("same.example" in hit.url for hit in out), 2)

    def test_same_provider_variants_do_not_fake_corroboration(self):
        from app.search.engine import _merge_hits

        repeated = [
            SearchHit("Repeated", "https://one.example/item?utm_source=a", "", provider="ddg", rank=0),
            SearchHit("Repeated", "https://one.example/item?utm_source=b", "", provider="ddg", rank=1),
        ]
        corroborated = [
            SearchHit("Shared", "https://two.example/item", "", provider="ddg", rank=1),
            SearchHit("Shared", "https://two.example/item?fbclid=x", "", provider="bing", rank=1),
        ]

        out = _merge_hits([repeated, corroborated], "query")

        self.assertEqual(canonical_url(out[0].url), "https://two.example/item")
        self.assertEqual(out[0].providers, ("ddg", "bing"))

    def test_canonical_url_only_removes_known_tracking(self):
        url = "HTTPS://Example.COM:443/path?id=7&utm_campaign=x&gclid=y#part"
        self.assertEqual(canonical_url(url), "https://example.com/path?id=7")
        self.assertEqual(canonical_url("https://example.com/?ref=essential"), "https://example.com/?ref=essential")


class YahooParseTest(unittest.TestCase):
    def test_ru_and_title(self):
        from app.search.providers.yahoo import parse_yahoo_html

        html = """
        <div class="algo-sr relsrch"><a href="https://r.search.yahoo.com/x/RU=https%3a%2f%2fgithub.com%2fulab-uiuc%2fLLMRouter/RK=2/RS=abc">
        <h3 class="title"><span class="d-b fz-20">LLMRouter GitHub</span></h3></a>
        <p class="fc-dustygray fz-14">An open-source library</p></div>
        """
        hits = parse_yahoo_html(html)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].url, "https://github.com/ulab-uiuc/LLMRouter")
        self.assertIn("LLMRouter", hits[0].title)


class BingParseTest(unittest.TestCase):
    def test_bing_cite_and_base64(self):
        from app.search.providers.bing import parse_bing_html

        hits = parse_bing_html(BING_HTML)
        self.assertGreaterEqual(len(hits), 2)
        urls = {h.url for h in hits}
        self.assertTrue(any("github.com" in u for u in urls))
        self.assertTrue(any("openrouter.ai" in u for u in urls))


class DdgParseTest(unittest.TestCase):
    def test_lite_protocol_relative_uddg(self):
        from app.search.providers.ddg import parse_ddg_html

        hits = parse_ddg_html(LITE_HTML)
        self.assertGreaterEqual(len(hits), 1)
        self.assertEqual(hits[0].url, "https://coinmarketcap.com/currencies/bitcoin/")
        self.assertIn("79172", hits[0].snippet)


class PackLiveTest(unittest.TestCase):
    def test_live_quote_leads_pack(self):
        from app.search.pack import pack_search
        from app.search.providers.base import QueryResult, SearchHit

        packed = pack_search(
            [
                QueryResult(
                    query="btc",
                    hits=[
                        SearchHit("Wiki", "https://en.wikipedia.org/wiki/Bitcoin", "history", provider="wikipedia"),
                        SearchHit("LIVE", "https://www.coingecko.com/en/coins/bitcoin", "LIVE QUOTE: bitcoin = 100 USD (CoinGecko)", provider="quotes"),
                    ],
                    error=None,
                    kind="ok",
                    provider="quotes+wikipedia",
                    latency_ms=1,
                    cached=False,
                    status=200,
                )
            ]
        )
        self.assertIn("LIVE QUOTE", packed)
        self.assertLess(packed.find("LIVE QUOTE"), packed.find("wikipedia.org"))

    def test_pack_keeps_complete_source_records(self):
        from app.search.pack import pack_search
        from app.search.providers.base import QueryResult, SearchHit

        results = []
        for index in range(8):
            hits = [
                SearchHit(
                    f"Title {index}-{rank}",
                    f"https://example{rank}.com/{index}/" + "x" * 300,
                    "snippet " * 35,
                    provider="ddg",
                )
                for rank in range(8)
            ]
            results.append(QueryResult(f"query {index}", hits, None, "ok", "ddg", 1, False, 200))
        packed = pack_search(results)
        self.assertLessEqual(len(packed), 24_000)
        self.assertNotIn("[truncated]", packed)
        # Eight queries share the budget at four hits each; every hit shown
        # keeps its whole record (title, complete URL line, snippet).
        from app.search.pack import hits_per_query

        self.assertEqual(hits_per_query(8), 4)
        for index in range(8):
            for rank in range(8):
                url = f"https://example{rank}.com/{index}/" + "x" * 300
                shown = f"Title {index}-{rank}" in packed
                self.assertEqual(shown, rank < 4, (index, rank))
                if shown:
                    self.assertIn("\n   " + url + "\n", packed)
        # Internal source ids and engine provenance are not model evidence.
        self.assertNotIn("[web-", packed)
        self.assertNotIn("found via", packed)


class QuoteDetectTest(unittest.TestCase):
    def test_bitcoin_price_question(self):
        from app.search.providers.quotes import is_live_query, quote_ids

        self.assertEqual(quote_ids("What's the price of Bitcoin?")[0], "bitcoin")
        self.assertTrue(is_live_query("What's the price of Bitcoin?"))
        self.assertTrue(is_live_query("BTC USD price"))
        self.assertFalse(is_live_query("history of Ada Lovelace"))


class RecencyTest(unittest.TestCase):
    def test_buckets(self):
        self.assertIsNone(freshness_bucket(None))
        self.assertEqual(freshness_bucket(1), "pd")
        self.assertEqual(freshness_bucket(7), "pw")
        self.assertEqual(freshness_bucket(31), "pm")
        self.assertEqual(freshness_bucket(40), "py")

    def test_live_quote_heuristic(self):
        self.assertEqual(infer_recency("BTC USD price", None), 1)
        self.assertIsNone(infer_recency("Ada Lovelace", None))
        self.assertEqual(cache_ttl("pd", "ok"), 45.0)
        self.assertEqual(cache_ttl("pw", "ok"), 600.0)


class SchemaTest(unittest.TestCase):
    def test_schemas_avoid_anyof(self):
        # web_search takes query or queries; that cannot be "required" without
        # anyOf, which some providers reject, so neither keyword appears.
        params = SCHEMAS["web_search"]["function"]["parameters"]
        self.assertNotIn("anyOf", params)
        self.assertNotIn("required", params)
        # read_url has one mandatory argument and says so plainly.
        params = SCHEMAS["read_url"]["function"]["parameters"]
        self.assertNotIn("anyOf", params)
        self.assertEqual(params["required"], ["url"])

    def test_merge_queries(self):
        self.assertEqual(merge_queries({"query": "alpha", "queries": ["beta"]}), ["alpha", "beta"])
        self.assertEqual(merge_queries({"query": "alpha", "queries": ["beta", "alpha"]}), ["beta", "alpha"])
        self.assertEqual(merge_queries({}), [])
        self.assertEqual(sanitize_query("  x  "), "x")


class EngineAsyncTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from app.search.engine import _cache
        from app.search.providers import ddg as ddg_mod

        _cache.clear()
        ddg_mod._fail_streak = 0
        ddg_mod._break_until = 0.0

    async def test_brave_200(self):
        client = FakeClient()

        async def get(url, kwargs):
            return FakeResp(
                200,
                {
                    "web": {
                        "results": [
                            {
                                "title": "BTC",
                                "url": "https://example.com/btc",
                                "description": "65000",
                                "page_age": "1d",
                            }
                        ]
                    }
                },
            )

        client.get_impl = get
        with (
            patch("app.search.engine.get_client", return_value=client),
            patch("app.search.engine._brave_key", return_value="k"),
            patch("app.search.providers.brave.brave_auth_failed", return_value=False),
            patch("app.search.engine.brave_auth_failed", return_value=False),
        ):
            from app.search import cache as cache_mod

            cache_mod.TtlLru.clear  # noqa
            from app.search.engine import _cache

            _cache.clear()
            out = await search_bulk(["BTC USD price"], user_id=1, recency_days=1)
        self.assertEqual(out[0].kind, "ok")
        self.assertEqual(out[0].hits[0].url, "https://example.com/btc")
        brave_calls = [c for c in client.calls if "api.search.brave.com" in str(c[1])]
        self.assertTrue(brave_calls)
        self.assertEqual(brave_calls[0][2]["timeout"], TIMEOUT_BRAVE)
        self.assertEqual(brave_calls[0][2]["params"].get("freshness"), "pd")

    async def test_brave_429_is_error_not_empty(self):
        client = FakeClient()

        async def get(url, kwargs):
            return FakeResp(429, headers={"Retry-After": "0"})

        client.get_impl = get
        with (
            patch("app.search.engine.get_client", return_value=client),
            patch("app.search.engine._brave_key", return_value="k"),
            patch("app.search.engine.brave_auth_failed", return_value=False),
        ):
            from app.search.engine import _cache

            _cache.clear()
            out = await search_bulk(["news"], user_id=2)
        self.assertEqual(out[0].kind, "provider_error")
        self.assertIn("search failed", (out[0].error or "").lower())
        self.assertNotIn("provider", (out[0].error or "").lower())

    async def test_live_quote_uses_coingecko(self):
        client = FakeClient()

        async def get(url, kwargs):
            self.assertIn("coingecko.com", url)
            return FakeResp(200, {"bitcoin": {"usd": 79172}})

        client.get_impl = get
        with (
            patch("app.search.engine.get_client", return_value=client),
            patch("app.search.engine._brave_key", return_value=""),
        ):
            from app.search.engine import _cache

            _cache.clear()
            out = await search_bulk(["BTC USD price"], user_id=9, recency_days=1)
        self.assertEqual(out[0].kind, "ok")
        self.assertTrue(any("79172" in (h.snippet or "") for h in out[0].hits))
        self.assertIn("quotes", out[0].provider)

    async def test_ddg_202_still_parses_lite_html(self):
        client = FakeClient()

        async def req(method, url, **kwargs):
            return FakeResp(202, text=LITE_HTML)

        client.request_impl = req
        with (
            patch("app.search.engine.get_client", return_value=client),
            patch("app.search.engine._brave_key", return_value=""),
            patch("app.search.engine.quote_ids", return_value=None),
        ):
            from app.search.engine import _cache

            _cache.clear()
            out = await search_bulk(["open source router"], user_id=8)
        self.assertEqual(out[0].kind, "ok")
        self.assertTrue(out[0].hits)
        self.assertTrue(out[0].hits[0].url.startswith("https://"))

    async def test_zero_query_execute_error(self):
        text, trace = await execute_tool("web_search", "{}", user_id=1, allowed_urls=set())
        self.assertEqual(trace["status"], "error")
        self.assertIn("no valid query", text)

    async def test_sibling_timeout_does_not_cancel(self):
        client = FakeClient()
        n = {"i": 0}

        async def get(url, kwargs):
            n["i"] += 1
            if "slow" in str(kwargs.get("params", {}).get("q")):
                await asyncio.sleep(0.05)
                raise TimeoutError("nope")
            return FakeResp(
                200,
                {"web": {"results": [{"title": "Fast", "url": "https://example.com/f", "description": "ok"}]}},
            )

        async def wrapped(url, kwargs):
            try:
                return await get(url, kwargs)
            except TimeoutError:
                import httpx

                raise httpx.TimeoutException("t")

        client.get_impl = wrapped
        with (
            patch("app.search.engine.get_client", return_value=client),
            patch("app.search.engine._brave_key", return_value="k"),
            patch("app.search.engine.brave_auth_failed", return_value=False),
        ):
            from app.search.engine import _cache

            _cache.clear()
            out = await search_bulk(["fast query", "slow query"], user_id=3)
        kinds = {r.query: r.kind for r in out}
        self.assertEqual(kinds["fast query"], "ok")

    async def test_ddg_timeout_object(self):
        client = FakeClient()

        async def req(method, url, **kwargs):
            self.assertEqual(kwargs.get("timeout"), TIMEOUT_DDG)
            return FakeResp(202, text=LITE_HTML)

        client.request_impl = req
        with (
            patch("app.search.engine.get_client", return_value=client),
            patch("app.search.engine._brave_key", return_value=""),
        ):
            from app.search.engine import _cache

            _cache.clear()
            out = await search_bulk(["hello world"], user_id=4)
        self.assertTrue(out)
        self.assertEqual(out[0].kind, "ok")
        self.assertTrue(out[0].hits)
        ddg_timeouts = [c[2].get("timeout") for c in client.calls if "duckduckgo" in str(c[1])]
        self.assertIn(TIMEOUT_DDG, ddg_timeouts)

    async def test_soft_deadline_returns_hits_and_cancels_straggler(self):
        from app.search import engine

        cancelled = asyncio.Event()

        async def fast(*args, **kwargs):
            return ProviderResponse(
                hits=[SearchHit("Fast", "https://fast.example/result", "ok", provider="ddg")],
                status=200,
            )

        async def slow(*args, **kwargs):
            try:
                await asyncio.sleep(1)
            except asyncio.CancelledError:
                cancelled.set()
                raise

        empty = AsyncMock(return_value=ProviderResponse(hits=[], status=200, error="empty"))
        with (
            patch("app.search.engine._brave_key", return_value=""),
            patch.object(engine._ddg, "search", side_effect=fast),
            patch.object(engine._bing, "search", side_effect=slow),
            patch.object(engine._yahoo, "search", empty),
            patch.object(engine._hn, "search", empty),
            patch.object(engine._wiki, "search", empty),
            patch("app.search.engine.QUERY_SOFT_DEADLINE", 0.02),
            patch("app.search.engine.QUERY_DEADLINE", 0.2),
            patch("app.search.engine._allow", return_value=True),
        ):
            started = asyncio.get_running_loop().time()
            out = await search_bulk(["how to test orchestration"], user_id=10)
            elapsed = asyncio.get_running_loop().time() - started

        self.assertEqual(out[0].kind, "ok")
        self.assertLess(elapsed, 0.15)
        self.assertTrue(cancelled.is_set())

    async def test_no_hits_waits_until_hard_deadline(self):
        from app.search import engine

        async def slow(*args, **kwargs):
            await asyncio.sleep(1)

        with (
            patch("app.search.engine._brave_key", return_value=""),
            patch.object(engine._ddg, "search", side_effect=slow),
            patch.object(engine._bing, "search", side_effect=slow),
            patch.object(engine._yahoo, "search", side_effect=slow),
            patch.object(engine._hn, "search", side_effect=slow),
            patch.object(engine._wiki, "search", side_effect=slow),
            patch("app.search.engine.QUERY_SOFT_DEADLINE", 0.01),
            patch("app.search.engine.QUERY_DEADLINE", 0.04),
            patch("app.search.engine._allow", return_value=True),
        ):
            started = asyncio.get_running_loop().time()
            out = await search_bulk(["how to test hard deadline"], user_id=11)
            elapsed = asyncio.get_running_loop().time() - started

        self.assertEqual(out[0].kind, "provider_error")
        self.assertGreaterEqual(elapsed, 0.03)
        self.assertLess(elapsed, 0.2)

    async def test_fetch_90kb_not_too_large(self):
        from app.search.engine import _page_cache, fetch_url
        from app.search.ssrf import public_http_url

        url = "https://example.com/doc"
        article = "<article>" + ("<p>word </p>" * 200) + "</article>"
        html = "<html><body>" + article + "</body></html>"
        body = html.encode() + b" " * (90_000 - len(html.encode()))
        client = FakeClient()
        client.stream_impl = lambda method, u, kwargs=None, **kw: FakeStream(200, body, {"content-type": "text/html"})
        _page_cache.clear()
        with (
            patch("app.search.engine.get_fetch_client", return_value=client),
            patch("app.search.engine.public_ips", return_value=["93.184.216.34"]),
            patch("app.search.engine._allow", return_value=True),
        ):
            extract, wrapped, err = await fetch_url(url, user_id=1, allowed={url})
        self.assertNotIn("too large", wrapped.lower())
        self.assertTrue(extract is not None or "extract" in wrapped.lower() or "word" in wrapped)

    async def test_fetch_pins_resolved_ip_and_preserves_host_and_sni(self):
        from app.search.engine import _page_cache, fetch_url

        url = "https://example.com/doc"
        client = FakeClient()
        client.stream_impl = lambda method, u, kwargs=None, **kw: FakeStream(
            200, b"<article>Public document body with enough useful text to extract safely.</article>"
        )
        _page_cache.clear()
        with (
            patch("app.search.engine.get_fetch_client", return_value=client),
            patch("app.search.engine.public_ips", return_value=["93.184.216.34"]) as resolve,
            patch("app.search.engine._allow", return_value=True),
        ):
            extract, _, err = await fetch_url(url, user_id=1, allowed={url})
        self.assertIsNotNone(extract)
        self.assertFalse(err)
        resolve.assert_called_once_with("example.com", 443)
        _, target, kwargs = client.calls[0]
        self.assertEqual(target, "https://93.184.216.34:443/doc")
        self.assertEqual(kwargs["headers"]["Host"], "example.com")
        self.assertEqual(kwargs["extensions"], {"sni_hostname": "example.com"})

    async def test_fetch_dns_resolution_does_not_block_event_loop(self):
        from app.search.engine import _page_cache, fetch_url

        url = "https://example.com/threaded"
        client = FakeClient()
        client.stream_impl = lambda method, u, kwargs=None, **kw: FakeStream(
            200, b"<article>Threaded DNS resolution document body.</article>"
        )

        def slow_resolve(host, port):
            time.sleep(0.05)
            return ["93.184.216.34"]

        async def ticker():
            await asyncio.sleep(0.005)
            return True

        _page_cache.clear()
        with (
            patch("app.search.engine.get_fetch_client", return_value=client),
            patch("app.search.engine.public_ips", side_effect=slow_resolve),
            patch("app.search.engine._allow", return_value=True),
        ):
            (_, _, err), ticked = await asyncio.gather(
                fetch_url(url, user_id=1, allowed={url}), ticker()
            )
        self.assertFalse(err)
        self.assertTrue(ticked)

    async def test_fetch_cache_reuses_extract_for_find_without_budget(self):
        from app.search.engine import _page_cache, fetch_url, is_fetch_cached

        url = "https://example.com/cache"
        body = b"<article>Alpha opening. The cached needle is here. Omega ending.</article>"
        client = FakeClient()
        client.stream_impl = lambda method, u, kwargs=None, **kw: FakeStream(200, body)
        _page_cache.clear()
        allowed = {url}
        with (
            patch("app.search.engine.get_fetch_client", return_value=client),
            patch("app.search.engine.public_ips", return_value=["93.184.216.34"]),
            patch("app.search.engine._allow", side_effect=[True, False]) as permit,
        ):
            first, _, first_err = await fetch_url(url, user_id=1, allowed=allowed)
            self.assertTrue(is_fetch_cached(url, allowed))
            found, _, found_err = await fetch_url(url, user_id=1, allowed=allowed, find="cached needle")
        self.assertIsNotNone(first)
        self.assertFalse(first_err)
        self.assertIsNotNone(found)
        self.assertFalse(found_err)
        self.assertTrue(found.used_find)
        self.assertIn("cached needle", found.text)
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(permit.call_count, 1)

    async def test_fetch_cache_find_uses_document_beyond_opening_extract(self):
        from app.search.engine import _page_cache, fetch_url

        url = "https://example.com/long-cache"
        body = (
            "<article>"
            + ("<p>opening words in a cached paragraph.</p>" * 300)
            + "<p>deep-cache-marker appears in this final passage.</p></article>"
        ).encode()
        client = FakeClient()
        client.stream_impl = lambda method, u, kwargs=None, **kw: FakeStream(200, body)
        _page_cache.clear()
        with (
            patch("app.search.engine.get_fetch_client", return_value=client),
            patch("app.search.engine.public_ips", return_value=["93.184.216.34"]),
            patch("app.search.engine._allow", return_value=True),
        ):
            await fetch_url(url, user_id=1, allowed={url})
            found, _, err = await fetch_url(
                url, user_id=1, allowed={url}, find="deep-cache-marker"
            )
        self.assertFalse(err)
        self.assertIsNotNone(found)
        self.assertIn("deep-cache-marker", found.text)
        self.assertEqual(len(client.calls), 1)

    async def test_redirect_is_resolved_and_validated_separately(self):
        from app.search.engine import _page_cache, fetch_url

        source = "https://example.com/old"
        target = "https://docs.example.org/new"
        client = FakeClient()
        responses = iter(
            [
                FakeStream(302, headers={"location": target}),
                FakeStream(200, b"<article>Redirect destination public document text.</article>"),
            ]
        )
        client.stream_impl = lambda method, u, kwargs=None, **kw: next(responses)
        _page_cache.clear()
        with (
            patch("app.search.engine.get_fetch_client", return_value=client),
            patch(
                "app.search.engine.public_ips",
                side_effect=[["93.184.216.34"], ["1.1.1.1"]],
            ) as resolve,
            patch("app.search.engine._allow", return_value=True),
        ):
            extract, _, err = await fetch_url(source, user_id=1, allowed={source})
        self.assertIsNotNone(extract)
        self.assertFalse(err)
        self.assertEqual(
            resolve.call_args_list,
            [unittest.mock.call("example.com", 443), unittest.mock.call("docs.example.org", 443)],
        )
        self.assertEqual(client.calls[1][2]["headers"]["Host"], "docs.example.org")

    async def test_fetched_page_adds_only_safe_https_links(self):
        from app.search.engine import _page_cache, fetch_url

        url = "https://example.com/index"
        body = (
            b"<article>Links in a sufficiently useful page body. "
            b"<a href='/next'>Next</a><a href='https://localhost/private'>Private</a>"
            b"<a href='http://public.example/plain'>Plain</a></article>"
        )
        client = FakeClient()
        client.stream_impl = lambda method, u, kwargs=None, **kw: FakeStream(200, body)
        _page_cache.clear()
        allowed = {url}
        with (
            patch("app.search.engine.get_fetch_client", return_value=client),
            patch("app.search.engine.public_ips", return_value=["93.184.216.34"]),
            patch("app.search.engine._allow", return_value=True),
        ):
            extract, _, err = await fetch_url(url, user_id=1, allowed=allowed)
        self.assertIsNotNone(extract)
        self.assertFalse(err)
        self.assertIn("https://example.com/next", allowed)
        self.assertNotIn("https://localhost/private", allowed)
        self.assertNotIn("http://public.example/plain", allowed)

    async def test_safe_get_refuses_redirect_to_foreign_host(self):
        from app.search.providers.base import safe_get

        client = FakeClient()

        async def get(url, kwargs):
            if "good.example" in url:
                return FakeResp(302, headers={"location": "https://127.0.0.1/secret"})
            return FakeResp(200, text="ok")

        client.get_impl = get
        with self.assertRaises(ValueError):
            await safe_get(
                client,
                "https://good.example/search",
                host_ok=lambda u: u.startswith("https://good.example"),
            )
        self.assertFalse(any("127.0.0.1" in str(call[1]) for call in client.calls))

    async def test_dropped_bulk_queries_report_clip_not_failure(self):
        from app.search.engine import MAX_QUERIES_PER_BULK, search_bulk

        client = FakeClient()
        client.get_impl = AsyncMock(
            return_value=FakeResp(200, {"web": {"results": [{"title": "x", "url": "https://e.com"}]}})
        )
        queries = [f"query {i}" for i in range(MAX_QUERIES_PER_BULK + 2)]
        with (
            patch("app.search.engine.get_client", return_value=client),
            patch("app.search.engine._brave_key", return_value="k"),
            patch("app.search.engine.brave_auth_failed", return_value=False),
            patch("app.search.engine._allow", return_value=True),
        ):
            from app.search.engine import _cache

            _cache.clear()
            out = await search_bulk(queries, user_id=77)
        dropped = [r for r in out if "dropped" in (r.error or "")]
        self.assertEqual(len(dropped), 2)
        for r in dropped:
            self.assertNotIn("failed", (r.error or "").lower())

    async def test_quotes_empty_is_not_a_genuine_no_results(self):
        from app.search.providers.base import ProviderResponse

        client = FakeClient()

        async def quotes_none(*args, **kwargs):
            return ProviderResponse(hits=[], status=0, error="empty")

        async def empty(*args, **kwargs):
            return ProviderResponse(hits=[], status=200, error="empty")

        from app.search import engine

        with (
            patch("app.search.engine._brave_key", return_value=""),
            patch("app.search.engine._allow", return_value=True),
            patch("app.search.engine.quote_ids", return_value=("bitcoin", "usd")),
            patch("app.search.engine.is_live_query", return_value=True),
            patch.object(engine._quotes, "search", side_effect=quotes_none),
            patch.object(engine._ddg, "search", side_effect=empty),
            patch.object(engine._bing, "search", side_effect=empty),
            patch.object(engine._yahoo, "search", side_effect=empty),
            patch.object(engine._hn, "search", side_effect=empty),
            patch.object(engine._wiki, "search", side_effect=empty),
        ):
            from app.search.engine import _cache

            _cache.clear()
            out = await search_bulk(["btc price"], user_id=78)
        self.assertEqual(out[0].kind, "provider_error")



# Bing's current markup: the real target is base64 in an HTML-escaped
# "&amp;u=a1..." parameter and <cite> is only a breadcrumb.
REAL_BING_BLOCK = (
    '<li class="b_algo" data-id iid=SERP.5349><div class="b_tpcn"><a class="tilk" '
    'href="https://www.bing.com/ck/a?!&amp;&amp;p=abc&amp;u=a1aHR0cHM6Ly93d3cuZ2Vla3Nmb3JnZWVrcy5vcmcvcHl0aG9uL3B5dGhvbi10YXNrZ3JvdXBzLXdpdGgtYXN5bmNpby8&amp;ntb=1">'
    '<cite>https://www.geeksforgeeks.org &#8250; python &#8250; python-taskgroups-with-asyncio</cite></a></div>'
    '<h2><a href="https://www.bing.com/ck/a?!&amp;&amp;p=abc&amp;u=a1aHR0cHM6Ly93d3cuZ2Vla3Nmb3JnZWVrcy5vcmcvcHl0aG9uL3B5dGhvbi10YXNrZ3JvdXBzLXdpdGgtYXN5bmNpby8&amp;ntb=1">'
    'Python Taskgroups with asyncIO - GeeksforGeeks</a></h2><p class="b_lineclamp2">Jul 23, 2025 · Prerequisite</p></li>'
)


class SearchQualityTest(unittest.TestCase):
    def test_bing_result_url_is_the_page_not_the_breadcrumb_host(self):
        from app.search.providers.bing import parse_bing_html

        hits = parse_bing_html(REAL_BING_BLOCK)
        self.assertEqual(hits[0].url, "https://www.geeksforgeeks.org/python/python-taskgroups-with-asyncio/")

    def test_off_topic_hits_sink_and_drop_when_enough_hits_are_on_topic(self):
        from app.search.engine import _merge_hits

        degraded = [
            SearchHit("WON Definition & Meaning", "https://dict.example/won", "The meaning of won", provider="bing", rank=0),
            SearchHit("South Korean won", "https://wiki.example/won", "Currency of South Korea", provider="bing", rank=1),
        ]
        good = [
            SearchHit(f"2026 FIFA World Cup final {i}", f"https://site{i}.example/world-cup-2026", "Spain won the 2026 World Cup final", provider="yahoo", rank=i)
            for i in range(5)
        ]
        out = _merge_hits([degraded, good], "who won the 2026 world cup")
        urls = [h.url for h in out]
        self.assertNotIn("https://dict.example/won", urls)
        self.assertNotIn("https://wiki.example/won", urls)
        self.assertEqual(len(urls), 5)

    def test_coverage_only_reorders_when_too_few_hits_are_on_topic(self):
        from app.search.engine import _merge_hits

        first = [SearchHit("Unrelated page", "https://a.example/x", "nothing", provider="bing", rank=0)]
        second = [SearchHit("Rust reqwest timeout example", "https://b.example/reqwest-timeout", "", provider="yahoo", rank=3)]
        out = _merge_hits([first, second], "rust reqwest timeout example")
        self.assertEqual([h.url for h in out], ["https://b.example/reqwest-timeout", "https://a.example/x"])

    def test_adapter_plans(self):
        from app.search.adapters import plan_for

        blob = plan_for("https://github.com/encode/httpx/blob/master/httpx/_client.py")
        self.assertEqual(blob.target, "https://raw.githubusercontent.com/encode/httpx/master/httpx/_client.py")
        self.assertIsNone(plan_for("https://github.com/encode/httpx"))
        so = plan_for("https://stackoverflow.com/questions/76196208/rust-request-with-timeout")
        self.assertEqual((so.kind, so.site, so.ident), ("stackexchange", "stackoverflow", "76196208"))
        self.assertEqual(plan_for("https://unix.stackexchange.com/questions/42/x").site, "unix")
        npm = plan_for("https://www.npmjs.com/package/@scope/pkg")
        self.assertEqual((npm.kind, npm.ident, npm.version), ("npm", "@scope/pkg", "latest"))
        self.assertIsNone(plan_for("https://example.com/questions/1"))

    def test_pack_search_shows_excerpts_and_drops_them_before_whole_queries(self):
        from app.search.pack import RESULT_CHARS, pack_search
        from app.search.providers.base import QueryResult

        hit = SearchHit("Docs", "https://docs.example/a", "snippet", provider="yahoo", excerpt="The answer paragraph.")
        packed = pack_search([QueryResult("q", [hit], None, "ok", "yahoo", 1, False, 200)])
        self.assertIn("[page excerpt, read from the page]\nThe answer paragraph.\n   [/page excerpt]", packed)
        self.assertIn("1 top result page(s) were read", packed)
        huge = [
            QueryResult(f"query {i}", [SearchHit(f"T{i}-{r}", f"https://e{r}.example/{i}", "s" * 290, provider="yahoo", excerpt="E" * 3000) for r in range(4)], None, "ok", "yahoo", 1, False, 200)
            for i in range(6)
        ]
        packed = pack_search(huge)
        self.assertLessEqual(len(packed), RESULT_CHARS)
        for i in range(6):
            self.assertIn(f"## Q{i + 1}: query {i}", packed)

    def test_pack_page_has_outline_and_only_this_windows_links(self):
        from app.search.extract import FETCH_CHARS, extract_page
        from app.search.pack import pack_page

        body = (
            "<article><h1>Guide</h1><p>See <a href='/early'>early link</a>.</p>"
            + "".join(f"<h2>Part {i}</h2><p>{'filler words ' * 400}</p>" for i in range(8))
            + "<p>Finally <a href='/late'>late link</a>.</p></article>"
        )
        first = extract_page(body, content_type="text/html", url="https://docs.example/guide")
        self.assertGreater(first.total_chars, FETCH_CHARS)
        packed = pack_page("https://docs.example/guide", first)
        self.assertIn("Page outline (heading, offset to read it with offset=N):", packed)
        self.assertIn("- Part 7 @", packed)
        self.assertIn("- early link: https://docs.example/early", packed)
        self.assertNotIn("https://docs.example/late", packed)
        late_at = int(packed.split("- Part 7 @", 1)[1].split()[0])
        later = extract_page(body, content_type="text/html", url="https://docs.example/guide", offset=late_at)
        self.assertIn("- late link: https://docs.example/late", pack_page("https://docs.example/guide", later))


def _fetch_patches(client):
    return (
        patch("app.search.engine.get_fetch_client", return_value=client),
        patch("app.search.engine.public_ips", return_value=["93.184.216.34"]),
        patch("app.search.engine._allow", return_value=True),
    )


def _logical_url(target, kwargs):
    from urllib.parse import urlparse, urlunparse

    parsed = urlparse(target)
    return urlunparse(("https", kwargs["headers"]["Host"], parsed.path, "", parsed.query, ""))


class FetchUpgradeTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from app.search import engine

        engine._page_cache.clear()
        engine._blocked_hosts.clear()

    async def _fetch(self, client, url, **kw):
        from app.search.engine import fetch_url

        a, b, c = _fetch_patches(client)
        with a, b, c:
            return await fetch_url(url, user_id=1, allowed=set(), **kw)

    async def test_asks_for_markdown_and_gzip_and_reads_markdown(self):
        client = FakeClient()
        md = b"# Models\n\n| Model | Price |\n| --- | --- |\n| grok-4.7 | $2 |\n\n## Limits\n\nRate limits apply."
        client.stream_impl = lambda method, u, kwargs: FakeStream(200, md, {"content-type": "text/markdown; charset=utf-8"})
        extract, wrapped, err = await self._fetch(client, "https://docs.example/models")
        self.assertFalse(err)
        headers = client.calls[0][2]["headers"]
        self.assertTrue(headers["Accept"].startswith("text/markdown"))
        self.assertIn("gzip", headers["Accept-Encoding"])
        self.assertIn("| grok-4.7 | $2 |", wrapped)
        self.assertIn("length: ", wrapped)
        self.assertEqual([t for _, _, t in extract.outline], ["Models", "Limits"])

    async def test_octet_stream_pdf_is_sniffed_and_extracted(self):
        client = FakeClient()
        client.stream_impl = lambda method, u, kwargs: FakeStream(200, b"%PDF-1.7 fake", {"content-type": "application/octet-stream"})
        with patch("app.search.engine._pdf_text", AsyncMock(return_value="Manual text for the 811LM remote.")):
            extract, wrapped, err = await self._fetch(client, "https://files.example/manual")
        self.assertFalse(err)
        self.assertIn("811LM remote", wrapped)

    async def test_office_files_and_untyped_binaries_are_refused_early(self):
        consumed = []

        class CountingStream(FakeStream):
            async def aiter_bytes(self, chunk_size=8192):
                for i in range(50):
                    consumed.append(i)
                    yield b"PK\x03\x04\x00\x00" + b"\x00" * 65530

        client = FakeClient()
        docx = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        client.stream_impl = lambda method, u, kwargs: FakeStream(200, b"PK\x03\x04", {"content-type": docx})
        _, _, err = await self._fetch(client, "https://files.example/report.docx")
        self.assertIn("not readable text", err)
        client.stream_impl = lambda method, u, kwargs: CountingStream(200, b"", {"content-type": "application/octet-stream"})
        _, _, err = await self._fetch(client, "https://files.example/archive")
        self.assertIn("binary file", err)
        self.assertEqual(consumed, [0])

    async def test_timeouts_and_bot_walls_get_clear_messages(self):
        import httpx

        from app.search.engine import is_host_blocked

        client = FakeClient()

        def slow(method, u, kwargs):
            raise httpx.ReadTimeout("slow")

        client.stream_impl = slow
        _, _, err = await self._fetch(client, "https://slow.example/page")
        self.assertIn("timed out", err)
        client.stream_impl = lambda method, u, kwargs: FakeStream(403, b"denied", {"content-type": "text/html"})
        _, _, err = await self._fetch(client, "https://walled.example/page")
        self.assertIn("HTTP 403", err)
        self.assertIn("blocks automated readers", err)
        self.assertNotIn("refused", err.lower())
        self.assertTrue(is_host_blocked("https://walled.example/other"))

    async def test_stackexchange_question_read_through_the_api(self):
        import json as _json

        question = {"items": [{"title": "Rust request with timeout", "score": 3, "answer_count": 2, "tags": ["rust"],
                               "accepted_answer_id": 9, "body": "<p>How do I set a timeout?</p>"}]}
        answers = {"items": [{"answer_id": 8, "score": 1, "body": "<p>Low answer.</p>"},
                             {"answer_id": 9, "score": 5, "body": "<pre><code>client.timeout(Duration::from_secs(5))</code></pre>"}]}
        client = FakeClient()

        def serve(method, u, kwargs):
            url = _logical_url(u, kwargs)
            if url.startswith("https://api.stackexchange.com/2.3/questions/76196208/answers"):
                return FakeStream(200, _json.dumps(answers).encode(), {"content-type": "application/json"})
            if url.startswith("https://api.stackexchange.com/2.3/questions/76196208"):
                return FakeStream(200, _json.dumps(question).encode(), {"content-type": "application/json"})
            return FakeStream(403, b"blocked", {"content-type": "text/html"})

        client.stream_impl = serve
        extract, wrapped, err = await self._fetch(client, "https://stackoverflow.com/questions/76196208/rust-request-with-timeout")
        self.assertFalse(err)
        self.assertIn("How do I set a timeout?", wrapped)
        self.assertLess(wrapped.find("## Accepted answer (score 5)"), wrapped.find("## Answer (score 1)"))
        self.assertIn("client.timeout(Duration::from_secs(5))", wrapped)
        self.assertFalse(any("stackoverflow.com" in _logical_url(c[1], c[2]) for c in client.calls))

    async def test_failed_adapter_falls_back_to_the_page(self):
        client = FakeClient()

        def serve(method, u, kwargs):
            url = _logical_url(u, kwargs)
            if "raw.githubusercontent.com" in url:
                return FakeStream(404, b"", {"content-type": "text/plain"})
            return FakeStream(200, b"<article>" + b"GitHub rendered file view text. " * 20 + b"</article>")

        client.stream_impl = serve
        extract, wrapped, err = await self._fetch(client, "https://github.com/o/r/blob/main/README.md")
        self.assertFalse(err)
        self.assertIn("GitHub rendered file view text", wrapped)


class ExcerptTest(unittest.IsolatedAsyncioTestCase):
    def _results(self):
        from app.search.providers.base import QueryResult

        return [
            QueryResult("alpha pricing", [SearchHit(f"A{r}", f"https://a{r}.example/p", "s", provider="yahoo") for r in range(3)], None, "ok", "yahoo", 1, False, 200),
            QueryResult("beta limits", [SearchHit(f"B{r}", f"https://b{r}.example/p", "s", provider="yahoo") for r in range(3)], None, "ok", "yahoo", 1, False, 200),
        ]

    async def test_reads_best_hits_first_and_attaches_relevant_passages(self):
        from app.search import excerpts
        from app.search.extract import extract_page

        seen = []

        async def fake_fetch(url, **kwargs):
            seen.append((url, kwargs.get("rate_kind")))
            page = f"# Page {url}\n\nIntro words.\n\n## Pricing\n\nThe alpha pricing is $5.\n\n## Limits\n\nbeta limits are 10 rps."
            return extract_page(page, content_type="text/markdown"), "", ""

        with patch.object(excerpts, "fetch_url", fake_fetch):
            out = await excerpts.attach_excerpts(self._results(), user_id=1, max_pages=3)
        self.assertEqual([u for u, _ in seen], ["https://a0.example/p", "https://b0.example/p", "https://a1.example/p"])
        self.assertTrue(all(kind == "prefetch" for _, kind in seen))
        self.assertIn("alpha pricing is $5", out[0].hits[0].excerpt)
        self.assertIn("beta limits are 10 rps", out[1].hits[0].excerpt)
        self.assertEqual(out[1].hits[1].excerpt, "")

    async def test_slow_pages_miss_the_deadline_but_finish_in_background(self):
        from app.search import excerpts
        from app.search.extract import extract_page

        release = asyncio.Event()
        finished = []

        async def fake_fetch(url, **kwargs):
            if "a0" not in url:
                await release.wait()
                finished.append(url)
            return extract_page("# T\n\nalpha pricing words", content_type="text/markdown"), "", ""

        with patch.object(excerpts, "fetch_url", fake_fetch):
            out = await excerpts.attach_excerpts(self._results(), user_id=1, max_pages=2, deadline=0.05)
            self.assertTrue(out[0].hits[0].excerpt)
            self.assertEqual(out[1].hits[0].excerpt, "")
            release.set()
            for _ in range(20):
                if finished:
                    break
                await asyncio.sleep(0.01)
        self.assertEqual(finished, ["https://b0.example/p"])

    async def test_skips_blocked_hosts_pdfs_and_quotes(self):
        from app.search import engine, excerpts
        from app.search.providers.base import QueryResult

        engine._blocked_hosts.set("walled.example", True, 60)
        hits = [
            SearchHit("Q", "https://quotes.example/btc", "LIVE", provider="quotes"),
            SearchHit("W", "https://walled.example/x", "", provider="yahoo"),
            SearchHit("P", "https://files.example/paper.pdf", "", provider="yahoo"),
        ]
        picks = excerpts._pick([QueryResult("q", hits, None, "ok", "x", 1, False, 200)], 5)
        self.assertEqual(picks, [])
        engine._blocked_hosts.clear()

    async def test_web_search_tool_marks_read_hits(self):
        from app import chat_tools
        from app.search import excerpts
        from app.search.extract import extract_page
        from app.search.providers.base import QueryResult

        async def fake_bulk(queries, **kwargs):
            return [QueryResult(queries[0], [SearchHit("Doc", "https://doc.example/a", "s", provider="yahoo")], None, "ok", "yahoo", 1, False, 200)]

        async def fake_fetch(url, **kwargs):
            return extract_page("# Doc\n\nThe release date is October 23.", content_type="text/markdown"), "", ""

        with (
            patch.object(chat_tools, "search_bulk", fake_bulk),
            patch.object(excerpts, "fetch_url", fake_fetch),
            patch.object(chat_tools.settings, "web_search_excerpt_pages", 3),
        ):
            text, trace = await chat_tools.execute_tool(
                "web_search", json.dumps({"query": "release date"}), user_id=1, allowed_urls=set()
            )
        self.assertIn("The release date is October 23.", text)
        self.assertEqual(trace["results"][0]["status"], "read")


class ProviderTransportTest(unittest.IsolatedAsyncioTestCase):
    async def test_redirect_hops_do_not_resend_the_first_query_params(self):
        from app.search.providers.base import safe_get

        client = FakeClient()
        answers = iter([
            FakeResp(307, headers={"location": "/_bv/v.gif?orig=%2Fsearch%3Fp%3Dq"}),
            FakeResp(307, headers={"location": "/search?p=q"}),
            FakeResp(200, text="results"),
        ])

        async def get(url, kwargs):
            return next(answers)

        client.get_impl = get
        resp = await safe_get(client, "https://search.yahoo.com/search", host_ok=lambda u: "yahoo.com" in u, params={"p": "q"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(client.calls[0][2].get("params"), {"p": "q"})
        self.assertNotIn("params", client.calls[1][2])
        self.assertNotIn("params", client.calls[2][2])

    async def test_yahoo_retries_one_transient_server_error(self):
        from app.search.providers.yahoo import YahooProvider

        page = (
            '<div class="algo-sr"><a href="https://r.search.yahoo.com/x/RU=https%3a%2f%2fdocs.example%2fpage/RK=2">'
            '<h3 class="title">Docs page</h3></a><p class="fc-dustygray">About it</p></div>'
        )
        client = FakeClient()
        answers = iter([FakeResp(500, text="oops"), FakeResp(200, text=page)])

        async def get(url, kwargs):
            return next(answers)

        client.get_impl = get
        got = await YahooProvider().search(client, "docs", recency_days=None, timeout=YahooProvider.timeout)
        self.assertEqual([h.url for h in got.hits], ["https://docs.example/page"])
        self.assertEqual(len(client.calls), 2)

    async def test_ddg_challenge_pauses_the_provider(self):
        from app.search.providers import ddg as ddg_mod

        ddg_mod.reset_circuit()
        ddg_mod._warmed = True
        client = FakeClient()

        async def req(method, url, **kwargs):
            return FakeResp(202, text="<html>Unfortunately, bots use DuckDuckGo too. anomaly</html>")

        client.request_impl = req
        provider = ddg_mod.DdgProvider()
        first = await provider.search(client, "q", recency_days=None, timeout=TIMEOUT_DDG)
        self.assertEqual(first.error, "challenge")
        self.assertEqual(len(client.calls), 1)  # no second endpoint tried
        second = await provider.search(client, "q", recency_days=None, timeout=TIMEOUT_DDG)
        self.assertEqual(second.error, "circuit")
        self.assertEqual(len(client.calls), 1)
        ddg_mod.reset_circuit()


if __name__ == "__main__":
    unittest.main()



class MalformedPageInputTest(unittest.TestCase):
    def test_bad_charset_redirect_and_hostname_fail_cleanly(self):
        from app.search.engine import _decode, urljoin_safe
        from app.search.ssrf import public_ips

        self.assertEqual(_decode(b"hello", "text/html; charset=idna"), "hello")
        self.assertIsInstance(_decode(b"<meta charset=punycode>\xff hi", "text/html"), str)
        self.assertEqual(urljoin_safe("https://a.example/", "https://[bad"), "")
        self.assertEqual(public_ips("a" * 64 + ".example.com"), [])
