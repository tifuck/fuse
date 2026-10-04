// Todo section suite: todo_write never prints as a transcript row. The list
// lives in its own section under the status line and above the composer, in
// every reading mode, and it never touches the spinner's words.
//
//  1. Placement and the spinner: status line, then the section, then the
//     composer; the spinner keeps its own verb and state.
//  2. In place: a later todo_write updates the section, a failed one and an
//     unrelated call leave it alone, an explicit empty list clears it.
//  3. When it shows: an unfinished list stays (and can be dismissed); a
//     finished one shows while the main run or any subagent is running, then
//     goes after a short beat; an old finished list never returns under a later
//     turn.
//  4. The settled line: one line in the transcript, where a list finished.
//  5. Isolation: a subagent's list and the main chat's list never mix, whichever
//     one changes, and tool ids that repeat across runs do not link them.
const assert = require('node:assert/strict');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const origin = process.env.FUSE_BROWSER_ORIGIN || 'http://127.0.0.1:8099';
assert.match(origin, /^http:\/\/127\.0\.0\.1:\d+$/);

const todo = (id, items, extra = {}) => ({ id, name: 'todo_write', status: 'done', error: '', results: [], todos: items.map(([content, status], i) => ({ id: String(i + 1), content, status })), ...extra });
const ROOT = { host: 'qa-box', cwd: '/srv/qa', platform: 'posix', project_root: '/srv/qa' };
const running = (id) => ({
  id, name: 'run_terminal_command', status: 'running', command: 'npm test', description: 'Run the tests', child_id: 'job-1', command_id: 'job-1', job_id: 'job-1',
  state: 'running', execution_status: 'running', elapsed_ms: 4000, results: [], error: '', output: '', ...ROOT,
});
const turn = (tools, segments, extra = {}) => ({
  id: 'turn', role: 'assistant', content: '', reasoning: '', model: 'qa/model', elapsedMs: 30000, tools,
  segments: segments || tools.map((t) => ({ type: 'tool', id: t.id })), ...extra,
});
const OPEN = [['Read the layout code', 'completed'], ['Spawn reviewers for each pane', 'in_progress'], ['Collect and merge findings', 'pending']];
const DONE = OPEN.map(([text]) => [text, 'completed']);

(async () => {
  const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'], ...(process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {}) });
  const failures = [];
  const check = (cond, message) => { if (!cond) failures.push(message); };
  try {
    const page = await browser.newPage({ reducedMotion: 'reduce', viewport: { width: 1280, height: 900 } });
    const errors = [];
    page.on('pageerror', (error) => errors.push(error.message));
    await page.route('**/*', (route) => new URL(route.request().url()).origin === origin ? route.continue() : route.abort());
    await page.goto(origin);
    await page.waitForFunction(() => window.fuseToolFixture);

    const frame = () => page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    const mount = async (rows, opts = {}) => {
      await page.evaluate(([rows, opts]) => { fuseToolFixture.todo.reset(); fuseToolFixture.mountThread(rows, opts); }, [rows, opts]);
      await frame();
    };
    const section = () => page.evaluate(() => {
      const el = document.querySelector('[data-live-todos]');
      return { shown: !el.hidden, rows: [...el.querySelectorAll('.chat-todo-row')].map((row) => [row.className.replace('chat-todo-row ', ''), row.textContent.trim()]), text: el.textContent.replace(/\s+/g, ' ').trim(), dismiss: Boolean(el.querySelector('[data-todo-dismiss]')) };
    });
    const logText = () => page.evaluate(() => document.querySelector('.chat-log').textContent);

    for (const view of ['console', 'mainframe', 'cli', 'compact']) {
      await page.evaluate((view) => fuseToolFixture.view(view), view);
      const tag = (what) => `${what} [${view}]`;
      const user = { role: 'user', content: 'Go.' };

      // 1. Placement and the spinner.
      await mount([user, turn([todo('t1', OPEN), running('r1')])], { live: true, phase: 'tools' });
      const placed = await page.evaluate(() => {
        const box = (sel) => { const el = document.querySelector(sel); return el && !el.hidden ? el.getBoundingClientRect() : null; };
        const dock = box('[data-live-dock]');
        const todos = box('[data-live-todos]');
        const composer = box('[data-chat-form]');
        return {
          dock: dock && [dock.top, dock.bottom], todos: todos && [todos.top, todos.bottom], composer: composer && composer.top,
          verb: document.querySelector('[data-live-dock] .chat-status-verb').textContent,
          status: document.querySelector('[data-live-dock-label]').textContent,
          row: document.querySelector('[data-tool="todo_write"]') ? 'row' : '', log: document.querySelector('.chat-log').textContent,
        };
      });
      check(placed.dock && placed.todos, tag('status line and todo section are both shown'));
      if (placed.dock && placed.todos) {
        check(placed.todos[0] >= placed.dock[1] - 1, tag(`todo section sits under the status line (${placed.dock} / ${placed.todos})`));
        check(placed.todos[1] <= placed.composer + 1, tag(`todo section sits above the composer (${placed.todos[1]} vs ${placed.composer})`));
      }
      check(!/Spawn reviewers/i.test(placed.status), tag(`the spinner never shows the todo: ${placed.status}`));
      check(!/updating todos/i.test(placed.status), tag('the spinner never says "updating todos"'));
      check(placed.row === '' && !/Spawn reviewers/.test(placed.log), tag('todo_write prints no transcript row'));
      const first = await section();
      check(first.rows.length === 3 && first.rows[1][0] === 'is-in-progress' && first.rows[0][0] === 'is-completed' && first.rows[2][0] === 'is-pending', tag(`rows carry their states ${JSON.stringify(first.rows)}`));
      check(/Todos\s*1\/3/.test(first.text), tag(`head counts finished items: ${first.text}`));

      // 2. In place.
      await page.evaluate(([next]) => fuseToolFixture.append(next, true), [todo('t2', [['Read the layout code', 'completed'], ['Spawn reviewers for each pane', 'completed'], ['Collect and merge findings', 'in_progress']])]);
      await frame();
      const updated = await section();
      check(updated.rows.length === 3 && updated.rows[2][0] === 'is-in-progress' && /2\/3/.test(updated.text), tag(`a later todo_write updates the section: ${updated.text}`));
      check((await page.locator('[data-live-todos]').count()) === 1, tag('there is one todo section'));
      await page.evaluate(([bad]) => fuseToolFixture.append(bad, true), [{ id: 't3', name: 'todo_write', status: 'error', error: 'todos[0].content is required', results: [] }]);
      await frame();
      check((await section()).text === updated.text, tag('a failed todo_write leaves the list alone'));
      await page.evaluate(([next]) => fuseToolFixture.append(next, true), [todo('t4', [])]);
      await frame();
      check(!(await section()).shown, tag('an explicit empty list clears the section'));

      // 3. When it shows.
      await mount([user, turn([todo('t1', OPEN)])], { live: false });
      const idle = await section();
      check(idle.shown && idle.dismiss, tag('an unfinished list stays after the run, with a dismiss button'));
      await page.locator('[data-live-todos] [data-todo-dismiss]').click();
      await frame();
      check(!(await section()).shown, tag('dismissing hides an unfinished list'));

      await mount([user, turn([todo('t1', DONE)])], { live: false });
      check(!(await section()).shown, tag('a finished list is not shown when nothing is running'));
      await mount([user, turn([todo('t1', DONE), running('r1')])], { live: true, phase: 'tools' });
      const during = await section();
      check(during.shown && !during.dismiss, tag('a finished list shows while the run is live, with no dismiss button'));
      await page.evaluate(() => { fuseToolFixture.context.live(false); fuseToolFixture.repaint(); });
      await frame();
      check((await section()).shown, tag('the finished list holds for a beat when the run ends'));
      await page.waitForTimeout(1900);
      check(!(await section()).shown, tag('then it goes'));

      // A running subagent keeps a finished list up.
      await mount([user, turn([todo('t1', DONE)])], { live: false });
      await page.evaluate(() => fuseToolFixture.todo.agents([{ id: 'kid', status: 'running' }]));
      await frame();
      check((await section()).shown, tag('a running subagent keeps a finished list shown'));
      await page.evaluate(() => fuseToolFixture.todo.agents([{ id: 'kid', status: 'completed' }]));
      await page.waitForTimeout(1900);
      check(!(await section()).shown, tag('and it goes once the subagent is done'));

      // An old finished list never comes back under a later turn.
      await mount([user, turn([todo('t1', DONE)], null, { id: 'old' }), { role: 'user', content: 'Again.' }, turn([running('r2')], null, { id: 'new' })], { live: true, phase: 'tools' });
      check(!(await section()).shown, tag('an old finished list does not show under a later turn'));

      // 4. The settled line.
      const settle = [user, turn(
        [todo('a', OPEN), todo('b', DONE), todo('c', DONE)],
        [{ type: 'tool', id: 'a' }, { type: 'content', text: 'Working. ' }, { type: 'tool', id: 'b' }, { type: 'content', text: 'Done. ' }, { type: 'tool', id: 'c' }],
        { content: 'Working. Done. ' },
      )];
      await mount(settle, { live: false });
      const lines = await page.evaluate(() => [...document.querySelectorAll('[data-todo-line]')].map((el) => el.textContent.trim()));
      check(lines.length === 1 && lines[0] === 'Todos · 3/3 done', tag(`one settled line where the list finished: ${JSON.stringify(lines)}`));
      // The mark is drawn in Console (a typed glyph like ☒ prints as a broken box in many fonts) and a check elsewhere.
      await mount(settle, { live: false });
      const mark = await page.evaluate(() => { const style = getComputedStyle(document.querySelector('.chat-todo-line-mark'), '::before'); return { content: style.content, radius: style.borderTopLeftRadius, width: parseFloat(style.width) }; });
      if (view === 'console') check(mark.content === '""' && mark.radius === '50%' && mark.width > 2 && mark.width < 10, tag(`the settled line's mark is a dot in Console: ${JSON.stringify(mark)}`));
      else check(mark.content === '"✓"', tag(`the settled line's mark is a check: ${mark.content}`));
      if (view === 'console') {
        await mount([user, turn([todo('m', [['Done', 'completed'], ['Doing', 'in_progress'], ['Next', 'pending'], ['Dropped', 'cancelled']])])], { live: false });
        const glyphs = await page.evaluate(() => [...document.querySelectorAll('[data-live-todos] .chat-todo-box')].map((el) => getComputedStyle(el, '::before').content));
        check(glyphs.length === 4 && glyphs.every((content) => content === '""'), tag(`Console todo marks are drawn dots, not typed glyphs: ${JSON.stringify(glyphs)}`));
      }
      await mount([user, turn([todo('a', OPEN)])], { live: false });
      check((await page.locator('[data-todo-line]').count()) === 0, tag('an unfinished list leaves no line'));
      await mount([user, turn([todo('a', [['One', 'completed'], ['Two', 'cancelled']])])], { live: false });
      const cancelled = await page.evaluate(() => document.querySelector('[data-todo-line]')?.textContent.trim());
      check(cancelled === 'Todos · 1/2 done · 1 cancelled', tag(`a cancelled item is counted: ${cancelled}`));

      // Long lists fold, and the head opens them.
      const long = Array.from({ length: 12 }, (_, i) => [`Item ${i + 1}`, i < 4 ? 'completed' : i === 4 ? 'in_progress' : 'pending']);
      await mount([user, turn([todo('long', long)])], { live: false });
      const folded = await section();
      check(folded.rows.length <= 9 && folded.rows.some(([cls]) => cls === 'is-in-progress') && /4 done/.test(folded.text), tag(`a long list folds finished items and keeps the active one: ${folded.rows.length} rows`));
      await page.locator('[data-live-todos] [data-todo-toggle]').click();
      await frame();
      check((await section()).rows.length === 12, tag('the head opens the full list'));
    }

    // 5. Isolation: main chat and a subagent, in the same page.
    await page.evaluate(() => fuseToolFixture.view('console'));
    await page.route('**/chat/runs/kid-run', (route) => route.fulfill({
      status: 200, contentType: 'application/json',
      body: JSON.stringify({
        id: 'kid-run', event_cursor: 0, message_id: 'kid-message', description: 'Inspect the panes', status: 'running',
        // The child's first call repeats the main chat's tool id on purpose.
        message: { id: 'kid-message', role: 'assistant', content: '', tools: [todo('t1', [['Child: read the CSS', 'completed'], ['Child: report', 'in_progress']])], segments: [{ type: 'tool', id: 't1' }] },
        messages: [],
      }),
    }));
    await mount([{ role: 'user', content: 'Go.' }, turn([todo('t1', OPEN), { id: 'spawn', name: 'spawn_subagent', status: 'done', child_id: 'kid-run', description: 'Inspect the panes', results: [], error: '' }])], { live: false });
    await page.evaluate(() => fuseToolFixture.todo.openAgent('kid-run'));
    await page.waitForFunction(() => !document.querySelector('[data-agent-live-todos]').hidden);
    await frame();
    const both = await page.evaluate(() => ({
      main: document.querySelector('[data-live-todos]').textContent.replace(/\s+/g, ' ').trim(),
      child: document.querySelector('[data-agent-live-todos]').textContent.replace(/\s+/g, ' ').trim(),
    }));
    check(/Spawn reviewers/.test(both.main) && !/Child:/.test(both.main), `main section shows only the main list: ${both.main}`);
    check(/Child: report/.test(both.child) && !/Spawn reviewers/.test(both.child), `subagent section shows only its own list: ${both.child}`);
    // The main list changes; the child's does not follow, and the reverse.
    await page.evaluate(([next]) => { fuseToolFixture.append(next, true); fuseToolFixture.todo.sync(); }, [todo('t9', [['Main only', 'in_progress']])]);
    await frame();
    const after = await page.evaluate(() => ({
      main: document.querySelector('[data-live-todos]').textContent.replace(/\s+/g, ' ').trim(),
      child: document.querySelector('[data-agent-live-todos]').textContent.replace(/\s+/g, ' ').trim(),
    }));
    check(/Main only/.test(after.main) && !/Child:/.test(after.main), `main updates alone: ${after.main}`);
    check(after.child === both.child, `the subagent's list did not move with the main chat's: ${after.child}`);
    check(!/Child:/.test(await logText()) , 'the subagent\'s list never reaches the main transcript');
    await page.locator('[data-agents-close]').click();
    await frame();
    check(await page.evaluate(() => !document.querySelector('[data-agent-live-todos]').checkVisibility()), 'closing the subagent pane takes its section with it');

    assert.deepEqual(errors, [], `page errors: ${errors.join('; ')}`);
  } finally {
    await browser.close();
  }
  assert.equal(failures.length, 0, `${failures.length} todo section problems\n${failures.join('\n')}`);
  console.log('PASS: todo section — placement, spinner, in-place updates, visibility, settled line, isolation in 4 views');
})().catch((error) => { console.error(error); process.exit(1); });
