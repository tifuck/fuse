// Undo/redo stacks, rewind, message editing.
import { S } from '../state.js';
import { newSession } from '../state.js';

function cloneTranscript(list) {
  try {
    return JSON.parse(JSON.stringify(list));
  } catch (_) {
    return Array.isArray(list) ? list.map((msg) => ({ ...msg })) : [];
  }
}

const historyWrites = new Map();

function dropLastTurn(list) {
  const next = list.slice();
  if (!next.length) return next;
  if (next[next.length - 1].role === "assistant") next.pop();
  if (next.length && next[next.length - 1].role === "user") next.pop();
  else if (next.length === list.length) next.pop();
  return next;
}

function askConfirm(message, opts) {
  if (typeof window.fuseConfirm === "function") return window.fuseConfirm(message, opts);
  return Promise.resolve(window.confirm(message));
}

export function createUndo(ctx) {
  const { artifacts, composer, context, net, picker, sync, traces, transcript, turns } = ctx;
    function resetHistoryStacks() {
    S.undoStack = [];
    S.redoStack = [];
    composer.closeSlash();
    turns.closeRewind();
  }

    function historyKey(id) {
    return S.STORE_UNDO + String(id || "");
  }

    function saveHistoryStacks() {
    if (!S.currentId) return;
    const key = historyKey(S.currentId);
    clearTimeout(historyWrites.get(key));
    const undo = S.undoStack.slice(-12), redo = S.redoStack.slice(-12);
    // Persistence is best effort; never serialize twelve multi-megabyte
    // transcripts synchronously in a Send handler. Keep in-memory undo intact.
    historyWrites.set(key, setTimeout(() => {
      historyWrites.delete(key);
      try {
        let remaining = 1000000;
        const bounded = stack => {
          const rows = [];
          for (let i = stack.length - 1; i >= 0; i--) {
            const row = JSON.stringify(stack[i]);
            if (row.length > remaining) break;
            remaining -= row.length;
            rows.unshift(row);
          }
          return "[" + rows.join(",") + "]";
        };
        sessionStorage.setItem(key, '{"u":' + bounded(undo) + ',"r":' + bounded(redo) + '}');
      } catch (_) {}
    }, 250));
  }

    function dropHistoryStacks(id) {
    if (!id) return;
    clearTimeout(historyWrites.get(historyKey(id)));
    historyWrites.delete(historyKey(id));
    try { sessionStorage.removeItem(historyKey(id)); } catch (_) {}
  }

    function loadHistoryStacks() {
    S.undoStack = [];
    S.redoStack = [];
    composer.closeSlash();
    turns.closeRewind();
    if (!S.currentId) return;
    try {
      const raw = sessionStorage.getItem(historyKey(S.currentId));
      if (!raw) return;
      const data = JSON.parse(raw);
      if (Array.isArray(data.u)) S.undoStack = data.u.filter(Array.isArray).map(cloneTranscript).slice(-12);
      if (Array.isArray(data.r)) S.redoStack = data.r.filter(Array.isArray).map(cloneTranscript).slice(-12);
    } catch (_) {
      S.undoStack = [];
      S.redoStack = [];
    }
  }

    function snapshotMessages() {
    return cloneTranscript(traces.serialMessages());
  }

    function rememberUndo(snap) {
    if (!snap) return;
    S.undoStack.push(snap);
    if (S.undoStack.length > 40) S.undoStack.shift();
    S.redoStack = [];
    saveHistoryStacks();
  }

    function applyTranscript(next, hint) {
    S.messages = Array.isArray(next)
      ? next.map((msg) => ({
          ...msg,
          attachments: Array.isArray(msg.attachments) ? msg.attachments.slice() : [],
          tools: Array.isArray(msg.tools) ? msg.tools.slice() : [],
          segments: Array.isArray(msg.segments) ? msg.segments.map((part) => ({ ...part })) : undefined,
        }))
      : [];
    S.editingIndex = -1;
    composer.persist(true);
    transcript.paint();
    context.recountLocalContext();
    artifacts.rememberLatestArtifact();
    if (hint) picker.setHint(hint);
  }

    async function undoConversation(times = 1) {
    if (traces.runIsActive(S.activeRun) || S.sending) {
      picker.setHint("Wait for the current reply to finish.", true);
      return;
    }
    if (S.loading || S.partialTranscript || !net.requireLive()) return;
    if (!S.messages.length && !S.undoStack.length) {
      picker.setHint("Nothing to undo.");
      return;
    }
    const origin = S.currentId;
    const gen = S.generation;
    if (turns.parseSlashCommand(S.input.value)?.name === "undo") {
      S.input.value = "";
      S.drafts.set(origin, "");
      composer.resizeInput();
      composer.syncEmptyComposer();
    }
    const draft = S.input.value;
    const draftVersion = S.composerVersion;
    let restored;
    S.sending = true;
    composer.syncComposer();
    try {
      await sync.flushPending();
      for (let i = 0; i < times; i += 1) {
        if (gen !== S.generation || origin !== S.currentId || traces.runIsActive(S.activeRun)) break;
        const current = snapshotMessages();
        const stacked = S.undoStack.length > 0;
        const previous = stacked ? S.undoStack.at(-1) : dropLastTurn(current);
        if (!stacked && previous.length === current.length) break;
        const retained = new Set(previous.map(msg => msg.id));
        const removed = current.findLast(msg => msg.role === "user" && !msg.auto && !retained.has(msg.id));
        const payload = { messages: previous, expected_revision: S.serverRevision, command_id: newSession() };
        const task = S.saveChain.then(() => net.api("/chat/threads/" + origin, {
          method: "PATCH",
          body: JSON.stringify(payload),
        }));
        S.saveChain = task.catch(() => {});
        const updated = await task;
        if (gen !== S.generation || origin !== S.currentId) break;
        if (stacked) S.undoStack.pop();
        S.redoStack.push(current);
        saveHistoryStacks();
        sync.acceptThread(updated);
        context.recountLocalContext();
        if (removed) restored = removed.content || "";
        picker.setHint(previous.length ? "Undid last turn." : "Conversation cleared.");
      }
    } catch (error) {
      if (gen === S.generation && origin === S.currentId) {
        await sync.reconcileThread().catch(() => {});
        if (net.isTransportError(error)) net.markDisconnected();
        sync.showFailure("Undo was not confirmed", error);
      }
    } finally {
      S.sending = false;
      if (gen === S.generation && origin === S.currentId && restored !== undefined &&
          S.composerVersion === draftVersion && S.input.value === draft && !draft) {
        S.input.value = restored;
        S.drafts.set(origin, restored);
        composer.closeSlash();
        if (window.FuseComposerSpell) {
          FuseComposerSpell.close();
          FuseComposerSpell.refresh();
        }
        composer.resizeInput();
        composer.syncEmptyComposer();
        context.syncContextMeter();
        S.input.focus();
        S.input.setSelectionRange(S.input.value.length, S.input.value.length);
      }
      composer.syncComposer();
      sync.syncStatus();
      if (gen === S.generation && !S.streaming) turns.drainQueue();
    }
  }

    function redoConversation() {
    if (traces.runIsActive(S.activeRun) || S.sending) return;
    if (!net.requireLive()) return;
    if (!S.redoStack.length) {
      picker.setHint("Nothing to redo.");
      return;
    }
    S.undoStack.push(snapshotMessages());
    saveHistoryStacks();
    applyTranscript(S.redoStack.pop(), "Redid.");
  }

    function mutateTranscript(fn, hint) {
    if (traces.runIsActive(S.activeRun) || S.sending) {
      picker.setHint("Wait for the current reply to finish.", true);
      return false;
    }
    if (!net.requireLive()) return false;
    rememberUndo(snapshotMessages());
    fn();
    S.editingIndex = -1;
    composer.persist(true);
    transcript.paint();
    context.recountLocalContext();
    artifacts.rememberLatestArtifact();
    if (hint) picker.setHint(hint);
    return true;
  }

    function deleteAt(index, mode) {
    if (traces.runIsActive(S.activeRun) || S.sending) {
      picker.setHint("Wait for the current reply to finish.", true);
      return;
    }
    if (index < 0 || index >= S.messages.length) return;
    mutateTranscript(() => {
      if (mode === "one") S.messages.splice(index, 1);
      else if (mode === "from") S.messages = S.messages.slice(0, index);
      else if (mode === "after") S.messages = S.messages.slice(0, index + 1);
    }, mode === "one" ? "Message deleted. /undo restores it." : "Removed. /undo restores it.");
  }

    async function confirmDeleteAt(index, mode) {
    const prompts = {
      one: "Delete this message? /undo restores it.",
      from: "Delete this message and everything after it? /undo restores it.",
      after: "Keep messages up to here and drop everything after? /undo restores it.",
    };
    if (!(await askConfirm(prompts[mode] || prompts.one, { confirmLabel: "Delete", danger: true }))) return;
    deleteAt(index, mode);
  }

    function rewindTo(index) {
    if (traces.runIsActive(S.activeRun) || S.sending) return;
    if (!net.requireLive()) return;
    const msg = S.messages[index];
    if (!msg || msg.role !== "user") return;
    const text = msg.content || "";
    const kept = Array.isArray(msg.attachments) ? msg.attachments.slice() : [];
    turns.closeRewind();
    if (!mutateTranscript(() => {
      S.messages = S.messages.slice(0, index);
    }, "Rewound. Edit and send, or keep going.")) return;
    const fresh = S.pendingAttachments.filter(
      (att) => !kept.some((other) => String(other.id || "") && String(other.id) === String(att.id))
    );
    const merged = kept.concat(fresh);
    S.pendingAttachments = merged.slice(0, S.ATTACH_MAX);
    if (merged.length > S.ATTACH_MAX) {
      picker.setHint("Up to " + S.ATTACH_MAX + " attachments stay in the composer; some were left out.", true, 4000);
    }
    S.input.value = text;
    composer.renderAttachList();
    composer.resizeInput();
    composer.syncComposer();
    S.input.focus();
  }

    function beginEdit(index) {
    if (traces.runIsActive(S.activeRun) || !S.messages[index] || S.messages[index].role !== "user") return;
    S.editingIndex = index;
    const atts = S.messages[index].attachments;
    S.editingFiles = Array.isArray(atts) ? atts.slice() : [];
    transcript.paint();
    const box = S.log.querySelector("[data-edit]");
    if (box) {
      box.focus();
      box.setSelectionRange(box.value.length, box.value.length);
    }
  }

    function commitEdit(index, text) {
    if (traces.runIsActive(S.activeRun) || S.sending) return;
    if (!net.requireLive()) return;
    const next = (text || "").trim();
    const kept = S.editingFiles.slice();
    S.editingFiles = [];
    if (!next && !kept.length) {
      S.editingIndex = -1;
      transcript.paint();
      return;
    }
    const snap = snapshotMessages();
    S.messages = S.messages.slice(0, index);
    S.editingIndex = -1;
    composer.persist(true);
    context.recountLocalContext();
    turns.completeTurn(next, kept, { undoSnap: snap, truncatedTo: index, restoreDraft: true });
  }

  return {
    beginEdit,
    commitEdit,
    confirmDeleteAt,
    dropHistoryStacks,
    loadHistoryStacks,
    mutateTranscript,
    redoConversation,
    rememberUndo,
    resetHistoryStacks,
    rewindTo,
    snapshotMessages,
    undoConversation,
    askConfirm,
  };
}

export { askConfirm };
