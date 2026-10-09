// Pure tool-trace semantics: execution state, exit codes, command parsing. No runtime state; every function is a direct export so the node suites exercise the real code.
function mapAgentToolStatus(status) {
  if (status === "completed" || status === "done") return "done";
  if (status === "cancelled" || status === "interrupted" || status === "lost") return "interrupted";
  if (status === "failed" || status === "error" || status === "timed_out") return "error";
  if (status === "queued") return "queued";
  if (status === "running" || status === "progress" || status === "cancelling") return "running";
  return status ? "interrupted" : "";
}

function childBackedTool(name) {
  return name === "spawn_subagent" || name === "get_subagent_output" || name === "run_terminal_command" || name === "get_command_output" || name === "monitor";
}

function executionIdentity(trace) {
  return trace?.command_id || trace?.child_id || trace?.job_id || "";
}

const EXECUTION_FIELDS = ["command_id", "job_id", "state", "execution_status", "output", "output_chars", "output_chars_exact",
  "output_retained_chars", "truncated", "output_mode", "output_start", "termination_confirmed", "output_bytes"];

function executionState(trace) {
  // A kill receipt never owns an execution; its target's row shows the stop.
  if (trace?.name === "kill_command") return "";
  return trace?.state || trace?.execution_status || "";
}

function executionPending(trace) {
  return ["running", "queued", "cancelling"].includes(executionState(trace));
}

function toolIsCurrent(trace) {
  if (!trace) return false;
  if (["completed", "done", "failed", "error", "timed_out", "cancelled", "interrupted", "lost"].includes(executionState(trace))) return false;
  if (trace.status !== "running" && trace.status !== "progress" && trace.status !== "queued") return false;
  if (toolBackground(trace) && executionIdentity(trace) && trace.name !== "get_command_output" && trace.name !== "get_subagent_output") return false;
  return true;
}

function isShellExecTool(name) {
  return name === "run_terminal_command" || name === "get_command_output" || name === "kill_command" || name === "monitor";
}

// Output whose columns mean something (shell, matches, listings): it keeps
// its lines and scrolls sideways instead of wrapping mid-token.
function isCodeOutputTool(name) {
  return isShellExecTool(name) || ["grep", "glob", "list_dir", "read_file"].includes(name);
}

function shellExitOnly(trace) {
  // A relayed exit code is command status, not a tool failure. Traces written
  // before this distinction marked non-zero exits as errors; render those as
  // completed commands too.
  if (!trace || !isShellExecTool(trace.name)) return false;
  if (!Number.isFinite(trace.exit_code)) return false;
  const err = String(trace.error || "").trim();
  return err === "" || /^exit -?\d+$/i.test(err) || /^Command exited with code -?\d+$/i.test(err);
}

function shellExitCode(trace) {
  if (!trace || !isShellExecTool(trace.name)) return null;
  return Number.isFinite(trace.exit_code) ? trace.exit_code : null;
}

function toolFailed(trace) {
  if (!trace) return false;
  if (["completed", "done"].includes(executionState(trace))) return false;
  if (trace.status !== "error" && trace.status !== "failed") return false;
  return !shellExitOnly(trace);
}

// One presentation state for standalone tools, grouped children, and summaries.
function toolBackground(trace) {
  return Boolean(trace.background || (trace.name === "spawn_subagent" && trace.summary === "Background task started"));
}

function toolKey(trace, index) {
  return `tool-${trace.id || index}`;
}

function countsLabel(counts) {
  const active = counts.phases ? Object.entries(counts.phases).map(([label, n]) => `${n} ${label}`) : counts.running ? [`${counts.running} running`] : [];
  return [...active, ...[["done", "done"], ["failed", "failed"], ["stopped", "stopped"], ["warning", "nonzero exit"]]
    .filter(([key]) => counts[key])
    .map(([key, label]) => `${counts[key]} ${label}${key === "warning" && counts[key] > 1 ? "s" : ""}`)].join(" · ");
}

function runIsActive(run) {
  return Boolean(run && ["queued", "running"].includes(run.status));
}

function autoEventLabel(content) {
  const text = String(content || "");
  if (text.includes("SCHEDULED RUN")) return "Automated · scheduled run";
  // Envelopes always carry an id attribute; the handoff boilerplate names
  // the bare tags in prose and must not classify the message.
  const commands = text.includes("<command_result id=");
  const monitors = text.includes("<monitor_event id=") || text.includes("<monitor_result id=");
  const reports = text.includes("<task id=");
  if ((commands || monitors) && reports) return "Automated · background results";
  if (monitors && commands) return "Automated · background results";
  if (monitors) return "Automated · monitor event";
  if (commands) return "Automated · background command result";
  if (reports) return "Automated · background subagent report";
  return "Automated · background event";
}

function parseCommandResults(text) {
  const results = [];
  const re = /<(command_result|monitor_result|monitor_event) id="([^"]+)"([^>]*)>([\s\S]*?)<\/\1>/g;
  let match;
  while ((match = re.exec(String(text || "")))) {
    const tag = match[1];
    const attrs = match[3] || "";
    const body = match[4] || "";
    const split = body.indexOf("\n\n");
    const meta = split >= 0 ? body.slice(0, split) : body;
    const output = split >= 0 ? body.slice(split + 2) : "";
    const pick = (key) => {
      const line = meta.split("\n").find((l) => l.startsWith(key + ":"));
      return line ? line.slice(key.length + 1).trim() : "";
    };
    const attr = (key) => {
      const hit = attrs.match(new RegExp(key + '="([^"]*)"'));
      return hit ? hit[1] : "";
    };
    const code = pick("exit_code");
    const duration = Number(pick("duration_ms"));
  const timeout = Number(pick("timeout_ms"));
    const rawLine = meta.split("\n").find((l) => l.startsWith("host:")) || "";
    const hm = rawLine.match(/^host:\s*(.*?)\s*(?:command|source):\s*(.*)$/);
    const target = pick("target") || (hm ? hm[2].trim() : pick("command"));
    const events = Number(pick("events"));
    const counted = Number.isFinite(events) ? events : Number(attr("count"));
    results.push({
      id: match[2],
      tag,
      state: attr("state") || (tag === "monitor_event" ? "running" : ""),
      host: hm ? hm[1].trim() : pick("host"),
      command: target,
      url: pick("url"),
      source: pick("source"),
      event_count: Number.isFinite(counted) ? counted : null,
      exit_code: /^-?\d+$/.test(code) ? Number(code) : null,
      duration_ms: Number.isFinite(duration) ? duration : null,
      timeout_ms: Number.isFinite(timeout) && timeout > 0 ? timeout : null,
      error: pick("error"),
      description: pick("description"),
      output: output.trim(),
      monitor: tag !== "command_result",
    });
  }
  return results;
}

const SETTLED_TOOL_STATUS = new Set(["done", "error", "interrupted"]);

function applyCommandResult(trace, result) {
  if (["get_command_output", "kill_command"].includes(trace.name)) return;
  // Legacy wake envelopes must not replace an authoritative retained-output
  // record with a shorter text preview or discard its provenance; how the
  // command ended still applies, or its row would say it runs forever.
  const legacy = typeof trace.output === "string" && result.output_mode == null && result.output_chars == null;
  if (["running", "cancelling"].includes(result.state)) {
    if (!SETTLED_TOOL_STATUS.has(trace.status)) trace.status = "running";
  } else if (result.state === "completed") {
    trace.status = "done";
    if (result.exit_code !== null) trace.exit_code = result.exit_code;
    else delete trace.exit_code;
    trace.error = "";
  } else {
    trace.status = ["cancelled", "interrupted", "lost"].includes(result.state) ? "interrupted" : "error";
    delete trace.exit_code;
    trace.error = result.error || result.state;
  }
  if (result.duration_ms !== null) trace.latency_ms = result.duration_ms;
  if (result.state) {
    trace.state = result.state;
    trace.execution_status = result.state;
  }
  if (legacy) return;
  if (!trace.output && result.output) trace.output = result.output;
  if (result.output_mode != null || result.output_chars != null) {
    EXECUTION_FIELDS.forEach(key => { if (Object.prototype.hasOwnProperty.call(result, key)) trace[key] = result[key]; });
    for (const key of ["host", "cwd", "command", "duration_ms", "execution_duration_ms", "timeout_ms", "elapsed_ms", "background", "event_count", "source", "url"]) {
      if (Object.prototype.hasOwnProperty.call(result, key)) trace[key] = result[key];
    }
    if (result.child_id) trace.child_id = result.child_id;
    trace.execution_status = result.state;
    trace.error = result.error || "";
    return;
  }
  const out = result.output || (result.state === "completed" ? "(no output)" : result.error || result.state);
  trace.preview = String(out).slice(0, 800);
  trace.summary = String(out).slice(0, 4000);
  trace.output_truncated = String(out).length > 4000;
}

function wakeStepTrace(result) {
  const monitor = Boolean(result.monitor);
  const liveEvent = result.tag === "monitor_event";
  const trace = {
    name: monitor ? "monitor" : "run_terminal_command",
    status: liveEvent ? "running" : result.state === "completed" ? "done" : "error",
    host: result.host,
    command: result.command || result.id,
    url: result.url || "",
    source: result.source || "",
    event_count: result.event_count,
    child_id: result.id,
    description: result.description || "",
    background: true,
    backgroundResult: true,
    exit_code: result.exit_code,
    latency_ms: result.duration_ms,
    preview: (result.output || result.error || result.state || "").slice(0, 800),
    error: result.state === "completed" || liveEvent ? "" : result.error || result.state,
  };
  applyCommandResult(trace, result);
  if (liveEvent) {
    trace.status = "running";
    trace.state = "running";
  }
  return trace;
}

// Background results in delivery order: subagent reports (<task>), finished
// commands and monitor events. One parser serves a wake turn's message and a
// mid-reply `delivery` segment, so both render the same rows. A report may
// quote "</task>" itself, so a task ends at its own "</task>" + "task_id: <id>"
// line when present (app/chat_generation/inbox.envelope_items does the same).
function envelopeItems(text) {
  const source = String(text || "");
  const start = /<(task|command_result|monitor_event|monitor_result) id="([^"]*)"[^>]*>/g;
  const out = [];
  let match;
  while ((match = start.exec(source))) {
    const [opening, tag, id] = match;
    const close = `</${tag}>`;
    const from = match.index + opening.length;
    let end = -1;
    if (tag === "task") {
      const anchor = source.indexOf(`${close}\ntask_id: ${id}`, from);
      if (anchor >= 0) {
        const lineEnd = source.indexOf("\n", anchor + close.length + 1);
        end = lineEnd < 0 ? source.length : lineEnd;
      }
    }
    if (end < 0) {
      const found = source.indexOf(close, from);
      if (found < 0) break;
      end = found + close.length;
    }
    out.push({ tag, id, text: source.slice(match.index, end) });
    start.lastIndex = end;
  }
  return out;
}

function parseTaskReport(item) {
  const text = item.text;
  const attr = (text.match(/^<task [^>]*\bstate="([^"]*)"/) || [])[1] || "";
  const description = ((text.match(/<summary>([\s\S]*?)<\/summary>/) || [])[1] || "").trim();
  const body = text.match(/<(task_result|task_error)>\n?([\s\S]*?)\n?<\/\1>/);
  // Older envelopes carry the report bare inside <task>.
  const bare = () => text.replace(/^<task [^>]*>/, "").replace(/<\/task>[\s\S]*$/, "")
    .replace(/<(summary|task_generation)>[\s\S]*?<\/\1>/g, "").trim();
  return { id: item.id, state: attr, description, report: body ? body[2].trim() : bare(), failed: Boolean(body && body[1] === "task_error") };
}

// A delivered subagent report, as a row: the child's id opens its pane.
function reportStepTrace(report) {
  const state = report.state || "completed";
  const stopped = ["cancelled", "interrupted"].includes(state);
  const failed = !stopped && (report.failed || state === "error");
  return {
    name: "spawn_subagent",
    agentReport: true,
    backgroundResult: true,
    child_id: report.id,
    description: report.description,
    state,
    status: stopped ? "interrupted" : failed ? "error" : "done",
    summary: report.report,
    error: failed ? (report.report.split("\n").find((line) => line.trim()) || "failed").trim() : "",
  };
}

function deliveryItems(text) {
  return envelopeItems(text).map((item) => item.tag === "task"
    ? { kind: "task", report: parseTaskReport(item) }
    : { kind: "command", result: parseCommandResults(item.text)[0] }).filter((item) => item.report || item.result);
}

function deliveryTraces(text) {
  return deliveryItems(text).map((item) => item.kind === "task" ? reportStepTrace(item.report) : wakeStepTrace(item.result));
}

function foldAutoCommandResults(rows) {
  rows.forEach((msg, index) => {
    if (msg.role !== "user" || !msg.auto) return;
    const text = String(msg.content || "");
    const items = deliveryItems(text);
    if (!items.length) {
      msg.autoSteps = null;
      return;
    }
    msg.autoSteps = items.map((item) => item.kind === "task" ? reportStepTrace(item.report) : wakeStepTrace(item.result));
    const results = items.map((item) => item.kind === "command" ? item.result : null);
    const findTrace = (id) => {
      for (const name of ["run_terminal_command", "get_command_output", "monitor"]) {
        for (let i = 0; i < index; i++) {
          const prev = rows[i];
          if (prev.role !== "assistant" || !Array.isArray(prev.tools)) continue;
          const hit = prev.tools.find((t) => t && t.name === name && String(executionIdentity(t)) === id);
          if (hit) return hit;
        }
      }
      return null;
    };
    results.forEach((result, i) => {
      if (!result) return;
      const trace = findTrace(result.id);
      if (!trace) return;
      applyCommandResult(trace, result);
      if (trace.description && msg.autoSteps?.[i]) msg.autoSteps[i].description = trace.description;
    });
  });
}

export { EXECUTION_FIELDS, SETTLED_TOOL_STATUS, applyCommandResult, autoEventLabel, childBackedTool, countsLabel, deliveryTraces, envelopeItems, executionIdentity, executionPending, executionState, foldAutoCommandResults, isCodeOutputTool, isShellExecTool, mapAgentToolStatus, parseCommandResults, runIsActive, shellExitCode, shellExitOnly, toolBackground, toolFailed, toolIsCurrent, toolKey, wakeStepTrace };
