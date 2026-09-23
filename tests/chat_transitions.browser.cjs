// Real offline HTTP/SSE flows with long histories. Never run against production.
const assert = require('node:assert/strict');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const origin = process.env.FUSE_BROWSER_ORIGIN || 'http://127.0.0.1:8104';
assert.match(origin, /^http:\/\/127\.0\.0\.1:\d+$/);

(async () => {
  const browser = await chromium.launch({ args: ['--no-sandbox'] });
  const errors = [];
  try {
    for (const width of [1440, 390]) for (const view of ['cli', 'compact']) {
      const context = await browser.newContext({ viewport: { width, height: 950 }, reducedMotion: 'no-preference' });
      await context.route('**/*', route => new URL(route.request().url()).origin === origin ? route.continue() : route.abort());
      const page = await context.newPage();
      page.on('pageerror', error => errors.push(error.message));
      await page.goto(origin);
      await page.locator('[data-sync-label]').filter({ hasText: 'Live sync' }).waitFor({ state: 'attached' });
      const created = await page.evaluate(async () => fuseToolFixture.api('/chat/threads', { method: 'POST', body: JSON.stringify({ model: 'offline', tools: [], command_id: crypto.randomUUID(), messages: Array.from({ length: 14 }, (_, i) => [
        { role: 'user', content: `History question ${i}` },
        { role: 'assistant', content: Array.from({ length: 14 }, (_, j) => `History ${i}, paragraph ${j}. This settled answer must stay mounted and retain its reading position.`).join('\n\n'), elapsedMs: 1000 },
      ]).flat() }) }));
      await page.goto(`${origin}/chat?t=${created.id}`);
      await page.waitForFunction(() => fuseToolFixture.snapshot().length >= 28);
      await page.evaluate(view => { fuseToolFixture.view(view); fuseToolFixture.follow(); }, view);
      await page.waitForFunction(() => fuseToolFixture.scrollState().gap <= 1);
      const input = page.locator('[data-chat-input]');
      const send = async text => { await input.fill(text); await page.locator('[data-chat-send]').click(); };
      const watch = async () => page.evaluate(() => {
        window.transitionSamples = [];
        window.transitionWatching = true;
        window.transitionStart = fuseToolFixture.scrollState().top;
        const sample = () => {
          if (!window.transitionWatching) return;
          transitionSamples.push(fuseToolFixture.scrollState().top);
          requestAnimationFrame(sample);
        };
        requestAnimationFrame(sample);
      });
      const check = async label => {
        await page.waitForTimeout(350);
        const result = await page.evaluate(() => { transitionWatching = false; return { start: transitionStart, min: Math.min(...transitionSamples), samples: transitionSamples.length }; });
        assert.ok(result.start > 1200, 'fixture must exercise a genuinely long conversation');
        assert.ok(result.min > result.start - 950, `${label} must not sweep from the top: ${JSON.stringify(result)}`);
      };
      await watch();
      await send('Queue fixture tool-step transitions');
      await page.locator('[data-tool="read_file"][data-state="running"]').waitFor();
      await check('new send');
      await page.evaluate(() => {
        const id = fuseToolFixture.runInfo().message_id;
        window.beforeSteerNode = [...document.querySelectorAll('[data-message-id]')].find(el => el.dataset.messageId === id);
        fuseToolFixture.follow(false);
        window.readingAnchor = [...document.querySelectorAll('.chat-turn.is-user')].at(-3);
        readingAnchor.scrollIntoView({ block: 'center' });
      });
      await page.waitForTimeout(400);
      const readingTop = await page.evaluate(() => readingAnchor.getBoundingClientRect().top);
      await watch();
      await send('Priority instruction');
      await send('Remaining queued instruction');
      await page.locator('[data-queue-send]').first().click();
      await page.waitForFunction(() => document.querySelector('[data-chat-log]').textContent.includes('Acknowledged: Priority instruction'));
      await check('send now');
      assert.equal(await page.evaluate(() => fuseToolFixture.scrollState().following), false, 'steering does not override a manually paused reader');
      assert.ok(Math.abs(await page.evaluate(() => readingAnchor.getBoundingClientRect().top) - readingTop) < 2, 'queued controls and steer insertion preserve the reading anchor');
      await page.evaluate(() => fuseToolFixture.follow());
      await page.waitForFunction(() => fuseToolFixture.scrollState().gap <= 1);
      assert.equal(await page.evaluate(() => beforeSteerNode.isConnected && beforeSteerNode.dataset.messageId !== fuseToolFixture.runInfo().message_id), true, 'the old reply node becomes the sealed prefix in place');
      const frozen = await page.evaluate(() => {
        const id = beforeSteerNode.dataset.messageId;
        const message = fuseToolFixture.snapshot().find(m => m.id === id);
        return { id, elapsed: message.elapsedMs, usage: message.usage };
      });
      await page.waitForTimeout(700);
      const later = await page.evaluate(id => fuseToolFixture.snapshot().find(m => m.id === id), frozen.id);
      assert.equal(later.elapsedMs, frozen.elapsed, 'prefix duration freezes at delivery');
      assert.deepEqual(later.usage, frozen.usage, 'prefix usage cannot borrow continuation tokens');
      await watch();
      await page.locator('[data-chat-send]').click(); // empty composer = Stop
      await page.waitForFunction(() => document.querySelector('[data-chat-log]').textContent.includes('Synchronized answer: Remaining queued instruction'));
      await check('stop and queue drain');
      await page.waitForFunction(() => !fuseToolFixture.runInfo());

      // Stop after acceptance but before delivery: the empty continuation must
      // not leave a standalone token/duration footer, nor block the next turn.
      await send('Queue fixture thinking-step slow-steer');
      await page.locator('.is-live-think').waitFor();
      await send('Accepted immediately before stop');
      const accepted = page.waitForResponse(r => r.url().endsWith('/steer') && r.request().method() === 'POST');
      await page.locator('[data-queue-send]').click();
      assert.equal((await accepted).status(), 200);
      await watch();
      await page.locator('[data-chat-send]').click();
      await page.waitForFunction(() => !fuseToolFixture.runInfo());
      await check('stop before steer delivery');
      assert.equal(await page.evaluate(() => [...document.querySelectorAll('.chat-turn.is-assistant:not([data-deferred-message])')].some(el => !el.hidden && !el.querySelector('[data-flow]')?.textContent.trim() && !el.querySelector('[data-err]')?.textContent.trim())), false, 'no empty assistant artifact/footer');
      const emptyRetry = page.locator('[data-empty-retry]');
      await emptyRetry.waitFor();
      await emptyRetry.click();
      await page.waitForFunction(() => document.querySelector('[data-chat-log]').textContent.includes('Synchronized answer: Accepted immediately before stop'));
      await page.waitForFunction(() => !fuseToolFixture.runInfo());
      await send('Fresh message after stopping');
      await page.waitForFunction(() => document.querySelector('[data-chat-log]').textContent.includes('Synchronized answer: Fresh message after stopping'));
      await page.waitForFunction(() => !fuseToolFixture.runInfo());

      // Hold cancellation in flight and send again while the UI says Stopping.
      await send('Queue fixture tool-step cancel race');
      await page.locator('[data-tool="read_file"][data-state="running"]').last().waitFor();
      await page.route('**/cancel', route => route.fulfill({ status: 503, json: { detail: 'Offline cancellation failure' } }), { times: 1 });
      await page.locator('[data-chat-send]').click();
      await page.waitForFunction(() => fuseToolFixture.runInfo()?.status === 'running' && !fuseToolFixture.runInfo()?.cancel_requested && document.body.classList.contains('is-streaming'));
      await page.waitForTimeout(300);
      assert.equal(await page.locator('[data-live-dock]').isVisible(), true, 'a failed stop cannot leave a phantom stopping state or hide the active indicator');
      let releaseCancel;
      const cancelGate = new Promise(resolve => { releaseCancel = resolve; });
      await page.route('**/cancel', async route => { await cancelGate; await route.continue(); }, { times: 1 });
      await page.locator('[data-chat-send]').click();
      await page.waitForFunction(() => document.querySelector('[data-live-dock-label]').textContent === 'Stopping');
      await send('Message while cancellation is pending');
      assert.equal(await page.locator('.chat-queue-item').count(), 1);
      await watch();
      releaseCancel();
      await page.waitForFunction(() => document.querySelector('[data-chat-log]').textContent.includes('Synchronized answer: Message while cancellation is pending'));
      await check('cancellation-to-next-worker handoff');
      await page.waitForFunction(() => !fuseToolFixture.runInfo());
      await page.waitForFunction(() => fuseToolFixture.scrollState().gap <= 1);
      await page.waitForTimeout(400);
      await watch();
      await page.waitForTimeout(700);
      const idle = await page.evaluate(() => { transitionWatching = false; return Math.max(...transitionSamples) - Math.min(...transitionSamples); });
      assert.ok(idle <= 1, `no idle vertical jitter: ${idle}px`);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
      await context.close();
      console.log(`PASS long-history send/steer/stop/drain/cancel-race, fragment metadata and settled scroll ${view} ${width}`);
    }
    assert.deepEqual(errors, []);
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
