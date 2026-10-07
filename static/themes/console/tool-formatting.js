// tool-formatting presentation. Dependencies are live getters supplied by index.js.
export function create(deps) {
function isSearchTool(name) {
    return name === "web_search" || name === "search";
  }

function stackSpec(name) {
    return STACK[name] || null;
  }

function stackKey(name) {
    const spec = stackSpec(name);
    if (spec) return "stack:" + spec.family;
    if (isSearchTool(name)) return "search";
    if (name === "read_url") return "page";
    if (name === "run_terminal_command") return "shell";
    if (name === "monitor") return "monitor";
    if (name === "get_command_output") return "job";
    if (name === "spawn_subagent") return "spawn";
    if (name === "get_subagent_output") return "wait";
    if (name === "list_subagents") return "agents";
    if (name === "search_models") return "models";
    return "one:" + (name || "unknown");
  }

function toolIcon(name, failed, interrupted) {
    if (failed) return STEP_ICONS.error;
    if (interrupted) return STEP_ICONS.stop;
    return STEP_ICONS[TOOL_ICON[name]] || STEP_ICONS.tool;
  }

function shortToolPath(path, trace) {
    const raw = String(path || "").trim();
    if (!raw) return raw;
    const windows = trace?.platform === "windows" || /^[a-z]:[\\/]|^\\\\/i.test(raw);
    const normalized = windows ? raw.replaceAll("\\", "/") : raw;
    if (!normalized.startsWith("/") && !/^[a-z]:\//i.test(normalized)) return raw;
    const bound = deps.sessionProject();
    // Never shorten a different machine's path against this chat's project.
    const sameHost = !trace?.host || trace.host === bound?.host_alias;
    const anchor = String(trace?.project_root || trace?.cwd || (sameHost && bound?.remote_path) || "").trim();
    const base = (windows ? anchor.replaceAll("\\", "/") : anchor).replace(/\/+$/, "");
    const key = windows ? normalized.toLowerCase() : normalized;
    const rootKey = windows ? base.toLowerCase() : base;
    if (base && (key === rootKey || key.startsWith(rootKey + "/"))) {
      if (key === rootKey) return ".";
      return normalized.slice(base.length + 1);
    }
    return raw;
  }

function compactPath(path, trace, cap = 58) {
    const value = shortToolPath(path, trace);
    if (value.length <= cap) return value;
    const parts = value.replaceAll("\\", "/").split("/");
    const leaf = parts.pop() || "";
    const prefix = parts[0] === "" ? (parts[1] || "") ? "/" + parts[1] : "/" : parts[0];
    const join = prefix ? prefix + (prefix === "/" ? "" : "/") + "…/" : "…/";
    let tail = leaf;
    while (parts.length > 1 && (join + parts.at(-1) + "/" + tail).length <= cap) tail = parts.pop() + "/" + tail;
    return join + tail;
  }

function displayCommand(trace) {
    const raw = String(trace?.command || "").trim();
    if (trace?.platform === "windows" || /^[a-z]:[\\/]/i.test(trace?.cwd || "")) return raw;
    // Display-only, literal POSIX prefix. No expansions, semicolons or shell parsing guesses.
    const match = raw.match(/^cd\s+(?:--\s+)?(?:'([^']*)'|"([^"$`\\]*)"|([^\s;&|<>$`\\"']+))\s*&&\s*([\s\S]+)$/);
    if (!match) return raw;
    const target = match[1] ?? match[2] ?? match[3];
    const cwd = String(trace?.cwd || "").replace(/\/+$/, "");
    return target.startsWith("/") && cwd && target.replace(/\/+$/, "") === cwd ? match[4] : raw;
  }

function toolSubject(trace) {
    const name = trace.name;
    if (name === "monitor") return toolText(trace.url || displayCommand(trace) || deps.executionIdentity(trace) || "watch");
    if (deps.isShellExecTool(name)) return toolText(commandFor(trace) || "command");
    if (["read_file", "write_file", "search_replace", "list_dir"].includes(name)) {
      const range = name === "read_file" && Number.isFinite(trace.offset) ? ` :${trace.offset}${Number.isFinite(trace.limit) ? `–${trace.offset + trace.limit - 1}` : ""}` : "";
      return compactPath(trace.path || ".", trace) + range;
    }
    if (["grep", "glob"].includes(name)) return [toolText(trace.pattern || trace.query || "pattern", 90), trace.path ? `in ${compactPath(trace.path, trace, 36)}` : ""].filter(Boolean).join(" ");
    if (["spawn_subagent", "get_subagent_output", "kill_subagent"].includes(name)) {
      const label = toolText(agentDescriptionFor(trace) || "subagent", 120);
      return name === "spawn_subagent" && trace.model ? `${label} · ${toolText(trace.model, 40)}` : label;
    }
    if (name === "read_url") { try { const u = new URL(trace.url); return u.hostname.replace(/^www\./, "") + (u.pathname === "/" ? "" : compactPath(u.pathname, {}, 45)); } catch { return trace.url || "page"; } }
    if (isSearchTool(name)) {
      const n = searchCountFor(trace);
      return searchQueries(trace).length === 1 ? toolText(searchQueries(trace)[0], 100) : `${n} ${n === 1 ? "query" : "queries"}`;
    }
    if (name === "create_artifact" || name === "update_artifact") return trace.title || "artifact";
    if (name === "load_skill") return [trace.preview || "skill", trace.path ? compactPath(trace.path, trace) : ""].filter(Boolean).join(" / ");
    if (name === "fuse-steer") return trace.summary || "New instruction";
    return toolText(trace.query || trace.description || trace.title || trace.preview || trace.path || "", 140);
  }

function toolAction(trace) {
    const labels = { read_file: "Read", list_dir: "List", grep: "Grep", glob: "Glob", write_file: "Write", search_replace: "Edit",
      run_terminal_command: "Shell", get_command_output: "Output", kill_command: "Stop", monitor: "Watch", spawn_subagent: "Agent",
      get_subagent_output: "Result", kill_subagent: "Stop", list_subagents: "Agents", search_models: "Models", search_tools: "Tools",
      load_skill: "Skill", propose_skill: "Draft", todo_write: "Tasks", scratch: "Scratch", read_url: "Page", web_search: "Search", search: "Search",
      create_artifact: "Create", update_artifact: "Update", "fuse-steer": "Notice" };
    return labels[trace.name] || String(trace.name || "Tool").replaceAll("_", " ");
  }

function pageHost(url) {
    try {
      return url ? new URL(url).hostname.replace(/^www\./, "") : "";
    } catch (_) {
      return "";
    }
  }

function stripPreview(raw, max) {
    const text = String(raw || "")
      .replace(/```[\s\S]*?```/g, " ")
      .replace(/`+/g, "")
      .replace(/[*_>#~\-]+/g, " ")
      .replace(/\s+/g, " ")
      .trim();
    if (!text || text === "still running") return "";
    return shortToolText(text, max || 80);
  }

function relatedTrace(match) {
    for (let i = deps.messages.length - 1; i >= 0; i -= 1) {
      const found = (deps.messages[i].tools || []).find(match);
      if (found) return found;
    }
    return null;
  }

function agentDescriptionFor(trace) {
    if (trace && trace.description) return trace.description;
    const id = trace && trace.child_id;
    return id ? relatedTrace((t) => t.name === "spawn_subagent" && t.child_id === id && t.description)?.description || "" : "";
  }

function commandFor(trace) {
    const own = displayCommand(trace);
    if (own) return own;
    const origin = commandOrigin(trace);
    return origin ? displayCommand(origin) : "";
  }

function commandOrigin(trace) {
    const id = trace && (trace.command_id || trace.child_id || trace.job_id);
    return id ? relatedTrace((t) => t !== trace && (t.name === "run_terminal_command" || t.name === "monitor") && (t.command_id === id || t.child_id === id) && t.command) : null;
  }

function agentSubject(trace) {
    const description = stripPreview(agentDescriptionFor(trace), 60);
    if (description) return description;
    if (trace && trace.status === "error") return "";
    return stripPreview(trace && trace.preview, 80);
  }

function toolText(raw, max) {
    const text = String(raw || "").replace(/\s+/g, " ").trim();
    const cap = max || 4000;
    if (text.length <= cap) return text;
    return text.slice(0, cap - 1) + "…";
  }

function shortToolText(raw, max) {
    return toolText(raw, max || 72);
  }

function cleanToolOutput(trace, text) {
    let out = String(text || "").replace(/^From host [^\n]*:\n/, "");
    const root = String((trace && (trace.project_root || trace.cwd)) || "").replace(/\/+$/, "");
    if (root.length > 1 && root !== "~" && ["grep", "glob", "list_dir"].includes(trace && trace.name)) {
      out = out.split(root + "/").join("");
    }
    return out;
  }

function splitNumberedLines(text) {
    const lines = String(text || "").replace(/\s+$/, "").split("\n");
    // A truncated read can end mid-line ("82"); drop that fragment.
    if (lines.length > 1 && !/^\s*\d+:/.test(lines[lines.length - 1])) lines.pop();
    const rows = lines.map((line) => line.match(/^\s*(\d+):\s?(.*)$/));
    if (!lines.length || rows.some((row) => !row)) return null;
    const start = Number(rows[0][1]);
    if (!rows.every((row, i) => Number(row[1]) === start + i)) return null;
    return { start, text: rows.map((row) => row[2]).join("\n") };
  }

function fileBodyHtml(trace, body, limit) {
    const parsed = trace.name === "read_file" ? splitNumberedLines(body) : null;
    return consoleNumberedHtml(parsed ? parsed.text : body, { limit, lang: consoleLangForPath(trace.path), start: parsed ? parsed.start : 1 });
  }

function kvListHtml(rows, limit = 12) {
    const items = rows.filter((row) => row && row.title).map((row) =>
      `<div class="chat-kv-row">${row.state ? `<span class="chat-kv-dot is-${deps.escapeHtml(row.state)}" aria-hidden="true"></span>` : ""}` +
      `<span class="chat-kv-title">${deps.escapeHtml(row.title)}</span>${row.detail ? `<span class="chat-kv-detail">${deps.escapeHtml(row.detail)}</span>` : ""}</div>`);
    if (!items.length) return "";
    const extra = items.length > limit ? `<div class="chat-console-more">… +${items.length - limit} more</div>` : "";
    return `<div class="chat-kv-list">${items.slice(0, limit).join("")}</div>${extra}`;
  }

function structuredToolHtml(trace, output) {
    const name = trace && trace.name;
    const text = String(output || "").trim();
    if (!text) return null;
    const json = () => { try { return JSON.parse(text); } catch (_) { return null; } };
    // Retained output can be cut mid-payload; salvage the complete objects.
    const objects = () => (text.match(/\{[^{}]*\}/g) || []).map((raw) => { try { return JSON.parse(raw); } catch (_) { return null; } }).filter(Boolean);
    if (name === "spawn_subagent" || name === "get_subagent_output") {
      const result = text.match(/<task_result>([\s\S]*?)(?:<\/task_result>|$)/);
      if (!result) return null;
      const body = result[1].trim();
      if (!body || /^The task is working in the background\./.test(body)) return `<p class="chat-tool-note">Running in the background.</p>`;
      return `<div class="chat-tool-md chat-md">${deps.renderMarkdown(body, false, [])}</div>`;
    }
    if (name === "list_subagents") {
      const data = json();
      const list = data ? [...(data.live || []), ...(data.finished || [])] : objects().filter((row) => row.id && row.status);
      if (!data && !list.length) return null;
      const rows = list.map((row) => ({
        state: row.status === "running" || row.status === "queued" ? "live" : row.status === "completed" ? "done" : row.status === "failed" ? "fail" : "idle",
        title: row.description || row.id,
        detail: [row.status, row.model].filter(Boolean).join(" · "),
      }));
      return kvListHtml(rows) || `<p class="chat-tool-note">No subagents.</p>`;
    }
    if (name === "search_models") {
      const data = json();
      const models = data && Array.isArray(data.models) ? data.models : objects().filter((row) => row.id);
      if (!models.length && !data) return null;
      return kvListHtml(models.map((row) => ({ title: row.name || row.id, detail: [row.id, row.provider].filter(Boolean).join(" · ") }))) || `<p class="chat-tool-note">No matching models.</p>`;
    }
    if (name === "search_tools") {
      const data = json();
      if (!data) return null;
      const loaded = [...(data.loaded || []), ...(data.already_loaded || [])];
      return loaded.length ? kvListHtml(loaded.map((tool) => ({ title: tool }))) : `<p class="chat-tool-note">No tools loaded.</p>`;
    }
    if (name === "scratch") {
      const data = json();
      if (!data || typeof data.slots !== "object" || !data.slots) return null;
      return kvListHtml(Object.entries(data.slots).map(([key, size]) => ({ title: key, detail: `${Number(size).toLocaleString()} chars` })));
    }
    if (name === "load_skill") {
      const start = text.search(/^#\s/m);
      if (start < 0) return null;
      return `<div class="chat-tool-md chat-md">${deps.renderMarkdown(text.slice(start), false, [])}</div>`;
    }
    if (name === "write_file" && /^wrote\s/i.test(text) && !text.includes("\n")) return "";
    return null;
  }

function webResultsHtml(results, limit = 8) {
    const rows = (results || []).map((row) => {
      const url = deps.safeUrl(row.url);
      if (!url || !/^https?:/i.test(url)) return "";
      let domain = url;
      try { domain = new URL(url).hostname.replace(/^www\./, ""); } catch (_) {}
      return `<a class="chat-web-row" href="${deps.escapeHtml(url)}" target="_blank" rel="noopener noreferrer"><span class="chat-web-title">${deps.escapeHtml(row.title || domain)}</span><span class="chat-web-domain">${deps.escapeHtml(domain)}</span></a>`;
    }).filter(Boolean);
    if (!rows.length) return "";
    const extra = rows.length > limit ? `<div class="chat-console-more">… +${rows.length - limit} more</div>` : "";
    return `<div class="chat-web-list">${rows.slice(0, limit).join("")}</div>${extra}`;
  }

function consoleArgText(value, cap = 90) {
    return toolText(String(value || "").trim(), cap);
  }

function consoleCallHtml(trace, presentation, full = false) {
    const name = trace && trace.name;
    const running = Boolean(presentation && presentation.running);
    const host = trace.host || "";
    const hostHtml = host ? `<span class="chat-tool-host" title="Host ${deps.escapeHtml(host)}">${deps.escapeHtml(host)}</span>` : "";
    const clock = (presentation) ? deps.ccCallClockHtml(trace, presentation) : "";
    // Console: a running call reads as Claude Code's live line, one row
    // counting up ("Running npm test · 12s"); it prints as Bash(…) once done.
    const pending = /^(queued|cancelling)$/i.test(presentation && presentation.statusLabel);
    const live = (running) && !full && !deps.toolBackground(trace) && !pending ? deps.ccLiveText(trace, 1000) : "";
    if (live) return `<span class="chat-console-call is-live">${deps.escapeHtml(live)}</span>${hostHtml}${clock}`;
    if ((running) && name === "run_terminal_command" && pending) {
      return `<span class="chat-console-call is-live">${deps.escapeHtml(presentation.statusLabel)}</span>${hostHtml}${clock}`;
    }
    const verb = deps.consoleVerb(name, trace);
    if (["kill_command", "todo_write", "list_subagents"].includes(name) || (name === "scratch" && !scratchAction(trace))) {
      // A bare call keeps the shared tail too: one clock and one host badge
      // per call, like every other row.
      return `<span class="chat-console-call"><span class="chat-tool-action">${deps.escapeHtml(verb)}</span></span>${hostHtml}${clock}`;
    }
    if (name === "run_terminal_command" && running && !deps.toolBackground(trace) && !full && false) {
      const label = String((trace && trace.progressLabel) || "").trim() || "Running";
      return `<span class="chat-console-call"><span class="chat-tool-action">${deps.escapeHtml(label)}</span></span>`;
    }
    let inner = "";
    // A wrapped path or command breaks after a slash, never inside a name.
    const path = ["read_file", "write_file", "search_replace", "list_dir", "run_terminal_command", "get_command_output"].includes(name);
    if (["read_file", "write_file", "search_replace", "list_dir"].includes(name)) {
      inner = consoleArgText(compactPath(trace.path || ".", trace, 88), 88);
    } else if ((name === "run_terminal_command") && deps.ccSedEdit(displayCommand(trace))) {
      inner = consoleArgText(compactPath(deps.ccSedEdit(displayCommand(trace)).file, trace, 88), 88);
    } else if (name === "run_terminal_command") {
      inner = full ? displayCommand(trace) || "command" : consoleArgText(displayCommand(trace) || "command", 140);
    } else if (name === "get_command_output") {
      // Wait names what it waits on: the command's description, else the
      // command itself (an interrupted Wait carries only the job id).
      const id = trace.command_id || trace.child_id || trace.job_id;
      inner = consoleArgText(commandOrigin(trace)?.description || commandFor(trace) || (id ? `job ${id}` : "command"), 140);
    } else if (name === "monitor") {
      inner = consoleArgText(trace.description || trace.url || displayCommand(trace) || "watch", 100);
    } else if ((name === "grep" || name === "glob")) {
      // Claude Code: Search(pattern: "x", path: "src")
      const pattern = consoleArgText(trace.pattern || trace.query || "", 80);
      const where = String(trace.path || "").trim();
      inner = `pattern: "${pattern}"` + (where ? `, path: "${consoleArgText(compactPath(where, trace, 60), 60)}"` : "");
    } else if (name === "grep" || name === "glob") {
      inner = consoleArgText(trace.pattern || trace.query || "pattern", 90);
    } else if (name === "read_url") {
      inner = consoleArgText(trace.url || "page", 100);
    } else if (isSearchTool(name)) {
      const queries = searchQueries(trace);
      inner = queries.length ? `"${consoleArgText(queries[0], 80)}"${queries.length > 1 ? ` +${queries.length - 1}` : ""}` : "";
    } else if (["spawn_subagent", "get_subagent_output"].includes(name)) {
      inner = consoleArgText(agentSubject(trace) || "subagent", 90);
    } else if (name === "kill_subagent") {
      inner = consoleArgText(agentDescriptionFor(trace) || trace.child_id || "subagent", 90);
    } else if (name === "scratch") {
      inner = consoleArgText(scratchAction(trace), 90);
    } else if (name === "create_artifact" || name === "update_artifact") {
      inner = consoleArgText(trace.title || "artifact", 80);
    } else if (name === "load_skill") {
      // The skill row reads as its outcome sentence ("Loaded the ponytail
      // skill"). The name keeps its cap and ellipsis; failures keep the
      // family's Error: fold below, interruptions their established mark.
      const skill = consoleArgText(trace.preview || "", 80);
      const tail = skill ? `<span class="chat-console-val">${deps.escapeHtml(skill)}</span> skill` : "skill";
      const p = presentation || deps.toolPresentation(trace, false);
      const sentence = p && p.failed ? `Failed to load the ${tail}`
        : p && (p.running || p.interrupted) ? `Loading the ${tail}`
        : `Loaded the ${tail}`;
      return `<span class="chat-console-call">${sentence}</span>` + hostHtml + clock;
    } else {
      inner = consoleArgText(toolSubject(trace), 90);
    }
    const call = inner
      ? `<span class="chat-console-call"><span class="chat-tool-action">${deps.escapeHtml(verb)}</span><span class="chat-console-paren">(</span><span class="chat-console-val">${path ? deps.escapeHtml(inner).replace(/\//g, "/<wbr>") : deps.escapeHtml(inner)}</span><span class="chat-console-paren">)</span></span>`
      : `<span class="chat-console-call"><span class="chat-tool-action">${deps.escapeHtml(verb)}</span></span>`;
    return call + hostHtml + clock;
  }

function scratchAction(trace) {
    const own = deps.ccFlat(trace && trace.description);
    if (own) return own;
    const read = String((trace && trace.preview) || "").match(/^(\S+) \(\d+ chars\)$/);
    return read ? `Read ${read[1]}` : "";
  }

function consoleOutputText(trace, failed, running) {
    if (failed) return String(trace.error || trace.summary || trace.preview || "failed").trim();
    if (typeof trace.output === "string" && trace.output) return cleanToolOutput(trace, trace.output);
    const preview = [trace.preview, trace.summary].filter((value) => {
      const text = String(value || "").trim();
      return text && text !== "still running" && text !== "background task started";
    }).join("\n\n");
    if (preview) return preview;
    if (running) return "Running…";
    if (trace.command) return String(trace.command);
    return "";
  }

function consoleMatchCount(trace) {
    const n = Number(trace && (trace.match_count ?? trace.count ?? trace.n));
    if (Number.isFinite(n) && n > 0) return n;
    const body = String((trace && (trace.output || trace.preview)) || "");
    const lines = body.split("\n").map((line) => line.trim()).filter(Boolean);
    if (lines.length > 1) return lines.length;
    const hits = body.match(/:\d+:/g);
    if (hits && hits.length) return hits.length;
    return lines.length;
  }

function consoleLineCount(trace) {
    const preview = String((trace && (trace.preview || trace.summary)) || "");
    const named = typeof trace?.output === "string" && trace.output ? null : preview.match(/(\d+)\s+lines?\b/i);
    if (named) return Number(named[1]);
    const body = typeof trace.output === "string" && trace.output
      ? cleanToolOutput(trace, trace.output)
      : (deps.looksLikeDiff(preview) ? "" : preview);
    if (!body) return 0;
    const text = String(body).replace(/\s+$/, "");
    return text ? text.split("\n").length : 0;
  }

function consoleLangForPath(path) {
    const ext = String(path || "").split(".").pop().toLowerCase();
    const map = {
      html: "xml", htm: "xml", xml: "xml", svg: "xml",
      js: "javascript", mjs: "javascript", cjs: "javascript", jsx: "javascript",
      ts: "typescript", tsx: "typescript",
      py: "python", rb: "ruby", go: "go", rs: "rust",
      css: "css", scss: "scss", json: "json", md: "markdown",
      sh: "bash", bash: "bash", zsh: "bash", c: "c", h: "c",
      cpp: "cpp", cc: "cpp", yml: "yaml", yaml: "yaml", toml: "ini",
    };
    return map[ext] || "";
  }

function consoleNumberedHtml(text, { limit, lang, start = 1 } = {}) {
    const lines = String(text || "").replace(/\s+$/, "").split("\n");
    if (!lines.length || (lines.length === 1 && !lines[0])) return "";
    const cap = Number.isFinite(limit) ? Math.min(limit, lines.length) : lines.length;
    const width = String(start + cap - 1).length;
    const rows = lines.slice(0, cap).map((line, i) => {
      const n = String(start + i).padStart(width, " ");
      const body = lang ? deps.highlightCode(lang, line) : deps.escapeHtml(line);
      return `<div class="chat-console-nline"><span class="chat-console-n">${n}</span><span class="chat-console-nbody">${body || " "}</span></div>`;
    }).join("");
    const extra = lines.length > cap
      ? `<div class="chat-console-more">… +${lines.length - cap} lines</div>`
      : "";
    return `<div class="chat-console-lines">${rows}</div>${extra}`;
  }

const STEP_ICONS = {
    think: '<svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true"><path d="M12 5a3 3 0 1 0-5.997.125 4 4 0 0 0-2.526 5.77 4 4 0 0 0 .556 6.588A4 4 0 1 0 12 18Z" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/><path d="M12 5a3 3 0 1 1 5.997.125 4 4 0 0 1 2.526 5.77 4 4 0 0 1-.556 6.588A4 4 0 1 1 12 18Z" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/><path d="M15 13a4.5 4.5 0 0 1-3-4 4.5 4.5 0 0 1-3 4" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    search: '<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><circle cx="7.2" cy="7.2" r="3.6" fill="none" stroke="currentColor" stroke-width="1.3"/><path d="M10 10.1L13.4 13.5" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/></svg>',
    read: '<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><path d="M8 4.1C7.2 3.2 6 2.7 4.4 2.7H2.4v9.3h2.4c1.5 0 2.6.5 3.2 1.3" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"/><path d="M8 4.1c.8-.9 2-1.4 3.6-1.4h2v9.3h-2.4c-1.5 0-2.6.5-3.2 1.3" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"/><path d="M8 4.1v8.8" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/></svg>',
    link: '<svg viewBox="0 0 16 16" width="13" height="13" aria-hidden="true"><path d="M6.4 3.2H3.6A1.2 1.2 0 0 0 2.4 4.4v8a1.2 1.2 0 0 0 1.2 1.2h8a1.2 1.2 0 0 0 1.2-1.2V9.6" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"/><path d="M9.2 2.6H13.4V6.8M7.6 8.4L13.4 2.6" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    file: '<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><path d="M3.4 4.4h9.2M3.4 8h9.2M3.4 11.6h6.2" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/></svg>',
    tool: '<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><path d="M8 2.6l5.4 5.4L8 13.4 2.6 8z" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"/><circle cx="8" cy="8" r="1.1" fill="currentColor"/></svg>',
    model: '<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><path d="M8 2.4l5 2.8v5.6l-5 2.8-5-2.8V5.2z" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"/><path d="M3.2 5.3L8 8l4.8-2.7M8 8v5.4" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"/></svg>',
    todo: '<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><path d="M2.6 4.4l1.1 1.1 1.9-2.1M2.6 10.4l1.1 1.1 1.9-2.1M7.8 4.6h5.6M7.8 10.6h5.6" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    scratch: '<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><rect x="3.2" y="2.8" width="9.6" height="10.8" rx="1.3" fill="none" stroke="currentColor" stroke-width="1.3"/><path d="M5.6 2v1.6M8 2v1.6M10.4 2v1.6M5.6 7h4.8M5.6 9.8h3.2" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/></svg>',
    steer: '<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><path d="M3 12.6V8.2c0-1.7 1.3-3 3-3h7" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/><path d="M10.4 2.6L13 5.2l-2.6 2.6" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    error: '<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><path d="M8 2.8l5.1 8.9H2.9z" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"/><path d="M8 6.4v2.4" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/><circle cx="8" cy="11" r="0.55" fill="currentColor"/></svg>',
    stop: '<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><rect x="4.8" y="4.8" width="6.4" height="6.4" rx="1.2" fill="none" stroke="currentColor" stroke-width="1.3"/></svg>',
    artifact: '<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><rect x="2.8" y="3.6" width="10.4" height="8.8" rx="1.4" fill="none" stroke="currentColor" stroke-width="1.3"/><path d="M5 9.1l1.6-1.9 1.5 1.6 2.1-2.6 2.1 2.9" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"/></svg>',
    web: '<svg viewBox="0 0 16 16" width="13" height="13" aria-hidden="true"><circle cx="8" cy="8" r="5.1" fill="none" stroke="currentColor" stroke-width="1.25"/><ellipse cx="8" cy="8" rx="2.15" ry="5.1" fill="none" stroke="currentColor" stroke-width="1.25"/><path d="M2.9 8h10.2" fill="none" stroke="currentColor" stroke-width="1.25"/></svg>',
    folder: '<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><path d="M2.6 4.7h3.2l1.1 1.4H13.4v6.4H2.6z" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"/></svg>',
    file: '<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><path d="M4.3 2.7h5.1L11.8 5.2v8.1H4.3z" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"/><path d="M9.4 2.7v2.5h2.4M6.1 8.4h3.8M6.1 10.6h2.6" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    grep: '<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><circle cx="7" cy="7" r="3.3" fill="none" stroke="currentColor" stroke-width="1.3"/><path d="M9.5 9.6L12.8 13M5.6 7h2.8" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/></svg>',
    glob: '<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><path d="M8 2.7v10.6M2.7 8h10.6M4.4 4.4l7.2 7.2M11.6 4.4l-7.2 7.2" fill="none" stroke="currentColor" stroke-width="1.25" stroke-linecap="round"/></svg>',
    write: '<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><path d="M4.2 2.8h5L11.7 5.3v8H4.2z" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"/><path d="M9.2 2.8v2.5h2.5M8 7.2v4.4M5.8 9.4h4.4" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    edit: '<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><path d="M9.3 3.4l3.3 3.3-6.6 6.6H2.7v-3.3z" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"/><path d="M8.2 4.5l3.3 3.3" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/></svg>',
    shell: '<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><rect x="2.5" y="3.2" width="11" height="9.6" rx="1.7" fill="none" stroke="currentColor" stroke-width="1.3"/><path d="M5.4 6.4l2.1 1.6-2.1 1.6M8.6 10.1h2.3" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    watch: '<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><circle cx="8" cy="8" r="5.1" fill="none" stroke="currentColor" stroke-width="1.3"/><path d="M8 5.2v3.1l2 1.2" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    tools: '<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><path d="M6.2 9.8L3.4 12.6M10.4 3.2l2.4 2.4-6.1 6.1H4.3V9.3z" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"/></svg>',
    skill: '<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><path d="M3.1 3.2h3.5c.8 0 1.4.3 1.4 1.1 0-.8.6-1.1 1.4-1.1h3.5v9.1H9.5c-.9 0-1.5.3-1.5 1.1 0-.8-.6-1.1-1.5-1.1H3.1z" fill="none" stroke="currentColor" stroke-width="1.25" stroke-linejoin="round"/><path d="M8 4.3v9.1" fill="none" stroke="currentColor" stroke-width="1.25"/></svg>',
    await: '<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><path d="M4.6 2.6h6.8M4.6 13.4h6.8M5.4 2.6c0 2.6 2.6 3.6 2.6 5.4S5.4 10.8 5.4 13.4M10.6 2.6c0 2.6-2.6 3.6-2.6 5.4s2.6 2.8 2.6 5.4" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    agent: '<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><circle cx="5.2" cy="6.2" r="1.7" fill="none" stroke="currentColor" stroke-width="1.3"/><circle cx="10.8" cy="6.2" r="1.7" fill="none" stroke="currentColor" stroke-width="1.3"/><path d="M3.5 12.2c.4-1.6 1.5-2.5 3-2.5s2.6.9 3 2.5M9.3 9.7c.5-.3 1.1-.5 1.7-.5 1.5 0 2.6.9 3 2.5" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/></svg>',
  };

const TOOL_ICON = {
    read_file: "file",
    list_dir: "folder",
    grep: "grep",
    glob: "glob",
    write_file: "write",
    search_replace: "edit",
    run_terminal_command: "shell",
    get_command_output: "shell",
    kill_command: "stop",
    monitor: "watch",
    search_tools: "tools",
    load_skill: "skill",
    propose_skill: "skill",
    todo_write: "todo",
    scratch: "scratch",
    spawn_subagent: "agent",
    get_subagent_output: "await",
    kill_subagent: "stop",
    list_subagents: "agent",
    search_models: "model",
    read_url: "link",
    web_search: "web",
    search: "web",
    create_artifact: "artifact",
    update_artifact: "artifact",
    "fuse-steer": "steer",
  };

const STACK = {
    read_file: { family: "read", verb: "Read", verbing: "Reading", one: "file", many: "files" },
    list_dir: { family: "list", verb: "Listed", verbing: "Listing", one: "directory", many: "directories" },
    grep: { family: "grep", verb: "Grepped", verbing: "Grepping", one: "pattern", many: "patterns" },
    glob: { family: "glob", verb: "Globbed", verbing: "Globbing", one: "pattern", many: "patterns" },
    write_file: { family: "write", verb: "Wrote", verbing: "Writing", one: "file", many: "files" },
    search_replace: { family: "edit", verb: "Edited", verbing: "Editing", one: "file", many: "files" },
  };

function visibleTraces(traces) {
    const list = Array.isArray(traces) ? traces : [];
    // A returned poll and a subsequent wait are separate invocations even
    // when they reference the same execution. Count and identify both.
    return list.map((trace, index) => ({ trace, index }));
  }

function groupTraces(traces) {
    const groups = [];
    visibleTraces(traces).forEach(({ trace, index }) => {
      const last = groups[groups.length - 1];
      const key = stackKey(trace && trace.name);
      groups.push({ name: (trace && trace.name) || "unknown", key, items: [{ trace, index }] });
    });
    return groups;
  }

function normErr(s) {
    return String(s || "").replace(/\s+/g, " ").trim().toLowerCase();
  }

function failNoteHtml(message) {
    const text = String(message || "").trim();
    if (!text) return "";
    return deps.detailSectionHtml("Error", text, "is-error");
  }

function stepOpenAt(map, key) {
    return Boolean(map && map[key] === true);
  }

function searchCountFor(trace) {
    if (!trace) return 0;
    if (Array.isArray(trace.queries) && trace.queries.length) return trace.queries.length;
    return 1;
  }

function searchQueries(trace) {
    if (!trace) return [];
    if (Array.isArray(trace.queries) && trace.queries.length) return trace.queries.filter((q) => typeof q === "string" && q.trim());
    if (typeof trace.query === "string" && trace.query.trim()) return [trace.query];
    return [];
  }

  return { isSearchTool, stackSpec, stackKey, toolIcon, shortToolPath, compactPath, displayCommand, toolSubject, toolAction, pageHost, stripPreview, relatedTrace, agentDescriptionFor, commandFor, commandOrigin, agentSubject, toolText, shortToolText, cleanToolOutput, splitNumberedLines, fileBodyHtml, kvListHtml, structuredToolHtml, webResultsHtml, consoleArgText, consoleCallHtml, scratchAction, consoleOutputText, consoleMatchCount, consoleLineCount, consoleLangForPath, consoleNumberedHtml, STEP_ICONS, TOOL_ICON, STACK, visibleTraces, groupTraces, normErr, failNoteHtml, stepOpenAt, searchCountFor, searchQueries };
}
