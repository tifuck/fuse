// Run and sync status painting, failure banner, cancel results.
import { S } from '../state.js';
import { runIsStopping } from '../core.js';

let renderedRunState = "";

export function createStatus(ctx) {
  const { apply, composer, net, picker, sidebar, traces, transcript, turns, ui } = ctx;
    function showFailure(action, error) {
    const message = (error && error.message) || "Request failed.";
    picker.setHint(`${action}: ${message}`, true);
  }

    function syncStatus() {
    const el = S.root.querySelector("[data-sync-label]");
    const bar = S.root.querySelector(".chat-sync");
    const live = net.isLive();
    let text = S.syncLabel;
    let state = live ? "live" : S.syncState;
    let fault = false;
    if (S.syncState === "session") {
      text = "Session expired. Sign in again.";
      fault = true;
    } else if (!live) {
      if (S.syncState === "offline" || (S.syncState === "reconnecting" && S.reconnectAttempt > 0)) {
        text = "Disconnected";
        state = "disconnected";
      }
    } else if (S.pendingTurn?.id === S.currentId) {
      text = "Send unconfirmed. Retry safely.";
    }
    if (el) el.textContent = text;
    if (bar) {
      bar.dataset.syncState = state;
      bar.classList.toggle("is-fault", fault);
      const hideSync = !fault && !S.pendingTurns.size && S.pendingTurn?.id !== S.currentId && state !== "disconnected" && state !== "session";
      bar.hidden = hideSync;
    }
    const retry = S.root.querySelector("[data-sync-retry]");
    if (retry) retry.hidden = !(live && S.pendingTurn?.id === S.currentId);
    const discard = S.root.querySelector('[data-pending-discard]');
    if (discard) discard.hidden = !S.pendingTurns.size;
    composer.syncComposer();
  }

    function syncFailure(error) {
    if (error?.status === 404 && S.currentId) {
      sidebar.threadGone(S.currentId);
      return;
    }
    if (net.isTransportError(error) || !error?.status) net.markDisconnected();
    else {
      S.syncState = "reconnecting";
      S.syncLabel = "Reconnecting";
      syncStatus();
    }
  }

    function setRunning(repaint = true) {
    const prior = S.streaming;
    S.streaming = Boolean(S.activeRun && ["queued", "running"].includes(S.activeRun.status) && !runIsStopping(S.activeRun));
    document.body.classList.toggle("is-streaming", S.streaming);
    if (S.streaming || transcript.agentClockLive()) transcript.startLiveClock();
    else transcript.stopLiveClock();
    const current = sidebar.threadById(S.currentId);
    if (current) current.running = sidebar.currentThreadBusy();
    const state = `${S.activeRun?.id || ""}:${S.activeRun?.message_id || ""}:${S.activeRun?.status || ""}:${runIsStopping(S.activeRun)}`;
    if (prior !== S.streaming || state !== renderedRunState) {
      renderedRunState = state;
      sidebar.renderHistory();
      if (repaint && S.messages.length) transcript.schedulePaintLast();
    }
    sidebar.syncRunSpinners();
    composer.syncComposer();
    ui.syncLiveDock();
    turns.renderQueue();
  }

    function notifyTurnDone(threadId, payload, type) {
    try {
      if (type !== "run.completed" && type !== "run.failed") return;
      if (!window.fusePush || !window.fusePush.local) return;
      if (!("Notification" in window) || Notification.permission !== "granted") return;
      if (document.visibilityState !== "hidden" && threadId === S.currentId) return;
      const thread = sidebar.threadById(threadId);
      const title = thread && thread.title ? `Fuse · ${thread.title}` : "Fuse";
      let body = "";
      const msg = payload && payload.message;
      if (msg) body = String(msg.error || msg.content || "").trim().slice(0, 160);
      const note = new Notification(title, { body, icon: "/static/icon-192.png", tag: "fuse-chat-" + (threadId || "x") });
      note.onclick = () => {
        try {
          window.focus();
          if (threadId && threadId !== S.currentId) location.assign("/chat?t=" + encodeURIComponent(threadId));
        } catch {}
      };
    } catch {}
  }

    function healStuckRun(run) {
    if (!traces.runIsActive(run) || !run.id) return;
    // The server checkpoints a live run constantly, so updated_at is the
    // liveness signal; creation age says nothing because agent turns may
    // legitimately run for hours. Only heal a run whose checkpoints have
    // gone stale (or one already asked to stop). 30 min is well past the
    // server 180s stall reaper, so this only fires on a truly dead row.
    const stamp = Date.parse(run.updated_at || run.created_at || "");
    const idleMs = Number.isFinite(stamp) ? Date.now() - stamp : Infinity;
    if (!run.cancel_requested && idleMs < 30 * 60 * 1000) return;
    if (S.healRunId === run.id) return;
    S.healRunId = run.id;
    const runId = run.id;
    net.api("/chat/runs/" + encodeURIComponent(runId))
      .then((fresh) => {
        const fr = fresh || {};
        if (!["queued", "running"].includes(fr.status)) return null;
        const freshStamp = Date.parse(fr.updated_at || fr.created_at || "");
        const freshIdle = Number.isFinite(freshStamp) ? Date.now() - freshStamp : Infinity;
        if (!fr.cancel_requested && freshIdle < 30 * 60 * 1000) return null;
        return net.api("/chat/runs/" + encodeURIComponent(runId) + "/cancel", { method: "POST", body: "{}" });
      })
      .then((data) => { if (data) applyCancelResult(data, runId); })
      .then(() => apply.reconcileThread().catch(() => {}))
      .catch(() => {})
      .finally(() => { if (S.healRunId === runId) S.healRunId = ""; });
  }

    function applyCancelResult(data, runId) {
    const run = data && data.run;
    // A late acknowledgement must not install a stopped thread's run into an
    // idle thread (or resurrect a run already cleared by a terminal event).
    if (runId && S.activeRun?.id !== runId) return;
    if (!run) {
      if (S.activeRun && (!runId || S.activeRun.id === runId)) {
        if (!runId || S.stoppingRunId === runId) S.stoppingRunId = "";
        S.activeRun = null;
        setRunning();
      }
      return;
    }
    if (traces.runIsActive(run)) {
      S.stoppingRunId = run.id || runId || S.stoppingRunId;
      S.activeRun = { ...(S.activeRun || {}), ...run, cancel_requested: true };
      setRunning();
      return;
    }
    if (S.activeRun && S.activeRun.id === (run.id || runId)) {
      if (!runId || S.stoppingRunId === (run.id || runId)) S.stoppingRunId = "";
      S.activeRun = null;
      setRunning();
    }
  }

  return {
    applyCancelResult,
    healStuckRun,
    notifyTurnDone,
    setRunning,
    showFailure,
    syncFailure,
    syncStatus,
  };
}
