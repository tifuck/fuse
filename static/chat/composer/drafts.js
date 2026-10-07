// Per-thread drafts for text and attachments.
import { S } from '../state.js';

export function createDrafts(ctx) {
  const { sync } = ctx;
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

  return {
    persist,
  };
}
