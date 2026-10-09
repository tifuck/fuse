// tool-headers presentation. Dependencies are live getters supplied by index.js.
export function create(deps) {
function consoleVerb(name, trace) {
    if (name === "fuse-steer") return "Notice";
    {
      if (name === "grep" || name === "glob") return "Search";
      if (name === "spawn_subagent") return deps.ccAgentType(trace);
      if (name === "run_terminal_command" && deps.ccSedEdit(deps.displayCommand(trace))) return "Update";
    }
    if (deps.isSearchTool(name)) return "Web Search";
    if (name === "read_url") return "Fetch";
    if (name === "read_file") return "Read";
    if (name === "list_dir") return "List";
    if (name === "write_file") return "Write";
    if (name === "search_replace") return "Update";
    if (name === "run_terminal_command") return "Bash";
    if (name === "monitor") return "Monitor";
    if (name === "get_command_output") return "Wait";
    if (name === "kill_command") return "Stop Task";
    if (name === "kill_subagent") return "Stop";
    if (name === "spawn_subagent") return "Agent";
    if (name === "get_subagent_output") return "Await";
    if (name === "list_subagents") return "Agents";
    if (name === "search_models") return "Models";
    if (name === "load_skill") return "Skill";
    if (name === "propose_skill") return "Draft";
    if (name === "grep") return "Grep";
    if (name === "glob") return "Glob";
    if (name === "todo_write") return "Update Todos";
    if (name === "scratch") return "Scratch";
    if (name === "create_artifact" || name === "update_artifact") return "Artifact";
    if (name === "search_tools") return "Tools";
    if (name === "read_transcript") return "Transcript";
    return "Tool";
  }

function toolStateHtml(running, failed, interrupted, statusLabel) {
    const label = statusLabel || (failed ? "Failed" : interrupted ? "Stopped" : running ? "Running" : "Done");
    return `<span class="chat-step-state is-empty" title="${deps.escapeHtml(label)}" aria-hidden="true"></span>`;

  }

function toolOpenHtml(kind, id) {
    if (!id) return '<span class="chat-step-open is-empty" aria-hidden="true">Open</span>';
    const attr = kind === "artifact" ? "data-open-artifact" : "data-open-agent";
    return `<button type="button" class="chat-step-open" ${attr}="${deps.escapeHtml(id)}" aria-label="Open ${kind === "artifact" ? "artifact" : "agent"}">Open${(`<span class="chat-open-target">&nbsp;${kind === "artifact" ? "artifact" : "agent"} ↗</span>`)}</button>`;
  }

function toolCaretHtml(expandable, open) {
    if (!expandable) return '<span class="chat-step-caret is-empty" aria-hidden="true"></span>';
    return `<span class="chat-step-caret" aria-hidden="true">${deps.STEP_CHEVRON}</span>`;
  }

function toolHeadHtml({ line, title, running, failed, interrupted, expandable, open, openKind, openId, kind, statusLabel, openRow, contextHost }) {
    const label = `<span class="chat-step-label"${title ? ` title="${deps.escapeHtml(title)}"` : ""}>${line}</span>`;
    const inner = kind
      ? `<span class="chat-step-copy"><span class="chat-step-kind">${deps.escapeHtml(kind)}</span>${label}</span>`
      : label;
    const copy = expandable
      ? `<button type="button" class="chat-step-toggle" data-step-toggle aria-expanded="${open ? "true" : "false"}">${inner}</button>`
      : openRow
        ? `<button type="button" class="chat-step-toggle is-open-agent" data-open-agent="${deps.escapeHtml(openId || "")}" aria-haspopup="dialog" title="Open subagent">${inner}</button>`
        : `<div class="chat-step-static">${inner}</div>`;
    const caret = expandable
      ? `<button type="button" class="chat-step-caret" data-step-toggle aria-expanded="${open ? "true" : "false"}" aria-label="Toggle details">${deps.STEP_CHEVRON}</button>`
      : openRow
        ? `<span class="chat-step-caret" aria-hidden="true">${deps.STEP_CHEVRON}</span>`
        : toolCaretHtml(false);
    return `<div class="chat-step-head">${copy}${toolStateHtml(running, failed, interrupted, statusLabel)}${toolOpenHtml(openKind, openId)}${caret}` +
      (contextHost && !openId ? `<span class="chat-step-host" title="Host ${deps.escapeHtml(contextHost)}">@${deps.escapeHtml(contextHost)}</span>` : "") + `</div>`;
  }

  return { consoleVerb, toolStateHtml, toolOpenHtml, toolCaretHtml, toolHeadHtml };
}
