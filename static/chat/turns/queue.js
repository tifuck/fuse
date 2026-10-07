// Queued turns: store, render, drain, dispatch.
import { S } from '../state.js';
import { escapeHtml } from '../core.js';
import { newSession } from '../state.js';

let queueDraining = false;

export function createQueue(ctx) {
  const { composer, picker, send, sync, traces, transcript, ui } = ctx;
    function parseSlashCommand(text) {
    const match = String(text || "").trim().match(/^\/([a-z][a-z-]*)(?:\s+([\s\S]*))?$/i);
    if (!match) return null;
    if (!S.SLASH_COMMANDS.some(command => command.name === match[1].toLowerCase())) return null;
    return { name: match[1].toLowerCase(), arg: (match[2] || "").trim() };
  }

    function retireAcceptedQueue() {
    const pending = S.queuedTurns.filter((item) => !S.acceptedQueueCommands.has(item.id) && !S.acceptedQueueCommands.has(item.id + ":turn"));
    if (pending.length !== S.queuedTurns.length) {
      S.queuedTurns = pending;
      persistQueue();
      renderQueue();
    }
  }

    function queueStoreKey(id = S.currentId) {
    return "fuse.chat.queue." + id;
  }

    function persistQueue() {
    if (!S.currentId) return;
    try {
      localStorage.setItem(queueStoreKey(), JSON.stringify(S.queuedTurns));
    } catch (_) {}
  }

    function loadQueue() {
    S.queuedTurns = [];
    if (!S.currentId) return renderQueue();
    try {
      const raw = localStorage.getItem(queueStoreKey());
      const items = raw ? JSON.parse(raw) : [];
      if (Array.isArray(items))
        S.queuedTurns = items
          .filter((item) => item && typeof item.text === "string")
          .map((item) => ({ ...item, sending: false }));
    } catch (_) {}
    retireAcceptedQueue();
    renderQueue();
  }

    function acknowledgeQueuedTurn(origin, id) {
    if (origin === S.currentId) {
      S.queuedTurns = S.queuedTurns.filter((entry) => entry.id !== id);
      persistQueue();
      renderQueue();
    } else {
      try {
        const saved = JSON.parse(localStorage.getItem(queueStoreKey(origin)) || "[]");
        localStorage.setItem(queueStoreKey(origin), JSON.stringify(saved.filter((entry) => entry.id !== id)));
      } catch (_) {}
    }
  }

    function renderQueue() {
    if (!S.queueEl || !S.queueListEl) return;
    const focused = S.queueListEl.contains(document.activeElement) ? document.activeElement : null;
    const focusedId = focused?.closest("[data-queue-id]")?.dataset.queueId;
    const action = focused?.hasAttribute("data-queue-send") ? "data-queue-send" : focused?.hasAttribute("data-queue-edit") ? "data-queue-edit" : "data-queue-cancel";
    const count = S.queuedTurns.length;
    const viewport = transcript.captureThreadViewport();
    const queueTop = S.queueListEl.scrollTop;
    S.queueEl.hidden = !count;
    S.root.classList.toggle("is-queued", count > 0);
    if (S.queueCountEl) S.queueCountEl.textContent = count ? String(count) : "";
    if (!count) {
      S.queueListEl.innerHTML = "";
      composer.syncDockSpace();
      transcript.restoreThreadViewport(viewport);
      composer.scheduleFitChatBar();
      return;
    }
    const html = S.queuedTurns
      .map(
        (item, index) =>
          `<li class="chat-queue-item${item.immediate ? " is-immediate" : ""}${item.sending ? " is-sending" : ""}" data-queue-index="${index}" data-queue-id="${escapeHtml(item.id)}" aria-busy="${Boolean(item.sending)}">` +
          `<span class="chat-queue-dot" aria-hidden="true"></span>` +
          `<span class="chat-queue-text" title="${escapeHtml(item.text)}">${escapeHtml(item.text || "Attached files")}</span>` +
          `<span class="chat-queue-state" role="status">${item.sending ? "Sending…" : item.immediate ? "Next" : "Queued"}${item.attachments?.length ? ` · ${item.attachments.length} attached` : ""}</span>` +
          '<span class="chat-queue-actions">' +
          `<button type="button" class="chat-queue-action" data-queue-send="${index}" title="Stop the current reply and send this message now"${!S.streaming ? " hidden" : ""}${item.sending || item.immediate || !S.streaming ? " disabled" : ""}>Send now</button>` +
          `<button type="button" class="chat-queue-action" data-queue-edit="${index}"${item.sending ? " disabled" : ""}>Edit</button>` +
          `<button type="button" class="chat-queue-action" data-queue-cancel="${index}"${item.sending ? " disabled" : ""}>Cancel</button>` +
          "</span></li>",
      )
      .join("");
    const template = document.createElement("template");
    template.innerHTML = html;
    const existing = new Map([...S.queueListEl.children].map(el => [el.dataset.queueId, el]));
    const keep = new Set();
    let next = S.queueListEl.firstElementChild;
    for (const fresh of [...template.content.children]) {
      const row = existing.get(fresh.dataset.queueId) || fresh;
      if (row !== fresh) {
        row.className = fresh.className;
        row.dataset.queueIndex = fresh.dataset.queueIndex;
        row.setAttribute("aria-busy", fresh.getAttribute("aria-busy"));
        ui.patchMarkdown(row, fresh.innerHTML);
      }
      if (row !== next) S.queueListEl.insertBefore(row, next);
      next = row.nextElementSibling;
      keep.add(row);
    }
    for (const row of [...S.queueListEl.children]) if (!keep.has(row)) row.remove();
    S.queueListEl.scrollTop = queueTop;
    if (focusedId) S.queueListEl.querySelector(`[data-queue-id="${CSS.escape(focusedId)}"] [${action}]:not(:disabled)`)?.focus({ preventScroll: true });
    composer.syncDockSpace();
    transcript.restoreThreadViewport(viewport);
    composer.scheduleFitChatBar();
  }

    function enqueueTurn(text, attachments, opts) {
    const trimmed = String(text || "").trim();
    if (!trimmed && !(attachments && attachments.length)) return false;
    S.queuedTurns.push({
      id: newSession(),
      text: trimmed,
      attachments: (attachments || []).slice(),
      immediate: Boolean(opts && opts.immediate),
      sending: false,
    });
    persistQueue();
    renderQueue();
    return true;
  }

    function removeQueuedTurn(index) {
    if (index < 0 || index >= S.queuedTurns.length) return;
    S.queuedTurns.splice(index, 1);
    persistQueue();
    renderQueue();
  }

    async function drainQueue(opts) {
    if (queueDraining || traces.runIsActive(S.activeRun) || S.streaming || S.sending || S.loading) return;
    if (!S.queuedTurns.length) return;
    const item = S.queuedTurns[0];
    if (item.sending) return;
    item.sending = true;
    renderQueue();
    queueDraining = true;
    const origin = S.currentId;
    try {
      const ok = await send.completeTurn(item.text, item.attachments || [], { queueId: item.id, commandId: item.id + ":turn", follow: opts?.follow !== false });
      if (ok) {
        acknowledgeQueuedTurn(origin, item.id);
        composer.scheduleFitChatBar();
      } else {
        item.sending = false;
        renderQueue();
      }
    } catch (_) {
      item.sending = false;
      renderQueue();
    } finally {
      queueDraining = false;
      composer.resizeInput();
      sync.syncStatus();
    }
  }

    function queueIncomingTurn() {
    const text = S.input.value.trim();
    const attachments = S.pendingAttachments.slice();
    if ((!text && !attachments.length) || !S.modelId) return;
    if (window.FuseComposerSpell && FuseComposerSpell.isOpen()) return;
    transcript.dismissRecaps();
    enqueueTurn(text, attachments);
    S.input.value = "";
    S.drafts.delete(S.currentId);
    S.pendingAttachments = [];
    composer.renderAttachList();
    if (window.FuseComposerSpell) {
      FuseComposerSpell.close();
      FuseComposerSpell.refresh();
    }
    composer.restoreEnterKeyHint();
    picker.setHint(S.streaming ? "Queued — it sends when this turn finishes." : "Queued.");
    composer.syncComposer();
    composer.resizeInput();
    sync.syncStatus();
  }

    async function sendQueuedNow(item) {
    if (!item || item.sending) return;
    // Send now interrupts: the running reply is stopped outright (Stop kills
    // its tree and force-finishes a wedged worker after the grace window),
    // then this message starts a new turn. Steering kept the old request
    // streaming until its next tool boundary, so the session the user just
    // replaced went on producing tokens and late tool calls.
    item.immediate = true;
    S.queuedTurns = [item, ...S.queuedTurns.filter((entry) => entry.id !== item.id)];
    persistQueue();
    renderQueue();
    if (traces.runIsActive(S.activeRun) || S.streaming) {
      const origin = S.currentId;
      item.sending = true;
      renderQueue();
      await send.stopActiveRun();
      item.sending = false;
      if (origin !== S.currentId) return;
      renderQueue();
    }
    drainQueue({ follow: false });
  }

  return {
    acknowledgeQueuedTurn,
    drainQueue,
    enqueueTurn,
    loadQueue,
    parseSlashCommand,
    queueIncomingTurn,
    queueStoreKey,
    removeQueuedTurn,
    renderQueue,
    retireAcceptedQueue,
    sendQueuedNow,
  };
}
