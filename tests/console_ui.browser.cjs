// Console UI suite: the rows Claude Code's transcript defines, checked on
// the real renderers in the isolated fixture page (no provider/host traffic).
//
// 1. Regression scenarios, one per bug fixed in the Console port: stale
//    "Still running" Waits, running calls folded into summary lines, doubled
//    clocks, placeholder headers, monitor events, dot colours, thinking.
// 2. Every scenario and every generated step also passes ConsoleAudit.check
//    (tests/console_ui_lib.js): rules judged against the latest state of each
//    call and command, never a row's own saved copy.
// 3. A seeded generator plays out command/Wait/monitor/kill/edit histories
//    step by step, live and settled, inside the page (one round trip per
//    seed). CONSOLE_UI_SEEDS sets how many (default 60); a failure prints
//    its seed and step so `CONSOLE_UI_ONLY=<seed>` replays it.
const assert = require('node:assert/strict');
const path = require('node:path');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const origin = process.env.FUSE_BROWSER_ORIGIN || 'http://127.0.0.1:8099';
assert.match(origin, /^http:\/\/127\.0\.0\.1:\d+$/);
const SEEDS = Number(process.env.CONSOLE_UI_SEEDS || 60);
const ONLY = process.env.CONSOLE_UI_ONLY ? Number(process.env.CONSOLE_UI_ONLY) : null;

const ROOT = { host: 'qa-box', cwd: '/srv/qa', platform: 'posix', project_root: '/srv/qa' };
let seq = 0;
const tool = (name, fields = {}) => ({ id: `c${++seq}`, name, status: 'done', error: '', results: [], output: '', duration_ms: 120, call_duration_ms: 120, ...ROOT, ...fields });
const sh = (command, fields = {}) => {
  const job = fields.job || `j${++seq}`;
  const running = fields.running;
  return tool('run_terminal_command', {
    command, child_id: job, command_id: job, job_id: job, timeout_ms: 120000, termination_confirmed: true,
    status: running ? 'running' : 'done', state: running ? 'running' : 'completed', execution_status: running ? 'running' : 'completed',
    exit_code: running ? null : 0, elapsed_ms: fields.ms ?? 900, execution_duration_ms: running ? null : fields.ms ?? 900,
    output: fields.output || '', preview: (fields.output || '').slice(0, 120), ...fields.extra,
    ...(fields.description ? { description: fields.description } : {}), ...(fields.background ? { background: true } : {}),
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
const TESTOUT = 'tests/test_a.py ....\ntests/test_b.py ......\ntests/test_c.py ..\n';

function scenarios() {
  const list = [];
  const add = (name, build) => list.push({ name, ...build() });

  add('one clock on a running command, none under it', () => {
    const read = tool('read_file', { path: '/srv/qa/app/run.py', output: '1: x\n' });
    const cmd = sh('python -m pytest -q tests/', { running: true, ms: 12000, output: TESTOUT, description: 'Run the test suite' });
    return { rows: [user('Go.'), turn([think('Look.', 4000), T(read), T(cmd)], [read, cmd])], live: true, phase: 'tools',
      truth: { call: { [read.id]: 'done', [cmd.id]: 'running' }, exec: { [cmd.command_id]: 'running' } },
      has: ['Thought for 4s, read 1 file', 'Running the test suite · 1', '(timeout: 2m)', '$ python -m pytest -q tests/ (3 lines)'],
      lacks: ['Running the test suite · 12s…', '· timeout'] };
  });
  add('a running search stays its own row; the line above stays settled', () => {
    const a = tool('grep', { pattern: 'retries', path: '/srv/qa/app', output: 'app/x.py:1:retries\n' });
    const b = tool('list_dir', { path: '/srv/qa/tests', output: 'a.py\nb.py\n' });
    const c = tool('grep', { pattern: 'timeout_for', path: '/srv/qa', status: 'running', elapsed_ms: 3500 });
    return { rows: [user('Go.'), turn([think('Find it.', 3000), T(a), T(b), T(c)], [a, b, c])], live: true, phase: 'tools',
      truth: { call: { [a.id]: 'done', [b.id]: 'done', [c.id]: 'running' } },
      has: ['Thought for 3s, searched for 1 pattern, listed 1 directory', 'Searching for timeout_for'], lacks: ['Searched for 2 patterns'] };
  });
  add('a thought streaming after calls gets its own live line', () => {
    const a = tool('read_file', { path: '/srv/qa/app/config.py', output: '1: retries = 3\n' });
    return { rows: [user('Go.'), turn([think('Plan.', 5000), T(a), think('Considering', 0)], [a])], live: true, phase: 'model',
      truth: { call: { [a.id]: 'done' } }, has: ['Thought for 5s, read 1 file', 'Thinking for'], lacks: ['Thinking for 5s, reading'] };
  });
  add('a landed thought reads "Thought for", not a frozen "Thinking for"', () => {
    const a = tool('read_file', { path: '/srv/qa/app/config.py', output: '1: x\n' });
    return { rows: [user('Go.'), turn([think('Plan.', 5000), T(a), think('Next, check the tests.', 6000)], [a])], live: true, phase: 'calling',
      truth: { call: { [a.id]: 'done' } }, has: ['Thought for 6s'], lacks: ['Thinking for 6s'] };
  });
  add('a thought before the answer stays its own line', () => {
    const a = tool('grep', { pattern: 'retries', path: '/srv/qa', output: 'app/x.py:1:retries\n' });
    return { rows: [user('Go.'), turn([think('Plan.', 8000), T(a), think('Answer now.', 17000), say('Here.')], [a], ended)],
      truth: { call: { [a.id]: 'done' } }, has: ['Thought for 8s, searched for 1 pattern', 'Thought for 17s'] };
  });
  add('a Wait that timed out says "Returned…" once its command ended', () => {
    const cmd = sh('cd /root/fuse && ./scripts/promote-dev.sh', { background: true, ms: 15000, job: 'stale1',
      extra: { status: 'interrupted', state: 'interrupted', execution_status: 'interrupted', error: 'agent is offline', exit_code: null } });
    const w1 = tool('get_command_output', { status: 'done', state: 'running', execution_status: 'running', background: true, child_id: 'stale1', command_id: 'stale1', call_duration_ms: 30006 });
    const w2 = { id: `c${++seq}`, name: 'get_command_output', child_id: 'stale1', status: 'interrupted', preview: 'still running', results: [], error: '' };
    return { rows: [user('Go.'), turn([say('Promoting.\n\n'), T(cmd), T(w1), T(w2), say('Done.')], [cmd, w1, w2], ended)],
      truth: { call: { [cmd.id]: 'interrupted', [w1.id]: 'done', [w2.id]: 'interrupted' }, exec: { stale1: 'interrupted' } },
      has: ['Wait(cd /root/fuse && ./scripts/promote-dev.sh) · 30s', 'Returned while the command was still running', 'Interrupted · the run ended before it finished'],
      lacks: ['Still running', 'Wait(command)'] };
  });
  add('a Wait says "Still running" while its command runs', () => {
    const cmd = sh('npm run build', { background: true, running: true, ms: 60000, job: 'live1', extra: { status: 'done' } });
    const w = tool('get_command_output', { status: 'done', state: 'running', execution_status: 'running', background: true, child_id: 'live1', command_id: 'live1', call_duration_ms: 30006, output: 'building...\n' });
    return { rows: [user('Go.'), turn([say('Building.\n\n'), T(cmd), T(w), say('Still going.')], [cmd, w], ended)],
      truth: { call: { [cmd.id]: 'done', [w.id]: 'done' }, exec: { live1: 'running' } },
      has: ['Still running', 'Running in the background', 'Wait(npm run build) · 30s'] };
  });
  add('a Wait without a command names its job', () => {
    const w = { id: `c${++seq}`, name: 'get_command_output', child_id: 'deadbeef0001', status: 'interrupted', preview: 'still running', results: [], error: '' };
    return { rows: [user('Go.'), turn([say('Waiting.\n\n'), T(w)], [w], ended)], truth: { call: { [w.id]: 'interrupted' } },
      has: ['Wait(job deadbeef0001)'], lacks: ['Wait(command)'] };
  });
  add('a running background command is never folded', () => {
    const cmd = sh('./GameServer -config=serverGS.cfg', { background: true, running: true, ms: 95000, job: 'bg1', output: 'Loading...\n', extra: { status: 'done' }, description: 'Start the game server' });
    const a = tool('read_file', { path: '/srv/qa/serverGS.cfg', output: '1: hostname\n' });
    const b = tool('grep', { pattern: 'maxPlayers', path: '/srv/qa', output: 'serverGS.cfg:3:maxPlayers = 60\n' });
    return { rows: [user('Go.'), turn([think('Start.', 2000), T(cmd), T(a), think('Check.', 3000), T(b), say('Starting.')], [cmd, a, b])], live: true, phase: 'model',
      truth: { call: { [cmd.id]: 'done', [a.id]: 'done', [b.id]: 'done' }, exec: { bg1: 'running' } },
      has: ['Bash(./GameServer -config=serverGS.cfg) · 1m 3', 'Running in the background', '(timeout: 2m)'], lacks: ['ran 1 shell command'] };
  });
  add('a monitor event leads with its description; "+N lines" opens the rest', () => {
    const mon = tool('monitor', { description: 'Game startup milestones in new log', command: 'tail -F x.log', background: true, child_id: 'mon1', command_id: 'mon1', execution_status: 'running', state: 'running', timeout_ms: 300000 });
    const event = '<monitor_event id="mon1" seq="1">\nhost: qa-box command: tail -F x.log\ndescription: Game startup milestones in new log\n\n' +
      '19:54:15 SUCCESS: SteamGameServer_Init(0,8766)\n19:54:16 SUCCESS: Mission read\n19:54:17 SUCCESS: Server ready\n19:54:18 SUCCESS: Steam OK\n19:54:19 SUCCESS: Players 0/60\n</monitor_event>';
    return { rows: [user('Go.'), turn([say('Watching.\n\n'), T(mon)], [mon], ended), user(event, { auto: true })],
      truth: { call: { [mon.id]: 'done' }, exec: { mon1: 'running' }, monitor: { mon1: 'Game startup milestones in new log' } },
      has: ['Monitor event: Game startup milestones in new log', '19:54:15 SUCCESS', '… +2 lines', 'Monitor started · task mon1', '(timeout: 5m)'],
      lacks: ['19:54:19'], expand: { click: '.is-event [data-step-toggle].is-event-more', has: ['19:54:19 SUCCESS: Players 0/60'] } };
  });
  // A settled command folds into its summary line; as the newest call of a
  // live turn it prints on its own row, which is where its dot shows.
  for (const [label, cmd, truthCall, truthExec, has] of [
    ['grep miss is finished (white)', () => sh('grep -rn max_retries app/', { ms: 300, extra: { exit_code: 1 } }), 'done', 'completed', 'No matches found'],
    ['a failing command is red', () => sh('npm run lint', { ms: 3000, output: 'FAIL\n', extra: { exit_code: 1, status: 'error', error: 'Command exited with code 1' } }), 'error', 'failed', 'Error: Exit code 1'],
    ['an interrupted command is red', () => sh('npm run e2e', { ms: 3000, extra: { status: 'interrupted', state: 'interrupted', execution_status: 'interrupted', error: 'agent is offline', exit_code: null } }), 'interrupted', 'interrupted', 'Interrupted'],
  ]) {
    add(`dots: ${label}`, () => {
      const c = cmd();
      const cut = tool('read_url', { url: 'https://peps.python.org/pep-0654/', status: 'interrupted' });
      const edit = tool('search_replace', { path: '/srv/qa/app/c.py', preview: '@@ -1 +1 @@\n-a\n+b\n', output: 'updated' });
      return { rows: [user('Go.'), turn([say('Checks.\n\n'), T(edit), T(cut), T(c)], [edit, cut, c])], live: true, phase: 'model',
        truth: { call: { [c.id]: truthCall, [cut.id]: 'interrupted', [edit.id]: 'done' }, exec: { [c.command_id]: truthExec } }, has: [has], diffRows: 2 };
    });
  }
  add('a background command that ended says how; its row stops pulsing', () => {
    const cmd = sh('npm run build', { background: true, ms: 300, job: 'bgend', extra: { status: 'done', state: 'running', execution_status: 'running', exit_code: null } });
    const wake = '<command_result id="bgend" state="completed">\nhost: qa-box command: npm run build\nexit_code: 2\nduration_ms: 64000\n\nbuild failed\n</command_result>';
    return { rows: [user('Go.'), turn([say('Building.\n\n'), T(cmd)], [cmd], ended), user(wake, { auto: true })],
      truth: { call: { [cmd.id]: 'done' }, exec: { bgend: 'completed' } },
      has: ['Ran 1 shell command', 'Background command "npm run build" completed (exit code 2)'], lacks: ['Running in the background'] };
  });
  add('a background command that ended, as the newest row, says how it ended', () => {
    const cmd = sh('npm run build', { background: true, ms: 300, job: 'bgend2', extra: { status: 'done', state: 'completed', execution_status: 'completed', exit_code: 2 } });
    return { rows: [user('Go.'), turn([say('Building.\n\n'), T(cmd)], [cmd])], live: true, phase: 'model',
      truth: { call: { [cmd.id]: 'done' }, exec: { bgend2: 'completed' } }, has: ['Ran in the background · exit code 2'], lacks: ['Running in the background'] };
  });
  add('a long running command fills the row before its clock', () => {
    const cmd = sh("cd /srv/qa && PYTHONPATH=/srv/qa/vendor/lib/python3.12/site-packages python -m pytest -q tests/integration/test_dispatcher_backoff_and_retries.py -k 'not slow and not flaky' --maxfail=3", { running: true, ms: 7000 });
    return { rows: [user('Go.'), turn([think('Run.', 2000), T(cmd)], [cmd])], live: true, phase: 'tools',
      truth: { call: { [cmd.id]: 'running' }, exec: { [cmd.command_id]: 'running' } }, wide: true, has: ['Running PYTHONPATH=/srv/qa/vendor'] };
  });
  // Rhythm and columns (ConsoleAudit: row-height, prose-gap, agent-sub-column,
  // foot-column): prose between a summary line, an agents tree and a call, then
  // the turn's closing line.
  add('prose, an agents tree and the closing line keep one rhythm and one column', () => {
    const agents = ['Audit retry settings', 'Check the transport timeouts', 'Review the dispatcher tests']
      .map((description, i) => tool('spawn_subagent', { description, child_id: `rhythm-agent-${i}`, agent_tool_uses: 3 + i }));
    const read = tool('read_file', { path: '/srv/qa/app/config.py', output: '1: retries = 3\n' });
    const edit = tool('search_replace', { path: '/srv/qa/app/config.py', preview: '@@ -1 +1 @@\n-retries = 3\n+retries = 5\n', output: 'updated' });
    const all = [read, ...agents, edit];
    return { rows: [user('Go.'), turn([think('Look.', 2000), T(read), say('Fanning out.\n\n'), ...agents.map(T), say('All three are back.\n\n'), T(edit), say('Retries are now 5.')], all, ended)],
      truth: { call: Object.fromEntries(all.map((t) => [t.id, 'done'])) }, has: ['3 agents finished', 'Retries are now 5.', 'for 30s'], diffRows: 2 };
  });
  return list;
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
    await page.addScriptTag({ path: path.join(__dirname, 'console_ui_lib.js') });
    await page.evaluate(() => fuseToolFixture.view('console'));

    // 1. Regression scenarios.
    const failures = [];
    for (const width of [1280, 390, 320]) {
      await page.setViewportSize({ width, height: 900 });
      for (const view of ['cli', 'compact', 'console', 'mainframe']) {
        const result = await page.evaluate(async ({ view, call }) => {
          fuseToolFixture.view(view);
          await new Promise((resolve) => requestAnimationFrame(resolve));
          const todo = { id: 'scale-todo', name: 'todo_write', status: 'done', todos: [{ content: 'Audit chat text scaling across every reading mode and every tool output', status: 'in_progress' }], results: [], error: '' };
          fuseToolFixture.mountThread([{ role: 'user', content: 'Go.' }, { id: 'scale-turn', role: 'assistant', content: '', reasoning: 'Checking.', elapsedMs: 65000, content: 'Working.', tools: [todo, call], segments: [{ type: 'tool', id: todo.id }, { type: 'content', text: 'Working.' }, { type: 'tool', id: call.id }] }], { live: true, phase: 'tools' });
          const dock = document.querySelector('[data-live-dock]');
          const label = dock.querySelector('.chat-status-verb');
          const probe = document.createElement('div');
          probe.innerHTML = '<div class="chat-agent-live-dock"><span class="chat-status-verb">Working</span></div>' +
            '<div class="chat-turn"><div class="chat-recap">Recap</div><div class="chat-bubble">Message</div></div>' +
            '<div class="chat-compact-mark">Compacted</div><details class="chat-compact-banner"><summary>Summary</summary><div class="chat-compact-handoff">Handoff</div></details>' +
            '<span class="chat-user-file">File</span><span class="chat-file-ext">TXT</span><div class="chat-foot"><span class="chat-foot-meta">Tokens</span></div>' +
            '<span class="chat-source-domain">example.test</span><span class="chat-source-title">Source</span><span class="chat-source-state">Read</span><details class="chat-citations"><summary><span class="chat-source-badge">1 source</span></summary></details>';
          document.querySelector('.chat-log').append(probe);
          const targets = [label, ...probe.querySelectorAll('.chat-status-verb, .chat-recap, .chat-bubble, .chat-compact-mark, .chat-compact-banner, .chat-compact-handoff, .chat-user-file, .chat-file-ext, .chat-foot-meta, .chat-source-domain, .chat-source-title, .chat-source-state, .chat-source-badge')];
          const samples = [];
          const sizes = [];
          const aligned = [];
          for (const size of [100, 50, 130]) {
            FuseChatAppearance.setSize(size);
            sizes.push(parseFloat(getComputedStyle(label).fontSize));
            samples.push(targets.map((el) => parseFloat(getComputedStyle(el).fontSize)));
            // The status line is a row like the call above it: its glyph sits on
            // the dot column and its text starts where the call's text starts, in
            // the same type, at every text size.
            const row = document.querySelector('.chat-step.is-console-call');
            const dot = row && row.querySelector(':scope > .chat-step-node');
            const head = row && (row.querySelector('.chat-console-call') || row.querySelector('.chat-step-label'));
            const glyph = dock.querySelector('.chat-activity-spin');
            const prose = document.querySelector('.chat-md');
            const box = (el) => el.getBoundingClientRect();
            if (view === 'mainframe' && glyph && prose) {
              // Mainframe folds a running call into the live line, so its text column is the prose's.
              aligned.push({
                size,
                text: Math.abs(box(label).left - (box(prose).left + parseFloat(getComputedStyle(prose).paddingLeft))),
                type: Math.abs(parseFloat(getComputedStyle(label).fontSize) - parseFloat(getComputedStyle(prose).fontSize)),
                at: 'prose',
              });
            } else if (row && dot && head && glyph) {
              aligned.push({
                size,
                glyph: Math.abs(box(glyph).left + box(glyph).width / 2 - (box(dot).left + box(dot).width / 2)),
                text: Math.abs(box(label).left - box(head).left),
                type: Math.abs(parseFloat(getComputedStyle(label).fontSize) - parseFloat(getComputedStyle(head).fontSize)),
                at: head.className,
              });
            } else aligned.push({ size, missing: true });
          }
          const meta = dock.querySelector('.chat-status-meta');
          const bounds = dock.getBoundingClientRect();
          const verbBounds = label.getBoundingClientRect();
          const unscaled = targets.filter((el, i) => Math.abs(samples[1][i] / samples[0][i] - 0.5) > 0.01 || Math.abs(samples[2][i] / samples[0][i] - 1.3) > 0.01).map((el) => el.className);
          probe.remove();
          const out = { unscaled, sizes, aligned, label: label.textContent, metaHidden: getComputedStyle(meta).display === 'none', fits: verbBounds.right <= bounds.right + 1, ellipsis: getComputedStyle(label).textOverflow === 'ellipsis' };
          FuseChatAppearance.setSize(100);
          return out;
        }, { view, call: sh('npm test', { running: true, ms: 4000, description: 'Run the tests' }) });
        if (Math.abs(result.sizes[1] / result.sizes[0] - 0.5) > 0.01 || Math.abs(result.sizes[2] / result.sizes[0] - 1.3) > 0.01) failures.push(`status scaling ${view} @${width}: ${result.sizes}`);
        if (result.unscaled.length) failures.push(`transcript scaling ${view} @${width}: ${result.unscaled.join(', ')}`);
        if (view === 'console' || view === 'mainframe') {
          for (const row of result.aligned) {
            if (row.missing) failures.push(`status line alignment ${view} @${width}: no call row to compare with`);
            else if ((view === 'console' && row.glyph > 1) || row.text > 1.5 || row.type > 0.1) failures.push(`status line off the rows ${view} @${width} ${row.size}%: glyph ${(row.glyph || 0).toFixed(1)}px, text ${row.text.toFixed(1)}px, type ${row.type.toFixed(2)}px [${row.at}]`);
          }
        }
        if (view === 'console') {
          if (result.label.startsWith('Audit chat text scaling')) failures.push('Console status must never show the in-progress todo; it has its own section');
          if (width <= 600 && (!result.metaHidden || !result.fits || !result.ellipsis)) failures.push('mobile Console status must hide counters and ellipsize the task');
          if (width === 1280 && result.metaHidden) failures.push('desktop Console keeps status counters');
        }
      }
    }
    await page.evaluate(() => fuseToolFixture.view('console'));
    for (const sc of scenarios()) {
      if (ONLY !== null) break;
      for (const width of sc.wide ? [1280, 390] : [1280]) {
        await page.setViewportSize({ width, height: 900 });
        const result = await page.evaluate(async (sc) => {
          fuseToolFixture.thinking(true);
          fuseToolFixture.mountThread(sc.rows, { live: Boolean(sc.live), phase: sc.phase || '' });
          await new Promise((r) => setTimeout(r, 30));
          const text = document.querySelector('.chat-log').innerText.replace(/[ \t]+/g, ' ');
          const flat = text.replace(/\s*\n\s*/g, ' ');
          const out = { missing: [], present: [], audit: ConsoleAudit.check(sc.truth, { live: Boolean(sc.live) }) };
          for (const want of sc.has || []) if (!flat.includes(want)) out.missing.push(want);
          for (const not of sc.lacks || []) if (flat.includes(not)) out.present.push(not);
          if (sc.diffRows) {
            const rows = document.querySelectorAll('.chat-log .chat-console-lines.is-diff .chat-console-nline').length;
            if (rows !== sc.diffRows) out.missing.push(`${sc.diffRows} diff rows (saw ${rows})`);
          }
          if (sc.wide) {
            const label = [...document.querySelectorAll('.chat-log li.is-console-call .chat-step-label')].pop();
            const call = label && label.querySelector('.chat-console-call');
            const took = label && label.querySelector('.chat-console-took');
            if (!call || call.getBoundingClientRect().width < label.getBoundingClientRect().width * 0.6) out.missing.push('command fills most of the row');
            if (!took || took.getBoundingClientRect().right > label.getBoundingClientRect().right + 1) out.missing.push('clock stays inside the row');
          }
          if (sc.expand) {
            document.querySelector(sc.expand.click)?.click();
            await new Promise((r) => setTimeout(r, 30));
            const opened = document.querySelector('.chat-log').innerText.replace(/\s*\n\s*/g, ' ');
            for (const want of sc.expand.has) if (!opened.includes(want)) out.missing.push(`after opening: ${want}`);
          }
          return out;
        }, sc);
        const where = `${sc.name} @${width}`;
        for (const m of result.missing) failures.push(`${where}: missing "${m}"`);
        for (const p of result.present) failures.push(`${where}: should not show "${p}"`);
        for (const v of result.audit) failures.push(`${where}: ${v.rule} ${v.detail} | ${v.row}`);
      }
    }
    await page.setViewportSize({ width: 1280, height: 900 });

    // /thinking hides every thought at once, in every view, and brings them back.
    if (ONLY === null) {
      const thinking = await page.evaluate(() => {
        const a = { id: 'th1', name: 'read_file', status: 'done', path: '/srv/qa/a.py', output: '1: x\n', results: [], error: '' };
        const rows = [{ role: 'user', content: 'Go.' }, { role: 'assistant', model: 'qa/model', elapsedMs: 9000, endedAt: '2026-10-03T20:00:00Z',
          reasoning: 'Plan.Answer.', content: 'Done.', tools: [a],
          segments: [{ type: 'reasoning', text: 'Plan.', ms: 4000 }, { type: 'tool', id: 'th1' }, { type: 'reasoning', text: 'Answer.', ms: 9000 }, { type: 'content', text: 'Done.' }] }];
        const out = {};
        for (const view of ['console', 'cli', 'compact']) {
          fuseToolFixture.view(view);
          fuseToolFixture.thinking(true);
          fuseToolFixture.mountThread(rows);
          const shown = document.querySelectorAll('.chat-log .is-think, .chat-log .is-console-group').length;
          const label = document.querySelector('.chat-log').innerText;
          fuseToolFixture.slash('thinking', '');
          const hidden = ConsoleAudit.check({}, { thinking: false }).filter((v) => v.rule === 'thinking-shown').length;
          const after = document.querySelector('.chat-log').innerText;
          fuseToolFixture.slash('thinking', 'on');
          out[view] = { shown, hidden, thoughtBefore: /Thought for|Plan\./.test(label), thoughtAfter: /Thought for|Plan\./.test(after),
            back: /Thought for|Plan\./.test(document.querySelector('.chat-log').innerText) };
        }
        fuseToolFixture.view('console');
        return out;
      });
      for (const [view, r] of Object.entries(thinking)) {
        if (!r.thoughtBefore) failures.push(`/thinking ${view}: thoughts shown before`);
        if (r.thoughtAfter || r.hidden) failures.push(`/thinking ${view}: thoughts still shown after /thinking`);
        if (!r.back) failures.push(`/thinking ${view}: /thinking on brings them back`);
      }
    }

    // 2–3. Generated histories, every step audited.
    const seeds = ONLY !== null ? [ONLY] : Array.from({ length: SEEDS }, (_, i) => i + 1);
    let renders = 0;
    for (const seed of seeds) {
      const result = await page.evaluate(async (seed) => {
        const steps = ConsoleAudit.generate(seed);
        const found = [];
        steps.forEach((step, index) => {
          fuseToolFixture.mountThread(step.rows, { live: step.live, phase: step.phase });
          for (const v of ConsoleAudit.check(step.truth, { live: step.live, thinkingLine: step.thinkingLine })) found.push({ step: index, note: step.note, ...v });
        });
        // The finished thread with thinking hidden, and in the other views.
        const last = steps[steps.length - 1];
        fuseToolFixture.thinking(false);
        fuseToolFixture.mountThread(last.rows);
        for (const v of ConsoleAudit.check(last.truth, { thinking: false })) found.push({ step: steps.length - 1, note: 'thinking hidden', ...v });
        fuseToolFixture.thinking(true);
        for (const view of ['cli', 'compact']) {
          fuseToolFixture.view(view);
          fuseToolFixture.mountThread(last.rows);
          const text = document.querySelector('.chat-log').innerText;
          if (/\bundefined\b|\bNaN\b|\[object Object\]/.test(text)) found.push({ step: steps.length - 1, note: view, rule: 'placeholder-text', row: '', detail: '' });
        }
        fuseToolFixture.view('console');
        return { renders: steps.length + 3, found };
      }, seed);
      renders += result.renders;
      for (const v of result.found) failures.push(`seed ${seed} step ${v.step} (${v.note}): ${v.rule} ${v.detail} | ${v.row}`);
    }

    if (errors.length) failures.push(...errors.map((e) => `page error: ${e}`));
    const unique = [...new Set(failures)];
    if (unique.length) {
      console.error(unique.slice(0, 60).join('\n'));
      if (unique.length > 60) console.error(`… and ${unique.length - 60} more`);
    }
    assert.equal(unique.length, 0, `${unique.length} Console UI problems`);
    console.log(`PASS: Console UI — ${scenarios().length} regression scenarios, /thinking in 3 views, ${seeds.length} generated histories (${renders} renders) in ${((Date.now() - started) / 1000).toFixed(1)}s`);
  } finally {
    await browser.close();
  }
})().catch((error) => { console.error(error); process.exit(1); });
