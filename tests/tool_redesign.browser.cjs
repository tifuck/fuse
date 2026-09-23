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

    for (const width of [1440, 390]) for (const view of ['cli', 'compact']) {
      await page.setViewportSize({ width, height: 1000 });
      await page.emulateMedia({ reducedMotion: 'reduce' });
      await page.evaluate(({ root, view }) => {
        const base = { cwd: root, project_root: root, host: 'workstation', platform: 'posix' };
        fuseToolFixture.view(view);
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
      }, { root, view });
      const group = page.locator('[data-step="group-tool-read-a"]');
      const heads = await page.locator('.chat-step:not(.is-think) > .chat-step-main > .chat-step-head').evaluateAll(nodes => nodes.filter(el => el.getBoundingClientRect().height).map(el => {
        const label = el.querySelector('.chat-step-label');
        const state = el.querySelector('.chat-step-state');
        const r = el.getBoundingClientRect(), s = state.getBoundingClientRect();
        return { height: r.height, row: parseFloat(getComputedStyle(label).lineHeight), stateY: s.y, top: r.y, bottom: r.bottom,
          font: getComputedStyle(label).fontFamily, size: getComputedStyle(label).fontSize, weight: getComputedStyle(label).fontWeight,
          color: getComputedStyle(label).color, background: getComputedStyle(label).backgroundImage,
          stateRight: s.right, whitespace: getComputedStyle(state).whiteSpace };
      }));
      assert.ok(heads.every(h => h.height <= h.row + 1), `all collapsed headers must be one dense line: ${JSON.stringify(heads)}`);
      assert.ok(heads.every(h => h.stateY >= h.top && h.stateY < h.bottom && h.whitespace === 'nowrap'));
      assert.equal(new Set(heads.map(h => h.font + h.size)).size, 1, 'group and standalone typography match');
      assert.equal(new Set(heads.map(h => h.weight + h.color + h.background)).size, 1, 'tool targets do not inherit running shimmer or different emphasis');
      assert.ok(Math.max(...heads.map(h => h.stateRight)) - Math.min(...heads.map(h => h.stateRight)) < 2, 'all top-level statuses share the right alignment');
      await page.locator('.chat-flow').screenshot({ path: `${out}/${view}-${width}-collapsed.png` });
      const toggle = group.locator(':scope > .chat-step-main > .chat-step-head > .chat-step-toggle');
      assert.match(await toggle.textContent(), /client\/config.ts, server\/config.ts/);
      await toggle.click();
      await group.getByRole('button', { name: 'Expand results', exact: true }).click();
      assert.equal(await group.locator('[data-tool="read_file"] .chat-step-toggle[aria-expanded="true"]').count(), 2);
      assert.ok((await group.innerText()).includes(root + '/src/client/config.ts'));
      await group.getByRole('button', { name: 'Collapse results', exact: true }).click();
      assert.equal(await group.locator('[data-tool="read_file"] .chat-step-toggle[aria-expanded="false"]').count(), 2);
      const shellGroup = page.locator('[data-step="group-tool-shell-a"]');
      await shellGroup.locator(':scope > .chat-step-main > .chat-step-head > .chat-step-toggle').click();
      const shell = page.locator('[data-step="tool-shell-a"]');
      assert.match(await shell.locator('.chat-tool-subject').innerText(), /npm run build/);
      assert.doesNotMatch(await shell.locator('.chat-tool-subject').innerText(), /cd |\/home/);
      await shell.locator('.chat-step-toggle').click();
      await shell.getByRole('button', { name: 'Copy command', exact: true }).click();
      assert.equal(await page.evaluate(() => navigator.clipboard.readText()), `cd '${root}' && npm run build`);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
      const flow = page.locator('.chat-flow');
      const height = Math.ceil((await flow.boundingBox()).height);
      await page.setViewportSize({ width, height: Math.max(1000, height + 340) });
      await flow.scrollIntoViewIfNeeded();
      await flow.screenshot({ path: `${out}/${view}-${width}.png` });
    }

    // Background-only work is discoverable even without a subagent. Both parent
    // references and commands originating in child transcripts expose output.
    await page.evaluate(() => {
      fuseToolFixture.mount([{ tools: [{ id: 'work-job', name: 'run_terminal_command', command_id: 'job-linked', status: 'running', state: 'running', background: true, command: 'npm test', output: 'Parent command output' }] }]);
      fuseToolFixture.work([{ id: 'job-linked', command_id: 'job-linked', state: 'running', command: 'npm test', output: 'Parent command output', background: true }]);
    });
    assert.match(await page.locator('.chat-agents-bar [data-agents-count]').textContent(), /1 command/);
    await page.locator('[data-show-job="job-linked"]').click();
    assert.equal(await page.locator('[data-step="tool-work-job"] .chat-step-toggle').getAttribute('aria-expanded'), 'true');
    await page.evaluate(() => fuseToolFixture.work([{ id: 'job-child', command_id: 'job-child', state: 'running', host: 'workstation', cwd: '/repo', command: 'npm run build', output: 'Child command output', background: true }]));
    await page.locator('[data-show-job="job-child"]').click();
    await page.getByText('Child command output', { exact: true }).waitFor();
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    await page.locator('[data-agents-close]').click();

    // Both directions animate, and a completion arriving during motion retains
    // disclosure/focus and paints the final state rather than dropping the update.
    for (const view of ['cli', 'compact']) {
      await page.emulateMedia({ reducedMotion: 'no-preference' });
      await page.evaluate(view => {
        fuseToolFixture.view(view);
        fuseToolFixture.mount([{ tools: [{ id: 'motion', name: 'run_terminal_command', status: 'running', command: 'npm test' }] }], true);
      }, view);
      const row = page.locator('[data-step="tool-motion"]');
      const toggle = row.locator('.chat-step-toggle');
      await toggle.click();
      assert.ok(await row.evaluate(el => el.getAnimations({ subtree: true }).some(a => a.effect.getKeyframes().some(k => k.height !== undefined))));
      await page.evaluate(() => fuseToolFixture.toolEvent({ event: 'tool', id: 'motion', name: 'run_terminal_command', status: 'done', state: 'completed', output: 'All tests passed', exit_code: 0 }));
      await page.waitForFunction(() => document.querySelector('[data-step="tool-motion"]').dataset.state === 'done');
      assert.equal(await toggle.getAttribute('aria-expanded'), 'true');
      assert.ok((await row.innerText()).includes('All tests passed'));
      await toggle.click();
      assert.ok(await row.evaluate(el => el.getAnimations({ subtree: true }).some(a => a.effect.getKeyframes().some(k => k.height !== undefined))));
      await page.waitForFunction(() => document.querySelector('[data-step="tool-motion"] .chat-step-reveal').getBoundingClientRect().height === 0);
    }
    assert.deepEqual(errors, []);
    console.log('PASS target formatting, full command copy, live enrichment, terminal precedence, group controls, responsive layouts, and two-way motion with concurrent completion');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
