from __future__ import annotations

import html as html_lib
import json
import logging
import re
import time
from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse, urlunparse

logger = logging.getLogger(__name__)

FETCH_MAX_BYTES = 512_000
# One page of extracted text per read_url call; pass offset to continue.
FETCH_CHARS = 16_000
FIND_MAX_CHARS = 80
FIND_WINDOW = 400
FIND_MAX_MATCHES = 8
JSON_CHARS = 16_000
CODE_BLOCK_CHARS = 1_200
TABLE_MAX_ROWS = 20
MAX_LINKS = 40
PASSAGE_CHARS = 1_000
PASSAGE_MAX = 6
MIN_MAIN_CHARS = 200
SPA_MIN_CHARS = 80

SKIP_TAGS = frozenset(
    {
        "script",
        "style",
        "noscript",
        "svg",
        "iframe",
        "object",
        "embed",
        "template",
        "canvas",
        "link",
    }
)
CHROME_TAGS = frozenset({"nav", "footer", "aside", "form", "button", "input", "select"})
VOID_TAGS = frozenset(
    {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }
)
# skip-marked void elements never carry a closing tag, so they must not
# enter the skip counter (a bare <link> in the body would otherwise
# swallow the rest of the document)
VOID_SKIP_TAGS = frozenset({"link", "embed", "meta"})
STACK_MAX = 64
CHROME_ROLES = frozenset({"navigation", "banner", "contentinfo", "complementary", "search"})
MAIN_TAGS = frozenset({"article", "main"})
MAIN_IDS = frozenset({"content", "main-content", "main"})
MAIN_CLASSES = frozenset({"post-content", "entry-content", "markdown-body", "prose"})
BLOCK_TAGS = frozenset({"p", "div", "section", "li", "blockquote", "pre", "h1", "h2", "h3", "h4", "h5", "h6", "tr"})
HEADING_TAGS = {"h1": "#", "h2": "##", "h3": "###", "h4": "####", "h5": "#####", "h6": "######"}

_TITLE_RE = re.compile(r"(?is)<title[^>]*>(.*?)</title>")
_META_RE = re.compile(r"(?is)<meta\s+([^>]+)/?>")
_ATTR_RE = re.compile(r"""(?is)([^\s=]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'>]+)))?""")
_LD_RE = re.compile(r"(?is)<script[^>]*type\s*=\s*['\"]application/ld\+json['\"][^>]*>(.*?)</script>")
_TAG_RE = re.compile(r"(?is)<[^>]+>")
_WS_RE = re.compile(r"[ \t\x0b\x0c]+")
_MULTI_NL = re.compile(r"\n{3,}")
_ARTICLE_TYPES = frozenset({"article", "newsarticle", "techarticle", "blogposting"})
_ARTICLE_HEADER_CLASSES = frozenset(
    {"article-header", "article__header", "entry-header", "post-header", "story-header"}
)
_WORD_RE = re.compile(r"[\w'-]+", re.UNICODE)
_STOP_WORDS = frozenset(
    {
        "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "how",
        "in", "is", "it", "of", "on", "or", "that", "the", "this", "to", "was",
        "what", "when", "where", "which", "who", "why", "with",
    }
)


@dataclass
class PageExtract:
    title: str
    text: str
    truncated: bool
    kind: str
    used_find: bool
    chars_in: int
    chars_out: int
    links: list[dict[str, str]] = field(default_factory=list)
    # Paging over the full extracted text (plain page mode only; find/query
    # passages are not paged). total_chars is the extracted length.
    offset: int = 0
    total_chars: int = 0
    next_offset: int | None = None
    paged: bool = False
    # Bytes the server sent for the page ("Received 12.3KB (200 OK)").
    bytes_in: int = 0


@dataclass(frozen=True)
class _Passage:
    start: int
    end: int
    text: str
    heading: str = ""


def _attr_map(raw: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for match in _ATTR_RE.finditer(raw or ""):
        key = (match.group(1) or "").lower()
        val = match.group(2) or match.group(3) or match.group(4) or ""
        out[key] = html_lib.unescape(val)
    return out


def _strip_tags(text: str) -> str:
    text = html_lib.unescape(_TAG_RE.sub(" ", text or ""))
    return re.sub(r"\s+", " ", text).strip()


def _meta_content(attrs: dict[str, str]) -> str:
    return (attrs.get("content") or "").strip()


def _collect_metadata(raw: str) -> dict[str, str]:
    prefix = raw[:80_000]
    meta = {"title": "", "description": "", "og_title": "", "og_description": "", "ld_headline": "", "ld_body": ""}
    title_m = _TITLE_RE.search(prefix)
    if title_m:
        meta["title"] = _strip_tags(title_m.group(1))[:200]
    for tag in _META_RE.finditer(prefix):
        attrs = _attr_map(tag.group(1))
        name = (attrs.get("name") or "").lower()
        prop = (attrs.get("property") or "").lower()
        content = _meta_content(attrs)
        if not content:
            continue
        if name == "description" and not meta["description"]:
            meta["description"] = content[:400]
        if prop == "og:title" and not meta["og_title"]:
            meta["og_title"] = content[:200]
        if prop == "og:description" and not meta["og_description"]:
            meta["og_description"] = content[:400]
    for block in _LD_RE.finditer(prefix):
        body, headline = _ld_article(block.group(1))
        if body and len(body) > len(meta["ld_body"]):
            meta["ld_body"] = body
            meta["ld_headline"] = headline
    return meta


def _ld_article(raw: str) -> tuple[str, str]:
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError, RecursionError):
        return "", ""
    nodes = data if isinstance(data, list) else [data]
    if isinstance(data, dict) and isinstance(data.get("@graph"), list):
        nodes = data["@graph"]
    best_body, best_head = "", ""
    for node in nodes:
        if not isinstance(node, dict):
            continue
        types = node.get("@type")
        if isinstance(types, str):
            types = [types]
        if not isinstance(types, list):
            continue
        if not any(str(t).lower() in _ARTICLE_TYPES for t in types):
            continue
        body = node.get("articleBody")
        if not isinstance(body, str) or not body.strip():
            continue
        body = _clean_fragment(body)
        headline = _strip_tags(str(node.get("headline") or ""))[:200]
        if len(body) > len(best_body):
            best_body, best_head = body, headline
    return best_body, best_head


class _Node:
    __slots__ = (
        "tag", "ident", "classes", "role", "itemprop", "href", "parts", "link_chars",
        "is_chrome", "is_main",
    )

    def __init__(self, tag: str, attrs: list[tuple[str, str | None]], *, in_article: bool = False):
        ad = {k.lower(): (v or "") for k, v in attrs}
        self.tag = tag
        self.ident = (ad.get("id") or "").lower()
        self.classes = set((ad.get("class") or "").lower().split())
        self.role = (ad.get("role") or "").lower()
        self.itemprop = (ad.get("itemprop") or "").lower()
        self.href = (ad.get("href") or "").strip()
        self.parts: list[str] = []
        self.link_chars = 0
        self.is_chrome = (
            tag in CHROME_TAGS
            or (
                tag == "header"
                and not in_article
                and not bool(self.classes & _ARTICLE_HEADER_CLASSES)
                and self.itemprop not in {"headline", "author", "datepublished", "datemodified"}
            )
            or self.role in CHROME_ROLES
            or "cookie" in self.ident
            or "cookie" in self.classes
        )
        self.is_main = (
            tag in MAIN_TAGS
            or self.ident in MAIN_IDS
            or bool(self.classes & MAIN_CLASSES)
            or self.role == "main"
            or self.itemprop == "articlebody"
        )

    def text(self) -> str:
        return "".join(self.parts)


class _ContentParser(HTMLParser):
    def __init__(self, *, base_url: str = "") -> None:
        super().__init__(convert_charrefs=True)
        self.skip = 0
        self.in_head = 0
        self.stack: list[_Node] = []
        self.candidates: list[_Node] = []
        self.body_parts: list[str] = []
        self.anchor = 0
        self.in_pre = 0
        self.in_code = 0
        self.table_rows: dict[_Node, int] = {}
        self.row_cells: list[str] = []
        self.in_td = 0
        self.base_url = base_url
        self.links: list[dict[str, str]] = []
        self._link_urls: set[str] = set()

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag == "head":
            self.in_head += 1
            return
        if self.in_head:
            return
        if tag in SKIP_TAGS:
            if tag not in VOID_SKIP_TAGS:
                self.skip += 1
            return
        if self.skip:
            return
        if tag == "br":
            self._emit("\n")
            return
        if tag == "img":
            alt = ""
            for k, v in attrs:
                if k.lower() == "alt" and v:
                    alt = v.strip()
                    break
            if alt:
                self._emit(alt + " ")
            return
        if tag in VOID_TAGS:
            return
        if tag == "a":
            self.anchor += 1
        if tag in ("pre",):
            self.in_pre += 1
        if tag == "code" and not self.in_pre:
            self.in_code += 1
        if tag == "tr":
            self.row_cells = []
        if tag in ("td", "th"):
            self.in_td += 1
        if len(self.stack) >= STACK_MAX:
            return
        node = _Node(
            tag,
            attrs,
            in_article=any(n.tag == "article" or n.itemprop == "articlebody" for n in self.stack),
        )
        self.stack.append(node)
        if tag == "table":
            self.table_rows[node] = 0

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag == "head" and self.in_head:
            self.in_head -= 1
            return
        if self.in_head:
            return
        if tag in SKIP_TAGS and self.skip and tag not in VOID_SKIP_TAGS:
            self.skip -= 1
            return
        if self.skip:
            return
        if tag == "a" and self.anchor:
            self.anchor -= 1
        if tag == "pre" and self.in_pre:
            self.in_pre -= 1
        if tag == "code" and self.in_code and not self.in_pre:
            self.in_code -= 1
        if tag in ("td", "th") and self.in_td:
            self.in_td -= 1
            cell = ""
            if self.stack and self.stack[-1].tag in ("td", "th"):
                cell = re.sub(r"\s+", " ", self.stack[-1].text()).strip()
            if cell:
                self.row_cells.append(cell)
        row_line: str | None = None
        if tag == "tr" and self.row_cells:
            table = next((node for node in reversed(self.stack) if node.tag == "table"), None)
            count = self.table_rows.get(table, 0) if table else 0
            if table is None or count < TABLE_MAX_ROWS:
                if table is not None:
                    self.table_rows[table] = count + 1
                row_line = " | ".join(self.row_cells)
            self.row_cells = []
        if not self.stack:
            return
        if self.stack[-1].tag != tag:
            return
        node = self.stack.pop()
        self._finish_node(node, row_line=row_line)
        if tag == "table":
            self.table_rows.pop(node, None)

    def _finish_node(self, node: _Node, *, row_line: str | None = None) -> None:
        tag = node.tag
        prefix = HEADING_TAGS.get(tag)
        text = node.text()
        if tag == "tr" and row_line is not None:
            # the flattened row replaces the raw cell text, which would
            # otherwise show up twice (once plain, once "a | b | c")
            node.parts = ["\n" + row_line + "\n\n"]
            text = "".join(node.parts)
        elif tag == "tr":
            # Each table is capped independently. Excess rows are omitted
            # rather than leaking their unflattened cell text.
            node.parts = []
            text = ""
        elif prefix and text.strip():
            heading = f"\n{prefix} {text.strip()[:200]}\n"
            node.parts = [heading]
            text = heading
        elif tag == "li":
            body = text.strip()
            node.parts = [f"\n- {body}\n"] if body else []
            text = "".join(node.parts)
        elif tag == "pre":
            body = text[:CODE_BLOCK_CHARS]
            node.parts = [f"\n{body}\n"]
            text = "".join(node.parts)
        elif tag in BLOCK_TAGS:
            node.parts = [text.strip() + "\n\n"] if text.strip() else []
            text = "".join(node.parts)
        if tag == "a" and node.href and not any(parent.is_chrome for parent in self.stack):
            self._collect_link(node.href, re.sub(r"\s+", " ", text).strip())
        if node.is_main and not node.is_chrome:
            self.candidates.append(node)
        if node.is_chrome:
            # drop nav/header/footer/aside/form/cookie subtrees entirely;
            # their text nested in lists would otherwise leak into the page
            return
        if self.stack:
            self.stack[-1].parts.extend(node.parts)
            self.stack[-1].link_chars += node.link_chars
        else:
            self.body_parts.extend(node.parts)

    def _collect_link(self, href: str, label: str) -> None:
        if len(self.links) >= MAX_LINKS:
            return
        href = html_lib.unescape(href).strip()
        if not href or href.startswith(("#", "javascript:", "data:", "mailto:", "tel:")):
            return
        absolute = urljoin(self.base_url, href) if self.base_url else href
        parsed = urlparse(absolute)
        if parsed.scheme and parsed.scheme not in {"http", "https"}:
            return
        if self.base_url and not parsed.netloc:
            return
        absolute = urlunparse(parsed._replace(fragment=""))
        base = urlunparse(urlparse(self.base_url)._replace(fragment="")) if self.base_url else ""
        if not absolute or absolute == base or absolute in self._link_urls:
            return
        self._link_urls.add(absolute)
        self.links.append({"url": absolute, "text": label[:200]})

    def close(self) -> None:
        try:
            super().close()
        except Exception:
            pass
        self._flush_open_nodes()

    def _flush_open_nodes(self) -> None:
        """Truncated downloads and malformed pages (arxiv, for one) leave
        elements open at EOF, so their text never reaches candidates or
        body_parts and the page gets misread as a JavaScript app. Merge the
        remaining stack upward exactly like handle_endtag would."""
        if not self.stack:
            return
        remaining = self.stack
        self.stack = []
        while len(remaining) > 1:
            child = remaining.pop()
            self.stack = remaining
            self._finish_node(child)
        self.stack = []
        root = remaining[0]
        self._finish_node(root)

    def handle_data(self, data):
        if self.skip or self.in_head or not data:
            return
        if self.in_pre or self.in_code:
            chunk = data[:CODE_BLOCK_CHARS]
        else:
            chunk = _WS_RE.sub(" ", data)
        if not chunk:
            return
        self._emit(chunk)
        if self.anchor:
            n = len(chunk.strip())
            if self.stack:
                self.stack[-1].link_chars += n

    def handle_comment(self, data):
        return

    def _emit(self, chunk: str) -> None:
        if self.stack:
            top = self.stack[-1]
            if top.is_chrome:
                return
            top.parts.append(chunk)
        else:
            self.body_parts.append(chunk)


def _collapse(text: str) -> str:
    text = _WS_RE.sub(" ", text)
    text = _MULTI_NL.sub("\n\n", text)
    return text.strip()


def _clean_fragment(fragment: str) -> str:
    """Apply normal script/chrome/tag cleanup to structured article HTML."""
    parser = _ContentParser()
    try:
        parser.feed(fragment or "")
        parser.close()
    except Exception:
        pass
    cleaned = _collapse("".join(parser.body_parts))
    return cleaned or _collapse(_strip_tags(fragment or ""))


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
    heading_start = 0
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
            heading_start = match.start(1)
            continue
        rendered = block
        if heading and not parts:
            rendered = heading + "\n\n" + block
        projected = len("\n\n".join(parts + [rendered]))
        if parts and projected > max_chars:
            chunks.append(_Passage(start, end, "\n\n".join(parts), chunk_heading))
            start, parts, chunk_heading = -1, [], ""
            rendered = (heading + "\n\n" if heading else "") + block
        if start < 0:
            start = match.start(1)
            chunk_heading = heading
        end = match.end(1)
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
            text = prior.text + ("\n\n" if addition else "") + addition
            merged[-1] = _Passage(prior.start, max(prior.end, passage.end), text, prior.heading)
        else:
            merged.append(passage)
    return merged


def select_passages(text: str, *, query: str | None = None, find: str | None = None) -> str:
    """Select relevant passages by exact phrase or lexical query relevance."""
    chunks = _passage_chunks(text)
    literal = re.sub(r"\s+", " ", (find or "")).strip()[:FIND_MAX_CHARS]
    selected: list[_Passage] = []
    if literal and len(literal) >= 2:
        low = text.lower()
        cursor = 0
        while len(selected) < FIND_MAX_MATCHES:
            index = low.find(literal.lower(), cursor)
            if index < 0:
                break
            selected.extend(chunk for chunk in chunks if chunk.start <= index < chunk.end)
            cursor = index + max(1, len(literal))
        if not selected:
            return f"No literal match for {literal!r} in the extracted text.\nPage opening:\n{text[:FETCH_CHARS]}"
    elif query:
        phrase = re.sub(r"\s+", " ", query).strip().lower()
        terms = [word for word in _WORD_RE.findall(phrase) if len(word) > 1 and word not in _STOP_WORDS]
        if not terms:
            return text[:FETCH_CHARS]
        scored: list[tuple[float, int, _Passage]] = []
        for chunk in chunks:
            low = chunk.text.lower()
            words = _WORD_RE.findall(low)
            score = sum(words.count(term) for term in terms)
            score += 3 * sum(1 for term in set(terms) if term in (chunk.heading or "").lower())
            if phrase and phrase in low:
                score += 8
            if score:
                scored.append((score / max(1.0, len(words) ** 0.25), -chunk.start, chunk))
        selected = [item[2] for item in sorted(scored, reverse=True)[:PASSAGE_MAX]]
        if not selected:
            return text[:FETCH_CHARS]
    else:
        return text[:FETCH_CHARS]
    return "\n---\n".join(item.text for item in _merge_passages(selected))[:FETCH_CHARS]


def find_windows(text: str, needle: str) -> str:
    normalized = re.sub(r"\s+", " ", (needle or "")).strip()[:FIND_MAX_CHARS]
    if len(normalized) < 2:
        return text[:FETCH_CHARS]
    return select_passages(text, find=normalized)


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
    links: list[dict[str, str]] = []

    full = ""
    if "json" in ctype and "html" not in ctype:
        kind = "json"
        try:
            parsed = json.loads(raw)
            full = json.dumps(parsed, indent=2, ensure_ascii=False)
        except (json.JSONDecodeError, TypeError, ValueError):
            kind = "text"
            full = re.sub(r"\s+", " ", raw).strip()
        text, truncated = _window(full, offset, JSON_CHARS)
    elif "html" in ctype or raw.lstrip()[:15].lower().startswith("<!doctype html") or raw.lstrip()[:6].lower().startswith("<html"):
        meta = _collect_metadata(raw)
        title = meta.get("og_title") or meta.get("title") or ""
        parser = _ContentParser(base_url=url)
        try:
            parser.feed(raw)
        except Exception:
            pass
        try:
            parser.close()
        except Exception:
            pass
        links = parser.links
        picked = ""
        ld_body = meta.get("ld_body") or ""
        if len(ld_body) >= MIN_MAIN_CHARS:
            head = meta.get("ld_headline") or title
            picked = (f"# {head}\n\n" if head else "") + ld_body
        if not picked:
            ranked = []
            for node in parser.candidates:
                body = _collapse("".join(node.parts))
                if not body:
                    continue
                # Prefer a page's explicit main region when its density is
                # effectively tied with a nested article. The wrapper often
                # carries useful repository/article metadata immediately
                # before the article body.
                score = len(body) - 2 * node.link_chars + (100 if node.tag == "main" else 0)
                ranked.append((score, len(body), body))
            ranked.sort(reverse=True)
            long = [body for score, length, body in ranked if length >= MIN_MAIN_CHARS]
            if long:
                picked = long[0]
            elif ranked:
                picked = ranked[0][2]
        if not picked:
            picked = _collapse("".join(parser.body_parts))
        structured = bool(parser.candidates) or bool(ld_body)
        if len(picked) < SPA_MIN_CHARS and not structured:
            kind = "spa"
            full = _spa_text(meta)
            text = full
        else:
            kind = "html"
            if title and title.lower() not in picked[:400].lower():
                picked = f"# {title}\n\n{picked}"
            full = picked
            text, truncated = _window(full, offset, FETCH_CHARS)
    else:
        kind = "text"
        full = re.sub(r"\s+", " ", raw).strip()
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

    out = PageExtract(
        title=title,
        text=text.strip(),
        truncated=truncated,
        kind=kind,
        used_find=used_find,
        chars_in=len(raw),
        chars_out=len(text),
        links=links,
        offset=start,
        total_chars=len(full),
        next_offset=window_end if paged and window_end < len(full) else None,
        paged=paged,
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
