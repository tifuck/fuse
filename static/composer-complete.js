/* Trigger parser + ranking for composer @ / path / history palettes. No DOM. */
(() => {
  const root = typeof window !== "undefined" ? window : globalThis;
  const SLASH_BUILTINS = /^(undo|retry|redo|rewind|title|init|compact|help)$/;

  function joinPath(base, name) {
    const b = String(base || "").replace(/\/+$/, "");
    const n = String(name || "").replace(/^\/+/, "");
    if (!b) return n;
    if (!n) return b;
    return b + "/" + n;
  }

  function unique(list, keyFn) {
    const seen = new Set();
    const out = [];
    for (const item of list) {
      const key = keyFn(item);
      if (!key || seen.has(key)) continue;
      seen.add(key);
      out.push(item);
    }
    return out;
  }

  function insideFenceOrUrl(text, caret) {
    const before = text.slice(0, caret);
    const line = before.split("\n").pop() || "";
    let ticks = 0;
    for (const ch of line) if (ch === "`") ticks += 1;
    if (ticks % 2 === 1) return true;
    if (/https?:\/\/\S*$/i.test(line) || /mailto:\S*$/i.test(line)) return true;
    return false;
  }

  function knownPathStrings(ctx) {
    const out = [];
    const listing = ctx && ctx.listing;
    const base = listing && listing.path;
    for (const entry of (listing && listing.entries) || []) {
      const full = joinPath(base, entry.name);
      out.push(full);
      out.push(String(entry.name || ""));
      if (entry.kind === "dir" || entry.kind === "directory") {
        out.push(full + "/");
        out.push(String(entry.name || "") + "/");
      }
    }
    for (const t of (ctx && ctx.traces) || []) {
      for (const key of ["path", "cwd", "pattern"]) {
        if (t && t[key]) out.push(String(t[key]));
      }
    }
    for (const a of (ctx && ctx.attachments) || []) {
      const name = a && (a.name || a.filename || a.path);
      if (name) out.push(String(name));
    }
    if (ctx && ctx.project && ctx.project.remote_path) out.push(String(ctx.project.remote_path));
    return out.filter(Boolean);
  }

  function tokenMatchesKnown(token, known) {
    const t = token.toLowerCase();
    if (!t) return false;
    return known.some((p) => {
      const pl = String(p || "").toLowerCase();
      if (!pl) return false;
      if (pl.startsWith(t) || pl === t.replace(/\/$/, "")) return true;
      const parts = pl.split(/[\\/]/);
      for (let i = 0; i < parts.length; i += 1) {
        const suffix = parts.slice(i).join("/");
        if (suffix.startsWith(t)) return true;
      }
      return false;
    });
  }

  function trigger(text, caret, ctx) {
    text = String(text || "");
    if (caret == null) caret = text.length;
    caret = Math.max(0, Math.min(caret, text.length));
    if (!text.includes("\n") && text.startsWith("/")) {
      const m = text.match(/^\/([a-z0-9]*(?:-[a-z0-9]*)*)/i);
      return {
        kind: "slash",
        query: m ? (m[1] || "").toLowerCase() : "",
        start: 0,
        end: m ? m[0].length : 1,
      };
    }
    if (insideFenceOrUrl(text, caret)) return null;
    const before = text.slice(0, caret);
    const at = before.match(/@([A-Za-z0-9_./:+-]*)$/);
    if (at) {
      return { kind: "at", query: at[1], start: caret - at[0].length, end: caret };
    }
    const m = before.match(/([^\s]+)$/);
    if (!m) return null;
    const token = m[1];
    const start = caret - token.length;
    if (start === 0 && token.startsWith("/")) return null;
    const isRel = /^(?:\.\.?|~)(?:\/[A-Za-z0-9._/-]*)?$/.test(token) && token.includes("/");
    const isAbsMid = start > 0 && /^\/[A-Za-z0-9._/-]*$/.test(token);
    const known = knownPathStrings(ctx);
    const knownHit = token.includes("/") && tokenMatchesKnown(token, known);
    if (isRel || isAbsMid || knownHit) {
      return { kind: "path", query: token, start, end: caret };
    }
    return null;
  }

  function scoreItem(item, q) {
    const text = String(item.text || "").toLowerCase();
    const query = String(q || "").toLowerCase();
    let score = 0;
    if (query && text.startsWith(query)) score += 3;
    else if (query && text.includes(query)) score += 1;
    if (item.recent) score += 2;
    return score;
  }

  function items(trig, ctx) {
    if (!trig) return [];
    ctx = ctx || {};
    if (trig.kind === "at") return atItems(trig.query, ctx);
    if (trig.kind === "path") return pathItems(trig.query, ctx);
    return [];
  }

  function atItems(query, ctx) {
    const q = String(query || "").toLowerCase();
    const rows = [];
    const listing = ctx.listing;
    const traces = ctx.traces || [];
    traces.forEach((t, idx) => {
      for (const key of ["path", "cwd", "pattern"]) {
        if (!t[key]) continue;
        rows.push({
          kind: "file",
          text: String(t[key]),
          insert: String(t[key]),
          hint: key === "cwd" ? "cwd" : "file",
          recent: idx < 8,
        });
      }
    });
    (ctx.attachments || []).forEach((a) => {
      const name = a.name || a.filename || a.path;
      if (!name) return;
      rows.push({ kind: "file", text: String(name), insert: String(name), hint: "attachment", recent: true });
    });
    ((listing && listing.entries) || []).forEach((entry) => {
      const full = joinPath(listing.path, entry.name);
      rows.push({
        kind: "file",
        text: entry.name,
        insert: full,
        hint: entry.kind === "dir" || entry.kind === "directory" ? "folder" : "file",
      });
    });
    (ctx.projects || []).forEach((p) => {
      const name = p.project_name || p.name || "";
      if (!name) return;
      rows.push({
        kind: "project",
        text: name,
        insert: p.remote_path || name,
        hint: p.host_alias || "project",
      });
    });
    const hosts = new Set();
    (ctx.projects || []).forEach((p) => { if (p.host_alias) hosts.add(p.host_alias); });
    (ctx.threads || []).forEach((t) => { if (t.host_alias) hosts.add(t.host_alias); });
    hosts.forEach((alias) => {
      rows.push({ kind: "host", text: alias, insert: alias, hint: "host" });
    });
    (ctx.threads || []).forEach((t) => {
      const title = (t.title || "").trim();
      if (!title || title.toLowerCase() === "new chat" || t.archived) return;
      const insert = /\s/.test(title) ? `"${title}"` : title;
      rows.push({ kind: "chat", text: title, insert, hint: "chat" });
    });
    (ctx.skills || []).forEach((s) => {
      const slug = s.slug || s.name;
      if (!slug) return;
      rows.push({ kind: "skill", text: slug, insert: slug, hint: s.description || "skill" });
    });
    const filtered = unique(rows, (row) => row.kind + ":" + row.insert.toLowerCase())
      .filter((row) => !q || row.text.toLowerCase().includes(q) || row.insert.toLowerCase().includes(q));
    filtered.sort((a, b) => scoreItem(b, q) - scoreItem(a, q) || a.text.localeCompare(b.text));
    if (!q) return filtered.slice(0, 12);
    return filtered.slice(0, 12);
  }

  function pathItems(query, ctx) {
    const q = String(query || "");
    const known = knownPathStrings(ctx);
    const rows = [];
    known.forEach((p) => {
      if (!tokenMatchesKnown(q, [p]) && !String(p).toLowerCase().includes(q.toLowerCase())) return;
      rows.push({ kind: "file", text: p, insert: p, hint: "path" });
    });
    const listing = ctx.listing;
    ((listing && listing.entries) || []).forEach((entry) => {
      const full = joinPath(listing.path, entry.name);
      if (!tokenMatchesKnown(q, [full, entry.name])) return;
      rows.push({
        kind: "file",
        text: full,
        insert: full,
        hint: entry.kind === "dir" || entry.kind === "directory" ? "folder" : "file",
      });
    });
    const filtered = unique(rows, (row) => row.insert.toLowerCase());
    filtered.sort((a, b) => scoreItem(b, q) - scoreItem(a, q) || a.text.localeCompare(b.text));
    return filtered.slice(0, 12);
  }

  function history(ctx) {
    const prompts = (ctx && ctx.prompts) || [];
    const out = [];
    const seen = new Set();
    for (let i = prompts.length - 1; i >= 0; i -= 1) {
      const text = String(prompts[i] || "").trim();
      if (!text) continue;
      const slash = text.match(/^\/([a-z0-9]+)\b/i);
      if (slash && SLASH_BUILTINS.test(slash[1].toLowerCase())) continue;
      const key = text.toLowerCase();
      if (seen.has(key)) continue;
      seen.add(key);
      out.push(text);
      if (out.length >= 20) break;
    }
    return out;
  }

  root.FuseComposerComplete = {
    trigger,
    items,
    history,
    joinPath,
  };
})();
