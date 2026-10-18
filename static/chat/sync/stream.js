// Event stream: receiveEvent and the websocket connect loop.
import { S } from '../state.js';

export function createStream(ctx) {
  const { agents, apply, composer, context, net, perms, persist, picker, sidebar, status, traces, transcript, turns, ui } = ctx;
    async function receiveEvent(event) {
    if (event.id <= S.cursor) return;
    // A gap replay (replayGap) resends events this view already handled. Only
    // the open thread's run events can be new to it; the rest would fire their
    // side effects (notifications, refetches, unread badges) a second time.
    if (event.id <= S.replayedThrough && !(event.thread_id === S.currentId && event.type.startsWith("run."))) {
      S.cursor = event.id;
      return;
    }
    const payload = event.payload || {};
    if (event.type === "context.updated") {
      if (S.contextSaving) await S.contextSaving.catch(() => {});
      apply.acceptContext(payload);
    } else if (event.type === "projects.updated") {
      if (Array.isArray(payload.projects)) {
        S.projects = payload.projects;
        sidebar.renderHistory();
      }
    } else if (event.type === "draft.updated") {
      if (payload.origin !== S.draftOrigin) composer.applyRemoteDraft(String(payload.key ?? ""), payload.text);
    } else if (event.type === "compaction.done" || event.type === "compaction.updated" || event.type === "compaction.pruned") {
      if (event.thread_id === S.currentId) {
        if (payload && payload.meter) context.acceptMeter(payload.meter);
        else if (payload && payload.compaction && payload.compaction.meter) context.acceptMeter(payload.compaction.meter);
        else context.syncContextMeter();
        apply.reconcileThread().catch(() => {});
      }
    } else if (event.type.startsWith("thread.")) {
      if (S.renameSaving) await S.renameSaving.catch(() => {});
      // Refresh the search, but keep its current hits on screen until the new
      // ones land: clearing them first flipped content matches to the local
      // title filter on every thread event.
      if (S.historyQuery) sidebar.queueSearch();
      if (event.type === "thread.deleted") {
        sidebar.threadGone(payload.id || event.thread_id);
      } else {
        const prior = sidebar.threadById(payload.id);
        if (prior && payload.title && payload.title !== prior.title) picker.releaseHint("title:" + payload.id);
        if (!prior || payload.revision >= prior.revision) sidebar.upsertLocal(payload);
        if (event.thread_id === S.currentId && event.id > S.viewCursor) {
          await persist.flushSave().catch(() => {});
          if (event.thread_id === S.currentId && payload.revision > S.serverRevision) {
            if (S.activeRun && !Array.isArray(payload.messages) && !payload.transcript_changed) {
              S.serverRevision = payload.revision;
            } else {
              await apply.reconcileThread();
            }
          }
        }
      }
      sidebar.renderHistory();
    } else if (event.type === "recap.updated") {
      if (event.thread_id === S.currentId) {
        if (!S.sending && !S.queuedTurns.length) S.dismissedRecapAnchors.get(S.currentId)?.delete(String(payload.anchor_id));
        await apply.reconcileThread();
      }
    } else if (event.type === "recap.failed") {
      if (event.thread_id === S.currentId) picker.setHint("Could not generate a recap. Try /recap again.", true);
    } else if (event.type === "title.failed") {
      if (event.thread_id === S.currentId) picker.setHint("Title generation failed. Try /title again.", true);
    } else if (event.type.startsWith("run.") && event.thread_id && event.thread_id !== S.currentId &&
               (event.type === "run.started" || ["run.completed", "run.cancelled", "run.failed", "run.interrupted"].includes(event.type))) {
      const item = sidebar.threadById(event.thread_id);
      if (item) {
        const started = event.type === "run.started";
        // A finished root turn can leave subagents working. Its thread.updated
        // follows in the same commit and carries the authoritative flag, so
        // only clear here when no thread row (and so no such event) exists.
        const next = started || (payload.revision != null ? item.running : false);
        if (started || payload.revision == null) S.serverRunning.set(item.id, next);
        // A finished conversation the user is not reading gets the sidebar
        // badge; opening the thread clears it (the snapshot GET marks read).
        if (!started && !item.unread) item.unread = true;
        if (item.running !== next || item.unread) {
          item.running = next;
          sidebar.renderHistory();
        }
      }
      status.notifyTurnDone(event.thread_id, payload, event.type);
    } else if (event.thread_id === S.currentId && event.type === "agent.tick") {
      // Compact progress for subagents this socket is not viewing. Enough
      // for the agent tree and permission asks; the full patch stream of the
      // viewed agent arrives as regular agent.progress.
      if (Array.isArray(payload.permissions)) perms.syncPermQueueFromMessage({ permissions: payload.permissions });
      const row = S.agentsRows.find((item) => String(item.id) === String(payload.run_id));
      if (row) {
        if (Object.prototype.hasOwnProperty.call(payload, "current")) row.current = payload.current;
        row.quiet_s = 0;
        row.updated_at = new Date().toISOString();
        if (Date.now() - S._agentTickRenderAt > 1000) {
          S._agentTickRenderAt = Date.now();
          agents.renderAgentsList();
        }
      } else if (payload.run_id && Date.now() - S.agentsRefreshAt > 1500) {
        S.agentsRefreshAt = Date.now();
        agents.refreshAgents();
      }
    } else if (event.thread_id === S.currentId && event.type.startsWith("agent.")) {
      const cid = payload.child_id || payload.run_id;
      if (cid && event.type !== "agent.progress") {
        S.agentLifecycleEpoch += 1;
        const row = { ...payload, id: cid };
        if (!row.status && ["agent.started", "agent.resumed"].includes(event.type)) row.status = "queued";
        S.agentLifecycle.set(String(cid), row);
      }
      if (S.agentsOpen && cid === S.agentViewId && !S.agentRun) S.agentPendingEvents.push(event);
      if (cid && event.type !== "agent.progress") traces.reconcileAgentTraces([payload], event.type === "agent.started" || event.type === "agent.resumed");
      if (event.type === "agent.progress") {
        if (Array.isArray(payload.permissions)) perms.syncPermQueueFromMessage({ permissions: payload.permissions });
        if (payload.run_id === S.agentViewId) S.agentLastEventAt = Date.now();
        if (S.agentsOpen && S.agentViewId && payload.run_id === S.agentViewId && S.agentRun && event.id > (S.agentRun.event_cursor || 0)) {
          if (S.agentRun.status === "queued") S.agentRun.status = "running";
          S.agentRun.updated_at = new Date().toISOString();
          if (!S.agentRun.message || typeof S.agentRun.message !== "object") S.agentRun.message = {};
          apply.applyProgressPayload(S.agentRun.message, payload, true);
          S.agentRun.event_cursor = event.id;
          agents.scheduleAgentView();
          // Compaction rewrites the child's provider transcript. The live
          // bubble above is the post-handoff continuation; reload the seed so
          // the summary is shown and the replaced turns leave the panel.
          const compactedNow = (payload.events || []).some((obj) => {
            const fuse = obj && obj.fuse;
            return fuse && (fuse.event === "context" && fuse.phase === "compacted" || fuse.event === "notice" && agents.compactionMarkerText(fuse.message));
          });
          if (compactedNow) agents.pollAgentView();
        }
      }
      if (event.type === "agent.started" || !event.type.startsWith("agent.progress")) {
        agents.refreshAgents();
      } else if (S.agentsOpen && Date.now() - S.agentsRefreshAt > 1500) {
        S.agentsRefreshAt = Date.now();
        agents.refreshAgents();
      }
      if (S.agentsOpen && S.agentViewId && cid === S.agentViewId && event.type !== "agent.progress") {
        if (S.agentRun && event.id > (S.agentRun.event_cursor || 0)) {
          S.agentRun.event_cursor = event.id;
          if (payload.status) S.agentRun.status = payload.status;
          else if (event.type === "agent.resumed" || event.type === "agent.started") S.agentRun.status = "queued";
          if (payload.cancel_requested != null) S.agentRun.cancel_requested = payload.cancel_requested;
          else if (event.type === "agent.resumed") S.agentRun.cancel_requested = false;
          if (payload.message) apply.applyProgressPayload(S.agentRun.message, payload, true);
          agents.scheduleAgentView();
        }
        agents.pollAgentView();
      }
    } else if (event.thread_id === S.currentId && event.id > S.viewCursor && event.type.startsWith("run.")) {
      S.lastRunReceiptAt = Date.now();
      if (event.type === "run.started" || !S.activeRun || S.activeRun.id !== payload.run_id) {
        await apply.reconcileThread();
      } else if (event.type === "run.progress") {
        // Applied while a message is being edited too: S.cursor moves past
        // this event either way, so a skipped patch would never come back.
        if (S.activeRun.status === "queued") S.activeRun.status = "running";
        const msg = S.messages.find((item) => item.id === payload.message_id);
        if (!msg) await apply.reconcileThread();
        else {
          apply.applyProgressPayload(msg, payload);
          // Trace reconciliation walks every message x tool x lifecycle row;
          // run.progress arrives ~5x/s while streaming, so match the agent.tick
          // path's 1s cadence. Settled agent events still reconcile at once,
          // and thread snapshots rebuild traces from scratch.
          if (Date.now() - S.agentTraceRenderAt > 1000) {
            S.agentTraceRenderAt = Date.now();
            traces.reconcileAgentTraces([...S.agentLifecycle.values()]);
          }
          // Updates only exist once a steer split the reply (display_parts);
          // they resize frozen-prefix articles above the reading position, so
          // hold the viewport across the synchronous repaint.
          const updateViewport = transcript.captureThreadViewport();
          let reconciled = false;
          for (const update of payload.updates || []) {
            const index = S.messages.findIndex((item) => item.id === update.id);
            if (index < 0) { await apply.reconcileThread(); reconciled = true; break; }
            Object.assign(S.messages[index], update);
            const article = traces.articleFor(S.messages[index]);
            if (article) ui.updateAssistant(article, S.messages[index], false);
          }
          // A snapshot just applied set the cursors (newer than this event)
          // and restored its own viewport.
          if (!reconciled) {
            transcript.restoreThreadViewport(updateViewport);
            S.viewCursor = event.id;
            S.threadWatermark = event.id;
          }
          transcript.schedulePaintLast();
        }
      } else if (event.type === "run.cancel_requested") {
        if (S.activeRun && S.activeRun.id === payload.run_id) {
          S.stoppingRunId = payload.run_id || S.stoppingRunId;
          S.activeRun.cancel_requested = true;
          // A Stop from another tab or device shows here too.
          picker.holdHint("stop:" + payload.run_id, "Stopping…");
          status.setRunning();
        }
        S.viewCursor = event.id;
        S.threadWatermark = event.id;
      } else if (["run.completed", "run.cancelled", "run.failed", "run.interrupted"].includes(event.type)) {
        const msg = S.messages.find((item) => item.id === payload.message_id);
        if (msg && payload.message) Object.assign(msg, payload.message);
        if (msg && Array.isArray(msg.permissions)) {
          msg.permissions.forEach((perm) => {
            if (perm.kind === "tool_permission" && perm.status === "pending") perm.status = "cancelled";
          });
        }
        perms.sweepStuckPermCards();
        // Only the run the UI is tracking may clear live state; a late or
        // out-of-order terminal event for a previous run must not kill the
        // current turn's display.
        const isTracked = !S.activeRun || S.activeRun.id === payload.run_id;
        // A Stop from any tab or device pauses this device's queue for the thread.
        if (isTracked && event.type === "run.cancelled" && event.thread_id === S.currentId) turns.holdQueueForStop(payload.run_id, true);
        if (isTracked) {
          if (S.compacting) { S.compacting = false; context.syncContextMeter(); }
          S.stoppingRunId = "";
          S.activeRun = null;
          status.setRunning();
        }
        status.notifyTurnDone(event.thread_id, payload, event.type);
        if (payload.revision != null) S.serverRevision = payload.revision;
        S.viewCursor = event.id;
        S.threadWatermark = event.id;
        if (S.editingIndex < 0 && msg && !msg.noActions) {
          const article = traces.articleFor(msg);
          if (article) ui.mountActions(article, S.messages.indexOf(msg));
        }
        status.syncStatus();
        context.syncContextMeter();
        if (S.transcriptDirty && !S.streaming) await persist.flushSave().catch(() => {});
        await apply.reconcileThread();
        if (isTracked) turns.drainQueue();
      }
    }
    S.cursor = Math.max(S.cursor, event.id);
  }

    // A live run's snapshot is durable only up to its stamped cursor, and the
    // run's later patches may already have gone by on this socket (another
    // thread was open, or the snapshot landed after them). Only the server can
    // resend them: reopen the socket at the snapshot's cursor. Once per cursor,
    // so a gap a replay cannot close falls back to a snapshot instead of looping.
    function replayGap() {
    const from = S.viewCursor;
    if (!Number.isSafeInteger(from) || from <= 0 || from >= S.cursor || from === S.gapReplayFrom) return false;
    S.gapReplayFrom = from;
    S.replayedThrough = Math.max(S.replayedThrough, S.cursor);
    S.cursor = from;
    connectSync(false, true);
    return true;
  }

    async function connectSync(resync = !S.syncInitialized, reopen = false) {
    if (!resync && !reopen && net.isLive()) return;
    const epoch = ++S.syncEpoch;
    window.clearTimeout(S.reconnectTimer);
    window.clearTimeout(S.heartbeatTimer);
    if (S.socket) { S.socket.onclose = null; S.socket.close(); S.socket = null; }
    S.syncState = navigator.onLine ? "connecting" : "offline";
    S.syncLabel = navigator.onLine ? (S.reconnectAttempt ? "Reconnecting" : "Connecting") : "Disconnected";
    status.syncStatus();
    const retry = () => {
      if (epoch !== S.syncEpoch) return;
      S.syncState = navigator.onLine ? "reconnecting" : "offline";
      S.syncLabel = navigator.onLine ? (S.reconnectAttempt ? "Disconnected" : "Reconnecting") : "Disconnected";
      status.syncStatus();
      S.reconnectTimer = window.setTimeout(() => connectSync(resync), Math.min(30000, 700 * 2 ** Math.min(S.reconnectAttempt++, 6)) * (0.75 + Math.random() * 0.5));
    };
    try {
      if (resync) {
        const data = await net.api("/chat/sync");
        if (epoch !== S.syncEpoch) return;
        S.cursor = data.cursor;
        S.syncInitialized = true;
        S.historyLoaded = true;
        S.threads = data.threads.filter((item) => !item.archived);
        S.archivedThreads = data.threads.filter((item) => item.archived);
        sidebar.noteServerRunning(data.threads);
        if (Array.isArray(data.projects)) S.projects = data.projects;
        apply.acceptContext(data.context);
        composer.acceptDrafts(data.drafts);
        sidebar.resetSearchLists();
        if (S.historyQuery) sidebar.queueSearch();
        sidebar.renderHistory();
        await apply.reconcileThread();
        if (epoch !== S.syncEpoch) return;
        // The socket must not start ahead of the thread snapshot's durable
        // cursor, or patches between the two would never be replayed.
        if (Number.isSafeInteger(S.viewCursor) && S.viewCursor > 0) S.cursor = Math.min(S.cursor, S.viewCursor);
      }
      const url = new URL("/chat/events", location.href);
      url.protocol = location.protocol === "https:" ? "wss:" : "ws:";
      url.searchParams.set("cursor", String(S.cursor));
      const ws = S.socket = new WebSocket(url);
      ws.onopen = () => {
        heartbeat();
        // Re-assert the viewed subagent so a reconnect keeps full patches
        // coming for it (the new socket starts with no view).
        S._syncViewSent = null;
        net.sendSyncView(S.agentsOpen ? S.agentViewId : "");
      };
      const heartbeat = () => {
        window.clearTimeout(S.heartbeatTimer);
        S.heartbeatTimer = window.setTimeout(() => {
          if (epoch !== S.syncEpoch) return;
          net.markDisconnected();
          ws.close();
        }, S.HEARTBEAT_MS);
      };
      let chain = Promise.resolve();
      ws.onmessage = (message) => {
        if (epoch !== S.syncEpoch) return;
        heartbeat();
        chain = chain.then(async () => {
          if (epoch !== S.syncEpoch) return;
          const data = JSON.parse(message.data);
          if (data.type === "ping") { ws.send("pong"); return; }
          if (data.type === "resync_required") { connectSync(true); return; }
          if (data.type === "events") for (const event of data.events || []) {
            if (epoch !== S.syncEpoch) return;
            try {
              await receiveEvent(event);
            } catch (error) {
              if (error?.gap && event.thread_id === S.currentId && event.type?.startsWith("run.") && replayGap()) return;
              // A single poison event used to reject the whole sync chain,
              // closing the socket and stranding the run spinner until a
              // refresh. Heal instead: skip the event and refetch state.
              console.warn("Sync event handler failed", event && event.type, error);
              S.cursor = Math.max(S.cursor, Number(event && event.id) || S.cursor);
              if (event.type?.startsWith("agent.") && S.agentViewId) agents.pollAgentView().catch(() => {});
              else apply.reconcileThread().catch(() => {});
            }
          }
          if (data.type === "ready") {
            S.cursor = Math.max(S.cursor, data.cursor);
            await apply.reconcileThread();
            if (epoch !== S.syncEpoch) return;
            S.reconnectAttempt = 0;
            S.syncState = "live";
            S.syncLabel = "Live sync";
            status.syncStatus();
            await persist.flushPending().catch(() => {});
            if (epoch === S.syncEpoch && net.isLive()) turns.drainQueue();
          }
        }).catch((error) => { status.syncFailure(error); if (epoch === S.syncEpoch) ws.close(); });
      };
      ws.onclose = (event) => {
        S._syncViewSent = "";
        if (event.code === 4401) {
          S.syncState = "session";
          S.syncLabel = "Session expired. Sign in again.";
          status.syncStatus();
          return;
        }
        retry();
      };
      ws.onerror = () => ws.close();
    } catch (error) { status.syncFailure(error); retry(); }
  }

  return {
    connectSync,
    receiveEvent,
  };
}
