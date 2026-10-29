// What a fresh launch says about updates, from what the last run left in
// <userData>/update-state.json: {version, pending: {to, tries, installing}}.
// No Electron here, so tests/desktop_update_notice.test.cjs runs it under node.

// After this many installs of one version fail, stop retrying it on every quit
// (a .deb without a polkit agent would otherwise prompt and fail forever).
const MAX_INSTALL_TRIES = 3;

function launchNotice(state, current, hadUpdaterLog) {
  const pending = state.pending;
  // Builds before this file existed left no version, only the updater log.
  if (state.version ? state.version !== current : hadUpdaterLog) {
    const msg = state.version ? `${state.version} → ${current}` : `Now on ${current}`;
    return { state: { version: current }, toast: { kind: "ok", title: "Fuse updated", msg, ttl: 10000 } };
  }
  if (pending && pending.installing) {
    const tries = (pending.tries || 0) + 1;
    const title = `Fuse ${pending.to} didn't install`;
    const toast = tries >= MAX_INSTALL_TRIES
      ? { kind: "err", title, msg: `Still on ${current}. Install it from the Download page.`, actionLabel: "Download", href: "/download" }
      : { kind: "warn", title, msg: `Still on ${current}. Fuse tries again when it closes.`, ttl: 10000 };
    return { state: { version: current, pending: { to: pending.to, tries } }, toast };
  }
  return { state: { ...state, version: current }, toast: null };
}

module.exports = { MAX_INSTALL_TRIES, launchNotice };
