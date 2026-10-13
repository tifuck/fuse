// Send path: slash parse, complete, stop, retry, rewind.
import { S } from '../state.js';
import { escapeHtml } from '../core.js';
import { newSession } from '../state.js';

// These commands are dispatched locally, never queued as model input.
const LOCAL_SLASH = new Set(["thinking", "help", "permissions", "undo"]);

export function createSend(ctx) {
  const { composer, context, net, overlays, panels, picker, queue, sidebar, sync, traces, transcript, ui } = ctx;
    function closeRewind() {
    const wasOpen = S.rewindPanel && !S.rewindPanel.hidden;
    if (S.rewindPanel) S.rewindPanel.hidden = true;
    if (S.rewindList) S.rewindList.innerHTML = "";
    if (wasOpen) {
      overlays.armOverlayGhost(S.rewindClose);
      overlays.overlayDrop("rewind");
    }
  }

    function openRewind() {
    if (!S.rewindPanel || !S.rewindList) return;
    if (traces.runIsActive(S.activeRun) || S.sending) return;
    if (!net.requireLive()) return;
    composer.closeSlash();
    overlays.overlayPush("rewind");
    const prompts = S.messages
      .map((msg, index) => ({ msg, index }))
      .filter((item) => item.msg.role === "user");
    S.rewindPanel.hidden = false;
    S.rewindList.innerHTML = "";
    if (!prompts.length) {
      const empty = document.createElement("p");
      empty.className = "chat-rewind-empty";
      empty.textContent = "No messages to rewind to yet.";
      S.rewindList.appendChild(empty);
      return;
    }
    prompts.slice().reverse().forEach((item) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "chat-rewind-item";
      const preview = String(item.msg.content || "").replace(/\s+/g, " ").trim() || "(empty)";
      const after = S.messages.length - item.index - 1;
      btn.innerHTML = `<strong>${escapeHtml(preview.slice(0, 72))}</strong><span>${
        after ? after + " after this" : "latest"
      }</span>`;
      btn.addEventListener("click", () => sidebar.rewindTo(item.index));
      S.rewindList.appendChild(btn);
    });
  }

    function retryAssistant(index) {
    if (traces.runIsActive(S.activeRun) || S.sending || S.messages[index - 1]?.role !== "user") return;
    // Only the final turn is retryable; anything earlier would drop later turns.
    if (index < S.messages.length - 1) return;
    const previous = S.messages[index - 1];
    const snap = sidebar.snapshotMessages();

    completeTurn(previous.content, (previous.attachments || []).slice(), { undoSnap: snap, truncatedTo: index - 1, progress: "Retrying…" });
  }

    function retryLastTurn() {
    if (traces.runIsActive(S.activeRun) || S.sending) {
      picker.setHint("Wait for the current reply to finish.", true);
      return;
    }
    if (!net.requireLive()) return;
    const last = S.messages.at(-1);
    if (!last) {
      picker.setHint("Nothing to retry.");
      return;
    }
    const index = last.role === "assistant" ? S.messages.length - 1 : S.messages.length;
    if (S.messages[index - 1]?.role !== "user") {
      picker.setHint("Nothing to retry.");
      return;
    }
    retryAssistant(index);
  }

    // Picks a failed final turn back up. The failed run's provider history
    // (tool calls and results) is retained, so the model sees the work it
    // already did; Retry instead drops the turn and replays the prompt.
    function continueAssistant() {
    if (traces.runIsActive(S.activeRun) || S.sending) {
      picker.setHint("Wait for the current reply to finish.", true);
      return;
    }
    const last = S.messages.at(-1);
    if (last?.role !== "assistant" || !last.error) return;
    completeTurn("Continue from where you stopped. The previous attempt ended with an error; do not redo work that already completed.", [], { progress: "Continuing…" });
  }

    function syncEmptyReplyRetry() {
    const last = S.messages.at(-1);
    const empty = last?.role === "assistant" && !((last.content || "").trim() || (last.reasoning || "").trim() || last.error || last.tools?.length || last.permissions?.length);
    const existing = S.log.querySelector("[data-empty-retry]");
    if (!empty || traces.runIsActive(S.activeRun) || S.messages.at(-2)?.role !== "user") { existing?.remove(); return; }
    if (existing?.dataset.emptyRetry === last.id) return;
    existing?.remove();
    const host = traces.articleFor(S.messages.at(-2))?.querySelector(".chat-msg-actions");
    if (!host) return;
    const button = document.createElement("button");
    button.type = "button"; button.className = "chat-icon-btn";
    button.dataset.emptyRetry = last.id; button.title = "Retry"; button.setAttribute("aria-label", "Retry");
    button.innerHTML = ui.ACTION_ICONS.retry;
    button.addEventListener("click", () => retryAssistant(S.messages.findIndex(msg => msg.id === last.id)));
    host.appendChild(button);
  }

    async function stopActiveRun() {
    const id = S.activeRun?.id;
    if (!id) return;
    // Hold before the request: its reconcile must not drain the queue.
    queue.holdQueueForStop(id);
    S.stoppingRunId = id;
    if (S.activeRun) S.activeRun.cancel_requested = true;
    // Held until the run leaves the active state (status.setRunning).
    picker.holdHint("stop:" + id, "Stopping…");
    sync.setRunning();
    try {
      const data = await net.api("/chat/runs/" + encodeURIComponent(id) + "/cancel", { method: "POST", body: "{}" });
      sync.applyCancelResult(data, id);
    } catch (error) {
      sync.showFailure("Stop failed", error);
      if (S.activeRun?.id === id) {
        if (S.stoppingRunId === id) S.stoppingRunId = "";
        S.activeRun.cancel_requested = false;
        sync.setRunning();
      }
    }
    await sync.reconcileThread().catch(() => {});
  }

    async function send() {
    const text = S.input.value.trim();
    const local = queue.parseSlashCommand(text);
    if (local && LOCAL_SLASH.has(local.name)) {
      composer.closeSlash();
      composer.runSlash(local.name, local.arg);
      return;
    }
    if (traces.runIsActive(S.activeRun)) {
      if (text || S.pendingAttachments.length) {
        // Composer has content: the button is Send and queues the turn,
        // exactly like Enter. Clear the box first to reach Stop again.
        if (window.FuseComposerSpell) FuseComposerSpell.close();
        composer.closeSlash();
        composer.restoreEnterKeyHint();
        queue.queueIncomingTurn();
        return;
      }
      if (window.FuseComposerSpell) FuseComposerSpell.close();
      composer.closeSlash();
      composer.restoreEnterKeyHint();
      await stopActiveRun();
      return;
    }
    if (window.FuseComposerSpell && FuseComposerSpell.isOpen()) return;
    if (composer.extraPaletteOpen()) {
      composer.acceptCurrentPalette();
      return;
    }
    if ((!text && !S.pendingAttachments.length) || !S.modelId) return;
    const slash = queue.parseSlashCommand(text);
    if (slash) {
      composer.closeSlash();
      composer.runSlash(slash.name, slash.arg);
      return;
    }
    if (S.slashMenu && !S.slashMenu.hidden && S.slashKind === "slash") {
      const items = composer.filteredSlash();
      if (items[S.slashIndex]) {
        composer.acceptSlash(items[S.slashIndex]);
        return;
      }
    }
    const query = composer.slashQuery();
    if (query && !query.name) {
      composer.renderSlash();
      return;
    }
    const invoke = composer.skillInvokeFromText(text);
    completeTurn(text, null, invoke ? { invoke_skill: invoke } : undefined);
  }

    async function completeTurn(text, extraAttachments, opts) {
    const usedPending = extraAttachments == null;
    const attachments = usedPending ? S.pendingAttachments.slice() : extraAttachments.slice();
    if (S.sending || S.loading || (traces.runIsActive(S.activeRun) && !S.pendingTurn)) return;
    if (!net.requireLive()) return;
    if (!(text || "").trim() && !attachments.length) return;
    transcript.dismissRecaps();
    S.navigation += 1;
    const gen = S.generation;
    const origin = S.currentId;
    const settings = { model: S.modelId, tools: panels.enabledTools(), context: S.threadContext };
    S.sending = true;
    // Retry, edit and continue replace history, so they say so until the
    // replacement turn is accepted; the live status line takes over from there.
    if (opts?.progress) picker.holdHint("turn", opts.progress);
    // Only composer sends own the composer. Edits and retries carry their own
    // text and must not overwrite an unrelated draft.
    if (!opts?.queueId && usedPending && !opts?.undoSnap) {
      S.input.value = text;
      S.drafts.set(origin, text);
    }
    // Show local feedback before copying history or waiting for HTTP. This is
    // deliberately outside the durable transcript: retries keep their command
    // identity, and a failed request never leaves a phantom saved message.
    const pending = document.createElement("div");
    pending.className = "chat-log chat-pending-send";
    pending.setAttribute("role", "status");
    const pendingArticle = document.createElement("article");
    pendingArticle.className = "chat-turn is-user";
    const bubble = document.createElement("div");
    bubble.className = "chat-bubble";
    bubble.textContent = text || `${attachments.length} attachment(s)`;
    const status = document.createElement("small");
    status.textContent = "Sending…";

    pendingArticle.append(bubble);
    pending.append(pendingArticle, status);
    S.log.after(pending);
    transcript.setActive(true);
    // Send now from the queue keeps a paused reader's position: the interrupt
    // transition must not yank the viewport to the bottom.
    if (!opts || opts.follow !== false) {
      S.stickToBottom = true;
      transcript.scrollThread();
    }
    composer.syncComposer();
    try {
      await new Promise(resolve => {
        const timer = setTimeout(resolve, 100);
        requestAnimationFrame(() => setTimeout(() => { clearTimeout(timer); resolve(); }, 0));
      });
      if (gen !== S.generation) return;
      const undoSnap = opts && opts.undoSnap ? opts.undoSnap : sidebar.snapshotMessages();
      await sync.flushPending();
      if (gen !== S.generation) return;

      if (!S.currentId) {
        S.pendingCreate ||= sidebar.bindCreatePayload({ ...settings, messages: [], command_id: newSession() });
        const created = await net.api("/chat/threads", { method: "POST", body: JSON.stringify(S.pendingCreate) });
        if (gen !== S.generation) return;
        S.currentId = created.id;
        S.serverRevision = created.revision;
        S.pendingCreate = null;
        S.pendingProject = null;
        sidebar.upsertLocal(created);
        sync.setThreadUrl(S.currentId);
      }
      if (S.pendingTurn && (S.pendingTurn.id !== S.currentId || S.pendingTurn.body.text !== text || (opts?.truncatedTo != null && S.pendingTurn.body.replace_from !== opts.truncatedTo))) {
        throw Object.assign(new Error("A previous send is unconfirmed. Retry its original text before sending another message."), { local: true });
      }
      if (!S.pendingTurn) sync.setPendingTurn({
        id: S.currentId,
        undoSnap,
        body: {
          text,
          attachments,
          reasoning_effort: picker.currentEffort(),
          model: S.modelId,
          tools: panels.enabledTools(),
          context: S.threadContext,
          expected_revision: S.serverRevision,
          ...(opts?.truncatedTo != null ? { replace_from: opts.truncatedTo } : {}),
          command_id: (opts && opts.commandId) || newSession(),
          ...(opts && opts.invoke_skill ? { invoke_skill: opts.invoke_skill } : {}),
        },
      });
      const request = S.pendingTurn;
      const data = await net.api("/chat/threads/" + request.id + "/turns", { method: "POST", body: JSON.stringify(request.body) });
      if (opts?.queueId) queue.acknowledgeQueuedTurn(origin, opts.queueId);
      // Accepted, so no longer unconfirmed, even if the user navigated away
      // meanwhile: cleared by its own thread id, not whichever chat is open.
      sync.setPendingTurn(null, request.id);
      if (gen !== S.generation) return;
      if (usedPending) {
        // Files pasted or dropped while this send was in flight stay queued.
        const sent = new Set(attachments);
        S.pendingAttachments = S.pendingAttachments.filter((att) => !sent.has(att));
        composer.renderAttachList();
      }
      if (!opts?.queueId && usedPending && !opts?.undoSnap && S.input.value === text) {
        if (window.FuseComposerSpell) FuseComposerSpell.learnFromSent(text);
        S.input.value = "";
        S.drafts.delete(origin);
        S.drafts.delete(S.currentId);
        S.draftFiles.delete(origin);
        S.draftFiles.delete(S.currentId);
        if (window.FuseComposerSpell) {
          FuseComposerSpell.close();
          FuseComposerSpell.refresh();
        }
        composer.restoreEnterKeyHint();
        composer.resizeInput();
      }
      pending.remove();
      sync.acceptThread(data.thread);
      sidebar.rememberUndo(request.undoSnap || undoSnap);
      // The acknowledgment is a consistent snapshot. If the socket already
      // applied later progress of this run, acceptThread keeps that reply.
      return true;
    } catch (error) {
      if (gen !== S.generation) return false;
      if (opts?.undoSnap && !opts.edit && !S.input.value.trim() && !S.pendingAttachments.length) {
        // The transcript was never truncated, so a failed replacement only
        // needs its text (and files) back in the composer to try again. An
        // edit still has them in its open editor.
        S.input.value = text;
        S.drafts.set(origin, text);
        S.pendingAttachments = attachments;
        composer.renderAttachList();
        composer.resizeInput();
      }
      if (error.local) {
        sync.showFailure("Send not confirmed; your draft is retained", error);
      } else if (error.status === 409) {
        sync.setPendingTurn(null);
        await sync.reconcileThread().catch(() => {});
        sync.showFailure("Send not confirmed; your draft is retained", error);
      } else if (error.status && error.status < 500) {
        sync.setPendingTurn(null);
        sync.showFailure("Send not confirmed; your draft is retained", error);
      } else {
        net.markDisconnected();
        sync.showFailure("Can't connect to the backend", error);
      }
      return false;
    } finally {
      pending.remove();
      if (opts?.progress) picker.releaseHint("turn");
      // `sending` is global, not per thread: a navigation away mid-send must
      // still release it or every send/open/new-chat control stays locked.
      S.sending = false;
      if (gen === S.generation) { transcript.setActive(S.messages.length > 0); composer.resizeInput(); }
      sync.syncStatus();
    }
  }

  return {
    closeRewind,
    completeTurn,
    openRewind,
    continueAssistant,
    retryAssistant,
    retryLastTurn,
    send,
    stopActiveRun,
    syncEmptyReplyRetry,
    LOCAL_SLASH,
  };
}

export { LOCAL_SLASH };
