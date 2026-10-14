// Text and thinking interleaved with tool calls, over a
// whole live turn (the suites named in the brief pin tool rows, follow, pill,
// chunk boundaries and settled thinking on their own):
//  1. A turn driven phase by phase (model → calling → tools → model → done):
//     the live status line's verb, token arrows and elapsed clock per phase,
//     the folded "Thinking for/Thought for Ns" line's placement and duration,
//     the running call's own row and ticking clock, all reconciled by the
//     persisted message at done — same text blocks, same rows, same order —
//     ending in the "✻ <verb> for <dur> · done <time>" foot.
//  2. Prose blocks between tool rows: ragged chunk splits across a tool-state
//     boundary never duplicate or drop text, prose gaps stay symmetric, and
//     long unbreakable prose does not overflow at 390px.
//  3. The hidden-thinking switch mid-turn, live and at the reconcile.
//  4. Stopping/stopped and failed endings for the status line and the foot.
//  5. The ✻ line's other values: pending agents, still-running shells, and
//     no close at all without elapsedMs.
const assert = require('node:assert/strict');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const origin = process.env.FUSE_BROWSER_ORIGIN || 'http://127.0.0.1:8099';
assert.match(origin, /^http:\/\/127\.0\.0\.1:\d+$/);

const ROOT = { host: 'qa-box', cwd: '/srv/qa', platform: 'posix', project_root: '/srv/qa' };
let seq = 0;
const tool = (name, fields = {}) => ({ id: `tt${++seq}`, name, status: 'done', error: '', results: [], output: '', duration_ms: 120, call_duration_ms: 120, ...ROOT, ...fields });
const think = (text, ms) => ({ type: 'reasoning', text, ...(ms != null ? { ms } : {}) });
const say = (text) => ({ type: 'content', text });
const T = (t) => ({ type: 'tool', id: t.id });
// ccTurnVerb: the closing line's word is fixed per message id (chat.js).
const CC_TURN_VERBS = ['Baked', 'Brewed', 'Churned', 'Cogitated', 'Cooked', 'Crunched', 'Sautéed', 'Worked'];
const turnVerb = (id) => {
  let hash = 0;
  for (let i = 0; i < id.length; i += 1) hash = (Math.imul(hash, 31) + id.charCodeAt(i)) | 0;
  return CC_TURN_VERBS[(hash >>> 0) % CC_TURN_VERBS.length];
};

// Serialized into the page: one snapshot of everything this suite asserts on.
function snap() {
  const art = document.querySelector('.chat-log > article:last-child');
  const dock = document.querySelector('[data-live-dock]');
  const label = document.querySelector('[data-live-dock-label]');
  const flow = art && art.querySelector('[data-flow]');
  const top = (li) => !li.closest('.chat-console-bundle');
  const norm = (t) => String(t || '').replace(/\s+/g, ' ').trim();
  return {
    dock: dock && label ? {
      hidden: dock.hidden,
      phase: dock.dataset.phase || '',
      verb: norm(label.querySelector('.chat-status-verb')?.textContent),
      meta: norm(label.querySelector('.chat-status-meta')?.textContent),
      glyph: norm(dock.querySelector('.chat-console-spin')?.textContent),
    } : null,
    foot: norm(art && art.querySelector('[data-foot]')?.innerText),
    blocks: flow ? [...flow.children].map((el) => el.classList.contains('chat-md')
      ? { md: norm(el.innerText) }
      : { act: [...el.querySelectorAll('li.chat-step')].filter(top).map((li) => li.dataset.step || li.className) }) : [],
    steps: art ? [...art.querySelectorAll('li.chat-step[data-step]')].map((li) => li.dataset.step) : [],
    labels: art ? [...art.querySelectorAll('li.chat-step')].filter(top).map((li) => norm(li.querySelector('.chat-step-label')?.innerText || li.innerText).slice(0, 120)) : [],
    rows: art ? [...art.querySelectorAll('li.chat-step')].filter(top).map((li) => norm(li.innerText).slice(0, 200)) : [],
    clocks: art ? [...art.querySelectorAll('[data-cc-clock]')].map((el) => `${el.dataset.ccClock}:${norm(el.textContent)}`) : [],
    prose: art ? [...art.querySelectorAll('[data-flow] > .chat-md')].map((el) => norm(el.innerText)) : [],
  };
}

(async () => {
  const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'], ...(process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {}) });
  const failures = [];
  const check = (cond, message) => { if (!cond) failures.push(message); };
  const errors = [];
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, reducedMotion: 'reduce' });
    page.on('pageerror', (error) => errors.push(error.message));
    await page.route('**/*', (route) => new URL(route.request().url()).origin === origin ? route.continue() : route.abort());
    await page.goto(origin);
    await page.waitForFunction(() => window.fuseToolFixture);
    await page.evaluate((src) => { window.__tt = { snap: eval(`(${src})`) }; }, snap.toString());
    const snapNow = () => page.evaluate(() => window.__tt.snap());
    // mount a live turn; every later step patches the whole message, as the
    // server's checkpoints do, and moves the run's activity phase.
    const start = (id, elapsedMs) => page.evaluate(({ id, elapsedMs }) => {
      const f = fuseToolFixture;
      f.thinking(true);
      f.todo.agents([]);
      f.mountThread([
        { role: 'user', content: 'Go.', reasoning: '', error: '' },
        { id, role: 'assistant', model: 'qa/model', content: '', reasoning: '', tools: [], segments: [], elapsedMs },
      ], { live: true, phase: 'model' });
    }, { id, elapsedMs });
    const drive = (patch, phase) => page.evaluate(({ patch, phase }) => {
      const f = fuseToolFixture;
      f.stream(1, patch);
      if (phase) f.toolEvent({ event: 'activity', ...phase });
    }, { patch, phase });
    const finish = (msg) => page.evaluate((msg) => {
      const f = fuseToolFixture;
      f.run({ status: 'completed' });
      f.progress(1, { message: msg });
    }, msg);

    // —— 1. Console: a whole turn, phase by phase. ——
    await start('tt-turn-a', 9000);
    const read = tool('read_file', { path: '/srv/qa/app/config.py', status: 'running', elapsed_ms: 3000 });
    const grep = tool('grep', { pattern: 'retries', path: '/srv/qa', status: 'running', elapsed_ms: 1500 });
    const readDone = { ...read, status: 'done', output: '1: retries = 3\n', call_duration_ms: 4100, elapsed_ms: undefined };
    const grepDone = { ...grep, status: 'done', output: 'app/x.py:1:retries = 3\n', call_duration_ms: 2600, elapsed_ms: undefined };
    const persisted = {
      id: 'tt-turn-a', role: 'assistant', model: 'qa/model', elapsedMs: 133000, endedAt: '2026-10-05T09:42:00Z',
      content: 'Read it.\n\nRetries are now 5.', reasoning: 'Plan the retry audit.Now check the tests.',
      usage: { prompt_tokens: 1200, completion_tokens: 80 }, tools: [readDone, grepDone],
      segments: [think('Plan the retry audit.', 6505), T(readDone), say('Read it.\n\n'), think('Now check the tests.', 4200), T(grepDone), say('Retries are now 5.')],
    };

    // model, thinking: own ticking line; dock shows the elapsed clock, down
    // tokens and "thinking"; the spin glyph is the still frame.
    await drive({ reasoning: 'Plan the retry audit.', segments: [think('Plan the retry audit.')] });
    let s = await snapNow();
    check(s.dock && !s.dock.hidden, `dock shown while thinking: ${JSON.stringify(s.dock)}`);
    check(/^\S+…$/.test(s.dock.verb), `spinner verb carries the ellipsis: ${s.dock.verb}`);
    check(s.dock.meta.includes('· thinking') && s.dock.meta.includes('↓ '), `thinking status names thinking and down tokens: ${s.dock.meta}`);
    check(s.dock.glyph === '✻', `reduced-motion spin glyph is the still frame: ${s.dock.glyph}`);
    check(s.labels.join('|') === 'Thinking for 1s…', `a streaming thought is its own ticking line: ${s.labels}`);
    const verb = s.dock.verb;
    await page.waitForFunction(() => window.__tt.snap().clocks.some((c) => /^think:[2-9]s$/.test(c)), null, { timeout: 4500 });
    s = await snapNow();
    check(Number((s.dock.meta.match(/\((\d+)s/) || [])[1]) >= 10, `the dock's elapsed clock ticks: ${s.dock.meta}`);

    // calling: the thought lands with the server's duration, clock gone.
    await drive({ segments: [think('Plan the retry audit.', 6505)] }, { phase: 'calling' });
    s = await snapNow();
    check(s.labels.join('|') === 'Thought for 6s', `a landed thought reads "Thought for" with its own duration, not the page's tick: ${s.labels}`);
    check(s.clocks.length === 0, `the landed thought keeps no live clock: ${s.clocks}`);
    // The dock leaves "thinking" the moment a call starts streaming, and
    // names the tool once the stream has, before its arguments finish.
    check(s.dock.phase === 'calling' && s.dock.meta.includes('· calling tool') && !/thinking|thought for/.test(s.dock.meta),
      `an unnamed call being written reads "calling tool": ${JSON.stringify(s.dock)}`);
    await drive({}, { phase: 'calling', tool: 'web_search' });
    s = await snapNow();
    check(s.dock.meta.includes('· searching the web') && s.dock.meta.includes('↓ '), `a search call being written reads "searching the web" with down tokens: ${s.dock.meta}`);
    await drive({}, { phase: 'calling', tool: 'read_file' });
    s = await snapNow();
    check(s.dock.meta.endsWith('· reading)'), `the dock follows the newest call's name: ${s.dock.meta}`);

    // tools: the call stands alone, ticking; dock flips to up tokens.
    await drive({ tools: [read], segments: [think('Plan the retry audit.', 6505), T(read)] }, { phase: 'tools', tool_id: read.id });
    s = await snapNow();
    check(s.labels.length === 2 && /^Reading app\/config\.py(?: \S+)? · 3s/.test(s.labels[1]), `a running call is its own row with its clock: ${s.labels}`);
    check(s.dock.phase === 'working' && s.dock.meta.includes('↑ '), `tools phase flips the dock to up tokens: ${s.dock.meta}`);
    await page.waitForFunction(() => /reading/.test(window.__tt.snap().dock.meta), null, { timeout: 5000 });
    await page.waitForFunction(() => /^Reading app\/config\.py(?: \S+)? · [4-9]s/.test(window.__tt.snap().labels[1] || ''), null, { timeout: 5000 });

    // model again: the call folds behind its thought, prose follows.
    await drive({ content: 'Read it.\n\n', tools: [readDone], segments: [think('Plan the retry audit.', 6505), T(readDone), say('Read it.\n\n')] }, { phase: 'model' });
    s = await snapNow();
    check(s.labels.join('|') === 'Thought for 6s, read 1 file', `the ended call folds into the thought's line: ${s.labels}`);
    check(s.prose.join('||') === 'Read it.', `prose between tool rows lands in its own block: ${s.prose}`);
    check(s.dock.phase === 'writing' && s.dock.meta.includes('↓ '), `writing phase shows down tokens: ${s.dock.meta}`);
    await page.waitForFunction(() => /^\(\d+s · ↓ [\d.]+[kM]? tokens\)$/.test(window.__tt.snap().dock.meta), null, { timeout: 5000 });

    // a second thought between calls: the line above stays settled.
    await drive({ segments: [think('Plan the retry audit.', 6505), T(readDone), say('Read it.\n\n'), think('Now check the tests.')] });
    s = await snapNow();
    check(s.labels.join('|') === 'Thought for 6s, read 1 file|Thinking for 1s…', `a second thought gets a new live line below the prose: ${s.labels}`);
    check(s.blocks.map((b) => b.md ? 'md' : 'act').join(',') === 'act,md,act', `blocks stay ordered around a mid-turn thought: ${JSON.stringify(s.blocks)}`);
    await page.waitForFunction(() => /think:[2-9]s/.test(window.__tt.snap().clocks.join(',') || ''), null, { timeout: 4500 });

    // it lands, its own call runs and ends, the answer streams.
    await drive({ tools: [readDone, grep], segments: [think('Plan the retry audit.', 6505), T(readDone), say('Read it.\n\n'), think('Now check the tests.', 4200), T(grep)] }, { phase: 'tools', tool_id: grep.id });
    s = await snapNow();
    check(s.labels[0] === 'Thought for 6s, read 1 file' && s.labels[1] === 'Thought for 4s' && /^Searching for retries/.test(s.labels[2] || ''), `each thought lands on its own line, the running call stands alone: ${s.labels}`);
    check(s.steps.filter((k) => k === `tool-${read.id}`).length === 1 && s.steps.filter((k) => k === `tool-${grep.id}`).length === 1, `each call renders exactly once: ${s.steps}`);
    await page.waitForFunction(() => /searching/.test(window.__tt.snap().dock.meta), null, { timeout: 5000 });
    await drive({ content: 'Read it.\n\nRetries are now 5.', tools: [readDone, grepDone], segments: persisted.segments }, { phase: 'model' });
    const live = await snapNow();
    check(live.prose.join('||') === 'Read it.||Retries are now 5.', `both prose blocks stream in order: ${live.prose}`);

    // done: reconcile from the persisted message.
    await finish(persisted);
    await page.waitForFunction(() => window.__tt.snap().dock.hidden, null, { timeout: 4000 });
    s = await snapNow();
    const doneAt = await page.evaluate(() => {
      const when = new Date('2026-10-05T09:42:00Z');
      const time = when.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
      const startOf = (date) => new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime();
      const days = Math.round((startOf(new Date()) - startOf(when)) / 86400000);
      // Mirrors ccDoneAt (call-progress.js), including its older-than-a-week form.
      if (days === 0) return time;
      if (days > 0 && days < 7) return `${when.toLocaleDateString([], { weekday: 'long' })} ${time}`;
      return `${when.toLocaleDateString([], { weekday: 'long', month: 'short', day: 'numeric' })}, ${time}`;
    });
    check(s.foot === `✻ ${turnVerb('tt-turn-a')} for 2m 13s · done ${doneAt}`, `the closing line reports the persisted duration and end time: ${s.foot}`);
    check(s.dock.verb === verb, `the spinner's word holds for the whole turn: ${s.dock.verb} then ${verb}`);
    check(JSON.stringify(s.blocks) === JSON.stringify(live.blocks), `the reconcile keeps the live render's blocks exactly: ${JSON.stringify(s.blocks)} vs ${JSON.stringify(live.blocks)}`);
    check(JSON.stringify(s.steps) === JSON.stringify(live.steps), `the reconcile keeps every row in order: ${s.steps} vs ${live.steps}`);
    check(JSON.stringify(s.prose) === JSON.stringify(live.prose), `the reconcile keeps the prose text exactly: ${s.prose}`);
    check(s.clocks.length === 0 && !s.labels.some((l) => /^Thinking for/.test(l)), `nothing still ticks or streams after done: ${s.clocks} ${s.labels}`);
    check((s.prose.join(' ').match(/Read it\./g) || []).length === 1 && (s.prose.join(' ').match(/Retries are now 5\./g) || []).length === 1, `no prose is doubled across the state boundaries: ${s.prose}`);

    // —— 2. Ragged chunk boundaries around a tool; gaps; 390px. ——
    const one = 'The retries constant lives in `app/config.py`, a path that keeps going ';
    const two = 'and the tests pin it at 3.';
    const long = 'see /srv/qa/vendor/lib/python3.12/site-packages/very_long_distribution_name/inner/module_deep.py for the vendored copy that also pins it';
    const readC = tool('read_file', { path: '/srv/qa/app/very_long_module_name_here.py', output: '1: x\n', call_duration_ms: 3200 });
    const finalC = { id: 'tt-chunks', role: 'assistant', model: 'qa/model', elapsedMs: 21000, endedAt: '2026-10-05T09:42:00Z',
      content: one + two + '\n\n' + long, tools: [readC], segments: [say(one), T(readC), say(two + '\n\n' + long)] };
    {
      const chunked = await page.evaluate(async ({ one, two, long, readC, finalC }) => {
        const f = fuseToolFixture;
        const frame = () => new Promise((r) => requestAnimationFrame(r));
        f.mountThread([
          { role: 'user', content: 'Go.', reasoning: '', error: '' },
          { id: 'tt-chunks', role: 'assistant', model: 'qa/model', content: '', reasoning: '', tools: [], segments: [], elapsedMs: 8000 },
        ], { live: true, phase: 'model' });
        f.stream(1, { content: one.slice(0, 17), segments: [{ type: 'content', text: one.slice(0, 17) }] });
        await frame();
        f.stream(1, { content: one, segments: [{ type: 'content', text: one }] });
        await frame();
        f.stream(1, { tools: [readC], segments: [{ type: 'content', text: one }, { type: 'tool', id: readC.id }] });
        f.toolEvent({ event: 'activity', phase: 'tools', tool_id: readC.id });
        f.toolEvent({ event: 'activity', phase: 'model' });
        await frame();
        f.stream(1, { content: one + two, segments: [{ type: 'content', text: one }, { type: 'tool', id: readC.id }, { type: 'content', text: two }] });
        await frame();
        f.run({ status: 'completed' });
        f.progress(1, { message: finalC });
        await new Promise((r) => setTimeout(r, 250));
        const md = [...document.querySelectorAll('.chat-log > article:last-child [data-flow] > .chat-md')].map((el) => el.innerText.replace(/\s+/g, ' ').trim());
        const gaps = [...document.querySelectorAll('.chat-log > article:last-child [data-flow]')].flatMap((flow) => {
          const blocks = [...flow.children].filter((el) => el.matches('.chat-md, .chat-activity'));
          const out = [];
          for (let i = 1; i < blocks.length - 1; i += 1) {
            if (!blocks[i].matches('.chat-md')) continue;
            const above = blocks[i].getBoundingClientRect().top - blocks[i - 1].getBoundingClientRect().bottom;
            const below = blocks[i + 1].getBoundingClientRect().top - blocks[i].getBoundingClientRect().bottom;
            out.push(Math.abs(above - below));
          }
          return out;
        });
        return { md, gaps, overflow: document.scrollingElement.scrollWidth > window.innerWidth };
      }, { one, two, long, readC, finalC });
      check(chunked.md.length === 2, `prose split by a tool renders two blocks: ${chunked.md}`);
      check(chunked.md[0] === 'The retries constant lives in app/config.py, a path that keeps going', `the first block keeps its exact streamed text: ${chunked.md[0]}`);
      check(chunked.md[1].startsWith('and the tests pin it at 3.'), `the second block starts where the first ended: ${chunked.md[1]}`);
      check(!chunked.md[1].includes('keeps going and'), `the boundary does not double the split word: ${chunked.md[1]}`);
      check((chunked.md.join(' ').match(/pin it at 3/g) || []).length === 1, `the tail sentence arrives once: ${chunked.md.join(' ')}`);
      check(chunked.md[1].includes('module_deep.py'), `the long path survives the reconcile: ${chunked.md[1].slice(0, 80)}`);
      check(chunked.gaps.every((g) => g <= 0.5), `prose gaps between tool rows stay symmetric: ${chunked.gaps}`);
      check(!chunked.overflow, `no horizontal overflow at 1440`);
    }
    await page.setViewportSize({ width: 390, height: 844 });
    {
      const mobile = await page.evaluate(() => {
        const f = fuseToolFixture;
        const read = { id: 'tt-m1', name: 'read_file', status: 'done', path: '/srv/qa/vendor/lib/python3.12/site-packages/very_long_distribution_name/inner/module_deep.py', output: '1: x\n', call_duration_ms: 3200, error: '', results: [], host: 'qa-box', cwd: '/srv/qa', platform: 'posix', project_root: '/srv/qa' };
        const tail = 'After a call that pins /srv/qa/vendor/lib/python3.12/site-packages/very_long_distribution_name/inner/module_deep.py in prose.';
        f.mountThread([
          { role: 'user', content: 'Go.', reasoning: '', error: '' },
          { id: 'tt-mobile', role: 'assistant', model: 'qa/model', elapsedMs: 21000, endedAt: '2026-10-05T09:42:00Z', tools: [read],
            content: 'Before the call.\n\n' + tail,
            segments: [{ type: 'content', text: 'Before the call.\n\n' }, { type: 'tool', id: read.id }, { type: 'content', text: tail }] },
        ]);
        return { overflow: document.scrollingElement.scrollWidth > window.innerWidth + 1 };
      });
      check(!mobile.overflow, `long prose and paths do not overflow at 390px`);
    }
    await page.setViewportSize({ width: 1440, height: 1000 });

    // —— 3. The hidden-thinking switch, live and at the reconcile. ——
    {
      await start('tt-hide', 4000);
      const readH = tool('read_file', { path: '/srv/qa/app/a.py', output: '1: x\n', call_duration_ms: 900 });
      await drive({ reasoning: 'Secret plan.', segments: [think('Secret plan.')] });
      await page.waitForFunction(() => /^Thinking for/.test(window.__tt.snap().labels[0] || ''), null, { timeout: 3500 });
      await page.evaluate(() => fuseToolFixture.thinking(false));
      const hidden = await snapNow();
      check(!hidden.labels.some((l) => /Thinking for|Thought for|Secret plan/.test(l)), `the switch hides the live thought line at once: ${hidden.labels}`);
      check(hidden.clocks.length === 0, `the hidden live thought keeps no ticking clock: ${hidden.clocks}`);
      check(hidden.dock.meta.includes('thinking'), `the status line still says the model is thinking while hidden: ${hidden.dock.meta}`);
      await drive({ reasoning: 'Secret plan. More.', segments: [think('Secret plan. More.')] });
      const stillHidden = await snapNow();
      check(!stillHidden.labels.some((l) => /Thinking for|Secret plan/.test(l)), `streaming into a hidden thought still renders no row: ${stillHidden.labels}`);
      await page.evaluate(() => fuseToolFixture.thinking(true));
      await page.waitForFunction(() => /^Thinking for/.test((window.__tt.snap().labels[0] || '')), null, { timeout: 3500 });
      await page.waitForFunction(() => /think:[2-9]s/.test(window.__tt.snap().clocks.join(',') || ''), null, { timeout: 4500 });
      await page.evaluate(() => fuseToolFixture.thinking(false));
      await finish({ id: 'tt-hide', role: 'assistant', model: 'qa/model', elapsedMs: 12000, endedAt: '2026-10-05T09:42:00Z',
        content: 'Done anyway.', reasoning: 'Secret plan. More.', tools: [readH],
        segments: [think('Secret plan. More.', 5000), T(readH), say('Done anyway.')] });
      await page.waitForTimeout(250);
      const hiddenSettled = await snapNow();
      check(!hiddenSettled.labels.some((l) => /Thought for|Thinking for|Secret plan/.test(l)), `the settle with thinking hidden drops every thought row: ${hiddenSettled.labels}`);
      check(!hiddenSettled.prose.join(' ').includes('Secret plan'), `hidden reasoning never leaks into prose: ${hiddenSettled.prose}`);
      await page.evaluate(() => fuseToolFixture.thinking(true));
      const back = await snapNow();
      check(back.labels.some((l) => /^Thought for 5s/.test(l)), `the switch restores landed thoughts with their durations: ${back.labels}`);
    }

    // —— 4. Stopping/stopped and failed endings. ——
    {
      await start('tt-stop', 21000);
      const cmd = tool('run_terminal_command', { command: 'npm run build', description: 'Build the bundle', child_id: 'ttb1', command_id: 'ttb1', status: 'running', state: 'running', execution_status: 'running', exit_code: null, elapsed_ms: 5000, execution_duration_ms: null });
      await drive({ content: 'Building.\n\n', tools: [cmd], segments: [say('Building.\n\n'), T(cmd)] }, { phase: 'tools', tool_id: cmd.id });
      let s = await snapNow();
      const stopVerb = s.dock.verb;
      await page.evaluate(() => fuseToolFixture.run({ cancel_requested: true }));
      s = await snapNow();
      check(!s.dock.hidden && /\bstopping\b/.test(s.dock.meta), `stopping keeps the dock with a stopping state: ${s.dock.meta}`);
      check(s.dock.verb === stopVerb, `stopping keeps the turn's word: ${s.dock.verb} vs ${stopVerb}`);
      const stopMsg = { id: 'tt-stop', role: 'assistant', model: 'qa/model', elapsedMs: 24000, endedAt: '2026-10-05T09:44:10Z', stopped: true,
        content: 'Building.\n\n', tools: [{ ...cmd, status: 'interrupted', state: 'interrupted', execution_status: 'interrupted', error: 'the run ended before it finished', exit_code: null, elapsed_ms: undefined }],
        segments: [say('Building.\n\n'), T(cmd)] };
      await page.evaluate((stopMsg) => {
        const f = fuseToolFixture;
        f.run({ status: 'cancelled', cancel_requested: false });
        f.progress(1, { message: stopMsg });
      }, stopMsg);
      await page.waitForFunction(() => window.__tt.snap().dock.hidden, null, { timeout: 4000 });
      s = await snapNow();
      // Since f4b4635 the folded summary is only its summary; the call's own
      // row (shown once the line opens) carries the interrupted state.
      const stoppedState = await page.evaluate((id) => document.querySelector(`.chat-log > article:last-child [data-step="tool-${id}"]`)?.getAttribute('data-state'), cmd.id);
      check(stoppedState === 'interrupted', `a stopped turn settles its running call as interrupted: ${stoppedState} ${s.rows}`);
      check(s.clocks.length === 0, `a stopped turn leaves no ticking clock: ${s.clocks}`);
      check(s.foot === `✻ Interrupted · ${turnVerb('tt-stop')} for 24s`, `a stopped close says Interrupted, never done: ${s.foot}`);

      await start('tt-fail', 30000);
      await finish({ id: 'tt-fail', role: 'assistant', model: 'qa/model', elapsedMs: 30000, endedAt: '2026-10-05T09:50:00Z', error: 'Upstream returned 502', content: '', reasoning: '', tools: [], segments: [] });
      await page.waitForTimeout(250);
      const failed = await snapNow();
      check(failed.dock.hidden, `a failed run hides the status line: ${JSON.stringify(failed.dock)}`);
      check((await page.evaluate(() => document.querySelector('.chat-log > article:last-child [data-err]')?.innerText || '')).includes('Upstream returned 502'), 'the failure text shows on the turn');
      check(failed.foot === `✻ Failed · ${turnVerb('tt-fail')} for 30s`, `a failed close says Failed, never done: ${failed.foot}`);
    }

    // —— 5. The ✻ line's other values. ——
    {
      const footMsg = (over = {}) => ({ id: 'tt-foot', role: 'assistant', model: 'qa/model', elapsedMs: 60000, endedAt: '2026-10-05T10:00:00Z',
        usage: { prompt_tokens: 1000, completion_tokens: 40 }, content: 'Waiting on reviewers.', tools: [], segments: [say('Waiting on reviewers.')], ...over });
      const settle = (msg, extra = {}) => page.evaluate(({ msg, extra }) => {
        const f = fuseToolFixture;
        if (extra.agents) f.todo.agents(extra.agents);
        if (extra.jobs) { f.work(extra.jobs); document.querySelector('[data-agents-close]')?.click(); }
        f.run({ status: 'completed' });
        f.progress(1, { message: msg });
      }, { msg, extra });
      await start('tt-foot', 60000);
      await settle(footMsg(), { agents: [{ child_id: 'ag1', status: 'running', description: 'Audit retries' }, { child_id: 'ag2', status: 'running', description: 'Check timeouts' }] });
      check((await snapNow()).foot === '✻ Waiting for 2 background agents to finish', `pending agents replace the close: ${(await snapNow()).foot}`);
      await page.evaluate(() => fuseToolFixture.todo.agents([]));
      await settle(footMsg(), { jobs: [{ id: 'sh1', command_id: 'sh1', state: 'running', command: 'npm run build' }] });
      check(/· 1 shell still running$/.test((await snapNow()).foot), `a running shell appends to the close: ${(await snapNow()).foot}`);
      await settle(footMsg(), { jobs: [{ id: 'sh2', command_id: 'sh2', state: 'running', command: 'npm test' }, { id: 'mon1', command_id: 'mon1', state: 'running', kind: 'monitor' }] });
      check(/· 2 background tasks still running$/.test((await snapNow()).foot), `mixed running work sums to background tasks: ${(await snapNow()).foot}`);
      await page.evaluate(() => fuseToolFixture.work([]));
      await page.evaluate(() => { document.querySelector('[data-agents-close]')?.click(); });
      await settle(footMsg({ elapsedMs: undefined, endedAt: undefined }));
      const noClock = (await snapNow()).foot;
      check(!noClock.includes('✻') && noClock.includes('40 tok'), `no elapsedMs means no closing line, only the turn meta: ${noClock}`);
    }

    check(errors.length === 0, `page errors: ${errors.join('; ')}`);
  } finally {
    await browser.close();
  }
  const unique = [...new Set(failures)];
  if (unique.length) console.error(unique.join('\n'));
  assert.equal(unique.length, 0, `${unique.length} console text/thinking problems`);
  console.log('PASS: console text + thinking — live phases, folded thoughts, status line, ✻ close, hidden switch, reconcile');
})().catch((error) => { console.error(error); process.exit(1); });
