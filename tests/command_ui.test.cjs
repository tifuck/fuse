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
  const ctx = vm.createContext({});
  for (const name of ['isShellExecTool', 'childBackedTool', 'toolIsCurrent', 'toolRunning', 'backgroundCommandLabel', 'applyCommandResult']) load(name, ctx);
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
