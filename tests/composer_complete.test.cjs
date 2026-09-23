const { test } = require("node:test");
const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { resolve } = require("node:path");
const vm = require("node:vm");

function load() {
  const world = { console };
  world.window = world;
  world.globalThis = world;
  vm.runInNewContext(readFileSync(resolve(__dirname, "../static/composer-complete.js"), "utf8"), world);
  return world.FuseComposerComplete;
}

const complete = load();

test("and/ does not trigger path without a known path", () => {
  const t = complete.trigger("and/", 4, { traces: [], listing: null, attachments: [] });
  assert.equal(t, null);
});

test("src/ triggers when listing has src", () => {
  const ctx = { listing: { path: "/proj", entries: [{ name: "src", kind: "dir" }] } };
  const t = complete.trigger("src/", 4, ctx);
  assert.ok(t);
  assert.equal(t.kind, "path");
  const rows = complete.items(t, ctx);
  assert.ok(rows.some((r) => r.insert === "/proj/src" || r.insert.endsWith("/src")));
});

test("@ empty query still produces items", () => {
  const ctx = {
    projects: [{ project_name: "fuse", remote_path: "/root/fuse", host_alias: "local" }],
    skills: [{ slug: "ponytail", description: "style" }],
    traces: [],
    attachments: [],
    threads: [],
  };
  const t = complete.trigger("@", 1, ctx);
  assert.equal(t.kind, "at");
  const rows = complete.items(t, ctx);
  assert.ok(rows.length > 0);
  assert.ok(rows.some((r) => r.kind === "skill" && r.insert === "ponytail"));
});

test("@skill inserts slug not /slug", () => {
  const ctx = { skills: [{ slug: "ponytail" }], projects: [], traces: [], attachments: [], threads: [] };
  const t = complete.trigger("please run @pony", 16, ctx);
  const rows = complete.items(t, ctx);
  assert.equal(rows[0].insert, "ponytail");
  assert.notEqual(rows[0].insert[0], "/");
});

test("./src is a path trigger", () => {
  const t = complete.trigger("./src", 5, { traces: [], listing: null });
  assert.equal(t.kind, "path");
});

test("slash at start is informational slash", () => {
  const t = complete.trigger("/hel", 4, {});
  assert.equal(t.kind, "slash");
});

test("history skips auto and builtin slash", () => {
  const rows = complete.history({
    prompts: ["/undo last", "Build the dock", "/help", "Build the dock"],
  }).map(String);
  assert.equal(JSON.stringify(rows), JSON.stringify(["Build the dock"]));
});

test("listing insert joins path and basename", () => {
  assert.equal(complete.joinPath("/root/fuse/", "src"), "/root/fuse/src");
});
