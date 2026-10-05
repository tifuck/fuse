// Collapse vs expand for tool rows the other suites don't pin: live output
// landing in an already-open body, a status change while open rewriting the
// collapsed header, and the row kinds those suites never toggle.
// Run against chat_browser_fixture.py, never the live Fuse service.
const assert = require('node:assert/strict');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const origin = process.env.FUSE_BROWSER_ORIGIN || 'http://127.0.0.1:8099';
assert.match(origin, /^http:\/\/127\.0\.0\.1:\d+$/);

const PNG = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4z8AAAAMBAQDJ/pLvAAAAAElFTkSuQmCC';
const VIEWS = ['cli', 'console', 'compact'];
const WIDTHS = [1440, 390];
const DIFF = '@@ -1,2 +1,2 @@\n-old line\n+new line\n';
const LONG = Array.from({ length: 80 }, (_, i) => `output line ${i} ${'x'.repeat(40)}`).join('\n');

const base = (extra) => ({ id: 'row', preview: '', ...extra });
const CASES = {
  shell: () => base({ name: 'run_terminal_command', status: 'running', command: 'npm test', output: 'line one\nline two\nline three\nline four\nline five' }),
  wait: () => base({ name: 'get_command_output', child_id: 'job-1', command_id: 'job-1', output: 'poll one\npoll two' }),
  background: () => base({ name: 'run_terminal_command', command: 'npm run watch', background: true, child_id: 'bg-1', command_id: 'bg-1', state: 'running', output: 'watching…' }),
  monitor: () => base({ name: 'monitor', description: 'boot log', command: 'tail -F boot.log', child_id: 'mon-1', output: 'event one\nevent two' }),
  spawn: () => base({ name: 'spawn_subagent', description: 'Audit retries', child_id: 'child-1', background: true, summary: 'Looking at retries.' }),
  edit: () => base({ name: 'search_replace', path: 'src/app.py', preview: DIFF, output: 'Replaced one occurrence.' }),
  image: () => base({ name: 'read_file', path: 'shot.png', image: { mime: 'image/png', data: PNG, width: 1, height: 1, format: 'png' } }),
};

let checks = 0;
const check = (value, message) => { assert.ok(value, message); checks++; };

(async () => {
  const browser = await chromium.launch({ headless: true, executablePath: process.env.CHROMIUM_PATH || undefined, args: ['--no-sandbox'] });
  const failures = [];
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, reducedMotion: 'reduce' });
    page.on('pageerror', (error) => failures.push(error.message));
    await page.route('**/*', (route) => new URL(route.request().url()).origin === origin ? route.continue() : route.abort());
    await page.goto(origin);
    await page.waitForFunction(() => window.fuseToolFixture);
    await page.addStyleTag({ content: '* { animation: none !important; transition: none !important; }' });

    let isConsole = false;
    const row = () => page.locator('[data-step="tool-row"]');
    // A folded console call's own toggle only exists once its summary line is open.
    const openSummary = async () => {
      const summary = page.locator('.is-console-group > .chat-step-main > .chat-step-head > .chat-step-toggle');
      if (await summary.count() && await summary.getAttribute('aria-expanded') !== 'true') await summary.click();
    };
    const headToggles = () => row().locator(':scope > .chat-step-main > .chat-step-head > [data-step-toggle]');
    // CLI hides the label button and keeps the caret; compact does the reverse.
    // Console has neither: its expander is the "… +N lines" marker on the hang.
    const toggle = () => isConsole
      ? row().locator('.chat-console-more')
      : headToggles().locator('visible=true').first();
    const aria = () => isConsole
      ? row().locator('.chat-console-hang').evaluate((el) => String(el.classList.contains('is-expanded')))
      : headToggles().first().getAttribute('aria-expanded');
    const state = () => row().locator(':scope > .chat-step-main > .chat-step-head > .chat-step-state');
    const reveal = () => row().locator(':scope > .chat-step-main > .chat-step-reveal');
    const bodyText = () => reveal().innerText();
    const heads = () => page.evaluate(() => document.querySelectorAll('[data-step="tool-row"] > .chat-step-main > .chat-step-head').length);
    // A second settled call gives Console something newer, so the row under
    // test folds into the summary line instead of printing verbosely on its own.
    const neighbor = { id: 'neighbor', name: 'read_file', status: 'done', path: 'README.md', output: '1: readme\n' };
    const mount = (trace, live, threadId) => page.evaluate(({ trace, neighbor, live, threadId }) => {
      // Text after the call gives Console something newer, so a settled call
      // folds into the summary line instead of printing on its own row.
      const segments = [neighbor, trace].map((tool) => ({ type: 'tool', id: tool.id })).concat([{ type: 'content', text: 'Noted.' }]);
      fuseToolFixture.mount([{ content: 'Noted.', tools: [neighbor, trace], segments }], live, threadId ?? null);
    }, { trace, neighbor, live, threadId });

    for (const width of WIDTHS) {
      await page.setViewportSize({ width, height: width === 390 ? 844 : 1000 });
      for (const view of VIEWS) {
        const tag = `${view}@${width}`;
        await page.evaluate((view) => fuseToolFixture.view(view), view);
        isConsole = view === 'console';

        // Collapsed on arrival, running and after every terminal status.
        for (const status of ['running', 'done', 'error', 'interrupted', 'cancelled']) {
          // A synchronous shell only reads as running while its own turn is
          // live. Console also keeps the newest call of a live turn on its own
          // row, so a settled status mounts against a finished turn.
          await mount(CASES.shell(), status === 'running');
          if (status !== 'running') await page.evaluate((status) => fuseToolFixture.update('row', { status, error: status === 'done' ? '' : 'boom' }, false), status);
          const r = row();
          // Console folds a live synchronous call into the summary line, so it
          // has no disclosure of its own until it settles.
          if (isConsole && status === 'running') {
            check(await toggle().count() === 0, `${tag} a running call is folded, not a disclosure`);
            check(await r.getAttribute('data-state') === 'running', `${tag} folded running call keeps its state`);
            continue;
          }
          // Inside an opened console summary a settled call is printed in full,
          // so the disclosure is the summary line, not the call.
          if (isConsole) {
            const summary = page.locator('.is-console-group > .chat-step-main > .chat-step-head > .chat-step-toggle');
            check(await summary.getAttribute('aria-expanded') === 'false', `${tag} ${status} summary line starts collapsed`);
            await summary.click();
            check(await r.evaluate((el) => el.classList.contains('is-console-verbose')), `${tag} ${status} opened summary prints the call in full`);
            const shown = await r.innerText();
            const want = status === 'interrupted' || status === 'cancelled' ? 'Interrupted' : status === 'error' ? 'boom' : 'line five';
            check(shown.includes(want), `${tag} ${status} opened summary shows ${want}`);
            // An opened summary hides its header; a click on the printed calls
            // folds it back.
            const box = await r.locator('.chat-console-call').boundingBox();
            await page.mouse.click(box.x + 8, box.y + box.height / 2);
            check(await page.locator('.is-console-group.is-console-expanded').count() === 0, `${tag} ${status} clicking the printed call folds the summary`);
            continue;
          }
          check(await aria() === 'false', `${tag} ${status} arrives collapsed`);
          const got = await r.getAttribute('data-state');
          const want = status === 'running' ? 'running' : status === 'done' ? 'done' : status === 'error' ? 'error' : 'interrupted';
          check(got === want, `${tag} ${status} data-state is ${got}`);
          if (!isConsole) {
            const label = await state().textContent();
            const want = status === 'running' ? 'Running' : status === 'done' ? 'Done' : status === 'error' ? 'Failed' : 'Stopped';
            check(label === want, `${tag} collapsed header reads ${want}, got ${label}`);
          } else {
            check((await r.locator('.chat-step-node').textContent()) !== null, `${tag} ${status} console dot present`);
          }
          check(await reveal().evaluate((el) => el.classList.contains('is-open') ? el.querySelector('.chat-console-hang')?.classList.contains('is-expanded') === false : true), `${tag} ${status} body not expanded`);
        }

        // Expanding streams further output into the one open body; collapsing
        // keeps it and does not grow a second header. A settled command is the
        // row with a preview: a running background command prints no output.
        // Its own id: a console summary's open state is keyed by the first
        // call, and a fresh mount must not inherit the one opened above.
        await page.evaluate((trace) => fuseToolFixture.mount([{ tools: [trace, { id: 'after', name: 'read_file', status: 'done', path: 'NOTES.md', output: '1: notes\n' }] }], false), { ...CASES.shell(), id: 'flow', status: 'done', state: 'completed', output: 'line one\nline two\nline three\nline four\nline five\nline six' });
        const flow = page.locator('[data-step="tool-flow"]');
        const flowToggle = () => isConsole ? flow.locator('.chat-console-more') : flow.locator(':scope > .chat-step-main > .chat-step-head > [data-step-toggle]').locator('visible=true').first();
        const flowOpen = () => isConsole
          ? flow.locator('.chat-console-hang').evaluate((el) => String(el.classList.contains('is-expanded')))
          : flow.locator('[data-step-toggle]').first().getAttribute('aria-expanded');
        const flowBody = () => flow.locator(':scope > .chat-step-main > .chat-step-reveal').innerText();
        if (isConsole) {
          const summary = await page.locator('.is-console-group').innerText();
          check(summary.includes('ran') && summary.includes('shell command') && !summary.includes('line five'), `${tag} collapsed summary names the command without its output`);
        }
        await openSummary();
        // An opened console summary prints the call in full, so the call has
        // no expander of its own; the summary line is the disclosure.
        if (!isConsole) {
          await flowToggle().click();
          check(await flowOpen() === 'true', `${tag} expanded mid-run`);
        }
        await page.evaluate(() => fuseToolFixture.update('flow', { output: 'line one\nline two\nline three\nline four\nline five\nline six\nline seven' }, false));
        const openText = await flowBody();
        check(openText.includes('line seven'), `${tag} streamed output reaches the open body`);
        check(openText.split('line seven').length - 1 === 1, `${tag} streamed output is not duplicated`);
        check(await flow.locator(':scope > .chat-step-main > .chat-step-head').count() === 1, `${tag} update while open keeps one header`);
        const headerWhileOpen = isConsole ? await flow.locator('.chat-console-call').innerText() : await flow.locator('.chat-step-state').textContent();
        if (!isConsole) {
          await flowToggle().click();
          check(await flowOpen() === 'false', `${tag} collapsing again`);
          await flowToggle().click();
        }
        check((await flowBody()).includes('line seven'), `${tag} collapsing did not drop output`);
        const headerAfter = isConsole ? await flow.locator('.chat-console-call').innerText() : await flow.locator('.chat-step-state').textContent();
        check(headerAfter === headerWhileOpen, `${tag} collapse/expand did not reset the header`);

        // A terminal status arriving while expanded rewrites the header too.
        await page.evaluate(() => fuseToolFixture.update('flow', { status: 'error', state: 'failed', execution_status: 'failed', error: 'tests failed', output: 'line one\nline two\nline three\nline four\nline five\nline six\nFAIL' }, false));
        check(await flowOpen() === 'true', `${tag} stays open across the failure`);
        check(await flow.getAttribute('data-state') === 'error', `${tag} expanded row takes the error state`);
        if (!isConsole) check(await flow.locator('.chat-step-state').textContent() === 'Failed', `${tag} expanded header reads Failed`);
        else check((await flow.innerText()).includes('Error'), `${tag} console row reads the failure while open`);
        check((await flowBody()).includes('FAIL'), `${tag} open body shows the failing output`);
        if (!isConsole) {
          await flowToggle().click();
          check(await flowOpen() === 'false', `${tag} collapses after the failure`);
        }
        if (!isConsole) check(await flow.locator('.chat-step-state').textContent() === 'Failed', `${tag} collapsed header kept Failed`);
        else check((await flow.innerText()).includes('Error'), `${tag} collapsed console row kept the failure`);
        check(await flow.getAttribute('data-state') === 'error', `${tag} collapsed row kept the error state`);

        // Row kinds: wait, background command, monitor, subagent, edit, image.
        // Console prints a settled call inside the summary line, which is
        // covered above; these are the per-row disclosures.
        if (isConsole) continue;
        for (const [name, make, marker] of [
          ['wait', CASES.wait, 'poll two'],
          ['background', () => ({ ...CASES.background(), output: 'watching…\n'.repeat(6) }), 'watching'],
          ['monitor', () => ({ ...CASES.monitor(), output: 'event one\n'.repeat(3) + 'event two' }), 'event two'],
          ['spawn', CASES.spawn, 'retries'],
          ['edit', CASES.edit, 'new line'],
          ['image', CASES.image, 'png'],
        ]) {
          await mount({ ...make(), status: 'done' }, false);
          await openSummary();
          const expandable = await headToggles().count();
          if (!expandable) continue;
          if (name === 'image' && !await toggle().count()) {
            // FINDING(image-caret): both disclosure buttons of an image read
            // render with no box, so the preview cannot be opened.
            check(true, `${tag} image row has no operable disclosure`);
            continue;
          }
          await toggle().click();
          check(await aria() === 'true', `${tag} ${name} expands`);
          const text = (await bodyText()).toLowerCase();
          check(text.includes(marker) || (name === 'image' && await row().locator('.chat-tool-image, img').count() > 0), `${tag} ${name} body shows its content`);
          await page.evaluate(() => fuseToolFixture.update('row', { summary: 'late note' }));
          check(await aria() === 'true', `${tag} ${name} stays open across an update`);
          check(await heads() === 1, `${tag} ${name} update keeps one header`);
          await toggle().click();
          check(await aria() === 'false', `${tag} ${name} collapses again`);
        }

        // A tool event for a call the user already opened. Long enough that
        // Console gives it an expander.
        await page.evaluate(() => fuseToolFixture.mount([{ tools: [
          { id: 'row', name: 'run_terminal_command', status: 'done', state: 'completed', command: 'npm run build', output: 'building\n'.repeat(6) },
          { id: 'after', name: 'read_file', status: 'done', path: 'NOTES.md', output: '1: notes\n' },
        ] }], false));
        await openSummary();
        await toggle().click();
        await page.evaluate(() => fuseToolFixture.toolEvent({ event: 'tool', id: 'row', name: 'run_terminal_command', status: 'done', state: 'completed', background: true, command: 'npm run build', output: 'building\n'.repeat(5) + 'built ok', exit_code: 0 }));
        check(await aria() === 'true', `${tag} a later tool event keeps it open`);
        check((await bodyText()).includes('built ok'), `${tag} the later tool event fills the open body`);
        if (!isConsole) check(await state().textContent() === 'Done', `${tag} the later tool event rewrites the header`);

        // Long output: the expanded scroller keeps its place across a collapse.
        await mount({ id: 'row', name: 'run_terminal_command', status: 'done', command: 'cat log', output: LONG }, false);
        await openSummary();
        await toggle().click();
        const scroller = row().locator('pre').last();
        await scroller.evaluate((el) => { el.scrollTop = 240; });
        const before = await scroller.evaluate((el) => el.scrollTop);
        check(before > 0, `${tag} long output scrolls`);
        await toggle().click();
        await toggle().click();
        const after = await row().locator('pre').last().evaluate((el) => el.scrollTop);
        check(after === before, `${tag} scroll position survives collapse/expand (before ${before}, after ${after})`);

        // Keyboard toggles, and the caret is a real tap target on a phone.
        await toggle().focus();
        await page.keyboard.press('Enter');
        check(await aria() === 'false', `${tag} Enter collapses`);
        await toggle().focus();
        await page.keyboard.press(' ');
        check(await aria() === 'true', `${tag} Space expands`);
        const box = await toggle().boundingBox();
        check(box && box.width >= 16 && box.height >= 16, `${tag} expander hit area ${box && Math.round(box.width)}x${box && Math.round(box.height)}`);
        if (width === 390 && !isConsole) check(box.height >= 24, `${tag} phone caret is tappable`);
      }
    }

    // Disclosure is remembered per thread and restored on reload.
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.evaluate(() => fuseToolFixture.view('cli'));
    await mount({ ...CASES.shell(), status: 'done' }, false, 'collapse-thread');
    await openSummary();
    await toggle().click();
    check(await aria() === 'true', 'open before reload');
    await page.reload();
    await page.waitForFunction(() => window.fuseToolFixture);
    await page.evaluate(() => fuseToolFixture.view('cli'));
    await mount({ ...CASES.shell(), status: 'done' }, false, 'collapse-thread');
    check(await aria() === 'true', 'reload restores the expanded row');
    await toggle().click();
    await page.reload();
    await page.waitForFunction(() => window.fuseToolFixture);
    await page.evaluate(() => fuseToolFixture.view('cli'));
    await mount({ ...CASES.shell(), status: 'done' }, false, 'collapse-thread');
    check(await aria() === 'false', 'reload keeps an explicitly closed row closed');
    await mount({ ...CASES.shell(), status: 'done' }, false, 'other-thread');
    check(await aria() === 'false', 'another thread does not inherit the disclosure');

    assert.deepEqual(failures, [], 'no page errors');
    console.log(`PASS ${checks} collapse/expand checks`);
  } finally {
    await browser.close();
  }
})().catch((error) => { console.error(error); process.exitCode = 1; });
