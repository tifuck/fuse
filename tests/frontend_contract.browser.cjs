// Backend/frontend lifecycle contract. Isolated fixture only; no model calls.
const assert = require('node:assert/strict');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const origin = process.env.FUSE_BROWSER_ORIGIN || 'http://127.0.0.1:8099';
assert.match(origin, /^http:\/\/127\.0\.0\.1:\d+$/);
(async () => {
  const browser = await chromium.launch({ headless: true, executablePath: process.env.CHROMIUM_PATH || undefined, args: ['--no-sandbox'] });
  const errors = [];
  try {
    const page = await browser.newPage({ reducedMotion: 'reduce' });
    page.on('pageerror', e => errors.push(e.message));
    await page.route('**/*', route => new URL(route.request().url()).origin === origin ? route.continue() : route.abort());
    await page.goto(origin);
    await page.waitForFunction(() => Boolean(window.fuseToolFixture));
    const row = page.locator('[data-step="tool-contract"]');
    const state = row.locator(':scope > .chat-step-main > .chat-step-head > .chat-step-state');
    const base = { id: 'contract', name: 'get_command_output', status: 'done', execution_status: 'running', state: 'running', command_id: 'canonical-job', background: true, output: '', output_chars: 0, output_chars_exact: true, output_retained_chars: 0, truncated: false, output_mode: 'full', output_start: 0, termination_confirmed: null };
    for (const width of [1440, 390]) {
      await page.setViewportSize({ width, height: 900 });

      await page.evaluate(t => fuseToolFixture.mount([{ tools: [t], segments: [{ type: 'tool', id: t.id }] }], true), base);
      assert.equal(await state.getAttribute('title'), 'Done', 'returned poll is a completed invocation');
      await page.evaluate(() => fuseToolFixture.run({ activity: { phase: 'model' } }));
      assert.match(await page.locator('[data-live-dock-label]').textContent(), /waiting/i, 'durable model phase overrides old pending tool traces before fresh tokens');
      assert.doesNotMatch(await page.locator('[data-live-dock-label]').textContent(), /command/, 'the pending poll must not label the model phase');
      await page.evaluate(() => fuseToolFixture.stream(0, { tools: [{ id: 'contract', name: 'get_command_output', status: 'running', command_id: 'canonical-job' }], segments: [{ type: 'content', text: 'Old text' }, { type: 'tool', id: 'contract' }] }));
      await page.evaluate(() => fuseToolFixture.run({ activity: { phase: 'tools', tool_id: 'contract' } }));
      assert.match(await page.locator('[data-live-dock-label]').textContent(), /waiting for command/);
      await page.evaluate(() => fuseToolFixture.progress(0, { events: [{ fuse: { event: 'activity', phase: 'model', run_id: 'fixture-run' } }] }));
      await page.evaluate(() => fuseToolFixture.update('contract', {}, true));
      assert.match(await page.locator('[data-live-dock-label]').textContent(), /waiting/i, 'streamed activity events update the durable phase projection');
      assert.doesNotMatch(await page.locator('[data-live-dock-label]').textContent(), /command/, 'the durable model phase drops the tool label');

      const execution = { ...base, name: 'run_terminal_command' };
      await page.evaluate(t => fuseToolFixture.mount([{ tools: [{ ...t, state: 'cancelling', execution_status: 'cancelling', termination_confirmed: false }] }]), execution);
      assert.equal(await state.getAttribute('title'), 'Cancelling');
      assert.equal(await row.getAttribute('data-state'), 'running');
      // An empty-output row carries no detail chips and must never claim a clean stop.
      assert.doesNotMatch(await row.evaluate(el => el.textContent), /Termination (confirmed|unknown)/);
      await page.evaluate(() => fuseToolFixture.update('contract', { status: 'interrupted', state: 'cancelled', execution_status: 'cancelled', termination_confirmed: null }));
      assert.equal(await state.getAttribute('title'), 'Cancelled', 'unknown termination must not be presented as confirmed stopped');
      await page.evaluate(t => fuseToolFixture.mount([{ tools: [{ ...t, state: 'timed_out', execution_status: 'timed_out', status: 'error', termination_confirmed: false, error: 'Deadline exceeded' }] }]), execution);
      assert.equal(await state.getAttribute('title'), 'Timed out');
      assert.doesNotMatch(await row.evaluate(el => el.textContent), /Termination confirmed/, 'a timed-out row never claims a confirmed stop');
      await page.evaluate(t => fuseToolFixture.mount([{ tools: [t] }]), execution);
      // New authoritative output must replace even a nonempty stale preview.
      const output = '😀'.repeat(8000);
      const tail = { ...base, state: 'completed', execution_status: 'completed', exit_code: 2, error: 'Command exited with code 2', status: 'error', output, output_chars: 10000, output_retained_chars: 8000, output_start: 2000, output_mode: 'tail', truncated: true, termination_confirmed: true };
      await page.evaluate(job => fuseToolFixture.jobs([{ ...job, id: job.command_id }]), tail);
      assert.equal(await state.getAttribute('title'), 'Exit 2');
      // The retained tail is the row's output body.
      assert.ok((await row.locator('pre').textContent()).includes(output), 'all 8000 Unicode code points are retained');
      const saved = await page.evaluate(t => fuseToolFixture.serializeTrace(t), tail);
      for (const key of ['command_id', 'output', 'output_chars', 'output_chars_exact', 'output_retained_chars', 'truncated', 'output_mode', 'output_start', 'termination_confirmed']) assert.deepEqual(saved[key], tail[key], `serialization preserves ${key}`);
      const merged = await page.evaluate(t => fuseToolFixture.merge(t, { id: t.id, output: '', output_start: null, termination_confirmed: null, exit_code: null }, 'done'), tail);
      assert.equal(merged.output, '');
      assert.equal(merged.output_start, null);
      assert.equal(merged.termination_confirmed, null);
      assert.equal(merged.exit_code, null);
      await page.evaluate(() => fuseToolFixture.update('contract', { status: 'interrupted', state: 'lost', execution_status: 'lost', termination_confirmed: false, error: 'Connection lost' }));
      assert.equal(await state.getAttribute('title'), 'Lost');
      assert.doesNotMatch(await row.evaluate(el => el.textContent), /Termination (confirmed|unknown)/, 'a lost row never claims a known termination');
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
      await page.evaluate(() => {
        fuseToolFixture.mount([{ tools: [{ id: 'poll-old', name: 'get_subagent_output', child_id: 'same-child', status: 'done', execution_status: 'running' }] }], true);
        fuseToolFixture.progress(0, { events: [{ fuse: { event: 'tool', id: 'poll-new', name: 'get_subagent_output', child_id: 'same-child', status: 'running' } }] });
      });
      assert.deepEqual(await page.evaluate(() => fuseToolFixture.snapshot()[0].tools.map(t => [t.id, t.status])), [['poll-old', 'done'], ['poll-new', 'running']], 'distinct polls of the same child retain invocation identity');
      console.log(`PASS activity, returned polls, command identity/output provenance ${width}`);
    }
    await page.setViewportSize({ width: 1440, height: 900 });

    await page.evaluate(() => {
      fuseToolFixture.mount([{ content: '', reasoning: '', tools: [], segments: [] }], true);
      fuseToolFixture.context.accept({ scope: 'prompt', used: 12000, limit: 200000, run_id: 'fixture-run' });
      fuseToolFixture.stream(0, { content: 'abcd'.repeat(40), reasoning: '', segments: [{ type: 'content', text: 'abcd'.repeat(40) }] });
    });
    const down = await page.locator('[data-live-dock-label]').textContent();
    assert.match(down, /↓/, 'console dock counts streamed output with a down arrow');
    assert.doesNotMatch(down, /\b0 tokens\b/);
    const first = Number((down.match(/↓\s+([\d.]+k?)/) || [])[1].replace('k', 'e3'));
    await page.evaluate(() => fuseToolFixture.stream(0, { content: 'abcd'.repeat(80), reasoning: '', segments: [{ type: 'content', text: 'abcd'.repeat(80) }] }));
    const more = await page.locator('[data-live-dock-label]').textContent();
    const second = Number((more.match(/↓\s+([\d.]+k?)/) || [])[1].replace('k', 'e3'));
    assert.ok(second > first, 'down-arrow count ticks as tokens stream in');
    await page.evaluate(() => {
      fuseToolFixture.stream(0, { tools: [{ id: 'bash', name: 'run_terminal_command', status: 'running', command: 'ls' }], segments: [{ type: 'content', text: 'abcd'.repeat(80) }, { type: 'tool', id: 'bash' }] });
      fuseToolFixture.run({ activity: { phase: 'tools', tool_id: 'bash' } });
    });
    const up = await page.locator('[data-live-dock-label]').textContent();
    assert.match(up, /↑/, 'tool execution switches the dock to an up arrow');
    assert.doesNotMatch(up, /12k/, 'up arrow is this round of tool/prompt tokens, not the full context window');
    assert.doesNotMatch(up, /\b0 tokens\b/);
    await page.evaluate(() => {
      fuseToolFixture.progress(0, { events: [{ fuse: { event: 'activity', phase: 'model', run_id: 'fixture-run' } }] });
      fuseToolFixture.stream(0, {
        content: 'abcd'.repeat(80) + 'xyz'.repeat(20),
        reasoning: 'think'.repeat(10),
        tools: [{ id: 'bash', name: 'run_terminal_command', status: 'done', command: 'ls' }],
        segments: [
          { type: 'content', text: 'abcd'.repeat(80) },
          { type: 'tool', id: 'bash' },
          { type: 'reasoning', text: 'think'.repeat(10) },
          { type: 'content', text: 'xyz'.repeat(20) },
        ],
      });
    });
    const next = await page.locator('[data-live-dock-label]').textContent();
    assert.match(next, /↓/, 'next model round switches back to a down arrow');
    const third = Number((next.match(/↓\s+([\d.]+k?)/) || [])[1].replace('k', 'e3'));
    assert.ok(third < second, 'down-arrow count resets for the new thinking/output round');

    await page.evaluate(() => fuseToolFixture.run({ activity: { phase: 'model' } }));
    assert.match(await page.locator('[data-live-dock-label]').textContent(), /↓/, 'model phase keeps the output-token status after a run update');
    console.log('PASS console live dock ↑↓');
    assert.deepEqual(errors, []);
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exitCode = 1; });
