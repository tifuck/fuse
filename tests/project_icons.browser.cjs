// Project icons survive the local summary projection, without refresh or model calls.
const assert = require('node:assert/strict');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const origin = process.env.FUSE_BROWSER_ORIGIN || 'http://127.0.0.1:8099';
assert.match(origin, /^http:\/\/127\.0\.0\.1:\d+$/);

(async () => {
  const browser = await chromium.launch({ headless: true, executablePath: process.env.CHROMIUM_PATH || undefined, args: ['--no-sandbox'] });
  const errors = [];
  try {
    for (const width of [1440, 390]) {
      const context = await browser.newContext({ viewport: { width, height: 900 }, reducedMotion: 'reduce' });
      await context.route('**/*', route => new URL(route.request().url()).origin === origin ? route.continue() : route.abort());
      const page = await context.newPage();
      page.on('pageerror', error => errors.push(error.message));
      await page.goto(origin);
      await page.locator('[data-sync-label]').filter({ hasText: 'Live sync' }).waitFor({ state: 'attached' });
      const thread = { id: 'project-icon-fixture', kind: 'project', host_id: 7, project_id: 9, project_name: 'Teal project', host_alias: 'box', title: 'New chat', revision: 1, archived: false, running: false, created_at: '2026-10-03T00:00:00Z', updated_at: '2026-10-03T00:00:00Z', project_color: '#12A594', project_logo_url: '' };
      const project = { id: 9, host_id: 7, name: thread.project_name, host_alias: 'box', color: thread.project_color, logo_url: '' };
      await page.evaluate(({ thread, project }) => fuseToolFixture.sidebar.mount(thread, [project]), { thread, project });
      const row = page.locator('[data-thread="project-icon-fixture"]');
      const folderColor = locator => locator.locator('svg.fp-icon').evaluate(el => el.style.getPropertyValue('--fp-color'));
      const checkColor = async (color, label) => {
        assert.equal(await folderColor(row), color, `${label}: sidebar color`);
        assert.equal(await folderColor(page.locator('[data-project-icon]')), color, `${label}: title color`);
      };
      await checkColor('#12A594', 'new session');
      await page.evaluate(() => {
        document.querySelector('[data-project-sort]').value = 'project';
        document.querySelector('[data-project-sort]').dispatchEvent(new Event('change'));
      });
      assert.equal(await folderColor(page.locator('.chat-history-project-head')), '#12A594', 'group heading color');
      await page.evaluate(thread => fuseToolFixture.event({ id: 0, type: 'thread.updated', thread_id: thread.id, payload: { ...thread, revision: 2, title: 'Renamed session' } }), thread);
      await checkColor('#12A594', 'live update');
      const { project_color, project_logo_url, ...partial } = thread;
      await page.evaluate(thread => fuseToolFixture.sidebar.update({ ...thread, revision: 3 }), partial);
      await checkColor('#12A594', 'omitted metadata');
      const logo = '/static/favicon.svg';
      await page.evaluate(thread => fuseToolFixture.sidebar.update(thread), { ...thread, revision: 4, project_logo_url: logo });
      assert.equal(await row.locator('img.fp-logo').getAttribute('src'), logo, 'logo survives local update');
      assert.equal(await page.locator('[data-project-icon] img.fp-logo').getAttribute('src'), logo, 'title matches sidebar logo');
      assert.equal(await page.locator('.chat-history-project-head img.fp-logo').getAttribute('src'), logo, 'group heading logo');
      await page.evaluate(thread => fuseToolFixture.sidebar.update(thread), { ...partial, revision: 5, archived: true });
      assert.equal((await page.evaluate(() => fuseToolFixture.sidebar.snapshot()))[0].project_logo_url, logo, 'archive keeps omitted icon metadata');
      await page.evaluate(thread => fuseToolFixture.sidebar.update(thread), { ...partial, revision: 6, archived: false });
      assert.equal(await row.locator('img.fp-logo').getAttribute('src'), logo, 'restore keeps logo');
      await page.evaluate(thread => fuseToolFixture.sidebar.update(thread), { ...thread, revision: 7, project_logo_url: '' });
      assert.equal(await row.locator('img.fp-logo').count(), 0, 'explicit empty logo removes image');
      await checkColor('#12A594', 'logo removal');
      // Without the native project cache, the title must use preserved thread metadata too.
      await page.evaluate(thread => fuseToolFixture.sidebar.mount(thread), { ...thread, project_color: '#0090FF' });
      await checkColor('#0090FF', 'snapshot without project cache');
      console.log(`PASS project colors, groups, live updates, partial metadata, logos, archive/restore (${width})`);
      await context.close();
    }
    assert.deepEqual(errors, []);
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
