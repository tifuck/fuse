/* Code-aware spell-check for #chat-input. Tokenizer + overlay + correction popover. */
(() => {
  const root = typeof window !== "undefined" ? window : globalThis;
  const DICT_URL = "/static/dict/en-US.bin.gz?v=1";
  const DICT_VER = "1";
  const SPELL_STORE = "fuse.chat.spell.v1";
  const IDB_NAME = "fuse-composer";
  const IDB_STORE = "dict";
  const LETTERS = "abcdefghijklmnopqrstuvwxyz'";
  const TWO_LETTER_ENGLISH = new Set(
    "am an as at be by do go he if in is it me my no of ok on or ox so to up us we ye".split(" ")
  );
  const CONTRACTIONS = new Set([
    "aren't", "can't", "couldn't", "didn't", "doesn't", "don't", "hadn't", "hasn't",
    "haven't", "he'd", "he'll", "he's", "i'd", "i'll", "i'm", "i've", "isn't", "let's",
    "she'd", "she'll", "she's", "shouldn't", "that's", "there's", "they'd", "they'll",
    "they're", "they've", "wasn't", "we'd", "we'll", "we're", "we've", "weren't",
    "what's", "won't", "wouldn't", "you'd", "you'll", "you're", "you've",
  ]);
  const EXT_RE = /^(py|js|ts|tsx|jsx|mjs|cjs|css|html|json|md|mdc|toml|yml|yaml|go|rs|rb|java|kt|c|h|cc|cpp|hpp|sh|zsh|bash|sql|proto|lock|map|svg|png|jpg|webp|wasm|so|dll|exe|deb|appimage)$/i;
  const OPS = new Set(["C++", "C#", "F#", "++", "=>", "==="]);
  const METRIC_PROPS = [
    "box-sizing", "font", "font-family", "font-size", "font-weight",
    "font-style", "font-variant", "line-height", "letter-spacing",
    "text-transform", "text-indent", "word-spacing",
    "padding-top", "padding-right", "padding-bottom", "padding-left",
    "border-top-width", "border-right-width", "border-bottom-width", "border-left-width",
    "border-top-style", "border-right-style", "border-bottom-style", "border-left-style",
    "overflow-wrap", "word-wrap", "word-break", "white-space", "tab-size",
  ];

  let englishSet = null;
  let dictState = "idle";
  let env = null;
  let composing = false;
  let lastIssues = [];
  let lastText = "";
  let checkTimer = 0;
  let paintFrame = 0;
  let popIndex = 0;
  let popIssue = null;
  let popSuggestions = [];
  let ignored = new Set();
  let sessionLearned = new Set();
  let added = loadAdded();
  let debug = false;

  try {
    debug = typeof location !== "undefined" && /(?:^|[?&])composer_debug=1(?:&|$)/.test(location.search || "");
  } catch (_) {}

  function loadAdded() {
    try {
      const raw = JSON.parse(root.localStorage.getItem(SPELL_STORE) || "null");
      const list = raw && Array.isArray(raw.added) ? raw.added : [];
      return new Set(list.map((w) => String(w || "").toLowerCase()).filter((w) => /^[a-z][a-z']{0,31}$/.test(w)).slice(0, 2000));
    } catch (_) {
      return new Set();
    }
  }

  function persistAdded() {
    try {
      root.localStorage.setItem(SPELL_STORE, JSON.stringify({ v: 1, added: Array.from(added).slice(0, 2000) }));
    } catch (_) {}
  }

  function isCamel(t) {
    return /^[a-z][A-Za-z0-9]*[A-Z][A-Za-z0-9]*$/.test(t);
  }
  function isPascalInterior(t) {
    return /^[A-Z][a-z0-9]+[A-Z][A-Za-z0-9]*$/.test(t);
  }
  function isInteriorCapital(t) {
    if (t === t.toLowerCase() || t === t.toUpperCase()) return false;
    return /[A-Z]/.test(t.slice(1));
  }
  function isSnake(t) {
    return /^[A-Za-z][A-Za-z0-9]*_[A-Za-z0-9_]+$/.test(t);
  }
  function isSlugWithDigit(t) {
    return /[0-9]/.test(t) && /^[A-Za-z0-9]+(?:[.-][A-Za-z0-9]+)+$/.test(t);
  }
  function isAlnumAbbrev(t) {
    return /^[A-Za-z][A-Za-z0-9]*[0-9][A-Za-z0-9]*$/.test(t);
  }
  function isAcronym(t) {
    return /^[A-Z]{2,6}$/.test(t);
  }
  function isIdentSkip(t) {
    return isCamel(t) || isPascalInterior(t) || isInteriorCapital(t) || isSnake(t)
      || isSlugWithDigit(t) || isAlnumAbbrev(t) || isAcronym(t);
  }
  function isLatinWord(t) {
    return /^[A-Za-z]+(?:'[A-Za-z]+)?$/.test(t);
  }
  function hasNonLatin(t) {
    return /[^\u0000-\u024F]/.test(t);
  }

  function atLineStart(text, idx) {
    return idx === 0 || text[idx - 1] === "\n" || text[idx - 1] === "\r";
  }

  function emit(tokens, text, kind, start, end) {
    if (end > start) tokens.push({ kind, start, end, text: text.slice(start, end) });
  }

  function skipFence(text, i) {
    if (!atLineStart(text, i)) return -1;
    let fence = "";
    if (text.startsWith("```", i)) fence = "```";
    else if (text.startsWith("~~~", i)) fence = "~~~";
    else return -1;
    const n = text.length;
    let j = i + fence.length;
    while (j < n && text[j] !== "\n" && text[j] !== "\r") j += 1;
    if (j < n && text[j] === "\r") j += 1;
    if (j < n && text[j] === "\n") j += 1;
    let k = j;
    while (k < n) {
      if (atLineStart(text, k) && text.startsWith(fence, k)) {
        k += fence.length;
        while (k < n && text[k] !== "\n" && text[k] !== "\r") k += 1;
        if (k < n && text[k] === "\r") k += 1;
        if (k < n && text[k] === "\n") k += 1;
        return k;
      }
      k += 1;
    }
    return n;
  }

  function skipInlineCode(text, i) {
    if (text[i] !== "`") return -1;
    let ticks = 0;
    const n = text.length;
    while (i + ticks < n && text[i + ticks] === "`") ticks += 1;
    if (ticks >= 3 && atLineStart(text, i)) return -1;
    let j = i + ticks;
    while (j < n && text[j] !== "\n" && text[j] !== "\r") {
      let k = 0;
      while (j + k < n && text[j + k] === "`") k += 1;
      if (k === ticks) return j + k;
      j += 1;
    }
    return j;
  }

  function skipUrl(text, i) {
    const n = text.length;
    let rest = text.slice(i);
    let m = rest.match(/^(https?:\/\/\S+|www\.\S+|mailto:\S+)/i);
    if (!m) return -1;
    let end = i + m[1].length;
    while (end > i && ".,;:!?)]}".includes(text[end - 1])) end -= 1;
    return end;
  }

  function skipEmail(text, i) {
    const m = text.slice(i).match(/^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}/);
    return m ? i + m[0].length : -1;
  }

  function skipMdLinkUrl(text, i) {
    if (text[i] !== "(") return -1;
    if (i === 0) return -1;
    if (text[i - 1] !== "]") return -1;
    let j = i + 1;
    const n = text.length;
    while (j < n && text[j] !== ")" && text[j] !== "\n") j += 1;
    if (j < n && text[j] === ")") return j + 1;
    return -1;
  }

  function skipSlashCommand(text, i) {
    if (i !== 0 || text[0] !== "/" || text.includes("\n")) return -1;
    const m = text.match(/^\/([a-z0-9]*(?:-[a-z0-9]*)*)/i);
    if (!m) return -1;
    return m[0].length;
  }

  function skipAtToken(text, i) {
    if (text[i] !== "@") return -1;
    const m = text.slice(i).match(/^@[A-Za-z0-9_./:+-]+/);
    return m ? i + m[0].length : -1;
  }

  function skipPath(text, i) {
    const rest = text.slice(i);
    if (/^(\.\/|\.\.\/|~\/)/.test(rest)) {
      const m = rest.match(/^(?:\.\.?|~)?\/[^\s,;:!?)]*/);
      return m ? i + m[0].length : -1;
    }
    if (text[i] === "/" && i > 0 && /[A-Za-z0-9._-]/.test(text[i + 1] || "")) {
      const m = rest.match(/^\/[^\s,;:!?)]+/);
      return m ? i + m[0].length : -1;
    }
    if (/^[A-Za-z]:[\\/]/.test(rest)) {
      const m = rest.match(/^[A-Za-z]:[\\/][^\s,;:!?)]*/);
      return m ? i + m[0].length : -1;
    }
    if (text[i] === "." && /[A-Za-z0-9]/.test(text[i + 1] || "") && (i === 0 || /\s/.test(text[i - 1]))) {
      const m = rest.match(/^\.[A-Za-z0-9_.-]+/);
      if (m) return i + m[0].length;
    }
    if (/^[A-Za-z0-9_.-]+[\\/]/.test(rest)) {
      const m = rest.match(/^[^\s,;:!?)]+[\\/][^\s,;:!?)]+/);
      if (m && /[\\/]/.test(m[0])) return i + m[0].length;
    }
    return -1;
  }

  function skipExt(text, i) {
    const m = text.slice(i).match(/^[A-Za-z0-9_.-]+\.([A-Za-z0-9]+)/);
    if (!m) return -1;
    if (!EXT_RE.test(m[1])) return -1;
    const prev = i === 0 ? "" : text[i - 1];
    if (prev && /[A-Za-z0-9]/.test(prev)) return -1;
    return i + m[0].length;
  }

  function skipHash(text, i) {
    const m = text.slice(i).match(/^[0-9a-fA-F]{7,64}/);
    if (!m) return -1;
    const tok = m[0];
    const next = text[i + tok.length] || "";
    if (/[A-Za-z]/.test(next)) return -1;
    if (!/[0-9]/.test(tok) && tok.length < 12) return -1;
    return i + tok.length;
  }

  function skipColor(text, i) {
    if (text[i] !== "#") return -1;
    const m = text.slice(i).match(/^#(?:[0-9a-fA-F]{3,4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})(?![A-Za-z0-9])/);
    return m ? i + m[0].length : -1;
  }

  function skipVersion(text, i) {
    const prev = i === 0 ? "" : text[i - 1];
    if (prev && /[A-Za-z0-9]/.test(prev)) return -1;
    const m = text.slice(i).match(/^v?\d+\.\d+(?:\.\d+)?(?:[-+][A-Za-z0-9.]+)?/);
    if (!m) return -1;
    const next = text[i + m[0].length] || "";
    if (/[A-Za-z]/.test(next)) return -1;
    return i + m[0].length;
  }

  function skipTicket(text, i) {
    const prev = i === 0 ? "" : text[i - 1];
    if (prev && /[A-Za-z0-9]/.test(prev)) return -1;
    let m = text.slice(i).match(/^[A-Z]{2,10}-\d+/);
    if (m) return i + m[0].length;
    m = text.slice(i).match(/^PR\s*#?\d+/);
    if (m) return i + m[0].length;
    if (text[i] === "#" && !atLineStart(text, i)) {
      m = text.slice(i).match(/^#\d+/);
      if (m) return i + m[0].length;
    }
    if (atLineStart(text, i) && /^#{1,6}\s/.test(text.slice(i))) {
      let j = i;
      while (text[j] === "#") j += 1;
      return j;
    }
    return -1;
  }

  function skipNumber(text, i) {
    const prev = i === 0 ? "" : text[i - 1];
    if (prev && /[A-Za-z0-9]/.test(prev)) return -1;
    let m = text.slice(i).match(/^0x[0-9a-fA-F]+/);
    if (m) return i + m[0].length;
    m = text.slice(i).match(/^\d+(?:\.\d+)?(?:e[+-]?\d+)?(?:px|rem|em|dvh|vh|vw|ms|s|kb|mb|gb|k|m)?/i);
    if (m) return i + m[0].length;
    m = text.slice(i).match(/^\d+%/);
    if (m) return i + m[0].length;
    return -1;
  }

  function skipOp(text, i) {
    for (const op of OPS) {
      if (text.startsWith(op, i)) {
        const next = text[i + op.length] || "";
        if (op.length <= 2 && /[A-Za-z]/.test(next) && op !== "C#" && op !== "F#" && op !== "C++") continue;
        return i + op.length;
      }
    }
    return -1;
  }

  function skipHostOrModel(text, i) {
    const m = text.slice(i).match(/^[A-Za-z0-9][A-Za-z0-9._-]*(?:\/[A-Za-z0-9._-]+)+/);
    if (!m) return -1;
    if (!/[./]/.test(m[0])) return -1;
    if (m[0].includes(".") || m[0].includes("/")) return i + m[0].length;
    return -1;
  }

  function classifyWord(part) {
    if (!part) return "skip";
    if (hasNonLatin(part)) return "skip";
    if (isIdentSkip(part)) return "skip";
    if (/^[A-Z][A-Za-z]*'[A-Z][A-Za-z]+$/.test(part)) return "skip";
    let stem = part;
    if (/^[A-Za-z]+'s$/i.test(part)) stem = part.slice(0, -2);
    if (CONTRACTIONS.has(part.toLowerCase())) return "word";
    if (!isLatinWord(stem) && !isLatinWord(part)) return "skip";
    const probe = isLatinWord(stem) ? stem : part;
    if (probe.length === 1) return "skip";
    if (probe.length > 32) return "skip";
    if (probe.length === 2) {
      const low = probe.toLowerCase();
      const lex = root.FuseComposerLexicon;
      if (TWO_LETTER_ENGLISH.has(low) || (lex && lex.has(low))) return "word";
      return "skip";
    }
    return "word";
  }

  function emitIdent(tokens, text, start, end) {
    const candidate = text.slice(start, end);
    if (isIdentSkip(candidate)) {
      emit(tokens, text, "skip", start, end);
      return;
    }
    if (/^[A-Z][A-Za-z]*'[A-Z][A-Za-z]+$/.test(candidate)) {
      emit(tokens, text, "skip", start, end);
      return;
    }
    if (candidate.includes("-") && !isSlugWithDigit(candidate)) {
      let pos = start;
      for (const part of candidate.split("-")) {
        if (!part) {
          emit(tokens, text, "skip", pos, pos + 1);
          pos += 1;
          continue;
        }
        emit(tokens, text, classifyWord(part), pos, pos + part.length);
        pos += part.length;
        if (pos < end && text[pos] === "-") {
          emit(tokens, text, "skip", pos, pos + 1);
          pos += 1;
        }
      }
      return;
    }
    emit(tokens, text, classifyWord(candidate), start, end);
  }

  function tokenize(text) {
    const tokens = [];
    const n = text.length;
    let i = 0;
    while (i < n) {
      let end = skipFence(text, i);
      if (end > i) { emit(tokens, text, "skip", i, end); i = end; continue; }
      end = skipInlineCode(text, i);
      if (end > i) { emit(tokens, text, "skip", i, end); i = end; continue; }
      end = skipUrl(text, i);
      if (end > i) { emit(tokens, text, "skip", i, end); i = end; continue; }
      end = skipEmail(text, i);
      if (end > i) { emit(tokens, text, "skip", i, end); i = end; continue; }
      end = skipMdLinkUrl(text, i);
      if (end > i) { emit(tokens, text, "skip", i, end); i = end; continue; }
      end = skipSlashCommand(text, i);
      if (end > i) { emit(tokens, text, "skip", i, end); i = end; continue; }
      end = skipAtToken(text, i);
      if (end > i) { emit(tokens, text, "skip", i, end); i = end; continue; }
      end = skipPath(text, i);
      if (end > i) { emit(tokens, text, "skip", i, end); i = end; continue; }
      end = skipExt(text, i);
      if (end > i) { emit(tokens, text, "skip", i, end); i = end; continue; }
      end = skipHash(text, i);
      if (end > i) { emit(tokens, text, "skip", i, end); i = end; continue; }
      end = skipColor(text, i);
      if (end > i) { emit(tokens, text, "skip", i, end); i = end; continue; }
      end = skipVersion(text, i);
      if (end > i) { emit(tokens, text, "skip", i, end); i = end; continue; }
      end = skipTicket(text, i);
      if (end > i) { emit(tokens, text, "skip", i, end); i = end; continue; }
      end = skipNumber(text, i);
      if (end > i) { emit(tokens, text, "skip", i, end); i = end; continue; }
      end = skipOp(text, i);
      if (end > i) { emit(tokens, text, "skip", i, end); i = end; continue; }
      end = skipHostOrModel(text, i);
      if (end > i) { emit(tokens, text, "skip", i, end); i = end; continue; }

      if (/[A-Za-z0-9'.-]/.test(text[i])) {
        let j = i + 1;
        while (j < n && /[A-Za-z0-9'.-]/.test(text[j])) j += 1;
        emitIdent(tokens, text, i, j);
        i = j;
        continue;
      }
      emit(tokens, text, "skip", i, i + 1);
      i += 1;
    }
    return tokens;
  }

  function suppressCurrent(token, caret, text) {
    if (caret == null || caret < token.start || caret > token.end) return false;
    const after = text[caret] || "";
    if (after && /[\s.,;:!?)"'`\]]/.test(after)) return false;
    return true;
  }

  function contextWords(ctx) {
    const out = new Set();
    if (!ctx) return out;
    const push = (value) => {
      String(value || "").toLowerCase().split(/[^a-z0-9']+/).forEach((part) => {
        if (part.length >= 2 && part.length <= 32) out.add(part);
      });
    };
    (ctx.skills || []).forEach((s) => push(s.slug || s.name));
    (ctx.models || []).forEach((m) => {
      push(m.id); push(m.name); push(m.label);
    });
    (ctx.projects || []).forEach((p) => {
      push(p.project_name); push(p.host_alias); push(p.name);
    });
    (ctx.threads || []).forEach((t) => push(t.title));
    (ctx.traces || []).forEach((t) => {
      push(t.name); push(t.path); push(t.cwd);
    });
    (ctx.attachments || []).forEach((a) => push((a.name || a.filename || "").replace(/\.[A-Za-z0-9]+$/, "")));
    ((ctx.listing && ctx.listing.entries) || []).forEach((e) => push(e.name));
    if (ctx.project) {
      push(ctx.project.project_name);
      push(ctx.project.host_alias);
    }
    return out;
  }

  function check(text, opts) {
    opts = opts || {};
    if (opts.composing) return [];
    const ready = opts.ready != null ? opts.ready : dictState === "ready";
    const english = opts.english || englishSet;
    if (!ready || !english) return [];
    const caret = opts.caret;
    const learned = opts.learned || sessionLearned;
    const ctxSet = contextWords(opts.context);
    const lex = root.FuseComposerLexicon;
    const issues = [];
    const tokens = tokenize(text);
    for (const tok of tokens) {
      if (tok.kind !== "word") continue;
      if (suppressCurrent(tok, caret, text)) continue;
      let stem = tok.text;
      if (/^[A-Za-z]+'s$/i.test(stem)) stem = stem.slice(0, -2);
      const low = stem.toLowerCase();
      if (ignored.has(low) || added.has(low) || learned.has(low)) continue;
      if (lex && lex.has(low)) continue;
      if (ctxSet.has(low)) continue;
      if (CONTRACTIONS.has(low)) continue;
      if (english.has(low)) continue;
      issues.push({ start: tok.start, end: tok.end, word: tok.text });
    }
    return issues;
  }

  function edits1(word) {
    const splits = [];
    for (let i = 0; i <= word.length; i += 1) splits.push([word.slice(0, i), word.slice(i)]);
    const out = new Set();
    for (const [a, b] of splits) {
      if (b) out.add(a + b.slice(1));
      if (b.length > 1) out.add(a + b[1] + b[0] + b.slice(2));
      if (b) {
        for (const c of LETTERS) out.add(a + c + b.slice(1));
      }
      for (const c of LETTERS) out.add(a + c + b);
    }
    return out;
  }

  function suggest(word) {
    const low = String(word || "").toLowerCase();
    const english = englishSet || new Set();
    const lex = root.FuseComposerLexicon;
    const pool = (w) => english.has(w) || (lex && lex.has(w)) || added.has(w) || sessionLearned.has(w);
    const hits = [];
    const seen = new Set();
    const consider = (w, dist) => {
      if (!w || seen.has(w) || !pool(w) || w === low) return;
      seen.add(w);
      const lexHit = lex && lex.has(w) ? 0 : 1;
      hits.push({ w, dist, lexHit });
    };
    for (const w of edits1(low)) consider(w, 1);
    hits.sort((a, b) => a.lexHit - b.lexHit || a.dist - b.dist || a.w.length - b.w.length || a.w.localeCompare(b.w));
    if (hits.length < 3) {
      const start = Date.now();
      for (const e1 of edits1(low)) {
        if (Date.now() - start > 8) break;
        for (const w of edits1(e1)) consider(w, 2);
      }
      hits.sort((a, b) => a.lexHit - b.lexHit || a.dist - b.dist || a.w.length - b.w.length || a.w.localeCompare(b.w));
    }
    return hits.slice(0, 5).map((h) => preserveCase(word, h.w));
  }

  function preserveCase(orig, next) {
    if (orig === orig.toUpperCase()) return next.toUpperCase();
    if (orig[0] === orig[0].toUpperCase()) return next[0].toUpperCase() + next.slice(1);
    return next;
  }

  function escapeHtml(value) {
    return escapeText(value);
  }

  function prefs() {
    if (env && typeof env.getPrefs === "function") {
      try { return env.getPrefs() || { spell: true }; } catch (_) {}
    }
    return { spell: true, palettes: true, history: true };
  }

  function syncMetrics() {
    if (!env || !env.input || !env.mirror) return;
    const input = env.input;
    const mirror = env.mirror;
    const cs = getComputedStyle(input);
    for (const p of METRIC_PROPS) mirror.style.setProperty(p, cs.getPropertyValue(p));
    mirror.style.width = input.clientWidth + "px";
    mirror.style.height = input.clientHeight + "px";
    mirror.scrollTop = input.scrollTop;
    mirror.scrollLeft = input.scrollLeft;
  }

  function paint() {
    if (!env || !env.mirror || !env.input) return;
    const mirror = env.mirror;
    const text = env.input.value;
    if (!prefs().spell || !text) {
      mirror.innerHTML = "";
      return;
    }
    const issues = lastIssues;
    let html = "";
    let pos = 0;
    for (const iss of issues) {
      html += escapeHtml(text.slice(pos, iss.start));
      html += `<span class="chat-misspell" data-i="${iss.start}:${iss.end}">${escapeHtml(text.slice(iss.start, iss.end))}</span>`;
      pos = iss.end;
    }
    html += escapeHtml(text.slice(pos));
    html += "\n";
    mirror.innerHTML = html;
    syncMetrics();
  }

  function schedulePaint() {
    if (paintFrame) return;
    paintFrame = root.requestAnimationFrame(() => {
      paintFrame = 0;
      paint();
    });
  }

  function refresh() {
    if (!env || !env.input) return;
    if (!prefs().spell || composing) {
      lastIssues = [];
      lastText = env.input.value;
      schedulePaint();
      return;
    }
    const text = env.input.value;
    lastText = text;
    const caret = env.input.selectionStart;
    if (text.length > 20000) {
      const head = text.slice(0, 1000);
      const tail = text.slice(-8000);
      slice = head + "\n" + tail;
      // Full-text check on last 8k + first 1k: map issues from tail.
      const headIssues = check(head, {
        caret: caret <= 1000 ? caret : -1,
        composing: false,
        learned: sessionLearned,
        context: env.getContext ? env.getContext() : null,
      });
      const tailStart = text.length - 8000;
      const tailIssues = check(tail, {
        caret: caret >= tailStart ? caret - tailStart : -1,
        composing: false,
        learned: sessionLearned,
        context: env.getContext ? env.getContext() : null,
      }).map((iss) => ({ start: iss.start + tailStart, end: iss.end + tailStart, word: iss.word }));
      lastIssues = headIssues.concat(tailIssues);
    } else {
      lastIssues = check(text, {
        caret,
        composing: false,
        learned: sessionLearned,
        context: env.getContext ? env.getContext() : null,
      });
    }
    if (debug) {
      try {
        console.info("composer-spell", {
          dictStatus: dictState,
          tokens: tokenize(text).length,
          skipped: tokenize(text).filter((t) => t.kind === "skip").length,
          issues: lastIssues.length,
        });
      } catch (_) {}
    }
    schedulePaint();
  }

  function debounceCheck() {
    root.clearTimeout(checkTimer);
    const text = env && env.input ? env.input.value : "";
    const delay = text.length > 4000 ? 400 : 200;
    checkTimer = root.setTimeout(refresh, delay);
  }

  function issueAtCaret() {
    if (!env || !env.input) return null;
    const caret = env.input.selectionStart;
    if (caret !== env.input.selectionEnd) return null;
    return lastIssues.find((iss) => caret >= iss.start && caret <= iss.end) || null;
  }

  function hidePopover() {
    popIssue = null;
    popSuggestions = [];
    popIndex = 0;
    if (env && env.input) env.input.removeAttribute("aria-activedescendant");
    if (env && env.popover) {
      env.popover.hidden = true;
      env.popover.innerHTML = "";
    }
  }

  function isOpen() {
    return Boolean(env && env.popover && !env.popover.hidden && popIssue);
  }

  function placePopover(issue) {
    const pop = env.popover;
    if (!pop || !env.mirror) return;
    const span = env.mirror.querySelector(`[data-i="${issue.start}:${issue.end}"]`);
    const rect = span ? span.getBoundingClientRect() : env.input.getBoundingClientRect();
    const vv = root.visualViewport;
    const vh = (vv && vv.height) || root.innerHeight || 800;
    pop.hidden = false;
    const box = pop.getBoundingClientRect();
    let top = rect.bottom + 6;
    if (top + box.height > vh - 8) top = Math.max(8, rect.top - box.height - 6);
    let left = rect.left;
    const maxLeft = (root.innerWidth || 800) - box.width - 8;
    if (left > maxLeft) left = Math.max(8, maxLeft);
    pop.style.left = Math.round(left) + "px";
    pop.style.top = Math.round(top) + "px";
  }

  // Suggestions, then Ignore and Add to dictionary: one list the arrow keys
  // walk while focus stays in the textarea (aria-activedescendant).
  function popChoices() {
    return popSuggestions.map((word) => ({ label: word, run: () => applyCorrection(popIssue, word) }))
      .concat([
        { label: "Ignore", attr: "data-spell-ignore", run: () => ignore(popIssue.word) },
        { label: "Add to dictionary", attr: "data-spell-add", run: () => add(popIssue.word) },
      ]);
  }

  function renderPopover() {
    const pop = env && env.popover;
    if (!pop || !popIssue) return;
    pop.innerHTML = "";
    const actions = document.createElement("div");
    actions.className = "chat-spell-pop-actions";
    actions.setAttribute("role", "group");
    actions.setAttribute("aria-label", "Actions");
    popChoices().forEach((choice, index) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.id = "chat-spell-opt-" + index;
      btn.tabIndex = -1;
      btn.setAttribute("role", "option");
      btn.setAttribute("aria-selected", String(index === popIndex));
      if (choice.attr) btn.setAttribute(choice.attr, "");
      btn.className = index === popIndex ? "is-active" : "";
      btn.textContent = choice.label;
      btn.addEventListener("mousedown", (event) => event.preventDefault());
      btn.addEventListener("click", choice.run);
      (choice.attr ? actions : pop).appendChild(btn);
    });
    pop.appendChild(actions);
    if (env.input) env.input.setAttribute("aria-activedescendant", "chat-spell-opt-" + popIndex);
    placePopover(popIssue);
  }

  function openAtCaret() {
    if (!env || !env.input || composing || !prefs().spell) return;
    if (env.input.selectionStart !== env.input.selectionEnd) return;
    const issue = issueAtCaret();
    if (!issue) return;
    popIssue = issue;
    popSuggestions = suggest(issue.word);
    popIndex = 0;
    if (env.input) env.input.setAttribute("enterkeyhint", "enter");
    renderPopover();
  }

  function applyCorrection(issue, replacement) {
    if (!env || !env.input || composing || !issue) return;
    const value = env.input.value;
    if (value.slice(issue.start, issue.end) !== issue.word) {
      hidePopover();
      return;
    }
    env.input.setRangeText(replacement, issue.start, issue.end, "end");
    hidePopover();
    if (typeof env.onChange === "function") env.onChange();
    refresh();
  }

  function ignore(word) {
    ignored.add(String(word || "").toLowerCase());
    hidePopover();
    refresh();
  }

  function add(word) {
    const low = String(word || "").toLowerCase();
    if (/^[a-z][a-z']{0,31}$/.test(low)) {
      added.add(low);
      persistAdded();
    }
    hidePopover();
    refresh();
  }

  function accept() {
    if (!isOpen()) return;
    const choice = popChoices()[popIndex];
    if (choice) choice.run();
    else hidePopover();
  }

  function move(delta) {
    if (!isOpen()) return;
    const count = popSuggestions.length + 2;
    popIndex = (popIndex + delta + count) % count;
    renderPopover();
  }

  function close() {
    hidePopover();
  }

  function learnFromSent(text) {
    if (!text || dictState !== "ready") return;
    const issues = new Set(check(text, { ready: true, caret: -1, learned: sessionLearned }).map((i) => i.word.toLowerCase()));
    for (const tok of tokenize(text)) {
      if (tok.kind !== "word") continue;
      const low = tok.text.toLowerCase();
      if (!issues.has(low) && sessionLearned.size < 500) sessionLearned.add(low);
    }
  }

  function rebuildLearned(prompts) {
    sessionLearned = new Set();
    (prompts || []).forEach((p) => learnFromSent(p));
  }

  function idbOpen() {
    return new Promise((resolve, reject) => {
      const req = indexedDB.open(IDB_NAME, 1);
      req.onupgradeneeded = () => {
        const db = req.result;
        if (!db.objectStoreNames.contains(IDB_STORE)) db.createObjectStore(IDB_STORE);
      };
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => reject(req.error);
    });
  }

  function idbGet(db, key) {
    return new Promise((resolve, reject) => {
      const tx = db.transaction(IDB_STORE, "readonly");
      const req = tx.objectStore(IDB_STORE).get(key);
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => reject(req.error);
    });
  }

  function idbPut(db, key, value) {
    return new Promise((resolve, reject) => {
      const tx = db.transaction(IDB_STORE, "readwrite");
      const req = tx.objectStore(IDB_STORE).put(value, key);
      req.onsuccess = () => resolve();
      req.onerror = () => reject(req.error);
    });
  }

  function decodeWords(buf) {
    const text = new TextDecoder("utf-8").decode(buf);
    const set = new Set();
    for (const line of text.split(/\n+/)) {
      const w = line.trim().toLowerCase();
      if (w) set.add(w);
    }
    return set;
  }

  async function loadEnglish() {
    try {
      const db = await idbOpen();
      const cached = await idbGet(db, "en-US:" + DICT_VER);
      if (cached instanceof Uint8Array && cached.length) return decodeWords(cached);
      if (cached instanceof ArrayBuffer && cached.byteLength) return decodeWords(new Uint8Array(cached));
    } catch (_) {}
    const res = await fetch(DICT_URL, { credentials: "same-origin" });
    if (!res.ok) throw new Error("dict http " + res.status);
    let buf = new Uint8Array(await res.arrayBuffer());
    const gzipMagic = buf.length >= 2 && buf[0] === 0x1f && buf[1] === 0x8b;
    if (gzipMagic) {
      if (typeof DecompressionStream !== "function") throw new Error("no DecompressionStream");
      buf = new Uint8Array(await new Response(
        new Blob([buf]).stream().pipeThrough(new DecompressionStream("gzip"))
      ).arrayBuffer());
    }
    try {
      const db = await idbOpen();
      await idbPut(db, "en-US:" + DICT_VER, buf);
    } catch (_) {}
    return decodeWords(buf);
  }

  function startDictLoad() {
    if (dictState !== "idle") return;
    if (!prefs().spell) return;
    dictState = "loading";
    const fail = () => {
      dictState = "failed";
      englishSet = null;
      lastIssues = [];
      schedulePaint();
    };
    const timer = root.setTimeout(fail, 8000);
    loadEnglish().then((set) => {
      root.clearTimeout(timer);
      if (set && set.size >= 1000) {
        englishSet = set;
        dictState = "ready";
        refresh();
      } else fail();
    }).catch(fail);
  }

  function bindInput() {
    const input = env.input;
    input.addEventListener("scroll", () => {
      if (paintFrame) return;
      paintFrame = root.requestAnimationFrame(() => {
        paintFrame = 0;
        syncMetrics();
      });
    });
    input.addEventListener("compositionstart", () => {
      composing = true;
      hidePopover();
    });
    input.addEventListener("compositionend", () => {
      composing = false;
      refresh();
    });
    input.addEventListener("click", () => {
      if (composing) return;
      if (input.selectionStart !== input.selectionEnd) return;
      openAtCaret();
    });
  }

  function mount(next) {
    env = next || null;
    if (!env || !env.input) return;
    bindInput();
    syncMetrics();
    const idle = root.requestIdleCallback || ((fn) => root.setTimeout(fn, 1200));
    idle(() => startDictLoad(), { timeout: 1200 });
  }

  function dispose() {
    hidePopover();
    root.clearTimeout(checkTimer);
    env = null;
    lastIssues = [];
  }

  root.FuseComposerSpell = {
    tokenize,
    check,
    isCamel,
    isPascalInterior,
    isInteriorCapital,
    isSnake,
    isSlugWithDigit,
    isAlnumAbbrev,
    isAcronym,
    mount,
    syncMetrics,
    refresh,
    debounceCheck,
    dispose,
    applyCorrection,
    ignore,
    add,
    isOpen,
    close,
    accept,
    move,
    openAtCaret,
    dictStatus() { return dictState; },
    learnFromSent,
    rebuildLearned,
    get composing() { return composing; },
    set composing(v) { composing = Boolean(v); },
  };
})();
