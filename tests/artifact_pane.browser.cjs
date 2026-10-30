// The artifact pane beside the chat, through its real code path: a call
// drafting, running and publishing; a failed or stopped call never leaves the
// pane on "Building…"; an update keeps the page on screen; the page bridge
// (errors with Fix, send to chat); versions and source; live updates;
// /artifacts and attaching; sharing; a deleted artifact; and the standalone
// /a/ page, which document navigation to /content lands on.
const assert = require('node:assert/strict');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const origin = process.env.FUSE_BROWSER_ORIGIN || 'http://127.0.0.1:8099';
assert.match(origin, /^http:\/\/127\.0\.0\.1:\d+$/);

const PAGE = (marker) => `<!doctype html><html><head><title>t</title></head><body>
<h1 id="top">${marker}</h1><p style="height:2000px">tall</p>
<a id="out" href="https://example.com/x">out</a>
<script>setTimeout(function () { throw new Error("boom from ${marker}"); }, 30);</script>
</body></html>`;

(async () => {
  const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'], ...(process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {}) });
  const errors = [];
  try {
    const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, reducedMotion: 'reduce' });
    await context.grantPermissions(['clipboard-read', 'clipboard-write'], { origin });
    const page = await context.newPage();
    page.on('pageerror', (error) => errors.push(error.message));
    page.on('dialog', (dialog) => dialog.dismiss());
    await page.route('**/*', (route) => new URL(route.request().url()).origin === origin ? route.continue() : route.abort());
    await page.goto(origin);
    await page.locator('[data-sync-label]').filter({ hasText: 'Live sync' }).waitFor({ state: 'attached' });
    await page.waitForFunction(() => window.fuseToolFixture);

    const make = (body) => page.evaluate(async (body) => {
      const res = await fetch('/__fixture/artifact', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
      return res.json();
    }, body);
    const state = () => page.evaluate(() => fuseToolFixture.artifactState());
    const mount = (traces, live) => page.evaluate(([traces, live]) => fuseToolFixture.mountThread([{ role: 'user', content: 'Go.' }, {
      role: 'assistant', model: 'qa/model', elapsedMs: live ? null : 4000, content: 'Done.',
      segments: [...traces.map((t) => ({ type: 'tool', id: t.id })), { type: 'content', text: 'Done.' }], tools: traces,
    }], { live }), [traces, live]);
    const send = (event) => page.evaluate((event) => fuseToolFixture.toolEvent({ event: 'tool', ...event }), event);
    const pane = page.locator('[data-chat-artifact]');
    const toggleText = () => page.locator('[data-artifact-toggle] span').innerText();
    const frameSrc = () => page.locator('[data-artifact-frame]').getAttribute('src');
    const alertText = () => page.locator('[data-artifact-alert-text]').innerText();

    const first = await make({ title: 'Sales board', html: PAGE('first-v1'), icon: '📊' });
    const other = await make({ title: 'Other page', html: PAGE('other-v1') });

    // Drafting: the arguments stream before the call runs.
    await mount([{ id: 'c1', name: 'create_artifact', status: 'running', title: 'Sales board' }], true);
    await page.evaluate(() => fuseToolFixture.patch({ drafting: { tool: 'create_artifact', title: 'Sales board', chars: 12400 } }));
    assert.equal(await toggleText(), 'Writing…', 'drafting shows on the toggle');
    await send({ id: 'c1', name: 'create_artifact', status: 'running', title: 'Sales board' });
    assert.equal(await toggleText(), 'Publishing…', 'a running call shows as publishing');
    assert.equal(await pane.isHidden(), true, 'a closed pane does not jump open while the page is written');

    // Published: the first artifact of a chat opens the pane (desktop).
    await send({ id: 'c1', name: 'create_artifact', status: 'done', title: 'Sales board', identifier: first.identifier, version: 1, visibility: 'private', icon: '📊' });
    await pane.waitFor({ state: 'visible' });
    await page.waitForFunction((id) => (document.querySelector('[data-artifact-frame]').getAttribute('src') || '').includes(`/a/${id}/content`), first.identifier);
    assert.equal(await toggleText(), 'Artifact', 'the toggle settles');
    assert.equal(await page.locator('[data-artifact-icon]').innerText(), '📊');

    // The bridge reports the page's error; Fix puts it in the composer.
    await page.locator('[data-artifact-alert]').waitFor({ state: 'visible', timeout: 8000 });
    assert.match(await alertText(), /Page error: .*boom from first-v1/);
    await page.locator('[data-artifact-fix]').click();
    assert.match(await page.locator('#chat-input').inputValue(), /boom from first-v1.*Please fix it/s);
    const frame = page.frames().find((f) => f.url().includes(`/a/${first.identifier}/content`));
    assert.ok(frame, 'artifact frame is reachable');
    await frame.evaluate(() => window.fuse.sendToChat('Picked: Q3'));
    assert.match(await page.locator('#chat-input').inputValue(), /Picked: Q3$/);
    assert.equal(await frame.evaluate(() => { localStorage.setItem('k', 'v'); return localStorage.getItem('k'); }), 'v', 'storage works in the sandbox');

    // An update keeps the page on screen; a failed one says why, keeps it.
    await mount([{ id: 'c1', name: 'create_artifact', status: 'done', title: 'Sales board', identifier: first.identifier, version: 1 },
      { id: 'u1', name: 'update_artifact', status: 'running', title: 'Sales board', identifier: first.identifier }], true);
    await send({ id: 'u1', name: 'update_artifact', status: 'running', title: 'Sales board', identifier: first.identifier });
    let seen = await state();
    assert.equal(seen.current.identifier, first.identifier, 'the page stays while it is updated');
    assert.equal(await page.locator('[data-artifact-frame]').isVisible(), true);
    await send({ id: 'u1', name: 'update_artifact', status: 'error', title: 'Sales board', error: 'artifact is at v2, not v1' });
    assert.match(await alertText(), /Not published: artifact is at v2/);
    assert.equal((await state()).current.building, false);

    // A create that is stopped never leaves "Building…".
    await mount([{ id: 'c2', name: 'create_artifact', status: 'running', title: 'Doomed' }], true);
    await send({ id: 'c2', name: 'create_artifact', status: 'running', title: 'Doomed' });
    await send({ id: 'c2', name: 'create_artifact', status: 'interrupted', title: 'Doomed' });
    await page.evaluate(() => fuseToolFixture.repaint());
    seen = await state();
    assert.equal(Boolean(seen.current && seen.current.building), false, 'no stuck build after a stop');
    assert.notEqual(await toggleText(), 'Publishing…');

    // A new version published elsewhere shows in place; versions get a picker.
    await mount([{ id: 'c1', name: 'create_artifact', status: 'done', title: 'Sales board', identifier: first.identifier, version: 1 }], false);
    await page.evaluate((id) => fuseToolFixture.artifacts.showArtifact(id, null, { explicit: true }), first.identifier);
    await make({ identifier: first.identifier, html: PAGE('first-v2'), note: 'Q2 numbers' });
    await page.waitForFunction(() => document.querySelectorAll('[data-artifact-version] option').length === 2, null, { timeout: 15000 });
    assert.match(await page.locator('[data-artifact-version]').innerText(), /v2 \(latest\) · Q2 numbers/);
    await page.locator('[data-artifact-version]').selectOption('1');
    assert.match(await frameSrc(), /\?v=1$/);
    await page.locator('[data-artifact-mode="source"]').click();
    await page.waitForFunction(() => document.querySelector('[data-artifact-source]').textContent.includes('first-v1'));
    assert.equal((await page.locator('[data-artifact-source]').textContent()).includes('fuseArtifact'), false, 'source is the stored page, not the served one');
    await page.locator('[data-artifact-menu-toggle]').click();
    assert.equal(await page.locator('[data-artifact-act="revert"]').isVisible(), true, 'an older version offers restore');
    await page.keyboard.press('Escape');
    assert.equal(await page.locator('[data-artifact-menu]').isHidden(), true, 'Escape closes the menu first');
    assert.equal(await pane.isVisible(), true, '... and leaves the pane open');
    await page.locator('[data-artifact-mode="preview"]').click();
    await page.locator('[data-artifact-version]').selectOption('2');

    // Sharing from the pane.
    await page.locator('[data-artifact-menu-toggle]').click();
    await page.locator('[data-artifact-act="share"]').click();
    await page.waitForFunction(() => document.querySelector('[data-artifact-visibility]').textContent === 'shared');

    // /artifacts: this chat first, then the rest; opening one attaches it.
    await page.evaluate(() => fuseToolFixture.slash('artifacts'));
    await page.locator('[data-artifact-pick]').first().waitFor();
    const heads = await page.locator('.chat-artifact-list-head').allTextContents();
    assert.deepEqual(heads, ['This chat', 'Your artifacts']);
    await page.locator(`[data-artifact-pick="${other.identifier}"]`).click();
    await page.waitForFunction((id) => (document.querySelector('[data-artifact-frame]').getAttribute('src') || '').includes(id), other.identifier);
    assert.equal(await page.locator('[data-artifact-title]').innerText(), 'Other page');

    // Ctrl+] closes and reopens it.
    await page.keyboard.press('Control+]');
    assert.equal(await pane.isHidden(), true);
    await page.keyboard.press('Control+]');
    await pane.waitFor({ state: 'visible' });

    // A deleted artifact says so instead of showing an error body.
    await page.evaluate(() => fuseToolFixture.artifacts.showArtifact('deadbeef-0000-4000-8000-000000000000', null, { explicit: true }));
    await page.waitForFunction(() => /deleted or archived/.test(document.querySelector('[data-artifact-alert-text]').textContent));
    assert.equal(await page.locator('[data-artifact-frame]').isHidden(), true);

    // A chat without artifacts closes a pane the reader did not open on purpose.
    await page.evaluate((id) => fuseToolFixture.artifacts.showArtifact(id), first.identifier);
    await mount([{ id: 's1', name: 'web_search', status: 'done', query: 'x', queries: ['x'], results: [] }], false);
    assert.equal(await pane.isHidden(), true, 'pane closes with no artifact in the chat');

    // The standalone page: owner controls, and /content as a document goes there.
    const view = await context.newPage();
    await view.route('**/*', (route) => new URL(route.request().url()).origin === origin ? route.continue() : route.abort());
    await view.goto(`${origin}/a/${first.identifier}/content`);
    assert.equal(new URL(view.url()).pathname, `/a/${first.identifier}`, 'content opened as a page redirects to the viewer');
    assert.equal(await view.locator('.artifact-menu > summary', { hasText: 'Share' }).count(), 1);
    assert.equal(await view.locator('[data-artifact-version] option').count(), 2);
    await view.close();

    assert.deepEqual(errors.filter((message) => !/boom from /.test(message)), [], 'the chat page itself threw');
    console.log('PASS: artifact pane — drafting, publish, bridge, failed and stopped calls, versions, live update, share, /artifacts, Ctrl+], deleted, standalone page');
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
