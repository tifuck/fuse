// Run only against chat_browser_fixture.py: all model output is local/mocked.
const assert = require('node:assert/strict');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const origin = process.env.FUSE_BROWSER_ORIGIN || 'http://127.0.0.1:8099';
assert.match(origin, /^http:\/\/127\.0\.0\.1:\d+$/);

(async () => {
  const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] });
  try {
    const page = await browser.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.route('**/*', route => new URL(route.request().url()).origin === origin ? route.continue() : route.abort());
    await page.goto(origin);
    const id = await page.evaluate(async () => {
      const messages = Array.from({ length: 80 }, (_, i) => ({
        role: i % 2 ? 'assistant' : 'user',
        content: i % 2 ? `Historical response ${i}\n\n` + 'A long answer with **formatting** and `code`.\n\n'.repeat(100) : `Question ${i}`,
      }));
      const response = await fetch('/chat/threads', { method: 'POST', headers: {
        'Content-Type': 'application/json', 'X-CSRF-Token': 'browser-test-csrf',
      }, body: JSON.stringify({ model: 'offline', messages, command_id: 'performance-fixture' }) });
      if (!response.ok) throw new Error(await response.text());
      return (await response.json()).id;
    });
    await page.goto(origin + '/chat?t=' + id);
    await page.waitForFunction(() => document.querySelectorAll('[data-chat-log] > article').length === 80 &&
      document.querySelector('[data-sync-label]').textContent === 'Live sync');
    assert.ok(await page.locator('[data-deferred-message]').count() >= 30);
    await page.evaluate(() => {
      window.__retainedArticle = document.querySelector('[data-chat-log]').lastElementChild;
    });
    // Delayed acknowledgment must still display immediate, non-durable feedback.
    await page.route('**/turns', async route => {
      await new Promise(resolve => setTimeout(resolve, 700));
      await route.continue();
    });
    await page.locator('[data-chat-input]').fill('Responsive send fixture');
    await page.locator('[data-chat-send]').click();
    await page.locator('.chat-pending-send').waitFor({ timeout: 500 });
    assert.equal(await page.locator('[data-chat-log] > article').count(), 80);
    await page.waitForFunction(() => document.querySelectorAll('[data-chat-log] > article').length === 82);
    await page.locator('.chat-pending-send').waitFor({ state: 'detached' });
    assert.equal(await page.evaluate(() => window.__retainedArticle === document.querySelector('[data-chat-log]').children[79]), true);
    await page.waitForFunction(() => !document.body.classList.contains('is-streaming'));
    // Scrolling to old responses hydrates them without dropping transcript rows.
    await page.evaluate(() => {
      window.fuseToolFixture.follow(false);
      document.querySelector('[data-chat-thread]').scrollTop = 0;
    });
    await page.waitForFunction(() => !document.querySelector('[data-chat-log]').children[1].hasAttribute('data-deferred-message'));
    assert.match(await page.locator('[data-chat-log] > article').nth(1).innerText(), /Historical response 1/);
    assert.equal(await page.locator('[data-chat-log] > article').count(), 82);
    assert.deepEqual(errors, []);
    console.log('PASS long-history lazy rendering, immediate send feedback, retained DOM, and scroll hydration');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exit(1); });
