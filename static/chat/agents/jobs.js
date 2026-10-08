// Background jobs list and refresh.
import { S } from '../state.js';

let jobsRendered = "";

let jobsThread = "";

let jobsRequest = null;

let jobsRefreshAgain = false;

function jobElapsedMs(job, nowMs) {
  const started = Number(job && job.started_ms);
  if (Number.isFinite(started) && started > 0) return Math.max(0, nowMs - started);
  return Math.max(0, Number(job && job.elapsed_ms) || 0);
}

let agentJobsRendered = "";

export function createJobs(ctx) {
  const { live, net, sync, traces, transcript, ui } = ctx;
    async function onJobsClick(event) {
    const output = event.target.closest("[data-show-job]");
    if (output) {
      const id = output.dataset.showJob;
      // A subagent's command lives in that subagent's transcript, which is
      // already on screen below its list; the main agent's lives in the thread.
      const inAgent = Boolean(S.agentJobsEl && S.agentJobsEl.contains(output));
      const row = (inAgent ? S.agentBodyEl : S.log)?.querySelector(`[data-execution="${CSS.escape(id)}"]`);
      if (row && inAgent) {
        transcript.revealToolRow(row);
        setTimeout(() => { if (row.isConnected) { row.scrollIntoView({ block: "center", behavior: S.reduceMotion.matches ? "auto" : "smooth" }); row.classList.add("is-work-highlight"); setTimeout(() => row.classList.remove("is-work-highlight"), 1800); } }, S.reduceMotion.matches ? 0 : 230);
      } else if (row) {
        transcript.pauseThreadFollow();
        transcript.revealToolRow(row);
        setTimeout(() => { if (row.isConnected) { row.scrollIntoView({ block: "center", behavior: S.reduceMotion.matches ? "auto" : "smooth" }); row.classList.add("is-work-highlight"); setTimeout(() => row.classList.remove("is-work-highlight"), 1800); } }, S.reduceMotion.matches ? 0 : 230);
      } else {
        const job = S.jobsRows.find(job => String(job.command_id || job.id) === id);
        const container = output.closest(".chat-jobs-row");
        if (job && container) {
          let details = container.querySelector(".chat-jobs-detail");
          if (!details) {
            details = document.createElement("div"); details.className = "chat-jobs-detail chat-step-reveal";
            details.innerHTML = `<div class="chat-step-reveal-inner">${ui.toolDetailHtml({ ...job, name: job.name === "monitor" || job.kind === "monitor" ? "monitor" : "run_terminal_command", status: "running" }, false, true)}</div>`;
            container.appendChild(details);
          }
          transcript.setToolReveal(details, !details.classList.contains("is-open"));
        }
      }
      return;
    }
    const button = event.target.closest("[data-kill-job]");
    if (!button || !S.currentId) return;
    const tid = S.currentId;
    button.disabled = true;
    try {
      await net.api("/chat/threads/" + encodeURIComponent(tid) + "/jobs/" + encodeURIComponent(button.dataset.killJob) + "/kill", { method: "POST", body: "{}" });
      if (tid === S.currentId) await refreshJobs();
    } catch (error) {
      button.disabled = false;
      sync.showFailure("Could not stop background command", error);
    }
  }

    // Each transcript's pinned strip contains only its owner's commands.
  function jobsFor(agentId) {
    const owner = String(agentId || "");
    return S.jobsRows.filter((job) => String(job.agent_run_id || "") === owner);
  }

    function renderJobsList() {
    const nowMs = Date.now();
    const sigOf = (rows) => JSON.stringify(rows.map((job) => [job.id, job.command, job.description, job.host, job.state, job.event_count, job.kind]));
    if (S.jobsListEl) {
      const rows = jobsFor("");
      const sig = sigOf(rows);
      if (sig !== jobsRendered) {
        jobsRendered = sig;
        S.jobsListEl.hidden = !rows.length;
        S.jobsListEl.innerHTML = rows.length
          ? ui.workHeadHtml("Background commands", String(rows.length)) +
            rows.map((job) => ui.jobRowHtml(job, nowMs)).join("")
          : "";
      }
    }
    if (S.agentJobsEl) {
      const rows = S.agentViewId ? jobsFor(S.agentViewId) : [];
      const sig = S.agentViewId + sigOf(rows);
      if (sig !== agentJobsRendered) {
        agentJobsRendered = sig;
        S.agentJobsEl.hidden = !rows.length;
        S.agentJobsEl.innerHTML = rows.length
          ? ui.workHeadHtml("Background commands", String(rows.length)) + rows.map((job) => ui.jobRowHtml(job, nowMs)).join("")
          : "";
      }
    }
    tickJobTimes();
  }

    function tickJobTimes() {
    if (!S.agentsPanel || !S.jobsRows.length) return;
    const nowMs = Date.now();
    S.jobsRows.forEach((job) => {
      S.root.querySelectorAll('[data-job-time="' + CSS.escape(String(job.id)) + '"]').forEach((el) => {
        el.textContent = ui.formatActivityClock(Math.round(jobElapsedMs(job, nowMs) / 1000));
      });
    });
  }

    async function refreshJobs() {
    if (!S.currentId) {
      S.jobsRows = [];
      renderJobsList();
      return;
    }
    const tid = S.currentId;
    const gen = S.generation;
    if (jobsRequest?.tid === tid && jobsRequest.gen === gen) { jobsRefreshAgain = true; return; }
    if (jobsThread !== tid) {
      jobsThread = tid;
      S.completedJobs.clear();
      S.jobsRows = [];
      renderJobsList();
    }
    const ticket = jobsRequest = { tid, gen };
    try {
      const data = await net.api("/chat/threads/" + encodeURIComponent(tid) + "/jobs");
      if (tid !== S.currentId || gen !== S.generation) return;
      S.jobsRows = Array.isArray(data.jobs) ? data.jobs : [];
      S.jobsThreadId = tid;
      renderJobsList();
      live.syncAgentsUi();
      (Array.isArray(data.recent) ? data.recent : []).forEach((job) => S.completedJobs.set(String(job.id), job));
      traces.reconcileBgTraces([...S.jobsRows, ...S.completedJobs.values()]);
      traces.settleOrphanTraces(["run_terminal_command", "monitor"], [...S.jobsRows, ...S.completedJobs.values()]);
    } catch {} finally {
      if (jobsRequest === ticket) {
        jobsRequest = null;
        if (jobsRefreshAgain) { jobsRefreshAgain = false; refreshJobs(); }
      }
    }
  }

  return {
    jobsFor,
    onJobsClick,
    refreshJobs,
    renderJobsList,
    tickJobTimes,
    jobElapsedMs,
  };
}

export { jobElapsedMs };
