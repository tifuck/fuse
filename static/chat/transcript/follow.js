// Scroll capture, follow/pause, snap, restore.
import { S } from '../state.js';

let threadFollowFrame = 0;

let threadFollowThread = null;

let resumeFollowOnScroll = true;

export function createFollow(ctx) {
  const { ui } = ctx;
    function restoreThreadScroll(wasAtBottom, savedTop) {
    if (!S.thread) return;
    S.ignoreScroll = true;
    if (wasAtBottom) {
      S.stickToBottom = true;
      snapThread();
    } else {
      S.thread.scrollTop = savedTop;
    }
    syncLatest();
    window.requestAnimationFrame(() => {
      if (S.stickToBottom) snapThread();
      S.ignoreScroll = false;
      syncLatest();
    });
  }

    function nearBottom() {
    const gap = S.thread.scrollHeight - S.thread.scrollTop - S.thread.clientHeight;
    return gap < 96;
  }

    function captureThreadViewport() {
    const threadId = S.log._threadId === undefined ? S.currentId : S.log._threadId;
    // Another thread's reading position is never restored (see
    // restoreThreadViewport), so do not force a layout to measure it.
    if (threadId !== S.currentId) return { threadId, top: S.lastThreadScrollTop || 0, following: S.stickToBottom };
    const edge = S.thread.getBoundingClientRect().top;
    const anchor = [...S.log.children].find(el => !el.hidden && el.getBoundingClientRect().bottom > edge);
    return { threadId, top: S.thread.scrollTop, following: S.stickToBottom,
      anchorId: anchor?.dataset.messageId, offset: anchor ? anchor.getBoundingClientRect().top - edge : 0 };
  }

    function restoreThreadViewport(saved) {
    if (!saved || saved.threadId !== S.currentId) return;
    const anchor = saved.anchorId && [...S.log.children].find(el => el.dataset.messageId === saved.anchorId && !el.hidden);
    // Restore the reading anchor synchronously, before a follow animation can
    // start. Never animate from the temporary zero caused by replacing nodes.
    const target = anchor ? S.thread.scrollTop + anchor.getBoundingClientRect().top - S.thread.getBoundingClientRect().top - saved.offset : saved.top;
    S.thread.scrollTop = Math.max(0, target);
    S.lastThreadScrollTop = S.thread.scrollTop;
    if (saved.following && S.stickToBottom) scrollThread();
    else syncLatest();
  }

    // The scroll gap at which the running reply's status line slips out of
  // view: its spinner's middle reaches the composer laid over the transcript,
  // or the transcript's bottom edge where the composer sits below it. Null
  // while no status line is showing.
  function liveStatusEdge(gap) {
    if (!S.liveDock || S.liveDock.hidden || S.liveDock.getAttribute("aria-hidden") === "true") return null;
    const mark = (S.liveDockSpin?.getClientRects().length ? S.liveDockSpin : S.liveDock).getBoundingClientRect();
    const view = S.thread.getBoundingClientRect();
    const dockTop = S.form ? S.form.getBoundingClientRect().top : view.bottom;
    return gap - (mark.top + mark.height / 2 - Math.min(view.bottom, dockTop));
  }

    function syncLatestSpin(live) {
    S.latestPill.classList.toggle("is-live", live);
    if (!S.latestSpin) return;
    if (!live) {
      if (S.latestSpin.firstChild) S.latestSpin.replaceChildren();
      delete S.latestSpin.dataset.spinSet;
    } else ui.setConsoleSpin(S.latestSpin);
  }

    function syncLatest() {
    if (!S.latestPill) return;
    // A reader who nudged up a line is still at the end; the pill waits until
    // they are genuinely away (or output piles up below), and once shown it
    // holds until they are back near the bottom, so it never flickers at the
    // threshold. While a reply runs, its status line ends the transcript and
    // the pill takes over its spinner the moment that line scrolls out of
    // view, handing it back once the line is visible again.
    const gap = S.thread.scrollHeight - S.thread.scrollTop - S.thread.clientHeight;
    const edge = liveStatusEdge(gap);
    const away = edge == null ? Math.max(160, S.thread.clientHeight * 0.35) : edge;
    const back = edge == null ? 48 : edge - 8;
    const hide = !S.messages.length || gap <= 1 || (S.stickToBottom && nearBottom()) || gap < (S.latestPill.hidden ? away : back);
    if (hide) {
      S.latestPill.hidden = true;
      syncLatestSpin(false);
      return;
    }
    syncLatestSpin(edge != null);
    S.latestPill.hidden = false;
  }

    function threadFollowTarget() {
    return Math.max(0, S.thread.scrollHeight - S.thread.clientHeight);
  }

    function snapThread() {
    if (!S.thread || !S.stickToBottom) return;
    const target = threadFollowTarget();
    // Tolerate a rounding pixel: fractional console geometry can flip the
    // integer scrollHeight by 1px, and correcting that flip makes the whole
    // viewport oscillate a pixel; real growth is a line or more.
    if (Math.abs(target - S.thread.scrollTop) > 1) S.thread.scrollTop = target;
    S.lastThreadScrollTop = S.thread.scrollTop;
    syncLatest();
  }

    function pauseThreadFollow() {
    S.stickToBottom = false;
    resumeFollowOnScroll = false;
    S.ignoreScroll = false;
    if (threadFollowFrame) cancelAnimationFrame(threadFollowFrame);
    if (S.liveDockFollowFrame) cancelAnimationFrame(S.liveDockFollowFrame);
    threadFollowFrame = S.liveDockFollowFrame = 0;
    S.lastThreadScrollTop = S.thread.scrollTop;
    syncLatest();
  }

    // Following the bottom is pinning, not scrolling: the content grows and the
  // viewport moves with it in the same frame, so the newest text and the status
  // line hold one screen position while text fills in above them. An eased
  // follow (the old behavior) let each chunk land below the view first and
  // glide up a few frames later, which read as text popping in under the
  // composer. Only the "Jump to latest" pill glides, from a real distance.
  function scrollThread(glide = false) {
    const entering = threadFollowThread !== S.currentId;
    threadFollowThread = S.currentId;
    if (!S.stickToBottom) {
      syncLatest();
      return;
    }
    const target = threadFollowTarget();
    const gap = target - S.thread.scrollTop;
    if (entering || !glide || S.reduceMotion.matches || !S.streaming || Math.abs(gap) <= 1) {
      if (threadFollowFrame) {
        cancelAnimationFrame(threadFollowFrame);
        threadFollowFrame = 0;
      }
      snapThread();
      return;
    }
    if (threadFollowFrame) return;
    let lastTime = performance.now();
    const step = now => {
      if (!S.stickToBottom) { threadFollowFrame = 0; return; }
      const next = threadFollowTarget();
      const remain = next - S.thread.scrollTop;
      if (S.reduceMotion.matches || !S.streaming || Math.abs(remain) <= 1) {
        snapThread();
        threadFollowFrame = 0;
        return;
      }
      const dt = Math.min(48, Math.max(8, now - lastTime));
      lastTime = now;
      S.thread.scrollTop += Math.sign(remain) * Math.max(1, Math.abs(remain) * (1 - Math.exp(-dt / 48)));
      threadFollowFrame = requestAnimationFrame(step);
    };
    threadFollowFrame = requestAnimationFrame(step);
    syncLatest();
  }

  return {
    captureThreadViewport,
    nearBottom,
    pauseThreadFollow,
    restoreThreadScroll,
    restoreThreadViewport,
    scrollThread,
    snapThread,
    syncLatest,
    threadFollowTarget,
    get resumeFollowOnScroll() { return resumeFollowOnScroll; },
    set resumeFollowOnScroll(value) { resumeFollowOnScroll = value; },
    get threadFollowFrame() { return threadFollowFrame; },
  };
}

export { resumeFollowOnScroll, threadFollowFrame };
