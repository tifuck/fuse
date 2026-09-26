// Real queue HTTP routes and SSE, controlled offline worker; never use production.
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const origin = process.env.FUSE_BROWSER_ORIGIN || 'http://127.0.0.1:8099';
assert.match(origin, /^http:\/\/127\.0\.0\.1:\d+$/);
const out = process.env.QUEUE_UI_ARTIFACTS || '/tmp/opencode/queue-ui-artifacts';

(async () => {
  await fs.mkdir(out, { recursive: true });
  const browser = await chromium.launch({ args: ['--no-sandbox'], executablePath: process.env.CHROMIUM_PATH || undefined });
  const errors = [];
  try {
    for (const width of [1440, 390]) for (const view of ['cli', 'compact']) {
      const context = await browser.newContext({ viewport: { width, height: width === 390 ? 844 : 1000 }, reducedMotion: 'reduce' });
      await context.route('**/*', route => new URL(route.request().url()).origin === origin ? route.continue() : route.abort());
      const page = await context.newPage();
      page.on('pageerror', error => errors.push(error.message));
      await page.goto(origin);
      await page.locator('[data-sync-label]').filter({ hasText: 'Live sync' }).waitFor({ state: 'attached' });
      // Phones open on the chat list; start a chat from it like a user would.
      if (await page.locator('[data-chat].is-history').count()) await page.locator('.chat-history [data-chat-new]').click();
      await page.evaluate(view => fuseToolFixture.view(view), view);
      const input = page.locator('[data-chat-input]');
      const send = async text => { await input.fill(text); await page.locator('[data-chat-send]').click(); };
      const queue = page.locator('[data-chat-queue]');
      await send(`Queue fixture ${view} ${width}`);
      await page.waitForFunction(() => document.body.classList.contains('is-streaming'));
      await page.waitForFunction(() => document.querySelector('[data-chat-log]').textContent.includes('Working before'));
      const id = new URL(page.url()).searchParams.get('t');
      const read = () => page.evaluate(async id => (await fetch('/chat/threads/' + id)).json(), id);
      const initial = await read();
      const runId = initial.run.id;
      await send('Edit this queued instruction');
      await page.locator('[data-queue-edit]').click();
      assert.equal(await input.inputValue(), 'Edit this queued instruction');
      assert.equal(await queue.isVisible(), false);
      await input.fill('Cancel this instruction');
      await page.locator('[data-chat-send]').click();
      await page.locator('[data-queue-cancel]').click();
      assert.equal(await queue.isVisible(), false);
      await send('Send this now');
      await send('Keep this queued for later');
      await input.fill('An unsent draft must survive');
      await page.screenshot({ path: `${out}/${view}-${width}-queued.png` });
      const observer = await browser.newPage({ viewport: { width: 1100, height: 850 } });
      observer.on('pageerror', error => errors.push(error.message));
      await observer.route('**/*', route => new URL(route.request().url()).origin === origin ? route.continue() : route.abort());
      await observer.goto(origin);
      await observer.goto(`${origin}/chat?t=${id}`);
      await observer.waitForFunction(() => document.body.classList.contains('is-streaming'));
      const response = page.waitForResponse(response => response.url().endsWith('/steer') && response.request().method() === 'POST');
      await page.evaluate(() => {
        window.__tailLeaked = false;
        window.__tailObserver = new MutationObserver(() => {
          const user = [...document.querySelectorAll('.chat-turn.is-user')].find(el => el.textContent.includes('Send this now'));
          if (user?.nextElementSibling?.textContent.includes('PREVIOUS_TAIL')) window.__tailLeaked = true;
        });
        window.__tailObserver.observe(document.querySelector('[data-chat-log]'), { subtree: true, childList: true, characterData: true });
      });
      let releaseSteer;
      const steerGate = new Promise(resolve => { releaseSteer = resolve; });
      await page.route('**/steer', async route => { await steerGate; await route.continue(); }, { times: 1 });
      await page.locator('[data-queue-send]').first().click();
      const sendingItem = page.locator('.chat-queue-item').first();
      assert.equal(await sendingItem.getAttribute('aria-busy'), 'true');
      assert.match(await sendingItem.locator('.chat-queue-state').textContent(), /Sending/);
      assert.equal(await sendingItem.locator('button:disabled').count(), 3, 'all actions are disabled during submission');
      assert.equal(await input.inputValue(), 'An unsent draft must survive');
      releaseSteer();
      const accepted = await response;
      assert.equal(accepted.status(), 200, await accepted.text());
      await page.waitForFunction(() => document.querySelector('[data-chat-log]').textContent.includes('Acknowledged: Send this now'));
      await observer.waitForFunction(() => document.querySelector('[data-chat-log]').textContent.includes('Acknowledged: Send this now'));
      assert.equal(await page.evaluate(() => { window.__tailObserver.disconnect(); return window.__tailLeaked; }), false, 'old tokens must never render below the new user message');
      assert.equal(await page.locator('.chat-queue-item').count(), 1);
      assert.ok((await queue.innerText()).includes('Keep this queued for later'));
      assert.equal((await queue.innerText()).includes('Send this now'), false);
      assert.equal(await input.inputValue(), 'An unsent draft must survive');
      const after = await read();
      assert.equal(after.run.id, runId, 'Send now continues the same run');
      const steerIndex = after.messages.findIndex(m => m.role === 'user' && m.content === 'Send this now');
      assert.ok(steerIndex > 0);
      assert.equal(after.messages[steerIndex + 1].id, after.run.message_id);
      assert.ok(after.messages[steerIndex - 1].content.includes('Working before'));
      assert.equal(after.messages[steerIndex - 1].content.includes('Acknowledged:'), false);
      assert.ok(after.messages[steerIndex - 1].content.includes('PREVIOUS_TAIL'));
      assert.equal(after.messages[steerIndex + 1].content.includes('PREVIOUS_TAIL'), false);
      for (const p of [page, observer]) {
        assert.equal(await p.locator('[data-live-dock]').isVisible(), true, 'spinner must remain visible');
        assert.equal(await p.evaluate(() => document.body.classList.contains('is-streaming')), true);
      }
      await page.screenshot({ path: `${out}/${view}-${width}-sent-now.png` });
      if (view === 'compact' && width === 1440) {
        await send('Survive a lost acknowledgment');
        await page.route('**/steer', async route => {
          await route.fetch();
          await route.abort('failed');
        }, { times: 1 });
        await page.locator('[data-queue-send]').last().click();
        await page.waitForFunction(() => [...document.querySelectorAll('.is-user')].some(el => el.textContent.includes('Survive a lost acknowledgment')));
        await page.waitForFunction(() => document.querySelectorAll('.chat-queue-item').length === 1);
        assert.equal((await read()).messages.filter(m => m.role === 'user' && m.content === 'Survive a lost acknowledgment').length, 1);
      }
      await page.reload();
      await page.waitForFunction(() => document.body.classList.contains('is-streaming'));
      assert.equal(await page.locator('.chat-queue-item').count(), 1);
      assert.equal(await page.locator('[data-live-dock]').isVisible(), true);
      await page.request.post(`${origin}/__fixture/finish/${runId}`);
      await page.waitForFunction(() => [...document.querySelectorAll('.is-user')].some(el => el.textContent.includes('Keep this queued for later')));
      await page.waitForFunction(() => !document.body.classList.contains('is-streaming') && !document.querySelector('.chat-queue-item'));
      assert.equal(await queue.isVisible(), false);
      const final = await read();
      assert.equal(final.messages.filter(m => m.role === 'user' && m.content === 'Send this now').length, 1);
      assert.equal(final.messages.filter(m => m.role === 'user' && m.content === 'Keep this queued for later').length, 1);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
      await observer.close();
      if (view === 'compact' && width === 390) {
        await send('Queue fixture attachment priority');
        await page.waitForFunction(() => document.body.classList.contains('is-streaming'));
        const attachmentRun = (await read()).run.id;
        const attachmentRequests = [];
        const recordRequest = request => {
          if (request.method() === 'POST' && /\/(steer|cancel)$/.test(request.url())) attachmentRequests.push({ url: request.url(), body: request.postDataJSON() });
        };
        page.on('request', recordRequest);
        await send('Earlier queued message');
        await page.locator('[data-chat-file]').setInputFiles({ name: 'note.txt', mimeType: 'text/plain', buffer: Buffer.from('Offline attachment contents') });
        await page.locator('.chat-attach-chip').waitFor();
        await send('Attached priority');
        await page.locator('[data-queue-send]').last().click();
        await page.waitForFunction(() => [...document.querySelectorAll('.is-user')].some(el => el.textContent.includes('Attached priority')));
        assert.equal((await read()).run.id, attachmentRun, 'attachments steer the existing run');
        assert.equal(attachmentRequests.filter(r => r.url.endsWith('/cancel')).length, 0, 'attachment Send now never cancels');
        assert.equal(attachmentRequests.find(r => r.url.endsWith('/steer')).body.attachments.length, 1);
        await page.locator('[data-chat-file]').setInputFiles({ name: 'only.txt', mimeType: 'text/plain', buffer: Buffer.from('Attachment-only steer') });
        await page.locator('.chat-attach-chip').waitFor();
        await send('');
        await page.locator('[data-queue-send]').last().click();
        await page.waitForFunction(() => document.querySelectorAll('.chat-queue-item').length === 1);
        assert.equal((await read()).run.id, attachmentRun, 'attachment-only Send now also continues the same run');
        const attachmentOnly = attachmentRequests.filter(r => r.url.endsWith('/steer')).at(-1).body;
        assert.equal(attachmentOnly.text, '');
        assert.equal(attachmentOnly.attachments.length, 1);
        assert.equal(attachmentRequests.filter(r => r.url.endsWith('/cancel')).length, 0);
        page.off('request', recordRequest);
        await page.request.post(`${origin}/__fixture/finish/${attachmentRun}`);
        await page.waitForFunction(() => !document.body.classList.contains('is-streaming') && !document.querySelector('.chat-queue-item'));
        const priority = (await read()).messages.filter(m => m.role === 'user').map(m => m.content);
        assert.ok(priority.indexOf('Attached priority') < priority.indexOf('Earlier queued message'), 'selected attachment goes first');
        console.log('PASS attachment and attachment-only Send now steer without cancelling; remaining queue drains afterward');
      }
      await context.close();
      console.log(`PASS queue edit/cancel/send-now/continuation/reload/drain/observer ${view} ${width}`);
    }
    assert.deepEqual(errors, []);
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
