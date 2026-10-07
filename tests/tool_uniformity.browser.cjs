// UNIFORMITY + CORRECTNESS matrix over every tool-call
// row type, in four presentations: settled/default, user-collapsed,
// user-expanded, and GROUPED (summary lines like "read 2 files, ran 1 shell
// command"). Isolated fixture only; never the live Fuse service.
//
// Angle (none of the neighbours pins this): CROSS-TOOL uniformity — one
// status vocabulary and casing, one placement for duration/exit/meta, one
// disclosure anatomy and chevron direction, one icon slot, one running
// affordance and one truncation pattern for every family — plus whole-matrix
// correctness (labels vs the trace's final status, counters vs members, no
// stale running, no duplicated meta between header and row).
//
// Already pinned elsewhere and deliberately NOT re-pinned here: per-tool
// lifecycle strings and values (tests/console_file_tools.browser.cjs,
// console_meta_tools.browser.cjs, console_shell_lifecycle.browser.cjs,
// console_subagent_rows.browser.cjs), seeded histories and stale-state rules
// (tests/console_ui.browser.cjs + tests/console_ui_lib.js), wait/exit
// precedence and the tool-state gallery (tests/tool_ui.browser.cjs), widget
// details (tests/tool_widgets.browser.cjs), collapse transitions
// (tests/collapse_states.browser.cjs). The skill row's one-line
// condensation is pinned by tests/tool_edge_cases.browser.cjs; only its
// semantics are pinned below.
const assert = require('node:assert/strict');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const origin = process.env.FUSE_BROWSER_ORIGIN || 'http://127.0.0.1:8099';
assert.match(origin, /^http:\/\/127\.0\.0\.1:\d+$/);

const ROOT = { host: 'qa-box', cwd: '/srv/qa', platform: 'posix', project_root: '/srv/qa' };
const PNG = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4z8AAAAMBAQDJ/pLvAAAAAElFTkSuQmCC';
const HITS = [
  { title: 'Console row guide', url: 'https://example.com/guide', snippet: 'How a row settles.' },
  { title: 'Lifecycle notes', url: 'https://example.com/lifecycle', snippet: 'Running, then done.' },
];
const MODELS = JSON.stringify({ models: [{ id: 'grok-4', name: 'Grok 4', provider: 'xai' }] });
const SLOTS = JSON.stringify({ slots: { brief: 1200 } });
const lines = (from, n) => Array.from({ length: n }, (_, i) => `${from + i}: line ${from + i}`).join('\n');

// Every name the Console row switch handles (static/chat.js consoleVerb,
// consoleCallHtml, consoleFold, toolAction) plus the fall-through name and
// the two names that print no row. `clock` marks the families whose duration
// rides the header clock; the rest state theirs in their result line (chat.js
// ccCallClockHtml: "Calls whose result already says how long (web search,
// subagents) have no clock"). `disclosure` is whether the row hides content
// behind its own head toggle (consoleFold full body).
const DUR = 43000; // "43s" — one distinctive duration token per clock row
const TOOLS = [
  { name: 'read_file', verb: 'Read', subject: 'app/config.py', clock: true, disclosure: true,
    trace: { status: 'done', path: '/srv/qa/app/config.py', offset: 40, limit: 60, output: lines(40, 60), call_duration_ms: DUR } },
  { name: 'write_file', verb: 'Write', subject: 'app/new.py', clock: true, disclosure: true,
    trace: { status: 'done', path: '/srv/qa/app/new.py', content: lines(1, 15), output: 'From host qa-box:\ncreated /srv/qa/app/new.py (28 bytes)', call_duration_ms: DUR } },
  { name: 'search_replace', verb: 'Update', subject: 'app/config.py', clock: true, disclosure: false,
    trace: { status: 'done', path: '/srv/qa/app/config.py', preview: '@@ -10,2 +10,2 @@\n-retries = 3\n+retries = 5', output: 'updated', call_duration_ms: DUR } },
  { name: 'list_dir', verb: 'List', subject: 'tests', clock: true, disclosure: true,
    trace: { status: 'done', path: '/srv/qa/tests', output: 'a.py\nb.py\nc.py\n', call_duration_ms: DUR } },
  { name: 'grep', verb: 'Search', subject: 'retries', clock: true, disclosure: true,
    trace: { status: 'done', pattern: 'retries', path: '/srv/qa', output: 'app/a.py:12:retries = 3\napp/b.py:30:retries = 5\n', call_duration_ms: DUR } },
  { name: 'glob', verb: 'Search', subject: '**/*.ts', clock: true, disclosure: true,
    trace: { status: 'done', pattern: '**/*.ts', path: '/srv/qa', output: 'src/a.ts\nsrc/b.ts\n', call_duration_ms: DUR } },
  { name: 'run_terminal_command', verb: 'Bash', subject: 'npm test', clock: true, disclosure: true,
    trace: { status: 'done', state: 'completed', execution_status: 'completed', command: 'npm test', command_id: 'j1', child_id: 'j1', job_id: 'j1', exit_code: 0, output: 'one\ntwo\nthree\nfour\nfive\n', call_duration_ms: DUR, termination_confirmed: true } },
  { name: 'get_command_output', verb: 'Wait', subject: 'npm run build', clock: true, disclosure: true,
    trace: { status: 'done', command: 'npm run build', command_id: 'j2', child_id: 'j2', job_id: 'j2', output: 'one\ntwo\nthree\nfour\nfive\n', call_duration_ms: DUR } },
  // `bare` rows print just their verb (chat.js consoleCallHtml:8368); the
  // subject rides their result line, and the shared clock/host tail applies.
  { name: 'kill_command', verb: 'Stop Task', subject: 'npm run watch', clock: true, disclosure: false, bare: true,
    trace: { status: 'done', command_id: 'j3', child_id: 'j3', output: '', call_duration_ms: DUR },
    related: { id: 't-kill_command-src', name: 'run_terminal_command', status: 'done', background: true, command: 'npm run watch', command_id: 'j3', child_id: 'j3', ...ROOT } },
  { name: 'monitor', verb: 'Monitor', subject: 'errors in worker.log', clock: true, disclosure: true,
    trace: { status: 'done', description: 'errors in worker.log', command: 'tail -F app.log', command_id: 'm1', child_id: 'm1', job_id: 'm1', event_count: 3, timeout_ms: 300000, call_duration_ms: DUR } },
  { name: 'web_search', verb: 'Web Search', subject: 'fuse rows', clock: false, disclosure: true,
    trace: { status: 'done', query: 'fuse rows', queries: ['fuse rows', 'tool lifecycle'], latency_ms: 840, results: HITS } },
  { name: 'search', verb: 'Web Search', subject: 'fuse rows', clock: false, disclosure: true,
    trace: { status: 'done', query: 'fuse rows', latency_ms: 840, results: HITS } },
  { name: 'read_url', verb: 'Fetch', subject: 'example.com/doc', clock: true, disclosure: true,
    trace: { status: 'done', url: 'https://example.com/doc', http_status: 200, bytes: 12600, call_duration_ms: DUR, results: [{ title: 'Doc', url: 'https://example.com/doc', snippet: 'Augusta wrote notes.' }] } },
  { name: 'spawn_subagent', verb: 'Agent', subject: 'Audit retries', clock: false, disclosure: false,
    trace: { status: 'done', description: 'Audit retries', child_id: 'c1', agent_tool_uses: 3, agent_tokens: 32100, agent_work_ms: 65000 } },
  { name: 'get_subagent_output', verb: 'Await', subject: 'Audit retries', clock: false, ticking: true, disclosure: false,
    trace: { status: 'done', description: 'Audit retries', child_id: 'c1', summary: 'AWAIT_REPORT', agent_work_ms: 41000 } },
  { name: 'kill_subagent', verb: 'Stop', subject: 'Audit retries', clock: true, disclosure: false,
    trace: { status: 'done', description: 'Audit retries', child_id: 'c1', output: '', call_duration_ms: DUR } },
  { name: 'list_subagents', verb: 'Agents', subject: '2 live, 1 finished', clock: true, disclosure: true, bare: true,
    trace: { status: 'done', preview: '2 live, 1 finished', output: '{"live":[{"id":"n1","description":"Rewrite auth","status":"running"}],"finished":[{"id":"old","description":"Prior audit","status":"completed"}]}', call_duration_ms: DUR } },
  { name: 'search_models', verb: 'Models', subject: 'grok', clock: true, disclosure: true,
    trace: { status: 'done', query: 'grok', preview: '2 models', output: MODELS, call_duration_ms: DUR } },
  { name: 'load_skill', verb: 'Loaded the', subject: 'ponytail', clock: true, disclosure: true,
    trace: { status: 'done', preview: 'ponytail', output: 'Loaded.\n\n# Ponytail\n\nBe lazy.', call_duration_ms: DUR } },
  { name: 'propose_skill', verb: 'Draft', subject: 'ponytail', clock: true, disclosure: true,
    trace: { status: 'done', preview: 'ponytail', output: 'Proposed skill `ponytail` (not saved).', call_duration_ms: DUR } },
  { name: 'create_artifact', verb: 'Artifact', subject: 'Sales dashboard', clock: true, disclosure: false,
    trace: { status: 'done', title: 'Sales dashboard', identifier: 'art-1', version: 3, visibility: 'private', call_duration_ms: DUR } },
  { name: 'update_artifact', verb: 'Artifact', subject: 'Sales dashboard', clock: true, disclosure: false,
    trace: { status: 'done', title: 'Sales dashboard', identifier: 'art-1', version: 4, visibility: 'public', call_duration_ms: DUR } },
  { name: 'scratch', verb: 'Scratch', subject: 'Write brief', clock: true, disclosure: true,
    trace: { status: 'done', description: 'Write brief', preview: '1 slots', output: SLOTS, call_duration_ms: DUR } },
  { name: 'frobnicate', verb: 'Tool', subject: 'odd', clock: true, disclosure: false,
    trace: { status: 'done', preview: 'odd', call_duration_ms: DUR } },
  { name: 'fuse-steer', verb: 'Notice', subject: 'New instruction', clock: true, disclosure: false,
    trace: { status: 'done', summary: 'New instruction', call_duration_ms: DUR } },
  // read_file image read: same family, different fold (no disclosure, figure inline).
  { name: 'read_file-image', verb: 'Read', subject: 'shots/before.png', clock: true, disclosure: false,
    trace: { status: 'done', path: '/srv/qa/shots/before.png', call_duration_ms: DUR,
      image: { format: 'PNG', mime: 'image/png', width: 640, height: 480, data: PNG } } },
  // Row-free by design (their lifecycle is pinned by console_meta_tools.browser.cjs).
  { name: 'todo_write', verb: null, subject: null, clock: false, disclosure: false,
    trace: { status: 'done', todos: [{ id: '1', content: 'Pin the rows', status: 'in_progress' }] } },
  { name: 'search_tools', verb: null, subject: null, clock: false, disclosure: false,
    trace: { status: 'done', query: 'shell', preview: 'run_terminal_command, get_command_output' } },
];
const ROW_FREE = new Set(['todo_write', 'search_tools']);
// Duration tokens the no-clock families state in their result line instead.
const RESULT_DUR = { web_search: '840ms', search: '840ms', spawn_subagent: '1m 5s', get_subagent_output: '41s' };

const idOf = (t) => `t-${t.name}`;
const nameOf = (t) => (t.name === 'read_file-image' ? 'read_file' : t.name);
const traceOf = (t) => ({ ...ROOT, id: idOf(t), name: nameOf(t), ...t.trace });

// One row's whole anatomy, judged only on visible text.
const COLLECT = () => {
  const flat = (el) => (el ? el.innerText.replace(/\s+/g, ' ').trim() : '');
  const all = (el) => (el ? el.textContent.replace(/\s+/g, ' ').trim() : '');
  return [...document.querySelectorAll('.chat-log li.chat-step')].map((li) => {
    const head = li.querySelector(':scope > .chat-step-main > .chat-step-head');
    const hang = li.querySelector(':scope > .chat-step-main .chat-console-hang');
    const node = li.querySelector(':scope > .chat-step-node');
    const stateEl = head && head.querySelector('.chat-step-state');
    const caret = head && head.querySelector(':scope > .chat-step-caret');
    const nrect = node && node.getBoundingClientRect();
    const svg = node && node.querySelector('svg');
    return {
      step: li.dataset.step || '', tool: li.dataset.tool || '', exec: li.dataset.execution || '',
      state: li.dataset.state || '', cls: li.className,
      visible: li.getClientRects().length > 0,
      inBundle: Boolean(li.closest('.chat-console-bundle')),
      call: flat(li.querySelector('.chat-console-call')),
      liveCall: Boolean(li.querySelector('.chat-console-call.is-live')),
      title: stateEl ? stateEl.getAttribute('title') || '' : null,
      stateText: flat(stateEl),
      result: flat(hang && hang.querySelector('.chat-console-hang-body > .chat-console-result-text')),
      headText: flat(head),
      took: [...li.querySelectorAll('.chat-console-took')].map((el) => el.textContent.trim()),
      tickClocks: li.querySelectorAll('[data-cc-clock]').length,
      toggles: head ? [...head.querySelectorAll('[data-step-toggle]')].map((el) => el.getAttribute('aria-expanded')) : [],
      caretCls: caret ? caret.className : null,
      caretAria: caret ? caret.getAttribute('aria-expanded') : null,
      caretShown: Boolean(caret) && caret.getClientRects().length > 0,
      hangCls: hang ? hang.className : '',
      // Every note hang the row itself owns (direct children of the head
      // wrapper — a group's member hangs live deeper, inside the bundle).
      notes: [...li.querySelectorAll(':scope > .chat-step-main > .chat-console-hang')].map(flat),
      expanded: li.classList.contains('is-console-expanded'),
      bodyText: flat(hang),
      bodyAll: all(hang),
      more: [...li.querySelectorAll('.chat-console-more')].map((el) => ({ text: el.textContent.trim(), visible: el.getClientRects().length > 0 })),
      trunc: [...li.querySelectorAll('.chat-tool-truncated')].map((el) => ({ text: el.textContent.trim(), visible: el.getClientRects().length > 0 })),
      node: nrect ? { w: Math.round(nrect.width * 10) / 10, h: Math.round(nrect.height * 10) / 10, svg: Boolean(svg), svgW: svg ? Number(svg.getAttribute('width')) : 0, svgH: svg ? Number(svg.getAttribute('height')) : 0 } : null,
      imgs: li.querySelectorAll('img').length,
      right: Math.round((li.getBoundingClientRect().right) * 10) / 10,
    };
  });
};

(async () => {
  const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'], ...(process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {}) });
  const failures = [];
  const check = (cond, message) => { if (!cond) failures.push(message); };
  let assertions = 0;
  const ok = (cond, message) => { check(cond, message); assertions += 1; };
  try {
    const page = await browser.newPage({ reducedMotion: 'reduce', viewport: { width: 1440, height: 1000 } });
    const errors = [];
    page.on('pageerror', (error) => errors.push(error.message));
    await page.route('**/*', (route) => {
      if (new URL(route.request().url()).origin !== origin) return route.abort();
      return route.continue();
    });
    await page.goto(origin);
    await page.locator('[data-sync-label]').filter({ hasText: 'Live sync' }).waitFor({ state: 'attached' });
    await page.waitForFunction(() => Boolean(window.fuseToolFixture));
    await page.addStyleTag({ content: '* { animation: none !important; transition: none !important; }' });
    await page.evaluate(() => { fuseToolFixture.thinking(true); });
    const raf = () => page.evaluate(() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r))));
    const rows = () => page.evaluate(COLLECT);

    // Mount one tool as the newest call of a live block: its row prints on its
    // own whether or not the family can fold, so the same row anatomy is
    // testable for every tool.
    const mountOne = (t, { live = true, extra = [], patch = null } = {}) => page.evaluate(({ trace, related, extra, live, patch }) => {
      const tools = [...(related ? [related] : []), ...extra, trace];
      if (patch) Object.assign(trace, patch);
      fuseToolFixture.mountThread([
        { role: 'user', content: 'Go.' },
        { role: 'assistant', model: 'qa/model', elapsedMs: 30000, content: '', tools,
          segments: tools.map((tool) => ({ type: 'tool', id: tool.id })) },
      ], { live, phase: live ? 'model' : '' });
    }, { trace: traceOf(t), related: t.related || null, extra, live, patch });
    const rowFor = (list, t) => list.find((r) => r.step === `tool-${idOf(t)}`);

    // —— 1. The matrix: settled/default, user-expanded, user-collapsed ——
    const settled = {};
    for (const t of TOOLS) {
      await mountOne(t);
      await raf();
      const before = await rows();
      const r0 = rowFor(before, t);
      if (ROW_FREE.has(t.name)) {
        ok(!r0, `${t.name}: prints a transcript row (${JSON.stringify(r0 && r0.headText)})`);
        continue;
      }
      ok(Boolean(r0 && r0.visible), `${t.name}: no visible row`);
      if (!r0) continue;
      settled[t.name] = r0;
      // (a) settled/default: final status, default disclosure state.
      ok(r0.call.startsWith(t.verb), `${t.name}: call reads "${t.verb}(…)", got "${r0.call}"`);
      // Bare-verb rows carry their subject on the result line instead.
      const said = t.bare ? r0.headText + ' ' + r0.result : r0.call;
      ok(said.includes(t.subject), `${t.name}: row names "${t.subject}", got "${said}"`);
      ok(r0.title === 'Done', `${t.name}: settled title is "${r0.title}"`);
      ok(r0.state === 'done', `${t.name}: settled data-state is "${r0.state}"`);
      // Console hides the caret on every call row (chat-design.css:3150
      // display:none !important — deliberate), so the disclosure contract is
      // the label toggle itself; rows without one show all they have.
      ok(r0.caretShown === false, `${t.name}: a chevron slipped into Console (${r0.caretCls})`);
      ok(r0.toggles.length === (t.disclosure ? 2 : 0), `${t.name}: disclosure anatomy ${JSON.stringify(r0.toggles)} for disclosure=${t.disclosure}`);
      ok(r0.toggles.every((a) => a === 'false'), `${t.name}: arrives collapsed (${JSON.stringify(r0.toggles)})`);
      if (t.disclosure) ok(!r0.expanded, `${t.name}: arrives collapsed (is-console-expanded)`);
      else ok(r0.caretCls === 'chat-step-caret is-empty' && r0.caretAria === null, `${t.name}: no caret for a row with nothing to open (${r0.caretCls})`);

      // (b) user-expanded / (c) user-collapsed, on rows that disclose.
      if (t.disclosure) {
        const clicked = await page.evaluate((step) => {
          const li = document.querySelector(`[data-step="${step}"]`);
          const btn = li && li.querySelector(':scope > .chat-step-main > .chat-step-head [data-step-toggle]');
          if (btn) btn.click();
          return Boolean(btn);
        }, r0.step);
        if (!clicked) { ok(false, `${t.name}: expected a head toggle to expand`); continue; }
        await raf();
        const open = rowFor(await rows(), t);
        ok(open.expanded && open.toggles.every((a) => a === 'true'), `${t.name}: expand sets every head toggle and the row (${JSON.stringify(open.toggles)})`);
        ok(open.hangCls.includes('is-expanded'), `${t.name}: expand opens the hang`);
        // The folded body carries its "… +N lines" fold counter — a visible
        // disclosure label, not withheld content; expanding swaps it for the
        // hidden lines themselves. Compare body content, not the counter.
        const foldedBody = r0.bodyText.replace(/… \+[\d,]+ (lines?|more|tool uses?)\s*/g, '');
        ok(open.bodyText.length >= foldedBody.length, `${t.name}: expansion loses content`);
        ok(open.bodyText.length > foldedBody.length, `${t.name}: expansion shows nothing new (${open.bodyText.length} vs ${foldedBody.length})`);
        // Disclosure direction is uniform because Console shows no chevron on
        // any call row (chat-design.css:3150); the caret button is kept in
        // sync with the label toggle either way.
        ok(open.caretAria === 'true' && open.caretShown === r0.caretShown, `${t.name}: the hidden caret left sync (${open.caretAria})`);
        await page.evaluate((step) => {
          const li = document.querySelector(`[data-step="${step}"]`);
          const btn = li && li.querySelector(':scope > .chat-step-main > .chat-step-head [data-step-toggle]');
          if (btn) btn.click();
        }, r0.step);
        await raf();
        const shut = rowFor(await rows(), t);
        ok(!shut.expanded && shut.toggles.every((a) => a === 'false') && !shut.hangCls.includes('is-expanded'),
          `${t.name}: collapse restores the default (${JSON.stringify(shut.toggles)} ${shut.hangCls})`);
        ok(shut.bodyText === r0.bodyText, `${t.name}: collapse/expand changed the folded body`);
        ok(shut.caretAria === 'false', `${t.name}: the caret does not return (${shut.caretAria})`);
      }
    }

    // —— 2. Cross-family uniformity over the settled matrix ——
    const callRows = Object.values(settled);
    const slots = new Set(callRows.map((r) => `${r.node.w}x${r.node.h}`));
    ok(slots.size === 1, `icon slot dimensions differ between families: ${[...slots].join(', ')}`);
    ok(callRows.every((r) => r.node.svg && r.node.svgW >= 13 && r.node.svgW <= 14 && r.node.svgH === r.node.svgW),
      `every call row mounts a square glyph in the slot: ${callRows.filter((r) => !r.node.svg).map((r) => r.tool).join(', ')}`);
    ok(callRows.every((r) => r.title === 'Done' && r.state === 'done' && r.stateText === ''),
      'equivalent outcomes share one status word and casing (Done / data-state done / empty visible state text)');
    // One duration slot per row: the head clock for clock families, the result
    // line for the families whose result already says how long.
    for (const t of TOOLS) {
      const r = settled[t.name];
      if (!r) continue;
      ok(r.took.length <= 1 && r.tickClocks <= 1, `${t.name}: more than one clock on the row (${JSON.stringify(r.took)} +${r.tickClocks} ticking)`);
      if (!t.clock) {
        ok(r.took.length === 0, `${t.name}: this family states its duration in the result or has none (clock slot empty; got ${JSON.stringify(r.took)})`);
      } else {
        ok(r.took.length === 1 && r.took[0] === '· 43s', `${t.name}: duration lives in the header clock (got ${JSON.stringify(r.took)})`);
        ok(!r.result.includes('43s'), `${t.name}: duration duplicated into the result line ("${r.result}")`);
      }
      if (RESULT_DUR[t.name]) {
        ok(r.result.includes(RESULT_DUR[t.name]), `${t.name}: result line states the duration ("${r.result}")`);
        ok(!r.headText.includes(RESULT_DUR[t.name]), `${t.name}: duration duplicated into the header`);
      }
    }
    // Disclosure anatomy and chevron direction are one contract for all rows:
    // call rows uniformly hide the caret and rotate nothing (the
    // chevron rules in app.css:4713 target other rows' carets).
    ok(callRows.every((r) => r.caretShown === false), `a Console call row grew a visible chevron: ${callRows.filter((r) => r.caretShown).map((r) => r.tool).join(', ')}`);
    // Hidden content always comes with a disclosure; rows without one show all they have.
    for (const t of TOOLS) {
      const r = settled[t.name];
      if (!r) continue;
      // The Await report rides its row for Open agent (pinned by
      // console_subagent_rows.browser.cjs), not behind a disclosure.
      if (t.name === 'get_subagent_output' || t.name === 'spawn_subagent') continue;
      ok((r.bodyAll.length > r.bodyText.length) === t.disclosure, `${t.name}: hidden body without a disclosure (visible ${r.bodyText.length}, all ${r.bodyAll.length}, disclosure=${t.disclosure})`);
    }
    // Truncation: a fold counter or a truncation note marks everything the row
    // withholds; permanent loss always carries counts.
    for (const t of TOOLS) {
      const r = settled[t.name];
      if (!r) continue;
      for (const marker of r.more) {
        ok(/^… \+[\d,]+ (lines?|more|tool uses?)$/.test(marker.text), `${t.name}: fold counter shape: "${marker.text}"`);
        ok(t.disclosure, `${t.name}: a fold counter on a row that opens nothing`);
      }
      for (const note of r.trunc) {
        ok(/truncated|retained/i.test(note.text), `${t.name}: truncation note without counts: "${note.text}"`);
        ok(/\d/.test(note.text), `${t.name}: truncation note without sizes: "${note.text}"`);
      }
    }
    // The skill row: semantics only (its one-line condensation is pinned by
    // tests/tool_edge_cases.browser.cjs). The loaded outcome now rides the
    // call line, so both texts count.
    const skill = settled.load_skill;
    ok(/ponytail/.test(skill.headText + skill.bodyAll) && /loaded/i.test(skill.headText + skill.bodyAll) && skill.title === 'Done',
      `load_skill semantics (name + loaded + outcome): "${skill.headText}" / "${skill.bodyAll}"`);

    // —— 3. Outcome vocabulary: done vs error vs interrupted vs cancelled/lost ——
    const vocab = await page.evaluate(async ({ TOOLS, ROOT }) => {
      const idOf = (t) => `t-${t.name}`;
      const raf = () => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));
      const flat = (el) => (el ? el.innerText.replace(/\s+/g, ' ').trim() : '');
      const out = [];
      for (const t of TOOLS) {
        if (t.name === 'todo_write' || t.name === 'search_tools') continue;
        for (const end of [
          // Outcomes come with a coherent execution state: for a child-backed
          // call the execution record governs the presentation (toolFailed
          // returns false while the execution reads completed).
          { key: 'error', patch: { status: 'error', error: 'boom from the fixture', state: 'failed', execution_status: 'failed' } },
          { key: 'interrupted', patch: { status: 'interrupted', error: '', state: 'interrupted', execution_status: 'interrupted', termination_confirmed: true } },
          { key: 'cancelled', patch: { status: 'cancelled', error: 'cancelled by the user', state: 'cancelled', execution_status: 'cancelled', termination_confirmed: true } },
          { key: 'lost', patch: { status: 'interrupted', error: '', state: 'lost', execution_status: 'lost' } },
        ]) {
          const trace = { ...ROOT, ...t.trace, id: idOf(t), name: t.name === 'read_file-image' ? 'read_file' : t.name, status: 'running' };
          fuseToolFixture.mountThread([
            { role: 'user', content: 'Go.' },
            { role: 'assistant', model: 'qa/model', elapsedMs: 30000, content: '', tools: [trace], segments: [{ type: 'tool', id: trace.id }] },
          ], { live: true, phase: 'model' });
          fuseToolFixture.update(trace.id, end.patch, true);
          await raf();
          const li = document.querySelector(`[data-step="tool-${trace.id}"]`);
          const hang = li.querySelector('.chat-console-hang');
          out.push({
            name: t.name, key: end.key,
            state: li.dataset.state,
            cls: li.className,
            title: li.querySelector('.chat-step-state').getAttribute('title'),
            body: flat(hang),
            bodyAll: hang.textContent.replace(/\s+/g, ' ').trim(),
            result: flat(hang.querySelector('.chat-console-hang-body > .chat-console-result-text')),
          });
        }
      }
      return out;
    }, { TOOLS, ROOT });
    const byName = (name, key) => vocab.find((v) => v.name === name && v.key === key);
    // The hang branch character ("└") prefixes every body; strip it.
    const say = (v) => v.body.replace(/^└\s*/, '');
    // One lost/cancelled vocabulary, with the two documented exceptions: a
    // kill receipt never owns an execution (chat.js executionState: "A kill
    // receipt never owns an execution"), and a Wait/Await result belongs to
    // the call itself (chat.js toolPresentation returnedWait).
    const LOST_TITLE = { kill_command: 'Stopped', get_command_output: 'Interrupted', get_subagent_output: 'Stopped' };
    for (const v of vocab) {
      if (v.key === 'error') {
        ok(v.state === 'error' && v.title === 'Failed', `${v.name}: a failure reads "${v.title}"/${v.state}`);
        // One failure vocabulary across families: the red "Error: " fold.
        ok(say(v).startsWith('Error: boom from the fixture'), `${v.name}: failure reads "Error: …", got "${say(v)}"`);
      }
      if (v.key === 'interrupted') {
        ok(v.state === 'interrupted' && v.title === 'Stopped', `${v.name}: confirmed stop reads "${v.title}"/${v.state}`);
        ok(say(v).startsWith('Interrupted · '), `${v.name}: interrupted reason prefix, got "${say(v)}"`);
      }
      if (v.key === 'cancelled') {
        ok(v.state === 'interrupted' && v.title === 'Stopped', `${v.name}: cancelled (confirmed) reads "${v.title}"/${v.state}`);
      }
      if (v.key === 'lost') {
        const want = LOST_TITLE[v.name] || 'Lost';
        ok(v.state === 'interrupted' && v.title === want, `${v.name}: a lost result reads "${v.title}"/${v.state}, expected "${want}"`);
        if (want === 'Lost') ok(say(v).includes('the result was lost'), `${v.name}: lost reason, got "${say(v)}"`);
      }
    }
    // The fuse-steer family fails exactly like the rest, in the same casing.
    ok(byName('fuse-steer', 'error').body.includes('Error: boom'), 'fuse-steer failure folds like the rest');

    // —— 4. Running affordance and settle ——
    const running = await page.evaluate(async ({ TOOLS, ROOT }) => {
      const idOf = (t) => `t-${t.name}`;
      const raf = () => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));
      const flat = (el) => (el ? el.innerText.replace(/\s+/g, ' ').trim() : '');
      const out = [];
      for (const t of TOOLS) {
        if (t.name === 'todo_write' || t.name === 'search_tools') continue;
        const trace = { ...ROOT, ...t.trace, id: idOf(t), name: t.name === 'read_file-image' ? 'read_file' : t.name,
          status: 'running', state: 'running', execution_status: 'running', exit_code: undefined, call_duration_ms: undefined };
        fuseToolFixture.mountThread([
          { role: 'user', content: 'Go.' },
          { role: 'assistant', model: 'qa/model', elapsedMs: 30000, content: '', tools: [trace], segments: [{ type: 'tool', id: trace.id }] },
        ], { live: true, phase: 'tools' });
        await raf();
        const li = document.querySelector(`[data-step="tool-${trace.id}"]`);
        const hang = li.querySelector('.chat-console-hang');
        const live = {
          name: t.name,
          state: li.dataset.state,
          cls: li.className,
          hangCls: hang ? hang.className : '',
          liveCall: Boolean(li.querySelector('.chat-console-call.is-live')),
          tick: li.querySelectorAll('[data-cc-clock]').length,
          staticTook: li.querySelectorAll('.chat-console-took:not([data-cc-clock])').length,
          title: li.querySelector('.chat-step-state').getAttribute('title'),
          body: flat(hang),
        };
        // Settle it in place: nothing may keep claiming to run.
        fuseToolFixture.update(trace.id, { status: 'done', state: 'completed', execution_status: 'completed', call_duration_ms: 43000 }, true);
        await raf();
        const li2 = document.querySelector(`[data-step="tool-${trace.id}"]`);
        const hang2 = li2.querySelector('.chat-console-hang');
        out.push({ ...live, after: {
          state: li2.dataset.state,
          cls: li2.className,
          hangCls: hang2 ? hang2.className : '',
          liveCall: Boolean(li2.querySelector('.chat-console-call.is-live')),
          tick: li2.querySelectorAll('[data-cc-clock]').length,
          title: li2.querySelector('.chat-step-state').getAttribute('title'),
        } });
      }
      return out;
    }, { TOOLS, ROOT });
    for (const r of running) {
      const t = TOOLS.find((x) => x.name === r.name);
      ok(r.state === 'running' && r.cls.includes('is-running'), `${r.name}: running row pulses (is-running), got ${r.cls}`);
      ok(r.hangCls.includes('is-running') || r.liveCall, `${r.name}: running affordance (live line or hang), got call=${r.liveCall} hang=${r.hangCls}`);
      ok(r.title === 'Running' || r.title === 'Waiting' || r.title === 'Queued' || r.title === 'Background',
        `${r.name}: running vocabulary "${r.title}"`);
      ok(r.staticTook === 0, `${r.name}: a ticking clock must not freeze into a duration while running`);
      ok(r.tick === (t && (t.clock || t.ticking) ? 1 : 0), `${r.name}: one ticking clock iff the family carries one (got ${r.tick})`);
      ok(r.after.cls.includes('is-done') && !r.after.cls.includes('is-running') && !r.after.hangCls.includes('is-running'),
        `${r.name}: settle leaves no running affordance (${r.after.cls})`);
      ok(r.after.title === 'Done' && r.after.state === 'done', `${r.name}: settle rewrites the label to Done (${r.after.title})`);
      ok(r.after.tick === 0 && !r.after.liveCall, `${r.name}: settle stops the clock and the live line (${r.after.tick})`);
    }

    // —— 5. GROUPED: summary lines and their members ——
    const groupMount = (tools, { live = false, tail = true } = {}) => page.evaluate(({ tools, live, tail }) => {
      fuseToolFixture.mountThread([
        { role: 'user', content: 'Go.' },
        { role: 'assistant', model: 'qa/model', elapsedMs: 30000, ...(live ? {} : { endedAt: '2026-10-03T20:00:00Z' }), content: tail ? 'Done.' : '',
          tools, segments: [...tools.map((tool) => ({ type: 'tool', id: tool.id })), ...(tail ? [{ type: 'content', text: 'Done.' }] : [])] },
      ], { live, phase: live ? 'model' : '' });
    }, { tools, live, tail });
    const groupRows = async () => (await rows()).filter((r) => r.visible);
    const T = (id, extra) => ({ ...ROOT, id, ...extra });
    const summaryLabels = (list) => list.filter((r) => r.cls.includes('is-console-group') && !r.cls.includes('is-console-agents')).map((r) => r.headText);

    // 5a. A mixed-family run: the header's verb/count text matches its members.
    await groupMount([
      T('g-read-a', { name: 'read_file', status: 'done', path: '/srv/qa/app/a.py', output: '1: a' }),
      T('g-read-b', { name: 'read_file', status: 'done', path: '/srv/qa/app/b.py', output: '1: b' }),
      T('g-grep', { name: 'grep', status: 'done', pattern: 'retries', path: '/srv/qa', output: 'app/a.py:12:retries = 3\n' }),
      T('g-glob', { name: 'glob', status: 'done', pattern: '**/*.ts', path: '/srv/qa', output: 'src/a.ts\n' }),
      T('g-list', { name: 'list_dir', status: 'done', path: '/srv/qa/tests', output: 'a.py\nb.py\nc.py\n' }),
      T('g-shell', { name: 'run_terminal_command', status: 'done', state: 'completed', execution_status: 'completed', command: 'npm test', command_id: 'gj', child_id: 'gj', job_id: 'gj', exit_code: 0, output: 'ok\n', termination_confirmed: true }),
    ]);
    await raf();
    let g = await groupRows();
    ok(JSON.stringify(summaryLabels(g)) === JSON.stringify(['Searched for 2 patterns, read 2 files, listed 1 directory, ran 1 shell command']),
      `mixed summary counts: ${JSON.stringify(summaryLabels(g))}`);
    ok(g.filter((r) => r.step.startsWith('tool-')).length === 0, `foldable calls print their own rows before the summary opens: ${g.map((r) => r.step).join(', ')}`);
    // Open: every member prints once, in order, with its own label and state.
    const opened = await page.evaluate(() => {
      const btn = document.querySelector('.is-console-group > .chat-step-main > .chat-step-head > .chat-step-toggle');
      if (btn) btn.click();
      return Boolean(btn);
    });
    ok(opened, 'the mixed run folds into an openable summary line');
    await raf();
    g = await groupRows();
    const members = g.filter((r) => r.inBundle);
    ok(members.length === 6, `the opened summary prints each member once (${members.length})`);
    ok(JSON.stringify(members.map((r) => r.tool)) === JSON.stringify(['read_file', 'read_file', 'grep', 'glob', 'list_dir', 'run_terminal_command']),
      `members keep their order and names: ${members.map((r) => r.tool).join(', ')}`);
    ok(members.every((r) => r.title === 'Done'), 'every member of a settled summary reads Done');
    ok(members.every((r) => r.toggles.length === 0), 'members of an opened summary print whole (no disclosure of their own)');
    ok(!members.some((r) => r.cls.includes('is-running')), 'a summary never folds a running call');
    // The group header counts and the members never duplicate each other.
    const groupHead = g.find((r) => r.cls.includes('is-console-group'));
    ok(groupHead.headText === 'Searched for 2 patterns, read 2 files, listed 1 directory, ran 1 shell command', 'opening keeps the summary text');
    ok(!groupHead.headText.includes('Read('), 'the summary header does not repeat member labels');
    // A click on any printed member folds the summary back — uniform for every
    // member family (read rows and shell rows alike).
    for (const step of ['tool-g-read-a', 'tool-g-shell']) {
      const openBefore = (await groupRows()).find((r) => r.cls.includes('is-console-group')).expanded;
      ok(openBefore, 'summary is open before the click');
      await page.evaluate((s) => {
        const li = document.querySelector(`[data-step="${s}"]`);
        const call = li && li.querySelector('.chat-console-call');
        if (!call) return;
        const box = li.getBoundingClientRect();
        call.dispatchEvent(new MouseEvent('click', { bubbles: true, clientX: box.x + 8, clientY: box.y + box.height / 2 }));
      }, step);
      await raf();
      const now = (await groupRows()).find((r) => r.cls.includes('is-console-group'));
      ok(!now.expanded, `${step}: clicking a printed member folds its summary`);
      await page.evaluate(() => {
        const btn = document.querySelector('.is-console-group > .chat-step-main > .chat-step-head > .chat-step-toggle');
        if (btn) btn.click();
      });
      await raf();
    }

    // 5b. A non-foldable call breaks the line; it never joins the counts.
    await groupMount([
      T('b-read-a', { name: 'read_file', status: 'done', path: '/srv/qa/app/a.py', output: '1: a' }),
      T('b-edit', { name: 'search_replace', status: 'done', path: '/srv/qa/app/a.py', preview: '@@ -1,1 +1,1 @@\n-a\n+b', output: 'updated' }),
      T('b-web', { name: 'web_search', status: 'done', query: 'x', latency_ms: 840, results: HITS }),
      T('b-read-b', { name: 'read_file', status: 'done', path: '/srv/qa/app/b.py', output: '1: b' }),
    ]);
    await raf();
    g = await groupRows();
    ok(JSON.stringify(summaryLabels(g)) === JSON.stringify(['Read 1 file', 'Read 1 file']),
      `every non-foldable call ends the line: ${JSON.stringify(summaryLabels(g))}`);
    const standalones = g.filter((r) => r.step.startsWith('tool-') && !r.inBundle);
    ok(JSON.stringify(standalones.map((r) => r.tool)) === JSON.stringify(['search_replace', 'web_search']),
      `edit and web stand on their own rows: ${standalones.map((r) => r.tool).join(', ')}`);
    ok(standalones.every((r) => !r.inBundle && !r.cls.includes('is-console-group')), 'standalone rows sit outside every bundle');

    // 5c. Members that end badly.
    await groupMount([
      T('e-read-ok', { name: 'read_file', status: 'done', path: '/srv/qa/app/a.py', output: '1: a' }),
      T('e-read-bad', { name: 'read_file', status: 'error', path: '/root/secrets/id_rsa', error: 'permission denied' }),
      T('e-shell-cut', { name: 'run_terminal_command', status: 'interrupted', error: '', termination_confirmed: true, command: 'npm test', command_id: 'ej', child_id: 'ej', job_id: 'ej' }),
    ]);
    await raf();
    g = await groupRows();
    const failGroup = g.find((r) => r.cls.includes('is-console-group'));
    // Both members that ended badly surface on the summary line itself, each
    // as the note its own row prints: "Interrupted · …" and "Error: …".
    ok(failGroup.notes.some((n) => n.includes('Interrupted · the run ended before it finished')),
      `an interrupted member surfaces on the summary: ${JSON.stringify(failGroup.notes)}`);
    // …and a failing member too, mirroring the interrupted note: same
    // "Error: …" line the member's own row prints.
    ok(failGroup.headText === 'Read 2 files, ran 1 shell command', `the summary keeps its counts: "${failGroup.headText}"`);
    ok(failGroup.notes.some((n) => n.replace(/^└\s*/, '').startsWith('Error: permission denied')),
      `a failing member surfaces on the folded summary: ${JSON.stringify(failGroup.notes)}`);
    await page.evaluate(() => {
      const btn = document.querySelector('.is-console-group > .chat-step-main > .chat-step-head > .chat-step-toggle');
      if (btn) btn.click();
    });
    await raf();
    g = await groupRows();
    const bad = g.filter((r) => r.inBundle);
    ok(JSON.stringify(bad.map((r) => r.state)) === JSON.stringify(['done', 'error', 'interrupted']),
      `member states surface on open: ${bad.map((r) => r.state).join(', ')}`);
    ok(bad[1].bodyText.replace(/^└\s*/, '').startsWith('Error: permission denied'), `the failure opens inside the line: "${bad[1].bodyText}"`);
    ok(bad[2].bodyText.replace(/^└\s*/, '').startsWith('Interrupted · '), `the cut member says why: "${bad[2].bodyText}"`);
    ok(bad[1].hangCls.includes('is-error') && bad[2].cls.includes('is-interrupted'), 'member marks differ per outcome');

    // 5d. Counters against members. The noun counts distinct files, so two
    // reads of one path are one file.
    await groupMount([
      T('d-read-1', { name: 'read_file', status: 'done', path: '/srv/qa/app/same.py', output: '1: a' }),
      T('d-read-2', { name: 'read_file', status: 'done', path: '/srv/qa/app/same.py', output: '1: b' }),
    ]);
    await raf();
    g = await groupRows();
    ok(JSON.stringify(summaryLabels(g)) === JSON.stringify(['Read 1 file']), `counts name files, not calls: ${JSON.stringify(summaryLabels(g))}`);

    // 5e. A path read plus a shell read: two read-family members, two files —
    // both sides count (ccGroupHtml sums path reads and shell reads).
    await groupMount([
      T('m-read', { name: 'read_file', status: 'done', path: '/srv/qa/README.md', output: '1: readme' }),
      T('m-cat', { name: 'run_terminal_command', status: 'done', state: 'completed', execution_status: 'completed', command: 'cat log', command_id: 'mj', child_id: 'mj', job_id: 'mj', exit_code: 0, output: 'log body\n', termination_confirmed: true }),
    ]);
    await raf();
    g = await groupRows();
    ok(JSON.stringify(summaryLabels(g)) === JSON.stringify(['Read 2 files']), `path read + shell read both count: ${JSON.stringify(summaryLabels(g))}`);

    // 5f. Running calls never fold: the running affordance stays on its own row.
    await groupMount([
      T('r-read', { name: 'read_file', status: 'done', path: '/srv/qa/app/a.py', output: '1: a' }),
      T('r-shell', { name: 'run_terminal_command', status: 'running', state: 'running', execution_status: 'running', command: 'npm test', command_id: 'rj', child_id: 'rj', job_id: 'rj' }),
    ], { live: true, tail: true });
    await raf();
    g = await groupRows();
    const runningShell = g.find((r) => r.step === 'tool-r-shell');
    ok(runningShell && !runningShell.inBundle, `a running call stands on its own row (${runningShell && runningShell.cls})`);
    ok(runningShell.state === 'running' && (runningShell.hangCls.includes('is-running') || runningShell.liveCall), 'the running row carries the running affordance');
    const foldedRead = g.find((r) => r.inBundle);
    ok(foldedRead === undefined || foldedRead.step === 'tool-r-read', 'the settled read folded into the line above');

    // —— 6. Monitor events and wake notices share the row anatomy ——
    await page.evaluate(({ ROOT }) => {
      const monitor = { ...ROOT, id: 'n-monitor', name: 'monitor', status: 'done', description: 'errors in worker.log', command: 'tail -F app.log', command_id: 'nm', child_id: 'nm', job_id: 'nm', event_count: 1, timeout_ms: 300000 };
      fuseToolFixture.mountThread([
        { role: 'user', content: 'Watch.' },
        { role: 'assistant', model: 'qa/model', elapsedMs: 3000, endedAt: '2026-10-03T20:00:00Z', content: '', tools: [monitor], segments: [{ type: 'tool', id: 'n-monitor' }] },
        { role: 'user', auto: true, content: '<monitor_event id="nm" seq="1">\nhost: qa-box command: tail -F app.log\ndescription: errors in worker.log\n\nERROR one\nERROR two\n</monitor_event>' },
      ], { live: false });
    }, { ROOT });
    await raf();
    const notices = (await rows()).filter((r) => r.cls.includes('is-console-notice') && r.visible);
    ok(notices.length === 1, `monitor events print as notice rows (${notices.length})`);
    if (notices[0]) {
      ok(notices[0].tool === '' && notices[0].state === '' && notices[0].node && !notices[0].node.svg,
        'a notice is not a tool call: no tool name, no state slot, no glyph in the icon slot');
      ok(notices[0].title === null || notices[0].title === '', 'a notice carries no status word');
    }

    // —— 7. Layout: every row type again at 390, plus 1440 ——
    for (const width of [1440, 390]) {
      await page.setViewportSize({ width, height: width === 390 ? 844 : 1000 });
      const slots = new Set();
      for (const t of TOOLS) {
        await mountOne(t);
        await raf();
        const layout = await page.evaluate(() => ({
          overflow: document.scrollingElement.scrollWidth - window.innerWidth,
          rows: [...document.querySelectorAll('.chat-log li.is-console-call')].filter((el) => el.getClientRects().length).map((el) => {
            const r = el.querySelector(':scope > .chat-step-node').getBoundingClientRect();
            return { step: el.dataset.step, right: el.getBoundingClientRect().right, slot: `${Math.round(r.width * 10) / 10}x${Math.round(r.height * 10) / 10}` };
          }),
        }));
        ok(layout.overflow <= 0, `@${width}/${t.name}: page overflows by ${layout.overflow}px`);
        for (const row of layout.rows) {
          ok(row.right <= width + 1, `@${width}/${t.name}: row ${row.step} runs ${Math.round(row.right - width)}px past the viewport`);
          slots.add(row.slot);
        }
      }
      ok(slots.size === 1, `@${width}: icon slots disagree across families: ${[...slots].join(', ')}`);
    }

    assert.deepEqual(errors, [], `page errors: ${errors.join('; ')}`);
  } finally {
    await browser.close();
  }
  const unique = [...new Set(failures)];
  if (unique.length) {
    console.error(unique.slice(0, 200).join('\n'));
    if (unique.length > 200) console.error(`… and ${unique.length - 200} more`);
  }
  assert.equal(unique.length, 0, `${unique.length} tool uniformity problems`);
  console.log(`PASS tool uniformity: ${TOOLS.length} tool row types × settled/collapsed/expanded/grouped, ${assertions} assertions`);
})().catch((error) => { console.error(error); process.exit(1); });
