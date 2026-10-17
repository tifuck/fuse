// Thread/context snapshot application and fuse event application.
import { S } from '../state.js';
import { newSession, resolveListedModel } from '../state.js';
import { deltaParts, messageRunLive, splitThink, untimed } from '../core.js';

// Stored messages leave these out when empty (app/chat_schema.py
// parse_messages). A merge into the prior object would keep the old value.
const SERVER_OPTIONAL = ["notice", "elapsedMs", "endedAt", "stopped", "auto", "usage", "model", "tools", "permissions", "segments", "attachments"];

export function createApply(ctx) {
  const { agents, artifacts, composer, context, net, panels, perms, persist, picker, sidebar, status, traces, transcript, turns, ui } = ctx;
  // Patches are suffixes of the last published text. One that does not fit
  // means events between this view's snapshot and the patch were never
  // applied; sync/stream.js replays them from the snapshot cursor.
  const gapError = (message) => Object.assign(new Error(message), { gap: true });
    async function runTitleRegen() {
    if (S.titleRegenRequest) return;
    if (!S.currentId || !S.messages.length) {
      picker.setHint("Nothing to title yet.");
      return;
    }
    if (!net.requireLive()) return;
    S.titleRegenRequest = true;
    try {
      const data = await net.api("/chat/threads/" + S.currentId + "/title", {
        method: "POST",
        body: JSON.stringify({ command_id: newSession() }),
      });
      if (data && data.started) {
        // Held until the title lands (thread.updated) or fails (title.failed),
        // with a backstop for a regenerated title identical to the old one.
        const key = "title:" + S.currentId;
        picker.holdHint(key, "Regenerating title\u2026");
        window.setTimeout(() => picker.releaseHint(key), 30000);
      }
      else if (data?.busy) picker.setHint("Title generation is already running.");
    } catch (error) {
      if (error && error.status === 429) picker.setHint(error.message || "Title regeneration limit reached. Try again later.", true);
      else status.showFailure("Could not regenerate the title", error);
    } finally {
      S.titleRegenRequest = false;
    }
  }

    async function runRecap() {
    if (!S.currentId || !S.messages.length || !net.requireLive()) return;
    try {
      await net.api("/chat/threads/" + S.currentId + "/recap", { method: "POST", body: "{}" });
      picker.setHint("Recap queued; generates once background work finishes.");
    } catch (error) { status.showFailure("Could not request a recap", error); }
  }

    const recapsToggle = S.viewMenu?.querySelector("[data-recaps-toggle]");

    function acceptContext(data) {
    if (!data || Number(data.revision) < S.contextRevision) return;
    if (S.memoryDirty && Number(data.revision) <= S.contextRevision) {
      status.syncStatus();
      return;
    }
    S.memoryDirty = false;
    S.pendingContext = null;
    S.contextRevision = Number(data.revision || 0);
    S.recapsEnabled = data.recaps !== false;
    if (recapsToggle) recapsToggle.checked = S.recapsEnabled;
    S.instructions = data.instructions || "";
    S.profile = data.profile || "";
    panels.syncMemoryUi();
    status.syncStatus();
  }

    function acceptThread(data) {
    if (data && !Number.isSafeInteger(data.event_cursor)) throw new Error("Thread snapshot is missing event_cursor; live sync requires a consistent snapshot.");
    if (!data || data.id !== S.currentId) return;
    // Viewing the thread is what dismisses its finished-run badge; the server
    // snapshot arrives with unread already cleared by the GET.
    const sidebarRow = sidebar.threadById(S.currentId);
    if (sidebarRow && sidebarRow.unread) {
      sidebarRow.unread = false;
      sidebar.renderHistory();
    }
    transcript.setPartialTranscript(false);
    S.lastRunReceiptAt = Date.now();
    perms.acceptPermThread(data);
    S.acceptedQueueCommands = new Set(data.accepted_queue_ids || []);
    turns.retireAcceptedQueue();
    if (S.editingIndex >= 0) {
      const editing = S.messages[S.editingIndex];
      const next = data.messages[S.editingIndex];
      if (!editing || !next || next.id !== editing.id || next.content !== editing.content) {
        S.editingIndex = -1;
        S.editingFiles = [];
      }
    }
    const viewport = transcript.captureThreadViewport();
    const old = new Map(S.messages.map((msg) => [msg.id, msg]));
    const previousReply = old.get(S.activeRun?.message_id);
    let rekeyed = false;
    if (previousReply && S.activeRun?.id === data.run?.id) {
      // The steer response freezes the run's *durable* row, which can lag the
      // streamed patches by up to a flush interval — the snapshot's frozen
      // text may be shorter or longer than what this client holds. Accept a
      // prefix in either direction (one side is always a prefix of the other
      // at a steer); otherwise the in-place re-key below never runs and the
      // relocated node visibly jumps the transcript.
      const prefixEither = (a, b) => !b || a.startsWith(b) || b.startsWith(a);
      const prevTools = previousReply.tools || [];
      const frozen = data.messages.find(msg => msg.role === "assistant" && msg.tools_live && !old.has(msg.id) &&
        (previousReply.content || previousReply.reasoning || previousReply.tools?.length) &&
        prefixEither(String(msg.content || ""), previousReply.content || "") && prefixEither(String(msg.reasoning || ""), previousReply.reasoning || "") &&
        (!(msg.tools || []).length || !prevTools.length ||
          (msg.tools || []).every(tool => prevTools.some(p => p.id === tool.id)) ||
          prevTools.every(tool => (msg.tools || []).some(next => next.id === tool.id))));
      if (frozen) {
        rekeyed = true;
        // A stale snapshot would drop the streamed tail until the next
        // checkpoint update arrives; keep the client's longer text.
        if (String(frozen.content || "").length < String(previousReply.content || "").length ||
            String(frozen.reasoning || "").length < String(previousReply.reasoning || "").length) {
          for (const key of ["content", "reasoning", "segments"]) if (previousReply[key] != null) frozen[key] = previousReply[key];
        }
        for (const key of ["stepOpen", "activityOpenMap"]) if (previousReply[key]) frozen[key] = { ...previousReply[key] };
        const node = [...S.log.children].find(el => el.dataset.messageId === previousReply.id);
        if (node) { node.dataset.messageId = frozen.id; node._rebind?.(frozen); }
        if (viewport.anchorId === previousReply.id) viewport.anchorId = frozen.id;
        delete previousReply._liveOrigin;
        delete previousReply._elapsedObserved;
      }
    }
    // A live run's snapshot is durable only up to its stamped cursor (state
    // writes are throttled), and this view may already hold the run's later
    // patches. Rolling the reply back to the stamp would leave the next patch
    // past the end of its text, and those patches are never resent, so the
    // reply this view holds is kept, with the cursors that describe it.
    const ahead = !rekeyed && traces.runIsActive(data.run) && S.activeRun?.id === data.run.id &&
      Number(data.event_cursor) < S.viewCursor;
    const keepLive = ahead ? old.get(data.run.message_id) || null : null;
    // A local transcript edit (delete, attachment removal) waits up to 200 ms
    // for its save. A snapshot taken at the same revision predates it: keep
    // the edit and let the queued save carry it. If the server moved on, the
    // edit conflicts and the server wins, but say so instead of silently
    // dropping what the user just did.
    const keepLocalTranscript = S.transcriptDirty && !S.streaming && !traces.runIsActive(data.run) &&
      Number(data.revision) === Number(S.serverRevision);
    if (keepLocalTranscript) data = { ...data, messages: S.messages.map((msg) => ({ ...msg })) };
    else if (S.transcriptDirty) picker.setHint("This chat changed elsewhere; your unsaved edit was not kept.", true);
    const same = S.messages.length <= data.messages.length && S.log.children.length === S.messages.length &&
      S.messages.every((msg, i) => msg.id === data.messages[i].id && S.log.children[i].dataset.messageId === msg.id);
    const oldLength = S.messages.length;
    S.contextCut = data.context_cut || null;
    S.threadRecaps = transcript.visibleRecaps(data);
    S.messages = data.messages.map((msg) => {
      const prior = old.get(msg.id);
      if (!prior) return { ...msg };
      if (prior === keepLive) return prior;
      // The live reply's projection is cursor-stamped and fills in as it
      // streams; only stored rows are complete.
      if (msg.id !== data.run?.message_id) for (const key of SERVER_OPTIONAL) if (!(key in msg)) delete prior[key];
      Object.assign(prior, msg);
      delete prior._raw;
      delete prior._reason;
      return prior;
    });
    S.serverRevision = data.revision;
    if (!keepLive) {
      S.viewCursor = Number(data.event_cursor || 0);
      S.threadWatermark = Number.isSafeInteger(data.thread_cursor) ? Number(data.thread_cursor) : S.viewCursor;
    }
    const priorRun = S.activeRun;
    S.activeRun = keepLive ? { ...data.run, status: priorRun.status, activity: priorRun.activity || data.run.activity } : data.run;
    if (S.activeRun && S.stoppingRunId && S.activeRun.id === S.stoppingRunId) S.activeRun.cancel_requested = true;
    if (!traces.runIsActive(S.activeRun) && (!S.activeRun || !S.stoppingRunId || S.activeRun.id === S.stoppingRunId)) S.stoppingRunId = "";
    status.setRunning(false);
    status.healStuckRun(S.activeRun);
    // Unsaved local settings (model, search, thread context) outrank the
    // snapshot: it predates them. Keep them and re-save against the new
    // revision instead of silently reverting what the user just changed.
    const keepLocalSettings = S.dirty;
    if (!keepLocalSettings) {
      S.threadContext = data.context || "";
      S.searchOn = data.tools?.includes("web_search") ?? true;
      const listed = resolveListedModel(data.model);
      if (listed && S.modelId !== listed) { S.modelId = listed; picker.renderPickerButton(); }
    }
    context.acceptCacheProbe(data.compaction?.cache);
    if (data.compaction && data.compaction.meter) context.acceptMeter(data.compaction.meter);
    else context.syncContextMeter();
    S.transcriptDirty = keepLocalTranscript;
    S.pendingSave = null;
    if (keepLocalSettings || keepLocalTranscript) persist.queueSave();
    else S.dirty = false;
    panels.syncMemoryUi();
    // The fast path patches the articles of S.messages in place. A compacted
    // thread shows its context view instead, so a snapshot that switches
    // views repaints even when the message ids are unchanged.
    if (same && transcript.viewMessages() === S.messages) {
      traces.foldAutoCommandResults(S.messages);
      traces.reconcileBgTraces([...S.completedJobs.values()], false);
      S.messages.forEach((msg, i) => {
        if (i >= oldLength) {
          const live = messageRunLive(msg);
          const article = msg.role === "user" ? ui.renderUser(msg, i, live) : ui.renderAssistant(msg, live);
          article.dataset.messageId = msg.id || "";
          if (msg.role === "assistant" && S.log.childElementCount) transcript.markArriving(article);
          S.log.appendChild(article);
          return;
        }
        const article = S.log.children[i];
        if (msg.role === "assistant") {
          const live = messageRunLive(msg);
          ui.updateAssistant(article, msg, live);
          if (!live) ui.mountActions(article, i);
        } else if (article.querySelector(".chat-bubble")) {
          if (article.querySelector(".chat-bubble").textContent !== msg.content) article.querySelector(".chat-bubble").textContent = msg.content;
          ui.fillUserFiles(article, msg, i);
        }
      });
      transcript.setActive(S.messages.length > 0);
      composer.syncComposer();
    } else transcript.paint();
    transcript.paintRecaps();
    // This snapshot already reconciles children transactionally. Cached list
    // rows/events can predate a resume and must not overwrite it now or later.
    S.agentLifecycle.clear();
    S.agentLifecycleEpoch += 1;
    turns.syncEmptyReplyRetry();
    sidebar.upsertLocal(data);
    sidebar.renderHistory();
    transcript.restoreThreadViewport(viewport);
    artifacts.rememberLatestArtifact();
    status.syncStatus();
  }

    async function reconcileThread() {
    const id = S.currentId;
    if (!id) return;
    const gen = S.generation;
    const load = ++S.loadEpoch;
    let data;
    // Keep the race-healing read, but avoid rebuilding/downloading a snapshot
    // already represented by this view (including in-flight run progress).
    const query = (!S.dirty && !S.transcriptDirty
      ? "?observed_cursor=" + S.viewCursor + "&observed_thread_cursor=" + S.threadWatermark + "&pack=1"
      : "?pack=1") + net.queuedParam(id);
    try { data = net.unpackThread(await net.api("/chat/threads/" + id + query)); }
    catch (error) {
      if (id !== S.currentId || gen !== S.generation || load !== S.loadEpoch) return;
      if (error.status !== 404) throw error;
      sidebar.threadGone(id);
      return;
    }
    if (id !== S.currentId || gen !== S.generation || load !== S.loadEpoch) return;
    if (!data.unchanged) acceptThread(data);
    if (!S.streaming) turns.drainQueue();
  }

    function applyProgressPayload(msg, payload, child = false) {
    if (!msg || !payload) return;
    if (Array.isArray(payload.tools)) {
      const prior = new Map((msg.tools || []).map((trace) => [trace.id, trace]));
      msg.tools = payload.tools.map((trace) => traces.mergeToolEvent(prior.get(trace.id), trace, trace.status));
      const started = msg.tools.findLast(trace => trace.status === "running" && prior.get(trace.id)?.status !== "running");
      if (started) {
        msg._activity = { phase: "tools", tool_id: started.id };
        if (!child && S.activeRun?.message_id === msg.id) S.activeRun.activity = msg._activity;
      }
    }
    if (Array.isArray(payload.permissions)) {
      msg.permissions = payload.permissions;
      perms.syncPermQueueFromMessage(msg);
    }
    if (payload.message) {
      // Child completion and parent checkpoints travel independently. An older
      // parent snapshot must not revive the same already-finished invocation.
      const finished = new Map((msg.tools || []).filter((trace) => trace.id && traces.childBackedTool(trace.name) && !traces.executionPending(trace) &&
        ["done", "error", "failed", "interrupted", "cancelled"].includes(trace.status)).map((trace) => [trace.id, trace]));
      Object.assign(msg, payload.message);
      (msg.tools || []).forEach((trace) => {
        const prior = finished.get(trace.id);
        if (prior && prior.child_id === trace.child_id && ["running", "progress", "queued"].includes(trace.status)) Object.assign(trace, prior);
      });
      delete msg._raw;
      delete msg._reason;
      return;
    }
    for (const obj of payload.events || []) {
      if (obj.fuse) applyFuseEvent(msg, obj.fuse, true, child);
      else {
        const part = deltaParts(obj);
        if (part.error) msg.error = part.error;
        if (part.usage) msg.usage = part.usage;
        msg._raw = (msg._raw ?? msg.content ?? "") + (part.content || "");
        msg._reason = (msg._reason ?? msg.reasoning ?? "") + (part.reasoning || "");
        const split = splitThink(msg._raw);
        msg.content = split.content;
        msg.reasoning = msg._reason + (msg._reason && split.reasoning ? "\n" : "") + split.reasoning;
      }
    }
  }

    function applyFuseEvent(msg, event, passive = false, child = false) {
    if (!event || typeof event !== "object") return;
    if (event.event === "heartbeat") {
      if (!child) S.lastRunReceiptAt = Date.now();
      if (child && S.agentRun) S.agentRun.updated_at = new Date().toISOString();
      return;
    }
    if (event.event === "activity") {
      msg._activity = { ...event };
      if (!child && S.activeRun && (!event.run_id || event.run_id === S.activeRun.id)) S.activeRun.activity = { ...event };
      return;
    }
    if (event.event === "cache") {
      if (!child && event.state === "miss") context.noteCacheMiss(event);
      return;
    }
    if (event.event === "context") {
      // Subagent context meters describe the child's isolated window, not
      // this thread's occupancy; they must never move the thread ring.
      if (child) {
        if (S.agentsOpen && S.agentViewId && S.agentRun && (event.run_id === S.agentRun.id || event.run_id === S.agentViewId)) {
          S.agentRun.compaction = { meter: { ...event, scope: "prompt" } };
          agents.scheduleAgentView();
        }
        return;
      }
      S.compacting = event.phase === "compacting" || Boolean(event.compacting);
      // Every context event riding a run stream is a live prompt reading —
      // including the post-auto-compact "compacted" phase, which describes
      // the rebuilt in-flight prompt, not an at-rest transcript snapshot.
      context.acceptMeter({ ...event, scope: "prompt", compacting: S.compacting });
      if (event.phase === "compacted") S.compacting = false;
      return;
    }
    if (event.event === "message.patch") {
      for (const field of ["content", "reasoning"]) {
        const patch = event[field];
        if (!patch || !Number.isInteger(patch.from) || patch.from < 0 || patch.from > (msg[field] || "").length) {
          throw gapError("Output checkpoint mismatch; replaying missed patches.");
        }
        msg[field] = (msg[field] || "").slice(0, patch.from) + patch.text;
      }
      if (event.segments) {
        if (!Array.isArray(msg.segments)) msg.segments = [];
        const change = event.segments;
        if (Number.isInteger(change.from)) {
          if (change.from < 0 || change.from > msg.segments.length || !Array.isArray(change.items)) {
            throw gapError("Segment checkpoint mismatch; replaying missed patches.");
          }
          msg.segments.splice(change.from, msg.segments.length - change.from, ...change.items);
        } else {
          for (const patch of change.texts || []) {
            const segment = msg.segments[patch.index];
            if (!segment || (segment.type !== "content" && segment.type !== "reasoning") ||
                !Number.isInteger(patch.from) || patch.from < 0 || patch.from > segment.text.length) {
              throw gapError("Segment checkpoint mismatch; replaying missed patches.");
            }
            segment.text = segment.text.slice(0, patch.from) + patch.text;
            if (segment.type === "reasoning" && Number.isInteger(patch.ms)) segment.ms = patch.ms;
          }
          if (Array.isArray(change.append)) {
            if (Number.isInteger(change.base)) {
              if (change.base === msg.segments.length) msg.segments.push(...change.append);
              // A resend may carry a thought's newer duration; only content must match.
              else if (change.base < 0 || JSON.stringify(msg.segments.slice(change.base, change.base + change.append.length).map(untimed)) !== JSON.stringify(change.append.map(untimed))) {
                throw gapError("Segment append base mismatch; replaying missed patches.");
              }
            } else msg.segments.push(...change.append); // pre-upgrade replay
          }
        }
      }
      msg.elapsedMs = event.elapsedMs;
      if (typeof event.endedAt === "string" && event.endedAt) msg.endedAt = event.endedAt;
      msg.usage = event.usage;
      msg.error = event.error || "";
      if (Number.isFinite(Number(event.streamOut)) && Number(event.streamOut) >= 0) {
        msg.streamOut = Math.round(Number(event.streamOut));
      }
      return;
    }
    if (event.event === "toast" && event.message) {
      const line = String(event.message).trim();
      if (line && window.fuseUI) {
        window.fuseUI.toast({
          key: "fuse-chat-toast:" + (msg && msg.id ? msg.id : "turn") + ":" + line,
          kind: event.kind || "idle",
          title: event.title || "Fuse",
          msg: line,
          ttl: 6000,
        });
      }
      return;
    }
    if (event.event === "notice" && event.message) {
      const line = String(event.message).trim();
      const marker = agents.compactionMarkerText(line);
      if (marker) {
        if (!Array.isArray(msg.segments)) msg.segments = [];
        const handoff = String(event.handoff || "").trim();
        if (handoff && !marker.startsWith("Context pruned")) {
          msg.content = "";
          msg.reasoning = "";
          msg.tools = [];
          msg.error = "";
          delete msg._raw;
          delete msg._reason;
          msg.segments = [{ type: "marker", kind: "compaction", text: marker, summary: handoff }];
          return;
        }
        if (!msg.segments.some((part) => part && part.type === "marker" && part.text === marker)) {
          msg.segments.push({ type: "marker", kind: marker.startsWith("Context pruned") ? "prune" : "compaction", text: marker });
        }
        return;
      }
      // Run notices (provider retries, cut-off calls, limits) are "notice"
      // marker segments placed by the run worker (chat_runs.Output.apply) and
      // arrive in the message.patch ahead of this event; mirroring them here
      // would fork the segment list from the server's.
      return;
    }
    if (event.event === "artifact" && event.identifier) {
      if (!passive) artifacts.showArtifact(event.identifier, event).catch(() => {});
      return;
    }
    if (event.event === "permission") {
      if (!Array.isArray(msg.permissions)) msg.permissions = [];
      const ident = String(event.id || "");
      if (!ident) return;
        if (event.kind === "tool_permission") {
          const rec = {
            id: ident.slice(0, 64),
            kind: "tool_permission",
            status: ["pending", "allowed", "denied", "timeout", "cancelled"].includes(event.status) ? event.status : "denied",
            tool: String(event.tool || "").slice(0, 40),
            host: String(event.host || "").slice(0, 80),
            path: String(event.path || "").slice(0, 400),
            cwd: String(event.cwd || "").slice(0, 400),
            command: String(event.command || "").slice(0, 400),
            secret: Boolean(event.secret),
            requester: String(event.requester_label || event.requester || "Main agent").slice(0, 200),
            reasons: Array.isArray(event.reasons) ? event.reasons.map((r) => String(r).slice(0, 120)).slice(0, 4) : [],
          };
          if (event.expires_at) rec.expires_at = String(event.expires_at).slice(0, 40);
          if (event.description) rec.description = String(event.description).slice(0, 200);
        if (["allow_session", "allow_host"].includes(event.grant_scope)) rec.grant_scope = event.grant_scope;
        if (event.grant_kind === "dir" || event.grant_kind === "file") rec.grant_kind = event.grant_kind;
        if (event.grant_value) rec.grant_value = String(event.grant_value).slice(0, 400);
        const pidx = msg.permissions.findIndex((p) => p.id === ident);
        if (pidx >= 0) msg.permissions[pidx] = { ...msg.permissions[pidx], ...rec };
        else msg.permissions.push(rec);
        if (rec.status === "pending") perms.upsertPermCard({ ...rec });
        else {
          S.permCards.delete(ident);
          perms.renderPermQueue();
          if (S.permsOpen && (rec.grant_scope === "allow_session" || rec.grant_scope === "allow_host")) perms.refreshPerms();
        }
        return;
      }
      const rec = {
        id: ident.slice(0, 64),
        kind: "skill_save",
        status: event.status || "pending",
        slug: String(event.slug || "").slice(0, 64),
        description: String(event.description || "").slice(0, 1024),
        overwrite: Boolean(event.overwrite),
        enable: event.enable !== false,
        files: Array.isArray(event.files) ? event.files.map((n) => String(n).slice(0, 200)).slice(0, 50) : [],
        origin_existing: String(event.origin_existing || "").slice(0, 40),
      };
      const idx = msg.permissions.findIndex((p) => p.id === ident);
      if (idx >= 0) msg.permissions[idx] = { ...msg.permissions[idx], ...rec };
      else msg.permissions.push(rec);
      if (Array.isArray(msg.segments) && !msg.segments.some((part) => part.type === "permission" && part.id === ident)) {
        msg.segments.push({ type: "permission", id: ident });
      }
      return;
    }
    if (event.event !== "tool") return;
    if (!Array.isArray(msg.tools)) msg.tools = [];
    const byId = event.id ? msg.tools.findIndex((t) => t.id === event.id) : -1;
    if (event.status === "progress") {
      const rec = byId >= 0 ? msg.tools[byId] : null;
      if (!rec || traces.SETTLED_TOOL_STATUS.has(rec.status)) return;
      rec.progressLabel = event.query ? `Searching “${event.query}” (${event.n ?? 0})` : rec.progressLabel;
      rec.progressN = event.n;
      rec.latency_ms = event.latency_ms;
      if (event.event_count != null) rec.event_count = event.event_count;
      if (event.output != null) rec.output = event.output;
      if (event.preview) rec.preview = event.preview;
      if (event.elapsed_ms != null) rec.elapsed_ms = event.elapsed_ms;
      return;
    }
    if (event.status === "running") {
      if (byId < 0 || !traces.SETTLED_TOOL_STATUS.has(msg.tools[byId].status)) {
        const activity = { phase: "tools", tool_id: event.id };
        msg._activity = activity;
        if (!child && S.activeRun && msg.id === S.activeRun.message_id) S.activeRun.activity = activity;
      }
      if (traces.mergeSubagentWait(msg, event, "running")) return;
      if (byId < 0) msg.tools.push(traces.mergeToolEvent(null, event, "running"));
      else msg.tools[byId] = traces.mergeToolEvent(msg.tools[byId], event, "running");
      if (Array.isArray(msg.segments) && event.id && !msg.segments.some((part) => part.type === "tool" && part.id === event.id)) {
        msg.segments.push({ type: "tool", id: event.id });
      }
      if (!passive && (event.name === "create_artifact" || event.name === "update_artifact")) {
        artifacts.startArtifactBuild(event.title || (event.name === "update_artifact" ? "Updating artifact" : "New artifact"));
      }
      return;
    }
    if (traces.mergeSubagentWait(msg, event, event.status || "done")) return;
    const rec = traces.mergeToolEvent(byId >= 0 ? msg.tools[byId] : null, event, event.status || "done");
    if (byId >= 0) msg.tools[byId] = rec;
    else msg.tools.push(rec);
    if (Array.isArray(msg.segments) && rec.id && !msg.segments.some((part) => part.type === "tool" && part.id === rec.id)) {
      msg.segments.push({ type: "tool", id: rec.id });
    }
    if (!passive && (rec.name === "create_artifact" || rec.name === "update_artifact") && rec.identifier && rec.status === "done") {
      artifacts.showArtifact(rec.identifier, rec).catch(() => {});
    }
  }

  return {
    acceptContext,
    acceptThread,
    applyFuseEvent,
    applyProgressPayload,
    recapsToggle,
    reconcileThread,
    runRecap,
    runTitleRegen,
  };
}
