// markdown-stream presentation. Dependencies are live getters supplied by index.js.
export function create(deps) {
function renderChunk(trimmed) {
    if (!trimmed) return "";
    const table = deps.parseTable(trimmed);
    if (table) return table;
    if (/^[-*_]{3,}$/.test(trimmed)) return "<hr>";
    const heading = deps.parseHeadingLine(trimmed);
    if (heading && !trimmed.includes("\n")) return deps.renderHeading(heading);
    const quoteLines = trimmed.split("\n");
    if (quoteLines.every((line) => /^>\s?/.test(line) || !line.trim())) return deps.renderQuote(trimmed);
    const lines = trimmed.split("\n");
    if (deps.looksLikeListBlock(lines)) {
      const list = deps.renderList(lines);
      if (list) return list;
    }
    const mixed = splitParagraphLists(lines);
    if (mixed) return mixed;
    return `<p>${deps.renderInline(lines.join("\n")).replace(/\n/g, "<br>")}</p>`;
  }

function splitParagraphLists(lines) {
    const opens = (line) => {
      const marker = deps.parseListMarker(line);
      return Boolean(marker && (!marker.ordered || marker.number === 1));
    };
    if (!lines.some((line, index) => index > 0 && opens(line))) return "";
    let html = "";
    let para = [];
    let list = [];
    const flushPara = () => {
      if (para.length) html += `<p>${deps.renderInline(para.join("\n")).replace(/\n/g, "<br>")}</p>`;
      para = [];
    };
    const flushList = () => {
      if (list.length) html += deps.renderList(list);
      list = [];
    };
    lines.forEach((line) => {
      if (list.length && (deps.parseListMarker(line) || /^\s{2,}\S/.test(line))) { list.push(line); return; }
      if (opens(line)) { flushPara(); list.push(line); return; }
      flushList();
      para.push(line);
    });
    flushList();
    flushPara();
    return html;
  }

function renderBlocks(text, catalog, streaming) {
    return deps.mergeListBlocks(deps.rewriteCites(String(text || ""), catalog, streaming).split(/\n{2,}/))
      .map((block) => {
        const trimmed = block.trim();
        if (!trimmed) return "";
        const table = deps.parseTable(trimmed);
        if (table) return table;
        const lines = trimmed.split("\n");
        if (!lines.some((line) => deps.parseHeadingLine(line))) return renderChunk(trimmed);
        let html = "";
        let buf = [];
        const flush = () => {
          if (!buf.length) return;
          html += renderChunk(buf.join("\n"));
          buf = [];
        };
        lines.forEach((line) => {
          const heading = deps.parseHeadingLine(line);
          if (heading) {
            flush();
            html += deps.renderHeading(heading);
            return;
          }
          buf.push(line);
        });
        flush();
        return html;
      })
      .join("");
  }

function renderMarkdown(src, streaming, catalog) {
    if (deps.detailProbe) return deps.escapeHtml(String(src || ""));
    const text = String(src || "").replace(/\r\n/g, "\n");
    return deps.renderDetailsBlocks(text, catalog, streaming) || (streaming ? "" : "<p></p>");
  }

function patchMarkdown(target, html) {
    patchMarkdownFrom(target, html, 0);
  }

function patchMarkdownFrom(target, html, skipCount) {
    const template = document.createElement("template");
    template.innerHTML = html;
    const sync = (parent, source) => {
      let current = parent.firstChild;
      for (const fresh of [...source.childNodes]) {
        if (!current) { parent.appendChild(fresh.cloneNode(true)); continue; }
        const next = current.nextSibling;
        if (!current.isEqualNode(fresh)) {
          if (current.nodeType === Node.TEXT_NODE && fresh.nodeType === Node.TEXT_NODE) {
            if (fresh.data.startsWith(current.data)) current.appendData(fresh.data.slice(current.data.length));
            else current.data = fresh.data;
          } else if (current.nodeType === Node.ELEMENT_NODE && fresh.nodeType === Node.ELEMENT_NODE && current.tagName === fresh.tagName) {
            const keepOpen = current.tagName === "DETAILS" && current.dataset.mdDetails === fresh.dataset.mdDetails;
            for (const attr of [...current.attributes]) if (!(keepOpen && attr.name === "open") && !fresh.hasAttribute(attr.name)) current.removeAttribute(attr.name);
            for (const attr of [...fresh.attributes]) if (!(keepOpen && attr.name === "open") && current.getAttribute(attr.name) !== attr.value) current.setAttribute(attr.name, attr.value);
            sync(current, fresh);
          } else parent.replaceChild(fresh.cloneNode(true), current);
        }
        current = next;
      }
      while (current) { const next = current.nextSibling; current.remove(); current = next; }
    };
    if (skipCount > 0) {
      // The frozen prefix is never re-diffed: isEqualNode is a deep compare,
      // so touching it would reintroduce the O(message) per-event cost this
      // splitting exists to remove.
      const frozen = target.childNodes[skipCount - 1];
      if (!frozen) { patchMarkdown(target, html); return; }
      let current = frozen.nextSibling;
      for (const fresh of [...template.content.childNodes]) {
        if (!current) { target.appendChild(fresh.cloneNode(true)); continue; }
        const next = current.nextSibling;
        if (!current.isEqualNode(fresh)) {
          if (current.nodeType === Node.TEXT_NODE && fresh.nodeType === Node.TEXT_NODE) {
            if (fresh.data.startsWith(current.data)) current.appendData(fresh.data.slice(current.data.length));
            else current.data = fresh.data;
          } else if (current.nodeType === Node.ELEMENT_NODE && fresh.nodeType === Node.ELEMENT_NODE && current.tagName === fresh.tagName) {
            const keepOpen = current.tagName === "DETAILS" && current.dataset.mdDetails === fresh.dataset.mdDetails;
            for (const attr of [...current.attributes]) if (!(keepOpen && attr.name === "open") && !fresh.hasAttribute(attr.name)) current.removeAttribute(attr.name);
            for (const attr of [...fresh.attributes]) if (!(keepOpen && attr.name === "open") && current.getAttribute(attr.name) !== attr.value) current.setAttribute(attr.name, attr.value);
            sync(current, fresh);
          } else target.replaceChild(fresh.cloneNode(true), current);
        }
        current = next;
      }
      while (current) { const next = current.nextSibling; current.remove(); current = next; }
      return;
    }
    sync(target, template.content);
  }

function mdStableBoundary(text) {
    // Disclosure node IDs use document-relative offsets. Keep disclosures
    // on the full renderer until the parser exposes stable block identities.
    if (/<\/?details\b/i.test(text)) return 0;
    let fence = false, boundary = 0;
    for (let i = 0; i < text.length; i += 1) {
      if (text.startsWith("```", i)) { fence = !fence; i += 2; continue; }
      // A list may continue past a blank line (loose items), so never freeze
      // right after one: the renderer merges it with the next block.
      if (!fence && text[i] === "\n" && text[i + 1] === "\n" && !mdEndsInList(text, i)) boundary = i + 2;
    }
    return boundary;
  }

function mdEndsInList(text, end) {
    const start = text.lastIndexOf("\n\n", end - 1);
    const lines = text.slice(start < 0 ? 0 : start + 2, end).split("\n").filter((line) => line.trim());
    return lines.length > 0 && deps.looksLikeListBlock(lines);
  }

function _mdNodeKey(node) {
    return node.nodeType === Node.TEXT_NODE ? node.nodeValue : node.outerHTML;
  }

function streamMarkdown(el, text, writing, catalog) {
    const catalogKey = catalog.join("\n") + "\u0000" + Boolean(writing);
    const reset = el._mdCatalog !== catalogKey || (el._mdText && !text.startsWith(el._mdText));
    let anchor = reset ? 0 : (el._mdAnchor || 0);
    let frozen = reset ? 0 : (el._mdFrozen || 0);
    const want = mdStableBoundary(text);
    if (!reset && want > anchor) {
      const deltaHtml = renderMarkdown(text.slice(anchor, want), writing, catalog);
      const probe = document.createElement("template");
      probe.innerHTML = deltaHtml;
      const deltaNodes = [...probe.content.childNodes];
      const liveNodes = [...el.childNodes].slice(frozen, frozen + deltaNodes.length);
      const aligned = liveNodes.length === deltaNodes.length &&
        liveNodes.every((node, index) => _mdNodeKey(node) === _mdNodeKey(deltaNodes[index]));
      if (aligned) {
        frozen += deltaNodes.length;
        anchor = want;
      }
      // Misaligned: freeze nothing this round and re-render from the old
      // anchor so the visible region is reconciled against the full text.
    }
    const tailHtml = renderMarkdown(text.slice(anchor), writing, catalog);
    patchMarkdownFrom(el, tailHtml, frozen);
    el._mdAnchor = anchor;
    el._mdFrozen = frozen;
    el._mdText = text;
    el._mdCatalog = catalogKey;
  }

function revealCut(text, from, to) {
    if (to >= text.length) return text.length;
    const tail = text.slice(to, to + 14);
    const end = tail.search(/\s/);
    return end >= 0 ? to + end + 1 : to;
  }

function revealNow(md, text, writing, catalog) {
    if (md._revealRaf) cancelAnimationFrame(md._revealRaf);
    md._revealRaf = 0;
    md._revealTarget = text;
    md._revealShown = text.length;
    md._revealRate = 0;
    streamMarkdown(md, text, writing, catalog);
  }

function revealTick(md) {
    md._revealRaf = 0;
    if (!md.isConnected) return;
    const { _revealTarget: text, _revealCatalog: catalog } = md;
    const now = performance.now();
    const dt = now - (md._revealAt || now - 16);
    md._revealAt = now;
    // A throttled or hidden tab must not drip text out at the paced rate.
    if (document.hidden || dt > 400 || deps.reduceMotion.matches) {
      md._src = md._revealSrc;
      revealNow(md, text, md._revealWriting, catalog);
      if (deps.stickToBottom) deps.snapThread();
      return;
    }
    const backlog = text.length - md._revealShown;
    if (backlog <= 0) return;
    const step = backlog <= deps.REVEAL_CALM ? backlog : Math.max(1, Math.round(md._revealRate * Math.min(dt, 64)));
    md._revealShown = revealCut(text, md._revealShown, md._revealShown + step);
    const done = md._revealShown >= text.length;
    streamMarkdown(md, done ? text : text.slice(0, md._revealShown), done ? md._revealWriting : true, catalog);
    if (done) md._src = md._revealSrc;
    md.querySelectorAll("pre").forEach((el, i) => {
      const saved = md._revealPre && md._revealPre[i];
      if (saved) { el.scrollLeft = saved[0]; el.scrollTop = saved[1]; }
    });
    if (deps.stickToBottom) deps.snapThread();
    if (!done) md._revealRaf = requestAnimationFrame(() => revealTick(md));
  }

function paintContentBlock(md, text, writing, catalog, srcKey) {
    const paced = !deps.reduceMotion.matches && !document.hidden && (writing || md._revealRaf);
    if (!paced) {
      md._src = srcKey;
      revealNow(md, text, writing, catalog);
      return;
    }
    const first = md._revealTarget === undefined;
    const continues = !first && text.startsWith(md._revealTarget);
    // Text that was already there (a thread opened mid-reply) is not streamed in.
    if (first) md._revealShown = text.length > deps.REVEAL_FIRST ? text.length : 0;
    else if (!continues) md._revealShown = text.length;
    const grew = md._revealTarget !== text;
    md._revealTarget = text;
    md._revealCatalog = catalog;
    md._revealWriting = writing;
    md._revealSrc = srcKey;
    const backlog = text.length - md._revealShown;
    if (grew) md._revealRate = backlog / deps.REVEAL_MS;
    // Nothing to pace: a short reply, a calm stream's next few characters.
    // They show in this very paint, not a frame later.
    if (backlog <= deps.REVEAL_CALM) {
      md._src = srcKey;
      revealNow(md, text, writing, catalog);
      return;
    }
    md._revealPre = [...md.querySelectorAll("pre")].map((el) => [el.scrollLeft, el.scrollTop]);
    if (!md._revealRaf) {
      md._revealAt = 0;
      md._revealRaf = requestAnimationFrame(() => revealTick(md));
    }
  }

  return { renderChunk, splitParagraphLists, renderBlocks, renderMarkdown, patchMarkdown, patchMarkdownFrom, mdStableBoundary, mdEndsInList, _mdNodeKey, streamMarkdown, revealCut, revealNow, revealTick, paintContentBlock };
}
