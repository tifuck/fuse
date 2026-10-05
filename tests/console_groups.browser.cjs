// Console reading-mode GROUPS: the summary lines ("Read 2 files", "Ran 1
// shell command", "Committed 1a2b3c4…") a run folds into, and the collapsed
// state those groups remember.
//
// Already pinned elsewhere — not repeated here: row states, dots, clocks and
// the generated audit rules (console_ui.browser.cjs + console_ui_lib.js),
// read/search fold labels and their growth (console_file_tools.browser.cjs),
// group open/fold clicks and detail refresh (stream_consistency.browser.cjs,
// collapse_states.browser.cjs), an expanded summary surviving a view switch
// and a remount (console_reconcile.browser.cjs), settled agents-group child
// labels (console_subagent_rows.browser.cjs), per-kind header geometry
// (console_layout.browser.cjs). This file pins only: summary counts and verbs
// against the group's real membership as calls arrive, mixed transcripts
// keeping row order (nothing swallowed into the wrong group), a group's
// collapsed state across a page reload, a console↔mainframe switch and new
// output, truncation indicators on folded command output, and group rows at
// 390px.
// Run against chat_browser_fixture.py, never the live Fuse service.
const assert = require('node:assert/strict');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const origin = process.env.FUSE_BROWSER_ORIGIN || 'http://127.0.0.1:8099';
assert.match(origin, /^http:\/\/127\.0\.0\.1:\d+$/);

const ROOT = { host: 'qa-box', cwd: '/srv/qa', platform: 'posix', project_root: '/srv/qa' };
let seq = 0;
const tool = (name, fields = {}) => ({
  id: `g${++seq}`, name, status: 'done', error: '', results: [], output: '',
  duration_ms: 120, call_duration_ms: 3200, ...ROOT, ...fields,
});
const sh = (command, fields = {}) => {
  const job = fields.job || `job${++seq}`;
  return tool('run_terminal_command', {
    command, child_id: job, command_id: job, job_id: job, timeout_ms: 120000,
    termination_confirmed: true, state: 'completed', execution_status: 'completed', exit_code: 0,
    elapsed_ms: fields.ms ?? 2400, execution_duration_ms: fields.ms ?? 2400,
    output: fields.output || '', preview: (fields.output || '').slice(0, 120), ...fields.extra,
    ...(fields.id ? { id: fields.id } : {}),
  });
};
const T = (trace) => ({ type: 'tool', id: trace.id });
const say = (text) => ({ type: 'content', text });
const think = (text, ms) => ({ type: 'reasoning', text, ms });
const turn = (segments, tools, extra = {}) => ({
  role: 'assistant', model: 'qa/model', elapsedMs: 30000, tools, segments,
  content: segments.filter((s) => s.type === 'content').map((s) => s.text).join(''),
  reasoning: segments.filter((s) => s.type === 'reasoning').map((s) => s.text).join(''), ...extra,
});
const user = (content) => ({ role: 'user', content, reasoning: '', error: '' });
const ended = { endedAt: '2026-10-03T20:00:00Z' };

// Transcript shape, in DOM order: markdown blocks, summary lines (with their
// bundle membership), standalone call rows, and the agents block's children.
// textContent, not innerText: membership must be readable whether the bundle
// is open or not.
const readGroups = () => {
  const norm = (el) => (el ? el.textContent.replace(/\s+/g, ' ').trim() : '');
  const log = document.querySelector('.chat-log');
  const tops = [];
  for (const article of log.querySelectorAll('.chat-turn.is-assistant')) {
    for (const block of article.querySelectorAll('.chat-flow > .chat-md, .chat-flow > .chat-activity')) {
      if (block.classList.contains('chat-md')) { tops.push({ kind: 'md', text: norm(block).slice(0, 40) }); continue; }
    for (const li of block.querySelectorAll(':scope > .chat-activity-panel > .chat-activity-panel-inner > ol.chat-timeline > li.chat-step')) {
      if (li.classList.contains('is-console-group')) {
        tops.push({
          kind: li.classList.contains('is-console-agents') ? 'agents' : 'group',
          step: li.dataset.step || '',
          label: norm(li.querySelector(':scope > .chat-step-main > .chat-step-head .chat-step-label')),
          expanded: li.classList.contains('is-console-expanded'),
          members: [...li.querySelectorAll(':scope > .chat-step-main > .chat-console-bundle .chat-timeline > li')]
            .map((m) => m.dataset.step || `think:${m.dataset.thought}`),
          children: [...li.querySelectorAll('.chat-console-agent')].map(norm),
        });
      } else {
        tops.push({ kind: 'row', step: li.dataset.step || '', tool: li.dataset.tool || '', text: norm(li).slice(0, 60) });
      }
    }
    }
  }
  const counts = {};
  log.querySelectorAll('li.chat-step').forEach((li) => { const s = li.dataset.step; if (s) counts[s] = (counts[s] || 0) + 1; });
  return {
    tops,
    groups: tops.filter((t) => t.kind === 'group'),
    agents: tops.filter((t) => t.kind === 'agents'),
    rows: tops.filter((t) => t.kind === 'row'),
    dupes: Object.entries(counts).filter(([, n]) => n > 1),
    overflow: document.scrollingElement.scrollWidth - window.innerWidth,
    placeholders: /\bundefined\b|\bNaN\b|\[object Object\]/.test(log.textContent),
  };
};

// 390px grid probe inside one open group: unbroken identifiers must scroll
// sideways as one line box, headers must not spill the log or the page.
const groupGrid = () => {
  const log = document.querySelector('.chat-log');
  const group = log.querySelector('li.is-console-group.is-console-expanded');
  const out = { overflow: document.scrollingElement.scrollWidth - window.innerWidth, missing: !group, wrapped: [], spills: [], scrollers: [] };
  if (!group) return out;
  const logRight = log.getBoundingClientRect().right;
  const label = group.querySelector(':scope > .chat-step-main > .chat-step-head .chat-step-label');
  if (label && label.getBoundingClientRect().right > logRight + 1) out.spills.push(`group label: ${label.textContent.slice(0, 30)}`);
  for (const li of group.querySelectorAll('.chat-console-bundle li.is-console-call')) {
    const lab = li.querySelector('.chat-step-label');
    if (lab && lab.getBoundingClientRect().right > logRight + 1) out.spills.push(`${li.dataset.step} header`);
  }
  for (const box of group.querySelectorAll('.chat-console-bundle .chat-console-out, .chat-console-bundle .chat-console-lines')) {
    if (box.getClientRects().length && box.scrollWidth > box.clientWidth + 1) out.scrollers.push(box.className);
  }
  for (const line of group.querySelectorAll('.chat-console-bundle .chat-console-stdout')) {
    const cs = getComputedStyle(line);
    const lh = parseFloat(cs.lineHeight) || parseFloat(cs.fontSize) * 1.5;
    const h = line.getBoundingClientRect().height;
    if (h > lh * 1.75) out.wrapped.push(`${Math.round(h)}px vs ${lh.toFixed(1)}px: ${line.textContent.slice(0, 30)}`);
  }
  for (const body of group.querySelectorAll('.chat-console-bundle .chat-console-nbody')) {
    if (body.getClientRects().length > 1) out.wrapped.push(`nbody ${body.getClientRects().length} boxes: ${body.textContent.slice(0, 30)}`);
  }
  return out;
};

(async () => {
  const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'], ...(process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {}) });
  const failures = [];
  const check = (value, message) => { if (!value) failures.push(message); };
  const frame = () => page.evaluate(() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r))));
  const snap = async () => { await frame(); return page.evaluate(readGroups); };
  const pass = (name, from) => {
    const fresh = failures.slice(from);
    if (fresh.length) console.error(`FAIL ${name}\n  ${fresh.slice(0, 5).join('\n  ')}`);
    else console.log(`PASS ${name}`);
  };
  let page;
  try {
    page = await browser.newPage({ reducedMotion: 'reduce', viewport: { width: 1440, height: 1000 } });
    const errors = [];
    page.on('pageerror', (error) => errors.push(error.message));
    await page.route('**/*', (route) => new URL(route.request().url()).origin === origin ? route.continue() : route.abort());
    await page.goto(origin);
    await page.waitForFunction(() => window.fuseToolFixture);
    await page.addStyleTag({ content: '* { animation: none !important; transition: none !important; }' });
    const setup = (view) => page.evaluate((v) => { fuseToolFixture.thinking(true); fuseToolFixture.view(v); }, view || 'console');
    await setup('console');
    const mount = (rows, live, threadId) => page.evaluate(({ rows, live, threadId }) => fuseToolFixture.mount(rows, live, threadId ?? null), { rows, live, threadId });
    const groupToggle = () => page.locator('.chat-log li.is-console-group:not(.is-console-agents) > .chat-step-main > .chat-step-head > .chat-step-toggle').first();
    const groupState = () => page.locator('.chat-log li.is-console-group:not(.is-console-agents)').first()
      .evaluate((el) => ({ open: el.classList.contains('is-console-expanded'), aria: el.querySelector(':scope > .chat-step-main > .chat-step-head > .chat-step-toggle')?.getAttribute('aria-expanded') }));

    // —— 1. The summary line counts and names what its group holds ——
    {
      const from = failures.length;
      const commit = sh('git commit -m "fix retries"', { output: '[main 1a2b3c4] fix retries\n 1 file changed\n' });
      const push = sh('git push origin dev', { output: '1a2b3c4..def5678  dev -> dev\n' });
      const build = sh('npm run build', { output: 'built ok\n' });
      const test = sh('npm test', { output: '2 passed\n' });
      await mount([user('Go.'), turn([say('Working.\n\n'), T(commit), T(push), T(build), T(test), say('Done.')], [commit, push, build, test], ended)], false);
      let s = await snap();
      check(s.groups.length === 1, `one summary line for four folded calls, saw ${s.groups.length}`);
      const label = s.groups[0]?.label || '';
      check(label.includes('Committed 1a2b3c4'), `commit verb and sha in "${label}"`);
      check(label.includes('pushed to dev'), `push verb and ref in "${label}"`);
      check(label.includes('ran 2 shell commands'), `shell count excludes git work in "${label}"`);
      check(!/ran 4 shell commands/.test(label), `git work must not count as shell commands in "${label}"`);
      await groupToggle().click();
      s = await snap();
      check(s.groups[0].expanded, 'the summary opens');
      check(s.groups[0].members.length === 4, `the label covers 4 members, saw ${s.groups[0].members.length}`);
      check(s.groups[0].members.join(',') === [commit, push, build, test].map((t) => `tool-${t.id}`).join(','),
        `members keep call order, saw ${s.groups[0].members.join(',')}`);
      check(!s.dupes.length && !s.placeholders && s.overflow <= 0, `transcript audit: ${JSON.stringify(s.dupes)} overflow ${s.overflow}`);
      pass('summary counts and verbs match the group contents', from);
    }

    // —— 2. A late call joins: the line counts it; the newest call folds in
    //      only once something newer arrives ——
    {
      const from = failures.length;
      const readA = tool('read_file', { id: 'late-a', path: '/srv/qa/app/alpha.py', output: '1: x\n' });
      const readB = tool('read_file', { id: 'late-b', path: '/srv/qa/app/beta.py', output: '1: y\n' });
      const lateShell = sh('make all', { output: 'built\n', extra: { id: 'late-shell' } });
      await mount([user('Go.'), turn([T(readA), T(readB)], [readA, readB])], true);
      let s = await snap();
      check(s.groups.length === 1 && s.groups[0].label === 'Read 1 file', `while live, one settled read folds: ${s.groups.map((g) => g.label).join('|')}`);
      check(s.groups[0].members.join() === 'tool-late-a', `the group holds only the older read: ${s.groups[0].members.join()}`);
      check(s.rows.some((r) => r.step === 'tool-late-b'), 'the newest call stands on its own row while its block is live');
      await page.evaluate((e) => fuseToolFixture.toolEvent(e), { event: 'tool', ...lateShell });
      s = await snap();
      check(s.groups[0].label === 'Read 2 files', `the late call pushed the newest read into the line: "${s.groups[0].label}"`);
      check(s.groups[0].members.join() === 'tool-late-a,tool-late-b', `members grew with it: ${s.groups[0].members.join()}`);
      check(s.rows.some((r) => r.step === 'tool-late-shell'), 'the newest call (the shell) stands alone now');
      await page.evaluate(({ content, segments }) => fuseToolFixture.stream(1, { content, segments }), {
        content: 'Next.\n\n',
        segments: [T(readA), T(readB), T(lateShell), say('Next.\n\n')],
      });
      s = await snap();
      check(s.groups[0].label === 'Read 2 files, ran 1 shell command', `narration folds the newest call in and the label counts it: "${s.groups[0].label}"`);
      check(s.groups[0].members.join() === 'tool-late-a,tool-late-b,tool-late-shell', `all three are members now: ${s.groups[0].members.join()}`);
      check(!s.rows.some((r) => r.step === 'tool-late-shell'), 'the folded call left no standalone row');
      check(s.dupes.length === 0 && s.overflow <= 0, `audit: ${JSON.stringify(s.dupes)} overflow ${s.overflow}`);
      pass('a late call joins the group and the label counts it', from);
    }

    // —— 3. Mixed transcript: groups, a lone call, prose and a thought keep
    //      their order; nothing joins the wrong group ——
    {
      const from = failures.length;
      const a = tool('read_file', { id: 'mix-a', path: '/srv/qa/app/a.py', output: '1: x\n' });
      const b = tool('read_file', { id: 'mix-b', path: '/srv/qa/app/b.py', output: '1: y\n' });
      const edit = tool('search_replace', { id: 'mix-edit', path: '/srv/qa/app/a.py', preview: '@@ -1,2 +1,2 @@\n-old\n+new\n', output: 'updated' });
      const cmd = sh('npm test', { id: 'mix-cmd', output: '2 passed\n' });
      const d = tool('read_file', { id: 'mix-d', path: '/srv/qa/app/d.py', output: '1: z\n' });
      const rows = [turn([
        T(a), T(b), say('Prose one.\n\n'), T(edit), T(cmd), say('Prose two.\n\n'), T(d), think('Wrapping up.', 12000), say('Done.'),
      ], [a, b, edit, cmd, d], ended)];
      await mount(rows, false);
      const s = await snap();
      const sig = s.tops.map((t) => (t.kind === 'md' ? `md:${t.text}` : t.kind === 'group' ? `group:${t.label}` : `${t.kind}:${t.step}`));
      check(JSON.stringify(sig) === JSON.stringify([
        'group:Read 2 files', 'md:Prose one.', 'row:tool-mix-edit', 'group:Ran 1 shell command',
        'md:Prose two.', 'group:Read 1 file', 'group:Thought for 12s', 'md:Done.',
      ]), `rows keep their order:\n  saw  ${JSON.stringify(sig)}`);
      check(s.groups[0].members.join() === 'tool-mix-a,tool-mix-b', `the first group holds exactly its two reads: ${s.groups[0].members.join()}`);
      check(s.groups[2].members.join() === 'tool-mix-d', `the read after the prose is its own line, not swallowed above: ${s.groups[2].members.join()}`);
      check(s.groups[3].members.length === 1 && !s.groups[3].members.some((m) => m.startsWith('tool-')), 'the thought line holds only the thought');
      check(s.rows.filter((r) => r.step === 'tool-mix-edit').length === 1, 'the lone edit is one standalone row');
      check(!s.dupes.length && !s.placeholders && s.overflow <= 0, `audit: ${JSON.stringify(s.dupes)} overflow ${s.overflow}`);
      pass('mixed transcript keeps order; lone calls and thoughts stay out of the wrong group', from);
    }

    // —— 4. A group the user collapsed stays collapsed across a page reload
    //      and a reading-mode switch; one the user opened comes back open ——
    {
      const from = failures.length;
      const persistRows = () => [user('Go.'), turn([say('Working.\n\n'),
        T(tool('read_file', { id: 'p1', path: '/srv/qa/app/alpha.py', output: '1: x\n' })),
        T(tool('read_file', { id: 'p2', path: '/srv/qa/app/beta.py', output: '1: y\n' })),
        T(tool('read_file', { id: 'p3', path: '/srv/qa/app/gamma.py', output: '1: z\n' })),
        say('Done.')], [
        { id: 'p1', name: 'read_file', status: 'done', path: '/srv/qa/app/alpha.py', output: '1: x\n', results: [], error: '', ...ROOT },
        { id: 'p2', name: 'read_file', status: 'done', path: '/srv/qa/app/beta.py', output: '1: y\n', results: [], error: '', ...ROOT },
        { id: 'p3', name: 'read_file', status: 'done', path: '/srv/qa/app/gamma.py', output: '1: z\n', results: [], error: '', ...ROOT },
      ], ended)];
      const remount = async (threadId) => { await mount(persistRows(), false, threadId); await frame(); };
      await remount('groups-thread');
      check((await groupState()).aria === 'false', 'the summary starts collapsed');
      await groupToggle().click();
      check((await groupState()).open, 'opening the summary');
      // The user folds it back with a click on its printed calls.
      await page.locator('.chat-log li.is-console-group .chat-console-bundle .chat-step-node').first().click({ force: true });
      check(!(await groupState()).open, 'a click on the printed calls folds the summary');
      await page.reload();
      await page.waitForFunction(() => window.fuseToolFixture);
      await setup('console');
      await remount('groups-thread');
      check(!(await groupState()).open && (await groupState()).aria === 'false', 'reload keeps an explicitly closed summary closed');
      await groupToggle().click();
      check((await groupState()).open, 'opening before the next reload');
      await page.reload();
      await page.waitForFunction(() => window.fuseToolFixture);
      await setup('console');
      await remount('groups-thread');
      const reopened = await groupState();
      check(reopened.open && reopened.aria === 'true', `reload restores the open summary (got ${JSON.stringify(reopened)})`);
      await page.locator('.chat-log li.is-console-group .chat-console-bundle .chat-step-node').first().click({ force: true });
      check(!(await groupState()).open, 'folding it again before the view switch');
      for (const view of ['mainframe', 'console']) {
        await page.evaluate((v) => fuseToolFixture.view(v), view);
        await page.waitForFunction((v) => document.querySelector('[data-chat]')?.getAttribute('data-view') === v, view);
        const state = await groupState();
        check(!state.open && state.aria === 'false', `${view} keeps the collapsed summary collapsed (got ${JSON.stringify(state)})`);
      }
      await remount('other-groups-thread');
      check(!(await groupState()).open, 'another thread does not inherit the disclosure');
      pass('collapsed state survives reload and a console↔mainframe switch', from);
    }

    // —— 5. New output never re-opens or re-closes a group ——
    // A trailing narration keeps the calls' block off the live slot, so every
    // call stays a member while output streams (the live hold-back itself is
    // pinned in scenario 2).
    {
      const from = failures.length;
      const tools = [
        { id: 's1', name: 'read_file', status: 'done', path: '/srv/qa/app/one.py', output: '1: x\n', results: [], error: '', ...ROOT },
        { id: 's2', name: 'read_file', status: 'done', path: '/srv/qa/app/two.py', output: '1: y\n', results: [], error: '', ...ROOT },
      ];
      const rows = [user('Go.'), turn([...tools.map(T), say('Done.')], tools, ended)];
      await mount(rows, false, 'groups-stream');
      await frame();
      await groupToggle().click();
      check((await groupState()).open, 'the summary opens');
      await page.evaluate(() => fuseToolFixture.update('s1', { output: '1: x\n2: x2\n3: STREAMED_ONE\n' }, true));
      let s = await snap();
      check(s.groups[0].expanded, 'output landing in an open summary leaves it open');
      check(s.groups[0].members.length === 2 && !s.dupes.length, `the update added no rows: ${s.groups[0].members.join()}`);
      await page.locator('.chat-log li.is-console-group .chat-console-bundle .chat-step-node').first().click({ force: true });
      check(!(await groupState()).open, 'folding it before more output arrives');
      await page.evaluate(() => fuseToolFixture.update('s2', { output: '1: y\n2: STREAMED_TWO\n' }, true));
      s = await snap();
      check(!s.groups[0].expanded && (await groupState()).aria === 'false', 'more output does not re-open a collapsed summary');
      // A late call joins the same line: the label counts it even while the
      // summary is folded, and the fold survives.
      const s3 = tool('read_file', { id: 's3', path: '/srv/qa/app/three.py', output: '1: z\n' });
      await page.evaluate(({ content, segments, tools }) => fuseToolFixture.stream(1, { content, segments, tools }), {
        content: 'Done.', tools: [...tools, s3],
        segments: [...tools.map(T), T(s3), say('Done.')],
      });
      s = await snap();
      check(s.groups[0].label === 'Read 3 files', `a late call updates the label even while collapsed: "${s.groups[0].label}"`);
      check(!s.groups[0].expanded && (await groupState()).aria === 'false', 'a late call does not re-open a collapsed summary');
      check(s.groups[0].members.join() === 'tool-s1,tool-s2,tool-s3', `the late call joined the group: ${s.groups[0].members.join()}`);
      await groupToggle().click();
      await page.evaluate(() => fuseToolFixture.update('s2', { output: '1: y\n2: STREAMED_THREE\n' }, true));
      s = await snap();
      check(s.groups[0].expanded, 'more output keeps an open summary open');
      check(s.groups[0].members.length === 3 && !s.dupes.length, `no row duplicated across the updates: ${s.groups[0].members.join()}`);
      const text = await page.locator('.chat-log[data-chat-log]').innerText();
      check(text.includes('STREAMED_THREE') && !text.includes('STREAMED_TWO'), 'the open member shows the newest output only');
      check(s.overflow <= 0, `overflow ${s.overflow}`);
      pass('new output never re-opens or re-closes a group', from);
    }

    // —— 5b. A member held out when the user folds re-opens the group on its
    //       way back in ——
    {
      const from = failures.length;
      const tools = [
        { id: 'f1', name: 'read_file', status: 'done', path: '/srv/qa/app/one.py', output: '1: x\n', results: [], error: '', ...ROOT },
        { id: 'f2', name: 'read_file', status: 'done', path: '/srv/qa/app/two.py', output: '1: y\n', results: [], error: '', ...ROOT },
      ];
      await mount([user('Go.'), turn(tools.map(T), tools, ended)], false, 'groups-reopen');
      await frame();
      await groupToggle().click();
      check((await groupState()).open, 'the summary opens with both calls inside');
      // Streaming makes the newest call stand alone (scenario 2), so the
      // user folds the summary while only one call is still inside it.
      await page.evaluate(() => fuseToolFixture.update('f1', { output: '1: x\n2: x2\n' }, true));
      let s = await snap();
      check(s.groups[0].members.join() === 'tool-f1', `the live block holds its newest call out: ${s.groups[0].members.join()}`);
      await page.locator('.chat-log li.is-console-group .chat-console-bundle .chat-step-node').first().click({ force: true });
      check(!(await groupState()).open, 'the user folds the summary');
      // The line settles again and the held-out call rejoins it.
      await page.evaluate(({ segments }) => fuseToolFixture.stream(1, { content: 'Done.', segments }), {
        segments: [...tools.map(T), say('Done.')],
      });
      s = await snap();
      check(s.groups[0].members.join() === 'tool-f1,tool-f2', `the held-out call rejoins the line: ${s.groups[0].members.join()}`);
      // FINDING(group-reopen): intended the user's fold survives the member's
      // return (the group must stay collapsed), actually the group silently
      // re-opens: collapseConsoleGroup clears stepOpen only for the members
      // currently in the bundle (static/chat.js:10008), and ccGroupHtml reads
      // "open" as any member key being true (static/chat.js:12118), so the
      // held-out member's stale true re-opens the summary when it rejoins.
      check(s.groups[0].expanded === true, `FINDING group-reopen: a member held out at fold time re-opens the group (got expanded=${s.groups[0].expanded})`);
      pass('FINDING group-reopen: held-out member re-opens a folded summary', from);
    }

    // —— 6. Folded command output says it was cut; expanding loses no end ——
    {
      const from = failures.length;
      const out = ['FIRST_LINE', ...Array.from({ length: 8 }, (_, i) => `out ${i + 2}`), 'LAST_LINE'].join('\n') + '\n';
      const a = tool('read_file', { id: 'cut-a', path: '/srv/qa/app/a.py', output: '1: x\n' });
      const cmd = sh('npm run build', { id: 'cut-cmd', output: out, ms: 4200 });
      await mount([user('Go.'), turn([T(a), T(cmd)], [a, cmd])], true);
      await frame();
      const row = page.locator('[data-step="tool-cut-cmd"]');
      const folded = await row.innerText();
      check(folded.includes('FIRST_LINE'), 'the fold shows the first lines');
      check(!folded.includes('LAST_LINE'), 'the fold hides the tail');
      const more = row.locator('.chat-console-more');
      check(await more.count() === 1, 'the fold carries a "… +N lines" marker');
      check(((await more.textContent()) || '').trim() === '… +7 lines', `the marker counts the hidden lines, saw "${await more.textContent()}"`);
      // FINDING(fold-marker-hidden): intended the folded row says how much
      // output it hides ("… +7 lines" visible on the hang, as the monitor
      // event's sibling marker is), actually the marker is CSS-hidden while
      // the fold is closed (static/chat-design.css:2690) and the expanded
      // state hides the whole preview (static/chat-design.css:3183), so the
      // hidden-line count is never visible. The caret is the only affordance.
      check(await more.isVisible() === false, 'FINDING fold-marker-hidden: the hidden-line count never shows on the folded row');
      await row.locator(':scope > .chat-step-main > .chat-step-head > .chat-step-toggle').click();
      await frame();
      const openText = await row.innerText();
      check(openText.includes('FIRST_LINE') && openText.includes('LAST_LINE'), 'expanding keeps both ends of the output');
      check(openText.split('FIRST_LINE').length - 1 === 1 && openText.split('LAST_LINE').length - 1 === 1, 'neither end is duplicated');
      check(await row.locator('pre.chat-console-out.is-stdout .chat-console-stdout').count() === 10, 'the expanded body prints every line');

      // A command the server truncated says so on its folded row, and says
      // which part it kept when only a tail survived. Each is the newest call
      // of its live block, so it prints on its own folded row.
      const trunc = sh('npm run report', { id: 'cut-trunc', output: 'kept one\nkept two\nLAST_KEPT\n', extra: { truncated: true, output_chars: 5000 } });
      await mount([user('Go.'), turn([T(a), T(trunc)], [a, trunc])], true);
      await frame();
      const truncRow = await page.locator('[data-step="tool-cut-trunc"]').innerText();
      check(/Output truncated · [\d,]+ of 5,000 characters retained\./.test(truncRow),
        `the folded row says the output was truncated, saw "${truncRow}"`);
      check(truncRow.includes('kept one') && truncRow.includes('LAST_KEPT'), 'the retained output keeps both of its ends');
      const tail = sh('npm run tail', { id: 'cut-tail', output: 'tail one\nLAST_TAIL\n', extra: { output_mode: 'tail', output_start: 4000, output_chars: 5000 } });
      await mount([user('Go.'), turn([T(a), T(tail)], [a, tail])], true);
      await frame();
      const tailRow = await page.locator('[data-step="tool-cut-tail"]').innerText();
      check(/Retained tail · [\d,]+ of 5,000 characters · starts at offset 4,000\./.test(tailRow),
        `the folded tail says where it starts, saw "${tailRow}"`);
      check(tailRow.includes('LAST_TAIL'), 'the tail keeps its last line');
      pass('folded command output says it was cut; expanding loses no end', from);
    }

    // —— 7. Group rows at 390px: identifiers scroll sideways, never wrap or
    //      break the grid ——
    {
      const from = failures.length;
      await page.setViewportSize({ width: 390, height: 844 });
      const token = 'dispatcher_backoff_and_retries_' + 'x'.repeat(120);
      const read = tool('read_file', { id: 'wide-read', path: `/srv/qa/integration/${token}/module.py`, output: `1: const TOKEN = ${token};\n2: done\n` });
      const cmd = sh('npm run build', { id: 'wide-cmd', output: `start\nconst value = ${token};\nend\n` });
      await mount([user('Go.'), turn([say('Working.\n\n'), T(read), T(cmd), say('Done.')], [read, cmd], ended)], false);
      await frame();
      await groupToggle().click();
      await frame();
      const grid = await page.evaluate(groupGrid);
      check(!grid.missing, 'the summary is open for the probe');
      check(grid.overflow <= 0, `390px page overflow ${grid.overflow}px`);
      check(grid.spills.length === 0, `group rows spill the log: ${JSON.stringify(grid.spills)}`);
      check(grid.wrapped.length === 0, `long identifiers wrapped instead of scrolling: ${JSON.stringify(grid.wrapped)}`);
      check(grid.scrollers.length >= 1, `the wide output does not scroll sideways: ${JSON.stringify(grid.scrollers)}`);
      pass('group rows at 390px: identifiers scroll, headers keep the grid', from);
    }

    // —— 8. Agents group: its head counts its children and names their type ——
    {
      const from = failures.length;
      const spawn = (id, child, type, description) => ({
        id, name: 'spawn_subagent', status: 'running', error: '', results: [], description,
        preview: description, subagent_type: type, child_id: child, agent_tool_uses: 2,
      });
      const one = spawn('ag-a', 'child-a', 'Explore', 'Audit the retry ladder');
      const two = spawn('ag-b', 'child-b', 'Explore', 'Check the transport timeouts');
      await mount([user('Go.'), turn([T(one), T(two)], [one, two])], true);
      await frame();
      let s = await snap();
      check(s.agents.length === 1, `one agents block, saw ${s.agents.length}`);
      check(s.agents[0].label === 'Running 2 Explore agents…', `typed head while they run: "${s.agents[0].label}"`);
      check(s.agents[0].children.length === 2, `two children under the head, saw ${s.agents[0].children.length}`);
      await page.evaluate(() => fuseToolFixture.update('ag-a', { status: 'done', agent_tool_uses: 4, agent_tokens: 2100, agent_work_ms: 21000 }, true));
      s = await snap();
      check(s.agents[0].label === 'Running 2 Explore agents…', `the head stays live until the last child ends: "${s.agents[0].label}"`);
      await page.evaluate(() => fuseToolFixture.update('ag-b', { status: 'done', agent_tool_uses: 3, agent_tokens: 900, agent_work_ms: 8000 }, true));
      s = await snap();
      check(s.agents[0].label === '2 Explore agents finished', `settled typed head: "${s.agents[0].label}"`);
      check(s.agents[0].children.length === 2, `settled children stay listed: ${s.agents[0].children.join(' | ')}`);
      check(s.agents[0].children.every((c) => /tool uses/.test(c)), `settled children keep their counts: ${s.agents[0].children.join(' | ')}`);
      // Mixed types: the head drops the type, each child names its own.
      const mixedA = spawn('ag-c', 'child-c', 'Explore', 'Audit the retries');
      const mixedB = spawn('ag-d', 'child-d', 'general-purpose', 'Ship the docs');
      await mount([user('Go.'), turn([T(mixedA), T(mixedB)], [{ ...mixedA, status: 'done', agent_tool_uses: 1, agent_work_ms: 4000 }, { ...mixedB, status: 'done', agent_tool_uses: 2, agent_work_ms: 5000 }], ended)], false);
      s = await snap();
      check(s.agents[0].label === '2 agents finished', `mixed-type head: "${s.agents[0].label}"`);
      check(s.agents[0].children.some((c) => c.includes('Explore')) && s.agents[0].children.some((c) => c.includes('Agent')),
        `each child names its type: ${s.agents[0].children.join(' | ')}`);
      check(!s.dupes.length && s.overflow <= 0, `audit: ${JSON.stringify(s.dupes)} overflow ${s.overflow}`);
      pass('agents group head counts its children and names their type', from);
    }

    if (errors.length) failures.push(...errors.map((e) => `page error: ${e}`));
    const unique = [...new Set(failures)];
    if (unique.length) console.error(unique.slice(0, 40).join('\n'));
    assert.equal(unique.length, 0, `${unique.length} console group problems`);
  } finally {
    if (page) await page.close().catch(() => {});
    await browser.close();
  }
})().catch((error) => { console.error(error); process.exit(1); });
