"""Main-content extraction (no network)."""

from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("APP_SECRET", "x" * 32)
os.environ.setdefault("DATABASE_PATH", str(Path("/tmp") / "fuse-extract-test.db"))

from app.search.extract import TABLE_MAX_ROWS, chunk_text, extract_page, find_windows, select_passages


class ExtractTest(unittest.TestCase):
    def test_skips_css_js_keeps_article(self):
        html = (
            "<html><head><style>" + ("body{color:red}" * 5000) + "</style>"
            "<script>var secretBundle='ignore me xyzzy';</script></head>"
            "<body><nav>Home About Cookie</nav>"
            "<article><h1>Release notes</h1><p>GLM-5.3 is out today with faster decode.</p></article>"
            "<footer>copyright</footer></body></html>"
        )
        got = extract_page(html, content_type="text/html")
        self.assertIn("GLM-5.3", got.text)
        self.assertNotIn("xyzzy", got.text)
        self.assertNotIn("color:red", got.text)
        self.assertNotEqual(got.kind, "empty")
        self.assertFalse(got.truncated)

    def test_nav_is_not_the_opening(self):
        nav = "Skip to content " * 80
        html = (
            f"<html><body><nav>{nav}</nav><header>Site chrome</header>"
            "<article><p>The actual changelog lives here uniquely.</p></article>"
            "<footer>footer links</footer></body></html>"
        )
        got = extract_page(html, content_type="text/html")
        self.assertIn("actual changelog", got.text)
        self.assertLess(got.text.find("actual changelog"), 200)

    def test_markdown_body_wins(self):
        html = (
            "<html><body><div class='sidebar'>ads ads ads</div>"
            "<div class='markdown-body'><p>GitHub readme body here.</p></div></body></html>"
        )
        got = extract_page(html, content_type="text/html")
        self.assertIn("GitHub readme", got.text)
        self.assertNotIn("ads ads", got.text)

    def test_json_ld_article(self):
        html = """<html><head>
        <script type="application/ld+json">
        {"@type":"NewsArticle","headline":"Hello","articleBody":"%s"}
        </script></head><body><p>chrome</p></body></html>""" % ("Important body text. " * 20)
        got = extract_page(html, content_type="text/html")
        self.assertIn("Important body text", got.text)

    def test_json_ld_article_body_is_cleaned_like_html(self):
        body = "<h2>Details</h2><p>Useful &amp; safe.</p><style>.secret{}</style>" + " More." * 40
        html = "<script type='application/ld+json'>" + json.dumps(
            {"@type": "Article", "headline": "Structured", "articleBody": body}
        ) + "</script>"
        got = extract_page(html, content_type="text/html")
        self.assertIn("## Details", got.text)
        self.assertIn("Useful & safe", got.text)
        self.assertNotIn("secret", got.text)

    def test_article_header_metadata_survives_site_header_filter(self):
        html = (
            "<html><body><header>Global navigation</header><article>"
            "<header><h1>Investigation</h1><p>By A. Reporter</p><time>14 September 2026</time></header>"
            "<p>Article evidence and analysis.</p></article></body></html>"
        )
        got = extract_page(html, content_type="text/html")
        self.assertIn("# Investigation", got.text)
        self.assertIn("By A. Reporter", got.text)
        self.assertIn("14 September 2026", got.text)
        self.assertNotIn("Global navigation", got.text)

    def test_script_text_absent(self):
        html = "<html><body><script>var x='ignore me'</script><article><p>Visible only.</p></article></body></html>"
        got = extract_page(html, content_type="text/html")
        self.assertNotIn("ignore me", got.text)
        self.assertIn("Visible only", got.text)

    def test_find_literal(self):
        paras = "".join(f"<p>paragraph {i} filler text</p>" for i in range(40))
        html = f"<html><body><article>{paras}<p>token GLM-5.3 appears here</p></article></body></html>"
        got = extract_page(html, content_type="text/html", find="GLM-5.3")
        self.assertTrue(got.used_find)
        self.assertIn("GLM-5.3", got.text)

    def test_find_regex_is_literal(self):
        html = "<html><body><article><p>aaaaaaa</p></article></body></html>"
        got = extract_page(html, content_type="text/html", find="(a+)+")
        self.assertIn("No literal match", got.text)

    def test_spa_notice(self):
        html = '<html><head><meta property="og:description" content="A chart app"></head><body><div id="root"></div></body></html>'
        got = extract_page(html, content_type="text/html")
        self.assertEqual(got.kind, "spa")
        self.assertIn("JavaScript app", got.text)
        self.assertIn("A chart app", got.text)

    def test_json_body(self):
        got = extract_page('{"ok": true, "n": 1}', content_type="application/json")
        self.assertEqual(got.kind, "json")
        self.assertIn('"ok"', got.text)

    def test_find_windows_helper(self):
        text = "alpha " * 50 + "NEEDLE" + " omega" * 50
        out = find_windows(text, "NEEDLE")
        self.assertIn("NEEDLE", out)

    def test_collects_resolved_useful_outbound_links(self):
        html = (
            "<article><p>Read <a href='/guide#start'>the guide</a> and "
            "<a href='https://other.test/report'>external report</a>.</p>"
            "<a href='#local'>local section</a><a href='javascript:void(0)'>bad</a></article>"
        )
        got = extract_page(html, content_type="text/html", url="https://example.test/news/item")
        self.assertEqual(
            [{"url": link["url"], "text": link["text"]} for link in got.links],
            [
                {"url": "https://example.test/guide", "text": "the guide"},
                {"url": "https://other.test/report", "text": "external report"},
            ],
        )
        # Each link carries where its label sits in the extracted text, so a
        # read_url window can list the links that belong to it.
        for link in got.links:
            self.assertEqual(got.full_text[link["at"]:link["at"] + len(link["text"])], link["text"])

    def test_heading_aware_chunks_repeat_context_after_size_split(self):
        text = "# Release notes\n\n" + ("First paragraph about setup. " * 8) + "\n\nSecond paragraph has target details."
        chunks = chunk_text(text, max_chars=200)
        self.assertGreaterEqual(len(chunks), 2)
        self.assertTrue(all(chunk.startswith("# Release notes") for chunk in chunks))

    def test_lexical_query_selects_relevant_heading_and_passage(self):
        text = (
            "# Gardening\n\nTomatoes need sunlight and water.\n\n"
            "# Database recovery\n\nRestore the replica from a verified backup after corruption."
        )
        out = select_passages(text, query="how to restore database backup")
        self.assertIn("# Database recovery", out)
        self.assertIn("verified backup", out)
        self.assertNotIn("Tomatoes", out)

    def test_literal_selection_merges_duplicate_hits_in_one_passage(self):
        text = "# Topic\n\nneedle appears once and needle appears twice in the same paragraph."
        out = find_windows(text, "needle")
        self.assertEqual(out.count("# Topic"), 1)
        self.assertEqual(out.count("needle appears once"), 1)


class ParserRegressionTest(unittest.TestCase):
    def test_space_only_lines_collapse_like_blank_lines(self):
        # Layout padding between list items ("<li>\n   \n   1 file</li>")
        # must not survive as runs of whitespace-only lines.
        page = "<html><body><main><ul>" + "".join(
            f"<li>\n      \n      \n      v{i}\n      \n      \n      1 file\n      </li>" for i in range(40)
        ) + "</ul></main></body></html>"
        got = extract_page(page, content_type="text/html")
        self.assertNotRegex(got.text, r"\n[ \t]+\n")
        self.assertNotIn("\n\n\n", got.text)
        self.assertIn("v39", got.text)

    """Regressions found by the real-page fixtures in tests/fixtures/sites."""

    def test_void_link_in_body_does_not_swallow_document(self):
        # arxiv serves <link> without a closing tag in the body; it is a
        # void element and must not open a skip block (real-page case:
        # arxiv_abs.html lost its whole abstract to this).
        html = (
            "<html><head><title>Paper</title></head><body>"
            '<link rel="stylesheet" href="/x.css">'
            "<blockquote class='abstract'>The dominant sequence transduction "
            "models are based on attention and dispense entirely with "
            "recurrence and convolution.</blockquote></body></html>"
        )
        got = extract_page(html, content_type="text/html")
        self.assertIn("dominant sequence transduction", got.text)

    def test_unclosed_elements_still_extract(self):
        # Truncated downloads (FETCH_MAX_BYTES cut) and malformed pages
        # never close their elements; text must not be lost to the SPA
        # fallback (real-page cases: arxiv_abs.html, wikipedia cut at 512KB).
        html = (
            "<html><head><title>Doc</title></head><body><div id='content'>"
            "<main><article><p>" + ("Meaningful article body. " * 30) + "</p>"
        )  # nothing is closed below <p>
        got = extract_page(html, content_type="text/html")
        self.assertEqual(got.kind, "html")
        self.assertIn("Meaningful article body", got.text)

    def test_nav_with_nested_lists_is_dropped(self):
        # Chrome text nested in <ul>/<li> used to leak through; only direct
        # text children of chrome tags were dropped (real-page case: the
        # 112-language list on wikipedia_ada_lovelace.html).
        html = (
            "<html><body><nav><ul>"
            "<li><a href='#'>English</a></li><li><a href='#'>Deutsch</a></li>"
            "<li><a href='#'>Français</a></li></ul></nav>"
            "<article><p>The actual encyclopaedia content stands alone here.</p></article>"
            "</body></html>"
        )
        got = extract_page(html, content_type="text/html")
        self.assertIn("encyclopaedia content", got.text)
        self.assertNotIn("Deutsch", got.text)
        self.assertNotIn("Français", got.text)

    def test_table_row_is_flattened_once(self):
        # The </tr> handler used to keep both the raw cell text and the
        # "a | b" flattened line, doubling every row (real-page case:
        # hackernews.html showed each item twice).
        html = (
            "<html><body><table>"
            "<tr><td>1.</td><td>TALA Is Open-Source</td><td>24 points</td></tr>"
            "<tr><td>2.</td><td>Watch Los Angeles get built</td><td>197 points</td></tr>"
            "</table></body></html>"
        )
        got = extract_page(html, content_type="text/html")
        self.assertIn("1. | TALA Is Open-Source | 24 points", got.text)
        self.assertEqual(got.text.count("TALA Is Open-Source"), 1)
        self.assertEqual(got.text.count("197 points"), 1)

    def test_table_row_cap_omits_excess_rows_without_raw_leak(self):
        rows = "".join(f"<tr><td>row-{i}</td><td>value-{i}</td></tr>" for i in range(TABLE_MAX_ROWS + 2))
        got = extract_page(f"<html><body><table>{rows}</table></body></html>", content_type="text/html")
        self.assertIn(f"row-{TABLE_MAX_ROWS - 1} | value-{TABLE_MAX_ROWS - 1}", got.text)
        self.assertNotIn(f"row-{TABLE_MAX_ROWS}", got.text)
        self.assertNotIn(f"value-{TABLE_MAX_ROWS + 1}", got.text)

    def test_table_row_cap_is_per_table(self):
        rows = "".join(f"<tr><td>first-{i}</td></tr>" for i in range(TABLE_MAX_ROWS))
        html = f"<table>{rows}</table><table><tr><td>second-table-row</td></tr></table>"
        got = extract_page(html, content_type="text/html")
        self.assertIn("second-table-row", got.text)

    def test_crossed_closing_tags_keep_content(self):
        html = "<html><body><article><div><p>Malformed but useful</div><p>Still present</article>"
        got = extract_page(html, content_type="text/html")
        self.assertIn("Malformed but useful", got.text)
        self.assertIn("Still present", got.text)

    def test_footer_subtree_dropped(self):
        html = (
            "<html><body><article><p>Solid main content lives here.</p></article>"
            "<footer><ul><li>Privacy policy</li><li>Cookie settings</li></ul></footer>"
            "</body></html>"
        )
        got = extract_page(html, content_type="text/html")
        self.assertIn("Solid main content", got.text)
        self.assertNotIn("Cookie settings", got.text)


class MarkdownRenderTest(unittest.TestCase):
    """HTML5 tree building and the markdown the model reads."""

    def test_unclosed_list_items_do_not_hide_the_article(self):
        # Minified Docusaurus pages omit </li>; the old tag stack never
        # re-synced and nested the whole article inside <nav> (Electron docs
        # extracted to "Skip to main content").
        html = (
            "<body><nav><ul><li><a href=/>Home<li><a href=/docs>Docs</ul></nav>"
            "<main><article><h1>Releases</h1><p>" + "Electron ships every eight weeks. " * 8
            + "<ul><li>alpha<li>beta<li>stable</ul></article></main></body>"
        )
        got = extract_page(html, content_type="text/html")
        self.assertIn("# Releases", got.text)
        self.assertIn("Electron ships every eight weeks", got.text)
        self.assertIn("- alpha\n- beta\n- stable", got.text)
        self.assertNotIn("Docs", got.text)

    def test_code_blocks_keep_indentation_fences_and_language(self):
        html = (
            "<article><p>" + "Example follows. " * 20 + "</p>"
            "<div class='highlight-python3'><div class='highlight'><pre>"
            "<span class='linenos'>1</span>async def main():\n"
            "    if ready:\n        await run()\n</pre></div></div>"
            "<p>Call <code>asyncio.run(main())</code> once.</p></article>"
        )
        got = extract_page(html, content_type="text/html")
        self.assertIn("```python3\nasync def main():\n    if ready:\n        await run()\n```", got.text)
        self.assertIn("Call `asyncio.run(main())` once.", got.text)

    def test_data_table_is_markdown_and_layout_table_is_not(self):
        html = (
            "<article><p>" + "Prices below. " * 20 + "</p><table><thead><tr><th>Model</th><th>Input</th></tr></thead>"
            "<tbody><tr><td>grok-4.7</td><td>$2 | cached $0.5</td></tr><tr><td>glm-5</td><td>$1</td></tr></tbody></table>"
            "<table><tr><td><b>Site</b> new | past</td></tr></table></article>"
        )
        got = extract_page(html, content_type="text/html")
        self.assertIn("| Model | Input |\n| --- | --- |\n| grok-4.7 | $2 \\| cached $0.5 |", got.text)
        self.assertIn("Site new | past", got.text)

    def test_table_row_cap_says_rows_were_omitted(self):
        rows = "".join(f"<tr><td>r{i}</td><td>v{i}</td></tr>" for i in range(TABLE_MAX_ROWS + 5))
        got = extract_page(f"<article><table>{rows}</table></article>", content_type="text/html")
        self.assertIn("(… 5 more table rows not shown)", got.text)

    def test_lists_number_and_nest(self):
        html = "<article><ol start=3><li>three<ul><li>inner</li></ul></li><li>four</li></ol>" + "<p>pad</p>" * 30 + "</article>"
        got = extract_page(html, content_type="text/html")
        self.assertIn("3. three\n   - inner\n4. four", got.text)

    def test_definition_lists_keep_term_and_description(self):
        html = "<article><dl><dt><code>callbackFn</code></dt><dd><p>A function to run.</p></dd></dl>" + "<p>x</p>" * 40 + "</article>"
        got = extract_page(html, content_type="text/html")
        self.assertIn("`callbackFn`\n: A function to run.", got.text)

    def test_citation_markers_hidden_text_and_screen_reader_chrome_dropped(self):
        html = (
            "<article><p>Spain won the final.<sup class='reference'><a href='#c1'>[3]</a></sup></p>"
            "<p hidden>secret hidden text</p><span class='sr-only'>Skip to content</span>"
            "<div style='display: none'>invisible</div>" + "<p>more body text here</p>" * 20 + "</article>"
        )
        got = extract_page(html, content_type="text/html")
        self.assertIn("Spain won the final.", got.text)
        self.assertNotIn("[3]", got.text)
        for junk in ("secret hidden text", "Skip to content", "invisible"):
            self.assertNotIn(junk, got.text)

    def test_listing_of_article_cards_keeps_the_list(self):
        cards = "".join(f"<article><h2>Story {i}</h2><p>Teaser for story number {i} here.</p></article>" for i in range(6))
        got = extract_page(f"<body><main>{cards}</main></body>", content_type="text/html")
        for i in range(6):
            self.assertIn(f"## Story {i}", got.text)

    def test_published_date_from_meta_and_json_ld(self):
        meta = "<head><meta property='article:published_time' content='2026-09-09T10:00:00Z'></head>"
        got = extract_page(f"<html>{meta}<body><article><p>{'Body. ' * 50}</p></article></body></html>")
        self.assertEqual(got.published, "2026-09-09")
        ld = json.dumps({"@type": "NewsArticle", "headline": "H", "datePublished": "2026-10-01"})
        got = extract_page(f"<html><head><script type='application/ld+json'>{ld}</script></head><body><article><p>{'Body. ' * 50}</p></article></body></html>")
        self.assertEqual(got.published, "2026-10-01")

    def test_outline_lists_headings_with_offsets_but_not_code_comments(self):
        text = "# Title\n\nintro\n\n```python\n# not a heading\n```\n\n## Install\n\nsteps"
        got = extract_page(text, content_type="text/markdown")
        self.assertEqual([(lvl, title) for _, lvl, title in got.outline], [(1, "Title"), (2, "Install")])
        install_at = got.outline[1][0]
        self.assertTrue(got.full_text[install_at:].startswith("## Install"))

    def test_plain_text_and_markdown_keep_their_lines(self):
        text = "line one\n    indented code\n\n\n\n\nlast line   \n"
        got = extract_page(text, content_type="text/plain")
        self.assertEqual(got.text, "line one\n    indented code\n\n\nlast line")

    def test_rss_feed_becomes_a_dated_list(self):
        feed = (
            "<?xml version='1.0'?><rss><channel><title>Release feed</title>"
            "<item><title>v2.0 released</title><link>https://example.test/v2</link>"
            "<pubDate>Mon, 12 Oct 2026 09:00:00 GMT</pubDate><description>&lt;p&gt;Big &amp;amp; fast&lt;/p&gt;</description></item>"
            "</channel></rss>"
        )
        got = extract_page(feed, content_type="application/rss+xml")
        self.assertIn("# Release feed", got.text)
        self.assertIn("- v2.0 released (Mon, 12 Oct 2026 09:00:00 GMT)", got.text)
        self.assertIn("https://example.test/v2", got.text)
        self.assertNotIn("<item>", got.text)

    def test_nested_junk_elements_are_removed_safely(self):
        # Freeing a nested match after its parent subtree must never happen.
        html = "<article><template><template><p>x</p></template></template><object><object>y</object></object>" + "<p>body</p>" * 50 + "</article>"
        got = extract_page(html, content_type="text/html")
        self.assertIn("body", got.text)
        self.assertNotIn("x\n", got.text[:3])

    def test_query_passages_carry_offsets_to_read_on(self):
        text = "# Setup\n\n" + "Install the package. " * 40 + "\n\n# Recovery\n\nRestore the replica from a verified backup."
        out = select_passages(text, query="restoring backups")
        self.assertIn("# Recovery", out)
        offset = int(out.split("[offset ", 1)[1].split("]", 1)[0])
        self.assertIn("Restore the replica", text[offset:offset + 200])

    def test_best_excerpt_prefers_relevant_passage_else_opening(self):
        from app.search.extract import best_excerpt

        text = "# Intro\n\nGeneral words about the product.\n\n# Pricing\n\nThe pro plan costs $20 per month."
        self.assertIn("$20 per month", best_excerpt(text, "pro plan price per month", 500))
        self.assertTrue(best_excerpt(text, "zzz unrelated", 40).startswith("# Intro"))


class HostileInputTest(unittest.TestCase):
    def test_unclosed_metadata_tags_parse_in_linear_time(self):
        import time

        for page in ("<title>" * 11000, "<meta a" * 11000, "<script type='application/ld+json'>" * 2200):
            started = time.monotonic()
            extract_page(page, content_type="text/html")
            self.assertLess(time.monotonic() - started, 1.0, page[:20])

    def test_find_hit_at_end_of_one_huge_paragraph_is_returned(self):
        text = "word " * 6000 + "NEEDLE-AT-END"
        got = extract_page(text, content_type="text/plain", find="NEEDLE-AT-END")
        self.assertIn("NEEDLE-AT-END", got.text)

    def test_find_phrase_split_by_passage_cut_is_returned_whole(self):
        from app.search.extract import PASSAGE_CHARS

        phrase = "restore the replica"
        text = "a" * (PASSAGE_CHARS - len(phrase) // 2) + phrase + " and then more words. " + ("tail " * 100)
        self.assertIn(phrase, text)
        out = find_windows(text, phrase)
        self.assertIn(phrase, out)
        self.assertNotIn("No literal match", out)
        for content_type in ("text/plain", "text/html"):
            raw = text if content_type == "text/plain" else f"<html><body><article><p>{text}</p></article></body></html>"
            got = extract_page(raw, content_type=content_type, url="https://example.test/a", find=phrase)
            self.assertIn(phrase, got.text, content_type)

    def test_find_phrase_cut_before_a_space_under_a_heading_keeps_the_space(self):
        from app.search.extract import PASSAGE_CHARS

        body = "a" * (PASSAGE_CHARS - 8) + " restore" + " the replica now"
        self.assertIn("restore the replica", find_windows("# Heading\n\n" + body + "\n", "restore the replica"))

    def test_empty_bing_cite_keeps_other_results(self):
        from app.search.providers.bing import parse_bing_html

        good = '<li class="b_algo"><h2><a href="https://good.test/a">Good</a></h2><cite>good.test</cite><p>ok</p></li>'
        bad = '<li class="b_algo"><h2><a href="https://bad.test/a">Bad</a></h2><cite> </cite><p>x</p></li>'
        hits = parse_bing_html(f"<ol id='b_results'>{bad}{good}</ol>")
        self.assertTrue(any("good.test" in h.url for h in hits), hits)


class PdfWorkerTest(unittest.IsolatedAsyncioTestCase):
    async def test_pdf_extracts_in_limited_child_and_rejects_invalid_input(self):
        import io
        from pypdf import PdfWriter
        from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
        from app.search.engine import _pdf_text

        writer = PdfWriter()
        page = writer.add_blank_page(width=300, height=300)
        font = DictionaryObject({NameObject("/Type"): NameObject("/Font"),
                                 NameObject("/Subtype"): NameObject("/Type1"),
                                 NameObject("/BaseFont"): NameObject("/Helvetica")})
        page[NameObject("/Resources")] = DictionaryObject({
            NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})})
        content = DecodedStreamObject()
        content.set_data(b"BT /F1 12 Tf 10 10 Td (PDF fixture) Tj ET")
        page[NameObject("/Contents")] = writer._add_object(content)
        buffer = io.BytesIO()
        writer.write(buffer)
        self.assertIn("PDF fixture", await _pdf_text(buffer.getvalue()))
        with self.assertRaises(ValueError):
            await _pdf_text(b"not a PDF")

    async def test_cancellation_kills_and_reaps_worker(self):
        import asyncio
        from unittest.mock import AsyncMock, Mock, patch
        from app.search.engine import _pdf_text

        started = asyncio.Event()
        async def communicate(_):
            started.set()
            await asyncio.Event().wait()
        proc = Mock(returncode=None, communicate=communicate, wait=AsyncMock())
        with patch("app.search.engine.asyncio.create_subprocess_exec", AsyncMock(return_value=proc)):
            task = asyncio.create_task(_pdf_text(b"pdf"))
            await started.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        proc.kill.assert_called_once()
        proc.wait.assert_awaited_once()


class CappedTransportTest(unittest.IsolatedAsyncioTestCase):
    async def test_gzip_bomb_is_inflated_only_to_the_cap(self):
        import gzip

        import httpx

        from app.search.client import CappedTransport

        bomb = gzip.compress(b"\0" * (64 * 1024 * 1024))

        class RawBody(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield bomb

        def handler(request):
            return httpx.Response(200, headers={"content-encoding": "gzip"}, stream=RawBody())

        transport = CappedTransport(100_000, inner=httpx.MockTransport(handler))
        async with httpx.AsyncClient(transport=transport) as client:
            resp = await client.get("https://bomb.test/")
        self.assertEqual(len(resp.content), 100_000)

    async def test_identity_body_is_capped_and_plain_bodies_pass(self):
        import httpx

        from app.search.client import CappedTransport

        def handler(request):
            return httpx.Response(200, content=b"x" * 500 if request.url.path == "/big" else b"hello")

        transport = CappedTransport(100, inner=httpx.MockTransport(handler))
        async with httpx.AsyncClient(transport=transport) as client:
            self.assertEqual(len((await client.get("https://t.test/big")).content), 100)
            self.assertEqual((await client.get("https://t.test/small")).content, b"hello")


if __name__ == "__main__":
    unittest.main()
