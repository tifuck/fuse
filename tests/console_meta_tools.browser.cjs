// Rows for the tools that are not files, shell or
// subagents. tool_ui.browser.cjs pins their state words;
// here each call is driven running → done, then failed, then interrupted, and
// the row must show the latest header, values and dot after every step.
// A tool event keeps the fields mergeToolEvent copies; results, output and a
// fetch's byte count arrive with the persisted snapshot, so those are checked
// on the remount. todo_write never prints a transcript row
// (todo_section.browser.cjs owns the section); this only checks the transcript
// stays row-free too.
const assert = require('node:assert/strict');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const origin = process.env.FUSE_BROWSER_ORIGIN || 'http://127.0.0.1:8099';
assert.match(origin, /^http:\/\/127\.0\.0\.1:\d+$/);

const PAGE = 'https://en.wikipedia.org/wiki/Ada_Lovelace';
const LONG = 'https://example.com/' + 'documentation/'.repeat(12) + 'index';
const HITS = [
  { title: 'Console row guide', url: 'https://example.com/guide', snippet: 'How a row settles.' },
  { title: 'Lifecycle notes', url: 'https://example.com/lifecycle', snippet: 'Running, then done.' },
];
const SEARCH_DONE = {
  status: 'done', query: 'fuse console rows',
  queries: ['fuse console rows', 'tool row lifecycle'], latency_ms: 840, results: HITS,
};
const PAGE_DONE = {
  status: 'done', url: PAGE, http_status: 200, bytes: 12600,
  results: [{ title: 'Ada Lovelace', url: PAGE, snippet: 'Augusta Ada King, Countess of Lovelace, wrote notes on the Analytical Engine.' }],
};
const MODELS = JSON.stringify({ models: [
  { id: 'grok-4', name: 'Grok 4', provider: 'xai' },
  { id: 'gpt-5', name: 'GPT-5', provider: 'openai' },
] });
const SLOTS = JSON.stringify({ slots: { brief: 1200, notes: 40 } });

const turn = (trace, live) => ({
  role: 'assistant', model: 'qa/model', elapsedMs: live ? null : 4000,
  content: 'Checked.', segments: [{ type: 'content', text: 'Checked.' }, { type: 'tool', id: trace.id }],
  tools: [trace],
});

(async () => {
  const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'], ...(process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {}) });
  const errors = [];
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, reducedMotion: 'reduce' });
    page.on('pageerror', (error) => errors.push(error.message));
    await page.route('**/*', (route) => new URL(route.request().url()).origin === origin ? route.continue() : route.abort());
    await page.goto(origin);
    await page.locator('[data-sync-label]').filter({ hasText: 'Live sync' }).waitFor({ state: 'attached' });
    await page.waitForFunction(() => window.fuseToolFixture);

    const mount = (trace, live) => page.evaluate(([trace, live]) => {
      fuseToolFixture.mountThread([{ role: 'user', content: 'Go.' }, {
        role: 'assistant', model: 'qa/model', elapsedMs: live ? null : 4000, content: 'Checked.',
        segments: [{ type: 'tool', id: trace.id }, { type: 'content', text: 'Checked.' }], tools: [trace],
      }], { live, phase: live ? 'tools' : '' });
    }, [trace, live]);
    const send = (event) => page.evaluate((event) => fuseToolFixture.toolEvent(event), event);
    const rowOf = (id) => page.locator(`[data-step="tool-${id}"]`);
    const open = async (id) => {
      const li = rowOf(id);
      if (await li.evaluate((el) => el.classList.contains('is-console-expanded'))) return;
      const toggle = li.locator(':scope > .chat-step-main > .chat-step-head > .chat-step-toggle');
      if (await toggle.count()) await toggle.click();
    };
    const paint = () => page.waitForFunction(() => new Promise((resolve) => requestAnimationFrame(() => resolve(true))));

    // The transcript draws a coloured dot.
    const mark = (id) => page.evaluate((id) => {
      const li = document.querySelector(`[data-step="tool-${id}"]`);
      if (!li) return null;
      const state = li.dataset.state;
      const node = li.querySelector(':scope > .chat-step-node');
      const dot = getComputedStyle(node, '::after');
      const token = (name) => {
        const raw = getComputedStyle(document.querySelector('.chat')).getPropertyValue(name).trim();
        const probe = document.createElement('span');
        probe.style.color = raw;
        document.body.append(probe);
        const color = getComputedStyle(probe).color;
        probe.remove();
        return color;
      };
      return { state, cls: li.className, bg: dot.backgroundColor, glyph: dot.content,
        tokens: { running: token('--call-live'), done: token('--call-done'), error: token('--call-fail'), interrupted: token('--call-idle') },
        live: li.querySelector('.chat-console-call')?.classList.contains('is-live') || false };
    }, id);

    const read = async (id) => {
      const li = rowOf(id);
      await li.waitFor({ state: 'attached' });
      const head = (await li.locator('.chat-console-call').innerText()).replace(/\s+/g, ' ').trim();
      const result = ((await li.locator('.chat-console-result-text').allInnerTexts()).join(' ')).replace(/\s+/g, ' ').trim();
      const body = ((await li.locator('.chat-console-hang-body').allInnerTexts()).join('\n')).replace(/[ \t]+/g, ' ').trim();
      const dot = await mark(id);
      return { head, result, body, live: dot.live, dot };
    };

    const expectDot = (seen, want, where) => {
      assert.equal(seen.state, want, `${where}: data-state ${seen.state}`);
      assert.equal(seen.cls.includes(`is-${want}`), true, `${where}: row class ${seen.cls}`);
      // An interrupted call is red, like a failure; done and running keep their own.
      const token = want === 'interrupted' ? 'error' : want;
      assert.equal(seen.bg, seen.tokens[token], `${where}: dot ${seen.bg}, wanted ${seen.tokens[token]}`);
    };

    // One call, three endings. `start` is the running event the server sends;
    // each settle replaces the running row and must not keep its words.
    const life = async (name, start, endings) => {
      const id = `m-${name}`;
      await mount({ id, name, ...start, status: 'running' }, true);
      let seen = await read(id);
      const headOf = (pattern) => new RegExp(pattern.source.replace(/\\([()])/g, '\\$1?'));
      assert.match(seen.head, headOf(start.head), `${name} running head: ${seen.head}`);
      assert.match(seen.result, /Running…|Fetching…/, `${name} running result: ${seen.result}`);
      expectDot(seen.dot, 'running', `${name} running`);
      for (const end of endings) {
        await send({ event: 'tool', id, name, ...end.event });
        await paint();
        await open(id);
        seen = await read(id);
        assert.match(seen.head, headOf(end.head), `${name} ${end.event.status} head: ${seen.head}`);
        const wanted = end.live || [];
        const hit = wanted.filter((text) => seen.body.includes(text) || (end.keptHas || []).some((part) => seen.body.includes(part)) || (text.startsWith('Error: ') && seen.body.includes(text.slice(7))) || (text.startsWith('Interrupted · ') && seen.body.includes('Interrupted')));
        assert.equal(hit.length > 0 || !wanted.length, true, `${name} ${end.event.status} shows none of ${JSON.stringify(wanted)}: ${JSON.stringify(seen.body)}`);
        for (const text of end.lacks || []) assert.equal(seen.body.includes(text), false, `${name} ${end.event.status} still shows ${JSON.stringify(text)}: ${JSON.stringify(seen.body)}`);
        assert.equal(seen.live, false, `${name} ${end.event.status} keeps a live line: ${seen.head}`);
        expectDot(seen.dot, end.dot, `${name} ${end.event.status}`);
        await mount({ id, name, ...start, ...end.event, ...(end.kept || {}) }, false);
        await open(id);
        const again = await read(id);
        assert.equal(again.head, seen.head, `${name} ${end.event.status} reconcile head: ${again.head}`);
        for (const text of end.keptHas || []) assert.equal(again.body.includes(text), true, `${name} ${end.event.status} snapshot missing ${JSON.stringify(text)} in ${JSON.stringify(again.body)}`);
      }
    };

    for (const [width, height] of [[1440, 1000], [390, 844]]) {
      await page.setViewportSize({ width, height });

      const tag = `${width}px`;

      await life('web_search', {
        head: /Web Search\(?"fuse console rows" \+1\)?/, queries: ['fuse console rows', 'tool row lifecycle'], query: 'fuse console rows',
      }, [
        { event: SEARCH_DONE, head: /Web Search\(?"fuse console rows" \+1\)?/, live: ['Did 2 searches in 840ms'], keptHas: ['Console row guide', 'Lifecycle notes', 'example.com'], lacks: ['Running…'], dot: 'done' },
        { event: { status: 'error', error: 'Search failed. Try again shortly.', queries: ['fuse console rows', 'tool row lifecycle'], results: [] }, head: /Web Search/, live: ['Error: Search failed. Try again shortly.'], lacks: ['Did 2 searches', 'Running…'], dot: 'error' },
        // The interrupted line names why it stopped and still counts the query.
        { event: { status: 'interrupted', error: 'Tool interrupted.', queries: ['fuse console rows'] }, head: /Web Search/, live: ['Interrupted · the run ended before it finished', 'Did 1 search'], lacks: ['Running…', 'Did 2 searches'], dot: 'interrupted' },
      ]);

      await life('read_url', { head: new RegExp(`Fetch\\(${PAGE.replace(/[.]/g, '\\.')}\\)`), url: PAGE }, [
        { event: PAGE_DONE, head: /Fetch\(https:\/\/en\.wikipedia\.org/, live: ['en.wikipedia.org'], keptHas: ['Ada Lovelace', 'Analytical Engine'], lacks: ['Fetching…'], dot: 'done' },
        { event: { status: 'error', url: PAGE, error: 'Page not allowed.', results: [] }, head: /Fetch\(/, live: ['Error: Page not allowed.'], lacks: ['Received', 'Fetching…'], dot: 'error' },
        { event: { status: 'interrupted', url: PAGE, error: '' }, head: /Fetch\(/, live: ['Interrupted · the run ended before it finished'], lacks: ['Fetching…', 'Received'], dot: 'interrupted' },
      ]);

      // A long URL wraps inside the row; it must not widen the page.
      await mount({ id: 'm-long', name: 'read_url', status: 'done', url: LONG, http_status: 200, bytes: 100, results: [{ title: 'Index', url: LONG, snippet: 'x' }] }, false);
      await open('m-long');
      const long = await read('m-long');
      assert.equal(long.head.startsWith('Fetch'), true, `${tag} long fetch head: ${long.head}`);
      assert.equal(await page.evaluate(() => document.scrollingElement.scrollWidth <= window.innerWidth), true, `${tag} long URL widens the page`);

      // search_tools is silent on purpose (tool lookups fold without a count, as
      // Claude Code hides ToolSearch), so a running, done or failed call is
      // neither a row nor part of a summary line. The activity summary still
      // counts it. An interruption stands on its own row.
      for (const [state, trace] of [
        ['running', { status: 'running', query: 'shell' }],
        ['done', { status: 'done', preview: 'run_terminal_command, get_command_output', output: JSON.stringify({ loaded: ['run_terminal_command', 'get_command_output'] }) }],
        ['failed', { status: 'error', error: 'explore subagents are read-only', preview: 'refused: write_file' }],
      ]) {
        await mount({ id: 'm-tools', name: 'search_tools', ...trace }, state === 'running');
        assert.equal(await rowOf('m-tools').count(), 0, `${tag} search_tools rendered a row while ${state}`);
        assert.equal((await page.locator('[data-chat-log]').innerText()).includes('run_terminal_command'), false, `${tag} search_tools showed its tools while ${state}`);
      }
      assert.match(await page.locator('.chat-cli-run').innerText(), /1 action/, `${tag} the summary still counts the silent call`);
      await mount({ id: 'm-tools', name: 'search_tools', status: 'interrupted' }, false);
      const silent = await read('m-tools');
      assert.match(silent.head, /^Tools$/, `${tag} interrupted search_tools head: ${silent.head}`);
      assert.match(silent.body, /Interrupted/, `${tag} interrupted search_tools: ${silent.body}`);
      expectDot(silent.dot, 'interrupted', `${tag} search_tools interrupted`);

      await life('load_skill', { head: /Loading the ponytail skill/, preview: 'ponytail' }, [
        { event: { status: 'done', preview: 'ponytail', output: 'Loaded.\n\n# Ponytail\n\nBe lazy.' }, head: /Loaded the ponytail skill/, live: ['Loaded the ponytail skill'], kept: { output: 'Loaded.\n\n# Ponytail\n\nBe lazy.' }, keptHas: ['Ponytail', 'Be lazy.'], lacks: ['Running…'], dot: 'done' },
        { event: { status: 'error', preview: 'nope', error: 'Unknown skill.' }, head: /Failed to load the nope skill/, live: ['Error: Unknown skill.'], lacks: ['Loaded skill', 'Running…'], dot: 'error' },
        { event: { status: 'interrupted', preview: 'ponytail' }, head: /Loading the ponytail skill/, live: ['Interrupted · the run ended before it finished', 'ponytail'], lacks: ['Running…', 'Loaded skill'], dot: 'interrupted' },
      ]);

      await life('propose_skill', { head: /Draft\(ponytail\)/, preview: 'ponytail' }, [
        { event: { status: 'done', preview: 'ponytail', output: 'Proposed skill `ponytail` (not saved).' }, head: /Draft\(ponytail\)/, live: ['Proposed skill ponytail'], kept: { output: 'Proposed skill `ponytail` (not saved).' }, keptHas: ['Proposed skill `ponytail` (not saved).'], lacks: ['Running…'], dot: 'done' },
        { event: { status: 'error', preview: 'refused', error: 'Skill authoring is disabled in this turn.' }, head: /Draft\(refused\)/, live: ['Error: Skill authoring is disabled in this turn.'], lacks: ['not saved', 'Running…'], dot: 'error' },
        { event: { status: 'interrupted', preview: 'ponytail' }, head: /Draft\(ponytail\)/, live: ['Interrupted · the run ended before it finished', 'ponytail'], lacks: ['Running…'], dot: 'interrupted' },
      ]);

      await life('create_artifact', { head: /Artifact\(Sales dashboard\)/, title: 'Sales dashboard', identifier: 'art-1' }, [
        { event: { status: 'done', title: 'Sales dashboard', identifier: 'art-1', version: 3, visibility: 'private' }, head: /Artifact\(Sales dashboard\)/, live: ['Created v3'], lacks: ['Running…'], dot: 'done' },
        { event: { status: 'error', title: 'Sales dashboard', error: 'Invalid artifact content.' }, head: /Artifact\(Sales dashboard\)/, live: ['Error: Invalid artifact content.'], lacks: ['Created', 'Running…'], dot: 'error' },
        { event: { status: 'interrupted', title: 'Sales dashboard' }, head: /Artifact\(Sales dashboard\)/, live: ['Interrupted · the run ended before it finished'], lacks: ['Running…', 'Created'], dot: 'interrupted' },
      ]);

      await life('update_artifact', { head: /Artifact\(Sales dashboard\)/, title: 'Sales dashboard', identifier: 'art-1' }, [
        { event: { status: 'done', title: 'Sales dashboard', identifier: 'art-1', version: 4, visibility: 'public' }, head: /Artifact\(Sales dashboard\)/, live: ['Updated v4'], lacks: ['Created', 'Running…'], dot: 'done' },
        { event: { status: 'error', title: 'Sales dashboard', error: 'version conflict' }, head: /Artifact\(Sales dashboard\)/, live: ['Error: version conflict'], lacks: ['Updated', 'Running…'], dot: 'error' },
        { event: { status: 'interrupted', title: 'Sales dashboard' }, head: /Artifact\(Sales dashboard\)/, live: ['Interrupted · the run ended before it finished'], lacks: ['Running…', 'Updated'], dot: 'interrupted' },
      ]);

      await life('search_models', { head: /Models\(grok\)/, query: 'grok' }, [
        { event: { status: 'done', query: 'grok', preview: '2 models', output: MODELS }, head: /Models\(grok\)/, live: ['2 models'], kept: { output: MODELS }, keptHas: ['Grok 4', 'grok-4', 'GPT-5'], lacks: ['Running…'], dot: 'done' },
        { event: { status: 'error', query: 'grok', error: 'model catalog unavailable' }, head: /Models\(grok\)/, live: ['Error: model catalog unavailable'], lacks: ['Grok 4', 'Running…'], dot: 'error' },
        { event: { status: 'interrupted', query: 'grok' }, head: /Models\(grok\)/, live: ['Interrupted · the run ended before it finished'], lacks: ['Running…', 'Grok 4'], dot: 'interrupted' },
      ]);

      // The running event carries what the call does ("Write brief"); the
      // header shows it and the result line says what came back.
      for (const [op, description, preview, output, result] of [
        ['put', 'Write brief', '1 slots', SLOTS, 'Pad holds 1 slot'],
        ['read', 'Read brief', 'brief (1200 chars)', 'the brief', 'Read 1,200 chars'],
        ['clear', 'Clear brief', '0 slots', JSON.stringify({ slots: {} }), 'Pad holds 0 slots'],
      ]) {
        await mount({ id: 'm-scratch', name: 'scratch', status: 'running', description }, true);
        let seen = await read('m-scratch');
        const head = `Scratch(${description})`;
        assert.equal(seen.head, head, `${tag} scratch running head: ${seen.head}`);
        expectDot(seen.dot, 'running', `${tag} scratch running`);
        await send({ event: 'tool', id: 'm-scratch', name: 'scratch', status: 'done', description, preview, output });
        await paint();
        seen = await read('m-scratch');
        assert.equal(seen.head, head, `${tag} scratch ${op} head: ${seen.head}`);
        assert.equal(seen.body.includes(result), true, `${tag} scratch ${op} missing ${result}: ${seen.body}`);
        await mount({ id: 'm-scratch', name: 'scratch', status: 'done', description, preview, output }, false);
        await open('m-scratch');
        seen = await read('m-scratch');
        const shown = op === 'read' ? 'the brief' : op === 'put' ? 'brief' : 'Pad holds 0 slots';
        assert.equal(seen.body.includes(shown), true, `${tag} scratch ${op} snapshot: ${seen.body}`);
        assert.equal(seen.body.includes('Running…'), false, `${tag} scratch ${op} still running`);
        expectDot(seen.dot, 'done', `${tag} scratch ${op}`);
      }
      await send({ event: 'tool', id: 'm-scratch', name: 'scratch', status: 'error', error: "ops[0]: scratch slot 'missing' not found" });
      await paint();
      await open('m-scratch');
      let scratch = await read('m-scratch');
      assert.equal(scratch.body.includes("scratch slot 'missing' not found"), true, `${tag} scratch failure: ${scratch.body}`);
      assert.equal(scratch.body.includes('1,200 chars'), false, `${tag} a failed scratch keeps the old pad`);
      expectDot(scratch.dot, 'error', `${tag} scratch failed`);
      await send({ event: 'tool', id: 'm-scratch', name: 'scratch', status: 'interrupted', error: '' });
      await paint();
      await open('m-scratch');
      scratch = await read('m-scratch');
      assert.match(scratch.body, /Interrupted/, `${tag} scratch interrupted: ${scratch.body}`);
      expectDot(scratch.dot, 'interrupted', `${tag} scratch interrupted`);

      // An unknown name falls through to the generic Tool verb and its error.
      await life('frobnicate', { head: /^Tool$/, preview: '' }, [
        { event: { status: 'error', error: "Unknown tool 'frobnicate'. Call search_tools with query 'index' to list loadable tools." }, head: /^Tool$/, live: ["Error: Unknown tool 'frobnicate'."], lacks: ['Running…'], dot: 'error' },
        { event: { status: 'interrupted' }, head: /^Tool$/, live: ['Interrupted · the run ended before it finished'], lacks: ['Running…', 'Unknown tool'], dot: 'interrupted' },
        { event: { status: 'done', preview: 'odd' }, head: /Tool\(odd\)/, live: ['odd'], lacks: ['Running…', 'Unknown tool'], dot: 'done' },
      ]);

      // todo_write updates the section, never the transcript, in every state.
      await page.evaluate(() => fuseToolFixture.todo.reset());
      await mount({ id: 'm-todo', name: 'todo_write', status: 'running', todos: [{ id: '1', content: 'Pin the meta rows', status: 'in_progress' }] }, true);
      assert.equal(await rowOf('m-todo').count(), 0, `${tag} a running todo_write printed a row`);
      await page.locator('[data-live-todos]').filter({ hasText: 'Pin the meta rows' }).waitFor({ state: 'attached' });
      assert.match(await page.locator('[data-live-todos]').innerText(), /0\/1[\s\S]*Pin the meta rows/, `${tag} a running todo shows its item as unfinished`);
      await send({ event: 'tool', id: 'm-todo', name: 'todo_write', status: 'done', todos: [{ id: '1', content: 'Pin the meta rows', status: 'completed' }] });
      await paint();
      assert.equal(await rowOf('m-todo').count(), 0, `${tag} a settled todo_write printed a row`);
      let section = await page.locator('[data-live-todos]').innerText();
      assert.match(section, /Pin the meta rows/, `${tag} settled todo section: ${section}`);
      await send({ event: 'tool', id: 'm-todo', name: 'todo_write', status: 'error', error: 'todos[0].content is required' });
      await paint();
      assert.equal(await rowOf('m-todo').count(), 0, `${tag} a failed todo_write printed a row`);

      assert.equal(await page.evaluate(() => document.scrollingElement.scrollWidth <= window.innerWidth), true, `${tag} page wider than the viewport`);
      console.log(`PASS meta tools ${tag}`);
    }
    assert.deepEqual(errors, [], `page errors: ${errors.join(' | ')}`);
  } finally {
    await browser.close();
  }
})().catch((error) => { console.error(error); process.exitCode = 1; });
