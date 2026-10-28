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
    for (const width of [1440, 390]) {
      const context = await browser.newContext({ viewport: { width, height: width === 390 ? 844 : 1000 }, reducedMotion: 'reduce' });
      await context.route('**/*', route => new URL(route.request().url()).origin === origin ? route.continue() : route.abort());
      const page = await context.newPage();
      page.on('pageerror', error => errors.push(error.message));
      await page.goto(origin);
      await page.locator('[data-sync-label]').filter({ hasText: 'Live sync' }).waitFor({ state: 'attached' });
      // Phones open on the chat list; start a chat from it like a user would.
      if (await page.locator('[data-chat].is-history').count()) await page.locator('.chat-history [data-chat-new]').click();

      const input = page.locator('[data-chat-input]');
      const send = async text => { await input.fill(text); await page.locator('[data-chat-send]').click(); };
      const queue = page.locator('[data-chat-queue]');
      await send(`Queue fixture ${width}`);
      await page.waitForFunction(() => document.body.classList.contains('is-streaming'));
      await page.waitForFunction(() => document.querySelector('[data-chat-log]').textContent.includes('Working before'));
      const id = new URL(page.url()).searchParams.get('t');
      const read = () => page.evaluate(async id => (await fetch('/chat/threads/' + id)).json(), id);
      const runSnapshot = runId => page.evaluate(async runId => (await fetch('/chat/runs/' + runId)).json(), runId);
      const initial = await read();
      const runId = initial.run.id;
      await send('Edit this queued instruction');
      // One line per message: Edit and Cancel sit behind the row's ⋮ menu.
      assert.equal(await page.locator('[data-queue-edit]').isVisible(), false, 'Edit waits behind the menu');
      assert.equal(await page.locator('.chat-queue-state').textContent(), '', 'the header says Queued; the row does not');
      const item = await page.locator('.chat-queue-item').boundingBox();
      const text = await page.locator('.chat-queue-text').boundingBox();
      const sendNow = await page.locator('[data-queue-send]').boundingBox();
      assert.ok(Math.abs((sendNow.y + sendNow.height / 2) - (text.y + text.height / 2)) < 4 && item.height < 40, `one row per message at ${width}px`);
      await page.locator('[data-queue-more]').click();
      await page.locator('[data-queue-edit]').click();
      assert.equal(await input.inputValue(), 'Edit this queued instruction');
      assert.equal(await queue.isVisible(), false);
      await input.fill('Cancel this instruction');
      await page.locator('[data-chat-send]').click();
      await page.locator('[data-queue-more]').click();
      await page.keyboard.press('Escape');
      assert.equal(await page.locator('[data-queue-cancel]').isVisible(), false, 'Escape closes the menu');
      await page.locator('[data-queue-more]').click();
      await page.locator('[data-queue-cancel]').click();
      assert.equal(await queue.isVisible(), false);
      await send('Queue fixture send now target');
      await send('Keep this queued for later');
      await input.fill('An unsent draft must survive');
      await page.screenshot({ path: `${out}/${width}-queued.png` });
      const observer = await browser.newPage({ viewport: { width: 1100, height: 850 } });
      observer.on('pageerror', error => errors.push(error.message));
      await observer.route('**/*', route => new URL(route.request().url()).origin === origin ? route.continue() : route.abort());
      await observer.goto(origin);
      await observer.goto(`${origin}/chat?t=${id}`);
      await observer.waitForFunction(() => document.body.classList.contains('is-streaming'));
      const requests = [];
      page.on('request', request => {
        if (request.method() !== 'POST') return;
        const url = new URL(request.url());
        if (/\/(steer|cancel|turns|queue)$/.test(url.pathname)) requests.push({ path: url.pathname, scope: url.searchParams.get('scope'), body: request.postDataJSON() });
      });
      // Gate the stop so the sending state can be observed mid-flight.
      let releaseStop;
      const stopGate = new Promise(resolve => { releaseStop = resolve; });
      await page.route(url => url.pathname.endsWith('/cancel'), async route => { await stopGate; await route.continue(); }, { times: 1 });
      await page.locator('[data-queue-send]').first().click();
      const sendingItem = page.locator('.chat-queue-item').first();
      assert.equal(await sendingItem.getAttribute('aria-busy'), 'true');
      assert.match(await sendingItem.locator('.chat-queue-state').textContent(), /Sending/);
      assert.equal(await sendingItem.locator('button:disabled').count(), 4, 'all actions (Send now, the menu, Edit, Cancel) are disabled during submission');
      assert.equal(await input.inputValue(), 'An unsent draft must survive');
      releaseStop();
      await page.waitForFunction(() => document.querySelector('[data-chat-log]').textContent.includes('Queue fixture send now target'));
      await observer.waitForFunction(() => document.querySelector('[data-chat-log]').textContent.includes('Queue fixture send now target'));
      assert.equal(requests.filter(r => r.path.endsWith('/steer')).length, 0, 'Send now no longer steers the live run');
      assert.equal(requests.filter(r => r.path.endsWith('/cancel')).length, 1, 'Send now stops the running reply');
      assert.equal(requests.find(r => r.path.endsWith('/cancel')).scope, 'reply', 'Send now stops only the reply, never its subagents');
      // Queued messages go through the server's copy, which starts them when
      // the run ends with or without this tab; the user message below lands once.
      assert.equal(requests.filter(r => r.path.endsWith('/turns') && r.body.text === 'Queue fixture send now target').length, 0);
      const sentTurn = requests.filter(r => r.path.endsWith('/queue') && r.body.text === 'Queue fixture send now target');
      assert.ok(sentTurn.length >= 1 && sentTurn[0].body.release === true, 'Send now tells the server this item goes first before stopping');
      assert.ok(requests.indexOf(sentTurn[0]) < requests.findIndex(r => r.path.endsWith('/cancel')), 'the server learns the priority before the run ends');
      assert.equal(sentTurn[0].body.attachments.length, 0);
      const after = await read();
      assert.notEqual(after.run.id, runId, 'Send now starts a new run');
      assert.ok(['queued', 'running'].includes(after.run.status), 'the new turn is live');
      const stopped = await runSnapshot(runId);
      assert.ok(!['queued', 'running'].includes(stopped.status), 'the interrupted run is terminal');
      assert.ok(stopped.cancel_requested, 'the interrupted run was stopped on request');
      const userMessages = after.messages.filter(m => m.role === 'user' && m.content === 'Queue fixture send now target');
      assert.equal(userMessages.length, 1);
      const oldIndex = after.messages.findIndex(m => m.id === initial.run.message_id);
      const userIndex = after.messages.findIndex(m => m.id === userMessages[0].id);
      const newIndex = after.messages.findIndex(m => m.id === after.run.message_id);
      assert.ok(oldIndex >= 0 && oldIndex < userIndex, 'the interrupted reply stays above the new user message');
      assert.ok(newIndex > userIndex, 'the new reply is a separate node below the new user message');
      // The interrupted reply must freeze the moment Send now is pressed: no
      // tokens or tool calls from the killed session may land afterwards.
      await page.waitForTimeout(800);
      const frozen = await page.evaluate(oldId => {
        const node = [...document.querySelectorAll('[data-message-id]')].find(el => el.dataset.messageId === oldId);
        return node ? node.textContent.length : -1;
      }, initial.run.message_id);
      const grew = await page.evaluate(newId => {
        const node = [...document.querySelectorAll('[data-message-id]')].find(el => el.dataset.messageId === newId);
        return node ? node.textContent.length : -1;
      }, after.run.message_id);
      await page.waitForTimeout(800);
      assert.equal(await page.evaluate(oldId => {
        const node = [...document.querySelectorAll('[data-message-id]')].find(el => el.dataset.messageId === oldId);
        return node ? node.textContent.length : -1;
      }, initial.run.message_id), frozen, 'the interrupted reply never grows after Send now');
      assert.ok(await page.evaluate(newId => {
        const node = [...document.querySelectorAll('[data-message-id]')].find(el => el.dataset.messageId === newId);
        return node ? node.textContent.length : -1;
      }, after.run.message_id) > grew, 'the new session streams into its own reply');
      assert.equal(await page.locator('.chat-queue-item').count(), 1);
      assert.ok((await queue.innerText()).includes('Keep this queued for later'));
      assert.equal((await queue.innerText()).includes('Queue fixture send now target'), false);
      assert.equal(await input.inputValue(), 'An unsent draft must survive');
      for (const p of [page, observer]) {
        assert.equal(await p.locator('[data-live-dock]').isVisible(), true, 'spinner must remain visible');
        assert.equal(await p.evaluate(() => document.body.classList.contains('is-streaming')), true);
      }
      await page.screenshot({ path: `${out}/${width}-sent-now.png` });
      await page.reload();
      await page.waitForFunction(() => document.body.classList.contains('is-streaming'));
      assert.equal(await page.locator('.chat-queue-item').count(), 1);
      assert.equal(await page.locator('[data-live-dock]').isVisible(), true);
      await page.request.post(`${origin}/__fixture/finish/${after.run.id}`);
      await page.waitForFunction(() => [...document.querySelectorAll('.is-user')].some(el => el.textContent.includes('Keep this queued for later')));
      await page.waitForFunction(() => !document.body.classList.contains('is-streaming') && !document.querySelector('.chat-queue-item'));
      assert.equal(await queue.isVisible(), false);
      const final = await read();
      assert.equal(final.messages.filter(m => m.role === 'user' && m.content === 'Queue fixture send now target').length, 1);
      assert.equal(final.messages.filter(m => m.role === 'user' && m.content === 'Keep this queued for later').length, 1);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
      await observer.close();
      if (width === 1440) {
        // Stop means stop: the queue holds instead of starting a new turn.
        await send('Queue fixture stop holds the queue');
        await page.waitForFunction(() => document.body.classList.contains('is-streaming'));
        await send('Held after stop');
        await input.fill('');
        await page.locator('[data-chat-send]').click();
        await page.waitForFunction(() => !document.body.classList.contains('is-streaming'));
        await page.waitForTimeout(1500);
        const isHeldSent = async () => (await read()).messages.some(m => m.role === 'user' && m.content === 'Held after stop');
        assert.equal(await isHeldSent(), false, 'Stop does not send the queued message');
        assert.equal(await page.locator('.chat-queue-item').count(), 1);
        assert.match(await page.locator('.chat-queue-item .chat-queue-state').textContent(), /Stopped/);
        assert.equal(await page.locator('[data-queue-send]').isEnabled(), true, 'Send now releases a held queue');
        await page.reload();
        await page.locator('.chat-queue-item').waitFor();
        await page.waitForTimeout(1500);
        assert.equal(await isHeldSent(), false, 'the hold survives a reload');
        await page.locator('[data-queue-send]').click();
        await page.waitForFunction(() => [...document.querySelectorAll('.is-user')].some(el => el.textContent.includes('Held after stop')));
        await page.waitForFunction(() => !document.body.classList.contains('is-streaming') && !document.querySelector('.chat-queue-item'));
        console.log('PASS Stop holds the queue until Send now');

        // Retry and edit replace the tail atomically via replace_from on the
        // turn POST. When the server refuses the replacement (a subagent of
        // the chat still runs, or another tab changed the transcript), nothing
        // may be sent: no duplicate prompt, no turn started, transcript intact.
        const lastPrompt = (await read()).messages.filter(m => m.role === 'user').at(-1).content;
        const countOf = async text => (await read()).messages.filter(m => m.role === 'user' && m.content === text).length;
        const before = await countOf(lastPrompt);
        const beforeRows = (await read()).messages.length;
        const refuse = async route => route.request().method() === 'POST'
          ? route.fulfill({ status: 409, contentType: 'application/json', body: JSON.stringify({ detail: 'cannot replace messages during an active run' }) })
          : route.continue();
        await page.route(`**/chat/threads/${id}/turns`, refuse);
        await input.fill('/retry');
        await input.press('Enter');
        await page.waitForTimeout(300);
        await input.press('Enter').catch(() => {});
        await page.waitForTimeout(2500);
        assert.equal(await countOf(lastPrompt), before, 'a refused Retry sends nothing');
        assert.equal((await read()).run, null, 'no turn started');
        assert.equal((await read()).messages.length, beforeRows, 'transcript was not truncated');
        await page.unroute(`**/chat/threads/${id}/turns`, refuse);
        console.log('PASS refused Retry does not duplicate the prompt');

        await send('Queue fixture lost acknowledgment');
        await page.waitForFunction(() => document.body.classList.contains('is-streaming'));
        await send('Survive a lost acknowledgment');
        await page.route('**/queue', async route => {
          await route.fetch();
          await route.abort('failed');
        }, { times: 2 });
        await page.locator('[data-queue-send]').last().click();
        await page.waitForFunction(() => [...document.querySelectorAll('.is-user')].some(el => el.textContent.includes('Survive a lost acknowledgment')));
        await page.waitForFunction(() => document.querySelectorAll('.chat-queue-item').length === 0);
        assert.equal((await read()).messages.filter(m => m.role === 'user' && m.content === 'Survive a lost acknowledgment').length, 1);
        await page.waitForFunction(() => !document.body.classList.contains('is-streaming'));
        console.log('PASS lost turn acknowledgment still lands exactly once and retires the queue item');

        // A queued message used to wait for its tab to send it, so a hidden,
        // frozen or closed tab stalled it. The server holds a copy now.
        await send('Queue fixture closed tab');
        await page.waitForFunction(() => document.body.classList.contains('is-streaming'));
        const closedRun = (await read()).run.id;
        const mirrored = page.waitForResponse(r => r.url().endsWith('/queue') && r.request().postDataJSON()?.text === 'Sent with no tab open');
        await send('Sent with no tab open');
        await mirrored;
        await page.close();
        let landed = false;
        for (let i = 0; i < 100 && !landed; i += 1) {
          await context.request.post(`${origin}/__fixture/finish/${closedRun}`);
          const thread = await (await context.request.get(`${origin}/chat/threads/${id}`)).json();
          landed = thread.messages.some(m => m.role === 'user' && m.content === 'Sent with no tab open');
          if (!landed) await new Promise(resolve => setTimeout(resolve, 100));
        }
        assert.ok(landed, 'the queued message sends when the turn ends with its tab closed');
        console.log('PASS a queued message sends with no tab open');

      }
      if (width === 390) {
        await send('Queue fixture attachment priority');
        await page.waitForFunction(() => document.body.classList.contains('is-streaming'));
        const attachmentRun = (await read()).run.id;
        const attachmentRequests = [];
        const recordRequest = request => {
          if (request.method() === 'POST' && /\/(steer|cancel|turns|queue)$/.test(new URL(request.url()).pathname)) attachmentRequests.push({ url: new URL(request.url()).pathname, body: request.postDataJSON() });
        };
        page.on('request', recordRequest);
        await send('Earlier queued message');
        await page.locator('[data-chat-file]').setInputFiles({ name: 'note.txt', mimeType: 'text/plain', buffer: Buffer.from('Offline attachment contents') });
        await page.locator('.chat-attach-chip').waitFor();
        await send('Attached priority');
        const priorityTurn = page.waitForResponse(r => r.url().endsWith('/queue') && r.request().method() === 'POST' && r.request().postDataJSON().release === true && r.request().postDataJSON().text === 'Attached priority');
        await page.locator('[data-queue-send]').last().click();
        await priorityTurn;
        await page.waitForFunction(() => [...document.querySelectorAll('.is-user')].some(el => el.textContent.includes('Attached priority')));
        assert.ok(attachmentRequests.filter(r => r.url.endsWith('/cancel')).length >= 1, 'Send now stops the running reply even with attachments');
        assert.equal(attachmentRequests.filter(r => r.url.endsWith('/steer')).length, 0);
        const attachmentTurn = attachmentRequests.filter(r => r.url.endsWith('/queue') && r.body.text === 'Attached priority').at(-1);
        assert.equal(attachmentTurn.body.attachments.length, 1);
        assert.ok(!['queued', 'running'].includes((await runSnapshot(attachmentRun)).status), 'the interrupted attachment run is terminal');
        await page.waitForFunction(() => !document.body.classList.contains('is-streaming') && !document.querySelector('.chat-queue-item'));
        await send('Queue fixture attachment only');
        await page.waitForFunction(() => document.body.classList.contains('is-streaming'));
        await page.locator('[data-chat-file]').setInputFiles({ name: 'only.txt', mimeType: 'text/plain', buffer: Buffer.from('Attachment-only send') });
        await page.locator('.chat-attach-chip').waitFor();
        await send('');
        const attachmentOnlyTurn = page.waitForResponse(r => r.url().endsWith('/queue') && r.request().method() === 'POST' && r.request().postDataJSON().release === true && r.request().postDataJSON().text === '');
        await page.locator('[data-queue-send]').first().click();
        await attachmentOnlyTurn;
        await page.waitForFunction(() => document.querySelectorAll('.chat-queue-item').length === 0);
        await page.waitForFunction(() => !document.body.classList.contains('is-streaming'));
        const attachmentOnly = attachmentRequests.filter(r => r.url.endsWith('/queue')).at(-1).body;
        assert.equal(attachmentOnly.text, '');
        assert.equal(attachmentOnly.attachments.length, 1);
        assert.ok((await read()).messages.some(m => m.role === 'user' && !(m.content || '').trim() && (m.attachments || []).length === 1), 'the attachment-only message lands as a real user message');
        page.off('request', recordRequest);
        const priority = (await read()).messages.filter(m => m.role === 'user').map(m => m.content);
        assert.ok(priority.indexOf('Attached priority') < priority.indexOf('Earlier queued message'), 'selected attachment goes first');
        console.log('PASS attachment Send now interrupts and sends files as a new turn; remaining queue drains afterward');
      }
      await context.close();
      console.log(`PASS queue edit/cancel/send-now-interrupt/reload/drain/observer ${width}`);
    }
    assert.deepEqual(errors, []);
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
