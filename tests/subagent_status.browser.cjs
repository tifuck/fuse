// Subagent dock, pane and report delivery. Asserts latest server truth
// (mocked run/agent/job payloads), not pixels.
// Already covered elsewhere — do not re-pin: lifecycle matrix and
// "No completed report" summaries (subagent_lifecycle.browser.cjs),
// open/disclosure/404 (agent_ui.browser.cjs), stale-poll cursor
// (stream_consistency.browser.cjs), todo isolation (todo_section.browser.cjs),
// envelope labels and foldAutoCommandResults (command_ui.test.cjs).
const assert = require('node:assert/strict');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const origin = process.env.FUSE_BROWSER_ORIGIN || 'http://127.0.0.1:8099';
assert.match(origin, /^http:\/\/127\.0\.0\.1:\d+$/);

const THREAD = 'status-thread';
const now = new Date().toISOString();
const runs = new Map();
let agents = [];
let jobs = [];

function run(id, patch) {
  const row = {
    id, event_cursor: 1, message_id: id + '-msg', thread_id: THREAD,
    description: id, status: 'running', created_at: now, updated_at: now,
    message: { role: 'assistant', content: '', tools: [] }, messages: [],
    ...patch,
  };
  runs.set(id, row);
  return row;
}
function bump(id, patch) {
  const row = runs.get(id);
  row.event_cursor += 1;
  Object.assign(row, patch);
  return row;
}

(async () => {
  const browser = await chromium.launch({ headless: true, executablePath: process.env.CHROMIUM_PATH || undefined, args: ['--no-sandbox'] });
  const errors = [];
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 900 }, reducedMotion: 'reduce' });
    page.on('pageerror', (e) => errors.push(e.message));
    // Last registered route runs first, so the catch-all must be registered
    // before the handlers that replace it.
    await page.route('**/*', (route) => new URL(route.request().url()).origin === origin ? route.continue() : route.abort());
    await page.route('**/chat/runs/*', (route) => {
      const id = decodeURIComponent(new URL(route.request().url()).pathname.split('/').pop());
      const row = runs.get(id);
      return row
        ? route.fulfill({ json: row })
        : route.fulfill({ status: 404, json: { detail: 'missing ' + id } });
    });
    await page.route('**/chat/threads/' + THREAD + '/agents', (route) => route.fulfill({ json: { agents } }));
    await page.route('**/chat/threads/' + THREAD + '/jobs', (route) => route.fulfill({ json: { jobs, recent: [] } }));
    await page.goto(origin);
    await page.waitForFunction(() => Boolean(window.fuseToolFixture));

    const read = () => page.evaluate(() => {
      const text = (sel) => document.querySelector(sel)?.textContent || '';
      const rows = [...document.querySelectorAll('[data-agents-list] [data-agent-id]')].map((el) => ({
        id: el.getAttribute('data-agent-id'),
        status: (el.querySelector('.chat-agents-state')?.textContent || '').trim(),
        cls: el.className,
        dot: getComputedStyle(el.querySelector('.chat-agents-dot')).backgroundColor,
        text: el.textContent.replace(/\s+/g, ' ').trim(),
      }));
      const dock = document.querySelector('[data-agent-live-dock]');
      const pane = document.querySelector('[data-agent-log-body]');
      const strip = (sel) => [...document.querySelectorAll(sel + ' [data-show-job]')].map((el) => el.getAttribute('data-show-job'));
      const main = document.querySelector('[data-chat-thread]');
      const history = document.querySelector('.chat-history-item.is-on');
      return {
        rows,
        badge: document.querySelector('[data-agent-status]')?.textContent || '',
        badgeClass: document.querySelector('[data-agent-status]')?.className || '',
        resume: Boolean(document.querySelector('[data-agent-resume]') && !document.querySelector('[data-agent-resume]').hidden),
        stop: Boolean(document.querySelector('[data-agent-stop]') && !document.querySelector('[data-agent-stop]').hidden),
        dockHidden: dock ? dock.hidden : null,
        dockSpin: Boolean(document.querySelector('[data-agent-live-spin]')?.firstChild),
        dockLabel: text('[data-agent-live-label]').replace(/\s+/g, ' ').trim(),
        pane: pane ? pane.textContent : '',
        handoff: Boolean(pane?.querySelector('[data-compaction] .chat-compact-handoff')),
        paneJobs: strip('[data-agent-jobs]'),
        mainJobs: strip('[data-jobs-list]'),
        main: main ? main.textContent : '',
        sidebarRunning: Boolean(history?.classList.contains('is-running')),
        sidebarBusy: history?.getAttribute('aria-busy') || '',
        tabCount: document.querySelector('.chat-agents-bar [data-agents-count]')?.textContent || '',
        tabLabel: document.querySelector('.chat-agents-bar')?.getAttribute('aria-label') || '',
      };
    });

    // --- 1. Dock rows: status text, badge class, dot colour, resumable ---
    agents = [
      { id: 'alpha', status: 'completed', description: 'Alpha worker', work_ms: 4000, created_at: now },
      { id: 'bravo', status: 'failed', description: 'Bravo worker', error: 'provider down', work_ms: 2000, created_at: now },
      { id: 'charlie', status: 'interrupted', description: 'Charlie worker', resumable: true, has_transcript: true, work_ms: 9000, created_at: now },
      { id: 'delta', status: 'running', description: 'Delta worker', work_ms: 1500, work_live: true, current: 'read_file', created_at: now },
      { id: 'echo', status: 'queued', description: 'Echo worker', work_ms: 0, created_at: now },
    ];
    run('alpha', { status: 'completed', description: 'Alpha worker', message: { role: 'assistant', content: 'ALPHA_REPORT_ONLY' } });
    run('bravo', { status: 'failed', description: 'Bravo worker', message: { role: 'assistant', content: 'BRAVO_PARTIAL_MUST_NOT_DELIVER', error: 'provider down' } });
    run('charlie', { status: 'interrupted', description: 'Charlie worker', resumable: true, has_transcript: true, resume_count: 0, message: { role: 'assistant', content: 'CHARLIE_PARTIAL' } });
    run('delta', { status: 'running', description: 'Delta worker', activity: { phase: 'tools' }, message: { role: 'assistant', content: '', tools: [{ id: 'd-read', name: 'read_file', status: 'running', path: 'README.md' }] } });
    run('echo', { status: 'queued', description: 'Echo worker', message: { role: 'assistant', content: '' } });
    await page.evaluate((thread) => {
      fuseToolFixture.view('cli');
      fuseToolFixture.mount([{ tools: [
        { id: 'sp-a', name: 'spawn_subagent', status: 'done', background: true, child_id: 'alpha', description: 'Alpha worker' },
        { id: 'sp-b', name: 'spawn_subagent', status: 'done', background: true, child_id: 'bravo', description: 'Bravo worker' },
        { id: 'sp-c', name: 'spawn_subagent', status: 'done', background: true, child_id: 'charlie', description: 'Charlie worker' },
        { id: 'sp-d', name: 'spawn_subagent', status: 'done', background: true, child_id: 'delta', description: 'Delta worker', execution_status: 'running' },
        { id: 'sp-e', name: 'spawn_subagent', status: 'done', background: true, child_id: 'echo', description: 'Echo worker', execution_status: 'queued' },
        { id: 'wait-a', name: 'get_subagent_output', status: 'running', child_id: 'alpha' },
      ] }], false, thread);
      fuseToolFixture.sidebar.mount({ id: thread, title: 'Status fixture', updated_at: new Date().toISOString(), running: false });
    }, THREAD);
    await page.locator('.chat-agents-bar').click();
    await page.waitForFunction(() => document.querySelectorAll('[data-agents-list] [data-agent-id]').length === 5);
    let ui = await read();
    const byId = Object.fromEntries(ui.rows.map((row) => [row.id, row]));
    assert.deepEqual(ui.rows.map((row) => row.id).slice(0, 2), ['delta', 'echo'], 'live rows sort ahead of finished ones');
    assert.equal(byId.delta.status, 'running');
    assert.equal(byId.echo.status, 'queued');
    assert.equal(byId.alpha.status, 'completed');
    assert.equal(byId.bravo.status, 'failed');
    assert.match(byId.bravo.text, /provider down/);
    assert.equal(byId.charlie.status, 'interrupted');
    assert.match(byId.charlie.text, /resumable/);
    assert.match(byId.alpha.cls, /is-completed/);
    assert.match(byId.bravo.cls, /is-failed/);
    assert.match(byId.charlie.cls, /is-interrupted/);
    assert.notEqual(byId.alpha.dot, byId.bravo.dot, 'completed and failed dots differ');
    assert.notEqual(byId.delta.dot, byId.alpha.dot, 'a running dot differs from a completed one');
    assert.equal(byId.charlie.dot, byId.delta.dot, 'interrupted shares the live dot colour');
    assert.match(ui.tabLabel, /2 agents/);
    console.log('PASS dock status labels, classes and dots');

    // --- 2. A report renders only in the agent it names ---
    await page.locator('[data-agent-id="alpha"]').click();
    await page.locator('[data-agent-log-body]').getByText('ALPHA_REPORT_ONLY', { exact: true }).waitFor();
    ui = await read();
    assert.equal(ui.badge, 'completed');
    assert.match(ui.badgeClass, /badge on/);
    assert.equal(ui.resume, false, 'a completed child has no Resume');
    assert.equal(ui.dockHidden, true, 'a finished child shows no live dock');
    assert.doesNotMatch(ui.pane, /BRAVO_PARTIAL|CHARLIE_PARTIAL/);
    assert.doesNotMatch(ui.main, /ALPHA_REPORT_ONLY|BRAVO_PARTIAL/, 'a child report never lands in the main transcript');
    await page.locator('[data-agent-back]').click();
    await page.locator('[data-agent-id="bravo"]').click();
    await page.locator('[data-agent-status]').getByText('failed', { exact: true }).waitFor();
    ui = await read();
    assert.match(ui.badgeClass, /badge fault/);
    assert.match(ui.pane, /BRAVO_PARTIAL_MUST_NOT_DELIVER/, 'the failed child pane shows its own transcript');
    assert.doesNotMatch(ui.pane, /ALPHA_REPORT_ONLY/, 'opening bravo must not keep alpha\'s report');
    assert.doesNotMatch(ui.main, /BRAVO_PARTIAL_MUST_NOT_DELIVER/);
    console.log('PASS report stays inside the named agent pane');

    // --- 3. Wait rows: waiting, then the completed report, never a partial ---
    await page.locator('[data-agent-back]').click();
    await page.locator('[data-agents-close]').click();
    const mainTools = [
      { id: 'sp-a', name: 'spawn_subagent', status: 'done', background: true, child_id: 'alpha', description: 'Alpha worker' },
      { id: 'sp-b', name: 'spawn_subagent', status: 'done', background: true, child_id: 'bravo', description: 'Bravo worker' },
      { id: 'wait-a', name: 'get_subagent_output', status: 'running', child_id: 'alpha' },
    ];
    const wait = await page.evaluate((tools) => {
      const f = fuseToolFixture;
      const row = (id, live) => f.state(f.snapshot()[0].tools.find((t) => t.id === id), live);
      // A join only reads as Waiting while its own turn is still live.
      f.mount([{ tools }], true, 'status-thread');
      const waiting = row('wait-a', true);
      f.mount([{ tools }], false, 'status-thread');
      f.toolEvent({ event: 'tool', id: 'wait-a', name: 'get_subagent_output', child_id: 'alpha', status: 'done', summary: 'ALPHA_REPORT_ONLY' });
      const done = row('wait-a', false);
      f.agents([{ id: 'bravo', status: 'failed', message: { content: 'BRAVO_PARTIAL_MUST_NOT_DELIVER', error: 'provider down' } }]);
      const all = f.snapshot()[0].tools;
      return {
        waiting, done,
        spawnSummary: all.find((t) => t.child_id === 'bravo').summary,
        waitSummary: all.find((t) => t.id === 'wait-a').summary || '',
      };
    }, mainTools);
    assert.equal(wait.waiting.statusLabel, 'Waiting');
    assert.equal(wait.waiting.running, true, 'a live join waits while its child runs');
    assert.equal(wait.done.statusLabel, 'Done');
    assert.equal(wait.done.running, false);
    assert.match(wait.spawnSummary, /No completed report/);
    assert.doesNotMatch(wait.spawnSummary, /MUST_NOT_DELIVER/, 'a child that died delivers no partial as its report');
    assert.equal(wait.waitSummary, 'ALPHA_REPORT_ONLY', 'reconciling another child does not rewrite a returned wait');
    const waitRow = page.locator('[data-step="tool-wait-a"]');
    assert.match(await waitRow.innerText(), /Done/);
    await waitRow.locator('.chat-step-toggle').first().click();
    assert.match(await waitRow.innerText(), /ALPHA_REPORT_ONLY/);
    assert.doesNotMatch(await page.locator('[data-step="tool-sp-b"]').innerText(), /MUST_NOT_DELIVER/);
    console.log('PASS wait rows and refused partial reports');

    // --- 4. Sidebar keeps spinning after the main turn while a child runs ---
    await page.evaluate((thread) => {
      fuseToolFixture.sidebar.update({ id: thread, title: 'Status fixture', updated_at: new Date().toISOString(), running: false });
    }, THREAD);
    ui = await read();
    assert.equal(ui.sidebarRunning, true, 'a live subagent keeps the thread row spinning after the turn');
    assert.equal(ui.sidebarBusy, 'true');
    agents = agents.map((row) => row.id === 'delta' || row.id === 'echo' ? { ...row, status: 'completed' } : row);
    await page.locator('.chat-agents-bar').click();
    await page.waitForFunction(() => ![...document.querySelectorAll('[data-agents-list] .chat-agents-state')].some((el) => /running|queued/.test(el.textContent)));
    ui = await read();
    assert.equal(ui.sidebarRunning, false, 'the row goes idle once every subagent settles');
    assert.notEqual(ui.sidebarBusy, 'true');
    console.log('PASS sidebar activity outlives the main turn');

    // --- 5. Resuming an interrupted child: dock, pane, held wake ---
    await page.locator('[data-agent-id="charlie"]').click();
    await page.locator('[data-agent-resume]').waitFor({ state: 'visible' });
    ui = await read();
    assert.equal(ui.badge, 'interrupted');
    assert.match(ui.badgeClass, /badge stop/);
    assert.equal(ui.resume, true);
    assert.equal(ui.stop, false);
    agents = agents.map((row) => row.id === 'charlie' ? { ...row, status: 'running', resumable: false, wake_state: '' } : row);
    bump('charlie', { status: 'running', resumable: false, message: { role: 'assistant', content: 'CHARLIE_RESUMED_WORK', tools: [{ id: 'c-read', name: 'read_file', status: 'running', path: 'NOTES.md' }] } });
    // The dock list refreshes on open, and the sidebar reads that list.
    await page.locator('[data-agents-close]').click();
    await page.locator('.chat-agents-bar').click();
    await page.waitForFunction(() => document.querySelector('[data-agent-id="charlie"]'));
    await page.evaluate(() => fuseToolFixture.todo.openAgent('charlie'));
    await page.locator('[data-agent-log-body]').getByText('CHARLIE_RESUMED_WORK').waitFor();
    ui = await read();
    assert.equal(ui.badge, 'running');
    assert.equal(ui.resume, false, 'Resume leaves once the child is live again');
    assert.equal(ui.stop, true);
    assert.equal(ui.dockHidden, false, 'a resumed child shows the live dock');
    assert.equal(ui.dockSpin, true, 'the dock spinner is mounted while it works');
    assert.equal(ui.sidebarRunning, true, 'resuming a child wakes the stopped thread\'s row');
    assert.doesNotMatch(ui.main, /CHARLIE_RESUMED_WORK/);
    agents = agents.map((row) => row.id === 'charlie' ? { ...row, status: 'completed', wake_state: 'held' } : row);
    bump('charlie', { status: 'completed', message: { role: 'assistant', content: 'CHARLIE_FINAL_REPORT' } });
    await page.evaluate(() => fuseToolFixture.pollAgent());
    await page.locator('[data-agent-status]').getByText('completed', { exact: true }).waitFor();
    await page.locator('[data-agent-back]').click();
    await page.locator('[data-agent-id="charlie"]').getByText('report waits for your next message').waitFor();
    ui = await read();
    assert.match(ui.rows.find((row) => row.id === 'charlie').text, /report waits for your next message/);
    assert.equal(ui.dockHidden, true, 'the dock spinner leaves when the resumed child finishes');
    assert.doesNotMatch(ui.main, /CHARLIE_FINAL_REPORT/, 'a held report is not painted into the main transcript');
    console.log('PASS resumed child wakes the row and holds its report');

    // --- 6. Background commands stay in their owner's strip ---
    jobs = [
      { id: 'job-alpha', command_id: 'job-alpha', agent_run_id: 'alpha', state: 'running', command: 'alpha-only-cmd', background: true, elapsed_ms: 1000 },
      { id: 'job-main', command_id: 'job-main', agent_run_id: '', state: 'running', command: 'main-only-cmd', background: true, elapsed_ms: 1000 },
      { id: 'job-delta', command_id: 'job-delta', agent_run_id: 'delta', state: 'completed', command: 'delta-only-cmd', background: true, elapsed_ms: 500 },
    ];
    await page.locator('[data-agent-id="alpha"]').click();
    await page.locator('[data-agent-jobs]').getByText('alpha-only-cmd').waitFor();
    ui = await read();
    assert.deepEqual(ui.paneJobs, ['job-alpha'], 'the open pane lists only its owner\'s command');
    assert.deepEqual(ui.mainJobs, ['job-main'], 'the thread strip lists only the main agent\'s command');
    assert.equal(ui.rows.find((row) => row.id === 'alpha').text.includes('1 command'), true);
    assert.equal(ui.rows.find((row) => row.id === 'bravo').text.includes('command'), false, 'a child with no jobs shows no command count');
    await page.locator('[data-agent-back]').click();
    await page.locator('[data-agent-jobs]').waitFor({ state: 'hidden' });
    ui = await read();
    assert.deepEqual(ui.paneJobs, [], 'leaving the pane clears its command strip');
    assert.deepEqual(ui.mainJobs, ['job-main']);
    console.log('PASS owner-scoped background commands');

    // --- 7. Compaction keeps the assignment handoff inside that pane ---
    const handoff = 'Assignment for INDIA only: finish the audit. Compaction is not completion.';
    run('india', {
      status: 'running', description: 'India worker', event_cursor: 3,
      messages: [
        { role: 'marker', kind: 'compaction', content: 'Context compacted', summary: handoff },
        { role: 'assistant', content: 'INDIA_KEPT_AFTER_HANDOFF', tools: [{ id: 'kept-read', name: 'read_file', status: 'done', path: 'KEEP.md', preview: 'kept line' }] },
      ],
      message: { role: 'assistant', content: 'INDIA_LIVE_AFTER_HANDOFF', tools: [] },
    });
    run('juliet', { status: 'completed', description: 'Juliet worker', message: { role: 'assistant', content: 'JULIET_REPORT_ONLY' } });
    agents = [
      { id: 'india', status: 'running', description: 'India worker', work_ms: 1000, work_live: true, created_at: now },
      { id: 'juliet', status: 'completed', description: 'Juliet worker', work_ms: 1000, created_at: now },
    ];
    await page.evaluate(() => fuseToolFixture.pollAgent());
    await page.locator('[data-agent-id="india"]').click();
    await page.locator('[data-agent-log-body] [data-compaction]').waitFor();
    ui = await read();
    assert.equal(ui.handoff, true, 'the compacted pane shows the handoff sent to the model');
    assert.match(ui.pane, /Assignment for INDIA only/);
    assert.match(ui.pane, /Compaction is not completion/);
    assert.match(ui.pane, /INDIA_KEPT_AFTER_HANDOFF/);
    assert.match(ui.pane, /INDIA_LIVE_AFTER_HANDOFF/);
    assert.doesNotMatch(ui.main, /Assignment for INDIA only|INDIA_LIVE_AFTER_HANDOFF/);
    await page.locator('[data-agent-back]').click();
    await page.locator('[data-agent-id="juliet"]').click();
    await page.locator('[data-agent-log-body]').getByText('JULIET_REPORT_ONLY', { exact: true }).waitFor();
    ui = await read();
    assert.equal(ui.handoff, false, 'a sibling pane does not inherit the compacted handoff');
    assert.doesNotMatch(ui.pane, /INDIA|Assignment for/);
    assert.doesNotMatch(ui.main, /JULIET_REPORT_ONLY/);
    console.log('PASS compaction handoff stays in the owning pane');

    assert.deepEqual(errors, []);
  } finally { await browser.close(); }
})().catch((error) => { console.error(error); process.exitCode = 1; });
