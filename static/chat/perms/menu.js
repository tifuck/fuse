// Grants menu: hosts, modes, revoke, refresh.
import { S } from '../state.js';
import { escapeHtml } from '../core.js';

let permsMenuEl = null;

let threadPermData = null;

let permsFilter = "";

const PERMS_ICON_X = '<svg viewBox="0 0 16 16" width="12" height="12" aria-hidden="true"><path d="M4 4l8 8M12 4l-8 8" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>';

const PERMS_ICON_LOCK = '<svg viewBox="0 0 16 16" width="12" height="12" aria-hidden="true"><rect x="3.5" y="7" width="9" height="6.5" rx="1.5" fill="none" stroke="currentColor" stroke-width="1.3"/><path d="M5.5 7V5.3a2.5 2.5 0 0 1 5 0V7" fill="none" stroke="currentColor" stroke-width="1.3"/></svg>';

const PERMS_ICON_DIR = '<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><path d="M2 4.5c0-.6.4-1 1-1h3.2l1.4 1.5H13c.6 0 1 .4 1 1v6c0 .6-.4 1-1 1H3c-.6 0-1-.4-1-1z" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"/></svg>';

const PERMS_ICON_FILE = '<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><path d="M4 2h5l3 3v9H4z" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"/><path d="M9 2v3h3" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"/></svg>';

const PERMS_MODES = [
  { id: "ask", label: "Ask", hint: "Calls outside these directories wait for your answer." },
  { id: "auto", label: "Auto", hint: "Jev reviews each call and allows routine ones. Dangerous commands and secrets still ask." },
  { id: "bypass", label: "Bypass", hint: "No prompts. Read-only projects still apply." },
];

const PERMS_TAGS = { temp: "temp", work: "workspace", session: "this chat", host: "always" };

function permHostList() {
  return Array.isArray(threadPermData && threadPermData.hosts) ? threadPermData.hosts : [];
}

// Middle-truncate paths: the leading directories give way first so the
// last segment (the part that tells two grants apart) stays readable.
function permPathHtml(path) {
  const trimmed = path.replace(/[\\/]+$/, "");
  const cut = Math.max(trimmed.lastIndexOf("/"), trimmed.lastIndexOf("\\"));
  const head = cut > 0 ? path.slice(0, cut + 1) : "";
  const tail = cut > 0 ? path.slice(cut + 1) : path;
  return (head ? `<span class="chat-perms-path-head">${escapeHtml(head)}</span>` : "") +
    `<span class="chat-perms-path-tail">${escapeHtml(tail)}</span>`;
}

function permRowHtml(row) {
  const action = row.grant
    ? `<button type="button" class="chat-perms-x" data-perm-revoke aria-label="Remove access to ${escapeHtml(row.path)}" title="Remove access">${PERMS_ICON_X}</button>`
    : `<span class="chat-perms-x is-fixed" role="img" aria-label="Built in" title="Built in: always allowed">${PERMS_ICON_LOCK}</span>`;
  return `<li class="chat-perms-row${row.grant ? "" : " is-fixed"}"${row.grant ? ` data-perm-grant="${Number(row.grant)}"` : ""}>` +
    `<span class="chat-perms-icon">${row.kind === "file" ? PERMS_ICON_FILE : PERMS_ICON_DIR}</span>` +
    `<span class="chat-perms-path" title="${escapeHtml(row.path)}">${permPathHtml(row.path)}</span>` +
    `<span class="chat-perms-tag is-${row.tag}">${PERMS_TAGS[row.tag]}</span>${action}</li>`;
}

function paintPermsList() {
  const listEl = permsMenuEl && permsMenuEl.querySelector("[data-perms-list]");
  if (!listEl) return;
  if (!threadPermData) {
    listEl.innerHTML = '<p class="chat-perms-empty">Loading…</p>';
    return;
  }
  const q = permsFilter.trim().toLowerCase();
  const threadHost = Number(threadPermData.thread_host_id);
  const hosts = [...permHostList()].sort((a, b) => (Number(b.id) === threadHost) - (Number(a.id) === threadHost));
  const groups = hosts.map((h) => {
    const name = String(h.host || "host");
    const rows = [];
    (h.roots || []).forEach((r) => {
      if (r && r.path) rows.push({ path: String(r.path), kind: "dir", tag: r.kind === "work" ? "work" : "temp" });
    });
    (h.grants || []).forEach((g) => {
      rows.push({ path: String(g.value || ""), kind: g.kind === "file" ? "file" : "dir", tag: g.scope === "host" ? "host" : "session", grant: g.id });
    });
    const shown = q && !name.toLowerCase().includes(q) ? rows.filter((r) => r.path.toLowerCase().includes(q)) : rows;
    if (!shown.length) return "";
    const granted = (h.grants || []).length;
    return `<section class="chat-perms-group"><p><span class="chat-perms-host">${escapeHtml(name)}</span>` +
      (Number(h.id) === threadHost ? '<span class="chat-perms-here">this chat</span>' : "") +
      `<span class="chat-perms-count">${granted ? `${granted} granted` : ""}</span></p>` +
      `<ul aria-label="${escapeHtml(name)}">${shown.map(permRowHtml).join("")}</ul></section>`;
  }).join("");
  listEl.innerHTML = groups || `<p class="chat-perms-empty">${q ? "Nothing matches." : "No machines yet."}</p>`;
}

export function createMenu(ctx) {
  const { composer, net, overlays, picker, queue, sync } = ctx;
    const permHost = S.root.querySelector("[data-perm-host]");

    let autoAvailable = Boolean(S.config.openrouter);

    function permMode() {
    return S.threadBypass ? "bypass" : (S.threadAuto ? "auto" : "ask");
  }

    // The permissions menu mirrors the model picker: filter row on top, one
  // group per machine (the chat's own machine first), single-line rows, and
  // the mode control at the foot where the picker keeps thinking effort.
  function bindPermsMenu(menu) {
    const search = menu.querySelector("[data-perms-search]");
    menu.addEventListener("click", (event) => event.stopPropagation());
    menu.querySelector("[data-perms-close]")?.addEventListener("click", () => setPermsOpen(false));
    search.addEventListener("input", () => {
      permsFilter = search.value;
      paintPermsList();
    });
    menu.addEventListener("keydown", (event) => {
      if (event.key !== "Escape") return;
      event.preventDefault();
      event.stopPropagation();
      if (search.value) {
        search.value = "";
        permsFilter = "";
        paintPermsList();
        search.focus();
        return;
      }
      setPermsOpen(false);
      if (S.permChip) S.permChip.focus();
    });
    menu.querySelector("[data-perms-modes]").addEventListener("click", (event) => {
      const btn = event.target.closest("[data-perms-mode]");
      if (btn && !btn.disabled) setPermMode(btn.getAttribute("data-perms-mode"));
    });
    menu.querySelector("[data-perms-list]").addEventListener("click", (event) => {
      const btn = event.target.closest("[data-perm-revoke]");
      if (btn) revokePermGrant(btn);
    });
  }

    function renderPermsMenu() {
    if (!permHost) return;
    if (permsMenuEl) permsMenuEl.remove();
    const menu = document.createElement("div");
    menu.className = "chat-perms-menu" + (S.pickerSheetQuery.matches ? " is-sheet" : "");
    menu.setAttribute("role", "dialog");
    menu.setAttribute("aria-label", "Permissions");
    menu.hidden = true;
    menu.innerHTML =
      '<div class="chat-picker-head"><p>Permissions</p><button type="button" class="chat-menu-close" data-perms-close aria-label="Close"><svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><path d="M3.5 3.5l9 9M12.5 3.5l-9 9" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg></button></div>' +
      '<div class="chat-picker-searchrow"><svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><circle cx="7" cy="7" r="4.4" fill="none" stroke="currentColor" stroke-width="1.5"/><path d="M10.4 10.4L13.8 13.8" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg><input type="search" class="chat-picker-search" data-perms-search placeholder="Filter machines and directories" aria-label="Filter machines and directories" autocomplete="off" spellcheck="false"><kbd class="chat-picker-kbd" aria-hidden="true">esc</kbd></div>' +
      '<div class="chat-perms-list" data-perms-list></div>' +
      '<div class="chat-perms-foot"><div class="chat-perms-mode"><p class="chat-perms-mode-label" aria-hidden="true">Mode</p><div class="chat-perms-modes" data-perms-modes role="radiogroup" aria-label="Permission mode">' +
      PERMS_MODES.map((m) => `<button type="button" role="radio" aria-checked="false" data-perms-mode="${m.id}">${m.label}</button>`).join("") +
      '</div></div><p class="chat-perms-hint" data-perms-hint></p></div>';
    bindPermsMenu(menu);
    // Phones get the model picker's full-screen sheet, portaled to the chat
    // root for the same reason (composer ancestors would contain it).
    overlays.attachSheetSwipe(menu, ".chat-picker-head", () => menu.classList.contains("is-sheet"), () => setPermsOpen(false));
    if (S.pickerSheetQuery.matches) S.root.appendChild(menu);
    else permHost.appendChild(menu);
    permsMenuEl = menu;
    paintPermsPanel();
  }

    function placePermsMenu() {
    composer.placeComposerPop(permsMenuEl, S.permChip, 30 * 16, true);
  }

    function setPermsOpen(open) {
    const next = Boolean(open);
    if (!permHost) return;
    if (next === S.permsOpen) {
      if (next) refreshPerms();
      return;
    }
    S.permsOpen = next;
    if (next) {
      picker.closePicker();
      overlays.overlayPush("perms");
      if (!permsMenuEl) renderPermsMenu();
      else permsMenuEl.classList.toggle("is-sheet", S.pickerSheetQuery.matches);
      const search = permsMenuEl && permsMenuEl.querySelector("[data-perms-search]");
      if (search) search.value = "";
      permsFilter = "";
      if (permsMenuEl) permsMenuEl.hidden = false;
      paintPermsPanel();
      placePermsMenu();
      // Same rule as the model picker: focus would pop the phone keyboard.
      if (search && !S.pickerSheetQuery.matches) search.focus({ preventScroll: true });
      refreshPerms();
    } else {
      overlays.armOverlayGhost(permsMenuEl?.querySelector("[data-perms-close]"));
      overlays.overlayDrop("perms");
      if (permsMenuEl) permsMenuEl.hidden = true;
    }
    queue.syncPermChip();
  }

    function paintPermsPanel() {
    paintPermsMode();
    paintPermsList();
  }

    function paintPermsMode() {
    if (!permsMenuEl) return;
    const mode = permMode();
    permsMenuEl.querySelectorAll("[data-perms-mode]").forEach((btn) => {
      const id = btn.getAttribute("data-perms-mode");
      const on = id === mode;
      btn.classList.toggle("is-on", on);
      btn.setAttribute("aria-checked", on ? "true" : "false");
      btn.disabled = id === "auto" && !autoAvailable && !on;
      btn.title = btn.disabled ? "Add an OpenRouter key on Sources to enable auto-approve." : "";
    });
    const hint = permsMenuEl.querySelector("[data-perms-hint]");
    if (hint) {
      const spec = PERMS_MODES.find((m) => m.id === mode) || PERMS_MODES[0];
      hint.textContent = spec.hint + (autoAvailable ? "" : " Auto needs an OpenRouter key on Sources.");
    }
  }

    async function revokePermGrant(btn) {
    const row = btn.closest("[data-perm-grant]");
    const gid = Number(row && row.getAttribute("data-perm-grant"));
    if (!gid || !threadPermData) return;
    // Drop the row at once; a failure reloads the real list.
    const neighbour = (row.nextElementSibling && row.nextElementSibling.matches("[data-perm-grant]") && row.nextElementSibling) ||
      (row.previousElementSibling && row.previousElementSibling.matches("[data-perm-grant]") && row.previousElementSibling);
    const nextId = neighbour && neighbour.getAttribute("data-perm-grant");
    threadPermData = {
      ...threadPermData,
      hosts: permHostList().map((h) => ({ ...h, grants: (h.grants || []).filter((g) => Number(g.id) !== gid) })),
    };
    paintPermsList();
    if (!S.pickerSheetQuery.matches && permsMenuEl) {
      const target = (nextId && permsMenuEl.querySelector(`[data-perm-grant="${nextId}"] [data-perm-revoke]`)) ||
        permsMenuEl.querySelector("[data-perms-search]");
      if (target) target.focus({ preventScroll: true });
    }
    try {
      await net.api(`/chat/permissions/grants/${encodeURIComponent(gid)}`, { method: "DELETE" });
    } catch (error) {
      if (error && error.status === 404) return;
      sync.showFailure("Could not remove access", error);
      refreshPerms();
    }
  }

    async function refreshPerms() {
    if (!S.currentId) return;
    if (!permsMenuEl) renderPermsMenu();
    try {
      threadPermData = await net.api(`/chat/threads/${encodeURIComponent(S.currentId)}/permissions`);
      S.threadBypass = Boolean(threadPermData.bypass);
      S.threadAuto = Boolean(threadPermData.auto);
      autoAvailable = Boolean(threadPermData.auto_available);
      if (threadPermData.auto_available === false) S.threadAuto = false;
      queue.seedPendingPermCards(threadPermData);
      queue.syncPermChip();
      paintPermsPanel();
    } catch (error) {
      const listEl = permsMenuEl && permsMenuEl.querySelector("[data-perms-list]");
      if (listEl && !threadPermData) listEl.innerHTML = '<p class="chat-perms-empty">Could not load permissions.</p>';
    }
  }

    async function setPermMode(mode) {
    if (!S.currentId || mode === permMode()) return;
    if (mode === "auto" && !autoAvailable) return;
    const body = { expected_revision: S.serverRevision };
    if (mode === "bypass") body.bypass_permissions = true;
    else {
      if (S.threadBypass) body.bypass_permissions = false;
      if (S.threadAuto !== (mode === "auto")) body.auto_permissions = mode === "auto";
    }
    const prev = [S.threadBypass, S.threadAuto];
    S.threadBypass = mode === "bypass";
    if (mode !== "bypass") S.threadAuto = mode === "auto";
    queue.syncPermChip();
    try {
      const data = await net.api(`/chat/threads/${encodeURIComponent(S.currentId)}`, {
        method: "PATCH",
        body: JSON.stringify(body),
      });
      if (data && Object.prototype.hasOwnProperty.call(data, "bypass_permissions")) S.threadBypass = Boolean(data.bypass_permissions);
      if (data && Object.prototype.hasOwnProperty.call(data, "auto_permissions")) S.threadAuto = Boolean(data.auto_permissions);
      if (data && data.revision != null) S.serverRevision = data.revision;
      if (S.threadBypass) {
        S.permCards.clear();
        queue.renderPermQueue();
      }
    } catch (error) {
      [S.threadBypass, S.threadAuto] = prev;
      sync.showFailure("Could not change permission mode", error);
    }
    queue.syncPermChip();
  }

  return {
    paintPermsMode,
    paintPermsPanel,
    permHost,
    placePermsMenu,
    refreshPerms,
    renderPermsMenu,
    setPermsOpen,
    get permsFilter() { return permsFilter; },
    set permsFilter(value) { permsFilter = value; },
    get permsMenuEl() { return permsMenuEl; },
    set permsMenuEl(value) { permsMenuEl = value; },
    get threadPermData() { return threadPermData; },
    set threadPermData(value) { threadPermData = value; },
  };
}

export { permsFilter, permsMenuEl, threadPermData };
