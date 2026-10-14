// Recovery states the other browser suites do not pin: a dropped stream re-arms
// the composer, an aborted turn leaves no live row, an edit restores the caret,
// deleting the open thread leaves no stale selection, and permission chrome stays
// inside the viewport. Isolated fixture server only.
const assert = require('node:assert/strict');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const origin = process.env.FUSE_BROWSER_ORIGIN || 'http://127.0.0.1:8099';
assert.match(origin, /^http:\/\/127\.0\.0\.1:\d+$/);

(async () => {
  const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] });
  const errors = [];
  const live = page => page.locator('[data-sync-label]').filter({ hasText: 'Live sync' }).waitFor({ state: 'attached' });
  const page = async (width = 1440) => {
    const context = await browser.newContext({ viewport: { width, height: width === 390 ? 844 : 1000 }, reducedMotion: 'reduce' });
    await context.route('**/*', route => new URL(route.request().url()).origin === origin ? route.continue() : route.abort());
    const p = await context.newPage();
    p.on('pageerror', error => errors.push(`${width}: ${error.message}`));
    await p.goto(origin);
    await live(p);
    return p;
  };
  const seed = async (p, messages, title = 'Recovery fixture') => p.evaluate(async ({ messages, title }) => fuseToolFixture.api('/chat/threads', {
    method: 'POST', body: JSON.stringify({ model: 'offline', tools: [], title, command_id: crypto.randomUUID(), messages }),
  }), { messages, title });
  const open = async (p, id) => {
    await p.goto(`${origin}/chat?t=${id}`);
    await live(p);
    await p.waitForFunction(() => fuseToolFixture.snapshot().length > 0);
  };
  const turn = (user, assistant) => [
    { id: crypto.randomUUID(), role: 'user', content: user },
    { id: crypto.randomUUID(), role: 'assistant', content: assistant },
  ];
  const composer = async p => p.evaluate(() => {
    const send = document.querySelector('[data-chat-send]');
    return {
      streaming: document.body.classList.contains('is-streaming'),
      label: send.getAttribute('aria-label'),
      disabled: send.disabled,
      pending: document.querySelectorAll('.chat-pending-send').length,
      hint: document.querySelector('[data-chat-hint]')?.textContent || '',
    };
  });
  const states = p => p.evaluate(() => [...document.querySelectorAll('[data-chat-log] [data-state]')].map(el => el.dataset.state));
  const storedTurn = p => p.evaluate(() => {
    const raw = JSON.parse(sessionStorage.getItem('fuse.chat.pendingTurn'));
    return Array.isArray(raw) ? raw[0] : raw;
  });
  const box = async (p, selector) => p.locator(selector).evaluate(el => {
    const r = el.getBoundingClientRect();
    return { left: r.left, right: r.right, top: r.top, bottom: r.bottom, width: innerWidth, height: innerHeight, sheet: el.classList.contains('is-sheet') };
  });
  try {
    // A transport failure on an open socket must not leave "Sending…" up.
    {
      const p = await page();
      await p.locator('[data-chat-input]').fill('Send that the socket drops');
      await p.route('**/turns', route => route.abort('failed'), { times: 1 });
      await p.locator('[data-chat-send]').click();
      await p.waitForFunction(() => document.querySelector('[data-sync-label]')?.textContent === 'Reconnecting');
      const after = await composer(p);
      assert.equal(after.streaming, false, 'a dropped send is not a live turn');
      assert.equal(after.pending, 0, 'the Sending… row is gone');
      assert.equal(after.label, 'Send', 'the button is not stuck on Stop');
      assert.equal(after.disabled, true, 'send waits out the reconnect instead of posting blind');
      assert.equal(await p.locator('[data-chat-input]').inputValue(), 'Send that the socket drops', 'the draft survives the drop');
      assert.match(after.hint, /Can't connect to the backend/);
      const id = new URL(p.url()).searchParams.get('t');
      const stored = await storedTurn(p);
      assert.equal(stored.id, id, 'the unconfirmed send is remembered for this thread');
      assert.equal(stored.body.text, 'Send that the socket drops');
      await p.unroute('**/turns');
      await p.reload();
      await p.locator('[data-sync-label]').filter({ hasText: 'Send unconfirmed' }).waitFor({ state: 'attached' });
      await p.waitForFunction(id => new URL(location.href).searchParams.get('t') === id, id);
      const restored = await storedTurn(p);
      assert.equal(restored.body.text, 'Send that the socket drops', 'the unconfirmed send survives the reload');
      assert.equal(await p.locator('[data-sync-label]').innerText(), 'Send unconfirmed. Retry safely.');
      assert.equal(await p.locator('[data-chat-input]').inputValue(), '', 'the draft is not pasted back; the retry owns it');
      assert.equal(await p.locator('[data-sync-retry]').isVisible(), true, 'the retry is offered on the live socket');
      await p.locator('[data-sync-retry]').click();
      await p.waitForFunction(() => !sessionStorage.getItem('fuse.chat.pendingTurn'));
      assert.equal((await composer(p)).pending, 0, 'the retried send clears the guard');
      await p.waitForFunction(() => !document.body.classList.contains('is-streaming'), null, { timeout: 30000 });
      console.log('PASS dropped send re-arms the composer and retries the same turn');
      await p.context().close();
    }

    // A 409 names the rejected send; it must not read as a lost connection.
    {
      const p = await page();
      await p.locator('[data-chat-input]').fill('Send the server rejects');
      await p.route('**/turns', route => route.fulfill({ status: 409, json: { detail: 'Revision changed' } }), { times: 1 });
      await p.locator('[data-chat-send]').click();
      await p.waitForFunction(() => (document.querySelector('[data-chat-hint]')?.textContent || '').includes('Revision changed'));
      const after = await composer(p);
      assert.equal(await p.locator('[data-sync-label]').innerText(), 'Live sync', 'a 409 keeps the socket');
      assert.equal(after.pending, 0);
      assert.equal(after.disabled, false, 'the composer is usable again');
      assert.equal(await p.evaluate(() => sessionStorage.getItem('fuse.chat.pendingTurn')), null, 'a definitive rejection drops the pending turn');
      console.log('PASS a rejected send stays on the live socket');
      await p.context().close();
    }

    // Compaction, a running tool and a wake row all settle when the turn ends.
    {
      const p = await page();
      const created = await seed(p, turn('Compact this', 'Working'));
      await open(p, created.id);

      await p.locator('[data-chat-input]').fill('Queue fixture tool-step compaction');
      await p.locator('[data-chat-send]').click();
      await p.waitForFunction(() => document.body.classList.contains('is-streaming'));
      await p.waitForFunction(() => document.querySelector('[data-tool="read_file"][data-state="running"]'));
      await p.evaluate(() => {
        const message = fuseToolFixture.snapshot().find(row => row.id === fuseToolFixture.runInfo().message_id);
        fuseToolFixture.context.fuse(message, { event: 'context', phase: 'compacting', used: 9000, limit: 10000 });
      });
      await p.waitForFunction(() => document.querySelector('[data-chat-context]').classList.contains('is-busy'));
      const runId = await p.evaluate(() => fuseToolFixture.runInfo().id);
      await p.request.post(`${origin}/__fixture/finish/${runId}`);
      await p.waitForFunction(() => !document.body.classList.contains('is-streaming'), null, { timeout: 30000 });
      await p.waitForFunction(() => !document.querySelector('[data-chat-context]').classList.contains('is-busy'));
      assert.deepEqual((await states(p)).filter(s => s === 'running'), [], 'no tool row stays running after the turn');
      const rows = await p.evaluate(() => fuseToolFixture.snapshot().at(-1).tools.map(t => t.status));
      assert.ok(rows.includes('done'), 'the running tool reaches a terminal status');
      console.log('PASS compaction and tool rows settle with the turn');
      await p.context().close();
    }

    // An edit box survives a snapshot of the same message, and a rewind restores it.
    {
      const p = await page();
      const created = await seed(p, [...turn('Keep this wording', 'First answer'), ...turn('Change this one', 'Second answer')]);
      await open(p, created.id);
      const editBtn = p.locator('[data-chat-log] [aria-label="Edit"]').last();
      assert.ok(await editBtn.count() >= 1, 'the user turn offers Edit');
      await editBtn.evaluate(el => el.click());
      const box = p.locator('[data-edit]');
      await box.waitFor();
      await box.fill('Change this one, revised');
      assert.equal(await box.evaluate(el => document.activeElement === el && el.selectionStart === el.value.length), true, 'the edit opens with the caret at the end');
      await p.locator('[data-edit]').press('Escape');
      assert.equal(await p.locator('[data-edit]').count(), 0, 'escape closes the edit');
      const rewindBtn = p.locator('[data-chat-log] [aria-label="Rewind"]').first();
      assert.ok(await rewindBtn.count() >= 1, 'the user turn offers Rewind');
      await rewindBtn.evaluate(el => el.click());
      await p.waitForFunction(() => document.querySelector('[data-chat-input]').value === 'Keep this wording');
      const caret = await p.locator('[data-chat-input]').evaluate(el => ({ start: el.selectionStart, end: el.selectionEnd, len: el.value.length, focused: document.activeElement === el }));
      assert.deepEqual(caret, { start: 'Keep this wording'.length, end: 'Keep this wording'.length, len: 'Keep this wording'.length, focused: true });
      assert.equal(await p.evaluate(() => fuseToolFixture.snapshot().length), 0, 'rewind drops everything from that prompt on');
      console.log('PASS edit survives a repaint and rewind restores the prompt at the caret');
      await p.context().close();
    }

    // Deleting the open thread, including the last one, never leaves a dead selection.
    {
      const p = await page();
      const older = await seed(p, turn('Older thread', 'Older answer'), 'Older thread');
      const newer = await seed(p, turn('Newer thread', 'Newer answer'), 'Newer thread');
      await open(p, newer.id);
      await p.waitForFunction(id => document.querySelector(`[data-thread="${id}"].is-on`), newer.id);
      const remove = async id => {
        const row = p.locator(`[data-thread="${id}"]`);
        if (!(await row.isVisible())) await p.locator('.chat-bar [data-history-toggle]').click();
        await row.hover();
        await row.locator('button.more').click();
        await p.getByRole('menuitem', { name: 'Delete', exact: true }).click();
        await p.getByRole('dialog').getByRole('button', { name: 'Delete', exact: true }).click();
        await p.waitForFunction(gone => !document.querySelector(`[data-thread="${gone}"]`), id);
        const order = await p.evaluate(async () => {
          const data = await fuseToolFixture.api('/chat/sync');
          return data.threads.filter(item => !item.archived && item.kind !== 'project')
            .sort((a, b) => Number(Boolean(b.running)) - Number(Boolean(a.running)) || String(b.updated_at).localeCompare(String(a.updated_at)))
            .map(item => item.id);
        });
        const ids = [id, ...order];
        const next = ids[1] ?? null;
        const landed = new URL(p.url()).searchParams.get('t');
        assert.equal(landed, next, `opens the next thread in list order, else the previous: ${order.join(',')}`);
        if (landed) assert.equal(await p.locator(`[data-thread="${landed}"].is-on`).count(), 1);
        else assert.equal(await p.locator('[data-thread].is-on').count(), 0, 'the last thread leaves no selection');
      };
      await remove(newer.id);
      const opened = await p.evaluate(() => JSON.stringify({ url: new URL(location.href).searchParams.get('t'), text: fuseToolFixture.snapshot().map(m => m.content).slice(0, 2) }));
      assert.match(opened, /Older thread/, 'the first delete opens the older thread: ' + opened);
      await remove(older.id);
      assert.equal(await p.locator('[data-chat-input]').isVisible(), true, 'the composer stays usable');
      console.log('PASS deleting the open thread lands on the next sibling');
      await p.context().close();
    }

    // Cancelling mid-tool settles every running row and re-arms the composer.
    {
      const p = await page();

      await p.locator('[data-chat-input]').fill('Queue fixture tool-step then cancel');
      await p.locator('[data-chat-send]').click();
      await p.waitForFunction(() => document.querySelector('[data-state="running"]'));
      await p.locator('[data-chat-input]').fill('');
      await p.locator('[data-chat-send]').click();
      await p.waitForFunction(() => !document.body.classList.contains('is-streaming'), null, { timeout: 30000 });
      await p.waitForFunction(() => ![...document.querySelectorAll('[data-chat-log] [data-state]')].some(el => el.dataset.state === 'running'));
      const left = await states(p);
      assert.deepEqual(left.filter(s => s === 'running'), [], `cancel leaves no running row: ${left}`);
      assert.ok(left.every(s => ['done', 'interrupted', 'error', 'warning'].includes(s)), `every row is terminal: ${left}`);
      const after = await composer(p);
      assert.equal(after.label, 'Send');
      assert.equal(after.pending, 0);
      console.log('PASS cancel mid-tool settles every row');
      await p.context().close();
    }

    // Retry resends the last prompt; Send now interrupts the live run.
    {
      const p = await page();
      const created = await seed(p, turn('Retry me', 'First try'));
      await open(p, created.id);
      const retryBtn = p.locator('[data-chat-log] .chat-msg-actions button[aria-label="Retry"]');
      assert.equal(await retryBtn.count(), 1, 'the last assistant turn offers Retry');
      await retryBtn.evaluate(el => el.click());
      await p.waitForFunction(() => document.body.classList.contains('is-streaming'));
      await p.waitForFunction(() => !document.body.classList.contains('is-streaming'), null, { timeout: 30000 });
      const after = await p.evaluate(async id => (await fuseToolFixture.api('/chat/threads/' + id)).messages.map(m => m.content), created.id);
      assert.equal(after.filter(text => text === 'Retry me').length, 1, 'retry replaces the turn instead of duplicating it');
      assert.ok(after.at(-1).includes('Retry me'), 'the new answer follows the retried prompt');
      await p.locator('[data-chat-input]').fill('Hold this turn open for a steer');
      await p.locator('[data-chat-send]').click();
      await p.waitForFunction(() => document.body.classList.contains('is-streaming'));
      const run = await p.evaluate(() => fuseToolFixture.runInfo().id);
      await p.locator('[data-chat-input]').fill('Steer this in');
      await p.locator('[data-chat-send]').click();
      await p.locator('[data-chat-queue]').filter({ hasText: 'Steer this in' }).waitFor();
      await p.locator('[data-queue-send]').click();
      await p.waitForFunction(run => {
        const r = fuseToolFixture.runInfo();
        return r && r.id && r.id !== run && document.querySelector('[data-chat-log]').textContent.includes('Steer this in');
      }, run);
      assert.notEqual(await p.evaluate(() => fuseToolFixture.runInfo().id), run, 'Send now interrupts and starts a new run');
      assert.equal(await p.locator('[data-chat-queue]').isVisible(), false, 'the sent item leaves the queue');
      await p.waitForFunction(() => !document.body.classList.contains('is-streaming'), null, { timeout: 30000 });
      assert.equal((await composer(p)).label, 'Send', 'the composer settles after the interrupted run');
      console.log('PASS retry replaces the turn and Send now interrupts onto a fresh run');
      await p.context().close();
    }

    // A background command nobody tracks must not render Running once the turn is over.
    {
      const p = await page();
      const tool = { id: 'orphan-cmd', name: 'run_terminal_command', status: 'running', state: 'running', execution_status: 'running', command_id: 'orphan-1', command: 'sleep 9999', background: true };
      const created = await seed(p, [
        { id: crypto.randomUUID(), role: 'user', content: 'Run it in the background' },
        { id: crypto.randomUUID(), role: 'assistant', content: 'Started.', tools: [tool], segments: [{ type: 'content', text: 'Started.' }, { type: 'tool', id: 'orphan-cmd' }] },
      ]);
      await open(p, created.id);

      await p.evaluate(() => fuseToolFixture.jobs([]));
      await p.waitForFunction(() => document.querySelector('[data-tool="run_terminal_command"]').dataset.state !== 'running');
      assert.equal(await p.locator('[data-tool="run_terminal_command"]').getAttribute('data-state'), 'interrupted');
      const settled = await p.evaluate(() => fuseToolFixture.snapshot().at(-1).tools[0]);
      assert.equal(settled.error, 'No longer tracked by the server.');
      console.log('PASS an orphaned background command settles instead of staying Running');
      await p.context().close();
    }

    // Opening an old thread hydrates its messages and parks the caret in the composer.
    {
      const p = await page();
      const messages = Array.from({ length: 6 }, (_, i) => turn(`Prompt ${i}`, `Answer ${i} is long enough to hydrate.`)).flat();
      const created = await seed(p, messages, 'Hydration thread');
      await open(p, created.id);
      assert.equal(await p.evaluate(() => fuseToolFixture.snapshot().length), messages.length);
      assert.equal(await p.locator('.is-user').count(), 6, 'every user turn is rendered');
      assert.ok(await p.locator('.is-assistant').count() >= 1, 'answers are hydrated, not left as shells');
      const caret = await p.locator('[data-chat-input]').evaluate(el => ({ value: el.value, start: el.selectionStart, focused: document.activeElement === el }));
      assert.deepEqual(caret, { value: '', start: 0, focused: true });
      console.log('PASS opening an old thread hydrates it and focuses an empty composer');
      await p.context().close();
    }

    // Permission mode, the auto wall, and popovers that must not follow the model name.
    {
      const desktop = await page(1440);
      const created = await seed(desktop, turn('Check permissions', 'Done'));
      await open(desktop, created.id);
      const chip = desktop.locator('[data-perm-chip]');
      assert.equal(await chip.evaluate(el => String(el.classList.contains('is-on'))), 'false', 'a fresh thread asks');
      await chip.click();
      const menu = desktop.locator('.chat-perms-menu');
      await menu.waitFor();
      const ask = await box(desktop, '.chat-perms-menu');
      assert.equal(ask.sheet, false);
      assert.ok(ask.left >= 0 && ask.right <= ask.width, `permissions menu fits: ${JSON.stringify(ask)}`);
      const auto = menu.locator('[data-perms-mode="auto"]');
      assert.equal(await auto.isDisabled(), true, 'auto is walled off without an OpenRouter key');
      assert.match(await auto.getAttribute('title'), /OpenRouter key/);
      assert.equal(await auto.getAttribute('aria-checked'), 'false');
      assert.match(await menu.locator('[data-perms-hint]').innerText(), /OpenRouter key/);
      await menu.locator('[data-perms-mode="bypass"]').click();
      await desktop.waitForFunction(() => document.querySelector('[data-perm-chip]').classList.contains('is-on'));
      assert.match(await chip.getAttribute('aria-label'), /Bypass on/);
      const mode = await desktop.evaluate(async id => (await fuseToolFixture.api('/chat/threads/' + id + '/permissions')).bypass, created.id);
      assert.equal(mode, true, 'the server stored bypass');
      await desktop.keyboard.press('Escape');
      await menu.waitFor({ state: 'hidden' });

      await desktop.locator('.chat-picker-btn').click();
      const picker = desktop.locator('.chat-picker-menu');
      await picker.waitFor();
      const short = await box(desktop, '.chat-picker-menu');
      const longName = 'Offline fixture ' + 'with a very long display name '.repeat(8);
      await desktop.evaluate(name => {
        const btn = document.querySelector('.chat-picker-btn');
        btn.querySelector('span').textContent = name;
      }, longName);
      await desktop.evaluate(() => window.dispatchEvent(new Event('resize')));
      const long = await box(desktop, '.chat-picker-menu');
      assert.ok(Math.abs(long.right - short.right) <= 1, `picker stays right-pinned: ${short.right} -> ${long.right}`);
      assert.ok(long.left >= 0 && long.right <= long.width, 'a long model name cannot push the picker off screen');
      await desktop.keyboard.press('Escape');
      console.log('PASS permission modes and a stable right-pinned picker');
      await desktop.context().close();

      const phone = await page(390);
      await phone.goto(`${origin}/chat?t=${created.id}`);
      await live(phone);
      if (await phone.locator('[data-chat].is-history').count()) await phone.locator(`[data-thread="${created.id}"]`).click();
      await phone.locator('[data-perm-chip]').click();
      const sheet = phone.locator('.chat-perms-menu');
      await sheet.waitFor();
      const placed = await box(phone, '.chat-perms-menu');
      assert.equal(placed.sheet, true, 'a phone gets the full-screen sheet');
      assert.ok(placed.left >= -1 && placed.right <= placed.width + 1, `sheet stays on screen: ${JSON.stringify(placed)}`);
      assert.ok(placed.bottom <= placed.height + 1);
      console.log('PASS the phone permissions menu is a sheet inside the viewport');
      await phone.context().close();
    }

    assert.deepEqual(errors, []);
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
