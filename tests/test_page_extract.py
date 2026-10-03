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
            got.links,
            [
                {"url": "https://example.test/guide", "text": "the guide"},
                {"url": "https://other.test/report", "text": "external report"},
            ],
        )

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


if __name__ == "__main__":
    unittest.main()
