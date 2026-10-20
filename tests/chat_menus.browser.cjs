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
      // The chip shows the effort as the picker names it; the stored label is "Low".
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
      // x clears the collapsed sidebar's 12px peek edge (c8211a5e).
      await page.locator('[data-chat-thread]').click({ position: { x: 40, y: 8 } });
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
      assert.equal(await chip.evaluate(el => String(el.classList.contains('is-on'))), 'false');
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
      assert.equal(await chip.evaluate(el => String(el.classList.contains('is-bypass'))), 'false', 'ask keeps the blue shield');
      await menu.locator('[data-perms-mode="bypass"]').click();
      await page.waitForFunction(() => document.querySelector('[data-perm-chip]').classList.contains('is-on'));
      assert.equal(await chip.evaluate(el => String(el.classList.contains('is-bypass'))), 'true', 'bypass greys the shield');
      assert.equal(await menu.locator('[data-perms-mode="bypass"]').getAttribute('aria-checked'), 'true');
      // Radio keys: only the checked mode is a tab stop; arrows skip the
      // disabled Auto and pick the next mode.
      const radios = () => menu.locator('[data-perms-mode]').evaluateAll(els => els.map(el => [el.dataset.permsMode, el.tabIndex, el.getAttribute('aria-checked')]));
      assert.deepEqual((await radios()).map(r => r[1]), [-1, -1, 0]);
      await menu.locator('[data-perms-mode="bypass"]').focus();
      await page.keyboard.press('ArrowLeft');
      await page.waitForFunction(() => document.querySelector('[data-perms-mode="ask"]').getAttribute('aria-checked') === 'true');
      assert.equal(await page.evaluate(() => document.activeElement.dataset.permsMode), 'ask', 'ArrowLeft moves to Ask, past disabled Auto');
      await page.keyboard.press('ArrowRight');
      await page.waitForFunction(() => document.querySelector('[data-perms-mode="bypass"]').getAttribute('aria-checked') === 'true');
      await page.locator('[data-chat-thread]').click({ position: { x: 40, y: 8 } });
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
      assert.equal(await chip.evaluate(el => String(el.classList.contains('is-on'))), 'false', 'a new thread asks');

      await page.goto(origin + '/chat?t=' + first);
      await live();
      await page.waitForFunction(() => document.querySelector('[data-perm-chip-label]').textContent === 'Bypass on');
      assert.equal(await chip.evaluate(el => String(el.classList.contains('is-on'))), 'true', 'reopening a bypass thread keeps Bypass on');
      await page.reload();
      await live();
      await page.waitForFunction(() => document.querySelector('[data-perm-chip-label]').textContent === 'Bypass on');
      await page.goto(origin + '/chat?t=' + second);
      await live();
      await page.waitForFunction(() => document.querySelector('[data-perm-chip-label]').textContent === 'Bypass');
      assert.notEqual(second, first);

      // A new chat starts in the account default, and its mode can change
      // before the first send (the server applies it on create).
      const settings = body => page.evaluate(b => fuseToolFixture.api('/hosts/settings', { method: 'POST', body: JSON.stringify(b) }), body);
      await settings({ default_chat_permissions: 'bypass' });
      await page.goto(origin + '/chat');
      await live();
      await page.waitForFunction(() => document.querySelector('[data-perm-chip]').classList.contains('is-bypass'));
      await chip.click();
      await menu.waitFor();
      assert.match(await menu.locator('.chat-perms-empty').innerText(), /once the chat starts/);
      await menu.locator('[data-perms-mode="ask"]').click();
      await page.waitForFunction(() => !document.querySelector('[data-perm-chip]').classList.contains('is-on'));
      await page.keyboard.press('Escape');
      await menu.waitFor({ state: 'hidden' });
      await settings({ default_chat_permissions: 'ask' });
    }

    // Composer palettes and the picker keep focus in their text field and name
    // the highlighted option with aria-activedescendant.
    {
      const input = page.locator('[data-chat-input]');
      const active = (sel) => page.evaluate(s => {
        const el = document.querySelector(s);
        const id = el.getAttribute('aria-activedescendant');
        const opt = id && document.getElementById(id);
        return { id, focused: document.activeElement === el, role: opt?.getAttribute('role'), selected: opt?.getAttribute('aria-selected'), text: opt?.textContent };
      }, sel);
      await input.fill('/');
      await page.locator('[data-chat-slash]:not([hidden])').waitFor();
      const first = await active('[data-chat-input]');
      assert.equal(first.role, 'option', `the textarea points at the highlighted command: ${JSON.stringify(first)}`);
      assert.equal(first.selected, 'true');
      assert.equal(await input.getAttribute('aria-expanded'), null, 'a textarea carries no aria-expanded');
      await page.keyboard.press('ArrowDown');
      const second = await active('[data-chat-input]');
      assert.ok(second.focused && second.role === 'option' && second.id !== first.id, `ArrowDown moves the active descendant: ${JSON.stringify(second)}`);
      await page.keyboard.press('Escape');
      assert.equal(await input.getAttribute('aria-activedescendant'), null, 'closing the palette clears it');
      await input.fill('');

      await pickerBtn().click();
      await picker().waitFor();
      const pick = await active('.chat-picker-search');
      assert.ok(pick.focused && pick.role === 'option', `the picker search names the highlighted model: ${JSON.stringify(pick)}`);
      assert.equal(await page.locator('.chat-picker-list > [role="group"][aria-labelledby]').count() > 0, true, 'models are grouped by provider inside the listbox');
      await page.keyboard.press('ArrowDown');
      assert.notEqual((await active('.chat-picker-search')).id, pick.id, 'ArrowDown moves the picker highlight');
      await page.keyboard.press('Escape');
      await picker().waitFor({ state: 'hidden' });

      // Spelling: Alt+Enter opens the popover; suggestions, Ignore and Add are
      // one list walked by arrows while the textarea keeps focus.
      await page.waitForFunction(() => window.FuseComposerSpell?.dictStatus() === 'ready', null, { timeout: 15000 });
      await input.fill('teh cat ');
      await input.evaluate(el => { el.setSelectionRange(3, 3); window.FuseComposerSpell.refresh(); });
      await page.keyboard.press('Alt+Enter');
      await page.locator('[data-chat-spell-pop]:not([hidden])').waitFor();
      const options = await page.locator('[data-chat-spell-pop] [role="option"]').allTextContents();
      assert.deepEqual(options.slice(-2), ['Ignore', 'Add to dictionary'], `Ignore and Add are options in the list: ${options}`);
      for (let i = 1; i < options.length; i += 1) await page.keyboard.press('ArrowDown');
      assert.equal((await active('[data-chat-input]')).text, 'Add to dictionary', 'arrows reach the dictionary action');
      await page.keyboard.press('Escape');
      assert.equal(await input.getAttribute('aria-activedescendant'), null, 'closing the popover clears it');
      await input.fill('');
    }

    // Account: a disclosure (not an ARIA menu) that takes focus on open and
    // gives it back to its button on Escape.
    {
      const toggle = page.locator('.chat-history [data-account-toggle]');
      const collapsed = await page.evaluate(() => document.querySelector('[data-chat]').classList.contains('history-collapsed'));
      if (collapsed) await page.locator('.chat-bar [data-history-toggle]').click();
      await toggle.click();
      const state = () => page.evaluate(() => ({
        role: document.querySelector('.chat-history [data-account-menu]').getAttribute('role'),
        items: document.querySelectorAll('.chat-history [data-account-menu] [role="menuitem"]').length,
        inside: document.querySelector('.chat-history [data-account-menu]').contains(document.activeElement),
        expanded: document.querySelector('.chat-history [data-account-toggle]').getAttribute('aria-expanded'),
      }));
      assert.deepEqual(await state(), { role: 'group', items: 0, inside: true, expanded: 'true' });
      await page.keyboard.press('Escape');
      assert.equal(await toggle.evaluate(el => document.activeElement === el && el.getAttribute('aria-expanded')), 'false', 'Escape returns focus to the account button');
      if (collapsed) await page.locator('[data-history-toggle]:visible').first().click();
    }

    // Modal sheets: focus goes in, the page behind is inert, and Escape hands
    // focus back to the opener with the page live again.
    {
      const activeIs = sel => page.evaluate(s => document.activeElement === document.querySelector(s), sel);
      const stageInert = () => page.evaluate(() => document.querySelector('.chat-stage').inert);
      for (const [toggle, sheet, inside] of [
        ['[data-skills-toggle]', '[data-chat-skills]', '[data-skills-close]'],
        ['[data-memory-toggle]', '[data-chat-memory]', '[data-memory-instructions]'],
        ['[data-view-toggle]', '[data-view-menu]', null],
      ]) {
        await page.locator(toggle).click();
        await page.locator(sheet).waitFor();
        if (inside) assert.equal(await activeIs(inside), true, `${sheet} takes focus`);
        else assert.equal(await page.evaluate(s => document.querySelector(s).contains(document.activeElement), sheet), true, `${sheet} takes focus`);
        assert.equal(await stageInert(), true, `${sheet} makes the page behind inert`);
        await page.keyboard.press('Escape');
        await page.locator(sheet).waitFor({ state: 'hidden' });
        assert.equal(await stageInert(), false, `closing ${sheet} restores the page`);
        assert.equal(await activeIs(toggle), true, `closing ${sheet} returns focus to its toggle`);
      }
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
      assert.equal(await p.evaluate(() => document.activeElement === document.querySelector('.chat-picker-menu')), true, 'the phone sheet itself takes focus, no keyboard');
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
