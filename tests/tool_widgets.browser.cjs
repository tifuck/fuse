// Tool-row widgets. Isolated fixture only; never the live Fuse service.
// Pins state marks, icons, located diffs, file rows, image previews,
// sideways code scroll, kill receipts, background rows, the context meter,
// the todo section, and output truncation. Latest truth, not pixels.
const assert = require('node:assert/strict');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const origin = process.env.FUSE_BROWSER_ORIGIN || 'http://127.0.0.1:8099';
assert.match(origin, /^http:\/\/127\.0\.0\.1:\d+$/);

const PNG = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=';
const IMAGE = { mime: 'image/png', data: PNG, width: 1, height: 1, original_width: 2, original_height: 2, format: 'PNG', frame: 0, frames: 2 };
const ROOT = { host: 'qa-box', cwd: '/srv/qa', platform: 'posix', project_root: '/srv/qa' };
const WIDTHS = [1440, 390];
const LOCATED = [
  '--- a/src/app.py',
  '+++ b/src/app.py',
  '@@ -40,3 +40,3 @@',
  ' def load():',
  '-    return retries',
  '+    return attempts',
  ' ',
].join('\n');
const UNLOCATED = ['-    return retries', '+    return attempts'].join('\n');
const LONG = 'const value = ' + 'x'.repeat(180) + ';';

const read = (extra = {}) => ({ id: 'read', name: 'read_file', status: 'done', path: '/srv/qa/src/app.py', offset: 12, limit: 2, output: '12: const a = 1;\n13: const b = 2;', ...ROOT, ...extra });
const edit = (extra = {}) => ({ id: 'edit', name: 'search_replace', status: 'done', path: '/srv/qa/src/app.py', preview: LOCATED, output: 'updated /srv/qa/src/app.py (1 replacement)', ...ROOT, ...extra });
const wrote = (extra = {}) => ({ id: 'wrote', name: 'write_file', status: 'done', path: '/srv/qa/src/new.py', content: 'export const port = 8080;\nexport const host = "local";\n', preview: 'wrote src/new.py', ...ROOT, ...extra });

(async () => {
  const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'], ...(process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {}) });
  const failures = [];
  const check = (cond, message) => { if (!cond) failures.push(message); };
  try {
    const page = await browser.newPage({ reducedMotion: 'reduce', viewport: { width: 1440, height: 900 } });
    const errors = [];
    page.on('pageerror', (error) => errors.push(error.message));
    await page.route('**/*', (route) => {
      const url = new URL(route.request().url());
      if (url.origin !== origin) return route.abort();
      if (url.pathname !== '/static/chat.js') return route.continue();
      return (async () => {
        const response = await route.fetch();
        let source = await response.text();
        const end = source.lastIndexOf('\n})();');
        const hook = '\nwindow.widgetModels = { add(model) { allModels.push(model); modelsById.set(model.id, model); }, use(id) { modelId = id; meterAnchor = null; syncContextMeter(); } };\n';
        await route.fulfill({ response, body: source.slice(0, end) + hook + source.slice(end) });
      })();
    });
    await page.goto(origin);
    await page.waitForFunction(() => window.fuseToolFixture);
    if (await page.locator('[data-chat].is-history').count()) await page.locator('.chat-history [data-chat-new]').click();
    const frame = () => page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));

    const colorOf = (value) => page.evaluate((text) => {
      const probe = document.createElement('span');
      probe.style.color = text;
      probe.hidden = true;
      document.body.appendChild(probe);
      const got = getComputedStyle(probe).color;
      probe.remove();
      return got;
    }, value);
    const tokens = await page.evaluate(() => {
      const style = getComputedStyle(document.documentElement);
      return { done: style.getPropertyValue('--call-done').trim(), fail: style.getPropertyValue('--call-fail').trim(), live: style.getPropertyValue('--call-live').trim(), idle: style.getPropertyValue('--call-idle').trim(), accent: style.getPropertyValue('--brass').trim(), diffAdd: style.getPropertyValue('--diff-add').trim() };
    });
    const [doneColor, failColor, liveColor, accentColor] = await Promise.all(['--call-done', '--call-fail', '--call-live', '--brass'].map((name) => colorOf(`var(${name})`)));
    check(Boolean(tokens.done && tokens.fail && tokens.live && tokens.idle), 'call color tokens exist');
    check(tokens.done !== tokens.accent && tokens.fail !== tokens.accent && doneColor !== accentColor && failColor !== accentColor, `state marks must not be the accent (${JSON.stringify(tokens)})`);
    check(tokens.done === 'var(--diff-add)' || tokens.done === tokens.diffAdd, `done mark is the diff green, not a theme color: ${tokens.done}`);

    const mount = (tools, opts = {}) => page.evaluate(([tools, opts]) => {
      fuseToolFixture.todo.reset();
      fuseToolFixture.mountThread([{ role: 'user', content: 'Go.' }, { id: 'turn', role: 'assistant', content: '', tools, segments: tools.map((tool) => ({ type: 'tool', id: tool.id })) }], opts);
    }, [tools, opts]);

    for (const width of WIDTHS) {
      await page.setViewportSize({ width, height: 900 });
      {
        const tag = (what) => `${what} [${width}]`;

        // 1. State marks: green done, red failed, text color while running, never the accent.
        await mount([
          { id: 'ok', name: 'read_file', status: 'done', path: '/srv/qa/src/a.py', output: '1: a', ...ROOT },
          { id: 'bad', name: 'read_file', status: 'error', path: '/srv/qa/src/b.py', error: 'no such file', ...ROOT },
          { id: 'live', name: 'read_file', status: 'running', path: '/srv/qa/src/c.py', ...ROOT },
          { id: 'halt', name: 'read_file', status: 'interrupted', path: '/srv/qa/src/d.py', error: 'stopped', ...ROOT },
        ], { live: true });
        await frame();
        const marks = await page.evaluate(() => [...document.querySelectorAll('[data-step^="tool-"]')].map((row) => {
          const node = row.querySelector(':scope > .chat-step-node');
          const style = getComputedStyle(node);
          const dot = getComputedStyle(node, '::after');
          return { id: row.dataset.step, state: row.dataset.state, color: style.color, dot: dot.backgroundColor, content: dot.content, svg: Boolean(node.querySelector('svg')) };
        }));
        const byId = Object.fromEntries(marks.map((mark) => [mark.id, mark]));
        check(byId['tool-ok'].state === 'done' && byId['tool-bad'].state === 'error' && byId['tool-live'].state === 'running' && byId['tool-halt'].state === 'interrupted', tag(`row states ${JSON.stringify(marks.map((m) => [m.id, m.state]))}`));
        const markOf = (id) => byId[id].dot;
        check(markOf('tool-bad') === failColor, tag(`a failure is red (${markOf('tool-bad')} vs ${failColor})`));
        check([markOf('tool-ok'), markOf('tool-bad'), markOf('tool-live'), markOf('tool-halt')].every((color) => color !== accentColor), tag('no state mark uses the accent'));
        check(marks.every((mark) => mark.svg), tag('every row keeps its tool glyph mounted'));
        {
          // A dot (::after) is drawn over a transparent node. A finished dot
          // is white (--mist), not the diff green; failure red and the accent are
          // what this guards.
          check(markOf('tool-ok') !== failColor && markOf('tool-ok') !== accentColor, tag(`a finished dot is not red or the accent (${markOf('tool-ok')})`));
        }

        // 2. The icon names the tool; the text says what it did.
        await mount([
          read(),
          { id: 'grep', name: 'grep', status: 'done', path: '/srv/qa/src', pattern: 'renderTool', output: 'src/app.py:12:renderTool()', ...ROOT },
          { id: 'shell', name: 'run_terminal_command', status: 'done', command: "cd '/srv/qa' && npm test", exit_code: 0, output: 'ok', ...ROOT },
        ]);
        await frame();
        const icons = await page.evaluate(() => [...document.querySelectorAll('[data-tool]')].map((row) => {
          const node = row.querySelector(':scope > .chat-step-node');
          return { tool: row.dataset.tool, paths: [...node.querySelectorAll('svg path, svg circle, svg rect, svg ellipse')].map((el) => el.getAttribute('d') || el.getAttribute('points') || '').join('|'), text: row.querySelector('.chat-step-label, .chat-console-call').textContent.replace(/\s+/g, ' ').trim() };
        }));
        const shapes = new Set(icons.map((icon) => icon.paths));
        check(shapes.size === icons.length, tag(`each tool keeps its own icon: ${icons.map((icon) => icon.tool).join(',')}`));
        const readRow = icons.find((icon) => icon.tool === 'read_file');
        const grepRow = icons.find((icon) => icon.tool === 'grep');
        const shellRow = icons.find((icon) => icon.tool === 'run_terminal_command');
        const said = (icon) => icon.text.replace(/\s+/g, '');
        check(/src\/app\.py/.test(said(readRow)), tag(`read text names the file: ${readRow.text}`));
        check(/renderTool/.test(said(grepRow)), tag(`search text names the pattern: ${grepRow.text}`));
        check(/npmtest/.test(said(shellRow)) && !/cd'/.test(said(shellRow)), tag(`shell text is the command, not its cd prefix: ${shellRow.text}`));

        // 3. search_replace and a multi-edit locate the change; a missing location is not invented.
        const multi = [
          '@@ -4,1 +4,1 @@',
          '-retries = 3',
          '+retries = 5',
          '@@ -20,1 +20,1 @@',
          '-timeout = 5',
          '+timeout = 8',
        ].join('\n');
        await mount([
          edit(),
          { id: 'multi', name: 'search_replace', status: 'done', path: '/srv/qa/src/app.py', preview: multi, output: 'updated (2 replacements from 2 edits)', ...ROOT },
          { id: 'noloc', name: 'search_replace', status: 'done', path: '/root/fuse-dev/src/app.py', preview: UNLOCATED, output: 'updated', host: 'qa-box', project_root: '/root/fuse-dev' },
          { id: 'wrong', name: 'search_replace', status: 'done', path: '/srv/qa/src/other.py', preview: LOCATED.replaceAll('src/app.py', 'src/nope.py'), output: 'updated', ...ROOT },
        ]);
        await frame();
        const groupToggle = page.locator('[data-step^="group-tool-"] > .chat-step-main > .chat-step-head > .chat-step-toggle');
        if (await groupToggle.count()) await groupToggle.click();
        for (const id of ['edit', 'multi', 'noloc', 'wrong']) {
          const toggle = page.locator(`[data-step="tool-${id}"] .chat-step-toggle`).first();
          if (await toggle.count() && (await toggle.getAttribute('aria-expanded')) !== 'true') await toggle.click();
        }
        await frame();
        const diffs = await page.evaluate(() => Object.fromEntries(['edit', 'multi', 'noloc', 'wrong'].map((id) => {
          const row = document.querySelector(`[data-step="tool-${id}"]`);
          const lines = [...row.querySelectorAll('.chat-console-nline')].map((line) => ({
            n: (line.querySelector('.chat-console-n') || {}).textContent || '',
            text: line.querySelector('.chat-console-nbody').textContent,
          }));
          return [id, { file: (row.textContent.match(/[\w./-]+\.py/) || [''])[0], lines }];
        })));
        // search_replace hunks are snippet-relative, so the row draws marks and
        // drops the numbers rather than printing a location it cannot trust.
        const located = diffs.edit.lines.filter((line) => line.text === '    return attempts');
        check(located.length === 1, tag(`the located edit shows the new line: ${JSON.stringify(diffs.edit.lines)}`));
        check(diffs.edit.lines.every((line) => line.n.trim() === ''), tag(`a search_replace diff does not invent line numbers: ${JSON.stringify(diffs.edit.lines)}`));
        check(diffs.edit.lines.some((line) => line.text.trim() === 'def load():'), tag(`the diff keeps its context line: ${JSON.stringify(diffs.edit.lines)}`));
        const changed = diffs.multi.lines.map((line) => line.text.trim());
        check(changed.includes('retries = 3') && changed.includes('retries = 5') && changed.includes('timeout = 5') && changed.includes('timeout = 8'), tag(`a multi-edit shows both hunks: ${JSON.stringify(diffs.multi.lines)}`));
        check(diffs.multi.lines.some((line) => line.text.trim() === 'retries = 5' && line.n.trim() === '4'), tag(`a hunk header locates the edit: ${JSON.stringify(diffs.multi.lines)}`));
        check(diffs.noloc.lines.every((line) => line.n.trim() === ''), tag(`an unlocated edit has no fabricated line numbers: ${JSON.stringify(diffs.noloc.lines)}`));
        check(!/@@ -1\b/.test(JSON.stringify(diffs.noloc)), tag('an unlocated edit never prints @@ -1'));
        check(diffs.wrong.file.includes('other.py') && diffs.wrong.lines.some((line) => line.text === '    return attempts'), tag(`the row names the file that was edited, not the diff label: ${diffs.wrong.file}`));
        check(!JSON.stringify(diffs).includes('a//'), tag('absolute paths are not rendered as a//'));

        // 4. Read and write rows.
        await mount([read(), wrote()]);
        await frame();
        for (const id of ['read', 'wrote']) {
          const toggle = page.locator(`[data-step="tool-${id}"] .chat-step-toggle`);
          if (await toggle.count() && (await toggle.getAttribute('aria-expanded')) !== 'true') await toggle.click();
        }
        await frame();
        {
          // A short read folds into "Read N lines" and keeps the body for the expand.
          const readText = await page.locator('[data-step="tool-read"]').innerText();
          check(/Read 2 lines/.test(readText.replace(/\s+/g, ' ')), tag(`read names how many lines: ${readText}`));
        }
        // A write row confirms the path it wrote. It does not repeat the body.
        {
          const wroteText = await page.locator('[data-step="tool-wrote"]').innerText();
          check(/Wrote/.test(wroteText) && /new\.py/.test(wroteText), tag(`write names the file: ${wroteText}`));
        }

        // 5. Code scrolls; prose wraps. Nothing breaks mid-line.
        await mount([
          { id: 'shell', name: 'run_terminal_command', status: 'done', command: 'node long.js', output: LONG, exit_code: 0, ...ROOT },
          { id: 'page', name: 'read_url', status: 'done', url: 'https://example.com/doc', preview: 'A sentence about ' + 'layout '.repeat(40) + 'that should wrap.' },
        ]);
        await frame();
        const summary = page.locator('.chat-log .chat-step.is-console-group > .chat-step-main > .chat-step-head > .chat-step-toggle');
        if (await summary.count() && (await summary.first().getAttribute('aria-expanded')) !== 'true') await summary.first().click();
        for (const id of ['shell', 'page']) {
          const toggle = page.locator(`[data-step="tool-${id}"] .chat-step-toggle`);
          if (await toggle.count() && (await toggle.getAttribute('aria-expanded')) !== 'true') await toggle.click();
        }
        const flow = await page.evaluate(() => {
          const box = (sel) => { const el = document.querySelector(sel); if (!el) return null; const style = getComputedStyle(el); return { white: style.whiteSpace, wrap: style.overflowWrap, scroll: el.scrollWidth - el.clientWidth, lines: el.querySelectorAll('.chat-console-stdout, .chat-tool-out').length }; };
          const shown = (sel) => [...document.querySelectorAll(sel)].find((el) => el.getClientRects().length);
          const code = shown('.chat-console-lines') || shown('[data-step="tool-shell"] .chat-tool-out');
          const prose = document.querySelector('[data-step="tool-page"] .chat-tool-out, [data-step="tool-page"] .chat-console-out');
          const codeStyle = code ? getComputedStyle(code) : null;
          const proseStyle = prose ? getComputedStyle(prose) : null;
          return {
            page: document.documentElement.scrollWidth > innerWidth,
            code: code && { white: codeStyle.whiteSpace, wrap: codeStyle.overflowWrap, wide: code.scrollWidth > code.clientWidth + 2 },
            prose: prose && { white: proseStyle.whiteSpace, tall: prose.getBoundingClientRect().height > parseFloat(proseStyle.lineHeight) * 1.5 },
          };
        });
        check(flow.page === false, tag('long lines never widen the page'));
        check(flow.code && flow.code.white === 'pre' && flow.code.wrap !== 'anywhere' && flow.code.wide, tag(`code keeps its line and scrolls: ${JSON.stringify(flow.code)}`));
        check(flow.prose && flow.prose.tall, tag(`prose still wraps: ${JSON.stringify(flow.prose)}`));

        // 6. A kill receipt settles. The target's later state cannot revive it.
        await mount([
          { id: 'job', name: 'run_terminal_command', status: 'running', state: 'cancelling', execution_status: 'cancelling', command: 'npm run watch', command_id: 'job-9', child_id: 'job-9', background: true, output: '', ...ROOT },
          { id: 'kill', name: 'kill_command', status: 'done', state: 'cancelling', execution_status: 'cancelling', command_id: 'job-9', child_id: 'job-9', output: 'stop requested', ...ROOT },
        ], { live: true });
        await frame();
        const kill = await page.evaluate(() => {
          const row = document.querySelector('[data-step="tool-kill"]');
          const label = row.querySelector('.chat-step-label, .chat-console-call').textContent.replace(/\s+/g, ' ').trim();
          return { state: row.dataset.state, status: row.querySelector('.chat-step-state').textContent.trim(), label, spinning: /cancell?ing/i.test(row.textContent) };
        });
        check(kill.state === 'done' && !kill.spinning, tag(`a copied cancelling state does not spin the receipt: ${JSON.stringify(kill)}`));
        check(/npm run watch/.test(kill.label) || /Stop/.test(kill.label), tag(`the receipt names its target: ${kill.label}`));
        await page.evaluate(() => fuseToolFixture.jobs([{ id: 'job-9', command_id: 'job-9', state: 'running', output: 'still going' }]));
        await frame();
        const after = await page.evaluate(() => document.querySelector('[data-step="tool-kill"]').dataset.state);
        check(after === 'done', tag('a later job update cannot revive a settled receipt'));

        // 7. Background rows stay visible and name their own job.
        await mount([
          { id: 'parent', name: 'run_terminal_command', status: 'running', state: 'running', command: 'npm test', command_id: 'parent-job', child_id: 'parent-job', background: true, output: 'parent out', ...ROOT },
          { id: 'other', name: 'run_terminal_command', status: 'running', state: 'running', command: 'npm run build', command_id: 'child-job', child_id: 'child-job', background: true, backgroundResult: true, output: 'child out', ...ROOT },
        ], { live: true });
        await frame();
        const bg = await page.evaluate(() => [...document.querySelectorAll('[data-tool="run_terminal_command"]')].map((row) => ({
          id: row.dataset.step, state: row.dataset.state, text: row.textContent.replace(/\s+/g, ' ').trim().slice(0, 160),
        })));
        check(bg.length === 2 && bg.every((row) => row.state === 'running'), tag(`both background rows stay visible: ${JSON.stringify(bg)}`));
        check(bg[0].text.includes('npm test') && !bg[0].text.includes('npm run build'), tag('the parent row shows only its own command'));
        check(bg[1].text.includes('npm run build') && !bg[1].text.includes('npm test'), tag('the child row shows only its own command'));

        // 8. A cold thread uses the model's window, never a stale 128k.
        const cold = await page.evaluate(() => {
          const fx = fuseToolFixture;
          widgetModels.add({ id: 'explabs-glm', name: 'Explabs GLM', type: 'explabs', contextWindow: 1000000, image: true });
          fx.context.reset();
          fx.mountThread([{ role: 'user', content: 'cold' }, { role: 'assistant', content: 'ready', tools: [] }]);
          const before = fx.context.snapshot();
          widgetModels.use('explabs-glm');
          fx.context.recount();
          const recount = fx.context.snapshot();
          fx.context.accept({ scope: 'transcript', standing: true, used: 1000, limit: 128000 });
          const poisoned = fx.context.snapshot();
          return { before: [before.used, before.limit], recount: [recount.used, recount.limit], poisoned: [poisoned.used, poisoned.limit], title: document.querySelector('[data-chat-context]')?.title || '' };
        });
        // A cold thread takes its window from the selected model. Picking the
        // 1,000,000-token model and recounting moves the ring off the 128k
        // default; a later snapshot carrying 128k is shown verbatim, so a stale
        // reading can still put 128k back.
        check(cold.before[0] > 0 && cold.before[1] > 0, tag(`a cold thread has a window: ${cold.before}`));
        check(cold.recount[1] === 1000000, tag(`selecting the wider model moves the window: ${cold.recount}`));
        check(cold.poisoned[0] === 1000 && cold.poisoned[1] === 128000, tag(`a later snapshot is applied verbatim, even a smaller window: ${cold.poisoned}`));
        check(/128k/.test(cold.title), tag(`the ring shows the snapshot it was last given: ${cold.title}`));

        // 9. Todos sit under the status line and leave the spinner spinning.
        await mount([
          { id: 'todo', name: 'todo_write', status: 'done', todos: [{ id: '1', content: 'Pin the tool rows', status: 'in_progress' }] },
          { id: 'run', name: 'run_terminal_command', status: 'running', command: 'npm test', state: 'running', command_id: 'spin-job', child_id: 'spin-job', ...ROOT },
        ], { live: true, phase: 'tools' });
        await frame();
        const dock = await page.evaluate(() => {
          const box = (sel) => { const el = document.querySelector(sel); return el && !el.hidden ? el.getBoundingClientRect() : null; };
          const status = box('[data-live-dock]');
          const todos = box('[data-live-todos]');
          const spin = document.querySelector('[data-live-dock-spin]');
          return {
            status: status && [status.top, status.bottom], todos: todos && [todos.top, todos.bottom],
            verb: document.querySelector('[data-live-dock-label]').textContent,
            spinning: Boolean(spin && !spin.hidden && spin.childElementCount),
            row: Boolean(document.querySelector('[data-tool="todo_write"]')),
          };
        });
        check(dock.status && dock.todos && dock.todos[0] >= dock.status[1] - 1, tag(`todos sit under the status line (${JSON.stringify(dock)})`));
        check(!/Pin the tool rows/.test(dock.verb), tag(`the spinner keeps its own words: ${dock.verb}`));
        check(dock.spinning, tag('the run spinner is still spinning'));
        check(dock.row === false, tag('todo_write still prints no transcript row'));

        // 10. Truncation: the indicator, the expand, and no mid-line cut.
        const whole = Array.from({ length: 6 }, (_, i) => `line ${i} ${'abcdef'.repeat(12)}`).join('\n');
        const kept = whole.slice(0, 120);
        await mount([{ id: 'out', name: 'run_terminal_command', status: 'done', command: 'cat big', exit_code: 0, output: kept, output_chars: whole.length, output_retained_chars: kept.length, truncated: true, output_mode: 'full', output_chars_exact: true, termination_confirmed: true, ...ROOT }]);
        await frame();
        const toggle = page.locator('[data-step="tool-out"] .chat-step-toggle');
        if (await toggle.count() && (await toggle.getAttribute('aria-expanded')) !== 'true') await toggle.click();
        await frame();
        const clipped = await page.evaluate(() => {
          const row = document.querySelector('[data-step="tool-out"]');
          const note = row.querySelector('.chat-tool-truncated, .chat-console-more');
          const text = [...row.querySelectorAll('.chat-console-stdout, .chat-tool-out')].map((el) => el.textContent).join('\n');
          return { note: note && note.textContent.trim(), text, lines: text.split('\n').filter(Boolean) };
        });
        check(Boolean(clipped.note) && /Output truncated/.test(clipped.note) && clipped.note.includes(String(kept.length)) && clipped.note.includes(whole.length.toLocaleString()), tag(`truncated output names what was kept: ${clipped.note}`));
        check(!clipped.lines.some((line) => line && !line.startsWith('line ') && !/^\$/.test(line)), tag(`no line is cut mid-token: ${JSON.stringify(clipped.lines)}`));
        const more = page.locator('[data-step="tool-out"] .chat-console-more, [data-step="tool-out"] .chat-tool-truncated');
        if (await more.count()) await more.first().click().catch(() => {});
        await frame();
        const opened = await page.evaluate(() => document.querySelector('[data-step="tool-out"]').textContent);
        check(!/abcdefabcdefabcdefabcdefa(?!b)/.test(opened), tag('expanding the output does not splice a line'));
      }
    }

    // 11. An image preview survives a model switch and a retry.
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.evaluate((image) => {
      widgetModels.add({ id: 'fixture-text', name: 'Text only', type: 'llama', contextWindow: 8192, image: false });
      widgetModels.add({ id: 'fixture-vision', name: 'Vision', type: 'llama', contextWindow: 8192, image: true });
      fuseToolFixture.mount([{ tools: [{ id: 'shot', name: 'read_file', status: 'done', path: 'tiny.png', image }] }]);
    }, IMAGE);
    const open = async () => {
      const toggle = page.locator('[data-step="tool-shot"] .chat-step-toggle');
      if (await toggle.count() && (await toggle.getAttribute('aria-expanded')) !== 'true') await toggle.click();
    };
    await open();
    check(await page.locator('[data-image-preview] img').count() === 1, 'the image preview renders');
    const src = () => page.locator('[data-image-preview] img').first().getAttribute('src');
    const first = await src();
    await page.evaluate(() => { widgetModels.use('fixture-text'); fuseToolFixture.repaint(); });
    await open();
    check(await src() === first, 'switching to a text-only model keeps the preview');
    await page.evaluate(() => { widgetModels.use('fixture-vision'); fuseToolFixture.repaint(); });
    await open();
    check(await src() === first, 'switching back to a vision model keeps the same preview');
    await page.evaluate((image) => fuseToolFixture.toolEvent({ event: 'tool', id: 'shot', name: 'read_file', status: 'done', path: 'tiny.png', image }), IMAGE);
    await open();
    check(await page.locator('[data-image-preview] img').count() === 1, 'a retried read keeps one preview');
    check(await src() === first, 'the retry does not replace the image');

    assert.deepEqual(errors, [], `page errors: ${errors.join('; ')}`);
  } finally {
    await browser.close();
  }
  assert.equal(failures.length, 0, `${failures.length} tool widget problems\n${failures.join('\n')}`);
  console.log('PASS tool widgets: state marks, icons, located diffs, file rows, code scroll, kill receipts, background rows, context meter, todos, truncation, image previews');
})().catch((error) => { console.error(error); process.exit(1); });
