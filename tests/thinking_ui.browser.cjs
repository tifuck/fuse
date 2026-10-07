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
    for (const width of [1440, 390]) {
      await page.setViewportSize({ width, height: 950 });

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
      console.log(`PASS content streaming motion and follow ${width}`);
    }

    assert.deepEqual(errors, []);
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
