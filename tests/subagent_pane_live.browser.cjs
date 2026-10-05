// Open subagent pane (Console + Mainframe): the child's own transcript while
// the pane stays open. Dock labels, report delivery, disclosure and the
// lifecycle matrix live in subagent_status / agent_ui / subagent_lifecycle.
const assert = require('node:assert/strict');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const origin = process.env.FUSE_BROWSER_ORIGIN || 'http://127.0.0.1:8099';
assert.match(origin, /^http:\/\/127\.0\.0\.1:\d+$/);

// Fresh: a stale updated_at reads as stalled (quiet >= 180s) and hides the dock.
const NOW = new Date().toISOString();
const LONG = '/srv/work/projects/fuse/app/web/notifications/very/deeply/nested/service-worker-registration.js';
const runs = new Map();
let agents = [];
let jobs = [];
let THREAD = '';

function child(id, patch) {
  const row = {
    id, event_cursor: 1, message_id: id + '-msg', thread_id: '',
    description: id, status: 'running', created_at: NOW, updated_at: NOW,
    work_ms: 4000, work_live: true, resumable: false, has_transcript: true,
    message: { role: 'assistant', content: '', reasoning: '', tools: [], elapsedMs: 4000 },
    messages: [],
    ...patch,
  };
  runs.set(id, row);
  return row;
}
function bump(id, patch) {
  const row = runs.get(id);
  row.event_cursor += 1;
  row.updated_at = new Date().toISOString();
  Object.assign(row, patch);
  if (patch.message) row.message = { role: 'assistant', ...patch.message };
  return row;
}

(async () => {
  const browser = await chromium.launch({ headless: true, executablePath: process.env.CHROMIUM_PATH || undefined, args: ['--no-sandbox'] });
  const errors = [];
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, reducedMotion: 'reduce' });
    page.on('pageerror', (e) => errors.push(e.message));
    await page.route('**/*', (route) => new URL(route.request().url()).origin === origin ? route.continue() : route.abort());
    await page.route('**/chat/runs/*', (route) => {
      const id = decodeURIComponent(new URL(route.request().url()).pathname.split('/').pop());
      const row = runs.get(id);
      return row ? route.fulfill({ json: row }) : route.fulfill({ status: 404, json: { detail: 'missing ' + id } });
    });
    await page.route('**/chat/threads/*/agents', (route) => route.fulfill({ json: { agents } }));
    await page.route('**/chat/threads/*/jobs', (route) => route.fulfill({ json: { jobs, recent: [] } }));
    await page.goto(origin);
    await page.locator('[data-sync-label]').filter({ hasText: 'Live sync' }).waitFor({ state: 'attached' });
    await page.waitForFunction(() => Boolean(window.fuseToolFixture));

    const pane = () => page.evaluate(() => {
      const body = document.querySelector('[data-agent-log-body]');
      const flat = (el) => (el?.textContent || '').replace(/\s+/g, ' ').trim();
      const rowOf = (el) => ({
        tool: el.getAttribute('data-tool') || '',
        state: el.getAttribute('data-state') || '',
        hang: el.querySelector('.chat-console-hang')?.className || '',
        text: flat(el),
      });
      const steps = [...(body?.querySelectorAll(':scope [data-step]') || [])].map(rowOf);
      const notices = [...(body?.querySelectorAll('.is-console-notice') || [])].map((el) => flat(el));
      const dock = document.querySelector('[data-agent-live-dock]');
      const verb = document.querySelector('[data-agent-live-label] .chat-status-verb');
      const meta = document.querySelector('[data-agent-live-label] .chat-status-meta');
      const badge = document.querySelector('[data-agent-status]');
      const current = body?.querySelector('[data-agent-current]');
      const clocks = [...(body?.querySelectorAll('[data-cc-clock]') || [])].map((el) => el.textContent);
      const box = (sel) => {
        const el = document.querySelector(sel);
        if (!el || el.hidden) return null;
        const r = el.getBoundingClientRect();
        return { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) };
      };
      return {
        title: flat(document.querySelector('[data-agent-title]')),
        badge: badge?.textContent || '',
        badgeClass: badge?.className || '',
        stop: Boolean(document.querySelector('[data-agent-stop]') && !document.querySelector('[data-agent-stop]').hidden),
        resume: Boolean(document.querySelector('[data-agent-resume]') && !document.querySelector('[data-agent-resume]').hidden),
        dockHidden: dock ? dock.hidden : null,
        dockVerb: verb?.textContent || '',
        dockMeta: meta && !meta.hidden ? meta.textContent : '',
        spin: Boolean(document.querySelector('[data-agent-live-spin]')?.firstChild),
        text: flat(body),
        steps, notices, clocks,
        turns: body?.querySelectorAll('[data-agent-current]').length || 0,
        articles: body?.children.length || 0,
        report: flat(current),
        main: flat(document.querySelector('[data-chat-thread]')),
        paneJobs: [...document.querySelectorAll('[data-agent-jobs] [data-show-job]')].map((el) => el.getAttribute('data-show-job')),
        overflow: document.scrollingElement.scrollWidth - window.innerWidth,
        panel: box('[data-chat-agents]'),
        close: box('[data-agents-close]'),
        view: document.querySelector('.chat')?.getAttribute('data-view') || '',
      };
    });
    const open = async (id) => {
      await page.evaluate((agent) => fuseToolFixture.todo.openAgent(agent), id);
      await page.locator('[data-agent-log-body]').waitFor({ state: 'visible' });
    };
    const poll = () => page.evaluate(() => fuseToolFixture.pollAgent());

    const created = await page.evaluate(async () => fuseToolFixture.api('/chat/threads', {
      method: 'POST', body: JSON.stringify({ model: 'offline', tools: [], command_id: crypto.randomUUID(), title: 'Pane fixture' }),
    }));
    THREAD = created.id;
    for (const row of runs.values()) row.thread_id = THREAD;

    // --- 1. Live transcript: text, thinking, then a mix of calls ---
    child('nova', {
      description: 'Audit the router',
      message: { role: 'assistant', content: 'NOVA_DRAFT_ONE', reasoning: 'NOVA_THINKING_ABOUT_THE_ROUTER', tools: [], elapsedMs: 4000 },
    });
    agents = [{ id: 'nova', status: 'running', description: 'Audit the router', work_ms: 4000, work_live: true, created_at: NOW }];
    await page.evaluate((thread) => {
      fuseToolFixture.view('console');
      fuseToolFixture.thinking(true);
      fuseToolFixture.mount([{ tools: [
        { id: 'sp-nova', name: 'spawn_subagent', status: 'done', background: true, child_id: 'nova', description: 'Audit the router' },
      ] }], false, thread);
      fuseToolFixture.sidebar.mount({ id: thread, title: 'Pane fixture', updated_at: new Date().toISOString(), running: false });
    }, THREAD);
    await open('nova');
    await page.locator('[data-agent-log-body]').getByText('NOVA_DRAFT_ONE').waitFor();
    let ui = await pane();
    assert.equal(ui.title, 'Audit the router', `title: ${ui.title}`);
    assert.equal(ui.badge, 'running', `badge: ${ui.badge}`);
    assert.match(ui.badgeClass, /badge live/, ui.badgeClass);
    assert.equal(ui.stop, true, 'a running child offers Stop');
    assert.equal(ui.dockHidden, false, 'the live dock shows while the child runs');
    assert.match(ui.dockVerb, /…$/, `dock verb keeps its ellipsis: ${ui.dockVerb}`);
    assert.match(ui.text, /NOVA_THINKING_ABOUT_THE_ROUTER/, 'thinking renders inside the open pane');
    assert.doesNotMatch(ui.main, /NOVA_DRAFT_ONE|NOVA_THINKING/, 'child text never lands in the parent transcript');

    bump('nova', { message: {
      content: 'NOVA_DRAFT_TWO', reasoning: 'NOVA_THINKING_ABOUT_THE_ROUTER and the pool', elapsedMs: 9000,
      tools: [
        { id: 't-read', name: 'read_file', status: 'running', path: 'app/web/chat.py', description: 'Read the chat route' },
        { id: 't-grep', name: 'grep', status: 'done', pattern: 'pollAgentView', path: 'static', preview: 'static/chat.js:15320: async function pollAgentView()' },
      ],
    } });
    await poll();
    await page.locator('[data-agent-log-body]').getByText('NOVA_DRAFT_TWO').waitFor();
    ui = await pane();
    const read = ui.steps.find((row) => row.tool === 'read_file');
    const grep = ui.steps.find((row) => row.tool === 'grep');
    assert.equal(read?.state, 'running', `read stays live: ${JSON.stringify(read)}`);
    // A described running call reads as its -ing phrase; the path shows once it ends.
    assert.match(read.text, /Reading the chat route/, `running read reads its description: ${read.text}`);
    assert.doesNotMatch(read.hang, /is-running/, `a described read has no hang of its own: ${read.hang}`);
    assert.equal(grep?.state, 'done', `grep settles: ${JSON.stringify(grep)}`);
    assert.match(grep.text, /Search\(pattern: "pollAgentView", path: "static"\)/, `grep names its pattern: ${grep.text}`);
    assert.match(ui.text, /and the pool/, 'thinking grows instead of being replaced');
    assert.equal(ui.turns, 1, `one current bubble: ${ui.turns}`);
    assert.doesNotMatch(ui.main, /NOVA_DRAFT_TWO|pollAgentView/);

    bump('nova', { message: {
      content: 'NOVA_DRAFT_TWO', reasoning: 'NOVA_THINKING_ABOUT_THE_ROUTER and the pool', elapsedMs: 12000,
      tools: [
        { id: 't-read', name: 'read_file', status: 'done', path: 'app/web/chat.py', preview: 'def route():\n    return', output: 'def route():\n    return' },
        { id: 't-grep', name: 'grep', status: 'done', pattern: 'pollAgentView', path: 'static', preview: 'static/chat.js:15320: async function pollAgentView()' },
        { id: 't-sh', name: 'run_terminal_command', status: 'running', command: 'pytest tests/test_chat.py -q', description: 'Run the chat tests', elapsed_ms: 3000 },
        { id: 't-ed', name: 'search_replace', status: 'done', path: 'app/web/chat.py', added: 2, removed: 1, preview: '@@ -1 +1 @@\n-old\n+new\n+line' },
        { id: 't-sc', name: 'scratch', status: 'done', preview: '{"slots":{"notes":42}}' },
      ],
    } });
    await poll();
    await page.locator('[data-agent-log-body] [data-tool="run_terminal_command"]').waitFor();
    ui = await pane();
    const readDone = ui.steps.find((row) => row.tool === 'read_file');
    const sh = ui.steps.find((row) => row.tool === 'run_terminal_command');
    const edit = ui.steps.find((row) => row.tool === 'search_replace');
    const scratch = ui.steps.find((row) => row.tool === 'scratch');
    assert.equal(readDone?.state, 'done', `read finishes: ${JSON.stringify(readDone)}`);
    assert.match(readDone.text, /Read\(app\/web\/chat\.py\)/, `finished read prints the call: ${readDone.text}`);
    assert.match(readDone.text, /Read 2 lines/, `finished read counts lines: ${readDone.text}`);
    assert.doesNotMatch(readDone.hang, /is-running/, `finished read drops the running hang: ${readDone.hang}`);
    assert.equal(sh?.state, 'running', `shell stays live: ${JSON.stringify(sh)}`);
    assert.match(sh.text, /Running the chat tests/, `shell reads its description: ${sh.text}`);
    assert.match(edit.text, /Added 2 lines, removed 1 line/, `edit names the change: ${edit.text}`);
    assert.match(edit.text, /\+new/, 'the diff stays under the edit row');
    assert.equal(scratch?.state, 'done', `scratch settles: ${JSON.stringify(scratch)}`);
    assert.match(scratch.text, /notes/, `scratch shows its slot: ${scratch.text}`);
    assert.equal(ui.steps.filter((row) => row.tool === 'read_file').length, 1, 'a settled call does not duplicate its row');
    const clock = ui.clocks.find((text) => text.trim());
    assert.ok(clock, `the running shell shows a clock: ${JSON.stringify(ui.clocks)}`);
    // The pane has no parent turn streaming, so its own timer must move the clock.
    await page.waitForFunction((seen) => {
      const next = [...document.querySelectorAll('[data-agent-log-body] [data-cc-clock]')].map((el) => el.textContent).find((text) => text.trim());
      return next && next !== seen;
    }, clock, { timeout: 5000 });

    // --- 2. Finishing clears every running mark; the report stays ---
    bump('nova', {
      status: 'completed', work_live: false,
      message: {
        content: 'NOVA_FINAL_REPORT', reasoning: '', elapsedMs: 15000,
        tools: [
          { id: 't-read', name: 'read_file', status: 'done', path: 'app/web/chat.py', output: 'def route():\n    return' },
          { id: 't-grep', name: 'grep', status: 'done', pattern: 'pollAgentView', path: 'static', preview: 'static/chat.js:15320: async function pollAgentView()' },
          { id: 't-sh', name: 'run_terminal_command', status: 'done', command: 'pytest tests/test_chat.py -q', exit_code: 0, call_duration_ms: 4200, output: '2 passed' },
          { id: 't-ed', name: 'search_replace', status: 'done', path: 'app/web/chat.py', added: 2, removed: 1, preview: '@@ -1 +1 @@\n-old\n+new\n+line' },
          { id: 't-sc', name: 'scratch', status: 'done', preview: '{"slots":{"notes":42}}' },
        ],
      },
    });
    agents = [{ id: 'nova', status: 'completed', description: 'Audit the router', work_ms: 15000, work_live: false, created_at: NOW }];
    await poll();
    await page.locator('[data-agent-status]').getByText('completed', { exact: true }).waitFor();
    ui = await pane();
    assert.equal(ui.badge, 'completed', `badge settles: ${ui.badge}`);
    assert.match(ui.badgeClass, /badge on/, ui.badgeClass);
    assert.equal(ui.stop, false, 'Stop leaves when the child finishes');
    assert.equal(ui.dockHidden, true, 'the live dock hides when the child finishes');
    assert.equal(ui.spin, false, 'the dock spinner is gone');
    assert.equal(ui.steps.filter((row) => row.state === 'running').length, 0, `no row stays Running: ${JSON.stringify(ui.steps.map((r) => r.tool + ':' + r.state))}`);
    assert.equal(ui.clocks.filter((text) => text.trim()).length, 0, `finished rows stop ticking: ${JSON.stringify(ui.clocks)}`);
    const shell = ui.steps.find((row) => row.tool === 'run_terminal_command');
    assert.match(shell.text, /Bash\(pytest tests\/test_chat\.py -q\)/, `finished shell prints the command: ${shell.text}`);
    assert.match(shell.text, /2 passed/, 'the shell keeps its output');
    assert.match(ui.text, /NOVA_FINAL_REPORT/, 'the final report shows in the pane');
    assert.doesNotMatch(ui.main, /NOVA_FINAL_REPORT/);
    const settled = ui.text;
    await poll();
    ui = await pane();
    assert.equal(ui.text, settled, 'a repeated snapshot does not change the settled transcript');
    assert.equal(ui.articles, 1, `one article after reconcile: ${ui.articles}`);

    // --- 3. Two children never share a transcript ---
    child('ivy', {
      status: 'running', description: 'Check the pool',
      message: { role: 'assistant', content: 'IVY_ONLY_TEXT', tools: [
        { id: 'i-grep', name: 'grep', status: 'running', pattern: 'IVY_PATTERN', path: 'app' },
      ] },
    });
    agents = [
      { id: 'nova', status: 'completed', description: 'Audit the router', work_ms: 15000, work_live: false, created_at: NOW },
      { id: 'ivy', status: 'running', description: 'Check the pool', work_ms: 2000, work_live: true, created_at: NOW },
    ];
    await page.locator('[data-agent-back]').click();
    await page.locator('[data-agent-id="ivy"]').waitFor();
    await open('ivy');
    await page.locator('[data-agent-log-body]').getByText('IVY_ONLY_TEXT').waitFor();
    ui = await pane();
    assert.equal(ui.title, 'Check the pool', `ivy title: ${ui.title}`);
    assert.equal(ui.badge, 'running');
    assert.match(ui.text, /IVY_PATTERN/);
    assert.doesNotMatch(ui.text, /NOVA_FINAL_REPORT|chat\.py|NOVA_DRAFT/, `ivy must not inherit nova: ${ui.text.slice(0, 240)}`);
    bump('ivy', { status: 'completed', work_live: false, message: { role: 'assistant', content: 'IVY_FINAL', tools: [
      { id: 'i-grep', name: 'grep', status: 'done', pattern: 'IVY_PATTERN', path: 'app', preview: 'app/pool.py:4: rotator' },
    ] } });
    await poll();
    await page.locator('[data-agent-status]').getByText('completed', { exact: true }).waitFor();
    await page.locator('[data-agent-back]').click();
    await open('nova');
    await page.locator('[data-agent-log-body]').getByText('NOVA_FINAL_REPORT').waitFor();
    ui = await pane();
    assert.doesNotMatch(ui.text, /IVY_ONLY_TEXT|IVY_PATTERN|IVY_FINAL/, 'returning to nova shows only nova');
    assert.match(ui.text, /2 passed/, 'nova still has the shell output it finished with');
    assert.equal(ui.badge, 'completed');
    assert.equal(ui.dockHidden, true);

    // --- 4. Close, reopen, reload: nothing lost, nothing doubled ---
    await page.locator('[data-agents-close]').click();
    await page.locator('[data-agent-view]').waitFor({ state: 'hidden' });
    await open('nova');
    await page.locator('[data-agent-log-body]').getByText('NOVA_FINAL_REPORT').waitFor();
    ui = await pane();
    assert.equal(ui.steps.filter((row) => row.tool === 'read_file').length, 1, 'reopening does not double rows');
    assert.match(ui.text, /NOVA_FINAL_REPORT/);
    await page.reload();
    await page.locator('[data-sync-label]').filter({ hasText: 'Live sync' }).waitFor({ state: 'attached' });
    await page.waitForFunction(() => Boolean(window.fuseToolFixture));
    await page.evaluate((thread) => {
      fuseToolFixture.view('console');
      fuseToolFixture.thinking(true);
      fuseToolFixture.mount([{ tools: [
        { id: 'sp-nova', name: 'spawn_subagent', status: 'done', background: true, child_id: 'nova', description: 'Audit the router' },
      ] }], false, thread);
      fuseToolFixture.sidebar.mount({ id: thread, title: 'Pane fixture', updated_at: new Date().toISOString(), running: false });
    }, THREAD);
    await open('nova');
    await page.locator('[data-agent-log-body]').getByText('NOVA_FINAL_REPORT').waitFor();
    ui = await pane();
    assert.equal(ui.steps.map((row) => row.tool).filter(Boolean).length, 5, `reload keeps every call: ${JSON.stringify(ui.steps.map((r) => r.tool))}`);
    assert.equal(ui.steps.filter((row) => row.state === 'running').length, 0, 'reload does not revive a finished call');
    console.log('PASS console live pane, isolation, reopen and reload');

    // --- 5. A failed child and a killed child keep their output ---
    for (const view of ['console', 'mainframe']) {
      child('fail', {
        status: 'failed', description: 'Broken worker', work_live: false,
        message: { role: 'assistant', content: 'FAIL_PARTIAL_OUTPUT', error: 'provider down', tools: [
          { id: 'f-read', name: 'read_file', status: 'error', path: 'missing.py', error: 'No such file' },
        ] },
      });
      child('killed', {
        status: 'cancelled', description: 'Stopped worker', work_live: false, resumable: true, resume_count: 0,
        message: { role: 'assistant', content: 'KILLED_KEPT_OUTPUT', tools: [
          { id: 'k-sh', name: 'run_terminal_command', status: 'interrupted', command: 'sleep 30', error: 'cancelled' },
        ] },
      });
      agents = [
        { id: 'fail', status: 'failed', description: 'Broken worker', error: 'provider down', work_ms: 2000, created_at: NOW },
        { id: 'killed', status: 'cancelled', description: 'Stopped worker', resumable: true, work_ms: 3000, created_at: NOW },
      ];
      await page.evaluate((name) => fuseToolFixture.view(name), view);
      await page.locator('[data-agents-close]').click();
      await open('fail');
      await page.locator('[data-agent-status]').getByText('failed', { exact: true }).waitFor();
      ui = await pane();
      assert.match(ui.badgeClass, /badge fault/, `${view} failed badge: ${ui.badgeClass}`);
      assert.equal(ui.resume, false, `${view}: a failed child has no Resume`);
      assert.equal(ui.dockHidden, true, `${view}: a failed child shows no live dock`);
      assert.match(ui.text, /FAIL_PARTIAL_OUTPUT/, `${view}: the failed pane keeps its text`);
      assert.match(ui.text, /No such file/, `${view}: the failed call keeps its error`);
      const failed = ui.steps.find((row) => row.tool === 'read_file');
      assert.equal(failed?.state, 'error', `${view} failed row: ${JSON.stringify(failed)}`);
      assert.doesNotMatch(failed.hang, /is-running/, `${view}: a failed row is not Running`);
      await page.locator('[data-agent-back]').click();
      await open('killed');
      await page.locator('[data-agent-status]').getByText('cancelled', { exact: true }).waitFor();
      ui = await pane();
      assert.match(ui.badgeClass, /badge stop/, `${view} cancelled badge: ${ui.badgeClass}`);
      assert.equal(ui.resume, true, `${view}: an interrupted child offers Resume`);
      assert.match(ui.text, /KILLED_KEPT_OUTPUT/);
      const stopped = ui.steps.find((row) => row.tool === 'run_terminal_command');
      assert.equal(stopped?.state, 'interrupted', `${view} killed row: ${JSON.stringify(stopped)}`);
      assert.doesNotMatch(stopped.hang, /is-running/, `${view}: a killed call is not Running`);
      // Console names why it stopped; Mainframe shares the markup but not that line.
      if (view === 'console') assert.match(stopped.text, /Interrupted · stopped/, `${view}: ${stopped.text}`);
      else assert.match(stopped.text, /sleep 30/, `${view}: the killed command stays: ${stopped.text}`);
      assert.doesNotMatch(ui.text, /FAIL_PARTIAL_OUTPUT/, `${view}: the killed pane drops the failed sibling`);
      console.log(`PASS failed and killed children ${view}`);
    }

    // --- 6. A background-command handoff folds into a step, not a raw blob ---
    const envelope = '[Fuse] AUTOMATED EVENT — not written by the user. Background results:\n\n'
      + '<command_result id="jobnova01" state="completed">\nhost: box command: nohup pytest -q\nexit_code: 0\nduration_ms: 2500\ndescription: Run the suite\n\n2 passed, 0 failed\n</command_result>\n\n'
      + 'These are for you, not the user.';
    child('fold', {
      status: 'running', description: 'Folder', event_cursor: 2,
      messages: [
        { role: 'assistant', id: 'fold-seed', content: 'FOLD_BEFORE', tools: [
          { id: 'bg', name: 'run_terminal_command', status: 'running', background: true, child_id: 'jobnova01', command: 'nohup pytest -q', description: 'Run the suite' },
        ] },
        { role: 'user', auto: true, content: envelope },
      ],
      message: { role: 'assistant', content: 'FOLD_AFTER', tools: [] },
    });
    agents = [{ id: 'fold', status: 'running', description: 'Folder', work_ms: 1000, work_live: true, created_at: NOW }];
    jobs = [{ id: 'jobnova01', command_id: 'jobnova01', agent_run_id: 'fold', state: 'completed', command: 'nohup pytest -q', exit_code: 0, background: true, elapsed_ms: 2500 }];
    await page.evaluate(() => fuseToolFixture.view('console'));
    await page.locator('[data-agents-close]').click();
    await open('fold');
    await page.locator('[data-agent-log-body]').getByText(/background command/i).waitFor();
    ui = await pane();
    assert.ok(ui.notices.some((text) => /Background command "Run the suite" completed \(exit code 0\)/.test(text)),
      `handoff folds into a notice: ${JSON.stringify(ui.notices)}`);
    assert.match(ui.text, /2 passed, 0 failed/, 'the folded notice keeps the command output');
    assert.doesNotMatch(ui.text, /<command_result|AUTOMATED EVENT/, 'the raw envelope is not painted');
    assert.match(ui.text, /FOLD_BEFORE/);
    assert.match(ui.text, /FOLD_AFTER/);
    assert.deepEqual(ui.paneJobs, ['jobnova01'], `the pane strip lists the child's command: ${JSON.stringify(ui.paneJobs)}`);
    const bg = ui.steps.find((row) => row.tool === 'run_terminal_command');
    assert.notEqual(bg?.state, 'running', `the handed-off command leaves Running: ${JSON.stringify(bg)}`);
    console.log('PASS background-command handoff folds into the pane');

    // --- 7. Layout: overlay at 390, side pane at 1440, close reachable ---
    for (const [width, height] of [[390, 844], [1440, 1000]]) {
      await page.setViewportSize({ width, height });
      await page.locator('[data-chat-agents]').waitFor({ state: 'visible' });
      ui = await pane();
      assert.ok(ui.panel, `${width}: pane has a box`);
      assert.ok(ui.close, `${width}: close control is shown`);
      assert.ok(ui.close.w >= 24 && ui.close.h >= 24, `${width}: close is tappable ${JSON.stringify(ui.close)}`);
      assert.ok(ui.close.x >= 0 && ui.close.x + ui.close.w <= width + 1, `${width}: close stays inside ${JSON.stringify(ui.close)}`);
      const covered = await page.evaluate(() => {
        const el = document.querySelector('[data-agents-close]');
        const r = el.getBoundingClientRect();
        const hit = document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2);
        return el.contains(hit);
      });
      assert.equal(covered, true, `${width}: close is the topmost element`);
      if (width === 390) {
        assert.ok(ui.panel.w >= 350, `390 overlay fills the screen: ${JSON.stringify(ui.panel)}`);
        assert.ok(ui.panel.x <= 1, `390 overlay starts at the edge: ${JSON.stringify(ui.panel)}`);
      } else {
        assert.ok(ui.panel.w < 900 && ui.panel.x > 400, `1440 pane sits beside the thread: ${JSON.stringify(ui.panel)}`);
      }
      assert.ok(ui.overflow <= 0, `${width}: no page overflow (${ui.overflow}px)`);
      const wrap = await page.evaluate(() => {
        const val = document.querySelector('[data-agent-log-body] .chat-console-val');
        if (!val) return null;
        const body = document.querySelector('[data-agent-log-body]');
        return { scroll: val.scrollWidth > val.clientWidth + 1, inside: val.getBoundingClientRect().right <= body.getBoundingClientRect().right + 1 };
      });
      if (wrap) assert.equal(wrap.inside, true, `${width}: a call argument stays inside the pane`);
      console.log(`PASS pane layout ${width}`);
    }

    // A long path must wrap inside the pane, not widen the page.
    bump('fold', { message: { role: 'assistant', content: 'FOLD_AFTER', tools: [
      { id: 't-long', name: 'read_file', status: 'done', path: LONG, output: 'export const sw = 1;\n' },
    ] } });
    await page.setViewportSize({ width: 390, height: 844 });
    await poll();
    await page.waitForFunction(() => document.querySelector('[data-agent-log-body]')?.textContent.includes('service-worker-registration.js'));
    ui = await pane();
    assert.ok(ui.overflow <= 0, `a long path does not widen the page (${ui.overflow}px)`);
    console.log('PASS long path wraps at 390');

    assert.deepEqual(errors, []);
  } finally { await browser.close(); }
})().catch((error) => { console.error(error); process.exitCode = 1; });
