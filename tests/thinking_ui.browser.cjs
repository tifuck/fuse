// Real renderer, isolated fixture, no model/host/external requests.
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const origin = process.env.FUSE_BROWSER_ORIGIN || 'http://127.0.0.1:8099';
assert.match(origin, /^http:\/\/127\.0\.0\.1:\d+$/);
const out = process.env.THINKING_UI_ARTIFACTS || '/tmp/opencode/thinking-ui';

(async () => {
  await fs.mkdir(out, { recursive: true });
  const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] });
  try {
    const page = await browser.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.route('**/*', route => new URL(route.request().url()).origin === origin ? route.continue() : route.abort());
    await page.goto(origin);
    await page.context().grantPermissions(['clipboard-read', 'clipboard-write'], { origin });
    await page.waitForFunction(() => window.fuseToolFixture);
    const markdown = '**Inspecting** `src/config.ts`\n\n### Check the configuration\n- Read the defaults\n- Check *overrides*\n\n```js\nconst retries = 3;\n```\n\n[Reference](https://example.com)\n\n' +
      Array.from({ length: 80 }, (_, i) => `Thought ${i}: checking **configuration** details.`).join('\n');
    const tail = selector => page.waitForFunction(selector => {
      const el = document.querySelector(selector);
      return el && el.clientHeight > 0 && el.scrollHeight - el.clientHeight - el.scrollTop <= 1;
    }, selector);
    for (const width of [1440, 390]) for (const view of ['cli', 'compact']) {
      await page.setViewportSize({ width, height: 950 });
      await page.emulateMedia({ reducedMotion: 'no-preference' });
      // Mount a complete running snapshot, as when joining a chat mid-thought.
      await page.evaluate(({ view, markdown }) => {
        fuseToolFixture.view(view);
        fuseToolFixture.mount([{ id: 'thinking-snapshot', reasoning: markdown,
          tools: [{ id: 'read-before', name: 'read_file', status: 'done', path: 'src/config.ts' }],
          segments: [{ type: 'tool', id: 'read-before' }, { type: 'reasoning', text: markdown }] }], true);
      }, { view, markdown });
      const row = page.locator('[data-step="think-1"]');
      const peek = row.locator('.chat-think-preview');
      const selector = '[data-step="think-1"] .chat-think-preview';
      await tail(selector);
      assert.equal(await row.getAttribute('data-think-mode'), 'tail', 'both views default to a live tail');
      const dimensions = await peek.evaluate(el => ({ height: el.clientHeight, width: el.clientWidth,
        rowWidth: el.closest('.is-think').clientWidth, line: parseFloat(getComputedStyle(el).lineHeight), fontStyle: getComputedStyle(el).fontStyle }));
      assert.ok(Math.abs(dimensions.height - dimensions.line * 5) < 2, `exactly five dense lines: ${JSON.stringify(dimensions)}`);
      assert.ok(dimensions.width >= dimensions.rowWidth - 2, 'thinking uses full available width without a left gutter');
      assert.equal(dimensions.fontStyle, 'italic');
      assert.equal(await peek.locator('h3').count(), 1);
      assert.equal(await peek.locator('ul > li').count(), 2);
      assert.equal(await peek.locator('pre code').count(), 1);
      assert.ok(await peek.locator('strong').count() > 1);
      assert.equal(await row.locator('.chat-step-toggle a, .chat-step-toggle button').count(), 0, 'interactive Markdown is outside the disclosure button');
      await row.screenshot({ path: `${out}/tail-${view}-${width}.png` });

      await page.evaluate(() => { window.settledThinkingNode = document.querySelector('.chat-think-markdown > p'); });
      await page.evaluate(() => {
        const msg = fuseToolFixture.snapshot()[0];
        msg.segments[1].text += '\n' + Array.from({ length: 35 }, (_, i) => `New chunk ${i}`).join('\n');
        fuseToolFixture.stream(0, msg);
      });
      assert.equal(await page.evaluate(() => settledThinkingNode === document.querySelector('.chat-think-markdown > p')), true, 'settled Markdown nodes survive streaming');
      await tail(selector);
      await peek.evaluate(el => { el.dispatchEvent(new WheelEvent('wheel', { deltaY: -30, bubbles: true })); el.scrollTop = 32; });
      await page.evaluate(() => {
        const msg = fuseToolFixture.snapshot()[0];
        msg.segments[1].text += '\nAnother streamed paragraph.';
        fuseToolFixture.stream(0, msg);
        fuseToolFixture.update('read-before', { preview: 'Updated receipt' }, true);
      });
      await page.waitForTimeout(180);
      assert.equal(await peek.evaluate(el => el.scrollTop), 32, 'manual reading survives both text growth and structural tool repaint');
      await page.evaluate(() => fuseToolFixture.repaint());
      assert.equal(await peek.evaluate(el => el.scrollTop), 32, 'full conversation repaint preserves manual thinking scroll');
      await peek.evaluate(el => { el.scrollTop = el.scrollHeight; });
      await row.getByRole('button', { name: 'Expand full thinking', exact: true }).click();
      assert.equal(await row.getAttribute('data-think-mode'), 'expanded');
      await tail(selector);
      assert.ok((await peek.boundingBox()).height > dimensions.height);
      assert.ok((await peek.boundingBox()).width >= dimensions.rowWidth - 2);
      await row.screenshot({ path: `${out}/expanded-${view}-${width}.png` });
      await peek.evaluate(el => { el.dispatchEvent(new WheelEvent('wheel', { deltaY: -30, bubbles: true })); el.scrollTop = 0; });
      await row.screenshot({ path: `${out}/markdown-${view}-${width}.png` });
      await peek.getByRole('button', { name: 'Copy', exact: true }).click();
      assert.equal(await page.evaluate(() => navigator.clipboard.readText()), 'const retries = 3;');
      assert.equal(await row.getAttribute('data-think-mode'), 'expanded', 'Markdown controls do not toggle thinking');
      await row.getByRole('button', { name: 'Show five-line thinking preview' }).click();
      await tail(selector);
      await row.locator('.chat-step-toggle').click();
      assert.equal(await row.getAttribute('data-think-mode'), 'collapsed');
      await peek.waitFor({ state: 'hidden' });
      assert.equal(await row.locator('.chat-think-summary strong').count(), 1);
      assert.equal(await row.locator('.chat-think-summary code').count(), 1);
      await page.evaluate(() => {
        const msg = fuseToolFixture.snapshot()[0]; msg.segments[1].text += '\nStill thinking'; fuseToolFixture.stream(0, msg);
      });
      assert.equal(await row.getAttribute('data-think-mode'), 'collapsed', 'explicit collapse is respected while streaming');
      await row.screenshot({ path: `${out}/collapsed-${view}-${width}.png` });

      // Large normal-output chunks never jump the view by more than a viewport:
      // the view stays pinned to the end (no frame where new text sits below it)
      // and the text is revealed at a steady pace instead of landing as one
      // block. Reduced motion goes straight to the tail.
      for (const motion of ['no-preference', 'reduce']) {
        await page.emulateMedia({ reducedMotion: motion });
        await page.evaluate(() => { fuseToolFixture.mount([{ content: 'Settled paragraph.\n\n'.repeat(60) }], true); fuseToolFixture.follow(); });
        await page.waitForFunction(() => fuseToolFixture.scrollState().gap <= 1);
        const trajectory = await page.evaluate(async () => {
          const first = document.querySelector('[data-content-block] > p');
          const before = fuseToolFixture.scrollState().top;
          fuseToolFixture.stream(0, { content: 'Settled paragraph.\n\n'.repeat(60) + 'Large new chunk.\n\n'.repeat(60) });
          await new Promise(requestAnimationFrame);
          return { before, ...fuseToolFixture.scrollState(), sameNode: first === document.querySelector('[data-content-block] > p') };
        });
        assert.equal(trajectory.sameNode, true);
        assert.ok(trajectory.gap <= 1, `the view trails a large chunk instead of staying pinned: ${JSON.stringify(trajectory)}`);
        const viewport = await page.evaluate(() => document.querySelector('[data-chat-thread]').clientHeight);
        if (motion === 'no-preference') assert.ok(trajectory.top - trajectory.before < viewport, `a large chunk moved the view a whole viewport in one frame: ${JSON.stringify(trajectory)}`);
        await page.waitForFunction(() => fuseToolFixture.scrollState().gap <= 1);
        await page.waitForFunction(() => document.querySelectorAll('[data-content-block] > p').length >= 120, null, { timeout: 3000 });
      }
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
      console.log(`PASS Markdown thinking, live join, full width, scroll preservation and streaming motion ${view} ${width}`);
    }

    let childThought = markdown;
    await page.route('**/chat/runs/thinking-child', route => route.fulfill({ json: {
      id: 'thinking-child', event_cursor: 0, message_id: 'thinking-child-message', status: 'running', description: 'Thinking child',
      created_at: new Date().toISOString(), updated_at: new Date().toISOString(), messages: [],
      message: { role: 'assistant', reasoning: childThought, content: '', tools: [], segments: [{ type: 'reasoning', text: childThought }] },
    } }));
    await page.setViewportSize({ width: 1440, height: 950 });
    for (const view of ['cli', 'compact']) {
      await page.evaluate(view => {
        fuseToolFixture.view(view);
        fuseToolFixture.mount([{ tools: [{ id: 'child-launch', name: 'spawn_subagent', child_id: 'thinking-child', status: 'running', background: true, description: 'Thinking child' }] }]);
      }, view);
      await page.getByRole('button', { name: 'Open agent', exact: true }).click();
      const childPeek = page.locator('[data-agent-log-body] .chat-think-preview');
      await tail('[data-agent-log-body] .chat-think-preview');
      assert.equal(await page.locator('[data-agent-log-body] .is-think').getAttribute('data-think-mode'), 'tail');
      await childPeek.evaluate(el => { el.scrollTop = 30; });
      childThought += '\nChild update during inspection.';
      await page.evaluate(() => fuseToolFixture.pollAgent());
      assert.equal(await childPeek.evaluate(el => el.scrollTop), 30, 'agent polling preserves manually scrolled thinking');
      await page.locator('[data-agent-log-body]').getByRole('button', { name: 'Expand full thinking' }).click();
      await childPeek.getByRole('button', { name: 'Copy', exact: true }).click();
      assert.equal(await page.evaluate(() => navigator.clipboard.readText()), 'const retries = 3;');
      await page.locator('[data-agents-close]').click();
    }
    assert.deepEqual(errors, []);
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
