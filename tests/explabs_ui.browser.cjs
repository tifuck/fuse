// Isolated synthetic pages: real Jinja templates and tab JS, no DB, keys or API traffic.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { execFileSync } = require('node:child_process');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const origin = process.env.FUSE_BROWSER_ORIGIN || 'http://127.0.0.1:8099';
assert.match(origin, /^http:\/\/127\.0\.0\.1:\d+$/);
const root = path.resolve(__dirname, '..');
const pages = JSON.parse(execFileSync(process.env.PYTHON || '/root/fuse/venv/bin/python', ['-c', `
import json
from pathlib import Path
from jinja2 import Environment, FileSystemLoader, ChoiceLoader, DictLoader
root = Path('templates')
base = '<!doctype html><html><head><meta name="viewport" content="width=device-width"><link rel="stylesheet" href="/static/app.css"></head><body>{% block body %}{% endblock %}<script src="/static/back.js"></script><script src="/static/app.js"></script></body></html>'
env = Environment(loader=ChoiceLoader([DictLoader({'base.html': base}), FileSystemLoader(root)]), autoescape=True)
source = (root / 'sources.html').read_text()
nav = source.split('<nav class="usage-tabs"', 1)[1].split('</nav>', 1)[0]
panel = source.split('<div data-tab-panel="explabs"', 1)[1].split('<dialog', 1)[0]
fragment = '<div data-tab-root><nav class="usage-tabs"' + nav + '</nav><div data-tab-panel="pool">Synthetic pools</div><div data-tab-panel="openrouter" class="hidden">Synthetic OpenRouter</div><div data-tab-panel="explabs"' + panel
sources = {}
for scenario in ('connected', 'unconnected', 'flash'):
    connected = scenario != 'unconnected'
    html = env.from_string(fragment).render(csrf_token='synthetic-csrf', explabs_account={'id': 11, 'label': 'Synthetic personal key'} if connected else None, explabs_access={'ok': connected, 'model_count': 1 if connected else 0}, explabs_flash_msg='Synthetic key saved' if scenario == 'flash' else '', explabs_flash_kind='ok', openrouter_flash_msg='')
    sources[scenario] = base.replace('{% block body %}{% endblock %}', html)
models = env.get_template('models.html').render(available=[{'type': 'explabs', 'title': 'Experiential Labs', 'models': [{'id': 'explabs/vendor/chat-model', 'display_id': 'vendor/chat-model', 'name': 'Synthetic Chat Model', 'context_window': 262144}]}], locked=[], available_count=1, locked_count=0, provider_logo_url=lambda kind: '/static/icon.svg')
print(json.dumps({'sources': sources, 'models': models}))
`], { cwd: root, encoding: 'utf8' }));
(async () => {
  const browser = await chromium.launch({ headless: true, executablePath: process.env.CHROMIUM_PATH || undefined, args: ['--no-sandbox'] });
  const errors = [];
  try {
    const page = await browser.newPage({ reducedMotion: 'reduce' });
    page.on('pageerror', e => errors.push(e.message));
    await page.route('**/*', route => {
      const url = new URL(route.request().url());
      if (url.origin !== origin) return route.abort();
      if (url.pathname === '/sources') return route.fulfill({ contentType: 'text/html', body: pages.sources[url.searchParams.get('scenario') || 'connected'] });
      if (url.pathname === '/models') return route.fulfill({ contentType: 'text/html', body: pages.models });
      if (url.pathname.startsWith('/static/')) {
        const file = path.join(root, url.pathname);
        if (fs.existsSync(file)) return route.fulfill({ path: file });
      }
      return route.fulfill({ status: 404, body: '' });
    });
    for (const width of [1440, 390]) {
      await page.setViewportSize({ width, height: 900 });
      for (const scenario of ['connected', 'unconnected', 'flash']) {
      await page.goto(origin + '/sources?scenario=' + scenario);
      const tab = page.locator('[data-tab="explabs"]');
      const panel = page.locator('[data-tab-panel="explabs"]');
      assert.equal(await panel.isVisible(), scenario === 'flash');
      await tab.click();
      assert.equal(await tab.getAttribute('aria-selected'), 'true');
      assert.equal(await panel.isVisible(), true);
      const key = panel.locator('[name="api_key"]');
      assert.equal(await key.getAttribute('type'), 'password');
      assert.equal(await key.inputValue(), '');
      assert.equal(await key.getAttribute('autocomplete'), 'off');
      assert.equal(await panel.locator('input[name="csrf_token"]').count(), scenario === 'unconnected' ? 1 : 2);
      for (const token of await panel.locator('input[name="csrf_token"]').all()) assert.equal(await token.inputValue(), 'synthetic-csrf');
      assert.match(await panel.innerText(), scenario === 'unconnected' ? /Save my key/ : /Replace my key/);
      assert.equal(await panel.locator('form[action="/sources/explabs/11/revoke"]').count(), scenario === 'unconnected' ? 0 : 1);
      if (scenario === 'flash') assert.match(await panel.locator('[role="alert"]').innerText(), /Synthetic key saved/);
      assert.match(await panel.innerText(), /subscription pools never fall back/);
      assert.equal(await panel.locator('input[type="checkbox"]').count(), 0);
      await page.locator('[data-tab="pool"]').click();
      assert.equal(await panel.isVisible(), false);
      await tab.click();
      assert.equal(await panel.isVisible(), true);
      console.log('PASS Experiential Labs personal key forms', scenario, width);
      }
      await page.goto(origin + '/models');
      assert.match(await page.locator('.catalog-family').innerText(), /Experiential Labs/);
      assert.equal(await page.locator('[data-copy="explabs/vendor/chat-model"]').count(), 1);
      assert.match(await page.locator('.catalog-family').innerText(), /Synthetic Chat Model/);
      console.log('PASS Experiential Labs personal key forms and model availability', width);
    }
    assert.deepEqual(errors, []);
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
