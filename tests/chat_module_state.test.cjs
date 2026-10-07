const { test } = require('node:test');
const assert = require('node:assert/strict');
const { S } = require('../static/chat/state.js');
const { createTranscript } = require('../static/chat/transcript.js');
const { createPerms } = require('../static/chat/perms.js');
const menuState = require('../static/chat/perms/menu.js');

test('composed composer exposes its live resize lock', () => {
  global.window = { innerWidth: 1440, innerHeight: 900 };
  const { createComposer } = require('../static/chat/composer.js');
  const observed = [];
  S.config = { limits: {} };
  const composer = createComposer({});
  window.getComputedStyle = () => {
    observed.push(composer.dockSpaceLocked);
    return { minHeight: '20', maxHeight: '192' };
  };
  Object.assign(S, {
    root: { classList: { contains: () => false } },
    input: { placeholder: 'Message', dataset: {}, style: { height: '20px' }, value: '', scrollHeight: 20 },
    pickerSheetQuery: { matches: false }, thread: null, form: null,
  });
  assert.equal(composer.dockSpaceLocked, false);
  composer.resizeInput();
  assert.deepEqual(observed, [true]);
  assert.equal(composer.dockSpaceLocked, false);
  delete global.window;
});

test('composed transcript flushes the newest queued paint exactly once', () => {
  Object.assign(S, { pendingFullPaint: false, pendingAgentPaint: false, pendingAgentListPaint: false });
  const calls = [];
  const transcript = createTranscript({ ui: { updateAssistant: (...args) => calls.push(args) } });
  const article = { isConnected: true };
  const msg = { content: 'latest' };
  transcript.pendingToolPaint = { article, msg: { content: 'old' }, live: true };
  transcript.pendingToolPaint = { article, msg, live: false };
  transcript.flushToolGesture();
  assert.deepEqual(calls, [[article, msg, false]]);
  assert.equal(transcript.pendingToolPaint, null);
  transcript.flushToolGesture();
  assert.equal(calls.length, 1);
  transcript.pendingToolPaint = { article: { isConnected: false }, msg, live: false };
  transcript.flushToolGesture();
  assert.equal(calls.length, 1);
  assert.equal(transcript.pendingToolPaint, null);
});

test('composed permissions expose live owner state and reset it on navigation', async () => {
  Object.assign(S, { root: { querySelector: () => null }, config: {}, currentId: 'thread' });
  const response = { hosts: [], bypass: false, auto: false };
  const perms = createPerms({ net: { api: async () => response } });
  // The request updates the owner before attempting DOM work in this minimal fixture.
  await perms.refreshPerms();
  assert.equal(perms.threadPermData, response);
  const menu = {};
  perms.permsMenuEl = menu;
  perms.permsFilter = 'machine';
  assert.equal(menuState.permsMenuEl, menu);
  assert.equal(menuState.permsFilter, 'machine');
  perms.threadPermData = null;
  perms.permsMenuEl = null;
  perms.permsFilter = '';
  assert.equal(menuState.threadPermData, null);
  assert.equal(menuState.permsMenuEl, null);
  assert.equal(menuState.permsFilter, '');
});
