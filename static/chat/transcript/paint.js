// Message hydration, paint scheduling, recaps.
import { S } from '../state.js';
import { messageRunLive } from '../core.js';

let dockTimer = 0;

function whenIdle(fn) {
  if (typeof window.requestIdleCallback === "function") window.requestIdleCallback(fn, { timeout: 2000 });
  else window.setTimeout(fn, 200);
}

// Disclosure is device-local UI state, never part of the server transcript.
// Retain a bounded set of boolean maps for reloads and thread navigation.
const DISCLOSURE_STORE = "fuse.chat.disclosures.v1";

function assistantSegments(msg) {
  if (Array.isArray(msg.segments)) return msg.segments;
  const legacy = [];
  if (msg.reasoning) legacy.push({ type: "reasoning", text: msg.reasoning });
  (msg.tools || []).forEach((trace) => {
    if (trace.id) legacy.push({ type: "tool", id: trace.id });
  });
  (msg.permissions || []).forEach((perm) => {
    if (perm.id && perm.kind !== "tool_permission") legacy.push({ type: "permission", id: perm.id });
  });
  if (msg.content) legacy.push({ type: "content", text: msg.content });
  return legacy;
}

// A reply that arrives in the open thread settles in (see .is-arriving).
function markArriving(article) {
  article.classList.add("is-arriving");
  const settle = (event) => {
    if (event.target !== article) return;
    article.classList.remove("is-arriving");
    article.removeEventListener("animationend", settle);
  };
  article.addEventListener("animationend", settle);
}

// Frame-coalesced repaint entry points. Sync events can arrive dozens of
// times per second across many subagents; state updates stay eager and
// cheap, while the DOM repaint runs at most once per animation frame.
let _paintLastQueued = false;

export function createPaint(ctx) {
  const { agents, artifacts, composer, context, follow, sidebar, traces, turns, ui } = ctx;
    function playTranscriptFx() {
    S.root.classList.remove("is-docking");
    S.root.classList.add("is-entering");
    if (dockTimer) window.clearTimeout(dockTimer);
    dockTimer = window.setTimeout(() => {
      dockTimer = 0;
      S.root.classList.remove("is-entering");
    }, 480);
  }

    function setActive(on) {
    const wasEmpty = S.root.classList.contains("is-empty");
    S.root.classList.toggle("is-active", on);
    S.root.classList.toggle("is-empty", !on);
    if (on && wasEmpty) composer.playDockTransition();
    if (!on && dockTimer) {
      window.clearTimeout(dockTimer);
      dockTimer = 0;
      S.root.classList.remove("is-docking");
      S.root.classList.remove("is-entering");
    }
    if (on) S.root.classList.remove("is-composing");
    S.log.hidden = !on;
    sidebar.syncTitle();
    composer.syncEmptyComposer();
    context.syncContextMeter();
  }

    // While only the newest messages of a streamed snapshot are loaded, the
  // transcript is display-only: a save would replace the whole thread with
  // the tail. acceptThread clears it when the complete snapshot lands.
  function setPartialTranscript(on) {
    S.partialTranscript = on;
    S.root.toggleAttribute("data-transcript-partial", on);
  }

    function disclosureKey(msg) {
    return S.currentId && msg.id ? `${S.currentId}:${msg.id}` : "";
  }

    function saveDisclosures(msg) {
    const key = disclosureKey(msg);
    if (!key) return;
    try {
      const saved = JSON.parse(sessionStorage.getItem(DISCLOSURE_STORE) || "{}");
      delete saved[key];
      saved[key] = { stepOpen: msg.stepOpen || {}, activityOpenMap: msg.activityOpenMap || {} };
      sessionStorage.setItem(DISCLOSURE_STORE, JSON.stringify(Object.fromEntries(Object.entries(saved).slice(-100))));
    } catch (_) {}
  }

    function restoreDisclosures(msg) {
    const key = disclosureKey(msg);
    if (!key) return;
    try {
      const saved = JSON.parse(sessionStorage.getItem(DISCLOSURE_STORE) || "{}")[key];
      for (const field of ["stepOpen", "activityOpenMap"]) {
        if (!msg[field] && saved?.[field] && typeof saved[field] === "object") {
          msg[field] = Object.fromEntries(Object.entries(saved[field]).filter(([, value]) => typeof value === "boolean"));
        }
      }
    } catch (_) {}
  }

    function runPhase(msg) {
    return (msg?.id === S.activeRun?.message_id ? S.activeRun?.activity : msg?._activity)?.phase || "";
  }

    const transcriptDomOptions = {
    island: node => node.hasAttribute("data-reason-preview") || node.hasAttribute("data-content-block") || node.dataset.step?.startsWith("think-"),
    dispose: node => { if (node.nodeType === 1) ui.disposeThinking(node); },
  };

    const historyObserver = typeof IntersectionObserver === "function" ? new IntersectionObserver(entries => {
    for (const entry of entries) if (entry.isIntersecting) hydrateHistoricalMessage(entry.target);
  }, { root: S.thread, rootMargin: "600px 0px" }) : null;

    function hydrateHistoricalMessage(shell) {
    if (!shell.isConnected || !shell.hasAttribute("data-deferred-message")) { historyObserver?.unobserve(shell); return; }
    const shown = viewMessages();
    const index = shown.findIndex(msg => msg.id === shell.dataset.messageId);
    if (index < 0) return;
    historyObserver?.unobserve(shell);
    const viewport = follow.captureThreadViewport();
    const previousHeight = shell.offsetHeight;
    const article = ui.renderAssistant(shown[index], false);
    article.dataset.messageId = shown[index].id;
    shell.replaceWith(article);
    paintRecaps(article);
    if (viewport.following && S.stickToBottom) {
      // Loading older history is not new model output. Compensate its height
      // immediately instead of animating through pages of newly mounted text.
      S.thread.scrollTop = viewport.top + article.offsetHeight - previousHeight;
      S.lastThreadScrollTop = S.thread.scrollTop;
      follow.scrollThread();
    } else follow.restoreThreadViewport(viewport);
  }

    function renderTranscriptMessage(msg, index, live) {
    // Keep stable, indexed shells so editing/rewinding and run updates still
    // address the complete server-owned transcript. Expensive markdown, tool
    // trees and syntax highlighting for old answers are created on demand.
    if (msg.role === "marker" || msg.kind === "compaction" || msg.compaction) return agents.agentSeedArticle(msg, index);
    if (historyObserver && msg.role === "assistant" && !live && index < viewMessages().length - 8 &&
        (msg.content || msg.reasoning || msg.tools?.length || msg.permissions?.length || ui.visibleAssistantError(msg))) {
      const shell = document.createElement("article");
      shell.className = "chat-turn is-assistant chat-deferred-message";
      shell.dataset.messageId = msg.id;
      shell.setAttribute("data-deferred-message", "");
      const button = document.createElement("button");
      button.type = "button";
      button.className = "btn ghost sm";
      button.textContent = "Load earlier response";
      button.addEventListener("click", () => hydrateHistoricalMessage(shell));
      shell.append(button);
      historyObserver.observe(shell);
      return shell;
    }
    return msg.role === "user" ? ui.renderUser(msg, index, live) : ui.renderAssistant(msg, live);
  }

    function viewMessages() {
    if (S.editingIndex >= 0 || !S.contextMessages.length) return S.messages;
    if (traces.runIsActive(S.activeRun) && !S.contextLive) return S.messages;
    const live = S.messages.find((msg) => msg.role === "assistant" && messageRunLive(msg));
    if (!live || S.contextMessages.some((msg) => msg.id === live.id)) return S.contextMessages;
    return S.contextMessages.concat([live]);
  }

    function visibleRecaps(data) {
    const dismissed = S.dismissedRecapAnchors.get(data.id);
    return (Array.isArray(data.recaps) ? data.recaps : []).filter(row => !dismissed?.has(String(row.anchor_id)));
  }

    function dismissRecaps(remember = true) {
    if (remember && S.currentId && S.threadRecaps.length) {
      const dismissed = S.dismissedRecapAnchors.get(S.currentId) || new Set();
      S.threadRecaps.forEach(row => dismissed.add(String(row.anchor_id)));
      S.dismissedRecapAnchors.set(S.currentId, dismissed);
    }
    S.threadRecaps = [];
    S.log.querySelectorAll(".chat-recap").forEach(node => node.remove());
  }

    function paintRecaps(target = null) {
    const byAnchor = new Map(S.threadRecaps.map(row => [String(row.anchor_id), String(row.text || "")]));
    for (const article of target ? [target] : S.log.children) {
      const text = byAnchor.get(article.dataset.messageId);
      let node = article.querySelector(":scope > .chat-recap");
      if (!text || article.hasAttribute("data-deferred-message")) { node?.remove(); continue; }
      if (!node) {
        node = document.createElement("p");
        node.className = "chat-recap";
        node.setAttribute("aria-label", "Work recap");
        article.appendChild(node);
      }
      if (node.dataset.text !== text) {
        const icon = document.createElement("span");
        icon.className = "chat-recap-icon";
        icon.setAttribute("aria-hidden", "true");
        icon.innerHTML = '<svg viewBox="0 0 16 16" width="16" height="16" focusable="false" aria-hidden="true"><rect x="3" y="2" width="10" height="12" rx="1.5" fill="none" stroke="currentColor" stroke-width="1.25"/><path d="M5.5 5.5h5M5.5 8h5M5.5 10.5h3" fill="none" stroke="currentColor" stroke-width="1.25" stroke-linecap="round"/></svg>';
        const copy = document.createElement("span");
        copy.className = "chat-recap-copy";
        const label = Object.assign(document.createElement("span"), { className: "chat-recap-label", textContent: "Recap: " });
        const summary = Object.assign(document.createElement("em"), { className: "chat-recap-text", textContent: text });
        copy.append(label, summary);
        node.replaceChildren(icon, copy);
        node.dataset.text = text;
      }
    }
  }

    function paint() {
    if (S.toolGestureOwner && S.log.contains(S.toolGestureOwner)) { S.pendingFullPaint = true; return; }
    traces.foldAutoCommandResults(S.messages);
    traces.reconcileBgTraces([...S.completedJobs.values()], false);
    const viewport = follow.captureThreadViewport();
    const existing = new Map(S.log._threadId === S.currentId ? [...S.log.children].map(el => [el.dataset.messageId, el]) : []);
    const keep = new Set();
    let cursor = S.log.firstElementChild;
    viewMessages().forEach((msg, index) => {
      const live = msg.role === "assistant" && messageRunLive(msg);
      const signature = msg.role === "user" ? JSON.stringify([index, "console", live, S.editingIndex === index, msg.content, msg.attachments, msg.auto, msg.autoSteps, msg.noActions]) : "";
      let article = existing.get(msg.id);
      const reusable = article && article.classList.contains(msg.role === "assistant" ? "is-assistant" : "is-user") &&
        (msg.role === "assistant" || article._userSignature === signature) && !(live && article.hasAttribute("data-deferred-message"));
      if (!reusable) {
        const old = article;
        article = renderTranscriptMessage(msg, index, live);
        if (!old && !cursor && msg.role === "assistant" && S.log._threadId === S.currentId && S.log.childElementCount) markArriving(article);
        if (old) { ui.disposeThinking(old); historyObserver?.unobserve(old); old.replaceWith(article); if (cursor === old) cursor = article; }
      } else if (msg.role === "assistant") {
        for (const key of ["stepOpen", "activityOpenMap"]) if (!msg[key] && article._message?.[key]) msg[key] = article._message[key];
      }
      article.dataset.messageId = msg.id || "";
      article._userSignature = signature;
      if (article !== cursor) S.log.insertBefore(article, cursor);
      cursor = article.nextElementSibling;
      keep.add(article);
      if (reusable && msg.role === "assistant") ui.updateAssistant(article, msg, live);
      if (msg.role === "assistant" && !live && !msg.noActions) {
        ui.mountActions(article, index);
      }
    });
    for (const article of [...S.log.children]) if (!keep.has(article)) { ui.disposeThinking(article); historyObserver?.unobserve(article); article.remove(); }
    S.log._threadId = S.currentId;
    paintRecaps();
    turns.syncEmptyReplyRetry();
    setActive(S.messages.length > 0);
    artifacts.rememberLatestArtifact();
    composer.syncComposer();
    context.syncContextMeter();
    ui.syncLiveDock();
    follow.restoreThreadViewport(viewport);
    if (viewport.threadId !== S.currentId) follow.scrollThread();
  }

    function schedulePaintLast() {
    if (_paintLastQueued) return;
    _paintLastQueued = true;
    requestAnimationFrame(() => {
      _paintLastQueued = false;
      paintLast();
    });
  }

    function paintLast() {
    const msg = traces.runIsActive(S.activeRun) ? activeAssistant() : S.messages[S.messages.length - 1];
    const shown = viewMessages();
    const index = msg ? shown.findIndex((item) => item === msg || (msg.id && item.id === msg.id)) : -1;
    const last = index >= 0 ? S.log.children[index] : null;
    if (!msg || msg.role !== "assistant" || !last || !last.classList.contains("is-assistant") || last.dataset.messageId !== msg.id) {
      paint();
      return;
    }
    const viewport = follow.captureThreadViewport();
    ui.updateAssistant(last, msg, messageRunLive(msg));
    paintRecaps(last);
    turns.syncEmptyReplyRetry();
    artifacts.rememberLatestArtifact();
    setActive(true);
    composer.syncComposer();
    if (S.streaming) {
      const now = Date.now();
      if (now - S.contextRecountAt >= 400) {
        S.contextRecountAt = now;
        context.liveStreamEstimate();
      }
    } else context.syncContextMeter();
    ui.syncLiveDock();
    follow.restoreThreadViewport(viewport);
  }

    function activeAssistant() {
    if (S.activeRun?.message_id) return S.messages.find(msg => msg.role === "assistant" && msg.id === S.activeRun.message_id) || null;
    return [...S.messages].reverse().find((msg) => msg.role === "assistant");
  }

  return {
    activeAssistant,
    dismissRecaps,
    hydrateHistoricalMessage,
    paint,
    paintRecaps,
    playTranscriptFx,
    restoreDisclosures,
    runPhase,
    saveDisclosures,
    schedulePaintLast,
    setActive,
    setPartialTranscript,
    transcriptDomOptions,
    visibleRecaps,
    assistantSegments,
    markArriving,
    whenIdle,
  };
}

export { assistantSegments, markArriving, whenIdle };
