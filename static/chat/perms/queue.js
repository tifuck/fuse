// Permission request queue: cards, countdowns, decisions.
import { S } from '../state.js';

let permTick = 0;

// Cards already announced, and those already warned at 15 seconds left.
const permSpoken = new Set();
const permWarned = new Set();

function speakPerm(rec) {
  if (permSpoken.has(rec.id)) return;
  permSpoken.add(rec.id);
  const copy = permCardCopy(rec);
  const who = rec.requester && rec.requester !== "Main agent" ? ` from ${rec.requester}` : "";
  globalThis.fuseAnnounce?.(`Permission needed${who}: ${copy.action}${copy.path ? " " + copy.path : ""}. Allow or Deny.`, true);
}

const PERM_TOTAL_S = 60;

function permToolLabel(tool) {
  return ({
    read_file: "Read file",
    write_file: "Write file",
    search_replace: "Edit file",
    list_dir: "List folder",
    grep: "Search folder",
    glob: "Find files",
    run_terminal_command: "Run command",
    spawn_subagent: "Start subagent in",
  })[tool] || (tool || "Action").replace(/_/g, " ");
}

function permGrantKind(rec) {
  if (rec.grant_kind === "file" || rec.grant_kind === "dir") return rec.grant_kind;
  if (rec.secret || rec.tool === "read_file" || rec.tool === "write_file" || rec.tool === "search_replace") return "file";
  if (rec.tool === "list_dir" || rec.tool === "grep" || rec.tool === "glob") return "dir";
  return rec.tool === "run_terminal_command" || rec.tool === "spawn_subagent" ? "dir" : "";
}

function permGrantPath(rec) {
  return String(rec.grant_value || rec.path || rec.cwd || "").trim();
}

function permCardCopy(rec) {
  const kind = permGrantKind(rec);
  const path = permGrantPath(rec);
  const noun = kind === "file" ? "file" : "folder";
  const verb = rec.secret ? "Read secret" : permToolLabel(rec.tool);
  // Any non-trivial command asks even inside the project; only say
  // "outside" when the gate said so (reconnect-seeded cards carry no reasons).
  const shell = rec.tool === "run_terminal_command" || rec.tool === "monitor";
  const outside = (rec.reasons || []).some((r) => String(r).includes("outside"));
  const why = rec.secret ? "Secret-named file" : (shell && !outside ? "Command needs approval" : "Outside allowed folders");
  return {
    action: verb,
    noun,
    path,
    why,
    command: rec.tool === "run_terminal_command" ? String(rec.command || "").trim() : "",
    // The model's label for the command, above it as in Claude Code's dialog.
    description: shell ? String(rec.description || "").replace(/\s+/g, " ").trim() : "",
    host: rec.host || "",
  };
}

function permLeftS(expiresAt) {
  const t = Date.parse(expiresAt || "");
  if (!Number.isFinite(t)) return null;
  return Math.max(0, (t - Date.now()) / 1000);
}

function fmtPermCountdown(expiresAt) {
  const left = permLeftS(expiresAt);
  if (left == null) return "";
  const s = Math.floor(left);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

function permCardSignature(rec) {
  return [rec.tool, rec.host, rec.path, rec.cwd, rec.command, rec.description || "", rec.requester, rec.secret ? "1" : "0", rec.grant_kind || "", rec.grant_value || "", (rec.reasons || []).join("|")].join("\0");
}

function mixRgb(a, b, t) {
  const u = Math.max(0, Math.min(1, t));
  return `rgb(${Math.round(a[0] + (b[0] - a[0]) * u)} ${Math.round(a[1] + (b[1] - a[1]) * u)} ${Math.round(a[2] + (b[2] - a[2]) * u)})`;
}

let _permThemeCache = { key: "", brass: null, yellow: null, red: null };

function stopPermTick() {
  if (!permTick) return;
  cancelAnimationFrame(permTick);
  permTick = 0;
}

// Subagent asks live in child runs, which can outlive the parent turn and
// never appear in the parent snapshot; only the main agent's asks are
// settled by a parent snapshot or terminal event. Child cards still expire
// via expires_at or are resolved by their own agent.* permission updates.
function permFromSubagent(rec) {
  const who = rec && (rec.requester_label || rec.requester);
  if (who && typeof who === "object") return who.role === "subagent";
  const text = String(who || "").trim();
  if (!text || text.toLowerCase() === "main agent") return false;
  try {
    const parsed = JSON.parse(text);
    if (parsed && typeof parsed === "object") return parsed.role === "subagent";
  } catch (_) {}
  return true;
}

export function createQueue(ctx) {
  const { menu, net, sync, ui } = ctx;
    async function hydratePermissionCards(flow) {
    flow.querySelectorAll("[data-perm-id]").forEach((card) => {
      if (card.dataset.hydrated === "1") return;
      card.dataset.hydrated = "1";
      const id = card.getAttribute("data-perm-id");
      const body = card.querySelector("[data-perm-body]");
      if (!id || !body) return;
      net.api("/skills/proposals/" + encodeURIComponent(id)).then((data) => {
        if (typeof data.body === "string") body.value = data.body;
      }).catch(() => {});
    });
  }

    const permQueueEl = S.root.querySelector("[data-perm-queue]");

    const permQueueList = S.root.querySelector("[data-perm-queue-list]");

    const permChipLabel = S.root.querySelector("[data-perm-chip-label]");

    function acceptPermThread(data) {
    if (data && Object.prototype.hasOwnProperty.call(data, "bypass_permissions")) {
      S.threadBypass = Boolean(data.bypass_permissions);
    }
    if (data && Object.prototype.hasOwnProperty.call(data, "auto_permissions")) {
      S.threadAuto = Boolean(data.auto_permissions);
    }
    syncPermChip();
    clearMainAgentPermCards();
    const live = data && data.run && data.run.message;
    if (live) syncPermQueueFromMessage(live);
    renderPermQueue();
  }

    function syncPermChip() {
    if (!S.permChip) return;
    const mode = S.threadBypass ? "bypass" : (S.threadAuto ? "auto" : "ask");
    S.permChip.classList.toggle("is-on", mode !== "ask");
    S.permChip.classList.toggle("is-auto", mode === "auto");
    S.permChip.setAttribute("aria-expanded", S.permsOpen ? "true" : "false");
    const title = mode === "bypass"
      ? "Bypass on — no permission prompts"
      : (mode === "auto" ? "Auto-approve on — Jev allows ordinary calls" : "Permission prompts on");
    S.permChip.setAttribute("title", title);
    const label = mode === "bypass" ? "Bypass on" : (mode === "auto" ? "Auto" : "Bypass");
    if (permChipLabel) permChipLabel.textContent = label;
    // The spoken name starts with the visible word so voice control can say it;
    // the button opens the menu, so it carries no pressed state.
    S.permChip.setAttribute("aria-label", `${label}, permissions: ${mode === "bypass" ? "no prompts" : mode === "auto" ? "auto-approve" : "prompts on"}`);
    menu.paintPermsMode();
  }

    function upsertPermCard(rec, paint = true) {
    if (!rec || !rec.id) return;
    if (rec.status && rec.status !== "pending") {
      if (S.permCards.delete(rec.id) && paint) renderPermQueue();
      return;
    }
    const prev = S.permCards.get(rec.id) || {};
    const next = { ...prev, ...rec, status: "pending" };
    if (!(next.expires_at || next.expiresAt) && (prev.expires_at || prev.expiresAt)) {
      next.expires_at = prev.expires_at || prev.expiresAt;
    }
    const left = permLeftS(next.expires_at || next.expiresAt);
    if (left == null || left <= 0) {
      if (S.permCards.delete(rec.id) && paint) renderPermQueue();
      return;
    }
    S.permCards.set(rec.id, next);
    if (paint) renderPermQueue();
  }

    function syncPermQueueFromMessage(msg) {
    const rows = Array.isArray(msg && msg.permissions) ? msg.permissions : [];
    let changed = false;
    rows.forEach((perm) => {
      if (!perm || perm.kind !== "tool_permission" || !perm.id) return;
      if (perm.status === "pending") {
        const left = permLeftS(perm.expires_at || perm.expiresAt);
        if (left == null || left <= 0) {
          if (S.permCards.has(perm.id)) {
            S.permCards.delete(perm.id);
            changed = true;
          }
          return;
        }
        upsertPermCard(perm, false);
        changed = true;
      } else if (S.permCards.has(perm.id)) {
        S.permCards.delete(perm.id);
        changed = true;
      }
    });
    if (changed) renderPermQueue();
  }

    function seedPendingPermCards(panel) {
    // Subagent asks live in child runs, not the thread transcript: after a
    // reload or socket resync the durable pending list is the only way to
    // restore their cards. Stale entries self-drop via expires_at. Auto mode
    // still asks for secrets/destructive shells, so only bypass suppresses.
    if (!panel || !Array.isArray(panel.pending) || S.threadBypass) return;
    let changed = false;
    panel.pending.forEach((perm) => {
      if (!perm || perm.kind !== "tool_permission" || perm.status !== "pending" || !perm.id) return;
      upsertPermCard(perm, false);
      changed = true;
    });
    if (changed) renderPermQueue();
  }

    async function seedPendingFromServer(id) {
    try {
      const panel = await net.api(`/chat/threads/${encodeURIComponent(id)}/permissions`);
      if (id !== S.currentId) return;
      seedPendingPermCards(panel);
    } catch (error) { /* card replay is best effort */ }
  }

    function parseCssRgb(raw, fallback) {
    const text = String(raw || "").trim() || fallback;
    if (text.charAt(0) === "#") {
      const hex = text.length === 4
        ? text.slice(1).split("").map((ch) => ch + ch).join("")
        : text.slice(1, 7);
      return [parseInt(hex.slice(0, 2), 16), parseInt(hex.slice(2, 4), 16), parseInt(hex.slice(4, 6), 16)];
    }
    const m = text.match(/rgba?\(\s*([0-9.]+)\s*[, ]\s*([0-9.]+)\s*[, ]\s*([0-9.]+)/);
    if (m) return [Number(m[1]), Number(m[2]), Number(m[3])];
    return parseCssRgb(fallback, "#B5E05A");
  }

    function permThemeColors(el) {
    // Theme variables are identical for every card; resolve them once per
    // theme instead of forcing computed style on each card per frame.
    const root = document.documentElement;
    const key = root.getAttribute("data-theme") || "";
    if (_permThemeCache.key === key && _permThemeCache.brass) return _permThemeCache;
    const theme = getComputedStyle(el);
    _permThemeCache = {
      key,
      brass: parseCssRgb(theme.getPropertyValue("--brass"), "#B5E05A"),
      yellow: parseCssRgb(theme.getPropertyValue("--status-live"), "#FBBF24"),
      red: parseCssRgb(theme.getPropertyValue("--fault"), "#F87171"),
    };
    return _permThemeCache;
  }

    function permUrgencyColor(el, frac) {
    const { brass, yellow, red } = permThemeColors(el);
    const spent = 1 - Math.max(0, Math.min(1, frac));
    if (spent < 0.45) return mixRgb(brass, yellow, spent / 0.45);
    return mixRgb(yellow, red, (spent - 0.45) / 0.55);
  }

    function renderPermQueue() {
    if (!permQueueEl || !permQueueList) return;
    for (const [id, rec] of S.permCards) {
      const left = permLeftS(rec.expires_at || rec.expiresAt);
      if (rec.status !== "pending" || left == null || left <= 0) S.permCards.delete(id);
    }
    const pending = S.threadBypass
      ? []
      : [...S.permCards.values()].filter((rec) => rec.status === "pending" && permLeftS(rec.expires_at || rec.expiresAt) > 0);
    const want = new Set(pending.map((rec) => rec.id));
    [...permQueueList.children].forEach((el) => {
      const id = el.getAttribute("data-perm-card");
      if (!want.has(id)) el.remove();
    });
    pending.forEach((rec, index) => {
      let el = permQueueList.querySelector(`[data-perm-card="${CSS.escape(rec.id)}"]`);
      const sig = permCardSignature(rec);
      const depth = Math.min(index, 2);
      if (index > 2) {
        if (el) el.remove();
        return;
      }
      if (!el) {
        permQueueList.insertAdjacentHTML("beforeend", ui.permQueueCardHtml(rec));
        el = permQueueList.lastElementChild;
        speakPerm(rec);
      } else if (el.getAttribute("data-perm-sig") !== sig) {
        const wrap = document.createElement("template");
        wrap.innerHTML = ui.permQueueCardHtml(rec);
        const next = wrap.content.firstElementChild;
        el.replaceWith(next);
        el = next;
      }
      if (el) {
        el.setAttribute("data-perm-sig", sig);
        el.classList.toggle("is-front", index === 0);
        el.inert = index !== 0;
        el.style.setProperty("--perm-depth", String(depth));
      }
      if (el && permQueueList.children[index] !== el) permQueueList.insertBefore(el, permQueueList.children[index] || null);
    });
    const hasPending = pending.length > 0;
    permQueueEl.hidden = !hasPending;
    permQueueEl.classList.toggle("is-stacked", pending.length > 1);
    let badge = permQueueEl.querySelector("[data-perm-queue-count]");
    if (pending.length > 1) {
      if (!badge) {
        badge = document.createElement("span");
        badge.className = "chat-perm-queue-count mono";
        badge.setAttribute("data-perm-queue-count", "");
        permQueueEl.insertBefore(badge, permQueueList);
      }
      badge.textContent = pending.length + " waiting";
    } else if (badge) {
      badge.remove();
    }
    paintPermCountdowns();
    if (hasPending) startPermTick();
    else stopPermTick();
  }

    function startPermTick() {
    if (permTick) return;
    const loop = () => {
      permTick = 0;
      paintPermCountdowns();
      if (permQueueEl && !permQueueEl.hidden) startPermTick();
    };
    permTick = requestAnimationFrame(loop);
  }

    function paintPermCountdowns() {
    let anyPending = false;
    let dropped = false;
    for (const rec of [...S.permCards.values()]) {
      if (rec.status !== "pending") continue;
      const left = permLeftS(rec.expires_at || rec.expiresAt);
      if (left == null || left <= 0) {
        S.permCards.delete(rec.id);
        dropped = true;
        continue;
      }
      anyPending = true;
      const card = permQueueList && permQueueList.querySelector(`[data-perm-card="${CSS.escape(rec.id)}"]`);
      if (!card) continue;
      const expires = rec.expires_at || rec.expiresAt;
      const frac = Math.max(0, Math.min(1, left / PERM_TOTAL_S));
      const count = card.querySelector("[data-perm-count]");
      if (count) count.textContent = fmtPermCountdown(expires);
      card.style.setProperty("--perm-left", String(frac));
      card.style.setProperty("--perm-color", permUrgencyColor(card, frac));
      card.classList.toggle("is-low", left <= 15);
      if (left <= 15 && !permWarned.has(rec.id)) {
        permWarned.add(rec.id);
        globalThis.fuseAnnounce?.(`${Math.ceil(left)} seconds left to answer the permission request.`, true);
      }
    }
    if (dropped) renderPermQueue();
    else if (!anyPending) stopPermTick();
  }

    // "More time" restarts the card's minute (the server caps how long one
  // request may be held).
  async function extendPermCard(id) {
    if (!S.currentId) return;
    const before = S.permCards.get(id)?.expires_at;
    try {
      const data = await net.api(`/chat/threads/${encodeURIComponent(S.currentId)}/permissions/${encodeURIComponent(id)}`, {
        method: "POST",
        body: JSON.stringify({ action: "extend" }),
      });
      if (data.status !== "pending") return;
      permWarned.delete(id);
      upsertPermCard(data);
      globalThis.fuseAnnounce?.(data.expires_at === before ? "This request cannot be extended further." : "One more minute to answer.");
    } catch (error) {
      sync.showFailure("Could not extend the request", error);
    }
  }

    async function postPermDecision(id, action) {
    if (!S.currentId) return;
    try {
      const data = await net.api(`/chat/threads/${encodeURIComponent(S.currentId)}/permissions/${encodeURIComponent(id)}`, {
        method: "POST",
        body: JSON.stringify({ action }),
      });
      S.permCards.delete(id);
      renderPermQueue();
      if (S.permsOpen && (action === "allow_session" || action === "allow_host")) menu.refreshPerms();
    } catch (error) {
      sync.showFailure("Permission decision failed", error);
    }
  }

    function clearMainAgentPermCards() {
    for (const [id, rec] of S.permCards) if (!permFromSubagent(rec)) S.permCards.delete(id);
  }

    function sweepStuckPermCards() {
    clearMainAgentPermCards();
    renderPermQueue();
  }

  return {
    acceptPermThread,
    hydratePermissionCards,
    permQueueList,
    postPermDecision,
    renderPermQueue,
    seedPendingFromServer,
    seedPendingPermCards,
    sweepStuckPermCards,
    syncPermChip,
    syncPermQueueFromMessage,
    upsertPermCard,
    extendPermCard,
    PERM_TOTAL_S,
    fmtPermCountdown,
    permCardCopy,
    permCardSignature,
    permGrantKind,
    permGrantPath,
    permLeftS,
    permToolLabel,
  };
}

export { PERM_TOTAL_S, fmtPermCountdown, permCardCopy, permCardSignature, permGrantKind, permGrantPath, permLeftS, permToolLabel };
