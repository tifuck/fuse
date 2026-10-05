// User-visible Tasks (schedules) page states no other suite pins.
//
// Server contracts stay in tests/test_schedules.py.
// This drives the real /tasks and /schedules routes on the isolated fixture.
const assert = require('node:assert/strict');
const { execFileSync } = require('node:child_process');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');

const origin = process.env.FUSE_BROWSER_ORIGIN || 'http://127.0.0.1:8099';
assert.match(origin, /^http:\/\/127\.0\.0\.1:\d+$/);

const tasksHtml = execFileSync(process.env.PYTHON || '/root/fuse/venv/bin/python', ['-c', `
import json
from types import SimpleNamespace
from app.web.deps import templates
req = SimpleNamespace(url=SimpleNamespace(path='/tasks'), headers={}, session={'csrf': 'browser-test-csrf'})
html = templates.get_template('tasks.html').render({
    'request': req, 'user': {'email': 'browser@test.invalid', 'is_admin': False}, 'csrf_token': 'browser-test-csrf',
    'schedule_bundle': {'schedules': [], 'projects': [], 'models': [{'id': 'offline', 'name': 'Offline'}], 'failures': 0},
})
print(json.dumps(html))
`], { cwd: require('node:path').resolve(__dirname, '..'), encoding: 'utf8' });
const TASKS_PAGE = JSON.parse(tasksHtml);

const LONG_NAME = 'Overnight repository digest for the release branch and its open reviews';
const LONG_PROMPT = 'Summarize overnight changes.\n\n' + 'Keep the full diff context. '.repeat(40);

(async () => {
  const browser = await chromium.launch({
    headless: true,
    executablePath: process.env.CHROMIUM_PATH || undefined,
    args: ['--no-sandbox'],
  });
  const errors = [];
  try {
    const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, reducedMotion: 'reduce' });
    await context.route('**/*', (route) => {
      const url = new URL(route.request().url());
      if (url.origin !== origin) return route.abort();
      // The fixture has no provider, so the real page renders an empty model
      // list and the form can never save. Serve the same template with one.
      if (url.pathname === '/tasks') return route.fulfill({ contentType: 'text/html', body: TASKS_PAGE });
      return route.continue();
    });
    const page = await context.newPage();
    page.on('pageerror', (error) => errors.push(String(error)));
    page.on('dialog', (dialog) => dialog.accept());

    const csrf = () => page.locator('meta[name="csrf-token"]').getAttribute('content');
    const api = async (path, opts = {}) => {
      const token = await csrf();
      return page.evaluate(async ({ path, opts, token }) => {
        const headers = { 'X-CSRF-Token': token, ...(opts.headers || {}) };
        let body = opts.body;
        if (body && typeof body !== 'string') {
          headers['Content-Type'] = 'application/json';
          body = JSON.stringify(body);
        }
        const res = await fetch(path, { credentials: 'same-origin', method: opts.method || 'GET', headers, body });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(`${res.status} ${data?.error?.message || data?.detail || res.statusText}`);
        return data;
      }, { path, opts, token });
    };
    const overflow = () => page.evaluate(() => document.scrollingElement.scrollWidth - window.innerWidth);
    const row = (name) => page.locator('[data-sched-body] tr', { has: page.locator('.sched-name', { hasText: name }) });

    await page.goto(origin + '/');
    await page.waitForURL(/\/chat/);

    // Empty tasks: the table is hidden and the empty copy is the only list state.
    await page.goto(origin + '/tasks');
    await page.locator('[data-sched]').waitFor();
    assert.equal(await page.locator('[data-sched-table]').isHidden(), true, 'empty: table hidden');
    assert.equal(await page.locator('[data-sched-empty]').isVisible(), true, 'empty: empty copy shown');
    assert.equal(await page.locator('[data-sched-body] tr').count(), 0, 'empty: no rows');
    assert.equal(await page.locator('[data-sched-detail]').isHidden(), true, 'empty: history closed');
    assert.ok((await overflow()) <= 1, `empty desktop overflow ${await overflow()}px`);

    // Daily, weekly and every each swap the fields the builder claims to show.
    await page.locator('[data-sched-new]').first().click();
    await page.locator('[data-sched-modal]').waitFor({ state: 'visible' });
    assert.equal(await page.locator('#sched-title').innerText(), 'New task');
    assert.equal(await page.locator('[data-sched-delete]').isHidden(), true, 'new task hides delete');
    const fields = async () => page.evaluate(() => ({
      time: document.querySelector('[data-f-time-wrap]').hidden,
      every: document.querySelector('[data-f-every-wrap]').hidden,
      once: document.querySelector('[data-f-once-wrap]').hidden,
      cron: document.querySelector('[data-f-cron-wrap]').hidden,
      days: document.querySelector('[data-f-dows]').hidden,
      project: document.querySelector('[data-f-project-wrap]').hidden,
    }));
    assert.deepEqual(await fields(), { time: false, every: true, once: true, cron: true, days: true, project: true }, 'daily shows only the time');
    await page.locator('[data-f-builder]').selectOption('weekly');
    assert.equal((await fields()).days, false, 'weekly shows the day picker');
    await page.locator('[data-f-builder]').selectOption('every');
    let shown = await fields();
    assert.equal(shown.every, false, 'every shows the interval');
    assert.equal(shown.time, true, 'every hides the clock');
    await page.locator('[data-f-builder]').selectOption('daily');
    await page.locator('[data-sched-close]').first().click();
    await page.locator('[data-sched-modal]').waitFor({ state: 'hidden' });

    // Create: the row, its recurrence label and its next fire appear without a reload.
    await page.locator('[data-sched-new]').first().click();
    await page.locator('[data-f-name]').fill(LONG_NAME);
    await page.locator('[data-f-prompt]').fill(LONG_PROMPT);
    await page.locator('[data-f-tz]').fill('America/New_York');
    await page.locator('[data-f-time]').fill('09:30');
    await page.waitForFunction(() => /Next:/.test(document.querySelector('[data-f-preview]')?.textContent || ''));
    assert.match(await page.locator('[data-f-preview]').innerText(), /Next:/, 'preview lists the next fires');
    await page.locator('[data-sched-form] button[type="submit"]').click();
    await page.locator('[data-sched-modal]').waitFor({ state: 'hidden' });
    const created = row(LONG_NAME);
    await created.waitFor();
    assert.equal(await page.locator('[data-sched-empty]').isHidden(), true, 'a saved task replaces the empty copy');
    assert.equal(await created.locator('td').nth(1).innerText(), 'Chat');
    assert.equal(await created.locator('td').nth(2).innerText(), 'Daily 09:30 (America/New_York)');
    assert.match(await created.locator('td').nth(3).innerText(), /Never fired/);
    const nextText = (await created.locator('.sched-when').innerText()).trim();
    assert.notEqual(nextText, '—', `next fire rendered, saw ${JSON.stringify(nextText)}`);
    const truth = await api('/schedules');
    const saved = truth.schedules.find((item) => item.name === LONG_NAME);
    assert.ok(saved, 'server stored the task');
    assert.equal(saved.when, 'Daily 09:30 (America/New_York)');
    const clip = await created.locator('.sched-name').evaluate((el) => ({
      title: el.getAttribute('title'),
      ellipsis: getComputedStyle(el).textOverflow,
      clipped: el.scrollWidth > el.clientWidth + 1,
    }));
    assert.equal(clip.title, LONG_NAME, 'a long name keeps its full text on the button');
    assert.equal(clip.ellipsis, 'ellipsis');
    assert.equal(clip.clipped, true, 'a long name is truncated, not wrapped');
    console.log('PASS create shows the row, timezone label and next fire');

    // Opening a task that has never fired shows that, not a spinner.
    await created.locator('td').nth(2).click();
    await page.locator('[data-detail-fires]').getByText('No fires yet').waitFor();
    assert.equal(await page.locator('[data-detail-title]').innerText(), LONG_NAME);
    assert.equal((await page.locator('[data-detail-fires]').innerText()).includes('Loading'), false, 'history settled');
    await created.locator('td').nth(2).click();
    await page.locator('[data-sched-detail]').waitFor({ state: 'hidden' });
    console.log('PASS empty history opens and closes');

    // Edit: a new name and an hourly recurrence replace the row in place.
    await created.locator('[data-edit]').click();
    await page.locator('#sched-title', { hasText: 'Edit task' }).waitFor();
    assert.equal(await page.locator('[data-f-name]').inputValue(), LONG_NAME, 'edit reloads the name');
    assert.equal(await page.locator('[data-f-tz]').inputValue(), 'America/New_York');
    assert.equal(await page.locator('[data-f-builder]').inputValue(), 'daily', 'a daily task reopens as daily');
    assert.equal(await page.locator('[data-f-prompt]').inputValue(), LONG_PROMPT, 'the full prompt reloads');
    await page.locator('[data-f-name]').fill('Hourly digest');
    await page.locator('[data-f-builder]').selectOption('every');
    await page.locator('[data-f-every]').selectOption('3600');
    await page.locator('[data-sched-form] button[type="submit"]').click();
    await page.locator('[data-sched-modal]').waitFor({ state: 'hidden' });
    const edited = row('Hourly digest');
    await edited.waitFor();
    assert.equal(await row(LONG_NAME).count(), 0, 'the old name is gone');
    assert.equal(await edited.locator('td').nth(2).innerText(), 'Every 1 hour');
    console.log('PASS edit replaces the name and recurrence label');

    // Pause and off have no control of their own. The list still has to trade
    // the next-fire time for the pill once the server says so.
    await api('/schedules/' + saved.id, { method: 'PATCH', body: { paused: true } });
    await page.reload();
    const paused = row('Hourly digest');
    await paused.waitFor();
    assert.equal(await paused.locator('.sched-pill').innerText(), 'Paused');
    assert.equal((await paused.locator('.sched-when').innerText()).trim(), '—', 'a paused task has no next fire');
    await api('/schedules/' + saved.id, { method: 'PATCH', body: { paused: false, enabled: false } });
    await page.reload();
    const off = row('Hourly digest');
    await off.waitFor();
    assert.equal(await off.locator('.sched-pill').innerText(), 'Off');
    await api('/schedules/' + saved.id, { method: 'PATCH', body: { enabled: true } });
    await page.reload();
    await page.waitForFunction(() => !document.querySelector('[data-sched-body] .sched-pill'));
    assert.notEqual((await row('Hourly digest').locator('.sched-when').innerText()).trim(), '—', 'resuming restores a next fire');
    console.log('PASS pause and off pills follow the server');

    // A rejected save stays open and names the problem instead of closing.
    await row('Hourly digest').locator('[data-edit]').click();
    await page.locator('[data-f-tz]').fill('Not/AZone');
    await page.locator('[data-sched-form] button[type="submit"]').click();
    await page.locator('[data-sched-error]').waitFor({ state: 'visible' });
    const fault = (await page.locator('[data-sched-error]').innerText()).trim();
    assert.notEqual(fault, '', `form error was blank, modal open=${await page.locator('[data-sched-modal]').isVisible()}`);
    assert.equal(await page.locator('[data-sched-modal]').isVisible(), true, 'a rejected save keeps the form open');
    await page.locator('[data-sched-close]').first().click();
    console.log('PASS a bad timezone stays on the form');

    // Delete: confirming removes the row and restores the empty state.
    await row('Hourly digest').locator('[data-edit]').click();
    await page.locator('[data-sched-delete]').click();
    await page.locator('[data-sched-modal]').waitFor({ state: 'hidden' });
    await page.waitForFunction(() => document.querySelector('[data-sched-body]').children.length === 0);
    assert.equal(await page.locator('[data-sched-empty]').isVisible(), true, 'deleting the last task restores the empty copy');
    assert.equal((await api('/schedules')).schedules.length, 0, 'server deleted the task');
    console.log('PASS delete restores the empty list');

    await page.setViewportSize({ width: 390, height: 844 });
    await page.locator('[data-sched-new]').first().click();
    await page.locator('[data-sched-modal]').waitFor({ state: 'visible' });
    const dialog = await page.locator('[data-sched-modal]').evaluate((el) => {
      const box = el.getBoundingClientRect();
      return { left: box.left, right: box.right, width: window.innerWidth };
    });
    assert.ok(dialog.left >= -1 && dialog.right <= dialog.width + 1, `task dialog off screen: ${JSON.stringify(dialog)}`);
    assert.ok((await overflow()) <= 1, `tasks phone overflow ${await overflow()}px`);
    await page.locator('[data-sched-close]').first().click();
    console.log('PASS tasks fit a phone');

    assert.deepEqual(errors, [], `page errors: ${errors.join(' | ')}`);
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
