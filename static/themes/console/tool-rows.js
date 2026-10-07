// tool-rows presentation. Dependencies are live getters supplied by index.js.
export function create(deps) {
function toolImage(trace) {
    const image = trace?.name === "read_file" && trace.image;
    if (!image || !["image/png", "image/jpeg", "image/webp", "image/gif"].includes(image.mime)) return null;
    const safe = { mime: image.mime };
    if (typeof image.preview_url === "string" && /^\/chat\/runs\/[A-Za-z0-9_-]{1,80}\/images\/ts_\d+_\d+$/.test(image.preview_url)) {
      safe.preview_url = image.preview_url;
    } else if (typeof image.data === "string" && image.data.length && image.data.length % 4 === 0 &&
        !/[^A-Za-z0-9+/=]/.test(image.data) && /^[^=]*={0,2}$/.test(image.data)) {
      safe.data = image.data;
    } else return null;
    for (const key of ["width", "height", "original_width", "original_height", "frame", "frames"]) {
      if (Number.isSafeInteger(image[key]) && image[key] >= (key === "frame" ? 0 : 1)) safe[key] = image[key];
    }
    if (typeof image.format === "string") safe.format = image.format.slice(0, 32);
    return safe;
  }

function toolImageHtml(trace) {
    const image = toolImage(trace);
    if (!image) return '<span class="chat-tool-truncated">Image preview unavailable.</span>';
    const info = [image.format || image.mime];
    if (image.width && image.height) info.push(`${image.width} × ${image.height}`);
    if (image.original_width && image.original_height && (image.original_width !== image.width || image.original_height !== image.height)) {
      info.push(`resized from ${image.original_width} × ${image.original_height}`);
    }
    if (image.frames > 1 && image.frame != null) info.push(`frame ${image.frame + 1} of ${image.frames}`);
    return `<figure class="chat-tool-image">${deps.imagePreviewButton({ data: image.preview_url || `data:${image.mime};base64,${image.data}`, name: trace.path || "Image preview" })}` +
      `<figcaption>${deps.escapeHtml(info.join(" · "))}<span>Preview only; model image support depends on the selected model.</span></figcaption></figure>`;
  }

function toolDetailHtml(trace, failed, running) {
    const sections = [];
    const hasImage = trace.name === "read_file" && trace.image && !failed && !running;
    if (hasImage) sections.push(toolImageHtml(trace));
    const error = failed ? trace.error || trace.summary || trace.preview || "The tool failed without an error message."
      : ["interrupted", "cancelled"].includes(trace.status) || ["cancelled", "interrupted", "lost"].includes(deps.executionState(trace)) ? trace.error || "" : "";
    if (error) sections.push(deps.failNoteHtml(error));
    const inputs = [trace.command, trace.path, trace.pattern, trace.url, deps.searchQueries(trace).join("\n"), trace.description, trace.title];
    const seen = new Set([error, ...inputs].filter(Boolean).map(deps.normErr));
    const preview = trace.summary && String(trace.summary).startsWith(String(trace.preview || "")) ? "" : trace.preview;
    const authoritativeOutput = typeof trace.output === "string";
    let output = authoritativeOutput ? deps.cleanToolOutput(trace, trace.output) : [preview, trace.summary].filter((value) => {
      const key = deps.normErr(value);
      if (!key || seen.has(key) || key === "still running" || key === "background task started") return false;
      seen.add(key);
      return true;
    }).join("\n\n");
    if (error && deps.normErr(output) === deps.normErr(error)) output = "";
    const outputLabel = { read_file: "Contents", list_dir: "Entries", grep: "Matches", glob: "Matches", search_replace: "Change result", write_file: "Write result", spawn_subagent: "Report", get_subagent_output: "Report", monitor: "Events" }[trace.name] || "Output";
    const hasResults = Boolean(trace.results?.length) && (deps.isSearchTool(trace.name) || trace.name === "read_url");
    const diff = ["search_replace", "write_file"].includes(trace.name) && !failed
      ? [trace.preview, trace.output].find((value) => deps.looksLikeDiff(value)) : "";
    const fileBody = trace.name === "write_file" && !failed && !diff
      ? (deps.looksLikeDiff(trace.preview) ? "" : String(trace.preview || "").trim()) : "";
    let shownOutput = !hasResults;
    if (hasResults) {
      const snippet = trace.name === "read_url" ? String(trace.results[0].snippet || "").trim() : "";
      sections.push(`<section class="chat-tool-section is-sources" data-tool-section="Sources"><span class="chat-tool-section-label visually-hidden">Sources</span>${deps.webResultsHtml(trace.results)}` +
        (snippet ? `<pre class="chat-tool-out">${deps.escapeHtml(deps.toolText(snippet, 1600))}</pre>` : "") + "</section>");
    } else if (diff) {
      shownOutput = false;
      sections.push(`<section class="chat-tool-section is-result is-file" data-tool-section="Changes"><div class="chat-tool-section-head"><span class="chat-tool-section-label">Changes</span>` +
        `<button type="button" class="chat-tool-copy" data-copy-tool aria-label="Copy changes">Copy</button></div>` +
        `<div class="chat-tool-out is-file">${deps.consoleDiffHtml(diff, { numbers: trace.name !== "search_replace" })}</div></section>`);
    } else if (fileBody) {
      shownOutput = false;
      sections.push(`<section class="chat-tool-section is-result is-file" data-tool-section="Contents"><div class="chat-tool-section-head"><span class="chat-tool-section-label">Contents</span>` +
        `<button type="button" class="chat-tool-copy" data-copy-tool aria-label="Copy contents">Copy</button></div>` +
        `<div class="chat-tool-out is-file">${deps.fileBodyHtml(trace, fileBody)}</div></section>`);
    } else if (!failed && output && deps.structuredToolHtml(trace, output) !== null) {
      shownOutput = false;
      const body = deps.structuredToolHtml(trace, output);
      if (body) sections.push(`<section class="chat-tool-section is-result is-structured" data-tool-section="${outputLabel}"><span class="chat-tool-section-label visually-hidden">${outputLabel}</span>${body}</section>`);
    } else if (trace.name === "read_file" && output && !failed) {
      sections.push(`<section class="chat-tool-section is-result is-file" data-tool-section="${outputLabel}"><div class="chat-tool-section-head"><span class="chat-tool-section-label">${outputLabel}</span>` +
        `<button type="button" class="chat-tool-copy" data-copy-tool aria-label="Copy ${outputLabel.toLowerCase()}">Copy</button></div>` +
        `<div class="chat-tool-out is-file">${deps.fileBodyHtml(trace, output)}</div></section>`);
    } else if (output || (trace.command && !failed)) {
      sections.push(detailSectionHtml(outputLabel, output || (running ? "Waiting for output" : "No output"), `${output ? "is-result" : "is-result is-empty"}${deps.isCodeOutputTool(trace.name) ? " is-code" : ""}`, authoritativeOutput ? 24000 : 4000, trace.command || ""));
    }
    if (authoritativeOutput && shownOutput) {
      const retained = Array.from(trace.output).length;
      const total = Number.isFinite(trace.output_chars) ? trace.output_chars : retained;
      let note = "";
      if (trace.output_mode === "partial" && (trace.truncated || total > retained)) note = `Partial output · ${retained.toLocaleString()} characters retained · total ${trace.output_chars_exact === true ? total.toLocaleString() : `unknown (at least ${total.toLocaleString()})`}.`;
      else if (trace.output_mode === "tail") note = `Retained tail · ${retained.toLocaleString()} of ${trace.output_chars_exact === false ? "at least " : ""}${total.toLocaleString()} characters${Number.isFinite(trace.output_start) ? ` · starts at offset ${trace.output_start.toLocaleString()}` : ""}.`;
      else if (trace.truncated || total > retained) note = `Output truncated · ${retained.toLocaleString()} of ${trace.output_chars_exact === false ? "at least " : ""}${total.toLocaleString()} characters retained.`;
      if (note) sections.push(`<span class="chat-tool-truncated">${deps.escapeHtml(note)}</span>`);
    } else if (trace.output_truncated && output.length <= 4000) sections.push('<span class="chat-tool-truncated">Output preview was truncated before display.</span>');
    if (!hasImage && !error && !output && !hasResults && !trace.command && !["todo_write", "create_artifact", "update_artifact"].includes(trace.name)) {
      sections.push(detailSectionHtml("Output", running ? "Waiting for output"
        : trace.child_id && ["spawn_subagent", "get_subagent_output"].includes(trace.name) ? "No inline output. Open agent for its transcript." : "No output", "is-empty"));
    }
    const exit = deps.shellExitCode(trace);
    const work = deps.agentTraceWorkMs(trace);
    const duration = work !== undefined ? work
      : Number.isFinite(trace.execution_duration_ms) ? trace.execution_duration_ms : Number.isFinite(trace.duration_ms) ? trace.duration_ms : trace.latency_ms;
    const state = deps.executionState(trace);
    const queries = deps.searchQueries(trace);
    const meta = [
      trace.host ? `Host ${trace.host}` : "",
      trace.cwd && trace.cwd !== trace.project_root ? `Directory ${trace.cwd}` : trace.cwd && !trace.host ? `Directory ${trace.cwd}` : "",
      trace.path && ["read_file", "write_file", "search_replace", "list_dir", "grep", "glob"].includes(trace.name) ? trace.path : "",
      Number.isFinite(trace.offset) ? `lines ${trace.offset}–${trace.offset + (Number.isFinite(trace.limit) ? trace.limit - 1 : 0)}` : "",
      trace.include ? `include ${trace.include}` : "",
      queries.length > 1 ? queries.join(" · ") : "",
      running && trace.progressLabel ? trace.progressLabel : "",
      deps.toolBackground(trace) ? "background" : "",
      state && !["completed", "running"].includes(state) ? state.replace(/_/g, " ") : "",
      trace.wake_state === "dropped" ? "Report not delivered to the main agent" : trace.wake_state === "held" ? "Main agent stopped; report goes with your next message" : trace.wake_state === "retrying" ? "Report pending delivery" : "",
      trace.status === "done" && deps.executionPending(trace) ? "Tool returned; execution continues" : "",
      trace.termination_confirmed === true ? "Termination confirmed" : trace.termination_confirmed === false ? "Termination not confirmed"
        : deps.isShellExecTool(trace.name) && state && !deps.executionPending(trace) ? "Termination unknown" : "",
      trace.version != null ? `v${trace.version}` : "", trace.visibility || "",
      exit !== null ? `exit ${exit}` : "",
      state === "timed_out" && Number.isFinite(trace.timeout_ms) ? `timeout ${deps.humanMs(trace.timeout_ms)}` : "",
      trace.name === "monitor" && Number.isFinite(trace.event_count) ? `${trace.event_count} events` : "",
      trace.name === "todo_write" || duration == null ? "" : work != null ? `Agent worked ${deps.formatTurnDuration(work)}` : deps.humanMs(duration),
      // The id matters when a run did not end cleanly (to find or stop it).
      deps.executionIdentity(trace) && ((state && !["completed", "running"].includes(state)) || trace.termination_confirmed === false) ? `ID ${deps.executionIdentity(trace)}` : "",
    ].filter(Boolean);
    if (meta.length) sections.push(`<div class="chat-tool-context"${deps.executionIdentity(trace) ? ` title="ID ${deps.escapeHtml(deps.executionIdentity(trace))}"` : ""}>${meta.map((value) => `<span>${deps.escapeHtml(value)}</span>`).join("")}</div>`);
    return `<div class="chat-tool-details">${sections.join("")}</div>`;
  }

function detailSectionHtml(label, value, kind = "", cap = 4000, command = "") {
    const text = String(value || "");
    const characters = Array.from(text);
    const clipped = characters.slice(0, cap).join("");
    const ansi = kind.includes("is-result") || kind === "is-command" || kind === "is-error";
    const cmd = command ? `<span class="chat-tool-cmd">$ ${deps.escapeHtml(command)}</span>\n` : "";
    const body = deps.looksLikeDiff(clipped)
      ? `<pre class="chat-tool-out is-diff">${cmd}<code>${deps.highlightCode("diff", clipped)}</code></pre>`
      : `<pre class="chat-tool-out">${cmd}${cmd ? `<span class="chat-tool-text">${ansi ? deps.renderAnsi(clipped) : deps.escapeHtml(clipped)}</span>` : ansi ? deps.renderAnsi(clipped) : deps.escapeHtml(clipped)}</pre>`;
    return `<section class="chat-tool-section ${kind}" data-tool-section="${deps.escapeHtml(label)}"><div class="chat-tool-section-head"><span class="chat-tool-section-label">${deps.escapeHtml(label)}</span>` +
      (command ? `<button type="button" class="chat-tool-copy" data-copy-tool data-copy-value="${deps.escapeHtml(command)}" aria-label="Copy command">Copy command</button>` : "") +
      `<button type="button" class="chat-tool-copy" data-copy-tool aria-label="Copy ${deps.escapeHtml(label.toLowerCase())}">Copy</button></div>` +
      body +
      (characters.length > cap ? `<span class="chat-tool-truncated">Preview truncated at ${cap.toLocaleString()} characters.</span>` : "") + "</section>";
  }

function toolRowLabel(trace) {
    const subject = deps.toolSubject(trace);
    return `${deps.toolAction(trace)}${subject ? ` · ${subject}` : ""}`;
  }

function toolStepHtml(trace, index, live, open, openMap, opts = {}) {
    const presentation = deps.toolPresentation(trace, live);
    const { running, interrupted, failed } = presentation;
    const artifact = trace.name === "create_artifact" || trace.name === "update_artifact";
    const read = trace.name === "read_url";
    const search = deps.isSearchTool(trace.name);
    const label = artifact ? `${trace.name === "create_artifact" ? "Create artifact" : "Update artifact"} · ${trace.title || "artifact"}`
      : read ? `Page read · ${deps.pageHost(trace.url) || "page"}`
      : search ? `Search · ${deps.searchCountFor(trace) === 1 && deps.searchQueries(trace)[0] ? deps.shortToolText(deps.searchQueries(trace)[0], 72) : `${deps.searchCountFor(trace)} ${deps.searchCountFor(trace) === 1 ? "query" : "queries"}`}`
      : toolRowLabel(trace);
    const kind = artifact ? "artifact" : read ? "read" : search ? "search" : TOOL_KIND[trace.name] || "workspace";
    const openKind = artifact && trace.identifier && presentation.state === "done" ? "artifact"
      : ["spawn_subagent", "get_subagent_output"].includes(trace.name) && trace.child_id ? "agent" : "";
    const openId = openKind === "artifact" ? trace.identifier : openKind === "agent" ? trace.child_id : "";
    const revealed = open;
    let inner;
    const icon = (failed ? deps.STEP_ICONS.error : interrupted ? deps.STEP_ICONS.stop : artifact ? deps.STEP_ICONS.artifact : deps.toolIcon(trace.name, false, false));
    {
      const verbose = Boolean(opts.verbose);
      const fold = deps.consoleFold(trace, presentation, verbose);
      // An opened summary line prints a call the way an expanded row reads.
      const preview = verbose ? "" : fold.preview;
      inner = verbose ? fold.full || fold.preview : fold.full;
      const expandable = !verbose && openKind !== "agent" && Boolean(fold.full);
      const head = deps.toolHeadHtml({
        ...presentation, line: deps.consoleCallHtml(trace, presentation, verbose), title: trace.command || trace.path || trace.url || label, expandable, open: revealed, openKind, openId,
        contextHost: "",
      });
      const hangOpen = verbose ? Boolean(inner) : revealed && expandable;
      const status = fold.status;
      // A pending call names its deadline at the end of the line under it
      // ("Waiting for output… (timeout: 2m)"), and that line exists even when
      // the call has nothing else to say yet. A fold that already printed the
      // deadline (the command line under a described shell) is left as it is.
      const timeout = (running) && !verbose && !/\btimeout: [^)]*/.test(`${status} ${preview}`) ? deps.ccTimeoutNote(trace) : "";
      const showHang = Boolean(status || preview || inner || timeout);
      // A fold with only its hidden body (the settled skill load) prints one
      // line: the branch glyph and the hang's margins wait for the open.
      const bodyOnly = !status && !preview;
      // A non-zero exit Console prints as an error ("Error: Exit code 1")
      // gets the red dot too; exit 1 from grep and friends stays finished.
      const dotState = (presentation.state === "warning") && trace.name === "run_terminal_command" &&
        deps.ccExitMeaning(deps.displayCommand(trace), deps.shellExitCode(trace)).isError ? "error" : presentation.state;
      return (
        `<li class="chat-step is-console-call is-${kind} is-${dotState}${hangOpen || verbose ? " is-console-expanded" : ""}${verbose ? " is-console-verbose" : ""}" style="--console-hang: ${deps.consoleVerb(trace.name, trace).length + 1}ch" data-execution="${deps.escapeHtml(deps.executionIdentity(trace))}" data-tool="${deps.escapeHtml(trace.name)}" data-state="${presentation.state}" data-step="${deps.escapeHtml(index)}">` +
        `<span class="chat-step-node" aria-hidden="true">${icon}</span>` +
        `<div class="chat-step-main">${head}` +
        (showHang
          ? `<div class="chat-step-reveal is-open">` +
            `<div class="chat-step-reveal-inner">` +
            `<div class="chat-console-hang${running ? " is-running" : ""}${failed ? " is-error" : ""}${fold.keep ? " is-kept" : ""}${bodyOnly ? " is-body-only" : ""}${hangOpen ? " is-expanded" : ""}">` +
            `<span class="chat-console-branch" aria-hidden="true">└</span>` +
            `<div class="chat-console-hang-body">` +
            (status || timeout ? `<span class="chat-console-result-text">${deps.consoleStatusHtml(status)}${status && timeout ? " " : ""}${timeout ? `<span class="chat-console-timeout">${deps.escapeHtml(timeout)}</span>` : ""}</span>` : "") +
            (preview ? `<div class="chat-console-preview">${preview}</div>` : "") +
            (inner ? inner : "") +
            `</div></div></div></div>`
          : "") +
        `</div></li>`
      );
    }

  }

const TOOL_KIND = {
    read_file: "file",
    list_dir: "list",
    grep: "grep",
    glob: "glob",
    write_file: "write",
    search_replace: "edit",
    run_terminal_command: "shell",
    get_command_output: "shell",
    monitor: "watch",
    search_tools: "tools",
    load_skill: "skill",
    spawn_subagent: "agent",
    get_subagent_output: "agent",
    kill_subagent: "agent",
    list_subagents: "agent",
    search_models: "search",
  };

  return { toolImage, toolImageHtml, toolDetailHtml, detailSectionHtml, toolRowLabel, toolStepHtml, TOOL_KIND };
}
