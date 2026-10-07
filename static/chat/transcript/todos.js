// Todo section state machine and rendering triggers.
import { S } from '../state.js';

const TODO_STATUSES = { pending: "Pending", in_progress: "In progress", completed: "Done", cancelled: "Cancelled" };

function normalizeTodos(raw) {
  if (!Array.isArray(raw)) return [];
  const out = [];
  raw.slice(0, 40).forEach((item, index) => {
    if (!item || typeof item !== "object") return;
    const status = TODO_STATUSES[item.status] ? item.status : "pending";
    const content = String(item.content || "").replace(/\s+/g, " ").trim().slice(0, 200);
    if (!content) return;
    out.push({ id: String(item.id || index + 1).slice(0, 64), content, status });
  });
  return out;
}

// —— The todo list ——
// todo_write never prints as a transcript row. The model re-sends the whole
// list on every call, so a list is the newest successful call in ONE message:
// the main chat's section reads the thread's own messages, a subagent's reads
// that child's message. Nothing is looked up by tool id across messages (ids
// restart in every run: a parent and a child can both own "ts_0_0"), so one
// agent's list can never show up in, or be updated by, another's.
const todoClosed = (item) => item.status === "completed" || item.status === "cancelled";

const todoSettled = (items) => items.length > 0 && items.every(todoClosed);

const todoLanded = (trace) => Boolean(trace && trace.name === "todo_write" && trace.status !== "error" && !trace.error && Array.isArray(trace.todos));

function latestTodoState(msg) {
  const traces = Array.isArray(msg && msg.tools) ? msg.tools : [];
  for (let i = traces.length - 1; i >= 0; i -= 1) {
    const trace = traces[i];
    if (!todoLanded(trace)) continue;
    const state = { key: `${msg.id || ""}:${trace.id || i}`, items: normalizeTodos(trace.todos) };
    // A subagent's compaction prunes the traces it already acted on; its
    // list is still the one the model is working from.
    if (msg.noActions) msg._todoLast = state;
    return state;
  }
  return msg && msg.noActions && msg._todoLast || null;
}

function todoSettledText(items) {
  const done = items.filter((item) => item.status === "completed").length;
  const cancelled = items.length - done;
  return `Todos · ${done}/${items.length} done${cancelled ? ` · ${cancelled} cancelled` : ""}`;
}

// The call that takes a list from open to settled leaves one line in the
// transcript: tool id -> its text. Every other todo_write prints nothing.
function todoSettleLines(msg) {
  const lines = new Map();
  let before = false;
  (Array.isArray(msg && msg.tools) ? msg.tools : []).forEach((trace) => {
    if (!todoLanded(trace)) return;
    const items = normalizeTodos(trace.todos);
    const settled = todoSettled(items);
    if (settled && !before) lines.set(trace.id, todoSettledText(items));
    before = settled;
  });
  return lines;
}

const TODO_LINGER_MS = 1500;

const STORE_TODO_DISMISSED = "fuse.chat.todos.dismissed.v1";

let todoSyncQueued = false;

export function createTodos(ctx) {
  const { agents, follow, traces, ui } = ctx;
    function todoDismissed(key) {
    if (!S.todoDismissedKeys) {
      try { S.todoDismissedKeys = new Set(JSON.parse(localStorage.getItem(STORE_TODO_DISMISSED) || "[]")); } catch { S.todoDismissedKeys = new Set(); }
    }
    return S.todoDismissedKeys.has(key);
  }

    function dismissTodos(key) {
    todoDismissed(key);
    S.todoDismissedKeys.add(key);
    try { localStorage.setItem(STORE_TODO_DISMISSED, JSON.stringify([...S.todoDismissedKeys].slice(-60))); } catch {}
  }

    // An unfinished list stays until it is dismissed. A finished one shows only
  // while something is still running on the message that owns it, then holds
  // for a beat so the last check lands before the section goes.
  function todoShown(sec, candidate, active) {
    const state = candidate && candidate.state;
    if (!state || !state.items.length || todoDismissed(state.key)) {
      sec.shownKey = "";
      sec.lingerUntil = 0;
      return false;
    }
    if (!todoSettled(state.items) || (active && candidate.latest)) {
      sec.shownKey = state.key;
      sec.lingerUntil = 0;
      return true;
    }
    if (sec.shownKey === state.key) {
      const now = Date.now();
      if (!sec.lingerUntil) sec.lingerUntil = now + TODO_LINGER_MS;
      if (now < sec.lingerUntil) {
        clearTimeout(sec.timer);
        sec.timer = setTimeout(scheduleTodoSync, sec.lingerUntil - now + 20);
        return true;
      }
    }
    sec.shownKey = "";
    sec.lingerUntil = 0;
    return false;
  }

    function paintTodoSection(sec, candidate, active, afterResize) {
    const el = sec.el;
    if (!el) return;
    const state = candidate && candidate.state;
    if (!todoShown(sec, candidate, active)) {
      if (!el.hidden) {
        el.hidden = true;
        el.replaceChildren();
        sec.sig = "";
        sec.key = "";
        afterResize();
      }
      return;
    }
    const sig = `${state.key}|${active}|${sec.expanded}|${JSON.stringify(state.items)}`;
    sec.key = state.key;
    if (sig === sec.sig && !el.hidden) return;
    sec.sig = sig;
    el.innerHTML = ui.todoPanelHtml(state, { active, expanded: sec.expanded });
    el.hidden = false;
    afterResize();
  }

    // The newest list in the thread, and whether its message is the thread's last
  // reply (an old finished list never reappears under a later turn).
  function mainTodoCandidate() {
    let latest = true;
    for (let i = S.messages.length - 1; i >= 0; i -= 1) {
      const msg = S.messages[i];
      if (!msg || msg.role !== "assistant") continue;
      const state = latestTodoState(msg);
      if (state) return { state, latest };
      latest = false;
    }
    return null;
  }

    function mainTodoActive() {
    if (S.streaming || traces.runIsActive(S.activeRun)) return true;
    for (const row of S.agentLifecycle.values()) if (agents.agentLive(row.status)) return true;
    return S.agentsRows.some((row) => agents.agentLive(row.status));
  }

    function syncTodoSections() {
    paintTodoSection(S.todoSections.main, mainTodoCandidate(), mainTodoActive(), () => {
      ui.followLiveDockSlide();
      if (S.stickToBottom) follow.scrollThread();
      follow.syncLatest();
    });
    const child = S.agentViewId && S.agentRun && S.agentMsgState ? latestTodoState(S.agentMsgState) : null;
    paintTodoSection(S.todoSections.agent, child && { state: child, latest: true }, Boolean(S.agentRun && agents.agentLive(S.agentRun.status)), () => {
      if (S.agentStick && S.agentLogEl) agents.restoreAgentScroll(true, 0);
    });
  }

    function scheduleTodoSync() {
    if (todoSyncQueued) return;
    todoSyncQueued = true;
    requestAnimationFrame(() => {
      todoSyncQueued = false;
      syncTodoSections();
    });
  }

    function onTodoClick(event) {
    const kind = Object.keys(S.todoSections).find((name) => S.todoSections[name].el && S.todoSections[name].el.contains(event.target));
    if (!kind) return;
    const sec = S.todoSections[kind];
    if (event.target.closest("[data-todo-dismiss]")) {
      dismissTodos(sec.key);
      sec.sig = "";
      scheduleTodoSync();
    } else if (event.target.closest("[data-todo-toggle]")) {
      sec.expanded = !sec.expanded;
      scheduleTodoSync();
    }
  }

  return {
    onTodoClick,
    scheduleTodoSync,
    syncTodoSections,
    latestTodoState,
    normalizeTodos,
    todoClosed,
    todoSettleLines,
  };
}

export { latestTodoState, normalizeTodos, todoClosed, todoSettleLines };
