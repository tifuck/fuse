// Composer sizing, flip, viewport pinning, hint.
import { S } from '../state.js';

let dockSpaceLocked = false;

let viewportFrame = 0;

let viewportWidth = window.innerWidth;

let viewportBaseline = window.innerHeight;

let viewportHeight = NaN;

let viewportOffset = NaN;

let viewportBlurredAt = 0;

// How long blur keeps the keyboard layout while the keyboard animates away.
const KEYBOARD_CLOSE_MS = 500;

let chatBarFitRaf = 0;

export function createInput(ctx) {
  const { net, overlays, transcript } = ctx;
    // Composer popovers (model, permissions, context) share one placement:
  // rise from the top of the whole composer, line up with the button's left
  // edge (or its right edge when pinned or out of room), and clamp to the
  // viewport. The origin is measured rather than assumed, because a chat
  // backdrop gives .chat-box a backdrop-filter, which makes it the containing
  // block for even position:fixed children.
  function placeComposerPop(menu, anchor, maxHeight, pinRight) {
    if (!menu || menu.hidden || !anchor || menu.classList.contains("is-sheet")) return;
    const gutter = 8;
    menu.style.top = "auto";
    menu.style.right = "auto";
    menu.style.left = "0px";
    menu.style.bottom = "0px";
    // The open animation translates the box; measure it at rest.
    menu.style.setProperty("transform", "none", "important");
    const origin = menu.getBoundingClientRect();
    menu.style.removeProperty("transform");
    const b = anchor.getBoundingClientRect();
    const box = anchor.closest(".chat-box");
    const top = (box ? box.getBoundingClientRect().top : b.top) - 6;
    menu.style.maxHeight = Math.max(200, Math.min(maxHeight, Math.round(top - 48))) + "px";
    const width = menu.offsetWidth;
    const vw = document.documentElement.clientWidth;
    let left = b.left;
    if ((pinRight && !overlays.overlayIsMobile()) || left + width > vw - gutter) left = b.right - width;
    left = Math.max(gutter, Math.min(left, vw - gutter - width));
    menu.style.left = Math.round(left - origin.left) + "px";
    menu.style.bottom = Math.round(origin.bottom - top) + "px";
  }

    function resizeInput() {
    // Placeholder text is excluded from the measurement: engines fold wrapped
    // placeholder lines into scrollHeight, which would balloon the terminal
    // composer's single-row layout on narrow screens.
    const ph = S.input.placeholder;
    if (!S.input.dataset.placeholder) S.input.dataset.placeholder = ph;
    const wanted = S.pickerSheetQuery.matches ? "Ask or type /" : S.input.dataset.placeholder;
    if (ph !== wanted) S.input.placeholder = wanted;
    S.input.placeholder = "";
    const followGap = S.thread ? S.thread.scrollHeight - S.thread.scrollTop - S.thread.clientHeight : Infinity;
    const threadTop = S.thread ? S.thread.scrollTop : 0;
    const previousHeight = S.input.style.height;
    dockSpaceLocked = true;
    S.input.style.height = "auto";
    syncComposerExpanded();
    const style = window.getComputedStyle(S.input);
    const min = parseFloat(style.minHeight) || 0;
    const max = parseFloat(style.maxHeight) || 192;
    const nextHeight = Math.min(Math.max(S.input.scrollHeight, min), Math.max(min, max)) + "px";
    S.input.style.height = nextHeight;
    dockSpaceLocked = false;
    S.input.placeholder = wanted;
    // The height:auto measure collapses a wrapped field to one row for an
    // instant. Where the dock is in flow (Console) that grows the transcript,
    // and the browser clamps its scrollTop to the new max. Restore it before
    // paint, or every keystroke drops the log a line and the eased follow
    // drags it back: a per-key jitter while a reply streams.
    if (S.thread && S.thread.scrollTop !== threadTop) S.thread.scrollTop = threadTop;
    // Keep the transcript glued while the row grows or shrinks: the eased
    // follow lags the layout change, so without this re-pin the log bounces
    // up and down on every new wrapped line. Skip the snap when height is
    // unchanged — overlay/focus remeasures used to bounce a settled log.
    if (S.thread && (S.stickToBottom || followGap <= 1) && previousHeight !== nextHeight) S.thread.scrollTop = Math.max(0, S.thread.scrollHeight - S.thread.clientHeight);
    syncDockSpace();
    if (window.FuseComposerSpell) FuseComposerSpell.syncMetrics();
  }

    function syncScrollGutter() {
    if (!S.root || !S.thread) return;
    const gutter = Math.max(0, Math.round(S.thread.offsetWidth - S.thread.clientWidth));
    if (S.root.style.getPropertyValue("--chat-scroll-gutter") !== gutter + "px") S.root.style.setProperty("--chat-scroll-gutter", gutter + "px");
  }

    function syncDockSpace() {
    if (!S.root || !S.form) return;
    const h = S.form.getBoundingClientRect().height;
    const value = Math.max(0, Math.round(h)) + "px";
    if (S.root.style.getPropertyValue("--dock-h") !== value) S.root.style.setProperty("--dock-h", value);
    syncScrollGutter();
  }

    function syncComposerExpanded() {
    const expanded = S.root.classList.contains("is-composer-expanded");
    let next = false;
    if (S.input.value) {
      if (expanded || S.input.value.includes("\n")) next = true;
      else {
        const style = window.getComputedStyle(S.input);
        const line = parseFloat(style.lineHeight) || 24;
        const pad = (parseFloat(style.paddingTop) || 0) + (parseFloat(style.paddingBottom) || 0);
        next = S.input.scrollHeight - pad > line * 1.5;
      }
    }
    if (next === expanded) return;
    const moving = composerFlipTargets();
    const before = moving.map((el) => el.getBoundingClientRect());
    S.root.classList.toggle("is-composer-expanded", next);
    composerFlip(moving, before);
  }

    function composerFlipTargets() {
    if (S.reduceMotion.matches || !S.form) return [];
    const box = S.form.querySelector(".chat-box");
    if (!box) return [];
    return [box.querySelector(".chat-compose"), ...box.querySelectorAll(".chat-box-left > *, .chat-box-right > *")]
      .filter((el) => el && el.offsetParent !== null);
  }

    // FLIP: every piece glides from where it was to where the new layout puts
  // it, so the reflow reads as one motion instead of a jump. Transforms only;
  // the caret and focus stay on the same textarea throughout.
  function composerFlip(moving, before) {
    moving.forEach((el, i) => {
      const was = before[i];
      const now = el.getBoundingClientRect();
      const dx = was.left - now.left;
      const dy = was.top - now.top;
      if (Math.abs(dx) < 1 && Math.abs(dy) < 1) return;
      el.animate(
        [{ transform: `translate(${dx}px, ${dy}px)` }, { transform: "none" }],
        { duration: S.MOTION.base, easing: S.MOTION.snap },
      );
    });
  }

    function pinVisualViewport() {
    if (viewportFrame) return;
    viewportFrame = window.requestAnimationFrame(() => {
      viewportFrame = 0;
      const vv = window.visualViewport;
      // Zoom is browser magnification, not a smaller application viewport.
      // Measure the layout viewport instead of skipping the update: iOS stays
      // zoomed after a small field's focus zoom, and skipping froze the app
      // at its keyboard height.
      const zoomed = Boolean(vv && Math.abs(vv.scale - 1) > 0.01);
      const inner = window.innerHeight;
      const vvHeight = vv && !zoomed ? vv.height : inner;
      const vvOffset = vv && !zoomed ? Math.max(0, vv.offsetTop) : 0;
      const composerFocus = S.form.contains(document.activeElement) &&
        document.activeElement.matches("textarea, input, [contenteditable='true']");
      const mobile = window.innerWidth <= 800;
      const sheetFocus = document.activeElement instanceof Element &&
        Boolean(document.activeElement.closest(".chat-sheet, .chat-view-menu"));
      const editing = composerFocus || sheetFocus;
      if (Math.abs(window.innerWidth - viewportWidth) > 1) {
        viewportWidth = window.innerWidth;
        viewportBaseline = inner;
      }
      // Android can resize both viewports; preserve the pre-keyboard height.
      viewportBaseline = Math.max(viewportBaseline, inner, Math.round(vvHeight + vvOffset));
      const occluded = viewportBaseline - vvHeight > 80;
      if (editing) viewportBlurredAt = 0;
      else if (!viewportBlurredAt) viewportBlurredAt = performance.now();
      // Blur keeps the keyboard layout only while the keyboard animates away
      // (the focusout re-pins at 700ms settle it). A viewport still occluded
      // after that is a stale WebKit report, not a keyboard.
      const closing = document.body.classList.contains("is-keyboard") &&
        performance.now() - viewportBlurredAt < KEYBOARD_CLOSE_MS;
      const keyboard = mobile && occluded && (editing || closing);
      if (!keyboard && !editing) viewportBaseline = inner;
      // Keyboard open: do not apply offsetTop. Android Chrome/Brave with
      // interactive-widget=resizes-content already shrinks the layout; a
      // leftover visual-viewport pan plus top:offsetTop leaves a black gap
      // above the keyboard. Cancel the pan and fill the remaining area.
      let height = Math.max(1, Math.round(vvHeight));
      let offset = keyboard ? 0 : Math.max(0, Math.round(vvOffset));
      if (keyboard) {
        if (vv && vvOffset > 1) {
          try { vv.scrollTo(0, 0); } catch (_) {}
        }
        document.documentElement.scrollTop = 0;
        document.body.scrollTop = 0;
        const layoutLooksResized = inner <= vvHeight + vvOffset + 40;
        height = Math.max(1, Math.round(layoutLooksResized ? inner : (vv ? vv.height : inner)));
      }
      const wasAtBottom = S.stickToBottom;
      const savedTop = S.thread ? S.thread.scrollTop : 0;
      const rootStyle = document.documentElement.style;
      const heightStable = Math.abs(viewportHeight - height) <= 1;
      const offsetStable = Math.abs(viewportOffset - offset) <= 1;
      const keyboardChanged = document.body.classList.contains("is-keyboard") !== keyboard;
      const layoutChanged = keyboardChanged || !heightStable || !offsetStable;
      if (!layoutChanged && !composerFocus && !keyboard) return;
      viewportHeight = height;
      viewportOffset = offset;
      // At rest the app is plain CSS 100dvh; only an open keyboard pins the
      // measured height. A pinned rest height inherits every bad WebKit
      // report and shows as a dead band under the composer.
      if (keyboard) rootStyle.setProperty("--vv-height", height + "px");
      else rootStyle.removeProperty("--vv-height");
      rootStyle.setProperty("--vv-offset", offset + "px");
      document.body.classList.toggle("is-keyboard", keyboard);
      if (keyboard && sheetFocus && document.activeElement instanceof Element) {
        document.activeElement.scrollIntoView({ block: "nearest" });
      }
      if (composerFocus || keyboard) resizeInput();
      else {
        syncEmptyComposer();
        syncDockSpace();
      }
      if (layoutChanged) transcript.restoreThreadScroll(wasAtBottom, savedTop);
    });
  }

    function syncComposer() {
    const hasText = Boolean(S.input.value.trim());
    const hasAttach = S.pendingAttachments.length > 0;
    const stop = S.streaming && !(hasText || hasAttach);
    S.sendBtn.disabled = S.streaming ? false : S.sending || S.loading || !(hasText || hasAttach) || !S.modelId || !net.isLive();
    S.sendBtn.classList.toggle("is-stop", stop);
    S.sendBtn.setAttribute("aria-label", stop ? "Stop" : "Send");
    S.newBtns.forEach((btn) => {
      btn.disabled = S.sending;
    });
    S.input.disabled = !S.allModels.length;
    if (S.attachBtn) S.attachBtn.disabled = S.streaming || S.sending || !S.modelId;
  }

    // Mobile wraps the bar onto extra rows; publish its measured height so the
  // thread and top fade clear it. Desktop is a fixed single row.
  function fitChatBar() {
    const bar = S.root.querySelector(".chat-bar");
    if (!bar) return;
    const beforeH = S.root.style.getPropertyValue("--chat-bar-h");
    const mobile = window.matchMedia("(max-width: 800px)").matches;
    // Desktop with nothing published: no change, so no forced layout read.
    if (!mobile && !beforeH) return;
    const following = S.stickToBottom;
    const savedTop = S.thread ? S.thread.scrollTop : 0;
    if (mobile) {
      S.root.style.setProperty("--chat-bar-h", Math.ceil(bar.getBoundingClientRect().height) + "px");
    } else {
      S.root.style.removeProperty("--chat-bar-h");
    }
    if (following && beforeH !== S.root.style.getPropertyValue("--chat-bar-h")) transcript.restoreThreadScroll(true, savedTop);
  }

    function scheduleFitChatBar() {
    if (chatBarFitRaf) return;
    chatBarFitRaf = window.requestAnimationFrame(() => {
      chatBarFitRaf = 0;
      fitChatBar();
    });
  }

    function playDockTransition() {
    {
      transcript.playTranscriptFx();
      return;
    }

  }

    function syncEmptyComposer() {
    const filling = Boolean((S.input && S.input.value.trim()) || S.pendingAttachments.length);
    const empty = S.root.classList.contains("is-empty");
    if (!empty || !filling) {
      S.root.classList.remove("is-composing");
      return;
    }
    S.root.classList.add("is-composing");
  }

  return {
    pinVisualViewport,
    placeComposerPop,
    playDockTransition,
    resizeInput,
    scheduleFitChatBar,
    syncComposer,
    syncDockSpace,
    syncEmptyComposer,
    get dockSpaceLocked() { return dockSpaceLocked; },
  };
}

export { dockSpaceLocked };
