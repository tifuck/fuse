// Chat state that late async results and repaints must not clobber: results
// land only on the chat they were requested for, the reader's picks and edits
// survive repaints, and a stopping reply is still the live one.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { S } = require('../static/chat/state.js');
const { createThreads, openedMessage } = require('../static/chat/sidebar/threads.js');
const { createMerge } = require('../static/chat/traces/merge.js');
const { createArtifacts } = require('../static/chat/artifacts.js');
const { createContext } = require('../static/chat/context.js');
const { createPanels } = require('../static/chat/panels.js');
const { createSlash } = require('../static/chat/composer/slash.js');
const { createPerms } = require('../static/chat/perms.js');
const { createQueue: createPermQueue } = require('../static/chat/perms/queue.js');

const noop = () => {};
// Any collaborator method a test does not name is a no-op.
const stub = (named = {}) => new Proxy(named, { get: (t, k) => (k in t ? t[k] : k === 'then' ? undefined : noop) });
const deferred = () => { let resolve; const promise = new Promise(r => { resolve = r; }); return { promise, resolve }; };
const tick = () => new Promise(resolve => setImmediate(resolve));

test('Rename from a menu that outlived a list repaint edits the row on screen', () => {
  global.CSS = { escape: String };
  global.document = { createElement: () => ({ setAttribute: noop, focus: noop, select: noop, addEventListener: noop }) };
  const detached = { isConnected: false, closest: () => detached };
  const onScreen = { isConnected: true, appended: [], appendChild(el) { this.appended.push(el); } };
  let row = onScreen;
  let selector = '';
  Object.assign(S, { threads: [{ id: 't1', title: 'Old' }], archivedThreads: [], historyQuery: '', showArchived: false, historyTab: 'chats',
    renaming: false, historyList: { querySelectorAll: () => [], querySelector: (sel) => { selector = sel; return row; } } });
  let items = [];
  const threads = createThreads({ net: stub({ requireLive: () => true }), overlays: stub({ showPop: (_anchor, list) => { items = list; } }) });
  threads.threadMenu(detached, 't1');
  const rename = items.find((item) => item.label === 'Rename');
  rename.run();
  assert.equal(selector, '.chat-history-item[data-thread="t1"]');
  assert.equal(onScreen.appended.length, 1, 'the rename field goes into the live row');
  assert.equal(S.renaming, true);
  S.renaming = false;
  row = null;
  rename.run();
  assert.equal(S.renaming, false, 'with no row on screen the list is not left frozen');
});

test('an opened thread keeps the permissions its skill-proposal cards render from', () => {
  const perm = { id: 'p1', kind: 'skill_save', status: 'pending', slug: 'notes' };
  const msg = openedMessage({ id: 'a', role: 'assistant', content: '', segments: [{ type: 'permission', id: 'p1' }], permissions: [perm] });
  assert.deepEqual(msg.permissions, [perm]);
  assert.deepEqual(openedMessage({ id: 'u', role: 'user', content: 'hi' }).permissions, []);
});

test('a skill proposal body the user edited survives a flow rebuild', async () => {
  Object.assign(S, { root: { querySelector: () => null } });
  let fetches = 0;
  const queue = createPermQueue({ net: { api: async () => { fetches += 1; return { body: 'server draft' }; } } });
  const body = { value: '' };
  // The rebuild patched the card in place (FuseChatDOM keeps compatible nodes).
  const card = { getAttribute: () => 'p1', querySelector: () => body };
  const flow = { querySelectorAll: () => [card] };
  queue.hydratePermissionCards(flow);
  await tick();
  assert.equal(body.value, 'server draft');
  body.value = 'my edit';
  queue.hydratePermissionCards(flow);
  await tick();
  assert.equal(fetches, 1, 'a kept card is not fetched again');
  assert.equal(body.value, 'my edit');

  const typed = { value: '' };
  queue.hydratePermissionCards({ querySelectorAll: () => [{ getAttribute: () => 'p2', querySelector: () => typed }] });
  typed.value = 'typed before the draft arrived';
  await tick();
  assert.equal(typed.value, 'typed before the draft arrived');
});

test('a stopping reply repaints as live when its agents and jobs settle', () => {
  const msg = { id: 'm', role: 'assistant', tools: [
    { id: 's', name: 'spawn_subagent', status: 'running', child_id: 'c1' },
    { id: 'b', name: 'run_terminal_command', status: 'running', command_id: 'j1', background: true },
  ] };
  const painted = [];
  Object.assign(S, { messages: [msg], agentRun: null, agentsOpen: false, streaming: false, stoppingRunId: 'r',
    activeRun: { id: 'r', message_id: 'm', status: 'running', cancel_requested: true },
    log: { children: [{ dataset: { messageId: 'm' } }] } });
  const merge = createMerge({ agents: stub(), transcript: stub(),
    ui: stub({ updateAssistant: (_article, _msg, live) => painted.push(live), workClocksLive: () => false }) });
  merge.reconcileAgentTraces([{ id: 'c1', status: 'cancelled' }]);
  merge.reconcileBgTraces([{ command_id: 'j1', state: 'cancelled' }]);
  assert.deepEqual(painted, [true, true], 'the same live state paintLast uses');
});

test('an open artifact pane keeps the reader\'s pick across repaints', () => {
  Object.assign(S, { root: { classList: { toggle: noop, add: noop } }, artifactPanel: null, artifactBackdrop: null, artifactToggle: null,
    artifactTitleEl: null, artifactStatusEl: null, artifactVisibilityEl: null, artifactOpenLink: null, artifactBuilding: null,
    artifactFrame: null, artifactSource: null, artifactModeBtns: [], artifactVersionWrap: null, artifactVersionEl: null, artifactMode: 'preview',
    messages: [{ id: 'a', role: 'assistant', tools: [
      { name: 'create_artifact', identifier: 'old', status: 'done', version: 1 },
      { name: 'create_artifact', identifier: 'new', status: 'done', version: 1 },
    ] }] });
  const artifacts = createArtifacts({ overlays: stub(), composer: stub(), panels: stub(), picker: stub(), sidebar: stub(), net: stub() });
  S.artifactOpen = true;
  S.currentArtifact = { identifier: 'old', version: 1, versions: [1, 2] };
  artifacts.rememberLatestArtifact();
  assert.deepEqual(S.currentArtifact, { identifier: 'old', version: 1, versions: [1, 2] });
  S.artifactOpen = false;
  artifacts.rememberLatestArtifact();
  assert.equal(S.currentArtifact.identifier, 'new', 'a closed pane follows the newest artifact');
});

test('a compaction that finishes after its chat was left leaves the open chat alone', async () => {
  const reply = deferred();
  const accepted = [];
  const hints = [];
  const busy = {};
  const button = { classList: { toggle: (name, on) => { busy[name] = on; } }, setAttribute: noop };
  Object.assign(S, { currentId: 'a', messages: [{ id: 'u', role: 'user', content: 'hi' }], sending: false, streaming: false, activeRun: null,
    compactingRequest: false, compacting: false, contextWrap: null, contextFill: null, contextBtn: button, contextPop: null, contextAlert: null,
    liveMeter: { used: 900, limit: 1000, scope: 'transcript' }, modelsById: new Map(), modelId: 'm', pendingAttachments: [], input: { value: '' },
    compactCfg: { warn_ratio: 0.8, auto_ratio: 0.9, auto: true, default_window: 1000 }, CONTEXT_RING: 75 });
  const context = createContext({
    net: stub({ requireLive: () => true, api: () => reply.promise }),
    picker: stub({ releaseHint: (_key, text) => hints.push(text), setHint: (text) => hints.push(text) }),
    sync: stub({ acceptThread: (data) => accepted.push(data.id) }), traces: stub({ runIsActive: () => false }), ui: stub(),
  });
  const running = context.runCompact('');
  assert.equal(busy['is-busy'], true);
  S.currentId = 'b';
  S.liveMeter = { used: 10, limit: 1000, scope: 'transcript' };
  context.syncContextMeter();
  assert.equal(busy['is-busy'], false, 'only the compacting chat shows busy');
  await context.runCompact('');
  assert.match(hints.at(-1), /Another chat is compacting/);
  reply.resolve({ thread: { id: 'a', compaction: { meter: { used: 50, limit: 1000, scope: 'transcript' } } } });
  await running;
  assert.deepEqual(accepted, ['a']);
  assert.equal(S.liveMeter.used, 10, 'the open chat keeps its own meter');
  assert.equal(S.compactingRequest, false);
  assert.match(hints.at(-1), /Context compacted · 50/);
});

test('an effort change warns only while a cache built at another effort is warm', () => {
  const pins = [];
  const store = new Map();
  global.localStorage = { getItem: (k) => (store.has(k) ? store.get(k) : null), setItem: (k, v) => store.set(k, v) };
  const opus = { id: 'claude-opus', type: 'claude' };
  const glm = { id: 'glm-5', type: 'zai' };
  Object.assign(S, { modelsById: new Map([[opus.id, opus], [glm.id, glm]]), modelId: opus.id });
  const context = createContext({
    net: stub(), sync: stub(), traces: stub(), ui: stub(),
    picker: stub({ effortLabel: (e) => e, pinHint: (key, text, action) => pins.push({ key, text, action }),
      unpinHint: (key) => pins.push({ key, gone: true }) }),
  });
  const warm = (extra) => context.acceptCacheProbe({ at: Date.now() / 1000, prompt: 80_000, model: opus.id, ttl: 3600, hit: true, effort: 'xhigh', ...extra });
  warm();
  pins.length = 0;
  context.warnEffortCacheReset('low');
  assert.match(pins.at(-1).text, /New effort, cold cache .* xhigh keeps the cache/);
  context.warnEffortCacheReset('xhigh');
  assert.deepEqual(pins.at(-1), { key: 'effort-cache', gone: true }, 'going back to the cached effort clears it');
  warm({ hit: false });
  pins.length = 0;
  context.warnEffortCacheReset('low');
  assert.equal(pins.some((p) => p.text), false, 'a cold cache has nothing to lose');
  S.modelId = glm.id;
  warm({ model: glm.id, at: Date.now() / 1000 + 1 });
  context.warnEffortCacheReset('none');
  assert.match(pins.at(-1).text, /may reset/, 'undocumented providers get a softer warning');
  pins.at(-1).action.run();
  pins.length = 0;
  context.warnEffortCacheReset('none');
  assert.equal(pins.some((p) => p.text), false, "Don't warn again sticks");
  delete global.localStorage;
});

test('project reads that land after another chat opened are dropped', async () => {
  let reply;
  const api = () => (reply = deferred()).promise;
  let project = { host_id: 1, project_id: 2, remote_path: '/p' };
  Object.assign(S, { config: {}, projectListingCache: null });
  const slash = createSlash({ net: stub({ api }), sidebar: stub({ sessionProject: () => project }) });
  let loading = slash.refreshProjectListing();
  project = null;
  reply.resolve({ path: '/p', entries: [{ name: 'a.txt' }] });
  await loading;
  assert.equal(S.projectListingCache, null, 'a plain chat gets no project listing');
  project = { host_id: 1, project_id: 2, remote_path: '/p' };
  loading = slash.refreshProjectListing();
  reply.resolve({ path: '/p', entries: [{ name: 'a.txt' }] });
  await loading;
  assert.deepEqual(S.projectListingCache, { path: '/p', entries: [{ name: 'a.txt' }] });

  Object.assign(S, { currentId: 'a', projectAgentsOk: false, memoryProject: null, root: { classList: { toggle: noop } } });
  const panels = createPanels({ net: stub({ api }), sidebar: stub({ threadById: () => ({ kind: 'project' }) }) });
  loading = panels.refreshProjectAgents();
  S.currentId = 'b';
  reply.resolve({ applicable: true, state: 'loaded', name: 'AGENTS.md' });
  await loading;
  assert.equal(S.projectAgentsOk, false, 'the other chat\'s project instructions are not shown');
});

test('a permission read that lands after another chat opened is dropped', async () => {
  Object.assign(S, { root: { querySelector: () => null }, config: {}, currentId: 'a', threadBypass: false, threadAuto: false, permCards: new Map() });
  const reply = deferred();
  const perms = createPerms({ net: { api: () => reply.promise }, sync: stub() });
  const reading = perms.refreshPerms();
  S.currentId = 'b';
  const expires_at = new Date(Date.now() + 60000).toISOString();
  reply.resolve({ bypass: false, auto: true, auto_available: true, pending: [{ id: 'ask', kind: 'tool_permission', status: 'pending', expires_at }] });
  await reading;
  assert.equal(S.threadAuto, false, 'the other chat\'s mode is not applied');
  assert.equal(S.permCards.size, 0, 'its pending asks are not seeded here');
  assert.notEqual(perms.threadPermData?.auto, true);
});
