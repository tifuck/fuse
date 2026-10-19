"""Page extraction stays linear on hostile input and unchanged on normal input.

Extraction runs in a worker thread, but CPython's re and the tree walks hold
the GIL, so a quadratic pass over a multi-megabyte page freezes the server.
"""

import re
import time

from selectolax.lexbor import LexborHTMLParser

from app.search import extract

N = 200_000
BUDGET_S = 0.5


def _timed(fn, *args, **kwargs):
    started = time.perf_counter()
    out = fn(*args, **kwargs)
    elapsed = time.perf_counter() - started
    assert elapsed < BUDGET_S, f"{fn.__name__} took {elapsed:.2f}s"
    return out


def _html(body: str) -> str:
    return f"<html><body>{body}</body></html>"


# --- pathological input completes quickly ----------------------------------


def test_long_space_run_without_newline():
    page = _timed(extract.extract_page, "a" + " " * N + "b", content_type="text/plain")
    assert page.full_text == "a" + " " * N + "b"
    _timed(extract._collapse, "a" + " \t" * N)


def test_unclosed_feed_items():
    raw = "<rss><channel><title>Feed</title>" + "<item" * (N // 5) + "<entry" * (N // 6)
    page = _timed(extract.extract_page, raw, content_type="application/rss+xml")
    assert page.full_text == "# Feed"


def test_unclosed_fields_inside_one_feed_item():
    for opener in ("<title", "<link", "<link rel=x>", "<pubDate", "<summary", "<description"):
        raw = "<rss><item>" + opener * (N // len(opener)) + "</item></rss>"
        page = _timed(extract.extract_page, raw, content_type="application/rss+xml")
        assert page.full_text.startswith("- (untitled)")


def test_heading_with_long_mark_or_space_run():
    for filler in ("#", "\u3000", "# ", "¶ "):
        page = _timed(extract.extract_page, _html("<h2>Title" + filler * (N // len(filler)) + "x</h2><p>body</p>"))
        assert page.text.startswith("## Title")


def test_long_consent_class_token():
    raw = _html('<div class="' + "consent" * (N // 7) + '!">x</div><p>' + "word " * 100 + "</p>")
    assert "word" in _timed(extract.extract_page, raw).text


def test_deeply_nested_chrome_and_content_wrappers():
    # Lexbor's own tree builder is quadratic in the depth of nested block
    # elements, so the depth here stays small enough for it; the tree walks
    # of _prune and _pick_root must still be linear in it.
    depth = 6_000
    for tag in ("nav", "header", "article", "form"):
        raw = _html(f"<{tag}>x " * depth + "word " * 2_000)
        _timed(extract._html_extract, raw, "")
    raw = _html("<main>" + "<a href=x>t <table><tr><td>" * depth + "</main>")
    _timed(extract._html_extract, raw, "")


# --- same output as the regexes they replace ---------------------------------


def test_strip_line_ends_matches_regex():
    regex = re.compile(r"[ \t]+\n")
    for text in ("", "a  \nb\t\n", "a \t \n\n  \n c  ", "  \n", "x\r \n y", "tail  "):
        assert extract._strip_line_ends(text) == regex.sub("\n", text)


def test_trim_heading_matches_regex():
    regex = re.compile(r"\s*[¶#🔗§]+\s*$")
    for text in ("Install ¶", "Install", "C# basics", "Notes #", "Notes # ", "A ## # §", "¶", "x \u3000🔗 ", " # x", ""):
        assert extract._trim_heading(text) == regex.sub("", text)


def test_chrome_token_consent_matches_old_pattern():
    old = re.compile(r"^(?:\w*consent\w*)$")
    new = re.compile(r"^(?:(?=\w*consent)\w*)$")
    for token in ("consent", "cookieconsent", "consent_banner", "my-consent", "consent!", "con", "xconsentx", ""):
        assert bool(new.match(token)) == bool(old.match(token))


def test_feed_text_matches_regex_scan():
    item = re.compile(r"(?is)<(item|entry)\b.*?</\1>")
    raw = (
        "<rss><channel><title><![CDATA[News]]></title>"
        "<item><title>One</title><link>https://a.example/1</link><pubDate>Mon, 01 Jan 2024</pubDate>"
        "<description><![CDATA[<p>first</p>]]></description></item>"
        "<ITEM x='1'><title>Two</title><link rel=alternate><link href='https://a.example/2'/></Item>"
        "<items>not an item</items><item>unclosed <entry><title>Three</title>"
        "<link href=\"https://a.example/3\"/><updated>2024-01-02</updated><summary>s</summary></entry>"
    )
    assert [block for block in extract._feed_items(raw)] == [m.group(0) for m in item.finditer(raw)]
    assert extract._feed_text(raw) == (
        "# News\n"
        "- One (Mon, 01 Jan 2024)\n  https://a.example/1\n  first\n"
        "- Two\n  https://a.example/2\n"
        "- Three (2024-01-02)\n  https://a.example/3\n  s"
    )


def test_feed_item_cap_kept():
    raw = "<rss><channel><title>F</title>" + "<item><title>t</title></item>" * (extract.FEED_MAX_ITEMS + 5)
    lines = extract._feed_text(raw).split("\n")
    assert lines[0] == "# F"
    assert lines[1:] == ["- t"] * extract.FEED_MAX_ITEMS + ["(… more feed items not shown)"]


def test_sizes_match_direct_measurement():
    html = _html(
        "<header> Site <a href='/'> Home </a></header>"
        "<main role=main><article><h1>T</h1>\u00a0<p>a  b <b>c</b>\u200b<a href=x>\n link \t</a></p>"
        "<!-- note --><div hidden> \u3000 </div><a><table><tr><td><a href=y>in</a></td></tr></table></a></article></main>"
        "<form> <nav class=menu>x</nav> </form><footer>\x1c foot \x1c</footer>"
    )
    tree = LexborHTMLParser(html)
    sizes = extract._Sizes(tree.root)
    for node in tree.root.traverse():
        if node.tag.startswith("-"):
            continue
        assert sizes.text_len(node) == extract._text_len(node), node.tag
        assert sizes.link_len(node) == sum(len((a.text() or "").strip()) for a in node.css("a")), node.tag
        assert sizes.holds_content(node) == (node.css_first("article, main, [role=main]") is not None), node.tag


def test_normal_page_extract_unchanged():
    html = _html(
        "<nav class='menu'><a href='/a'>A</a></nav>"
        "<main><h2 id=s>Setup <a class=headerlink href='#s'>¶</a></h2>"
        "<p>Install it   \n with <code>pip</code>.  </p><p>Then run it.</p></main><footer>f</footer>"
    )
    page = extract.extract_page(html, url="https://docs.example/")
    assert page.text == "## Setup\n\nInstall it with `pip`.\n\nThen run it."
