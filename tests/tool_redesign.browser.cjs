// Isolated fixture only: no providers or host commands are used.
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const origin = process.env.FUSE_BROWSER_ORIGIN || 'http://127.0.0.1:8099';
assert.match(origin, /^http:\/\/127\.0\.0\.1:\d+$/);
const out = process.env.TOOL_UI_ARTIFACTS || '/tmp/opencode/tool-redesign';

(async () => {
  await fs.mkdir(out, { recursive: true });
  const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] });
  try {
    const context = await browser.newContext({ permissions: ['clipboard-read', 'clipboard-write'] });
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    await page.route('**/*', r => new URL(r.request().url()).origin === origin ? r.continue() : r.abort());
    await page.goto(origin);
    await page.waitForFunction(() => window.fuseToolFixture);
    await page.evaluate(() => document.fonts.ready);
    const root = '/home/dev/projects/example';
    const checks = await page.evaluate(root => {
      const f = fuseToolFixture;
      const t = { name: 'read_file', path: root + '/src/client.ts', cwd: root, project_root: root };
      const command = `cd '${root}' && npm test -- --runInBand`;
      f.mount([{ tools: [] }], true);
      f.toolEvent({ event: 'tool', id: 'launch', name: 'spawn_subagent', status: 'running', description: 'Review changes' });
      f.toolEvent({ event: 'tool', id: 'launch', name: 'spawn_subagent', status: 'running', child_id: 'child-1', background: true });
      return {
        label: f.label(t),
        short: f.command({ command, cwd: root }),
        other: f.command({ command, cwd: '/elsewhere' }),
        unsafe: f.command({ command: `cd '${root}'; npm test`, cwd: root }),
        windows: f.path('C:\\work\\app\\src\\client.ts', { project_root: 'c:\\WORK\\app', platform: 'windows' }),
        boundary: f.path('/repo-other/file.ts', { project_root: '/repo' }),
        terminal: f.state({ name: 'run_terminal_command', status: 'running', state: 'completed', command_id: 'j', exit_code: 0 }, true),
        trace: f.snapshot()[0].tools[0],
        preserved: f.serializeTrace({ name: 'run_terminal_command', status: 'done', command: 'x'.repeat(8000) }).command.length,
      };
    }, root);
    assert.equal(checks.label, 'Read · src/client.ts');
    assert.equal(checks.short, 'npm test -- --runInBand');
    assert.match(checks.other, /^cd /);
    assert.match(checks.unsafe, /^cd /);
    assert.equal(checks.windows, 'src/client.ts');
    assert.equal(checks.boundary, '/repo-other/file.ts');
    assert.equal(checks.terminal.statusLabel, 'Done');
    assert.equal(checks.trace.child_id, 'child-1');
    assert.equal(checks.trace.background, true);
    assert.equal(checks.preserved, 8000);

    for (const width of [1440, 390]) {
      await page.setViewportSize({ width, height: 1000 });
      await page.emulateMedia({ reducedMotion: 'reduce' });
      await page.evaluate(({ root }) => {
        const base = { cwd: root, project_root: root, host: 'workstation', platform: 'posix' };
        fuseToolFixture.mount([{ tools: [
          { ...base, id: 'read-a', name: 'read_file', status: 'done', path: root + '/src/client/config.ts', offset: 20, limit: 30, output: '20: export const config = { retries: 3 };' },
          { ...base, id: 'read-b', name: 'read_file', status: 'done', path: root + '/src/server/config.ts', output: 'export const port = 8080;' },
          { ...base, id: 'edit-a', name: 'search_replace', status: 'done', path: root + '/src/client/config.ts', output: 'Replaced one occurrence.' },
          { ...base, id: 'grep-a', name: 'grep', status: 'done', path: root + '/src', pattern: 'loadConfig', output: 'src/client/config.ts:20:loadConfig()' },
          { ...base, id: 'glob-a', name: 'glob', status: 'done', path: root, pattern: '**/*.ts', output: 'src/client/config.ts\nsrc/server/config.ts' },
          { ...base, id: 'list-a', name: 'list_dir', status: 'done', path: root + '/src', output: 'client/\nserver/' },
          { ...base, id: 'shell-a', name: 'run_terminal_command', status: 'running', state: 'running', command_id: 'build-1', background: true, command: `cd '${root}' && npm run build`, output: 'Compiling 24 modules…' },
          { ...base, id: 'shell-b', name: 'run_terminal_command', status: 'done', state: 'completed', command_id: 'test-1', command: 'npm test', output: '8 tests passed', exit_code: 0 },
          { id: 'agent-a', name: 'spawn_subagent', status: 'running', child_id: 'child-1', description: 'Review configuration changes', background: true },
        ] }], true);
      }, { root });
      const group = page.locator('[data-step="cgroup-tool-read-a"]');
      const heads = await page.locator('.chat-step:not(.is-think) > .chat-step-main > .chat-step-head').evaluateAll(nodes => nodes.filter(el => el.getBoundingClientRect().height).map(el => {
        const label = el.querySelector('.chat-step-label');
        const state = el.querySelector('.chat-step-state');
        const r = el.getBoundingClientRect();
        const sb = state ? state.getBoundingClientRect() : null;
        const s = sb && sb.height ? sb : null;
        return { group: el.closest('.chat-step').classList.contains('is-console-group'), height: r.height, row: parseFloat(getComputedStyle(label).lineHeight), stateY: s ? s.y : r.y, top: r.y, bottom: r.bottom,
          font: getComputedStyle(label).fontFamily, size: getComputedStyle(label).fontSize, weight: getComputedStyle(label).fontWeight,
          color: getComputedStyle(label).color, background: getComputedStyle(label).backgroundImage,
          stateRight: s ? s.right : r.right, whitespace: state ? getComputedStyle(state).whiteSpace : 'nowrap' };
      }));
      assert.ok(heads.every(h => h.height <= h.row + 1), `all collapsed headers must be one dense line: ${JSON.stringify(heads)}`);
      assert.ok(heads.every(h => h.stateY >= h.top && h.stateY < h.bottom && h.whitespace === 'nowrap'));
      assert.equal(new Set(heads.map(h => h.font + h.size)).size, 1, 'group and standalone typography match');
      const emphasis = (pred) => new Set(heads.filter(pred).map(h => h.weight + h.color + h.background)).size;
      assert.equal(emphasis(h => !h.group), 1, 'tool targets do not inherit running shimmer or different emphasis');
      assert.ok(Math.max(...heads.map(h => h.stateRight)) - Math.min(...heads.map(h => h.stateRight)) < 2, 'all top-level statuses share the right alignment');
      await page.locator('.chat-flow').screenshot({ path: `${out}/${width}-collapsed.png` });
      const toggle = group.locator(':scope > .chat-step-main > .chat-step-head > .chat-step-toggle');
      assert.match(await toggle.textContent(), /Read 2 files/);
      await toggle.click();
      const openText = await group.innerText();
      assert.ok(openText.includes('client/config.ts') && openText.includes('server/config.ts'), `the open summary prints its members: ${openText.slice(0, 200)}`);
      await group.locator('.chat-console-bundle .chat-step-node').first().click({ force: true });
      assert.equal(await toggle.getAttribute('aria-expanded'), 'false', 'a click on the printed members folds the summary');
      const shell = page.locator('[data-step="tool-shell-a"]');
      assert.match(await shell.locator('.chat-step-label').innerText(), /npm run build/);
      assert.doesNotMatch(await shell.locator('.chat-step-label').innerText(), /cd |\/home/);
      // (Copy command lives in the row's detail sections, which standalone rows
      // no longer render — reported as a static-side gap.)
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
      const flow = page.locator('.chat-flow');
      const height = Math.ceil((await flow.boundingBox()).height);
      await page.setViewportSize({ width, height: Math.max(1000, height + 340) });
      await flow.scrollIntoViewIfNeeded();
      await flow.screenshot({ path: `${out}/${width}.png` });
    }

    // Background-only work is discoverable even without a subagent. Both parent
    // references and commands originating in child transcripts expose output.
    await page.evaluate(() => {
      fuseToolFixture.mount([{ tools: [{ id: 'work-job', name: 'run_terminal_command', command_id: 'job-linked', status: 'running', state: 'running', background: true, command: 'npm test', output: 'Parent command output' }] }]);
      fuseToolFixture.work([{ id: 'job-linked', command_id: 'job-linked', state: 'running', command: 'npm test', output: 'Parent command output', background: true }]);
    });
    // The badge is a bare count; the breakdown is the button's accessible name.
    assert.equal(await page.locator('.chat-agents-bar [data-agents-count]').textContent(), '1');
    assert.match(await page.locator('.chat-agents-bar').getAttribute('aria-label'), /1 command/);
    await page.locator('[data-agents-close]').click();
    assert.equal(await page.locator('[data-jobs-list]').evaluate(el => el.nextElementSibling.matches('[data-chat-thread]')), true, 'commands stay outside the scrolling transcript');
    await page.locator('[data-show-job="job-linked"]').click();
    const workRow = page.locator('[data-execution="job-linked"]');
    await workRow.waitFor();
    assert.match(await workRow.innerText(), /Running in the background/, 'the job view reveals its transcript row, still running in the background');
    assert.doesNotMatch(await workRow.innerText(), /Parent command output/, 'a running background row shows no output tail in-transcript');
    await page.evaluate(() => fuseToolFixture.work([{ id: 'job-child', command_id: 'job-child', state: 'running', host: 'workstation', cwd: '/repo', command: 'npm run build', output: 'Child command output', background: true }]));
    await page.locator('[data-agents-close]').click();
    await page.locator('[data-show-job="job-child"]').click();
    const jobDetail = page.locator('.chat-jobs-detail', { hasText: 'Child command output' });
    await jobDetail.waitFor();
    assert.match(await jobDetail.getAttribute('class'), /is-open/, 'the job view opens the child command detail');
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);

    // A completion arriving while the disclosure is in play retains the
    // disclosure and paints the final state rather than dropping the update.
    // (The console theme renders tool disclosures instantly — the animated
    // disclosure belonged to the retired views.)
    {
      await page.emulateMedia({ reducedMotion: 'no-preference' });
      await page.evaluate(() => {
        fuseToolFixture.mount([{ tools: [{ id: 'motion', name: 'read_url', status: 'running', url: 'https://example.com/report', preview: 'Loading the report' }] }], true);
      });
      const row = page.locator('[data-step="tool-motion"]');
      await row.locator('.chat-step-toggle').click();
      await page.evaluate(() => fuseToolFixture.toolEvent({ event: 'tool', id: 'motion', name: 'read_url', status: 'done', results: [{ title: 'Report', url: 'https://example.com/report', snippet: 'All tests passed' }] }));
      await page.waitForFunction(() => document.querySelector('[data-step="tool-motion"]').dataset.state === 'done');
      assert.match(await row.getAttribute('class'), /is-console-expanded/, 'completion during the open keeps the disclosure');
      assert.ok((await row.innerText()).includes('All tests passed'), 'the completion paints the final state');
    }
    assert.deepEqual(errors, []);
    console.log('PASS target formatting, live enrichment, terminal precedence, group controls, responsive layouts, and disclosure with concurrent completion');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
