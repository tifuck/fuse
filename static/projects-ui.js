/* One project dialog for every page: the chooser behind chat's Projects
   "+", and the add/edit form (name, machine, folder, read-only, folder color,
   logo) that Machines, Projects and chat all open. Centered on desktop,
   full-screen on phones. Also the one icon helper: a project's folder in its
   own color, or its logo at the same size. */
(() => {
  if (window.FuseProjects) return;

  const COLORS = ["#E5484D", "#F76B15", "#FFC53D", "#46A758", "#12A594", "#0090FF", "#3E63DD", "#8E4EC6", "#D6409F", "#A18072"];
  const LOGO_PX = 128;
  const LOGO_MAX = 96 * 1024;
  const FOLDER_PATH = "M1.9 4.1c0-.6.5-1.1 1.1-1.1h3.1l1.5 1.5H13c.6 0 1.1.5 1.1 1.1v6.3c0 .6-.5 1.1-1.1 1.1H3c-.6 0-1.1-.5-1.1-1.1z";

  const esc = (s) => escapeText(s);
  const csrf = () => document.querySelector('meta[name="csrf-token"]')?.content || "";
  const validColor = (c) => (/^#[0-9A-Fa-f]{6}$/.test(String(c || "")) ? String(c).toUpperCase() : "");

  async function api(path, opts) {
    const headers = { "X-CSRF-Token": csrf(), Accept: "application/json" };
    if (opts && opts.body) headers["Content-Type"] = "application/json";
    const res = await fetch(path, { ...opts, headers, credentials: "same-origin" });
    const text = await res.text();
    let data = {};
    try {
      data = text ? JSON.parse(text) : {};
    } catch (_) {
      data = { error: { message: text || res.statusText } };
    }
    if (!res.ok) {
      const body = data.error || {};
      const err = new Error(
        (typeof body.message === "string" && body.message) || (typeof data.detail === "string" && data.detail) || res.statusText
      );
      err.status = res.status;
      err.type = body.type || "";
      throw err;
    }
    return data;
  }

  function colorFor(project) {
    return validColor(project && (project.color || project.project_color)) || COLORS[0];
  }

  function logoFor(project) {
    return (project && (project.logo_url || project.project_logo_url)) || "";
  }

  /* The project's icon at the caller's size. Extra classes keep each page's
     existing glyph sizing (chat rows use .chat-history-glyph). */
  function icon(project, opts) {
    const size = (opts && opts.size) || 16;
    const cls = ["fp-icon", (opts && opts.className) || ""].join(" ").trim();
    const logo = logoFor(project);
    if (logo) {
      return `<img class="${esc(cls)} fp-logo" src="${esc(logo)}" width="${size}" height="${size}" alt="" loading="lazy" decoding="async">`;
    }
    return (
      `<svg class="${esc(cls)}" viewBox="0 0 16 16" width="${size}" height="${size}" aria-hidden="true" style="--fp-color:${colorFor(project)}">` +
      `<path d="${FOLDER_PATH}" fill="var(--fp-color)" stroke="var(--fp-color)" stroke-width="1.1" stroke-linejoin="round"/>` +
      `<path d="M1.9 6.2h12.2" stroke="color-mix(in srgb, #000 28%, var(--fp-color))" stroke-width="1" fill="none"/></svg>`
    );
  }

  /* —— dialog shell —— */
  let dialog = null;
  let state = null;

  function ensureDialog() {
    if (dialog) return dialog;
    dialog = document.createElement("dialog");
    dialog.className = "modal fp-dialog";
    dialog.setAttribute("aria-labelledby", "fp-title");
    dialog.innerHTML =
      `<div class="fp-shell">` +
      `<header class="fp-head">` +
      `<button type="button" class="fp-back" data-fp-back hidden aria-label="Back"><svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true"><path d="M10 3.5 5.5 8l4.5 4.5" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg></button>` +
      `<div class="fp-titles"><p class="fp-kicker" data-fp-kicker></p><h2 id="fp-title" data-fp-title></h2></div>` +
      `<button type="button" class="fp-close" data-fp-close aria-label="Close"><svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><path d="M3.5 3.5l9 9M12.5 3.5l-9 9" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg></button>` +
      `</header>` +
      `<div class="fp-body" data-fp-body></div>` +
      `</div>`;
    document.body.append(dialog);
    dialog.addEventListener("click", (event) => {
      if (event.target === dialog) close(null);
      if (event.target.closest("[data-fp-close]")) close(null);
      if (event.target.closest("[data-fp-back]") && state && state.back) state.back();
    });
    // Closed from outside (the phone back gesture closes open dialogs).
    dialog.addEventListener("close", () => {
      if (!state) return;
      const done = state;
      state = null;
      document.documentElement.classList.remove("fp-open");
      if (done.resolve) done.resolve(null);
    });
    dialog.addEventListener("cancel", (event) => {
      event.preventDefault();
      if (state && state.back && state.view !== state.root) state.back();
      else close(null);
    });
    return dialog;
  }

  function open(next) {
    ensureDialog();
    if (state && state.resolve) state.resolve(null);
    state = next;
    if (!dialog.open) dialog.showModal();
    document.documentElement.classList.add("fp-open");
  }

  function close(result) {
    const done = state;
    state = null;
    if (dialog && dialog.open) dialog.close();
    document.documentElement.classList.remove("fp-open");
    if (done && done.resolve) done.resolve(result);
  }

  function setHead(kicker, title, canBack) {
    dialog.querySelector("[data-fp-kicker]").textContent = kicker || "";
    dialog.querySelector("[data-fp-title]").textContent = title || "";
    dialog.querySelector("[data-fp-back]").hidden = !canBack;
  }

  function body() {
    return dialog.querySelector("[data-fp-body]");
  }

  function vaultRetry(err, retry) {
    if (err && err.type === "vault_locked" && window.fuseUI && window.fuseUI.vaultPrompt) {
      window.fuseUI.vaultPrompt({
        error: err.message || "Unlock your machines with your account password.",
        message: "SSH machines keep their secrets wrapped with your account password.",
        onUnlocked: retry,
      });
      return true;
    }
    return false;
  }

  async function loadHosts() {
    const data = await api("/hosts/api");
    return Array.isArray(data.hosts) ? data.hosts : [];
  }

  /* —— chooser —— */
  function renderChooser() {
    const s = state;
    setHead("Projects", s.title || "Open a project", false);
    const q = (s.query || "").trim().toLowerCase();
    const groups = [];
    s.hosts.forEach((host) => {
      const items = (host.projects || []).filter((p) => {
        if (Boolean(p.archived) !== Boolean(s.showArchived)) return false;
        if (!q) return true;
        return [p.name, p.remote_path, host.alias].some((v) => String(v || "").toLowerCase().includes(q));
      });
      if (items.length) groups.push({ host, items });
    });
    const archivedCount = s.hosts.reduce((n, h) => n + (h.projects || []).filter((p) => p.archived).length, 0);
    const current = s.current ? `${s.current.host_id}:${s.current.project_id}` : "";
    let list = "";
    if (!s.hosts.length) {
      list =
        `<div class="fp-empty"><p>Projects live on a machine. Connect one first; it takes one command.</p>` +
        `<a class="btn sm" href="/hosts">Connect a machine</a></div>`;
    } else if (!groups.length) {
      list = `<div class="fp-empty"><p>${
        q ? "No projects match." : s.showArchived ? "No archived projects." : "No projects yet. Add a folder on one of your machines."
      }</p></div>`;
    } else {
      list = groups
        .map(({ host, items }) =>
          `<section class="fp-group"><p class="fp-group-head"><span>${esc(host.alias || "machine")}</span>` +
          `${host.online === false ? '<span class="fp-off">offline</span>' : ""}</p><ul class="fp-list">` +
          items
            .map((p) => {
              const key = `${host.id}:${p.id}`;
              const on = key === current;
              return (
                `<li><button type="button" class="fp-row${on ? " is-current" : ""}" data-fp-pick="${esc(key)}"${on ? ' aria-current="true"' : ""}>` +
                icon(p, { size: 22 }) +
                `<span class="fp-row-text"><span class="fp-row-name">${esc(p.name)}${
                  p.permission_mode === "read_only" ? '<span class="fp-badge">Read-only</span>' : ""
                }</span><span class="fp-row-path">${esc(p.remote_path)}</span></span>` +
                `<span class="fp-row-go" aria-hidden="true">${s.showArchived ? "Restore" : on ? "Current" : s.pickLabel || "Open"}</span></button></li>`
              );
            })
            .join("") +
          `</ul></section>`
        )
        .join("");
    }
    body().innerHTML =
      `<div class="fp-toolbar">` +
      `<label class="fp-search"><span class="visually-hidden">Search projects</span>` +
      `<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><circle cx="7" cy="7" r="4.4" fill="none" stroke="currentColor" stroke-width="1.5"/><path d="M10.4 10.4L13.8 13.8" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg>` +
      `<input type="search" data-fp-search placeholder="Search projects" autocomplete="off" value="${esc(s.query || "")}"></label>` +
      (s.hosts.length ? `<button type="button" class="btn sm" data-fp-new>New project</button>` : "") +
      `</div>` +
      `<div class="fp-scroll">${list}</div>` +
      (archivedCount || s.showArchived
        ? `<footer class="fp-foot"><button type="button" class="btn ghost sm" data-fp-archived>${
            s.showArchived ? "Back to projects" : `Archived (${archivedCount})`
          }</button></footer>`
        : "");
    const search = body().querySelector("[data-fp-search]");
    search.addEventListener("input", () => {
      s.query = search.value;
      const pos = search.selectionStart;
      renderChooser();
      const again = body().querySelector("[data-fp-search]");
      again.focus();
      try { again.setSelectionRange(pos, pos); } catch (_) {}
    });
    body().querySelector("[data-fp-new]")?.addEventListener("click", () => {
      s.view = "editor";
      openEditorView({ hosts: s.hosts, hostId: s.hostId, onBack: () => { s.view = "chooser"; renderChooser(); } });
    });
    body().querySelector("[data-fp-archived]")?.addEventListener("click", () => {
      s.showArchived = !s.showArchived;
      renderChooser();
    });
    body().querySelectorAll("[data-fp-pick]").forEach((btn) =>
      btn.addEventListener("click", async () => {
        const [hid, pid] = btn.getAttribute("data-fp-pick").split(":");
        const host = s.hosts.find((h) => String(h.id) === hid);
        let project = host && (host.projects || []).find((p) => String(p.id) === pid);
        if (!project) return;
        if (project.archived) {
          btn.disabled = true;
          try {
            project = await api(`/hosts/${hid}/projects/${pid}`, { method: "PATCH", body: JSON.stringify({ archived: false }) });
          } catch (err) {
            btn.disabled = false;
            return toastError(err);
          }
        }
        close({ ...project, host_id: Number(hid), host_alias: host.alias || "" });
      })
    );
    if (!matchMedia("(max-width: 800px)").matches) search.focus();
  }

  function toastError(err) {
    const message = (err && err.message) || "Something went wrong";
    if (window.fuseUI && window.fuseUI.toast) window.fuseUI.toast(message);
    else alert(message);
  }

  /** Resolves to the picked (or newly created) project with host_id, or null. */
  async function openChooser(opts) {
    const options = opts || {};
    return new Promise((resolve) => {
      open({ resolve, view: "chooser", root: "chooser", hosts: [], query: "", showArchived: false, current: options.current || null, title: options.title, pickLabel: options.pickLabel, hostId: options.hostId });
      state.back = () => {
        state.view = "chooser";
        renderChooser();
      };
      setHead("Projects", options.title || "Open a project", false);
      body().innerHTML = `<div class="fp-loading" aria-busy="true"><span></span><span></span><span></span></div>`;
      const mine = state;
      loadHosts()
        .then((hosts) => {
          if (state !== mine) return;
          mine.hosts = hosts;
          renderChooser();
        })
        .catch((err) => {
          if (state !== mine) return;
          body().innerHTML = `<div class="fp-empty"><p class="callout fault">${esc(err.message || "Could not load projects")}</p></div>`;
        });
    });
  }

  /* —— editor —— */
  function openEditorView(opts) {
    const s = state;
    const project = opts.project || null;
    const hosts = opts.hosts || [];
    const draft = {
      hostId: String(project ? project.host_id : opts.hostId || (hosts[0] && hosts[0].id) || ""),
      name: project ? project.name : "",
      path: project ? project.remote_path : "",
      readOnly: project ? project.permission_mode === "read_only" : false,
      color: project ? colorFor(project) : COLORS[Math.floor(Math.random() * COLORS.length)],
      logoUrl: project ? logoFor(project) : "",
      logoData: "",
      logoCleared: false,
    };
    s.editor = draft;
    s.back = opts.onBack || null;
    const canBack = Boolean(opts.onBack);
    const hostById = (id) => hosts.find((h) => String(h.id) === String(id));
    if (!draft.path && !project) draft.path = (hostById(draft.hostId) || {}).default_cwd || "";

    const render = () => {
      setHead(project ? "Project" : "New project", project ? "Edit project" : "Add project", canBack);
      s.view = "editor";
      s.back = opts.onBack || null;
      const preview = { color: draft.color, logo_url: draft.logoData || (draft.logoCleared ? "" : draft.logoUrl) };
      const hostSelect = project
        ? `<input value="${esc((hostById(draft.hostId) || {}).alias || project.host_alias || "")}" disabled>`
        : `<select name="host" data-fp-host required>${hosts
            .map((h) => `<option value="${esc(h.id)}"${String(h.id) === draft.hostId ? " selected" : ""}>${esc(h.alias || "machine")}</option>`)
            .join("")}</select>`;
      body().innerHTML =
        `<form class="fp-form" data-fp-form novalidate>` +
        `<div class="fp-scroll fp-form-body">` +
        `<div class="fp-preview">${icon(preview, { size: 44, className: "fp-preview-icon" })}<div><p class="fp-preview-name" data-fp-preview-name>${esc(draft.name || "Project name")}</p>` +
        `<p class="fp-preview-path" data-fp-preview-path>${esc(draft.path || "Choose a folder")}</p></div></div>` +
        `<p class="callout fault" data-fp-error hidden></p>` +
        `<label class="fp-field"><span>Name</span><input name="name" required maxlength="80" autocomplete="off" placeholder="api" value="${esc(draft.name)}"></label>` +
        `<label class="fp-field"><span>Machine</span>${hostSelect}</label>` +
        `<label class="fp-field fp-field-wide"><span>Folder</span><span class="fp-path-row">` +
        `<input name="remote_path" required maxlength="400" placeholder="Choose a folder" autocomplete="off" value="${esc(draft.path)}">` +
        `<button type="button" class="btn sm ghost" data-fp-browse>Browse</button></span></label>` +
        `<label class="check fp-field-wide"><input type="checkbox" name="read_only"${draft.readOnly ? " checked" : ""}><span>Read-only (no writes or shell in this project)</span></label>` +
        `<fieldset class="fp-field fp-field-wide fp-colors"><legend>Folder color</legend><div class="fp-swatches">` +
        COLORS.map(
          (c) =>
            `<button type="button" class="fp-swatch${c === draft.color ? " is-on" : ""}" data-fp-color="${c}" style="--fp-color:${c}" aria-label="Color ${c}" aria-pressed="${c === draft.color}"></button>`
        ).join("") +
        `<label class="fp-swatch fp-swatch-custom${COLORS.includes(draft.color) ? "" : " is-on"}" style="--fp-color:${draft.color}" title="Custom color">` +
        `<span class="visually-hidden">Custom color</span><input type="color" data-fp-custom value="${draft.color.toLowerCase()}"></label>` +
        `</div></fieldset>` +
        `<div class="fp-field fp-field-wide fp-logo-field"><span>Logo</span><div class="fp-logo-row">` +
        `<label class="btn sm ghost fp-upload">${preview.logo_url ? "Replace logo" : "Upload logo"}<input type="file" accept="image/png,image/jpeg,image/webp,image/svg+xml,image/gif" data-fp-logo hidden></label>` +
        (preview.logo_url ? `<button type="button" class="btn sm ghost" data-fp-logo-clear>Remove</button>` : "") +
        `<small>Shown instead of the folder everywhere this project appears. Square images work best.</small></div></div>` +
        `</div>` +
        `<footer class="fp-foot fp-actions">` +
        `<button type="button" class="btn sm ghost" data-fp-cancel>${canBack ? "Back" : "Cancel"}</button>` +
        `<button type="submit" class="btn sm" data-fp-submit>${project ? "Save project" : "Add project"}</button>` +
        `</footer></form>`;
      bind();
    };

    const form = () => body().querySelector("[data-fp-form]");
    const showError = (text) => {
      const el = body().querySelector("[data-fp-error]");
      if (!el) return;
      el.hidden = !text;
      el.textContent = text || "";
      if (text) el.scrollIntoView({ block: "nearest" });
    };
    const capture = () => {
      const f = form();
      if (!f) return;
      draft.name = f.elements.name.value;
      draft.path = f.elements.remote_path.value;
      draft.readOnly = f.elements.read_only.checked;
      if (f.elements.host) draft.hostId = f.elements.host.value;
    };
    const repaintPreview = () => {
      const holder = body().querySelector(".fp-preview");
      if (!holder) return;
      const preview = { color: draft.color, logo_url: draft.logoData || (draft.logoCleared ? "" : draft.logoUrl) };
      holder.querySelector(".fp-preview-icon").outerHTML = icon(preview, { size: 44, className: "fp-preview-icon" });
      holder.querySelector("[data-fp-preview-name]").textContent = draft.name || "Project name";
      holder.querySelector("[data-fp-preview-path]").textContent = draft.path || "Choose a folder";
    };

    // The machine the folder default came from. The form's input listener has
    // already captured the new host by the time "change" fires.
    let lastHostId = draft.hostId;
    // Set once a create succeeds, so a retry after a failed logo upload
    // updates that project instead of POSTing a duplicate.
    let createdProject = null;

    function bind() {
      const f = form();
      lastHostId = draft.hostId;
      f.addEventListener("input", () => {
        capture();
        repaintPreview();
      });
      f.elements.host?.addEventListener("change", () => {
        const prev = hostById(lastHostId);
        capture();
        lastHostId = draft.hostId;
        const next = hostById(draft.hostId);
        if (!f.elements.remote_path.value || (prev && f.elements.remote_path.value === prev.default_cwd)) {
          f.elements.remote_path.value = (next && next.default_cwd) || "";
          capture();
          repaintPreview();
        }
      });
      const custom = f.querySelector("[data-fp-custom]");
      // Swatches update in place so keyboard focus stays on the one chosen.
      const pick = (color, fromCustom) => {
        draft.color = color;
        f.querySelectorAll(".fp-swatch").forEach((el) => {
          const preset = el.getAttribute("data-fp-color");
          const on = preset ? !fromCustom && preset === color : fromCustom || !COLORS.includes(color);
          el.classList.toggle("is-on", on);
          if (preset) el.setAttribute("aria-pressed", String(on));
        });
        if (fromCustom) custom.parentElement.style.setProperty("--fp-color", color);
        repaintPreview();
      };
      f.querySelectorAll("[data-fp-color]").forEach((btn) =>
        btn.addEventListener("click", () => pick(btn.getAttribute("data-fp-color"), false))
      );
      custom.addEventListener("input", () => {
        const color = validColor(custom.value);
        if (color) pick(color, true);
      });
      f.querySelector("[data-fp-logo]").addEventListener("change", async (event) => {
        const file = event.target.files && event.target.files[0];
        if (!file) return;
        capture();
        try {
          draft.logoData = await rasterLogo(file);
          draft.logoCleared = false;
          render();
        } catch (err) {
          showError(err.message || "Could not read that image");
        }
      });
      f.querySelector("[data-fp-logo-clear]")?.addEventListener("click", () => {
        capture();
        draft.logoData = "";
        draft.logoCleared = true;
        render();
      });
      f.querySelector("[data-fp-browse]").addEventListener("click", () => {
        capture();
        if (!draft.hostId) return showError("Choose a machine first.");
        openBrowseView(draft.hostId, draft.path, (path) => {
          draft.path = path;
          const base = String(path || "").replace(/[\\/]+$/, "").split(/[\\/]/).pop();
          if (!draft.name.trim() && base && base !== "/") draft.name = base;
          render();
        }, render);
      });
      f.querySelector("[data-fp-cancel]").addEventListener("click", () => {
        if (opts.onBack) opts.onBack();
        else close(null);
      });
      f.addEventListener("submit", async (event) => {
        event.preventDefault();
        capture();
        showError("");
        if (!draft.name.trim() || !draft.path.trim() || !draft.hostId) {
          return showError("Name, machine and folder are required.");
        }
        const submit = f.querySelector("[data-fp-submit]");
        submit.disabled = true;
        const body = {
          name: draft.name.trim(),
          remote_path: draft.path.trim(),
          permission_mode: draft.readOnly ? "read_only" : "project",
          color: draft.color,
        };
        try {
          const existing = project || createdProject;
          let saved = existing
            ? await api(`/hosts/${draft.hostId}/projects/${existing.id}`, { method: "PATCH", body: JSON.stringify(body) })
            : await api(`/hosts/${draft.hostId}/projects`, { method: "POST", body: JSON.stringify(body) });
          if (!project) createdProject = saved;
          if (draft.logoData) {
            saved = await api(`/hosts/${draft.hostId}/projects/${saved.id}/logo`, { method: "PUT", body: JSON.stringify({ data: draft.logoData }) });
          } else if (draft.logoCleared && project && project.logo_url) {
            saved = await api(`/hosts/${draft.hostId}/projects/${saved.id}/logo`, { method: "DELETE" });
          }
          const host = hostById(draft.hostId);
          close({ ...saved, host_id: Number(draft.hostId), host_alias: (host && host.alias) || (project && project.host_alias) || "", created: !project });
        } catch (err) {
          submit.disabled = false;
          showError(err.message || "Could not save the project");
        }
      });
      if (!matchMedia("(max-width: 800px)").matches) f.elements.name.focus({ preventScroll: true });
    }
    render();
  }

  /** Add (no project) or edit a project. Resolves to the saved project or null. */
  async function openEditor(opts) {
    const options = opts || {};
    return new Promise((resolve) => {
      open({ resolve, view: "editor", root: "editor" });
      const mine = state;
      const start = (hosts) => {
        if (state !== mine) return;
        const project = options.project ? { ...options.project, host_id: options.project.host_id || options.hostId } : null;
        if (!project && !hosts.length) {
          setHead("New project", "Add project", false);
          body().innerHTML =
            `<div class="fp-empty"><p>Projects live on a machine. Connect one first; it takes one command.</p>` +
            `<a class="btn sm" href="/hosts">Connect a machine</a></div>`;
          return;
        }
        openEditorView({ hosts, hostId: options.hostId, project });
      };
      if (Array.isArray(options.hosts) && options.hosts.length) start(options.hosts);
      else {
        setHead("Project", options.project ? "Edit project" : "Add project", false);
        body().innerHTML = `<div class="fp-loading" aria-busy="true"><span></span><span></span><span></span></div>`;
        loadHosts().then(start).catch((err) => {
          if (state !== mine) return;
          body().innerHTML = `<div class="fp-empty"><p class="callout fault">${esc(err.message || "Could not load machines")}</p></div>`;
        });
      }
    });
  }

  /* —— folder browser (inside the same dialog) —— */
  // Windows connectors list drive paths (C:\Users\me), so join with backslashes there.
  function isWinPath(p) {
    const v = String(p || "");
    return /^[A-Za-z]:[\\/]/.test(v) || v.startsWith("\\\\") || (v.includes("\\") && !v.startsWith("/"));
  }

  function openBrowseView(hostId, start, onUse, onBack) {
    const s = state;
    const b = { path: start || "", parent: "", home: "", entries: [], err: "", loading: true };
    s.back = onBack;
    s.view = "browse";
    const crumbs = (path) => {
      const raw = String(path || "");
      if (isWinPath(raw)) {
        const m = raw.match(/^([A-Za-z]:\\)(.*)$/);
        if (!m) return `<nav class="fp-crumbs" aria-label="Folder path"><button type="button" disabled>${esc(raw)}</button></nav>`;
        let acc = m[1];
        return (
          `<nav class="fp-crumbs" aria-label="Folder path"><button type="button" data-fp-go="${esc(m[1])}">${esc(m[1])}</button>` +
          m[2].split("\\").filter(Boolean).map((part) => {
            acc = acc.endsWith("\\") ? acc + part : acc + "\\" + part;
            return `<button type="button" data-fp-go="${esc(acc)}">${esc(part)}</button>`;
          }).join("") +
          `</nav>`
        );
      }
      const parts = raw.split("/").filter(Boolean);
      let acc = "";
      return (
        `<nav class="fp-crumbs" aria-label="Folder path"><button type="button" data-fp-go="/">/</button>` +
        parts.map((part) => {
          acc += "/" + part;
          return `<button type="button" data-fp-go="${esc(acc)}">${esc(part)}</button>`;
        }).join("") +
        `</nav>`
      );
    };
    const render = () => {
      setHead("Folders", "Choose a folder", true);
      const dirs = (b.entries || []).filter((e) => e && (e.dir || e.kind === "dir" || e.type === "dir"));
      body().innerHTML =
        `<div class="fp-toolbar fp-browse-bar">${crumbs(b.path)}` +
        (b.parent ? `<button type="button" class="btn sm ghost" data-fp-up>Up</button>` : "") +
        (b.home && b.home !== b.path ? `<button type="button" class="btn sm ghost" data-fp-home>Home</button>` : "") +
        `</div>` +
        (b.err ? `<p class="callout fault fp-inline-error">${esc(b.err)}</p>` : "") +
        `<div class="fp-scroll"><ul class="fp-dirs">` +
        (b.loading
          ? `<li class="fp-muted">Opening…</li>`
          : dirs.length
            ? dirs.map((d) => `<li><button type="button" data-fp-into="${esc(d.name)}">${icon({ color: "#8F978F" }, { size: 15 })}<span>${esc(d.name)}</span></button></li>`).join("")
            : `<li class="fp-muted">No folders here</li>`) +
        `</ul></div>` +
        `<footer class="fp-foot fp-actions"><span class="fp-chosen">${esc(b.path || "~")}</span>` +
        `<button type="button" class="btn sm" data-fp-use${b.loading || b.err ? " disabled" : ""}>Use this folder</button></footer>`;
      body().querySelectorAll("[data-fp-go]").forEach((el) => el.addEventListener("click", () => load(el.getAttribute("data-fp-go"))));
      body().querySelectorAll("[data-fp-into]").forEach((el) =>
        el.addEventListener("click", () => {
          const name = el.getAttribute("data-fp-into");
          if (isWinPath(b.path)) load(String(b.path).replace(/\\+$/, "") + "\\" + name);
          else {
            const base = String(b.path || "").replace(/\/+$/, "");
            load((base && base !== "/" ? base : "") + "/" + name);
          }
        })
      );
      body().querySelector("[data-fp-up]")?.addEventListener("click", () => load(b.parent));
      body().querySelector("[data-fp-home]")?.addEventListener("click", () => load(b.home));
      body().querySelector("[data-fp-use]").addEventListener("click", () => onUse(b.path));
    };
    const load = async (path) => {
      b.loading = true;
      b.err = "";
      render();
      try {
        const data = await api(`/hosts/${hostId}/browse`, { method: "POST", body: JSON.stringify({ path: path || "" }) });
        if (state !== s || s.view !== "browse") return;
        b.path = data.path || path || "";
        b.parent = data.parent || "";
        b.home = data.home || "";
        b.entries = data.entries || [];
      } catch (err) {
        if (vaultRetry(err, () => load(path))) b.err = "Unlock your machines to browse.";
        else b.err = err.message || "Could not list that folder";
      }
      b.loading = false;
      if (state === s && s.view === "browse") render();
    };
    load(start || "");
  }

  /* Logos are stored as a small square PNG made here: any format the browser
     can draw (SVG included) is rasterized, so the server never serves SVG. */
  function rasterLogo(file) {
    return new Promise((resolve, reject) => {
      if (!/^image\//.test(file.type)) return reject(new Error("Choose an image file."));
      if (file.size > 8 * 1024 * 1024) return reject(new Error("That image is over 8 MB."));
      // The page CSP allows data: images but not blob: ones.
      const reader = new FileReader();
      reader.onerror = () => reject(new Error("Could not read that image."));
      reader.onload = () => {
        const img = new Image();
        img.onload = () => {
          try {
            const canvas = document.createElement("canvas");
            canvas.width = LOGO_PX;
            canvas.height = LOGO_PX;
            const ctx = canvas.getContext("2d");
            const w = img.naturalWidth || LOGO_PX;
            const h = img.naturalHeight || LOGO_PX;
            const scale = Math.min(LOGO_PX / w, LOGO_PX / h);
            const dw = w * scale;
            const dh = h * scale;
            ctx.drawImage(img, (LOGO_PX - dw) / 2, (LOGO_PX - dh) / 2, dw, dh);
            let data = canvas.toDataURL("image/png");
            if (data.length * 0.75 > LOGO_MAX) data = canvas.toDataURL("image/webp", 0.85);
            if (data.length * 0.75 > LOGO_MAX) throw new Error("That image is too detailed. Try a simpler one.");
            resolve(data);
          } catch (err) {
            reject(err);
          }
        };
        img.onerror = () => reject(new Error("Could not read that image."));
        img.src = String(reader.result || "");
      };
      reader.readAsDataURL(file);
    });
  }

  window.FuseProjects = { COLORS, icon, colorFor, logoFor, openChooser, openEditor, api, isOpen: () => Boolean(dialog && dialog.open), close: () => close(null) };
})();
