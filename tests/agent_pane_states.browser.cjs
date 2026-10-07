// Chat-page RIGHT agent pane/dock: persistence and multi-agent states across
// navigation (panel close, model switch, reload, thread switch).
// Asserts latest server truth (mocked run/agent payloads), not pixels.
// Already covered elsewhere — do not re-pin: dock status labels/classes/dots,
// report delivery scope and "No completed report" (subagent_status.browser.cjs),
// open/disclosure/404+Retry (agent_ui.browser.cjs), the lifecycle matrix
// (subagent_lifecycle.browser.cjs), the child's live transcript and pane layout
// (subagent_pane_live.browser.cjs), parent-transcript subagent rows
// (console_subagent_rows.browser.cjs), owner command strips
// (subagent_status.browser.cjs), the bare count badge for commands
// (tool_redesign.browser.cjs).
const assert = require('node:assert/strict');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const origin = process.env.FUSE_BROWSER_ORIGIN || 'http://127.0.0.1:8099';
assert.match(origin, /^http:\/\/127\.0\.0\.1:\d+$/);

// Fresh unless a scenario pins quiet_s: a stale updated_at reads as stalled.
const NOW = new Date().toISOString();
const TATE_DESC = 'Tate worker auditing every caller with a very long description that must wrap inside the dock at phone width';
const runs = new Map();
const agentsByThread = {};
let THREAD_A = '';
let THREAD_B = '';

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

function agent(id, patch) {
  return {
    id, status: 'running', description: id, work_ms: 4000, work_live: true,
    created_at: NOW, resumable: false, has_transcript: true, ...patch,
  };
}

(async () => {
  const browser = await chromium.launch({ headless: true, executablePath: process.env.CHROMIUM_PATH || undefined, args: ['--no-sandbox'] });
  const errors = [];
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, reducedMotion: 'reduce' });
    page.on('pageerror', (e) => errors.push(e.message));
    await page.route('**/*', (route) => new URL(route.request().url()).origin === origin ? route.continue() : route.abort());
    await page.goto(origin);
    await page.locator('[data-sync-label]').filter({ hasText: 'Live sync' }).waitFor({ state: 'attached' });
    await page.waitForFunction(() => Boolean(window.fuseToolFixture));

    const spawnTools = (thread) => (agentsByThread[thread] || []).map((row) => ({
      id: 'sp-' + row.id, name: 'spawn_subagent', status: 'done', background: true,
      child_id: row.id, description: row.description,
    }));

    // The fixture config lists exactly one model, so the real model switch
    // (picker -> selectModel) has nothing to switch to. Serve an instrumented
    // chat.js that registers a second fixture model at boot. (Fulfilling the
    // document instead breaks the live-sync WebSocket in Chromium.)
    const src = await page.evaluate(() => fetch('/static/chat.js').then((r) => r.text()));
    const needle = '\n})();';
    assert.ok(src.includes(needle), 'fixture injection seam found in chat.js');
    const patched = src.replace(needle, "\nS.allModels.push({ id: 'offline-two', name: 'Offline fixture two', type: 'llama', title: 'Test', reasoning: ['none', 'low', 'medium', 'high'], reasoningDefault: 'medium' });\nS.modelsById.set('offline-two', S.allModels[S.allModels.length - 1]);\n" + needle);
    await page.route('**/static/chat.js*', (route) => route.fulfill({ status: 200, contentType: 'text/javascript', body: patched }));
    await page.route('**/chat/runs/*', (route) => {
      const id = decodeURIComponent(new URL(route.request().url()).pathname.split('/').pop());
      const row = runs.get(id);
      if (!row) return route.fulfill({ status: 404, json: { detail: 'missing ' + id } });
      // Serve fresh stamps: a stale updated_at reads as stalled (quiet >= 180s).
      const json = row.quiet_s == null ? { ...row, updated_at: new Date().toISOString() } : { ...row };
      return route.fulfill({ json });
    });
    await page.route('**/chat/threads/*/agents', (route) => {
      const tid = decodeURIComponent(new URL(route.request().url()).pathname.split('/').at(-2));
      const agents = (agentsByThread[tid] || []).map((row) => row.quiet_s == null ? { ...row, updated_at: new Date().toISOString() } : { ...row });
      return route.fulfill({ json: { agents } });
    });
    await page.route('**/chat/threads/*/jobs', (route) => route.fulfill({ json: { jobs: [], recent: [] } }));

    // Reload so chat.js boots with the second model registered.
    await page.reload();
    await page.locator('[data-sync-label]').filter({ hasText: 'Live sync' }).waitFor({ state: 'attached' });
    await page.waitForFunction(() => Boolean(window.fuseToolFixture));

    const createdA = await page.evaluate(async () => fuseToolFixture.api('/chat/threads', {
      method: 'POST', body: JSON.stringify({ model: 'offline', tools: [], command_id: crypto.randomUUID(), title: 'Pane states A' }),
    }));
    THREAD_A = createdA.id;
    const createdB = await page.evaluate(async () => fuseToolFixture.api('/chat/threads', {
      method: 'POST', body: JSON.stringify({ model: 'offline', tools: [], command_id: crypto.randomUUID(), title: 'Pane states B' }),
    }));
    THREAD_B = createdB.id;

    // Thread A: kilo/nov/tate run, que is freshly queued, rex hit the resume
    // cap, lima finished, mike died. Thread B owns oscar only.
    child('kilo', { thread_id: THREAD_A, description: 'Kilo worker', message: { role: 'assistant', content: 'KILO_DRAFT_ONE', tools: [
      { id: 'k-read', name: 'read_file', status: 'running', path: 'kilo-notes.md' },
    ] } });
    child('nov', { thread_id: THREAD_A, description: 'Nov worker', message: { role: 'assistant', content: 'NOV_STALLED_TEXT', tools: [
      { id: 'n-read', name: 'read_file', status: 'running', path: 'nov.md' },
    ] } });
    child('tate', { thread_id: THREAD_A, description: TATE_DESC, message: { role: 'assistant', content: 'TATE_DRAFT', tools: [] } });
    child('que', { thread_id: THREAD_A, description: 'Queued worker', status: 'queued', work_ms: 0, work_live: false });
    child('rex', { thread_id: THREAD_A, description: 'Rex worker', status: 'interrupted', work_live: false,
      resumable: true, has_transcript: true, resume_count: 3,
      message: { role: 'assistant', content: 'REX_INTERRUPTED_OUTPUT', tools: [] } });
    child('lima', { thread_id: THREAD_A, description: 'Lima worker', status: 'completed', work_live: false,
      message: { role: 'assistant', content: 'LIMA_REPORT_ONLY', tools: [] } });
    child('mike', { thread_id: THREAD_A, description: 'Mike worker', status: 'failed', work_live: false,
      message: { role: 'assistant', content: 'MIKE_PARTIAL_OUTPUT', error: 'provider 502', tools: [] } });
    child('oscar', { thread_id: THREAD_B, description: 'Oscar worker', message: { role: 'assistant', content: 'OSCAR_ONLY_TEXT', tools: [] } });

    agentsByThread[THREAD_A] = [
      agent('kilo', { description: 'Kilo worker' }),
      agent('nov', { description: 'Nov worker' }),
      agent('tate', { description: TATE_DESC }),
      agent('que', { description: 'Queued worker', status: 'queued', work_ms: 0, work_live: false }),
      agent('rex', { description: 'Rex worker', status: 'interrupted', work_live: false, resumable: true }),
      agent('lima', { description: 'Lima worker', status: 'completed', work_live: false }),
      agent('mike', { description: 'Mike worker', status: 'failed', work_live: false, error: 'provider 502' }),
    ];
    agentsByThread[THREAD_B] = [agent('oscar', { description: 'Oscar worker' })];
    const setAgent = (id, patch) => Object.assign(agentsByThread[THREAD_A].find((row) => row.id === id), patch);

    await page.evaluate(({ thread, tools }) => {
      fuseToolFixture.mount([{ tools }], false, thread);
      fuseToolFixture.sidebar.mount({ id: thread, title: 'Pane states A', updated_at: new Date().toISOString(), running: false });
    }, { thread: THREAD_A, tools: spawnTools(THREAD_A) });
    await page.evaluate((thread) => {
      fuseToolFixture.sidebar.update({ id: thread, title: 'Pane states B', updated_at: new Date().toISOString(), running: false });
    }, THREAD_B);

    const bar = page.locator('.chat-agents-bar');
    const paneText = () => page.locator('[data-agent-log-body]').innerText();
    const countEl = page.locator('.chat-agents-bar [data-agents-count]');
    const read = () => page.evaluate(() => {
      const flat = (el) => (el?.textContent || '').replace(/\s+/g, ' ').trim();
      const rows = [...document.querySelectorAll('[data-agents-list] [data-agent-id]')].map((el) => ({
        id: el.getAttribute('data-agent-id'),
        state: flat(el.querySelector('.chat-agents-state')),
        kicker: flat(el.previousElementSibling && el.previousElementSibling.matches('.chat-agents-kicker') ? el.previousElementSibling : null),
      }));
      const dock = document.querySelector('[data-agent-live-dock]');
      return {
        rows,
        title: flat(document.querySelector('[data-agent-title]')),
        badge: flat(document.querySelector('[data-agent-status]')),
        badgeClass: document.querySelector('[data-agent-status]')?.className || '',
        resume: Boolean(document.querySelector('[data-agent-resume]') && !document.querySelector('[data-agent-resume]').hidden),
        stop: Boolean(document.querySelector('[data-agent-stop]') && !document.querySelector('[data-agent-stop]').hidden),
        dockHidden: dock ? dock.hidden : null,
        dockVerb: flat(document.querySelector('[data-agent-live-label] .chat-status-verb')),
        spin: Boolean(document.querySelector('[data-agent-live-spin]')?.firstChild),
        text: flat(document.querySelector('[data-agent-log-body]')),
        steps: [...document.querySelectorAll('[data-agent-log-body] [data-step]')].map((el) => el.getAttribute('data-tool') || ''),
        articles: document.querySelector('[data-agent-log-body]')?.children.length || 0,
        errorVisible: Boolean(document.querySelector('[data-agent-error]') && !document.querySelector('[data-agent-error]').hidden),
        errorText: flat(document.querySelector('[data-agent-error]')),
        viewOpen: Boolean(document.querySelector('[data-agent-view]') && !document.querySelector('[data-agent-view]').hidden),
        overviewOpen: Boolean(document.querySelector('[data-agents-overview]') && !document.querySelector('[data-agents-overview]').hidden),
        panelText: flat(document.querySelector('[data-agents-list]')),
        count: flat(document.querySelector('.chat-agents-bar [data-agents-count]')),
        tabLabel: document.querySelector('.chat-agents-bar')?.getAttribute('aria-label') || '',
        liveLabel: flat(document.querySelector('[data-agents-live]')),
        liveHidden: Boolean(document.querySelector('[data-agents-live]')?.hidden),
        docks: document.querySelectorAll('[data-agent-live-dock]').length,
        panes: document.querySelectorAll('[data-agent-log-body]').length,
        modelBtn: flat(document.querySelector('[data-chat-picker] .chat-picker-btn')),
        overflow: document.scrollingElement.scrollWidth - window.innerWidth,
      };
    });
    const openPane = async (id) => {
      await page.locator(`[data-agent-id="${id}"]`).click();
      await page.locator('[data-agent-view]').waitFor({ state: 'visible' });
    };
    const poll = () => page.evaluate(() => fuseToolFixture.pollAgent());

    // --- 1. Multi-agent dock: counts, badges and entries appear/disappear ---
    await bar.click();
    await page.waitForFunction(() => document.querySelectorAll('[data-agents-list] [data-agent-id]').length === 7);
    let ui = await read();
    assert.equal(ui.count, '4', `badge counts live children (3 running + 1 queued): ${ui.count}`);
    assert.equal(ui.tabLabel, 'Agents and commands: 4 agents', `tab label names the work: ${ui.tabLabel}`);
    assert.equal(ui.liveLabel, '4 live', `live counter: ${ui.liveLabel}`);
    assert.equal(ui.rows.filter((row) => row.state === 'running').length, 3);
    assert.equal(ui.rows.find((row) => row.id === 'que').state, 'queued');
    assert.equal(ui.rows.find((row) => row.id === 'rex').state, 'interrupted');
    assert.equal(ui.rows.find((row) => row.id === 'lima').state, 'completed');
    assert.equal(ui.rows.find((row) => row.id === 'mike').state, 'failed');
    setAgent('nov', { status: 'completed', work_live: false });
    await page.waitForFunction(() => document.querySelector('.chat-agents-bar [data-agents-count]')?.textContent === '3');
    setAgent('que', { status: 'completed' });
    await page.waitForFunction(() => document.querySelector('.chat-agents-bar [data-agents-count]')?.textContent === '2');
    setAgent('tate', { status: 'completed', work_live: false });
    await page.waitForFunction(() => document.querySelector('.chat-agents-bar [data-agents-count]')?.textContent === '1');
    ui = await read();
    assert.equal(ui.tabLabel, 'Agents and commands: 1 agent', `the label stays singular for one: ${ui.tabLabel}`);
    assert.equal(ui.liveLabel, '1 live');
    setAgent('kilo', { status: 'completed', work_live: false });
    await page.waitForFunction(() => !document.querySelector('.chat-agents-bar [data-agents-count]')?.textContent);
    ui = await read();
    assert.equal(ui.count, '', 'the badge empties when nothing is live');
    assert.equal(ui.liveHidden, true, 'the live counter hides with no live child');
    assert.equal(ui.tabLabel, 'Agents and commands');
    assert.equal(ui.rows.filter((row) => row.state === 'running' || row.state === 'queued').length, 0, 'no row stays in the live states');
    // Entries come back when the children run again.
    setAgent('kilo', { status: 'running', work_live: true });
    setAgent('nov', { status: 'running', work_live: true });
    setAgent('tate', { status: 'running', work_live: true });
    setAgent('que', { status: 'queued' });
    await page.waitForFunction(() => document.querySelector('.chat-agents-bar [data-agents-count]')?.textContent === '4');
    console.log('PASS multi-agent dock counts and entries');

    // --- 2. Switching the open pane between children: no cross-delivery ---
    await openPane('kilo');
    await page.locator('[data-agent-log-body]').getByText('KILO_DRAFT_ONE').waitFor();
    ui = await read();
    assert.equal(ui.title, 'Kilo worker');
    assert.equal(ui.badge, 'running');
    assert.match(ui.badgeClass, /badge live/);
    assert.equal(ui.stop, true, 'a running child offers Stop');
    assert.equal(ui.resume, false, 'a running child offers no Resume');
    assert.equal(ui.dockHidden, false, 'the live dock shows while the child runs');
    assert.equal(ui.spin, true, 'the dock spinner is mounted while it works');
    assert.equal(ui.docks, 1, 'exactly one dock in the DOM');
    assert.equal(ui.panes, 1, 'exactly one pane body in the DOM');
    // Live updates for other children must not leak into the open pane.
    bump('lima', { message: { role: 'assistant', content: 'LIMA_REPORT_UPDATED', tools: [] } });
    bump('mike', { message: { role: 'assistant', content: 'MIKE_AFTER_BUMP', error: 'provider 502', tools: [] } });
    await poll();
    ui = await read();
    assert.equal(ui.title, 'Kilo worker', 'the open pane still shows its own child');
    assert.equal(ui.badge, 'running');
    assert.doesNotMatch(ui.text, /LIMA_REPORT|MIKE_AFTER_BUMP|MIKE_PARTIAL/, `no sibling content in kilo's pane: ${ui.text.slice(0, 200)}`);
    await page.locator('[data-agent-back]').click();
    await openPane('mike');
    await page.locator('[data-agent-log-body]').getByText('MIKE_AFTER_BUMP').waitFor();
    ui = await read();
    assert.equal(ui.title, 'Mike worker');
    assert.equal(ui.badge, 'failed', 'the pane shows the terminal state, not eternal running');
    assert.match(ui.badgeClass, /badge fault/);
    assert.match(ui.text, /provider 502/, `the failure surfaces its error: ${ui.text.slice(0, 200)}`);
    assert.equal(ui.dockHidden, true, 'a failed child shows no live dock');
    assert.equal(ui.spin, false, 'no spinner is left behind for a failed child');
    assert.equal(ui.stop, false);
    assert.doesNotMatch(ui.text, /LIMA_REPORT|KILO_DRAFT/, `mike keeps only its own content: ${ui.text.slice(0, 200)}`);
    // While mike is open, kilo finishes — mike's pane must not change.
    bump('kilo', {
      status: 'completed', work_live: false,
      message: { role: 'assistant', content: 'KILO_FINAL_REPORT', tools: [
        { id: 'k-read', name: 'read_file', status: 'done', path: 'kilo-notes.md', output: 'kilo line\n' },
      ] },
    });
    setAgent('kilo', { status: 'completed', work_live: false });
    await poll();
    ui = await read();
    assert.equal(ui.title, 'Mike worker', 'a sibling finishing does not steal the open pane');
    assert.equal(ui.badge, 'failed');
    assert.doesNotMatch(ui.text, /KILO_FINAL_REPORT|KILO_DRAFT/, `no cross-delivery into mike's pane: ${ui.text.slice(0, 200)}`);
    await page.locator('[data-agent-back]').click();
    await openPane('kilo');
    await page.locator('[data-agent-log-body]').getByText('KILO_FINAL_REPORT').waitFor();
    ui = await read();
    assert.equal(ui.badge, 'completed');
    assert.match(ui.badgeClass, /badge on/);
    assert.equal(ui.dockHidden, true, 'the dock leaves when the child finishes');
    assert.equal(ui.spin, false);
    assert.doesNotMatch(ui.text, /MIKE_AFTER_BUMP|LIMA_REPORT/, 'kilo kept only its own content');
    assert.equal(ui.steps.filter((tool) => tool === 'read_file').length, 1, 'a settled call does not duplicate its row');
    assert.doesNotMatch(ui.text, /Running…|Working/, 'no stale running mark after the child settles');
    console.log('PASS pane switching keeps each child\'s content in its own pane');

    // --- 3. A stalled child reads as stalled and settles without a stuck spinner ---
    runs.get('nov').quiet_s = 300;
    setAgent('nov', { quiet_s: 300 });
    await page.locator('[data-agent-back]').click();
    await openPane('nov');
    await page.waitForFunction(() => document.querySelector('[data-agent-status]')?.textContent === 'stalled');
    ui = await read();
    assert.equal(ui.badge, 'stalled', 'a quiet child reads as stalled, not running');
    assert.match(ui.badgeClass, /badge fault/);
    assert.equal(ui.dockHidden, false, 'the dock stays to report the stall');
    assert.match(ui.dockVerb, /stalled/i, `the dock says why: ${ui.dockVerb}`);
    assert.equal(ui.spin, false, 'a stalled child shows no spinner');
    assert.equal(ui.resume, false);
    assert.equal(ui.stop, true);
    assert.match(ui.text, /NOV_STALLED_TEXT/);
    await page.waitForFunction(() => document.querySelector('[data-agent-id="nov"] .chat-agents-state')?.textContent === 'stalled');
    ui = await read();
    assert.equal(ui.rows.find((row) => row.id === 'nov').state, 'stalled', 'the dock list agrees about the stall');
    // The child settles: every running/stalled mark must clear at once.
    bump('nov', {
      status: 'completed', work_live: false, quiet_s: 0,
      message: { role: 'assistant', content: 'NOV_FINAL_REPORT', tools: [
        { id: 'n-read', name: 'read_file', status: 'done', path: 'nov.md', output: 'nov line\n' },
      ] },
    });
    setAgent('nov', { status: 'completed', work_live: false, quiet_s: 0 });
    await poll();
    await page.locator('[data-agent-status]').getByText('completed', { exact: true }).waitFor();
    ui = await read();
    assert.equal(ui.badge, 'completed');
    assert.match(ui.badgeClass, /badge on/);
    assert.equal(ui.dockHidden, true, 'the dock hides once the stalled child settles');
    assert.equal(ui.spin, false, 'no spinner is stuck after the child settles');
    assert.match(ui.text, /NOV_FINAL_REPORT/, 'the settled transcript keeps its report');
    console.log('PASS stalled child shows the stalled state and settles cleanly');

    // --- 4. Resume affordances: hidden while live and at the resume cap ---
    await page.locator('[data-agent-back]').click();
    await openPane('rex');
    await page.locator('[data-agent-status]').getByText('interrupted', { exact: true }).waitFor();
    ui = await read();
    assert.equal(ui.badge, 'interrupted');
    assert.match(ui.badgeClass, /badge stop/);
    assert.equal(ui.resume, false, 'a child at the resume cap offers no Resume');
    assert.equal(ui.stop, false);
    assert.match(ui.text, /REX_INTERRUPTED_OUTPUT/);
    console.log('PASS resume control hidden while live and at the resume cap');

    // --- 5. Pane loading/error states: an empty transcript vs an unavailable one ---
    setAgent('que', { status: 'queued' });
    await page.locator('[data-agent-back]').click();
    await openPane('que');
    await page.waitForFunction(() => {
      const badge = document.querySelector('[data-agent-status]')?.textContent || '';
      return badge && badge !== 'loading';
    });
    ui = await read();
    assert.equal(ui.badge, 'queued', `the badge settles instead of sticking at loading: ${ui.badge}`);
    assert.equal(ui.errorVisible, false, 'an empty transcript is not an error');
    assert.equal(ui.articles, 1, `one (empty) bubble, not a leftover transcript: ${ui.articles}`);
    assert.equal(ui.steps.length, 0, 'no stale tool rows from the previously open pane');
    assert.doesNotMatch(ui.text, /KILO|MIKE|LIMA|NOV|TATE|REX|read_file/, `no leftover content from the previously open pane: ${JSON.stringify(ui.text)}`);
    // textContent also carries hidden chrome (the "0 sources" citations badge);
    // the visible transcript of an empty child is empty.
    assert.equal((await paneText()).trim(), '', 'an empty child renders an empty pane');
    // A pane that becomes unavailable mid-poll surfaces the error and stops.
    const limaRow = runs.get('lima');
    await page.locator('[data-agent-back]').click();
    await openPane('lima');
    await page.locator('[data-agent-log-body]').getByText('LIMA_REPORT_UPDATED').waitFor();
    runs.delete('lima');
    for (let i = 0; i < 3; i++) await poll();
    await page.locator('[data-agent-error]').waitFor({ state: 'visible' });
    ui = await read();
    assert.match(ui.badge, /unavailable/i, `the pane reports the broken fetch: ${ui.badge}`);
    assert.match(ui.badgeClass, /badge fault/);
    assert.match(ui.errorText, /Could not open subagent/, `the error is surfaced: ${ui.errorText}`);
    assert.equal(ui.dockHidden, true, 'an unavailable pane shows no live dock');
    assert.equal(ui.spin, false);
    assert.equal(ui.resume, false);
    assert.equal(ui.stop, false);
    runs.set('lima', limaRow);
    await poll();
    await page.locator('[data-agent-error]').waitFor({ state: 'hidden' });
    ui = await read();
    assert.equal(ui.badge, 'completed', `recovers when the transcript returns: ${ui.badge}`);
    assert.match(ui.text, /LIMA_REPORT_UPDATED/, 'the transcript comes back with the recovery');
    console.log('PASS empty and unavailable pane states');

    // --- 6. Persistence: panel close, model switch, reload ---
    // (a) Closing the panel keeps the child; reopening re-attaches to it.
    await page.locator('[data-agents-close]').click();
    await page.locator('[data-chat-agents]').waitFor({ state: 'hidden' });
    await bar.click();
    await page.locator('[data-chat-agents]').waitFor({ state: 'visible' });
    ui = await read();
    assert.equal(ui.viewOpen, true, 'reopening the panel returns to the open child, not the list');
    assert.equal(ui.overviewOpen, false, 'no orphaned list pane behind the child');
    assert.equal(ui.title, 'Lima worker');
    assert.match(ui.text, /LIMA_REPORT_UPDATED/);
    assert.equal(ui.badge, 'completed');
    assert.equal(ui.docks, 1, 'no duplicated dock after a close/reopen cycle');
    assert.equal(ui.panes, 1);
    // (b) Open the running Tate child; the switch checks below assert its state.
    await page.locator('[data-agent-back]').click();
    await openPane('tate');
    await page.locator('[data-agent-log-body]').getByText('TATE_DRAFT').waitFor();
    // (c) A model switch leaves the pane exactly where it was.
    await page.locator('[data-chat-picker] .chat-picker-btn').click();
    await page.locator('.chat-picker-menu').waitFor({ state: 'visible' });
    assert.equal(await page.locator('.chat-picker-list button[data-id]').count(), 2, 'the picker offers the second fixture model');
    await page.locator('.chat-picker-list button[data-id="offline-two"]').click();
    await page.waitForFunction(() => (document.querySelector('[data-chat-picker] .chat-picker-btn')?.textContent || '').includes('Offline fixture two'));
    ui = await read();
    assert.equal(ui.viewOpen, true, 'the pane survives a model switch');
    assert.match(ui.title, /^Tate worker/, 'the model switch does not reset the pane');
    assert.equal(ui.badge, 'running');
    assert.equal(ui.dockHidden, false, 'the dock re-attaches after the switch');
    assert.equal(ui.spin, true);
    assert.match(ui.text, /TATE_DRAFT/);
    assert.equal(ui.docks, 1);
    assert.equal(ui.panes, 1);
    // (d) Reload: no orphaned pane, then the dock and content re-attach.
    await page.reload();
    await page.locator('[data-sync-label]').filter({ hasText: 'Live sync' }).waitFor({ state: 'attached' });
    await page.waitForFunction(() => Boolean(window.fuseToolFixture));
    await page.evaluate(({ thread, tools, other }) => {
      fuseToolFixture.mount([{ tools }], false, thread);
      fuseToolFixture.sidebar.mount({ id: thread, title: 'Pane states A', updated_at: new Date().toISOString(), running: false });
      fuseToolFixture.sidebar.update({ id: other, title: 'Pane states B', updated_at: new Date().toISOString(), running: false });
    }, { thread: THREAD_A, other: THREAD_B, tools: spawnTools(THREAD_A) });
    await bar.click();
    await page.waitForFunction(() => document.querySelectorAll('[data-agents-list] [data-agent-id]').length === 7);
    ui = await read();
    assert.equal(ui.viewOpen, false, 'after a reload nothing is left open by itself');
    assert.equal(ui.overviewOpen, true, 'the reload lands on the agents list, not a stale pane');
    assert.equal(ui.docks, 1);
    assert.equal(ui.panes, 1);
    await openPane('tate');
    await page.locator('[data-agent-log-body]').getByText('TATE_DRAFT').waitFor();
    ui = await read();
    assert.match(ui.title, /^Tate worker/, 'the pane re-attaches to its child after a reload');
    assert.equal(ui.badge, 'running', 'a live child is live again after the reload');
    assert.equal(ui.dockHidden, false, 'the dock re-attaches after the reload');
    assert.equal(ui.spin, true, 'the spinner is back exactly once');
    assert.equal((await paneText()).split('TATE_DRAFT').length - 1, 1, 'no duplicated content after the reload');
    console.log('PASS pane survives panel close, a model switch, and reload');

    // --- 7. Thread switch: the pane resets cleanly, never orphans, never bleeds ---
    const openRow = async (id) => {
      const row = page.locator(`[data-thread="${id}"]`);
      if (!await row.isVisible()) await page.locator('.chat-bar [data-history-toggle]').click();
      await row.locator('button.open').click();
      await page.waitForFunction((tid) => document.querySelector(`[data-thread="${tid}"]`)?.classList.contains('is-on'), id);
    };
    await openRow(THREAD_B);
    await page.waitForFunction(() => {
      const rows = [...document.querySelectorAll('[data-agents-list] [data-agent-id]')];
      return rows.length === 1 && rows[0].getAttribute('data-agent-id') === 'oscar';
    });
    ui = await read();
    assert.equal(ui.viewOpen, false, 'switching threads closes the pane instead of orphaning it');
    assert.equal(ui.overviewOpen, true, 'the panel lands on the new thread\'s list');
    assert.equal(ui.count, '1', `the badge counts the new thread's live child: ${ui.count}`);
    assert.doesNotMatch(ui.panelText, /Kilo|Tate|Lima|Mike|Nov|Rex|Queued|TATE_DRAFT|KILO/, `thread B's dock lists only its own children: ${ui.panelText.slice(0, 200)}`);
    await openPane('oscar');
    await page.locator('[data-agent-log-body]').getByText('OSCAR_ONLY_TEXT').waitFor();
    ui = await read();
    assert.equal(ui.title, 'Oscar worker');
    assert.equal(ui.badge, 'running');
    assert.doesNotMatch(ui.text, /TATE_DRAFT|KILO|MIKE|LIMA/, 'no cross-thread content in the pane');
    await openRow(THREAD_A);
    await page.waitForFunction(() => document.querySelectorAll('[data-agents-list] [data-agent-id]').length === 7);
    ui = await read();
    assert.equal(ui.viewOpen, false, 'coming back resets the pane again — no stale view of the other thread');
    assert.doesNotMatch(ui.panelText, /Oscar|OSCAR/, `thread A's dock lists only its own children: ${ui.panelText.slice(0, 200)}`);
    await openPane('tate');
    await page.locator('[data-agent-log-body]').getByText('TATE_DRAFT').waitFor();
    ui = await read();
    assert.match(ui.title, /^Tate worker/, 'the round trip re-attaches to the right child');
    assert.equal(ui.badge, 'running');
    assert.equal((await paneText()).split('TATE_DRAFT').length - 1, 1, 'the round trip neither loses nor duplicates content');
    console.log('PASS thread switch resets the pane without orphans or cross-delivery');

    // --- 8. The dock list fits a phone: 390px, no overflow, rows inside ---
    await page.locator('[data-agent-back]').click();
    await page.locator('[data-agents-list]').waitFor({ state: 'visible' });
    await page.setViewportSize({ width: 390, height: 844 });
    await page.locator('[data-chat-agents]').waitFor({ state: 'visible' });
    ui = await read();
    assert.ok(ui.overflow <= 0, `390: the agents list does not widen the page (${ui.overflow}px)`);
    const rowsFit = await page.evaluate(() => [...document.querySelectorAll('[data-agents-list] [data-agent-id]')].every((el) => {
      const r = el.getBoundingClientRect();
      return r.left >= -1 && r.right <= window.innerWidth + 1;
    }));
    assert.equal(rowsFit, true, '390: every dock row stays inside the viewport');
    console.log('PASS agent list layout at 390');

    assert.deepEqual(errors, []);
  } finally { await browser.close(); }
})().catch((error) => { console.error(error); process.exitCode = 1; });
