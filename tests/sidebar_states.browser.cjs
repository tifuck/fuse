// Sidebar and history-list states no other suite pins, asserted against the
// latest /chat/threads payload rather than a screenshot.
//
// Already covered, so not repeated here:
//   sidebar_spinner.browser.cjs — a clean first-send turn clears the spinner,
//     and the chat menu opens while that turn runs.
//   project_icons.browser.cjs — one local archive/restore of a mounted row.
//   chat_sync.browser.cjs — rename and delete replication across two pages.
//   test_projects_retention.py — the server unarchive contract, no sidebar.
const assert = require('node:assert/strict');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const origin = process.env.FUSE_BROWSER_ORIGIN || 'http://127.0.0.1:8099';
assert.match(origin, /^http:\/\/127\.0\.0\.1:\d+$/);

const PROMPT = 'Name this sidebar fixture chat from its opening request';
const PREVIEW = 'Name this sidebar fixture chat from…';

(async () => {
  const browser = await chromium.launch({ headless: true, executablePath: process.env.CHROMIUM_PATH || undefined, args: ['--no-sandbox'] });
  const errors = [];
  try {
    const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, reducedMotion: 'reduce' });
    await context.route('**/*', route => new URL(route.request().url()).origin === origin ? route.continue() : route.abort());
    const page = await context.newPage();
    page.on('pageerror', error => errors.push(String(error)));
    const api = (path, opts) => page.evaluate(async ({ path, opts }) => fuseToolFixture.api(path, opts), { path, opts });
    const threads = async () => {
      const data = await api('/chat/threads');
      return { threads: data.threads || [], archived: data.archived || [], projects: data.projects || [] };
    };
    const row = id => page.locator(`[data-thread="${id}"]`);
    const names = () => page.locator('[data-history-list]:visible [data-thread]').evaluateAll(nodes => nodes.map(node => node.getAttribute('data-thread')));
    const openHistory = async () => {
      if (!(await page.locator('[data-history-list]').isVisible())) await page.locator('.chat-bar [data-history-toggle]').click();
    };
    const spins = () => page.evaluate(() => ({
      visible: [...document.querySelectorAll('[data-run-spin]')].filter(el => !el.hidden).map(el => el.getAttribute('data-run-spin')),
      tabs: [...document.querySelectorAll('[data-history-tab]')].map(el => el.getAttribute('aria-busy')),
      title: Boolean(document.querySelector('.chat-bar-title [data-run-spin], [data-chat-title] [data-run-spin]')),
    }));
    const patch = (id, body) => api('/chat/threads/' + id, { method: 'PATCH', body: JSON.stringify({ ...body, command_id: crypto.randomUUID() }) });
    const current = async id => (await threads()).threads.concat((await threads()).archived).find(item => item.id === id);

    await page.goto(origin);
    await page.locator('[data-sync-label]').filter({ hasText: 'Live sync' }).waitFor({ state: 'attached' });
    await openHistory();

    // Nothing is named before the first send, and an empty project list never
    // renders a Projects section of its own.
    assert.equal(await page.locator('[data-chat-title]').innerText(), '');
    assert.equal(await page.locator('.chat-history-kicker', { hasText: 'Projects' }).count(), 0, 'no empty project list on the Chats tab');
    await page.locator('[data-history-tab="projects"]').click();
    await page.locator('.chat-history-empty').waitFor();
    assert.equal(await page.locator('.chat-history-project-head, [data-project]').count(), 0, 'the Projects tab lists sessions, not an empty project list');
    await page.locator('[data-history-tab="chats"]').click();

    // The preview title lands once, during the first turn, and the spinner
    // comes on with it.
    await page.locator('[data-chat-input]').fill(PROMPT);
    await page.locator('[data-chat-send]').click();
    await page.waitForFunction(() => document.body.classList.contains('is-streaming'));
    const id = new URL(page.url()).searchParams.get('t');
    await row(id).waitFor({ state: 'attached' });
    let truth = await threads();
    assert.equal(truth.threads.find(item => item.id === id).title, PREVIEW, 'server: titled from the first prompt while the turn runs');
    assert.equal(truth.threads.find(item => item.id === id).running, true);
    assert.equal(await row(id).locator('.chat-history-name').innerText(), PREVIEW);
    assert.equal(await page.locator('[data-chat-title]').innerText(), PREVIEW, 'the bar shows that one title, not a second rewrite');
    assert.equal(await row(id).getAttribute('aria-busy'), 'true');
    let spin = await spins();
    assert.ok(spin.visible.includes('chats') && spin.visible.some(name => name.startsWith('h-')), `row and tab spin on send: ${JSON.stringify(spin)}`);

    // A subagent that outlives the root turn keeps the spinner. Settling it
    // clears every spinner, because the server then reports running=false.
    const child = await api('/__fixture/sidebar/child', { method: 'POST', body: '{}' });
    await page.waitForFunction(() => !document.body.classList.contains('is-streaming'), null, { timeout: 30000 });
    await page.waitForTimeout(600);
    truth = await threads();
    assert.equal(truth.threads.find(item => item.id === id).running, true, 'server: a live subagent keeps the thread running');
    assert.match(await row(id).getAttribute('class'), /is-running/);
    spin = await spins();
    assert.ok(spin.visible.includes('chats'), `tab spinner survives the turn: ${JSON.stringify(spin)}`);
    await api('/__fixture/sidebar/child/' + child.child_id, { method: 'DELETE' });
    await page.waitForFunction(thread => {
      const item = document.querySelector(`[data-thread="${thread}"]`);
      return item && !item.classList.contains('is-running');
    }, id, { timeout: 15000 });
    truth = await threads();
    assert.equal(truth.threads.find(item => item.id === id).running, false);
    spin = await spins();
    assert.deepEqual(spin.visible, [], `no spinner once the server says stopped: ${JSON.stringify(spin)}`);
    assert.deepEqual(spin.tabs.filter(value => value === 'true'), []);
    console.log('PASS title once on first send; spinner on, held by a subagent, then cleared');

    // A failed turn and a cancelled turn both end running=false, and a later
    // prompt must not rename the chat.
    const send = async text => {
      await page.locator('[data-chat-input]').fill(text);
      await page.locator('[data-chat-send]').click();
      await page.waitForFunction(() => document.body.classList.contains('is-streaming'));
    };
    const stopped = async () => {
      await page.waitForFunction(() => !document.body.classList.contains('is-streaming'), null, { timeout: 30000 });
      await page.waitForTimeout(400);
    };
    await send('Fail this sidebar turn');
    await stopped();
    truth = await threads();
    assert.equal(truth.threads.find(item => item.id === id).running, false);
    assert.equal(truth.threads.find(item => item.id === id).title, PREVIEW, 'a second prompt does not apply another title');
    assert.deepEqual((await spins()).visible, [], 'a failed turn leaves no spinner');
    await send('Cancel this sidebar turn');
    await page.locator('[data-chat-input]').fill('');
    await page.locator('[data-chat-send]').click();
    await stopped();
    truth = await threads();
    assert.equal(truth.threads.find(item => item.id === id).running, false);
    assert.equal(truth.threads.find(item => item.id === id).title, PREVIEW);
    assert.deepEqual((await spins()).visible, [], 'a cancelled turn leaves no spinner');
    console.log('PASS failed and cancelled turns clear the spinner without renaming');

    // The phone title row has no spinner of its own; the sidebar row still does.
    await page.setViewportSize({ width: 390, height: 844 });
    await send('Mobile spinner check for the title row');
    spin = await spins();
    assert.equal(spin.title, false, 'mobile title row shows no run spinner');
    await openHistory();
    assert.equal(await row(id).getAttribute('aria-busy'), 'true', 'the sidebar row still spins on a phone');
    await stopped();
    await page.setViewportSize({ width: 1440, height: 1000 });
    console.log('PASS mobile title row has no run spinner');

    // Title and model changes leave an archive where it is. An edit, a send,
    // an undo and a retry each bring it back, in the server's order, selected.
    const archive = async () => {
      const item = await current(id);
      await patch(id, { archived: true, expected_revision: item.revision });
      await page.waitForFunction(thread => !document.querySelector(`[data-thread="${thread}"]`), id);
    };
    const restored = async (label) => {
      await row(id).waitFor({ state: 'attached', timeout: 20000 });
      truth = await threads();
      assert.ok(truth.threads.some(item => item.id === id), `${label}: server restored the chat`);
      assert.equal(truth.archived.some(item => item.id === id), false);
      await openHistory();
      const shown = await names();
      assert.deepEqual(shown, truth.threads.map(item => item.id), `${label}: sidebar order matches the server`);
      // Archiving the open chat closes it, so a restore of a closed chat is not
      // selected. Reopening it must select exactly that row.
      if (new URL(page.url()).searchParams.get('t') !== id) {
        await row(id).locator('.open').click();
        await page.waitForFunction(thread => new URL(location.href).searchParams.get('t') === thread, id);
      }
      assert.equal(await page.locator('[data-history-list]:visible .is-on').getAttribute('data-thread'), id, `${label}: the open chat is the selected row`);
      const count = page.locator('[data-history-archived-count]');
      if (truth.archived.length) assert.equal(await count.innerText(), String(truth.archived.length));
      else assert.equal(await page.locator('[data-history-archived]').isHidden(), true, `${label}: archived control hides at zero`);
    };
    await openHistory();
    await row(id).hover();
    await row(id).locator('button.more').click();
    await page.getByRole('button', { name: 'Archive', exact: true }).click();
    await page.waitForFunction(() => !new URL(location.href).searchParams.get('t'));
    truth = await threads();
    assert.ok(truth.archived.some(item => item.id === id));
    assert.equal(await page.locator('[data-history-archived-count]').innerText(), String(truth.archived.length), 'archived count tracks the server');
    let item = truth.archived.find(entry => entry.id === id);
    await patch(id, { title: 'Renamed while archived', expected_revision: item.revision });
    truth = await threads();
    assert.ok(truth.archived.some(entry => entry.id === id && entry.title === 'Renamed while archived'), 'a rename leaves it archived');
    assert.equal(await row(id).count(), 0);
    item = truth.archived.find(entry => entry.id === id);
    await patch(id, { model: 'offline', expected_revision: item.revision });
    assert.ok((await threads()).archived.some(entry => entry.id === id), 'a model change leaves it archived');

    const full = await api('/chat/threads/' + id);
    await patch(id, { messages: full.messages.slice(0, -1), expected_revision: full.revision });
    await restored('edit');
    console.log('PASS title and model changes keep an archive; an edit restores it');

    await archive();
    await page.goto(origin + '/chat?t=' + id);
    await page.locator('[data-sync-label]').filter({ hasText: 'Live sync' }).waitFor({ state: 'attached' });
    await send('A message brings the archive back');
    await stopped();
    await restored('send');

    await archive();
    // Retry exists only on the last assistant turn, so it has to run before undo
    // drops that turn.
    await page.locator('[data-chat-log] .chat-msg-actions button[aria-label="Retry"]').last().click();
    await page.waitForFunction(() => document.body.classList.contains('is-streaming'));
    await stopped();
    await restored('retry');

    await archive();
    await page.locator('[data-chat-input]').fill('/undo');
    await page.locator('[data-chat-send]').click();
    await restored('undo');
    console.log('PASS send, undo and retry each restore an archived chat in server order');

    // Saving the transcript unchanged is not activity.
    await archive();
    const shelved = await api('/chat/threads/' + id);
    await patch(id, { messages: shelved.messages, expected_revision: shelved.revision });
    await page.waitForTimeout(600);
    truth = await threads();
    assert.ok(truth.archived.some(entry => entry.id === id), 'a no-op save leaves the chat archived');
    assert.equal(await row(id).count(), 0);
    console.log('PASS a no-op save does not restore an archived chat');

    // A steer is activity too. Archiving closes the chat, so the steer goes
    // through the API while the held turn is still running, then the page
    // reopens onto the restored chat.
    await page.goto(origin + '/chat?t=' + id);
    await page.locator('[data-sync-label]').filter({ hasText: 'Live sync' }).waitFor({ state: 'attached' });
    await send('Steer fixture hold this turn open');
    await page.waitForTimeout(300);
    await archive();
    const steered = await api('/chat/threads/' + id + '/steer', { method: 'POST', body: JSON.stringify({ text: 'Steer this archived chat back', command_id: crypto.randomUUID() }) });
    assert.equal(steered.thread.archived, false, 'server: a steer unarchives the chat');
    await page.goto(origin + '/chat?t=' + id);
    await page.locator('[data-sync-label]').filter({ hasText: 'Live sync' }).waitFor({ state: 'attached' });
    await page.locator('[data-chat-log]').filter({ hasText: 'Steer this archived chat back' }).waitFor({ timeout: 20000 });
    await restored('steer');
    const run = await page.evaluate(() => fuseToolFixture.runInfo());
    if (run) await api('/__fixture/finish/' + run.id, { method: 'POST', body: '{}' });
    await stopped();
    console.log('PASS a steer restores an archived chat');

    // Resume brings a stopped run's chat back. The server only resumes a run
    // that is still the latest turn, so this cancels a fresh one.
    await page.goto(origin + '/chat?t=' + id);
    await page.locator('[data-sync-label]').filter({ hasText: 'Live sync' }).waitFor({ state: 'attached' });
    await send('Cancel for resume');
    await page.locator('[data-chat-input]').fill('');
    await page.locator('[data-chat-send]').click();
    await stopped();
    await archive();
    const runs = await api('/__fixture/sidebar/runs/' + id);
    const cancelled = runs.find(entry => entry.status === 'cancelled');
    assert.ok(cancelled, `a cancelled turn exists: ${JSON.stringify(runs.map(entry => entry.status))}`);
    await api('/__fixture/sidebar/runs/' + cancelled.id + '/transcript', { method: 'POST', body: '{}' });
    await api('/chat/runs/' + cancelled.id + '/resume', { method: 'POST', body: '{}' });
    await restored('resume');
    await stopped();
    console.log('PASS a resume restores an archived chat');

    // A scheduled fire is a turn the user never typed. It restores the chat
    // the same way, and the row carries the task marker.
    const workspace = await api('/__fixture/sidebar/workspace', { method: 'POST', body: '{}' });
    await archive();
    const scheduled = await api('/__fixture/sidebar/scheduled/' + id, { method: 'POST', body: '{}' });
    assert.equal(scheduled.archived, false, 'server: a scheduled turn unarchives the chat');
    await page.goto(origin + '/chat?t=' + id);
    await page.locator('[data-sync-label]').filter({ hasText: 'Live sync' }).waitFor({ state: 'attached' });
    await restored('scheduled run');
    truth = await threads();
    const linked = truth.threads.find(entry => entry.id === id);
    assert.ok(linked.schedule_id, `server: the restored chat stays linked to its schedule ${JSON.stringify(linked)}`);
    assert.match(await row(id).getAttribute('class'), /is-task/, 'the restored row keeps its scheduled marker');
    await page.reload();
    await page.locator('[data-sync-label]').filter({ hasText: 'Live sync' }).waitFor({ state: 'attached' });
    await openHistory();
    assert.match(await row(id).getAttribute('class'), /is-task/, 'a reload keeps the scheduled marker');
    await stopped();
    console.log('PASS a scheduled run restores an archived chat');

    // Moving into a project is offered from the chat menu while a turn runs,
    // and it is refused until the turn ends. Once it lands, the session moves
    // to the Projects tab and an archived target project comes back with it.
    await send('Hold this turn while the menu moves it');
    await openHistory();
    await row(id).hover();
    await row(id).locator('button.more').click();
    const menu = page.locator('.chat-pop');
    await menu.waitFor();
    assert.equal(await page.evaluate(() => document.body.classList.contains('is-streaming')), true);
    assert.match(await menu.innerText(), /Move to project/, 'the move action stays available mid-turn');
    await page.keyboard.press('Escape');
    const refused = await page.evaluate(async ({ id, host, project, revision }) => {
      try {
        await fuseToolFixture.api('/chat/threads/' + id + '/project', { method: 'POST', body: JSON.stringify({ host_id: host, project_id: project, expected_revision: revision, command_id: crypto.randomUUID() }) });
        return { status: 200 };
      } catch (error) {
        return { status: error.status || 0, message: String(error.message || error) };
      }
    }, { id, host: workspace.host_id, project: workspace.project_id, revision: (await current(id)).revision });
    assert.equal(refused.status, 409, `a move during a turn is refused: ${JSON.stringify(refused)}`);
    await page.locator('[data-chat-input]').fill('');
    await page.locator('[data-chat-send]').click();
    await stopped();
    const moved = await api('/chat/threads/' + id + '/project', {
      method: 'POST',
      body: JSON.stringify({ host_id: workspace.host_id, project_id: workspace.project_id, expected_revision: (await current(id)).revision, command_id: crypto.randomUUID() }),
    });
    assert.equal(moved.kind, 'project');
    assert.equal(moved.project_archived, false, 'server: the move restores the archived project');
    await page.locator('[data-history-tab="projects"]').click();
    await row(id).waitFor({ state: 'attached' });
    truth = await threads();
    assert.equal(truth.projects.find(entry => entry.id === workspace.project_id).archived, false, 'the project is back in the sidebar payload');
    assert.equal(await page.locator('[data-history-list]:visible [data-thread]').count() > 0, true);
    assert.equal(await page.locator('.chat-history-empty').count(), 0, 'a project with a session renders no empty-project list');
    const projectOrder = await names();
    assert.deepEqual(projectOrder, truth.threads.filter(entry => entry.kind === 'project').map(entry => entry.id), 'project sessions follow the server order');
    console.log('PASS a mid-turn move is offered, refused, then restores the project');

    assert.deepEqual(errors, []);
    await context.close();
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
