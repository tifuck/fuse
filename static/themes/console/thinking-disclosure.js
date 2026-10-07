// thinking-disclosure presentation. Dependencies are live getters supplied by index.js.
export function create(deps) {
function thinkingPosition(peek) {
    const state = peek?._thinking;
    if (state?.frame && state.initialized && Math.abs(peek.scrollTop - state.lastTop) > 2) stopThinkingFollow(peek);
    return { top: peek?.scrollTop || 0,
      pinned: !peek?.clientHeight ? state?.follow !== false : state?.frame ? state.follow : peek.scrollHeight - peek.clientHeight - peek.scrollTop <= 3,
      initialized: Boolean(state?.initialized) };
  }

function stopThinkingFollow(peek) {
    if (!peek?._thinking) return;
    const state = peek._thinking;
    state.follow = false;
    state.snap = false;
    if (state.frame) cancelAnimationFrame(state.frame);
    state.frame = 0;
  }

function followThinking(peek, snap = false) {
    const state = peek._thinking;
    if (!state || !state.follow) return;
    state.snap ||= snap;
    if (state.frame) return;
    state.lastTop = peek.scrollTop;
    let previous = performance.now();
    const step = now => {
      if (!peek.isConnected || !state.follow) { state.frame = 0; return; }
      // Initial render happens in a detached article. Measure only now, after
      // attachment and the live/expanded CSS have established the viewport.
      if (!peek.clientHeight) { state.frame = 0; return; }
      const target = Math.max(0, peek.scrollHeight - peek.clientHeight);
      const gap = target - peek.scrollTop;
      if (state.snap || deps.reduceMotion.matches || Math.abs(gap) <= 1) {
        peek.scrollTop = target;
        state.lastTop = peek.scrollTop;
        state.initialized = true; state.snap = false; state.frame = 0;
        return;
      }
      const dt = Math.min(48, Math.max(8, now - previous)); previous = now;
      peek.scrollTop += Math.sign(gap) * Math.max(1, Math.abs(gap) * (1 - Math.exp(-dt / 42)));
      state.lastTop = peek.scrollTop;
      state.frame = requestAnimationFrame(step);
    };
    state.frame = requestAnimationFrame(step);
  }

function initThinking(peek) {
    if (peek._thinking) return;
    const state = peek._thinking = { follow: true, initialized: false, frame: 0, snap: false, touchY: null, lastTop: peek.scrollTop };
    peek.addEventListener("wheel", event => { if (event.deltaY < 0) stopThinkingFollow(peek); }, { passive: true });
    peek.addEventListener("touchstart", event => { state.touchY = event.touches[0]?.clientY ?? null; }, { passive: true });
    peek.addEventListener("touchmove", event => {
      const y = event.touches[0]?.clientY;
      if (y != null && state.touchY != null && y > state.touchY) stopThinkingFollow(peek);
      state.touchY = y ?? null;
    }, { passive: true });
    peek.addEventListener("keydown", event => { if (["ArrowUp", "PageUp", "Home"].includes(event.key)) stopThinkingFollow(peek); });
    peek.addEventListener("pointerdown", event => {
      if (event.target === peek && event.clientX >= peek.getBoundingClientRect().right - 16) stopThinkingFollow(peek);
    }, { passive: true });
    peek.addEventListener("scroll", () => {
      if (state.frame && state.initialized && Math.abs(peek.scrollTop - state.lastTop) > 2) stopThinkingFollow(peek);
      if (!state.frame && peek.clientHeight) {
        state.follow = peek.scrollHeight - peek.clientHeight - peek.scrollTop <= 3;
        state.lastTop = peek.scrollTop;
      }
    }, { passive: true });
    const observer = new ResizeObserver(() => {
      if (!peek.isConnected) { observer.disconnect(); stopThinkingFollow(peek); return; }
      if (state.follow) followThinking(peek, !state.initialized);
    });
    observer.observe(peek);
    observer.observe(peek.firstElementChild);
    state.observer = observer;
  }

function disposeThinking(container) {
    container?.querySelectorAll("[data-reason-preview]").forEach(peek => {
      stopThinkingFollow(peek);
      peek._thinking?.observer?.disconnect();
    });
  }

function syncThinkingMode(li, msg, animate = false) {
    const windowEl = li.querySelector(".chat-think-window");
    // Console hides the whole bundle when collapsed, so that is what moves.
    const revealEl = (li.querySelector(".chat-console-bundle") || windowEl);
    const height = animate ? revealEl.getBoundingClientRect().height : 0;
    const previousMode = li.dataset.thinkMode;
    const key = li.dataset.step;
    const choice = msg.stepOpen?.[key];
    const live = li.classList.contains("is-live-think");
    const mode = choice === true ? "expanded" : choice === false ? "collapsed" : live ? "tail" : "collapsed";
    li.dataset.thinkMode = mode;
    li.classList.toggle("is-console-expanded", mode === "expanded");
    li.querySelectorAll(":scope > .chat-step-main > .chat-step-head [data-step-toggle]").forEach(button => button.setAttribute("aria-expanded", String(mode !== "collapsed")));
    const size = li.querySelector("[data-think-size]");
    size.hidden = !live || mode === "collapsed";
    size.textContent = (mode === "expanded" ? "hide" : "stream");
    size.setAttribute("aria-label", (mode === "expanded" ? "Hide thinking stream" : "Show thinking stream"));
    const peek = li.querySelector("[data-reason-preview]");
    peek.tabIndex = mode === "collapsed" ? -1 : 0;
    windowEl.inert = mode === "collapsed";
    if (animate && previousMode !== mode) deps.setToolReveal(revealEl, mode !== "collapsed", height);
    return mode;
  }

  return { thinkingPosition, stopThinkingFollow, followThinking, initThinking, disposeThinking, syncThinkingMode };
}
