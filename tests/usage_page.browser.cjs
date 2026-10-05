// Usage and API-keys pages: user-visible states no other suite pins.
//
// Already covered, so not repeated here:
//   tests/test_usage_analytics_mariadb.py — /usage.json totals, filters, rates.
//   tests/test_settings_pages_mariadb.py — key reveal-once HTML and page titles.
//
// The fixture user has no usage rows and is not admin, so chart payloads are
// fulfilled here. Key create, reveal and delete go through the real routes.
const assert = require('node:assert/strict');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const origin = process.env.FUSE_BROWSER_ORIGIN || 'http://127.0.0.1:8099';
assert.match(origin, /^http:\/\/127\.0\.0\.1:\d+$/);

const LONG = 'vendor/org/' + 'extremely-long-model-identifier-'.repeat(4) + 'v9';

function payload(range, { empty = false, huge = false, slow = false } = {}) {
  const tokens = empty ? 0 : huge ? 12500000000 : 1800;
  const requests = empty ? 0 : huge ? 2500000 : 3;
  const cost = empty ? 0 : huge ? 1234567000000 : 150;
  const model = huge ? LONG : 'glm-5.3';
  const at = '2026-10-05T12:00:00Z';
  const point = (n) => (empty ? 0 : n);
  return {
    range: { id: range, from: at, to: at, bucket: 'hour', bucket_seconds: 3600 },
    scope: 'account',
    provider: '',
    provider_options: empty ? [] : [{ id: 'zai', name: 'Z.ai' }],
    summary: {
      tokens, requests, cost, cost_label: empty ? '$0' : huge ? '$1,234,567' : '$0.0002',
      estimated_cost: cost, estimated_cost_label: '', pricing_coverage: empty ? 0 : 100,
      priced: requests, untracked: 0, failed: empty ? 0 : 1, cancelled: 0,
      ok_requests: empty ? 0 : requests - 1, prompt: empty ? 0 : tokens, cached: 0,
      output: 0, completion: 0, reasoning: 0, priced_tokens: tokens,
      token_change: empty ? null : 100, request_change: empty ? null : 100,
      cost_change: empty ? null : 100, cache_hit: empty ? null : 0, cache_hit_change: null,
      blended: null, blended_change: null, success_rate: empty ? null : 66.7,
      success_change: null, per_request: empty ? null : Math.round(tokens / requests),
      per_request_change: null, cost_per_request: null, cache_saved: 0,
      cache_saved_known: false, peak_at: empty ? null : at, peak_tokens: tokens,
      models_used: empty ? 0 : 1, empty,
    },
    previous: { tokens: 0, requests: 0, cost: 0 },
    mix: empty ? [] : [{ id: 'uncached', name: 'Uncached input', tokens, share: 100 }],
    series: [{
      at, tokens: point(tokens), requests: point(requests), cost: point(cost), failed: empty ? 0 : 1,
      segments: { [model]: point(tokens), other: 0 },
      cost_segments: { [model]: point(cost), other: 0 },
      request_segments: { [model]: point(requests), other: 0 },
    }],
    series_models: empty ? [] : [{ id: model, name: model, provider: 'zai', provider_label: 'Z.ai' }],
    models: empty ? [] : [{
      id: model, name: model, provider: 'zai', provider_label: 'Z.ai',
      tokens, requests, prompt: tokens, output: 0, completion: 0, cached: 0,
      cache_hit: 0, failed: huge ? 0 : 1, error_rate: huge ? 0 : 33.3,
      cost, cost_known: true, cost_tracked: true, per_request: Math.round(tokens / requests),
      per_million: null, token_change: null, is_new: true, spark: [0, tokens],
    }],
    providers: empty ? [] : [{
      id: 'zai', name: 'Z.ai', tokens, requests, cost, failed: 1, cache_hit: 0,
      token_change: null, is_new: true,
    }],
    keys: empty ? [] : [{ name: 'Laptop', prefix: 'sk-fuse-ab', kind: 'key', tokens, requests, cost, failed: 0, last_at: at }],
    recent: empty ? [] : Array.from({ length: 12 }, (_, i) => ({
      at, model, provider_label: 'Z.ai', via: 'Laptop', via_kind: 'key',
      prompt: tokens, cached: 0, output: 0, reasoning: 0, cost, cost_known: true,
      status: i === 0 ? 'failed' : 'success',
    })),
    activity: empty ? [] : [[Date.parse(at) / 1000, tokens, requests]],
    activity_days: 7,
    users: [],
    _slow: slow,
  };
}

(async () => {
  const browser = await chromium.launch({ headless: true, executablePath: process.env.CHROMIUM_PATH || undefined, args: ['--no-sandbox'] });
  const errors = [];
  try {
    const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, reducedMotion: 'reduce' });
    await context.route('**/*', route => {
      const url = new URL(route.request().url());
      if (url.origin !== origin) return route.abort();
      if (url.pathname !== '/usage.json') return route.continue();
      const body = payload(url.searchParams.get('range') || '24h', {
        empty: url.searchParams.get('range') === '30m',
        huge: url.searchParams.get('range') === '30d',
        slow: url.searchParams.get('range') === '6h',
      });
      const slow = body._slow;
      delete body._slow;
      const send = () => route.fulfill({ contentType: 'application/json', body: JSON.stringify(body) });
      if (slow) return setTimeout(send, 700);
      return send();
    });
    const page = await context.newPage();
    page.on('pageerror', error => errors.push(String(error)));
    const kpi = (id) => page.locator(`[data-kpi="${id}"] [data-kpi-value]`);
    const settled = () => page.waitForFunction(() => {
      const plot = document.querySelector('[data-plot]');
      return plot && !plot.hasAttribute('aria-busy') && plot.textContent.trim().length > 0;
    });
    const overflow = () => page.evaluate(() => document.scrollingElement.scrollWidth <= window.innerWidth + 1);

    await page.goto(origin + '/');
    await page.goto(origin + '/usage');
    await page.locator('[data-usage-dashboard]').waitFor();
    await settled();

    // Zero traffic paints an empty state, not leftover "Loading…" or dashes.
    await page.locator('[data-range="30m"]').click();
    await settled();
    assert.equal(await page.locator('[data-range="30m"]').getAttribute('aria-pressed'), 'true', 'range button reflects 30m');
    assert.match(await page.locator('[data-usage-caption]').innerText(), /Last 30 minutes/, 'caption follows the range');
    assert.match(await kpi('tokens').innerText(), /^0$/, `empty tokens, got ${await kpi('tokens').innerText()}`);
    assert.match(await kpi('requests').innerText(), /^0$/, `empty requests, got ${await kpi('requests').innerText()}`);
    assert.match(await kpi('success').innerText(), /—/, `empty success, got ${await kpi('success').innerText()}`);
    assert.match(await page.locator('[data-plot]').innerText(), /No recorded requests/, 'empty chart says so');
    assert.match(await page.locator('[data-models]').innerText(), /No model usage/, 'empty models table says so');
    assert.match(await page.locator('[data-providers]').innerText(), /No provider usage/, 'empty providers says so');
    assert.match(await page.locator('[data-reliability]').innerText(), /No requests in this range/, 'empty reliability says so');
    assert.doesNotMatch(await page.locator('[data-usage-dashboard]').innerText(), /Loading/, 'nothing still says Loading');
    assert.equal(await page.locator('[data-usage-scope]').count(), 0, 'a non-admin never sees the all-users scope');

    // A slow range keeps the busy flag until the response, then replaces it.
    const slow = page.locator('[data-range="6h"]').click();
    await page.waitForFunction(() => document.querySelector('[data-plot]')?.getAttribute('aria-busy') === 'true');
    await slow;
    await settled();
    assert.equal(await page.locator('[data-range="6h"]').getAttribute('aria-pressed'), 'true');
    assert.match(await kpi('requests').innerText(), /^3$/, `slow range settled on 3 requests, got ${await kpi('requests').innerText()}`);

    // Switching back to 24h replaces the empty totals; the chart metric does not refetch.
    let fetches = 0;
    page.on('request', req => { if (req.url().includes('/usage.json')) fetches += 1; });
    const before = fetches;
    await page.locator('[data-range="24h"]').click();
    await settled();
    assert.ok(fetches > before, 'a range change fetches /usage.json');
    const afterRange = fetches;
    assert.match(await kpi('tokens').innerText(), /1\.8K|1,800/, `24h tokens, got ${await kpi('tokens').innerText()}`);
    assert.match(await kpi('requests').innerText(), /^3$/, `24h requests, got ${await kpi('requests').innerText()}`);
    assert.match(await page.locator('[data-chart-sub]').innerText(), /1,800 total/, `chart subtotal, got ${await page.locator('[data-chart-sub]').innerText()}`);
    assert.match(await page.locator('[data-models] tr').first().innerText(), /glm-5\.3/, 'model row names the model');
    assert.match(await page.locator('[data-recent] tr').first().innerText(), /failed/, 'the newest recent row keeps its status');
    await page.locator('[data-mode="requests"]').click();
    await page.waitForFunction(() => document.querySelector('[data-chart-title]')?.textContent === 'Requests by model');
    assert.equal(fetches, afterRange, 'a chart-metric switch repaints without another fetch');
    assert.equal(await page.locator('[data-mode="requests"]').getAttribute('aria-pressed'), 'true');

    // Twelve recent rows stay collapsed at ten until asked.
    assert.equal(await page.locator('[data-recent] tr').count(), 10, 'recent preview is ten rows');
    const more = page.locator('[data-recent-more]');
    assert.match(await more.innerText(), /Show all 12/);
    await more.click();
    assert.equal(await page.locator('[data-recent] tr').count(), 12, 'show all reveals every row');
    assert.equal(await overflow(), true, '1440: page does not scroll sideways');

    // Huge counts compact, and a long model id truncates with its full name as a title.
    await page.locator('[data-range="30d"]').click();
    await settled();
    const shown = await kpi('tokens').innerText();
    assert.match(shown, /B|12,500,000,000/, `huge tokens compact or group, got ${shown}`);
    assert.ok(shown.length < 16, `huge token text overflows its cell: ${shown}`);
    const name = page.locator('[data-models] .uz-name strong');
    assert.equal(await name.getAttribute('title'), LONG, 'truncated model id keeps a title');
    const clip = await name.evaluate(el => {
      const s = getComputedStyle(el);
      return { overflow: s.overflow, ellipsis: s.textOverflow, nowrap: s.whiteSpace, fits: el.clientWidth <= el.closest('td').clientWidth };
    });
    assert.deepEqual(
      { overflow: clip.overflow, ellipsis: clip.ellipsis, nowrap: clip.nowrap, fits: clip.fits },
      { overflow: 'hidden', ellipsis: 'ellipsis', nowrap: 'nowrap', fits: true },
      `long model id is clipped with an ellipsis ${JSON.stringify(clip)}`,
    );
    assert.equal(await overflow(), true, '1440: huge numbers do not widen the page');

    // A failed fetch replaces the busy chart with an error, not a stuck spinner.
    await page.unroute('**/*');
    await context.route('**/*', route => {
      const url = new URL(route.request().url());
      if (url.origin !== origin) return route.abort();
      if (url.pathname === '/usage.json') return route.fulfill({ status: 500, contentType: 'application/json', body: '{"error":"down"}' });
      return route.continue();
    });
    await page.locator('[data-range="7d"]').click();
    await settled();
    assert.match(await page.locator('[data-plot]').innerText(), /could not be loaded/, 'a 500 says the data failed');
    assert.match(await kpi('tokens').innerText(), /—/, `a 500 clears the token total, got ${await kpi('tokens').innerText()}`);
    assert.match(await page.locator('[data-kpi="tokens"] [data-kpi-delta]').innerText(), /Load failed/);
    assert.equal(await page.locator('[data-plot]').getAttribute('aria-busy'), null, 'a 500 clears the busy flag');

    // 390: the range control and a stacked model card stay inside the viewport.
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto(origin + '/usage');
    await settled();
    assert.equal(await overflow(), true, '390: usage page does not scroll sideways');
    const range = await page.locator('[data-usage-range]').boundingBox();
    assert.ok(range && range.x >= 0 && range.x + range.width <= 390, `390: range control off-screen ${JSON.stringify(range)}`);
    const card = page.locator('[data-models] tr').first();
    const box = await card.boundingBox();
    assert.ok(box && box.width <= 390, `390: model card wider than the viewport ${JSON.stringify(box)}`);
    assert.equal(await page.locator('.uz-models thead').evaluate(el => getComputedStyle(el).display), 'none', '390: the models header is hidden for stacked cards');

    // Keys: an empty list, then create reveals the secret once and lists the name.
    await page.goto(origin + '/keys');
    await page.locator('#keys').waitFor();
    assert.match(await page.locator('#keys').innerText(), /No API keys yet/, 'a fresh account lists no keys');
    await page.locator('#uz-key-name').fill('CI laptop');
    await page.locator('.uz-key-create button').click();
    await page.waitForURL(/\/keys\?created=1$/);
    const reveal = page.locator('.uz-reveal .code-chip');
    await reveal.waitFor();
    const secret = (await reveal.innerText()).trim();
    assert.match(secret, /^sk-/, `created key is shown once, got ${secret.slice(0, 12)}`);
    assert.match(await page.locator('.uz-keys').innerText(), /CI laptop/, 'the new key is in the list');
    assert.match(await page.locator('.uz-keys').innerText(), /never used/, 'an unused key says so');
    await page.reload();
    await page.locator('#keys').waitFor();
    assert.equal(await page.locator('.uz-reveal').count(), 0, 'a reload does not show the secret again');
    assert.match(await page.locator('.uz-keys').innerText(), /CI laptop/, 'the key survives the reload');

    // A rejected name stays out of the list and the typed name is kept.
    await page.locator('#uz-key-name').fill('bad\u0007name');
    await page.locator('.uz-key-create button').click();
    await page.locator('.callout.fault').waitFor();
    assert.match(await page.locator('.callout.fault').innerText(), /1–80 characters/);
    assert.equal(await page.locator('#uz-key-name').inputValue(), 'bad\u0007name', 'the rejected name stays in the field');
    assert.equal(await page.locator('.uz-keys li', { hasText: 'bad' }).count(), 0, 'a rejected name is not listed');

    // Deleting asks first; cancelling keeps the row, confirming removes it.
    await page.locator('.uz-keys li', { hasText: 'CI laptop' }).locator('button').click();
    const dialog = page.locator('dialog[data-confirm]');
    await page.waitForFunction(() => document.querySelector('dialog[data-confirm]')?.open);
    assert.match(await dialog.innerText(), /CI laptop/);
    await dialog.locator('[data-confirm-no]').click();
    await page.waitForFunction(() => !document.querySelector('dialog[data-confirm]')?.open);
    assert.equal(await page.locator('.uz-keys li', { hasText: 'CI laptop' }).count(), 1, 'cancel keeps the key');
    await page.locator('.uz-keys li', { hasText: 'CI laptop' }).locator('button').click();
    await page.waitForFunction(() => document.querySelector('dialog[data-confirm]')?.open);
    await dialog.locator('[data-confirm-yes]').click();
    await page.waitForURL(url => url.pathname === '/keys' && !url.search);
    await page.locator('#keys .uz-empty').waitFor();
    assert.equal(await page.locator('.uz-keys li').count(), 0, 'confirm removes the key from the list');
    assert.equal(await overflow(), true, '390: keys page does not scroll sideways');

    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.goto(origin + '/keys');
    await page.locator('#uz-key-name').waitFor();
    assert.equal(await overflow(), true, '1440: keys page does not scroll sideways');

    assert.deepEqual(errors, [], `page errors: ${errors.join(' | ')}`);
    console.log('PASS usage and keys pages');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
