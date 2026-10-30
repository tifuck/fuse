// Artifact pane: the page beside the chat. Opens it, versions with notes,
// source and diffs, the /artifacts list, sharing, live updates, the page
// bridge (errors, links, copy, send to chat) and when it opens by itself.
import { S } from './state.js';
import { escapeHtml } from './core.js';
import { returnFocus } from './overlays.js';

const NAMES = new Set(["create_artifact", "update_artifact", "read_artifact", "edit_artifact"]);
const WRITES = new Set(["create_artifact", "update_artifact", "edit_artifact"]);
const AUTO_KEY = "fuse.artifact.autoOpen";
const WIDTH_KEY = "fuse.artifact.width";
const POLL_MS = 8000;

function artifactContentUrl(id, version) {
  const path = "/a/" + encodeURIComponent(id) + "/content";
  return version ? path + "?v=" + encodeURIComponent(version) : path;
}

// An identifier, or the one in an /a/<id> link (models pass either).
function normalizeId(raw) {
  const text = String(raw || "").trim();
  if (/^[A-Za-z0-9_-]{1,64}$/.test(text)) return text;
  const match = text.match(/(?:^|\/)a\/([A-Za-z0-9_-]{8,64})(?=$|[/?#])/);
  return match ? match[1] : "";
}

function autoOpenOn() {
  try { return localStorage.getItem(AUTO_KEY) !== "0"; } catch (_) { return true; }
}

function sizeLabel(chars) {
  const n = Number(chars) || 0;
  return n >= 1000 ? `${(n / 1000).toFixed(n >= 10000 ? 0 : 1)}k chars` : `${n} chars`;
}

function absolute(url) {
  try { return new URL(url, location.origin).href; } catch (_) { return url; }
}

export function createArtifacts(ctx) {
  const { net, overlays, panels, picker, sidebar } = ctx;
  const panel = S.artifactPanel;
  const q = (sel) => (panel ? panel.querySelector(sel) : null);
  const el = {
    icon: q("[data-artifact-icon]"),
    listToggle: q("[data-artifact-list-toggle]"),
    list: q("[data-artifact-list]"),
    reload: q("[data-artifact-reload]"),
    menuToggle: q("[data-artifact-menu-toggle]"),
    menu: q("[data-artifact-menu]"),
    expand: q("[data-artifact-expand]"),
    alert: q("[data-artifact-alert]"),
    alertText: q("[data-artifact-alert-text]"),
    fix: q("[data-artifact-fix]"),
    alertClose: q("[data-artifact-alert-close]"),
    resize: q("[data-artifact-resize]"),
    auto: q("[data-artifact-autoopen]"),
    toggleLabel: S.artifactToggle ? S.artifactToggle.querySelector("span") : null,
  };
  // The artifact write in flight: "writing" while its arguments stream,
  // "saving" once the call runs. Drives the toggle and pane status.
  let activity = null;
  let activityTimer = 0;
  let fixError = null;
  let pollTimer = 0;
  let metaTag = "";
  let freshTimer = 0;
  let listItems = null;
  const scrolls = new Map();

  function showFailure(title, error) {
    if (ctx.sync && ctx.sync.showFailure) ctx.sync.showFailure(title, error);
  }

  function hint(text) {
    if (picker && picker.setHint) picker.setHint(text);
  }

  function artifactsInThread() {
    const out = [];
    (S.messages || []).forEach((msg) => {
      (msg.tools || []).forEach((trace) => {
        if (NAMES.has(trace.name) && normalizeId(trace.identifier) && trace.status !== "error") out.push(trace);
      });
    });
    return out;
  }

  function latestArtifact() {
    const all = artifactsInThread();
    return all.length ? all[all.length - 1] : null;
  }

  // One entry per artifact, the latest trace of each, oldest first.
  function distinctArtifacts() {
    const seen = new Map();
    artifactsInThread().forEach((trace) => {
      const id = normalizeId(trace.identifier);
      seen.delete(id);
      seen.set(id, trace);
    });
    return [...seen.values()];
  }

  function findTrace(id) {
    for (const msg of S.messages || []) {
      for (const trace of msg.tools || []) if (trace.id === id) return trace;
    }
    return null;
  }

  function seedFrom(trace) {
    return {
      identifier: normalizeId(trace.identifier),
      title: trace.title || "Artifact",
      icon: trace.icon || "",
      visibility: trace.visibility || "",
      version: null,
      url: "/a/" + normalizeId(trace.identifier),
    };
  }

  // A call that ended without a done event reaching the pane (an error, a
  // stop, a reload mid-call) must not leave it on "Building…".
  function settleActivity() {
    if (!activity) return;
    const rec = activity.id ? findTrace(activity.id) : null;
    if (rec ? rec.status === "running" : S.streaming) return;
    finishActivity(rec);
  }

  function finishActivity(rec) {
    const was = activity;
    activity = null;
    clearTimeout(activityTimer);
    const cur = S.currentArtifact;
    if (cur && cur.updating) cur.updating = false;
    const done = rec && rec.status === "done" && normalizeId(rec.identifier);
    if (cur && cur.building) {
      S.currentArtifact = done ? seedFrom(rec) : cur.previous || null;
      if (S.currentArtifact) loadFrame(true);
    }
    if (done) return;
    if (rec && rec.status === "error" && S.artifactOpen) {
      showAlert(`Not published: ${rec.error || "the call failed"}`);
    } else if (was && rec && rec.status === "interrupted" && S.artifactOpen) {
      showAlert("Stopped before the artifact was saved.");
    }
  }

  function rememberLatestArtifact() {
    settleActivity();
    const all = artifactsInThread();
    const last = all.length ? all[all.length - 1] : null;
    if (!last) {
      // Nothing in this transcript: keep only what the reader opened on
      // purpose (from /artifacts or a link) or a page still being built.
      if ((S.currentArtifact && (S.currentArtifact.building || S.currentArtifact.explicit)) || S.artifactMode === "list") {
        syncArtifactUi();
        return;
      }
      S.currentArtifact = null;
      // Close through setArtifactOpen so the mobile overlay/history entry is
      // dropped too; a bare flag write leaves a Back press that does nothing.
      if (S.artifactOpen) setArtifactOpen(false);
      else syncArtifactUi();
      return;
    }
    // An open pane keeps what the reader picked (an older artifact, a version,
    // one opened from /artifacts, a page being rebuilt).
    if (S.currentArtifact && (S.currentArtifact.building || (S.artifactOpen && S.currentArtifact.identifier))) {
      syncArtifactUi();
      return;
    }
    S.currentArtifact = seedFrom(last);
    syncArtifactUi();
  }

  function statusText() {
    const cur = S.currentArtifact;
    if (activity) {
      const what = activity.title ? `“${activity.title}”` : activity.tool === "create_artifact" ? "a new artifact" : "the artifact";
      const verb = activity.phase === "writing" ? (activity.tool === "create_artifact" ? "Writing" : "Changing") : "Publishing";
      return `${verb} ${what}${activity.chars ? ` · ${sizeLabel(activity.chars)}` : ""}…`;
    }
    if (cur && cur.missing) return "Not available";
    return "";
  }

  function versionsOf(cur) {
    const history = (cur && cur.history) || [];
    if (history.length) return history;
    return ((cur && cur.versions) || []).map((version) => ({ version, note: "" }));
  }

  function viewing(cur) {
    return (cur && (cur.version || cur.current)) || null;
  }

  function syncVersionPicker(cur) {
    if (!S.artifactVersionWrap || !S.artifactVersionEl) return;
    const items = versionsOf(cur);
    if (!cur || items.length < 2) {
      S.artifactVersionWrap.hidden = true;
      return;
    }
    S.artifactVersionWrap.hidden = false;
    const latest = items[items.length - 1].version;
    const current = String(cur.version || latest);
    const sig = items.map((item) => `${item.version}:${item.note}`).join("|") + `#${current}#${cur.shared_version || ""}`;
    if (S.artifactVersionEl._sig === sig) return;
    S.artifactVersionEl._sig = sig;
    S.artifactVersionEl.innerHTML = items
      .slice()
      .reverse()
      .map((item) => {
        const tags = [item.version === latest ? "latest" : "", cur.visibility === "public" && item.version === (cur.shared_version || latest) ? "shared" : ""].filter(Boolean);
        const note = item.note ? ` · ${item.note.slice(0, 48)}` : "";
        const label = `v${item.version}${tags.length ? ` (${tags.join(", ")})` : ""}${note}`;
        return `<option value="${escapeHtml(String(item.version))}"${String(item.version) === current ? " selected" : ""}>${escapeHtml(label)}</option>`;
      })
      .join("");
  }

  function syncMenu(cur) {
    if (!el.menu) return;
    const has = Boolean(cur && cur.identifier && !cur.missing);
    const pub = has && cur.visibility === "public";
    const items = versionsOf(cur);
    const at = viewing(cur);
    const latest = items.length ? items[items.length - 1].version : at;
    const show = (act, on, label) => {
      const item = el.menu.querySelector(`[data-artifact-act="${act}"]`);
      if (!item) return;
      item.hidden = !on;
      if (label) item.textContent = label;
    };
    show("share", has, pub ? "Stop sharing" : "Share with a link");
    show("copy-link", has, pub ? "Copy share link" : "Copy link (only you)");
    show("pin", pub && items.length > 1, cur && cur.shared_version ? `Viewers: always the latest (pinned to v${cur.shared_version})` : `Viewers: only v${at}`);
    show("rotate", pub, "Reset share link");
    show("copy-source", has);
    show("download", has);
    show("diff", has && items.length > 1 && at > items[0].version, `Changes from v${(items[items.findIndex((item) => item.version === at) - 1] || {}).version || ""}`);
    show("revert", has && at && latest && at < latest, `Restore v${at} as the latest`);
    if (el.auto) el.auto.checked = autoOpenOn();
    const download = el.menu.querySelector('[data-artifact-act="download"]');
    if (download && has) download.setAttribute("href", `/a/${encodeURIComponent(cur.identifier)}/download${cur.version ? `?v=${cur.version}` : ""}`);
  }

  function syncToggle() {
    if (!S.artifactToggle) return;
    const count = distinctArtifacts().length;
    S.artifactToggle.disabled = false;
    S.artifactToggle.setAttribute("aria-expanded", S.artifactOpen ? "true" : "false");
    S.artifactToggle.classList.toggle("is-on", S.artifactOpen);
    S.artifactToggle.classList.toggle("is-busy", Boolean(activity));
    const label = activity ? (activity.phase === "writing" ? "Writing…" : "Publishing…") : count > 1 ? `Artifacts · ${count}` : "Artifact";
    if (el.toggleLabel && el.toggleLabel.textContent !== label) el.toggleLabel.textContent = label;
    S.artifactToggle.title = count ? "Artifact preview (Ctrl+])" : "Artifacts (Ctrl+])";
  }

  function syncArtifactUi() {
    const cur = S.currentArtifact;
    if (S.artifactPanel) S.artifactPanel.hidden = !S.artifactOpen;
    if (S.artifactBackdrop) S.artifactBackdrop.hidden = !(S.artifactOpen && window.matchMedia("(max-width: 800px)").matches);
    S.root.classList.toggle("is-artifact", S.artifactOpen);
    syncToggle();
    const building = Boolean(cur && cur.building);
    const listing = S.artifactMode === "list";
    const ident = cur && !cur.missing && cur.identifier;
    if (S.artifactTitleEl) S.artifactTitleEl.textContent = listing ? "Artifacts" : (cur && cur.title) || "Artifact";
    if (el.icon) el.icon.textContent = listing ? "" : (cur && cur.icon) || "";
    if (el.listToggle) el.listToggle.setAttribute("aria-expanded", listing ? "true" : "false");
    if (S.artifactStatusEl) {
      const text = statusText();
      S.artifactStatusEl.hidden = !text;
      S.artifactStatusEl.textContent = text;
    }
    if (S.artifactVisibilityEl) {
      const vis = !listing && cur && cur.identifier ? cur.visibility : "";
      S.artifactVisibilityEl.hidden = !vis;
      S.artifactVisibilityEl.textContent = vis === "public" ? "shared" : vis || "";
      S.artifactVisibilityEl.classList.toggle("on", vis === "public");
    }
    if (S.artifactOpenLink) {
      if (ident && !listing) {
        S.artifactOpenLink.hidden = false;
        S.artifactOpenLink.href = "/a/" + encodeURIComponent(ident) + (cur.version ? `?v=${cur.version}` : "");
      } else {
        S.artifactOpenLink.hidden = true;
        S.artifactOpenLink.removeAttribute("href");
      }
    }
    if (S.artifactBuilding) {
      S.artifactBuilding.hidden = !building || listing;
      if (building) S.artifactBuilding.textContent = activity && activity.chars ? `Building the page… ${sizeLabel(activity.chars)}` : "Building the page…";
    }
    if (S.artifactFrame) S.artifactFrame.hidden = building || listing || S.artifactMode !== "preview" || !ident;
    if (S.artifactSource) S.artifactSource.hidden = building || listing || S.artifactMode !== "source";
    if (el.list) el.list.hidden = !listing;
    S.artifactModeBtns.forEach((btn) => {
      btn.hidden = listing;
      btn.setAttribute("aria-pressed", btn.getAttribute("data-artifact-mode") === S.artifactMode ? "true" : "false");
    });
    if (el.reload) el.reload.hidden = listing || !ident;
    if (el.menuToggle) el.menuToggle.hidden = listing || !ident;
    if (listing && S.artifactVersionWrap) S.artifactVersionWrap.hidden = true;
    else syncVersionPicker(cur);
    syncMenu(cur);
    syncPoll();
  }

  function syncPoll() {
    if (!panel) return;
    const cur = S.currentArtifact;
    const want = S.artifactOpen && !document.hidden && S.artifactMode !== "list" && cur && cur.identifier && !cur.building && !cur.missing;
    if (want && !pollTimer) pollTimer = setInterval(() => { refreshMeta(S.generation, true); }, POLL_MS);
    else if (!want && pollTimer) {
      clearInterval(pollTimer);
      pollTimer = 0;
    }
  }

  function setArtifactOpen(open) {
    const next = Boolean(open);
    if (next === S.artifactOpen) {
      if (next) syncArtifactUi();
      return;
    }
    S.artifactOpen = next;
    if (S.artifactOpen) {
      if (S.memoryOpen) panels.setMemoryOpen(false);
      if (S.skillsOpen) panels.setSkillsOpen(false);
      if (overlays.overlayIsMobile()) sidebar.setHistoryOpen(false);
      else if (window.matchMedia("(max-width: 1100px)").matches) {
        S.root.classList.add("history-collapsed");
        if (typeof sidebar.syncHistory === "function") sidebar.syncHistory();
      }
      picker.closePicker();
      overlays.overlayPush("artifact");
      loadFrame(false);
    } else {
      closeMenu();
      overlays.armOverlayGhost(S.root.querySelector("[data-artifact-close]"));
      overlays.overlayDrop("artifact");
      if (S.artifactMode === "list") S.artifactMode = "preview";
    }
    syncArtifactUi();
    // Only a click moves focus in; a stream opening the pane must not take it.
    if (S.artifactOpen) { if (document.activeElement?.matches("button")) S.artifactPanel?.focus({ preventScroll: true }); }
    else returnFocus(S.artifactPanel, S.artifactToggle && !S.artifactToggle.disabled ? S.artifactToggle : S.input);
    if (ctx.composer && ctx.composer.scheduleFitChatBar) ctx.composer.scheduleFitChatBar();
  }

  function frameUrl() {
    const cur = S.currentArtifact;
    return cur && cur.identifier && !cur.building && !cur.missing ? artifactContentUrl(cur.identifier, cur.version) : "";
  }

  function loadFrame(force) {
    if (!S.artifactFrame || !S.artifactOpen) return;
    const url = frameUrl();
    if (!url) {
      S.artifactFrame.removeAttribute("src");
      return;
    }
    if (force || S.artifactFrame.getAttribute("src") !== url) {
      hideAlert();
      // Setting src navigates even when it is unchanged: a reload.
      S.artifactFrame.src = url;
    }
  }

  function pulse() {
    if (!S.artifactToggle) return;
    S.artifactToggle.classList.add("is-fresh");
    clearTimeout(freshTimer);
    freshTimer = setTimeout(() => S.artifactToggle.classList.remove("is-fresh"), 4000);
  }

  function noteDrafting(info) {
    if (!info || !WRITES.has(info.tool)) return;
    const same = activity && activity.phase === "writing" && activity.tool === info.tool;
    activity = {
      phase: "writing",
      tool: info.tool,
      title: info.title || (same ? activity.title : ""),
      identifier: normalizeId(info.identifier),
      chars: Number(info.chars) || 0,
      id: "",
    };
    clearTimeout(activityTimer);
    activityTimer = setTimeout(() => {
      if (activity && activity.phase === "writing") {
        activity = null;
        syncArtifactUi();
      }
    }, 20000);
    const cur = S.currentArtifact;
    if (S.artifactOpen && info.tool === "create_artifact" && !(cur && (cur.identifier || cur.building))) beginPlaceholder(activity.title, cur);
    else if (cur && activity.identifier && cur.identifier === activity.identifier) cur.updating = true;
    syncArtifactUi();
  }

  function beginPlaceholder(title, previous) {
    S.currentArtifact = { identifier: "", title: title || "New artifact", building: true, previous: previous && previous.identifier ? previous : null, visibility: "private" };
    S.artifactMode = "preview";
    S.artifactSourceLoaded = "";
    if (S.artifactFrame) S.artifactFrame.removeAttribute("src");
    if (S.artifactSource) S.artifactSource.textContent = "";
  }

  // The call started running (its arguments are complete).
  function startArtifactBuild(event) {
    const info = typeof event === "string" ? { name: "create_artifact", title: event } : event || {};
    if (!WRITES.has(info.name)) return;
    const id = normalizeId(info.identifier);
    activity = { phase: "saving", tool: info.name, title: info.title || (activity && activity.title) || "", identifier: id, chars: activity ? activity.chars : 0, id: info.id || "" };
    clearTimeout(activityTimer);
    const cur = S.currentArtifact;
    if (cur && cur.building) {
      if (info.title) cur.title = info.title;
    } else if (cur && id && cur.identifier === id) {
      cur.updating = true;
    } else if (S.artifactOpen && info.name === "create_artifact") {
      beginPlaceholder(info.title, cur);
    } else {
      pulse();
    }
    syncArtifactUi();
  }

  // A create/update/edit call settled. Done: show or refresh the page per
  // the open policy; failed: restore what was on screen and say why.
  function noteArtifactResult(rec) {
    if (!rec || !WRITES.has(rec.name)) return;
    const id = normalizeId(rec.identifier);
    if (rec.status !== "done" || !id) {
      finishActivity(rec);
      syncArtifactUi();
      return;
    }
    finishActivity(rec);
    const cur = S.currentArtifact;
    if (S.artifactOpen && (!cur || !cur.identifier || cur.building || cur.identifier === id)) {
      showArtifact(id, rec, { keepMode: true, force: true }).catch(() => {});
      return;
    }
    const first = distinctArtifacts().filter((trace) => normalizeId(trace.identifier) !== id).length === 0;
    if (!S.artifactOpen && rec.name === "create_artifact" && first && autoOpenOn() && !overlays.overlayIsMobile()) {
      showArtifact(id, rec).catch(() => {});
      return;
    }
    if (!S.artifactOpen) S.currentArtifact = seedFrom(rec);
    pulse();
    syncArtifactUi();
  }

  async function showArtifact(identifier, seed, opts = {}) {
    identifier = normalizeId(identifier);
    if (!identifier) return;
    const gen = S.generation;
    const prior = S.currentArtifact && S.currentArtifact.identifier === identifier ? S.currentArtifact : {};
    S.currentArtifact = {
      identifier,
      title: (seed && seed.title) || prior.title || "Artifact",
      icon: (seed && seed.icon) || prior.icon || "",
      visibility: (seed && seed.visibility) || prior.visibility || "",
      version: opts.version || null,
      current: prior.current || null,
      versions: prior.versions || [],
      history: prior.history || [],
      shared_version: prior.shared_version ?? null,
      share_url: prior.share_url || "",
      url: "/a/" + identifier,
      building: false,
      explicit: Boolean(opts.explicit || (prior.explicit && opts.keepMode)),
    };
    if (prior.identifier !== identifier) metaTag = "";
    if (!opts.keepMode || S.artifactMode === "list") S.artifactMode = "preview";
    S.artifactSourceLoaded = "";
    if (S.artifactSource && S.artifactMode !== "source") S.artifactSource.textContent = "";
    if (!S.artifactOpen) setArtifactOpen(true);
    loadFrame(Boolean(opts.force));
    syncArtifactUi();
    await refreshMeta(gen, false);
    if (S.artifactMode === "source") await loadArtifactSource().catch((error) => showFailure("Could not load source", error));
  }

  async function fetchMeta(id, tag) {
    const res = await fetch("/a/" + encodeURIComponent(id) + "/meta", {
      credentials: "same-origin",
      headers: tag ? { "If-None-Match": tag } : {},
    });
    if (res.status === 304) return null;
    if (!res.ok) {
      const error = new Error(res.status === 404 ? "This artifact was deleted or archived, or is not yours." : `Could not load the artifact (${res.status}).`);
      error.status = res.status;
      throw error;
    }
    return { tag: res.headers.get("ETag") || "", meta: await res.json() };
  }

  async function refreshMeta(gen, poll) {
    const cur = S.currentArtifact;
    if (!cur || !cur.identifier) return;
    const id = cur.identifier;
    const stale = () => gen !== S.generation || !S.currentArtifact || S.currentArtifact.identifier !== id;
    let got;
    try {
      got = await fetchMeta(id, poll ? metaTag : "");
    } catch (error) {
      if (stale()) return;
      if (error.status === 404) {
        S.currentArtifact.missing = true;
        showAlert(error.message);
        if (S.artifactFrame) S.artifactFrame.removeAttribute("src");
        syncArtifactUi();
      }
      return;
    }
    if (!got || stale()) return;
    metaTag = got.tag;
    const meta = got.meta;
    const live = S.currentArtifact;
    const before = live.current;
    // Opened by its share link: the owner works on it by its identifier.
    if (meta.owner && meta.identifier && meta.identifier !== live.identifier) {
      live.identifier = meta.identifier;
      live.url = "/a/" + meta.identifier;
    }
    Object.assign(live, {
      missing: false,
      title: meta.title || live.title,
      icon: meta.icon || "",
      visibility: meta.visibility || live.visibility,
      current: meta.version,
      versions: Array.isArray(meta.versions) ? meta.versions : [],
      history: Array.isArray(meta.history) ? meta.history : [],
      shared_version: meta.shared_version ?? null,
      share_url: meta.share_url || "",
    });
    if (poll && before && meta.version !== before && !live.version) {
      // A newer version landed elsewhere (another chat, tab or the gallery):
      // show it in place, as an open Claude Code artifact does.
      S.artifactSourceLoaded = "";
      loadFrame(true);
      if (S.artifactMode === "source") loadArtifactSource().catch(() => {});
      hint(`“${live.title}” updated to v${meta.version}.`);
    }
    syncArtifactUi();
  }

  async function loadArtifactSource(diffFrom) {
    const cur = S.currentArtifact;
    if (!cur || !cur.identifier) return;
    const gen = S.generation;
    const ver = cur.version || "";
    const key = cur.identifier + ":" + ver + (diffFrom ? ":diff" + diffFrom : "");
    if (S.artifactSourceLoaded === key) return;
    const base = "/a/" + encodeURIComponent(cur.identifier);
    const url = diffFrom
      ? `${base}/diff?from=${encodeURIComponent(diffFrom)}${ver ? `&to=${encodeURIComponent(ver)}` : ""}`
      : `${base}/source${ver ? `?v=${encodeURIComponent(ver)}` : ""}`;
    const res = await fetch(url, { credentials: "same-origin" });
    const text = await res.text();
    if (gen !== S.generation || !S.currentArtifact || key !== S.currentArtifact.identifier + ":" + (S.currentArtifact.version || "") + (diffFrom ? ":diff" + diffFrom : "")) return;
    if (!res.ok) throw new Error("Could not load source");
    S.artifactSourceLoaded = key;
    if (!S.artifactSource) return;
    S.artifactSource.classList.toggle("is-diff", Boolean(diffFrom));
    if (!diffFrom) {
      S.artifactSource.textContent = text;
      return;
    }
    S.artifactSource.innerHTML = (text || "No changes.\n")
      .split("\n")
      .map((line) => {
        const cls = line.startsWith("@@") ? "is-hunk" : line.startsWith("+") && !line.startsWith("+++") ? "is-add" : line.startsWith("-") && !line.startsWith("---") ? "is-del" : "";
        return `<span class="${cls}">${escapeHtml(line)}</span>`;
      })
      .join("\n");
  }

  function setMode(mode) {
    S.artifactMode = mode;
    syncArtifactUi();
    if (mode === "source") loadArtifactSource().catch((error) => showFailure("Could not load source", error));
    if (mode === "preview") loadFrame(false);
  }

  function showAlert(text, fix) {
    if (!el.alert) return;
    fixError = fix || null;
    el.alert.hidden = false;
    el.alert.classList.toggle("is-error", Boolean(fix));
    if (el.alertText) el.alertText.textContent = text;
    if (el.fix) el.fix.hidden = !fix;
  }

  function hideAlert() {
    fixError = null;
    if (el.alert) el.alert.hidden = true;
  }

  function compose(text) {
    if (!text || !S.input) return;
    const now = S.input.value.replace(/\s+$/, "");
    S.input.value = now ? `${now}\n\n${text}` : text;
    S.input.dispatchEvent(new Event("input", { bubbles: true }));
    S.input.focus();
    try { S.input.setSelectionRange(S.input.value.length, S.input.value.length); } catch (_) {}
  }

  async function copyText(text, done) {
    try {
      await navigator.clipboard.writeText(text);
      hint(done || "Copied.");
    } catch (error) {
      showFailure("Could not copy", error);
    }
  }

  function openExternal(raw) {
    let url;
    try { url = new URL(raw); } catch (_) { return; }
    if (url.protocol !== "http:" && url.protocol !== "https:") return;
    if (url.origin === location.origin) {
      showAlert("Links to other pages do not resolve inside an artifact; it is one page (use #anchors).");
      return;
    }
    if (window.confirm(`Open this link from the artifact in a new tab?\n\n${url.href}`)) window.open(url.href, "_blank", "noopener,noreferrer");
  }

  function onFrameMessage(event) {
    if (!S.artifactFrame || event.source !== S.artifactFrame.contentWindow) return;
    const data = event.data;
    if (!data || data.fuseArtifact !== 1 || typeof data.type !== "string") return;
    const cur = S.currentArtifact;
    const key = cur ? `${cur.identifier}:${cur.version || ""}` : "";
    if (data.type === "error") {
      const message = String(data.message || "Error").slice(0, 600);
      const where = String(data.where || "").slice(0, 200);
      showAlert(`Page error: ${message.slice(0, 240)}${where ? ` (${where.slice(0, 60)})` : ""}`, { message, where });
    } else if (data.type === "open") {
      openExternal(String(data.url || ""));
    } else if (data.type === "copy") {
      copyText(String(data.text || "").slice(0, 200000), "Copied from the artifact.");
    } else if (data.type === "compose") {
      compose(String(data.text || "").slice(0, 20000));
      hint("Added from the artifact to your message.");
    } else if (data.type === "scroll" && key) {
      scrolls.set(key, Math.max(0, Number(data.y) || 0));
    } else if (data.type === "ready" && key && scrolls.get(key)) {
      S.artifactFrame.contentWindow.postMessage({ fuseArtifact: 1, type: "scroll", y: scrolls.get(key) }, "*");
    }
  }

  function fixWithModel() {
    const cur = S.currentArtifact;
    if (!fixError || !cur) return;
    const ver = viewing(cur);
    compose(`The artifact “${cur.title}” (${cur.identifier}${ver ? `, v${ver}` : ""}) shows this error in the browser: ${fixError.message}${fixError.where ? ` (at ${fixError.where})` : ""}. Please fix it.`);
    hideAlert();
  }

  async function act(name) {
    const cur = S.currentArtifact;
    closeMenu();
    if (!cur || !cur.identifier) return;
    const base = "/a/" + encodeURIComponent(cur.identifier);
    const post = (path, body) => net.api(base + path, { method: "POST", body: JSON.stringify(body || {}) });
    try {
      if (name === "share") {
        const data = await post("/visibility", { visibility: cur.visibility === "public" ? "private" : "public" });
        cur.visibility = data.visibility;
        cur.share_url = data.share_url;
        if (data.visibility === "public") await copyText(absolute(data.share_url), "Shared. Link copied: anyone with it can view the page.");
        else hint("Stopped sharing. The link no longer works for anyone else.");
      } else if (name === "copy-link") {
        await copyText(absolute(cur.visibility === "public" ? cur.share_url || cur.url : cur.url), cur.visibility === "public" ? "Share link copied." : "Link copied. Only you can open it.");
      } else if (name === "pin") {
        const data = await post("/pin", { version: cur.shared_version ? null : viewing(cur) });
        cur.shared_version = data.shared_version;
        hint(data.shared_version ? `Viewers now see v${data.shared_version} only.` : "Viewers now see the latest version.");
      } else if (name === "rotate") {
        if (!window.confirm("Reset the share link? The current link stops working for everyone.")) return;
        const data = await post("/rotate-link");
        cur.share_url = data.share_url;
        await copyText(absolute(data.share_url), "New share link copied. The old one no longer works.");
      } else if (name === "copy-source") {
        const res = await fetch(`${base}/source${cur.version ? `?v=${cur.version}` : ""}`, { credentials: "same-origin" });
        if (!res.ok) throw new Error("Could not load source");
        await copyText(await res.text(), "Source copied.");
      } else if (name === "diff") {
        const items = versionsOf(cur);
        const at = viewing(cur);
        const prev = items[items.findIndex((item) => item.version === at) - 1];
        if (!prev) return;
        S.artifactMode = "source";
        syncArtifactUi();
        await loadArtifactSource(prev.version);
        return;
      } else if (name === "revert") {
        const at = viewing(cur);
        const data = await post("/revert", { version: at });
        cur.version = null;
        hint(`Restored v${at} as v${data.version}.`);
        S.artifactSourceLoaded = "";
        loadFrame(true);
      }
      metaTag = "";
      await refreshMeta(S.generation, false);
    } catch (error) {
      showFailure("Artifact action failed", error);
    }
  }

  function closeMenu() {
    if (!el.menu || el.menu.hidden) return;
    el.menu.hidden = true;
    if (el.menuToggle) el.menuToggle.setAttribute("aria-expanded", "false");
  }

  function toggleMenu() {
    if (!el.menu) return;
    const open = el.menu.hidden;
    el.menu.hidden = !open;
    if (el.menuToggle) el.menuToggle.setAttribute("aria-expanded", open ? "true" : "false");
    if (open) el.menu.querySelector("button:not([hidden]), a:not([hidden])")?.focus();
  }

  // /artifacts: this chat's artifacts first, then the rest of the account's.
  async function openList() {
    S.artifactMode = "list";
    listItems = null;
    if (!S.artifactOpen) setArtifactOpen(true);
    else syncArtifactUi();
    renderList();
    try {
      const data = await net.api("/artifacts/list" + (S.currentId ? `?thread=${encodeURIComponent(S.currentId)}` : ""));
      listItems = Array.isArray(data.items) ? data.items : [];
    } catch (error) {
      listItems = [];
      showFailure("Could not list artifacts", error);
    }
    renderList();
    el.list?.querySelector("[data-artifact-pick]")?.focus({ preventScroll: true });
  }

  function renderList() {
    if (!el.list) return;
    if (listItems === null) {
      el.list.innerHTML = '<p class="chat-artifact-list-empty">Loading…</p>';
      return;
    }
    const here = new Set(distinctArtifacts().map((trace) => normalizeId(trace.identifier)));
    const items = listItems.map((item) => ({ ...item, in_thread: item.in_thread || here.has(item.identifier) }));
    const row = (item) => `<li class="chat-artifact-row">
        <button type="button" class="chat-artifact-row-open" data-artifact-pick="${escapeHtml(item.identifier)}" data-in-thread="${item.in_thread ? "1" : ""}">
          <span class="chat-artifact-row-icon" aria-hidden="true">${escapeHtml(item.icon || "▦")}</span>
          <span class="chat-artifact-row-copy"><span class="chat-artifact-row-title">${escapeHtml(item.title)}</span>
          <small>v${escapeHtml(String(item.version))}${item.visibility === "public" ? " · shared" : ""}</small></span>
        </button>
        <button type="button" class="chat-artifact-tool" data-artifact-copy="${escapeHtml(item.visibility === "public" ? item.share_url : item.url)}" aria-label="Copy link to ${escapeHtml(item.title)}">Copy link</button>
      </li>`;
    const group = (label, rows) => (rows.length ? `<h3 class="chat-artifact-list-head">${label}</h3><ul class="chat-artifact-rows">${rows.map(row).join("")}</ul>` : "");
    const body = group("This chat", items.filter((item) => item.in_thread)) + group(S.currentId ? "Other artifacts" : "Your artifacts", items.filter((item) => !item.in_thread));
    el.list.innerHTML = (body || '<p class="chat-artifact-list-empty">No artifacts yet. Ask for a chart, a dashboard or a document and it shows up here.</p>')
      + '<p class="chat-artifact-list-foot"><a href="/artifacts">Manage artifacts</a> · opening one from another chat attaches it here</p>';
  }

  async function pickFromList(id, inThread) {
    if (!inThread && S.currentId) {
      try {
        await net.api("/a/" + encodeURIComponent(id) + "/attach", { method: "POST", body: JSON.stringify({ thread_id: S.currentId }) });
      } catch (error) {
        showFailure("Could not attach the artifact", error);
      }
    }
    const item = (listItems || []).find((entry) => entry.identifier === id) || {};
    await showArtifact(id, item, { explicit: true });
  }

  // The latest published version of each artifact this transcript shows.
  function versionMarks() {
    const marks = new Map();
    artifactsInThread().forEach((trace) => {
      if (!WRITES.has(trace.name) || trace.status !== "done" || trace.version == null) return;
      const id = normalizeId(trace.identifier);
      const version = Number(trace.version) || 0;
      if (!marks.has(id) || marks.get(id).version < version) marks.set(id, { version, title: trace.title || "Artifact" });
    });
    return marks;
  }

  // Undo, rewind or delete cut later versions out of the chat, but the
  // artifact keeps them: offer to put it back where the chat now ends.
  function offerRestore(before) {
    if (!before || !window.fuseUI) return;
    const after = versionMarks();
    for (const [id, was] of before) {
      const now = after.get(id);
      if (!now || now.version >= was.version) continue;
      const toast = window.fuseUI.toast({
        key: `artifact-rewind:${id}`,
        kind: "idle",
        title: now.title,
        msg: `The chat now ends at v${now.version}; the artifact still has v${was.version}.`,
        actionLabel: `Restore v${now.version}`,
        ttl: 20000,
        onAction: async () => {
          try {
            const data = await net.api(`/a/${encodeURIComponent(id)}/revert`, { method: "POST", body: JSON.stringify({ version: now.version }) });
            toast.close();
            hint(`Restored v${now.version} of “${now.title}” as v${data.version}.`);
            if (S.artifactOpen && S.currentArtifact && S.currentArtifact.identifier === id) {
              S.currentArtifact.version = null;
              metaTag = "";
              loadFrame(true);
              refreshMeta(S.generation, false);
            }
          } catch (error) {
            showFailure("Could not restore the artifact", error);
          }
        },
      });
    }
  }

  function toggleLatest() {
    if (S.artifactOpen) {
      setArtifactOpen(false);
      return;
    }
    const cur = S.currentArtifact;
    if (cur && (cur.identifier || cur.building)) {
      if (cur.identifier) showArtifact(cur.identifier, cur, { keepMode: true }).catch((error) => showFailure("Could not open artifact", error));
      else setArtifactOpen(true);
      return;
    }
    const last = latestArtifact();
    if (last) showArtifact(last.identifier, last).catch((error) => showFailure("Could not open artifact", error));
    else openList();
  }

  function applyWidth(px) {
    if (!px || !S.root.style) return;
    S.root.style.setProperty("--chat-artifact", `${Math.round(px)}px`);
  }

  function bindResize() {
    if (!el.resize) return;
    try { applyWidth(Number(localStorage.getItem(WIDTH_KEY)) || 0); } catch (_) {}
    const clamp = (px) => Math.max(320, Math.min(px, S.root.getBoundingClientRect().width - 360));
    const save = (px) => { try { localStorage.setItem(WIDTH_KEY, String(Math.round(px))); } catch (_) {} };
    el.resize.addEventListener("pointerdown", (event) => {
      if (event.button !== 0) return;
      event.preventDefault();
      el.resize.setPointerCapture(event.pointerId);
      S.root.classList.add("is-artifact-resizing");
      const right = S.root.getBoundingClientRect().right;
      let width = 0;
      const move = (ev) => { width = clamp(right - ev.clientX); applyWidth(width); };
      const up = () => {
        el.resize.removeEventListener("pointermove", move);
        S.root.classList.remove("is-artifact-resizing");
        if (width) save(width);
      };
      el.resize.addEventListener("pointermove", move);
      el.resize.addEventListener("pointerup", up, { once: true });
      el.resize.addEventListener("pointercancel", up, { once: true });
    });
    el.resize.addEventListener("keydown", (event) => {
      if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
      event.preventDefault();
      const width = clamp(S.artifactPanel.getBoundingClientRect().width + (event.key === "ArrowLeft" ? 32 : -32));
      applyWidth(width);
      save(width);
    });
  }

  // Wires the pane's own controls; chat.js calls this once.
  function bind() {
    if (!panel) return;
    S.artifactToggle?.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
      toggleLatest();
    });
    S.artifactClose?.addEventListener("click", (event) => {
      event.preventDefault();
      setArtifactOpen(false);
    });
    S.artifactBackdrop?.addEventListener("click", () => setArtifactOpen(false));
    S.artifactModeBtns.forEach((btn) => btn.addEventListener("click", (event) => {
      event.preventDefault();
      setMode(btn.getAttribute("data-artifact-mode") || "preview");
    }));
    S.artifactVersionEl?.addEventListener("change", () => {
      const cur = S.currentArtifact;
      if (!cur || !cur.identifier) return;
      const picked = Number(S.artifactVersionEl.value) || null;
      cur.version = picked && picked !== cur.current ? picked : null;
      S.artifactSourceLoaded = "";
      loadFrame(false);
      if (S.artifactMode === "source") loadArtifactSource().catch((error) => showFailure("Could not load source", error));
      syncArtifactUi();
    });
    el.listToggle?.addEventListener("click", () => {
      if (S.artifactMode === "list") {
        const cur = S.currentArtifact;
        setMode("preview");
        if (!(cur && cur.identifier)) setArtifactOpen(false);
      } else openList();
    });
    el.reload?.addEventListener("click", () => {
      S.artifactSourceLoaded = "";
      if (S.artifactMode === "source") loadArtifactSource().catch((error) => showFailure("Could not load source", error));
      else loadFrame(true);
    });
    el.menuToggle?.addEventListener("click", (event) => {
      event.stopPropagation();
      toggleMenu();
    });
    el.menu?.addEventListener("click", (event) => {
      const item = event.target.closest("[data-artifact-act]");
      if (!item || item.tagName === "A") {
        if (item) closeMenu();
        return;
      }
      event.preventDefault();
      act(item.getAttribute("data-artifact-act"));
    });
    el.auto?.addEventListener("change", () => {
      try { localStorage.setItem(AUTO_KEY, el.auto.checked ? "1" : "0"); } catch (_) {}
    });
    el.expand?.addEventListener("click", () => {
      const wide = !S.root.classList.contains("is-artifact-wide");
      S.root.classList.toggle("is-artifact-wide", wide);
      el.expand.setAttribute("aria-pressed", wide ? "true" : "false");
    });
    el.fix?.addEventListener("click", fixWithModel);
    el.alertClose?.addEventListener("click", hideAlert);
    el.list?.addEventListener("click", (event) => {
      const copy = event.target.closest("[data-artifact-copy]");
      if (copy) {
        copyText(absolute(copy.getAttribute("data-artifact-copy")), "Link copied.");
        return;
      }
      const pick = event.target.closest("[data-artifact-pick]");
      if (pick) pickFromList(pick.getAttribute("data-artifact-pick"), pick.getAttribute("data-in-thread") === "1").catch((error) => showFailure("Could not open artifact", error));
    });
    document.addEventListener("click", (event) => {
      if (el.menu && !el.menu.hidden && !el.menu.contains(event.target) && event.target !== el.menuToggle) closeMenu();
    });
    document.addEventListener("keydown", (event) => {
      if ((event.ctrlKey || event.metaKey) && !event.altKey && !event.shiftKey && event.key === "]") {
        event.preventDefault();
        toggleLatest();
      } else if (event.key === "Escape" && el.menu && !el.menu.hidden) {
        event.preventDefault();
        event.stopPropagation();
        closeMenu();
        el.menuToggle?.focus();
      }
    }, true);
    document.addEventListener("visibilitychange", syncPoll);
    window.addEventListener("message", onFrameMessage);
    bindResize();
  }

  return {
    artifactsInThread,
    bind,
    distinctArtifacts,
    latestArtifact,
    loadArtifactSource,
    noteArtifactResult,
    noteDrafting,
    offerRestore,
    openList,
    versionMarks,
    rememberLatestArtifact,
    setArtifactOpen,
    showArtifact,
    startArtifactBuild,
    syncArtifactUi,
    artifactContentUrl,
  };
}

export { artifactContentUrl, normalizeId };
