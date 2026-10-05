// Console UI audit, loaded into the isolated fixture page by
// console_ui.browser.cjs. Two parts:
//
// check(truth, opts): rules every rendered Console transcript must keep,
//   judged against the latest state of each call and command (`truth`),
//   never against a row's own saved copy. Stale "Still running" rows,
//   running calls folded into a summary line, placeholder headers, wrong
//   dot colours and doubled clocks all break a rule here.
// generate(seed): a seeded history of reads, searches, foreground and
//   background commands, Waits, monitors, kills, edits, web calls, thoughts
//   and text, then wake messages after the turn. Each step is a thread
//   snapshot with the truth at that moment, rendered live or settled.
window.ConsoleAudit = (() => {
  const RUNNING = new Set(["running"]);

  function visible(el) {
    return Boolean(el && el.isConnected && el.getClientRects().length && getComputedStyle(el).visibility !== "hidden");
  }

  function rowsOf(root) {
    return [...root.querySelectorAll("li.chat-step")].filter((li) => !li.closest(".chat-console-bundle") && !li.closest(".chat-console-agent-tree"));
  }

  function headText(li) {
    const head = li.querySelector(":scope > .chat-step-main > .chat-step-head .chat-step-label, :scope > .chat-step-main .chat-console-notice-text");
    return (head ? head.innerText : "").replace(/\s+/g, " ").trim();
  }

  function color(css) {
    const probe = document.createElement("span");
    probe.style.background = css;
    document.querySelector(".chat-log").append(probe);
    const out = getComputedStyle(probe).backgroundColor;
    probe.remove();
    return out;
  }

  function check(truth = {}, opts = {}) {
    const exec = truth.exec || {};
    const call = truth.call || {};
    const monitors = truth.monitor || {};
    const out = [];
    const add = (rule, li, detail = "") => out.push({ rule, row: li ? li.innerText.replace(/\s+/g, " ").trim().slice(0, 140) : "", detail });
    const log = document.querySelector(".chat-log");
    const white = color("var(--mist)");
    const red = color("var(--call-fail)");
    const steps = new Set();

    for (const li of rowsOf(log)) {
      if (!visible(li)) continue;
      const text = li.innerText.replace(/\s+/g, " ").trim();
      const head = headText(li);
      const execId = li.dataset.execution || "";
      const callId = (li.dataset.step || "").replace(/^tool-/, "");
      const tool = li.dataset.tool || "";
      const isCall = li.classList.contains("is-console-call");
      const isNotice = li.classList.contains("is-console-notice");
      const isGroup = li.classList.contains("is-console-group");
      const execState = exec[execId];
      const callState = call[callId];

      if (li.dataset.step && !li.closest(".chat-auto-steps")) {
        if (steps.has(li.dataset.step)) add("duplicate-row", li, li.dataset.step);
        steps.add(li.dataset.step);
      }
      if (!head && !text) add("empty-row", li);

      // Stale state: a row may say a command or call is running only while it is.
      if (/\bStill running\b/.test(text) && execState && !RUNNING.has(execState)) add("stale-still-running", li, `command ${execId} is ${execState}`);
      if (/Running in the background/.test(text) && execState && !RUNNING.has(execState)) add("stale-background", li, `command ${execId} is ${execState}`);
      if (/Waiting for output…/.test(text) && callState && callState !== "running") add("stale-waiting", li, `wait is ${callState}`);
      if (isCall && li.classList.contains("is-running")) {
        // A background command or monitor runs on after its call returned.
        const process = ["run_terminal_command", "monitor"].includes(tool) && execState !== undefined && callState === "done";
        const state = process ? execState : callState;
        if (state && state !== "running") add("stale-running-dot", li, `${process ? "command" : "call"} is ${state}`);
      }
      if (isCall && callState === "running" && opts.live && !li.classList.contains("is-running") && tool !== "spawn_subagent") {
        add("running-not-shown", li, `call ${callId} runs`);
      }

      // Notices report how a command ended; they must agree with it.
      if (isNotice && execId && execState) {
        const notice = head || text;
        if (/ completed\b/.test(notice) && execState !== "completed") add("notice-mismatch", li, `command is ${execState}`);
        if (/ failed\b/.test(notice) && execState !== "failed") add("notice-mismatch", li, `command is ${execState}`);
        if (/ was stopped\b|" stopped\b/.test(notice) && execState !== "interrupted") add("notice-mismatch", li, `command is ${execState}`);
      }
      if (isNotice && /^Monitor event:/.test(head)) {
        const description = monitors[execId];
        if (description && !head.startsWith(`Monitor event: ${description}`)) add("monitor-event-header", li, `expected the description "${description}" first`);
      }

      // Headers never fall back to placeholders.
      if (/^\w[\w ]*\((command|event|undefined|null|NaN|job)?\)/.test(head) || /\bundefined\b|\bNaN\b|\[object Object\]/.test(head) ||
          /^Monitor event: (event|monitor)\b/.test(head)) {
        add("placeholder-header", li, head);
      }

      // One clock per call, on its header, in Claude Code's duration format.
      if (isCall) {
        const clocks = li.querySelectorAll("[data-cc-clock]");
        if (clocks.length > 1) add("double-clock", li, `${clocks.length} clocks`);
        for (const took of li.querySelectorAll(".chat-console-took")) {
          const value = took.textContent;
          if (value && !/^ · (\d+s|\d+m \d+s|\d+h \d+m \d+s|\d+d \d+h \d+m)$/.test(value)) add("clock-format", li, JSON.stringify(value));
        }
        if (/\(\d+(?:\.\d+)?s(?: ·|\))/.test(li.querySelector(".chat-console-hang")?.innerText || "")) add("hang-clock", li);
      }

      // Summary lines never hold a running call and never speak in the present
      // tense unless a thought is streaming.
      if (isGroup) {
        if (li.querySelector(".chat-console-bundle li.is-running")) add("running-call-folded", li);
        if (li.classList.contains("is-running") && !/^Thinking for /.test(head)) add("live-summary-line", li, head);
        if (!head) add("empty-summary-line", li);
      }

      // Dots: finished white, failed or cut short red, running pulses.
      if (isCall || isNotice) {
        const node = li.querySelector(":scope > .chat-step-node");
        const after = node && getComputedStyle(node, "::after");
        if (after && after.content !== "none" && getComputedStyle(node).display !== "none" && !li.classList.contains("is-running")) {
          const bad = li.matches(".is-error, .is-interrupted, .is-red, .is-gold");
          const want = bad ? red : white;
          if (after.backgroundColor !== want) add("dot-colour", li, `${after.backgroundColor} for ${bad ? "a failure" : "a finished row"}`);
        }
      }
    }

    // Rhythm: every one-line row is one --console-row tall, and the gap
    // between two blocks never depends on which kinds meet (prose adds its own
    // margin on both sides, never on one).
    const probe = document.createElement("span");
    probe.style.cssText = "display:block;position:absolute;visibility:hidden;height:var(--console-row)";
    log.append(probe);
    const rowH = probe.getBoundingClientRect().height;
    probe.remove();
    for (const li of rowsOf(log)) {
      if (!visible(li) || li.classList.contains("is-think")) continue;
      const head = li.querySelector(":scope > .chat-step-main > .chat-step-head");
      const label = head && head.querySelector(".chat-step-label");
      if (!head || !label || !(li.classList.contains("is-console-call") || li.classList.contains("is-console-group"))) continue;
      if (label.getBoundingClientRect().height > rowH * 1.5) continue;
      const h = head.getBoundingClientRect().height;
      if (Math.abs(h - rowH) > 0.5) add("row-height", li, `${h.toFixed(2)}px head, ${rowH.toFixed(2)}px row`);
    }
    for (const flow of log.querySelectorAll(".chat-flow")) {
      const blocks = [...flow.children].filter((el) => visible(el) && el.matches(".chat-md, .chat-activity"));
      for (const md of blocks.filter((el) => el.matches(".chat-md"))) {
        const last = md.lastElementChild;
        if (last && getComputedStyle(last).marginBottom !== "0px") add("prose-trailing-margin", null, `${last.tagName} margin-bottom ${getComputedStyle(last).marginBottom}`);
      }
      for (let i = 1; i < blocks.length - 1; i++) {
        if (!blocks[i].matches(".chat-md")) continue;
        const above = blocks[i].getBoundingClientRect().top - blocks[i - 1].getBoundingClientRect().bottom;
        const below = blocks[i + 1].getBoundingClientRect().top - blocks[i].getBoundingClientRect().bottom;
        if (Math.abs(above - below) > 0.5) add("prose-gap", null, `${above.toFixed(2)}px above, ${below.toFixed(2)}px below`);
      }
    }
    for (const tree of log.querySelectorAll(".chat-console-agent-tree")) {
      if (!visible(tree)) continue;
      const lefts = [...tree.querySelectorAll(".chat-console-agent-sub .chat-console-dim")].map((el) => Math.round(el.getBoundingClientRect().left * 10) / 10);
      if (new Set(lefts).size > 1) add("agent-sub-column", null, lefts.join(", "));
    }
    for (const turn of log.querySelectorAll(".chat-console-turn")) {
      const text = turn.querySelector(":scope > span:not(.chat-console-turn-mark)");
      const column = log.querySelector(".chat-flow > .chat-md p, .chat-step:not(.is-think) .chat-step-label");
      if (!visible(turn) || !text || !visible(column)) continue;
      const want = column.getBoundingClientRect().left;
      const got = text.getBoundingClientRect().left;
      if (Math.abs(got - want) > 0.6) add("foot-column", null, `${got.toFixed(1)} against ${want.toFixed(1)}`);
    }

    // A thought streaming after calls has its own ticking "Thinking for" line.
    if (opts.thinkingLine && opts.thinking !== false &&
        !rowsOf(log).some((li) => visible(li) && /^Thinking for /.test(headText(li)))) add("no-live-thought-line", null);

    if (opts.thinking === false) {
      if (log.querySelector(".is-think, .chat-console-thought")) add("thinking-shown", null, "a thought row is visible");
      for (const li of rowsOf(log)) if (visible(li) && /^(Thought|Thinking) for /.test(headText(li))) add("thinking-shown", li);
    }

    const dock = document.querySelector("[data-live-dock]");
    const dockShown = Boolean(dock && !dock.hidden);
    if (opts.live && !dockShown) add("dock-missing", null);
    if (opts.live && dockShown) {
      const label = (document.querySelector("[data-live-dock-label]")?.textContent || "").trim();
      if (!/^\S.*…/.test(label)) add("dock-label", null, JSON.stringify(label));
      if (/\bundefined\b|\bNaN\b/.test(label)) add("dock-label", null, JSON.stringify(label));
    }
    if (opts.live === false && dockShown && dock.getAttribute("aria-hidden") !== "true") add("dock-stale", null, "status row shown after the run");

    if (document.documentElement.scrollWidth > innerWidth + 1) add("page-overflow", null, `${document.documentElement.scrollWidth} > ${innerWidth}`);
    return out;
  }

  // —— Generator ——
  function rng(seed) {
    let s = (seed * 2654435761) >>> 0 || 1;
    return () => {
      s ^= s << 13; s >>>= 0; s ^= s >>> 17; s ^= s << 5; s >>>= 0;
      return s / 4294967296;
    };
  }

  const ROOT = { host: "qa-box", cwd: "/srv/qa", platform: "posix", project_root: "/srv/qa" };
  const COMMANDS = [
    ["python -m pytest -q tests/", "Run the test suite"],
    ["npm run build -- --mode production --sourcemap", "Build the production bundle"],
    ["cd /srv/qa && ./scripts/promote-dev.sh 2>&1 | tail -20", ""],
    ["make -j8 all", ""],
    ["./GameServer -config=serverGS.cfg -port=2302", "Start the game server"],
    ["docker compose up --build api worker", "Bring up the stack"],
  ];
  const OUTPUT = ["tests/test_a.py ....", "Compiling 1204 modules", "✓ built in 3.2s", "listening on :8080", "WARN cache miss", "done"];

  function generate(seed) {
    const r = rng(seed);
    const pick = (list) => list[Math.floor(r() * list.length)];
    const chance = (p) => r() < p;
    let n = 0;
    const next = (prefix) => `${prefix}${seed}x${++n}`;
    const truth = { exec: {}, call: {}, monitor: {} };
    const assistant = { id: `a${seed}`, role: "assistant", content: "", reasoning: "", tools: [], segments: [], elapsedMs: 30000, model: "qa/model" };
    const thread = [{ id: `u${seed}`, role: "user", content: "Go.", reasoning: "", error: "" }, assistant];
    const steps = [];
    const background = [];
    const monitors = [];
    const snap = (live, phase, note, extra = {}) => steps.push({ rows: structuredClone(thread), live, phase, truth: structuredClone(truth), note, ...extra });

    const lines = (k) => Array.from({ length: k }, (_, i) => `${pick(OUTPUT)} ${i + 1}`).join("\n") + (k ? "\n" : "");
    const base = (name, fields = {}) => ({
      id: next("t"), name, status: "done", error: "", results: [], output: "", duration_ms: 120, call_duration_ms: 120, ...ROOT, ...fields,
    });
    const push = (trace) => {
      assistant.tools.push(trace);
      assistant.segments.push({ type: "tool", id: trace.id });
      truth.call[trace.id] = trace.status === "running" ? "running" : trace.status === "error" ? "error" : trace.status === "interrupted" ? "interrupted" : "done";
      return trace;
    };
    const settle = (trace, status) => {
      trace.status = status;
      truth.call[trace.id] = status === "error" ? "error" : status;
    };
    const text = (value) => {
      assistant.segments.push({ type: "content", text: value });
      assistant.content += value;
    };
    const think = (value, ms) => {
      assistant.segments.push({ type: "reasoning", text: value, ms });
      assistant.reasoning += value;
    };
    const shell = (fields) => {
      const job = next("j");
      return base("run_terminal_command", {
        command: fields.command, description: fields.description || undefined, background: Boolean(fields.background),
        child_id: job, command_id: job, job_id: job, timeout_ms: 120000, output: fields.output || "",
        preview: (fields.output || "").slice(0, 120), status: fields.status || "done", state: fields.state, execution_status: fields.state,
        exit_code: fields.exit_code, elapsed_ms: fields.elapsed_ms, execution_duration_ms: fields.ms, call_duration_ms: fields.ms ?? 120,
        termination_confirmed: true,
      });
    };
    const setExec = (trace, state, extra = {}) => {
      const fields = { completed: { status: "done", state: "completed" }, failed: { status: "error", state: "timed_out", error: "timed out" },
        interrupted: { status: "interrupted", state: "interrupted", error: "agent is offline" } }[state];
      Object.assign(trace, fields, { execution_status: fields.state }, extra);
      truth.exec[trace.command_id] = state;
    };

    const actions = [
      // A thought: streams (live, model phase), then lands (calling phase).
      () => {
        // After any call since the last thought or text, a live line shows it.
        const segs = assistant.segments;
        const lastThought = segs.map((seg) => seg.type).lastIndexOf("reasoning");
        const lastText = segs.map((seg) => seg.type).lastIndexOf("content");
        const afterCall = segs.slice(Math.max(lastThought, lastText) + 1).some((seg) => seg.type === "tool");
        think(`Consider step ${n}.`, 1000 + Math.floor(r() * 20000));
        snap(true, "model", "thought streaming", { thinkingLine: afterCall });
        snap(true, "calling", "thought landed");
      },
      // A read, search, glob or listing: running, then done or failed.
      () => {
        const kind = pick(["read_file", "grep", "glob", "list_dir"]);
        const fields = kind === "read_file" ? { path: `/srv/qa/app/mod${n}.py` } : kind === "list_dir" ? { path: "/srv/qa/tests" } : { pattern: `needle${n}`, path: "/srv/qa" };
        const trace = push(base(kind, { ...fields, status: "running", elapsed_ms: Math.floor(r() * 4000) }));
        snap(true, "tools", `${kind} running`);
        const failed = chance(0.15);
        settle(trace, failed ? "error" : "done");
        if (failed) trace.error = "No such file or directory";
        else trace.output = kind === "read_file" ? "1: x\n2: y\n" : kind === "list_dir" ? "a.py\nb.py\n" : `app/x.py:1:needle${n}\n`;
        trace.call_duration_ms = Math.floor(r() * 5000);
        snap(true, "model", `${kind} ended`);
      },
      // A foreground command: running at some age, then exit 0, a grep miss,
      // a failure or an interruption.
      () => {
        const [command, description] = pick(COMMANDS);
        const grep = chance(0.2);
        const trace = push(shell({ command: grep ? `grep -rn missing${n} app/` : command, description: chance(0.5) ? description : "",
          status: "running", state: "running", elapsed_ms: pick([300, 5000, 21000, 95000]), output: chance(0.5) ? lines(Math.floor(r() * 8)) : "" }));
        truth.exec[trace.command_id] = "running";
        snap(true, "tools", "command running");
        const end = grep ? "grep-miss" : pick(["ok", "ok", "fail", "interrupt"]);
        const ms = 200 + Math.floor(r() * 60000);
        if (end === "ok") { setExec(trace, "completed", { exit_code: 0, execution_duration_ms: ms, output: lines(Math.floor(r() * 12)) }); settle(trace, "done"); }
        else if (end === "grep-miss") { setExec(trace, "completed", { exit_code: 1, execution_duration_ms: ms, output: "" }); settle(trace, "done"); }
        else if (end === "fail") { setExec(trace, "failed", { execution_duration_ms: ms, output: lines(4) }); settle(trace, "error"); }
        else { setExec(trace, "interrupted"); settle(trace, "interrupted"); }
        snap(true, "model", `command ${end}`);
      },
      // A background command: the call returns at once, the process runs on.
      () => {
        const [command, description] = pick(COMMANDS);
        const trace = push(shell({ command, description: chance(0.6) ? description : "", background: true, status: "done", state: "running",
          elapsed_ms: pick([1000, 30000, 400000]), ms: 300 }));
        truth.exec[trace.command_id] = "running";
        background.push(trace);
        snap(true, "model", "background launched");
      },
      // A Wait on a running background command: returns with the command
      // done, times out while it runs, or is cut off with the run.
      () => {
        const target = background.find((t) => truth.exec[t.command_id] === "running");
        if (!target) return;
        const wait = push(base("get_command_output", { command: target.command, child_id: target.command_id, command_id: target.command_id, job_id: target.command_id,
          background: true, status: "running", state: "running", execution_status: "running", elapsed_ms: 47000, timeout_ms: 1800000 }));
        snap(true, "tools", "wait running");
        const end = pick(["finished", "timeout", "timeout", "interrupted"]);
        if (end === "finished") {
          Object.assign(wait, { status: "done", state: "completed", execution_status: "completed", exit_code: 0, output: lines(3), call_duration_ms: 31000 });
          setExec(target, "completed", { exit_code: 0, execution_duration_ms: 90000 });
          truth.call[wait.id] = "done";
        } else if (end === "timeout") {
          Object.assign(wait, { status: "done", state: "running", execution_status: "running", output: "", call_duration_ms: 30006 });
          truth.call[wait.id] = "done";
        } else {
          for (const key of ["state", "execution_status", "background", "command", "command_id", "job_id", "elapsed_ms", "timeout_ms"]) delete wait[key];
          Object.assign(wait, { status: "interrupted", preview: "still running", call_duration_ms: undefined, duration_ms: undefined });
          truth.call[wait.id] = "interrupted";
        }
        snap(true, "model", `wait ${end}`);
        // Later, the command it timed out on may end on its own.
        if (end === "timeout" && chance(0.6)) {
          setExec(target, pick(["completed", "failed", "interrupted"]), { exit_code: 0 });
          snap(true, "model", "command ended after the wait");
        }
      },
      // A monitor: returns at once and watches.
      () => {
        const job = next("m");
        const description = pick(["Game startup milestones in new log", "errors in worker.log", "deploy health checks"]);
        push(base("monitor", { description, command: "tail -F /var/log/app.log", pattern: "ERROR|SUCCESS", source: "command", event_count: 0,
          background: true, child_id: job, command_id: job, execution_status: "running", state: "running", timeout_ms: 300000 }));
        truth.exec[job] = "running";
        truth.monitor[job] = description;
        monitors.push({ job, description });
        snap(true, "model", "monitor started");
      },
      // Stop a running background command.
      () => {
        const target = background.find((t) => truth.exec[t.command_id] === "running");
        if (!target) return;
        push(base("kill_command", { child_id: target.command_id, command_id: target.command_id }));
        setExec(target, "interrupted", { error: "cancelled", termination_confirmed: true });
        snap(true, "model", "command stopped");
      },
      // Calls that never fold: edits, writes, web search and fetch.
      () => {
        const kind = pick(["search_replace", "write_file", "web_search", "read_url"]);
        const fields = kind === "search_replace" ? { path: "/srv/qa/app/config.py", preview: "@@ -1 +1 @@\n-retries = 3\n+retries = 5", output: "updated" }
          : kind === "write_file" ? { path: "/srv/qa/app/new.py", content: "print('hi')\n" }
          : kind === "web_search" ? { queries: ["asyncio timeout"], latency_ms: 2300, results: [{ title: "t", url: "https://example.com", snippet: "s" }] }
          : { url: "https://docs.python.org/3/", bytes: 12000, http_status: 200 };
        const trace = push(base(kind, { ...fields, status: "running" }));
        snap(true, "tools", `${kind} running`);
        settle(trace, "done");
        trace.call_duration_ms = Math.floor(r() * 6000);
        snap(true, "model", `${kind} done`);
      },
      // Narration.
      () => {
        text(`Step ${n} is done.\n\n`);
        snap(true, "model", "text");
      },
    ];

    const count = 5 + Math.floor(r() * 9);
    for (let i = 0; i < count; i += 1) pick(actions)();
    text("All done.");
    assistant.endedAt = new Date().toISOString();
    snap(false, "", "turn ended");

    // After the turn: wake messages report background commands and monitors.
    for (const trace of background) {
      if (truth.exec[trace.command_id] !== "running" || !chance(0.7)) continue;
      // The server's end states (command_jobs.notification); a non-zero exit
      // still "completed". Most envelopes carry output_chars; old ones did not.
      const state = pick(["completed", "completed", "completed", "timed_out", "cancelled", "interrupted"]);
      const exit = state === "completed" ? pick([0, 0, 2]) : null;
      const body = lines(2);
      thread.push({ id: next("w"), role: "user", auto: true, reasoning: "", error: "", content:
        `<command_result id="${trace.command_id}" state="${state}">\nhost: qa-box command: ${trace.command}\n` +
        (trace.description ? `description: ${trace.description}\n` : "") +
        (exit !== null ? `exit_code: ${exit}\n` : "") + `duration_ms: 64000\n` +
        (state === "timed_out" ? "error: timed out\n" : "") +
        (chance(0.8) ? `output_chars: ${body.length}\noutput_chars_exact: true\n` : "") + `\n${body}</command_result>` });
      truth.exec[trace.command_id] = state === "completed" ? "completed" : state === "timed_out" ? "failed" : "interrupted";
      snap(false, "", `wake ${state}`);
    }
    for (const { job, description } of monitors) {
      const events = Math.floor(r() * 3);
      for (let e = 1; e <= events; e += 1) {
        thread.push({ id: next("w"), role: "user", auto: true, reasoning: "", error: "", content:
          `<monitor_event id="${job}" seq="${e}">\nhost: qa-box command: tail -F /var/log/app.log\ndescription: ${description}\n\n${lines(1 + Math.floor(r() * 6))}</monitor_event>` });
        snap(false, "", "monitor event");
      }
    }
    return steps;
  }

  return { check, generate };
})();
