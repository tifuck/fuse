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
    for (const width of [1440, 390]) for (const view of ['cli', 'compact']) {
      await page.setViewportSize({ width, height: width === 390 ? 844 : 1000 });
      await page.evaluate(view => fuseToolFixture.view(view), view);
      await page.evaluate(() => fuseToolFixture.mount([{
        content: 'Earlier answer.', reasoning: 'Earlier thought.\nLatest thinking.',
        tools: [{ id: 'poll', name: 'get_command_output', status: 'running', child_id: 'job-1' }],
        segments: [{ type: 'content', text: 'Earlier answer.' }, { type: 'tool', id: 'poll' }, { type: 'reasoning', text: 'Latest thinking.' }],
      }], true));
      const label = page.locator('[data-live-dock-label]');
      assert.equal(await label.textContent(), 'Thinking', 'fresh reasoning outranks an older pending poll');
      assert.equal(await page.locator('[data-step="think-2"].is-live-think').count(), 1, 'latest thinking stays live after earlier answer text');
      assert.equal(await page.locator('[data-step="tool-poll"]').getAttribute('data-state'), 'running', 'background lifecycle remains independent of model activity');
      await page.evaluate(() => fuseToolFixture.stream(0, { segments: [{ type: 'reasoning', text: 'Plan' }, { type: 'tool', id: 'poll' }] }));
      assert.equal(await label.textContent(), 'Waiting for command output', 'new poll outranks earlier reasoning');
      await page.evaluate(() => fuseToolFixture.update('poll', { status: 'done' }, true));
      assert.equal(await label.textContent(), 'Working', 'a completed tool does not resurrect old reasoning');
      await page.evaluate(() => fuseToolFixture.stream(0, { segments: [{ type: 'tool', id: 'poll' }, { type: 'content', text: 'Fresh answer' }] }));
      assert.equal(await label.textContent(), 'Writing');
      await page.evaluate(() => fuseToolFixture.run({ cancel_requested: true }));
      assert.equal(await label.textContent(), 'Stopping', 'cancellation remains visible until the server confirms terminal state');
      assert.equal(await page.evaluate(() => fuseToolFixture.activity({ id: 'independent-child', reasoning: 'Thinking independently', segments: [{ type: 'reasoning', text: 'Thinking independently' }] }, null, true).label), 'Thinking', 'parent cancellation must not label an independent child as stopping');
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
      assert.equal(await label.textContent(), 'Queued');

      // Live thinking tails follow output unless the reader scrolls inside it.
      await page.evaluate(() => fuseToolFixture.mount([{
        content: 'Earlier answer', reasoning: 'thinking',
        tools: [{ id: 'bg', name: 'run_terminal_command', status: 'running', child_id: 'job-2', background: true }],
        segments: [{ type: 'content', text: 'Earlier answer' }, { type: 'tool', id: 'bg' }, { type: 'reasoning', text: Array.from({ length: 80 }, (_, i) => `Reasoning line ${i}`).join('\n') }],
      }], true));
      const thinking = page.locator('[data-step="think-2"]');
      const toggle = thinking.locator('.chat-step-toggle');
      if (await toggle.getAttribute('aria-expanded') !== 'true') await toggle.click();
      const peek = thinking.locator('.chat-think-preview');
      await page.waitForFunction(() => { const el = document.querySelector('[data-step="think-2"] .chat-think-preview'); return el.clientHeight > 0 && el.scrollHeight - el.clientHeight - el.scrollTop <= 2; });
      assert.ok((await peek.boundingBox()).height < 330, 'live expanded reasoning remains bounded');
      await peek.evaluate(el => { el.scrollTop = 40; });
      await page.evaluate(() => fuseToolFixture.update('bg', { preview: 'New background output' }, true));
      assert.equal(await peek.evaluate(el => el.scrollTop), 40, 'tool rerender preserves manually scrolled reasoning');
      await peek.evaluate(el => { el.scrollTop = el.scrollHeight; });
      await page.evaluate(() => {
        const msg = fuseToolFixture.snapshot()[0];
        msg.segments[2].text += '\nNewest reasoning line';
        fuseToolFixture.stream(0, msg);
      });
      await page.waitForFunction(() => { const el = document.querySelector('[data-step="think-2"] .chat-think-preview'); return el.scrollHeight - el.clientHeight - el.scrollTop <= 2; });
      await page.screenshot({ path: `${out}/thinking-${view}-${width}.png` });
      const liveHeight = (await peek.boundingBox()).height;
      await page.evaluate(() => {
        fuseToolFixture.run({ status: 'completed' });
        fuseToolFixture.progress(0, { message: fuseToolFixture.snapshot()[0] });
      });
      const completedPeek = await peek.boundingBox();
      assert.ok(!completedPeek || completedPeek.height <= liveHeight + 2, 'completed reasoning collapses or retains the explicitly chosen reader size');

      for (const reduced of ['no-preference', 'reduce']) {
        await page.emulateMedia({ reducedMotion: reduced });
        await page.evaluate(() => fuseToolFixture.mount([{ tools: [{ id: 'motion', name: 'read_file', status: 'done', path: 'README.md', preview: 'File contents' }] }]));
        await page.locator('[data-step="tool-motion"] .chat-step-toggle').click();
        if (reduced === 'reduce') assert.equal(await page.locator('[data-step="tool-motion"] .chat-step-reveal').evaluate(el => el.getAnimations().length), 0, 'reduced-motion disables disclosure animation');
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
      console.log(`PASS status precedence, thinking scroll, streaming follow ${view} ${width}`);
    }
    assert.deepEqual(errors, []);
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
