// Offline, MariaDB-backed fixture only. Node identity is a correctness contract.
const assert = require('node:assert/strict');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const origin = process.env.FUSE_BROWSER_ORIGIN || 'http://127.0.0.1:8106';
assert.match(origin, /^http:\/\/127\.0\.0\.1:\d+$/);
(async () => {
  const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] });
  try {
    for (const width of [390, 1440]) {
      const page = await browser.newPage({ viewport: { width, height: 900 } });
      const errors = [];
      page.on('pageerror', error => errors.push(error.message));
      await page.route('**/*', route => new URL(route.request().url()).origin === origin ? route.continue() : route.abort());
      await page.goto(origin);
      await page.waitForFunction(() => window.fuseToolFixture);
      await page.evaluate(() => {
        fuseToolFixture.mount([{ content: 'Stable paragraph.\n\nGrowing', tools: [],
          segments: [{ type: 'content', text: 'Stable paragraph.\n\nGrowing' }] }], true);
        window.savedParagraph = document.querySelector('[data-content-block] p');
        window.savedMessage = document.querySelector('[data-chat-log] article');
        for (let i = 0; i < 100; i++) {
          const text = 'Stable paragraph.\n\nGrowing' + ' output'.repeat(i + 1);
          fuseToolFixture.stream(0, { content: text, segments: [{ type: 'content', text }] });
        }
      });
      assert.equal(await page.evaluate(() => savedParagraph === document.querySelector('[data-content-block] p')), true);
      assert.equal(await page.evaluate(() => savedMessage === document.querySelector('[data-chat-log] article')), true);
      assert.equal(await page.evaluate(() => document.querySelector('[data-content-block]').textContent), 'Stable paragraph.Growing' + ' output'.repeat(100));
      await page.evaluate(() => {
        const message = fuseToolFixture.snapshot()[0];
        message.tools = [{ id: 'search-1', name: 'web_search', status: 'running', query: 'fixture' }];
        message.segments.push({ type: 'tool', id: 'search-1' });
        fuseToolFixture.stream(0, message);
        fuseToolFixture.update('search-1', { status: 'done', results: [{ title: 'Fixture result', url: 'https://example.com/' }] }, true);
      });
      assert.equal(await page.evaluate(() => savedParagraph === document.querySelector('[data-content-block] p')), true);
      assert.deepEqual(errors, []);
      console.log(`PASS incremental text and search preserve DOM ${width}`);
      await page.close();
    }
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
