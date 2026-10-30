// Trace mutation: subagent reconciliation, event merge, persistence, wake folding.
import { S } from '../state.js';
import { EXECUTION_FIELDS, applyCommandResult, childBackedTool, executionIdentity, executionPending, executionState, isShellExecTool, mapAgentToolStatus, runIsActive, shellExitCode, toolBackground, toolFailed, toolIsCurrent } from './semantics.js';
import { messageRunLive, runIsStopping } from '../core.js';
import { newSession } from '../state.js';

function lastCurrentTrace(groups) {
  const items = groups.flatMap((g) => g.items);
  for (let i = items.length - 1; i >= 0; i--) {
    // The todo list is bookkeeping with its own section; it never names what
    // the run is doing.
    if (items[i].trace.name !== "todo_write" && toolIsCurrent(items[i].trace)) return items[i].trace;
  }
  return null;
}

export function createMerge(ctx) {
  const { agents, transcript, ui } = ctx;
    function toolRunning(trace, live) {
    // Waiting is an invocation, not the lifetime of the child it observes.
    if (/^get_(command|subagent)_output$/.test(trace?.name) && ["done", "error", "failed", "interrupted", "cancelled"].includes(trace.status)) return false;
    if (/^get_(command|subagent)_output$/.test(trace?.name) && !live && !toolAwaitingCancellation(trace)) return false;
    if (["completed", "done", "failed", "error", "timed_out", "cancelled", "interrupted", "lost"].includes(executionState(trace))) return false;
    if (["running", "queued", "cancelling"].includes(executionState(trace))) {
      // The call already ended: the row is terminal, whatever the saved
      // execution still says.
      if (trace && trace.name === "run_terminal_command" && ["interrupted", "cancelled"].includes(trace.status)) return false;
      return true;
    }
    if (trace && childBackedTool(trace.name) && executionIdentity(trace) && ["running", "progress", "queued"].includes(trace.status)) return true;
    return Boolean((live || toolAwaitingCancellation(trace)) && toolIsCurrent(trace));
  }

    function toolAwaitingCancellation(trace) {
    return Boolean(runIsActive(S.activeRun) && runIsStopping(S.activeRun) && transcript.activeAssistant()?.tools?.includes(trace));
  }

    function toolInterrupted(trace, live) {
    if (!trace) return false;
    if (["completed", "done"].includes(executionState(trace))) return false;
    if (trace.status === "interrupted" || trace.status === "cancelled") return true;
    if (live || toolAwaitingCancellation(trace)) return false;
    if (childBackedTool(trace.name) && executionIdentity(trace) && !/^get_(command|subagent)_output$/.test(trace.name)) return false;
    return trace.status === "running" || trace.status === "progress" || trace.status === "queued";
  }

    function toolPresentation(trace, live) {
    const execution = executionState(trace);
    const returnedWait = /^get_(command|subagent)_output$/.test(trace.name) && ["done", "error", "failed", "interrupted", "cancelled"].includes(trace.status);
    const abandonedWait = /^get_(command|subagent)_output$/.test(trace.name) && !returnedWait && !live && !toolAwaitingCancellation(trace);
    const endedCommand = trace && trace.name === "run_terminal_command" && ["interrupted", "cancelled"].includes(trace.status);
    const executing = !endedCommand && !returnedWait && !abandonedWait && ["running", "queued", "cancelling"].includes(execution);
    const failed = !executing && ((!returnedWait && ["timed_out", "failed", "error"].includes(execution)) || toolFailed(trace));
    const interrupted = !executing && ((!returnedWait && ["cancelled", "interrupted", "lost"].includes(execution)) || toolInterrupted(trace, live));
    const running = !failed && !interrupted && toolRunning(trace, live);
    const exit = shellExitCode(trace);
    const warning = !running && !failed && !interrupted && exit !== null && exit !== 0;
    const uncertainTermination = trace.termination_confirmed === false || (isShellExecTool(trace.name) && Boolean(execution) && trace.termination_confirmed !== true);
    const statusLabel = !returnedWait && execution === "cancelling" ? "Cancelling" : !returnedWait && execution === "timed_out" ? "Timed out" : !returnedWait && execution === "lost" ? "Lost"
      : failed ? "Failed" : interrupted ? uncertainTermination ? (execution === "cancelled" ? "Cancelled" : "Interrupted") : "Stopped" : running
      ? execution === "queued" || trace.status === "queued" ? "Queued" : trace.status === "done" && executing ? "Background" : /^get_(command|subagent)_output$/.test(trace.name) ? "Waiting" : toolBackground(trace) ? "Background" : "Running"
      : warning ? `Exit ${exit}` : "Done";
    return { failed, interrupted, running, warning, statusLabel,
      state: failed ? "error" : interrupted ? "interrupted" : running ? "running" : warning ? "warning" : "done" };
  }

    function toolCounts(items, live) {
    const counts = { running: 0, failed: 0, stopped: 0, warning: 0, done: 0, phases: {} };
    items.forEach(({ trace }) => {
      const state = toolPresentation(trace, live);
      counts[state.failed ? "failed" : state.interrupted ? "stopped" : state.running ? "running" : state.warning ? "warning" : "done"]++;
      if (state.running) {
        const phase = state.statusLabel.toLowerCase();
        counts.phases[phase] = (counts.phases[phase] || 0) + 1;
      }
    });
    return counts;
  }

    function reconcileAgentTraces(rows, allowRestart = false) {
    const byId = new Map(rows.map((row) => [String(row.child_id || row.run_id || row.id), row]));
    let childChanged = false;
    const targets = [...S.messages, ...(S.agentRun?.messages || []), ...(S.agentRun?.message ? [S.agentRun.message] : [])];
    targets.forEach((msg, index) => {
      let changed = false;
      (msg.tools || []).forEach((trace) => {
        if (!["spawn_subagent", "get_subagent_output"].includes(trace.name) || !trace.child_id) return;
        const row = byId.get(String(trace.child_id));
        if (!row) return;
        const status = mapAgentToolStatus(row.status);
        if (!status && !row.cancel_requested) return;
        const pending = ["running", "progress", "queued"].includes(trace.status) || executionPending(trace);
        // A finished join is historical. A spawn tracks its child's lifecycle.
        if (trace.name === "get_subagent_output") return;
        if (["running", "queued"].includes(status) && !pending && !allowRestart && !row.cancel_requested) return;
        const next = { ...trace };
        if (["running", "queued"].includes(status) && !pending) {
          delete next.summary;
          delete next.preview;
          delete next.error;
        }
        if (status) {
          next.status = status;
          next.execution_status = row.status;
          if (Object.prototype.hasOwnProperty.call(next, "state")) next.state = row.status;
        }
        if (row.cancel_requested && ["running", "queued", ""].includes(status)) {
          next.status = next.status === "done" ? "done" : "running";
          next.execution_status = "cancelling";
          if (Object.prototype.hasOwnProperty.call(next, "state")) next.state = "cancelling";
        }
        if (trace.status === "done" && ["running", "queued"].includes(next.status)) next.status = "done";
        if (!["running", "queued"].includes(next.status) && next.summary === "Background task started") next.summary = "";
        if (!["running", "queued"].includes(next.status) && next.preview === "still running") next.preview = "";
        if (row.description) next.description = row.description;
        if (row.subagent_type && !next.subagent_type) next.subagent_type = row.subagent_type;
        // Claude Code's agent rows: the child's latest calls while it runs,
        // then "Done (N tool uses · Xk tokens · 1m 5s)".
        if (Array.isArray(row.recent)) {
          const same = JSON.stringify(row.recent) === JSON.stringify(trace.agent_recent || null);
          next.agent_recent = same ? trace.agent_recent : row.recent;
        }
        const uses = Object.values(row.tool_counts || {}).reduce((sum, n) => sum + (Number(n) || 0), 0);
        if (row.tool_counts) next.agent_tool_uses = uses;
        if (Number(row.context?.used) > 0) next.agent_tokens = Number(row.context.used);
        // The child's total active time; its spawn call returned long before.
        // While the row runs, (agent_work_ms, _workSnap) is one pair captured
        // at the same instant and its clock ticks forward from the base. Both
        // halves move together: the snap shifts by the base's jump so the
        // clock stays continuous and a repeated payload re-renders nothing,
        // and a pair with no snap (rebuilt after reload) anchors at this poll
        // instead of extrapolating into the future.
        const work = Number(row.work_ms);
        if (Number.isFinite(work) && work >= 0) {
          const prev = Number(trace.agent_work_ms);
          next.agent_work_ms = work;
          next._workSnap = Number.isFinite(prev) && trace._workSnap ? trace._workSnap + (work - prev) : Date.now();
        }
        if (row.wake_state) next.wake_state = row.wake_state;
        else delete next.wake_state;
        const terminalFailure = ["failed", "error", "cancelled", "interrupted", "lost", "timed_out"].includes(row.status);
        const reason = row.message?.error || row.error || row.message?.notice || "";
        const summary = terminalFailure
          ? `Subagent ${row.status}${reason ? `: ${reason}` : ""}. No completed report is available.`
          : row.status === "completed" || row.status === "done" ? row.message?.content || row.summary : "";
        // List summaries are previews, not replacements for a full report
        // already received through the lifecycle event or tool result.
        if (summary && (terminalFailure || row.message || !next.summary || next.summary === "Background task started")) {
          next.summary = String(summary).slice(0, 4000);
          next.output_truncated = String(summary).length > 4000;
          if (terminalFailure) next.preview = next.summary.slice(0, 800);
        }
        if (next.status === "error") next.error = row.message?.error || row.error || next.error || "Subagent failed without a report.";
        else if (next.status === "interrupted") next.error = row.message?.error || row.error || next.error || "";
        else if (["done", "running", "queued"].includes(next.status)) delete next.error;
        const traceChanged = Object.keys(next).some((key) => next[key] !== trace[key]) ||
          Object.keys(trace).some((key) => !(key in next));
        if (traceChanged) {
          Object.keys(trace).forEach((key) => delete trace[key]);
          Object.assign(trace, next);
          changed = true;
        }
      });
      if (changed && index < S.messages.length) {
        const article = articleFor(msg);
        if (article) ui.updateAssistant(article, msg, messageRunLive(msg));
      }
      if (changed && index >= S.messages.length) childChanged = true;
    });
    if (childChanged && S.agentsOpen) agents.scheduleAgentView();
    ui.syncLiveDock();
    if (ui.workClocksLive()) transcript.startLiveClock();
  }

    function persistToolTrace(trace) {
    if (!trace || typeof trace !== "object") return null;
    const keepRunning = childBackedTool(trace.name) && executionIdentity(trace) && (trace.status === "running" || trace.status === "progress");
    const status = keepRunning
      ? "running"
      : (trace.status === "running" || trace.status === "progress" ? "interrupted" : trace.status || "done");
    const rec = {
      name: trace.name || "unknown",
      status: status === "progress" ? "interrupted" : status,
    };
    if (trace.id) rec.id = String(trace.id).slice(0, 64);
    if (trace.query) rec.query = String(trace.query).slice(0, 300);
    if (Array.isArray(trace.queries) && trace.queries.length) {
      rec.queries = trace.queries.filter((q) => typeof q === "string").slice(0, 8).map((q) => q.slice(0, 400));
    }
    if (trace.url) rec.url = String(trace.url).slice(0, 2000);
    if (trace.error) rec.error = String(trace.error).slice(0, 400);
    if (trace.latency_ms != null && Number.isFinite(Number(trace.latency_ms))) rec.latency_ms = Number(trace.latency_ms);
    for (const key of ["duration_ms", "elapsed_ms", "started_ms", "timeout_ms", "call_duration_ms", "execution_duration_ms", "agent_work_ms", "offset", "limit", "max_matches"]) {
      if (trace[key] != null && Number.isFinite(Number(trace[key]))) rec[key] = Number(trace[key]);
    }
    if (trace.title) rec.title = String(trace.title).slice(0, 80);
    if (trace.source) rec.source = String(trace.source).slice(0, 16);
    if (trace.event_count != null && Number.isFinite(Number(trace.event_count))) rec.event_count = Number(trace.event_count);
    if (trace.identifier) rec.identifier = String(trace.identifier).slice(0, 64);
    if (trace.visibility === "public" || trace.visibility === "private") rec.visibility = trace.visibility;
    if (trace.version != null && Number.isFinite(Number(trace.version))) rec.version = Number(trace.version);
    if (trace.icon) rec.icon = String(trace.icon).slice(0, 16);
    if (trace.format === "html" || trace.format === "markdown") rec.format = trace.format;
    if (trace.host) rec.host = String(trace.host).slice(0, 40);
    if (trace.path) rec.path = String(trace.path).slice(0, 400);
    if (trace.cwd) rec.cwd = String(trace.cwd).slice(0, 400);
    if (trace.command) rec.command = String(trace.command).slice(0, 8000);
    for (const key of ["project_root", "platform", "include"]) if (trace[key]) rec[key] = String(trace[key]).slice(0, key === "include" ? 200 : 400);
    if (trace.pattern) rec.pattern = String(trace.pattern).slice(0, 200);
    if (trace.preview) rec.preview = String(trace.preview).slice(0, 800);
    const image = ui.toolImage(trace);
    if (image) rec.image = image;
    if (trace.child_id) rec.child_id = String(trace.child_id).slice(0, 80);
    if (trace.background) rec.background = true;
    if (trace.summary) rec.summary = String(trace.summary).slice(0, 2000);
    if (trace.description) rec.description = String(trace.description).slice(0, 200);
    if (trace.subagent_type) rec.subagent_type = String(trace.subagent_type).slice(0, 32);
    for (const key of ["bytes", "http_status", "agent_tool_uses", "agent_tokens"]) {
      const n = Number(trace[key]);
      if (trace[key] != null && Number.isInteger(n) && n >= 0) rec[key] = n;
    }
    if (Array.isArray(trace.todos)) rec.todos = transcript.normalizeTodos(trace.todos);
    if (trace.exit_code != null && Number.isFinite(Number(trace.exit_code))) rec.exit_code = Number(trace.exit_code);
    EXECUTION_FIELDS.forEach(key => { if (Object.prototype.hasOwnProperty.call(trace, key)) rec[key] = trace[key]; });
    if (Array.isArray(trace.results)) {
      rec.results = trace.results.slice(0, 24).map((row) => ({
        title: String((row && row.title) || "").slice(0, 200),
        url: String((row && row.url) || "").slice(0, 2000),
        snippet: String((row && row.snippet) || "").slice(0, 400),
        query: String((row && row.query) || "").slice(0, 400),
      }));
    }
    return rec;
  }

    function mergeSubagentWait(msg, event, status) {
    if (event.name !== "get_subagent_output" || !event.child_id) return false;
    const existing = (msg.tools || []).find((t) => t.name === "get_subagent_output" && t.child_id === event.child_id && (!event.id || t.id === event.id));
    if (!existing) return false;
    if ((status === "running" || status === "progress") && (existing.status === "done" || existing.status === "error")) return true;
    const merged = mergeToolEvent(existing, event, status);
    const idx = msg.tools.indexOf(existing);
    if (idx >= 0) msg.tools[idx] = merged;
    return true;
  }

    function mergeToolEvent(prev, event, status) {
    if (prev && !executionPending(prev) && prev.id === event.id && ["done", "error", "failed", "interrupted", "cancelled"].includes(prev.status) &&
        ["running", "progress", "queued"].includes(status || event.status)) return { ...prev };
    const rec = prev && typeof prev === "object" ? { ...prev } : {};
    rec.id = event.id || rec.id || "";
    rec.name = event.name || rec.name || "unknown";
    rec.status = status || event.status || rec.status || "done";
    if (prev?.status === "done" && executionPending(prev) && /^get_(command|subagent)_output$/.test(prev.name) && rec.status === "running") rec.status = "done";
    rec.results = Array.isArray(event.results) ? event.results : rec.results || [];
    const fields = [
      "query",
      "url",
      "title",
      "identifier",
      "visibility",
      "icon",
      "format",
      "error",
      "host",
      "path",
      "command",
      "cwd",
      "preview",
      "summary",
      "child_id",
      "description",
      "pattern",
      "background",
      "output_truncated",
      "image",
      "project_root", "platform", "include",
      "source",
    ];
    fields.forEach((key) => {
      if (event[key] != null && event[key] !== "") rec[key] = event[key];
    });
    // A settled join carries no preview of its own, and the "still running"
    // it was born with is a placeholder, not a result. Left in place it
    // outlives the child and the row keeps saying the child is running.
    if (/^get_(command|subagent)_output$/.test(rec.name) && !["running", "progress", "queued"].includes(rec.status)
        && rec.preview === "still running" && event.preview == null) {
      rec.preview = "";
    }
    EXECUTION_FIELDS.forEach(key => { if (Object.prototype.hasOwnProperty.call(event, key)) rec[key] = event[key]; });
    if (Object.prototype.hasOwnProperty.call(event, "exit_code")) rec.exit_code = event.exit_code;
    if (Array.isArray(event.queries)) rec.queries = event.queries;
    else if (!Array.isArray(rec.queries)) rec.queries = [];
    if (Array.isArray(event.todos)) rec.todos = transcript.normalizeTodos(event.todos);
    if (event.version != null) rec.version = event.version;
    if (event.latency_ms != null) rec.latency_ms = event.latency_ms;
    for (const key of ["duration_ms", "elapsed_ms", "started_ms", "timeout_ms", "call_duration_ms", "execution_duration_ms", "agent_work_ms", "offset", "limit", "max_matches"]) {
      if (event[key] != null) rec[key] = event[key];
    }
    if (event.exit_code != null) rec.exit_code = event.exit_code;
    if (event.event_count != null) rec.event_count = event.event_count;
    return rec;
  }

    function serialMessages() {
    if (S.partialTranscript) throw new Error("This chat is still loading. Try again in a moment.");
    return S.messages
      .filter((msg) => msg && (msg.role === "user" || msg.role === "assistant"))
      .map((msg) => {
        const row = {
          id: msg.id || (msg.id = newSession()),
          role: msg.role,
          content: msg.content || "",
          reasoning: msg.reasoning || "",
          error: msg.error || "",
          usage: msg.usage || null,
          tools: Array.isArray(msg.tools) ? msg.tools.map(persistToolTrace).filter(Boolean) : [],
          notice: msg.notice || "",
        };
        if (Array.isArray(msg.segments)) row.segments = msg.segments.map((part) => ({ ...part }));
        if (msg.role === "assistant" && msg.elapsedMs != null) row.elapsedMs = msg.elapsedMs;
        if (msg.role === "assistant" && msg.endedAt) row.endedAt = msg.endedAt;
        if (msg.role === "assistant" && msg.model) row.model = msg.model;
        if (msg.role === "assistant" && msg.stopped) row.stopped = true;
        if (msg.role === "user" && msg.auto) row.auto = true;
        if (Array.isArray(msg.attachments) && msg.attachments.length) row.attachments = msg.attachments;
        return row;
      });
  }

    function tracesSig(traces) {
    // Output, child identity, exit code and background transitions also change UI.
    // A finished call's elapsed clock is not part of that: ticking it rebuilt
    // every settled row and replayed the expand animation on each update.
    return JSON.stringify((traces || []).map((trace) => {
      if (!trace || typeof trace !== "object") return trace;
      const presentation = toolPresentation(trace, true);
      if (presentation.running) return trace;
      const { elapsed_ms, ...rest } = trace;
      return rest;
    }));
  }

    // Every glyph that steps: the docks, the latest pill, and the sidebar's
  // run spinners (thread rows, Chats/Projects tabs, heading and title).

  function runForMessage(msg) {
    if (!msg) return null;
    if (S.activeRun && msg.id === S.activeRun.message_id) return S.activeRun;
    if (S.agentRun && msg.id === S.agentRun.message_id) return S.agentRun;
    return null;
  }

    // A summary line's membership shifts while it streams: the newest call (or
  // a running one) prints on its own row until something newer arrives, then
  // folds back into the line. A fold or open must therefore stamp the flag on
  // every call that can still join the line — the members the line holds now
  // plus the foldable rows beside it in the same block — or the returning
  // call's stale flag would re-open (or re-close) the line behind the user's
  // back. Foldability is asked of the same classifier the grouper uses.

  // The bar's closures capture `index`, and Retry exists only on the last
  // message, so rebuild whenever either changes — a reused article otherwise
  // keeps a Retry that truncates every later turn.


  function articleFor(msg) {
    if (!msg || !msg.id) return null;
    return [...S.log.children].find((el) => el.dataset.messageId === msg.id) || null;
  }

    // A background call whose job/agent row no longer exists can never report
  // back; left alone it renders Running forever and keeps its poll alive.
  // Only judged while no run is live (a fresh call's row is written first).
  function settleOrphanTraces(names, rows) {
    if (runIsActive(S.activeRun)) return;
    const known = new Set(rows.map((row) => String(row.command_id || row.child_id || row.run_id || row.id)));
    let changed = false;
    S.messages.forEach((msg) => {
      if (msg.role !== "assistant" || !Array.isArray(msg.tools)) return;
      msg.tools.forEach((trace) => {
        if (!trace || !names.includes(trace.name) || !executionIdentity(trace) || known.has(String(executionIdentity(trace)))) return;
        if (!(executionPending(trace) || ["running", "progress", "queued"].includes(trace.status))) return;
        trace.status = "interrupted";
        trace.state = trace.execution_status = "interrupted";
        trace.error = trace.error || "No longer tracked by the server.";
        if (trace.preview === "still running") trace.preview = "";
        changed = true;
      });
    });
    if (!changed) return;
    S.messages.forEach((msg) => {
      const article = msg.id ? articleFor(msg) : null;
      if (article && msg.role === "assistant") ui.updateAssistant(article, msg, false);
    });
  }

    function hasRunningBgTraces() {
    return S.messages.some(
      (msg) =>
        Array.isArray(msg.tools) &&
        msg.tools.some(
          (t) => t && (["running", "progress", "queued"].includes(t.status) || executionPending(t)) && executionIdentity(t) && isShellExecTool(t.name)
        )
    );
  }

    function reconcileBgTraces(recent, repaint = true) {
    if (!recent.length) return;
    const done = new Map(recent.map((job) => [String(job.command_id || job.id || job.child_id), job]));
    let changed = false;
    const targets = [...S.messages, ...(S.agentRun?.messages || []), ...(S.agentRun?.message ? [S.agentRun.message] : [])];
    targets.forEach((msg) => {
      if (msg.role !== "assistant" || !Array.isArray(msg.tools)) return;
      msg.tools.forEach((trace) => {
        if (!trace || (!executionPending(trace) && !["running", "progress", "queued"].includes(trace.status)) || !executionIdentity(trace)) return;
        if (!["run_terminal_command", "monitor"].includes(trace.name)) return;
        const job = done.get(String(executionIdentity(trace)));
        if (!job || !["completed", "timed_out", "failed", "error", "cancelled", "interrupted", "lost"].includes(job.state) &&
            !(["running", "cancelling"].includes(job.state) && typeof job.output === "string")) return;
        applyCommandResult(trace, {
          ...job,
          state: job.state === "completed" ? "completed" : job.state,
          exit_code: job.exit_code != null && Number.isFinite(Number(job.exit_code)) ? Number(job.exit_code) : null,
          duration_ms: job.duration_ms != null && Number.isFinite(Number(job.duration_ms)) ? Number(job.duration_ms) : null,
          error: job.error || "",
          output: typeof job.output === "string" ? job.output : job.out || "",
        });
        changed = true;
      });
    });
    if (changed && repaint) {
      if (S.agentsOpen && S.agentRun) agents.scheduleAgentView();
      const articles = new Map([...S.log.children].map((el) => [el.dataset.messageId, el]));
      S.messages.forEach((msg) => {
        const article = msg.id ? articles.get(msg.id) : null;
        if (article && msg.role === "assistant") {
          ui.updateAssistant(article, msg, messageRunLive(msg));
        }
      });
    }
  }

  return {
    articleFor,
    hasRunningBgTraces,
    mergeSubagentWait,
    mergeToolEvent,
    persistToolTrace,
    reconcileAgentTraces,
    reconcileBgTraces,
    runForMessage,
    serialMessages,
    settleOrphanTraces,
    toolAwaitingCancellation,
    toolCounts,
    toolInterrupted,
    toolPresentation,
    toolRunning,
    tracesSig,
    lastCurrentTrace,
  };
}

export { lastCurrentTrace };
