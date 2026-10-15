// Phone back gesture: every sheet, drawer, popover and dialog that should
// close on "back" registers here. open() pushes one history entry per layer;
// back pops the top layer only. A layer closed from its own UI steps history
// back over its entry instead of rewriting it, so no dead entry is left for
// the next swipe to land on. Layers closed from underneath stay as "dead"
// placeholders until everything above them is gone, then are skipped together.
window.FuseBack = (function fuseBack() {
  const mobile = window.matchMedia("(max-width: 800px)");
  const stack = []; // { id, close, dead, pushed }
  let inflight = 0; // our own traversals whose popstate has not arrived yet
  let backBy = 0;
  let flushQueued = false;
  const settledQueue = [];

  function carry() {
    const state = history.state;
    return state && state.fuseChat ? { fuseChat: true } : {};
  }
  function pushEntry(entry) {
    entry.pushed = true;
    history.pushState({ ...carry(), fuseBack: entry.id }, "");
  }
  let changeQueued = false;
  function changed() {
    if (changeQueued) return;
    changeQueued = true;
    queueMicrotask(() => {
      changeQueued = false;
      document.dispatchEvent(new Event("fuse:back-change"));
    });
  }
  function busy() {
    return inflight > 0 || flushQueued;
  }
  function settle() {
    if (busy()) return;
    for (const entry of stack) if (!entry.pushed && !entry.dead) pushEntry(entry);
    while (settledQueue.length && !busy()) settledQueue.shift()();
  }
  function goBack(n) {
    backBy += n;
    if (flushQueued) return;
    // Batch every close made in this task (e.g. a pane and the subagent open
    // on top of it) into one traversal; browsers drop a second back() that is
    // issued before the first one lands.
    flushQueued = true;
    queueMicrotask(() => {
      flushQueued = false;
      const k = backBy;
      backBy = 0;
      if (k > 0) {
        inflight += 1;
        history.go(-k);
      } else settle();
    });
  }
  function trimDead() {
    let n = 0;
    while (stack.length && stack[stack.length - 1].dead) {
      if (stack.pop().pushed) n += 1;
    }
    if (n) goBack(n);
  }
  function has(id) {
    return stack.some((entry) => entry.id === id && !entry.dead);
  }
  function open(id, close) {
    if (!mobile.matches || has(id)) return;
    const entry = { id, close, dead: false, pushed: false };
    stack.push(entry);
    if (!busy()) pushEntry(entry);
    changed();
  }
  function drop(id) {
    for (let i = stack.length - 1; i >= 0; i -= 1) {
      const entry = stack[i];
      if (entry.id !== id || entry.dead) continue;
      entry.dead = true;
      trimDead();
      changed();
      return;
    }
  }
  // Run fn once no traversal of ours is pending, so a URL write lands on the
  // entry the page ends up on rather than on one that is about to be popped.
  function whenSettled(fn) {
    if (busy()) settledQueue.push(fn);
    else fn();
  }
  window.addEventListener("popstate", (event) => {
    if (inflight > 0) {
      inflight -= 1;
      event.fuseBackHandled = true;
      settle();
      return;
    }
    if (stack.length) {
      event.fuseBackHandled = true;
      const top = stack.pop();
      trimDead();
      if (!top.dead) {
        try { top.close(); } catch (error) { console.error(error); }
      }
      changed();
      if (!busy()) settle();
      return;
    }
    if (event.state && event.state.fuseBack) {
      // A layer entry left over from before a reload: nothing is open for it.
      event.fuseBackHandled = true;
      inflight += 1;
      history.back();
    }
  });

  // Modal <dialog>s on every page (confirm, Hosts, Tasks,
  // Sources) register themselves: back closes the dialog like its Close button.
  let dialogSeq = 0;
  function watchDialog(dialog) {
    if (dialog._fuseBackId) return;
    dialog._fuseBackId = "dialog:" + (++dialogSeq);
    const sync = () => {
      if (dialog.open) open(dialog._fuseBackId, () => { if (dialog.open) dialog.close(); });
      else drop(dialog._fuseBackId);
    };
    new MutationObserver(sync).observe(dialog, { attributes: true, attributeFilter: ["open"] });
    if (dialog.open) sync();
  }
  function watchDialogs(rootEl) {
    rootEl.querySelectorAll?.("dialog").forEach(watchDialog);
  }
  watchDialogs(document);
  new MutationObserver((records) => {
    for (const record of records) {
      record.addedNodes.forEach((node) => {
        if (node.nodeType !== 1) return;
        if (node.tagName === "DIALOG") watchDialog(node);
        else watchDialogs(node);
      });
    }
  }).observe(document.documentElement, { childList: true, subtree: true });

  // An iOS Home Screen app has no system back swipe. Give it one: a drag that
  // starts at the left edge steps back, with a chevron that follows the finger.
  if (window.navigator.standalone === true) {
    let swipe = null;
    let cue = null;
    const EDGE = 18;
    const TRIGGER = 72;
    const cueEl = () => {
      if (cue) return cue;
      cue = document.createElement("div");
      cue.className = "fuse-back-cue";
      cue.setAttribute("aria-hidden", "true");
      cue.innerHTML = '<svg viewBox="0 0 16 16" width="16" height="16"><path d="M10 3.5L5.5 8l4.5 4.5" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>';
      document.body.appendChild(cue);
      return cue;
    };
    document.addEventListener("touchstart", (event) => {
      const touch = event.touches[0];
      if (event.touches.length !== 1 || !touch || touch.clientX > EDGE) { swipe = null; return; }
      if (event.target.closest?.("[data-fuse-dial], input[type=range]")) { swipe = null; return; }
      swipe = { x: touch.clientX, y: touch.clientY, dx: 0, locked: false };
    }, { passive: true });
    document.addEventListener("touchmove", (event) => {
      if (!swipe) return;
      const touch = event.touches[0];
      const dx = touch.clientX - swipe.x;
      const dy = touch.clientY - swipe.y;
      if (!swipe.locked) {
        if (Math.abs(dy) > 12 && Math.abs(dy) > Math.abs(dx)) { swipe = null; return; }
        if (dx < 10) return;
        swipe.locked = true;
      }
      swipe.dx = Math.max(0, dx);
      const el = cueEl();
      const p = Math.min(1, swipe.dx / TRIGGER);
      el.style.top = swipe.y + "px";
      el.style.setProperty("--p", p.toFixed(3));
      el.classList.toggle("is-armed", p >= 1);
      el.classList.add("is-on");
      if (event.cancelable) event.preventDefault();
    }, { passive: false });
    const end = () => {
      if (!swipe) return;
      const fire = swipe.locked && swipe.dx >= TRIGGER;
      swipe = null;
      if (cue) cue.classList.remove("is-on", "is-armed");
      if (fire) history.back();
    };
    document.addEventListener("touchend", end, { passive: true });
    document.addEventListener("touchcancel", end, { passive: true });
  }

  function layers() {
    return stack.filter((entry) => !entry.dead).map((entry) => entry.id);
  }
  return { open, drop, has, layers, whenSettled, isMobile: () => mobile.matches };
})();
