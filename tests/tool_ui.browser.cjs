// Run against chat_browser_fixture.py, never the live Fuse service.
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const origin = process.env.FUSE_BROWSER_ORIGIN || 'http://127.0.0.1:8099';
assert.match(origin, /^http:\/\/127\.0\.0\.1:\d+$/);
const out = process.env.TOOL_UI_ARTIFACTS || '/tmp/opencode/tool-ui-artifacts';
const baseline = process.env.TOOL_UI_BASELINE;
const PNG = baseline ? require(process.env.PNGJS_MODULE || 'pngjs').PNG : null;
const visualFailures = [];
const names = ['read_url', 'web_search', 'search', 'create_artifact', 'update_artifact', 'read_file', 'list_dir', 'grep', 'glob',
  'write_file', 'search_replace', 'run_terminal_command', 'get_command_output', 'kill_command', 'spawn_subagent',
  'get_subagent_output', 'kill_subagent', 'list_subagents', 'search_models', 'search_tools', 'load_skill', 'propose_skill', 'todo_write', 'unknown_tool'];
const samples = {
  read_url: { url: 'https://example.com/documentation', preview: 'Documentation page content.' },
  web_search: { queries: ['layout consistency', 'tool lifecycle'], results: [{ title: 'Tool lifecycle guide', url: 'https://example.com/guide', snippet: 'A source with a short excerpt.' }] },
  search: { query: 'layout consistency' },
  create_artifact: { title: 'Sales dashboard', identifier: 'fixture-artifact' },
  update_artifact: { title: 'Sales dashboard', identifier: 'fixture-artifact' },
  run_terminal_command: { command: 'npm run build', exit_code: 0, preview: 'Build completed in 1.2s.' },
  get_command_output: { command: 'npm run build', child_id: 'fixture-job' },
  spawn_subagent: { description: 'Inspect authentication', child_id: 'fixture-agent', model: 'grok-4.6' },
  get_subagent_output: { description: 'Inspect authentication', child_id: 'fixture-agent' },
  search_models: { query: 'grok', preview: '2 models' },
  grep: { pattern: 'render.*Tool', path: 'static/chat.js' }, glob: { pattern: '**/*.js' }, list_dir: { path: 'static/' },
};
const trace = (name, status = 'done', extra = {}) => ({ id: 'tool-a', name, status, path: ['read_file', 'write_file', 'search_replace'].includes(name) ? 'src/components/tool-view.js' : undefined,
  preview: 'Representative tool output.\nSecond output line.', ...samples[name], ...(status === 'error' ? { error: 'The operation could not complete. Check the target and try again.' } : {}), ...extra });
const manifest = [];
const failures = [];
let assertions = 0;

(async () => {
  await fs.mkdir(out, { recursive: true });
  if (baseline) assert.notEqual(path.resolve(baseline), path.resolve(out), 'Baseline and output directories must differ');
  const browser = await chromium.launch({ headless: true, executablePath: process.env.CHROMIUM_PATH || undefined, args: ['--no-sandbox'] });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, reducedMotion: 'reduce' });
    page.on('pageerror', error => failures.push(error.message));
    await page.route('**/*', route => new URL(route.request().url()).origin === origin ? route.continue() : route.abort());
    await page.goto(origin);
    await page.locator('[data-sync-label]').filter({ hasText: 'Live sync' }).waitFor({ state: 'attached' });
    await page.waitForFunction(() => Boolean(window.fuseToolFixture));
    await page.evaluate(() => document.fonts.ready);
    await page.addStyleTag({ content: '* { animation: none !important; transition: none !important; caret-color: transparent !important; }' });
    const mount = (tools, live = false) => page.evaluate(({ tools, live }) => fuseToolFixture.mount([{ tools }], live), { tools, live });
    const row = id => page.locator(`[data-step="tool-${id}"]`);
    const toggle = r => r.locator(':scope > .chat-step-main > .chat-step-head > .chat-step-toggle');
    const body = r => r.locator(':scope > .chat-step-main > .chat-step-reveal');
    const state = r => r.locator(':scope > .chat-step-main > .chat-step-head > .chat-step-state');
    const capture = async (name, r, expected) => {
      const viewport = page.viewportSize();
      const height = Math.ceil((await r.boundingBox()).height);
      // Component crops taller than the viewport otherwise contain sticky app
      // chrome and clipped ancestors. Keep the tested width, temporarily add
      // vertical room, then return to the actual mobile/desktop test viewport.
      const captureHeight = Math.max(viewport.height, height + 320);
      if (captureHeight !== viewport.height) await page.setViewportSize({ ...viewport, height: captureHeight });
      await r.scrollIntoViewIfNeeded();
      const file = `${name}.png`;
      const image = await r.screenshot({ path: path.join(out, file), animations: 'disabled' });
      if (captureHeight !== viewport.height) await page.setViewportSize(viewport);
      const entry = { case: name, expected, screenshot: file, viewport, captureHeight };
      manifest.push(entry);
      if (baseline) {
        try {
          const before = PNG.sync.read(await fs.readFile(path.join(baseline, file)));
          const after = PNG.sync.read(image);
          assert.equal(after.width, before.width, 'Screenshot width changed');
          assert.equal(after.height, before.height, 'Screenshot height changed');
          let changed = 0;
          for (let i = 0; i < after.data.length; i += 4) {
            const different = [0, 1, 2, 3].some(channel => Math.abs(after.data[i + channel] - before.data[i + channel]) > 16);
            if (different) changed++;
            after.data[i] = different ? 255 : 24;
            after.data[i + 1] = different ? 50 : 24;
            after.data[i + 2] = different ? 80 : 24;
            after.data[i + 3] = 255;
          }
          entry.changedPixelRatio = changed / (after.width * after.height);
          if (entry.changedPixelRatio > 0.005) {
            entry.diff = `${name}.diff.png`;
            await fs.writeFile(path.join(out, entry.diff), PNG.sync.write(after));
            throw new Error(`${(entry.changedPixelRatio * 100).toFixed(2)}% of pixels changed`);
          }
        } catch (error) {
          entry.regression = error.message;
          visualFailures.push(`${name}: ${error.message}`);
        }
      }
    };
    for (const width of [1440, 390]) {
      await page.setViewportSize({ width, height: width === 390 ? 844 : 1000 });
      for (const view of ['cli', 'compact']) {
        await page.evaluate(view => fuseToolFixture.view(view), view);
        for (const name of names) for (const status of ['running', 'done', 'error', 'interrupted']) {
          // Exit status is independent of tool failure; do not mask error fixtures with exit=0.
          const t = trace(name, status, status === 'done' ? {} : { exit_code: undefined });
          await mount([t], status === 'running');
          const r = row('tool-a');
          assert.equal(await toggle(r).getAttribute('aria-expanded'), 'false');
          assert.equal(await body(r).isVisible(), false);
          assert.equal(await state(r).isVisible(), true);
          const expected = status === 'error' ? 'Failed' : status === 'interrupted' ? 'Stopped' : status === 'running' ? /^get_(command|subagent)_output$/.test(name) ? 'Waiting' : 'Running' : 'Done';
          assert.equal(await state(r).textContent(), expected, `${name}/${status}/${view}`);
          const screenshot = process.env.TOOL_UI_ALL_SCREENSHOTS === '1' || ['read_url', 'create_artifact', 'grep', 'run_terminal_command', 'spawn_subagent'].includes(name);
          const key = `${name}.${status}.${view}.${width}`;
          if (screenshot) await capture(`${key}.collapsed`, r, { view, width, status: expected, expanded: false });
          await toggle(r).click();
          assert.equal(await body(r).isVisible(), true);
          assert.equal(await toggle(r).getAttribute('aria-expanded'), 'true');
          if (status === 'error') assert.equal(await r.locator('.chat-tool-section.is-error').count(), 1);
          if (screenshot) await capture(`${key}.expanded`, r, { view, width, status: expected, expanded: true });
          await toggle(r).click();
          await page.evaluate(() => fuseToolFixture.update('tool-a', { summary: 'New streamed output' }, false));
          assert.equal(await body(r).isVisible(), false, 'rerender must preserve collapsed details');
          assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
          assertions++;
        }

        // Nested rows obey the same contract, including mixed outcomes.
        for (const name of names.filter(n => !['create_artifact', 'update_artifact', 'unknown_tool', 'search_tools', 'load_skill', 'propose_skill', 'todo_write', 'kill_command', 'kill_subagent'].includes(n))) {
          await mount([trace(name, 'done', { id: 'a' }), trace(name, 'error', { id: 'b', exit_code: undefined })]);
          const group = page.locator('[data-step="group-tool-a"]');
          assert.equal(await state(group).textContent(), '1 done · 1 failed');
          await toggle(group).click();
          assert.equal(await state(row('b')).textContent(), 'Failed');
          await toggle(row('b')).click();
          assert.equal(await body(row('b')).isVisible(), true);
          if (['read_url', 'spawn_subagent', 'list_dir'].includes(name)) await capture(`group.${name}.${view}.${width}`, group, { status: '1 done · 1 failed', expanded: true });
          await toggle(group).click();
          assert.equal(await toggle(row('b')).getAttribute('aria-expanded'), 'true', 'parent toggle must not change child aria state');
          await toggle(group).click();
          assert.equal(await body(row('b')).isVisible(), true);
          assertions++;
        }

        // Existing singleton becomes a group without losing tool disclosure identity.
        await mount([trace('grep', 'done', { id: 'a' })]);
        await toggle(row('a')).click();
        await page.evaluate(t => fuseToolFixture.append(t), trace('grep', 'done', { id: 'b' }));
        assert.equal(await toggle(page.locator('[data-step="group-tool-a"]')).getAttribute('aria-expanded'), 'true', 'new group keeps an already-open child visible');
        assert.equal(await toggle(row('a')).getAttribute('aria-expanded'), 'true');
        assert.equal(await toggle(row('b')).getAttribute('aria-expanded'), 'false');
        await page.evaluate(view => { fuseToolFixture.view(view === 'cli' ? 'compact' : 'cli'); fuseToolFixture.view(view); }, view);
        assert.equal(await toggle(row('a')).getAttribute('aria-expanded'), 'true', 'view switch preserves disclosure');

        await mount([trace('read_file', 'queued', { preview: '' })], true);
        assert.equal(await state(row('tool-a')).textContent(), 'Queued');
        await page.evaluate(() => fuseToolFixture.update('tool-a', { status: 'done' }));
        await toggle(row('tool-a')).click();
        assert.ok((await body(row('tool-a')).innerText()).includes('No output'));
        await mount([trace('read_file', 'running')], false);
        assert.equal(await state(row('tool-a')).textContent(), 'Stopped', 'restored unfinished synchronous tools are not live');
        await mount([trace('spawn_subagent', 'error', { error: '', summary: '', preview: 'The model returned an empty reply.' })]);
        await toggle(row('tool-a')).click();
        assert.equal(await row('tool-a').locator('.chat-tool-section.is-error .chat-tool-out').textContent(), 'The model returned an empty reply.');
        assert.equal(await row('tool-a').locator('.chat-tool-section:not(.is-error) .chat-tool-out').filter({ hasText: 'empty reply' }).count(), 0);
        await mount([trace('spawn_subagent', 'done', { summary: 'Background task started', preview: '' })]);
        await toggle(row('tool-a')).click();
        assert.equal((await body(row('tool-a')).textContent()).includes('Background task started'), false);

        // Signature includes output and exit changes; no stale running indicator.
        await mount([trace('run_terminal_command', 'running', { child_id: 'job-1', background: true, exit_code: undefined })]);
        assert.equal(await state(row('tool-a')).textContent(), 'Background');
        await toggle(row('tool-a')).click();
        await page.evaluate(() => fuseToolFixture.update('tool-a', { preview: 'Fresh partial output' }));
        assert.ok((await body(row('tool-a')).innerText()).includes('Fresh partial output'));
        await page.evaluate(() => fuseToolFixture.jobs([{ id: 'job-1', state: 'completed', exit_code: 2, out: 'Build failed', duration_ms: 500 }]));
        assert.equal(await state(row('tool-a')).textContent(), 'Exit 2');
        assert.equal(await row('tool-a').getAttribute('data-state'), 'warning');
        await capture(`shell.nonzero.${view}.${width}`, row('tool-a'), { status: 'Exit 2', expanded: true });

        // Completion reaches older turns, not merely the first or last message.
        await page.evaluate(() => fuseToolFixture.mount([
          { tools: [{ id: 'unrelated', name: 'read_file', status: 'done', path: 'README.md' }] },
          { tools: [{ id: 'spawn', name: 'spawn_subagent', child_id: 'child-1', background: true, status: 'running' }] },
          { tools: [{ id: 'wait', name: 'get_subagent_output', child_id: 'child-1', status: 'running' }] },
          { content: 'Main agent finished its other work.', tools: [] },
        ]));
        assert.equal(await state(row('spawn')).textContent(), 'Background');
        await page.evaluate(() => fuseToolFixture.event({ id: Date.now(), thread_id: null, type: 'agent.failed', payload: { run_id: 'child-1', status: 'failed', message: { error: 'Child failed' } } }));
        assert.equal(await state(row('spawn')).textContent(), 'Failed');
        assert.equal(await state(row('wait')).textContent(), 'Failed');
        await page.evaluate(() => fuseToolFixture.agents([{ run_id: 'child-1', status: 'running' }]));
        assert.equal(await state(row('spawn')).textContent(), 'Failed', 'delayed progress cannot revive terminal state');
        await page.evaluate(() => fuseToolFixture.progress(1, { message: { tools: [{ id: 'spawn', name: 'spawn_subagent', child_id: 'child-1', status: 'running' }] } }));
        assert.equal(await state(row('spawn')).textContent(), 'Failed', 'stale parent snapshot cannot revive completed child');
        assert.equal(await page.evaluate(() => fuseToolFixture.merge({ id: 'x', status: 'done' }, { id: 'x', status: 'progress' }, 'running').status), 'done');
        assert.ok((await page.locator('[data-activity-summary]').allTextContents()).some(text => text.includes('failed')));

        await mount([trace('read_file', 'done', { path: '/very-long-path/'.repeat(25), preview: 'long output '.repeat(600) })]);
        await toggle(row('tool-a')).click();
        assert.equal(await row('tool-a').locator('.chat-tool-truncated').count(), 1);
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
        await capture(`long-output.${view}.${width}`, row('tool-a'), { expanded: true, truncated: true });

        // Attached command checks need not carry the spawn's background flag.
        // All pending references settle, but historical completed checks do not.
        await mount([
          trace('get_command_output', 'progress', { id: 'poll', child_id: 'attached-job', background: false, preview: '' }),
          trace('get_command_output', 'done', { id: 'historical', child_id: 'attached-job', preview: 'Earlier poll result' }),
        ]);
        await page.evaluate(() => fuseToolFixture.jobs([{ id: 'attached-job', state: 'running' }]));
        assert.equal(await state(row('poll')).textContent(), 'Waiting', 'nonterminal jobs must not fail pending checks');
        await page.evaluate(() => fuseToolFixture.jobs([{ id: 'attached-job', state: 'completed', exit_code: -9, out: 'x'.repeat(6000) }]));
        await toggle(page.locator('[data-step="group-tool-poll"]')).click();
        assert.equal(await state(row('poll')).textContent(), 'Exit -9');
        assert.equal(await state(row('historical')).textContent(), 'Done');
        await toggle(row('poll')).click();
        assert.equal(await row('poll').locator('.chat-tool-truncated').count(), 1, 'client-side result truncation must be explicit');
        await page.evaluate(() => fuseToolFixture.jobs([{ id: 'attached-job', state: 'running' }]));
        assert.equal(await state(row('poll')).textContent(), 'Exit -9');

        await mount([trace('get_command_output', 'running', { background: true })]);
        assert.equal(await state(row('tool-a')).textContent(), 'Waiting', 'waiting takes precedence over the job background flag');
        await page.evaluate(() => fuseToolFixture.jobs([{ id: 'fixture-job', state: 'timed_out', error: 'Command deadline exceeded' }]));
        assert.equal(await state(row('tool-a')).textContent(), 'Failed', 'timed-out jobs must settle waiting rows');
        await mount([trace('run_terminal_command', 'error', { exit_code: -9, error: 'exit -9' })]);
        assert.equal(await state(row('tool-a')).textContent(), 'Exit -9', 'signal exits are command outcomes, not tool failures');

        await mount([trace('spawn_subagent', 'running', { child_id: 'queued-agent', background: true })]);
        await page.evaluate(() => fuseToolFixture.agents([{ run_id: 'queued-agent', status: 'queued' }]));
        assert.equal(await state(row('tool-a')).textContent(), 'Queued', 'queued child state survives reconciliation');
        await page.evaluate(() => fuseToolFixture.agents([{ run_id: 'queued-agent', status: 'cancelled', message: { error: 'Child stopped by user', content: 'report '.repeat(900) } }]));
        assert.equal(await state(row('tool-a')).textContent(), 'Stopped');
        await toggle(row('tool-a')).click();
        assert.equal(await row('tool-a').locator('.is-error .chat-tool-out').textContent(), 'Child stopped by user');
        assert.equal(await row('tool-a').locator('.chat-tool-truncated').count(), 1);

        await mount([trace('run_terminal_command', 'interrupted', { error: 'Host connection lost', exit_code: undefined, host: 'build-host', cwd: '/srv/build', duration_ms: 234, timeout_ms: 5000 })]);
        await toggle(row('tool-a')).click();
        assert.equal(await row('tool-a').locator('.is-error .chat-tool-out').textContent(), 'Host connection lost');
        assert.match(await row('tool-a').locator('.chat-tool-context').textContent(), /Host build-host.*Directory \/srv\/build/);
        assert.match(await row('tool-a').locator('.is-meta .chat-tool-out').textContent(), /234 ms.*Timeout 5000 ms/);
        // Rerendering streamed details must not drop keyboard focus to the body.
        await toggle(row('tool-a')).focus();
        await page.evaluate(() => fuseToolFixture.update('tool-a', { preview: 'New output while inspecting' }));
        assert.equal(await toggle(row('tool-a')).evaluate(el => el === document.activeElement), true);
        assert.equal(await toggle(row('tool-a')).getAttribute('aria-expanded'), 'true');

        await mount([trace('kill_command', 'done', { child_id: 'job-unique', preview: '' })]);
        assert.match(await toggle(row('tool-a')).innerText(), /job-unique/, 'stop rows expose target identity');
        await mount([trace('create_artifact', 'done', { version: 3, visibility: 'private' })]);
        assert.equal(await row('tool-a').locator('.chat-step-open').getAttribute('aria-label'), 'Open artifact');
        await toggle(row('tool-a')).click();
        assert.match(await row('tool-a').locator('.is-meta .chat-tool-out').innerText(), /Version 3.*Visibility private/);

        // Exercise every outcome together; two-state groups miss label starvation.
        await mount([
          trace('write_file', 'done', { id: 'mixed-a' }),
          trace('write_file', 'error', { id: 'mixed-b' }),
          trace('write_file', 'interrupted', { id: 'mixed-c' }),
          trace('write_file', 'running', { id: 'mixed-d' }),
          trace('write_file', 'queued', { id: 'mixed-e' }),
        ], true);
        const mixed = page.locator('[data-step="group-tool-mixed-a"]');
        assert.equal(await state(mixed).textContent(), '1 running · 1 queued · 1 done · 1 failed · 1 stopped');
        assert.ok((await toggle(mixed).boundingBox()).width > 90, 'group title retains readable width');
        await toggle(mixed).click();
        assert.equal(await state(row('mixed-c')).textContent(), 'Stopped');
        const doneColor = await row('mixed-a').locator(':scope > .chat-step-node').evaluate(el => getComputedStyle(el).color);
        const failedColor = await row('mixed-b').locator(':scope > .chat-step-node').evaluate(el => getComputedStyle(el).color);
        assert.notEqual(doneColor, failedColor, 'mixed group must not recolor completed child as failed/running');
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
        await capture(`group.mixed-outcomes.${view}.${width}`, mixed, { expanded: true, status: '1 running · 1 queued · 1 done · 1 failed · 1 stopped' });

        await page.evaluate(() => fuseToolFixture.mount([{ reasoning: 'First paragraph.\n\nSecond paragraph.\n  indented detail', tools: [] }]));
        const thinking = page.locator('[data-step="think-0"]');
        assert.equal(await thinking.locator('.chat-think-markdown p').count(), 2, 'reasoning uses Markdown paragraphs');
        assert.match(await thinking.locator('.chat-think-preview').textContent(), /First paragraph\..*Second paragraph\./s);
        if (await toggle(thinking).getAttribute('aria-expanded') !== 'true') await toggle(thinking).click();
        await toggle(thinking).click();
        await page.evaluate(() => fuseToolFixture.progress(0, { message: { reasoning: 'First paragraph.\n\nSecond paragraph.\n  indented detail\nMore thinking.' } }));
        assert.equal(await toggle(thinking).getAttribute('aria-expanded'), 'false', 'thinking disclosure survives updates');

        await page.evaluate(() => FuseChatAppearance.setTheme('paper'));
        await mount([trace('create_artifact', 'error', { preview: '', error: 'Invalid artifact content.\nThe document is missing its root element.' })]);
        await toggle(row('tool-a')).click();
        await capture(`artifact.error.paper.${view}.${width}`, row('tool-a'), { theme: 'paper', expanded: true, status: 'Failed' });
        await page.evaluate(() => FuseChatAppearance.setTheme('fuse'));
      }
    }

    // Narrow devices and enlarged text must retain usable disclosure controls.
    await page.setViewportSize({ width: 320, height: 700 });
    for (const view of ['cli', 'compact']) {
      await page.evaluate(view => { fuseToolFixture.view(view); FuseChatAppearance.setSize(130); }, view);
      await mount([trace('spawn_subagent', 'running', { description: 'Investigate a very long authentication integration description', background: true })]);
      await toggle(row('tool-a')).click();
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
      assert.ok((await toggle(row('tool-a')).boundingBox()).width > 40, 'narrow tool labels remain operable');
      if (view === 'cli') {
        assert.ok((await toggle(row('tool-a')).boundingBox()).width > 40, 'terminal action rows retain a usable single-line target');
        assert.ok((await toggle(row('tool-a')).boundingBox()).height < 220, 'expanded target must not wrap into a tall column of fragments');
      }
      assert.equal(await row('tool-a').getByRole('button', { name: 'Open agent', exact: true }).isVisible(), true);
      await capture(`agent.enlarged.${view}.320`, row('tool-a'), { expanded: true, textScale: 130, width: 320 });
    }
    await page.evaluate(() => {
      FuseChatAppearance.setSize(100);
      FuseChatAppearance.setFont('compact', 'serif');
    });
    assert.match(await page.locator('[data-chat-font-select] option').first().textContent(), /Bricolage Grotesque/, 'View default label must not change to the custom font');
    await page.evaluate(() => FuseChatAppearance.setFont('compact', null));
    assert.match(await page.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue('--chat-view-font')), /Bricolage Grotesque/);

    // Reload restores only UI booleans, scoped to a thread/message identity.
    await page.evaluate(() => {
      fuseToolFixture.view('compact');
      fuseToolFixture.mount([{ tools: [{ id: 'reload-tool', name: 'read_file', status: 'error', path: 'README.md', error: 'Unavailable' }] }], false, 'disclosure-test');
    });
    await page.locator('button[data-activity-summary]').click();
    await toggle(row('reload-tool')).click();
    await page.reload();
    await page.waitForFunction(() => Boolean(window.fuseToolFixture));
    await page.evaluate(() => fuseToolFixture.mount([{ tools: [{ id: 'reload-tool', name: 'read_file', status: 'error', path: 'README.md', error: 'Unavailable' }] }], false, 'disclosure-test'));
    assert.equal(await body(row('reload-tool')).isVisible(), true, 'reload restores expanded activity and tool');
    await toggle(row('reload-tool')).click();
    await page.evaluate(() => fuseToolFixture.mount([{ tools: [{ id: 'reload-tool', name: 'read_file', status: 'error', error: 'Unavailable' }] }], false, 'disclosure-test'));
    assert.equal(await body(row('reload-tool')).isVisible(), false, 'explicitly closed errors stay closed');
    await page.evaluate(() => fuseToolFixture.mount([{ tools: [{ id: 'reload-tool', name: 'read_file', status: 'error', error: 'Unavailable' }] }], false, 'other-thread'));
    assert.equal(await page.locator('button[data-activity-summary]').getAttribute('aria-expanded'), 'false', 'disclosure maps do not leak between threads');
    assert.deepEqual(failures, [], 'browser errors');
    await fs.writeFile(path.join(out, 'manifest.json'), JSON.stringify({ assertions, browser: browser.version(), baseline: baseline || null, cases: manifest }, null, 2));
    const escape = text => String(text).replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('"', '&quot;');
    await fs.writeFile(path.join(out, 'index.html'), `<!doctype html><meta charset="utf-8"><title>Fuse tool-state gallery</title>
      <style>body{font:14px system-ui;background:#171918;color:#eee;padding:24px}main{display:grid;grid-template-columns:repeat(auto-fit,minmax(360px,1fr));gap:24px}article{min-width:0;padding:16px;background:#252826;border-radius:8px}img{max-width:100%;height:auto}h2{font-size:14px;overflow-wrap:anywhere}pre{white-space:pre-wrap;color:#bcc5bd}</style>
      <h1>Fuse tool-state gallery</h1><p>${assertions} matrix cases plus lifecycle transitions. ${manifest.length} screenshots. Expand an image for full resolution.</p><main>` +
      manifest.map(item => `<article><h2>${escape(item.case)}</h2><pre>${escape(JSON.stringify(item.expected))}</pre>${item.regression ? `<p>${escape(item.regression)}</p>` : ''}<a href="${item.screenshot}"><img loading="lazy" src="${item.screenshot}" alt="${escape(item.case)}"></a>${item.diff ? `<a href="${item.diff}">Pixel diff</a>` : ''}</article>`).join('') + '</main>');
    assert.deepEqual(visualFailures, [], 'visual regressions; inspect the gallery before changing a baseline');
    console.log(`PASS ${assertions} tool/state/view/viewport and grouped cases, plus lifecycle transitions. ${manifest.length} screenshots in ${out}`);
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
