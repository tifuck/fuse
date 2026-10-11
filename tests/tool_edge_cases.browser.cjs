// Edge cases and update-time jitter for the tool
// families the neighboring suites pin least — load_skill, web_search/read_url,
// artifacts, image reads, spawn_subagent / list_subagents /
// get_subagent_output, todo_write's row absence, and monitor rows — plus
// expansion stability while a live turn streams: a settled row above must not
// flash open or collapse when output lands on a row below.
//
// Pinned elsewhere and deliberately NOT re-pinned here: the meta-tool
// lifecycle matrix (tests/console_meta_tools.browser.cjs), file/search rows
// (tests/console_file_tools.browser.cjs), image preview/lightbox/unsafe input
// (tests/tool_image.browser.cjs), widgets and shell-output truncation
// (tests/tool_widgets.browser.cjs), the todo section
// (tests/todo_section.browser.cjs), subagent rows, backgrounds and the
// wait-stays-done contract (tests/console_subagent_rows.browser.cjs,
// tests/command_ui.test.cjs), disclosure mechanics
// (tests/collapse_states.browser.cjs), and an expanded web search listing its
// results (commit 636f12b). Run against tests/chat_browser_fixture.py only;
// never the live Fuse service.
const assert = require('node:assert/strict');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const origin = process.env.FUSE_BROWSER_ORIGIN || 'http://127.0.0.1:8099';
assert.match(origin, /^http:\/\/127\.0\.0\.1:\d+$/);

const PNG = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4z8AAAAMBAQDJ/pLvAAAAAElFTkSuQmCC';
const PAGE = 'https://en.wikipedia.org/wiki/Ada_Lovelace';
const ROOT = { host: 'qa-box', cwd: '/srv/qa', platform: 'posix', project_root: '/srv/qa' };
let seq = 0;
const tool = (name, fields = {}) => ({
  id: `x${++seq}`, name, status: 'done', error: '', results: [], output: '', ...ROOT, ...fields,
});
const T = (t) => ({ type: 'tool', id: t.id });
const say = (text) => ({ type: 'content', text });
const turn = (tools, segments, extra = {}) => ({
  id: `a${++seq}`, role: 'assistant', model: 'qa/model', reasoning: '',
  content: segments.filter((s) => s.type === 'content').map((s) => s.text).join(''),
  tools, segments, ...extra,
});
const user = (content) => ({ role: 'user', content, reasoning: '', error: '' });
const hits = (n, prefix = 'Result') => Array.from({ length: n }, (_, i) => ({
  title: `${prefix} ${i + 1}`, url: `https://example.com/r${i + 1}`, snippet: `Snippet ${i + 1}.`,
}));

(async () => {
  const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'], ...(process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {}) });
  const failures = [];
  const check = (cond, message) => { if (!cond) failures.push(message); };
  const errors = [];
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, reducedMotion: 'reduce' });
    page.setDefaultTimeout(15000);
    page.on('pageerror', (error) => errors.push(error.message));
    await page.route('**/*', (route) => new URL(route.request().url()).origin === origin ? route.continue() : route.abort());
    await page.route('**/chat/runs/edge-child/images/ts_9_0', (route) => route.fulfill({ status: 404, body: '' }));
    await page.goto(origin);
    await page.locator('[data-sync-label]').filter({ hasText: 'Live sync' }).waitFor({ state: 'attached' });
    await page.waitForFunction(() => window.fuseToolFixture);
    await page.addStyleTag({ content: '* { animation: none !important; transition: none !important; }' });
    await page.evaluate(() => fuseToolFixture.thinking(true));

    const frame = () => page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    const width = (w) => page.setViewportSize({ width: w, height: w === 390 ? 844 : 1000 });
    const mount = (rows, live = false) => page.evaluate(([rows, live]) => {
      fuseToolFixture.todo.reset();
      fuseToolFixture.mount(rows, live);
    }, [rows, live]);
    const send = (event) => page.evaluate((event) => fuseToolFixture.toolEvent(event), event);
    const upd = (id, patch, live = false) => page.evaluate(([id, patch, live]) => fuseToolFixture.update(id, patch, live), [id, patch, live]);
    const openRow = async (id) => {
      const toggle = page.locator(`[data-step="tool-${id}"] > .chat-step-main > .chat-step-head > .chat-step-toggle`);
      if (await toggle.count() && await toggle.getAttribute('aria-expanded') !== 'true') await toggle.click();
      await frame();
    };
    const overflow = () => page.evaluate(() => document.scrollingElement.scrollWidth - window.innerWidth);
    const countSel = (sel) => page.locator(sel).count();

    // One row's settled rendering: header, result line, body, disclosure state,
    // per-family extras (web rows, kv rows, image figure, open buttons).
    const rowSnap = (id) => page.evaluate((id) => {
      const li = document.querySelector(`[data-step="tool-${id}"]`);
      if (!li) return null;
      const flat = (el) => (el ? el.innerText.replace(/\s+/g, ' ').trim() : '');
      const hang = li.querySelector(':scope > .chat-step-main .chat-console-hang');
      return {
        cls: li.className,
        state: li.dataset.state || '',
        stateTitle: li.querySelector('.chat-step-state')?.getAttribute('title') || '',
        labelTitle: li.querySelector('.chat-step-label')?.getAttribute('title') || '',
        head: flat(li.querySelector('.chat-step-head .chat-console-call')),
        result: flat(li.querySelector('.chat-console-result-text')),
        body: flat(li.querySelector('.chat-console-hang-body')),
        hangExpanded: Boolean(hang && hang.classList.contains('is-expanded')),
        rowExpanded: li.classList.contains('is-console-expanded'),
        toggleAria: [...li.querySelectorAll('[data-step-toggle]')].map((el) => el.getAttribute('aria-expanded')),
        webRows: [...li.querySelectorAll('.chat-web-row')].map((el) => ({
          title: flat(el.querySelector('.chat-web-title')), href: el.getAttribute('href') || '',
        })),
        more: [...li.querySelectorAll('.chat-console-more')].map(flat),
        kvRows: [...li.querySelectorAll('.chat-kv-row')].map(flat),
        openArtifact: li.querySelector('[data-open-artifact]')?.getAttribute('data-open-artifact') || '',
        openAgent: li.querySelector('[data-open-agent]')?.getAttribute('data-open-agent') || '',
        images: li.querySelectorAll('.chat-tool-image').length,
        fig: flat(li.querySelector('.chat-tool-image figcaption')),
        text: flat(li),
        dom: li.textContent || '',
      };
    }, id);
    const rowOf = (id) => rowSnap(id);
    const eq = (actual, expected, where) => check(actual === expected, `${where}: ${JSON.stringify(actual)} !== ${JSON.stringify(expected)}`);

    // ————————————————————————————————————————————————
    // load_skill: semantic content (name, loaded indication, outcomes) plus
    // the condensed settled row — one line carrying call and outcome
    // together, no second sub-line, with the loaded body opening on demand.
    // ————————————————————————————————————————————————
    console.log('edge: skill load');
    await width(1440);
    const singleLine = (id) => page.evaluate((id) => {
      const li = document.querySelector(`[data-step="tool-${id}"]`);
      const head = li.querySelector(':scope > .chat-step-main > .chat-step-head');
      const label = li.querySelector('.chat-step-label');
      return {
        liH: li.getBoundingClientRect().height,
        headH: head.getBoundingClientRect().height,
        labelH: label.getBoundingClientRect().height,
        result: (li.querySelector('.chat-console-result-text')?.innerText || '').trim(),
        visible: li.innerText.replace(/\s+/g, ' ').trim(),
      };
    }, id);
    try {
      const sk = tool('load_skill', { preview: 'café-🚀-über', output: 'Loaded.\n\n# Café 🚀\n\nBe excellent.', call_duration_ms: 900 });
      await mount([turn([sk], [T(sk)])]);
      await frame();
      let row = await rowOf(sk.id);
      eq(row.head, 'Loaded the café-🚀-über skill', 'skill done line is the outcome sentence');
      eq(row.result, '', 'the settled skill row keeps no second sub-line');
      eq(row.state, 'done', 'skill done state');
      eq(row.stateTitle, 'Done', 'skill done dot title');
      // The settled default row is a single line: the row is exactly as tall
      // as its head.
      const one = await singleLine(sk.id);
      check(Math.abs(one.liH - one.headH) <= 1, `the settled skill row is a single line: row ${one.liH}px vs head ${one.headH}px`);
      check(/loaded/i.test(one.visible) && one.visible.includes('café-🚀-über'), `the one line carries the name and the outcome: ${one.visible}`);
      await openRow(sk.id);
      row = await rowOf(sk.id);
      check(row.body.includes('Café') && row.body.includes('Be excellent'), `skill body keeps the loaded content: ${row.body}`);
      check(!row.body.includes('Loaded.'), `skill body drops the transport prelude: ${row.body}`);

      // A missing preview falls back to the bare noun, and the outcome stands.
      const bare = tool('load_skill', { output: 'Loaded.\n\n# Bare\n\nFine.' });
      await mount([turn([bare], [T(bare)])]);
      await frame();
      row = await rowOf(bare.id);
      eq(row.head, 'Loaded the skill', 'skill without preview');
      eq(row.result, '', 'skill without preview keeps one line');

      // While it runs the line is progressive; the family's running mark
      // hangs under it.
      const bad = tool('load_skill', { preview: 'nope', status: 'running', output: '' });
      await mount([turn([bad], [T(bad)])], true);
      await frame();
      row = await rowOf(bad.id);
      eq(row.head, 'Loading the nope skill', 'skill running line');
      eq(row.result, 'Running…', 'skill running mark');
      await send({ event: 'tool', id: bad.id, name: 'load_skill', status: 'error', preview: 'nope', error: "Unknown skill 'nope'." });
      await frame();
      row = await rowOf(bad.id);
      eq(row.head, 'Failed to load the nope skill', 'skill failed line keeps the name');
      check(!row.head.includes('loaded'), `a failed skill never claims loaded: ${row.head}`);
      check(row.body.includes('Error:') && row.body.includes("Unknown skill 'nope'."), `skill failure keeps the family Error: mark: ${row.body}`);
      eq(row.state, 'error', 'skill failed state');
      eq(row.stateTitle, 'Failed', 'skill failed dot title');

      // A lost result reads as lost, not as a generic stop.
      const lost = tool('load_skill', { preview: 'ponytail', status: 'interrupted', execution_status: 'lost' });
      await mount([turn([lost], [T(lost)])]);
      await frame();
      row = await rowOf(lost.id);
      eq(row.head, 'Loading the ponytail skill', 'an interrupted load keeps its in-flight line');
      eq(row.result, 'Interrupted · the result was lost', 'skill lost result');
      check(!row.head.includes('loaded'), `a lost skill never claims loaded: ${row.head}`);
      eq(row.state, 'interrupted', 'skill lost state');
      eq(row.stateTitle, 'Lost', 'skill lost dot title');

      // A very long skill name truncates with an ellipsis and never widens
      // the page, at both widths.
      for (const w of [1440, 390]) {
        await width(w);
        const long = tool('load_skill', { preview: `skill-${'x'.repeat(160)}`, output: 'Loaded.\n\n# Long\n\nFine.' });
        await mount([turn([long], [T(long)])]);
        await frame();
        row = await rowOf(long.id);
        check(row.head.startsWith('Loaded the ') && row.head.endsWith(' skill'), `skill long head keeps the sentence: ${row.head}`);
        check(row.head.includes('…'), `skill long name truncates with an ellipsis: ${row.head}`);
        check(row.head.length < 120, `skill long name stays one row: ${row.head.length} chars`);
        check((await overflow()) <= 0, `skill long name overflows at ${w}px: ${await overflow()}`);
        const line = await singleLine(long.id);
        check(Math.abs(line.liH - line.headH) <= 1, `the long skill row stays a single line at ${w}px: row ${line.liH}px vs head ${line.headH}px`);
      }
      await width(1440);

      // Remount preserves the compact skill sentence and Unicode name.
      await mount([turn([sk], [T(sk)])]);
      await frame();
      row = await rowOf(sk.id);
      eq(row.head, 'Loaded the café-🚀-über skill', 'default skill line reads the same sentence');
      eq(row.result, '', 'default skill row keeps no second sub-line');
      const mf = await singleLine(sk.id);
      check(mf.liH < mf.labelH * 2, `the default skill row is one line: row ${mf.liH}px vs line ${mf.labelH}px`);
      check(/loaded/i.test(mf.visible) && mf.visible.includes('café-🚀-über'), `default one line carries the name and the outcome: ${mf.visible}`);
    } catch (error) { failures.push(`block threw: ${error.message}`); }

    // ————————————————————————————————————————————————
    // web_search / read_url: empty and oversized result sets, the results
    // count label matching the actual rows, long URLs/titles, unsafe URLs,
    // error / timed out / lost, missing optional fields, truncation honesty.
    // ————————————————————————————————————————————————
    console.log('edge: web search + fetch');
    try {
      // Empty results: the search still reports its query count and latency,
      // and claims no rows.
      const quiet = tool('web_search', { query: 'quiet', queries: ['quiet'], latency_ms: 240, results: [] });
      await mount([turn([quiet], [T(quiet)])]);
      await frame();
      let row = await rowOf(quiet.id);
      eq(row.head, 'Web Search("quiet")', 'empty search head');
      eq(row.result, 'Did 1 search in 240ms', 'empty search result line');
      eq(row.webRows.length, 0, 'empty search shows no result rows');
      eq(row.more.length, 0, 'empty search shows no overflow marker');

      // Many results: 8 shown, the overflow marker names the hidden count and
      // shown + hidden equals the actual results.
      const many = tool('web_search', { query: 'many', queries: ['many', 'more'], latency_ms: 840, results: hits(20) });
      await mount([turn([many], [T(many)])]);
      await frame();
      row = await rowOf(many.id);
      eq(row.result, 'Did 2 searches in 840ms', 'many results count label');
      await openRow(many.id);
      row = await rowOf(many.id);
      eq(row.webRows.length, 8, 'many results shows the first page');
      eq(row.more.join(''), '… +12 more', 'many results overflow marker');
      check(row.webRows.length + 12 === many.results.length, `shown + hidden equals the actual results: ${row.webRows.length} + 12 vs ${many.results.length}`);
      eq(row.webRows[0].title, 'Result 1', 'result titles render in order');
      eq(row.webRows[0].href, 'https://example.com/r1', 'result links point at their urls');

      // Truncation boundary: exactly 8 results fill the page with no marker;
      // one more adds exactly one hidden row.
      for (const [n, shown, marker] of [[8, 8, ''], [9, 8, '… +1 more']]) {
        const set = tool('web_search', { query: `set${n}`, queries: [`set${n}`], results: hits(n) });
        await mount([turn([set], [T(set)])]);
        await frame();
        await openRow(set.id);
        row = await rowOf(set.id);
        eq(row.webRows.length, shown, `${n} results page size`);
        eq(row.more.join(''), marker, `${n} results overflow marker`);
      }

      // Unsafe or unparseable URLs are dropped, not linked.
      const mixed = tool('web_search', {
        query: 'mixed', queries: ['mixed'], results: [
          { title: 'Safe', url: 'https://example.com/ok', snippet: 'fine' },
          { title: 'Evil', url: 'javascript:alert(1)', snippet: 'x' },
          { title: 'Rel', url: '/relative/path', snippet: 'x' },
        ],
      });
      await mount([turn([mixed], [T(mixed)])]);
      await frame();
      await openRow(mixed.id);
      row = await rowOf(mixed.id);
      eq(row.webRows.length, 1, 'unsafe urls are not rendered as rows');
      eq(row.webRows[0].href, 'https://example.com/ok', 'the safe url survives');

      // Long titles and URLs: the row truncates or scrolls, never breaks the
      // grid or widens the page.
      for (const w of [1440, 390]) {
        await width(w);
        const wide = tool('web_search', {
          query: 'wide', queries: ['wide'], results: [{
            title: `A title that goes on and on ${'and on '.repeat(30)}end`,
            url: `https://example.com/${'documentation/'.repeat(12)}index`,
            snippet: 'x',
          }],
        });
        await mount([turn([wide], [T(wide)])]);
        await frame();
        await openRow(wide.id);
        check((await overflow()) <= 0, `long result row overflows at ${w}px: ${await overflow()}`);
        const bounds = await page.evaluate(() => {
          const list = document.querySelector('[data-step] .chat-web-list');
          const box = list.getBoundingClientRect();
          return [...list.querySelectorAll('.chat-web-row')].map((el) => {
            const r = el.getBoundingClientRect();
            return { left: Math.round(r.left * 10) / 10, right: Math.round(r.right * 10) / 10 };
          }).concat([{ left: Math.round(box.left * 10) / 10, right: Math.round(box.right * 10) / 10, isBox: true }]);
        });
        const container = bounds.pop();
        check(bounds.every((r) => r.left >= container.left - 1 && r.right <= container.right + 1),
          `long result row leaves the grid at ${w}px: ${JSON.stringify(bounds)} in ${JSON.stringify(container)}`);
      }
      await width(1440);

      // A fetch's result line reports what actually arrived.
      const fetch = tool('read_url', {
        url: PAGE, http_status: 200, bytes: 12600,
        results: [{ title: 'Ada Lovelace', url: PAGE, snippet: 'Augusta Ada King wrote notes on the Analytical Engine.' }],
      });
      await mount([turn([fetch], [T(fetch)])]);
      await frame();
      row = await rowOf(fetch.id);
      eq(row.head, `Fetch(${PAGE})`, 'fetch head');
      eq(row.result, 'Received 12.3KB (200 OK)', 'fetch result line names bytes and status');
      await openRow(fetch.id);
      row = await rowOf(fetch.id);
      eq(row.webRows.length, 1, 'fetch lists its source');
      check(row.body.includes('Analytical Engine'), `fetch body keeps the snippet: ${row.body}`);

      // Missing optional fields: no bytes falls back to the preview, then to
      // the page host; no http_status is 200.
      const noBytes = tool('read_url', { url: 'https://example.com/doc', http_status: 200, results: [{ title: 'Doc', url: 'https://example.com/doc', snippet: 'x' }] });
      const noPreview = tool('read_url', { url: 'https://example.com/other', http_status: 200, results: [] });
      await mount([turn([noBytes, noPreview], [T(noBytes), T(noPreview)])]);
      await frame();
      eq((await rowOf(noBytes.id)).result, 'example.com', 'fetch without bytes falls back to the host');
      eq((await rowOf(noPreview.id)).result, 'example.com', 'fetch without bytes or preview falls back to the host');

      // A non-2xx response is not labelled OK: the parenthetical carries the
      // status alone. 2xx keeps its "(200 OK)" phrase, pinned above.
      const missing = tool('read_url', { url: 'https://example.com/gone', http_status: 404, bytes: 512, results: [] });
      await mount([turn([missing], [T(missing)])]);
      await frame();
      row = await rowOf(missing.id);
      eq(row.result, 'Received 512 bytes (404)', 'a 404 receipt names its status without claiming OK');

      // Error, timed out and lost each keep their own words.
      const failed = tool('read_url', { url: 'https://example.com/flaky', status: 'error', error: 'DNS lookup failed.' });
      await mount([turn([failed], [T(failed)])]);
      await frame();
      row = await rowOf(failed.id);
      check(row.body.includes('DNS lookup failed.'), `fetch failure shows its error: ${row.body}`);
      eq(row.state, 'error', 'fetch failure state');
      eq(row.stateTitle, 'Failed', 'fetch failure dot title');

      const timedOut = tool('read_url', { url: 'https://example.com/slow', status: 'error', execution_status: 'timed_out', error: 'timed out' });
      await mount([turn([timedOut], [T(timedOut)])]);
      await frame();
      row = await rowOf(timedOut.id);
      eq(row.state, 'error', 'timed out fetch state');
      eq(row.stateTitle, 'Timed out', 'timed out fetch dot title');

      const lostFetch = tool('read_url', { url: 'https://example.com/lost', status: 'interrupted', execution_status: 'lost' });
      await mount([turn([lostFetch], [T(lostFetch)])]);
      await frame();
      row = await rowOf(lostFetch.id);
      eq(row.result, 'Interrupted · the result was lost', 'lost fetch result');
      eq(row.stateTitle, 'Lost', 'lost fetch dot title');

      // Truncation honesty: the shown snippet is a prefix of the real one and
      // ends with an ellipsis, never a cut word passed off as whole.
      const truth = 'abcdefghij'.repeat(150);
      const snip = tool('read_url', {
        url: 'https://example.com/long', http_status: 200, bytes: 2000,
        results: [{ title: 'Long', url: 'https://example.com/long', snippet: truth }],
      });
      await mount([turn([snip], [T(snip)])]);
      await frame();
      await openRow(snip.id);
      const shown = await page.evaluate(() => {
        const pre = document.querySelector('[data-step] .chat-console-hang-body pre');
        return pre ? pre.innerText.trim() : '';
      });
      check(shown.endsWith('…'), `truncated snippet marks the cut: …${shown.slice(-10)}`);
      eq(shown, truth.slice(0, 1199) + '…', 'truncated snippet is exactly the kept prefix');

      // Two fetches keep their own content; nothing crosses between rows.
      const first = tool('read_url', { url: 'https://example.com/first', http_status: 200, bytes: 100, results: [{ title: 'First', url: 'https://example.com/first', snippet: 'AAA_SNIPPET first' }] });
      const second = tool('read_url', { url: 'https://example.com/second', http_status: 200, bytes: 100, results: [{ title: 'Second', url: 'https://example.com/second', snippet: 'BBB_SNIPPET second' }] });
      await mount([turn([first, second], [T(first), T(second)])]);
      await frame();
      await openRow(first.id);
      await openRow(second.id);
      const a = await rowOf(first.id);
      const b = await rowOf(second.id);
      check(a.dom.includes('AAA_SNIPPET') && !a.dom.includes('BBB_SNIPPET'), `first fetch keeps only its own content: ${a.dom.slice(0, 200)}`);
      check(b.dom.includes('BBB_SNIPPET') && !b.dom.includes('AAA_SNIPPET'), `second fetch keeps only its own content: ${b.dom.slice(0, 200)}`);
    } catch (error) { failures.push(`block threw: ${error.message}`); }

    // ————————————————————————————————————————————————
    // create_artifact / update_artifact: status labels, titles, expanded
    // content, missing optional fields.
    // ————————————————————————————————————————————————
    console.log('edge: artifacts');
    try {
      const art = tool('create_artifact', { title: 'Sales dashboard', identifier: 'art-1', version: 3, visibility: 'private', output: 'Artifact body saved.' });
      await mount([turn([art], [T(art)])]);
      await frame();
      let row = await rowOf(art.id);
      eq(row.head, 'Artifact(Sales dashboard)', 'artifact head names the title');
      eq(row.result, 'Created v3', 'artifact created label carries the version');
      eq(row.stateTitle, 'Done', 'artifact done dot title');
      eq(row.openArtifact, 'art-1', 'artifact row opens its artifact');
      await openRow(art.id);
      row = await rowOf(art.id);
      check(row.body.includes('Artifact body saved.'), `artifact body keeps its content: ${row.body}`);

      const rev = tool('update_artifact', { title: 'Sales dashboard', identifier: 'art-1', version: 4, output: 'Artifact body updated.' });
      await mount([turn([rev], [T(rev)])]);
      await frame();
      eq((await rowOf(rev.id)).result, 'Updated v4', 'artifact updated label carries the version');

      // Missing optional fields: no version prints a bare outcome, no title
      // falls back to the noun, no identifier offers no open button.
      const bare = tool('create_artifact', { output: 'Saved.' });
      await mount([turn([bare], [T(bare)])]);
      await frame();
      row = await rowOf(bare.id);
      eq(row.head, 'Artifact(artifact)', 'artifact without a title');
      eq(row.result, 'Created', 'artifact without a version');
      eq(row.openArtifact, '', 'artifact without an identifier offers no open button');

      const bad = tool('update_artifact', { title: 'Sales dashboard', identifier: 'art-1', status: 'error', error: 'Invalid artifact content.' });
      await mount([turn([bad], [T(bad)])]);
      await frame();
      row = await rowOf(bad.id);
      check(row.body.includes('Invalid artifact content.'), `artifact failure shows its error: ${row.body}`);
      eq(row.state, 'error', 'artifact failure state');
      eq(row.stateTitle, 'Failed', 'artifact failure dot title');

      // Unicode and very long titles truncate with an ellipsis and never
      // widen the page.
      for (const w of [1440, 390]) {
        await width(w);
        const long = tool('create_artifact', { title: `🚀 ${'é'.repeat(160)}`, identifier: 'art-2', version: 1, output: 'Saved.' });
        await mount([turn([long], [T(long)])]);
        await frame();
        row = await rowOf(long.id);
        check(row.head.includes('🚀'), `unicode artifact title renders: ${row.head.slice(0, 40)}`);
        check(row.head.includes('…'), `long artifact title truncates with an ellipsis: ${row.head.slice(0, 100)}`);
        check(row.head.length < 120, `long artifact title stays one row: ${row.head.length} chars`);
        check((await overflow()) <= 0, `long artifact title overflows at ${w}px: ${await overflow()}`);
      }
      await width(1440);
    } catch (error) { failures.push(`block threw: ${error.message}`); }

    // ————————————————————————————————————————————————
    // Image reads: preview presence, dimensions text edge cases, animated
    // frame counts, and the error when the preview is unavailable.
    // ————————————————————————————————————————————————
    console.log('edge: images');
    try {
      // Frame counts print exactly when the image is animated.
      const anim = tool('read_file', {
        path: 'shots/anim.png', image: { mime: 'image/png', data: PNG, width: 640, height: 480, format: 'PNG', frames: 5, frame: 0 },
      });
      await mount([turn([anim], [T(anim)])]);
      await frame();
      let row = await rowOf(anim.id);
      eq(row.images, 1, 'image read renders one preview');
      check(row.fig.includes('640 × 480'), `image dimensions text: ${row.fig}`);
      check(row.fig.includes('frame 1 of 5'), `animated image names its frame: ${row.fig}`);
      check(!row.fig.includes('resized from'), `an unresized image claims no resize: ${row.fig}`);

      const still = tool('read_file', {
        path: 'shots/still.png', image: { mime: 'image/png', data: PNG, width: 640, height: 480, format: 'PNG', frames: 1, frame: 0 },
      });
      await mount([turn([still], [T(still)])]);
      await frame();
      row = await rowOf(still.id);
      check(!row.fig.includes('frame'), `a still image prints no frame count: ${row.fig}`);

      // Missing dimensions: the figure shows just the format.
      const dimless = tool('read_file', { path: 'shots/mystery.png', image: { mime: 'image/png', data: PNG, format: 'PNG' } });
      await mount([turn([dimless], [T(dimless)])]);
      await frame();
      row = await rowOf(dimless.id);
      check(row.fig.includes('PNG'), `image without dimensions keeps its format: ${row.fig}`);
      check(!row.fig.includes('×'), `image without dimensions prints no size: ${row.fig}`);

      // A jpeg preview renders like any other.
      const jpeg = tool('read_file', { path: 'shots/pic.jpg', image: { mime: 'image/jpeg', data: PNG, width: 10, height: 10, format: 'JPEG' } });
      await mount([turn([jpeg], [T(jpeg)])]);
      await frame();
      eq((await rowOf(jpeg.id)).images, 1, 'jpeg preview renders');

      // A preview that 404s says so instead of showing a broken frame. The
      // call folds into the summary line, so open that first: only a visible
      // preview loads (and only a loaded one can fail).
      const gone = tool('read_file', {
        path: 'shots/gone.png', image: { mime: 'image/png', preview_url: '/chat/runs/edge-child/images/ts_9_0', width: 10, height: 10, format: 'PNG' },
      });
      await mount([turn([gone], [T(gone)])]);
      await frame();
      const summary = page.locator('.is-console-group > .chat-step-main > .chat-step-head > .chat-step-toggle');
      if (await summary.count() && await summary.getAttribute('aria-expanded') !== 'true') await summary.click();
      const goneRow = page.locator(`[data-step="tool-${gone.id}"]`);
      await goneRow.locator('.chat-tool-image img').evaluate((img) => { img.decode().catch(() => {}); }).catch(() => {});
      await page.waitForFunction(() => {
        const li = document.querySelector('[data-step^="tool-"]');
        return li && /no longer be available/.test(li.innerText);
      }, null, { timeout: 4000 }).catch(() => {});
      row = await rowOf(gone.id);
      check(/no longer be available/.test(row.text), `a dead preview explains itself: ${row.text}`);
      eq(await goneRow.locator('[data-image-preview]').count(), 0, 'a dead preview shows no broken button');
    } catch (error) { failures.push(`block threw: ${error.message}`); }

    // ————————————————————————————————————————————————
    // spawn_subagent / list_subagents / get_subagent_output: what the label
    // says was spawned, a failed spawn's error, list rows counting the actual
    // children, and await labels with missing or unicode fields.
    // ————————————————————————————————————————————————
    console.log('edge: subagents');
    try {
      // The row says what was spawned: the description in the head, the model
      // where the full label shows it.
      const spawn = tool('spawn_subagent', {
        status: 'running', description: 'Audit the pool', preview: 'Audit the pool',
        subagent_type: 'explore', model: 'grok-4', child_id: 'kid-1',
      });
      await mount([turn([spawn], [T(spawn)])], true);
      await frame();
      let row = await rowOf(spawn.id);
      eq(row.head, 'Explore(Audit the pool)', 'spawn head names the child kind and description');
      check(row.labelTitle.includes('Audit the pool') && row.labelTitle.includes('grok-4'), `spawn label names description and model: ${row.labelTitle}`);
      // The terminal update comes through the trace's own fields (the agent
      // feed path is console_subagent_rows' case).
      await upd(spawn.id, { status: 'done', agent_tool_uses: 1, agent_tokens: 840, agent_work_ms: 41000 }, true);
      await frame();
      row = await rowOf(spawn.id);
      eq(row.result, 'Done (1 tool use · 840 tokens · 41s)', 'spawn done counts');
      eq(row.state, 'done', 'spawn done state');

      // Missing optional fields: no description and no child still reads as an
      // agent call, and offers no open button.
      const bare = tool('spawn_subagent', { status: 'running' });
      await mount([turn([bare], [T(bare)])], true);
      await frame();
      row = await rowOf(bare.id);
      eq(row.head, 'Agent(subagent)', 'spawn without a description');
      eq(row.openAgent, '', 'spawn without a child offers no open button');

      // A spawn that fails outright shows the error on its own row.
      const badSpawn = tool('spawn_subagent', { status: 'running', description: 'Ship it', preview: 'Ship it', child_id: 'kid-2' });
      await mount([turn([badSpawn], [T(badSpawn)])], true);
      await frame();
      await send({ event: 'tool', id: badSpawn.id, name: 'spawn_subagent', status: 'error', error: 'provider refused the spawn' });
      await frame();
      row = await rowOf(badSpawn.id);
      check(row.body.includes('provider refused the spawn'), `failed spawn shows its error: ${row.body}`);
      eq(row.state, 'error', 'failed spawn state');
      eq(row.stateTitle, 'Failed', 'failed spawn dot title');

      // list_subagents rows count the actual children.
      const five = tool('list_subagents', {
        preview: '5 children', output: JSON.stringify({
          live: [
            { id: 'c1', description: 'Rewrite auth', status: 'running', model: 'grok-4' },
            { id: 'c2', description: 'Ship docs', status: 'running' },
          ],
          finished: [
            { id: 'c3', description: 'Prior audit', status: 'completed' },
            { id: 'c4', description: 'Flaky test', status: 'failed' },
            { id: 'c5', description: 'Stopped job', status: 'cancelled' },
          ],
        }),
      });
      await mount([turn([five], [T(five)])]);
      await frame();
      await openRow(five.id);
      row = await rowOf(five.id);
      eq(row.head, 'Agents', 'list_subagents head');
      eq(row.kvRows.length, 5, 'list rows equal the children');
      check(row.kvRows[0].includes('Rewrite auth') && row.kvRows[0].includes('running · grok-4'), `list row names child and state: ${row.kvRows[0]}`);
      check(row.kvRows.some((line) => line.includes('Flaky test') && line.includes('failed')), `list rows keep failure states: ${row.kvRows.join(' | ')}`);

      // Past the page limit the overflow marker names the hidden children.
      const fifteen = tool('list_subagents', {
        preview: '15 children', output: JSON.stringify({
          live: Array.from({ length: 10 }, (_, i) => ({ id: `l${i}`, description: `Live ${i}`, status: 'running' })),
          finished: Array.from({ length: 5 }, (_, i) => ({ id: `f${i}`, description: `Done ${i}`, status: 'completed' })),
        }),
      });
      await mount([turn([fifteen], [T(fifteen)])]);
      await frame();
      await openRow(fifteen.id);
      row = await rowOf(fifteen.id);
      eq(row.kvRows.length, 12, 'list rows cap at the page size');
      eq(row.more.join(''), '… +3 more', 'list overflow marker names the hidden children');
      check(row.kvRows.length + 3 === 15, 'shown + hidden children equal the actual children');

      // An await with missing fields still reads as a wait on something.
      const awaitBare = tool('get_subagent_output', { status: 'done' });
      await mount([turn([awaitBare], [T(awaitBare)])]);
      await frame();
      row = await rowOf(awaitBare.id);
      eq(row.head, 'Await(subagent)', 'await without a description');
      eq(row.result, 'Agent "subagent" finished', 'await without a duration');

      // Unicode descriptions survive the label (preview cleanup normalises
      // punctuation to spaces); the duration joins the line.
      const awaitUni = tool('get_subagent_output', { status: 'done', description: 'Über-🚀-audit', preview: 'Über-🚀-audit', child_id: 'kid-3', agent_work_ms: 41000 });
      await mount([turn([awaitUni], [T(awaitUni)])]);
      await frame();
      row = await rowOf(awaitUni.id);
      eq(row.head, 'Await(Über 🚀 audit)', 'await names its child');
      eq(row.result, 'Agent "Über 🚀 audit" finished · 41s', 'await result line');
    } catch (error) { failures.push(`block threw: ${error.message}`); }

    // ————————————————————————————————————————————————
    // todo_write: it never prints a transcript row. The section is
    // todo_section.browser.cjs's; this pins only the row absence plus the one
    // state that suite misses (interrupted): no row, and its list stays in the
    // section/line machinery exactly like the code treats a landed call.
    // ————————————————————————————————————————————————
    console.log('edge: todo row absence');
    try {
      const tw = tool('todo_write', { status: 'interrupted', todos: [{ id: '1', content: 'Edge check', status: 'completed' }] });
      await mount([turn([tw], [T(tw)])]);
      await frame();
      eq(await countSel(`[data-tool="todo_write"]`), 0, 'an interrupted todo_write prints no transcript row');
      eq(await countSel('[data-todo-line]'), 1, 'the interrupted call still leaves its settle line (its list arrived)');
      const doneTw = tool('todo_write', { status: 'done', todos: [{ id: '1', content: 'Edge check', status: 'completed' }] });
      const errTw = tool('todo_write', { status: 'error', error: 'todos[0].content is required' });
      await mount([turn([doneTw, errTw], [T(doneTw), T(errTw)])]);
      await frame();
      eq(await countSel(`[data-tool="todo_write"]`), 0, 'settled todo_write calls print no transcript rows');
    } catch (error) { failures.push(`block threw: ${error.message}`); }

    // ————————————————————————————————————————————————
    // Monitor rows: the start receipt while it watches, the event count
    // tracking the events actually delivered, and a killed watch showing
    // stopped, never running.
    // ————————————————————————————————————————————————
    console.log('edge: monitors');
    try {
      const watch = tool('monitor', {
        status: 'running', description: 'boot log', command: 'tail -F boot.log',
        command_id: 'm1', child_id: 'm1', job_id: 'm1', timeout_ms: 300000, event_count: 0,
      });
      await mount([turn([watch], [T(watch)])], true);
      await frame();
      let row = await rowOf(watch.id);
      eq(row.head, 'Monitor(boot log)', 'monitor head names what it watches');
      eq(row.result, 'Monitor started · task m1 (timeout: 5m)', 'running monitor names its deadline');
      // Events arrive as updates; the count label tracks the delivered count.
      for (const n of [1, 3]) {
        await upd(watch.id, { event_count: n }, true);
        await frame();
      }
      // A killed watch settles as stopped with its reason, never as running.
      await send({ event: 'tool', id: watch.id, name: 'monitor', status: 'interrupted', error: 'cancelled', termination_confirmed: true });
      await frame();
      row = await rowOf(watch.id);
      eq(row.result, 'Interrupted · stopped', 'killed watch says stopped');
      eq(row.state, 'interrupted', 'killed watch state');
      eq(row.stateTitle, 'Stopped', 'killed watch dot title');
      check(!row.cls.includes('is-running'), `killed watch is not running: ${row.cls}`);
      check(!row.text.includes('Monitor started'), `killed watch drops its start receipt: ${row.text}`);

      // The settled result line keeps the start receipt; the event count rides
      // the job row's detail chips (chat-tool-context).
      const counted = tool('monitor', { description: 'worker log', command: 'tail -F worker.log', command_id: 'm2', event_count: 3 });
      await mount([turn([counted], [T(counted)])]);
      await frame();
      let snap = await rowOf(counted.id);
      eq(snap.result, 'Monitor started · task m2', 'the settled monitor keeps its start receipt');
      const jobChips = async (id, trace) => {
        await page.evaluate(({ id, trace }) => fuseToolFixture.work([{ ...trace, command_id: undefined, id, kind: 'monitor', state: 'running' }]), { id, trace });
        await page.locator(`[data-show-job="${id}"]`).click();
        return page.locator('.chat-jobs-detail .chat-tool-context').innerText();
      };
      let chips = await jobChips('watch-m2', counted);
      check(/3 events?/.test(chips), `the count chip carries the delivered events: ${chips}`);
      await upd(counted.id, { event_count: 1 });
      await frame();
      snap = await rowOf(counted.id);
      eq(snap.result, 'Monitor started · task m2', 'the receipt survives a count update');
      const updated = await page.evaluate((id) => fuseToolFixture.snapshot()[0].tools.find((t) => t.id === id), counted.id);
      chips = await jobChips('watch-m2', updated);
      check(/1 event/.test(chips), `the count chip tracks a single event: ${chips}`);
      check(!/3 events?/.test(chips), `the count chip drops stale counts: ${chips}`);
      const uncounted = tool('monitor', { description: 'idle log', command: 'tail -F idle.log' });
      await mount([turn([uncounted], [T(uncounted)])]);
      await frame();
      snap = await rowOf(uncounted.id);
      eq(snap.result, 'Monitor started', 'a watch with no count keeps its start receipt');
      chips = await jobChips('watch-idle', uncounted);
      check(!/\d+ events?/.test(chips), `a watch with no count carries no count chip: ${chips}`);
    } catch (error) { failures.push(`block threw: ${error.message}`); }

    // ————————————————————————————————————————————————
    // UPDATE JITTER: while a live turn streams, rows above the churn keep
    // their disclosure state and content — no flash open/collapse across two
    // rAF frames, no scroll jumps, and a terminal update landing on a
    // collapsed row updates its label without expanding it.
    // ————————————————————————————————————————————————
    const tall = `Setup paragraph ${'with some words '.repeat(20)}.\n\n`.repeat(24);
    const jitter = async (w) => {
      await width(w);
      const fetchA = tool('web_search', { query: 'alpha', queries: ['alpha'], latency_ms: 300, results: hits(2, 'Alpha') });
      const fetchB = tool('read_url', { url: 'https://example.com/stream', status: 'running', output: '', results: [] });
      const skillC = tool('load_skill', { preview: 'ponytail', output: 'Loaded.\n\n# Ponytail\n\nBe lazy.', call_duration_ms: 300 });
      const fetchD = tool('read_url', { url: 'https://example.com/last', status: 'running', output: '', results: [] });
      // A and C sit above every update; B and D take the churn below the prose.
      const segs = (tail = '') => [say(tall), T(fetchA), T(skillC), say('Checking the sources.\n\n' + tail), T(fetchB), T(fetchD)];
      await page.evaluate(({ rows }) => {
        fuseToolFixture.thinking(true);
        fuseToolFixture.mountThread(rows, { live: true, phase: 'tools' });
        fuseToolFixture.follow(false);
      }, { rows: [user('Go.'), turn([fetchA, skillC, fetchB, fetchD], segs(), { elapsedMs: 9000 })] });
      await frame();
      const where = `${w}px`;

      // The user opens row A above; row C stays collapsed. The thread is
      // scrolled away from the bottom with follow off.
      await openRow(fetchA.id);
      let a = await rowOf(fetchA.id);
      check(a.rowExpanded && a.hangExpanded, `${where} fetchA opens: ${a.cls}`);
      const bodyBefore = a.body;
      const scroll = await page.evaluate(() => {
        const thread = document.querySelector('[data-chat-thread]');
        if (thread.scrollHeight <= thread.clientHeight + 300) return { tall: false };
        thread.scrollTop = Math.min(240, thread.scrollHeight - thread.clientHeight - 40);
        return { tall: true, top: thread.scrollTop, following: fuseToolFixture.scrollState().following };
      });
      check(scroll.tall, `${where} the jitter thread is tall enough to scroll`);
      check(scroll.following === false, `${where} follow is off before the churn: ${JSON.stringify(scroll)}`);

      // Churn on rows BELOW (B and D): growing output, streaming prose, a
      // status nudge — while A and C must stay exactly as they are.
      const steps = [];
      for (let i = 1; i <= 3; i += 1) {
        steps.push({ call: 'toolEvent', args: [{ event: 'tool', id: fetchB.id, name: 'read_url', status: 'running', output: `stream line ${i}\n`.repeat(i), preview: `stream line ${i}` }] });
        steps.push({ call: 'stream', args: [1, { content: tall + 'Checking the sources.\n\n' + `Turn text ${i} `, segments: segs(`Turn text ${i} `) }] });
        steps.push({ call: 'toolEvent', args: [{ event: 'tool', id: fetchD.id, name: 'read_url', status: 'running', output: `tail ${i}`, results: [] }] });
      }
      const sigOf = (id) => page.evaluate((id) => {
        const li = document.querySelector(`[data-step="tool-${id}"]`);
        if (!li) return 'MISSING';
        const hang = li.querySelector(':scope > .chat-step-main .chat-console-hang');
        const toggles = [...li.querySelectorAll('[data-step-toggle]')].map((el) => el.getAttribute('aria-expanded')).join(',');
        return [li.className, hang ? hang.className : '', toggles].join(' | ');
      }, id);
      const watch = [fetchA.id, skillC.id];
      const before = Object.fromEntries(await Promise.all(watch.map(async (id) => [id, await sigOf(id)])));
      const frames = Object.fromEntries(watch.map((id) => [id, []]));
      for (const step of steps) {
        await page.evaluate(([call, args]) => fuseToolFixture[call](...args), [step.call, step.args]);
        for (let f = 0; f < 2; f += 1) {
          await frame();
          for (const id of watch) frames[id].push(await sigOf(id));
        }
      }
      const samples = { before, frames };

      for (const id of [fetchA.id, skillC.id]) {
        const list = samples.frames[id];
        check(list.length > 0, `${where} row ${id} was sampled across frames`);
        const flips = list.filter((s) => s !== samples.before[id]);
        check(flips.length === 0, `${where} row ${id} flipped across frames: ${JSON.stringify([samples.before[id], ...new Set(flips)])}`);
      }
      // (a) the settled row above stays collapsed; (b) the user-opened row
      // stays open with its content.
      const c = await rowOf(skillC.id);
      check(!c.rowExpanded && !c.hangExpanded, `${where} skillC stays collapsed through the churn: ${c.cls}`);
      a = await rowOf(fetchA.id);
      check(a.rowExpanded && a.hangExpanded, `${where} fetchA stays open through the churn: ${a.cls}`);
      eq(a.body, bodyBefore, `${where} fetchA keeps its content`);
      check(a.toggleAria.length > 0 && a.toggleAria.every((v) => v === 'true'), `${where} fetchA toggles keep their aria state: ${a.toggleAria}`);
      // (c) nothing scrolled.
      const after = await page.evaluate(() => {
        const thread = document.querySelector('[data-chat-thread]');
        return { top: thread.scrollTop, following: fuseToolFixture.scrollState().following };
      });
      eq(after.top, scroll.top, `${where} churn below does not scroll the thread`);
      eq(after.following, false, `${where} churn below does not resume follow`);

      // A terminal update on the collapsed row D: its label moves on, the row
      // stays closed.
      const beforeD = await rowOf(fetchD.id);
      check(['Fetching…', 'Running…'].includes(beforeD.result), `${where} fetchD is fetching before its result: ${beforeD.result}`);
      await send({ event: 'tool', id: fetchD.id, name: 'read_url', status: 'done', url: 'https://example.com/last', http_status: 200, bytes: 2048, results: [{ title: 'Last', url: 'https://example.com/last', snippet: 'LAST_CONTENT' }] });
      await frame();
      await frame();
      const d = await rowOf(fetchD.id);
      // The live event carries the result list but not the byte count (that
      // arrives with the persisted snapshot), so the label moves to the page
      // host at once and becomes the receipt at the settle below.
      check(d.result !== beforeD.result && !/Fetching|Running/.test(d.result), `${where} fetchD label updates while collapsed: ${d.result}`);
      eq(d.result, 'example.com', `${where} fetchD live label names the page host`);
      eq(d.head, beforeD.head, `${where} fetchD head stays its own`);
      check(!d.rowExpanded && !d.hangExpanded, `${where} fetchD does not expand on its terminal update: ${d.cls}`);
      check(d.toggleAria.every((v) => v === 'false'), `${where} fetchD toggle stays closed: ${d.toggleAria}`);
      check(d.dom.includes('LAST_CONTENT'), `${where} fetchD carries its result: ${d.dom.slice(0, 200)}`);

      // The live turn settles from the persisted message (which carries the
      // fetch's byte count): the open row keeps its disclosure, the collapsed
      // one keeps its state, and the receipt lands without expanding.
      await page.evaluate((fetchDId) => {
        const f = fuseToolFixture;
        const msgs = f.snapshot();
        const trace = (msgs[1].tools || []).find((t) => t.id === fetchDId);
        if (trace) { trace.bytes = 2048; trace.http_status = 200; }
        f.run({ status: 'completed' });
        f.progress(1, { message: msgs[1] });
      }, fetchD.id);
      await frame();
      a = await rowOf(fetchA.id);
      const settled = await rowOf(skillC.id);
      const settledD = await rowOf(fetchD.id);
      check(a.rowExpanded && a.hangExpanded, `${where} fetchA stays open at the settle: ${a.cls}`);
      eq(a.body, bodyBefore, `${where} fetchA keeps its content at the settle`);
      check(!settled.rowExpanded && !settled.hangExpanded, `${where} skillC stays collapsed at the settle: ${settled.cls}`);
      eq(settledD.result, 'Received 2KB (200 OK)', `${where} the settle brings the fetch receipt`);
      check(!settledD.rowExpanded && !settledD.hangExpanded, `${where} fetchD stays collapsed at the settle: ${settledD.cls}`);
    };

    for (const w of [1440, 390]) {
      try {
        console.log(`edge: jitter ${w}px`);
        await jitter(w);
      } catch (error) {
        failures.push(`jitter ${w}px threw: ${error.message}`);
      }
    }

    check(errors.length === 0, `page errors: ${errors.join('; ')}`);
    const unique = [...new Set(failures)];
    if (unique.length) console.error(unique.join('\n'));
    assert.equal(unique.length, 0, `${unique.length} tool edge-case problems`);
    console.log('PASS: tool edge cases — skill load, web search/fetch, artifacts, images, subagents, todos, monitors, and update jitter');
  } finally {
    await browser.close();
  }
})().catch((error) => { console.error(error); process.exit(1); });
