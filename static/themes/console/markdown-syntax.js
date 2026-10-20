// markdown-syntax presentation. Dependencies are live getters supplied by index.js.
export function create(deps) {
function citeHost(url) {
    try {
      return new URL(url).hostname.replace(/^www\./, "") || url;
    } catch (_) {
      return url;
    }
  }

function citeUrlFromPayload(payload, catalog) {
    const parts = String(payload || "")
      .split(/\uE202/)
      .map((part) => part.trim())
      .filter(Boolean);
    for (const part of parts) {
      const url = deps.safeUrl(part);
      if (url && /^https?:/i.test(url)) return url;
    }
    const joined = parts.join(" ");
    const indexed = joined.match(/turn\d+(?:search|news|ref)(\d+)/i) || joined.match(/(?:search|news|ref)(\d+)/i);
    if (indexed && catalog && catalog.length) {
      const url = catalog[Number(indexed[1])];
      if (url) return url;
    }
    return "";
  }

function rewriteCites(text, catalog, streaming) {
    let out = String(text || "").replace(/[\uE200\uE206]cite\uE202([^\uE201]*)\uE201/gi, (_, payload) => {
      const url = citeUrlFromPayload(payload, catalog);
      return url ? `[${citeHost(url)}](${url})` : "";
    });
    if (streaming) out = out.replace(/[\uE200\uE206]cite(?:\uE202[^\uE201]*)?$/i, "");
    else out = out.replace(/[\uE200-\uE20F]/g, "");
    out = out.replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)(?:\s+\2)?/g, "[$1]($2)");
    return out;
  }

function linkHtml(url, label, cls) {
    const art = url.match(/^\/a\/([A-Za-z0-9_-]+)$/);
    if (art) {
      return `<a class="chat-md-artifact" href="${deps.escapeHtml(url)}" data-open-artifact="${deps.escapeHtml(art[1])}">${label}</a>`;
    }
    let klass = cls || "";
    if (!klass && /^https?:/i.test(url)) {
      try {
        const host = new URL(url).hostname.replace(/^www\./, "");
        if (label === host || label === url) klass = "chat-md-cite";
      } catch (_) {}
    }
    const attr = klass ? ` class="${klass}"` : "";
    const extra = /^mailto:/i.test(url) ? "" : ' target="_blank" rel="noopener noreferrer"';
    return `<a${attr} href="${deps.escapeHtml(url)}"${extra}>${label}</a>`;
  }

function autolinkHtml(src) {
    const held = [];
    let out = String(src || "").replace(/<a\b[^>]*>[\s\S]*?<\/a>|<img\b[^>]*>/gi, (tag) => {
      held.push(tag);
      return `\uE000L${held.length - 1}\uE001`;
    });
    out = out.replace(/(^|[\s>(])((?:https?:\/\/|mailto:)[^\s<]+)(?=$|[\s<])/gi, (all, lead, raw) => {
      let href = raw.replace(/&amp;/g, "&");
      let trail = "";
      while (/[),.;:!?]$/.test(href) && !/[/)]]$/.test(href)) {
        trail = href.slice(-1) + trail;
        href = href.slice(0, -1);
      }
      const url = deps.safeUrl(href);
      if (!url) return all;
      return lead + linkHtml(url, deps.escapeHtml(href)) + trail;
    });
    return out.replace(/\uE000L(\d+)\uE001/g, (_, i) => held[Number(i)] || "");
  }

function renderInline(text) {
    const codes = [];
    let src = String(text || "").replace(/`([^`]+)`/g, (_, code) => {
      codes.push(code);
      return `\uE000C${codes.length - 1}\uE001`;
    });
    src = deps.escapeHtml(src);
    // Remote images are CSP-blocked (img-src 'self' data:), so image syntax
    // renders as a link to the source instead of a permanently broken <img>.
    src = src.replace(/!\[([^\]]*)\]\(([^)]+)\)/g, (_, alt, href) => {
      const url = deps.safeUrl(href.replace(/&amp;/g, "&"));
      if (!url || !/^https?:/i.test(url)) return alt;
      return linkHtml(url, alt || deps.escapeHtml(url));
    });
    src = src.replace(/\[([^\]]+)\]\(([^)]+)\)/g, (_, label, href) => {
      const url = deps.safeUrl(href.replace(/&amp;/g, "&"));
      if (!url) return label;
      return linkHtml(url, label);
    });
    src = autolinkHtml(src);
    const emphasis = (value) => value
      .replace(/~~([^~]+)~~/g, "<del>$1</del>")
      .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
      .replace(/__([^_]+)__/g, "<strong>$1</strong>")
      .replace(/(^|[^\w])\*([^*\n]+)\*(?!\*)/g, "$1<em>$2</em>")
      .replace(/(^|[^\w])_([^_\n]+)_(?!_)/g, "$1<em>$2</em>");
    const links = [];
    src = src.replace(/(<a\b[^>]*>)([\s\S]*?)<\/a>/gi, (_, opening, label) => {
      const href = (opening.match(/\bhref="([^"]*)"/) || [])[1];
      links.push(opening + (label === href ? label : emphasis(label)) + "</a>");
      return `\uE000L${links.length - 1}\uE001`;
    });
    src = emphasis(src);
    src = src.replace(/\uE000L(\d+)\uE001/g, (_, i) => links[Number(i)] || "");
    src = src.replace(/\uE000C(\d+)\uE001/g, (_, i) => `<code>${deps.escapeHtml(codes[Number(i)] || "")}</code>`);
    return src;
  }

function trimFence(code) {
    const lines = String(code || "").replace(/^\n+/, "").replace(/\n+$/, "").split("\n");
    let pad = null;
    for (const line of lines) {
      if (!line.trim()) continue;
      const n = (line.match(/^[ \t]*/) || [""])[0].length;
      pad = pad === null ? n : Math.min(pad, n);
      if (pad === 0) break;
    }
    if (!pad) return lines.join("\n");
    return lines.map((line) => line.slice(pad)).join("\n");
  }

function codeLanguage(lang) {
    const name = String(lang || "").toLowerCase().split(/\s+/)[0];
    const aliases = {
      "c++": "cpp", cc: "cpp", cxx: "cpp", hpp: "cpp",
      cs: "csharp", html: "xml", py3: "python", python3: "python",
      jsonc: "json", node: "javascript", postgres: "sql", postgresql: "sql",
      sqlite: "sql", console: "shell", patch: "diff", udiff: "diff",
    };
    return aliases[name] || name;
  }

function highlightCode(lang, code) {
    const source = String(code || "");
    if (deps.detailProbe) return deps.escapeHtml(source);
    const language = codeLanguage(lang);
    if (!window.hljs || !language || source.length > 100000 || !window.hljs.getLanguage(language)) {
      return deps.escapeHtml(source);
    }
    const key = language + "\u0000" + source;
    const cached = _hljsCache.get(key);
    if (cached !== undefined) return cached;
    let html;
    try {
      html = window.hljs.highlight(source, { language, ignoreIllegals: true }).value;
    } catch (_) {
      return deps.escapeHtml(source);
    }
    if (_hljsCache.size >= _HLJS_CACHE_MAX) {
      _hljsCache.clear();
    }
    _hljsCache.set(key, html);
    return html;
  }

function renderFence(lang, code) {
    const label = deps.escapeHtml((lang || "code").split(/\s+/)[0] || "code");
    return `<div class="chat-code"><div class="chat-code-head"><span>${label}</span><button type="button" class="chat-icon-btn" data-copy-code title="Copy" aria-label="Copy">${ACTION_ICONS.copy}</button></div><pre><code>${highlightCode(lang, trimFence(code))}</code></pre></div>`;
  }

function parseTable(block) {
    const lines = block.split("\n").filter((line) => line.trim());
    if (lines.length < 2 || !lines[0].includes("|")) return "";
    const cells = (line) =>
      line
        .replace(/^\s*\|/, "")
        .replace(/\|\s*$/, "")
        .split("|")
        .map((cell) => cell.trim());
    const isSep = (line) => /^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$/.test(line) || /^\s*\|?\s*:?-+\s*$/.test(line);
    const looksRow = (line) => line.includes("|") && !isSep(line) && cells(line).some(Boolean);
    if (!looksRow(lines[0])) return "";
    let bodyStart = 1;
    if (isSep(lines[1])) bodyStart = 2;
    else if (!lines.slice(1).every(looksRow)) return "";
    const head = cells(lines[0]);
    const rows = lines.slice(bodyStart).filter(looksRow).map(cells);
    if (!head.length || !rows.length) return "";
    const thead = `<tr>${head.map((cell) => `<th>${renderInline(cell)}</th>`).join("")}</tr>`;
    const tbody = rows.map((row) => `<tr>${row.map((cell) => `<td>${renderInline(cell)}</td>`).join("")}</tr>`).join("");
    return `<table><thead>${thead}</thead><tbody>${tbody}</tbody></table>`;
  }

function parseHeadingLine(line) {
    const match = String(line || "").match(/^\s{0,3}(#{1,6})\s+(.+?)\s*$/);
    if (!match) return null;
    const title = match[2].replace(/[ \t]#+\s*$/, "").replace(/`+$/, "").trim();
    if (!title) return null;
    return { level: match[1].length, title };
  }

function isFenceLang(info) {
    const first = String(info || "")
      .trim()
      .split(/\s+/)[0] || "";
    if (!first) return true;
    if (/^#{1,6}$/.test(first) || first.startsWith("#")) return false;
    return /^[A-Za-z][\w+#.-]*$/.test(first);
  }

function renderHeading(parsed) {
    return `<h${parsed.level}>${renderInline(parsed.title)}</h${parsed.level}>`;
  }

function parseListMarker(line) {
    const match = String(line || "").match(/^(\s*)([-*+]|\d+\.)\s+(.*)$/);
    if (!match) return null;
    const indent = match[1].replace(/\t/g, "  ").length;
    const ordered = /^\d+\./.test(match[2]);
    return { indent, ordered, number: ordered ? parseInt(match[2], 10) : 0, text: match[3] };
  }

function mergeListBlocks(blocks) {
    const out = [];
    blocks.forEach((block) => {
      const prev = out[out.length - 1];
      const lines = block.trim().split("\n");
      if (prev !== undefined && block.trim() && looksLikeListBlock(prev.trim().split("\n")) &&
          (parseListMarker(lines[0]) || /^\s{2,}\S/.test(block.replace(/^\n+/, ""))) &&
          looksLikeListBlock([...prev.trim().split("\n"), ...block.replace(/^\n+/, "").split("\n")])) {
        out[out.length - 1] = prev.replace(/\s+$/, "") + "\n" + block.replace(/^\n+/, "");
        return;
      }
      out.push(block);
    });
    return out;
  }

function looksLikeListBlock(lines) {
    return lines.some((line) => parseListMarker(line)) &&
      lines.every((line) => !line.trim() || parseListMarker(line) || /^\s{2,}\S/.test(line));
  }

function renderList(lines) {
    const items = [];
    lines.forEach((line) => {
      const marker = parseListMarker(line);
      if (marker) {
        items.push({ indent: marker.indent, ordered: marker.ordered, number: marker.number, parts: [marker.text] });
        return;
      }
      if (items.length && line.trim()) items[items.length - 1].parts.push(line.trim());
    });
    if (!items.length) return "";
    const rootIndent = Math.min(...items.map((item) => item.indent));
    const stack = [];
    let html = "";
    const closeTo = (indent) => {
      while (stack.length && stack[stack.length - 1].indent >= indent) {
        html += "</li></" + stack.pop().tag + ">";
      }
    };
    const open = (tag, item) => (tag === "ol" && item.number > 1 ? `<ol start="${item.number}">` : `<${tag}>`);
    items.forEach((item) => {
      const indent = item.indent < rootIndent ? rootIndent : item.indent;
      const tag = item.ordered ? "ol" : "ul";
      const body = renderInline(item.parts.join(" "));
      if (!stack.length) {
        html += `${open(tag, item)}<li>${body}`;
        stack.push({ indent, tag });
        return;
      }
      const top = stack[stack.length - 1];
      if (indent > top.indent) {
        html += `${open(tag, item)}<li>${body}`;
        stack.push({ indent, tag });
        return;
      }
      closeTo(indent + 1);
      const parent = stack[stack.length - 1];
      if (!parent || parent.indent !== indent) {
        html += `${open(tag, item)}<li>${body}`;
        stack.push({ indent, tag });
        return;
      }
      if (parent.tag !== tag) {
        html += `</li></${parent.tag}>${open(tag, item)}<li>${body}`;
        parent.tag = tag;
        return;
      }
      html += `</li><li>${body}`;
    });
    while (stack.length) html += "</li></" + stack.pop().tag + ">";
    return html;
  }

function renderQuote(trimmed) {
    const lines = trimmed.split("\n").map((line) => line.replace(/^>\s?/, ""));
    const alert = String(lines[0] || "").match(/^\[!(NOTE|TIP|IMPORTANT|WARNING|CAUTION)\]\s*(.*)$/i);
    if (alert) {
      const kind = alert[1].toLowerCase();
      const rest = [alert[2], ...lines.slice(1)]
        .map((line) => String(line || "").replace(/^\[!(NOTE|TIP|IMPORTANT|WARNING|CAUTION)\]\s*/i, ""))
        .join("\n")
        .trim();
      const body = rest ? renderInline(rest).replace(/\n/g, "<br>") : "";
      return `<blockquote class="chat-md-alert is-${kind}"><span class="chat-md-alert-label">${MD_ALERTS[kind]}</span>${body ? `<p>${body}</p>` : ""}</blockquote>`;
    }
    return `<blockquote>${renderInline(lines.join("\n")).replace(/\n/g, "<br>")}</blockquote>`;
  }

function renderFencedMarkdown(text, catalog, streaming) {
    const src = String(text || "");
    let html = "";
    let i = 0;
    while (i < src.length) {
      const start = src.indexOf("```", i);
      if (start === -1) {
        html += deps.renderBlocks(src.slice(i), catalog, streaming);
        break;
      }
      html += deps.renderBlocks(src.slice(i, start), catalog, streaming);
      const nl = src.indexOf("\n", start + 3);
      const lang = (nl === -1 ? src.slice(start + 3) : src.slice(start + 3, nl)).trim();
      const bodyStart = nl === -1 ? src.length : nl + 1;
      const closing = /^ {0,3}`{3,}[ \t]*(?:\r?$)/gm;
      closing.lastIndex = bodyStart;
      const close = closing.exec(src);
      const end = close ? close.index : -1;
      if (!isFenceLang(lang)) {
        const inner = (lang ? lang + "\n" : "") + src.slice(bodyStart, end === -1 ? src.length : end);
        html += deps.renderBlocks(inner.replace(/```\s*$/, ""), catalog, streaming);
        if (end === -1) break;
        i = end + close[0].length;
        if (src[i] === "\n") i += 1;
        continue;
      }
      if (end === -1) {
        html += renderFence(lang, src.slice(bodyStart));
        break;
      }
      html += renderFence(lang, src.slice(bodyStart, end));
      i = end + close[0].length;
      if (src[i] === "\n") i += 1;
    }
    return html;
  }

function renderDetailsBlocks(text, catalog, streaming) {
    const src = String(text || "");
    // Only block-position tags are structural. Inline code, prose examples and
    // fenced HTML must never pair with a closing tag later in the answer.
    const roots = [], stack = [];
    let offset = 0, fence = null;
    for (const line of src.split("\n")) {
      const marker = line.match(/^ {0,3}(`{3,}|~{3,})(.*)$/);
      if (fence) {
        if (marker && marker[1][0] === fence[0] && marker[1].length >= fence.length && !marker[2].trim()) fence = null;
      } else if (marker) {
        fence = marker[1];
      } else {
        const opening = line.match(/^ {0,3}<details(\s+open)?\s*>(?=\s*(?:<summary>|$))/i);
        if (opening && stack.length < 32) {
          const node = { start: offset, body: offset + opening[0].length, open: Boolean(opening[1]), children: [] };
          (stack.length ? stack[stack.length - 1].children : roots).push(node);
          stack.push(node);
        }
        // A close may follow an inline summary/body, but never a backtick
        // example or a tag mentioned in ordinary prose.
        const close = line.match(/<\/details>\s*$/i);
        if (close && stack.length && (opening || /^ {0,3}<\/details>\s*$/i.test(line))) {
          const node = stack.pop();
          node.close = offset + close.index;
          node.end = node.close + 10;
        }
      }
      offset += line.length + 1;
    }
    const renderRange = (start, end, nodes) => {
      let html = "", cursor = start;
      for (const node of nodes) {
        if (node.end == null) continue; // Incomplete streamed markup stays literal.
        html += renderFencedMarkdown(src.slice(cursor, node.start), catalog, streaming);
        const inner = src.slice(node.body, node.close);
        const summaryMatch = inner.match(/^\s*<summary>([^\n]*?)<\/summary>[ \t]*\n?/i);
        const summary = summaryMatch ? renderInline(summaryMatch[1].trim()) : "Details";
        const bodyStart = node.body + (summaryMatch ? summaryMatch[0].length : 0);
        html += `<details class="chat-md-details" data-md-details="${node.start}"${node.open ? " open" : ""}><summary>${summary || "Details"}</summary>${renderRange(bodyStart, node.close, node.children)}</details>`;
        cursor = node.end;
      }
      return html + renderFencedMarkdown(src.slice(cursor, end), catalog, streaming);
    };
    return renderRange(0, src.length, roots);
  }

function looksLikeDiff(text) {
    const lines = String(text || "").split("\n");
    if (lines.length < 2) return false;
    const hits = lines.filter((line) => /^(@@ |\+\+\+ |--- |diff --git |index |\+[^+]|-[^-])/.test(line)).length;
    return hits >= 2;
  }

function renderAnsi(text) {
    const src = String(text || "");
    if (!/\x1b\[/.test(src)) return deps.escapeHtml(src);
    let html = "";
    let i = 0;
    let bold = false;
    let underline = false;
    let color = "";
    const open = () => {
      const cls = [bold ? "is-bold" : "", underline ? "is-underline" : "", color ? "ansi-" + color : ""].filter(Boolean);
      return cls.length ? `<span class="${cls.join(" ")}">` : "";
    };
    let wrap = open();
    const flush = (chunk) => {
      if (!chunk) return;
      html += wrap ? wrap + deps.escapeHtml(chunk) + "</span>" : deps.escapeHtml(chunk);
    };
    while (i < src.length) {
      const esc = src.indexOf("\x1b[", i);
      if (esc === -1) {
        flush(src.slice(i));
        break;
      }
      flush(src.slice(i, esc));
      const end = src.indexOf("m", esc + 2);
      if (end === -1 || end - esc > 24) {
        flush(src.slice(esc, esc + 1));
        i = esc + 1;
        continue;
      }
      const codes = src.slice(esc + 2, end).split(";").map((n) => Number(n) || 0);
      codes.forEach((code) => {
        if (code === 0) { bold = false; underline = false; color = ""; }
        else if (code === 1) bold = true;
        else if (code === 4) underline = true;
        else if (code === 22) bold = false;
        else if (code === 24) underline = false;
        else if (code === 39) color = "";
        else if (ANSI_COLORS[code]) color = ANSI_COLORS[code];
      });
      wrap = open();
      i = end + 1;
    }
    return html;
  }

const ACTION_ICONS = {
    copy: '<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><rect x="5.5" y="5.5" width="7" height="8" rx="1.3" fill="none" stroke="currentColor" stroke-width="1.4"/><path d="M10.5 5.5V4.2A1.2 1.2 0 0 0 9.3 3H4.2A1.2 1.2 0 0 0 3 4.2v7.1A1.2 1.2 0 0 0 4.2 12.5H5.5" fill="none" stroke="currentColor" stroke-width="1.4"/></svg>',
    copied: '<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><path d="M3.6 8.6l3 3 5.8-6.6" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    edit: '<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><path d="M9.2 3.4l3.4 3.4L6 13.4H2.6v-3.4zM8.1 4.5l3.4 3.4" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round"/></svg>',
    retry: '<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><path d="M12.6 8A4.6 4.6 0 1 1 11.3 4.2" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/><path d="M11.2 2.4v2.6h-2.6" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    more: '<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><circle cx="4" cy="8" r="1.05" fill="currentColor"/><circle cx="8" cy="8" r="1.05" fill="currentColor"/><circle cx="12" cy="8" r="1.05" fill="currentColor"/></svg>',
    rewind: '<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><path d="M3.4 8a4.6 4.6 0 1 0 1.4-3.3" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/><path d="M3.2 3.4v2.7h2.7" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  };

const _hljsCache = new Map();

const _HLJS_CACHE_MAX = 600;

const MD_ALERTS = { note: "Note", tip: "Tip", important: "Important", warning: "Warning", caution: "Caution" };

const ANSI_COLORS = { 30: "black", 31: "red", 32: "green", 33: "yellow", 34: "blue", 35: "magenta", 36: "cyan", 37: "white", 90: "bright-black", 91: "bright-red", 92: "bright-green", 93: "bright-yellow", 94: "bright-blue", 95: "bright-magenta", 96: "bright-cyan", 97: "bright-white" };

  return { citeHost, citeUrlFromPayload, rewriteCites, linkHtml, autolinkHtml, renderInline, trimFence, codeLanguage, highlightCode, renderFence, parseTable, parseHeadingLine, isFenceLang, renderHeading, parseListMarker, mergeListBlocks, looksLikeListBlock, renderList, renderQuote, renderFencedMarkdown, renderDetailsBlocks, looksLikeDiff, renderAnsi, ACTION_ICONS, _hljsCache, _HLJS_CACHE_MAX, MD_ALERTS, ANSI_COLORS };
}
