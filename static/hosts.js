(() => {
  const root = document.querySelector("[data-hosts]");
  if (!root) return;
  const cfgEl = document.getElementById("hosts-config");
  let bundle = { hosts: [], settings: {}, vault: "locked" };
  try {
    bundle = JSON.parse(cfgEl ? cfgEl.textContent : "{}") || bundle;
  } catch (_) {}

  function csrf() {
    const el = document.querySelector('meta[name="csrf-token"]');
    return el ? el.getAttribute("content") : "";
  }

  async function api(path, opts) {
    const headers = { "X-CSRF-Token": csrf(), Accept: "application/json" };
    if (opts && opts.body && !(opts.body instanceof FormData)) {
      headers["Content-Type"] = "application/json";
    }
    const res = await fetch(path, { ...opts, headers, credentials: "same-origin" });
    const text = await res.text();
    let data = {};
    try {
      data = text ? JSON.parse(text) : {};
    } catch (_) {
      data = { error: { message: text || res.statusText } };
    }
    if (!res.ok) {
      const errBody = data.error || {};
      const msg =
        (typeof errBody.message === "string" && errBody.message) ||
        (typeof data.detail === "string" && data.detail) ||
        res.statusText;
      const err = new Error(msg);
      err.status = res.status;
      err.type = errBody.type || "";
      throw err;
    }
    return data;
  }

  const list = root.querySelector("[data-host-list]");
  const detail = root.querySelector("[data-host-detail]");
  const layout = root.querySelector("[data-hosts-layout]");
  const wizard = root.querySelector("[data-wizard]");
  const empty = root.querySelector("[data-empty]");
  const wizardError = root.querySelector("[data-wizard-error]");
  const connector = root.querySelector("[data-connector]");
  const connectorError = root.querySelector("[data-connector-error]");
  const installLine = root.querySelector("[data-install-line]");
  const installWindows = root.querySelector("[data-install-windows]");
  const downloadWindows = root.querySelector("[data-download-windows]");
  const connectorWait = root.querySelector("[data-connector-wait]");
  const pageError = root.querySelector("[data-page-error]");

  let wizardHostId = null;
  let wizardProbe = null;
  let wizardStep = 1;
  let busy = false;
  let activePairing = null;
  let hostsSocket = null;
  let hostsEpoch = 0;
  let testingId = null;
  const browseState = {
    open: false,
    hostId: null,
    target: "project",
    path: "",
    parent: "",
    home: "",
    entries: [],
    loading: false,
    err: "",
  };

  function hasSshHosts() {
    return (bundle.hosts || []).some((h) => h.auth_type !== "agent" && h.kind !== "agent");
  }

  function vaultGate() {
    if (!window.fuseUI) return;
    window.fuseUI.vaultGate({
      uid: bundle.uid,
      vault: bundle.vault,
      hasSsh: hasSshHosts(),
      message: "Unlock machines to use SSH. Your account password unwraps those secrets here — connector machines never need it.",
      onUnlocked: () => {
        setVault("unlocked");
        reload();
      },
    });
  }

  function setVault(state) {
    bundle.vault = state;
    root.setAttribute("data-vault", state);
  }

  function showErr(el, msg) {
    if (!el) return;
    if (!msg) {
      el.hidden = true;
      el.textContent = "";
      return;
    }
    el.hidden = false;
    el.textContent = msg;
  }

  // One escaper for every classic script: escapeText in app.js (loaded first).
  function esc(value) {
    return escapeText(value);
  }

  function locked() {
    return bundle.vault !== "unlocked";
  }

  function ask(message, opts) {
    if (typeof window.fuseConfirm === "function") return window.fuseConfirm(message, opts);
    return Promise.resolve(window.confirm(message));
  }

  function fmtWhen(raw) {
    if (!raw) return "";
    try {
      const d = new Date(raw);
      if (Number.isNaN(d.getTime())) return raw;
      return d.toISOString().replace("T", " ").replace(/\.\d+Z$/, " UTC");
    } catch (_) {
      return raw;
    }
  }

  function relWhen(raw) {
    if (!raw) return "";
    const t = Date.parse(raw);
    if (Number.isNaN(t)) return "";
    const s = Math.max(0, (Date.now() - t) / 1000);
    if (s < 60) return "just now";
    if (s < 3600) return Math.floor(s / 60) + "m ago";
    if (s < 86400) return Math.floor(s / 3600) + "h ago";
    if (s < 7 * 86400) return Math.floor(s / 86400) + "d ago";
    return new Date(t).toISOString().slice(0, 10);
  }

  const ICON = {
    chev:
      '<svg class="hosts-chev" viewBox="0 0 16 16" width="13" height="13" aria-hidden="true"><path d="M6 3.5L10.5 8 6 12.5" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    edit:
      '<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><path d="M11.2 2l2.8 2.8M2.5 13.5l.9-3.2 7.3-7.3 2.3 2.3-7.3 7.3-3.2.9z" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    x:
      '<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><path d="M3.5 3.5l9 9M12.5 3.5l-9 9" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg>',
  };

  // The machine whose detail pane is showing. Defaults to the first machine,
  // so the page always reads as list + detail.
  let selectedId = null;

  function currentHost() {
    const hosts = bundle.hosts || [];
    return hosts.find((h) => Number(h.id) === Number(selectedId)) || hosts[0] || null;
  }

  function isAgent(host) {
    return host.auth_type === "agent" || host.kind === "agent";
  }

  function hostStatus(host) {
    if (isAgent(host)) return host.online ? "ok" : "idle";
    if (!host.pinned) return "warn";
    if (host.last_error) return "err";
    if (host.last_ok_at) return "ok";
    return "idle";
  }

  function statusTitle(host) {
    if (isAgent(host)) return host.online ? "Connector online" : "Connector offline";
    if (!host.pinned) return "Not trusted yet";
    if (host.last_error) return "Last check failed";
    if (host.last_ok_at) return "Reachable";
    return "Trusted";
  }

  function projectRow(host, p) {
    const ro = p.permission_mode === "read_only" ? '<span class="badge">Read-only</span>' : "";
    const archived = p.archived ? '<span class="badge">Archived</span>' : "";
    const icon = window.FuseProjects ? window.FuseProjects.icon(p, { size: 18, className: "hosts-proj-icon" }) : "";
    return (
      `<li class="hosts-proj${p.archived ? " is-archived" : ""}" data-project-row="${host.id}:${p.id}">` +
      `<button type="button" class="hosts-proj-open" data-open-project="${host.id}:${p.id}" title="Start a new chat for ${esc(p.name)}">` +
      `<span class="hosts-proj-name">${icon}<span class="hosts-proj-label">${esc(p.name)}</span>${ro}${archived}</span>` +
      `<span class="hosts-proj-path">${esc(p.remote_path)}</span>` +
      `<span class="hosts-proj-go">New chat →</span>` +
      `</button>` +
      `<button type="button" class="hosts-ibtn" data-edit-project="${host.id}:${p.id}" title="Edit project" aria-label="Edit project ${esc(p.name)}">${ICON.edit}</button>` +
      `<button type="button" class="hosts-ibtn is-danger" data-del-project="${host.id}:${p.id}" title="Remove project" aria-label="Remove project ${esc(p.name)}">${ICON.x}</button>` +
      `</li>`
    );
  }

  function hostFacts(host) {
    const agent = isAgent(host);
    const facts = [];
    facts.push({
      label: "Address",
      value: agent ? `${host.username}@${host.hostname}` : `${host.username}@${host.hostname}:${host.port}`,
      mono: true,
      trunc: true,
    });
    facts.push({ label: "Access", value: agent ? "connector" : `ssh · ${host.auth_type}` });
    if (host.default_cwd) facts.push({ label: "Default path", value: host.default_cwd, mono: true, trunc: true });
    if (agent && host.agent_os) {
      const osn = String(host.agent_os).toLowerCase().startsWith("win") ? "Windows" : host.agent_os;
      facts.push({ label: "OS", value: osn });
    }
    if (agent && host.agent_version) facts.push({ label: "Agent", value: "v" + host.agent_version });
    if (!agent && host.fingerprint) {
      facts.push({
        label: "Fingerprint",
        value: `${host.key_algo || ""} ${host.fingerprint}`.trim(),
        mono: true,
        trunc: true,
      });
    }
    const stamp = host.last_ok_at || host.last_seen_at;
    if (stamp) {
      facts.push({
        label: "Last check",
        value: `${host.last_ok_at ? "ok" : "seen"} ${relWhen(stamp)}`,
        title: fmtWhen(stamp),
      });
    }
    const cells = facts
      .map(
        (f) =>
          `<div><dt>${f.label}</dt><dd class="${f.mono ? "mono" : ""}${f.trunc ? " is-trunc" : ""}"${f.title ? ` title="${esc(f.title)}"` : ""}>${esc(f.value)}</dd></div>`
      )
      .join("");
    return `<dl class="hosts-facts">${cells}</dl>`;
  }

  function statusLine(host) {
    const stamp = host.last_ok_at || host.last_seen_at;
    const when = stamp ? relWhen(stamp) : "";
    if (isAgent(host)) {
      if (host.online) return "Online" + (when ? ` · seen ${when}` : "");
      return when ? `Offline · last seen ${when}` : "Offline · never connected";
    }
    if (!host.pinned) return "Not trusted yet";
    if (host.last_error) return "Last check failed" + (when ? ` · ok ${when}` : "");
    return host.last_ok_at ? `Reachable · checked ${when}` : "Trusted · not checked yet";
  }

  // The selected machine: who it is and whether it is up, what it holds
  // (projects), how Fuse reaches it (facts), and what you can do.
  function hostPane(host) {
    const agent = isAgent(host);
    const projects = host.projects || [];
    const st = hostStatus(host);
    const testing = Number(testingId) === Number(host.id);
    const parts = [];

    parts.push(
      `<header class="hosts-pane-head">` +
      `<div class="hosts-pane-id">` +
      `<p class="hosts-pane-kicker">${agent ? "Connector machine" : "SSH machine"}</p>` +
      `<h2>${esc(host.alias)}</h2>` +
      `<p class="hosts-pane-status is-${st}"><span class="hosts-dot is-${st}" aria-hidden="true"></span>${esc(statusLine(host))}</p>` +
      `</div>` +
      `<div class="hosts-drawer-actions">` +
      `<button type="button" class="btn sm ghost" data-test="${host.id}"${testing ? " disabled" : ""}>${testing ? "Testing…" : "Test"}</button>` +
      (agent ? "" : host.pinned ? `<button type="button" class="btn sm ghost" data-probe="${host.id}">Re-trust</button>` : "") +
      `<button type="button" class="btn sm ghost" data-edit-host="${host.id}">Edit</button>` +
      `<button type="button" class="btn sm danger" data-del-host="${host.id}">Remove</button>` +
      `</div>` +
      `</header>`
    );

    if (!agent && !host.pinned) {
      parts.push(
        `<div class="hosts-warn">` +
        `<p>Not trusted yet. Compare this machine's fingerprint before agents can connect.</p>` +
        `<button type="button" class="btn sm" data-probe="${host.id}">Trust machine</button>` +
        `</div>`
      );
    }
    if (host.last_error) {
      parts.push(`<p class="callout fault">${esc(host.last_error)}</p>`);
    }

    parts.push(hostFacts(host));

    parts.push(
      `<section class="hosts-projects" aria-label="Projects on ${esc(host.alias)}">` +
      `<div class="hosts-sub"><h3>Projects <span>${projects.length}</span></h3>` +
      `<button type="button" class="btn sm ghost" data-add-project="${host.id}">+ Add project</button>` +
      `</div>` +
      (projects.length
        ? `<ul class="hosts-proj-list">${projects.map((p) => projectRow(host, p)).join("")}</ul>`
        : `<p class="hosts-empty-proj">No projects yet. Add a folder on this machine to start working on it in chat.</p>`) +
      `</section>`
    );
    return `<article class="hosts-pane hosts-drawer" data-detail-host-id="${host.id}">${parts.join("")}</article>`;
  }

  function hostRow(host) {
    const agent = isAgent(host);
    const st = hostStatus(host);
    const current = currentHost();
    const on = Boolean(current && Number(current.id) === Number(host.id));
    const n = (host.projects || []).length;
    const addr = agent ? `${host.username}@${host.hostname}` : `${host.username}@${host.hostname}:${host.port}`;
    const stamp = host.last_ok_at || host.last_seen_at;
    return (
      `<article class="hosts-row${on ? " is-on" : ""}" data-host-id="${host.id}" data-kind="${agent ? "agent" : "ssh"}">` +
      `<button type="button" class="hosts-row-head" data-toggle-card="${host.id}" aria-pressed="${on}" title="${esc(statusTitle(host))}">` +
      `<span class="hosts-dot is-${st}" aria-hidden="true"></span>` +
      `<span class="hosts-row-name"><b>${esc(host.alias)}</b></span>` +
      `<span class="hosts-row-meta"><span class="hosts-chip">${agent ? "connector" : "ssh"}</span><span class="hosts-row-addr">${esc(addr)}</span></span>` +
      `<span class="hosts-row-side">` +
      `<span>${n} project${n === 1 ? "" : "s"}</span>` +
      (stamp ? `<span class="hosts-when">${esc(relWhen(stamp))}</span>` : "") +
      `</span>` +
      `</button>` +
      `</article>`
    );
  }

  const browseModal = root.querySelector("[data-browse-modal]");
  const browseBody = browseModal ? browseModal.querySelector("[data-browse-body]") : null;

  function isWinPath(p) {
    const s = String(p || "");
    return /^[A-Za-z]:[\\/]/.test(s) || s.startsWith("\\\\") || (s.includes("\\") && !s.startsWith("/"));
  }

  function crumbsHtml(path) {
    const raw = path || "/";
    if (isWinPath(raw)) {
      const m = String(raw).match(/^([A-Za-z]:\\)(.*)$/);
      const bits = [];
      if (m) {
        bits.push(
          `<button type="button" class="hosts-crumb${m[2] ? "" : " is-here"}" data-browse-go="${esc(m[1])}">${esc(m[1])}</button>`
        );
        let acc = m[1];
        const parts = m[2].split("\\").filter(Boolean);
        parts.forEach((part, i) => {
          acc = acc.endsWith("\\") ? acc + part : acc + "\\" + part;
          bits.push(
            `<button type="button" class="hosts-crumb${i === parts.length - 1 ? " is-here" : ""}" data-browse-go="${esc(acc)}">${esc(part)}</button>`
          );
        });
      } else {
        bits.push(`<span class="hosts-crumb is-here">${esc(raw)}</span>`);
      }
      return `<nav class="hosts-crumbs" aria-label="Folder path">${bits.join("")}</nav>`;
    }
    const parts = raw === "/" ? [] : String(raw).replace(/\/+$/, "").split("/").filter(Boolean);
    let acc = "";
    const bits = [
      `<button type="button" class="hosts-crumb${parts.length ? "" : " is-here"}" data-browse-go="/">/</button>`,
    ];
    parts.forEach((part, i) => {
      acc += "/" + part;
      bits.push(
        `<button type="button" class="hosts-crumb${i === parts.length - 1 ? " is-here" : ""}" data-browse-go="${esc(acc)}">${esc(part)}</button>`
      );
    });
    return `<nav class="hosts-crumbs" aria-label="Folder path">${bits.join("")}</nav>`;
  }

  function renderBrowseModal() {
    if (!browseBody) return;
    if (!browseState.open) {
      browseBody.innerHTML = "";
      return;
    }
    const path = browseState.path || "~";
    const rows = (browseState.entries || [])
      .filter((item) => item && item.name && !String(item.name).startsWith("."))
      .slice()
      .sort((a, b) => {
        if (a.kind === b.kind) return String(a.name).localeCompare(String(b.name));
        return a.kind === "dir" ? -1 : 1;
      })
      .map((item) => {
        if (item.kind === "dir") {
          return (
            `<li><button type="button" class="hosts-browse-item" data-browse-into="${esc(item.name)}">` +
            `<span class="hosts-browse-kind">dir</span><span>${esc(item.name)}</span></button></li>`
          );
        }
        return `<li class="hosts-browse-file"><span class="hosts-browse-kind">file</span><span>${esc(item.name)}</span></li>`;
      })
      .join("");
    const up = browseState.parent ? `<button type="button" class="btn sm ghost" data-browse-up>Up</button>` : "";
    const home =
      browseState.home && browseState.home !== path
        ? `<button type="button" class="btn sm ghost" data-browse-home>Home</button>`
        : "";
    browseBody.innerHTML =
      `<div class="hosts-browse-bar">` +
      crumbsHtml(path) +
      home +
      up +
      `</div>` +
      (browseState.loading ? `<p class="usage-sub hosts-browse-loading">Opening…</p>` : "") +
      (browseState.err ? `<p class="callout fault">${esc(browseState.err)}</p>` : "") +
      `<ul class="hosts-browse-list">${rows || "<li class='usage-sub'>Empty folder</li>"}</ul>`;
  }

  function openBrowse(hostId, target, start) {
    browseState.open = true;
    browseState.hostId = hostId;
    browseState.target = target || "project";
    browseState.path = start || "";
    browseState.parent = "";
    browseState.home = "";
    browseState.entries = [];
    browseState.err = "";
    if (browseModal && browseModal.showModal) browseModal.showModal();
    loadBrowse(start);
  }

  function closeBrowse() {
    browseState.open = false;
    browseState.entries = [];
    if (browseModal && browseModal.open) browseModal.close();
    renderBrowseModal();
  }

  async function loadBrowse(path) {
    if (!browseState.hostId) return;
    browseState.loading = true;
    if (path) browseState.path = path;
    renderBrowseModal();
    try {
      const data = await api(`/hosts/${browseState.hostId}/browse`, {
        method: "POST",
        body: JSON.stringify({ path: path || browseState.path || "" }),
      });
      browseState.path = data.path || path || "";
      browseState.parent = data.parent || "";
      browseState.home = data.home || "";
      browseState.entries = data.entries || [];
      browseState.err = "";
    } catch (err) {
      browseState.err = err.message || "Could not list that folder";
    } finally {
      browseState.loading = false;
      renderBrowseModal();
    }
  }

  function applyBrowsePath(path) {
    const base = (path || "").replace(/\/+$/, "").split("/").pop() || "";
    if (browseState.target === "wizard") {
      const input = wizard && wizard.querySelector("[data-w-proj-path]");
      const name = wizard && wizard.querySelector("[data-w-proj-name]");
      if (input) input.value = path || "";
      if (name && !name.value.trim() && base && base !== "/") name.value = base;
    }
  }

  function paint() {
    setVault(bundle.vault);
    const hosts = bundle.hosts || [];
    const pending = bundle.pending || [];
    const wizardOpen = wizard && !wizard.hidden;
    const connectorOpen = connector && !connector.hidden;
    document.querySelectorAll("[data-hosts-add]").forEach((btn) => {
      btn.hidden = wizardOpen || connectorOpen;
    });
    if (empty) empty.hidden = hosts.length > 0 || pending.length > 0 || wizardOpen || connectorOpen;
    list.innerHTML = hosts.length
      ? `<p class="hosts-list-head">Machines <span>${hosts.length}</span></p>` + hosts.map(hostRow).join("")
      : "";
    if (layout) layout.classList.toggle("is-solo", !hosts.length);
    const current = currentHost();
    if (detail) {
      detail.hidden = wizardOpen || connectorOpen || !current;
      detail.innerHTML = detail.hidden ? "" : hostPane(current);
    }
    if (connectorOpen && activePairing && installLine && activePairing.install) {
      installLine.textContent = activePairing.install;
    }
  }

  async function reload() {
    bundle = await api("/hosts/api");
    paint();
  }

  function setStep(n) {
    wizardStep = n;
    wizard.querySelectorAll("[data-step]").forEach((el) => {
      el.hidden = Number(el.getAttribute("data-step")) !== n;
    });
    wizard.querySelectorAll("[data-step-dot]").forEach((el) => {
      el.classList.toggle("is-on", Number(el.getAttribute("data-step-dot")) === n);
    });
    const back = wizard.querySelector("[data-wizard-back]");
    const next = wizard.querySelector("[data-wizard-next]");
    back.hidden = n === 1;
    next.textContent = n === 3 ? (wizardProbe ? "Trust & test" : "Probe") : n === 4 ? "Finish" : "Next";
  }

  function syncWizardAuth() {
    const key = wizard.querySelector("[data-w-auth]").value === "key";
    wizard.querySelector("[data-w-secret-label]").textContent = key ? "Private key (PEM)" : "Password";
    wizard.querySelector("[data-w-secret]").hidden = !key;
    wizard.querySelector("[data-w-password]").hidden = key;
    wizard.querySelector("[data-w-pass-wrap]").hidden = !key;
  }

  function openWizard() {
    wizardHostId = null;
    wizardProbe = null;
    wizard.hidden = false;
    if (empty) empty.hidden = true;
    showErr(wizardError, "");
    wizard.querySelector("[data-w-alias]").value = "";
    wizard.querySelector("[data-w-hostname]").value = "";
    wizard.querySelector("[data-w-port]").value = "22";
    wizard.querySelector("[data-w-username]").value = "";
    wizard.querySelector("[data-w-auth]").value = "key";
    wizard.querySelector("[data-w-secret]").value = "";
    wizard.querySelector("[data-w-password]").value = "";
    wizard.querySelector("[data-w-passphrase]").value = "";
    wizard.querySelector("[data-w-cwd]").value = "";
    wizard.querySelector("[data-w-proj-name]").value = "";
    wizard.querySelector("[data-w-proj-path]").value = "";
    wizard.querySelector("[data-w-proj-ro]").checked = false;
    wizard.querySelector("[data-w-fingerprint]").hidden = true;
    wizard.querySelector("[data-w-trust-status]").textContent = "Not probed yet.";
    syncWizardAuth();
    setStep(1);
    paint();
  }

  function closeWizard() {
    wizard.hidden = true;
    wizardHostId = null;
    wizardProbe = null;
    paint();
  }

  async function enableCodingIfOff() {
    if (bundle.settings && bundle.settings.agent_coding) return;
    try {
      bundle.settings = await api("/hosts/settings", {
        method: "POST",
        body: JSON.stringify({ agent_coding: true }),
      });
    } catch (_) {}
  }

  function setBusy(on) {
    busy = Boolean(on);
    const next = wizard.querySelector("[data-wizard-next]");
    if (next) next.disabled = busy;
    wizard.setAttribute("aria-busy", busy ? "true" : "false");
  }

  async function wizardNext() {
    if (busy) return;
    showErr(wizardError, "");
    try {
      if (wizardStep === 1) {
        const alias = wizard.querySelector("[data-w-alias]").value.trim();
        const hostname = wizard.querySelector("[data-w-hostname]").value.trim();
        const username = wizard.querySelector("[data-w-username]").value.trim();
        if (!alias || !hostname || !username) {
          showErr(wizardError, "Nickname, hostname, and username are required.");
          return;
        }
        setStep(2);
        return;
      }
      if (wizardStep === 2) {
        const auth = wizard.querySelector("[data-w-auth]").value;
        const secret =
          auth === "key"
            ? wizard.querySelector("[data-w-secret]").value
            : wizard.querySelector("[data-w-password]").value;
        if (wizardHostId) {
          // Back from step 3 and edited: save the corrections to the machine
          // already created, or probe/trust/test would target the old one.
          setBusy(true);
          const patch = {
            alias: wizard.querySelector("[data-w-alias]").value.trim(),
            hostname: wizard.querySelector("[data-w-hostname]").value.trim(),
            port: Number(wizard.querySelector("[data-w-port]").value || 22),
            username: wizard.querySelector("[data-w-username]").value.trim(),
            auth_type: auth,
            default_cwd: wizard.querySelector("[data-w-cwd]").value.trim(),
          };
          if (secret && secret.trim()) patch.secret = secret;
          const pass = wizard.querySelector("[data-w-passphrase]").value;
          if (auth === "key" && pass) patch.passphrase = pass;
          await api(`/hosts/${wizardHostId}`, { method: "PATCH", body: JSON.stringify(patch) });
          wizardProbe = null;
          const fp = wizard.querySelector("[data-w-fingerprint]");
          if (fp) fp.hidden = true;
          await reload();
          setStep(3);
          return;
        }
        if (!secret || !secret.trim()) {
          showErr(wizardError, auth === "key" ? "Paste a private key." : "Enter the SSH password.");
          return;
        }
        setBusy(true);
        const payload = {
          alias: wizard.querySelector("[data-w-alias]").value.trim(),
          hostname: wizard.querySelector("[data-w-hostname]").value.trim(),
          port: Number(wizard.querySelector("[data-w-port]").value || 22),
          username: wizard.querySelector("[data-w-username]").value.trim(),
          auth_type: auth,
          secret,
        };
        const pass = wizard.querySelector("[data-w-passphrase]").value;
        const cwd = wizard.querySelector("[data-w-cwd]").value.trim();
        if (auth === "key" && pass) payload.passphrase = pass;
        if (cwd) payload.default_cwd = cwd;
        const host = await api("/hosts", { method: "POST", body: JSON.stringify(payload) });
        wizardHostId = host.id;
        await reload();
        setStep(3);
        return;
      }
      if (wizardStep === 3) {
        if (!wizardHostId) {
          showErr(wizardError, "Add the machine first.");
          return;
        }
        setBusy(true);
        if (!wizardProbe) {
          wizard.querySelector("[data-w-trust-status]").textContent = "Probing…";
          wizardProbe = await api(`/hosts/${wizardHostId}/probe`, { method: "POST", body: "{}" });
          const fp = wizard.querySelector("[data-w-fingerprint]");
          fp.hidden = false;
          fp.textContent = `${wizardProbe.algorithm || ""} ${wizardProbe.fingerprint || ""}`.trim();
          wizard.querySelector("[data-w-trust-status]").textContent =
            "Compare this fingerprint with the machine, then continue to pin and test.";
          setStep(3);
          return;
        }
        wizard.querySelector("[data-w-trust-status]").textContent = "Trusting and testing…";
        await api(`/hosts/${wizardHostId}/trust`, {
          method: "POST",
          body: JSON.stringify({
            algorithm: wizardProbe.algorithm || "",
            fingerprint: wizardProbe.fingerprint || "",
          }),
        });
        await api(`/hosts/${wizardHostId}/test`, { method: "POST", body: "{}" });
        await enableCodingIfOff();
        await reload();
        const cwd = wizard.querySelector("[data-w-cwd]").value.trim();
        if (cwd) wizard.querySelector("[data-w-proj-path]").value = cwd;
        wizard.querySelector("[data-w-trust-status]").textContent = "Trusted and reachable.";
        setStep(4);
        return;
      }
      if (wizardStep === 4) {
        const name = wizard.querySelector("[data-w-proj-name]").value.trim();
        const path = wizard.querySelector("[data-w-proj-path]").value.trim();
        if (!name && !path) {
          closeWizard();
          return;
        }
        if (!name || !path) {
          showErr(wizardError, "Project name and working directory are required, or leave both blank to skip.");
          return;
        }
        setBusy(true);
        const body = { name, remote_path: path };
        if (wizard.querySelector("[data-w-proj-ro]").checked) body.permission_mode = "read_only";
        await api(`/hosts/${wizardHostId}/projects`, { method: "POST", body: JSON.stringify(body) });
        await reload();
        closeWizard();
      }
    } catch (err) {
      showErr(wizardError, err.message || "Something went wrong.");
    } finally {
      setBusy(false);
    }
  }

  function usablePairing(pairing) {
    return Boolean(pairing && (pairing.install || pairing.install_windows));
  }

  function showConnector(pairing) {
    activePairing = pairing;
    if (wizard) wizard.hidden = true;
    if (empty) empty.hidden = true;
    if (connector) connector.hidden = false;
    showErr(connectorError, "");
    if (installLine) {
      installLine.textContent =
        pairing.install || "Install code is shown only once. Cancel and create a new code.";
    }
    if (installWindows) {
      installWindows.textContent =
        pairing.install_windows || "Install code is shown only once. Cancel and create a new code.";
    }
    if (downloadWindows) {
      if (pairing.download_windows) {
        downloadWindows.hidden = false;
        downloadWindows.setAttribute("href", pairing.download_windows);
        downloadWindows.setAttribute("download", "fuse-agent-windows-amd64.exe");
      } else {
        downloadWindows.hidden = true;
        downloadWindows.removeAttribute("href");
      }
    }
    if (connectorWait) {
      const exp = pairing.expires_at ? ` Code expires ${fmtWhen(pairing.expires_at)}.` : "";
      connectorWait.textContent = "Waiting for this machine…" + exp;
    }
    paint();
  }

  function closeConnector() {
    activePairing = null;
    if (connector) connector.hidden = true;
    showErr(connectorError, "");
    paint();
  }

  async function startConnector() {
    showErr(pageError, "");
    showErr(connectorError, "");
    try {
      const pairing = await api("/hosts/pairings", { method: "POST", body: "{}" });
      bundle.pending = bundle.pending || [];
      if (!bundle.pending.some((p) => p.id === pairing.id)) bundle.pending.unshift(pairing);
      showConnector(pairing);
    } catch (err) {
      showErr(pageError, err.message || "Could not create install code");
    }
  }

  function upsertHost(host) {
    if (!host || host.id == null) return;
    const hosts = bundle.hosts || [];
    const idx = hosts.findIndex((h) => h.id === host.id);
    if (idx >= 0) {
      const prev = hosts[idx];
      hosts[idx] = { ...prev, ...host, projects: host.projects || prev.projects || [] };
    } else {
      hosts.push({ ...host, projects: host.projects || [] });
    }
    hosts.sort((a, b) => String(a.alias || "").localeCompare(String(b.alias || "")));
    bundle.hosts = hosts;
  }

  function connectHosts() {
    hostsEpoch += 1;
    const epoch = hostsEpoch;
    if (hostsSocket) {
      try { hostsSocket.close(); } catch (_) {}
    }
    const url = new URL("/hosts/events", location.href);
    url.protocol = location.protocol === "https:" ? "wss:" : "ws:";
    const ws = (hostsSocket = new WebSocket(url));
    ws.onmessage = (message) => {
      if (epoch !== hostsEpoch) return;
      let data = {};
      try { data = JSON.parse(message.data); } catch (_) { return; }
      if (data.type === "ping") {
        try { ws.send("pong"); } catch (_) {}
        return;
      }
      if (data.type === "ready") {
        bundle.hosts = data.hosts || [];
        bundle.pending = data.pending || [];
        if (data.vault) setVault(data.vault);
        if (data.base) bundle.base = data.base;
        if (activePairing && !(bundle.pending || []).some((p) => p.id === activePairing.id)) {
          const next = (bundle.pending || []).find(usablePairing);
          if (next) showConnector(next);
          else closeConnector();
        }
        paint();
        return;
      }
      if (data.type === "host") {
        upsertHost(data.host);
        if (activePairing && data.host && data.host.kind === "agent") closeConnector();
        paint();
        return;
      }
      if (data.type === "host_removed") {
        bundle.hosts = (bundle.hosts || []).filter((h) => h.id !== data.id);
        paint();
        return;
      }
      if (data.type === "pending") {
        bundle.pending = bundle.pending || [];
        const pairing = data.pairing;
        if (pairing && !bundle.pending.some((p) => p.id === pairing.id)) bundle.pending.unshift(pairing);
        paint();
        return;
      }
      if (data.type === "pending_gone") {
        bundle.pending = (bundle.pending || []).filter((p) => p.id !== data.id);
        if (activePairing && activePairing.id === data.id && (!bundle.pending || !bundle.pending.length)) closeConnector();
        paint();
      }
    };
    ws.onclose = () => {
      if (epoch !== hostsEpoch) return;
      window.setTimeout(() => {
        if (epoch === hostsEpoch) connectHosts();
      }, 1500);
    };
    ws.onerror = () => {
      try { ws.close(); } catch (_) {}
    };
  }

  document.querySelectorAll("[data-wizard-start]").forEach((btn) => {
    btn.addEventListener("click", startConnector);
  });
  if (connector) {
    const copyBtn = connector.querySelector("[data-copy-install]");
    if (copyBtn) {
      copyBtn.addEventListener("click", async () => {
        const line = (installLine && installLine.textContent) || "";
        try {
          await navigator.clipboard.writeText(line.trim());
          copyBtn.classList.add("ok");
          window.setTimeout(() => copyBtn.classList.remove("ok"), 800);
        } catch (_) {
          copyBtn.classList.add("err");
          window.setTimeout(() => copyBtn.classList.remove("err"), 1200);
        }
      });
    }
    const copyWin = connector.querySelector("[data-copy-windows]");
    if (copyWin) {
      copyWin.addEventListener("click", async () => {
        const line = (installWindows && installWindows.textContent) || "";
        try {
          await navigator.clipboard.writeText(line.trim());
          copyWin.classList.add("ok");
          window.setTimeout(() => copyWin.classList.remove("ok"), 800);
        } catch (_) {
          copyWin.classList.add("err");
          window.setTimeout(() => copyWin.classList.remove("err"), 1200);
        }
      });
    }
    const cancelBtn = connector.querySelector("[data-connector-cancel]");
    if (cancelBtn) {
      cancelBtn.addEventListener("click", async () => {
        const id = activePairing && activePairing.id;
        if (id) {
          try { await api(`/hosts/pairings/${id}`, { method: "DELETE" }); } catch (_) {}
          bundle.pending = (bundle.pending || []).filter((p) => p.id !== id);
        }
        closeConnector();
      });
    }
    const sshInstead = connector.querySelector("[data-ssh-instead]");
    if (sshInstead) {
      sshInstead.addEventListener("click", () => {
        if (locked()) {
          if (window.fuseUI) {
            window.fuseUI.vaultPrompt({
              error: "Unlock with your account password to add an SSH machine.",
              message: "SSH machines keep their secrets wrapped with your account password.",
              onUnlocked: () => {
                closeConnector();
                openWizard();
              },
            });
          }
          return;
        }
        closeConnector();
        openWizard();
      });
    }
  }
  wizard.querySelector("[data-wizard-cancel]").addEventListener("click", closeWizard);
  wizard.querySelector("[data-wizard-back]").addEventListener("click", () => {
    if (wizardStep > 1) setStep(wizardStep - 1);
  });
  wizard.querySelector("[data-wizard-next]").addEventListener("click", wizardNext);
  wizard.querySelector("[data-w-auth]").addEventListener("change", syncWizardAuth);

  const hostModal = root.querySelector("[data-edit-host-modal]");
  const hostModalErr = hostModal ? hostModal.querySelector("[data-edit-host-error]") : null;

  function findHost(id) {
    return (bundle.hosts || []).find((h) => String(h.id) === String(id));
  }

  function newCommandId() {
    if (crypto.randomUUID) return crypto.randomUUID();
    return "c" + Math.random().toString(36).slice(2) + Date.now().toString(36);
  }

  function openHostModal(host) {
    if (!hostModal || !host) return;
    showErr(hostModalErr, "");
    hostModal.dataset.hostId = String(host.id);
    const form = hostModal.querySelector("[data-edit-host-form]");
    form.alias.value = host.alias || "";
    form.hostname.value = host.hostname || "";
    form.username.value = host.username || "";
    form.default_cwd.value = host.default_cwd || "";
    const portField = hostModal.querySelector("[data-port-field]");
    if (portField) portField.hidden = isAgent(host);
    if (!isAgent(host)) form.port.value = host.port || 22;
    if (hostModal.showModal) hostModal.showModal();
  }

  // Add and edit use the same project dialog as chat and Projects.
  async function editProject(host, p) {
    if (!host || !window.FuseProjects) return;
    const saved = await window.FuseProjects.openEditor({
      hostId: host.id,
      hosts: bundle.hosts || [],
      project: p ? { ...p, host_id: host.id, host_alias: host.alias } : null,
    });
    if (saved) await reload();
  }

  if (hostModal) {
    const form = hostModal.querySelector("[data-edit-host-form]");
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      showErr(hostModalErr, "");
      const hostId = hostModal.dataset.hostId;
      const data = Object.fromEntries(new FormData(form).entries());
      const host = findHost(hostId);
      if (host && isAgent(host)) {
        delete data.port;
      } else {
        data.port = Number(data.port || 22);
      }
      try {
        await api(`/hosts/${hostId}`, { method: "PATCH", body: JSON.stringify(data) });
        hostModal.close();
        await reload();
      } catch (err) {
        showErr(hostModalErr, err.message || "Could not save machine");
      }
    });
  }

  if (browseModal) {
    browseModal.addEventListener("close", () => {
      browseState.open = false;
      browseState.entries = [];
      renderBrowseModal();
    });
  }

  root.addEventListener("click", async (event) => {
    const toggle = event.target.closest("[data-toggle-card]");
    const addBtn = event.target.closest("[data-add-project]");
    const probe = event.target.closest("[data-probe]");
    const test = event.target.closest("[data-test]");
    const delHost = event.target.closest("[data-del-host]");
    const delProj = event.target.closest("[data-del-project]");
    const open = event.target.closest("[data-open-project]");
    const editHost = event.target.closest("[data-edit-host]");
    const editProj = event.target.closest("[data-edit-project]");
    const wizardBrowseBtn = event.target.closest("[data-w-browse]");
    const wizardPathInput = event.target.closest("[data-w-proj-path]");
    const browseInto = event.target.closest("[data-browse-into]");
    const browseUp = event.target.closest("[data-browse-up]");
    const browseHome = event.target.closest("[data-browse-home]");
    const browseGo = event.target.closest("[data-browse-go]");
    const browseUse = event.target.closest("[data-browse-use]");
    try {
      if (toggle) {
        selectedId = Number(toggle.getAttribute("data-toggle-card"));
        if (wizard && !wizard.hidden) closeWizard();
        paint();
        // Stacked layout: bring the detail into view under the list.
        if (detail && window.matchMedia("(max-width: 900px)").matches) detail.scrollIntoView({ block: "start", behavior: "smooth" });
        return;
      }
      if (addBtn) {
        const host = findHost(addBtn.getAttribute("data-add-project"));
        selectedId = Number(addBtn.getAttribute("data-add-project"));
        await editProject(host, null);
        return;
      }
      if (editHost) {
        openHostModal(findHost(editHost.getAttribute("data-edit-host")));
        return;
      }
      if (editProj) {
        const [hid, pid] = editProj.getAttribute("data-edit-project").split(":");
        const host = findHost(hid);
        const p = host && (host.projects || []).find((x) => String(x.id) === String(pid));
        await editProject(host, p);
        return;
      }
      if (wizardBrowseBtn || wizardPathInput) {
        if (!wizardHostId) {
          showErr(wizardError, "Add the machine first.");
          return;
        }
        const start = ((wizard.querySelector("[data-w-proj-path]") || {}).value || "").trim();
        openBrowse(wizardHostId, "wizard", start);
        return;
      }
      if (browseInto) {
        const name = browseInto.getAttribute("data-browse-into") || "";
        const cur = browseState.path || "";
        let next;
        if (isWinPath(cur)) {
          next = cur.replace(/\\+$/, "") + "\\" + name;
        } else {
          next = (cur === "/" ? "" : String(cur).replace(/\/+$/, "")) + "/" + name;
        }
        await loadBrowse(next);
        return;
      }
      if (browseUp) {
        await loadBrowse(browseState.parent || "/");
        return;
      }
      if (browseHome) {
        await loadBrowse(browseState.home || "~");
        return;
      }
      if (browseGo) {
        await loadBrowse(browseGo.getAttribute("data-browse-go") || browseGo.getAttribute("data-path") || "/");
        return;
      }
      if (browseUse) {
        applyBrowsePath(browseState.path || "");
        closeBrowse();
        return;
      }
      if (probe) {
        const id = probe.getAttribute("data-probe");
        const data = await api(`/hosts/${id}/probe`, { method: "POST", body: "{}" });
        const fp = `${data.algorithm || ""} ${data.fingerprint || ""}`.trim();
        if (!fp) throw new Error("Probe did not return a fingerprint.");
        if (await ask(`Trust this machine?\n${fp}`)) {
          await api(`/hosts/${id}/trust`, {
            method: "POST",
            body: JSON.stringify({ algorithm: data.algorithm || "", fingerprint: data.fingerprint || "" }),
          });
        }
        await reload();
      } else if (test) {
        await runTest(Number(test.getAttribute("data-test")));
        return;
      } else if (delHost) {
        if (!(await ask("Remove this machine and its projects?", { confirmLabel: "Remove", danger: true }))) return;
        await api(`/hosts/${delHost.getAttribute("data-del-host")}`, { method: "DELETE" });
        await reload();
      } else if (delProj) {
        const [hid, pid] = delProj.getAttribute("data-del-project").split(":");
        const host = findHost(hid);
        const p = host && (host.projects || []).find((x) => String(x.id) === String(pid));
        const label = p ? `"${p.name}"` : "this project";
        if (!(await ask(`Delete project ${label} and its chats? The folder on the machine is not touched.`, { confirmLabel: "Delete", danger: true }))) return;
        await api(`/hosts/${hid}/projects/${pid}?sessions=delete`, { method: "DELETE" });
        await reload();
      } else if (open) {
        const [hid, pid] = open.getAttribute("data-open-project").split(":");
        const host = findHost(hid);
        const p = host && (host.projects || []).find((x) => String(x.id) === String(pid));
        const thread = await api("/chat/threads", {
          method: "POST",
          body: JSON.stringify({
            kind: "project",
            host_id: Number(hid),
            project_id: Number(pid),
            title: (p && p.name) || "",
            command_id: newCommandId(),
          }),
        });
        if (thread && thread.id) window.location.href = "/chat?t=" + thread.id;
      }
    } catch (err) {
      if (err.type === "vault_locked") {
        setVault("locked");
        if (window.fuseUI) {
          window.fuseUI.vaultPrompt({
            error: err.message || "Unlock your machines with your account password.",
            message: "SSH machines keep their secrets wrapped with your account password.",
            onUnlocked: () => {
              setVault("unlocked");
              reload();
            },
          });
        }
        return;
      }
      showErr(pageError, err.message || "Request failed");
    }
  });

  async function runTest(id) {
    const host = findHost(id);
    if (!host || !window.fuseUI) return;
    const alias = host.alias || "machine";
    testingId = id;
    paint();
    window.fuseUI.toast({
      key: "host-test-" + id,
      kind: "idle",
      title: alias,
      msg: "Testing connection…",
    });
    try {
      const data = await api(`/hosts/${id}/test`, { method: "POST", body: "{}" });
      await enableCodingIfOff();
      if (data && data.host) upsertHost(data.host);
      window.fuseUI.toast({
        key: "host-test-" + id,
        kind: "ok",
        title: alias,
        msg: "Reachable",
        actionLabel: "Test",
        onAction: () => runTest(id),
        ttl: 6000,
      });
    } catch (err) {
      window.fuseUI.toast({
        key: "host-test-" + id,
        kind: "err",
        title: alias,
        msg: err.message || "Test failed",
        actionLabel: "Test",
        onAction: () => runTest(id),
        ttl: 15000,
      });
    }
    testingId = null;
    paint();
  }

  const firstUsable = (bundle.pending || []).find(usablePairing);
  if (firstUsable && !activePairing) {
    showConnector(firstUsable);
  }
  connectHosts();
  paint();
  vaultGate();
})();
