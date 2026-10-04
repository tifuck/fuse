// Streaming feel: a reader following the bottom sees text fill in smoothly.
//
//  1. Pinned: the view moves with the content in the same frame, so after any
//     chunk the scroll gap is 0 on every frame (it used to trail by 25-50px and
//     glide up, which read as text appearing under the composer).
//  2. Paced: a burst of text is spread over a few frames at a steady rate, never
//     painted as a block, never lost, and always complete within a fraction of
//     a second.
//  3. No lag when calm: a small chunk shows on the frame it arrives.
//  4. Finishing: a burst still being revealed when the turn ends completes, and
//     the final text is exact.
//  5. Reduced motion shows text whole, immediately.
const assert = require('node:assert/strict');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const origin = process.env.FUSE_BROWSER_ORIGIN || 'http://127.0.0.1:8099';
assert.match(origin, /^http:\/\/127\.0\.0\.1:\d+$/);

const BURST = 'A burst of streamed text that arrives all at once and should be spread over a few frames. '.repeat(5);

(async () => {
  const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'], ...(process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {}) });
  const failures = [];
  const check = (cond, message) => { if (!cond) failures.push(message); };
  try {
    for (const reduced of [false, true]) {
      const page = await browser.newPage({ reducedMotion: reduced ? 'reduce' : 'no-preference', viewport: { width: 1280, height: 800 } });
      const errors = [];
      page.on('pageerror', (error) => errors.push(error.message));
      await page.route('**/*', (route) => new URL(route.request().url()).origin === origin ? route.continue() : route.abort());
      await page.goto(origin);
      await page.waitForFunction(() => window.fuseToolFixture);
      for (const view of ['console', 'mainframe', 'cli', 'compact']) {
        const tag = (what) => `${what} [${view}${reduced ? ', reduced motion' : ''}]`;
        const run = await page.evaluate(async ({ view, burst }) => {
          fuseToolFixture.view(view);
          await new Promise((r) => requestAnimationFrame(r));
          const rows = [];
          for (let i = 0; i < 14; i++) {
            rows.push({ role: 'user', content: `Question ${i}` });
            const text = 'Answer '.repeat(30) + i;
            rows.push({ role: 'assistant', content: text, tools: [], segments: [{ type: 'content', text }] });
          }
          rows.push({ role: 'user', content: 'Go.' });
          rows.push({ id: 'live', role: 'assistant', content: 'Start. ', tools: [], segments: [{ type: 'content', text: 'Start. ' }] });
          fuseToolFixture.mountThread(rows, { live: true, phase: 'model' });
          fuseToolFixture.follow(true);
          await new Promise((r) => setTimeout(r, 300));
          const index = fuseToolFixture.snapshot().length - 1;
          const thread = document.querySelector('[data-chat-thread]');
          const frame = () => new Promise((r) => requestAnimationFrame(r));
          const lastMd = () => [...document.querySelectorAll('.chat-md')].pop();
          const chars = () => lastMd().textContent.length;
          const gap = () => Math.round(thread.scrollHeight - thread.clientHeight - thread.scrollTop);
          const push = (text) => fuseToolFixture.stream(index, { content: text, segments: [{ type: 'content', text }] });

          // A burst: sample every frame.
          let text = 'Start. ';
          const base = chars();
          text += '\n\n' + burst;
          const gaps = [];
          const shown = [];
          push(text);
          const t0 = performance.now();
          while (performance.now() - t0 < 700) {
            gaps.push(gap());
            shown.push(chars() - base);
            await frame();
          }
          const full = shown[shown.length - 1];

          // A calm chunk: visible by the next frame.
          const before = chars();
          text += ' ok';
          push(text);
          await frame();
          await frame();
          const calm = chars() - before;

          // A burst cut short by the turn ending still completes.
          text += '\n\n' + burst;
          push(text);
          await frame();
          fuseToolFixture.context.live(false);
          fuseToolFixture.repaint();
          await new Promise((r) => setTimeout(r, 600));
          return { gaps, shown, full, calm, final: lastMd().textContent, expectLength: text.replace(/\n\n/g, '').length, finalGap: gap(), burstLength: burst.length };
        }, { view, burst: BURST });
        check(Math.max(...run.gaps) <= 1, tag(`the view trails the content by up to ${Math.max(...run.gaps)}px`));
        check(run.full >= run.burstLength - 3, tag(`the whole burst appears (${run.full} of ${run.burstLength})`));
        const early = run.shown.slice(0, 3);
        if (reduced) check(early[0] >= run.burstLength - 3, tag(`reduced motion shows the burst whole (${early})`));
        else {
          check(early[0] < run.burstLength * 0.5, tag(`a burst is not painted as one block (first frame ${early[0]} of ${run.burstLength})`));
          check(run.shown.every((n, i) => i === 0 || n >= run.shown[i - 1]), tag('text only ever grows while it is revealed'));
          const done = run.shown.findIndex((n) => n >= run.burstLength - 3);
          check(done >= 4 && done <= 30, tag(`the burst is spread over a few frames (completed at frame ${done})`));
        }
        check(run.calm >= 3, tag(`a calm chunk shows on the frame it arrives (${run.calm} chars)`));
        check(run.final.length >= run.expectLength - 6, tag(`a burst still revealing when the turn ends completes (${run.final.length} of ${run.expectLength})`));
        check(run.finalGap <= 1, tag(`the view ends pinned (${run.finalGap}px)`));
      }
      assert.deepEqual(errors, [], `page errors: ${errors.join('; ')}`);
      await page.close();
    }
  } finally {
    await browser.close();
  }
  assert.equal(failures.length, 0, `${failures.length} streaming problems\n${failures.join('\n')}`);
  console.log('PASS: streaming — pinned follow, paced bursts, calm chunks, clean finish, reduced motion in 4 views');
})().catch((error) => { console.error(error); process.exit(1); });
