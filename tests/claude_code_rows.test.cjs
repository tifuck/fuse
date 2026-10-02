// Console's port of Claude Code's per-call rows: exit-code meanings, sed
// edits, counts, and what a running subagent's row says it is doing.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(require('node:path').join(__dirname, '../static/chat.js'), 'utf8');
function load(name, context) {
  const start = source.indexOf(`  function ${name}(`);
  assert.ok(start >= 0, name);
  const end = source.indexOf('\n  }', start) + 4;
  vm.runInContext(source.slice(start, end), context);
}
function constant(name, context) {
  const start = source.indexOf(`  const ${name} =`);
  assert.ok(start >= 0, name);
  const end = source.indexOf(';\n', start) + 2;
  vm.runInContext(source.slice(start, end).replace(`const ${name}`, `var ${name}`), context);
}
function world() {
  const ctx = vm.createContext({ compactPath: (path) => path });
  for (const name of ['CC_SEARCH_CMDS', 'CC_READ_CMDS', 'CC_LIST_CMDS', 'CC_NEUTRAL_CMDS', 'CC_FIRST_CMD', 'CC_EXIT_NOTES', 'CC_COMPACT', 'CC_CALL_NAMES']) constant(name, ctx);
  for (const name of ['ccShellSegments', 'ccShellKind', 'ccExitMeaning', 'ccShellWords', 'ccSedEdit', 'ccTokens', 'ccBytes', 'ccDeadline',
    'ccFlat', 'ccClip', 'ccCallName', 'ccCallSummary', 'ccCallKind', 'ccCountsText', 'ccAgentLastCall', 'ccCallLine']) load(name, ctx);
  return ctx;
}

test('exit 1 from grep, find, diff and test is not a failure', () => {
  const c = world();
  assert.deepEqual({ ...c.ccExitMeaning('grep -rn foo src', 1) }, { isError: false, message: 'No matches found' });
  assert.deepEqual({ ...c.ccExitMeaning('cd app && rg TODO', 1) }, { isError: false, message: 'No matches found' });
  assert.deepEqual({ ...c.ccExitMeaning('git diff --quiet', 1) }, { isError: false, message: 'Files differ' });
  assert.deepEqual({ ...c.ccExitMeaning('test -f x', 1) }, { isError: false, message: 'Condition is false' });
  assert.equal(c.ccExitMeaning('grep foo bar', 2).isError, true);
  assert.equal(c.ccExitMeaning('npm test', 1).isError, true);
  assert.equal(c.ccExitMeaning('npm test', 0).isError, false);
});

test('a single-file sed -i is an Update of that file', () => {
  const c = world();
  assert.equal(c.ccSedEdit("sed -i 's/old/new/g' app/main.py").file, 'app/main.py');
  assert.equal(c.ccSedEdit("sed -i.bak -E 's/a+/b/' x.txt").file, 'x.txt');
  assert.equal(c.ccSedEdit("sed -i '' -e 's/a/b/' x.txt").file, 'x.txt');
  assert.equal(c.ccSedEdit("sed 's/a/b/' x.txt"), null);
  assert.equal(c.ccSedEdit("sed -i 's/a/b/' x.txt y.txt"), null);
  assert.equal(c.ccSedEdit("sed -i 's/a/b/' *.txt"), null);
  assert.equal(c.ccSedEdit("sed -i \"s/$X/b/\" x.txt"), null);
  assert.equal(c.ccSedEdit("sed -i '1d' x.txt"), null);
  assert.equal(c.ccSedEdit("sed -i 's/a/b/' x.txt && echo ok"), null);
});

test('counts read the way Claude Code prints them', () => {
  const c = world();
  assert.equal(c.ccTokens(999), '999');
  assert.equal(c.ccTokens(32100), '32.1k');
  assert.equal(c.ccTokens(1_250_000), '1.3m');
  assert.equal(c.ccBytes(512), '512 bytes');
  assert.equal(c.ccBytes(12_595), '12.3KB');
  assert.equal(c.ccBytes(1024 * 1024 * 1.5), '1.5MB');
  assert.equal(c.ccDeadline(1_800_000), '30m');
  assert.equal(c.ccDeadline(90_000), '1m 30s');
  assert.equal(c.ccDeadline(3_600_000), '1h');
  assert.equal(c.ccDeadline(45_000), '45s');
});

test("a running subagent's row names its latest step", () => {
  const c = world();
  assert.equal(c.ccAgentLastCall([
    { name: 'run_terminal_command', status: 'done', command: 'npm test', description: 'Run the unit tests' },
  ]), 'Bash: Run the unit tests');
  assert.equal(c.ccAgentLastCall([
    { name: 'run_terminal_command', status: 'done', command: 'npm test' },
    { name: 'grep', status: 'done', pattern: 'login' },
    { name: 'read_file', status: 'done', path: 'src/auth.ts' },
    { name: 'read_file', status: 'running', path: 'src/user.ts' },
  ]), 'Searching for 1 pattern, reading 2 files…');
  assert.equal(c.ccAgentLastCall([{ name: 'read_url', status: 'done', url: 'https://example.com/docs' }]), 'Fetch: https://example.com/docs');
  assert.equal(c.ccAgentLastCall([{ name: 'grep', status: 'running', pattern: 'x' }]), '');
  assert.equal(c.ccCallLine({ name: 'grep', pattern: 'login' }), 'Search(login)');
});

test('a shell approval card shows the description above the command', () => {
  const ctx = vm.createContext({ PERM_TOTAL_S: 60 });
  for (const name of ['escapeHtml', 'permToolLabel', 'permGrantKind', 'permGrantPath', 'permCardCopy', 'permLeftS', 'fmtPermCountdown', 'permCardSignature', 'permQueueCardHtml']) load(name, ctx);
  const rec = {
    id: 'pa_1', status: 'pending', tool: 'run_terminal_command', host: 'qa-box', cwd: '/srv/app', command: 'npm install',
    description: 'Install package dependencies', requester: 'Main agent', reasons: ['shell command requires approval'],
    expires_at: new Date(Date.now() + 50000).toISOString(),
  };
  const html = ctx.permQueueCardHtml(rec);
  const desc = html.indexOf('chat-perm-card-desc">Install package dependencies<');
  const cmd = html.indexOf('chat-perm-card-cmd mono">npm install<');
  assert.ok(desc > 0 && cmd > desc, html);
  assert.notEqual(ctx.permCardSignature(rec), ctx.permCardSignature({ ...rec, description: '' }));
  assert.ok(!ctx.permQueueCardHtml({ ...rec, description: '' }).includes('chat-perm-card-desc'));
});
