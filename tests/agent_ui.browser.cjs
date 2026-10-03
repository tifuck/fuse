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
    for (const width of [390, 1440]) for (const view of ['compact', 'cli']) {
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
      await page.evaluate(view => {
        fuseToolFixture.view(view);
        fuseToolFixture.mount([{ tools: [{ id: 'open-child', name: 'spawn_subagent', status: 'done', child_id: 'fixture-child', description: 'Completed audit child' }] }]);
      }, view);
      const row = page.locator('[data-step="tool-open-child"]');
      const toggle = row.locator('.chat-step-toggle');
      await toggle.click();
      assert.equal(await toggle.getAttribute('aria-expanded'), 'true', 'one disclosure click opens once');
      await page.evaluate(() => fuseToolFixture.agents([{ id: 'fixture-child', status: 'completed', summary: 'Fresh reconciliation output' }]));
      assert.equal(await toggle.getAttribute('aria-expanded'), 'true', 'reconciliation preserves expanded disclosure');
      await toggle.click();
      assert.equal(await toggle.getAttribute('aria-expanded'), 'false', 'one disclosure click closes once');
      const toggleBox = await toggle.boundingBox();
      await page.mouse.move(toggleBox.x + toggleBox.width / 2, toggleBox.y + toggleBox.height / 2);
      await page.mouse.down();
      await page.evaluate(() => fuseToolFixture.update('open-child', { summary: 'Update while disclosing' }));
      await page.mouse.up();
      await page.waitForTimeout(50);
      assert.equal(await toggle.getAttribute('aria-expanded'), 'true', 'reconciliation during disclosure gesture must not swallow or double-toggle the click');
      await page.waitForFunction(() => document.querySelector('[data-step="tool-open-child"]').textContent.includes('Update while disclosing'));
      assert.match(await row.innerText(), /Update while disclosing/, 'deferred paint applies the latest reconciled output after the click');
      const initialOpen = row.getByRole('button', { name: 'Open agent', exact: true });
      if (width === 390) await initialOpen.tap();
      else await initialOpen.click();
      const panel = page.getByRole('dialog', { name: 'Agents and commands', exact: true });
      await panel.getByText('Existing child transcript loaded.', { exact: true }).waitFor();
      assert.equal(requests, 1, 'Open action makes exactly one request');
      const box = await panel.boundingBox();
      console.log(view, width, 'panel bounds', box);
      assert.ok(box.width > (width === 390 ? 350 : 200), 'panel occupies visible viewport space');
      assert.equal(await panel.evaluate(el => {
        const r = el.getBoundingClientRect();
        return el.contains(document.elementFromPoint(r.left + r.width / 2, r.top + 80));
      }), true, 'panel is not covered by its backdrop');
      await page.screenshot({ path: `/tmp/opencode/agent-ui-artifacts/open-${view}-${width}.png` });
      if (view === 'compact') await panel.locator('[data-activity-summary]').click();
      const childToggle = panel.locator('[data-step="tool-child-read"] .chat-step-toggle');
      await childToggle.click();
      await page.evaluate(() => fuseToolFixture.pollAgent());
      assert.equal(await childToggle.getAttribute('aria-expanded'), 'true', 'child transcript reload preserves disclosure');
      await panel.locator('[data-agents-close]').click();
      await panel.waitFor({ state: 'hidden' });
      const beforeGestureOpen = requests;
      const openButton = row.getByRole('button', { name: 'Open agent', exact: true });
      const rect = await openButton.boundingBox();
      await page.mouse.move(rect.x + rect.width / 2, rect.y + rect.height / 2);
      await page.mouse.down();
      await page.evaluate(() => fuseToolFixture.update('open-child', { summary: 'Reconcile between pointer down and up' }));
      await page.mouse.up();
      await panel.waitFor({ state: 'visible', timeout: 2000 });
      await panel.getByText('Existing child transcript loaded.', { exact: true }).waitFor();
      assert.equal(requests, beforeGestureOpen + 1, 'a reconciliation during the pointer gesture must not swallow or duplicate Open');
      await panel.locator('[data-agents-close]').click();
      await panel.waitFor({ state: 'hidden' });
      missing = true;
      await row.getByRole('button', { name: 'Open agent', exact: true }).click();
      await panel.getByText(/Saved child is unavailable/).waitFor();
      await page.screenshot({ path: `/tmp/opencode/agent-ui-artifacts/error-${view}-${width}.png` });
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
      console.log(`PASS Open agent, disclosure, and visible errors ${view} ${width}`);
    }
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
