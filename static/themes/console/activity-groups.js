// activity-groups presentation. Dependencies are live getters supplied by index.js.
export function create(deps) {
function ccTurnLineHtml(msg, latest) {
    if (msg.elapsedMs == null) return "";
    const pendingAgents = latest ? (deps.agentsRows || []).filter((row) => deps.agentLive(row.status)).length : 0;
    const head = `<span class="chat-console-turn-mark" aria-hidden="true">✻</span> `;
    if (pendingAgents) {
      return `${head}<span>Waiting for <b>${pendingAgents}</b> background ${pendingAgents === 1 ? "agent" : "agents"} to finish</span>`;
    }
    // A stopped or failed turn never finished, so it says so instead of "done".
    const outcome = msg.stopped ? "Interrupted" : msg.error ? "Failed" : "";
    const done = outcome ? "" : deps.ccDoneAt(msg.endedAt);
    const still = latest ? deps.ccStillRunningText() : "";
    return `${head}<span>${outcome ? `${outcome} · ` : ""}${deps.escapeHtml(deps.ccTurnVerb(msg))} for ${deps.escapeHtml(deps.ccDuration(Math.max(0, Number(msg.elapsedMs) || 0)))}` +
      `${done ? ` · done ${deps.escapeHtml(done)}` : ""}${still ? ` · ${deps.escapeHtml(still)} still running` : ""}</span>`;
  }

function ccShellHint(trace) {
    const command = deps.displayCommand(trace);
    const nl = command.indexOf("\n");
    const first = (nl === -1 ? command : command.slice(0, nl)).trim();
    if (first.startsWith("#") && !first.startsWith("#!") &&
        !(nl !== -1 && command.slice(nl + 1).split("\n").some((line) => line.trim() && !line.trim().startsWith("#")))) {
      const note = first.replace(/^#+\s*/, "");
      if (note && !/[\x00-\x1f\x7f-\x9f]/.test(note)) return deps.ccClip(note, 300);
    }
    const lines = command.split("\n").map(deps.ccFlat).filter(Boolean);
    return lines.length ? deps.ccClip("$ " + lines.join("\n"), 300) : "";
  }

function ccPathHint(trace) {
    const path = String((trace && trace.path) || "").trim();
    return path ? deps.ccClip(deps.ccFlat(deps.shortToolPath(path, trace)), 300) : "";
  }

function ccActivityText(trace, cap = 50) {
    const name = trace.name;
    if (name === "run_terminal_command") {
      const command = deps.displayCommand(trace);
      return command ? deps.ccFlat(`Running ${deps.ccClip(deps.ccFlat(command), cap)}`) : "Running command";
    }
    if (name === "read_file") return trace.path ? `Reading ${ccPathHint(trace)}` : "Reading file";
    if (name === "grep") return trace.pattern ? deps.ccFlat(`Searching for ${deps.ccClip(trace.pattern, cap)}`) : "Searching";
    if (name === "glob") return trace.pattern ? deps.ccFlat(`Finding ${deps.ccClip(trace.pattern, cap)}`) : "Finding files";
    if (name === "list_dir") return trace.path ? `Listing ${ccPathHint(trace)}` : "Listing directory";
    return "";
  }

function ccRunning(trace) {
    return deps.toolPresentation(trace, true).running && !deps.toolBackground(trace);
  }

function ccSince(trace) {
    if (!trace._since) {
      const started = Number(trace.started_ms);
      trace._since = Number.isFinite(started) && started > 0
        ? started
        : Date.now() - Math.max(0, Number(trace.elapsed_ms) || 0);
    }
    return trace._since;
  }

function ccHeadline(items, msg) {
    const last = items[items.length - 1];
    if (!last || last.type !== "tool") return "";
    const activity = msg?.id === deps.activeRun?.message_id ? deps.activeRun?.activity : msg?._activity;
    const busy = items.some((item) => item.type === "tool" && ccRunning(item.entry.trace));
    if (activity?.phase ? activity.phase !== "tools" : !busy) return "";
    return ccLiveText(last.entry.trace);
  }

function ccLiveText(trace, cap) {
    if (!CC_LIVE_TOOLS.has(trace && trace.name)) return "";
    const description = deps.ccFlat(trace.description);
    const text = description ? deps.ccRunningForm(description) ?? description : ccActivityText(trace, cap);
    return text ? text.charAt(0).toUpperCase() + text.slice(1) : "";
  }

function ccElapsedText(since) {
    const ms = Date.now() - since;
    return ms >= 2000 ? ` · ${deps.ccDuration(ms)}` : "";
  }

function ccShellSuffix(since, lines) {
    const seconds = Math.floor((Date.now() - since) / 1000);
    if (seconds < 2) return "";
    const took = deps.ccDuration(seconds * 1000);
    return lines > 0 ? ` (${took} · ${lines} ${lines === 1 ? "line" : "lines"})` : ` (${took})`;
  }

function ccThinkClock(base, since) {
    return deps.ccDuration(Math.max(1000, base + (since ? Date.now() - since : 0)));
  }

function syncConsoleClocks(root) {
    root.querySelectorAll("[data-cc-clock]").forEach((el) => {
      const since = Number(el.dataset.since) || 0;
      const kind = el.dataset.ccClock;
      const text = kind === "think" ? ccThinkClock(Number(el.dataset.base) || 0, since)
        : kind === "work" ? deps.ccDuration(Math.max(1000, (Number(el.dataset.base) || 0) + (since ? Date.now() - since : 0)))
        : kind === "suffix" ? ccShellSuffix(since, Number(el.dataset.lines) || 0)
        : ccElapsedText(since);
      if (el.textContent !== text) el.textContent = text;
    });
  }

function workClocksLive() {
    return Boolean(document.querySelector('[data-cc-clock="work"]'));
  }

function syncWorkClocks() {
    document.querySelectorAll('[data-cc-clock="work"]').forEach((el) => {
      const base = Number(el.dataset.base) || 0;
      const since = Number(el.dataset.since) || 0;
      const text = deps.ccDuration(Math.max(1000, base + (since ? Date.now() - since : 0)));
      if (el.textContent !== text) el.textContent = text;
    });
  }

function ccGroupHtml(group, msg, live, active, openMap, thinking) {
    // A live thought's line keeps the key it will have once the thought lands.
    const key = "cgroup-" + (group.items[0]?.key || `think-${group.liveThought}`);


    const open = group.items.length > 0 && group.items.some((item) => deps.stepOpenAt(openMap, "cgroup-" + item.key));
    let search = 0;
    let readOps = 0;
    let list = 0;
    let bash = 0;
    let gitOpBash = 0;
    let thoughtMs = 0;
    let thought = false;
    let latestThought;
    let hint;
    let errored = false;
    let runningSince = 0;
    let shell = null;
    const readPaths = new Set();
    const searchArgs = [];
    const commits = [];
    const pushes = [];
    const branches = [];
    const prs = [];
    group.items.forEach((item) => {
      if (item.type === "think") {
        thought = true;
        thoughtMs += item.ms;
        latestThought = item.text;
        return;
      }
      latestThought = undefined;
      const trace = item.entry.trace;
      const presentation = deps.toolPresentation(trace, live);
      if (presentation.failed) errored = true;
      if (presentation.running && !deps.toolBackground(trace)) {
        const since = ccSince(trace);
        runningSince = Math.max(runningSince, since);
        if (trace.name === "run_terminal_command" && (!shell || since < shell.since)) {
          const out = typeof trace.output === "string" ? trace.output.replace(/\s+$/, "") : "";
          shell = { since, lines: out ? out.split("\n").length : 0 };
        }
      }
      const cls = item.cls;
      if (cls.kind === "silent") return;
      if (cls.kind === "bash") {
        bash += 1;
        hint = ccShellHint(trace) || hint;
        const ops = !presentation.running && typeof trace.output === "string" ? deps.ccGitOps(deps.displayCommand(trace), trace.output) : null;
        if (ops) {
          gitOpBash += 1;
          if (ops.commit) commits.push(ops.commit);
          if (ops.push) pushes.push(ops.push);
          if (ops.branch) branches.push(ops.branch);
          if (ops.pr) prs.push(ops.pr);
        }
      } else if (cls.kind === "list") {
        list += 1;
        hint = (cls.shell ? ccShellHint(trace) : ccPathHint(trace)) || hint;
      } else if (cls.kind === "search") {
        search += 1;
        const pattern = cls.shell ? "" : deps.ccClip(deps.ccFlat(trace.pattern || trace.query), 300);
        if (pattern) {
          searchArgs.push(pattern);
          hint = `"${pattern}"`;
        }
      } else if (!cls.shell && trace.path) {
        readPaths.add(String(trace.path));
        hint = ccPathHint(trace) || hint;
      } else {
        readOps += 1;
        if (cls.shell) hint = ccShellHint(trace) || hint;
      }
    });
    if (active && thinking && !group.items.length) thought = true;
    // Path reads count distinct files (two reads of one path are one file);
    // shell reads (cat, head, …) have no path to dedupe and count their calls.
    // Both sides always count: a path read plus a shell read is two files.
    const reads = readPaths.size + readOps;
    const shells = Math.max(0, bash - gitOpBash);
    if (!thought && !search && !reads && !list && !shells && !gitOpBash) return "";

    const parts = [];
    if (thought) {
      const since = active && thinking ? thinking.since : 0;
      const clock = since
        ? `<b data-cc-clock="think" data-base="${Math.round(thoughtMs)}" data-since="${since}">${ccThinkClock(thoughtMs, since)}</b>`
        : `<b>${ccThinkClock(thoughtMs, 0)}</b>`;
      parts.push(`${active ? "Thinking" : "Thought"} for ${clock}`);
    }
    const add = (verb, rest) => {
      const word = parts.length ? verb : verb.charAt(0).toUpperCase() + verb.slice(1);
      parts.push(rest ? `${deps.escapeHtml(word)} ${rest}` : deps.escapeHtml(word));
    };
    const bold = (text) => `<b>${deps.escapeHtml(text)}</b>`;
    for (const [kind, verb] of [["committed", "committed"], ["amended", "amended commit"], ["cherry-picked", "cherry-picked"]]) {
      const shas = commits.filter((commit) => commit.kind === kind).map((commit) => commit.sha);
      if (shas.length) add(verb, bold(shas.join(", ")));
    }
    if (pushes.length) add("pushed to", bold([...new Set(pushes)].join(", ")));
    branches.forEach((branch) => add(branch.action === "merged" ? "merged" : "rebased onto", bold(branch.ref)));
    prs.forEach((pr) => add(CC_PR_VERBS[pr.action] || pr.action, bold(pr.url && pr.url.includes("/-/merge_requests/") ? `MR !${pr.number}` : `PR #${pr.number}`)));
    if (search) add(active ? "searching for" : "searched for", `<b>${search}</b> ${search === 1 ? "pattern" : "patterns"}`);
    if (reads) add(active ? "reading" : "read", `<b>${reads}</b> ${reads === 1 ? "file" : "files"}`);
    if (list) add(active ? "listing" : "listed", `<b>${list}</b> ${list === 1 ? "directory" : "directories"}`);
    if (shells) add(active ? "running" : "ran", `<b>${shells}</b> shell ${shells === 1 ? "command" : "commands"}`);

    const headline = active ? ccHeadline(group.items, msg) : "";
    const elapsed = active && runningSince
      ? `<span class="chat-console-dim" data-cc-clock="elapsed" data-since="${runningSince}">${ccElapsedText(runningSince)}</span>` : "";
    const label = (headline ? deps.escapeHtml(headline) : parts.join(", ")) + elapsed + (active && !headline ? "…" : "");

    let hintHtml = "";
    if (active && latestThought !== undefined) {
      hintHtml = `<span class="chat-console-thinkhint">${deps.escapeHtml(deps.ccClip(deps.ccFlat(latestThought), 3000))}</span>`;
    } else if (active) {
      const text = hint ?? (searchArgs.length ? `"${searchArgs[searchArgs.length - 1]}"` : "");
      if (text) {
        const lines = deps.ccClip(text, 700).split("\n");
        const suffix = shell
          ? `<span data-cc-clock="suffix" data-since="${shell.since}" data-lines="${shell.lines}">${ccShellSuffix(shell.since, shell.lines)}</span>` : "";
        hintHtml = lines.map((line, i) => `<span class="chat-console-hint-line">${deps.escapeHtml(line)}${i === lines.length - 1 ? suffix : ""}</span>`).join("");
      }
    }
    const hang = (body, cls = "") => `<div class="chat-console-hang${cls}"><span class="chat-console-branch" aria-hidden="true">└</span><div class="chat-console-hang-body">${body}</div></div>`;
    const rows = group.items.map((item) => item.type === "think"
      ? `<li class="chat-step is-console-thought" data-thought="${item.segmentIndex}">` +
        `<span class="chat-step-node" aria-hidden="true"></span>` +
        `<div class="chat-step-main"><div class="chat-step-head"><span class="chat-step-label">Thought for ${deps.escapeHtml(ccThinkClock(item.ms, 0))}</span></div>` +
        `<div class="chat-console-thought chat-think-markdown">${deps.renderMarkdown(item.text, false)}</div></div></li>`
      : deps.toolStepHtml(item.entry.trace, item.key, live, true, openMap, { verbose: true })).join("");
    const state = active ? `is-running${errored ? " is-errored" : ""}` : "is-done";
    // A streaming thought: the transcript fills [data-reason-preview] and
    // keeps it pinned to the newest line (thinking-disclosure.js).
    if (!group.items.length && group.liveThought !== undefined) {
      return (
        `<li class="chat-step is-console-group is-console-live-think ${state}" data-step="think-${group.liveThought}">` +
        `<span class="chat-step-node" aria-hidden="true"></span>` +
        `<div class="chat-step-main">` +
        `<div class="chat-step-head"><button type="button" class="chat-step-toggle" data-step-toggle aria-expanded="false"><span class="chat-step-label">${label}</span></button></div>` +
        `<div class="chat-think-window"><div class="chat-console-think-peek" data-reason-preview="${group.liveThought}" aria-label="Live thinking"><div class="chat-console-thought chat-think-markdown"></div></div></div>` +
        `</div></li>`
      );
    }
    return (
      `<li class="chat-step is-console-group ${state}${open ? " is-console-expanded" : ""}" data-step="${deps.escapeHtml(key)}">` +
      `<span class="chat-step-node" aria-hidden="true"></span>` +
      `<div class="chat-step-main">` +
      `<div class="chat-step-head">` + (group.items.length
        ? `<button type="button" class="chat-step-toggle" data-step-toggle aria-expanded="${open ? "true" : "false"}"><span class="chat-step-label">${label}</span></button>`
        : `<div class="chat-step-toggle"><span class="chat-step-label">${label}</span></div>`) + `</div>` +
      // A failed or interrupted member says so on its own row once the line
      // is opened; the folded line is only its summary.
      (hintHtml ? hang(hintHtml, " is-group-hint") : "") +
      `<div class="chat-console-bundle"><ol class="chat-timeline">${rows}</ol></div>` +
      `</div></li>`
    );
  }

function consoleActivityEntriesHtml(entries, msg, live, blockLive = false) {
    const stepOpen = msg.stepOpen || {};
    const times = deps.consoleFreezeThoughtTimes(msg, live);
    const segs = deps.assistantSegments(msg);
    let lastReasonIndex = -1;
    segs.forEach((seg, index) => { if (seg && seg.type === "reasoning") lastReasonIndex = index; });
    const phase = deps.runPhase(msg);
    const streamingLast = Boolean(live && lastReasonIndex >= 0 && !segs.some((seg, index) => index > lastReasonIndex) && phase !== "calling" && phase !== "tools");
    const rows = [];

    let group = null;
    let thinking = null;
    const flush = () => {
      if (group) rows.push(group);
      group = null;
    };
    // Console: a running call (a background command too, for as long as its
    // process runs), and while the block is live its newest call, print on
    // their own row; a call folds into the line once it has ended and
    // something newer (a thought, a call, text) has arrived. Claude Code
    // folds both; Fuse never hides a command that is still running.
    let newest = -1;
    if ((blockLive)) {
      for (let k = entries.length - 1; k >= 0 && newest < 0; k -= 1) {
        if (!(entries[k].type === "tool" && deps.ccToolClass(entries[k].trace)?.kind === "silent")) newest = k;
      }
    }
    // A silent call (search_tools) stays hidden unless it was cut short: an
    // interrupted one prints on its own row, saying what the run was doing.
    const holds = (entry, at, cls) => cls.kind === "silent" ? deps.toolPresentation(entry.trace, live).interrupted
      : at === newest || deps.toolPresentation(entry.trace, live).running;
    let i = 0;
    while (i < entries.length) {
      const entry = entries[i];
      // Thinking hidden: thoughts neither print nor end a line; the status
      // line says when the model is thinking.
      if (entry.type === "reasoning" && !deps.showThinking) {
        i += 1;
        continue;
      }
      if (entry.type === "reasoning") {
        const run = [entry];
        let j = i + 1;
        while (j < entries.length && entries[j].type === "reasoning") run.push(entries[j++]);
        i = j;
        const last = run[run.length - 1];
        const streamingThought = streamingLast && last.segmentIndex === lastReasonIndex;
        // Console: a thought after calls ends the line, where Claude Code
        // ends one at narration (the API marks such thinking as narration).
        const endsLine = (group) && group.items.some((item) => item.type === "tool") &&
          (streamingThought || run.some((item) => String(item.text || "").trim()));
        if (endsLine) {
          group.endedByThought = true;
          flush();
        }
        if (streamingThought) {
          const clock = msg._thoughtClock || { liveStart: 0 };
          thinking = { since: Date.now() - Math.max(0, (deps.liveElapsedSeconds(msg, live) || 0) * 1000 - (clock.liveStart || 0)) };
          // Console: the line it ended stays settled, so the thought's
          // clock runs on a line of its own (Claude Code runs it on the
          // live line, which a thought does not end there).
          // Also after a call on its own row (running, or the newest one).
          if (endsLine || ((!group))) rows.push({ items: [], liveThought: last.segmentIndex });
          continue;
        }
        const text = run.map((item) => String(item.text || "").trim()).filter(Boolean).join("\n\n");
        if (!text) continue;
        const key = `think-${last.segmentIndex}`;
        (group ||= { items: [] }).items.push({ type: "think", key, text, ms: times[key] || 1000, segmentIndex: last.segmentIndex });
        continue;
      }
      i += 1;
      if (entry.type !== "tool") continue;
      const key = deps.toolKey(entry.trace, entry.index);
      const cls = deps.ccToolClass(entry.trace);
      if (cls && !holds(entry, i - 1, cls)) {
        (group ||= { items: [] }).items.push({ type: "tool", entry, cls, key });
        continue;
      }
      flush();
      // Console: subagents started together print as one block.
      if ((entry.trace.name === "spawn_subagent")) {
        const agents = [{ entry, key }];
        while (i < entries.length && entries[i].type === "tool" && entries[i].trace.name === "spawn_subagent") {
          const next = entries[i++];
          agents.push({ entry: next, key: deps.toolKey(next.trace, next.index) });
        }
        if (agents.length > 1) {
          rows.push({ agents });
          continue;
        }
      }
      rows.push({ entry, key });
    }
    flush();
    const lastRow = rows[rows.length - 1];
    return rows.map((row) => {
      if (row.agents) return deps.ccAgentsGroupHtml(row.agents, live);
      if (!row.items) return deps.toolStepHtml(row.entry.trace, row.key, live, deps.stepOpenAt(stepOpen, row.key), stepOpen);
      const current = row === lastRow && !row.endedByThought;
      // Console keeps calls off a live line, so only a thought makes it live.
      const active = Boolean(blockLive && current && ((row.liveThought !== undefined)));
      return ccGroupHtml(row, msg, live, active, stepOpen, current ? thinking : null);
    }).join("");
  }

const CC_PR_VERBS = { created: "created", edited: "edited", merged: "merged", commented: "commented on", closed: "closed",
    reopened: "reopened", ready: "marked ready", draft: "marked draft", "auto-merge-enabled": "enabled auto-merge on",
    "auto-merge-disabled": "disabled auto-merge on" };

const CC_LIVE_TOOLS = new Set(["run_terminal_command", "read_file", "grep", "glob", "list_dir"]);

  return { ccTurnLineHtml, ccShellHint, ccPathHint, ccActivityText, ccRunning, ccSince, ccHeadline, ccLiveText, ccElapsedText, ccShellSuffix, ccThinkClock, syncConsoleClocks, workClocksLive, syncWorkClocks, ccGroupHtml, consoleActivityEntriesHtml, CC_PR_VERBS, CC_LIVE_TOOLS };
}
