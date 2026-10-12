// Context meter: token estimation, occupancy, compaction.
import { S } from './state.js';
import { escapeHtml, formatTokens } from './core.js';
import { newSession } from './state.js';

// Below this many prompt tokens a lost cache is not worth mentioning
// (matches cache_watch.MIN_TOKENS).
const CACHE_MIN_TOKENS = 4096;

const TOOL_FAMILIES = {
  web_search: "web search",
  read_url: "web search",
  run_terminal_command: "shell",
  get_command_output: "shell",
  kill_command: "shell",
  monitor: "shell",
  read_file: "files",
  list_dir: "files",
  grep: "files",
  glob: "files",
  write_file: "files",
  search_replace: "files",
  create_artifact: "artifacts",
  update_artifact: "artifacts",
  spawn_subagent: "subagents",
  get_subagent_output: "subagents",
  kill_subagent: "subagents",
  list_subagents: "subagents",
  search_models: "models",
  search_tools: "catalog",
  load_skill: "skills",
  propose_skill: "skills",
  todo_write: "todos",
  read_transcript: "transcript",
};

function estimateChars(text) {
  const s = String(text || "");
  return s ? Math.max(1, Math.ceil(s.length / 4)) : 0;
}

function toolFamily(name) {
  const key = String(name || "").trim();
  return TOOL_FAMILIES[key] || (key ? key.replace(/_/g, " ") : "tools");
}

function estimateTrace(trace) {
  if (!trace || typeof trace !== "object") return 0;
  const preview = String(trace.preview || "");
  const summary = String(trace.summary || "");
  const output = summary.startsWith(preview) ? summary : preview.startsWith(summary) ? preview : [preview, summary].filter(Boolean).join("\n");
  const bits = [trace.name, trace.command, trace.path, trace.url, trace.pattern, output, trace.error, trace.description];
  const queries = [...new Set([trace.query, ...(Array.isArray(trace.queries) ? trace.queries : [])].filter(q => typeof q === "string" && q))];
  bits.push(...queries);
  return estimateChars(bits.filter(Boolean).join(" "));
}

// One fixed row set for every context card (main agent and subagents), so
// the breakdown keeps its shape between turns; empty rows dim, not vanish.
function partsRows(parts) {
  const n = (key) => Math.max(0, Number(parts?.[key]) || 0);
  const rows = [
    ["System", n("system"), ""],
    ["History", n("history"), ""],
    ["Messages", n("messages"), "child"],
    ["Thinking", n("thinking"), "child"],
    ["Tool calls", n("tool_calls"), "child"],
  ];
  const families = parts?.tool_families || {};
  Object.keys(families).sort().forEach((name) => {
    const value = Number(families[name]) || 0;
    if (value) rows.push([name.replace(/\b\w/g, (ch) => ch.toUpperCase()), value, "grand"]);
  });
  rows.push(["Tool schemas", n("tools"), ""]);
  return rows;
}

// Shared context card: built once per popover, then only its numbers are
// updated, so the text is stable while a turn streams.
function fillContextCard(pop, card) {
  if (!pop) return;
  if (!pop.querySelector("[data-ctx-used]")) {
    pop.innerHTML =
      '<div class="chat-context-pop-head"><p class="chat-context-pop-kicker">Context</p>' +
      '<p class="chat-context-pop-meter"><span data-ctx-used></span><span class="chat-context-pop-pct" data-ctx-pct></span></p></div>' +
      '<div class="chat-context-pop-bar" aria-hidden="true"><i data-ctx-bar></i></div>' +
      '<ul class="chat-context-pop-parts" data-ctx-parts></ul>' +
      '<div class="chat-context-pop-foot"><p class="chat-context-pop-note" data-ctx-note></p>' +
      (card.compact ? '<button type="button" class="btn sm" data-context-pop-compact>Compact now</button>' : "") +
      "</div>";
  }
  const ratio = Math.max(0, Math.min(1, card.ratio || 0));
  const pct = Math.round(ratio * 100);
  pop.querySelector("[data-ctx-used]").textContent = `${formatTokens(card.used)} / ${formatTokens(card.limit)}`;
  pop.querySelector("[data-ctx-pct]").textContent = `${pct}%`;
  const bar = pop.querySelector("[data-ctx-bar]");
  bar.style.width = `${(ratio * 100).toFixed(1)}%`;
  pop.classList.toggle("is-warn", Boolean(card.warn) && !card.auto);
  pop.classList.toggle("is-auto", Boolean(card.auto));
  const list = pop.querySelector("[data-ctx-parts]");
  const html = card.rows.map(([label, tokens, nest]) => {
    const cls = [nest === "child" ? "is-child" : "", nest === "grand" ? "is-grand" : "", tokens <= 0 ? "is-zero" : ""].filter(Boolean).join(" ");
    return `<li${cls ? ` class="${cls}"` : ""}><span>${escapeHtml(label)}</span><span>${formatTokens(tokens)}</span></li>`;
  }).join("");
  if (list.dataset.html !== html) {
    list.innerHTML = html;
    list.dataset.html = html;
  }
  pop.querySelector("[data-ctx-note]").textContent = card.autoEnabled
    ? `Auto-compacts at ${Math.round((card.autoRatio || 0) * 100)}%`
    : "Auto-compact is off";
}

let contextRecountPending = false;

let _meterDebounce = 0;

export function createContext(ctx) {
  const { net, picker, sync, traces, ui } = ctx;
    function currentModel() {
    return S.modelsById.get(S.modelId) || null;
  }

    // Text-only models get a short note instead of each image (app/model_caps.py).
  function imageTokens() {
    const model = currentModel();
    return model && model.image === false ? 30 : 1500;
  }

    function chatHasImages() {
    const isImage = (att) => att && att.kind === "image";
    return S.pendingAttachments.some(isImage) || S.messages.some((msg) => (msg.attachments || []).some(isImage) || (msg.tools || []).some((trace) => ui.toolImage(trace)));
  }

    function warnTextOnlyModel() {
    const model = currentModel();
    if (model && model.image === false && chatHasImages()) {
      picker.setHint(model.name + " can't view images. They'll be skipped and kept for image models.");
    }
  }

    function modelWindow() {
    const model = currentModel();
    return (model && model.contextWindow) || Number(S.compactCfg.default_window) || 128000;
  }

    function localOccupancy() {
    let messagesN = 0;
    let thinking = 0;
    const families = {};
    for (const msg of S.messages) {
      messagesN += 4;
      const content = msg.content;
      if (typeof content === "string") messagesN += estimateChars(content);
      else if (Array.isArray(content)) {
        for (const part of content) {
          if (typeof part === "string") messagesN += estimateChars(part);
          else if (part && typeof part === "object") {
            if (part.type === "image_url" || part.type === "input_image") messagesN += imageTokens();
            else messagesN += estimateChars(part.text || part.content || "");
          }
        }
      }
      thinking += estimateChars(msg.reasoning);
      for (const call of msg.tool_calls || []) {
        const fn = (call && call.function) || {};
        const name = fn.name || call.name || "tools";
        const family = toolFamily(name);
        families[family] = (families[family] || 0) + estimateChars(String(name) + String(fn.arguments || ""));
      }
      for (const trace of msg.tools || []) {
        const family = toolFamily(trace && trace.name);
        families[family] = (families[family] || 0) + estimateTrace(trace);
      }
      for (const att of msg.attachments || []) {
        if (String(att.kind || att.mime || "").startsWith("image")) messagesN += imageTokens();
        else messagesN += estimateChars(att.data || att.text || "");
      }
    }
    const toolCalls = Object.values(families).reduce((sum, n) => sum + n, 0);
    const history = messagesN + thinking + toolCalls;
    const system = Number(S.liveMeter && S.liveMeter.parts && S.liveMeter.parts.system) || 0;
    const schemas = Number(S.liveMeter && S.liveMeter.parts && S.liveMeter.parts.tools) || 0;
    return {
      system,
      history,
      messages: messagesN,
      thinking,
      tool_calls: toolCalls,
      tool_families: families,
      tools: schemas,
      draft: 0,
      total: system + history + schemas,
    };
  }

    function estimateLocalTokens() {
    return Math.max(0, localOccupancy().total);
  }

    function estimateDraftTokens() {
    if (S.streaming) return 0;
    let total = 0;
    const text = (S.input && S.input.value) || "";
    if (text) total += Math.max(1, Math.ceil(text.length / 4));
    for (const att of S.pendingAttachments) {
      if (String(att.kind || att.mime || "").startsWith("image")) {
        total += imageTokens();
      } else {
        const data = att.data || att.text || "";
        if (typeof data === "string" && data) total += Math.max(1, Math.ceil(data.length / 4));
      }
    }
    return total;
  }

    function occupancyRows(snap) {
    const parts = (snap && snap.parts && typeof snap.parts === "object") ? snap.parts : {};
    const draft = estimateDraftTokens();
    // Zero is authoritative too. Do not mix a compacted prompt breakdown
    // with the much larger, still-visible historical transcript.
    const source = snap?.parts ? parts : S.liveMeter ? {} : localOccupancy();
    const system = Number(source.system) || 0;
    const history = Number(source.history) || 0;
    const messagesN = Number(source.messages) || 0;
    const thinking = Number(source.thinking) || 0;
    const toolCalls = Number(source.tool_calls) || 0;
    const schemas = Number(source.tools) || 0;
    const families = source.tool_families || {};
    if (S.liveMeter && !snap?.parts) return [["Context", snap.used - draft, ""], ...(draft ? [["Draft", draft, ""]] : [])];
    const rows = partsRows({ system, history, messages: messagesN, thinking, tool_calls: toolCalls, tools: schemas, tool_families: families });
    if (draft) rows.push(["Draft", draft, ""]);
    return rows;
  }

    function contextSnapshot() {
    const limit = modelWindow();
    const fromLive = S.liveMeter && typeof S.liveMeter.used === "number";
    const draftTokens = estimateDraftTokens();
    const hasDraft = draftTokens > 0;
    // The server supplies measured single-request occupancy or a calibrated
    // estimate of retained context. Never use summed turn billing as context.
    const baseUsed = fromLive
      ? Math.max(0, Number(S.liveMeter.used) || 0)
      : (S.messages.length ? estimateLocalTokens() : 0);
    const used = baseUsed + draftTokens;
    const windowSize = fromLive && S.liveMeter.limit ? Number(S.liveMeter.limit) || limit : limit;
    const ratio = windowSize ? used / windowSize : 0;
    const warnRatio = Number(S.liveMeter && S.liveMeter.warn_ratio) || Number(S.compactCfg.warn_ratio) || 0.8;
    const autoRatio = Number(S.liveMeter && S.liveMeter.auto_ratio) || Number(S.compactCfg.auto_ratio) || 0.9;
    const warn = ratio >= warnRatio;
    const auto = ratio >= autoRatio;
    return {
      used,
      limit: windowSize,
      model: (S.liveMeter && S.liveMeter.model) || S.modelId,
      basis: S.liveMeter?.basis || "visible_transcript_estimate",
      scope: S.liveMeter?.scope || "transcript",
      refreshing: Boolean(contextRecountPending && S.liveMeter),
      provider_usage: S.liveMeter?.provider_usage || null,
      history_info: S.liveMeter?.history_info || null,
      estimated: hasDraft ? true : (fromLive ? Boolean(S.liveMeter.estimated) : true),
      ratio,
      warn,
      auto,
      warn_ratio: warnRatio,
      auto_ratio: autoRatio,
      auto_enabled: S.liveMeter && typeof S.liveMeter.auto_enabled === "boolean" ? S.liveMeter.auto_enabled : S.compactCfg.auto !== false,
      strategy: (S.liveMeter && S.liveMeter.strategy) || S.compactCfg.strategy,
      compacting: S.compacting || Boolean(S.liveMeter && S.liveMeter.compacting),
      parts: S.liveMeter && S.liveMeter.parts && typeof S.liveMeter.parts === "object" ? S.liveMeter.parts : null,
    };
  }

    // Prompt-cache warmth from the thread's newest request probe
  // (app/chat_generation/cache_watch.py). Warm while that request is inside
  // the provider's cache lifetime and the chat was being served from cache.
    function acceptCacheProbe(probe) {
    S.cacheProbe = probe && typeof probe === "object" && Number(probe.at) > 0 ? probe : null;
  }

    function warmCache() {
    const probe = S.cacheProbe;
    if (!probe || !probe.hit || Number(probe.prompt) < CACHE_MIN_TOKENS) return null;
    return Date.now() / 1000 - Number(probe.at) <= Number(probe.ttl) ? probe : null;
  }

    // Said once per run, and it stays until dismissed: a miss costs real
  // quota, so it should not slip past while the user reads the reply.
    function noteCacheMiss(event) {
    const id = String(event.run_id || "");
    if (S.cacheNoticed.has(id)) return;
    S.cacheNoticed.add(id);
    const prompt = Number(event.prompt) || 0;
    const uncached = Math.max(0, prompt - (Number(event.cached) || 0));
    const why = {
      account: " (another pool account served it)",
      settings: " (instructions, tools or effort changed)",
      history: " (earlier messages changed)",
      provider: " (the provider dropped its cache)",
    }[event.reason] || "";
    picker.pinHint("cache:" + id, `Cache miss · ${formatTokens(uncached)} of ${formatTokens(prompt)} input tokens uncached${why}`);
  }

    // The model switch warning: the new model has no cache for this chat.
    function warnModelCacheReset(nextId) {
    const warm = warmCache();
    if (!warm || warm.model === nextId) return;
    picker.setHint(`New model, cold cache · next reply sends ${formatTokens(warm.prompt)} tokens uncached`);
  }

    function acceptMeter(meter) {
    if (!meter || typeof meter !== "object") return;
    if (meter.cache) acceptCacheProbe(meter.cache);
    const scope = meter.scope === "transcript" ? "transcript" : "prompt";
    // Prompt-scope readings (live request during a turn) only apply while a
    // run is active. Transcript snapshots are authoritative at rest, so
    // reloads and confirmed edits/deletes outrank stale streaming estimates.
    // Mid-run they lag the in-flight turn by construction — durable rows
    // exclude in-turn tool results and streaming output — so they may only
    // seed a missing meter, never displace the live prompt reading.
    const runActive = Boolean(S.streaming || S.sending || traces.runIsActive(S.activeRun));
    if (scope === "prompt" && !runActive) return;
    if (scope === "prompt" && meter.run_id && traces.runIsActive(S.activeRun) && meter.run_id !== S.activeRun.id) return;
    const sameRun = !S.liveMeter?.run_id || !S.activeRun?.id || S.liveMeter.run_id === S.activeRun.id;
    if (scope === "transcript" && runActive && S.liveMeter?.scope === "prompt" && sameRun && !contextRecountPending) return;
    // Do not carry ownership, calibration or provenance from the previous run
    // into a new snapshot that intentionally omits those fields.
    S.liveMeter = { ...meter, scope };
    contextRecountPending = false;
    if (!("parts" in meter)) delete S.liveMeter.parts;
    if (!("basis" in meter)) delete S.liveMeter.basis;
    // Usage diagnostics belong to this reading, not the previous request.
    if (!("reported_request_tokens" in meter)) delete S.liveMeter.reported_request_tokens;
    if (!("provider_usage" in meter)) delete S.liveMeter.provider_usage;
    S.meterAnchor = { meter: { ...S.liveMeter }, local: localOccupancy() };
    syncContextMeter();
  }

    function applyMeterParts(parts, scope) {
    const limit = (S.liveMeter && S.liveMeter.limit) || modelWindow();
    const warnRatio = Number(S.liveMeter && S.liveMeter.warn_ratio) || Number(S.compactCfg.warn_ratio) || 0.8;
    const autoRatio = Number(S.liveMeter && S.liveMeter.auto_ratio) || Number(S.compactCfg.auto_ratio) || 0.9;
    const ratio = limit ? parts.total / limit : 0;
    S.liveMeter = {
      ...(S.liveMeter || {}),
      used: parts.total,
      limit,
      model: S.modelId,
      estimated: true,
      ratio: Math.round(ratio * 10000) / 10000,
      warn: ratio >= warnRatio,
      auto: ratio >= autoRatio,
      standing: scope === "transcript",
      scope,
      basis: "visible_transcript_estimate",
      provider_usage: null,
      parts,
    };
    syncContextMeter();
  }

    function recountLocalContext() {
    S.meterAnchor = null;
    if (["payload_chars_v1", "provider_calibrated", "provider_usage", "provider_prompt"].includes(S.liveMeter?.basis) && (!S.liveMeter.model || S.liveMeter.model === S.modelId)) {
      // The browser only has previews, not the retained provider transcript.
      // Keep the last measurement explicitly marked pending until the save's
      // authoritative snapshot arrives instead of inventing a much lower total.
      contextRecountPending = true;
      syncContextMeter();
      return;
    }
    contextRecountPending = false;
    const parts = localOccupancy();
    parts.total = parts.system + parts.history + parts.tools;
    applyMeterParts(parts, "transcript");
    // Transcript surgery invalidates diagnostics from the last request.
    if (S.liveMeter) delete S.liveMeter.reported_request_tokens;
    syncContextMeter();
  }

    function liveStreamEstimate() {
    // The backend measures the actual provider payload, including full tool
    // results. Visible reasoning and preview growth are not that payload.
    if (["payload_chars_v1", "provider_calibrated", "provider_usage", "provider_prompt"].includes(S.liveMeter?.basis)) { syncContextMeter(); return; }
    const local = localOccupancy();
    if (!S.liveMeter) {
      applyMeterParts(local, "prompt");
      S.meterAnchor = { meter: { ...S.liveMeter }, local };
      return;
    }
    if (!S.meterAnchor) S.meterAnchor = { meter: { ...S.liveMeter }, local };
    const base = S.meterAnchor.meter;
    // Estimate only new visible material since this reading. Full provider
    // tool results are absent from the UI; rebuilding their total from trace
    // previews caused alternating high/low readings every 400 ms.
    const delta = Object.fromEntries(["messages", "thinking", "tool_calls"].map(key =>
      [key, Math.max(0, local[key] - S.meterAnchor.local[key])]));
    const growth = delta.messages + delta.thinking + delta.tool_calls;
    const used = Math.max(0, Number(base.used) || 0) + growth;
    S.liveMeter = { ...base, used, scope: "prompt", estimated: growth > 0 || Boolean(base.estimated) };
    if (base.parts) {
      const parts = { ...base.parts };
      for (const key of Object.keys(delta)) parts[key] = (Number(parts[key]) || 0) + delta[key];
      parts.tool_families = { ...(base.parts.tool_families || {}) };
      for (const [family, value] of Object.entries(local.tool_families)) {
        const extra = Math.max(0, value - (S.meterAnchor.local.tool_families[family] || 0));
        if (extra) parts.tool_families[family] = (Number(parts.tool_families[family]) || 0) + extra;
      }
      parts.history = (Number(base.parts.history) || 0) + growth;
      parts.total = used;
      S.liveMeter.parts = parts;
    }
    syncContextMeter();
  }

    function closeContextPop() {
    if (S.contextPop) S.contextPop.hidden = true;
    if (S.contextBtn) S.contextBtn.setAttribute("aria-expanded", "false");
  }

    function hideContextAlert() {
    if (S.contextAlert) S.contextAlert.hidden = true;
  }

    function showContextAlert(text) {
    if (!S.contextAlert || !S.contextAlertText) return;
    S.contextAlertText.textContent = text;
    S.contextAlert.hidden = false;
  }

    function maybeContextWarn(snap) {
    if (!S.messages.length) {
      hideContextAlert();
      return;
    }
    if (!snap.warn) {
      S.contextWarned = false;
      S.contextAlertHidden = false;
      hideContextAlert();
      return;
    }
    if (S.contextAlertHidden) return;
    const pct = Math.max(1, Math.round(snap.ratio * 100));
    const autoPct = Math.round(snap.auto_ratio * 100);
    if (snap.auto && snap.auto_enabled) {
      showContextAlert(`Context is ${pct}% full. Auto-compact runs at ${autoPct}%.`);
      S.contextWarned = true;
      return;
    }
    if (!S.contextWarned) {
      S.contextWarned = true;
      showContextAlert(`Context is ${pct}% full. Compaction starts at ${autoPct}%.`);
    }
  }

    function syncContextMeter() {
    const active = S.messages.length > 0;
    if (S.contextWrap) S.contextWrap.hidden = !active;
    if (!active) {
      closeContextPop();
      hideContextAlert();
      return;
    }
    const snap = contextSnapshot();
    const ratio = Math.max(0, Math.min(1, snap.ratio || 0));
    const usedLabel = formatTokens(snap.used);
    const limitLabel = formatTokens(snap.limit);
    const pct = Math.round(ratio * 100);
    const title = `${usedLabel} / ${limitLabel} tokens (${pct}%)${snap.refreshing ? " · updating" : ""}`;
    if (S.contextFill) {
      const offset = S.CONTEXT_RING * (1 - ratio);
      S.contextFill.style.strokeDasharray = String(S.CONTEXT_RING);
      S.contextFill.style.strokeDashoffset = String(offset);
    }
    if (S.contextBtn) {
      S.contextBtn.classList.toggle("is-warn", Boolean(snap.warn) && !snap.auto);
      S.contextBtn.classList.toggle("is-auto", Boolean(snap.auto));
      S.contextBtn.classList.toggle("is-busy", Boolean(snap.compacting || S.compactingRequest));
      S.contextBtn.setAttribute("aria-label", "Context usage " + title);
      S.contextBtn.title = title;
    }
    if (S.contextPop && !S.contextPop.hidden) {
      fillContextCard(S.contextPop, {
        used: snap.used, limit: snap.limit, ratio, rows: occupancyRows(snap), warn: snap.warn, auto: snap.auto,
        autoEnabled: snap.auto_enabled, autoRatio: snap.auto_ratio, compact: true,
      });
    }
    maybeContextWarn(snap);
  }

    async function runCompact(instructions) {
    if (S.compactingRequest) return;
    if (!S.currentId || !S.messages.length) {
      picker.setHint("Nothing to compact yet.");
      return;
    }
    if (traces.runIsActive(S.activeRun) || S.sending) {
      picker.setHint("Wait for this reply to finish, then compact.", true);
      return;
    }
    if (!net.requireLive()) return;
    S.compactingRequest = true;
    syncContextMeter();
    closeContextPop();
    picker.holdHint("compact", "Compacting context · summarizing " + formatTokens(contextSnapshot().used) + " tokens, this can take a few minutes…");
    try {
      const data = await net.api("/chat/threads/" + S.currentId + "/compact", {
        method: "POST",
        body: JSON.stringify({
          expected_revision: S.serverRevision,
          command_id: newSession(),
          instructions: instructions || "",
        }),
      });
      if (data && data.thread) sync.acceptThread(data.thread);
      if (data && data.thread && data.thread.compaction && data.thread.compaction.meter) {
        acceptMeter(data.thread.compaction.meter);
      } else {
        syncContextMeter();
      }
      if (data && data.compacted === false) {
        picker.releaseHint("compact", "Not enough history to compact yet.");
      } else {
        const meter = (data && data.thread && data.thread.compaction && data.thread.compaction.meter) || contextSnapshot();
        picker.releaseHint("compact", "Context compacted · " + formatTokens(meter.used) + " / " + formatTokens(meter.limit) + " tokens.");
        S.contextWarned = false;
        S.contextAlertHidden = false;
        hideContextAlert();
      }
    } catch (error) {
      sync.showFailure("Could not compact this chat", error);
    } finally {
      S.compactingRequest = false;
      syncContextMeter();
    }
  }

    function queueContextMeter() {
    // localOccupancy is O(transcript); recount at most 300ms after typing
    // stops instead of on every keystroke of a long conversation.
    window.clearTimeout(_meterDebounce);
    _meterDebounce = window.setTimeout(() => { _meterDebounce = 0; syncContextMeter(); }, 300);
  }

  return {
    acceptCacheProbe,
    acceptMeter,
    noteCacheMiss,
    warmCache,
    warnModelCacheReset,
    closeContextPop,
    contextSnapshot,
    currentModel,
    imageTokens,
    hideContextAlert,
    liveStreamEstimate,
    modelWindow,
    occupancyRows,
    queueContextMeter,
    recountLocalContext,
    runCompact,
    syncContextMeter,
    warnTextOnlyModel,
    estimateChars,
    estimateTrace,
    fillContextCard,
    partsRows,
  };
}

export { estimateChars, estimateTrace, fillContextCard, partsRows };
