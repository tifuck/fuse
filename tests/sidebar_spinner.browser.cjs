// The sidebar's run spinner (thread row, Chats tab) must clear when the turn
// ends, including for a chat created by its first send (no agents ever load).
const assert = require('node:assert/strict');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const origin = process.env.FUSE_BROWSER_ORIGIN || 'http://127.0.0.1:8099';
assert.match(origin, /^http:\/\/127\.0\.0\.1:\d+$/);

(async () => {
  const browser = await chromium.launch({ args: ['--no-sandbox'], executablePath: process.env.CHROMIUM_PATH || undefined });
  const errors = [];
  try {
    for (const view of ['cli', 'console', 'mainframe']) {
      const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, reducedMotion: 'reduce' });
      await context.route('**/*', route => new URL(route.request().url()).origin === origin ? route.continue() : route.abort());
      const page = await context.newPage();
      page.on('pageerror', error => errors.push(error.message));
      await page.goto(origin);
      await page.locator('[data-sync-label]').filter({ hasText: 'Live sync' }).waitFor({ state: 'attached' });
      await page.evaluate(view => fuseToolFixture.view(view), view);
      await page.locator('[data-chat-input]').fill(`Spinner fixture ${view}`);
      await page.locator('[data-chat-send]').click();
      await page.waitForFunction(() => document.body.classList.contains('is-streaming'));
      const row = page.locator('.chat-history-item.is-on');
      await row.waitFor({ state: 'attached' });
      assert.equal(await row.getAttribute('aria-busy'), 'true', `${view}: row spins while the turn streams`);
      // A running turn must not lock the chat menu (it used to return silently).
      if (!(await row.isVisible())) await page.locator('.chat-bar [data-history-toggle]').click();
      await row.hover();
      await row.locator('button.more').click();
      const menu = await page.locator('.chat-pop').innerText();
      assert.equal(await page.evaluate(() => document.body.classList.contains('is-streaming')), true, `${view}: still streaming while the menu opens`);
      assert.match(menu, /Rename[\s\S]*Move to project[\s\S]*Delete/, `${view}: chat menu opens mid-turn: ${menu}`);
      await page.mouse.click(900, 500);
      await page.locator('.chat-pop').waitFor({ state: 'detached' });
      await page.waitForFunction(() => !document.body.classList.contains('is-streaming'), null, { timeout: 30000 });
      // Let the closing thread.updated / run.completed events land.
      await page.waitForTimeout(1500);
      const state = await page.evaluate(() => ({
        row: document.querySelector('.chat-history-item.is-on')?.className || '',
        busy: document.querySelector('.chat-history-item.is-on')?.getAttribute('aria-busy') || '',
        spins: [...document.querySelectorAll('[data-run-spin]')].filter(el => !el.hidden).map(el => el.getAttribute('data-run-spin')),
        tab: [...document.querySelectorAll('[data-history-tab]')].map(el => el.getAttribute('aria-busy')),
      }));
      assert.doesNotMatch(state.row, /is-running/, `${view}: row stops spinning after the turn: ${JSON.stringify(state)}`);
      assert.deepEqual(state.spins, [], `${view}: no run spinner left: ${JSON.stringify(state)}`);
      assert.deepEqual(state.tab.filter(v => v === 'true'), [], `${view}: tabs idle: ${JSON.stringify(state)}`);
      console.log(`PASS sidebar spinner clears after a first-send turn (${view})`);
      await context.close();
    }
    assert.deepEqual(errors, []);
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
