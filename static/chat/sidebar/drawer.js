// History drawer state, collapse, resize.
import { S } from '../state.js';

let historyWasOpen = false;

const STORE_HISTORY_COLLAPSED = "fuse.chat.historyCollapsed.v1";

function historyCollapsedPrefs() {
  try { return JSON.parse(localStorage.getItem(STORE_HISTORY_COLLAPSED) || "{}") || {}; } catch (_) { return {}; }
}

export function createDrawer(ctx) {
  const { agents, artifacts, composer, overlays, panels } = ctx;
    const historyPanel = S.root.querySelector("[data-history]");

    const historyCollapse = historyPanel?.querySelector("[data-history-toggle]");

    const historyBackdrop = S.root.querySelector("[data-history-backdrop]");

    const historyBackground = S.root.querySelectorAll(".chat-thread, .chat-dock, .chat-latest, .chat-bar > :not([data-history-toggle])");

    function syncHistory() {
    const drawer = S.historyMobile.matches && S.root.classList.contains("is-history");
    const expanded = S.historyMobile.matches ? drawer : !S.root.classList.contains("history-collapsed");
    const focusInHistory = historyPanel?.contains(document.activeElement);
    S.historyToggles.forEach((btn) => btn.setAttribute("aria-expanded", String(expanded)));
    if (historyBackdrop) historyBackdrop.hidden = !drawer;
    historyBackground.forEach((element) => { element.inert = drawer; });
    if (historyPanel) historyPanel.inert = !expanded;
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
    syncHistory,
    historyCollapsedPrefs,
  };
}

export { historyCollapsedPrefs };
