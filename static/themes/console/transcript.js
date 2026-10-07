// transcript presentation. Dependencies are live getters supplied by index.js.
export function create(deps) {
function activityEntriesHtml(entries, msg, live, blockKey, blockLive = false) {
    return deps.consoleActivityEntriesHtml(entries, msg, live, blockLive);

  }

function activityBlockHtml(entries, msg, live, blockIndex, showElapsed, turnLive = live) {
    const key = String(blockIndex);
    const aid = "chat-activity-" + ++deps.activitySeq;
    const traces = entries.filter((entry) => entry.type === "tool").map((entry) => entry.trace);
    const reasoning = entries.filter((entry) => entry.type === "reasoning").map((entry) => entry.text).join("");
    const groups = deps.groupTraces(traces);
    const waiting = live && !entries.length;
    if (waiting) return "";
    const blockMsg = { ...msg, reasoning, content: "" };
    const kind = deps.activityKind(blockMsg, groups, live);
    const defaultOpen = Boolean(live || true);
    msg.activityOpenMap ||= {};
    if (!(key in msg.activityOpenMap)) msg.activityOpenMap[key] = defaultOpen;
    const open = msg.activityOpenMap?.[key] ?? defaultOpen;
    const steps = activityEntriesHtml(entries, msg, turnLive || Boolean(msg.tools_live), key, live);
    const classes = ["chat-activity"];
    if (open || true) classes.push("is-open");
    // A model wait/background handoff must not hide already-rendered tools.
    if (kind) classes.push(kind === "waiting" ? "is-pending-phase" : `is-${kind}`);
    classes.push("is-bare");
    if (!live && msg.stopped) classes.push("is-stopped");
    if (!live && msg.error) classes.push("is-failed");
    const label = deps.escapeHtml(deps.activityLabel(blockMsg, groups, live));
    const elapsed = showElapsed && !live ? '<span data-activity-elapsed></span>' : "";
    const summary = live
      ? ""
      : (`<p class="chat-cli-run" data-activity-summary aria-hidden="true"><span class="chat-activity-text"><span>${label}</span>${elapsed}</span></p>`);
    return (
      `<section class="${classes.join(" ")}" data-activity data-activity-block="${key}">` +
      summary +
      `<div class="chat-activity-panel" id="${aid}" role="region" aria-label="Activity details"><div class="chat-activity-panel-inner"><ol class="chat-timeline">${steps}</ol></div></div>` +
      "</section>"
    );
  }

function visibleAssistantError(msg) {
    if (!msg.error) return "";
    const index = deps.messages.findIndex(item => item === msg || (msg.id && item.id === msg.id));
    if (index >= 0 && deps.messages.slice(index + 1).some(item => item.role === "user")) return "";
    return String(msg.error).replace(/\s+/g, " ").trim();
  }

function updateAssistant(article, msg, live) {
    // Off-screen historical responses are hydrated from the latest message
    // object when they enter the viewport, rather than on every sync event.
    if (!article || article.hasAttribute("data-deferred-message")) return;
    if (deps.log.contains(article) && article.dataset.messageId && article.dataset.messageId !== msg.id) { deps.pendingFullPaint = true; return; }
    article._rebind?.(msg);
    if (deps.toolMotions.has(article)) { deps.motionPaints.set(article, { msg, live }); return; }
    if (deps.toolGestureOwner === article && article.isConnected) {
      deps.pendingToolPaint = { article, msg, live };
      return;
    }
    const flow = article.querySelector("[data-flow]");
    const err = article.querySelector("[data-err]");
    const foot = article.querySelector("[data-foot]");
    const meta = article.querySelector("[data-meta]");
    const traces = Array.isArray(msg.tools) ? msg.tools : [];
    const hasMarker = Array.isArray(msg.segments) && msg.segments.some((part) => part && part.type === "marker");
    const errText = visibleAssistantError(msg);
    const hasOutput = Boolean((msg.content || "").trim() || (msg.reasoning || "").trim() || errText || traces.length || msg.permissions?.length || hasMarker);
    article.hidden = !hasOutput;
    const sourceMap = new Map();
    traces.forEach((trace) => {
      const rows = trace.results || (trace.name === "read_url" && trace.status === "done" && trace.url ? [{ url: trace.url, status: "read" }] : []);
      rows.filter((row) => /^https?:/i.test(deps.safeUrl(row.url))).forEach((row) => {
        const prior = sourceMap.get(row.url) || {};
        sourceMap.set(row.url, { ...prior, ...row, status: prior.status === "read" || row.status === "read" || trace.name === "read_url" ? "read" : "found" });
      });
    });
    const content = String(msg.content || "");
    const sources = [...sourceMap.values()].map((row) => ({ ...row, cited: content.includes(row.url) }));
    const citations = article.querySelector("[data-citations]");
    const inActivity = Boolean(traces.length);
    citations.hidden = inActivity || !sources.length || !msg.content;
    const sourceGrid = article.querySelector("[data-source-grid]");
    const sourceSignature = JSON.stringify(sources);
    if (sourceGrid._src !== sourceSignature) {
      sourceGrid._src = sourceSignature;
      sourceGrid.innerHTML = deps.sourceList(sources);
      article.querySelector("[data-source-count]").textContent = `${sources.length} ${sources.length === 1 ? "source" : "sources"}`;
    }
    const catalog = sources.map((row) => row.url);
    const segments = deps.assistantSegments(msg);
    const toolById = new Map(traces.map((trace, index) => [trace.id, { trace, index }]));
    const permById = new Map((msg.permissions || []).map((perm) => [perm.id, perm]));
    const settleLines = deps.todoSettleLines(msg);
    const normalized = segments.flatMap((part, segmentIndex) => {
      if (!part || !part.type) return [];
      if (part.type === "marker") {
        return [{ type: "marker", segmentIndex, text: part.text || "", kind: part.kind || "compaction", summary: part.summary || "" }];
      }
      if (part.type === "tool") {
        const found = toolById.get(part.id);
        if (!found) return [];
        // A todo list is not a row: it lives in the section under the status
        // line. Only the call that finishes the list leaves a line behind.
        if (found.trace.name === "todo_write") {
          const text = settleLines.get(found.trace.id);
          return text ? [{ type: "todo-line", segmentIndex, text }] : [];
        }
        return [{ type: "tool", segmentIndex, ...found }];
      }
      if (part.type === "permission") {
        const found = permById.get(part.id);
        if (!found || found.kind === "tool_permission") return [];
        return [{ type: "permission", segmentIndex, perm: found }];
      }
      // Thinking hidden (Show thinking, /thinking): no view prints thoughts.
      if (part.type === "reasoning" && !deps.showThinking) return [];
      if (part.type === "reasoning" && live && segmentIndex === segments.length - 1) return [{ type: part.type, text: part.text || "", segmentIndex }];
      return typeof part.text === "string" && part.text ? [{ type: part.type, text: part.text, segmentIndex }] : [];
    });
    const blocks = [];
    normalized.forEach((part) => {
      if (part.type === "permission") {
        blocks.push({ kind: "permission", entries: [part] });
        return;
      }
      if (part.type === "marker") {
        blocks.push({ kind: "marker", text: part.text, summary: part.summary || "" });
        return;
      }
      if (part.type === "todo-line") {
        blocks.push({ kind: "todos", text: part.text });
        return;
      }
      const kind = part.type === "content" ? "content" : "activity";
      const last = blocks[blocks.length - 1];
      if (last && last.kind === kind) last.entries.push(part);
      else blocks.push({ kind, entries: [part] });
    });
    const lastActivity = blocks.map((block) => block.kind).lastIndexOf("activity");
    const liveGroups = deps.groupTraces(traces);
    const liveKind = deps.activityKind(msg, liveGroups, live);
    // Structure only: types, ids and immutable marker text. Including segment
    // text here would rebuild the whole message DOM on every streamed delta
    // (the shape-change path innerHTML-renders every block); text growth is
    // handled per block by the settled/tail markdown streamer below.
    const structure = segments.map((part) => part?.type === "marker"
      ? `marker:${part.text || ""}:${part.summary || ""}`
      : `${part?.type || ""}:${part.id || ""}`).join("|");
    const permSig = (msg.permissions || []).map((p) => `${p.id}:${p.status}`).join("|");
    const traceById = new Map(traces.map((trace) => [trace && trace.id, trace]));
    // Console prints a thought only once it lands, and its live summary line
    // reads the run's phase; streamed thinking text alone repaints nothing.
    const consoleLive = (live);
    const phase = consoleLive ? deps.runPhase(msg) : "";
    const streamingReason = consoleLive && phase !== "calling" && segments.length && segments[segments.length - 1]?.type === "reasoning" ? segments.length - 1 : -1;
    const blockSig = (block, blockIndex) => {
      if (block.kind === "activity") {
        return block.entries.map((entry) => {
          if (entry.type !== "tool") return `r:${entry.segmentIndex}:${entry.segmentIndex === streamingReason ? "" : entry.text || ""}`;
          const trace = entry.trace;
          const running = Boolean(trace && deps.toolPresentation(trace, live).running);
          return `t:${trace && trace.id}:${deps.tracesSig([trace])}:${running}`;
        }).join("~") + (consoleLive && blockIndex === blocks.length - 1 ? `~ph:${phase}` : "");
      }
      if (block.kind === "marker") return `m:${block.text}:${block.summary || ""}`;
      if (block.kind === "todos") return `o:${block.text}`;
      if (block.kind === "permission") return `p:${block.entries[0] && block.entries[0].perm && block.entries[0].perm.id}`;
      return `c:${blockIndex}`;
    };
    // showThinking changes which blocks exist without changing the segment
    // structure, so it belongs to the shape: a toggle must reach the rebuild.
    const sig = `${Boolean(live)}|${"console"}|${deps.showThinking}|${liveKind}|${structure}|${permSig}|${[...settleLines.keys()].join(",")}`;
    let reasoningPositions = null;
    const sameShape = flow._sig === sig && flow.childElementCount === blocks.length;
    if (sameShape) {
      // One running tool must not remount the settled rows beside it. Replacing
      // a row that is already open replays its expand animation.
      blocks.forEach((block, blockIndex) => {
        const next = blockSig(block, blockIndex);
        const node = flow.children[blockIndex];
        if (!node || node._blockSig === next || block.kind === "content") return;
        if (block.kind !== "activity") {
          const html = block.kind === "permission"
            ? deps.permissionCardHtml(block.entries[0].perm, msg)
            : block.kind === "todos" ? deps.todoLineHtml(block.text) : deps.compactionMarkHtml(block.text, block.summary);
          if (node.outerHTML === html) { node._blockSig = next; return; }
          const fresh = document.createElement("template");
          fresh.innerHTML = html;
          const replacement = fresh.content.firstElementChild;
          if (!replacement) return;
          replacement._blockSig = next;
          node.replaceWith(replacement);
          return;
        }
        const html = activityBlockHtml(block.entries, msg, live && blockIndex === blocks.length - 1, blockIndex, !live && blockIndex === lastActivity, live);
        const fresh = document.createElement("template");
        fresh.innerHTML = html;
        const incoming = fresh.content.firstElementChild;
        if (!incoming) return;
        FuseChatDOM.patch(node, incoming, deps.transcriptDomOptions);
        node._blockSig = next;
      });
    } else if (flow._sig !== sig) {
      reasoningPositions = new Map([...flow.querySelectorAll("[data-reason-preview]")].map(el =>
        [el.dataset.reasonPreview, deps.thinkingPosition(el)]));
      const focused = flow.contains(document.activeElement) ? document.activeElement : null;
      const focusedStep = focused?.closest("[data-step]")?.dataset.step;
      const focusedControl = focused?.matches(".chat-step-caret") ? ".chat-step-caret" : focused?.matches(".chat-step-toggle") ? ".chat-step-toggle" : focused?.matches(".chat-think-size") ? ".chat-think-size" : focused?.matches(".chat-step-open") ? ".chat-step-open" : "";
      const focusedThinking = focused?.matches("[data-reason-preview]") ? focused.dataset.reasonPreview : null;
      const focusedSection = focused?.matches("[data-copy-tool]") ? focused.closest("[data-tool-section]")?.dataset.toolSection : "";
      const preKey = (el, i) => el.closest("[data-tool-section]") ? `${el.closest("[data-step]")?.dataset.step}:${el.closest("[data-tool-section]").dataset.toolSection}` : `pre-${i}`;
      const positions = new Map([...flow.querySelectorAll("pre")].map((el, i) => [preKey(el, i), [el.scrollLeft, el.scrollTop]]));



      flow._sig = sig;
      const nextFlow = blocks.map((block, blockIndex) => {
        if (block.kind === "permission") {
          return deps.permissionCardHtml(block.entries[0].perm, msg);
        }
        if (block.kind === "marker") {
          return deps.compactionMarkHtml(block.text, block.summary);
        }
        if (block.kind === "todos") return deps.todoLineHtml(block.text);
        if (block.kind === "content") {
          const text = block.entries.map((entry) => entry.text).join("");
          const writing = live && blockIndex === blocks.length - 1;
          return `<div class="chat-md${writing ? " is-writing" : ""}" data-content-block="${blockIndex}"></div>`;
        }
        const blockLive = live && blockIndex === blocks.length - 1;
         return activityBlockHtml(block.entries, msg, blockLive, blockIndex, !live && blockIndex === lastActivity, live);
       }).join("");
      FuseChatDOM.reconcile(flow, nextFlow, deps.transcriptDomOptions);
      [...flow.children].forEach((node, blockIndex) => { node._blockSig = blockSig(blocks[blockIndex], blockIndex); });
      flow.querySelectorAll("pre").forEach((el, i) => {
        const position = positions.get(preKey(el, i));
        if (position) { el.scrollLeft = position[0]; el.scrollTop = position[1]; }
      });
      deps.hydratePermissionCards(flow);
      if (focusedStep && focusedControl) {
        flow.querySelector(`[data-step="${CSS.escape(focusedStep)}"] > .chat-step-main > .chat-step-head > ${focusedControl}`)?.focus({ preventScroll: true });
      }
      if (focusedStep && focusedSection) flow.querySelector(`[data-step="${CSS.escape(focusedStep)}"] [data-tool-section="${CSS.escape(focusedSection)}"] [data-copy-tool]`)?.focus({ preventScroll: true });
      if (focusedThinking != null) flow.querySelector(`[data-reason-preview="${CSS.escape(focusedThinking)}"]`)?.focus({ preventScroll: true });
    }
    if (live) deps.syncLiveDock();
    normalized.filter((part) => part.type === "reasoning").forEach((part) => {
      const peek = flow.querySelector(`[data-reason-preview="${part.segmentIndex}"]`);
      const li = flow.querySelector(`[data-step="think-${part.segmentIndex}"]`);
      if (!li || !peek) return;
      const later = normalized.some((entry) => entry.segmentIndex > part.segmentIndex);
      const liveThink = Boolean(live && !later);
      let source = String(part.text || "").trim();
      if ((li.dataset.reasonIds)) {
        const ids = new Set(li.dataset.reasonIds.split(","));
        source = normalized
          .filter((entry) => entry.type === "reasoning" && ids.has(String(entry.segmentIndex)))
          .map((entry) => String(entry.text || "").trim())
          .filter(Boolean)
          .join("\n\n");
      }
      // A settled preview whose text is unchanged kept its node and scroll
      // offset through the patch. Reading its position anyway forced a
      // layout per message whenever a snapshot repainted a long transcript.
      const unchanged = !reasoningPositions && peek._thinking && !liveThink && peek._source === source && peek._live === liveThink;
      const saved = unchanged ? null : reasoningPositions?.get(String(part.segmentIndex)) || (peek._thinking ? deps.thinkingPosition(peek) : null);
      deps.initThinking(peek);
      const wasMode = li.dataset.thinkMode;
      const settling = li.classList.contains("is-live-think") && !liveThink;
      li.classList.toggle("is-live-think", liveThink);
      deps.syncThinkingMode(li, msg, settling && Boolean(wasMode) && !deps.reduceMotion.matches);
      if (unchanged) return;
      if (peek._source !== source || peek._live !== liveThink) {
        peek._source = source; peek._live = liveThink;
        deps.streamMarkdown(peek.firstElementChild, source, liveThink, catalog);
        peek.classList.toggle("is-clipped", liveThink && peek.scrollHeight > peek.clientHeight + 1);
        ;
      }
      if (reasoningPositions?.has(String(part.segmentIndex)) && saved) {
        peek.scrollTop = saved.top;
        peek._thinking.lastTop = peek.scrollTop;
        peek._thinking.initialized = saved.initialized;
      }
      if (saved && !saved.pinned) {
        deps.stopThinkingFollow(peek);
        peek.scrollTop = saved.top;
      } else if (liveThink || saved?.pinned && saved?.initialized) {
        peek._thinking.follow = true;
        deps.followThinking(peek, !saved?.initialized);
      } else if (!liveThink) peek._thinking.follow = false;
    });
    blocks.forEach((block, blockIndex) => {
      if (block.kind !== "content") return;
      const md = flow.querySelector(`[data-content-block="${blockIndex}"]`);
      if (!md) return;
      const text = block.entries.map((entry) => entry.text).join("");
      const writing = live && blockIndex === blocks.length - 1;
      const srcKey = text + "\0" + catalog.join("\n");
      md.classList.toggle("is-writing", writing);
      if (md._src === srcKey) return;
      const positions = [...md.querySelectorAll("pre")].map((el) => [el.scrollLeft, el.scrollTop]);
      deps.paintContentBlock(md, text, writing, catalog, srcKey);
      md.querySelectorAll("pre").forEach((el, i) => {
        if (positions[i]) { el.scrollLeft = positions[i][0]; el.scrollTop = positions[i][1]; }
      });
    });
    deps.syncActivityElapsed(article, msg, live);
    deps.syncActivitySpin(article, msg, live);
    if (live) deps.startLiveClock();
    const toolFailKeys = traces
      .filter((t) => deps.toolFailed(t))
      .map((t) => deps.normErr(t.error || "failed"));
    const errDup = Boolean(errText && toolFailKeys.includes(deps.normErr(errText)));
    if (errText && !errDup) {
      err.hidden = false;
      err.textContent = errText;
    } else {
      err.hidden = true;
      err.textContent = "";
    }
    const usageBits = deps.formatTurnMeta(msg);
    const turnLine = (!live) && hasOutput ? deps.ccTurnLineHtml(msg, msg === deps.messages[deps.messages.length - 1]) : "";
    if (turnLine) {
      foot.hidden = false;
      meta.innerHTML = `<span class="chat-console-turn">${turnLine}</span>`;
    } else if (!live && hasOutput) {
      foot.hidden = false;
      meta.innerHTML = usageBits
        .map((bit) => `<span title="${deps.escapeHtml(bit.title)}">${deps.escapeHtml(bit.text)}</span>`)
        .join('<span class="chat-foot-dot" aria-hidden="true">·</span>');
    } else {
      foot.hidden = true;
    }
  }

  return { activityEntriesHtml, activityBlockHtml, visibleAssistantError, updateAssistant };
}
