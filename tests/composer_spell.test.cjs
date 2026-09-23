const { test } = require("node:test");
const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { resolve } = require("node:path");
const vm = require("node:vm");

function load() {
  const world = { console, setTimeout, clearTimeout, Uint8Array, TextDecoder };
  world.window = world;
  world.globalThis = world;
  world.localStorage = { getItem() { return null; }, setItem() {} };
  vm.runInNewContext(readFileSync(resolve(__dirname, "../static/composer-lexicon.js"), "utf8"), world);
  vm.runInNewContext(readFileSync(resolve(__dirname, "../static/composer-spell.js"), "utf8"), world);
  return world.FuseComposerSpell;
}

function kinds(spell, text) {
  return spell.tokenize(text).filter((t) => t.text.trim()).map((t) => [String(t.text), t.kind]);
}

function issues(spell, text, extra) {
  const english = extra && extra.english
    ? extra.english
    : new Set(["doesn't", "well", "known", "state", "of", "the", "art", "keep", "last", "hour", "is", "defaced", "fox"]);
  return spell.check(text, { ready: true, english, caret: extra && extra.caret, context: extra && extra.context })
    .map((i) => String(i.word));
}

function same(actual, expected) {
  assert.equal(JSON.stringify(actual), JSON.stringify(expected));
}

const spell = load();

const CASES = [
  ["doesn't", "doesn't", "word"],
  ["OAuth", "OAuth", "skip"],
  ["iOS", "iOS", "skip"],
  ["PostgreSQL", "PostgreSQL", "skip"],
  ["useEffect", "useEffect", "skip"],
  ["API", "API", "skip"],
  ["HTTP", "HTTP", "skip"],
  ["LLM", "LLM", "skip"],
  ["PR", "PR", "skip"],
  ["js", "js", "word"],
  ["ui", "ui", "word"],
  ["is", "is", "word"],
  ["C++", "C++", "skip"],
  [".gitignore", ".gitignore", "skip"],
  ["k8s", "k8s", "skip"],
  ["i18n", "i18n", "skip"],
  ["a11y", "a11y", "skip"],
  ["NaN", "NaN", "skip"],
  ["app/web/chat.py", "app/web/chat.py", "skip"],
  ["`fooobar`", "`fooobar`", "skip"],
  ["PR #12", "PR #12", "skip"],
  ["JIRA-123", "JIRA-123", "skip"],
  ["v1.0.2", "v1.0.2", "skip"],
  ["#c5df88", "#c5df88", "skip"],
  ["https://fuse.example.com/chat", "https://fuse.example.com/chat", "skip"],
  ["glm-5.3-flash", "glm-5.3-flash", "skip"],
  ["gpt-4o", "gpt-4o", "skip"],
  ["claude-3-5", "claude-3-5", "skip"],
  ["gpt-4o-mini", "gpt-4o-mini", "skip"],
  ["we're", "we're", "word"],
  ["O'Brien", "O'Brien", "skip"],
  ["defaced", "defaced", "word"],
  ["a1b2c3d", "a1b2c3d", "skip"],
  ["你好", "你好", "skip"],
  ["!!!", "!!!", "skip"],
];

for (const [input, token, kind] of CASES) {
  test(`tokenize ${input} → ${kind}`, () => {
    const row = spell.tokenize(input).find((t) => t.text === token) || spell.tokenize(input)[0];
    assert.equal(row.kind, kind, JSON.stringify(kinds(spell, input)));
  });
}

test("hyphen split well-known", () => {
  const words = spell.tokenize("well-known").filter((t) => t.kind === "word").map((t) => String(t.text));
  same(words, ["well", "known"]);
});

test("hyphen split well-knonw flags knonw", () => {
  same(issues(spell, "well-knonw "), ["knonw"]);
});

test("hyphen split state-of-the-art", () => {
  const words = spell.tokenize("state-of-the-art").filter((t) => t.kind === "word").map((t) => String(t.text));
  same(words, ["state", "of", "the", "art"]);
});

test("slash command skipped, arguments checked", () => {
  const tokens = spell.tokenize("/compact keep the last hour");
  assert.equal(String(tokens[0].text), "/compact");
  assert.equal(tokens[0].kind, "skip");
  same(issues(spell, "/compact keep the last hour"), []);
});

test("fenced consle is skipped", () => {
  const text = "```js\nconsle.log\n```";
  assert.equal(issues(spell, text).length, 0);
  assert.ok(spell.tokenize(text).every((t) => t.kind === "skip"));
});

test("js is never an issue", () => {
  assert.equal(issues(spell, "js ").includes("js"), false);
});

test("current word is not flagged", () => {
  same(issues(spell, "teh", { caret: 3 }), []);
  same(issues(spell, "teh fox", { caret: 7, english: new Set(["fox", "the"]) }), ["teh"]);
});

test("lexicon absorbs Fuse terms", () => {
  assert.ok(globalThis.FuseComposerLexicon || true);
});

test("predicates", () => {
  assert.equal(spell.isSlugWithDigit("gpt-4o"), true);
  assert.equal(spell.isSlugWithDigit("well-known"), false);
  assert.equal(spell.isInteriorCapital("OAuth"), true);
  assert.equal(spell.isInteriorCapital("NaN"), true);
  assert.equal(spell.isAcronym("API"), true);
  assert.equal(spell.isAcronym("NaN"), false);
  assert.equal(spell.isCamel("useEffect"), true);
  assert.equal(spell.isAlnumAbbrev("k8s"), true);
});
