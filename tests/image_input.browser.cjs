const assert = require('node:assert/strict');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const origin = process.env.FUSE_BROWSER_ORIGIN || 'http://127.0.0.1:8099';
assert.match(origin, /^http:\/\/127\.0\.0\.1:\d+$/);

(async () => {
  const browser = await chromium.launch({ args: ['--no-sandbox'] });
  try {
    for (const width of [1440, 390]) {
      const context = await browser.newContext({ viewport: { width, height: 1000 } });
      await context.route('**/*', route => new URL(route.request().url()).origin === origin ? route.continue() : route.abort());
      await context.route('**/static/chat.js*', async route => {
        const response = await route.fetch();
        let source = await response.text();
        const end = source.lastIndexOf('\n})();');
        source = source.slice(0, end) + `
          window.imageFixture = {
            resizeImage, selectModel, imageTokens,
            setup() {
              const model = currentModel();
              model.image = true;
              const text = { ...model, id: 'fixture-text', name: 'Text only', image: false };
              allModels.push(text); modelsById.set(text.id, text);
              pendingAttachments = [{ kind: 'image', name: 'test.png', data: 'data:image/png;base64,AA==' }];
              renderAttachList(); renderPickerList();
            },
            images() { return pendingAttachments.length; }
          };
        ` + source.slice(end);
        await route.fulfill({ response, body: source });
      });
      const page = await context.newPage();
      const errors = [];
      page.on('pageerror', error => errors.push(error.message));
      await page.goto(origin);
      await page.locator('[data-sync-label]').filter({ hasText: 'Live sync' }).waitFor({ state: 'attached' });
      if (await page.locator('[data-chat].is-history').count()) await page.locator('.chat-history [data-chat-new]').click();
      await page.evaluate(() => imageFixture.setup());
      await page.locator('.chat-picker-btn').click();
      assert.equal(await page.locator('.chat-picker-image').count(), 1);
      await page.evaluate(() => imageFixture.selectModel('fixture-text', true));
      await page.waitForFunction(() => document.querySelector('[data-chat-hint]')?.textContent.includes("can't view images"));
      assert.equal(await page.evaluate(() => imageFixture.images()), 1);
      assert.equal(await page.evaluate(() => imageFixture.imageTokens()), 30);
      await page.evaluate(() => imageFixture.selectModel('offline', true));
      assert.equal(await page.evaluate(() => imageFixture.imageTokens()), 1500);
      assert.equal(await page.evaluate(() => imageFixture.images()), 1);
      const upload = await page.evaluate(async () => {
        const canvas = document.createElement('canvas');
        canvas.width = canvas.height = 4;
        const original = canvas.toDataURL('image/png');
        const blob = await (await fetch(original)).blob();
        const kept = await imageFixture.resizeImage(new File([blob], 'alpha.png', { type: 'image/png' }));
        const assertSame = kept.url === original;
        const big = document.createElement('canvas');
        big.width = 1300; big.height = 10;
        const bigBlob = await (await fetch(big.toDataURL('image/png'))).blob();
        const resized = await imageFixture.resizeImage(new File([bigBlob], 'big.png', { type: 'image/png' }));
        const img = new Image(); img.src = resized.url; await img.decode();
        const sample = document.createElement('canvas'); sample.width = sample.height = 1;
        sample.getContext('2d').drawImage(img, 0, 0);
        return { same: assertSame, mime: kept.mime, resized: resized.mime, pixel: [...sample.getContext('2d').getImageData(0, 0, 1, 1).data] };
      });
      assert.equal(upload.same, true);
      assert.equal(upload.mime, 'image/png');
      assert.equal(upload.resized, 'image/jpeg');
      assert.ok(upload.pixel.slice(0, 3).every(v => v > 245));
      assert.deepEqual(errors, []);
      await context.close();
    }
    console.log('PASS image picker, model switching, PNG retention and JPEG transparency at desktop/mobile widths');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
