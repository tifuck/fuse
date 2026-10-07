// Isolated production-renderer checks: no provider or external network calls.
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const origin = process.env.FUSE_BROWSER_ORIGIN || 'http://127.0.0.1:8099';
assert.match(origin, /^http:\/\/127\.0\.0\.1:\d+$/);
const out = process.env.FRONTEND_LIFECYCLE_ARTIFACTS || '/tmp/opencode/frontend-lifecycle-artifacts';

(async () => {
  const browser = await chromium.launch({ headless: true, executablePath: process.env.CHROMIUM_PATH || undefined, args: ['--no-sandbox'] });
  const errors = [];
  try {
    await fs.mkdir(out, { recursive: true });
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
    page.on('pageerror', e => errors.push(e.message));
    await page.route('**/*', route => new URL(route.request().url()).origin === origin ? route.continue() : route.abort());
    await page.goto(origin);
    await page.waitForFunction(() => Boolean(window.fuseToolFixture));
    for (const width of [1440, 390]) {
      await page.setViewportSize({ width, height: width === 390 ? 844 : 1000 });

      await page.evaluate(() => fuseToolFixture.mount([{
        content: 'Earlier answer.', reasoning: 'Earlier thought.\nLatest thinking.',
        tools: [{ id: 'poll', name: 'get_command_output', status: 'running', child_id: 'job-1' }],
        segments: [{ type: 'content', text: 'Earlier answer.' }, { type: 'tool', id: 'poll' }, { type: 'reasoning', text: 'Latest thinking.' }],
      }], true));
      const label = page.locator('[data-live-dock-label]');
      assert.match(await label.textContent(), /thinking/, 'fresh reasoning outranks an older pending poll');
      assert.equal(await page.locator('[data-step="cgroup-think-2"].is-running').count(), 1, 'latest thinking stays live after earlier answer text');
      assert.equal(await page.locator('[data-step="tool-poll"]').getAttribute('data-state'), 'running', 'background lifecycle remains independent of model activity');
      await page.evaluate(() => fuseToolFixture.stream(0, { segments: [{ type: 'reasoning', text: 'Plan' }, { type: 'tool', id: 'poll' }] }));
      assert.doesNotMatch(await label.textContent(), /thinking/, 'new poll outranks earlier reasoning');
      await page.waitForFunction(() => /waiting for command/.test(document.querySelector('[data-live-dock-label]').textContent));
      await page.evaluate(() => fuseToolFixture.update('poll', { status: 'done' }, true));
      assert.doesNotMatch(await label.textContent(), /thinking/, 'a completed tool does not resurrect old reasoning');
      await page.evaluate(() => fuseToolFixture.stream(0, { segments: [{ type: 'tool', id: 'poll' }, { type: 'content', text: 'Fresh answer' }] }));
      assert.doesNotMatch(await label.textContent(), /thinking/, 'writing outranks old reasoning');
      await page.evaluate(() => fuseToolFixture.run({ cancel_requested: true }));
      await page.waitForFunction(() => /stopping/.test(document.querySelector('[data-live-dock-label]').textContent), null, { timeout: 5000 });
      assert.equal(await page.evaluate(() => fuseToolFixture.activity({ id: 'independent-child', reasoning: 'Thinking independently', segments: [{ type: 'reasoning', text: 'Thinking independently' }] }, null, true).label), 'thinking', 'parent cancellation must not label an independent child as stopping');
      assert.equal(await page.locator('[data-live-dock]').getAttribute('aria-hidden'), 'false');
      await page.evaluate(() => fuseToolFixture.run({ status: 'cancelled' }));
      assert.equal(await page.locator('[data-live-dock]').getAttribute('aria-hidden'), 'true');
      await page.evaluate(() => fuseToolFixture.run({ status: 'running', cancel_requested: false }));
      await page.evaluate(() => fuseToolFixture.stream(0, {
        tools: [{ id: 'read', name: 'read_file', status: 'running', path: 'README.md' }],
        segments: [{ type: 'tool', id: 'read' }, { type: 'content', text: 'Concurrent output' }],
      }));
      assert.equal(await page.locator('[data-step="tool-read"]').getAttribute('data-state'), 'running', 'earlier activity block still belongs to the live turn');
      await page.evaluate(() => fuseToolFixture.run({ cancel_requested: true }));
      assert.equal(await page.locator('[data-step="tool-read"]').getAttribute('data-state'), 'running', 'requested cancellation is not yet a stopped tool');
      await page.evaluate(() => fuseToolFixture.run({ status: 'cancelled' }));
      await page.evaluate(() => fuseToolFixture.update('read', { status: 'interrupted' }));
      assert.equal(await page.locator('[data-step="tool-read"]').getAttribute('data-state'), 'interrupted', 'confirmed cancellation settles the tool');
      await page.evaluate(() => fuseToolFixture.run({ status: 'queued', cancel_requested: false }));
      assert.match(await label.textContent(), /queued/);

      for (const reduced of ['no-preference', 'reduce']) {
        await page.emulateMedia({ reducedMotion: reduced });
        await page.evaluate(() => fuseToolFixture.mount([{ tools: [
          { id: 'motion-a', name: 'read_file', status: 'done', path: 'README.md', output: '1: x\n', results: [], error: '' },
          { id: 'motion', name: 'read_file', status: 'done', path: 'README.md', preview: 'File contents', output: Array.from({ length: 20 }, (_, i) => `line ${i}`).join('\n') },
        ], segments: [{ type: 'tool', id: 'motion-a' }, { type: 'tool', id: 'motion' }] }]));
        await page.locator('.is-console-group > .chat-step-main > .chat-step-head > .chat-step-toggle').click();
        if (reduced === 'reduce') assert.equal(await page.locator('.is-console-group').evaluate(el => el.getAnimations({ subtree: true }).length), 0, 'reduced-motion disables disclosure animation');
        await page.evaluate(() => {
          fuseToolFixture.mount([{ content: 'Streaming line.\n\n'.repeat(100) }], true);
          fuseToolFixture.follow();
        });
        await page.waitForFunction(() => fuseToolFixture.scrollState().gap <= 2);
        await page.evaluate(() => fuseToolFixture.stream(0, { content: 'Streaming line.\n\n'.repeat(104) }));
        await page.waitForFunction(() => fuseToolFixture.scrollState().gap <= 2);
        // Even a small upward gesture should immediately suspend following.
        await page.locator('[data-chat-thread]').evaluate(el => {
          el.dispatchEvent(new WheelEvent('wheel', { deltaY: -30, bubbles: true }));
          el.scrollTop -= 30;
        });
        const paused = await page.evaluate(() => fuseToolFixture.scrollState().top);
        await page.evaluate(() => fuseToolFixture.stream(0, { content: 'Streaming line.\n\n'.repeat(108) }));
        await page.waitForTimeout(350);
        const state = await page.evaluate(() => fuseToolFixture.scrollState());
        assert.equal(state.following, false, 'streaming must not fight a small upward manual scroll');
        assert.ok(Math.abs(state.top - paused) <= 2, 'reader position stays stable');
        await page.locator('[data-chat-latest]').click();
        await page.waitForFunction(() => fuseToolFixture.scrollState().gap <= 2);
        assert.equal(await page.evaluate(() => fuseToolFixture.scrollState().following), true);
      }
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
      console.log(`PASS status precedence, disclosure motion, streaming follow ${width}`);
    }
    assert.deepEqual(errors, []);
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
