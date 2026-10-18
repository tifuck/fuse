// Per-thread drafts for text and attachments. Text syncs across the user's
// devices (PUT /chat/drafts, draft.updated events); attachments stay here.
import { S, draftKey, newSession } from '../state.js';

const SAVE_MS = 600;

export function createDrafts(ctx) {
  const { input, net, sidebar, sync } = ctx;
    function persist(transcript = false) {
    try {
      if (S.modelId) localStorage.setItem(S.STORE_MODEL, S.modelId);
      localStorage.setItem(S.STORE_EFFORT, JSON.stringify(S.effortByModel));
    } catch (_) {}
    S.dirty = true;
    S.transcriptDirty ||= transcript;
    S.revision += 1;
    if (transcript) sync.enqueueSave(S.generation).catch(() => {});
    else sync.queueSave();
    sync.syncStatus();
  }

  // Server keys are strings: the plain new chat (null here) is "".
  const origin = S.draftOrigin = newSession();
  const pending = new Map();
  const timers = new Map();

  function flushDraft(wire, keepalive = false) {
    window.clearTimeout(timers.get(wire));
    timers.delete(wire);
    if (!pending.has(wire)) return;
    const text = pending.get(wire);
    pending.delete(wire);
    net.api("/chat/drafts", { method: "PUT", keepalive, body: JSON.stringify({ key: wire, text, origin }) }).catch(() => {
      // Offline or refused: retry unless newer text is already queued.
      if (pending.has(wire)) return;
      pending.set(wire, text);
      timers.set(wire, window.setTimeout(() => flushDraft(wire), 5000));
    });
  }

  if (S.drafts) {
    S.drafts.save = (key, text) => {
      const wire = key ?? "";
      pending.set(wire, text);
      window.clearTimeout(timers.get(wire));
      timers.set(wire, window.setTimeout(() => flushDraft(wire), SAVE_MS));
    };
    // Leaving for another page must not drop the last keystrokes.
    window.addEventListener("pagehide", () => [...pending.keys()].forEach((wire) => flushDraft(wire, true)));
  }

  function showDraft(key) {
    if (key !== draftKey() || S.input.value === (S.drafts.get(key) || "")) return;
    S.input.value = S.drafts.get(key) || "";
    input.resizeInput();
    input.syncComposer();
    input.syncEmptyComposer();
  }

  // Another device changed one draft. A local edit still being saved wins.
  function applyRemoteDraft(wire, text) {
    if (pending.has(wire)) return;
    const key = wire === "" ? null : wire;
    S.drafts.remote(key, text || "");
    showDraft(key);
    sidebar.renderHistory();
  }

  // Full snapshot (/chat/sync): the server's set replaces every saved draft.
  function acceptDrafts(map) {
    if (!map || typeof map !== "object") return;
    const keys = new Set([...S.drafts.keys()].map((key) => key ?? ""));
    Object.keys(map).forEach((wire) => keys.add(wire));
    keys.forEach((wire) => {
      if (pending.has(wire)) return;
      const key = wire === "" ? null : wire;
      S.drafts.remote(key, String(map[wire] || ""));
      showDraft(key);
    });
  }

  return {
    acceptDrafts,
    applyRemoteDraft,
    persist,
  };
}
