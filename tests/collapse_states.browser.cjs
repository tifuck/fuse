// Collapse vs expand for tool rows the other suites don't pin: live output
// landing in an already-open body, a status change while open rewriting the
// collapsed header, and the row kinds those suites never toggle.
// Run against chat_browser_fixture.py, never the live Fuse service.
const assert = require('node:assert/strict');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const origin = process.env.FUSE_BROWSER_ORIGIN || 'http://127.0.0.1:8099';
assert.match(origin, /^http:\/\/127\.0\.0\.1:\d+$/);

const PNG = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4z8AAAAMBAQDJ/pLvAAAAAElFTkSuQmCC';
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

    const row = () => page.locator('[data-step="tool-row"]');
    // A folded console call's own toggle only exists once its summary line is open.
    const openSummary = async () => {
      const summary = page.locator('.is-console-group > .chat-step-main > .chat-step-head > .chat-step-toggle');
      if (await summary.count() && await summary.getAttribute('aria-expanded') !== 'true') await summary.click();
    };
    // The expander is the "… +N lines" marker on the hang.
    const toggle = () => row().locator('.chat-console-more');
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
      {
        const tag = `${width}px`;

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
          if (status === 'running') {
            check(await toggle().count() === 0, `${tag} a running call is folded, not a disclosure`);
            check(await r.getAttribute('data-state') === 'running', `${tag} folded running call keeps its state`);
            continue;
          }
          // Inside an opened console summary a settled call is printed in full,
          // so the disclosure is the summary line, not the call.
          {
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
        }

        // Expanding streams further output into the one open body; collapsing
        // keeps it and does not grow a second header. A settled command is the
        // row with a preview: a running background command prints no output.
        // Its own id: a console summary's open state is keyed by the first
        // call, and a fresh mount must not inherit the one opened above.
        await page.evaluate((trace) => fuseToolFixture.mount([{ tools: [trace, { id: 'after', name: 'read_file', status: 'done', path: 'NOTES.md', output: '1: notes\n' }] }], false), { ...CASES.shell(), id: 'flow', status: 'done', state: 'completed', output: 'line one\nline two\nline three\nline four\nline five\nline six' });
        const flow = page.locator('[data-step="tool-flow"]');
        const flowOpen = () => flow.locator('.chat-console-hang').evaluate((el) => String(el.classList.contains('is-expanded')));
        const flowBody = () => flow.locator(':scope > .chat-step-main > .chat-step-reveal').innerText();
        {
          const summary = await page.locator('.is-console-group').innerText();
          check(summary.includes('ran') && summary.includes('shell command') && !summary.includes('line five'), `${tag} collapsed summary names the command without its output`);
        }
        await openSummary();
        await page.evaluate(() => fuseToolFixture.update('flow', { output: 'line one\nline two\nline three\nline four\nline five\nline six\nline seven' }, false));
        const openText = await flowBody();
        check(openText.includes('line seven'), `${tag} streamed output reaches the open body`);
        check(openText.split('line seven').length - 1 === 1, `${tag} streamed output is not duplicated`);
        check(await flow.locator(':scope > .chat-step-main > .chat-step-head').count() === 1, `${tag} update while open keeps one header`);
        const headerWhileOpen = await flow.locator('.chat-console-call').innerText();
        check((await flowBody()).includes('line seven'), `${tag} collapsing did not drop output`);
        const headerAfter = await flow.locator('.chat-console-call').innerText();
        check(headerAfter === headerWhileOpen, `${tag} collapse/expand did not reset the header`);

        // A terminal status arriving while expanded rewrites the header too.
        await page.evaluate(() => fuseToolFixture.update('flow', { status: 'error', state: 'failed', execution_status: 'failed', error: 'tests failed', output: 'line one\nline two\nline three\nline four\nline five\nline six\nFAIL' }, false));
        check(await flowOpen() === 'true', `${tag} stays open across the failure`);
        check(await flow.getAttribute('data-state') === 'error', `${tag} expanded row takes the error state`);
        check((await flow.innerText()).includes('Error'), `${tag} row reads the failure while open`);
        check((await flowBody()).includes('FAIL'), `${tag} open body shows the failing output`);
        check((await flow.innerText()).includes('Error'), `${tag} collapsed row kept the failure`);
        check(await flow.getAttribute('data-state') === 'error', `${tag} collapsed row kept the error state`);
      }
    }

    assert.deepEqual(failures, [], 'no page errors');
    console.log(`PASS ${checks} collapse/expand checks`);
  } finally {
    await browser.close();
  }
})().catch((error) => { console.error(error); process.exitCode = 1; });
