// History drawer state, collapse, resize.
import { S } from '../state.js';

let historyWasOpen = false;

const STORE_HISTORY_COLLAPSED = "fuse.chat.historyCollapsed.v1";
// Desktop peek: the pointer rests on the left edge this long before the
// collapsed list slides in, and it stays this long after the pointer leaves.
const PEEK_OPEN_MS = 120;
const PEEK_CLOSE_MS = 1000;

function historyCollapsedPrefs() {
  try { return JSON.parse(localStorage.getItem(STORE_HISTORY_COLLAPSED) || "{}") || {}; } catch (_) { return {}; }
}

export function createDrawer(ctx) {
  const { agents, artifacts, composer, overlays, panels } = ctx;
    const historyPanel = S.root.querySelector("[data-history]");

    const historyCollapse = historyPanel?.querySelector("[data-history-toggle]");

    const historyBackdrop = S.root.querySelector("[data-history-backdrop]");

    const historyBackground = S.root.querySelectorAll(".chat-thread, .chat-dock, .chat-latest, .chat-bar > :not([data-history-toggle])");

    const historyEdge = S.root.querySelector("[data-history-edge]");

    let peekOpenTimer = 0;

    let peekCloseTimer = 0;

    let peekHover = false;

    function clearPeekTimers() {
    clearTimeout(peekOpenTimer);
    clearTimeout(peekCloseTimer);
    peekOpenTimer = 0;
  }

    function canPeek() {
    return !S.historyMobile.matches && S.root.classList.contains("history-collapsed");
  }

    function peeking() {
    return S.root.classList.contains("history-peek");
  }

    // Overlays the collapsed list without unpinning it. Closing slides it out
  // under history-peek-leave, so collapsing or loading the page never animates.
  function setHistoryPeek(open) {
    clearPeekTimers();
    const next = Boolean(open) && canPeek();
    if (next === peeking()) return;
    if (!next && historyPanel?.contains(document.activeElement)) S.historyToggle?.focus({ preventScroll: true });
    S.root.classList.toggle("history-peek", next);
    S.root.classList.toggle("history-peek-leave", !next && canPeek());
    if (next || !historyPanel) return;
    Promise.allSettled(historyPanel.getAnimations().map((animation) => animation.finished)).then(() => {
      if (!peeking()) S.root.classList.remove("history-peek-leave");
    });
  }

    // A row menu, dialog, resize drag or typing in the list keeps it open.
  function schedulePeekClose() {
    clearTimeout(peekCloseTimer);
    peekCloseTimer = setTimeout(() => {
      if (peekHover) return;
      const active = document.activeElement;
      const held = S.pop || document.querySelector("dialog[open]") || document.body.classList.contains("is-resizing-history") ||
        (historyPanel?.contains(active) && active.matches("input, select, textarea"));
      if (held) schedulePeekClose();
      else setHistoryPeek(false);
    }, PEEK_CLOSE_MS);
  }

    // Real travel only: the zone appearing under a resting pointer (page load,
  // a collapse) fires pointerenter but no movement. A held button is a drag,
  // such as a text selection run to the edge.
  historyEdge?.addEventListener("pointermove", (event) => {
    if (event.pointerType !== "mouse" || event.buttons || !(event.movementX || event.movementY)) return;
    if (!canPeek() || peeking() || peekOpenTimer) return;
    peekHover = false;
    peekOpenTimer = setTimeout(() => setHistoryPeek(true), PEEK_OPEN_MS);
  });

    historyEdge?.addEventListener("pointerleave", () => {
    if (!peeking()) clearPeekTimers();
    else if (!peekHover) schedulePeekClose();
  });

    historyPanel?.addEventListener("pointerenter", (event) => {
    if (event.pointerType !== "mouse") return;
    peekHover = true;
    clearTimeout(peekCloseTimer);
    if (S.root.classList.contains("history-peek-leave")) setHistoryPeek(true);
  });

    historyPanel?.addEventListener("pointerleave", () => {
    peekHover = false;
    if (peeking()) schedulePeekClose();
  });

    document.addEventListener("pointerdown", (event) => {
    if (peeking() && !historyPanel.contains(event.target) && !event.target.closest?.(".chat-pop, dialog")) setHistoryPeek(false);
  }, true);

    function syncHistory() {
    const drawer = S.historyMobile.matches && S.root.classList.contains("is-history");
    const expanded = S.historyMobile.matches ? drawer : !S.root.classList.contains("history-collapsed");
    const focusInHistory = historyPanel?.contains(document.activeElement);
    // toggle(…, false) writes only when the class is there; remove() always
    // rewrites the attribute and would re-run this observer forever.
    if (!canPeek()) {
      clearPeekTimers();
      S.root.classList.toggle("history-peek", false);
      S.root.classList.toggle("history-peek-leave", false);
    }
    const peek = S.root.classList.contains("history-peek") || S.root.classList.contains("history-peek-leave");
    S.historyToggles.forEach((btn) => btn.setAttribute("aria-expanded", String(expanded)));
    if (historyBackdrop) historyBackdrop.hidden = !drawer;
    historyBackground.forEach((element) => { element.inert = drawer; });
    if (historyPanel) historyPanel.inert = !expanded && !peek;
    if (historyWasOpen && !drawer) {
      overlays.closePop();
      if (focusInHistory && !document.body.classList.contains("is-nav")) {
        S.historyToggle?.focus({ preventScroll: true });
      }
    }
    historyWasOpen = drawer;
    composer.scheduleFitChatBar();
  }

    // On mobile the chat list is the page's base screen ("home"): opened that way
  // it adds no history entry, so back from the list leaves the app instead of
  // bouncing into the chat underneath.
  function setHistoryOpen(open, { home = false } = {}) {
    const next = Boolean(open);
    const was = S.root.classList.contains("is-history");
    S.root.classList.toggle("is-history", next);
    // The home screen appears in place; only an explicit open slides in.
    if (next && !was) S.root.classList.toggle("history-home", home);
    if (!next) S.root.classList.remove("history-home");
    if (next && !was) {
      document.dispatchEvent(new Event("fuse:history-open"));
      overlays.closePop();
      if (S.memoryOpen) panels.setMemoryOpen(false);
      if (S.skillsOpen && overlays.overlayIsMobile()) panels.setSkillsOpen(false);
      if (S.artifactOpen && overlays.overlayIsMobile()) artifacts.setArtifactOpen(false);
      if (overlays.overlayIsMobile() && !home) overlays.overlayPush("history");
    } else if (!next && was) {
      overlays.armOverlayGhost(historyCollapse);
      overlays.overlayDrop("history");
    }
    syncHistory();
    if (next && !home && overlays.overlayIsMobile()) historyCollapse?.focus({ preventScroll: true });
  }

    function saveHistoryCollapsed() {
    const prefs = historyCollapsedPrefs();
    prefs["console"] = S.root.classList.contains("history-collapsed");
    try { localStorage.setItem(STORE_HISTORY_COLLAPSED, JSON.stringify(prefs)); } catch (_) {}
  }

    function clampHistoryWidth(px) {
    const deskMax = Math.min(S.HISTORY_WIDTH.max, Math.max(S.HISTORY_WIDTH.min, window.innerWidth - 340));
    return Math.round(Math.min(deskMax, Math.max(S.HISTORY_WIDTH.min, px)));
  }

    function applyHistoryWidth(px, persist) {
    S.root.style.setProperty("--chat-history", px + "px");
    S.root.querySelector("[data-history-resize]")?.setAttribute("aria-valuenow", String(px));
    if (persist) {
      try { localStorage.setItem(S.STORE_HISTORY_WIDTH, String(px)); } catch (_) {}
    }
  }

    function currentHistoryWidth() {
    const raw = Number.parseFloat(S.root.style.getPropertyValue("--chat-history"));
    return Number.isFinite(raw) ? raw : S.HISTORY_WIDTH.default;
  }

  return {
    applyHistoryWidth,
    clampHistoryWidth,
    currentHistoryWidth,
    historyBackdrop,
    historyPanel,
    saveHistoryCollapsed,
    setHistoryOpen,
    setHistoryPeek,
    syncHistory,
    historyCollapsedPrefs,
  };
}

export { historyCollapsedPrefs };
