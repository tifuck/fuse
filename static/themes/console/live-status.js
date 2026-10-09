// live-status presentation. Dependencies are live getters supplied by index.js.
export function create(deps) {
function formatActivityClock(secondsF) {
    const total = Math.max(1, Math.round(secondsF));
    const h = Math.floor(total / 3600);
    const m = Math.floor((total % 3600) / 60);
    const s = total % 60;
    if (h) return `${h}h ${m}m ${s}s`;
    if (m) return `${m}m ${s}s`;
    return `${s}s`;
  }

function formatActivityElapsed(secondsF, live) {
    if (secondsF == null || (live && secondsF < 1)) return "";
    return ` for ${formatActivityClock(secondsF)}`;
  }

function consoleFlow(status, msg) {
    if (!status) return "down";
    if (status.phase === "thinking" || status.phase === "writing") return "down";
    if (status.phase === "stopping" || status.phase === "error") return "";
    if (status.phase === "searching" || status.phase === "reading" || status.phase === "working"
        || status.phase === "building" || status.phase === "spawning") return "up";
    if (status.current && deps.toolPresentation(status.current, true).running) return "up";
    const traces = Array.isArray(msg && msg.tools) ? msg.tools : [];
    if ((status.phase === "queued" || status.phase === "waiting") && traces.length) return "up";
    return "down";
  }

function consoleRoundKey(msg, status) {
    const traces = Array.isArray(msg && msg.tools) ? msg.tools : [];
    return `${consoleFlow(status, msg)}|${traces.length}`;
  }

function ensureConsoleRound(msg, status, meter = deps.liveMeter) {
    const key = consoleRoundKey(msg, status);
    if (msg._consoleRound && msg._consoleRound.key === key) return msg._consoleRound;
    const split = deps.usageInOut(msg && msg.usage);
    const prev = msg._consoleRound;
    const flow = consoleFlow(status, msg);
    // After a tool round the first new tokens are already on the message
    // when we notice the switch. Keep the origin snapshotted when tools began.
    const keepOrigin = prev && flow === "down" && String(prev.key).startsWith("up|");
    if (prev) delete msg.streamOut;
    const meterUsed = meter && typeof meter.used === "number" ? Math.max(0, Math.round(Number(meter.used) || 0)) : 0;
    msg._consoleRound = {
      key,
      content: keepOrigin ? prev.content : (prev ? String((msg && msg.content) || "").length : 0),
      reasoning: keepOrigin ? prev.reasoning : (prev ? String((msg && msg.reasoning) || "").length : 0),
      usageOut: keepOrigin ? prev.usageOut : (prev && split ? split.out : 0),
      meterUsed: keepOrigin ? prev.meterUsed : meterUsed,
    };
    return msg._consoleRound;
  }

function formatConsoleCount(n) {
    const tokens = Math.max(0, Math.round(Number(n) || 0));
    if (tokens >= 1_000_000 || Math.round(tokens / 1000) >= 1000) return (tokens / 1_000_000).toFixed(tokens >= 10_000_000 ? 0 : 1).replace(/\.0$/, "") + "M";
    if (tokens >= 10_000) return Math.round(tokens / 1000) + "k";
    if (tokens >= 1000) return (tokens / 1000).toFixed(1).replace(/\.0$/, "") + "k";
    return String(tokens);
  }

function consoleDownTokens(msg, round) {
    const grown = deps.estimateChars(
      String((msg && msg.content) || "").slice(round.content)
      + String((msg && msg.reasoning) || "").slice(round.reasoning)
    );
    const fromServer = Math.max(0, Math.round(Number(msg && msg.streamOut) || 0));
    let down = Math.max(grown, fromServer);
    const split = deps.usageInOut(msg && msg.usage);
    if (split && split.out > round.usageOut) down = Math.max(down, split.out - round.usageOut);
    return down;
  }

function consoleUpTokens(msg, round, meter) {
    const fromMeter = meter && typeof meter.used === "number" ? Math.max(0, Math.round(Number(meter.used) || 0)) : 0;
    if (fromMeter && round && fromMeter > (round.meterUsed || 0)) return fromMeter - (round.meterUsed || 0);
    const traces = Array.isArray(msg && msg.tools) ? msg.tools : [];
    const last = traces[traces.length - 1];
    return last ? deps.estimateTrace(last) : 0;
  }

function ccSpinnerVerb(msg) {
    const key = String((msg && msg.id) || "");
    let hash = 0;
    for (let i = 0; i < key.length; i += 1) hash = (Math.imul(hash, 31) + key.charCodeAt(i)) | 0;
    return CC_SPINNER_VERBS[(hash >>> 0) % CC_SPINNER_VERBS.length];
  }

function ccThinkingState(msg, thinking) {
    const now = Date.now();
    const clock = (msg._spinThink ||= { since: 0, flash: null });
    if (thinking) {
      if (!clock.since) {
        // The thought's own duration, streamed on its segment. Anchoring at
        // page paint restarted "still thinking" on every reload.
        const segs = Array.isArray(msg.segments) ? msg.segments : [];
        const last = [...segs].reverse().find((seg) => seg && seg.type === "reasoning");
        const ms = Number(last && last.ms);
        clock.since = Number.isFinite(ms) && ms > 0 ? now - ms : now;
      }
      clock.flash = null;
      const ms = now - clock.since;
      return ms >= 45000 ? "deep in thought" : ms >= 30000 ? "thinking some more" : ms >= 20000 ? "thinking more"
        : ms >= 10000 ? "still thinking" : "thinking";
    }
    if (clock.since) {
      clock.flash = { ms: now - clock.since, until: now + 2000 };
      clock.since = 0;
    }
    if (clock.flash && now < clock.flash.until) return `thought for ${Math.max(1, Math.round(clock.flash.ms / 1000))}s`;
    clock.flash = null;
    return "";
  }

function ccSpinnerState(status) {
    const extra = String(status.label || "").split(" · ").slice(1);
    let state = "";
    if (status.phase === "queued") state = "queued";
    else if (status.phase === "stopping") state = "stopping";
    else if (status.current) state = deps.executionState(status.current) === "cancelling" ? "cancelling"
      : deps.isSearchTool(status.current.name) ? "searching the web" : CC_SPINNER_STATES[status.current.name] || "running tool";
    else if (status.phase === "calling") state = !status.tool ? "calling tool"
      : deps.isSearchTool(status.tool) ? "searching the web" : CC_SPINNER_STATES[status.tool] || `calling ${status.tool}`;
    else if (status.phase === "waiting" || status.phase === "starting") state = "waiting";
    else if (status.phase === "working") state = "working";
    return [state, ...extra].filter(Boolean).join(" · ");
  }

function formatConsoleLiveStatus(msg, groups, opts) {
    const status = deps.currentActivity(msg, groups, true);
    const meter = opts ? opts.meter : deps.liveMeter;
    const elapsed = opts && opts.seconds != null ? opts.seconds : deps.liveElapsedSeconds(msg, true);
    const seconds = Math.max(0, Math.floor(elapsed || 0));
    const round = ensureConsoleRound(msg, status, meter);
    const flow = consoleFlow(status, msg);
    let tokenText = "";
    if (flow === "down") {
      const n = consoleDownTokens(msg, round);
      if (n) tokenText = `↓ ${formatConsoleCount(n)} tokens`;
    } else if (flow === "up") {
      const n = consoleUpTokens(msg, round, meter);
      if (n) tokenText = `↑ ${formatConsoleCount(n)} tokens`;
    }
    const thought = ccThinkingState(msg, status.phase === "thinking");
    // A tool call being written or run replaces the "thought for Ns" flash at
    // once: that is the newer activity.
    const state = status.phase === "thinking" ? thought
      : status.phase === "calling" || status.current ? ccSpinnerState(status) : thought || ccSpinnerState(status);
    // Claude Code shows the clock once there is something else to show, or
    // after 16s.
    const clock = tokenText || state || seconds > 16 ? deps.ccDuration(seconds * 1000) : "";
    const parts = [clock, tokenText, state].filter(Boolean);
    const verb = ccSpinnerVerb(msg);
    return { verb: /(…|\.\.\.)$/.test(verb) ? verb : `${verb}…`, meta: parts.length ? `(${parts.join(" · ")})` : "" };
  }

function syncActivityElapsed(article, msg, live) {
    const elapsedEls = article.querySelectorAll("[data-activity-elapsed]");
    if (!elapsedEls.length) return;
    const text = formatActivityElapsed(deps.liveElapsedSeconds(msg, live), live);
    elapsedEls.forEach((el) => {
      if (el.closest("[data-live-status]")) {
        if (el.textContent !== text) el.textContent = text;
        return;
      }
      const next = el.closest("[data-activity]")?.classList.contains("is-waiting") ? "" : text;
      if (el.textContent !== next) el.textContent = next;
    });
  }

function activitySpinSet() {
    return deps.currentRunSpin();
  }

function syncActivitySpin(article, msg, live) {
    const els = article.querySelectorAll("[data-activity-spin]");
    if (!els.length) return;
    if (!live) {
      els.forEach((el) => {
        el.replaceChildren();
        delete el.dataset.spinSet;
      });
      return;
    }
    const el = article.querySelector("[data-live-status] [data-activity-spin]") || [...els].find((node) => node.closest(".chat-activity.is-live, .chat-activity.is-waiting"));
    if (!el) return;
    deps.applySpinEl(el, activitySpinSet(msg), "live", true, msg._spinOrigin);
  }

const CC_SPINNER_VERBS = [
    "Accomplishing", "Actioning", "Actualizing", "Architecting", "Baking", "Beaming", "Beboppin'", "Befuddling",
    "Billowing", "Blanching", "Bloviating", "Boogieing", "Boondoggling", "Booping", "Bootstrapping", "Brewing", "Bunning",
    "Burrowing", "Calculating", "Canoodling", "Caramelizing", "Cascading", "Catapulting", "Cerebrating", "Channeling",
    "Choreographing", "Churning", "Clauding", "Coalescing", "Cogitating", "Combobulating", "Composing", "Computing",
    "Concocting", "Considering", "Contemplating", "Cooking", "Crafting", "Creating", "Crunching", "Crystallizing",
    "Cultivating", "Deciphering", "Deliberating", "Determining", "Dilly-dallying", "Discombobulating", "Doing", "Doodling",
    "Drizzling", "Ebbing", "Effecting", "Elucidating", "Embellishing", "Enchanting", "Envisioning", "Fermenting",
    "Fiddle-faddling", "Finagling", "Flambéing", "Flibbertigibbeting", "Flowing", "Flummoxing", "Fluttering", "Forging",
    "Forming", "Frolicking", "Frosting", "Gallivanting", "Galloping", "Garnishing", "Generating", "Gesticulating",
    "Germinating", "Gitifying", "Grooving", "Gusting", "Harmonizing", "Hashing", "Hatching", "Herding", "Honking",
    "Hullaballooing", "Hyperspacing", "Ideating", "Imagining", "Improvising", "Incubating", "Inferring", "Infusing",
    "Ionizing", "Jitterbugging", "Julienning", "Kerfuffling", "Kneading", "Leavening", "Levitating", "Lollygagging",
    "Manifesting", "Marinating", "Meandering", "Metamorphosing", "Misting", "Moonwalking", "Moseying", "Mulling",
    "Mustering", "Musing", "Nebulizing", "Nesting", "Newspapering", "Noodling", "Nucleating", "Onioning", "Orbiting",
    "Orchestrating", "Osmosing", "Perambulating", "Percolating", "Perusing", "Philosophizing", "Photosynthesizing",
    "Polishing", "Pollinating", "Pondering", "Pontificating", "Pouncing", "Precipitating", "Prestidigitating",
    "Processing", "Proofing", "Propagating", "Puttering", "Puzzling", "Quantumizing", "Razzle-dazzling", "Razzmatazzing",
    "Recombobulating", "Reticulating", "Roosting", "Ruminating", "Sautéing", "Scampering", "Schlepping", "Scurrying",
    "Seasoning", "Shenaniganing", "Shimmying", "Simmering", "Skedaddling", "Sketching", "Slithering", "Smooshing",
    "Sock-hopping", "Spelunking", "Spinning", "Sprouting", "Stewing", "Sublimating", "Swirling", "Swooping", "Symbioting",
    "Synthesizing", "Tempering", "Thinking", "Thundering", "Tinkering", "Tomfoolering", "Topsy-turvying", "Transfiguring",
    "Transmogrifying", "Transmuting", "Twisting", "Undulating", "Unfurling", "Unraveling", "Vibing", "Waddling",
    "Wandering", "Warping", "Whatchamacalliting", "Whirlpooling", "Whirring", "Whisking", "Wibbling", "Working",
    "Wrangling", "Zesting", "Zigzagging",
  ];

const CC_SPINNER_STATES = {
    run_terminal_command: "running command", get_command_output: "waiting for command", kill_command: "stopping command",
    read_file: "reading", grep: "searching", glob: "searching", list_dir: "listing", search_replace: "editing",
    write_file: "writing file", read_url: "fetching", spawn_subagent: "running agent", get_subagent_output: "waiting for agent",
    kill_subagent: "stopping agent", list_subagents: "listing agents", monitor: "watching",
    create_artifact: "building artifact", update_artifact: "building artifact", search_models: "finding models",
  };

  return { formatActivityClock, formatActivityElapsed, consoleFlow, consoleRoundKey, ensureConsoleRound, formatConsoleCount, consoleDownTokens, consoleUpTokens, ccSpinnerVerb, ccThinkingState, ccSpinnerState, formatConsoleLiveStatus, syncActivityElapsed, activitySpinSet, syncActivitySpin, CC_SPINNER_VERBS, CC_SPINNER_STATES };
}
