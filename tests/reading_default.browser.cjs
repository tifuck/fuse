const assert = require('node:assert/strict');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const origin = process.env.FUSE_BROWSER_ORIGIN || 'http://127.0.0.1:8099';
assert.match(origin, /^http:\/\/127\.0\.0\.1:\d+$/);
(async () => {
  const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] });
  try {
    for (const legacy of [null, 'cli', 'compact', 'mainframe', 'prism', 'console', 'invalid']) {
      const page = await browser.newPage();
      const errors = [];
      page.on('pageerror', error => errors.push(error.message));
      await page.route('**/*', route => new URL(route.request().url()).origin === origin ? route.continue() : route.abort());
      await page.addInitScript(value => {
        try {
          if (value !== null) localStorage.setItem('fuse.chat.view.v2', value);
          localStorage.setItem('fuse.chat.fonts.v1', JSON.stringify({ console: 'redhat', cli: 'fraunces' }));
          localStorage.setItem('fuse.chat.theme.v1', 'paper');
        } catch (_) { /* sandboxed frames have no storage */ }
      }, legacy);
      await page.goto(origin);
      await page.waitForFunction(() => Boolean(window.fuseToolFixture));
      assert.equal(await page.locator('[data-chat]').getAttribute('data-view'), 'console');
      assert.equal(await page.locator('html').getAttribute('data-chat-font'), 'redhat');
      assert.equal(await page.locator('html').getAttribute('data-chat-theme'), 'paper');
      await page.getByRole('button', { name: 'Appearance', exact: true }).click();
      const menu = page.getByRole('dialog', { name: 'Appearance', exact: true });
      assert.equal(await menu.locator('[data-view]').count(), 0);
      assert.doesNotMatch(await menu.innerText(), /Reading mode|Terminal|Compact|Mainframe|Console/);
      const thinking = menu.locator('[data-thinking-toggle]');
      assert.equal(await thinking.count(), 1);
      await thinking.uncheck();
      assert.equal(await thinking.isChecked(), false);
      await thinking.check();
      assert.equal(await thinking.isChecked(), true);
      assert.deepEqual(errors, []);
      await page.close();
    }
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
