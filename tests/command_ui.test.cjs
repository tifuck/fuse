const { test } = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const chat = require('./chat_source.cjs');
// The real modularized runtime: semantics are static exports, the mutating
// trace functions come from the actual merge factory over the real shared
// state (chat.S). Stub collaborators match the seams the vm-based suites
// stubbed (scheduleAgentView, startLiveClock, ...).
const REG = { ...chat.Core, ...chat.Semantics, ...chat.makeTraces() };
function load(name, context) {
  const themed = require("./theme_factory.cjs")(context)[name];
  if (typeof themed === "function") { context[name] = themed; return; }
  assert.ok(typeof REG[name] === 'function', `missing module function: ${name}`);
  context[name] = REG[name];
}
// S-backed aliases must stay live (Object.assign would flatten getters), so
// tests keep driving the real state owner through them.
const S_ALIASES = ['activeRun', 'messages', 'agentRun', 'agentPendingEvents', 'agentsRows',
  'agentsRequest', 'agentsRefreshAgain', 'agentLifecycleEpoch', 'agentLifecycle', 'agentsFailCount'];
function world() {
  const ctx = Object.assign({}, REG, {
    applyCancelResult: chat.makeSyncStatus().applyCancelResult,
    acceptAgentSnapshot: chat.makeAgentView({ traces: REG }).acceptAgentSnapshot,
    assistantSegments: () => [],
    groupTraces: () => [],
  });
  for (const key of S_ALIASES) Object.defineProperty(ctx, key, {
    get: () => chat.S[key], set: (v) => { chat.S[key] = v; }, enumerable: true, configurable: true,
  });
  return vm.createContext(ctx);
}
test('automated labels read envelope ids, not the handoff boilerplate', () => {
  const c = vm.createContext({});
  load('autoEventLabel', c);
  const footer = "These are for you, not the user. A <monitor_event> means the watch fired — do not start another. "
    + 'A <monitor_result state="timed_out"> means the watch ended.';
  const commandOnly = '[Fuse] AUTOMATED EVENT — not written by the user. Background results:\n\n'
    + '<command_result id="ab12cd34ef56" state="completed">\nexit 0\n</command_result>\n\n' + footer;
  assert.equal(c.autoEventLabel(commandOnly), 'Automated · background command result');
  assert.equal(c.autoEventLabel('<task id="c1" state="completed">report</task>'), 'Automated · background subagent report');
  assert.equal(c.autoEventLabel('<monitor_event id="m1" seq="1">line</monitor_event>'), 'Automated · monitor event');
  assert.equal(
    c.autoEventLabel('<command_result id="ab12cd34ef56" state="completed"></command_result>\n<task id="c1" state="completed"></task>'),
    'Automated · background results');
  assert.equal(
    c.autoEventLabel('<monitor_event id="m1" seq="1"></monitor_event>\n<command_result id="ab12cd34ef56" state="completed"></command_result>'),
    'Automated · background results');
});

test('a subagent auto row folds into background-command steps like the thread', () => {
  const c = world();
  for (const name of ['parseCommandResults', 'wakeStepTrace', 'foldAutoCommandResults']) load(name, c);
  const rows = [
    { role: "assistant", content: "", tools: [] },
    { role: "user", auto: true, content: '[Fuse] AUTOMATED EVENT — not written by the user. Background results:\n\n'
      + '<command_result id="ab12cd34ef56" state="completed">\nhost: box command: nohup strings -a lib.so > out.txt\nexit_code: 0\nduration_ms: 3\n\nstrings started 560656\n</command_result>\n\n'
      + 'These are for you, not the user. A <monitor_event> means the watch fired.' },
  ];
  c.foldAutoCommandResults(rows);
  assert.equal(rows[1].autoSteps.length, 1);
  const step = rows[1].autoSteps[0];
  assert.equal(step.name, 'run_terminal_command');
  assert.equal(step.child_id, 'ab12cd34ef56');
  assert.equal(step.backgroundResult, true);
  assert.equal(step.status, 'done');
  assert.equal(step.exit_code, 0);
});

test('background execution stays live independently of the assistant turn', () => {
  const c = world();
  const trace = {name: 'run_terminal_command', background: true, status: 'running', child_id: 'job-1', command: 'sleep 10'};
  assert.equal(c.toolRunning(trace, false), true);
  c.applyCommandResult(trace, {state: 'completed', exit_code: null, duration_ms: 10, output: 'done'});
  assert.equal(c.toolRunning(trace, false), false);
  assert.equal(trace.exit_code, undefined);
});
test('completion receipts and cancellation are distinct from execution', () => {
  const c = world();
  const trace = {name: 'run_terminal_command', background: true, backgroundResult: true, status: 'done', child_id: 'job-2', command: 'sleep 2'};
  c.applyCommandResult(trace, {state: 'cancelled', exit_code: null, duration_ms: null, error: 'cancelled', output: ''});
  assert.equal(trace.status, 'interrupted');
  assert.equal(c.toolRunning(trace, false), false);
});
test('foreground commands retain ordinary presentation', () => {
  const c = world();
  const trace = {name: 'run_terminal_command', status: 'running'};
  assert.equal(c.toolRunning(trace, true), true);
  assert.equal(c.toolRunning(trace, false), false);
});

test('returned waits stay complete while their child continues in the background', () => {
  const c = world();
  for (const name of ['get_command_output', 'get_subagent_output']) {
    for (const execution_status of ['running', 'queued', 'cancelling', 'completed', 'failed']) {
      const trace = { name, id: 'wait-1', child_id: 'child', status: 'done', execution_status };
      assert.equal(c.toolPresentation(trace, true).running, false);
      assert.equal(c.toolPresentation(trace, true).statusLabel, 'Done');
    }
  }
  assert.equal(c.toolRunning({name: 'spawn_subagent', status: 'done', execution_status: 'running'}, false), true);
});

test('a wait abandoned by its parent cannot spin for the lifetime of its child', () => {
  const c = world();
  for (const name of ['get_command_output', 'get_subagent_output']) {
    for (const execution_status of [undefined, 'running', 'cancelling']) {
      const trace = { name, child_id: 'child', status: 'running', execution_status };
      assert.equal(c.toolPresentation(trace, true).running, true);
      assert.equal(c.toolPresentation(trace, false).running, false);
      assert.equal(c.toolPresentation(trace, false).interrupted, true);
    }
  }
});

test('agent lifecycle reconciliation never completes or restarts wait invocations', () => {
  const c = world();
  Object.assign(chat.S, { messages: [{ tools: [
    { name: 'spawn_subagent', child_id: 'child', status: 'done', state: 'running', execution_status: 'running' },
    { name: 'get_subagent_output', child_id: 'child', status: 'running' },
    { name: 'get_subagent_output', child_id: 'child', status: 'done', execution_status: 'running' },
  ] }], agentRun: null, log: { children: [] } });
  load('mapAgentToolStatus', c);
  load('articleFor', c);
  load('reconcileAgentTraces', c);
  c.reconcileAgentTraces([{ id: 'child', status: 'completed', summary: 'Report' }]);
  assert.equal(c.messages[0].tools[0].execution_status, 'completed');
  assert.equal(c.toolRunning(c.messages[0].tools[0], false), false, 'state and execution_status must agree');
  assert.equal(c.messages[0].tools[1].status, 'running', 'only the tool result completes an in-flight wait');
  assert.equal(c.messages[0].tools[2].status, 'done');
});

test('agent list previews do not truncate a delivered report', () => {
  const c = world();
  const report = 'Final report. '.repeat(100);
  Object.assign(chat.S, { messages: [{ tools: [{ name: 'spawn_subagent', child_id: 'child', status: 'done', summary: report }] }],
    agentRun: null, log: { children: [] } });
  load('mapAgentToolStatus', c);
  load('articleFor', c);
  load('reconcileAgentTraces', c);
  c.reconcileAgentTraces([{ id: 'child', status: 'completed', summary: report.slice(0, 200) }]);
  assert.equal(c.messages[0].tools[0].summary, report);
});

test('background command completion reaches nested-agent transcript tools', () => {
  const c = world();
  const trace = { name: 'run_terminal_command', child_id: 'job', status: 'done', execution_status: 'running', background: true };
  let paints = 0;
  const traceModule = chat.makeTraces({ agents: { scheduleAgentView() { paints++; } } });
  Object.assign(chat.S, { messages: [], agentRun: { messages: [{ role: 'assistant', tools: [trace] }] }, agentsOpen: true,
    log: { children: [] } });
  c.reconcileBgTraces = traceModule.reconcileBgTraces;
  c.reconcileBgTraces([{ id: 'job', state: 'completed', output: 'Final output', output_chars: 12, exit_code: 0 }]);
  assert.equal(c.toolPresentation(trace, false).statusLabel, 'Done');
  assert.equal(trace.output, 'Final output');
  assert.equal(paints, 1);
});

test('historical tool activity cannot resurrect a thinking spinner or running label', () => {
  const c = world();
  Object.assign(c, { activeRun: null, groupTraces: () => [], lastCurrentTrace: () => null,
    assistantSegments: () => [], runIsStopping: () => false });
  load('currentActivity', c);
  const trace = { name: 'get_subagent_output', id: 'wait', status: 'running', child_id: 'child' };
  const state = c.currentActivity({ stopped: true, tools: [trace], _activity: { phase: 'tools', tool_id: 'wait' } }, [], false);
  assert.equal(state.phase, 'stopped');
  assert.equal(state.current, null);
});

test('command polling cannot rewrite pending or returned wait invocations', () => {
  const c = world();
  Object.assign(chat.S, { messages: [], agentRun: null, agentsOpen: false, log: { children: [] } });
  load('reconcileBgTraces', c);
  for (const state of ['completed', 'failed', 'cancelled', 'lost']) {
    for (const status of ['running', 'done', 'error', 'interrupted']) {
      const trace = { name: 'get_command_output', child_id: 'job', status, state: 'running', output: 'observed' };
      const before = { ...trace };
      chat.S.messages = [{ role: 'assistant', tools: [trace] }];
      c.reconcileBgTraces([{ id: 'job', state, output: 'later output', output_chars: 12 }], false);
      assert.deepEqual(trace, before);
    }
  }
});

test('wake envelopes preserve historical command observations', () => {
  const c = world();
  for (const status of ['running', 'done', 'interrupted']) {
    const trace = { name: 'get_command_output', status, child_id: 'job', summary: 'observed' };
    const before = { ...trace };
    c.applyCommandResult(trace, { state: 'failed', error: 'later failure', output: 'later output' });
    assert.deepEqual(trace, before);
  }
});

test('late cancellation acknowledgement cannot install a run in an idle thread', () => {
  const c = world();
  Object.assign(chat.S, { activeRun: null, stoppingRunId: '' });
  c.applyCancelResult({ run: { id: 'old-run', status: 'running', cancel_requested: true } }, 'old-run');
  assert.equal(c.activeRun, null);
  c.activeRun = { id: 'new-run', status: 'running' };
  c.applyCancelResult({ run: { id: 'old-run', status: 'cancelled' } }, 'old-run');
  assert.equal(c.activeRun.id, 'new-run');
});

test('agent activity uses its own queue and cancellation state', () => {
  const c = world();
  Object.assign(chat.S, { activeRun: { message_id: 'root', status: 'running' },
    agentRun: { message_id: 'child', status: 'queued' } });
  load('currentActivity', c);
  assert.equal(c.currentActivity({ id: 'child' }, [], true).label, 'Queued');
  chat.S.agentRun.status = 'running';
  chat.S.agentRun.cancel_requested = true;
  assert.equal(c.currentActivity({ id: 'child' }, [], true).label, 'Stopping');
});

test('agent snapshots retain activity and replay cancellation without regressing the cursor', () => {
  const c = world();
  Object.assign(chat.S, { agentRun: null, jobsRows: [], completedJobs: new Map(), agentPendingEvents: [
      { id: 12, type: 'agent.cancel_requested', payload: { run_id: 'child', status: 'running', cancel_requested: true } },
    ] });
  assert.equal(c.acceptAgentSnapshot({ id: 'child', event_cursor: 10, status: 'running', message: {}, activity: { phase: 'model' } }), true);
  assert.equal(c.agentRun.message._activity.phase, 'model');
  assert.equal(c.agentRun.cancel_requested, true);
  assert.equal(c.agentRun.event_cursor, 12);
  assert.equal(c.acceptAgentSnapshot({ id: 'child', event_cursor: 11, status: 'running', message: {} }), false);
  assert.equal(c.agentRun.cancel_requested, true);
  chat.S.agentPendingEvents = [{ id: 14, type: 'agent.resumed', payload: { run_id: 'child' } }];
  c.acceptAgentSnapshot({ id: 'child', event_cursor: 13, status: 'cancelled', cancel_requested: true, message: {} });
  assert.equal(c.agentRun.status, 'queued');
  assert.equal(c.agentRun.cancel_requested, false);
});

test('agent list rejects reads overtaken by lifecycle events and schedules a replacement', async () => {
  const c = world();
  const pending = [];
  Object.assign(chat.S, { currentId: 'thread', generation: 1, agentsRequest: null, agentsRefreshAgain: false,
    agentLifecycleEpoch: 0, agentLifecycle: new Map(), agentsRows: [], agentsFailCount: 0 });
  c.refreshAgents = chat.makeAgentView({ traces: c, net: { api: () => new Promise(resolve => pending.push(resolve)) } }).refreshAgents;
  const first = c.refreshAgents();
  c.agentLifecycleEpoch++;
  await c.refreshAgents();
  pending.shift()({ agents: [{ id: 'child', status: 'running' }] });
  await first;
  assert.equal(c.agentsRows.length, 0, 'stale running state must not be published');
  assert.equal(pending.length, 1, 'a lifecycle refresh must not be dropped');
  pending.shift()({ agents: [{ id: 'child', status: 'completed' }] });
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(c.agentsRows[0].status, 'completed');
});

test('a wait trace cannot change the background classification of its child', () => {
  const c = world();
  Object.assign(c, { activeRun: { message_id: 'message', activity: { phase: 'tools' } },
    runIsStopping: () => false, groupTraces: () => [], lastCurrentTrace: () => null,
    assistantSegments: () => [] });
  load('currentActivity', c);
  const tools = [
    { id: 'spawn', name: 'spawn_subagent', child_id: 'child', status: 'done', execution_status: 'running', background: true },
    { id: 'other', name: 'spawn_subagent', child_id: 'other-child', status: 'done', execution_status: 'running', background: true },
    { id: 'wait', name: 'get_subagent_output', child_id: 'child', status: 'running' },
  ];
  assert.match(c.currentActivity({ id: 'message', tools }, [], true).label, /2 active \/ 2 background/);
  tools[2].status = 'done';
  assert.match(c.currentActivity({ id: 'message', tools }, [], true).label, /2 active \/ 2 background/);
});
