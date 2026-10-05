// Agent work clocks: the Console agents group row clock
// (data-cc-clock="work", static/chat.js ccAgentsGroupHtml/syncWorkClocks) and
// the dock's row time (agentDurationSeconds). Pins the intended invariant for
// a running child's displayed work time: monotonic non-decreasing, never above
// server work_ms plus one poll interval, no '1s' clamp after showing more,
// still ticking after the parent turn ends, sane across a mid-run reload, and
// frozen to the final duration at settle.
//
// Already covered elsewhere — do not re-pin: the pane clock ticker
// (subagent_pane_live.browser.cjs), group row content and settled
// finished/failed/stopped labels (console_subagent_rows.browser.cjs), dock
// status labels, dots and badges (subagent_status.browser.cjs).
const assert = require('node:assert/strict');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const origin = process.env.FUSE_BROWSER_ORIGIN || 'http://127.0.0.1:8099';
assert.match(origin, /^http:\/\/127\.0\.0\.1:\d+$/);

// ccDuration and formatActivityClock both print "<n>d/h/m/s" tokens:
// "0s", "45s", "1m 13s", "2h 7m 5s", "1d 2h 3m".
function parseDur(text) {
  let secs = 0;
  let matched = false;
  for (const m of String(text || '').matchAll(/(\d+)\s*([dhms])/g)) {
    matched = true;
    secs += Number(m[1]) * { d: 86400, h: 3600, m: 60, s: 1 }[m[2]];
  }
  return matched ? secs : null;
}

const NOW = new Date().toISOString();
let seq = 0;
const spawn = (fields) => ({
  id: `sp${++seq}`, name: 'spawn_subagent', status: 'running', error: '', results: [],
  description: 'Audit the pool', preview: 'Audit the pool', subagent_type: 'general-purpose',
  background: true, summary: 'Background task started', ...fields,
});
const agentRow = (id, patch) => ({
  id, status: 'running', description: 'Audit the pool', created_at: NOW,
  work_ms: 20000, work_live: true, ...patch,
});

(async () => {
  const browser = await chromium.launch({ headless: true, executablePath: process.env.CHROMIUM_PATH || undefined, args: ['--no-sandbox'] });
  const errors = [];
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, reducedMotion: 'reduce' });
    page.on('pageerror', (e) => errors.push(e.message));
    let agentsPayload = [];
    let agentGets = 0;
    await page.route('**/*', (route) => new URL(route.request().url()).origin === origin ? route.continue() : route.abort());
    await page.route('**/chat/runs/*', (route) => route.fulfill({ json: {
      id: decodeURIComponent(new URL(route.request().url()).pathname.split('/').pop()),
      event_cursor: 1, message_id: 'x-msg', thread_id: '', description: 'x', status: 'running',
      created_at: NOW, updated_at: NOW, message: { role: 'assistant', content: '', tools: [] }, messages: [],
    } }));
    await page.route('**/chat/threads/*/agents', (route) => {
      agentGets += 1;
      return route.fulfill({ json: { agents: agentsPayload } });
    });
    await page.route('**/chat/threads/*/jobs', (route) => route.fulfill({ json: { jobs: [], recent: [] } }));
    await page.goto(origin);
    await page.waitForFunction(() => Boolean(window.fuseToolFixture));

    const groupClocks = () => page.evaluate(() => {
      const out = {};
      for (const li of document.querySelectorAll('li.chat-console-agent[data-open-agent]')) {
        const el = li.querySelector('[data-cc-clock="work"]');
        out[li.getAttribute('data-open-agent')] = el ? el.textContent.trim() : '';
      }
      return out;
    });
    const groupNames = () => page.evaluate(() => Object.fromEntries(
      [...document.querySelectorAll('li.chat-console-agent[data-open-agent]')].map((li) => [
        li.getAttribute('data-open-agent'),
        (li.querySelector('.chat-console-agent-name')?.innerText || '').replace(/\s+/g, ' ').trim(),
      ])));
    const mountGroup = (a, b) => page.evaluate(({ a, b }) => {
      fuseToolFixture.view('console');
      fuseToolFixture.mount([{ tools: [a, b] }], false);
    }, { a, b });
    // One agents poll/reconcile (the exact call refreshAgents makes) plus a
    // settle long enough for the 250ms clock tick to recompute the text.
    const reconcile = async (rows) => {
      await page.evaluate((r) => fuseToolFixture.agents(r), rows);
      await page.waitForTimeout(400);
    };
    const waitRow = (sel) => page.waitForFunction((s) => Boolean(document.querySelector(s)), sel, { timeout: 4000 });

    // --- 1. Ticking with no streaming parent: monotone across two poll
    // cycles and one full repaint, bounded by server work_ms + slack ---
    const a1 = spawn({ id: 'sp-a1', child_id: 'a1', description: 'Index the docs' });
    const a2 = spawn({ id: 'sp-a2', child_id: 'a2', description: 'Ship the docs' });
    await mountGroup(a1, a2);
    await waitRow('[data-step="agents-a1"]');
    await reconcile([agentRow('a1', { work_ms: 20000 }), agentRow('a2', { work_ms: 20000 })]);
    const steady = [];
    for (let i = 0; i < 11; i += 1) {
      steady.push(await groupClocks());
      // Two /agents poll cycles with an unchanged base must not disturb the
      // clock (the pair is matched, so a no-op reconcile repaints nothing),
      // and a full repaint re-uses the article without rewinding the text.
      if (i === 4 || i === 7) await page.evaluate((rows) => fuseToolFixture.agents(rows),
        [agentRow('a1', { work_ms: 20000 }), agentRow('a2', { work_ms: 20000 })]);
      if (i === 9) await page.evaluate(() => fuseToolFixture.repaint());
      await page.waitForTimeout(250);
    }
    for (const id of ['a1', 'a2']) {
      const secs = steady.map((s) => parseDur(s[id]));
      assert.ok(secs.every((v) => v != null), `${id} clock always parses | ${JSON.stringify(steady)}`);
      for (let i = 1; i < secs.length; i += 1) {
        assert.ok(secs[i] >= secs[i - 1], `${id} clock never decreases at ${i} | ${JSON.stringify(secs)}`);
      }
      assert.ok(secs[secs.length - 1] - secs[0] >= 1.5,
        `${id} keeps ticking after the parent turn ended | ${JSON.stringify(secs)}`);
      assert.ok(Math.max(...secs) <= 20 + 5,
        `${id} never exceeds work_ms + one poll + slack | ${JSON.stringify(secs)}`);
    }
    console.log('PASS running clocks tick monotonically with no streaming parent');

    // --- 2. A poll that advances work_ms must not drop the clock ---
    const b1 = spawn({ id: 'sp-b1', child_id: 'b1', description: 'Index the docs' });
    const b2 = spawn({ id: 'sp-b2', child_id: 'b2', description: 'Ship the docs' });
    await mountGroup(b1, b2);
    await waitRow('[data-step="agents-b1"]');
    await reconcile([agentRow('b1', { work_ms: 20000 }), agentRow('b2', { work_ms: 20000 })]);
    const pre = await groupClocks();
    await reconcile([agentRow('b1', { work_ms: 60000 }), agentRow('b2', { work_ms: 60000 })]);
    const post = await groupClocks();
    // FINDING(work-clock-drop): intended the clock re-anchors to server
    // work_ms (~1m) and never decreases; actually each reconcile shifts
    // _workSnap forward by (work_ms - stale agent_work_ms) without re-basing
    // agent_work_ms, so the display plummets from ~20s to the 1s clamp (the
    // user's "DROP by ~20 seconds" and "one second") and never recovers.
    for (const id of ['b1', 'b2']) {
      assert.equal(post[id], '1s', `${id} clamps to 1s after the work_ms jump | pre ${JSON.stringify(pre)} post ${JSON.stringify(post)}`);
      assert.ok(parseDur(pre[id]) - parseDur(post[id]) >= 15,
        `${id} drops by ~20s at the poll | pre ${JSON.stringify(pre)} post ${JSON.stringify(post)}`);
    }
    // A re-delivery of the same payload shifts the snap again (it is not
    // idempotent), so the clamp holds instead of the clock recovering.
    await reconcile([agentRow('b1', { work_ms: 60000 }), agentRow('b2', { work_ms: 60000 })]);
    const again = await groupClocks();
    assert.equal(again.b1, '1s', `re-delivery keeps the clamp | ${JSON.stringify(again)}`);
    assert.equal(again.b2, '1s', `re-delivery keeps the clamp | ${JSON.stringify(again)}`);
    console.log('PASS pinned poll drop and 1s clamp (FINDING work-clock-drop)');

    // --- 3. Mid-run reload: the clock must resume to server truth ---
    const r1 = spawn({ id: 'sp-r1', child_id: 'r1', description: 'Index the docs' });
    const r2 = spawn({ id: 'sp-r2', child_id: 'r2', description: 'Ship the docs' });
    await mountGroup(r1, r2);
    await waitRow('[data-step="agents-r1"]');
    await reconcile([agentRow('r1', { work_ms: 20000 }), agentRow('r2', { work_ms: 20000 })]);
    const persisted = await page.evaluate(() => fuseToolFixture.snapshot()[0].tools.map((t) => fuseToolFixture.serializeTrace(t)));
    await page.evaluate((tools) => {
      fuseToolFixture.mountThread([
        { role: 'user', content: 'Run both audits.' },
        { role: 'assistant', content: 'Both audits are running.', tools, elapsedMs: 200000, endedAt: new Date().toISOString() },
      ]);
    }, persisted);
    await waitRow('[data-step="agents-r1"]');
    // FINDING(reload-freeze): intended the clock keeps ticking while the
    // child runs; actually a trace rebuilt from a persisted snapshot carries
    // agent_work_ms without _workSnap, renders data-since=0 and freezes at
    // the stale base until the next poll.
    const frozen1 = await groupClocks();
    await page.waitForTimeout(1200);
    const frozen2 = await groupClocks();
    for (const id of ['r1', 'r2']) {
      assert.equal(frozen1[id], '20s', `${id} reload renders the stale base | ${JSON.stringify(frozen1)}`);
      assert.equal(frozen2[id], '20s', `${id} clock is frozen after reload | ${JSON.stringify([frozen1, frozen2])}`);
    }
    // The first post-reload poll must resume within one poll interval of
    // server truth (work_ms moved on to 50s while the page was away).
    await reconcile([agentRow('r1', { work_ms: 50000 }), agentRow('r2', { work_ms: 50000 })]);
    const resumed = await groupClocks();
    // FINDING(reload-clamp): intended the clock resumes at ~50s; actually the
    // first poll stamps _workSnap in the future (now + (work - staleBase)),
    // so the display goes negative and clamps to '1s' for many seconds.
    for (const id of ['r1', 'r2']) {
      assert.equal(resumed[id], '1s', `${id} first poll resets the clock to 1s | ${JSON.stringify(resumed)}`);
    }
    await page.waitForTimeout(1500);
    const held = await groupClocks();
    for (const id of ['r1', 'r2']) {
      assert.equal(held[id], '1s', `${id} clamped 1s is a multi-second freeze | ${JSON.stringify(held)}`);
    }
    console.log('PASS pinned reload freeze and 1s reset (FINDING reload-freeze, reload-clamp)');

    // --- 4. A work_live flap between legs must not overshoot server truth ---
    const f1 = spawn({ id: 'sp-f1', child_id: 'f1', description: 'Index the docs' });
    const f2 = spawn({ id: 'sp-f2', child_id: 'f2', description: 'Ship the docs' });
    await mountGroup(f1, f2);
    await waitRow('[data-step="agents-f1"]');
    await reconcile([agentRow('f1', { work_ms: 20000 }), agentRow('f2', { work_ms: 20000 })]);
    await page.waitForTimeout(9000);
    const ticked = await groupClocks();
    for (const id of ['f1', 'f2']) {
      assert.ok(parseDur(ticked[id]) >= 25, `${id} keeps ticking while it runs | ${JSON.stringify(ticked)}`);
    }
    // Leg boundary: the server pauses active work (work_live false) and
    // credits 2s at the leg end. The fresh poll must re-anchor the clock.
    await reconcile([agentRow('f1', { work_ms: 22000, work_live: false }), agentRow('f2', { work_ms: 22000, work_live: false })]);
    const over = await groupClocks();
    // FINDING(flap-overshoot): intended the clock re-anchors to work_ms
    // (≤ 22s + one poll + slack ≈ 25s); actually the work_live false branch
    // re-bases agent_work_ms but keeps the old _workSnap, so the clock keeps
    // ticking from the stale snap and overshoots by its age (~9s).
    for (const id of ['f1', 'f2']) {
      assert.ok(parseDur(over[id]) >= 27, `${id} overshoots work_ms after the flap | ${JSON.stringify(over)}`);
    }
    console.log('PASS pinned flap overshoot (FINDING flap-overshoot)');

    // --- 5. Dock clocks (agents list) across real /agents polls ---
    const created = await page.evaluate(async () => fuseToolFixture.api('/chat/threads', {
      method: 'POST', body: JSON.stringify({ model: 'offline', tools: [], command_id: crypto.randomUUID(), title: 'Clock fixture' }),
    }));
    const THREAD = created.id;
    agentsPayload = [
      agentRow('d1', { work_ms: 30000, description: 'Index the docs' }),
      agentRow('d2', { work_ms: 15000, description: 'Ship the docs' }),
    ];
    await page.evaluate((thread) => {
      fuseToolFixture.view('console');
      fuseToolFixture.mount([{ tools: [] }], false, thread);
      fuseToolFixture.sidebar.mount({ id: thread, title: 'Clock fixture', updated_at: new Date().toISOString(), running: false });
    }, THREAD);
    await page.locator('.chat-agents-bar').click();
    await page.waitForFunction(() => document.querySelectorAll('[data-agents-list] [data-agent-id]').length === 2);
    const dockTimes = () => page.evaluate(() => {
      const out = {};
      for (const btn of document.querySelectorAll('[data-agents-list] [data-agent-id]')) {
        out[btn.getAttribute('data-agent-id')] = (btn.querySelector('.chat-agents-time')?.textContent || '').trim();
      }
      return out;
    });
    const dockGets0 = agentGets;
    const dock = [];
    for (let i = 0; i < 26; i += 1) {
      dock.push(await dockTimes());
      await page.waitForTimeout(250);
    }
    assert.ok(agentGets - dockGets0 >= 2, `the dock polls /agents while open (${agentGets - dockGets0} polls)`);
    for (const id of ['d1', 'd2']) {
      const secs = dock.map((s) => parseDur(s[id]));
      assert.ok(secs.every((v) => v != null && v >= 14), `${id} dock time always parses | ${JSON.stringify(dock)}`);
      assert.ok(Math.max(...secs) <= (id === 'd1' ? 30 : 15) + 5, `${id} dock time stays within work_ms + poll + slack | ${JSON.stringify(secs)}`);
      const drops = secs.slice(1).map((v, i) => secs[i] - v).filter((d) => d > 0);
      // FINDING(dock-sawtooth): intended the dock row time is monotonic while
      // the child runs; actually every /agents poll re-stamps row._workAt
      // (chat.js refreshAgents), so the time sawtooths back down to work_ms
      // at each poll whenever the server's work_ms lags wall time.
      assert.ok(drops.some((d) => d >= 0.7), `${id} dock time drops at each poll | drops ${JSON.stringify(drops)} samples ${JSON.stringify(secs)}`);
    }
    console.log('PASS pinned dock sawtooth (FINDING dock-sawtooth)');

    // --- 6. Settle: the line freezes to the final duration and stops ---
    const s1 = spawn({ id: 'sp-s1', child_id: 's1', description: 'Index the docs' });
    const s2 = spawn({ id: 'sp-s2', child_id: 's2', description: 'Ship the docs' });
    await mountGroup(s1, s2);
    await waitRow('[data-step="agents-s1"]');
    await reconcile([agentRow('s1', { work_ms: 21000 }), agentRow('s2', { work_ms: 24000 })]);
    assert.equal((await groupClocks()).s1, '21s', 'the running clock shows the server base');
    await reconcile([
      agentRow('s1', { status: 'completed', work_live: false, work_ms: 23000, message: { role: 'assistant', content: 'REPORT_S1' } }),
      agentRow('s2', { status: 'failed', work_live: false, work_ms: 24000, error: 'provider down' }),
    ]);
    const settled = await groupNames();
    assert.match(settled.s1, /finished · 23s$/, `the finished line shows the final duration | ${JSON.stringify(settled)}`);
    assert.match(settled.s2, /failed$/, `a failed line keeps no ticking clock | ${JSON.stringify(settled)}`);
    const afterSettle = await groupClocks();
    assert.deepEqual(afterSettle, { s1: '', s2: '' }, `no work clock survives settle | ${JSON.stringify(afterSettle)}`);
    const settledText = settled;
    await page.waitForTimeout(1200);
    assert.deepEqual(await groupNames(), settledText, 'a settled line does not tick');
    await reconcile([
      agentRow('s1', { status: 'completed', work_live: false, work_ms: 23000, message: { role: 'assistant', content: 'REPORT_S1' } }),
      agentRow('s2', { status: 'failed', work_live: false, work_ms: 24000, error: 'provider down' }),
    ]);
    await page.evaluate(() => fuseToolFixture.repaint());
    assert.deepEqual(await groupNames(), settledText, 're-reconcile and repaint freeze the settle value');
    console.log('PASS settle freezes to the final duration');

    assert.deepEqual(errors, []);
  } finally { await browser.close(); }
})().catch((error) => { console.error(error); process.exitCode = 1; });
