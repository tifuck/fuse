function csrfToken() {
  const el = document.querySelector('meta[name="csrf-token"]');
  return (el && el.getAttribute("content")) || "";
}

window.fuseConfirm = function fuseConfirm(message, opts) {
  const options = opts && typeof opts === "object" ? opts : {};
  const dialog = document.querySelector("dialog.modal[data-confirm]");
  const text = dialog && dialog.querySelector("[data-confirm-text]");
  const yes = dialog && dialog.querySelector("[data-confirm-yes]");
  const no = dialog && dialog.querySelector("[data-confirm-no]");
  if (!dialog || !text || !yes || !no || typeof dialog.showModal !== "function") {
    return Promise.resolve(window.confirm(message || "Are you sure?"));
  }
  if (dialog._fuseConfirmBusy) return Promise.resolve(false);
  dialog._fuseConfirmBusy = new Promise((resolve) => {
    const label = options.confirmLabel || "Confirm";
    const previous = yes.textContent;
    let settled = false;
    text.textContent = message || "Are you sure?";
    yes.textContent = label;
    yes.classList.toggle("danger", Boolean(options.danger));
    const finish = (ok) => {
      if (settled) return;
      settled = true;
      yes.removeEventListener("click", onYes);
      no.removeEventListener("click", onNo);
      dialog.removeEventListener("cancel", onCancel);
      dialog.removeEventListener("click", onBackdrop);
      dialog.removeEventListener("close", onClose);
      yes.textContent = previous;
      yes.classList.remove("danger");
      dialog._fuseConfirmBusy = null;
      if (dialog.open) dialog.close();
      resolve(Boolean(ok));
    };
    const onYes = (event) => {
      event.preventDefault();
      finish(true);
    };
    const onNo = (event) => {
      event.preventDefault();
      finish(false);
    };
    const onCancel = (event) => {
      event.preventDefault();
      finish(false);
    };
    const onBackdrop = (event) => {
      if (event.target === dialog) finish(false);
    };
    const onClose = () => finish(false);
    yes.addEventListener("click", onYes);
    no.addEventListener("click", onNo);
    dialog.addEventListener("cancel", onCancel);
    dialog.addEventListener("click", onBackdrop);
    dialog.addEventListener("close", onClose, { once: true });
    if (!dialog.open) dialog.showModal();
    yes.focus();
  });
  return dialog._fuseConfirmBusy;
};
function fuseConfirm(message, opts) {
  return window.fuseConfirm(message, opts);
}

// Screen-reader announcements through the page's two standing live regions
// (base.html). Clearing first lets a repeated message be read again.
window.fuseAnnounce = function fuseAnnounce(text, assertive) {
  const el = document.querySelector(assertive ? "[data-announce-alert]" : "[data-announce]");
  if (!el) return;
  el.textContent = "";
  window.setTimeout(() => { el.textContent = text || ""; }, 50);
};

(function openRouterSettings() {
  const root = document.querySelector("[data-or-models]");
  if (!root) return;
  const settings = document.querySelector("[data-or-settings]");
  const status = document.querySelector("[data-or-status]");
  const tpl = document.querySelector("[data-or-drawer-tpl]");
  const list = root.querySelector("[data-or-list]");
  const filter = root.querySelector("[data-or-filter]");
  const count = root.querySelector("[data-or-count]");
  const empty = root.querySelector("[data-or-empty]");
  const paidNote = root.querySelector("[data-or-paid-note]");
  const rows = Array.from(list.querySelectorAll("[data-or-row]"));
  let view = "all";
  let statusTimer = 0;

  function say(text, bad) {
    if (!status) return;
    status.textContent = text;
    status.classList.toggle("bad", Boolean(bad));
    status.classList.add("show");
    clearTimeout(statusTimer);
    statusTimer = setTimeout(() => status.classList.remove("show"), bad ? 5000 : 1800);
  }

  async function send(method, url, body) {
    const r = await fetch(url, {
      method,
      credentials: "same-origin",
      headers: body ? { "Content-Type": "application/json", "X-CSRF-Token": csrfToken() } : {},
      body: body ? JSON.stringify(body) : undefined,
    });
    let data = {};
    try { data = await r.json(); } catch (_) { /* non-JSON error page */ }
    if (!r.ok) throw new Error(data.error || `Request failed (${r.status})`);
    return data;
  }

  function isOn(row) { return row.classList.contains("is-on"); }

  function refresh() {
    const needle = (filter.value || "").trim().toLowerCase();
    let shown = 0;
    let on = 0;
    rows.forEach((row) => {
      if (isOn(row)) on += 1;
      const text = row.dataset.id + " " + row.querySelector(".or-row-name").textContent.toLowerCase();
      const match =
        (!needle || text.toLowerCase().includes(needle)) &&
        (view === "all" ||
          (view === "on" && isOn(row)) ||
          (view === "pinned" && Number(row.dataset.pinned) > 0) ||
          (view === "free" && row.dataset.free === "1"));
      row.hidden = !match;
      if (match) shown += 1;
    });
    count.textContent = `${on} enabled · ${shown} shown`;
    empty.hidden = shown > 0;
  }

  // clearPaid: "Every paid model" was just switched off, which empties the list.
  function applyAccount(state, clearPaid) {
    paidNote.hidden = !state.paid_all;
    rows.forEach((row) => {
      const box = row.querySelector("[data-or-model]");
      const free = row.dataset.free === "1";
      const on = free ? state.free_models : state.paid_all || (!clearPaid && box.checked);
      box.disabled = free || state.paid_all;
      box.checked = on;
      row.classList.toggle("is-on", on);
    });
    refresh();
  }

  if (settings) {
    settings.addEventListener("change", async (event) => {
      const box = event.target.closest("[data-or-setting]");
      if (!box) return;
      const key = box.dataset.orSetting;
      const want = box.checked;
      box.disabled = true;
      try {
        const state = await send("POST", "/sources/openrouter/settings", { [key]: want });
        settings.querySelectorAll("[data-or-setting]").forEach((el) => {
          if (el.dataset.orSetting in state) el.checked = Boolean(state[el.dataset.orSetting]);
        });
        applyAccount(state, key === "paid_all" && !want);
        say(key === "paid_all" && !want ? "Paid models are off. Turn on the ones you want." : "Saved");
      } catch (err) {
        box.checked = !want;
        say(err.message, true);
      } finally {
        box.disabled = false;
      }
    });
  }

  list.addEventListener("change", async (event) => {
    const box = event.target.closest("[data-or-model]");
    if (!box) return;
    const row = box.closest("[data-or-row]");
    const want = box.checked;
    row.classList.toggle("is-on", want);
    refresh();
    try {
      const data = await send("POST", "/sources/openrouter/model", { model: row.dataset.id, enabled: want });
      box.checked = data.enabled;
      row.classList.toggle("is-on", data.enabled);
      say(data.enabled ? "Added to your model picker" : "Removed from your model picker");
    } catch (err) {
      box.checked = !want;
      row.classList.toggle("is-on", !want);
      say(err.message, true);
    }
    refresh();
  });

  function money(v) {
    if (v === null || v === undefined) return "—";
    if (v === 0) return "Free";
    return "$" + (v < 0.1 ? v.toPrecision(2) : Number(v.toFixed(2)).toString());
  }

  function tokens(n) {
    if (!n) return "—";
    return n >= 1000000 ? `${+(n / 1048576).toFixed(1)}M` : `${Math.round(n / 1024)}K`;
  }

  function closeDrawer(row) {
    const drawer = row.querySelector("[data-or-drawer]");
    if (drawer) drawer.remove();
    row.classList.remove("is-open");
    row.querySelector("[data-or-providers]").setAttribute("aria-expanded", "false");
  }

  function setPinned(row, n) {
    row.dataset.pinned = String(n);
    row.querySelector("[data-or-pin-label]").textContent = n ? `${n} pinned` : "Providers";
  }

  async function openDrawer(row) {
    rows.forEach((other) => { if (other !== row && other.classList.contains("is-open")) closeDrawer(other); });
    const drawer = tpl.content.firstElementChild.cloneNode(true);
    row.appendChild(drawer);
    row.classList.add("is-open");
    row.querySelector("[data-or-providers]").setAttribute("aria-expanded", "true");
    const title = drawer.querySelector("[data-or-drawer-title]");
    const note = drawer.querySelector("[data-or-drawer-note]");
    const table = drawer.querySelector("table");
    const body = table.querySelector("tbody");
    const actions = drawer.querySelector(".or-drawer-actions");
    const hint = drawer.querySelector("[data-or-hint]");
    let data;
    try {
      data = await send("GET", "/sources/openrouter/providers?model=" + encodeURIComponent(row.dataset.id));
    } catch (err) {
      note.textContent = err.message;
      note.classList.add("bad");
      return;
    }
    if (!row.contains(drawer)) return;
    const providers = data.providers || [];
    title.textContent = `Providers for ${row.querySelector(".or-row-name").textContent}`;
    if (!providers.length) {
      note.textContent = "OpenRouter lists no providers for this model right now.";
      return;
    }
    const chosen = new Set(data.selected || []);
    const stale = [...chosen].filter((slug) => !providers.some((p) => p.slug === slug));
    note.textContent = chosen.size
      ? "Requests for this model only go to the checked providers. OpenRouter still balances between them."
      : "Any provider can serve this model. Check providers to allow only those.";
    if (stale.length) note.textContent += ` No longer listed: ${stale.join(", ")}.`;
    providers.forEach((p) => {
      const tr = document.createElement("tr");
      if (!p.up) tr.className = "down";
      // Provider fields come from OpenRouter: build with text nodes only.
      const cell = (text, cls) => {
        const td = document.createElement("td");
        if (cls) td.className = cls;
        if (text !== undefined) td.textContent = text;
        tr.appendChild(td);
        return td;
      };
      const tag = (parent, text, cls) => {
        const s = document.createElement("span");
        s.className = cls;
        s.textContent = text;
        parent.append(" ", s);
      };
      const box = document.createElement("input");
      box.type = "checkbox";
      box.setAttribute("aria-label", "Allow " + p.name);
      cell().appendChild(box);
      const nameTd = cell(undefined, "or-prov-name");
      const nameCell = document.createElement("span");
      nameTd.appendChild(nameCell);
      if (p.quantization) tag(nameTd, p.quantization, "or-sub");
      if ((p.variants || []).length > 1) tag(nameTd, p.variants.length + " endpoints", "or-sub");
      cell(money(p.input), "num");
      cell(money(p.output), "num");
      cell(money(p.cache_read), "num");
      cell(tokens(p.context), "num");
      cell(p.uptime === null ? "—" : p.uptime + "%", "num");
      if (p.privacy_url && /^https:\/\//.test(p.privacy_url)) {
        const a = document.createElement("a");
        a.href = p.privacy_url;
        a.target = "_blank";
        a.rel = "noopener noreferrer";
        a.textContent = p.name;
        a.title = "Privacy policy";
        nameCell.appendChild(a);
      } else {
        nameCell.textContent = p.name;
      }
      box.value = p.slug;
      box.checked = chosen.has(p.slug);
      tr.addEventListener("click", (event) => {
        if (event.target === box || event.target.closest("a")) return;
        box.checked = !box.checked;
        box.dispatchEvent(new Event("change", { bubbles: true }));
      });
      body.appendChild(tr);
    });
    function checked() {
      return Array.from(body.querySelectorAll("input:checked")).map((el) => el.value);
    }
    function sync() {
      const n = checked().length;
      hint.textContent = n ? `Only ${n} provider${n === 1 ? "" : "s"} will be used.` : "No providers checked: any provider can serve it.";
    }
    body.addEventListener("change", sync);
    table.hidden = false;
    actions.hidden = false;
    sync();
    async function save(slugs) {
      const buttons = actions.querySelectorAll("button");
      buttons.forEach((b) => { b.disabled = true; });
      try {
        const res = await send("POST", "/sources/openrouter/providers", { model: row.dataset.id, providers: slugs });
        setPinned(row, (res.selected || []).length);
        closeDrawer(row);
        say(res.selected && res.selected.length ? "Providers pinned" : "Using any provider");
        refresh();
      } catch (err) {
        say(err.message, true);
        buttons.forEach((b) => { b.disabled = false; });
      }
    }
    drawer.querySelector("[data-or-save]").addEventListener("click", () => save(checked()));
    drawer.querySelector("[data-or-any]").addEventListener("click", () => save([]));
  }

  list.addEventListener("click", (event) => {
    const btn = event.target.closest("[data-or-providers]");
    if (!btn) return;
    const row = btn.closest("[data-or-row]");
    if (row.classList.contains("is-open")) closeDrawer(row);
    else openDrawer(row);
  });

  list.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    const row = event.target.closest(".is-open");
    if (!row) return;
    closeDrawer(row);
    row.querySelector("[data-or-providers]").focus();
  });

  root.querySelectorAll("[data-or-view]").forEach((btn) => {
    btn.addEventListener("click", () => {
      view = btn.dataset.orView;
      root.querySelectorAll("[data-or-view]").forEach((b) => {
        const on = b === btn;
        b.classList.toggle("on", on);
        b.setAttribute("aria-pressed", on ? "true" : "false");
      });
      refresh();
    });
  });
  filter.addEventListener("input", refresh);
  filter.addEventListener("keydown", (event) => {
    if (event.key === "Enter") event.preventDefault();
  });
  refresh();
})();

(function accountMenu() {
  document.querySelectorAll("[data-account]").forEach((root) => {
    const toggle = root.querySelector("[data-account-toggle]");
    const menu = root.querySelector("[data-account-menu]");
    if (!toggle || !menu) return;

    const closer = root.querySelector("[data-account-close]");
    const backId = "account:" + (toggle.id || Math.random().toString(36).slice(2));
    function setOpen(open) {
      const next = Boolean(open);
      const was = !menu.hidden;
      const focusInside = menu.contains(document.activeElement);
      root.classList.toggle("is-open", next);
      menu.hidden = !next;
      toggle.setAttribute("aria-expanded", next ? "true" : "false");
      if (next && !was) FuseBack.open(backId, () => setOpen(false));
      else if (!next && was) FuseBack.drop(backId);
      // The menu sits above its button in the DOM: move focus in on open, and
      // back to the button when it closes around the focus.
      if (next && !was) menu.querySelector("nav a, form button")?.focus();
      else if (!next && was && focusInside) toggle.focus();
    }

    toggle.addEventListener("click", (event) => {
      event.stopPropagation();
      const next = menu.hidden;
      document.querySelectorAll("[data-account]").forEach((other) => {
        if (other !== root) {
          other.classList.remove("is-open");
          const otherMenu = other.querySelector("[data-account-menu]");
          const otherToggle = other.querySelector("[data-account-toggle]");
          if (otherMenu) otherMenu.hidden = true;
          if (otherToggle) otherToggle.setAttribute("aria-expanded", "false");
        }
      });
      setOpen(next);
    });
    closer?.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
      setOpen(false);
    });
    document.addEventListener("click", (event) => {
      if (!root.contains(event.target)) setOpen(false);
    });
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape") setOpen(false);
    });
  });
})();

(function settingsRail() {
  const shell = document.querySelector("[data-settings]");
  const rail = document.querySelector("[data-app-rail]");
  const toggles = Array.from(document.querySelectorAll("[data-rail-toggle]"));
  const scrim = document.querySelector("[data-nav-scrim]");
  if (!shell || !rail || !toggles.length) return;

  const mobile = window.matchMedia("(max-width: 800px)");
  const STORE_WIDTH = "fuse.settings.railWidth";
  const WIDTH = { min: 14 * 16, max: 30 * 16, default: 19 * 16 };
  const background = shell.querySelectorAll(".settings-bar > :not([data-rail-toggle]), .settings-scroll");

  function clampWidth(px) {
    const deskMax = Math.min(WIDTH.max, Math.max(WIDTH.min, window.innerWidth - 340));
    return Math.round(Math.min(deskMax, Math.max(WIDTH.min, px)));
  }
  function applyWidth(px, persist) {
    shell.style.setProperty("--chat-history", px + "px");
    shell.querySelector("[data-rail-resize]")?.setAttribute("aria-valuenow", String(px));
    if (persist) {
      try { localStorage.setItem(STORE_WIDTH, String(px)); } catch (_) {}
    }
  }
  function currentWidth() {
    const raw = Number.parseFloat(shell.style.getPropertyValue("--chat-history"));
    return Number.isFinite(raw) ? raw : WIDTH.default;
  }

  function sync() {
    const drawer = mobile.matches && shell.classList.contains("is-history");
    const expanded = mobile.matches ? drawer : !shell.classList.contains("history-collapsed");
    toggles.forEach((btn) => {
      btn.setAttribute("aria-expanded", expanded ? "true" : "false");
      const label = btn.querySelector(".visually-hidden");
      if (label) label.textContent = expanded ? "Close sidebar" : "Open sidebar";
    });
    if (scrim) scrim.hidden = !drawer;
    document.body.classList.toggle("is-nav", drawer);
    background.forEach((el) => { el.inert = drawer; });
    rail.inert = !expanded;
  }

  function setDrawer(open) {
    const next = Boolean(open);
    const was = shell.classList.contains("is-history");
    shell.classList.toggle("is-history", next);
    document.body.classList.toggle("is-nav", next && mobile.matches);
    if (next && !was && mobile.matches) FuseBack.open("settings-rail", () => setDrawer(false));
    else if (!next && was) FuseBack.drop("settings-rail");
    sync();
  }

  toggles.forEach((btn) => {
    btn.addEventListener("click", (event) => {
      event.stopPropagation();
      if (mobile.matches) setDrawer(!shell.classList.contains("is-history"));
      else {
        shell.classList.toggle("history-collapsed");
        sync();
      }
    });
  });
  if (scrim) scrim.addEventListener("click", () => setDrawer(false));
  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    if (mobile.matches && shell.classList.contains("is-history")) {
      event.stopPropagation();
      setDrawer(false);
    }
  });
  mobile.addEventListener("change", () => {
    setDrawer(false);
    shell.classList.remove("history-collapsed");
    sync();
  });

  const resizeHandle = shell.querySelector("[data-rail-resize]");
  if (resizeHandle) {
    let savedWidth = 0;
    try { savedWidth = Number(localStorage.getItem(STORE_WIDTH)) || 0; } catch (_) {}
    if (savedWidth >= WIDTH.min && savedWidth <= WIDTH.max) applyWidth(savedWidth, false);
    let resizeRaf = 0;
    resizeHandle.addEventListener("pointerdown", (event) => {
      if (window.innerWidth <= 800) return;
      event.preventDefault();
      try { resizeHandle.setPointerCapture(event.pointerId); } catch (_) {}
      resizeHandle.classList.add("is-dragging");
      document.body.classList.add("is-resizing-history");
      const onMove = (moveEvent) => {
        const rect = shell.getBoundingClientRect();
        cancelAnimationFrame(resizeRaf);
        resizeRaf = requestAnimationFrame(() => {
          applyWidth(clampWidth(moveEvent.clientX - rect.left), false);
        });
      };
      const onUp = () => {
        resizeHandle.classList.remove("is-dragging");
        document.body.classList.remove("is-resizing-history");
        resizeHandle.removeEventListener("pointermove", onMove);
        resizeHandle.removeEventListener("pointerup", onUp);
        resizeHandle.removeEventListener("pointercancel", onUp);
        applyWidth(clampWidth(currentWidth()), true);
      };
      resizeHandle.addEventListener("pointermove", onMove);
      resizeHandle.addEventListener("pointerup", onUp);
      resizeHandle.addEventListener("pointercancel", onUp);
    });
    resizeHandle.addEventListener("keydown", (event) => {
      const step = event.shiftKey ? 32 : 16;
      const now = currentWidth();
      const to = { ArrowRight: now + step, ArrowLeft: now - step, Home: WIDTH.min, End: WIDTH.max, Enter: WIDTH.default }[event.key];
      if (to === undefined) return;
      event.preventDefault();
      applyWidth(clampWidth(to), true);
    });
    resizeHandle.addEventListener("dblclick", () => applyWidth(WIDTH.default, true));
  }

  sync();
})();

document.querySelectorAll(".copy").forEach((el) => {
  el.addEventListener("click", async () => {
    const t = el.getAttribute("data-copy") || el.textContent;
    const originalTitle = el.getAttribute("title") || "";
    try {
      await navigator.clipboard.writeText(t.trim());
      el.classList.add("ok");
      setTimeout(() => el.classList.remove("ok"), 800);
    } catch (_) {
      el.classList.add("err");
      el.setAttribute("title", "Clipboard copy failed. Select and copy the text manually.");
      el.setAttribute("aria-label", "Clipboard copy failed");
      const label = el.querySelector(".catalog-copy");
      if (label) label.textContent = "Copy failed";
      const failure = document.createElement("span");
      failure.className = "err";
      failure.textContent = " Copy failed";
      if (!label) el.appendChild(failure);
      setTimeout(() => {
        el.classList.remove("err");
        if (originalTitle) el.setAttribute("title", originalTitle);
        else el.removeAttribute("title");
        el.removeAttribute("aria-label");
        if (label) label.textContent = "Copy";
        failure.remove();
      }, 2200);
    }
  });
});

const docsJump = document.querySelector("[data-docs-jump]");
if (docsJump) {
  const links = [...docsJump.querySelectorAll("a[href^='#']")];
  const mark = (id) => {
    links.forEach((link) => link.classList.toggle("on", link.getAttribute("href") === "#" + id));
  };
  links.forEach((link) => {
    link.addEventListener("click", () => mark((link.getAttribute("href") || "").slice(1)));
  });
  const sections = links
    .map((link) => document.getElementById((link.getAttribute("href") || "").slice(1)))
    .filter(Boolean);
  if (sections.length && "IntersectionObserver" in window) {
    const io = new IntersectionObserver(
      (entries) => {
        const vis = entries
          .filter((entry) => entry.isIntersecting)
          .sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top)[0];
        if (!vis) return;
        mark(vis.target.id);
      },
      { rootMargin: "-20% 0px -65% 0px", threshold: [0, 0.15, 0.4] }
    );
    sections.forEach((section) => io.observe(section));
  }
}

// Section tabs: a .settings-jump nav of #anchors becomes real tabs, so a page
// with several long sections (Settings, admin Providers and Pools) shows one at
// a time instead of scrolling the whole shell to an anchor. A section belongs
// to a tab by its id or by data-section="<id>". The open tab survives a form
// post (sessionStorage per path) and is linkable (#id).
document.querySelectorAll("nav.settings-jump").forEach((nav) => {
  const links = Array.from(nav.querySelectorAll('a[href^="#"]'));
  if (!links.length) return;
  const ids = links.map((a) => a.getAttribute("href").slice(1));
  const scope = nav.parentElement;
  const members = (id) => Array.from(scope.querySelectorAll(`#${CSS.escape(id)}, [data-section="${id}"]`));
  if (!ids.every((id) => members(id).length)) return;
  const key = "fuse.tabs." + location.pathname;
  nav.setAttribute("role", "tablist");
  links.forEach((a) => {
    a.setAttribute("role", "tab");
    a.setAttribute("aria-controls", a.getAttribute("href").slice(1));
  });
  function show(id, push) {
    if (!ids.includes(id)) id = ids[0];
    links.forEach((a) => {
      const on = a.getAttribute("href") === "#" + id;
      a.classList.toggle("on", on);
      a.setAttribute("aria-selected", on ? "true" : "false");
      a.tabIndex = on ? 0 : -1;
    });
    ids.forEach((other) => members(other).forEach((el) => { el.hidden = other !== id; }));
    try { sessionStorage.setItem(key, id); } catch (_) {}
    if (push) history.replaceState(null, "", "#" + id);
    const scroller = nav.closest(".settings-scroll");
    if (scroller && push) scroller.scrollTop = 0;
    document.dispatchEvent(new CustomEvent("fuse-section-tab", { detail: { id } }));
  }
  nav.addEventListener("click", (event) => {
    const a = event.target.closest('a[href^="#"]');
    if (!a) return;
    event.preventDefault();
    show(a.getAttribute("href").slice(1), true);
    a.focus();
  });
  nav.addEventListener("keydown", (event) => {
    if (event.key !== "ArrowRight" && event.key !== "ArrowLeft") return;
    const i = links.indexOf(document.activeElement);
    if (i < 0) return;
    event.preventDefault();
    const next = links[(i + (event.key === "ArrowRight" ? 1 : links.length - 1)) % links.length];
    show(next.getAttribute("href").slice(1), true);
    next.focus();
  });
  let start = location.hash.slice(1);
  if (!ids.includes(start)) {
    try { start = sessionStorage.getItem(key) || ""; } catch (_) { start = ""; }
  }
  show(start, false);
  window.addEventListener("hashchange", () => { if (ids.includes(location.hash.slice(1))) show(location.hash.slice(1), false); });
});

document.querySelectorAll("[data-page-tabs]").forEach((nav) => {
  const root = nav.closest("[data-tab-root]") || document;
  nav.querySelectorAll("[data-tab]").forEach((button) => {
    const panel = root.querySelector(`[data-tab-panel="${CSS.escape(button.dataset.tab)}"]`);
    if (panel) {
      panel.id ||= "tabpanel-" + button.dataset.tab;
      button.setAttribute("aria-controls", panel.id);
    }
    button.addEventListener("click", () => {
      const tab = button.dataset.tab;
      nav.querySelectorAll("[data-tab]").forEach((item) => {
        const on = item === button;
        item.classList.toggle("on", on);
        item.setAttribute("aria-selected", on ? "true" : "false");
      });
      root.querySelectorAll("[data-tab-panel]").forEach((panel) => {
        panel.classList.toggle("hidden", panel.getAttribute("data-tab-panel") !== tab);
      });
    });
  });
});

// Every other tablist (settings sections have their own above): one tab stop
// on the selected tab, arrow/Home/End keys select a neighbour by clicking it so
// each page's own handler runs, and the tab's panel (aria-controls) is a
// tabpanel labelled by the tab that is showing it.
document.querySelectorAll('[role="tablist"]:not(.settings-jump)').forEach((list, n) => {
  const tabs = Array.from(list.querySelectorAll('[role="tab"]'));
  if (!tabs.length) return;
  tabs.forEach((tab, i) => { tab.id ||= `tab-${n}-${i}`; });
  const sync = () => {
    const current = tabs.find((tab) => tab.getAttribute("aria-selected") === "true") || tabs[0];
    tabs.forEach((tab) => {
      tab.tabIndex = tab === current ? 0 : -1;
      const panel = document.getElementById(tab.getAttribute("aria-controls") || "");
      if (panel && tab === current) {
        panel.setAttribute("role", "tabpanel");
        panel.setAttribute("aria-labelledby", tab.id);
      }
    });
  };
  sync();
  new MutationObserver(sync).observe(list, { subtree: true, attributeFilter: ["aria-selected"] });
  list.addEventListener("keydown", (event) => {
    const live = tabs.filter((tab) => !tab.disabled && tab.getClientRects().length);
    const i = live.indexOf(document.activeElement);
    if (i < 0) return;
    const to = { ArrowRight: i + 1, ArrowLeft: i - 1, Home: 0, End: live.length - 1 }[event.key];
    if (to === undefined) return;
    event.preventDefault();
    const next = live[(to + live.length) % live.length];
    next.focus();
    next.click();
  });
});

const artifactVersion = document.querySelector("[data-artifact-page] [data-artifact-version]");
if (artifactVersion) {
  artifactVersion.addEventListener("change", () => {
    const url = new URL(window.location.href);
    url.searchParams.set("v", artifactVersion.value);
    window.location.assign(url.toString());
  });
}

document.querySelectorAll("form[data-confirm]").forEach((form) => {
  form.addEventListener("submit", (event) => {
    if (form.dataset.confirmed === "1") {
      delete form.dataset.confirmed;
      return;
    }
    event.preventDefault();
    const submitter = form.querySelector('button[type="submit"], button:not([type]), input[type="submit"]');
    const confirmLabel = ((submitter && (submitter.textContent || submitter.value)) || "Confirm").replace(/\s+/g, " ").trim() || "Confirm";
    const danger = Boolean(form.querySelector("button.danger, .btn.danger"));
    fuseConfirm(form.getAttribute("data-confirm") || "Are you sure?", {
      confirmLabel,
      danger,
    }).then((ok) => {
      if (!ok) return;
      form.dataset.confirmed = "1";
      if (typeof form.requestSubmit === "function") form.requestSubmit();
      else form.submit();
    });
  });
});

document.querySelectorAll("[data-dialog-open]").forEach((button) => {
  button.addEventListener("click", () => {
    const dialog = document.getElementById(button.getAttribute("data-dialog-open"));
    if (dialog && !dialog.open) dialog.showModal();
  });
});

document.querySelectorAll("dialog.modal").forEach((dialog) => {
  if (dialog.hasAttribute("data-confirm") && !dialog.id) return;
  dialog.querySelectorAll("[data-dialog-close]").forEach((button) => {
    button.addEventListener("click", () => dialog.close());
  });
  dialog.addEventListener("click", (event) => {
    if (event.target === dialog) dialog.close();
  });
  if (dialog.hasAttribute("data-auto-open")) dialog.showModal();
  const auth = dialog.querySelector("[data-auth-open]");
  if (auth && dialog.hasAttribute("data-auto-open")) {
    const href = auth.getAttribute("href");
    if (href) window.open(href, "_blank", "noopener");
  }
});

function syncSourceForm() {
  const type = document.querySelector("#source-type");
  if (!type) return;
  const v = type.value;
  document.querySelectorAll(".f-url").forEach((n) => n.classList.remove("hidden"));
  document.querySelectorAll(".f-llama").forEach((n) => n.classList.toggle("hidden", v !== "llama"));
  document.querySelectorAll(".f-keys").forEach((n) => n.classList.toggle("hidden", v === "llama"));
  document.querySelectorAll(".f-rot").forEach((n) => n.classList.toggle("hidden", v !== "openai"));
  document.querySelectorAll(".f-model").forEach((n) => n.classList.toggle("hidden", v !== "ollama"));
}
const typeSel = document.querySelector("#source-type");
if (typeSel) {
  typeSel.addEventListener("change", syncSourceForm);
  syncSourceForm();
}

async function pollDevice(endpoint, interval, redirect) {
  const panel = document.querySelector(`[data-device-poll][data-endpoint="${endpoint}"]`);
  const status = panel ? panel.querySelector(".poll-status") : null;
  let wait = Math.max(3, Number(interval) || 5) * 1000;
  async function tick() {
    try {
      const r = await fetch(endpoint, {
        method: "POST",
        headers: { "X-CSRF-Token": csrfToken() },
      });
      const data = await r.json();
      if (r.status === 401) {
        window.location.assign("/login");
        return;
      }
      if (data.ok) {
        window.location.href = redirect;
        return;
      }
      if (data.pending) {
        if (data.retry_after) wait = Math.max(3, Number(data.retry_after) || 5) * 1000;
        if (status) status.textContent = data.error || "Waiting for confirmation…";
        setTimeout(tick, wait);
        return;
      }
      if (status) status.textContent = data.error || "Sign-in failed.";
    } catch (e) {
      if (status) status.textContent = "Could not reach the server.";
      setTimeout(tick, wait);
    }
  }
  setTimeout(tick, wait);
}

document.querySelectorAll("[data-device-poll]").forEach((device) => {
  pollDevice(
    device.getAttribute("data-endpoint"),
    device.getAttribute("data-interval"),
    device.getAttribute("data-redirect") || window.location.pathname
  );
});

function escapeText(s) {
  return String(s || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function probeBox(id) {
  const el = document.getElementById("probe-" + id);
  if (!el) return null;
  el.classList.remove("hidden");
  if (el.tagName === "TR") return el.querySelector("td") || el;
  return el;
}

function renderProbe(el, data) {
  if (!el) return;
  if (data.error && !(data.checks && data.checks.length)) {
    el.innerHTML = `<p class="callout fault" role="alert">${escapeText(data.error)}</p>`;
    return;
  }
  const items = (data.checks || [])
    .map((c) => {
      const kind = c.skip ? "skip" : c.ok ? "ok" : "bad";
      const mark = c.skip ? "skip" : c.ok ? "ok" : "fail";
      return `<li><span class="mark ${kind}">${mark}</span><span><b>${escapeText(c.name)}</b> <span class="detail">${escapeText(c.detail || "")}</span></span></li>`;
    })
    .join("");
  const ms = data.ms ? ` · ${data.ms}ms` : "";
  const head = data.ok
    ? `<p class="callout ok">Passed${ms}</p>`
    : `<p class="callout fault">Failed${ms}</p>`;
  el.innerHTML = `${head}<ul class="checks">${items}</ul>`;
}

document.querySelectorAll(".probe").forEach((btn) => {
  btn.addEventListener("click", async () => {
    const id = btn.getAttribute("data-source");
    if (!id) return;
    const out = probeBox(id);
    btn.disabled = true;
    const label = btn.textContent;
    btn.textContent = "Testing…";
    if (out) out.innerHTML = `<p class="pulse">Sending a short prompt and checking the wire…</p>`;
    try {
      const r = await fetch("/admin/sources/" + encodeURIComponent(id) + "/test", {
        method: "POST",
        headers: { "X-CSRF-Token": csrfToken() },
      });
      const data = await r.json();
      renderProbe(out, data);
    } catch (_) {
      renderProbe(out, { ok: false, error: "Could not reach the server.", checks: [] });
    } finally {
      btn.disabled = false;
      btn.textContent = label;
    }
  });
});

/* —— Shared toasts + vault unlock prompt (fuseUI) —— */
(() => {
  // fuse.vault.pw used to live in localStorage; the password is now held in
  // this page load only, so a reload re-prompts instead of caching a secret.
  const DISMISS_KEY = "fuse.vault.dismissed";
  let cachedUid = null;
  let cachedPw = "";
  const store = {
    load(uid) {
      return cachedPw && Number(cachedUid) === Number(uid) ? cachedPw : "";
    },
    save(uid, password) {
      cachedUid = Number(uid);
      cachedPw = String(password);
    },
    clear() {
      cachedUid = null;
      cachedPw = "";
    },
  };
  let toastRoot = null;
  let vaultEl = null;
  let vaultScrim = null;
  let vaultHandlers = [];
  let vaultUid = null;
  let autoTried = false;

  function csrf() {
    const el = document.querySelector('meta[name="csrf-token"]');
    return (el && el.getAttribute("content")) || "";
  }

  function ensureRoot() {
    if (!toastRoot || !document.body.contains(toastRoot)) {
      toastRoot = document.createElement("div");
      toastRoot.className = "fuse-toasts";
      document.body.appendChild(toastRoot);
    }
    return toastRoot;
  }

  function dismissed() {
    try {
      return sessionStorage.getItem(DISMISS_KEY) === "1";
    } catch (_) {
      return false;
    }
  }

  function setDismissed() {
    try {
      sessionStorage.setItem(DISMISS_KEY, "1");
    } catch (_) {}
  }

  function clearDismissed() {
    try {
      sessionStorage.removeItem(DISMISS_KEY);
    } catch (_) {}
  }

  function toast(opts) {
    opts = opts || {};
    const key = opts.key || "";
    if (key) {
      const root = toastRoot;
      const existing = root && root.querySelector('[data-toast-key="' + key.replace(/"/g, '\\"') + '"]');
      if (existing && existing._ctrl) {
        existing._ctrl.update(opts);
        return existing._ctrl;
      }
    }
    const el = document.createElement("div");
    el.className = "fuse-toast is-idle";
    if (key) el.setAttribute("data-toast-key", key);
    el.setAttribute("role", "status");
    const dot = document.createElement("span");
    dot.className = "fuse-toast-dot";
    const body = document.createElement("div");
    body.className = "fuse-toast-body";
    const title = document.createElement("b");
    const msg = document.createElement("span");
    body.appendChild(title);
    body.appendChild(msg);
    const act = document.createElement("span");
    act.className = "fuse-toast-act";
    const x = document.createElement("button");
    x.type = "button";
    x.className = "fuse-toast-x";
    x.textContent = "✕";
    x.setAttribute("aria-label", "Dismiss");
    el.appendChild(dot);
    el.appendChild(body);
    el.appendChild(act);
    el.appendChild(x);
    let timer = null;
    const ctrl = {
      el,
      update(next) {
        next = next || {};
        el.className = "fuse-toast is-" + (next.kind || "idle");
        title.textContent = next.title || "";
        msg.textContent = next.msg || "";
        act.innerHTML = "";
        if (next.actionLabel) {
          const btn = document.createElement("button");
          btn.type = "button";
          btn.className = "btn sm ghost";
          btn.textContent = next.actionLabel;
          btn.addEventListener("click", () => {
            if (typeof next.onAction === "function") next.onAction();
          });
          act.appendChild(btn);
        }
        window.clearTimeout(timer);
        if (next.ttl) timer = window.setTimeout(() => ctrl.close(), next.ttl);
      },
      close() {
        window.clearTimeout(timer);
        el.remove();
      },
    };
    el._ctrl = ctrl;
    x.addEventListener("click", () => ctrl.close());
    ensureRoot().appendChild(el);
    ctrl.update(opts);
    return ctrl;
  }

  function closeVault() {
    if (vaultEl) {
      vaultEl.remove();
      vaultEl = null;
    }
    if (vaultScrim) {
      vaultScrim.remove();
      vaultScrim = null;
    }
    vaultHandlers = [];
  }

  function emitUnlocked() {
    clearDismissed();
    const fns = vaultHandlers.slice();
    closeVault();
    fns.forEach((fn) => {
      try {
        fn();
      } catch (_) {}
    });
  }

  function vaultPrompt(opts) {
    opts = opts || {};
    if (typeof opts.onUnlocked === "function" && !vaultHandlers.includes(opts.onUnlocked)) {
      vaultHandlers.push(opts.onUnlocked);
    }
    if (opts.error) clearDismissed();
    if (vaultEl) {
      const err = vaultEl.querySelector(".fuse-vault-err");
      if (err) {
        err.hidden = !opts.error;
        err.textContent = opts.error || "";
      }
      return;
    }
    const el = document.createElement("div");
    el.className = "fuse-vault";
    const scrim = document.createElement("div");
    scrim.className = "fuse-vault-scrim is-on";
    const why = document.createElement("p");
    why.className = "fuse-vault-why";
    why.textContent = opts.message || "Unlock machines to use SSH. Your account password unwraps those secrets here.";
    const form = document.createElement("form");
    const label = document.createElement("label");
    label.className = "fuse-vault-field";
    const span = document.createElement("span");
    span.textContent = "Account password";
    const input = document.createElement("input");
    input.type = "password";
    input.maxLength = 256;
    input.autocomplete = "current-password";
    input.placeholder = "Account password";
    label.appendChild(span);
    label.appendChild(input);
    const submit = document.createElement("button");
    submit.type = "submit";
    submit.className = "btn sm";
    submit.textContent = "Unlock";
    form.appendChild(label);
    form.appendChild(submit);
    const err = document.createElement("p");
    err.className = "callout fault fuse-vault-err";
    err.hidden = true;
    if (opts.error) {
      err.hidden = false;
      err.textContent = opts.error;
    }
    const dismiss = document.createElement("button");
    dismiss.type = "button";
    dismiss.className = "fuse-vault-dismiss";
    dismiss.textContent = "Not now";
    el.appendChild(why);
    el.appendChild(form);
    el.appendChild(err);
    el.appendChild(dismiss);
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const password = input.value;
      if (!password) return;
      submit.disabled = true;
      try {
        const res = await fetch("/account/vault/unlock", {
          method: "POST",
          credentials: "same-origin",
          headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf() },
          body: JSON.stringify({ password }),
        });
        if (res.ok) {
          if (vaultUid != null) store.save(vaultUid, password);
          input.value = "";
          emitUnlocked();
          return;
        }
        if (res.status === 400 || res.status === 401) store.clear();
        let detail = "Password is wrong.";
        try {
          const body = await res.json();
          const msg = body && body.detail && typeof body.detail === "string" ? body.detail : "";
          if (msg) detail = msg;
        } catch (_) {}
        err.hidden = false;
        err.textContent = detail;
      } catch (_) {
        err.hidden = false;
        err.textContent = "Could not reach Fuse. Try again.";
      } finally {
        submit.disabled = false;
      }
    });
    dismiss.addEventListener("click", () => {
      setDismissed();
      closeVault();
    });
    vaultEl = el;
    vaultScrim = scrim;
    document.body.appendChild(scrim);
    document.body.appendChild(el);
    input.focus();
  }

  async function tryStoredPassword() {
    if (vaultUid == null) return false;
    const password = store.load(vaultUid);
    if (!password) return false;
    try {
      const res = await fetch("/account/vault/unlock", {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf() },
        body: JSON.stringify({ password }),
      });
      if (res.ok) {
        store.save(vaultUid, password);
        return true;
      }
      if (res.status === 400 || res.status === 401) store.clear();
      return false;
    } catch (_) {
      return false;
    }
  }

  async function vaultGate(opts) {
    opts = opts || {};
    vaultUid = opts.uid != null ? Number(opts.uid) : vaultUid;
    if (typeof opts.onUnlocked === "function" && !vaultHandlers.includes(opts.onUnlocked)) {
      vaultHandlers.push(opts.onUnlocked);
    }
    if (opts.vault === "unlocked" || opts.hasSsh === false) return;
    if (!autoTried) {
      autoTried = true;
      if (await tryStoredPassword()) {
        emitUnlocked();
        return;
      }
    }
    if (!opts.force && dismissed()) return;
    vaultPrompt(opts);
  }

  window.fuseUI = {
    toast,
    vaultPrompt,
    vaultGate,
    closeVault,
    vault: store,
  };
})();

/* —— Bug report —— */
(function () {
  const ERROR_RING_MAX = 6;
  const errorRing = [];
  const THEME_KEY = "fuse.chat.theme.v1";

  window.addEventListener("error", (event) => {
    errorRing.push({
      message: String(event.message || "error").slice(0, 200),
      source: String(event.filename || "").split("/").pop(),
      line: event.lineno,
      at: new Date().toISOString(),
    });
    if (errorRing.length > ERROR_RING_MAX) errorRing.shift();
  });
  window.addEventListener("unhandledrejection", (event) => {
    const reason = event.reason && (event.reason.message || event.reason);
    errorRing.push({
      message: String(reason || "unhandled rejection").slice(0, 200),
      at: new Date().toISOString(),
    });
    if (errorRing.length > ERROR_RING_MAX) errorRing.shift();
  });

  function currentView() {
    const body = document.body;
    if (body.classList.contains("is-artifact-page")) return "artifact";
    if (body.classList.contains("is-settings")) return "settings";
    if (body.classList.contains("is-chat")) return "chat";
    return "site";
  }

  function uiState() {
    const state = {};
    const toggle = document.querySelector("[data-history-toggle]");
    if (toggle) state.sidebarOpen = toggle.getAttribute("aria-expanded") === "true";
    const tab = document.querySelector("[data-history-tab][aria-selected='true']");
    if (tab) state.historyTab = tab.getAttribute("data-history-tab");
    const artifact = document.querySelector("[data-chat-artifact]");
    if (artifact) state.artifactOpen = !artifact.hidden;
    state.streaming = document.body.classList.contains("is-streaming");
    return state;
  }

  function snapshot() {
    const chatRoot = document.querySelector("[data-chat]");
    const theme = chatRoot ? localStorage.getItem(THEME_KEY) || "" : "";
    return {
      view: currentView(),
      ui_state: uiState(),
      viewport: `${window.innerWidth}x${window.innerHeight}`,
      screen: `${screen.width}x${screen.height}`,
      dpr: Number(window.devicePixelRatio.toFixed(2)),
      locale: navigator.language || "",
      timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || "",
      online: navigator.onLine,
      page_age_ms: Math.round(performance.now()),
      theme,
      thread_ref: (location.pathname.match(/^\/chat\/([^/]+)/) || [])[1] || "",
      user_agent: navigator.userAgent,
      recent_errors: errorRing.slice(-ERROR_RING_MAX),
    };
  }

  function init() {
    const dialog = document.querySelector("dialog.modal[data-bug-report]");
    if (!dialog || typeof dialog.showModal !== "function") return;
    const form = dialog.querySelector("[data-bug-report-form]");
    const done = dialog.querySelector("[data-bug-done]");
    const summary = dialog.querySelector("[data-bug-summary]");
    const expected = dialog.querySelector("[data-bug-expected]");
    const details = dialog.querySelector("[data-bug-details]");
    const contextPre = dialog.querySelector("[data-bug-context]");
    const status = dialog.querySelector("[data-bug-status]");
    const refOut = dialog.querySelector("[data-bug-ref]");
    const send = dialog.querySelector("[data-bug-send]");
    let busy = false;

    const reset = () => {
      form.hidden = false;
      done.hidden = true;
      status.textContent = "";
      send.disabled = false;
      busy = false;
    };

    dialog.addEventListener("close", reset);
    dialog.querySelectorAll("[data-bug-close], [data-bug-finish]").forEach((button) => {
      button.addEventListener("click", () => dialog.close());
    });
    dialog.addEventListener("click", (event) => {
      if (event.target === dialog) dialog.close();
    });

    const toast = (message) => {
      if (window.fuseUI && typeof window.fuseUI.toast === "function") window.fuseUI.toast(message);
    };

    const submit = async (event) => {
      event.preventDefault();
      if (busy) return;
      const text = summary.value.trim();
      if (text.length < 4) {
        status.textContent = "A few words are needed.";
        return;
      }
      busy = true;
      send.disabled = true;
      status.textContent = "Sending…";
      const snap = snapshot();
      contextPre.textContent = JSON.stringify(
        { page: location.pathname + location.search, ...snap },
        null,
        2,
      );
      try {
        const response = await fetch("/report-issue", {
          method: "POST",
          headers: { "Content-Type": "application/json", "X-CSRF-Token": csrfToken() },
          body: JSON.stringify({
            summary: text.slice(0, 4000),
            expected: expected.value.trim().slice(0, 2000),
            page: location.pathname + location.search,
            view: snap.view,
            context: snap,
          }),
        });
        const payload = await response.json().catch(() => ({}));
        if (!response.ok || !payload.ok) {
          const message = payload && payload.detail ? payload.detail : "Could not send the report.";
          status.textContent = typeof message === "string" ? message : "Could not send the report.";
          send.disabled = false;
          busy = false;
          return;
        }
        refOut.textContent = payload.ref || "";
        form.hidden = true;
        done.hidden = false;
        summary.value = "";
        expected.value = "";
      } catch (_) {
        status.textContent = "Network error — try again.";
        send.disabled = false;
        busy = false;
      }
    };

    form.addEventListener("submit", submit);
    details.addEventListener("toggle", () => {
      if (details.open) {
        const snap = snapshot();
        contextPre.textContent = JSON.stringify(
          { page: location.pathname + location.search, ...snap },
          null,
          2,
        );
      }
    });

    document.querySelectorAll("[data-bug-report-trigger]").forEach((button) => {
      button.addEventListener("click", () => {
        reset();
        if (details.open) details.open = false;
        dialog.showModal();
        summary.focus();
      });
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init, { once: true });
  } else {
    init();
  }
})();

(function sourceMineFilter() {
  function byGroup(attr, group) {
    return Array.from(document.querySelectorAll(`[${attr}]`)).filter((el) => el.getAttribute("data-mine-group") === group);
  }

  function tablesFor(input) {
    const group = input.getAttribute("data-mine-group") || "";
    if (group) return byGroup("data-source-table", group);
    const next = input.closest(".stack-y")?.querySelector("[data-source-table]");
    return next ? [next] : [];
  }

  function inputsFor(input) {
    const group = input.getAttribute("data-mine-group") || "";
    if (!group) return [input];
    return byGroup("data-mine-only", group);
  }

  function apply(input) {
    const on = Boolean(input.checked);
    inputsFor(input).forEach((el) => {
      el.checked = on;
    });
    tablesFor(input).forEach((table) => {
      table.classList.toggle("is-mine-only", on);
    });
  }

  document.querySelectorAll("[data-mine-only]").forEach((input) => {
    input.addEventListener("change", () => apply(input));
    apply(input);
  });

  // A private donor uses only their own accounts (the server enforces it too),
  // so "Nobody" locks the routing choice to "Only your accounts".
  document.querySelectorAll("[data-share-form]").forEach((form) => {
    const pool = form.querySelector("[data-share-use-pool]");
    const mine = form.querySelector('input[name="use"][value="mine"]');
    if (!pool || !mine) return;
    let chosen = pool.checked ? pool : mine;
    const sync = () => {
      const isPrivate = Boolean(form.querySelector("[data-share-private]")?.checked);
      if (isPrivate) {
        if (!pool.disabled) chosen = pool.checked ? pool : mine;
        mine.checked = true;
      } else if (pool.disabled) {
        chosen.checked = true;
      }
      pool.disabled = isPrivate;
    };
    form.querySelectorAll('input[name="share"]').forEach((input) => input.addEventListener("change", sync));
    form.querySelectorAll('input[name="use"]').forEach((input) => {
      input.addEventListener("change", () => {
        if (input.checked) chosen = input;
      });
    });
    sync();
  });
})();
