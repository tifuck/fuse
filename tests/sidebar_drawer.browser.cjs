// Chat-page history sidebar: drawer open/close at 390 and 1440, tab states,
// row states, and title truncation — asserted against the DOM and server
// truth (fuseToolFixture.api), never screenshots.
//
// Already pinned elsewhere, so not repeated here:
//   sidebar_states.browser.cjs — titles on send, spinner lifecycle, archive/
//     restore flows, row order vs the server, archived counts, empty Projects
//     tab text, the chat menu mid-turn.
//   sidebar_spinner.browser.cjs — the spinner clears after a first-send turn.
//   chat_sync.browser.cjs — rename/delete replication across two pages, the
//     delete-confirm accept path, whole-page mobile overflow after a delete.
//   project_icons.browser.cjs — project colors/logos, group headings, archive.
//   recovery_states.browser.cjs — deleting the open thread lands on the next
//     sibling with no stale selection.
const assert = require('node:assert/strict');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const origin = process.env.FUSE_BROWSER_ORIGIN || 'http://127.0.0.1:8099';
assert.match(origin, /^http:\/\/127\.0\.0\.1:\d+$/);

const LONG_TITLE = 'Sidebar truncation fixture ' + 'with a very long title '.repeat(14);
const LONG_PROJECT = 'A very long project name '.repeat(10);
const NOW = '2026-10-05T00:00:00Z';

(async () => {
  const browser = await chromium.launch({ headless: true, executablePath: process.env.CHROMIUM_PATH || undefined, args: ['--no-sandbox'] });
  const errors = [];
  const makePage = async (width = 1440) => {
    const context = await browser.newContext({ viewport: { width, height: width === 390 ? 844 : 1000 }, isMobile: width === 390, reducedMotion: 'reduce' });
    await context.route('**/*', route => new URL(route.request().url()).origin === origin ? route.continue() : route.abort());
    const page = await context.newPage();
    page.on('pageerror', error => errors.push(`${width}: ${error.message}`));
    await page.goto(origin);
    await page.locator('[data-sync-label]').filter({ hasText: 'Live sync' }).waitFor({ state: 'attached' });
    return page;
  };
  const api = (page, path, opts) => page.evaluate(async ({ path, opts }) => fuseToolFixture.api(path, opts), { path, opts });
  const seed = (page, title) => api(page, '/chat/threads', {
    method: 'POST',
    body: JSON.stringify({
      model: 'offline', tools: [], title, command_id: crypto.randomUUID(),
      messages: [
        { id: crypto.randomUUID(), role: 'user', content: title + ' prompt' },
        { id: crypto.randomUUID(), role: 'assistant', content: 'Seeded answer for the sidebar drawer fixture.' },
      ],
    }),
  });
  const live = page => page.locator('[data-sync-label]').filter({ hasText: 'Live sync' }).waitFor({ state: 'attached' });
  const toggles = page => page.locator('[data-history-toggle]').evaluateAll(nodes => nodes.map(node => node.getAttribute('aria-expanded')));
  const drawerOpen = page => page.evaluate(() => document.querySelector('[data-chat]').classList.contains('is-history'));
  const openSidebar = async page => {
    if (!(await page.locator('[data-history-list]').isVisible())) {
      await page.locator('.chat-bar [data-history-toggle]').click();
      await page.locator('[data-history-list]').waitFor();
    }
  };
  const noOverflow = page => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth);
  const truncation = (page, locator) => locator.evaluate(el => {
    const style = getComputedStyle(el);
    return { wrap: style.whiteSpace, over: style.textOverflow, clip: style.overflow, clipped: el.scrollWidth > el.clientWidth };
  });
  const rowMenu = async (page, id) => {
    const row = page.locator(`[data-thread="${id}"]`);
    await row.hover();
    await row.locator('button.more').click();
    await page.locator('.chat-pop').waitFor();
  };
  try {
    // Desktop: the collapse toggle, its aria state, and the per-view pref.
    {
      const page = await makePage(1440);
      assert.equal(await page.locator('[data-history-list]').isVisible(), false, 'harness views start with the sidebar collapsed');
      assert.deepEqual(await toggles(page), ['false', 'false', 'false']);
      await page.locator('.chat-bar [data-history-toggle]').click();
      await page.locator('[data-history-list]').waitFor();
      assert.deepEqual(await toggles(page), ['true', 'true', 'true'], 'every toggle reflects the open sidebar');
      assert.equal((await page.evaluate(() => JSON.parse(localStorage.getItem('fuse.chat.historyCollapsed.v1') || '{}'))).cli, false, 'the open state is remembered for this view');
      assert.equal(await noOverflow(page), true, 'an open sidebar never overflows the window');
      await page.locator('.chat-history-head [data-history-toggle]').click();
      await page.locator('[data-history-list]').waitFor({ state: 'hidden' });
      assert.deepEqual(await toggles(page), ['false', 'false', 'false'], 'the head toggle collapses the sidebar');
      assert.equal((await page.evaluate(() => JSON.parse(localStorage.getItem('fuse.chat.historyCollapsed.v1') || '{}'))).cli, true, 'the collapsed state is remembered too');
      await page.locator('.chat-bar [data-history-toggle]').click();
      await page.locator('[data-history-list]').waitFor();
      await page.reload();
      await live(page);
      await page.locator('[data-history-list]').waitFor();
      assert.deepEqual(await toggles(page), ['true', 'true', 'true'], 'the open sidebar survives a reload');
      console.log('PASS desktop sidebar toggle, aria-expanded, and per-view persistence');
      await page.context().close();
    }

    // Tabs: active indicator, per-tab new action, filters, empty states, and
    // the tab itself surviving a reload. Runs before any thread is seeded so
    // the empty states are real.
    {
      const page = await makePage(1440);
      await openSidebar(page);
      const tabState = () => page.evaluate(() => [...document.querySelectorAll('[data-history-tab]')].map(el => ({
        tab: el.getAttribute('data-history-tab'), selected: el.getAttribute('aria-selected'), on: el.classList.contains('is-on'),
      })));
      const rows = () => page.locator('[data-history-list] [data-thread]').evaluateAll(nodes => nodes.map(node => node.getAttribute('data-thread')));
      await page.locator('.chat-history-empty').waitFor();
      assert.equal(await page.locator('.chat-history-empty').innerText(), 'No chats yet.');
      assert.deepEqual(await tabState(), [
        { tab: 'chats', selected: 'true', on: true },
        { tab: 'projects', selected: 'false', on: false },
      ]);
      await page.locator('[data-history-tab="projects"]').click();
      assert.deepEqual(await tabState(), [
        { tab: 'chats', selected: 'false', on: false },
        { tab: 'projects', selected: 'true', on: true },
      ]);
      assert.equal(await page.locator('.chat-history-empty').innerText(), 'No project sessions yet. Use New project to start one.');
      assert.equal(await page.locator('[data-chat-new-label]').textContent(), 'New project', 'the new action renames per tab');
      assert.equal(await page.locator('[data-chat-new]').getAttribute('aria-label'), 'New project session');
      assert.equal(await page.locator('[data-project-filters]').isHidden(), false, 'project filters show on the Projects tab');
      await page.locator('[data-history-tab="chats"]').click();
      assert.equal(await page.locator('[data-chat-new-label]').textContent(), 'New chat');
      assert.equal(await page.locator('[data-chat-new]').getAttribute('aria-label'), 'New chat');
      assert.equal(await page.locator('[data-project-filters]').isHidden(), true, 'project filters leave with the tab');

      const chatRow = { id: 'tab-chat', kind: 'chat', title: 'Plain chat for tabs', revision: 1, archived: false, running: false, created_at: NOW, updated_at: NOW };
      const projectRow = { id: 'tab-project', kind: 'project', title: 'Project session for tabs', project_name: 'Tab project', host_alias: 'box', host_id: 7, project_id: 9, project_color: '#12A594', project_logo_url: '', revision: 1, archived: false, running: false, created_at: NOW, updated_at: NOW };
      await page.evaluate(({ chatRow, projectRow }) => {
        fuseToolFixture.sidebar.mount(chatRow);
        fuseToolFixture.sidebar.update(projectRow);
      }, { chatRow, projectRow });
      await page.locator('[data-history-tab="chats"]').click();
      assert.deepEqual(await rows(), ['tab-chat'], 'the Chats tab lists only plain chats');
      await page.locator('[data-history-tab="projects"]').click();
      assert.deepEqual(await rows(), ['tab-project'], 'the Projects tab lists only project sessions');
      await page.reload();
      await live(page);
      await openSidebar(page);
      assert.equal(await page.locator('[data-history-tab="projects"]').getAttribute('aria-selected'), 'true', 'the active tab survives a reload');
      assert.equal(await page.locator('[data-history-tab="chats"]').getAttribute('aria-selected'), 'false');
      console.log('PASS tab indicator, per-tab new action, filters, empty states, and reload persistence');
      await page.context().close();
    }

    // Long titles and project names truncate on one line with an ellipsis and
    // a full-title tooltip; the row never grows with the text.
    for (const width of [1440, 390]) {
      const page = await makePage(width);
      await openSidebar(page);
      const threads = {
        long: { id: 'long-title', kind: 'chat', title: LONG_TITLE, revision: 1, archived: false, running: false, created_at: NOW, updated_at: NOW },
        short: { id: 'short-title', kind: 'chat', title: 'Short', revision: 1, archived: false, running: false, created_at: NOW, updated_at: NOW },
        project: { id: 'long-project', kind: 'project', title: 'Project session row', project_name: LONG_PROJECT, host_alias: 'box', host_id: 7, project_id: 9, project_color: '#12A594', project_logo_url: '', revision: 1, archived: false, running: false, created_at: NOW, updated_at: NOW },
      };
      await page.evaluate(threads => {
        fuseToolFixture.sidebar.mount(threads.long);
        fuseToolFixture.sidebar.update(threads.short);
        fuseToolFixture.sidebar.update(threads.project);
      }, threads);
      const name = page.locator('[data-thread="long-title"] .chat-history-name');
      assert.deepEqual(await truncation(page, name), { wrap: 'nowrap', over: 'ellipsis', clip: 'hidden', clipped: true }, 'the long title is one ellipsized line');
      assert.equal(await page.locator('[data-thread="long-title"] button.open').getAttribute('title'), LONG_TITLE, 'the full title stays available as a tooltip');
      const heights = await page.evaluate(() => ({
        longName: document.querySelector('[data-thread="long-title"] .chat-history-name').getBoundingClientRect().height,
        shortName: document.querySelector('[data-thread="short-title"] .chat-history-name').getBoundingClientRect().height,
        long: document.querySelector('[data-thread="long-title"]').getBoundingClientRect().height,
        short: document.querySelector('[data-thread="short-title"]').getBoundingClientRect().height,
      }));
      assert.ok(Math.abs(heights.longName - heights.shortName) <= 1, `the title stays one line: ${JSON.stringify(heights)}`);
      assert.ok(Math.abs(heights.long - heights.short) <= 1, `a long title must not blow up the row: ${JSON.stringify(heights)}`);
      assert.ok(heights.long < 60, `rows stay compact at ${width}: ${JSON.stringify(heights)}`);
      await page.locator('[data-history-tab="projects"]').click();
      await page.locator('[data-thread="long-project"]').waitFor();
      const projectHeight = await page.locator('[data-thread="long-project"]').evaluate(el => el.getBoundingClientRect().height);
      assert.ok(projectHeight < 76, `the two-line project row stays two lines at ${width}: ${projectHeight}`);
      const meta = page.locator('[data-thread="long-project"] .chat-history-project').first();
      assert.deepEqual(await truncation(page, meta), { wrap: 'nowrap', over: 'ellipsis', clip: 'hidden', clipped: true }, 'a long project name truncates on the meta line');
      await page.evaluate(() => {
        const select = document.querySelector('[data-project-sort]');
        select.value = 'project';
        select.dispatchEvent(new Event('change'));
      });
      const headName = page.locator('.chat-history-project-head .chat-history-project');
      assert.equal((await truncation(page, headName)).over, 'ellipsis', 'group headings truncate a long project name');
      assert.equal((await truncation(page, headName)).clipped, true);
      assert.ok(await headName.evaluate(el => el.parentElement.getBoundingClientRect().height < 48), 'the group heading stays one line');
      assert.equal(await noOverflow(page), true, `${width}: long names never overflow the window`);
      console.log(`PASS long titles and project names truncate without wrapping (${width})`);
      await page.context().close();
    }

    // Mobile drawer: opens as the home list, toggles, scrim, escape, fit.
    {
      const page = await makePage(390);
      const a = await seed(page, 'First drawer fixture thread');
      const b = await seed(page, 'Second drawer fixture thread');
      await page.reload();
      await live(page);
      await page.locator(`[data-thread="${a.id}"]`).waitFor();
      assert.equal(await drawerOpen(page), true, 'a phone opens on the chat list');
      assert.equal(await page.locator('[data-history]').isVisible(), true);
      assert.equal(await page.locator('[data-history-backdrop]').isHidden(), false, 'the scrim is shown behind the drawer');
      assert.deepEqual(await toggles(page), ['true', 'true', 'true']);
      const box = await page.locator('[data-history]').boundingBox();
      assert.ok(box.x >= -1 && box.x + box.width <= 391 && box.y >= -1 && box.y + box.height <= 845, `the drawer fits the viewport: ${JSON.stringify(box)}`);
      assert.equal(await noOverflow(page), true);
      const rowBox = await page.locator(`[data-thread="${b.id}"]`).boundingBox();
      assert.ok(rowBox.x >= box.x - 1 && rowBox.x + rowBox.width <= box.x + box.width + 1, `rows sit inside the drawer: ${JSON.stringify(rowBox)}`);
      assert.equal(await page.locator(`[data-thread="${b.id}"] button.open`).evaluate(el => el.getBoundingClientRect().height >= 44), true, 'rows are tap-sized');

      await page.locator('.chat-history-head [data-history-toggle]').click();
      await page.waitForFunction(() => !document.querySelector('[data-chat]').classList.contains('is-history'));
      assert.equal(await page.locator('[data-history]').isVisible(), false);
      assert.equal(await page.locator('[data-history-backdrop]').isHidden(), true, 'the scrim goes away with the drawer');
      assert.deepEqual(await toggles(page), ['false', 'false', 'false'], 'toggles reflect the closed drawer');

      await page.locator('.chat-bar [data-history-toggle]').click();
      await page.waitForFunction(() => document.querySelector('[data-chat]').classList.contains('is-history'));
      assert.deepEqual(await toggles(page), ['true', 'true', 'true']);
      // The scrim sits under the full-width drawer once it settles, so the
      // close click is dispatched on the element itself (its handler is the
      // contract under test).
      await page.locator('[data-history-backdrop]').dispatchEvent('click');
      await page.waitForFunction(() => !document.querySelector('[data-chat]').classList.contains('is-history'));
      assert.equal(await page.locator('[data-history-backdrop]').isHidden(), true);

      await page.locator('.chat-bar [data-history-toggle]').click();
      await page.waitForFunction(() => document.querySelector('[data-chat]').classList.contains('is-history'));
      await page.keyboard.press('Escape');
      await page.waitForFunction(() => !document.querySelector('[data-chat]').classList.contains('is-history'));
      assert.deepEqual(await toggles(page), ['false', 'false', 'false'], 'escape closes the drawer');
      console.log('PASS mobile drawer toggles, scrim, escape, and viewport fit');
      await page.context().close();
    }

    // Tapping a row opens the chat and clears the drawer.
    {
      const page = await makePage(390);
      const created = await seed(page, 'Tap this row to open');
      await page.reload();
      await live(page);
      await page.locator(`[data-thread="${created.id}"]`).waitFor();
      await page.locator(`[data-thread="${created.id}"] button.open`).click();
      await page.waitForFunction(id => new URL(location.href).searchParams.get('t') === id, created.id);
      await page.waitForFunction(() => !document.querySelector('[data-chat]').classList.contains('is-history'));
      assert.equal(await page.locator(`[data-thread="${created.id}"].is-on`).count(), 1, 'the opened chat is the selected row');
      assert.equal(await page.locator('[data-chat-input]').isVisible(), true, 'the composer comes up with the chat');
      console.log('PASS tapping a sidebar row opens the chat and closes the drawer');
      await page.context().close();
    }

    // Back navigation from a chat reveals the list again (the entry below the
    // chat is the list on a phone).
    {
      const page = await makePage(390);
      const created = await seed(page, 'Back navigation fixture');
      await page.reload();
      await live(page);
      await page.locator(`[data-thread="${created.id}"]`).waitFor();
      await page.locator(`[data-thread="${created.id}"] button.open`).click();
      await page.waitForFunction(id => new URL(location.href).searchParams.get('t') === id, created.id);
      await page.waitForFunction(() => !document.querySelector('[data-chat]').classList.contains('is-history'));
      await page.goBack();
      await page.waitForFunction(() => document.querySelector('[data-chat]').classList.contains('is-history'));
      assert.equal(new URL(page.url()).searchParams.get('t'), null, 'back returns to the list address');
      assert.deepEqual(await toggles(page), ['true', 'true', 'true']);
      console.log('PASS back navigation from a chat reveals the chat list');
      await page.context().close();
    }

    // Row states: selection, hover actions, one menu at a time, rename
    // affordance states, and the visible delete confirmation.
    {
      const page = await makePage(1440);
      const a = await seed(page, 'Rename target thread');
      const b = await seed(page, 'Delete target thread');
      const freshB = await api(page, '/chat/threads/' + b.id);
      await page.reload();
      await live(page);
      await openSidebar(page);
      await page.locator(`[data-thread="${a.id}"] button.open`).click();
      await page.waitForFunction(id => new URL(location.href).searchParams.get('t') === id, a.id);
      await page.waitForFunction(id => document.querySelector(`[data-thread="${id}"]`)?.classList.contains('is-on'), a.id);
      assert.equal(await page.locator('[data-history-list] .chat-history-item.is-on').count(), 1, 'exactly one selected row');

      await page.mouse.move(900, 500);
      assert.equal(await page.locator(`[data-thread="${b.id}"] button.more`).evaluate(el => getComputedStyle(el).opacity), '0', 'row actions hide at rest');
      await page.locator(`[data-thread="${b.id}"]`).hover();
      await page.waitForFunction(id => getComputedStyle(document.querySelector(`[data-thread="${id}"] button.more`)).opacity === '1', b.id);

      // The menu pop anchors under its row's ⋯ button and can cover the rows
      // below it, so open the lower row first (its menu drops into free
      // space), then the upper row's menu while the first is still up.
      const boxA = await page.locator(`[data-thread="${a.id}"]`).boundingBox();
      const boxB = await page.locator(`[data-thread="${b.id}"]`).boundingBox();
      const lower = boxA.y > boxB.y ? a : b;
      const upper = boxA.y > boxB.y ? b : a;
      await rowMenu(page, lower.id);
      assert.deepEqual(await page.locator('.chat-pop button').allTextContents(), ['Rename', 'Fork', 'Archive', 'Move to project', 'Delete'], 'the chat menu labels match the row state');
      assert.equal(await page.locator('.chat-history-item.is-menu').count(), 1, 'the open menu flags its row');
      assert.equal(await page.locator(`[data-thread="${lower.id}"].is-menu`).count(), 1);
      // The flag alone must hold the row's ⋯ button once the pointer leaves
      // and focus is gone (:hover and :focus-within no longer apply).
      await page.evaluate(() => document.activeElement?.blur());
      await page.mouse.move(900, 500);
      // The hover fade that opened the menu may still be settling on 1.
      await page.waitForFunction(id => {
        const row = document.querySelector(`[data-thread="${id}"]`);
        return row?.classList.contains('is-menu') && getComputedStyle(row.querySelector('button.more')).opacity === '1';
      }, lower.id);
      assert.equal(await page.locator(`[data-thread="${lower.id}"] button.more`).evaluate(el => getComputedStyle(el).opacity), '1', "the ⋯ button stays while the row's menu is open");
      // A repaint replaces the row nodes; the flag must follow the menu.
      await page.evaluate(fresh => fuseToolFixture.sidebar.update({ ...fresh, revision: fresh.revision + 1 }), freshB);
      assert.equal(await page.locator('.chat-history-item.is-menu').count(), 1, 'the flag survives a list repaint');
      assert.equal(await page.locator(`[data-thread="${lower.id}"].is-menu`).count(), 1);
      await rowMenu(page, upper.id);
      assert.equal(await page.locator('.chat-pop').count(), 1, 'opening another row replaces the menu: only one is open');
      assert.equal(await page.locator('.chat-history-item.is-menu').count(), 1, 'the flag follows the replaced menu');
      assert.equal(await page.locator(`[data-thread="${upper.id}"].is-menu`).count(), 1);
      await page.keyboard.press('Escape');
      await page.locator('.chat-pop').waitFor({ state: 'detached' });
      assert.equal(await page.locator('.chat-pop').count(), 0, 'escape closes the row menu');
      assert.equal(await page.locator('.chat-history-item.is-menu').count(), 0, 'closing the menu clears the row flag');

      await rowMenu(page, a.id);
      await page.getByRole('button', { name: 'Rename', exact: true }).click();
      const rename = page.locator(`[data-thread="${a.id}"] input.rename`);
      await rename.waitFor();
      assert.equal(await rename.inputValue(), 'Rename target thread', 'the rename box opens on the current title');
      assert.equal(await rename.getAttribute('maxlength'), '80');
      assert.equal(await rename.evaluate(el => document.activeElement === el && el.selectionStart === 0 && el.selectionEnd === el.value.length), true, 'focused with the title selected');
      // A live update must not clobber the box mid-edit.
      const sync = await api(page, '/chat/sync');
      await page.evaluate(({ fresh, eventId }) => fuseToolFixture.event({
        id: eventId, type: 'thread.updated', thread_id: fresh.id,
        payload: { ...fresh, revision: fresh.revision + 5, title: 'Delete target thread, retitled', updated_at: new Date().toISOString() },
      }), { fresh: freshB, eventId: sync.cursor + 1 });
      assert.equal(await rename.count(), 1, 'an incoming update does not destroy the rename box');
      await rename.press('Escape');
      assert.equal(await page.locator(`[data-thread="${a.id}"] input.rename`).count(), 0, 'escape leaves the rename box');
      assert.equal(await page.locator(`[data-thread="${a.id}"] .chat-history-name`).innerText(), 'Rename target thread', 'escape keeps the old title');
      assert.equal(await page.locator(`[data-thread="${b.id}"] .chat-history-name`).innerText(), 'Delete target thread, retitled', 'the parked update lands once the rename ends');

      await rowMenu(page, a.id);
      await page.getByRole('button', { name: 'Rename', exact: true }).click();
      await rename.fill('');
      await rename.press('Enter');
      await page.waitForFunction(id => !document.querySelector(`[data-thread="${id}"] input.rename`), a.id);
      assert.equal(await page.locator(`[data-thread="${a.id}"] .chat-history-name`).innerText(), 'Rename target thread', 'an empty rename is ignored');
      assert.equal((await api(page, '/chat/threads/' + a.id)).title, 'Rename target thread', 'the server keeps the old title');

      await rowMenu(page, a.id);
      await page.getByRole('button', { name: 'Rename', exact: true }).click();
      await rename.fill('Renamed from the row menu');
      await rename.press('Enter');
      await page.waitForFunction(() => document.querySelector('[data-thread] input.rename') === null && [...document.querySelectorAll('[data-thread] .chat-history-name')].some(el => el.textContent === 'Renamed from the row menu'));
      assert.equal((await api(page, '/chat/threads/' + a.id)).title, 'Renamed from the row menu', 'a saved rename reaches the server');

      await rowMenu(page, b.id);
      await page.getByRole('button', { name: 'Delete', exact: true }).click();
      const dialog = page.locator('dialog.modal[data-confirm]');
      await dialog.waitFor();
      assert.equal(await dialog.evaluate(el => el.open), true, 'delete asks in a modal dialog');
      assert.equal(await page.locator('[data-confirm-text]').innerText(), 'Delete this chat? This cannot be undone.');
      assert.equal(await page.locator('[data-confirm-yes]').innerText(), 'Delete');
      assert.equal(await page.locator('[data-confirm-no]').innerText(), 'Cancel');
      await page.locator('[data-confirm-no]').click();
      await dialog.waitFor({ state: 'hidden' });
      assert.equal(await page.locator(`[data-thread="${b.id}"]`).count(), 1, 'cancel keeps the row');
      assert.equal((await api(page, '/chat/threads/' + b.id)).title, 'Delete target thread', 'cancel keeps the chat on the server');
      console.log('PASS selection, hover actions, one open menu, rename states, and the delete dialog');
      await page.context().close();
    }

    // New chat: empty composer, no stale selection or title; on a phone the
    // drawer closes with it.
    {
      const desktop = await makePage(1440);
      const created = await seed(desktop, 'New chat reset fixture');
      await desktop.reload();
      await live(desktop);
      await openSidebar(desktop);
      await desktop.locator(`[data-thread="${created.id}"] button.open`).click();
      await desktop.waitForFunction(id => new URL(location.href).searchParams.get('t') === id, created.id);
      await desktop.locator('[data-chat-new]').click();
      await desktop.waitForFunction(() => !new URL(location.href).searchParams.get('t'));
      assert.equal(await desktop.locator('[data-chat-input]').inputValue(), '', 'new chat starts with an empty composer');
      assert.equal(await desktop.locator('[data-history-list] .chat-history-item.is-on').count(), 0, 'no stale selection');
      assert.equal(await desktop.locator('[data-chat-title]').innerText(), '', 'no stale title');
      await desktop.context().close();

      const phone = await makePage(390);
      const onPhone = await seed(phone, 'New chat phone fixture');
      await phone.reload();
      await live(phone);
      await phone.locator(`[data-thread="${onPhone.id}"] button.open`).click();
      await phone.waitForFunction(id => new URL(location.href).searchParams.get('t') === id, onPhone.id);
      await phone.locator('.chat-bar [data-history-toggle]').click();
      await phone.waitForFunction(() => document.querySelector('[data-chat]').classList.contains('is-history'));
      await phone.locator('[data-chat-new]').click();
      await phone.waitForFunction(() => !document.querySelector('[data-chat]').classList.contains('is-history') && !new URL(location.href).searchParams.get('t'));
      assert.equal(await phone.locator('[data-chat-input]').inputValue(), '', 'the phone composer is empty after New chat');
      assert.equal(await phone.locator('[data-chat-input]').isVisible(), true);
      assert.equal(await phone.locator('[data-history-list] .chat-history-item.is-on').count(), 0, 'no stale selection on the phone');
      console.log('PASS new chat clears the composer, selection, title, and the drawer');
      await phone.context().close();
    }

    assert.deepEqual(errors, []);
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
