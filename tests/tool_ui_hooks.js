// Loaded inside chat.js's closure by the isolated browser server only.
// Exercises the modularized runtime through its real owners: state lives on
// S (static/chat/state.js), functions on their feature interfaces
// (transcript.paint(), sidebar.renderHistory(), ...), and the theme
// destructuring keeps its classic names in scope for direct calls.
window.fuseToolFixture = {
  mount(rows, live = false, threadId = null) {
    S.messages = rows.map((row, index) => ({
      id: `fixture-message-${index}`, role: 'assistant', content: '', reasoning: '',
      noActions: true, ...(threadId ? {} : { activityOpenMap: { 0: true } }), ...structuredClone(row),
    }));
    S.currentId = threadId;
    S.log._threadId = threadId;
    S.activeRun = live ? { id: 'fixture-run', message_id: S.messages.at(-1).id, status: 'running' } : null;
    S.streaming = live;
    S.root.classList.add('history-collapsed');
    S.root.classList.remove('is-empty');
    S.log.hidden = false;
    S.welcomeEl.hidden = true;
    S.log.replaceChildren(...S.messages.map((msg, i) => {
      const article = renderAssistant(msg, live && i === S.messages.length - 1);
      article.dataset.messageId = msg.id;
      return article;
    }));
    S.thread.scrollTop = 0;
    ui.syncLiveDock();
  },
  // A whole thread through the real paint path: user and automated wake
  // messages, wake results applied to their calls, every message hydrated.
  mountThread(rows, { live = false, phase = '' } = {}) {
    S.messages = rows.map((row, index) => ({
      id: `fixture-message-${index}`, role: 'assistant', content: '', reasoning: '', ...structuredClone(row),
    }));
    S.currentId = null;
    S.log._threadId = null;
    const last = [...S.messages].reverse().find(msg => msg.role === 'assistant');
    S.activeRun = live && last ? { id: 'fixture-run', message_id: last.id, status: 'running', ...(phase ? { activity: { phase } } : {}) } : null;
    S.streaming = Boolean(live && last);
    S.root.classList.add('history-collapsed');
    S.root.classList.remove('is-empty');
    S.log.hidden = false;
    S.welcomeEl.hidden = true;
    S.log.replaceChildren();
    transcript.paint();
    S.log.querySelectorAll('[data-deferred-message]').forEach(transcript.hydrateHistoricalMessage);
    ui.syncLiveDock();
  },
  thinking: panels.setShowThinking,
  slash: composer.runSlash,
  artifacts,
  artifactState: () => ({ open: S.artifactOpen, mode: S.artifactMode, current: S.currentArtifact ? structuredClone({ ...S.currentArtifact, previous: undefined }) : null }),
  patch(event) {
    const msg = S.messages.at(-1);
    sync.applyFuseEvent(msg, { event: 'message.patch', content: { from: (msg.content || '').length, text: '' }, reasoning: { from: (msg.reasoning || '').length, text: '' }, ...event });
  },
  api: net.api,
  runInfo: () => structuredClone(S.activeRun),
  // Stands in for the thread's prompt-cache probe (compaction.cache).
  cacheProbe(probe) { context.acceptCacheProbe(probe); },
  elapsed: transcript.liveElapsedSeconds,
  repaint: transcript.paint,
  update(id, patch, live = false) {
    S.messages.forEach((msg, i) => {
      const trace = (msg.tools || []).find(t => t.id === id);
      if (trace) Object.assign(trace, patch);
      if (msg.role === 'assistant') updateAssistant(S.log.children[i], msg, live);
    });
  },
  append(trace, live = false) {
    const msg = S.messages.at(-1);
    msg.tools.push(trace);
    updateAssistant(S.log.lastElementChild, msg, live);
  },
  agents: traces.reconcileAgentTraces,
  todo: {
    sync: transcript.syncTodoSections,
    openAgent(id) { return agents.showAgent(id); },
    agents(rows) { S.agentsRows = structuredClone(rows); transcript.syncTodoSections(); },
    reset() { Object.values(S.todoSections).forEach((sec) => { sec.sig = ''; sec.shownKey = ''; sec.lingerUntil = 0; sec.expanded = false; }); S.todoDismissedKeys = new Set(); },
  },
  // Leaves the thread for an empty chat (archive/delete of the open one).
  clearChat: (opts) => clearChatState(opts),
  agentState: () => ({ open: S.agentsOpen, viewId: S.agentViewId, rows: S.agentsRows.length, jobs: S.jobsRows.length }),
  pollAgent: agents.pollAgentView,
  refreshAgents: agents.refreshAgents,
  jobs: traces.reconcileBgTraces,
  work(rows) { S.jobsRows = structuredClone(rows); agents.renderJobsList(); agents.syncAgentsUi(); },
  event: sync.receiveEvent,
  sidebar: {
    mount(thread, nativeProjects = []) {
      S.threads = [];
      S.archivedThreads = [];
      S.projects = structuredClone(nativeProjects);
      S.pendingProject = null;
      S.currentId = thread.id;
      sidebar.upsertLocal(structuredClone(thread));
      sidebar.renderHistory();
    },
    update(thread) { sidebar.upsertLocal(structuredClone(thread)); sidebar.renderHistory(); },
    snapshot: () => structuredClone([...S.threads, ...S.archivedThreads]),
  },
  state: traces.toolPresentation,
  activity: ui.currentActivity,
  merge: traces.mergeToolEvent,
  serializeTrace: traces.persistToolTrace,
  label: toolRowLabel,
  command: displayCommand,
  path: shortToolPath,
  toolEvent(event) {
    const msg = S.messages.at(-1);
    sync.applyFuseEvent(msg, event);
    updateAssistant(S.log.lastElementChild, msg, true);
  },
  progress(messageIndex, payload) {
    sync.applyProgressPayload(S.messages[messageIndex], payload);
    updateAssistant(S.log.children[messageIndex], S.messages[messageIndex], false);
    ui.syncLiveDock();
  },
  stream(messageIndex, patch) {
    sync.applyProgressPayload(S.messages[messageIndex], { message: patch });
    updateAssistant(S.log.children[messageIndex], S.messages[messageIndex], true);
    ui.syncLiveDock();
    transcript.scrollThread();
  },
  follow(on = true) { if (on) { S.stickToBottom = true; transcript.scrollThread(); } else transcript.pauseThreadFollow(); },
  scrollState: () => ({ following: S.stickToBottom, frame: S.threadFollowFrame, top: S.thread.scrollTop, gap: S.thread.scrollHeight - S.thread.clientHeight - S.thread.scrollTop, streaming: S.streaming, reduced: S.reduceMotion.matches }),
  run(patch) { S.activeRun = { ...S.activeRun, ...patch }; sync.setRunning(); },
  snapshot: () => structuredClone(S.messages),
  dock: () => S.liveDockLabel && S.liveDockLabel.textContent,
  perm: (rec) => perms.upsertPermCard(rec),
  context: {
    accept: context.acceptMeter,
    fuse: sync.applyFuseEvent,
    snapshot: context.contextSnapshot,
    estimate: context.liveStreamEstimate,
    recount: context.recountLocalContext,
    rows: () => context.occupancyRows(context.contextSnapshot()),
    live(on) { S.streaming = Boolean(on); if (!on) S.activeRun = null; },
    reset() { S.liveMeter = null; S.meterAnchor = null; S.compacting = false; },
    busy: () => Boolean(S.compacting),
  },
};
