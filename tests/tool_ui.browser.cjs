// Run against chat_browser_fixture.py, never the live Fuse service.
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const origin = process.env.FUSE_BROWSER_ORIGIN || 'http://127.0.0.1:8099';
assert.match(origin, /^http:\/\/127\.0\.0\.1:\d+$/);
const out = process.env.TOOL_UI_ARTIFACTS || '/tmp/opencode/tool-ui-artifacts';
const baseline = process.env.TOOL_UI_BASELINE;
const visualFailures = [];
const manifest = [];
const failures = [];
let assertions = 0;

(async () => {
  await fs.mkdir(out, { recursive: true });
  if (baseline) assert.notEqual(path.resolve(baseline), path.resolve(out), 'Baseline and output directories must differ');
  const browser = await chromium.launch({ headless: true, executablePath: process.env.CHROMIUM_PATH || undefined, args: ['--no-sandbox'] });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, reducedMotion: 'reduce' });
    page.on('pageerror', error => failures.push(error.message));
    await page.route('**/*', route => new URL(route.request().url()).origin === origin ? route.continue() : route.abort());
    await page.goto(origin);
    await page.locator('[data-sync-label]').filter({ hasText: 'Live sync' }).waitFor({ state: 'attached' });
    await page.waitForFunction(() => Boolean(window.fuseToolFixture));
    await page.evaluate(() => document.fonts.ready);
    await page.addStyleTag({ content: '* { animation: none !important; transition: none !important; caret-color: transparent !important; }' });

    await page.evaluate(() => {
      FuseChatAppearance.setSize(100);
    });

    assert.deepEqual(failures, [], 'browser errors');
    await fs.writeFile(path.join(out, 'manifest.json'), JSON.stringify({ assertions, browser: browser.version(), baseline: baseline || null, cases: manifest }, null, 2));
    const escape = text => String(text).replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('"', '&quot;');
    await fs.writeFile(path.join(out, 'index.html'), `<!doctype html><meta charset="utf-8"><title>Fuse tool-state gallery</title>
      <style>body{font:14px system-ui;background:#171918;color:#eee;padding:24px}main{display:grid;grid-template-columns:repeat(auto-fit,minmax(360px,1fr));gap:24px}article{min-width:0;padding:16px;background:#252826;border-radius:8px}img{max-width:100%;height:auto}h2{font-size:14px;overflow-wrap:anywhere}pre{white-space:pre-wrap;color:#bcc5bd}</style>
      <h1>Fuse tool-state gallery</h1><p>${assertions} checks. ${manifest.length} screenshots. Expand an image for full resolution.</p><main>` +
      manifest.map(item => `<article><h2>${escape(item.case)}</h2><pre>${escape(JSON.stringify(item.expected))}</pre>${item.regression ? `<p>${escape(item.regression)}</p>` : ''}<a href="${item.screenshot}"><img loading="lazy" src="${item.screenshot}" alt="${escape(item.case)}"></a>${item.diff ? `<a href="${item.diff}">Pixel diff</a>` : ''}</article>`).join('') + '</main>');
    assert.deepEqual(visualFailures, [], 'visual regressions; inspect the gallery before changing a baseline');
    console.log(`PASS tool state gallery: fixture boots clean, ${manifest.length} screenshots in ${out}`);
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
