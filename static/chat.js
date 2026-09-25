(() => {
  const root = document.querySelector("[data-chat]");
  if (!root) return;

  const cfgEl = document.getElementById("chat-config");
  let config = { families: [], locked: [], preselect: "", tools: [], context: {}, limits: {} };
  try {
    config = JSON.parse(cfgEl ? cfgEl.textContent : "{}") || config;
  } catch (_) {}

  const thread = root.querySelector("[data-chat-thread]");
  const log = root.querySelector("[data-chat-log]");
  const form = root.querySelector("[data-chat-form]");
  const input = root.querySelector("[data-chat-input]");
  const sendBtn = root.querySelector("[data-chat-send]");
  const newBtns = [...root.querySelectorAll("[data-chat-new]")];
  const hint = root.querySelector("[data-chat-hint]");
  const pickerHost = root.querySelector("[data-chat-picker]");
  const attachBtn = root.querySelector("[data-chat-attach]");
  const fileInput = root.querySelector("[data-chat-file]");
  const attachList = root.querySelector("[data-chat-attach-list]");
  const chatBox = root.querySelector(".chat-box");
  const historyList = root.querySelector("[data-history-list]");
  const historyKicker = root.querySelector("[data-history-kicker]");
  const historySearch = root.querySelector("[data-history-search]");
  const archivedBtn = root.querySelector("[data-history-archived]");
  const projectPicker = root.querySelector("[data-project-picker]");
  const projectPickerList = root.querySelector("[data-project-picker-list]");
  const projectPickerForm = root.querySelector("[data-project-picker-form]");
  const projectPickerTitle = root.querySelector("[data-project-picker-title]");
  const projectPickerBack = root.querySelector("[data-project-picker-back]");
  const projectPickerHost = root.querySelector("[data-project-picker-host]");
  const projectPickerName = root.querySelector("[data-project-picker-name]");
  const projectPickerPath = root.querySelector("[data-project-picker-path]");
  const projectPickerBrowseBtn = root.querySelector("[data-project-picker-browse]");
  const projectPickerBrowser = root.querySelector("[data-project-picker-browser]");
  const projectPickerError = root.querySelector("[data-project-picker-error]");
  const slashMenu = root.querySelector("[data-chat-slash]");
  const slashList = root.querySelector("[data-chat-slash-list]");
  const slashKicker = slashMenu ? slashMenu.querySelector(".chat-slash-kicker") : null;
  const spellPop = root.querySelector("[data-chat-spell-pop]");
  const composeMirror = root.querySelector("[data-chat-compose-mirror]");
  const rewindPanel = root.querySelector("[data-chat-rewind]");
  const rewindList = root.querySelector("[data-chat-rewind-list]");
  const rewindClose = root.querySelector("[data-rewind-close]");
  const contextWrap = root.querySelector("[data-context-wrap]");
  const contextBtn = root.querySelector("[data-chat-context]");
  const contextFill = root.querySelector("[data-context-fill]");
  const contextPop = root.querySelector("[data-context-pop]");
  const contextPopMeter = root.querySelector("[data-context-pop-meter]");
  const contextPopParts = root.querySelector("[data-context-pop-parts]");
  const contextPopLast = root.querySelector("[data-context-pop-last]");
  const contextPopNote = root.querySelector("[data-context-pop-note]");
  const contextPopCompact = root.querySelector("[data-context-pop-compact]");
  const contextAlert = root.querySelector("[data-context-alert]");
  const contextAlertText = root.querySelector("[data-context-alert-text]");
  const contextAlertCompact = root.querySelector("[data-context-alert-compact]");
  const contextAlertDismiss = root.querySelector("[data-context-alert-dismiss]");
  const historyToggles = [...root.querySelectorAll("[data-history-toggle]")];
  const historyToggle = root.querySelector(".chat-bar [data-history-toggle]");
  const titleEl = root.querySelector("[data-chat-title]");
  const projectChip = root.querySelector("[data-project-chip]");
  const projectName = root.querySelector("[data-project-name]");
  const welcomeEl = root.querySelector("[data-chat-welcome]");
  const memoryPanel = root.querySelector("[data-chat-memory]");
  const memoryToggle = root.querySelector("[data-memory-toggle]");
  const memoryClose = root.querySelector("[data-memory-close]");
  const memoryBackdrop = root.querySelector("[data-memory-backdrop]");
  const skillsPanel = root.querySelector("[data-chat-skills]");
  const skillsToggle = root.querySelector("[data-skills-toggle]");
  const skillsClose = root.querySelector("[data-skills-close]");
  const skillsBackdrop = root.querySelector("[data-skills-backdrop]");
  const viewToggle = root.querySelector("[data-view-toggle]");
  const viewMenu = root.querySelector("[data-view-menu]");
  const viewBackdrop = root.querySelector("[data-view-backdrop]");
  const viewLabel = root.querySelector("[data-view-label]");
  if (viewMenu && viewMenu.parentElement !== document.body) document.body.append(viewMenu);
  if (viewBackdrop && viewBackdrop.parentElement !== document.body) document.body.append(viewBackdrop);
  const memoryInstructions = root.querySelector("[data-memory-instructions]");
  const memoryProfile = root.querySelector("[data-memory-profile]");
  const memoryThread = root.querySelector("[data-memory-thread]");
  const memoryProject = root.querySelector("[data-memory-project]");
  const contextTabButtons = Array.from(root.querySelectorAll("[data-context-tab]"));
  let contextTab = "instructions";
  function setContextTab(name) {
    contextTab = contextTabButtons.some((btn) => btn.getAttribute("data-context-tab") === name) ? name : "instructions";
    contextTabButtons.forEach((btn) => {
      const on = btn.getAttribute("data-context-tab") === contextTab;
      btn.classList.toggle("is-on", on);
      btn.setAttribute("aria-selected", on ? "true" : "false");
      const panel = root.querySelector('[data-context-panel="' + btn.getAttribute("data-context-tab") + '"]');
      if (panel) panel.hidden = !on;
    });
  }
  contextTabButtons.forEach((btn) => {
    btn.addEventListener("click", () => setContextTab(btn.getAttribute("data-context-tab")));
  });
  const searchChip = root.querySelector("[data-tool-search]");
  const liveDock = root.querySelector("[data-live-dock]");
  const liveDockLabel = root.querySelector("[data-live-dock-label]");
  const liveDockSpin = root.querySelector("[data-live-dock-spin]");
  let liveDockHideTimer = 0;
  let liveDockShowFrame = 0;
  let liveDockFollowFrame = 0;
  const STORE_VIEW = "fuse.chat.view.v2";
  const CHAT_VIEWS = ["cli", "compact", "console"];
  const VIEW_LABELS = {
    cli: "Terminal",
    compact: "Compact",
    console: "Console",
  };
  const artifactPanel = root.querySelector("[data-chat-artifact]");
  const artifactToggle = root.querySelector("[data-artifact-toggle]");
  const artifactClose = root.querySelector("[data-artifact-close]");
  const artifactBackdrop = root.querySelector("[data-artifact-backdrop]");
  const artifactTitleEl = root.querySelector("[data-artifact-title]");
  const artifactStatusEl = root.querySelector("[data-artifact-status]");
  const artifactVisibilityEl = root.querySelector("[data-artifact-visibility]");
  const artifactVersionWrap = root.querySelector("[data-artifact-version-wrap]");
  const artifactVersionEl = root.querySelector("[data-artifact-version]");
  const artifactOpenLink = root.querySelector("[data-artifact-open]");
  const artifactFrame = root.querySelector("[data-artifact-frame]");
  const artifactSource = root.querySelector("[data-artifact-source]");
  const artifactBuilding = root.querySelector("[data-artifact-building]");
  const artifactModeBtns = [...root.querySelectorAll("[data-artifact-mode]")];
  const agentsPanel = root.querySelector("[data-chat-agents]");
  const agentsTabs = Array.from(root.querySelectorAll("[data-agents-tab]"));
  const agentsCloseBtn = root.querySelector("[data-agents-close]");
  const agentsBackdrop = root.querySelector("[data-agents-backdrop]");
  const agentsLiveEl = root.querySelector("[data-agents-live]");
  const agentsListEl = root.querySelector("[data-agents-list]");
  const jobsListEl = root.querySelector("[data-jobs-list]");
  const completedJobs = new Map();
  const agentViewEl = root.querySelector("[data-agent-view]");
  const agentBackBtn = root.querySelector("[data-agent-back]");
  const agentTitleEl = root.querySelector("[data-agent-title]");
  const agentStatusEl = root.querySelector("[data-agent-status]");
  const agentStopBtn = root.querySelector("[data-agent-stop]");
  const agentResumeBtn = root.querySelector("[data-agent-resume]");
  const agentForceBtn = root.querySelector("[data-agent-force]");
  const queueEl = root.querySelector("[data-chat-queue]");
  const queueListEl = root.querySelector("[data-chat-queue-list]");
  const queueCountEl = root.querySelector("[data-chat-queue-count]");
  const agentLogEl = root.querySelector("[data-agent-log]");
  const agentBodyEl = root.querySelector("[data-agent-log-body]");
  const agentLiveDock = root.querySelector("[data-agent-live-dock]");
  const agentLiveSpin = root.querySelector("[data-agent-live-spin]");
  const agentLiveLabel = root.querySelector("[data-agent-live-label]");
  const agentMeterEl = root.querySelector("[data-agent-meter]");
  const agentMeterLabel = root.querySelector("[data-agent-meter-label]");
  const agentContextFill = root.querySelector("[data-agent-context-fill]");

  const STORE_MODEL = "fuse.chat.model";
  const STORE_EFFORT = "fuse.chat.effort";
  const STORE_TAB = "fuse.chat.historyTab";
  const STORE_HISTORY_WIDTH = "fuse.chat.historyWidth";
  const STORE_PROJECT_SORT = "fuse.chat.projectSort";
  const STORE_PROJECT_FILTER = "fuse.chat.projectFilter";
  const STORE_UNDO = "fuse.chat.undo.";
  const HISTORY_WIDTH = { min: 14 * 16, max: 30 * 16, default: 19 * 16 };
  const families = Array.isArray(config.families) ? config.families : [];
  function defaultReasoningFor(type) {
    if (type === "grok") return ["low", "medium", "high", "xhigh"];
    if (type === "zai") return ["none", "high"];
    if (type === "kimi") return ["low", "high", "max"];
    if (type === "mimo") return ["none", "high"];
    if (type === "ollama") return ["none", "low", "medium", "high", "max"];
    return ["none", "minimal", "low", "medium", "high", "xhigh", "max"];
  }
  function providerLogo(type) {
    return `<img class="provider-logo" src="/static/logos/${escapeHtml(type)}.svg" alt="" aria-hidden="true" draggable="false">`;
  }
  const allModels = families.flatMap((family) =>
    (family.models || []).map((model) => ({
      id: model.id,
      name: model.name || model.id,
      type: family.type,
      title: family.title,
      reasoning: Array.isArray(model.reasoning)
        ? model.reasoning
        : defaultReasoningFor(family.type),
      reasoningDefault: model.reasoning_default || "",
      contextWindow: Number(model.context_window) || 0,
    }))
  );
  const modelsById = new Map(allModels.map((model) => [model.id, model]));
  const DEVIN_EFFORTS = ["none", "minimal", "low", "medium", "high", "xhigh", "max"];
  let effortByModel = {};
  try {
    effortByModel = JSON.parse(localStorage.getItem(STORE_EFFORT) || "{}") || {};
  } catch (_) {
    effortByModel = {};
  }

  function resolveListedModel(id) {
    const wanted = String(id || "").trim();
    if (!wanted) return "";
    if (modelsById.has(wanted)) return wanted;
    if (!wanted.startsWith("devin/")) return "";
    const parts = wanted.slice("devin/".length).split("-").filter(Boolean);
    while (parts.length && (parts[parts.length - 1] === "usa" || parts[parts.length - 1] === "priority")) {
      parts.pop();
    }
    let familyTail = "";
    if (parts.length && parts[parts.length - 1] === "1m") {
      familyTail = parts.pop();
    }
    if (parts.length >= 2 && parts[parts.length - 1] === "fast" && DEVIN_EFFORTS.includes(parts[parts.length - 2])) {
      parts.pop();
    }
    let effort = "";
    if (parts.length && DEVIN_EFFORTS.includes(parts[parts.length - 1])) {
      effort = parts.pop();
    }
    if (familyTail) parts.push(familyTail);
    const base = "devin/" + parts.join("-");
    if (!modelsById.has(base)) return "";
    if (effort) {
      const model = modelsById.get(base);
      const efforts = (model && model.reasoning) || [];
      let mapped = effort;
      if (!efforts.includes(mapped) && (effort === "none" || effort === "minimal") && efforts.includes("low")) {
        mapped = "low";
      }
      if (efforts.includes(mapped)) effortByModel[base] = mapped;
    }
    return base;
  }

  let modelId = pickInitialModel();
  let sessionId = newSession();
  let messages = [];
  let contextMessages = [];
  let contextLive = false;
  let undoStack = [];
  let redoStack = [];
  let slashIndex = 0;
  let slashKind = "slash";
  let paletteTrigger = null;
  let paletteItems = [];
  let projectListingCache = null;
  let liveMeter = null;
  let meterAnchor = null;
  let contextRecountAt = 0;
  let compacting = false;
  let compactingRequest = false;
  let titleRegenRequest = false;
  let contextWarned = false;
  let contextAlertHidden = false;
  let streaming = false;
  let activeRun = null;
  let serverRevision = 0;
  let contextRevision = Number(config.context?.revision || 0);
  let contextEdit = 0;
  let contextSaving = null;
  let sending = false;
  let loading = false;
  let healRunId = "";
  let stoppingRunId = "";
  let pendingTurn = null;
  let pendingCreate = null;
  let pendingProject = null;
  let viewCursor = 0;
  // Per-thread event watermark echoed for the reconcile short-circuit.
  // viewCursor stays user-global (snapshot + event guards); this one only
  // moves for events of the open thread, so other threads' subagents
  // streaming cannot defeat the unchanged-snapshot fast path.
  let threadWatermark = 0;
  let cursor = 0;
  let socket = null;
  let reconnectTimer = 0;
  let dockResizeObserver = null;
  let reconnectAttempt = 0;
  let syncEpoch = 0;
  let loadEpoch = 0;
  let navigation = 0;
  let syncState = "connecting";
  let syncLabel = "Connecting";
  let syncInitialized = false;
  let heartbeatTimer = 0;
  let pendingDeletes = new Set();
  let pendingSave = null;
  let pendingContext = null;
  let searchActiveQuery = "";
  let searchThreads = [];
  let searchArchived = [];
  let searchProjects = [];
  let searchTimer = 0;
  let searchGen = 0;
  const HEARTBEAT_MS = 35000;
  const AGE_TICK_MS = 30000;
  let liveClockTimer = 0;
  let runSpinOrigin = 0;
  let runSpinTimer = 0;
  let ageTimer = 0;
  const ACTIVITY_SPINNERS = [
    { id: "ink-runner", html: '<svg class="motion-art loader-ink-runner" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" aria-hidden="true" focusable="false"><g class="loader-icon"><path class="loader-run" pathLength="100" d="M32 32C23 18 10 17 10 32S23 46 32 32 54 17 54 32 41 46 32 32Z"/></g></svg>' },
    { id: "time-turn", html: '<svg class="motion-art loader-time-turn" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" aria-hidden="true" focusable="false"><g class="loader-icon"><g class="loader-rotate"><path class="loader-glass" pathLength="100" d="M18 14H46C46 23 36 26 36 32S46 41 46 50H18C18 41 28 38 28 32S18 23 18 14Z"/></g></g></svg>' },
    { id: "time-exchange", html: '<svg class="motion-art loader-time-exchange" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" aria-hidden="true" focusable="false"><g class="loader-icon"><path d="M17 13H47L32 29ZM17 51H47L32 35Z"/><path class="loader-solid loader-upper" d="M18 14H46L32 28Z"/><path class="loader-solid loader-lower" d="M18 50H46L32 36Z"/></g></svg>' },
    { id: "cardan-precession", html: '<svg class="motion-art loader-cardan-precession" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" aria-hidden="true" focusable="false"><g class="loader-icon"><g class="loader-precess"><g transform="rotate(-40 32 32)"><ellipse class="loader-orbit" pathLength="100" cx="32" cy="32" rx="23" ry="14"/></g><g transform="rotate(40 32 32)"><ellipse class="loader-orbit loader-orbit-second" pathLength="100" cx="32" cy="32" rx="23" ry="14"/></g></g></g></svg>' },
    { id: "cardan-bands", html: '<svg class="motion-art loader-cardan-bands" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" aria-hidden="true" focusable="false"><g class="loader-icon"><g transform="rotate(-45 32 32)"><ellipse class="loader-band" pathLength="100" cx="32" cy="32" rx="23" ry="13"/></g><g transform="rotate(45 32 32)"><ellipse class="loader-band loader-band-second" pathLength="100" cx="32" cy="32" rx="23" ry="13"/></g></g></svg>' },
    { id: "cardan-sphere", html: '<svg class="motion-art loader-cardan-sphere" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" aria-hidden="true" focusable="false"><g class="loader-icon"><g class="loader-sphere-turn"><circle class="loader-equator" pathLength="100" cx="32" cy="32" r="23"/><ellipse class="loader-meridian" pathLength="100" cx="32" cy="32" rx="11" ry="23"/></g></g></svg>' },
    { id: "portal-chase", html: '<svg class="motion-art loader-portal-chase" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" aria-hidden="true" focusable="false"><g class="loader-icon"><g class="loader-square-outer"><path class="loader-square-run" pathLength="100" d="M16 16H48V48H16Z"/></g><g class="loader-square-inner"><path class="loader-square-run" pathLength="100" d="M24 24H40V40H24Z"/></g></g></svg>' },
    { id: "portal-corners", html: '<svg class="motion-art loader-portal-corners" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" aria-hidden="true" focusable="false"><g class="loader-icon"><path class="loader-corner" style="--loader-phase:0s" d="M13 27V13H27"/><path class="loader-corner" style="--loader-phase:-.9s" d="M37 13H51V27"/><path class="loader-corner" style="--loader-phase:-.6s" d="M51 37V51H37"/><path class="loader-corner" style="--loader-phase:-.3s" d="M27 51H13V37"/></g></svg>' },
    { id: "solar-iris", html: '<svg class="motion-art loader-solar-iris" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" aria-hidden="true" focusable="false"><g class="loader-icon"><g class="loader-iris-turn"><g transform="rotate(0 32 32)"><path class="loader-solid loader-iris-blade" d="M32 9A23 23 0 0 1 50 18L39 30L34 22Z"/></g><g transform="rotate(60 32 32)"><path class="loader-solid loader-iris-blade" d="M32 9A23 23 0 0 1 50 18L39 30L34 22Z"/></g><g transform="rotate(120 32 32)"><path class="loader-solid loader-iris-blade" d="M32 9A23 23 0 0 1 50 18L39 30L34 22Z"/></g><g transform="rotate(180 32 32)"><path class="loader-solid loader-iris-blade" d="M32 9A23 23 0 0 1 50 18L39 30L34 22Z"/></g><g transform="rotate(240 32 32)"><path class="loader-solid loader-iris-blade" d="M32 9A23 23 0 0 1 50 18L39 30L34 22Z"/></g><g transform="rotate(300 32 32)"><path class="loader-solid loader-iris-blade" d="M32 9A23 23 0 0 1 50 18L39 30L34 22Z"/></g></g></g></svg>' },
    { id: "solar-wedges", html: '<svg class="motion-art loader-solar-wedges" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" aria-hidden="true" focusable="false"><g class="loader-icon"><g transform="rotate(0 32 32)"><path class="loader-solid loader-wedge" style="--loader-phase:0s" d="M35 9A23 23 0 0 1 50.4 18.2L40.2 25.9A10.2 10.2 0 0 0 35 22.2Z"/></g><g transform="rotate(60 32 32)"><path class="loader-solid loader-wedge" style="--loader-phase:-.167s" d="M35 9A23 23 0 0 1 50.4 18.2L40.2 25.9A10.2 10.2 0 0 0 35 22.2Z"/></g><g transform="rotate(120 32 32)"><path class="loader-solid loader-wedge" style="--loader-phase:-.333s" d="M35 9A23 23 0 0 1 50.4 18.2L40.2 25.9A10.2 10.2 0 0 0 35 22.2Z"/></g><g transform="rotate(180 32 32)"><path class="loader-solid loader-wedge" style="--loader-phase:-.5s" d="M35 9A23 23 0 0 1 50.4 18.2L40.2 25.9A10.2 10.2 0 0 0 35 22.2Z"/></g><g transform="rotate(240 32 32)"><path class="loader-solid loader-wedge" style="--loader-phase:-.667s" d="M35 9A23 23 0 0 1 50.4 18.2L40.2 25.9A10.2 10.2 0 0 0 35 22.2Z"/></g><g transform="rotate(300 32 32)"><path class="loader-solid loader-wedge" style="--loader-phase:-.833s" d="M35 9A23 23 0 0 1 50.4 18.2L40.2 25.9A10.2 10.2 0 0 0 35 22.2Z"/></g></g></svg>' },
    { id: "solar-turbine", html: '<svg class="motion-art loader-solar-turbine" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" aria-hidden="true" focusable="false"><g class="loader-icon"><g class="loader-turbine-turn"><g transform="rotate(0 32 32)"><path class="loader-solid loader-vane" d="M29 27C19 28 13 22 15 16C25 9 39 12 40 23L35 29Q32 25 29 27Z"/></g><g transform="rotate(120 32 32)"><path class="loader-solid loader-vane" d="M29 27C19 28 13 22 15 16C25 9 39 12 40 23L35 29Q32 25 29 27Z"/></g><g transform="rotate(240 32 32)"><path class="loader-solid loader-vane" d="M29 27C19 28 13 22 15 16C25 9 39 12 40 23L35 29Q32 25 29 27Z"/></g></g></g></svg>' },
    { id: "fluid-eight", html: '<svg class="motion-art loader-fluid-eight" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" aria-hidden="true" focusable="false"><g class="loader-icon"><path class="loader-fluid-path" pathLength="100" d="M32 8C45.25 8 56 18.75 56 32C56 45.25 45.25 56 32 56C18.75 56 8 45.25 8 32C8 18.75 18.75 8 32 8Z"/></g></svg>' },
    { id: "elastic-orbit", html: '<svg class="motion-art loader-elastic-orbit" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" aria-hidden="true" focusable="false"><g class="loader-icon"><g class="loader-elastic-turn"><rect class="loader-elastic-path" x="13" y="13" width="38" height="38" rx="12" pathLength="100"/></g></g></svg>' },
    { id: "braided-current", html: '<svg class="motion-art loader-braided-current" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" aria-hidden="true" focusable="false"><g class="loader-icon"><g class="loader-braid-turn"><path class="loader-braid-path" pathLength="100" d="M32 9C41 9 59 42 52 48C45 54 9 54 10 43C11 32 23 9 32 9Z"/><path class="loader-braid-path loader-braid-second" pathLength="100" d="M32 9C41 9 59 42 52 48C45 54 9 54 10 43C11 32 23 9 32 9Z"/></g></g></svg>' },
    { id: "shutter-drive", html: '<svg class="motion-art loader-shutter-drive" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" aria-hidden="true" focusable="false"><g class="loader-icon"><g class="loader-shutter-turn"><g transform="rotate(0 32 32)"><path class="loader-solid loader-shutter-blade" d="M31 9C43 8 51 16 53 26L36 26L30 20Z"/></g><g transform="rotate(120 32 32)"><path class="loader-solid loader-shutter-blade" d="M31 9C43 8 51 16 53 26L36 26L30 20Z"/></g><g transform="rotate(240 32 32)"><path class="loader-solid loader-shutter-blade" d="M31 9C43 8 51 16 53 26L36 26L30 20Z"/></g></g></g></svg>' },
  ];
  ACTIVITY_SPINNERS.push(...(window.FuseSpinnerNext || []));
  ACTIVITY_SPINNERS.push(...(window.FuseSpinnerFuture || []));
  const SPINNER_HOLD_MS = 8000;
  const SPINNER_MEMORY = 5;
  const SPINNER_STILL = '<span class="cas-still" aria-hidden="true"></span>';
  let spinWindow = -1;
  let currentSpin = null;
  const recentSpinIds = [];
  function pickRandomSpin() {
    const pool = ACTIVITY_SPINNERS.filter((s) => !recentSpinIds.includes(s.id));
    const source = pool.length ? pool : ACTIVITY_SPINNERS;
    return source[Math.floor(Math.random() * source.length)];
  }
  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
  const pickerSheetQuery = window.matchMedia("(max-width: 800px)");
  const overlayMobile = pickerSheetQuery;
  let overlayStack = [];
  let overlayPopLock = false;
  let overlayGhostUntil = 0;
  let overlayGhostRect = null;
  function overlayIsMobile() {
    return overlayMobile.matches;
  }
  function armOverlayGhost(el) {
    if (!overlayIsMobile()) {
      overlayGhostUntil = 0;
      overlayGhostRect = null;
      return;
    }
    overlayGhostUntil = performance.now() + 420;
    if (el && el.getBoundingClientRect) {
      const r = el.getBoundingClientRect();
      overlayGhostRect = { left: r.left - 8, right: r.right + 8, top: r.top - 8, bottom: r.bottom + 8 };
    } else {
      overlayGhostRect = null;
    }
  }
  function overlayPush(kind) {
    if (!overlayIsMobile()) return;
    if (overlayStack[overlayStack.length - 1] === kind) return;
    overlayStack.push(kind);
    overlayPopLock = true;
    history.pushState({ fuseOverlay: kind }, "");
    queueMicrotask(() => { overlayPopLock = false; });
  }
  function overlayDrop(kind) {
    const idx = overlayStack.lastIndexOf(kind);
    if (idx < 0) return;
    overlayStack.splice(idx, 1);
    if (overlayIsMobile() && history.state && history.state.fuseOverlay === kind) {
      const prev = overlayStack[overlayStack.length - 1];
      history.replaceState(prev ? { fuseOverlay: prev } : (history.state && history.state.fuseChat ? { fuseChat: true } : {}), "");
    }
  }
  function overlayCloseKind(kind, fromPop) {
    if (kind === "view") {
      if (fromPop) closeViewMenuDom();
      else setViewMenuOpen(false);
    } else if (kind === "memory") setMemoryOpen(false);
    else if (kind === "skills") setSkillsOpen(false);
    else if (kind === "perms") setPermsOpen(false);
    else if (kind === "agents") setAgentsOpen(false);
    else if (kind === "artifact") setArtifactOpen(false);
    else if (kind === "picker") closePicker();
    else if (kind === "history") setHistoryOpen(false);
    else if (kind === "rewind") closeRewind();
    else if (kind === "agent") closeAgentView();
  }
  document.addEventListener("click", (event) => {
    if (event.detail === 0) return; // Keyboard/programmatic activation is not a touch ghost.
    if (!overlayIsMobile() || performance.now() > overlayGhostUntil) return;
    if (event.target.closest?.(".chat-menu-close, [data-picker-close], [data-memory-close], [data-skills-close], [data-perms-close], [data-agents-close], [data-artifact-close], [data-view-close], [data-account-close], [data-rewind-close], [data-agent-back]")) return;
    const r = overlayGhostRect;
    if (r && (event.clientX < r.left || event.clientX > r.right || event.clientY < r.top || event.clientY > r.bottom)) return;
    event.preventDefault();
    event.stopPropagation();
  }, true);
  document.addEventListener("touchend", (event) => {
    const close = event.target.closest?.(".chat-menu-close, [data-picker-close], [data-memory-close], [data-skills-close], [data-agents-close], [data-artifact-close], [data-view-close], [data-account-close], [data-rewind-close], [data-agent-back]");
    if (!close) return;
    event.preventDefault();
    close.click();
  }, { capture: true, passive: false });
  let renaming = false;
  let renameSaving = null;
  let transcriptDirty = false;
  const drafts = new Map();
  let stickToBottom = true;
  let pickerOpen = false;
  let pickerFilter = "";
  let pickerIndex = 0;
  let pickerMenuEl = null;
  let pendingAttachments = [];
  let threads = [];
  let archivedThreads = [];
  let projects = [];
  let pickerHosts = [];
  let pickerMode = "list";
  let pickerBrowse = null;
  let currentId = null;
  let showArchived = false;
  let historyQuery = "";
  let historyTab = "chats";
  let projectSort = "recent";
  let projectFilter = "";
  try {
    historyTab = localStorage.getItem(STORE_TAB) === "projects" ? "projects" : "chats";
    projectSort = localStorage.getItem(STORE_PROJECT_SORT) === "project" ? "project" : "recent";
    projectFilter = localStorage.getItem(STORE_PROJECT_FILTER) || "";
  } catch (_) {
    historyTab = "chats";
  }
  let saveTimer = 0;
  let editingIndex = -1;
  let editingFiles = [];
  let lightbox = null;
  let pop = null;
  let searchOn = true;
  let instructions = (config.context && config.context.instructions) || "";
  let profile = (config.context && config.context.profile) || "";
  let threadContext = "";
  let memoryOpen = false;
  let memoryTimer = 0;
  let generation = 0;
  let revision = 0;
  let dirty = false;
  let memoryDirty = false;
  let projectAgentsOk = false;
  let hintTimer = 0;
  let hintFrame = 0;
  let ignoreScroll = false;
  let artifactOpen = false;
  let artifactMode = "preview";
  let currentArtifact = null;
  let skillsOpen = false;
  let chatView = "cli";
  try {
    const storedView = localStorage.getItem(STORE_VIEW);
    if (CHAT_VIEWS.includes(storedView)) chatView = storedView;
  } catch (_) {}
  let artifactSourceLoaded = "";
  let agentsOpen = false;
  let agentsRows = [];
  let agentViewId = "";
  let agentLastEventAt = 0;
  let _agentTickRenderAt = 0;
  let _syncViewSent = "";
  function sendSyncView(runId) {
    const next = String(runId || "");
    if (_syncViewSent === next) return;
    if (socket && socket.readyState === WebSocket.OPEN) {
      try {
        socket.send(JSON.stringify({ type: "view", run_id: next || null }));
        _syncViewSent = next;
      } catch (_) {}
    }
  }
  let agentRun = null;
  let agentPollRequest = null;
  let agentPollAgain = false;
  let agentPendingEvents = [];
  let agentMsgState = null;
  let agentsTimer = 0;
  let agentsPollTick = 0;
  let agentsRefreshAt = 0;
  let agentsViewGen = 0;
  let agentsRequest = null;
  let agentsRefreshAgain = false;
  let agentLifecycleEpoch = 0;
  const agentLifecycle = new Map();
  let agentTraceRenderAt = 0;
  let agentsFailCount = 0;
  let agentViewFailCount = 0;
  let agentStick = true;
  let agentIgnoreScroll = false;
  let agentFollowFrame = 0;
  let agentTailInitialized = false;
  let agentLastScrollTop = 0;
  let agentResumeOnScroll = true;

  const VAULT_WHAT = "Unlock machines to use SSH. Your account password unwraps those secrets here — chat still works without it.";

  function showVaultUnlock(message, opts) {
    const force = opts && opts.force;
    if (!force && !config.ssh_hosts) return;
    if (window.fuseUI) {
      window.fuseUI.vaultPrompt({
        message: VAULT_WHAT,
        error: message || "",
        onUnlocked: () => {
          config.vault = "unlocked";
        },
      });
    }
  }

  function hideVaultUnlock() {
    if (window.fuseUI) window.fuseUI.closeVault();
  }

  if (config.vault === "locked" && config.ssh_hosts && window.fuseUI) {
    window.fuseUI.vaultGate({
      uid: config.uid,
      vault: config.vault,
      hasSsh: true,
      message: VAULT_WHAT,
      onUnlocked: () => {
        config.vault = "unlocked";
      },
    });
  }

  function csrfToken() {
    const el = document.querySelector('meta[name="csrf-token"]');
    return (el && el.getAttribute("content")) || "";
  }

  async function api(path, opts) {
    opts = opts || {};
    const headers = { "X-CSRF-Token": csrfToken(), ...(opts.headers || {}) };
    if (opts.body && !headers["Content-Type"]) headers["Content-Type"] = "application/json";
    let res;
    try {
      res = await fetch(path, { ...opts, headers });
    } catch (_) {
      if (!isLive()) markDisconnected();
      const netError = new Error("Can't connect to the backend");
      netError.status = 0;
      throw netError;
    }
    const data = await res.json().catch(() => ({}));
    const errType = data.error && data.error.type;
    const nestedType = data.detail && data.detail.error && data.detail.error.type;
    if (res.status === 401 && (errType === "vault_locked" || nestedType === "vault_locked")) {
      showVaultUnlock("Unlock machines to use SSH.", { force: true });
      const vaultError = new Error((data.error && data.error.message) || "Unlock your machines");
      vaultError.status = 401;
      vaultError.vaultLocked = true;
      throw vaultError;
    }
    if (res.status === 401 || (res.status === 403 && data.error && data.error.type === "auth")) {
      window.location.assign("/login");
      const authError = new Error("Your session expired. Redirecting to sign in.");
      authError.status = res.status;
      throw authError;
    }
    if (!res.ok) {
      const detail = data.detail;
      const msg = typeof detail === "string" ? detail : typeof data.error === "string" ? data.error : data.error && data.error.message;
      const requestError = new Error(msg || `Request failed (${res.status})`);
      requestError.status = res.status;
      throw requestError;
    }
    return data;
  }

  function isLive() {
    return syncState === "live" && socket && socket.readyState === WebSocket.OPEN;
  }

  function requireLive() {
    if (isLive()) return true;
    setHint("Can't connect to the backend", true);
    return false;
  }

  function markDisconnected() {
    if (syncState === "session") return;
    syncState = navigator.onLine ? "reconnecting" : "offline";
    syncLabel = navigator.onLine ? "Reconnecting" : "Disconnected";
    syncStatus();
  }

  function isTransportError(error) {
    return !error || !error.status;
  }

  function newSession() {
    if (crypto.randomUUID) return crypto.randomUUID();
    return "c" + Math.random().toString(36).slice(2) + Date.now().toString(36);
  }

  function pickInitialModel() {
    const wanted = resolveListedModel((config.preselect || "").trim());
    if (wanted) return wanted;
    try {
      const stored = resolveListedModel(localStorage.getItem(STORE_MODEL) || "");
      if (stored) return stored;
    } catch (_) {}
    const preferred = ["grok", "chatgpt", "llama", "zai", "kimi", "mimo", "ollama", "openrouter"];
    for (const type of preferred) {
      const found = allModels.find((model) => model.type === type);
      if (found) return found.id;
    }
    return allModels[0] ? allModels[0].id : "";
  }

  function escapeHtml(value) {
    return String(value || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function safeUrl(raw) {
    const url = String(raw || "").trim();
    if (/^https?:\/\//i.test(url) || /^mailto:/i.test(url)) return url;
    if (/^\/a\/[A-Za-z0-9_-]+(?:\/(?:content|download))?$/.test(url)) return url;
    if (url === "/artifacts") return url;
    return "";
  }

  function sourceCatalog(msg) {
    const urls = [];
    const seen = new Set();
    const add = (raw) => {
      const url = safeUrl(raw);
      if (!url || !/^https?:/i.test(url) || seen.has(url)) return;
      seen.add(url);
      urls.push(url);
    };
    (msg && msg.tools ? msg.tools : []).forEach((trace) => {
      (trace.results || []).forEach((row) => add(row && row.url));
      if (trace.name === "read_url") add(trace.url);
    });
    return urls;
  }

  function citeHost(url) {
    try {
      return new URL(url).hostname.replace(/^www\./, "") || url;
    } catch (_) {
      return url;
    }
  }

  function citeUrlFromPayload(payload, catalog) {
    const parts = String(payload || "")
      .split(/\uE202/)
      .map((part) => part.trim())
      .filter(Boolean);
    for (const part of parts) {
      const url = safeUrl(part);
      if (url && /^https?:/i.test(url)) return url;
    }
    const joined = parts.join(" ");
    const indexed = joined.match(/turn\d+(?:search|news|ref)(\d+)/i) || joined.match(/(?:search|news|ref)(\d+)/i);
    if (indexed && catalog && catalog.length) {
      const url = catalog[Number(indexed[1])];
      if (url) return url;
    }
    return "";
  }

  function rewriteCites(text, catalog, streaming) {
    let out = String(text || "").replace(/[\uE200\uE206]cite\uE202([^\uE201]*)\uE201/gi, (_, payload) => {
      const url = citeUrlFromPayload(payload, catalog);
      return url ? `[${citeHost(url)}](${url})` : "";
    });
    if (streaming) out = out.replace(/[\uE200\uE206]cite(?:\uE202[^\uE201]*)?$/i, "");
    else out = out.replace(/[\uE200-\uE20F]/g, "");
    out = out.replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)(?:\s+\2)?/g, "[$1]($2)");
    return out;
  }

  function linkHtml(url, label, cls) {
    const art = url.match(/^\/a\/([A-Za-z0-9_-]+)$/);
    if (art) {
      return `<a class="chat-md-artifact" href="${escapeHtml(url)}" data-open-artifact="${escapeHtml(art[1])}">${label}</a>`;
    }
    let klass = cls || "";
    if (!klass && /^https?:/i.test(url)) {
      try {
        const host = new URL(url).hostname.replace(/^www\./, "");
        if (label === host || label === url) klass = "chat-md-cite";
      } catch (_) {}
    }
    const attr = klass ? ` class="${klass}"` : "";
    const extra = /^mailto:/i.test(url) ? "" : ' target="_blank" rel="noopener noreferrer"';
    return `<a${attr} href="${escapeHtml(url)}"${extra}>${label}</a>`;
  }

  function autolinkHtml(src) {
    const held = [];
    let out = String(src || "").replace(/<a\b[^>]*>[\s\S]*?<\/a>|<img\b[^>]*>/gi, (tag) => {
      held.push(tag);
      return `%%L${held.length - 1}%%`;
    });
    out = out.replace(/(^|[\s>(])((?:https?:\/\/|mailto:)[^\s<]+)(?=$|[\s<])/gi, (all, lead, raw) => {
      let href = raw.replace(/&amp;/g, "&");
      let trail = "";
      while (/[),.;:!?]$/.test(href) && !/[/)]]$/.test(href)) {
        trail = href.slice(-1) + trail;
        href = href.slice(0, -1);
      }
      const url = safeUrl(href);
      if (!url) return all;
      return lead + linkHtml(url, escapeHtml(href)) + trail;
    });
    return out.replace(/%%L(\d+)%%/g, (_, i) => held[Number(i)] || "");
  }

  function renderInline(text) {
    const codes = [];
    let src = String(text || "").replace(/`([^`]+)`/g, (_, code) => {
      codes.push(code);
      return `%%C${codes.length - 1}%%`;
    });
    src = escapeHtml(src);
    src = src.replace(/!\[([^\]]*)\]\(([^)]+)\)/g, (_, alt, href) => {
      const url = safeUrl(href.replace(/&amp;/g, "&"));
      if (!url || !/^https?:/i.test(url)) return alt;
      return `<img src="${escapeHtml(url)}" alt="${alt}" loading="lazy">`;
    });
    src = src.replace(/\[([^\]]+)\]\(([^)]+)\)/g, (_, label, href) => {
      const url = safeUrl(href.replace(/&amp;/g, "&"));
      if (!url) return label;
      return linkHtml(url, label);
    });
    src = autolinkHtml(src);
    src = src.replace(/~~([^~]+)~~/g, "<del>$1</del>");
    src = src.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
    src = src.replace(/__([^_]+)__/g, "<strong>$1</strong>");
    src = src.replace(/(^|[^\w])\*([^*\n]+)\*(?!\*)/g, "$1<em>$2</em>");
    src = src.replace(/(^|[^\w])_([^_\n]+)_(?!_)/g, "$1<em>$2</em>");
    src = src.replace(/%%C(\d+)%%/g, (_, i) => `<code>${escapeHtml(codes[Number(i)] || "")}</code>`);
    return src;
  }

  const ACTION_ICONS = {
    copy: '<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><rect x="5.5" y="5.5" width="7" height="8" rx="1.3" fill="none" stroke="currentColor" stroke-width="1.4"/><path d="M10.5 5.5V4.2A1.2 1.2 0 0 0 9.3 3H4.2A1.2 1.2 0 0 0 3 4.2v7.1A1.2 1.2 0 0 0 4.2 12.5H5.5" fill="none" stroke="currentColor" stroke-width="1.4"/></svg>',
    copied: '<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><path d="M3.6 8.6l3 3 5.8-6.6" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    edit: '<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><path d="M9.2 3.4l3.4 3.4L6 13.4H2.6v-3.4zM8.1 4.5l3.4 3.4" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round"/></svg>',
    retry: '<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><path d="M12.6 8A4.6 4.6 0 1 1 11.3 4.2" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/><path d="M11.2 2.4v2.6h-2.6" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    more: '<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><circle cx="4" cy="8" r="1.05" fill="currentColor"/><circle cx="8" cy="8" r="1.05" fill="currentColor"/><circle cx="12" cy="8" r="1.05" fill="currentColor"/></svg>',
    rewind: '<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><path d="M3.4 8a4.6 4.6 0 1 0 1.4-3.3" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/><path d="M3.2 3.4v2.7h2.7" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  };

  function trimFence(code) {
    const lines = String(code || "").replace(/^\n+/, "").replace(/\n+$/, "").split("\n");
    let pad = null;
    for (const line of lines) {
      if (!line.trim()) continue;
      const n = (line.match(/^[ \t]*/) || [""])[0].length;
      pad = pad === null ? n : Math.min(pad, n);
      if (pad === 0) break;
    }
    if (!pad) return lines.join("\n");
    return lines.map((line) => line.slice(pad)).join("\n");
  }

  function codeLanguage(lang) {
    const name = String(lang || "").toLowerCase().split(/\s+/)[0];
    const aliases = {
      "c++": "cpp", cc: "cpp", cxx: "cpp", hpp: "cpp",
      cs: "csharp", html: "xml", py3: "python", python3: "python",
      jsonc: "json", node: "javascript", postgres: "sql", postgresql: "sql",
      sqlite: "sql", console: "shell", patch: "diff", udiff: "diff",
    };
    return aliases[name] || name;
  }

  // Highlight memoization: streaming re-renders a message on nearly every
  // frame, and settled fences never change once closed. Re-parsing them with
  // hljs every time is the dominant O(message) cost per event; string-keyed
  // map hits cost a cached hash plus one memcmp.
  const _hljsCache = new Map();
  const _HLJS_CACHE_MAX = 600;

  function highlightCode(lang, code) {
    const source = String(code || "");
    const language = codeLanguage(lang);
    if (!window.hljs || !language || source.length > 100000 || !window.hljs.getLanguage(language)) {
      return escapeHtml(source);
    }
    const key = language + "\u0000" + source;
    const cached = _hljsCache.get(key);
    if (cached !== undefined) return cached;
    let html;
    try {
      html = window.hljs.highlight(source, { language, ignoreIllegals: true }).value;
    } catch (_) {
      return escapeHtml(source);
    }
    if (_hljsCache.size >= _HLJS_CACHE_MAX) {
      _hljsCache.clear();
    }
    _hljsCache.set(key, html);
    return html;
  }

  function renderFence(lang, code) {
    const label = escapeHtml((lang || "code").split(/\s+/)[0] || "code");
    return `<div class="chat-code"><div class="chat-code-head"><span>${label}</span><button type="button" class="chat-icon-btn" data-copy-code title="Copy" aria-label="Copy">${ACTION_ICONS.copy}</button></div><pre><code>${highlightCode(lang, trimFence(code))}</code></pre></div>`;
  }

  function parseTable(block) {
    const lines = block.split("\n").filter((line) => line.trim());
    if (lines.length < 2 || !lines[0].includes("|")) return "";
    const cells = (line) =>
      line
        .replace(/^\s*\|/, "")
        .replace(/\|\s*$/, "")
        .split("|")
        .map((cell) => cell.trim());
    const isSep = (line) => /^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$/.test(line) || /^\s*\|?\s*:?-+\s*$/.test(line);
    const looksRow = (line) => line.includes("|") && !isSep(line) && cells(line).some(Boolean);
    if (!looksRow(lines[0])) return "";
    let bodyStart = 1;
    if (isSep(lines[1])) bodyStart = 2;
    else if (!lines.slice(1).every(looksRow)) return "";
    const head = cells(lines[0]);
    const rows = lines.slice(bodyStart).filter(looksRow).map(cells);
    if (!head.length || !rows.length) return "";
    const thead = `<tr>${head.map((cell) => `<th>${renderInline(cell)}</th>`).join("")}</tr>`;
    const tbody = rows.map((row) => `<tr>${row.map((cell) => `<td>${renderInline(cell)}</td>`).join("")}</tr>`).join("");
    return `<table><thead>${thead}</thead><tbody>${tbody}</tbody></table>`;
  }

  function parseHeadingLine(line) {
    const match = String(line || "").match(/^\s{0,3}(#{1,6})\s+(.+?)\s*$/);
    if (!match) return null;
    const title = match[2].replace(/[ \t]#+\s*$/, "").replace(/`+$/, "").trim();
    if (!title) return null;
    return { level: match[1].length, title };
  }

  function isFenceLang(info) {
    const first = String(info || "")
      .trim()
      .split(/\s+/)[0] || "";
    if (!first) return true;
    if (/^#{1,6}$/.test(first) || first.startsWith("#")) return false;
    return /^[A-Za-z][\w+#.-]*$/.test(first);
  }

  function renderHeading(parsed) {
    return `<h${parsed.level}>${renderInline(parsed.title)}</h${parsed.level}>`;
  }

  const MD_ALERTS = { note: "Note", tip: "Tip", important: "Important", warning: "Warning", caution: "Caution" };

  function parseListMarker(line) {
    const match = String(line || "").match(/^(\s*)([-*+]|\d+\.)\s+(.*)$/);
    if (!match) return null;
    const indent = match[1].replace(/\t/g, "  ").length;
    return { indent, ordered: /^\d+\./.test(match[2]), text: match[3] };
  }

  function looksLikeListBlock(lines) {
    return lines.some((line) => parseListMarker(line)) &&
      lines.every((line) => !line.trim() || parseListMarker(line) || /^\s{2,}\S/.test(line));
  }

  function renderList(lines) {
    const items = [];
    lines.forEach((line) => {
      const marker = parseListMarker(line);
      if (marker) {
        items.push({ indent: marker.indent, ordered: marker.ordered, parts: [marker.text] });
        return;
      }
      if (items.length && line.trim()) items[items.length - 1].parts.push(line.trim());
    });
    if (!items.length) return "";
    const rootIndent = Math.min(...items.map((item) => item.indent));
    const stack = [];
    let html = "";
    const closeTo = (indent) => {
      while (stack.length && stack[stack.length - 1].indent >= indent) {
        html += "</li></" + stack.pop().tag + ">";
      }
    };
    items.forEach((item) => {
      const indent = item.indent < rootIndent ? rootIndent : item.indent;
      const tag = item.ordered ? "ol" : "ul";
      const body = renderInline(item.parts.join(" "));
      if (!stack.length) {
        html += `<${tag}><li>${body}`;
        stack.push({ indent, tag });
        return;
      }
      const top = stack[stack.length - 1];
      if (indent > top.indent) {
        html += `<${tag}><li>${body}`;
        stack.push({ indent, tag });
        return;
      }
      closeTo(indent + 1);
      const parent = stack[stack.length - 1];
      if (!parent || parent.indent !== indent) {
        html += `<${tag}><li>${body}`;
        stack.push({ indent, tag });
        return;
      }
      if (parent.tag !== tag) {
        html += `</li></${parent.tag}><${tag}><li>${body}`;
        parent.tag = tag;
        return;
      }
      html += `</li><li>${body}`;
    });
    while (stack.length) html += "</li></" + stack.pop().tag + ">";
    return html;
  }

  function renderQuote(trimmed) {
    const lines = trimmed.split("\n").map((line) => line.replace(/^>\s?/, ""));
    const alert = String(lines[0] || "").match(/^\[!(NOTE|TIP|IMPORTANT|WARNING|CAUTION)\]\s*(.*)$/i);
    if (alert) {
      const kind = alert[1].toLowerCase();
      const rest = [alert[2], ...lines.slice(1)]
        .map((line) => String(line || "").replace(/^\[!(NOTE|TIP|IMPORTANT|WARNING|CAUTION)\]\s*/i, ""))
        .join("\n")
        .trim();
      const body = rest ? renderInline(rest).replace(/\n/g, "<br>") : "";
      return `<blockquote class="chat-md-alert is-${kind}"><span class="chat-md-alert-label">${MD_ALERTS[kind]}</span>${body ? `<p>${body}</p>` : ""}</blockquote>`;
    }
    return `<blockquote>${renderInline(lines.join("\n")).replace(/\n/g, "<br>")}</blockquote>`;
  }

  function renderFencedMarkdown(text, catalog, streaming) {
    const src = String(text || "");
    let html = "";
    let i = 0;
    while (i < src.length) {
      const start = src.indexOf("```", i);
      if (start === -1) {
        html += renderBlocks(src.slice(i), catalog, streaming);
        break;
      }
      html += renderBlocks(src.slice(i, start), catalog, streaming);
      const nl = src.indexOf("\n", start + 3);
      const lang = (nl === -1 ? src.slice(start + 3) : src.slice(start + 3, nl)).trim();
      const bodyStart = nl === -1 ? src.length : nl + 1;
      const end = src.indexOf("```", bodyStart);
      if (!isFenceLang(lang)) {
        const inner = (lang ? lang + "\n" : "") + src.slice(bodyStart, end === -1 ? src.length : end);
        html += renderBlocks(inner.replace(/```\s*$/, ""), catalog, streaming);
        if (end === -1) break;
        i = end + 3;
        if (src[i] === "\n") i += 1;
        continue;
      }
      if (end === -1) {
        html += renderFence(lang, src.slice(bodyStart));
        break;
      }
      html += renderFence(lang, src.slice(bodyStart, end));
      i = end + 3;
      if (src[i] === "\n") i += 1;
    }
    return html;
  }

  function renderDetailsBlocks(text, catalog, streaming) {
    const src = String(text || "");
    // Only block-position tags are structural. Inline code, prose examples and
    // fenced HTML must never pair with a closing tag later in the answer.
    const roots = [], stack = [];
    let offset = 0, fence = null;
    for (const line of src.split("\n")) {
      const marker = line.match(/^ {0,3}(`{3,}|~{3,})(.*)$/);
      if (fence) {
        if (marker && marker[1][0] === fence[0] && marker[1].length >= fence.length && !marker[2].trim()) fence = null;
      } else if (marker) {
        fence = marker[1];
      } else {
        const opening = line.match(/^ {0,3}<details(\s+open)?\s*>(?=\s*(?:<summary>|$))/i);
        if (opening && stack.length < 32) {
          const node = { start: offset, body: offset + opening[0].length, open: Boolean(opening[1]), children: [] };
          (stack.length ? stack[stack.length - 1].children : roots).push(node);
          stack.push(node);
        }
        // A close may follow an inline summary/body, but never a backtick
        // example or a tag mentioned in ordinary prose.
        const close = line.match(/<\/details>\s*$/i);
        if (close && stack.length && (opening || /^ {0,3}<\/details>\s*$/i.test(line))) {
          const node = stack.pop();
          node.close = offset + close.index;
          node.end = node.close + 10;
        }
      }
      offset += line.length + 1;
    }
    const renderRange = (start, end, nodes) => {
      let html = "", cursor = start;
      for (const node of nodes) {
        if (node.end == null) continue; // Incomplete streamed markup stays literal.
        html += renderFencedMarkdown(src.slice(cursor, node.start), catalog, streaming);
        const inner = src.slice(node.body, node.close);
        const summaryMatch = inner.match(/^\s*<summary>([^\n]*?)<\/summary>[ \t]*\n?/i);
        const summary = summaryMatch ? renderInline(summaryMatch[1].trim()) : "Details";
        const bodyStart = node.body + (summaryMatch ? summaryMatch[0].length : 0);
        html += `<details class="chat-md-details" data-md-details="${node.start}"${node.open ? " open" : ""}><summary>${summary || "Details"}</summary>${renderRange(bodyStart, node.close, node.children)}</details>`;
        cursor = node.end;
      }
      return html + renderFencedMarkdown(src.slice(cursor, end), catalog, streaming);
    };
    return renderRange(0, src.length, roots);
  }

  function looksLikeDiff(text) {
    const lines = String(text || "").split("\n");
    if (lines.length < 2) return false;
    const hits = lines.filter((line) => /^(@@ |\+\+\+ |--- |diff --git |index |\+[^+]|-[^-])/.test(line)).length;
    return hits >= 2;
  }

  const ANSI_COLORS = { 30: "black", 31: "red", 32: "green", 33: "yellow", 34: "blue", 35: "magenta", 36: "cyan", 37: "white", 90: "bright-black", 91: "bright-red", 92: "bright-green", 93: "bright-yellow", 94: "bright-blue", 95: "bright-magenta", 96: "bright-cyan", 97: "bright-white" };

  function renderAnsi(text) {
    const src = String(text || "");
    if (!/\x1b\[/.test(src)) return escapeHtml(src);
    let html = "";
    let i = 0;
    let bold = false;
    let underline = false;
    let color = "";
    const open = () => {
      const cls = [bold ? "is-bold" : "", underline ? "is-underline" : "", color ? "ansi-" + color : ""].filter(Boolean);
      return cls.length ? `<span class="${cls.join(" ")}">` : "";
    };
    let wrap = open();
    const flush = (chunk) => {
      if (!chunk) return;
      html += wrap ? wrap + escapeHtml(chunk) + "</span>" : escapeHtml(chunk);
    };
    while (i < src.length) {
      const esc = src.indexOf("\x1b[", i);
      if (esc === -1) {
        flush(src.slice(i));
        break;
      }
      flush(src.slice(i, esc));
      const end = src.indexOf("m", esc + 2);
      if (end === -1 || end - esc > 24) {
        flush(src.slice(esc, esc + 1));
        i = esc + 1;
        continue;
      }
      const codes = src.slice(esc + 2, end).split(";").map((n) => Number(n) || 0);
      codes.forEach((code) => {
        if (code === 0) { bold = false; underline = false; color = ""; }
        else if (code === 1) bold = true;
        else if (code === 4) underline = true;
        else if (code === 22) bold = false;
        else if (code === 24) underline = false;
        else if (code === 39) color = "";
        else if (ANSI_COLORS[code]) color = ANSI_COLORS[code];
      });
      wrap = open();
      i = end + 1;
    }
    return html;
  }

  function renderChunk(trimmed) {
    if (!trimmed) return "";
    const table = parseTable(trimmed);
    if (table) return table;
    if (/^[-*_]{3,}$/.test(trimmed)) return "<hr>";
    const heading = parseHeadingLine(trimmed);
    if (heading && !trimmed.includes("\n")) return renderHeading(heading);
    const quoteLines = trimmed.split("\n");
    if (quoteLines.every((line) => /^>\s?/.test(line) || !line.trim())) return renderQuote(trimmed);
    const lines = trimmed.split("\n");
    if (looksLikeListBlock(lines)) {
      const list = renderList(lines);
      if (list) return list;
    }
    return `<p>${renderInline(lines.join("\n")).replace(/\n/g, "<br>")}</p>`;
  }

  function renderBlocks(text, catalog, streaming) {
    return rewriteCites(String(text || ""), catalog, streaming)
      .split(/\n{2,}/)
      .map((block) => {
        const trimmed = block.trim();
        if (!trimmed) return "";
        const table = parseTable(trimmed);
        if (table) return table;
        const lines = trimmed.split("\n");
        if (!lines.some((line) => parseHeadingLine(line))) return renderChunk(trimmed);
        let html = "";
        let buf = [];
        const flush = () => {
          if (!buf.length) return;
          html += renderChunk(buf.join("\n"));
          buf = [];
        };
        lines.forEach((line) => {
          const heading = parseHeadingLine(line);
          if (heading) {
            flush();
            html += renderHeading(heading);
            return;
          }
          buf.push(line);
        });
        flush();
        return html;
      })
      .join("");
  }

  function renderMarkdown(src, streaming, catalog) {
    const text = String(src || "").replace(/\r\n/g, "\n");
    return renderDetailsBlocks(text, catalog, streaming) || (streaming ? "" : "<p></p>");
  }

  // Keep settled Markdown nodes mounted while a streamed paragraph grows.
  // Replacing the entire body on every chunk resets selection, code scroll and
  // browser layout anchors even when only its final text node changed.
  function patchMarkdown(target, html) {
    patchMarkdownFrom(target, html, 0);
  }

  function patchMarkdownFrom(target, html, skipCount) {
    const template = document.createElement("template");
    template.innerHTML = html;
    const sync = (parent, source) => {
      let current = parent.firstChild;
      for (const fresh of [...source.childNodes]) {
        if (!current) { parent.appendChild(fresh.cloneNode(true)); continue; }
        const next = current.nextSibling;
        if (!current.isEqualNode(fresh)) {
          if (current.nodeType === Node.TEXT_NODE && fresh.nodeType === Node.TEXT_NODE) {
            if (fresh.data.startsWith(current.data)) current.appendData(fresh.data.slice(current.data.length));
            else current.data = fresh.data;
          } else if (current.nodeType === Node.ELEMENT_NODE && fresh.nodeType === Node.ELEMENT_NODE && current.tagName === fresh.tagName) {
            const keepOpen = current.tagName === "DETAILS" && current.dataset.mdDetails === fresh.dataset.mdDetails;
            for (const attr of [...current.attributes]) if (!(keepOpen && attr.name === "open") && !fresh.hasAttribute(attr.name)) current.removeAttribute(attr.name);
            for (const attr of [...fresh.attributes]) if (!(keepOpen && attr.name === "open") && current.getAttribute(attr.name) !== attr.value) current.setAttribute(attr.name, attr.value);
            sync(current, fresh);
          } else parent.replaceChild(fresh.cloneNode(true), current);
        }
        current = next;
      }
      while (current) { const next = current.nextSibling; current.remove(); current = next; }
    };
    if (skipCount > 0) {
      // The frozen prefix is never re-diffed: isEqualNode is a deep compare,
      // so touching it would reintroduce the O(message) per-event cost this
      // splitting exists to remove.
      const frozen = target.childNodes[skipCount - 1];
      if (!frozen) { patchMarkdown(target, html); return; }
      let current = frozen.nextSibling;
      for (const fresh of [...template.content.childNodes]) {
        if (!current) { target.appendChild(fresh.cloneNode(true)); continue; }
        const next = current.nextSibling;
        if (!current.isEqualNode(fresh)) {
          if (current.nodeType === Node.TEXT_NODE && fresh.nodeType === Node.TEXT_NODE) {
            if (fresh.data.startsWith(current.data)) current.appendData(fresh.data.slice(current.data.length));
            else current.data = fresh.data;
          } else if (current.nodeType === Node.ELEMENT_NODE && fresh.nodeType === Node.ELEMENT_NODE && current.tagName === fresh.tagName) {
            const keepOpen = current.tagName === "DETAILS" && current.dataset.mdDetails === fresh.dataset.mdDetails;
            for (const attr of [...current.attributes]) if (!(keepOpen && attr.name === "open") && !fresh.hasAttribute(attr.name)) current.removeAttribute(attr.name);
            for (const attr of [...fresh.attributes]) if (!(keepOpen && attr.name === "open") && current.getAttribute(attr.name) !== attr.value) current.setAttribute(attr.name, attr.value);
            sync(current, fresh);
          } else target.replaceChild(fresh.cloneNode(true), current);
        }
        current = next;
      }
      while (current) { const next = current.nextSibling; current.remove(); current = next; }
      return;
    }
    sync(target, template.content);
  }

  // Char offset where a streaming markdown text becomes structurally stable:
  // the last blank-line block boundary that is not inside an open fence or
  // details block. Conservatively matches the renderer's own segmentation,
  // so splitting there can never alter the combined output.
  function mdStableBoundary(text) {
    // Disclosure node IDs use document-relative offsets. Keep disclosures
    // on the full renderer until the parser exposes stable block identities.
    if (/<\/?details\b/i.test(text)) return 0;
    let fence = false, boundary = 0;
    for (let i = 0; i < text.length; i += 1) {
      if (text.startsWith("```", i)) { fence = !fence; i += 2; continue; }
      if (!fence && text[i] === "\n" && text[i + 1] === "\n") boundary = i + 2;
    }
    return boundary;
  }

  function _mdNodeKey(node) {
    return node.nodeType === Node.TEXT_NODE ? node.nodeValue : node.outerHTML;
  }

  // Streamed markdown renderer: appends-only text is split into a frozen
  // prefix (rendered once, never re-diffed) and a small active tail that is
  // re-rendered per update. Amortized O(new text) per event instead of
  // O(whole message).
  function streamMarkdown(el, text, writing, catalog) {
    const catalogKey = catalog.join("\n") + "\u0000" + Boolean(writing);
    const reset = el._mdCatalog !== catalogKey || (el._mdText && !text.startsWith(el._mdText));
    let anchor = reset ? 0 : (el._mdAnchor || 0);
    let frozen = reset ? 0 : (el._mdFrozen || 0);
    const want = mdStableBoundary(text);
    if (!reset && want > anchor) {
      const deltaHtml = renderMarkdown(text.slice(anchor, want), writing, catalog);
      const probe = document.createElement("template");
      probe.innerHTML = deltaHtml;
      const deltaNodes = [...probe.content.childNodes];
      const liveNodes = [...el.childNodes].slice(frozen, frozen + deltaNodes.length);
      const aligned = liveNodes.length === deltaNodes.length &&
        liveNodes.every((node, index) => _mdNodeKey(node) === _mdNodeKey(deltaNodes[index]));
      if (aligned) {
        frozen += deltaNodes.length;
        anchor = want;
      }
      // Misaligned: freeze nothing this round and re-render from the old
      // anchor so the visible region is reconciled against the full text.
    }
    const tailHtml = renderMarkdown(text.slice(anchor), writing, catalog);
    patchMarkdownFrom(el, tailHtml, frozen);
    el._mdAnchor = anchor;
    el._mdFrozen = frozen;
    el._mdText = text;
    el._mdCatalog = catalogKey;
  }

  function splitThink(text) {
    const re = /<\/?(?:think|thinking|thought|reasoning)>/gi;
    let reasoning = "";
    let content = "";
    let inThink = false;
    let i = 0;
    let match;
    while ((match = re.exec(text))) {
      const chunk = text.slice(i, match.index);
      if (inThink) reasoning += chunk;
      else content += chunk;
      inThink = match[0][1] !== "/";
      i = match.index + match[0].length;
    }
    const tail = text.slice(i);
    if (inThink) reasoning += tail;
    else content += tail;
    return { reasoning, content, inThink };
  }

  function deltaParts(obj) {
    if (!obj || typeof obj !== "object") return {};
    if (obj.error) {
      const err = obj.error;
      return { error: typeof err === "string" ? err : err.message || "Upstream error" };
    }
    const choice = (obj.choices || [])[0] || {};
    const delta = choice.delta || {};
    const message = choice.message || {};
    let content = "";
    let reasoning = "";
    const take = (value) => {
      if (typeof value === "string") return value;
      if (Array.isArray(value)) {
        return value
          .map((part) => (typeof part === "string" ? part : part && (part.text || part.content) || ""))
          .join("");
      }
      if (value && typeof value === "object") {
        if (typeof value.content === "string") return value.content;
        if (typeof value.text === "string") return value.text;
        if (typeof value.summary === "string") return value.summary;
      }
      return "";
    };
    content = take(delta.content) || (choice.delta ? "" : take(message.content));
    for (const key of ["reasoning_content", "reasoning", "thinking", "reasoning_text"]) {
      reasoning += take(delta[key]) || take(message[key]) || take(obj[key]);
    }
    return {
      content,
      reasoning,
      finish: choice.finish_reason,
      usage: obj.usage && typeof obj.usage === "object" ? obj.usage : null,
    };
  }

  function formatTurnDuration(ms) {
    const n = Math.max(0, Math.round(Number(ms) || 0));
    if (n < 10_000) {
      const sec = n / 1000;
      const digits = n < 1000 || (n < 10_000 && n % 1000) ? 1 : 0;
      return sec.toFixed(digits).replace(/\.0$/, "") + "s";
    }
    const total = Math.max(1, Math.round(n / 1000));
    const h = Math.floor(total / 3600);
    const m = Math.floor((total % 3600) / 60);
    const s = total % 60;
    if (h) {
      if (m && s) return `${h}h ${m}m ${s}s`;
      if (m) return `${h}h ${m}m`;
      if (s) return `${h}h ${s}s`;
      return `${h}h`;
    }
    if (m) return s ? `${m}m ${s}s` : `${m}m`;
    return `${s}s`;
  }

  function usageInOut(usage) {
    if (!usage || typeof usage !== "object") return null;
    const inRaw = usage.prompt_tokens ?? usage.input_tokens;
    const outRaw = usage.output_tokens ?? usage.completion_tokens;
    if (inRaw == null && outRaw == null) return null;
    const inn = Math.max(0, Math.round(Number(inRaw) || 0));
    const out = Math.max(0, Math.round(Number(outRaw) || 0));
    if (!inn && !out) return null;
    return { inn, out };
  }

  function currentModel() {
    return modelsById.get(modelId) || null;
  }

  const compactCfg = Object.assign(
    { warn_ratio: 0.8, auto_ratio: 0.9, auto: true, default_window: 128000, strategy: "summarize" },
    config.compaction && typeof config.compaction === "object" ? config.compaction : {}
  );
  const CONTEXT_RING = 2 * Math.PI * 12;

  function formatTokens(value) {
    const n = Math.max(0, Math.round(Number(value) || 0));
    if (n >= 1_000_000) return (n / 1_000_000).toFixed(n >= 10_000_000 ? 0 : 1).replace(/\.0$/, "") + "M";
    if (n >= 10_000) return Math.round(n / 1000) + "k";
    if (n >= 1000) return (n / 1000).toFixed(1).replace(/\.0$/, "") + "k";
    return String(n);
  }

  function shortModelName(id) {
    const raw = String(id || "").trim();
    if (!raw) return "";
    const leaf = raw.includes("/") ? raw.slice(raw.lastIndexOf("/") + 1) : raw;
    return leaf.slice(0, 20);
  }

  function formatTurnMeta(msg) {
    const bits = [];
    if (chatView === "console") {
      const split = usageInOut(msg && msg.usage);
      if (split && split.out) {
        const fmt = new Intl.NumberFormat();
        bits.push({
          text: formatTokens(split.out) + " tok",
          title: fmt.format(split.out) + " output tokens this reply",
        });
      }
    } else {
      const model = (msg && msg.model) || modelId || "";
      const name = shortModelName(model);
      if (name) bits.push({ text: name, title: model });
    }
    if (msg && msg.elapsedMs != null) {
      const text = formatTurnDuration(msg.elapsedMs);
      bits.push({ text, title: text + " elapsed" });
    }
    return bits;
  }

  function modelWindow() {
    const model = currentModel();
    return (model && model.contextWindow) || Number(compactCfg.default_window) || 128000;
  }

  const TOOL_FAMILIES = {
    web_search: "web search",
    read_url: "web search",
    run_terminal_command: "shell",
    get_command_output: "shell",
    kill_command: "shell",
    monitor: "shell",
    read_file: "files",
    list_dir: "files",
    grep: "files",
    glob: "files",
    write_file: "files",
    search_replace: "files",
    create_artifact: "artifacts",
    update_artifact: "artifacts",
    spawn_subagent: "subagents",
    get_subagent_output: "subagents",
    kill_subagent: "subagents",
    list_subagents: "subagents",
    search_models: "models",
    search_tools: "catalog",
    load_skill: "skills",
    propose_skill: "skills",
    todo_write: "todos",
  };

  function estimateChars(text) {
    const s = String(text || "");
    return s ? Math.max(1, Math.ceil(s.length / 4)) : 0;
  }

  function toolFamily(name) {
    const key = String(name || "").trim();
    return TOOL_FAMILIES[key] || (key ? key.replace(/_/g, " ") : "tools");
  }

  function estimateTrace(trace) {
    if (!trace || typeof trace !== "object") return 0;
    const preview = String(trace.preview || "");
    const summary = String(trace.summary || "");
    const output = summary.startsWith(preview) ? summary : preview.startsWith(summary) ? preview : [preview, summary].filter(Boolean).join("\n");
    const bits = [trace.name, trace.command, trace.path, trace.url, trace.pattern, output, trace.error, trace.description];
    const queries = [...new Set([trace.query, ...(Array.isArray(trace.queries) ? trace.queries : [])].filter(q => typeof q === "string" && q))];
    bits.push(...queries);
    return estimateChars(bits.filter(Boolean).join(" "));
  }

  function localOccupancy() {
    let messagesN = 0;
    let thinking = 0;
    const families = {};
    for (const msg of messages) {
      messagesN += 4;
      const content = msg.content;
      if (typeof content === "string") messagesN += estimateChars(content);
      else if (Array.isArray(content)) {
        for (const part of content) {
          if (typeof part === "string") messagesN += estimateChars(part);
          else if (part && typeof part === "object") {
            if (part.type === "image_url" || part.type === "input_image") messagesN += 1500;
            else messagesN += estimateChars(part.text || part.content || "");
          }
        }
      }
      thinking += estimateChars(msg.reasoning);
      for (const call of msg.tool_calls || []) {
        const fn = (call && call.function) || {};
        const name = fn.name || call.name || "tools";
        const family = toolFamily(name);
        families[family] = (families[family] || 0) + estimateChars(String(name) + String(fn.arguments || ""));
      }
      for (const trace of msg.tools || []) {
        const family = toolFamily(trace && trace.name);
        families[family] = (families[family] || 0) + estimateTrace(trace);
      }
      for (const att of msg.attachments || []) {
        if (String(att.kind || att.mime || "").startsWith("image")) messagesN += 1500;
        else messagesN += estimateChars(att.data || att.text || "");
      }
    }
    const toolCalls = Object.values(families).reduce((sum, n) => sum + n, 0);
    const history = messagesN + thinking + toolCalls;
    const system = Number(liveMeter && liveMeter.parts && liveMeter.parts.system) || 0;
    const schemas = Number(liveMeter && liveMeter.parts && liveMeter.parts.tools) || 0;
    return {
      system,
      history,
      messages: messagesN,
      thinking,
      tool_calls: toolCalls,
      tool_families: families,
      tools: schemas,
      draft: 0,
      total: system + history + schemas,
    };
  }

  function estimateLocalTokens() {
    return Math.max(0, localOccupancy().total);
  }

  function estimateDraftTokens() {
    if (streaming) return 0;
    let total = 0;
    const text = (input && input.value) || "";
    if (text) total += Math.max(1, Math.ceil(text.length / 4));
    for (const att of pendingAttachments) {
      if (String(att.kind || att.mime || "").startsWith("image")) {
        total += 1500;
      } else {
        const data = att.data || att.text || "";
        if (typeof data === "string" && data) total += Math.max(1, Math.ceil(data.length / 4));
      }
    }
    return total;
  }

  function occupancyRows(snap) {
    const parts = (snap && snap.parts && typeof snap.parts === "object") ? snap.parts : {};
    const draft = estimateDraftTokens();
    // Zero is authoritative too. Do not mix a compacted prompt breakdown
    // with the much larger, still-visible historical transcript.
    const source = snap?.parts ? parts : liveMeter ? {} : localOccupancy();
    const system = Number(source.system) || 0;
    const history = Number(source.history) || 0;
    const messagesN = Number(source.messages) || 0;
    const thinking = Number(source.thinking) || 0;
    const toolCalls = Number(source.tool_calls) || 0;
    const schemas = Number(source.tools) || 0;
    const families = source.tool_families || {};
    if (liveMeter && !snap?.parts) return [["Context", snap.used - draft, ""], ...(draft ? [["Draft", draft, ""]] : [])];
    const rows = [["System", system, ""]];
    rows.push(["History", history, ""]);
    if (messagesN || thinking || toolCalls) {
      rows.push(["Messages", messagesN, "child"]);
      if (thinking) rows.push(["Thinking", thinking, "child"]);
      if (toolCalls) {
        rows.push(["Tool calls", toolCalls, "child"]);
        Object.keys(families).sort().forEach((name) => {
          const n = Number(families[name]) || 0;
          if (n) rows.push([name.replace(/\b\w/g, (ch) => ch.toUpperCase()), n, "grand"]);
        });
      }
    }
    if (schemas) rows.push(["Tool schemas", schemas, ""]);
    if (draft) rows.push(["Draft", draft, ""]);
    return rows.filter((row) => row[1] > 0 || row[0] === "History");
  }

  let contextRecountPending = false;
  function contextSnapshot() {
    const limit = modelWindow();
    const fromLive = liveMeter && typeof liveMeter.used === "number";
    const draftTokens = estimateDraftTokens();
    const hasDraft = draftTokens > 0;
    // The server supplies measured single-request occupancy or a calibrated
    // estimate of retained context. Never use summed turn billing as context.
    const baseUsed = fromLive
      ? Math.max(0, Number(liveMeter.used) || 0)
      : (messages.length ? estimateLocalTokens() : 0);
    const used = baseUsed + draftTokens;
    const windowSize = fromLive && liveMeter.limit ? Number(liveMeter.limit) || limit : limit;
    const ratio = windowSize ? used / windowSize : 0;
    const warnRatio = Number(liveMeter && liveMeter.warn_ratio) || Number(compactCfg.warn_ratio) || 0.8;
    const autoRatio = Number(liveMeter && liveMeter.auto_ratio) || Number(compactCfg.auto_ratio) || 0.9;
    const warn = ratio >= warnRatio;
    const auto = ratio >= autoRatio;
    return {
      used,
      limit: windowSize,
      model: (liveMeter && liveMeter.model) || modelId,
      basis: liveMeter?.basis || "visible_transcript_estimate",
      scope: liveMeter?.scope || "transcript",
      refreshing: Boolean(contextRecountPending && liveMeter),
      provider_usage: liveMeter?.provider_usage || null,
      history_info: liveMeter?.history_info || null,
      estimated: hasDraft ? true : (fromLive ? Boolean(liveMeter.estimated) : true),
      ratio,
      warn,
      auto,
      warn_ratio: warnRatio,
      auto_ratio: autoRatio,
      auto_enabled: liveMeter && typeof liveMeter.auto_enabled === "boolean" ? liveMeter.auto_enabled : compactCfg.auto !== false,
      strategy: (liveMeter && liveMeter.strategy) || compactCfg.strategy,
      compacting: compacting || Boolean(liveMeter && liveMeter.compacting),
      parts: liveMeter && liveMeter.parts && typeof liveMeter.parts === "object" ? liveMeter.parts : null,
    };
  }

  function acceptMeter(meter) {
    if (!meter || typeof meter !== "object") return;
    const scope = meter.scope === "transcript" ? "transcript" : "prompt";
    // Prompt-scope readings (live request during a turn) only apply while a
    // run is active. Transcript snapshots are authoritative at rest, so
    // reloads and confirmed edits/deletes outrank stale streaming estimates.
    // Mid-run they lag the in-flight turn by construction — durable rows
    // exclude in-turn tool results and streaming output — so they may only
    // seed a missing meter, never displace the live prompt reading.
    const runActive = Boolean(streaming || sending || runIsActive(activeRun));
    if (scope === "prompt" && !runActive) return;
    if (scope === "prompt" && meter.run_id && runIsActive(activeRun) && meter.run_id !== activeRun.id) return;
    const sameRun = !liveMeter?.run_id || !activeRun?.id || liveMeter.run_id === activeRun.id;
    if (scope === "transcript" && runActive && liveMeter?.scope === "prompt" && sameRun && !contextRecountPending) return;
    // Do not carry ownership, calibration or provenance from the previous run
    // into a new snapshot that intentionally omits those fields.
    liveMeter = { ...meter, scope };
    contextRecountPending = false;
    if (!("parts" in meter)) delete liveMeter.parts;
    if (!("basis" in meter)) delete liveMeter.basis;
    // Usage diagnostics belong to this reading, not the previous request.
    if (!("reported_request_tokens" in meter)) delete liveMeter.reported_request_tokens;
    if (!("provider_usage" in meter)) delete liveMeter.provider_usage;
    meterAnchor = { meter: { ...liveMeter }, local: localOccupancy() };
    syncContextMeter();
  }

  function applyMeterParts(parts, scope) {
    const limit = (liveMeter && liveMeter.limit) || modelWindow();
    const warnRatio = Number(liveMeter && liveMeter.warn_ratio) || Number(compactCfg.warn_ratio) || 0.8;
    const autoRatio = Number(liveMeter && liveMeter.auto_ratio) || Number(compactCfg.auto_ratio) || 0.9;
    const ratio = limit ? parts.total / limit : 0;
    liveMeter = {
      ...(liveMeter || {}),
      used: parts.total,
      limit,
      model: modelId,
      estimated: true,
      ratio: Math.round(ratio * 10000) / 10000,
      warn: ratio >= warnRatio,
      auto: ratio >= autoRatio,
      standing: scope === "transcript",
      scope,
      basis: "visible_transcript_estimate",
      provider_usage: null,
      parts,
    };
    syncContextMeter();
  }

  function recountLocalContext() {
    meterAnchor = null;
    if (["payload_chars_v1", "provider_calibrated", "provider_usage", "provider_prompt"].includes(liveMeter?.basis) && (!liveMeter.model || liveMeter.model === modelId)) {
      // The browser only has previews, not the retained provider transcript.
      // Keep the last measurement explicitly marked pending until the save's
      // authoritative snapshot arrives instead of inventing a much lower total.
      contextRecountPending = true;
      syncContextMeter();
      return;
    }
    contextRecountPending = false;
    const parts = localOccupancy();
    parts.total = parts.system + parts.history + parts.tools;
    applyMeterParts(parts, "transcript");
    // Transcript surgery invalidates diagnostics from the last request.
    if (liveMeter) delete liveMeter.reported_request_tokens;
    syncContextMeter();
  }

  function liveStreamEstimate() {
    // The backend measures the actual provider payload, including full tool
    // results. Visible reasoning and preview growth are not that payload.
    if (["payload_chars_v1", "provider_calibrated", "provider_usage", "provider_prompt"].includes(liveMeter?.basis)) { syncContextMeter(); return; }
    const local = localOccupancy();
    if (!liveMeter) {
      applyMeterParts(local, "prompt");
      meterAnchor = { meter: { ...liveMeter }, local };
      return;
    }
    if (!meterAnchor) meterAnchor = { meter: { ...liveMeter }, local };
    const base = meterAnchor.meter;
    // Estimate only new visible material since this reading. Full provider
    // tool results are absent from the UI; rebuilding their total from trace
    // previews caused alternating high/low readings every 400 ms.
    const delta = Object.fromEntries(["messages", "thinking", "tool_calls"].map(key =>
      [key, Math.max(0, local[key] - meterAnchor.local[key])]));
    const growth = delta.messages + delta.thinking + delta.tool_calls;
    const used = Math.max(0, Number(base.used) || 0) + growth;
    liveMeter = { ...base, used, scope: "prompt", estimated: growth > 0 || Boolean(base.estimated) };
    if (base.parts) {
      const parts = { ...base.parts };
      for (const key of Object.keys(delta)) parts[key] = (Number(parts[key]) || 0) + delta[key];
      parts.tool_families = { ...(base.parts.tool_families || {}) };
      for (const [family, value] of Object.entries(local.tool_families)) {
        const extra = Math.max(0, value - (meterAnchor.local.tool_families[family] || 0));
        if (extra) parts.tool_families[family] = (Number(parts.tool_families[family]) || 0) + extra;
      }
      parts.history = (Number(base.parts.history) || 0) + growth;
      parts.total = used;
      liveMeter.parts = parts;
    }
    syncContextMeter();
  }

  function closeContextPop() {
    if (contextPop) contextPop.hidden = true;
    if (contextBtn) contextBtn.setAttribute("aria-expanded", "false");
  }

  function hideContextAlert() {
    if (contextAlert) contextAlert.hidden = true;
  }

  function showContextAlert(text) {
    if (!contextAlert || !contextAlertText) return;
    contextAlertText.textContent = text;
    contextAlert.hidden = false;
  }

  function maybeContextWarn(snap) {
    if (!messages.length) {
      hideContextAlert();
      return;
    }
    if (!snap.warn) {
      contextWarned = false;
      contextAlertHidden = false;
      hideContextAlert();
      return;
    }
    if (contextAlertHidden) return;
    const pct = Math.max(1, Math.round(snap.ratio * 100));
    const autoPct = Math.round(snap.auto_ratio * 100);
    if (snap.auto && snap.auto_enabled) {
      showContextAlert(`Context is ${pct}% full. Auto-compact runs at ${autoPct}%.`);
      contextWarned = true;
      return;
    }
    if (!contextWarned) {
      contextWarned = true;
      showContextAlert(`Context is ${pct}% full. Compaction starts at ${autoPct}%.`);
    }
  }

  function syncContextMeter() {
    const active = messages.length > 0;
    if (contextWrap) contextWrap.hidden = !active;
    if (!active) {
      closeContextPop();
      hideContextAlert();
      return;
    }
    const snap = contextSnapshot();
    const ratio = Math.max(0, Math.min(1, snap.ratio || 0));
    const usedLabel = formatTokens(snap.used);
    const limitLabel = formatTokens(snap.limit);
    const pct = Math.round(ratio * 100);
    const title = `${usedLabel} / ${limitLabel} tokens (${pct}%)${snap.refreshing ? " · updating" : ""}`;
    if (contextFill) {
      const offset = CONTEXT_RING * (1 - ratio);
      contextFill.style.strokeDasharray = String(CONTEXT_RING);
      contextFill.style.strokeDashoffset = String(offset);
    }
    if (contextBtn) {
      contextBtn.classList.toggle("is-warn", Boolean(snap.warn) && !snap.auto);
      contextBtn.classList.toggle("is-auto", Boolean(snap.auto));
      contextBtn.classList.toggle("is-busy", Boolean(snap.compacting || compactingRequest));
      contextBtn.setAttribute("aria-label", "Context usage " + title);
      contextBtn.title = title;
    }
    if (contextPopMeter) {
      contextPopMeter.textContent = `${usedLabel} / ${limitLabel}${snap.refreshing ? " · updating" : ""}`;
    }
    if (contextPopParts && contextPop && !contextPop.hidden) {
      const rows = occupancyRows(snap);
      contextPopParts.innerHTML = rows.map(([label, tokens, nest]) => {
        const cls = [nest === "child" ? "is-child" : "", nest === "grand" ? "is-grand" : "", tokens <= 0 ? "is-zero" : ""].filter(Boolean).join(" ");
        return `<li${cls ? ` class="${cls}"` : ""}><span>${label}</span><span>${formatTokens(tokens)}</span></li>`;
      }).join("");
    }
    if (contextPopLast) {
      const usage = snap.provider_usage;
      const prompt = Number(usage?.prompt_tokens ?? usage?.input_tokens) || 0;
      contextPopLast.hidden = !prompt;
      contextPopLast.textContent = prompt ? `Last request reported: ${formatTokens(prompt)} input tokens.` : "";
    }
    if (contextPopNote) {
      const bits = [];
      if (snap.refreshing) bits.push("Updating after a transcript change; showing the last server measurement");
      bits.push(snap.estimated ? "Estimated active context" : "Provider-reported request usage");
      bits.push(snap.scope === "prompt" ? "Current model request" : "Retained context for the next request");
      bits.push("These can differ without compaction: output/reasoning replay and provider calibration can change the next request size");
      if (!snap.estimated && snap.parts) bits.push("Category breakdown is estimated");
      bits.push("Saved messages and tool results are retained across turns; reasoning replay depends on the provider");
      if (snap.history_info?.trace_fallbacks) bits.push(`${snap.history_info.trace_fallbacks} older tool results were recovered from stored traces and may be incomplete`);
      if (snap.history_info?.mode === "legacy") bits.push("This older run uses its saved context; full history is rebuilt on the next turn");
      bits.push((pct || 0) + "% of this model's window");
      if (snap.auto_enabled) bits.push("Auto-compact at " + Math.round(snap.auto_ratio * 100) + "%");
      else bits.push("Auto-compact is off");
      contextPopNote.textContent = bits.join(". ") + ".";
    }
    maybeContextWarn(snap);
  }

  async function runCompact(instructions) {
    if (compactingRequest) return;
    if (!currentId || !messages.length) {
      setHint("Nothing to compact yet.");
      return;
    }
    if (runIsActive(activeRun) || sending) {
      setHint("Wait for this reply to finish, then compact.");
      return;
    }
    if (!requireLive()) return;
    compactingRequest = true;
    syncContextMeter();
    closeContextPop();
    try {
      const data = await api("/chat/threads/" + currentId + "/compact", {
        method: "POST",
        body: JSON.stringify({
          expected_revision: serverRevision,
          command_id: newSession(),
          instructions: instructions || "",
        }),
      });
      if (data && data.thread) acceptThread(data.thread);
      if (data && data.thread && data.thread.compaction && data.thread.compaction.meter) {
        acceptMeter(data.thread.compaction.meter);
      } else {
        syncContextMeter();
      }
      if (data && data.compacted === false) {
        setHint("Not enough history to compact yet.");
      } else {
        const meter = (data && data.thread && data.thread.compaction && data.thread.compaction.meter) || contextSnapshot();
        setHint("Context compacted · " + formatTokens(meter.used) + " / " + formatTokens(meter.limit) + " tokens.");
        contextWarned = false;
        contextAlertHidden = false;
        hideContextAlert();
      }
    } catch (error) {
      showFailure("Could not compact this chat", error);
    } finally {
      compactingRequest = false;
      syncContextMeter();
    }
  }

  async function runTitleRegen() {
    if (titleRegenRequest) return;
    if (!currentId || !messages.length) {
      setHint("Nothing to title yet.");
      return;
    }
    if (!requireLive()) return;
    titleRegenRequest = true;
    try {
      const data = await api("/chat/threads/" + currentId + "/title", {
        method: "POST",
        body: JSON.stringify({ command_id: newSession() }),
      });
      if (data && data.started) setHint("Regenerating title\u2026", false, 6000);
    } catch (error) {
      if (error && error.status === 429) setHint(error.message || "Title regeneration limit reached. Try again later.", true);
      else showFailure("Could not regenerate the title", error);
    } finally {
      titleRegenRequest = false;
    }
  }

  const EFFORT_LABELS = {
    none: "Off",
    minimal: "Minimal",
    low: "Low",
    medium: "Medium",
    high: "High",
    xhigh: "Extra high",
    max: "Max",
  };
  const PICKER_CHECK =
    '<svg class="chat-picker-check" viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><path d="M3 8.6l3.2 3.2L13 5" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>';
  const PICKER_CHEV =
    '<svg class="chat-picker-chev" viewBox="0 0 16 16" width="12" height="12" aria-hidden="true"><path d="M3.5 6l4.5 4.5L12.5 6" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>';

  function modelEfforts() {
    const model = currentModel();
    return (model && model.reasoning) || defaultReasoningFor(model && model.type);
  }

  function defaultEffort() {
    const model = currentModel();
    const efforts = modelEfforts();
    const wanted = (model && model.reasoningDefault) || "";
    if (wanted && efforts.includes(wanted)) return wanted;
    if (efforts.includes("medium")) return "medium";
    return efforts[0] || "medium";
  }

  function currentEffort() {
    const efforts = modelEfforts();
    const stored = effortByModel[modelId];
    if (stored && efforts.includes(stored)) return stored;
    return defaultEffort();
  }

  function effortLabel(effort) {
    const model = currentModel();
    if (model && model.type === "zai") return effort === "none" ? "Off" : "On";
    return EFFORT_LABELS[effort] || effort;
  }

  function setEffort(value) {
    const efforts = modelEfforts();
    if (!efforts.includes(value)) return;
    effortByModel[modelId] = value;
    try {
      localStorage.setItem(STORE_EFFORT, JSON.stringify(effortByModel));
    } catch (_) {}
    const model = currentModel();
    const label = effortLabel(value);
    const small = pickerHost.querySelector(".chat-picker-btn small");
    if (small) small.textContent = label;
    const btn = pickerHost.querySelector(".chat-picker-btn");
    if (btn && model) btn.title = model.id + " · " + label;
    syncEffortSlider(value);
  }

  function setHint(text, fault, duration) {
    if (!hint) return;
    window.clearTimeout(hintTimer);
    if (hintFrame) {
      window.cancelAnimationFrame(hintFrame);
      hintFrame = 0;
    }
    hint.classList.toggle("is-fault", Boolean(fault));
    const show = Boolean(text);
    if (!show) {
      hint.classList.remove("is-on");
      hintTimer = window.setTimeout(() => {
        hint.hidden = true;
        hint.textContent = "";
      }, 180);
      return;
    }
    hint.hidden = false;
    hint.textContent = "";
    hint.appendChild(document.createTextNode(text));
    hintFrame = window.requestAnimationFrame(() => {
      hintFrame = 0;
      hint.classList.add("is-on");
    });
    if (duration === 0) return;
    const ms = duration != null ? duration : fault ? 4500 : 2200;
    hintTimer = window.setTimeout(() => setHint(""), ms);
  }

  function defaultHint() {
    if (!allModels.length) {
      setHint("No models are unlocked yet. ", false, 0);
      const link = document.createElement("a");
      link.href = "/sources";
      link.textContent = "Open Sources";
      hint.appendChild(link);
      return;
    }
    setHint("");
  }

  function showFailure(action, error) {
    const message = (error && error.message) || "Request failed.";
    setHint(`${action}: ${message}`, true);
  }

  let dockSpaceLocked = false;
  function resizeInput() {
    // Placeholder text is excluded from the measurement: engines fold wrapped
    // placeholder lines into scrollHeight, which would balloon the terminal
    // composer's single-row layout on narrow screens.
    const ph = input.placeholder;
    if (!input.dataset.placeholder) input.dataset.placeholder = ph;
    const wanted = pickerSheetQuery.matches ? "Message" : input.dataset.placeholder;
    if (ph !== wanted) input.placeholder = wanted;
    input.placeholder = "";
    const followGap = thread ? thread.scrollHeight - thread.scrollTop - thread.clientHeight : Infinity;
    const previousHeight = input.style.height;
    dockSpaceLocked = true;
    input.style.height = "auto";
    const style = window.getComputedStyle(input);
    const min = parseFloat(style.minHeight) || 0;
    const max = parseFloat(style.maxHeight) || 192;
    const nextHeight = Math.min(Math.max(input.scrollHeight, min), Math.max(min, max)) + "px";
    input.style.height = nextHeight;
    dockSpaceLocked = false;
    input.placeholder = wanted;
    // Keep the transcript glued while the row grows or shrinks: the eased
    // follow lags the layout change, so without this re-pin the log bounces
    // up and down on every new wrapped line. Skip the snap when height is
    // unchanged — overlay/focus remeasures used to bounce a settled log.
    if (thread && followGap < 96 && previousHeight !== nextHeight) thread.scrollTop = Math.max(0, thread.scrollHeight - thread.clientHeight);
    syncDockSpace();
    if (window.FuseComposerSpell) FuseComposerSpell.syncMetrics();
  }

  function syncScrollGutter() {
    if (!root || !thread) return;
    const gutter = Math.max(0, Math.round(thread.offsetWidth - thread.clientWidth));
    if (root.style.getPropertyValue("--chat-scroll-gutter") !== gutter + "px") root.style.setProperty("--chat-scroll-gutter", gutter + "px");
  }

  function syncDockSpace() {
    if (!root || !form) return;
    const h = form.getBoundingClientRect().height;
    const value = Math.max(0, Math.round(h)) + "px";
    if (root.style.getPropertyValue("--dock-h") !== value) root.style.setProperty("--dock-h", value);
    syncScrollGutter();
  }

  let viewportFrame = 0;
  let viewportWidth = window.innerWidth;
  let viewportBaseline = window.innerHeight;
  let composerKeyboardSeen = false;

  function setComposerExpanded(expanded) {
    root.classList.toggle("is-composer-expanded", expanded && window.innerWidth <= 800);
    resizeInput();
  }

  function pinVisualViewport() {
    if (viewportFrame) return;
    viewportFrame = window.requestAnimationFrame(() => {
      viewportFrame = 0;
      const vv = window.visualViewport;
      // Zoom is browser magnification, not a smaller application viewport.
      if (vv && Math.abs(vv.scale - 1) > 0.01) return;
      const inner = window.innerHeight;
      const vvHeight = vv ? vv.height : inner;
      const vvOffset = vv ? Math.max(0, vv.offsetTop) : 0;
      const composerFocus = form.contains(document.activeElement) &&
        document.activeElement.matches("textarea, input, [contenteditable='true']");
      const mobile = window.innerWidth <= 800;
      const sheetFocus = document.activeElement instanceof Element &&
        Boolean(document.activeElement.closest(".chat-sheet, .chat-view-menu"));
      const editing = composerFocus || sheetFocus;
      if (Math.abs(window.innerWidth - viewportWidth) > 1) {
        viewportWidth = window.innerWidth;
        viewportBaseline = inner;
      }
      // Android can resize both viewports; preserve the pre-keyboard height.
      viewportBaseline = Math.max(viewportBaseline, inner, Math.round(vvHeight + vvOffset));
      const occluded = viewportBaseline - vvHeight > 80;
      // Focus and keyboard visibility are different states on Android: hiding
      // the keyboard leaves the textarea focused. Wait for an observed shrink
      // before treating a restored viewport as dismissal (not opening latency).
      if (mobile && occluded && document.activeElement === input) {
        composerKeyboardSeen = true;
        root.classList.add("is-composer-expanded");
      } else if (!mobile || (composerKeyboardSeen && !occluded)) {
        composerKeyboardSeen = false;
        root.classList.remove("is-composer-expanded");
      }
      const keyboard = mobile && occluded && (editing || document.body.classList.contains("is-keyboard"));
      if (!keyboard && !editing) viewportBaseline = inner;
      // Keyboard open: do not apply offsetTop. Android Chrome/Brave with
      // interactive-widget=resizes-content already shrinks the layout; a
      // leftover visual-viewport pan plus top:offsetTop leaves a black gap
      // above the keyboard. Cancel the pan and fill the remaining area.
      let height = Math.max(1, Math.round(vvHeight));
      let offset = keyboard ? 0 : Math.max(0, Math.round(vvOffset));
      if (keyboard) {
        if (vv && vvOffset > 1) {
          try { vv.scrollTo(0, 0); } catch (_) {}
        }
        document.documentElement.scrollTop = 0;
        document.body.scrollTop = 0;
        const layoutLooksResized = inner <= vvHeight + vvOffset + 40;
        height = Math.max(1, Math.round(layoutLooksResized ? inner : (vv ? vv.height : inner)));
      }
      const wasAtBottom = stickToBottom;
      const savedTop = thread ? thread.scrollTop : 0;
      const rootStyle = document.documentElement.style;
      const prevHeight = parseFloat(rootStyle.getPropertyValue("--vv-height"));
      const prevOffset = parseFloat(rootStyle.getPropertyValue("--vv-offset"));
      const heightStable = Number.isFinite(prevHeight) && Math.abs(prevHeight - height) <= 1;
      const offsetStable = Number.isFinite(prevOffset) && Math.abs(prevOffset - offset) <= 1;
      const keyboardChanged = document.body.classList.contains("is-keyboard") !== keyboard;
      const layoutChanged = keyboardChanged || !heightStable || !offsetStable;
      if (!layoutChanged && !composerFocus && !keyboard) return;
      rootStyle.setProperty("--vv-height", height + "px");
      rootStyle.setProperty("--vv-offset", offset + "px");
      document.body.classList.toggle("is-keyboard", keyboard);
      if (keyboard && sheetFocus && document.activeElement instanceof Element) {
        document.activeElement.scrollIntoView({ block: "nearest" });
      }
      if (composerFocus || keyboard) resizeInput();
      else {
        syncEmptyComposer();
        syncDockSpace();
      }
      if (layoutChanged) restoreThreadScroll(wasAtBottom, savedTop);
    });
  }

  function restoreThreadScroll(wasAtBottom, savedTop) {
    if (!thread) return;
    ignoreScroll = true;
    if (wasAtBottom) {
      stickToBottom = true;
      snapThread();
    } else {
      thread.scrollTop = savedTop;
    }
    syncLatest();
    window.requestAnimationFrame(() => {
      if (stickToBottom) snapThread();
      ignoreScroll = false;
      syncLatest();
    });
  }

  function syncComposer() {
    const hasText = Boolean(input.value.trim());
    const hasAttach = pendingAttachments.length > 0;
    const stop = streaming && !(hasText || hasAttach);
    sendBtn.disabled = streaming ? false : sending || loading || !(hasText || hasAttach) || !modelId || !isLive();
    sendBtn.classList.toggle("is-stop", stop);
    sendBtn.setAttribute("aria-label", stop ? "Stop" : "Send");
    const pickerOpen = Boolean(projectPicker && !projectPicker.hidden);
    newBtns.forEach((btn) => {
      btn.disabled = sending;
      btn.setAttribute("aria-expanded", String(historyTab === "projects" && pickerOpen));
    });
    input.disabled = !allModels.length;
    if (attachBtn) attachBtn.disabled = streaming || sending || !modelId;
  }

  function persist(transcript = false) {
    try {
      if (modelId) localStorage.setItem(STORE_MODEL, modelId);
      localStorage.setItem(STORE_EFFORT, JSON.stringify(effortByModel));
    } catch (_) {}
    dirty = true;
    transcriptDirty ||= transcript;
    revision += 1;
    if (transcript) enqueueSave(generation).catch(() => {});
    else queueSave();
    syncStatus();
  }

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

  function toolRunning(trace, live) {
    // Waiting is an invocation, not the lifetime of the child it observes.
    if (/^get_(command|subagent)_output$/.test(trace?.name) && ["done", "error", "failed", "interrupted", "cancelled"].includes(trace.status)) return false;
    if (/^get_(command|subagent)_output$/.test(trace?.name) && !live && !toolAwaitingCancellation(trace)) return false;
    if (["completed", "done", "failed", "error", "timed_out", "cancelled", "interrupted", "lost"].includes(executionState(trace))) return false;
    if (["running", "queued", "cancelling"].includes(executionState(trace))) return true;
    if (trace && childBackedTool(trace.name) && executionIdentity(trace) && ["running", "progress", "queued"].includes(trace.status)) return true;
    return Boolean((live || toolAwaitingCancellation(trace)) && toolIsCurrent(trace));
  }

  function toolAwaitingCancellation(trace) {
    return Boolean(runIsActive(activeRun) && runIsStopping(activeRun) && activeAssistant()?.tools?.includes(trace));
  }

  function isShellExecTool(name) {
    return name === "run_terminal_command" || name === "get_command_output" || name === "kill_command" || name === "monitor";
  }

  function backgroundCommandLabel(trace) {
    if (!trace || !isShellExecTool(trace.name) || !trace.background || !trace.child_id) return "";
    const kind = trace.backgroundResult ? "background result" : "background";
    return `${kind} · ${trace.child_id} · ${String(trace.command || "command").trim()}`;
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

  function toolInterrupted(trace, live) {
    if (!trace) return false;
    if (["completed", "done"].includes(executionState(trace))) return false;
    if (trace.status === "interrupted" || trace.status === "cancelled") return true;
    if (live || toolAwaitingCancellation(trace)) return false;
    if (childBackedTool(trace.name) && executionIdentity(trace) && !/^get_(command|subagent)_output$/.test(trace.name)) return false;
    return trace.status === "running" || trace.status === "progress" || trace.status === "queued";
  }

  // One presentation state for standalone tools, grouped children, and summaries.
  function toolBackground(trace) {
    return Boolean(trace.background || (trace.name === "spawn_subagent" && trace.summary === "Background task started"));
  }

  function toolPresentation(trace, live) {
    const execution = executionState(trace);
    const returnedWait = /^get_(command|subagent)_output$/.test(trace.name) && ["done", "error", "failed", "interrupted", "cancelled"].includes(trace.status);
    const abandonedWait = /^get_(command|subagent)_output$/.test(trace.name) && !returnedWait && !live && !toolAwaitingCancellation(trace);
    const executing = !returnedWait && !abandonedWait && ["running", "queued", "cancelling"].includes(execution);
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

  function toolKey(trace, index) {
    return `tool-${trace.id || index}`;
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

  function countsLabel(counts) {
    const active = counts.phases ? Object.entries(counts.phases).map(([label, n]) => `${n} ${label}`) : counts.running ? [`${counts.running} running`] : [];
    return [...active, ...[["done", "done"], ["failed", "failed"], ["stopped", "stopped"], ["warning", "nonzero exit"]]
      .filter(([key]) => counts[key]).map(([key, label]) => `${counts[key]} ${label}`)].join(" · ");
  }

  function reconcileAgentTraces(rows, allowRestart = false) {
    const byId = new Map(rows.map((row) => [String(row.child_id || row.run_id || row.id), row]));
    let childChanged = false;
    const targets = [...messages, ...(agentRun?.messages || []), ...(agentRun?.message ? [agentRun.message] : [])];
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
      if (changed && index < messages.length && log.children[index]) updateAssistant(log.children[index], msg, Boolean(streaming && activeRun?.message_id === msg.id));
      if (changed && index >= messages.length) childChanged = true;
    });
    if (childChanged && agentsOpen) scheduleAgentView();
    syncLiveDock();
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
    for (const key of ["duration_ms", "elapsed_ms", "timeout_ms", "call_duration_ms", "execution_duration_ms", "offset", "limit", "max_matches"]) {
      if (trace[key] != null && Number.isFinite(Number(trace[key]))) rec[key] = Number(trace[key]);
    }
    if (trace.title) rec.title = String(trace.title).slice(0, 80);
    if (trace.source) rec.source = String(trace.source).slice(0, 16);
    if (trace.event_count != null && Number.isFinite(Number(trace.event_count))) rec.event_count = Number(trace.event_count);
    if (trace.identifier) rec.identifier = String(trace.identifier).slice(0, 64);
    if (trace.visibility === "public" || trace.visibility === "private") rec.visibility = trace.visibility;
    if (trace.version != null && Number.isFinite(Number(trace.version))) rec.version = Number(trace.version);
    if (trace.host) rec.host = String(trace.host).slice(0, 40);
    if (trace.path) rec.path = String(trace.path).slice(0, 400);
    if (trace.cwd) rec.cwd = String(trace.cwd).slice(0, 400);
    if (trace.command) rec.command = String(trace.command).slice(0, 8000);
    for (const key of ["project_root", "platform", "include"]) if (trace[key]) rec[key] = String(trace[key]).slice(0, key === "include" ? 200 : 400);
    if (trace.pattern) rec.pattern = String(trace.pattern).slice(0, 200);
    if (trace.preview) rec.preview = String(trace.preview).slice(0, 800);
    if (trace.child_id) rec.child_id = String(trace.child_id).slice(0, 80);
    if (trace.background) rec.background = true;
    if (trace.summary) rec.summary = String(trace.summary).slice(0, 2000);
    if (trace.description) rec.description = String(trace.description).slice(0, 200);
    const todos = normalizeTodos(trace.todos);
    if (todos.length) rec.todos = todos;
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
      "project_root", "platform", "include",
      "source",
    ];
    fields.forEach((key) => {
      if (event[key] != null && event[key] !== "") rec[key] = event[key];
    });
    EXECUTION_FIELDS.forEach(key => { if (Object.prototype.hasOwnProperty.call(event, key)) rec[key] = event[key]; });
    if (Object.prototype.hasOwnProperty.call(event, "exit_code")) rec.exit_code = event.exit_code;
    if (Array.isArray(event.queries)) rec.queries = event.queries;
    else if (!Array.isArray(rec.queries)) rec.queries = [];
    if (Array.isArray(event.todos)) rec.todos = normalizeTodos(event.todos);
    if (event.version != null) rec.version = event.version;
    if (event.latency_ms != null) rec.latency_ms = event.latency_ms;
    for (const key of ["duration_ms", "elapsed_ms", "timeout_ms", "call_duration_ms", "execution_duration_ms", "offset", "limit", "max_matches"]) {
      if (event[key] != null) rec[key] = event[key];
    }
    if (event.exit_code != null) rec.exit_code = event.exit_code;
    if (event.event_count != null) rec.event_count = event.event_count;
    return rec;
  }

  function serialMessages() {
    return messages
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
        if (msg.role === "assistant" && msg.model) row.model = msg.model;
        if (msg.role === "assistant" && msg.stopped) row.stopped = true;
        if (msg.role === "user" && msg.auto) row.auto = true;
        if (Array.isArray(msg.attachments) && msg.attachments.length) row.attachments = msg.attachments;
        return row;
      });
  }

  function enabledTools() {
    return searchOn ? ["web_search"] : [];
  }

  function hasMemory() {
    const fallback = ((config.context && config.context.default_instructions) || "").trim();
    const instr = (instructions || "").trim();
    const customInstr = Boolean(instr && instr !== fallback);
    return Boolean(customInstr || (profile || "").trim() || (threadContext || "").trim());
  }

  function syncMemoryUi() {
    if (memoryInstructions) memoryInstructions.value = instructions;
    if (memoryProfile) memoryProfile.value = profile;
    if (memoryThread) memoryThread.value = threadContext;
    if (memoryPanel) memoryPanel.hidden = !memoryOpen;
    if (memoryBackdrop) memoryBackdrop.hidden = !memoryOpen;
    root.classList.toggle("is-memory", memoryOpen);
    if (memoryToggle) {
      memoryToggle.setAttribute("aria-pressed", memoryOpen ? "true" : "false");
      memoryToggle.setAttribute("aria-expanded", memoryOpen ? "true" : "false");
      memoryToggle.classList.toggle("is-on", memoryOpen);
      memoryToggle.classList.toggle("has-notes", !memoryOpen && (hasMemory() || projectAgentsOk));
    }
    if (searchChip) {
      searchChip.classList.toggle("is-on", searchOn);
      searchChip.setAttribute("aria-pressed", searchOn ? "true" : "false");
      searchChip.setAttribute("aria-label", searchOn ? "Web search on" : "Web search off");
      searchChip.title = searchOn ? "Web search is on" : "Web search is off";
    }
  }

  function renderProjectAgents(data) {
    projectAgentsOk = Boolean(data && data.applicable && data.state === "loaded");
    if (!memoryProject) {
      syncMemoryUi();
      return;
    }
    if (!data || !data.applicable) {
      memoryProject.hidden = true;
      syncMemoryUi();
      return;
    }
    let label;
    if (data.state === "loaded") {
      label = "Project instructions: " + (data.name || "AGENTS.md") + " loaded from the project folder.";
    } else if (data.state === "missing") {
      label = "No AGENTS.md, agent.md, or CLAUDE.md found in the project folder.";
    } else if (data.reason === "locked") {
      label = "Project instructions unavailable: vault is locked.";
    } else {
      label = "Project instructions unavailable: host not reachable.";
    }
    memoryProject.textContent = label;
    memoryProject.classList.toggle("is-ok", data.state === "loaded");
    memoryProject.hidden = false;
    syncMemoryUi();
  }

  async function refreshProjectAgents() {
    const id = currentId;
    if (!id) {
      renderProjectAgents(null);
      return;
    }
    const thread = threadById(id);
    if (!thread || thread.kind !== "project") {
      renderProjectAgents(null);
      return;
    }
    try {
      renderProjectAgents(await api("/chat/threads/" + id + "/project-agents"));
    } catch {
      renderProjectAgents({ applicable: true, state: "error", reason: "" });
    }
  }

  function queueMemorySave() {
    memoryDirty = true;
    contextEdit += 1;
    syncStatus();
    window.clearTimeout(memoryTimer);
    memoryTimer = window.setTimeout(() => {
      saveMemory().catch((error) => showFailure("Context was not saved", error));
    }, 400);
  }

  async function saveMemory() {
    window.clearTimeout(memoryTimer);
    if (contextSaving) { await contextSaving; return saveMemory(); }
    if (!memoryDirty) return;
    if (!isLive()) {
      markDisconnected();
      return;
    }
    const edit = contextEdit;
    if (!pendingContext || pendingContext.revision !== contextRevision) {
      pendingContext = { revision: contextRevision, command_id: newSession() };
    }
    contextSaving = api("/chat/context", {
      method: "PUT",
      body: JSON.stringify({
        instructions,
        profile,
        expected_revision: contextRevision,
        command_id: pendingContext.command_id,
      }),
    });
    try {
      const data = await contextSaving;
      contextRevision = data.revision;
      pendingContext = null;
      if (edit === contextEdit) {
        memoryDirty = false;
        instructions = data.instructions;
        profile = data.profile;
        syncMemoryUi();
      }
      syncStatus();
    } catch (error) {
      if (error.status === 409) {
        pendingContext = null;
        memoryDirty = false;
        try {
          acceptContext(await api("/chat/context"));
        } catch (_) {}
        return;
      }
      if (isTransportError(error)) markDisconnected();
      syncStatus();
      throw error;
    } finally { contextSaving = null; }
  }

  let saveChain = Promise.resolve();

  function queueSave() {
    window.clearTimeout(saveTimer);
    const gen = generation;
    saveTimer = window.setTimeout(() => {
      enqueueSave(gen).catch(() => {});
    }, 200);
  }

  function enqueueSave(expectedGeneration) {
    window.clearTimeout(saveTimer);
    const task = saveChain.then(() => saveNow(expectedGeneration));
    saveChain = task.catch(() => {});
    return task;
  }

  async function flushSave() {
    window.clearTimeout(saveTimer);
    if (!dirty) return saveChain;
    return enqueueSave(generation);
  }

  async function flushPending() {
    window.clearTimeout(memoryTimer);
    if (memoryDirty) await saveMemory();
    await flushSave();
  }

  async function saveNow(expectedGeneration) {
    if (expectedGeneration !== generation) return;
    if (!dirty) return;
    if (!currentId && !messages.length) {
      dirty = false;
      pendingSave = null;
      return;
    }
    if (!isLive()) {
      markDisconnected();
      throw new Error("Can't connect to the backend");
    }
    const savedRevision = revision;
    const targetId = currentId;
    const payload = {
      model: modelId,
      context: threadContext,
      tools: enabledTools(),
    };
    if (transcriptDirty && !streaming) payload.messages = serialMessages();
    if (targetId) {
      payload.expected_revision = serverRevision;
      if (!pendingSave || pendingSave.id !== targetId || pendingSave.revision !== serverRevision) {
        pendingSave = { id: targetId, revision: serverRevision, command_id: newSession() };
      }
      payload.command_id = pendingSave.command_id;
    }
    try {
      if (!targetId) {
        pendingCreate ||= bindCreatePayload({ ...payload, command_id: newSession() });
        const created = await api("/chat/threads", { method: "POST", body: JSON.stringify(pendingCreate) });
        if (expectedGeneration !== generation) return;
        currentId = created.id;
        pendingCreate = null;
        pendingProject = null;
        serverRevision = created.revision;
        sessionId = "thread-" + currentId;
        setThreadUrl(currentId);
        upsertLocal(created);
        renderHistory();
      } else {
        const updated = await api("/chat/threads/" + targetId, { method: "PATCH", body: JSON.stringify(payload) });
        if (expectedGeneration !== generation || currentId !== targetId) return;
        serverRevision = updated.revision;
        pendingSave = null;
        upsertLocal(updated);
        renderHistory();
      }
      if (revision === savedRevision) { dirty = false; transcriptDirty = false; }
      else queueSave();
      if (!dirty) defaultHint();
      syncStatus();
    } catch (error) {
      if (expectedGeneration !== generation) throw error;
      if (error.status === 404) {
        dirty = false;
        transcriptDirty = false;
        pendingSave = null;
        threadGone(targetId);
        return;
      }
      if (error.status === 409) {
        pendingSave = null;
        dirty = false;
        transcriptDirty = false;
        await reconcileThread();
        return;
      }
      dirty = true;
      if (isTransportError(error)) markDisconnected();
      else showFailure("Chat was not saved", error);
      throw error;
    }
  }

  function setThreadUrl(id) {
    const url = new URL(window.location.href);
    if (id) url.searchParams.set("t", String(id));
    else url.searchParams.delete("t");
    const mark = id && historyMobile.matches ? { fuseChat: true } : {};
    if (mark.fuseChat && !(history.state && history.state.fuseChat)) {
      // Mobile: entering a chat leaves the previous entry (the chat list) below,
      // so a back swipe returns to the list instead of leaving the app.
      window.history.pushState(mark, "", url);
    } else {
      window.history.replaceState(mark, "", url);
    }
  }

  function syncStatus() {
    const el = root.querySelector("[data-sync-label]");
    const bar = root.querySelector(".chat-sync");
    const live = isLive();
    let text = syncLabel;
    let state = live ? "live" : syncState;
    let fault = false;
    if (syncState === "session") {
      text = "Session expired. Sign in again.";
      fault = true;
    } else if (!live) {
      if (syncState === "offline" || (syncState === "reconnecting" && reconnectAttempt > 0)) {
        text = "Disconnected";
        state = "disconnected";
      }
    } else if (pendingTurn) {
      text = "Send unconfirmed. Retry safely.";
    }
    if (el) el.textContent = text;
    if (bar) {
      bar.dataset.syncState = state;
      bar.classList.toggle("is-fault", fault);
      const hideSync = !fault && !pendingTurn && state !== "disconnected" && state !== "session";
      bar.hidden = hideSync;
    }
    const retry = root.querySelector("[data-sync-retry]");
    if (retry) retry.hidden = !(live && pendingTurn);
    syncComposer();
  }

  function syncFailure(error) {
    if (error?.status === 404 && currentId) {
      threadGone(currentId);
      return;
    }
    if (isTransportError(error) || !error?.status) markDisconnected();
    else {
      syncState = "reconnecting";
      syncLabel = "Reconnecting";
      syncStatus();
    }
  }

  function runIsStopping(run) {
    return Boolean(run && (run.cancel_requested || (stoppingRunId && run.id === stoppingRunId)));
  }

  let renderedRunState = "";
  let lastRunReceiptAt = Date.now();
  let runReconcilePending = false;
  function setRunning(repaint = true) {
    const prior = streaming;
    streaming = Boolean(activeRun && ["queued", "running"].includes(activeRun.status) && !runIsStopping(activeRun));
    document.body.classList.toggle("is-streaming", streaming);
    if (streaming) startLiveClock();
    else stopLiveClock();
    const current = threadById(currentId);
    if (current) current.running = streaming;
    const state = `${activeRun?.id || ""}:${activeRun?.message_id || ""}:${activeRun?.status || ""}:${runIsStopping(activeRun)}`;
    if (prior !== streaming || state !== renderedRunState) {
      renderedRunState = state;
      renderHistory();
      if (repaint && messages.length) schedulePaintLast();
    }
    syncRunSpinners();
    syncComposer();
    syncLiveDock();
    renderQueue();
  }

  function notifyTurnDone(threadId, payload, type) {
    try {
      if (type !== "run.completed" && type !== "run.failed") return;
      if (!window.fusePush || !window.fusePush.local) return;
      if (!("Notification" in window) || Notification.permission !== "granted") return;
      if (document.visibilityState !== "hidden" && threadId === currentId) return;
      const thread = threadById(threadId);
      const title = thread && thread.title ? `Fuse · ${thread.title}` : "Fuse";
      let body = "";
      const msg = payload && payload.message;
      if (msg) body = String(msg.error || msg.content || "").trim().slice(0, 160);
      const note = new Notification(title, { body, icon: "/static/icon-192.png", tag: "fuse-chat-" + (threadId || "x") });
      note.onclick = () => {
        try {
          window.focus();
          if (threadId && threadId !== currentId) location.assign("/chat?t=" + encodeURIComponent(threadId));
        } catch {}
      };
    } catch {}
  }

  function runIsActive(run) {
    return Boolean(run && ["queued", "running"].includes(run.status));
  }

  function messageRunLive(msg) {
    return Boolean(runIsActive(activeRun) && msg?.id === activeRun.message_id);
  }

  function healStuckRun(run) {
    if (!runIsActive(run) || !run.id) return;
    // The server checkpoints a live run constantly, so updated_at is the
    // liveness signal; creation age says nothing because agent turns may
    // legitimately run for hours. Only heal a run whose checkpoints have
    // gone stale (or one already asked to stop). 30 min is well past the
    // server 180s stall reaper, so this only fires on a truly dead row.
    const stamp = Date.parse(run.updated_at || run.created_at || "");
    const idleMs = Number.isFinite(stamp) ? Date.now() - stamp : Infinity;
    if (!run.cancel_requested && idleMs < 30 * 60 * 1000) return;
    if (healRunId === run.id) return;
    healRunId = run.id;
    const runId = run.id;
    api("/chat/runs/" + encodeURIComponent(runId))
      .then((fresh) => {
        const fr = fresh || {};
        if (!["queued", "running"].includes(fr.status)) return null;
        const freshStamp = Date.parse(fr.updated_at || fr.created_at || "");
        const freshIdle = Number.isFinite(freshStamp) ? Date.now() - freshStamp : Infinity;
        if (!fr.cancel_requested && freshIdle < 30 * 60 * 1000) return null;
        return api("/chat/runs/" + encodeURIComponent(runId) + "/cancel", { method: "POST", body: "{}" });
      })
      .then((data) => { if (data) applyCancelResult(data, runId); })
      .then(() => reconcileThread().catch(() => {}))
      .catch(() => {})
      .finally(() => { if (healRunId === runId) healRunId = ""; });
  }

  function applyCancelResult(data, runId) {
    const run = data && data.run;
    // A late acknowledgement must not install a stopped thread's run into an
    // idle thread (or resurrect a run already cleared by a terminal event).
    if (runId && activeRun?.id !== runId) return;
    if (!run) {
      if (activeRun && (!runId || activeRun.id === runId)) {
        if (!runId || stoppingRunId === runId) stoppingRunId = "";
        activeRun = null;
        setRunning();
      }
      return;
    }
    if (runIsActive(run)) {
      stoppingRunId = run.id || runId || stoppingRunId;
      activeRun = { ...(activeRun || {}), ...run, cancel_requested: true };
      setRunning();
      return;
    }
    if (activeRun && activeRun.id === (run.id || runId)) {
      if (!runId || stoppingRunId === (run.id || runId)) stoppingRunId = "";
      activeRun = null;
      setRunning();
    }
  }

  function acceptContext(data) {
    if (!data || Number(data.revision) < contextRevision) return;
    if (memoryDirty && Number(data.revision) <= contextRevision) {
      syncStatus();
      return;
    }
    memoryDirty = false;
    pendingContext = null;
    contextRevision = Number(data.revision || 0);
    instructions = data.instructions || "";
    profile = data.profile || "";
    syncMemoryUi();
    syncStatus();
  }

  function acceptThread(data) {
    if (data && !Number.isSafeInteger(data.event_cursor)) throw new Error("Thread snapshot is missing event_cursor; live sync requires a consistent snapshot.");
    if (!data || data.id !== currentId) return;
    // A snapshot may carry an older durable replay cursor than the patches
    // already applied (server state writes are throttled). Accept it and let
    // the socket replay the gap; the snapshot is the authoritative base.
    if (Number(data.event_cursor) < viewCursor) viewCursor = Number(data.event_cursor);
    lastRunReceiptAt = Date.now();
    acceptPermThread(data);
    acceptedQueueCommands = new Set(data.accepted_queue_ids || []);
    retireAcceptedQueue();
    if (editingIndex >= 0) {
      editingIndex = -1;
      editingFiles = [];
    }
    const viewport = captureThreadViewport();
    const old = new Map(messages.map((msg) => [msg.id, msg]));
    const previousReply = old.get(activeRun?.message_id);
    if (previousReply && activeRun?.id === data.run?.id) {
      const frozen = data.messages.find(msg => msg.role === "assistant" && msg.tools_live && !old.has(msg.id) &&
        (previousReply.content || previousReply.reasoning || previousReply.tools?.length) &&
        String(msg.content || "").startsWith(previousReply.content || "") && String(msg.reasoning || "").startsWith(previousReply.reasoning || "") &&
        (previousReply.tools || []).every(tool => (msg.tools || []).some(next => next.id === tool.id)));
      if (frozen) {
        for (const key of ["stepOpen", "activityOpenMap"]) if (previousReply[key]) frozen[key] = { ...previousReply[key] };
        const node = [...log.children].find(el => el.dataset.messageId === previousReply.id);
        if (node) { node.dataset.messageId = frozen.id; node._rebind?.(frozen); }
        if (viewport.anchorId === previousReply.id) viewport.anchorId = frozen.id;
        delete previousReply._liveOrigin;
        delete previousReply._elapsedObserved;
      }
    }
    const same = messages.length <= data.messages.length && log.children.length === messages.length &&
      messages.every((msg, i) => msg.id === data.messages[i].id && log.children[i].dataset.messageId === msg.id);
    const oldLength = messages.length;
    contextMessages = Array.isArray(data.context_messages) ? data.context_messages.map((msg) => ({ ...msg, noActions: true })) : [];
    contextLive = Boolean(data.context_live);
    messages = data.messages.map((msg) => {
      const prior = old.get(msg.id);
      if (!prior) return { ...msg };
      Object.assign(prior, msg);
      delete prior._raw;
      delete prior._reason;
      return prior;
    });
    serverRevision = data.revision;
    viewCursor = Number(data.event_cursor || 0);
    threadWatermark = Number.isSafeInteger(data.thread_cursor) ? Number(data.thread_cursor) : viewCursor;
    activeRun = data.run;
    if (activeRun && stoppingRunId && activeRun.id === stoppingRunId) activeRun.cancel_requested = true;
    if (!runIsActive(activeRun) && (!activeRun || !stoppingRunId || activeRun.id === stoppingRunId)) stoppingRunId = "";
    setRunning(false);
    healStuckRun(activeRun);
    threadContext = data.context || "";
    searchOn = data.tools?.includes("web_search") ?? true;
    const listed = resolveListedModel(data.model);
    if (listed && modelId !== listed) { modelId = listed; renderPickerButton(); }
    if (data.compaction && data.compaction.meter) acceptMeter(data.compaction.meter);
    else syncContextMeter();
    dirty = false;
    transcriptDirty = false;
    pendingSave = null;
    syncMemoryUi();
    if (same) {
      foldAutoCommandResults();
      reconcileBgTraces([...completedJobs.values()], false);
      messages.forEach((msg, i) => {
        if (i >= oldLength) {
          const live = messageRunLive(msg);
          const article = msg.role === "user" ? renderUser(msg, i, live) : renderAssistant(msg, live);
          article.dataset.messageId = msg.id || "";
          log.appendChild(article);
          return;
        }
        const article = log.children[i];
        if (msg.role === "assistant") {
          const live = messageRunLive(msg);
          updateAssistant(article, msg, live);
          if (!live) mountActions(article, i);
        } else if (article.querySelector(".chat-bubble")) {
          if (article.querySelector(".chat-bubble").textContent !== msg.content) article.querySelector(".chat-bubble").textContent = msg.content;
          fillUserFiles(article, msg, i);
        }
      });
      setActive(messages.length > 0);
      syncComposer();
    } else paint();
    // This snapshot already reconciles children transactionally. Cached list
    // rows/events can predate a resume and must not overwrite it now or later.
    agentLifecycle.clear();
    agentLifecycleEpoch += 1;
    syncEmptyReplyRetry();
    upsertLocal(data);
    renderHistory();
    restoreThreadViewport(viewport);
    rememberLatestArtifact();
    syncStatus();
  }

  async function reconcileThread() {
    const id = currentId;
    if (!id) return;
    const gen = generation;
    const load = ++loadEpoch;
    let data;
    // Keep the race-healing read, but avoid rebuilding/downloading a snapshot
    // already represented by this view (including in-flight run progress).
    const query = !dirty && !transcriptDirty
      ? "?observed_cursor=" + viewCursor + "&observed_thread_cursor=" + threadWatermark
      : "";
    try { data = await api("/chat/threads/" + id + query); }
    catch (error) {
      if (id !== currentId || gen !== generation || load !== loadEpoch) return;
      if (error.status !== 404) throw error;
      threadGone(id);
      return;
    }
    if (id !== currentId || gen !== generation || load !== loadEpoch) return;
    if (!data.unchanged) acceptThread(data);
    if (!streaming) drainQueue();
  }

  async function receiveEvent(event) {
    if (event.id <= cursor) return;
    const payload = event.payload || {};
    if (event.type === "context.updated") {
      if (contextSaving) await contextSaving.catch(() => {});
      acceptContext(payload);
    } else if (event.type === "compaction.done" || event.type === "compaction.updated" || event.type === "compaction.pruned") {
      if (event.thread_id === currentId) {
        if (payload && payload.meter) acceptMeter(payload.meter);
        else if (payload && payload.compaction && payload.compaction.meter) acceptMeter(payload.compaction.meter);
        else syncContextMeter();
        reconcileThread().catch(() => {});
      }
    } else if (event.type.startsWith("thread.")) {
      if (renameSaving) await renameSaving.catch(() => {});
      searchGen += 1;
      resetSearchLists();
      if (historyQuery) queueSearch();
      if (event.type === "thread.deleted") {
        threadGone(payload.id || event.thread_id);
      } else {
        const prior = threadById(payload.id);
        if (!prior || payload.revision >= prior.revision) upsertLocal(payload);
        if (event.thread_id === currentId && event.id > viewCursor) {
          await flushSave().catch(() => {});
          if (event.thread_id === currentId && payload.revision > serverRevision) {
            if (activeRun && !Array.isArray(payload.messages) && !payload.transcript_changed) {
              serverRevision = payload.revision;
            } else {
              await reconcileThread();
            }
          }
        }
      }
      renderHistory();
    } else if (event.type === "title.failed") {
      if (event.thread_id === currentId) setHint("Title generation failed. Try /title again.", true);
    } else if (event.type.startsWith("run.") && event.thread_id && event.thread_id !== currentId &&
               (event.type === "run.started" || ["run.completed", "run.cancelled", "run.failed", "run.interrupted"].includes(event.type))) {
      const item = threadById(event.thread_id);
      if (item) {
        const next = event.type === "run.started";
        if (item.running !== next) {
          item.running = next;
          renderHistory();
        }
      }
      notifyTurnDone(event.thread_id, payload, event.type);
    } else if (event.thread_id === currentId && event.type === "agent.tick") {
      // Compact progress for subagents this socket is not viewing. Enough
      // for the Work list and permission asks; the full patch stream of the
      // viewed agent arrives as regular agent.progress.
      if (Array.isArray(payload.permissions)) syncPermQueueFromMessage({ permissions: payload.permissions });
      const row = agentsRows.find((item) => String(item.id) === String(payload.run_id));
      if (row) {
        if (Object.prototype.hasOwnProperty.call(payload, "current")) row.current = payload.current;
        row.quiet_s = 0;
        row.updated_at = new Date().toISOString();
        if (agentsOpen && Date.now() - _agentTickRenderAt > 1000) {
          _agentTickRenderAt = Date.now();
          renderAgentsList();
        }
      }
    } else if (event.thread_id === currentId && event.type.startsWith("agent.")) {
      const cid = payload.child_id || payload.run_id;
      if (cid && event.type !== "agent.progress") {
        agentLifecycleEpoch += 1;
        const row = { ...payload, id: cid };
        if (!row.status && ["agent.started", "agent.resumed"].includes(event.type)) row.status = "queued";
        agentLifecycle.set(String(cid), row);
      }
      if (agentsOpen && cid === agentViewId && !agentRun) agentPendingEvents.push(event);
      if (cid && event.type !== "agent.progress") reconcileAgentTraces([payload], event.type === "agent.started" || event.type === "agent.resumed");
      if (event.type === "agent.progress") {
        if (Array.isArray(payload.permissions)) syncPermQueueFromMessage({ permissions: payload.permissions });
        if (payload.run_id === agentViewId) agentLastEventAt = Date.now();
        if (agentsOpen && agentViewId && payload.run_id === agentViewId && agentRun && event.id > (agentRun.event_cursor || 0)) {
          if (agentRun.status === "queued") agentRun.status = "running";
          agentRun.updated_at = new Date().toISOString();
          if (!agentRun.message || typeof agentRun.message !== "object") agentRun.message = {};
          applyProgressPayload(agentRun.message, payload, true);
          agentRun.event_cursor = event.id;
          scheduleAgentView();
          // Compaction rewrites the child's provider transcript. The live
          // bubble above is the post-handoff continuation; reload the seed so
          // the summary is shown and the replaced turns leave the panel.
          const compactedNow = (payload.events || []).some((obj) => {
            const fuse = obj && obj.fuse;
            return fuse && (fuse.event === "context" && fuse.phase === "compacted" || fuse.event === "notice" && compactionMarkerText(fuse.message));
          });
          if (compactedNow) pollAgentView();
        }
      }
      if (event.type === "agent.started" || !event.type.startsWith("agent.progress")) {
        refreshAgents();
      } else if (agentsOpen && Date.now() - agentsRefreshAt > 1500) {
        agentsRefreshAt = Date.now();
        refreshAgents();
      }
      if (agentsOpen && agentViewId && cid === agentViewId && event.type !== "agent.progress") {
        if (agentRun && event.id > (agentRun.event_cursor || 0)) {
          agentRun.event_cursor = event.id;
          if (payload.status) agentRun.status = payload.status;
          else if (event.type === "agent.resumed" || event.type === "agent.started") agentRun.status = "queued";
          if (payload.cancel_requested != null) agentRun.cancel_requested = payload.cancel_requested;
          else if (event.type === "agent.resumed") agentRun.cancel_requested = false;
          if (payload.message) applyProgressPayload(agentRun.message, payload, true);
          scheduleAgentView();
        }
        pollAgentView();
      }
    } else if (event.thread_id === currentId && event.id > viewCursor && event.type.startsWith("run.")) {
      lastRunReceiptAt = Date.now();
      if (event.type === "run.started" || !activeRun || activeRun.id !== payload.run_id) {
        await reconcileThread();
      } else if (event.type === "run.progress" && editingIndex < 0) {
        if (activeRun.status === "queued") activeRun.status = "running";
        const msg = messages.find((item) => item.id === payload.message_id);
        if (!msg) await reconcileThread();
        else {
          applyProgressPayload(msg, payload);
          // Trace reconciliation walks every message x tool x lifecycle row;
          // run.progress arrives ~5x/s while streaming, so match the agent.tick
          // path's 1s cadence. Terminal agent events still reconcile at once,
          // and thread snapshots rebuild traces from scratch.
          if (Date.now() - agentTraceRenderAt > 1000) {
            agentTraceRenderAt = Date.now();
            reconcileAgentTraces([...agentLifecycle.values()]);
          }
          for (const update of payload.updates || []) {
            const index = messages.findIndex((item) => item.id === update.id);
            if (index < 0) { await reconcileThread(); break; }
            Object.assign(messages[index], update);
            if (log.children[index]) updateAssistant(log.children[index], messages[index], false);
          }
          viewCursor = event.id;
          threadWatermark = event.id;
          schedulePaintLast();
        }
      } else if (event.type === "run.cancel_requested") {
        if (activeRun && activeRun.id === payload.run_id) {
          stoppingRunId = payload.run_id || stoppingRunId;
          activeRun.cancel_requested = true;
          setRunning();
          setHint("Stopping…");
        }
        viewCursor = event.id;
        threadWatermark = event.id;
      } else if (["run.completed", "run.cancelled", "run.failed", "run.interrupted"].includes(event.type)) {
        const msg = messages.find((item) => item.id === payload.message_id);
        if (msg && payload.message) Object.assign(msg, payload.message);
        if (msg && Array.isArray(msg.permissions)) {
          msg.permissions.forEach((perm) => {
            if (perm.kind === "tool_permission" && perm.status === "pending") perm.status = "cancelled";
          });
        }
        sweepStuckPermCards();
        // Only the run the UI is tracking may clear live state; a late or
        // out-of-order terminal event for a previous run must not kill the
        // current turn's display.
        const isTracked = !activeRun || activeRun.id === payload.run_id;
        if (isTracked) {
          stoppingRunId = "";
          activeRun = null;
          setRunning();
        }
        notifyTurnDone(event.thread_id, payload, event.type);
        if (payload.revision != null) serverRevision = payload.revision;
        viewCursor = event.id;
        threadWatermark = event.id;
        if (editingIndex < 0 && msg) {
          const article = log.children[messages.indexOf(msg)];
          if (article) mountActions(article, messages.indexOf(msg));
        }
        syncStatus();
        syncContextMeter();
        await reconcileThread();
        if (isTracked) drainQueue();
      }
    }
    cursor = Math.max(cursor, event.id);
  }

  async function connectSync(resync = !syncInitialized) {
    if (!resync && isLive()) return;
    const epoch = ++syncEpoch;
    window.clearTimeout(reconnectTimer);
    window.clearTimeout(heartbeatTimer);
    if (socket) { socket.onclose = null; socket.close(); socket = null; }
    syncState = navigator.onLine ? "connecting" : "offline";
    syncLabel = navigator.onLine ? (reconnectAttempt ? "Reconnecting" : "Connecting") : "Disconnected";
    syncStatus();
    const retry = () => {
      if (epoch !== syncEpoch) return;
      syncState = navigator.onLine ? "reconnecting" : "offline";
      syncLabel = navigator.onLine ? (reconnectAttempt ? "Disconnected" : "Reconnecting") : "Disconnected";
      syncStatus();
      reconnectTimer = window.setTimeout(() => connectSync(resync), Math.min(30000, 700 * 2 ** Math.min(reconnectAttempt++, 6)) * (0.75 + Math.random() * 0.5));
    };
    try {
      if (resync) {
        const data = await api("/chat/sync");
        if (epoch !== syncEpoch) return;
        cursor = data.cursor;
        syncInitialized = true;
        threads = data.threads.filter((item) => !item.archived);
        archivedThreads = data.threads.filter((item) => item.archived);
        if (Array.isArray(data.projects)) projects = data.projects;
        acceptContext(data.context);
        resetSearchLists();
        if (historyQuery) queueSearch();
        renderHistory();
        await reconcileThread();
        if (epoch !== syncEpoch) return;
        // The socket must not start ahead of the thread snapshot's durable
        // cursor, or patches between the two would never be replayed.
        if (Number.isSafeInteger(viewCursor) && viewCursor > 0) cursor = Math.min(cursor, viewCursor);
      }
      const url = new URL("/chat/events", location.href);
      url.protocol = location.protocol === "https:" ? "wss:" : "ws:";
      url.searchParams.set("cursor", String(cursor));
      const ws = socket = new WebSocket(url);
      ws.onopen = () => {
        heartbeat();
        // Re-assert the viewed subagent so a reconnect keeps full patches
        // coming for it (the new socket starts with no view).
        _syncViewSent = null;
        sendSyncView(agentsOpen ? agentViewId : "");
      };
      const heartbeat = () => {
        window.clearTimeout(heartbeatTimer);
        heartbeatTimer = window.setTimeout(() => {
          if (epoch !== syncEpoch) return;
          markDisconnected();
          ws.close();
        }, HEARTBEAT_MS);
      };
      let chain = Promise.resolve();
      ws.onmessage = (message) => {
        if (epoch !== syncEpoch) return;
        heartbeat();
        chain = chain.then(async () => {
          if (epoch !== syncEpoch) return;
          const data = JSON.parse(message.data);
          if (data.type === "ping") { ws.send("pong"); return; }
          if (data.type === "resync_required") { connectSync(true); return; }
          if (data.type === "events") for (const event of data.events || []) {
            if (epoch !== syncEpoch) return;
            try {
              await receiveEvent(event);
            } catch (error) {
              // A single poison event used to reject the whole sync chain,
              // closing the socket and stranding the run spinner until a
              // refresh. Heal instead: skip the event and refetch state.
              console.warn("Sync event handler failed", event && event.type, error);
              cursor = Math.max(cursor, Number(event && event.id) || cursor);
              if (event.type?.startsWith("agent.") && agentViewId) pollAgentView().catch(() => {});
              else reconcileThread().catch(() => {});
            }
          }
          if (data.type === "ready") {
            cursor = Math.max(cursor, data.cursor);
            await reconcileThread();
            if (epoch !== syncEpoch) return;
            reconnectAttempt = 0;
            syncState = "live";
            syncLabel = "Live sync";
            syncStatus();
            flushPending().catch(() => {});
          }
        }).catch((error) => { syncFailure(error); if (epoch === syncEpoch) ws.close(); });
      };
      ws.onclose = (event) => {
        _syncViewSent = "";
        if (event.code === 4401) {
          syncState = "session";
          syncLabel = "Session expired. Sign in again.";
          syncStatus();
          return;
        }
        retry();
      };
      ws.onerror = () => ws.close();
    } catch (error) { syncFailure(error); retry(); }
  }

  root.querySelector("[data-sync-retry]").addEventListener("click", () => {
    if (!requireLive()) return;
    if (pendingTurn && pendingTurn.id === currentId) completeTurn(pendingTurn.body.text);
    else setHint("Open the chat with the unconfirmed send to retry it.", true);
  });
  window.addEventListener("online", () => connectSync());
  window.addEventListener("offline", () => markDisconnected());
  window.addEventListener("pageshow", (event) => { if (event.persisted) connectSync(); });
  document.addEventListener("visibilitychange", () => { if (!document.hidden && !isLive()) connectSync(); });
  window.addEventListener("beforeunload", (event) => {
    if (dirty || memoryDirty || pendingTurn || pendingCreate) { event.preventDefault(); event.returnValue = ""; }
  });

  function upsertLocal(thread) {
    if (!thread || !thread.id) return;
    const bucket = thread.archived ? archivedThreads : threads;
    const other = thread.archived ? threads : archivedThreads;
    const idx = bucket.findIndex((item) => item.id === thread.id);
    const prior = idx >= 0 ? bucket[idx] : other.find((item) => item.id === thread.id);
    let running;
    if (thread.id === currentId) running = streaming;
    else if (thread.run && thread.run.cancel_requested) running = false;
    else if (Object.prototype.hasOwnProperty.call(thread, "running")) running = Boolean(thread.running);
    else if (thread.run && ["queued", "running"].includes(thread.run.status)) running = true;
    else running = Boolean(prior && prior.running);
    const lite = {
      id: thread.id,
      revision: thread.revision,
      title: thread.title,
      model: thread.model,
      archived: thread.archived,
      created_at: thread.created_at,
      kind: thread.kind || "chat",
      host_id: thread.host_id ?? null,
      project_id: thread.project_id ?? null,
      host_alias: thread.host_alias || "",
      project_name: thread.project_name || "",
      updated_at: thread.updated_at,
      running,
    };
    if (idx >= 0) bucket[idx] = lite;
    else bucket.unshift(lite);
    const drop = other.findIndex((item) => item.id === thread.id);
    if (drop >= 0) other.splice(drop, 1);
    bucket.sort((a, b) => String(b.updated_at).localeCompare(String(a.updated_at)));
    if (thread.kind === "project" && thread.id === currentId && !historyQuery) {
      setHistoryTab("projects", { persist: true, silent: true });
    }
    syncTitle();
  }

  function dropThread(id) {
    if (id == null) return;
    pendingDeletes.delete(id);
    threads = threads.filter((item) => item.id !== id);
    archivedThreads = archivedThreads.filter((item) => item.id !== id);
    searchThreads = searchThreads.filter((item) => item.id !== id);
    searchArchived = searchArchived.filter((item) => item.id !== id);
    searchProjects = searchProjects.filter((item) => item.thread_id !== id);
  }

  function threadGone(id) {
    dropHistoryStacks(id);
    dropThread(id);
    if (currentId === id) clearChatState();
    else renderHistory();
  }

  function nearBottom() {
    const gap = thread.scrollHeight - thread.scrollTop - thread.clientHeight;
    return gap < 96;
  }

  function captureThreadViewport() {
    const edge = thread.getBoundingClientRect().top;
    const anchor = [...log.children].find(el => !el.hidden && el.getBoundingClientRect().bottom > edge);
    return { threadId: log._threadId === undefined ? currentId : log._threadId, top: thread.scrollTop, following: stickToBottom,
      anchorId: anchor?.dataset.messageId, offset: anchor ? anchor.getBoundingClientRect().top - edge : 0 };
  }

  function restoreThreadViewport(saved) {
    if (!saved || saved.threadId !== currentId) return;
    const anchor = saved.anchorId && [...log.children].find(el => el.dataset.messageId === saved.anchorId && !el.hidden);
    // Restore the reading anchor synchronously, before a follow animation can
    // start. Never animate from the temporary zero caused by replacing nodes.
    const target = anchor ? thread.scrollTop + anchor.getBoundingClientRect().top - thread.getBoundingClientRect().top - saved.offset : saved.top;
    thread.scrollTop = Math.max(0, target);
    lastThreadScrollTop = thread.scrollTop;
    if (saved.following && stickToBottom) scrollThread();
    else syncLatest();
  }

  function syncLatest() {
    const latest = root.querySelector("[data-chat-latest]");
    if (latest) latest.hidden = !messages.length || thread.scrollHeight - thread.scrollTop - thread.clientHeight <= 1 || (stickToBottom && nearBottom());
  }

  function threadFollowTarget() {
    return Math.max(0, thread.scrollHeight - thread.clientHeight);
  }

  function snapThread() {
    if (!thread || !stickToBottom) return;
    const target = threadFollowTarget();
    if (Math.abs(target - thread.scrollTop) > 0.5) thread.scrollTop = target;
    lastThreadScrollTop = thread.scrollTop;
    syncLatest();
  }

  let threadFollowFrame = 0;
  let threadFollowThread = null;
  let lastThreadScrollTop = 0;
  let resumeFollowOnScroll = true;
  function pauseThreadFollow() {
    stickToBottom = false;
    resumeFollowOnScroll = false;
    ignoreScroll = false;
    if (threadFollowFrame) cancelAnimationFrame(threadFollowFrame);
    if (liveDockFollowFrame) cancelAnimationFrame(liveDockFollowFrame);
    threadFollowFrame = liveDockFollowFrame = 0;
    lastThreadScrollTop = thread.scrollTop;
    syncLatest();
  }

  function scrollThread() {
    const entering = threadFollowThread !== currentId;
    threadFollowThread = currentId;
    if (!stickToBottom) {
      syncLatest();
      return;
    }
    const target = threadFollowTarget();
    const gap = target - thread.scrollTop;
    if (entering || reduceMotion.matches || !streaming || Math.abs(gap) <= 1) {
      if (threadFollowFrame) {
        cancelAnimationFrame(threadFollowFrame);
        threadFollowFrame = 0;
      }
      snapThread();
      return;
    }
    if (threadFollowFrame) return;
    let lastTime = performance.now();
    const step = now => {
      if (!stickToBottom) { threadFollowFrame = 0; return; }
      const next = threadFollowTarget();
      const remain = next - thread.scrollTop;
      if (reduceMotion.matches || !streaming || Math.abs(remain) <= 1) {
        snapThread();
        threadFollowFrame = 0;
        return;
      }
      const dt = Math.min(48, Math.max(8, now - lastTime));
      lastTime = now;
      thread.scrollTop += Math.sign(remain) * Math.max(1, Math.abs(remain) * (1 - Math.exp(-dt / 48)));
      threadFollowFrame = requestAnimationFrame(step);
    };
    threadFollowFrame = requestAnimationFrame(step);
    syncLatest();
  }

  function sessionProject() {
    if (pendingProject) return pendingProject;
    const current = threadById(currentId);
    if (current && current.kind === "project") {
      const info = resolveProjectInfo(current.host_id, current.project_id);
      return {
        host_id: current.host_id,
        project_id: current.project_id,
        host_alias: info?.host_alias || current.host_alias || "",
        project_name: info?.project_name || current.project_name || "",
        remote_path: info?.remote_path || "",
        permission_mode: info?.permission_mode || "",
      };
    }
    return null;
  }

  function isUntitledThread(current) {
    if (!current) return true;
    const title = (current.title || "").trim();
    if (!title || title.toLowerCase() === "new chat") return true;
    if (current.kind === "project" && current.project_name &&
        title.toLowerCase() === String(current.project_name).trim().toLowerCase()) return true;
    return false;
  }

  function syncTitle() {
    const current = threadById(currentId);
    const bound = sessionProject();
    const untitled = isUntitledThread(current);
    const name = untitled ? "" : current.title;
    if (titleEl) {
      titleEl.textContent = name;
      titleEl.classList.toggle("is-untitled", untitled);
    }
    root.classList.toggle("is-untitled", untitled);
    if (projectChip) {
      const project = bound && bound.project_name;
      if (bound && project) {
        projectChip.hidden = false;
        if (projectName) projectName.textContent = project;
        projectChip.title = [project, ...projectWorkspaceDetails(bound)].join(" | ");
        projectChip.setAttribute("aria-label", projectChip.title);
      } else {
        projectChip.hidden = true;
        if (projectName) projectName.textContent = "";
        projectChip.removeAttribute("title");
        projectChip.removeAttribute("aria-label");
      }
    }
    root.classList.toggle("is-project", Boolean(bound));
    if (welcomeEl) {
      if (bound && (bound.project_name || bound.host_alias)) {
        const project = bound.project_name || "project";
        const alias = bound.host_alias || "host";
        welcomeEl.textContent = `Work on ${project} on ${alias}.`;
      } else {
        welcomeEl.textContent = "What are we building?";
      }
    }
    document.title = currentId && name && name !== "New chat" ? name + " · Fuse" : "Chat · Fuse";
    scheduleFitChatBar();
  }

  let chatBarFitRaf = 0;

  function fitChatBar() {
    const bar = root.querySelector(".chat-bar");
    if (!bar) return;
    const following = stickToBottom;
    const savedTop = thread ? thread.scrollTop : 0;
    const beforeH = root.style.getPropertyValue("--chat-bar-h");
    const beforeCompact = bar.classList.contains("is-compact");
    if (window.matchMedia("(max-width: 800px)").matches) {
      bar.classList.remove("is-compact");
      root.style.setProperty("--chat-bar-h", Math.ceil(bar.getBoundingClientRect().height) + "px");
      if (following && (beforeH !== root.style.getPropertyValue("--chat-bar-h") || beforeCompact)) restoreThreadScroll(true, savedTop);
      return;
    }
    root.style.removeProperty("--chat-bar-h");
    const actions = bar.querySelector(".chat-bar-actions");
    if (!actions) return;
    bar.classList.remove("is-compact");
    const overflow = bar.scrollWidth - bar.clientWidth > 1;
    const actionsOverflow = actions.scrollWidth - actions.clientWidth > 1;
    const titleWrap = titleEl && titleEl.closest(".chat-bar-title");
    const titled = Boolean(
      titleWrap &&
      titleEl &&
      !titleEl.classList.contains("is-untitled") &&
      getComputedStyle(titleWrap).display !== "none"
    );
    const titleBox = titled ? titleWrap.getBoundingClientRect().width : 0;
    const titleCrushed = titled && titleEl.scrollWidth - titleWrap.clientWidth > 1 && titleBox < 88;
    bar.classList.toggle("is-compact", overflow || actionsOverflow || titleCrushed);
    if (following && (beforeH !== root.style.getPropertyValue("--chat-bar-h") || beforeCompact !== bar.classList.contains("is-compact"))) {
      restoreThreadScroll(true, savedTop);
    }
  }

  function scheduleFitChatBar() {
    if (chatBarFitRaf) return;
    chatBarFitRaf = window.requestAnimationFrame(() => {
      chatBarFitRaf = 0;
      fitChatBar();
    });
  }

  let dockTimer = 0;

  function beginDockFx() {
    root.classList.add("is-docking");
    if (dockTimer) window.clearTimeout(dockTimer);
    dockTimer = window.setTimeout(() => {
      dockTimer = 0;
      root.classList.remove("is-docking");
      root.classList.remove("is-entering");
    }, 480);
  }

  function playDockTransition() {
    if (isHarnessView()) {
      playTranscriptFx();
      return;
    }
    const stage = root.querySelector(".chat-stage");
    if (stage) {
      const slide = -Math.max(0, (stage.clientHeight - form.offsetHeight) / 2);
      root.style.setProperty("--dock-slide", slide.toFixed(1) + "px");
    }
    beginDockFx();
  }

  function playTranscriptFx() {
    root.classList.remove("is-docking");
    root.classList.add("is-entering");
    if (dockTimer) window.clearTimeout(dockTimer);
    dockTimer = window.setTimeout(() => {
      dockTimer = 0;
      root.classList.remove("is-entering");
    }, 480);
  }

  function setActive(on) {
    const wasEmpty = root.classList.contains("is-empty");
    root.classList.toggle("is-active", on);
    root.classList.toggle("is-empty", !on);
    if (on && wasEmpty) playDockTransition();
    if (!on && dockTimer) {
      window.clearTimeout(dockTimer);
      dockTimer = 0;
      root.classList.remove("is-docking");
      root.classList.remove("is-entering");
    }
    if (on) root.classList.remove("is-composing");
    log.hidden = !on;
    syncTitle();
    syncEmptyComposer();
    syncContextMeter();
  }

  function syncEmptyComposer() {
    const filling = Boolean((input && input.value.trim()) || pendingAttachments.length);
    const empty = root.classList.contains("is-empty");
    if (!empty || !filling) {
      root.classList.remove("is-composing");
      return;
    }
    root.classList.add("is-composing");
  }

  function closePop() {
    if (pop) pop.remove();
    pop = null;
    historyList && historyList.querySelectorAll(".is-menu").forEach((el) => el.classList.remove("is-menu"));
  }

  function showPop(anchor, items) {
    closePop();
    pop = document.createElement("div");
    pop.className = "chat-pop";
    items.forEach((item) => {
      if (item.sep) {
        const rule = document.createElement("div");
        rule.className = "chat-pop-sep";
        rule.setAttribute("aria-hidden", "true");
        pop.appendChild(rule);
        return;
      }
      const btn = document.createElement("button");
      btn.type = "button";
      btn.textContent = item.label;
      if (item.danger) btn.className = "danger";
      btn.addEventListener("click", (event) => {
        event.preventDefault();
        closePop();
        Promise.resolve(item.run()).catch((error) => showFailure(`${item.label} failed`, error));
      });
      pop.appendChild(btn);
    });
    document.body.appendChild(pop);
    const rect = anchor.getBoundingClientRect();
    const width = pop.offsetWidth;
    const height = pop.offsetHeight;
    let left = rect.right - width;
    let top = rect.bottom + 4;
    if (left < 8) left = 8;
    if (left + width > window.innerWidth - 8) left = window.innerWidth - width - 8;
    if (top + height > window.innerHeight - 8) top = Math.max(8, rect.top - height - 4);
    pop.style.left = left + "px";
    pop.style.top = top + "px";
  }

  function setHistoryTab(tab, opts) {
    const next = tab === "projects" ? "projects" : "chats";
    const persist = !opts || opts.persist !== false;
    const silent = Boolean(opts && opts.silent);
    historyTab = next;
    if (persist) {
      try { localStorage.setItem(STORE_TAB, next); } catch (_) {}
    }
    if (next === "chats") showArchived = showArchived;
    else showArchived = false;
    syncHistoryChrome();
    if (!silent) renderHistory();
  }

  function syncHistoryChrome() {
    root.querySelectorAll("[data-history-tab]").forEach((btn) => {
      const on = btn.getAttribute("data-history-tab") === historyTab;
      btn.setAttribute("aria-selected", String(on));
      btn.classList.toggle("is-on", on);
    });
    const heading = root.querySelector("[data-history-heading]");
    if (heading) heading.textContent = historyTab === "projects" ? "Projects" : "Chats";
    const newLabel = root.querySelector("[data-chat-new-label]");
    const newBtn = root.querySelector("[data-chat-new]");
    const projectLabel = historyTab === "projects";
    if (newLabel) newLabel.textContent = projectLabel ? "New project" : "New chat";
    if (newBtn) {
      newBtn.setAttribute("aria-label", projectLabel ? "New project" : "New chat");
      newBtn.title = projectLabel ? "New project" : "New chat";
    }
    if (!projectLabel) setProjectPickerOpen(false);
    const filters = root.querySelector("[data-project-filters]");
    if (filters) {
      filters.hidden =
        historyTab !== "projects" ||
        Boolean(historyQuery.trim()) ||
        showArchived ||
        Boolean(projectPicker && !projectPicker.hidden);
    }
    fillProjectFilterOptions();
    syncComposer();
  }

  function filteredHistory() {
    const source = showArchived ? archivedThreads : threads.filter((item) => item.kind !== "project");
    const q = historyQuery.trim().toLowerCase();
    if (!q) return [...source].sort(runningFirst);
    if (searchActiveQuery && searchActiveQuery.toLowerCase() === q) {
      return [...searchThreads, ...searchArchived].sort(runningFirst);
    }
    return [...source].sort(runningFirst).filter((item) => (item.title || "").toLowerCase().includes(q));
  }

  function stampSpinHtml(html, stamp) {
    const tag = String(stamp || "run").replace(/[^A-Za-z0-9_-]/g, "") || "run";
    return html.replace(/id="cas-/g, `id="${tag}-cas-`).replace(/url\(#cas-/g, `url(#${tag}-cas-`);
  }

  function spinAge(origin) {
    const elapsed = Math.max(0, Date.now() - (origin || Date.now()));
    return `${((elapsed % SPINNER_HOLD_MS) / 1000).toFixed(3)}s`;
  }

  function currentRunSpin() {
    if (reduceMotion.matches) return { key: "still", html: SPINNER_STILL };
    const win = Math.floor(Date.now() / SPINNER_HOLD_MS);
    if (currentSpin === null || win !== spinWindow) {
      spinWindow = win;
      currentSpin = pickRandomSpin();
      recentSpinIds.push(currentSpin.id);
      if (recentSpinIds.length > SPINNER_MEMORY) recentSpinIds.shift();
    }
    return { key: currentSpin.id, html: currentSpin.html };
  }
  window.fuseSpinDebug = {
    state: () => ({ current: currentSpin && currentSpin.id, recent: [...recentSpinIds], window: spinWindow }),
    pick: pickRandomSpin,
    tick: currentRunSpin,
  };

  function applySpinEl(el, spin, stamp, swap, origin) {
    if (!el || !spin) return;
    const current = el.querySelector(":scope > .cas-face:not(.cas-swap-out)");
    if (el.dataset.spinSet === spin.key && current) return;
    const swapping = swap !== false && Boolean(el.dataset.spinSet) && Boolean(current) && !reduceMotion.matches;
    el.dataset.spinSet = spin.key;
    const face = document.createElement("span");
    face.className = "cas-face";
    if (origin) face.style.setProperty("--cas-age", spinAge(origin));
    face.innerHTML = stampSpinHtml(spin.html, stamp);
    if (!swapping) {
      el.replaceChildren(face);
      return;
    }
    face.classList.add("cas-swap-in");
    el.querySelectorAll(":scope > .cas-face").forEach((prev) => {
      prev.classList.remove("cas-swap-in");
      prev.classList.add("cas-swap-out");
      const done = () => prev.remove();
      prev.addEventListener("animationend", done, { once: true });
      window.setTimeout(done, 480);
    });
    el.appendChild(face);
  }

  function runSpinMarkup(stamp) {
    const spin = currentRunSpin();
    const age = runSpinOrigin ? ` style="--cas-age:${spinAge(runSpinOrigin)}"` : "";
    return `<span class="chat-run-spin" data-run-spin="${escapeHtml(String(stamp))}" data-spin-set="${spin.key}" aria-hidden="true"><span class="cas-face"${age}>${stampSpinHtml(spin.html, stamp)}</span></span>`;
  }

  function fillSpinEl(el, stamp, swap) {
    applySpinEl(el, currentRunSpin(), stamp, swap, runSpinOrigin);
  }

  function setSpinVisible(el, on, stamp) {
    if (!el) return;
    el.hidden = !on;
    if (on) fillSpinEl(el, stamp, false);
    else {
      el.replaceChildren();
      delete el.dataset.spinSet;
    }
  }

  function threadIsRunning(item) {
    return Boolean(item && item.running);
  }

  function anyRunningOfKind(kind) {
    const match = (item) => threadIsRunning(item) && (kind === "project" ? item.kind === "project" : item.kind !== "project");
    return threads.some(match) || archivedThreads.some(match) || searchThreads.some(match) || searchArchived.some(match);
  }

  function stopRunSpinClock() {
    if (runSpinTimer) {
      window.clearInterval(runSpinTimer);
      runSpinTimer = 0;
    }
  }

  function startRunSpinClock() {
    if (runSpinTimer || reduceMotion.matches) return;
    runSpinTimer = window.setInterval(() => {
      const nodes = [...root.querySelectorAll("[data-run-spin]")].filter((el) => !el.hidden);
      if (!nodes.length) {
        stopRunSpinClock();
        return;
      }
      nodes.forEach((el) => fillSpinEl(el, el.getAttribute("data-run-spin") || "run"));
    }, 250);
  }

  function syncRunSpinners() {
    const chatBusy = anyRunningOfKind("chat");
    const projectBusy = anyRunningOfKind("project");
    const current = threadById(currentId);
    const currentBusy = streaming;
    const headingBusy = historyTab === "projects" ? projectBusy : chatBusy;
    setSpinVisible(root.querySelector("[data-run-spin='heading']"), headingBusy, "heading");
    setSpinVisible(root.querySelector("[data-run-spin='chats']"), chatBusy, "chats");
    setSpinVisible(root.querySelector("[data-run-spin='projects']"), projectBusy, "projects");
    setSpinVisible(root.querySelector("[data-run-spin='title']"), currentBusy, "title");
    root.querySelectorAll("[data-history-tab]").forEach((btn) => {
      const tab = btn.getAttribute("data-history-tab");
      const busy = tab === "projects" ? projectBusy : chatBusy;
      btn.classList.toggle("is-busy", busy);
      btn.setAttribute("aria-busy", busy ? "true" : "false");
    });
    const titleWrap = titleEl && titleEl.closest(".chat-bar-title");
    if (titleWrap) titleWrap.setAttribute("aria-busy", currentBusy ? "true" : "false");
    root.querySelectorAll("[data-run-spin]").forEach((el) => {
      if (!el.hidden) fillSpinEl(el, el.getAttribute("data-run-spin") || "run", false);
    });
    const any = chatBusy || projectBusy || currentBusy;
    if (any) {
      if (!runSpinOrigin) runSpinOrigin = Date.now();
      startRunSpinClock();
    } else {
      runSpinOrigin = 0;
      stopRunSpinClock();
    }
  }

  function ageStamp(iso) {
    const ts = Date.parse(iso || "");
    if (!Number.isFinite(ts)) return "";
    const secs = Math.max(0, Math.floor((Date.now() - ts) / 1000));
    if (secs < 60) return "now";
    const mins = Math.floor(secs / 60);
    if (mins < 60) return mins + "M";
    const hours = Math.floor(mins / 60);
    if (hours < 24) return hours + "h";
    const days = Math.floor(hours / 24);
    if (days < 7) return days + "d";
    const weeks = Math.floor(days / 7);
    if (weeks < 5) return weeks + "w";
    const months = Math.floor(days / 30.44);
    if (months < 12) return months + "mo";
    return Math.floor(days / 365.25) + "y";
  }

  function ageChip(item) {
    if (!item || !item.updated_at) return "";
    const stamp = ageStamp(item.updated_at);
    if (!stamp) return "";
    const when = new Date(item.updated_at);
    const title = Number.isFinite(when.getTime()) ? ` title="${escapeHtml(when.toLocaleString())}"` : "";
    return `<span class="chat-history-age" data-age-stamp="${escapeHtml(String(item.updated_at))}"${title}>${escapeHtml(stamp)}</span>`;
  }

  function refreshAgeChips() {
    if (!historyList) return;
    historyList.querySelectorAll("[data-age-stamp]").forEach((el) => {
      const next = ageStamp(el.getAttribute("data-age-stamp"));
      if (next && el.textContent !== next) el.textContent = next;
    });
  }

  function startAgeClock() {
    if (ageTimer) return;
    ageTimer = window.setInterval(refreshAgeChips, AGE_TICK_MS);
  }

  function runningFirst(a, b) {
    const ra = Number(Boolean(a.running));
    const rb = Number(Boolean(b.running));
    if (ra !== rb) return rb - ra;
    return String(b.updated_at).localeCompare(String(a.updated_at));
  }

  function historyIcons() {
    return {
      folder:
        '<svg class="chat-history-glyph" viewBox="0 0 16 16" width="13" height="13" aria-hidden="true"><path d="M2.6 4.7h3.2l1.1 1.4H13.4v6.4H2.6z" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"/></svg>',
      chat:
        '<svg class="chat-history-glyph" viewBox="0 0 16 16" width="13" height="13" aria-hidden="true"><path d="M3.2 3.6h9.6v7.2H6.4L3.2 13.4z" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"/></svg>',
    };
  }

  function projectMeta(name, host) {
    const hostLabel = host
      ? `<span class="chat-history-host-label">${escapeHtml(host)}</span>`
      : "";
    return `<span class="chat-history-meta"><span class="chat-history-project">${escapeHtml(name || "Project")}</span>${hostLabel}</span>`;
  }

  function historyRow(item, grouped = false) {
    const on = item.id === currentId ? " is-on" : "";
    const project = item.kind === "project";
    const running = threadIsRunning(item);
    let sessionTitle = item.title || "New chat";
    if (project && item.project_name && sessionTitle === item.project_name) sessionTitle = "New chat";
    const title = escapeHtml(sessionTitle);
    const snip = item.snippet ? `<span class="chat-history-snip">${escapeHtml(item.snippet)}</span>` : "";
    const chip = searchingChip(item);
    const meta =
      project && !grouped ? projectMeta(item.project_name || "Project", item.host_alias) : "";
    const lead =
      (running ? runSpinMarkup("h-" + item.id) : ageChip(item)) +
      (project ? historyIcons().folder : "");
    const groupedClass = project && grouped ? " is-grouped" : "";
    const runningClass = running ? " is-running" : "";
    const busy = running ? ' aria-busy="true"' : "";
    const working = running ? '<span class="visually-hidden">Working</span>' : "";
    const twoLine = Boolean(item.snippet || meta);
    const scheduledClass = item.schedule_id ? " is-task" : "";
    return `<div class="chat-history-item${on}${twoLine ? " has-snip" : ""}${project ? " is-project" : ""}${groupedClass}${runningClass}${scheduledClass}" data-thread="${escapeHtml(String(item.id))}"${busy}><button type="button" class="open" title="${title}">${working}<span class="chat-history-title">${lead}<span class="chat-history-copy"><span class="chat-history-name">${title}</span>${meta}</span></span>${chip}${snip}</button><button type="button" class="more" aria-label="Chat actions">⋯</button></div>`;
  }

  function projectRow(item) {
    const bound = sessionProject();
    const on =
      bound &&
      Number(bound.project_id) === Number(item.id) &&
      Number(bound.host_id) === Number(item.host_id)
        ? " is-on"
        : "";
    const running = threadIsRunning(item);
    const name = item.name || "Project";
    const hostLabel = item.host_alias
      ? `<span class="chat-history-host-label">${escapeHtml(item.host_alias)}</span>`
      : "";
    const path = item.remote_path
      ? `<span class="chat-history-meta"><span class="chat-history-project">${escapeHtml(item.remote_path)}</span>${hostLabel}</span>`
      : "";
    const chip = historyQuery.trim() ? '<span class="chat-history-chip">Project</span>' : "";
    const threadAttr = item.thread_id ? ` data-thread="${escapeHtml(String(item.thread_id))}"` : "";
    const lead =
      (running ? runSpinMarkup("p-" + item.host_id + "-" + item.id) : ageChip(item)) +
      historyIcons().folder;
    const runningClass = running ? " is-running" : "";
    const busy = running ? ' aria-busy="true"' : "";
    const working = running ? '<span class="visually-hidden">Working</span>' : "";
    const twoLine = Boolean(item.remote_path);
    return `<div class="chat-history-item is-project${on}${twoLine ? " has-snip" : ""}${runningClass}" data-project="${item.host_id}:${item.id}"${threadAttr}${busy}><button type="button" class="open" title="${escapeHtml(name)}">${working}<span class="chat-history-title">${lead}<span class="chat-history-copy"><span class="chat-history-name">${escapeHtml(name)}</span>${path}</span></span>${chip}</button></div>`;
  }

  function searchingChip(item) {
    const searching = Boolean(historyQuery.trim());
    if (!searching) return "";
    if (item.archived) return '<span class="chat-history-chip">Archived</span>';
    if (item.kind === "project") return '<span class="chat-history-chip">Project</span>';
    return "";
  }

  function projectKey(hostId, projectId) {
    return `${hostId}:${projectId}`;
  }

  function projectWorkspaceDetails(info) {
    const details = [];
    if (info.host_alias) details.push("Host: " + info.host_alias);
    if (info.remote_path) details.push("Path: " + info.remote_path);
    if (info.permission_mode) {
      const permission = { read_only: "Read-only", project: "Project-scoped" }[info.permission_mode] || info.permission_mode;
      details.push("Project policy: " + permission);
    }
    return details;
  }

  function fillProjectFilterOptions() {
    const select = root.querySelector("[data-project-filter]");
    if (!select) return;
    const seen = new Map();
    projects.forEach((item) => {
      const key = projectKey(item.host_id, item.id);
      seen.set(key, item.host_alias ? `${item.name || "Project"} · ${item.host_alias}` : (item.name || "Project"));
    });
    threads.forEach((item) => {
      if (item.kind !== "project" || item.project_id == null) return;
      const key = projectKey(item.host_id, item.project_id);
      if (!seen.has(key)) {
        seen.set(key, item.host_alias ? `${item.project_name || "Project"} · ${item.host_alias}` : (item.project_name || "Project"));
      }
    });
    const current = projectFilter;
    const options = [...seen.entries()].sort((a, b) => a[1].localeCompare(b[1]));
    select.innerHTML =
      `<option value="">All projects</option>` +
      options.map(([key, label]) => `<option value="${escapeHtml(key)}">${escapeHtml(label)}</option>`).join("");
    if (current && seen.has(current)) select.value = current;
    else {
      projectFilter = "";
      select.value = "";
    }
    const sortEl = root.querySelector("[data-project-sort]");
    if (sortEl) sortEl.value = projectSort;
  }

  function projectSessionList() {
    let items = threads.filter((item) => item.kind === "project");
    if (projectFilter) {
      items = items.filter((item) => projectKey(item.host_id, item.project_id) === projectFilter);
    }
    if (projectSort === "project") {
      items.sort((a, b) => {
        const ka = `${a.host_alias || ""} / ${a.project_name || ""}`;
        const kb = `${b.host_alias || ""} / ${b.project_name || ""}`;
        const cmp = ka.localeCompare(kb);
        if (cmp) return cmp;
        return runningFirst(a, b);
      });
    } else {
      items.sort(runningFirst);
    }
    return items;
  }

  function projectOverviewHtml(items, opts) {
    if (!items.length) return "";
    const bound = sessionProject();
    const heading = opts && opts.heading === false
      ? ""
      : '<p class="project-overview-heading">Workspaces</p>';
    return '<section class="project-overview" aria-label="Project overview">' + heading +
      items.map((item) => {
        const key = projectKey(item.host_id, item.id);
        const selected = bound && projectKey(bound.host_id, bound.project_id) === key;
        const name = item.name || "Project";
        const details = projectWorkspaceDetails(item).map((detail) =>
          `<span class="project-overview-detail">${escapeHtml(detail)}</span>`
        ).join("");
        return `<div class="project-overview-item${selected ? " project-overview-current" : ""}">` +
          `<button type="button" class="project-overview-open" data-project-overview-open="${escapeHtml(key)}" title="${escapeHtml([name, ...projectWorkspaceDetails(item)].join(" | "))}"${selected ? ' aria-current="true"' : ""}>` +
          `<span class="project-overview-name">${escapeHtml(name)}</span>` +
          `<span class="project-overview-details">${details}</span></button>` +
          `<button type="button" class="project-overview-start" data-project-overview-start="${escapeHtml(key)}" aria-label="${escapeHtml("Start session in " + name + (item.host_alias ? " on " + item.host_alias : ""))}">Start session</button></div>`;
      }).join("") + '</section>';
  }

  function groupedProjectSessions(items) {
    const byKey = new Map();
    items.forEach((item) => {
      const key = projectKey(item.host_id, item.project_id);
      if (!byKey.has(key)) {
        byKey.set(key, {
          name: item.project_name || "Project",
          host: item.host_alias || "",
          items: [],
        });
      }
      byKey.get(key).items.push(item);
    });
    return [...byKey.values()];
  }

  function syncHistoryFooter() {
    const footer = root.querySelector(".chat-history-footer");
    if (!footer) return;
    footer.hidden = Boolean(projectPicker && !projectPicker.hidden);
    syncRunSpinners();
  }

  function renderHistory() {
    if (!historyList) return;
    if (renaming) return;
    const searching = Boolean(historyQuery.trim());
    syncHistoryChrome();
    if (historyKicker) {
      if (searching || showArchived) {
        historyKicker.textContent = searching ? "Results" : "Archived";
        historyKicker.hidden = false;
      } else {
        historyKicker.hidden = true;
      }
    }
    if (archivedBtn) {
      archivedBtn.hidden = searching || historyTab !== "chats" || (archivedThreads.length === 0 && !showArchived);
      archivedBtn.classList.toggle("is-on", showArchived);
      archivedBtn.textContent = showArchived ? "Back to chats" : "Archived (" + archivedThreads.length + ")";
    }
    if (searching) {
      const q = historyQuery.trim().toLowerCase();
      const nativeHits = searchActiveQuery && searchActiveQuery.toLowerCase() === q
        ? searchProjects
        : projects.filter((item) =>
            (item.name || "").toLowerCase().includes(q) ||
            (item.host_alias || "").toLowerCase().includes(q) ||
            (item.remote_path || "").toLowerCase().includes(q)
          );
      const chats = (searchActiveQuery && searchActiveQuery.toLowerCase() === q
        ? [...searchThreads, ...searchArchived]
        : [...filteredHistory(), ...threads.filter((item) => item.kind === "project" && (item.title || "").toLowerCase().includes(q))]
      ).sort(runningFirst);
      const threadProjectKeys = new Set(
        chats.filter((item) => item.kind === "project").map((item) => projectKey(item.host_id, item.project_id))
      );
      const unusedHits = nativeHits.filter((item) => !threadProjectKeys.has(projectKey(item.host_id, item.id)));
      let html = "";
      if (chats.length) html += chats.map((item) => historyRow(item)).join("");
      if (unusedHits.length) {
        if (chats.length) html += `<p class="chat-history-kicker">Projects</p>`;
        html += unusedHits.map(projectRow).join("");
      }
      historyList.innerHTML = html || `<p class="chat-history-empty">No chats or projects match your search.</p>`;
      syncHistoryFooter();
      return;
    }
    if (showArchived) {
      const items = archivedThreads;
      historyList.innerHTML = items.length
        ? items.map((item) => historyRow(item)).join("")
        : `<p class="chat-history-empty">No archived chats.</p>`;
      syncHistoryFooter();
      return;
    }
    if (historyTab === "projects") {
      const sessions = projectSessionList();
      const used = new Set(sessions.map((item) => projectKey(item.host_id, item.project_id)));
      const unused = projects.filter((item) => {
        if (projectFilter && projectKey(item.host_id, item.id) !== projectFilter) return false;
        return !used.has(projectKey(item.host_id, item.id));
      });
      if (!sessions.length && !unused.length) {
        historyList.innerHTML =
          `<p class="chat-history-empty">No project sessions yet. Use New project to start one.</p>`;
        syncHistoryFooter();
        return;
      }
      let html = "";
      if (projectSort === "project" && sessions.length) {
        groupedProjectSessions(sessions).forEach((group) => {
          html +=
            `<div class="chat-history-project-head">` +
            `<span class="chat-history-project">${escapeHtml(group.name)}</span>` +
            (group.host ? `<span class="chat-history-host-label">${escapeHtml(group.host)}</span>` : "") +
            `</div>`;
          html += group.items.map((item) => historyRow(item, true)).join("");
        });
      } else {
        html += sessions.map((item) => historyRow(item)).join("");
      }
      if (unused.length) {
        if (sessions.length) html += `<p class="chat-history-kicker">Projects</p>`;
        html += unused.map(projectRow).join("");
      }
      historyList.innerHTML = html;
      syncHistoryFooter();
      return;
    }
    const items = filteredHistory();
    if (!items.length) {
      historyList.innerHTML = `<p class="chat-history-empty">No chats yet.</p>`;
    } else {
      historyList.innerHTML = items.map((item) => historyRow(item)).join("");
    }
    syncHistoryFooter();
  }

  function resetSearchLists() {
    searchActiveQuery = "";
    searchThreads = [];
    searchArchived = [];
    searchProjects = [];
  }

  function setSearchOpen(open) {
    const top = root.querySelector(".chat-history-top");
    const row = root.querySelector("[data-history-row]");
    if (!top || !row || !historySearch || !searchToggle) return;
    searchOpen = open;
    top.classList.toggle("is-searching", open);
    searchToggle.setAttribute("aria-expanded", String(open));
    if (open) {
      setProjectPickerOpen(false);
      row.hidden = false;
      historySearch.focus();
      historySearch.select();
    } else {
      row.hidden = true;
      if (historyQuery) {
        historyQuery = "";
        historySearch.value = "";
        resetSearchLists();
        renderHistory();
      }
      searchToggle.focus({ preventScroll: true });
    }
  }

  function queueSearch() {
    window.clearTimeout(searchTimer);
    const gen = ++searchGen;
    const q = historyQuery.trim();
    if (!q) {
      resetSearchLists();
      renderHistory();
      return;
    }
    searchTimer = window.setTimeout(async () => {
      try {
        const data = await api("/chat/threads?q=" + encodeURIComponent(q));
        if (gen !== searchGen) return;
        searchActiveQuery = q;
        searchThreads = data.threads || [];
        searchArchived = data.archived || [];
        searchProjects = data.projects || [];
        renderHistory();
      } catch (error) {
        if (gen === searchGen) showFailure("Chat search failed", error);
      }
    }, 250);
  }

  async function refreshThreads() {
    const before = cursor;
    const data = await api("/chat/threads");
    if (cursor !== before) return;
    threads = data.threads || [];
    archivedThreads = data.archived || [];
    if (Array.isArray(data.projects)) projects = data.projects;
    syncTitle();
    renderHistory();
  }

  async function openProject(hostId, projectId) {
    if (sending) return;
    if (!requireLive()) return;
    const hid = Number(hostId);
    const pid = Number(projectId);
    const existing =
      threads.find((item) => item.kind === "project" && Number(item.host_id) === hid && Number(item.project_id) === pid) ||
      archivedThreads.find((item) => item.kind === "project" && Number(item.host_id) === hid && Number(item.project_id) === pid);
    if (existing) {
      await openThread(existing.id);
      return;
    }
    await startProjectThread(hostId, projectId);
  }

  function setProjectPickerOpen(open) {
    if (!projectPicker) return;
    if (!open) {
      projectPicker.hidden = true;
      if (historyList) historyList.hidden = false;
      pickerMode = "list";
      pickerBrowse = null;
      if (projectPickerForm) projectPickerForm.hidden = true;
      if (projectPickerList) projectPickerList.hidden = false;
      if (projectPickerBack) projectPickerBack.hidden = true;
      if (projectPickerBrowser) {
        projectPickerBrowser.hidden = true;
        projectPickerBrowser.innerHTML = "";
      }
      if (projectPickerError) {
        projectPickerError.hidden = true;
        projectPickerError.textContent = "";
      }
      if (projectPickerTitle) projectPickerTitle.textContent = "Workspaces";
      syncHistoryFooter();
      syncComposer();
      return;
    }
    if (historyList) historyList.hidden = true;
    const footer = root.querySelector(".chat-history-footer");
    if (footer) footer.hidden = true;
    if (historyKicker) historyKicker.hidden = true;
    projectPicker.hidden = false;
    syncHistoryFooter();
    syncComposer();
  }

  function pickerHostById(id) {
    return pickerHosts.find((host) => String(host.id) === String(id));
  }

  function pickerWorkspaceItems() {
    const items = [];
    pickerHosts.forEach((host) => {
      (Array.isArray(host.projects) ? host.projects : []).forEach((item) => {
        items.push({
          host_id: host.id,
          id: item.id,
          host_alias: host.alias || "",
          name: item.name || "Project",
          remote_path: item.remote_path || "",
          permission_mode: item.permission_mode || "",
        });
      });
    });
    return items;
  }

  function renderProjectPickerList() {
    if (!projectPickerList) return;
    if (!pickerHosts.length) {
      projectPickerList.innerHTML =
        `<p class="chat-history-empty">No machines yet. Add one from Machines in the account menu.</p>` +
        `<a class="chat-history-empty-link" href="/projects">Add a machine</a>`;
      return;
    }
    const workspaces = pickerWorkspaceItems();
    projectPickerList.innerHTML =
      (workspaces.length
        ? projectOverviewHtml(workspaces, { heading: false })
        : `<p class="chat-history-empty">No projects yet. Create one on a host.</p>`) +
      `<button type="button" class="chat-project-pick is-add" data-project-picker-add>New project on a host</button>`;
  }

  function fillPickerHosts() {
    if (!projectPickerHost) return;
    projectPickerHost.innerHTML = pickerHosts
      .map((host) => `<option value="${escapeHtml(String(host.id))}">${escapeHtml(host.alias || "host")}</option>`)
      .join("");
    syncPickerHostDefaults();
  }

  function syncPickerHostDefaults() {
    const host = pickerHostById(projectPickerHost && projectPickerHost.value);
    if (!host) return;
    if (projectPickerPath && !projectPickerPath.value) {
      projectPickerPath.value = host.default_cwd || "";
    }
  }

  function showPickerForm() {
    pickerMode = "form";
    pickerBrowse = null;
    if (projectPickerList) projectPickerList.hidden = true;
    if (projectPickerForm) projectPickerForm.hidden = false;
    if (projectPickerBack) projectPickerBack.hidden = false;
    if (projectPickerTitle) projectPickerTitle.textContent = "New on a host";
    if (projectPickerName) projectPickerName.value = "";
    if (projectPickerPath) projectPickerPath.value = "";
    if (projectPickerBrowser) {
      projectPickerBrowser.hidden = true;
      projectPickerBrowser.innerHTML = "";
    }
    if (projectPickerError) {
      projectPickerError.hidden = true;
      projectPickerError.textContent = "";
    }
    fillPickerHosts();
    if (projectPickerName) projectPickerName.focus();
  }

  function showPickerList() {
    pickerMode = "list";
    pickerBrowse = null;
    if (projectPickerForm) projectPickerForm.hidden = true;
    if (projectPickerList) projectPickerList.hidden = false;
    if (projectPickerBack) projectPickerBack.hidden = true;
    if (projectPickerBrowser) {
      projectPickerBrowser.hidden = true;
      projectPickerBrowser.innerHTML = "";
    }
    if (projectPickerTitle) projectPickerTitle.textContent = "Workspaces";
    if (projectPickerError) {
      projectPickerError.hidden = true;
      projectPickerError.textContent = "";
    }
  }

  function renderPickerBrowse() {
    if (!projectPickerBrowser || !pickerBrowse) return;
    const path = pickerBrowse.path || "/";
    const dirs = (pickerBrowse.entries || []).filter((item) => item && (item.dir || item.kind === "dir"));
    const rows = dirs
      .map(
        (item) =>
          `<li><button type="button" class="hosts-browse-item" data-picker-into="${escapeHtml(item.name)}"><span class="hosts-browse-kind">dir</span><span>${escapeHtml(item.name)}</span></button></li>`
      )
      .join("");
    projectPickerBrowser.hidden = false;
    projectPickerBrowser.innerHTML =
      `<div class="hosts-browse-bar"><span class="hosts-browse-path">${escapeHtml(path)}</span>` +
      `<button type="button" class="btn sm ghost" data-picker-up>Up</button>` +
      `<button type="button" class="btn sm" data-picker-use>Use this folder</button></div>` +
      `<ul class="hosts-browse-list">${rows || "<li class='usage-sub'>Empty folder</li>"}</ul>`;
  }

  async function loadPickerBrowse(path) {
    const hostId = projectPickerHost && projectPickerHost.value;
    if (!hostId) return;
    const data = await api(`/hosts/${hostId}/browse`, {
      method: "POST",
      body: JSON.stringify({ path: path || "" }),
    });
    pickerBrowse = {
      path: data.path || path || "/",
      parent: data.parent || "",
      entries: Array.isArray(data.entries) ? data.entries : [],
    };
    if (projectPickerPath) projectPickerPath.value = pickerBrowse.path;
    const base = String(pickerBrowse.path).replace(/\/+$/, "").split("/").pop();
    if (projectPickerName && !projectPickerName.value && base && base !== "/") projectPickerName.value = base;
    renderPickerBrowse();
  }

  async function openProjectPicker() {
    if (sending) return;
    if (projectPicker && !projectPicker.hidden) {
      setProjectPickerOpen(false);
      return;
    }
    try {
      const data = await api("/hosts/api");
      pickerHosts = Array.isArray(data.hosts) ? data.hosts : [];
      showPickerList();
      renderProjectPickerList();
      setProjectPickerOpen(true);
    } catch (error) {
      showFailure("Could not load projects", error);
    }
  }

  function resolveProjectInfo(hostId, projectId, extra) {
    const hid = Number(hostId);
    const pid = Number(projectId);
    if (!Number.isFinite(hid) || !Number.isFinite(pid)) return null;
    const native = projects.find((item) => Number(item.host_id) === hid && Number(item.id) === pid);
    if (native) {
      return {
        host_id: hid,
        project_id: pid,
        host_alias: native.host_alias || "",
        project_name: native.name || "Project",
        remote_path: native.remote_path || "",
        permission_mode: native.permission_mode || "",
        default_model: native.default_model || "",
      };
    }
    const host = pickerHostById(hostId);
    const proj = host && (host.projects || []).find((item) => Number(item.id) === pid);
    if (host && proj) {
      return {
        host_id: hid,
        project_id: pid,
        host_alias: host.alias || "",
        project_name: proj.name || "Project",
        remote_path: proj.remote_path || "",
        permission_mode: proj.permission_mode || "",
        default_model: proj.default_model || "",
      };
    }
    if (extra && (extra.id == null || Number(extra.id) === pid)) {
      return {
        host_id: hid,
        project_id: pid,
        host_alias: (host && host.alias) || extra.host_alias || "",
        project_name: extra.name || extra.project_name || "Project",
        remote_path: extra.remote_path || "",
        permission_mode: extra.permission_mode || "",
        default_model: extra.default_model || "",
      };
    }
    return null;
  }

  function bindCreatePayload(payload) {
    if (!pendingProject) return payload;
    payload.kind = "project";
    payload.host_id = pendingProject.host_id;
    payload.project_id = pendingProject.project_id;
    return payload;
  }

  async function startProjectThread(hostId, projectId, extra) {
    if (sending) return;
    const info = resolveProjectInfo(hostId, projectId, extra);
    if (!info) {
      showFailure("Could not start a project session", new Error("project not found"));
      return;
    }
    const nav = navigation;
    try {
      await flushPending();
      if (nav !== navigation) return;
      setProjectPickerOpen(false);
      setHistoryTab("projects", { persist: true, silent: true });
      clearChatState();
      pendingProject = info;
      const listed = resolveListedModel(info.default_model);
      if (listed) {
        modelId = listed;
        renderPickerButton();
      }
      syncTitle();
      renderHistory();
    } catch (error) {
      showFailure("Could not start a project session", error);
    }
  }

  async function createPickerProject(event) {
    event.preventDefault();
    if (!projectPickerHost || !projectPickerName || !projectPickerPath) return;
    const hostId = projectPickerHost.value;
    const name = projectPickerName.value.trim();
    const path = projectPickerPath.value.trim();
    if (!hostId || !name || !path) return;
    if (projectPickerError) {
      projectPickerError.hidden = true;
      projectPickerError.textContent = "";
    }
    const submitBtn = projectPickerForm.querySelector('button[type="submit"]');
    if (submitBtn) submitBtn.disabled = true;
    try {
      const created = await api(`/hosts/${hostId}/projects`, {
        method: "POST",
        body: JSON.stringify({ name, remote_path: path }),
      });
      await refreshThreads().catch(() => {});
      await startProjectThread(hostId, created.id, created);
    } catch (error) {
      if (projectPickerError) {
        projectPickerError.hidden = false;
        projectPickerError.textContent = error.message || "Could not create the project.";
      } else {
        showFailure("Could not create the project", error);
      }
    } finally {
      if (submitBtn) submitBtn.disabled = false;
    }
  }

  async function openThread(id) {
    if (sending) return;
    const nav = ++navigation;
    loading = true;
    syncComposer();
    try {
    drafts.set(currentId, input.value);
    await flushPending();
    if (nav !== navigation) return;
    const expectedGeneration = ++generation;
    const data = await api("/chat/threads/" + id);
    if (expectedGeneration !== generation || nav !== navigation) return;
    if (!Number.isSafeInteger(data.event_cursor)) throw new Error("Thread snapshot is missing event_cursor.");
    drafts.set(currentId, input.value);
    currentId = data.id;
    sessionId = "thread-" + data.id;
    const listed = resolveListedModel(data.model);
    if (listed) {
      modelId = listed;
      renderPickerButton();
    }
    contextMessages = Array.isArray(data.context_messages) ? data.context_messages.map((msg) => ({ ...msg, noActions: true })) : [];
    contextLive = Boolean(data.context_live);
    messages = (data.messages || []).map((msg) => ({
      id: msg.id,
      role: msg.role,
      content: msg.content || "",
      reasoning: msg.reasoning || "",
      error: msg.error || "",
      usage: msg.usage || null,
      model: msg.model || "",
      tools: Array.isArray(msg.tools) ? msg.tools : [],
      tools_live: Boolean(msg.tools_live),
      segments: Array.isArray(msg.segments) ? msg.segments : undefined,
      notice: msg.notice || "",
      elapsedMs: msg.elapsedMs ?? null,
      stopped: Boolean(msg.stopped),
      attachments: Array.isArray(msg.attachments) ? msg.attachments : [],
      auto: Boolean(msg.auto),
    }));
    serverRevision = data.revision;
    viewCursor = Number(data.event_cursor || 0);
    threadWatermark = Number.isSafeInteger(data.thread_cursor) ? Number(data.thread_cursor) : viewCursor;
    activeRun = data.run;
    // Restore any live permission asks (parent or subagent) the event stream
    // cannot replay after a reload — child asks never touch the transcript.
    seedPendingFromServer(currentId);
    // Never carry the previous thread's meter into this one.
    liveMeter = null;
    compacting = false;
    contextWarned = false;
    contextAlertHidden = false;
    if (activeRun && stoppingRunId && activeRun.id === stoppingRunId) activeRun.cancel_requested = true;
    if (!runIsActive(activeRun) && (!activeRun || !stoppingRunId || activeRun.id === stoppingRunId)) stoppingRunId = "";
    setRunning(false);
    healStuckRun(activeRun);
    if (data.compaction && data.compaction.meter) acceptMeter(data.compaction.meter);
    else syncContextMeter();
    acceptedQueueCommands = new Set(data.accepted_queue_ids || []);
    loadQueue();
    if (!streaming) drainQueue();
    dirty = false;
    pendingCreate = null;
    pendingProject = null;
    pendingSave = null;
    input.value = drafts.get(currentId) || "";
    resizeInput();
    threadContext = data.context || "";
    searchOn = Array.isArray(data.tools) ? data.tools.includes("web_search") : true;
    syncMemoryUi();
    if (data.kind === "project") refreshProjectAgents();
    else renderProjectAgents(null);
    editingIndex = -1;
    loadHistoryStacks();
    setThreadUrl(currentId);
    if (data.kind === "project") setHistoryTab("projects", { persist: true, silent: true });
    else setHistoryTab("chats", { persist: true, silent: true });
    stickToBottom = true;
    if (searchOpen) {
      const top = root.querySelector(".chat-history-top");
      const row = root.querySelector("[data-history-row]");
      searchOpen = false;
      if (top) top.classList.remove("is-searching");
      if (row) row.hidden = true;
      if (searchToggle) searchToggle.setAttribute("aria-expanded", "false");
    }
    if (historyQuery) {
      historyQuery = "";
      if (historySearch) historySearch.value = "";
      resetSearchLists();
    }
    const openedEmpty = root.classList.contains("is-empty");
    paint();
    if (!openedEmpty && messages.length > 0) playTranscriptFx();
    renderHistory();
    setHistoryOpen(false);
    rememberLatestArtifact();
    resetAgents();
    if (window.matchMedia("(hover: hover) and (pointer: fine)").matches) input.focus();
    defaultHint();
    syncStatus();
    if (window.FuseComposerSpell) {
      FuseComposerSpell.rebuildLearned(
        messages.filter((m) => m.role === "user" && !m.auto).map((m) => m.content)
      );
      FuseComposerSpell.refresh();
    }
    refreshProjectListing();
    // Covers events consumed while the navigation snapshot was in flight.
    reconcileThread().catch(syncFailure);
    } finally {
      if (nav === navigation) {
        loading = false;
        syncComposer();
        if (!streaming) drainQueue();
      }
    }
  }

  function startRename(row, thread) {
    renaming = true;
    const inputEl = document.createElement("input");
    inputEl.className = "rename";
    inputEl.value = thread.title || "";
    inputEl.maxLength = 80;
    row.innerHTML = "";
    row.appendChild(inputEl);
    inputEl.focus();
    inputEl.select();
    let finishing = false;
    const finish = async (save) => {
      if (finishing) return;
      finishing = true;
      const next = inputEl.value.trim();
      if (save && next && next !== thread.title) {
        try {
          if (!requireLive()) {
            finishing = false;
            return;
          }
          const latest = threadById(thread.id) || thread;
          renameSaving = api("/chat/threads/" + thread.id, {
            method: "PATCH",
            body: JSON.stringify({ title: next, expected_revision: latest.revision, command_id: newSession() }),
          });
          const updated = await renameSaving;
          if (thread.id === currentId) serverRevision = Math.max(serverRevision, updated.revision);
          upsertLocal(updated);
        } catch (error) {
          finishing = false;
          showFailure("Rename failed; the title is still editable", error);
          inputEl.focus();
          inputEl.select();
          return;
        } finally { renameSaving = null; }
      }
      renaming = false;
      renderHistory();
      reconcileThread().catch(syncFailure);
    };
    inputEl.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        finish(true);
      } else if (event.key === "Escape") {
        event.preventDefault();
        finish(false);
      }
    });
    inputEl.addEventListener("blur", () => finish(true));
  }

  function threadById(id) {
    return threads.concat(archivedThreads).find((item) => item.id === id);
  }

  function threadMenu(anchor, id) {
    if (streaming) return;
    const thread = threadById(id);
    if (!thread) return;
    if (!requireLive()) return;
    anchor.closest(".chat-history-item")?.classList.add("is-menu");
    const items = [
      { label: "Rename", run: () => startRename(anchor.closest(".chat-history-item"), thread) },
      {
        label: "Duplicate",
        run: async () => {
          if (!requireLive()) return;
          const nav = navigation;
          await flushPending();
          if (nav !== navigation) return;
          const copy = await api("/chat/threads/" + id + "/duplicate", { method: "POST", body: JSON.stringify({ expected_revision: threadById(id)?.revision, command_id: newSession() }) });
          upsertLocal(copy);
          renderHistory();
          if (nav === navigation) await openThread(copy.id);
        },
      },
      {
        label: thread.archived ? "Unarchive" : "Archive",
        run: async () => {
          if (!requireLive()) return;
          const nav = navigation;
          await flushPending();
          if (nav !== navigation) return;
          const latest = threadById(id) || thread;
          const updated = await api("/chat/threads/" + id, {
            method: "PATCH",
            body: JSON.stringify({
              archived: !latest.archived,
              expected_revision: latest.revision,
              command_id: newSession(),
            }),
          });
          upsertLocal(updated);
          if (nav === navigation && currentId === id && updated.archived) clearChatState();
          renderHistory();
        },
      },
      {
        label: "Delete",
        danger: true,
        run: async () => {
          if (!requireLive()) return;
          if (!(await askConfirm("Delete this chat? This cannot be undone.", { confirmLabel: "Delete", danger: true }))) return;
          const nav = navigation;
          const latest = threadById(id) || thread;
          pendingDeletes.add(id);
          try {
            await api("/chat/threads/" + id, {
              method: "DELETE",
              body: JSON.stringify({ expected_revision: latest.revision, command_id: newSession() }),
            });
          } catch (error) {
            if (error.status === 404) {
              threadGone(id);
              return;
            }
            if (error.status === 409) {
              const fresh = await api("/chat/threads/" + id).catch(() => null);
              if (!fresh) {
                threadGone(id);
                return;
              }
              await api("/chat/threads/" + id, {
                method: "DELETE",
                body: JSON.stringify({ expected_revision: fresh.revision, command_id: newSession() }),
              });
            } else {
              pendingDeletes.delete(id);
              throw error;
            }
          }
          if (nav !== navigation) return;
          threadGone(id);
        },
      },
    ];
    showPop(anchor, items);
  }

  async function copyText(text) {
    try {
      await navigator.clipboard.writeText(text || "");
      setHint("Copied to clipboard.");
    } catch (error) {
      showFailure("Clipboard copy failed", error);
      throw error;
    }
  }

  function resetHistoryStacks() {
    undoStack = [];
    redoStack = [];
    closeSlash();
    closeRewind();
  }

  function historyKey(id) {
    return STORE_UNDO + String(id || "");
  }

  function cloneTranscript(list) {
    try {
      return JSON.parse(JSON.stringify(list));
    } catch (_) {
      return Array.isArray(list) ? list.map((msg) => ({ ...msg })) : [];
    }
  }

  const historyWrites = new Map();
  function saveHistoryStacks() {
    if (!currentId) return;
    const key = historyKey(currentId);
    clearTimeout(historyWrites.get(key));
    const undo = undoStack.slice(-12), redo = redoStack.slice(-12);
    // Persistence is best effort; never serialize twelve multi-megabyte
    // transcripts synchronously in a Send handler. Keep in-memory undo intact.
    historyWrites.set(key, setTimeout(() => {
      historyWrites.delete(key);
      try {
        let remaining = 1000000;
        const bounded = stack => {
          const rows = [];
          for (let i = stack.length - 1; i >= 0; i--) {
            const row = JSON.stringify(stack[i]);
            if (row.length > remaining) break;
            remaining -= row.length;
            rows.unshift(row);
          }
          return "[" + rows.join(",") + "]";
        };
        sessionStorage.setItem(key, '{"u":' + bounded(undo) + ',"r":' + bounded(redo) + '}');
      } catch (_) {}
    }, 250));
  }

  function dropHistoryStacks(id) {
    if (!id) return;
    clearTimeout(historyWrites.get(historyKey(id)));
    historyWrites.delete(historyKey(id));
    try { sessionStorage.removeItem(historyKey(id)); } catch (_) {}
  }

  function loadHistoryStacks() {
    undoStack = [];
    redoStack = [];
    closeSlash();
    closeRewind();
    if (!currentId) return;
    try {
      const raw = sessionStorage.getItem(historyKey(currentId));
      if (!raw) return;
      const data = JSON.parse(raw);
      if (Array.isArray(data.u)) undoStack = data.u.filter(Array.isArray).map(cloneTranscript).slice(-12);
      if (Array.isArray(data.r)) redoStack = data.r.filter(Array.isArray).map(cloneTranscript).slice(-12);
    } catch (_) {
      undoStack = [];
      redoStack = [];
    }
  }

  function snapshotMessages() {
    return cloneTranscript(serialMessages());
  }

  function rememberUndo(snap) {
    if (!snap) return;
    undoStack.push(snap);
    if (undoStack.length > 40) undoStack.shift();
    redoStack = [];
    saveHistoryStacks();
  }

  function applyTranscript(next, hint) {
    messages = Array.isArray(next)
      ? next.map((msg) => ({
          ...msg,
          attachments: Array.isArray(msg.attachments) ? msg.attachments.slice() : [],
          tools: Array.isArray(msg.tools) ? msg.tools.slice() : [],
          segments: Array.isArray(msg.segments) ? msg.segments.map((part) => ({ ...part })) : undefined,
        }))
      : [];
    editingIndex = -1;
    persist(true);
    paint();
    recountLocalContext();
    rememberLatestArtifact();
    if (hint) setHint(hint);
  }

  function dropLastTurn(list) {
    const next = list.slice();
    if (!next.length) return next;
    if (next[next.length - 1].role === "assistant") next.pop();
    if (next.length && next[next.length - 1].role === "user") next.pop();
    else if (next.length === list.length) next.pop();
    return next;
  }

  function undoConversation() {
    if (runIsActive(activeRun) || sending) return;
    if (!requireLive()) return;
    if (!messages.length) {
      setHint("Nothing to undo.");
      return;
    }
    const current = snapshotMessages();
    let previous;
    if (undoStack.length) previous = undoStack.pop();
    else {
      previous = dropLastTurn(current);
      if (previous.length === current.length) {
        setHint("Nothing to undo.");
        return;
      }
    }
    redoStack.push(current);
    saveHistoryStacks();
    applyTranscript(previous, previous.length ? "Undid last turn." : "Conversation cleared.");
  }

  function redoConversation() {
    if (runIsActive(activeRun) || sending) return;
    if (!requireLive()) return;
    if (!redoStack.length) {
      setHint("Nothing to redo.");
      return;
    }
    undoStack.push(snapshotMessages());
    saveHistoryStacks();
    applyTranscript(redoStack.pop(), "Redid.");
  }

  function mutateTranscript(fn, hint) {
    if (runIsActive(activeRun) || sending) {
      setHint("Wait for the current reply to finish.", true);
      return false;
    }
    if (!requireLive()) return false;
    rememberUndo(snapshotMessages());
    fn();
    editingIndex = -1;
    persist(true);
    paint();
    recountLocalContext();
    rememberLatestArtifact();
    if (hint) setHint(hint);
    return true;
  }

  function askConfirm(message, opts) {
    if (typeof window.fuseConfirm === "function") return window.fuseConfirm(message, opts);
    return Promise.resolve(window.confirm(message));
  }

  function deleteAt(index, mode) {
    if (runIsActive(activeRun) || sending) {
      setHint("Wait for the current reply to finish.", true);
      return;
    }
    if (index < 0 || index >= messages.length) return;
    mutateTranscript(() => {
      if (mode === "one") messages.splice(index, 1);
      else if (mode === "from") messages = messages.slice(0, index);
      else if (mode === "after") messages = messages.slice(0, index + 1);
    }, mode === "one" ? "Message deleted. /undo restores it." : "Removed. /undo restores it.");
  }

  async function confirmDeleteAt(index, mode) {
    const prompts = {
      one: "Delete this message? /undo restores it.",
      from: "Delete this message and everything after it? /undo restores it.",
      after: "Keep messages up to here and drop everything after? /undo restores it.",
    };
    if (!(await askConfirm(prompts[mode] || prompts.one, { confirmLabel: "Delete", danger: true }))) return;
    deleteAt(index, mode);
  }

  function rewindTo(index) {
    if (runIsActive(activeRun) || sending) return;
    if (!requireLive()) return;
    const msg = messages[index];
    if (!msg || msg.role !== "user") return;
    const text = msg.content || "";
    const kept = Array.isArray(msg.attachments) ? msg.attachments.slice() : [];
    closeRewind();
    if (!mutateTranscript(() => {
      messages = messages.slice(0, index);
    }, "Rewound. Edit and send, or keep going.")) return;
    const fresh = pendingAttachments.filter(
      (att) => !kept.some((other) => String(other.id || "") && String(other.id) === String(att.id))
    );
    const merged = kept.concat(fresh);
    pendingAttachments = merged.slice(0, ATTACH_MAX);
    if (merged.length > ATTACH_MAX) {
      setHint("Up to " + ATTACH_MAX + " attachments stay in the composer; some were left out.", true, 4000);
    }
    input.value = text;
    renderAttachList();
    resizeInput();
    syncComposer();
    input.focus();
  }

  function beginEdit(index) {
    if (runIsActive(activeRun) || !messages[index] || messages[index].role !== "user") return;
    editingIndex = index;
    const atts = messages[index].attachments;
    editingFiles = Array.isArray(atts) ? atts.slice() : [];
    paint();
    const box = log.querySelector("[data-edit]");
    if (box) {
      box.focus();
      box.setSelectionRange(box.value.length, box.value.length);
    }
  }

  function commitEdit(index, text) {
    if (runIsActive(activeRun) || sending) return;
    if (!requireLive()) return;
    const next = (text || "").trim();
    const kept = editingFiles.slice();
    editingFiles = [];
    if (!next && !kept.length) {
      editingIndex = -1;
      paint();
      return;
    }
    const snap = snapshotMessages();
    messages = messages.slice(0, index);
    editingIndex = -1;
    persist(true);
    recountLocalContext();
    completeTurn(next, kept, { undoSnap: snap });
  }

  const SLASH_MAX = 24;
  const SLASH_COMMANDS = [
    { name: "undo", hint: "Undo the last turn", needs: "messages" },
    { name: "retry", hint: "Resend the last message", needs: "messages" },
    { name: "redo", hint: "Redo an undone turn", needs: "redo" },
    { name: "rewind", hint: "Jump back to an earlier message", needs: "messages" },
    { name: "title", hint: "Regenerate the chat title from this conversation", needs: "messages" },
    { name: "permissions", hint: "Manage agent permissions and bypass" },
    { name: "init", hint: "Create or update AGENTS.md", needs: "project", args: true },
    { name: "compact", hint: "Compress conversation context", needs: "messages", args: true },
    { name: "help", hint: "List slash commands" },
  ];

  function skillsOn() {
    return Boolean(config.coding) || Boolean(sessionProject());
  }

  function skillCatalog() {
    return Array.isArray(config.skills) ? config.skills : [];
  }

  function skillInvokeFromText(text) {
    if (!skillsOn()) return null;
    const match = String(text || "").trim().match(/^\/([a-z0-9]+(?:-[a-z0-9]+)*)(?:\s+([\s\S]*))?$/i);
    if (!match) return null;
    const name = match[1].toLowerCase();
    if (SLASH_COMMANDS.some((cmd) => cmd.name === name)) return null;
    const skill = skillCatalog().find((item) => item.slug === name && item.user_invocable !== false);
    if (!skill) return null;
    return { name, arguments: (match[2] || "").trim() };
  }

  async function refreshSkills() {
    try {
      const data = await api("/skills/index");
      if (data && Array.isArray(data.skills)) config.skills = data.skills;
    } catch (_) {}
  }
  refreshSkills();
  window.addEventListener("focus", refreshSkills);
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") refreshSkills();
  });

  function slashQuery() {
    const text = input.value;
    if (!text.startsWith("/") || text.includes("\n")) return null;
    const match = text.match(/^\/([a-z0-9]*(?:-[a-z0-9]*)*)?(?:(\s+)([\s\S]*))?$/i);
    if (!match) return null;
    return {
      name: (match[1] || "").toLowerCase(),
      spaced: Boolean(match[2]),
      arg: (match[3] || "").trim(),
      raw: text,
    };
  }

  function slashAvailable(cmd) {
    if (!cmd) return false;
    if (cmd.needs === "project") return Boolean(sessionProject());
    if (cmd.needs === "messages") return messages.length > 0;
    if (cmd.needs === "redo") return redoStack.length > 0;
    return true;
  }

  function filteredSlash() {
    const query = slashQuery();
    if (!query || query.spaced) return [];
    const q = query.name;
    const builtins = SLASH_COMMANDS.filter((cmd) => !q || cmd.name.startsWith(q));
    const skills = skillsOn()
      ? skillCatalog()
          .filter((item) => item.user_invocable !== false && (!q || String(item.slug || "").startsWith(q)))
          .map((item) => ({
            name: item.slug,
            hint: item.description || "",
            args: true,
            skill: true,
          }))
      : [];
    return builtins.concat(skills).slice(0, SLASH_MAX);
  }

  function restoreEnterKeyHint() {
    if (input) input.setAttribute("enterkeyhint", "send");
  }

  function setEnterKeyHintEnter() {
    if (input) input.setAttribute("enterkeyhint", "enter");
  }

  function syncComposerAria() {
    if (!input) return;
    const spellOpen = window.FuseComposerSpell && FuseComposerSpell.isOpen();
    const palOpen = slashMenu && !slashMenu.hidden;
    if (spellOpen) {
      input.setAttribute("aria-expanded", "true");
      if (spellPop && spellPop.id) input.setAttribute("aria-controls", spellPop.id);
      else input.setAttribute("aria-controls", "chat-spell-pop");
    } else if (palOpen) {
      input.setAttribute("aria-autocomplete", "list");
      input.setAttribute("aria-expanded", "true");
      input.setAttribute("aria-controls", "chat-slash-list");
    } else {
      input.removeAttribute("aria-expanded");
      input.removeAttribute("aria-controls");
      input.removeAttribute("aria-autocomplete");
    }
  }

  function composerPrefs() {
    if (window.FuseChatAppearance && typeof FuseChatAppearance.getComposerPrefs === "function") {
      return FuseChatAppearance.getComposerPrefs();
    }
    return { spell: true, palettes: true, history: true };
  }

  function getComposerContext() {
    const userMsgs = messages.filter((m) => m.role === "user" && !m.auto);
    return {
      skills: config.skills,
      slash: SLASH_COMMANDS,
      models: allModels,
      threads,
      projects,
      project: sessionProject(),
      traces: messages.flatMap((m) => m.tools || []),
      attachments: pendingAttachments.concat(userMsgs.flatMap((m) => m.attachments || [])),
      prompts: userMsgs.map((m) => m.content),
      listing: projectListingCache,
    };
  }

  function closeSlash() {
    slashKind = "slash";
    paletteTrigger = null;
    paletteItems = [];
    if (slashMenu) {
      slashMenu.hidden = true;
      slashMenu.removeAttribute("data-kind");
    }
    if (slashList) slashList.innerHTML = "";
    restoreEnterKeyHint();
    syncComposerAria();
  }

  function extraPaletteOpen() {
    return slashMenu && !slashMenu.hidden && slashKind !== "slash";
  }

  async function refreshProjectListing() {
    projectListingCache = null;
    const bound = sessionProject();
    if (!bound || config.vault === "locked" || !bound.host_id) return;
    try {
      const data = await api(`/hosts/${bound.host_id}/browse`, {
        method: "POST",
        body: JSON.stringify({ path: bound.remote_path || "" }),
      });
      projectListingCache = {
        path: data.path || bound.remote_path,
        entries: (data.entries || []).slice(0, 200),
      };
    } catch (_) {}
  }

  function paletteKicker(kind) {
    if (kind === "at") return ["Mentions", "Tab to insert"];
    if (kind === "path") return ["Paths", "Tab to insert"];
    if (kind === "history") return ["Earlier prompts", "Enter to insert"];
    return ["Commands", "Tab to complete · Enter to select"];
  }

  function acceptPaletteItem(item) {
    if (!item || item.disabled) return;
    const start = paletteTrigger ? paletteTrigger.start : 0;
    const end = paletteTrigger ? paletteTrigger.end : (slashKind === "history" ? 0 : 0);
    input.setRangeText(item.insert, start, end, "end");
    closeSlash();
    drafts.set(currentId, input.value);
    resizeInput();
    syncComposer();
    syncEmptyComposer();
    input.focus();
    if (window.FuseComposerSpell) FuseComposerSpell.refresh();
  }

  function acceptCurrentPalette() {
    if (slashKind === "slash") {
      const items = filteredSlash();
      if (items[slashIndex]) acceptSlash(items[slashIndex]);
      return;
    }
    const item = paletteItems[slashIndex];
    if (item) acceptPaletteItem(item);
  }

  function renderPalette(kind, rows, trigger) {
    if (!slashMenu || !slashList) return;
    slashKind = kind;
    paletteTrigger = trigger || null;
    paletteItems = rows || [];
    if (!paletteItems.length && kind !== "at") {
      closeSlash();
      return;
    }
    if (slashIndex >= paletteItems.length) slashIndex = Math.max(0, paletteItems.length - 1);
    if (slashIndex < 0) slashIndex = 0;
    closeRewind();
    closeContextPop();
    if (window.FuseComposerSpell && FuseComposerSpell.isOpen()) FuseComposerSpell.close();
    const [title, hint] = paletteKicker(kind);
    if (slashKicker) slashKicker.innerHTML = `${escapeHtml(title)} <span>${escapeHtml(hint)}</span>`;
    slashMenu.hidden = false;
    slashMenu.setAttribute("data-kind", kind);
    slashList.innerHTML = "";
    if (!paletteItems.length) {
      const empty = document.createElement("p");
      empty.className = "chat-slash-empty";
      empty.textContent = "No matches";
      slashList.appendChild(empty);
    } else {
      paletteItems.forEach((item, index) => {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "chat-slash-item" + (index === slashIndex ? " is-active" : "");
        btn.setAttribute("role", "option");
        btn.setAttribute("aria-selected", String(index === slashIndex));
        btn.id = "chat-slash-" + kind + "-" + index;
        const label = item.kind === "skill" ? item.text : item.text;
        btn.innerHTML = `<kbd>${escapeHtml(label)}</kbd><span>${escapeHtml(item.hint || item.kind || "")}</span>`;
        btn.addEventListener("mousedown", (event) => event.preventDefault());
        btn.addEventListener("click", () => acceptPaletteItem(item));
        slashList.appendChild(btn);
      });
      slashList.setAttribute("aria-activedescendant", "chat-slash-" + kind + "-" + slashIndex);
      const active = slashList.querySelector(".is-active");
      if (active) active.scrollIntoView({ block: "nearest" });
    }
    setEnterKeyHintEnter();
    syncComposerAria();
  }

  function openHistoryPeek() {
    if (!composerPrefs().history) return;
    const prompts = window.FuseComposerComplete
      ? FuseComposerComplete.history(getComposerContext())
      : [];
    if (!prompts.length) return;
    slashIndex = 0;
    renderPalette("history", prompts.map((text) => ({
      kind: "history",
      text: text.length > 72 ? text.slice(0, 69) + "…" : text,
      insert: text,
      hint: "prompt",
    })), { start: 0, end: 0, kind: "history", query: "" });
  }

  function renderComposerSuggestions() {
    if (window.FuseComposerSpell && FuseComposerSpell.composing) {
      closeSlash();
      return;
    }
    const q = slashQuery();
    if (q) {
      slashKind = "slash";
      if (filteredSlash().length) renderSlash();
      else closeSlash();
      return;
    }
    const palettesOn = composerPrefs().palettes;
    if (palettesOn && window.FuseComposerComplete) {
      const t = FuseComposerComplete.trigger(input.value, input.selectionStart, getComposerContext());
      if (t && t.kind === "at") {
        slashIndex = 0;
        renderPalette("at", FuseComposerComplete.items(t, getComposerContext()), t);
        return;
      }
      if (t && t.kind === "path") {
        const rows = FuseComposerComplete.items(t, getComposerContext());
        if (rows.length) {
          slashIndex = 0;
          renderPalette("path", rows, t);
          return;
        }
      }
    }
    closeSlash();
  }

  function slashValue(cmd, extra) {
    const arg = extra == null ? "" : String(extra).trim();
    if (arg) return "/" + cmd.name + " " + arg;
    return "/" + cmd.name + (cmd.args ? " " : "");
  }

  function acceptSlash(cmd) {
    if (!cmd) return;
    const query = slashQuery();
    input.value = slashValue(cmd, query && cmd.args ? query.arg : "");
    closeSlash();
    drafts.set(currentId, input.value);
    resizeInput();
    syncComposer();
    syncEmptyComposer();
    input.focus();
    const end = input.value.length;
    try { input.setSelectionRange(end, end); } catch (_) {}
  }

  function renderSlash() {
    if (!slashMenu || !slashList) return;
    const items = filteredSlash();
    if (!items.length) {
      closeSlash();
      return;
    }
    slashKind = "slash";
    paletteTrigger = null;
    paletteItems = [];
    if (slashIndex >= items.length) slashIndex = items.length - 1;
    if (slashIndex < 0) slashIndex = 0;
    closeRewind();
    closeContextPop();
    if (slashKicker) slashKicker.innerHTML = `Commands <span>Tab to complete · Enter to select</span>`;
    slashMenu.hidden = false;
    slashMenu.setAttribute("data-kind", "slash");
    slashList.innerHTML = "";
    items.forEach((cmd, index) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "chat-slash-item" + (index === slashIndex ? " is-active" : "");
      if (!slashAvailable(cmd)) btn.classList.add("is-off");
      btn.setAttribute("role", "option");
      btn.setAttribute("aria-selected", String(index === slashIndex));
      btn.id = "chat-slash-" + cmd.name;
      btn.innerHTML = `<kbd>/${escapeHtml(cmd.name)}</kbd><span>${escapeHtml(cmd.hint)}</span>`;
      btn.addEventListener("mousedown", (event) => event.preventDefault());
      btn.addEventListener("click", () => acceptSlash(cmd));
      slashList.appendChild(btn);
    });
    slashList.setAttribute("aria-activedescendant", items[slashIndex] ? "chat-slash-" + items[slashIndex].name : "");
    const active = slashList.querySelector(".is-active");
    if (active) active.scrollIntoView({ block: "nearest" });
    setEnterKeyHintEnter();
    syncComposerAria();
  }

  function closeRewind() {
    const wasOpen = rewindPanel && !rewindPanel.hidden;
    if (rewindPanel) rewindPanel.hidden = true;
    if (rewindList) rewindList.innerHTML = "";
    if (wasOpen) {
      armOverlayGhost(rewindClose);
      overlayDrop("rewind");
    }
  }

  function openRewind() {
    if (!rewindPanel || !rewindList) return;
    if (runIsActive(activeRun) || sending) return;
    if (!requireLive()) return;
    closeSlash();
    overlayPush("rewind");
    const prompts = messages
      .map((msg, index) => ({ msg, index }))
      .filter((item) => item.msg.role === "user");
    rewindPanel.hidden = false;
    rewindList.innerHTML = "";
    if (!prompts.length) {
      const empty = document.createElement("p");
      empty.className = "chat-rewind-empty";
      empty.textContent = "No messages to rewind to yet.";
      rewindList.appendChild(empty);
      return;
    }
    prompts.slice().reverse().forEach((item) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "chat-rewind-item";
      const preview = String(item.msg.content || "").replace(/\s+/g, " ").trim() || "(empty)";
      const after = messages.length - item.index - 1;
      btn.innerHTML = `<strong>${escapeHtml(preview.slice(0, 72))}</strong><span>${
        after ? after + " after this" : "latest"
      }</span>`;
      btn.addEventListener("click", () => rewindTo(item.index));
      rewindList.appendChild(btn);
    });
  }

  function runSlash(name, arg) {
    closeSlash();
    const query = slashQuery();
    const rest = arg != null ? arg : (query && query.arg) || "";
    input.value = "";
    resizeInput();
    syncComposer();
    if (name === "undo") {
      const times = Math.max(1, Math.min(20, parseInt(rest, 10) || 1));
      for (let i = 0; i < times; i += 1) undoConversation();
      return;
    }
    if (name === "retry") {
      retryLastTurn();
      return;
    }
    if (name === "redo") {
      redoConversation();
      return;
    }
    if (name === "rewind") {
      openRewind();
      return;
    }
    if (name === "help") {
      setHint("/undo, /retry, /redo, /rewind, /title, /permissions, /compact, /init. Skills: type / then a name. Manage on Skills.");
      return;
    }
    if (name === "permissions") {
      setPermsOpen(true);
      return;
    }
    if (name === "title") {
      runTitleRegen();
      return;
    }
    if (name === "compact") {
      runCompact(rest);
      return;
    }
    if (name === "init") {
      if (!sessionProject()) {
        setHint("/init only works in a project chat.");
        return;
      }
      completeTurn(rest ? "/init " + rest : "/init");
    }
  }

  function retryAssistant(index) {
    if (runIsActive(activeRun) || sending || messages[index - 1]?.role !== "user") return;
    const previous = messages[index - 1];
    const snap = snapshotMessages();
    messages = messages.slice(0, index - 1);
    persist(true);
    recountLocalContext();
    completeTurn(previous.content, (previous.attachments || []).slice(), { undoSnap: snap });
  }

  function retryLastTurn() {
    if (runIsActive(activeRun) || sending) {
      setHint("Wait for the current reply to finish.", true);
      return;
    }
    if (!requireLive()) return;
    const last = messages.at(-1);
    if (!last) {
      setHint("Nothing to retry.");
      return;
    }
    const index = last.role === "assistant" ? messages.length - 1 : messages.length;
    if (messages[index - 1]?.role !== "user") {
      setHint("Nothing to retry.");
      return;
    }
    retryAssistant(index);
  }

  function syncEmptyReplyRetry() {
    const last = messages.at(-1);
    const empty = last?.role === "assistant" && !((last.content || "").trim() || (last.reasoning || "").trim() || last.error || last.tools?.length || last.permissions?.length);
    const existing = log.querySelector("[data-empty-retry]");
    if (!empty || runIsActive(activeRun) || messages.at(-2)?.role !== "user") { existing?.remove(); return; }
    if (existing?.dataset.emptyRetry === last.id) return;
    existing?.remove();
    const host = log.children[messages.length - 2]?.querySelector(".chat-msg-actions");
    if (!host) return;
    const button = document.createElement("button");
    button.type = "button"; button.className = "chat-icon-btn";
    button.dataset.emptyRetry = last.id; button.title = "Retry"; button.setAttribute("aria-label", "Retry");
    button.innerHTML = ACTION_ICONS.retry;
    button.addEventListener("click", () => retryAssistant(messages.findIndex(msg => msg.id === last.id)));
    host.appendChild(button);
  }

  function msgActions(index, role) {
    const bar = document.createElement("div");
    bar.className = "chat-msg-actions";
    const add = (label, svg, act) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "chat-icon-btn";
      btn.title = label;
      btn.setAttribute("aria-label", label);
      btn.innerHTML = svg;
      btn.addEventListener("click", (event) => {
        event.preventDefault();
        act(btn);
      });
      bar.appendChild(btn);
    };
    add("Copy", ACTION_ICONS.copy, (btn) => {
      const msg = messages[index];
      copyText(msg ? rewriteCites(msg.content, sourceCatalog(msg), false) : "")
        .then(() => {
          btn.innerHTML = ACTION_ICONS.copied;
          btn.setAttribute("aria-label", "Copied");
          setTimeout(() => {
            btn.innerHTML = ACTION_ICONS.copy;
            btn.setAttribute("aria-label", "Copy");
          }, 1200);
        })
        .catch(() => {});
    });
    if (role === "user") add("Edit", ACTION_ICONS.edit, () => beginEdit(index));
    if (role === "user") add("Rewind", ACTION_ICONS.rewind, () => rewindTo(index));
    if (role === "assistant" && index === messages.length - 1) add("Retry", ACTION_ICONS.retry, () => retryAssistant(index));
    add("More", ACTION_ICONS.more, (btn) => {
      const items = [
        { label: "Delete", danger: true, run: () => confirmDeleteAt(index, "one") },
        { label: "Delete from here", danger: true, run: () => confirmDeleteAt(index, "from") },
        { label: "Keep up to here", danger: true, run: () => confirmDeleteAt(index, "after") },
      ];
      if (messages.length || undoStack.length || redoStack.length) items.push({ sep: true });
      if (messages.length || undoStack.length) items.push({ label: "Undo last turn", run: () => undoConversation() });
      if (redoStack.length) items.push({ label: "Redo", run: () => redoConversation() });
      showPop(btn, items);
    });
    return bar;
  }

  function autoEventLabel(content) {
    const text = String(content || "");
    if (text.includes("SCHEDULED RUN")) return "Automated · scheduled run";
    const commands = text.includes("<command_result");
    const monitors = text.includes("<monitor_event") || text.includes("<monitor_result");
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
      const rawLine = meta.split("\n").find((l) => l.startsWith("host:")) || "";
      const hm = rawLine.match(/^host:\s*(.*?)\s*(?:command|source):\s*(.*)$/);
      const target = pick("target") || (hm ? hm[2].trim() : pick("command"));
      const events = Number(pick("events") || attr("count"));
      results.push({
        id: match[2],
        tag,
        state: attr("state") || (tag === "monitor_event" ? "running" : ""),
        host: hm ? hm[1].trim() : pick("host"),
        command: target,
        url: pick("url"),
        source: pick("source"),
        event_count: Number.isFinite(events) ? events : null,
        exit_code: /^-?\d+$/.test(code) ? Number(code) : null,
        duration_ms: Number.isFinite(duration) ? duration : null,
        error: pick("error"),
        output: output.trim(),
        monitor: tag !== "command_result",
      });
    }
    return results;
  }

  function applyCommandResult(trace, result) {
    if (["get_command_output", "kill_command"].includes(trace.name)) return;
    // Legacy wake envelopes must not replace an authoritative retained-output
    // record with a shorter text preview or discard its provenance.
    if (typeof trace.output === "string" && result.output_mode == null && result.output_chars == null) return;
    if (["running", "cancelling"].includes(result.state)) {
      if (trace.status !== "done") trace.status = "running";
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

  function foldAutoCommandResults() {
    messages.forEach((msg, index) => {
      if (msg.role !== "user" || !msg.auto) return;
      const text = String(msg.content || "");
      if ((!text.includes("<command_result") && !text.includes("<monitor_")) || text.includes("<task id=")) {
        msg.autoSteps = null;
        return;
      }
      const results = parseCommandResults(text);
      msg.autoSteps = results.length ? results.map(wakeStepTrace) : null;
      const findTrace = (id) => {
        for (const name of ["run_terminal_command", "get_command_output", "monitor"]) {
          for (let i = 0; i < index; i++) {
            const prev = messages[i];
            if (prev.role !== "assistant" || !Array.isArray(prev.tools)) continue;
            const hit = prev.tools.find((t) => t && t.name === name && String(executionIdentity(t)) === id);
            if (hit) return hit;
          }
        }
        return null;
      };
      results.forEach((result) => {
        const trace = findTrace(result.id);
        if (trace) applyCommandResult(trace, result);
      });
    });
  }

  function renderAutoSteps(msg) {
    const article = document.createElement("article");
    article.className = "chat-turn is-user is-auto-steps";
    article.setAttribute("aria-label", "Background command results");
    const steps = msg.autoSteps
      .map((trace, i) => toolStepHtml(trace, "wake-" + (msg.id || "") + "-" + i, false, false, null))
      .join("");
    article.innerHTML = '<div class="chat-auto-steps"><ol class="chat-timeline">' + steps + "</ol></div>";
    article.addEventListener("click", (event) => {
      const btn = event.target.closest("[data-step-toggle]");
      if (!btn || event.target.closest(".chat-step-open")) return;
      const li = btn.closest("[data-step]");
      if (!li) return;
      const reveal = li.querySelector(":scope > .chat-step-main > .chat-step-reveal");
      if (!reveal) return;
      event.preventDefault();
      const open = !reveal.classList.contains("is-open");
      setToolReveal(reveal, open);
      li.querySelectorAll(":scope > .chat-step-main > .chat-step-head [data-step-toggle]").forEach((el) => el.setAttribute("aria-expanded", String(open)));
    });
    return article;
  }

  function renderUser(msg, index, live) {
    const article = document.createElement("article");
    article.className = "chat-turn is-user";
    article.setAttribute("aria-label", "Your message");
    if (msg.auto) {
      if (Array.isArray(msg.autoSteps) && msg.autoSteps.length) {
        return renderAutoSteps(msg);
      }
      const label = autoEventLabel(msg.content);
      article.classList.add("is-auto");
      article.setAttribute("aria-label", label);
      article.innerHTML =
        '<button type="button" class="chat-auto-chip" data-auto-toggle aria-expanded="false">' +
        '<span class="chat-auto-dot" aria-hidden="true"></span>' +
        '<span class="chat-auto-label">' + escapeHtml(label) + "</span>" +
        STEP_CHEVRON +
        "</button>" +
        '<div class="chat-auto-reveal"><div class="chat-auto-reveal-inner"><pre>' + escapeHtml(msg.content) + "</pre></div></div>";
      return article;
    }
    if (editingIndex === index) {
      const wrap = document.createElement("div");
      wrap.className = "chat-edit";
      const area = document.createElement("textarea");
      area.dataset.edit = "1";
      area.setAttribute("aria-label", "Edit message");
      area.value = msg.content;
      const actions = document.createElement("div");
      actions.className = "chat-edit-actions";
      const cancel = document.createElement("button");
      cancel.type = "button";
      cancel.className = "btn ghost sm";
      cancel.textContent = "Cancel";
      cancel.addEventListener("click", () => {
        editingIndex = -1;
        editingFiles = [];
        paint();
      });
      const save = document.createElement("button");
      save.type = "button";
      save.className = "btn sm";
      save.textContent = "Save & resend";
      save.addEventListener("click", () => commitEdit(index, area.value));
      area.addEventListener("keydown", (event) => {
        if (event.key === "Escape") {
          editingIndex = -1;
          editingFiles = [];
          paint();
        } else if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
          event.preventDefault();
          commitEdit(index, area.value);
        }
      });
      actions.appendChild(cancel);
      actions.appendChild(save);
      wrap.appendChild(area);
      if (editingFiles.length) wrap.appendChild(editFilesStrip());
      wrap.appendChild(actions);
      article.appendChild(wrap);
      return article;
    }
    fillUserFiles(article, msg, index);
    if (msg.content) {
      const bubble = document.createElement("div");
      bubble.className = "chat-bubble";
      bubble.textContent = msg.content;
      article.appendChild(bubble);
    }
    if (!live && !msg.noActions) article.appendChild(msgActions(index, "user"));
    return article;
  }

  const FILE_CHIP_ICON =
    '<svg viewBox="0 0 16 16" width="13" height="13" aria-hidden="true"><path d="M4.3 2.7h5.1l2.4 2.5v8.1H4.3z" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"/><path d="M9.4 2.7v2.5h2.4M6.1 8.4h3.8M6.1 10.6h2.6" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"/></svg>';

  function fileExtLabel(name) {
    const match = String(name || "").match(/\.([a-z0-9]{1,5})$/i);
    return match ? match[1].toUpperCase() : "";
  }

  function imagePreviewButton(att) {
    const name = att.name || "";
    return `<button type="button" class="chat-user-file-view" data-image-preview="${escapeHtml(att.data)}" data-image-name="${escapeHtml(name)}" aria-label="Preview ${escapeHtml(name || "image")} full screen"><img src="${escapeHtml(att.data)}" alt="${escapeHtml(name || "Image")}" loading="lazy"></button>`;
  }

  function attachmentRemoveButton(att, position) {
    const key = String(att.id || "");
    return `<button type="button" class="chat-user-file-x" data-remove-attachment="${escapeHtml(key || String(position))}" data-att-index="${position}" aria-label="Remove ${escapeHtml(att.name || "attachment")} from this message" title="Remove from message"><svg viewBox="0 0 16 16" width="11" height="11" aria-hidden="true"><path d="M4 4l8 8M12 4l-8 8" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg></button>`;
  }

  function attachmentChipHtml(att, position) {
    if (att.kind === "image" && att.data) {
      return `<figure class="chat-user-file is-image">${imagePreviewButton(att)}${attachmentRemoveButton(att, position)}</figure>`;
    }
    const ext = fileExtLabel(att.name);
    return `<div class="chat-user-file is-file"><span class="chat-file-icon">${FILE_CHIP_ICON}</span><span class="chat-file-name">${escapeHtml(att.name || "File")}</span>${
      ext ? `<span class="chat-file-ext">${escapeHtml(ext)}</span>` : ""
    }${attachmentRemoveButton(att, position)}</div>`;
  }

  function fillUserFiles(article, msg, index) {
    let row = article.querySelector("[data-user-files]");
    const files = Array.isArray(msg.attachments) ? msg.attachments : [];
    if (!files.length) {
      if (row) row.remove();
      return;
    }
    if (!row) {
      row = document.createElement("div");
      row.className = "chat-user-files";
      row.dataset.userFiles = "1";
      const bubble = article.querySelector(".chat-bubble");
      article.insertBefore(row, bubble || article.firstChild);
    }
    row.dataset.msgIndex = String(index);
    const signature = JSON.stringify(files);
    if (row._filesSignature === signature) return;
    row._filesSignature = signature;
    row.innerHTML = files.map((att, position) => attachmentChipHtml(att, position)).join("");
  }

  function dropAttachment(list, key, position) {
    return list.filter((att, i) => {
      const id = String(att.id || "");
      return id ? id !== key : i !== position;
    });
  }

  function removeAttachment(index, key, position) {
    if (runIsActive(activeRun) || sending) {
      setHint("Wait for the reply to finish before changing attachments.", true, 3200);
      return;
    }
    if (!requireLive()) return;
    const msg = messages[index];
    if (!msg || msg.role !== "user") return;
    const atts = Array.isArray(msg.attachments) ? msg.attachments : [];
    if (!atts.length) return;
    const kept = dropAttachment(atts, key, position);
    if (kept.length === atts.length) return;
    const empties = !kept.length && !(msg.content || "").trim();
    mutateTranscript(() => {
      const target = messages[index];
      if (!target || target.role !== "user") return;
      const rest = dropAttachment(Array.isArray(target.attachments) ? target.attachments : [], key, position);
      if (rest.length === (target.attachments || []).length) return;
      if (!rest.length && !(target.content || "").trim()) messages.splice(index, 1);
      else target.attachments = rest;
    }, empties ? "Removed the attachment and its empty message." : "Attachment removed. It will leave the model's context on the next turn.");
  }

  function openLightbox(src, name) {
    if (!src) return;
    closeLightbox();
    const overlay = document.createElement("div");
    overlay.className = "chat-lightbox";
    overlay.setAttribute("role", "dialog");
    overlay.setAttribute("aria-modal", "true");
    overlay.setAttribute("aria-label", name ? `Image preview: ${name}` : "Image preview");
    const img = document.createElement("img");
    img.className = "chat-lightbox-img";
    img.src = src;
    img.alt = name || "Image preview";
    overlay.appendChild(img);
    if (name) {
      const cap = document.createElement("p");
      cap.className = "chat-lightbox-name";
      cap.textContent = name;
      overlay.appendChild(cap);
    }
    const close = document.createElement("button");
    close.type = "button";
    close.className = "chat-lightbox-close";
    close.setAttribute("aria-label", "Close preview");
    close.innerHTML = '<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><path d="M4 4l8 8M12 4l-8 8" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>';
    overlay.appendChild(close);
    const onKey = (event) => {
      if (event.key === "Escape") {
        event.preventDefault();
        closeLightbox();
      }
    };
    close.addEventListener("click", () => closeLightbox());
    overlay.addEventListener("click", (event) => {
      if (event.target === overlay) closeLightbox();
    });
    document.body.appendChild(overlay);
    document.addEventListener("keydown", onKey);
    lightbox = { el: overlay, onKey, prevFocus: document.activeElement };
    requestAnimationFrame(() => {
      overlay.classList.add("is-open");
      close.focus();
    });
  }

  function closeLightbox() {
    if (!lightbox) return;
    const entry = lightbox;
    lightbox = null;
    document.removeEventListener("keydown", entry.onKey);
    entry.el.remove();
    if (entry.prevFocus && typeof entry.prevFocus.focus === "function") {
      try { entry.prevFocus.focus(); } catch (_) {}
    }
  }

  function editFilesStrip() {
    const strip = document.createElement("div");
    strip.className = "chat-edit-files";
    strip.setAttribute("aria-label", "Attachments kept with this message");
    strip.innerHTML = editingFiles
      .map((att, index) => {
        const thumb =
          att.kind === "image" && att.data
            ? `<button type="button" class="chat-attach-thumb" data-image-preview="${escapeHtml(att.data)}" data-image-name="${escapeHtml(att.name || "")}" aria-label="Preview ${escapeHtml(att.name || "image")}"><img src="${escapeHtml(att.data)}" alt=""></button>`
            : `<span class="chat-attach-icon">${FILE_CHIP_ICON}</span>`;
        return `<div class="chat-attach-chip">${thumb}<span>${escapeHtml(att.name || "File")}</span><button type="button" data-edit-file-remove="${index}" aria-label="Remove ${escapeHtml(att.name || "file")}"><svg viewBox="0 0 16 16" width="10" height="10" aria-hidden="true"><path d="M4 4l8 8M12 4l-8 8" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg></button></div>`;
      })
      .join("");
    strip.addEventListener("click", (event) => {
      const view = event.target.closest("[data-image-preview]");
      if (view) {
        event.preventDefault();
        openLightbox(view.getAttribute("data-image-preview"), view.getAttribute("data-image-name") || "");
        return;
      }
      const btn = event.target.closest("[data-edit-file-remove]");
      if (!btn) return;
      event.preventDefault();
      editingFiles.splice(Number(btn.getAttribute("data-edit-file-remove")), 1);
      const next = editFilesStrip();
      strip.replaceWith(next);
      const box = next.parentElement?.querySelector("[data-edit]");
      if (box) box.focus();
    });
    return strip;
  }

  function sourceList(rows) {
    const items = (rows || []).map((row) => {
      const url = safeUrl(row.url);
      if (!url || !/^https?:/i.test(url)) return "";
      let domain = url;
      try {
        domain = new URL(url).hostname.replace(/^www\./, "");
      } catch (_) {}
      const state = row.cited ? "cited" : row.status === "read" ? "read" : "found";
      return `<li><a class="chat-source-row" href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer"><span class="chat-source-domain">${escapeHtml(domain)}</span><span class="chat-source-title">${escapeHtml(row.title || url)}</span><span class="chat-source-state is-${state}">${state}</span></a></li>`;
    }).filter(Boolean);
    if (!items.length) return "";
    return `<ul class="chat-source-list">${items.join("")}</ul>`;
  }

  const STEP_ICONS = {
    think: '<svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true"><path d="M12 5a3 3 0 1 0-5.997.125 4 4 0 0 0-2.526 5.77 4 4 0 0 0 .556 6.588A4 4 0 1 0 12 18Z" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/><path d="M12 5a3 3 0 1 1 5.997.125 4 4 0 0 1 2.526 5.77 4 4 0 0 1-.556 6.588A4 4 0 1 1 12 18Z" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/><path d="M15 13a4.5 4.5 0 0 1-3-4 4.5 4.5 0 0 1-3 4" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    search: '<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><circle cx="7.2" cy="7.2" r="3.6" fill="none" stroke="currentColor" stroke-width="1.3"/><path d="M10 10.1L13.4 13.5" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/></svg>',
    read: '<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><path d="M8 4.1C7.2 3.2 6 2.7 4.4 2.7H2.4v9.3h2.4c1.5 0 2.6.5 3.2 1.3" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"/><path d="M8 4.1c.8-.9 2-1.4 3.6-1.4h2v9.3h-2.4c-1.5 0-2.6.5-3.2 1.3" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"/><path d="M8 4.1v8.8" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/></svg>',
    link: '<svg viewBox="0 0 16 16" width="13" height="13" aria-hidden="true"><path d="M6.4 3.2H3.6A1.2 1.2 0 0 0 2.4 4.4v8a1.2 1.2 0 0 0 1.2 1.2h8a1.2 1.2 0 0 0 1.2-1.2V9.6" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"/><path d="M9.2 2.6H13.4V6.8M7.6 8.4L13.4 2.6" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    file: '<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><path d="M3.4 4.4h9.2M3.4 8h9.2M3.4 11.6h6.2" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/></svg>',
    tool: '<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><rect x="2.5" y="3.2" width="11" height="9.6" rx="1.8" fill="none" stroke="currentColor" stroke-width="1.3"/><path d="M5.5 6.3l2 1.7-2 1.7M8.6 10h2.2" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    error: '<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><path d="M8 2.8l5.1 8.9H2.9z" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"/><path d="M8 6.4v2.4" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/><circle cx="8" cy="11" r="0.55" fill="currentColor"/></svg>',
    stop: '<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><rect x="4.8" y="4.8" width="6.4" height="6.4" rx="1.2" fill="none" stroke="currentColor" stroke-width="1.3"/></svg>',
    artifact: '<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><rect x="2.8" y="3.6" width="10.4" height="8.8" rx="1.4" fill="none" stroke="currentColor" stroke-width="1.3"/><path d="M5 9.1l1.6-1.9 1.5 1.6 2.1-2.6 2.1 2.9" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"/></svg>',
    web: '<svg viewBox="0 0 16 16" width="13" height="13" aria-hidden="true"><circle cx="8" cy="8" r="5.1" fill="none" stroke="currentColor" stroke-width="1.25"/><ellipse cx="8" cy="8" rx="2.15" ry="5.1" fill="none" stroke="currentColor" stroke-width="1.25"/><path d="M2.9 8h10.2" fill="none" stroke="currentColor" stroke-width="1.25"/></svg>',
    folder: '<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><path d="M2.6 4.7h3.2l1.1 1.4H13.4v6.4H2.6z" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"/></svg>',
    file: '<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><path d="M4.3 2.7h5.1L11.8 5.2v8.1H4.3z" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"/><path d="M9.4 2.7v2.5h2.4M6.1 8.4h3.8M6.1 10.6h2.6" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    grep: '<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><circle cx="7" cy="7" r="3.3" fill="none" stroke="currentColor" stroke-width="1.3"/><path d="M9.5 9.6L12.8 13M5.6 7h2.8" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/></svg>',
    glob: '<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><path d="M8 2.7v10.6M2.7 8h10.6M4.4 4.4l7.2 7.2M11.6 4.4l-7.2 7.2" fill="none" stroke="currentColor" stroke-width="1.25" stroke-linecap="round"/></svg>',
    write: '<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><path d="M4.2 2.8h5L11.7 5.3v8H4.2z" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"/><path d="M9.2 2.8v2.5h2.5M8 7.2v4.4M5.8 9.4h4.4" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    edit: '<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><path d="M9.3 3.4l3.3 3.3-6.6 6.6H2.7v-3.3z" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"/><path d="M8.2 4.5l3.3 3.3" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/></svg>',
    shell: '<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><rect x="2.5" y="3.2" width="11" height="9.6" rx="1.7" fill="none" stroke="currentColor" stroke-width="1.3"/><path d="M5.4 6.4l2.1 1.6-2.1 1.6M8.6 10.1h2.3" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    watch: '<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><circle cx="8" cy="8" r="5.1" fill="none" stroke="currentColor" stroke-width="1.3"/><path d="M8 5.2v3.1l2 1.2" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    tools: '<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><path d="M6.2 9.8L3.4 12.6M10.4 3.2l2.4 2.4-6.1 6.1H4.3V9.3z" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"/></svg>',
    skill: '<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><path d="M3.1 3.2h3.5c.8 0 1.4.3 1.4 1.1 0-.8.6-1.1 1.4-1.1h3.5v9.1H9.5c-.9 0-1.5.3-1.5 1.1 0-.8-.6-1.1-1.5-1.1H3.1z" fill="none" stroke="currentColor" stroke-width="1.25" stroke-linejoin="round"/><path d="M8 4.3v9.1" fill="none" stroke="currentColor" stroke-width="1.25"/></svg>',
    agent: '<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><circle cx="5.2" cy="6.2" r="1.7" fill="none" stroke="currentColor" stroke-width="1.3"/><circle cx="10.8" cy="6.2" r="1.7" fill="none" stroke="currentColor" stroke-width="1.3"/><path d="M3.5 12.2c.4-1.6 1.5-2.5 3-2.5s2.6.9 3 2.5M9.3 9.7c.5-.3 1.1-.5 1.7-.5 1.5 0 2.6.9 3 2.5" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/></svg>',
  };
  const STEP_CHEVRON =
    '<svg class="chat-step-chevron" viewBox="0 0 16 16" width="11" height="11" aria-hidden="true"><path d="M5 6.5l3 3 3-3" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>';
  let activitySeq = 0;

  function viewGroupsAdjacent() {
    return !isConsoleView();
  }

  function viewCollapsesActivity() {
    return chatView === "compact";
  }

  function viewFlattensNested() {
    return true;
  }

  function thinkLabel() {
    if (chatView === "compact") return "Think";
    if (isConsoleView()) return "thinking";
    return "think";
  }

  function visibleTraces(traces) {
    const list = Array.isArray(traces) ? traces : [];
    // A returned poll and a subsequent wait are separate invocations even
    // when they reference the same execution. Count and identify both.
    return list.map((trace, index) => ({ trace, index }));
  }

  function groupTraces(traces) {
    const groups = [];
    visibleTraces(traces).forEach(({ trace, index }) => {
      const last = groups[groups.length - 1];
      const key = stackKey(trace && trace.name);
      if (viewGroupsAdjacent() && last && last.key === key) last.items.push({ trace, index });
      else groups.push({ name: (trace && trace.name) || "unknown", key, items: [{ trace, index }] });
    });
    return groups;
  }

  function normErr(s) {
    return String(s || "").replace(/\s+/g, " ").trim().toLowerCase();
  }

  function coalesceLines(raw) {
    const lines = String(raw || "")
      .split(/\n+/)
      .map((s) => s.trim())
      .filter(Boolean);
    const order = [];
    const counts = new Map();
    for (const line of lines) {
      const key = line.toLowerCase();
      if (!counts.has(key)) order.push(line);
      counts.set(key, (counts.get(key) || 0) + 1);
    }
    return order.map((line) => {
      const n = counts.get(line.toLowerCase());
      return n > 1 ? `${line} ×${n}` : line;
    });
  }

  function uniformFail(items) {
    if (!items.length || !items.every((i) => toolFailed(i.trace))) return "";
    const first = normErr(items[0].trace.error || "failed");
    if (!items.every((i) => normErr(i.trace.error || "failed") === first)) return "";
    return String(items[0].trace.error || "Something went wrong.").replace(/\s+/g, " ").trim();
  }

  function failKind(items) {
    const names = new Set(items.map((i) => i.trace && i.trace.name));
    if (names.size === 1 && names.has("read_url")) return "Page read";
    if (names.size === 1 && isSearchTool([...names][0])) return "Web search";
    if (names.size === 1 && [...names][0] === "read_file") return "File read";
    if (names.size === 1 && [...names][0] === "list_dir") return "Directory listing";
    if (names.size === 1 && [...names][0] === "run_terminal_command") return "Shell";
    if (names.size === 1 && WORKSPACE_VERBS[[...names][0]]) return WORKSPACE_VERBS[[...names][0]];
    return "Steps";
  }

  function failKindWord(items) {
    const names = new Set(items.map((i) => i.trace && i.trace.name));
    if (names.size === 1) return cliKind([...names][0], "", items[0] && items[0].trace);
    return cliKind("", "fail");
  }

  function failNoteHtml(message) {
    const text = String(message || "").trim();
    if (!text) return "";
    return detailSectionHtml("Error", text, "is-error");
  }

  function failClusterHtml(items, index, open = false) {
    const n = items.length;
    const noun = failKind(items);
    const first = items[0] && items[0].trace;
    let label = n > 1 ? `${noun} failed ×${n}` : `${noun} failed`;
    if (n === 1 && first) {
      if (first.name === "read_url") {
        const host = pageHost(first.url);
        label = host ? `Page read failed · ${host}` : "Page read failed";
      } else if (isSearchTool(first.name)) {
        const q = searchQueries(first)[0];
        label = q ? `Web search failed · “${shortToolText(q, 48)}”` : "Web search failed";
      } else if (first.name === "read_file") {
        label = first.path ? `File read failed · ${first.path}` : "File read failed";
      } else if (first.name === "run_terminal_command") {
        const detail = toolText(first.command || "") || "command";
        const exit = Number.isFinite(first.exit_code) ? ` (exit ${first.exit_code})` : "";
        label = `${detail}${exit}`;
      }
    }
    const error = uniformFail(items) || (first && first.error) || "";
    return (
      `<li class="chat-step is-error is-fail-cluster" data-step="${index}">` +
      `<span class="chat-step-node" aria-hidden="true">${STEP_ICONS.error}</span>` +
      `<div class="chat-step-main">${toolHeadHtml({ line: escapeHtml(label), title: label, running: false, failed: true, interrupted: false, expandable: Boolean(error), open, kind: failKindWord(items) })}` +
      (error ? `<div class="chat-step-reveal${open ? " is-open" : ""}"><div class="chat-step-reveal-inner"><div class="chat-tool-details">${failNoteHtml(error)}</div></div></div>` : "") +
      `</div></li>`
    );
  }

  function stepOpenAt(map, key) {
    return Boolean(map && map[key] === true);
  }

  // Disclosure is device-local UI state, never part of the server transcript.
  // Retain a bounded set of boolean maps for reloads and thread navigation.
  const DISCLOSURE_STORE = "fuse.chat.disclosures.v1";
  function disclosureKey(msg) {
    return currentId && msg.id ? `${currentId}:${msg.id}` : "";
  }
  function saveDisclosures(msg) {
    const key = disclosureKey(msg);
    if (!key) return;
    try {
      const saved = JSON.parse(sessionStorage.getItem(DISCLOSURE_STORE) || "{}");
      delete saved[key];
      saved[key] = { stepOpen: msg.stepOpen || {}, activityOpenMap: msg.activityOpenMap || {} };
      sessionStorage.setItem(DISCLOSURE_STORE, JSON.stringify(Object.fromEntries(Object.entries(saved).slice(-100))));
    } catch (_) {}
  }
  function restoreDisclosures(msg) {
    const key = disclosureKey(msg);
    if (!key) return;
    try {
      const saved = JSON.parse(sessionStorage.getItem(DISCLOSURE_STORE) || "{}")[key];
      for (const field of ["stepOpen", "activityOpenMap"]) {
        if (!msg[field] && saved?.[field] && typeof saved[field] === "object") {
          msg[field] = Object.fromEntries(Object.entries(saved[field]).filter(([, value]) => typeof value === "boolean"));
        }
      }
    } catch (_) {}
  }

  function thinkOpenAt(map, key) {
    if (map && Object.prototype.hasOwnProperty.call(map, key)) return map[key] === true;
    return false;
  }

  function searchCountFor(trace) {
    if (!trace) return 0;
    if (Array.isArray(trace.queries) && trace.queries.length) return trace.queries.length;
    return 1;
  }

  function searchSiteCount(group) {
    return group.items.reduce((n, item) => n + searchCountFor(item.trace), 0);
  }

  function ranSearchesLabelHtml(n) {
    const count = Math.max(1, Number(n) || 1);
    return `Searched <span class="chat-count" data-search-count>${count}</span> <span data-search-noun>${count === 1 ? "web query" : "web queries"}</span>`;
  }

  function searchQueries(trace) {
    if (!trace) return [];
    if (Array.isArray(trace.queries) && trace.queries.length) return trace.queries.filter((q) => typeof q === "string" && q.trim());
    if (typeof trace.query === "string" && trace.query.trim()) return [trace.query];
    return [];
  }

  function queryRowHtml(query, running) {
    const q = String(query || "").replace(/\s+/g, " ").trim();
    const shown = q ? `“${escapeHtml(q)}”` : (running ? "Searching" : "the web");
    const title = running
      ? q ? `Searching the web for ${q}` : "Searching the web"
      : q ? `Searched the web for ${q}` : "Searched the web";
    return (
      `<div class="chat-query${running ? " is-running" : ""}">` +
      `<span class="chat-query-icon" aria-hidden="true">${STEP_ICONS.web}</span>` +
      `<span class="chat-query-label" title="${escapeHtml(title)}">${shown}</span>` +
      "</div>"
    );
  }

  function nestRowsHtml(rows, kind, openMap, openKey) {
    if (!rows.length) return "";
    if (viewFlattensNested() || rows.length <= 2) return `<div class="chat-substeps">${rows.join("")}</div>`;
    const latest = rows[rows.length - 1];
    const prev = rows.slice(0, -1);
    const n = prev.length;
    const open = stepOpenAt(openMap, openKey);
    const noun = kind === "page" ? (n === 1 ? "page" : "pages") : n === 1 ? "search" : "searches";
    return (
      `<div class="chat-substeps">${latest}` +
      `<div class="chat-prev" data-prev="${escapeHtml(openKey)}">` +
      `<button type="button" class="chat-prev-toggle" data-prev-toggle aria-expanded="${open ? "true" : "false"}">` +
      `${STEP_CHEVRON}<span>+${n} previous ${noun}</span></button>` +
      `<div class="chat-step-reveal${open ? " is-open" : ""}"><div class="chat-step-reveal-inner">${prev.join("")}</div></div>` +
      "</div></div>"
    );
  }

  function queryListHtml(items, live, openMap, openKey) {
    const rows = [];
    items.forEach((entry) => {
      const trace = entry.trace || entry;
      const running = toolRunning(trace, live);
      const queries = searchQueries(trace);
      if (!queries.length) {
        rows.push(queryRowHtml("", running));
        return;
      }
      queries.forEach((q, i) => rows.push(queryRowHtml(q, running && i === queries.length - 1)));
    });
    return nestRowsHtml(rows, "search", openMap, openKey || "prev-search");
  }

   function settleToolReveal(row) {
    row?.querySelectorAll(".chat-step-reveal.is-open > .chat-step-reveal-inner").forEach((el) => {
      el.style.animation = "none";
    });
  }

  function patchSettledToolRow(row, freshRow) {
    if (!row?.dataset.state || row.dataset.state === "running" || freshRow?.dataset.state !== row.dataset.state) return false;
    const label = (el) => el.querySelector(":scope > .chat-step-main > .chat-step-head .chat-step-label");
    const result = (el) => el.querySelector(":scope > .chat-step-main .chat-console-result-text");
    const currentLabel = label(row);
    const nextLabel = label(freshRow);
    const currentResult = result(row);
    const nextResult = result(freshRow);
    if (!currentLabel || !nextLabel || currentLabel.textContent !== nextLabel.textContent) return false;
    if (Boolean(currentResult) !== Boolean(nextResult)) return false;
    if (row.dataset.tool !== freshRow.dataset.tool) return false;
    if ((row.querySelector(":scope > .chat-step-main > .chat-step-head [data-step-toggle]")?.getAttribute("aria-expanded") || "") !== (freshRow.querySelector(":scope > .chat-step-main > .chat-step-head [data-step-toggle]")?.getAttribute("aria-expanded") || "")) return false;
    const copyText = (from, to) => {
      if (!from || !to || from.textContent === to.textContent) return;
      to.textContent = from.textContent;
    };
    copyText(nextResult, currentResult);
    const state = row.querySelector(":scope > .chat-step-main > .chat-step-head .chat-step-state");
    const nextState = freshRow.querySelector(":scope > .chat-step-main > .chat-step-head .chat-step-state");
    if (state && nextState && state.textContent !== nextState.textContent) state.textContent = nextState.textContent;
    // Settled executions can still receive updated detailed output.
    patchMarkdown(row, freshRow.innerHTML);
    return true;
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

  function applySearchCounts(timeline, groups) {
    if (!timeline._siteCounts) timeline._siteCounts = {};
    groups.forEach((group, groupIndex) => {
      if (group.name !== "web_search" && group.name !== "search") return;
      const key = `${groupIndex}:${group.name}`;
      const n = searchSiteCount(group);
      const prev = timeline._siteCounts[key];
      timeline._siteCounts[key] = n;
      const stepKey = group.items.length === 1 ? String(group.items[0].index) : `group-${groupIndex}`;
      const step = timeline.querySelector(`[data-step="${stepKey}"]`);
      const el = step && step.querySelector("[data-search-count]");
      if (!el) return;
      if (Number(el.textContent) !== n) {
        el.textContent = n;
        const noun = step.querySelector("[data-search-noun]");
        if (noun) noun.textContent = n === 1 ? "web query" : "web queries";
      }
      if (prev != null && prev !== n) {
        el.classList.remove("is-bump");
        void el.offsetWidth;
        el.classList.add("is-bump");
      }
    });
  }

  function lastCurrentTrace(groups) {
    const items = groups.flatMap((g) => g.items);
    for (let i = items.length - 1; i >= 0; i--) {
      if (toolIsCurrent(items[i].trace)) return items[i].trace;
    }
    return null;
  }

  const ACTIVITY_VERBS = [
    "Compiling", "Interpreting", "Parsing", "Tracing", "Type-checking", "Linting",
    "Debugging", "Profiling", "Benchmarking", "Optimizing", "Vectorizing", "Parallelizing",
    "Refactoring", "Memoizing", "Serializing", "Marshalling", "Normalizing", "Deduplicating",
    "Indexing", "Hashing", "Caching", "Diffing", "Merging", "Rebasing",
    "Resolving", "Inferring", "Traversing", "Bootstrapping", "Scaffolding", "Provisioning",
    "Architecting", "Prototyping", "Contemplating", "Synthesizing", "Grokking", "Extrapolating",
  ];
  let verbPick = "";
  let verbAt = 0;
  function activityVerb() {
    const now = Date.now();
    if (!verbPick || now - verbAt >= 2400) {
      let next = verbPick;
      while (ACTIVITY_VERBS.length > 1 && next === verbPick) {
        next = ACTIVITY_VERBS[Math.floor(Math.random() * ACTIVITY_VERBS.length)];
      }
      verbPick = next;
      verbAt = now;
    }
    return verbPick;
  }

  function setLiveLabelText(el, text) {
    if (!el || el.textContent === text) return;
    el.textContent = text;
    if (isConsoleView()) {
      el.classList.remove("is-swap");
      return;
    }
    el.classList.remove("is-swap");
    void el.offsetWidth;
    el.classList.add("is-swap");
  }

  function currentActivity(msg, groups, live) {
    const traces = Array.isArray(msg && msg.tools) ? msg.tools : [];
    const grouped = groups || groupTraces(traces);
    const owner = msg?.id === activeRun?.message_id ? activeRun
      : typeof agentRun !== "undefined" && msg?.id === agentRun?.message_id ? agentRun : null;
    const stopping = Boolean(live && owner && runIsStopping(owner));
    let current = live && !stopping ? lastCurrentTrace(grouped) : null;
    const activity = msg?.id === activeRun?.message_id ? activeRun?.activity : msg?._activity;
    if (activity?.phase === "model") current = null;
    else if (live && !stopping && activity?.phase === "tools" && activity.tool_id) {
      current = traces.find(trace => trace.id === activity.tool_id && toolIsCurrent(trace)) || current;
    }
    const segments = assistantSegments(msg);
    const last = [...segments].reverse().find(part => part && (part.type === "tool" || String(part.text || "").trim()));
    // Ordered text after a tool is newer activity, even if that tool's child
    // is still alive or its terminal checkpoint has not arrived yet.
    if (current && !activity?.phase && Array.isArray(msg?.segments) && ["reasoning", "content"].includes(last?.type)) {
      const toolIndex = segments.findLastIndex(part => part.type === "tool" && part.id === current.id);
      if (toolIndex >= 0 && segments.lastIndexOf(last) > toolIndex) current = null;
    }
    let phase = "idle";
    let label = "";
    if (stopping) {
      phase = "stopping";
      label = "Stopping";
    } else if (live && owner?.status === "queued") {
      phase = "queued";
      label = "Queued";
    } else if (current?.status === "queued") {
      phase = "queued";
      label = isConsoleView()
        ? (isShellExecTool(current.name) ? "queued bash" : current.name === "spawn_subagent" ? "queued task" : "queued")
        : (isShellExecTool(current.name) ? "Command queued" : current.name === "spawn_subagent" ? "Subagent queued" : "Tool queued");
    } else if (current) {
      if (isSearchTool(current.name)) {
        phase = "searching";
        label = isConsoleView() ? "searching" : "Searching the web";
      } else if (current.name === "read_url") {
        phase = "reading";
        label = isConsoleView() ? "fetching" : "Reading a page";
      } else if (current.name === "create_artifact" || current.name === "update_artifact") {
        phase = "building";
        label = isConsoleView() ? "artifact" : "Building an artifact";
      } else if (current.name === "list_subagents") {
        phase = "working";
        label = isConsoleView() ? "agents" : "Listing subagents";
      } else if (current.name === "search_models") {
        phase = "searching";
        label = isConsoleView() ? "models" : "Finding models";
      } else if (current.name === "get_command_output") {
        phase = "working";
        label = isConsoleView() ? "awaiting bash" : "Waiting for command output";
      } else if (current.name === "kill_command") {
        phase = "working";
        label = isConsoleView() ? "killing" : "Stopping a command";
      } else if (current.name === "kill_subagent") {
        phase = "working";
        label = isConsoleView() ? "killing task" : "Stopping a subagent";
      } else if (current.name === "spawn_subagent") {
        phase = "spawning";
        label = isConsoleView() ? "task" : "Running a subagent";
      } else if (current.name === "get_subagent_output") {
        phase = "waiting";
        label = isConsoleView() ? "awaiting task" : "Waiting for a subagent";
      } else if (current.name === "run_terminal_command") {
        phase = "working";
        label = isConsoleView() ? "bash" : "Running a command";
      } else if (current.name === "monitor") {
        phase = "working";
        label = isConsoleView() ? "watch" : "Watching for events";
      } else {
        phase = "working";
        label = isConsoleView() ? consoleVerb(current.name, current).toLowerCase() : toolRowLabel(current);
      }
    } else if (live && activity?.phase === "tools") {
      phase = "working";
      label = isConsoleView() ? "working" : "Working";
    } else if (live) {
      if (last && last.type === "reasoning") {
        phase = "thinking";
        label = isConsoleView() ? "thinking" : "Thinking";
      } else if (last && last.type === "content") {
        phase = "writing";
        label = isConsoleView() ? "writing" : "Writing";
      } else if (last && last.type === "tool") {
        phase = activity?.phase === "model" ? "waiting" : "starting";
        label = activity?.phase === "model" ? (isConsoleView() ? "waiting" : "Waiting for model") : (isConsoleView() ? "working" : "Working");
      } else if (msg && msg.reasoning && !msg.content) {
        phase = "thinking";
        label = isConsoleView() ? "thinking" : "Thinking";
      } else if (msg && msg.content) {
        phase = "writing";
        label = isConsoleView() ? "writing" : "Writing";
      } else {
        phase = "waiting";
        label = isConsoleView() ? "waiting" : "Waiting for model";
      }
    } else if (msg && msg.stopped) {
      phase = "stopped";
      label = "Stopped";
    } else if (msg && msg.error) {
      phase = "error";
      label = "Failed";
    } else {
      const items = grouped.flatMap((g) => g.items);
      const total = items.length;
      const counts = toolCounts(items, live || Boolean(msg?.tools_live));
      phase = "done";
      if (!total) label = msg && msg.reasoning ? "Thought" : "Worked";
      else if (counts.done === total) label = `Completed ${total} ${total === 1 ? "action" : "actions"}`;
      else label = `${total} ${total === 1 ? "action" : "actions"} · ${countsLabel(counts)}`;
    }
    if (live && !stopping) {
      const executions = new Map();
      for (const trace of traces) {
        if (!toolPresentation(trace, true).running) continue;
        const key = executionIdentity(trace) || trace.id;
        // A foreground join must not erase its child's background identity.
        executions.set(key, { background: toolBackground(trace) || Boolean(executions.get(key)?.background) });
      }
      if (executions.size > 1) {
        const background = [...executions.values()].filter(toolBackground).length;
        label += ` · ${executions.size} active${background ? ` / ${background} background` : ""}`;
      }
    }
    return { phase, label, current, live: Boolean(live && !stopping), busy: Boolean(live) };
  }

  function activityKind(msg, groups, live) {
    if (!live) return "";
    const status = currentActivity(msg, groups, live);
    if (status.phase === "searching") return "searching";
    if (status.phase === "reading") return "reading";
    if (status.phase === "building") return "building";
    if (status.phase === "writing") return "writing";
    if (status.phase === "thinking") return "thinking";
    if (status.phase === "starting" || status.phase === "stopping") return "waiting";
    if (status.current) return "working";
    return "waiting";
  }

  function liveStatusLabel(msg, groups) {
    return currentActivity(msg, groups, true).label;
  }

  function liveDockMotionMs() {
    return window.matchMedia("(prefers-reduced-motion: reduce)").matches ? 0 : 260;
  }

  function followLiveDockSlide() {
    if (!thread || !stickToBottom) return;
    if (liveDockFollowFrame) cancelAnimationFrame(liveDockFollowFrame);
    const until = performance.now() + liveDockMotionMs();
    const step = (now) => {
      if (stickToBottom && thread) scrollThread();
      if (now < until) liveDockFollowFrame = requestAnimationFrame(step);
      else liveDockFollowFrame = 0;
    };
    liveDockFollowFrame = requestAnimationFrame(step);
  }

  function setLiveDockSpace() {
    syncDockSpace();
  }

  function hideLiveDock() {
    if (!liveDock) return;
    liveDock.hidden = true;
    liveDock.setAttribute("aria-hidden", "true");
    if (liveDockSpin) {
      stopConsoleSpin();
      liveDockSpin.replaceChildren();
      delete liveDockSpin.dataset.spinSet;
    }
  }

  const CONSOLE_SPIN_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"];
  let consoleSpinTimer = 0;
  function stopConsoleSpin() {
    if (consoleSpinTimer) {
      window.clearInterval(consoleSpinTimer);
      consoleSpinTimer = 0;
    }
  }
  function setConsoleSpin(el) {
    if (!el) return;
    let mark = el.querySelector(".chat-console-spin");
    if (el.dataset.spinSet !== "console-braille" || !mark) {
      el.dataset.spinSet = "console-braille";
      el.replaceChildren();
      mark = document.createElement("span");
      mark.className = "chat-console-spin";
      mark.setAttribute("aria-hidden", "true");
      mark.dataset.frame = "0";
      mark.textContent = CONSOLE_SPIN_FRAMES[0];
      el.appendChild(mark);
    }
    if (consoleSpinTimer) return;
    consoleSpinTimer = window.setInterval(() => {
      const spin = liveDockSpin && liveDockSpin.querySelector(".chat-console-spin");
      if (!spin || liveDock.hidden) {
        stopConsoleSpin();
        return;
      }
      const next = (Number(spin.dataset.frame || 0) + 1) % CONSOLE_SPIN_FRAMES.length;
      spin.dataset.frame = String(next);
      spin.textContent = CONSOLE_SPIN_FRAMES[next];
    }, 200);
  }

  function syncLiveDock() {
    if (!liveDock) return;
    const live = Boolean(streaming || (runIsActive(activeRun) && runIsStopping(activeRun)));
    const msg = live ? activeAssistant() : null;
    const on = Boolean(live && msg && msg.role === "assistant");
    if (on) {
      if (liveDockHideTimer) {
        clearTimeout(liveDockHideTimer);
        liveDockHideTimer = 0;
      }
      liveDock.hidden = false;
      liveDock.setAttribute("aria-hidden", "false");
      const groups = groupTraces(Array.isArray(msg.tools) ? msg.tools : []);
      const status = currentActivity(msg, groups, true);
      setLiveLabelText(liveDockLabel, isConsoleView() ? formatConsoleLiveStatus(msg, groups) : status.label);
      if (liveDockSpin && isConsoleView()) setConsoleSpin(liveDockSpin);
      else if (liveDockSpin) applySpinEl(liveDockSpin, activitySpinSet(msg), "dock", true, msg._spinOrigin);
      const wasOn = root.classList.contains("is-live-dock");
      if (!wasOn && !liveDockShowFrame) {
        liveDockShowFrame = requestAnimationFrame(() => {
          liveDockShowFrame = 0;
           if (!runIsActive(activeRun)) return;
          setLiveDockSpace();
          root.classList.add("is-live-dock");
          syncDockSpace();
          followLiveDockSlide();
        });
      } else if (wasOn) {
        setLiveDockSpace();
      }
      return;
    }
    if (!root.classList.contains("is-live-dock") && liveDock.hidden) return;
    if (liveDockHideTimer) return;
    root.classList.remove("is-live-dock");
    syncDockSpace();
    followLiveDockSlide();
    liveDock.setAttribute("aria-hidden", "true");
    const wait = liveDockMotionMs();
    if (!wait) {
      hideLiveDock();
      return;
    }
    liveDockHideTimer = setTimeout(() => {
      liveDockHideTimer = 0;
      if (!runIsActive(activeRun) || !activeAssistant()) hideLiveDock();
    }, wait);
  }

  function activityLabel(msg, groups, live) {
    return currentActivity(msg, groups, live).label;
  }

  function liveElapsedSeconds(msg, live) {
    if (!live) {
      delete msg._liveOrigin;
      return msg.elapsedMs != null ? msg.elapsedMs / 1000 : null;
    }
    if (!msg._liveOrigin || msg._elapsedObserved !== msg.elapsedMs) {
      msg._elapsedObserved = msg.elapsedMs;
      const elapsed = Number(msg.elapsedMs);
      if (Number.isFinite(elapsed) && elapsed >= 0) msg._liveOrigin = Date.now() - elapsed;
      else {
        const started = Date.parse((activeRun && activeRun.created_at) || "");
        msg._liveOrigin = Number.isFinite(started) ? started : Date.now();
      }
    }
    return Math.max(0, (Date.now() - msg._liveOrigin) / 1000);
  }

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
    if (status.current && toolPresentation(status.current, true).running) return "up";
    const traces = Array.isArray(msg && msg.tools) ? msg.tools : [];
    if ((status.phase === "queued" || status.phase === "waiting") && traces.length) return "up";
    return "down";
  }

  function consoleRoundKey(msg, status) {
    const traces = Array.isArray(msg && msg.tools) ? msg.tools : [];
    return `${consoleFlow(status, msg)}|${traces.length}`;
  }

  function ensureConsoleRound(msg, status) {
    const key = consoleRoundKey(msg, status);
    if (msg._consoleRound && msg._consoleRound.key === key) return msg._consoleRound;
    const split = usageInOut(msg && msg.usage);
    const prev = msg._consoleRound;
    const flow = consoleFlow(status, msg);
    // After a tool round the first new tokens are already on the message
    // when we notice the switch. Keep the origin snapshotted when tools began.
    const keepOrigin = prev && flow === "down" && String(prev.key).startsWith("up|");
    if (prev) delete msg.streamOut;
    const meterUsed = liveMeter && typeof liveMeter.used === "number" ? Math.max(0, Math.round(Number(liveMeter.used) || 0)) : 0;
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
    if (tokens >= 1_000_000) return (tokens / 1_000_000).toFixed(tokens >= 10_000_000 ? 0 : 1).replace(/\.0$/, "") + "M";
    if (tokens >= 10_000) return Math.round(tokens / 1000) + "k";
    if (tokens >= 1000) return (tokens / 1000).toFixed(1).replace(/\.0$/, "") + "k";
    return String(tokens);
  }

  function consoleDownTokens(msg, round) {
    const grown = estimateChars(
      String((msg && msg.content) || "").slice(round.content)
      + String((msg && msg.reasoning) || "").slice(round.reasoning)
    );
    const fromServer = Math.max(0, Math.round(Number(msg && msg.streamOut) || 0));
    let down = Math.max(grown, fromServer);
    const split = usageInOut(msg && msg.usage);
    if (split && split.out > round.usageOut) down = Math.max(down, split.out - round.usageOut);
    return down;
  }

  function consoleUpTokens(msg, round) {
    const fromMeter = liveMeter && typeof liveMeter.used === "number" ? Math.max(0, Math.round(Number(liveMeter.used) || 0)) : 0;
    if (fromMeter && round && fromMeter > (round.meterUsed || 0)) return fromMeter - (round.meterUsed || 0);
    const traces = Array.isArray(msg && msg.tools) ? msg.tools : [];
    const last = traces[traces.length - 1];
    return last ? estimateTrace(last) : 0;
  }

  function formatConsoleLiveStatus(msg, groups) {
    const status = currentActivity(msg, groups, true);
    const verb = String(status.label || "working").replace(/\.+$/, "");
    const seconds = Math.max(0, Math.floor(liveElapsedSeconds(msg, true) || 0));
    const round = ensureConsoleRound(msg, status);
    const flow = consoleFlow(status, msg);
    let tokenText = "";
    if (flow === "down") {
      const n = consoleDownTokens(msg, round);
      if (n) tokenText = `↓ ${formatConsoleCount(n)}`;
    } else if (flow === "up") {
      const n = consoleUpTokens(msg, round);
      if (n) tokenText = `↑ ${formatConsoleCount(n)}`;
    }
    return tokenText ? `${verb}.. (${seconds}s · ${tokenText})` : `${verb}.. (${seconds}s)`;
  }

  function syncActivityElapsed(article, msg, live) {
    const elapsedEls = article.querySelectorAll("[data-activity-elapsed]");
    if (!elapsedEls.length) return;
    const text = formatActivityElapsed(liveElapsedSeconds(msg, live), live);
    elapsedEls.forEach((el) => {
      if (el.closest("[data-live-status]")) {
        el.textContent = text;
        return;
      }
      el.textContent = el.closest("[data-activity]")?.classList.contains("is-waiting") ? "" : text;
    });
  }

  function activitySpinSet() {
    return currentRunSpin();
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
    applySpinEl(el, activitySpinSet(msg), "live", true, msg._spinOrigin);
  }

  function stopLiveClock() {
    if (liveClockTimer) {
      window.clearInterval(liveClockTimer);
      liveClockTimer = 0;
    }
  }

  function startLiveClock() {
    if (liveClockTimer) return;
    liveClockTimer = window.setInterval(() => {
      if (!streaming) {
        stopLiveClock();
        return;
      }
      const msg = activeAssistant();
      const article = msg ? log.children[messages.indexOf(msg)] : null;
      if (!msg || msg.role !== "assistant" || !article || !article.classList.contains("is-assistant")) return;
      syncActivityElapsed(article, msg, true);
      syncActivitySpin(article, msg, true);
      syncLiveDock();
    }, 250);
  }

  function thinkPreview(text) {
    return String(text || "").replace(/\s+/g, " ").trim();
  }

  function thinkingSummaryHtml(text) {
    const line = String(text || "").split("\n").filter(line => line.trim() && !/^\s*```/.test(line))
      .slice(0, 3).map(line => line.replace(/^\s*(?:#{1,6}\s+|>\s*|[-*+]\s+|\d+[.)]\s+)/, "")).join(" ").slice(0, 1200);
    // The collapsed summary lives inside a button; links remain styled text.
    return renderInline(line).replace(/<a\b[^>]*>/g, "<span>").replace(/<\/a>/g, "</span>");
  }

  function thinkingPosition(peek) {
    const state = peek?._thinking;
    if (state?.frame && state.initialized && Math.abs(peek.scrollTop - state.lastTop) > 2) stopThinkingFollow(peek);
    return { top: peek?.scrollTop || 0,
      pinned: !peek?.clientHeight ? state?.follow !== false : state?.frame ? state.follow : peek.scrollHeight - peek.clientHeight - peek.scrollTop <= 3,
      initialized: Boolean(state?.initialized) };
  }

  function stopThinkingFollow(peek) {
    if (!peek?._thinking) return;
    const state = peek._thinking;
    state.follow = false;
    state.snap = false;
    if (state.frame) cancelAnimationFrame(state.frame);
    state.frame = 0;
  }

  function followThinking(peek, snap = false) {
    const state = peek._thinking;
    if (!state || !state.follow) return;
    state.snap ||= snap;
    if (state.frame) return;
    state.lastTop = peek.scrollTop;
    let previous = performance.now();
    const step = now => {
      if (!peek.isConnected || !state.follow) { state.frame = 0; return; }
      // Initial render happens in a detached article. Measure only now, after
      // attachment and the live/expanded CSS have established the viewport.
      if (!peek.clientHeight) { state.frame = 0; return; }
      const target = Math.max(0, peek.scrollHeight - peek.clientHeight);
      const gap = target - peek.scrollTop;
      if (state.snap || reduceMotion.matches || Math.abs(gap) <= 1) {
        peek.scrollTop = target;
        state.lastTop = peek.scrollTop;
        state.initialized = true; state.snap = false; state.frame = 0;
        return;
      }
      const dt = Math.min(48, Math.max(8, now - previous)); previous = now;
      peek.scrollTop += Math.sign(gap) * Math.max(1, Math.abs(gap) * (1 - Math.exp(-dt / 42)));
      state.lastTop = peek.scrollTop;
      state.frame = requestAnimationFrame(step);
    };
    state.frame = requestAnimationFrame(step);
  }

  function initThinking(peek) {
    if (peek._thinking) return;
    const state = peek._thinking = { follow: true, initialized: false, frame: 0, snap: false, touchY: null, lastTop: peek.scrollTop };
    peek.addEventListener("wheel", event => { if (event.deltaY < 0) stopThinkingFollow(peek); }, { passive: true });
    peek.addEventListener("touchstart", event => { state.touchY = event.touches[0]?.clientY ?? null; }, { passive: true });
    peek.addEventListener("touchmove", event => {
      const y = event.touches[0]?.clientY;
      if (y != null && state.touchY != null && y > state.touchY) stopThinkingFollow(peek);
      state.touchY = y ?? null;
    }, { passive: true });
    peek.addEventListener("keydown", event => { if (["ArrowUp", "PageUp", "Home"].includes(event.key)) stopThinkingFollow(peek); });
    peek.addEventListener("pointerdown", event => {
      if (event.target === peek && event.clientX >= peek.getBoundingClientRect().right - 16) stopThinkingFollow(peek);
    }, { passive: true });
    peek.addEventListener("scroll", () => {
      if (state.frame && state.initialized && Math.abs(peek.scrollTop - state.lastTop) > 2) stopThinkingFollow(peek);
      if (!state.frame && peek.clientHeight) {
        state.follow = peek.scrollHeight - peek.clientHeight - peek.scrollTop <= 3;
        state.lastTop = peek.scrollTop;
      }
    }, { passive: true });
    const observer = new ResizeObserver(() => {
      if (!peek.isConnected) { observer.disconnect(); stopThinkingFollow(peek); return; }
      if (state.follow) followThinking(peek, !state.initialized);
    });
    observer.observe(peek);
    observer.observe(peek.firstElementChild);
    state.observer = observer;
  }

  function disposeThinking(container) {
    container?.querySelectorAll("[data-reason-preview]").forEach(peek => {
      stopThinkingFollow(peek);
      peek._thinking?.observer?.disconnect();
    });
  }

  function syncThinkingMode(li, msg, animate = false) {
    const windowEl = li.querySelector(".chat-think-window");
    const height = animate ? windowEl.getBoundingClientRect().height : 0;
    const previousMode = li.dataset.thinkMode;
    const key = li.dataset.step;
    const choice = msg.stepOpen?.[key];
    const live = li.classList.contains("is-live-think");
    const mode = choice === true ? "expanded" : choice === false ? "collapsed" : live ? (isConsoleView() ? "expanded" : "tail") : "collapsed";
    li.dataset.thinkMode = mode;
    li.querySelectorAll(":scope > .chat-step-main > .chat-step-head [data-step-toggle]").forEach(button => button.setAttribute("aria-expanded", String(mode !== "collapsed")));
    const size = li.querySelector("[data-think-size]");
    size.hidden = !live || mode === "collapsed";
    size.textContent = isConsoleView()
      ? (mode === "expanded" ? "hide" : "stream")
      : (mode === "expanded" ? "5 lines" : "More");
    size.setAttribute("aria-label", isConsoleView()
      ? (mode === "expanded" ? "Hide thinking stream" : "Show thinking stream")
      : (mode === "expanded" ? "Show five-line thinking preview" : "Expand full thinking"));
    const peek = li.querySelector("[data-reason-preview]");
    peek.tabIndex = mode === "collapsed" ? -1 : 0;
    windowEl.inert = mode === "collapsed";
    if (animate && previousMode !== mode) setToolReveal(windowEl, mode !== "collapsed", height);
    return mode;
  }

  function thinkStepHtml(text, key, open) {
    const preview = String(text || "").trim();
    return (
      `<li class="chat-step is-think" data-think-mode="${open ? "expanded" : "collapsed"}" data-step="${escapeHtml(key)}">` +
      `<span class="chat-step-node" aria-hidden="true">${STEP_ICONS.think}</span>` +
      '<div class="chat-step-main">' +
      `<div class="chat-step-head">` +
      `<button type="button" class="chat-step-toggle" data-step-toggle aria-expanded="${open ? "true" : "false"}">` +
      `<span class="chat-step-copy"><span class="chat-step-kind">${thinkLabel()}</span>` +
       `<span class="chat-step-label chat-think-summary">${thinkingSummaryHtml(preview)}</span></span>` +
      `</button>` +
      `<span class="chat-step-state is-empty" aria-hidden="true"></span>` +
      `<button type="button" class="chat-think-size" data-think-size hidden>More</button>` +
      `<button type="button" class="chat-step-caret" data-step-toggle aria-expanded="${open ? "true" : "false"}" aria-label="Toggle details">${STEP_CHEVRON}</button>` +
      `</div><div class="chat-think-window"><div class="chat-think-preview" data-reason-preview="${escapeHtml(key.slice(6))}" role="region" aria-label="Thinking" tabindex="${open ? "0" : "-1"}"><div class="chat-think-markdown"></div></div></div>` +
      "</div></li>"
    );
  }

  const WORKSPACE_VERBS = {
    read_file: "Read",
    list_dir: "Listed",
    grep: "Grep",
    glob: "Glob",
    write_file: "Wrote",
    search_replace: "Edited",
    run_terminal_command: "Shell",
    get_command_output: "Job",
    kill_command: "Kill",
    monitor: "Watch",
    search_tools: "Tools",
    load_skill: "Skill",
    propose_skill: "Skill draft",
    todo_write: "Todos",
    spawn_subagent: "Subagent",
    get_subagent_output: "Await task",
    kill_subagent: "Subagent",
    list_subagents: "Agents",
    search_models: "Models",
  };

  function isWorkspaceTool(name) {
    return Boolean(WORKSPACE_VERBS[name]);
  }

  function isSearchTool(name) {
    return name === "web_search" || name === "search";
  }

  const TOOL_ICON = {
    read_file: "file",
    list_dir: "folder",
    grep: "grep",
    glob: "glob",
    write_file: "write",
    search_replace: "edit",
    run_terminal_command: "shell",
    get_command_output: "shell",
    kill_command: "stop",
    monitor: "watch",
    search_tools: "tools",
    load_skill: "skill",
    todo_write: "edit",
    spawn_subagent: "agent",
    get_subagent_output: "await",
    kill_subagent: "stop",
    list_subagents: "agent",
    search_models: "search",
    read_url: "read",
    web_search: "search",
    search: "search",
  };

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

  const STACK = {
    read_file: { family: "read", verb: "Read", verbing: "Reading", one: "file", many: "files" },
    list_dir: { family: "list", verb: "Listed", verbing: "Listing", one: "directory", many: "directories" },
    grep: { family: "grep", verb: "Grepped", verbing: "Grepping", one: "pattern", many: "patterns" },
    glob: { family: "glob", verb: "Globbed", verbing: "Globbing", one: "pattern", many: "patterns" },
    write_file: { family: "write", verb: "Wrote", verbing: "Writing", one: "file", many: "files" },
    search_replace: { family: "edit", verb: "Edited", verbing: "Editing", one: "file", many: "files" },
  };

  function stackSpec(name) {
    return STACK[name] || null;
  }

  function isStackableWorkspace(name) {
    return Boolean(stackSpec(name));
  }

  function stackKey(name) {
    const spec = stackSpec(name);
    if (spec) return "stack:" + spec.family;
    if (isSearchTool(name)) return "search";
    if (name === "read_url") return "page";
    if (name === "run_terminal_command") return "shell";
    if (name === "monitor") return "monitor";
    if (name === "get_command_output") return "job";
    if (name === "spawn_subagent") return "spawn";
    if (name === "get_subagent_output") return "wait";
    if (name === "list_subagents") return "agents";
    if (name === "search_models") return "models";
    return "one:" + (name || "unknown");
  }

  function stackChildLabel(trace) {
    if (backgroundCommandLabel(trace)) return backgroundCommandLabel(trace);
    const name = trace && trace.name;
    const path = String((trace && trace.path) || "").trim();
    const shortPath = shortToolPath(path, trace);
    const pattern = String((trace && (trace.pattern || trace.query)) || "").trim();
    if (name === "grep") {
      const q = pattern ? `“${toolText(pattern)}”` : "";
      if (q && shortPath) return `${q} in ${shortPath}`;
      return q || shortPath || "Grep";
    }
    if (name === "glob") return toolText(pattern || shortPath) || "Glob";
    if (name === "list_dir") return shortPath || "/";
    if (name === "run_terminal_command") {
      const detail = (isHarnessView() ? String(trace.command || "").trim() : toolText(trace.command || "")) || "command";
      const exit = shellExitCode(trace);
      return exit !== null && exit !== 0 ? `${detail} (exit ${exit})` : detail;
    }
    if (name === "get_command_output") {
      const detail = (isHarnessView() ? String(trace.command || "").trim() : toolText(trace.command || "")) || toolText(trace.preview || "command output");
      const exit = shellExitCode(trace);
      return exit !== null && exit !== 0 ? `${detail} (exit ${exit})` : detail;
    }
    if (name === "monitor") {
      const target = toolText((trace && (trace.url || trace.command)) || "") || "watch";
      const n = Number(trace && trace.event_count);
      return Number.isFinite(n) && n > 0 ? `${target} · ${n}` : target;
    }
    if (name === "spawn_subagent") return agentSubject(trace) || (trace.status === "error" ? "failed" : "subagent");
    if (name === "get_subagent_output") {
      if (String((trace && trace.preview) || "") === "still running") return "still running";
      return agentSubject(trace) || (trace.status === "error" ? "failed" : "result");
    }
    if (name === "list_subagents") return agentListLine(trace);
    return toolPathLeaf(path) || activityLine(trace);
  }

  function toolIcon(name, failed, interrupted) {
    if (failed) return STEP_ICONS.error;
    if (interrupted) return STEP_ICONS.stop;
    return STEP_ICONS[TOOL_ICON[name] || "tool"];
  }

  function toolPathLeaf(path) {
    const p = String(path || "").replace(/\/+$/, "");
    if (!p || p === "/") return p || "/";
    const parts = p.split("/");
    return parts[parts.length - 1] || p;
  }

  function shortToolPath(path, trace) {
    const raw = String(path || "").trim();
    if (!raw) return raw;
    const windows = trace?.platform === "windows" || /^[a-z]:[\\/]|^\\\\/i.test(raw);
    const normalized = windows ? raw.replaceAll("\\", "/") : raw;
    if (!normalized.startsWith("/") && !/^[a-z]:\//i.test(normalized)) return raw;
    const bound = sessionProject();
    // Never shorten a different machine's path against this chat's project.
    const sameHost = !trace?.host || trace.host === bound?.host_alias;
    const anchor = String(trace?.project_root || trace?.cwd || (sameHost && bound?.remote_path) || "").trim();
    const base = (windows ? anchor.replaceAll("\\", "/") : anchor).replace(/\/+$/, "");
    const key = windows ? normalized.toLowerCase() : normalized;
    const rootKey = windows ? base.toLowerCase() : base;
    if (base && (key === rootKey || key.startsWith(rootKey + "/"))) {
      if (key === rootKey) return ".";
      return normalized.slice(base.length + 1);
    }
    return raw;
  }

  function compactPath(path, trace, cap = 58) {
    const value = shortToolPath(path, trace);
    if (value.length <= cap) return value;
    const parts = value.replaceAll("\\", "/").split("/");
    const leaf = parts.pop() || "";
    const prefix = parts[0] === "" ? (parts[1] || "") ? "/" + parts[1] : "/" : parts[0];
    let tail = leaf;
    while (parts.length > 1 && (prefix + "/…/" + parts.at(-1) + "/" + tail).length <= cap) tail = parts.pop() + "/" + tail;
    return (prefix ? prefix + "/…/" : "…/") + tail;
  }

  function displayCommand(trace) {
    const raw = String(trace?.command || "").trim();
    if (trace?.platform === "windows" || /^[a-z]:[\\/]/i.test(trace?.cwd || "")) return raw;
    // Display-only, literal POSIX prefix. No expansions, semicolons or shell parsing guesses.
    const match = raw.match(/^cd\s+(?:--\s+)?(?:'([^']*)'|"([^"$`\\]*)"|([^\s;&|<>$`\\"']+))\s*&&\s*([\s\S]+)$/);
    if (!match) return raw;
    const target = match[1] ?? match[2] ?? match[3];
    const cwd = String(trace?.cwd || "").replace(/\/+$/, "");
    return target.startsWith("/") && cwd && target.replace(/\/+$/, "") === cwd ? match[4] : raw;
  }

  function toolSubject(trace) {
    const name = trace.name;
    if (name === "monitor") return toolText(trace.url || displayCommand(trace) || executionIdentity(trace) || "watch");
    if (isShellExecTool(name)) return toolText(displayCommand(trace) || executionIdentity(trace) || "command");
    if (["read_file", "write_file", "search_replace", "list_dir"].includes(name)) {
      const range = name === "read_file" && Number.isFinite(trace.offset) ? ` :${trace.offset}${Number.isFinite(trace.limit) ? `–${trace.offset + trace.limit - 1}` : ""}` : "";
      return compactPath(trace.path || ".", trace) + range;
    }
    if (["grep", "glob"].includes(name)) return [toolText(trace.pattern || trace.query || "pattern", 90), trace.path ? `in ${compactPath(trace.path, trace, 36)}` : ""].filter(Boolean).join(" ");
    if (["spawn_subagent", "get_subagent_output", "kill_subagent"].includes(name)) {
      const label = toolText(trace.description || executionIdentity(trace) || "subagent", 120);
      return name === "spawn_subagent" && trace.model ? `${label} · ${toolText(trace.model, 40)}` : label;
    }
    if (name === "read_url") { try { const u = new URL(trace.url); return u.hostname.replace(/^www\./, "") + (u.pathname === "/" ? "" : compactPath(u.pathname, {}, 45)); } catch { return trace.url || "page"; } }
    if (isSearchTool(name)) return searchQueries(trace).length === 1 ? toolText(searchQueries(trace)[0], 100) : `${searchCountFor(trace)} queries`;
    if (name === "create_artifact" || name === "update_artifact") return trace.title || "artifact";
    if (name === "load_skill") return [trace.preview || "skill", trace.path ? compactPath(trace.path, trace) : ""].filter(Boolean).join(" / ");
    if (name === "fuse-steer") return trace.summary || "New instruction";
    return toolText(trace.query || trace.description || trace.title || trace.preview || trace.path || "", 140);
  }

  function toolAction(trace) {
    const labels = { read_file: "Read", list_dir: "List", grep: "Grep", glob: "Glob", write_file: "Write", search_replace: "Edit",
      run_terminal_command: "Shell", get_command_output: "Output", kill_command: "Stop", monitor: "Watch", spawn_subagent: "Agent",
      get_subagent_output: "Result", kill_subagent: "Stop", list_subagents: "Agents", search_models: "Models", search_tools: "Tools",
      load_skill: "Skill", propose_skill: "Draft", todo_write: "Tasks", read_url: "Page", web_search: "Search", search: "Search",
      create_artifact: "Create", update_artifact: "Update", "fuse-steer": "Notice" };
    return labels[trace.name] || String(trace.name || "Tool").replaceAll("_", " ");
  }

  function toolLabelHtml(trace) {
    const bound = sessionProject();
    const host = trace.host && (!bound || trace.host !== bound.host_alias) ? trace.host : "";
    const file = ["read_file", "write_file", "search_replace", "list_dir"].includes(trace.name);
    const subject = toolSubject(trace);
    const slash = file ? subject.replaceAll("\\", "/").lastIndexOf("/") : -1;
    const subjectHtml = slash >= 0 ? `<span class="chat-path-parent">${escapeHtml(subject.slice(0, slash + 1))}</span><span class="chat-path-leaf">${escapeHtml(subject.slice(slash + 1))}</span>` : escapeHtml(subject);
    if (isConsoleView()) return consoleCallHtml(trace);
    const verb = isCliView() ? cliVerb(trace.name, trace) : toolAction(trace);
    const prompt = (trace.name === "run_terminal_command" || (trace.name === "monitor" && !trace.url)) && isCliView()
      ? '<span class="chat-command-prompt">$ </span>'
      : "";
    return `<span class="chat-tool-action">${escapeHtml(verb)}</span>` +
      `<span class="chat-tool-subject${file ? " is-path" : ""}">${prompt}${subjectHtml}</span>` +
      (host ? `<span class="chat-tool-host" title="Host ${escapeHtml(host)}">${escapeHtml(host)}</span>` : "");
  }

  function pageHost(url) {
    try {
      return url ? new URL(url).hostname.replace(/^www\./, "") : "";
    } catch (_) {
      return "";
    }
  }

  function stripPreview(raw, max) {
    const text = String(raw || "")
      .replace(/```[\s\S]*?```/g, " ")
      .replace(/`+/g, "")
      .replace(/[*_>#~\-]+/g, " ")
      .replace(/\s+/g, " ")
      .trim();
    if (!text || text === "still running") return "";
    return shortToolText(text, max || 80);
  }

  function agentSubject(trace) {
    const description = stripPreview(trace && trace.description, 60);
    if (description) return description;
    if (trace && trace.status === "error") return "";
    return stripPreview(trace && trace.preview, 80);
  }

  function agentListLine(trace) {
    const preview = String((trace && trace.preview) || "").replace(/\s+/g, " ").trim();
    return preview || "agents";
  }

  function countPhrase(n, one, many) {
    return `${n} ${n === 1 ? one : many}`;
  }

  function toolText(raw, max) {
    const text = String(raw || "").replace(/\s+/g, " ").trim();
    const cap = max || 4000;
    if (text.length <= cap) return text;
    return text.slice(0, cap - 1) + "…";
  }

  function shortToolText(raw, max) {
    return toolText(raw, max || 72);
  }

  function toolTarget(trace) {
    return toolText(
      (trace && (trace.preview || trace.description || trace.query || trace.title || trace.path || trace.command)) || ""
    );
  }

  function isCliView() {
    return chatView === "cli";
  }

  function isConsoleView() {
    return chatView === "console";
  }

  function isHarnessView() {
    return isCliView() || isConsoleView();
  }

  function consoleArgText(value, cap = 90) {
    return toolText(String(value || "").trim(), cap);
  }

  function consoleCallHtml(trace, presentation) {
    const name = trace && trace.name;
    const running = Boolean(presentation && presentation.running);
    const verb = consoleVerb(name, trace);
    if (name === "kill_command" || name === "kill_subagent") {
      return `<span class="chat-console-call"><span class="chat-tool-action">${escapeHtml(verb)}</span></span>`;
    }
    if (name === "run_terminal_command" && running && !toolBackground(trace)) {
      const label = String((trace && trace.progressLabel) || "").trim() || "Running";
      return `<span class="chat-console-call"><span class="chat-tool-action">${escapeHtml(label)}</span></span>`;
    }
    let inner = "";
    if (["read_file", "write_file", "search_replace", "list_dir"].includes(name)) {
      inner = consoleArgText(compactPath(trace.path || ".", trace, 88), 88);
    } else if (name === "run_terminal_command" || name === "get_command_output") {
      inner = consoleArgText(displayCommand(trace) || "command", 140);
    } else if (name === "monitor") {
      inner = consoleArgText(trace.url || displayCommand(trace) || "watch", 100);
    } else if (name === "grep" || name === "glob") {
      inner = consoleArgText(trace.pattern || trace.query || "pattern", 90);
    } else if (name === "read_url") {
      inner = consoleArgText(trace.url || "page", 100);
    } else if (isSearchTool(name)) {
      const queries = searchQueries(trace);
      const q = queries.length === 1 ? queries[0] : `${queries.length} queries`;
      inner = q ? `"${consoleArgText(q, 88)}"` : "";
    } else if (["spawn_subagent", "get_subagent_output"].includes(name)) {
      inner = consoleArgText(agentSubject(trace) || "subagent", 90);
    } else if (name === "create_artifact" || name === "update_artifact") {
      inner = consoleArgText(trace.title || "artifact", 80);
    } else if (name === "load_skill") {
      inner = consoleArgText(trace.preview || "skill", 80);
    } else {
      inner = consoleArgText(toolSubject(trace), 90);
    }
    const host = trace.host && trace.host !== sessionProject()?.host_alias ? trace.host : "";
    const call = inner
      ? `<span class="chat-console-call"><span class="chat-tool-action">${escapeHtml(verb)}</span><span class="chat-console-paren">(</span><span class="chat-console-val">${escapeHtml(inner)}</span><span class="chat-console-paren">)</span></span>`
      : `<span class="chat-console-call"><span class="chat-tool-action">${escapeHtml(verb)}</span></span>`;
    return call + (host ? `<span class="chat-tool-host" title="Host ${escapeHtml(host)}">${escapeHtml(host)}</span>` : "");
  }

  function consoleOutputText(trace, failed, running) {
    if (failed) return String(trace.error || trace.summary || trace.preview || "failed").trim();
    if (typeof trace.output === "string" && trace.output) return trace.output;
    const preview = [trace.preview, trace.summary].filter((value) => {
      const text = String(value || "").trim();
      return text && text !== "still running" && text !== "background task started";
    }).join("\n\n");
    if (preview) return preview;
    if (running) return "Running…";
    if (trace.command) return String(trace.command);
    return "";
  }

  function consoleMatchCount(trace) {
    const n = Number(trace && (trace.match_count ?? trace.count ?? trace.n));
    if (Number.isFinite(n) && n > 0) return n;
    const body = String((trace && (trace.output || trace.preview)) || "");
    const lines = body.split("\n").map((line) => line.trim()).filter(Boolean);
    if (lines.length > 1) return lines.length;
    const hits = body.match(/:\d+:/g);
    if (hits && hits.length) return hits.length;
    return lines.length;
  }

  function consoleLineCount(trace) {
    const preview = String((trace && (trace.preview || trace.summary)) || "");
    const named = preview.match(/(\d+)\s+lines?\b/i);
    if (named) return Number(named[1]);
    const body = typeof trace.output === "string" && trace.output
      ? trace.output
      : (looksLikeDiff(preview) ? "" : preview);
    if (!body) return 0;
    const text = String(body).replace(/\s+$/, "");
    return text ? text.split("\n").length : 0;
  }

  function consoleLangForPath(path) {
    const ext = String(path || "").split(".").pop().toLowerCase();
    const map = {
      html: "xml", htm: "xml", xml: "xml", svg: "xml",
      js: "javascript", mjs: "javascript", cjs: "javascript", jsx: "javascript",
      ts: "typescript", tsx: "typescript",
      py: "python", rb: "ruby", go: "go", rs: "rust",
      css: "css", scss: "scss", json: "json", md: "markdown",
      sh: "bash", bash: "bash", zsh: "bash", c: "c", h: "c",
      cpp: "cpp", cc: "cpp", yml: "yaml", yaml: "yaml", toml: "ini",
    };
    return map[ext] || "";
  }

  function consoleNumberedHtml(text, { limit, lang, start = 1 } = {}) {
    const lines = String(text || "").replace(/\s+$/, "").split("\n");
    if (!lines.length || (lines.length === 1 && !lines[0])) return "";
    const cap = Number.isFinite(limit) ? Math.min(limit, lines.length) : lines.length;
    const width = String(start + cap - 1).length;
    const rows = lines.slice(0, cap).map((line, i) => {
      const n = String(start + i).padStart(width, " ");
      const body = lang ? highlightCode(lang, line) : escapeHtml(line);
      return `<div class="chat-console-nline"><span class="chat-console-n">${n}</span><span class="chat-console-nbody">${body || " "}</span></div>`;
    }).join("");
    const extra = lines.length > cap
      ? `<div class="chat-console-more">… +${lines.length - cap} lines</div>`
      : "";
    return `<div class="chat-console-lines">${rows}</div>${extra}`;
  }

  function consoleDiffHtml(text, { limit } = {}) {
    const lines = String(text || "").split("\n");
    let oldN = 0;
    let newN = 0;
    const rows = [];
    lines.forEach((line) => {
      const hunk = line.match(/^@@\s+-(\d+)(?:,\d+)?\s+\+(\d+)/);
      if (hunk) {
        oldN = Number(hunk[1]);
        newN = Number(hunk[2]);
        return;
      }
      if (/^(--- |\+\+\+ |diff |index )/.test(line)) return;
      if (line.startsWith("-")) {
        rows.push({ n: oldN++, kind: "del", text: line.slice(1) });
        return;
      }
      if (line.startsWith("+")) {
        rows.push({ n: newN++, kind: "add", text: line.slice(1) });
        return;
      }
      const body = line.startsWith(" ") ? line.slice(1) : line;
      rows.push({ n: oldN, kind: "ctx", text: body });
      oldN += 1;
      newN += 1;
    });
    if (!rows.length) return "";
    const cap = Number.isFinite(limit) ? Math.min(limit, rows.length) : rows.length;
    const width = String(Math.max(...rows.map((row) => row.n), 1)).length;
    const table = rows.slice(0, cap).map((row) => {
      const mark = row.kind === "del" ? "-" : row.kind === "add" ? "+" : " ";
      return `<div class="chat-console-nline is-${row.kind}"><span class="chat-console-n">${String(row.n).padStart(width, " ")}</span><span class="chat-console-mark">${mark}</span><span class="chat-console-nbody">${escapeHtml(row.text)}</span></div>`;
    }).join("");
    const extra = rows.length > cap
      ? `<div class="chat-console-more">… +${rows.length - cap} lines</div>`
      : "";
    return `<div class="chat-console-lines is-diff">${table}</div>${extra}`;
  }

  function consoleFileBody(trace) {
    if (typeof trace.output === "string" && trace.output) return trace.output;
    const preview = String(trace.preview || "").trim();
    if (preview && preview !== "still running" && preview !== "background task started" && !looksLikeDiff(preview)) return preview;
    return "";
  }

  function consoleCollapsedPreview(trace, failed, running) {
    if (failed || running) return "";
    const name = trace && trace.name;
    if (name === "write_file") {
      const body = consoleFileBody(trace);
      if (!body) return "";
      return consoleNumberedHtml(body, { limit: 10, lang: consoleLangForPath(trace.path) });
    }
    if (name === "search_replace") {
      const diff = [trace.preview, trace.output].find((value) => looksLikeDiff(value));
      if (diff) return consoleDiffHtml(diff, { limit: 12 });
      return "";
    }
    return "";
  }

  function consoleDetailHtml(trace, failed, running) {
    if (trace && trace.name === "todo_write") {
      // Not wrapped in .chat-console-out: the list is always shown inline and
      // the step is never expandable, so the hang stays out of the band flow.
      return todoListHtml(trace.todos, { live: running });
    }
    if (trace && ["read_file", "write_file"].includes(trace.name)) {
      const body = consoleFileBody(trace);
      if (!body) return "";
      return `<div class="chat-console-out is-file">${consoleNumberedHtml(body, { lang: consoleLangForPath(trace.path) })}</div>`;
    }
    if (trace && trace.name === "search_replace") {
      const diff = [trace.preview, trace.output].find((value) => looksLikeDiff(value));
      if (diff) return `<div class="chat-console-out is-diff">${consoleDiffHtml(diff)}</div>`;
    }
    let body = consoleOutputText(trace, failed, running);
    if (!body) return "";
    if ((trace.name === "grep" || trace.name === "glob") && !body.includes("\n") && (body.match(/:\d+:/g) || []).length > 1) {
      body = body.replace(/\s+(\/(?:home|Users|var|tmp|opt|usr)|[A-Za-z]:\\|\S+:\d+:)/g, "\n$1").trim();
    }
    if (looksLikeDiff(body)) return `<div class="chat-console-out is-diff">${consoleDiffHtml(body)}</div>`;
    const lines = String(body).replace(/\s+$/, "").split("\n");
    const inner = lines.map((line) => `<div class="chat-console-stdout">${renderAnsi(line) || " "}</div>`).join("");
    return `<pre class="chat-console-out is-stdout">${inner}</pre>`;
  }

  function consoleDurationLabel(trace) {
    const ms = Number(trace && (trace.execution_duration_ms ?? trace.duration_ms ?? trace.elapsed_ms));
    if (!Number.isFinite(ms) || ms < 0) return "";
    const s = Math.max(1, Math.round(ms / 1000));
    if (s < 60) return `${s}s`;
    return `${Math.floor(s / 60)}m ${s % 60}s`;
  }

  function consoleResultLine(trace, presentation) {
    const { running, failed, interrupted } = presentation;
    const name = trace && trace.name;
    if (running && name === "run_terminal_command" && toolBackground(trace)) return "Running in the background";
    if (running && name === "run_terminal_command") return `$ ${displayCommand(trace) || "command"}`;
    if (running && name === "spawn_subagent") return "Backgrounded agent";
    if (running) return presentation.statusLabel === "Cancelling" ? "Cancelling…" : "Running…";
    if (failed) return String(trace.error || trace.summary || "failed").replace(/\s+/g, " ").trim();
    if (interrupted) return "Interrupted";
    if (trace.wake_state === "dropped") return "Report not delivered";
    const preview = String(trace.preview || trace.summary || "").replace(/\s+/g, " ").trim();
    if (name === "read_url") {
      const host = pageHost(trace.url);
      if (preview && preview !== "still running" && preview.length < 72) return preview;
      return host || "done";
    }
    if (name === "read_file") {
      const n = consoleLineCount(trace);
      if (n) return `Read ${n} line${n === 1 ? "" : "s"}`;
      if (/^\d+\s+lines?/i.test(preview)) return preview.replace(/^(\d+\s+lines?).*/i, "Read $1");
      return "Read";
    }
    if (name === "list_dir") {
      const n = consoleLineCount(trace);
      if (n) return `${n} ${n === 1 ? "entry" : "entries"}`;
      return preview || "listed";
    }
    if (name === "write_file") {
      const n = consoleLineCount(trace);
      const path = compactPath(trace.path || ".", trace, 72);
      if (n) return `Wrote ${n} line${n === 1 ? "" : "s"} to ${path}`;
      return path ? `Wrote ${path}` : "Wrote file";
    }
    if (name === "search_replace") {
      const added = Number(trace.added ?? trace.lines_added);
      const removed = Number(trace.removed ?? trace.lines_removed);
      if (Number.isFinite(added) || Number.isFinite(removed)) {
        return `Added ${Number.isFinite(added) ? added : 0} line${added === 1 ? "" : "s"}, removed ${Number.isFinite(removed) ? removed : 0} line${removed === 1 ? "" : "s"}`;
      }
      const hunks = String(trace.preview || "").split("\n").filter((line) => line.startsWith("-") && !line.startsWith("---")).length;
      if (hunks) return `Added ${hunks} line${hunks === 1 ? "" : "s"}, removed ${hunks} line${hunks === 1 ? "" : "s"}`;
      return "Edited";
    }
    if (name === "todo_write") {
      const todos = normalizeTodos(trace.todos);
      if (todos.length) {
        const done = todos.filter((item) => item.status === "completed").length;
        return `${todos.length} ${todos.length === 1 ? "todo" : "todos"} · ${done}/${todos.length} done`;
      }
      return "no todos";
    }
    if (name === "run_terminal_command") {
      if (toolBackground(trace)) {
        const exit = shellExitCode(trace);
        if (exit === 0) return `Background command completed (exit code 0)`;
        if (exit !== null) return `Background command exited ${exit}`;
      }
      const exit = shellExitCode(trace);
      if (exit !== null && exit !== 0) return `exit ${exit}`;
      return preview && preview !== "still running" ? shortToolText(preview, 100) : (displayCommand(trace) || "done");
    }
    if (name === "kill_command") {
      const cmd = displayCommand(trace) || preview;
      return cmd ? `${cmd} · stopped` : "stopped";
    }
    if (name === "spawn_subagent") {
      const took = consoleDurationLabel(trace);
      return took ? `finished · ${took}` : "Backgrounded agent";
    }
    if (name === "get_subagent_output") {
      const subject = agentSubject(trace) || "subagent";
      const took = consoleDurationLabel(trace);
      return took ? `Agent "${subject}" finished · ${took}` : `Agent "${subject}" finished`;
    }
    if (name === "monitor") {
      const n = Number(trace.event_count);
      if (Number.isFinite(n)) return `${n} ${n === 1 ? "event" : "events"}`;
      return preview || "watching";
    }
    if (name === "grep" || name === "glob") {
      const n = consoleMatchCount(trace);
      const lines = String(trace.output || trace.preview || "").split("\n").map((line) => line.trim()).filter(Boolean);
      if (n <= 1 && lines[0] && lines[0].length <= 140) return lines[0];
      if (n) return `${n} ${n === 1 ? "match" : "matches"}`;
      return "";
    }
    if (isSearchTool(name)) {
      const hits = Array.isArray(trace.results) ? trace.results.length : 0;
      if (hits) return `${hits} ${hits === 1 ? "result" : "results"}`;
      const queries = searchQueries(trace);
      if (queries.length) return queries.length === 1 ? "1 query" : `${queries.length} queries`;
      return "done";
    }
    return preview && preview !== "still running" ? shortToolText(preview, 80) : "done";
  }

  function consoleVerb(name, trace) {
    if (name === "fuse-steer") return "notice";
    if (isSearchTool(name)) return "Web Search";
    if (name === "read_url") return "Fetch";
    if (name === "read_file") return "Read";
    if (name === "list_dir") return "List";
    if (name === "write_file") return "Write";
    if (name === "search_replace") return "Update";
    if (name === "run_terminal_command") return "Bash";
    if (name === "monitor") return "Watch";
    if (name === "get_command_output") return "Wait";
    if (name === "kill_command") return "Stop Task";
    if (name === "kill_subagent") return "Stop";
    if (name === "spawn_subagent") return "Agent";
    if (name === "get_subagent_output") return "Await";
    if (name === "list_subagents") return "Agents";
    if (name === "search_models") return "Models";
    if (name === "load_skill") return "Skill";
    if (name === "grep") return "Grep";
    if (name === "glob") return "Glob";
    if (name === "todo_write") return "Todo";
    if (name === "create_artifact" || name === "update_artifact") return "Artifact";
    if (name === "search_tools") return "Tools";
    return "Tool";
  }

  function cliLine(trace, running = false, failed = false) {
    const name = trace && trace.name;
    const path = String((trace && trace.path) || "").trim();
    const cmd = String((trace && trace.command) || "").trim();
    const pattern = String((trace && (trace.pattern || trace.query)) || "").trim();
    if (name === "fuse-steer") return toolText((trace && trace.summary) || "") || "Fuse notice";
    if (name === "run_terminal_command") {
      const detail = toolText(cmd) || "command";
      const exit = shellExitCode(trace);
      if (exit !== null && (failed || exit !== 0)) return `${detail} (exit ${exit})`;
      if (running) return detail ? `exec ${detail}` : "exec";
      return detail;
    }
    if (name === "monitor") {
      const target = toolText((trace && (trace.url || cmd)) || "") || "watch";
      const n = Number(trace && trace.event_count);
      if (failed) return `watch failed · ${target}`;
      if (running) return Number.isFinite(n) && n > 0 ? `watch ${target} · ${n}` : `watch ${target}`;
      return Number.isFinite(n) ? `${target} · ${n} events` : target;
    }
    if (name === "get_command_output") {
      if (!cmd) return running ? "awaiting bg command" : "bg result";
      return running ? `waiting · ${cmd}` : `result · ${cmd}`;
    }
    if (name === "kill_command") return "stop command";
    if (name === "write_file" || name === "search_replace" || name === "read_file") return shortToolPath(path, trace) || "file";
    if (name === "list_dir") return shortToolPath(path, trace) || "/";
    if (name === "grep") {
      const q = pattern ? `“${toolText(pattern)}”` : "";
      const p = shortToolPath(path, trace);
      if (q && p) return `${q} in ${p}`;
      return q || p || "pattern";
    }
    if (name === "glob") return toolText(pattern || shortToolPath(path, trace)) || "files";
    if (name === "load_skill") {
      const skill = toolText((trace && trace.preview) || "skill");
      const p = shortToolPath(path, trace);
      return p ? `${skill} / ${p}` : skill;
    }
    if (name === "spawn_subagent") return agentSubject(trace) || (failed ? "failed" : "subagent");
    if (name === "get_subagent_output") {
      if (running || String((trace && trace.preview) || "") === "still running") {
        return agentSubject(trace) ? `waiting on ${agentSubject(trace)}` : "waiting on subagent";
      }
      const subject = agentSubject(trace);
      if (failed) return subject ? `${subject} failed` : "failed";
      return subject ? `result · ${subject}` : "result";
    }
    if (name === "kill_subagent") return "stop subagent";
    if (name === "list_subagents") {
      const detail = agentListLine(trace);
      return detail && detail !== "agents" ? detail : "listed";
    }
    if (name === "search_models") {
      const q = toolText((trace && (trace.query || trace.preview)) || "");
      return q && q !== "providers" ? q : "catalog";
    }
    if (name === "search_tools") {
      const q = toolText((trace && trace.query) || "");
      return q && q !== "index" ? q : "index";
    }
    if (name === "todo_write") return toolText((trace && trace.preview) || "tasks");
    if (name === "fuse-steer") return toolText((trace && trace.summary) || "") || "Fuse notice";
    return toolText(path || cmd || pattern || (trace && (trace.preview || trace.description)) || name || "tool");
  }

  function activityLine(trace, running = false, failed = false) {
    return isHarnessView() ? cliLine(trace, running, failed) : workspaceLine(trace, running, failed);
  }

  function friendlyToolList(raw) {
    const names = {
      run_terminal_command: "shell command",
      get_command_output: "command output",
      kill_command: "stop command",
      monitor: "monitor",
      read_file: "file reader",
      list_dir: "directory listing",
      grep: "content search",
      glob: "file matching",
      write_file: "file writer",
      search_replace: "file editor",
      spawn_subagent: "subagents",
    };
    return String(raw || "")
      .split(",")
      .map((name) => name.trim())
      .filter(Boolean)
      .map((name) => names[name] || name.replaceAll("_", " "))
      .join(", ");
  }

  function cliVerb(name, trace) {
    if (name === "fuse-steer") return "steer";
    if (isSearchTool(name)) return "web";
    if (name === "read_url") return "page";
    if (name === "read_file") return "read";
    if (name === "list_dir") return "dir";
    if (name === "write_file") return "write";
    if (name === "search_replace") return "edit";
    if (name === "run_terminal_command") return "shell";
    if (name === "monitor") return "watch";
    if (name === "get_command_output") return "job";
    if (name === "kill_command" || name === "kill_subagent") return "kill";
    if (name === "spawn_subagent") return "agent";
    if (name === "get_subagent_output") {
      if (trace && (trace.status === "running" || String(trace.preview || "") === "still running")) return "wait";
      return "result";
    }
    if (name === "list_subagents") return "agents";
    if (name === "search_models") return "models";
    if (name === "load_skill") return "skill";
    if (name === "grep") return "grep";
    if (name === "glob") return "glob";
    if (name === "todo_write") return "todo";
    if (name === "create_artifact" || name === "update_artifact") return "art";
    if (name === "search_tools") return "tools";
    return "tool";
  }

  function cliKind(name, fallback, trace) {
    if (name === "run_terminal_command") return "shell";
    if (name === "monitor") return "watch";
    if (!isHarnessView()) return "";
    if (fallback) return fallback;
    return cliVerb(name, trace);
  }

  function workspaceLine(trace, running = false, failed = false) {
    const name = trace && trace.name;
    const path = String((trace && trace.path) || "").trim();
    const cmd = String((trace && trace.command) || "").trim();
    const pattern = String((trace && (trace.pattern || trace.query)) || "").trim();
    const target = toolTarget(trace);
    if (name === "load_skill") {
      const skill = toolText((trace && trace.preview) || "skill");
      const subject = path ? `${skill} / ${shortToolPath(path, trace)}` : skill;
      if (failed) return path ? `Could not read skill file ${subject}` : `Could not load skill ${subject}`;
      if (running) return path ? `Reading skill file ${subject}` : `Loading skill ${subject}`;
      return path ? `Read skill file ${subject}` : `Loaded skill ${subject}`;
    }
    if (name === "search_tools") {
      const q = toolText((trace && trace.query) || "");
      if (failed) return q ? `Could not find tools for ${q}` : "Could not find tools";
      if (running) return q ? `Finding tools for ${q}` : "Finding available tools";
      if (q === "hosts" || target === "hosts") return "Listed available hosts";
      if (q === "skills" || target === "skills") return "Listed available skills";
      return target && target !== "index" ? `Enabled tools: ${friendlyToolList(target)}` : "Checked available tools";
    }
    if (name === "search_models") {
      const q = toolText((trace && (trace.query || trace.preview)) || "");
      if (failed) return q ? `Could not find models for ${q}` : "Could not find models";
      if (running) return q ? `Finding models for ${q}` : "Finding available models";
      if (!q || q === "providers") return "Listed available providers";
      return `Found models for ${q}`;
    }
    if (name === "todo_write") {
      if (failed) return "Could not update tasks";
      return running ? "Updating task list" : target ? `Updated ${target}` : "Updated task list";
    }
    if (name === "spawn_subagent") {
      const subject = agentSubject(trace) || toolText(target);
      if (failed) return subject ? `Could not start subagent: ${subject}` : "Could not start subagent";
      if (running) return subject ? `Spawning subagent: ${subject}` : "Spawning subagent";
      return subject ? `Spawned subagent: ${subject}` : "Spawned subagent";
    }
    if (name === "get_subagent_output") {
      const subject = agentSubject(trace);
      if (failed) return subject ? `Could not check subagent: ${subject}` : "Could not check subagent";
      if (running || String((trace && trace.preview) || "") === "still running") {
        return subject ? `Waiting for ${subject}` : "Waiting for subagent";
      }
      return subject ? `Subagent result: ${subject}` : "Received subagent result";
    }
    if (name === "kill_subagent") return failed ? "Could not stop subagent" : running ? "Stopping subagent" : "Stopped subagent";
    if (name === "list_subagents") {
      const detail = agentListLine(trace);
      if (failed) return "Could not list agents";
      if (running) return "Listing agents";
      return detail && detail !== "agents" ? `Listed agents — ${detail}` : "Listed agents";
    }
    if (name === "get_command_output") {
      const subject = toolText(cmd);
      if (failed) return subject ? `Could not read ${subject} output` : "Could not read background output";
      if (running) return subject ? `Waiting on ${subject}` : "Waiting on background command";
      return subject ? `Result · ${subject}` : "Background command result";
    }
    if (name === "kill_command") return failed ? "Could not stop command" : running ? "Stopping command" : "Stopped command";
    if (name === "run_terminal_command") {
      const detail = toolText(cmd) || "command";
      const exit = shellExitCode(trace);
      if (exit !== null && (failed || exit !== 0)) return `${detail} (exit ${exit})`;
      if (running) return detail ? `Executing ${detail}` : "Executing command";
      return detail ? `Executed ${detail}` : "Executed command";
    }
    if (name === "monitor") {
      const target = toolText((trace && (trace.url || cmd)) || "") || "target";
      const n = Number(trace && trace.event_count);
      const count = Number.isFinite(n) ? ` · ${n} ${n === 1 ? "event" : "events"}` : "";
      if (failed) return `Could not watch ${target}`;
      if (running) return `Watching ${target}${count}`;
      return `Watched ${target}${count}`;
    }
    const shortPath = shortToolPath(path, trace);
    if (name === "write_file") return path ? `${running ? "Writing" : "Wrote"} ${shortPath}` : running ? "Writing a file" : "Wrote a file";
    if (name === "search_replace") return path ? `${running ? "Editing" : "Edited"} ${shortPath}` : running ? "Editing a file" : "Edited a file";
    if (name === "read_file") return path ? `${running ? "Reading" : "Read"} ${shortPath}` : running ? "Reading a file" : "Read a file";
    if (name === "list_dir") return path ? `${running ? "Listing" : "Listed"} ${shortPath}` : running ? "Listing a directory" : "Listed a directory";
    if (name === "grep") {
      const q = pattern ? `“${toolText(pattern)}”` : "";
      const verb = running ? "Grepping" : "Grepped";
      if (q && shortPath) return `${verb} ${q} in ${shortPath}`;
      if (q) return `${verb} ${q}`;
      return shortPath ? `${verb} ${shortPath}` : `${verb} file contents`;
    }
    if (name === "glob") return pattern ? `${running ? "Globbing" : "Globbed"} ${toolText(pattern)}` : shortPath ? `${running ? "Globbing" : "Globbed"} files in ${shortPath}` : `${running ? "Globbing" : "Globbed"} files`;
    if (WORKSPACE_VERBS[name]) {
      const extra = toolText(path || cmd || pattern || (trace && trace.description) || "");
      return extra ? `${WORKSPACE_VERBS[name]} ${extra}` : WORKSPACE_VERBS[name];
    }
    return name || "Tool";
  }

  function toolStateHtml(running, failed, interrupted, statusLabel) {
    const label = statusLabel || (failed ? "Failed" : interrupted ? "Stopped" : running ? "Running" : "Done");
    if (isConsoleView()) return `<span class="chat-step-state is-empty" title="${escapeHtml(label)}" aria-hidden="true"></span>`;
    return `<span class="chat-step-state" title="${escapeHtml(label)}">${escapeHtml(label)}</span>`;
  }

  function toolOpenHtml(kind, id) {
    if (!id) return '<span class="chat-step-open is-empty" aria-hidden="true">Open</span>';
    const attr = kind === "artifact" ? "data-open-artifact" : "data-open-agent";
    return `<button type="button" class="chat-step-open" ${attr}="${escapeHtml(id)}" aria-label="Open ${kind === "artifact" ? "artifact" : "agent"}">Open${isHarnessView() ? `<span class="chat-open-target">&nbsp;${kind === "artifact" ? "artifact" : "agent"} ↗</span>` : ""}</button>`;
  }

  function toolCaretHtml(expandable, open) {
    if (!expandable) return '<span class="chat-step-caret is-empty" aria-hidden="true"></span>';
    return `<span class="chat-step-caret" aria-hidden="true">${STEP_CHEVRON}</span>`;
  }

  function toolHeadHtml({ line, title, running, failed, interrupted, expandable, open, openKind, openId, kind, statusLabel, openRow, contextHost }) {
    const label = `<span class="chat-step-label"${title ? ` title="${escapeHtml(title)}"` : ""}>${line}</span>`;
    const inner = kind
      ? `<span class="chat-step-copy"><span class="chat-step-kind">${escapeHtml(kind)}</span>${label}</span>`
      : label;
    const copy = expandable
      ? `<button type="button" class="chat-step-toggle" data-step-toggle aria-expanded="${open ? "true" : "false"}">${inner}</button>`
      : openRow
        ? `<button type="button" class="chat-step-toggle is-open-agent" data-open-agent="${escapeHtml(openId || "")}" aria-haspopup="dialog" title="Open subagent">${inner}</button>`
        : `<div class="chat-step-static">${inner}</div>`;
    const caret = expandable
      ? `<button type="button" class="chat-step-caret" data-step-toggle aria-expanded="${open ? "true" : "false"}" aria-label="Toggle details">${STEP_CHEVRON}</button>`
      : openRow
        ? `<span class="chat-step-caret" aria-hidden="true">${STEP_CHEVRON}</span>`
        : toolCaretHtml(false);
    return `<div class="chat-step-head">${copy}${toolStateHtml(running, failed, interrupted, statusLabel)}${toolOpenHtml(openKind, openId)}${caret}` +
      (contextHost && !openId ? `<span class="chat-step-host" title="Host ${escapeHtml(contextHost)}">@${escapeHtml(contextHost)}</span>` : "") + `</div>`;
  }

  function terminalOutputHtml(output, error, running, exitNote = "", background = false) {
    const lines = [];
    if (output) lines.push(`<div class="chat-terminal-line">${renderAnsi(output)}</div>`);
    if (error) lines.push(`<div class="chat-terminal-line is-error">${renderAnsi(error)}</div>`);
    if (exitNote) lines.push(`<div class="chat-terminal-line is-exit"><span class="chat-terminal-exit">${escapeHtml(exitNote)}</span></div>`);
    if (running) lines.push(`<div class="chat-terminal-line is-pending"><span class="chat-terminal-pending">${background ? (output ? "Running in background" : "Sent to background") : output ? "Running" : "Waiting for output"}</span></div>`);
    if (!lines.length) lines.push('<div class="chat-terminal-line"><span class="chat-terminal-empty">No output</span></div>');
    return `<div class="chat-terminal-output">${lines.join("")}</div>`;
  }

  const TODO_STATUSES = { pending: "Pending", in_progress: "In progress", completed: "Done", cancelled: "Cancelled" };

  function normalizeTodos(raw) {
    if (!Array.isArray(raw)) return [];
    const out = [];
    raw.slice(0, 40).forEach((item, index) => {
      if (!item || typeof item !== "object") return;
      const status = TODO_STATUSES[item.status] ? item.status : "pending";
      const content = String(item.content || "").replace(/\s+/g, " ").trim().slice(0, 200);
      if (!content) return;
      out.push({ id: String(item.id || index + 1).slice(0, 64), content, status });
    });
    return out;
  }

  function todoListHtml(todos, { live = false } = {}) {
    const items = normalizeTodos(todos);
    if (!items.length) return "";
    const done = items.filter((item) => item.status === "completed").length;
    return `<ol class="chat-todo" data-todo-list>${items.map((item) => {
      const mark = item.status === "completed" ? "✓" : item.status === "cancelled" ? "×" : item.status === "in_progress" ? "›" : "○";
      return `<li class="chat-todo-item is-${item.status.replace("_", "-")}${live && item.status === "in_progress" ? " is-current" : ""}">` +
        `<span class="chat-todo-mark" aria-hidden="true">${mark}</span>` +
        `<span class="chat-todo-copy">${escapeHtml(item.content)}</span>` +
        `<span class="chat-todo-status">${TODO_STATUSES[item.status]}</span></li>`;
    }).join("")}</ol><p class="chat-todo-count">${done}/${items.length} done</p>`;
  }



  function exitNoteHtml(code) {
    const text = `exit ${Number(code)}`;
    return (
      `<div class="chat-query is-exit-note">` +
      `<span class="chat-query-icon" aria-hidden="true">${STEP_ICONS.shell}</span>` +
      `<span class="chat-query-label" title="${escapeHtml(text)}">${escapeHtml(text)}</span>` +
      `</div>`
    );
  }

  function toolDetailHtml(trace, failed, running) {
    const sections = [];
    const error = failed ? trace.error || trace.summary || trace.preview || "The tool failed without an error message."
      : ["interrupted", "cancelled"].includes(trace.status) || ["cancelled", "interrupted", "lost"].includes(executionState(trace)) ? trace.error || "" : "";
    const inputs = [["Command", trace.command], ["Path", trace.path], ["Pattern", trace.pattern], ["URL", trace.url],
      ["Query", searchQueries(trace).join("\n")], ["Task", trace.description], ["Title", trace.title]];
    if (error) sections.push(failNoteHtml(error));
    if (trace.command) sections.push(detailSectionHtml("Command", trace.command, "is-command", 8000));
    const context = [trace.host ? `Host ${trace.host}` : "", trace.cwd ? `Directory ${trace.cwd}` : "",
      Number.isFinite(trace.offset) ? `From line ${trace.offset}${Number.isFinite(trace.limit) ? ` · up to ${trace.limit} lines` : ""}` : "",
      trace.include ? `Include ${trace.include}` : "", trace.max_matches ? `Limit ${trace.max_matches} matches` : "",
      running && trace.progressLabel ? trace.progressLabel : "",
      ["write_file", "search_replace"].includes(trace.name) && trace.preview ? trace.preview : ""].filter(Boolean);
    if (context.length) sections.push(`<div class="chat-tool-context">${context.map(value => `<span>${escapeHtml(value)}</span>`).join("")}</div>`);
    const seen = new Set([error, ...inputs.map(([, value]) => value)].filter(Boolean).map(normErr));
    const preview = trace.summary && String(trace.summary).startsWith(String(trace.preview || "")) ? "" : trace.preview;
    const authoritativeOutput = typeof trace.output === "string";
    const output = authoritativeOutput ? trace.output : [preview, trace.summary].filter((value) => {
      const key = normErr(value);
      if (!key || seen.has(key) || key === "still running" || key === "background task started") return false;
      seen.add(key);
      return true;
    }).join("\n\n");
      const outputLabel = { read_file: "Contents", list_dir: "Entries", grep: "Matches", glob: "Matches", search_replace: "Change result", write_file: "Write result", todo_write: "Tasks", spawn_subagent: "Report", get_subagent_output: "Report", monitor: "Events" }[trace.name] || "Output";
    if (trace.name === "todo_write") {
      const list = todoListHtml(trace.todos, { live: running });
      if (list) sections.push(`<section class="chat-tool-section is-todos" data-tool-section="Tasks">${list}</section>`);
    } else if (output) sections.push(detailSectionHtml(outputLabel, output, "is-result", authoritativeOutput ? 24000 : 4000));
    if (authoritativeOutput) {
      const retained = Array.from(output).length;
      const total = Number.isFinite(trace.output_chars) ? trace.output_chars : retained;
      let note = "";
      if (trace.output_mode === "partial" && (trace.truncated || total > retained)) note = `Partial output · ${retained.toLocaleString()} characters retained · total ${trace.output_chars_exact === true ? total.toLocaleString() : `unknown (at least ${total.toLocaleString()})`}.`;
      else if (trace.output_mode === "tail") note = `Retained tail · ${retained.toLocaleString()} of ${trace.output_chars_exact === false ? "at least " : ""}${total.toLocaleString()} characters${Number.isFinite(trace.output_start) ? ` · starts at offset ${trace.output_start.toLocaleString()}` : ""}.`;
      else if (trace.truncated || total > retained) note = `Output truncated · ${retained.toLocaleString()} of ${trace.output_chars_exact === false ? "at least " : ""}${total.toLocaleString()} characters retained.`;
      if (note) sections.push(`<span class="chat-tool-truncated">${escapeHtml(note)}</span>`);
    } else if (trace.output_truncated && output.length <= 4000) sections.push('<span class="chat-tool-truncated">Output preview was truncated before display.</span>');
    if (trace.results?.length) sections.push(`<section class="chat-tool-section"><span class="chat-tool-section-label">Sources</span>${sourceList(trace.results)}</section>`);
    if (!error && !output && !trace.results?.length) sections.push(detailSectionHtml("Output", running ? "Waiting for output"
      : trace.child_id && ["spawn_subagent", "get_subagent_output"].includes(trace.name) ? "No inline output. Open agent for its transcript." : "No output", "is-empty"));
    inputs.filter(([label]) => label !== "Command").forEach(([label, value]) => { if (value) sections.push(detailSectionHtml(label, value, "is-input")); });
    const exit = shellExitCode(trace);
    const duration = Number.isFinite(trace.execution_duration_ms) ? trace.execution_duration_ms : Number.isFinite(trace.duration_ms) ? trace.duration_ms : trace.latency_ms;
    const metadata = [toolBackground(trace) ? "Background execution" : "", executionIdentity(trace) ? `ID ${executionIdentity(trace)}` : "",
      executionState(trace) ? `Execution ${executionState(trace)}` : "",
      trace.wake_state === "dropped" ? "Report not delivered to the main agent" : trace.wake_state === "retrying" ? "Report pending delivery" : "",
      trace.status === "done" && executionPending(trace) ? "Tool returned; execution continues" : "",
      trace.termination_confirmed === true ? "Termination confirmed" : trace.termination_confirmed === false ? "Termination not confirmed"
        : isShellExecTool(trace.name) && executionState(trace) && !executionPending(trace) ? "Termination unknown" : "",
      trace.version != null ? `Version ${trace.version}` : "", trace.visibility ? `Visibility ${trace.visibility}` : "",
      exit !== null ? `Exit ${exit}` : "", Number.isFinite(duration) ? `${duration} ms` : "",
      Number.isFinite(trace.call_duration_ms) && trace.call_duration_ms !== duration ? `Tool call ${trace.call_duration_ms} ms` : "",
      running && Number.isFinite(trace.elapsed_ms) ? `Elapsed ${trace.elapsed_ms} ms` : "",
      Number.isFinite(trace.timeout_ms) ? `Timeout ${trace.timeout_ms} ms` : "",
      trace.name === "monitor" && Number.isFinite(trace.event_count) ? `${trace.event_count} events` : "",
      trace.name === "monitor" && trace.source ? `Source ${trace.source}` : ""].filter(Boolean);
    if (metadata.length) sections.push(detailSectionHtml("Execution", metadata.join(" · "), "is-meta"));
    return `<div class="chat-tool-details">${sections.join("")}</div>`;
  }

  function detailSectionHtml(label, value, kind = "", cap = 4000) {
    const text = String(value || "");
    const characters = Array.from(text);
    const clipped = characters.slice(0, cap).join("");
    const ansi = kind === "is-result" || kind === "is-command" || kind === "is-error";
    const body = looksLikeDiff(clipped)
      ? `<pre class="chat-tool-out is-diff"><code>${highlightCode("diff", clipped)}</code></pre>`
      : `<pre class="chat-tool-out">${ansi ? renderAnsi(clipped) : escapeHtml(clipped)}</pre>`;
    return `<section class="chat-tool-section ${kind}" data-tool-section="${escapeHtml(label)}"><div class="chat-tool-section-head"><span class="chat-tool-section-label">${escapeHtml(label)}</span>` +
      `<button type="button" class="chat-tool-copy" data-copy-tool aria-label="Copy ${escapeHtml(label.toLowerCase())}">Copy</button></div>` +
      body +
      (characters.length > cap ? `<span class="chat-tool-truncated">Preview truncated at ${cap.toLocaleString()} characters.</span>` : "") + "</section>";
  }

  function toolRowLabel(trace) {
    const subject = toolSubject(trace);
    return `${toolAction(trace)}${subject ? ` · ${subject}` : ""}`;
  }

  function toolStepHtml(trace, index, live, open, openMap) {
    const presentation = toolPresentation(trace, live);
    const { running, interrupted, failed } = presentation;
    const artifact = trace.name === "create_artifact" || trace.name === "update_artifact";
    const read = trace.name === "read_url";
    const search = isSearchTool(trace.name);
    const label = artifact ? `${trace.name === "create_artifact" ? "Create artifact" : "Update artifact"} · ${trace.title || "artifact"}`
      : read ? `Page read · ${pageHost(trace.url) || "page"}`
      : search ? `Search · ${searchCountFor(trace) === 1 && searchQueries(trace)[0] ? shortToolText(searchQueries(trace)[0], 72) : `${searchCountFor(trace)} queries`}`
      : toolRowLabel(trace);
    const kind = artifact ? "artifact" : read ? "read" : search ? "search" : TOOL_KIND[trace.name] || "workspace";
    const openKind = artifact && trace.identifier && presentation.state === "done" ? "artifact"
      : ["spawn_subagent", "get_subagent_output"].includes(trace.name) && trace.child_id ? "agent" : "";
    const openId = openKind === "artifact" ? trace.identifier : openKind === "agent" ? trace.child_id : "";
    const inner = isConsoleView() ? consoleDetailHtml(trace, failed, running) : toolDetailHtml(trace, failed, running);
    // Todo lists are plain inline content in console view: always shown, no disclosure.
    const revealed = open || (!isConsoleView() && trace.name === "todo_write" && normalizeTodos(trace.todos).length > 0);
    const icon = failed ? STEP_ICONS.error : interrupted ? STEP_ICONS.stop : artifact ? STEP_ICONS.artifact : toolIcon(trace.name, false, false);
    if (isConsoleView()) {
      const result = consoleResultLine(trace, presentation);
      const preview = consoleCollapsedPreview(trace, failed, running);
      const expandable = openKind !== "agent" && trace.name !== "todo_write" && Boolean(inner) && inner !== preview;
      const head = toolHeadHtml({
        ...presentation, line: consoleCallHtml(trace, presentation), title: trace.command || trace.path || trace.url || label, expandable, open: revealed, openKind, openId,
        contextHost: "",
      });
      const hangOpen = revealed && expandable;
      const status = result || (running ? "Running…" : "done");
      const showHang = true;
      return (
        `<li class="chat-step is-console-call is-${kind} is-${presentation.state}${hangOpen ? " is-console-expanded" : ""}" data-execution="${escapeHtml(executionIdentity(trace))}" data-tool="${escapeHtml(trace.name)}" data-state="${presentation.state}" data-step="${escapeHtml(index)}">` +
        `<span class="chat-step-node" aria-hidden="true">${icon}</span>` +
        `<div class="chat-step-main">${head}` +
        (showHang
          ? `<div class="chat-step-reveal is-open">` +
            `<div class="chat-step-reveal-inner">` +
            `<div class="chat-console-hang${running ? " is-running" : ""}${failed ? " is-error" : ""}${hangOpen ? " is-expanded" : ""}">` +
            `<span class="chat-console-branch" aria-hidden="true">└</span>` +
            `<div class="chat-console-hang-body">` +
            `<span class="chat-console-result-text">${escapeHtml(status)}</span>` +
            (preview ? `<div class="chat-console-preview">${preview}</div>` : "") +
            (inner ? inner : "") +
            `</div></div></div></div>`
          : "") +
        `</div></li>`
      );
    }
    const head = toolHeadHtml({
      ...presentation, line: toolLabelHtml(trace), title: trace.command || trace.path || trace.url || label, expandable: Boolean(inner), open: revealed, openKind, openId,
      contextHost: trace.host && trace.host !== sessionProject()?.host_alias ? trace.host : "",
    });
    return (
       `<li class="chat-step is-${kind} is-${presentation.state}" data-execution="${escapeHtml(executionIdentity(trace))}" data-tool="${escapeHtml(trace.name)}" data-state="${presentation.state}" data-step="${escapeHtml(index)}">` +
      `<span class="chat-step-node" aria-hidden="true">${icon}</span>` +
      `<div class="chat-step-main">${head}` +
      (inner ? `<div class="chat-step-reveal${revealed ? " is-open" : ""}"><div class="chat-step-reveal-inner">${inner}</div></div>` : "") +
      "</div></li>"
    );
  }

  function stackSubject(trace) {
    const name = trace && trace.name;
    if (name === "grep" || name === "glob") {
      const p = toolText(String((trace && (trace.pattern || trace.query)) || "").trim(), 40);
      return p ? `“${p}”` : "";
    }
    return shortToolPath(String((trace && trace.path) || "").trim(), trace) || toolPathLeaf(String((trace && trace.path) || "").trim());
  }

  function cliStackTitle(group, items, failed, running) {
    const spec = STACK[group.name];
    const n = items.length;
    const fallback = stackTitle(group.name, n, failed, running);
    if (!isHarnessView() || !spec || failed === n) return fallback;
    const subjects = items.map((i) => stackSubject(i.trace));
    const first = subjects[0];
    if (!first) return fallback;
    const verb = running ? spec.verbing : spec.verb;
    const failSuffix = failed ? ` · ${failed} failed` : "";
    if (n === 2 && subjects[1]) return `${verb} ${first} and ${subjects[1]}${failSuffix}`;
    if (n >= 3) return `${verb} ${first} and ${countPhrase(n - 1, `other ${spec.one}`, `other ${spec.many}`)}${failSuffix}`;
    return fallback;
  }

  function stackTitle(name, n, failed, running) {
    if (name === "run_terminal_command") {
      const noun = countPhrase(n, "command", "commands");
      if (isHarnessView()) {
        if (failed === n) return `${noun} failed`;
        if (failed) return `${noun} · ${failed} failed`;
        return running ? `exec ${noun}` : noun;
      }
      if (failed === n) return `${noun} failed`;
      if (failed) return `${running ? "Executing" : "Executed"} ${noun} · ${failed} failed`;
      return `${running ? "Executing" : "Executed"} ${noun}`;
    }
    if (name === "get_command_output") {
      const noun = countPhrase(n, "job", "jobs");
      if (failed === n) return `${noun} failed`;
      if (failed) return `${running ? "Waiting for" : "Checked"} ${noun} · ${failed} failed`;
      return `${running ? "Waiting for" : "Checked"} ${noun}`;
    }
    if (name === "spawn_subagent") {
      const noun = countPhrase(n, "subagent", "subagents");
      if (failed === n) return `${noun} failed`;
      if (failed) return `${running ? "Spawning" : "Spawned"} ${noun} · ${failed} failed`;
      return `${running ? "Spawning" : "Spawned"} ${noun}`;
    }
    if (name === "get_subagent_output") {
      const noun = countPhrase(n, "result", "results");
      if (failed === n) return `${noun} failed`;
      if (failed) return `${running ? "Waiting for" : "Collected"} ${noun} · ${failed} failed`;
      return `${running ? "Waiting for" : "Collected"} ${noun}`;
    }
    if (name === "list_subagents") {
      return running ? "Listing agents" : "Listed agents";
    }
    if (name === "search_models") {
      return running ? "Finding models" : "Found models";
    }
    const spec = stackSpec(name) || { verb: "Ran", verbing: "Running", one: "step", many: "steps" };
    const noun = n === 1 ? spec.one : spec.many;
    const verb = running ? spec.verbing : spec.verb;
    if (failed === n) return `${n} ${noun} failed`;
    if (failed) return `${verb} ${n} ${noun} · ${failed} failed`;
    return `${verb} ${n} ${noun}`;
  }

  function workspaceSubRow(entry, groupIndex, itemIndex, live, openMap) {
    const key = toolKey(entry.trace, entry.index);
    return toolStepHtml(entry.trace, key, live, stepOpenAt(openMap, key), openMap);
  }

  function workspaceStackHtml(group, groupIndex, live, openMap) {
    const items = group.items;
    const counts = toolCounts(items, live);
    const { running, failed, stopped: interrupted } = counts;
    const nouns = { read_file: "file reads", list_dir: "directory listings", grep: "content searches", glob: "file matches",
      write_file: "file writes", search_replace: "file edits", run_terminal_command: "commands", get_command_output: "command checks",
      spawn_subagent: "subagents", get_subagent_output: "agent results", list_subagents: "agent listings", search_models: "model searches", read_url: "page reads" };
    const label = isSearchTool(group.name) ? `${searchSiteCount(group)} web queries` : `${items.length} ${nouns[group.name] || "tools"}`;
    const paths = ["read_file", "write_file", "search_replace", "list_dir"].includes(group.name);
    const subjects = paths ? [...new Set(items.map(i => String(i.trace.path || ".").replaceAll("\\", "/")))].map((path, _, all) => {
      const parts = path.replace(/\/$/, "").split("/");
      let n = 1;
      while (n < parts.length && all.some(other => other !== path && other.split("/").slice(-n).join("/") === parts.slice(-n).join("/"))) n++;
      return parts.slice(-n).join("/");
    }) : [...new Set(items.map(i => toolSubject(i.trace)).filter(Boolean))];
    const preview = subjects.slice(0, 2).map(s => paths ? compactPath(s, {}, 42) : shortToolText(s, 60)).join(", ") + (subjects.length > 2 ? ` +${subjects.length - 2}` : "");
    const hosts = [...new Set(items.map(i => i.trace.host).filter(Boolean))];
    const icon = isHarnessView() ? toolIcon(group.name, false, false) : failed === items.length ? STEP_ICONS.error : toolIcon(group.name, false, interrupted === items.length);
    const state = running ? " is-running" : failed ? " is-error" : interrupted ? " is-interrupted" : counts.warning ? " is-warning" : " is-done";
    const kind = TOOL_KIND[group.name] ? ` is-${TOOL_KIND[group.name]}` : "";
    const groupKey = `group-${groupIndex}`;
    const open = Object.prototype.hasOwnProperty.call(openMap || {}, groupKey)
      ? stepOpenAt(openMap, groupKey)
      : items.some((entry) => stepOpenAt(openMap, toolKey(entry.trace, entry.index)));
    const inner = `<ol class="chat-tool-children">${items.map((entry, i) => workspaceSubRow(entry, groupIndex, i, live, openMap)).join("")}</ol>`;
    const allOpen = items.every(entry => stepOpenAt(openMap, toolKey(entry.trace, entry.index)));
    const targetCount = paths ? `${subjects.length} ${group.name === "list_dir" ? (subjects.length === 1 ? "directory" : "directories") : (subjects.length === 1 ? "file" : "files")} · ${items.length} operations` : `${items.length} calls`;
    return (
      `<li class="chat-step is-workspace is-stack${kind}${state}" data-step="group-${escapeHtml(groupIndex)}">` +
      `<span class="chat-step-node" aria-hidden="true">${icon}</span>` +
      '<div class="chat-step-main">' +
      toolHeadHtml({
        line: `<span class="chat-tool-action">${escapeHtml(isConsoleView() ? consoleVerb(group.name, items[0]?.trace) : isCliView() ? cliVerb(group.name, items[0]?.trace) : toolAction(items[0].trace))}</span><span class="chat-group-title">${escapeHtml(label)}</span><span class="chat-group-preview">${escapeHtml(preview)}</span>` + (hosts.length > 1 ? `<span class="chat-tool-host">${hosts.length} hosts</span>` : ""),
        title: items.slice(0, 10).map((i) => toolRowLabel(i.trace)).join("\n"),
        running,
        failed: Boolean(failed),
        interrupted: Boolean(interrupted),
        statusLabel: countsLabel(counts),
        expandable: true,
        open,
      }) +
      `<div class="chat-step-reveal${open ? " is-open" : ""}"><div class="chat-step-reveal-inner"><div class="chat-group-toolbar"><span>${escapeHtml(targetCount)} · ${escapeHtml(countsLabel(counts))}</span><button type="button" data-group-expand>${allOpen ? "Collapse results" : "Expand results"}</button></div>${inner}</div></div>` +
      "</div></li>"
    );
  }

  function groupStepHtml(group, groupIndex, live, openMap) {
    return workspaceStackHtml(group, groupIndex, live, openMap);
  }

  // Keep an activated control mounted through pointer-up/click. Replacing its
  // node between those events silently drops the native click. Only painting
  // is deferred; incoming state continues to reconcile normally.
  let toolGestureOwner = null;
  let toolGestureTimer = 0;
  let pendingToolPaint = null;
  let pendingFullPaint = false;
  let pendingAgentPaint = false;
  let pendingAgentListPaint = false;
  function flushToolGesture() {
    if (toolGestureTimer) clearTimeout(toolGestureTimer);
    toolGestureTimer = 0;
    toolGestureOwner = null;
    const pending = pendingToolPaint;
    const full = pendingFullPaint;
    const agent = pendingAgentPaint;
    const list = pendingAgentListPaint;
    pendingToolPaint = null;
    pendingFullPaint = pendingAgentPaint = pendingAgentListPaint = false;
    if (full) paint();
    else if (pending?.article.isConnected) updateAssistant(pending.article, pending.msg, pending.live);
    if (agent && agentsOpen) renderAgentView();
    if (list && agentsOpen) renderAgentsList();
  }
  function beginToolGesture(event) {
    const control = event.target.closest?.("[data-step-toggle], [data-think-size], [data-activity-summary], .chat-step-open, [data-open-agent], [data-agent-id]");
    if (!control) return;
    flushToolGesture();
    if (log.contains(control)) pauseThreadFollow();
    toolGestureOwner = control.closest(".chat-turn") || agentsListEl;
  }
  function endToolGesture() {
    if (!toolGestureOwner) return;
    if (toolGestureTimer) clearTimeout(toolGestureTimer);
    toolGestureTimer = setTimeout(flushToolGesture, 0); // after the native click
  }
  root.addEventListener("pointerdown", beginToolGesture, true);
  root.addEventListener("click", () => { if (toolGestureOwner) flushToolGesture(); });
  root.addEventListener("keydown", event => {
    if (["Enter", " "].includes(event.key) && !event.repeat) beginToolGesture(event);
  }, true);
  document.addEventListener("pointerup", endToolGesture, true);
  document.addEventListener("pointercancel", flushToolGesture, true);
  document.addEventListener("keyup", event => { if (["Enter", " "].includes(event.key)) endToolGesture(); }, true);
  window.addEventListener("blur", flushToolGesture);

  const toolMotions = new Map();
  const motionPaints = new Map();
  function revealToolRow(row) {
    if (!row) return;
    const activity = row.closest("[data-activity]");
    if (activity && !activity.classList.contains("is-open")) activity.querySelector("button[data-activity-summary]")?.click();
    const group = row.parentElement?.closest(".is-stack");
    if (group) {
      const toggle = group.querySelector(":scope > .chat-step-main > .chat-step-head > [data-step-toggle]");
      if (toggle?.getAttribute("aria-expanded") !== "true") toggle?.click();
    }
    const toggle = row.querySelector(":scope > .chat-step-main > .chat-step-head > [data-step-toggle]");
    if (toggle?.getAttribute("aria-expanded") !== "true") toggle?.click();
  }
  function setToolReveal(reveal, open, fromHeight) {
    if (!reveal) return;
    const owner = reveal.closest(".chat-turn");
    const start = fromHeight ?? reveal.getBoundingClientRect().height;
    reveal._disclosureAnimation?.cancel();
    reveal.classList.toggle("is-open", open);
    if (reduceMotion.matches) return;
    reveal.style.display = "block";
    reveal.style.overflow = "hidden";
    const end = open ? reveal.scrollHeight : 0;
    const animation = reveal.animate([{ height: `${start}px`, opacity: open ? 0.3 : 1 }, { height: `${end}px`, opacity: open ? 1 : 0 }],
      { duration: isHarnessView() ? 140 : 210, easing: "cubic-bezier(.2,.7,.2,1)" });
    reveal._disclosureAnimation = animation;
    if (owner) toolMotions.set(owner, (toolMotions.get(owner) || 0) + 1);
    const finish = () => {
      if (reveal._disclosureAnimation === animation) {
        reveal.style.display = ""; reveal.style.overflow = "";
        reveal._disclosureAnimation = null;
      }
      if (owner) {
        const n = (toolMotions.get(owner) || 1) - 1;
        if (n) toolMotions.set(owner, n);
        else {
          toolMotions.delete(owner);
          const paint = motionPaints.get(owner);
          motionPaints.delete(owner);
          if (paint && owner.isConnected) updateAssistant(owner, paint.msg, paint.live);
          if (pendingAgentPaint) { pendingAgentPaint = false; renderAgentView(); }
        }
      }
    };
    animation.onfinish = finish;
    animation.oncancel = finish;
  }

  document.addEventListener("click", async event => {
    const copy = event.target.closest("[data-copy-tool]");
    if (copy) {
      event.preventDefault();
      const text = copy.closest(".chat-tool-section")?.querySelector("pre")?.textContent || "";
      try { await navigator.clipboard.writeText(text); copy.textContent = "Copied"; setTimeout(() => { if (copy.isConnected) copy.textContent = "Copy"; }, 1400); }
      catch { copy.textContent = "Select to copy"; }
    }
  });

  function renderAssistant(msg, live) {
    restoreDisclosures(msg);
    const article = document.createElement("article");
    article.className = "chat-turn is-assistant";
    article._message = msg;
    article._rebind = next => { msg = next; article._message = next; };
    article.setAttribute("aria-label", "Assistant response");
    article.innerHTML =
      '<div class="chat-assistant">' +
      '<div class="chat-flow" data-flow></div>' +
      '<details class="chat-citations" data-citations hidden><summary><span class="chat-source-badge" data-source-count></span></summary><div data-source-grid></div></details>' +
      '<p class="chat-error" data-err hidden></p>' +
      '<div class="chat-foot" data-foot hidden><span class="chat-foot-meta" data-meta></span><div class="chat-msg-actions" data-actions-host></div></div>' +
      "</div>";
    article.addEventListener("click", (event) => {
      const attention = event.target.closest("[data-activity-attention]");
      if (attention) {
        const activity = attention.closest("[data-activity]");
        const rows = activity.querySelectorAll("[data-tool]:is(.is-running,.is-error,.is-warning,.is-interrupted)");
        rows.forEach(revealToolRow);
        return;
      }
      const summaryBtn = event.target.closest("[data-activity-summary]");
      if (!summaryBtn || summaryBtn.tagName !== "BUTTON") return;
      const activityEl = summaryBtn.closest("[data-activity]");
      if (activityEl.classList.contains("is-waiting") || activityEl.classList.contains("is-bare")) return;
      const open = !activityEl.classList.contains("is-open");
      if (open) pauseThreadFollow();
      activityEl.classList.toggle("is-open", open);
      summaryBtn.setAttribute("aria-expanded", String(open));
      if (!msg.activityOpenMap) msg.activityOpenMap = {};
      msg.activityOpenMap[activityEl.dataset.activityBlock] = open;
      saveDisclosures(msg);
      if (open) requestAnimationFrame(() => {
        activityEl.querySelectorAll(".is-live-think .chat-think-preview").forEach(body => followThinking(body, true));
      });
    });
    article.addEventListener("click", (event) => {
      const thinkingSize = event.target.closest("[data-think-size]");
      if (thinkingSize) {
        const li = thinkingSize.closest("[data-step]");
        msg.stepOpen ||= {};
        if (li.dataset.thinkMode === "expanded" && isConsoleView()) msg.stepOpen[li.dataset.step] = false;
        else if (li.dataset.thinkMode === "expanded") delete msg.stepOpen[li.dataset.step];
        else msg.stepOpen[li.dataset.step] = true;
        syncThinkingMode(li, msg, true);
        const peek = li.querySelector("[data-reason-preview]");
        peek._thinking.follow = true;
        followThinking(peek);
        saveDisclosures(msg);
        return;
      }
      const expand = event.target.closest("[data-group-expand]");
      if (expand) {
        const group = expand.closest(".is-stack");
        const toggles = [...group.querySelectorAll(".chat-tool-children > .chat-step > .chat-step-main > .chat-step-head > .chat-step-toggle")];
        const open = !toggles.every(toggle => toggle.getAttribute("aria-expanded") === "true");
        toggles.forEach(toggle => { if ((toggle.getAttribute("aria-expanded") === "true") !== open) toggle.click(); });
        expand.textContent = open ? "Collapse results" : "Expand results";
        return;
      }
      const prev = event.target.closest("[data-prev-toggle]");
      if (prev) {
        event.preventDefault();
        const box = prev.closest("[data-prev]");
        const reveal = box && box.querySelector(":scope > .chat-step-reveal");
        if (!reveal) return;
        const open = !reveal.classList.contains("is-open");
        reveal.classList.toggle("is-open", open);
        prev.setAttribute("aria-expanded", String(open));
        if (!msg.stepOpen) msg.stepOpen = {};
        msg.stepOpen[box.dataset.prev] = open;
        saveDisclosures(msg);
        return;
      }
      const sub = event.target.closest("[data-substep-toggle]");
      if (sub) {
        event.preventDefault();
        const box = sub.closest("[data-substep]");
        const reveal = box && box.querySelector(".chat-step-reveal");
        if (!reveal) return;
        const open = !reveal.classList.contains("is-open");
        reveal.classList.toggle("is-open", open);
        sub.setAttribute("aria-expanded", String(open));
        if (!msg.stepOpen) msg.stepOpen = {};
        msg.stepOpen[box.dataset.substep] = open;
        saveDisclosures(msg);
        return;
      }
      const btn = event.target.closest("[data-step-toggle]");
      if (!btn || event.target.closest(".chat-step-open")) return;
      if (isConsoleView() && event.target.closest("a")) return;
      const li = btn.closest("[data-step]");
      if (!li) return;
      const reveal = li.querySelector(":scope > .chat-step-main > .chat-step-reveal");
      const hang = li.querySelector(":scope > .chat-step-main .chat-console-hang");
      const isThink = li.classList.contains("is-think");
      const isConsoleCall = li.classList.contains("is-console-call");
      const isShellGroup = li.classList.contains("is-console-shell-group");
      if (!reveal && !isThink && !isConsoleCall && !isShellGroup) return;
      if (isThink && li.dataset.thinkMode !== "collapsed" && String(window.getSelection() || "").trim()) return;
      event.preventDefault();
      const open = isThink
        ? li.dataset.thinkMode === "collapsed"
        : isConsoleCall
          ? !hang?.classList.contains("is-expanded")
          : isShellGroup
            ? !li.classList.contains("is-console-expanded")
            : !reveal.classList.contains("is-open");
      if (open && !isThink) pauseThreadFollow();
      if (isConsoleCall && hang) {
        hang.classList.toggle("is-expanded", open);
        li.classList.toggle("is-console-expanded", open);
      } else if (isShellGroup) {
        li.classList.toggle("is-console-expanded", open);
      } else if (reveal) {
        reveal.classList.toggle("is-revealing", open);
        setToolReveal(reveal, open);
      }
      li.querySelectorAll(":scope > .chat-step-main > .chat-step-head [data-step-toggle]").forEach((el) => el.setAttribute("aria-expanded", String(open)));
      if (!msg.stepOpen) msg.stepOpen = {};
      msg.stepOpen[li.dataset.step] = open;
      if (isThink) {
        syncThinkingMode(li, msg, true);
        li.classList.toggle("is-console-expanded", li.dataset.thinkMode === "expanded");
        const peek = li.querySelector("[data-reason-preview]");
        if (open && li.classList.contains("is-live-think")) {
          peek._thinking.follow = true;
          followThinking(peek, true);
        } else if (open) {
          stopThinkingFollow(peek);
          peek.scrollTop = 0;
        }
      }
      saveDisclosures(msg);
    });
    updateAssistant(article, msg, live);
    if (!live && !msg.noActions) mountActions(article, messages.indexOf(msg));
    return article;
  }

  function mountActions(article, index) {
    const host = article.querySelector("[data-actions-host]");
    if (!host || host.childElementCount) return;
    host.appendChild(msgActions(index, "assistant"));
  }

  async function hydratePermissionCards(flow) {
    flow.querySelectorAll("[data-perm-id]").forEach((card) => {
      if (card.dataset.hydrated === "1") return;
      card.dataset.hydrated = "1";
      const id = card.getAttribute("data-perm-id");
      const body = card.querySelector("[data-perm-body]");
      if (!id || !body) return;
      api("/skills/proposals/" + encodeURIComponent(id)).then((data) => {
        if (typeof data.body === "string") body.value = data.body;
      }).catch(() => {});
    });
  }

  if (log) {
    log.addEventListener("click", async (event) => {
      const save = event.target.closest("[data-perm-save]");
      const reject = event.target.closest("[data-perm-reject]");
      const card = event.target.closest("[data-perm-id]");
      if (!card || (!save && !reject)) return;
      const id = card.getAttribute("data-perm-id");
      try {
        if (reject) {
          await api("/skills/proposals/" + encodeURIComponent(id) + "/reject", { method: "POST", body: "{}" });
        } else {
          const body = {
            slug: card.querySelector("[data-perm-slug]")?.value,
            description: card.querySelector("[data-perm-desc]")?.value,
            body: card.querySelector("[data-perm-body]")?.value,
            enable: Boolean(card.querySelector("[data-perm-enable]")?.checked),
            replace: Boolean(card.querySelector("[data-perm-replace]")?.checked),
          };
          await api("/skills/proposals/" + encodeURIComponent(id) + "/confirm", { method: "POST", body: JSON.stringify(body) });
        }
        await refreshSkills();
        await reconcileThread();
      } catch (error) {
        showFailure("Skill was not saved", error);
      }
    });
  }

  function permissionCardHtml(perm, msg) {
    if (!perm) return "";
    if (perm.kind === "tool_permission") return "";
    const status = perm.status || "pending";
    const pending = status === "pending";
    const files = (perm.files || []).map((name) => escapeHtml(name)).join(" · ");
    const replace = perm.overwrite
      ? `<label class="hosts-field"><span><input type="checkbox" data-perm-replace> Replace existing \`${escapeHtml(perm.slug || "")}\`${perm.origin_existing ? ` (${escapeHtml(perm.origin_existing)})` : ""}</span></label>`
      : "";
    const actions = pending
      ? `<div class="chat-perm-actions"><button type="button" class="btn sm ghost" data-perm-reject>Discard</button><button type="button" class="btn sm" data-perm-save>Save skill</button></div>`
      : "";
    const fields = pending
      ? `<label class="hosts-field"><span>slug</span><input data-perm-slug maxlength="64" value="${escapeHtml(perm.slug || "")}"></label>
         <label class="hosts-field"><span>when</span><input data-perm-desc maxlength="1024" value="${escapeHtml(perm.description || "")}"></label>
         <label class="hosts-field"><span>body</span><textarea class="mono" data-perm-body rows="8"></textarea></label>
         <p class="usage-sub">${files || "SKILL.md"}</p>
         <label class="hosts-field"><span><input type="checkbox" data-perm-enable ${perm.enable === false ? "" : "checked"}> Enable after save</span></label>
         ${replace}`
      : `<p class="chat-perm-slug mono">${escapeHtml(perm.slug || "")}</p><p class="usage-sub">${escapeHtml(perm.description || "")}</p>`;
    return `<div class="chat-perm" data-perm-id="${escapeHtml(perm.id)}" data-msg="${escapeHtml(msg.id || "")}">
      <div class="chat-perm-head"><span>Skill proposal</span><span class="chat-perm-status">${escapeHtml(status)}</span></div>
      ${fields}${actions}
    </div>`;
  }

  // --- tool permission queue (all views) ---

  const permQueueEl = root.querySelector("[data-perm-queue]");
  const permQueueList = root.querySelector("[data-perm-queue-list]");
  const permHost = root.querySelector("[data-perm-host]");
  const permChip = root.querySelector("[data-perm-chip]");
  const permChipLabel = root.querySelector("[data-perm-chip-label]");
  let permsMenuEl = null;
  let permsOpen = false;
  let threadBypass = false;
  let threadAuto = false;
  let autoAvailable = Boolean(config.openrouter);
  let threadPermData = null;
  let permsHostTab = null;

  function acceptPermThread(data) {
    if (data && Object.prototype.hasOwnProperty.call(data, "bypass_permissions")) {
      threadBypass = Boolean(data.bypass_permissions);
    }
    if (data && Object.prototype.hasOwnProperty.call(data, "auto_permissions")) {
      threadAuto = Boolean(data.auto_permissions);
    }
    syncPermChip();
    permCards.clear();
    const live = data && data.run && data.run.message;
    if (live) syncPermQueueFromMessage(live);
    renderPermQueue();
  }

  function syncPermChip() {
    if (!permChip) return;
    const mode = threadBypass ? "bypass" : (threadAuto ? "auto" : "ask");
    permChip.classList.toggle("is-on", mode !== "ask");
    permChip.classList.toggle("is-auto", mode === "auto");
    permChip.setAttribute("aria-pressed", mode !== "ask" ? "true" : "false");
    permChip.setAttribute("aria-expanded", permsOpen ? "true" : "false");
    const title = mode === "bypass"
      ? "Bypass on — no permission prompts"
      : (mode === "auto" ? "Auto-approve on — Jev allows ordinary calls" : "Permission prompts on");
    permChip.setAttribute("title", title);
    permChip.setAttribute("aria-label", title);
    if (permChipLabel) permChipLabel.textContent = mode === "bypass" ? "Bypass on" : (mode === "auto" ? "Auto" : "Bypass");
  }

  const permCards = new Map();
  let permTick = 0;
  const PERM_TOTAL_S = 60;

  function upsertPermCard(rec, paint = true) {
    if (!rec || !rec.id) return;
    if (rec.status && rec.status !== "pending") {
      if (permCards.delete(rec.id) && paint) renderPermQueue();
      return;
    }
    const prev = permCards.get(rec.id) || {};
    const next = { ...prev, ...rec, status: "pending" };
    if (!(next.expires_at || next.expiresAt) && (prev.expires_at || prev.expiresAt)) {
      next.expires_at = prev.expires_at || prev.expiresAt;
    }
    const left = permLeftS(next.expires_at || next.expiresAt);
    if (left == null || left <= 0) {
      if (permCards.delete(rec.id) && paint) renderPermQueue();
      return;
    }
    permCards.set(rec.id, next);
    if (paint) renderPermQueue();
  }

  function syncPermQueueFromMessage(msg) {
    const rows = Array.isArray(msg && msg.permissions) ? msg.permissions : [];
    let changed = false;
    rows.forEach((perm) => {
      if (!perm || perm.kind !== "tool_permission" || !perm.id) return;
      if (perm.status === "pending") {
        const left = permLeftS(perm.expires_at || perm.expiresAt);
        if (left == null || left <= 0) {
          if (permCards.has(perm.id)) {
            permCards.delete(perm.id);
            changed = true;
          }
          return;
        }
        upsertPermCard(perm, false);
        changed = true;
      } else if (permCards.has(perm.id)) {
        permCards.delete(perm.id);
        changed = true;
      }
    });
    if (changed) renderPermQueue();
  }

  function seedPendingPermCards(panel) {
    // Subagent asks live in child runs, not the thread transcript: after a
    // reload or socket resync the durable pending list is the only way to
    // restore their cards. Stale entries self-drop via expires_at. Auto mode
    // still asks for secrets/destructive shells, so only bypass suppresses.
    if (!panel || !Array.isArray(panel.pending) || threadBypass) return;
    let changed = false;
    panel.pending.forEach((perm) => {
      if (!perm || perm.kind !== "tool_permission" || perm.status !== "pending" || !perm.id) return;
      upsertPermCard(perm, false);
      changed = true;
    });
    if (changed) renderPermQueue();
  }

  async function seedPendingFromServer(id) {
    try {
      const panel = await api(`/chat/threads/${encodeURIComponent(id)}/permissions`);
      if (id !== currentId) return;
      seedPendingPermCards(panel);
    } catch (error) { /* card replay is best effort */ }
  }

  function permToolLabel(tool) {
    return ({
      read_file: "Read file",
      write_file: "Write file",
      search_replace: "Edit file",
      list_dir: "List folder",
      grep: "Search folder",
      glob: "Find files",
      run_terminal_command: "Run command",
    })[tool] || (tool || "Action").replace(/_/g, " ");
  }

  function permGrantKind(rec) {
    if (rec.grant_kind === "file" || rec.grant_kind === "dir") return rec.grant_kind;
    if (rec.secret || rec.tool === "read_file" || rec.tool === "write_file" || rec.tool === "search_replace") return "file";
    if (rec.tool === "list_dir" || rec.tool === "grep" || rec.tool === "glob") return "dir";
    return rec.tool === "run_terminal_command" ? "dir" : "";
  }

  function permGrantPath(rec) {
    return String(rec.grant_value || rec.path || rec.cwd || "").trim();
  }

  function permCardCopy(rec) {
    const kind = permGrantKind(rec);
    const path = permGrantPath(rec);
    const noun = kind === "file" ? "file" : "folder";
    const verb = rec.secret ? "Read secret" : permToolLabel(rec.tool);
    const why = rec.secret ? "Secret-named file" : "Outside allowed folders";
    return {
      action: verb,
      noun,
      path,
      why,
      command: rec.tool === "run_terminal_command" ? String(rec.command || "").trim() : "",
      host: rec.host || "",
    };
  }

  function permLeftS(expiresAt) {
    const t = Date.parse(expiresAt || "");
    if (!Number.isFinite(t)) return null;
    return Math.max(0, (t - Date.now()) / 1000);
  }

  function fmtPermCountdown(expiresAt) {
    const left = permLeftS(expiresAt);
    if (left == null) return "";
    const s = Math.floor(left);
    return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
  }

  function permCardSignature(rec) {
    return [rec.tool, rec.host, rec.path, rec.cwd, rec.command, rec.requester, rec.secret ? "1" : "0", rec.grant_kind || "", rec.grant_value || "", (rec.reasons || []).join("|")].join("\0");
  }

  function parseCssRgb(raw, fallback) {
    const text = String(raw || "").trim() || fallback;
    if (text.charAt(0) === "#") {
      const hex = text.length === 4
        ? text.slice(1).split("").map((ch) => ch + ch).join("")
        : text.slice(1, 7);
      return [parseInt(hex.slice(0, 2), 16), parseInt(hex.slice(2, 4), 16), parseInt(hex.slice(4, 6), 16)];
    }
    const m = text.match(/rgba?\(\s*([0-9.]+)\s*[, ]\s*([0-9.]+)\s*[, ]\s*([0-9.]+)/);
    if (m) return [Number(m[1]), Number(m[2]), Number(m[3])];
    return parseCssRgb(fallback, "#B5E05A");
  }

  function mixRgb(a, b, t) {
    const u = Math.max(0, Math.min(1, t));
    return `rgb(${Math.round(a[0] + (b[0] - a[0]) * u)} ${Math.round(a[1] + (b[1] - a[1]) * u)} ${Math.round(a[2] + (b[2] - a[2]) * u)})`;
  }

  let _permThemeCache = { key: "", brass: null, yellow: null, red: null };
  function permThemeColors(el) {
    // Theme variables are identical for every card; resolve them once per
    // theme instead of forcing computed style on each card per frame.
    const root = document.documentElement;
    const key = root.getAttribute("data-theme") || "";
    if (_permThemeCache.key === key && _permThemeCache.brass) return _permThemeCache;
    const theme = getComputedStyle(el);
    _permThemeCache = {
      key,
      brass: parseCssRgb(theme.getPropertyValue("--brass"), "#B5E05A"),
      yellow: parseCssRgb(theme.getPropertyValue("--status-live"), "#FBBF24"),
      red: parseCssRgb(theme.getPropertyValue("--fault"), "#F87171"),
    };
    return _permThemeCache;
  }

  function permUrgencyColor(el, frac) {
    const { brass, yellow, red } = permThemeColors(el);
    const spent = 1 - Math.max(0, Math.min(1, frac));
    if (spent < 0.45) return mixRgb(brass, yellow, spent / 0.45);
    return mixRgb(yellow, red, (spent - 0.45) / 0.55);
  }

  function permOutcomeLabel(rec) {
    if (rec.status === "allowed") {
      if (rec.grant_scope === "allow_host") return "Allowed · always on this host";
      if (rec.grant_scope === "allow_session") return "Allowed · this session";
      return "Allowed";
    }
    if (rec.status === "timeout") return "Timed out";
    if (rec.status === "cancelled") return "Cancelled";
    return "Denied";
  }

  function permQueueCardHtml(rec) {
    if (rec.status !== "pending") return "";
    const copy = permCardCopy(rec);
    const who = rec.requester && rec.requester !== "Main agent"
      ? `<span class="chat-perm-card-agent is-sub">Subagent</span><span class="chat-perm-card-who">${escapeHtml(rec.requester)}</span>`
      : `<span class="chat-perm-card-agent">Main</span>`;
    const bits = [copy.action, copy.host, copy.why].filter(Boolean);
    const meta = bits.length ? `<span class="chat-perm-card-meta">${escapeHtml(bits.join(" · "))}</span>` : "";
    const grant = copy.path
      ? `<p class="chat-perm-card-grant"><span class="chat-perm-card-kind">${escapeHtml(copy.noun)}</span><span class="mono chat-perm-card-path" title="${escapeHtml(copy.path)}">${escapeHtml(copy.path)}</span></p>`
      : "";
    const body = copy.command
      ? `${grant}<pre class="chat-perm-card-cmd mono">${escapeHtml(copy.command)}</pre>`
      : (grant || `<p class="chat-perm-card-detail mono">${escapeHtml(copy.path || "this location")}</p>`);
    const expires = rec.expires_at || rec.expiresAt || "";
    const left = permLeftS(expires);
    const frac = left == null ? 1 : Math.max(0, Math.min(1, left / PERM_TOTAL_S));
    return `<li class="chat-perm-card is-pending${left != null && left <= 15 ? " is-low" : ""}" data-perm-card="${escapeHtml(rec.id)}" data-perm-sig="${escapeHtml(permCardSignature(rec))}" role="alert" style="--perm-left:${frac}">
      <div class="chat-perm-card-row">
        <span class="chat-perm-card-dot" aria-hidden="true"></span>
        ${who}${meta}
        <span class="chat-perm-card-count mono" data-perm-count>${escapeHtml(fmtPermCountdown(expires))}</span>
        <div class="chat-perm-card-actions">
          <button type="button" class="chat-perm-act" data-perm-decide="deny">Deny</button>
          <span class="chat-perm-always">
            <button type="button" class="chat-perm-act is-allow" data-perm-decide="allow">Allow</button>
            <button type="button" class="chat-perm-act is-split" data-perm-always aria-expanded="false" aria-label="Allow always options">
              <svg viewBox="0 0 16 16" width="10" height="10" aria-hidden="true"><path d="M3.2 10.2 8 5.4l4.8 4.8" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/></svg>
            </button>
            <div class="chat-pop chat-perm-always-menu" data-perm-always-menu hidden>
              <button type="button" data-perm-decide="allow_session">Allow this ${escapeHtml(copy.noun)} in this chat</button>
              <button type="button" data-perm-decide="allow_host">Always allow this ${escapeHtml(copy.noun)}</button>
            </div>
          </span>
        </div>
      </div>
      ${body}
      <span class="chat-perm-card-bar" data-perm-bar aria-hidden="true"></span>
    </li>`;
  }

  function renderPermQueue() {
    if (!permQueueEl || !permQueueList) return;
    for (const [id, rec] of permCards) {
      const left = permLeftS(rec.expires_at || rec.expiresAt);
      if (rec.status !== "pending" || left == null || left <= 0) permCards.delete(id);
    }
    const pending = threadBypass
      ? []
      : [...permCards.values()].filter((rec) => rec.status === "pending" && permLeftS(rec.expires_at || rec.expiresAt) > 0);
    const want = new Set(pending.map((rec) => rec.id));
    [...permQueueList.children].forEach((el) => {
      const id = el.getAttribute("data-perm-card");
      if (!want.has(id)) el.remove();
    });
    pending.forEach((rec, index) => {
      let el = permQueueList.querySelector(`[data-perm-card="${CSS.escape(rec.id)}"]`);
      const sig = permCardSignature(rec);
      const depth = Math.min(index, 2);
      if (index > 2) {
        if (el) el.remove();
        return;
      }
      if (!el) {
        permQueueList.insertAdjacentHTML("beforeend", permQueueCardHtml(rec));
        el = permQueueList.lastElementChild;
      } else if (el.getAttribute("data-perm-sig") !== sig) {
        const wrap = document.createElement("template");
        wrap.innerHTML = permQueueCardHtml(rec);
        const next = wrap.content.firstElementChild;
        el.replaceWith(next);
        el = next;
      }
      if (el) {
        el.setAttribute("data-perm-sig", sig);
        el.classList.toggle("is-front", index === 0);
        el.setAttribute("aria-hidden", index === 0 ? "false" : "true");
        el.style.setProperty("--perm-depth", String(depth));
      }
      if (el && permQueueList.children[index] !== el) permQueueList.insertBefore(el, permQueueList.children[index] || null);
    });
    const hasPending = pending.length > 0;
    permQueueEl.hidden = !hasPending;
    permQueueEl.classList.toggle("is-stacked", pending.length > 1);
    let badge = permQueueEl.querySelector("[data-perm-queue-count]");
    if (pending.length > 1) {
      if (!badge) {
        badge = document.createElement("span");
        badge.className = "chat-perm-queue-count mono";
        badge.setAttribute("data-perm-queue-count", "");
        permQueueEl.insertBefore(badge, permQueueList);
      }
      badge.textContent = pending.length + " waiting";
    } else if (badge) {
      badge.remove();
    }
    paintPermCountdowns();
    if (hasPending) startPermTick();
    else stopPermTick();
  }

  function startPermTick() {
    if (permTick) return;
    const loop = () => {
      permTick = 0;
      paintPermCountdowns();
      if (permQueueEl && !permQueueEl.hidden) startPermTick();
    };
    permTick = requestAnimationFrame(loop);
  }

  function stopPermTick() {
    if (!permTick) return;
    cancelAnimationFrame(permTick);
    permTick = 0;
  }

  function paintPermCountdowns() {
    let anyPending = false;
    let dropped = false;
    for (const rec of [...permCards.values()]) {
      if (rec.status !== "pending") continue;
      const left = permLeftS(rec.expires_at || rec.expiresAt);
      if (left == null || left <= 0) {
        permCards.delete(rec.id);
        dropped = true;
        continue;
      }
      anyPending = true;
      const card = permQueueList && permQueueList.querySelector(`[data-perm-card="${CSS.escape(rec.id)}"]`);
      if (!card) continue;
      const expires = rec.expires_at || rec.expiresAt;
      const frac = Math.max(0, Math.min(1, left / PERM_TOTAL_S));
      const count = card.querySelector("[data-perm-count]");
      if (count) count.textContent = fmtPermCountdown(expires);
      card.style.setProperty("--perm-left", String(frac));
      card.style.setProperty("--perm-color", permUrgencyColor(card, frac));
      card.classList.toggle("is-low", left <= 15);
    }
    if (dropped) renderPermQueue();
    else if (!anyPending) stopPermTick();
  }

  async function postPermDecision(id, action) {
    if (!currentId) return;
    try {
      const data = await api(`/chat/threads/${encodeURIComponent(currentId)}/permissions/${encodeURIComponent(id)}`, {
        method: "POST",
        body: JSON.stringify({ action }),
      });
      permCards.delete(id);
      renderPermQueue();
      if (permsOpen && (action === "allow_session" || action === "allow_host")) refreshPerms();
    } catch (error) {
      showFailure("Permission decision failed", error);
    }
  }

  if (permQueueList) {
    permQueueList.addEventListener("click", (event) => {
      const alwaysBtn = event.target.closest("[data-perm-always]");
      if (alwaysBtn) {
        const wrap = alwaysBtn.closest(".chat-perm-always");
        const menu = wrap && wrap.querySelector("[data-perm-always-menu]");
        if (menu) {
          menu.hidden = !menu.hidden;
          alwaysBtn.setAttribute("aria-expanded", menu.hidden ? "false" : "true");
        }
        return;
      }
      const btn = event.target.closest("[data-perm-decide]");
      if (!btn) return;
      const card = btn.closest("[data-perm-card]");
      if (!card) return;
      const id = card.getAttribute("data-perm-card");
      const action = btn.getAttribute("data-perm-decide");
      if (action) postPermDecision(id, action);
    });
  }

  function sweepStuckPermCards() {
    permCards.clear();
    renderPermQueue();
  }

  function bindPermsMenu(menu) {
    const bypass = menu.querySelector("[data-perms-bypass]");
    const auto = menu.querySelector("[data-perms-auto]");
    const closeBtn = menu.querySelector("[data-perms-close]");
    const tabsEl = menu.querySelector("[data-perms-tabs]");
    const pathsEl = menu.querySelector("[data-perms-paths]");
    menu.addEventListener("click", (event) => event.stopPropagation());
    if (closeBtn) closeBtn.addEventListener("click", () => setPermsOpen(false));
    if (bypass) bypass.addEventListener("change", () => setThreadBypass(bypass.checked));
    if (auto) auto.addEventListener("change", () => setThreadAuto(auto.checked));
    if (tabsEl) {
      tabsEl.addEventListener("click", (event) => {
        const btn = event.target.closest("[data-perms-host]");
        if (!btn) return;
        event.preventDefault();
        event.stopPropagation();
        const hid = Number(btn.getAttribute("data-perms-host"));
        if (!Number.isFinite(hid) || hid === permsHostTab) return;
        permsHostTab = hid;
        paintPermsPanel();
      });
    }
    if (pathsEl) {
      pathsEl.addEventListener("click", async (event) => {
        const btn = event.target.closest("[data-perm-revoke]");
        if (!btn) return;
        const row = btn.closest("[data-perm-grant]");
        const gid = row && row.getAttribute("data-perm-grant");
        if (!gid) return;
        btn.disabled = true;
        try {
          await api(`/chat/permissions/grants/${encodeURIComponent(gid)}`, { method: "DELETE" });
          refreshPerms();
        } catch (error) {
          btn.disabled = false;
          showFailure("Could not revoke grant", error);
        }
      });
    }
  }

  function renderPermsMenu() {
    if (!permHost) return;
    if (permsMenuEl) permsMenuEl.remove();
    const menu = document.createElement("div");
    menu.className = "chat-perms-menu" + (pickerSheetQuery.matches ? " is-sheet" : "");
    menu.setAttribute("role", "dialog");
    menu.setAttribute("aria-label", "Permissions");
    menu.hidden = true;
    menu.innerHTML =
      '<div class="chat-picker-head"><p>Permissions</p><button type="button" class="chat-menu-close" data-perms-close aria-label="Close"><svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><path d="M3.5 3.5l9 9M12.5 3.5l-9 9" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg></button></div>' +
      '<div class="chat-perms-body">' +
      '<label class="chat-perms-check"><input type="checkbox" data-perms-bypass><span class="chat-perms-check-box" aria-hidden="true"></span><span class="chat-perms-check-copy"><strong>Bypass prompts</strong><small>Skip asking. Read-only projects still apply.</small></span></label>' +
      '<label class="chat-perms-check"><input type="checkbox" data-perms-auto><span class="chat-perms-check-box" aria-hidden="true"></span><span class="chat-perms-check-copy"><strong>Auto-approve</strong><small data-perms-auto-hint>Jev allows ordinary out-of-scope calls. Unsure or dangerous still asks.</small></span></label>' +
      '<div class="chat-perms-section"><p class="chat-perms-heading">Working directories</p><div class="chat-perms-tabs" data-perms-tabs role="tablist" aria-label="Hosts"></div><ul class="chat-perms-paths" data-perms-paths></ul><p class="chat-perms-empty" data-perms-empty hidden>No access on this host.</p></div>' +
      "</div>";
    bindPermsMenu(menu);
    if (pickerSheetQuery.matches) root.appendChild(menu);
    else permHost.appendChild(menu);
    permsMenuEl = menu;
  }

  function placePermsMenu() {
    if (!permsMenuEl || !permChip || pickerSheetQuery.matches) return;
    const chip = permChip.getBoundingClientRect();
    const width = Math.min(384, Math.max(280, window.innerWidth - 24));
    let left = Math.round(chip.right - width);
    left = Math.max(12, Math.min(left, window.innerWidth - width - 12));
    permsMenuEl.style.position = "fixed";
    permsMenuEl.style.left = left + "px";
    permsMenuEl.style.right = "auto";
    permsMenuEl.style.bottom = Math.max(12, window.innerHeight - chip.top + 8) + "px";
    permsMenuEl.style.width = width + "px";
    permsMenuEl.style.zIndex = "40";
  }

  function setPermsOpen(open) {
    const next = Boolean(open);
    if (!permHost) return;
    if (next === permsOpen) {
      if (next) refreshPerms();
      return;
    }
    permsOpen = next;
    if (next) {
      closePicker();
      overlayPush("perms");
      if (!permsMenuEl) renderPermsMenu();
      else permsMenuEl.classList.toggle("is-sheet", pickerSheetQuery.matches);
      if (permsMenuEl) permsMenuEl.hidden = false;
      placePermsMenu();
      if (threadPermData) paintPermsPanel();
      refreshPerms();
    } else {
      overlayDrop("perms");
      if (permsMenuEl) permsMenuEl.hidden = true;
    }
    syncPermChip();
  }

  function permHostList() {
    return Array.isArray(threadPermData && threadPermData.hosts) ? threadPermData.hosts : [];
  }

  function permSelectedHost() {
    const hosts = permHostList();
    if (!hosts.length) return null;
    const wanted = Number(permsHostTab);
    const match = hosts.find((h) => Number(h.id) === wanted);
    if (match) return match;
    const threadId = Number(threadPermData && threadPermData.thread_host_id);
    return hosts.find((h) => Number(h.id) === threadId) || hosts.find((h) => h.active) || hosts[0];
  }

  function paintPermsPanel() {
    if (!permsMenuEl) return;
    const tabsEl = permsMenuEl.querySelector("[data-perms-tabs]");
    const pathsEl = permsMenuEl.querySelector("[data-perms-paths]");
    const emptyEl = permsMenuEl.querySelector("[data-perms-empty]");
    const bypassInput = permsMenuEl.querySelector("[data-perms-bypass]");
    const autoInput = permsMenuEl.querySelector("[data-perms-auto]");
    const autoHint = permsMenuEl.querySelector("[data-perms-auto-hint]");
    const hosts = permHostList();
    const selected = permSelectedHost();
    if (selected) permsHostTab = Number(selected.id);
    if (bypassInput) bypassInput.checked = threadBypass;
    if (autoInput) {
      autoInput.checked = threadAuto;
      autoInput.disabled = !autoAvailable;
    }
    if (autoHint) {
      autoHint.textContent = autoAvailable
        ? "Jev allows ordinary out-of-scope calls. Unsure or dangerous still asks."
        : "Add an OpenRouter key on Sources to enable auto-approve.";
    }
    if (tabsEl) {
      tabsEl.hidden = hosts.length === 0;
      const want = hosts.map((h) => String(h.id));
      const have = [...tabsEl.querySelectorAll("[data-perms-host]")].map((el) => el.getAttribute("data-perms-host"));
      const same = want.length === have.length && want.every((id, i) => id === have[i]);
      if (!same) {
        tabsEl.innerHTML = hosts.map((h) => {
          const on = selected && Number(h.id) === Number(selected.id);
          return `<button type="button" class="chat-perms-tab${on ? " is-on" : ""}" role="tab" aria-selected="${on ? "true" : "false"}" data-perms-host="${Number(h.id)}">${escapeHtml(h.host || "host")}</button>`;
        }).join("");
      } else {
        [...tabsEl.querySelectorAll("[data-perms-host]")].forEach((el) => {
          const on = selected && Number(el.getAttribute("data-perms-host")) === Number(selected.id);
          el.classList.toggle("is-on", on);
          el.setAttribute("aria-selected", on ? "true" : "false");
        });
      }
    }
    const roots = (selected && selected.roots) || [];
    const grants = (selected && selected.grants) || [];
    const rows = [];
    roots.forEach((root) => {
      const path = String((root && root.path) || "");
      if (!path) return;
      rows.push(`<li class="is-root" title="${escapeHtml(path)}"><span class="mono chat-perms-path">${escapeHtml(path)}</span></li>`);
    });
    grants.forEach((g) => {
      const path = String(g.value || "");
      const scope = g.scope === "host" ? "always" : "session";
      rows.push(`<li class="is-grant" data-perm-grant="${Number(g.id)}" title="${escapeHtml(path)}">
        <span class="mono chat-perms-path">${escapeHtml(path)}</span>
        <span class="chat-perms-scope">${escapeHtml(scope)}</span>
        <button type="button" class="chat-perms-revoke" data-perm-revoke aria-label="Revoke ${escapeHtml(path)}">
          <svg viewBox="0 0 16 16" width="12" height="12" aria-hidden="true"><path d="M3.5 3.5l9 9M12.5 3.5l-9 9" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>
        </button>
      </li>`);
    });
    if (pathsEl) {
      pathsEl.innerHTML = rows.join("");
      pathsEl.hidden = rows.length === 0;
    }
    if (emptyEl) emptyEl.hidden = rows.length > 0;
  }

  async function refreshPerms() {
    if (!currentId) return;
    if (!permsMenuEl) renderPermsMenu();
    try {
      threadPermData = await api(`/chat/threads/${encodeURIComponent(currentId)}/permissions`);
      threadBypass = Boolean(threadPermData.bypass);
      threadAuto = Boolean(threadPermData.auto);
      autoAvailable = Boolean(threadPermData.auto_available);
      if (threadPermData.auto_available === false) threadAuto = false;
      seedPendingPermCards(threadPermData);
      syncPermChip();
      paintPermsPanel();
    } catch (error) {
      if (permsMenuEl) {
        const pathsEl = permsMenuEl.querySelector("[data-perms-paths]");
        const tabsEl = permsMenuEl.querySelector("[data-perms-tabs]");
        if (pathsEl) pathsEl.innerHTML = "";
        if (tabsEl) tabsEl.innerHTML = "";
      }
    }
  }

  async function setThreadBypass(next) {
    if (!currentId) return;
    const bypassInput = permsMenuEl && permsMenuEl.querySelector("[data-perms-bypass]");
    try {
      const data = await api(`/chat/threads/${encodeURIComponent(currentId)}`, {
        method: "PATCH",
        body: JSON.stringify({ bypass_permissions: next, expected_revision: serverRevision }),
      });
      threadBypass = Boolean(data && data.bypass_permissions);
      if (data && data.revision != null) serverRevision = data.revision;
      syncPermChip();
      if (bypassInput) bypassInput.checked = threadBypass;
      if (next) {
        permCards.clear();
        renderPermQueue();
      }
    } catch (error) {
      showFailure("Could not change bypass", error);
      if (bypassInput) bypassInput.checked = threadBypass;
    }
  }

  async function setThreadAuto(next) {
    if (!currentId) return;
    const autoInput = permsMenuEl && permsMenuEl.querySelector("[data-perms-auto]");
    if (!autoAvailable) {
      if (autoInput) autoInput.checked = false;
      return;
    }
    try {
      const data = await api(`/chat/threads/${encodeURIComponent(currentId)}`, {
        method: "PATCH",
        body: JSON.stringify({ auto_permissions: next, expected_revision: serverRevision }),
      });
      threadAuto = data && Object.prototype.hasOwnProperty.call(data, "auto_permissions")
        ? Boolean(data.auto_permissions)
        : Boolean(next);
      if (data && data.revision != null) serverRevision = data.revision;
      syncPermChip();
      if (autoInput) autoInput.checked = threadAuto;
    } catch (error) {
      showFailure("Could not change auto-approve", error);
      if (autoInput) autoInput.checked = threadAuto;
    }
  }

  if (permChip) {
    permChip.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
      setPermsOpen(!permsOpen);
    });
  }
  document.addEventListener("click", (event) => {
    if (!permsOpen) return;
    const path = typeof event.composedPath === "function" ? event.composedPath() : [];
    const inside = path.some((node) => {
      if (!node || node.nodeType !== 1) return false;
      return node === permHost || node === permsMenuEl || node.classList.contains("chat-perms-menu") || node.hasAttribute("data-perm-host");
    });
    if (inside) return;
    if (event.target.closest("[data-perm-host], .chat-perms-menu")) return;
    setPermsOpen(false);
  });
  pickerSheetQuery.addEventListener("change", () => {
    if (permsMenuEl) {
      permsMenuEl.remove();
      permsMenuEl = null;
    }
    if (permsOpen) {
      renderPermsMenu();
      if (permsMenuEl) permsMenuEl.hidden = false;
      placePermsMenu();
      if (threadPermData) paintPermsPanel();
      refreshPerms();
    }
  });
  window.addEventListener("resize", () => { if (permsOpen) placePermsMenu(); });

  function assistantSegments(msg) {
    if (Array.isArray(msg.segments)) return msg.segments;
    const legacy = [];
    if (msg.reasoning) legacy.push({ type: "reasoning", text: msg.reasoning });
    (msg.tools || []).forEach((trace) => {
      if (trace.id) legacy.push({ type: "tool", id: trace.id });
    });
    (msg.permissions || []).forEach((perm) => {
      if (perm.id && perm.kind !== "tool_permission") legacy.push({ type: "permission", id: perm.id });
    });
    if (msg.content) legacy.push({ type: "content", text: msg.content });
    return legacy;
  }

  function consoleThoughtCounts(tools) {
    const names = (tools || []).map((item) => item.trace && item.trace.name);
    const reads = names.filter((name) => name === "read_file").length;
    const writes = names.filter((name) => name === "write_file").length;
    const edits = names.filter((name) => name === "search_replace").length;
    const shells = names.filter((name) => name === "run_terminal_command" || name === "get_command_output").length;
    const fetches = names.filter((name) => name === "read_url").length;
    const searches = names.filter((name) => name === "grep" || name === "glob" || isSearchTool(name)).length;
    const bits = [];
    if (reads) bits.push(`read ${reads} ${reads === 1 ? "file" : "files"}`);
    if (writes) bits.push(`wrote ${writes} ${writes === 1 ? "file" : "files"}`);
    if (edits) bits.push(`edited ${edits} ${edits === 1 ? "file" : "files"}`);
    if (shells) bits.push(`ran ${shells} shell ${shells === 1 ? "command" : "commands"}`);
    if (searches) bits.push(`searched for ${searches} ${searches === 1 ? "pattern" : "patterns"}`);
    if (fetches) bits.push(`read ${fetches} ${fetches === 1 ? "page" : "pages"}`);
    return bits;
  }

  const CONSOLE_THOUGHT_VERBS = ["Thought", "Pondered", "Cogitated", "Ruminated", "Mused", "Considered"];

  function consoleThoughtVerb(key) {
    const s = String(key || "thought");
    let h = 2166136261;
    for (let i = 0; i < s.length; i += 1) h = Math.imul(h ^ s.charCodeAt(i), 16777619);
    return CONSOLE_THOUGHT_VERBS[(h >>> 0) % CONSOLE_THOUGHT_VERBS.length];
  }

  function consoleThoughtClock(seconds) {
    const total = Math.max(1, Math.round(Number(seconds) || 0));
    if (total < 60) return `${total}s`;
    const m = Math.floor(total / 60);
    const s = total % 60;
    return s ? `${m}m ${s}s` : `${m}m`;
  }

  function consoleThoughtSummary(key, seconds) {
    return `${consoleThoughtVerb(key)} for ${consoleThoughtClock(seconds)}`;
  }

  function consoleFreezeThoughtTimes(msg, live) {
    if (!msg._thoughtMs) msg._thoughtMs = {};
    if (!msg._thoughtClock) msg._thoughtClock = { liveKey: "", liveStart: 0 };
    const segs = Array.isArray(msg.segments) ? msg.segments : [];
    const groups = [];
    for (let i = 0; i < segs.length; i += 1) {
      if (!segs[i] || segs[i].type !== "reasoning") continue;
      let j = i;
      while (j + 1 < segs.length && segs[j + 1] && segs[j + 1].type === "reasoning") j += 1;
      groups.push({ key: "think-" + j, index: j });
      i = j;
    }
    const elapsed = (liveElapsedSeconds(msg, live) || 0) * 1000;
    const last = groups[groups.length - 1];
    const liveKey = live && last && !segs.some((seg, index) => index > last.index) ? last.key : "";
    const clock = msg._thoughtClock;
    if (clock.liveKey && clock.liveKey !== liveKey) {
      if (msg._thoughtMs[clock.liveKey] == null) {
        msg._thoughtMs[clock.liveKey] = Math.max(1000, elapsed - (clock.liveStart || 0));
      }
    }
    if (liveKey && clock.liveKey !== liveKey) {
      clock.liveKey = liveKey;
      clock.liveStart = elapsed;
    } else if (!liveKey) {
      clock.liveKey = "";
    }
    if (!live) {
      let toolMs = 0;
      (msg.tools || []).forEach((trace) => {
        const n = Number(trace.call_duration_ms ?? trace.execution_duration_ms ?? trace.elapsed_ms ?? trace.latency_ms);
        if (Number.isFinite(n) && n > 0) toolMs += n;
      });
      const total = Number(msg.elapsedMs);
      const budget = Math.max(groups.length * 1000, (Number.isFinite(total) ? total : elapsed) - toolMs);
      const weights = groups.map((group) => {
        const seg = segs[group.index];
        return Math.max(1, String(seg && seg.text || "").length);
      });
      const sum = weights.reduce((a, b) => a + b, 0) || 1;
      let assigned = 0;
      const missing = groups.filter((group) => msg._thoughtMs[group.key] == null);
      missing.forEach((group, i) => {
        const share = i === missing.length - 1
          ? Math.max(1000, budget - assigned)
          : Math.max(1000, Math.round(budget * (weights[groups.indexOf(group)] / sum)));
        msg._thoughtMs[group.key] = share;
        assigned += share;
      });
      clock.liveKey = "";
    }
    return msg._thoughtMs;
  }

  function consoleShellGroupHtml(items, live, openMap) {
    const first = items[0];
    const key = "shell-group-" + toolKey(first.trace, first.index);
    const open = stepOpenAt(openMap, key);
    const inner = items.map((item) => toolStepHtml(item.trace, toolKey(item.trace, item.index), live, stepOpenAt(openMap, toolKey(item.trace, item.index)), openMap)).join("");
    return (
      `<li class="chat-step is-console-shell-group${open ? " is-console-expanded" : ""}" data-step="${escapeHtml(key)}">` +
      `<div class="chat-step-main">` +
      `<div class="chat-step-head">` +
      `<button type="button" class="chat-step-toggle" data-step-toggle aria-expanded="${open ? "true" : "false"}">` +
      `<span class="chat-step-label">Ran ${items.length} shell commands</span>` +
      `</button></div>` +
      `<div class="chat-console-bundle"><ol class="chat-timeline">${inner}</ol></div>` +
      `</div></li>`
    );
  }

  function consoleToolList(items, msg, live, openMap) {
    const parts = [];
    let i = 0;
    while (i < items.length) {
      const name = items[i].trace && items[i].trace.name;
      if (name === "run_terminal_command") {
        let j = i + 1;
        while (j < items.length && items[j].trace && items[j].trace.name === "run_terminal_command") j += 1;
        const group = items.slice(i, j);
        if (group.length >= 2) parts.push(consoleShellGroupHtml(group, live, openMap));
        else {
          const item = group[0];
          parts.push(toolStepHtml(item.trace, toolKey(item.trace, item.index), live, stepOpenAt(openMap, toolKey(item.trace, item.index)), openMap));
        }
        i = j;
        continue;
      }
      const item = items[i];
      parts.push(toolStepHtml(item.trace, toolKey(item.trace, item.index), live, stepOpenAt(openMap, toolKey(item.trace, item.index)), openMap));
      i += 1;
    }
    return parts;
  }

  function consoleThinkStepHtml(text, key, open, extras) {
    const summary = extras && extras.summary ? extras.summary : "Thought";
    const toolsHtml = extras && extras.toolsHtml ? extras.toolsHtml : "";
    const reasonIds = extras && extras.reasonIds ? extras.reasonIds : "";
    return (
      `<li class="chat-step is-think is-console-think${open ? " is-console-expanded" : ""}" data-think-mode="${open ? "expanded" : "collapsed"}" data-step="${escapeHtml(key)}"${reasonIds ? ` data-reason-ids="${escapeHtml(reasonIds)}"` : ""}>` +
      `<div class="chat-step-main">` +
      `<div class="chat-step-head">` +
      `<button type="button" class="chat-step-toggle" data-step-toggle aria-expanded="${open ? "true" : "false"}">` +
      `<span class="chat-step-copy"><span class="chat-step-kind">${thinkLabel()}</span>` +
      `<span class="chat-step-label chat-think-summary">${escapeHtml(summary)}</span></span>` +
      `</button>` +
      `<span class="chat-step-state is-empty" aria-hidden="true"></span>` +
      `<button type="button" class="chat-think-size" data-think-size hidden>More</button>` +
      `</div>` +
      `<div class="chat-console-bundle">` +
      (toolsHtml ? `<ol class="chat-console-bundle-tools chat-timeline">${toolsHtml}</ol>` : "") +
      `<div class="chat-think-window" data-step-toggle>` +
      `<div class="chat-think-preview" data-reason-preview="${escapeHtml(key.slice(6))}" role="region" aria-label="Thinking" tabindex="${open ? "0" : "-1"}"><div class="chat-think-markdown"></div></div>` +
      `</div>` +
      `</div></div></li>`
    );
  }

  function consoleActivityEntriesHtml(entries, msg, live) {
    const parts = [];
    const stepOpen = msg.stepOpen || {};
    const times = consoleFreezeThoughtTimes(msg, live);
    const segs = Array.isArray(msg.segments) ? msg.segments : [];
    let lastReasonIndex = -1;
    segs.forEach((seg, index) => { if (seg && seg.type === "reasoning") lastReasonIndex = index; });
    const liveLastThink = Boolean(live && lastReasonIndex >= 0 && !segs.some((seg, index) => index > lastReasonIndex));
    let i = 0;
    while (i < entries.length) {
      const entry = entries[i];
      if (entry.type === "reasoning") {
        const reasons = [entry];
        let j = i + 1;
        while (j < entries.length && entries[j].type === "reasoning") {
          reasons.push(entries[j]);
          j += 1;
        }
        const last = reasons[reasons.length - 1];
        const joined = reasons.map((item) => String(item.text || "").trim()).filter(Boolean).join("\n\n");
        const isLiveThis = liveLastThink && last.segmentIndex === lastReasonIndex;
        {
          const key = `think-${last.segmentIndex}`;
          const open = Object.prototype.hasOwnProperty.call(stepOpen, key) ? thinkOpenAt(stepOpen, key) : isLiveThis;
          parts.push(consoleThinkStepHtml(joined, key, open, {
            summary: isLiveThis ? "Thinking…" : consoleThoughtSummary(key, (times[key] || 1000) / 1000),
            reasonIds: reasons.map((item) => item.segmentIndex).join(","),
          }));
        }
        i = j;
        continue;
      }
      const tools = [];
      while (i < entries.length && entries[i].type === "tool") {
        tools.push(entries[i]);
        i += 1;
      }
      parts.push(...consoleToolList(tools, msg, live, stepOpen));
    }
    return parts.join("");
  }

  function activityEntriesHtml(entries, msg, live, blockKey) {
    if (isConsoleView()) return consoleActivityEntriesHtml(entries, msg, live);
    const parts = [];
    const stepOpen = msg.stepOpen || {};
    let pendingTools = [];
    let groupIndex = 0;
    const flushTools = () => {
      if (!pendingTools.length) return;
      const groups = [];
      pendingTools.forEach((item) => {
        const last = groups[groups.length - 1];
        const key = stackKey(item.trace.name);
        if (viewGroupsAdjacent() && last && last.key === key) last.items.push(item);
        else groups.push({ name: item.trace.name || "unknown", key, items: [item] });
      });
      groups.forEach((group) => {
        if (group.items.length === 1) {
          const item = group.items[0];
          const key = toolKey(item.trace, item.index);
          parts.push(toolStepHtml(item.trace, key, live, stepOpenAt(stepOpen, key), stepOpen));
        } else if (isSearchTool(group.name) || group.name === "read_url" || isStackableWorkspace(group.name) || group.key === "shell" || group.key === "job" || group.key === "spawn" || group.key === "wait" || group.key === "agents") {
          const first = group.items[0];
          parts.push(groupStepHtml(group, toolKey(first.trace, first.index), live, stepOpen));
        } else {
          group.items.forEach((item) => {
            const key = toolKey(item.trace, item.index);
            parts.push(toolStepHtml(item.trace, key, live, stepOpenAt(stepOpen, key), stepOpen));
          });
        }
      });
      pendingTools = [];
    };
    entries.forEach((entry) => {
      if (entry.type === "tool") {
        pendingTools.push(entry);
        return;
      }
      flushTools();
      const key = `think-${entry.segmentIndex}`;
      parts.push(thinkStepHtml(entry.text, key, thinkOpenAt(stepOpen, key)));
    });
    flushTools();
    return parts.join("");
  }

  function activityBlockHtml(entries, msg, live, blockIndex, showElapsed, turnLive = live) {
    const key = String(blockIndex);
    const aid = "chat-activity-" + ++activitySeq;
    const traces = entries.filter((entry) => entry.type === "tool").map((entry) => entry.trace);
    const reasoning = entries.filter((entry) => entry.type === "reasoning").map((entry) => entry.text).join("");
    const groups = groupTraces(traces);
    const waiting = live && !entries.length;
    if (waiting) return "";
    const blockMsg = { ...msg, reasoning, content: "" };
    const kind = activityKind(blockMsg, groups, live);
    const defaultOpen = Boolean(live || !viewCollapsesActivity());
    msg.activityOpenMap ||= {};
    if (!(key in msg.activityOpenMap)) msg.activityOpenMap[key] = defaultOpen;
    const open = msg.activityOpenMap?.[key] ?? defaultOpen;
    const steps = activityEntriesHtml(entries, msg, turnLive || Boolean(msg.tools_live), key);
    const classes = ["chat-activity"];
    if (open || isHarnessView()) classes.push("is-open");
    // A model wait/background handoff must not hide already-rendered tools.
    if (kind) classes.push(kind === "waiting" ? "is-pending-phase" : `is-${kind}`);
    if (isHarnessView()) classes.push("is-bare");
    if (!live && msg.stopped) classes.push("is-stopped");
    if (!live && msg.error) classes.push("is-failed");
    const label = escapeHtml(activityLabel(blockMsg, groups, live));
    const elapsed = showElapsed && !live ? '<span data-activity-elapsed></span>' : "";
    const summary = live
      ? ""
      : isHarnessView()
        ? `<p class="chat-cli-run" data-activity-summary aria-hidden="true"><span class="chat-activity-text"><span>${label}</span>${elapsed}</span></p>`
        : (
          `<button type="button" class="chat-activity-summary" data-activity-summary aria-expanded="${open ? "true" : "false"}" aria-controls="${aid}">` +
          `<span class="chat-activity-text"><span>${label}</span>${elapsed}</span>` +
          '<svg class="chat-activity-chevron" viewBox="0 0 16 16" width="12" height="12" aria-hidden="true"><path d="M5 6.5l3 3 3-3" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>' +
           "</button>"
         );
    const attentionCount = traces.filter(t => { const p = toolPresentation(t, turnLive); return p.running || p.failed || p.interrupted || p.warning; }).length;
    const attention = chatView === "compact" && !live && attentionCount
      ? `<button type="button" class="chat-activity-attention" data-activity-attention>Inspect ${attentionCount} active / flagged ${attentionCount === 1 ? "call" : "calls"} ↗</button>` : "";
    return (
      `<section class="${classes.join(" ")}" data-activity data-activity-block="${key}">` +
      summary + attention +
      `<div class="chat-activity-panel" id="${aid}" role="region" aria-label="Activity details"><div class="chat-activity-panel-inner"><ol class="chat-timeline">${steps}</ol></div></div>` +
      "</section>"
    );
  }

  function liveStatusHtml(msg, traces, live) {
    if (!live) return "";
    const groups = groupTraces(traces);
    const label = escapeHtml(liveStatusLabel(msg, groups));
    return (
      `<div class="chat-live-status" data-live-status>` +
      '<span class="chat-activity-spin" data-activity-spin aria-hidden="true"></span>' +
      `<span class="chat-status-shimmer"><span class="chat-activity-text"><span data-live-label>${label}</span><span data-activity-elapsed></span></span></span>` +
      "</div>"
    );
  }

  const transcriptDomOptions = {
    island: node => node.hasAttribute("data-reason-preview") || node.hasAttribute("data-content-block") || node.dataset.step?.startsWith("think-"),
    dispose: node => { if (node.nodeType === 1) disposeThinking(node); },
  };

  function updateAssistant(article, msg, live) {
    // Off-screen historical responses are hydrated from the latest message
    // object when they enter the viewport, rather than on every sync event.
    if (!article || article.hasAttribute("data-deferred-message")) return;
    if (log.contains(article) && article.dataset.messageId && article.dataset.messageId !== msg.id) { pendingFullPaint = true; return; }
    article._rebind?.(msg);
    if (toolMotions.has(article)) { motionPaints.set(article, { msg, live }); return; }
    if (toolGestureOwner === article && article.isConnected) {
      pendingToolPaint = { article, msg, live };
      return;
    }
    const flow = article.querySelector("[data-flow]");
    const err = article.querySelector("[data-err]");
    const foot = article.querySelector("[data-foot]");
    const meta = article.querySelector("[data-meta]");
    const traces = Array.isArray(msg.tools) ? msg.tools : [];
    const hasMarker = Array.isArray(msg.segments) && msg.segments.some((part) => part && part.type === "marker");
    const hasOutput = Boolean((msg.content || "").trim() || (msg.reasoning || "").trim() || msg.error || traces.length || msg.permissions?.length || hasMarker);
    article.hidden = !hasOutput;
    const sourceMap = new Map();
    traces.forEach((trace) => {
      const rows = trace.results || (trace.name === "read_url" && trace.status === "done" && trace.url ? [{ url: trace.url, status: "read" }] : []);
      rows.filter((row) => /^https?:/i.test(safeUrl(row.url))).forEach((row) => {
        const prior = sourceMap.get(row.url) || {};
        sourceMap.set(row.url, { ...prior, ...row, status: prior.status === "read" || row.status === "read" || trace.name === "read_url" ? "read" : "found" });
      });
    });
    const content = String(msg.content || "");
    const sources = [...sourceMap.values()].map((row) => ({ ...row, cited: content.includes(row.url) }));
    const citations = article.querySelector("[data-citations]");
    const inActivity = Boolean(traces.length);
    citations.hidden = inActivity || !sources.length || !msg.content;
    const sourceGrid = article.querySelector("[data-source-grid]");
    const sourceSignature = JSON.stringify(sources);
    if (sourceGrid._src !== sourceSignature) {
      sourceGrid._src = sourceSignature;
      sourceGrid.innerHTML = sourceList(sources);
      article.querySelector("[data-source-count]").textContent = `${sources.length} ${sources.length === 1 ? "source" : "sources"}`;
    }
    const catalog = sources.map((row) => row.url);
    const segments = assistantSegments(msg);
    const toolById = new Map(traces.map((trace, index) => [trace.id, { trace, index }]));
    const permById = new Map((msg.permissions || []).map((perm) => [perm.id, perm]));
    const normalized = segments.flatMap((part, segmentIndex) => {
      if (!part || !part.type) return [];
      if (part.type === "marker") {
        return [{ type: "marker", segmentIndex, text: part.text || "", kind: part.kind || "compaction", summary: part.summary || "" }];
      }
      if (part.type === "tool") {
        const found = toolById.get(part.id);
        return found ? [{ type: "tool", segmentIndex, ...found }] : [];
      }
      if (part.type === "permission") {
        const found = permById.get(part.id);
        if (!found || found.kind === "tool_permission") return [];
        return [{ type: "permission", segmentIndex, perm: found }];
      }
      return typeof part.text === "string" && part.text ? [{ type: part.type, text: part.text, segmentIndex }] : [];
    });
    const blocks = [];
    normalized.forEach((part) => {
      if (part.type === "permission") {
        blocks.push({ kind: "permission", entries: [part] });
        return;
      }
      if (part.type === "marker") {
        blocks.push({ kind: "marker", text: part.text, summary: part.summary || "" });
        return;
      }
      const kind = part.type === "content" ? "content" : "activity";
      const last = blocks[blocks.length - 1];
      if (last && last.kind === kind) last.entries.push(part);
      else blocks.push({ kind, entries: [part] });
    });
    const lastActivity = blocks.map((block) => block.kind).lastIndexOf("activity");
    const liveGroups = groupTraces(traces);
    const liveKind = activityKind(msg, liveGroups, live);
    // Structure only: types, ids and immutable marker text. Including segment
    // text here would rebuild the whole message DOM on every streamed delta
    // (the shape-change path innerHTML-renders every block); text growth is
    // handled per block by the settled/tail markdown streamer below.
    const structure = segments.map((part) => part?.type === "marker"
      ? `marker:${part.text || ""}:${part.summary || ""}`
      : `${part?.type || ""}:${part.id || ""}`).join("|");
    const permSig = (msg.permissions || []).map((p) => `${p.id}:${p.status}`).join("|");
    const traceById = new Map(traces.map((trace) => [trace && trace.id, trace]));
    const blockSig = (block, blockIndex) => {
      if (block.kind === "activity") {
        return block.entries.map((entry) => {
          if (entry.type !== "tool") return `r:${entry.segmentIndex}:${entry.text || ""}`;
          const trace = entry.trace;
          const running = Boolean(trace && toolPresentation(trace, live).running);
          return `t:${trace && trace.id}:${tracesSig([trace])}:${running}`;
        }).join("~");
      }
      if (block.kind === "marker") return `m:${block.text}:${block.summary || ""}`;
      if (block.kind === "permission") return `p:${block.entries[0] && block.entries[0].perm && block.entries[0].perm.id}`;
      return `c:${blockIndex}`;
    };
    const sig = `${Boolean(live)}|${chatView}|${liveKind}|${structure}|${permSig}`;
    let reasoningPositions = null;
    const sameShape = flow._sig === sig && flow.childElementCount === blocks.length;
    if (sameShape) {
      // One running tool must not remount the settled rows beside it. Replacing
      // a row that is already open replays its expand animation.
      blocks.forEach((block, blockIndex) => {
        const next = blockSig(block, blockIndex);
        const node = flow.children[blockIndex];
        if (!node || node._blockSig === next || block.kind === "content") return;
        if (block.kind !== "activity") {
          const html = block.kind === "permission"
            ? permissionCardHtml(block.entries[0].perm, msg)
            : compactionMarkHtml(block.text, block.summary);
          if (node.outerHTML === html) { node._blockSig = next; return; }
          const fresh = document.createElement("template");
          fresh.innerHTML = html;
          const replacement = fresh.content.firstElementChild;
          if (!replacement) return;
          replacement._blockSig = next;
          node.replaceWith(replacement);
          return;
        }
        const html = activityBlockHtml(block.entries, msg, live && blockIndex === blocks.length - 1, blockIndex, !live && blockIndex === lastActivity, live);
        const fresh = document.createElement("template");
        fresh.innerHTML = html;
        const incoming = fresh.content.firstElementChild;
        if (!incoming) return;
        FuseChatDOM.patch(node, incoming, transcriptDomOptions);
        node._blockSig = next;
      });
    } else if (flow._sig !== sig) {
      reasoningPositions = new Map([...flow.querySelectorAll("[data-reason-preview]")].map(el =>
        [el.dataset.reasonPreview, thinkingPosition(el)]));
      const focused = flow.contains(document.activeElement) ? document.activeElement : null;
      const focusedStep = focused?.closest("[data-step]")?.dataset.step;
      const focusedControl = focused?.matches(".chat-step-caret") ? ".chat-step-caret" : focused?.matches(".chat-step-toggle") ? ".chat-step-toggle" : focused?.matches(".chat-think-size") ? ".chat-think-size" : focused?.matches(".chat-step-open") ? ".chat-step-open" : "";
      const focusedThinking = focused?.matches("[data-reason-preview]") ? focused.dataset.reasonPreview : null;
      const focusedSection = focused?.matches("[data-copy-tool]") ? focused.closest("[data-tool-section]")?.dataset.toolSection : "";
      const preKey = (el, i) => el.closest("[data-tool-section]") ? `${el.closest("[data-step]")?.dataset.step}:${el.closest("[data-tool-section]").dataset.toolSection}` : `pre-${i}`;
      const positions = new Map([...flow.querySelectorAll("pre")].map((el, i) => [preKey(el, i), [el.scrollLeft, el.scrollTop]]));
      flow._sig = sig;
      const nextFlow = blocks.map((block, blockIndex) => {
        if (block.kind === "permission") {
          return permissionCardHtml(block.entries[0].perm, msg);
        }
        if (block.kind === "marker") {
          return compactionMarkHtml(block.text, block.summary);
        }
        if (block.kind === "content") {
          const text = block.entries.map((entry) => entry.text).join("");
          const writing = live && blockIndex === blocks.length - 1;
          return `<div class="chat-md${writing ? " is-writing" : ""}" data-content-block="${blockIndex}"></div>`;
        }
        const blockLive = live && blockIndex === blocks.length - 1;
         return activityBlockHtml(block.entries, msg, blockLive, blockIndex, !live && blockIndex === lastActivity, live);
       }).join("");
      FuseChatDOM.reconcile(flow, nextFlow, transcriptDomOptions);
      [...flow.children].forEach((node, blockIndex) => { node._blockSig = blockSig(blocks[blockIndex], blockIndex); });
      flow.querySelectorAll("pre").forEach((el, i) => {
        const position = positions.get(preKey(el, i));
        if (position) { el.scrollLeft = position[0]; el.scrollTop = position[1]; }
      });
      hydratePermissionCards(flow);
      if (focusedStep && focusedControl) {
        flow.querySelector(`[data-step="${CSS.escape(focusedStep)}"] > .chat-step-main > .chat-step-head > ${focusedControl}`)?.focus({ preventScroll: true });
      }
      if (focusedStep && focusedSection) flow.querySelector(`[data-step="${CSS.escape(focusedStep)}"] [data-tool-section="${CSS.escape(focusedSection)}"] [data-copy-tool]`)?.focus({ preventScroll: true });
      if (focusedThinking != null) flow.querySelector(`[data-reason-preview="${CSS.escape(focusedThinking)}"]`)?.focus({ preventScroll: true });
    }
    if (live) syncLiveDock();
    normalized.filter((part) => part.type === "reasoning").forEach((part) => {
      const peek = flow.querySelector(`[data-reason-preview="${part.segmentIndex}"]`);
      const li = flow.querySelector(`[data-step="think-${part.segmentIndex}"]`);
      if (!li || !peek) return;
      const saved = reasoningPositions?.get(String(part.segmentIndex)) || (peek._thinking ? thinkingPosition(peek) : null);
      initThinking(peek);
      const later = normalized.some((entry) => entry.segmentIndex > part.segmentIndex);
      const liveThink = Boolean(live && !later);
      li.classList.toggle("is-live-think", liveThink);
      syncThinkingMode(li, msg);
      let source = String(part.text || "").trim();
      if (isConsoleView() && li.dataset.reasonIds) {
        const ids = new Set(li.dataset.reasonIds.split(","));
        source = normalized
          .filter((entry) => entry.type === "reasoning" && ids.has(String(entry.segmentIndex)))
          .map((entry) => String(entry.text || "").trim())
          .filter(Boolean)
          .join("\n\n");
      }
      if (peek._source !== source || peek._live !== liveThink) {
        peek._source = source; peek._live = liveThink;
        streamMarkdown(peek.firstElementChild, source, liveThink, catalog);
        if (!isConsoleView()) {
          const summary = li.querySelector(".chat-think-summary");
          if (summary) summary.innerHTML = thinkingSummaryHtml(source);
        }
      }
      if (reasoningPositions?.has(String(part.segmentIndex)) && saved) {
        peek.scrollTop = saved.top;
        peek._thinking.lastTop = peek.scrollTop;
        peek._thinking.initialized = saved.initialized;
      }
      if (saved && !saved.pinned) {
        stopThinkingFollow(peek);
        peek.scrollTop = saved.top;
      } else if (liveThink || saved?.pinned && saved?.initialized) {
        peek._thinking.follow = true;
        followThinking(peek, !saved?.initialized);
      } else if (!liveThink) peek._thinking.follow = false;
    });
    blocks.forEach((block, blockIndex) => {
      if (block.kind !== "content") return;
      const md = flow.querySelector(`[data-content-block="${blockIndex}"]`);
      if (!md) return;
      const text = block.entries.map((entry) => entry.text).join("");
      const writing = live && blockIndex === blocks.length - 1;
      const srcKey = text + "\0" + catalog.join("\n");
      md.classList.toggle("is-writing", writing);
      if (md._src === srcKey) return;
      const positions = [...md.querySelectorAll("pre")].map((el) => [el.scrollLeft, el.scrollTop]);
      md._src = srcKey;
      streamMarkdown(md, text, writing, catalog);
      md.querySelectorAll("pre").forEach((el, i) => {
        if (positions[i]) { el.scrollLeft = positions[i][0]; el.scrollTop = positions[i][1]; }
      });
    });
    syncActivityElapsed(article, msg, live);
    syncActivitySpin(article, msg, live);
    if (live) startLiveClock();
    const toolFailKeys = traces
      .filter((t) => toolFailed(t))
      .map((t) => normErr(t.error || "failed"));
    const errText = String(msg.error || "").replace(/\s+/g, " ").trim();
    const errDup = Boolean(errText && toolFailKeys.includes(normErr(errText)));
    if (errText && !errDup) {
      err.hidden = false;
      err.textContent = errText;
    } else {
      err.hidden = true;
      err.textContent = "";
    }
    const usageBits = formatTurnMeta(msg);
    if (!live && hasOutput) {
      foot.hidden = false;
      meta.innerHTML = usageBits
        .map((bit) => `<span title="${escapeHtml(bit.title)}">${escapeHtml(bit.text)}</span>`)
        .join('<span class="chat-foot-dot" aria-hidden="true">·</span>');
    } else {
      foot.hidden = true;
    }
  }

  const historyObserver = typeof IntersectionObserver === "function" ? new IntersectionObserver(entries => {
    for (const entry of entries) if (entry.isIntersecting) hydrateHistoricalMessage(entry.target);
  }, { root: thread, rootMargin: "600px 0px" }) : null;

  function hydrateHistoricalMessage(shell) {
    historyObserver?.unobserve(shell);
    if (!shell.isConnected || !shell.hasAttribute("data-deferred-message")) return;
    const index = messages.findIndex(msg => msg.id === shell.dataset.messageId);
    if (index < 0) return;
    const viewport = captureThreadViewport();
    const previousHeight = shell.offsetHeight;
    const article = renderAssistant(messages[index], false);
    article.dataset.messageId = messages[index].id;
    shell.replaceWith(article);
    if (viewport.following && stickToBottom) {
      // Loading older history is not new model output. Compensate its height
      // immediately instead of animating through pages of newly mounted text.
      thread.scrollTop = viewport.top + article.offsetHeight - previousHeight;
      lastThreadScrollTop = thread.scrollTop;
      scrollThread();
    } else restoreThreadViewport(viewport);
  }

  function renderTranscriptMessage(msg, index, live) {
    // Keep stable, indexed shells so editing/rewinding and run updates still
    // address the complete server-owned transcript. Expensive markdown, tool
    // trees and syntax highlighting for old answers are created on demand.
    if (historyObserver && msg.role === "assistant" && !live && index < messages.length - 8 &&
        (msg.content || msg.reasoning || msg.tools?.length || msg.permissions?.length || msg.error)) {
      const shell = document.createElement("article");
      shell.className = "chat-turn is-assistant chat-deferred-message";
      shell.dataset.messageId = msg.id;
      shell.setAttribute("data-deferred-message", "");
      const button = document.createElement("button");
      button.type = "button";
      button.className = "btn ghost sm";
      button.textContent = "Load earlier response";
      button.addEventListener("click", () => hydrateHistoricalMessage(shell));
      shell.append(button);
      historyObserver.observe(shell);
      return shell;
    }
    return msg.role === "user" ? renderUser(msg, index, live) : renderAssistant(msg, live);
  }

  function viewMessages() {
    if (editingIndex >= 0 || !contextMessages.length) return messages;
    if (runIsActive(activeRun) && !contextLive) return messages;
    const live = messages.find((msg) => msg.role === "assistant" && messageRunLive(msg));
    if (!live || contextMessages.some((msg) => msg.id === live.id)) return contextMessages;
    return contextMessages.concat([live]);
  }

  function paint() {
    if (toolGestureOwner && log.contains(toolGestureOwner)) { pendingFullPaint = true; return; }
    foldAutoCommandResults();
    reconcileBgTraces([...completedJobs.values()], false);
    const viewport = captureThreadViewport();
    const existing = new Map(log._threadId === currentId ? [...log.children].map(el => [el.dataset.messageId, el]) : []);
    const keep = new Set();
    let cursor = log.firstElementChild;
    viewMessages().forEach((msg, index) => {
      const live = msg.role === "assistant" && messageRunLive(msg);
      const signature = msg.role === "user" ? JSON.stringify([index, chatView, live, editingIndex === index, msg.content, msg.attachments, msg.auto, msg.autoSteps, msg.noActions]) : "";
      let article = existing.get(msg.id);
      const reusable = article && article.classList.contains(msg.role === "assistant" ? "is-assistant" : "is-user") &&
        (msg.role === "assistant" || article._userSignature === signature) && !(live && article.hasAttribute("data-deferred-message"));
      if (!reusable) {
        const old = article;
        article = renderTranscriptMessage(msg, index, live);
        if (old) { disposeThinking(old); historyObserver?.unobserve(old); old.replaceWith(article); if (cursor === old) cursor = article; }
      } else if (msg.role === "assistant") {
        for (const key of ["stepOpen", "activityOpenMap"]) if (!msg[key] && article._message?.[key]) msg[key] = article._message[key];
      }
      article.dataset.messageId = msg.id || "";
      article._userSignature = signature;
      if (article !== cursor) log.insertBefore(article, cursor);
      cursor = article.nextElementSibling;
      keep.add(article);
      if (reusable && msg.role === "assistant") updateAssistant(article, msg, live);
      if (msg.role === "assistant" && !live && !msg.noActions) {
        if (article._actionIndex !== index) article.querySelector("[data-actions-host]")?.replaceChildren();
        mountActions(article, index); article._actionIndex = index;
      }
    });
    for (const article of [...log.children]) if (!keep.has(article)) { disposeThinking(article); historyObserver?.unobserve(article); article.remove(); }
    log._threadId = currentId;
    syncEmptyReplyRetry();
    setActive(messages.length > 0);
    rememberLatestArtifact();
    syncComposer();
    syncContextMeter();
    syncLiveDock();
    restoreThreadViewport(viewport);
    if (viewport.threadId !== currentId) scrollThread();
  }

  // Frame-coalesced repaint entry points. Sync events can arrive dozens of
  // times per second across many subagents; state updates stay eager and
  // cheap, while the DOM repaint runs at most once per animation frame.
  let _paintLastQueued = false;
  function schedulePaintLast() {
    if (_paintLastQueued) return;
    _paintLastQueued = true;
    requestAnimationFrame(() => {
      _paintLastQueued = false;
      paintLast();
    });
  }

  let _agentViewQueued = false;
  function scheduleAgentView() {
    if (_agentViewQueued) return;
    _agentViewQueued = true;
    requestAnimationFrame(() => {
      _agentViewQueued = false;
      renderAgentView();
    });
  }

  document.addEventListener("visibilitychange", () => {
    // Background tabs throttle rAF to nothing; repaint live streams on return.
    if (!document.hidden) {
      if (streaming) schedulePaintLast();
      if (agentsOpen && agentViewId) scheduleAgentView();
    }
  });

  function paintLast() {
    const msg = runIsActive(activeRun) ? activeAssistant() : messages[messages.length - 1];
    const shown = viewMessages();
    const index = msg ? shown.findIndex((item) => item === msg || (msg.id && item.id === msg.id)) : -1;
    const last = index >= 0 ? log.children[index] : null;
    if (!msg || msg.role !== "assistant" || !last || !last.classList.contains("is-assistant") || last.dataset.messageId !== msg.id) {
      paint();
      return;
    }
    const viewport = captureThreadViewport();
    updateAssistant(last, msg, messageRunLive(msg));
    syncEmptyReplyRetry();
    rememberLatestArtifact();
    setActive(true);
    syncComposer();
    if (streaming) {
      const now = Date.now();
      if (now - contextRecountAt >= 400) {
        contextRecountAt = now;
        liveStreamEstimate();
      }
    } else syncContextMeter();
    syncLiveDock();
    restoreThreadViewport(viewport);
  }

  function activeAssistant() {
    if (activeRun?.message_id) return messages.find(msg => msg.role === "assistant" && msg.id === activeRun.message_id) || null;
    return [...messages].reverse().find((msg) => msg.role === "assistant");
  }

  function filteredModels() {
    const q = pickerFilter.trim().toLowerCase();
    if (!q) return allModels;
    return allModels.filter(
      (model) =>
        model.name.toLowerCase().includes(q) ||
        model.id.toLowerCase().includes(q) ||
        model.title.toLowerCase().includes(q)
    );
  }

  function renderPickerList() {
    const menu = (pickerMenuEl || pickerHost).querySelector(".chat-picker-list");
    if (!menu) return;
    renderPickerThinking();
    const items = filteredModels();
    if (pickerIndex >= items.length) pickerIndex = Math.max(0, items.length - 1);
    if (!items.length) {
      menu.innerHTML = '<p class="chat-picker-empty">No models match.</p>';
      return;
    }
    const groups = [];
    items.forEach((model, index) => {
      let group = groups[groups.length - 1];
      if (!group || group.type !== model.type) {
        group = { type: model.type, title: model.title, items: [] };
        groups.push(group);
      }
      group.items.push({ model, index });
    });
    menu.innerHTML = groups
      .map(
        (group) =>
          `<div class="chat-picker-group"><p>${escapeHtml(group.title)}</p>${group.items
            .map(({ model, index }) => {
              const selected = model.id === modelId;
              const row =
                `<button type="button" role="option" data-id="${escapeHtml(model.id)}" aria-selected="${
                  selected ? "true" : "false"
                }" class="${selected ? "is-selected" : ""}${index === pickerIndex ? " is-active" : ""}">` +
                providerLogo(model.type) +
                `<span>${escapeHtml(model.name)}</span>` +
                (selected ? PICKER_CHECK : "") +
                `</button>`;
              return `<div class="chat-picker-row">${row}</div>`;
            })
            .join("")}</div>`
      )
      .join("");
  }

  function renderPickerThinking() {
    const host = (pickerMenuEl || pickerHost).querySelector(".chat-picker-thinking-host");
    if (!host) return;
    const model = currentModel();
    const efforts = modelEfforts();
    const effort = currentEffort();
    if (!efforts.length) {
      host.innerHTML = "";
      return;
    }
    const index = Math.max(0, efforts.indexOf(effort));
    const ticks = efforts
      .map(
        (_, i) =>
          `<i style="left:${
            efforts.length > 1 ? ((i / (efforts.length - 1)) * 100).toFixed(4) : 50
          }%"></i>`
      )
      .join("");
    host.innerHTML =
      `<section class="chat-picker-thinking" aria-label="Thinking mode">` +
      `<div class="chat-picker-thinking-head"><p>Thinking mode</p><small class="chat-picker-thinking-value">${escapeHtml(
        effortLabel(effort)
      )}</small></div>` +
      `<div class="chat-picker-efforts">` +
      `<div class="chat-picker-efforts-ticks" aria-hidden="true">${ticks}</div>` +
      `<input type="range" class="chat-picker-efforts-slider" data-effort-slider min="0" max="${
        efforts.length - 1
      }" step="1" value="${index}" aria-label="Thinking mode" aria-valuetext="${escapeHtml(
        effortLabel(effort)
      )}">` +
      `</div></section>`;
    syncEffortSlider(effort);
  }

  function syncEffortSlider(effort) {
    const menu = pickerMenuEl || pickerHost;
    const slider = menu.querySelector("[data-effort-slider]");
    if (!slider) return;
    const efforts = modelEfforts();
    const index = Math.max(0, efforts.indexOf(effort));
    if (Number(slider.value) !== index) slider.value = index;
    const span = efforts.length > 1 ? index / (efforts.length - 1) : 1;
    slider.style.setProperty("--effort-fill", (span * 100).toFixed(2) + "%");
    slider.setAttribute("aria-valuenow", String(index));
    slider.setAttribute("aria-valuetext", effortLabel(efforts[index]));
    const value = menu.querySelector(".chat-picker-thinking-value");
    if (value) value.textContent = effortLabel(efforts[index]);
  }

  function closePicker() {
    const wasOpen = pickerOpen;
    pickerOpen = false;
    if (pickerMenuEl) pickerMenuEl.hidden = true;
    const btn = pickerHost.querySelector(".chat-picker-btn");
    if (btn) btn.setAttribute("aria-expanded", "false");
    if (wasOpen) {
      armOverlayGhost(pickerMenuEl?.querySelector("[data-picker-close]"));
      overlayDrop("picker");
    }
  }

  function openPicker() {
    if (!allModels.length) return;
    setPermsOpen(false);
    pickerOpen = true;
    overlayPush("picker");
    pickerFilter = "";
    const items = filteredModels();
    pickerIndex = Math.max(
      0,
      items.findIndex((model) => model.id === modelId)
    );
    const btn = pickerHost.querySelector(".chat-picker-btn");
    const search = pickerMenuEl && pickerMenuEl.querySelector(".chat-picker-search");
    if (pickerMenuEl) pickerMenuEl.hidden = false;
    if (btn) btn.setAttribute("aria-expanded", "true");
    // Focus would pop the touch keyboard on mobile; the sheet stays keyboard-free until tapped.
    if (search) {
      search.value = "";
      if (!pickerSheetQuery.matches) search.focus();
    }
    renderPickerList();
  }

  function selectModel(id, dismiss) {
    id = resolveListedModel(id) || id;
    if (!modelsById.has(id)) return;
    if (id !== modelId && !requireLive()) return;
    modelId = id;
    pickerIndex = Math.max(0, allModels.findIndex((model) => model.id === id));
    persist();
    if (dismiss || !pickerOpen) {
      closePicker();
      renderPickerButton();
      const btn = pickerHost.querySelector(".chat-picker-btn");
      if (btn) btn.focus();
    } else {
      pickerFilter = "";
      const model = currentModel();
      const effort = currentEffort();
      const btn = pickerHost.querySelector(".chat-picker-btn");
      const logo = btn && btn.querySelector(".provider-logo");
      if (logo) logo.outerHTML = providerLogo(model.type);
      if (btn) {
        btn.querySelector("span").textContent = model.name;
        btn.querySelector("small").textContent = effortLabel(effort);
        btn.title = model.id + " · " + effortLabel(effort);
      }
      const search = pickerMenuEl && pickerMenuEl.querySelector(".chat-picker-search");
      if (search) search.value = "";
      renderPickerList();
      if (search && !pickerSheetQuery.matches) search.focus();
    }
    syncComposer();
    if (liveMeter) {
      const changed = liveMeter.model !== id;
      liveMeter = { ...liveMeter, model: id, limit: modelWindow() };
      if (changed) {
        liveMeter.estimated = true;
        liveMeter.provider_usage = null;
        delete liveMeter.calibration;
        if (liveMeter.raw_parts) {
          liveMeter.used = liveMeter.raw_parts.total;
          liveMeter.parts = liveMeter.raw_parts;
          liveMeter.basis = "payload_chars_v1";
          delete liveMeter.raw_parts;
        }
      }
    }
    meterAnchor = null;
    syncContextMeter();
  }

  function renderPickerButton() {
    const model = currentModel();
    pickerHost.innerHTML = "";
    pickerMenuEl = null;
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "chat-picker-btn";
    btn.setAttribute("aria-haspopup", "dialog");
    btn.setAttribute("aria-expanded", pickerOpen ? "true" : "false");
    if (!model) {
      btn.disabled = true;
      btn.innerHTML = "<span>No models</span>";
      pickerHost.appendChild(btn);
      return;
    }
    const effort = currentEffort();
    const think = effortLabel(effort);
    btn.innerHTML = `${providerLogo(model.type)}<span></span><small></small>${PICKER_CHEV}`;
    btn.querySelector("span").textContent = model.name;
    btn.querySelector("small").textContent = think;
    btn.title = model.id + " · " + think;
    btn.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
      if (pickerOpen) closePicker();
      else openPicker();
    });
    pickerHost.appendChild(btn);
    pickerMenuEl = renderPickerSheet();
    if (pickerSheetQuery.matches) {
      // A fixed sheet cannot live here: transformed/backdrop-filtered composer
      // ancestors (glass dock) would turn it into a contained, clipped box.
      root.appendChild(pickerMenuEl);
    } else {
      pickerHost.appendChild(pickerMenuEl);
    }
    pickerMenuEl.hidden = !pickerOpen;
    if (pickerOpen) renderPickerList();
  }

  function renderPickerSheet() {
    const menu = document.createElement("div");
    menu.className = "chat-picker-menu" + (pickerSheetQuery.matches ? " is-sheet" : "");
    menu.setAttribute("role", "dialog");
    menu.setAttribute("aria-label", "Model picker");
    menu.innerHTML =
      '<div class="chat-picker-head"><p>Model</p><button type="button" class="chat-menu-close chat-picker-done" data-picker-close aria-label="Close"><svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><path d="M3.5 3.5l9 9M12.5 3.5l-9 9" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg></button></div>' +
      '<div class="chat-picker-searchrow"><svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><circle cx="7" cy="7" r="4.4" fill="none" stroke="currentColor" stroke-width="1.5"/><path d="M10.4 10.4L13.8 13.8" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg><input type="search" class="chat-picker-search" placeholder="Search models" aria-label="Search models"></div>' +
      '<div class="chat-picker-list" role="listbox" aria-label="Models"></div>' +
      '<div class="chat-picker-thinking-host"></div>';
    const search = menu.querySelector(".chat-picker-search");
    search.addEventListener("input", () => {
      pickerFilter = search.value;
      pickerIndex = 0;
      renderPickerList();
    });
    search.addEventListener("keydown", (event) => {
      const items = filteredModels();
      if (event.key === "ArrowDown") {
        event.preventDefault();
        pickerIndex = Math.min(items.length - 1, pickerIndex + 1);
        renderPickerList();
      } else if (event.key === "ArrowUp") {
        event.preventDefault();
        pickerIndex = Math.max(0, pickerIndex - 1);
        renderPickerList();
      } else if (event.key === "Enter") {
        event.preventDefault();
        if (items[pickerIndex]) selectModel(items[pickerIndex].id, true);
      } else if (event.key === "Escape") {
        event.preventDefault();
        closePicker();
        const btn = pickerHost.querySelector(".chat-picker-btn");
        if (btn) btn.focus();
      }
    });
    menu.addEventListener("click", (event) => {
      event.stopPropagation();
      const closeBtn = event.target.closest("[data-picker-close]");
      if (closeBtn) {
        event.preventDefault();
        closeBtn.blur();
        closePicker();
        const btn = pickerHost.querySelector(".chat-picker-btn");
        if (btn && !pickerSheetQuery.matches) btn.focus();
        return;
      }
      const target = event.target.closest("button[data-id]");
      if (target) {
        const id = target.getAttribute("data-id");
        selectModel(id, id === modelId);
      }
    });
    menu.addEventListener("input", (event) => {
      const slider = event.target.closest("[data-effort-slider]");
      if (!slider) return;
      const effort = modelEfforts()[Number(slider.value)];
      if (effort) setEffort(effort);
    });
    return menu;
  }

  document.addEventListener("click", (event) => {
    if (!pickerOpen) return;
    if (pickerHost.contains(event.target)) return;
    if (pickerMenuEl && pickerMenuEl.contains(event.target)) return;
    closePicker();
  });

  log.addEventListener("click", async (event) => {
    const previewBtn = event.target.closest("[data-image-preview]");
    if (previewBtn) {
      event.preventDefault();
      openLightbox(previewBtn.getAttribute("data-image-preview"), previewBtn.getAttribute("data-image-name") || "");
      return;
    }
    const removeBtn = event.target.closest("[data-remove-attachment]");
    if (removeBtn) {
      event.preventDefault();
      const row = removeBtn.closest("[data-user-files]");
      if (!row) return;
      removeAttachment(
        Number(row.getAttribute("data-msg-index")),
        removeBtn.getAttribute("data-remove-attachment") || "",
        Number(removeBtn.getAttribute("data-att-index"))
      );
      return;
    }
    const openArt = event.target.closest("[data-open-artifact]");
    if (openArt) {
      event.preventDefault();
      event.stopPropagation();
      showArtifact(openArt.getAttribute("data-open-artifact")).catch((error) => showFailure("Could not open artifact", error));
      return;
    }
    const openAgentEl = event.target.closest("[data-open-agent]");
    if (openAgentEl) {
      event.preventDefault();
      event.stopPropagation();
      showAgent(openAgentEl.getAttribute("data-open-agent"));
      return;
    }
    const autoToggle = event.target.closest("[data-auto-toggle]");
    if (autoToggle) {
      event.preventDefault();
      const turn = autoToggle.closest(".chat-turn.is-auto");
      if (!turn) return;
      const open = !turn.classList.contains("is-open");
      turn.classList.toggle("is-open", open);
      autoToggle.setAttribute("aria-expanded", String(open));
      return;
    }
  });
  // Code blocks are shared by the main transcript and the agent reader.
  root.addEventListener("click", async (event) => {
    const btn = event.target.closest("[data-copy-code]");
    if (!btn) return;
    const code = btn.closest(".chat-code")?.querySelector("code");
    if (!code) return;
    try {
      await navigator.clipboard.writeText(code.textContent || "");
      setHint("Copied to clipboard.");
      btn.innerHTML = ACTION_ICONS.copied;
      btn.setAttribute("aria-label", "Copied");
      setTimeout(() => {
        btn.innerHTML = ACTION_ICONS.copy;
        btn.setAttribute("aria-label", "Copy");
      }, 1200);
    } catch (error) {
      showFailure("Clipboard copy failed", error);
    }
  });

  // Let a vertical gesture leave a capped code pane once it reaches either edge.
  let codeTouch = null;
  log.addEventListener("touchstart", (event) => {
    const pane = event.target.closest(".chat-code pre");
    codeTouch = pane && event.touches.length === 1
      ? { x: event.touches[0].clientX, y: event.touches[0].clientY }
      : null;
  }, { passive: true });
  log.addEventListener("touchmove", (event) => {
    const pane = event.target.closest(".chat-code pre");
    if (!pane || !codeTouch || event.touches.length !== 1) return;
    const deltaY = event.touches[0].clientY - codeTouch.y;
    const deltaX = event.touches[0].clientX - codeTouch.x;
    codeTouch = { x: event.touches[0].clientX, y: event.touches[0].clientY };
    if (Math.abs(deltaY) < 1 || Math.abs(deltaX) > Math.abs(deltaY)) return;
    const atTop = pane.scrollTop <= 0;
    const atBottom = pane.scrollTop + pane.clientHeight >= pane.scrollHeight - 1;
    if ((deltaY > 0 && atTop) || (deltaY < 0 && atBottom)) {
      event.preventDefault();
      thread.scrollTop -= deltaY;
    }
  }, { passive: false });
  log.addEventListener("touchend", () => { codeTouch = null; }, { passive: true });

  thread.addEventListener("wheel", event => {
    if (event.deltaY < 0) pauseThreadFollow();
    else if (event.deltaY > 0) resumeFollowOnScroll = true;
  }, { passive: true });
  thread.addEventListener("pointerdown", event => {
    if (event.target === thread && event.clientX >= thread.getBoundingClientRect().right - 18) {
      pauseThreadFollow();
      resumeFollowOnScroll = true;
    }
  }, { passive: true });
  let threadTouchY = null;
  thread.addEventListener("touchstart", event => { threadTouchY = event.touches[0]?.clientY ?? null; }, { passive: true });
  thread.addEventListener("touchmove", event => {
    const y = event.touches[0]?.clientY;
    if (y != null && threadTouchY != null && y > threadTouchY + 1) pauseThreadFollow();
    else if (y != null && threadTouchY != null && y < threadTouchY - 1) resumeFollowOnScroll = true;
    threadTouchY = y ?? null;
  }, { passive: true });
  thread.addEventListener("keydown", event => {
    if (["ArrowUp", "PageUp", "Home"].includes(event.key) && !event.target.closest("input, textarea, [contenteditable]")) pauseThreadFollow();
    else if (["ArrowDown", "PageDown", "End"].includes(event.key)) resumeFollowOnScroll = true;
  });
  thread.addEventListener("scroll", () => {
    if (ignoreScroll) return;
    const top = thread.scrollTop;
    if (threadFollowFrame && stickToBottom) {
      lastThreadScrollTop = top;
      return;
    }
    stickToBottom = nearBottom() && (stickToBottom || (resumeFollowOnScroll && top > lastThreadScrollTop));
    lastThreadScrollTop = top;
    syncLatest();
  });
  root.querySelector("[data-chat-latest]")?.addEventListener("click", () => {
    stickToBottom = true;
    scrollThread();
  });

  function parseSlashCommand(text) {
    const match = String(text || "").trim().match(/^\/(undo|retry|redo|rewind|title|permissions|init|compact|help)(?:\s+([\s\S]*))?$/i);
    if (!match) return null;
    return { name: match[1].toLowerCase(), arg: (match[2] || "").trim() };
  }

  let queuedTurns = [];
  let queueDraining = false;
  let acceptedQueueCommands = new Set();

  function retireAcceptedQueue() {
    const pending = queuedTurns.filter((item) => !acceptedQueueCommands.has(item.id) && !acceptedQueueCommands.has(item.id + ":turn"));
    if (pending.length !== queuedTurns.length) {
      queuedTurns = pending;
      persistQueue();
      renderQueue();
    }
  }

  function queueStoreKey(id = currentId) {
    return "fuse.chat.queue." + id;
  }

  function persistQueue() {
    if (!currentId) return;
    try {
      localStorage.setItem(queueStoreKey(), JSON.stringify(queuedTurns));
    } catch (_) {}
  }

  function loadQueue() {
    queuedTurns = [];
    if (!currentId) return renderQueue();
    try {
      const raw = localStorage.getItem(queueStoreKey());
      const items = raw ? JSON.parse(raw) : [];
      if (Array.isArray(items))
        queuedTurns = items
          .filter((item) => item && typeof item.text === "string")
          .map((item) => ({ ...item, sending: false }));
    } catch (_) {}
    retireAcceptedQueue();
    renderQueue();
  }

  function acknowledgeQueuedTurn(origin, id) {
    if (origin === currentId) {
      queuedTurns = queuedTurns.filter((entry) => entry.id !== id);
      persistQueue();
      renderQueue();
    } else {
      try {
        const saved = JSON.parse(localStorage.getItem(queueStoreKey(origin)) || "[]");
        localStorage.setItem(queueStoreKey(origin), JSON.stringify(saved.filter((entry) => entry.id !== id)));
      } catch (_) {}
    }
  }

  window.addEventListener("storage", (event) => {
    if (currentId && event.key === queueStoreKey()) {
      loadQueue();
      if (!runIsActive(activeRun)) drainQueue();
    }
  });

  function renderQueue() {
    if (!queueEl || !queueListEl) return;
    const focused = queueListEl.contains(document.activeElement) ? document.activeElement : null;
    const focusedId = focused?.closest("[data-queue-id]")?.dataset.queueId;
    const action = focused?.hasAttribute("data-queue-send") ? "data-queue-send" : focused?.hasAttribute("data-queue-edit") ? "data-queue-edit" : "data-queue-cancel";
    const count = queuedTurns.length;
    const viewport = captureThreadViewport();
    const queueTop = queueListEl.scrollTop;
    queueEl.hidden = !count;
    root.classList.toggle("is-queued", count > 0);
    if (queueCountEl) queueCountEl.textContent = count ? String(count) : "";
    if (!count) {
      queueListEl.innerHTML = "";
      syncDockSpace();
      restoreThreadViewport(viewport);
      scheduleFitChatBar();
      return;
    }
    const html = queuedTurns
      .map(
        (item, index) =>
          `<li class="chat-queue-item${item.immediate ? " is-immediate" : ""}${item.sending ? " is-sending" : ""}" data-queue-index="${index}" data-queue-id="${escapeHtml(item.id)}" aria-busy="${Boolean(item.sending)}">` +
          `<span class="chat-queue-dot" aria-hidden="true"></span>` +
          `<span class="chat-queue-text" title="${escapeHtml(item.text)}">${escapeHtml(item.text || "Attached files")}</span>` +
          `<span class="chat-queue-state" role="status">${item.sending ? "Sending…" : item.immediate ? "Next" : "Queued"}${item.attachments?.length ? ` · ${item.attachments.length} attached` : ""}</span>` +
          '<span class="chat-queue-actions">' +
          `<button type="button" class="chat-queue-action" data-queue-send="${index}" title="Deliver this message and any files at the agent's next step"${!streaming ? " hidden" : ""}${item.sending || item.immediate || !streaming ? " disabled" : ""}>Send now</button>` +
          `<button type="button" class="chat-queue-action" data-queue-edit="${index}"${item.sending ? " disabled" : ""}>Edit</button>` +
          `<button type="button" class="chat-queue-action" data-queue-cancel="${index}"${item.sending ? " disabled" : ""}>Cancel</button>` +
          "</span></li>",
      )
      .join("");
    const template = document.createElement("template");
    template.innerHTML = html;
    const existing = new Map([...queueListEl.children].map(el => [el.dataset.queueId, el]));
    const keep = new Set();
    let next = queueListEl.firstElementChild;
    for (const fresh of [...template.content.children]) {
      const row = existing.get(fresh.dataset.queueId) || fresh;
      if (row !== fresh) {
        row.className = fresh.className;
        row.dataset.queueIndex = fresh.dataset.queueIndex;
        row.setAttribute("aria-busy", fresh.getAttribute("aria-busy"));
        patchMarkdown(row, fresh.innerHTML);
      }
      if (row !== next) queueListEl.insertBefore(row, next);
      next = row.nextElementSibling;
      keep.add(row);
    }
    for (const row of [...queueListEl.children]) if (!keep.has(row)) row.remove();
    queueListEl.scrollTop = queueTop;
    if (focusedId) queueListEl.querySelector(`[data-queue-id="${CSS.escape(focusedId)}"] [${action}]:not(:disabled)`)?.focus({ preventScroll: true });
    syncDockSpace();
    restoreThreadViewport(viewport);
    scheduleFitChatBar();
  }

  function enqueueTurn(text, attachments, opts) {
    const trimmed = String(text || "").trim();
    if (!trimmed && !(attachments && attachments.length)) return false;
    queuedTurns.push({
      id: newSession(),
      text: trimmed,
      attachments: (attachments || []).slice(),
      immediate: Boolean(opts && opts.immediate),
      sending: false,
    });
    persistQueue();
    renderQueue();
    return true;
  }

  function removeQueuedTurn(index) {
    if (index < 0 || index >= queuedTurns.length) return;
    queuedTurns.splice(index, 1);
    persistQueue();
    renderQueue();
  }

  async function drainQueue() {
    if (queueDraining || runIsActive(activeRun) || streaming || sending || loading) return;
    if (!queuedTurns.length) return;
    const item = queuedTurns[0];
    if (item.sending) return;
    item.sending = true;
    renderQueue();
    queueDraining = true;
    const origin = currentId;
    try {
      const ok = await completeTurn(item.text, item.attachments || [], { queueId: item.id, commandId: item.id + ":turn" });
      if (ok) {
        acknowledgeQueuedTurn(origin, item.id);
        scheduleFitChatBar();
      } else {
        item.sending = false;
        renderQueue();
      }
    } catch (_) {
      item.sending = false;
      renderQueue();
    } finally {
      queueDraining = false;
      resizeInput();
      syncStatus();
    }
  }

  function queueIncomingTurn() {
    const text = input.value.trim();
    const attachments = pendingAttachments.slice();
    if ((!text && !attachments.length) || !modelId) return;
    if (window.FuseComposerSpell && FuseComposerSpell.isOpen()) return;
    enqueueTurn(text, attachments);
    input.value = "";
    drafts.delete(currentId);
    pendingAttachments = [];
    renderAttachList();
    if (window.FuseComposerSpell) {
      FuseComposerSpell.close();
      FuseComposerSpell.refresh();
    }
    restoreEnterKeyHint();
    setHint(streaming ? "Queued — it sends when this turn finishes." : "Queued.");
    syncComposer();
    resizeInput();
    syncStatus();
  }

  if (queueListEl)
    queueListEl.addEventListener("click", (event) => {
      const sendBtnEl = event.target.closest("[data-queue-send]");
      const editBtnEl = event.target.closest("[data-queue-edit]");
      const undoBtnEl = event.target.closest("[data-queue-cancel]");
      if (sendBtnEl) {
        const index = Number(sendBtnEl.getAttribute("data-queue-send"));
        const item = queuedTurns[index];
        if (!item || item.sending) return;
        sendQueuedNow(item);
        return;
      }
      if (editBtnEl) {
        const index = Number(editBtnEl.getAttribute("data-queue-edit"));
        const item = queuedTurns[index];
        if (!item || item.sending) return;
        removeQueuedTurn(index);
        if (input.value.trim() || pendingAttachments.length) enqueueTurn(input.value, pendingAttachments);
        input.value = item.text;
        pendingAttachments = (item.attachments || []).slice();
        renderAttachList();
        drafts.set(currentId, item.text);
        input.focus();
        resizeInput();
        syncComposer();
        return;
      }
      if (undoBtnEl) {
        const index = Number(undoBtnEl.getAttribute("data-queue-cancel"));
        if (!queuedTurns[index]?.sending) removeQueuedTurn(index);
      }
    });

  async function sendQueuedNow(item) {
    if (!item || item.sending) return;
    if (streaming && activeRun && activeRun.id) {
      // The server atomically inserts a user message and stages it for the
      // next model boundary. The existing run and remaining queue continue.
      item.sending = true;
      renderQueue();
      setHint("Sending now…");
      const origin = currentId;
      const body = JSON.stringify({ text: item.text, command_id: item.id, attachments: item.attachments || [] });
      try {
        const data = await api("/chat/threads/" + encodeURIComponent(origin) + "/steer", { method: "POST", body });
        acknowledgeQueuedTurn(origin, item.id);
        if (origin !== currentId) return;
        if (data.thread) acceptThread(data.thread);
        scheduleFitChatBar();
        setHint("Sent — the agent receives your message at its next step.");
        await reconcileThread().catch(() => {});
      } catch (error) {
        if (origin !== currentId) return;
        if (acceptedQueueCommands.has(item.id)) {
          acknowledgeQueuedTurn(origin, item.id);
          setHint("Sent — confirmed by the conversation.");
          return;
        }
        item.sending = false;
        renderQueue();
        if (error && error.status === 409) {
          item.immediate = true;
          queuedTurns = [item, ...queuedTurns.filter((entry) => entry.id !== item.id)];
          persistQueue();
          renderQueue();
          setHint("Run finished — sending as a new turn.");
          await reconcileThread().catch(() => {});
          if (origin === currentId) drainQueue();
        } else if (error && error.status === 429) {
          setHint("Still delivering earlier messages — try Send now again shortly.");
        } else {
          if (!error.status || error.status >= 500) markDisconnected();
          setHint(error.status && error.status < 500 ? "Message was not accepted — still queued." : "Connection interrupted — retry Send now safely.", true);
        }
      }
      return;
    }
    // A finishing run may no longer accept steers. Preserve selected priority
    // and drain after its natural terminal transition; never cancel for files.
    item.immediate = true;
    queuedTurns = [item, ...queuedTurns.filter((entry) => entry.id !== item.id)];
    persistQueue();
    renderQueue();
    drainQueue();
  }

  async function stopActiveRun() {
    const id = activeRun?.id;
    if (!id) return;
    stoppingRunId = id;
    if (activeRun) activeRun.cancel_requested = true;
    setRunning();
    setHint("Stopping…");
    try {
      const data = await api("/chat/runs/" + encodeURIComponent(id) + "/cancel", { method: "POST", body: "{}" });
      applyCancelResult(data, id);
    } catch (error) {
      showFailure("Stop failed", error);
      if (activeRun?.id === id) {
        if (stoppingRunId === id) stoppingRunId = "";
        activeRun.cancel_requested = false;
        setRunning();
      }
    }
    await reconcileThread().catch(() => {});
  }

  async function send() {
    const text = input.value.trim();
    if (runIsActive(activeRun)) {
      if (text || pendingAttachments.length) {
        // Composer has content: the button is Send and queues the turn,
        // exactly like Enter. Clear the box first to reach Stop again.
        if (window.FuseComposerSpell) FuseComposerSpell.close();
        closeSlash();
        restoreEnterKeyHint();
        queueIncomingTurn();
        return;
      }
      if (window.FuseComposerSpell) FuseComposerSpell.close();
      closeSlash();
      restoreEnterKeyHint();
      await stopActiveRun();
      return;
    }
    if (window.FuseComposerSpell && FuseComposerSpell.isOpen()) return;
    if (extraPaletteOpen()) {
      acceptCurrentPalette();
      return;
    }
    if ((!text && !pendingAttachments.length) || !modelId) return;
    const slash = parseSlashCommand(text);
    if (slash) {
      closeSlash();
      runSlash(slash.name, slash.arg);
      return;
    }
    if (slashMenu && !slashMenu.hidden && slashKind === "slash") {
      const items = filteredSlash();
      if (items[slashIndex]) {
        acceptSlash(items[slashIndex]);
        return;
      }
    }
    const query = slashQuery();
    if (query && !query.name) {
      renderSlash();
      return;
    }
    const invoke = skillInvokeFromText(text);
    completeTurn(text, null, invoke ? { invoke_skill: invoke } : undefined);
  }

  async function completeTurn(text, extraAttachments, opts) {
    const usedPending = extraAttachments == null;
    const attachments = usedPending ? pendingAttachments.slice() : extraAttachments.slice();
    if (sending || loading || (runIsActive(activeRun) && !pendingTurn)) return;
    if (!requireLive()) return;
    if (!(text || "").trim() && !attachments.length) return;
    navigation += 1;
    const gen = generation;
    const origin = currentId;
    const settings = { model: modelId, tools: enabledTools(), context: threadContext };
    sending = true;
    if (!opts?.queueId) {
      input.value = text;
      drafts.set(origin, text);
    }
    // Show local feedback before copying history or waiting for HTTP. This is
    // deliberately outside the durable transcript: retries keep their command
    // identity, and a failed request never leaves a phantom saved message.
    const pending = document.createElement("div");
    pending.className = "chat-log chat-pending-send";
    pending.setAttribute("role", "status");
    const pendingArticle = document.createElement("article");
    pendingArticle.className = "chat-turn is-user";
    const bubble = document.createElement("div");
    bubble.className = "chat-bubble";
    bubble.textContent = text || `${attachments.length} attachment(s)`;
    const status = document.createElement("small");
    status.textContent = "Sending…";
    // The status stays outside the turn article: console paints the user turn
    // itself as the gray box, and the label belongs below it in every view.
    pendingArticle.append(bubble);
    pending.append(pendingArticle, status);
    log.after(pending);
    setActive(true);
    stickToBottom = true;
    scrollThread();
    syncComposer();
    try {
      await new Promise(resolve => {
        const timer = setTimeout(resolve, 100);
        requestAnimationFrame(() => setTimeout(() => { clearTimeout(timer); resolve(); }, 0));
      });
      if (gen !== generation) return;
      const undoSnap = opts && opts.undoSnap ? opts.undoSnap : snapshotMessages();
      await flushPending();
      if (gen !== generation) return;
      if (!currentId) {
        pendingCreate ||= bindCreatePayload({ ...settings, messages: [], command_id: newSession() });
        const created = await api("/chat/threads", { method: "POST", body: JSON.stringify(pendingCreate) });
        if (gen !== generation) return;
        currentId = created.id;
        serverRevision = created.revision;
        pendingCreate = null;
        pendingProject = null;
        upsertLocal(created);
        setThreadUrl(currentId);
      }
      if (pendingTurn && (pendingTurn.id !== currentId || pendingTurn.body.text !== text)) {
        throw new Error("A previous send is unconfirmed. Retry its original text before sending another message.");
      }
      pendingTurn ||= {
        id: currentId,
        body: {
          text,
          attachments,
          reasoning_effort: currentEffort(),
          model: modelId,
          tools: enabledTools(),
          context: threadContext,
          expected_revision: serverRevision,
          command_id: (opts && opts.commandId) || newSession(),
          ...(opts && opts.invoke_skill ? { invoke_skill: opts.invoke_skill } : {}),
        },
      };
      const data = await api("/chat/threads/" + pendingTurn.id + "/turns", { method: "POST", body: JSON.stringify(pendingTurn.body) });
      if (opts?.queueId) acknowledgeQueuedTurn(origin, opts.queueId);
      if (gen !== generation) return;
      pendingTurn = null;
      if (usedPending) {
        pendingAttachments = [];
        renderAttachList();
      }
      if (!opts?.queueId && input.value === text) {
        if (window.FuseComposerSpell) FuseComposerSpell.learnFromSent(text);
        input.value = "";
        drafts.delete(origin);
        drafts.delete(currentId);
        if (window.FuseComposerSpell) {
          FuseComposerSpell.close();
          FuseComposerSpell.refresh();
        }
        restoreEnterKeyHint();
      }
      pending.remove();
      acceptThread(data.thread);
      rememberUndo(undoSnap);
      // The acknowledgment is already a consistent snapshot. Subsequent
      // progress is replayed by the socket from its independent cursor.
      return true;
    } catch (error) {
      if (gen !== generation) return false;
      if (error.status === 409) {
        pendingTurn = null;
        await reconcileThread().catch(() => {});
        showFailure("Send not confirmed; your draft is retained", error);
      } else if (error.status && error.status < 500) {
        pendingTurn = null;
        showFailure("Send not confirmed; your draft is retained", error);
      } else {
        markDisconnected();
        showFailure("Can't connect to the backend", error);
      }
      return false;
    } finally {
      pending.remove();
      if (gen === generation) setActive(messages.length > 0);
      if (gen === generation) { sending = false; syncComposer(); resizeInput(); syncStatus(); }
    }
  }

  function applyProgressPayload(msg, payload, child = false) {
    if (!msg || !payload) return;
    if (Array.isArray(payload.tools)) {
      const prior = new Map((msg.tools || []).map((trace) => [trace.id, trace]));
      // fuse-steer rows are client-rendered server notices, not model tools —
      // the server snapshot never contains them, so a replace would wipe them.
      const steers = (msg.tools || []).filter((trace) => trace && trace.name === "fuse-steer");
      msg.tools = payload.tools.map((trace) => mergeToolEvent(prior.get(trace.id), trace, trace.status));
      for (const steer of steers) {
        if (!msg.tools.some((trace) => trace.id === steer.id)) msg.tools.push(steer);
      }
      const started = msg.tools.findLast(trace => trace.status === "running" && prior.get(trace.id)?.status !== "running");
      if (started) {
        msg._activity = { phase: "tools", tool_id: started.id };
        if (!child && activeRun?.message_id === msg.id) activeRun.activity = msg._activity;
      }
    }
    if (Array.isArray(payload.permissions)) {
      msg.permissions = payload.permissions;
      syncPermQueueFromMessage(msg);
    }
    if (payload.message) {
      // Child completion and parent checkpoints travel independently. An older
      // parent snapshot must not revive the same already-finished invocation.
      const finished = new Map((msg.tools || []).filter((trace) => trace.id && childBackedTool(trace.name) && !executionPending(trace) &&
        ["done", "error", "failed", "interrupted", "cancelled"].includes(trace.status)).map((trace) => [trace.id, trace]));
      Object.assign(msg, payload.message);
      (msg.tools || []).forEach((trace) => {
        const prior = finished.get(trace.id);
        if (prior && prior.child_id === trace.child_id && ["running", "progress", "queued"].includes(trace.status)) Object.assign(trace, prior);
      });
      ensureSteerSegments(msg);
      delete msg._raw;
      delete msg._reason;
      return;
    }
    for (const obj of payload.events || []) {
      if (obj.fuse) applyFuseEvent(msg, obj.fuse, true, child);
      else {
        const part = deltaParts(obj);
        if (part.error) msg.error = part.error;
        if (part.usage) msg.usage = part.usage;
        msg._raw = (msg._raw ?? msg.content ?? "") + (part.content || "");
        msg._reason = (msg._reason ?? msg.reasoning ?? "") + (part.reasoning || "");
        const split = splitThink(msg._raw);
        msg.content = split.content;
        msg.reasoning = msg._reason + (msg._reason && split.reasoning ? "\n" : "") + split.reasoning;
      }
    }
  }

  function ensureSteerSegments(msg) {
    if (!msg || !Array.isArray(msg.tools) || !Array.isArray(msg.segments)) return;
    for (const trace of msg.tools) {
      if (trace && trace.name === "fuse-steer" && trace.id &&
          !msg.segments.some((part) => part && part.type === "tool" && part.id === trace.id)) {
        msg.segments.push({ type: "tool", id: trace.id });
      }
    }
  }

  function applyFuseEvent(msg, event, passive = false, child = false) {
    if (!event || typeof event !== "object") return;
    if (event.event === "heartbeat") {
      if (!child) lastRunReceiptAt = Date.now();
      if (child && agentRun) agentRun.updated_at = new Date().toISOString();
      return;
    }
    if (event.event === "activity") {
      msg._activity = { ...event };
      if (!child && activeRun && (!event.run_id || event.run_id === activeRun.id)) activeRun.activity = { ...event };
      return;
    }
    if (event.event === "context") {
      // Subagent context meters describe the child's isolated window, not
      // this thread's occupancy; they must never move the thread ring.
      if (child) {
        if (agentsOpen && agentViewId && agentRun && (event.run_id === agentRun.id || event.run_id === agentViewId)) {
          agentRun.compaction = { meter: { ...event, scope: "prompt" } };
          syncAgentMeter(agentRun);
        }
        return;
      }
      compacting = event.phase === "compacting" || Boolean(event.compacting);
      // Every context event riding a run stream is a live prompt reading —
      // including the post-auto-compact "compacted" phase, which describes
      // the rebuilt in-flight prompt, not an at-rest transcript snapshot.
      acceptMeter({ ...event, scope: "prompt", compacting });
      if (event.phase === "compacted") compacting = false;
      return;
    }
    if (event.event === "message.patch") {
      for (const field of ["content", "reasoning"]) {
        const patch = event[field];
        if (!patch || !Number.isInteger(patch.from) || patch.from < 0 || patch.from > (msg[field] || "").length) {
          throw new Error("Output checkpoint mismatch; reconnecting.");
        }
        msg[field] = (msg[field] || "").slice(0, patch.from) + patch.text;
      }
      if (event.segments) {
        if (!Array.isArray(msg.segments)) msg.segments = [];
        const change = event.segments;
        if (Number.isInteger(change.from)) {
          if (change.from < 0 || change.from > msg.segments.length || !Array.isArray(change.items)) {
            throw new Error("Segment checkpoint mismatch; reconnecting.");
          }
          msg.segments.splice(change.from, msg.segments.length - change.from, ...change.items);
        } else {
          for (const patch of change.texts || []) {
            const segment = msg.segments[patch.index];
            if (!segment || (segment.type !== "content" && segment.type !== "reasoning") ||
                !Number.isInteger(patch.from) || patch.from < 0 || patch.from > segment.text.length) {
              throw new Error("Segment checkpoint mismatch; reconnecting.");
            }
            segment.text = segment.text.slice(0, patch.from) + patch.text;
          }
          if (Array.isArray(change.append)) {
            if (Number.isInteger(change.base)) {
              if (change.base === msg.segments.length) msg.segments.push(...change.append);
              else if (change.base < 0 || JSON.stringify(msg.segments.slice(change.base, change.base + change.append.length)) !== JSON.stringify(change.append)) {
                throw new Error("Segment append base mismatch; refetching snapshot.");
              }
            } else msg.segments.push(...change.append); // pre-upgrade replay
          }
        }
      }
      ensureSteerSegments(msg);
      msg.elapsedMs = event.elapsedMs;
      msg.usage = event.usage;
      msg.error = event.error || "";
      if (Number.isFinite(Number(event.streamOut)) && Number(event.streamOut) >= 0) {
        msg.streamOut = Math.round(Number(event.streamOut));
      }
      return;
    }
    if (event.event === "toast" && event.message) {
      const line = String(event.message).trim();
      if (line && window.fuseUI) {
        window.fuseUI.toast({
          key: "fuse-chat-toast:" + (msg && msg.id ? msg.id : "turn") + ":" + line,
          kind: event.kind || "idle",
          title: event.title || "Fuse",
          msg: line,
          ttl: 6000,
        });
      }
      return;
    }
    if (event.event === "notice" && event.message) {
      const line = String(event.message).trim();
      const marker = compactionMarkerText(line);
      if (marker) {
        if (!Array.isArray(msg.segments)) msg.segments = [];
        const handoff = String(event.handoff || "").trim();
        if (handoff && !marker.startsWith("Context pruned")) {
          msg.content = "";
          msg.reasoning = "";
          msg.tools = [];
          msg.error = "";
          delete msg._raw;
          delete msg._reason;
          msg.segments = [{ type: "marker", kind: "compaction", text: marker, summary: handoff }];
          return;
        }
        if (!msg.segments.some((part) => part && part.type === "marker" && part.text === marker)) {
          msg.segments.push({ type: "marker", kind: marker.startsWith("Context pruned") ? "prune" : "compaction", text: marker });
        }
        return;
      }
      if (line) {
        // Steers render as inline flow items (like tool rows) at the point
        // they happened — never as a banner pinned under the message.
        if (!Array.isArray(msg.tools)) msg.tools = [];
        const id = "steer-" + Date.now().toString(36) + "-" + Math.random().toString(36).slice(2, 8);
        msg.tools.push({ id, name: "fuse-steer", status: "done", summary: line });
        if (Array.isArray(msg.segments) && !msg.segments.some((part) => part.type === "tool" && part.id === id)) {
          msg.segments.push({ type: "tool", id });
        }
      }
      return;
    }
    if (event.event === "artifact" && event.identifier) {
      if (!passive) showArtifact(event.identifier, event).catch(() => {});
      return;
    }
    if (event.event === "permission") {
      if (!Array.isArray(msg.permissions)) msg.permissions = [];
      const ident = String(event.id || "");
      if (!ident) return;
        if (event.kind === "tool_permission") {
          const rec = {
            id: ident.slice(0, 64),
            kind: "tool_permission",
            status: ["pending", "allowed", "denied", "timeout", "cancelled"].includes(event.status) ? event.status : "denied",
            tool: String(event.tool || "").slice(0, 40),
            host: String(event.host || "").slice(0, 80),
            path: String(event.path || "").slice(0, 400),
            cwd: String(event.cwd || "").slice(0, 400),
            command: String(event.command || "").slice(0, 400),
            secret: Boolean(event.secret),
            requester: String(event.requester_label || event.requester || "Main agent").slice(0, 200),
            reasons: Array.isArray(event.reasons) ? event.reasons.map((r) => String(r).slice(0, 120)).slice(0, 4) : [],
          };
          if (event.expires_at) rec.expires_at = String(event.expires_at).slice(0, 40);
        if (["allow_session", "allow_host"].includes(event.grant_scope)) rec.grant_scope = event.grant_scope;
        if (event.grant_kind === "dir" || event.grant_kind === "file") rec.grant_kind = event.grant_kind;
        if (event.grant_value) rec.grant_value = String(event.grant_value).slice(0, 400);
        const pidx = msg.permissions.findIndex((p) => p.id === ident);
        if (pidx >= 0) msg.permissions[pidx] = { ...msg.permissions[pidx], ...rec };
        else msg.permissions.push(rec);
        if (rec.status === "pending") upsertPermCard({ ...rec });
        else {
          permCards.delete(ident);
          renderPermQueue();
          if (permsOpen && (rec.grant_scope === "allow_session" || rec.grant_scope === "allow_host")) refreshPerms();
        }
        return;
      }
      const rec = {
        id: ident.slice(0, 64),
        kind: "skill_save",
        status: event.status || "pending",
        slug: String(event.slug || "").slice(0, 64),
        description: String(event.description || "").slice(0, 1024),
        overwrite: Boolean(event.overwrite),
        enable: event.enable !== false,
        files: Array.isArray(event.files) ? event.files.map((n) => String(n).slice(0, 200)).slice(0, 50) : [],
        origin_existing: String(event.origin_existing || "").slice(0, 40),
      };
      const idx = msg.permissions.findIndex((p) => p.id === ident);
      if (idx >= 0) msg.permissions[idx] = { ...msg.permissions[idx], ...rec };
      else msg.permissions.push(rec);
      if (Array.isArray(msg.segments) && !msg.segments.some((part) => part.type === "permission" && part.id === ident)) {
        msg.segments.push({ type: "permission", id: ident });
      }
      return;
    }
    if (event.event !== "tool") return;
    if (!Array.isArray(msg.tools)) msg.tools = [];
    const byId = event.id ? msg.tools.findIndex((t) => t.id === event.id) : -1;
    if (event.status === "progress") {
      const rec = byId >= 0 ? msg.tools[byId] : null;
      if (!rec) return;
      rec.progressLabel = event.query ? `Searching “${event.query}” (${event.n ?? 0})` : rec.progressLabel;
      rec.progressN = event.n;
      rec.latency_ms = event.latency_ms;
      if (event.event_count != null) rec.event_count = event.event_count;
      if (event.output != null) rec.output = event.output;
      if (event.preview) rec.preview = event.preview;
      if (event.elapsed_ms != null) rec.elapsed_ms = event.elapsed_ms;
      return;
    }
    if (event.status === "running") {
      const activity = { phase: "tools", tool_id: event.id };
      msg._activity = activity;
      if (!child && activeRun && msg.id === activeRun.message_id) activeRun.activity = activity;
      if (mergeSubagentWait(msg, event, "running")) return;
      if (byId < 0) msg.tools.push(mergeToolEvent(null, event, "running"));
      else msg.tools[byId] = mergeToolEvent(msg.tools[byId], event, "running");
      if (Array.isArray(msg.segments) && event.id && !msg.segments.some((part) => part.type === "tool" && part.id === event.id)) {
        msg.segments.push({ type: "tool", id: event.id });
      }
      if (!passive && (event.name === "create_artifact" || event.name === "update_artifact")) {
        startArtifactBuild(event.title || (event.name === "update_artifact" ? "Updating artifact" : "New artifact"));
      }
      return;
    }
    if (mergeSubagentWait(msg, event, event.status || "done")) return;
    const rec = mergeToolEvent(byId >= 0 ? msg.tools[byId] : null, event, event.status || "done");
    if (byId >= 0) msg.tools[byId] = rec;
    else msg.tools.push(rec);
    if (Array.isArray(msg.segments) && rec.id && !msg.segments.some((part) => part.type === "tool" && part.id === rec.id)) {
      msg.segments.push({ type: "tool", id: rec.id });
    }
    if (!passive && (rec.name === "create_artifact" || rec.name === "update_artifact") && rec.identifier && rec.status === "done") {
      showArtifact(rec.identifier, rec).catch(() => {});
    }
  }

  function clearChatState() {
    drafts.set(currentId, input.value);
    stoppingRunId = "";
    activeRun = null;
    setRunning();
    permCards.clear();
    renderPermQueue();
    threadBypass = false;
    threadAuto = false;
    threadPermData = null;
    permsHostTab = null;
    syncPermChip();
    navigation += 1;
    loading = false;
    generation += 1;
    window.clearTimeout(saveTimer);
    messages = [];
    resetHistoryStacks();
    currentId = null;
    serverRevision = 0;
    viewCursor = 0;
    pendingCreate = null;
    pendingProject = null;
    pendingSave = null;
    input.value = drafts.get(null) || "";
    resizeInput();
    dirty = false;
    transcriptDirty = false;
    revision += 1;
    editingIndex = -1;
    sessionId = newSession();
    liveMeter = null;
    compacting = false;
    compactingRequest = false;
    contextWarned = false;
    contextAlertHidden = false;
    threadContext = "";
    searchOn = true;
    pendingAttachments = [];
    renderAttachList();
    setThreadUrl(null);
    paint();
    renderHistory();
    defaultHint();
    syncMemoryUi();
    setHistoryOpen(false);
    currentArtifact = null;
    setArtifactOpen(false);
    syncStatus();
    if (window.matchMedia("(hover: hover) and (pointer: fine)").matches) input.focus();
    if (window.FuseComposerSpell) {
      FuseComposerSpell.rebuildLearned([]);
      FuseComposerSpell.refresh();
    }
    projectListingCache = null;
  }

  async function resetChat() {
    if (sending) return;
    const nav = ++navigation;
    try {
      await flushPending();
    } catch (error) {
      showFailure("Start a new chat after the current chat is saved", error);
      return;
    }
    if (nav === navigation) {
      setHistoryTab("chats");
      clearChatState();
      resetAgents();
    }
  }

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    send();
  });
  if (contextBtn) {
    contextBtn.addEventListener("click", (event) => {
      event.preventDefault();
      if (!contextPop || contextWrap.hidden) return;
      const open = contextPop.hidden;
      contextPop.hidden = !open;
      contextBtn.setAttribute("aria-expanded", String(open));
      if (open) {
        closeSlash();
        closeRewind();
        syncContextMeter();
      }
    });
  }
  if (contextPopCompact) contextPopCompact.addEventListener("click", () => runCompact(""));
  if (contextAlertCompact) contextAlertCompact.addEventListener("click", () => runCompact(""));
  if (contextAlertDismiss) {
    contextAlertDismiss.addEventListener("click", () => {
      contextAlertHidden = true;
      hideContextAlert();
    });
  }
  let _meterDebounce = 0;
  function queueContextMeter() {
    // localOccupancy is O(transcript); recount at most 300ms after typing
    // stops instead of on every keystroke of a long conversation.
    window.clearTimeout(_meterDebounce);
    _meterDebounce = window.setTimeout(() => { _meterDebounce = 0; syncContextMeter(); }, 300);
  }

  let emptyCollapseTimer = 0;
  function armEmptyCollapse() {
    window.clearTimeout(emptyCollapseTimer);
    emptyCollapseTimer = 0;
    if (window.innerWidth > 800 || input.value) return;
    // Some phones hide the keyboard without blurring or resizing. An empty
    // field with no further keystrokes is that case; typing reopens it.
    emptyCollapseTimer = window.setTimeout(() => {
      emptyCollapseTimer = 0;
      if (!input.value && document.activeElement === input) setComposerExpanded(false);
    }, 700);
  }
  input.addEventListener("input", () => {
    if (input.value) setComposerExpanded(true);
    armEmptyCollapse();
    drafts.set(currentId, input.value);
    resizeInput();
    syncComposer();
    queueContextMeter();
    syncEmptyComposer();
    slashIndex = 0;
    if (window.FuseComposerSpell && FuseComposerSpell.composing) {
      closeSlash();
      return;
    }
    renderComposerSuggestions();
    if (window.FuseComposerSpell) FuseComposerSpell.debounceCheck();
  });
  input.addEventListener("focus", () => {
    setComposerExpanded(true);
    armEmptyCollapse();
    document.documentElement.scrollTop = 0;
    document.body.scrollTop = 0;
    pinVisualViewport();
  });
  // Re-tapping a still-focused field after keyboard dismissal must reopen it.
  input.addEventListener("pointerdown", () => setComposerExpanded(true));
  input.addEventListener("blur", () => {
    setComposerExpanded(false);
    window.setTimeout(syncEmptyComposer, 0);
  });
  ["focusin", "focusout"].forEach((type) => root.addEventListener(type, (event) => {
    const target = event.target instanceof Element ? event.target : null;
    const related = event.relatedTarget instanceof Element ? event.relatedTarget : null;
    const field = "textarea, input, [contenteditable='true']";
    const moved =
      Boolean(target?.matches(field) || related?.matches(field) ||
        target?.closest(".chat-sheet, .chat-view-menu") ||
        related?.closest(".chat-sheet, .chat-view-menu"));
    if (!moved) return;
    pinVisualViewport();
    window.setTimeout(pinVisualViewport, 100);
    window.setTimeout(pinVisualViewport, 350);
    window.setTimeout(pinVisualViewport, 700);
  }));
  function isComposerPrintable(event) {
    return event.key.length === 1 && !event.ctrlKey && !event.metaKey && !event.altKey;
  }
  input.addEventListener("keydown", (event) => {
    if (event.isComposing || event.keyCode === 229) return;

    const spellOpen = window.FuseComposerSpell && FuseComposerSpell.isOpen();
    const slashOpen = slashMenu && !slashMenu.hidden;
    const extraOpen = extraPaletteOpen();
    const slashItems = slashOpen && slashKind === "slash" ? filteredSlash() : paletteItems;

    if (streaming && event.key === "Enter" && !event.shiftKey) {
      // Enter never stops a running turn: it queues the message. Send now on
      // a queued row injects that message at the next tool boundary instead.
      event.preventDefault();
      queueIncomingTurn();
      return;
    }

    if (event.key === "Enter" && event.shiftKey) {
      if (spellOpen) FuseComposerSpell.close();
      else if (extraOpen) closeSlash();
      return;
    }

    if (spellOpen) {
      if (["ArrowDown", "ArrowUp", "Tab", "Enter", "Escape"].includes(event.key)) {
        event.preventDefault();
        if (event.key === "Escape") FuseComposerSpell.close();
        else if (event.key === "Enter" || event.key === "Tab") FuseComposerSpell.accept();
        else FuseComposerSpell.move(event.key === "ArrowDown" ? 1 : -1);
        if (!FuseComposerSpell.isOpen()) restoreEnterKeyHint();
        syncComposerAria();
        return;
      }
      if (isComposerPrintable(event) || event.key === "Backspace" || event.key === "Delete") {
        FuseComposerSpell.close();
        restoreEnterKeyHint();
        syncComposerAria();
      }
    }

    if (slashOpen && (event.key === "ArrowDown" || event.key === "ArrowUp")) {
      event.preventDefault();
      const max = Math.max(0, (slashKind === "slash" ? filteredSlash() : paletteItems).length - 1);
      slashIndex = event.key === "ArrowDown"
        ? Math.min(max, slashIndex + 1)
        : Math.max(0, slashIndex - 1);
      if (slashKind === "slash") renderSlash();
      else renderPalette(slashKind, paletteItems, paletteTrigger);
      return;
    }
    if (slashOpen && event.key === "Tab") {
      event.preventDefault();
      acceptCurrentPalette();
      return;
    }
    if (slashOpen && event.key === "Escape") {
      event.preventDefault();
      closeSlash();
      return;
    }
    if (slashOpen && event.key === "Enter") {
      event.preventDefault();
      acceptCurrentPalette();
      return;
    }

    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "z" && !input.value) {
      event.preventDefault();
      if (event.shiftKey) redoConversation();
      else undoConversation();
      return;
    }

    if (event.key === "Enter" && event.altKey && !event.shiftKey) {
      event.preventDefault();
      if (window.FuseComposerSpell) FuseComposerSpell.openAtCaret();
      syncComposerAria();
      return;
    }

    if (event.key === "ArrowUp" && !input.value && !slashOpen && !spellOpen) {
      event.preventDefault();
      openHistoryPeek();
      return;
    }

    if (event.key === "Enter") {
      event.preventDefault();
      send();
    }
  });
  if (searchChip) {
    searchChip.addEventListener("click", (event) => {
      event.preventDefault();
      if (!requireLive()) return;
      searchOn = !searchOn;
      persist();
      syncMemoryUi();
    });
  }

  const ATTACH_MAX = Number(config.limits && config.limits.attachments) || 4;
  const ATTACH_CHARS = Number(config.limits && config.limits.attachment_chars) || 1500000;
  const TEXT_EXTS = /\.(txt|md|markdown|csv|json|xml|html|htm|css|js|ts|py|rb|go|rs|java|c|h|cpp|sh|yml|yaml|toml|ini|log)$/i;

  function renderAttachList() {
    if (!attachList) return;
    if (!pendingAttachments.length) {
      attachList.hidden = true;
      attachList.innerHTML = "";
      syncComposer();
      syncContextMeter();
      syncEmptyComposer();
      return;
    }
    attachList.hidden = false;
    attachList.innerHTML = pendingAttachments
      .map((att, index) => {
        const thumb =
          att.kind === "image" && att.data
            ? `<button type="button" class="chat-attach-thumb" data-image-preview="${escapeHtml(att.data)}" data-image-name="${escapeHtml(att.name || "")}" aria-label="Preview ${escapeHtml(att.name || "image")}"><img src="${escapeHtml(att.data)}" alt=""></button>`
            : `<span class="chat-attach-icon">${FILE_CHIP_ICON}</span>`;
        return `<div class="chat-attach-chip">${thumb}<span>${escapeHtml(att.name || "File")}</span><button type="button" data-remove-attach="${index}" aria-label="Remove ${escapeHtml(att.name || "file")}">×</button></div>`;
      })
      .join("");
    syncComposer();
    syncContextMeter();
    syncEmptyComposer();
  }

  function readAsDataUrl(file) {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(String(reader.result || ""));
      reader.onerror = () => reject(new Error("Could not read " + (file.name || "file")));
      reader.readAsDataURL(file);
    });
  }

  function loadImage(url) {
    return new Promise((resolve, reject) => {
      const img = new Image();
      img.onload = () => resolve(img);
      img.onerror = () => reject(new Error("Could not read image"));
      img.src = url;
    });
  }

  async function resizeImage(file) {
    const original = await readAsDataUrl(file);
    if (file.type === "image/gif" && original.length <= ATTACH_CHARS) return { url: original, mime: "image/gif" };
    const img = await loadImage(original);
    const maxEdge = 1280;
    const scale = Math.min(1, maxEdge / Math.max(img.width || 1, img.height || 1));
    const width = Math.max(1, Math.round((img.width || 1) * scale));
    const height = Math.max(1, Math.round((img.height || 1) * scale));
    const canvas = document.createElement("canvas");
    canvas.width = width;
    canvas.height = height;
    const ctx = canvas.getContext("2d");
    if (!ctx) throw new Error("Could not process image");
    ctx.drawImage(img, 0, 0, width, height);
    let quality = 0.84;
    let url = canvas.toDataURL("image/jpeg", quality);
    while (url.length > ATTACH_CHARS && quality > 0.5) {
      quality -= 0.08;
      url = canvas.toDataURL("image/jpeg", quality);
    }
    if (url.length > ATTACH_CHARS) throw new Error((file.name || "Image") + " is too large after shrinking");
    return { url, mime: "image/jpeg" };
  }

  function isTextFile(file) {
    const mime = (file.type || "").toLowerCase();
    if (mime.startsWith("text/")) return true;
    if (["application/json", "application/xml", "text/xml", "text/markdown", "text/csv"].includes(mime)) return true;
    return TEXT_EXTS.test(file.name || "");
  }

  async function fileToAttachment(file) {
    const mime = (file.type || "").toLowerCase();
    const name = (file.name || "attachment").slice(0, 200);
    if (mime.startsWith("image/") || /\.(png|jpe?g|gif|webp)$/i.test(name)) {
      const data = await resizeImage(file);
      return { id: newSession(), name, mime: data.mime, kind: "image", data: data.url };
    }
    if (mime === "application/pdf" || /\.pdf$/i.test(name)) {
      const url = await readAsDataUrl(file);
      if (url.length > ATTACH_CHARS) throw new Error((name || "PDF") + " is too large (about 1 MB max)");
      return { id: newSession(), name, mime: "application/pdf", kind: "file", data: url };
    }
    if (isTextFile(file)) {
      const text = await file.text();
      if (text.length > 200000) throw new Error((name || "File") + " is too long");
      return { id: newSession(), name, mime: mime || "text/plain", kind: "text", data: text };
    }
    throw new Error("Attach an image, PDF, or text file.");
  }

  async function addFiles(fileList) {
    const files = [...(fileList || [])];
    if (!files.length) return;
    for (const file of files) {
      if (pendingAttachments.length >= ATTACH_MAX) {
        setHint("Up to " + ATTACH_MAX + " attachments per message.", true, 4000);
        break;
      }
      try {
        pendingAttachments.push(await fileToAttachment(file));
      } catch (error) {
        showFailure("Could not attach file", error);
      }
    }
    renderAttachList();
  }

  if (attachBtn && fileInput) {
    attachBtn.addEventListener("click", (event) => {
      event.preventDefault();
      fileInput.value = "";
      fileInput.click();
    });
    fileInput.addEventListener("change", () => addFiles(fileInput.files));
  }
  if (attachList) {
    attachList.addEventListener("click", (event) => {
      const view = event.target.closest("[data-image-preview]");
      if (view) {
        event.preventDefault();
        openLightbox(view.getAttribute("data-image-preview"), view.getAttribute("data-image-name") || "");
        return;
      }
      const btn = event.target.closest("[data-remove-attach]");
      if (!btn) return;
      pendingAttachments.splice(Number(btn.getAttribute("data-remove-attach")), 1);
      renderAttachList();
    });
  }
  if (chatBox) {
    ["dragenter", "dragover"].forEach((type) => {
      chatBox.addEventListener(type, (event) => {
        if (![...event.dataTransfer.types].includes("Files")) return;
        event.preventDefault();
        chatBox.classList.add("is-drop");
      });
    });
    ["dragleave", "drop"].forEach((type) => {
      chatBox.addEventListener(type, (event) => {
        event.preventDefault();
        chatBox.classList.remove("is-drop");
      });
    });
    chatBox.addEventListener("drop", (event) => {
      if (event.dataTransfer && event.dataTransfer.files && event.dataTransfer.files.length) {
        addFiles(event.dataTransfer.files);
      }
    });
  }
  input.addEventListener("paste", (event) => {
    const items = event.clipboardData && event.clipboardData.items;
    if (!items) return;
    const files = [];
    for (const item of items) {
      if (item.kind === "file") {
        const file = item.getAsFile();
        if (file) files.push(file);
      }
    }
    if (files.length) {
      event.preventDefault();
      addFiles(files);
    }
  });
  function artifactContentUrl(id, version) {
    const path = "/a/" + encodeURIComponent(id) + "/content";
    return version ? path + "?v=" + encodeURIComponent(version) : path;
  }

  function artifactsInThread() {
    const out = [];
    messages.forEach((msg) => {
      (msg.tools || []).forEach((trace) => {
        if (
          (trace.name === "create_artifact" || trace.name === "update_artifact") &&
          trace.identifier &&
          trace.status !== "error"
        ) {
          out.push(trace);
        }
      });
    });
    return out;
  }

  function latestArtifact() {
    const all = artifactsInThread();
    return all.length ? all[all.length - 1] : null;
  }

  function rememberLatestArtifact() {
    const last = latestArtifact();
    if (!last) {
      currentArtifact = null;
      artifactOpen = false;
      syncArtifactUi();
      return;
    }
    if (currentArtifact && currentArtifact.building) {
      syncArtifactUi();
      return;
    }
    currentArtifact = {
      identifier: last.identifier,
      title: last.title || "Artifact",
      visibility: last.visibility || "",
      version: last.version,
      url: last.url || "/a/" + last.identifier,
    };
    syncArtifactUi();
  }

  function syncArtifactUi() {
    if (artifactPanel) artifactPanel.hidden = !artifactOpen;
    if (artifactBackdrop) artifactBackdrop.hidden = !(artifactOpen && window.matchMedia("(max-width: 800px)").matches);
    root.classList.toggle("is-artifact", artifactOpen);
    if (artifactToggle) {
      const has = Boolean(latestArtifact() || (currentArtifact && currentArtifact.building));
      artifactToggle.disabled = !has;
      artifactToggle.setAttribute("aria-disabled", has ? "false" : "true");
      artifactToggle.setAttribute("aria-pressed", artifactOpen ? "true" : "false");
      artifactToggle.setAttribute("aria-expanded", artifactOpen ? "true" : "false");
      artifactToggle.classList.toggle("is-on", artifactOpen);
      artifactToggle.title = has ? "Artifact preview" : "No artifact in this chat yet";
    }
    const building = Boolean(currentArtifact && currentArtifact.building);
    const ident = currentArtifact && currentArtifact.identifier;
    if (artifactTitleEl) artifactTitleEl.textContent = (currentArtifact && currentArtifact.title) || "Artifact";
    if (artifactStatusEl) {
      artifactStatusEl.hidden = !building;
      artifactStatusEl.textContent = building ? "Building…" : "";
    }
    if (artifactVisibilityEl) {
      const vis = currentArtifact && currentArtifact.visibility;
      artifactVisibilityEl.hidden = !vis;
      artifactVisibilityEl.textContent = vis || "";
      artifactVisibilityEl.classList.toggle("on", vis === "public");
    }
    if (artifactOpenLink) {
      if (ident) {
        artifactOpenLink.hidden = false;
        artifactOpenLink.href = "/a/" + encodeURIComponent(ident);
      } else {
        artifactOpenLink.hidden = true;
        artifactOpenLink.removeAttribute("href");
      }
    }
    if (artifactBuilding) artifactBuilding.hidden = !building;
    if (artifactFrame) {
      artifactFrame.hidden = building || artifactMode !== "preview" || !ident;
    }
    if (artifactSource) artifactSource.hidden = building || artifactMode !== "source";
    artifactModeBtns.forEach((btn) => {
      btn.setAttribute("aria-pressed", btn.getAttribute("data-artifact-mode") === artifactMode ? "true" : "false");
    });
    const versions = (currentArtifact && currentArtifact.versions) || [];
    if (artifactVersionWrap && artifactVersionEl) {
      if (versions.length > 1) {
        artifactVersionWrap.hidden = false;
        const current = String(currentArtifact.version || versions[versions.length - 1]);
        if (artifactVersionEl._sig !== versions.join(",") + current) {
          artifactVersionEl._sig = versions.join(",") + current;
          artifactVersionEl.innerHTML = versions
            .slice()
            .reverse()
            .map((ver) => `<option value="${ver}"${String(ver) === current ? " selected" : ""}>v${ver}</option>`)
            .join("");
        }
      } else {
        artifactVersionWrap.hidden = true;
      }
    }
  }

  function setArtifactOpen(open) {
    const next = Boolean(open);
    if (next === artifactOpen) {
      if (next) syncArtifactUi();
      return;
    }
    artifactOpen = next;
    if (artifactOpen) {
      if (memoryOpen) setMemoryOpen(false);
      if (skillsOpen) setSkillsOpen(false);
      if (agentsOpen) setAgentsOpen(false);
      if (overlayIsMobile()) setHistoryOpen(false);
      else if (window.matchMedia("(max-width: 1100px)").matches) {
        root.classList.add("history-collapsed");
        if (typeof syncHistory === "function") syncHistory();
      }
      closePicker();
      overlayPush("artifact");
    } else {
      armOverlayGhost(root.querySelector("[data-artifact-close]"));
      overlayDrop("artifact");
    }
    syncArtifactUi();
    scheduleFitChatBar();
  }

  function agentLive(status) {
    return status === "queued" || status === "running";
  }

  function agentQuietSeconds(row) {
    if (row && Number.isFinite(Number(row.quiet_s))) return Math.max(0, Number(row.quiet_s));
    return agentElapsedSeconds(row && row.updated_at);
  }

  function agentIsStalled(row) {
    if (!row || !agentLive(row.status) || row.cancel_requested) return false;
    if (row.current === "stalled") return true;
    return agentQuietSeconds(row) >= 180;
  }

  function agentIsWaiting(row) {
    if (!row || !agentLive(row.status) || row.cancel_requested || agentIsStalled(row)) return false;
    if (row.current === "waiting") return true;
    return agentQuietSeconds(row) >= 60;
  }

  function agentStatusText(row) {
    if (row.cancel_requested) {
      const stamp = Date.parse(row.cancel_requested_at || "");
      const secs = Number.isFinite(stamp) ? Math.max(0, (Date.now() - stamp) / 1000) : 0;
      if (secs >= 30) return "wedged — force stop";
      return secs >= 15 ? `stopping ${formatActivityClock(secs)}` : "stopping";
    }
    if (row.status === "queued") return "queued";
    if (row.status === "completed") return "completed";
    if (row.status === "failed") return "failed";
    if (row.status === "cancelled") return "cancelled";
    if (row.status === "interrupted") return "interrupted";
    if (agentIsStalled(row)) return "stalled";
    if (agentIsWaiting(row)) return "waiting on model";
    if (row.status === "running") return "running";
    return row.status || "unknown";
  }

  function agentBadgeClass(row) {
    if (!row) return "badge";
    if (row.cancel_requested) return "badge live";
    if (row.status === "completed") return "badge on";
    if (row.status === "failed" || agentIsStalled(row)) return "badge fault";
    if (agentLive(row.status)) return "badge live";
    if (row.status === "cancelled" || row.status === "interrupted") return "badge stop";
    return "badge";
  }

  function agentElapsedSeconds(iso) {
    const t = Date.parse(iso || "");
    return Number.isFinite(t) ? Math.max(0, (Date.now() - t) / 1000) : 0;
  }

  function agentDurationSeconds(row) {
    const start = Date.parse(row.created_at || "");
    if (!Number.isFinite(start)) return null;
    let end = Date.parse(row.updated_at || "");
    if (!Number.isFinite(end) || agentLive(row.status)) end = Date.now();
    return Math.max(0, (end - start) / 1000);
  }

  function agentRowsSorted() {
    const live = agentsRows
      .filter((row) => agentLive(row.status))
      .sort((a, b) => agentElapsedSeconds(b.created_at) - agentElapsedSeconds(a.created_at));
    const done = agentsRows.filter((row) => !agentLive(row.status));
    return { live, done };
  }

  const AGENT_ACTIVITY_LABELS = {
    run_terminal_command: "shell",
    get_command_output: "job",
    kill_command: "kill",
    monitor: "watch",
    kill_subagent: "kill",
    web_search: "search",
    read_url: "page",
    read_file: "file",
    list_dir: "dir",
    write_file: "write",
    search_replace: "edit",
    grep: "grep",
    glob: "glob",
    spawn_subagent: "agent",
    get_subagent_output: "wait",
    list_subagents: "agents",
    search_models: "models",
    load_skill: "skill",
    todo_write: "todo",
    create_artifact: "artifact",
    update_artifact: "artifact",
    search_tools: "tools",
  };

  function agentActivitySummary(row) {
    const entries = row.tool_counts && typeof row.tool_counts === "object" ? Object.entries(row.tool_counts) : [];
    const counts = entries
      .filter(([, n]) => Number(n) > 0)
      .map(([name, n]) => `${n} ${AGENT_ACTIVITY_LABELS[name] || name}`)
      .join(", ");
    if (agentIsStalled(row)) return counts ? `stalled · ${counts}` : "stalled";
    if (agentIsWaiting(row) && (!row.current || row.current === "thinking" || row.current === "waiting")) {
      return counts ? `waiting · ${counts}` : "waiting on model";
    }
    if (!row.current) return counts;
    const label = row.current === "thinking" ? "thinking" : AGENT_ACTIVITY_LABELS[row.current] || row.current;
    return counts ? `${label} · ${counts}` : label;
  }

  function agentRowHtml(row) {
    const ctx = row.context && Number(row.context.limit) > 0
      ? `${Math.round((Number(row.context.used) || 0) / Number(row.context.limit) * 100)}% ctx`
      : "";
    const meta = [
      row.subagent_type && row.subagent_type !== "general-purpose" ? row.subagent_type : "",
      row.children ? `${row.children} spawn${row.children === 1 ? "" : "s"}` : "",
      row.model || "",
      ctx,
      row.resumable && !agentLive(row.status) ? "resumable" : "",
    ].filter(Boolean).join(" · ");
    const dur = agentDurationSeconds(row);
    const err = String(row.error || "").trim();
    const wake = row.wake_state === "dropped" ? "report not delivered" : row.wake_state === "retrying" ? "report pending" : "";
    const sum = err || wake || agentActivitySummary(row);
    return (
      `<button type="button" class="chat-agents-row is-${escapeHtml(row.status || "unknown")}${row.wake_state === "dropped" ? " is-undelivered" : ""}${agentIsStalled(row) ? " is-stalled" : ""}" data-agent-id="${escapeHtml(row.id)}">` +
      '<span class="chat-agents-dot" aria-hidden="true"></span>' +
      `<span class="chat-agents-rowcopy"><span class="chat-agents-name">${escapeHtml(row.description || "Subagent")}</span>` +
      `<span class="chat-agents-meta">${escapeHtml(agentStatusText(row))}${meta ? " · " + escapeHtml(meta) : ""}${dur != null ? " · " + formatActivityClock(dur) : ""}</span>` +
      (sum ? `<span class="chat-agents-sum">${escapeHtml(sum.slice(0, 180))}</span>` : "") +
      '</span><span class="chat-agents-open" aria-hidden="true">Open</span></button>'
    );
  }

  function renderAgentsList() {
    if (!agentsListEl) return;
    if (toolGestureOwner === agentsListEl) { pendingAgentListPaint = true; return; }
    const { live, done } = agentRowsSorted();
    let html = "";
    if (live.length) html += '<p class="chat-agents-kicker">Live · longest first</p>' + live.map(agentRowHtml).join("");
    if (done.length) html += '<p class="chat-agents-kicker">Finished</p>' + done.map(agentRowHtml).join("");
    FuseChatDOM.reconcile(agentsListEl,
      html || '<p class="chat-agents-empty">No subagents yet. The main agent spawns them for isolated sub-tasks while it works.</p>');
  }

  function renderAgentHead() {
    if (!agentViewId || !agentRun) return;
    if (agentTitleEl) agentTitleEl.textContent = agentRun.description || "Subagent";
    if (agentStatusEl) {
      agentStatusEl.textContent = agentStatusText(agentRun);
      agentStatusEl.className = agentBadgeClass(agentRun);
    }
    if (agentStopBtn) agentStopBtn.hidden = !agentLive(agentRun.status);
    const stamp = Date.parse(agentRun.cancel_requested_at || "");
    const stoppingSecs = Number.isFinite(stamp) ? Math.max(0, (Date.now() - stamp) / 1000) : 0;
    if (agentForceBtn) {
      agentForceBtn.hidden = !agentLive(agentRun.status);
      agentForceBtn.classList.toggle("is-urgent", Boolean(agentRun.cancel_requested && stoppingSecs >= 15));
    }
    if (agentResumeBtn) agentResumeBtn.hidden = !agentResumeAvailable(agentRun);
  }

  function agentResumeAvailable(run) {
    if (!run || agentLive(run.status)) return false;
    // Mirror chat_store.resume_run: only interrupted/cancelled. Showing Resume
    // for a failed (e.g. provider error) run would just 409; continuing those
    // happens via spawn_subagent task_id in the main chat instead.
    if (run.status !== "interrupted" && run.status !== "cancelled") return false;
    if (run.resume_count != null && Number(run.resume_count) >= 3) return false;
    if (run.resumable != null) return Boolean(run.resumable);
    return Boolean(run.has_transcript);
  }

  function compactionMarkerText(line) {
    const text = String(line || "").trim().toLowerCase();
    if (text.startsWith("context compacted")) return "Context compacted. Earlier turns were replaced with a handoff. Recent messages are unchanged.";
    if (text.startsWith("context pruning")) return "Context pruned. Older tool outputs were shortened; recent results are unchanged.";
    return "";
  }

  function compactionMarkHtml(text, summary) {
    const body = String(text || "").trim();
    const title = body.startsWith("Context pruned") ? "Context pruned" : "Context compacted";
    const handoff = String(summary || "").trim();
    if (title === "Context compacted" && handoff) {
      return (
        `<details class="chat-compact-banner" data-compaction>` +
        `<summary><strong>Compaction</strong><span>Handoff sent to the model</span></summary>` +
        `<div class="chat-compact-handoff">${renderMarkdown(handoff, false, [])}</div>` +
        `</details>`
      );
    }
    const detail = body.startsWith(title) ? body.slice(title.length).replace(/^[.\s]+/, "") : body;
    return `<p class="chat-compact-mark" data-compaction><strong>${escapeHtml(title)}</strong>${detail ? `<span>${escapeHtml(detail)}</span>` : ""}</p>`;
  }

  function agentSeedArticle(item, index) {
    // Same renderers as the main chat so every styling/behavior tweak
    // (tool timelines, themes, bubbles) applies here automatically.
    if (item.role === "marker" || item.kind === "compaction" || item.compaction) {
      const article = document.createElement("article");
      article.className = "chat-turn is-marker";
      article.innerHTML = compactionMarkHtml(item.content || "Context compacted", item.summary || item.content || "");
      return article;
    }
    if (item.role === "user") {
      // live=true suppresses main-chat-bound actions; the negative index
      // can never collide with a main-chat editingIndex.
      return renderUser({
        role: "user",
        content: String(item.content || ""),
        auto: Boolean(item.auto),
        attachments: Array.isArray(item.attachments) ? item.attachments : [],
      }, -2 - index, true);
    }
    return renderAssistant(
      {
        id: item.id || ("agent-seed-" + index),
        role: "assistant",
        content: String(item.content || ""),
        reasoning: String(item.reasoning || ""),
        error: String(item.error || ""),
        usage: item.usage || null,
        tools: Array.isArray(item.tools) ? item.tools : [],
        segments: Array.isArray(item.segments) ? item.segments : undefined,
        notice: item.notice || "",
        elapsedMs: item.elapsedMs ?? null,
        stopped: Boolean(item.stopped),
        model: item.model || "",
        noActions: true,
      },
      false,
    );
  }

  function syncAgentMeter(run) {
    if (!agentMeterEl) return;
    const meter = run && run.compaction && run.compaction.meter;
    const used = Number(meter && meter.used);
    const limit = Number(meter && meter.limit);
    if (!meter || !Number.isFinite(used) || !Number.isFinite(limit) || limit <= 0) {
      agentMeterEl.hidden = true;
      return;
    }
    const ratio = Math.max(0, Math.min(1, used / limit));
    const pct = Math.round(ratio * 100);
    agentMeterEl.hidden = false;
    if (agentContextFill) {
      agentContextFill.style.strokeDasharray = String(CONTEXT_RING);
      agentContextFill.style.strokeDashoffset = String(CONTEXT_RING * (1 - ratio));
    }
    const btn = agentMeterEl.querySelector("[data-agent-context]");
    if (btn) {
      btn.classList.toggle("is-warn", Boolean(meter.warn) && !meter.auto);
      btn.classList.toggle("is-auto", Boolean(meter.auto));
      btn.classList.toggle("is-busy", Boolean(meter.compacting));
      const title = `${formatTokens(used)} / ${formatTokens(limit)} tokens (${pct}%)`;
      btn.setAttribute("aria-label", "Subagent context usage " + title);
      btn.title = title;
    }
    if (agentMeterLabel) agentMeterLabel.textContent = `${formatTokens(used)} / ${formatTokens(limit)} · ${pct}%`;
  }

  function agentNearBottom() {
    if (!agentLogEl) return true;
    const gap = agentLogEl.scrollHeight - agentLogEl.scrollTop - agentLogEl.clientHeight;
    return gap < 96;
  }

  function restoreAgentScroll(wasAtBottom, savedTop) {
    if (!agentLogEl) return;
    if (!wasAtBottom) {
      if (agentFollowFrame) cancelAnimationFrame(agentFollowFrame);
      agentFollowFrame = 0;
      agentLogEl.scrollTop = savedTop;
      requestAnimationFrame(() => { agentIgnoreScroll = false; });
      return;
    }
    agentStick = true;
    if (agentFollowFrame) return;
    let last = performance.now();
    const step = now => {
      if (!agentStick || !agentLogEl || !agentsOpen) { agentFollowFrame = 0; agentIgnoreScroll = false; return; }
      agentIgnoreScroll = true;
      const target = Math.max(0, agentLogEl.scrollHeight - agentLogEl.clientHeight);
      const gap = target - agentLogEl.scrollTop;
      if (!agentTailInitialized || reduceMotion.matches || !agentLive(agentRun?.status) || Math.abs(gap) <= 1) {
        agentLogEl.scrollTop = target;
        agentTailInitialized = true; agentFollowFrame = 0;
        requestAnimationFrame(() => { if (!agentFollowFrame) agentIgnoreScroll = false; });
        return;
      }
      const dt = Math.min(48, Math.max(8, now - last)); last = now;
      agentLogEl.scrollTop += Math.sign(gap) * Math.max(1, Math.abs(gap) * (1 - Math.exp(-dt / 48)));
      agentFollowFrame = requestAnimationFrame(step);
    };
    agentFollowFrame = requestAnimationFrame(step);
  }

  function syncAgentLiveDock() {
    if (!agentLiveDock) return;
    const stalled = Boolean(agentRun && agentIsStalled(agentRun));
    const live = Boolean(agentViewId && agentsOpen && agentRun && agentLive(agentRun.status) && agentViewFailCount < 3 && !stalled);
    const meter = agentRun && agentRun.compaction && agentRun.compaction.meter;
    const hasMeter = Boolean(meter && Number(meter.limit) > 0);
    if (!live && !hasMeter && !stalled) {
      if (!agentLiveDock.hidden) agentLiveDock.hidden = true;
      agentLiveDock.setAttribute("aria-hidden", "true");
      if (agentLiveSpin && (agentLiveSpin.firstChild || agentLiveSpin.dataset.spinSet)) {
        agentLiveSpin.replaceChildren();
        delete agentLiveSpin.dataset.spinSet;
      }
      return;
    }
    agentLiveDock.hidden = false;
    agentLiveDock.setAttribute("aria-hidden", "false");
    if (agentLiveSpin) agentLiveSpin.hidden = !live;
    const shimmer = agentLiveDock.querySelector(".chat-status-shimmer");
    if (shimmer) shimmer.hidden = !live;
    if (stalled) {
      setLiveLabelText(agentLiveLabel, "Stalled — no progress");
      if (agentLiveSpin && (agentLiveSpin.firstChild || agentLiveSpin.dataset.spinSet)) {
        agentLiveSpin.replaceChildren();
        delete agentLiveSpin.dataset.spinSet;
      }
    } else if (live) {
      const msg = agentMsgState || {};
      if (!msg._spinOrigin) msg._spinOrigin = Date.now();
      const groups = groupTraces(Array.isArray(msg.tools) ? msg.tools : []);
      const status = currentActivity(msg, groups, true);
      setLiveLabelText(agentLiveLabel, status.label);
      if (agentLiveSpin) applySpinEl(agentLiveSpin, activitySpinSet(msg), "agent-dock", true, msg._spinOrigin);
    } else if (agentLiveSpin && (agentLiveSpin.firstChild || agentLiveSpin.dataset.spinSet)) {
      agentLiveSpin.replaceChildren();
      delete agentLiveSpin.dataset.spinSet;
    }
  }

  function renderAgentView() {
    if (!agentRun || !agentLogEl || !agentBodyEl) return;
    if ([...toolMotions.keys()].some(owner => agentBodyEl.contains(owner))) { pendingAgentPaint = true; return; }
    if (toolGestureOwner && agentBodyEl.contains(toolGestureOwner)) { pendingAgentPaint = true; return; }
    renderAgentHead();
    syncAgentMeter(agentRun);
    const live = agentLive(agentRun.status);
    const wasAtBottom = agentStick;
    const savedTop = agentLogEl.scrollTop;
    const savedPeeks = new Map(
      [...agentBodyEl.querySelectorAll("[data-reason-preview]")].map((el) => [
        el.getAttribute("data-reason-preview"),
        thinkingPosition(el),
      ])
    );
    // Arm before mutating: clearing the log clamps scrollTop and fires a
    // scroll event that must not flip agentStick back on.
    agentIgnoreScroll = true;
    const raw = agentRun.message || {};
    const id = agentRun.message_id;
    agentMsgState = agentMsgState && agentMsgState.id === id ? agentMsgState : { id, role: "assistant", noActions: true };
    Object.assign(agentMsgState, {
      content: raw.content || "",
      reasoning: raw.reasoning || "",
      error: raw.error || "",
      usage: raw.usage || null,
      tools: Array.isArray(raw.tools) ? raw.tools : [],
      segments: Array.isArray(raw.segments) ? raw.segments : undefined,
      notice: raw.notice || "",
      stopped: Boolean(raw.stopped),
    });
    const seed = Array.isArray(agentRun.messages) ? agentRun.messages : [];
    const compacted = seed.some((item) => item && (item.compaction || item.kind === "compaction"));
    if (compacted) {
      // The seed is the post-handoff provider transcript. A live bubble that
      // still lists tools from before that handoff is not part of the session.
      const kept = new Set();
      seed.forEach((item) => (item.tools || []).forEach((tool) => tool && tool.id && kept.add(tool.id)));
      const handoffAt = (agentMsgState.segments || []).findLastIndex((part) => part && part.type === "marker" && part.kind !== "prune" && part.summary);
      if (handoffAt >= 0) {
        agentMsgState.segments = agentMsgState.segments.slice(handoffAt);
        const liveIds = new Set(agentMsgState.segments.filter((part) => part && part.type === "tool" && part.id).map((part) => part.id));
        agentMsgState.tools = (agentMsgState.tools || []).filter((tool) => tool && liveIds.has(tool.id));
      } else if (kept.size) {
        agentMsgState.tools = (agentMsgState.tools || []).filter((tool) => tool && kept.has(tool.id));
        if (Array.isArray(agentMsgState.segments)) {
          agentMsgState.segments = agentMsgState.segments.filter((part) => !part || part.type !== "tool" || kept.has(part.id));
        }
      } else {
        agentMsgState.tools = [];
        if (Array.isArray(agentMsgState.segments)) {
          agentMsgState.segments = agentMsgState.segments.filter((part) => part && part.type !== "tool");
        }
      }
    }
    // Historical rows can receive corrected status/output. Include their
    // contents so an unchanged identity cannot leave stale details mounted.
    const seedSig = seed.map((item) => {
      if (!item || typeof item !== "object") return "";
      return JSON.stringify(item);
    }).join("\u001e");
    let article = agentBodyEl.querySelector("[data-agent-current]");
    const sameRun = article && article.dataset.messageId === String(id || "");
    if (!article || !sameRun) {
      disposeThinking(agentBodyEl);
      agentBodyEl.innerHTML = "";
      seed.forEach((item, index) => agentBodyEl.appendChild(agentSeedArticle(item, index)));
      article = renderAssistant(agentMsgState, live);
      article.dataset.messageId = id || "";
      article.dataset.agentCurrent = "true";
      agentBodyEl.appendChild(article);
      agentBodyEl._seedSig = seedSig;
    } else {
      if (agentBodyEl._seedSig !== seedSig) {
        const prior = agentBodyEl._seedSig ? agentBodyEl._seedSig.split("\u001e") : [];
        const grown = seedSig.startsWith(agentBodyEl._seedSig) && seed.length >= prior.length;
        if (!grown) {
          disposeThinking(agentBodyEl);
          [...agentBodyEl.children].forEach((node) => { if (node !== article) node.remove(); });
          seed.forEach((item, index) => agentBodyEl.insertBefore(agentSeedArticle(item, index), article));
        } else {
          seed.slice(prior.length).forEach((item, offset) => {
            agentBodyEl.insertBefore(agentSeedArticle(item, prior.length + offset), article);
          });
        }
        agentBodyEl._seedSig = seedSig;
      }
      updateAssistant(article, agentMsgState, live);
    }
    // A poll rebuilds every node, so carry live think previews over: pinned
    // ones stay glued to the newest line, scrolled-up ones keep their spot.
    article.querySelectorAll("[data-reason-preview]").forEach((el) => {
      const saved = savedPeeks.get(el.getAttribute("data-reason-preview"));
      if (saved && !saved.pinned) { stopThinkingFollow(el); el.scrollTop = saved.top; }
      else if (el._thinking?.follow) followThinking(el, !saved?.initialized);
    });
    restoreAgentScroll(wasAtBottom, savedTop);
    syncAgentLiveDock();
  }

  async function showAgent(id) {
    if (!id) return;
    const gen = ++agentsViewGen;
    agentLastEventAt = 0;
    agentViewId = id;
    sendSyncView(id);
    agentRun = null;
    agentMsgState = null;
    agentPendingEvents = [];
    agentViewFailCount = 0;
    agentStick = true;
    agentTailInitialized = false;
    if (agentFollowFrame) cancelAnimationFrame(agentFollowFrame);
    agentFollowFrame = 0;
    if (agentBodyEl) { disposeThinking(agentBodyEl); agentBodyEl.innerHTML = ""; }
    if (agentLogEl) agentLogEl.scrollTop = 0;
    if (agentMeterEl) agentMeterEl.hidden = true;
    if (agentTitleEl) agentTitleEl.textContent = "Subagent";
    if (agentStatusEl) {
      agentStatusEl.textContent = "loading";
      agentStatusEl.className = "badge live";
    }
    if (agentStopBtn) agentStopBtn.hidden = true;
    if (agentForceBtn) agentForceBtn.hidden = true;
    if (agentResumeBtn) agentResumeBtn.hidden = true;
    const errorBox = root.querySelector("[data-agent-error]");
    if (errorBox) errorBox.hidden = true;
    syncAgentLiveDock();
    setAgentsOpen(true);
    overlayPush("agent");
    agentBackBtn?.focus({ preventScroll: true });
    try {
      const run = await api("/chat/runs/" + encodeURIComponent(id));
      if (gen !== agentsViewGen) return;
      agentViewFailCount = 0;
      acceptAgentSnapshot(run);
      renderAgentView();
    } catch (error) {
      if (gen !== agentsViewGen) return;
      agentViewFailCount = 3;
      showFailure("Could not open subagent", error);
      markAgentViewUnavailable(error);
    }
  }

  function markAgentViewUnavailable(error) {
    const errorBox = root.querySelector("[data-agent-error]");
    if (agentStatusEl) { agentStatusEl.textContent = "Unavailable"; agentStatusEl.className = "badge fault"; }
    if (agentStopBtn) agentStopBtn.hidden = true;
    if (agentForceBtn) agentForceBtn.hidden = true;
    if (agentResumeBtn) agentResumeBtn.hidden = true;
    if (errorBox) {
      errorBox.hidden = false;
      errorBox.querySelector("[data-agent-error-text]").textContent = `Could not open subagent: ${error?.message || "Request failed."}`;
    }
    syncAgentLiveDock();
  }

  async function pollAgentView() {
    if (!agentViewId || !agentsOpen) return;
    const id = agentViewId;
    const gen = agentsViewGen;
    if (agentPollRequest?.id === id && agentPollRequest.gen === gen) { agentPollAgain = true; return; }
    const ticket = agentPollRequest = { id, gen };
    try {
      const run = await api("/chat/runs/" + encodeURIComponent(id));
      if (id !== agentViewId || gen !== agentsViewGen || !agentsOpen) return;
      agentViewFailCount = 0;
      const next = agentLive(run.status);
      if (!acceptAgentSnapshot(run)) return;
      const errorBox = root.querySelector("[data-agent-error]");
      if (errorBox) errorBox.hidden = true;
      renderAgentView();
      if (!next) refreshAgents();
    } catch (error) {
      if (id !== agentViewId || gen !== agentsViewGen || !agentsOpen) return;
      agentViewFailCount += 1;
      if (agentViewFailCount >= 3) markAgentViewUnavailable(error);
    } finally {
      if (agentPollRequest === ticket) {
        agentPollRequest = null;
        if (agentPollAgain) { agentPollAgain = false; if (id === agentViewId && gen === agentsViewGen && agentsOpen) pollAgentView(); }
      }
    }
  }

  function acceptAgentSnapshot(run) {
    if (!Number.isSafeInteger(run.event_cursor)) throw new Error("Subagent snapshot is missing its event cursor.");
    if (agentRun?.id === run.id && run.event_cursor < (agentRun.event_cursor || 0)) return false;
    agentRun = run;
    if (agentRun.message) agentRun.message._activity = run.activity || null;
    for (const event of agentPendingEvents) {
      if (event.payload?.run_id === run.id && event.id > agentRun.event_cursor) {
        applyProgressPayload(agentRun.message, event.payload, true);
        if (event.payload.status) agentRun.status = event.payload.status;
        else if (["agent.started", "agent.resumed"].includes(event.type)) agentRun.status = "queued";
        else if (event.type === "agent.progress" && agentRun.status === "queued") agentRun.status = "running";
        if (event.payload.cancel_requested != null) agentRun.cancel_requested = event.payload.cancel_requested;
        else if (event.type === "agent.resumed") agentRun.cancel_requested = false;
        agentRun.event_cursor = event.id;
      }
    }
    agentPendingEvents = [];
    reconcileBgTraces([...jobsRows, ...completedJobs.values()], false);
    return true;
  }

  async function refreshAgents() {
    if (!currentId) {
      agentsRows = [];
      jobsRows = [];
      agentsRequest = null;
      renderJobsList();
      syncAgentsUi();
      return;
    }
    const tid = currentId;
    const gen = generation;
    if (agentsRequest?.tid === tid && agentsRequest.gen === gen) { agentsRefreshAgain = true; return; }
    const ticket = agentsRequest = { tid, gen };
    const epoch = agentLifecycleEpoch;
    try {
      const data = await api("/chat/threads/" + encodeURIComponent(tid) + "/agents");
      if (tid !== currentId || gen !== generation) return;
      if (epoch !== agentLifecycleEpoch) { agentsRefreshAgain = true; return; }
      agentsFailCount = 0;
      agentsRows = Array.isArray(data.agents) ? data.agents : [];
      agentsRows.forEach(row => agentLifecycle.set(String(row.id), row));
      reconcileAgentTraces(agentsRows, true);
      syncAgentsUi();
    } catch {
      if (tid === currentId && gen === generation) agentsFailCount += 1;
    } finally {
      if (agentsRequest === ticket) {
        agentsRequest = null;
        if (agentsRefreshAgain) { agentsRefreshAgain = false; refreshAgents(); }
      }
    }
    refreshJobs();
  }

  let jobsRows = [];
  let jobsRendered = "";
  let jobsThread = "";
  let jobsRequest = null;
  let jobsRefreshAgain = false;

  if (jobsListEl) jobsListEl.addEventListener("click", async (event) => {
    const output = event.target.closest("[data-show-job]");
    if (output) {
      const id = output.dataset.showJob;
      const row = log.querySelector(`[data-execution="${CSS.escape(id)}"]`);
      if (row) {
        setAgentsOpen(false);
        pauseThreadFollow();
        revealToolRow(row);
        setTimeout(() => { if (row.isConnected) { row.scrollIntoView({ block: "center", behavior: reduceMotion.matches ? "auto" : "smooth" }); row.classList.add("is-work-highlight"); setTimeout(() => row.classList.remove("is-work-highlight"), 1800); } }, reduceMotion.matches ? 0 : 230);
      } else {
        const job = jobsRows.find(job => String(job.command_id || job.id) === id);
        const container = output.closest(".chat-jobs-row");
        if (job && container) {
          let details = container.querySelector(".chat-jobs-detail");
          if (!details) {
            details = document.createElement("div"); details.className = "chat-jobs-detail chat-step-reveal";
            details.innerHTML = `<div class="chat-step-reveal-inner">${toolDetailHtml({ ...job, name: job.name === "monitor" || job.kind === "monitor" ? "monitor" : "run_terminal_command", status: "running" }, false, true)}</div>`;
            container.appendChild(details);
          }
          setToolReveal(details, !details.classList.contains("is-open"));
        }
      }
      return;
    }
    const button = event.target.closest("[data-kill-job]");
    if (!button || !currentId) return;
    const tid = currentId;
    button.disabled = true;
    try {
      await api("/chat/threads/" + encodeURIComponent(tid) + "/jobs/" + encodeURIComponent(button.dataset.killJob) + "/kill", { method: "POST", body: "{}" });
      if (tid === currentId) await refreshJobs();
    } catch (error) {
      button.disabled = false;
      showFailure("Could not stop background command", error);
    }
  });

  function jobElapsedMs(job, nowMs) {
    const started = Number(job && job.started_ms);
    if (Number.isFinite(started) && started > 0) return Math.max(0, nowMs - started);
    return Math.max(0, Number(job && job.elapsed_ms) || 0);
  }

  function jobRowHtml(job, nowMs) {
    const secs = Math.round(jobElapsedMs(job, nowMs) / 1000);
    const state = String(job.state || "running");
    const cancelling = state === "cancelling";
    const live = ["running", "queued", "cancelling"].includes(state);
    const label = cancelling ? "Cancelling" : live ? "Background" : state.replace(/_/g, " ");
    return (
      `<div class="chat-jobs-row is-${escapeHtml(state)}${live ? " is-running" : ""}">` +
      '<span class="chat-agents-dot" aria-hidden="true"></span>' +
      '<span class="chat-jobs-copy">' +
      `<button type="button" class="chat-jobs-output-link" data-show-job="${escapeHtml(job.command_id || job.id)}" title="Show command and output"><span class="chat-jobs-cmd">${escapeHtml(toolText(displayCommand(job) || job.id))}</span></button>` +
      `<span class="chat-jobs-meta" title="${escapeHtml(job.command_id || job.id)}">${job.host ? escapeHtml(job.host) + " · " : ""}${job.kind === "monitor" || job.name === "monitor" ? "Watch · " : ""}${escapeHtml(label)}${Number.isFinite(Number(job.event_count)) ? " · " + Number(job.event_count) + " events" : ""} · View output ↗</span>` +
      `<span class="chat-jobs-time" data-job-time="${escapeHtml(job.id)}">${formatActivityClock(secs)}</span>` +
      "</span>" +
      `<button type="button" class="chat-jobs-kill" data-kill-job="${escapeHtml(job.command_id || job.id)}" title="Stop this background command">${cancelling ? "Cancelling" : "Stop"}</button>` +
      "</div>"
    );
  }

  function renderJobsList() {
    if (!jobsListEl) return;
    const nowMs = Date.now();
    const sig = JSON.stringify(jobsRows.map((job) => [job.id, job.command, job.host, job.state, job.event_count, job.kind]));
    if (sig !== jobsRendered) {
      jobsRendered = sig;
      jobsListEl.hidden = !jobsRows.length;
      jobsListEl.innerHTML = jobsRows.length
        ? '<p class="chat-agents-kicker">Background work · ' + jobsRows.length + "</p>" +
          jobsRows.map((job) => jobRowHtml(job, nowMs)).join("")
        : "";
      return;
    }
    tickJobTimes();
  }

  function tickJobTimes() {
    if (!jobsListEl || jobsListEl.hidden || !jobsRows.length) return;
    const nowMs = Date.now();
    jobsRows.forEach((job) => {
      const el = jobsListEl.querySelector('[data-job-time="' + job.id + '"]');
      if (el) el.textContent = formatActivityClock(Math.round(jobElapsedMs(job, nowMs) / 1000));
    });
  }

  async function refreshJobs() {
    if (!currentId) {
      jobsRows = [];
      renderJobsList();
      return;
    }
    const tid = currentId;
    const gen = generation;
    if (jobsRequest?.tid === tid && jobsRequest.gen === gen) { jobsRefreshAgain = true; return; }
    if (jobsThread !== tid) {
      jobsThread = tid;
      completedJobs.clear();
      jobsRows = [];
      renderJobsList();
    }
    const ticket = jobsRequest = { tid, gen };
    try {
      const data = await api("/chat/threads/" + encodeURIComponent(tid) + "/jobs");
      if (tid !== currentId || gen !== generation) return;
      jobsRows = Array.isArray(data.jobs) ? data.jobs : [];
      renderJobsList();
      syncAgentsUi();
      (Array.isArray(data.recent) ? data.recent : []).forEach((job) => completedJobs.set(String(job.id), job));
      reconcileBgTraces([...jobsRows, ...completedJobs.values()]);
    } catch {} finally {
      if (jobsRequest === ticket) {
        jobsRequest = null;
        if (jobsRefreshAgain) { jobsRefreshAgain = false; refreshJobs(); }
      }
    }
  }

  function hasRunningBgTraces() {
    return messages.some(
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
    const targets = [...messages, ...(agentRun?.messages || []), ...(agentRun?.message ? [agentRun.message] : [])];
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
      if (agentsOpen && agentRun) scheduleAgentView();
      messages.forEach((msg, index) => {
        const article = log.children[index];
        if (article && msg.role === "assistant") {
          updateAssistant(article, msg, Boolean(streaming && activeRun && msg.id === activeRun.message_id));
        }
      });
    }
  }

  setInterval(() => {
    if (currentId && (agentsOpen || hasRunningBgTraces())) refreshJobs();
  }, 3000);
  // agent.tick events drive the closed-panel list now; the poll is only a
  // fallback for missed started/ended events (socket resync, older server).
  setInterval(() => {
    if (currentId && !agentsOpen && (agentsRows.some((row) => agentLive(row.status)) || messages.some((msg) =>
      (msg.tools || []).some((trace) => ["spawn_subagent", "get_subagent_output"].includes(trace.name) && trace.child_id && toolRunning(trace, false))))) refreshAgents();
  }, 10000);

  // Transport heartbeats alone do not prove that this particular run is alive.
  // A quiet current run gets an authoritative snapshot; the server's existing
  // orphan recovery handles absent workers. Never retry a model call here.
  setInterval(async () => {
    if (!currentId || !runIsActive(activeRun) || document.hidden || !isLive() || runReconcilePending || Date.now() - lastRunReceiptAt < 15000) return;
    runReconcilePending = true;
    try { await reconcileThread(); } catch (_) {} finally { runReconcilePending = false; }
  }, 5000);

  function syncAgentsUi() {
    const { live } = agentRowsSorted();
    const liveCount = live.length;
    const commandCount = jobsRows.length;
    const workCount = liveCount + commandCount;
    const workLabel = [liveCount ? `${liveCount} agent${liveCount === 1 ? "" : "s"}` : "", commandCount ? `${commandCount} command${commandCount === 1 ? "" : "s"}` : ""].filter(Boolean).join(" · ");
    if (agentsPanel) agentsPanel.hidden = !agentsOpen;
    if (agentsBackdrop) agentsBackdrop.hidden = !(agentsOpen && window.matchMedia("(max-width: 800px)").matches);
    root.classList.toggle("is-agents", agentsOpen);
    if (agentsLiveEl) {
      agentsLiveEl.hidden = !liveCount;
      agentsLiveEl.textContent = liveCount ? `${liveCount} live` : "";
    }
    if (agentsListEl) agentsListEl.hidden = Boolean(agentViewId);
    if (agentViewEl) agentViewEl.hidden = !agentViewId;
    for (const tab of agentsTabs) {
      tab.setAttribute("aria-expanded", agentsOpen ? "true" : "false");
      tab.classList.toggle("is-live", workCount > 0);
      tab.title = workLabel || "Agents and background commands";
      const countEl = tab.querySelector("[data-agents-count]");
      if (countEl) {
        const next = workLabel;
        if (next && countEl.textContent !== next) {
          countEl.classList.remove("is-bump");
          void countEl.offsetWidth;
          countEl.classList.add("is-bump");
        }
        countEl.textContent = next;
        countEl.classList.toggle("has-count", Boolean(next));
      }
    }
    renderAgentsList();
    renderAgentHead();
  }

  function agentsTick() {
    if (!agentsOpen) return;
    renderAgentsList();
    tickJobTimes();
    syncAgentLiveDock();
    agentsPollTick += 1;
    if (agentsPollTick < 2) return;
    agentsPollTick = 0;
    const wsHealthy = isLive() && Date.now() - agentLastEventAt < 3000;
    if (agentViewId && (!agentRun || agentLive(agentRun.status) || agentViewFailCount) && !wsHealthy) pollAgentView();
    refreshAgents();
  }

  function setAgentsOpen(open) {
    const next = Boolean(open);
    if (next === agentsOpen) {
      if (next) syncAgentsUi();
      return;
    }
    agentsOpen = next;
    if (agentsOpen) {
      if (artifactOpen) setArtifactOpen(false);
      if (memoryOpen) setMemoryOpen(false);
      if (skillsOpen) setSkillsOpen(false);
      setPermsOpen(false);
      if (overlayIsMobile()) setHistoryOpen(false);
      else if (window.matchMedia("(max-width: 1100px)").matches) {
        root.classList.add("history-collapsed");
        if (typeof syncHistory === "function") syncHistory();
      }
      closePicker();
      overlayPush("agents");
      if (!agentsTimer) agentsTimer = setInterval(agentsTick, 1000);
      refreshAgents();
      if (agentViewId && agentRun) pollAgentView();
    } else {
      agentsViewGen += 1;
      if (!agentRun) { agentViewId = ""; agentMsgState = null; }
      armOverlayGhost(root.querySelector("[data-agents-close]"));
      overlayDrop("agent");
      overlayDrop("agents");
      if (agentsTimer) {
        clearInterval(agentsTimer);
        agentsTimer = 0;
      }
    }
    syncAgentsUi();
    scheduleFitChatBar();
  }

  function resetAgents() {
    agentsViewGen += 1;
    agentLifecycle.clear();
    agentLifecycleEpoch += 1;
    agentsRefreshAgain = false;
    agentsRows = [];
    jobsRows = [];
    completedJobs.clear();
    renderJobsList();
    agentViewId = "";
    agentRun = null;
    agentMsgState = null;
    agentViewFailCount = 0;
    agentsFailCount = 0;
    syncAgentsUi();
    refreshAgents();
  }

  for (const tab of agentsTabs) tab.addEventListener("click", () => setAgentsOpen(!agentsOpen));
  if (agentsCloseBtn) agentsCloseBtn.addEventListener("click", () => setAgentsOpen(false));
  if (agentsBackdrop) agentsBackdrop.addEventListener("click", () => setAgentsOpen(false));
  if (agentLogEl)
    agentLogEl.addEventListener("scroll", () => {
      if (agentIgnoreScroll) return;
      const top = agentLogEl.scrollTop;
      agentStick = agentNearBottom() && (agentStick || agentResumeOnScroll && top > agentLastScrollTop);
      agentLastScrollTop = top;
    });
  if (agentLogEl) {
    const pause = () => {
      agentStick = false; agentIgnoreScroll = false; agentResumeOnScroll = false;
      if (agentFollowFrame) cancelAnimationFrame(agentFollowFrame);
      agentFollowFrame = 0;
    };
    agentLogEl.addEventListener("wheel", event => { if (event.deltaY < 0) pause(); else if (event.deltaY > 0) agentResumeOnScroll = true; }, { passive: true });
    let y = null;
    agentLogEl.addEventListener("touchstart", event => { y = event.touches[0]?.clientY ?? null; }, { passive: true });
    agentLogEl.addEventListener("touchmove", event => {
      const next = event.touches[0]?.clientY;
      if (next != null && y != null && next > y) pause();
      else if (next != null && y != null && next < y) agentResumeOnScroll = true;
      y = next ?? null;
    }, { passive: true });
    agentLogEl.addEventListener("keydown", event => { if (["ArrowUp", "PageUp", "Home"].includes(event.key)) pause(); else if (["ArrowDown", "PageDown", "End"].includes(event.key)) agentResumeOnScroll = true; });
  }
  if (agentBodyEl)
    agentBodyEl.addEventListener("click", (event) => {
      const openAgentEl = event.target.closest("[data-open-agent]");
      if (!openAgentEl) return;
      event.preventDefault();
      event.stopPropagation();
      showAgent(openAgentEl.getAttribute("data-open-agent"));
    });
  function closeAgentView() {
    sendSyncView("");
    if (!agentViewId) return;
    agentsViewGen += 1;
    agentViewId = "";
    agentRun = null;
    agentMsgState = null;
    agentViewFailCount = 0;
    syncAgentLiveDock();
    armOverlayGhost(agentBackBtn);
    overlayDrop("agent");
    syncAgentsUi();
  }
  if (agentBackBtn)
    agentBackBtn.addEventListener("click", () => closeAgentView());
  root.querySelector("[data-agent-retry]")?.addEventListener("click", () => { if (agentViewId) showAgent(agentViewId); });
  if (agentStopBtn)
    agentStopBtn.addEventListener("click", async () => {
      if (!agentViewId || !agentRun) return;
      if (!(await askConfirm("Stop this subagent? Its children stop too; the main agent keeps running.", { confirmLabel: "Stop", danger: true }))) return;
      try {
        await api("/chat/runs/" + encodeURIComponent(agentViewId) + "/cancel?scope=self", { method: "POST", body: "{}" });
        setHint("Stopped subagent.");
        await pollAgentView();
        refreshAgents();
      } catch (error) {
        showFailure("Could not stop subagent", error);
      }
    });
  if (agentForceBtn)
    agentForceBtn.addEventListener("click", async () => {
      if (!agentViewId || !agentRun) return;
      if (!(await askConfirm("Force stop this subagent now? It is wedged in stopping; force-finalize its state.", { confirmLabel: "Force stop", danger: true }))) return;
      try {
        await api("/chat/runs/" + encodeURIComponent(agentViewId) + "/cancel?scope=self&force=1", { method: "POST", body: "{}" });
        setHint("Force-stopped subagent.");
        await pollAgentView();
        refreshAgents();
      } catch (error) {
        showFailure("Could not force-stop subagent", error);
      }
    });
  if (agentResumeBtn)
    agentResumeBtn.addEventListener("click", async () => {
      if (!agentViewId || !agentRun) return;
      if (!(await askConfirm("Resume this subagent from where it stopped? Its report goes to the main agent when it finishes.", { confirmLabel: "Resume" }))) return;
      try {
        const data = await api("/chat/runs/" + encodeURIComponent(agentViewId) + "/resume", { method: "POST", body: "{}" });
        if (data && data.run) {
          acceptAgentSnapshot(await api("/chat/runs/" + encodeURIComponent(agentViewId)));
          renderAgentView();
        }
        setHint("Resumed subagent.");
        refreshAgents();
      } catch (error) {
        showFailure("Could not resume subagent", error);
      }
    });
  if (agentsPanel)
    agentsPanel.addEventListener("click", (event) => {
      const row = event.target.closest("[data-agent-id]");
      if (row) showAgent(row.getAttribute("data-agent-id"));
    });
  syncAgentsUi();

  function syncSkillsUi() {
    if (skillsPanel) skillsPanel.hidden = !skillsOpen;
    if (skillsBackdrop) skillsBackdrop.hidden = !skillsOpen;
    root.classList.toggle("is-skills", skillsOpen);
    if (skillsToggle) {
      skillsToggle.setAttribute("aria-pressed", skillsOpen ? "true" : "false");
      skillsToggle.setAttribute("aria-expanded", skillsOpen ? "true" : "false");
      skillsToggle.classList.toggle("is-on", skillsOpen);
    }
  }

  function setSkillsOpen(open) {
    const next = Boolean(open);
    if (next === skillsOpen) {
      if (next) syncSkillsUi();
      return;
    }
    skillsOpen = next;
    if (skillsOpen) {
      if (memoryOpen) setMemoryOpen(false);
      if (artifactOpen) setArtifactOpen(false);
      if (agentsOpen) setAgentsOpen(false);
      setPermsOpen(false);
      if (overlayIsMobile()) setHistoryOpen(false);
      closePicker();
      overlayPush("skills");
      if (document.activeElement === input) input.blur();
    } else {
      armOverlayGhost(skillsClose);
      overlayDrop("skills");
    }
    syncSkillsUi();
  }

  function applyChatView(next) {
    const previous = chatView;
    chatView = CHAT_VIEWS.includes(next) ? next : "cli";
    root.setAttribute("data-view", chatView);
    if (viewLabel) viewLabel.textContent = VIEW_LABELS[chatView] || "View";
    if (viewMenu) {
      viewMenu.querySelectorAll("[data-view]").forEach((btn) => {
        btn.classList.toggle("is-on", btn.getAttribute("data-view") === chatView);
      });
    }
    if (viewToggle) viewToggle.classList.toggle("is-on", chatView !== "cli");
    document.documentElement.dataset.chatTerminal = String(chatView === "cli");
    if (chatView === "console") document.documentElement.dataset.chatConsole = "true";
    else document.documentElement.removeAttribute("data-chat-console");
    try { localStorage.setItem(STORE_VIEW, chatView); } catch (_) {}
    if (previous !== chatView && messages.length) paint();
    if (previous !== chatView && agentViewId && agentRun) renderAgentView();
  }

  let viewMenuState = false;
  function closeViewMenuDom() {
    const wasOpen = viewMenu && !viewMenu.hidden;
    viewMenu.hidden = true;
    if (viewBackdrop) viewBackdrop.hidden = true;
    viewToggle.setAttribute("aria-expanded", "false");
    root.classList.remove("view-sheet-open");
    viewMenu.classList.remove("is-dragging");
    viewMenu.style.transform = "";
    viewMenuState = false;
    if (wasOpen) armOverlayGhost(viewMenu.querySelector("[data-view-close]"));
  }
  function setViewMenuOpen(open) {
    if (!viewMenu || !viewToggle) return;
    const mobile = overlayIsMobile();
    const wasOpen = !viewMenu.hidden;
    if (!wasOpen && !open) return;
    viewMenu.hidden = !open;
    if (viewBackdrop) viewBackdrop.hidden = !open;
    viewToggle.setAttribute("aria-expanded", open ? "true" : "false");
    root.classList.toggle("view-sheet-open", open);
    if (open) {
      overlayPush("view");
      viewMenuState = mobile;
      if (!mobile) {
        (viewMenu.querySelector("button.is-on") || viewMenu.querySelector("[data-view]") || viewMenu).focus();
      }
      window.FuseChatAppearance?.syncUi();
      return;
    }
    armOverlayGhost(viewMenu.querySelector("[data-view-close]"));
    overlayDrop("view");
    viewMenuState = false;
    viewMenu.classList.remove("is-dragging");
    viewMenu.style.transform = "";
    if (!mobile) viewToggle.focus();
  }

  function startArtifactBuild(title) {
    currentArtifact = { title: title || "Artifact", building: true, identifier: "", visibility: "private" };
    artifactMode = "preview";
    artifactSourceLoaded = "";
    if (artifactFrame) artifactFrame.removeAttribute("src");
    if (artifactSource) artifactSource.textContent = "";
    setArtifactOpen(true);
    syncArtifactUi();
  }

  async function showArtifact(identifier, seed) {
    if (!identifier) return;
    const gen = generation;
    const prior = currentArtifact && currentArtifact.identifier === identifier ? currentArtifact : {};
    currentArtifact = {
      identifier,
      title: (seed && seed.title) || prior.title || "Artifact",
      visibility: (seed && seed.visibility) || prior.visibility || "",
      version: seed && seed.version != null ? seed.version : prior.version,
      versions: prior.versions || [],
      url: "/a/" + identifier,
      building: false,
    };
    artifactMode = "preview";
    artifactSourceLoaded = "";
    if (artifactSource) artifactSource.textContent = "";
    setArtifactOpen(true);
    const version = currentArtifact.version;
    if (artifactFrame) artifactFrame.src = artifactContentUrl(identifier, version);
    syncArtifactUi();
    try {
      const meta = await api("/a/" + encodeURIComponent(identifier) + "/meta");
      if (gen !== generation || !currentArtifact || currentArtifact.identifier !== identifier) return;
      currentArtifact.title = meta.title || currentArtifact.title;
      currentArtifact.visibility = meta.visibility || currentArtifact.visibility;
      currentArtifact.version = meta.version;
      currentArtifact.versions = Array.isArray(meta.versions) ? meta.versions : [];
      currentArtifact.url = meta.url || currentArtifact.url;
      if (artifactFrame && artifactFrame.getAttribute("src") !== artifactContentUrl(identifier, currentArtifact.version)) {
        artifactFrame.src = artifactContentUrl(identifier, currentArtifact.version);
      }
      syncArtifactUi();
    } catch (_) {}
  }

  async function loadArtifactSource() {
    if (!currentArtifact || !currentArtifact.identifier) return;
    const gen = generation;
    const key = currentArtifact.identifier + ":" + (currentArtifact.version || "");
    if (artifactSourceLoaded === key) return;
    const res = await fetch(artifactContentUrl(currentArtifact.identifier, currentArtifact.version), { credentials: "same-origin" });
    const text = await res.text();
    if (gen !== generation || !currentArtifact || key !== currentArtifact.identifier + ":" + (currentArtifact.version || "")) return;
    if (!res.ok) throw new Error("Could not load source");
    artifactSourceLoaded = key;
    if (artifactSource) artifactSource.textContent = text;
  }

  function setMemoryOpen(open) {
    const next = Boolean(open);
    if (next === memoryOpen) {
      if (next) syncMemoryUi();
      return;
    }
    memoryOpen = next;
    if (open) {
      if (overlayIsMobile()) setHistoryOpen(false);
      if (artifactOpen) setArtifactOpen(false);
      if (agentsOpen) setAgentsOpen(false);
      if (skillsOpen) setSkillsOpen(false);
      closePicker();
      overlayPush("memory");
      if (document.activeElement === input) input.blur();
      refreshProjectAgents();
    } else {
      armOverlayGhost(memoryClose);
      overlayDrop("memory");
    }
    syncMemoryUi();
    if (open) {
      const panel = root.querySelector('[data-context-panel="' + contextTab + '"]');
      const area = (panel && panel.querySelector("textarea")) || memoryInstructions;
      if (area) area.focus({ preventScroll: true });
    } else {
      pinVisualViewport();
    }
  }
  if (memoryToggle) {
    memoryToggle.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
      setMemoryOpen(!memoryOpen);
    });
  }
  if (memoryClose) {
    memoryClose.addEventListener("click", (event) => {
      event.preventDefault();
      setMemoryOpen(false);
    });
  }
  if (memoryBackdrop) {
    memoryBackdrop.addEventListener("click", () => setMemoryOpen(false));
  }
  if (skillsToggle) {
    skillsToggle.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
      setSkillsOpen(!skillsOpen);
    });
  }
  if (skillsClose) {
    skillsClose.addEventListener("click", (event) => {
      event.preventDefault();
      setSkillsOpen(false);
    });
  }
  if (skillsBackdrop) {
    skillsBackdrop.addEventListener("click", () => setSkillsOpen(false));
  }
  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    if (pickerOpen) {
      event.preventDefault();
      closePicker();
    } else if (skillsOpen) {
      event.preventDefault();
      setSkillsOpen(false);
    } else if (memoryOpen) {
      event.preventDefault();
      setMemoryOpen(false);
    } else if (permsOpen) {
      event.preventDefault();
      setPermsOpen(false);
    } else if (viewMenu && !viewMenu.hidden) {
      event.preventDefault();
      setViewMenuOpen(false);
      viewToggle?.focus();
    }
  });
  if (viewToggle && viewMenu) {
    const viewSheetClose = viewMenu.querySelector("[data-view-close]");
    viewSheetClose?.addEventListener("click", (event) => {
      event.preventDefault();
      setViewMenuOpen(false);
    });
    viewMenu.addEventListener("keydown", (event) => {
      if (event.key === "Escape") {
        event.preventDefault();
        setViewMenuOpen(false);
        viewToggle.focus();
      }
    });
    viewToggle.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
      setViewMenuOpen(viewMenu.hidden);
    });
    viewMenu.querySelectorAll("[data-view]").forEach((btn) => {
      btn.addEventListener("click", (event) => {
        event.preventDefault();
        applyChatView(btn.getAttribute("data-view"));
        if (!pickerSheetQuery.matches) viewToggle.focus();
      });
    });
    viewBackdrop?.addEventListener("click", () => setViewMenuOpen(false));
  }
  applyChatView(chatView);
  if (artifactToggle) {
    artifactToggle.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
      const last = latestArtifact();
      if (!last && !(currentArtifact && currentArtifact.building)) return;
      if (artifactOpen) setArtifactOpen(false);
      else if (last) showArtifact(last.identifier, last).catch((error) => showFailure("Could not open artifact", error));
    });
  }
  if (artifactClose) {
    artifactClose.addEventListener("click", (event) => {
      event.preventDefault();
      setArtifactOpen(false);
    });
  }
  if (artifactBackdrop) {
    artifactBackdrop.addEventListener("click", () => setArtifactOpen(false));
  }
  artifactModeBtns.forEach((btn) => {
    btn.addEventListener("click", (event) => {
      event.preventDefault();
      artifactMode = btn.getAttribute("data-artifact-mode") || "preview";
      syncArtifactUi();
      if (artifactMode === "source") {
        loadArtifactSource().catch((error) => showFailure("Could not load source", error));
      }
    });
  });
  if (artifactVersionEl) {
    artifactVersionEl.addEventListener("change", () => {
      if (!currentArtifact || !currentArtifact.identifier) return;
      currentArtifact.version = Number(artifactVersionEl.value) || currentArtifact.version;
      artifactSourceLoaded = "";
      if (artifactFrame) artifactFrame.src = artifactContentUrl(currentArtifact.identifier, currentArtifact.version);
      if (artifactMode === "source") loadArtifactSource().catch((error) => showFailure("Could not load source", error));
      syncArtifactUi();
    });
  }
  [memoryInstructions, memoryProfile, memoryThread].forEach((area) => {
    if (!area) return;
    area.addEventListener("input", () => {
      if (area === memoryInstructions) instructions = area.value;
      else if (area === memoryProfile) profile = area.value;
      else threadContext = area.value;
      syncMemoryUi();
      if (!isLive()) requireLive();
      if (area === memoryThread) persist();
      else queueMemorySave();
    });
  });
  newBtns.forEach((btn) =>
    btn.addEventListener("click", () => {
      if (historyTab === "projects") {
        openProjectPicker().catch((error) => showFailure("Could not start a project", error));
        return;
      }
      resetChat().catch((error) => showFailure("Could not start a new chat", error));
    })
  );
  if (projectPicker) {
    projectPicker.addEventListener("click", (event) => {
      const overview = event.target.closest("[data-project-overview-open], [data-project-overview-start]");
      if (overview) {
        const start = overview.hasAttribute("data-project-overview-start");
        const [hostId, projectId] = (start ? overview.dataset.projectOverviewStart : overview.dataset.projectOverviewOpen).split(":");
        if (hostId && projectId && requireLive()) {
          setProjectPickerOpen(false);
          startProjectThread(hostId, projectId).catch((error) => showFailure("Could not start a project session", error));
        }
        return;
      }
      const pick = event.target.closest("[data-pick-project]");
      if (pick) {
        const [hostId, projectId] = String(pick.getAttribute("data-pick-project") || "").split(":");
        if (hostId && projectId) {
          setProjectPickerOpen(false);
          startProjectThread(hostId, projectId);
        }
        return;
      }
      if (event.target.closest("[data-project-picker-add]")) {
        if (!pickerHosts.length) return;
        showPickerForm();
        return;
      }
      if (event.target.closest("[data-project-picker-close]")) {
        setProjectPickerOpen(false);
        return;
      }
      if (event.target.closest("[data-project-picker-back]")) {
        showPickerList();
        return;
      }
      if (event.target.closest("[data-project-picker-browse]")) {
        const path = (projectPickerPath && projectPickerPath.value) || "";
        loadPickerBrowse(path).catch((error) => showFailure("Could not browse that host", error));
        return;
      }
      const into = event.target.closest("[data-picker-into]");
      if (into && pickerBrowse) {
        const name = into.getAttribute("data-picker-into") || "";
        const base = String(pickerBrowse.path || "").replace(/\/+$/, "");
        const next = !base || base === "/" ? "/" + name : base + "/" + name;
        loadPickerBrowse(next).catch((error) => showFailure("Could not browse that folder", error));
        return;
      }
      if (event.target.closest("[data-picker-up]") && pickerBrowse) {
        loadPickerBrowse(pickerBrowse.parent || "/").catch((error) => showFailure("Could not browse that folder", error));
        return;
      }
      if (event.target.closest("[data-picker-use]") && pickerBrowse) {
        if (projectPickerPath) projectPickerPath.value = pickerBrowse.path || "";
        const base = String(pickerBrowse.path || "").replace(/\/+$/, "").split("/").pop();
        if (projectPickerName && !projectPickerName.value && base && base !== "/") projectPickerName.value = base;
      }
    });
  }
  if (projectPickerForm) {
    projectPickerForm.addEventListener("submit", (event) => {
      createPickerProject(event).catch((error) => showFailure("Could not create the project", error));
    });
  }
  if (projectPickerHost) {
    projectPickerHost.addEventListener("change", () => {
      const host = pickerHostById(projectPickerHost.value);
      if (projectPickerPath) projectPickerPath.value = (host && host.default_cwd) || "";
      pickerBrowse = null;
      if (projectPickerBrowser) {
        projectPickerBrowser.hidden = true;
        projectPickerBrowser.innerHTML = "";
      }
    });
  }
  if (rewindClose) rewindClose.addEventListener("click", closeRewind);
  const historyPanel = root.querySelector("[data-history]");
  const historyCollapse = historyPanel?.querySelector("[data-history-toggle]");
  const historyBackdrop = root.querySelector("[data-history-backdrop]");
  const historyMobile = window.matchMedia("(max-width: 800px)");
  const historyBackground = root.querySelectorAll(".chat-thread, .chat-dock, .chat-latest, .chat-bar > :not([data-history-toggle])");
  let historyWasOpen = false;

  function syncHistory() {
    const drawer = historyMobile.matches && root.classList.contains("is-history");
    const expanded = historyMobile.matches ? drawer : !root.classList.contains("history-collapsed");
    const focusInHistory = historyPanel?.contains(document.activeElement);
    historyToggles.forEach((btn) => btn.setAttribute("aria-expanded", String(expanded)));
    if (historyBackdrop) historyBackdrop.hidden = !drawer;
    historyBackground.forEach((element) => { element.inert = drawer; });
    if (historyPanel) historyPanel.inert = !expanded;
    if (historyWasOpen && !drawer) {
      closePop();
      if (focusInHistory && !document.body.classList.contains("is-nav")) {
        historyToggle?.focus({ preventScroll: true });
      }
    }
    historyWasOpen = drawer;
    scheduleFitChatBar();
  }

  function setHistoryOpen(open) {
    const next = Boolean(open);
    const was = root.classList.contains("is-history");
    root.classList.toggle("is-history", next);
    if (next && !was) {
      document.dispatchEvent(new Event("fuse:history-open"));
      closePop();
      if (memoryOpen) setMemoryOpen(false);
      if (skillsOpen && overlayIsMobile()) setSkillsOpen(false);
      if (artifactOpen && overlayIsMobile()) setArtifactOpen(false);
      if (agentsOpen && overlayIsMobile()) setAgentsOpen(false);
      if (overlayIsMobile()) overlayPush("history");
    } else if (!next && was) {
      armOverlayGhost(historyCollapse);
      overlayDrop("history");
    }
    syncHistory();
    if (next && overlayIsMobile()) historyCollapse?.focus({ preventScroll: true });
  }

  historyBackdrop?.addEventListener("click", () => setHistoryOpen(false));
  // Back swipe: inside a chat it reveals the chat list (like the Chats toggle);
  // from the list itself the browser leaves the page, as usual.
  window.addEventListener("popstate", (event) => {
    if (overlayPopLock) {
      overlayPopLock = false;
      return;
    }
    if (overlayStack.length) {
      const top = overlayStack[overlayStack.length - 1];
      // Landed back ON this overlay's own history entry (something above it
      // was popped, e.g. the account page): keep it open.
      if (event.state && event.state.fuseOverlay === top) return;
      const kind = overlayStack.pop();
      overlayCloseKind(kind, true);
      return;
    }
    if (!overlayIsMobile()) return;
    if (event.state && event.state.fuseChat) {
      if (root.classList.contains("is-history")) setHistoryOpen(false);
      return;
    }
    if (currentId && !root.classList.contains("is-history")) setHistoryOpen(true);
  });
  // app.js closes history directly when opening global navigation.
  new MutationObserver(syncHistory).observe(root, { attributes: true, attributeFilter: ["class"] });
  historyMobile.addEventListener("change", () => {
    setHistoryOpen(false);
    pinVisualViewport();
  });
  pickerSheetQuery.addEventListener("change", () => {
    renderPickerButton();
    if (viewMenu && !viewMenu.hidden) {
      viewMenu.style.transform = "";
      viewMenu.classList.remove("is-dragging");
      root.classList.toggle("view-sheet-open", overlayIsMobile());
    }
  });
  syncHistory();
  let terminalHistoryState = null;
  function syncTerminalHistory() {
    if (isHarnessView() && terminalHistoryState === null) {
      terminalHistoryState = {
        collapsed: root.classList.contains("history-collapsed"),
        drawer: root.classList.contains("is-history"),
      };
      root.classList.add("history-collapsed");
      setHistoryOpen(false);
    } else if (!isHarnessView() && terminalHistoryState !== null) {
      root.classList.toggle("history-collapsed", terminalHistoryState.collapsed);
      setHistoryOpen(historyMobile.matches && terminalHistoryState.drawer);
      terminalHistoryState = null;
    }
  }
  new MutationObserver(syncTerminalHistory).observe(root, { attributes: true, attributeFilter: ["data-view"] });
  syncTerminalHistory();
  historyToggles.forEach((btn) => {
    btn.addEventListener("click", (event) => {
      event.stopPropagation();
      if (historyMobile.matches) setHistoryOpen(!root.classList.contains("is-history"));
      else {
        root.classList.toggle("history-collapsed");
        syncHistory();
      }
    });
  });
  const resizeHandle = root.querySelector("[data-history-resize]");
  function clampHistoryWidth(px) {
    const deskMax = Math.min(HISTORY_WIDTH.max, Math.max(HISTORY_WIDTH.min, window.innerWidth - 340));
    return Math.round(Math.min(deskMax, Math.max(HISTORY_WIDTH.min, px)));
  }
  function applyHistoryWidth(px, persist) {
    root.style.setProperty("--chat-history", px + "px");
    if (persist) {
      try { localStorage.setItem(STORE_HISTORY_WIDTH, String(px)); } catch (_) {}
    }
  }
  function currentHistoryWidth() {
    const raw = Number.parseFloat(root.style.getPropertyValue("--chat-history"));
    return Number.isFinite(raw) ? raw : HISTORY_WIDTH.default;
  }
  if (resizeHandle) {
    let savedWidth = 0;
    try { savedWidth = Number(localStorage.getItem(STORE_HISTORY_WIDTH)) || 0; } catch (_) {}
    if (savedWidth >= HISTORY_WIDTH.min && savedWidth <= HISTORY_WIDTH.max) {
      applyHistoryWidth(savedWidth, false);
    }
    let resizeRaf = 0;
    resizeHandle.addEventListener("pointerdown", (event) => {
      if (window.innerWidth <= 800) return;
      event.preventDefault();
      try { resizeHandle.setPointerCapture(event.pointerId); } catch (_) {}
      resizeHandle.classList.add("is-dragging");
      document.body.classList.add("is-resizing-history");
      const onMove = (moveEvent) => {
        const rect = root.getBoundingClientRect();
        cancelAnimationFrame(resizeRaf);
        resizeRaf = requestAnimationFrame(() => {
          applyHistoryWidth(clampHistoryWidth(moveEvent.clientX - rect.left), false);
        });
      };
      const onUp = () => {
        resizeHandle.classList.remove("is-dragging");
        document.body.classList.remove("is-resizing-history");
        resizeHandle.removeEventListener("pointermove", onMove);
        resizeHandle.removeEventListener("pointerup", onUp);
        resizeHandle.removeEventListener("pointercancel", onUp);
        applyHistoryWidth(clampHistoryWidth(currentHistoryWidth()), true);
      };
      resizeHandle.addEventListener("pointermove", onMove);
      resizeHandle.addEventListener("pointerup", onUp);
      resizeHandle.addEventListener("pointercancel", onUp);
    });
    resizeHandle.addEventListener("keydown", (event) => {
      if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
      event.preventDefault();
      const step = event.shiftKey ? 32 : 16;
      applyHistoryWidth(clampHistoryWidth(currentHistoryWidth() + (event.key === "ArrowRight" ? step : -step)), true);
    });
    resizeHandle.addEventListener("dblclick", () => applyHistoryWidth(HISTORY_WIDTH.default, true));
  }
  const agentsResizeHandle = root.querySelector("[data-agents-resize]");
  const STORE_AGENTS_WIDTH = "fuse.chat.agentsWidth";
  const AGENTS_WIDTH = { min: 16 * 16, max: 56 * 16, defaultPx: 480 };
  function clampAgentsWidth(px) {
    const max = Math.min(AGENTS_WIDTH.max, Math.max(AGENTS_WIDTH.min, window.innerWidth - 340));
    return Math.round(Math.min(max, Math.max(AGENTS_WIDTH.min, px)));
  }
  function applyAgentsWidth(px, persist) {
    root.style.setProperty("--chat-agents", px + "px");
    if (persist) {
      try { localStorage.setItem(STORE_AGENTS_WIDTH, String(px)); } catch (_) {}
    }
  }
  function currentAgentsWidth() {
    const raw = Number.parseFloat(root.style.getPropertyValue("--chat-agents"));
    return Number.isFinite(raw) ? raw : AGENTS_WIDTH.defaultPx;
  }
  if (agentsResizeHandle) {
    let savedAgentsWidth = 0;
    try { savedAgentsWidth = Number(localStorage.getItem(STORE_AGENTS_WIDTH)) || 0; } catch (_) {}
    if (savedAgentsWidth >= AGENTS_WIDTH.min && savedAgentsWidth <= AGENTS_WIDTH.max) {
      applyAgentsWidth(savedAgentsWidth, false);
    }
    let agentsResizeRaf = 0;
    agentsResizeHandle.addEventListener("pointerdown", (event) => {
      if (window.innerWidth <= 800) return;
      event.preventDefault();
      try { agentsResizeHandle.setPointerCapture(event.pointerId); } catch (_) {}
      agentsResizeHandle.classList.add("is-dragging");
      document.body.classList.add("is-resizing-agents");
      const onMove = (moveEvent) => {
        const rect = root.getBoundingClientRect();
        cancelAnimationFrame(agentsResizeRaf);
        agentsResizeRaf = requestAnimationFrame(() => {
          applyAgentsWidth(clampAgentsWidth(rect.right - moveEvent.clientX), false);
        });
      };
      const onUp = () => {
        agentsResizeHandle.classList.remove("is-dragging");
        document.body.classList.remove("is-resizing-agents");
        agentsResizeHandle.removeEventListener("pointermove", onMove);
        agentsResizeHandle.removeEventListener("pointerup", onUp);
        agentsResizeHandle.removeEventListener("pointercancel", onUp);
        applyAgentsWidth(clampAgentsWidth(currentAgentsWidth()), true);
      };
      agentsResizeHandle.addEventListener("pointermove", onMove);
      agentsResizeHandle.addEventListener("pointerup", onUp);
      agentsResizeHandle.addEventListener("pointercancel", onUp);
    });
    agentsResizeHandle.addEventListener("keydown", (event) => {
      if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
      event.preventDefault();
      const step = event.shiftKey ? 32 : 16;
      applyAgentsWidth(clampAgentsWidth(currentAgentsWidth() + (event.key === "ArrowLeft" ? step : -step)), true);
    });
    agentsResizeHandle.addEventListener("dblclick", () => {
      root.style.removeProperty("--chat-agents");
      try { localStorage.removeItem(STORE_AGENTS_WIDTH); } catch (_) {}
    });
  }
  const searchToggle = root.querySelector("[data-history-search-toggle]");
  const searchCloseBtn = root.querySelector("[data-history-search-close]");
  let searchOpen = false;
  if (searchToggle) {
    searchToggle.addEventListener("click", (event) => {
      event.preventDefault();
      if (searchOpen) setSearchOpen(false);
      else setSearchOpen(true);
    });
  }
  root.querySelectorAll("[data-history-tab]").forEach((btn) => {
    btn.addEventListener("click", (event) => {
      event.preventDefault();
      if (searchOpen) setSearchOpen(false);
      showArchived = false;
      setHistoryTab(btn.getAttribute("data-history-tab"));
    });
  });
  const projectSortEl = root.querySelector("[data-project-sort]");
  if (projectSortEl) {
    projectSortEl.value = projectSort;
    projectSortEl.addEventListener("change", () => {
      projectSort = projectSortEl.value === "project" ? "project" : "recent";
      try { localStorage.setItem(STORE_PROJECT_SORT, projectSort); } catch (_) {}
      renderHistory();
    });
  }
  const projectFilterEl = root.querySelector("[data-project-filter]");
  if (projectFilterEl) {
    projectFilterEl.addEventListener("change", () => {
      projectFilter = projectFilterEl.value || "";
      try { localStorage.setItem(STORE_PROJECT_FILTER, projectFilter); } catch (_) {}
      renderHistory();
    });
  }
  syncHistoryChrome();
  if (searchCloseBtn) {
    searchCloseBtn.addEventListener("click", (event) => {
      event.preventDefault();
      setSearchOpen(false);
    });
  }
  if (historySearch) {
    historySearch.addEventListener("input", () => {
      historyQuery = historySearch.value;
      renderHistory();
      queueSearch();
    });
    historySearch.addEventListener("keydown", (event) => {
      if (event.key !== "Escape") return;
      event.stopPropagation();
      if (historySearch.value) {
        historySearch.value = "";
        historyQuery = "";
        resetSearchLists();
        renderHistory();
        queueSearch();
      } else {
        setSearchOpen(false);
      }
    });
  }
  if (archivedBtn) {
    archivedBtn.addEventListener("click", () => {
      showArchived = !showArchived;
      renderHistory();
    });
  }
  if (historyList) {
    historyList.addEventListener("click", (event) => {
      const overview = event.target.closest("[data-project-overview-open], [data-project-overview-start]");
      if (overview) {
        const start = overview.hasAttribute("data-project-overview-start");
        const [hostId, projectId] = (start ? overview.dataset.projectOverviewStart : overview.dataset.projectOverviewOpen).split(":");
        if (hostId && projectId && requireLive()) {
          const action = start ? startProjectThread(hostId, projectId) : openProject(hostId, projectId);
          action.catch((error) => showFailure("Project could not be opened", error));
        }
        return;
      }
      const more = event.target.closest("button.more");
      if (more) {
        event.preventDefault();
        event.stopPropagation();
        const id = more.closest("[data-thread]")?.dataset.thread;
        if (id) threadMenu(more, id);
        return;
      }
      const open = event.target.closest("button.open");
      if (open) {
        const row = open.closest("[data-project], [data-thread]");
        const project = row?.dataset.project;
        if (project) {
          const [hostId, projectId] = project.split(":");
          if (hostId && projectId) openProject(hostId, projectId);
          return;
        }
        const id = row?.dataset.thread;
        if (id) openThread(id).catch((error) => showFailure("Chat could not be opened", error));
      }
    });
  }
  document.addEventListener("click", (event) => {
    const t = event.target;
    if (t.closest && t.closest("[data-chat-send], .chat-send")) return;
    if (window.FuseComposerSpell && FuseComposerSpell.isOpen() &&
        spellPop && !spellPop.contains(t) && t !== input) {
      FuseComposerSpell.close();
      restoreEnterKeyHint();
      syncComposerAria();
    }
    if (slashMenu && !slashMenu.hidden && !slashMenu.contains(t) && t !== input &&
        !(spellPop && spellPop.contains(t))) closeSlash();
    if (rewindPanel && !rewindPanel.hidden && !rewindPanel.contains(event.target)) closeRewind();
    if (contextPop && !contextPop.hidden && contextWrap && !contextWrap.contains(event.target)) closeContextPop();
    if (pop && !pop.contains(event.target) && !event.target.closest(".more") && !event.target.closest(".chat-msg-actions")) {
      closePop();
    }
    if (root.classList.contains("is-history") && historyToggle && !historyPanel.contains(event.target) && !historyToggle.contains(event.target) && !event.target.closest(".chat-pop")) {
      setHistoryOpen(false);
    }
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Tab" && historyMobile.matches && root.classList.contains("is-history")) {
      const controls = [...historyPanel.querySelectorAll("button, input, [tabindex]"), ...(pop ? pop.querySelectorAll("button") : [])]
        .filter((element) => element && !element.disabled && element.tabIndex >= 0 && element.getClientRects().length);
      const index = controls.indexOf(document.activeElement);
      event.preventDefault();
      const next = index < 0 ? (event.shiftKey ? controls.length - 1 : 0) :
        (index + (event.shiftKey ? -1 : 1) + controls.length) % controls.length;
      controls[next]?.focus({ preventScroll: true });
    }
    if (event.key === "Escape") {
      const confirmOpen = document.querySelector("dialog.modal[data-confirm][open]");
      if (confirmOpen) return;
      closePop();
      if (window.FuseComposerSpell && FuseComposerSpell.isOpen()) {
        FuseComposerSpell.close();
        restoreEnterKeyHint();
        syncComposerAria();
        event.preventDefault();
        return;
      }
      if (slashMenu && !slashMenu.hidden) {
        closeSlash();
        return;
      }
      if (rewindPanel && !rewindPanel.hidden) {
        closeRewind();
        return;
      }
      if (searchOpen) {
        setSearchOpen(false);
        return;
      }
      if (projectPicker && !projectPicker.hidden) {
        setProjectPickerOpen(false);
        return;
      }
      setHistoryOpen(false);
      if (artifactOpen) {
        setArtifactOpen(false);
        return;
      }
      if (memoryOpen) setMemoryOpen(false);
    }
  });

  renderPickerButton();
  defaultHint();
  syncMemoryUi();
  paint();
  startAgeClock();
  resizeInput();
  syncEmptyComposer();
  syncDockSpace();
  if (window.FuseComposerSpell) {
    FuseComposerSpell.mount({
      input,
      mirror: composeMirror,
      popover: spellPop,
      getPrefs: composerPrefs,
      getContext: getComposerContext,
      onChange() {
        drafts.set(currentId, input.value);
        resizeInput();
        syncComposer();
        syncEmptyComposer();
      },
    });
  }
  document.addEventListener("fuse-composer-metrics", () => {
    if (window.FuseComposerSpell) {
      FuseComposerSpell.syncMetrics();
      FuseComposerSpell.refresh();
    }
  });
  document.addEventListener("fuse-composer-pref", () => {
    if (!composerPrefs().palettes && extraPaletteOpen()) closeSlash();
    if (window.FuseComposerSpell) FuseComposerSpell.refresh();
  });
  if (typeof ResizeObserver !== "undefined") {
    dockResizeObserver = new ResizeObserver(() => {
      if (dockSpaceLocked) return;
      const following = stickToBottom;
      const savedTop = thread ? thread.scrollTop : 0;
      const beforeDock = root.style.getPropertyValue("--dock-h");
      syncDockSpace();
      if (window.FuseComposerSpell) FuseComposerSpell.syncMetrics();
      if (following && root.style.getPropertyValue("--dock-h") !== beforeDock) restoreThreadScroll(true, savedTop);
    });
    dockResizeObserver.observe(form);
    const chatBar = root.querySelector(".chat-bar");
    if (chatBar) new ResizeObserver(() => scheduleFitChatBar()).observe(chatBar);
  }
  if (document.fonts && document.fonts.ready) {
    document.fonts.ready.then(() => scheduleFitChatBar()).catch(() => {});
  }
  scheduleFitChatBar();
  pinVisualViewport();
  if (window.visualViewport) {
    window.visualViewport.addEventListener("resize", pinVisualViewport);
    window.visualViewport.addEventListener("scroll", pinVisualViewport);
  }
  window.addEventListener("resize", pinVisualViewport);
  // Viewport class changes re-run the composer sizing and rebuild the picker:
  // the placeholder swap and row height depend on the breakpoint, and the
  // model menu must switch between the anchored dropdown and the fixed sheet.
  pickerSheetQuery.addEventListener("change", () => {
    resizeInput();
    renderPickerButton();
  });
  window.addEventListener("orientationchange", () => {
    pinVisualViewport();
    window.setTimeout(pinVisualViewport, 350);
  });
  const initialNavigation = navigation;
  connectSync(true)
    .then(() => {
      if (navigation !== initialNavigation) return;
      const params = new URLSearchParams(window.location.search);
      const wanted = (params.get("t") || "").trim();
      const art = (params.get("a") || "").trim();
      const open = wanted ? openThread(wanted) : Promise.resolve();
      return open.then(() => {
        if (art && artifactsInThread().some((t) => t.identifier === art)) return showArtifact(art);
        rememberLatestArtifact();
      });
    })
    .catch((error) => showFailure("Chat history could not be loaded", error));
})();
