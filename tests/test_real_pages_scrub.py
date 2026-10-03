"""Scrubbing regression suite against real downloaded web pages.

Every fixture in tests/fixtures/sites/MANIFEST.json is a snapshot of a real
website (Wikipedia, GitHub, MDN, Hacker News, PyPI, Guardian, arXiv, python
docs, a JSON API). These tests pin the contract for read_url-style extraction:

  1. main content survives  (must_contain)
  2. page plumbing does not (must_not_contain + global markup/JS/CSS sweep)
  3. output stays small     (capped, big compression ratio) so pages do not
     fill the model context with code instead of information
"""

from __future__ import annotations

import os
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("APP_SECRET", "x" * 32)

try:
    import page_fixtures as pf
except ModuleNotFoundError:  # loaded as tests.* instead of discover mode
    from tests import page_fixtures as pf
from app.search.extract import FETCH_CHARS, extract_page, find_windows
from app.chat_tools import html_to_text


def html_fixtures():
    return [f for f in pf.FIXTURES if f["content_type"] == "text/html"]


class FixtureIntegrityTest(unittest.TestCase):
    """The fixture set itself must be complete and well described."""

    def test_manifest_has_entries(self):
        self.assertGreaterEqual(len(pf.FIXTURES), 8)

    def test_every_file_exists_and_is_https(self):
        for f in pf.FIXTURES:
            with self.subTest(fix=f["name"]):
                path = pf.SITES_DIR / f["file"]
                self.assertTrue(path.exists(), f"missing snapshot {f['file']}")
                self.assertGreater(path.stat().st_size, 200, "snapshot suspiciously tiny")
                self.assertTrue(f["url"].startswith("https://"))
                for key in ("must_contain", "must_not_contain", "expect_kind", "host"):
                    self.assertIn(key, f)

    def test_must_contain_phrases_actually_in_raw(self):
        # Tag-tolerant: extraction joins text across tags ("points</span> by",
        # pygments spans in code samples), so strip tags before matching.
        tag = re.compile(r"<[^>]+>")
        for f in pf.FIXTURES:
            raw = tag.sub("", pf.raw_page(f["name"]))
            for needle in f["must_contain"]:
                if needle == "JavaScript app":  # extractor notice, not page text
                    continue
                with self.subTest(fix=f["name"], needle=needle):
                    self.assertIn(needle, raw)

    def test_must_not_markers_actually_in_raw(self):
        for f in pf.FIXTURES:
            raw = pf.raw_page(f["name"])
            for marker in f["must_not_contain"]:
                with self.subTest(fix=f["name"], marker=marker):
                    self.assertIn(marker, raw, "junk marker no longer in snapshot; refresh marker")


class InfoPreservedTest(unittest.TestCase):
    def test_main_content_survives_extraction(self):
        for f in pf.FIXTURES:
            with self.subTest(fix=f["name"]):
                got = pf.extract_of(f["name"])
                self.assertNotEqual(got.kind, "empty")
                self.assertNotEqual(got.kind, "text")
                for needle in f["must_contain"]:
                    self.assertIn(needle, got.text)

    def test_titles_extracted_for_html_pages(self):
        for f in html_fixtures():
            with self.subTest(fix=f["name"]):
                got = pf.extract_of(f["name"])
                self.assertTrue(got.title, "no <title>/og:title found")

    def test_html_to_text_helper_matches_extract(self):
        for name in ("wikipedia_ada_lovelace", "guardian_article", "example_com"):
            with self.subTest(fix=name):
                self.assertEqual(html_to_text(pf.raw_page(name)), pf.extract_of(name).text)


class JunkAbsentTest(unittest.TestCase):
    def test_per_page_junk_markers_absent(self):
        for f in pf.FIXTURES:
            got = pf.extract_of(f["name"])
            for marker in f["must_not_contain"]:
                with self.subTest(fix=f["name"], marker=marker):
                    self.assertNotIn(marker, got.text)

    def test_global_script_css_sweep_absent(self):
        for f in pf.FIXTURES:
            got = pf.extract_of(f["name"])
            for marker in pf.GLOBAL_JUNK:
                with self.subTest(fix=f["name"], marker=marker):
                    self.assertNotIn(marker, got.text)

    def test_no_raw_markup_leakage(self):
        for f in html_fixtures():
            got = pf.extract_of(f["name"])
            sweep = pf.MARKUP_JUNK if not f["code_samples"] else pf.MARKUP_JUNK[1:]
            for marker in sweep:
                with self.subTest(fix=f["name"], marker=marker):
                    self.assertNotIn(marker, got.text)

    def test_no_json_app_shell_leak_on_github(self):
        got = pf.extract_of("github_llmrouter")
        for marker in ('"refInfo"', "embeddedData", "treeItems", "payloadAll"):
            self.assertNotIn(marker, got.text)


class ContextBudgetTest(unittest.TestCase):
    """The point of scrubbing: information in, code out, few chars for the model."""

    def test_output_capped_at_fetch_chars(self):
        for f in pf.FIXTURES:
            with self.subTest(fix=f["name"]):
                got = pf.extract_of(f["name"])
                self.assertLessEqual(got.chars_out, FETCH_CHARS)
                self.assertLessEqual(len(got.text), FETCH_CHARS)

    def test_large_pages_compress_hard(self):
        # Measured on everything extracted (all pages read_url can serve), so
        # the check does not move with the per-call FETCH_CHARS budget, which
        # test_output_capped_at_fetch_chars covers on its own.
        for f in pf.FIXTURES:
            got = pf.extract_of(f["name"])
            if got.chars_in < 50_000:
                continue
            with self.subTest(fix=f["name"]):
                ratio = got.chars_in / max(1, got.total_chars)
                self.assertGreaterEqual(ratio, 10.0, f"{f['name']} only compressed {ratio:.1f}x")

    def test_small_page_not_flagged_truncated(self):
        got = pf.extract_of("example_com")
        self.assertFalse(got.truncated)
        self.assertEqual(got.kind, "html")

    def test_huge_pages_flag_truncated(self):
        for name in ("wikipedia_ada_lovelace", "github_llmrouter"):
            with self.subTest(fix=name):
                self.assertTrue(pf.extract_of(name).truncated)

    def test_truncated_flag_matches_budget(self):
        # A page is flagged truncated exactly when more text follows the first
        # FETCH_CHARS page (pypi's ~13k of readable text now fits whole).
        for f in pf.FIXTURES:
            with self.subTest(fix=f["name"]):
                got = pf.extract_of(f["name"])
                self.assertEqual(got.truncated, got.total_chars > FETCH_CHARS)
                if not got.truncated:
                    self.assertEqual(got.chars_out, got.total_chars)


class RealPageBehaviorTest(unittest.TestCase):
    def test_find_pulls_windows_from_long_wikipedia_page(self):
        got = extract_page(
            pf.raw_page("wikipedia_ada_lovelace"),
            content_type="text/html",
            url=pf.BY_NAME["wikipedia_ada_lovelace"]["url"],
            find="analytical engine",
        )
        self.assertTrue(got.used_find)
        self.assertIn("analytical engine", got.text.lower())
        self.assertLessEqual(len(got.text), FETCH_CHARS)

    def test_find_miss_reports_cleanly(self):
        got = extract_page(
            pf.raw_page("example_com"),
            content_type="text/html",
            find="zzz-not-on-page",
        )
        self.assertIn("No literal match", got.text)
        self.assertIn("Example Domain", got.text)

    def test_arxiv_static_page_extracts_abstract(self):
        # arxiv abs pages carry a bare <link> in the body and never close
        # <main>; both used to swallow the abstract into the SPA fallback.
        got = pf.extract_of("arxiv_abs")
        self.assertEqual(got.kind, "html")
        self.assertIn("Attention Is All You Need", got.text)
        self.assertIn("dominant sequence transduction", got.text)
        self.assertNotIn("JavaScript app", got.text)

    def test_json_api_stays_pretty_and_intact(self):
        got = pf.extract_of("github_api_repo")
        self.assertEqual(got.kind, "json")
        self.assertIn('"full_name"', got.text.replace(" ", "").replace('"full_name"', '"full_name"'))
        self.assertIn("psf/requests", got.text)
        self.assertIn("html_url", got.text)

    def test_mdn_keeps_code_samples_but_not_site_chrome(self):
        got = pf.extract_of("mdn_array_filter")
        self.assertIn("words.filter", got.text)
        self.assertIn("Expected output", got.text)
        self.assertNotIn("Skip to search", got.text)
        self.assertNotIn("Sign up for free", got.text)

    def test_hackernews_titles_survive_table_layout(self):
        got = pf.extract_of("hackernews")
        self.assertIn("TALA Is Open-Source", got.text)
        self.assertIn("points by", got.text)
        # nested-table rows used to duplicate every item; the flattened
        # row must replace the raw cell text, not accompany it
        self.assertEqual(got.text.count("TALA Is Open-Source"), 1)
        self.assertEqual(got.text.count("new | past"), 1)

    def test_guardian_article_prose_without_adtech(self):
        got = pf.extract_of("guardian_article")
        self.assertIn("editorially independent", got.text)
        self.assertNotIn("googletag", got.text.lower())
        self.assertNotIn("prebid", got.text.lower())

    def test_wikipedia_lead_leaves_no_room_for_sidebar_noise(self):
        got = pf.extract_of("wikipedia_ada_lovelace")
        # the interlanguage list (inside header chrome) used to eat the window
        self.assertNotIn("Alemannisch", got.text)
        self.assertNotIn("Toggle the table of contents", got.text)
        self.assertLess(got.text.find("Augusta Ada King"), 400)


if __name__ == "__main__":
    unittest.main()
