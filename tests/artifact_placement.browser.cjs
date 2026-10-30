// Where an artifact shows up in the transcript, from a real turn: the message
// is sent through the composer, the server's real loop runs (the scripted
// provider in chat_browser_fixture.py makes the create/edit_artifact call,
// the artifact is really stored, and the model's reply follows the directions
// the real tool result gives). Every artifact the reply made or changed gets
// one card after the reply text; links to it inside the text read as plain
// links, never as buttons in a sentence; a line holding only that link is
// dropped in favour of the card.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const origin = process.env.FUSE_BROWSER_ORIGIN || 'http://127.0.0.1:8099';
assert.match(origin, /^http:\/\/127\.0\.0\.1:\d+$/);
const shots = process.env.LOG_DIR || '/tmp/fuse-browser-tests';

(async () => {
  const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'], ...(process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {}) });
  const errors = [];
  try {
    for (const [width, height] of [[1280, 900], [390, 844]]) {
      const page = await browser.newPage({ viewport: { width, height }, reducedMotion: 'reduce' });
      page.on('pageerror', (error) => errors.push(error.message));
      await page.route('**/*', (route) => new URL(route.request().url()).origin === origin ? route.continue() : route.abort());
      await page.goto(origin);
      await page.locator('[data-sync-label]').filter({ hasText: 'Live sync' }).waitFor({ state: 'attached' });
      await page.evaluate(() => { try { localStorage.setItem('fuse.artifact.autoOpen', '0'); } catch (_) {} });
      // Phones open on the chat list; a chat starts from New chat, as a user does.
      if (await page.evaluate(() => document.querySelector('.chat').classList.contains('history-home'))) {
        await page.locator('[data-chat-new]:visible').first().click();
        await page.waitForFunction(() => !document.querySelector('.chat').classList.contains('history-home'));
      }
      const input = page.locator('#chat-input');
      const articles = page.locator('[data-chat-log] article.chat-turn.is-assistant');

      // One real turn: send, then wait for the reply text and the run to end.
      const turn = async (text, done) => {
        const before = await articles.count();
        await input.fill(text);
        await page.locator('[data-chat-send]').click();
        await page.waitForFunction(([n, done]) => {
          const all = [...document.querySelectorAll('[data-chat-log] [data-flow]')];
          return all.length > n && all.at(-1).textContent.includes(done);
        }, [before, done], { timeout: 30000 });
        await page.waitForFunction(() => !document.querySelector('[data-chat-send].is-stop'), null, { timeout: 30000 });
        await page.waitForTimeout(300);
        return articles.last();
      };

      // What the reader sees of artifacts in one message.
      const layout = (article) => article.evaluate((el) => {
        const flow = el.querySelector('[data-flow]');
        const blocks = [...flow.querySelectorAll('[data-content-block]')];
        const textBottom = Math.max(0, ...blocks.map((b) => b.getBoundingClientRect().bottom));
        const links = [...flow.querySelectorAll('[data-content-block] a[data-open-artifact]')].map((a) => {
          const style = getComputedStyle(a);
          const p = a.closest('p, li') || a.parentElement;
          const lineHeight = parseFloat(getComputedStyle(p).lineHeight) || 20;
          return {
            display: style.display,
            border: parseFloat(style.borderTopWidth) || 0,
            padding: parseFloat(style.paddingTop) || 0,
            tall: a.getBoundingClientRect().height / lineHeight,
            alone: p.textContent.trim() === a.textContent.trim(),
          };
        });
        const cards = [...el.querySelectorAll('[data-artifact-cards] [data-open-artifact]')].map((card) => ({
          id: card.getAttribute('data-open-artifact'),
          text: card.textContent.replace(/\s+/g, ' ').trim(),
          top: card.getBoundingClientRect().top,
          width: card.getBoundingClientRect().width,
          flowWidth: flow.getBoundingClientRect().width,
        }));
        return { links, cards, textBottom, rowOpen: el.querySelectorAll('.chat-step [data-open-artifact]').length };
      });

      const check = async (article, label, { title, meta, linkInText }) => {
        const seen = await layout(article);
        fs.writeFileSync(`${shots}/artifact-placement-${width}-${label}.json`, JSON.stringify(seen, null, 1));
        await article.screenshot({ path: `${shots}/artifact-placement-${width}-${label}.png` });
        assert.equal(seen.cards.length, 1, `${width} ${label}: one card per artifact, got ${JSON.stringify(seen.cards)}`);
        const [card] = seen.cards;
        assert.match(card.text, new RegExp(title), `${width} ${label}: card names the artifact: ${card.text}`);
        assert.match(card.text, meta, `${width} ${label}: card says what happened: ${card.text}`);
        assert.ok(card.top >= seen.textBottom - 1, `${width} ${label}: card sits after the reply text (${card.top} < ${seen.textBottom})`);
        assert.ok(card.width <= card.flowWidth + 1, `${width} ${label}: card stays inside the message column`);
        for (const link of seen.links) {
          assert.equal(link.display, 'inline', `${width} ${label}: an artifact link in text is inline text, not a button`);
          assert.equal(link.border, 0, `${width} ${label}: no button border on a link in a sentence`);
          assert.ok(link.tall < 1.35, `${width} ${label}: the link does not stretch its line (${link.tall.toFixed(2)} lines)`);
          assert.equal(link.alone, false, `${width} ${label}: a line holding only the link duplicates the card`);
        }
        assert.equal(seen.links.length, linkInText, `${width} ${label}: ${seen.links.length} artifact links left in the text`);
        return card;
      };

      // A model following today's directions: no link, the card carries it.
      let article = await turn('Artifact fixture line: make a Q3 revenue board', 'Ask if you want churn added');
      const first = await check(article, 'follows-directions', { title: 'Q3 revenue board', meta: /Created · v1/, linkInText: 0 });

      // Out of habit a model may still glue a link into a sentence.
      article = await turn('Artifact fixture habit: make another board', 'It splits revenue by region');
      const habit = await check(article, 'habit-link', { title: 'Q3 revenue board', meta: /Created · v1/, linkInText: 1 });

      // Most stored replies put the link alone on its own line: the card replaces it.
      article = await turn('Artifact fixture own-line: one more board', 'Ask if you want churn added');
      const ownLine = await check(article, 'own-line-link', { title: 'Q3 revenue board', meta: /Created · v1/, linkInText: 0 });
      assert.equal((await article.locator('[data-flow]').innerText()).includes('View the Q3 revenue board'), false, `${width}: the link line is gone`);

      // A change to the first one gets its own card in that reply.
      article = await turn('Artifact fixture update the first board', 'Ask if you want churn added');
      const edited = await check(article, 'edit', { title: 'Q3 revenue board', meta: /Edited · v2/, linkInText: 0 });
      assert.equal(edited.id, first.id, `${width}: the edit card points at the first artifact`);

      // The card opens the pane on that artifact.
      await article.locator('[data-artifact-cards] [data-open-artifact]').click();
      await page.locator('[data-chat-artifact]').waitFor({ state: 'visible' });
      assert.match(await page.locator('[data-artifact-frame]').getAttribute('src'), new RegExp(`/a/${first.id}/content`));

      // Deleted or archived later: the card stays as a record but says so and
      // no longer opens a pane that cannot load it.
      for (const [id, action] of [[habit.id, 'delete'], [ownLine.id, 'archive']]) {
        const status = await page.evaluate(([id, action]) => fetch(`/a/${id}/${action}`, {
          method: 'POST', credentials: 'same-origin', body: '{}', headers: {
            'Content-Type': 'application/json', 'X-CSRF-Token': document.querySelector('meta[name="csrf-token"]').content,
          },
        }).then((res) => res.status), [id, action]);
        assert.equal(status, 200, `${width}: ${action} ${id}`);
      }
      await page.reload();
      await page.locator('[data-artifact-cards] [data-open-artifact]').first().waitFor({ state: 'attached', timeout: 30000 });
      const gone = await page.evaluate(() => [...document.querySelectorAll('[data-artifact-cards] .chat-artifact-card')]
        .map((card) => ({ text: card.textContent.replace(/\s+/g, ' ').trim(), opens: card.hasAttribute('data-open-artifact') })));
      await articles.nth(1).screenshot({ path: `${shots}/artifact-placement-${width}-deleted.png` });
      assert.equal(gone.length, 4, `${width}: every reply keeps its card: ${JSON.stringify(gone)}`);
      assert.deepEqual(gone.map((card) => card.opens), [true, false, false, true], `${width}: only live artifacts open: ${JSON.stringify(gone)}`);
      assert.match(gone[1].text, /Deleted$/, `${width}: deleted card says so`);
      assert.match(gone[2].text, /Archived$/, `${width}: archived card says so`);
      await page.close();
    }
    assert.deepEqual(errors, []);
    console.log('PASS: artifact placement — real turns: one card after the reply, inline links, no duplicate link line, edit card, opens the pane, deleted/archived cards inert');
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
