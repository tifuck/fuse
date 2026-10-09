// Composer toast lifecycle against the isolated server only: a toast for an
// action in flight (Stopping…, Retrying…) lives exactly as long as the action,
// and a result toast keeps its full time instead of being wiped by a save.
const assert = require('node:assert/strict');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const origin = process.env.FUSE_BROWSER_ORIGIN || 'http://127.0.0.1:8099';
assert.match(origin, /^http:\/\/127\.0\.0\.1:\d+$/);

(async () => {
  const browser = await chromium.launch({ headless: true, executablePath: process.env.CHROMIUM_PATH || undefined, args: ['--no-sandbox'] });
  const errors = [];
  try {
    const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
    await context.route('**/*', route => (new URL(route.request().url()).origin === origin ? route.continue() : route.abort()).catch(() => {}));
    const page = await context.newPage();
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(origin);
    await page.locator('[data-sync-label]').filter({ hasText: 'Live sync' }).waitFor({ state: 'attached' });

    const toast = () => page.evaluate(() => {
      const el = document.querySelector('[data-chat-hint]');
      return el.hidden || !el.classList.contains('is-on') ? '' : el.textContent;
    });
    const runGone = () => page.waitForFunction(() => !fuseToolFixture.runInfo());
    const streaming = () => page.waitForFunction(() => document.body.classList.contains('is-streaming'));
    const idle = () => page.waitForFunction(() => !document.body.classList.contains('is-streaming') && !fuseToolFixture.runInfo(), null, { timeout: 30000 });
    const toastGoneWithin = async (ms, what) => {
      await page.waitForFunction(() => {
        const el = document.querySelector('[data-chat-hint]');
        return el.hidden || !el.classList.contains('is-on');
      }, null, { timeout: ms }).catch(() => { throw new Error(`${what} toast outlived its action by more than ${ms}ms`); });
    };
    const toastShows = text => page.waitForFunction(text => {
      const el = document.querySelector('[data-chat-hint]');
      return !el.hidden && el.classList.contains('is-on') && el.textContent === text;
    }, text, { timeout: 2000 });
    const send = async text => {
      await page.locator('[data-chat-input]').fill(text);
      await page.locator('[data-chat-send]').click();
    };

    // Stop: the toast leaves as soon as the run is stopped, not on a timer.
    await send('Queue fixture tool-step toast stop');
    await streaming();
    await page.waitForTimeout(400);
    await page.locator('[data-chat-send]').click();
    await toastShows('Stopping…');
    await runGone();
    await toastGoneWithin(1000, 'Stopping…');
    console.log('PASS Stopping… leaves when the run stops');

    // A slow stop keeps saying Stopping… for as long as it takes.
    await send('Queue fixture tool-step toast slow stop');
    await streaming();
    await page.route('**/cancel', async route => { await new Promise(r => setTimeout(r, 3200)); await route.continue(); }, { times: 1 });
    await page.waitForTimeout(400);
    await page.locator('[data-chat-send]').click();
    await page.waitForTimeout(2800);
    assert.ok(runActive(await page.evaluate(() => fuseToolFixture.runInfo())), 'the run is still stopping');
    assert.equal(await toast(), 'Stopping…', 'Stopping… stays while the stop is in flight');
    await runGone();
    await toastGoneWithin(1000, 'Stopping…');
    console.log('PASS Stopping… stays through a slow stop');

    // Retry says so until the replacement turn is accepted.
    await send('Plain message for toast retry');
    await streaming();
    await idle();
    await page.evaluate(() => [...document.querySelectorAll('.is-assistant')].at(-1).querySelector('[aria-label="Retry"]').click());
    await toastShows('Retrying…');
    await page.waitForFunction(() => fuseToolFixture.runInfo()?.status === 'running');
    await toastGoneWithin(1000, 'Retrying…');
    assert.ok(await page.evaluate(() => document.body.classList.contains('is-streaming')), 'the retried reply is still generating');
    await idle();
    console.log('PASS Retrying… leaves once the retry is running');

    // A result toast keeps its time; the save it triggers must not wipe it.
    await page.evaluate(() => [...document.querySelectorAll('.is-user')].at(-1).querySelector('[aria-label="More"]').click());
    await page.locator('.chat-pop button, [role=menuitem]').filter({ hasText: /^Delete$/ }).first().click();
    await page.locator('dialog button, .chat-confirm button').filter({ hasText: /^Delete$/ }).first().click();
    await page.waitForTimeout(1200);
    assert.equal(await toast(), 'Message deleted. /undo restores it.');
    console.log('PASS result toast survives the save it triggers');

    assert.deepEqual(errors, []);
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exit(1); });

function runActive(run) {
  return Boolean(run && ['queued', 'running'].includes(run.status));
}
