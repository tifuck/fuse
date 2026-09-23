// Optional Playwright regression suite for the context ring state machine.
// Covers the mid-run meter overwrite RCA: standing snapshots and subagent
// meters must never displace the live prompt reading while a run is active.
const assert = require('node:assert/strict');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const origin = process.env.FUSE_BROWSER_ORIGIN || 'http://127.0.0.1:8099';
assert.match(origin, /^http:\/\/127\.0\.0\.1:\d+$/);

(async () => {
  const browser = await chromium.launch({ headless: true, executablePath: process.env.CHROMIUM_PATH || undefined, args: ['--no-sandbox'] });
  const errors = [];
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  await context.route('**/*', route => new URL(route.request().url()).origin === origin ? route.continue() : route.abort());
  const page = await context.newPage();
  page.on('pageerror', error => errors.push(error.message));
  await page.goto(origin);
  await page.locator('[data-sync-label]').filter({ hasText: 'Live sync' }).waitFor({ state: 'attached' });

  // Fixture functions only exist inside the page; evaluate returns JSON.
  const used = () => page.evaluate(() => window.fuseToolFixture.context.snapshot().used);
  const busy = () => page.evaluate(() => window.fuseToolFixture.context.busy());
  const ringTitle = () => page.locator('[data-chat-context]').getAttribute('title');

  try {
    // A live run with an authoritative prompt-scope reading on the ring.
    await page.evaluate(() => {
      const fx = window.fuseToolFixture;
      fx.context.reset();
      fx.mount([{ content: 'turn in flight' }], true);
      fx.context.accept({ event: 'context', phase: 'round', scope: 'prompt', used: 80000, limit: 200000, estimated: true });
    });
    assert.equal(await used(), 80000);
    assert.equal(await ringTitle(), '80k / 200k tokens (40%)');
    await page.evaluate(() => window.fuseToolFixture.context.estimate());
    assert.equal(await used(), 80000, 'stream estimates cannot rebuild provider context from short UI previews');
    await page.evaluate(() => {
      const fx = window.fuseToolFixture;
      fx.stream(0, { content: 'turn in flight' + 'x'.repeat(400) });
      fx.context.estimate();
    });
    assert.equal(await used(), 80100, 'only new output since the authoritative reading is added');
    await page.evaluate(() => window.fuseToolFixture.context.estimate());
    assert.equal(await used(), 80100, 'repeated estimates do not count the same output twice');

    // Mid-run standing snapshot (durable rows without the in-flight turn):
    // the flash RCA — it must be rejected while the live reading exists.
    await page.evaluate(() => window.fuseToolFixture.context.accept({ scope: 'transcript', standing: true, used: 20000, limit: 200000 }));
    assert.equal(await used(), 80100);

    // A subagent's own context meter must never move the thread ring.
    await page.evaluate(() => {
      const fx = window.fuseToolFixture;
      fx.context.fuse(
        { id: 'fixture-message-0' },
        { event: 'context', phase: 'round', used: 3000, limit: 128000, compacting: true },
        false,
        true,
      );
    });
    assert.equal(await used(), 80100);
    assert.equal(await busy(), false);

    // Run-stream context events are live prompt readings even when the
    // backend tags a post-auto-compact meter as transcript scope.
    await page.evaluate(() => window.fuseToolFixture.context.fuse({ id: 'fixture-message-0' }, { event: 'context', phase: 'compacting', used: 80100, limit: 200000 }, false, false));
    assert.equal(await busy(), true);
    await page.evaluate(() => {
      const fx = window.fuseToolFixture;
      fx.context.fuse(
        { id: 'fixture-message-0' },
        { event: 'context', phase: 'compacted', scope: 'transcript', standing: true, used: 6000, limit: 200000 },
        false,
        false,
      );
    });
    assert.equal(await used(), 6000);
    assert.equal(await busy(), false);
    assert.equal(await page.evaluate(() => window.fuseToolFixture.context.snapshot().compacting), false, 'compaction completion clears the meter busy flag as well as local state');
    await page.evaluate(() => window.fuseToolFixture.context.estimate());
    assert.equal(await used(), 6000, 'old visible history cannot inflate a newly compacted prompt');
    await page.evaluate(() => {
      const fx = window.fuseToolFixture;
      fx.context.accept({ scope: 'prompt', used: 5000, limit: 200000, parts: { system: 1000, history: 4000, messages: 4000, thinking: 0, tool_calls: 0, tools: 0, tool_families: {} } });
      fx.stream(0, { reasoning: 'r'.repeat(400) });
      fx.context.estimate();
    });
    assert.equal(await used(), 5100);
    await page.evaluate(() => window.fuseToolFixture.context.accept({ scope: 'prompt', used: 1000, limit: 200000, parts: { system: 1000, history: 0, messages: 0, thinking: 0, tool_calls: 0, tools: 0, tool_families: {} } }));
    const rows = await page.evaluate(() => window.fuseToolFixture.context.rows());
    assert.deepEqual(rows, [['System', 1000, ''], ['History', 0, '']], 'authoritative zero breakdowns must not fall back to visible history');

    // The client must not invent a floor from legacy or cumulative billing.
    await page.evaluate(() => window.fuseToolFixture.context.accept({ event: 'context', phase: 'round', scope: 'prompt', basis: 'payload_chars_v1', used: 10000, limit: 200000, reported_request_tokens: 50000, provider_usage: { total_tokens: 900000 } }));
    assert.equal(await used(), 10000);
    await page.evaluate(() => window.fuseToolFixture.context.estimate());
    assert.equal(await used(), 10000, 'estimate ticks must not restore a provider-usage floor');
    await page.evaluate(() => {
      const fx = window.fuseToolFixture;
      fx.stream(0, { reasoning: 'Long visible thinking '.repeat(5000), tools: [{ id: 'preview-only', name: 'read_file', status: 'done', preview: 'Short preview of a much larger result' }] });
      fx.context.estimate();
    });
    assert.equal(await used(), 10000, 'authoritative payload basis must not be inflated by UI-only reasoning or tool previews');
    for (const basis of ['provider_usage', 'provider_calibrated']) {
      await page.evaluate(basis => window.fuseToolFixture.context.accept({ scope: 'prompt', basis,
        used: 60050, limit: 200000, estimated: basis !== 'provider_usage',
        provider_usage: { prompt_tokens: 60000, completion_tokens: 50, cached_tokens: 59000 } }), basis);
      await page.evaluate(() => window.fuseToolFixture.context.estimate());
      assert.equal(await used(), 60050, 'server measurement/calibration must survive local estimate ticks');
      assert.match(await page.locator('[data-context-pop-last]').textContent(), /60k input tokens/);
    }
    // Every authoritative payload reading can replace the previous reading.
    await page.evaluate(() => window.fuseToolFixture.context.accept({ event: 'context', phase: 'round', scope: 'prompt', used: 9000, limit: 200000 }));
    assert.equal(await used(), 9000);

    // At rest the standing transcript snapshot is authoritative again.
    await page.evaluate(() => {
      const fx = window.fuseToolFixture;
      fx.context.live(false);
      fx.context.accept({ scope: 'transcript', standing: true, used: 20000, limit: 200000 });
    });
    assert.equal(await used(), 20000);
    // Prompt-scope leftovers are ignored once the run has ended.
    await page.evaluate(() => window.fuseToolFixture.context.accept({ scope: 'prompt', used: 99000, limit: 200000 }));
    assert.equal(await used(), 20000);
    assert.equal(await ringTitle(), '20k / 200k tokens (10%)');

    // Stop/start changes run ownership. A previous request must not block the
    // new run's retained snapshot or donate its metadata to the new reading.
    await page.evaluate(() => {
      const fx = fuseToolFixture;
      fx.mount([{ content: 'new run' }], true);
      fx.context.accept({ scope: 'prompt', run_id: 'fixture-run', used: 600000, limit: 1000000, basis: 'provider_usage' });
      fx.run({ id: 'next-run' });
      fx.context.accept({ scope: 'transcript', used: 517000, limit: 1000000, basis: 'provider_calibrated', estimated: true });
    });
    assert.equal(await used(), 517000, 'new-run retained snapshot replaces the previous run prompt');
    await page.evaluate(() => fuseToolFixture.context.accept({ scope: 'prompt', run_id: 'fixture-run', used: 700000, limit: 1000000 }));
    assert.equal(await used(), 517000, 'late old-run meters cannot revive stale occupancy');
    await page.evaluate(() => fuseToolFixture.context.recount());
    assert.equal(await used(), 517000, 'retry/edit cannot replace retained history with a UI-preview estimate');
    assert.equal(await page.evaluate(() => fuseToolFixture.context.snapshot().refreshing), true);
    await page.evaluate(() => fuseToolFixture.context.accept({ scope: 'transcript', used: 510000, limit: 1000000, basis: 'provider_calibrated', estimated: true }));
    assert.equal(await used(), 510000, 'authoritative decreases remain allowed');
    assert.equal(await page.evaluate(() => fuseToolFixture.context.snapshot().refreshing), false);

    await page.setViewportSize({ width: 390, height: 844 });
    await page.locator('[data-chat-context]').click();
    const mobile = await page.locator('[data-context-pop]').evaluate(el => {
      const rect = el.getBoundingClientRect();
      return { visible: !el.hidden, left: rect.left, right: rect.right, width: innerWidth };
    });
    assert.equal(mobile.visible, true);
    assert.ok(mobile.left >= 0 && mobile.right <= mobile.width, 'context popover must fit a narrow viewport');

    assert.deepEqual(errors, []);
    console.log('context meter browser suite passed');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exit(1); });
