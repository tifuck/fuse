// Optional Playwright regression suite against chat_browser_fixture.py only.
const assert = require('node:assert/strict');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const origin = process.env.FUSE_BROWSER_ORIGIN || 'http://127.0.0.1:8099';
assert.match(origin, /^http:\/\/127\.0\.0\.1:\d+$/);

(async () => {
  const browser = await chromium.launch({ headless: true, executablePath: process.env.CHROMIUM_PATH || undefined, args: ['--no-sandbox'] });
  const errors = [];
  const makePage = async (mobile = false) => {
    const context = await browser.newContext({ viewport: mobile ? { width: 390, height: 844 } : { width: 1440, height: 1000 }, isMobile: mobile });
    await context.route('**/*', route => new URL(route.request().url()).origin === origin ? route.continue() : route.abort());
    const page = await context.newPage();
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(origin);
    await page.locator('[data-sync-label]').filter({ hasText: 'Live sync' }).waitFor({ state: 'attached' });
    // Phones open on the chat list; start a chat from it like a user would.
    if (await page.locator('[data-chat].is-history').count()) await page.locator('.chat-history [data-chat-new]').click();
    return page;
  };
  const transcript = page => page.locator('[data-chat-log]').innerText();
  const finish = page => page.waitForFunction(() => !document.body.classList.contains('is-streaming') && document.querySelectorAll('.is-assistant').length > 0);
  const send = async (page, text) => {
    await page.locator('[data-chat-input]').fill(text);
    await page.locator('[data-chat-send]').click();
    await page.waitForFunction(() => document.body.classList.contains('is-streaming'));
  };
  try {
    const desktop = await makePage();
    const phone = await makePage(true);
    await phone.locator('[data-chat-input]').fill('Unsent phone draft');
    await send(desktop, 'Cross-device fixture');
    const id = new URL(desktop.url()).searchParams.get('t');
    assert.ok(id);
    await phone.locator(`[data-thread="${id}"]`).waitFor({ state: 'attached' });
    assert.equal(await phone.locator('[data-chat-input]').inputValue(), 'Unsent phone draft');
    await phone.goto(origin + '/chat?t=' + id);
    await phone.waitForFunction(() => document.body.classList.contains('is-streaming'));
    await phone.locator('[data-chat-input]').fill('Keep this local draft');
    await phone.waitForFunction(() => document.querySelector('[data-chat-log]').textContent.includes('Synchronized answer'));
    // Reload the origin while the server continues the same run.
    await desktop.reload();
    await desktop.locator('[data-sync-label]').filter({ hasText: 'Live sync' }).waitFor({ state: 'attached' });
    await Promise.all([finish(desktop), finish(phone)]);
    const contents = async page => page.locator('.chat-reply').allTextContents();
    assert.deepEqual(await contents(desktop), await contents(phone));
    assert.equal(await phone.locator('[data-chat-input]').inputValue(), 'Keep this local draft');
    assert.equal(await desktop.locator('.is-user').count(), 1);
    assert.equal(await phone.locator('.is-user').count(), 1);
    console.log('PASS new thread, live response, reconnect, and local draft preservation');

    await send(desktop, 'Stop from phone');
    await phone.waitForFunction(() => document.body.classList.contains('is-streaming'));
    // With a draft, Send queues a follow-up. Clear it to exercise Stop.
    await phone.locator('[data-chat-input]').fill('');
    await phone.locator('[data-chat-send]').click();
    await Promise.all([finish(desktop), finish(phone)]);
    assert.equal(await desktop.locator('.is-user').count(), 2);
    assert.deepEqual(await contents(desktop), await contents(phone));
    console.log('PASS remote cancellation');

    await desktop.locator('[data-memory-toggle]').click();
    await desktop.locator('[data-context-tab="profile"]').click();
    await desktop.locator('[data-memory-profile]').fill('Context from desktop');
    await phone.waitForFunction(() => document.querySelector('[data-memory-profile]').value === 'Context from desktop');
    await desktop.locator('[data-memory-close]').click();
    await send(phone, 'Phone follows up');
    await desktop.waitForFunction(() => document.body.classList.contains('is-streaming'));
    await Promise.all([finish(desktop), finish(phone)]);
    assert.equal(await desktop.locator('[data-memory-profile]').inputValue(), 'Context from desktop');
    assert.deepEqual(await contents(desktop), await contents(phone));
    console.log('PASS shared context and reverse-direction generation');

    // A disconnected observer catches up from its durable cursor.
    await phone.context().setOffline(true);
    await send(desktop, 'Offline catch-up');
    await finish(desktop);
    await phone.context().setOffline(false);
    await phone.locator('[data-sync-label]').filter({ hasText: 'Live sync' }).waitFor({ state: 'attached' });
    await phone.waitForFunction(() => document.querySelectorAll('.is-user').length === 4);
    await finish(phone);
    assert.deepEqual(await contents(desktop), await contents(phone));
    await phone.waitForFunction(() => document.querySelector('[data-chat-log]').textContent.includes('Offline catch-up'));
    console.log('PASS offline replay');

    if (!await desktop.locator(`[data-thread="${id}"]`).isVisible()) await desktop.locator('.chat-bar [data-history-toggle]').click();
    await desktop.locator(`[data-thread="${id}"]`).hover();
    await desktop.locator(`[data-thread="${id}"] .more`).click();
    await desktop.getByRole('button', { name: 'Rename', exact: true }).click();
    await desktop.locator('.rename').fill('Renamed on desktop');
    await desktop.locator('.rename').press('Enter');
    await phone.locator('[data-chat-title]').filter({ hasText: 'Renamed on desktop' }).waitFor();
    assert.equal(await desktop.locator('[data-sync-label]').innerText(), 'Live sync');
    assert.equal(await phone.locator('[data-sync-label]').innerText(), 'Live sync');
    console.log('PASS rename synchronization without self-conflict');

    // Metadata changes must never upload the full conversation.
    const patchRequest = desktop.waitForRequest(request => request.method() === 'PATCH' && request.url().includes('/chat/threads/'));
    await desktop.locator('[data-tool-search]').click();
    assert.equal(Object.hasOwn((await patchRequest).postDataJSON(), 'messages'), false);
    await phone.waitForFunction(() => document.querySelector('[data-tool-search]').getAttribute('aria-pressed') === 'false');
    console.log('PASS compact settings writes');

    await Promise.all([desktop.locator('[data-chat-input]').fill('Simultaneous desktop'), phone.locator('[data-chat-input]').fill('Simultaneous phone')]);
    await Promise.all([desktop.locator('[data-chat-send]').click(), phone.locator('[data-chat-send]').click()]);
    await desktop.waitForFunction(() => document.querySelectorAll('[data-chat-log] .is-user').length === 5);
    await phone.waitForFunction(() => document.querySelectorAll('[data-chat-log] .is-user').length === 5);
    const latest = await desktop.evaluate(async () => (await fetch('/chat/threads/' + new URL(location.href).searchParams.get('t'))).json());
    assert.equal(latest.messages.filter(message => message.role === 'user').length, 5);
    assert.deepEqual(await contents(desktop), await contents(phone));
    console.log('PASS concurrent send syncs to the latest server transcript');

    await Promise.all([finish(desktop), finish(phone)]);
    const threadId = new URL(desktop.url()).searchParams.get('t');
    await desktop.locator(`[data-thread="${threadId}"]`).hover();
    await desktop.locator(`[data-thread="${threadId}"] .more`).click();
    await desktop.getByRole('button', { name: 'Delete', exact: true }).click();
    await desktop.getByRole('dialog').getByRole('button', { name: 'Delete', exact: true }).click();
    await desktop.waitForFunction(() => !new URL(location.href).searchParams.get('t'));
    await phone.waitForFunction(id => !document.querySelector('[data-thread="' + id + '"]'), threadId);
    assert.equal(await desktop.locator('[data-sync-label]').innerText(), 'Live sync');
    assert.equal(await phone.locator('[data-sync-label]').innerText(), 'Live sync');
    console.log('PASS delete replicates without a local-copy review');

    const overflow = await phone.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
    assert.equal(overflow, false, 'mobile page must not overflow horizontally');
    assert.deepEqual(errors, []);
    console.log('PASS mobile layout and no browser exceptions');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
