// Pure-function pins for the chat page's visible label text: group and
// summary labels, durations and clocks, truncation notices, and the status
// verb maps. Functions already pinned by command_ui / chat_formatters /
// claude_code_rows / markdown (escapeHtml, toolText, displayCommand,
// commandFor, toolPresentation, mapAgentToolStatus, ccDuration,
// formatTurnDuration, formatActivityClock, consoleResultLine, ...) are
// loaded here only as dependencies and are not asserted.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = require('./chat_source.cjs');

function load(name, context) {
  const themed = require("./theme_factory.cjs")(context)[name];
  if (typeof themed === "function") { context[name] = themed; return; }
  const start = source.indexOf(`  function ${name}(`);
  assert.ok(start >= 0, name);
  const end = source.indexOf('\n  }', start) + 4;
  vm.runInContext(source.slice(start, end), context);
}

function constant(name, context) {
  const start = source.indexOf(`  const ${name} =`);
  assert.ok(start >= 0, name);
  const end = source.indexOf(';\n', start) + 2;
  vm.runInContext(source.slice(start, end).replace(`const ${name}`, `var ${name}`), context);
}

// Tool presentation counting: toolCounts / countsLabel.
function executionWorld(extra = {}) {
  const ctx = vm.createContext(Object.assign({
    activeRun: null,
    runIsActive: () => false,
    runIsStopping: () => false,
    activeAssistant: () => null,
    EXECUTION_FIELDS: ['state', 'execution_status', 'output', 'output_mode', 'output_chars'],
  }, extra));
  for (const name of ['executionState', 'executionIdentity', 'executionPending', 'toolBackground',
    'toolAwaitingCancellation', 'isShellExecTool', 'childBackedTool', 'toolIsCurrent', 'toolRunning',
    'applyCommandResult', 'shellExitOnly', 'shellExitCode', 'toolFailed',
    'toolInterrupted', 'toolPresentation']) load(name, ctx);
  return ctx;
}

// Row labels: toolAction / toolSubject / toolRowLabel.
function rowWorld() {
  const ctx = vm.createContext({ messages: [], sessionProject: () => null, URL });
  for (const name of ['escapeHtml', 'toolText', 'displayCommand', 'relatedTrace', 'commandOrigin', 'commandFor',
    'executionIdentity', 'isShellExecTool', 'isSearchTool', 'shortToolPath', 'compactPath', 'searchCountFor',
    'searchQueries', 'agentDescriptionFor', 'toolAction', 'toolSubject', 'toolRowLabel']) load(name, ctx);
  return ctx;
}




test('summary counts name each state and never drop a category', () => {
  const c = executionWorld();
  load('toolCounts', c);
  load('countsLabel', c);
  const items = [
    { trace: { name: 'grep', status: 'done' } },
    { trace: { name: 'grep', status: 'error', error: 'boom' } },
    { trace: { name: 'grep', status: 'interrupted' } },
    { trace: { name: 'run_terminal_command', status: 'done', exit_code: 2 } },
    { trace: { name: 'grep', status: 'running' } },
    { trace: { name: 'grep', status: 'queued' } },
    { trace: { name: 'run_terminal_command', status: 'running', background: true, child_id: 'job-1' } },
  ];
  const counts = c.toolCounts(items, true);
  assert.equal(counts.running, 3);
  assert.equal(counts.failed, 1);
  assert.equal(counts.stopped, 1);
  assert.equal(counts.warning, 1);
  assert.equal(counts.done, 1);
  assert.deepEqual({ ...counts.phases }, { running: 1, queued: 1, background: 1 });
  assert.equal(c.countsLabel(counts),
    '1 running · 1 queued · 1 background · 1 done · 1 failed · 1 stopped · 1 nonzero exit');
  assert.equal(c.countsLabel({ running: 0, failed: 0, stopped: 0, warning: 0, done: 0, phases: {} }), '');
  // Counts without a phase breakdown still name the running work.
  assert.equal(c.countsLabel({ running: 2, failed: 1, stopped: 0, warning: 0, done: 0 }), '2 running · 1 failed');
  // History (not live): a call left running counts as stopped, not running.
  const stale = c.toolCounts([{ trace: { name: 'grep', status: 'running' } }], false);
  assert.equal(stale.stopped, 1);
  assert.equal(stale.running, 0);
  assert.equal(c.countsLabel({ running: 0, failed: 0, stopped: 0, warning: 2, done: 0, phases: {} }),
    '2 nonzero exits');
});

test('truncation notices say exactly what was kept', () => {
  const c = vm.createContext({});
  c.escapeHtml = (v) => String(v ?? '');
  load('consoleTruncationNote', c);
  // The notice text inside its wrapper span.
  const note = (trace) => c.consoleTruncationNote(trace).replace(/^<span[^>]*>/, '').replace(/<\/span>$/, '');
  assert.equal(c.consoleTruncationNote({}), '');
  assert.equal(c.consoleTruncationNote({ output: 42 }), '');
  assert.equal(note({ output: 'kept whole', output_chars: 10, truncated: false }), '');
  assert.equal(
    note({ output: 'ab', output_mode: 'partial', truncated: true, output_chars: 8 }),
    'Partial output · 2 characters retained · total unknown (at least 8).');
  assert.equal(
    note({ output: 'abcd', output_mode: 'partial', truncated: true, output_chars: 9, output_chars_exact: true }),
    'Partial output · 4 characters retained · total 9.');
  assert.equal(
    note({ output: 'abcd', output_mode: 'tail', output_chars: 9, output_chars_exact: false, output_start: 5 }),
    'Retained tail · 4 of at least 9 characters · starts at offset 5.');
  assert.equal(
    note({ output: 'abcd', output_mode: 'tail', output_chars: 9 }),
    'Retained tail · 4 of 9 characters.');
  assert.equal(
    note({ output: 'ab', output_chars: 8, truncated: true }),
    'Output truncated · 2 of 8 characters retained.');
  assert.equal(
    note({ output: 'ab', output_chars: 8, truncated: true, output_chars_exact: false }),
    'Output truncated · 2 of at least 8 characters retained.');
  // Counts are code points, so a kept emoji counts as one retained character.
  assert.equal(
    note({ output: '😀😀', output_chars: 5, truncated: true }),
    'Output truncated · 2 of 5 characters retained.');
  // A partial mode without truncation and nothing lost prints no notice.
  assert.equal(c.consoleTruncationNote({ output: 'ab', output_mode: 'partial', output_chars: 2, truncated: false }), '');
  const wrapped = c.consoleTruncationNote({ output: 'ab', output_chars: 8, truncated: true });
  assert.match(wrapped, /^<span class="chat-tool-truncated">/);
  assert.match(wrapped, /characters retained\.<\/span>$/);
});

test('paths shorten against the project and fold with an honest ellipsis', () => {
  const c = vm.createContext({ sessionProject: () => null });
  for (const name of ['shortToolPath', 'compactPath']) load(name, c);
  assert.equal(c.shortToolPath('/work/proj/src/a.py', { project_root: '/work/proj' }), 'src/a.py');
  assert.equal(c.shortToolPath('/work/proj', { project_root: '/work/proj' }), '.');
  assert.equal(c.shortToolPath('/elsewhere/a.py', { project_root: '/work/proj' }), '/elsewhere/a.py');
  assert.equal(c.shortToolPath('relative/a.py', { project_root: '/work/proj' }), 'relative/a.py');
  assert.equal(c.shortToolPath('C:\\work\\proj\\a.py', { platform: 'windows', project_root: 'C:\\work\\proj' }), 'a.py');
  // A different machine's path is never shortened against this project.
  assert.equal(c.shortToolPath('/work/proj/a.py', { host: 'other-box', project_root: '' }), '/work/proj/a.py');

  assert.equal(c.compactPath('src/a.py', {}), 'src/a.py');
  // Long leaf, nothing worth absorbing: the ellipsis hides only middle dirs.
  assert.equal(c.compactPath('aa/bbb/ccc/ddd/eeee.txt', {}, 12), 'aa/…/eeee.txt');
  // More room: trailing directories come back while they fit.
  assert.equal(c.compactPath('aa/bbb/ccc/ddd/eeee.txt', {}, 20), 'aa/…/ddd/eeee.txt');
  assert.equal(c.compactPath('C:\\alpha\\bravo\\charlie\\delta\\epsilon-long-name.txt', { platform: 'windows' }, 24),
    'C:/…/epsilon-long-name.txt');
  // A single long segment has no directories to hide; the leaf stays whole.
  assert.equal(c.compactPath('y'.repeat(70), {}), '…/' + 'y'.repeat(70));
  // A long root-level file folds to '/…/<leaf>' with no doubled slash.
  const rootLeaf = 'x'.repeat(60) + '.txt';
  assert.equal(c.compactPath('/' + rootLeaf), '/…/' + rootLeaf);
});

test('tool rows keep their verb map and plain-word fallbacks', () => {
  const c = vm.createContext({});
  load('toolAction', c);
  const labels = {
    read_file: 'Read', list_dir: 'List', grep: 'Grep', glob: 'Glob', write_file: 'Write', search_replace: 'Edit',
    run_terminal_command: 'Shell', get_command_output: 'Output', kill_command: 'Stop', monitor: 'Watch',
    spawn_subagent: 'Agent', get_subagent_output: 'Result', kill_subagent: 'Stop', list_subagents: 'Agents',
    search_models: 'Models', search_tools: 'Tools', load_skill: 'Skill', propose_skill: 'Draft', todo_write: 'Tasks',
    scratch: 'Scratch', read_url: 'Page', web_search: 'Search', search: 'Search',
    create_artifact: 'Create', update_artifact: 'Update', 'fuse-steer': 'Notice',
  };
  for (const [name, label] of Object.entries(labels)) assert.equal(c.toolAction({ name }), label, name);
  assert.equal(c.toolAction({ name: 'my_custom_tool' }), 'my custom tool', 'unknown tools become words');
  assert.equal(c.toolAction({ name: '' }), 'Tool');
  assert.equal(c.toolAction({}), 'Tool');
});

test('row subjects name what each call worked on', () => {
  const c = rowWorld();
  assert.equal(c.toolSubject({ name: 'monitor', url: 'wss://logs.example/ws' }), 'wss://logs.example/ws');
  assert.equal(c.toolSubject({ name: 'monitor', command: 'watch log' }), 'watch log');
  assert.equal(c.toolSubject({ name: 'monitor', child_id: 'mon-1' }), 'mon-1');
  assert.equal(c.toolSubject({ name: 'monitor' }), 'watch');
  assert.equal(c.toolSubject({ name: 'run_terminal_command', command: ' ls -la ' }), 'ls -la');
  assert.equal(c.toolSubject({ name: 'run_terminal_command' }), 'command');
  assert.equal(c.toolSubject({ name: 'read_file', path: '/work/proj/src/a.py', project_root: '/work/proj' }), 'src/a.py');
  assert.equal(c.toolSubject({ name: 'read_file', path: '/work/proj/src/a.py', project_root: '/work/proj', offset: 10, limit: 5 }),
    'src/a.py :10–14', 'a bounded read shows its range');
  assert.equal(c.toolSubject({ name: 'read_file', path: '/work/proj/a.py', project_root: '/work/proj', offset: 3 }), 'a.py :3');
  assert.equal(c.toolSubject({ name: 'grep', pattern: 'foo', path: '/work/proj/src', project_root: '/work/proj' }), 'foo in src');
  assert.equal(c.toolSubject({ name: 'grep', pattern: 'foo' }), 'foo');
  assert.equal(c.toolSubject({ name: 'grep' }), 'pattern');
  assert.equal(c.toolSubject({ name: 'glob', query: '*.cjs' }), '*.cjs');
  assert.equal(c.toolSubject({ name: 'spawn_subagent', description: 'Find the bug' }), 'Find the bug');
  assert.equal(c.toolSubject({ name: 'spawn_subagent', description: 'X', model: 'sonnet' }), 'X · sonnet');
  assert.equal(c.toolSubject({ name: 'spawn_subagent' }), 'subagent');
  assert.equal(c.toolSubject({ name: 'read_url', url: 'https://www.example.com/docs/page' }), 'example.com/docs/page');
  assert.equal(c.toolSubject({ name: 'read_url', url: 'https://example.com/' }), 'example.com');
  assert.equal(c.toolSubject({ name: 'read_url', url: 'not a url' }), 'not a url');
  assert.equal(c.toolSubject({ name: 'web_search', query: 'fuse router' }), 'fuse router');
  assert.equal(c.toolSubject({ name: 'web_search', queries: ['a', 'b'] }), '2 queries');
  assert.equal(c.toolSubject({ name: 'web_search', queries: ['a', ' '] }), 'a', 'blank queries do not count');
  assert.equal(c.toolSubject({ name: 'create_artifact', title: 'Report' }), 'Report');
  assert.equal(c.toolSubject({ name: 'update_artifact' }), 'artifact');
  assert.equal(c.toolSubject({ name: 'fuse-steer', summary: 'Do X' }), 'Do X');
  assert.equal(c.toolSubject({ name: 'fuse-steer' }), 'New instruction');
  assert.equal(c.toolSubject({ name: 'todo_write', query: 'ship it' }), 'ship it');
  // The row label joins verb and subject on one separator, once.
  assert.equal(c.toolRowLabel({ name: 'read_file', path: '/work/proj/src/a.py', project_root: '/work/proj' }), 'Read · src/a.py');
  assert.equal(c.toolRowLabel({ name: 'scratch' }), 'Scratch');
});

test('spinner state names the call that is running', () => {
  const c = vm.createContext({});
  constant('CC_SPINNER_STATES', c);
  for (const name of ['executionState', 'isSearchTool', 'ccSpinnerState']) load(name, c);
  assert.equal(c.ccSpinnerState({ phase: 'queued', label: 'Queued' }), 'queued');
  assert.equal(c.ccSpinnerState({ phase: 'stopping', label: 'Stopping' }), 'stopping');
  assert.equal(c.ccSpinnerState({ phase: 'waiting', label: 'Waiting for model' }), 'waiting');
  assert.equal(c.ccSpinnerState({ phase: 'starting', label: 'Working' }), 'waiting');
  assert.equal(c.ccSpinnerState({ phase: 'working', label: 'Working' }), 'working');
  const of = (name, extra = {}) => c.ccSpinnerState({ phase: 'working', label: 'Working', current: { name, ...extra } });
  assert.equal(of('run_terminal_command'), 'running command');
  assert.equal(of('get_command_output'), 'waiting for command');
  assert.equal(of('kill_command'), 'stopping command');
  assert.equal(of('read_file'), 'reading');
  assert.equal(of('write_file'), 'writing file');
  assert.equal(of('search_replace'), 'editing');
  assert.equal(of('list_dir'), 'listing');
  assert.equal(of('read_url'), 'fetching');
  assert.equal(of('spawn_subagent'), 'running agent');
  assert.equal(of('get_subagent_output'), 'waiting for agent');
  assert.equal(of('kill_subagent'), 'stopping agent');
  assert.equal(of('list_subagents'), 'listing agents');
  assert.equal(of('monitor'), 'watching');
  assert.equal(of('create_artifact'), 'building artifact');
  assert.equal(of('update_artifact'), 'building artifact');
  assert.equal(of('search_models'), 'finding models');
  assert.equal(of('web_search'), 'searching the web', 'search tools name the web, not a file');
  assert.equal(of('search'), 'searching the web');
  // A tool with no spinner verb still gets a state, not silence.
  assert.equal(of('todo_write'), 'running tool');
  // The execution, not the call row, owns cancellation.
  assert.equal(of('read_file', { state: 'cancelling' }), 'cancelling');
  assert.equal(of('read_file', { execution_status: 'cancelling' }), 'cancelling');
  // Counts appended to the activity label survive the join, once.
  assert.equal(c.ccSpinnerState({ phase: 'working', label: 'Working · 2 active / 1 background' }),
    'working · 2 active / 1 background');
  assert.equal(c.ccSpinnerState({ phase: 'thinking', label: 'Thinking' }), '');
});

test('thinking status deepens with time and then flashes the thought', () => {
  const c = vm.createContext({});
  load('ccThinkingState', c);
  const at = (ms) => ({ _spinThink: { since: Date.now() - ms, flash: null } });
  assert.equal(c.ccThinkingState({}, true), 'thinking');
  assert.equal(c.ccThinkingState(at(9000), true), 'thinking');
  assert.equal(c.ccThinkingState(at(10000), true), 'still thinking');
  assert.equal(c.ccThinkingState(at(25000), true), 'thinking more');
  assert.equal(c.ccThinkingState(at(35000), true), 'thinking some more');
  assert.equal(c.ccThinkingState(at(50000), true), 'deep in thought');
  // Ending a thought flashes its length for two seconds, then clears.
  const msg = at(4200);
  assert.equal(c.ccThinkingState(msg, false), 'thought for 4s');
  assert.equal(c.ccThinkingState(msg, false), 'thought for 4s', 'the flash holds while it is on screen');
  msg._spinThink.flash.until = Date.now() - 1;
  assert.equal(c.ccThinkingState(msg, false), '');
  assert.equal(c.ccThinkingState({ _spinThink: { since: Date.now(), flash: null } }, false), 'thought for 1s',
    'a sub-second thought still reads as one second');
  assert.equal(c.ccThinkingState({}, false), '', 'no thought, no note');
});

test('agent done receipts count tool uses, tokens, and time', () => {
  const c = vm.createContext({});
  constant('CC_COMPACT', c);
  for (const name of ['ccTokens', 'ccDuration', 'ccAgentDoneText']) load(name, c);
  assert.equal(c.ccAgentDoneText({}), 'Done');
  assert.equal(c.ccAgentDoneText({ agent_tool_uses: 1 }), 'Done (1 tool use)');
  assert.equal(c.ccAgentDoneText({ agent_tool_uses: 14 }), 'Done (14 tool uses)');
  assert.equal(c.ccAgentDoneText({ agent_tool_uses: 0 }), 'Done (0 tool uses)');
  assert.equal(c.ccAgentDoneText({ agent_tokens: 500 }), 'Done (500 tokens)');
  assert.equal(c.ccAgentDoneText({ agent_tokens: 32100 }), 'Done (32.1k tokens)');
  assert.equal(c.ccAgentDoneText({ agent_tokens: 0 }), 'Done', 'zero tokens is no tokens');
  assert.equal(c.ccAgentDoneText({ agent_work_ms: 65000 }), 'Done (1m 5s)');
  assert.equal(c.ccAgentDoneText({ agent_work_ms: 0 }), 'Done (1s)', 'work time is at least one second');
  assert.equal(c.ccAgentDoneText({ agent_tool_uses: 14, agent_tokens: 32100, agent_work_ms: 65000 }),
    'Done (14 tool uses · 32.1k tokens · 1m 5s)');
  assert.equal(c.ccAgentDoneText({ agent_tool_uses: 'x', agent_tokens: 'y', agent_work_ms: 'z' }), 'Done',
    'non-numeric receipts contribute nothing');
});

test('background task counts pluralize each kind', () => {
  const c = vm.createContext({ jobsRows: [], agentsRows: [] });
  for (const name of ['agentLive', 'ccStillRunningCount', 'ccStillRunningText']) load(name, c);
  c.jobsRows = [{ state: 'running' }];
  assert.equal(c.ccStillRunningCount(), '1 shell');
  c.jobsRows = [{ state: 'running' }, { state: 'queued' }];
  assert.equal(c.ccStillRunningCount(), '2 shells');
  c.jobsRows = [{ kind: 'monitor', state: 'running' }, { kind: 'monitor', state: 'cancelling' }];
  assert.equal(c.ccStillRunningCount(), '2 monitors');
  c.jobsRows = [{ state: 'completed' }, { state: 'failed' }, { status: 'cancelled' }];
  assert.equal(c.ccStillRunningCount(), '', 'settled rows are not background work');
  c.agentsRows = [{ status: 'running' }];
  assert.equal(c.ccStillRunningCount(), '1 local agent');
  c.agentsRows = [{ status: 'queued' }, { status: 'running' }, { status: 'completed' }];
  assert.equal(c.ccStillRunningCount(), '2 local agents');
  c.jobsRows = [{ kind: 'monitor', state: 'running' }, { state: 'running' }, { state: 'running' }];
  c.agentsRows = [{ status: 'running' }, { status: 'running' }];
  assert.equal(c.ccStillRunningCount(), '5 background tasks', 'two kinds collapse to the total');
  c.jobsRows = [];
  c.agentsRows = [];
  assert.equal(c.ccStillRunningCount(), '');
  assert.equal(c.ccStillRunningText(), '');
});

test('the turn line says done, interrupted, or failed', () => {
  const c = vm.createContext({ jobsRows: [], agentsRows: [] });
  for (const name of ['escapeHtml', 'ccDuration', 'ccTurnVerb', 'ccDoneAt', 'agentLive', 'ccStillRunningCount', 'ccStillRunningText', 'ccTurnLineHtml']) load(name, c);
  constant('CC_TURN_VERBS', c);
  const head = '<span class="chat-console-turn-mark" aria-hidden="true">✻</span> ';
  assert.equal(c.ccTurnLineHtml({ elapsedMs: null }, false), '');
  const when = new Date();
  when.setHours(10, 0, 0, 0);
  const today = when.toISOString();
  const time = when.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
  const verb = c.ccTurnVerb({ id: 'turn-1' });
  assert.equal(c.ccTurnLineHtml({ id: 'turn-1', elapsedMs: 65000, endedAt: today }, false),
    `${head}<span>${verb} for 1m 5s · done ${time}</span>`);
  const stopped = c.ccTurnVerb({ id: 'turn-2' });
  const stoppedLine = c.ccTurnLineHtml({ id: 'turn-2', elapsedMs: 5000, stopped: true, endedAt: today }, true);
  assert.equal(stoppedLine, `${head}<span>Interrupted · ${stopped} for 5s</span>`,
    'a stopped turn never finished, so it prints no done time');
  const failed = c.ccTurnVerb({ id: 'turn-3' });
  assert.equal(c.ccTurnLineHtml({ id: 'turn-3', elapsedMs: 0, error: 'boom', endedAt: today }, true),
    `${head}<span>Failed · ${failed} for 0s</span>`);
  c.agentsRows = [{ status: 'running' }];
  assert.equal(c.ccTurnLineHtml({ id: 'turn-4', elapsedMs: 65000, endedAt: today }, true),
    `${head}<span>Waiting for <b>1</b> background agent to finish</span>`);
  c.agentsRows = [{ status: 'running' }, { status: 'queued' }];
  assert.match(c.ccTurnLineHtml({ id: 'turn-5', elapsedMs: 65000, endedAt: today }, true),
    /<b>2<\/b> background agents to finish/);
  c.agentsRows = [];
  c.jobsRows = [{ kind: 'shell', state: 'running' }];
  const latest = c.ccTurnLineHtml({ id: 'turn-6', elapsedMs: 65000, endedAt: today }, true);
  assert.match(latest, / · 1 shell still running<\/span>$/, 'the latest line reports live background work');
  const older = c.ccTurnLineHtml({ id: 'turn-7', elapsedMs: 65000, endedAt: today }, false);
  assert.doesNotMatch(older, /still running/, 'older lines are history, not live status');
});

test('done-at times pick the right grain for the day distance', () => {
  const c = vm.createContext({});
  load('ccDoneAt', c);
  assert.equal(c.ccDoneAt(''), '');
  assert.equal(c.ccDoneAt('not a date'), '');
  assert.equal(c.ccDoneAt(null), '');
  const at = (daysAgo) => {
    const when = new Date();
    when.setDate(when.getDate() - daysAgo);
    when.setHours(10, 0, 0, 0);
    return when;
  };
  const timeOf = (when) => when.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
  const weekdayOf = (when) => when.toLocaleDateString([], { weekday: 'long' });
  const dateOf = (when) => when.toLocaleDateString([], { weekday: 'long', month: 'short', day: 'numeric' });
  assert.equal(c.ccDoneAt(at(0).toISOString()), timeOf(at(0)), 'today is just the time');
  assert.equal(c.ccDoneAt(at(3).toISOString()), `${weekdayOf(at(3))} ${timeOf(at(3))}`, 'this week names the day');
  assert.equal(c.ccDoneAt(at(6).toISOString()), `${weekdayOf(at(6))} ${timeOf(at(6))}`, 'six days back is still this week');
  assert.equal(c.ccDoneAt(at(7).toISOString()), `${dateOf(at(7))}, ${timeOf(at(7))}`, 'a week back names the date');
  assert.equal(c.ccDoneAt(at(30).toISOString()), `${dateOf(at(30))}, ${timeOf(at(30))}`);
});

test('turn meta shows output tokens and elapsed time', () => {
  const c = vm.createContext({});
  for (const name of ['usageInOut', 'formatTokens', 'formatTurnDuration', 'formatTurnMeta']) load(name, c);
  const bits = c.formatTurnMeta({ usage: { prompt_tokens: 100, output_tokens: 500 }, elapsedMs: 1500 });
  assert.deepEqual({ ...bits[0] }, { text: '500 tok', title: '500 output tokens this reply' });
  assert.deepEqual({ ...bits[1] }, { text: '1.5s', title: '1.5s elapsed' });
  assert.equal(c.formatTurnMeta({ usage: { prompt_tokens: 100 } }).length, 0,
    'console prints output tokens only');
});


test('usage splits input and output tokens', () => {
  const c = vm.createContext({});
  load('usageInOut', c);
  assert.equal(c.usageInOut(null), null);
  assert.equal(c.usageInOut('nope'), null);
  assert.deepEqual({ ...c.usageInOut({ prompt_tokens: 10, output_tokens: 5 }) }, { inn: 10, out: 5 });
  assert.deepEqual({ ...c.usageInOut({ input_tokens: 7, completion_tokens: 2 }) }, { inn: 7, out: 2 });
  assert.deepEqual({ ...c.usageInOut({ prompt_tokens: 3, input_tokens: 9 }) }, { inn: 3, out: 0 },
    'prompt_tokens wins over the alternate field');
  assert.deepEqual({ ...c.usageInOut({ prompt_tokens: 3 }) }, { inn: 3, out: 0 });
  assert.equal(c.usageInOut({ prompt_tokens: 0, output_tokens: 0 }), null, 'no usage recorded');
  assert.deepEqual({ ...c.usageInOut({ prompt_tokens: -5, output_tokens: '3' }) }, { inn: 0, out: 3 },
    'counts never go negative');
  assert.equal(c.usageInOut({ prompt_tokens: 'x' }), null, 'junk is not a count');
});

test('token counts abbreviate at thousand and million', () => {
  const c = vm.createContext({});
  for (const name of ['formatTokens', 'formatConsoleCount']) load(name, c);
  for (const fmt of [c.formatTokens, c.formatConsoleCount]) {
    assert.equal(fmt(0), '0');
    assert.equal(fmt(999), '999');
    assert.equal(fmt(1000), '1k');
    assert.equal(fmt(1500), '1.5k');
    assert.equal(fmt(9999), '10k');
    assert.equal(fmt(10000), '10k');
    assert.equal(fmt(10500), '11k');
    assert.equal(fmt(1000000), '1M');
    assert.equal(fmt(1500000), '1.5M');
    assert.equal(fmt(12000000), '12M');
    assert.equal(fmt(-7), '0');
    assert.equal(fmt(NaN), '0');
    assert.equal(fmt(999999), '1M', 'a rounded k count that reaches 1000 hands off to M');
  }
});

test('sub-second durations stay in ms and hand off above a second', () => {
  const c = vm.createContext({});
  load('formatTurnDuration', c);
  load('humanMs', c);
  assert.equal(c.humanMs(0), '0ms');
  assert.equal(c.humanMs(450), '450ms');
  assert.equal(c.humanMs(999), '999ms');
  assert.equal(c.humanMs(1000), '1.0s', 'seconds below ten keep one decimal');
  assert.equal(c.humanMs(1500), '1.5s');
  assert.equal(c.humanMs(2000), '2.0s');
  assert.equal(c.humanMs(9500), '9.5s');
  assert.equal(c.humanMs(10000), '10s', 'whole seconds at ten drop the decimal');
  assert.equal(c.humanMs(25000), '25s');
  assert.equal(c.humanMs(65000), '1m 5s');
  assert.equal(c.humanMs(-5), '');
  assert.equal(c.humanMs(NaN), '');
  assert.equal(c.humanMs('x'), '');
  // A rounded value that reaches the unit boundary renders in the next unit.
  assert.equal(c.humanMs(999.6), '1.0s');
});

test('console durations prefer tracked work time', () => {
  const c = vm.createContext({});
  for (const name of ['toolBackground', 'agentTraceWorkMs', 'formatActivityClock', 'consoleDurationLabel']) load(name, c);
  assert.equal(c.agentTraceWorkMs({ name: 'grep' }), undefined, 'only agents carry work time');
  assert.equal(c.agentTraceWorkMs({ name: 'spawn_subagent', agent_work_ms: 5 }), 5);
  assert.equal(c.agentTraceWorkMs({ name: 'spawn_subagent', agent_work_ms: 'x' }), undefined);
  assert.equal(c.agentTraceWorkMs({ name: 'spawn_subagent', agent_work_ms: -5 }), undefined, 'negative work is no work');
  assert.equal(c.agentTraceWorkMs({ name: 'spawn_subagent', background: true, child_id: 'c' }), null,
    'a background agent without work tracking is unknown, not zero');
  assert.equal(c.consoleDurationLabel({ name: 'spawn_subagent', agent_work_ms: 65000 }), '1m 5s');
  assert.equal(c.consoleDurationLabel({ name: 'spawn_subagent', agent_work_ms: 500 }), '1s');
  assert.equal(c.consoleDurationLabel({ name: 'spawn_subagent', agent_work_ms: 0 }), '1s');
  assert.equal(c.consoleDurationLabel({ name: 'spawn_subagent', agent_work_ms: -5, duration_ms: 2500 }), '3s',
    'without work tracking a foreground agent falls back to wall time');
  assert.equal(c.consoleDurationLabel({ name: 'spawn_subagent', background: true, child_id: 'c', duration_ms: 2500 }), '',
    'wall time never labels a background agent');
  assert.equal(c.consoleDurationLabel({ name: 'run_terminal_command', execution_duration_ms: 2500, duration_ms: 9000, elapsed_ms: 99000 }), '3s');
  assert.equal(c.consoleDurationLabel({ name: 'run_terminal_command', duration_ms: 1500 }), '2s');
  assert.equal(c.consoleDurationLabel({ name: 'run_terminal_command', duration_ms: 90000 }), '1m 30s');
  assert.equal(c.consoleDurationLabel({ name: 'run_terminal_command', duration_ms: -1 }), '');
  assert.equal(c.consoleDurationLabel({ name: 'run_terminal_command' }), '');
  assert.equal(c.consoleDurationLabel(null), '', 'no trace, no duration to show');
});

test('activity elapsed text only appears once there is time to show', () => {
  const c = vm.createContext({});
  for (const name of ['formatActivityClock', 'formatActivityElapsed']) load(name, c);
  assert.equal(c.formatActivityElapsed(null, false), '');
  assert.equal(c.formatActivityElapsed(undefined, true), '');
  assert.equal(c.formatActivityElapsed(0.5, true), '', 'a live clock under a second prints nothing yet');
  assert.equal(c.formatActivityElapsed(0, true), '');
  assert.equal(c.formatActivityElapsed(0, false), ' for 1s', 'a settled zero still rounds up to one second');
  assert.equal(c.formatActivityElapsed(30, true), ' for 30s');
  assert.equal(c.formatActivityElapsed(3661, true), ' for 1h 1m 1s');
});



test('untitled threads hide their title', () => {
  const c = vm.createContext({});
  load('isUntitledThread', c);
  assert.equal(c.isUntitledThread(null), true);
  assert.equal(c.isUntitledThread({}), true);
  assert.equal(c.isUntitledThread({ title: '   ' }), true);
  assert.equal(c.isUntitledThread({ title: 'New Chat' }), true, 'the default title is no title');
  assert.equal(c.isUntitledThread({ title: 'Fix the router' }), false);
  assert.equal(c.isUntitledThread({ kind: 'project', project_name: 'Fuse', title: ' fuse ' }), true,
    'a project thread named after its project is untitled');
  assert.equal(c.isUntitledThread({ kind: 'project', project_name: 'Fuse', title: 'Fuse Dev' }), false);
  assert.equal(c.isUntitledThread({ project_name: 'Fuse', title: 'Fuse' }), false,
    'only project threads borrow their project name');
});
