// Sync and turn state across the real modules (static/chat/**), with stub
// collaborators: a snapshot older than the patches already applied, the gap
// replay, progress while editing, the compacted view, queued items sent while
// another tab reloads the queue, and unconfirmed sends that were accepted.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { S } = require('../static/chat/state.js');
const { createApply } = require('../static/chat/sync/apply.js');
const { createStream } = require('../static/chat/sync/stream.js');
const { createPersist } = require('../static/chat/sync/persist.js');
const { createPaint } = require('../static/chat/transcript/paint.js');
const { createQueue } = require('../static/chat/turns/queue.js');
const { createSend } = require('../static/chat/turns/send.js');
const { createUndo } = require('../static/chat/sidebar/undo.js');
const { createRender } = require('../static/chat/sidebar/render.js');
const { runIsActive } = require('../static/chat/traces/semantics.js');
const { readQueue } = require('../static/chat/turns/queue-store.js');

const noop = () => {};
// Any collaborator method a test does not name is a no-op.
const stub = (named = {}) => new Proxy(named, { get: (t, k) => (k in t ? t[k] : k === 'then' ? undefined : noop) });
function storage() {
  const map = new Map();
  return { get length() { return map.size; }, key: i => [...map.keys()][i] ?? null, getItem: k => map.get(k) ?? null,
    setItem: (k, v) => map.set(k, String(v)), removeItem: k => map.delete(k), clear: () => map.clear() };
}
global.localStorage = storage();
global.sessionStorage = storage();
// state.js only installs this accessor in a browser (initState).
Object.defineProperty(S, 'pendingTurn', {
  configurable: true,
  get: () => S.pendingTurns.get(String(S.currentId)) || null,
  set: value => { if (value) S.pendingTurns.set(String(value.id), value); else S.pendingTurns.delete(String(S.currentId)); },
});
const tick = () => new Promise(resolve => setImmediate(resolve));
const article = id => ({ dataset: { messageId: id }, querySelector: () => null });

function baseState(overrides = {}) {
  Object.assign(S, {
    currentId: 't', generation: 1, navigation: 0, editingIndex: -1, editingFiles: [], transcriptDirty: false, dirty: false,
    streaming: false, sending: false, loading: false, stoppingRunId: '', serverRevision: 3, viewCursor: 0, threadWatermark: 0,
    cursor: 0, replayedThrough: 0, gapReplayFrom: 0, activeRun: null, messages: [], contextMessages: [], contextLive: false,
    completedJobs: new Map(), agentLifecycle: new Map(), agentLifecycleEpoch: 0, modelsById: new Map(), modelId: 'offline',
    pendingTurns: new Map(), queuedTurns: [], acceptedQueueCommands: new Set(), sendNowRuns: new Set(),
    locallyStoppedRuns: new Set(), queueEl: null, queueListEl: null, historyQuery: '', searchThreads: [],
    searchArchived: [], searchActiveQuery: '', searchGen: 0, renameSaving: null, agentsRows: [], agentsOpen: false,
    agentViewId: '', SLASH_COMMANDS: [],
  }, overrides);
  S.log = { children: S.messages.map(msg => article(msg.id)), childElementCount: S.messages.length, appendChild: noop };
}

function makeApply(calls = {}) {
  const traces = stub({ runIsActive });
  const paint = createPaint({ traces });
  return createApply({
    traces,
    transcript: stub({ captureThreadViewport: () => ({}), visibleRecaps: () => [], viewMessages: paint.viewMessages, paint: () => { calls.paint = (calls.paint || 0) + 1; } }),
    ui: stub({ updateAssistant: () => { calls.update = (calls.update || 0) + 1; } }),
    agents: stub(), artifacts: stub(), composer: stub(), context: stub(), net: stub(), panels: stub(), perms: stub(),
    persist: stub(), picker: stub(), sidebar: stub(), status: stub(), turns: stub(),
  });
}

test('a snapshot older than the applied patches keeps the newer live reply', () => {
  const live = { id: 'm', role: 'assistant', content: 'Hello world, streamed', reasoning: '', tools: [], segments: [] };
  baseState({
    messages: [{ id: 'u', role: 'user', content: 'Hi' }, live], streaming: true, viewCursor: 50, threadWatermark: 50,
    activeRun: { id: 'r', message_id: 'm', status: 'running', activity: { phase: 'tools', tool_id: 'x' } },
  });
  const apply = makeApply();
  const snapshot = (cursor, content) => ({
    id: 't', revision: 3, event_cursor: cursor, thread_cursor: cursor,
    run: { id: 'r', message_id: 'm', status: 'queued' },
    messages: [{ id: 'u', role: 'user', content: 'Hi' }, { id: 'm', role: 'assistant', content, reasoning: '', tools: [], segments: [] }],
  });
  apply.acceptThread(snapshot(40, 'Hello'));
  assert.equal(S.messages[1], live, 'the live reply object is kept');
  assert.equal(S.messages[1].content, 'Hello world, streamed', 'the reply is not rolled back to the stamp');
  assert.equal(S.viewCursor, 50, 'the cursor still describes the kept reply');
  assert.equal(S.threadWatermark, 50);
  assert.equal(S.activeRun.status, 'running', 'progress already moved the run out of queued');
  assert.equal(S.activeRun.activity.phase, 'tools', 'the live activity is kept');

  apply.acceptThread(snapshot(60, 'Hello world, streamed and newer'));
  assert.equal(S.messages[1].content, 'Hello world, streamed and newer', 'a newer snapshot still wins');
  assert.equal(S.viewCursor, 60);
});

test('a snapshot clears fields the server left out instead of keeping the old ones', () => {
  const file = { id: 'f', name: 'a.txt', kind: 'text', data: 'x' };
  const user = { id: 'u', role: 'user', content: 'Hi', attachments: [file] };
  baseState({ messages: [user, { id: 'm', role: 'assistant', content: 'Hel', usage: { output_tokens: 3 }, tools: [] }],
    activeRun: { id: 'r', message_id: 'm', status: 'running' } });
  makeApply().acceptThread({ id: 't', revision: 4, event_cursor: 9, run: { id: 'r', message_id: 'm', status: 'running' },
    messages: [{ id: 'u', role: 'user', content: 'Hi' }, { id: 'm', role: 'assistant', content: 'Hello' }] });
  assert.equal(S.messages[0], user, 'the message object is kept');
  assert.equal('attachments' in S.messages[0], false, 'an attachment removed elsewhere does not come back');
  assert.deepEqual(S.messages[1].usage, { output_tokens: 3 }, 'the live reply keeps what it streamed');
});

test('a snapshot that switches to the compacted view repaints instead of patching in place', () => {
  const messages = () => [{ id: 'u', role: 'user', content: 'Hi' }, { id: 'a', role: 'assistant', content: 'Done', tools: [] }];
  const snapshot = extra => ({ id: 't', revision: 4, event_cursor: 9, run: null, messages: messages(), ...extra });

  baseState({ messages: messages() });
  let calls = {};
  makeApply(calls).acceptThread(snapshot({}));
  assert.equal(calls.paint, undefined, 'unchanged ids and the full view take the fast path');
  assert.equal(calls.update, 1);

  baseState({ messages: messages() });
  calls = {};
  makeApply(calls).acceptThread(snapshot({ context_messages: [{ id: 'c', role: 'marker', kind: 'compaction', summary: 'Earlier work' }, { id: 'a', role: 'assistant', content: 'Done' }] }));
  assert.equal(calls.paint, 1, 'the compacted view replaces the full transcript');
  assert.equal(calls.update, undefined);
});

function makeStream(calls, apply = {}) {
  return createStream({
    apply: stub({ reconcileThread: async () => { calls.reconcile = (calls.reconcile || 0) + 1; }, ...apply }),
    status: stub({ notifyTurnDone: () => { calls.notify = (calls.notify || 0) + 1; } }),
    sidebar: stub({ queueSearch: () => { calls.search = (calls.search || 0) + 1; }, threadById: () => null,
      resetSearchLists: createRender(stub()).resetSearchLists }),
    net: stub({ isLive: () => true }),
    transcript: stub({ captureThreadViewport: () => ({}) }),
    traces: stub({ runIsActive, articleFor: () => null }),
    agents: stub(), context: stub(), perms: stub(), persist: stub({ flushSave: async () => {} }), picker: stub(), turns: stub(), ui: stub(),
  });
}

test('run progress is applied while a message is being edited', async () => {
  const live = { id: 'm', role: 'assistant', content: '', tools: [] };
  baseState({ messages: [{ id: 'u', role: 'user', content: 'Hi' }, live], editingIndex: 0, cursor: 10, viewCursor: 10,
    activeRun: { id: 'r', message_id: 'm', status: 'running' } });
  const applied = [];
  const stream = makeStream({}, { applyProgressPayload: (msg, payload) => applied.push([msg, payload]) });
  await stream.receiveEvent({ id: 11, type: 'run.progress', thread_id: 't', payload: { run_id: 'r', message_id: 'm' } });
  assert.equal(applied.length, 1);
  assert.equal(applied[0][0], live);
  assert.equal(S.viewCursor, 11);
  assert.equal(S.cursor, 11);
});

test('a progress update that had to refetch keeps the snapshot\'s newer cursors', async () => {
  baseState({ messages: [{ id: 'm', role: 'assistant', content: '' }], cursor: 10, viewCursor: 10, threadWatermark: 10,
    activeRun: { id: 'r', message_id: 'm', status: 'running' } });
  const calls = {};
  const stream = makeStream(calls, { applyProgressPayload: noop,
    reconcileThread: async () => { calls.reconcile = 1; S.viewCursor = 20; S.threadWatermark = 20; } });
  await stream.receiveEvent({ id: 11, type: 'run.progress', thread_id: 't', payload: { run_id: 'r', message_id: 'm', updates: [{ id: 'gone' }] } });
  assert.equal(calls.reconcile, 1);
  assert.equal(S.viewCursor, 20);
  assert.equal(S.threadWatermark, 20);
  assert.equal(S.cursor, 11);
});

test('events resent by a gap replay fire only the open thread\'s run updates', async () => {
  baseState({ messages: [{ id: 'm', role: 'assistant', content: '' }], cursor: 40, viewCursor: 40, replayedThrough: 90,
    activeRun: { id: 'r', message_id: 'm', status: 'running' } });
  const calls = {};
  const applied = [];
  const stream = makeStream(calls, { applyProgressPayload: (msg, payload) => applied.push(payload) });
  await stream.receiveEvent({ id: 41, type: 'run.completed', thread_id: 'other', payload: { run_id: 'x', message: { content: 'done' } } });
  await stream.receiveEvent({ id: 42, type: 'thread.updated', thread_id: 'other', payload: { id: 'other', revision: 2 } });
  assert.equal(calls.notify, undefined, 'an already-handled completion does not notify twice');
  assert.equal(S.cursor, 42);
  await stream.receiveEvent({ id: 43, type: 'run.progress', thread_id: 't', payload: { run_id: 'r', message_id: 'm' } });
  assert.equal(applied.length, 1, 'the open thread\'s missed patch is applied');
  await stream.receiveEvent({ id: 91, type: 'run.completed', thread_id: 'other', payload: { run_id: 'x', message: { content: 'done' } } });
  assert.equal(calls.notify, 1, 'events past the replay are handled normally');
});

test('a thread event refreshes the search without clearing its hits', async () => {
  baseState({ historyQuery: 'needle', searchActiveQuery: 'needle', searchThreads: [{ id: 'hit', title: 'Matched in a message' }], cursor: 5 });
  const calls = {};
  await makeStream(calls).receiveEvent({ id: 6, type: 'thread.updated', thread_id: 'other', payload: { id: 'other', revision: 1 } });
  assert.deepEqual(S.searchThreads, [{ id: 'hit', title: 'Matched in a message' }]);
  assert.equal(S.searchActiveQuery, 'needle');
  assert.equal(calls.search, 1);
});

test('a patch past a consumed gap reopens the socket at the snapshot cursor, once per cursor', async () => {
  const sockets = [];
  global.WebSocket = class { constructor(url) { this.url = String(url); sockets.push(this); } close() {} send() {} };
  global.window = { setTimeout, clearTimeout };
  global.location = { href: 'https://fuse.test/chat', protocol: 'https:' };
  Object.defineProperty(globalThis, 'navigator', { value: { onLine: true }, configurable: true });
  baseState({ messages: [{ id: 'm', role: 'assistant', content: 'Hel' }], cursor: 90, viewCursor: 40, syncInitialized: true, socket: null,
    activeRun: { id: 'r', message_id: 'm', status: 'running' }, HEARTBEAT_MS: 35000, reconnectAttempt: 0, syncEpoch: 0 });
  const calls = {};
  const gap = () => { throw Object.assign(new Error('Output checkpoint mismatch'), { gap: true }); };
  const stream = makeStream(calls, { applyProgressPayload: gap });
  const deliver = async (ws, events) => { ws.onmessage({ data: JSON.stringify({ type: 'events', events }) }); await tick(); await tick(); };
  const original = console.warn;
  console.warn = noop;
  try {
    await stream.connectSync(false, true);
    assert.equal(sockets.length, 1);
    await deliver(sockets[0], [{ id: 91, type: 'run.progress', thread_id: 't', payload: { run_id: 'r', message_id: 'm' } },
      { id: 92, type: 'run.progress', thread_id: 't', payload: { run_id: 'r', message_id: 'm' } }]);
    assert.equal(sockets.length, 2, 'the gap reopens the socket');
    assert.equal(new URL(sockets[1].url).searchParams.get('cursor'), '40', 'replay starts at the snapshot cursor');
    assert.equal(S.replayedThrough, 90, 'events up to the old cursor are marked as handled');
    assert.equal(calls.reconcile, undefined, 'no snapshot refetch while the replay can close the gap');

    await deliver(sockets[1], [{ id: 41, type: 'run.progress', thread_id: 't', payload: { run_id: 'r', message_id: 'm' } }]);
    assert.equal(sockets.length, 2, 'a replay that cannot close the gap is not repeated');
    assert.equal(calls.reconcile, 1, 'it falls back to a snapshot');
  } finally {
    console.warn = original;
  }
});

test('a queued item stays in flight when another tab reloads the queue', async () => {
  localStorage.clear();
  baseState({ currentId: 'q' });
  let finish;
  const queue = createQueue({
    composer: stub(), picker: stub(), transcript: stub(), ui: stub(), sync: stub(), traces: stub({ runIsActive }),
    send: stub({ completeTurn: () => new Promise(resolve => { finish = resolve; }) }),
  });
  assert.equal(queue.enqueueTurn('first', []), true);
  const draining = queue.drainQueue();
  assert.equal(queue.queueItemSending(S.queuedTurns[0]), true);
  queue.loadQueue();
  const reloaded = S.queuedTurns[0];
  assert.equal(queue.queueItemSending(reloaded), true, 'the fresh copy from storage is still sending');
  queue.holdQueueForStop('other-run');
  assert.equal(queue.queueHeld(), false, 'a Stop hold skips the message being sent');
  finish(false);
  await draining;
  assert.equal(queue.queueItemSending(S.queuedTurns[0]), false);
  assert.equal(S.queuedTurns.length, 1, 'a refused send stays queued');
});

test('a queued send that fails after its chat was replaced is not written into the open chat', async () => {
  localStorage.clear();
  baseState({ currentId: 'a' });
  let finish;
  const queue = createQueue({
    composer: stub(), picker: stub(), transcript: stub(), ui: stub(), sync: stub(), traces: stub({ runIsActive }),
    send: stub({ completeTurn: () => new Promise(resolve => { finish = resolve; }) }),
  });
  assert.equal(queue.enqueueTurn('for a', []), true);
  const draining = queue.drainQueue();
  // a was deleted elsewhere mid-send and the next chat opened.
  S.currentId = 'b';
  finish(false);
  await draining;
  assert.deepEqual(readQueue('b'), []);
  assert.equal(readQueue('a').length, 1);
});

function makeSend(api, calls = {}) {
  global.document = { createElement: () => ({ className: '', setAttribute: noop, append: noop, remove: noop, textContent: '' }) };
  global.requestAnimationFrame = fn => fn();
  global.window = { setTimeout, clearTimeout };
  S.log.after = noop;
  const persist = createPersist({});
  return createSend({
    net: stub({ requireLive: () => true, api }),
    sync: stub({ flushPending: async () => {}, setPendingTurn: persist.setPendingTurn }),
    sidebar: stub({ snapshotMessages: () => [] }),
    composer: stub(), context: stub(), overlays: stub(), panels: stub({ enabledTools: () => [] }), picker: stub({ currentEffort: () => 'low' }),
    queue: stub(), traces: stub({ runIsActive }), transcript: stub(), ui: stub(),
    ...calls,
  });
}

test('an accepted send is no longer unconfirmed when the chat changed mid-request', async () => {
  baseState({ currentId: 't1', input: { value: '' }, pendingAttachments: [], drafts: new Map(), draftFiles: new Map(), threadContext: '' });
  const send = makeSend(async () => { S.generation += 1; S.currentId = null; return { thread: { id: 't1' } }; });
  await send.completeTurn('hello', []);
  assert.equal(S.pendingTurns.has('t1'), false);
  assert.equal(sessionStorage.getItem(S.STORE_PENDING_TURN || 'fuse.chat.pendingTurn'), null);
});

test('a failed edit keeps its editor and does not overwrite the composer', async () => {
  const files = [{ id: 'f1', name: 'notes.txt', kind: 'text', data: 'x' }];
  baseState({ messages: [{ id: 'u', role: 'user', content: 'old', attachments: files }, { id: 'a', role: 'assistant', content: 'reply' }],
    editingIndex: 0, editingFiles: files.slice(), input: { value: '' }, pendingAttachments: [], drafts: new Map(), draftFiles: new Map(), threadContext: '' });
  const send = makeSend(async () => { throw Object.assign(new Error('Server error'), { status: 503 }); });
  const seen = [];
  const undo = createUndo({
    traces: stub({ runIsActive }), net: stub({ requireLive: () => true }), transcript: stub(),
    turns: stub({ completeTurn: (...args) => { seen.push(args); return send.completeTurn(...args); } }),
    artifacts: stub(), composer: stub(), context: stub(), picker: stub(), sync: stub(),
  });
  assert.equal(await undo.commitEdit(0, 'new text'), false);
  assert.deepEqual(seen[0][1], files, 'the kept files are sent');
  assert.equal(seen[0][2].edit, true);
  assert.equal(S.editingIndex, 0, 'the editor stays open after the failure');
  assert.deepEqual(S.editingFiles, files, 'a second Save still carries the attachments');
  assert.equal(S.input.value, '', 'the edit text stays in its editor, not the composer');
});
