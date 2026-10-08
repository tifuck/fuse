const assert = require('node:assert/strict');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const origin = process.env.FUSE_BROWSER_ORIGIN || 'http://127.0.0.1:8099';
assert.match(origin, /^http:\/\/127\.0\.0\.1:\d+$/);

(async () => {
  const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] });
  const errors = [];
  try {
    for (const width of [1440, 390]) {
      const page = await browser.newPage({ viewport: { width, height: 900 }, reducedMotion: 'reduce' });
      page.on('pageerror', error => errors.push(error.message));
      await page.route('**/*', route => new URL(route.request().url()).origin === origin ? route.continue() : route.abort());
      await page.goto(origin);
      await page.waitForFunction(() => window.fuseToolFixture);
      const marker = { type: 'marker', kind: 'resume', text: 'Resumed after server restart from the last saved tool results.' };
      const before = 'Saved output before the restart.';
      const message = { id: 'resumed-message', role: 'assistant', content: before, reasoning: '', tools: [],
        segments: [{ type: 'content', text: before }, marker] };
      await page.evaluate(message => fuseToolFixture.mountThread([
        { id: 'user-before', role: 'user', content: 'Work' }, message,
      ], { live: true, phase: 'model' }), message);
      const mark = page.locator('[data-resume-marker]');
      await mark.waitFor();
      assert.equal(await mark.count(), 1);
      for (const text of ['First resumed output.', 'More resumed output.\n\n'.repeat(100)]) {
        message.content = before + text;
        message.segments = [{ type: 'content', text: before }, marker, { type: 'content', text }];
        await page.evaluate(message => fuseToolFixture.stream(1, message), message);
        await page.waitForFunction(() => document.querySelector('[data-resume-marker]')?.nextElementSibling?.textContent.includes('resumed output'));
        const shape = await page.evaluate(() => {
          const mark = document.querySelector('[data-resume-marker]');
          const article = mark.closest('article');
          return { before: mark.previousElementSibling.textContent, after: mark.nextElementSibling.textContent,
            inFlow: mark.parentElement.hasAttribute('data-flow'), footerHidden: article.querySelector('[data-notice]').hidden,
            bottom: mark.getBoundingClientRect().bottom, nextTop: mark.nextElementSibling.getBoundingClientRect().top };
        });
        assert.equal(shape.before.trim(), before);
        assert.match(shape.after, /resumed output/);
        assert.equal(shape.inFlow, true);
        assert.equal(shape.footerHidden, true, 'no duplicate footer notice');
        assert.ok(shape.bottom <= shape.nextTop, 'new output is below the fixed chronological marker');
        assert.equal(await mark.count(), 1);
      }
      // Round-trip through the backend parser and reload, then start another turn.
      const thread = await page.evaluate(async message => fuseToolFixture.api('/chat/threads', {
        method: 'POST', body: JSON.stringify({ model: 'offline', tools: [], title: 'Resume marker', command_id: crypto.randomUUID(),
          messages: [{ id: 'user-before', role: 'user', content: 'Work' }, message,
            { id: 'user-next', role: 'user', content: 'Next turn' }, { id: 'assistant-next', role: 'assistant', content: 'New reply' }] }),
      }), message);
      await page.goto(`${origin}/chat?t=${thread.id}`);
      await mark.waitFor();
      assert.equal(await mark.count(), 1);
      assert.equal(await mark.evaluate(el => el.closest('article').dataset.messageId), message.id);
      assert.match(await mark.evaluate(el => el.nextElementSibling.textContent), /resumed output/);
      assert.equal(await page.locator('[data-message-id="assistant-next"] [data-resume-marker]').count(), 0);
      await page.close();
      console.log(`PASS resume marker stays chronological while streaming and after reload at ${width}px`);
    }
    assert.deepEqual(errors, []);
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
