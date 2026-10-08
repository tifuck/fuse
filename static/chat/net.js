// HTTP/session transport: csrf, api, thread snapshots, stream reads, vault unlock.
import { S } from './state.js';
import { readQueue } from './turns/queue-store.js';

const VAULT_WHAT = "Unlock machines to use SSH. Your account password unwraps those secrets here — chat still works without it.";

function csrfToken() {
  const el = document.querySelector('meta[name="csrf-token"]');
  return (el && el.getAttribute("content")) || "";
}

function unpackSegments(list) {
  if (!Array.isArray(list)) return;
  for (const msg of list) {
    if (!msg || !Array.isArray(msg.segments)) continue;
    const at = { content: 0, reasoning: 0 };
    msg.segments = msg.segments.map((seg) => {
      if (!seg || !(seg.type in at)) return seg;
      if (typeof seg.text === "string" || typeof seg.n !== "number") {
        at[seg.type] += typeof seg.text === "string" ? seg.text.length : 0;
        return seg;
      }
      const { n, ...rest } = seg;
      rest.text = String(msg[seg.type] || "").slice(at[seg.type], at[seg.type] + n);
      at[seg.type] += n;
      return rest;
    });
  }
}

function unpackThread(data) {
  if (data && data.segments_packed) {
    unpackSegments(data.messages);
    unpackSegments(data.context_messages);
    delete data.segments_packed;
  }
  return data;
}

// A streamed snapshot (?stream=1) is NDJSON: for long threads a partial line
// with only the newest messages, then the complete snapshot from the same
// server build. Resolves with the complete one.
async function readThreadStream(res, onPartial) {
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let parts = [];
  let full = null;
  const take = (line) => {
    if (!line.trim()) return;
    const data = unpackThread(JSON.parse(line));
    if (data.partial) onPartial(data);
    else full = data;
  };
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    // Only the new chunk is scanned for the line break; the multi-megabyte
    // second line is joined once instead of being rescanned per chunk.
    let text = decoder.decode(value, { stream: true });
    let nl;
    while ((nl = text.indexOf("\n")) >= 0) {
      parts.push(text.slice(0, nl));
      take(parts.join(""));
      parts = [];
      text = text.slice(nl + 1);
    }
    if (text) parts.push(text);
  }
  parts.push(decoder.decode());
  take(parts.join(""));
  if (!full) throw new Error("Thread snapshot was incomplete.");
  return full;
}

// chat-boot.js starts the linked thread's download from <head>.
function takeBootThread(id) {
  const boot = window.__fuseBootThread;
  window.__fuseBootThread = null;
  return boot && boot.id === id ? boot.response : null;
}

function isTransportError(error) {
  return !error || !error.status;
}

export function createNet(ctx) {
  const { picker, sync, turns } = ctx;
    function sendSyncView(runId) {
    const next = String(runId || "");
    if (S._syncViewSent === next) return;
    if (S.socket && S.socket.readyState === WebSocket.OPEN) {
      try {
        S.socket.send(JSON.stringify({ type: "view", run_id: next || null }));
        S._syncViewSent = next;
      } catch (_) {}
    }
  }

    function showVaultUnlock(message, opts) {
    const force = opts && opts.force;
    if (!force && !S.config.ssh_hosts) return;
    if (window.fuseUI) {
      window.fuseUI.vaultPrompt({
        message: VAULT_WHAT,
        error: message || "",
        onUnlocked: () => {
          S.config.vault = "unlocked";
        },
      });
    }
  }

    async function api(path, opts) {
    opts = opts || {};
    const headers = { "X-CSRF-Token": csrfToken(), ...(opts.headers || {}) };
    if (opts.body && !headers["Content-Type"]) headers["Content-Type"] = "application/json";
    let res;
    try {
      res = await fetch(path, { ...opts, headers });
    } catch (_) {
      if (!isLive()) markDisconnected();
      const netError = new Error("Can't connect to the backend");
      netError.status = 0;
      throw netError;
    }
    const data = await res.json().catch(() => ({}));
    const errType = data.error && data.error.type;
    const nestedType = data.detail && data.detail.error && data.detail.error.type;
    if (res.status === 401 && (errType === "vault_locked" || nestedType === "vault_locked")) {
      showVaultUnlock("Unlock machines to use SSH.", { force: true });
      const vaultError = new Error((data.error && data.error.message) || "Unlock your machines");
      vaultError.status = 401;
      vaultError.vaultLocked = true;
      throw vaultError;
    }
    if (res.status === 401 || (res.status === 403 && data.error && data.error.type === "auth")) {
      window.location.assign("/login");
      const authError = new Error("Your session expired. Redirecting to sign in.");
      authError.status = res.status;
      throw authError;
    }
    if (!res.ok) {
      const detail = data.detail;
      const msg = typeof detail === "string" ? detail : typeof data.error === "string" ? data.error : data.error && data.error.message;
      const requestError = new Error(msg || `Request failed (${res.status})`);
      requestError.status = res.status;
      throw requestError;
    }
    return data;
  }

    // Command ids still waiting in a thread's local send queue. The snapshot
  // reports which of them the server already accepted (accepted_queue_ids).
  function queuedParam(id) {
    try {
      const items = readQueue(id);
      const ids = Array.isArray(items) ? items.map((item) => item && item.id).filter((v) => typeof v === "string" && v).slice(0, 64) : [];
      return ids.length ? "&queued=" + encodeURIComponent(ids.join(",")) : "";
    } catch (_) {
      return "";
    }
  }

    async function fetchThreadSnapshot(id, onPartial) {
    const queued = queuedParam(id);
    const boot = takeBootThread(id);
    const res = await ((!queued && boot) || fetch("/chat/threads/" + encodeURIComponent(id) + "?pack=1&stream=1" + queued, {
      headers: { "X-CSRF-Token": csrfToken() },
    }).catch(() => null));
    const streamed = res && res.ok && res.body && (res.headers.get("Content-Type") || "").includes("ndjson");
    if (streamed) {
      try { return await readThreadStream(res, onPartial); } catch (_) {}
    }
    // Errors, redirects to sign-in and interrupted streams take the ordinary
    // request path so they surface exactly as before.
    return unpackThread(await api("/chat/threads/" + id + "?pack=1" + queued));
  }

    function isLive() {
    return S.syncState === "live" && S.socket && S.socket.readyState === WebSocket.OPEN;
  }

    function requireLive() {
    if (isLive()) return true;
    picker.setHint("Can't connect to the backend", true);
    return false;
  }

    function markDisconnected() {
    if (S.syncState === "session") return;
    S.syncState = navigator.onLine ? "reconnecting" : "offline";
    S.syncLabel = navigator.onLine ? "Reconnecting" : "Disconnected";
    sync.syncStatus();
    // An open socket never closes itself while the server keeps pinging, so
    // nothing would flip syncState back to live. Closing runs the retry path.
    if (S.socket && S.socket.readyState === WebSocket.OPEN) S.socket.close();
  }

  return {
    api,
    fetchThreadSnapshot,
    isLive,
    markDisconnected,
    queuedParam,
    requireLive,
    sendSyncView,
    VAULT_WHAT,
    isTransportError,
    unpackThread,
  };
}

export { VAULT_WHAT, isTransportError, unpackThread };
