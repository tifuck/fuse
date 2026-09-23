// Loaded inside chat.js's closure by the isolated browser server only.
window.fuseToolFixture = {
  mount(rows, live = false, threadId = null) {
    messages = rows.map((row, index) => ({
      id: `fixture-message-${index}`, role: 'assistant', content: '', reasoning: '',
      noActions: true, ...(threadId ? {} : { activityOpenMap: { 0: true } }), ...structuredClone(row),
    }));
    currentId = threadId;
    log._threadId = threadId;
    activeRun = live ? { id: 'fixture-run', message_id: messages.at(-1).id, status: 'running' } : null;
    streaming = live;
    root.classList.add('history-collapsed');
    root.classList.remove('is-empty');
    log.hidden = false;
    welcomeEl.hidden = true;
    log.replaceChildren(...messages.map((msg, i) => {
      const article = renderAssistant(msg, live && i === messages.length - 1);
      article.dataset.messageId = msg.id;
      return article;
    }));
    thread.scrollTop = 0;
    syncLiveDock();
  },
  view: applyChatView,
  api,
  runInfo: () => structuredClone(activeRun),
  elapsed: liveElapsedSeconds,
  repaint: paint,
  update(id, patch, live = false) {
    messages.forEach((msg, i) => {
      const trace = (msg.tools || []).find(t => t.id === id);
      if (trace) Object.assign(trace, patch);
      updateAssistant(log.children[i], msg, live);
    });
  },
  append(trace, live = false) {
    const msg = messages.at(-1);
    msg.tools.push(trace);
    updateAssistant(log.lastElementChild, msg, live);
  },
  agents: reconcileAgentTraces,
  pollAgent: pollAgentView,
  jobs: reconcileBgTraces,
  work(rows) { setAgentsOpen(true); jobsRows = structuredClone(rows); renderJobsList(); syncAgentsUi(); },
  event: receiveEvent,
  state: toolPresentation,
  activity: currentActivity,
  merge: mergeToolEvent,
  serializeTrace: persistToolTrace,
  label: toolRowLabel,
  command: displayCommand,
  path: shortToolPath,
  toolEvent(event) {
    const msg = messages.at(-1);
    applyFuseEvent(msg, event);
    updateAssistant(log.lastElementChild, msg, true);
  },
  progress(messageIndex, payload) {
    applyProgressPayload(messages[messageIndex], payload);
    updateAssistant(log.children[messageIndex], messages[messageIndex], false);
    syncLiveDock();
  },
  stream(messageIndex, patch) {
    applyProgressPayload(messages[messageIndex], { message: patch });
    updateAssistant(log.children[messageIndex], messages[messageIndex], true);
    syncLiveDock();
    scrollThread();
  },
  follow(on = true) { if (on) { stickToBottom = true; scrollThread(); } else pauseThreadFollow(); },
  scrollState: () => ({ following: stickToBottom, frame: threadFollowFrame, top: thread.scrollTop, gap: thread.scrollHeight - thread.clientHeight - thread.scrollTop, streaming, reduced: reduceMotion.matches }),
  run(patch) { activeRun = { ...activeRun, ...patch }; setRunning(); },
  snapshot: () => structuredClone(messages),
  dock: () => liveDockLabel && liveDockLabel.textContent,
  context: {
    accept: acceptMeter,
    fuse: applyFuseEvent,
    snapshot: contextSnapshot,
    estimate: liveStreamEstimate,
    recount: recountLocalContext,
    rows: () => occupancyRows(contextSnapshot()),
    live(on) { streaming = Boolean(on); if (!on) activeRun = null; },
    reset() { liveMeter = null; meterAnchor = null; compacting = false; },
    busy: () => Boolean(compacting),
  },
};
