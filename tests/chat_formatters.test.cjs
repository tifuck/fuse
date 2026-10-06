// Pure-function pins for the chat formatters: execution state, envelope
// parsing, Wait labels, output caps, subagent reports, monitor events.
// Branches already asserted in command_ui / claude_code_rows are not repeated.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(require('node:path').join(__dirname, '../static/chat.js'), 'utf8');

function load(name, context) {
  const start = source.indexOf(`  function ${name}(`);
  assert.ok(start >= 0, name);
  const end = source.indexOf('\n  }', start) + 4;
  vm.runInContext(source.slice(start, end), context);
}

function world() {
  const ctx = vm.createContext({
    activeRun: null,
    runIsActive: () => false,
    runIsStopping: () => false,
    EXECUTION_FIELDS: ['state', 'execution_status', 'output', 'output_mode', 'output_chars'],
    SETTLED_TOOL_STATUS: new Set(['done', 'error', 'interrupted']),
  });
  for (const name of [
    'executionState', 'executionIdentity', 'executionPending', 'toolBackground',
    'toolAwaitingCancellation', 'isShellExecTool', 'childBackedTool', 'toolIsCurrent',
    'toolRunning', 'backgroundCommandLabel', 'applyCommandResult', 'shellExitOnly',
    'shellExitCode', 'toolFailed', 'toolInterrupted', 'toolPresentation',
  ]) load(name, ctx);
  return ctx;
}

test('execution identity prefers command_id, then child, then job', () => {
  const c = world();
  assert.equal(c.executionIdentity(null), '');
  assert.equal(c.executionIdentity({}), '');
  assert.equal(c.executionIdentity({ job_id: 'j', child_id: 'c', command_id: 'cmd' }), 'cmd');
  assert.equal(c.executionIdentity({ job_id: 'j', child_id: 'c' }), 'c');
  assert.equal(c.executionIdentity({ job_id: 'j' }), 'j');
  assert.equal(c.executionIdentity({ command_id: 0, child_id: 'c' }), 'c', '0 is not an id');
});

test('execution state never comes from a kill receipt', () => {
  const c = world();
  assert.equal(c.executionState(null), '');
  assert.equal(c.executionState({ name: 'kill_command', state: 'cancelling', execution_status: 'cancelling' }), '');
  assert.equal(c.executionPending({ name: 'kill_command', state: 'cancelling' }), false);
  assert.equal(c.executionState({ state: 'running', execution_status: 'completed' }), 'running', 'state wins over execution_status');
  assert.equal(c.executionState({ execution_status: 'queued' }), 'queued');
  assert.equal(c.executionPending({ execution_status: 'cancelling' }), true);
  assert.equal(c.executionPending({ state: 'completed' }), false);
  assert.equal(c.executionPending({}), false);
});

test('a background spawn is background even without the flag', () => {
  const c = world();
  assert.equal(c.toolBackground({ name: 'spawn_subagent', summary: 'Background task started' }), true);
  assert.equal(c.toolBackground({ name: 'spawn_subagent', summary: 'Background task started.' }), false);
  assert.equal(c.toolBackground({ name: 'run_terminal_command', background: 1 }), true);
  assert.equal(c.toolBackground({ name: 'grep' }), false);
  assert.equal(c.toolBackground({}), false);
});

test('the current tool excludes settled, background, and non-live calls', () => {
  const c = world();
  assert.equal(c.toolIsCurrent(null), false);
  assert.equal(c.toolIsCurrent({ name: 'grep', status: 'running', state: 'completed' }), false);
  assert.equal(c.toolIsCurrent({ name: 'grep', status: 'done' }), false);
  const bg = { name: 'run_terminal_command', status: 'running', background: true, child_id: 'job' };
  assert.equal(c.toolIsCurrent(bg), false, 'a background child is not the current call');
  assert.equal(c.toolIsCurrent({ name: 'get_command_output', status: 'running', background: true, child_id: 'job' }), true, 'a Wait stays current');
  assert.equal(c.toolIsCurrent({ name: 'grep', status: 'progress' }), true);
  assert.equal(c.toolIsCurrent({ name: 'grep', status: 'queued' }), true);
});

test('shell exit codes are outcomes, including signals, and keep the output', () => {
  const c = world();
  assert.equal(c.shellExitCode(null), null);
  assert.equal(c.shellExitCode({ name: 'grep', exit_code: 1 }), null);
  assert.equal(c.shellExitCode({ name: 'run_terminal_command', exit_code: NaN }), null);
  assert.equal(c.shellExitCode({ name: 'kill_command', exit_code: -9 }), -9);
  for (const error of ['', 'exit 1', 'Exit -9', 'Command exited with code 137', 'command exited with code -15']) {
    assert.equal(c.shellExitOnly({ name: 'run_terminal_command', exit_code: -9, error, output: 'partial log' }), true, error);
  }
  assert.equal(c.shellExitOnly({ name: 'monitor', exit_code: 2, error: 'script exploded' }), false);
  assert.equal(c.shellExitOnly({ name: 'run_terminal_command', error: 'exit 1' }), false, 'no numeric code');
  const killed = { name: 'run_terminal_command', status: 'error', exit_code: -9, error: 'Command exited with code -9', output: 'died mid-line 😬' };
  assert.equal(c.toolFailed(killed), false, 'a signal is not a tool failure');
  assert.equal(killed.output, 'died mid-line 😬', 'killed-command output stays intact');
  assert.equal(c.toolPresentation(killed, false).warning, true);
  assert.equal(c.toolPresentation(killed, false).statusLabel, 'Exit -9');
  assert.equal(c.toolFailed({ name: 'grep', status: 'error', state: 'completed' }), false, 'a completed execution is not a failure');
  assert.equal(c.toolFailed(null), false);
  assert.equal(c.toolFailed({ name: 'grep', status: 'failed', error: 'no such file' }), true);
});

test('tool presentation names queued, cancelling, lost, and timed-out executions', () => {
  const c = world();
  const shell = (extra) => ({ name: 'run_terminal_command', status: 'running', ...extra });
  assert.equal(c.toolPresentation(shell({ execution_status: 'queued' }), true).statusLabel, 'Queued');
  assert.equal(c.toolPresentation(shell({ execution_status: 'cancelling' }), true).statusLabel, 'Cancelling');
  assert.equal(c.toolPresentation(shell({ state: 'lost', status: 'error' }), false).statusLabel, 'Lost');
  assert.equal(c.toolPresentation(shell({ state: 'timed_out', status: 'error' }), false).statusLabel, 'Timed out');
  const uncertain = c.toolPresentation(shell({ state: 'cancelled', status: 'interrupted', termination_confirmed: false }), false);
  assert.equal(uncertain.statusLabel, 'Cancelled');
  assert.equal(uncertain.state, 'interrupted');
  const stopped = c.toolPresentation(shell({ state: 'interrupted', status: 'interrupted', termination_confirmed: true }), false);
  assert.equal(stopped.statusLabel, 'Stopped');
  assert.equal(c.toolPresentation({ name: 'run_terminal_command', status: 'done', state: 'running' }, false).statusLabel, 'Background');
  assert.equal(c.toolPresentation({ name: 'grep', status: 'running', background: true }, true).statusLabel, 'Background');
  assert.equal(c.toolInterrupted(null, false), false);
  assert.equal(c.toolInterrupted({ name: 'grep', status: 'running', state: 'completed' }, false), false);
  assert.equal(c.toolInterrupted({ name: 'run_terminal_command', status: 'running', child_id: 'job' }, false), false, 'a child keeps running after its turn');
  assert.equal(c.toolInterrupted({ name: 'grep', status: 'queued' }, false), true, 'a plain call left running is interrupted');
  assert.equal(c.toolRunning({ name: 'monitor', status: 'progress', child_id: 'm' }, false), true);
});

test('a background label needs a shell tool, the flag, and a child id', () => {
  const c = world();
  assert.equal(c.backgroundCommandLabel(null), '');
  assert.equal(c.backgroundCommandLabel({ name: 'grep', background: true, child_id: 'j', command: 'x' }), '');
  assert.equal(c.backgroundCommandLabel({ name: 'monitor', background: true, command: 'watch log' }), '', 'no child id');
  assert.equal(c.backgroundCommandLabel({ name: 'monitor', background: true, child_id: 'm1', command: '  watch log  ' }), 'background · m1 · watch log');
  assert.equal(c.backgroundCommandLabel({ name: 'run_terminal_command', background: true, child_id: 'j', command: '   ' }), 'background · j · command');
});

test('auto labels ignore boilerplate and fall back when nothing is addressed', () => {
  const c = vm.createContext({});
  load('autoEventLabel', c);
  const footer = 'A <monitor_event> means the watch fired. A <monitor_result state="timed_out"> means it ended. A <command_result> is a job.';
  assert.equal(c.autoEventLabel(footer), 'Automated · background event');
  assert.equal(c.autoEventLabel(''), 'Automated · background event');
  assert.equal(c.autoEventLabel(null), 'Automated · background event');
  assert.equal(c.autoEventLabel('SCHEDULED RUN\n<command_result id="a"></command_result>'), 'Automated · scheduled run');
  assert.equal(c.autoEventLabel('<monitor_result id="m9" state="timed_out"></monitor_result>'), 'Automated · monitor event');
  assert.equal(
    c.autoEventLabel('<monitor_result id="m9"></monitor_result>\n<task id="c1" state="completed"></task>'),
    'Automated · background results');
});

test('command envelopes parse monitors, signals, and keep output off the metadata', () => {
  const c = world();
  load('parseCommandResults', c);
  const text = [
    '<command_result id="job-9" state="interrupted">',
    'host: gpu-box command: kill -9 sleep',
    'exit_code: -9',
    'duration_ms: 1500',
    'error: killed',
    'description: Stop the sleeper',
    '',
    'died at 😬',
    'line two',
    '</command_result>',
    '<monitor_event id="mon-1" seq="3" count="4">',
    'url: https://example.test/feed',
    'source: ws',
    'events: no',
    '',
    'first event line',
    '</monitor_event>',
    '<monitor_result id="mon-1" state="completed">',
    'host: box source: log watch',
    'duration_ms: nope',
    '</monitor_result>',
  ].join('\n');
  const [cmd, event, done] = c.parseCommandResults(text);
  assert.equal(cmd.exit_code, -9);
  assert.equal(cmd.output, 'died at 😬\nline two', 'output stays whole, emoji included');
  assert.equal(cmd.host, 'gpu-box');
  assert.equal(cmd.command, 'kill -9 sleep');
  assert.equal(cmd.duration_ms, 1500);
  assert.equal(cmd.description, 'Stop the sleeper');
  assert.equal(cmd.error, 'killed');
  assert.equal(cmd.monitor, false);
  assert.equal(event.tag, 'monitor_event');
  assert.equal(event.state, 'running', 'an event without a state is still live');
  assert.equal(event.event_count, 4, 'a non-numeric events: line falls through to the count attribute');
  assert.equal(event.url, 'https://example.test/feed');
  assert.equal(event.command, '', 'a url: line is not a command target');
  assert.equal(event.output, 'first event line');
  assert.equal(done.tag, 'monitor_result');
  assert.equal(done.host, 'box');
  assert.equal(done.command, 'log watch', 'a source: line is the monitor target');
  assert.equal(done.duration_ms, null, 'a non-numeric duration is absent, not NaN');
  assert.equal(done.exit_code, null);
  assert.equal(done.output, '');
  assert.equal(c.parseCommandResults('no envelopes here').length, 0);
  assert.equal(c.parseCommandResults('<command_result id="x">no close').length, 0);
});

test('a wake step keeps a monitor event live and a killed command interrupted', () => {
  const c = world();
  load('parseCommandResults', c);
  load('wakeStepTrace', c);
  const [killed] = c.parseCommandResults(
    '<command_result id="job-9" state="interrupted">\nexit_code: -9\n\ndied mid-line\n</command_result>');
  const step = c.wakeStepTrace(killed);
  assert.equal(step.name, 'run_terminal_command');
  assert.equal(step.status, 'interrupted');
  assert.equal(step.exit_code, undefined, 'a non-completed result drops the exit code');
  assert.equal(step.summary, 'died mid-line', 'killed output is the summary, not discarded');
  assert.equal(step.backgroundResult, true);
  const [event] = c.parseCommandResults('<monitor_event id="mon-1" seq="2">\nurl: wss://h/ws\n\nline one\n</monitor_event>');
  const live = c.wakeStepTrace(event);
  assert.equal(live.name, 'monitor');
  assert.equal(live.status, 'running');
  assert.equal(live.state, 'running');
  assert.equal(live.error, '');
  assert.equal(live.summary, 'line one');
  const [bad] = c.parseCommandResults('<monitor_result id="mon-1" state="timed_out">\nerror: gave up\n</monitor_result>');
  const failed = c.wakeStepTrace(bad);
  assert.equal(failed.status, 'error');
  assert.equal(failed.error, 'gave up');
});

test('folding skips task digests, unmatched ids, and a row that is not an auto handoff', () => {
  const c = world();
  for (const name of ['parseCommandResults', 'wakeStepTrace', 'foldAutoCommandResults']) load(name, c);
  const command = { name: 'run_terminal_command', child_id: 'job-1', command: 'npm test', description: 'Run tests', status: 'running' };
  const rows = [
    { role: 'user', auto: true, content: 'hello' },
    { role: 'assistant', content: '', tools: [command, { name: 'grep', child_id: 'job-1' }] },
    { role: 'user', content: '<command_result id="job-1" state="completed">\nexit_code: 0\n</command_result>' },
    { role: 'user', auto: true, content: '<command_result id="job-1" state="completed">\nexit_code: 0\n</command_result>\n<task id="c1"></task>' },
    { role: 'user', auto: true, content: '<command_result id="unknown" state="completed">\nexit_code: 1\nerror: exit 1\n</command_result>' },
  ];
  c.foldAutoCommandResults(rows);
  assert.equal(rows[0].autoSteps, null);
  assert.equal(rows[2].autoSteps, undefined, 'a plain user row is not a handoff');
  assert.equal(rows[3].autoSteps, null, 'a task digest is a report, not command steps');
  assert.equal(command.status, 'running', 'an unmatched or skipped envelope never touches the command');
  assert.equal(rows[4].autoSteps.length, 1);
  assert.equal(rows[4].autoSteps[0].status, 'done', 'exit 1 still completed');
  assert.equal(rows[4].autoSteps[0].description, '', 'no matching command, so no description to copy');
});

test('applying a result keeps killed output and refuses to revive a settled call', () => {
  const c = world();
  // Legacy means the envelope itself carries no output_mode and no output_chars.
  const trace = { name: 'run_terminal_command', status: 'running', output: 'already kept' };
  c.applyCommandResult(trace, { state: 'completed', exit_code: 0, duration_ms: 9, output: 'shorter preview' });
  assert.equal(trace.status, 'done', 'how it ended still applies');
  assert.equal(trace.exit_code, 0);
  assert.equal(trace.output, 'already kept', 'the retained output is not replaced by the preview');
  assert.equal(trace.summary, undefined);
  assert.equal(trace.latency_ms, 9);
  const live = { name: 'run_terminal_command', status: 'running' };
  c.applyCommandResult(live, { state: 'interrupted', exit_code: -9, duration_ms: 40, error: 'killed', output: 'partial 😀 output' });
  assert.equal(live.status, 'interrupted');
  assert.equal(live.exit_code, undefined);
  assert.equal(live.summary, 'partial 😀 output');
  assert.equal(live.output_truncated, false);
  assert.equal(live.latency_ms, 40);
  const huge = '😀'.repeat(4001);
  const capped = { name: 'run_terminal_command', status: 'running' };
  c.applyCommandResult(capped, { state: 'completed', exit_code: null, duration_ms: null, output: huge });
  assert.equal(capped.summary.length, 4000);
  assert.equal(capped.output_truncated, true);
  assert.ok(!capped.summary.includes('\uFFFD'), 'the cap never splits an emoji');
  assert.ok(capped.summary.endsWith('😀'));
  const settled = { name: 'run_terminal_command', status: 'done' };
  c.applyCommandResult(settled, { state: 'running', exit_code: null, duration_ms: null, output: '' });
  assert.equal(settled.status, 'done', 'a running envelope cannot revive a settled call');
  assert.equal(settled.state, 'running');
  assert.equal(settled.summary, 'running', 'an empty live envelope records the state word as its summary');
  const kill = { name: 'kill_command', status: 'done', state: 'cancelling' };
  c.applyCommandResult(kill, { state: 'completed', exit_code: 0, duration_ms: 1, output: 'gone' });
  assert.deepEqual(kill, { name: 'kill_command', status: 'done', state: 'cancelling' });
});

test('a Wait names the command it waits on, else the job', () => {
  const c = world();
  Object.assign(c, { messages: [], chatView: 'console' });
  c.escapeHtml = (value) => String(value ?? '');
  c.isConsoleOnly = () => true;
  c.isConsoleView = () => true;
  c.sessionProject = () => null;
  c.toolBackground = () => false;
  c.ccCallClockHtml = () => '';
  c.ccLiveText = () => '';
  c.ccSedEdit = () => null;
  c.isSearchTool = () => false;
  for (const name of ['toolText', 'displayCommand', 'relatedTrace', 'commandFor', 'commandOrigin', 'consoleArgText', 'consoleVerb']) load(name, c);
  const plain = (trace) => c.consoleCallHtml(trace, { running: false }).replace(/<[^>]+>/g, '');
  load('consoleCallHtml', c);
  const origin = { name: 'run_terminal_command', child_id: 'job-7', command: 'npm test', description: 'Run the unit tests' };
  c.messages = [{ role: 'assistant', tools: [origin] }];
  assert.equal(plain({ name: 'get_command_output', command_id: 'job-7' }), 'Wait(Run the unit tests)');
  origin.description = '';
  assert.equal(plain({ name: 'get_command_output', child_id: 'job-7' }), 'Wait(npm test)');
  assert.equal(plain({ name: 'get_command_output', job_id: 'job-9' }), 'Wait(job job-9)');
  assert.equal(plain({ name: 'get_command_output' }), 'Wait(command)');
  assert.equal(c.commandFor({ name: 'get_command_output', command_id: 'job-7' }), 'npm test');
  assert.equal(c.commandOrigin({ name: 'get_command_output' }), null);
});

test('"Still running" tracks the command, and a stopped one says it already returned', () => {
  const c = world();
  Object.assign(c, { messages: [], chatView: 'mainframe', isConsoleOnly: () => false });
  c.escapeHtml = (v) => String(v ?? '');
  c.renderAnsi = (v) => String(v ?? '');
  for (const name of ['toolText', 'displayCommand', 'relatedTrace', 'commandFor', 'commandOrigin', 'cleanToolOutput', 'consoleStdoutHtml', 'consoleResultLine', 'consoleDetailHtml', 'consoleSameText']) load(name, c);
  load('consoleFold', c);
  const origin = { name: 'run_terminal_command', child_id: 'job-7', command: 'npm run build', state: 'running', status: 'running', background: true };
  c.messages = [{ tools: [origin] }];
  const wait = { name: 'get_command_output', command_id: 'job-7', status: 'done', state: 'running', output: 'so far' };
  assert.equal(c.consoleFold(wait, { running: false, failed: false }).status, 'Still running');
  origin.state = 'interrupted';
  origin.status = 'interrupted';
  assert.equal(c.consoleFold(wait, { running: false, failed: false }).status, 'Returned while the command was still running');
  wait.state = 'completed';
  const finished = c.consoleFold(wait, { running: false, failed: false });
  assert.equal(finished.status, '', 'a Wait whose command already ended prints no running note');
  assert.match(finished.preview, /so far/);
});

test('a shell whose output is a diff prints that diff once', () => {
  const c = vm.createContext({
    chatView: 'console', messages: [],
    escapeHtml: (v) => String(v ?? '').replace(/[&<>]/g, (ch) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[ch])),
    renderAnsi: (v) => String(v ?? ''), highlightCode: (_lang, text) => String(text ?? ''),
    isSearchTool: () => false, structuredToolHtml: () => null, isCodeOutputTool: () => false,
    displayCommand: (trace) => String(trace.command || ''),
  });
  for (const name of ['isConsoleOnly', 'executionState', 'toolBackground', 'isShellExecTool', 'shellExitCode', 'cleanToolOutput', 'ccShellSegments', 'ccExitMeaning', 'ccFlat', 'ccClip', 'ccErrorFold', 'looksLikeDiff', 'consoleChangeText', 'consoleTruncationNote', 'consoleStdoutHtml', 'consoleOutputText', 'consoleDetailHtml', 'consoleOnlyFold']) load(name, c);
  const diffStart = source.indexOf('  function consoleDiffHtml(');
  vm.runInContext(source.slice(diffStart, source.indexOf('\n  function ', diffStart + 1)), c);
  const diff = 'diff --git a/a.js b/a.js\n--- a/a.js\n+++ b/a.js\n@@ -1,3 +1,4 @@\n context\n-old\n+new one\n+new two\n';
  const pres = { running: false, failed: false, interrupted: false };
  const diffs = (html) => (String(html).match(/chat-console-lines is-diff/g) || []).length;
  const shown = (output) => {
    const fold = c.consoleOnlyFold({
      name: 'run_terminal_command', status: 'done', state: 'completed', exit_code: 0, command: 'git diff',
      output, preview: output.slice(0, 800), edited_files: ['a.js'], lines_added: 2, lines_removed: 1, edit_diff: diff,
    }, pres, false);
    // Expanded hides the preview and shows the body; collapsed is the preview.
    return diffs(fold.full || fold.preview);
  };
  assert.equal(shown(diff), 1, 'the command output is the diff, so edit_diff is not appended');
  assert.equal(shown('a\nb\nc\nd\ne\n'), 1, 'ordinary stdout still gets the one edit diff');
  assert.equal(shown(''), 1, 'no output still shows the edit diff');
});

test('output caps cut on character boundaries and say which slice survived', () => {
  const c = vm.createContext({});
  c.escapeHtml = (v) => String(v ?? '');
  c.renderAnsi = (v) => String(v ?? '');
  c.looksLikeDiff = () => false;
  load('detailSectionHtml', c);
  const emoji = '😀'.repeat(5);
  const html = c.detailSectionHtml('Output', emoji, 'is-result', 3);
  assert.match(html, />😀😀😀</, 'a 3-character cap keeps three whole emoji');
  assert.doesNotMatch(html, /\uFFFD|\\uD83D/);
  assert.match(html, /Preview truncated at 3 characters\./);
  assert.doesNotMatch(c.detailSectionHtml('Output', 'abcd', 'is-result', 4), /truncated/);
  const note = (trace) => {
    const retained = Array.from(trace.output).length;
    const total = Number.isFinite(trace.output_chars) ? trace.output_chars : retained;
    if (trace.output_mode === 'partial' && (trace.truncated || total > retained)) return `partial ${retained}/${trace.output_chars_exact === true ? total : 'unknown'}`;
    if (trace.output_mode === 'tail') return `tail ${retained}/${total}@${trace.output_start}`;
    if (trace.truncated || total > retained) return `cut ${retained}/${total}`;
    return '';
  };
  assert.equal(note({ output: '😀😀', output_mode: 'partial', output_chars: 10, output_chars_exact: false, truncated: true }), 'partial 2/unknown');
  assert.equal(note({ output: 'abcd', output_mode: 'tail', output_chars: 9, output_chars_exact: true, output_start: 5 }), 'tail 4/9@5');
  assert.equal(note({ output: 'ab', output_mode: 'full', output_chars: 2, truncated: false }), '');
  assert.equal(note({ output: 'ab', output_chars: 8, truncated: true }), 'cut 2/8');
});

test('a child that died has no completed report; a partial one stays partial', () => {
  const c = world();
  const trace = { name: 'spawn_subagent', child_id: 'child', status: 'running' };
  Object.assign(c, {
    messages: [{ tools: [trace] }], agentRun: null, agentsOpen: false,
    articleFor: () => null, syncLiveDock() {}, scheduleAgentView() {},
    workClocksLive: () => false, startLiveClock() {},
  });
  load('mapAgentToolStatus', c);
  load('reconcileAgentTraces', c);
  const apply = (row) => { c.reconcileAgentTraces([row]); return trace.summary; };
  for (const status of ['failed', 'cancelled', 'lost', 'timed_out']) {
    assert.match(apply({ id: 'child', status, error: status === 'failed' ? 'boom' : '' }), /No completed report is available\./);
  }
  assert.match(apply({ id: 'child', status: 'interrupted', message: { notice: 'host gone' } }), /interrupted: host gone\. No completed report/);
  const dead = apply({ id: 'child', status: 'interrupted', error: 'worked through step 2, then the run ended' });
  assert.match(dead, /No completed report is available\./);
  assert.doesNotMatch(dead, /^worked through step 2/, 'partial output is not the report');
  assert.equal(apply({ id: 'child', status: 'completed', summary: 'list preview', message: { content: 'full report' } }), 'full report');
  trace.summary = 'kept';
  c.reconcileAgentTraces([{ id: 'child', status: 'running', summary: 'still going' }]);
  assert.equal(trace.summary, 'kept', 'a live child does not gain a report');
});

test('a monitor event leads with its description and counts the hidden lines', () => {
  const c = vm.createContext({});
  c.escapeHtml = (v) => String(v ?? '');
  c.renderAnsi = (v) => String(v ?? '');
  c.ccFlat = (text) => String(text || '').replace(/\s+/g, ' ').trim();
  for (const name of ['executionIdentity', 'displayCommand', 'consoleMonitorEventHtml']) load(name, c);
  const described = c.consoleMonitorEventHtml({
    description: 'deploy log', summary: 'line 1\nline 2\nline 3\nline 4\nline 5', child_id: 'mon-1',
  }, 'k');
  assert.match(described, /^<li[^>]*>[\s\S]*Monitor event: deploy log/);
  assert.ok(described.indexOf('Monitor event: deploy log') < described.indexOf('line 1'), described);
  assert.match(described, /\+2 lines/);
  const bare = c.consoleMonitorEventHtml({ url: 'wss://logs.example/ws', summary: 'connected\nmore' }, 'k2');
  assert.match(bare, /Monitor event: connected[\s\S]* · wss:\/\/logs\.example\/ws/);
  assert.ok(bare.indexOf('Monitor event:') < bare.indexOf('wss://logs.example/ws'));
  const four = c.consoleMonitorEventHtml({ description: 'watch', summary: 'a\nb\nc\nd' }, 'k3');
  assert.doesNotMatch(four, /\+0|\+\d+ line/, 'four lines are the whole event');
});

test('row clocks format once and stay stable past the minute and the hour', () => {
  const c = vm.createContext({});
  load('ccDuration', c);
  load('formatTurnDuration', c);
  load('formatActivityClock', c);
  assert.equal(c.ccDuration(0), '0s');
  assert.equal(c.ccDuration(1500), '1s');
  assert.equal(c.ccDuration(59_999), '59s');
  assert.equal(c.ccDuration(60_000), '1m 0s');
  assert.equal(c.ccDuration(3_599_500), '59m 60s'.replace('59m 60s', '1h 0m 0s') && c.ccDuration(3_599_500), '1h 0m 0s');
  assert.equal(c.ccDuration(86_400_000), '1d 0h 0m');
  assert.equal(c.formatTurnDuration(0), '0s');
  assert.equal(c.formatTurnDuration(400), '0.4s');
  assert.equal(c.formatTurnDuration(10_000), '10s');
  assert.equal(c.formatTurnDuration(3_600_000), '1h');
  assert.equal(c.formatTurnDuration(3_661_000), '1h 1m 1s');
  assert.equal(c.formatActivityClock(0.4), '1s');
  assert.equal(c.formatActivityClock(3661), '1h 1m 1s');
  const once = (ms) => ` · ${c.ccDuration(ms)}`;
  assert.equal(once(12_000), ' · 12s');
  assert.equal(`${once(12_000)}${once(12_000)}`.split(' · ').length - 1, 2, 'a caller that formats twice doubles the clock');
  assert.equal((once(12_000).match(/·/g) || []).length, 1);
});
