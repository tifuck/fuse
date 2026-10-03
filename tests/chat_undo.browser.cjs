// Acknowledged undo and local composer drafts against the isolated server only.
const assert = require('node:assert/strict');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const origin = process.env.FUSE_BROWSER_ORIGIN || 'http://127.0.0.1:8099';
assert.match(origin, /^http:\/\/127\.0\.0\.1:\d+$/);

(async () => {
  const browser = await chromium.launch({ args: ['--no-sandbox'], executablePath: process.env.CHROMIUM_PATH || undefined });
  const errors = [];
  try {
    for (const width of [1440, 390]) for (const view of ['cli', 'compact', 'console', 'mainframe']) {
      const context = await browser.newContext({ viewport: { width, height: 950 }, reducedMotion: 'reduce' });
      await context.route('**/*', route => new URL(route.request().url()).origin === origin ? route.continue() : route.abort());
      const page = await context.newPage();
      page.on('pageerror', error => errors.push(error.message));
      await page.goto(origin);
      const input = page.locator('[data-chat-input]');
      const live = () => page.locator('[data-sync-label]').filter({ hasText: 'Live sync' }).waitFor({ state: 'attached' });
      const seed = async (texts = ['123'], auto = false) => {
        const created = await page.evaluate(async ({ texts, auto }) => fuseToolFixture.api('/chat/threads', {
          method: 'POST', body: JSON.stringify({ model: 'offline', tools: [], command_id: crypto.randomUUID(), messages: texts.flatMap((content, i) => [
            { id: crypto.randomUUID(), role: 'user', content, ...(auto && i === texts.length - 1 ? { auto: true } : {}) },
            { id: crypto.randomUUID(), role: 'assistant', content: 'Settled response' },
          ]) }),
        }), { texts, auto });
        await page.goto(`${origin}/chat?t=${created.id}`);
        await live();
        await page.waitForFunction(n => fuseToolFixture.snapshot().length === n, texts.length * 2);
        await page.evaluate(view => fuseToolFixture.view(view), view);
        return created;
      };
      const slash = async text => { await input.fill(text); await input.press('Enter'); };
      const value = text => page.waitForFunction(text => document.querySelector('[data-chat-input]').value === text, text);
      const idle = () => page.waitForFunction(() => !FuseChatActions.state().running);
      const count = n => page.waitForFunction(n => fuseToolFixture.snapshot().length === n, n);
      const history = async (id, u) => {
        await page.evaluate(({ id, u }) => sessionStorage.setItem('fuse.chat.undo.' + id, JSON.stringify({ u, r: [] })), { id, u });
        await page.reload(); await live(); await count(2);
        await page.evaluate(view => fuseToolFixture.view(view), view);
      };
      const gatePatch = async () => {
        let release, arrived;
        const gate = new Promise(resolve => { release = resolve; });
        const seen = new Promise(resolve => { arrived = resolve; });
        await page.route('**/chat/threads/*', async route => {
          if (route.request().method() !== 'PATCH') return route.fallback();
          arrived(); await gate; await route.continue();
        }, { times: 1 });
        return { release, seen };
      };

      const first = await seed();
      const gate = await gatePatch();
      await slash('/undo'); await gate.seen;
      assert.equal(await input.inputValue(), '', 'do not restore before acknowledgment');
      assert.equal(await page.evaluate(() => fuseToolFixture.snapshot().length), 2, 'no optimistic removal');
      gate.release(); await value('123'); await idle(); await count(0);
      assert.equal(await input.evaluate(el => document.activeElement === el && el.selectionStart === 3 && el.selectionEnd === 3), true);
      assert.equal(await page.locator('[data-chat-send]').isEnabled(), true);
      assert.equal((await page.evaluate(id => fuseToolFixture.api('/chat/threads/' + id), first.id)).messages.length, 0);
      assert.equal(await page.evaluate(() => document.querySelector('[data-chat]').classList.contains('is-composing')), true);
      await page.evaluate(() => FuseChatActions.run('redo')); await count(2); await idle();
      assert.equal(await input.inputValue(), '123', 'redo preserves restored draft');
      await input.fill(''); await input.press('Control+z'); await value('123'); await idle();

      const exact = '  **Unicode λ**\n```js\n123\n```\n trailing  ';
      await seed([exact]); await page.reload(); await live(); await count(2);
      await slash('/undo'); await value(exact); await idle();
      assert.ok(await input.evaluate(el => el.getBoundingClientRect().height > 30));

      await seed(['earlier', 'latest']);
      await slash('/undo 2'); await value('earlier'); await idle(); await count(0);

      const stacked = await seed(['snapshot prompt']);
      await history(stacked.id, [[]]);
      await slash('/undo'); await value('snapshot prompt'); await idle(); await count(0);

      await seed();
      const draftGate = await gatePatch();
      await slash('/undo'); await draftGate.seen;
      await input.fill('New draft'); await input.fill('');
      draftGate.release(); await idle(); await count(0);
      assert.equal(await input.inputValue(), '', 'typing and clearing still takes ownership of the draft');

      await seed(); await input.fill('Keep existing draft');
      await page.evaluate(() => FuseChatActions.run('undo')); await idle(); await count(0);
      assert.equal(await input.inputValue(), 'Keep existing draft');

      await seed(['automated'], true); await slash('/undo'); await idle(); await count(0);
      assert.equal(await input.inputValue(), '', 'automated user rows are not composer prompts');

      const deleted = await seed();
      const prior = (await page.evaluate(id => fuseToolFixture.api('/chat/threads/' + id), deleted.id)).messages;
      prior.splice(1, 0, { id: 'restored-user', role: 'user', content: 'Previously deleted' });
      await history(deleted.id, [prior]);
      await input.fill('Unrelated draft'); await page.evaluate(() => FuseChatActions.run('undo'));
      await idle(); await count(3);
      assert.equal(await input.inputValue(), 'Unrelated draft', 'undoing deletion does not invent a removed prompt');

      for (const status of [409, 404, 503]) {
        const failed = await seed(); await history(failed.id, [[]]);
        await page.route('**/chat/threads/*', route => route.request().method() === 'PATCH'
          ? route.fulfill({ status, json: { detail: 'Rejected undo fixture' } }) : route.fallback(), { times: 1 });
        await slash('/undo'); await idle(); await count(2);
        assert.equal(await input.inputValue(), '', `no restored text on ${status}`);
        assert.equal(await page.evaluate(id => JSON.parse(sessionStorage.getItem('fuse.chat.undo.' + id)).u.length, failed.id), 1, 'failed undo preserves stack');
        await slash('/undo'); await value('123'); await idle(); await count(0);
      }

      await seed();
      const abort = await gatePatch();
      await slash('/undo'); await abort.seen;
      await page.evaluate(() => FuseChatActions.run('undo'));
      abort.release(); await value('123'); await idle(); await count(0);
      assert.equal(await page.evaluate(() => FuseChatActions.state().canRedo), true);

      if (width === 1440 && view === 'cli') {
        await seed();
        await input.fill('Actual sent prompt'); await page.locator('[data-chat-send]').click();
        await page.waitForFunction(() => fuseToolFixture.runInfo()?.status === 'running');
        await idle(); await count(4);
        await slash('/undo'); await value('Actual sent prompt'); await idle(); await count(2);
        await page.locator('[data-chat-send]').click();
        await page.waitForFunction(() => fuseToolFixture.runInfo()?.status === 'running');
        await idle(); await count(4);
        assert.equal(await input.inputValue(), '', 'restored prompt sends normally');

        const shared = await seed();
        const observer = await context.newPage();
        observer.on('pageerror', error => errors.push(error.message));
        await observer.goto(`${origin}/chat?t=${shared.id}`);
        await observer.waitForFunction(() => fuseToolFixture.snapshot().length === 2);
        await observer.locator('[data-chat-input]').fill('Remote draft');
        await slash('/undo'); await value('123'); await idle();
        await observer.waitForFunction(() => fuseToolFixture.snapshot().length === 0);
        assert.equal(await observer.locator('[data-chat-input]').inputValue(), 'Remote draft', 'undo does not populate another device');
        await observer.close();

        await seed(['first', 'second']);
        let patches = 0;
        await page.route('**/chat/threads/*', async route => {
          if (route.request().method() !== 'PATCH') return route.fallback();
          if (++patches === 2) return route.fulfill({ status: 409, json: { detail: 'Second undo rejected' } });
          await route.continue();
        });
        await slash('/undo 2'); await value('second'); await idle(); await count(2);
        assert.equal(patches, 2, 'multi-undo stops after rejection');
        await page.unroute('**/chat/threads/*');

        const gone = await seed();
        const goneGate = await gatePatch();
        await slash('/undo'); await goneGate.seen;
        await page.evaluate(async ({ id, revision }) => fuseToolFixture.api('/chat/threads/' + id, {
          method: 'DELETE', body: JSON.stringify({ expected_revision: revision, command_id: crypto.randomUUID() }),
        }), gone);
        await page.waitForFunction(() => !new URL(location.href).searchParams.get('t'));
        await input.fill('New chat draft'); goneGate.release(); await idle();
        assert.equal(await input.inputValue(), 'New chat draft', 'deleted origin cannot populate a new chat');
      }

      await seed();
      await input.fill('Queue fixture active undo'); await page.locator('[data-chat-send]').click();
      await page.waitForFunction(() => fuseToolFixture.runInfo()?.status === 'running');
      await slash('/undo');
      assert.equal(await input.inputValue(), '/undo', 'blocked undo retains command');
      assert.equal(await page.locator('.chat-queue-item').count(), 0, 'undo must not reach model queue');
      await page.evaluate(async () => fuseToolFixture.api('/__fixture/finish/' + fuseToolFixture.runInfo().id, { method: 'POST' }));
      await idle();
      assert.equal(await page.evaluate(() => fuseToolFixture.snapshot().some(msg => msg.content === '/undo')), false);
      await context.close();
      console.log(`PASS acknowledged undo, stacks, failure recovery and draft ownership ${view} ${width}`);
    }
    assert.deepEqual(errors, []);
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
