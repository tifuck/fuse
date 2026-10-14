// iPhone-only failure classes, checked in Chromium at iPhone size so they need
// no device: text inflation left on, text fields under 16px (iOS zooms into
// them and stays zoomed), an app that stays shorter than the screen after the
// keyboard closes (normal, stale and zoomed viewport reports), and tappable
// chrome under a faked status bar and home bar. Real WebKit-on-iOS rendering
// still needs a device; these pin the bug classes seen there.
const assert = require('node:assert/strict');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const origin = process.env.FUSE_BROWSER_ORIGIN || 'http://127.0.0.1:8099';
assert.match(origin, /^http:\/\/127\.0\.0\.1:\d+$/);

const W = 390;
const H = 844;
const KEYBOARD = 336;
// iPhone 15 portrait: Dynamic Island status bar and home indicator.
const SAFE = { top: 59, bottom: 34 };

(async () => {
  const browser = await chromium.launch({ headless: true, executablePath: process.env.CHROMIUM_PATH || undefined, args: ['--no-sandbox'] });
  const errors = [];
  const makePage = async () => {
    const context = await browser.newContext({ viewport: { width: W, height: H }, isMobile: true, hasTouch: true, reducedMotion: 'reduce' });
    await context.route('**/*', route => new URL(route.request().url()).origin === origin ? route.continue() : route.abort());
    // Headless Chromium has no on-screen keyboard: a scriptable visual
    // viewport stands in for the one iOS reports.
    await context.addInitScript(({ w, h }) => {
      const vv = new EventTarget();
      Object.assign(vv, { width: w, height: h, offsetTop: 0, offsetLeft: 0, pageTop: 0, pageLeft: 0, scale: 1 });
      Object.defineProperty(window, 'visualViewport', { configurable: true, get: () => vv });
      window.fakeViewport = patch => { Object.assign(vv, patch); vv.dispatchEvent(new Event('resize')); };
    }, { w: W, h: H });
    const page = await context.newPage();
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(origin);
    await live(page);
    return page;
  };
  const live = page => page.locator('[data-sync-label]').filter({ hasText: 'Live sync' }).waitFor({ state: 'attached' });
  const frames = page => page.evaluate(() => new Promise(done => requestAnimationFrame(() => requestAnimationFrame(done))));
  const drawerOpen = page => page.evaluate(() => document.querySelector('[data-chat]').classList.contains('is-history'));
  const appBottom = page => page.evaluate(() => Math.round(document.body.getBoundingClientRect().bottom));
  const reaches = async (page, bottom, label) => {
    try {
      await page.waitForFunction(b => Math.round(document.body.getBoundingClientRect().bottom) === b, bottom, { timeout: 3000 });
    } catch (_) {
      assert.fail(`${label}: the app ends at ${await appBottom(page)}px, not ${bottom}px`);
    }
  };

  const smallFields = page => page.evaluate(() => [...document.querySelectorAll('input, select, textarea')]
    .filter(el => !['checkbox', 'radio', 'range', 'color', 'file', 'button', 'submit', 'reset', 'image', 'hidden'].includes(el.type))
    .map(el => ({ field: el.outerHTML.slice(0, 120), size: parseFloat(getComputedStyle(el).fontSize) }))
    .filter(({ size }) => size < 16));

  // Tappable controls whose box reaches under the status bar or home bar. The
  // transcript scrolls under the header by design, so its rows are skipped;
  // so is anything another element covers (a finger cannot reach it).
  const unsafeControls = page => page.evaluate(({ top, bottom }) => {
    const out = [];
    for (const el of document.querySelectorAll('button, a[href], input, select, textarea, [role="button"]')) {
      if (el.closest('.chat-thread')) continue;
      const r = el.getBoundingClientRect();
      if (r.width < 1 || r.height < 1) continue;
      const x = r.left + r.width / 2;
      const y = r.top + r.height / 2;
      if (x < 0 || x >= innerWidth || y < 0 || y >= innerHeight) continue;
      const hit = document.elementFromPoint(x, y);
      if (!hit || !el.contains(hit)) continue;
      if (r.top < top - 0.5 || r.bottom > innerHeight - bottom + 0.5) out.push(`${el.outerHTML.slice(0, 110)} @ ${Math.round(r.top)}..${Math.round(r.bottom)}`);
    }
    return out;
  }, SAFE);
  const fakeInsets = async page => {
    await page.evaluate(({ top, bottom }) => {
      document.documentElement.style.setProperty('--safe-top', top + 'px');
      document.documentElement.style.setProperty('--safe-bottom', bottom + 'px');
    }, SAFE);
    await frames(page);
    await frames(page);
  };

  try {
    const page = await makePage();
    const thread = await page.evaluate(async () => (await fuseToolFixture.api('/chat/threads', {
      method: 'POST',
      body: JSON.stringify({
        model: 'offline', tools: [], title: 'iPhone viewport fixture', command_id: crypto.randomUUID(),
        messages: [
          { id: crypto.randomUUID(), role: 'user', content: 'Check the phone chrome' },
          { id: crypto.randomUUID(), role: 'assistant', content: 'Seeded answer for the iPhone viewport fixture.' },
        ],
      }),
    })).id);

    // Text inflation off, and no field small enough for iOS to zoom into.
    {
      const adjust = await page.evaluate(() => {
        const style = getComputedStyle(document.documentElement);
        return style.textSizeAdjust || style.webkitTextSizeAdjust;
      });
      assert.equal(adjust, '100%', 'iOS text inflation is off for the whole page');
      assert.deepEqual(await smallFields(page), [], 'every text field is at least 16px at phone width');
      console.log('PASS text inflation off and no text field under 16px');
    }

    // Keyboard open and close: the app always returns to the full screen.
    {
      await page.goto(origin + '/chat?t=' + thread);
      await live(page);
      await page.evaluate(h => fakeViewport({ height: h }), H);
      await reaches(page, H, 'at rest');
      const input = page.locator('[data-chat-form] textarea');
      const openKeyboard = async label => {
        await input.focus();
        await page.evaluate(h => fakeViewport({ height: h }), H - KEYBOARD);
        await page.waitForFunction(() => document.body.classList.contains('is-keyboard'));
        await reaches(page, H - KEYBOARD, `${label}: keyboard open`);
      };
      const blur = () => page.evaluate(() => document.activeElement.blur());

      await openKeyboard('normal close');
      await blur();
      await page.evaluate(h => fakeViewport({ height: h }), H);
      await reaches(page, H, 'normal close');

      // iOS keeps reporting the keyboard-sized viewport after it closes.
      await openKeyboard('stale report');
      await blur();
      await reaches(page, H, 'stale report');
      assert.equal(await page.evaluate(() => document.body.classList.contains('is-keyboard')), false, 'a stale report does not hold the keyboard layout');
      await page.evaluate(h => fakeViewport({ height: h }), H);

      // iOS zoomed into a field and stays zoomed after the keyboard closes.
      await openKeyboard('zoomed close');
      await page.evaluate(h => fakeViewport({ scale: 1.6, height: h / 1.6 }), H - KEYBOARD);
      await blur();
      await page.evaluate(h => fakeViewport({ height: h / 1.6 }), H);
      await reaches(page, H, 'zoomed close');
      await page.evaluate(h => fakeViewport({ scale: 1, height: h }), H);
      await reaches(page, H, 'zoom reset');
      console.log('PASS the app returns to the full screen after normal, stale and zoomed keyboard closes');
    }

    // Faked iPhone insets: no tappable chrome under the status bar or home bar.
    {
      await fakeInsets(page);
      assert.deepEqual(await unsafeControls(page), [], 'chat view: chrome clears the status bar and home bar');
      assert.deepEqual(await smallFields(page), [], 'no text field under 16px in the chat view');

      await page.locator('.chat-bar [data-history-toggle]').click();
      await page.waitForFunction(() => document.querySelector('[data-chat]').classList.contains('is-history'));
      await frames(page);
      assert.deepEqual(await unsafeControls(page), [], 'drawer: its head and account row clear the insets');
      assert.deepEqual(await smallFields(page), [], 'no text field under 16px in the drawer');
      await page.keyboard.press('Escape');
      await page.waitForFunction(() => !document.querySelector('[data-chat]').classList.contains('is-history'));

      await page.goto(origin + '/chat');
      await live(page);
      await fakeInsets(page);
      if (await drawerOpen(page)) {
        await page.keyboard.press('Escape');
        await page.waitForFunction(() => !document.querySelector('[data-chat]').classList.contains('is-history'));
        await frames(page);
      }
      assert.deepEqual(await unsafeControls(page), [], 'new chat: the one-row header clears the status bar');
      console.log('PASS chat view, drawer and new chat clear faked iPhone insets');
    }

    assert.deepEqual(errors, [], 'no page errors');
    await page.context().close();
  } finally {
    await browser.close();
  }
})().catch(error => {
  console.error(error);
  process.exit(1);
});
