// Tool-row LAYOUT, measured with getBoundingClientRect at
// 1440x1000 and 390x844 (never screenshots): long unbroken paths, commands,
// URLs, patterns and subagent descriptions in every tool kind's header
// (ellipsis + title, or wrap once opened), multi-line commands, expanded
// bodies that scroll sideways instead of widening the page, one column grid
// across rows of different kinds, symmetric spacing around prose, the running
// chrome (status line, todos, composer) clear of the last row, and a
// running -> done header rewrite that shifts nothing.
// Row-state, dot colours, clocks and stale rules are pinned by
// console_ui.browser.cjs / console_rows_audit.browser.cjs / tool_widgets;
// this suite only pins geometry. Fixture only; never the live service.
const assert = require('node:assert/strict');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const origin = process.env.FUSE_BROWSER_ORIGIN || 'http://127.0.0.1:8099';
assert.match(origin, /^http:\/\/127\.0\.0\.1:\d+$/);

const SIZES = [[1440, 1000], [390, 844]];
const ROOT = { host: 'qa-box', cwd: '/srv/qa', platform: 'posix', project_root: '/srv/qa' };
const TOKEN = 'dispatcher_backoff_and_retries_integration_test_fixture_payload_0123456789abcdef';
const DEEPDIR = `/srv/qa/integration/backoff/retries/${TOKEN}_directory`;
const DEEP = `${DEEPDIR}/module_with_a_really_long_file_name_v2.py`;
const PAGE_URL = `https://docs.example.com/en-us/integration/${TOKEN}?section=backoff-retries&query=${TOKEN}`;
const PATTERN = `(?:(?:retry|backoff)_)?(?:max_)?attempts_[0-9a-f]{16}(?:_fallback)_v2`;
const MULTILINE = `for svc in api worker dispatcher; do\n  ./scripts/deploy.sh --service "$svc" --rollback-window 15m --verify-health && echo "$svc ok"\ndone`;
const DIFF = ['--- a/src/app.py', `+++ b/${DEEP}`, '@@ -40,2 +40,2 @@', ` def load():`, `-    return retries_${TOKEN}`, `+    return attempts_${TOKEN}`].join('\n');
const WIDE_LINE = `const value = ${'x'.repeat(240)};`;

let seq = 0;
const t = (name, fields = {}) => ({
  id: fields.id || `k${++seq}`, name, status: 'done', error: '', results: [], output: '',
  duration_ms: 2400, call_duration_ms: 2400, ...ROOT, ...fields,
});
const sh = (id, command, fields = {}) => t('run_terminal_command', {
  id, command, child_id: `job-${id}`, command_id: `job-${id}`, job_id: `job-${id}`, timeout_ms: 120000,
  termination_confirmed: true, state: 'completed', execution_status: 'completed', exit_code: 0,
  elapsed_ms: fields.ms ?? 9000, execution_duration_ms: fields.ms ?? 9000,
  output: fields.output || '', preview: (fields.output || '').slice(0, 120), ...fields,
});

// One call of every tool kind, each carrying an unbreakable long value where
// the kind has one. folds: true folds into a summary line (read-like calls
// settle out of a live thread); kinds without it never fold and always print
// their own row.
const kinds = () => [
  { trace: t('read_file', { id: 'k-read', path: DEEP, output: `1: retries = 3\n2: backoff = ${'x'.repeat(200)}\n3: done\n` }), folds: true },
  { trace: t('write_file', { id: 'k-write', path: DEEP.replace('module', 'generated'), content: `print("${'x'.repeat(200)}")\n` }) },
  { trace: t('search_replace', { id: 'k-edit', path: DEEP, preview: DIFF, output: `updated ${DEEP}`, added: 1, removed: 1 }) },
  { trace: t('list_dir', { id: 'k-list', path: DEEPDIR, output: `${TOKEN}_a.py\n${TOKEN}_b.py\n` }), folds: true },
  { trace: t('glob', { id: 'k-glob', pattern: `**/*${TOKEN}{,_fixture,_config}.py`, path: '/srv/qa', output: `tests/${TOKEN}_fixture.py\n` }), folds: true },
  { trace: t('grep', { id: 'k-grep', pattern: PATTERN, path: DEEPDIR, output: `${DEEP}:12:${PATTERN}\n` }), folds: true },
  { trace: sh('k-shell', MULTILINE, { ms: 124000, output: `svc api ok\nsvc worker ok\n${WIDE_LINE}\nsvc dispatcher ok\n5 lines tall\n` }), folds: true },
  { trace: sh('k-bg', `./gameserver -config=${TOKEN}.cfg -port=2302 -mod=@${TOKEN};@maps`, { background: true, ms: 300, state: 'running', execution_status: 'running', status: 'done', exit_code: null, output: 'Loading…\n' }) },
  { trace: t('get_command_output', { id: 'k-wait', command: `./scripts/promote.sh --service ${TOKEN} --rollback-window 15m`, background: true, child_id: 'job-k-bg', command_id: 'job-k-bg', call_duration_ms: 31000, output: 'promoted\n' }) },
  { trace: t('monitor', { id: 'k-monitor', description: `retries and failures in /var/log/worker/${TOKEN}.log while the deploy rolls out`, command: `tail -F /var/log/worker/${TOKEN}.log`, pattern: 'ERROR|SUCCESS', background: true, child_id: 'mon-k', command_id: 'mon-k', state: 'running', execution_status: 'running', event_count: 2, timeout_ms: 300000 }) },
  { trace: t('web_search', { id: 'k-search', queries: [`integration dispatcher backoff retries ${TOKEN}`, 'second query', 'third query'], latency_ms: 2300, results: [{ title: TOKEN, url: `https://example.test/${TOKEN}`, snippet: 's' }] }) },
  { trace: t('read_url', { id: 'k-fetch', url: PAGE_URL, bytes: 128000, http_status: 200, preview: 'A page about backoff.' }) },
  { trace: t('spawn_subagent', { id: 'k-spawn', description: `Audit the ${TOKEN} retry ladder and report the ceiling per region`, subagent_type: 'long-haul-refactoring', child_id: 'agent-k', agent_tool_uses: 3, agent_work_ms: 65000, background: true }) },
  { trace: t('get_subagent_output', { id: 'k-join', description: `Audit the ${TOKEN} retry ladder`, child_id: 'agent-k', agent_work_ms: 65000, call_duration_ms: 2100 }) },
  { trace: t('load_skill', { id: 'k-load', preview: `dietrichgebert/${TOKEN}-refactoring-agent` }) },
  { trace: t('create_artifact', { id: 'k-art', title: `${TOKEN} report`, identifier: 'layout-artifact', version: 3 }) },
  { trace: t('search_models', { id: 'k-models', results: [{ id: `explabs/glm-4.7-${TOKEN}`, name: `GLM ${TOKEN}`, provider: 'explabs' }] }) },
  { trace: t('list_subagents', { id: 'k-agents', results: [{ id: 'agent-k', status: 'completed', model: `explabs/glm-4.7-${TOKEN}`, description: `Audit the ${TOKEN} ladder` }] }) },
  { trace: t('scratch', { id: 'k-scratch', op: 'put', slots: { [`${TOKEN}_notes`]: 1234 } }) },
  { trace: t('kill_command', { id: 'k-kill', child_id: 'job-k-bg', command_id: 'job-k-bg' }) },
];
const say = (text) => ({ type: 'content', text });
const T = (trace) => ({ type: 'tool', id: trace.id });
const turn = (segments, tools, extra = {}) => ({
  role: 'assistant', model: 'qa/model', elapsedMs: 30000, tools, segments,
  content: segments.filter((s) => s.type === 'content').map((s) => s.text).join(''), reasoning: '', ...extra,
});
const user = (content) => ({ role: 'user', content, reasoning: '', error: '' });
const CODE_FENCE = `Prose before.\n\n\`\`\`python\n${WIDE_LINE}\nprint("done")\n\`\`\`\n\nProse after.\n\n`;

const kindsThread = (list) => {
  const segments = [];
  for (const kind of list) { segments.push(say('Step.\n\n'), T(kind.trace)); }
  segments.push(say(CODE_FENCE));
  return [user('Go.'), turn(segments, list.map((k) => k.trace))];
};

(async () => {
  const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'], ...(process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {}) });
  const failures = [];
  const check = (ok, message) => { if (!ok) failures.push(message); };
  try {
    const page = await browser.newPage({ reducedMotion: 'reduce', viewport: { width: 1440, height: 1000 } });
    const errors = [];
    page.on('pageerror', (error) => errors.push(error.message));
    await page.route('**/*', (route) => new URL(route.request().url()).origin === origin ? route.continue() : route.abort());
    await page.goto(origin);
    await page.waitForFunction(() => window.fuseToolFixture);
    await page.addStyleTag({ content: '* { animation: none !important; transition: none !important; }' });
    await page.evaluate(() => {
      window.__layout = () => {
        const log = document.querySelector('.chat-log');
        const box = (el) => { if (!el || !el.getClientRects().length) return null; const r = el.getBoundingClientRect(); return { l: r.left, t: r.top, r: r.right, b: r.bottom, w: r.width, h: r.height }; };
        const rows = [...log.querySelectorAll('li.chat-step')].filter((li) => li.getClientRects().length).map((li) => {
          const head = li.querySelector(':scope > .chat-step-main > .chat-step-head');
          const label = head && head.querySelector('.chat-step-label');
          const cs = label && getComputedStyle(label);
          const lh = cs && parseFloat(cs.lineHeight);
          const lines = (label, rect) => Number.isFinite(lh) && lh > 0 ? Math.max(1, Math.round(rect.h / lh)) : label.getClientRects().length;
          const scrollers = [...li.querySelectorAll('.chat-console-lines, pre.chat-console-out, .chat-tool-out')].filter((el) => el.getClientRects().length)
            .map((el) => ({ cls: el.className, rect: box(el), sw: el.scrollWidth - el.clientWidth }));
          const labelBox = label && box(label);
          return {
            step: li.dataset.step || '', tool: li.dataset.tool || '', state: li.dataset.state || '',
            group: li.classList.contains('is-console-group'), verbose: li.classList.contains('is-console-verbose'),
            row: box(li), node: box(li.querySelector(':scope > .chat-step-node')),
            toggle: box(head && (head.querySelector(':scope > .chat-step-toggle') || head.querySelector(':scope > .chat-step-static'))),
            label: label && labelBox && { ...labelBox, lines: lines(label, labelBox), clip: label.scrollWidth > label.clientWidth + 1, ell: cs.textOverflow === 'ellipsis', hide: cs.overflow === 'hidden', title: label.title || '' },
            call: box(label && label.querySelector('.chat-console-call')), took: box(label && label.querySelector('.chat-console-took')),
            host: box(label && label.querySelector('.chat-tool-host')),
            verb: label && (() => { const el = label.querySelector('.chat-tool-action'); const b = box(el); return b ? { ...b, minW: parseFloat(getComputedStyle(el).minWidth) } : null; })(),
            val: box(label && label.querySelector('.chat-console-val')), result: box(li.querySelector('.chat-console-result-text')),
            scrollers, text: ((label || li).innerText || '').replace(/\s+/g, ' ').trim().slice(0, 90),
          };
        });
        const dock = document.querySelector('[data-live-dock]');
        const todos = document.querySelector('[data-live-todos]');
        return {
          overflowX: Math.round((document.scrollingElement.scrollWidth - innerWidth) * 10) / 10, log: box(log), rows,
          dock: dock && !dock.hidden ? box(dock) : null, todos: todos && !todos.hidden ? box(todos) : null,
          compose: box(document.querySelector('.chat-dock .chat-box')),
        };
      };
    });
    const layout = () => page.evaluate(() => window.__layout());
    const openGroups = () => page.evaluate(() => {
      document.querySelectorAll('.chat-log .chat-step.is-console-group > .chat-step-main > .chat-step-head > [data-step-toggle]').forEach((button) => {
        if (button.getAttribute('aria-expanded') !== 'true') button.click();
      });
    });
    const frame = () => page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));

    // A row's header keeps inside the log, single-line unless it may wrap, and
    // its clock is never overlapped by the text or spilled.
    const rowHeader = (p, row, where, wrapOk = false) => {
      const at = `${where} ${row.step}`;
      if (!row.row || !row.label) return `${at}: no label`;
      if (row.row.r > p.log.r + 1) failures.push(`${at}: row spills the log (right ${row.row.r.toFixed(1)} > ${p.log.r.toFixed(1)}) "${row.text}"`);
      if (row.label.r > p.log.r + 1) failures.push(`${at}: header spills the log "${row.text}"`);
      if (!wrapOk && row.label.lines > 1) failures.push(`${at}: header wraps to ${row.label.lines} lines "${row.text}"`);
      if (wrapOk && row.label.lines > 1 && row.label.clip && !(row.label.ell && row.label.hide)) failures.push(`${at}: wrapped header clipped without ellipsis "${row.text}"`);
      // On a wrapped (opened) header the clock trails the last line, so the
      // call text's widest line can pass it without any overlap; only a
      // single-line header can truly collide. A chip past the label edge is a
      // spill only when the label does not clip (overflow: hidden).
      if (row.took && row.label.lines === 1) {
        if (row.call && row.call.r > row.took.l + 0.5) failures.push(`${at}: clock overlapped by the call text "${row.text}"`);
        if (row.took.r > row.label.r + 1) failures.push(`${at}: clock spills its header "${row.text}"`);
        if (row.took.b > row.label.b + 1) failures.push(`${at}: clock sinks below its row "${row.text}"`);
      }
      if (row.host && row.host.r > row.label.r + 1 && !row.label.hide) failures.push(`${at}: host chip spills its header "${row.text}"`);
      return null;
    };

    {
      for (const [width, height] of SIZES) {
        const where = `[${width}px]`;
        await page.setViewportSize({ width, height });

        // —— 1. Every tool kind live: kinds that never fold stand as rows with
        // ellipsized headers; read-like kinds fold into summary lines — either
        // way one column grid, no overflow.
        await page.evaluate((rows) => fuseToolFixture.mountThread(rows, { live: true, phase: 'tools' }), kindsThread(kinds()));
        await frame();
        const p = await layout();
        if (process.env.DEBUG) console.log(where, 'LIVE', JSON.stringify(p));
        check(p.overflowX <= 1, `${where}: live page overflows horizontally by ${p.overflowX}px`);
        const list = kinds();
        for (const kind of list) {
          const row = p.rows.find((r) => r.step === `tool-${kind.trace.id}`);
          const foldsHere = Boolean(kind.folds);
          if (foldsHere) {
            const group = p.rows.find((r) => r.step === `cgroup-tool-${kind.trace.id}`);
            check(!row && Boolean(group), `${where} ${kind.trace.id}: read-like call folds into its summary line (row ${Boolean(row)}, group ${Boolean(group)})`);
            if (group) rowHeader(p, group, where, true);
            continue;
          }
          if (!row) { failures.push(`${where} ${kind.trace.id}: no row (rows: ${p.rows.map((r) => r.step).join(',')})`); continue; }
          rowHeader(p, row, where);
          if (kind.trace.id === 'k-shell') {
            check(row.label.lines === 1, `${where}: a multi-line command header stays one line (${row.label.lines}) "${row.text}"`);
            check(row.label.title.includes('\n'), `${where}: the multi-line command keeps its line breaks in the row title (got ${JSON.stringify(row.label.title)})`);
          }
          if (['k-read', 'k-write', 'k-edit', 'k-fetch'].includes(kind.trace.id)) {
            const raw = { 'k-read': DEEP, 'k-write': DEEP.replace('module', 'generated'), 'k-edit': DEEP, 'k-fetch': PAGE_URL }[kind.trace.id];
            check(row.label.title === raw, `${where} ${kind.trace.id}: row title carries the full value (got ${JSON.stringify(row.label.title)})`);
          }
        }
        // Column grid across kinds (summary lines included): dots and the
        // label column.
        const standalone = p.rows.filter((r) => r.label);
        if (standalone.length >= 4) {
          const col = (values, name) => {
            const vs = values.filter((v) => Number.isFinite(v));
            const spread = Math.max(...vs) - Math.min(...vs);
            check(spread <= 1, `${where}: ${name} column drifts across kinds (${spread.toFixed(2)}px over ${vs.length} rows)`);
          };
          col(standalone.map((r) => r.node && r.node.l), 'dot');
          col(standalone.filter((r) => !r.group).map((r) => r.label.l), 'label');
          for (const r of standalone) if (r.node && r.label.l < r.node.r - 0.5) failures.push(`${where} ${r.step}: header starts left of the dot column "${r.text}"`);
        }
        // Sideways scrollers inside rows never widen the page or leave the log.
        for (const row of p.rows) {
          for (const sc of row.scrollers) {
            if (sc.rect && sc.rect.r > p.log.r + 1) failures.push(`${where} ${row.step}: ${sc.cls} body spills the log (right ${sc.rect.r.toFixed(1)} > ${p.log.r.toFixed(1)})`);
          }
        }

        // —— 2. Symmetric spacing: prose between rows gets the same gap above
        // and below; no two rows ever overlap.
        const gaps = await page.evaluate(() => {
          const flow = document.querySelector('.chat-log .chat-flow');
          if (!flow) return [];
          const blocks = [...flow.children].filter((el) => el.getClientRects().length);
          return blocks.map((el, i) => {
            const r = el.getBoundingClientRect();
            return { md: el.classList.contains('chat-md'), top: i ? r.top - blocks[i - 1].getBoundingClientRect().bottom : null, bottom: i < blocks.length - 1 ? blocks[i + 1].getBoundingClientRect().top - r.bottom : null };
          });
        });
        for (const md of gaps.filter((g) => g.md && g.top !== null && g.bottom !== null)) {
          check(Math.abs(md.top - md.bottom) <= 0.75, `${where}: prose gap asymmetric (${md.top.toFixed(2)}px above, ${md.bottom.toFixed(2)}px below)`);
        }
        for (let i = 1; i < p.rows.length; i++) {
          if (p.rows[i].row.t < p.rows[i - 1].row.b - 0.5) failures.push(`${where}: ${p.rows[i - 1].step} overlaps ${p.rows[i].step} below it`);
        }

        // —— 3. Settled snapshot, every summary line opened: headers wrap (or
        // ellipsize) instead of spilling, columns survive, nothing is dropped
        // or doubled versus the live render.
        await page.evaluate((rows) => fuseToolFixture.mountThread(rows), kindsThread(kinds()));
        await openGroups();
        await frame();
        const before = await layout();
        if (process.env.DEBUG) console.log(where, 'OPEN', JSON.stringify(before));
        check(before.overflowX <= 1, `${where}: opened page overflows horizontally by ${before.overflowX}px`);
        for (const kind of list) {
          const found = before.rows.filter((r) => r.step === `tool-${kind.trace.id}`);
          check(found.length === 1, `${where} ${kind.trace.id}: ${found.length} rows after opening every summary (want 1)`);
          for (const row of found) rowHeader(before, row, `${where} opened`, true);
        }
        const opened = before.rows.filter((r) => !r.group && r.label);
        if (opened.length >= 4) {
          const live = new Map(standalone.map((r) => [r.step, r]));
          for (const row of opened) {
            const was = live.get(row.step);
            if (was && Math.abs((was.label.l) - row.label.l) > 1) failures.push(`${where} ${row.step}: label column moves between live and opened render (${was.label.l.toFixed(1)} -> ${row.label.l.toFixed(1)})`);
          }
        }
        // An expanded body keeps its sideways scroller inside the log; a long
        // line stays one line box, and the markdown code fence scrolls too.
        const bodies = await page.evaluate(() => {
          const log = document.querySelector('.chat-log');
          const right = log.getBoundingClientRect().right;
          const measure = (el) => ({ cls: el.className, r: el.getBoundingClientRect().right, sw: el.scrollWidth - el.clientWidth, lines: [...el.querySelectorAll('.chat-console-nbody')].map((n) => n.getClientRects().length) });
          const code = document.querySelector('.chat-md .chat-code pre');
          return {
            right,
            out: [...log.querySelectorAll('li.chat-step .chat-console-lines, li.chat-step pre.chat-console-out')].filter((el) => el.getClientRects().length).slice(0, 8).map(measure),
            fence: code ? measure(code) : null,
          };
        });
        for (const body of bodies.out) {
          if (body.r > bodies.right + 1) failures.push(`${where}: expanded ${body.cls} spills the log (right ${body.r.toFixed(1)} > ${bodies.right.toFixed(1)})`);
          if (body.lines.some((n) => n > 1)) failures.push(`${where}: expanded ${body.cls} wraps a code line (${body.lines.join(',')}) instead of scrolling (scrollable ${body.sw}px)`);
        }
        if (bodies.fence) {
          check(bodies.fence.r <= bodies.right + 1, `${where}: the markdown code fence spills the log (right ${bodies.fence.r.toFixed(1)} > ${bodies.right.toFixed(1)})`);
        }

        // —— 4. Running chrome: the status line, todo list and composer clear
        // of the last running row, and of each other.
        const todoTrace = t('todo_write', { id: 'lay-todo', todos: [{ id: '1', content: `Pin the ${TOKEN} layout`, status: 'in_progress' }], duration_ms: 120, call_duration_ms: 120 });
        const runTrace = t('run_terminal_command', { id: 'lay-run', status: 'running', state: 'running', execution_status: 'running', command: `npm test ${TOKEN} --reporter dot`, child_id: 'lay-job', command_id: 'lay-job', timeout_ms: 120000, background: true, output: '', duration_ms: 900, call_duration_ms: 900 });
        await page.evaluate(({ rows }) => {
          fuseToolFixture.mountThread(rows, { live: true, phase: 'tools' });
          fuseToolFixture.follow(true);
        }, { rows: [user('Go.'), turn([say('Running.\n\n'), T(todoTrace), T(runTrace)], [todoTrace, runTrace])] });
        await frame();
        const chrome = await layout();
        if (process.env.DEBUG) console.log(where, 'CHROME', JSON.stringify(chrome));
        check(chrome.overflowX <= 1, `${where}: running page overflows horizontally by ${chrome.overflowX}px`);
        const last = chrome.rows[chrome.rows.length - 1];
        check(Boolean(chrome.dock), `${where}: status line visible while the run is live`);
        if (last && chrome.dock) check(last.row.b <= chrome.dock.t + 0.75, `${where}: status line overlaps the last row (row bottom ${last.row.b.toFixed(1)}, dock top ${chrome.dock.t.toFixed(1)}) "${last.text}"`);
        if (chrome.dock && chrome.todos) check(chrome.dock.b <= chrome.todos.t + 0.75, `${where}: todo list overlaps the status line (dock bottom ${chrome.dock.b.toFixed(1)}, todos top ${chrome.todos.t.toFixed(1)})`);
        const bottomMost = [chrome.todos || chrome.dock, chrome.compose].filter(Boolean);
        if (bottomMost[0] && bottomMost[1]) check(bottomMost[0].b <= bottomMost[1].t + 0.75, `${where}: composer overlaps the todo list / status area (${bottomMost[0].b.toFixed(1)} > ${bottomMost[1].t.toFixed(1)})`);

        // —— 5. Running -> done: the header rewrite changes width; the row
        // keeps its column and stays unclipped, and the neighbour above does
        // not move. mount(), not mountThread: fixture.update walks every
        // message and cannot cross a user article.
        const steadyTrace = t('write_file', { id: 'steady', path: DEEP, content: 'x = 1\n' });
        const turnTrace = sh('turnrow', `python3 -m pytest -q tests/integration/test_${TOKEN}.py -k 'backoff or retries' --maxfail=1`, { status: 'running', state: 'running', execution_status: 'running', exit_code: null, description: 'Rebuild the production bundle', ms: 4000, output: '' });
        await page.evaluate((msg) => fuseToolFixture.mount([msg], true), turn([say('Two calls.\n\n'), T(steadyTrace), T(turnTrace)], [steadyTrace, turnTrace]));
        await frame();
        const live1 = await layout();
        if (process.env.DEBUG) console.log(where, 'TURN', JSON.stringify(live1));
        const target = (p) => p.rows.find((r) => r.step === 'tool-turnrow' || r.step === 'cgroup-tool-turnrow');
        const steadyBefore = live1.rows.find((r) => r.step === 'tool-steady');
        const turnBefore = target(live1);
        check(Boolean(steadyBefore && turnBefore), `${where}: running->done rows present (${live1.rows.map((r) => r.step).join(',')})`);
        if (turnBefore && turnBefore.took) check(turnBefore.took.r <= turnBefore.label.r + 1, `${where}: ticking clock spills the running header "${turnBefore.text}"`);
        await page.evaluate(() => fuseToolFixture.update('turnrow', {
          status: 'done', state: 'completed', execution_status: 'completed', exit_code: 0,
          execution_duration_ms: 124000, elapsed_ms: 124000, output: 'ok\n', preview: 'ok',
        }));
        await frame();
        const live2 = await layout();
        if (process.env.DEBUG) console.log(where, 'DONE', JSON.stringify(live2));
        check(live2.overflowX <= 1, `${where}: done page overflows horizontally by ${live2.overflowX}px`);
        const steadyAfter = live2.rows.find((r) => r.step === 'tool-steady');
        const turnAfter = target(live2);
        check(Boolean(steadyAfter && turnAfter), `${where}: rows after settling (${live2.rows.map((r) => r.step).join(',')})`);
        if (steadyBefore && steadyAfter) {
          check(Math.abs(steadyBefore.row.t - steadyAfter.row.t) <= 0.5, `${where}: the row above moved when the header rewrote (${steadyBefore.row.t.toFixed(1)} -> ${steadyAfter.row.t.toFixed(1)})`);
          check(Math.abs(steadyBefore.label.l - steadyAfter.label.l) <= 0.5, `${where}: the row above's column moved when the header rewrote`);
        }
        if (turnBefore && turnAfter) {
          rowHeader(live2, turnAfter, `${where} settled`);
          check(Math.abs(turnBefore.label.l - turnAfter.label.l) <= 0.5, `${where}: the settled row left its column (${turnBefore.label.l.toFixed(1)} -> ${turnAfter.label.l.toFixed(1)}) "${turnAfter.text}"`);
          if (turnAfter.step === 'tool-turnrow') check(turnAfter.state === 'done', `${where}: the settled row reads done (got ${turnAfter.state})`);
        }
      }
    }

    assert.deepEqual(errors, [], `page errors: ${errors.join('; ')}`);
    const unique = [...new Set(failures)];
    if (unique.length) console.error(unique.join('\n'));
    assert.equal(unique.length, 0, `${unique.length} tool-row layout problems`);
    console.log('PASS console layout: every tool kind at 1440/390 — columns, long headers, clocks, sideways bodies, spacing, running chrome, running->done');
  } finally {
    await browser.close();
  }
})().catch((error) => { console.error(error); process.exit(1); });
