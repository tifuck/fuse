// Streaming text integrity. Isolated fixture only; no provider or host traffic.
//
// Pins what the other stream suites leave open:
//  1. Jump-to-latest: hidden while pinned or nudged, shown once genuinely away,
//     no trailing new-output dot, and its box does not shift when the live
//     spinner replaces the arrow.
//  2. Follow forgiveness: a wheel inside a code pane, a ctrl-zoom and a sideways
//     swipe keep following; scrolling up mid-stream pauses it and later output
//     does not drag the reader back.
//  3. The live status line ends the transcript (after the log, not pinned over
//     it) and scrolls away with the reply.
//  4. A live thinking row survives a view switch mid-stream with its text and
//     without the previous view's markup.
//  5. Streamed markdown at awkward chunk boundaries: a fence, a table and a link
//     split inside their tokens render once, with no duplicated or swallowed text.
//  6. A long answer streamed in truncated pieces equals the text the fixture
//     emitted, in every reading mode.
//  7. Reading text scale moves recap, status and transcript metadata with the
//     answer text.
const assert = require('node:assert/strict');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const origin = process.env.FUSE_BROWSER_ORIGIN || 'http://127.0.0.1:8099';
assert.match(origin, /^http:\/\/127\.0\.0\.1:\d+$/);

const VIEWS = ['cli', 'compact', 'console', 'mainframe'];

(async () => {
  const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'], ...(process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {}) });
  const failures = [];
  const check = (cond, message) => { if (!cond) failures.push(message); };
  const errors = [];
  try {
    const page = await browser.newPage({ viewport: { width: 1280, height: 800 }, reducedMotion: 'no-preference' });
    page.on('pageerror', (error) => errors.push(error.message));
    await page.route('**/*', (route) => new URL(route.request().url()).origin === origin ? route.continue() : route.abort());
    await page.goto(origin);
    await page.waitForFunction(() => window.fuseToolFixture);

    // —— 1 + 2 + 3. Pill, forgiveness, status line. ——
    for (const view of VIEWS) {
      const tag = (what) => `${what} [${view}]`;
      const run = await page.evaluate(async (view) => {
        const f = fuseToolFixture;
        f.view(view);
        await new Promise((r) => requestAnimationFrame(r));
        const rows = [];
        for (let i = 0; i < 16; i++) {
          rows.push({ role: 'user', content: `Question ${i}` });
          const text = `Settled answer ${i}. ` + 'A paragraph of earlier reading. '.repeat(24);
          rows.push({ role: 'assistant', content: text, tools: [], segments: [{ type: 'content', text }] });
        }
        const code = '```js\n' + Array.from({ length: 40 }, (_, i) => `const line${i} = ${i};`).join('\n') + '\n```\n\n';
        let live = 'Opening. ' + code;
        rows.push({ role: 'user', content: 'Stream.' });
        rows.push({ id: 'live', role: 'assistant', content: live, tools: [], segments: [{ type: 'content', text: live }] });
        f.mountThread(rows, { live: true, phase: 'model' });
        f.follow(true);
        await new Promise((r) => setTimeout(r, 500));
        const thread = document.querySelector('[data-chat-thread]');
        const log = document.querySelector('[data-chat-log]');
        const dock = document.querySelector('[data-live-dock]');
        const pill = document.querySelector('[data-chat-latest]');
        const frame = () => new Promise((r) => requestAnimationFrame(r));
        const gap = () => thread.scrollHeight - thread.clientHeight - thread.scrollTop;
        const index = f.snapshot().length - 1;
        const push = (text) => f.stream(index, { content: text, segments: [{ type: 'content', text }] });
        const pillState = () => {
          const box = pill.getBoundingClientRect();
          const arrow = pill.querySelector('.chat-latest-arrow');
          const spin = pill.querySelector('.chat-activity-spin');
          const shown = (el) => Boolean(el) && getComputedStyle(el).display !== 'none';
          return {
            hidden: pill.hidden,
            label: [...pill.childNodes].filter((node) => node.nodeType === Node.TEXT_NODE).map((node) => node.textContent.trim()).join(''),
            extras: [...pill.children].filter((el) => el !== arrow && el !== spin).map((el) => el.className),
            arrow: shown(arrow),
            spin: shown(spin),
            live: pill.classList.contains('is-live'),
            width: Math.round(box.width),
          };
        };

        const pinned = { gap: Math.round(gap()), pill: pillState(), following: f.scrollState().following };
        const order = dock.compareDocumentPosition(log);
        const dockAfterLog = Boolean(order & Node.DOCUMENT_POSITION_PRECEDING);
        // The dock is the thread's last flex child, so it ends the scroll range.
        const dockEnds = Math.abs(thread.scrollHeight - dock.offsetTop - dock.offsetHeight) <= 2;

        // A small nudge is still the end: no pill.
        thread.scrollTop -= 40;
        thread.dispatchEvent(new Event('scroll'));
        await frame();
        const nudged = { gap: Math.round(gap()), pill: pillState(), following: f.scrollState().following };

        // Genuinely away: the pill shows, and a burst below does not drag us back.
        const awayPx = Math.max(200, Math.round(thread.clientHeight * 0.5));
        thread.scrollTop -= awayPx;
        thread.dispatchEvent(new Event('scroll'));
        await frame();
        const awayTop = thread.scrollTop;
        const awayBefore = { gap: Math.round(gap()), pill: pillState(), following: f.scrollState().following };
        const widthBefore = pillState().width;
        live += '\n\n' + 'More output arrives below the reader. '.repeat(30);
        push(live);
        await new Promise((r) => setTimeout(r, 400));
        const awayAfter = {
          moved: Math.round(thread.scrollTop - awayTop),
          following: f.scrollState().following,
          pill: pillState(),
          widthDelta: pillState().width - widthBefore,
        };

        // Back at the end: the pill hides and the spinner hands back to the line.
        f.follow(true);
        await new Promise((r) => setTimeout(r, 500));
        const returned = { gap: Math.round(gap()), pill: pillState(), dockHidden: dock.hidden };

        // Forgiveness: a wheel inside the code pane, a ctrl-zoom and a sideways
        // swipe must not pause following.
        const pre = document.querySelector('[data-content-block] pre');
        pre.scrollTop = 20;
        pre.dispatchEvent(new WheelEvent('wheel', { deltaY: -40, bubbles: true, cancelable: true }));
        await frame();
        const wheelInside = f.scrollState().following;
        thread.dispatchEvent(new WheelEvent('wheel', { deltaY: -40, deltaX: 0, ctrlKey: true, bubbles: true }));
        thread.dispatchEvent(new WheelEvent('wheel', { deltaY: 4, deltaX: 80, bubbles: true }));
        await frame();
        const zoomAndSwipe = f.scrollState().following;

        // Scrolling up the transcript itself does pause it.
        thread.dispatchEvent(new WheelEvent('wheel', { deltaY: -80, bubbles: true }));
        thread.scrollTop = Math.max(0, thread.scrollTop - 220);
        thread.dispatchEvent(new Event('scroll'));
        await frame();
        const paused = !f.scrollState().following;
        const pausedTop = thread.scrollTop;
        live += '\n\nStill writing while the reader looks back.';
        push(live);
        await new Promise((r) => setTimeout(r, 250));
        const held = Math.abs(thread.scrollTop - pausedTop) <= 1 && !f.scrollState().following;

        // The dock and the log share the stage as their offset parent, so the
        // dock follows the transcript when it starts where the log ends.
        const last = log.lastElementChild;
        const follows = last && dock.offsetTop >= last.offsetTop + last.offsetHeight - 1 && dock.offsetTop - (last.offsetTop + last.offsetHeight) <= 16;
        const awayPlace = { dock: dock.offsetTop, logEnd: last ? last.offsetTop + last.offsetHeight : -1, scroll: Math.round(thread.scrollTop) };

        return { pinned, dockAfterLog, dockEnds, nudged, awayBefore, awayAfter, returned, wheelInside, zoomAndSwipe, paused, held, follows, awayPlace };
      }, view);

      check(run.pinned.gap <= 1 && run.pinned.following, tag(`pinned to the end (gap ${run.pinned.gap})`));
      check(run.pinned.pill.hidden, tag('no Jump to latest while pinned'));
      check(run.dockAfterLog, tag('the live status line follows the transcript'));
      check(run.dockEnds, tag('the live status line ends the transcript'));
      check(run.nudged.pill.hidden, tag(`a small nudge (${run.nudged.gap}px) shows no pill`));
      check(!run.awayBefore.pill.hidden, tag(`the pill appears once away (${run.awayBefore.gap}px)`));
      check(!run.awayBefore.following, tag('scrolling away pauses following'));
      check(run.awayBefore.pill.label === 'Jump to latest', tag(`pill text is only its label (${run.awayBefore.pill.label})`));
      check(run.awayBefore.pill.extras.length === 0, tag(`pill has no trailing new-output mark (${run.awayBefore.pill.extras})`));
      // Half a screen is already past the status row, so the pill has taken its
      // spinner; the arrow returns once the row is back on screen.
      check(run.awayBefore.pill.spin && !run.awayBefore.pill.arrow, tag('away from the status line, the pill carries its spinner'));
      check(Math.abs(run.awayAfter.moved) <= 2 && !run.awayAfter.following, tag(`later output does not drag a reader who scrolled up (${run.awayAfter.moved}px)`));
      check(Math.abs(run.awayAfter.widthDelta) <= 8, tag(`the pill width stays put when the spinner replaces the arrow (${run.awayAfter.widthDelta}px)`));
      check(run.returned.gap <= 1 && run.returned.pill.hidden && !run.returned.dockHidden, tag('returning to the end hides the pill and shows the status line'));
      check(run.wheelInside, tag('a wheel inside a code pane keeps following'));
      check(run.zoomAndSwipe, tag('ctrl-zoom and a sideways swipe keep following'));
      check(run.paused && run.held, tag('scrolling up mid-stream holds the reading position'));
      check(run.follows, tag(`the status line sits at the end of the log, so it scrolls with the reply (dock ${run.awayPlace.dock}, log ends ${run.awayPlace.logEnd})`));
    }

    // —— 4. Thinking rows survive a view switch mid-stream. ——
    {
      const thought = 'First I check the config.\n\nThen I ' + 'reconsider the fallback path. '.repeat(12);
      const switched = await page.evaluate(async (thought) => {
        const f = fuseToolFixture;
        f.thinking(true);
        f.view('console');
        await new Promise((r) => requestAnimationFrame(r));
        f.mountThread([
          { role: 'user', content: 'Think.' },
          { id: 'think', role: 'assistant', content: '', reasoning: thought, tools: [], segments: [{ type: 'reasoning', text: thought }] },
        ], { live: true, phase: 'model' });
        await new Promise((r) => setTimeout(r, 60));
        const before = document.querySelector('[data-chat-log]').innerText.replace(/\s+/g, ' ');
        f.view('cli');
        await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));
        const log = document.querySelector('[data-chat-log]');
        const row = log.querySelector('[data-step^="think-"]');
        const preview = row && row.querySelector('[data-reason-preview] .chat-think-markdown');
        const more = thought + ' And one more clause after the switch.';
        f.stream(1, { reasoning: more, segments: [{ type: 'reasoning', text: more }] });
        await new Promise((r) => requestAnimationFrame(r));
        const body = preview ? preview.innerText : '';
        return {
          before,
          classes: row ? row.className : '',
          consoleMarkup: Boolean(log.querySelector('.chat-console-bundle, .chat-console-thought, .is-console-group')),
          preview: body,
          live: Boolean(row && row.classList.contains('is-live-think')),
        };
      }, thought);
      check(!/chat-console|is-console/.test(switched.classes), `a think row rebuilt for Terminal (classes: ${switched.classes})`);
      check(!switched.consoleMarkup, 'Console markup does not survive into Terminal');
      check(switched.live, 'the thought stays live after the view switch');
      check(switched.preview.includes('reconsider the fallback path'), 'the thought text survives the view switch');
      check(switched.preview.includes('one more clause after the switch'), 'streaming continues into the rebuilt thought');
      check((switched.preview.match(/one more clause after the switch/g) || []).length === 1, 'the streamed addition is not doubled');
    }

    // —— 5 + 6. Chunk boundaries and a long answer, every view. ——
    const answer = [
      'Before the fence the prose stays whole.',
      '',
      '```python',
      'def answer(value):',
      '    return value + 1',
      '```',
      '',
      'A table of results follows.',
      '',
      '| Name | Count |',
      '| --- | --- |',
      '| alpha | 3 |',
      '| beta | 12 |',
      '',
      'See the [reference](https://example.com/docs) for the rest.',
      '',
      'Closing sentence that must not be swallowed. ' + 'Filler word '.repeat(400),
    ].join('\n');
    const splits = [0, 4, answer.indexOf('value'), answer.indexOf('| beta'), answer.indexOf(']('), answer.indexOf('https'), answer.length - 7];
    for (const view of VIEWS) {
      const tag = (what) => `${what} [${view}]`;
      const rendered = await page.evaluate(async ({ view, answer, splits }) => {
        const f = fuseToolFixture;
        f.view(view);
        await new Promise((r) => requestAnimationFrame(r));
        f.mountThread([
          { role: 'user', content: 'Write it.' },
          { id: 'live', role: 'assistant', content: '', tools: [], segments: [{ type: 'content', text: '' }] },
        ], { live: true, phase: 'model' });
        const index = f.snapshot().length - 1;
        const push = (text) => f.stream(index, { content: text, segments: [{ type: 'content', text }] });
        let text = '';
        for (const at of splits) {
          text = answer.slice(0, at);
          push(text);
          await new Promise((r) => requestAnimationFrame(r));
        }
        push(answer);
        f.context.live(false);
        f.repaint();
        await new Promise((r) => setTimeout(r, 700));
        const md = [...document.querySelectorAll('[data-content-block]')].pop();
        const fence = md.querySelector('pre code');
        const table = md.querySelector('table');
        const link = md.querySelector('a');
        return {
          text: md.innerText,
          fences: md.querySelectorAll('pre').length,
          code: fence ? fence.textContent : '',
          rows: table ? table.querySelectorAll('tbody tr').length : 0,
          cells: table ? [...table.querySelectorAll('td')].map((el) => el.textContent) : [],
          href: link ? link.getAttribute('href') : '',
          linkText: link ? link.textContent : '',
          links: md.querySelectorAll('a').length,
        };
      }, { view, answer, splits });
      const body = rendered.text.replace(/\s+/g, ' ');
      check(body.includes('Before the fence the prose stays whole'), tag('prose before a split fence survives'));
      check(body.includes('Closing sentence that must not be swallowed'), tag('the tail of a long answer is not truncated'));
      check((body.match(/Before the fence/g) || []).length === 1, tag('opening prose is not duplicated'));
      check((body.match(/Closing sentence/g) || []).length === 1, tag('closing prose is not duplicated'));
      check((body.match(/Filler word/g) || []).length === 400, tag(`every streamed word arrives once (saw ${(body.match(/Filler word/g) || []).length})`));
      check(rendered.fences === 1, tag(`one fence, not ${rendered.fences}`));
      check(rendered.code.includes('return value + 1') && !rendered.code.includes('```'), tag(`fence body is the code only (${rendered.code.slice(0, 80)})`));
      check(rendered.rows === 2 && rendered.cells.includes('alpha') && rendered.cells.includes('12'), tag(`table rows survive a split (${rendered.rows}: ${rendered.cells})`));
      check(rendered.links === 1 && rendered.href === 'https://example.com/docs' && rendered.linkText === 'reference', tag(`one link with its target (${rendered.links}, ${rendered.href}, ${rendered.linkText})`));
      check(!rendered.text.includes(']('), tag('the link markup is not shown raw'));
    }

    // —— 7. Reading text scale reaches recap, status and transcript metadata. ——
    for (const view of VIEWS) {
      const tag = (what) => `${what} [${view}]`;
      const scaled = await page.evaluate(async (view) => {
        const f = fuseToolFixture;
        f.view(view);
        await new Promise((r) => requestAnimationFrame(r));
        const text = 'Scaled answer body that the recap should match.';
        f.mountThread([
          { role: 'user', content: 'Scale.' },
          { id: 'ans', role: 'assistant', content: text, elapsedMs: 4200, usage: { prompt_tokens: 120, completion_tokens: 80 }, tools: [], segments: [{ type: 'content', text }] },
          { role: 'user', content: 'And keep going.' },
          { id: 'live', role: 'assistant', content: 'Still writing.', tools: [], segments: [{ type: 'content', text: 'Still writing.' }] },
        ], { live: true, phase: 'model' });
        await new Promise((r) => requestAnimationFrame(r));
        const article = document.querySelector('[data-message-id="ans"]');
        const recap = document.createElement('p');
        recap.className = 'chat-recap';
        recap.innerHTML = '<span class="chat-recap-text">Recap of the scaled answer.</span>';
        article.append(recap);
        const size = (sel) => {
          const el = document.querySelector(sel);
          return el ? parseFloat(getComputedStyle(el).fontSize) : 0;
        };
        const sample = () => ({
          body: size('[data-message-id="ans"] .chat-md'),
          recap: size('.chat-recap'),
          status: size('[data-live-dock] .chat-status-verb'),
          meta: size('[data-message-id="ans"] .chat-foot-meta'),
        });
        FuseChatAppearance.setSize(100);
        const base = sample();
        FuseChatAppearance.setSize(130);
        const up = sample();
        FuseChatAppearance.setSize(100);
        return { base, up };
      }, view);
      const ratio = (part) => scaled.up[part] / scaled.base[part];
      for (const part of ['body', 'recap', 'status', 'meta']) {
        check(Math.abs(ratio(part) - 1.3) < 0.02, tag(`${part} follows the reading scale (x${ratio(part).toFixed(3)})`));
      }
      check(Math.abs(scaled.base.recap - scaled.base.body) < 0.6, tag(`recap matches the answer text (${scaled.base.recap}px vs ${scaled.base.body}px)`));
      check(Math.abs(scaled.up.recap - scaled.up.body) < 0.6, tag(`recap still matches the answer text when scaled (${scaled.up.recap}px vs ${scaled.up.body}px)`));
    }

    assert.deepEqual(errors, [], `page errors: ${errors.join('; ')}`);
  } finally {
    await browser.close();
  }
  assert.equal(failures.length, 0, `${failures.length} stream integrity problems\n${failures.join('\n')}`);
  console.log('PASS: stream integrity — pill, follow forgiveness, status line, view-switch thinking, chunk boundaries, long answers, reading scale');
})().catch((error) => { console.error(error); process.exit(1); });
