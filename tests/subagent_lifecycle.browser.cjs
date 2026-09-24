const assert = require('node:assert/strict');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const origin = process.env.FUSE_BROWSER_ORIGIN || 'http://127.0.0.1:8099';
assert.match(origin, /^http:\/\/127\.0\.0\.1:\d+$/);
(async () => {
  const browser = await chromium.launch({headless: true, args: ['--no-sandbox']});
  try {
    const page = await browser.newPage();
    const errors = [];
    page.on('pageerror', e => errors.push(e.message));
    await page.route('**/*', route => new URL(route.request().url()).origin === origin ? route.continue() : route.abort());
    await page.goto(origin);
    await page.waitForFunction(() => Boolean(window.fuseToolFixture));
    for (const width of [390, 1440]) for (const view of ['cli', 'console', 'compact']) {
      await page.setViewportSize({width, height: 900});
      const result = await page.evaluate(view => {
        const f = fuseToolFixture;
        f.view(view);
        const endings = [
          ['completed', ''], ['cancelled', 'Stopped by user'],
          ['interrupted', 'Server shutdown'], ['failed', 'Provider unavailable'],
          ['failed', 'Unhandled exception'],
        ];
        f.mount([{tools: [{id: 'read', name: 'read_file', status: 'running', path: 'README.md'}]}], true);
        f.toolEvent({event: 'tool', id: 'read', name: 'read_file', status: 'done', preview: 'Read complete'});
        endings.forEach((_, i) => f.append({id: `spawn-${i}`, name: 'spawn_subagent', child_id: `child-${i}`,
          status: 'done', execution_status: 'running', background: true, description: `Lifecycle child ${i}`}, true));
        f.append({id: 'wait', name: 'get_subagent_output', child_id: 'child-0', status: 'running'}, true);
        const before = f.activity(f.snapshot()[0], null, true).label;
        endings.forEach(([status, error], i) => f.agents([{id: `child-${i}`, status,
          message: {content: status === 'completed' ? 'COMPLETED_REPORT' : 'PARTIAL_MUST_NOT_DELIVER', error,
            tools: [{name: 'read_file', preview: 'TOOL_MUST_NOT_DELIVER'}]}}]));
        f.toolEvent({event: 'tool', id: 'wait', name: 'get_subagent_output', child_id: 'child-0', status: 'done', summary: 'COMPLETED_REPORT'});
        f.agents([{id: 'child-0', status: 'completed', summary: 'COMPLETED'}]);
        const traces = f.snapshot()[0].tools;
        return {before, children: traces.filter(t => t.name === 'spawn_subagent').map(t => ({summary:t.summary, state:f.state(t, true).state})),
          wait:f.state(traces.find(t => t.id === 'wait'), true).state};
      }, view);
      assert.match(result.before, /5 active \/ 5 background/);
      assert.deepEqual(result.children.map(t => t.state), ['done', 'interrupted', 'interrupted', 'error', 'error']);
      assert.equal(result.children[0].summary, 'COMPLETED_REPORT');
      for (const child of result.children.slice(1)) {
        assert.match(child.summary, /No completed report/);
        assert.doesNotMatch(child.summary, /MUST_NOT_DELIVER/);
      }
      assert.equal(result.wait, 'done');
      console.log(`PASS lifecycle matrix ${view} ${width}`);
    }
    assert.deepEqual(errors, []);
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
