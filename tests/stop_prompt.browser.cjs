// The main Stop while background subagents run asks whether they stop too (a
// corner X or Esc dismisses it); without any, Stop acts at once. Real offline
// server, runs and subagent rows; never run against production.
const assert = require('node:assert/strict');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const origin = process.env.FUSE_BROWSER_ORIGIN || 'http://127.0.0.1:8099';
assert.match(origin, /^http:\/\/127\.0\.0\.1:\d+$/);
const shots = process.env.STOP_PROMPT_SHOTS || '';

(async () => {
  const browser = await chromium.launch({ headless: true, executablePath: process.env.CHROMIUM_PATH || undefined, args: ['--no-sandbox'] });
  const errors = [];
  try {
    for (const width of [1440, 390]) {
      const page = await browser.newPage({ viewport: { width, height: 900 }, reducedMotion: 'reduce' });
      page.on('pageerror', (e) => errors.push(e.message));
      await page.route('**/*', (route) => new URL(route.request().url()).origin === origin ? route.continue() : route.abort());
      const cancels = [];
      page.on('request', (req) => {
        const url = new URL(req.url());
        if (req.method() === 'POST' && /^\/chat\/runs\/[^/]+\/cancel$/.test(url.pathname)) cancels.push(url.searchParams.get('scope') || '');
      });
      await page.goto(origin);
      await page.waitForFunction(() => Boolean(window.fuseToolFixture));
      const created = await page.evaluate(() => fuseToolFixture.api('/chat/threads', {
        method: 'POST', body: JSON.stringify({ model: 'offline', tools: [], command_id: crypto.randomUUID(), title: 'Stop prompt' }),
      }));
      await page.goto(`${origin}/chat?t=${created.id}`);
      await page.locator('[data-sync-label]').filter({ hasText: 'Live sync' }).waitFor({ state: 'attached' });
      const input = page.locator('[data-chat-input]');
      const sendBtn = page.locator('[data-chat-send]'); // empty composer = Stop
      const dialog = page.locator('dialog[data-confirm]');
      const start = async (text) => {
        await input.fill(text);
        await sendBtn.click();
        await page.waitForFunction(() => fuseToolFixture.runInfo()?.status === 'running');
      };
      const row = (id) => page.evaluate((id) => {
        const el = document.querySelector(`[data-agents-list] [data-agent-id="${id}"]`);
        return el ? { state: el.querySelector('.chat-agents-state').textContent.trim(), cls: el.closest('.chat-agents-row').className } : null;
      }, id);

      // No background subagent: Stop acts at once.
      await start('Queue fixture tool-step no agents');
      await sendBtn.click();
      await page.waitForFunction(() => !fuseToolFixture.runInfo());
      assert.equal(await dialog.evaluate((el) => el.open), false, 'no prompt without subagents');
      assert.deepEqual(cancels.splice(0), [''], 'a plain Stop');

      // A background subagent: Stop asks first.
      await start('Queue fixture tool-step with agents');
      const { child_id: child } = await page.evaluate(() => fuseToolFixture.api('/__fixture/sidebar/child?background=1', { method: 'POST', body: '{}' }));
      await page.evaluate(() => fuseToolFixture.refreshAgents());
      await page.waitForFunction((id) => Boolean(document.querySelector(`[data-agents-list] [data-agent-id="${id}"]`)), child);
      // The first Stop's "Stopping…" toast has left; the prompt itself holds none.
      await page.waitForFunction(() => !document.querySelector('[data-chat-hint]').classList.contains('is-on'));
      await sendBtn.click();
      await dialog.waitFor({ state: 'visible' });
      const prompt = await page.evaluate(() => {
        const d = document.querySelector('dialog[data-confirm]');
        const x = d.querySelector('[data-confirm-x]');
        const box = d.getBoundingClientRect();
        const xb = x.getBoundingClientRect();
        return {
          text: d.querySelector('[data-confirm-text]').textContent,
          buttons: [...d.querySelectorAll('.modal-actions button')].map((b) => [b.textContent, b.className]),
          x: !x.hidden && getComputedStyle(x).display !== 'none' && xb.width > 0,
          corner: box.right - xb.right < 24 && xb.top - box.top < 24,
          focused: document.activeElement?.textContent,
        };
      });
      assert.equal(prompt.text, 'Stop the main agent?\n1 background subagent is still running. It keeps working unless you choose Stop all.');
      assert.deepEqual(prompt.buttons, [['Stop main agent', 'btn sm'], ['Stop all', 'btn sm danger']]);
      assert.ok(prompt.x && prompt.corner, `an X in the top right corner: ${JSON.stringify(prompt)}`);
      assert.equal(prompt.focused, 'Stop main agent', 'the choice that keeps the subagents has focus');
      assert.equal(await page.evaluate(() => document.querySelector('[data-chat-hint]').classList.contains('is-on')), false, 'nothing says Stopping before a choice');
      if (shots) await page.screenshot({ path: `${shots}/stop-prompt-${width}.png` });

      // The X and Esc close it and stop nothing; the shared confirm gets its own buttons back.
      await dialog.locator('[data-confirm-x]').click();
      await page.waitForFunction(() => !document.querySelector('dialog[data-confirm]').open);
      await sendBtn.click();
      await dialog.waitFor({ state: 'visible' });
      await page.keyboard.press('Escape');
      await page.waitForFunction(() => !document.querySelector('dialog[data-confirm]').open);
      await page.waitForTimeout(300);
      assert.deepEqual(cancels, [], 'dismissing stops nothing');
      assert.equal((await page.evaluate(() => fuseToolFixture.runInfo()))?.status, 'running');
      assert.deepEqual(await dialog.evaluate((d) => [...d.querySelectorAll('.modal-actions button')].map((b) => b.textContent)), ['Cancel', 'Confirm']);
      assert.equal(await dialog.locator('[data-confirm-x]').isHidden(), true);

      // Stop main agent: the reply ends and the subagent keeps working.
      await sendBtn.click();
      await dialog.waitFor({ state: 'visible' });
      await dialog.getByRole('button', { name: 'Stop main agent' }).click();
      await page.waitForFunction(() => !fuseToolFixture.runInfo());
      assert.deepEqual(cancels.splice(0), ['']);
      await page.evaluate(() => fuseToolFixture.refreshAgents());
      assert.match((await row(child)).cls, /\bis-running\b/, 'a background subagent outlives the main Stop');

      // Stop all: the subagent ends too and reads as cancelled, not stopping.
      await start('Queue fixture tool-step stop all');
      await sendBtn.click();
      await dialog.waitFor({ state: 'visible' });
      await dialog.getByRole('button', { name: 'Stop all' }).click();
      await page.waitForFunction(() => !fuseToolFixture.runInfo());
      assert.deepEqual(cancels.splice(0), ['all']);
      // Nothing is live now, so the tree folds; unfold it to read the row.
      await page.waitForFunction(() => document.querySelector('[data-agents-fold]')?.getAttribute('aria-expanded') === 'false', null, { timeout: 10000 });
      await page.evaluate(() => document.querySelector('[data-agents-fold]').click());
      await page.waitForFunction((id) => document.querySelector(`[data-agents-list] [data-agent-id="${id}"] .chat-agents-state`)?.textContent.trim() === 'cancelled', child, { timeout: 10000 });
      await page.close();
      console.log(`PASS stop prompt ${width}`);
    }
    assert.deepEqual(errors, []);
  } finally { await browser.close(); }
})().catch((error) => { console.error(error); process.exitCode = 1; });
