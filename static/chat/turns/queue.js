// Queued turns: store, render, drain, dispatch.
import { S } from '../state.js';
import { escapeHtml } from '../core.js';
import { newSession } from '../state.js';
import { readQueue, writeQueueItem, deleteQueueItem } from './queue-store.js';

let queueDraining = false;
// Held queue item ids per thread, when storage is unavailable.
const heldMemory = new Map();

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
      for (const item of S.queuedTurns) if (!pending.includes(item)) {
        try { deleteQueueItem(S.currentId, item.id); } catch (_) {}
      }
      S.queuedTurns = pending;
      renderQueue();
    }
  }

    function queueStoreKey(id = S.currentId) {
    return "fuse.chat.queue." + id;
  }

    // Stop means stop: messages queued when the user stopped a reply wait
    // for an explicit Send now instead of draining into a new turn. Messages
    // queued after the Stop are new instructions and send as usual. Held ids
    // are per thread and outlive reloads; storage is shared by this
    // browser's tabs.
    function queueHoldKey(id = S.currentId) {
    return "fuse.chat.queue.held." + id;
  }

    function heldIds(id = S.currentId) {
    if (!id) return new Set();
    try {
      const raw = JSON.parse(localStorage.getItem(queueHoldKey(id)) || "[]");
      const ids = new Set(Array.isArray(raw) ? raw.map(String) : []);
      for (const key of ids) localStorage.setItem(queueHoldKey(id) + '.' + key, '1');
      localStorage.removeItem(queueHoldKey(id));
      for (let i = 0; i < localStorage.length; i++) {
        const key = localStorage.key(i);
        if (key?.startsWith(queueHoldKey(id) + '.')) ids.add(key.slice(queueHoldKey(id).length + 1));
      }
      return ids;
    } catch (_) {
      return new Set(heldMemory.get(String(id)) || []);
    }
  }

    function queueHeld(id = S.currentId) {
    return heldIds(id).size > 0;
  }

    // A user Stop of the tracked run holds what is queued now. Send now's own
    // stop is not a hold, and this tab's Stop already held at click time (its
    // cancel event must not also hold messages typed while stopping).
    // The stop-cause marker is how tabs share why a run ended: 'stop' means a
    // user Stop whose queued messages must hold, 'send-now' means Send now
    // interrupted the run and must not hold. It is timestamped so stale
    // markers can be swept without touching a run another tab just stopped.
    const CAUSE_TTL = 60 * 60 * 1000;

    function causeOf(runId) {
    if (!runId) return "";
    try {
      const raw = localStorage.getItem('fuse.chat.queue.cancel.' + runId) || "";
      const [kind, at] = raw.split(':');
      if (kind === 'stop' || kind === 'send-now') {
        if (!at || Date.now() - Number(at) < CAUSE_TTL) return kind;
        localStorage.removeItem('fuse.chat.queue.cancel.' + runId);
      }
    } catch (_) {}
    return "";
  }

    function markCause(runId, kind) {
    try { localStorage.setItem('fuse.chat.queue.cancel.' + runId, kind + ':' + Date.now()); } catch (_) {}
  }

    function sweepCauses() {
    try {
      for (let i = localStorage.length - 1; i >= 0; i -= 1) {
        const key = localStorage.key(i);
        if (key?.startsWith('fuse.chat.queue.cancel.')) causeOf(key.slice('fuse.chat.queue.cancel.'.length));
      }
    } catch (_) {}
  }

    function holdQueueForStop(runId, fromEvent = false) {
    const cause = causeOf(runId);
    if (runId && (S.sendNowRuns.has(runId) || cause === 'send-now')) return;
    if (fromEvent && runId && (S.locallyStoppedRuns.has(runId) || cause === 'stop')) return;
    if (runId) {
      S.locallyStoppedRuns.add(runId);
      markCause(runId, 'stop');
    }
    try { S.queuedTurns = readQueue(S.currentId); } catch (_) {}
    const ids = heldIds();
    for (const item of S.queuedTurns) if (!item.sending) ids.add(item.id);
    heldMemory.set(String(S.currentId), [...ids]);
    try { for (const id of ids) localStorage.setItem(queueHoldKey() + '.' + id, '1'); } catch (_) {}
    renderQueue();
  }

    function persistQueue(item) {
    if (!S.currentId || !item) return;
    try { writeQueueItem(S.currentId, item); } catch (_) {}
  }

    function loadQueue() {
    S.queuedTurns = [];
    if (!S.currentId) return renderQueue();
    try {
      const items = readQueue(S.currentId);
      sweepCauses();
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
      renderQueue();
    }
    try { deleteQueueItem(origin, id); } catch (_) {}
  }

    function renderQueue() {
    if (!S.queueEl || !S.queueListEl) return;
    const focused = S.queueListEl.contains(document.activeElement) ? document.activeElement : null;
    const focusedId = focused?.closest("[data-queue-id]")?.dataset.queueId;
    const action = focused?.hasAttribute("data-queue-send") ? "data-queue-send" : focused?.hasAttribute("data-queue-edit") ? "data-queue-edit" : focused?.hasAttribute("data-queue-more") ? "data-queue-more" : "data-queue-cancel";
    const count = S.queuedTurns.length;
    const held = heldIds();
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
    if (!S.queuedTurns.some((item) => item.id === S.queueMenuId && !item.sending)) S.queueMenuId = "";
    // One line per message: the header says "Queued", so a row names only an
    // exceptional state. Edit and Cancel live behind the row's ⋮ menu.
    const html = S.queuedTurns
      .map((item, index) => {
        const state = [item.sending ? "Sending…" : item.immediate ? "Next" : held.has(item.id) ? "Stopped" : "",
          item.attachments?.length ? `${item.attachments.length} attached` : ""].filter(Boolean).join(" · ");
        const open = item.id === S.queueMenuId;
        const off = item.sending ? " disabled" : "";
        return `<li class="chat-queue-item${item.immediate ? " is-immediate" : ""}${item.sending ? " is-sending" : ""}" data-queue-index="${index}" data-queue-id="${escapeHtml(item.id)}" aria-busy="${Boolean(item.sending)}">` +
          `<span class="chat-queue-dot" aria-hidden="true"></span>` +
          `<span class="chat-queue-text" title="${escapeHtml(item.text)}">${escapeHtml(item.text || "Attached files")}</span>` +
          `<span class="chat-queue-state" role="status">${state}</span>` +
          '<span class="chat-queue-actions">' +
          `<button type="button" class="chat-queue-action" data-queue-send="${index}" title="${S.streaming ? "Stop the current reply and send this message now" : "Send this message now"}"${!S.streaming && !held.has(item.id) ? " hidden" : ""}${item.sending || (!S.streaming && !held.has(item.id)) ? " disabled" : ""}>Send now</button>` +
          '<span class="chat-queue-more-wrap">' +
          `<button type="button" class="chat-queue-more" data-queue-more="${index}" aria-haspopup="menu" aria-expanded="${open}" aria-label="More actions" title="Edit or cancel"${off}>` +
          '<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><circle cx="8" cy="3.5" r="1.3" fill="currentColor"/><circle cx="8" cy="8" r="1.3" fill="currentColor"/><circle cx="8" cy="12.5" r="1.3" fill="currentColor"/></svg></button>' +
          `<span class="chat-queue-menu" role="menu"${open ? "" : " hidden"}>` +
          `<button type="button" class="chat-queue-action" role="menuitem" data-queue-edit="${index}"${off}>Edit</button>` +
          `<button type="button" class="chat-queue-action" role="menuitem" data-queue-cancel="${index}"${off}>Cancel</button>` +
          "</span></span></span></li>";
      })
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
    const item = {
      created: Date.now(),
      id: newSession(),
      text: trimmed,
      attachments: (attachments || []).slice(),
      immediate: Boolean(opts && opts.immediate),
      sending: false,
    };
    S.queuedTurns.push(item);
    persistQueue(item);
    renderQueue();
    return true;
  }

    function removeQueuedTurn(index) {
    if (index < 0 || index >= S.queuedTurns.length) return;
    const [item] = S.queuedTurns.splice(index, 1);
    try { deleteQueueItem(S.currentId, item.id); } catch (_) {}
    renderQueue();
  }

    async function drainQueue(opts) {
    if (queueDraining || traces.runIsActive(S.activeRun) || S.streaming || S.sending || S.loading) return;
    const held = heldIds();
    const item = S.queuedTurns.find((entry) => !held.has(entry.id));
    if (!item || item.sending) return;
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
        item.immediate = false;
        persistQueue(item);
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
    item.priority = Date.now();
    S.queuedTurns = [item, ...S.queuedTurns.filter((entry) => entry.id !== item.id)];
    persistQueue(item);
    // Send now is the explicit release of this message's Stop hold.
    const held = heldIds();
    held.delete(item.id);
    heldMemory.set(String(S.currentId), [...held]);
    try { localStorage.removeItem(queueHoldKey() + '.' + item.id); } catch (_) {}
    renderQueue();
    if (traces.runIsActive(S.activeRun) || S.streaming) {
      const origin = S.currentId;
      item.sending = true;
      renderQueue();
      if (S.activeRun?.id) {
        S.sendNowRuns.add(S.activeRun.id);
        markCause(S.activeRun.id, 'send-now');
      }
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
    holdQueueForStop,
    loadQueue,
    queueHeld,
    queueHoldKey,
    parseSlashCommand,
    queueIncomingTurn,
    queueStoreKey,
    removeQueuedTurn,
    renderQueue,
    retireAcceptedQueue,
    sendQueuedNow,
  };
}
