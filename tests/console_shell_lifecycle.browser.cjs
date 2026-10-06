// Shell-family lifecycles in Console and Mainframe, driven the way the
// server drives them: a running mount, then tool events, progress snapshots
// and wake envelopes, asserted after each step.
//
// Already pinned by console_ui.browser.cjs, console_ui_lib.js and
// console_rows_audit.browser.cjs (static snapshots, not transitions): one
// running foreground command, a Wait that later says "Still running" or
// "Returned…", one monitor event, one background completion with a non-zero
// exit, a kill that keeps output, dot colours, clocks, the status line.
// This file only covers the transitions those snapshots skip: a Wait that is
// open while its command finishes or fails, a kill mid-output, monitor events
// accumulating then the watch ending, and two background commands finishing
// out of order.
const assert = require('node:assert/strict');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const origin = process.env.FUSE_BROWSER_ORIGIN || 'http://127.0.0.1:8099';
assert.match(origin, /^http:\/\/127\.0\.0\.1:\d+$/);

const ROOT = { host: 'qa-box', cwd: '/srv/qa', platform: 'posix', project_root: '/srv/qa' };
const shell = (id, command, extra = {}) => ({
  id, name: 'run_terminal_command', command, status: 'running', state: 'running',
  execution_status: 'running', exit_code: null, elapsed_ms: 4000, background: true,
  child_id: id, command_id: id, job_id: id, timeout_ms: 120000, termination_confirmed: true,
  output: '', error: '', results: [], ...ROOT, ...extra,
});

function read(page) {
  return page.evaluate(() => {
    const flat = (el) => (el ? el.innerText.replace(/\s+/g, ' ').trim() : '');
    const log = document.querySelector('.chat-log');
    const rows = [...log.querySelectorAll('li.chat-step')].map((li) => ({
      tool: li.dataset.tool || '', step: li.dataset.step || '', exec: li.dataset.execution || '',
      state: li.dataset.state || '', cls: li.className,
      head: flat(li.querySelector(':scope > .chat-step-main > .chat-step-head, :scope > .chat-step-main .chat-console-notice-text')),
      text: flat(li),
      clocks: [...li.querySelectorAll('[data-cc-clock]')].map((el) => el.textContent),
    }));
    const turn = flat(log.querySelector('.chat-console-turn'));
    const dock = document.querySelector('[data-live-dock]');
    const verb = document.querySelector('[data-live-dock] .chat-status-verb');
    return {
      view: document.querySelector('.chat')?.dataset.view || '',
      rows, turn,
      dock: Boolean(dock && !dock.hidden && dock.getAttribute('aria-hidden') !== 'true'),
      verb: verb ? verb.textContent.trim() : '',
      overflow: document.scrollingElement.scrollWidth - window.innerWidth,
      spins: [...document.querySelectorAll('[data-activity-spin], .chat-console-spin')].filter((el) => el.childNodes.length && !el.closest('[hidden]')).length,
    };
  });
}

const row = (snap, id) => snap.rows.find((r) => r.step === `tool-${id}`) || snap.rows.find((r) => r.exec === id);
const seen = (snap) => snap.rows.map((r) => r.text).join('\n');

(async () => {
  const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'], ...(process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {}) });
  const errors = [];
  try {
    const page = await browser.newPage({ reducedMotion: 'reduce', viewport: { width: 1440, height: 1000 } });
    page.on('pageerror', (error) => errors.push(error.message));
    await page.route('**/*', (route) => new URL(route.request().url()).origin === origin ? route.continue() : route.abort());
    await page.goto(origin);
    await page.locator('[data-sync-label]').filter({ hasText: 'Live sync' }).waitFor({ state: 'attached' });
    await page.waitForFunction(() => window.fuseToolFixture);

    // —— A background command, a Wait opened on it, then the command ends ——
    for (const view of ['console', 'mainframe']) {
      await page.evaluate((view) => fuseToolFixture.view(view), view);
      const cmd = shell('bg-wait', 'npm run build', { description: 'Build the bundle', output: 'compiling\n' });
      await page.evaluate((cmd) => fuseToolFixture.mountThread(
        [{ role: 'user', content: 'Build it.' }, { role: 'assistant', model: 'qa/model', elapsedMs: 20000, content: '', tools: [cmd], segments: [{ type: 'tool', id: cmd.id }] }],
        { live: true, phase: 'model' }), cmd);
      let snap = await read(page);
      const launched = row(snap, 'bg-wait');
      assert.ok(launched, `${view}: no row for the background launch: ${JSON.stringify(snap.rows)}`);
      assert.equal(launched.state, 'running', `${view}: launch should pulse while the process runs, saw ${launched.cls}`);
      assert.match(launched.text, /Running in the background/, `${view}: launch text: ${launched.text}`);
      if (view === 'console') assert.match(launched.text, /\(timeout: 2m\)/, `${view}: a running command names its deadline: ${launched.text}`);
      assert.equal(snap.dock, true, `${view}: the live dock is up during the turn`);

      await page.evaluate(() => fuseToolFixture.toolEvent({
        event: 'tool', id: 'wait-1', name: 'get_command_output', status: 'running', background: true,
        command_id: 'bg-wait', child_id: 'bg-wait', job_id: 'bg-wait', command: 'npm run build', elapsed_ms: 5000, ...{ host: 'qa-box' },
      }));
      snap = await read(page);
      const waiting = row(snap, 'wait-1');
      assert.ok(waiting, `${view}: the Wait never appeared: ${JSON.stringify(snap.rows.map((r) => r.head))}`);
      assert.equal(waiting.state, 'running', `${view}: an open Wait is a running call, saw ${waiting.cls}`);
      assert.match(waiting.head, /Wait.*Build the bundle/, `${view}: Wait header: ${waiting.head}`);
      // Console names the wait; Mainframe keeps the shared views' "Running…".
      assert.match(waiting.text, view === 'console' ? /Waiting for output…/ : /Running…/, `${view}: open Wait text: ${waiting.text}`);
      // A Wait that arrives without its own timeout falls back to the deadline of
      // the command it waits on, which is the only deadline the row can name.
      if (view === 'console') assert.match(waiting.text, /\(timeout: 2m\)/, `${view}: an open Wait names how long it waits: ${waiting.text}`);
      assert.doesNotMatch(seen(snap), /Still running/, `${view}: "Still running" belongs to a Wait that already returned`);
      assert.equal(snap.rows.filter((r) => r.exec === 'bg-wait').length, 2, `${view}: the launch and its Wait are two rows`);

      // Output grows while the Wait is open. Console's running background row
      // says only "Running in the background"; Mainframe prints the output.
      await page.evaluate(() => fuseToolFixture.toolEvent({
        event: 'tool', id: 'bg-wait', name: 'run_terminal_command', status: 'progress', output: 'compiling\nmodules 400\nmodules 800\n',
      }));
      snap = await read(page);
      assert.match(row(snap, 'bg-wait').text, /Running in the background/, `${view}: growing output unsettled the launch: ${row(snap, 'bg-wait').text}`);
      assert[view === 'console' ? 'doesNotMatch' : 'match'](row(snap, 'bg-wait').text, /modules 800/, `${view}: running background output: ${row(snap, 'bg-wait').text}`);
      assert.match(row(snap, 'wait-1').text, view === 'console' ? /Waiting for output…/ : /Running…/, `${view}: the open Wait changed while output grew: ${row(snap, 'wait-1').text}`);
      assert.doesNotMatch(row(snap, 'wait-1').text, /modules 800/, `${view}: command output landed in the Wait row`);

      // The command finishes while the Wait is open. The Wait returns with it.
      await page.evaluate(() => {
        fuseToolFixture.toolEvent({
          event: 'tool', id: 'bg-wait', name: 'run_terminal_command', status: 'done', state: 'completed',
          execution_status: 'completed', exit_code: 0, execution_duration_ms: 64000, output: 'compiling\nmodules 400\nmodules 800\nbuilt ok\n',
        });
        fuseToolFixture.toolEvent({
          event: 'tool', id: 'wait-1', name: 'get_command_output', status: 'done', state: 'completed',
          execution_status: 'completed', exit_code: 0, call_duration_ms: 9000, output: 'compiling\nmodules 400\nmodules 800\nbuilt ok\n',
        });
      });
      snap = await read(page);
      const done = row(snap, 'bg-wait');
      const returned = row(snap, 'wait-1');
      assert.equal(done.state, 'done', `${view}: the launch still pulses after the process ended: ${done.cls} | ${done.text}`);
      assert.match(done.text, /Ran in the background|Background command completed \(exit code 0\)/, `${view}: ended launch: ${done.text}`);
      assert.doesNotMatch(done.text, /Running in the background/, `${view}: the launch still says it is running: ${done.text}`);
      assert.equal(done.clocks.filter((c) => c.trim()).length, 0, `${view}: a finished background launch keeps ticking: ${done.clocks}`);
      assert.equal(returned.state, 'done', `${view}: the Wait stays running after it returned: ${returned.cls}`);
      assert.match(returned.text, /built ok/, `${view}: the Wait dropped the output it returned with: ${returned.text}`);
      if (view === 'console') assert.match(returned.text, /· 9s/, `${view}: the Wait's own duration never landed: ${returned.text}`);
      assert.doesNotMatch(seen(snap), /Still running|Waiting for output|Running…/, `${view}: a running claim survived the command ending: ${seen(snap)}`);

      // The turn ends. Nothing keeps saying it runs, and a reload paints the same rows.
      await page.evaluate(() => fuseToolFixture.run({ status: 'completed' }));
      await page.waitForFunction(() => !document.body.classList.contains('is-streaming'));
      snap = await read(page);
      assert.equal(snap.dock, false, `${view}: the live dock stays up after the turn: ${snap.verb}`);
      assert.equal(snap.spins, 0, `${view}: a spinner survives the turn`);
      assert.doesNotMatch(seen(snap), /\bRunning\b|Waiting for output/, `${view}: "Running" after the turn: ${seen(snap)}`);
      const liveText = seen(snap);
      await page.evaluate(() => fuseToolFixture.mountThread(fuseToolFixture.snapshot(), { live: false }));
      snap = await read(page);
      assert.equal(seen(snap), liveText, `${view}: reloading the settled thread changed the rows`);
      assert.ok(snap.overflow <= 0, `${view}: horizontal overflow ${snap.overflow}px`);
      console.log(`PASS background → Wait → completes, in ${view}`);
    }

    // —— The same Wait, but the command fails before it returns ——
    await page.setViewportSize({ width: 390, height: 844 });
    await page.evaluate(() => fuseToolFixture.view('console'));
    {
      const cmd = shell('bg-fail', 'npm test', { description: 'Run the suite' });
      const wait = { id: 'wait-2', name: 'get_command_output', status: 'running', state: 'running', execution_status: 'running', background: true, command_id: 'bg-fail', child_id: 'bg-fail', command: 'npm test', elapsed_ms: 6000, output: '', error: '', results: [], ...ROOT };
      await page.evaluate(({ cmd, wait }) => fuseToolFixture.mountThread(
        [{ role: 'user', content: 'Test it.' }, { role: 'assistant', elapsedMs: 12000, content: '', tools: [cmd, wait], segments: [{ type: 'tool', id: 'bg-fail' }, { type: 'tool', id: 'wait-2' }] }],
        { live: true, phase: 'tools' }), { cmd, wait });
      let snap = await read(page);
      assert.match(row(snap, 'wait-2').text, /Waiting for output…/, `open Wait before the failure: ${row(snap, 'wait-2').text}`);
      await page.evaluate(() => {
        const out = 'tests/a.py F\nFAIL suite\n';
        fuseToolFixture.toolEvent({ event: 'tool', id: 'bg-fail', name: 'run_terminal_command', status: 'error', state: 'failed', execution_status: 'failed', error: 'Command exited with code 1', exit_code: 1, execution_duration_ms: 18000, output: out });
        fuseToolFixture.toolEvent({ event: 'tool', id: 'wait-2', name: 'get_command_output', status: 'done', state: 'failed', execution_status: 'failed', exit_code: 1, call_duration_ms: 7000, output: out });
      });
      snap = await read(page);
      assert.equal(row(snap, 'bg-fail').state, 'error', `a failed background command keeps a running dot: ${row(snap, 'bg-fail').cls}`);
      assert.match(row(snap, 'bg-fail').text, /Failed in the background/, `failed launch: ${row(snap, 'bg-fail').text}`);
      assert.match(row(snap, 'wait-2').text, /Exit code 1/, `the Wait never picked up the failure: ${row(snap, 'wait-2').text}`);
      assert.match(row(snap, 'wait-2').text, /FAIL suite/, `the Wait dropped the failing output: ${row(snap, 'wait-2').text}`);
      assert.doesNotMatch(row(snap, 'wait-2').text, /Still running|Waiting for output/, `the Wait still waits after the failure: ${row(snap, 'wait-2').text}`);
      // A relayed non-zero exit is the command's status, not a failed call: warning, not error.
      assert.equal(row(snap, 'wait-2').state, 'warning', `a Wait that observed a non-zero exit: ${row(snap, 'wait-2').cls}`);
      assert.ok(snap.overflow <= 0, `390px overflow ${snap.overflow}px on a failing command`);
      console.log('PASS Wait open while its command fails');
    }

    // —— A long command keeps its deadline whole: the note wraps as a unit ——
    await page.evaluate(() => fuseToolFixture.view('console'));
    {
      const long = 'docker run --rm -v /srv/qa:/workspace -w /workspace ghcr.io/qa/builder:latest cargo build --release --all-features --target-dir /workspace/target';
      const cmd = shell('long-1', long, { background: false, description: 'Build the bundle' });
      await page.evaluate((cmd) => fuseToolFixture.mountThread(
        [{ role: 'user', content: 'Build it.' }, { role: 'assistant', model: 'qa/model', elapsedMs: 20000, content: '', tools: [cmd], segments: [{ type: 'tool', id: cmd.id }] }],
        { live: true, phase: 'tools' }), cmd);
      const snap = await read(page);
      assert.match(row(snap, 'long-1').text, /\(timeout: 2m\)/, `a described command names its deadline: ${row(snap, 'long-1').text}`);
      const note = await page.evaluate(() => {
        const el = document.querySelector('[data-step="tool-long-1"] .chat-console-timeout');
        if (!el) return null;
        const range = document.createRange();
        range.selectNodeContents(el);
        return { text: el.textContent, rects: range.getClientRects().length, whiteSpace: getComputedStyle(el).whiteSpace };
      });
      assert.ok(note, `the deadline note is its own span: ${row(snap, 'long-1').text}`);
      assert.equal(note.whiteSpace, 'nowrap', `the note never breaks inside itself: ${note.whiteSpace}`);
      assert.equal(note.rects, 1, `the note stays on one line: ${JSON.stringify(note)}`);
      assert.ok(snap.overflow <= 0, `390px overflow ${snap.overflow}px on a long command`);
      console.log('PASS deadline note never splits mid-note');
    }

    // —— A kill mid-output keeps what was already printed, and stops the clock ——
    await page.setViewportSize({ width: 1440, height: 1000 });
    for (const view of ['console', 'mainframe']) {
      await page.evaluate((view) => fuseToolFixture.view(view), view);
      const out = 'listening on :8080\nWARN cache miss\nready\n';
      const cmd = shell('bg-kill', 'npm start', { output: out, elapsed_ms: 15000 });
      await page.evaluate((cmd) => fuseToolFixture.mountThread(
        [{ role: 'user', content: 'Start it.' }, { role: 'assistant', elapsedMs: 8000, content: '', tools: [cmd], segments: [{ type: 'tool', id: cmd.id }] }],
        { live: true, phase: 'model' }), cmd);
      let snap = await read(page);
      assert.equal(row(snap, 'bg-kill').state, 'running', `${view}: not pulsing before the kill: ${row(snap, 'bg-kill').text}`);
      await page.evaluate(() => {
        fuseToolFixture.toolEvent({ event: 'tool', id: 'kill-1', name: 'kill_command', status: 'done', command_id: 'bg-kill', child_id: 'bg-kill', output: '' });
        fuseToolFixture.toolEvent({ event: 'tool', id: 'bg-kill', name: 'run_terminal_command', status: 'interrupted', state: 'interrupted', execution_status: 'interrupted', error: 'cancelled', exit_code: null, output: 'listening on :8080\nWARN cache miss\nready\n' });
      });
      snap = await read(page);
      const killed = row(snap, 'bg-kill');
      const kill = row(snap, 'kill-1');
      assert.equal(killed.state, 'interrupted', `${view}: a killed command keeps a running dot: ${killed.cls} | ${killed.text}`);
      assert.match(killed.text, /Interrupted( · stopped)?/, `${view}: kill result: ${killed.text}`);
      assert.match(killed.text, /listening on :8080/, `${view}: the kill dropped output already produced: ${killed.text}`);
      assert.match(killed.text, /ready/, `${view}: the kill truncated the kept output: ${killed.text}`);
      assert.doesNotMatch(killed.text, /Running in the background/, `${view}: the killed row still says it is running`);
      assert.equal(killed.clocks.filter((c) => c.trim()).length, 0, `${view}: the killed row's clock still ticks: ${killed.clocks}`);
      assert.ok(kill, `${view}: the Stop Task row never appeared`);
      assert.match(kill.head, /Stop Task/, `${view}: kill header: ${kill.head}`);
      assert.equal(kill.state, 'done', `${view}: the kill call itself looks interrupted: ${kill.cls}`);
      console.log(`PASS kill mid-output keeps the output, in ${view}`);
    }

    // —— Two background commands finish out of order, one of them after the turn ——
    await page.evaluate(() => fuseToolFixture.view('console'));
    {
      const first = shell('ord-1', 'npm run lint', { description: 'Lint', elapsed_ms: 30000 });
      const second = shell('ord-2', 'npm test', { description: 'Test', elapsed_ms: 5000 });
      await page.evaluate(({ first, second }) => fuseToolFixture.mountThread(
        [{ role: 'user', content: 'Both.' }, { role: 'assistant', elapsedMs: 4000, content: 'Running both.', tools: [first, second], segments: [{ type: 'content', text: 'Running both.' }, { type: 'tool', id: 'ord-1' }, { type: 'tool', id: 'ord-2' }] }],
        { live: true, phase: 'model' }), { first, second });
      await page.evaluate(() => fuseToolFixture.jobs([{ command_id: 'ord-2', id: 'ord-2', state: 'completed', exit_code: 2, duration_ms: 9000, output: 'tests failed\n', error: '' }]));
      let snap = await read(page);
      assert.match(row(snap, 'ord-2').text, /exit code 2/, `the later command's exit never reached its row: ${row(snap, 'ord-2').text}`);
      assert.equal(row(snap, 'ord-2').state, 'warning', `a non-zero completion stays a running row: ${row(snap, 'ord-2').cls}`);
      assert.equal(row(snap, 'ord-1').state, 'running', `finishing one command settled the other: ${row(snap, 'ord-1').cls} | ${row(snap, 'ord-1').text}`);
      assert.match(row(snap, 'ord-1').text, /Running in the background/, `the still-running command lost its row: ${row(snap, 'ord-1').text}`);
      // The turn ends with one command still going; the closing line names it, nothing else ticks.
      await page.evaluate(() => { fuseToolFixture.work([]); fuseToolFixture.run({ status: 'completed' }); });
      await page.waitForFunction(() => !document.body.classList.contains('is-streaming'));
      snap = await read(page);
      assert.equal(snap.dock, false, `dock stays after the turn: ${snap.verb}`);
      assert.match(row(snap, 'ord-1').text, /Running in the background/, `ending the turn settled a command that is still running: ${row(snap, 'ord-1').text}`);
      assert.doesNotMatch(row(snap, 'ord-2').text, /Running/, `the finished command revived when the turn ended: ${row(snap, 'ord-2').text}`);
      const env = (id, state, body) => `<command_result id="${id}" state="${state}">\nhost: qa-box command: ${id === 'ord-1' ? 'npm run lint' : 'npm test'}\n` +
        `description: ${id === 'ord-1' ? 'Lint' : 'Test'}\nexit_code: ${id === 'ord-1' ? 0 : 2}\nduration_ms: ${id === 'ord-1' ? 40000 : 9000}\noutput_chars: ${body.length}\noutput_chars_exact: true\n\n${body}</command_result>`;
      await page.evaluate((content) => {
        const rows = fuseToolFixture.snapshot();
        rows.push({ role: 'user', auto: true, content });
        fuseToolFixture.mountThread(rows, { live: false });
      }, env('ord-1', 'completed', 'lint clean'));
      snap = await read(page);
      assert.match(seen(snap), /Background command "Lint" completed \(exit code 0\)/, `the late completion never printed: ${seen(snap)}`);
      assert.match(seen(snap), /· 40s/, `the completion's duration never printed: ${seen(snap)}`);
      assert.match(seen(snap), /lint clean/, `the completion dropped its output: ${seen(snap)}`);
      assert.doesNotMatch(row(snap, 'ord-1').text, /Running in the background/, `the launch still says running after its wake: ${row(snap, 'ord-1').text}`);
      assert.match(seen(snap), /exit code 2/, `the earlier completion disappeared when the later one arrived: ${seen(snap)}`);
      console.log('PASS two background commands finish out of order');
    }

    // —— A monitor: events accumulate, then the watch ends three ways ——
    await page.evaluate(() => fuseToolFixture.view('console'));
    {
      const mon = shell('mon-1', 'tail -F /var/log/app.log', { name: 'monitor', description: 'errors in worker.log', event_count: 0, timeout_ms: 300000, pattern: 'ERROR' });
      await page.evaluate((mon) => fuseToolFixture.mountThread(
        [{ role: 'user', content: 'Watch.' }, { role: 'assistant', elapsedMs: 3000, content: '', tools: [mon], segments: [{ type: 'tool', id: mon.id }] }],
        { live: true, phase: 'model' }), mon);
      let snap = await read(page);
      assert.match(row(snap, 'mon-1').text, /Monitor started · task mon-1/, `monitor start: ${row(snap, 'mon-1').text}`);
      assert.match(row(snap, 'mon-1').text, /\(timeout: 5m\)/, `a running monitor names its deadline: ${row(snap, 'mon-1').text}`);
      assert.equal(row(snap, 'mon-1').state, 'running', `a live monitor does not pulse: ${row(snap, 'mon-1').cls}`);
      const event = (n, line) => `<monitor_event id="mon-1" seq="${n}">\nhost: qa-box command: tail -F /var/log/app.log\ndescription: errors in worker.log\n\n${line}\n</monitor_event>`;
      for (let n = 1; n <= 3; n += 1) {
        await page.evaluate((content) => {
          const rows = fuseToolFixture.snapshot();
          rows.push({ role: 'user', auto: true, content });
          fuseToolFixture.mountThread(rows, { live: true, phase: 'model' });
        }, event(n, `ERROR number ${n}`));
        snap = await read(page);
        const events = snap.rows.filter((r) => /Monitor event: errors in worker\.log/.test(r.text));
        assert.equal(events.length, n, `after event ${n} the transcript shows ${events.length}: ${snap.rows.map((r) => r.head).join(' | ')}`);
        assert.match(events[n - 1].text, new RegExp(`ERROR number ${n}`), `event ${n} shows the wrong line: ${events[n - 1].text}`);
        assert.equal(row(snap, 'mon-1').state, 'running', `event ${n} settled the monitor: ${row(snap, 'mon-1').cls}`);
      }
      await page.evaluate(() => fuseToolFixture.run({ status: 'completed' }));
      await page.waitForFunction(() => !document.body.classList.contains('is-streaming'));
      snap = await read(page);
      assert.equal(row(snap, 'mon-1').state, 'running', `ending the turn settled a monitor that is still watching: ${row(snap, 'mon-1').text}`);
      assert.equal(snap.dock, false, `the dock stays up after the turn that started the monitor: ${snap.verb}`);
      assert.doesNotMatch(seen(snap), /\bRunning\b|Waiting for output/, `a monitor event renders as a running call: ${seen(snap)}`);

      // The watch ends because it was killed: red, "stopped", and the events stay.
      const killed = `<monitor_result id="mon-1" state="cancelled">\nhost: qa-box command: tail -F /var/log/app.log\ndescription: errors in worker.log\nevents: 3\nerror: cancelled\n</monitor_result>`;
      await page.evaluate((content) => {
        const rows = fuseToolFixture.snapshot();
        rows.push({ role: 'user', auto: true, content });
        fuseToolFixture.mountThread(rows, { live: false });
      }, killed);
      snap = await read(page);
      assert.match(seen(snap), /Monitor "errors in worker\.log" stopped/, `a killed monitor: ${seen(snap)}`);
      assert.equal(snap.rows.filter((r) => /ERROR number/.test(r.text)).length, 3, `ending the watch dropped its events`);
      assert.doesNotMatch(row(snap, 'mon-1').text, /Monitor started/, `the monitor row still says it is watching: ${row(snap, 'mon-1').text}`);
      const stopped = snap.rows.find((r) => /Monitor "errors in worker\.log" stopped/.test(r.text));
      assert.ok(/\bis-gold\b/.test(stopped.cls), `a stopped monitor is not red: ${stopped.cls}`);

      // A watch that times out reads the same way, and names no stream.
      await page.evaluate((mon) => fuseToolFixture.mountThread(
        [{ role: 'user', content: 'Watch.' }, { role: 'assistant', elapsedMs: 3000, endedAt: '2026-10-05T12:00:00Z', content: '', tools: [{ ...mon, id: 'mon-2', child_id: 'mon-2', command_id: 'mon-2', job_id: 'mon-2' }], segments: [{ type: 'tool', id: 'mon-2' }] },
         { role: 'user', auto: true, content: '<monitor_result id="mon-2" state="timed_out">\nhost: qa-box command: tail -F /var/log/app.log\ndescription: errors in worker.log\nevents: 1\nduration_ms: 300000\nerror: timed out\n</monitor_result>' }],
        { live: false }), mon);
      snap = await read(page);
      assert.match(seen(snap), /Monitor "errors in worker\.log" stopped/, `a timed-out monitor: ${seen(snap)}`);
      assert.doesNotMatch(seen(snap), /stream ended|script failed/, `a timeout reads as a clean end: ${seen(snap)}`);

      // A watch that hits its event cap ends cleanly and says the stream ended.
      await page.evaluate((mon) => fuseToolFixture.mountThread(
        [{ role: 'user', content: 'Watch.' }, { role: 'assistant', elapsedMs: 3000, endedAt: '2026-10-05T12:00:00Z', content: '', tools: [{ ...mon, id: 'mon-3', child_id: 'mon-3', command_id: 'mon-3', job_id: 'mon-3', event_count: 3 }], segments: [{ type: 'tool', id: 'mon-3' }] },
         { role: 'user', auto: true, content: '<monitor_result id="mon-3" state="completed">\nhost: qa-box command: tail -F /var/log/app.log\ndescription: errors in worker.log\nevents: 3\nduration_ms: 12000\n</monitor_result>' }],
        { live: false }), mon);
      snap = await read(page);
      assert.match(seen(snap), /Monitor "errors in worker\.log" stream ended/, `a monitor that reached its cap: ${seen(snap)}`);
      assert.match(seen(snap), /· 12s/, `the watch's duration never printed: ${seen(snap)}`);
      assert.doesNotMatch(seen(snap), /stopped|script failed/, `a clean end reads as a failure: ${seen(snap)}`);
      const ended = snap.rows.find((r) => /stream ended/.test(r.text));
      assert.ok(/\bis-green\b/.test(ended.cls), `a cleanly ended monitor is not white: ${ended.cls}`);
      await page.setViewportSize({ width: 390, height: 844 });
      snap = await read(page);
      assert.ok(snap.overflow <= 0, `390px overflow ${snap.overflow}px on a monitor notice`);
      console.log('PASS monitor events accumulate, then the watch ends');
    }

    assert.deepEqual(errors, [], `page errors: ${errors.join('\n')}`);
  } finally { await browser.close(); }
})().catch((error) => { console.error(error); process.exitCode = 1; });
