const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(require('node:path').join(__dirname, '../static/chat.js'), 'utf8');
const start = source.indexOf('  function escapeHtml(');
const end = source.indexOf('  // Keep settled Markdown nodes', start);
assert.ok(start >= 0 && end > start);
const ctx = vm.createContext({ window: {} });
vm.runInContext(source.slice(start, end), ctx);

function md(src) {
  return ctx.renderMarkdown(src, false, []);
}

test('literal tag mentions cannot capture later prose or a real disclosure', () => {
  const src = 'Supports `<details>` and `<summary>`.\n\nKEEP THIS PARAGRAPH\n\n<details>\n<summary>Actual dump</summary>\ninside\n</details>\n\nAFTER';
  for (let i = 1; i <= src.length; i++) {
    const html = ctx.renderMarkdown(src.slice(0, i), true, []);
    if (i >= src.indexOf('KEEP THIS PARAGRAPH') + 19) {
      assert.ok(html.indexOf('KEEP THIS PARAGRAPH') >= 0);
      const disclosure = html.indexOf('<details class=');
      assert.ok(disclosure < 0 || html.indexOf('KEEP THIS PARAGRAPH') < disclosure);
    }
  }
  const html = md(src);
  assert.equal((html.match(/<details class=/g) || []).length, 1);
  assert.match(html, /<code>&lt;details&gt;<\/code>/);
  assert.match(html, /<summary>Actual dump<\/summary>/);
});

test('fenced HTML is literal and nested disclosures pair correctly', () => {
  const html = md('```html\n<details><summary>Example</summary>x</details>\n```\n\n<details><summary>Outer</summary>\n<details><summary>Inner</summary>body</details>\nouter tail\n</details>\nAFTER');
  assert.equal((html.match(/<details class=/g) || []).length, 2);
  assert.match(html, /&lt;details&gt;/);
  assert.match(html, /outer tail/);
  assert.ok(html.lastIndexOf('AFTER') > html.lastIndexOf('</details>'));
});

test('closing tags in fenced examples do not terminate a disclosure', () => {
  const html = md('<details>\n<summary>Code</summary>\n```html\n</details>\n```\nKEEP INSIDE\n</details>\nAFTER');
  assert.match(html, /&lt;\/details&gt;/);
  assert.ok(html.indexOf('KEEP INSIDE') < html.lastIndexOf('</details>'));
});

test('nested lists keep hierarchy', () => {
  const html = md('- a\n  - b\n- c');
  assert.match(html, /<ul><li>a<ul><li>b<\/li><\/ul><\/li><li>c<\/li><\/ul>/);
});

test('ordered lists nest under bullets', () => {
  const html = md('- files\n  1. one\n  2. two');
  assert.match(html, /<ul><li>files<ol><li>one<\/li><li>two<\/li><\/ol><\/li><\/ul>/);
});

test('github alerts become labeled blockquotes', () => {
  const html = md('> [!WARNING]\n> Do not deploy');
  assert.match(html, /class="chat-md-alert is-warning"/);
  assert.match(html, /chat-md-alert-label">Warning/);
  assert.match(html, /Do not deploy/);
  assert.equal((html.match(/Do not deploy/g) || []).length, 1);
});

test('same-line alert body is kept once', () => {
  const html = md('> [!TIP] Prefer search_replace.\n> Then test.');
  assert.match(html, /is-tip/);
  assert.match(html, /Prefer search_replace/);
  assert.equal((html.match(/Prefer search_replace/g) || []).length, 1);
});

test('unclosed details do not drop earlier prose', () => {
  const html = md('Each box shares layout w:220.\n\n<details>\n<summary>Dump</summary>\njson');
  assert.match(html, /Each box shares layout w:220/);
  assert.match(html, /&lt;details&gt;/);
});

test('autolinks wrap bare https urls once', () => {
  const html = md('See https://example.com/docs.');
  assert.match(html, /href="https:\/\/example.com\/docs"/);
  assert.equal((html.match(/<a /g) || []).length, 1);
});

test('markdown links are not double-wrapped', () => {
  const html = md('[Example](https://example.com/)');
  assert.equal((html.match(/<a /g) || []).length, 1);
  assert.match(html, />Example<\/a>/);
});

test('images require http urls', () => {
  const ok = md('![box](https://example.com/red.png)');
  const bad = md('![box](javascript:alert(1))');
  assert.match(ok, /<img src="https:\/\/example.com\/red.png" alt="box"/);
  assert.equal(bad.includes('<img'), false);
});

test('details allowlist strips other html', () => {
  const html = md('<details>\n<summary>More</summary>\nsecret\n</details>\n<script>alert(1)</script>');
  assert.match(html, /<details class="chat-md-details"[^>]*><summary>More<\/summary>/);
  assert.match(html, /secret/);
  assert.match(html, /&lt;script&gt;/);
  assert.equal(html.includes('<script>'), false);
});

test('details can wrap a fenced block', () => {
  const html = md('<details>\n<summary>JSON</summary>\n\n```json\n{"ok":true}\n```\n</details>');
  assert.match(html, /<details class="chat-md-details"[^>]*><summary>JSON<\/summary>/);
  assert.match(html, /class="chat-code"/);
  assert.match(html, /\{&quot;ok&quot;:true\}|\{"ok":true\}/);
});

test('pipe rows without a separator still become a table', () => {
  const html = md('Box | Hex | Purpose\nHealth | `#e5484d` | damage\nArmor | `#3b82f6` | shield');
  assert.match(html, /<table>/);
  assert.match(html, /<th>Box<\/th>/);
  assert.match(html, /<td>Health<\/td>/);
});

test('diff fences keep a code block', () => {
  const html = md('```diff\n- old\n+ new\n```');
  assert.match(html, /class="chat-code"/);
  assert.match(html, /- old/);
  assert.match(html, /\+ new/);
});

test('ansi sgr becomes color spans', () => {
  const html = ctx.renderAnsi('\x1b[31mfail\x1b[0m ok');
  assert.match(html, /class="ansi-red">fail<\/span>/);
  assert.match(html, /ok/);
});

test('looksLikeDiff detects unified hunks', () => {
  assert.equal(ctx.looksLikeDiff('--- a/x\n+++ b/x\n@@ -1 +1 @@\n-old\n+new'), true);
  assert.equal(ctx.looksLikeDiff('plain output'), false);
});

test('loose ordered lists stay one list instead of restarting at 1', () => {
  const html = md('1. first\n\n1. second\n   continued\n\n1. third\n\nAfter');
  assert.equal((html.match(/<ol/g) || []).length, 1);
  assert.equal((html.match(/<li>/g) || []).length, 3);
  assert.match(html, /second continued/);
  assert.match(html, /<\/ol><p>After<\/p>/);
});

test('ordered lists keep an explicit starting number', () => {
  assert.match(md('3. three\n4. four'), /^<ol start="3"><li>three<\/li><li>four<\/li><\/ol>$/);
  assert.match(md('1. one'), /^<ol><li>one<\/li><\/ol>$/);
});

test('a list may interrupt a paragraph without a blank line', () => {
  assert.equal(md('**box** — host\n- Roots: /tmp\n- Projects: a\n**next** — x'),
    '<p><strong>box</strong> — host</p><ul><li>Roots: /tmp</li><li>Projects: a</li></ul><p><strong>next</strong> — x</p>');
  assert.equal(md('Steps:\n1. a\n2. b'), '<p>Steps:</p><ol><li>a</li><li>b</li></ol>');
  // Only an ordered list starting at 1 may interrupt; "2024." is prose.
  assert.equal(md('Year was\n2024. Then more'), '<p>Year was<br>2024. Then more</p>');
});
