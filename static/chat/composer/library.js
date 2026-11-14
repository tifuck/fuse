// Prompt library: category tiles on an empty chat, a bottom sheet listing a
// category's prompts with a preview, and insertion into the composer with its
// fields as tab stops. The user always sends; a blank left unfilled gets one
// warning first.
import { S, draftKey } from '../state.js';
import { escapeHtml } from '../core.js';
import { attachSheetSwipe, modalInert, returnFocus } from '../overlays.js';
import { CATALOG } from './prompt-catalog.js';
import { composePrompt, fillFields, firstBlank, hasBlank, listFields, shiftFields } from './library-core.js';

const ICONS = {
  wrench: '<path d="M10.2 2.4a3.4 3.4 0 0 0-3.9 4.5L2.4 10.8a1.3 1.3 0 0 0 1.8 1.8l3.9-3.9a3.4 3.4 0 0 0 4.5-3.9l-2 2-1.7-.4-.4-1.7z" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"/>',
  review: '<path d="M2.5 3.5h11M2.5 7h7M2.5 10.5h5" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/><path d="m10 11 1.6 1.6L14 10" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"/>',
  bug: '<rect x="4.5" y="5" width="7" height="8" rx="3.5" fill="none" stroke="currentColor" stroke-width="1.3"/><path d="M8 5v8M2.5 8h2M11.5 8h2M3 4l2 1.5M13 4l-2 1.5M3 12.5l2-1.3M13 12.5l-2-1.3" stroke="currentColor" stroke-width="1.2" stroke-linecap="round"/>',
  shield: '<path d="M8 1.8l4.6 1.7v3.9c0 2.9-1.9 5.4-4.6 6.4-2.7-1-4.6-3.5-4.6-6.4V3.5z" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"/>',
  gauge: '<path d="M2.5 11a5.5 5.5 0 1 1 11 0" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/><path d="M8 11l3-3.5" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/>',
  layout: '<rect x="2.2" y="2.5" width="11.6" height="11" rx="1.4" fill="none" stroke="currentColor" stroke-width="1.3"/><path d="M2.2 6h11.6M6.5 6v7.5" stroke="currentColor" stroke-width="1.3"/>',
  flask: '<path d="M6.2 2.5h3.6M6.8 2.5v4L3.3 12.6a.9.9 0 0 0 .8 1.4h7.8a.9.9 0 0 0 .8-1.4L9.2 6.5v-4" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"/>',
  rocket: '<path d="M9.5 2.5c2.3.2 3.8 1.7 4 4L9 11 5 7z M5 7 3 7.5 2 10l2.5-.5M9 11l-.5 2L6 14l.5-2.5" fill="none" stroke="currentColor" stroke-width="1.2" stroke-linejoin="round"/>',
  layers: '<path d="M8 2 14 5 8 8 2 5z M2 8l6 3 6-3M2 11l6 3 6-3" fill="none" stroke="currentColor" stroke-width="1.2" stroke-linejoin="round"/>',
  book: '<path d="M2.5 3.5c2-.8 3.8-.6 5.5.6 1.7-1.2 3.5-1.4 5.5-.6v9c-2-.8-3.8-.6-5.5.6-1.7-1.2-3.5-1.4-5.5-.6z M8 4.1v9" fill="none" stroke="currentColor" stroke-width="1.2" stroke-linejoin="round"/>',
  eye: '<path d="M1.5 8S4 3.5 8 3.5 14.5 8 14.5 8 12 12.5 8 12.5 1.5 8 1.5 8z" fill="none" stroke="currentColor" stroke-width="1.3"/><circle cx="8" cy="8" r="2" fill="none" stroke="currentColor" stroke-width="1.3"/>',
  back: '<path d="M10 3 5 8l5 5" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>',
  close: '<path d="M3.5 3.5l9 9M12.5 3.5l-9 9" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/>',
  search: '<circle cx="7" cy="7" r="4.4" fill="none" stroke="currentColor" stroke-width="1.5"/><path d="M10.4 10.4L13.8 13.8" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>',
};
const icon = (name, size = 16) => `<svg viewBox="0 0 16 16" width="${size}" height="${size}" aria-hidden="true">${ICONS[name] || ""}</svg>`;

const PROMPTS = CATALOG.categories.flatMap((cat) => cat.prompts.map((prompt) => ({ ...prompt, cat })));
const BY_ID = new Map(PROMPTS.map((p) => [p.id, p]));

export function createLibrary(ctx) {
  const { picker, sidebar, stash } = ctx;
  let host = null;
  let sheet = null;
  let backdrop = null;
  let opener = null;
  // In place of the tiles on an empty chat; a modal sheet everywhere else.
  let inline = false;
  let view = { cat: null, query: "", preview: null, mode: null, mods: [] };
  // Tab stops of the last inserted prompt, valid while the box holds it.
  let fields = [];
  let tracked = "";
  let trackedKey = null;
  let warned = false;

  const codeTools = () => Boolean(S.config.coding) || Boolean(sidebar.sessionProject());
  const usable = (p) => p.needs !== "project" || codeTools();
  const modeTag = (p) => (p.needs_input ? "Needs input" : CATALOG.footers[p.modes[0]].short);

  function renderTiles() {
    if (!host) return;
    host.innerHTML =
      `<div class="chat-library-tiles" role="group" aria-label="Prompt library">${CATALOG.categories.map((c) =>
        `<button type="button" class="chat-library-tile" data-library-cat="${c.id}" aria-haspopup="dialog">${icon(c.icon, 18)}<span class="chat-library-tile-name">${escapeHtml(c.name)}</span><span class="chat-library-tile-count">${c.prompts.length} prompts</span></button>`).join("")}</div>`;
    host.hidden = false;
  }

  function rowHtml(p) {
    const ok = usable(p);
    return `<div class="chat-library-item" role="listitem">` +
      `<button type="button" class="chat-library-row" data-library-insert="${p.id}"${ok ? "" : ' aria-disabled="true"'}>` +
      `<span class="chat-library-row-title">${escapeHtml(p.title)}</span>` +
      `<span class="chat-library-row-blurb">${escapeHtml(ok ? p.blurb : "Needs a project chat. " + p.blurb)}</span></button>` +
      `<span class="chat-library-tag${p.modes[0] === "act" ? " is-act" : ""}">${escapeHtml(modeTag(p))}</span>` +
      `<button type="button" class="chat-library-peek" data-library-preview="${p.id}" aria-label="Preview ${escapeHtml(p.title)}">${icon("eye", 15)}</button></div>`;
  }

  function listHtml() {
    const q = view.query.trim().toLowerCase();
    const items = q
      ? PROMPTS.filter((p) => (p.title + " " + p.blurb + " " + p.tags.join(" ") + " " + p.cat.name).toLowerCase().includes(q))
      : CATALOG.categories.find((c) => c.id === view.cat).prompts.map((p) => BY_ID.get(p.id));
    if (!items.length) return `<p class="chat-library-empty">No prompt matches "${escapeHtml(view.query.trim())}".</p>`;
    return items.map(rowHtml).join("");
  }

  function previewHtml() {
    const p = BY_ID.get(view.preview);
    const mode = view.mode || p.modes[0];
    const mods = CATALOG.modifiers.filter((m) => m.modes.includes(mode));
    const fieldsList = listFields(p.body);
    const raw = composePrompt(CATALOG, p, mode, view.mods);
    const body = escapeHtml(raw.slice(0, p.body.length)).replace(/\{\{([^{}|]+)(?:\|([^{}]*))?\}\}/g,
      (m, label, def) => `<mark>${def == null ? "{{" + label + "}}" : def}</mark>`);
    return `<p class="chat-library-preview-blurb">${escapeHtml(p.blurb)}</p>` +
      (p.modes.length > 1 ? `<div class="chat-library-modes" role="group" aria-label="Mode">${p.modes.map((m) =>
        `<button type="button" data-library-mode="${m}" aria-pressed="${m === mode}">${escapeHtml(CATALOG.footers[m].label)}</button>`).join("")}</div>` : "") +
      (mods.length ? `<div class="chat-library-mods" role="group" aria-label="Add to the prompt">${mods.map((m) =>
        `<button type="button" data-library-mod="${m.id}" aria-pressed="${view.mods.includes(m.id)}">${escapeHtml(m.label)}</button>`).join("")}</div>` : "") +
      (fieldsList.length ? `<dl class="chat-library-fields">${fieldsList.map((f) =>
        `<div><dt>${escapeHtml(f.label)}</dt><dd${f.value == null ? ' class="is-blank"' : ""}>${escapeHtml(f.value == null ? "you fill this in" : f.value)}</dd></div>`).join("")}</dl>` : "") +
      `<pre class="chat-library-text">${body}<span class="chat-library-rules">${escapeHtml(raw.slice(p.body.length))}</span></pre>`;
  }

  function build() {
    backdrop = document.createElement("div");
    backdrop.className = "chat-library-backdrop";
    backdrop.hidden = true;
    sheet = document.createElement("div");
    sheet.className = "chat-library-sheet";
    sheet.setAttribute("role", "dialog");
    sheet.setAttribute("aria-modal", "true");
    sheet.setAttribute("aria-labelledby", "chat-library-title");
    sheet.tabIndex = -1;
    sheet.hidden = true;
    sheet.innerHTML =
      '<div class="chat-library-head"><span class="chat-library-grab" aria-hidden="true"></span>' +
      `<button type="button" class="chat-library-iconbtn" data-library-back aria-label="Back to the list" hidden>${icon("back", 14)}</button>` +
      '<span class="chat-library-head-icon" data-library-head-icon aria-hidden="true"></span>' +
      '<h2 id="chat-library-title" data-library-title></h2>' +
      '<span class="chat-library-head-meta" data-library-head-meta></span>' +
      `<button type="button" class="chat-library-iconbtn" data-library-close aria-label="Close">${icon("close", 14)}</button></div>` +
      `<label class="chat-library-search" data-library-searchrow>${icon("search", 14)}<input type="search" data-library-search placeholder="Search all ${PROMPTS.length} prompts" aria-label="Search prompts" autocomplete="off" enterkeyhint="search"></label>` +
      '<div class="chat-library-list" data-library-list role="list"></div>' +
      '<div class="chat-library-preview" data-library-preview-pane hidden></div>' +
      '<div class="chat-library-foot" data-library-foot hidden><button type="button" class="chat-library-insert" data-library-insert-preview>Insert into the message box</button></div>';
    S.root.append(backdrop, sheet);
    attachSheetSwipe(sheet, ".chat-library-head", () => S.overlayMobile.matches && !inline, close);
    backdrop.addEventListener("click", close);
    sheet.addEventListener("click", onSheetClick);
    sheet.addEventListener("keydown", onSheetKey);
    const search = sheet.querySelector("[data-library-search]");
    search.addEventListener("input", () => {
      view.query = search.value;
      paintList();
    });
  }

  function place() {
    if (!sheet || sheet.hidden || inline) return;
    if (S.overlayMobile.matches) {
      sheet.style.removeProperty("--library-left");
      sheet.style.removeProperty("--library-width");
      sheet.style.removeProperty("--library-bottom");
      return;
    }
    // Over the tiles when they show (same left edge), else over the composer.
    const box = (S.root.querySelector(".chat-box") || S.form).getBoundingClientRect();
    const grid = host?.querySelector(".chat-library-tiles");
    const tiles = grid && grid.offsetParent ? grid.getBoundingClientRect() : box;
    sheet.style.setProperty("--library-left", tiles.left + "px");
    sheet.style.setProperty("--library-width", Math.min(tiles.width, 48 * 16) + "px");
    sheet.style.setProperty("--library-bottom", Math.max(8, window.innerHeight - box.top + 8) + "px");
  }

  function paintHead() {
    const cat = view.preview ? BY_ID.get(view.preview).cat : CATALOG.categories.find((c) => c.id === view.cat);
    const searching = !view.preview && view.query.trim();
    sheet.querySelector("[data-library-title]").textContent = view.preview ? BY_ID.get(view.preview).title : searching ? "Search" : cat.name;
    sheet.querySelector("[data-library-head-icon]").innerHTML = view.preview || searching ? "" : icon(cat.icon, 16);
    sheet.querySelector("[data-library-head-meta]").textContent = view.preview || searching ? "" : `${cat.prompts.length} prompts`;
    const back = sheet.querySelector("[data-library-back]");
    back.hidden = !view.preview && !inline;
    back.setAttribute("aria-label", view.preview ? "Back to the list" : "Back to categories");
  }

  function paintList() {
    sheet.querySelector("[data-library-list]").innerHTML = listHtml();
    paintHead();
  }

  function paint() {
    const previewing = Boolean(view.preview);
    sheet.classList.toggle("is-preview", previewing);
    sheet.querySelector("[data-library-searchrow]").hidden = previewing;
    sheet.querySelector("[data-library-list]").hidden = previewing;
    sheet.querySelector("[data-library-foot]").hidden = !previewing;
    const pane = sheet.querySelector("[data-library-preview-pane]");
    pane.hidden = !previewing;
    if (previewing) {
      pane.innerHTML = previewHtml();
      pane.scrollTop = 0;
      const p = BY_ID.get(view.preview);
      const insertBtn = sheet.querySelector("[data-library-insert-preview]");
      insertBtn.disabled = !usable(p);
      insertBtn.textContent = usable(p) ? "Insert into the message box" : "Needs a project chat";
      paintHead();
    } else paintList();
  }

  function open(catId, from) {
    if (!sheet) build();
    opener = from || document.activeElement;
    const query = typeof catId === "string" && !CATALOG.categories.some((c) => c.id === catId) ? catId : "";
    view = { cat: query || !catId ? CATALOG.categories[0].id : catId, query, preview: null, mode: null, mods: [] };
    const search = sheet.querySelector("[data-library-search]");
    search.value = query;
    inline = Boolean(host) && S.root.classList.contains("is-empty");
    sheet.classList.toggle("is-inline", inline);
    sheet.setAttribute("aria-modal", String(!inline));
    host?.classList.toggle("is-open", inline);
    if (inline) host.append(sheet);
    else S.root.append(sheet);
    backdrop.hidden = inline;
    sheet.hidden = false;
    paint();
    place();
    if (!inline) modalInert(sheet, true);
    if (S.overlayMobile.matches && window.FuseBack) FuseBack.open("chat:library", () => close(true));
    // Focusing the search on a phone would raise the keyboard over the list.
    if (S.overlayMobile.matches) sheet.focus({ preventScroll: true });
    else search.focus({ preventScroll: true });
  }

  function close(fromBack) {
    if (!sheet || sheet.hidden) return;
    sheet.hidden = true;
    backdrop.hidden = true;
    host?.classList.remove("is-open");
    if (!inline) modalInert(sheet, false);
    if (fromBack !== true && window.FuseBack) FuseBack.drop("chat:library");
    returnFocus(sheet, opener);
  }

  function showPreview(id) {
    view.preview = id;
    view.mode = null;
    view.mods = [];
    paint();
    sheet.querySelector("[data-library-back]").focus({ preventScroll: true });
  }

  function backToList() {
    const id = view.preview;
    view.preview = null;
    paint();
    sheet.querySelector(`[data-library-preview="${CSS.escape(id)}"]`)?.focus({ preventScroll: true });
  }

  function onSheetClick(event) {
    const t = event.target;
    if (t.closest("[data-library-close]")) return close();
    if (t.closest("[data-library-back]")) return view.preview ? backToList() : close();
    const peek = t.closest("[data-library-preview]");
    if (peek) return showPreview(peek.getAttribute("data-library-preview"));
    const modeBtn = t.closest("[data-library-mode]");
    if (modeBtn) {
      view.mode = modeBtn.getAttribute("data-library-mode");
      paint();
      sheet.querySelector(`[data-library-mode="${view.mode}"]`)?.focus({ preventScroll: true });
      return;
    }
    const modBtn = t.closest("[data-library-mod]");
    if (modBtn) {
      const id = modBtn.getAttribute("data-library-mod");
      view.mods = view.mods.includes(id) ? view.mods.filter((m) => m !== id) : [...view.mods, id];
      paint();
      sheet.querySelector(`[data-library-mod="${id}"]`)?.focus({ preventScroll: true });
      return;
    }
    if (t.closest("[data-library-insert-preview]")) return insert(view.preview, view.mode, view.mods);
    const row = t.closest("[data-library-insert]");
    if (row) insert(row.getAttribute("data-library-insert"));
  }

  function onSheetKey(event) {
    if (event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation();
      if (view.preview) backToList();
      else close();
      return;
    }
    if ((event.key === "ArrowDown" || event.key === "ArrowUp") && !view.preview) {
      const rows = [...sheet.querySelectorAll(".chat-library-row")];
      if (!rows.length) return;
      event.preventDefault();
      const i = rows.indexOf(document.activeElement);
      const next = event.key === "ArrowDown" ? (i < 0 ? 0 : Math.min(rows.length - 1, i + 1)) : i <= 0 ? -1 : i - 1;
      if (next < 0) sheet.querySelector("[data-library-search]").focus();
      else rows[next].focus();
    }
  }

  async function insert(id, mode, mods = []) {
    const p = BY_ID.get(id);
    if (!p) return;
    if (!usable(p)) {
      picker.setHint(`"${p.title}" reads a codebase. Start a project chat to use it.`, "warn");
      return;
    }
    if (S.input.value.trim()) {
      await stash();
      if (S.input.value.trim()) return;
    }
    close();
    const filled = fillFields(composePrompt(CATALOG, p, mode || p.modes[0], mods));
    S.input.value = filled.text;
    // The composer's own input handler saves the draft, resizes and syncs.
    S.input.dispatchEvent(new Event("input", { bubbles: true }));
    fields = filled.fields;
    tracked = filled.text;
    trackedKey = draftKey();
    warned = false;
    const blank = fields.find((f) => f.required);
    const target = blank || fields[0];
    if (blank || !S.overlayMobile.matches) S.input.focus({ preventScroll: true });
    if (target) {
      S.input.setSelectionRange(target.start, target.end);
      S.input.scrollTop = 0;
    }
    const blanks = fields.filter((f) => f.required).length;
    picker.setHint(blanks
      ? `Fill ${blanks === 1 ? "the selected blank" : blanks + " blanks"}, then send. Tab moves between fields.`
      : fields.length ? "Ready to send. Tab selects the fields if you want to narrow them." : "Ready to send.");
  }

  function live() {
    if (!fields.length) return false;
    if (trackedKey !== draftKey() || S.input.value !== tracked) {
      fields = [];
      return false;
    }
    return true;
  }

  // Wires the DOM: called once by chat.js, after the composer exists.
  function mount() {
    host = S.root.querySelector("[data-chat-library]");
    S.input.addEventListener("input", () => {
      if (!fields.length) return;
      if (trackedKey !== draftKey() || !S.input.value) {
        fields = [];
        return;
      }
      fields = shiftFields(fields, tracked, S.input.value);
      tracked = S.input.value;
      warned = false;
    });

    // After chat.js's own handler, which takes Tab only for an open palette.
    S.input.addEventListener("keydown", (event) => {
      if (event.defaultPrevented || event.isComposing || (S.slashMenu && !S.slashMenu.hidden)) return;
      if (event.key === "Escape" && live()) {
        fields = [];
        return;
      }
      if (event.key !== "Tab" || event.altKey || event.ctrlKey || event.metaKey || !live()) return;
      event.preventDefault();
      const sorted = [...fields].sort((a, b) => a.start - b.start);
      const next = event.shiftKey
        ? [...sorted].reverse().find((f) => f.end < S.input.selectionStart) || sorted[sorted.length - 1]
        : sorted.find((f) => f.start >= S.input.selectionEnd) || sorted[0];
      S.input.setSelectionRange(next.start, next.end);
    });

    // Ahead of chat.js's Enter and submit handlers: one warning for a blank
    // still unfilled, then the next send goes through.
    function guard(event) {
      if (warned || !live() || !hasBlank(S.input.value)) return;
      warned = true;
      event.preventDefault();
      event.stopImmediatePropagation();
      const blank = fields.find((f) => f.required);
      if (blank) S.input.setSelectionRange(blank.start, blank.end);
      S.input.focus({ preventScroll: true });
      picker.setHint(`"${firstBlank(S.input.value)}" is still blank. Fill it in, or send again to send it as is.`, "warn");
    }
    S.input.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && !event.shiftKey && !event.altKey && !event.isComposing) guard(event);
    }, true);
    S.form?.addEventListener("submit", guard, true);

    host?.addEventListener("click", (event) => {
      const tile = event.target.closest("[data-library-cat]");
      if (tile) open(tile.getAttribute("data-library-cat"), tile);
    });

    renderTiles();
    window.addEventListener("resize", place);
  }

  return { openLibrary: open, closeLibrary: close, mountLibrary: mount };
}
