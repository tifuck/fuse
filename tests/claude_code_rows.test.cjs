// Console's port of Claude Code's per-call rows: exit-code meanings, sed
// edits, counts, and what a running subagent's row says it is doing.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = require('./chat_source.cjs');
// Real modules: pure helpers and permission-card copy from static exports.
const REG = { ...source.Core, ...source.Semantics, permToolLabel: source.Perms.permToolLabel,
  permGrantKind: source.Perms.permGrantKind, permGrantPath: source.Perms.permGrantPath,
  permCardCopy: source.Perms.permCardCopy, permLeftS: source.Perms.permLeftS,
  fmtPermCountdown: source.Perms.fmtPermCountdown, permCardSignature: source.Perms.permCardSignature };
function load(name, context) {
  const themed = require("./theme_factory.cjs")(context)[name];
  if (typeof themed === "function") { context[name] = themed; return; }
  assert.ok(typeof REG[name] === 'function', `missing module function: ${name}`);
  context[name] = REG[name];
}
function constant(name, context) {
  const start = source.source.indexOf(`  const ${name} =`);
  assert.ok(start >= 0, name);
  const end = source.source.indexOf(';\n', start) + 2;
  vm.runInContext(source.source.slice(start, end).replace(`const ${name}`, `var ${name}`), context);
}
function world() {
  const ctx = vm.createContext({ compactPath: (path) => path });
  for (const name of ['CC_SEARCH_CMDS', 'CC_READ_CMDS', 'CC_LIST_CMDS', 'CC_NEUTRAL_CMDS', 'CC_FIRST_CMD', 'CC_EXIT_NOTES', 'CC_COMPACT', 'CC_CALL_NAMES']) constant(name, ctx);
  for (const name of ['ccShellSegments', 'ccShellKind', 'ccExitMeaning', 'ccShellWords', 'ccSedEdit', 'ccTokens', 'ccBytes',
    'ccFlat', 'ccClip', 'ccCallName', 'ccCallSummary', 'ccCallKind', 'ccCountsText', 'ccAgentLastCall', 'ccCallLine']) load(name, ctx);
  return ctx;
}

test('exit 1 from grep, find, diff and test is not a failure', () => {
  const c = world();
  assert.deepEqual({ ...c.ccExitMeaning('grep -rn foo src', 1) }, { isError: false, message: 'No matches found' });
  assert.deepEqual({ ...c.ccExitMeaning('cd app && rg TODO', 1) }, { isError: false, message: 'No matches found' });
  assert.deepEqual({ ...c.ccExitMeaning('git diff --quiet', 1) }, { isError: false, message: 'Files differ' });
  assert.deepEqual({ ...c.ccExitMeaning('test -f x', 1) }, { isError: false, message: 'Condition is false' });
  assert.equal(c.ccExitMeaning('grep foo bar', 2).isError, true);
  assert.equal(c.ccExitMeaning('npm test', 1).isError, true);
  assert.equal(c.ccExitMeaning('npm test', 0).isError, false);
});

test('a single-file sed -i is an Update of that file', () => {
  const c = world();
  assert.equal(c.ccSedEdit("sed -i 's/old/new/g' app/main.py").file, 'app/main.py');
  assert.equal(c.ccSedEdit("sed -i.bak -E 's/a+/b/' x.txt").file, 'x.txt');
  assert.equal(c.ccSedEdit("sed -i '' -e 's/a/b/' x.txt").file, 'x.txt');
  assert.equal(c.ccSedEdit("sed 's/a/b/' x.txt"), null);
  assert.equal(c.ccSedEdit("sed -i 's/a/b/' x.txt y.txt"), null);
  assert.equal(c.ccSedEdit("sed -i 's/a/b/' *.txt"), null);
  assert.equal(c.ccSedEdit("sed -i \"s/$X/b/\" x.txt"), null);
  assert.equal(c.ccSedEdit("sed -i '1d' x.txt"), null);
  assert.equal(c.ccSedEdit("sed -i 's/a/b/' x.txt && echo ok"), null);
});

test('counts read the way Claude Code prints them', () => {
  const c = world();
  assert.equal(c.ccTokens(999), '999');
  assert.equal(c.ccTokens(32100), '32.1k');
  assert.equal(c.ccTokens(1_250_000), '1.3m');
  assert.equal(c.ccBytes(512), '512 bytes');
  assert.equal(c.ccBytes(12_595), '12.3KB');
  assert.equal(c.ccBytes(1024 * 1024 * 1.5), '1.5MB');
});

test("a call's time sits on its header from 2s, once per call", () => {
  const c = world();
  Object.assign(c, {
    escapeHtml: (text) => String(text),
    isSearchTool: (name) => name === 'web_search',
    toolBackground: (trace) => Boolean(trace.background),
  });
  for (const name of ['ccDuration', 'ccElapsedText', 'ccSince', 'ccCallClockHtml']) load(name, c);
  const took = (trace, running = false) => c.ccCallClockHtml(trace, { running }).replace(/<[^>]+>/g, '');
  assert.equal(took({ name: 'run_terminal_command', execution_duration_ms: 23_400, call_duration_ms: 30_000 }), ' · 23s');
  assert.equal(took({ name: 'read_file', call_duration_ms: 95_000 }), ' · 1m 35s');
  assert.equal(took({ name: 'grep', call_duration_ms: 1_500 }), '', 'under 2s');
  assert.equal(took({ name: 'run_terminal_command', background: true, execution_duration_ms: 60_000 }), '', 'background launch');
  assert.equal(took({ name: 'web_search', call_duration_ms: 4_000 }), '', 'web search says it in its result');
  assert.equal(took({ name: 'run_terminal_command', elapsed_ms: 12_000 }, true), ' · 12s', 'running ticks from its start');
  assert.equal(took({ name: 'run_terminal_command', elapsed_ms: 500 }, true), '', 'running under 2s');
  // A Wait's duration_ms is its command's run time, not the wait.
  assert.equal(took({ name: 'get_command_output', call_duration_ms: 31_000, duration_ms: 184_727 }), ' · 31s');
  assert.equal(took({ name: 'get_command_output', duration_ms: 184_727 }), '', 'no call time, no clock');
  assert.equal(took({ name: 'get_command_output', background: true, call_duration_ms: 30_006 }), ' · 30s', 'a Wait on a background command keeps its clock');
});

test("a running subagent's row names its latest step", () => {
  const c = world();
  assert.equal(c.ccAgentLastCall([
    { name: 'run_terminal_command', status: 'done', command: 'npm test', description: 'Run the unit tests' },
  ]), 'Bash: Run the unit tests');
  assert.equal(c.ccAgentLastCall([
    { name: 'run_terminal_command', status: 'done', command: 'npm test' },
    { name: 'grep', status: 'done', pattern: 'login' },
    { name: 'read_file', status: 'done', path: 'src/auth.ts' },
    { name: 'read_file', status: 'running', path: 'src/user.ts' },
  ]), 'Searching for 1 pattern, reading 2 files…');
  assert.equal(c.ccAgentLastCall([{ name: 'read_url', status: 'done', url: 'https://example.com/docs' }]), 'Fetch: https://example.com/docs');
  assert.equal(c.ccAgentLastCall([{ name: 'grep', status: 'running', pattern: 'x' }]), '');
  assert.equal(c.ccCallLine({ name: 'grep', pattern: 'login' }), 'Search(login)');
});

test('a shell approval card shows the description above the command', () => {
  const ctx = vm.createContext({ PERM_TOTAL_S: 60 });
  for (const name of ['escapeHtml', 'permToolLabel', 'permGrantKind', 'permGrantPath', 'permCardCopy', 'permLeftS', 'fmtPermCountdown', 'permCardSignature', 'permQueueCardHtml']) load(name, ctx);
  const rec = {
    id: 'pa_1', status: 'pending', tool: 'run_terminal_command', host: 'qa-box', cwd: '/srv/app', command: 'npm install',
    description: 'Install package dependencies', requester: 'Main agent', reasons: ['shell command requires approval'],
    expires_at: new Date(Date.now() + 50000).toISOString(),
  };
  const html = ctx.permQueueCardHtml(rec);
  const desc = html.indexOf('chat-perm-card-desc">Install package dependencies<');
  const cmd = html.indexOf('chat-perm-card-cmd mono">npm install<');
  assert.ok(desc > 0 && cmd > desc, html);
  assert.notEqual(ctx.permCardSignature(rec), ctx.permCardSignature({ ...rec, description: '' }));
  assert.ok(!ctx.permQueueCardHtml({ ...rec, description: '' }).includes('chat-perm-card-desc'));
});

test('an interrupted call says why, never naming Claude', () => {
  const ctx = vm.createContext({});
  for (const name of ['executionState', 'ccInterruptedText']) load(name, ctx);
  assert.equal(ctx.ccInterruptedText({ name: 'run_terminal_command', status: 'interrupted', execution_status: 'interrupted', error: 'agent is offline', host: 'gpu-box' }), 'Interrupted · gpu-box went offline');
  assert.equal(ctx.ccInterruptedText({ name: 'run_terminal_command', status: 'interrupted', execution_status: 'cancelled', error: 'cancellation requested; remote termination is not confirmed', termination_confirmed: false }), 'Interrupted · stopped; the process may still be running');
  assert.equal(ctx.ccInterruptedText({ name: 'search_replace', status: 'interrupted', error: 'Tool interrupted.' }), 'Interrupted · the run ended before it finished');
  assert.equal(ctx.ccInterruptedText({ name: 'run_terminal_command', status: 'running' }), 'Interrupted · the run ended before it finished');
  // The hub's restart and lost-connection errors say so, not "the run ended".
  assert.equal(ctx.ccInterruptedText({ name: 'run_terminal_command', status: 'interrupted', error: 'connector restarted; remote outcome is unknown', host: 'box' }), 'Interrupted · the connector on box restarted');
  assert.equal(ctx.ccInterruptedText({ name: 'read_file', status: 'interrupted', error: 'agent connection lost; remote outcome is unknown', host: 'box' }), 'Interrupted · box lost its connection');
  // A group line counts its interrupted calls when not all of them were.
  const restarted = { name: 'run_terminal_command', status: 'interrupted', error: 'connector restarted; remote outcome is unknown', host: 'box' };
  assert.equal(ctx.ccInterruptedText(restarted, { interrupted: 1, calls: 9 }), '1 of 9 interrupted · the connector on box restarted');
  assert.equal(ctx.ccInterruptedText(restarted, { interrupted: 2, calls: 2 }), 'Interrupted · the connector on box restarted');
  assert.ok(!/claude/i.test(source.source.slice(source.source.indexOf('  function ccInterruptedText('), source.source.indexOf('\n  }', source.source.indexOf('  function ccInterruptedText(')))));
});
