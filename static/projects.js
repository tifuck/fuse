/* Projects page: every project across machines, with the same dialog chat
   and Machines use for adding and editing (projects-ui.js). */
(() => {
  const root = document.querySelector("[data-projects-page]");
  if (!root || !window.FuseProjects) return;
  const P = window.FuseProjects;
  const grid = root.querySelector("[data-proj-grid]");
  const search = root.querySelector("[data-proj-search]");
  const machine = root.querySelector("[data-proj-machine]");
  const errorEl = root.querySelector("[data-proj-error]");
  let bundle = { projects: [], hosts: [] };
  try {
    bundle = JSON.parse(document.getElementById("projects-config").textContent || "{}");
  } catch (_) {}
  let tab = "active";
  let menu = null;

  const esc = (s) =>
    String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

  function showError(text) {
    errorEl.hidden = !text;
    errorEl.textContent = text || "";
  }

  function ago(iso) {
    const t = Date.parse(iso || "");
    if (!Number.isFinite(t)) return "";
    const s = Math.max(0, (Date.now() - t) / 1000);
    if (s < 90) return "just now";
    if (s < 3600) return Math.round(s / 60) + "m ago";
    if (s < 86400) return Math.round(s / 3600) + "h ago";
    if (s < 86400 * 45) return Math.round(s / 86400) + "d ago";
    return new Date(t).toLocaleDateString();
  }

  function hostOf(p) {
    return bundle.hosts.find((h) => Number(h.id) === Number(p.host_id)) || null;
  }

  function paintMachines() {
    const current = machine.value;
    machine.innerHTML =
      `<option value="">All machines</option>` +
      bundle.hosts.map((h) => `<option value="${esc(h.id)}">${esc(h.alias)}</option>`).join("");
    machine.value = bundle.hosts.some((h) => String(h.id) === current) ? current : "";
    machine.hidden = bundle.hosts.length < 2;
  }

  function card(p) {
    const key = `${p.host_id}:${p.id}`;
    const host = hostOf(p);
    const sessions = Number(p.sessions || 0);
    const when = ago(p.last_active_at || p.updated_at);
    const meta = [
      esc(p.host_alias || (host && host.alias) || "machine"),
      sessions ? `${sessions} chat${sessions === 1 ? "" : "s"}` : "No chats yet",
      when ? `Active ${esc(when)}` : "",
    ].filter(Boolean);
    const badges =
      (p.permission_mode === "read_only" ? '<span class="badge off">Read-only</span>' : "") +
      (p.running ? '<span class="badge cool">Working</span>' : "") +
      (host && host.online === false ? '<span class="badge off">Offline</span>' : "");
    return (
      `<li class="panel proj-card${p.archived ? " is-archived" : ""}" data-proj="${esc(key)}">` +
      `<button type="button" class="proj-open" data-proj-open="${esc(key)}" title="${p.thread_id ? "Open the latest chat" : "Start a chat"} in ${esc(p.name)}">` +
      P.icon(p, { size: 40, className: "proj-card-icon" }) +
      `<span class="proj-card-text"><span class="proj-card-name">${esc(p.name)}</span>` +
      `<span class="proj-card-path">${esc(p.remote_path)}</span>` +
      `<span class="proj-card-meta">${meta.map((m) => `<span>${m}</span>`).join("")}</span></span></button>` +
      `<div class="proj-card-foot"><span class="proj-badges">${badges}</span>` +
      (p.archived
        ? `<button type="button" class="btn ghost sm" data-proj-restore="${esc(key)}">Unarchive</button>`
        : `<button type="button" class="btn ghost sm" data-proj-chat="${esc(key)}">New chat</button>`) +
      `<button type="button" class="proj-more" data-proj-more="${esc(key)}" aria-label="Actions for ${esc(p.name)}" aria-haspopup="menu">⋯</button>` +
      `</div></li>`
    );
  }

  function paint() {
    closeMenu();
    const q = (search.value || "").trim().toLowerCase();
    const hostFilter = machine.value;
    const counts = { active: 0, archived: 0 };
    bundle.projects.forEach((p) => { counts[p.archived ? "archived" : "active"] += 1; });
    root.querySelectorAll("[data-proj-count]").forEach((el) => {
      const n = counts[el.getAttribute("data-proj-count")];
      el.textContent = n ? String(n) : "";
    });
    root.querySelectorAll("[data-proj-tab]").forEach((el) => {
      const on = el.getAttribute("data-proj-tab") === tab;
      el.setAttribute("aria-selected", String(on));
      el.classList.toggle("is-on", on);
    });
    const items = bundle.projects
      .filter((p) => Boolean(p.archived) === (tab === "archived"))
      .filter((p) => !hostFilter || String(p.host_id) === hostFilter)
      .filter((p) => !q || [p.name, p.remote_path, p.host_alias].some((v) => String(v || "").toLowerCase().includes(q)))
      .sort((a, b) => String(b.last_active_at || b.updated_at || "").localeCompare(String(a.last_active_at || a.updated_at || "")));
    if (items.length) {
      grid.innerHTML = items.map(card).join("");
      return;
    }
    let empty;
    if (!bundle.hosts.length) {
      empty = `<p>Projects live on a machine. Connect one first; it takes one command.</p><a class="btn sm" href="/hosts">Connect a machine</a>`;
    } else if (q || hostFilter) {
      empty = `<p>No projects match.</p>`;
    } else if (tab === "archived") {
      empty = `<p>Nothing archived. Archived projects keep their chats until you restore them.</p>`;
    } else {
      empty = `<p>No projects yet. A project is a folder on one of your machines that chats can work in.</p><button type="button" class="btn sm" data-proj-add>New project</button>`;
    }
    grid.innerHTML = `<li class="proj-empty">${empty}</li>`;
  }

  async function reload() {
    try {
      bundle = await P.api("/projects/api");
      showError("");
    } catch (err) {
      showError(err.message || "Could not load projects");
    }
    paintMachines();
    paint();
  }

  function find(key) {
    const [hid, pid] = String(key || "").split(":");
    return bundle.projects.find((p) => String(p.host_id) === hid && String(p.id) === pid);
  }

  async function startChat(p) {
    const thread = await P.api("/chat/threads", {
      method: "POST",
      body: JSON.stringify({
        kind: "project",
        host_id: Number(p.host_id),
        project_id: Number(p.id),
        title: "",
        command_id: crypto.randomUUID ? crypto.randomUUID() : "p" + Date.now() + Math.random().toString(36).slice(2),
      }),
    });
    if (thread && thread.id) location.href = "/chat?t=" + encodeURIComponent(thread.id);
  }

  async function edit(p) {
    const saved = await P.openEditor({ hostId: p.host_id, project: { ...p } });
    if (saved) await reload();
  }

  async function setArchived(p, archived) {
    await P.api(`/hosts/${p.host_id}/projects/${p.id}`, { method: "PATCH", body: JSON.stringify({ archived }) });
    await reload();
  }

  async function remove(p) {
    const n = Number(p.sessions || 0);
    const what = n ? ` and its ${n === 1 ? "chat" : n + " chats"}` : "";
    const ok = await window.fuseConfirm(
      `Delete project "${p.name}"${what}? The folder on ${p.host_alias || "the machine"} is not touched. This cannot be undone.`,
      { confirmLabel: "Delete", danger: true }
    );
    if (!ok) return;
    await P.api(`/hosts/${p.host_id}/projects/${p.id}?sessions=delete`, { method: "DELETE" });
    await reload();
  }

  function closeMenu() {
    if (!menu) return;
    menu.anchor.setAttribute("aria-expanded", "false");
    menu.el.remove();
    menu = null;
  }

  function openMenu(anchor, p) {
    closeMenu();
    const el = document.createElement("div");
    el.className = "proj-menu";
    el.setAttribute("role", "menu");
    const items = [
      ["Edit project", () => edit(p)],
      p.archived ? ["Unarchive", () => setArchived(p, false)] : ["Archive", () => setArchived(p, true)],
      ["Delete project", () => remove(p), true],
    ];
    items.forEach(([label, run, danger]) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.setAttribute("role", "menuitem");
      btn.textContent = label;
      if (danger) btn.className = "is-danger";
      btn.addEventListener("click", () => {
        closeMenu();
        Promise.resolve(run()).catch((err) => showError(err.message || `${label} failed`));
      });
      el.append(btn);
    });
    document.body.append(el);
    const r = anchor.getBoundingClientRect();
    const w = el.offsetWidth;
    const h = el.offsetHeight;
    let left = Math.min(window.innerWidth - w - 8, Math.max(8, r.right - w));
    let top = r.bottom + 4;
    if (top + h > window.innerHeight - 8) top = Math.max(8, r.top - h - 4);
    el.style.left = left + "px";
    el.style.top = top + "px";
    anchor.setAttribute("aria-expanded", "true");
    menu = { el, anchor };
    el.querySelector("button").focus();
  }

  document.addEventListener("click", (event) => {
    if (menu && !menu.el.contains(event.target) && !event.target.closest("[data-proj-more]")) closeMenu();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && menu) {
      const anchor = menu.anchor;
      closeMenu();
      anchor.focus();
    }
  });
  window.addEventListener("resize", closeMenu);

  document.addEventListener("click", async (event) => {
    const add = event.target.closest("[data-proj-add]");
    const open = event.target.closest("[data-proj-open]");
    const chat = event.target.closest("[data-proj-chat]");
    const restore = event.target.closest("[data-proj-restore]");
    const more = event.target.closest("[data-proj-more]");
    const tabBtn = event.target.closest("[data-proj-tab]");
    try {
      if (add) {
        const saved = await P.openEditor({});
        if (saved) {
          tab = "active";
          await reload();
        }
      } else if (tabBtn) {
        tab = tabBtn.getAttribute("data-proj-tab") === "archived" ? "archived" : "active";
        paint();
      } else if (more) {
        const p = find(more.getAttribute("data-proj-more"));
        if (p) {
          if (menu && menu.anchor === more) closeMenu();
          else openMenu(more, p);
        }
      } else if (restore) {
        const p = find(restore.getAttribute("data-proj-restore"));
        if (p) await setArchived(p, false);
      } else if (chat) {
        const p = find(chat.getAttribute("data-proj-chat"));
        if (p) await startChat(p);
      } else if (open) {
        const p = find(open.getAttribute("data-proj-open"));
        if (!p) return;
        if (p.thread_id) location.href = "/chat?t=" + encodeURIComponent(p.thread_id);
        else await startChat(p);
      }
    } catch (err) {
      showError(err.message || "Request failed");
    }
  });
  search.addEventListener("input", paint);
  machine.addEventListener("change", paint);

  paintMachines();
  paint();
})();
