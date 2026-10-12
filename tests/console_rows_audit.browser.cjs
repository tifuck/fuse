// Row-state audit for the default transcript. Seeds row types and state combinations
// the existing Console suite never generates (tests/console_ui.browser.cjs +
// console_ui_lib.js generate(), claude_code_rows.test.cjs, command_ui.test.cjs),
// and checks every render against the latest state of each call and command.
//
// Not covered here, because those suites already pin them: a single running
// foreground command, a timed-out Wait that later ends, one monitor event with
// a description, a background command that completes with a non-zero exit, a
// grep miss, one search_replace at hunk line 1, thinking shown and hidden,
// status-line text scaling.
const assert = require('node:assert/strict');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const origin = process.env.FUSE_BROWSER_ORIGIN || 'http://127.0.0.1:8099';
assert.match(origin, /^http:\/\/127\.0\.0\.1:\d+$/);

const ROOT = { host: 'qa-box', cwd: '/srv/qa', platform: 'posix', project_root: '/srv/qa' };
let seq = 0;
const tool = (name, fields = {}) => ({
  id: `r${++seq}`, name, status: 'done', error: '', results: [], output: '',
  duration_ms: 120, call_duration_ms: 120, ...ROOT, ...fields,
});
const sh = (command, fields = {}) => {
  const job = fields.job || `j${++seq}`;
  return tool('run_terminal_command', {
    command, child_id: job, command_id: job, job_id: job, timeout_ms: 120000, termination_confirmed: true,
    status: 'done', state: 'completed', execution_status: 'completed', exit_code: 0,
    elapsed_ms: fields.ms ?? 900, execution_duration_ms: fields.ms ?? 900,
    output: fields.output || '', preview: (fields.output || '').slice(0, 120), ...fields.extra,
    ...(fields.description ? { description: fields.description } : {}),
    ...(fields.background ? { background: true } : {}),
  });
};
const think = (text, ms) => ({ type: 'reasoning', text, ms });
const T = (trace) => ({ type: 'tool', id: trace.id });
const say = (text) => ({ type: 'content', text });
const turn = (segments, tools, extra = {}) => ({
  role: 'assistant', model: 'qa/model', elapsedMs: 30000, tools, segments,
  content: segments.filter((s) => s.type === 'content').map((s) => s.text).join(''),
  reasoning: segments.filter((s) => s.type === 'reasoning').map((s) => s.text).join(''), ...extra,
});
const user = (content, extra = {}) => ({ role: 'user', content, reasoning: '', error: '', ...extra });
const ended = { endedAt: '2026-10-03T20:00:00Z' };
const env = (tag, id, attrs, meta, body = '') => `<${tag} id="${id}"${attrs ? ` ${attrs}` : ''}>\n${meta}\n\n${body}</${tag}>`;

function scenarios() {
  const list = [];
  const add = (name, build) => list.push({ name, ...build() });

  // —— Call states the generator never pairs ——
  add('a queued command reads as queued, not as a running command', () => {
    // Console prints no "Queued" word on the row (that word is the status
    // line's). The row must not borrow the live "Running <command>" headline.
    const cmd = sh('npm run build', { extra: { status: 'queued', state: 'queued', execution_status: 'queued', exit_code: null, execution_duration_ms: null } });
    return { rows: [user('Go.'), turn([T(cmd)], [cmd])], live: true, phase: 'tools',
      truth: { call: { [cmd.id]: 'running' }, exec: { [cmd.command_id]: 'queued' } },
      has: ['Queued'], lacks: ['Running npm run build', 'Running in the background'], status: 'queued' };
  });
  add('a command being cancelled says Cancelling on the status line, with one clock', () => {
    const cmd = sh('npm run e2e', { ms: 8000, extra: { status: 'running', state: 'cancelling', execution_status: 'cancelling', exit_code: null, execution_duration_ms: null } });
    return { rows: [user('Go.'), turn([T(cmd)], [cmd])], live: true, phase: 'tools',
      truth: { call: { [cmd.id]: 'running' }, exec: { [cmd.command_id]: 'cancelling' } },
      has: ['Cancelling'], lacks: ['Running npm run e2e', 'Interrupted'], clocks: { [cmd.id]: 1 }, status: 'cancelling' };
  });
  add('a deadline is a failure, never an interruption', () => {
    // The newest call of a live turn stands alone, which is where the
    // "Timed out in the background" line is printed.
    const cmd = sh('npm run build', { background: true, ms: 120000, job: 'dead1', extra: { status: 'error', state: 'timed_out', execution_status: 'timed_out', error: 'timed out', exit_code: null } });
    return { rows: [user('Go.'), turn([say('Building.\n\n'), T(cmd)], [cmd])], live: true, phase: 'model',
      truth: { call: { [cmd.id]: 'error' }, exec: { dead1: 'failed' } },
      has: ['Timed out in the background'], lacks: ['Interrupted', 'Running in the background'] };
  });
  add('a lost command result says the result was lost', () => {
    const cmd = sh('make -j8 all', { extra: { status: 'interrupted', state: 'lost', execution_status: 'lost', error: '', exit_code: null } });
    return { rows: [user('Go.'), turn([say('Building.\n\n'), T(cmd)], [cmd], ended)],
      truth: { call: { [cmd.id]: 'interrupted' }, exec: { [cmd.command_id]: 'interrupted' } },
      has: ['Interrupted · the result was lost'], lacks: ['the run ended before it finished'], open: true };
  });
  add('a user stop says stopped, and warns when the process may still run', () => {
    const confirmed = sh('npm test', { extra: { status: 'interrupted', state: 'cancelled', execution_status: 'cancelled', error: 'cancelled', exit_code: null } });
    const maybe = sh('./long-job', { extra: { status: 'interrupted', state: 'cancelled', execution_status: 'cancelled', error: 'cancelled', exit_code: null, termination_confirmed: false } });
    return { rows: [user('Go.'), turn([say('Stopping.\n\n'), T(maybe)], [maybe])], live: true, phase: 'model',
      truth: { call: { [maybe.id]: 'interrupted' }, exec: { [maybe.command_id]: 'interrupted' } },
      has: ['the process may still be running'], lacks: ['the run ended before it finished'] };
  });
  add('a host going offline names the host', () => {
    const cmd = sh('npm test', { extra: { status: 'interrupted', state: 'interrupted', execution_status: 'interrupted', error: 'agent is offline', exit_code: null } });
    return { rows: [user('Go.'), turn([say('Running.\n\n'), T(cmd)], [cmd], ended)],
      truth: { call: { [cmd.id]: 'interrupted' }, exec: { [cmd.command_id]: 'interrupted' } },
      has: ['Interrupted · qa-box went offline'], lacks: ['the host went offline'], open: true };
  });
  add('an interrupted read keeps a red dot and says why', () => {
    const read = tool('read_file', { path: '/srv/qa/app/run.py', status: 'interrupted', error: 'Tool interrupted.', call_duration_ms: undefined, duration_ms: undefined });
    return { rows: [user('Go.'), turn([say('Reading.\n\n'), T(read)], [read], ended)],
      truth: { call: { [read.id]: 'interrupted' } },
      has: ['Interrupted · the run ended before it finished'], open: true };
  });

  // —— Running rows: one clock, full width, never folded ——
  add('two running calls each stand alone with exactly one clock', () => {
    const a = tool('read_file', { path: '/srv/qa/app/a.py', status: 'running', elapsed_ms: 4000 });
    const b = tool('grep', { pattern: 'retries', path: '/srv/qa', status: 'running', elapsed_ms: 6000 });
    return { rows: [user('Go.'), turn([T(a), T(b)], [a, b])], live: true, phase: 'tools',
      truth: { call: { [a.id]: 'running', [b.id]: 'running' } },
      has: ['Reading app/a.py', 'Searching for retries'], lacks: ['read 1 file', 'searched for'] };
  });
  add('a running call under 2s shows no clock at all', () => {
    // No job id: a foreground command, so the headline is the live line and
    // the clock span renders empty until the call reaches 2s.
    const cmd = tool('run_terminal_command', { command: 'true', status: 'running', state: 'running', execution_status: 'running', exit_code: null, elapsed_ms: 400, execution_duration_ms: null, call_duration_ms: null, duration_ms: null });
    return { rows: [user('Go.'), turn([T(cmd)], [cmd])], live: true, phase: 'tools',
      truth: { call: { [cmd.id]: 'running' }, exec: { [cmd.command_id]: 'running' } },
      has: ['Running true'], clocks: { [cmd.id]: 'empty' } };
  });
  add('a finished call under 2s has no clock; one over 2s has exactly one', () => {
    const fast = tool('read_file', { path: '/srv/qa/app/a.py', output: '1: x\n', call_duration_ms: 800 });
    const slow = tool('read_file', { path: '/srv/qa/app/b.py', output: '1: y\n', call_duration_ms: 3500 });
    return { rows: [user('Go.'), turn([say('Read.\n\n'), T(fast), T(slow)], [fast, slow], ended)],
      truth: { call: { [fast.id]: 'done', [slow.id]: 'done' } },
      clocks: { [fast.id]: 0, [slow.id]: 1 }, has: ['· 3s'], open: true };
  });
  add('a background launch has no clock of its own; its running row still ticks', () => {
    const cmd = sh('npm run build', { background: true, ms: 300, job: 'tick1', extra: { status: 'done', state: 'running', execution_status: 'running', exit_code: null, elapsed_ms: 20000 } });
    return { rows: [user('Go.'), turn([say('Building.\n\n'), T(cmd)], [cmd])], live: true, phase: 'model',
      truth: { call: { [cmd.id]: 'done' }, exec: { tick1: 'running' } },
      has: ['Running in the background'], clocks: { [cmd.id]: 1 } };
  });
  add('a running command header keeps the whole command, not a 50-column clip', () => {
    const command = 'python -m pytest -q tests/integration/test_dispatcher_backoff_and_retries.py';
    const cmd = sh(command, { extra: { status: 'running', state: 'running', execution_status: 'running', exit_code: null, execution_duration_ms: null, elapsed_ms: 3000 } });
    return { rows: [user('Go.'), turn([T(cmd)], [cmd])], live: true, phase: 'tools', wide: true,
      truth: { call: { [cmd.id]: 'running' }, exec: { [cmd.command_id]: 'running' } },
      has: [command], lacks: ['test_dispatcher_backoff…'] };
  });

  // —— Wait rows ——
  add('a live Wait says "Waiting for output" and names the command', () => {
    const cmd = sh('npm run build', { background: true, job: 'w1', extra: { status: 'done', state: 'running', execution_status: 'running', exit_code: null }, description: 'Build the bundle' });
    const w = tool('get_command_output', { status: 'running', state: 'running', execution_status: 'running', background: true, child_id: 'w1', command_id: 'w1', command: cmd.command, elapsed_ms: 5000, call_duration_ms: undefined });
    return { rows: [user('Go.'), turn([say('Building.\n\n'), T(cmd), T(w)], [cmd, w])], live: true, phase: 'tools',
      truth: { call: { [cmd.id]: 'done', [w.id]: 'running' }, exec: { w1: 'running' } },
      has: ['Wait(Build the bundle)', 'Waiting for output…'], lacks: ['Still running', 'Running…'] };
  });
  add('a Wait that returned with the command done never says "Still running"', () => {
    const cmd = sh('npm test', { background: true, job: 'w2', ms: 4000, output: 'ok\n' });
    const w = tool('get_command_output', { status: 'done', state: 'completed', execution_status: 'completed', background: true, child_id: 'w2', command_id: 'w2', command: cmd.command, output: 'ok\n', call_duration_ms: 4000 });
    return { rows: [user('Go.'), turn([say('Testing.\n\n'), T(cmd), T(w)], [cmd, w], ended)],
      truth: { call: { [cmd.id]: 'done', [w.id]: 'done' }, exec: { w2: 'completed' } },
      has: ['Wait(npm test)', 'ok'], lacks: ['Still running', 'Returned while'] };
  });
  add('a Wait names the command description when the command has one', () => {
    const cmd = sh('./GameServer -config=serverGS.cfg -port=2302', { background: true, job: 'w3', extra: { status: 'done', state: 'running', execution_status: 'running', exit_code: null }, description: 'Start the game server' });
    const w = tool('get_command_output', { status: 'done', state: 'running', execution_status: 'running', background: true, child_id: 'w3', command_id: 'w3', call_duration_ms: 30006 });
    return { rows: [user('Go.'), turn([say('Starting.\n\n'), T(cmd), T(w)], [cmd, w], ended)],
      truth: { call: { [cmd.id]: 'done', [w.id]: 'done' }, exec: { w3: 'running' } },
      has: ['Wait(Start the game server)', 'Still running'], lacks: ['Wait(./GameServer'] };
  });

  // —— Monitor events ——
  add('a monitor event without a description leads with its first output line', () => {
    const mon = tool('monitor', { command: 'tail -F /var/log/app.log', background: true, child_id: 'mon2', command_id: 'mon2', execution_status: 'running', state: 'running', timeout_ms: 300000 });
    const event = env('monitor_event', 'mon2', 'seq="1"', 'host: qa-box command: tail -F /var/log/app.log', 'ERROR worker died\nERROR worker died again\n');
    return { rows: [user('Go.'), turn([say('Watching.\n\n'), T(mon)], [mon], ended), user(event, { auto: true })],
      truth: { call: { [mon.id]: 'done' }, exec: { mon2: 'running' } },
      has: ['Monitor event: ERROR worker died'], lacks: ['Monitor event: event', 'Monitor event: tail'] };
  });
  add('a monitor event never renders as a running call', () => {
    const body = env('monitor_event', 'mon3', 'seq="2"', 'host: qa-box command: tail -F x.log\ndescription: errors in worker.log', 'ERROR one\n');
    return { rows: [user(body, { auto: true })], truth: { exec: { mon3: 'running' }, monitor: { mon3: 'errors in worker.log' } },
      has: ['Monitor event: errors in worker.log'], lacks: ['Running', 'Waiting for output'] };
  });
  add('a stopped monitor notice is red and says stopped', () => {
    const mon = tool('monitor', { description: 'deploy health checks', command: 'tail -F health', background: true, child_id: 'mon4', command_id: 'mon4', execution_status: 'running', state: 'running', timeout_ms: 300000 });
    const wake = env('monitor_result', 'mon4', 'state="cancelled"', 'host: qa-box command: tail -F health\ndescription: deploy health checks\nevents: 2\nerror: cancelled');
    return { rows: [user('Go.'), turn([say('Watching.\n\n'), T(mon)], [mon], ended), user(wake, { auto: true })],
      truth: { call: { [mon.id]: 'interrupted' }, exec: { mon4: 'interrupted' }, monitor: { mon4: 'deploy health checks' } },
      has: ['Monitor "deploy health checks" stopped'], lacks: ['stream ended', 'Running'] };
  });
  add('a monitor that ended cleanly says the stream ended', () => {
    const mon = tool('monitor', { description: 'boot sequence', url: '', command: 'tail -F boot', background: true, child_id: 'mon5', command_id: 'mon5', execution_status: 'running', state: 'running', event_count: 3, timeout_ms: 300000 });
    const wake = env('monitor_result', 'mon5', 'state="completed"', 'host: qa-box command: tail -F boot\ndescription: boot sequence\nevents: 3\nduration_ms: 4000');
    return { rows: [user('Go.'), turn([say('Watching.\n\n'), T(mon)], [mon], ended), user(wake, { auto: true })],
      truth: { call: { [mon.id]: 'done' }, exec: { mon5: 'completed' }, monitor: { mon5: 'boot sequence' } },
      has: ['Monitor "boot sequence" stream ended'], lacks: ['script failed', 'stopped'] };
  });

  // —— Background commands that stay visible and say how they ended ——
  add('a running background command stays its own row after later calls', () => {
    const cmd = sh('npm run build', { background: true, job: 'bg3', extra: { status: 'done', state: 'running', execution_status: 'running', exit_code: null, elapsed_ms: 40000 }, description: 'Build the bundle' });
    const read = tool('read_file', { path: '/srv/qa/README.md', output: '1: hi\n' });
    return { rows: [user('Go.'), turn([say('Building.\n\n'), T(cmd), T(read), say('Read it.')], [cmd, read], ended)],
      truth: { call: { [cmd.id]: 'done', [read.id]: 'done' }, exec: { bg3: 'running' } },
      has: ['Bash(npm run build)', 'Running in the background', 'Read 1 file'], lacks: ['ran 1 shell command'] };
  });
  add('a background command that failed says so and stops pulsing', () => {
    const cmd = sh('npm run build', { background: true, job: 'bg4', extra: { status: 'done', state: 'running', execution_status: 'running', exit_code: null } });
    const wake = env('command_result', 'bg4', 'state="timed_out"', 'host: qa-box command: npm run build\nduration_ms: 120000\nerror: timed out\noutput_chars: 0\noutput_chars_exact: true');
    return { rows: [user('Go.'), turn([say('Building.\n\n'), T(cmd)], [cmd], ended), user(wake, { auto: true })],
      truth: { call: { [cmd.id]: 'error' }, exec: { bg4: 'failed' } },
      has: ['Background command "npm run build" failed'], lacks: ['Running in the background', 'completed'] };
  });
  add('a background command the user stopped says it was stopped', () => {
    const cmd = sh('npm run e2e', { background: true, job: 'bg5', extra: { status: 'done', state: 'running', execution_status: 'running', exit_code: null }, description: 'Run the end to end tests' });
    const wake = env('command_result', 'bg5', 'state="cancelled"', 'host: qa-box command: npm run e2e\ndescription: Run the end to end tests\nduration_ms: 9000\nerror: cancelled\noutput_chars: 5\noutput_chars_exact: true', 'partial');
    return { rows: [user('Go.'), turn([say('Testing.\n\n'), T(cmd)], [cmd], ended), user(wake, { auto: true })],
      truth: { call: { [cmd.id]: 'interrupted' }, exec: { bg5: 'interrupted' } },
      has: ['Background command "Run the end to end tests" was stopped'], lacks: ['Running in the background', 'completed'] };
  });
  add('two clean background completions fold into one notice', () => {
    const a = env('command_result', 'bg6', 'state="completed"', 'host: qa-box command: npm run lint\nexit_code: 0\nduration_ms: 2000\noutput_chars: 2\noutput_chars_exact: true', 'ok');
    const b = env('command_result', 'bg7', 'state="completed"', 'host: qa-box command: npm test\nexit_code: 0\nduration_ms: 3000\noutput_chars: 2\noutput_chars_exact: true', 'ok');
    return { rows: [user(`${a}\n${b}`, { auto: true })], truth: { exec: { bg6: 'completed', bg7: 'completed' } },
      has: ['2 background commands completed'], lacks: ['Background command "npm'] };
  });
  add('a non-zero exit does not fold into the clean-completion notice', () => {
    const a = env('command_result', 'bg8', 'state="completed"', 'host: qa-box command: npm run lint\nexit_code: 0\nduration_ms: 2000\noutput_chars: 0\noutput_chars_exact: true');
    const b = env('command_result', 'bg9', 'state="completed"', 'host: qa-box command: npm test\nexit_code: 2\nduration_ms: 3000\noutput_chars: 4\noutput_chars_exact: true', 'fail');
    return { rows: [user(`${a}\n${b}`, { auto: true })], truth: { exec: { bg8: 'completed', bg9: 'completed' } },
      has: ['Background command "npm run lint" completed', 'Background command "npm test" completed (exit code 2)'],
      lacks: ['2 background commands completed'] };
  });

  // —— Killed-command output kept ——
  add('killing a command keeps the output it had already produced', () => {
    const out = 'listening on :8080\nWARN cache miss\nready\n';
    const cmd = sh('npm start', { background: true, job: 'kill1', output: out, extra: { status: 'done', state: 'running', execution_status: 'running', exit_code: null, elapsed_ms: 15000 } });
    const kill = tool('kill_command', { child_id: 'kill1', command_id: 'kill1', output: '' });
    return { rows: [user('Go.'), turn([say('Starting.\n\n'), T(kill), T(cmd)], [cmd, kill])], live: true, phase: 'model',
      patch: { index: 1, id: cmd.id, fields: { status: 'interrupted', state: 'interrupted', execution_status: 'interrupted', error: 'cancelled', exit_code: null, output: out } },
      truth: { call: { [cmd.id]: 'interrupted', [kill.id]: 'done' }, exec: { kill1: 'interrupted' } },
      has: ['listening on :8080', 'Stop Task', 'npm start · stopped', 'Interrupted · stopped'], lacks: ['Running in the background'] };
  });
  add('a killed command with no output still names what stopped', () => {
    const cmd = sh('sleep 30', { background: true, job: 'kill2', extra: { status: 'interrupted', state: 'interrupted', execution_status: 'interrupted', error: 'cancelled', exit_code: null } });
    const kill = tool('kill_command', { child_id: 'kill2', command_id: 'kill2' });
    return { rows: [user('Go.'), turn([say('Stopping.\n\n'), T(cmd), T(kill)], [cmd, kill], ended)],
      truth: { call: { [cmd.id]: 'interrupted', [kill.id]: 'done' }, exec: { kill2: 'interrupted' } },
      has: ['Stop Task', 'sleep 30 · stopped', 'Interrupted · stopped'], open: true };
  });

  // —— search_replace locates the edit ——
  add('an edit locates its hunk by the real start line, not line 1', () => {
    const diff = '@@ -240,2 +240,2 @@\n-retries = 3\n+retries = 5\n';
    const edit = tool('search_replace', { path: '/srv/qa/app/config.py', preview: diff, output: 'updated' });
    return { rows: [user('Go.'), turn([say('Editing.\n\n'), T(edit)], [edit], ended)],
      truth: { call: { [edit.id]: 'done' } },
      has: ['Update(app/config.py)', 'Added 1 line, removed 1 line'], diffStart: 240, lacks: ['@@'] };
  });
  add('an edit with no known location shows the change and no fabricated line number', () => {
    const diff = '-retries = 3\n+retries = 5\n';
    const edit = tool('search_replace', { path: '/root/fuse-dev/app/config.py', preview: diff, output: 'updated' });
    return { rows: [user('Go.'), turn([say('Editing.\n\n'), T(edit)], [edit], ended)],
      truth: { call: { [edit.id]: 'done' } },
      has: ['Update(/root/fuse-dev/app/config.py)', 'retries = 5'], bareDiff: true };
  });
  add('a failed edit is red and keeps the error, with no diff', () => {
    const edit = tool('search_replace', { path: '/srv/qa/app/missing.py', status: 'error', error: 'old_string not found', preview: '' });
    return { rows: [user('Go.'), turn([say('Editing.\n\n'), T(edit)], [edit], ended)],
      truth: { call: { [edit.id]: 'error' } },
      has: ['old_string not found'], lacks: ['Added', 'Updated'] };
  });

  // —— Thinking rows and the switch ——
  add('a thought streaming after a call is its own ticking line', () => {
    // The ticking line exists only while the thought is the last segment and
    // still empty; a thought with text has already landed as "Thought for".
    const a = tool('read_file', { path: '/srv/qa/app/a.py', output: '1: x\n' });
    return { rows: [user('Go.'), turn([T(a), think('', 0)], [a])], live: true, phase: '',
      truth: { call: { [a.id]: 'done' } },
      has: ['Thinking for', 'Read 1 file'], lacks: ['Thought for'], thinkingLine: true };
  });
  add('a thought ending a settled turn stays its own "Thought for" line', () => {
    const a = tool('read_file', { path: '/srv/qa/app/a.py', output: '1: x\n' });
    const b = tool('read_file', { path: '/srv/qa/app/b.py', output: '1: y\n' });
    return { rows: [user('Go.'), turn([T(a), think('That is everything.', 12000), T(b)], [a, b], ended)],
      truth: { call: { [a.id]: 'done', [b.id]: 'done' } },
      has: ['Thought for 12s', 'That is everything.'], lacks: ['Thinking for'], open: true };
  });
  add('hiding thinking drops thought rows and leaves the calls', () => {
    const a = tool('grep', { pattern: 'retries', path: '/srv/qa', output: 'app/x.py:1:retries\n' });
    return { rows: [user('Go.'), turn([think('Plan.', 6000), T(a), think('Answer.', 4000), say('Done.')], [a], ended)],
      truth: { call: { [a.id]: 'done' } }, thinking: false,
      has: ['Searched for 1 pattern', 'Done.'], lacks: ['Thought for', 'Plan.', 'Answer.'] };
  });

  // —— Todo section dots ——
  add('todo marks are dots, one per state, and the spinner ignores the list', () => {
    const items = [['Read the code', 'completed'], ['Fix the rows', 'in_progress'], ['Re-run the audit', 'pending'], ['Drop the old plan', 'cancelled']];
    const todo = tool('todo_write', { todos: items.map(([content, status], i) => ({ id: String(i + 1), content, status })) });
    const cmd = sh('npm test', { extra: { status: 'running', state: 'running', execution_status: 'running', exit_code: null, execution_duration_ms: null, elapsed_ms: 3000 }, description: 'Run the tests' });
    return { rows: [user('Go.'), turn([T(todo), T(cmd)], [todo, cmd])], live: true, phase: 'tools', todos: true,
      truth: { call: { [cmd.id]: 'running' }, exec: { [cmd.command_id]: 'running' } },
      has: ['Running the tests'], lacks: ['Fix the rows', 'Update Todos'] };
  });
  add('a finished list leaves one transcript line and no todo row', () => {
    const items = [['Read the code', 'completed'], ['Fix the rows', 'completed']];
    const todo = tool('todo_write', { todos: items.map(([content, status], i) => ({ id: String(i + 1), content, status })) });
    return { rows: [user('Go.'), turn([say('Working.\n\n'), T(todo), say('Done.')], [todo], ended)],
      truth: { call: { [todo.id]: 'done' } },
      has: ['Todos · 2/2 done'], lacks: ['Update Todos', 'Read the code'] };
  });

  // —— Compaction rows ——
  add('a compaction marker names the handoff and is not a call row', () => {
    const marker = { type: 'marker', kind: 'compaction', text: 'Context compacted. The conversation was replaced with a handoff. Nothing else was kept.', summary: '## Handoff\n\nRetries are now 5.' };
    const read = tool('read_file', { path: '/srv/qa/app/config.py', output: '1: retries = 5\n' });
    return { rows: [user('Go on.'), turn([marker, say('Continuing.'), T(read)], [read])], live: true, phase: 'model',
      truth: { call: { [read.id]: 'done' } },
      has: ['COMPACTION', 'Handoff sent to the model', 'Retries are now 5.', 'Read(app/config.py)'], lacks: ['undefined'], open: true };
  });
  add('a pruning marker stays a one-line mark', () => {
    const marker = { type: 'marker', kind: 'prune', text: 'Context pruned. Older tool outputs were shortened; recent results are unchanged.' };
    return { rows: [user('Go on.'), turn([marker, say('Continuing.')], [], ended)], truth: {},
      has: ['Context pruned', 'Older tool outputs were shortened'], lacks: ['Handoff sent'] };
  });

  // —— Automated rows labeled by envelope ids ——
  add('a command handoff whose prose names other tags labels as a command result', () => {
    const body = env('command_result', 'bg10', 'state="completed"', 'host: qa-box command: npm test\nexit_code: 0\nduration_ms: 3000\noutput_chars: 12\noutput_chars_exact: true',
      'ok\nA <monitor_event> means the watch saw a line. <monitor_result state="completed"> ends it.\n');
    return { rows: [user(body, { auto: true })], truth: { exec: { bg10: 'completed' } },
      has: ['Background command "npm test" completed'], lacks: ['background results', 'monitor event'] };
  });
  add('a report envelope renders as an agent notice, not a command row or a raw chip', () => {
    const body = '<task id="kid-1" state="completed">\nThe panes check out.\n</task>';
    return { rows: [user(body, { auto: true })], truth: {},
      has: ['Agent "subagent" finished', 'The panes check out.'], lacks: ['Automated · background subagent report', 'background command', 'Monitor event'] };
  });
  add('a scheduled run is not mistaken for a background result', () => {
    return { rows: [user('[Fuse] SCHEDULED RUN\nCheck the backups.', { auto: true })], truth: {},
      has: ['Automated · scheduled run'], lacks: ['background command', 'monitor event'] };
  });
  add('command and report envelopes together render one row each', () => {
    const body = env('command_result', 'bg11', 'state="completed"', 'host: qa-box command: npm test\nexit_code: 0\nduration_ms: 1000\noutput_chars: 2\noutput_chars_exact: true', 'ok') +
      '\n<task id="kid-2" state="completed">\nReport.\n</task>';
    return { rows: [user(body, { auto: true })], truth: { exec: { bg11: 'completed' } },
      has: ['Background command "npm test" completed', 'Agent "subagent" finished'], lacks: ['Automated · background results'] };
  });

  // —— A subagent's own background-command handoffs, in its pane ——
  add('a subagent pane folds its own command handoff into a step row', () => {
    const handoff = env('command_result', 'childjob', 'state="completed"', 'host: qa-box command: npm test\nexit_code: 0\nduration_ms: 4000\noutput_chars: 11\noutput_chars_exact: true', 'tests passed');
    const boilerplate = 'A <monitor_event> means the watch saw a line and a <monitor_result state="completed"> ends it.';
    return { rows: [user('Go.'), turn([say('Delegating.')], [], ended)], agent: {
      id: 'kid-rows', status: 'completed', description: 'Run the suite', event_cursor: 4, message_id: 'kid-msg',
      message: { id: 'kid-msg', role: 'assistant', content: 'Done.', tools: [], segments: [{ type: 'content', text: 'Done.' }] },
      messages: [
        { role: 'assistant', content: 'Running it.', tools: [sh('npm test', { job: 'childjob', extra: { status: 'done', state: 'running', execution_status: 'running', exit_code: null } })] },
        { role: 'user', auto: true, content: `${handoff}\n${boilerplate}` },
      ],
    }, truth: { exec: { childjob: 'completed' } },
      has: [], agentHas: ['Background command "npm test" completed', 'tests passed'], agentLacks: ['Automated · background results', 'A <monitor_event> means'] };
  });

  // —— Malformed and truncated text ——
  add('a truncated read drops the cut-off fragment and keeps real line numbers', () => {
    const read = tool('read_file', { path: '/srv/qa/app/run.py', output: '40: alpha\n41: beta\n42', output_chars: 40, output_mode: 'partial', truncated: true });
    return { rows: [user('Go.'), turn([say('Reading.\n\n'), T(read)], [read])], live: true, phase: 'model',
      truth: { call: { [read.id]: 'done' } },
      has: ['Read(app/run.py)', 'Read 3 lines', 'Partial output'], lacks: ['42'] };
  });
  add('ansi noise in command output does not leak raw escapes into the row', () => {
    const cmd = sh('npm test', { output: '\x1b[31mFAIL\x1b[0m suite\nok\n', extra: { exit_code: 1, status: 'error', error: 'Command exited with code 1' } });
    return { rows: [user('Go.'), turn([say('Testing.\n\n'), T(cmd)], [cmd])], live: true, phase: 'model',
      truth: { call: { [cmd.id]: 'error' }, exec: { [cmd.command_id]: 'failed' } },
      has: ['FAIL suite', 'Exit code 1'], lacks: ['[31m', '[0m'] };
  });
  add('a tool error that is not an Error line still reads as one', () => {
    const read = tool('read_file', { path: '/srv/qa/missing.py', status: 'error', error: 'No such file or directory' });
    const after = tool('list_dir', { path: '/srv/qa', output: 'app\n' });
    return { rows: [user('Go.'), turn([say('Reading.\n\n'), T(read), T(after)], [read, after], ended)],
      truth: { call: { [read.id]: 'error', [after.id]: 'done' } },
      has: ['Error: No such file or directory'], open: true };
  });

  return list;
}

// Rules judged against the latest state, scoped to what this file seeds.
function audit(truth, opts) {
  const exec = truth.exec || {};
  const call = truth.call || {};
  const monitors = truth.monitor || {};
  const out = [];
  const add = (rule, li, detail = '') => out.push({ rule, detail, row: li ? li.innerText.replace(/\s+/g, ' ').trim().slice(0, 160) : '' });
  const roots = [document.querySelector('.chat-log'), document.querySelector('[data-agent-view]')].filter(Boolean);
  const white = color('var(--mist)');
  const red = color('var(--call-fail)');
  const seen = new Set();
  for (const root of roots) {
    for (const li of root.querySelectorAll('li.chat-step')) {
      if (!shown(li) || li.closest('.chat-console-bundle')) continue;
      const text = li.innerText.replace(/\s+/g, ' ').trim();
      const head = headOf(li);
      const execId = li.dataset.execution || '';
      const callId = (li.dataset.step || '').replace(/^tool-/, '');
      const execState = exec[execId];
      const callState = call[callId];
      const isCall = li.classList.contains('is-console-call');
      if (li.dataset.step && !li.closest('.chat-auto-steps')) {
        const key = `${root.className}|${li.dataset.step}`;
        if (seen.has(key)) add('duplicate-row', li, li.dataset.step);
        seen.add(key);
      }
      if (!head && !text) add('empty-row', li);
      if (/\bStill running\b/.test(text) && execState && execState !== 'running') add('stale-still-running', li, `${execId} is ${execState}`);
      if (!opts.allowStale && /Running in the background/.test(text) && execState && execState !== 'running') add('stale-background', li, `${execId} is ${execState}`);
      if (/Waiting for output…/.test(text) && callState && callState !== 'running') add('stale-waiting', li, `wait is ${callState}`);
      if (!opts.allowStale && isCall && li.classList.contains('is-running') && callState && callState !== 'running' && execState !== 'running') add('stale-running-dot', li, `call ${callState}, command ${execState || 'none'}`);
      if (isCall && callState === 'running' && opts.live && !li.classList.contains('is-running')) add('running-not-shown', li, callId);
      if (/^Monitor event:/.test(head) && monitors[execId] && !head.startsWith(`Monitor event: ${monitors[execId]}`)) add('monitor-event-header', li, monitors[execId]);
      if (/\bundefined\b|\bNaN\b|\[object Object\]/.test(head) || /^\w[\w ]*\((command|event|undefined|null)\)/.test(head)) add('placeholder-header', li, head);
      if (isCall) {
        const clocks = li.querySelectorAll('[data-cc-clock], .chat-console-took');
        if (clocks.length > 1) add('double-clock', li, `${clocks.length} clocks`);
        if (opts.clocks && Object.prototype.hasOwnProperty.call(opts.clocks, callId)) {
          const want = opts.clocks[callId];
          const got = want === 'empty' ? (clocks.length === 1 && !clocks[0].textContent.trim() ? 'empty' : clocks.length) : clocks.length;
          if (got !== want) add('clock-count', li, `expected ${want}, saw ${got}`);
        }
      }
      if (li.classList.contains('is-console-group') && li.querySelector('.chat-console-bundle li.is-running')) add('running-call-folded', li);
      if ((isCall || li.classList.contains('is-console-notice')) && !li.classList.contains('is-running')) {
        const node = li.querySelector(':scope > .chat-step-node');
        const after = node && getComputedStyle(node, '::after');
        if (after && after.content !== 'none' && getComputedStyle(node).display !== 'none') {
          const bad = li.matches('.is-error, .is-interrupted, .is-red, .is-gold');
          const want = bad ? red : white;
          if (after.backgroundColor !== want) add('dot-colour', li, `${after.backgroundColor} for ${bad ? 'a failure' : 'a finished row'}`);
        }
      }
    }
  }
  if (opts.thinkingLine && ![...document.querySelectorAll('.chat-log li.chat-step')].some((li) => /^Thinking for /.test(headOf(li)) && shown(li))) add('no-live-thought-line', null);
  if (opts.thinking === false) {
    if (document.querySelector('.chat-log .is-think, .chat-log .chat-console-thought')) add('thinking-shown', null);
    for (const li of document.querySelectorAll('.chat-log li')) if (/^(Thought|Thinking) for /.test(headOf(li))) add('thinking-shown', li);
  }
  if (opts.live) {
    const dock = document.querySelector('[data-live-dock]');
    if (!dock || dock.hidden) add('dock-missing', null);
    if (opts.status && !(document.querySelector('[data-live-dock-label]')?.textContent || '').toLowerCase().includes(opts.status)) {
      add('status-label', null, document.querySelector('[data-live-dock-label]')?.textContent || '');
    }
  }
  if (opts.todos) {
    const sec = document.querySelector('[data-live-todos]');
    const rows = sec ? [...sec.querySelectorAll('.chat-todo-row')] : [];
    const states = ['is-completed', 'is-in-progress', 'is-pending', 'is-cancelled'];
    if (!sec || sec.hidden || rows.length !== 4) add('todo-rows', null, `${rows.length} rows`);
    states.forEach((state, i) => { if (rows[i] && !rows[i].classList.contains(state)) add('todo-state', null, `row ${i} is ${rows[i].className}`); });
    // Console draws the marks (empty glyph, round, one colour per state). A
    // typed glyph such as a check or a box is the bug the dots replaced.
    const marks = rows.map((row) => { const box = row.querySelector('.chat-todo-box'); const g = box && getComputedStyle(box, '::before'); return g ? `${g.content} ${g.borderRadius} ${g.backgroundColor}` : 'missing'; });
    const colours = new Set(marks.map((mark) => mark.split(' ').pop()));
    if (marks.some((mark) => !/^"" 50% /.test(mark)) || colours.size < 3) add('todo-dots', null, marks.join(' | '));
    const status = document.querySelector('[data-live-dock-label]')?.textContent || '';
    if (/Fix the rows|updating todos/i.test(status)) add('todo-in-spinner', null, status);
  }
  if (opts.diffStart) {
    const n = document.querySelector('.chat-log .chat-console-lines.is-diff .chat-console-n');
    if (!n || Number(n.textContent) !== opts.diffStart) add('diff-location', null, n ? n.textContent : 'no gutter');
  }
  if (opts.bareDiff) {
    const line = document.querySelector('.chat-log .chat-console-lines.is-diff .chat-console-nline');
    if (!line || !line.classList.contains('is-bare') || line.querySelector('.chat-console-n')) add('diff-bare', null, line ? line.className : 'no diff');
  }
  if (opts.numberedFrom) {
    const n = document.querySelector('.chat-log .chat-console-lines:not(.is-diff) .chat-console-n');
    if (!n || Number(n.textContent) !== opts.numberedFrom) add('read-line-numbers', null, n ? n.textContent : 'no gutter');
  }
  if (document.documentElement.scrollWidth > innerWidth + 1) add('page-overflow', null, `${document.documentElement.scrollWidth} > ${innerWidth}`);
  return out;

  function shown(el) { return Boolean(el && el.getClientRects().length && getComputedStyle(el).visibility !== 'hidden'); }
  function headOf(li) {
    const head = li.querySelector(':scope > .chat-step-main > .chat-step-head .chat-step-label, :scope > .chat-step-main .chat-console-notice-text');
    return (head ? head.innerText : '').replace(/\s+/g, ' ').trim();
  }
  function color(css) {
    const probe = document.createElement('span');
    probe.style.background = css;
    document.querySelector('.chat-log').append(probe);
    const value = getComputedStyle(probe).backgroundColor;
    probe.remove();
    return value;
  }
}

(async () => {
  const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'], ...(process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {}) });
  const started = Date.now();
  try {
    const page = await browser.newPage({ reducedMotion: 'reduce', viewport: { width: 1280, height: 900 } });
    const errors = [];
    page.on('pageerror', (error) => errors.push(error.message));
    await page.route('**/*', (route) => new URL(route.request().url()).origin === origin ? route.continue() : route.abort());
    await page.goto(origin);
    await page.waitForFunction(() => window.fuseToolFixture);

    const failures = [];
    for (const sc of scenarios()) {
      const result = await page.evaluate(async ({ sc, auditSrc }) => {
        window.RowsAudit = new Function('truth', 'opts', `${auditSrc}\nreturn audit(truth, opts);`);
        fuseToolFixture.thinking(sc.thinking !== false);
        fuseToolFixture.todo.reset();
        fuseToolFixture.mountThread(sc.rows, { live: Boolean(sc.live), phase: sc.phase || '' });
        if (sc.agent) {
          await pageRoute(sc.agent);
          await fuseToolFixture.todo.openAgent(sc.agent.id);
        }
        // The fixture's update() walks every message, and a user row's article
        // has no citations node, so patch the trace and repaint instead.
        if (sc.patch) {
          const rows = fuseToolFixture.snapshot();
          const trace = (rows[sc.patch.index].tools || []).find((t) => t.id === sc.patch.id);
          Object.assign(trace, sc.patch.fields);
          fuseToolFixture.mountThread(rows, { live: Boolean(sc.live), phase: sc.phase || '' });
        }
        await new Promise((r) => setTimeout(r, 40));
        const flat = (sel) => (document.querySelector(sel)?.innerText || '').replace(/[ \t]+/g, ' ').replace(/\s*\n\s*/g, ' ');
        if (sc.open) document.querySelectorAll('.chat-log [data-step-toggle], .chat-log .chat-compact-banner').forEach((el) => { if (el.tagName === 'DETAILS') el.open = true; else if (el.getAttribute('aria-expanded') === 'false') el.click(); });
        const text = flat('.chat-log');
        const agent = flat('[data-agent-view]');
        const out = { missing: [], present: [], agentMissing: [], agentPresent: [], audit: window.RowsAudit(sc.truth, sc) };
        for (const want of sc.has || []) if (!text.includes(want)) out.missing.push(want);
        for (const not of sc.lacks || []) if (text.includes(not)) out.present.push(not);
        if (sc.agentOpen) document.querySelectorAll('[data-agent-view] [data-step-toggle]').forEach((el) => { if (el.getAttribute('aria-expanded') === 'false') el.click(); });
        const agentText = sc.agentOpen ? flat('[data-agent-view]') : agent;
        for (const want of sc.agentHas || []) if (!agentText.includes(want)) out.agentMissing.push(want);
        for (const not of sc.agentLacks || []) if (!(sc.agentOpen ? flat('[data-agent-view]') : agent).includes(not)) {} else out.agentPresent.push(not);
        if (sc.patch) out.audit.push(...window.RowsAudit(sc.truth, sc));
        document.querySelector('[data-agents-close]')?.click();
        return out;
        function pageRoute(run) {
          const orig = window.fetch;
          window.fetch = (url, init) => String(url).includes(`/chat/runs/${run.id}`)
            ? Promise.resolve(new Response(JSON.stringify(run), { status: 200, headers: { 'content-type': 'application/json' } }))
            : orig(url, init);
        }
      }, { sc, auditSrc: audit.toString().replace(/^function audit\(truth, opts\) \{/, '').replace(/\n\}$/, '') });
      const where = sc.name;
      for (const m of result.missing) failures.push(`${where}: missing "${m}"`);
      for (const p of result.present) failures.push(`${where}: should not show "${p}"`);
      for (const m of result.agentMissing) failures.push(`${where}: agent pane missing "${m}"`);
      for (const p of result.agentPresent) failures.push(`${where}: agent pane should not show "${p}"`);
      for (const v of result.audit) failures.push(`${where}: ${v.rule} ${v.detail} | ${v.row}`);
    }
    if (errors.length) failures.push(...errors.map((e) => `page error: ${e}`));
    const unique = [...new Set(failures)];
    if (unique.length) {
      console.error(unique.slice(0, 80).join('\n'));
      if (unique.length > 80) console.error(`… and ${unique.length - 80} more`);
    }
    assert.equal(unique.length, 0, `${unique.length} Console row-state problems`);
    console.log(`PASS: Console row-state audit — ${scenarios().length} scenarios in ${((Date.now() - started) / 1000).toFixed(1)}s`);
  } finally {
    await browser.close();
  }
})().catch((error) => { console.error(error); process.exit(1); });
