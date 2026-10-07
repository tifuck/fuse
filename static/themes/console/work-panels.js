// work-panels presentation. Dependencies are live getters supplied by index.js.
export function create(deps) {
function agentRowHtml(row) {
    const ctx = row.context && Number(row.context.limit) > 0
      ? `${Math.round((Number(row.context.used) || 0) / Number(row.context.limit) * 100)}% ctx`
      : "";
    const commands = deps.jobsFor(row.id).length;
    const facts = [
      row.subagent_type && row.subagent_type !== "general-purpose" ? row.subagent_type : "",
      row.model || "",
      ctx,
      row.children ? `${row.children} spawn${row.children === 1 ? "" : "s"}` : "",
      row.resumable && !deps.agentLive(row.status) ? "resumable" : "",
    ].filter(Boolean);
    const dur = deps.agentDurationSeconds(row);
    const err = String(row.error || "").trim();
    const wake = row.wake_state === "dropped" ? "report not delivered" : row.wake_state === "held" ? "report waits for your next message" : row.wake_state === "retrying" ? "report pending" : "";
    const alert = err || wake;
    const lastCall = !alert && deps.agentLive(row.status) ? deps.ccAgentLastCall(row.recent) : "";
    const activity = alert ? "" : lastCall || deps.agentActivitySummary(row);
    const status = deps.agentStatusText(row);
    const doing = activity && !activity.startsWith(status) ? activity : "";
    return (
      `<button type="button" class="chat-agents-row is-${deps.escapeHtml(row.status || "unknown")}${row.wake_state === "dropped" ? " is-undelivered" : ""}${deps.agentIsStalled(row) ? " is-stalled" : ""}" data-agent-id="${deps.escapeHtml(row.id)}">` +
      '<span class="chat-agents-dot" aria-hidden="true"></span>' +
      `<span class="chat-agents-rowcopy"><span class="chat-agents-name">${deps.escapeHtml(row.description || "Subagent")}</span>` +
      `<span class="chat-agents-meta"><span class="chat-agents-state">${deps.escapeHtml(status)}</span>${doing ? `<span class="chat-agents-doing">${deps.escapeHtml(doing)}</span>` : ""}</span>` +
      (facts.length || commands
        ? `<span class="chat-agents-facts">${commands ? `<span class="chat-agents-cmds">${commands} command${commands === 1 ? "" : "s"}</span>` : ""}${facts.map((fact) => `<span>${deps.escapeHtml(fact)}</span>`).join("")}</span>`
        : "") +
      (alert ? `<span class="chat-agents-sum">${deps.escapeHtml(alert.slice(0, 180))}</span>` : "") +
      "</span>" +
      `<span class="chat-agents-time">${dur != null ? deps.formatActivityClock(dur) : ""}</span>` +
      '<svg class="chat-agents-chevron" viewBox="0 0 16 16" width="12" height="12" aria-hidden="true"><path d="M6 3.5L10.5 8 6 12.5" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>' +
      "</button>"
    );
  }

function workHeadHtml(title, note) {
    return `<header class="chat-work-head"><span>${deps.escapeHtml(title)}</span>${note ? `<small>${deps.escapeHtml(note)}</small>` : ""}</header>`;
  }

function compactionMarkHtml(text, summary) {
    const body = String(text || "").trim();
    const title = body.startsWith("Context pruned") ? "Context pruned" : "Context compacted";
    const handoff = String(summary || "").trim();
    if (title === "Context compacted" && handoff) {
      return (
        `<details class="chat-compact-banner" data-compaction>` +
        `<summary><strong>Compaction</strong><span>Handoff sent to the model</span></summary>` +
        `<div class="chat-compact-handoff">${deps.renderMarkdown(handoff, false, [])}</div>` +
        `</details>`
      );
    }
    const detail = body.startsWith(title) ? body.slice(title.length).replace(/^[.\s]+/, "") : body;
    return `<p class="chat-compact-mark" data-compaction><strong>${deps.escapeHtml(title)}</strong>${detail ? `<span>${deps.escapeHtml(detail)}</span>` : ""}</p>`;
  }

function jobRowHtml(job, nowMs) {
    const secs = Math.round(deps.jobElapsedMs(job, nowMs) / 1000);
    const state = String(job.state || "running");
    const cancelling = state === "cancelling";
    const live = ["running", "queued", "cancelling"].includes(state);
    const label = cancelling ? "cancelling" : live ? "" : state.replace(/_/g, " ");
    const monitor = job.kind === "monitor" || job.name === "monitor";
    // As in Claude Code's task list: a monitor by what it watches for, a shell by its command.
    const title = monitor && job.description ? job.description : deps.displayCommand(job) || job.id;
    const meta = [
      job.host || "",
      monitor ? "monitor" : "",
      label,
      Number.isFinite(Number(job.event_count)) ? `${Number(job.event_count)} events` : "",
    ].filter(Boolean).join(" · ");
    return (
      `<div class="chat-jobs-row is-${deps.escapeHtml(state)}${live ? " is-running" : ""}">` +
      '<span class="chat-agents-dot" aria-hidden="true"></span>' +
      `<button type="button" class="chat-jobs-output-link" data-show-job="${deps.escapeHtml(job.command_id || job.id)}" title="Show command and output">` +
      `<span class="chat-jobs-cmd">${deps.escapeHtml(deps.toolText(title))}</span>` +
      `<span class="chat-jobs-meta" title="${deps.escapeHtml(job.command_id || job.id)}">${deps.escapeHtml(meta)}</span></button>` +
      `<span class="chat-agents-time" data-job-time="${deps.escapeHtml(job.id)}">${deps.formatActivityClock(secs)}</span>` +
      `<button type="button" class="chat-jobs-kill" data-kill-job="${deps.escapeHtml(job.command_id || job.id)}" aria-label="${cancelling ? "Cancelling command" : "Stop background command"}" title="Stop this background command"${cancelling ? " disabled" : ""}>${cancelling ? "…" : "×"}</button>` +
      "</div>"
    );
  }

  return { agentRowHtml, workHeadHtml, compactionMarkHtml, jobRowHtml };
}
