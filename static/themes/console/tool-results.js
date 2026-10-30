// tool-results presentation. Dependencies are live getters supplied by index.js.
export function create(deps) {
function consoleDiffRows(text) {
    // A trailing newline is the end of the last line, not an empty row.
    const lines = String(text || "").replace(/\n+$/, "").split("\n");
    // A multi-file diff names each file as it changes; without the labels the
    // stacked hunks read as one file with out-of-order line numbers.
    const fileName = (line) => (line.match(/^diff --git a\/\S+ b\/(.+)$/) || line.match(/^\+\+\+ b\/(.+)$/) || [])[1];
    let oldN = 0;
    let newN = 0;
    let oldLeft = 0;
    let newLeft = 0;
    let file = "";
    const rows = [];
    lines.forEach((line) => {
      const inHunk = oldLeft > 0 || newLeft > 0;
      const named = !inHunk && fileName(line);
      if (named && named !== "/dev/null") {
        if (named !== file) rows.push({ kind: "file", text: named });
        file = named;
        return;
      }
      const hunk = line.match(/^@@\s+-(\d+)(?:,(\d+))?\s+\+(\d+)(?:,(\d+))?/);
      if (hunk) {
        oldN = Number(hunk[1]);
        newN = Number(hunk[3]);
        oldLeft = Number(hunk[2] ?? 1);
        newLeft = Number(hunk[4] ?? 1);
        return;
      }
      if (/^(diff |index |\\ )/.test(line) || (!inHunk && /^(--- |\+\+\+ )/.test(line))) return;
      // Blank lines carry no change worth reading: they keep their place in
      // the numbering but print no row. Removed lines are numbered in the old
      // file; added and unchanged lines in the new one, as Claude Code does.
      const kind = line.startsWith("-") ? "del" : line.startsWith("+") ? "add" : "ctx";
      const text = kind === "ctx" && !line.startsWith(" ") ? line : line.slice(1);
      const n = kind === "del" ? oldN : newN;
      if (kind !== "add") { oldN += 1; oldLeft = Math.max(0, oldLeft - 1); }
      if (kind !== "del") { newN += 1; newLeft = Math.max(0, newLeft - 1); }
      rows.push({ n, kind, text });
    });
    return rows;
  }

function consoleDiffHtml(text, { limit, numbers = true } = {}) {
    const parsed = consoleDiffRows(text);
    const multi = new Set(parsed.filter((row) => row.kind === "file").map((row) => row.text)).size > 1;
    const rows = parsed.filter((row) => row.kind === "file" ? multi : row.text.trim());
    if (!rows.length) return "";
    const cap = Number.isFinite(limit) ? Math.min(limit, rows.length) : rows.length;
    const width = String(Math.max(1, ...rows.filter((row) => row.kind !== "file").map((row) => row.n))).length;
    const table = rows.slice(0, cap).map((row) => {
      if (row.kind === "file") return `<div class="chat-console-nline is-file"><span class="chat-console-nbody">${deps.escapeHtml(row.text)}</span></div>`;
      const mark = row.kind === "del" ? "-" : row.kind === "add" ? "+" : " ";
      const gutter = numbers ? `<span class="chat-console-n">${String(row.n).padStart(width, " ")}</span>` : "";
      return `<div class="chat-console-nline is-${row.kind}${numbers ? "" : " is-bare"}">${gutter}<span class="chat-console-mark">${mark}</span><span class="chat-console-nbody">${deps.escapeHtml(row.text)}</span></div>`;
    }).join("");
    const extra = rows.length > cap
      ? `<div class="chat-console-more">… +${rows.length - cap} lines</div>`
      : "";
    return `<div class="chat-console-lines is-diff">${table}</div>${extra}`;
  }

// The first prose line of a skill body (after its "# Title"), skipping
// headings, lists, fences and the load preamble above the title.
function skillBodyLead(text) {
    const body = String(text || "");
    const start = body.search(/^#\s/m);
    if (start < 0) return "";
    const line = body.slice(start).split("\n").map((row) => row.trim())
      .find((row) => row && !/^(#|[-*+>|]|\d+[.)]\s|```|~~~|<)/.test(row));
    return line ? deps.ccFlat(line) : "";
  }

function consoleFileBody(trace) {
    if (typeof trace.output === "string" && trace.output) return deps.cleanToolOutput(trace, trace.output);
    const preview = String(trace.preview || "").trim();
    if (preview && preview !== "still running" && preview !== "background task started" && !deps.looksLikeDiff(preview)) return preview;
    return "";
  }

function consoleChangeText(added, removed) {
    const lines = (n) => `${n} ${n === 1 ? "line" : "lines"}`;
    if (added > 0 && removed > 0) return `Added ${lines(added)}, removed ${lines(removed)}`;
    if (added > 0) return `Added ${lines(added)}`;
    if (removed > 0) return `Removed ${lines(removed)}`;
    return "";
  }

function consoleDiffCounts(diff) {
    const rows = consoleDiffRows(diff);
    return {
      added: rows.filter((row) => row.kind === "add").length,
      removed: rows.filter((row) => row.kind === "del").length,
    };
  }

function consoleStdoutHtml(lines) {
    return `<div class="chat-console-lines is-stdout">${lines.map((line) => `<div class="chat-console-stdout">${deps.renderAnsi(line) || " "}</div>`).join("")}</div>`;
  }

function consoleTruncationNote(trace) {
    if (typeof trace?.output !== "string") return "";
    const retained = Array.from(trace.output).length;
    const total = Number.isFinite(trace.output_chars) ? trace.output_chars : retained;
    let note = "";
    if (trace.output_mode === "partial" && (trace.truncated || total > retained)) note = `Partial output · ${retained.toLocaleString()} characters retained · total ${trace.output_chars_exact === true ? total.toLocaleString() : `unknown (at least ${total.toLocaleString()})`}.`;
    else if (trace.output_mode === "tail") note = `Retained tail · ${retained.toLocaleString()} of ${trace.output_chars_exact === false ? "at least " : ""}${total.toLocaleString()} characters${Number.isFinite(trace.output_start) ? ` · starts at offset ${trace.output_start.toLocaleString()}` : ""}.`;
    else if (trace.truncated || total > retained) note = `Output truncated · ${retained.toLocaleString()} of ${trace.output_chars_exact === false ? "at least " : ""}${total.toLocaleString()} characters retained.`;
    return note ? `<span class="chat-tool-truncated">${deps.escapeHtml(note)}</span>` : "";
  }

function ccTimeoutNote(trace) {
    let ms = Number(trace && trace.timeout_ms);
    if (!(Number.isFinite(ms) && ms > 0) && trace && trace.name === "get_command_output") {
      ms = Number(deps.commandOrigin(trace)?.timeout_ms);
    }
    if (!Number.isFinite(ms) || ms <= 0) return "";
    return `(timeout: ${deps.ccTimeoutText(ms)})`;
  }

function consoleSameText(html, text) {
    const plain = String(html || "").replace(/<[^>]*>/g, " ").replace(/&[a-z#0-9]+;/gi, " ").replace(/\s+/g, " ").trim();
    return (plain === String(text || "").replace(/\s+/g, " ").trim());
  }

function consoleFold(trace, presentation, verbose) {
    if (trace?.name === "read_file" && trace.image && !presentation.running && !presentation.failed) {
      // An interrupted image read keeps its preview but says why, like every
      // other family; a success still reads as the image preview.
      return { status: presentation.interrupted ? deps.ccInterruptedText(trace) : "Image preview", preview: deps.toolImageHtml(trace), full: "" };
    }
    {
      const own = deps.consoleOnlyFold(trace, presentation, verbose);
      if (own) return own;
    }
    const { running, failed } = presentation;
    const name = trace && trace.name;
    const shell = name === "run_terminal_command" || name === "get_command_output";
    if (verbose && running && shell && !deps.toolBackground(trace)) {
      const out = consoleDetailHtml(trace, failed, running);
      return { status: "Running…", preview: "", full: consoleSameText(out, "Running…") ? "" : out };
    }
    const status = consoleResultLine(trace, presentation);
    const detail = () => {
      const html = consoleDetailHtml(trace, failed, running);
      return consoleSameText(html, status) ? "" : html;
    };
    if (running || (failed && !shell)) {
      if (shell) {
        const html = detail();
        const shown = [String(trace.command || "").trim(), `$ ${String(deps.displayCommand(trace) || "").trim()}`, "Running…"];
        return { status, preview: "", full: html && !shown.some((text) => consoleSameText(html, text)) ? html : "" };
      }
      return { status, preview: "", full: detail() };
    }
    if (name === "read_file") {
      const body = consoleFileBody(trace);
      if (!body) return { status, preview: "", full: "" };
      const note = consoleTruncationNote(trace);
      return { status, preview: `<div class="chat-console-out is-file">${deps.fileBodyHtml(trace, body, 10)}</div>${note}`, full: "", keep: Boolean(note) };
    }
    if (name === "kill_command" || name === "kill_subagent") return { status, preview: "", full: "" };
    if (name === "search_replace" || name === "write_file") {
      const diff = [trace.preview, trace.output].find((value) => deps.looksLikeDiff(value));
      if (diff) {
        const counted = consoleDiffCounts(diff);
        const added = Number(trace.added ?? trace.lines_added);
        const removed = Number(trace.removed ?? trace.lines_removed);
        const text = consoleChangeText(Number.isFinite(added) ? added : counted.added, Number.isFinite(removed) ? removed : counted.removed);
        return { status: text || (name === "write_file" ? status : "Updated"), preview: consoleDiffHtml(diff, { numbers: name !== "search_replace" || /^@@\s+-\d+/.test(diff) }), full: "" };
      }
      if (name === "search_replace") return { status, preview: "", full: "" };
      const body = typeof trace.content === "string" ? trace.content.replace(/\s+$/, "") : "";
      if (!body) return { status, preview: "", full: "" };
      const total = body.split("\n").length;
      return {
        status,
        preview: deps.fileBodyHtml(trace, body, 10),
        full: total > 10 ? `<div class="chat-console-out is-file">${deps.fileBodyHtml(trace, body)}</div>` : "",
      };
    }
    if (shell) {
      // A failure prints its error once (ccFailure), then any output of its own.
      const failure = failed ? deps.ccFailure(trace) : null;
      const raw = failure ? failure.output : typeof trace.output === "string" ? deps.cleanToolOutput(trace, trace.output)
        : [trace.preview, trace.summary].find((value) => { const text = String(value || "").trim(); return text && text !== "still running" && text !== "background task started"; }) || "";
      // Leading blank lines too, as ccFailure does (PowerShell tables open with them).
      const lines = String(raw).replace(/\s+$/, "").replace(/^\s*\n/, "").split("\n");
      const empty = !String(raw).trim();
      const exit = deps.shellExitCode(trace);
      // A non-zero exit prints its code above the output, as Claude Code does.
      const nonzero = exit !== null && exit !== 0;
      // A Wait that returned before its command ended says so above the
      // output. Its saved state is the command's at that moment: "Still
      // running" only while the command still runs, else what happened then.
      const wait = name === "get_command_output";
      const origin = wait && deps.executionState(trace) === "running" ? deps.commandOrigin(trace) : null;
      const stillRunning = !origin || deps.executionPending(origin) || deps.toolPresentation(origin, true).running;
      // A failure prints red with its "Error: " mark, like every other family
      // (ccErrorFold); an error that already carries a mark is kept verbatim.
      const reason = failure ? deps.providerErrorLine(deps.ccFlat(failure.error)) : status;
      const failedHead = failed && !/^(Error|Cancelled): /.test(reason) ? `Error: ${reason}` : reason;
      const head = nonzero ? `Exit code ${exit}` : failed ? failedHead
        : wait ? (deps.executionState(trace) !== "running" ? "" : stillRunning ? "Still running" : "Returned while the command was still running")
        : deps.toolBackground(trace) ? status : "";
      const note = typeof consoleTruncationNote === "function" ? consoleTruncationNote(trace) : "";
      if (empty) return { status: head || (note ? "" : "(No output)"), preview: note, full: "", keep: nonzero || Boolean(note) };
      const show = lines.length === 4 ? 4 : 3;
      const cut = lines.slice(0, show).map((line) => line.length > 400 ? line.slice(0, 399) + "…" : line);
      const hidden = lines.length - cut.length;
      const clipped = hidden > 0 || cut.some((line, i) => line !== lines[i]);
      return {
        status: head,
        keep: nonzero || Boolean(note),
        preview: consoleStdoutHtml(cut) + (hidden > 0 ? `<div class="chat-console-more" data-step-toggle>… +${hidden} ${hidden === 1 ? "line" : "lines"}</div>` : "") + note,
        full: clipped ? consoleDetailHtml(trace, false, running) : "",
      };
    }
    if (name === "grep" || name === "glob" || name === "list_dir") {
      const body = String(trace.output || trace.preview || "").trim();
      // The server's empty results are parenthesised: "(no matches)"
      // (grep, glob), "(no matching files)" (grep files_with_matches).
      const none = !body || /^\(?no (matches|matching files?|files?|entries)( found)?\)?\.?$/i.test(body);
      const n = none ? 0 : name === "list_dir" ? deps.consoleLineCount(trace) : deps.consoleMatchCount(trace);
      const unit = name === "list_dir" ? (n === 1 ? "entry" : "entries")
        : name === "grep" && /:\d+:/.test(body) ? (n === 1 ? "line" : "lines") : (n === 1 ? "file" : "files");
      // Content matches name their files too: "Found 12 lines across 3 files".
      const files = (name === "grep") && n && /:\d+:/.test(body)
        ? new Set(body.split("\n").map((line) => (line.match(/^(.*?):\d+:/) || [])[1]).filter(Boolean)).size : 0;
      const across = files ? ` across ${files} ${files === 1 ? "file" : "files"}` : "";
      return { status: `${name === "list_dir" ? "Listed" : "Found"} ${n} ${unit}${across}`, preview: "", full: n ? detail() : "" };
    }
    if (deps.isSearchTool(name)) {
      const n = deps.searchCountFor(trace);
      const ms = Number(trace.latency_ms ?? trace.duration_ms ?? trace.call_duration_ms);
      const took = Number.isFinite(ms) && ms > 0 ? ` in ${ms >= 1000 ? `${Math.round(ms / 1000)}s` : `${Math.round(ms)}ms`}` : "";
      const hits = !failed && trace.results?.length ? detail() : "";
      return { status: `Did ${n} search${n === 1 ? "" : "es"}${took}`, preview: "", full: hits };
    }
    return { status, preview: "", full: detail() };
  }

function consoleStatusHtml(text) {
    return deps.escapeHtml(text)
      .replace(/^(Read|Wrote|Added|Removed|Found|Listed) (\d[\d,]*)/, "$1 <b>$2</b>")
      .replace(/^(Received) (\S+)/, "$1 <b>$2</b>")
      .replace(/( across )(\d[\d,]*)/, "$1<b>$2</b>")
      .replace(/(, removed )(\d[\d,]*)/, "$1<b>$2</b>")
      .replace(/^(Wrote <b>[^<]*<\/b> lines? to )(.+)$/, "$1<b>$2</b>");
  }

function consoleDetailHtml(trace, failed, running) {
    if (trace?.name === "read_file" && trace.image && !failed && !running) return deps.toolImageHtml(trace);
    if (trace && ["read_file", "write_file"].includes(trace.name) && !failed) {
      const body = trace.name === "write_file" ? (typeof trace.content === "string" ? trace.content : "") : consoleFileBody(trace);
      if (!body) return "";
      return `<div class="chat-console-out is-file">${deps.fileBodyHtml(trace, body)}</div>${trace.name === "read_file" ? consoleTruncationNote(trace) : ""}`;
    }
    if (trace && deps.isSearchTool(trace.name) && !failed && trace.results?.length) {
      return `<div class="chat-console-out is-web">${deps.webResultsHtml(trace.results)}</div>`;
    }
    if (trace && trace.name === "read_url" && !failed && trace.results?.length) {
      const row = trace.results[0];
      const text = String(row.snippet || "").trim();
      return `<div class="chat-console-out is-web">${deps.webResultsHtml([row], 1)}${text ? `<pre class="chat-console-out is-stdout">${deps.escapeHtml(deps.toolText(text, 1200))}</pre>` : ""}</div>`;
    }
    if (trace && trace.name === "search_replace") {
      const diff = [trace.preview, trace.output].find((value) => deps.looksLikeDiff(value));
      if (diff) return `<div class="chat-console-out is-diff">${consoleDiffHtml(diff, { numbers: trace.name !== "search_replace" || /^@@\s+-\d+/.test(diff) })}</div>`;
    }
    let body = deps.consoleOutputText(trace, failed, running);
    if (!body) return "";
    const structured = failed || running ? null : deps.structuredToolHtml(trace, body);
    if (structured !== null) return structured ? `<div class="chat-console-out is-structured">${structured}</div>` : "";
    if ((trace.name === "grep" || trace.name === "glob") && !body.includes("\n") && (body.match(/:\d+:/g) || []).length > 1) {
      body = body.replace(/\s+(\/(?:home|Users|var|tmp|opt|usr)|[A-Za-z]:\\|\S+:\d+:)/g, "\n$1").trim();
    }
    if (deps.looksLikeDiff(body)) return `<div class="chat-console-out is-diff">${consoleDiffHtml(body)}</div>`;
    const lines = String(body).replace(/\s+$/, "").replace(/^\s*\n/, "").split("\n");
    const inner = lines.map((line) => `<div class="chat-console-stdout">${deps.renderAnsi(line) || " "}</div>`).join("");
    return `<pre class="chat-console-out is-stdout${deps.isCodeOutputTool(trace.name) ? " is-code" : ""}">${inner}</pre>`;
  }

function agentTraceWorkMs(trace) {
    if (!trace || !["spawn_subagent", "get_subagent_output"].includes(trace.name)) return undefined;
    const work = Number(trace.agent_work_ms);
    if (Number.isFinite(work) && work >= 0) return work;
    if (trace.name === "spawn_subagent" && !deps.toolBackground(trace)) return undefined;
    return null;
  }

function consoleDurationLabel(trace) {
    const work = agentTraceWorkMs(trace);
    const ms = work !== undefined ? Number(work ?? NaN) : Number(trace ? (trace.execution_duration_ms ?? trace.duration_ms ?? trace.elapsed_ms) : NaN);
    if (!Number.isFinite(ms) || ms < 0) return "";
    const s = Math.max(1, Math.round(ms / 1000));
    if (s < 60) return `${s}s`;
    return deps.formatActivityClock(s);
  }

function consoleResultLine(trace, presentation) {
    const { running, failed, interrupted } = presentation;
    const name = trace && trace.name;
    if (running && name === "run_terminal_command" && deps.toolBackground(trace)) return "Running in the background";
    if (running && name === "run_terminal_command") return `$ ${deps.displayCommand(trace) || "command"}`;
    if (running && name === "spawn_subagent") return deps.toolBackground(trace) ? "Backgrounded agent" : deps.ccAgentLastCall(trace.agent_recent) || "Initializing…";
    if ((name === "monitor") && !failed) {
      // The call returns at once; events print as their own rows. The watch's
      // deadline sits on the line under it, like every other pending call.
      const id = trace.command_id || trace.job_id || deps.executionIdentity(trace) || "";
      return `Monitor started${id ? ` · task ${id}` : ""}`;
    }
    if ((running) && name === "read_url") return "Fetching…";
    if (running) return presentation.statusLabel === "Cancelling" ? "Cancelling…" : "Running…";
    if (failed) return String(trace.error || trace.summary || "failed").replace(/\s+/g, " ").trim();
    if (interrupted) return "Interrupted";
    if (trace.wake_state === "dropped") return "Report not delivered";
    const preview = String(trace.preview || trace.summary || "").replace(/\s+/g, " ").trim();
    if ((name === "read_url") && Number(trace.bytes) > 0) {
      // The parenthetical is the HTTP status line: "OK" only where the
      // status actually says so.
      const code = Number(trace.http_status) || 200;
      return `Received ${deps.ccBytes(trace.bytes)} (${code}${code >= 200 && code < 300 ? " OK" : ""})`;
    }
    if (name === "read_url") {
      const host = deps.pageHost(trace.url);
      if (preview && preview !== "still running" && preview.length < 72) return preview;
      return host || "done";
    }
    if (name === "read_file") {
      const n = deps.consoleLineCount(trace);
      if (n) return `Read ${n} line${n === 1 ? "" : "s"}`;
      if (/^\d+\s+lines?/i.test(preview)) return preview.replace(/^(\d+\s+lines?).*/i, "Read $1");
      return "Read";
    }
    if (name === "list_dir") {
      const n = deps.consoleLineCount(trace);
      if (n) return `${n} ${n === 1 ? "entry" : "entries"}`;
      return preview || "listed";
    }
    if (name === "write_file") {
      const path = deps.compactPath(trace.path || ".", trace, 72);
      const n = typeof trace.content === "string" ? trace.content.replace(/\s+$/, "").split("\n").length : 0;
      if (n) return `Wrote ${n} line${n === 1 ? "" : "s"} to ${path}`;
      // The write receipt says whether the file was created and its size.
      const receipt = typeof trace.output === "string"
        ? deps.cleanToolOutput(trace, trace.output).trim().match(/^(created|wrote) \S+ \((\d+) bytes\)$/) : null;
      if (receipt) return `${receipt[1] === "created" ? "Created" : "Wrote"} ${path} (${receipt[2]} bytes)`;
      return path ? `Wrote ${path}` : "Wrote file";
    }
    if (name === "search_replace") {
      const added = Number(trace.added ?? trace.lines_added);
      const removed = Number(trace.removed ?? trace.lines_removed);
      const counted = consoleDiffCounts([trace.preview, trace.output].find((value) => deps.looksLikeDiff(value)) || "");
      return consoleChangeText(Number.isFinite(added) ? added : counted.added, Number.isFinite(removed) ? removed : counted.removed) || "Updated";
    }
    if (name === "todo_write") {
      const todos = deps.normalizeTodos(trace.todos);
      if (todos.length) {
        const done = todos.filter((item) => item.status === "completed").length;
        return `${todos.length} ${todos.length === 1 ? "todo" : "todos"} · ${done}/${todos.length} done`;
      }
      return "no todos";
    }
    if (name === "run_terminal_command") {
      if (deps.toolBackground(trace)) {
        const exit = deps.shellExitCode(trace);
        if (exit === 0) return `Background command completed (exit code 0)`;
        if (exit !== null) return `Background command exited ${exit}`;
      }
      const exit = deps.shellExitCode(trace);
      if (exit !== null && exit !== 0) return `exit ${exit}`;
      return preview && preview !== "still running" ? deps.shortToolText(preview, 100) : (deps.displayCommand(trace) || "done");
    }
    if (name === "kill_command") {
      const cmd = deps.commandFor(trace);
      if (cmd) return `${cmd} · stopped`;
      const id = trace.command_id || trace.child_id || trace.job_id;
      return id ? `job ${id} · stopped` : "Stopped";
    }
    if (name === "kill_subagent") {
      const already = preview.match(/^already (\w+)/);
      return already ? `Already ${already[1]} · nothing to stop` : "Subagent stopped";
    }
    if (name === "scratch") {
      const read = preview.match(/^\S+ \((\d+) chars\)$/);
      if (read) return `Read ${Number(read[1]).toLocaleString()} chars`;
      const slots = preview.match(/^(\d+) slots$/);
      if (slots) return `Pad holds ${slots[1]} ${slots[1] === "1" ? "slot" : "slots"}`;
      return preview || "done";
    }
    if (name === "spawn_subagent") {
      const took = consoleDurationLabel(trace);
      if (took) return `finished · ${took}`;
      return preview && preview !== "still running" ? deps.shortToolText(preview, 100) : "finished";
    }
    // A loaded skill reads as what it is for: its description, else the
    // body's first line of prose. The whole body opens below.
    if (name === "load_skill") {
      if (trace.path) return `Loaded ${deps.shortToolText(trace.path, 100)}`;
      const about = deps.ccFlat(trace.summary) || skillBodyLead(trace.output);
      return about ? deps.shortToolText(about, 160) : "Loaded skill";
    }
    if (name === "read_transcript") {
      const range = deps.transcriptRange(trace);
      return range ? `Read ${range}` : (preview || "Read transcript");
    }
    if (name === "propose_skill") return preview ? `Proposed skill ${preview}` : "Proposed skill";
    if (name === "search_tools") {
      if (String(trace.query || "") === "hosts") return "Listed connected hosts";
      if (String(trace.query || "") === "skills") return "Listed installed skills";
      const loaded = String(trace.preview || "").split(",").map((v) => v.trim()).filter(Boolean).length;
      return loaded ? `${loaded} tool${loaded === 1 ? "" : "s"} loaded` : "Checked available tools";
    }
    if (name === "get_command_output") {
      const text = preview && preview !== "still running" ? preview : "";
      return text && text !== deps.displayCommand(trace) ? deps.shortToolText(text, 100) : "No output";
    }
    if (name === "create_artifact" || name === "update_artifact" || name === "edit_artifact") {
      const v = trace.version != null ? ` v${trace.version}` : "";
      return `${name === "create_artifact" ? "Created" : name === "edit_artifact" ? "Edited" : "Updated"}${v}`;
    }
    if (name === "read_artifact") return trace.preview ? `Read ${deps.shortToolText(String(trace.preview), 80)}` : "Read source";
    if (name === "get_subagent_output") {
      const subject = deps.agentSubject(trace) || "subagent";
      // A join that returned while its child was still going is stored done
      // with execution_status running (chat_runtime rewrites the call, not
      // the child). Same signal the bash Wait reads; "finished" here is a lie.
      if (deps.executionState(trace) === "running" || String(trace.preview || "") === "still running") {
        return `Agent "${subject}" still running`;
      }
      const took = consoleDurationLabel(trace);
      const verb = presentation.failed ? "failed" : presentation.interrupted ? "stopped" : "finished";
      return took ? `Agent "${subject}" ${verb} · ${took}` : `Agent "${subject}" ${verb}`;
    }
    if (name === "monitor") {
      const n = Number(trace.event_count);
      if (Number.isFinite(n)) return `${n} ${n === 1 ? "event" : "events"}`;
      return preview || "watching";
    }
    if (name === "grep" || name === "glob") {
      const n = deps.consoleMatchCount(trace);
      const lines = String(trace.output || trace.preview || "").split("\n").map((line) => line.trim()).filter(Boolean);
      if (n <= 1 && lines[0] && lines[0].length <= 140) return lines[0];
      if (n) return `${n} ${n === 1 ? "match" : "matches"}`;
      return "";
    }
    if (deps.isSearchTool(name)) {
      const hits = Array.isArray(trace.results) ? trace.results.length : 0;
      if (hits) return `${hits} ${hits === 1 ? "result" : "results"}`;
      const queries = deps.searchQueries(trace);
      if (queries.length) return queries.length === 1 ? "1 query" : `${queries.length} queries`;
      return "done";
    }
    return preview && preview !== "still running" ? deps.shortToolText(preview, 80) : "done";
  }

  return { consoleDiffHtml, consoleFileBody, consoleChangeText, consoleDiffCounts, consoleStdoutHtml, consoleTruncationNote, ccTimeoutNote, consoleSameText, consoleFold, consoleStatusHtml, consoleDetailHtml, agentTraceWorkMs, consoleDurationLabel, consoleResultLine };
}
