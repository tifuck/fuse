// Hosts page (/hosts) states no other suite pins.
//
// Empty list, install-code panel, locked-vault SSH gate, pairing failure,
// connector / SSH status dots, project badges, edit dialog, test and probe
// faults, reload, and 1440 / 390 layout.
//
// The fixture user has no unlocked vault, so POST /hosts cannot create rows
// and this suite cannot insert them (no shared-fixture edits). The list is
// therefore served by fulfilling GET /hosts/api with the bundle the page
// already paints. Pairing cancel/delete, and the edit/probe/test calls the
// assertions watch, hit the page's own fetch path. Probe, test and browse
// are fulfilled so nothing dials a machine. /hosts/events is not started by
// the fixture, so that socket is closed immediately.
const assert = require('node:assert/strict');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const origin = process.env.FUSE_BROWSER_ORIGIN || 'http://127.0.0.1:8099';
assert.match(origin, /^http:\/\/127\.0\.0\.1:\d+$/);

const LONG_ALIAS = 'very-long-machine-nickname-edge';
const LONG_PATH = '/srv/' + 'deeply-nested/'.repeat(6) + 'api';
const NOW = '2026-10-05T11:00:00Z';

function host(fields) {
  return Object.assign({
    label: '', port: 22, default_cwd: '', key_algo: '', fingerprint: '', pinned: false,
    disabled: false, online: false, agent_version: '', agent_os: '', agent_arch: '',
    last_seen_at: null, last_ok_at: null, last_error: '', created_at: NOW, updated_at: NOW, projects: [],
  }, fields);
}

const HOSTS = [
  host({ id: 11, alias: 'online-box', hostname: 'edge.example', port: 0, username: 'deploy', auth_type: 'agent', kind: 'agent', online: true, pinned: true, agent_os: 'linux', agent_version: '1.4.0', last_seen_at: NOW, last_ok_at: NOW, projects: [
    { id: 101, name: 'api', remote_path: LONG_PATH, permission_mode: 'read_only', archived: false, notes: '' },
    { id: 102, name: 'old-api', remote_path: '/srv/old', permission_mode: 'project', archived: true, notes: '' },
  ] }),
  host({ id: 12, alias: 'quiet-box', hostname: 'quiet.example', port: 0, username: 'deploy', auth_type: 'agent', kind: 'agent', pinned: true }),
  host({ id: 13, alias: 'untrusted-box', hostname: 'ssh.example', username: 'root', auth_type: 'password', kind: 'ssh' }),
  host({ id: 14, alias: LONG_ALIAS, hostname: 'long.example', username: 'deploy', auth_type: 'key', kind: 'ssh', pinned: true, key_algo: 'ssh-ed25519', fingerprint: 'SHA256:fixturefingerprintfixturefingerprint', last_ok_at: '2026-10-05T10:00:00Z', last_error: 'connection timed out', default_cwd: '/srv/very/long/default/path/that/should/truncate' }),
];
const BUNDLE = { uid: 1, hosts: HOSTS, pending: [], base: origin, settings: { agent_coding: false }, vault: 'locked' };

(async () => {
  const browser = await chromium.launch({ headless: true, executablePath: process.env.CHROMIUM_PATH || undefined, args: ['--no-sandbox'] });
  const errors = [];
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, reducedMotion: 'reduce' });
  await context.route('**/*', route => new URL(route.request().url()).origin === origin ? route.continue() : route.abort());
  const page = await context.newPage();
  page.on('pageerror', error => errors.push(String(error)));
  await page.route('**/hosts/events', route => route.abort());

  let bundle = null;
  let failProbe = false;
  let failTest = false;
  let holdTest = null;
  const json = (route, status, body) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
  await page.route('**/hosts/api', route => bundle ? json(route, 200, bundle) : route.continue());
  await page.route('**/hosts/*/probe', route => json(route, failProbe ? 500 : 200, failProbe
    ? { error: { message: 'Probe fixture failed' } }
    : { algorithm: 'ssh-ed25519', fingerprint: 'SHA256:fixture' }));
  await page.route('**/hosts/*/test', async route => {
    if (holdTest) await holdTest;
    return json(route, failTest ? 500 : 200, failTest ? { error: { message: 'Test fixture failed' } } : { ok: true });
  });
  await page.route('**/hosts/*/browse', route => json(route, 200, {
    path: '/srv', parent: '/', home: '/home/deploy', entries: [{ name: 'api', kind: 'dir' }, { name: 'notes.txt', kind: 'file' }],
  }));
  await page.route('**/hosts/*', route => {
    if (route.request().method() !== 'PATCH') return route.fallback();
    return json(route, 500, { error: { message: 'Save fixture failed' } });
  });

  const overflow = () => page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  const card = id => page.locator(`[data-host-id="${id}"]`);
  // The selected machine's detail pane (list + detail layout).
  const pane = id => page.locator(`[data-detail-host-id="${id}"]`);
  const rowsOf = () => page.locator('[data-host-id]').evaluateAll(nodes => nodes.map(node => ({
    id: node.getAttribute('data-host-id'),
    kind: node.getAttribute('data-kind'),
    title: node.querySelector('.hosts-row-head').getAttribute('title'),
    dot: [...node.querySelector('.hosts-dot').classList].find(name => name.startsWith('is-')),
    chip: node.querySelector('.hosts-chip').textContent,
    projects: node.querySelector('.hosts-row-side').firstChild.textContent.trim(),
    alias: node.querySelector('.hosts-row-name b').textContent,
  })));

  try {
    await page.goto(origin + '/');
    await page.goto(origin + '/hosts');
    await page.locator('[data-hosts]').waitFor();

    const empty = page.locator('[data-empty]');
    const connector = page.locator('[data-connector]');
    const add = page.locator('[data-hosts-add]');
    assert.equal(await empty.isVisible(), true, 'empty state visible with zero hosts');
    assert.equal(await page.locator('[data-host-id]').count(), 0, 'no host rows when the list is empty');
    assert.equal(await add.isVisible(), true, 'Add a machine stays available while empty');
    assert.equal(await connector.isHidden(), true, 'install panel stays hidden until Start');
    assert.ok(await overflow() <= 1, `empty page overflow ${await overflow()}px`);

    await page.locator('[data-empty] [data-wizard-start]').click();
    await connector.waitFor({ state: 'visible' });
    assert.equal(await empty.isHidden(), true, 'empty panel hides once an install code is showing');
    assert.equal(await add.isHidden(), true, `header Add stays visible while installing: hidden=${await add.isHidden()}`);
    const lines = await page.locator('[data-install-line], [data-install-windows]').evaluateAll(nodes => nodes.map(node => node.textContent.trim()));
    const fallback = 'Install code is shown only once. Cancel and create a new code.';
    assert.deepEqual(lines, [fallback, fallback], `install lines: ${JSON.stringify(lines)}`);
    const download = page.locator('[data-download-windows]');
    const downloadState = await download.evaluate(el => ({ hidden: el.hidden, href: el.getAttribute('href'), shown: !!(el.offsetWidth || el.offsetHeight) }));
    assert.deepEqual(downloadState, { hidden: true, href: null, shown: false }, `download link: ${JSON.stringify(downloadState)}`);
    const waiting = await page.locator('[data-connector-wait]').innerText();
    assert.match(waiting, /Waiting for this machine/, `waiting copy: ${waiting}`);
    assert.match(waiting, /Code expires /, `install wait should name the expiry: ${waiting}`);
    const pending = page.waitForResponse(res => res.url().includes('/hosts/pairings/') && res.request().method() === 'DELETE');
    await page.locator('[data-connector-cancel]').click();
    const deleted = await pending;
    const afterCancel = await page.evaluate(() => ({
      empty: document.querySelector('[data-empty]').hidden,
      connector: document.querySelector('[data-connector]').hidden,
      hosts: document.querySelectorAll('[data-host-id]').length,
      error: document.querySelector('[data-page-error]').textContent,
    }));
    assert.equal(deleted.status(), 200, `cancel status ${deleted.status()} body ${afterCancel.error}`);
    assert.deepEqual(afterCancel, { empty: false, connector: true, hosts: 0, error: '' }, `after cancel: ${JSON.stringify(afterCancel)}`);
    console.log('PASS empty state and install panel');

    await page.locator('[data-empty] [data-wizard-start]').click();
    await connector.waitFor({ state: 'visible' });
    await page.locator('[data-ssh-instead]').click();
    await page.locator('.fuse-vault').waitFor();
    assert.equal(await page.locator('[data-wizard]').isHidden(), true, 'locked vault must not open the SSH wizard');
    assert.match(await page.locator('.fuse-vault-err').innerText(), /Unlock with your account password/, 'vault prompt explains the password');
    const vaultBox = await page.locator('.fuse-vault').boundingBox();
    assert.ok(vaultBox && vaultBox.x >= 0 && vaultBox.y >= 0 && vaultBox.x + vaultBox.width <= 1441, `vault prompt off-screen: ${JSON.stringify(vaultBox)}`);
    await page.locator('.fuse-vault-dismiss').click();
    await page.locator('.fuse-vault').waitFor({ state: 'hidden' });
    assert.equal(await connector.isVisible(), true, 'dismissing the vault keeps the install panel');
    await page.locator('[data-connector-cancel]').click();
    await page.waitForFunction(() => document.querySelector('[data-connector]').hidden);
    console.log('PASS locked vault blocks SSH');

    await page.route('**/hosts/pairings', route => route.request().method() === 'POST'
      ? json(route, 500, { error: { message: 'Install code fixture failed' } })
      : route.continue(), { times: 1 });
    await page.locator('[data-empty] [data-wizard-start]').click();
    await page.locator('[data-page-error]').waitFor({ state: 'visible' });
    assert.equal(await page.locator('[data-page-error]').innerText(), 'Install code fixture failed', 'page error quotes the pairing failure');
    assert.equal(await connector.isHidden(), true, 'a failed install code does not open the install panel');
    assert.equal(await empty.isVisible(), true, `empty panel after the failed code: hidden=${await empty.isHidden()}`);
    console.log('PASS pairing 500');

    bundle = BUNDLE;
    // The page paints #hosts-config and the fixture cannot insert host rows, so
    // the document response is rewritten with the bundle. Later reloads and the
    // reload() fetches both read it.
    await page.route('**/hosts', async route => {
      if (route.request().method() !== 'GET' || !bundle) return route.continue();
      const res = await route.fetch();
      const html = (await res.text()).replace(
        /(<script type="application\/json" id="hosts-config">)[\s\S]*?(<\/script>)/,
        (_, open, close) => open + JSON.stringify(bundle).replace(/</g, '\\u003c') + close,
      );
      await route.fulfill({ response: res, body: html });
    });
    await page.reload();
    await card(11).waitFor();
    assert.equal(await empty.isHidden(), true, 'a populated list hides the empty panel');
    let rows = await rowsOf();
    const byAlias = Object.fromEntries(rows.map(row => [row.alias, row]));
    assert.equal(byAlias['online-box'].dot, 'is-ok', `online: ${JSON.stringify(byAlias['online-box'])}`);
    assert.equal(byAlias['online-box'].title, 'Connector online');
    assert.equal(byAlias['online-box'].chip, 'connector');
    assert.equal(byAlias['online-box'].kind, 'agent');
    assert.equal(byAlias['online-box'].projects, '2 projects');
    assert.equal(byAlias['quiet-box'].dot, 'is-idle', `never connected: ${JSON.stringify(byAlias['quiet-box'])}`);
    assert.equal(byAlias['quiet-box'].title, 'Connector offline');
    assert.equal(byAlias['quiet-box'].projects, '0 projects');
    assert.equal(byAlias['untrusted-box'].dot, 'is-warn', `untrusted: ${JSON.stringify(byAlias['untrusted-box'])}`);
    assert.equal(byAlias['untrusted-box'].title, 'Not trusted yet');
    assert.equal(byAlias['untrusted-box'].chip, 'ssh');
    assert.equal(byAlias[LONG_ALIAS].dot, 'is-err', `failed check: ${JSON.stringify(byAlias[LONG_ALIAS])}`);
    assert.equal(byAlias[LONG_ALIAS].title, 'Last check failed');
    assert.deepEqual(rows.map(row => row.alias), ['online-box', 'quiet-box', 'untrusted-box', LONG_ALIAS].sort(), `order ${rows.map(row => row.alias).join(' | ')}`);
    const quietWhen = await card(12).locator('.hosts-when').count();
    assert.equal(quietWhen, 0, 'a connector that has never been seen shows no timestamp');
    const onlineWhen = (await card(11).locator('.hosts-when').innerText()).trim();
    assert.ok(onlineWhen.length > 0, `online row should show when it last checked in: ${onlineWhen}`);
    console.log('PASS status dots, chips, counts, order');

    assert.equal(await pane(11).count(), 1, 'the first machine is selected by default');
    await card(11).locator('[data-toggle-card]').click();
    assert.equal(await card(11).getAttribute('class'), 'hosts-row is-on', 'the selected row carries is-on');
    const readOnly = pane(11).locator('.hosts-proj', { hasText: 'api' }).first();
    await readOnly.waitFor();
    assert.match(await readOnly.innerText(), /read-only/i, 'read-only project shows its badge');
    assert.doesNotMatch(await readOnly.innerText(), /archived/i, 'the live project is not marked archived');
    const archived = pane(11).locator('.hosts-proj', { hasText: 'old-api' });
    assert.match(await archived.innerText(), /archived/i, 'archived project shows its badge');
    assert.match(await archived.getAttribute('class'), /is-archived/, 'archived row carries is-archived');
    const pathBox = await readOnly.locator('.hosts-proj-path').evaluate(el => ({
      text: el.textContent, title: el.getAttribute('title'),
      clipped: el.scrollWidth > el.clientWidth + 1, width: el.clientWidth,
    }));
    assert.equal(pathBox.text, LONG_PATH, `path text ${pathBox.text}`);
    assert.equal(pathBox.title, null, `path exposes its full text as a title: ${JSON.stringify(pathBox)}`);
    assert.ok(pathBox.width > 0 && pathBox.width < 1200, `path row wider than the page: ${JSON.stringify(pathBox)}`);
    await card(12).locator('[data-toggle-card]').click();
    assert.equal(await pane(11).count(), 0, 'selecting another machine replaces the pane');
    assert.match(await pane(12).locator('.hosts-empty-proj').innerText(), /No projects yet/, 'zero projects gets the empty line');
    assert.equal(await pane(12).locator('[data-project-row]').count(), 0, 'the empty line is not an empty project row');
    assert.match(await pane(12).locator('.hosts-pane-status').innerText(), /Offline · never connected/, 'a never-seen connector says so');
    await card(13).locator('[data-toggle-card]').click();
    assert.match(await pane(13).locator('.hosts-warn').innerText(), /Not trusted yet/, 'untrusted pane warns');
    assert.equal((await pane(13).locator('.hosts-warn [data-probe]').innerText()).trim(), 'Trust machine', 'untrusted action is Trust machine');
    await card(14).locator('[data-toggle-card]').click();
    assert.equal((await pane(14).locator(':scope > .callout.fault').innerText()).trim(), 'connection timed out', 'pane shows the last check error');
    const addr = pane(14).locator('.hosts-facts dd.is-trunc').first();
    const addrText = (await addr.innerText()).trim();
    assert.match(addrText, /deploy@long\.example:22/, `address ${addrText}`);
    assert.match(await pane(14).locator('.hosts-facts').innerText(), /ssh-ed25519 SHA256:fixture/, 'fingerprint is shown in full in the pane');
    console.log('PASS detail pane projects, empty, warning, error, truncation');

    await card(11).locator('[data-toggle-card]').click();
    await pane(11).locator('[data-edit-host]').click();
    const dialog = page.locator('[data-edit-host-modal]');
    await dialog.waitFor({ state: 'visible' });
    const portState = await dialog.locator('[data-port-field]').evaluate(el => ({ hidden: el.hidden, shown: !!(el.offsetWidth || el.offsetHeight) }));
    assert.deepEqual(portState, { hidden: true, shown: false }, `connector port field: ${JSON.stringify(portState)}`);
    assert.equal(await dialog.locator('[name="alias"]').inputValue(), 'online-box');
    assert.equal(await dialog.locator('[name="hostname"]').inputValue(), 'edge.example');
    assert.equal(await dialog.locator('[name="username"]').inputValue(), 'deploy');
    let shell = await dialog.locator('.modal-shell').boundingBox();
    assert.ok(shell && shell.x >= -1 && shell.y >= -1 && shell.x + shell.width <= 1441, `dialog box ${JSON.stringify(shell)}`);
    await dialog.locator('[name="alias"]').fill('renamed-box');
    await dialog.locator('[type="submit"]').click();
    await dialog.locator('[data-edit-host-error]').waitFor({ state: 'visible' });
    assert.equal((await dialog.locator('[data-edit-host-error]').innerText()).trim(), 'Save fixture failed', 'save error quotes the API');
    assert.equal(await dialog.isVisible(), true, 'a failed save leaves the editor open');
    assert.equal(await dialog.locator('[name="alias"]').inputValue(), 'renamed-box', 'a failed save keeps the typed nickname');
    assert.equal((await card(11).locator('.hosts-row-name b').innerText()).trim(), 'online-box', 'a failed save does not rename the row');
    await dialog.locator('[data-dialog-close]').first().click();
    await dialog.waitFor({ state: 'hidden' });
    await card(13).locator('[data-toggle-card]').click();
    await pane(13).locator('[data-edit-host]').click();
    await dialog.waitFor({ state: 'visible' });
    assert.equal(await dialog.locator('[data-port-field]').evaluate(el => el.hidden), false, 'SSH edit keeps the port attribute visible');
    assert.equal(await dialog.locator('[name="port"]').inputValue(), '22');
    await dialog.locator('[data-dialog-close]').first().click();
    await dialog.waitFor({ state: 'hidden' });
    console.log('PASS edit dialog: port hidden for a connector, save error sticks');

    let release;
    holdTest = new Promise(resolve => { release = resolve; });
    await card(12).locator('[data-toggle-card]').click();
    await pane(12).locator('[data-test]').click();
    await page.waitForFunction(() => {
      const button = document.querySelector('[data-detail-host-id="12"] [data-test]');
      return button && button.disabled && button.textContent === 'Testing…';
    });
    assert.match(await page.locator('.fuse-toast').innerText(), /quiet-box[\s\S]*Testing connection/, 'pending toast names the machine');
    release();
    holdTest = null;
    await page.waitForFunction(() => {
      const button = document.querySelector('[data-detail-host-id="12"] [data-test]');
      return button && !button.disabled && button.textContent === 'Test';
    });
    failTest = true;
    await pane(12).locator('[data-test]').click();
    await page.locator('.fuse-toast').filter({ hasText: 'Test fixture failed' }).waitFor();
    const toast = await page.locator('.fuse-toast').innerText();
    assert.match(toast, /quiet-box/, `failed toast names the machine: ${toast}`);
    assert.match(toast, /Test fixture failed/, `failed toast quotes the API: ${toast}`);
    await page.waitForFunction(() => {
      const button = document.querySelector('[data-detail-host-id="12"] [data-test]');
      return button && !button.disabled && button.textContent === 'Test';
    });
    failTest = false;
    console.log('PASS test pending and failure');

    failProbe = true;
    await card(13).locator('[data-toggle-card]').click();
    await pane(13).locator('.hosts-warn [data-probe]').click();
    await page.locator('[data-page-error]').waitFor({ state: 'visible' });
    assert.equal((await page.locator('[data-page-error]').innerText()).trim(), 'Probe fixture failed', 'probe 500 uses the page error');
    assert.equal(await pane(13).locator('.hosts-warn').isVisible(), true, 'a failed probe leaves the untrusted warning');
    assert.equal(await card(13).locator('.hosts-dot').evaluate(el => el.classList.contains('is-warn')), true, 'a failed probe does not change the dot');
    failProbe = false;
    console.log('PASS probe 500');

    await page.reload();
    await card(11).waitFor();
    rows = await rowsOf();
    assert.equal(rows.length, 4, `reload row count ${rows.length}: ${rows.map(row => row.alias).join(',')}`);
    assert.equal(await pane(11).count(), 1, 'reload selects the first machine again');
    assert.equal(await pane(13).count(), 0, 'reload does not keep the previous selection');
    assert.equal(await page.locator('[data-page-error]').isHidden(), true, 'reload clears the page error');
    console.log('PASS reload');

    for (const [width, height] of [[1440, 1000], [390, 844]]) {
      await page.setViewportSize({ width, height });
      await page.reload();
      await card(14).waitFor();
      const over = await overflow();
      assert.ok(over <= 1, `${width}: page overflow ${over}px`);
      const clipped = await card(14).locator('.hosts-row-name b').evaluate(el => ({
        text: el.textContent, title: el.getAttribute('title'), over: el.scrollWidth > el.clientWidth + 1,
      }));
      assert.equal(clipped.text, LONG_ALIAS, `${width}: alias ${clipped.text}`);
      assert.equal(clipped.over, false, `${width}: the alias has its own line, so it fits: ${JSON.stringify(clipped)}`);
      assert.equal(clipped.title, null, `${width}: the alias carries no title; the status tooltip is on the row`);
      assert.equal(await card(14).locator('.hosts-row-addr').isVisible(), true, `${width}: address visibility`);
      await card(14).locator('[data-toggle-card]').click();
      const paneOver = await pane(14).evaluate(el => el.scrollWidth - el.clientWidth);
      assert.ok(paneOver <= 1, `${width}: pane overflow ${paneOver}px`);
      await pane(14).locator('[data-edit-host]').click();
      shell = await page.locator('[data-edit-host-modal] .modal-shell').boundingBox();
      assert.ok(shell && shell.x >= -1 && shell.x + shell.width <= width + 1 && shell.y >= -1 && shell.y + shell.height <= height + 1, `${width}: dialog ${JSON.stringify(shell)}`);
      await page.locator('[data-edit-host-modal] [data-dialog-close]').first().click();
      await page.locator('[data-edit-host-modal]').waitFor({ state: 'hidden' });
      console.log('PASS layout', width);
    }

    assert.deepEqual(errors, [], `pageerror: ${errors.join(' | ')}`);
    console.log('PASS hosts page');
  } finally {
    await browser.close();
  }
})().catch(error => {
  console.error(error);
  process.exit(1);
});
