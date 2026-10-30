// Offline regression checks: actual runtime functions, no HTTP server/provider.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const root = path.resolve(__dirname, '..');
const noop = () => {};
function storage() {
  const map = new Map();
  return { get length() { return map.size; }, key: i => [...map.keys()][i], getItem: k => map.get(k) ?? null,
    setItem: (k, v) => map.set(k, String(v)), removeItem: k => map.delete(k) };
}
function load(file, names, env) {
  const code = fs.readFileSync(path.join(root, file), 'utf8').replace(/^import .*;\n/gm, '').replace(/^export \{.*\};?\n/gm, '').replace(/export (function|const)/g, '$1');
  const context = vm.createContext({ console, setTimeout, clearTimeout, Date, ...env });
  return vm.runInContext(code + '\n;({' + names.join(',') + '})', context);
}
function node() { return { className: '', setAttribute: noop, append: noop, after: noop, remove: noop }; }
const localStorage = storage();
const store = load('static/chat/turns/queue-store.js', ['readQueue', 'writeQueueItem', 'deleteQueueItem'], { localStorage });
const a = { id: 'a', text: 'first', created: 1 }, b = { id: 'b', text: 'second', created: 2 };
store.writeQueueItem('t', a); store.writeQueueItem('t', b);
store.deleteQueueItem('t', 'a'); store.writeQueueItem('t', a);
assert.deepEqual(Array.from(store.readQueue('t'), x => x.id), ['b'], 'stale writer cannot resurrect an ACK or lose another tab enqueue');
localStorage.setItem('fuse.chat.queue.legacy', JSON.stringify([a, b]));
assert.equal(store.readQueue('legacy').length, 2);
store.deleteQueueItem('legacy', 'a');
localStorage.setItem('fuse.chat.queue.legacy', JSON.stringify([a, b]));
assert.deepEqual(Array.from(store.readQueue('legacy'), x => x.id), ['b'], 'legacy migration cannot resurrect an ACK');
function queueTab() {
  const S = { currentId: 'q', queuedTurns: [], acceptedQueueCommands: new Set(), sendNowRuns: new Set(), locallyStoppedRuns: new Set(), activeRun: { id: 'run' }, SLASH_COMMANDS: [], queueEl: null };
  let q;
  const api = load('static/chat/turns/queue.js', ['createQueue'], { S, localStorage, ...store, escapeHtml: String, newSession: () => String(Math.random()) });
  q = api.createQueue({ composer: { resizeInput: noop, scheduleFitChatBar: noop }, net: { api: async () => ({}) }, panels: { enabledTools: () => [] }, picker: { currentEffort: () => '' }, transcript: {}, ui: {}, sync: { syncStatus: noop }, traces: { runIsActive: run => !!run }, send: { stopActiveRun: async () => q.holdQueueForStop('run'), completeTurn: async () => false } });
  return { S, q };
}
(async () => {
  const one = queueTab(), two = queueTab();
  one.q.enqueueTurn('one', []); two.q.enqueueTurn('two', []);
  one.q.loadQueue(); assert.equal(one.S.queuedTurns.length, 2);
  one.q.holdQueueForStop('run'); two.q.loadQueue(); assert.equal(two.q.queueHeld(), true);
  const reload = queueTab(); reload.q.loadQueue(); assert.equal(reload.q.queueHeld(), true, 'Stop hold survives reload and cross-tab storage');
  await one.q.sendQueuedNow(one.S.queuedTurns[0]);
  two.q.holdQueueForStop('run', true); two.q.loadQueue();
  two.q.removeQueuedTurn(1);
  assert.equal(two.q.queueHeld(), false, 'send-now cancellation in another tab must not re-hold the released item');
  assert.match(localStorage.getItem('fuse.chat.queue.cancel.run') || '', /^send-now:\d+$/, 'shared send-now cause marker is timestamped');

  const S = { currentId: 'thread', generation: 1, navigation: 0, messages: [{ role: 'user', content: 'old', id: 'u' }, { role: 'assistant', content: 'reply', id: 'a' }], serverRevision: 7,
    pendingAttachments: [], input: { value: '' }, drafts: new Map(), draftFiles: new Map(), modelId: 'test', log: node(), pendingTurns: new Map() };
  Object.defineProperty(S, 'pendingTurn', { configurable: true, get: () => S.pendingTurns.get(String(S.currentId)) || null, set: v => { if (v) S.pendingTurns.set(String(v.id), v); else S.pendingTurns.delete(String(S.currentId)); } });
  const original = JSON.stringify(S.messages), posts = [], undos = [];
  let fail = true;
  const composer = { persist: () => { throw Error('must not PATCH/truncate before replacement'); }, syncComposer: noop, resizeInput: noop, renderAttachList: noop };
  const ctx = { composer, context: {}, net: { requireLive: () => true, markDisconnected: noop, api: async (url, req) => { posts.push(JSON.parse(req.body)); if (fail) throw Error('lost ACK'); return { thread: {} }; } }, overlays: {}, panels: { enabledTools: () => [] }, picker: { currentEffort: () => 'low' }, queue: {}, sidebar: { snapshotMessages: () => JSON.parse(original), rememberUndo: snap => undos.push(snap) }, sync: { flushPending: async () => {}, setPendingTurn: (value, id = S.currentId) => { if (value) S.pendingTurns.set(String(value.id), value); else S.pendingTurns.delete(String(id)); }, showFailure: noop, syncStatus: noop, acceptThread: noop }, traces: { runIsActive: () => false }, transcript: { dismissRecaps: noop, setActive: noop, scrollThread: noop }, ui: {} };
  const send = load('static/chat/turns/send.js', ['createSend'], { S, newSession: () => 'command', draftKey: () => S.currentId || null, escapeHtml: String, window: {}, document: { createElement: node }, requestAnimationFrame: fn => fn() }).createSend(ctx);
  assert.equal(await send.completeTurn('edited', [], { undoSnap: JSON.parse(original), truncatedTo: 0 }), false);
  assert.equal(JSON.stringify(S.messages), original, 'failed Retry/Edit must not truncate the local transcript');
  assert.equal(S.input.value, 'edited'); assert.equal(posts[0].replace_from, 0); assert.equal(posts[0].expected_revision, 7);
  S.currentId = 'other';
  fail = false;
  assert.equal(await send.completeTurn('unrelated', [], {}), true, 'pendingTurn is scoped per thread and must not block another chat');
  assert.equal(posts[1].replace_from, undefined);
  S.currentId = 'thread';
  assert.notEqual(S.pendingTurn, null, 'original thread keeps its unconfirmed send for idempotent retry');
  await send.completeTurn('edited', [], { truncatedTo: 0 });
  assert.deepEqual(posts[2], posts[0], 'uncertain ACK retry must reuse the exact command body');
  assert.equal(JSON.stringify(undos[0]), original, 'successful replacement keeps the full pre-edit undo snapshot');

  const U = { currentId: 'u', generation: 1, serverRevision: 9, historyRevision: 9, undoStack: [], redoStack: [[{ id: 'diverged' }]], messages: [{ role: 'user', id: 'new' }], input: { value: '' }, drafts: new Map(), saveChain: Promise.resolve(), streaming: false, STORE_UNDO: 'undo.' };
  const sessionStorage = storage(); let patched = 0;
  const undoCtx = { artifacts: { versionMarks: () => new Map(), offerRestore: noop }, composer: { ...composer, closeSlash: noop, syncEmptyComposer: noop }, context: { recountLocalContext: noop, syncContextMeter: noop }, net: { requireLive: () => true, api: async (url, req) => { patched++; const body = JSON.parse(req.body); return { revision: 10, messages: body.messages }; } }, picker: { setHint: noop }, sync: { flushPending: async () => {}, acceptThread: updated => { U.serverRevision = updated.revision; U.messages = updated.messages; }, syncStatus: noop }, traces: { runIsActive: () => false, serialMessages: () => U.messages }, transcript: { paint: noop }, turns: { closeRewind: noop, parseSlashCommand: () => null, drainQueue: noop } };
  const undo = load('static/chat/sidebar/undo.js', ['createUndo'], { S: U, sessionStorage, newSession: () => 'undo-command', window: {} }).createUndo(undoCtx);
  await undo.redoConversation(); assert.equal(patched, 0, 'diverged redo snapshot must not PATCH'); assert.equal(U.redoStack.length, 0, 'stale redo entry is dropped');
  U.redoStack = [[{ id: 'new' }, { id: 'restore' }]];
  await undo.redoConversation(); assert.equal(patched, 1); assert.deepEqual(U.messages.map(m => m.id), ['new', 'restore']);
  await new Promise(resolve => setTimeout(resolve, 280));
  const saved = JSON.parse(sessionStorage.getItem('undo.u'));
  assert.equal(saved.r.length, 0, 'redo persists after pop'); assert.equal(saved.revision, 10);
  U.serverRevision = 11; undo.loadHistoryStacks(); assert.equal(U.undoStack.length, 0, 'reload rejects persisted snapshots from another server revision');
  U.undoStack = [[{ id: 'diverged' }]]; U.serverRevision = 11;
  await undo.undoConversation(); assert.equal(patched, 1, 'diverged undo snapshot must not PATCH'); assert.equal(U.undoStack.length, 0);
  U.undoStack = [[{ id: 'new' }]];
  await undo.undoConversation(); assert.equal(patched, 2, 'lineage-valid undo PATCHes'); assert.deepEqual(U.messages.map(m => m.id), ['new']);
  console.log('PASS offline chat mutations: per-item queue + migration, cross-tab Stop/send-now cause, atomic replace_from send, per-thread pendingTurn, uncertain-ACK idempotency, lineage-bound undo/redo');
})().catch(error => { console.error(error); process.exitCode = 1; });
