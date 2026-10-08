// History list rendering, tabs, search.
import { S } from '../state.js';
import { escapeHtml, safeUrl } from '../core.js';

function sourceCatalog(msg) {
  const urls = [];
  const seen = new Set();
  const add = (raw) => {
    const url = safeUrl(raw);
    if (!url || !/^https?:/i.test(url) || seen.has(url)) return;
    seen.add(url);
    urls.push(url);
  };
  (msg && msg.tools ? msg.tools : []).forEach((trace) => {
    (trace.results || []).forEach((row) => add(row && row.url));
    if (trace.name === "read_url") add(trace.url);
  });
  return urls;
}

function runningFirst(a, b) {
  const ra = Number(Boolean(a.running));
  const rb = Number(Boolean(b.running));
  if (ra !== rb) return rb - ra;
  return String(b.updated_at).localeCompare(String(a.updated_at));
}

function pinnedFirst(a, b) {
  return (Number(a.pin_order) || 0) - (Number(b.pin_order) || 0);
}

// Pinned rows leave the timeline for their own section at the top, in the
// order drag-and-drop wrote. Everything else keeps the running-first order.
function splitPinned(items) {
  return {
    pinned: items.filter((item) => item.pinned).sort(pinnedFirst),
    rest: items.filter((item) => !item.pinned),
  };
}

// Hairline between the pinned section and the timeline below it.
const PINNED_SEP = '<div class="chat-history-sep" aria-hidden="true"></div>';

function historyIcons() {
  return {
    folder:
      '<svg class="chat-history-glyph" viewBox="0 0 16 16" width="13" height="13" aria-hidden="true"><path d="M2.6 4.7h3.2l1.1 1.4H13.4v6.4H2.6z" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"/></svg>',
    chat:
      '<svg class="chat-history-glyph" viewBox="0 0 16 16" width="13" height="13" aria-hidden="true"><path d="M3.2 3.6h9.6v7.2H6.4L3.2 13.4z" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"/></svg>',
  };
}

export function createRender(ctx) {
  const { composer, net, overlays, projects, spinners, sync, threads } = ctx;
    function providerLogo(type) {
    const src = S.logoByType.get(type) || "/static/icon.svg";
    return `<img class="provider-logo" src="${escapeHtml(src)}" alt="" aria-hidden="true" draggable="false">`;
  }

    function setHistoryTab(tab, opts) {
    const next = tab === "projects" ? "projects" : "chats";
    const persist = !opts || opts.persist !== false;
    const silent = Boolean(opts && opts.silent);
    if (next !== S.historyTab) S.showArchived = false;
    S.historyTab = next;
    if (persist) {
      try { localStorage.setItem(S.STORE_TAB, next); } catch (_) {}
    }
    syncHistoryChrome();
    if (!silent) renderHistory();
  }

    function syncHistoryChrome() {
    S.root.querySelectorAll("[data-history-tab]").forEach((btn) => {
      const on = btn.getAttribute("data-history-tab") === S.historyTab;
      btn.setAttribute("aria-selected", String(on));
      btn.classList.toggle("is-on", on);
    });
    syncHistoryUnread();
    const heading = S.root.querySelector("[data-history-heading]");
    if (heading) heading.textContent = S.historyTab === "projects" ? "Projects" : "Chats";
    const newLabel = S.root.querySelector("[data-chat-new-label]");
    const newBtn = S.root.querySelector("[data-chat-new]");
    const projectLabel = S.historyTab === "projects";
    if (newLabel) newLabel.textContent = projectLabel ? "New project" : "New chat";
    if (newBtn) {
      newBtn.setAttribute("aria-label", projectLabel ? "New project session" : "New chat");
      newBtn.title = projectLabel ? "New project session" : "New chat";
      if (projectLabel) newBtn.setAttribute("aria-haspopup", "dialog");
      else newBtn.removeAttribute("aria-haspopup");
    }
    const filters = S.root.querySelector("[data-project-filters]");
    if (filters) {
      filters.hidden = S.historyTab !== "projects" || Boolean(S.historyQuery.trim()) || S.showArchived;
    }
    projects.fillProjectFilterOptions();
    composer.syncComposer();
  }

    // Unread counts for the tab switch: a finished conversation badges its
  // tab so the other tab's results are visible before switching.
  function syncHistoryUnread() {
    const counts = { chats: 0, projects: 0 };
    S.threads.forEach((item) => {
      if (!item.unread || item.id === S.currentId) return;
      counts[item.kind === "project" ? "projects" : "chats"] += 1;
    });
    S.root.querySelectorAll("[data-history-tab]").forEach((btn) => {
      const tab = btn.getAttribute("data-history-tab");
      const badge = btn.querySelector("[data-history-unread]");
      if (!badge) return;
      const count = counts[tab] || 0;
      badge.hidden = count === 0 || tab === S.historyTab;
      badge.textContent = count > 9 ? "9+" : String(count);
    });
  }

    // Archived threads belong to the tab of their kind: project sessions under
  // Projects, everything else under Chats.
  function archivedForTab() {
    const projectTab = S.historyTab === "projects";
    return S.archivedThreads.filter((item) => (item.kind === "project") === projectTab);
  }

    function archivedProjects() {
    return S.historyTab === "projects" ? S.projects.filter((item) => item.archived) : [];
  }

    function filteredHistory() {
    const source = S.showArchived ? archivedForTab() : S.threads.filter((item) => item.kind !== "project");
    const q = S.historyQuery.trim().toLowerCase();
    if (!q) return [...source].sort(runningFirst);
    if (S.searchActiveQuery && S.searchActiveQuery.toLowerCase() === q) {
      return [...S.searchThreads, ...S.searchArchived].sort(runningFirst);
    }
    return [...source].sort(runningFirst).filter((item) => (item.title || "").toLowerCase().includes(q));
  }

    function historyRow(item, grouped = false) {
    const on = item.id === S.currentId ? " is-on" : "";
    const project = item.kind === "project";
    const running = spinners.threadIsRunning(item);
    const unread = Boolean(item.unread) && item.id !== S.currentId;
    let sessionTitle = item.title || "New chat";
    if (project && item.project_name && sessionTitle === item.project_name) sessionTitle = "New chat";
    const title = escapeHtml(sessionTitle);
    const snip = item.snippet ? `<span class="chat-history-snip">${escapeHtml(item.snippet)}</span>` : "";
    const chip = searchingChip(item);
    const meta =
      project && !grouped ? projects.projectMeta(item.project_name || "Project", item.host_alias) : "";
    const dot = unread ? '<span class="chat-history-unread-dot" role="img" aria-label="Unread activity"></span>' : "";
    const age = running ? spinners.runSpinMarkup("h-" + item.id) : spinners.ageChip(item);
    const lead = project
      ? age + `<span class="chat-history-indicator is-folder">${projects.projectIcon({ color: item.project_color, logo_url: item.project_logo_url })}${dot}</span>`
      : `<span class="chat-history-indicator">${age}${dot}</span>`;
    const groupedClass = project && grouped ? " is-grouped" : "";
    const runningClass = running ? " is-running" : "";
    const busy = running ? ' aria-busy="true"' : "";
    const working = running ? '<span class="visually-hidden">Working</span>' : "";
    const twoLine = Boolean(item.snippet || meta);
    const scheduledClass = item.schedule_id ? " is-task" : "";
    const pinClass = item.pinned ? " is-pinned" : "";
    const unreadClass = unread ? " is-unread" : "";
    return `<div class="chat-history-item${on}${twoLine ? " has-snip" : ""}${project ? " is-project" : ""}${groupedClass}${runningClass}${scheduledClass}${pinClass}${unreadClass}" data-thread="${escapeHtml(String(item.id))}"${busy}><button type="button" class="open" title="${title}">${working}<span class="chat-history-title">${lead}<span class="chat-history-copy"><span class="chat-history-name">${title}</span>${meta}${snip}</span></span>${chip}</button><button type="button" class="more" aria-label="Chat actions">⋯</button></div>`;
  }

    function searchingChip(item) {
    const searching = Boolean(S.historyQuery.trim());
    if (!searching) return "";
    if (item.archived) return '<span class="chat-history-chip">Archived</span>';
    if (item.pinned) return '<span class="chat-history-chip">Pinned</span>';
    if (item.kind === "project") return '<span class="chat-history-chip">Project</span>';
    return "";
  }

    function syncHistoryFooter() {
    // A repaint replaced the row nodes; put the open menu's flag back.
    if (S.popRowKey && S.historyList) {
      const [attr, key] = S.popRowKey;
      for (const el of S.historyList.querySelectorAll(".chat-history-item")) {
        if (el.getAttribute(attr) === key) { el.classList.add("is-menu"); break; }
      }
    }
    const footer = S.root.querySelector(".chat-history-footer");
    if (!footer) return;
    footer.hidden = false;
    spinners.syncRunSpinners();
  }

    function renderHistory() {
    if (!S.historyList) return;
    if (S.renaming) return;
    // A repaint would rebuild the nodes under an active pin drag; the drop
    // handler re-renders from the saved order.
    if (S.pinDrag && S.pinDrag.dragging) return;
    const searching = Boolean(S.historyQuery.trim());
    syncHistoryChrome();
    if (S.historyKicker) {
      if (searching || S.showArchived) {
        S.historyKicker.textContent = searching ? "Results" : "Archived";
        S.historyKicker.hidden = false;
      } else {
        S.historyKicker.hidden = true;
      }
    }
    const archived = archivedForTab();
    if (S.archivedBtn) {
      S.archivedBtn.hidden = searching || (archived.length === 0 && archivedProjects().length === 0 && !S.showArchived);
      S.archivedBtn.classList.toggle("is-on", S.showArchived);
      S.archivedBtn.setAttribute("aria-pressed", String(S.showArchived));
      const label = S.archivedBtn.querySelector("[data-history-archived-label]");
      const count = S.archivedBtn.querySelector("[data-history-archived-count]");
      if (label) label.textContent = S.showArchived ? (S.historyTab === "projects" ? "Back to projects" : "Back to chats") : "Archived";
      if (count) count.textContent = S.showArchived ? "" : String(archived.length + archivedProjects().length);
    }
    if (searching) {
      const q = S.historyQuery.trim().toLowerCase();
      const nativeHits = S.searchActiveQuery && S.searchActiveQuery.toLowerCase() === q
        ? S.searchProjects
        : S.projects.filter((item) =>
            (item.name || "").toLowerCase().includes(q) ||
            (item.host_alias || "").toLowerCase().includes(q) ||
            (item.remote_path || "").toLowerCase().includes(q)
          );
      const chats = (S.searchActiveQuery && S.searchActiveQuery.toLowerCase() === q
        ? [...S.searchThreads, ...S.searchArchived]
        : [...filteredHistory(), ...S.threads.filter((item) => item.kind === "project" && (item.title || "").toLowerCase().includes(q))]
      ).sort(runningFirst);
      const threadProjectKeys = new Set(
        chats.filter((item) => item.kind === "project").map((item) => projects.projectKey(item.host_id, item.project_id))
      );
      const unusedHits = nativeHits.filter((item) => !threadProjectKeys.has(projects.projectKey(item.host_id, item.id)));
      let html = "";
      if (chats.length) html += chats.map((item) => historyRow(item)).join("");
      if (unusedHits.length) {
        if (chats.length) html += `<p class="chat-history-kicker">Projects</p>`;
        html += unusedHits.map(projects.projectRow).join("");
      }
      S.historyList.innerHTML = html || `<p class="chat-history-empty">No chats or projects match your search.</p>`;
      syncHistoryFooter();
      return;
    }
    if (S.showArchived) {
      const shelved = archivedProjects();
      let html = "";
      if (shelved.length) {
        html += `<p class="chat-history-kicker">Projects</p>` + shelved.map(projects.projectRow).join("");
        if (archived.length) html += `<p class="chat-history-kicker">Sessions</p>`;
      }
      html += archived.map((item) => historyRow(item)).join("");
      S.historyList.innerHTML = html ||
        `<p class="chat-history-empty">${S.historyTab === "projects" ? "No archived projects or sessions." : "No archived chats."}</p>`;
      syncHistoryFooter();
      return;
    }
    if (S.historyTab === "projects") {
      const sessions = projects.projectSessionList();
      const { pinned, rest } = splitPinned(sessions);
      if (!sessions.length) {
        S.historyList.innerHTML =
          `<p class="chat-history-empty">No project sessions yet. Use New project to start one.</p>`;
        syncHistoryFooter();
        return;
      }
      let html = "";
      if (pinned.length) html += `<p class="chat-history-kicker">Pinned</p>` + pinned.map((item) => historyRow(item)).join("") + (rest.length ? PINNED_SEP : "");
      if (S.projectSort === "project") {
        projects.groupedProjectSessions(rest).forEach((group) => {
          html +=
            `<div class="chat-history-project-head">` +
            projects.projectIcon(group.icon) +
            `<span class="chat-history-project">${escapeHtml(group.name)}</span>` +
            (group.host ? `<span class="chat-history-host-label">${escapeHtml(group.host)}</span>` : "") +
            `</div>`;
          html += group.items.map((item) => historyRow(item, true)).join("");
        });
      } else {
        html += rest.map((item) => historyRow(item)).join("");
      }
      S.historyList.innerHTML = html;
      syncHistoryFooter();
      return;
    }
    const items = filteredHistory();
    if (!items.length && !S.historyLoaded) {
      S.historyList.innerHTML = `<div class="chat-history-loading" aria-busy="true" aria-label="Loading chats">${"<span></span>".repeat(6)}</div>`;
    } else if (!items.length) {
      S.historyList.innerHTML = `<p class="chat-history-empty">No chats yet.</p>`;
    } else {
      const { pinned, rest } = splitPinned(items);
      S.historyList.innerHTML =
        (pinned.length ? `<p class="chat-history-kicker">Pinned</p>` + pinned.map((item) => historyRow(item)).join("") + (rest.length ? PINNED_SEP : "") : "") +
        rest.map((item) => historyRow(item)).join("");
    }
    syncHistoryFooter();
  }

    function resetSearchLists() {
    S.searchActiveQuery = "";
    S.searchThreads = [];
    S.searchArchived = [];
    S.searchProjects = [];
  }

    function setSearchOpen(open) {
    const top = S.root.querySelector(".chat-history-top");
    const row = S.root.querySelector("[data-history-row]");
    if (!top || !row || !S.historySearch || !S.searchToggle) return;
    const was = S.searchOpen;
    S.searchOpen = open;
    top.classList.toggle("is-searching", open);
    S.searchToggle.setAttribute("aria-expanded", String(open));
    if (open && !was) overlays.overlayPush("search");
    else if (!open && was) overlays.overlayDrop("search");
    if (open) {
      row.hidden = false;
      S.historySearch.focus();
      S.historySearch.select();
    } else {
      row.hidden = true;
      if (S.historyQuery) {
        S.historyQuery = "";
        S.historySearch.value = "";
        resetSearchLists();
        renderHistory();
      }
      S.searchToggle.focus({ preventScroll: true });
    }
  }

    function queueSearch() {
    window.clearTimeout(S.searchTimer);
    const gen = ++S.searchGen;
    const q = S.historyQuery.trim();
    if (!q) {
      resetSearchLists();
      renderHistory();
      return;
    }
    S.searchTimer = window.setTimeout(async () => {
      try {
        const data = await net.api("/chat/threads?q=" + encodeURIComponent(q));
        if (gen !== S.searchGen) return;
        S.searchActiveQuery = q;
        S.searchThreads = data.threads || [];
        S.searchArchived = data.archived || [];
        S.searchProjects = data.projects || [];
        renderHistory();
      } catch (error) {
        if (gen === S.searchGen) sync.showFailure("Chat search failed", error);
      }
    }, 250);
  }

    async function refreshThreads() {
    const before = S.cursor;
    const data = await net.api("/chat/threads");
    if (S.cursor !== before) return;
    S.historyLoaded = true;
    S.threads = data.threads || [];
    S.archivedThreads = data.archived || [];
    spinners.noteServerRunning(S.threads);
    spinners.noteServerRunning(S.archivedThreads);
    if (Array.isArray(data.projects)) S.projects = data.projects;
    threads.syncTitle();
    renderHistory();
  }

  return {
    filteredHistory,
    providerLogo,
    queueSearch,
    refreshThreads,
    renderHistory,
    resetSearchLists,
    setHistoryTab,
    setSearchOpen,
    syncHistoryChrome,
    historyIcons,
    runningFirst,
    sourceCatalog,
  };
}

export { historyIcons, runningFirst, sourceCatalog };
