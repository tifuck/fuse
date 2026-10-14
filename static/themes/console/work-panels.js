// work-panels presentation. Dependencies are live getters supplied by index.js.
const TREE_GLYPHS = { pipe: "│", tee: "├─", elbow: "└─" };

export function create(deps) {
// The work strip under the header prints agents the way Claude Code's agent
// tree does: one thin line each, tree connectors (opts.tree: one of pipe,
// blank, tee or elbow per level), a status dot, the task,
// what it is doing, its clock, and at most one action. The whole line but the
// action opens that agent's transcript in place of the main one.
function agentRowHtml(row, opts = {}) {
    const live = deps.agentLive(row.status);
    const ctx = row.context && Number(row.context.limit) > 0
      ? `${Math.round((Number(row.context.used) || 0) / Number(row.context.limit) * 100)}% ctx`
      : "";
    const dur = deps.agentDurationSeconds(row);
    const err = String(row.error || "").trim();
    const wake = row.wake_state === "dropped" ? "report not delivered" : row.wake_state === "held" ? "report waits for your next message" : row.wake_state === "retrying" ? "report pending" : "";
    const alert = err || wake;
    const lastCall = !alert && live ? deps.ccAgentLastCall(row.recent) : "";
    const activity = alert ? "" : lastCall || deps.agentActivitySummary(row);
    const status = deps.agentStatusText(row);
    const doing = activity && !activity.startsWith(status) ? activity : "";
    const commands = deps.jobsFor(row.id).length;
    const title = [
      row.description || "Subagent",
      status,
      row.subagent_type && row.subagent_type !== "general-purpose" ? row.subagent_type : "",
      row.model || "",
      ctx,
      commands ? `${commands} command${commands === 1 ? "" : "s"}` : "",
      alert,
    ].filter(Boolean).join(" · ");
    const id = deps.escapeHtml(row.id);
    const who = deps.escapeHtml(row.description || "subagent");
    const stamp = Date.parse(row.cancel_requested_at || "");
    const wedged = row.cancel_requested && Number.isFinite(stamp) && Date.now() - stamp >= 15000;
    const action = live && wedged
      ? `<button type="button" class="chat-agents-act is-urgent" data-agent-force="${id}" aria-label="Force stop ${who}" title="Wedged in stopping: force stop">⊗</button>`
      : live && !row.cancel_requested
        ? `<button type="button" class="chat-agents-act" data-agent-stop="${id}" aria-label="Stop ${who}" title="Stop this subagent">×</button>`
        : deps.agentResumeAvailable(row)
          ? `<button type="button" class="chat-agents-act is-resume" data-agent-resume="${id}" aria-label="Resume ${who}" title="Resume from where it stopped">↻</button>`
          : '<span class="chat-agents-act" aria-hidden="true"></span>';
    return (
      `<div class="chat-agents-row is-${deps.escapeHtml(row.status || "unknown")}${row.wake_state === "dropped" ? " is-undelivered" : ""}${deps.agentIsStalled(row) ? " is-stalled" : ""}" role="listitem"${opts.selected ? ' aria-current="true"' : ""}>` +
      `<button type="button" class="chat-agents-pick" data-agent-id="${id}" title="${deps.escapeHtml(title)}">` +
      (opts.tree && opts.tree.length ? `<span class="chat-agents-tree" aria-hidden="true">${opts.tree.map((kind) => `<i class="is-${deps.escapeHtml(kind)}">${TREE_GLYPHS[kind] || ""}</i>`).join("")}</span>` : "") +
      '<span class="chat-agents-dot" aria-hidden="true"></span>' +
      `<span class="chat-agents-name">${deps.escapeHtml(row.description || "Subagent")}</span>` +
      `<span class="chat-agents-meta"><span class="chat-agents-state">${deps.escapeHtml(status)}</span>` +
      (alert ? `<span class="chat-agents-sum">${deps.escapeHtml(alert.slice(0, 180))}</span>` : doing ? `<span class="chat-agents-doing">${deps.escapeHtml(doing)}</span>` : "") +
      "</span></button>" +
      `<span class="chat-agents-time">${dur != null ? deps.formatActivityClock(dur) : ""}</span>` +
      action +
      "</div>"
    );
  }

// The root of the tree. Its trailing control folds the subagents away.
function agentMainRowHtml({ state, doing, selected, total, expanded }) {
    const fold = `${total} agent${total === 1 ? "" : "s"}`;
    return (
      `<div class="chat-agents-row is-main is-${deps.escapeHtml(state)}" role="listitem"${selected ? ' aria-current="true"' : ""}>` +
      '<button type="button" class="chat-agents-pick" data-agent-main title="Main agent">' +
      '<span class="chat-agents-dot" aria-hidden="true"></span>' +
      '<span class="chat-agents-name">main</span>' +
      `<span class="chat-agents-meta"><span class="chat-agents-state">${deps.escapeHtml(state)}</span>${doing ? `<span class="chat-agents-doing">${deps.escapeHtml(doing)}</span>` : ""}</span>` +
      "</button>" +
      `<button type="button" class="chat-agents-fold" data-agents-fold aria-expanded="${expanded ? "true" : "false"}" title="${expanded ? "Hide" : "Show"} subagents">` +
      `<span>${fold}</span><svg viewBox="0 0 16 16" width="10" height="10" aria-hidden="true"><path d="M4 6l4 4 4-4" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg></button>` +
      "</div>"
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

// A background command: `$ command` (a monitor by what it watches for), then
// where and how it runs, its clock and a stop button. No header row: the
// prompt glyph says what it is.
function jobRowHtml(job, nowMs) {
    const secs = Math.round(deps.jobElapsedMs(job, nowMs) / 1000);
    const state = String(job.state || "running");
    const cancelling = state === "cancelling";
    const live = ["running", "queued", "cancelling"].includes(state);
    const label = cancelling ? "cancelling" : live ? "" : state.replace(/_/g, " ");
    const monitor = job.kind === "monitor" || job.name === "monitor";
    // As in Claude Code's task list: a monitor by what it watches for, a shell by its command.
    const title = monitor && job.description ? job.description : deps.displayCommand(job) || job.id;
    const events = Number(job.event_count);
    const meta = [
      job.host || "",
      monitor ? "monitor" : "",
      label,
      Number.isFinite(events) && events > 0 ? `${events} event${events === 1 ? "" : "s"}` : "",
    ].filter(Boolean).join(" · ");
    const id = deps.escapeHtml(job.command_id || job.id);
    return (
      `<div class="chat-jobs-row is-${deps.escapeHtml(state)}${live ? " is-running" : ""}${monitor ? " is-monitor" : ""}" role="listitem">` +
      `<button type="button" class="chat-jobs-output-link" data-show-job="${id}" title="${deps.escapeHtml(deps.toolText(title))}${meta ? " · " + deps.escapeHtml(meta) : ""}">` +
      '<span class="chat-agents-dot" aria-hidden="true"></span>' +
      `<span class="chat-jobs-cmd">${deps.escapeHtml(deps.toolText(title))}</span>` +
      (meta ? `<span class="chat-jobs-meta">${deps.escapeHtml(meta)}</span>` : "") +
      "</button>" +
      `<span class="chat-agents-time" data-job-time="${deps.escapeHtml(job.id)}">${deps.formatActivityClock(secs)}</span>` +
      `<button type="button" class="chat-jobs-kill" data-kill-job="${id}" aria-label="${cancelling ? "Cancelling" : "Stop"} ${deps.escapeHtml(deps.toolText(title))}" title="Stop this background command"${cancelling ? " disabled" : ""}>${cancelling ? "…" : "×"}</button>` +
      "</div>"
    );
  }

  return { agentRowHtml, agentMainRowHtml, workHeadHtml, compactionMarkHtml, jobRowHtml };
}
