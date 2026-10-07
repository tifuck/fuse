import { createConsoleTheme } from './themes/console/index.js';

(() => {
  const root = document.querySelector("[data-chat]");
  if (!root) return;

  const {
    ACTION_ICONS,
    FILE_CHIP_ICON,
    agentRowHtml,
    compactionMarkHtml,
    consoleSpinGlyph,
    currentActivity,
    disposeThinking,
    fillUserFiles,
    followLiveDockSlide,
    followThinking,
    formatActivityClock,
    formatConsoleLiveStatus,
    groupTraces,
    jobRowHtml,
    mountActions,
    patchMarkdown,
    permQueueCardHtml,
    renderAssistant,
    renderUser,
    setConsoleSpin,
    setLiveLabelText,
    setLivePhase,
    stopThinkingFollow,
    syncActivityElapsed,
    syncActivitySpin,
    syncConsoleClocks,
    syncLiveDock,
    syncWorkClocks,
    thinkingPosition,
    todoPanelHtml,
    toolDetailHtml,
    toolImage,
    updateAssistant,
    visibleAssistantError,
    workClocksLive,
    workHeadHtml,
  } = createConsoleTheme({
    get PERM_TOTAL_S() { return PERM_TOTAL_S; },
    get REVEAL_CALM() { return REVEAL_CALM; },
    get REVEAL_FIRST() { return REVEAL_FIRST; },
    get REVEAL_MS() { return REVEAL_MS; },
    get SETTLED_PHASES() { return SETTLED_PHASES; },
    get TODO_ROWS() { return TODO_ROWS; },
    get activeAssistant() { return activeAssistant; },
    get activeRun() { return activeRun; },
    get activitySeq() { return activitySeq; },
    set activitySeq(value) { activitySeq = value; },
    get agentActivitySummary() { return agentActivitySummary; },
    get agentDurationSeconds() { return agentDurationSeconds; },
    get agentIsStalled() { return agentIsStalled; },
    get agentLive() { return agentLive; },
    get agentLiveDock() { return agentLiveDock; },
    get agentLiveSpin() { return agentLiveSpin; },
    get agentRun() { return agentRun; },
    get agentStatusText() { return agentStatusText; },
    get agentsRows() { return agentsRows; },
    get applySpinEl() { return applySpinEl; },
    get assistantSegments() { return assistantSegments; },
    get autoEventLabel() { return autoEventLabel; },
    get beginEdit() { return beginEdit; },
    get commitEdit() { return commitEdit; },
    get confirmDeleteAt() { return confirmDeleteAt; },
    get consoleSpinFrame() { return consoleSpinFrame; },
    set consoleSpinFrame(value) { consoleSpinFrame = value; },
    get consoleSpinTimer() { return consoleSpinTimer; },
    set consoleSpinTimer(value) { consoleSpinTimer = value; },
    get copyText() { return copyText; },
    get countsLabel() { return countsLabel; },
    get currentRunSpin() { return currentRunSpin; },
    get detailProbe() { return detailProbe; },
    get editingFiles() { return editingFiles; },
    set editingFiles(value) { editingFiles = value; },
    get editingIndex() { return editingIndex; },
    set editingIndex(value) { editingIndex = value; },
    get escapeHtml() { return escapeHtml; },
    get estimateChars() { return estimateChars; },
    get estimateTrace() { return estimateTrace; },
    get executionIdentity() { return executionIdentity; },
    get executionPending() { return executionPending; },
    get executionState() { return executionState; },
    get fmtPermCountdown() { return fmtPermCountdown; },
    get formatTurnDuration() { return formatTurnDuration; },
    get formatTurnMeta() { return formatTurnMeta; },
    get humanMs() { return humanMs; },
    get hydratePermissionCards() { return hydratePermissionCards; },
    get isCodeOutputTool() { return isCodeOutputTool; },
    get isShellExecTool() { return isShellExecTool; },
    get jobElapsedMs() { return jobElapsedMs; },
    get jobsFor() { return jobsFor; },
    get jobsRows() { return jobsRows; },
    get lastCurrentTrace() { return lastCurrentTrace; },
    get latestPill() { return latestPill; },
    get latestSpin() { return latestSpin; },
    get liveDock() { return liveDock; },
    get liveDockFollowFrame() { return liveDockFollowFrame; },
    set liveDockFollowFrame(value) { liveDockFollowFrame = value; },
    get liveDockHideTimer() { return liveDockHideTimer; },
    set liveDockHideTimer(value) { liveDockHideTimer = value; },
    get liveDockLabel() { return liveDockLabel; },
    get liveDockShowFrame() { return liveDockShowFrame; },
    set liveDockShowFrame(value) { liveDockShowFrame = value; },
    get liveDockSpin() { return liveDockSpin; },
    get liveElapsedSeconds() { return liveElapsedSeconds; },
    get liveMeter() { return liveMeter; },
    get log() { return log; },
    get messages() { return messages; },
    get motionPaints() { return motionPaints; },
    get normalizeTodos() { return normalizeTodos; },
    get openLightbox() { return openLightbox; },
    get paint() { return paint; },
    get partialTranscript() { return partialTranscript; },
    get pauseThreadFollow() { return pauseThreadFollow; },
    get pendingFullPaint() { return pendingFullPaint; },
    set pendingFullPaint(value) { pendingFullPaint = value; },
    get pendingToolPaint() { return pendingToolPaint; },
    set pendingToolPaint(value) { pendingToolPaint = value; },
    get permCardCopy() { return permCardCopy; },
    get permCardSignature() { return permCardSignature; },
    get permLeftS() { return permLeftS; },
    get redoConversation() { return redoConversation; },
    get redoStack() { return redoStack; },
    get reduceMotion() { return reduceMotion; },
    get restoreDisclosures() { return restoreDisclosures; },
    get retryAssistant() { return retryAssistant; },
    get rewindTo() { return rewindTo; },
    get root() { return root; },
    get runIsActive() { return runIsActive; },
    get runIsStopping() { return runIsStopping; },
    get runPhase() { return runPhase; },
    get safeUrl() { return safeUrl; },
    get saveDisclosures() { return saveDisclosures; },
    get scheduleTodoSync() { return scheduleTodoSync; },
    get scrollThread() { return scrollThread; },
    get sessionProject() { return sessionProject; },
    get setToolReveal() { return setToolReveal; },
    get shellExitCode() { return shellExitCode; },
    get showPop() { return showPop; },
    get showThinking() { return showThinking; },
    get snapThread() { return snapThread; },
    get sourceCatalog() { return sourceCatalog; },
    get startLiveClock() { return startLiveClock; },
    get stickToBottom() { return stickToBottom; },
    get streaming() { return streaming; },
    get syncLatest() { return syncLatest; },
    get thread() { return thread; },
    get todoClosed() { return todoClosed; },
    get todoSettleLines() { return todoSettleLines; },
    get toolBackground() { return toolBackground; },
    get toolCounts() { return toolCounts; },
    get toolFailed() { return toolFailed; },
    get toolGestureOwner() { return toolGestureOwner; },
    get toolIsCurrent() { return toolIsCurrent; },
    get toolKey() { return toolKey; },
    get toolMotions() { return toolMotions; },
    get toolPresentation() { return toolPresentation; },
    get tracesSig() { return tracesSig; },
    get transcriptDomOptions() { return transcriptDomOptions; },
    get undoConversation() { return undoConversation; },
    get undoStack() { return undoStack; },
    get usageInOut() { return usageInOut; },
  });


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
  const contextAlert = root.querySelector("[data-context-alert]");
  const contextAlertText = root.querySelector("[data-context-alert-text]");
  const contextAlertCompact = root.querySelector("[data-context-alert-compact]");
  const contextAlertDismiss = root.querySelector("[data-context-alert-dismiss]");
  const historyToggles = [...root.querySelectorAll("[data-history-toggle]")];
  const historyToggle = root.querySelector(".chat-bar [data-history-toggle]");
  const titleEl = root.querySelector("[data-chat-title]");
  const projectChip = root.querySelector("[data-project-chip]");
  const projectName = root.querySelector("[data-project-name]");
  const projectIconEl = root.querySelector("[data-project-icon]");
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
  const latestPill = root.querySelector("[data-chat-latest]");
  const latestSpin = root.querySelector("[data-chat-latest-spin]");
  let liveDockHideTimer = 0;
  let liveDockShowFrame = 0;
  let liveDockFollowFrame = 0;


  const STORE_THINKING = "fuse.chat.thinking.v1";

  // Views that were renamed; a saved old id opens its successor.


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
  const agentsOverviewEl = root.querySelector("[data-agents-overview]");
  const agentJobsEl = root.querySelector("[data-agent-jobs]");
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
  const agentContextBtn = root.querySelector("[data-agent-context]");
  const agentContextPop = root.querySelector("[data-agent-context-pop]");
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
  // Logo URLs come from the server (provider_logo_url) so the picker shows
  // the same versioned file as /sources and /models.
  const logoByType = new Map(families.map((family) => [family.type, family.logo]));
  function providerLogo(type) {
    const src = logoByType.get(type) || "/static/icon.svg";
    return `<img class="provider-logo" src="${escapeHtml(src)}" alt="" aria-hidden="true" draggable="false">`;
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
      image: typeof model.image === "boolean" ? model.image : null,
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
  let threadRecaps = [];
  const dismissedRecapAnchors = new Map();
  let recapsEnabled = config.context?.recaps !== false;
  let contextMessages = [];
  let contextLive = false;
  let undoStack = [];
  let redoStack = [];
  let composerVersion = 0;
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
  let partialTranscript = false;
  let healRunId = "";
  let stoppingRunId = "";
  // Survives a reload (this tab only) so a send whose answer was lost is
  // retried with its original command_id instead of being posted twice.
  const STORE_PENDING_TURN = "fuse.chat.pendingTurn";
  let pendingTurn = null;
  try {
    const saved = JSON.parse(sessionStorage.getItem(STORE_PENDING_TURN) || "null");
    if (saved && saved.id && typeof saved.body?.text === "string" && saved.body.command_id) pendingTurn = saved;
  } catch (_) {}
  function setPendingTurn(value) {
    pendingTurn = value;
    try {
      if (value) sessionStorage.setItem(STORE_PENDING_TURN, JSON.stringify(value));
      else sessionStorage.removeItem(STORE_PENDING_TURN);
    } catch (_) {}
  }
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
  let historyLoaded = false;
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
  const MOTION = { fast: 120, base: 200, slow: 280, snap: "cubic-bezier(0.2, 0, 0, 1)", glide: "cubic-bezier(0.32, 0.72, 0, 1)" };
  const pickerSheetQuery = window.matchMedia("(max-width: 800px)");
  const overlayMobile = pickerSheetQuery;
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
  // Full-screen mobile sheets follow a downward drag on their head and close
  // past a distance or flick; otherwise they spring back (CSS transition).
  function attachSheetSwipe(sheet, headSelector, isSheet, close) {
    let start = null;
    const reset = () => {
      sheet.classList.remove("is-dragging");
      sheet.style.translate = "";
      sheet.style.opacity = "";
    };
    new MutationObserver(() => { if (!sheet.hidden) reset(); }).observe(sheet, { attributes: true, attributeFilter: ["hidden"] });
    sheet.addEventListener("pointerdown", (event) => {
      if (!isSheet() || event.button !== 0 || event.pointerType === "mouse") return;
      const head = event.target.closest(headSelector);
      if (!head || !sheet.contains(head) || event.target.closest("button, input, select, a")) return;
      start = { y: event.clientY, t: performance.now(), dy: 0, id: event.pointerId };
      try { head.setPointerCapture(event.pointerId); } catch (_) {}
    });
    sheet.addEventListener("pointermove", (event) => {
      if (!start || event.pointerId !== start.id) return;
      const dy = Math.max(0, event.clientY - start.y);
      if (!start.dy && dy < 4) return;
      start.dy = dy;
      sheet.classList.add("is-dragging");
      sheet.style.translate = `0 ${dy}px`;
      sheet.style.opacity = String(Math.max(0.5, 1 - dy / (sheet.clientHeight * 1.4)));
    });
    const end = (event) => {
      if (!start || event.pointerId !== start.id) return;
      const { dy, t } = start;
      start = null;
      const flick = dy / Math.max(1, performance.now() - t) > 0.6;
      sheet.classList.remove("is-dragging");
      if (dy > Math.min(140, sheet.clientHeight * 0.22) || (flick && dy > 24)) {
        // Keep the dragged offset; the hide transition carries it off-screen.
        sheet.style.translate = "0 100%";
        sheet.style.opacity = "0";
        close();
      } else {
        sheet.style.translate = "";
        sheet.style.opacity = "";
      }
    };
    sheet.addEventListener("pointerup", end);
    sheet.addEventListener("pointercancel", end);
  }
  // Composer popovers (model, permissions, context) share one placement:
  // rise from the top of the whole composer, line up with the button's left
  // edge (or its right edge when that runs out of room), and clamp to the
  // viewport. The origin is measured rather than assumed, because a chat
  // backdrop gives .chat-box a backdrop-filter, which makes it the containing
  // block for even position:fixed children.
  function placeComposerPop(menu, anchor, maxHeight, pinRight) {
    if (!menu || menu.hidden || !anchor || menu.classList.contains("is-sheet")) return;
    const gutter = 8;
    menu.style.top = "auto";
    menu.style.right = "auto";
    menu.style.left = "0px";
    menu.style.bottom = "0px";
    // The open animation translates the box; measure it at rest.
    menu.style.setProperty("transform", "none", "important");
    const origin = menu.getBoundingClientRect();
    menu.style.removeProperty("transform");
    const b = anchor.getBoundingClientRect();
    const box = anchor.closest(".chat-box");
    const top = (box ? box.getBoundingClientRect().top : b.top) - 6;
    menu.style.maxHeight = Math.max(200, Math.min(maxHeight, Math.round(top - 48))) + "px";
    const width = menu.offsetWidth;
    const vw = document.documentElement.clientWidth;
    let left = b.left;
    if (pinRight && !overlayIsMobile()) left = (box ? box.getBoundingClientRect().right : b.right) - width;
    else if (left + width > vw - gutter) left = b.right - width;
    left = Math.max(gutter, Math.min(left, vw - gutter - width));
    menu.style.left = Math.round(left - origin.left) + "px";
    menu.style.bottom = Math.round(origin.bottom - top) + "px";
  }
  // Mobile layers ride the site-wide back stack (FuseBack in app.js): back
  // closes the top one, and closing one from its own UI removes its entry.
  function overlayPush(kind) {
    if (!overlayIsMobile()) return;
    FuseBack.open("chat:" + kind, () => overlayCloseKind(kind, true));
  }
  function overlayDrop(kind) {
    FuseBack.drop("chat:" + kind);
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
    else if (kind === "search") setSearchOpen(false);
    else if (kind === "pop") closePop();
    else if (kind === "context") closeContextPop();
    else if (kind === "agent-context") closeAgentContextPop();
    else if (kind === "lightbox") closeLightbox();
  }
  // Popovers shown and hidden from many call sites follow their [hidden]
  // attribute instead of wiring every one of them.
  function backLayerFollowsHidden(el, kind) {
    if (!el) return;
    const sync = () => {
      if (el.hidden) overlayDrop(kind);
      else overlayPush(kind);
    };
    new MutationObserver(sync).observe(el, { attributes: true, attributeFilter: ["hidden"] });
  }
  // Layers that live inside the chat drawer. Opening a chat from the drawer
  // must retire their history entries before the chat's own URL is written.
  function dropDrawerLayers() {
    for (const kind of ["pop", "search", "history"]) overlayDrop(kind);
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
  // Composer attachments are drafts too: keyed by thread like the text.
  const draftFiles = new Map();
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
  let currentId = null;
  let showArchived = false;
  let historyQuery = "";
  let historyTab = "chats";
  // Press-hold drag inside the Pinned section; see the pointer handlers by the
  // history list click wiring.
  let pinDrag = null;
  let pinDragClickBlock = 0;
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
  let popRowKey = null;
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


  let showThinking = true;
  try { showThinking = localStorage.getItem(STORE_THINKING) !== "hidden"; } catch (_) {}
  let artifactSourceLoaded = "";
  let agentsOpen = false;
  let agentsRows = [];
  let agentsLoadedFor = "";
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

  // Packed snapshots (?pack=1) carry each text/reasoning segment's length in
  // place of a second copy of its text; slice it back out of the message.
  function untimed(segment) {
    if (!segment || typeof segment !== "object" || !("ms" in segment)) return segment;
    const { ms, ...rest } = segment;
    return rest;
  }

  function unpackSegments(list) {
    if (!Array.isArray(list)) return;
    for (const msg of list) {
      if (!msg || !Array.isArray(msg.segments)) continue;
      const at = { content: 0, reasoning: 0 };
      msg.segments = msg.segments.map((seg) => {
        if (!seg || !(seg.type in at)) return seg;
        if (typeof seg.text === "string" || typeof seg.n !== "number") {
          at[seg.type] += typeof seg.text === "string" ? seg.text.length : 0;
          return seg;
        }
        const { n, ...rest } = seg;
        rest.text = String(msg[seg.type] || "").slice(at[seg.type], at[seg.type] + n);
        at[seg.type] += n;
        return rest;
      });
    }
  }

  function unpackThread(data) {
    if (data && data.segments_packed) {
      unpackSegments(data.messages);
      unpackSegments(data.context_messages);
      delete data.segments_packed;
    }
    return data;
  }

  // A streamed snapshot (?stream=1) is NDJSON: for long threads a partial line
  // with only the newest messages, then the complete snapshot from the same
  // server build. Resolves with the complete one.
  async function readThreadStream(res, onPartial) {
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let parts = [];
    let full = null;
    const take = (line) => {
      if (!line.trim()) return;
      const data = unpackThread(JSON.parse(line));
      if (data.partial) onPartial(data);
      else full = data;
    };
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      // Only the new chunk is scanned for the line break; the multi-megabyte
      // second line is joined once instead of being rescanned per chunk.
      let text = decoder.decode(value, { stream: true });
      let nl;
      while ((nl = text.indexOf("\n")) >= 0) {
        parts.push(text.slice(0, nl));
        take(parts.join(""));
        parts = [];
        text = text.slice(nl + 1);
      }
      if (text) parts.push(text);
    }
    parts.push(decoder.decode());
    take(parts.join(""));
    if (!full) throw new Error("Thread snapshot was incomplete.");
    return full;
  }

  // chat-boot.js starts the linked thread's download from <head>.
  function takeBootThread(id) {
    const boot = window.__fuseBootThread;
    window.__fuseBootThread = null;
    return boot && boot.id === id ? boot.response : null;
  }

  // Command ids still waiting in a thread's local send queue. The snapshot
  // reports which of them the server already accepted (accepted_queue_ids).
  function queuedParam(id) {
    try {
      const items = JSON.parse(localStorage.getItem(queueStoreKey(id)) || "[]");
      const ids = Array.isArray(items) ? items.map((item) => item && item.id).filter((v) => typeof v === "string" && v).slice(0, 64) : [];
      return ids.length ? "&queued=" + encodeURIComponent(ids.join(",")) : "";
    } catch (_) {
      return "";
    }
  }

  async function fetchThreadSnapshot(id, onPartial) {
    const queued = queuedParam(id);
    const boot = takeBootThread(id);
    const res = await ((!queued && boot) || fetch("/chat/threads/" + encodeURIComponent(id) + "?pack=1&stream=1" + queued, {
      headers: { "X-CSRF-Token": csrfToken() },
    }).catch(() => null));
    const streamed = res && res.ok && res.body && (res.headers.get("Content-Type") || "").includes("ndjson");
    if (streamed) {
      try { return await readThreadStream(res, onPartial); } catch (_) {}
    }
    // Errors, redirects to sign-in and interrupted streams take the ordinary
    // request path so they surface exactly as before.
    return unpackThread(await api("/chat/threads/" + id + "?pack=1" + queued));
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
    // An open socket never closes itself while the server keeps pinging, so
    // nothing would flip syncState back to live. Closing runs the retry path.
    if (socket && socket.readyState === WebSocket.OPEN) socket.close();
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

  // Highlight memoization: streaming re-renders a message on nearly every
  // frame, and settled fences never change once closed. Re-parsing them with
  // hljs every time is the dominant O(message) cost per event; string-keyed
  // map hits cost a cached hash plus one memcmp.

  // True while tool detail HTML is built only to learn whether a collapsed
  // row has any details (see toolStepHtml): skip highlighting and markdown.
  let detailProbe = false;

  // Loose lists put blank lines between items; the block splitter would
  // otherwise emit one list per item and restart every ordinal at 1.

  // CommonMark lets a list interrupt a paragraph without a blank line
  // ("Intro:\n- a\n- b"). Only bullets or an ordered list starting at 1 may.

  // Keep settled Markdown nodes mounted while a streamed paragraph grows.
  // Replacing the entire body on every chunk resets selection, code scroll and
  // browser layout anchors even when only its final text node changed.

  // Char offset where a streaming markdown text becomes structurally stable:
  // the last blank-line block boundary that is not inside an open fence or
  // details block. Conservatively matches the renderer's own segmentation,
  // so splitting there can never alter the combined output.

  // Streamed markdown renderer: appends-only text is split into a frozen
  // prefix (rendered once, never re-diffed) and a small active tail that is
  // re-rendered per update. Amortized O(new text) per event instead of
  // O(whole message).


  // —— Paced reveal of streamed text ——
  // Tokens arrive in bursts (a sentence, a paragraph). Painting each burst whole
  // makes the transcript jump by lines, so a live block shows its text from a
  // cursor instead. A small backlog shows at once (a steady stream gains no
  // lag); a burst is spread at a constant rate so that it clears within
  // REVEAL_MS, and the cursor stops on a word end, never inside a word. Nothing
  // is held back for long: the block always catches up to the full text, and a
  // finished block, a hidden tab and reduced motion all show it immediately.
  const REVEAL_MS = 200;
  const REVEAL_CALM = 28;
  const REVEAL_FIRST = 240;

  // Shows `text` in a content block: paced while its message is streaming
  // (`writing`), whole otherwise.


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

  // Text-only models get a short note instead of each image (app/model_caps.py).
  function imageTokens() {
    const model = currentModel();
    return model && model.image === false ? 30 : 1500;
  }

  function chatHasImages() {
    const isImage = (att) => att && att.kind === "image";
    return pendingAttachments.some(isImage) || messages.some((msg) => (msg.attachments || []).some(isImage) || (msg.tools || []).some((trace) => toolImage(trace)));
  }

  function warnTextOnlyModel() {
    const model = currentModel();
    if (model && model.image === false && chatHasImages()) {
      setHint(model.name + " can't view images. They'll be skipped and kept for image models.", false, 6000);
    }
  }

  const compactCfg = Object.assign(
    { warn_ratio: 0.8, auto_ratio: 0.9, auto: true, default_window: 128000, strategy: "summarize" },
    config.compaction && typeof config.compaction === "object" ? config.compaction : {}
  );
  const CONTEXT_RING = 2 * Math.PI * 12;

  function formatTokens(value) {
    const n = Math.max(0, Math.round(Number(value) || 0));
    if (n >= 1_000_000 || Math.round(n / 1000) >= 1000) return (n / 1_000_000).toFixed(n >= 10_000_000 ? 0 : 1).replace(/\.0$/, "") + "M";
    if (n >= 10_000) return Math.round(n / 1000) + "k";
    if (n >= 1000) return (n / 1000).toFixed(1).replace(/\.0$/, "") + "k";
    return String(n);
  }

  function formatTurnMeta(msg) {
    const bits = [];
    {
      const split = usageInOut(msg && msg.usage);
      if (split && split.out) {
        const fmt = new Intl.NumberFormat();
        bits.push({
          text: formatTokens(split.out) + " tok",
          title: fmt.format(split.out) + " output tokens this reply",
        });
      }
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
            if (part.type === "image_url" || part.type === "input_image") messagesN += imageTokens();
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
        if (String(att.kind || att.mime || "").startsWith("image")) messagesN += imageTokens();
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
        total += imageTokens();
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
    const rows = partsRows({ system, history, messages: messagesN, thinking, tool_calls: toolCalls, tools: schemas, tool_families: families });
    if (draft) rows.push(["Draft", draft, ""]);
    return rows;
  }

  // One fixed row set for every context card (main agent and subagents), so
  // the breakdown keeps its shape between turns; empty rows dim, not vanish.
  function partsRows(parts) {
    const n = (key) => Math.max(0, Number(parts?.[key]) || 0);
    const rows = [
      ["System", n("system"), ""],
      ["History", n("history"), ""],
      ["Messages", n("messages"), "child"],
      ["Thinking", n("thinking"), "child"],
      ["Tool calls", n("tool_calls"), "child"],
    ];
    const families = parts?.tool_families || {};
    Object.keys(families).sort().forEach((name) => {
      const value = Number(families[name]) || 0;
      if (value) rows.push([name.replace(/\b\w/g, (ch) => ch.toUpperCase()), value, "grand"]);
    });
    rows.push(["Tool schemas", n("tools"), ""]);
    return rows;
  }

  // Shared context card: built once per popover, then only its numbers are
  // updated, so the text is stable while a turn streams.
  function fillContextCard(pop, card) {
    if (!pop) return;
    if (!pop.querySelector("[data-ctx-used]")) {
      pop.innerHTML =
        '<div class="chat-context-pop-head"><p class="chat-context-pop-kicker">Context</p>' +
        '<p class="chat-context-pop-meter"><span data-ctx-used></span><span class="chat-context-pop-pct" data-ctx-pct></span></p></div>' +
        '<div class="chat-context-pop-bar" aria-hidden="true"><i data-ctx-bar></i></div>' +
        '<ul class="chat-context-pop-parts" data-ctx-parts></ul>' +
        '<div class="chat-context-pop-foot"><p class="chat-context-pop-note" data-ctx-note></p>' +
        (card.compact ? '<button type="button" class="btn sm" data-context-pop-compact>Compact now</button>' : "") +
        "</div>";
    }
    const ratio = Math.max(0, Math.min(1, card.ratio || 0));
    const pct = Math.round(ratio * 100);
    pop.querySelector("[data-ctx-used]").textContent = `${formatTokens(card.used)} / ${formatTokens(card.limit)}`;
    pop.querySelector("[data-ctx-pct]").textContent = `${pct}%`;
    const bar = pop.querySelector("[data-ctx-bar]");
    bar.style.width = `${(ratio * 100).toFixed(1)}%`;
    pop.classList.toggle("is-warn", Boolean(card.warn) && !card.auto);
    pop.classList.toggle("is-auto", Boolean(card.auto));
    const list = pop.querySelector("[data-ctx-parts]");
    const html = card.rows.map(([label, tokens, nest]) => {
      const cls = [nest === "child" ? "is-child" : "", nest === "grand" ? "is-grand" : "", tokens <= 0 ? "is-zero" : ""].filter(Boolean).join(" ");
      return `<li${cls ? ` class="${cls}"` : ""}><span>${escapeHtml(label)}</span><span>${formatTokens(tokens)}</span></li>`;
    }).join("");
    if (list.dataset.html !== html) {
      list.innerHTML = html;
      list.dataset.html = html;
    }
    pop.querySelector("[data-ctx-note]").textContent = card.autoEnabled
      ? `Auto-compacts at ${Math.round((card.autoRatio || 0) * 100)}%`
      : "Auto-compact is off";
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
    if (contextPop && !contextPop.hidden) {
      fillContextCard(contextPop, {
        used: snap.used, limit: snap.limit, ratio, rows: occupancyRows(snap), warn: snap.warn, auto: snap.auto,
        autoEnabled: snap.auto_enabled, autoRatio: snap.auto_ratio, compact: true,
      });
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
    setHint("Compacting context · summarizing " + formatTokens(contextSnapshot().used) + " tokens, this can take a few minutes…", false, 0);
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
        setHint("Not enough history to compact yet.", false, 6000);
      } else {
        const meter = (data && data.thread && data.thread.compaction && data.thread.compaction.meter) || contextSnapshot();
        setHint("Context compacted · " + formatTokens(meter.used) + " / " + formatTokens(meter.limit) + " tokens.", false, 8000);
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
      else if (data?.busy) setHint("Title generation is already running.");
    } catch (error) {
      if (error && error.status === 429) setHint(error.message || "Title regeneration limit reached. Try again later.", true);
      else showFailure("Could not regenerate the title", error);
    } finally {
      titleRegenRequest = false;
    }
  }

  async function runRecap() {
    if (!currentId || !messages.length || !requireLive()) return;
    try {
      await api("/chat/threads/" + currentId + "/recap", { method: "POST", body: "{}" });
      setHint("Recap queued; generates once background work finishes.", false, 6000);
    } catch (error) { showFailure("Could not request a recap", error); }
  }

  const recapsToggle = viewMenu?.querySelector("[data-recaps-toggle]");
  if (recapsToggle) {
    recapsToggle.checked = recapsEnabled;
    recapsToggle.addEventListener("change", async () => {
      recapsToggle.disabled = true;
      try {
        const data = await api("/chat/context", { method: "PUT", body: JSON.stringify({
          recaps: recapsToggle.checked, expected_revision: contextRevision, command_id: newSession(),
        }) });
        acceptContext(data);
      } catch (error) {
        recapsToggle.checked = recapsEnabled;
        showFailure("Could not save recap preference", error);
      } finally { recapsToggle.disabled = false; }
    });
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
  const IMAGE_ICON =
    '<svg viewBox="0 0 16 16" width="12" height="12" aria-hidden="true"><rect x="2" y="3" width="12" height="10" rx="1.6" fill="none" stroke="currentColor" stroke-width="1.4"/><circle cx="6" cy="6.6" r="1.2" fill="currentColor"/><path d="M2.8 12l3.6-3.6 2.4 2.4 1.6-1.6 3 3" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round"/></svg>';
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
    const threadTop = thread ? thread.scrollTop : 0;
    const previousHeight = input.style.height;
    dockSpaceLocked = true;
    input.style.height = "auto";
    syncComposerExpanded();
    const style = window.getComputedStyle(input);
    const min = parseFloat(style.minHeight) || 0;
    const max = parseFloat(style.maxHeight) || 192;
    const nextHeight = Math.min(Math.max(input.scrollHeight, min), Math.max(min, max)) + "px";
    input.style.height = nextHeight;
    dockSpaceLocked = false;
    input.placeholder = wanted;
    // The height:auto measure collapses a wrapped field to one row for an
    // instant. Where the dock is in flow (Console) that grows the transcript,
    // and the browser clamps its scrollTop to the new max. Restore it before
    // paint, or every keystroke drops the log a line and the eased follow
    // drags it back: a per-key jitter while a reply streams.
    if (thread && thread.scrollTop !== threadTop) thread.scrollTop = threadTop;
    // Keep the transcript glued while the row grows or shrinks: the eased
    // follow lags the layout change, so without this re-pin the log bounces
    // up and down on every new wrapped line. Skip the snap when height is
    // unchanged — overlay/focus remeasures used to bounce a settled log.
    if (thread && (stickToBottom || followGap <= 1) && previousHeight !== nextHeight) thread.scrollTop = Math.max(0, thread.scrollHeight - thread.clientHeight);
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


  function syncComposerExpanded() {
    const expanded = root.classList.contains("is-composer-expanded");
    let next = false;
    if (input.value) {
      if (expanded || input.value.includes("\n")) next = true;
      else {
        const style = window.getComputedStyle(input);
        const line = parseFloat(style.lineHeight) || 24;
        const pad = (parseFloat(style.paddingTop) || 0) + (parseFloat(style.paddingBottom) || 0);
        next = input.scrollHeight - pad > line * 1.5;
      }
    }
    if (next === expanded) return;
    const moving = composerFlipTargets();
    const before = moving.map((el) => el.getBoundingClientRect());
    root.classList.toggle("is-composer-expanded", next);
    composerFlip(moving, before);
  }

  function composerFlipTargets() {
    if (reduceMotion.matches || !form) return [];
    const box = form.querySelector(".chat-box");
    if (!box) return [];
    return [box.querySelector(".chat-compose"), ...box.querySelectorAll(".chat-box-left > *, .chat-box-right > *")]
      .filter((el) => el && el.offsetParent !== null);
  }

  // FLIP: every piece glides from where it was to where the new layout puts
  // it, so the reflow reads as one motion instead of a jump. Transforms only;
  // the caret and focus stay on the same textarea throughout.
  function composerFlip(moving, before) {
    moving.forEach((el, i) => {
      const was = before[i];
      const now = el.getBoundingClientRect();
      const dx = was.left - now.left;
      const dy = was.top - now.top;
      if (Math.abs(dx) < 1 && Math.abs(dy) < 1) return;
      el.animate(
        [{ transform: `translate(${dx}px, ${dy}px)` }, { transform: "none" }],
        { duration: MOTION.base, easing: MOTION.snap },
      );
    });
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
    newBtns.forEach((btn) => {
      btn.disabled = sending;
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
    return Boolean(runIsActive(activeRun) && runIsStopping(activeRun) && activeAssistant()?.tools?.includes(trace));
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
      .filter(([key]) => counts[key])
      .map(([key, label]) => `${counts[key]} ${label}${key === "warning" && counts[key] > 1 ? "s" : ""}`)].join(" · ");
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
      if (changed && index < messages.length) {
        const article = articleFor(msg);
        if (article) updateAssistant(article, msg, Boolean(streaming && activeRun?.message_id === msg.id));
      }
      if (changed && index >= messages.length) childChanged = true;
    });
    if (childChanged && agentsOpen) scheduleAgentView();
    syncLiveDock();
    if (workClocksLive()) startLiveClock();
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
    if (trace.host) rec.host = String(trace.host).slice(0, 40);
    if (trace.path) rec.path = String(trace.path).slice(0, 400);
    if (trace.cwd) rec.cwd = String(trace.cwd).slice(0, 400);
    if (trace.command) rec.command = String(trace.command).slice(0, 8000);
    for (const key of ["project_root", "platform", "include"]) if (trace[key]) rec[key] = String(trace[key]).slice(0, key === "include" ? 200 : 400);
    if (trace.pattern) rec.pattern = String(trace.pattern).slice(0, 200);
    if (trace.preview) rec.preview = String(trace.preview).slice(0, 800);
    const image = toolImage(trace);
    if (image) rec.image = image;
    if (trace.child_id) rec.child_id = String(trace.child_id).slice(0, 80);
    if (trace.background) rec.background = true;
    if (trace.summary) rec.summary = String(trace.summary).slice(0, 2000);
    if (trace.description) rec.description = String(trace.description).slice(0, 200);
    if (trace.subagent_type) rec.subagent_type = String(trace.subagent_type).slice(0, 32);
    for (const key of ["bytes", "http_status", "agent_tool_uses", "agent_tokens", "lines_added", "lines_removed"]) {
      const n = Number(trace[key]);
      if (trace[key] != null && Number.isInteger(n) && n >= 0) rec[key] = n;
    }
    if (Array.isArray(trace.edited_files) && trace.edited_files.length) {
      rec.edited_files = trace.edited_files.filter((f) => typeof f === "string" && f).slice(0, 20).map((f) => f.slice(0, 400));
    }
    if (trace.edit_diff) rec.edit_diff = String(trace.edit_diff).slice(0, 12000);
    if (Array.isArray(trace.todos)) rec.todos = normalizeTodos(trace.todos);
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
    if (Array.isArray(event.todos)) rec.todos = normalizeTodos(event.todos);
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
    if (partialTranscript) throw new Error("This chat is still loading. Try again in a moment.");
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
        if (msg.role === "assistant" && msg.endedAt) row.endedAt = msg.endedAt;
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
      // A transcript edit withheld during streaming is still unsaved.
      // It stays dirty (no requeue loop mid-run) and is flushed once the run
      // reaches a terminal event.
      const transcriptSent = Boolean(payload.messages) || !transcriptDirty;
      if (revision !== savedRevision) queueSave();
      else if (transcriptSent) { dirty = false; transcriptDirty = false; }
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
        // A transcript conflict yields to the server. A settings-only save
        // stays dirty so the snapshot keeps the user's choice and re-saves it.
        if (payload.messages) dirty = false;
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

  function setThreadUrl(id, { stay = false } = {}) {
    const url = new URL(window.location.href);
    if (id) url.searchParams.set("t", String(id));
    else url.searchParams.delete("t");
    // Closing a layer (e.g. the drawer a chat was picked from) steps history
    // back asynchronously; write the address once that entry is current.
    if (!stay) dropDrawerLayers();
    FuseBack.whenSettled(() => {
      if (stay) {
        // Still on the mobile list (the open chat was deleted or archived):
        // update the address only, keep whatever entry we are on.
        window.history.replaceState(window.history.state, "", url);
        return;
      }
      const mark = historyMobile.matches ? { fuseChat: true } : {};
      if (mark.fuseChat && !(history.state && history.state.fuseChat)) {
        // Mobile: entering a chat leaves the previous entry (the chat list) below,
        // so a back swipe returns to the list instead of leaving the app.
        window.history.pushState(mark, "", url);
      } else {
        window.history.replaceState(mark, "", url);
      }
    });
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
    } else if (pendingTurn?.id === currentId) {
      text = "Send unconfirmed. Retry safely.";
    }
    if (el) el.textContent = text;
    if (bar) {
      bar.dataset.syncState = state;
      bar.classList.toggle("is-fault", fault);
      const hideSync = !fault && pendingTurn?.id !== currentId && state !== "disconnected" && state !== "session";
      bar.hidden = hideSync;
    }
    const retry = root.querySelector("[data-sync-retry]");
    if (retry) retry.hidden = !(live && pendingTurn?.id === currentId);
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
    if (streaming || agentClockLive()) startLiveClock();
    else stopLiveClock();
    const current = threadById(currentId);
    if (current) current.running = currentThreadBusy();
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
    recapsEnabled = data.recaps !== false;
    if (recapsToggle) recapsToggle.checked = recapsEnabled;
    instructions = data.instructions || "";
    profile = data.profile || "";
    syncMemoryUi();
    syncStatus();
  }

  function acceptThread(data) {
    if (data && !Number.isSafeInteger(data.event_cursor)) throw new Error("Thread snapshot is missing event_cursor; live sync requires a consistent snapshot.");
    if (!data || data.id !== currentId) return;
    // Viewing the thread is what dismisses its finished-run badge; the server
    // snapshot arrives with unread already cleared by the GET.
    const sidebarRow = threadById(currentId);
    if (sidebarRow && sidebarRow.unread) {
      sidebarRow.unread = false;
      renderHistory();
    }
    setPartialTranscript(false);
    // A snapshot may carry an older durable replay cursor than the patches
    // already applied (server state writes are throttled). Accept it and let
    // the socket replay the gap; the snapshot is the authoritative base.
    if (Number(data.event_cursor) < viewCursor) viewCursor = Number(data.event_cursor);
    lastRunReceiptAt = Date.now();
    acceptPermThread(data);
    acceptedQueueCommands = new Set(data.accepted_queue_ids || []);
    retireAcceptedQueue();
    if (editingIndex >= 0) {
      const editing = messages[editingIndex];
      const next = data.messages[editingIndex];
      if (!editing || !next || next.id !== editing.id || next.content !== editing.content) {
        editingIndex = -1;
        editingFiles = [];
      }
    }
    const viewport = captureThreadViewport();
    const old = new Map(messages.map((msg) => [msg.id, msg]));
    const previousReply = old.get(activeRun?.message_id);
    if (previousReply && activeRun?.id === data.run?.id) {
      // The steer response freezes the run's *durable* row, which can lag the
      // streamed patches by up to a flush interval — the snapshot's frozen
      // text may be shorter or longer than what this client holds. Accept a
      // prefix in either direction (one side is always a prefix of the other
      // at a steer); otherwise the in-place re-key below never runs and the
      // relocated node visibly jumps the transcript.
      const prefixEither = (a, b) => !b || a.startsWith(b) || b.startsWith(a);
      const prevTools = previousReply.tools || [];
      const frozen = data.messages.find(msg => msg.role === "assistant" && msg.tools_live && !old.has(msg.id) &&
        (previousReply.content || previousReply.reasoning || previousReply.tools?.length) &&
        prefixEither(String(msg.content || ""), previousReply.content || "") && prefixEither(String(msg.reasoning || ""), previousReply.reasoning || "") &&
        (!(msg.tools || []).length || !prevTools.length ||
          (msg.tools || []).every(tool => prevTools.some(p => p.id === tool.id)) ||
          prevTools.every(tool => (msg.tools || []).some(next => next.id === tool.id))));
      if (frozen) {
        // A stale snapshot would drop the streamed tail until the next
        // checkpoint update arrives; keep the client's longer text.
        if (String(frozen.content || "").length < String(previousReply.content || "").length ||
            String(frozen.reasoning || "").length < String(previousReply.reasoning || "").length) {
          for (const key of ["content", "reasoning", "segments"]) if (previousReply[key] != null) frozen[key] = previousReply[key];
        }
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
    threadRecaps = visibleRecaps(data);
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
    // Unsaved local settings (model, search, thread context) outrank the
    // snapshot: it predates them. Keep them and re-save against the new
    // revision instead of silently reverting what the user just changed.
    const keepLocalSettings = dirty;
    if (!keepLocalSettings) {
      threadContext = data.context || "";
      searchOn = data.tools?.includes("web_search") ?? true;
      const listed = resolveListedModel(data.model);
      if (listed && modelId !== listed) { modelId = listed; renderPickerButton(); }
    }
    if (data.compaction && data.compaction.meter) acceptMeter(data.compaction.meter);
    else syncContextMeter();
    transcriptDirty = false;
    pendingSave = null;
    if (keepLocalSettings) queueSave();
    else dirty = false;
    syncMemoryUi();
    if (same) {
      foldAutoCommandResults(messages);
      reconcileBgTraces([...completedJobs.values()], false);
      messages.forEach((msg, i) => {
        if (i >= oldLength) {
          const live = messageRunLive(msg);
          const article = msg.role === "user" ? renderUser(msg, i, live) : renderAssistant(msg, live);
          article.dataset.messageId = msg.id || "";
          if (msg.role === "assistant" && log.childElementCount) markArriving(article);
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
    paintRecaps();
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
    const query = (!dirty && !transcriptDirty
      ? "?observed_cursor=" + viewCursor + "&observed_thread_cursor=" + threadWatermark + "&pack=1"
      : "?pack=1") + queuedParam(id);
    try { data = unpackThread(await api("/chat/threads/" + id + query)); }
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
    } else if (event.type === "recap.updated") {
      if (event.thread_id === currentId) {
        if (!sending && !queuedTurns.length) dismissedRecapAnchors.get(currentId)?.delete(String(payload.anchor_id));
        await reconcileThread();
      }
    } else if (event.type === "recap.failed") {
      if (event.thread_id === currentId) setHint("Could not generate a recap. Try /recap again.", true);
    } else if (event.type === "title.failed") {
      if (event.thread_id === currentId) setHint("Title generation failed. Try /title again.", true);
    } else if (event.type.startsWith("run.") && event.thread_id && event.thread_id !== currentId &&
               (event.type === "run.started" || ["run.completed", "run.cancelled", "run.failed", "run.interrupted"].includes(event.type))) {
      const item = threadById(event.thread_id);
      if (item) {
        const started = event.type === "run.started";
        // A finished root turn can leave subagents working. Its thread.updated
        // follows in the same commit and carries the authoritative flag, so
        // only clear here when no thread row (and so no such event) exists.
        const next = started || (payload.revision != null ? item.running : false);
        if (started || payload.revision == null) serverRunning.set(item.id, next);
        // A finished conversation the user is not reading gets the sidebar
        // badge; opening the thread clears it (the snapshot GET marks read).
        if (!started && !item.unread) item.unread = true;
        if (item.running !== next || item.unread) {
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
      } else if (payload.run_id && Date.now() - agentsRefreshAt > 1500) {
        agentsRefreshAt = Date.now();
        refreshAgents();
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
          // path's 1s cadence. Settled agent events still reconcile at once,
          // and thread snapshots rebuild traces from scratch.
          if (Date.now() - agentTraceRenderAt > 1000) {
            agentTraceRenderAt = Date.now();
            reconcileAgentTraces([...agentLifecycle.values()]);
          }
          // Updates only exist once a steer split the reply (display_parts);
          // they resize frozen-prefix articles above the reading position, so
          // hold the viewport across the synchronous repaint.
          const updateViewport = captureThreadViewport();
          for (const update of payload.updates || []) {
            const index = messages.findIndex((item) => item.id === update.id);
            if (index < 0) { await reconcileThread(); break; }
            Object.assign(messages[index], update);
            const article = articleFor(messages[index]);
            if (article) updateAssistant(article, messages[index], false);
          }
          restoreThreadViewport(updateViewport);
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
          if (compacting) { compacting = false; syncContextMeter(); }
          stoppingRunId = "";
          activeRun = null;
          setRunning();
        }
        notifyTurnDone(event.thread_id, payload, event.type);
        if (payload.revision != null) serverRevision = payload.revision;
        viewCursor = event.id;
        threadWatermark = event.id;
        if (editingIndex < 0 && msg && !msg.noActions) {
          const article = articleFor(msg);
          if (article) mountActions(article, messages.indexOf(msg));
        }
        syncStatus();
        syncContextMeter();
        if (transcriptDirty && !streaming) await flushSave().catch(() => {});
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
        historyLoaded = true;
        threads = data.threads.filter((item) => !item.archived);
        archivedThreads = data.threads.filter((item) => item.archived);
        noteServerRunning(data.threads);
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
    if (Object.prototype.hasOwnProperty.call(thread, "running")) serverRunning.set(thread.id, Boolean(thread.running));
    if (thread.id === currentId) running = currentThreadBusy();
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
      project_color: thread.project_color ?? prior?.project_color ?? "",
      project_logo_url: thread.project_logo_url ?? prior?.project_logo_url ?? "",
      schedule_id: thread.schedule_id ?? prior?.schedule_id ?? null,
      updated_at: thread.updated_at,
      pinned: thread.pinned ?? prior?.pinned ?? false,
      pin_order: thread.pin_order ?? prior?.pin_order ?? 0,
      unread: thread.unread ?? prior?.unread ?? false,
      running,
    };
    if (idx >= 0) bucket[idx] = lite;
    else bucket.unshift(lite);
    const drop = other.findIndex((item) => item.id === thread.id);
    if (drop >= 0) other.splice(drop, 1);
    bucket.sort((a, b) => String(b.updated_at).localeCompare(String(a.updated_at)));
    if (!thread.archived && thread.kind === "project" && thread.project_archived === false) {
      const project = sidebarProject(thread.host_id, thread.project_id);
      if (project) project.archived = false;
    }
    if (!thread.archived && prior?.archived && thread.id === currentId) showArchived = false;
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
    const known = threads.some((item) => item.id === id) || archivedThreads.some((item) => item.id === id);
    const ids = filteredHistory().map((item) => String(item.id));
    const at = ids.indexOf(String(id));
    const next = at < 0 ? "" : (ids[at + 1] ?? ids[at - 1] ?? "");
    dropHistoryStacks(id);
    dropThread(id);
    if (currentId !== id) { if (known) renderHistory(); return; }
    if (!known) return;
    if (next) openThread(next, { gone: true }).catch((error) => showFailure("Chat could not be opened", error));
    else clearChatState({ keepList: true });
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

  // The scroll gap at which the running reply's status line slips out of
  // view: its spinner's middle reaches the composer laid over the transcript,
  // or the transcript's bottom edge where the composer sits below it. Null
  // while no status line is showing.
  function liveStatusEdge(gap) {
    if (!liveDock || liveDock.hidden || liveDock.getAttribute("aria-hidden") === "true") return null;
    const mark = (liveDockSpin?.getClientRects().length ? liveDockSpin : liveDock).getBoundingClientRect();
    const view = thread.getBoundingClientRect();
    const dockTop = form ? form.getBoundingClientRect().top : view.bottom;
    return gap - (mark.top + mark.height / 2 - Math.min(view.bottom, dockTop));
  }

  function syncLatestSpin(live) {
    latestPill.classList.toggle("is-live", live);
    if (!latestSpin) return;
    if (!live) {
      if (latestSpin.firstChild) latestSpin.replaceChildren();
      delete latestSpin.dataset.spinSet;
    } else setConsoleSpin(latestSpin);
  }

  function syncLatest() {
    if (!latestPill) return;
    // A reader who nudged up a line is still at the end; the pill waits until
    // they are genuinely away (or output piles up below), and once shown it
    // holds until they are back near the bottom, so it never flickers at the
    // threshold. While a reply runs, its status line ends the transcript and
    // the pill takes over its spinner the moment that line scrolls out of
    // view, handing it back once the line is visible again.
    const gap = thread.scrollHeight - thread.scrollTop - thread.clientHeight;
    const edge = liveStatusEdge(gap);
    const away = edge == null ? Math.max(160, thread.clientHeight * 0.35) : edge;
    const back = edge == null ? 48 : edge - 8;
    const hide = !messages.length || gap <= 1 || (stickToBottom && nearBottom()) || gap < (latestPill.hidden ? away : back);
    if (hide) {
      latestPill.hidden = true;
      syncLatestSpin(false);
      return;
    }
    syncLatestSpin(edge != null);
    latestPill.hidden = false;
  }

  function threadFollowTarget() {
    return Math.max(0, thread.scrollHeight - thread.clientHeight);
  }

  function snapThread() {
    if (!thread || !stickToBottom) return;
    const target = threadFollowTarget();
    // Tolerate a rounding pixel: fractional console geometry can flip the
    // integer scrollHeight by 1px, and correcting that flip makes the whole
    // viewport oscillate a pixel; real growth is a line or more.
    if (Math.abs(target - thread.scrollTop) > 1) thread.scrollTop = target;
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

  // Following the bottom is pinning, not scrolling: the content grows and the
  // viewport moves with it in the same frame, so the newest text and the status
  // line hold one screen position while text fills in above them. An eased
  // follow (the old behavior) let each chunk land below the view first and
  // glide up a few frames later, which read as text popping in under the
  // composer. Only the "Jump to latest" pill glides, from a real distance.
  function scrollThread(glide = false) {
    const entering = threadFollowThread !== currentId;
    threadFollowThread = currentId;
    if (!stickToBottom) {
      syncLatest();
      return;
    }
    const target = threadFollowTarget();
    const gap = target - thread.scrollTop;
    if (entering || !glide || reduceMotion.matches || !streaming || Math.abs(gap) <= 1) {
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
        color: info?.color || current.project_color || "",
        logo_url: info?.logo_url || current.project_logo_url || "",
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
        if (projectIconEl) projectIconEl.innerHTML = projectIcon(bound, 14);
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

  // Mobile wraps the bar onto extra rows; publish its measured height so the
  // thread and top fade clear it. Desktop is a fixed single row.
  function fitChatBar() {
    const bar = root.querySelector(".chat-bar");
    if (!bar) return;
    const following = stickToBottom;
    const savedTop = thread ? thread.scrollTop : 0;
    const beforeH = root.style.getPropertyValue("--chat-bar-h");
    if (window.matchMedia("(max-width: 800px)").matches) {
      root.style.setProperty("--chat-bar-h", Math.ceil(bar.getBoundingClientRect().height) + "px");
    } else {
      root.style.removeProperty("--chat-bar-h");
    }
    if (following && beforeH !== root.style.getPropertyValue("--chat-bar-h")) restoreThreadScroll(true, savedTop);
  }

  function scheduleFitChatBar() {
    if (chatBarFitRaf) return;
    chatBarFitRaf = window.requestAnimationFrame(() => {
      chatBarFitRaf = 0;
      fitChatBar();
    });
  }

  let dockTimer = 0;

  function playDockTransition() {
    {
      playTranscriptFx();
      return;
    }

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
    if (pop) {
      pop.remove();
      overlayDrop("pop");
    }
    pop = null;
    popRowKey = null;
    historyList && historyList.querySelectorAll(".is-menu").forEach((el) => el.classList.remove("is-menu"));
  }

  function showPop(anchor, items, at) {
    closePop();
    // The row whose menu is open keeps .is-menu while the pop is up. closePop()
    // just stripped every flag, so stamp the owner here and remember its key:
    // a repaint replaces the row nodes, and syncHistoryFooter re-stamps them.
    const menuRow = anchor && anchor.closest ? anchor.closest(".chat-history-item") : null;
    if (menuRow) {
      const attr = menuRow.hasAttribute("data-thread") ? "data-thread" : "data-project";
      popRowKey = [attr, menuRow.getAttribute(attr)];
      menuRow.classList.add("is-menu");
    }
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
    overlayPush("pop");
    const rect = anchor ? anchor.getBoundingClientRect() : null;
    const width = pop.offsetWidth;
    const height = pop.offsetHeight;
    let left = at ? at.x : rect.right - width;
    let top = at ? at.y : rect.bottom + 4;
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
    if (next !== historyTab) showArchived = false;
    historyTab = next;
    if (persist) {
      try { localStorage.setItem(STORE_TAB, next); } catch (_) {}
    }
    syncHistoryChrome();
    if (!silent) renderHistory();
  }

  function syncHistoryChrome() {
    root.querySelectorAll("[data-history-tab]").forEach((btn) => {
      const on = btn.getAttribute("data-history-tab") === historyTab;
      btn.setAttribute("aria-selected", String(on));
      btn.classList.toggle("is-on", on);
    });
    syncHistoryUnread();
    const heading = root.querySelector("[data-history-heading]");
    if (heading) heading.textContent = historyTab === "projects" ? "Projects" : "Chats";
    const newLabel = root.querySelector("[data-chat-new-label]");
    const newBtn = root.querySelector("[data-chat-new]");
    const projectLabel = historyTab === "projects";
    if (newLabel) newLabel.textContent = projectLabel ? "New project" : "New chat";
    if (newBtn) {
      newBtn.setAttribute("aria-label", projectLabel ? "New project session" : "New chat");
      newBtn.title = projectLabel ? "New project session" : "New chat";
      if (projectLabel) newBtn.setAttribute("aria-haspopup", "dialog");
      else newBtn.removeAttribute("aria-haspopup");
    }
    const filters = root.querySelector("[data-project-filters]");
    if (filters) {
      filters.hidden = historyTab !== "projects" || Boolean(historyQuery.trim()) || showArchived;
    }
    fillProjectFilterOptions();
    syncComposer();
  }

  // Unread counts for the tab switch: a finished conversation badges its
  // tab so the other tab's results are visible before switching.
  function syncHistoryUnread() {
    const counts = { chats: 0, projects: 0 };
    threads.forEach((item) => {
      if (!item.unread || item.id === currentId) return;
      counts[item.kind === "project" ? "projects" : "chats"] += 1;
    });
    root.querySelectorAll("[data-history-tab]").forEach((btn) => {
      const tab = btn.getAttribute("data-history-tab");
      const badge = btn.querySelector("[data-history-unread]");
      if (!badge) return;
      const count = counts[tab] || 0;
      badge.hidden = count === 0 || tab === historyTab;
      badge.textContent = count > 9 ? "9+" : String(count);
    });
  }

  // Archived threads belong to the tab of their kind: project sessions under
  // Projects, everything else under Chats.
  function archivedForTab() {
    const projectTab = historyTab === "projects";
    return archivedThreads.filter((item) => (item.kind === "project") === projectTab);
  }

  function archivedProjects() {
    return historyTab === "projects" ? projects.filter((item) => item.archived) : [];
  }

  function filteredHistory() {
    const source = showArchived ? archivedForTab() : threads.filter((item) => item.kind !== "project");
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
    {
      return `<span class="chat-run-spin" data-run-spin="${escapeHtml(String(stamp))}" data-spin-set="console-glyph" aria-hidden="true">` +
        `<span class="chat-console-spin">${consoleSpinGlyph()}</span></span>`;
    }

  }

  function fillSpinEl(el, stamp, swap) {

    {
      setConsoleSpin(el);
      return;
    }

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

  // The open thread is busy while its own turn streams or any of its
  // subagents is live: a background child can outlive the turn that spawned it.
  // Until this thread's agent list has loaded, keep the server's flag rather
  // than clearing it on an empty list.
  // The fallback must be the server's own last word, never the row's local
  // flag: setRunning writes that flag from this function, so reading it back
  // keeps a spinner the turn already ended (a chat created by its first send
  // never loads its agent list).
  const serverRunning = new Map();
  function noteServerRunning(list) {
    (list || []).forEach((item) => { if (item && item.id) serverRunning.set(item.id, Boolean(item.running)); });
  }
  function currentThreadBusy() {
    if (streaming || agentsRows.some((row) => agentLive(row.status))) return true;
    if (agentsLoadedFor === currentId) return false;
    return serverRunning.get(currentId) === true;
  }

  function syncCurrentThreadBusy() {
    const current = threadById(currentId);
    const busy = currentThreadBusy();
    if (current && current.running !== busy) {
      current.running = busy;
      renderHistory();
    }
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
    const headingBusy = historyTab === "projects" ? projectBusy : chatBusy;
    setSpinVisible(root.querySelector("[data-run-spin='heading']"), headingBusy, "heading");
    setSpinVisible(root.querySelector("[data-run-spin='chats']"), chatBusy, "chats");
    setSpinVisible(root.querySelector("[data-run-spin='projects']"), projectBusy, "projects");
    root.querySelectorAll("[data-history-tab]").forEach((btn) => {
      const tab = btn.getAttribute("data-history-tab");
      const busy = tab === "projects" ? projectBusy : chatBusy;
      btn.classList.toggle("is-busy", busy);
      btn.setAttribute("aria-busy", busy ? "true" : "false");
    });
    root.querySelectorAll("[data-run-spin]").forEach((el) => {
      if (!el.hidden) fillSpinEl(el, el.getAttribute("data-run-spin") || "run", false);
    });
    const any = chatBusy || projectBusy;
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
    if (mins < 60) return mins + "m";
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

  function pinnedFirst(a, b) {
    return (Number(a.pin_order) || 0) - (Number(b.pin_order) || 0);
  }

  // Pinned rows leave the timeline for their own section at the top, in the
  // order drag-and-drop wrote. Everything else keeps the running-first order.
  function splitPinned(items) {
    return {
      pinned: items.filter((item) => item.pinned).sort(pinnedFirst),
      rest: items.filter((item) => !item.pinned),
    };
  }

  function historyIcons() {
    return {
      folder:
        '<svg class="chat-history-glyph" viewBox="0 0 16 16" width="13" height="13" aria-hidden="true"><path d="M2.6 4.7h3.2l1.1 1.4H13.4v6.4H2.6z" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"/></svg>',
      chat:
        '<svg class="chat-history-glyph" viewBox="0 0 16 16" width="13" height="13" aria-hidden="true"><path d="M3.2 3.6h9.6v7.2H6.4L3.2 13.4z" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"/></svg>',
    };
  }

  // A project's folder in its own color, or its logo. Falls back to the
  // plain glyph if the shared helper failed to load.
  function projectIcon(project, size) {
    if (!window.FuseProjects) return historyIcons().folder;
    return window.FuseProjects.icon(project || {}, { size: size || 13, className: "chat-history-glyph" });
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
    const unread = Boolean(item.unread) && item.id !== currentId;
    let sessionTitle = item.title || "New chat";
    if (project && item.project_name && sessionTitle === item.project_name) sessionTitle = "New chat";
    const title = escapeHtml(sessionTitle);
    const snip = item.snippet ? `<span class="chat-history-snip">${escapeHtml(item.snippet)}</span>` : "";
    const chip = searchingChip(item);
    const meta =
      project && !grouped ? projectMeta(item.project_name || "Project", item.host_alias) : "";
    const lead =
      (running ? runSpinMarkup("h-" + item.id) : ageChip(item)) +
      (project ? projectIcon({ color: item.project_color, logo_url: item.project_logo_url }) : "");
    const groupedClass = project && grouped ? " is-grouped" : "";
    const runningClass = running ? " is-running" : "";
    const busy = running ? ' aria-busy="true"' : "";
    const working = running ? '<span class="visually-hidden">Working</span>' : "";
    const twoLine = Boolean(item.snippet || meta);
    const scheduledClass = item.schedule_id ? " is-task" : "";
    const pinClass = item.pinned ? " is-pinned" : "";
    const unreadClass = unread ? " is-unread" : "";
    const dot = unread ? '<span class="chat-history-unread-dot" title="New activity"></span>' : "";
    return `<div class="chat-history-item${on}${twoLine ? " has-snip" : ""}${project ? " is-project" : ""}${groupedClass}${runningClass}${scheduledClass}${pinClass}${unreadClass}" data-thread="${escapeHtml(String(item.id))}"${busy}><button type="button" class="open" title="${title}">${working}<span class="chat-history-title">${dot}${lead}<span class="chat-history-copy"><span class="chat-history-name">${title}</span>${meta}${snip}</span></span>${chip}</button><button type="button" class="more" aria-label="Chat actions">⋯</button></div>`;
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
    const chip = item.archived
      ? '<span class="chat-history-chip">Archived</span>'
      : historyQuery.trim() ? '<span class="chat-history-chip">Project</span>' : "";
    const threadAttr = item.thread_id ? ` data-thread="${escapeHtml(String(item.thread_id))}"` : "";
    const lead =
      (running ? runSpinMarkup("p-" + item.host_id + "-" + item.id) : ageChip(item)) +
      projectIcon(item);
    const runningClass = running ? " is-running" : "";
    const busy = running ? ' aria-busy="true"' : "";
    const working = running ? '<span class="visually-hidden">Working</span>' : "";
    const twoLine = Boolean(item.remote_path);
    return `<div class="chat-history-item is-project${on}${twoLine ? " has-snip" : ""}${runningClass}" data-project="${item.host_id}:${item.id}"${threadAttr}${busy}><button type="button" class="open" title="${escapeHtml(name)}">${working}<span class="chat-history-title">${lead}<span class="chat-history-copy"><span class="chat-history-name">${escapeHtml(name)}</span>${path}</span></span>${chip}</button><button type="button" class="more" aria-label="Project actions">⋯</button></div>`;
  }

  function searchingChip(item) {
    const searching = Boolean(historyQuery.trim());
    if (!searching) return "";
    if (item.archived) return '<span class="chat-history-chip">Archived</span>';
    if (item.pinned) return '<span class="chat-history-chip">Pinned</span>';
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
      if (item.archived) return;
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

  function groupedProjectSessions(items) {
    const byKey = new Map();
    items.forEach((item) => {
      const key = projectKey(item.host_id, item.project_id);
      if (!byKey.has(key)) {
        byKey.set(key, {
          name: item.project_name || "Project",
          host: item.host_alias || "",
          icon: { color: item.project_color, logo_url: item.project_logo_url },
          items: [],
        });
      }
      byKey.get(key).items.push(item);
    });
    return [...byKey.values()];
  }

  function syncHistoryFooter() {
    // A repaint replaced the row nodes; put the open menu's flag back.
    if (popRowKey && historyList) {
      const [attr, key] = popRowKey;
      for (const el of historyList.querySelectorAll(".chat-history-item")) {
        if (el.getAttribute(attr) === key) { el.classList.add("is-menu"); break; }
      }
    }
    const footer = root.querySelector(".chat-history-footer");
    if (!footer) return;
    footer.hidden = false;
    syncRunSpinners();
  }

  function renderHistory() {
    if (!historyList) return;
    if (renaming) return;
    // A repaint would rebuild the nodes under an active pin drag; the drop
    // handler re-renders from the saved order.
    if (pinDrag && pinDrag.dragging) return;
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
    const archived = archivedForTab();
    if (archivedBtn) {
      archivedBtn.hidden = searching || (archived.length === 0 && archivedProjects().length === 0 && !showArchived);
      archivedBtn.classList.toggle("is-on", showArchived);
      archivedBtn.setAttribute("aria-pressed", String(showArchived));
      const label = archivedBtn.querySelector("[data-history-archived-label]");
      const count = archivedBtn.querySelector("[data-history-archived-count]");
      if (label) label.textContent = showArchived ? (historyTab === "projects" ? "Back to projects" : "Back to chats") : "Archived";
      if (count) count.textContent = showArchived ? "" : String(archived.length + archivedProjects().length);
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
      const shelved = archivedProjects();
      let html = "";
      if (shelved.length) {
        html += `<p class="chat-history-kicker">Projects</p>` + shelved.map(projectRow).join("");
        if (archived.length) html += `<p class="chat-history-kicker">Sessions</p>`;
      }
      html += archived.map((item) => historyRow(item)).join("");
      historyList.innerHTML = html ||
        `<p class="chat-history-empty">${historyTab === "projects" ? "No archived projects or sessions." : "No archived chats."}</p>`;
      syncHistoryFooter();
      return;
    }
    if (historyTab === "projects") {
      const sessions = projectSessionList();
      const { pinned, rest } = splitPinned(sessions);
      if (!sessions.length) {
        historyList.innerHTML =
          `<p class="chat-history-empty">No project sessions yet. Use New project to start one.</p>`;
        syncHistoryFooter();
        return;
      }
      let html = "";
      if (pinned.length) html += `<p class="chat-history-kicker">Pinned</p>` + pinned.map((item) => historyRow(item)).join("");
      if (projectSort === "project") {
        groupedProjectSessions(rest).forEach((group) => {
          html +=
            `<div class="chat-history-project-head">` +
            projectIcon(group.icon) +
            `<span class="chat-history-project">${escapeHtml(group.name)}</span>` +
            (group.host ? `<span class="chat-history-host-label">${escapeHtml(group.host)}</span>` : "") +
            `</div>`;
          html += group.items.map((item) => historyRow(item, true)).join("");
        });
      } else {
        html += rest.map((item) => historyRow(item)).join("");
      }
      historyList.innerHTML = html;
      syncHistoryFooter();
      return;
    }
    const items = filteredHistory();
    if (!items.length && !historyLoaded) {
      historyList.innerHTML = `<div class="chat-history-loading" aria-busy="true" aria-label="Loading chats">${"<span></span>".repeat(6)}</div>`;
    } else if (!items.length) {
      historyList.innerHTML = `<p class="chat-history-empty">No chats yet.</p>`;
    } else {
      const { pinned, rest } = splitPinned(items);
      historyList.innerHTML =
        (pinned.length ? `<p class="chat-history-kicker">Pinned</p>` + pinned.map((item) => historyRow(item)).join("") : "") +
        rest.map((item) => historyRow(item)).join("");
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
    const was = searchOpen;
    searchOpen = open;
    top.classList.toggle("is-searching", open);
    searchToggle.setAttribute("aria-expanded", String(open));
    if (open && !was) overlayPush("search");
    else if (!open && was) overlayDrop("search");
    if (open) {
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
    historyLoaded = true;
    threads = data.threads || [];
    archivedThreads = data.archived || [];
    noteServerRunning(threads);
    noteServerRunning(archivedThreads);
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

  function pickerHostById(id) {
    return pickerHosts.find((host) => String(host.id) === String(id));
  }

  async function openProjectPicker() {
    if (sending || !window.FuseProjects) return;
    const bound = sessionProject();
    const picked = await window.FuseProjects.openChooser({
      title: "Start a project session",
      current: bound ? { host_id: bound.host_id, project_id: bound.project_id } : null,
    });
    if (!picked) return;
    if (picked.created || picked.archived === false) await refreshThreads().catch(() => {});
    if (!requireLive()) return;
    if (historyMobile.matches) setHistoryOpen(false);
    await startProjectThread(picked.host_id, picked.id, picked);
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
        color: native.color || "",
        logo_url: native.logo_url || "",
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
        color: proj.color || "",
        logo_url: proj.logo_url || "",
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
        color: extra.color || "",
        logo_url: extra.logo_url || "",
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
    dismissRecaps(false);
    try {
      await flushPending();
      if (nav !== navigation) return;
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

  async function openThread(id, opts) {
    const gone = Boolean(opts && opts.gone);
    if (sending && !gone) return;
    const nav = ++navigation;
    loading = true;
    syncComposer();
    try {
    if (!gone) {
      // A deleted thread keeps nothing: no draft to stash and no save to wait for.
      drafts.set(currentId, input.value);
      await flushPending();
    }
    if (nav !== navigation) return;
    const expectedGeneration = ++generation;
    const current = () => expectedGeneration === generation && nav === navigation;
    // Long threads stream their newest messages first: paint those at once,
    // then merge the complete transcript through the snapshot reconcile path.
    let opened = false;
    const data = await fetchThreadSnapshot(id, (partial) => {
      if (opened || !current()) return;
      opened = true;
      showOpenedThread(partial);
    });
    if (!current()) return;
    if (!opened) showOpenedThread(data);
    else if (partialTranscript) acceptThread(data);
    if (window.FuseComposerSpell) {
      // Learning from every earlier prompt is not needed for the first paint.
      const prompts = messages.filter((m) => m.role === "user" && !m.auto).map((m) => m.content);
      whenIdle(() => {
        if (!current()) return;
        FuseComposerSpell.rebuildLearned(prompts);
        FuseComposerSpell.refresh();
      });
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

  function showOpenedThread(data) {
    if (!Number.isSafeInteger(data.event_cursor)) throw new Error("Thread snapshot is missing event_cursor.");
    setPartialTranscript(Boolean(data.partial));
    drafts.set(currentId, input.value);
    draftFiles.set(currentId, pendingAttachments);
    currentId = data.id;
    sessionId = "thread-" + data.id;
    const listed = resolveListedModel(data.model);
    if (listed) {
      modelId = listed;
      renderPickerButton();
    }
    contextMessages = Array.isArray(data.context_messages) ? data.context_messages.map((msg) => ({ ...msg, noActions: true })) : [];
    contextLive = Boolean(data.context_live);
    threadRecaps = visibleRecaps(data);
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
      endedAt: typeof msg.endedAt === "string" ? msg.endedAt : "",
      stopped: Boolean(msg.stopped),
      attachments: Array.isArray(msg.attachments) ? msg.attachments : [],
      auto: Boolean(msg.auto),
    }));
    serverRevision = data.revision;
    acceptPermThread(data);
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
    pendingAttachments = draftFiles.get(currentId) || [];
    renderAttachList();
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
  }

  // While only the newest messages of a streamed snapshot are loaded, the
  // transcript is display-only: a save would replace the whole thread with
  // the tail. acceptThread clears it when the complete snapshot lands.
  function setPartialTranscript(on) {
    partialTranscript = on;
    root.toggleAttribute("data-transcript-partial", on);
  }

  function whenIdle(fn) {
    if (typeof window.requestIdleCallback === "function") window.requestIdleCallback(fn, { timeout: 2000 });
    else window.setTimeout(fn, 200);
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

  function sidebarProject(hostId, projectId) {
    return projects.find((item) => Number(item.host_id) === Number(hostId) && Number(item.id) === Number(projectId));
  }

  function boundTo(hostId, projectId) {
    const bound = sessionProject();
    return Boolean(bound && Number(bound.host_id) === Number(hostId) && Number(bound.project_id) === Number(projectId));
  }

  async function editProject(hostId, projectId) {
    if (!window.FuseProjects) return;
    const item = sidebarProject(hostId, projectId);
    const saved = await window.FuseProjects.openEditor({
      hostId,
      project: item
        ? { ...item, host_id: Number(hostId) }
        : { id: Number(projectId), host_id: Number(hostId), name: "Project", remote_path: "" },
    });
    if (!saved) return;
    if (pendingProject && boundTo(hostId, projectId)) {
      pendingProject = { ...pendingProject, project_name: saved.name, remote_path: saved.remote_path,
        permission_mode: saved.permission_mode, color: saved.color, logo_url: saved.logo_url };
    }
    await refreshThreads();
  }

  function projectMenu(anchor, key, at) {
    const [hostId, projectId] = String(key || "").split(":");
    const item = sidebarProject(hostId, projectId);
    if (!item || !requireLive()) return;
    const items = [
      {
        label: "New session",
        run: async () => {
          if (historyMobile.matches) setHistoryOpen(false);
          await startProjectThread(hostId, projectId, item);
        },
      },
      { label: "Edit project", run: () => editProject(hostId, projectId) },
      {
        label: item.archived ? "Unarchive project" : "Archive project",
        run: async () => {
          await api(`/hosts/${hostId}/projects/${projectId}`, {
            method: "PATCH",
            body: JSON.stringify({ archived: !item.archived }),
          });
          if (!item.archived && boundTo(hostId, projectId)) clearChatState({ keepList: true });
          await refreshThreads();
        },
      },
      {
        label: "Delete project",
        danger: true,
        run: async () => {
          const n = Number(item.sessions || 0);
          const what = n ? ` and its ${n === 1 ? "session" : n + " sessions"}` : "";
          const ok = await askConfirm(
            `Delete project "${item.name || "Project"}"${what}? The folder on ${item.host_alias || "the machine"} is not touched. This cannot be undone.`,
            { confirmLabel: "Delete", danger: true }
          );
          if (!ok) return;
          const wasBound = boundTo(hostId, projectId);
          await api(`/hosts/${hostId}/projects/${projectId}?sessions=delete`, { method: "DELETE" });
          if (wasBound) clearChatState({ keepList: true });
          await refreshThreads();
        },
      },
    ];
    showPop(anchor, items, at);
  }

  async function moveToProject(id) {
    const thread = threadById(id);
    if (!thread || !window.FuseProjects) return;
    const picked = await window.FuseProjects.openChooser({
      title: "Move to project",
      pickLabel: "Move here",
      current: thread.kind === "project" && thread.project_id != null
        ? { host_id: thread.host_id, project_id: thread.project_id } : null,
    });
    if (!picked || !requireLive()) return;
    if (thread.kind === "project" && Number(thread.host_id) === Number(picked.host_id)
        && Number(thread.project_id) === Number(picked.id)) return;
    const nav = navigation;
    await flushPending();
    const latest = threadById(id) || thread;
    const updated = await api("/chat/threads/" + id + "/project", {
      method: "POST",
      body: JSON.stringify({
        host_id: picked.host_id,
        project_id: picked.id,
        expected_revision: latest.revision,
        command_id: newSession(),
      }),
    });
    if (id === currentId) serverRevision = Math.max(serverRevision, updated.revision);
    upsertLocal(updated);
    renderHistory();
    setHint(`Moved to ${picked.name || "the project"}. The next turn runs in ${picked.remote_path || "its folder"}.`);
    await refreshThreads();
    if (nav === navigation && id === currentId) {
      syncTitle();
      syncComposer();
      reconcileThread().catch(syncFailure);
    }
  }

  function threadMenu(anchor, id, at) {
    const thread = threadById(id);
    if (!thread) return;
    if (!requireLive()) return;
    const items = [
      {
        label: thread.pinned ? "Unpin" : "Pin to top",
        run: async () => {
          if (!requireLive()) return;
          const latest = threadById(id) || thread;
          const updated = await api("/chat/threads/" + id, {
            method: "PATCH",
            body: JSON.stringify({
              pinned: !latest.pinned,
              expected_revision: latest.revision,
              command_id: newSession(),
            }),
          });
          upsertLocal(updated);
          renderHistory();
        },
      },
      { label: "Rename", run: () => startRename(anchor.closest(".chat-history-item"), thread) },
      {
        label: "Fork",
        run: async () => {
          if (!requireLive()) return;
          const nav = navigation;
          await flushPending();
          if (nav !== navigation) return;
          const copy = await api("/chat/threads/" + id + "/fork", { method: "POST", body: JSON.stringify({ expected_revision: threadById(id)?.revision, command_id: newSession() }) });
          upsertLocal(copy);
          renderHistory();
          setHint("Forked. The copy runs on its own: new chat, subagent and command ids.");
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
          if (nav === navigation && currentId === id && updated.archived) clearChatState({ keepList: true });
          renderHistory();
          // Restoring a session also restores its archived project.
          if (!updated.archived && updated.kind === "project") refreshThreads().catch(() => {});
        },
      },
      { label: thread.kind === "project" ? "Move to another project" : "Move to project", run: () => moveToProject(id) },
      ...(thread.kind === "project" && thread.project_id != null
        ? [{ label: "Project settings", run: () => editProject(thread.host_id, thread.project_id) }]
        : []),
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
    showPop(anchor, items, at);
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

  async function undoConversation(times = 1) {
    if (runIsActive(activeRun) || sending) {
      setHint("Wait for the current reply to finish.", true);
      return;
    }
    if (loading || partialTranscript || !requireLive()) return;
    if (!messages.length && !undoStack.length) {
      setHint("Nothing to undo.");
      return;
    }
    const origin = currentId;
    const gen = generation;
    if (parseSlashCommand(input.value)?.name === "undo") {
      input.value = "";
      drafts.set(origin, "");
      resizeInput();
      syncEmptyComposer();
    }
    const draft = input.value;
    const draftVersion = composerVersion;
    let restored;
    sending = true;
    syncComposer();
    try {
      await flushPending();
      for (let i = 0; i < times; i += 1) {
        if (gen !== generation || origin !== currentId || runIsActive(activeRun)) break;
        const current = snapshotMessages();
        const stacked = undoStack.length > 0;
        const previous = stacked ? undoStack.at(-1) : dropLastTurn(current);
        if (!stacked && previous.length === current.length) break;
        const retained = new Set(previous.map(msg => msg.id));
        const removed = current.findLast(msg => msg.role === "user" && !msg.auto && !retained.has(msg.id));
        const payload = { messages: previous, expected_revision: serverRevision, command_id: newSession() };
        const task = saveChain.then(() => api("/chat/threads/" + origin, {
          method: "PATCH",
          body: JSON.stringify(payload),
        }));
        saveChain = task.catch(() => {});
        const updated = await task;
        if (gen !== generation || origin !== currentId) break;
        if (stacked) undoStack.pop();
        redoStack.push(current);
        saveHistoryStacks();
        acceptThread(updated);
        recountLocalContext();
        if (removed) restored = removed.content || "";
        setHint(previous.length ? "Undid last turn." : "Conversation cleared.");
      }
    } catch (error) {
      if (gen === generation && origin === currentId) {
        await reconcileThread().catch(() => {});
        if (isTransportError(error)) markDisconnected();
        showFailure("Undo was not confirmed", error);
      }
    } finally {
      sending = false;
      if (gen === generation && origin === currentId && restored !== undefined &&
          composerVersion === draftVersion && input.value === draft && !draft) {
        input.value = restored;
        drafts.set(origin, restored);
        closeSlash();
        if (window.FuseComposerSpell) {
          FuseComposerSpell.close();
          FuseComposerSpell.refresh();
        }
        resizeInput();
        syncEmptyComposer();
        syncContextMeter();
        input.focus();
        input.setSelectionRange(input.value.length, input.value.length);
      }
      syncComposer();
      syncStatus();
      if (gen === generation && !streaming) drainQueue();
    }
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
    { name: "recap", hint: "Summarize the latest work once it settles", needs: "messages" },
    { name: "permissions", hint: "Manage agent permissions and bypass" },
    { name: "init", hint: "Create or update AGENTS.md", needs: "project", args: true },
    { name: "compact", hint: "Compress conversation context", needs: "messages", args: true },
    { name: "thinking", hint: "Show or hide thinking in every view" },
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
    if (name === "undo") {
      const times = Math.max(1, Math.min(20, parseInt(rest, 10) || 1));
      return undoConversation(times);
    }
    input.value = "";
    resizeInput();
    syncComposer();
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
      setHint("/undo, /retry, /redo, /rewind, /title, /recap, /permissions, /compact, /thinking, /init. Skills: type / then a name. Manage on Skills.");
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
    if (name === "recap") { runRecap(); return; }
    if (name === "thinking") {
      const want = rest.trim().toLowerCase();
      setShowThinking(/^(on|show|shown|yes|true)$/.test(want) ? true : /^(off|hide|hidden|no|false)$/.test(want) ? false : !showThinking);
      setHint(showThinking ? "Thinking is shown." : "Thinking is hidden. The status line still says when the model is thinking.");
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
    // Only the final turn is retryable; anything earlier would drop later turns.
    if (index < messages.length - 1) return;
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
    const host = articleFor(messages.at(-2))?.querySelector(".chat-msg-actions");
    if (!host) return;
    const button = document.createElement("button");
    button.type = "button"; button.className = "chat-icon-btn";
    button.dataset.emptyRetry = last.id; button.title = "Retry"; button.setAttribute("aria-label", "Retry");
    button.innerHTML = ACTION_ICONS.retry;
    button.addEventListener("click", () => retryAssistant(messages.findIndex(msg => msg.id === last.id)));
    host.appendChild(button);
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

  function foldAutoCommandResults(rows) {
    rows.forEach((msg, index) => {
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
            const prev = rows[i];
            if (prev.role !== "assistant" || !Array.isArray(prev.tools)) continue;
            const hit = prev.tools.find((t) => t && t.name === name && String(executionIdentity(t)) === id);
            if (hit) return hit;
          }
        }
        return null;
      };
      results.forEach((result, i) => {
        const trace = findTrace(result.id);
        if (!trace) return;
        applyCommandResult(trace, result);
        if (trace.description && msg.autoSteps?.[i]) msg.autoSteps[i].description = trace.description;
      });
    });
  }

  // Console prints an ended background command or monitor as Claude Code's
  // notice: 'Background command "<description>" completed · 12s'.

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
    overlayPush("lightbox");
    requestAnimationFrame(() => {
      overlay.classList.add("is-open");
      close.focus();
    });
  }

  function closeLightbox() {
    if (!lightbox) return;
    const entry = lightbox;
    lightbox = null;
    overlayDrop("lightbox");
    document.removeEventListener("keydown", entry.onKey);
    entry.el.remove();
    if (entry.prevFocus && typeof entry.prevFocus.focus === "function") {
      try { entry.prevFocus.focus(); } catch (_) {}
    }
  }

  let activitySeq = 0;

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

  function lastCurrentTrace(groups) {
    const items = groups.flatMap((g) => g.items);
    for (let i = items.length - 1; i >= 0; i--) {
      // The todo list is bookkeeping with its own section; it never names what
      // the run is doing.
      if (items[i].trace.name !== "todo_write" && toolIsCurrent(items[i].trace)) return items[i].trace;
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


  // The verb carries the shimmer and animates in when it changes; the console
  // meta (clock and token counter) ticks beside it without replaying that.

  // Console docks (main and subagent) share one glyph that blooms from a dot
  // to a full star and back. The frame index is global so both stay in step.


  let consoleSpinTimer = 0;
  let consoleSpinFrame = 0;

  // Every glyph that steps: the docks, the latest pill, and the sidebar's
  // run spinners (thread rows, Chats/Projects tabs, heading and title).

  function runForMessage(msg) {
    if (!msg) return null;
    if (activeRun && msg.id === activeRun.message_id) return activeRun;
    if (agentRun && msg.id === agentRun.message_id) return agentRun;
    return null;
  }

  // Work time, not wall time. The server's elapsedMs sums only the legs a run
  // actually executed (a stop, restart or resume never adds the pause), so
  // the clock anchors to it and ticks locally only while that run executes:
  // frozen while queued, re-anchored on every new leg and every checkpoint.
  function liveElapsedSeconds(msg, live) {
    const stored = Number(msg.elapsedMs);
    const known = msg.elapsedMs != null && Number.isFinite(stored) && stored >= 0;
    const run = runForMessage(msg);
    if (!live || (run && run.status === "queued")) {
      delete msg._liveOrigin;
      if (!live) return known ? stored / 1000 : null;
      return known ? stored / 1000 : 0;
    }
    const leg = run ? `${run.id}:${run.resume_count || 0}` : "";
    if (!msg._liveOrigin || msg._elapsedObserved !== msg.elapsedMs || msg._liveLeg !== leg) {
      msg._elapsedObserved = msg.elapsedMs;
      msg._liveLeg = leg;
      if (known) msg._liveOrigin = Date.now() - stored;
      else {
        // First leg before its first checkpoint. Only a root turn's
        // created_at marks when this message began; a child row keeps its
        // original spawn time across resumes.
        const started = run && run === activeRun ? Date.parse(run.created_at || "") : NaN;
        msg._liveOrigin = Number.isFinite(started) ? Math.min(Date.now(), started) : Date.now();
      }
    }
    return Math.max(0, (Date.now() - msg._liveOrigin) / 1000);
  }

  // Claude Code's spinner verbs (2.1.287). It picks one when its spinner
  // starts; Fuse keeps one per turn so the word holds across repaints.


  // The spinner's word: one of Claude Code's verbs, fixed per turn. It only
  // says the run is alive; the todo list has its own section under it.


  // Claude Code's thinking status: "thinking", deepening at 10/20/30/45s,
  // then "thought for 4s" for 2s once the thought ends.


  // What the run is doing, in the spinner's parentheses. Claude Code names
  // only thinking there; Fuse also names the call that is running.

  // Claude Code's status row: "✻ Cogitating… (1m 3s · ↓ 1.2k tokens · thinking)".
  // Main run by default; the subagent dock passes its own meter and clock.

  function stopLiveClock() {
    if (liveClockTimer) {
      window.clearInterval(liveClockTimer);
      liveClockTimer = 0;
    }
  }

  // The open subagent pane keeps its own clocks; they tick only while it shows
  // a live child, and the timer stops once neither transcript needs it.
  function agentClockLive() {
    return Boolean(agentsOpen && agentViewId && agentRun && agentLive(agentRun.status) && agentBodyEl);
  }

  function startLiveClock() {
    if (liveClockTimer) return;
    liveClockTimer = window.setInterval(() => {
      const child = agentClockLive();
      if (!streaming && !child && !workClocksLive()) {
        stopLiveClock();
        return;
      }
      if (streaming) {
        const msg = activeAssistant();
        const article = articleFor(msg);
        if (msg && msg.role === "assistant" && article && article.classList.contains("is-assistant")) {
          syncActivityElapsed(article, msg, true);
          syncActivitySpin(article, msg, true);
          syncConsoleClocks(article);
          syncLiveDock();
        }
      }
      if ((child)) {
        syncConsoleClocks(agentBodyEl);
        syncAgentLiveDock();
      }
      if (!streaming) syncWorkClocks();
    }, 250);
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

  // —— The todo list ——
  // todo_write never prints as a transcript row. The model re-sends the whole
  // list on every call, so a list is the newest successful call in ONE message:
  // the main chat's section reads the thread's own messages, a subagent's reads
  // that child's message. Nothing is looked up by tool id across messages (ids
  // restart in every run: a parent and a child can both own "ts_0_0"), so one
  // agent's list can never show up in, or be updated by, another's.
  const todoClosed = (item) => item.status === "completed" || item.status === "cancelled";
  const todoSettled = (items) => items.length > 0 && items.every(todoClosed);
  const todoLanded = (trace) => Boolean(trace && trace.name === "todo_write" && trace.status !== "error" && !trace.error && Array.isArray(trace.todos));

  function latestTodoState(msg) {
    const traces = Array.isArray(msg && msg.tools) ? msg.tools : [];
    for (let i = traces.length - 1; i >= 0; i -= 1) {
      const trace = traces[i];
      if (!todoLanded(trace)) continue;
      const state = { key: `${msg.id || ""}:${trace.id || i}`, items: normalizeTodos(trace.todos) };
      // A subagent's compaction prunes the traces it already acted on; its
      // list is still the one the model is working from.
      if (msg.noActions) msg._todoLast = state;
      return state;
    }
    return msg && msg.noActions && msg._todoLast || null;
  }

  function todoSettledText(items) {
    const done = items.filter((item) => item.status === "completed").length;
    const cancelled = items.length - done;
    return `Todos · ${done}/${items.length} done${cancelled ? ` · ${cancelled} cancelled` : ""}`;
  }

  // The call that takes a list from open to settled leaves one line in the
  // transcript: tool id -> its text. Every other todo_write prints nothing.
  function todoSettleLines(msg) {
    const lines = new Map();
    let before = false;
    (Array.isArray(msg && msg.tools) ? msg.tools : []).forEach((trace) => {
      if (!todoLanded(trace)) return;
      const items = normalizeTodos(trace.todos);
      const settled = todoSettled(items);
      if (settled && !before) lines.set(trace.id, todoSettledText(items));
      before = settled;
    });
    return lines;
  }

  const TODO_ROWS = 8;
  const TODO_LINGER_MS = 1500;
  const STORE_TODO_DISMISSED = "fuse.chat.todos.dismissed.v1";
  let todoDismissedKeys = null;

  function todoDismissed(key) {
    if (!todoDismissedKeys) {
      try { todoDismissedKeys = new Set(JSON.parse(localStorage.getItem(STORE_TODO_DISMISSED) || "[]")); } catch { todoDismissedKeys = new Set(); }
    }
    return todoDismissedKeys.has(key);
  }

  function dismissTodos(key) {
    todoDismissed(key);
    todoDismissedKeys.add(key);
    try { localStorage.setItem(STORE_TODO_DISMISSED, JSON.stringify([...todoDismissedKeys].slice(-60))); } catch {}
  }

  // The checklist, as the bottom section prints it. Past TODO_ROWS items the
  // finished ones fold into a count first, then the far end of the open ones;
  // the item in progress always stays, and the head opens the full list.


  const todoSections = {
    main: { el: root.querySelector("[data-live-todos]"), sig: "", expanded: false, shownKey: "", lingerUntil: 0, timer: 0, key: "" },
    agent: { el: root.querySelector("[data-agent-live-todos]"), sig: "", expanded: false, shownKey: "", lingerUntil: 0, timer: 0, key: "" },
  };

  // An unfinished list stays until it is dismissed. A finished one shows only
  // while something is still running on the message that owns it, then holds
  // for a beat so the last check lands before the section goes.
  function todoShown(sec, candidate, active) {
    const state = candidate && candidate.state;
    if (!state || !state.items.length || todoDismissed(state.key)) {
      sec.shownKey = "";
      sec.lingerUntil = 0;
      return false;
    }
    if (!todoSettled(state.items) || (active && candidate.latest)) {
      sec.shownKey = state.key;
      sec.lingerUntil = 0;
      return true;
    }
    if (sec.shownKey === state.key) {
      const now = Date.now();
      if (!sec.lingerUntil) sec.lingerUntil = now + TODO_LINGER_MS;
      if (now < sec.lingerUntil) {
        clearTimeout(sec.timer);
        sec.timer = setTimeout(scheduleTodoSync, sec.lingerUntil - now + 20);
        return true;
      }
    }
    sec.shownKey = "";
    sec.lingerUntil = 0;
    return false;
  }

  function paintTodoSection(sec, candidate, active, afterResize) {
    const el = sec.el;
    if (!el) return;
    const state = candidate && candidate.state;
    if (!todoShown(sec, candidate, active)) {
      if (!el.hidden) {
        el.hidden = true;
        el.replaceChildren();
        sec.sig = "";
        sec.key = "";
        afterResize();
      }
      return;
    }
    const sig = `${state.key}|${active}|${sec.expanded}|${JSON.stringify(state.items)}`;
    sec.key = state.key;
    if (sig === sec.sig && !el.hidden) return;
    sec.sig = sig;
    el.innerHTML = todoPanelHtml(state, { active, expanded: sec.expanded });
    el.hidden = false;
    afterResize();
  }

  // The newest list in the thread, and whether its message is the thread's last
  // reply (an old finished list never reappears under a later turn).
  function mainTodoCandidate() {
    let latest = true;
    for (let i = messages.length - 1; i >= 0; i -= 1) {
      const msg = messages[i];
      if (!msg || msg.role !== "assistant") continue;
      const state = latestTodoState(msg);
      if (state) return { state, latest };
      latest = false;
    }
    return null;
  }

  function mainTodoActive() {
    if (streaming || runIsActive(activeRun)) return true;
    for (const row of agentLifecycle.values()) if (agentLive(row.status)) return true;
    return agentsRows.some((row) => agentLive(row.status));
  }

  function syncTodoSections() {
    paintTodoSection(todoSections.main, mainTodoCandidate(), mainTodoActive(), () => {
      followLiveDockSlide();
      if (stickToBottom) scrollThread();
      syncLatest();
    });
    const child = agentViewId && agentRun && agentMsgState ? latestTodoState(agentMsgState) : null;
    paintTodoSection(todoSections.agent, child && { state: child, latest: true }, Boolean(agentRun && agentLive(agentRun.status)), () => {
      if (agentStick && agentLogEl) restoreAgentScroll(true, 0);
    });
  }

  let todoSyncQueued = false;
  function scheduleTodoSync() {
    if (todoSyncQueued) return;
    todoSyncQueued = true;
    requestAnimationFrame(() => {
      todoSyncQueued = false;
      syncTodoSections();
    });
  }

  function onTodoClick(event) {
    const kind = Object.keys(todoSections).find((name) => todoSections[name].el && todoSections[name].el.contains(event.target));
    if (!kind) return;
    const sec = todoSections[kind];
    if (event.target.closest("[data-todo-dismiss]")) {
      dismissTodos(sec.key);
      sec.sig = "";
      scheduleTodoSync();
    } else if (event.target.closest("[data-todo-toggle]")) {
      sec.expanded = !sec.expanded;
      scheduleTodoSync();
    }
  }
  Object.values(todoSections).forEach((sec) => sec.el && sec.el.addEventListener("click", onTodoClick));

  function humanMs(ms) {
    const n = Number(ms);
    if (!Number.isFinite(n) || n < 0) return "";
    const rounded = Math.round(n);
    if (rounded < 1000) return `${rounded}ms`;
    const secs = rounded / 1000;
    if (secs < 60) return `${secs < 10 ? secs.toFixed(1) : Math.round(secs)}s`;
    return formatTurnDuration(n);
  }




  root.addEventListener("error", (event) => {
    const image = event.target;
    if (image.tagName !== "IMG" || !image.closest(".chat-tool-image")) return;
    const button = image.closest("[data-image-preview]");
    if (button) {
      const note = document.createElement("span");
      note.className = "chat-tool-truncated";
      note.textContent = "Image preview unavailable. The saved image may no longer be available.";
      button.replaceWith(note);
    }
  }, true);

  // opts.verbose (Console): a row printed inside an opened summary line —
  // full command and output, no disclosure of its own.


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
    const group = row.parentElement?.closest(".is-stack, .is-console-group");
    if (group) {
      const toggle = group.querySelector(":scope > .chat-step-main > .chat-step-head > [data-step-toggle]");
      if (toggle?.getAttribute("aria-expanded") !== "true") toggle?.click();
    }
    const toggle = row.querySelector(":scope > .chat-step-main > .chat-step-head > [data-step-toggle]");
    if (toggle?.getAttribute("aria-expanded") !== "true") toggle?.click();
  }
  // Collapsed tool rows render an empty details body carrying a token for
  // their trace (see toolStepHtml). Weak references keep the registry from
  // pinning traces of threads that are no longer shown.

  function setToolReveal(reveal, open, fromHeight) {
    if (!reveal) return;
    if (open) void 0;
    const owner = reveal.closest(".chat-turn");
    const start = fromHeight ?? reveal.getBoundingClientRect().height;
    reveal._disclosureAnimation?.cancel();
    reveal.classList.toggle("is-open", open);
    if (reduceMotion.matches) return;
    reveal.style.display = "block";
    reveal.style.overflow = "hidden";
    const end = open ? reveal.scrollHeight : 0;
    const animation = reveal.animate([{ height: `${start}px`, opacity: open ? 0.3 : 1 }, { height: `${end}px`, opacity: open ? 1 : 0 }],
      { duration: (MOTION.fast), easing: MOTION.snap });
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
      const section = copy.closest(".chat-tool-section");
      const lines = section ? [...section.querySelectorAll(".chat-console-nbody")] : [];
      let text = copy.hasAttribute("data-copy-value") ? copy.getAttribute("data-copy-value")
        : lines.length ? lines.map((line) => line.textContent).join("\n") : "";
      if (!text && section?.querySelector("pre")) {
        const pre = section.querySelector("pre").cloneNode(true);
        pre.querySelector(".chat-tool-cmd")?.remove();
        text = pre.textContent.replace(/^\n/, "");
      }
      const idle = copy.textContent;
      try { await navigator.clipboard.writeText(text); copy.textContent = "Copied"; setTimeout(() => { if (copy.isConnected) copy.textContent = idle; }, 1400); }
      catch { copy.textContent = "Select to copy"; }
    }
  });

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
    return [...log.children].find((el) => el.dataset.messageId === msg.id) || null;
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
  let permsFilter = "";

  function acceptPermThread(data) {
    if (data && Object.prototype.hasOwnProperty.call(data, "bypass_permissions")) {
      threadBypass = Boolean(data.bypass_permissions);
    }
    if (data && Object.prototype.hasOwnProperty.call(data, "auto_permissions")) {
      threadAuto = Boolean(data.auto_permissions);
    }
    syncPermChip();
    clearMainAgentPermCards();
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
    paintPermsMode();
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
      spawn_subagent: "Start subagent in",
    })[tool] || (tool || "Action").replace(/_/g, " ");
  }

  function permGrantKind(rec) {
    if (rec.grant_kind === "file" || rec.grant_kind === "dir") return rec.grant_kind;
    if (rec.secret || rec.tool === "read_file" || rec.tool === "write_file" || rec.tool === "search_replace") return "file";
    if (rec.tool === "list_dir" || rec.tool === "grep" || rec.tool === "glob") return "dir";
    return rec.tool === "run_terminal_command" || rec.tool === "spawn_subagent" ? "dir" : "";
  }

  function permGrantPath(rec) {
    return String(rec.grant_value || rec.path || rec.cwd || "").trim();
  }

  function permCardCopy(rec) {
    const kind = permGrantKind(rec);
    const path = permGrantPath(rec);
    const noun = kind === "file" ? "file" : "folder";
    const verb = rec.secret ? "Read secret" : permToolLabel(rec.tool);
    // Any non-trivial command asks even inside the project; only say
    // "outside" when the gate said so (reconnect-seeded cards carry no reasons).
    const shell = rec.tool === "run_terminal_command" || rec.tool === "monitor";
    const outside = (rec.reasons || []).some((r) => String(r).includes("outside"));
    const why = rec.secret ? "Secret-named file" : (shell && !outside ? "Command needs approval" : "Outside allowed folders");
    return {
      action: verb,
      noun,
      path,
      why,
      command: rec.tool === "run_terminal_command" ? String(rec.command || "").trim() : "",
      // The model's label for the command, above it as in Claude Code's dialog.
      description: shell ? String(rec.description || "").replace(/\s+/g, " ").trim() : "",
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
    return [rec.tool, rec.host, rec.path, rec.cwd, rec.command, rec.description || "", rec.requester, rec.secret ? "1" : "0", rec.grant_kind || "", rec.grant_value || "", (rec.reasons || []).join("|")].join("\0");
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

  // Subagent asks live in child runs, which can outlive the parent turn and
  // never appear in the parent snapshot; only the main agent's asks are
  // settled by a parent snapshot or terminal event. Child cards still expire
  // via expires_at or are resolved by their own agent.* permission updates.
  function permFromSubagent(rec) {
    const who = rec && (rec.requester_label || rec.requester);
    if (who && typeof who === "object") return who.role === "subagent";
    const text = String(who || "").trim();
    if (!text || text.toLowerCase() === "main agent") return false;
    try {
      const parsed = JSON.parse(text);
      if (parsed && typeof parsed === "object") return parsed.role === "subagent";
    } catch (_) {}
    return true;
  }

  function clearMainAgentPermCards() {
    for (const [id, rec] of permCards) if (!permFromSubagent(rec)) permCards.delete(id);
  }

  function sweepStuckPermCards() {
    clearMainAgentPermCards();
    renderPermQueue();
  }

  const PERMS_ICON_X = '<svg viewBox="0 0 16 16" width="12" height="12" aria-hidden="true"><path d="M4 4l8 8M12 4l-8 8" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>';
  const PERMS_ICON_LOCK = '<svg viewBox="0 0 16 16" width="12" height="12" aria-hidden="true"><rect x="3.5" y="7" width="9" height="6.5" rx="1.5" fill="none" stroke="currentColor" stroke-width="1.3"/><path d="M5.5 7V5.3a2.5 2.5 0 0 1 5 0V7" fill="none" stroke="currentColor" stroke-width="1.3"/></svg>';
  const PERMS_ICON_DIR = '<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><path d="M2 4.5c0-.6.4-1 1-1h3.2l1.4 1.5H13c.6 0 1 .4 1 1v6c0 .6-.4 1-1 1H3c-.6 0-1-.4-1-1z" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"/></svg>';
  const PERMS_ICON_FILE = '<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><path d="M4 2h5l3 3v9H4z" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"/><path d="M9 2v3h3" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"/></svg>';
  const PERMS_MODES = [
    { id: "ask", label: "Ask", hint: "Calls outside these directories wait for your answer." },
    { id: "auto", label: "Auto", hint: "Jev reviews each call and allows routine ones. Dangerous commands and secrets still ask." },
    { id: "bypass", label: "Bypass", hint: "No prompts. Read-only projects still apply." },
  ];
  const PERMS_TAGS = { temp: "temp", work: "workspace", session: "this chat", host: "always" };

  function permMode() {
    return threadBypass ? "bypass" : (threadAuto ? "auto" : "ask");
  }

  // The permissions menu mirrors the model picker: filter row on top, one
  // group per machine (the chat's own machine first), single-line rows, and
  // the mode control at the foot where the picker keeps thinking effort.
  function bindPermsMenu(menu) {
    const search = menu.querySelector("[data-perms-search]");
    menu.addEventListener("click", (event) => event.stopPropagation());
    menu.querySelector("[data-perms-close]")?.addEventListener("click", () => setPermsOpen(false));
    search.addEventListener("input", () => {
      permsFilter = search.value;
      paintPermsList();
    });
    menu.addEventListener("keydown", (event) => {
      if (event.key !== "Escape") return;
      event.preventDefault();
      event.stopPropagation();
      if (search.value) {
        search.value = "";
        permsFilter = "";
        paintPermsList();
        search.focus();
        return;
      }
      setPermsOpen(false);
      if (permChip) permChip.focus();
    });
    menu.querySelector("[data-perms-modes]").addEventListener("click", (event) => {
      const btn = event.target.closest("[data-perms-mode]");
      if (btn && !btn.disabled) setPermMode(btn.getAttribute("data-perms-mode"));
    });
    menu.querySelector("[data-perms-list]").addEventListener("click", (event) => {
      const btn = event.target.closest("[data-perm-revoke]");
      if (btn) revokePermGrant(btn);
    });
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
      '<div class="chat-picker-searchrow"><svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><circle cx="7" cy="7" r="4.4" fill="none" stroke="currentColor" stroke-width="1.5"/><path d="M10.4 10.4L13.8 13.8" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg><input type="search" class="chat-picker-search" data-perms-search placeholder="Filter machines and directories" aria-label="Filter machines and directories" autocomplete="off" spellcheck="false"><kbd class="chat-picker-kbd" aria-hidden="true">esc</kbd></div>' +
      '<div class="chat-perms-list" data-perms-list></div>' +
      '<div class="chat-perms-foot"><div class="chat-perms-mode"><p class="chat-perms-mode-label" aria-hidden="true">Mode</p><div class="chat-perms-modes" data-perms-modes role="radiogroup" aria-label="Permission mode">' +
      PERMS_MODES.map((m) => `<button type="button" role="radio" aria-checked="false" data-perms-mode="${m.id}">${m.label}</button>`).join("") +
      '</div></div><p class="chat-perms-hint" data-perms-hint></p></div>';
    bindPermsMenu(menu);
    // Phones get the model picker's full-screen sheet, portaled to the chat
    // root for the same reason (composer ancestors would contain it).
    attachSheetSwipe(menu, ".chat-picker-head", () => menu.classList.contains("is-sheet"), () => setPermsOpen(false));
    if (pickerSheetQuery.matches) root.appendChild(menu);
    else permHost.appendChild(menu);
    permsMenuEl = menu;
    paintPermsPanel();
  }

  function placePermsMenu() {
    placeComposerPop(permsMenuEl, permChip, 30 * 16, true);
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
      const search = permsMenuEl && permsMenuEl.querySelector("[data-perms-search]");
      if (search) search.value = "";
      permsFilter = "";
      if (permsMenuEl) permsMenuEl.hidden = false;
      paintPermsPanel();
      placePermsMenu();
      // Same rule as the model picker: focus would pop the phone keyboard.
      if (search && !pickerSheetQuery.matches) search.focus({ preventScroll: true });
      refreshPerms();
    } else {
      armOverlayGhost(permsMenuEl?.querySelector("[data-perms-close]"));
      overlayDrop("perms");
      if (permsMenuEl) permsMenuEl.hidden = true;
    }
    syncPermChip();
  }

  function permHostList() {
    return Array.isArray(threadPermData && threadPermData.hosts) ? threadPermData.hosts : [];
  }

  function paintPermsPanel() {
    paintPermsMode();
    paintPermsList();
  }

  function paintPermsMode() {
    if (!permsMenuEl) return;
    const mode = permMode();
    permsMenuEl.querySelectorAll("[data-perms-mode]").forEach((btn) => {
      const id = btn.getAttribute("data-perms-mode");
      const on = id === mode;
      btn.classList.toggle("is-on", on);
      btn.setAttribute("aria-checked", on ? "true" : "false");
      btn.disabled = id === "auto" && !autoAvailable && !on;
      btn.title = btn.disabled ? "Add an OpenRouter key on Sources to enable auto-approve." : "";
    });
    const hint = permsMenuEl.querySelector("[data-perms-hint]");
    if (hint) {
      const spec = PERMS_MODES.find((m) => m.id === mode) || PERMS_MODES[0];
      hint.textContent = spec.hint + (autoAvailable ? "" : " Auto needs an OpenRouter key on Sources.");
    }
  }

  // Middle-truncate paths: the leading directories give way first so the
  // last segment (the part that tells two grants apart) stays readable.
  function permPathHtml(path) {
    const trimmed = path.replace(/[\\/]+$/, "");
    const cut = Math.max(trimmed.lastIndexOf("/"), trimmed.lastIndexOf("\\"));
    const head = cut > 0 ? path.slice(0, cut + 1) : "";
    const tail = cut > 0 ? path.slice(cut + 1) : path;
    return (head ? `<span class="chat-perms-path-head">${escapeHtml(head)}</span>` : "") +
      `<span class="chat-perms-path-tail">${escapeHtml(tail)}</span>`;
  }

  function permRowHtml(row) {
    const action = row.grant
      ? `<button type="button" class="chat-perms-x" data-perm-revoke aria-label="Remove access to ${escapeHtml(row.path)}" title="Remove access">${PERMS_ICON_X}</button>`
      : `<span class="chat-perms-x is-fixed" role="img" aria-label="Built in" title="Built in: always allowed">${PERMS_ICON_LOCK}</span>`;
    return `<li class="chat-perms-row${row.grant ? "" : " is-fixed"}"${row.grant ? ` data-perm-grant="${Number(row.grant)}"` : ""}>` +
      `<span class="chat-perms-icon">${row.kind === "file" ? PERMS_ICON_FILE : PERMS_ICON_DIR}</span>` +
      `<span class="chat-perms-path" title="${escapeHtml(row.path)}">${permPathHtml(row.path)}</span>` +
      `<span class="chat-perms-tag is-${row.tag}">${PERMS_TAGS[row.tag]}</span>${action}</li>`;
  }

  function paintPermsList() {
    const listEl = permsMenuEl && permsMenuEl.querySelector("[data-perms-list]");
    if (!listEl) return;
    if (!threadPermData) {
      listEl.innerHTML = '<p class="chat-perms-empty">Loading…</p>';
      return;
    }
    const q = permsFilter.trim().toLowerCase();
    const threadHost = Number(threadPermData.thread_host_id);
    const hosts = [...permHostList()].sort((a, b) => (Number(b.id) === threadHost) - (Number(a.id) === threadHost));
    const groups = hosts.map((h) => {
      const name = String(h.host || "host");
      const rows = [];
      (h.roots || []).forEach((r) => {
        if (r && r.path) rows.push({ path: String(r.path), kind: "dir", tag: r.kind === "work" ? "work" : "temp" });
      });
      (h.grants || []).forEach((g) => {
        rows.push({ path: String(g.value || ""), kind: g.kind === "file" ? "file" : "dir", tag: g.scope === "host" ? "host" : "session", grant: g.id });
      });
      const shown = q && !name.toLowerCase().includes(q) ? rows.filter((r) => r.path.toLowerCase().includes(q)) : rows;
      if (!shown.length) return "";
      const granted = (h.grants || []).length;
      return `<section class="chat-perms-group"><p><span class="chat-perms-host">${escapeHtml(name)}</span>` +
        (Number(h.id) === threadHost ? '<span class="chat-perms-here">this chat</span>' : "") +
        `<span class="chat-perms-count">${granted ? `${granted} granted` : ""}</span></p>` +
        `<ul aria-label="${escapeHtml(name)}">${shown.map(permRowHtml).join("")}</ul></section>`;
    }).join("");
    listEl.innerHTML = groups || `<p class="chat-perms-empty">${q ? "Nothing matches." : "No machines yet."}</p>`;
  }

  async function revokePermGrant(btn) {
    const row = btn.closest("[data-perm-grant]");
    const gid = Number(row && row.getAttribute("data-perm-grant"));
    if (!gid || !threadPermData) return;
    // Drop the row at once; a failure reloads the real list.
    const neighbour = (row.nextElementSibling && row.nextElementSibling.matches("[data-perm-grant]") && row.nextElementSibling) ||
      (row.previousElementSibling && row.previousElementSibling.matches("[data-perm-grant]") && row.previousElementSibling);
    const nextId = neighbour && neighbour.getAttribute("data-perm-grant");
    threadPermData = {
      ...threadPermData,
      hosts: permHostList().map((h) => ({ ...h, grants: (h.grants || []).filter((g) => Number(g.id) !== gid) })),
    };
    paintPermsList();
    if (!pickerSheetQuery.matches && permsMenuEl) {
      const target = (nextId && permsMenuEl.querySelector(`[data-perm-grant="${nextId}"] [data-perm-revoke]`)) ||
        permsMenuEl.querySelector("[data-perms-search]");
      if (target) target.focus({ preventScroll: true });
    }
    try {
      await api(`/chat/permissions/grants/${encodeURIComponent(gid)}`, { method: "DELETE" });
    } catch (error) {
      if (error && error.status === 404) return;
      showFailure("Could not remove access", error);
      refreshPerms();
    }
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
      const listEl = permsMenuEl && permsMenuEl.querySelector("[data-perms-list]");
      if (listEl && !threadPermData) listEl.innerHTML = '<p class="chat-perms-empty">Could not load permissions.</p>';
    }
  }

  async function setPermMode(mode) {
    if (!currentId || mode === permMode()) return;
    if (mode === "auto" && !autoAvailable) return;
    const body = { expected_revision: serverRevision };
    if (mode === "bypass") body.bypass_permissions = true;
    else {
      if (threadBypass) body.bypass_permissions = false;
      if (threadAuto !== (mode === "auto")) body.auto_permissions = mode === "auto";
    }
    const prev = [threadBypass, threadAuto];
    threadBypass = mode === "bypass";
    if (mode !== "bypass") threadAuto = mode === "auto";
    syncPermChip();
    try {
      const data = await api(`/chat/threads/${encodeURIComponent(currentId)}`, {
        method: "PATCH",
        body: JSON.stringify(body),
      });
      if (data && Object.prototype.hasOwnProperty.call(data, "bypass_permissions")) threadBypass = Boolean(data.bypass_permissions);
      if (data && Object.prototype.hasOwnProperty.call(data, "auto_permissions")) threadAuto = Boolean(data.auto_permissions);
      if (data && data.revision != null) serverRevision = data.revision;
      if (threadBypass) {
        permCards.clear();
        renderPermQueue();
      }
    } catch (error) {
      [threadBypass, threadAuto] = prev;
      showFailure("Could not change permission mode", error);
    }
    syncPermChip();
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

  // The run's live phase: "model" (a round started), "calling" (the model is
  // writing a tool call, so the thought before it has finished) or "tools".
  const SETTLED_PHASES = new Set(["calling", "tools"]);
  function runPhase(msg) {
    return (msg?.id === activeRun?.message_id ? activeRun?.activity : msg?._activity)?.phase || "";
  }



  const transcriptDomOptions = {
    island: node => node.hasAttribute("data-reason-preview") || node.hasAttribute("data-content-block") || node.dataset.step?.startsWith("think-"),
    dispose: node => { if (node.nodeType === 1) disposeThinking(node); },
  };

  const historyObserver = typeof IntersectionObserver === "function" ? new IntersectionObserver(entries => {
    for (const entry of entries) if (entry.isIntersecting) hydrateHistoricalMessage(entry.target);
  }, { root: thread, rootMargin: "600px 0px" }) : null;

  function hydrateHistoricalMessage(shell) {
    if (!shell.isConnected || !shell.hasAttribute("data-deferred-message")) { historyObserver?.unobserve(shell); return; }
    const shown = viewMessages();
    const index = shown.findIndex(msg => msg.id === shell.dataset.messageId);
    if (index < 0) return;
    historyObserver?.unobserve(shell);
    const viewport = captureThreadViewport();
    const previousHeight = shell.offsetHeight;
    const article = renderAssistant(shown[index], false);
    article.dataset.messageId = shown[index].id;
    shell.replaceWith(article);
    paintRecaps(article);
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
    if (msg.role === "marker" || msg.kind === "compaction" || msg.compaction) return agentSeedArticle(msg, index);
    if (historyObserver && msg.role === "assistant" && !live && index < viewMessages().length - 8 &&
        (msg.content || msg.reasoning || msg.tools?.length || msg.permissions?.length || visibleAssistantError(msg))) {
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

  // A reply that arrives in the open thread settles in (see .is-arriving).
  function markArriving(article) {
    article.classList.add("is-arriving");
    const settle = (event) => {
      if (event.target !== article) return;
      article.classList.remove("is-arriving");
      article.removeEventListener("animationend", settle);
    };
    article.addEventListener("animationend", settle);
  }

  function visibleRecaps(data) {
    const dismissed = dismissedRecapAnchors.get(data.id);
    return (Array.isArray(data.recaps) ? data.recaps : []).filter(row => !dismissed?.has(String(row.anchor_id)));
  }

  function dismissRecaps(remember = true) {
    if (remember && currentId && threadRecaps.length) {
      const dismissed = dismissedRecapAnchors.get(currentId) || new Set();
      threadRecaps.forEach(row => dismissed.add(String(row.anchor_id)));
      dismissedRecapAnchors.set(currentId, dismissed);
    }
    threadRecaps = [];
    log.querySelectorAll(".chat-recap").forEach(node => node.remove());
  }

  function paintRecaps(target = null) {
    const byAnchor = new Map(threadRecaps.map(row => [String(row.anchor_id), String(row.text || "")]));
    for (const article of target ? [target] : log.children) {
      const text = byAnchor.get(article.dataset.messageId);
      let node = article.querySelector(":scope > .chat-recap");
      if (!text || article.hasAttribute("data-deferred-message")) { node?.remove(); continue; }
      if (!node) {
        node = document.createElement("p");
        node.className = "chat-recap";
        node.setAttribute("aria-label", "Work recap");
        article.appendChild(node);
      }
      if (node.dataset.text !== text) {
        const icon = document.createElement("span");
        icon.className = "chat-recap-icon";
        icon.setAttribute("aria-hidden", "true");
        icon.innerHTML = '<svg viewBox="0 0 16 16" width="16" height="16" focusable="false" aria-hidden="true"><rect x="3" y="2" width="10" height="12" rx="1.5" fill="none" stroke="currentColor" stroke-width="1.25"/><path d="M5.5 5.5h5M5.5 8h5M5.5 10.5h3" fill="none" stroke="currentColor" stroke-width="1.25" stroke-linecap="round"/></svg>';
        const copy = document.createElement("span");
        copy.className = "chat-recap-copy";
        const label = Object.assign(document.createElement("span"), { className: "chat-recap-label", textContent: "Recap: " });
        const summary = Object.assign(document.createElement("em"), { className: "chat-recap-text", textContent: text });
        copy.append(label, summary);
        node.replaceChildren(icon, copy);
        node.dataset.text = text;
      }
    }
  }

  function paint() {
    if (toolGestureOwner && log.contains(toolGestureOwner)) { pendingFullPaint = true; return; }
    foldAutoCommandResults(messages);
    reconcileBgTraces([...completedJobs.values()], false);
    const viewport = captureThreadViewport();
    const existing = new Map(log._threadId === currentId ? [...log.children].map(el => [el.dataset.messageId, el]) : []);
    const keep = new Set();
    let cursor = log.firstElementChild;
    viewMessages().forEach((msg, index) => {
      const live = msg.role === "assistant" && messageRunLive(msg);
      const signature = msg.role === "user" ? JSON.stringify([index, "console", live, editingIndex === index, msg.content, msg.attachments, msg.auto, msg.autoSteps, msg.noActions]) : "";
      let article = existing.get(msg.id);
      const reusable = article && article.classList.contains(msg.role === "assistant" ? "is-assistant" : "is-user") &&
        (msg.role === "assistant" || article._userSignature === signature) && !(live && article.hasAttribute("data-deferred-message"));
      if (!reusable) {
        const old = article;
        article = renderTranscriptMessage(msg, index, live);
        if (!old && !cursor && msg.role === "assistant" && log._threadId === currentId && log.childElementCount) markArriving(article);
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
        mountActions(article, index);
      }
    });
    for (const article of [...log.children]) if (!keep.has(article)) { disposeThinking(article); historyObserver?.unobserve(article); article.remove(); }
    log._threadId = currentId;
    paintRecaps();
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
    paintRecaps(last);
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

  let pickerPinId = "";
  function filteredModels() {
    const q = pickerFilter.trim().toLowerCase();
    const pinned = modelsById.get(pickerPinId);
    let list = allModels;
    if (pinned) {
      const same = allModels.filter((m) => m.type === pinned.type && m.id !== pinned.id);
      const rest = allModels.filter((m) => m.type !== pinned.type);
      list = [pinned, ...same, ...rest];
    }
    if (!q) return list;
    return list.filter(
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
          // The provider mark sits once on the group label; rows carry the
          // name and context window only, so the list stays dense.
          `<div class="chat-picker-group"><p>${providerLogo(group.type)}<span>${escapeHtml(group.title)}</span></p>${group.items
            .map(({ model, index }) => {
              const selected = model.id === modelId;
              const ctx = model.contextWindow ? formatTokens(model.contextWindow) : "";
              const row =
                `<button type="button" role="option" data-id="${escapeHtml(model.id)}" aria-selected="${
                  selected ? "true" : "false"
                }" class="${selected ? "is-selected" : ""}${index === pickerIndex ? " is-active" : ""}">` +
                `<span>${escapeHtml(model.name)}</span>` +
                (model.image ? `<i class="chat-picker-image" title="Reads images" aria-label="Reads images">${IMAGE_ICON}</i>` : "") +
                (ctx ? `<small class="chat-picker-meta" title="Context window">${ctx}</small>` : "") +
                `<i class="chat-picker-mark" aria-hidden="true">${selected ? PICKER_CHECK : ""}</i>` +
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
      // One row: label, slider, value.
      `<section class="chat-picker-thinking" aria-label="Thinking mode">` +
      `<p class="chat-picker-thinking-label">Thinking</p>` +
      `<div class="chat-picker-efforts">` +
      `<div class="chat-picker-efforts-ticks" aria-hidden="true">${ticks}</div>` +
      `<input type="range" class="chat-picker-efforts-slider" data-effort-slider min="0" max="${
        efforts.length - 1
      }" step="1" value="${index}" aria-label="Thinking mode" aria-valuetext="${escapeHtml(
        effortLabel(effort)
      )}">` +
      `</div><small class="chat-picker-thinking-value">${escapeHtml(effortLabel(effort))}</small></section>`;
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
    pickerPinId = modelId;
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
    placePicker();
  }


  function placePicker() {
    placeComposerPop(pickerMenuEl, pickerHost.querySelector(".chat-picker-btn"), 26 * 16, true);
  }
  window.addEventListener("resize", () => { if (pickerOpen) placePicker(); });

  function selectModel(id, dismiss) {
    id = resolveListedModel(id) || id;
    if (!modelsById.has(id)) return;
    if (id !== modelId && !requireLive()) return;
    modelId = id;
    pickerIndex = Math.max(0, filteredModels().findIndex((model) => model.id === id));
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
    warnTextOnlyModel();
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
    // On mobile the sheet lives on the chat root, outside pickerHost; drop it
    // too or every re-render leaves an orphaned sheet behind.
    pickerMenuEl?.remove();
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
    if (pickerOpen) {
      renderPickerList();
      placePicker();
    }
  }

  function renderPickerSheet() {
    const menu = document.createElement("div");
    menu.className = "chat-picker-menu" + (pickerSheetQuery.matches ? " is-sheet" : "");
    menu.setAttribute("role", "dialog");
    menu.setAttribute("aria-label", "Model picker");
    menu.innerHTML =
      '<div class="chat-picker-head"><p>Model</p><button type="button" class="chat-menu-close chat-picker-done" data-picker-close aria-label="Close"><svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><path d="M3.5 3.5l9 9M12.5 3.5l-9 9" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg></button></div>' +
      '<div class="chat-picker-searchrow"><svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><circle cx="7" cy="7" r="4.4" fill="none" stroke="currentColor" stroke-width="1.5"/><path d="M10.4 10.4L13.8 13.8" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg><input type="search" class="chat-picker-search" placeholder="Search models" aria-label="Search models"><kbd class="chat-picker-kbd" aria-hidden="true">esc</kbd></div>' +
      '<div class="chat-picker-list" role="listbox" aria-label="Models"></div>' +
      '<div class="chat-picker-thinking-host"></div>';
    attachSheetSwipe(menu, ".chat-picker-head", () => menu.classList.contains("is-sheet"), closePicker);
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
    }
  });
  // Fuse Desktop's WebContentsView has no OS link menu, and a shipped binary
  // does not pick this up until its next release. The page menu covers both.
  log.addEventListener("contextmenu", (event) => {
    const link = event.target.closest("a[href]");
    if (!link || !log.contains(link)) return;
    let url;
    try { url = new URL(link.href, location.href); } catch (_) { return; }
    if (url.protocol !== "http:" && url.protocol !== "https:") return;
    event.preventDefault();
    showPop(null, [
      { label: "Open in browser", run: () => window.open(url.href, "_blank", "noopener") },
      { label: "Copy link", run: () => navigator.clipboard.writeText(url.href).then(() => setHint("Copied link.")) },
    ], { x: event.clientX, y: event.clientY });
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

  // A gesture over a nested scroller (code pane, thinking peek, table) that
  // can still move that way scrolls it, not the transcript, and must not
  // break the follow.
  function innerScrolls(target, dy) {
    for (let el = target instanceof Element ? target : null; el && el !== thread; el = el.parentElement) {
      if (el.scrollHeight <= el.clientHeight + 1) continue;
      const oy = getComputedStyle(el).overflowY;
      if (oy !== "auto" && oy !== "scroll") continue;
      if (dy < 0 ? el.scrollTop > 0 : el.scrollTop + el.clientHeight < el.scrollHeight - 1) return true;
    }
    return false;
  }
  thread.addEventListener("wheel", event => {
    // Pinch/ctrl-zoom and sideways swipes are not a request to stop following.
    if (event.ctrlKey || Math.abs(event.deltaX) > Math.abs(event.deltaY) || innerScrolls(event.target, event.deltaY)) return;
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
    if (y != null && threadTouchY != null && innerScrolls(event.target, threadTouchY - y)) { threadTouchY = y; return; }
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
    if (streaming || reduceMotion.matches) {
      scrollThread(true);
      return;
    }
    // Idle: glide the last stretch instead of teleporting. Far jumps first
    // snap to a screen above the end so the glide stays short.
    const target = threadFollowTarget();
    if (target - thread.scrollTop > thread.clientHeight * 3) thread.scrollTop = target - thread.clientHeight * 1.5;
    thread.scrollTo({ top: target, behavior: "smooth" });
  });

  function parseSlashCommand(text) {
    const match = String(text || "").trim().match(/^\/([a-z][a-z-]*)(?:\s+([\s\S]*))?$/i);
    if (!match) return null;
    if (!SLASH_COMMANDS.some(command => command.name === match[1].toLowerCase())) return null;
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
          `<button type="button" class="chat-queue-action" data-queue-send="${index}" title="Stop the current reply and send this message now"${!streaming ? " hidden" : ""}${item.sending || item.immediate || !streaming ? " disabled" : ""}>Send now</button>` +
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

  async function drainQueue(opts) {
    if (queueDraining || runIsActive(activeRun) || streaming || sending || loading) return;
    if (!queuedTurns.length) return;
    const item = queuedTurns[0];
    if (item.sending) return;
    item.sending = true;
    renderQueue();
    queueDraining = true;
    const origin = currentId;
    try {
      const ok = await completeTurn(item.text, item.attachments || [], { queueId: item.id, commandId: item.id + ":turn", follow: opts?.follow !== false });
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
    dismissRecaps();
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
    // Send now interrupts: the running reply is stopped outright (Stop kills
    // its tree and force-finishes a wedged worker after the grace window),
    // then this message starts a new turn. Steering kept the old request
    // streaming until its next tool boundary, so the session the user just
    // replaced went on producing tokens and late tool calls.
    item.immediate = true;
    queuedTurns = [item, ...queuedTurns.filter((entry) => entry.id !== item.id)];
    persistQueue();
    renderQueue();
    if (runIsActive(activeRun) || streaming) {
      const origin = currentId;
      item.sending = true;
      renderQueue();
      await stopActiveRun();
      item.sending = false;
      if (origin !== currentId) return;
      renderQueue();
    }
    drainQueue({ follow: false });
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

  // These commands are dispatched locally, never queued as model input.
  const LOCAL_SLASH = new Set(["thinking", "help", "permissions", "undo"]);

  async function send() {
    const text = input.value.trim();
    const local = parseSlashCommand(text);
    if (local && LOCAL_SLASH.has(local.name)) {
      closeSlash();
      runSlash(local.name, local.arg);
      return;
    }
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
    dismissRecaps();
    navigation += 1;
    const gen = generation;
    const origin = currentId;
    const settings = { model: modelId, tools: enabledTools(), context: threadContext };
    sending = true;
    // Only composer sends own the composer. Edits and retries carry their own
    // text and must not overwrite an unrelated draft.
    if (!opts?.queueId && usedPending && !opts?.undoSnap) {
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

    pendingArticle.append(bubble);
    pending.append(pendingArticle, status);
    log.after(pending);
    setActive(true);
    // Send now from the queue keeps a paused reader's position: the interrupt
    // transition must not yank the viewport to the bottom.
    if (!opts || opts.follow !== false) {
      stickToBottom = true;
      scrollThread();
    }
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
        throw Object.assign(new Error("A previous send is unconfirmed. Retry its original text before sending another message."), { local: true });
      }
      if (!pendingTurn) setPendingTurn({
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
      });
      const data = await api("/chat/threads/" + pendingTurn.id + "/turns", { method: "POST", body: JSON.stringify(pendingTurn.body) });
      if (opts?.queueId) acknowledgeQueuedTurn(origin, opts.queueId);
      if (gen !== generation) return;
      setPendingTurn(null);
      if (usedPending) {
        // Files pasted or dropped while this send was in flight stay queued.
        const sent = new Set(attachments);
        pendingAttachments = pendingAttachments.filter((att) => !sent.has(att));
        renderAttachList();
      }
      if (!opts?.queueId && usedPending && !opts?.undoSnap && input.value === text) {
        if (window.FuseComposerSpell) FuseComposerSpell.learnFromSent(text);
        input.value = "";
        drafts.delete(origin);
        drafts.delete(currentId);
        draftFiles.delete(origin);
        draftFiles.delete(currentId);
        if (window.FuseComposerSpell) {
          FuseComposerSpell.close();
          FuseComposerSpell.refresh();
        }
        restoreEnterKeyHint();
        resizeInput();
      }
      pending.remove();
      acceptThread(data.thread);
      rememberUndo(undoSnap);
      // The acknowledgment is already a consistent snapshot. Subsequent
      // progress is replayed by the socket from its independent cursor.
      return true;
    } catch (error) {
      if (gen !== generation) return false;
      if (error.local) {
        showFailure("Send not confirmed; your draft is retained", error);
      } else if (error.status === 409) {
        setPendingTurn(null);
        await reconcileThread().catch(() => {});
        showFailure("Send not confirmed; your draft is retained", error);
      } else if (error.status && error.status < 500) {
        setPendingTurn(null);
        showFailure("Send not confirmed; your draft is retained", error);
      } else {
        markDisconnected();
        showFailure("Can't connect to the backend", error);
      }
      return false;
    } finally {
      pending.remove();
      // `sending` is global, not per thread: a navigation away mid-send must
      // still release it or every send/open/new-chat control stays locked.
      sending = false;
      if (gen === generation) { setActive(messages.length > 0); resizeInput(); }
      syncStatus();
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
            if (segment.type === "reasoning" && Number.isInteger(patch.ms)) segment.ms = patch.ms;
          }
          if (Array.isArray(change.append)) {
            if (Number.isInteger(change.base)) {
              if (change.base === msg.segments.length) msg.segments.push(...change.append);
              // A resend may carry a thought's newer duration; only content must match.
              else if (change.base < 0 || JSON.stringify(msg.segments.slice(change.base, change.base + change.append.length).map(untimed)) !== JSON.stringify(change.append.map(untimed))) {
                throw new Error("Segment append base mismatch; refetching snapshot.");
              }
            } else msg.segments.push(...change.append); // pre-upgrade replay
          }
        }
      }
      ensureSteerSegments(msg);
      msg.elapsedMs = event.elapsedMs;
      if (typeof event.endedAt === "string" && event.endedAt) msg.endedAt = event.endedAt;
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
          if (event.description) rec.description = String(event.description).slice(0, 200);
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
      if (!rec || SETTLED_TOOL_STATUS.has(rec.status)) return;
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
      if (byId < 0 || !SETTLED_TOOL_STATUS.has(msg.tools[byId].status)) {
        const activity = { phase: "tools", tool_id: event.id };
        msg._activity = activity;
        if (!child && activeRun && msg.id === activeRun.message_id) activeRun.activity = activity;
      }
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

  function clearChatState({ keepList = false } = {}) {
    const stayOnList = keepList && historyMobile.matches && root.classList.contains("is-history");
    setPartialTranscript(false);
    drafts.set(currentId, input.value);
    draftFiles.set(currentId, pendingAttachments);
    stoppingRunId = "";
    activeRun = null;
    setRunning();
    permCards.clear();
    renderPermQueue();
    threadBypass = false;
    threadAuto = false;
    threadPermData = null;
    permsFilter = "";
    syncPermChip();
    navigation += 1;
    loading = false;
    generation += 1;
    window.clearTimeout(saveTimer);
    messages = [];
    threadRecaps = [];
    resetHistoryStacks();
    currentId = null;
    serverRevision = 0;
    viewCursor = 0;
    threadWatermark = 0;
    contextMessages = [];
    contextLive = false;
    // The queue belongs to the thread it was typed in (persisted under that
    // thread's key); never let it drain into the next chat.
    acceptedQueueCommands = new Set();
    loadQueue();
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
    pendingAttachments = draftFiles.get(null) || [];
    renderAttachList();
    setThreadUrl(null, { stay: stayOnList });
    paint();
    renderHistory();
    defaultHint();
    syncMemoryUi();
    if (!stayOnList) setHistoryOpen(false);
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
    dismissRecaps(false);
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
        placeComposerPop(contextPop, contextBtn, 30 * 16);
      }
    });
    window.addEventListener("resize", () => placeComposerPop(contextPop, contextBtn, 30 * 16));
  }
  backLayerFollowsHidden(contextPop, "context");
  if (contextPop) {
    contextPop.addEventListener("click", (event) => {
      if (event.target.closest("[data-context-pop-compact]")) runCompact("");
    });
  }
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

  input.addEventListener("input", () => {
    composerVersion += 1;
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
    document.documentElement.scrollTop = 0;
    document.body.scrollTop = 0;
    pinVisualViewport();
  });
  input.addEventListener("blur", () => {
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

    // Dispatch local commands ahead of the slash menu and send queue.
    if (event.key === "Enter" && !event.shiftKey && !event.altKey && !spellOpen) {
      const local = parseSlashCommand(input.value);
      if (local && LOCAL_SLASH.has(local.name)) {
        event.preventDefault();
        closeSlash();
        runSlash(local.name, local.arg);
        return;
      }
    }

    if (streaming && event.key === "Enter" && !event.shiftKey) {
      // Enter never stops a running turn: it queues the message. Send now on
      // a queued row stops the running reply and sends it as a new turn instead.
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
    // PNG/WebP keep transparency and sharp text; re-encode only when too big.
    const keep = file.type === "image/png" || file.type === "image/webp";
    if (keep && original.length <= ATTACH_CHARS && Math.max(img.width || 0, img.height || 0) <= maxEdge) {
      return { url: original, mime: file.type };
    }
    const scale = Math.min(1, maxEdge / Math.max(img.width || 1, img.height || 1));
    const width = Math.max(1, Math.round((img.width || 1) * scale));
    const height = Math.max(1, Math.round((img.height || 1) * scale));
    const canvas = document.createElement("canvas");
    canvas.width = width;
    canvas.height = height;
    const ctx = canvas.getContext("2d");
    if (!ctx) throw new Error("Could not process image");
    // JPEG has no alpha: without a fill, transparent pixels turn black.
    ctx.fillStyle = "#fff";
    ctx.fillRect(0, 0, width, height);
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
    // Reading a file is async: the chat may change, and a second paste may
    // run concurrently. Resolve the target list and the limit after the await.
    const origin = currentId;
    const target = () => {
      if (origin === currentId) return pendingAttachments;
      if (!draftFiles.has(origin)) draftFiles.set(origin, []);
      return draftFiles.get(origin);
    };
    const full = () => {
      if (target().length < ATTACH_MAX) return false;
      setHint("Up to " + ATTACH_MAX + " attachments per message.", true, 4000);
      return true;
    };
    for (const file of files) {
      if (full()) break;
      try {
        const att = await fileToAttachment(file);
        if (full()) break;
        target().push(att);
      } catch (error) {
        showFailure("Could not attach file", error);
      }
    }
    renderAttachList();
    warnTextOnlyModel();
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
      // Close through setArtifactOpen so the mobile overlay/history entry is
      // dropped too; a bare flag write leaves a Back press that does nothing.
      if (artifactOpen) setArtifactOpen(false);
      else syncArtifactUi();
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

  // Total active time across every leg the subagent ran (spawn, task_id
  // continuations, follow-ups, resumes). created_at/updated_at are wall
  // clock and would count every idle gap between legs, so they are not used.
  function agentDurationSeconds(row) {
    const base = Number(row && row.work_ms);
    if (!row || row.work_ms == null || !Number.isFinite(base) || base < 0) return null;
    if (!row._workAt) row._workAt = Date.now();
    const tail = row.work_live && row.status === "running" ? Date.now() - row._workAt : 0;
    return (base + Math.max(0, tail)) / 1000;
  }

  // The subagent dock clock: earlier legs plus the live message's clock.
  function agentRunWorkSeconds(run, msg) {
    const before = Math.max(0, Number(run && run.work_ms_before) || 0);
    return before / 1000 + (liveElapsedSeconds(msg, true) || 0);
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
    scratch: "scratch",
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

  function renderAgentsList() {
    if (!agentsListEl) return;
    if (toolGestureOwner === agentsListEl) { pendingAgentListPaint = true; return; }
    const { live, done } = agentRowsSorted();
    const count = [live.length ? `${live.length} running` : "", done.length ? `${done.length} finished` : ""].filter(Boolean).join(" · ");
    let html = workHeadHtml("Subagents", count);
    if (live.length) html += '<p class="chat-agents-kicker">Running · longest first</p>' + live.map(agentRowHtml).join("");
    if (done.length) html += '<p class="chat-agents-kicker">Finished</p>' + done.map(agentRowHtml).join("");
    if (!live.length && !done.length) html += '<p class="chat-agents-empty">No subagents yet. The main agent spawns them for isolated sub-tasks while it works.</p>';
    FuseChatDOM.reconcile(agentsListEl, html);
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
    if (text.startsWith("context compacted")) return "Context compacted. The conversation was replaced with a handoff. Nothing else was kept.";
    if (text.startsWith("context pruning")) return "Context pruned. Older tool outputs were shortened; recent results are unchanged.";
    return "";
  }

  function agentSeedArticle(item, index) {
    // Same renderers as the main chat so every styling/behavior tweak
    // (tool timelines, themes, bubbles) applies here automatically.
    if (item.role === "marker" || item.kind === "compaction" || item.compaction) {
      const article = document.createElement("article");
      article.className = "chat-turn is-marker";
      article.innerHTML = compactionMarkHtml("Context compacted", item.summary || item.content || "");
      return article;
    }
    if (item.role === "user") {
      // live=true suppresses main-chat-bound actions; the negative index
      // can never collide with a main-chat editingIndex.
      return renderUser({
        role: "user",
        content: String(item.content || ""),
        auto: Boolean(item.auto),
        autoSteps: Array.isArray(item.autoSteps) ? item.autoSteps : null,
        attachments: Array.isArray(item.attachments) ? item.attachments : [],
        fromParent: Boolean(item.fromParent),
        pending: Boolean(item.pending),
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
      closeAgentContextPop();
      return;
    }
    const ratio = Math.max(0, Math.min(1, used / limit));
    const pct = Math.round(ratio * 100);
    agentMeterEl.hidden = false;
    if (agentContextFill) {
      agentContextFill.style.strokeDasharray = String(CONTEXT_RING);
      agentContextFill.style.strokeDashoffset = String(CONTEXT_RING * (1 - ratio));
    }
    const btn = agentContextBtn;
    if (btn) {
      btn.classList.toggle("is-warn", Boolean(meter.warn) && !meter.auto);
      btn.classList.toggle("is-auto", Boolean(meter.auto));
      btn.classList.toggle("is-busy", Boolean(meter.compacting));
      const title = `${formatTokens(used)} / ${formatTokens(limit)} tokens (${pct}%)`;
      btn.setAttribute("aria-label", "Subagent context usage " + title);
      btn.title = title;
    }
    if (agentContextPop && !agentContextPop.hidden) {
      const parts = meter.parts && typeof meter.parts === "object" ? meter.parts : null;
      fillContextCard(agentContextPop, {
        used, limit, ratio, rows: parts ? partsRows(parts) : [["Context", used, ""]], warn: meter.warn, auto: meter.auto,
        autoEnabled: meter.auto_enabled !== false, autoRatio: Number(meter.auto_ratio) || Number(compactCfg.auto_ratio) || 0.9,
        compact: false,
      });
    }
  }

  function closeAgentContextPop() {
    if (agentContextPop) agentContextPop.hidden = true;
    if (agentContextBtn) agentContextBtn.setAttribute("aria-expanded", "false");
  }

  if (agentContextBtn) {
    agentContextBtn.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
      if (!agentContextPop) return;
      const open = agentContextPop.hidden;
      agentContextPop.hidden = !open;
      agentContextBtn.setAttribute("aria-expanded", String(open));
      if (open) syncAgentMeter(agentRun);
    });
  }
  backLayerFollowsHidden(agentContextPop, "agent-context");
  document.addEventListener("click", (event) => {
    if (agentContextPop && !agentContextPop.hidden && agentMeterEl && !agentMeterEl.contains(event.target)) closeAgentContextPop();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && agentContextPop && !agentContextPop.hidden) closeAgentContextPop();
  });

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
    if (agentFollowFrame) cancelAnimationFrame(agentFollowFrame);
    agentFollowFrame = 0;
    pinAgentLog();
  }

  // A subagent's transcript follows its end the way the main one does: the
  // view moves with the content in the same frame, never a few frames behind.
  function pinAgentLog() {
    if (!agentStick || !agentLogEl || !agentsOpen) { agentIgnoreScroll = false; return; }
    agentIgnoreScroll = true;
    agentLogEl.scrollTop = Math.max(0, agentLogEl.scrollHeight - agentLogEl.clientHeight);
    agentTailInitialized = true;
    requestAnimationFrame(() => { agentIgnoreScroll = false; });
  }

  function syncAgentLiveDock() {
    scheduleTodoSync();
    if (!agentLiveDock) return;
    const stalled = Boolean(agentRun && agentIsStalled(agentRun));
    const live = Boolean(agentViewId && agentsOpen && agentRun && agentLive(agentRun.status) && agentViewFailCount < 3 && !stalled);
    if (!live && !stalled) {
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
    if (shimmer) shimmer.hidden = !live && !stalled;
    if (stalled) {
      setLivePhase(agentLiveDock, "stalled");
      setLiveLabelText(agentLiveLabel, ("stalled — no progress"));
      if (agentLiveSpin && (agentLiveSpin.firstChild || agentLiveSpin.dataset.spinSet)) {
        agentLiveSpin.replaceChildren();
        delete agentLiveSpin.dataset.spinSet;
      }
    } else if (live) {
      const msg = agentMsgState || {};
      if (!msg._spinOrigin) msg._spinOrigin = Date.now();
      const groups = groupTraces(Array.isArray(msg.tools) ? msg.tools : []);
      const status = currentActivity(msg, groups, true);
      setLivePhase(agentLiveDock, status.phase);
      // The subagent dock mirrors the main dock of the selected view.
      {
        const line = formatConsoleLiveStatus(msg, groups, {
          meter: agentRun.compaction && agentRun.compaction.meter,
          seconds: agentRunWorkSeconds(agentRun, msg),
        });
        setLiveLabelText(agentLiveLabel, line.verb, line.meta);
        if (agentLiveSpin) setConsoleSpin(agentLiveSpin);
      }
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
      // Without it the live clock fell back to another run's start time.
      elapsedMs: raw.elapsedMs ?? null,
    });
    // Before compaction prunes the traces: the list the child works from.
    latestTodoState(agentMsgState);
    const seed = Array.isArray(agentRun.messages) ? agentRun.messages : [];
    // A subagent's own background-command handoffs fold into step rows here
    // exactly as the thread's auto wake rows do, instead of a raw report blob.
    foldAutoCommandResults(seed);
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
    if (agentClockLive()) startLiveClock();
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
    closeAgentContextPop();
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
      const priorWork = new Map(agentsRows.map((row) => [String(row.id), row]));
      agentsRows = Array.isArray(data.agents) ? data.agents : [];
      agentsLoadedFor = tid;
      const receivedAt = Date.now();
      agentsRows.forEach(row => {
        // (work_ms, _workAt) is one pair: shift the stamp by the base's jump
        // instead of re-stamping, so the dock clock never sawtooths back when
        // work_ms accrues slower than wall time.
        const prior = priorWork.get(String(row.id));
        row._workAt = prior && Number.isFinite(prior._workAt) && Number.isFinite(Number(prior.work_ms)) && Number.isFinite(Number(row.work_ms))
          ? prior._workAt + (Number(row.work_ms) - Number(prior.work_ms)) : receivedAt;
        agentLifecycle.set(String(row.id), row);
      });
      reconcileAgentTraces(agentsRows, true);
      settleOrphanTraces(["spawn_subagent"], agentsRows);
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

  async function onJobsClick(event) {
    const output = event.target.closest("[data-show-job]");
    if (output) {
      const id = output.dataset.showJob;
      // A subagent's command lives in that subagent's transcript, which is
      // already on screen below its list; the main agent's lives in the thread.
      const inAgent = Boolean(agentJobsEl && agentJobsEl.contains(output));
      const row = (inAgent ? agentBodyEl : log)?.querySelector(`[data-execution="${CSS.escape(id)}"]`);
      if (row && inAgent) {
        revealToolRow(row);
        setTimeout(() => { if (row.isConnected) { row.scrollIntoView({ block: "center", behavior: reduceMotion.matches ? "auto" : "smooth" }); row.classList.add("is-work-highlight"); setTimeout(() => row.classList.remove("is-work-highlight"), 1800); } }, reduceMotion.matches ? 0 : 230);
      } else if (row) {
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
  }
  if (jobsListEl) jobsListEl.addEventListener("click", onJobsClick);
  if (agentJobsEl) agentJobsEl.addEventListener("click", onJobsClick);

  function jobElapsedMs(job, nowMs) {
    const started = Number(job && job.started_ms);
    if (Number.isFinite(started) && started > 0) return Math.max(0, nowMs - started);
    return Math.max(0, Number(job && job.elapsed_ms) || 0);
  }

  // Each transcript's pinned strip contains only its owner's commands.
  function jobsFor(agentId) {
    const owner = String(agentId || "");
    return jobsRows.filter((job) => String(job.agent_run_id || "") === owner);
  }

  let agentJobsRendered = "";
  function renderJobsList() {
    const nowMs = Date.now();
    const sigOf = (rows) => JSON.stringify(rows.map((job) => [job.id, job.command, job.description, job.host, job.state, job.event_count, job.kind]));
    if (jobsListEl) {
      const rows = jobsFor("");
      const sig = sigOf(rows);
      if (sig !== jobsRendered) {
        jobsRendered = sig;
        jobsListEl.hidden = !rows.length;
        jobsListEl.innerHTML = rows.length
          ? workHeadHtml("Background commands", String(rows.length)) +
            rows.map((job) => jobRowHtml(job, nowMs)).join("")
          : "";
      }
    }
    if (agentJobsEl) {
      const rows = agentViewId ? jobsFor(agentViewId) : [];
      const sig = agentViewId + sigOf(rows);
      if (sig !== agentJobsRendered) {
        agentJobsRendered = sig;
        agentJobsEl.hidden = !rows.length;
        agentJobsEl.innerHTML = rows.length
          ? workHeadHtml("Background commands", String(rows.length)) + rows.map((job) => jobRowHtml(job, nowMs)).join("")
          : "";
      }
    }
    tickJobTimes();
  }

  function tickJobTimes() {
    if (!agentsPanel || !jobsRows.length) return;
    const nowMs = Date.now();
    jobsRows.forEach((job) => {
      root.querySelectorAll('[data-job-time="' + CSS.escape(String(job.id)) + '"]').forEach((el) => {
        el.textContent = formatActivityClock(Math.round(jobElapsedMs(job, nowMs) / 1000));
      });
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
      settleOrphanTraces(["run_terminal_command", "monitor"], [...jobsRows, ...completedJobs.values()]);
    } catch {} finally {
      if (jobsRequest === ticket) {
        jobsRequest = null;
        if (jobsRefreshAgain) { jobsRefreshAgain = false; refreshJobs(); }
      }
    }
  }

  // A background call whose job/agent row no longer exists can never report
  // back; left alone it renders Running forever and keeps its poll alive.
  // Only judged while no run is live (a fresh call's row is written first).
  function settleOrphanTraces(names, rows) {
    if (runIsActive(activeRun)) return;
    const known = new Set(rows.map((row) => String(row.command_id || row.child_id || row.run_id || row.id)));
    let changed = false;
    messages.forEach((msg) => {
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
    messages.forEach((msg) => {
      const article = msg.id ? articleFor(msg) : null;
      if (article && msg.role === "assistant") updateAssistant(article, msg, false);
    });
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
      const articles = new Map([...log.children].map((el) => [el.dataset.messageId, el]));
      messages.forEach((msg) => {
        const article = msg.id ? articles.get(msg.id) : null;
        if (article && msg.role === "assistant") {
          updateAssistant(article, msg, Boolean(streaming && activeRun && msg.id === activeRun.message_id));
        }
      });
    }
  }

  setInterval(() => {
    if (currentId && (agentsOpen || jobsRows.length || hasRunningBgTraces())) refreshJobs();
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
    scheduleTodoSync();
    syncCurrentThreadBusy();
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
    if (agentsPanel) {
      agentsPanel.querySelector('.chat-agents-copy').hidden = Boolean(agentViewId);
      agentsPanel.querySelector('[data-agent-head]').hidden = !agentViewId;
    }
    if (agentsOverviewEl) agentsOverviewEl.hidden = Boolean(agentViewId);
    if (agentViewEl) agentViewEl.hidden = !agentViewId;
    for (const tab of agentsTabs) {
      tab.setAttribute("aria-expanded", agentsOpen ? "true" : "false");
      tab.classList.toggle("is-live", workCount > 0);
      tab.title = workLabel || "Agents and background commands";
      tab.setAttribute("aria-label", workLabel ? `Agents and commands: ${workLabel}` : "Agents and commands");
      const countEl = tab.querySelector("[data-agents-count]");
      if (countEl) {
        // The badge is a count only; the breakdown lives in the title so the
        // badge keeps a fixed footprint inside the button.
        const next = workCount ? String(workCount) : "";
        if (next && countEl.textContent !== next) {
          countEl.classList.remove("is-bump");
          void countEl.offsetWidth;
          countEl.classList.add("is-bump");
        }
        countEl.textContent = next;
        countEl.classList.toggle("has-count", Boolean(next));
      }
    }
    renderJobsList();
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
      if (agentViewId) sendSyncView(agentViewId);
      if (agentViewId && agentRun) pollAgentView();
    } else {
      agentsViewGen += 1;
      // Progress for a hidden view is discarded; stop the server streaming it.
      sendSyncView("");
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
    agentsLoadedFor = "";
    jobsRows = [];
    completedJobs.clear();
    renderJobsList();
    sendSyncView("");
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
      const previewBtn = event.target.closest("[data-image-preview]");
      if (previewBtn) {
        event.preventDefault();
        event.stopPropagation();
        openLightbox(previewBtn.getAttribute("data-image-preview"), previewBtn.getAttribute("data-image-name") || "");
        return;
      }
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


  function setShowThinking(on) {
    showThinking = Boolean(on);
    try { localStorage.setItem(STORE_THINKING, showThinking ? "shown" : "hidden"); } catch (_) {}
    const toggle = viewMenu && viewMenu.querySelector("[data-thinking-toggle]");
    if (toggle) toggle.checked = showThinking;
    if (messages.length) paint();
    if (agentViewId && agentRun) renderAgentView();
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
    viewMenu.classList.toggle("is-sheet-motion", mobile);
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
  if (viewMenu) {
    attachSheetSwipe(viewMenu, "[data-view-sheet-head]", () => viewMenu.classList.contains("is-sheet-motion"), () => setViewMenuOpen(false));
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
    const thinkingToggle = viewMenu.querySelector("[data-thinking-toggle]");
    if (thinkingToggle) {
      thinkingToggle.checked = showThinking;
      thinkingToggle.addEventListener("change", () => setShowThinking(thinkingToggle.checked));
    }
    ;
    viewBackdrop?.addEventListener("click", () => setViewMenuOpen(false));
  }
  root.setAttribute("data-view", "console");
  document.documentElement.dataset.chatConsole = "true";
  document.documentElement.removeAttribute("data-chat-terminal");
  // Settings › Appearance links to /chat?appearance=1 to open this panel.
  if (viewMenu && new URLSearchParams(location.search).has("appearance")) {
    const params = new URLSearchParams(location.search);
    params.delete("appearance");
    const query = params.toString();
    history.replaceState(history.state, "", location.pathname + (query ? `?${query}` : "") + location.hash);
    setViewMenuOpen(true);
  }
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
  // The phone quick dial (static/quick-dial.js) runs chat actions through
  // this surface so it never duplicates their rules.
  window.FuseChatActions = {
    state() {
      return {
        running: runIsActive(activeRun) || sending,
        hasTurns: messages.length > 0,
        canRedo: redoStack.length > 0,
        loading,
      };
    },
    run(action) {
      if (action === "retry") {
        if (runIsActive(activeRun) || sending) stopActiveRun().catch((error) => showFailure("Could not stop", error));
        else retryLastTurn();
      } else if (action === "undo") undoConversation();
      else if (action === "redo") redoConversation();
      else if (action === "rewind") {
        if (!messages.length) setHint("Nothing to rewind.");
        else openRewind();
      } else if (action === "agents") setAgentsOpen(true);
      else if (action === "new") {
        const project = sessionProject();
        const start = project ? startProjectThread(project.host_id, project.project_id) : resetChat();
        Promise.resolve(start).catch((error) => showFailure("Could not start a new chat", error));
      }
    },
    hint(text) {
      setHint(text);
    },
  };
  newBtns.forEach((btn) =>
    btn.addEventListener("click", () => {
      if (historyTab === "projects") {
        openProjectPicker().catch((error) => showFailure("Could not start a project", error));
        return;
      }
      resetChat().catch((error) => showFailure("Could not start a new chat", error));
    })
  );
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

  // On mobile the chat list is the page's base screen ("home"): opened that way
  // it adds no history entry, so back from the list leaves the app instead of
  // bouncing into the chat underneath.
  function setHistoryOpen(open, { home = false } = {}) {
    const next = Boolean(open);
    const was = root.classList.contains("is-history");
    root.classList.toggle("is-history", next);
    // The home screen appears in place; only an explicit open slides in.
    if (next && !was) root.classList.toggle("history-home", home);
    if (!next) root.classList.remove("history-home");
    if (next && !was) {
      document.dispatchEvent(new Event("fuse:history-open"));
      closePop();
      if (memoryOpen) setMemoryOpen(false);
      if (skillsOpen && overlayIsMobile()) setSkillsOpen(false);
      if (artifactOpen && overlayIsMobile()) setArtifactOpen(false);
      if (agentsOpen && overlayIsMobile()) setAgentsOpen(false);
      if (overlayIsMobile() && !home) overlayPush("history");
    } else if (!next && was) {
      armOverlayGhost(historyCollapse);
      overlayDrop("history");
    }
    syncHistory();
    if (next && !home && overlayIsMobile()) historyCollapse?.focus({ preventScroll: true });
  }

  historyBackdrop?.addEventListener("click", () => setHistoryOpen(false));
  // Back swipe: inside a chat it reveals the chat list (like the Chats toggle);
  // from the list itself the browser leaves the page, as usual.
  window.addEventListener("popstate", (event) => {
    // FuseBack (app.js, registered first) already closed a layer for this one.
    if (event.fuseBackHandled) return;
    if (!overlayIsMobile()) return;
    if (event.state && event.state.fuseChat) {
      if (root.classList.contains("is-history")) setHistoryOpen(false);
      return;
    }
    if (!root.classList.contains("is-history")) setHistoryOpen(true, { home: true });
  });
  // app.js closes history directly when opening global navigation.
  new MutationObserver(syncHistory).observe(root, { attributes: true, attributeFilter: ["class"] });
  historyMobile.addEventListener("change", () => {
    setHistoryOpen(historyMobile.matches && !currentId && !messages.length, { home: true });
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

  const STORE_HISTORY_COLLAPSED = "fuse.chat.historyCollapsed.v1";
  function historyCollapsedPrefs() {
    try { return JSON.parse(localStorage.getItem(STORE_HISTORY_COLLAPSED) || "{}") || {}; } catch (_) { return {}; }
  }
  function saveHistoryCollapsed() {
    const prefs = historyCollapsedPrefs();
    prefs["console"] = root.classList.contains("history-collapsed");
    try { localStorage.setItem(STORE_HISTORY_COLLAPSED, JSON.stringify(prefs)); } catch (_) {}
  }
  const savedHistoryCollapsed = historyCollapsedPrefs()["console"];
  root.classList.toggle("history-collapsed", typeof savedHistoryCollapsed === "boolean" ? savedHistoryCollapsed : true);
  syncHistory();
  historyToggles.forEach((btn) => {
    btn.addEventListener("click", (event) => {
      event.stopPropagation();
      if (historyMobile.matches) setHistoryOpen(!root.classList.contains("is-history"));
      else {
        root.classList.toggle("history-collapsed");
        saveHistoryCollapsed();
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
    historyList.addEventListener("contextmenu", (event) => {
      const row = event.target.closest(".chat-history-item");
      if (!row || !historyList.contains(row)) return;
      event.preventDefault();
      const at = { x: event.clientX, y: event.clientY };
      if (row.dataset.project) {
        projectMenu(row, row.dataset.project, at);
        return;
      }
      if (row.dataset.thread) threadMenu(row, row.dataset.thread, at);
    });

    const pinnedRowEls = () => [...historyList.querySelectorAll(".chat-history-item.is-pinned")];

    function endPinDrag(commit) {
      const drag = pinDrag;
      pinDrag = null;
      if (!drag) return;
      window.clearTimeout(drag.holdTimer);
      if (!drag.dragging) return;
      drag.row.classList.remove("is-dragging");
      historyList.classList.remove("is-reordering");
      document.body.classList.remove("is-pin-dragging");
      // The pointerup that ends a drag also clicks the row it started on.
      pinDragClickBlock = Date.now() + 400;
      if (!commit) {
        renderHistory();
        return;
      }
      const ids = pinnedRowEls().map((el) => el.getAttribute("data-thread")).filter(Boolean);
      ids.forEach((id, index) => {
        const item = threadById(id);
        if (item) item.pin_order = index;
      });
      renderHistory();
      api("/chat/threads/pin-order", {
        method: "POST",
        body: JSON.stringify({ ids, command_id: newSession() }),
      })
        .then((data) => {
          (data.threads || []).forEach(upsertLocal);
          renderHistory();
        })
        .catch((error) => {
          showFailure("Could not save pinned order", error);
          refreshThreads().catch(() => {});
        });
    }

    function cancelPinDrag() {
      if (!pinDrag) return;
      if (pinDrag.dragging) endPinDrag(false);
      else {
        window.clearTimeout(pinDrag.holdTimer);
        pinDrag = null;
      }
    }

    function movePinnedDrag(event) {
      const row = pinDrag.row;
      const rows = pinnedRowEls().filter((el) => el !== row);
      let target = null;
      for (const el of rows) {
        const box = el.getBoundingClientRect();
        if (event.clientY < box.top + box.height / 2) {
          target = el;
          break;
        }
      }
      if (target) historyList.insertBefore(row, target);
      else if (rows.length) rows[rows.length - 1].after(row);
    }

    // Mouse drags a pinned row straight away; a finger must hold still first,
    // so a plain swipe keeps scrolling the list. Once the hold arms the drag,
    // the touchmove veto below keeps the browser from claiming the gesture.
    historyList.addEventListener("pointerdown", (event) => {
      if (event.button !== 0 || pinDrag) return;
      // Reordering needs the whole pinned section visible: search, the archive
      // view and a project filter each hide rows, so a drop there would write
      // an order the hidden rows never voted on.
      if (historyQuery.trim() || showArchived || (historyTab === "projects" && projectFilter)) return;
      const row = event.target.closest(".chat-history-item.is-pinned");
      if (!row || event.target.closest("button.more")) return;
      const touch = event.pointerType !== "mouse";
      pinDrag = {
        row,
        id: event.pointerId,
        start: { x: event.clientX, y: event.clientY },
        armed: !touch,
        dragging: false,
        holdTimer: 0,
      };
      if (!touch) return;
      pinDrag.holdTimer = window.setTimeout(() => {
        if (!pinDrag || pinDrag.dragging) return;
        pinDrag.armed = true;
        if (navigator.vibrate) navigator.vibrate(10);
      }, 260);
    });
    window.addEventListener("pointermove", (event) => {
      if (!pinDrag || event.pointerId !== pinDrag.id) return;
      const dist = Math.hypot(event.clientX - pinDrag.start.x, event.clientY - pinDrag.start.y);
      if (!pinDrag.dragging) {
        if (!pinDrag.armed) {
          if (dist > 8) cancelPinDrag();
          return;
        }
        if (dist < 3) return;
        pinDrag.dragging = true;
        pinDrag.row.classList.add("is-dragging");
        historyList.classList.add("is-reordering");
        document.body.classList.add("is-pin-dragging");
      }
      movePinnedDrag(event);
    }, { passive: true });
    historyList.addEventListener("touchmove", (event) => {
      if (pinDrag && pinDrag.dragging) event.preventDefault();
    }, { passive: false });
    window.addEventListener("pointerup", (event) => {
      if (!pinDrag || (pinDrag.dragging && event.pointerId !== pinDrag.id)) return;
      endPinDrag(pinDrag.dragging);
    });
    window.addEventListener("pointercancel", () => cancelPinDrag());

    historyList.addEventListener("click", (event) => {
      if (Date.now() < pinDragClickBlock) {
        event.preventDefault();
        event.stopPropagation();
        return;
      }
      const more = event.target.closest("button.more");
      if (more) {
        event.preventDefault();
        event.stopPropagation();
        const row = more.closest("[data-project], [data-thread]");
        if (row?.dataset.project) {
          projectMenu(more, row.dataset.project);
          return;
        }
        const id = row?.dataset.thread;
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
    if (root.classList.contains("is-history") && historyToggle && !historyPanel.contains(event.target) && !historyToggle.contains(event.target) && !event.target.closest(".chat-pop, dialog")) {
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
      const confirmOpen = document.querySelector("dialog.modal[data-confirm][open], dialog.fp-dialog[open]");
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
    // Opening a side pane (or resizing the window) narrows the transcript and
    // reflows it taller without a scroll event; a reader following the bottom
    // would otherwise be left with the last reply hidden under the dock.
    if (thread) {
      const threadResizeObserver = new ResizeObserver(() => {
        if (stickToBottom && !threadFollowFrame) snapThread();
      });
      threadResizeObserver.observe(thread);
      // Anything that makes the transcript taller (a streamed line, a tool row,
      // the status line or the todo section changing size) re-pins the bottom
      // before the browser paints, so a following reader never sees a frame
      // where the new content sits below the view.
      [log, liveDock, todoSections.main.el].forEach((el) => el && threadResizeObserver.observe(el));
    }
    const chatBar = root.querySelector(".chat-bar");
    if (chatBar) new ResizeObserver(() => scheduleFitChatBar()).observe(chatBar);
    if (agentBodyEl) new ResizeObserver(() => { if (agentStick) pinAgentLog(); }).observe(agentBodyEl);
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
  if (historyMobile.matches && !new URLSearchParams(window.location.search).get("t")) {
    setHistoryOpen(true, { home: true });
  }
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
