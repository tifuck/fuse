// Projects: rows, pickers, menus, move-to-project.
import { S } from '../state.js';
import { escapeHtml } from '../core.js';
import { newSession } from '../state.js';

function projectMeta(name, host) {
  const hostLabel = host
    ? `<span class="chat-history-host-label">${escapeHtml(host)}</span>`
    : "";
  return `<span class="chat-history-meta"><span class="chat-history-project">${escapeHtml(name || "Project")}</span>${hostLabel}</span>`;
}

function projectKey(hostId, projectId) {
  return `${hostId}:${projectId}`;
}

function projectWorkspaceDetails(info) {
  const details = [];
  if (info.host_alias) details.push("Host: " + info.host_alias);
  if (info.remote_path) details.push("Path: " + info.remote_path);
  if (info.permission_mode) {
    const permission = { read_only: "Read-only", project: "Project-scoped" }[info.permission_mode] || info.permission_mode;
    details.push("Project policy: " + permission);
  }
  return details;
}

function groupedProjectSessions(items) {
  const byKey = new Map();
  items.forEach((item) => {
    const key = projectKey(item.host_id, item.project_id);
    if (!byKey.has(key)) {
      byKey.set(key, {
        name: item.project_name || "Project",
        host: item.host_alias || "",
        icon: { color: item.project_color, logo_url: item.project_logo_url },
        items: [],
      });
    }
    byKey.get(key).items.push(item);
  });
  return [...byKey.values()];
}

export function createProjects(ctx) {
  const { chatApi, composer, drawer, net, overlays, picker, render, spinners, sync, threads, undo } = ctx;
    // A project's folder in its own color, or its logo. Falls back to the
  // plain glyph if the shared helper failed to load.
  function projectIcon(project, size) {
    if (!window.FuseProjects) return render.historyIcons().folder;
    return window.FuseProjects.icon(project || {}, { size: size || 13, className: "chat-history-glyph" });
  }

    function projectRow(item) {
    const bound = threads.sessionProject();
    const on =
      bound &&
      Number(bound.project_id) === Number(item.id) &&
      Number(bound.host_id) === Number(item.host_id)
        ? " is-on"
        : "";
    const running = spinners.threadIsRunning(item);
    const name = item.name || "Project";
    const hostLabel = item.host_alias
      ? `<span class="chat-history-host-label">${escapeHtml(item.host_alias)}</span>`
      : "";
    const path = item.remote_path
      ? `<span class="chat-history-meta"><span class="chat-history-project">${escapeHtml(item.remote_path)}</span>${hostLabel}</span>`
      : "";
    const chip = item.archived
      ? '<span class="chat-history-chip">Archived</span>'
      : S.historyQuery.trim() ? '<span class="chat-history-chip">Project</span>' : "";
    const threadAttr = item.thread_id ? ` data-thread="${escapeHtml(String(item.thread_id))}"` : "";
    const lead =
      (running ? spinners.runSpinMarkup("p-" + item.host_id + "-" + item.id) : spinners.ageChip(item)) +
      projectIcon(item);
    const runningClass = running ? " is-running" : "";
    const busy = running ? ' aria-busy="true"' : "";
    const working = running ? '<span class="visually-hidden">Working</span>' : "";
    const twoLine = Boolean(item.remote_path);
    return `<div class="chat-history-item is-project${on}${twoLine ? " has-snip" : ""}${runningClass}" data-project="${item.host_id}:${item.id}"${threadAttr}${busy}><button type="button" class="open" title="${escapeHtml(name)}"${on ? ' aria-current="true"' : ""}>${working}<span class="chat-history-title">${lead}<span class="chat-history-copy"><span class="chat-history-name">${escapeHtml(name)}</span>${path}</span></span>${chip}</button><button type="button" class="more" aria-label="Actions for project ${escapeHtml(name)}" aria-haspopup="menu" aria-expanded="false">⋯</button></div>`;
  }

    function fillProjectFilterOptions() {
    const select = S.root.querySelector("[data-project-filter]");
    if (!select) return;
    const seen = new Map();
    S.projects.forEach((item) => {
      if (item.archived) return;
      const key = projectKey(item.host_id, item.id);
      seen.set(key, item.host_alias ? `${item.name || "Project"} · ${item.host_alias}` : (item.name || "Project"));
    });
    S.threads.forEach((item) => {
      if (item.kind !== "project" || item.project_id == null) return;
      const key = projectKey(item.host_id, item.project_id);
      if (!seen.has(key)) {
        seen.set(key, item.host_alias ? `${item.project_name || "Project"} · ${item.host_alias}` : (item.project_name || "Project"));
      }
    });
    const current = S.projectFilter;
    const options = [...seen.entries()].sort((a, b) => a[1].localeCompare(b[1]));
    select.innerHTML =
      `<option value="">All projects</option>` +
      options.map(([key, label]) => `<option value="${escapeHtml(key)}">${escapeHtml(label)}</option>`).join("");
    if (current && seen.has(current)) select.value = current;
    else {
      S.projectFilter = "";
      select.value = "";
    }
    const sortEl = S.root.querySelector("[data-project-sort]");
    if (sortEl) sortEl.value = S.projectSort;
  }

    function projectSessionList(drafts = []) {
    let items = [...drafts, ...S.threads.filter((item) => item.kind === "project")];
    if (S.projectFilter) {
      items = items.filter((item) => projectKey(item.host_id, item.project_id) === S.projectFilter);
    }
    if (S.projectSort === "project") {
      items.sort((a, b) => {
        const ka = `${a.host_alias || ""} / ${a.project_name || ""}`;
        const kb = `${b.host_alias || ""} / ${b.project_name || ""}`;
        const cmp = ka.localeCompare(kb);
        if (cmp) return cmp;
        return render.runningFirst(a, b);
      });
    } else {
      items.sort(render.runningFirst);
    }
    return items;
  }

    async function openProject(hostId, projectId) {
    if (S.sending) return;
    if (!net.requireLive()) return;
    const hid = Number(hostId);
    const pid = Number(projectId);
    const existing =
      S.threads.find((item) => item.kind === "project" && Number(item.host_id) === hid && Number(item.project_id) === pid) ||
      S.archivedThreads.find((item) => item.kind === "project" && Number(item.host_id) === hid && Number(item.project_id) === pid);
    if (existing) {
      await threads.openThread(existing.id);
      return;
    }
    await threads.startProjectThread(hostId, projectId);
  }

    async function openProjectPicker() {
    if (S.sending || !window.FuseProjects) return;
    const bound = threads.sessionProject();
    const picked = await window.FuseProjects.openChooser({
      title: "Start a project session",
      current: bound ? { host_id: bound.host_id, project_id: bound.project_id } : null,
    });
    if (!picked) return;
    if (picked.created || picked.archived === false) await render.refreshThreads().catch(() => {});
    if (!net.requireLive()) return;
    if (S.historyMobile.matches) drawer.setHistoryOpen(false);
    await threads.startProjectThread(picked.host_id, picked.id, picked);
  }

    function resolveProjectInfo(hostId, projectId, extra) {
    const hid = Number(hostId);
    const pid = Number(projectId);
    if (!Number.isFinite(hid) || !Number.isFinite(pid)) return null;
    const native = S.projects.find((item) => Number(item.host_id) === hid && Number(item.id) === pid);
    if (native) {
      return {
        host_id: hid,
        project_id: pid,
        host_alias: native.host_alias || "",
        project_name: native.name || "Project",
        remote_path: native.remote_path || "",
        permission_mode: native.permission_mode || "",
        default_permissions: native.default_permissions || "",
        default_model: native.default_model || "",
        color: native.color || "",
        logo_url: native.logo_url || "",
      };
    }
    const host = threads.pickerHostById(hostId);
    const proj = host && (host.projects || []).find((item) => Number(item.id) === pid);
    if (host && proj) {
      return {
        host_id: hid,
        project_id: pid,
        host_alias: host.alias || "",
        project_name: proj.name || "Project",
        remote_path: proj.remote_path || "",
        permission_mode: proj.permission_mode || "",
        default_permissions: proj.default_permissions || "",
        default_model: proj.default_model || "",
        color: proj.color || "",
        logo_url: proj.logo_url || "",
      };
    }
    if (extra && (extra.id == null || Number(extra.id) === pid)) {
      return {
        host_id: hid,
        project_id: pid,
        host_alias: (host && host.alias) || extra.host_alias || "",
        project_name: extra.name || extra.project_name || "Project",
        remote_path: extra.remote_path || "",
        permission_mode: extra.permission_mode || "",
        default_permissions: extra.default_permissions || "",
        default_model: extra.default_model || "",
        color: extra.color || "",
        logo_url: extra.logo_url || "",
      };
    }
    return null;
  }

    function sidebarProject(hostId, projectId) {
    return S.projects.find((item) => Number(item.host_id) === Number(hostId) && Number(item.id) === Number(projectId));
  }

    function boundTo(hostId, projectId) {
    const bound = threads.sessionProject();
    return Boolean(bound && Number(bound.host_id) === Number(hostId) && Number(bound.project_id) === Number(projectId));
  }

    async function editProject(hostId, projectId) {
    if (!window.FuseProjects) return;
    const item = sidebarProject(hostId, projectId);
    const saved = await window.FuseProjects.openEditor({
      hostId,
      project: item
        ? { ...item, host_id: Number(hostId) }
        : { id: Number(projectId), host_id: Number(hostId), name: "Project", remote_path: "" },
    });
    if (!saved) return;
    if (S.pendingProject && boundTo(hostId, projectId)) {
      S.pendingProject = { ...S.pendingProject, project_name: saved.name, remote_path: saved.remote_path,
        permission_mode: saved.permission_mode, color: saved.color, logo_url: saved.logo_url };
    }
    await render.refreshThreads();
  }

    function projectMenu(anchor, key, at) {
    const [hostId, projectId] = String(key || "").split(":");
    const item = sidebarProject(hostId, projectId);
    if (!item || !net.requireLive()) return;
    const items = [
      {
        label: "New session",
        run: async () => {
          if (S.historyMobile.matches) drawer.setHistoryOpen(false);
          await threads.startProjectThread(hostId, projectId, item);
        },
      },
      { label: "Edit project", run: () => editProject(hostId, projectId) },
      {
        label: item.archived ? "Unarchive project" : "Archive project",
        run: async () => {
          await net.api(`/hosts/${hostId}/projects/${projectId}`, {
            method: "PATCH",
            body: JSON.stringify({ archived: !item.archived }),
          });
          if (!item.archived && boundTo(hostId, projectId)) chatApi.clearChatState({ keepList: true });
          await render.refreshThreads();
        },
      },
      {
        label: "Delete project",
        danger: true,
        run: async () => {
          const n = Number(item.sessions || 0);
          const what = n ? ` and its ${n === 1 ? "session" : n + " sessions"}` : "";
          const ok = await undo.askConfirm(
            `Delete project "${item.name || "Project"}"${what}? The folder on ${item.host_alias || "the machine"} is not touched. This cannot be undone.`,
            { confirmLabel: "Delete", danger: true }
          );
          if (!ok) return;
          const wasBound = boundTo(hostId, projectId);
          await net.api(`/hosts/${hostId}/projects/${projectId}?sessions=delete`, { method: "DELETE" });
          if (wasBound) chatApi.clearChatState({ keepList: true });
          await render.refreshThreads();
        },
      },
    ];
    overlays.showPop(anchor, items, at);
  }

    async function moveToProject(id) {
    const thread = threads.threadById(id);
    if (!thread || !window.FuseProjects) return;
    const picked = await window.FuseProjects.openChooser({
      title: "Move to project",
      pickLabel: "Move here",
      current: thread.kind === "project" && thread.project_id != null
        ? { host_id: thread.host_id, project_id: thread.project_id } : null,
    });
    if (!picked || !net.requireLive()) return;
    if (thread.kind === "project" && Number(thread.host_id) === Number(picked.host_id)
        && Number(thread.project_id) === Number(picked.id)) return;
    const nav = S.navigation;
    await sync.flushPending();
    const latest = threads.threadById(id) || thread;
    const updated = await net.api("/chat/threads/" + id + "/project", {
      method: "POST",
      body: JSON.stringify({
        host_id: picked.host_id,
        project_id: picked.id,
        expected_revision: latest.revision,
        command_id: newSession(),
      }),
    });
    if (id === S.currentId) S.serverRevision = Math.max(S.serverRevision, updated.revision);
    threads.upsertLocal(updated);
    render.renderHistory();
    picker.setHint(`Moved to ${picked.name || "the project"}. The next turn runs in ${picked.remote_path || "its folder"}.`);
    await render.refreshThreads();
    if (nav === S.navigation && id === S.currentId) {
      threads.syncTitle();
      composer.syncComposer();
      sync.reconcileThread().catch(sync.syncFailure);
    }
  }

  return {
    editProject,
    fillProjectFilterOptions,
    moveToProject,
    openProject,
    openProjectPicker,
    projectIcon,
    projectMenu,
    projectRow,
    projectSessionList,
    resolveProjectInfo,
    sidebarProject,
    groupedProjectSessions,
    projectKey,
    projectMeta,
    projectWorkspaceDetails,
  };
}

export { groupedProjectSessions, projectKey, projectMeta, projectWorkspaceDetails };
