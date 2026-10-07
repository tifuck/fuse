// Real click/disclosure interactions with mocked read-only child responses.
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const origin = process.env.FUSE_BROWSER_ORIGIN || 'http://127.0.0.1:8098';
assert.match(origin, /^http:\/\/127\.0\.0\.1:\d+$/);
(async () => {
  const browser = await chromium.launch({ headless: true, executablePath: process.env.CHROMIUM_PATH || undefined, args: ['--no-sandbox'] });
  try {
    await fs.mkdir('/tmp/opencode/agent-ui-artifacts', { recursive: true });
    for (const width of [390, 1440]) {
      const page = await browser.newPage({ viewport: { width, height: 900 }, hasTouch: width === 390 });
      const errors = [];
      let requests = 0;
      let missing = false;
      page.on('pageerror', e => errors.push(e.message));
      await page.route('**/*', route => new URL(route.request().url()).origin === origin ? route.continue() : route.abort());
      await page.route('**/chat/runs/fixture-child', route => {
        requests++;
        return route.fulfill({ status: missing ? 404 : 200, contentType: 'application/json', body: JSON.stringify(missing ? { detail: 'Saved child is unavailable' } : {
          id: 'fixture-child', event_cursor: 0, message_id: 'fixture-child-message', description: 'Completed audit child', status: 'completed',
          message: { role: 'assistant', content: 'Existing child transcript loaded.', tools: [{ id: 'child-read', name: 'read_file', status: 'done', path: 'README.md', preview: 'Child file contents' }] }, messages: [],
        }) });
      });
      await page.goto(origin);
      await page.waitForFunction(() => Boolean(window.fuseToolFixture));
      // Phones open on the chat list; leave it the way a reader would.
      if (await page.locator('[data-chat].is-history').count()) await page.locator('.chat-history [data-chat-new]').click();
      await page.evaluate(() => {
        fuseToolFixture.mount([{ tools: [{ id: 'open-child', name: 'spawn_subagent', status: 'done', child_id: 'fixture-child', description: 'Completed audit child' }] }]);
      });
      const row = page.locator('[data-step="tool-open-child"]');
      await row.waitFor();
      // A reconciliation repaints the row without disturbing the transcript.
      await page.evaluate(() => fuseToolFixture.agents([{ id: 'fixture-child', status: 'completed', summary: 'Fresh reconciliation output' }]));
      await page.evaluate(() => fuseToolFixture.update('open-child', { description: 'Update while disclosing' }));
      await page.waitForFunction(() => document.querySelector('[data-step="tool-open-child"]').textContent.includes('Update while disclosing'));
      assert.match(await row.innerText(), /Update while disclosing/, 'deferred paint applies the latest reconciled output');
      await page.evaluate(() => fuseToolFixture.todo.openAgent('fixture-child'));
      const panel = page.getByRole('dialog', { name: 'Agents and commands', exact: true });
      await panel.getByText('Existing child transcript loaded.', { exact: true }).waitFor();
      assert.equal(requests, 1, 'Open action makes exactly one request');
      assert.equal(await panel.locator('.chat-agents-copy').isVisible(), false, 'child replaces the overview heading');
      assert.equal(await panel.locator('[data-agent-head]').isVisible(), true, 'one header contains child navigation');
      assert.equal(await panel.locator('[data-agent-live-dock]').isVisible(), false, 'finished child has no empty status row');
      const box = await panel.boundingBox();
      console.log(width, 'panel bounds', box);
      assert.ok(box.width > (width === 390 ? 350 : 200), 'panel occupies visible viewport space');
      assert.equal(await panel.evaluate(el => {
        const r = el.getBoundingClientRect();
        return el.contains(document.elementFromPoint(r.left + r.width / 2, r.top + 80));
      }), true, 'panel is not covered by its backdrop');
      await page.screenshot({ path: `/tmp/opencode/agent-ui-artifacts/open-${width}.png` });
      await panel.locator('[data-agents-close]').click();
      await panel.waitFor({ state: 'hidden' });
      const beforeReopen = requests;
      await page.evaluate(() => fuseToolFixture.todo.openAgent('fixture-child'));
      await panel.getByText('Existing child transcript loaded.', { exact: true }).waitFor();
      assert.ok(requests > beforeReopen, 're-opening fetches the child again');
      await panel.locator('[data-agents-close]').click();
      await panel.waitFor({ state: 'hidden' });
      missing = true;
      await page.evaluate(() => fuseToolFixture.todo.openAgent('fixture-child'));
      await panel.getByText(/Saved child is unavailable/).waitFor();
      await page.screenshot({ path: `/tmp/opencode/agent-ui-artifacts/error-${width}.png` });
      missing = false;
      await panel.getByRole('button', { name: 'Retry', exact: true }).click();
      await panel.getByText('Existing child transcript loaded.', { exact: true }).waitFor();
      assert.equal(await panel.locator('[data-agent-error]').isVisible(), false);
      if (width === 390) {
        await page.goBack();
        await panel.locator('[data-agent-view]').waitFor({ state: 'hidden' });
        assert.equal(await panel.isVisible(), true, 'mobile Back returns from child to the agents list first');
        await page.goBack();
        await panel.waitFor({ state: 'hidden' });
      }
      assert.deepEqual(errors, []);
      await page.close();
      console.log(`PASS Open agent, panel recovery, and visible errors ${width}`);
    }
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
