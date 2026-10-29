const test = require("node:test");
const assert = require("node:assert/strict");
const { MAX_INSTALL_TRIES, launchNotice } = require("../fuse-desktop/update-notice.js");

test("fresh install says nothing and records the version", () => {
  const { state, toast } = launchNotice({}, "1.0.7", false);
  assert.equal(toast, null);
  assert.deepEqual(state, { version: "1.0.7" });
});

test("a build from before the state file says which version it is now", () => {
  const { state, toast } = launchNotice({}, "1.0.7", true);
  assert.equal(toast.title, "Fuse updated");
  assert.equal(toast.msg, "Now on 1.0.7");
  assert.deepEqual(state, { version: "1.0.7" });
});

test("a new version names the old and new versions and drops the pending install", () => {
  const saved = { version: "1.0.7", pending: { to: "1.0.8", tries: 1, installing: true } };
  const { state, toast } = launchNotice(saved, "1.0.8", true);
  assert.equal(toast.kind, "ok");
  assert.equal(toast.msg, "1.0.7 → 1.0.8");
  assert.deepEqual(state, { version: "1.0.8" });
});

test("same version with no install attempt is quiet and keeps the downloaded update", () => {
  const saved = { version: "1.0.7", pending: { to: "1.0.8", tries: 0 } };
  const { state, toast } = launchNotice(saved, "1.0.7", true);
  assert.equal(toast, null);
  assert.deepEqual(state, saved);
});

test("a failed install warns, counts the try and clears the attempt", () => {
  const saved = { version: "1.0.7", pending: { to: "1.0.8", tries: 0, installing: true } };
  const { state, toast } = launchNotice(saved, "1.0.7", true);
  assert.equal(toast.kind, "warn");
  assert.equal(toast.title, "Fuse 1.0.8 didn't install");
  assert.deepEqual(state, { version: "1.0.7", pending: { to: "1.0.8", tries: 1 } });
  assert.equal(launchNotice(state, "1.0.7", true).toast, null);
});

test("the last allowed failure points at the download page", () => {
  const saved = { version: "1.0.7", pending: { to: "1.0.8", tries: MAX_INSTALL_TRIES - 1, installing: true } };
  const { state, toast } = launchNotice(saved, "1.0.7", true);
  assert.equal(toast.kind, "err");
  assert.equal(toast.href, "/download");
  assert.equal(state.pending.tries, MAX_INSTALL_TRIES);
});
