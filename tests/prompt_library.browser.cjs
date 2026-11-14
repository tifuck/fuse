// Prompt library: tiles on an empty chat, the category sheet, preview, insert
// with tab-stop fields, the one-time blank warning, /prompts, and the phone
// layout. The chat config is swapped in before chat.js reads it so a coding
// chat (project prompts usable) and a plain chat can both be exercised.
const assert = require('node:assert/strict');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const origin = process.env.FUSE_BROWSER_ORIGIN || 'http://127.0.0.1:8099';
assert.match(origin, /^http:\/\/127\.0\.0\.1:\d+$/);

const withConfig = (coding) => `(() => { const swap = () => { const el = document.getElementById('chat-config');
  if (!el || el.dataset.lib) return; const cfg = JSON.parse(el.textContent); cfg.coding = ${coding}; el.textContent = JSON.stringify(cfg); el.dataset.lib = '1'; };
  new MutationObserver(swap).observe(document, { childList: true, subtree: true }); swap(); })()`;

(async () => {
  const browser = await chromium.launch({ headless: true, executablePath: process.env.CHROMIUM_PATH || undefined, args: ['--no-sandbox'] });
  const errors = [];
  const open = async (viewport, coding, extra = {}) => {
    const context = await browser.newContext({ viewport, reducedMotion: 'reduce', ...extra });
    await context.addInitScript(withConfig(coding));
    const page = await context.newPage();
    page.on('pageerror', (error) => errors.push(String(error)));
    await page.goto(origin + '/chat');
    await page.locator('[data-sync-label]').filter({ hasText: 'Live sync' }).waitFor({ state: 'attached' });
    await page.locator('[data-library-cat]').first().waitFor();
    return { context, page };
  };
  const selection = (page) => page.evaluate(() => { const i = document.querySelector('#chat-input'); return i.value.slice(i.selectionStart, i.selectionEnd); });
  const setBox = (page, value) => page.evaluate((v) => { const i = document.querySelector('#chat-input'); i.value = v; i.dispatchEvent(new Event('input', { bubbles: true })); i.blur(); }, value);
  try {
    // Desktop, coding chat.
    {
      const { context, page } = await open({ width: 1440, height: 900 }, true);
      assert.equal(await page.locator('.chat-library-tile').count(), 10);
      const center = await page.evaluate(() => { const t = document.querySelector('[data-chat-thread]').getBoundingClientRect(), g = document.querySelector('.chat-library-tiles').getBoundingClientRect();
        return [t.left + t.width / 2 - (g.left + g.width / 2), t.top + t.height / 2 - (g.top + g.height / 2)]; });
      assert.ok(Math.abs(center[0]) < 12 && Math.abs(center[1]) < 24, `tiles centered in the thread: ${center}`);
      // A tile swaps the grid for that category's prompt cards under a back bar.
      await page.click('[data-library-cat="perf"]');
      assert.equal(await page.locator('[data-chat-library] [data-library-insert]').count(), 6);
      assert.match(await page.locator('.chat-library-bar-title').innerText(), /Performance/);
      assert.ok(await page.evaluate(() => document.activeElement.matches('[data-library-home]')), 'focus on the back button');
      await page.keyboard.press('Escape');
      assert.equal(await page.locator('[data-library-cat]').count(), 10, 'Escape returns to the categories');
      assert.ok(await page.evaluate(() => document.activeElement.matches('[data-library-cat="perf"]')), 'focus back on the tile');
      await page.click('[data-library-cat="perf"]');
      await page.click('[data-library-home]');
      assert.equal(await page.locator('[data-library-cat]').count(), 10, 'the back button returns to the categories');
      // /prompts opens the sheet: search, preview, modes and modifiers.
      await page.fill('#chat-input', '/prompts latency');
      await page.keyboard.press('Enter');
      const sheet = page.locator('.chat-library-sheet');
      await sheet.waitFor();
      const box = await sheet.boundingBox(), composer = await page.locator('.chat-box').boundingBox();
      assert.ok(box.width <= 769 && box.y + box.height <= composer.y, `sheet placement ${JSON.stringify({ box, composer })}`);
      const row = await page.locator('.chat-library-search').boundingBox();
      assert.ok(row.height < 48, `search row is one line: ${row.height}`);
      await page.fill('[data-library-search]', 'flaky');
      assert.equal(await page.locator('.chat-library-item').count(), 1);
      await page.fill('[data-library-search]', 'zzzz');
      assert.ok(await page.locator('.chat-library-empty').isVisible());
      await page.fill('[data-library-search]', 'latency');
      await page.click('[data-library-preview="perf-latency"]');
      await page.click('[data-library-mode="audit_fix"]');
      await page.click('[data-library-mod="subagents"]');
      await page.keyboard.press('Escape');
      assert.ok(await page.locator('.chat-library-list').isVisible(), 'Escape in the preview goes back to the list');
      await page.click('[data-library-preview="perf-latency"]');
      await page.click('[data-library-mode="audit_fix"]');
      await page.click('[data-library-mod="subagents"]');
      await page.click('[data-library-insert-preview]');
      assert.ok(!(await sheet.isVisible()), 'insert closes the sheet');
      const text = await page.inputValue('#chat-input');
      assert.ok(text.startsWith('Scope: the request and job paths') && text.includes('explore subagents') && text.includes('Then fix every confirmed'));
      assert.equal(await selection(page), 'the request and job paths in this repository');
      assert.ok(!(await page.locator('[data-chat-library]').isVisible()), 'hidden while composing');
      await page.keyboard.type('app/web');
      await page.keyboard.press('Tab');
      assert.equal(await selection(page), 'app/web', 'Tab reselects the field being filled');
      await setBox(page, '');
      assert.ok(await page.locator('[data-chat-library]').isVisible(), 'back when the box is empty');
      // A required blank: selected, Tab to the next field, and Enter warns once.
      await page.click('[data-library-cat="build"]');
      await page.click('[data-chat-library] [data-library-insert="fix-bug"]');
      assert.equal(await selection(page), '{{What happens, and what should happen instead}}');
      await page.keyboard.press('Tab');
      assert.equal(await selection(page), 'unknown, find a reproduction');
      await page.keyboard.press('Enter');
      assert.ok((await page.inputValue('#chat-input')).startsWith('Bug: {{'), 'first Enter is held');
      assert.match(await page.locator('body').innerText(), /is still blank/);
      // /prompts with and without a query.
      await page.fill('#chat-input', '/prompts security');
      await page.keyboard.press('Enter');
      await sheet.waitFor();
      assert.ok((await page.locator('.chat-library-item').count()) >= 8);
      await page.keyboard.press('Escape');
      assert.ok(!(await sheet.isVisible()));
      await page.fill('#chat-input', '/prompts');
      await page.keyboard.press('Enter');
      await sheet.waitFor();
      await page.click('.chat-library-backdrop', { position: { x: 5, y: 5 } });
      assert.ok(!(await sheet.isVisible()), 'backdrop closes the sheet');
      assert.ok((await page.evaluate(() => document.scrollingElement.scrollWidth - document.documentElement.clientWidth)) <= 1);
      await context.close();
    }
    // Plain chat: project prompts are dimmed and refuse to insert.
    {
      const { context, page } = await open({ width: 1280, height: 800 }, false);
      await page.click('[data-library-cat="bugs"]');
      assert.equal(await page.locator('.chat-library-tile[aria-disabled="true"]').count(), 7);
      await page.click('[data-library-insert="logic"]', { force: true });
      assert.equal(await page.inputValue('#chat-input'), '');
      await context.close();
    }
    // Phones: two columns of tiles centered with a margin, a category as one
    // prompt per row (back returns), and /prompts as a full-width bottom sheet.
    for (const [width, height] of [[390, 844], [360, 640]]) {
      const { context, page } = await open({ width, height }, true, { hasTouch: true, isMobile: true });
      if (await page.locator('[data-chat-new]').isVisible()) { await page.click('[data-chat-new]'); await page.waitForTimeout(500); }
      assert.ok(await page.locator('[data-chat-library]').isVisible());
      assert.ok((await page.evaluate(() => document.scrollingElement.scrollWidth - document.documentElement.clientWidth)) <= 1, `${width}: no sideways scroll`);
      const widths = await page.locator('.chat-library-tile').evaluateAll((els) => els.map((e) => e.getBoundingClientRect().width));
      assert.ok(widths.length === 10 && Math.min(...widths) > 110, `${width}: tiles ${widths}`);
      const fits = await page.evaluate(() => { const t = document.querySelector('[data-chat-thread]').getBoundingClientRect(), g = document.querySelector('.chat-library-tiles').getBoundingClientRect();
        return g.top >= t.top - 1 && g.bottom <= t.bottom + 1 && g.width < t.width * 0.9 && Math.abs(t.left + t.width / 2 - (g.left + g.width / 2)) < 2; });
      assert.ok(fits, `${width}: the tiles sit centered with a margin, without overflowing`);
      await page.tap('[data-library-cat="security"]');
      const lefts = await page.locator('.chat-library-tile.is-prompt').evaluateAll((els) => els.map((e) => Math.round(e.getBoundingClientRect().left)));
      assert.ok(lefts.length === 8 && new Set(lefts).size === 1, `${width}: one prompt per row ${lefts}`);
      assert.ok((await page.evaluate(() => document.scrollingElement.scrollWidth - document.documentElement.clientWidth)) <= 1, `${width}: prompts, no sideways scroll`);
      await page.goBack();
      await page.waitForTimeout(300);
      assert.equal(await page.locator('[data-library-cat]').count(), 10, `${width}: back returns to the categories`);
      await page.fill('#chat-input', '/prompts security');
      await page.keyboard.press('Enter');
      const sheet = page.locator('.chat-library-sheet');
      await sheet.waitFor();
      const b = await sheet.boundingBox();
      assert.ok(Math.abs(b.width - width) < 2 && Math.abs(b.y + b.height - height) < 2, `${width}: bottom sheet ${JSON.stringify(b)}`);
      assert.ok(!(await page.evaluate(() => document.activeElement.matches('input'))), `${width}: no keyboard`);
      await page.tap('[data-library-preview="sec-authz"]');
      assert.ok(await page.locator('.chat-library-preview').isVisible());
      await page.goBack();
      await page.waitForTimeout(300);
      assert.ok(!(await sheet.isVisible()), `${width}: back closes the sheet`);
      await page.tap('[data-library-cat="design"]');
      await page.tap('[data-chat-library] [data-library-insert="tradeoffs"]');
      assert.ok((await page.inputValue('#chat-input')).startsWith('Decision: {{'), `${width}: inserted`);
      await context.close();
    }
    assert.deepEqual(errors, []);
    console.log('prompt library ok');
  } finally {
    await browser.close();
  }
})().catch((error) => { console.error(error); process.exit(1); });
