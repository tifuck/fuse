// Parallel calls of different kinds finishing out of order, then a late or
// duplicate event, a snapshot racing the live stream, a reload, and a view
// switch. Pins that Console and Mainframe show the latest state after each
// of those, and that a settled row never reverts to running.
// Run against chat_browser_fixture.py, never the live Fuse service.
const assert = require('node:assert/strict');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const origin = process.env.FUSE_BROWSER_ORIGIN || 'http://127.0.0.1:8099';
assert.match(origin, /^http:\/\/127\.0\.0\.1:\d+$/);

const FILE = '1: def timeout_for(name):\n2:     return 30\n';
const GREP = 'app/retry.py:12: timeout_for(name)\napp/retry.py:40: timeout_for(other)\n';
const SHELL = 'collected 2 items\n..\n2 passed';
const HITS = [
  { title: 'timeout_for docs', url: 'https://example.com/timeout', snippet: 'how the helper works' },
  { title: 'retry notes', url: 'https://example.com/retry', snippet: 'second hit' },
];

const READ = { id: 'read', name: 'read_file', path: 'app/retry.py', offset: 1, limit: 2 };
const GREP_CALL = { id: 'grep', name: 'grep', pattern: 'timeout_for', path: 'app' };
const BASH = { id: 'bash', name: 'run_terminal_command', command: 'python -m pytest -q tests/retry.py', command_id: 'job-retry', child_id: 'job-retry' };
const SEARCH = { id: 'search', name: 'web_search', queries: ['timeout_for helper'] };
const AGENT = { id: 'agent', name: 'spawn_subagent', description: 'Audit the retry helper', child_id: 'child-retry', background: true, summary: 'Background task started' };
const NOTE = { id: 'note', name: 'scratch', preview: '{"slots":{"brief":240}}' };
const CALLS = [READ, GREP_CALL, BASH, SEARCH, AGENT, NOTE];

const running = (call) => ({ ...call, status: 'running', results: [] });
const segments = () => CALLS.map((call) => ({ type: 'tool', id: call.id })).concat([{ type: 'content', text: 'Done.' }]);
const thread = (tools, extra = {}) => [
  { role: 'user', content: 'Check the retry helper.' },
  { id: 'turn', role: 'assistant', content: 'Done.', tools, segments: segments(), ...extra },
];

let checks = 0;
const check = (value, message) => { assert.ok(value, message); checks++; };

function readDom() {
  const flat = (node) => (node ? node.innerText : '').replace(/[ \t]+/g, ' ').replace(/\s*\n\s*/g, ' ').trim();
  const row = (id) => document.querySelector(`[data-tool][data-execution="${id}"], [data-step="tool-${id}"]`);
  const rows = {};
  for (const id of ['read', 'grep', 'bash', 'search', 'agent', 'note']) {
    const el = row(id);
    rows[id] = el ? { state: el.dataset.state || '', text: flat(el), n: document.querySelectorAll(`[data-step="tool-${id}"]`).length } : null;
  }
  const groups = [...document.querySelectorAll('.is-console-group > .chat-step-main > .chat-step-head .chat-step-label')]
    .map((el) => flat(el));
  const log = document.querySelector('.chat-log');
  return {
    rows,
    groups,
    calls: document.querySelectorAll('.chat-log [data-tool]').length,
    dock: document.querySelector('[data-live-dock]')?.hidden ? '' : flat(document.querySelector('[data-live-dock-label]')),
    status: flat(document.querySelector('[data-live-status] [data-live-label]')),
    spins: document.querySelectorAll('[data-activity-spin]:not(:empty), .chat-activity-spin:not(:empty)').length,
    text: flat(log),
    open: [...document.querySelectorAll('[data-step-toggle][aria-expanded="true"]')].map((el) => el.closest('[data-step]')?.dataset.step),
    overflow: document.scrollingElement.scrollWidth - document.scrollingElement.clientWidth,
  };
}

(async () => {
  const browser = await chromium.launch({ headless: true, executablePath: process.env.CHROMIUM_PATH || undefined, args: ['--no-sandbox'] });
  const errors = [];
  try {
    for (const width of [1440, 390]) {
      const page = await browser.newPage({ viewport: { width, height: width === 390 ? 844 : 1000 }, reducedMotion: 'reduce' });
      page.on('pageerror', (error) => errors.push(`${width}: ${error.message}`));
      await page.route('**/*', (route) => new URL(route.request().url()).origin === origin ? route.continue() : route.abort());
      await page.goto(origin);
      await page.locator('[data-sync-label]').filter({ hasText: 'Live sync' }).waitFor({ state: 'attached' });
      await page.waitForFunction(() => window.fuseToolFixture);
      await page.addStyleTag({ content: '* { animation: none !important; transition: none !important; }' });
      const tag = `@${width}`;
      const dom = () => page.evaluate(readDom);
      const event = (payload) => page.evaluate((body) => fuseToolFixture.toolEvent(body), payload);
      const waitState = (id, state) => page.waitForFunction(([rowId, want]) => {
        const el = document.querySelector(`[data-step="tool-${rowId}"]`);
        return Boolean(el && el.dataset.state === want);
      }, [id, state]);

      await page.evaluate((name) => fuseToolFixture.view(name), 'console');
      await page.evaluate((rows) => fuseToolFixture.mountThread(rows, { live: true, phase: 'tools' }), thread(CALLS.map(running)));
      let seen = await dom();
      check(seen.calls === 6, `${tag} six parallel calls render once, saw ${seen.calls}`);
      for (const id of ['read', 'grep', 'bash', 'search', 'agent', 'note']) {
        check(seen.rows[id]?.state === 'running', `${tag} ${id} starts running, saw ${JSON.stringify(seen.rows[id])}`);
      }
      check(!/read \d+ files|searched for|ran \d+ shell/.test(seen.text), `${tag} nothing has folded while every call still runs: ${seen.text.slice(0, 240)}`);
      // The status line counts every live execution, and says how many of them
      // were handed off to the background.
      // The counters live in the dock's meta, which is hidden at 390px.
      check(width === 390 || /6 active \/ 1 background/.test(seen.dock), `${tag} the dock counts all six live calls: ${seen.dock}`);

      // They finish in a different order than they started.
      const finishes = [
        { ...GREP_CALL, status: 'done', output: GREP, preview: GREP },
        { ...SEARCH, status: 'done', results: HITS, latency_ms: 840 },
        { ...BASH, status: 'done', state: 'completed', execution_status: 'completed', exit_code: 0, output: SHELL, termination_confirmed: true },
        { ...NOTE, status: 'done', preview: '{"slots":{"brief":240}}' },
        { ...AGENT, status: 'done', summary: 'The helper returns 30.', agent_work_ms: 4200, agent_tool_uses: 3 },
        { ...READ, status: 'done', output: FILE, preview: 'Read 2 lines' },
      ];
      for (const finish of finishes) {
        await event({ event: 'tool', ...finish });
        await waitState(finish.id, 'done');
        seen = await dom();
        check(seen.rows[finish.id].state === 'done', `${tag} ${finish.id} settles to done, saw ${seen.rows[finish.id].state}`);
        check(seen.rows[finish.id].n === 1, `${tag} ${finish.id} stays one row after it finishes, saw ${seen.rows[finish.id].n}`);
        const still = finishes.length - finishes.indexOf(finish) - 1;
        const want = still > 1 ? new RegExp(`${still} active`) : null;
        check(width === 390 || !want || want.test(seen.dock), `${tag} the dock drops to ${still} active after ${finish.id} finishes: ${seen.dock}`);
        for (const other of finishes) {
          if (other === finish) break;
          check(seen.rows[other.id].state === 'done', `${tag} ${other.id} stays done while ${finish.id} finishes, saw ${seen.rows[other.id].state}`);
        }
      }
      seen = await dom();
      check(seen.rows.grep.text.includes('Found 2 lines'), `${tag} the grep row counts its matches: ${seen.rows.grep.text}`);
      check(seen.rows.read.text.includes('Read 2 lines'), `${tag} the read row counts its lines: ${seen.rows.read.text}`);
      check(seen.rows.bash.text.includes('2 passed'), `${tag} the shell row shows its output: ${seen.rows.bash.text}`);
      check(seen.rows.search.text.includes('Did 1 search'), `${tag} the search row counts its query: ${seen.rows.search.text}`);
      check(/read 1 file/i.test(seen.text) && /searched for 1 pattern/i.test(seen.text) && /ran 1 shell command/i.test(seen.text),
        `${tag} the summary line counts every finished kind: ${seen.groups.join(' | ')}`);
      check(seen.overflow <= 0, `${tag} the settled transcript does not overflow sideways by ${seen.overflow}px`);

      // A replayed "running" for a call that already finished must not revert it.
      await event({ event: 'tool', ...running(READ) });
      await event({ event: 'tool', ...READ, status: 'done', output: FILE });
      seen = await dom();
      check(seen.rows.read.state === 'done', `${tag} a late running event leaves the read done, saw ${seen.rows.read.state}`);
      check(seen.rows.read.n === 1, `${tag} the replay does not add a second read row, saw ${seen.rows.read.n}`);
      check(!/Running…/.test(seen.rows.read.text), `${tag} the settled read does not say it is running: ${seen.rows.read.text}`);

      // The same finish delivered twice keeps one row and the same values.
      const before = seen.text;
      await event({ event: 'tool', ...BASH, status: 'done', state: 'completed', execution_status: 'completed', exit_code: 0, output: SHELL, termination_confirmed: true });
      seen = await dom();
      check(seen.rows.bash.n === 1 && seen.rows.bash.state === 'done', `${tag} a duplicate finish keeps one done shell row: ${JSON.stringify(seen.rows.bash)}`);
      check((seen.rows.bash.text.match(/2 passed/g) || []).length === 1, `${tag} the shell output is not doubled: ${seen.rows.bash.text}`);
      check(seen.text === before, `${tag} a duplicate finish does not change the transcript`);

      // A checkpoint that still says the read is running must not undo the finish.
      const stale = await page.evaluate(() => {
        const live = fuseToolFixture.snapshot();
        const tools = live[1].tools.map((trace) => trace.id === 'read' ? { ...trace, status: 'running', output: '' } : { ...trace });
        fuseToolFixture.progress(1, { tools });
        return document.querySelector('[data-step="tool-read"]')?.dataset.state || '';
      });
      check(stale === 'done', `${tag} an older snapshot cannot revive the finished read, saw ${stale}`);

      // The persisted snapshot, mounted as a reload, paints the same rows.
      const live = seen;
      const saved = await page.evaluate(() => fuseToolFixture.snapshot());
      await page.evaluate((rows) => {
        const next = structuredClone(rows);
        next[1].tools = next[1].tools.map((trace) => fuseToolFixture.serializeTrace(trace));
        fuseToolFixture.mountThread(next);
        fuseToolFixture.run({ status: 'completed' });
      }, saved);
      await page.waitForFunction(() => document.querySelector('[data-live-dock]')?.hidden === true);
      const reloaded = await dom();
      for (const id of ['read', 'grep', 'bash', 'search', 'agent', 'note']) {
        check(reloaded.rows[id]?.state === live.rows[id].state, `${tag} reload keeps ${id} ${live.rows[id].state}, saw ${reloaded.rows[id]?.state}`);
        check(reloaded.rows[id].text === live.rows[id].text, `${tag} reload keeps ${id}'s text\n live: ${live.rows[id].text}\n reload: ${reloaded.rows[id]?.text}`);
      }
      check(reloaded.groups.join('\n') === live.groups.join('\n'), `${tag} reload keeps the summary line\n live: ${live.groups.join(' | ')}\n reload: ${reloaded.groups.join(' | ')}`);
      check(!reloaded.dock, `${tag} the dock is gone once the turn has ended, saw ${reloaded.dock}`);

      // Open one summary, then switch views mid-turn and come back.
      await page.evaluate((rows) => fuseToolFixture.mountThread(rows, { live: true, phase: 'tools' }), thread(CALLS.map(running)));
      await event({ event: 'tool', ...GREP_CALL, status: 'done', output: GREP });
      await event({ event: 'tool', ...BASH, status: 'done', state: 'completed', execution_status: 'completed', exit_code: 0, output: SHELL, termination_confirmed: true });
      await page.evaluate(() => { document.querySelector('[data-chat]')?.classList.remove('is-history'); });
      const summary = page.locator('.is-console-group > .chat-step-main > .chat-step-head > .chat-step-toggle');
      await summary.click();
      await page.waitForFunction(() => document.querySelector('.is-console-group')?.classList.contains('is-console-expanded'));
      const opened = await dom();
      check(opened.open.length === 1, `${tag} one summary is open, saw ${opened.open.join(',')}`);
      check(opened.rows.bash.state === 'done' && opened.rows.read.state === 'running', `${tag} the open summary shows the finished shell beside the still-running read`);

      for (const view of ['mainframe', 'cli', 'console']) {
        await page.evaluate((name) => fuseToolFixture.view(name), view);
        await page.waitForFunction((name) => document.querySelector('[data-chat]')?.getAttribute('data-view') === name, view);
        const switched = await dom();
        check(switched.rows.bash?.state === 'done', `${tag} ${view} keeps the shell done, saw ${switched.rows.bash?.state}`);
        check(switched.rows.read?.state === 'running', `${tag} ${view} keeps the read running, saw ${switched.rows.read?.state}`);
        check(/Found 2 lines|timeout_for/.test(switched.rows.grep?.text || ''), `${tag} ${view} still shows the grep result: ${switched.rows.grep?.text}`);
        if (view === 'cli') {
          const bash = page.locator('[data-step="tool-bash"]');
          const toggle = bash.locator(':scope > .chat-step-main > .chat-step-head > [data-step-toggle]').locator('visible=true').first();
          if (await toggle.getAttribute('aria-expanded') !== 'true') await toggle.click();
          await bash.locator('.chat-step-reveal.is-open').waitFor();
          const body = await bash.locator('.chat-step-reveal').innerText();
          check((body.match(/2 passed/g) || []).length === 1, `${tag} cli shows the shell output once: ${body}`);
        } else {
          check((switched.rows.bash.text.match(/2 passed/g) || []).length === 1, `${tag} ${view} shows the shell output once: ${switched.rows.bash.text}`);
        }
        const openNow = await page.evaluate(() => {
          const msg = fuseToolFixture.snapshot().find((row) => row.role === 'assistant');
          const group = document.querySelector('.is-console-group');
          return { map: msg && msg.stepOpen || {}, key: group?.dataset.step || '', expanded: Boolean(group?.classList.contains('is-console-expanded')) };
        });
        if (view === 'mainframe') {
          // FINDING(view-switch-folds): the summary opened in Console keeps its
          // stepOpen flag, but Mainframe rebuilds the group under a different
          // key, so the row paints collapsed.
          check(openNow.map[opened.open[0]] === true, `${tag} mainframe keeps the open flag for ${opened.open[0]}: ${JSON.stringify(openNow.map)}`);
          check(!openNow.expanded && openNow.key !== opened.open[0], `${tag} mainframe paints it collapsed under ${openNow.key}`);
        } else if (view === 'console') {
          check(switched.open.includes(opened.open[0]), `${tag} console keeps the opened summary (${switched.open.join(',')})`);
        }
        check(switched.overflow <= 0, `${tag} ${view} does not overflow sideways by ${switched.overflow}px`);
      }
      const back = await dom();
      check(back.groups.join('\n') === opened.groups.join('\n'), `${tag} returning to console restores the summary line\n before: ${opened.groups.join(' | ')}\n after: ${back.groups.join(' | ')}`);
      console.log(`PASS reconcile ${tag} (${checks} checks)`);
      await page.close();
    }
    assert.deepEqual(errors, []);
  } finally { await browser.close(); }
})().catch((error) => { console.error(error); process.exitCode = 1; });
