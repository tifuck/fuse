// Chat-page chrome popovers no other suite pins.
//
// Already covered, so not repeated here:
//   recovery_states.browser.cjs — ask vs a walled-off Auto, bypass persistence
//     on the server, Escape hides the permissions menu, the phone sheet fits,
//     and a rewritten button label cannot shift a right-pinned picker.
//   context_meter.browser.cjs — the context popover fits a narrow viewport.
//
// The fixture's /chat lists only "offline". A richer catalog is injected into
// the page's own #chat-config before chat.js reads it, so the picker, the
// pin-on-open order, and per-model effort can be exercised without a fixture
// change. Permission mode, the bell, and the dial still use real routes.
const assert = require('node:assert/strict');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const origin = process.env.FUSE_BROWSER_ORIGIN || 'http://127.0.0.1:8099';
assert.match(origin, /^http:\/\/127\.0\.0\.1:\d+$/);

const FAMILIES = [
  { type: 'grok', title: 'Grok', models: [
    { id: 'grok-short', name: 'Grok', reasoning: ['low', 'high'], reasoning_default: 'low' },
    { id: 'grok-long', name: 'Grok Long', reasoning: ['none', 'high'], reasoning_default: 'none' },
  ] },
  { type: 'llama', title: 'Test', models: [
    { id: 'offline', name: 'Offline fixture', reasoning: ['none', 'low', 'medium', 'high'], reasoning_default: 'medium' },
  ] },
];

(async () => {
  const browser = await chromium.launch({ headless: true, executablePath: process.env.CHROMIUM_PATH || undefined, args: ['--no-sandbox'] });
  const errors = [];
  try {
    const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, reducedMotion: 'reduce' });
    await context.addInitScript(families => {
      const swap = () => {
        const el = document.getElementById('chat-config');
        if (!el || el.dataset.menus) return;
        let config;
        try { config = JSON.parse(el.textContent); } catch { return; }
        config.families = families;
        config.preselect = '';
        el.textContent = JSON.stringify(config);
        el.dataset.menus = '1';
      };
      new MutationObserver(swap).observe(document, { childList: true, subtree: true });
      swap();
    }, FAMILIES);
    await context.route('**/*', route => {
      const url = new URL(route.request().url());
      if (url.origin !== origin) return route.abort();
      if (url.pathname === '/notifications/status') {
        return route.fulfill({ contentType: 'application/json', body: JSON.stringify({ enabled: true, vapid: 'menus-vapid', count: 0 }) });
      }
      return route.continue();
    });
    context.on('page', page => page.on('pageerror', error => errors.push(String(error))));
    const page = await context.newPage();
    page.on('pageerror', error => errors.push(String(error)));

    const box = async selector => page.evaluate(sel => {
      const el = document.querySelector(sel);
      const r = el.getBoundingClientRect();
      return {
        left: r.left, right: r.right, top: r.top, bottom: r.bottom,
        width: window.innerWidth, height: window.innerHeight,
        overflow: document.scrollingElement.scrollWidth - document.documentElement.clientWidth,
        hidden: el.hidden, sheet: el.classList.contains('is-sheet'),
      };
    }, selector);
    const inside = (placed, label) => {
      assert.ok(placed.left >= -1 && placed.right <= placed.width + 1, `${label} stays inside horizontally: ${JSON.stringify(placed)}`);
      assert.ok(placed.top >= -1 && placed.bottom <= placed.height + 1, `${label} stays inside vertically: ${JSON.stringify(placed)}`);
      assert.ok(placed.overflow <= 1, `${label} does not widen the page: ${placed.overflow}`);
    };
    const live = () => page.locator('[data-sync-label]').filter({ hasText: 'Live sync' }).waitFor({ state: 'attached' });
    const pickerBtn = () => page.locator('.chat-picker-btn');
    const picker = () => page.locator('.chat-picker-menu');
    const optionIds = () => page.locator('.chat-picker-list [role="option"]').evaluateAll(nodes => nodes.map(node => node.getAttribute('data-id')));

    await page.goto(origin + '/chat');
    await live();
    await page.waitForFunction(() => (document.querySelector('.chat-picker-btn span') || {}).textContent === 'Grok');

    // Selected model is pinned to the top of its provider group on open, and
    // the menu stays put when the button name gets longer.
    {
      assert.equal(await pickerBtn().locator('span').innerText(), 'Grok', 'preferred provider wins when nothing is stored');
      // The chip uppercases the effort through CSS; the stored label stays "Low".
      assert.equal(await pickerBtn().locator('small').evaluate(el => el.textContent), 'Low', 'the model default effort shows on the button');
      await pickerBtn().click();
      await picker().waitFor();
      let placed = await box('.chat-picker-menu');
      inside(placed, 'desktop picker');
      assert.equal(placed.sheet, false);
      assert.deepEqual(await optionIds(), ['grok-short', 'grok-long', 'offline'], `selected model leads its group: ${await optionIds()}`);
      await page.locator('.chat-picker-list [data-id="grok-long"]').click();
      await page.waitForFunction(() => document.querySelector('.chat-picker-btn span').textContent === 'Grok Long');
      assert.equal(await picker().isVisible(), true, 'picking a different model keeps the menu open');
      // The pin is taken when the menu opens, so a selection made inside it
      // checks in place instead of jumping the list under the pointer.
      assert.deepEqual(await optionIds(), ['grok-short', 'grok-long', 'offline'], 'order stays pinned to the model that was current at open');
      assert.equal(await page.locator('.chat-picker-list [data-id="grok-long"]').getAttribute('aria-selected'), 'true');
      // A long name wraps the composer, so the menu follows the button. What
      // must hold is that it stays inside the viewport either way.
      inside(await box('.chat-picker-menu'), 'picker after a long name');
      await page.keyboard.press('Escape');
      await picker().waitFor({ state: 'hidden' });
      await pickerBtn().click();
      await picker().waitFor();
      assert.deepEqual(await optionIds(), ['grok-long', 'grok-short', 'offline'], 'reopening pins the model selected last time');
      await page.locator('.chat-thread').click({ position: { x: 8, y: 8 } });
      await picker().waitFor({ state: 'hidden' });
    }

    // Effort is per model, labelled on the button, and survives a reload.
    {
      await pickerBtn().click();
      const slider = page.locator('[data-effort-slider]');
      await slider.waitFor();
      assert.equal(await slider.getAttribute('aria-valuetext'), 'Off', 'none is labelled Off');
      await slider.fill('1');
      await page.waitForFunction(() => document.querySelector('.chat-picker-btn small').textContent === 'High');
      assert.equal(await page.locator('.chat-picker-thinking-value').innerText(), 'High');
      await page.keyboard.press('Escape');
      await page.reload();
      await live();
      await page.waitForFunction(() => document.querySelector('.chat-picker-btn small')?.textContent === 'High');
      assert.equal(await pickerBtn().locator('span').innerText(), 'Grok Long', 'the selected model survives a reload');
      await pickerBtn().click();
      await page.locator('.chat-picker-list [data-id="grok-short"]').click();
      await page.waitForFunction(() => document.querySelector('.chat-picker-btn small').textContent === 'Low');
      assert.equal(await page.locator('[data-effort-slider]').getAttribute('aria-valuetext'), 'Low', 'the short model keeps its own effort');
      await page.keyboard.press('Escape');
    }

    // Ask / Bypass labels, and the mode follows the thread across a switch.
    {
      const chip = page.locator('[data-perm-chip]');
      const label = page.locator('[data-perm-chip-label]');
      assert.equal(await label.innerText(), 'Bypass', 'ask mode labels the chip Bypass');
      assert.equal(await chip.getAttribute('aria-pressed'), 'false');
      const first = await page.evaluate(async () => (await fuseToolFixture.api('/chat/threads', {
        method: 'POST', body: JSON.stringify({ model: 'offline', tools: [], title: 'Permissions', command_id: crypto.randomUUID(), messages: [
          { id: crypto.randomUUID(), role: 'user', content: 'Pin the permission mode' },
          { id: crypto.randomUUID(), role: 'assistant', content: 'Done' },
        ] }),
      })).id);
      await page.goto(origin + '/chat?t=' + first);
      await live();
      await chip.click();
      const menu = page.locator('.chat-perms-menu');
      await menu.waitFor();
      inside(await box('.chat-perms-menu'), 'desktop permissions menu');
      await menu.locator('[data-perms-mode="bypass"]').click();
      await page.waitForFunction(() => document.querySelector('[data-perm-chip]').getAttribute('aria-pressed') === 'true');
      assert.equal(await menu.locator('[data-perms-mode="bypass"]').getAttribute('aria-checked'), 'true');
      await page.locator('.chat-thread').click({ position: { x: 8, y: 8 } });
      await menu.waitFor({ state: 'hidden' });

      const second = await page.evaluate(async () => (await fuseToolFixture.api('/chat/threads', {
        method: 'POST', body: JSON.stringify({ model: 'offline', tools: [], title: 'Still asking', command_id: crypto.randomUUID(), messages: [
          { id: crypto.randomUUID(), role: 'user', content: 'A second thread stays on ask' },
          { id: crypto.randomUUID(), role: 'assistant', content: 'Done' },
        ] }),
      })).id);
      await page.goto(origin + '/chat?t=' + second);
      await live();
      await page.waitForFunction(() => document.querySelector('[data-perm-chip-label]').textContent === 'Bypass');
      assert.equal(await chip.getAttribute('aria-pressed'), 'false', 'a new thread asks');

      await page.goto(origin + '/chat?t=' + first);
      await live();
      await page.waitForFunction(() => document.querySelector('[data-perm-chip-label]').textContent === 'Bypass on');
      assert.equal(await chip.getAttribute('aria-pressed'), 'true', 'reopening a bypass thread keeps Bypass on');
      await page.reload();
      await live();
      await page.waitForFunction(() => document.querySelector('[data-perm-chip-label]').textContent === 'Bypass on');
      await page.goto(origin + '/chat?t=' + second);
      await live();
      await page.waitForFunction(() => document.querySelector('[data-perm-chip-label]').textContent === 'Bypass');
      assert.notEqual(second, first);
    }

    assert.deepEqual(errors, []);
    await context.close();

    // Phone: the picker and the permissions menu are sheets, and the quick
    // dial fans inside the viewport, disables what it cannot run, and closes.
    {
      const phone = await browser.newContext({ viewport: { width: 390, height: 844 }, reducedMotion: 'reduce', hasTouch: true, isMobile: true });
      await phone.addInitScript(families => {
        const swap = () => {
          const el = document.getElementById('chat-config');
          if (!el || el.dataset.menus) return;
          let config;
          try { config = JSON.parse(el.textContent); } catch { return; }
          config.families = families;
          config.preselect = '';
          el.textContent = JSON.stringify(config);
          el.dataset.menus = '1';
        };
        new MutationObserver(swap).observe(document, { childList: true, subtree: true });
        swap();
      }, FAMILIES);
      await phone.route('**/*', route => new URL(route.request().url()).origin === origin ? route.continue() : route.abort());
      const p = await phone.newPage();
      p.on('pageerror', error => errors.push(String(error)));
      const phoneBox = async selector => p.evaluate(sel => {
        const el = document.querySelector(sel);
        const r = el.getBoundingClientRect();
        return { left: r.left, right: r.right, top: r.top, bottom: r.bottom, width: innerWidth, height: innerHeight, sheet: el.classList.contains('is-sheet'), overflow: document.scrollingElement.scrollWidth - document.documentElement.clientWidth };
      }, selector);
      const phoneInside = (placed, label) => {
        assert.ok(placed.left >= -1 && placed.right <= placed.width + 1, `${label} stays inside: ${JSON.stringify(placed)}`);
        assert.ok(placed.bottom <= placed.height + 1, `${label} stays above the fold: ${JSON.stringify(placed)}`);
        assert.ok(placed.overflow <= 1, `${label} does not widen the page`);
      };
      await p.goto(origin + '/chat');
      await p.locator('[data-sync-label]').filter({ hasText: 'Live sync' }).waitFor({ state: 'attached' });
      if (await p.locator('[data-chat].is-history').count()) await p.locator('.chat-history [data-chat-new]').click();
      await p.locator('[data-chat-input]').blur();
      await p.locator('[data-fuse-dial]').waitFor();
      const hub = await phoneBox('[data-fuse-dial]');
      phoneInside(hub, 'quick dial hub');
      await p.locator('[data-dial-hub]').click();
      await p.locator('[data-fuse-dial].is-open').waitFor();
      const fan = await p.locator('.fuse-dial-sat').evaluateAll(nodes => nodes.map(node => ({
        label: node.getAttribute('aria-label'),
        off: node.getAttribute('aria-disabled'),
        box: node.getBoundingClientRect().toJSON(),
      })));
      assert.deepEqual(fan.map(item => item.label), ['Retry', 'Undo', 'Redo', 'Rewind', 'Agents', 'New chat']);
      for (const id of ['Retry', 'Undo', 'Redo', 'Rewind']) {
        assert.equal(fan.find(item => item.label === id).off, 'true', `${id} is disabled on an empty chat`);
      }
      assert.equal(fan.find(item => item.label === 'New chat').off, 'false');
      for (const item of fan) phoneInside({ ...item.box, width: 390, height: 844, overflow: 0 }, item.label);
      await p.locator('.fuse-dial-sat[data-action="retry"]').click({ force: true });
      await p.locator('[data-chat-hint]').waitFor();
      assert.match(await p.locator('[data-chat-hint]').innerText(), /Nothing to retry/);
      await p.locator('[data-fuse-dial].is-open').waitFor({ state: 'hidden' });
      await p.keyboard.press('Escape');

      await p.locator('.chat-picker-btn').click();
      await p.locator('.chat-picker-menu').waitFor();
      const sheet = await phoneBox('.chat-picker-menu');
      assert.equal(sheet.sheet, true, 'a phone picker is a sheet');
      phoneInside(sheet, 'phone model sheet');
      await p.locator('[data-picker-close]').click();
      await p.locator('.chat-picker-menu').waitFor({ state: 'hidden' });
      await p.locator('[data-perm-chip]').click();
      await p.locator('.chat-perms-menu').waitFor();
      const perms = await phoneBox('.chat-perms-menu');
      assert.equal(perms.sheet, true);
      phoneInside(perms, 'phone permissions sheet');
      await p.locator('[data-perms-close]').click();
      await p.locator('.chat-perms-menu').waitFor({ state: 'hidden' });
      assert.deepEqual(errors, []);
      await phone.close();
    }
    console.log('PASS chat chrome menus');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
