// Agents list, badges, live dock, open/reset.
import { S } from '../state.js';

function agentLive(status) {
  return status === "queued" || status === "running";
}

function agentQuietSeconds(row) {
  if (row && Number.isFinite(Number(row.quiet_s))) return Math.max(0, Number(row.quiet_s));
  return agentElapsedSeconds(row && row.updated_at);
}

function agentIsStalled(row) {
  if (!row || !agentLive(row.status) || row.cancel_requested) return false;
  if (row.current === "stalled") return true;
  return agentQuietSeconds(row) >= 180;
}

function agentIsWaiting(row) {
  if (!row || !agentLive(row.status) || row.cancel_requested || agentIsStalled(row)) return false;
  if (row.current === "waiting") return true;
  return agentQuietSeconds(row) >= 60;
}

function agentBadgeClass(row) {
  if (!row) return "badge";
  if (row.cancel_requested) return "badge live";
  if (row.status === "completed") return "badge on";
  if (row.status === "failed" || agentIsStalled(row)) return "badge fault";
  if (agentLive(row.status)) return "badge live";
  if (row.status === "cancelled" || row.status === "interrupted") return "badge stop";
  return "badge";
}

function agentElapsedSeconds(iso) {
  const t = Date.parse(iso || "");
  return Number.isFinite(t) ? Math.max(0, (Date.now() - t) / 1000) : 0;
}

// Total active time across every leg the subagent ran (spawn, task_id
// continuations, follow-ups, resumes). created_at/updated_at are wall
// clock and would count every idle gap between legs, so they are not used.
function agentDurationSeconds(row) {
  const base = Number(row && row.work_ms);
  if (!row || row.work_ms == null || !Number.isFinite(base) || base < 0) return null;
  if (!row._workAt) row._workAt = Date.now();
  const tail = row.work_live && row.status === "running" ? Date.now() - row._workAt : 0;
  return (base + Math.max(0, tail)) / 1000;
}

const AGENT_ACTIVITY_LABELS = {
  run_terminal_command: "shell",
  get_command_output: "job",
  kill_command: "kill",
  monitor: "watch",
  kill_subagent: "kill",
  web_search: "search",
  read_url: "page",
  read_file: "file",
  list_dir: "dir",
  write_file: "write",
  search_replace: "edit",
  grep: "grep",
  glob: "glob",
  spawn_subagent: "agent",
  get_subagent_output: "wait",
  list_subagents: "agents",
  search_models: "models",
  load_skill: "skill",
  todo_write: "todo",
  scratch: "scratch",
  create_artifact: "artifact",
  update_artifact: "artifact",
  search_tools: "tools",
};

function agentActivitySummary(row) {
  const entries = row.tool_counts && typeof row.tool_counts === "object" ? Object.entries(row.tool_counts) : [];
  const counts = entries
    .filter(([, n]) => Number(n) > 0)
    .map(([name, n]) => `${n} ${AGENT_ACTIVITY_LABELS[name] || name}`)
    .join(", ");
  if (agentIsStalled(row)) return counts ? `stalled · ${counts}` : "stalled";
  if (agentIsWaiting(row) && (!row.current || row.current === "thinking" || row.current === "waiting")) {
    return counts ? `waiting · ${counts}` : "waiting on model";
  }
  if (!row.current) return counts;
  const label = row.current === "thinking" ? "thinking" : AGENT_ACTIVITY_LABELS[row.current] || row.current;
  return counts ? `${label} · ${counts}` : label;
}

function agentResumeAvailable(run) {
  if (!run || agentLive(run.status)) return false;
  // Mirror chat_store.resume_run: only interrupted/cancelled. Showing Resume
  // for a failed (e.g. provider error) run would just 409; continuing those
  // happens via spawn_subagent task_id in the main chat instead.
  if (run.status !== "interrupted" && run.status !== "cancelled") return false;
  if (run.resume_count != null && Number(run.resume_count) >= 3) return false;
  if (run.resumable != null) return Boolean(run.resumable);
  return Boolean(run.has_transcript);
}

function compactionMarkerText(line) {
  const text = String(line || "").trim().toLowerCase();
  if (text.startsWith("context compacted")) return "Context compacted. The conversation was replaced with a handoff. Nothing else was kept.";
  if (text.startsWith("context pruning")) return "Context pruned. Older tool outputs were shortened; recent results are unchanged.";
  return "";
}

export function createLive(ctx) {
  const { artifacts, composer, jobs, net, overlays, panels, perms, picker, sidebar, transcript, ui, view } = ctx;
    function agentStatusText(row) {
    if (row.cancel_requested) {
      const stamp = Date.parse(row.cancel_requested_at || "");
      const secs = Number.isFinite(stamp) ? Math.max(0, (Date.now() - stamp) / 1000) : 0;
      if (secs >= 30) return "wedged — force stop";
      return secs >= 15 ? `stopping ${ui.formatActivityClock(secs)}` : "stopping";
    }
    if (row.status === "queued") return "queued";
    if (row.status === "completed") return "completed";
    if (row.status === "failed") return "failed";
    if (row.status === "cancelled") return "cancelled";
    if (row.status === "interrupted") return "interrupted";
    if (agentIsStalled(row)) return "stalled";
    if (agentIsWaiting(row)) return "waiting on model";
    if (row.status === "running") return "running";
    return row.status || "unknown";
  }

    // The subagent dock clock: earlier legs plus the live message's clock.
  function agentRunWorkSeconds(run, msg) {
    const before = Math.max(0, Number(run && run.work_ms_before) || 0);
    return before / 1000 + (transcript.liveElapsedSeconds(msg, true) || 0);
  }

    function agentRowsSorted() {
    const live = S.agentsRows
      .filter((row) => agentLive(row.status))
      .sort((a, b) => agentElapsedSeconds(b.created_at) - agentElapsedSeconds(a.created_at));
    const done = S.agentsRows.filter((row) => !agentLive(row.status));
    return { live, done };
  }

    function renderAgentsList() {
    if (!S.agentsListEl) return;
    if (S.toolGestureOwner === S.agentsListEl) { S.pendingAgentListPaint = true; return; }
    const { live, done } = agentRowsSorted();
    const count = [live.length ? `${live.length} running` : "", done.length ? `${done.length} finished` : ""].filter(Boolean).join(" · ");
    let html = ui.workHeadHtml("Subagents", count);
    if (live.length) html += '<p class="chat-agents-kicker">Running · longest first</p>' + live.map(ui.agentRowHtml).join("");
    if (done.length) html += '<p class="chat-agents-kicker">Finished</p>' + done.map(ui.agentRowHtml).join("");
    if (!live.length && !done.length) html += '<p class="chat-agents-empty">No subagents yet. The main agent spawns them for isolated sub-tasks while it works.</p>';
    FuseChatDOM.reconcile(S.agentsListEl, html);
  }

    function renderAgentHead() {
    if (!S.agentViewId || !S.agentRun) return;
    if (S.agentTitleEl) S.agentTitleEl.textContent = S.agentRun.description || "Subagent";
    if (S.agentStatusEl) {
      S.agentStatusEl.textContent = agentStatusText(S.agentRun);
      S.agentStatusEl.className = agentBadgeClass(S.agentRun);
    }
    if (S.agentStopBtn) S.agentStopBtn.hidden = !agentLive(S.agentRun.status);
    const stamp = Date.parse(S.agentRun.cancel_requested_at || "");
    const stoppingSecs = Number.isFinite(stamp) ? Math.max(0, (Date.now() - stamp) / 1000) : 0;
    if (S.agentForceBtn) {
      S.agentForceBtn.hidden = !agentLive(S.agentRun.status);
      S.agentForceBtn.classList.toggle("is-urgent", Boolean(S.agentRun.cancel_requested && stoppingSecs >= 15));
    }
    if (S.agentResumeBtn) S.agentResumeBtn.hidden = !agentResumeAvailable(S.agentRun);
  }

    function syncAgentLiveDock() {
    transcript.scheduleTodoSync();
    if (!S.agentLiveDock) return;
    const stalled = Boolean(S.agentRun && agentIsStalled(S.agentRun));
    const live = Boolean(S.agentViewId && S.agentsOpen && S.agentRun && agentLive(S.agentRun.status) && S.agentViewFailCount < 3 && !stalled);
    if (!live && !stalled) {
      if (!S.agentLiveDock.hidden) S.agentLiveDock.hidden = true;
      S.agentLiveDock.setAttribute("aria-hidden", "true");
      if (S.agentLiveSpin && (S.agentLiveSpin.firstChild || S.agentLiveSpin.dataset.spinSet)) {
        S.agentLiveSpin.replaceChildren();
        delete S.agentLiveSpin.dataset.spinSet;
      }
      return;
    }
    S.agentLiveDock.hidden = false;
    S.agentLiveDock.setAttribute("aria-hidden", "false");
    if (S.agentLiveSpin) S.agentLiveSpin.hidden = !live;
    const shimmer = S.agentLiveDock.querySelector(".chat-status-shimmer");
    if (shimmer) shimmer.hidden = !live && !stalled;
    if (stalled) {
      ui.setLivePhase(S.agentLiveDock, "stalled");
      ui.setLiveLabelText(S.agentLiveLabel, ("stalled — no progress"));
      if (S.agentLiveSpin && (S.agentLiveSpin.firstChild || S.agentLiveSpin.dataset.spinSet)) {
        S.agentLiveSpin.replaceChildren();
        delete S.agentLiveSpin.dataset.spinSet;
      }
    } else if (live) {
      const msg = S.agentMsgState || {};
      if (!msg._spinOrigin) msg._spinOrigin = Date.now();
      const groups = ui.groupTraces(Array.isArray(msg.tools) ? msg.tools : []);
      const status = ui.currentActivity(msg, groups, true);
      ui.setLivePhase(S.agentLiveDock, status.phase);
      // The subagent dock mirrors the main dock of the selected view.
      {
        const line = ui.formatConsoleLiveStatus(msg, groups, {
          meter: S.agentRun.compaction && S.agentRun.compaction.meter,
          seconds: agentRunWorkSeconds(S.agentRun, msg),
        });
        ui.setLiveLabelText(S.agentLiveLabel, line.verb, line.meta);
        if (S.agentLiveSpin) ui.setConsoleSpin(S.agentLiveSpin);
      }
    } else if (S.agentLiveSpin && (S.agentLiveSpin.firstChild || S.agentLiveSpin.dataset.spinSet)) {
      S.agentLiveSpin.replaceChildren();
      delete S.agentLiveSpin.dataset.spinSet;
    }
  }

    function syncAgentsUi() {
    transcript.scheduleTodoSync();
    sidebar.syncCurrentThreadBusy();
    const { live } = agentRowsSorted();
    const liveCount = live.length;
    const workLabel = liveCount ? `${liveCount} agent${liveCount === 1 ? "" : "s"}` : "";
    if (S.agentsPanel) S.agentsPanel.hidden = !S.agentsOpen;
    if (S.agentsBackdrop) S.agentsBackdrop.hidden = !(S.agentsOpen && window.matchMedia("(max-width: 800px)").matches);
    S.root.classList.toggle("is-agents", S.agentsOpen);
    if (S.agentsLiveEl) {
      S.agentsLiveEl.hidden = !liveCount;
      S.agentsLiveEl.textContent = liveCount ? `${liveCount} live` : "";
    }
    if (S.agentsPanel) {
      S.agentsPanel.querySelector('.chat-agents-copy').hidden = Boolean(S.agentViewId);
      S.agentsPanel.querySelector('[data-agent-head]').hidden = !S.agentViewId;
    }
    if (S.agentsOverviewEl) S.agentsOverviewEl.hidden = Boolean(S.agentViewId);
    if (S.agentViewEl) S.agentViewEl.hidden = !S.agentViewId;
    for (const tab of S.agentsTabs) {
      tab.setAttribute("aria-expanded", S.agentsOpen ? "true" : "false");
      tab.classList.toggle("is-live", liveCount > 0);
      tab.title = workLabel || "Agents";
      tab.setAttribute("aria-label", workLabel ? `Agents: ${workLabel}` : "Agents");
      const countEl = tab.querySelector("[data-agents-count]");
      if (countEl) {
        // Live subagents only: background commands have their own strip.
        const next = liveCount ? String(liveCount) : "";
        if (next && countEl.textContent !== next) {
          countEl.classList.remove("is-bump");
          void countEl.offsetWidth;
          countEl.classList.add("is-bump");
        }
        countEl.textContent = next;
        countEl.classList.toggle("has-count", Boolean(next));
      }
    }
    jobs.renderJobsList();
    renderAgentsList();
    renderAgentHead();
  }

    function agentsTick() {
    if (!S.agentsOpen) return;
    renderAgentsList();
    jobs.tickJobTimes();
    syncAgentLiveDock();
    S.agentsPollTick += 1;
    if (S.agentsPollTick < 2) return;
    S.agentsPollTick = 0;
    const wsHealthy = net.isLive() && Date.now() - S.agentLastEventAt < 3000;
    if (S.agentViewId && (!S.agentRun || agentLive(S.agentRun.status) || S.agentViewFailCount) && !wsHealthy) view.pollAgentView();
    view.refreshAgents();
  }

    function setAgentsOpen(open) {
    const next = Boolean(open);
    if (next === S.agentsOpen) {
      if (next) syncAgentsUi();
      return;
    }
    S.agentsOpen = next;
    if (S.agentsOpen) {
      if (S.artifactOpen) artifacts.setArtifactOpen(false);
      if (S.memoryOpen) panels.setMemoryOpen(false);
      if (S.skillsOpen) panels.setSkillsOpen(false);
      perms.setPermsOpen(false);
      if (overlays.overlayIsMobile()) sidebar.setHistoryOpen(false);
      else if (window.matchMedia("(max-width: 1100px)").matches) {
        S.root.classList.add("history-collapsed");
        if (typeof sidebar.syncHistory === "function") sidebar.syncHistory();
      }
      picker.closePicker();
      overlays.overlayPush("agents");
      if (!S.agentsTimer) S.agentsTimer = setInterval(agentsTick, 1000);
      view.refreshAgents();
      if (S.agentViewId) net.sendSyncView(S.agentViewId);
      if (S.agentViewId && S.agentRun) view.pollAgentView();
    } else {
      S.agentsViewGen += 1;
      // Progress for a hidden view is discarded; stop the server streaming it.
      net.sendSyncView("");
      if (!S.agentRun) { S.agentViewId = ""; S.agentMsgState = null; }
      overlays.armOverlayGhost(S.root.querySelector("[data-agents-close]"));
      overlays.overlayDrop("agent");
      overlays.overlayDrop("agents");
      if (S.agentsTimer) {
        clearInterval(S.agentsTimer);
        S.agentsTimer = 0;
      }
    }
    syncAgentsUi();
    composer.scheduleFitChatBar();
  }

    function resetAgents() {
    S.agentsViewGen += 1;
    S.agentLifecycle.clear();
    S.agentLifecycleEpoch += 1;
    S.agentsRefreshAgain = false;
    S.agentsRows = [];
    S.agentsLoadedFor = "";
    S.jobsRows = [];
    S.completedJobs.clear();
    jobs.renderJobsList();
    net.sendSyncView("");
    S.agentViewId = "";
    S.agentRun = null;
    S.agentMsgState = null;
    S.agentViewFailCount = 0;
    S.agentsFailCount = 0;
    syncAgentsUi();
    view.refreshAgents();
  }

  return {
    agentStatusText,
    renderAgentHead,
    renderAgentsList,
    resetAgents,
    setAgentsOpen,
    syncAgentLiveDock,
    syncAgentsUi,
    agentActivitySummary,
    agentDurationSeconds,
    agentIsStalled,
    agentLive,
    agentQuietSeconds,
    compactionMarkerText,
  };
}

export { agentActivitySummary, agentDurationSeconds, agentIsStalled, agentLive, agentQuietSeconds, compactionMarkerText };
