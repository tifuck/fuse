// Shared mutable runtime state for the chat page: element refs, config
// catalogs, session/sync/sidebar/composer data. One field list, accessed as
// S.<field> from every module. Node-loadable: DOM init is guarded so the node
// test suites can require modules that import this file.
const root = typeof document === "undefined" ? null : document.querySelector("[data-chat]");
const S = { root };
if (root) initState();

function initState() {
  S.cfgEl = document.getElementById("chat-config");
  S.config = { families: [], locked: [], preselect: "", tools: [], context: {}, limits: {} };
    try {
    S.config = JSON.parse(S.cfgEl ? S.cfgEl.textContent : "{}") || S.config;
  } catch (_) {}
  S.thread = S.root.querySelector("[data-chat-thread]");
  S.log = S.root.querySelector("[data-chat-log]");
  S.form = S.root.querySelector("[data-chat-form]");
  S.input = S.root.querySelector("[data-chat-input]");
  S.sendBtn = S.root.querySelector("[data-chat-send]");
  S.newBtns = [...S.root.querySelectorAll("[data-chat-new]")];
  S.hint = S.root.querySelector("[data-chat-hint]");
  S.pickerHost = S.root.querySelector("[data-chat-picker]");
  S.attachBtn = S.root.querySelector("[data-chat-attach]");
  S.fileInput = S.root.querySelector("[data-chat-file]");
  S.attachList = S.root.querySelector("[data-chat-attach-list]");
  S.chatBox = S.root.querySelector(".chat-box");
  S.historyList = S.root.querySelector("[data-history-list]");
  S.historyKicker = S.root.querySelector("[data-history-kicker]");
  S.historySearch = S.root.querySelector("[data-history-search]");
  S.archivedBtn = S.root.querySelector("[data-history-archived]");
  S.slashMenu = S.root.querySelector("[data-chat-slash]");
  S.slashList = S.root.querySelector("[data-chat-slash-list]");
  S.slashKicker = S.slashMenu ? S.slashMenu.querySelector(".chat-slash-kicker") : null;
  S.spellPop = S.root.querySelector("[data-chat-spell-pop]");
  S.composeMirror = S.root.querySelector("[data-chat-compose-mirror]");
  S.rewindPanel = S.root.querySelector("[data-chat-rewind]");
  S.rewindList = S.root.querySelector("[data-chat-rewind-list]");
  S.rewindClose = S.root.querySelector("[data-rewind-close]");
  S.contextWrap = S.root.querySelector("[data-context-wrap]");
  S.contextBtn = S.root.querySelector("[data-chat-context]");
  S.contextFill = S.root.querySelector("[data-context-fill]");
  S.contextPop = S.root.querySelector("[data-context-pop]");
  S.contextAlert = S.root.querySelector("[data-context-alert]");
  S.contextAlertText = S.root.querySelector("[data-context-alert-text]");
  S.contextAlertCompact = S.root.querySelector("[data-context-alert-compact]");
  S.contextAlertDismiss = S.root.querySelector("[data-context-alert-dismiss]");
  S.historyToggles = [...S.root.querySelectorAll("[data-history-toggle]")];
  S.historyToggle = S.root.querySelector(".chat-bar [data-history-toggle]");
  S.titleEl = S.root.querySelector("[data-chat-title]");
  S.projectChip = S.root.querySelector("[data-project-chip]");
  S.projectName = S.root.querySelector("[data-project-name]");
  S.projectIconEl = S.root.querySelector("[data-project-icon]");
  S.welcomeEl = S.root.querySelector("[data-chat-welcome]");
  S.memoryPanel = S.root.querySelector("[data-chat-memory]");
  S.memoryToggle = S.root.querySelector("[data-memory-toggle]");
  S.memoryClose = S.root.querySelector("[data-memory-close]");
  S.memoryBackdrop = S.root.querySelector("[data-memory-backdrop]");
  S.skillsPanel = S.root.querySelector("[data-chat-skills]");
  S.skillsToggle = S.root.querySelector("[data-skills-toggle]");
  S.skillsClose = S.root.querySelector("[data-skills-close]");
  S.skillsBackdrop = S.root.querySelector("[data-skills-backdrop]");
  S.viewToggle = S.root.querySelector("[data-view-toggle]");
  S.viewMenu = S.root.querySelector("[data-view-menu]");
  S.viewBackdrop = S.root.querySelector("[data-view-backdrop]");
  S.memoryInstructions = S.root.querySelector("[data-memory-instructions]");
  S.memoryProfile = S.root.querySelector("[data-memory-profile]");
  S.memoryThread = S.root.querySelector("[data-memory-thread]");
  S.memoryProject = S.root.querySelector("[data-memory-project]");
  S.contextTabButtons = Array.from(S.root.querySelectorAll("[data-context-tab]"));
  S.contextTab = "instructions";
  S.searchChip = S.root.querySelector("[data-tool-search]");
  S.liveDock = S.root.querySelector("[data-live-dock]");
  S.liveDockLabel = S.root.querySelector("[data-live-dock-label]");
  S.liveDockSpin = S.root.querySelector("[data-live-dock-spin]");
  S.latestPill = S.root.querySelector("[data-chat-latest]");
  S.latestSpin = S.root.querySelector("[data-chat-latest-spin]");
  S.liveDockHideTimer = 0;
  S.liveDockShowFrame = 0;
  S.liveDockFollowFrame = 0;
  S.STORE_THINKING = "fuse.chat.thinking.v1";
  S.artifactPanel = S.root.querySelector("[data-chat-artifact]");
  S.artifactToggle = S.root.querySelector("[data-artifact-toggle]");
  S.artifactClose = S.root.querySelector("[data-artifact-close]");
  S.artifactBackdrop = S.root.querySelector("[data-artifact-backdrop]");
  S.artifactTitleEl = S.root.querySelector("[data-artifact-title]");
  S.artifactStatusEl = S.root.querySelector("[data-artifact-status]");
  S.artifactVisibilityEl = S.root.querySelector("[data-artifact-visibility]");
  S.artifactVersionWrap = S.root.querySelector("[data-artifact-version-wrap]");
  S.artifactVersionEl = S.root.querySelector("[data-artifact-version]");
  S.artifactOpenLink = S.root.querySelector("[data-artifact-open]");
  S.artifactFrame = S.root.querySelector("[data-artifact-frame]");
  S.artifactSource = S.root.querySelector("[data-artifact-source]");
  S.artifactBuilding = S.root.querySelector("[data-artifact-building]");
  S.artifactModeBtns = [...S.root.querySelectorAll("[data-artifact-mode]")];
  S.workStripEl = S.root.querySelector("[data-work-strip]");
  S.agentsListEl = S.root.querySelector("[data-agents-list]");
  S.jobsListEl = S.root.querySelector("[data-jobs-list]");
  S.completedJobs = new Map();
  S.agentViewEl = S.root.querySelector("[data-agent-view]");
  S.queueEl = S.root.querySelector("[data-chat-queue]");
  S.queueListEl = S.root.querySelector("[data-chat-queue-list]");
  S.queueCountEl = S.root.querySelector("[data-chat-queue-count]");
  S.agentLogEl = S.root.querySelector("[data-agent-log]");
  S.agentBodyEl = S.root.querySelector("[data-agent-log-body]");
  S.agentLiveDock = S.root.querySelector("[data-agent-live-dock]");
  S.agentLiveSpin = S.root.querySelector("[data-agent-live-spin]");
  S.agentLiveLabel = S.root.querySelector("[data-agent-live-label]");
  S.STORE_MODEL = "fuse.chat.model";
  S.STORE_EFFORT = "fuse.chat.effort";
  S.STORE_TAB = "fuse.chat.historyTab";
  S.STORE_HISTORY_WIDTH = "fuse.chat.historyWidth";
  S.STORE_PROJECT_SORT = "fuse.chat.projectSort";
  S.STORE_PROJECT_FILTER = "fuse.chat.projectFilter";
  S.STORE_UNDO = "fuse.chat.undo.";
  S.HISTORY_WIDTH = { min: 14 * 16, max: 30 * 16, default: 19 * 16 };
  S.families = Array.isArray(S.config.families) ? S.config.families : [];
  S.logoByType = new Map(S.families.map((family) => [family.type, family.logo]));
  S.allModels = S.families.flatMap((family) =>
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
  S.modelsById = new Map(S.allModels.map((model) => [model.id, model]));
  S.DEVIN_EFFORTS = ["none", "minimal", "low", "medium", "high", "xhigh", "max"];
  S.effortByModel = {};
    try {
    S.effortByModel = JSON.parse(localStorage.getItem(S.STORE_EFFORT) || "{}") || {};
  } catch (_) {
    S.effortByModel = {};
  }
  S.modelId = pickInitialModel();
  S.sessionId = newSession();
  S.messages = [];
  S.threadRecaps = [];
  S.dismissedRecapAnchors = new Map();
  S.recapsEnabled = S.config.context?.recaps !== false;
  S.contextMessages = [];
  S.contextLive = false;
  S.undoStack = [];
  S.redoStack = [];
  S.composerVersion = 0;
  S.slashIndex = 0;
  S.slashKind = "slash";
  S.paletteTrigger = null;
  S.paletteItems = [];
  S.projectListingCache = null;
  S.liveMeter = null;
  S.meterAnchor = null;
  S.contextRecountAt = 0;
  S.compacting = false;
  S.compactingRequest = false;
  S.titleRegenRequest = false;
  S.contextWarned = false;
  S.contextAlertHidden = false;
  S.streaming = false;
  S.activeRun = null;
  S.serverRevision = 0;
  S.historyRevision = 0;
  S.contextRevision = Number(S.config.context?.revision || 0);
  S.contextEdit = 0;
  S.contextSaving = null;
  S.sending = false;
  S.loading = false;
  S.partialTranscript = false;
  S.healRunId = "";
  S.stoppingRunId = "";
  S.STORE_PENDING_TURN = "fuse.chat.pendingTurn";
  S.pendingTurns = new Map();
  Object.defineProperty(S, 'pendingTurn', {
    configurable: true,
    get: () => S.pendingTurns.get(String(S.currentId)) || null,
    set: value => {
      if (value) S.pendingTurns.set(String(value.id), value);
      else S.pendingTurns.delete(String(S.currentId));
    },
  });
    try {
    const saved = JSON.parse(sessionStorage.getItem(S.STORE_PENDING_TURN) || "null");
    for (const item of Array.isArray(saved) ? saved : [saved]) {
      if (item?.id && typeof item.body?.text === 'string' && item.body.command_id) S.pendingTurn = item;
    }
  } catch (_) {}
  S.pendingCreate = null;
  S.pendingProject = null;
  S.viewCursor = 0;
  S.threadWatermark = 0;
  S.cursor = 0;
  // Events at or below this were handled before a gap replay resent them.
  S.replayedThrough = 0;
  // The snapshot cursor the last gap replay started from.
  S.gapReplayFrom = 0;
  S.socket = null;
  S.reconnectTimer = 0;
  S.dockResizeObserver = null;
  S.reconnectAttempt = 0;
  S.syncEpoch = 0;
  S.loadEpoch = 0;
  S.navigation = 0;
  S.syncState = "connecting";
  S.syncLabel = "Connecting";
  S.syncInitialized = false;
  S.historyLoaded = false;
  S.heartbeatTimer = 0;
  S.pendingDeletes = new Set();
  S.pendingSave = null;
  S.pendingContext = null;
  S.searchActiveQuery = "";
  S.searchThreads = [];
  S.searchArchived = [];
  S.searchProjects = [];
  S.searchTimer = 0;
  S.searchGen = 0;
  S.HEARTBEAT_MS = 35000;
  S.AGE_TICK_MS = 30000;
  S.liveClockTimer = 0;
  S.runSpinOrigin = 0;
  S.runSpinTimer = 0;
  S.ageTimer = 0;
  S.ACTIVITY_SPINNERS = [
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
  S.SPINNER_HOLD_MS = 8000;
  S.SPINNER_MEMORY = 5;
  S.SPINNER_STILL = '<span class="cas-still" aria-hidden="true"></span>';
  S.spinWindow = -1;
  S.currentSpin = null;
  S.recentSpinIds = [];
  S.reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
  S.MOTION = { fast: 120, base: 200, slow: 280, snap: "cubic-bezier(0.2, 0, 0, 1)", glide: "cubic-bezier(0.32, 0.72, 0, 1)" };
  S.pickerSheetQuery = window.matchMedia("(max-width: 800px)");
  S.overlayMobile = S.pickerSheetQuery;
  S.overlayGhostUntil = 0;
  S.overlayGhostRect = null;
  S.renaming = false;
  S.renameSaving = null;
  S.transcriptDirty = false;
  S.drafts = new Map();
  S.draftFiles = new Map();
  S.stickToBottom = true;
  S.pickerOpen = false;
  S.pickerFilter = "";
  S.pickerIndex = 0;
  S.pickerMenuEl = null;
  S.pendingAttachments = [];
  S.threads = [];
  S.archivedThreads = [];
  S.projects = [];
  S.pickerHosts = [];
  S.currentId = null;
  S.showArchived = false;
  S.historyQuery = "";
  S.historyTab = "chats";
  S.pinDrag = null;
  S.pinDragClickBlock = 0;
  S.projectSort = "recent";
  S.projectFilter = "";
    try {
    S.historyTab = localStorage.getItem(S.STORE_TAB) === "projects" ? "projects" : "chats";
    S.projectSort = localStorage.getItem(S.STORE_PROJECT_SORT) === "project" ? "project" : "recent";
    S.projectFilter = localStorage.getItem(S.STORE_PROJECT_FILTER) || "";
  } catch (_) {
    S.historyTab = "chats";
  }
  S.saveTimer = 0;
  S.editingIndex = -1;
  S.editingFiles = [];
  S.lightbox = null;
  S.pop = null;
  S.popRowKey = null;
  S.searchOn = true;
  S.instructions = (S.config.context && S.config.context.instructions) || "";
  S.profile = (S.config.context && S.config.context.profile) || "";
  S.threadContext = "";
  S.memoryOpen = false;
  S.memoryTimer = 0;
  S.generation = 0;
  S.revision = 0;
  S.dirty = false;
  S.memoryDirty = false;
  S.projectAgentsOk = false;
  S.hintTimer = 0;
  S.hintFrame = 0;
  S.hintKey = "";
  S.hintHeldAt = 0;
  S.hintReleaseTimer = 0;
  S.pinnedHint = null;
  S.cacheProbe = null;
  S.cacheNoticed = new Set();
  S.ignoreScroll = false;
  S.artifactOpen = false;
  S.artifactMode = "preview";
  S.currentArtifact = null;
  S.skillsOpen = false;
  S.showThinking = false;
    try { S.showThinking = localStorage.getItem(S.STORE_THINKING) === "shown"; } catch (_) {}
  S.artifactSourceLoaded = "";
  // True while a subagent's transcript replaces the main one.
  S.agentsOpen = false;
  // The agent tree under the header: null follows activity, else the user's choice.
  S.agentsFold = null;
  S.agentsRows = [];
  S.agentsLoadedFor = "";
  S.agentViewId = "";
  S.agentLastEventAt = 0;
  S._agentTickRenderAt = 0;
  S._syncViewSent = "";
  S.agentRun = null;
  S.agentPollRequest = null;
  S.agentPollAgain = false;
  S.agentPendingEvents = [];
  S.agentMsgState = null;
  S.agentsTimer = 0;
  S.agentsPollTick = 0;
  S.agentsRefreshAt = 0;
  S.agentsViewGen = 0;
  S.agentsRequest = null;
  S.agentsRefreshAgain = false;
  S.agentLifecycleEpoch = 0;
  S.agentLifecycle = new Map();
  S.agentTraceRenderAt = 0;
  S.agentsFailCount = 0;
  S.agentViewFailCount = 0;
  S.agentStick = true;
  S.agentIgnoreScroll = false;
  S.agentFollowFrame = 0;
  S.agentTailInitialized = false;
  S.agentLastScrollTop = 0;
  S.agentResumeOnScroll = true;
  S.detailProbe = false;
  S.REVEAL_MS = 200;
  S.REVEAL_CALM = 28;
  S.REVEAL_FIRST = 240;
  S.compactCfg = Object.assign(
    { warn_ratio: 0.8, auto_ratio: 0.9, auto: true, default_window: 128000, strategy: "summarize" },
    S.config.compaction && typeof S.config.compaction === "object" ? S.config.compaction : {}
  );
  S.CONTEXT_RING = 2 * Math.PI * 12;
  S.saveChain = Promise.resolve();
  S.lastRunReceiptAt = Date.now();
  S.lastThreadScrollTop = 0;
  S.serverRunning = new Map();
  S.SLASH_COMMANDS = [
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
  S.activitySeq = 0;
  S.consoleSpinTimer = 0;
  S.consoleSpinFrame = 0;
  S.todoDismissedKeys = null;
  S.todoSections = {
    main: { el: S.root.querySelector("[data-live-todos]"), sig: "", items: "", expanded: false, shownKey: "", lingerUntil: 0, timer: 0, key: "" },
    agent: { el: S.root.querySelector("[data-agent-live-todos]"), sig: "", items: "", expanded: false, shownKey: "", lingerUntil: 0, timer: 0, key: "" },
  };
  S.todoButton = S.root.querySelector("[data-todo-header]");
  S.toolGestureOwner = null;
  S.pendingFullPaint = false;
  S.pendingAgentPaint = false;
  S.pendingAgentListPaint = false;
  S.toolMotions = new Map();
  S.permChip = S.root.querySelector("[data-perm-chip]");
  S.permsOpen = false;
  S.threadBypass = false;
  S.threadAuto = false;
  S.permCards = new Map();
  S.SETTLED_PHASES = new Set(["calling", "tools"]);
  S.pickerPinId = "";
  S.queuedTurns = [];
  S.acceptedQueueCommands = new Set();
  // Runs stopped by a queue's Send now: their cancellation must not hold the queue.
  S.sendNowRuns = new Set();
  // Runs this tab stopped: the Stop click already held the queue.
  S.locallyStoppedRuns = new Set();
  S.ATTACH_MAX = Number(S.config.limits && S.config.limits.attachments) || 4;
  S.jobsRows = [];
  S.historyMobile = window.matchMedia("(max-width: 800px)");
  S.searchToggle = S.root.querySelector("[data-history-search-toggle]");
  S.searchOpen = false;
}

    function defaultReasoningFor(type) {
    if (type === "grok") return ["low", "medium", "high", "xhigh"];
    if (type === "zai") return ["none", "high"];
    if (type === "kimi") return ["low", "high", "max"];
    if (type === "mimo") return ["none", "high"];
    if (type === "ollama") return ["none", "low", "medium", "high", "max"];
    return ["none", "minimal", "low", "medium", "high", "xhigh", "max"];
  }

    function resolveListedModel(id) {
    const wanted = String(id || "").trim();
    if (!wanted) return "";
    if (S.modelsById.has(wanted)) return wanted;
    if (!wanted.startsWith("devin/")) return "";
    const parts = wanted.slice("devin/".length).split("-").filter(Boolean);
    while (parts.length && (parts[parts.length - 1] === "usa" || parts[parts.length - 1] === "priority")) {
      parts.pop();
    }
    let familyTail = "";
    if (parts.length && parts[parts.length - 1] === "1m") {
      familyTail = parts.pop();
    }
    if (parts.length >= 2 && parts[parts.length - 1] === "fast" && S.DEVIN_EFFORTS.includes(parts[parts.length - 2])) {
      parts.pop();
    }
    let effort = "";
    if (parts.length && S.DEVIN_EFFORTS.includes(parts[parts.length - 1])) {
      effort = parts.pop();
    }
    if (familyTail) parts.push(familyTail);
    const base = "devin/" + parts.join("-");
    if (!S.modelsById.has(base)) return "";
    if (effort) {
      const model = S.modelsById.get(base);
      const efforts = (model && model.reasoning) || [];
      let mapped = effort;
      if (!efforts.includes(mapped) && (effort === "none" || effort === "minimal") && efforts.includes("low")) {
        mapped = "low";
      }
      if (efforts.includes(mapped)) S.effortByModel[base] = mapped;
    }
    return base;
  }

    function newSession() {
    if (crypto.randomUUID) return crypto.randomUUID();
    return "c" + Math.random().toString(36).slice(2) + Date.now().toString(36);
  }

// Where the composer text is kept: a chat's id, or for an unsent chat null
// (plain) or "p:<host>:<project>" (project session), so each new chat keeps
// its own draft and the sidebar can list it.
function draftKey() {
  if (S.currentId) return S.currentId;
  const project = S.pendingProject;
  return project ? `p:${project.host_id}:${project.project_id}` : null;
}

    function pickInitialModel() {
    const wanted = resolveListedModel((S.config.preselect || "").trim());
    if (wanted) return wanted;
    try {
      const stored = resolveListedModel(localStorage.getItem(S.STORE_MODEL) || "");
      if (stored) return stored;
    } catch (_) {}
    const preferred = ["grok", "chatgpt", "llama", "zai", "kimi", "mimo", "ollama", "openrouter"];
    for (const type of preferred) {
      const found = S.allModels.find((model) => model.type === type);
      if (found) return found.id;
    }
    return S.allModels[0] ? S.allModels[0].id : "";
  }

export { S, defaultReasoningFor, draftKey, newSession, resolveListedModel };
