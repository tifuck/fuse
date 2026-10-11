// Turn persistence queue and thread URL.
import { S } from '../state.js';
import { newSession } from '../state.js';

export function createPersist(ctx) {
  const { apply, net, overlays, panels, picker, sidebar, status, traces } = ctx;
    function setPendingTurn(value) {
    S.pendingTurn = value;
    try {
      // The full undo snapshot makes an uncertain-ACK retry restorable after
      // a reload, but a multi-megabyte transcript must not evict the whole
      // sessionStorage store: past the cap the retry falls back to a fresh
      // snapshot of the untouched transcript.
      const rows = [...S.pendingTurns.values()].map((entry) => {
        if (!entry.undoSnap || JSON.stringify(entry.undoSnap).length <= 262144) return entry;
        return { ...entry, undoSnap: undefined };
      });
      if (rows.length) sessionStorage.setItem(S.STORE_PENDING_TURN, JSON.stringify(rows));
      else sessionStorage.removeItem(S.STORE_PENDING_TURN);
    } catch (_) {}
  }

    function queueSave() {
    window.clearTimeout(S.saveTimer);
    const gen = S.generation;
    S.saveTimer = window.setTimeout(() => {
      enqueueSave(gen).catch(() => {});
    }, 200);
  }

    function enqueueSave(expectedGeneration) {
    window.clearTimeout(S.saveTimer);
    const task = S.saveChain.then(() => saveNow(expectedGeneration));
    S.saveChain = task.catch(() => {});
    return task;
  }

    async function flushSave() {
    window.clearTimeout(S.saveTimer);
    if (!S.dirty) return S.saveChain;
    return enqueueSave(S.generation);
  }

    async function flushPending() {
    window.clearTimeout(S.memoryTimer);
    if (S.memoryDirty) await panels.saveMemory();
    await flushSave();
  }

    async function saveNow(expectedGeneration) {
    if (expectedGeneration !== S.generation) return;
    if (!S.dirty) return;
    if (!S.currentId && !S.messages.length) {
      S.dirty = false;
      S.pendingSave = null;
      return;
    }
    if (!net.isLive()) {
      net.markDisconnected();
      throw new Error("Can't connect to the backend");
    }
    const savedRevision = S.revision;
    const targetId = S.currentId;
    const payload = {
      model: S.modelId,
      context: S.threadContext,
      tools: panels.enabledTools(),
    };
    if (S.transcriptDirty && !S.streaming) payload.messages = traces.serialMessages();
    if (targetId) {
      payload.expected_revision = S.serverRevision;
      if (!S.pendingSave || S.pendingSave.id !== targetId || S.pendingSave.revision !== S.serverRevision) {
        S.pendingSave = { id: targetId, revision: S.serverRevision, command_id: newSession() };
      }
      payload.command_id = S.pendingSave.command_id;
    }
    try {
      if (!targetId) {
        S.pendingCreate ||= sidebar.bindCreatePayload({ ...payload, command_id: newSession() });
        const created = await net.api("/chat/threads", { method: "POST", body: JSON.stringify(S.pendingCreate) });
        if (expectedGeneration !== S.generation) return;
        S.currentId = created.id;
        S.pendingCreate = null;
        S.pendingProject = null;
        S.serverRevision = created.revision;
        S.sessionId = "thread-" + S.currentId;
        setThreadUrl(S.currentId);
        sidebar.upsertLocal(created);
        sidebar.renderHistory();
      } else {
        const updated = await net.api("/chat/threads/" + targetId, { method: "PATCH", body: JSON.stringify(payload) });
        if (expectedGeneration !== S.generation || S.currentId !== targetId) return;
        if (S.historyRevision === S.serverRevision) S.historyRevision = updated.revision;
        S.serverRevision = updated.revision;
        S.pendingSave = null;
        sidebar.upsertLocal(updated);
        sidebar.renderHistory();
      }
      // A transcript edit withheld during streaming is still unsaved.
      // It stays dirty (no requeue loop mid-run) and is flushed once the run
      // reaches a terminal event.
      const transcriptSent = Boolean(payload.messages) || !S.transcriptDirty;
      if (S.revision !== savedRevision) queueSave();
      else if (transcriptSent) { S.dirty = false; S.transcriptDirty = false; }
      // Only a save error is this save's to clear; other toasts keep their time.
      if (!S.dirty) picker.releaseHint("save");
      status.syncStatus();
    } catch (error) {
      if (expectedGeneration !== S.generation) throw error;
      if (error.status === 404) {
        S.dirty = false;
        S.transcriptDirty = false;
        S.pendingSave = null;
        sidebar.threadGone(targetId);
        return;
      }
      if (error.status === 409) {
        S.pendingSave = null;
        // A transcript conflict yields to the server. A settings-only save
        // stays dirty so the snapshot keeps the user's choice and re-saves it.
        if (payload.messages) {
          S.dirty = false;
          picker.setHint("This chat changed elsewhere; your edit was not saved.", true);
        }
        S.transcriptDirty = false;
        await apply.reconcileThread();
        return;
      }
      S.dirty = true;
      if (net.isTransportError(error)) net.markDisconnected();
      else status.showFailure("Chat was not saved", error, "save");
      throw error;
    }
  }

    function setThreadUrl(id, { stay = false } = {}) {
    const url = new URL(window.location.href);
    if (id) url.searchParams.set("t", String(id));
    else url.searchParams.delete("t");
    // Closing a layer (e.g. the drawer a chat was picked from) steps history
    // back asynchronously; write the address once that entry is current.
    if (!stay) overlays.dropDrawerLayers();
    FuseBack.whenSettled(() => {
      if (stay) {
        // Still on the mobile list (the open chat was deleted or archived):
        // update the address only, keep whatever entry we are on.
        window.history.replaceState(window.history.state, "", url);
        return;
      }
      const mark = S.historyMobile.matches ? { fuseChat: true } : {};
      if (mark.fuseChat && !(history.state && history.state.fuseChat)) {
        // Mobile: entering a chat leaves the previous entry (the chat list) below,
        // so a back swipe returns to the list instead of leaving the app.
        window.history.pushState(mark, "", url);
      } else {
        window.history.replaceState(mark, "", url);
      }
    });
  }

  return {
    enqueueSave,
    flushPending,
    flushSave,
    queueSave,
    setPendingTurn,
    setThreadUrl,
  };
}
