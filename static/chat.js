import { createConsoleTheme } from './themes/console/index.js';
import { S, draftKey, newSession } from './chat/state.js';
import { escapeHtml, formatTurnDuration, formatTurnMeta, humanMs, runIsStopping, safeUrl, usageInOut } from './chat/core.js';
import { createOverlays } from './chat/overlays.js';
import { createNet } from './chat/net.js';
import { createTraces } from './chat/traces.js';
import { createContext } from './chat/context.js';
import { createPicker } from './chat/picker.js';
import { createPanels } from './chat/panels.js';
import { createPerms } from './chat/perms.js';
import { createArtifacts } from './chat/artifacts.js';
import { createTranscript } from './chat/transcript.js';
import { createSidebar } from './chat/sidebar.js';
import { createSync } from './chat/sync.js';
import { createAgents } from './chat/agents.js';
import { createComposer } from './chat/composer.js';
import { createTurns } from './chat/turns.js';
import { createBoot } from './chat/boot.js';
import { createDictation } from './chat/dictation.js';

// Composition root. Everything that remains here is irreducible orchestration:
// the theme seam (createConsoleTheme destructuring with its fixture anchor),
// feature construction in dependency order, event-listener wiring for DOM that
// several features share, FuseChatActions, and reset. Domain logic lives in
// static/chat/**; boot.js owns the startup sequence.
(() => {
  const root = S.root;
  if (!root) return;

    const {
    ACTION_ICONS,
    FILE_CHIP_ICON,
    agentMainRowHtml,
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
    todoBarHtml,
    todoRowsHtml,
    todoTrayHtml,
    toolDetailHtml,
    toolImage,
    updateAssistant,
    visibleAssistantError,
    workClocksLive,
    workHeadHtml,
  } = createConsoleTheme({
    get PERM_TOTAL_S() { return perms.PERM_TOTAL_S; },
    get REVEAL_CALM() { return S.REVEAL_CALM; },
    get REVEAL_FIRST() { return S.REVEAL_FIRST; },
    get REVEAL_MS() { return S.REVEAL_MS; },
    get SETTLED_PHASES() { return S.SETTLED_PHASES; },
    get activeAssistant() { return transcript.activeAssistant; },
    get activeRun() { return S.activeRun; },
    get activitySeq() { return S.activitySeq; },
    set activitySeq(value) { S.activitySeq = value; },
    get agentActivitySummary() { return agents.agentActivitySummary; },
    get agentResumeAvailable() { return agents.agentResumeAvailable; },
    get agentDurationSeconds() { return agents.agentDurationSeconds; },
    get agentIsStalled() { return agents.agentIsStalled; },
    get agentLive() { return agents.agentLive; },
    get agentLiveDock() { return S.agentLiveDock; },
    get agentLiveSpin() { return S.agentLiveSpin; },
    get agentRun() { return S.agentRun; },
    get agentStatusText() { return agents.agentStatusText; },
    get agentsRows() { return S.agentsRows; },
    get applySpinEl() { return sidebar.applySpinEl; },
    get assistantSegments() { return transcript.assistantSegments; },
    get autoEventLabel() { return traces.autoEventLabel; },
    get beginEdit() { return sidebar.beginEdit; },
    get commitEdit() { return sidebar.commitEdit; },
    get confirmDeleteAt() { return sidebar.confirmDeleteAt; },
    get consoleSpinFrame() { return S.consoleSpinFrame; },
    set consoleSpinFrame(value) { S.consoleSpinFrame = value; },
    get consoleSpinTimer() { return S.consoleSpinTimer; },
    set consoleSpinTimer(value) { S.consoleSpinTimer = value; },
    get copyText() { return sidebar.copyText; },
    get countsLabel() { return traces.countsLabel; },
    get deliveryTraces() { return traces.deliveryTraces; },
    get currentRunSpin() { return sidebar.currentRunSpin; },
    get detailProbe() { return S.detailProbe; },
    get editingFiles() { return S.editingFiles; },
    set editingFiles(value) { S.editingFiles = value; },
    get editingIndex() { return S.editingIndex; },
    set editingIndex(value) { S.editingIndex = value; },
    get escapeHtml() { return escapeHtml; },
    get estimateChars() { return context.estimateChars; },
    get estimateTrace() { return context.estimateTrace; },
    get executionIdentity() { return traces.executionIdentity; },
    get executionPending() { return traces.executionPending; },
    get executionState() { return traces.executionState; },
    get fmtPermCountdown() { return perms.fmtPermCountdown; },
    get formatTurnDuration() { return formatTurnDuration; },
    get formatTurnMeta() { return formatTurnMeta; },
    get humanMs() { return humanMs; },
    get hydratePermissionCards() { return perms.hydratePermissionCards; },
    get isCodeOutputTool() { return traces.isCodeOutputTool; },
    get isShellExecTool() { return traces.isShellExecTool; },
    get jobElapsedMs() { return agents.jobElapsedMs; },
    get jobsFor() { return agents.jobsFor; },
    get jobsRows() { return S.jobsRows; },
    get lastCurrentTrace() { return traces.lastCurrentTrace; },
    get latestPill() { return S.latestPill; },
    get latestSpin() { return S.latestSpin; },
    get liveDock() { return S.liveDock; },
    get liveDockFollowFrame() { return S.liveDockFollowFrame; },
    set liveDockFollowFrame(value) { S.liveDockFollowFrame = value; },
    get liveDockHideTimer() { return S.liveDockHideTimer; },
    set liveDockHideTimer(value) { S.liveDockHideTimer = value; },
    get liveDockLabel() { return S.liveDockLabel; },
    get liveDockShowFrame() { return S.liveDockShowFrame; },
    set liveDockShowFrame(value) { S.liveDockShowFrame = value; },
    get liveDockSpin() { return S.liveDockSpin; },
    get liveElapsedSeconds() { return transcript.liveElapsedSeconds; },
    get liveMeter() { return S.liveMeter; },
    get log() { return S.log; },
    get messages() { return S.messages; },
    get motionPaints() { return transcript.motionPaints; },
    get normalizeTodos() { return transcript.normalizeTodos; },
    get openLightbox() { return panels.openLightbox; },
    get paint() { return transcript.paint; },
    get partialTranscript() { return S.partialTranscript; },
    get pauseThreadFollow() { return transcript.pauseThreadFollow; },
    get pendingFullPaint() { return S.pendingFullPaint; },
    set pendingFullPaint(value) { S.pendingFullPaint = value; },
    get pendingToolPaint() { return transcript.pendingToolPaint; },
    set pendingToolPaint(value) { transcript.pendingToolPaint = value; },
    get permCardCopy() { return perms.permCardCopy; },
    get permCardSignature() { return perms.permCardSignature; },
    get permLeftS() { return perms.permLeftS; },
    get redoConversation() { return sidebar.redoConversation; },
    get redoStack() { return S.redoStack; },
    get reduceMotion() { return S.reduceMotion; },
    get restoreDisclosures() { return transcript.restoreDisclosures; },
    get continueAssistant() { return turns.continueAssistant; },
    get retryAssistant() { return turns.retryAssistant; },
    get rewindTo() { return sidebar.rewindTo; },
    get root() { return S.root; },
    get runIsActive() { return traces.runIsActive; },
    get runIsStopping() { return runIsStopping; },
    get runPhase() { return transcript.runPhase; },
    get safeUrl() { return safeUrl; },
    get saveDisclosures() { return transcript.saveDisclosures; },
    get scheduleTodoSync() { return transcript.scheduleTodoSync; },
    get scrollThread() { return transcript.scrollThread; },
    get sessionProject() { return sidebar.sessionProject; },
    get setToolReveal() { return transcript.setToolReveal; },
    get shellExitCode() { return traces.shellExitCode; },
    get showPop() { return overlays.showPop; },
    get showThinking() { return S.showThinking; },
    get snapThread() { return transcript.snapThread; },
    get sourceCatalog() { return sidebar.sourceCatalog; },
    get startLiveClock() { return transcript.startLiveClock; },
    get stickToBottom() { return S.stickToBottom; },
    get streaming() { return S.streaming; },
    get syncLatest() { return transcript.syncLatest; },
    get thread() { return S.thread; },
    get todoClosed() { return transcript.todoClosed; },
    get todoSettleLines() { return transcript.todoSettleLines; },
    get toolBackground() { return traces.toolBackground; },
    get toolCounts() { return traces.toolCounts; },
    get toolFailed() { return traces.toolFailed; },
    get toolGestureOwner() { return S.toolGestureOwner; },
    get toolIsCurrent() { return traces.toolIsCurrent; },
    get toolKey() { return traces.toolKey; },
    get toolMotions() { return S.toolMotions; },
    get toolPresentation() { return traces.toolPresentation; },
    get tracesSig() { return traces.tracesSig; },
    get transcriptDomOptions() { return transcript.transcriptDomOptions; },
    get undoConversation() { return sidebar.undoConversation; },
    get undoStack() { return S.undoStack; },
    get usageInOut() { return usageInOut; },
  });

  const ui = {
    ACTION_ICONS,
    FILE_CHIP_ICON,
    agentMainRowHtml,
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
    todoBarHtml,
    todoRowsHtml,
    todoTrayHtml,
    toolDetailHtml,
    toolImage,
    updateAssistant,
    visibleAssistantError,
    workClocksLive,
    workHeadHtml,
  };

  const chatApi = { clearChatState };

  const overlays = createOverlays({
    agents: { closeAgentView: (...args) => agents.closeAgentView(...args) },
    artifacts: { setArtifactOpen: (...args) => artifacts.setArtifactOpen(...args) },
    context: { closeContextPop: (...args) => context.closeContextPop(...args) },
    panels: { closeLightbox: (...args) => panels.closeLightbox(...args), closeViewMenuDom: (...args) => panels.closeViewMenuDom(...args), setMemoryOpen: (...args) => panels.setMemoryOpen(...args), setSkillsOpen: (...args) => panels.setSkillsOpen(...args), setViewMenuOpen: (...args) => panels.setViewMenuOpen(...args) },
    perms: { setPermsOpen: (...args) => perms.setPermsOpen(...args) },
    picker: { closePicker: (...args) => picker.closePicker(...args) },
    sidebar: { setHistoryOpen: (...args) => sidebar.setHistoryOpen(...args), setSearchOpen: (...args) => sidebar.setSearchOpen(...args) },
    sync: { showFailure: (...args) => sync.showFailure(...args) },
    turns: { closeRewind: (...args) => turns.closeRewind(...args) },
  });

  const net = createNet({
    picker: { setHint: (...args) => picker.setHint(...args) },
    sync: { syncStatus: (...args) => sync.syncStatus(...args) },
    turns: { queueStoreKey: (...args) => turns.queueStoreKey(...args) },
  });

  const traces = createTraces({
    agents: { scheduleAgentView: (...args) => agents.scheduleAgentView(...args) },
    transcript: { activeAssistant: (...args) => transcript.activeAssistant(...args), normalizeTodos: (...args) => transcript.normalizeTodos(...args), startLiveClock: (...args) => transcript.startLiveClock(...args) },
    ui,
  });

  const context = createContext({
    net,
    picker: { effortLabel: (...args) => picker.effortLabel(...args), holdHint: (...args) => picker.holdHint(...args), pinHint: (...args) => picker.pinHint(...args), releaseHint: (...args) => picker.releaseHint(...args), setHint: (...args) => picker.setHint(...args), unpinHint: (...args) => picker.unpinHint(...args) },
    sync: { acceptThread: (...args) => sync.acceptThread(...args), showFailure: (...args) => sync.showFailure(...args) },
    traces,
    ui,
  });

  const picker = createPicker({
    composer: { persist: (...args) => composer.persist(...args), placeComposerPop: (...args) => composer.placeComposerPop(...args), syncComposer: (...args) => composer.syncComposer(...args) },
    context,
    net,
    overlays,
    perms: { setPermsOpen: (...args) => perms.setPermsOpen(...args) },
    sidebar: { providerLogo: (...args) => sidebar.providerLogo(...args) },
  });

  const panels = createPanels({
    agents: { renderAgentView: (...args) => agents.renderAgentView(...args), setAgentsOpen: (...args) => agents.setAgentsOpen(...args) },
    artifacts: { setArtifactOpen: (...args) => artifacts.setArtifactOpen(...args) },
    composer: { pinVisualViewport: (...args) => composer.pinVisualViewport(...args) },
    net,
    overlays,
    perms: { setPermsOpen: (...args) => perms.setPermsOpen(...args) },
    picker,
    sidebar: { setHistoryOpen: (...args) => sidebar.setHistoryOpen(...args), threadById: (...args) => sidebar.threadById(...args) },
    sync: { acceptContext: (...args) => sync.acceptContext(...args), showFailure: (...args) => sync.showFailure(...args), syncStatus: (...args) => sync.syncStatus(...args) },
    transcript: { paint: (...args) => transcript.paint(...args) },
  });

  const perms = createPerms({
    composer: { placeComposerPop: (...args) => composer.placeComposerPop(...args) },
    net,
    overlays,
    picker,
    sync: { showFailure: (...args) => sync.showFailure(...args) },
    ui,
  });

  const artifacts = createArtifacts({
    agents: { setAgentsOpen: (...args) => agents.setAgentsOpen(...args) },
    composer: { scheduleFitChatBar: (...args) => composer.scheduleFitChatBar(...args) },
    net,
    overlays,
    panels,
    picker,
    sidebar: { setHistoryOpen: (...args) => sidebar.setHistoryOpen(...args), syncHistory: (...args) => sidebar.syncHistory(...args) },
  });

  const transcript = createTranscript({
    agents: { agentLive: (...args) => agents.agentLive(...args), agentSeedArticle: (...args) => agents.agentSeedArticle(...args), renderAgentView: (...args) => agents.renderAgentView(...args), renderAgentsList: (...args) => agents.renderAgentsList(...args), restoreAgentScroll: (...args) => agents.restoreAgentScroll(...args), syncAgentLiveDock: (...args) => agents.syncAgentLiveDock(...args) },
    artifacts,
    composer: { playDockTransition: (...args) => composer.playDockTransition(...args), syncComposer: (...args) => composer.syncComposer(...args), syncEmptyComposer: (...args) => composer.syncEmptyComposer(...args) },
    context,
    sidebar: { syncTitle: (...args) => sidebar.syncTitle(...args) },
    traces,
    turns: { syncEmptyReplyRetry: (...args) => turns.syncEmptyReplyRetry(...args) },
    ui,
  });

  const sidebar = createSidebar({
    agents: { agentLive: (...args) => agents.agentLive(...args), resetAgents: (...args) => agents.resetAgents(...args), setAgentsOpen: (...args) => agents.setAgentsOpen(...args) },
    artifacts,
    chatApi,
    composer: { closeSlash: (...args) => composer.closeSlash(...args), persist: (...args) => composer.persist(...args), refreshProjectListing: (...args) => composer.refreshProjectListing(...args), renderAttachList: (...args) => composer.renderAttachList(...args), resizeInput: (...args) => composer.resizeInput(...args), scheduleFitChatBar: (...args) => composer.scheduleFitChatBar(...args), syncComposer: (...args) => composer.syncComposer(...args), syncEmptyComposer: (...args) => composer.syncEmptyComposer(...args) },
    context,
    net,
    overlays,
    panels,
    perms,
    picker,
    sync: { acceptThread: (...args) => sync.acceptThread(...args), flushPending: (...args) => sync.flushPending(...args), healStuckRun: (...args) => sync.healStuckRun(...args), reconcileThread: (...args) => sync.reconcileThread(...args), setRunning: (...args) => sync.setRunning(...args), setThreadUrl: (...args) => sync.setThreadUrl(...args), showFailure: (...args) => sync.showFailure(...args), syncFailure: (...args) => sync.syncFailure(...args), syncStatus: (...args) => sync.syncStatus(...args) },
    traces,
    transcript,
    turns: { closeRewind: (...args) => turns.closeRewind(...args), completeTurn: (...args) => turns.completeTurn(...args), drainQueue: (...args) => turns.drainQueue(...args), loadQueue: (...args) => turns.loadQueue(...args), parseSlashCommand: (...args) => turns.parseSlashCommand(...args) },
    ui,
  });

  const sync = createSync({
    agents: { compactionMarkerText: (...args) => agents.compactionMarkerText(...args), pollAgentView: (...args) => agents.pollAgentView(...args), refreshAgents: (...args) => agents.refreshAgents(...args), renderAgentsList: (...args) => agents.renderAgentsList(...args), scheduleAgentView: (...args) => agents.scheduleAgentView(...args) },
    artifacts,
    composer: { acceptDrafts: (...args) => composer.acceptDrafts(...args), applyRemoteDraft: (...args) => composer.applyRemoteDraft(...args), syncComposer: (...args) => composer.syncComposer(...args) },
    context,
    net,
    overlays,
    panels,
    perms,
    picker,
    sidebar,
    traces,
    transcript,
    turns: { drainQueue: (...args) => turns.drainQueue(...args), holdQueueForStop: (...args) => turns.holdQueueForStop(...args), renderQueue: (...args) => turns.renderQueue(...args), retireAcceptedQueue: (...args) => turns.retireAcceptedQueue(...args), syncEmptyReplyRetry: (...args) => turns.syncEmptyReplyRetry(...args) },
    ui,
  });

  const agents = createAgents({
    artifacts,
    composer: { scheduleFitChatBar: (...args) => composer.scheduleFitChatBar(...args) },
    context,
    net,
    overlays,
    panels,
    perms,
    picker,
    sidebar,
    sync,
    traces,
    transcript,
    ui,
  });

  const composer = createComposer({
    context,
    net,
    overlays,
    panels,
    perms,
    picker,
    sidebar,
    sync,
    traces,
    transcript,
    turns: { closeRewind: (...args) => turns.closeRewind(...args), completeTurn: (...args) => turns.completeTurn(...args), openRewind: (...args) => turns.openRewind(...args), retryLastTurn: (...args) => turns.retryLastTurn(...args) },
    ui,
  });

  const turns = createTurns({
    composer,
    context,
    net,
    overlays,
    panels,
    picker,
    sidebar,
    sync,
    traces,
    transcript,
    ui,
  });

  const boot = createBoot({
    artifacts,
    composer,
    sidebar,
    sync,
  });

  if (S.config.speech) createDictation({ picker });

    if (S.viewMenu && S.viewMenu.parentElement !== document.body) document.body.append(S.viewMenu);

    if (S.viewBackdrop && S.viewBackdrop.parentElement !== document.body) document.body.append(S.viewBackdrop);

    S.contextTabButtons.forEach((btn) => {
    btn.addEventListener("click", () => panels.setContextTab(btn.getAttribute("data-context-tab")));
  });

    S.ACTIVITY_SPINNERS.push(...(window.FuseSpinnerNext || []));

    S.ACTIVITY_SPINNERS.push(...(window.FuseSpinnerFuture || []));

    document.addEventListener("click", (event) => {
    if (event.detail === 0) return; // Keyboard/programmatic activation is not a touch ghost.
    if (!overlays.overlayIsMobile() || performance.now() > S.overlayGhostUntil) return;
    if (event.target.closest?.(".chat-menu-close, [data-picker-close], [data-memory-close], [data-skills-close], [data-perms-close], [data-artifact-close], [data-view-close], [data-account-close], [data-rewind-close]")) return;
    const r = S.overlayGhostRect;
    if (r && (event.clientX < r.left || event.clientX > r.right || event.clientY < r.top || event.clientY > r.bottom)) return;
    event.preventDefault();
    event.stopPropagation();
  }, true);

    document.addEventListener("touchend", (event) => {
    const close = event.target.closest?.(".chat-menu-close, [data-picker-close], [data-memory-close], [data-skills-close], [data-artifact-close], [data-view-close], [data-account-close], [data-rewind-close]");
    if (!close) return;
    event.preventDefault();
    close.click();
  }, { capture: true, passive: false });

    if (S.config.vault === "locked" && S.config.ssh_hosts && window.fuseUI) {
    window.fuseUI.vaultGate({
      uid: S.config.uid,
      vault: S.config.vault,
      hasSsh: true,
      message: net.VAULT_WHAT,
      onUnlocked: () => {
        S.config.vault = "unlocked";
      },
    });
  }

    if (sync.recapsToggle) {
    sync.recapsToggle.checked = S.recapsEnabled;
    sync.recapsToggle.addEventListener("change", async () => {
      sync.recapsToggle.disabled = true;
      try {
        const data = await net.api("/chat/context", { method: "PUT", body: JSON.stringify({
          recaps: sync.recapsToggle.checked, expected_revision: S.contextRevision, command_id: newSession(),
        }) });
        sync.acceptContext(data);
      } catch (error) {
        sync.recapsToggle.checked = S.recapsEnabled;
        sync.showFailure("Could not save recap preference", error);
      } finally { sync.recapsToggle.disabled = false; }
    });
  }

    let runReconcilePending = false;

    S.root.querySelector("[data-sync-retry]").addEventListener("click", () => {
    if (!net.requireLive()) return;
    if (S.pendingTurn && S.pendingTurn.id === S.currentId) turns.completeTurn(S.pendingTurn.body.text, S.pendingTurn.body.attachments || [], { truncatedTo: S.pendingTurn.body.replace_from, undoSnap: S.pendingTurn.undoSnap });
    else picker.setHint("Open the chat with the unconfirmed send to retry it.", true);
  });

    const discardPending = document.createElement('button');
    discardPending.type = 'button';
    discardPending.dataset.pendingDiscard = '';
    discardPending.hidden = true;
    discardPending.textContent = 'Review unconfirmed sends';
    discardPending.addEventListener('click', async () => {
      for (const pending of [...S.pendingTurns.values()]) {
        if (await sidebar.askConfirm(`Discard the unconfirmed send in chat ${pending.id}? It may already have been accepted; check the chat before sending again.\n\n${pending.body.text.slice(0, 240)}`, { confirmLabel: 'Discard' })) {
          S.pendingTurns.delete(String(pending.id));
        }
      }
      sync.setPendingTurn(S.pendingTurn);
      sync.syncStatus();
    });
    S.root.querySelector('.chat-sync')?.append(discardPending);

    window.addEventListener("online", () => sync.connectSync());

    window.addEventListener("offline", () => net.markDisconnected());

    window.addEventListener("pageshow", (event) => { if (event.persisted) sync.connectSync(); });

    document.addEventListener("visibilitychange", () => { if (!document.hidden && !net.isLive()) sync.connectSync(); });

    window.addEventListener("beforeunload", (event) => {
    if (S.dirty || S.memoryDirty || S.pendingTurns.size || S.pendingCreate) { event.preventDefault(); event.returnValue = ""; }
  });

    window.fuseSpinDebug = {
    state: () => ({ current: S.currentSpin && S.currentSpin.id, recent: [...S.recentSpinIds], window: S.spinWindow }),
    pick: sidebar.pickRandomSpin,
    tick: sidebar.currentRunSpin,
  };

    composer.refreshSkills();

    window.addEventListener("focus", composer.refreshSkills);

    document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") composer.refreshSkills();
  });

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
    read_transcript: "Transcript",
  };

    Object.values(S.todoSections).forEach((sec) => sec.el && sec.el.addEventListener("click", transcript.onTodoClick));
    if (S.todoButton) S.todoButton.addEventListener("click", transcript.onTodoButton);

    S.root.addEventListener("error", (event) => {
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

    S.root.addEventListener("pointerdown", transcript.beginToolGesture, true);

    S.root.addEventListener("click", () => { if (S.toolGestureOwner) transcript.flushToolGesture(); });

    S.root.addEventListener("keydown", event => {
    if (["Enter", " "].includes(event.key) && !event.repeat) transcript.beginToolGesture(event);
  }, true);

    document.addEventListener("pointerup", transcript.endToolGesture, true);

    document.addEventListener("pointercancel", transcript.flushToolGesture, true);

    document.addEventListener("keyup", event => { if (["Enter", " "].includes(event.key)) transcript.endToolGesture(); }, true);

    window.addEventListener("blur", transcript.flushToolGesture);

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

    if (S.log) {
    S.log.addEventListener("click", async (event) => {
      const save = event.target.closest("[data-perm-save]");
      const reject = event.target.closest("[data-perm-reject]");
      const card = event.target.closest("[data-perm-id]");
      if (!card || (!save && !reject)) return;
      const id = card.getAttribute("data-perm-id");
      try {
        if (reject) {
          await net.api("/skills/proposals/" + encodeURIComponent(id) + "/reject", { method: "POST", body: "{}" });
        } else {
          const body = {
            slug: card.querySelector("[data-perm-slug]")?.value,
            description: card.querySelector("[data-perm-desc]")?.value,
            body: card.querySelector("[data-perm-body]")?.value,
            enable: Boolean(card.querySelector("[data-perm-enable]")?.checked),
            replace: Boolean(card.querySelector("[data-perm-replace]")?.checked),
          };
          await net.api("/skills/proposals/" + encodeURIComponent(id) + "/confirm", { method: "POST", body: JSON.stringify(body) });
        }
        await composer.refreshSkills();
        await sync.reconcileThread();
      } catch (error) {
        sync.showFailure("Skill was not saved", error);
      }
    });
  }

    if (perms.permQueueList) {
    perms.permQueueList.addEventListener("click", (event) => {
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
      const more = event.target.closest("[data-perm-extend]");
      if (more) {
        const card = more.closest("[data-perm-card]");
        if (card) perms.extendPermCard(card.getAttribute("data-perm-card"));
        return;
      }
      const btn = event.target.closest("[data-perm-decide]");
      if (!btn) return;
      const card = btn.closest("[data-perm-card]");
      if (!card) return;
      const id = card.getAttribute("data-perm-card");
      const action = btn.getAttribute("data-perm-decide");
      if (action) perms.postPermDecision(id, action);
    });
  }

    if (S.permChip) {
    S.permChip.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
      perms.setPermsOpen(!S.permsOpen);
    });
  }
  perms.applyNewChatMode();

    document.addEventListener("click", (event) => {
    if (!S.permsOpen) return;
    const path = typeof event.composedPath === "function" ? event.composedPath() : [];
    const inside = path.some((node) => {
      if (!node || node.nodeType !== 1) return false;
      return node === perms.permHost || node === perms.permsMenuEl || node.classList.contains("chat-perms-menu") || node.hasAttribute("data-perm-host");
    });
    if (inside) return;
    if (event.target.closest("[data-perm-host], .chat-perms-menu")) return;
    perms.setPermsOpen(false);
  });

    S.pickerSheetQuery.addEventListener("change", () => {
    if (perms.permsMenuEl) {
      perms.permsMenuEl.remove();
      perms.permsMenuEl = null;
    }
    if (S.permsOpen) {
      perms.renderPermsMenu();
      if (perms.permsMenuEl) perms.permsMenuEl.hidden = false;
      perms.placePermsMenu();
      if (perms.threadPermData) perms.paintPermsPanel();
      perms.refreshPerms();
    }
  });

    window.addEventListener("resize", () => { if (S.permsOpen) perms.placePermsMenu(); });

    document.addEventListener("visibilitychange", () => {
    // Background tabs throttle rAF to nothing; repaint live streams on return.
    if (!document.hidden) {
      if (S.streaming) transcript.schedulePaintLast();
      if (S.agentsOpen && S.agentViewId) agents.scheduleAgentView();
    }
  });

    window.addEventListener("resize", () => { if (S.pickerOpen) picker.placePicker(); });

    document.addEventListener("click", (event) => {
    if (!S.pickerOpen) return;
    if (S.pickerHost.contains(event.target)) return;
    if (S.pickerMenuEl && S.pickerMenuEl.contains(event.target)) return;
    picker.closePicker();
  });

    S.log.addEventListener("click", async (event) => {
    const previewBtn = event.target.closest("[data-image-preview]");
    if (previewBtn) {
      event.preventDefault();
      panels.openLightbox(previewBtn.getAttribute("data-image-preview"), previewBtn.getAttribute("data-image-name") || "");
      return;
    }
    const removeBtn = event.target.closest("[data-remove-attachment]");
    if (removeBtn) {
      event.preventDefault();
      const row = removeBtn.closest("[data-user-files]");
      if (!row) return;
      composer.removeAttachment(
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
      artifacts.showArtifact(openArt.getAttribute("data-open-artifact")).catch((error) => sync.showFailure("Could not open artifact", error));
      return;
    }
    const openAgentEl = event.target.closest("[data-open-agent]");
    if (openAgentEl) {
      event.preventDefault();
      event.stopPropagation();
      agents.showAgent(openAgentEl.getAttribute("data-open-agent"));
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
  S.log.addEventListener("contextmenu", (event) => {
    const link = event.target.closest("a[href]");
    if (!link || !S.log.contains(link)) return;
    let url;
    try { url = new URL(link.href, location.href); } catch (_) { return; }
    if (url.protocol !== "http:" && url.protocol !== "https:") return;
    event.preventDefault();
    overlays.showPop(null, [
      { label: "Open in browser", run: () => window.open(url.href, "_blank", "noopener") },
      { label: "Copy link", run: () => navigator.clipboard.writeText(url.href).then(() => picker.setHint("Copied link.")) },
    ], { x: event.clientX, y: event.clientY });
  });

    // Code blocks are shared by the main transcript and the agent reader.
  S.root.addEventListener("click", async (event) => {
    const btn = event.target.closest("[data-copy-code]");
    if (!btn) return;
    const code = btn.closest(".chat-code")?.querySelector("code");
    if (!code) return;
    try {
      await navigator.clipboard.writeText(code.textContent || "");
      picker.setHint("Copied to clipboard.");
      btn.innerHTML = ACTION_ICONS.copied;
      btn.setAttribute("aria-label", "Copied");
      setTimeout(() => {
        btn.innerHTML = ACTION_ICONS.copy;
        btn.setAttribute("aria-label", "Copy");
      }, 1200);
    } catch (error) {
      sync.showFailure("Clipboard copy failed", error);
    }
  });

    // Let a vertical gesture leave a capped code pane once it reaches either edge.
  let codeTouch = null;

    S.log.addEventListener("touchstart", (event) => {
    const pane = event.target.closest(".chat-code pre");
    codeTouch = pane && event.touches.length === 1
      ? { x: event.touches[0].clientX, y: event.touches[0].clientY }
      : null;
  }, { passive: true });

    S.log.addEventListener("touchmove", (event) => {
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
      S.thread.scrollTop -= deltaY;
    }
  }, { passive: false });

    S.log.addEventListener("touchend", () => { codeTouch = null; }, { passive: true });

    S.thread.addEventListener("wheel", event => {
    // Pinch/ctrl-zoom and sideways swipes are not a request to stop following.
    if (event.ctrlKey || Math.abs(event.deltaX) > Math.abs(event.deltaY) || picker.innerScrolls(event.target, event.deltaY)) return;
    if (event.deltaY < 0) transcript.pauseThreadFollow();
    else if (event.deltaY > 0) transcript.resumeFollowOnScroll = true;
  }, { passive: true });

    S.thread.addEventListener("pointerdown", event => {
    if (event.target === S.thread && event.clientX >= S.thread.getBoundingClientRect().right - 18) {
      transcript.pauseThreadFollow();
      transcript.resumeFollowOnScroll = true;
    }
  }, { passive: true });

    let threadTouchY = null;

    S.thread.addEventListener("touchstart", event => { threadTouchY = event.touches[0]?.clientY ?? null; }, { passive: true });

    S.thread.addEventListener("touchmove", event => {
    const y = event.touches[0]?.clientY;
    if (y != null && threadTouchY != null && picker.innerScrolls(event.target, threadTouchY - y)) { threadTouchY = y; return; }
    if (y != null && threadTouchY != null && y > threadTouchY + 1) transcript.pauseThreadFollow();
    else if (y != null && threadTouchY != null && y < threadTouchY - 1) transcript.resumeFollowOnScroll = true;
    threadTouchY = y ?? null;
  }, { passive: true });

    S.thread.addEventListener("keydown", event => {
    if (["ArrowUp", "PageUp", "Home"].includes(event.key) && !event.target.closest("input, textarea, [contenteditable]")) transcript.pauseThreadFollow();
    else if (["ArrowDown", "PageDown", "End"].includes(event.key)) transcript.resumeFollowOnScroll = true;
  });

    S.thread.addEventListener("scroll", () => {
    if (S.ignoreScroll) return;
    const top = S.thread.scrollTop;
    if (transcript.threadFollowFrame && S.stickToBottom) {
      S.lastThreadScrollTop = top;
      return;
    }
    S.stickToBottom = transcript.nearBottom() && (S.stickToBottom || (transcript.resumeFollowOnScroll && top > S.lastThreadScrollTop));
    S.lastThreadScrollTop = top;
    transcript.syncLatest();
  });

    S.root.querySelector("[data-chat-latest]")?.addEventListener("click", () => {
    S.stickToBottom = true;
    if (S.streaming || S.reduceMotion.matches) {
      transcript.scrollThread(true);
      return;
    }
    // Idle: glide the last stretch instead of teleporting. Far jumps first
    // snap to a screen above the end so the glide stays short.
    const target = transcript.threadFollowTarget();
    if (target - S.thread.scrollTop > S.thread.clientHeight * 3) S.thread.scrollTop = target - S.thread.clientHeight * 1.5;
    S.thread.scrollTo({ top: target, behavior: "smooth" });
  });

    window.addEventListener("storage", (event) => {
    if (S.currentId && (event.key === turns.queueStoreKey() || event.key?.startsWith(turns.queueStoreKey() + '.item.') || event.key === turns.queueHoldKey() || event.key?.startsWith(turns.queueHoldKey() + '.'))) {
      turns.loadQueue();
      if (!traces.runIsActive(S.activeRun)) turns.drainQueue();
    }
  });

    if (S.queueListEl)
    S.queueListEl.addEventListener("click", (event) => {
      const moreBtnEl = event.target.closest("[data-queue-more]");
      if (moreBtnEl) {
        const item = S.queuedTurns[Number(moreBtnEl.getAttribute("data-queue-more"))];
        S.queueMenuId = item && S.queueMenuId !== item.id ? item.id : "";
        turns.renderQueue();
        if (S.queueMenuId) S.queueListEl.querySelector(`[data-queue-id="${CSS.escape(S.queueMenuId)}"] [data-queue-edit]`)?.focus({ preventScroll: true });
        return;
      }
      if (event.target.closest("[data-queue-edit], [data-queue-cancel]")) S.queueMenuId = "";
      const sendBtnEl = event.target.closest("[data-queue-send]");
      const editBtnEl = event.target.closest("[data-queue-edit]");
      const undoBtnEl = event.target.closest("[data-queue-cancel]");
      if (sendBtnEl) {
        const index = Number(sendBtnEl.getAttribute("data-queue-send"));
        const item = S.queuedTurns[index];
        if (!item || turns.queueItemSending(item)) return;
        turns.sendQueuedNow(item);
        return;
      }
      if (editBtnEl) {
        const index = Number(editBtnEl.getAttribute("data-queue-edit"));
        const item = S.queuedTurns[index];
        if (!item || turns.queueItemSending(item)) return;
        // Queue what is in the box first: if that cannot be saved, editing
        // would overwrite it with the queued text and lose it.
        if ((S.input.value.trim() || S.pendingAttachments.length) && !turns.enqueueTurn(S.input.value, S.pendingAttachments)) {
          picker.setHint("Could not queue the message in the box (browser storage is full or blocked).", true);
          return;
        }
        turns.removeQueuedTurn(index);
        S.input.value = item.text;
        S.pendingAttachments = (item.attachments || []).slice();
        composer.renderAttachList();
        S.drafts.set(draftKey(), item.text);
        S.input.focus();
        composer.resizeInput();
        composer.syncComposer();
        return;
      }
      if (undoBtnEl) {
        const index = Number(undoBtnEl.getAttribute("data-queue-cancel"));
        if (S.queuedTurns[index] && !turns.queueItemSending(S.queuedTurns[index])) turns.removeQueuedTurn(index);
      }
    });
    const closeQueueMenu = () => { if (S.queueMenuId) { S.queueMenuId = ""; turns.renderQueue(); } };
    document.addEventListener("pointerdown", (event) => { if (!event.target.closest?.(".chat-queue-more-wrap")) closeQueueMenu(); });
    document.addEventListener("keydown", (event) => { if (event.key === "Escape" && S.queueMenuId) closeQueueMenu(); });

    function clearChatState({ keepList = false } = {}) {
    const stayOnList = keepList && S.historyMobile.matches && S.root.classList.contains("is-history");
    transcript.setPartialTranscript(false);
    S.drafts.set(draftKey(), S.input.value);
    S.draftFiles.set(draftKey(), S.pendingAttachments);
    S.stoppingRunId = "";
    S.activeRun = null;
    S.cacheProbe = null;
    sync.setRunning();
    S.permCards.clear();
    perms.renderPermQueue();
    perms.threadPermData = null;
    perms.permsFilter = "";
    S.navigation += 1;
    S.loading = false;
    S.generation += 1;
    window.clearTimeout(S.saveTimer);
    S.messages = [];
    S.threadRecaps = [];
    sidebar.resetHistoryStacks();
    S.currentId = null;
    S.threadEffort = null;
    S.serverRevision = 0;
    S.viewCursor = 0;
    S.threadWatermark = 0;
    S.contextCut = null;
    // The queue belongs to the thread it was typed in (persisted under that
    // thread's key); never let it drain into the next chat.
    S.acceptedQueueCommands = new Set();
    turns.loadQueue();
    S.pendingCreate = null;
    S.pendingProject = null;
    perms.applyNewChatMode();
    S.pendingSave = null;
    S.input.value = S.drafts.get(null) || "";
    composer.resizeInput();
    S.dirty = false;
    S.transcriptDirty = false;
    S.revision += 1;
    S.editingIndex = -1;
    S.editingFiles = [];
    S.sessionId = newSession();
    S.liveMeter = null;
    S.compacting = false;
    S.contextWarned = false;
    S.contextAlertHidden = false;
    S.threadContext = "";
    S.searchOn = true;
    S.pendingAttachments = S.draftFiles.get(null) || [];
    composer.renderAttachList();
    sync.setThreadUrl(null, { stay: stayOnList });
    transcript.paint();
    sidebar.renderHistory();
    // The chip showed the left chat's effort; the empty chat has its own.
    picker.renderPickerButton();
    picker.defaultHint();
    panels.syncMemoryUi();
    if (!stayOnList) sidebar.setHistoryOpen(false);
    S.currentArtifact = null;
    artifacts.setArtifactOpen(false);
    // Every path that leaves a thread for an empty chat (new chat, archive or
    // delete of the open one, project removal) drops its agents, commands and
    // any subagent transcript on screen.
    agents.resetAgents();
    sync.syncStatus();
    if (window.matchMedia("(hover: hover) and (pointer: fine)").matches) S.input.focus();
    if (window.FuseComposerSpell) {
      FuseComposerSpell.rebuildLearned([]);
      FuseComposerSpell.refresh();
    }
    S.projectListingCache = null;
  }

    async function resetChat() {
    if (S.sending) return;
    transcript.dismissRecaps(false);
    const nav = ++S.navigation;
    try {
      await sync.flushPending();
    } catch (error) {
      sync.showFailure("Start a new chat after the current chat is saved", error);
      return;
    }
    if (nav === S.navigation) {
      sidebar.setHistoryTab("chats");
      clearChatState();
    }
  }

    // The composer always talks to the main agent: a message sent (or queued)
  // while a subagent is on screen brings the main transcript back to show it.
  function showMainForSend() {
    if (S.agentsOpen && (S.input.value.trim() || (S.attachList && !S.attachList.hidden))) agents.closeAgentView();
  }

    function sendFromComposer() {
    showMainForSend();
    turns.send();
  }

    S.form.addEventListener("submit", (event) => {
    event.preventDefault();
    sendFromComposer();
  });

    if (S.contextBtn) {
    S.contextBtn.addEventListener("click", (event) => {
      event.preventDefault();
      if (!S.contextPop || S.contextWrap.hidden) return;
      const open = S.contextPop.hidden;
      S.contextPop.hidden = !open;
      S.contextBtn.setAttribute("aria-expanded", String(open));
      if (open) {
        composer.closeSlash();
        turns.closeRewind();
        context.syncContextMeter();
        composer.placeComposerPop(S.contextPop, S.contextBtn, 30 * 16);
      }
    });
    window.addEventListener("resize", () => composer.placeComposerPop(S.contextPop, S.contextBtn, 30 * 16));
  }

    overlays.backLayerFollowsHidden(S.contextPop, "context");

    if (S.contextPop) {
    S.contextPop.addEventListener("click", (event) => {
      if (event.target.closest("[data-context-pop-compact]")) context.runCompact("");
    });
  }

    if (S.contextAlertCompact) S.contextAlertCompact.addEventListener("click", () => context.runCompact(""));

    if (S.contextAlertDismiss) {
    S.contextAlertDismiss.addEventListener("click", () => {
      S.contextAlertHidden = true;
      context.hideContextAlert();
    });
  }

    S.input.addEventListener("input", () => {
    S.composerVersion += 1;
    S.drafts.set(draftKey(), S.input.value);
    if (!S.currentId) {
      // The unsent chat's sidebar row follows what is typed.
      window.clearTimeout(S.draftRowTimer);
      S.draftRowTimer = window.setTimeout(() => sidebar.renderHistory(), 250);
    }
    composer.resizeInput();
    composer.syncComposer();
    context.queueContextMeter();
    composer.syncEmptyComposer();
    S.slashIndex = 0;
    if (window.FuseComposerSpell && FuseComposerSpell.composing) {
      composer.closeSlash();
      return;
    }
    composer.renderComposerSuggestions();
    if (window.FuseComposerSpell) FuseComposerSpell.debounceCheck();
  });

    S.input.addEventListener("focus", () => {
    document.documentElement.scrollTop = 0;
    document.body.scrollTop = 0;
    composer.pinVisualViewport();
  });

    S.input.addEventListener("blur", () => {
    window.setTimeout(composer.syncEmptyComposer, 0);
  });

    ["focusin", "focusout"].forEach((type) => S.root.addEventListener(type, (event) => {
    const target = event.target instanceof Element ? event.target : null;
    const related = event.relatedTarget instanceof Element ? event.relatedTarget : null;
    const field = "textarea, input, [contenteditable='true']";
    const moved =
      Boolean(target?.matches(field) || related?.matches(field) ||
        target?.closest(".chat-sheet, .chat-view-menu") ||
        related?.closest(".chat-sheet, .chat-view-menu"));
    if (!moved) return;
    composer.pinVisualViewport();
    window.setTimeout(composer.pinVisualViewport, 100);
    window.setTimeout(composer.pinVisualViewport, 350);
    window.setTimeout(composer.pinVisualViewport, 700);
  }));

    S.input.addEventListener("keydown", (event) => {
    if (event.isComposing || event.keyCode === 229) return;

    const spellOpen = window.FuseComposerSpell && FuseComposerSpell.isOpen();
    const slashOpen = S.slashMenu && !S.slashMenu.hidden;
    const extraOpen = composer.extraPaletteOpen();
    const slashItems = slashOpen && S.slashKind === "slash" ? composer.filteredSlash() : S.paletteItems;

    // Dispatch local commands ahead of the slash menu and send queue.
    if (event.key === "Enter" && !event.shiftKey && !event.altKey && !spellOpen) {
      const local = turns.parseSlashCommand(S.input.value);
      if (local && turns.LOCAL_SLASH.has(local.name)) {
        event.preventDefault();
        composer.closeSlash();
        composer.runSlash(local.name, local.arg);
        return;
      }
    }

    if (S.streaming && event.key === "Enter" && !event.shiftKey) {
      // Enter never stops a running turn: it queues the message. Send now on
      // a queued row stops the running reply and sends it as a new turn instead.
      event.preventDefault();
      showMainForSend();
      turns.queueIncomingTurn();
      return;
    }

    if (event.key === "Enter" && event.shiftKey) {
      if (spellOpen) FuseComposerSpell.close();
      else if (extraOpen) composer.closeSlash();
      return;
    }

    if (spellOpen) {
      if (["ArrowDown", "ArrowUp", "Tab", "Enter", "Escape"].includes(event.key)) {
        event.preventDefault();
        if (event.key === "Escape") FuseComposerSpell.close();
        else if (event.key === "Enter" || event.key === "Tab") FuseComposerSpell.accept();
        else FuseComposerSpell.move(event.key === "ArrowDown" ? 1 : -1);
        if (!FuseComposerSpell.isOpen()) composer.restoreEnterKeyHint();
        composer.syncComposerAria();
        return;
      }
      if (composer.isComposerPrintable(event) || event.key === "Backspace" || event.key === "Delete") {
        FuseComposerSpell.close();
        composer.restoreEnterKeyHint();
        composer.syncComposerAria();
      }
    }

    if (slashOpen && (event.key === "ArrowDown" || event.key === "ArrowUp")) {
      event.preventDefault();
      const max = Math.max(0, (S.slashKind === "slash" ? composer.filteredSlash() : S.paletteItems).length - 1);
      S.slashIndex = event.key === "ArrowDown"
        ? Math.min(max, S.slashIndex + 1)
        : Math.max(0, S.slashIndex - 1);
      if (S.slashKind === "slash") composer.renderSlash();
      else composer.renderPalette(S.slashKind, S.paletteItems, S.paletteTrigger);
      return;
    }
    if (slashOpen && event.key === "Tab") {
      event.preventDefault();
      composer.acceptCurrentPalette();
      return;
    }
    if (slashOpen && event.key === "Escape") {
      event.preventDefault();
      composer.closeSlash();
      return;
    }
    if (slashOpen && event.key === "Enter") {
      event.preventDefault();
      composer.acceptCurrentPalette();
      return;
    }

    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "z" && !S.input.value) {
      event.preventDefault();
      if (event.shiftKey) sidebar.redoConversation();
      else sidebar.undoConversation();
      return;
    }

    if (event.key === "Enter" && event.altKey && !event.shiftKey) {
      event.preventDefault();
      if (window.FuseComposerSpell) FuseComposerSpell.openAtCaret();
      composer.syncComposerAria();
      return;
    }

    if (event.key === "ArrowUp" && !S.input.value && !slashOpen && !spellOpen) {
      event.preventDefault();
      composer.openHistoryPeek();
      return;
    }

    if (event.key === "Enter") {
      event.preventDefault();
      sendFromComposer();
    }
  });

    if (S.searchChip) {
    S.searchChip.addEventListener("click", (event) => {
      event.preventDefault();
      if (!net.requireLive()) return;
      S.searchOn = !S.searchOn;
      composer.persist();
      panels.syncMemoryUi();
    });
  }

    if (S.attachBtn && S.fileInput) {
    S.attachBtn.addEventListener("click", (event) => {
      event.preventDefault();
      S.fileInput.value = "";
      S.fileInput.click();
    });
    S.fileInput.addEventListener("change", () => composer.addFiles(S.fileInput.files));
  }

    if (S.attachList) {
    S.attachList.addEventListener("click", (event) => {
      const view = event.target.closest("[data-image-preview]");
      if (view) {
        event.preventDefault();
        panels.openLightbox(view.getAttribute("data-image-preview"), view.getAttribute("data-image-name") || "");
        return;
      }
      const btn = event.target.closest("[data-remove-attach]");
      if (!btn) return;
      S.pendingAttachments.splice(Number(btn.getAttribute("data-remove-attach")), 1);
      composer.renderAttachList();
    });
  }

    if (S.chatBox) {
    ["dragenter", "dragover"].forEach((type) => {
      S.chatBox.addEventListener(type, (event) => {
        if (![...event.dataTransfer.types].includes("Files")) return;
        event.preventDefault();
        S.chatBox.classList.add("is-drop");
      });
    });
    ["dragleave", "drop"].forEach((type) => {
      S.chatBox.addEventListener(type, (event) => {
        event.preventDefault();
        S.chatBox.classList.remove("is-drop");
      });
    });
    S.chatBox.addEventListener("drop", (event) => {
      if (event.dataTransfer && event.dataTransfer.files && event.dataTransfer.files.length) {
        composer.addFiles(event.dataTransfer.files);
      }
    });
  }

    S.input.addEventListener("paste", (event) => {
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
      composer.addFiles(files);
    }
  });

    if (S.jobsListEl) S.jobsListEl.addEventListener("click", agents.onJobsClick);

    setInterval(() => {
    if (S.currentId && (S.agentsOpen || S.jobsRows.length || traces.hasRunningBgTraces())) agents.refreshJobs();
  }, 3000);

    // agent.tick events drive the closed-panel list now; the poll is only a
  // fallback for missed started/ended events (socket resync, older server).
  setInterval(() => {
    if (S.currentId && !S.agentsOpen && (S.agentsRows.some((row) => agents.agentLive(row.status)) || S.messages.some((msg) =>
      (msg.tools || []).some((trace) => ["spawn_subagent", "get_subagent_output"].includes(trace.name) && trace.child_id && traces.toolRunning(trace, false))))) agents.refreshAgents();
  }, 10000);

    // Transport heartbeats alone do not prove that this particular run is alive.
  // A quiet current run gets an authoritative snapshot; the server's existing
  // orphan recovery handles absent workers. Never retry a model call here.
  setInterval(async () => {
    if (!S.currentId || !traces.runIsActive(S.activeRun) || document.hidden || !net.isLive() || runReconcilePending || Date.now() - S.lastRunReceiptAt < 15000) return;
    runReconcilePending = true;
    try { await sync.reconcileThread(); } catch (_) {} finally { runReconcilePending = false; }
  }, 5000);

    if (S.agentLogEl)
    S.agentLogEl.addEventListener("scroll", () => {
      if (S.agentIgnoreScroll) return;
      const top = S.agentLogEl.scrollTop;
      S.agentStick = agents.agentNearBottom() && (S.agentStick || S.agentResumeOnScroll && top > S.agentLastScrollTop);
      S.agentLastScrollTop = top;
    });

    if (S.agentLogEl) {
    const pause = () => {
      S.agentStick = false; S.agentIgnoreScroll = false; S.agentResumeOnScroll = false;
      if (S.agentFollowFrame) cancelAnimationFrame(S.agentFollowFrame);
      S.agentFollowFrame = 0;
    };
    S.agentLogEl.addEventListener("wheel", event => { if (event.deltaY < 0) pause(); else if (event.deltaY > 0) S.agentResumeOnScroll = true; }, { passive: true });
    let y = null;
    S.agentLogEl.addEventListener("touchstart", event => { y = event.touches[0]?.clientY ?? null; }, { passive: true });
    S.agentLogEl.addEventListener("touchmove", event => {
      const next = event.touches[0]?.clientY;
      if (next != null && y != null && next > y) pause();
      else if (next != null && y != null && next < y) S.agentResumeOnScroll = true;
      y = next ?? null;
    }, { passive: true });
    S.agentLogEl.addEventListener("keydown", event => { if (["ArrowUp", "PageUp", "Home"].includes(event.key)) pause(); else if (["ArrowDown", "PageDown", "End"].includes(event.key)) S.agentResumeOnScroll = true; });
  }

    if (S.agentBodyEl)
    S.agentBodyEl.addEventListener("click", (event) => {
      const previewBtn = event.target.closest("[data-image-preview]");
      if (previewBtn) {
        event.preventDefault();
        event.stopPropagation();
        panels.openLightbox(previewBtn.getAttribute("data-image-preview"), previewBtn.getAttribute("data-image-name") || "");
        return;
      }
      const openAgentEl = event.target.closest("[data-open-agent]");
      if (!openAgentEl) return;
      event.preventDefault();
      event.stopPropagation();
      agents.showAgent(openAgentEl.getAttribute("data-open-agent"));
    });

    S.root.querySelector("[data-agent-retry]")?.addEventListener("click", () => {
      const id = S.agentViewId;
      if (!id) return;
      S.agentViewId = "";
      agents.showAgent(id);
    });

    // Row actions act on that row's subagent, open or not.
    async function stopAgent(id, force) {
      const ask = force
        ? "Force stop this subagent now? It is wedged in stopping; force-finalize its state."
        : "Stop this subagent? Its children stop too; the main agent keeps running.";
      if (!(await sidebar.askConfirm(ask, { confirmLabel: force ? "Force stop" : "Stop", danger: true }))) return;
      // Held until the subagent is seen finished (agents/live.js).
      picker.holdHint("agent-stop:" + id, force ? "Force-stopping subagent…" : "Stopping subagent…");
      try {
        await net.api("/chat/runs/" + encodeURIComponent(id) + "/cancel?scope=self" + (force ? "&force=1" : ""), { method: "POST", body: "{}" });
        if (S.agentViewId === id) await agents.pollAgentView();
        agents.refreshAgents();
      } catch (error) {
        sync.showFailure(force ? "Could not force-stop subagent" : "Could not stop subagent", error);
      }
    }

    async function resumeAgent(id) {
      if (!(await sidebar.askConfirm("Resume this subagent from where it stopped? Its report goes to the main agent when it finishes.", { confirmLabel: "Resume" }))) return;
      try {
        const data = await net.api("/chat/runs/" + encodeURIComponent(id) + "/resume", { method: "POST", body: "{}" });
        if (data && data.run && S.agentViewId === id) {
          agents.acceptAgentSnapshot(await net.api("/chat/runs/" + encodeURIComponent(id)));
          agents.renderAgentView();
        }
        picker.setHint("Resumed subagent.");
        agents.refreshAgents();
      } catch (error) {
        sync.showFailure("Could not resume subagent", error);
      }
    }

    if (S.agentsListEl)
    S.agentsListEl.addEventListener("click", (event) => {
      const action = event.target.closest("[data-agent-stop], [data-agent-force], [data-agent-resume]");
      if (action) {
        event.preventDefault();
        if (action.dataset.agentResume) resumeAgent(action.dataset.agentResume);
        else stopAgent(action.dataset.agentStop || action.dataset.agentForce, Boolean(action.dataset.agentForce));
        return;
      }
      if (event.target.closest("[data-agents-fold]")) {
        const btn = event.target.closest("[data-agents-fold]");
        agents.setAgentsFold(btn.getAttribute("aria-expanded") !== "true");
        return;
      }
      if (event.target.closest("[data-agent-main]")) { agents.closeAgentView(); return; }
      const row = event.target.closest("[data-agent-id]");
      if (row) agents.showAgent(row.getAttribute("data-agent-id"));
    });

    // Escape leaves a subagent's transcript when nothing else claims it.
    document.addEventListener("keydown", (event) => {
      if (event.key !== "Escape" || event.defaultPrevented || !S.agentsOpen) return;
      if (S.pickerOpen || S.permsOpen || S.memoryOpen || S.skillsOpen || S.artifactOpen) return;
      if (document.querySelector(".chat-slash:not([hidden]), .chat-rewind:not([hidden]), .chat-lightbox:not([hidden]), dialog[open]")) return;
      if (event.target === S.input && S.input.value) return;
      agents.closeAgentView();
    });

    agents.syncAgentsUi();

    if (S.viewMenu) {
    overlays.attachSheetSwipe(S.viewMenu, "[data-view-sheet-head]", () => S.viewMenu.classList.contains("is-sheet-motion"), () => panels.setViewMenuOpen(false));
  }

    if (S.memoryToggle) {
    S.memoryToggle.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
      panels.setMemoryOpen(!S.memoryOpen);
    });
  }

    if (S.memoryClose) {
    S.memoryClose.addEventListener("click", (event) => {
      event.preventDefault();
      panels.setMemoryOpen(false);
    });
  }

    if (S.memoryBackdrop) {
    S.memoryBackdrop.addEventListener("click", () => panels.setMemoryOpen(false));
  }

    if (S.skillsToggle) {
    S.skillsToggle.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
      panels.setSkillsOpen(!S.skillsOpen);
    });
  }

    if (S.skillsClose) {
    S.skillsClose.addEventListener("click", (event) => {
      event.preventDefault();
      panels.setSkillsOpen(false);
    });
  }

    if (S.skillsBackdrop) {
    S.skillsBackdrop.addEventListener("click", () => panels.setSkillsOpen(false));
  }

    document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    if (S.pickerOpen) {
      event.preventDefault();
      picker.closePicker();
    } else if (S.skillsOpen) {
      event.preventDefault();
      panels.setSkillsOpen(false);
    } else if (S.memoryOpen) {
      event.preventDefault();
      panels.setMemoryOpen(false);
    } else if (S.permsOpen) {
      event.preventDefault();
      perms.setPermsOpen(false);
    } else if (S.viewMenu && !S.viewMenu.hidden) {
      event.preventDefault();
      panels.setViewMenuOpen(false);
      S.viewToggle?.focus();
    }
  });

    if (S.viewToggle && S.viewMenu) {
    const viewSheetClose = S.viewMenu.querySelector("[data-view-close]");
    viewSheetClose?.addEventListener("click", (event) => {
      event.preventDefault();
      panels.setViewMenuOpen(false);
    });
    S.viewMenu.addEventListener("keydown", (event) => {
      if (event.key === "Escape") {
        event.preventDefault();
        panels.setViewMenuOpen(false);
        S.viewToggle.focus();
      }
    });
    S.viewToggle.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
      panels.setViewMenuOpen(S.viewMenu.hidden);
    });
    const thinkingToggle = S.viewMenu.querySelector("[data-thinking-toggle]");
    if (thinkingToggle) {
      thinkingToggle.checked = S.showThinking;
      thinkingToggle.addEventListener("change", () => panels.setShowThinking(thinkingToggle.checked));
    }
    const effortWarnToggle = S.viewMenu.querySelector("[data-effort-cache-warn-toggle]");
    if (effortWarnToggle) {
      // Read when the sheet opens: the toast's "Don't warn again" sets it too.
      const read = () => { try { effortWarnToggle.checked = localStorage.getItem(S.STORE_EFFORT_CACHE_WARN) !== "off"; } catch (_) {} };
      read();
      S.viewToggle.addEventListener("click", read);
      effortWarnToggle.addEventListener("change", () => {
        try { localStorage.setItem(S.STORE_EFFORT_CACHE_WARN, effortWarnToggle.checked ? "on" : "off"); } catch (_) {}
      });
    }
    ;
    S.viewBackdrop?.addEventListener("click", () => panels.setViewMenuOpen(false));
  }

    S.root.setAttribute("data-view", "console");

    document.documentElement.dataset.chatConsole = "true";

    document.documentElement.removeAttribute("data-chat-terminal");

    // Settings › Appearance links to /chat?appearance=1 to open this panel.
  if (S.viewMenu && new URLSearchParams(location.search).has("appearance")) {
    const params = new URLSearchParams(location.search);
    params.delete("appearance");
    const query = params.toString();
    history.replaceState(history.state, "", location.pathname + (query ? `?${query}` : "") + location.hash);
    panels.setViewMenuOpen(true);
  }

    if (S.artifactToggle) {
    S.artifactToggle.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
      const last = artifacts.latestArtifact();
      if (!last && !(S.currentArtifact && S.currentArtifact.building)) return;
      if (S.artifactOpen) artifacts.setArtifactOpen(false);
      else if (last) artifacts.showArtifact(last.identifier, last).catch((error) => sync.showFailure("Could not open artifact", error));
    });
  }

    if (S.artifactClose) {
    S.artifactClose.addEventListener("click", (event) => {
      event.preventDefault();
      artifacts.setArtifactOpen(false);
    });
  }

    if (S.artifactBackdrop) {
    S.artifactBackdrop.addEventListener("click", () => artifacts.setArtifactOpen(false));
  }

    S.artifactModeBtns.forEach((btn) => {
    btn.addEventListener("click", (event) => {
      event.preventDefault();
      S.artifactMode = btn.getAttribute("data-artifact-mode") || "preview";
      artifacts.syncArtifactUi();
      if (S.artifactMode === "source") {
        artifacts.loadArtifactSource().catch((error) => sync.showFailure("Could not load source", error));
      }
    });
  });

    if (S.artifactVersionEl) {
    S.artifactVersionEl.addEventListener("change", () => {
      if (!S.currentArtifact || !S.currentArtifact.identifier) return;
      S.currentArtifact.version = Number(S.artifactVersionEl.value) || S.currentArtifact.version;
      S.artifactSourceLoaded = "";
      if (S.artifactFrame) S.artifactFrame.src = artifacts.artifactContentUrl(S.currentArtifact.identifier, S.currentArtifact.version);
      if (S.artifactMode === "source") artifacts.loadArtifactSource().catch((error) => sync.showFailure("Could not load source", error));
      artifacts.syncArtifactUi();
    });
  }

    [S.memoryInstructions, S.memoryProfile, S.memoryThread].forEach((area) => {
    if (!area) return;
    area.addEventListener("input", () => {
      if (area === S.memoryInstructions) S.instructions = area.value;
      else if (area === S.memoryProfile) S.profile = area.value;
      else S.threadContext = area.value;
      panels.syncMemoryUi();
      if (!net.isLive()) net.requireLive();
      if (area === S.memoryThread) composer.persist();
      else panels.queueMemorySave();
    });
  });

    // The phone quick dial (static/quick-dial.js) runs chat actions through
  // this surface so it never duplicates their rules.
  window.FuseChatActions = {
    state() {
      return {
        running: traces.runIsActive(S.activeRun) || S.sending,
        hasTurns: S.messages.length > 0,
        canRedo: S.redoStack.length > 0,
        loading: S.loading,
      };
    },
    run(action) {
      if (action === "retry") {
        if (traces.runIsActive(S.activeRun) || S.sending) turns.requestStop().catch((error) => sync.showFailure("Could not stop", error));
        else turns.retryLastTurn();
      } else if (action === "undo") sidebar.undoConversation();
      else if (action === "redo") sidebar.redoConversation();
      else if (action === "rewind") {
        if (!S.messages.length) picker.setHint("Nothing to rewind.");
        else turns.openRewind();
      } else if (action === "agents") agents.setAgentsOpen(true);
      else if (action === "new") {
        const project = sidebar.sessionProject();
        const start = project ? sidebar.startProjectThread(project.host_id, project.project_id) : resetChat();
        Promise.resolve(start).catch((error) => sync.showFailure("Could not start a new chat", error));
      }
    },
    hint(text) {
      picker.setHint(text);
    },
  };

    S.newBtns.forEach((btn) =>
    btn.addEventListener("click", () => {
      if (S.historyTab === "projects") {
        sidebar.openProjectPicker().catch((error) => sync.showFailure("Could not start a project", error));
        return;
      }
      resetChat().catch((error) => sync.showFailure("Could not start a new chat", error));
    })
  );

    if (S.rewindClose) S.rewindClose.addEventListener("click", turns.closeRewind);

    sidebar.historyBackdrop?.addEventListener("click", () => sidebar.setHistoryOpen(false));

    // Back swipe: inside a chat it reveals the chat list (like the Chats toggle);
  // from the list itself the browser leaves the page, as usual.
  window.addEventListener("popstate", (event) => {
    // FuseBack (back.js, registered first) already closed a layer for this one.
    if (event.fuseBackHandled) return;
    if (!overlays.overlayIsMobile()) return;
    if (event.state && event.state.fuseChat) {
      if (S.root.classList.contains("is-history")) sidebar.setHistoryOpen(false);
      return;
    }
    if (!S.root.classList.contains("is-history")) sidebar.setHistoryOpen(true, { home: true });
  });

    // app.js closes history directly when opening global navigation.
  new MutationObserver(sidebar.syncHistory).observe(S.root, { attributes: true, attributeFilter: ["class"] });

    S.historyMobile.addEventListener("change", () => {
    sidebar.setHistoryOpen(S.historyMobile.matches && !S.currentId && !S.messages.length, { home: true });
    composer.pinVisualViewport();
  });

    S.pickerSheetQuery.addEventListener("change", () => {
    picker.renderPickerButton();
    if (S.viewMenu && !S.viewMenu.hidden) {
      S.viewMenu.style.transform = "";
      S.viewMenu.classList.remove("is-dragging");
      S.root.classList.toggle("view-sheet-open", overlays.overlayIsMobile());
    }
  });

    sidebar.syncHistory();

    const savedHistoryCollapsed = sidebar.historyCollapsedPrefs()["console"];

    S.root.classList.toggle("history-collapsed", typeof savedHistoryCollapsed === "boolean" ? savedHistoryCollapsed : true);

    sidebar.syncHistory();

    S.historyToggles.forEach((btn) => {
    btn.addEventListener("click", (event) => {
      event.stopPropagation();
      if (S.historyMobile.matches) sidebar.setHistoryOpen(!S.root.classList.contains("is-history"));
      else {
        S.root.classList.toggle("history-collapsed");
        sidebar.saveHistoryCollapsed();
        sidebar.syncHistory();
      }
    });
  });

    const resizeHandle = S.root.querySelector("[data-history-resize]");

    if (resizeHandle) {
    let savedWidth = 0;
    try { savedWidth = Number(localStorage.getItem(S.STORE_HISTORY_WIDTH)) || 0; } catch (_) {}
    if (savedWidth >= S.HISTORY_WIDTH.min && savedWidth <= S.HISTORY_WIDTH.max) {
      sidebar.applyHistoryWidth(savedWidth, false);
    }
    let resizeRaf = 0;
    resizeHandle.addEventListener("pointerdown", (event) => {
      if (window.innerWidth <= 800) return;
      event.preventDefault();
      try { resizeHandle.setPointerCapture(event.pointerId); } catch (_) {}
      resizeHandle.classList.add("is-dragging");
      document.body.classList.add("is-resizing-history");
      const onMove = (moveEvent) => {
        const rect = S.root.getBoundingClientRect();
        cancelAnimationFrame(resizeRaf);
        resizeRaf = requestAnimationFrame(() => {
          sidebar.applyHistoryWidth(sidebar.clampHistoryWidth(moveEvent.clientX - rect.left), false);
        });
      };
      const onUp = () => {
        resizeHandle.classList.remove("is-dragging");
        document.body.classList.remove("is-resizing-history");
        resizeHandle.removeEventListener("pointermove", onMove);
        resizeHandle.removeEventListener("pointerup", onUp);
        resizeHandle.removeEventListener("pointercancel", onUp);
        sidebar.applyHistoryWidth(sidebar.clampHistoryWidth(sidebar.currentHistoryWidth()), true);
      };
      resizeHandle.addEventListener("pointermove", onMove);
      resizeHandle.addEventListener("pointerup", onUp);
      resizeHandle.addEventListener("pointercancel", onUp);
    });
    resizeHandle.addEventListener("keydown", (event) => {
      const step = event.shiftKey ? 32 : 16;
      const now = sidebar.currentHistoryWidth();
      const to = { ArrowRight: now + step, ArrowLeft: now - step, Home: S.HISTORY_WIDTH.min, End: S.HISTORY_WIDTH.max, Enter: S.HISTORY_WIDTH.default }[event.key];
      if (to === undefined) return;
      event.preventDefault();
      sidebar.applyHistoryWidth(sidebar.clampHistoryWidth(to), true);
    });
    resizeHandle.addEventListener("dblclick", () => sidebar.applyHistoryWidth(S.HISTORY_WIDTH.default, true));
  }

    const searchCloseBtn = S.root.querySelector("[data-history-search-close]");

    if (S.searchToggle) {
    S.searchToggle.addEventListener("click", (event) => {
      event.preventDefault();
      if (S.searchOpen) sidebar.setSearchOpen(false);
      else sidebar.setSearchOpen(true);
    });
  }

    S.root.querySelectorAll("[data-history-tab]").forEach((btn) => {
    btn.addEventListener("click", (event) => {
      event.preventDefault();
      if (S.searchOpen) sidebar.setSearchOpen(false);
      S.showArchived = false;
      sidebar.setHistoryTab(btn.getAttribute("data-history-tab"));
    });
  });

    const projectSortEl = S.root.querySelector("[data-project-sort]");

    if (projectSortEl) {
    projectSortEl.value = S.projectSort;
    projectSortEl.addEventListener("change", () => {
      S.projectSort = projectSortEl.value === "project" ? "project" : "recent";
      try { localStorage.setItem(S.STORE_PROJECT_SORT, S.projectSort); } catch (_) {}
      sidebar.renderHistory();
    });
  }

    const projectFilterEl = S.root.querySelector("[data-project-filter]");

    if (projectFilterEl) {
    projectFilterEl.addEventListener("change", () => {
      S.projectFilter = projectFilterEl.value || "";
      try { localStorage.setItem(S.STORE_PROJECT_FILTER, S.projectFilter); } catch (_) {}
      sidebar.renderHistory();
    });
  }

    sidebar.syncHistoryChrome();

    if (searchCloseBtn) {
    searchCloseBtn.addEventListener("click", (event) => {
      event.preventDefault();
      sidebar.setSearchOpen(false);
    });
  }

    if (S.historySearch) {
    S.historySearch.addEventListener("input", () => {
      S.historyQuery = S.historySearch.value;
      sidebar.renderHistory();
      sidebar.queueSearch();
    });
    S.historySearch.addEventListener("keydown", (event) => {
      if (event.key !== "Escape") return;
      event.stopPropagation();
      if (S.historySearch.value) {
        S.historySearch.value = "";
        S.historyQuery = "";
        sidebar.resetSearchLists();
        sidebar.renderHistory();
        sidebar.queueSearch();
      } else {
        sidebar.setSearchOpen(false);
      }
    });
  }

    if (S.archivedBtn) {
    S.archivedBtn.addEventListener("click", () => {
      S.showArchived = !S.showArchived;
      sidebar.renderHistory();
    });
  }

    if (S.historyList) {
    S.historyList.addEventListener("contextmenu", (event) => {
      const row = event.target.closest(".chat-history-item");
      if (!row || !S.historyList.contains(row)) return;
      event.preventDefault();
      const at = { x: event.clientX, y: event.clientY };
      if (row.dataset.draft !== undefined) {
        sidebar.draftMenu(row, row.dataset.draft, at);
        return;
      }
      if (row.dataset.project) {
        sidebar.projectMenu(row, row.dataset.project, at);
        return;
      }
      if (row.dataset.thread) sidebar.threadMenu(row, row.dataset.thread, at);
    });

    const pinnedRowEls = () => [...S.historyList.querySelectorAll(".chat-history-item.is-pinned")];

    function endPinDrag(commit) {
      const drag = S.pinDrag;
      S.pinDrag = null;
      if (!drag) return;
      window.clearTimeout(drag.holdTimer);
      if (!drag.dragging) return;
      drag.row.classList.remove("is-dragging");
      S.historyList.classList.remove("is-reordering");
      document.body.classList.remove("is-pin-dragging");
      // The pointerup that ends a drag also clicks the row it started on.
      S.pinDragClickBlock = Date.now() + 400;
      if (!commit) {
        sidebar.renderHistory();
        return;
      }
      sidebar.savePinOrder(pinnedRowEls().map((el) => el.getAttribute("data-thread")).filter(Boolean));
    }

    function cancelPinDrag() {
      if (!S.pinDrag) return;
      if (S.pinDrag.dragging) endPinDrag(false);
      else {
        window.clearTimeout(S.pinDrag.holdTimer);
        S.pinDrag = null;
      }
    }

    function movePinnedDrag(event) {
      const row = S.pinDrag.row;
      const rows = pinnedRowEls().filter((el) => el !== row);
      let target = null;
      for (const el of rows) {
        const box = el.getBoundingClientRect();
        if (event.clientY < box.top + box.height / 2) {
          target = el;
          break;
        }
      }
      if (target) S.historyList.insertBefore(row, target);
      else if (rows.length) rows[rows.length - 1].after(row);
    }

    // Mouse drags a pinned row straight away; a finger must hold still first,
    // so a plain swipe keeps scrolling the list. Once the hold arms the drag,
    // the touchmove veto below keeps the browser from claiming the gesture.
    S.historyList.addEventListener("pointerdown", (event) => {
      if (event.button !== 0 || S.pinDrag) return;
      // Reordering needs the whole pinned section visible: search, the archive
      // view and a project filter each hide rows, so a drop there would write
      // an order the hidden rows never voted on.
      if (S.historyQuery.trim() || S.showArchived || (S.historyTab === "projects" && S.projectFilter)) return;
      const row = event.target.closest(".chat-history-item.is-pinned");
      if (!row || event.target.closest("button.more")) return;
      const touch = event.pointerType !== "mouse";
      S.pinDrag = {
        row,
        id: event.pointerId,
        start: { x: event.clientX, y: event.clientY },
        armed: !touch,
        dragging: false,
        holdTimer: 0,
      };
      if (!touch) return;
      S.pinDrag.holdTimer = window.setTimeout(() => {
        if (!S.pinDrag || S.pinDrag.dragging) return;
        S.pinDrag.armed = true;
        if (navigator.vibrate) navigator.vibrate(10);
      }, 260);
    });
    window.addEventListener("pointermove", (event) => {
      if (!S.pinDrag || event.pointerId !== S.pinDrag.id) return;
      const dist = Math.hypot(event.clientX - S.pinDrag.start.x, event.clientY - S.pinDrag.start.y);
      if (!S.pinDrag.dragging) {
        if (!S.pinDrag.armed) {
          if (dist > 8) cancelPinDrag();
          return;
        }
        if (dist < 3) return;
        S.pinDrag.dragging = true;
        S.pinDrag.row.classList.add("is-dragging");
        S.historyList.classList.add("is-reordering");
        document.body.classList.add("is-pin-dragging");
      }
      movePinnedDrag(event);
    }, { passive: true });
    S.historyList.addEventListener("touchmove", (event) => {
      if (S.pinDrag && S.pinDrag.dragging) event.preventDefault();
    }, { passive: false });
    window.addEventListener("pointerup", (event) => {
      if (!S.pinDrag || (S.pinDrag.dragging && event.pointerId !== S.pinDrag.id)) return;
      endPinDrag(S.pinDrag.dragging);
    });
    window.addEventListener("pointercancel", () => cancelPinDrag());

    S.historyList.addEventListener("click", (event) => {
      if (Date.now() < S.pinDragClickBlock) {
        event.preventDefault();
        event.stopPropagation();
        return;
      }
      const more = event.target.closest("button.more");
      if (more) {
        event.preventDefault();
        event.stopPropagation();
        const row = more.closest("[data-project], [data-thread], [data-draft]");
        if (row?.dataset.draft !== undefined) {
          sidebar.draftMenu(more, row.dataset.draft);
          return;
        }
        if (row?.dataset.project) {
          sidebar.projectMenu(more, row.dataset.project);
          return;
        }
        const id = row?.dataset.thread;
        if (id) sidebar.threadMenu(more, id);
        return;
      }
      const open = event.target.closest("button.open");
      if (open) {
        const row = open.closest("[data-project], [data-thread], [data-draft]");
        const draft = row?.dataset.draft;
        if (draft !== undefined) {
          const [, hostId, projectId] = draft.split(":");
          const start = draft ? sidebar.startProjectThread(hostId, projectId) : resetChat();
          start.catch((error) => sync.showFailure("Could not open the draft", error));
          return;
        }
        const project = row?.dataset.project;
        if (project) {
          const [hostId, projectId] = project.split(":");
          if (hostId && projectId) sidebar.openProject(hostId, projectId);
          return;
        }
        const id = row?.dataset.thread;
        if (id) sidebar.openThread(id).catch((error) => sync.showFailure("Chat could not be opened", error));
      }
    });
  }

    document.addEventListener("click", (event) => {
    const t = event.target;
    if (t.closest && t.closest("[data-chat-send], .chat-send")) return;
    if (window.FuseComposerSpell && FuseComposerSpell.isOpen() &&
        S.spellPop && !S.spellPop.contains(t) && t !== S.input) {
      FuseComposerSpell.close();
      composer.restoreEnterKeyHint();
      composer.syncComposerAria();
    }
    if (S.slashMenu && !S.slashMenu.hidden && !S.slashMenu.contains(t) && t !== S.input &&
        !(S.spellPop && S.spellPop.contains(t))) composer.closeSlash();
    if (S.rewindPanel && !S.rewindPanel.hidden && !S.rewindPanel.contains(event.target)) turns.closeRewind();
    if (S.contextPop && !S.contextPop.hidden && S.contextWrap && !S.contextWrap.contains(event.target)) context.closeContextPop();
    if (S.pop && !S.pop.contains(event.target) && !event.target.closest(".more") && !event.target.closest(".chat-msg-actions")) {
      overlays.closePop();
    }
    if (S.root.classList.contains("is-history") && S.historyToggle && !sidebar.historyPanel.contains(event.target) && !S.historyToggle.contains(event.target) && !event.target.closest(".chat-pop, dialog")) {
      sidebar.setHistoryOpen(false);
    }
  });

    document.addEventListener("keydown", (event) => {
    if (event.key === "Tab" && S.historyMobile.matches && S.root.classList.contains("is-history")) {
      const controls = [...sidebar.historyPanel.querySelectorAll("button, input, [tabindex]")]
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
      const popOpen = Boolean(S.pop);
      overlays.closePop();
      if (window.FuseComposerSpell && FuseComposerSpell.isOpen()) {
        FuseComposerSpell.close();
        composer.restoreEnterKeyHint();
        composer.syncComposerAria();
        event.preventDefault();
        return;
      }
      if (S.slashMenu && !S.slashMenu.hidden) {
        composer.closeSlash();
        return;
      }
      if (S.rewindPanel && !S.rewindPanel.hidden) {
        turns.closeRewind();
        return;
      }
      if (S.searchOpen) {
        sidebar.setSearchOpen(false);
        return;
      }
      sidebar.setHistoryOpen(false);
      if (!event.defaultPrevented && !popOpen) sidebar.setHistoryPeek(false);
      if (S.artifactOpen) {
        artifacts.setArtifactOpen(false);
        return;
      }
      if (S.memoryOpen) panels.setMemoryOpen(false);
    }
  });

    picker.renderPickerButton();

    picker.defaultHint();

    panels.syncMemoryUi();

    transcript.paint();

    sidebar.startAgeClock();

    composer.resizeInput();

    composer.syncEmptyComposer();

    composer.syncDockSpace();

    if (window.FuseComposerSpell) {
    FuseComposerSpell.mount({
      input: S.input,
      mirror: S.composeMirror,
      popover: S.spellPop,
      getPrefs: composer.composerPrefs,
      getContext: composer.getComposerContext,
      onChange() {
        S.drafts.set(draftKey(), S.input.value);
        composer.resizeInput();
        composer.syncComposer();
        composer.syncEmptyComposer();
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
    if (!composer.composerPrefs().palettes && composer.extraPaletteOpen()) composer.closeSlash();
    if (window.FuseComposerSpell) FuseComposerSpell.refresh();
  });

    if (typeof ResizeObserver !== "undefined") {
    S.dockResizeObserver = new ResizeObserver(() => {
      if (composer.dockSpaceLocked) return;
      const following = S.stickToBottom;
      const savedTop = S.thread ? S.thread.scrollTop : 0;
      const beforeDock = S.root.style.getPropertyValue("--dock-h");
      composer.syncDockSpace();
      if (window.FuseComposerSpell) FuseComposerSpell.syncMetrics();
      if (following && S.root.style.getPropertyValue("--dock-h") !== beforeDock) transcript.restoreThreadScroll(true, savedTop);
    });
    S.dockResizeObserver.observe(S.form);
    // Opening a side pane (or resizing the window) narrows the transcript and
    // reflows it taller without a scroll event; a reader following the bottom
    // would otherwise be left with the last reply hidden under the dock.
    if (S.thread) {
      const threadResizeObserver = new ResizeObserver(() => {
        if (S.stickToBottom && !transcript.threadFollowFrame) transcript.snapThread();
      });
      threadResizeObserver.observe(S.thread);
      // Anything that makes the transcript taller (a streamed line, a tool row,
      // the status line or the todo section changing size) re-pins the bottom
      // before the browser paints, so a following reader never sees a frame
      // where the new content sits below the view.
      [S.log, S.liveDock, S.todoSections.main.el].forEach((el) => el && threadResizeObserver.observe(el));
    }
    const chatBar = S.root.querySelector(".chat-bar");
    if (chatBar) new ResizeObserver(() => composer.scheduleFitChatBar()).observe(chatBar);
    if (S.agentBodyEl) {
      const agentResizeObserver = new ResizeObserver(() => { if (S.agentStick) agents.pinAgentLog(); });
      [S.agentBodyEl, S.agentLogEl].forEach((el) => el && agentResizeObserver.observe(el));
    }
    // The work strip sits above the transcript on screen: when it appears,
    // grows or goes away, a reader not following the bottom keeps the same
    // lines in view and only the scrollbar moves.
    if (S.workStripEl) {
      let stripH = S.workStripEl.offsetHeight;
      new ResizeObserver(() => {
        const h = S.workStripEl.offsetHeight;
        const agentView = S.root.classList.contains("is-agent-view");
        const view = agentView ? S.agentLogEl : S.thread;
        if (view && !(agentView ? S.agentStick : S.stickToBottom)) view.scrollTop += h - stripH;
        stripH = h;
      }).observe(S.workStripEl);
      // Switching between the main and a subagent transcript restores that
      // view's own scroll and redraws the strip for it: rebase, or the
      // restored position shifts by the difference.
      let stripView = S.root.classList.contains("is-agent-view");
      new MutationObserver(() => {
        const agentView = S.root.classList.contains("is-agent-view");
        if (agentView === stripView) return;
        stripView = agentView;
        stripH = S.workStripEl.offsetHeight;
      }).observe(S.root, { attributes: true, attributeFilter: ["class"] });
    }
  }

    if (document.fonts && document.fonts.ready) {
    document.fonts.ready.then(() => composer.scheduleFitChatBar()).catch(() => {});
  }

    composer.scheduleFitChatBar();

    composer.pinVisualViewport();

    // Viewport class changes re-run the composer sizing and rebuild the picker:
  // the placeholder swap and row height depend on the breakpoint, and the
  // model menu must switch between the anchored dropdown and the fixed sheet.
  S.pickerSheetQuery.addEventListener("change", () => {
    composer.resizeInput();
    picker.renderPickerButton();
  });
})();
