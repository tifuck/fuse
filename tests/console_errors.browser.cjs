// Failed and interrupted calls in the Console transcript, seeded with the
// trace shapes production actually stores (tool_failures + chat_threads,
// October 2026). Most call-level failures keep the model's result text as
// `output`, and that text is the same sentence as `error`.
//
// Rules:
// - A collapsed summary line ("Ran 3 shell commands") prints no error or
//   interruption under it; the member rows say so once the line is opened.
// - An opened failing row prints its error once, not the error and then the
//   same sentence again as output.
// - An interrupted command's partial output reads before the interruption.
const assert = require('node:assert/strict');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const origin = process.env.FUSE_BROWSER_ORIGIN || 'http://127.0.0.1:8099';
assert.match(origin, /^http:\/\/127\.0\.0\.1:\d+$/);

const ROOT = { host: 'qa-box', cwd: '/srv/qa', platform: 'posix', project_root: '/srv/qa' };
let seq = 0;
const tool = (name, fields = {}) => ({
  id: `e${++seq}`, name, status: 'done', error: '', results: [], output: '',
  duration_ms: 120, call_duration_ms: 120, ...ROOT, ...fields,
});
const sh = (command, fields = {}) => {
  const job = `ej${++seq}`;
  return tool('run_terminal_command', {
    command, child_id: job, command_id: job, job_id: job, timeout_ms: 120000, termination_confirmed: true,
    status: 'done', state: 'completed', execution_status: 'completed', exit_code: 0,
    elapsed_ms: 900, execution_duration_ms: 900, output: fields.output || '', preview: (fields.output || '').slice(0, 120),
    ...fields.extra,
  });
};
// A call that failed before any command ran: no job, output is the model text.
const refused = (name, error, fields = {}) => tool(name, { status: 'error', kind: 'tool_error', error, output: error, ...fields });
const T = (trace) => ({ type: 'tool', id: trace.id });
const say = (text) => ({ type: 'content', text });
const ended = { endedAt: '2026-10-03T20:00:00Z' };
const turn = (segments, tools) => ({
  role: 'assistant', model: 'qa/model', elapsedMs: 30000, tools, segments, ...ended,
  content: segments.filter((s) => s.type === 'content').map((s) => s.text).join(''), reasoning: '',
});
const user = (content) => ({ role: 'user', content, reasoning: '', error: '' });
const thread = (...traces) => [user('Go.'), turn([say('Working.\n\n'), ...traces.map(T), say('\n\nDone.')], traces)];

const DENIED = 'Permission denied by Jev (Auto mode automation), not by the user. Do not access /home/x/project; continue without it.';

function scenarios() {
  const list = [];
  const add = (name, build) => list.push({ name, ...build() });

  add('a refused command in a summary line: no note collapsed, one error opened', () => {
    const ok = sh('ls -la', { output: 'total 8\napp\n' });
    const bad = refused('run_terminal_command', DENIED, { command: 'cat /home/x/project/README.md' });
    const ok2 = sh('make lint', { output: 'ok\n' });
    return { rows: thread(ok, bad, ok2), once: { [bad.id]: ['Permission denied by Jev'] } };
  });
  add('an offline host in a summary line: no Interrupted note collapsed', () => {
    const ok = sh('make build', { output: 'built\n' });
    const gone = sh('npm test', { extra: { status: 'interrupted', state: 'interrupted', execution_status: 'interrupted', error: 'agent is offline', exit_code: null, termination_confirmed: false } });
    return { rows: thread(ok, gone), once: { [gone.id]: ['went offline'] } };
  });
  add('an invalid command prints its reason once', () => {
    const bad = refused('run_terminal_command', 'invalid command', { command: '' });
    const ok = sh('pwd', { output: '/srv/qa\n' });
    return { rows: thread(bad, ok), once: { [bad.id]: ['invalid command'] } };
  });
  add('a read outside the project and a grep timeout, each once', () => {
    const read = refused('read_file', 'path is outside the project root', { path: '/etc/shadow' });
    const grep = refused('grep', 'agent timed out', { pattern: 'TODO', path: '/srv/qa' });
    const ok = tool('read_file', { path: '/srv/qa/app/main.py', output: '1: import os\n' });
    return { rows: thread(read, grep, ok), once: { [read.id]: ['outside the project root'], [grep.id]: ['agent timed out'] } };
  });
  add('a failed edit with an exit suffix prints once', () => {
    const edit = refused('search_replace', 'old_string not found (exit 1)', { path: '/srv/qa/app/main.py', exit_code: '1' });
    return { rows: thread(edit), once: { [edit.id]: ['old_string not found'] } };
  });
  add('a short error the model saw a longer sentence for prints the longer one, once', () => {
    const kill = tool('kill_command', { status: 'error', error: 'not found', output: 'job not found (command jobs only; if this is a subagent id, use get_subagent_output)', child_id: 'zz1' });
    const wait = tool('get_command_output', { status: 'error', error: 'not found', output: 'job not found (command jobs only; if this is a subagent id, use get_subagent_output)', preview: 'still running', child_id: 'zz2' });
    return { rows: thread(kill, wait), once: { [kill.id]: ['not found'], [wait.id]: ['not found'] } };
  });
  add('a timed-out background command says so once and keeps its output', () => {
    const slow = sh('rg -n "Primary Request" big.log', { output: '162:Your task\n566:Primary Request\n', extra: { status: 'error', state: 'timed_out', execution_status: 'timed_out', error: 'agent timed out', exit_code: null, background: true, termination_confirmed: false } });
    return { rows: thread(slow), once: { [slow.id]: ['Timed out', '566:Primary Request'] } };
  });
  add('an interrupted command prints its partial output, then why it stopped', () => {
    const ok = sh('ls', { output: 'app\n' });
    const cut = sh('./build.sh', { output: 'step 1\nstep 2\n', extra: { status: 'interrupted', state: 'cancelled', execution_status: 'cancelled', error: 'cancellation requested; remote termination is not confirmed', exit_code: null, termination_confirmed: false } });
    return { rows: thread(ok, cut), once: { [cut.id]: ['step 2', 'Interrupted'] }, order: { [cut.id]: ['step 2', 'Interrupted'] } };
  });
  add('a failed write on its own row prints its error once', () => {
    const write = refused('write_file', '/srv/qa/tests/test_a.py already exists and has not been read in this session. read_file it first.', { path: '/srv/qa/tests/test_a.py' });
    return { rows: thread(write), once: { [write.id]: ['already exists and has not been read'] }, standalone: [write.id] };
  });
  return list;
}

function inspect(sc) {
  const out = [];
  const flat = (el) => (el ? el.innerText : '').replace(/\s+/g, ' ').trim();
  const shown = (el) => Boolean(el && el.getClientRects().length && getComputedStyle(el).visibility !== 'hidden');
  const log = document.querySelector('.chat-log');
  // Collapsed: a summary line is its label, nothing under it.
  for (const li of log.querySelectorAll('li.is-console-group:not(.is-console-agents)')) {
    if (li.classList.contains('is-console-expanded')) continue;
    for (const hang of li.querySelectorAll(':scope > .chat-step-main > .chat-console-hang')) {
      if (shown(hang)) out.push(`collapsed line has a note: "${flat(li.querySelector('.chat-step-head'))}" └ ${flat(hang)}`);
    }
    if (/Error:|Interrupted|offline/.test(flat(li.querySelector(':scope > .chat-step-main > .chat-step-head')))) out.push(`collapsed label names a failure: ${flat(li)}`);
  }
  for (const id of sc.standalone || []) {
    const row = log.querySelector(`li.is-console-call[data-step="tool-${id}"]`);
    if (!row || row.closest('.chat-console-bundle')) out.push(`${id} should print on its own row`);
  }
  // Open every line and row.
  log.querySelectorAll('[data-step-toggle]').forEach((el) => { if (el.getAttribute('aria-expanded') === 'false') el.click(); });
  for (const [id, needles] of Object.entries(sc.once || {})) {
    const row = log.querySelector(`li.is-console-call[data-step="tool-${id}"]`);
    if (!row) { out.push(`${id}: no row`); continue; }
    const text = flat(row).toLowerCase();
    for (const needle of needles) {
      const n = text.split(needle.toLowerCase()).length - 1;
      if (n !== 1) out.push(`${id}: "${needle}" shown ${n} times | ${flat(row).slice(0, 240)}`);
    }
    const order = (sc.order || {})[id];
    if (order) {
      const at = order.map((needle) => text.indexOf(needle.toLowerCase()));
      if (at.some((i, k) => k && i < at[k - 1])) out.push(`${id}: expected ${order.join(' before ')} | ${flat(row).slice(0, 240)}`);
    }
  }
  return out;
}

(async () => {
  const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'], ...(process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {}) });
  try {
    const page = await browser.newPage({ reducedMotion: 'reduce', viewport: { width: 1280, height: 900 } });
    const errors = [];
    page.on('pageerror', (error) => errors.push(error.message));
    await page.route('**/*', (route) => new URL(route.request().url()).origin === origin ? route.continue() : route.abort());
    await page.goto(origin);
    await page.waitForFunction(() => window.fuseToolFixture);
    const failures = [];
    for (const sc of scenarios()) {
      const found = await page.evaluate(async ({ sc, src }) => {
        fuseToolFixture.thinking(true);
        fuseToolFixture.mountThread(sc.rows, { live: false, phase: '' });
        await new Promise((r) => setTimeout(r, 40));
        return new Function('sc', `${src}\nreturn inspect(sc);`)(sc);
      }, { sc, src: inspect.toString() });
      failures.push(...found.map((f) => `${sc.name}: ${f}`));
    }
    failures.push(...errors.map((e) => `page error: ${e}`));
    if (failures.length) console.error(failures.join('\n'));
    assert.equal(failures.length, 0, `${failures.length} Console error-row problems`);
    console.log(`PASS: Console error rows — ${scenarios().length} scenarios`);
  } finally {
    await browser.close();
  }
})().catch((error) => { console.error(error); process.exit(1); });
