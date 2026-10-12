from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse, urlunparse

from selectolax.lexbor import LexborHTMLParser

from .text import bm25_scores, terms

logger = logging.getLogger(__name__)

# Decoded bytes read from one page. Lexbor builds the HTML5 tree of a
# multi-megabyte page in milliseconds, so this only bounds memory: a 2.7 MB
# Wikipedia article or a 2 MB docs page now arrives whole instead of losing
# everything after its inline bundles.
FETCH_MAX_BYTES = 6_000_000
PDF_MAX_BYTES = 16_000_000
# One window of extracted text per read_url call; pass offset to continue.
FETCH_CHARS = 24_000
FIND_MAX_CHARS = 80
FIND_MAX_MATCHES = 8
JSON_CHARS = FETCH_CHARS
CODE_BLOCK_CHARS = 20_000
TABLE_MAX_ROWS = 150
TABLE_CELL_CHARS = 300
TABLE_MAX_COLS = 30
MAX_LINKS = 400
PASSAGE_CHARS = 1_200
PASSAGE_MAX = 8
MIN_MAIN_CHARS = 200
SPA_MIN_CHARS = 80
RENDER_MAX_DEPTH = 150
FEED_MAX_ITEMS = 100

# Removed with their whole subtree before anything is read.
JUNK_TAGS = (
    "script", "style", "noscript", "template", "svg", "canvas", "iframe", "object",
    "embed", "link", "meta", "base", "head", "dialog", "video", "audio", "map",
    "input", "select", "textarea", "button", "option",
)
JUNK_SET = frozenset(JUNK_TAGS)
CHROME_TAGS = frozenset({"nav", "footer"})
CHROME_ROLES = frozenset(
    {"navigation", "banner", "contentinfo", "complementary", "search", "menu", "menubar",
     "toolbar", "tablist", "dialog", "alertdialog"}
)
# Exact class/id tokens of site furniture (nav, cookie banners, share bars,
# screen-reader-only "Skip to content", Wikipedia edit links, heading anchors).
_CHROME_TOKEN = re.compile(
    r"^(?:nav|navbar|navbox|navigation|menu|menubar|sidebar|side-bar|sidenav|"
    r"breadcrumbs?|toc|table-of-contents|site-footer|site-header|footer|masthead|"
    r"cookies?|cookie-?\w*|\w*-cookies?|\w*consent\w*|gdpr\w*|share|sharing|share-\w+|"
    r"social|social-\w+|newsletter|subscribe|related|related-\w+|recommended|promo|"
    r"advert\w*|ads|ad-slot|ad-container|banner-ad|skip-?links?|skip-to-content|"
    r"sr-only|visually-hidden|screen-reader-text|mw-editsection|mw-jump-link|catlinks|"
    r"printfooter|noprint|hatnote|announcement-?bar|edit-this-page|"
    r"vector-(?:header|toc|menu|main-menu|dropdown|page-toolbar|page-titlebar-toc|column-start|column-end|sticky-header|pinned-container|user-links|appearance)[\w-]*|"
    r"theme-edit-this-page|pagination-nav|headerlink|hash-link|anchor-link|heading-anchor|"
    r"linenos|lineno|line-numbers-rows|gutter)$"
)
_CALLOUT_TOKEN = re.compile(r"note|callout|admonition|warning|tip|info|alert|important|caution")
MAIN_SELECTOR = ", ".join(
    [
        "article", "main", "[role=main]", "[itemprop=articleBody]", "#content",
        "#main-content", "#main", "#mw-content-text", ".markdown-body", ".post-content",
        ".entry-content", ".article-content", ".article-body", ".post-body", ".prose",
        ".theme-doc-markdown", ".md-content", ".rst-content", "div.body", "#readme",
    ]
)
BLOCK_TAGS = frozenset(
    {
        "address", "article", "aside", "blockquote", "body", "caption", "center", "dd",
        "details", "div", "dl", "dt", "fieldset", "figcaption", "figure", "footer", "form",
        "h1", "h2", "h3", "h4", "h5", "h6", "header", "hgroup", "hr", "html", "legend", "li",
        "main", "menu", "nav", "ol", "p", "pre", "search", "section", "summary", "table",
        "tbody", "td", "tfoot", "th", "thead", "tr", "ul",
    }
)
HEADING_LEVEL = {"h1": 1, "h2": 2, "h3": 3, "h4": 4, "h5": 5, "h6": 6}
_ARTICLE_TYPES = frozenset({"article", "newsarticle", "techarticle", "blogposting", "report", "scholarlyarticle"})
_PUBLISHED_META = frozenset(
    {"article:published_time", "date", "pubdate", "publishdate", "datepublished", "dc.date",
     "dcterms.created", "citation_publication_date", "parsely-pub-date", "sailthru.date"}
)
_MODIFIED_META = frozenset({"article:modified_time", "og:updated_time", "datemodified", "dcterms.modified", "last-modified"})
_LANG_RE = re.compile(r"(?:^|\s)(?:language|lang|highlight-source|highlight|brush:?)-([a-z0-9_+#.-]+)", re.I)
_BR = " "  # <br> marker inside inline runs; never appears in page text we keep
_INLINE_WS = re.compile(r"[ \t\n\r\f\v ​‌‍﻿]+")
_MULTI_NL = re.compile(r"\n{3,}")
_TRAILING_WS = re.compile(r"[ \t]+\n")
_HEADING_TRAIL = re.compile(r"\s*[¶#🔗§]+\s*$")
_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_OUTLINE_RE = re.compile(r"^(#{1,6}) (.+)$")
_FENCE_RE = re.compile(r"^(```|~~~)")
_CITATION = re.compile(r"\[[^\]\n]{1,24}\]")


@dataclass
class PageExtract:
    title: str
    text: str
    truncated: bool
    kind: str
    used_find: bool
    chars_in: int
    chars_out: int
    links: list[dict] = field(default_factory=list)
    # Paging over the full extracted text (plain page mode only; find/query
    # passages are not paged). total_chars is the extracted length.
    offset: int = 0
    total_chars: int = 0
    next_offset: int | None = None
    paged: bool = False
    # Bytes the server sent for the page ("Received 12.3KB (200 OK)").
    bytes_in: int = 0
    # Complete extracted text, unwindowed; kept for cached find/query/offset
    # re-reads so callers never need the raw body again. Not serialized.
    full_text: str = field(default="", repr=False)
    # Publication / last-modified date the page declares (ISO date when known).
    published: str = ""
    # The download stopped at the byte cap, so the page itself is incomplete.
    download_truncated: bool = False
    # (offset, level, heading) for every heading in full_text.
    outline: list[tuple[int, int, str]] = field(default_factory=list, repr=False)


@dataclass(frozen=True)
class _Passage:
    start: int
    end: int
    text: str
    heading: str = ""


# --- metadata ----------------------------------------------------------------


def _norm_inline(text: str) -> str:
    lines = (text or "").split(_BR)
    out = [_INLINE_WS.sub(" ", line).strip() for line in lines]
    joined = "\n".join(out)
    return _MULTI_NL.sub("\n\n", joined).strip()


def _date_of(value: str) -> str:
    value = (value or "").strip()
    match = re.match(r"(\d{4}-\d{2}-\d{2})", value)
    return match.group(1) if match else value[:40]


def _ld_nodes(data) -> list[dict]:
    nodes = data if isinstance(data, list) else [data]
    out: list[dict] = []
    for node in nodes:
        if isinstance(node, dict) and isinstance(node.get("@graph"), list):
            out.extend(item for item in node["@graph"] if isinstance(item, dict))
        elif isinstance(node, dict):
            out.append(node)
    return out


def _ld_article(raw: str) -> dict:
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError, RecursionError):
        return {}
    best: dict = {}
    for node in _ld_nodes(data):
        types = node.get("@type")
        if isinstance(types, str):
            types = [types]
        if not isinstance(types, list) or not any(str(t).lower() in _ARTICLE_TYPES for t in types):
            continue
        found = {
            "headline": _norm_inline(str(node.get("headline") or ""))[:200],
            "published": _date_of(str(node.get("datePublished") or "")),
            "modified": _date_of(str(node.get("dateModified") or "")),
            "body": "",
        }
        body = node.get("articleBody")
        if isinstance(body, str) and body.strip():
            found["body"] = _clean_fragment(body)
        if len(found["body"]) >= len(best.get("body", "")) or not best:
            best = found
    return best


def _metadata(tree: LexborHTMLParser) -> dict[str, str]:
    meta = {
        "title": "", "og_title": "", "description": "", "og_description": "", "published": "",
        "modified": "", "site": "", "ld_headline": "", "ld_body": "",
    }
    title = tree.css_first("title")
    if title is not None:
        meta["title"] = _norm_inline(title.text())[:200]
    for node in tree.css("meta"):
        attrs = node.attributes
        name = (attrs.get("name") or attrs.get("itemprop") or "").lower()
        prop = (attrs.get("property") or "").lower()
        content = (attrs.get("content") or "").strip()
        if not content:
            continue
        if name == "description" and not meta["description"]:
            meta["description"] = content[:400]
        if prop == "og:title" and not meta["og_title"]:
            meta["og_title"] = content[:200]
        if prop == "og:description" and not meta["og_description"]:
            meta["og_description"] = content[:400]
        if prop == "og:site_name" and not meta["site"]:
            meta["site"] = content[:100]
        if (prop in _PUBLISHED_META or name in _PUBLISHED_META) and not meta["published"]:
            meta["published"] = _date_of(content)
        if (prop in _MODIFIED_META or name in _MODIFIED_META) and not meta["modified"]:
            meta["modified"] = _date_of(content)
    for node in tree.css("script"):
        if (node.attributes.get("type") or "").strip().lower() != "application/ld+json":
            continue
        found = _ld_article(node.text() or "")
        if not found:
            continue
        if len(found["body"]) > len(meta["ld_body"]):
            meta["ld_body"] = found["body"]
            meta["ld_headline"] = found["headline"]
        meta["published"] = meta["published"] or found["published"]
        meta["modified"] = meta["modified"] or found["modified"]
    return meta


# --- cleanup and main-content choice -------------------------------------------


def _is_chrome(tag: str, attrs: dict) -> bool:
    if tag in CHROME_TAGS:
        return True
    if (attrs.get("role") or "").lower() in CHROME_ROLES:
        return True
    tokens = f"{attrs.get('class') or ''} {attrs.get('id') or ''}".lower().split()
    if tag == "aside":
        return not any(_CALLOUT_TOKEN.search(token) for token in tokens)
    return any(_CHROME_TOKEN.match(token) for token in tokens)


def _is_hidden(attrs: dict) -> bool:
    if "hidden" in attrs or (attrs.get("aria-hidden") or "").lower() == "true":
        return True
    style = (attrs.get("style") or "").replace(" ", "").lower()
    return "display:none" in style or "visibility:hidden" in style


def _text_len(node) -> int:
    return len(_INLINE_WS.sub(" ", node.text(deep=True) or "").strip())


def _remove(root, judge) -> None:
    """Decompose every element judge() picks, never descending into one.

    Collecting nested matches first and freeing them in order would free a
    child after its parent subtree is already gone.
    """
    doomed = []
    stack = [root]
    while stack:
        node = stack.pop()
        for child in node.iter():
            if judge(child):
                doomed.append(child)
            else:
                stack.append(child)
    for node in doomed:
        node.decompose()


def _strip_junk(root) -> None:
    _remove(root, lambda node: node.tag in JUNK_SET)


def _prune(body) -> None:
    """Drop hidden nodes and site chrome, but never a wrapper of the content."""
    total = max(1, _text_len(body))

    def judge(node) -> bool:
        tag = node.tag
        attrs = node.attributes
        if tag == "header":
            chrome = not _inside_content(node)
        elif tag == "form":
            return _text_len(node) < 0.5 * total and node.css_first("article, main, [role=main]") is None
        else:
            chrome = _is_hidden(attrs) or _is_chrome(tag, attrs)
        if not chrome:
            return False
        if node.css_first("article, main, [role=main]") is not None:
            return False
        return _text_len(node) <= 0.4 * total

    _remove(body, judge)


def _inside_content(node) -> bool:
    parent = node.parent
    while parent is not None and parent.tag not in ("body", "html"):
        if parent.tag in ("article", "main") or (parent.attributes.get("role") or "") == "main":
            return True
        parent = parent.parent
    return False


def _link_len(node) -> int:
    return sum(len((a.text() or "").strip()) for a in node.css("a"))


def _pick_root(tree: LexborHTMLParser, body):
    seen: set[int] = set()
    ranked = []
    articles = 0
    for node in tree.css(MAIN_SELECTOR):
        if node.mem_id in seen:
            continue
        seen.add(node.mem_id)
        length = _text_len(node)
        if not length:
            continue
        if node.tag == "article":
            articles += 1
        is_main = node.tag == "main" or (node.attributes.get("role") or "") == "main"
        score = length - 2 * _link_len(node) + (100 if is_main else 0)
        ranked.append((score, length, node))
    if not ranked:
        return body, False
    ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
    long = [item for item in ranked if item[1] >= MIN_MAIN_CHARS]
    score, length, best = (long or ranked)[0]
    if articles >= 3 and best.tag == "article" and length < 0.4 * _text_len(body):
        # A listing of article cards: the page is the list, not one card.
        mains = [node for _, _, node in ranked if node.tag == "main"]
        return (mains[0] if mains else body), True
    return best, True


# --- HTML -> markdown --------------------------------------------------------


class _Ctx:
    __slots__ = ("base_url", "links", "_link_urls", "_base")

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url
        self.links: list[dict] = []
        self._link_urls: set[str] = set()
        self._base = urlunparse(urlparse(base_url)._replace(fragment="")) if base_url else ""

    def add_link(self, href: str, label: str) -> None:
        if len(self.links) >= MAX_LINKS or not label:
            return
        href = (href or "").strip()
        if not href or href.startswith(("#", "javascript:", "data:", "mailto:", "tel:")):
            return
        absolute = urljoin(self.base_url, href) if self.base_url else href
        parsed = urlparse(absolute)
        if parsed.scheme and parsed.scheme not in {"http", "https"}:
            return
        if self.base_url and not parsed.netloc:
            return
        absolute = urlunparse(parsed._replace(fragment=""))
        if not absolute or absolute == self._base or absolute in self._link_urls:
            return
        self._link_urls.add(absolute)
        self.links.append({"url": absolute, "text": label[:200]})


def _children(node):
    return node.iter(include_text=True)


def _has_block_child(node) -> bool:
    return any(child.tag in BLOCK_TAGS for child in node.iter())


def _render_children(node, ctx: _Ctx, depth: int = 0) -> list[str]:
    blocks: list[str] = []
    run: list[str] = []

    def flush() -> None:
        if run:
            text = _norm_inline("".join(run))
            if text:
                blocks.append(text)
            run.clear()

    for child in _children(node):
        tag = child.tag
        if tag == "-text":
            run.append(child.text_content or "")
            continue
        if not tag or tag.startswith("-"):
            continue
        if tag in BLOCK_TAGS or _has_block_child(child):
            flush()
            if tag == "a":
                ctx.add_link(child.attributes.get("href") or "", _norm_inline(child.text() or ""))
            blocks.extend(_render_block(child, ctx, depth + 1))
        else:
            run.append(_inline(child, ctx, depth + 1))
    flush()
    return blocks


INLINE_TAGS = frozenset(
    {
        "a", "abbr", "b", "bdi", "bdo", "big", "br", "cite", "code", "data", "del", "dfn", "em",
        "font", "i", "img", "ins", "kbd", "label", "mark", "math", "meter", "nobr", "output",
        "picture", "progress", "q", "rp", "rt", "ruby", "s", "samp", "small", "source", "span",
        "strike", "strong", "sub", "sup", "time", "tt", "u", "var", "wbr",
    }
)
_CODE_TAGS = frozenset({"code", "kbd", "samp", "tt"})


def _inline(node, ctx: _Ctx, depth: int) -> str:
    tag = node.tag
    if depth > RENDER_MAX_DEPTH:
        return " " + (node.text() or "") + " "
    if tag == "br":
        return _BR
    if tag == "wbr":
        return ""
    if tag == "img":
        attrs = node.attributes
        alt = _INLINE_WS.sub(" ", attrs.get("alt") or "").strip()
        if not alt:
            return ""
        if "math" in (attrs.get("class") or ""):
            return f" ${alt}$ "
        if len(alt.split()) < 2 or _in_link(node):
            return ""
        return f" [image: {alt[:200]}] "
    if tag == "math":
        alt = (node.attributes.get("alttext") or "").strip()
        return f" ${alt}$ " if alt else (node.text() or "")
    if tag in _CODE_TAGS:
        code = _INLINE_WS.sub(" ", node.text() or "").strip()
        if not code:
            return ""
        fence = "``" if "`" in code else "`"
        return f"{fence}{code}{fence}"
    parts: list[str] = []
    for child in _children(node):
        ctag = child.tag
        if ctag == "-text":
            parts.append(child.text_content or "")
        elif ctag and not ctag.startswith("-"):
            if ctag in BLOCK_TAGS:
                parts.append(" " + " ".join(_render_block(child, ctx, depth + 1)) + " ")
            else:
                parts.append(_inline(child, ctx, depth + 1))
    text = "".join(parts)
    if tag == "sup" and _CITATION.fullmatch(text.strip()):
        # Wikipedia-style reference markers ("[3]", "[citation needed]").
        return ""
    if tag == "a":
        ctx.add_link(node.attributes.get("href") or "", _norm_inline(text))
    elif tag == "q":
        text = f'"{text}"'
    return text


def _in_link(node) -> bool:
    parent = node.parent
    for _ in range(6):
        if parent is None:
            return False
        if parent.tag == "a":
            return True
        parent = parent.parent
    return False


_LIST_START = re.compile(r"^(?:- |\d+\. )")


def _join_item(blocks: list[str]) -> str:
    """Blocks of one list item: nested lists stay tight, paragraphs get a gap."""
    out = ""
    for block in blocks:
        if out:
            out += "\n" if _LIST_START.match(block) else "\n\n"
        out += block
    return out


def _indent(text: str, marker: str) -> str:
    pad = " " * len(marker)
    lines = text.split("\n")
    out = [marker + lines[0]]
    out.extend((pad + line) if line.strip() else "" for line in lines[1:])
    return "\n".join(out)


def _render_block(node, ctx: _Ctx, depth: int) -> list[str]:
    tag = node.tag
    if depth > RENDER_MAX_DEPTH:
        text = _norm_inline(node.text() or "")
        return [text] if text else []
    level = HEADING_LEVEL.get(tag)
    if level:
        text = _HEADING_TRAIL.sub("", _norm_inline(_inline(node, ctx, depth)).replace("\n", " "))
        return [f"{'#' * level} {text[:300]}"] if text else []
    if tag == "pre":
        return _render_pre(node)
    if tag in ("ul", "ol", "menu"):
        return _render_list(node, ctx, depth, ordered=tag == "ol")
    if tag == "li":
        body = _join_item(_render_children(node, ctx, depth))
        return [_indent(body, "- ")] if body else []
    if tag == "table":
        return _render_table(node, ctx, depth)
    if tag == "dl":
        return _render_dl(node, ctx, depth)
    if tag == "blockquote":
        inner = "\n\n".join(_render_children(node, ctx, depth))
        if not inner:
            return []
        return ["\n".join(("> " + line) if line else ">" for line in inner.split("\n"))]
    if tag == "hr":
        return []
    return _render_children(node, ctx, depth)


def _render_pre(node) -> list[str]:
    for br in node.css("br"):
        br.replace_with("\n")
    code = (node.text(deep=True) or "").replace("\r\n", "\n").replace("\r", "\n").replace(" ", " ")
    code = "\n".join(line.rstrip() for line in code.split("\n")).strip("\n")
    if not code.strip():
        return []
    lang = ""
    parent = node.parent
    probes = [node, node.css_first("code"), parent, parent.parent if parent is not None else None]
    for probe in probes:
        if probe is None:
            continue
        match = _LANG_RE.search(probe.attributes.get("class") or "")
        if match:
            lang = match.group(1).lower()[:20]
            break
    if len(code) > CODE_BLOCK_CHARS:
        omitted = len(code) - CODE_BLOCK_CHARS
        code = code[:CODE_BLOCK_CHARS].rstrip() + f"\n… ({omitted} more characters of this code block omitted)"
    fence = "~~~" if "```" in code else "```"
    return [f"{fence}{lang}\n{code}\n{fence}"]


def _render_list(node, ctx: _Ctx, depth: int, *, ordered: bool) -> list[str]:
    try:
        number = int(node.attributes.get("start") or 1)
    except ValueError:
        number = 1
    items: list[str] = []
    for child in node.iter():
        if child.tag == "li":
            body = _join_item(_render_children(child, ctx, depth + 1))
            marker = f"{number}. " if ordered else "- "
            number += 1
            if body:
                items.append(_indent(body, marker))
        else:
            items.extend(_render_block(child, ctx, depth + 1))
    return ["\n".join(items)] if items else []


def _render_dl(node, ctx: _Ctx, depth: int) -> list[str]:
    lines: list[str] = []
    for child in node.iter():
        if child.tag == "dt":
            term = _norm_inline(_inline(child, ctx, depth + 1)).replace("\n", " ")
            if term:
                lines.append(term)
        elif child.tag == "dd":
            body = "\n".join(_render_children(child, ctx, depth + 1))
            if body:
                lines.append(_indent(body, ": "))
        else:
            lines.extend(_render_block(child, ctx, depth + 1))
    return ["\n".join(lines)] if lines else []


def _table_rows(table) -> tuple[str, list]:
    caption = ""
    rows = []
    for child in table.iter():
        if child.tag in ("thead", "tbody", "tfoot"):
            rows.extend(row for row in child.iter() if row.tag == "tr")
        elif child.tag == "tr":
            rows.append(child)
        elif child.tag == "caption":
            caption = _norm_inline(child.text() or "")
    return caption, rows


def _cells(row) -> list:
    return [cell for cell in row.iter() if cell.tag in ("td", "th")]


def _is_layout_table(table, rows: list) -> bool:
    if (table.attributes.get("role") or "").lower() in ("presentation", "none"):
        return True
    # css() matches the node itself too, so a nested table means two hits.
    if len(rows) < 2 or len(table.css("table")) > 1 or table.css_first("pre") is not None:
        return True
    widths = [len(_cells(row)) for row in rows]
    if max(widths, default=0) < 2:
        return True
    cells = sum(widths) or 1
    return _text_len(table) / cells > 400


def _render_table(table, ctx: _Ctx, depth: int) -> list[str]:
    caption, rows = _table_rows(table)
    if not rows:
        return _render_children(table, ctx, depth)
    if _is_layout_table(table, rows):
        blocks: list[str] = []
        for row in rows:
            rendered = []
            for cell in _cells(row):
                parts = _render_children(cell, ctx, depth + 1)
                text = "\n\n".join(parts).strip()
                if text and not re.fullmatch(r"[\d\s]+", text):
                    rendered.append(text)
            if not rendered:
                continue
            if all("\n" not in part and len(part) <= 200 for part in rendered):
                blocks.append(" | ".join(rendered))
            else:
                blocks.extend(rendered)
        return ([caption] if caption else []) + blocks
    grid: list[list[str]] = []
    for row in rows:
        line: list[str] = []
        for cell in _cells(row):
            text = " ".join(_render_children(cell, ctx, depth + 1)).replace("\n", " ")
            text = _INLINE_WS.sub(" ", text).strip()[:TABLE_CELL_CHARS].replace("|", "\\|")
            line.append(text)
            try:
                span = max(1, min(int(cell.attributes.get("colspan") or 1), TABLE_MAX_COLS))
            except ValueError:
                span = 1
            line.extend([""] * (span - 1))
        if any(line):
            grid.append(line[:TABLE_MAX_COLS])
    if not grid:
        return []
    width = max(len(line) for line in grid)
    lines = []
    shown = grid[:TABLE_MAX_ROWS]
    for index, line in enumerate(shown):
        padded = line + [""] * (width - len(line))
        lines.append("| " + " | ".join(padded) + " |")
        if index == 0:
            lines.append("|" + " --- |" * width)
    if len(grid) > TABLE_MAX_ROWS:
        lines.append(f"(… {len(grid) - TABLE_MAX_ROWS} more table rows not shown)")
    return ([caption] if caption else []) + ["\n".join(lines)]


_SELF_RENDERED = frozenset({"ul", "ol", "menu", "table", "pre", "dl", "blockquote", "h1", "h2", "h3", "h4", "h5", "h6"})


def _render_root(root, ctx: _Ctx) -> str:
    if root.tag in _SELF_RENDERED:
        blocks = _render_block(root, ctx, 0)
    else:
        blocks = _render_children(root, ctx, 0)
    return _collapse("\n\n".join(blocks))


def _collapse(text: str) -> str:
    text = _TRAILING_WS.sub("\n", text)
    text = _MULTI_NL.sub("\n\n", text)
    return text.strip()


def _clean_fragment(fragment: str) -> str:
    """Render HTML found inside structured data (JSON-LD articleBody)."""
    if "<" not in (fragment or ""):
        return _collapse(fragment or "")
    tree = LexborHTMLParser(fragment)
    root = tree.body or tree.root
    if root is None:
        return ""
    _strip_junk(root)
    return _render_root(root, _Ctx(""))


def _html_extract(raw: str, url: str) -> tuple[str, str, list[dict], dict[str, str], bool]:
    """(title, text, links, meta, structured) for an HTML document."""
    tree = LexborHTMLParser(raw)
    meta = _metadata(tree)
    title = meta.get("og_title") or meta.get("title") or ""
    body = tree.body
    if body is None:
        return title, "", [], meta, False
    _strip_junk(body)
    _prune(body)
    root, structured = _pick_root(tree, body)
    ctx = _Ctx(url)
    picked = _render_root(root, ctx)
    ld_body = meta.get("ld_body") or ""
    if len(ld_body) >= MIN_MAIN_CHARS and len(picked) < 0.6 * len(ld_body):
        # The markup lost the story (paywall shell, client render); the
        # publisher's structured copy has it.
        head = meta.get("ld_headline") or title
        picked = (f"# {head}\n\n" if head else "") + ld_body
        structured = True
    return title, picked, ctx.links, meta, structured or bool(ld_body)


# --- other content types ----------------------------------------------------


def _clean_plain(raw: str) -> str:
    """Plain text, markdown and code keep their lines and indentation."""
    text = (raw or "").replace("\r\n", "\n").replace("\r", "\n").lstrip("﻿")
    text = _CTRL.sub("", text)
    text = _TRAILING_WS.sub("\n", text)
    text = re.sub(r"\n{4,}", "\n\n\n", text)
    return text.strip("\n")


_FEED_ITEM = re.compile(r"(?is)<(item|entry)\b.*?</\1>")
_FEED_FIELD = {
    "title": re.compile(r"(?is)<title[^>]*>(.*?)</title>"),
    "link": re.compile(r"(?is)<link[^>]*>(.*?)</link>|<link[^>]*href=[\"']([^\"']+)[\"']"),
    "date": re.compile(r"(?is)<(?:pubDate|published|updated|dc:date)[^>]*>(.*?)</"),
    "summary": re.compile(r"(?is)<(?:description|summary|content)[^>]*>(.*?)</(?:description|summary|content)>"),
}


def _feed_text(raw: str) -> str:
    """RSS/Atom feeds as a dated markdown list instead of raw XML."""

    def field_of(name: str, block: str) -> str:
        match = _FEED_FIELD[name].search(block)
        if not match:
            return ""
        value = next((g for g in match.groups() if g), "")
        value = re.sub(r"(?is)^<!\[CDATA\[(.*)\]\]>$", r"\1", value.strip())
        if "<" in value or "&" in value:
            value = _clean_fragment(value) if "<" in value else _norm_inline(value)
        return value.strip()

    lines: list[str] = []
    channel = _FEED_FIELD["title"].search(raw[:4000])
    if channel:
        lines.append("# " + _norm_inline(re.sub(r"(?is)<!\[CDATA\[(.*)\]\]>", r"\1", channel.group(1))))
    for index, match in enumerate(_FEED_ITEM.finditer(raw)):
        if index >= FEED_MAX_ITEMS:
            lines.append(f"(… more feed items not shown)")
            break
        block = match.group(0)
        title = _norm_inline(field_of("title", block)).replace("\n", " ")
        link = field_of("link", block)
        date = field_of("date", block)
        summary = _norm_inline(field_of("summary", block))[:500]
        head = f"- {title or '(untitled)'}"
        if date:
            head += f" ({date[:40]})"
        lines.append(head)
        if link:
            lines.append(f"  {link}")
        if summary:
            lines.append(f"  {summary}")
    return "\n".join(lines)


def _is_feed(raw: str, ctype: str) -> bool:
    head = (raw or "")[:2000].lower()
    return ("rss" in ctype or "atom" in ctype or "xml" in ctype) and ("<rss" in head or "<feed" in head)


# --- passages, find, outline -------------------------------------------------


def _passage_chunks(text: str, max_chars: int = PASSAGE_CHARS) -> list[_Passage]:
    """Split at headings and paragraphs while retaining heading context."""
    if not text:
        return []
    matches = list(
        re.finditer(r"(?ms)(?:^|\n)(#{1,6} [^\n]+\n?|[^\n].*?(?=\n\s*\n|\Z))", text)
    )
    if not matches:
        return [_Passage(0, len(text), text)]
    chunks: list[_Passage] = []
    heading = ""
    start = -1
    end = -1
    parts: list[str] = []
    chunk_heading = ""
    for match in matches:
        block = match.group(1).strip()
        if not block:
            continue
        is_heading = bool(re.match(r"^#{1,6} ", block))
        if is_heading:
            if parts:
                chunks.append(_Passage(start, end, "\n\n".join(parts), chunk_heading))
                start, parts, chunk_heading = -1, [], ""
            heading = block
            continue
        if len(block) <= max_chars:
            pieces = [(match.start(1), match.end(1), block)]
        else:
            # One oversized paragraph (plain text, PDFs) is split so a find
            # hit near its end returns the window that holds it.
            base = match.start(1) + len(match.group(1)) - len(match.group(1).lstrip())
            pieces = [(base + k, base + k + len(block[k:k + max_chars]), block[k:k + max_chars])
                      for k in range(0, len(block), max_chars)]
        for piece_start, piece_end, piece in pieces:
            rendered = piece
            if heading and not parts:
                rendered = heading + "\n\n" + piece
            projected = len("\n\n".join(parts + [rendered]))
            if parts and projected > max_chars:
                chunks.append(_Passage(start, end, "\n\n".join(parts), chunk_heading))
                start, parts, chunk_heading = -1, [], ""
                rendered = (heading + "\n\n" if heading else "") + piece
            if start < 0:
                start = piece_start
                chunk_heading = heading
            end = piece_end
            parts.append(rendered)
    if parts:
        chunks.append(_Passage(start, end, "\n\n".join(parts), chunk_heading))
    return chunks


def chunk_text(text: str, max_chars: int = PASSAGE_CHARS) -> list[str]:
    """Return heading/paragraph-aware chunks without rewording content."""
    return [chunk.text for chunk in _passage_chunks(text, max(200, max_chars))]


def _merge_passages(passages: list[_Passage]) -> list[_Passage]:
    merged: list[_Passage] = []
    for passage in sorted(passages, key=lambda item: item.start):
        if merged and passage.start <= merged[-1].end + 2:
            prior = merged[-1]
            addition = passage.text if passage.text not in prior.text else ""
            if addition and prior.heading and passage.heading == prior.heading:
                addition = addition.removeprefix(prior.heading).lstrip()
            # end == start is one paragraph sliced by the size cut, not two paragraphs.
            gap = "" if passage.start <= prior.end else "\n\n"
            text = prior.text + (gap if addition else "") + addition
            merged[-1] = _Passage(prior.start, max(prior.end, passage.end), text, prior.heading)
        else:
            merged.append(passage)
    return merged


def _render_passages(passages: list[_Passage], budget: int) -> str:
    out: list[str] = []
    used = 0
    for passage in _merge_passages(passages):
        block = f"[offset {passage.start}]\n{passage.text}"
        if out and used + len(block) + 5 > budget:
            break
        out.append(block)
        used += len(block) + 5
    return "\n---\n".join(out)[:budget]


def rank_passages(text: str, query: str) -> list[tuple[float, _Passage]]:
    """Chunks of text scored against query (BM25 + heading and phrase bonus)."""
    chunks = _passage_chunks(text)
    wanted = terms(query)
    if not chunks or not wanted:
        return []
    docs = [terms(chunk.text) for chunk in chunks]
    scores = bm25_scores(docs, wanted)
    phrase = re.sub(r"\s+", " ", query).strip().lower()
    unique = set(wanted)
    ranked: list[tuple[float, _Passage]] = []
    for score, chunk, doc in zip(scores, chunks, docs):
        if not score:
            continue
        heading_terms = set(terms(chunk.heading))
        score += 0.5 * len(unique & heading_terms)
        if len(phrase) > 3 and phrase in chunk.text.lower():
            score += 2.0
        ranked.append((score, chunk))
    ranked.sort(key=lambda item: (-item[0], item[1].start))
    return ranked


def select_passages(text: str, *, query: str | None = None, find: str | None = None) -> str:
    """Select relevant passages by exact phrase or lexical query relevance."""
    literal = re.sub(r"\s+", " ", (find or "")).strip()[:FIND_MAX_CHARS]
    if literal and len(literal) >= 2:
        chunks = _passage_chunks(text)
        selected: list[_Passage] = []
        low = text.lower()
        cursor = 0
        hits = 0
        while hits < FIND_MAX_MATCHES:
            index = low.find(literal.lower(), cursor)
            if index < 0:
                break
            hits += 1
            # A hit can start in one chunk and end in the next: the 1200-char
            # paragraph cut does not know where the phrase is.
            end = index + len(literal)
            selected.extend(chunk for chunk in chunks if chunk.start < end and index < chunk.end)
            cursor = index + max(1, len(literal))
        if not selected:
            return f"No literal match for {literal!r} in the extracted text.\nPage opening:\n{text[:FETCH_CHARS]}"
        return _render_passages(selected, FETCH_CHARS)
    if query:
        ranked = rank_passages(text, query)
        if not ranked:
            return text[:FETCH_CHARS]
        best = ranked[0][0]
        chosen = [chunk for score, chunk in ranked[:PASSAGE_MAX] if score >= 0.35 * best]
        return _render_passages(chosen, FETCH_CHARS)
    return text[:FETCH_CHARS]


def best_excerpt(text: str, query: str, budget: int) -> str:
    """The most query-relevant passages of a page, in page order, within budget.

    Falls back to the page opening when nothing matches (the lead of an
    article or doc page usually summarises it).
    """
    ranked = rank_passages(text, query)
    picked: list[_Passage] = []
    used = 0
    if ranked:
        best = ranked[0][0]
        for score, chunk in ranked[:3]:
            if score < 0.5 * best or (picked and used + len(chunk.text) > budget):
                continue
            picked.append(chunk)
            used += len(chunk.text)
    if not picked:
        opening = text[:budget]
        cut = opening.rfind("\n\n", budget // 2)
        return (opening[:cut] if cut > 0 and len(text) > budget else opening).strip()
    out = "\n…\n".join(chunk.text for chunk in _merge_passages(picked))
    return out[:budget].strip()


def find_windows(text: str, needle: str) -> str:
    normalized = re.sub(r"\s+", " ", (needle or "")).strip()[:FIND_MAX_CHARS]
    if len(normalized) < 2:
        return text[:FETCH_CHARS]
    return select_passages(text, find=normalized)


def _outline(text: str) -> list[tuple[int, int, str]]:
    out: list[tuple[int, int, str]] = []
    in_fence = ""
    pos = 0
    for line in text.split("\n"):
        fence = _FENCE_RE.match(line)
        if fence:
            if not in_fence:
                in_fence = fence.group(1)
            elif line.startswith(in_fence):
                in_fence = ""
        elif not in_fence:
            match = _OUTLINE_RE.match(line)
            if match:
                out.append((pos, len(match.group(1)), match.group(2).strip()[:120]))
        pos += len(line) + 1
    return out


def _place_links(full: str, links: list[dict]) -> list[dict]:
    """Attach the character offset where each link's label sits in full."""
    cursor = 0
    placed: list[dict] = []
    for link in links:
        label = link.get("text") or ""
        at = full.find(label, cursor) if label else -1
        if at < 0 and label:
            at = full.find(label)
        if at < 0:
            continue
        cursor = at + len(label)
        placed.append({**link, "at": at})
    return placed


def _spa_text(meta: dict[str, str]) -> str:
    title = meta.get("og_title") or meta.get("title") or ""
    desc = meta.get("og_description") or meta.get("description") or ""
    parts = []
    if title:
        parts.append(title)
    if desc:
        parts.append(desc)
    parts.append(
        "Little extractable HTML (likely a JavaScript app). The page was not read successfully; use other sources and do not invent contents."
    )
    return "\n".join(parts)


def _window(full: str, offset: int, size: int) -> tuple[str, bool]:
    """One page of text starting at offset; truncated means more follows."""
    start = min(max(0, offset), len(full))
    return full[start:start + size], len(full) > start + size


def _looks_html(raw: str) -> bool:
    head = raw.lstrip()[:200].lower()
    return head.startswith("<!doctype html") or head.startswith("<html") or (
        head.startswith("<") and "<body" in raw[:5000].lower()
    )


def extract_page(
    raw: str,
    *,
    content_type: str = "text/html",
    url: str = "",
    find: str | None = None,
    query: str | None = None,
    offset: int = 0,
) -> PageExtract:
    started = time.monotonic()
    offset = max(0, int(offset or 0))
    raw = raw or ""
    ctype = (content_type or "").lower()
    used_find = bool(find and len(re.sub(r"\s+", " ", find).strip()) >= 2)
    truncated = False
    kind = "html"
    title = ""
    text = ""
    links: list[dict] = []
    published = ""

    full = ""
    if "json" in ctype and "html" not in ctype:
        kind = "json"
        try:
            parsed = json.loads(raw)
            full = json.dumps(parsed, indent=2, ensure_ascii=False)
        except (json.JSONDecodeError, TypeError, ValueError, RecursionError):
            kind = "text"
            full = _clean_plain(raw)
        text, truncated = _window(full, offset, JSON_CHARS)
    elif _is_feed(raw, ctype):
        kind = "text"
        full = _feed_text(raw)
        text, truncated = _window(full, offset, FETCH_CHARS)
    elif "html" in ctype or _looks_html(raw):
        title, picked, links, meta, structured = _html_extract(raw, url)
        published = meta.get("published") or meta.get("modified") or ""
        if len(picked) < SPA_MIN_CHARS and not structured:
            kind = "spa"
            full = _spa_text(meta)
            text = full
            links = []
        else:
            kind = "html"
            if title and title.lower() not in picked[:400].lower():
                picked = f"# {title}\n\n{picked}"
            full = picked
            text, truncated = _window(full, offset, FETCH_CHARS)
    else:
        kind = "text"
        full = _clean_plain(raw)
        text, truncated = _window(full, offset, FETCH_CHARS)

    if used_find:
        source = full or text
        text = find_windows(source, find or "")
    elif query:
        source = full or text
        text = select_passages(source, query=query)

    paged = not used_find and not query and kind in ("html", "text", "json")
    start = min(offset, len(full)) if paged else 0
    if paged and full and offset >= len(full):
        text = ""
    elif not text.strip() and kind != "spa":
        kind = "empty"
    window_end = start + len(text)
    if not paged:
        text = text.strip()

    out = PageExtract(
        title=title,
        text=text,
        truncated=truncated,
        kind=kind,
        used_find=used_find,
        chars_in=len(raw),
        chars_out=len(text),
        links=_place_links(full, links) if kind == "html" else [],
        offset=start,
        total_chars=len(full),
        next_offset=window_end if paged and window_end < len(full) else None,
        paged=paged,
        full_text=full,
        published=published,
        outline=_outline(full) if kind in ("html", "text") else [],
    )
    host = ""
    try:
        host = (urlparse(url).hostname or "") if url else ""
    except Exception:
        host = ""
    logger.info(
        "fuse.extract url_host=%s chars_in=%s chars_out=%s kind=%s truncated=%s find=%s latency_ms=%s",
        host or "-",
        out.chars_in,
        out.chars_out,
        out.kind,
        int(out.truncated),
        int(out.used_find),
        int((time.monotonic() - started) * 1000),
    )
    return out
