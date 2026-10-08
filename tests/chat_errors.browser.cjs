// Offline fixture only: terminal errors are turn-local UI, not erased history.
const assert = require('node:assert/strict');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const origin = process.env.FUSE_BROWSER_ORIGIN || 'http://127.0.0.1:8099';
assert.match(origin, /^http:\/\/127\.0\.0\.1:\d+$/);

(async () => {
  const browser = await chromium.launch({ args: ['--no-sandbox'] });
  try {
    {
      const page = await browser.newPage();
      const errors = [];
      page.on('pageerror', error => errors.push(error.message));
      await page.route('**/*', route => new URL(route.request().url()).origin === origin ? route.continue() : route.abort());
      await page.goto(origin);
      await page.waitForFunction(() => window.fuseToolFixture);
      await page.evaluate(() => {
        window.failedRows = [
          { role: 'user', content: 'First request' },
          { role: 'assistant', content: '', error: 'No allowed providers are available for the selected model.' },
        ];
        fuseToolFixture.mountThread(failedRows);
      });
      assert.equal(await page.locator('[data-err]:visible').count(), 1, 'final failure is visible');
      assert.equal(await page.locator('[data-continue]:visible').count(), 1, 'a failed final turn offers Continue');
      // Provider retries are Fuse status lines, never tool rows.
      await page.evaluate(() => fuseToolFixture.mountThread([failedRows[0], { ...failedRows[1],
        notice: 'Provider error (503) — retrying this step in 2s (attempt 1 of 4).\nProvider error (503) — retrying this step in 6s (attempt 2 of 4).' }]));
      assert.equal(await page.locator('[data-notice]:visible .chat-notice-line').count(), 2, 'retry notices render as status lines');
      assert.equal(await page.locator('[data-tool="fuse-steer"]').count(), 0, 'notices are not tool rows');
      assert.equal(await page.locator('[data-err]:visible').count(), 1, 'final error still shows under the notices');
      await page.evaluate(() => fuseToolFixture.mountThread(failedRows));
      await page.evaluate(() => {
        // The accepted snapshot adds a user message before output arrives.
        fuseToolFixture.mountThread([...failedRows, { role: 'user', content: 'Try another model' }, { role: 'assistant' }], { live: true });
      });
      assert.equal(await page.locator('[data-err]:visible').count(), 0, 'next accepted turn clears the box before output');
      assert.equal(await page.locator('.is-assistant:visible').count(), 0, 'error-only turn leaves no empty footer');
      assert.ok((await page.evaluate(() => fuseToolFixture.snapshot()[1].error)).includes('No allowed providers'), 'stored error is preserved');
      await page.evaluate(() => fuseToolFixture.stream(3, { content: 'New reply', segments: [{ type: 'content', text: 'New reply' }] }));
      assert.equal(await page.locator('[data-content-block]').textContent(), 'New reply');
      assert.equal(await page.locator('[data-err]:visible').count(), 0);
      await page.evaluate(() => {
        fuseToolFixture.mountThread([
          failedRows[0], { ...failedRows[1], content: 'Partial output', tools: [{ id: 'failed-tool', name: 'read_file', status: 'error', error: 'Permission denied' }], segments: [{ type: 'content', text: 'Partial output' }, { type: 'tool', id: 'failed-tool' }] },
          { role: 'user', content: 'Next request' }, { role: 'assistant', error: 'Latest provider failure' },
        ]);
      });
      assert.equal(await page.locator('[data-err]:visible').textContent(), 'Latest provider failure', 'only current failure is visible');
      assert.equal(await page.locator('[data-content-block]').textContent(), 'Partial output', 'partial output is preserved');
      assert.equal(await page.locator('[data-tool="read_file"][data-state="error"]').count(), 1, 'historical tool failures are preserved');
      await page.evaluate(() => fuseToolFixture.repaint());
      assert.equal(await page.locator('[data-err]:visible').count(), 1, 'repaint does not revive old error');
      await page.evaluate(() => fuseToolFixture.mountThread(failedRows));
      assert.equal(await page.locator('[data-err]:visible').count(), 1, 'rewinding to failed turn restores its error');
      await page.evaluate(() => fuseToolFixture.mountThread([
        ...failedRows, ...Array.from({ length: 12 }, (_, i) => ({ role: i % 2 ? 'assistant' : 'user', content: `Later ${i}` })),
      ]));
      assert.equal(await page.locator('[data-err]:visible').count(), 0, 'hydrated history does not revive old errors');
      assert.equal(await page.locator('[data-message-id="fixture-message-1"]').isVisible(), false, 'old error-only row is not deferred as an empty placeholder');
      const created = await page.evaluate(async () => fuseToolFixture.api('/chat/threads', { method: 'POST', body: JSON.stringify({ model: 'offline', tools: [], command_id: crypto.randomUUID(), messages: failedRows }) }));
      await page.goto(`${origin}/chat?t=${created.id}`);
      await page.waitForFunction(() => fuseToolFixture.snapshot().length === 2);

      assert.equal(await page.locator('[data-err]:visible').count(), 1, 'reload retains unresolved error');
      assert.equal(await page.locator('[data-continue]:visible').count(), 1, 'reload keeps Continue on the failed final turn');
      await page.locator('[data-chat-input]').fill('Next accepted message');
      await page.locator('[data-chat-send]').click();
      await page.waitForFunction(() => fuseToolFixture.snapshot().some(row => row.role === 'user' && row.content === 'Next accepted message'));
      assert.equal(await page.locator('[data-err]:visible').count(), 0, 'HTTP acknowledgment updates existing error node');
      assert.equal(await page.locator('[data-continue]:visible').count(), 0, 'Continue leaves with the error');
      await page.waitForFunction(() => !fuseToolFixture.runInfo());
      await page.reload();
      await page.waitForFunction(() => fuseToolFixture.snapshot().length === 4);
      assert.equal(await page.locator('[data-err]:visible').count(), 0, 'reload after continuation keeps prior error hidden');
      assert.ok((await page.evaluate(() => fuseToolFixture.snapshot()[1].error)).includes('No allowed providers'));
      assert.deepEqual(errors, []);
      await page.close();
      console.log('PASS error lifecycle');
    }
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
