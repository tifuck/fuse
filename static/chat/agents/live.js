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
  read_transcript: "transcript",
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
  const { composer, jobs, net, overlays, perms, picker, sidebar, traces, transcript, ui, view } = ctx;
    function agentStatusText(row) {
    // cancel_requested stays set on the finished row: only a live one is stopping.
    if (row.cancel_requested && agentLive(row.status)) {
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

    // The viewed subagent's own snapshot is fresher than its list row.
  function agentRowsMerged() {
    const run = S.agentRun;
    const rows = S.agentsRows.slice();
    // An open child outside the listed window still gets its row.
    if (S.agentsOpen && run && String(run.id) === S.agentViewId && !rows.some((row) => String(row.id) === S.agentViewId)) {
      rows.push({ id: run.id, description: run.description || "Subagent", status: run.status, parent_run_id: run.parent_run_id, created_at: run.created_at, work_ms: null });
    }
    return rows.map((row) => run && String(run.id) === String(row.id)
      ? {
        ...row,
        status: run.status || row.status,
        cancel_requested: run.cancel_requested ?? row.cancel_requested,
        cancel_requested_at: run.cancel_requested_at || row.cancel_requested_at,
        resume_count: run.resume_count ?? row.resume_count,
        resumable: run.resumable ?? row.resumable,
        has_transcript: run.has_transcript ?? row.has_transcript,
      }
      : row);
  }

    // Spawn order, each child under its parent with tree connectors.
  // A row whose parent is not listed (pruned from the recent window) hangs
  // off the main agent.
  function agentTree() {
    const rows = agentRowsMerged().sort((a, b) =>
      (Date.parse(a.created_at || "") || 0) - (Date.parse(b.created_at || "") || 0) || String(a.id).localeCompare(String(b.id)));
    const ids = new Set(rows.map((row) => String(row.id)));
    const kids = new Map();
    rows.forEach((row) => {
      const parent = row.parent_run_id != null && ids.has(String(row.parent_run_id)) && String(row.parent_run_id) !== String(row.id) ? String(row.parent_run_id) : "";
      if (!kids.has(parent)) kids.set(parent, []);
      kids.get(parent).push(row);
    });
    const out = [];
    const seen = new Set();
    const walk = (parent, prefix) => {
      const list = kids.get(parent) || [];
      list.forEach((row, index) => {
        const id = String(row.id);
        if (seen.has(id)) return;
        seen.add(id);
        const last = index === list.length - 1;
        // One connector per level: a line on through an ancestor that has
        // later siblings, blank after a last one, then this row's tee or elbow.
        out.push({ row, tree: [...prefix, last ? "elbow" : "tee"] });
        walk(id, [...prefix, last ? "blank" : "pipe"]);
      });
    };
    walk("", []);
    return out;
  }

    function mainBusy() {
    return Boolean(S.streaming || traces.runIsActive(S.activeRun));
  }

    // The tree opens by itself while subagents run or one is on screen, and
  // folds to the main row once they are all done; a click on its toggle
  // holds the user's choice until the thread changes.
  function agentsExpanded(liveCount) {
    return S.agentsFold != null ? S.agentsFold : liveCount > 0 || Boolean(S.agentViewId);
  }

    function syncWorkStrip() {
    if (!S.workStripEl) return;
    const hidden = (!S.agentsListEl || S.agentsListEl.hidden) && (!S.jobsListEl || S.jobsListEl.hidden);
    if (S.workStripEl.hidden !== hidden) S.workStripEl.hidden = hidden;
  }

    function renderAgentsList() {
    if (!S.agentsListEl) return;
    if (S.toolGestureOwner === S.agentsListEl) { S.pendingAgentListPaint = true; return; }
    const viewing = S.agentsOpen ? S.agentViewId : "";
    if (!S.agentsRows.length && !viewing) {
      if (!S.agentsListEl.hidden) {
        S.agentsListEl.hidden = true;
        S.agentsListEl.replaceChildren();
      }
      syncWorkStrip();
      return;
    }
    const tree = agentTree();
    const liveCount = tree.filter((item) => agentLive(item.row.status)).length;
    const expanded = agentsExpanded(liveCount);
    let html = ui.agentMainRowHtml({
      state: mainBusy() ? "working" : "idle",
      doing: liveCount ? `${liveCount} running` : "",
      selected: !viewing,
      total: tree.length,
      expanded,
    });
    tree.forEach((item) => {
      const selected = String(item.row.id) === viewing;
      // Folded, the viewed subagent still shows under main.
      if (expanded || selected) html += ui.agentRowHtml(item.row, { tree: expanded ? item.tree : ["elbow"], selected });
    });
    FuseChatDOM.reconcile(S.agentsListEl, html);
    S.agentsListEl.hidden = false;
    S.agentsListEl.classList.toggle("is-folded", !expanded);
    syncWorkStrip();
    if (S.agentsListEl._shown !== viewing) {
      S.agentsListEl._shown = viewing;
      const current = S.agentsListEl.querySelector('[aria-current="true"]');
      if (current && S.agentsListEl.scrollHeight > S.agentsListEl.clientHeight) {
        // The main row (always first) stays pinned over the top of the list.
        const main = S.agentsListEl.firstElementChild;
        const pinned = main && main !== current ? main.offsetHeight : 0;
        const top = current.offsetTop - S.agentsListEl.offsetTop;
        if (top < S.agentsListEl.scrollTop + pinned || top + current.offsetHeight > S.agentsListEl.scrollTop + S.agentsListEl.clientHeight) {
          S.agentsListEl.scrollTop = Math.max(0, top - pinned - current.offsetHeight);
        }
      }
    }
  }

    // "Stopping subagent…" (chat.js) leaves once that subagent is seen
  // finished, in its open transcript or in the list.
    function syncAgentStopHint() {
    const held = picker.heldHint();
    if (!held.startsWith("agent-stop:")) return;
    const id = held.slice("agent-stop:".length);
    const run = S.agentRun && String(S.agentRun.id) === id ? S.agentRun : S.agentsRows.find((row) => String(row.id) === id);
    if (!run) picker.releaseHint(held);
    else if (!agentLive(run.status)) picker.releaseHint(held, "Stopped subagent.");
  }

    function renderAgentHead() {
    syncAgentStopHint();
  }

    function syncAgentLiveDock() {
    syncAgentStopHint();
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
    const view = Boolean(S.agentsOpen && S.agentViewId);
    S.root.classList.toggle("is-agent-view", view);
    if (S.agentViewEl) S.agentViewEl.hidden = !view;
    jobs.renderJobsList();
    renderAgentsList();
    renderAgentHead();
    syncStripTimer();
  }

    // One clock for the strip: live rows tick, and an open subagent polls.
  function syncStripTimer() {
    const need = S.agentsOpen || S.agentsRows.some((row) => agentLive(row.status)) || S.jobsRows.length > 0;
    if (need && !S.agentsTimer) S.agentsTimer = setInterval(agentsTick, 1000);
    else if (!need && S.agentsTimer) {
      clearInterval(S.agentsTimer);
      S.agentsTimer = 0;
    }
  }

    function agentsTick() {
    renderAgentsList();
    jobs.tickJobTimes();
    S.agentsPollTick += 1;
    if (!S.agentsOpen) {
      // Live rows in the tree refresh every few seconds on their own; the
      // socket's agent events usually beat this.
      if (S.agentsPollTick >= 4) {
        S.agentsPollTick = 0;
        if (S.agentsRows.some((row) => agentLive(row.status))) view.refreshAgents();
      }
      syncStripTimer();
      return;
    }
    syncAgentLiveDock();
    if (S.agentsPollTick < 2) return;
    S.agentsPollTick = 0;
    const wsHealthy = net.isLive() && Date.now() - S.agentLastEventAt < 3000;
    if (S.agentViewId && (!S.agentRun || agentLive(S.agentRun.status) || S.agentViewFailCount) && !wsHealthy) view.pollAgentView();
    view.refreshAgents();
  }

    function setAgentsFold(open) {
    S.agentsFold = Boolean(open);
    renderAgentsList();
  }

    // Open: the selected subagent's transcript takes the main one's place (the
  // caller sets agentViewId first). Closed: back to the main agent. Without a
  // subagent to show, opening just unfolds the tree.
  function setAgentsOpen(open) {
    if (open && !S.agentViewId) {
      setAgentsFold(true);
      if (!S.agentsRows.length) picker.setHint("No subagents in this chat yet.");
      return;
    }
    const next = Boolean(open);
    if (next === S.agentsOpen) {
      if (next) syncAgentsUi();
      return;
    }
    S.agentsOpen = next;
    if (next) {
      picker.closePicker();
      perms.setPermsOpen(false);
      S.mainScroll = S.thread ? { top: S.thread.scrollTop, stick: S.stickToBottom } : null;
      overlays.overlayPush("agent");
      view.refreshAgents();
      net.sendSyncView(S.agentViewId);
      if (S.agentRun) view.pollAgentView();
    } else {
      S.agentsViewGen += 1;
      // Progress for a hidden view is discarded; stop the server streaming it.
      net.sendSyncView("");
      S.agentViewId = "";
      S.agentRun = null;
      S.agentMsgState = null;
      S.agentViewFailCount = 0;
      overlays.overlayDrop("agent");
      view.refreshAgents();
    }
    syncAgentsUi();
    syncAgentLiveDock();
    if (!next && S.thread) {
      const saved = S.mainScroll;
      S.mainScroll = null;
      if (!saved || saved.stick) transcript.scrollThread();
      else S.thread.scrollTop = saved.top;
      transcript.syncLatest();
    }
    composer.scheduleFitChatBar();
  }

    function resetAgents() {
    // A new thread keeps its own scroll, not the one saved for the old thread.
    S.mainScroll = null;
    if (S.agentsOpen) setAgentsOpen(false);
    S.agentsViewGen += 1;
    S.agentLifecycle.clear();
    S.agentLifecycleEpoch += 1;
    S.agentsRefreshAgain = false;
    S.agentsRows = [];
    S.agentsLoadedFor = "";
    S.agentsFold = null;
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
    agentResumeAvailable,
    agentStatusText,
    renderAgentHead,
    renderAgentsList,
    resetAgents,
    setAgentsFold,
    setAgentsOpen,
    syncWorkStrip,
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
