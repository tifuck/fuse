// Chat-page empty/loading/composer send states: the states around sending and
// around having nothing to show. Isolated fixture server only — the model is
// mocked, and a "Queue fixture" prompt holds its run until the test finishes it.
//
//  1. First visit with zero threads: the welcome hero, no ghost rows, no
//     spinner, an enabled composer, and a first send that works from there.
//  2. The same empty state and first send on a phone (390), no overflow.
//  3. Send button states across a held turn: Send -> Stop -> Send (typed text
//     queues) -> Stop -> idle at done; no eternal "Sending…".
//  4. A failed send re-arms the button and a retry sends exactly once.
//  5. The Stop affordance: visible while running, clicking it settles the turn
//     and the composer comes back.
//  6. Send while streaming queues by design (hint, queue row, cleared box) and
//     the text lands exactly once when the turn ends.
//  7. Drafts live in memory: they survive thread round trips, and a reload
//     clears them cleanly with no stale send.
//  8. Background fetch failures: a down /chat/sync shows Disconnected with no
//     blank page and recovers without duplicated rows; a failed thread open
//     releases the composer and a retry opens the thread once.
const assert = require('node:assert/strict');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const origin = process.env.FUSE_BROWSER_ORIGIN || 'http://127.0.0.1:8099';
assert.match(origin, /^http:\/\/127\.0\.0\.1:\d+$/);

(async () => {
  const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'], ...(process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {}) });
  const errors = [];
  const make = async (width = 1440) => {
    const context = await browser.newContext({ viewport: { width, height: width === 390 ? 844 : 1000 }, reducedMotion: 'reduce' });
    await context.route('**/*', route => (new URL(route.request().url()).origin === origin ? route.continue() : route.abort()).catch(() => {}));
    const p = await context.newPage();
    p.on('pageerror', error => errors.push(`${width}: ${error.message}`));
    return p;
  };
  const live = p => p.locator('[data-sync-label]').filter({ hasText: 'Live sync' }).waitFor({ state: 'attached' });
  const boot = async (p, url = origin) => { await p.goto(url); await live(p); };
  const input = p => p.locator('[data-chat-input]');
  const sendClick = p => p.locator('[data-chat-send]').click();
  const settle = (p, timeout = 30000) => p.waitForFunction(() => !document.body.classList.contains('is-streaming') && !FuseChatActions.state().running, null, { timeout });
  const sendState = p => p.evaluate(() => {
    const send = document.querySelector('[data-chat-send]');
    const inputEl = document.querySelector('[data-chat-input]');
    return {
      streaming: document.body.classList.contains('is-streaming'),
      label: send.getAttribute('aria-label'),
      stop: send.classList.contains('is-stop'),
      disabled: send.disabled,
      input: inputEl.value,
      inputDisabled: inputEl.disabled,
      pending: document.querySelectorAll('.chat-pending-send').length,
      queue: document.querySelectorAll('.chat-queue-item').length,
      hint: document.querySelector('[data-chat-hint]')?.textContent || '',
    };
  });
  const emptyState = p => p.evaluate(() => {
    const chat = document.querySelector('[data-chat]');
    const log = document.querySelector('[data-chat-log]');
    const welcome = document.querySelector('[data-chat-welcome]');
    return {
      empty: chat.classList.contains('is-empty'),
      logHidden: log.hidden,
      rows: log.querySelectorAll('article').length,
      hero: getComputedStyle(welcome).opacity,
      heroText: welcome.textContent.trim(),
      dockShown: document.querySelector('[data-live-dock]').checkVisibility(),
      latestShown: document.querySelector('[data-chat-latest]').checkVisibility(),
      skeleton: document.querySelectorAll('.chat-history-loading').length,
      spins: [...document.querySelectorAll('[data-run-spin]')].filter(el => !el.hidden).length,
    };
  });
  const seed = async (p, messages, title = 'State fixture') => p.evaluate(async ({ messages, title }) => fuseToolFixture.api('/chat/threads', {
    method: 'POST', body: JSON.stringify({ model: 'offline', tools: [], title, command_id: crypto.randomUUID(), messages }),
  }), { messages, title });
  const turn = (user, assistant) => [
    { id: crypto.randomUUID(), role: 'user', content: user },
    { id: crypto.randomUUID(), role: 'assistant', content: assistant },
  ];
  const openRow = async (p, id) => {
    const row = p.locator(`[data-thread="${id}"]`);
    if (!(await row.isVisible())) await p.locator('.chat-bar [data-history-toggle]').click();
    await row.click();
    await p.waitForFunction(tid => new URL(location.href).searchParams.get('t') === tid, id);
  };
  const newChat = async p => {
    const btn = p.locator('[data-chat-new]:visible').first();
    if (await btn.count()) await btn.click();
    else await p.evaluate(() => FuseChatActions.run('new'));
    await p.waitForFunction(() => !new URL(location.href).searchParams.get('t'));
  };
  try {
    // 1. First visit with zero threads: the empty state renders and a first
    // send works straight from it.
    {
      const p = await make(1440);
      await boot(p);
      assert.equal(await p.evaluate(() => fuseToolFixture.snapshot().length), 0);
      assert.match(await p.locator('[data-history-list]').innerText(), /No chats yet/);
      const blank = await emptyState(p);
      assert.equal(blank.empty, true, 'the empty state renders');
      assert.equal(blank.hero, '1', `the welcome hero shows: ${blank.heroText}`);
      assert.match(blank.heroText, /What are we building/);
      assert.equal(blank.logHidden, true, 'no transcript stage yet');
      assert.equal(blank.rows, 0, 'no ghost rows');
      assert.equal(blank.dockShown, false, 'no run spinner');
      assert.equal(blank.latestShown, false, 'no jump pill');
      assert.equal(blank.skeleton, 0, 'the sidebar spinner cleared with the loaded history');
      assert.equal(blank.spins, 0);
      const idle = await sendState(p);
      assert.equal(idle.inputDisabled, false, 'the composer is enabled');
      assert.equal(idle.disabled, true, 'Send waits for content');
      assert.equal(idle.label, 'Send');
      assert.equal(idle.stop, false);
      assert.equal(idle.pending, 0);
      await input(p).fill('First send from the empty state');
      assert.equal((await sendState(p)).disabled, false, 'typing arms the send button');
      await sendClick(p);
      await p.waitForFunction(() => document.querySelector('[data-chat-log]').textContent.includes('Synchronized answer: First send from the empty state'));
      await settle(p);
      const after = await emptyState(p);
      assert.equal(after.empty, false, 'the first send activates the chat');
      assert.equal(after.hero, '0', 'the hero steps aside');
      assert.equal(after.logHidden, false);
      assert.ok(new URL(p.url()).searchParams.get('t'), 'the new thread owns the URL');
      const sent = await p.locator('.is-user').allTextContents();
      assert.equal(sent.filter(text => text.includes('First send from the empty state')).length, 1);
      const done = await sendState(p);
      assert.equal(done.label, 'Send');
      assert.equal(done.stop, false);
      assert.equal(done.disabled, true, 'back to idle with an empty box');
      assert.equal(done.input, '', 'a successful send clears the box');
      assert.equal(done.pending, 0);
      await p.context().close();
      console.log('PASS zero-thread empty state renders and the first send works from it');
    }

    // 2. The same states at 390, with no horizontal overflow.
    {
      const phone = await make(390);
      await boot(phone);
      if (await phone.locator('[data-chat].is-history').count()) await phone.locator('.chat-history [data-chat-new]').click();
      await phone.waitForFunction(() => document.querySelector('[data-chat]').classList.contains('is-empty'));
      const blank = await emptyState(phone);
      assert.equal(blank.empty, true);
      assert.equal(blank.hero, '1', 'the phone empty state shows the hero');
      assert.equal(blank.rows, 0, 'no ghost rows at 390');
      assert.equal(blank.dockShown, false);
      assert.equal(await phone.evaluate(() => document.documentElement.scrollWidth > innerWidth), false, 'no horizontal overflow at 390');
      await input(phone).fill('First send from the phone empty state');
      await sendClick(phone);
      await phone.waitForFunction(() => document.querySelector('[data-chat-log]').textContent.includes('Synchronized answer: First send from the phone empty state'));
      await settle(phone);
      assert.equal((await emptyState(phone)).empty, false);
      assert.equal(await phone.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
      await phone.context().close();
      console.log('PASS the empty state and first send hold at 390');
    }

    // 3. Send button states across a held turn.
    {
      const p = await make(1440);
      await boot(p);
      await input(p).fill('Queue fixture button states');
      const typed = await sendState(p);
      assert.equal(typed.disabled, false);
      assert.equal(typed.label, 'Send');
      assert.equal(typed.stop, false);
      await sendClick(p);
      await p.waitForFunction(() => document.body.classList.contains('is-streaming') && !document.querySelector('[data-chat-input]').value.trim());
      const streaming = await sendState(p);
      assert.equal(streaming.label, 'Stop', 'the button morphs to Stop while the run streams');
      assert.equal(streaming.stop, true);
      assert.equal(streaming.disabled, false, 'Stop is clickable');
      await input(p).fill('Typed mid-stream');
      await p.waitForFunction(() => document.querySelector('[data-chat-send]').getAttribute('aria-label') === 'Send');
      const morphed = await sendState(p);
      assert.equal(morphed.stop, false, 'with content the button is Send again (that turn would queue)');
      assert.equal(morphed.disabled, false);
      await input(p).fill('');
      await p.waitForFunction(() => document.querySelector('[data-chat-send]').getAttribute('aria-label') === 'Stop');
      const runId = await p.evaluate(() => fuseToolFixture.runInfo().id);
      await p.waitForFunction(() => (document.querySelector('[data-chat-log]')?.textContent || '').includes('Working before the queued instruction'));
      const finished = await p.request.post(`${origin}/__fixture/finish/${runId}`);
      assert.equal(finished.ok(), true, 'the run reaches the fixture gate');
      await settle(p);
      const done = await sendState(p);
      assert.equal(done.label, 'Send', 'the button returns to Send at done');
      assert.equal(done.stop, false);
      assert.equal(done.disabled, true, 'idle with an empty composer');
      assert.equal(done.pending, 0, 'no eternal Sending… row');
      assert.equal(done.queue, 0);
      await input(p).fill('The composer is back');
      assert.equal((await sendState(p)).disabled, false, 'the composer re-arms for the next turn');
      await p.context().close();
      console.log('PASS send button morphs Send→Stop→Send across a turn and idles at done');
    }

    // 4. A failed send: the button returns to idle and the retry sends once.
    {
      const p = await make(1440);
      await boot(p);
      await input(p).fill('Trigger a failed send once');
      await p.route('**/turns', route => route.fulfill({ status: 400, json: { detail: 'Offline rejected this turn' } }), { times: 1 });
      await sendClick(p);
      await p.waitForFunction(() => (document.querySelector('[data-chat-hint]')?.textContent || '').includes('Send not confirmed; your draft is retained'));
      const failed = await sendState(p);
      assert.equal(failed.streaming, false);
      assert.equal(failed.pending, 0, 'the Sending… row is gone after the error');
      assert.equal(failed.label, 'Send', 'the button is not stuck on Stop');
      assert.equal(failed.stop, false);
      assert.equal(failed.disabled, false, 'the retained draft keeps the composer usable');
      assert.equal(failed.input, 'Trigger a failed send once', 'the draft is retained for a retry');
      assert.equal(await p.evaluate(() => sessionStorage.getItem('fuse.chat.pendingTurn')), null, 'a definitive rejection drops the pending turn');
      await p.unroute('**/turns');
      await sendClick(p);
      await p.waitForFunction(() => document.querySelector('[data-chat-log]').textContent.includes('Synchronized answer: Trigger a failed send once'));
      await settle(p);
      const users = await p.locator('.is-user').allTextContents();
      assert.equal(users.filter(text => text.includes('Trigger a failed send once')).length, 1, 'the retry sends once, with no duplicate row');
      assert.equal(await input(p).inputValue(), '', 'a successful send clears the box');
      await p.context().close();
      console.log('PASS a failed send re-arms the button and the retry sends exactly once');
    }

    // 5. The Stop affordance ends the turn and the composer returns.
    {
      const p = await make(1440);
      await boot(p);
      await input(p).fill('Queue fixture stop affordance');
      await sendClick(p);
      await p.waitForFunction(() => document.body.classList.contains('is-streaming') && !document.querySelector('[data-chat-input]').value.trim());
      const stop = p.locator('[data-chat-send]');
      assert.equal(await stop.isVisible(), true, 'the affordance is visible while running');
      assert.equal(await stop.getAttribute('aria-label'), 'Stop');
      assert.equal(await stop.isEnabled(), true);
      await p.waitForFunction(() => (document.querySelector('[data-chat-log]')?.textContent || '').includes('Working before the queued instruction'));
      await sendClick(p);
      await settle(p);
      await p.waitForFunction(() => !fuseToolFixture.runInfo());
      const after = await sendState(p);
      assert.equal(after.label, 'Send', 'Stop is gone once the turn settles');
      assert.equal(after.stop, false);
      assert.equal(after.disabled, true, 'with the box empty the settled button is inert');
      assert.equal(after.pending, 0);
      assert.equal(after.queue, 0);
      await input(p).fill('After the stop');
      await sendClick(p);
      await p.waitForFunction(() => document.querySelector('[data-chat-log]').textContent.includes('Synchronized answer: After the stop'));
      await settle(p);
      await p.context().close();
      console.log('PASS the Stop affordance settles the turn and the composer comes back');
    }

    // 6. Send during streaming queues by design and loses nothing.
    {
      const p = await make(1440);
      await boot(p);
      const posts = [];
      p.on('request', request => {
        const path = new URL(request.url()).pathname;
        if (request.method() === 'POST' && (path.endsWith('/steer') || path.endsWith('/turns'))) posts.push(path);
      });
      await input(p).fill('Queue fixture send during stream');
      await sendClick(p);
      await p.waitForFunction(() => document.body.classList.contains('is-streaming') && !document.querySelector('[data-chat-input]').value.trim());
      const runId = await p.evaluate(() => fuseToolFixture.runInfo().id);
      const turnsBefore = posts.filter(path => path.endsWith('/turns')).length;
      await input(p).fill('Queued while the turn streams');
      await p.waitForFunction(() => document.querySelector('[data-chat-send]').getAttribute('aria-label') === 'Send');
      await sendClick(p);
      await p.locator('[data-chat-queue]').waitFor();
      await p.waitForFunction(() => (document.querySelector('[data-chat-hint]')?.textContent || '').includes('Queued — it sends when this turn finishes.'));
      const queued = await p.evaluate(() => ({
        input: document.querySelector('[data-chat-input]').value,
        label: document.querySelector('[data-chat-send]').getAttribute('aria-label'),
        stop: document.querySelector('[data-chat-send]').classList.contains('is-stop'),
        items: [...document.querySelectorAll('.chat-queue-item')].map(el => ({
          text: el.querySelector('.chat-queue-text').textContent,
          state: el.querySelector('.chat-queue-state').textContent,
          busy: el.getAttribute('aria-busy'),
          sendNow: Boolean(el.querySelector('[data-queue-send]:not([disabled])')),
        })),
      }));
      assert.equal(queued.input, '', 'the text moved into the queue instead of lingering in the box');
      assert.equal(queued.label, 'Stop', 'with the box cleared the button is Stop again');
      assert.equal(queued.stop, true);
      assert.equal(queued.items.length, 1);
      assert.equal(queued.items[0].text, 'Queued while the turn streams', 'the message is not silently lost');
      assert.equal(queued.items[0].state, 'Queued', 'a send during streaming queues by design');
      assert.equal(queued.items[0].busy, 'false');
      assert.equal(queued.items[0].sendNow, true, 'Send now stays available while the run streams');
      assert.equal(posts.filter(path => path.endsWith('/steer')).length, 0, 'queueing does not steer the live run');
      assert.equal(posts.filter(path => path.endsWith('/turns')).length, turnsBefore, 'queueing posts no new turn');
      assert.equal(await p.evaluate(() => fuseToolFixture.runInfo().id), runId, 'the live run is untouched');
      await p.waitForFunction(() => (document.querySelector('[data-chat-log]')?.textContent || '').includes('Working before the queued instruction'));
      const finished = await p.request.post(`${origin}/__fixture/finish/${runId}`);
      assert.equal(finished.ok(), true, 'the run reaches the fixture gate');
      await p.waitForFunction(() => [...document.querySelectorAll('.is-user')].some(el => el.textContent.includes('Queued while the turn streams')));
      await p.waitForFunction(() => !document.querySelector('.chat-queue-item'));
      await settle(p);
      const landed = await p.locator('.is-user').allTextContents();
      assert.equal(landed.filter(text => text.includes('Queued while the turn streams')).length, 1, 'the queued message lands exactly once');
      await p.context().close();
      console.log('PASS send during streaming queues with its hint and lands exactly once');
    }

    // 7. Drafts live in memory: thread round trips keep them, reload clears.
    {
      const p = await make(1440);
      await boot(p);
      const draft = 'Draft that must outlive a thread round trip';
      await input(p).fill(draft);

      const a = await seed(p, turn('Thread A prompt', 'Thread A answer'), 'Draft thread A');
      const b = await seed(p, turn('Thread B prompt', 'Thread B answer'), 'Draft thread B');
      await openRow(p, a.id);
      assert.equal(await input(p).inputValue(), '', 'another thread has its own draft slot');
      await input(p).fill('Thread A draft');
      await openRow(p, b.id);
      assert.equal(await input(p).inputValue(), '');
      await openRow(p, a.id);
      assert.equal(await input(p).inputValue(), 'Thread A draft', 'the draft round-trips through a thread switch');
      await newChat(p);
      assert.equal(await input(p).inputValue(), draft, 'the empty-state draft returns with New chat');
      assert.equal((await emptyState(p)).empty, true);

      await p.reload();
      await live(p);
      const cleared = await sendState(p);
      assert.equal(cleared.input, '', 'drafts are in-memory by design: a reload clears the box');
      assert.equal(cleared.disabled, true);
      assert.equal(cleared.pending, 0, 'no stale Sending… row');
      assert.equal(await p.evaluate(() => sessionStorage.getItem('fuse.chat.pendingTurn')), null, 'no stale send survives the reload');
      const blank = await emptyState(p);
      assert.equal(blank.rows, 0, 'no ghost rows after the reload');
      await input(p).fill('Fresh send after reload');
      await sendClick(p);
      await p.waitForFunction(() => document.querySelector('[data-chat-log]').textContent.includes('Synchronized answer: Fresh send after reload'));
      await settle(p);
      const sent = await p.locator('.is-user').allTextContents();
      assert.equal(sent.filter(text => text.includes('Fresh send after reload')).length, 1, 'the clean state sends normally');
      await p.context().close();
      console.log('PASS drafts survive thread switches and clear cleanly on reload');
    }

    // 8a. A down /chat/sync shows Disconnected, never a blank page, and the
    // recovery retry does not duplicate rows.
    {
      const p = await make(1440);
      await p.route('**/chat/sync', route => route.abort('failed'));
      await p.goto(origin);
      await p.waitForFunction(() => document.querySelector('.chat-sync')?.dataset.syncState === 'disconnected', null, { timeout: 15000 });
      const down = await p.evaluate(() => ({
        blank: !document.querySelector('[data-chat]')?.getClientRects().length,
        empty: document.querySelector('[data-chat]').classList.contains('is-empty'),
        hero: getComputedStyle(document.querySelector('[data-chat-welcome]')).opacity,
        label: document.querySelector('[data-sync-label]').textContent,
        inputDisabled: document.querySelector('[data-chat-input]').disabled,
      }));
      assert.equal(down.blank, false, 'the page is not blank while the sync fetch is down');
      assert.equal(down.empty, true);
      assert.equal(down.hero, '1', 'the empty-state hero still renders');
      assert.equal(down.label, 'Disconnected', 'the sync line names the failure');
      assert.equal(down.inputDisabled, false, 'the composer still accepts a draft');
      await input(p).fill('Typed while the backend is unreachable');
      assert.equal((await sendState(p)).disabled, true, 'send waits out the reconnect instead of posting blind');
      await p.unroute('**/chat/sync');
      await p.evaluate(() => window.dispatchEvent(new Event('online')));
      await live(p);
      await p.waitForFunction(() => !document.querySelector('.chat-history-loading'), null, { timeout: 15000 });
      const ids = await p.evaluate(() => [...document.querySelectorAll('[data-thread]')].map(el => el.dataset.thread));
      assert.ok(ids.length >= 2, `recovered rows are present: ${ids.length}`);
      assert.equal(ids.length, new Set(ids).size, `retry does not duplicate rows: ${ids.join(',')}`);
      assert.equal(await input(p).inputValue(), 'Typed while the backend is unreachable', 'the draft typed during the outage is not lost');
      await p.waitForFunction(() => !document.querySelector('[data-chat-send]').disabled);
      await sendClick(p);
      await p.waitForFunction(() => document.querySelector('[data-chat-log]').textContent.includes('Synchronized answer: Typed while the backend is unreachable'));
      await settle(p);
      await p.context().close();
      console.log('PASS a down /chat/sync shows Disconnected and recovers without duplicated rows');
    }

    // 8b. A failed thread open releases the composer; a retry opens the thread
    // once, with no ghost rows in between.
    {
      const p = await make(1440);
      await boot(p);
      const created = await seed(p, turn('Open me despite the outage', 'Recovered answer'), 'Open failure fixture');
      await p.route('**/chat/threads/*', route => route.abort('failed'));
      await p.goto(`${origin}/chat?t=${created.id}`);
      await p.waitForFunction(() => (document.querySelector('[data-chat-hint]')?.textContent || '').includes('Chat history could not be loaded'), null, { timeout: 15000 });
      const stuck = await sendState(p);
      assert.equal(stuck.pending, 0);
      assert.equal(stuck.label, 'Send', 'the failed open leaves no stuck Stop');
      assert.equal(stuck.stop, false);
      assert.equal(await p.evaluate(() => FuseChatActions.state().loading), false, 'the failed open releases the composer');
      assert.equal(await p.evaluate(() => document.querySelectorAll('[data-chat-log] > article').length), 0, 'the failed open paints no ghost rows');
      await input(p).fill('Composer works while the open route is down');
      await p.waitForFunction(() => !document.querySelector('[data-chat-send]').disabled, null, { timeout: 15000 });
      await p.unroute('**/chat/threads/*');
      await openRow(p, created.id);
      await p.waitForFunction(() => fuseToolFixture.snapshot().length === 2);
      const opened = await p.locator('[data-chat-log]').innerText();
      assert.equal((opened.match(/Open me despite the outage/g) || []).length, 1, 'the retry opens the thread once');
      assert.equal(await p.evaluate(() => FuseChatActions.state().loading), false);
      await p.context().close();
      console.log('PASS a failed thread open releases the composer and a retry opens once');
    }

    assert.deepEqual(errors, []);
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
