// Thread navigation: open, list mutation, rename, session project.
import { S } from '../state.js';
import { newSession, resolveListedModel } from '../state.js';

function isUntitledThread(current) {
  if (!current) return true;
  const title = (current.title || "").trim();
  if (!title || title.toLowerCase() === "new chat") return true;
  if (current.kind === "project" && current.project_name &&
      title.toLowerCase() === String(current.project_name).trim().toLowerCase()) return true;
  return false;
}

export function createThreads(ctx) {
  const { agents, artifacts, chatApi, composer, context, drawer, net, overlays, panels, perms, picker, projects, render, spinners, sync, traces, transcript, turns, undo } = ctx;
    function upsertLocal(thread) {
    if (!thread || !thread.id) return;
    const bucket = thread.archived ? S.archivedThreads : S.threads;
    const other = thread.archived ? S.threads : S.archivedThreads;
    const idx = bucket.findIndex((item) => item.id === thread.id);
    const prior = idx >= 0 ? bucket[idx] : other.find((item) => item.id === thread.id);
    let running;
    if (Object.prototype.hasOwnProperty.call(thread, "running")) S.serverRunning.set(thread.id, Boolean(thread.running));
    if (thread.id === S.currentId) running = spinners.currentThreadBusy();
    else if (thread.run && thread.run.cancel_requested) running = false;
    else if (Object.prototype.hasOwnProperty.call(thread, "running")) running = Boolean(thread.running);
    else if (thread.run && ["queued", "running"].includes(thread.run.status)) running = true;
    else running = Boolean(prior && prior.running);
    const lite = {
      id: thread.id,
      revision: thread.revision,
      title: thread.title,
      model: thread.model,
      archived: thread.archived,
      created_at: thread.created_at,
      kind: thread.kind || "chat",
      host_id: thread.host_id ?? null,
      project_id: thread.project_id ?? null,
      host_alias: thread.host_alias || "",
      project_name: thread.project_name || "",
      project_color: thread.project_color ?? prior?.project_color ?? "",
      project_logo_url: thread.project_logo_url ?? prior?.project_logo_url ?? "",
      schedule_id: thread.schedule_id ?? prior?.schedule_id ?? null,
      updated_at: thread.updated_at,
      pinned: thread.pinned ?? prior?.pinned ?? false,
      pin_order: thread.pin_order ?? prior?.pin_order ?? 0,
      unread: thread.unread ?? prior?.unread ?? false,
      running,
    };
    if (idx >= 0) bucket[idx] = lite;
    else bucket.unshift(lite);
    const drop = other.findIndex((item) => item.id === thread.id);
    if (drop >= 0) other.splice(drop, 1);
    bucket.sort((a, b) => String(b.updated_at).localeCompare(String(a.updated_at)));
    if (!thread.archived && thread.kind === "project" && thread.project_archived === false) {
      const project = projects.sidebarProject(thread.host_id, thread.project_id);
      if (project) project.archived = false;
    }
    if (!thread.archived && prior?.archived && thread.id === S.currentId) S.showArchived = false;
    if (thread.kind === "project" && thread.id === S.currentId && !S.historyQuery) {
      render.setHistoryTab("projects", { persist: true, silent: true });
    }
    syncTitle();
  }

    function dropThread(id) {
    if (id == null) return;
    S.pendingDeletes.delete(id);
    S.threads = S.threads.filter((item) => item.id !== id);
    S.archivedThreads = S.archivedThreads.filter((item) => item.id !== id);
    S.searchThreads = S.searchThreads.filter((item) => item.id !== id);
    S.searchArchived = S.searchArchived.filter((item) => item.id !== id);
    S.searchProjects = S.searchProjects.filter((item) => item.thread_id !== id);
  }

    function threadGone(id) {
    const known = S.threads.some((item) => item.id === id) || S.archivedThreads.some((item) => item.id === id);
    const ids = render.filteredHistory().map((item) => String(item.id));
    const at = ids.indexOf(String(id));
    const next = at < 0 ? "" : (ids[at + 1] ?? ids[at - 1] ?? "");
    undo.dropHistoryStacks(id);
    // A deleted/inaccessible thread resolves its unconfirmed send and its
    // queued messages outright: neither can ever be retried.
    if (S.pendingTurns.delete(String(id))) sync.setPendingTurn(S.pendingTurn);
    try {
      const gone = (key) => key === turns.queueStoreKey(id) || (key || "").startsWith(turns.queueStoreKey(id) + ".") ||
        key === turns.queueHoldKey(id) || (key || "").startsWith(turns.queueHoldKey(id) + ".");
      for (let i = localStorage.length - 1; i >= 0; i -= 1) {
        const key = localStorage.key(i);
        if (gone(key)) localStorage.removeItem(key);
      }
    } catch (_) {}
    dropThread(id);
    if (S.currentId !== id) { if (known) render.renderHistory(); return; }
    if (!known) return;
    if (next) openThread(next, { gone: true }).catch((error) => sync.showFailure("Chat could not be opened", error));
    else chatApi.clearChatState({ keepList: true });
  }

    function sessionProject() {
    if (S.pendingProject) return S.pendingProject;
    const current = threadById(S.currentId);
    if (current && current.kind === "project") {
      const info = projects.resolveProjectInfo(current.host_id, current.project_id);
      return {
        host_id: current.host_id,
        project_id: current.project_id,
        host_alias: info?.host_alias || current.host_alias || "",
        project_name: info?.project_name || current.project_name || "",
        remote_path: info?.remote_path || "",
        permission_mode: info?.permission_mode || "",
        color: info?.color || current.project_color || "",
        logo_url: info?.logo_url || current.project_logo_url || "",
      };
    }
    return null;
  }

    function syncTitle() {
    const current = threadById(S.currentId);
    const bound = sessionProject();
    const untitled = isUntitledThread(current);
    const name = untitled ? "" : current.title;
    if (S.titleEl) {
      S.titleEl.textContent = name;
      S.titleEl.classList.toggle("is-untitled", untitled);
    }
    S.root.classList.toggle("is-untitled", untitled);
    if (S.projectChip) {
      const project = bound && bound.project_name;
      if (bound && project) {
        S.projectChip.hidden = false;
        if (S.projectName) S.projectName.textContent = project;
        if (S.projectIconEl) S.projectIconEl.innerHTML = projects.projectIcon(bound, 14);
        S.projectChip.title = [project, ...projects.projectWorkspaceDetails(bound)].join(" | ");
        S.projectChip.setAttribute("aria-label", S.projectChip.title);
      } else {
        S.projectChip.hidden = true;
        if (S.projectName) S.projectName.textContent = "";
        S.projectChip.removeAttribute("title");
        S.projectChip.removeAttribute("aria-label");
      }
    }
    S.root.classList.toggle("is-project", Boolean(bound));
    if (S.welcomeEl) {
      if (bound && (bound.project_name || bound.host_alias)) {
        const project = bound.project_name || "project";
        const alias = bound.host_alias || "host";
        S.welcomeEl.textContent = `Work on ${project} on ${alias}.`;
      } else {
        S.welcomeEl.textContent = "What are we building?";
      }
    }
    document.title = S.currentId && name && name !== "New chat" ? name + " · Fuse" : "Chat · Fuse";
    composer.scheduleFitChatBar();
  }

    function pickerHostById(id) {
    return S.pickerHosts.find((host) => String(host.id) === String(id));
  }

    function bindCreatePayload(payload) {
    if (!S.pendingProject) return payload;
    payload.kind = "project";
    payload.host_id = S.pendingProject.host_id;
    payload.project_id = S.pendingProject.project_id;
    return payload;
  }

    async function startProjectThread(hostId, projectId, extra) {
    if (S.sending) return;
    const info = projects.resolveProjectInfo(hostId, projectId, extra);
    if (!info) {
      sync.showFailure("Could not start a project session", new Error("project not found"));
      return;
    }
    const nav = S.navigation;
    transcript.dismissRecaps(false);
    try {
      await sync.flushPending();
      if (nav !== S.navigation) return;
      render.setHistoryTab("projects", { persist: true, silent: true });
      chatApi.clearChatState();
      S.pendingProject = info;
      const listed = resolveListedModel(info.default_model);
      if (listed) {
        S.modelId = listed;
        picker.renderPickerButton();
      }
      syncTitle();
      render.renderHistory();
    } catch (error) {
      sync.showFailure("Could not start a project session", error);
    }
  }

    async function openThread(id, opts) {
    const gone = Boolean(opts && opts.gone);
    if (S.sending && !gone) return;
    const nav = ++S.navigation;
    S.loading = true;
    composer.syncComposer();
    try {
    if (!gone) {
      // A deleted thread keeps nothing: no draft to stash and no save to wait for.
      S.drafts.set(S.currentId, S.input.value);
      await sync.flushPending();
    }
    if (nav !== S.navigation) return;
    const expectedGeneration = ++S.generation;
    const current = () => expectedGeneration === S.generation && nav === S.navigation;
    // Long threads stream their newest messages first: paint those at once,
    // then merge the complete transcript through the snapshot reconcile path.
    let opened = false;
    const data = await net.fetchThreadSnapshot(id, (partial) => {
      if (opened || !current()) return;
      opened = true;
      showOpenedThread(partial);
    });
    if (!current()) return;
    if (!opened) showOpenedThread(data);
    else if (S.partialTranscript) sync.acceptThread(data);
    if (window.FuseComposerSpell) {
      // Learning from every earlier prompt is not needed for the first paint.
      const prompts = S.messages.filter((m) => m.role === "user" && !m.auto).map((m) => m.content);
      transcript.whenIdle(() => {
        if (!current()) return;
        FuseComposerSpell.rebuildLearned(prompts);
        FuseComposerSpell.refresh();
      });
    }
    composer.refreshProjectListing();
    // Covers events consumed while the navigation snapshot was in flight.
    sync.reconcileThread().catch(sync.syncFailure);
    } finally {
      if (nav === S.navigation) {
        S.loading = false;
        composer.syncComposer();
        if (!S.streaming) turns.drainQueue();
      }
    }
  }

    function showOpenedThread(data) {
    if (!Number.isSafeInteger(data.event_cursor)) throw new Error("Thread snapshot is missing event_cursor.");
    // Every article of another thread is about to be replaced. Hidden, it is
    // skipped by the layout reads below (queue, dock, composer) instead of
    // being laid out once more just before paint() drops it; paint's
    // setActive shows the log again in this same task, so nothing flashes.
    if (S.log._threadId !== data.id) S.log.hidden = true;
    transcript.setPartialTranscript(Boolean(data.partial));
    S.drafts.set(S.currentId, S.input.value);
    S.draftFiles.set(S.currentId, S.pendingAttachments);
    S.currentId = data.id;
    S.sessionId = "thread-" + data.id;
    const listed = resolveListedModel(data.model);
    if (listed) {
      S.modelId = listed;
      picker.renderPickerButton();
    }
    S.contextMessages = Array.isArray(data.context_messages) ? data.context_messages.map((msg) => ({ ...msg, noActions: true })) : [];
    S.contextLive = Boolean(data.context_live);
    S.threadRecaps = transcript.visibleRecaps(data);
    S.messages = (data.messages || []).map((msg) => ({
      id: msg.id,
      role: msg.role,
      content: msg.content || "",
      reasoning: msg.reasoning || "",
      error: msg.error || "",
      usage: msg.usage || null,
      model: msg.model || "",
      tools: Array.isArray(msg.tools) ? msg.tools : [],
      tools_live: Boolean(msg.tools_live),
      segments: Array.isArray(msg.segments) ? msg.segments : undefined,
      notice: msg.notice || "",
      elapsedMs: msg.elapsedMs ?? null,
      endedAt: typeof msg.endedAt === "string" ? msg.endedAt : "",
      stopped: Boolean(msg.stopped),
      attachments: Array.isArray(msg.attachments) ? msg.attachments : [],
      auto: Boolean(msg.auto),
    }));
    S.serverRevision = data.revision;
    perms.acceptPermThread(data);
    S.viewCursor = Number(data.event_cursor || 0);
    S.threadWatermark = Number.isSafeInteger(data.thread_cursor) ? Number(data.thread_cursor) : S.viewCursor;
    S.activeRun = data.run;
    // Restore any live permission asks (parent or subagent) the event stream
    // cannot replay after a reload — child asks never touch the transcript.
    perms.seedPendingFromServer(S.currentId);
    // Never carry the previous thread's meter into this one.
    S.liveMeter = null;
    S.compacting = false;
    S.contextWarned = false;
    S.contextAlertHidden = false;
    if (S.activeRun && S.stoppingRunId && S.activeRun.id === S.stoppingRunId) S.activeRun.cancel_requested = true;
    if (!traces.runIsActive(S.activeRun) && (!S.activeRun || !S.stoppingRunId || S.activeRun.id === S.stoppingRunId)) S.stoppingRunId = "";
    sync.setRunning(false);
    sync.healStuckRun(S.activeRun);
    context.acceptCacheProbe(data.compaction?.cache);
    if (data.compaction && data.compaction.meter) context.acceptMeter(data.compaction.meter);
    else context.syncContextMeter();
    S.acceptedQueueCommands = new Set(data.accepted_queue_ids || []);
    turns.loadQueue();
    if (!S.streaming) turns.drainQueue();
    S.dirty = false;
    S.pendingCreate = null;
    S.pendingProject = null;
    S.pendingSave = null;
    S.input.value = S.drafts.get(S.currentId) || "";
    S.pendingAttachments = S.draftFiles.get(S.currentId) || [];
    composer.renderAttachList();
    composer.resizeInput();
    S.threadContext = data.context || "";
    S.searchOn = Array.isArray(data.tools) ? data.tools.includes("web_search") : true;
    panels.syncMemoryUi();
    if (data.kind === "project") panels.refreshProjectAgents();
    else panels.renderProjectAgents(null);
    S.editingIndex = -1;
    undo.loadHistoryStacks();
    sync.setThreadUrl(S.currentId);
    if (data.kind === "project") render.setHistoryTab("projects", { persist: true, silent: true });
    else render.setHistoryTab("chats", { persist: true, silent: true });
    S.stickToBottom = true;
    if (S.searchOpen) {
      const top = S.root.querySelector(".chat-history-top");
      const row = S.root.querySelector("[data-history-row]");
      S.searchOpen = false;
      if (top) top.classList.remove("is-searching");
      if (row) row.hidden = true;
      if (S.searchToggle) S.searchToggle.setAttribute("aria-expanded", "false");
    }
    if (S.historyQuery) {
      S.historyQuery = "";
      if (S.historySearch) S.historySearch.value = "";
      render.resetSearchLists();
    }
    const openedEmpty = S.root.classList.contains("is-empty");
    transcript.paint();
    if (!openedEmpty && S.messages.length > 0) transcript.playTranscriptFx();
    render.renderHistory();
    drawer.setHistoryOpen(false);
    artifacts.rememberLatestArtifact();
    agents.resetAgents();
    if (window.matchMedia("(hover: hover) and (pointer: fine)").matches) S.input.focus();
    picker.defaultHint();
    sync.syncStatus();
  }

    function startRename(row, thread) {
    S.renaming = true;
    const inputEl = document.createElement("input");
    inputEl.className = "rename";
    inputEl.setAttribute("aria-label", "Rename chat");
    inputEl.value = thread.title || "";
    inputEl.maxLength = 80;
    row.innerHTML = "";
    row.appendChild(inputEl);
    inputEl.focus();
    inputEl.select();
    let finishing = false;
    const finish = async (save) => {
      if (finishing) return;
      finishing = true;
      const next = inputEl.value.trim();
      if (save && next && next !== thread.title) {
        try {
          if (!net.requireLive()) {
            finishing = false;
            return;
          }
          const latest = threadById(thread.id) || thread;
          S.renameSaving = net.api("/chat/threads/" + thread.id, {
            method: "PATCH",
            body: JSON.stringify({ title: next, expected_revision: latest.revision, command_id: newSession() }),
          });
          const updated = await S.renameSaving;
          if (thread.id === S.currentId) S.serverRevision = Math.max(S.serverRevision, updated.revision);
          upsertLocal(updated);
        } catch (error) {
          finishing = false;
          sync.showFailure("Rename failed; the title is still editable", error);
          inputEl.focus();
          inputEl.select();
          return;
        } finally { S.renameSaving = null; }
      }
      S.renaming = false;
      render.renderHistory();
      sync.reconcileThread().catch(sync.syncFailure);
    };
    inputEl.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        finish(true);
      } else if (event.key === "Escape") {
        event.preventDefault();
        finish(false);
      }
    });
    inputEl.addEventListener("blur", () => finish(true));
  }

    function threadById(id) {
    return S.threads.concat(S.archivedThreads).find((item) => item.id === id);
  }

    function threadMenu(anchor, id, at) {
    const thread = threadById(id);
    if (!thread) return;
    if (!net.requireLive()) return;
    const items = [
      {
        label: thread.pinned ? "Unpin" : "Pin to top",
        run: async () => {
          if (!net.requireLive()) return;
          const latest = threadById(id) || thread;
          const updated = await net.api("/chat/threads/" + id, {
            method: "PATCH",
            body: JSON.stringify({
              pinned: !latest.pinned,
              expected_revision: latest.revision,
              command_id: newSession(),
            }),
          });
          upsertLocal(updated);
          render.renderHistory();
        },
      },
      { label: "Rename", run: () => startRename(anchor.closest(".chat-history-item"), thread) },
      {
        label: "Fork",
        run: async () => {
          if (!net.requireLive()) return;
          const nav = S.navigation;
          await sync.flushPending();
          if (nav !== S.navigation) return;
          const copy = await net.api("/chat/threads/" + id + "/fork", { method: "POST", body: JSON.stringify({ expected_revision: threadById(id)?.revision, command_id: newSession() }) });
          upsertLocal(copy);
          render.renderHistory();
          picker.setHint("Forked. The copy runs on its own: new chat, subagent and command ids.");
          if (nav === S.navigation) await openThread(copy.id);
        },
      },
      {
        label: thread.archived ? "Unarchive" : "Archive",
        run: async () => {
          if (!net.requireLive()) return;
          const nav = S.navigation;
          await sync.flushPending();
          if (nav !== S.navigation) return;
          const latest = threadById(id) || thread;
          const updated = await net.api("/chat/threads/" + id, {
            method: "PATCH",
            body: JSON.stringify({
              archived: !latest.archived,
              expected_revision: latest.revision,
              command_id: newSession(),
            }),
          });
          upsertLocal(updated);
          if (nav === S.navigation && S.currentId === id && updated.archived) chatApi.clearChatState({ keepList: true });
          render.renderHistory();
          // Restoring a session also restores its archived project.
          if (!updated.archived && updated.kind === "project") render.refreshThreads().catch(() => {});
        },
      },
      { label: thread.kind === "project" ? "Move to another project" : "Move to project", run: () => projects.moveToProject(id) },
      ...(thread.kind === "project" && thread.project_id != null
        ? [{ label: "Project settings", run: () => projects.editProject(thread.host_id, thread.project_id) }]
        : []),
      {
        label: "Delete",
        danger: true,
        run: async () => {
          if (!net.requireLive()) return;
          if (!(await undo.askConfirm("Delete this chat? This cannot be undone.", { confirmLabel: "Delete", danger: true }))) return;
          const nav = S.navigation;
          const latest = threadById(id) || thread;
          S.pendingDeletes.add(id);
          try {
            await net.api("/chat/threads/" + id, {
              method: "DELETE",
              body: JSON.stringify({ expected_revision: latest.revision, command_id: newSession() }),
            });
          } catch (error) {
            if (error.status === 404) {
              threadGone(id);
              return;
            }
            if (error.status === 409) {
              const fresh = await net.api("/chat/threads/" + id).catch(() => null);
              if (!fresh) {
                threadGone(id);
                return;
              }
              await net.api("/chat/threads/" + id, {
                method: "DELETE",
                body: JSON.stringify({ expected_revision: fresh.revision, command_id: newSession() }),
              });
            } else {
              S.pendingDeletes.delete(id);
              throw error;
            }
          }
          if (nav !== S.navigation) return;
          threadGone(id);
        },
      },
    ];
    overlays.showPop(anchor, items, at);
  }

    async function copyText(text) {
    try {
      await navigator.clipboard.writeText(text || "");
      picker.setHint("Copied to clipboard.");
    } catch (error) {
      sync.showFailure("Clipboard copy failed", error);
      throw error;
    }
  }

  return {
    bindCreatePayload,
    copyText,
    openThread,
    pickerHostById,
    sessionProject,
    startProjectThread,
    syncTitle,
    threadById,
    threadGone,
    threadMenu,
    upsertLocal,
    isUntitledThread,
  };
}

export { isUntitledThread };
