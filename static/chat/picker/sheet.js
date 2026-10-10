// Picker button/sheet placement, composer hint.
import { S } from '../state.js';

const PICKER_CHEV =
  '<svg class="chat-picker-chev" viewBox="0 0 16 16" width="12" height="12" aria-hidden="true"><path d="M3.5 6l4.5 4.5L12.5 6" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>';

// A held toast leaves this soon at the earliest, so a fast action never
// flashes one in and out within a frame or two.
const HINT_MIN_HELD_MS = 500;

// Unheld toasts stay long enough to read: about 45ms per character on top of
// a base, never under 2.2s (4.5s for a fault) or over 8s.
function hintMs(text, fault) {
  return Math.min(8000, Math.max(fault ? 4500 : 2200, 1600 + String(text).length * 45));
}

export function createSheet(ctx) {
  const { composer, context, list, overlays, perms, sidebar } = ctx;
    // The one toast above the composer. A plain hint reports something that
  // already happened and times out; holdHint ties it to an action in flight.
    function setHint(text, fault, duration) {
    if (!S.hint) return;
    window.clearTimeout(S.hintTimer);
    window.clearTimeout(S.hintReleaseTimer);
    S.hintKey = "";
    if (S.hintFrame) {
      window.cancelAnimationFrame(S.hintFrame);
      S.hintFrame = 0;
    }
    S.hint.classList.toggle("is-fault", Boolean(fault));
    S.hint.classList.remove("is-pinned");
    const show = Boolean(text);
    if (!show && S.pinnedHint) {
      showPinned();
      return;
    }
    if (!show) {
      S.hint.classList.remove("is-on");
      S.hintTimer = window.setTimeout(() => {
        S.hint.hidden = true;
        S.hint.textContent = "";
      }, 180);
      return;
    }
    S.hint.hidden = false;
    S.hint.textContent = "";
    S.hint.appendChild(document.createTextNode(text));
    S.hintFrame = window.requestAnimationFrame(() => {
      S.hintFrame = 0;
      S.hint.classList.add("is-on");
    });
    if (duration === 0) return;
    const ms = duration != null ? duration : hintMs(text, fault);
    S.hintTimer = window.setTimeout(() => setHint(""), ms);
  }

    // A toast owned by `key` (e.g. "stop:<run id>"). It stays until
  // releaseHint(key) or until any other toast replaces it. Pass duration null
  // for a keyed toast that also times out normally.
    function holdHint(key, text, fault = false, duration = 0) {
    if (S.hintKey === key && S.hint?.textContent === text) return;
    setHint(text, fault, duration);
    S.hintKey = key;
    S.hintHeldAt = Date.now();
  }

    // Ends a held toast: hides it, or swaps in `text` as a normal timed hint.
  // A no-op once something else owns the toast.
    function releaseHint(key, text, fault) {
    if (!key || S.hintKey !== key) return;
    window.clearTimeout(S.hintReleaseTimer);
    const wait = HINT_MIN_HELD_MS - (Date.now() - S.hintHeldAt);
    if (wait > 0) {
      S.hintReleaseTimer = window.setTimeout(() => releaseHint(key, text, fault), wait);
      return;
    }
    setHint(text || "", fault);
  }

    function heldHint() {
    return S.hintKey || "";
  }

    // A toast that stays until the user closes it (or the chat changes). A
  // passing hint may cover it; it comes back when that hint ends.
    function pinHint(key, text) {
    S.pinnedHint = { key, text };
    showPinned();
  }

    function showPinned() {
    const pin = S.pinnedHint;
    setHint(pin.text, false, 0);
    S.hintKey = pin.key;
    S.hint.classList.add("is-pinned");
    const close = document.createElement("button");
    close.type = "button";
    close.className = "chat-toast-close";
    close.setAttribute("aria-label", "Dismiss");
    close.textContent = "\u00d7";
    close.addEventListener("click", () => unpinHint(pin.key));
    S.hint.appendChild(close);
  }

    function unpinHint(key) {
    if (!S.pinnedHint || (key && S.pinnedHint.key !== key)) return;
    const shown = S.hintKey === S.pinnedHint.key;
    S.pinnedHint = null;
    if (shown) setHint("");
  }

    function defaultHint() {
    S.pinnedHint = null;
    if (!S.allModels.length) {
      setHint("No models are unlocked yet. ", false, 0);
      const link = document.createElement("a");
      link.href = "/sources";
      link.textContent = "Open Sources";
      S.hint.appendChild(link);
      return;
    }
    setHint("");
  }

    function closePicker() {
    const wasOpen = S.pickerOpen;
    S.pickerOpen = false;
    if (S.pickerMenuEl) S.pickerMenuEl.hidden = true;
    const btn = S.pickerHost.querySelector(".chat-picker-btn");
    if (btn) btn.setAttribute("aria-expanded", "false");
    if (wasOpen) {
      overlays.armOverlayGhost(S.pickerMenuEl?.querySelector("[data-picker-close]"));
      overlays.overlayDrop("picker");
    }
  }

    function openPicker() {
    if (!S.allModels.length) return;
    perms.setPermsOpen(false);
    S.pickerOpen = true;
    overlays.overlayPush("picker");
    S.pickerFilter = "";
    S.pickerPinId = S.modelId;
    const items = list.filteredModels();
    S.pickerIndex = Math.max(
      0,
      items.findIndex((model) => model.id === S.modelId)
    );
    const btn = S.pickerHost.querySelector(".chat-picker-btn");
    const search = S.pickerMenuEl && S.pickerMenuEl.querySelector(".chat-picker-search");
    if (S.pickerMenuEl) S.pickerMenuEl.hidden = false;
    if (btn) btn.setAttribute("aria-expanded", "true");
    // Focus would pop the touch keyboard on mobile; the sheet stays keyboard-free until tapped.
    if (search) {
      search.value = "";
      if (!S.pickerSheetQuery.matches) search.focus();
    }
    list.renderPickerList();
    placePicker();
  }

    function placePicker() {
    composer.placeComposerPop(S.pickerMenuEl, S.pickerHost.querySelector(".chat-picker-btn"), 26 * 16, true);
  }

    function renderPickerButton() {
    const model = context.currentModel();
    S.pickerHost.innerHTML = "";
    // On mobile the sheet lives on the chat root, outside pickerHost; drop it
    // too or every re-render leaves an orphaned sheet behind.
    S.pickerMenuEl?.remove();
    S.pickerMenuEl = null;
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "chat-picker-btn";
    btn.setAttribute("aria-haspopup", "dialog");
    btn.setAttribute("aria-expanded", S.pickerOpen ? "true" : "false");
    if (!model) {
      btn.disabled = true;
      btn.innerHTML = "<span>No models</span>";
      S.pickerHost.appendChild(btn);
      return;
    }
    const effort = list.currentEffort();
    const think = list.effortLabel(effort);
    btn.innerHTML = `${sidebar.providerLogo(model.type)}<span></span><small></small>${PICKER_CHEV}`;
    btn.querySelector("span").textContent = model.name;
    btn.querySelector("small").textContent = think;
    btn.title = model.id + " · " + think;
    btn.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
      if (S.pickerOpen) closePicker();
      else openPicker();
    });
    S.pickerHost.appendChild(btn);
    S.pickerMenuEl = renderPickerSheet();
    if (S.pickerSheetQuery.matches) {
      // A fixed sheet cannot live here: transformed/backdrop-filtered composer
      // ancestors (glass dock) would turn it into a contained, clipped box.
      S.root.appendChild(S.pickerMenuEl);
    } else {
      S.pickerHost.appendChild(S.pickerMenuEl);
    }
    S.pickerMenuEl.hidden = !S.pickerOpen;
    if (S.pickerOpen) {
      list.renderPickerList();
      placePicker();
    }
  }

    function renderPickerSheet() {
    const menu = document.createElement("div");
    menu.className = "chat-picker-menu" + (S.pickerSheetQuery.matches ? " is-sheet" : "");
    menu.setAttribute("role", "dialog");
    menu.setAttribute("aria-label", "Model picker");
    menu.innerHTML =
      '<div class="chat-picker-head"><p>Model</p><button type="button" class="chat-menu-close chat-picker-done" data-picker-close aria-label="Close"><svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><path d="M3.5 3.5l9 9M12.5 3.5l-9 9" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg></button></div>' +
      '<div class="chat-picker-searchrow"><svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><circle cx="7" cy="7" r="4.4" fill="none" stroke="currentColor" stroke-width="1.5"/><path d="M10.4 10.4L13.8 13.8" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg><input type="search" class="chat-picker-search" placeholder="Search models" aria-label="Search models"><kbd class="chat-picker-kbd" aria-hidden="true">esc</kbd></div>' +
      '<div class="chat-picker-list" role="listbox" aria-label="Models"></div>' +
      '<div class="chat-picker-thinking-host"></div>';
    overlays.attachSheetSwipe(menu, ".chat-picker-head", () => menu.classList.contains("is-sheet"), closePicker);
    const search = menu.querySelector(".chat-picker-search");
    search.addEventListener("input", () => {
      S.pickerFilter = search.value;
      S.pickerIndex = 0;
      list.renderPickerList();
    });
    search.addEventListener("keydown", (event) => {
      const items = list.filteredModels();
      if (event.key === "ArrowDown") {
        event.preventDefault();
        S.pickerIndex = Math.min(items.length - 1, S.pickerIndex + 1);
        list.renderPickerList();
      } else if (event.key === "ArrowUp") {
        event.preventDefault();
        S.pickerIndex = Math.max(0, S.pickerIndex - 1);
        list.renderPickerList();
      } else if (event.key === "Enter") {
        event.preventDefault();
        if (items[S.pickerIndex]) list.selectModel(items[S.pickerIndex].id, true);
      } else if (event.key === "Escape") {
        event.preventDefault();
        closePicker();
        const btn = S.pickerHost.querySelector(".chat-picker-btn");
        if (btn) btn.focus();
      }
    });
    menu.addEventListener("click", (event) => {
      event.stopPropagation();
      const closeBtn = event.target.closest("[data-picker-close]");
      if (closeBtn) {
        event.preventDefault();
        closeBtn.blur();
        closePicker();
        const btn = S.pickerHost.querySelector(".chat-picker-btn");
        if (btn && !S.pickerSheetQuery.matches) btn.focus();
        return;
      }
      const target = event.target.closest("button[data-id]");
      if (target) {
        const id = target.getAttribute("data-id");
        list.selectModel(id, id === S.modelId);
      }
    });
    menu.addEventListener("input", (event) => {
      const slider = event.target.closest("[data-effort-slider]");
      if (!slider) return;
      const effort = list.modelEfforts()[Number(slider.value)];
      if (effort) list.setEffort(effort);
    });
    return menu;
  }

    // A gesture over a nested scroller (code pane, thinking peek, table) that
  // can still move that way scrolls it, not the transcript, and must not
  // break the follow.
  function innerScrolls(target, dy) {
    for (let el = target instanceof Element ? target : null; el && el !== S.thread; el = el.parentElement) {
      if (el.scrollHeight <= el.clientHeight + 1) continue;
      const oy = getComputedStyle(el).overflowY;
      if (oy !== "auto" && oy !== "scroll") continue;
      if (dy < 0 ? el.scrollTop > 0 : el.scrollTop + el.clientHeight < el.scrollHeight - 1) return true;
    }
    return false;
  }

  return {
    closePicker,
    defaultHint,
    heldHint,
    holdHint,
    innerScrolls,
    pinHint,
    placePicker,
    releaseHint,
    renderPickerButton,
    setHint,
    unpinHint,
  };
}
