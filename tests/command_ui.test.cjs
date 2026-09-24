const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(require('node:path').join(__dirname, '../static/chat.js'), 'utf8');
function load(name, context) {
  const start = source.indexOf(`  function ${name}(`);
  assert.ok(start >= 0);
  const end = source.indexOf('\n  }', start) + 4;
  vm.runInContext(source.slice(start, end), context);
}
function world() {
  const ctx = vm.createContext({ activeRun: null, runIsActive: () => false,
    EXECUTION_FIELDS: ['state', 'execution_status', 'output', 'output_mode', 'output_chars'] });
  for (const name of ['executionState', 'executionIdentity', 'executionPending', 'toolBackground', 'toolAwaitingCancellation', 'isShellExecTool', 'childBackedTool', 'toolIsCurrent', 'toolRunning', 'backgroundCommandLabel', 'applyCommandResult', 'shellExitOnly', 'shellExitCode', 'toolFailed', 'toolInterrupted', 'toolPresentation']) load(name, ctx);
  return ctx;
}
test('background execution stays live independently of the assistant turn', () => {
  const c = world();
  const trace = {name: 'run_terminal_command', background: true, status: 'running', child_id: 'job-1', command: 'sleep 10'};
  assert.equal(c.toolRunning(trace, false), true);
  assert.equal(c.backgroundCommandLabel(trace), 'background · job-1 · sleep 10');
  c.applyCommandResult(trace, {state: 'completed', exit_code: null, duration_ms: 10, output: 'done'});
  assert.equal(c.toolRunning(trace, false), false);
  assert.equal(trace.exit_code, undefined);
});
test('completion receipts and cancellation are distinct from execution', () => {
  const c = world();
  const trace = {name: 'run_terminal_command', background: true, backgroundResult: true, status: 'done', child_id: 'job-2', command: 'sleep 2'};
  assert.equal(c.backgroundCommandLabel(trace), 'background result · job-2 · sleep 2');
  c.applyCommandResult(trace, {state: 'cancelled', exit_code: null, duration_ms: null, error: 'cancelled', output: ''});
  assert.equal(trace.status, 'interrupted');
  assert.equal(c.toolRunning(trace, false), false);
});
test('foreground commands retain ordinary presentation', () => {
  const c = world();
  const trace = {name: 'run_terminal_command', status: 'running'};
  assert.equal(c.backgroundCommandLabel(trace), '');
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

test('agent lifecycle reconciliation never completes or restarts wait invocations', () => {
  const c = world();
  Object.assign(c, { messages: [{ tools: [
    { name: 'spawn_subagent', child_id: 'child', status: 'done', state: 'running', execution_status: 'running' },
    { name: 'get_subagent_output', child_id: 'child', status: 'running' },
    { name: 'get_subagent_output', child_id: 'child', status: 'done', execution_status: 'running' },
  ] }], agentRun: null, log: { children: [] }, syncLiveDock() {} });
  load('mapAgentToolStatus', c);
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
  Object.assign(c, { messages: [{ tools: [{ name: 'spawn_subagent', child_id: 'child', status: 'done', summary: report }] }],
    agentRun: null, log: { children: [] }, syncLiveDock() {} });
  load('mapAgentToolStatus', c);
  load('reconcileAgentTraces', c);
  c.reconcileAgentTraces([{ id: 'child', status: 'completed', summary: report.slice(0, 200) }]);
  assert.equal(c.messages[0].tools[0].summary, report);
});

test('background command completion reaches nested-agent transcript tools', () => {
  const c = world();
  const trace = { name: 'run_terminal_command', child_id: 'job', status: 'done', execution_status: 'running', background: true };
  let paints = 0;
  Object.assign(c, { messages: [], agentRun: { messages: [{ role: 'assistant', tools: [trace] }] }, agentsOpen: true,
    log: { children: [] }, scheduleAgentView() { paints++; } });
  load('reconcileBgTraces', c);
  c.reconcileBgTraces([{ id: 'job', state: 'completed', output: 'Final output', output_chars: 12, exit_code: 0 }]);
  assert.equal(c.toolPresentation(trace, false).statusLabel, 'Done');
  assert.equal(trace.output, 'Final output');
  assert.equal(paints, 1);
});

test('historical tool activity cannot resurrect a thinking spinner or running label', () => {
  const c = world();
  Object.assign(c, { activeRun: null, groupTraces: () => [], lastCurrentTrace: () => null,
    assistantSegments: () => [], isConsoleView: () => false, runIsStopping: () => false });
  load('currentActivity', c);
  const trace = { name: 'get_subagent_output', id: 'wait', status: 'running', child_id: 'child' };
  const state = c.currentActivity({ stopped: true, tools: [trace], _activity: { phase: 'tools', tool_id: 'wait' } }, [], false);
  assert.equal(state.phase, 'stopped');
  assert.equal(state.current, null);
});

test('agent snapshots retain activity and replay cancellation without regressing the cursor', () => {
  const c = world();
  Object.assign(c, { agentRun: null, jobsRows: [], completedJobs: new Map(),
    reconcileBgTraces() {}, applyProgressPayload() {}, agentPendingEvents: [
      { id: 12, type: 'agent.cancel_requested', payload: { run_id: 'child', status: 'running', cancel_requested: true } },
    ] });
  load('acceptAgentSnapshot', c);
  assert.equal(c.acceptAgentSnapshot({ id: 'child', event_cursor: 10, status: 'running', message: {}, activity: { phase: 'model' } }), true);
  assert.equal(c.agentRun.message._activity.phase, 'model');
  assert.equal(c.agentRun.cancel_requested, true);
  assert.equal(c.agentRun.event_cursor, 12);
  assert.equal(c.acceptAgentSnapshot({ id: 'child', event_cursor: 11, status: 'running', message: {} }), false);
  assert.equal(c.agentRun.cancel_requested, true);
  c.agentPendingEvents = [{ id: 14, type: 'agent.resumed', payload: { run_id: 'child' } }];
  c.acceptAgentSnapshot({ id: 'child', event_cursor: 13, status: 'cancelled', cancel_requested: true, message: {} });
  assert.equal(c.agentRun.status, 'queued');
  assert.equal(c.agentRun.cancel_requested, false);
});

test('agent list rejects reads overtaken by lifecycle events and schedules a replacement', async () => {
  const c = world();
  const start = source.indexOf('  async function refreshAgents(');
  const end = source.indexOf('\n  }', start) + 4;
  const pending = [];
  Object.assign(c, { currentId: 'thread', generation: 1, agentsRequest: null, agentsRefreshAgain: false,
    agentLifecycleEpoch: 0, agentLifecycle: new Map(), agentsRows: [], agentsFailCount: 0,
    api: () => new Promise(resolve => pending.push(resolve)), reconcileAgentTraces() {}, syncAgentsUi() {}, refreshJobs() {} });
  vm.runInContext(source.slice(start, end), c);
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
    assistantSegments: () => [], isConsoleView: () => false });
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
