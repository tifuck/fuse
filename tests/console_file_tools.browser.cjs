// Console/Mainframe rows for the file and search tools — read_file (ranges,
// images, refusals), write_file (create vs overwrite), search_replace (single
// and multi edits, counts), list_dir, glob, grep (modes, no match) — over
// their whole lifecycle: running header → done header with the right values →
// failed, plus the folded reads/searches summary line growing as calls are
// added and when one of them fails later, and a snapshot reconcile that must
// not change, duplicate or drop anything.
//
// Already pinned elsewhere, so not repeated here: interrupted reads and edits
// (tests/console_rows_audit.browser.cjs), generated read/search/glob/list_dir
// histories and stale-state rules (tests/console_ui.browser.cjs +
// console_ui_lib.js), per-tool state labels in cli/compact
// (tests/tool_ui.browser.cjs).
const assert = require('node:assert/strict');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const origin = process.env.FUSE_BROWSER_ORIGIN || 'http://127.0.0.1:8099';
assert.match(origin, /^http:\/\/127\.0\.0\.1:\d+$/);

const ROOT = { host: 'qa-box', cwd: '/srv/qa', platform: 'posix', project_root: '/srv/qa' };
let seq = 0;
const tool = (name, fields = {}) => ({
  id: `f${++seq}`, name, status: 'done', error: '', results: [], output: '',
  duration_ms: 120, call_duration_ms: 120, ...ROOT, ...fields,
});
const T = (trace) => ({ type: 'tool', id: trace.id });
const say = (text) => ({ type: 'content', text });
const turn = (segments, tools, extra = {}) => ({
  role: 'assistant', model: 'qa/model', elapsedMs: 30000, tools, segments,
  content: segments.filter((s) => s.type === 'content').map((s) => s.text).join(''),
  reasoning: '', ...extra,
});
const user = (content) => ({ role: 'user', content, reasoning: '', error: '' });
const ended = { endedAt: '2026-10-03T20:00:00Z' };
// A genuine 1×1 PNG (valid base64, %4 == 0) so the preview <img> decodes.
const PNG = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4z8AAAAMBAQDJ/pLvAAAAAElFTkSuQmCC';
const DEEP = 'integration/replication/backoff_and_retry_policies/module_with_a_long_name.py';
const lines = (from, n) => Array.from({ length: n }, (_, i) => `${from + i}: line ${from + i}`).join('\n');

function scenarios() {
  const list = [];
  const add = (name, build) => list.push({ name, ...build() });

  // —— read_file: a text range via offset/limit ——
  add('read range: running line, then done values, then a settle that keeps them', () => {
    const r = tool('read_file', { path: `/srv/qa/${DEEP}`, status: 'running', offset: 40, limit: 60, elapsed_ms: 4000, call_duration_ms: null, duration_ms: null });
    return { wide: true, rows: [user('Go.'), turn([T(r)], [r])], live: true, phase: 'tools', steps: [
      { name: 'running', has: ['Reading ' + DEEP], lacks: ['Read 60 lines', 'Read(' + DEEP + ')'], running: [r.id], states: { [r.id]: 'running' } },
      { name: 'done', event: { event: 'tool', id: r.id, status: 'done', output: lines(40, 60), call_duration_ms: 3500, offset: 40, limit: 60 },
        has: ['Read(' + DEEP + ')', 'Read 60 lines'], lacks: ['Reading ' + DEEP], running: [], states: { [r.id]: 'done' }, gutter: '40' },
      // The newest call of a live block keeps its own row; opening it prints
      // the numbered body with the real first line (40) and the cut note.
      { name: 'open', open: true, has: ['Read 60 lines', 'line 40', '… +50 lines'], gutter: '40' },
      // A snapshot reconcile with the same states changes nothing.
      { name: 'reconcile', remount: { live: true, phase: 'tools' }, labelsSame: true, gutter: '40' },
      // Settled, the read folds into one line and keeps its values on open.
      { name: 'settled', remount: { live: false }, has: ['Read 1 file'], running: [] },
      { name: 'settled open', open: true, has: ['Read 60 lines', 'line 40'], gutter: '40' },
    ] };
  });

  // —— read_file: an image, not a line count ——
  add('image read: running, then an image preview with its metadata', () => {
    const r = tool('read_file', { path: '/srv/qa/shots/before.png', status: 'running', elapsed_ms: 900, call_duration_ms: null, duration_ms: null });
    return { rows: [user('Go.'), turn([T(r)], [r])], live: true, phase: 'tools', steps: [
      { name: 'running', has: ['Reading shots/before.png'], lacks: ['Image preview'], running: [r.id] },
      { name: 'done', event: { event: 'tool', id: r.id, status: 'done',
          image: { format: 'PNG', mime: 'image/png', width: 640, height: 480, original_width: 1280, original_height: 960, frames: 3, frame: 1, data: PNG },
          call_duration_ms: 900 },
        has: ['Read(shots/before.png)', 'Image preview', 'PNG · 640 × 480 · resized from 1280 × 960', 'frame 2 of 3'],
        lacks: ['Read 1 line'], running: [], images: 1, states: { [r.id]: 'done' } },
      { name: 'settled', remount: { live: false }, has: ['Read 1 file'], images: 1 },
      { name: 'settled open', open: true, has: ['Image preview', 'PNG · 640 × 480'], images: 1 },
    ] };
  });

  // —— read_file: a refused read keeps the whole path and the error ——
  add('denied read: the error lands on a red row and survives a reload', () => {
    const r = tool('read_file', { path: '/root/secrets/id_rsa', status: 'running', call_duration_ms: null, duration_ms: null });
    return { rows: [user('Go.'), turn([T(r)], [r])], live: true, phase: 'tools', steps: [
      { name: 'running', has: ['Reading /root/secrets/id_rsa'], running: [r.id] },
      { name: 'denied', event: { event: 'tool', id: r.id, status: 'error', error: 'permission denied: /root/secrets/id_rsa (read)' },
        has: ['Read(/root/secrets/id_rsa)', 'Error: permission denied: /root/secrets/id_rsa (read)'],
        lacks: ['Reading /root/secrets'], running: [], states: { [r.id]: 'error' } },
      { name: 'settled', remount: { live: false }, has: ['Read 1 file'], states: {} },
      { name: 'settled open', open: true, has: ['Error: permission denied: /root/secrets/id_rsa (read)'], states: {} },
    ] };
  });

  // —— write_file: create and overwrite, running → done ——
  add('write: create and overwrite both land as a Write row', () => {
    const w = tool('write_file', { path: '/srv/qa/app/new.py', status: 'running', call_duration_ms: null, duration_ms: null });
    const v = tool('write_file', { path: '/srv/qa/app/config.py', status: 'running', call_duration_ms: null, duration_ms: null });
    return { rows: [user('Go.'), turn([T(w)], [w])], live: true, phase: 'tools', steps: [
      { name: 'running', has: ['Write(app/new.py)', 'Running…'], lacks: ['Wrote'], running: [w.id] },
      // FINDING(write-result-hidden): the server's write result ("created
      // <path> (N bytes)", app/workspace.py _write) never renders and the
      // "Wrote N lines to <path>" line (chat.js consoleResultLine) is keyed
      // on trace.content, which no tool event or persisted trace carries —
      // so Console shows only "Wrote <path>", and create is indistinguishable
      // from overwrite. Intended, per the consoleFold comment: "Wrote 40 lines
      // to x.py" plus the first lines of the file.
      { name: 'created', event: { event: 'tool', id: w.id, status: 'done', output: 'From host qa-box:\ncreated /srv/qa/app/new.py (28 bytes)', call_duration_ms: 2600 },
        has: ['Write(app/new.py) · 2s', 'Wrote app/new.py'], lacks: ['created /srv/qa/app/new.py', '(28 bytes)', 'Running…'], running: [] },
      { name: 'second write running', event: { event: 'tool', id: v.id, name: 'write_file', status: 'running', path: '/srv/qa/app/config.py', host: 'qa-box', cwd: '/srv/qa', project_root: '/srv/qa', platform: 'posix' },
        has: ['Wrote app/new.py', 'Write(app/config.py)'], running: [v.id] },
      { name: 'overwritten', event: { event: 'tool', id: v.id, status: 'done', output: 'From host qa-box:\nwrote /srv/qa/app/config.py (512 bytes)', call_duration_ms: 1400 },
        has: ['Wrote app/config.py'], lacks: ['wrote /srv/qa/app/config.py', '(512 bytes)'], running: [] },
      { name: 'settled', remount: { live: false }, has: ['Write(app/new.py)', 'Wrote app/new.py', 'Wrote app/config.py'] },
    ] };
  });

  // —— search_replace: single and multi edits, counts, failure ——
  add('edit: multi-edit diff, explicit counts, and a failed edit', () => {
    const e = tool('search_replace', { path: '/srv/qa/app/config.py', status: 'running', call_duration_ms: null, duration_ms: null });
    return { wide: true, rows: [user('Go.'), turn([T(e)], [e])], live: true, phase: 'tools', steps: [
      { name: 'running', has: ['Update(app/config.py)', 'Running…'], lacks: ['Added'], running: [e.id] },
      // Two hunks in one preview (multi-edit), numbered by the server's @@
      // headers, counted from the diff itself.
      { name: 'multi done', event: { event: 'tool', id: e.id, status: 'done',
          preview: '@@ -10,2 +10,2 @@\n-retries = 3\n+retries = 5\n@@ -40,1 +40,1 @@\n-timeout = 30\n+timeout = 60', call_duration_ms: 2100 },
        has: ['Update(app/config.py)', 'Added 2 lines, removed 2 lines', 'retries = 5', 'timeout = 60'],
        lacks: ['@@', 'Running…'], running: [], diffRows: 4 },
      // Server-sent counts win over the diff's own.
      { name: 'reconcile', remount: { live: true, phase: 'tools' }, labelsSame: true, diffRows: 4 },
      { name: 'counts win', remount: { live: false, patch: { [e.id]: { lines_added: 7, lines_removed: 5 } } },
        has: ['Added 7 lines, removed 5 lines', 'retries = 5'], diffRows: 4 },
      // A later edit that cannot find its text fails red, with no diff.
      { name: 'not found', remount: { live: true, phase: 'tools', patch: { [e.id]: { status: 'error', error: 'old_string not found. It must match the file exactly.', preview: '', lines_added: undefined, lines_removed: undefined } } },
        has: ['Error: old_string not found'], lacks: ['Added', 'retries = 5'], states: { [e.id]: 'error' } },
    ] };
  });

  // —— grep: content, files_with_matches, count, and no match ——
  add('grep modes: content counts lines and files, files counts paths', () => {
    const content = tool('grep', { pattern: 'retries', path: '/srv/qa', output: 'app/a.py:12:retries = 3\napp/b.py:30:retries = 5\napp/a.py:99:retries = 3' });
    const files = tool('grep', { pattern: 'TODO', path: '/srv/qa', output: 'app/a.py\napp/b.py\napp/c.py' });
    const count = tool('grep', { pattern: 'cache', path: '/srv/qa', output: 'app/a.py:4\napp/d.py:9' });
    return { rows: [user('Go.'), turn([say('Searching.\n\n'), T(content), T(files), T(count)], [content, files, count], ended)], steps: [
      { name: 'folded', has: ['Searched for 3 patterns'] },
      { name: 'open', open: true, has: ['Search(pattern: "retries", path: ".")', 'Found 3 lines across 2 files', 'app/a.py:12:retries = 3',
        'Search(pattern: "TODO", path: ".")', 'Found 3 files', 'Search(pattern: "cache", path: ".")', 'Found 2 files'] },
    ] };
  });

  add('grep no match: a live miss still reads as a search, not a found file', () => {
    const g = tool('grep', { pattern: 'missing', path: '/srv/qa', status: 'running', call_duration_ms: null, duration_ms: null });
    return { rows: [user('Go.'), turn([T(g)], [g])], live: true, phase: 'tools', steps: [
      { name: 'running', has: ['Searching for missing'], running: [g.id] },
      // FINDING(grep-no-match): the server's no-match body is "(no matches)"
      // (app/workspace.py _grep), but the fold's no-match test only accepts
      // an unparenthesised "no matches", so the miss is counted as one file
      // and reads "Found 1 file". Intended: a miss reads as no matches (the
      // none branch exists for exactly this body), e.g. "Found 0 files".
      { name: 'done', event: { event: 'tool', id: g.id, status: 'done', output: '(no matches)' },
        has: ['Found 1 file'], lacks: ['Searching for missing'], running: [] },
      { name: 'settled', remount: { live: false }, has: ['Searched for 1 pattern'] },
      { name: 'settled open', open: true, has: ['Found 1 file'] },
    ] };
  });

  // —— glob and list_dir ——
  add('glob and list_dir: counts, fold kinds, and a long pattern at 390px', () => {
    const g = tool('glob', { pattern: 'workflow_dispatch.*retry_backoff.*max_attempts=[0-9]+', path: '/srv/qa', output: 'tests/a.browser.cjs\ntests/b.browser.cjs' });
    const l = tool('list_dir', { path: '/srv/qa/tests', output: 'a/\nb/\na.py\nb.py\n' });
    const empty = tool('glob', { pattern: '*.nomatch', path: '/srv/qa', output: '(no matches)' });
    return { wide: true, rows: [user('Go.'), turn([say('Looking.\n\n'), T(g), T(l), T(empty)], [g, l, empty], ended)], steps: [
      { name: 'folded', has: ['Searched for 2 patterns, listed 1 directory'] },
      { name: 'open', open: true, has: ['Search(pattern: "workflow_dispatch.*retry_backoff.*max_attempts=[0-9]+"', 'Found 2 files', 'tests/a.browser.cjs',
        'List(tests)', 'Listed 4 entries', 'a.py'] },
    ] };
  });

  // —— the folded reads/searches line grows as calls arrive ——
  add('fold grows: counts update as reads and searches land', () => {
    const a = tool('read_file', { path: '/srv/qa/app/a.py', status: 'running', call_duration_ms: null, duration_ms: null });
    const g = tool('grep', { pattern: 'retries', path: '/srv/qa', status: 'running', call_duration_ms: null, duration_ms: null });
    const b = tool('read_file', { id: 'f-read2', path: '/srv/qa/app/b.py', status: 'running', call_duration_ms: null, duration_ms: null });
    const startEvent = (trace) => ({ event: 'tool', id: trace.id, name: trace.name, status: 'running', ...ROOT, ...(trace.pattern != null ? { pattern: trace.pattern, path: trace.path } : { path: trace.path }) });
    return { rows: [user('Go.'), turn([T(a)], [a])], live: true, phase: 'tools', steps: [
      { name: 'read runs', has: ['Reading app/a.py'], running: [a.id] },
      { name: 'read done', event: { event: 'tool', id: a.id, status: 'done', output: '1: x\n' },
        has: ['Read(app/a.py)', 'Read 1 line'], running: [] },
      // Once its members end, the line settles to counts even while newer
      // calls run on their own rows below ("Read 1 file", not "Reading…").
      { name: 'grep runs', event: startEvent(g),
        has: ['Read 1 file', 'Searching for retries'], lacks: ['Reading app/a.py'], running: [g.id] },
      { name: 'grep done', event: { event: 'tool', id: g.id, status: 'done', output: 'app/x.py:1:retries\n' },
        has: ['Read 1 file', 'Search(pattern: "retries", path: ".")', 'Found 1 line across 1 file'], running: [] },
      { name: 'second read runs', event: startEvent(b),
        has: ['Reading app/b.py'], running: [b.id] },
      { name: 'second read done', event: { event: 'tool', id: b.id, status: 'done', output: '1: y\n' },
        has: ['Read(app/b.py)', 'Read 1 line'], running: [] },
      { name: 'settled', remount: { live: false }, has: ['Searched for 1 pattern, read 2 files'], running: [] },
      { name: 'settled open', open: true, has: ['Read(app/a.py)', 'Search(pattern: "retries", path: ".")', 'Read(app/b.py)'] },
    ] };
  });

  // —— a folded read/search run where one call fails later ——
  add('fold fails later: the line keeps counting, the error opens inside it', () => {
    const a = tool('read_file', { path: '/srv/qa/app/a.py', output: '1: x\n' });
    const b = tool('grep', { path: '/srv/qa', pattern: 'needle', status: 'error', error: 'No such file or directory' });
    const c = tool('read_file', { path: '/srv/qa/app/b.py', output: '1: y\n' });
    return { rows: [user('Go.'), turn([say('Working.\n\n'), T(a), T(b), T(c)], [a, b, c], ended)], steps: [
      { name: 'folded', has: ['Searched for 1 pattern, read 2 files'], states: { [b.id]: 'error' } },
      { name: 'open', open: true, has: ['Read(app/a.py)', 'No such file or directory', 'Read(app/b.py)'], states: { [b.id]: 'error' } },
      // A reload of the same states keeps the line, its open rows and the
      // error exactly as they were.
      { name: 'reconcile', remount: { live: false }, labelsSame: true, has: ['No such file or directory'], states: { [b.id]: 'error' } },
    ] };
  });

  // —— Mainframe: the same settled thread, no call clocks, no "across" ——
  add('mainframe sibling: same values without Console-only extras', () => {
    const read = tool('read_file', { path: `/srv/qa/${DEEP}`, output: lines(40, 60), offset: 40, limit: 60, call_duration_ms: 3500 });
    const edit = tool('search_replace', { path: '/srv/qa/app/config.py', preview: '@@ -10,1 +10,1 @@\n-retries = 3\n+retries = 5\n' });
    const grep = tool('grep', { pattern: 'retries', path: '/srv/qa', output: 'app/a.py:12:retries = 3\napp/b.py:30:retries = 5\n' });
    const listing = tool('list_dir', { path: '/srv/qa/tests', output: 'a/\nb/\na.py\n' });
    const bad = tool('read_file', { path: '/root/secrets/id_rsa', status: 'error', error: 'permission denied: /root/secrets/id_rsa (read)' });
    return { view: 'mainframe', rows: [user('Go.'), turn([say('Working.\n\n'), T(read), T(edit), T(grep), T(listing), T(bad)], [read, edit, grep, listing, bad], ended)], steps: [
      // The edit never folds; the reads/search/listing form lines around it
      // (a refused read still counts as a read).
      { name: 'folded', has: ['Read 1 file', 'Searched for 1 pattern, read 1 file, listed 1 directory'] },
      { name: 'open', open: true, has: ['Read 60 lines', 'line 40', 'Added 1 line, removed 1 line', 'retries = 5', 'Found 2 lines', 'Listed 3 entries',
        'permission denied: /root/secrets/id_rsa (read)'],
        // Mainframe keeps the same rows without Console's per-call clocks or
        // the "across N files" suffix.
        lacks: [' · 3s', 'across 1 file'], gutter: '40' },
      // Switching back keeps every value.
      { name: 'console back', view: 'console', has: ['Read 60 lines', 'Added 1 line, removed 1 line', 'Found 2 lines across 2 files', ' · 3s'], gutter: '40' },
    ] };
  });

  return list;
}

// Applies a scenario in the page and returns one snapshot per step.
async function pageRunner(sc) {
  const log = () => document.querySelector('.chat-log');
  const labelOf = (li) => ((li.querySelector(':scope > .chat-step-main > .chat-step-head .chat-step-label') || {}).innerText || '').replace(/\s+/g, ' ').trim();
  const snap = () => {
    const rows = [...log().querySelectorAll('li.chat-step')].filter((li) => !li.closest('.chat-console-bundle'));
    const states = {};
    const running = [];
    // States come from every call row, folded inside an open summary line too.
    [...log().querySelectorAll('li.is-console-call')].forEach((li) => {
      const id = (li.dataset.step || '').replace(/^tool-/, '');
      states[id] = li.dataset.state || '';
      if (li.classList.contains('is-running')) running.push(id);
    });
    const labels = [];
    const groups = [];
    rows.forEach((li) => {
      const id = (li.dataset.step || '').replace(/^tool-/, '');
      labels.push(`${li.classList.contains('is-console-group') ? 'group' : 'call'}:${id}=${labelOf(li)}`);
      if (li.classList.contains('is-console-group')) groups.push(labelOf(li));
    });
    const audit = [];
    const seen = new Set();
    rows.forEach((li) => {
      if (li.dataset.step) { if (seen.has(li.dataset.step)) audit.push(`duplicate row ${li.dataset.step}`); seen.add(li.dataset.step); }
      if (/\bundefined\b|\bNaN\b|\[object Object\]/.test(li.innerText)) audit.push(`placeholder ${labelOf(li)}`);
    });
    if (document.scrollingElement.scrollWidth > innerWidth + 1) audit.push(`page overflow ${document.scrollingElement.scrollWidth} > ${innerWidth}`);
    return {
      flat: (log().innerText || '').replace(/[ \t]+/g, ' ').replace(/\s*\n\s*/g, ' '),
      labels, groups, states, running, audit,
      gutter: (log().querySelector('.chat-console-lines:not(.is-diff) .chat-console-n') || {}).textContent || '',
      diffRows: log().querySelectorAll('.chat-console-lines.is-diff .chat-console-nline').length,
      images: log().querySelectorAll('.chat-tool-image').length,
    };
  };
  const settle = () => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));
  fuseToolFixture.view(sc.view || 'console');
    fuseToolFixture.thinking(true);
    fuseToolFixture.mountThread(sc.rows, { live: Boolean(sc.live), phase: sc.phase || '' });
    const shots = [];
    const take = async () => { await settle(); shots.push(snap()); };
    await take();
    for (const st of sc.steps) {
      if (st.event) fuseToolFixture.toolEvent(st.event);
      if (st.remount) {
        const rows = fuseToolFixture.snapshot();
        if (st.remount.patch) {
          for (const msg of rows) for (const trace of msg.tools || []) {
            const patch = st.remount.patch[trace.id];
            if (patch) Object.assign(trace, patch);
          }
        }
        fuseToolFixture.mountThread(rows, { live: Boolean(st.remount.live), phase: st.remount.phase || '' });
      }
      if (st.open) document.querySelectorAll('.chat-log [data-step-toggle]').forEach((el) => { if (el.getAttribute('aria-expanded') === 'false') el.click(); });
      if (st.view) fuseToolFixture.view(st.view);
      await take();
    }
    return shots;
}

(async () => {
  const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'], ...(process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {}) });
  const started = Date.now();
  try {
    const page = await browser.newPage({ reducedMotion: 'reduce', viewport: { width: 1440, height: 1000 } });
    const errors = [];
    page.on('pageerror', (error) => errors.push(error.message));
    await page.route('**/*', (route) => new URL(route.request().url()).origin === origin ? route.continue() : route.abort());
    await page.goto(origin);
    await page.locator('[data-sync-label]').filter({ hasText: 'Live sync' }).waitFor({ state: 'attached' });
    await page.waitForFunction(() => window.fuseToolFixture);
    const failures = [];
    const pick = (value, view) => Array.isArray(value) ? value : (value && (value[view] || value.both)) || [];
    for (const sc of scenarios()) {
      for (const view of sc.view ? [sc.view] : ['console']) {
        for (const width of sc.wide ? [1440, 390] : [1440]) {
          await page.setViewportSize({ width, height: width === 390 ? 844 : 1000 });
          const shots = await page.evaluate(pageRunner, { ...sc, view });
          const checks = [{ name: 'mount', has: sc.has, lacks: sc.lacks, running: sc.running, states: sc.states }, ...sc.steps];
          shots.forEach((shot, index) => {
            const step = checks[index];
            const where = `${sc.name} [${view} @${width}] ${step.name || `step ${index}`}`;
            for (const want of pick(step.has, view)) if (!shot.flat.includes(want)) failures.push(`${where}: missing "${want}" — saw "${shot.flat}"`);
            for (const not of pick(step.lacks, view)) if (shot.flat.includes(not)) failures.push(`${where}: should not show "${not}" — saw "${shot.flat}"`);
            if (step.running) {
              const want = [...step.running].sort();
              const got = [...shot.running].sort();
              if (JSON.stringify(want) !== JSON.stringify(got)) failures.push(`${where}: running rows ${JSON.stringify(got)}, expected ${JSON.stringify(want)} — saw "${shot.flat}"`);
            }
            for (const [id, state] of Object.entries(step.states || {})) {
              if (shot.states[id] !== state) failures.push(`${where}: row ${id} is "${shot.states[id]}", expected "${state}" — saw "${shot.flat}"`);
            }
            if (step.gutter !== undefined && shot.gutter.trim() !== step.gutter) failures.push(`${where}: first line number "${shot.gutter.trim()}", expected "${step.gutter}"`);
            if (step.diffRows !== undefined && shot.diffRows !== step.diffRows) failures.push(`${where}: ${shot.diffRows} diff rows, expected ${step.diffRows} — saw "${shot.flat}"`);
            if (step.images !== undefined && shot.images !== step.images) failures.push(`${where}: ${shot.images} image figures, expected ${step.images}`);
            if (step.labelsSame && index > 0 && JSON.stringify(shot.labels) !== JSON.stringify(shots[index - 1].labels)) {
              failures.push(`${where}: reconcile changed the rows\n  before: ${shots[index - 1].labels.join(' | ')}\n  after:  ${shot.labels.join(' | ')}`);
            }
            for (const problem of shot.audit) failures.push(`${where}: ${problem}`);
          });
        }
      }
    }
    if (errors.length) failures.push(...errors.map((e) => `page error: ${e}`));
    const unique = [...new Set(failures)];
    if (unique.length) {
      console.error(unique.slice(0, 60).join('\n'));
      if (unique.length > 60) console.error(`… and ${unique.length - 60} more`);
    }
    assert.equal(unique.length, 0, `${unique.length} Console file/search tool problems`);
    console.log(`PASS: Console file/search tools — ${scenarios().length} scenarios in ${((Date.now() - started) / 1000).toFixed(1)}s`);
  } finally {
    await browser.close();
  }
})().catch((error) => { console.error(error); process.exit(1); });
