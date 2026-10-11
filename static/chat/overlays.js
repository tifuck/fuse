// Mobile sheets and overlay layer stack (push/drop/back).
import { S } from './state.js';

// Full-screen mobile sheets follow a downward drag on their head and close
// past a distance or flick; otherwise they spring back (CSS transition).
function attachSheetSwipe(sheet, headSelector, isSheet, close) {
  let start = null;
  const reset = () => {
    sheet.classList.remove("is-dragging");
    sheet.style.translate = "";
    sheet.style.opacity = "";
  };
  new MutationObserver(() => { if (!sheet.hidden) reset(); }).observe(sheet, { attributes: true, attributeFilter: ["hidden"] });
  sheet.addEventListener("pointerdown", (event) => {
    if (!isSheet() || event.button !== 0 || event.pointerType === "mouse") return;
    const head = event.target.closest(headSelector);
    if (!head || !sheet.contains(head) || event.target.closest("button, input, select, a")) return;
    start = { y: event.clientY, t: performance.now(), dy: 0, id: event.pointerId };
    try { head.setPointerCapture(event.pointerId); } catch (_) {}
  });
  sheet.addEventListener("pointermove", (event) => {
    if (!start || event.pointerId !== start.id) return;
    const dy = Math.max(0, event.clientY - start.y);
    if (!start.dy && dy < 4) return;
    start.dy = dy;
    sheet.classList.add("is-dragging");
    sheet.style.translate = `0 ${dy}px`;
    sheet.style.opacity = String(Math.max(0.5, 1 - dy / (sheet.clientHeight * 1.4)));
  });
  const end = (event) => {
    if (!start || event.pointerId !== start.id) return;
    const { dy, t } = start;
    start = null;
    const flick = dy / Math.max(1, performance.now() - t) > 0.6;
    sheet.classList.remove("is-dragging");
    if (dy > Math.min(140, sheet.clientHeight * 0.22) || (flick && dy > 24)) {
      // Keep the dragged offset; the hide transition carries it off-screen.
      sheet.style.translate = "0 100%";
      sheet.style.opacity = "0";
      close();
    } else {
      sheet.style.translate = "";
      sheet.style.opacity = "";
    }
  };
  sheet.addEventListener("pointerup", end);
  sheet.addEventListener("pointercancel", end);
}

function overlayDrop(kind) {
  FuseBack.drop("chat:" + kind);
}

// Layers that live inside the chat drawer. Opening a chat from the drawer
// must retire their history entries before the chat's own URL is written.
function dropDrawerLayers() {
  for (const kind of ["pop", "search", "history"]) overlayDrop(kind);
}

export function createOverlays(ctx) {
  const { agents, artifacts, context, panels, perms, picker, sidebar, sync, turns } = ctx;
    function overlayIsMobile() {
    return S.overlayMobile.matches;
  }

    function armOverlayGhost(el) {
    if (!overlayIsMobile()) {
      S.overlayGhostUntil = 0;
      S.overlayGhostRect = null;
      return;
    }
    S.overlayGhostUntil = performance.now() + 420;
    if (el && el.getBoundingClientRect) {
      const r = el.getBoundingClientRect();
      S.overlayGhostRect = { left: r.left - 8, right: r.right + 8, top: r.top - 8, bottom: r.bottom + 8 };
    } else {
      S.overlayGhostRect = null;
    }
  }

    // Mobile layers ride the site-wide back stack (FuseBack in app.js): back
  // closes the top one, and closing one from its own UI removes its entry.
  function overlayPush(kind) {
    if (!overlayIsMobile()) return;
    FuseBack.open("chat:" + kind, () => overlayCloseKind(kind, true));
  }

    function overlayCloseKind(kind, fromPop) {
    if (kind === "view") {
      if (fromPop) panels.closeViewMenuDom();
      else panels.setViewMenuOpen(false);
    } else if (kind === "memory") panels.setMemoryOpen(false);
    else if (kind === "skills") panels.setSkillsOpen(false);
    else if (kind === "perms") perms.setPermsOpen(false);
    else if (kind === "artifact") artifacts.setArtifactOpen(false);
    else if (kind === "picker") picker.closePicker();
    else if (kind === "history") sidebar.setHistoryOpen(false);
    else if (kind === "rewind") turns.closeRewind();
    else if (kind === "agent") agents.closeAgentView();
    else if (kind === "search") sidebar.setSearchOpen(false);
    else if (kind === "pop") closePop();
    else if (kind === "context") context.closeContextPop();
    else if (kind === "lightbox") panels.closeLightbox();
  }

    // Popovers shown and hidden from many call sites follow their [hidden]
  // attribute instead of wiring every one of them.
  function backLayerFollowsHidden(el, kind) {
    if (!el) return;
    const sync = () => {
      if (el.hidden) overlayDrop(kind);
      else overlayPush(kind);
    };
    new MutationObserver(sync).observe(el, { attributes: true, attributeFilter: ["hidden"] });
  }

    function closePop() {
    if (S.pop) {
      S.pop.remove();
      overlayDrop("pop");
    }
    S.pop = null;
    S.popRowKey = null;
    S.historyList && S.historyList.querySelectorAll(".is-menu").forEach((el) => el.classList.remove("is-menu"));
  }

    function showPop(anchor, items, at) {
    closePop();
    // The row whose menu is open keeps .is-menu while the pop is up. closePop()
    // just stripped every flag, so stamp the owner here and remember its key:
    // a repaint replaces the row nodes, and syncHistoryFooter re-stamps them.
    const menuRow = anchor && anchor.closest ? anchor.closest(".chat-history-item") : null;
    if (menuRow) {
      const attr = menuRow.hasAttribute("data-thread") ? "data-thread" : "data-project";
      S.popRowKey = [attr, menuRow.getAttribute(attr)];
      menuRow.classList.add("is-menu");
    }
    S.pop = document.createElement("div");
    S.pop.className = "chat-pop";
    items.forEach((item) => {
      if (item.sep) {
        const rule = document.createElement("div");
        rule.className = "chat-pop-sep";
        rule.setAttribute("aria-hidden", "true");
        S.pop.appendChild(rule);
        return;
      }
      const btn = document.createElement("button");
      btn.type = "button";
      btn.textContent = item.label;
      if (item.danger) btn.className = "danger";
      btn.addEventListener("click", (event) => {
        event.preventDefault();
        closePop();
        Promise.resolve(item.run()).catch((error) => sync.showFailure(`${item.label} failed`, error));
      });
      S.pop.appendChild(btn);
    });
    document.body.appendChild(S.pop);
    overlayPush("pop");
    const rect = anchor ? anchor.getBoundingClientRect() : null;
    const width = S.pop.offsetWidth;
    const height = S.pop.offsetHeight;
    let left = at ? at.x : rect.right - width;
    let top = at ? at.y : rect.bottom + 4;
    if (left < 8) left = 8;
    if (left + width > window.innerWidth - 8) left = window.innerWidth - width - 8;
    if (top + height > window.innerHeight - 8) top = Math.max(8, rect.top - height - 4);
    S.pop.style.left = left + "px";
    S.pop.style.top = top + "px";
  }

  return {
    armOverlayGhost,
    backLayerFollowsHidden,
    closePop,
    overlayIsMobile,
    overlayPush,
    showPop,
    attachSheetSwipe,
    dropDrawerLayers,
    overlayDrop,
  };
}

export { attachSheetSwipe, dropDrawerLayers, overlayDrop };
