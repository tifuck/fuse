// Synthetic images only; run against the isolated chat browser fixture.
const assert = require('node:assert/strict');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const origin = process.env.FUSE_BROWSER_ORIGIN || 'http://127.0.0.1:8099';
assert.match(origin, /^http:\/\/127\.0\.0\.1:\d+$/);
const image = { mime: 'image/png', data: 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=', width: 1, height: 1, original_width: 2, original_height: 2, format: 'PNG', frame: 0, frames: 3 };
const previewImage = { ...image, preview_url: '/chat/runs/image-child/images/ts_0_0' };
delete previewImage.data;
const trace = { id: 'image-read', name: 'read_file', status: 'done', path: 'tiny.png', image: previewImage };
const message = { id: 'image-message', role: 'assistant', content: '', tools: [trace], segments: [{ type: 'tool', id: trace.id }] };
(async () => {
  const browser = await chromium.launch({ args: ['--no-sandbox'] });
  try {
    const page = await browser.newPage({ reducedMotion: 'reduce' });
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.route('**/*', route => new URL(route.request().url()).origin === origin ? route.continue() : route.abort());
    await page.route('**/chat/runs/image-child', route => route.fulfill({ json: { id: 'image-child', status: 'completed', description: 'Image child', event_cursor: 0, message_id: message.id, message, messages: [] } }));
    await page.route('**/chat/runs/image-child/images/ts_0_0', route => route.fulfill({ contentType: 'image/png', body: Buffer.from(image.data, 'base64') }));
    await page.goto(origin);
    await page.waitForFunction(() => window.fuseToolFixture);
    const roundtrip = await page.evaluate(trace => fuseToolFixture.serializeTrace(fuseToolFixture.merge({}, trace, 'done')), trace);
    assert.deepEqual(roundtrip.image, previewImage);
    assert.deepEqual(await page.evaluate(trace => fuseToolFixture.serializeTrace(trace).image, { ...trace, image }), image);
    for (const width of [1440, 390]) {
      await page.setViewportSize({ width, height: 1000 });
      {

        await page.evaluate(message => fuseToolFixture.mount([message]), message);
        console.log(`Checking ${width}`);
        const check = async scope => {
          let preview = scope.locator('.chat-tool-image [data-image-preview]').first();
          if (!(await preview.isVisible())) {
            const toggles = scope.locator('.chat-step-toggle[aria-expanded="false"]');
            for (let i = 0; i < 4 && !(await preview.isVisible()); i++) {
              if (!(await toggles.count())) throw new Error(await scope.innerHTML());
              await toggles.first().click();
              await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
            }
          }
          assert.equal(await preview.isVisible(), true, `${width}`);
          const figure = scope.locator('.chat-tool-image').first();
          assert.match(await figure.innerText(), /resized from 2 × 2.*frame 1 of 3/);
          assert.match(await figure.innerText(), /Preview only/);
          assert.ok(!(await scope.innerText()).includes(image.data));
          await preview.locator('img').evaluate(img => img.decode());
          await preview.click();
          await page.locator('.chat-lightbox').waitFor({ state: 'visible' });
          assert.equal(await page.locator('.chat-lightbox-img').getAttribute('src'), previewImage.preview_url);
          await page.keyboard.press('Escape');
          await page.locator('.chat-lightbox').waitFor({ state: 'detached' });
        };
        await check(page.locator('[data-chat-log]'));
        await page.evaluate(() => fuseToolFixture.todo.openAgent('image-child'));
        await check(page.locator('[data-agent-log-body]'));
        await page.locator('[data-agents-list] [data-agent-main]').click();
        await page.locator('[data-agent-view]').waitFor({ state: 'hidden' });
      }
    }

    for (const bad of [{ ...image, mime: 'image/svg+xml' }, { ...image, data: '\" onerror=alert(1)' }, { ...image, data: 'https://example.com/a.png' }, { ...image, data: 'AA=A' }, { ...previewImage, preview_url: '//example.com/image' }, { ...previewImage, preview_url: '/chat/runs/../images/ts_0_0' }]) {
      await page.evaluate(trace => fuseToolFixture.mount([{ tools: [trace] }]), { ...trace, image: bad });
      const summary = page.locator('[data-chat-log] .chat-step-toggle[aria-expanded="false"]');
      if (await summary.count()) await summary.first().click();
      assert.equal(await page.locator('[data-chat-log] [data-image-preview]').count(), 0);
      assert.match(await page.locator('[data-chat-log]').innerText(), /Image preview unavailable/);
      assert.equal(await page.evaluate(trace => fuseToolFixture.serializeTrace(trace).image, { ...trace, image: bad }), undefined);
    }
    await page.route('**/chat/runs/image-child/images/ts_9_0', route => route.fulfill({ status: 404, body: '' }));
    await page.evaluate(trace => fuseToolFixture.mount([{ tools: [trace] }]), { ...trace, image: { ...previewImage, preview_url: '/chat/runs/image-child/images/ts_9_0' } });
    const closed = page.locator('[data-chat-log] .chat-step-toggle[aria-expanded="false"]');
    if (await closed.count()) await closed.first().click();
    await page.locator('[data-chat-log] .chat-tool-image .chat-tool-truncated').waitFor({ state: 'visible' });
    assert.equal(await page.locator('[data-chat-log] [data-image-preview]').count(), 0);
    assert.deepEqual(errors, []);
    console.log('PASS image tool preview, lightbox, metadata, persistence and unsafe input in main/subagent across four views and two widths');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
