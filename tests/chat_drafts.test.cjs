const { test } = require('node:test');
const assert = require('node:assert/strict');
const { S } = require('../static/chat/state.js');

test('a failed draft save is not retried once newer text was sent', async () => {
  const timers = [];
  global.window = {
    setTimeout: (fn) => timers.push(fn),
    clearTimeout: () => {},
    addEventListener: () => {},
  };
  const { createDrafts } = require('../static/chat/composer/drafts.js');
  const sent = [];
  const replies = [];
  const net = {
    api: (_url, opts) => {
      sent.push(JSON.parse(opts.body).text);
      return new Promise((resolve, reject) => replies.push({ resolve, reject }));
    },
  };
  S.drafts = new Map();
  createDrafts({ net, input: {}, sidebar: {}, sync: {} });
  const fire = () => timers.splice(0).forEach((fn) => fn());

  S.drafts.save('t1', 'old');
  fire();
  S.drafts.save('t1', 'newer');
  fire();
  assert.deepEqual(sent, ['old', 'newer']);
  replies[1].resolve({});
  replies[0].reject(new Error('offline'));
  await new Promise((resolve) => setImmediate(resolve));
  fire();
  assert.deepEqual(sent, ['old', 'newer']);

  S.drafts.save('t1', 'latest');
  fire();
  replies[2].reject(new Error('offline'));
  await new Promise((resolve) => setImmediate(resolve));
  fire();
  assert.deepEqual(sent, ['old', 'newer', 'latest', 'latest']);
  delete global.window;
});
