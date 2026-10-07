// Agent detail view: show, poll, meter, scroll pinning.
import { S } from '../state.js';
import { formatTokens } from '../core.js';

let _agentViewQueued = false;

export function createView(ctx) {
  const { context, jobs, live: agentLiveUi, net, overlays, sync, traces, transcript, ui } = ctx;
    function scheduleAgentView() {
    if (_agentViewQueued) return;
    _agentViewQueued = true;
    requestAnimationFrame(() => {
      _agentViewQueued = false;
      renderAgentView();
    });
  }

    function agentSeedArticle(item, index) {
    // Same renderers as the main chat so every styling/behavior tweak
    // (tool timelines, themes, bubbles) applies here automatically.
    if (item.role === "marker" || item.kind === "compaction" || item.compaction) {
      const article = document.createElement("article");
      article.className = "chat-turn is-marker";
      article.innerHTML = ui.compactionMarkHtml("Context compacted", item.summary || item.content || "");
      return article;
    }
    if (item.role === "user") {
      // live=true suppresses main-chat-bound actions; the negative index
      // can never collide with a main-chat editingIndex.
      return ui.renderUser({
        role: "user",
        content: String(item.content || ""),
        auto: Boolean(item.auto),
        autoSteps: Array.isArray(item.autoSteps) ? item.autoSteps : null,
        attachments: Array.isArray(item.attachments) ? item.attachments : [],
        fromParent: Boolean(item.fromParent),
        pending: Boolean(item.pending),
      }, -2 - index, true);
    }
    return ui.renderAssistant(
      {
        id: item.id || ("agent-seed-" + index),
        role: "assistant",
        content: String(item.content || ""),
        reasoning: String(item.reasoning || ""),
        error: String(item.error || ""),
        usage: item.usage || null,
        tools: Array.isArray(item.tools) ? item.tools : [],
        segments: Array.isArray(item.segments) ? item.segments : undefined,
        notice: item.notice || "",
        elapsedMs: item.elapsedMs ?? null,
        stopped: Boolean(item.stopped),
        model: item.model || "",
        noActions: true,
      },
      false,
    );
  }

    function syncAgentMeter(run) {
    if (!S.agentMeterEl) return;
    const meter = run && run.compaction && run.compaction.meter;
    const used = Number(meter && meter.used);
    const limit = Number(meter && meter.limit);
    if (!meter || !Number.isFinite(used) || !Number.isFinite(limit) || limit <= 0) {
      S.agentMeterEl.hidden = true;
      closeAgentContextPop();
      return;
    }
    const ratio = Math.max(0, Math.min(1, used / limit));
    const pct = Math.round(ratio * 100);
    S.agentMeterEl.hidden = false;
    if (S.agentContextFill) {
      S.agentContextFill.style.strokeDasharray = String(S.CONTEXT_RING);
      S.agentContextFill.style.strokeDashoffset = String(S.CONTEXT_RING * (1 - ratio));
    }
    const btn = S.agentContextBtn;
    if (btn) {
      btn.classList.toggle("is-warn", Boolean(meter.warn) && !meter.auto);
      btn.classList.toggle("is-auto", Boolean(meter.auto));
      btn.classList.toggle("is-busy", Boolean(meter.compacting));
      const title = `${formatTokens(used)} / ${formatTokens(limit)} tokens (${pct}%)`;
      btn.setAttribute("aria-label", "Subagent context usage " + title);
      btn.title = title;
    }
    if (S.agentContextPop && !S.agentContextPop.hidden) {
      const parts = meter.parts && typeof meter.parts === "object" ? meter.parts : null;
      context.fillContextCard(S.agentContextPop, {
        used, limit, ratio, rows: parts ? context.partsRows(parts) : [["Context", used, ""]], warn: meter.warn, auto: meter.auto,
        autoEnabled: meter.auto_enabled !== false, autoRatio: Number(meter.auto_ratio) || Number(S.compactCfg.auto_ratio) || 0.9,
        compact: false,
      });
    }
  }

    function closeAgentContextPop() {
    if (S.agentContextPop) S.agentContextPop.hidden = true;
    if (S.agentContextBtn) S.agentContextBtn.setAttribute("aria-expanded", "false");
  }

    function agentNearBottom() {
    if (!S.agentLogEl) return true;
    const gap = S.agentLogEl.scrollHeight - S.agentLogEl.scrollTop - S.agentLogEl.clientHeight;
    return gap < 96;
  }

    function restoreAgentScroll(wasAtBottom, savedTop) {
    if (!S.agentLogEl) return;
    if (!wasAtBottom) {
      if (S.agentFollowFrame) cancelAnimationFrame(S.agentFollowFrame);
      S.agentFollowFrame = 0;
      S.agentLogEl.scrollTop = savedTop;
      requestAnimationFrame(() => { S.agentIgnoreScroll = false; });
      return;
    }
    S.agentStick = true;
    if (S.agentFollowFrame) cancelAnimationFrame(S.agentFollowFrame);
    S.agentFollowFrame = 0;
    pinAgentLog();
  }

    // A subagent's transcript follows its end the way the main one does: the
  // view moves with the content in the same frame, never a few frames behind.
  function pinAgentLog() {
    if (!S.agentStick || !S.agentLogEl || !S.agentsOpen) { S.agentIgnoreScroll = false; return; }
    S.agentIgnoreScroll = true;
    S.agentLogEl.scrollTop = Math.max(0, S.agentLogEl.scrollHeight - S.agentLogEl.clientHeight);
    S.agentTailInitialized = true;
    requestAnimationFrame(() => { S.agentIgnoreScroll = false; });
  }

    function renderAgentView() {
    if (!S.agentRun || !S.agentLogEl || !S.agentBodyEl) return;
    if ([...S.toolMotions.keys()].some(owner => S.agentBodyEl.contains(owner))) { S.pendingAgentPaint = true; return; }
    if (S.toolGestureOwner && S.agentBodyEl.contains(S.toolGestureOwner)) { S.pendingAgentPaint = true; return; }
    agentLiveUi.renderAgentHead();
    syncAgentMeter(S.agentRun);
    const live = agentLiveUi.agentLive(S.agentRun.status);
    const wasAtBottom = S.agentStick;
    const savedTop = S.agentLogEl.scrollTop;
    const savedPeeks = new Map(
      [...S.agentBodyEl.querySelectorAll("[data-reason-preview]")].map((el) => [
        el.getAttribute("data-reason-preview"),
        ui.thinkingPosition(el),
      ])
    );
    // Arm before mutating: clearing the log clamps scrollTop and fires a
    // scroll event that must not flip agentStick back on.
    S.agentIgnoreScroll = true;
    const raw = S.agentRun.message || {};
    const id = S.agentRun.message_id;
    S.agentMsgState = S.agentMsgState && S.agentMsgState.id === id ? S.agentMsgState : { id, role: "assistant", noActions: true };
    Object.assign(S.agentMsgState, {
      content: raw.content || "",
      reasoning: raw.reasoning || "",
      error: raw.error || "",
      usage: raw.usage || null,
      tools: Array.isArray(raw.tools) ? raw.tools : [],
      segments: Array.isArray(raw.segments) ? raw.segments : undefined,
      notice: raw.notice || "",
      stopped: Boolean(raw.stopped),
      // Without it the live clock fell back to another run's start time.
      elapsedMs: raw.elapsedMs ?? null,
    });
    // Before compaction prunes the traces: the list the child works from.
    transcript.latestTodoState(S.agentMsgState);
    const seed = Array.isArray(S.agentRun.messages) ? S.agentRun.messages : [];
    // A subagent's own background-command handoffs fold into step rows here
    // exactly as the thread's auto wake rows do, instead of a raw report blob.
    traces.foldAutoCommandResults(seed);
    const compacted = seed.some((item) => item && (item.compaction || item.kind === "compaction"));
    if (compacted) {
      // The seed is the post-handoff provider transcript. A live bubble that
      // still lists tools from before that handoff is not part of the session.
      const kept = new Set();
      seed.forEach((item) => (item.tools || []).forEach((tool) => tool && tool.id && kept.add(tool.id)));
      const handoffAt = (S.agentMsgState.segments || []).findLastIndex((part) => part && part.type === "marker" && part.kind !== "prune" && part.summary);
      if (handoffAt >= 0) {
        S.agentMsgState.segments = S.agentMsgState.segments.slice(handoffAt);
        const liveIds = new Set(S.agentMsgState.segments.filter((part) => part && part.type === "tool" && part.id).map((part) => part.id));
        S.agentMsgState.tools = (S.agentMsgState.tools || []).filter((tool) => tool && liveIds.has(tool.id));
      } else if (kept.size) {
        S.agentMsgState.tools = (S.agentMsgState.tools || []).filter((tool) => tool && kept.has(tool.id));
        if (Array.isArray(S.agentMsgState.segments)) {
          S.agentMsgState.segments = S.agentMsgState.segments.filter((part) => !part || part.type !== "tool" || kept.has(part.id));
        }
      } else {
        S.agentMsgState.tools = [];
        if (Array.isArray(S.agentMsgState.segments)) {
          S.agentMsgState.segments = S.agentMsgState.segments.filter((part) => part && part.type !== "tool");
        }
      }
    }
    // Historical rows can receive corrected status/output. Include their
    // contents so an unchanged identity cannot leave stale details mounted.
    const seedSig = seed.map((item) => {
      if (!item || typeof item !== "object") return "";
      return JSON.stringify(item);
    }).join("\u001e");
    let article = S.agentBodyEl.querySelector("[data-agent-current]");
    const sameRun = article && article.dataset.messageId === String(id || "");
    if (!article || !sameRun) {
      ui.disposeThinking(S.agentBodyEl);
      S.agentBodyEl.innerHTML = "";
      seed.forEach((item, index) => S.agentBodyEl.appendChild(agentSeedArticle(item, index)));
      article = ui.renderAssistant(S.agentMsgState, live);
      article.dataset.messageId = id || "";
      article.dataset.agentCurrent = "true";
      S.agentBodyEl.appendChild(article);
      S.agentBodyEl._seedSig = seedSig;
    } else {
      if (S.agentBodyEl._seedSig !== seedSig) {
        const prior = S.agentBodyEl._seedSig ? S.agentBodyEl._seedSig.split("\u001e") : [];
        const grown = seedSig.startsWith(S.agentBodyEl._seedSig) && seed.length >= prior.length;
        if (!grown) {
          ui.disposeThinking(S.agentBodyEl);
          [...S.agentBodyEl.children].forEach((node) => { if (node !== article) node.remove(); });
          seed.forEach((item, index) => S.agentBodyEl.insertBefore(agentSeedArticle(item, index), article));
        } else {
          seed.slice(prior.length).forEach((item, offset) => {
            S.agentBodyEl.insertBefore(agentSeedArticle(item, prior.length + offset), article);
          });
        }
        S.agentBodyEl._seedSig = seedSig;
      }
      ui.updateAssistant(article, S.agentMsgState, live);
    }
    // A poll rebuilds every node, so carry live think previews over: pinned
    // ones stay glued to the newest line, scrolled-up ones keep their spot.
    article.querySelectorAll("[data-reason-preview]").forEach((el) => {
      const saved = savedPeeks.get(el.getAttribute("data-reason-preview"));
      if (saved && !saved.pinned) { ui.stopThinkingFollow(el); el.scrollTop = saved.top; }
      else if (el._thinking?.follow) ui.followThinking(el, !saved?.initialized);
    });
    restoreAgentScroll(wasAtBottom, savedTop);
    agentLiveUi.syncAgentLiveDock();
    if (transcript.agentClockLive()) transcript.startLiveClock();
  }

    async function showAgent(id) {
    if (!id) return;
    const gen = ++S.agentsViewGen;
    S.agentLastEventAt = 0;
    S.agentViewId = id;
    net.sendSyncView(id);
    S.agentRun = null;
    S.agentMsgState = null;
    S.agentPendingEvents = [];
    S.agentViewFailCount = 0;
    S.agentStick = true;
    S.agentTailInitialized = false;
    if (S.agentFollowFrame) cancelAnimationFrame(S.agentFollowFrame);
    S.agentFollowFrame = 0;
    if (S.agentBodyEl) { ui.disposeThinking(S.agentBodyEl); S.agentBodyEl.innerHTML = ""; }
    if (S.agentLogEl) S.agentLogEl.scrollTop = 0;
    if (S.agentMeterEl) S.agentMeterEl.hidden = true;
    closeAgentContextPop();
    if (S.agentTitleEl) S.agentTitleEl.textContent = "Subagent";
    if (S.agentStatusEl) {
      S.agentStatusEl.textContent = "loading";
      S.agentStatusEl.className = "badge live";
    }
    if (S.agentStopBtn) S.agentStopBtn.hidden = true;
    if (S.agentForceBtn) S.agentForceBtn.hidden = true;
    if (S.agentResumeBtn) S.agentResumeBtn.hidden = true;
    const errorBox = S.root.querySelector("[data-agent-error]");
    if (errorBox) errorBox.hidden = true;
    agentLiveUi.syncAgentLiveDock();
    agentLiveUi.setAgentsOpen(true);
    overlays.overlayPush("agent");
    S.agentBackBtn?.focus({ preventScroll: true });
    try {
      const run = await net.api("/chat/runs/" + encodeURIComponent(id));
      if (gen !== S.agentsViewGen) return;
      S.agentViewFailCount = 0;
      acceptAgentSnapshot(run);
      renderAgentView();
    } catch (error) {
      if (gen !== S.agentsViewGen) return;
      S.agentViewFailCount = 3;
      sync.showFailure("Could not open subagent", error);
      markAgentViewUnavailable(error);
    }
  }

    function markAgentViewUnavailable(error) {
    const errorBox = S.root.querySelector("[data-agent-error]");
    if (S.agentStatusEl) { S.agentStatusEl.textContent = "Unavailable"; S.agentStatusEl.className = "badge fault"; }
    if (S.agentStopBtn) S.agentStopBtn.hidden = true;
    if (S.agentForceBtn) S.agentForceBtn.hidden = true;
    if (S.agentResumeBtn) S.agentResumeBtn.hidden = true;
    if (errorBox) {
      errorBox.hidden = false;
      errorBox.querySelector("[data-agent-error-text]").textContent = `Could not open subagent: ${error?.message || "Request failed."}`;
    }
    agentLiveUi.syncAgentLiveDock();
  }

    async function pollAgentView() {
    if (!S.agentViewId || !S.agentsOpen) return;
    const id = S.agentViewId;
    const gen = S.agentsViewGen;
    if (S.agentPollRequest?.id === id && S.agentPollRequest.gen === gen) { S.agentPollAgain = true; return; }
    const ticket = S.agentPollRequest = { id, gen };
    try {
      const run = await net.api("/chat/runs/" + encodeURIComponent(id));
      if (id !== S.agentViewId || gen !== S.agentsViewGen || !S.agentsOpen) return;
      S.agentViewFailCount = 0;
      const next = agentLiveUi.agentLive(run.status);
      if (!acceptAgentSnapshot(run)) return;
      const errorBox = S.root.querySelector("[data-agent-error]");
      if (errorBox) errorBox.hidden = true;
      renderAgentView();
      if (!next) refreshAgents();
    } catch (error) {
      if (id !== S.agentViewId || gen !== S.agentsViewGen || !S.agentsOpen) return;
      S.agentViewFailCount += 1;
      if (S.agentViewFailCount >= 3) markAgentViewUnavailable(error);
    } finally {
      if (S.agentPollRequest === ticket) {
        S.agentPollRequest = null;
        if (S.agentPollAgain) { S.agentPollAgain = false; if (id === S.agentViewId && gen === S.agentsViewGen && S.agentsOpen) pollAgentView(); }
      }
    }
  }

    function acceptAgentSnapshot(run) {
    if (!Number.isSafeInteger(run.event_cursor)) throw new Error("Subagent snapshot is missing its event cursor.");
    if (S.agentRun?.id === run.id && run.event_cursor < (S.agentRun.event_cursor || 0)) return false;
    S.agentRun = run;
    if (S.agentRun.message) S.agentRun.message._activity = run.activity || null;
    for (const event of S.agentPendingEvents) {
      if (event.payload?.run_id === run.id && event.id > S.agentRun.event_cursor) {
        sync.applyProgressPayload(S.agentRun.message, event.payload, true);
        if (event.payload.status) S.agentRun.status = event.payload.status;
        else if (["agent.started", "agent.resumed"].includes(event.type)) S.agentRun.status = "queued";
        else if (event.type === "agent.progress" && S.agentRun.status === "queued") S.agentRun.status = "running";
        if (event.payload.cancel_requested != null) S.agentRun.cancel_requested = event.payload.cancel_requested;
        else if (event.type === "agent.resumed") S.agentRun.cancel_requested = false;
        S.agentRun.event_cursor = event.id;
      }
    }
    S.agentPendingEvents = [];
    traces.reconcileBgTraces([...S.jobsRows, ...S.completedJobs.values()], false);
    return true;
  }

    async function refreshAgents() {
    if (!S.currentId) {
      S.agentsRows = [];
      S.jobsRows = [];
      S.agentsRequest = null;
      jobs.renderJobsList();
      agentLiveUi.syncAgentsUi();
      return;
    }
    const tid = S.currentId;
    const gen = S.generation;
    if (S.agentsRequest?.tid === tid && S.agentsRequest.gen === gen) { S.agentsRefreshAgain = true; return; }
    const ticket = S.agentsRequest = { tid, gen };
    const epoch = S.agentLifecycleEpoch;
    try {
      const data = await net.api("/chat/threads/" + encodeURIComponent(tid) + "/agents");
      if (tid !== S.currentId || gen !== S.generation) return;
      if (epoch !== S.agentLifecycleEpoch) { S.agentsRefreshAgain = true; return; }
      S.agentsFailCount = 0;
      const priorWork = new Map(S.agentsRows.map((row) => [String(row.id), row]));
      S.agentsRows = Array.isArray(data.agents) ? data.agents : [];
      S.agentsLoadedFor = tid;
      const receivedAt = Date.now();
      S.agentsRows.forEach(row => {
        // (work_ms, _workAt) is one pair: shift the stamp by the base's jump
        // instead of re-stamping, so the dock clock never sawtooths back when
        // work_ms accrues slower than wall time.
        const prior = priorWork.get(String(row.id));
        row._workAt = prior && Number.isFinite(prior._workAt) && Number.isFinite(Number(prior.work_ms)) && Number.isFinite(Number(row.work_ms))
          ? prior._workAt + (Number(row.work_ms) - Number(prior.work_ms)) : receivedAt;
        S.agentLifecycle.set(String(row.id), row);
      });
      traces.reconcileAgentTraces(S.agentsRows, true);
      traces.settleOrphanTraces(["spawn_subagent"], S.agentsRows);
      agentLiveUi.syncAgentsUi();
    } catch {
      if (tid === S.currentId && gen === S.generation) S.agentsFailCount += 1;
    } finally {
      if (S.agentsRequest === ticket) {
        S.agentsRequest = null;
        if (S.agentsRefreshAgain) { S.agentsRefreshAgain = false; refreshAgents(); }
      }
    }
    jobs.refreshJobs();
  }

    function closeAgentView() {
    net.sendSyncView("");
    if (!S.agentViewId) return;
    S.agentsViewGen += 1;
    S.agentViewId = "";
    S.agentRun = null;
    S.agentMsgState = null;
    S.agentViewFailCount = 0;
    agentLiveUi.syncAgentLiveDock();
    overlays.armOverlayGhost(S.agentBackBtn);
    overlays.overlayDrop("agent");
    agentLiveUi.syncAgentsUi();
  }

  return {
    acceptAgentSnapshot,
    agentNearBottom,
    agentSeedArticle,
    closeAgentContextPop,
    closeAgentView,
    pinAgentLog,
    pollAgentView,
    refreshAgents,
    renderAgentView,
    restoreAgentScroll,
    scheduleAgentView,
    showAgent,
    syncAgentMeter,
  };
}
