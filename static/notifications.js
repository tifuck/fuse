/* Fuse chat-completion notifications: bell toggle + Web Push subscription.
   Per-device: each browser/PWA subscribes itself, so a turn finished on the
   server reaches every signed-in device (desktop, Android PWA, iOS PWA).
   Where Web Push is unavailable (Electron desktop wrapper, some embedded
   browsers), the bell falls back to local alerts: in-page notifications
   driven by the live chat event socket while this app is open. */
(() => {
  "use strict";
  if (!("serviceWorker" in navigator)) return;

  const csrf = () => {
    const el = document.querySelector('meta[name="csrf-token"]');
    return el ? el.content : "";
  };

  async function post(path, body) {
    const res = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf() },
      body: JSON.stringify(body),
    });
    if (!res.ok) throw new Error(String(res.status));
    return res.json();
  }

  // Signing out stops this device's alerts: otherwise chat titles and reply
  // previews keep arriving on a signed-out (possibly shared) device.
  async function dropDeviceSubscription() {
    const reg = await navigator.serviceWorker.getRegistration("/").catch(() => null);
    const sub = reg && "PushManager" in window ? await reg.pushManager.getSubscription().catch(() => null) : null;
    if (!sub) return;
    await post("/notifications/unsubscribe", { endpoint: sub.endpoint }).catch(() => {});
    await sub.unsubscribe().catch(() => {});
  }

  document.addEventListener("submit", (ev) => {
    const form = ev.target;
    if (!(form instanceof HTMLFormElement) || form.getAttribute("action") !== "/logout" || form.dataset.pushCleared) return;
    ev.preventDefault();
    form.dataset.pushCleared = "1";
    const timeout = new Promise((resolve) => setTimeout(resolve, 2500));
    Promise.race([dropDeviceSubscription(), timeout]).finally(() => form.submit());
  });

  const btn = document.querySelector("[data-notif-toggle]");
  if (!btn) return;
  const label = btn.querySelector("span");
  const IDLE = "Alerts";
  const STORE_KEY = "fuse.alerts.mode";
  let busy = false;
  let vapid = "";
  let serverCount = 0;
  let mode = null; // "push" | "local" | null (off)
  let localSub = null;

  // Alerts stay in the OS notification list (and in the dock counter GNOME
  // derives from it) until closed, and a tag does not replace one on Linux:
  // each thread keeps one, closed once the thread is read here or elsewhere.
  const shown = new Map();
  const tagFor = (threadId) => "fuse-chat-" + (threadId || "x");
  const viewedThread = () => new URLSearchParams(location.search).get("t") || "";
  const watching = () => document.visibilityState === "visible" && document.hasFocus();

  // `open` shows the thread in place: a reload would orphan every other alert.
  function show(threadId, title, body, open) {
    if (mode !== "local" || Notification.permission !== "granted") return;
    if (shown.has(threadId)) shown.get(threadId).close();
    const note = new Notification(title, { body, icon: "/static/icon-192.png", tag: tagFor(threadId) });
    shown.set(threadId, note);
    note.onclose = () => {
      if (shown.get(threadId) === note) shown.delete(threadId);
    };
    note.onclick = () => {
      note.close();
      window.focus();
      // window.focus() cannot raise or restore the desktop app's window.
      window.dispatchEvent(new Event("fuse:raise"));
      if (threadId && threadId !== viewedThread()) open();
    };
  }

  async function clear(threadId) {
    if (!threadId) return;
    const note = shown.get(threadId);
    if (note) {
      shown.delete(threadId);
      note.close();
    }
    if (mode !== "push") return;
    const reg = await navigator.serviceWorker.getRegistration("/").catch(() => null);
    const notes = reg && reg.getNotifications ? await reg.getNotifications({ tag: tagFor(threadId) }).catch(() => []) : [];
    notes.forEach((item) => item.close());
  }

  const clearWatched = () => {
    if (watching()) clear(viewedThread());
  };
  window.addEventListener("focus", clearWatched);
  document.addEventListener("visibilitychange", clearWatched);
  // A reloaded page cannot reach these alerts again, so they would never clear.
  window.addEventListener("pagehide", () => shown.forEach((note) => note.close()));

  window.fusePush = {
    get push() {
      return mode === "push";
    },
    get local() {
      return mode === "local";
    },
    show,
    clear,
  };

  function urlB64ToUint8Array(b64) {
    const pad = "=".repeat((4 - (b64.length % 4)) % 4);
    const raw = atob((b64 || "").replace(/-/g, "+").replace(/_/g, "/") + pad);
    return Uint8Array.from(raw, (ch) => ch.charCodeAt(0));
  }

  function save() {
    try {
      if (mode) localStorage.setItem(STORE_KEY, mode);
      else localStorage.removeItem(STORE_KEY);
    } catch {}
  }

  function paint() {
    const on = mode !== null;
    btn.classList.toggle("is-on", on);
    btn.setAttribute("aria-pressed", on ? "true" : "false");
    if (on) {
      btn.title =
        mode === "push"
          ? "Alerts are on for this device — click to turn off"
          : "Alerts are on for this device while the app is open — click to turn off";
    } else if (serverCount > 0) {
      btn.title = `Alerts are on for ${serverCount} device${serverCount === 1 ? "" : "s"} — click to add this device`;
    } else {
      btn.title = "Notify me when a chat finishes on any device";
    }
  }

  let hintTimer = 0;
  function hint(text) {
    if (!label) return;
    label.textContent = text;
    window.clearTimeout(hintTimer);
    hintTimer = window.setTimeout(() => {
      label.textContent = IDLE;
    }, 4000);
  }

  async function tryEnablePush() {
    // Returns "push" on success; throws when the browser has no reachable
    // push service (Electron wrapper, embedded Chromium) or the network fails.
    if (!("PushManager" in window)) throw new Error("push unavailable");
    const reg = await navigator.serviceWorker.register("/sw.js", { scope: "/" });
    await navigator.serviceWorker.ready;
    let sub = await reg.pushManager.getSubscription();
    if (!sub) {
      sub = await reg.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: urlB64ToUint8Array(vapid),
      });
    }
    const json = sub.toJSON();
    try {
      await post("/notifications/subscribe", {
        endpoint: json.endpoint,
        keys: json.keys || {},
        user_agent: String(navigator.userAgent || "").slice(0, 300),
      });
    } catch (error) {
      // Without the server row the bell would claim "on" for a dead subscription.
      await sub.unsubscribe().catch(() => {});
      throw error;
    }
    localSub = sub;
    return "push";
  }

  async function enable() {
    if (!("Notification" in window)) {
      hint("Unsupported");
      return;
    }
    const perm = await Notification.requestPermission();
    if (perm !== "granted") {
      hint("Blocked");
      return;
    }
    try {
      mode = await tryEnablePush();
    } catch {
      // No push service (desktop wrapper) or transient failure: local alerts
      // still cover this device through the live event socket while open.
      mode = "local";
    }
    save();
    paint();
    hint(mode === "push" ? "On" : "On · open");
    try {
      new Notification("Fuse", {
        body:
          mode === "push"
            ? "Alerts are on — you'll hear when a chat finishes."
            : "Alerts are on for this device while the app is open.",
        icon: "/static/icon-192.png",
        tag: "fuse-alerts-on",
      });
    } catch {}
  }

  async function disable() {
    if (mode === "push" && localSub) {
      const server = await post("/notifications/unsubscribe", { endpoint: localSub.endpoint }).then(() => true, () => false);
      const browser = await localSub.unsubscribe().catch(() => false);
      // Either side gone stops delivery; with both still live, alerts are on.
      if (!server && !browser) {
        hint("Failed");
        return;
      }
    }
    mode = null;
    localSub = null;
    save();
    paint();
    hint("Off");
  }

  async function refresh() {
    try {
      const reg =
        "serviceWorker" in navigator
          ? (await navigator.serviceWorker.getRegistration("/").catch(() => null)) || null
          : null;
      localSub =
        reg && "PushManager" in window ? await reg.pushManager.getSubscription().catch(() => null) : null;
      const query = localSub ? "?endpoint=" + encodeURIComponent(localSub.endpoint) : "";
      const res = await fetch("/notifications/status" + query, { headers: { Accept: "application/json" } });
      if (!res.ok) return;
      const data = await res.json();
      if (!data.enabled || !("Notification" in window)) {
        btn.hidden = true;
        return;
      }
      vapid = data.vapid;
      serverCount = Number(data.count) || 0;
      if (localSub && data.mine === false) {
        // Another account's subscription, left behind by a sign-out on this
        // browser: drop it so that account's alerts stop arriving here.
        await localSub.unsubscribe().catch(() => {});
        localSub = null;
        if (mode === "push") mode = null;
      }
      if (localSub) mode = "push";
      else if (mode !== "local") mode = null;
      if (mode === "local" && Notification.permission !== "granted") {
        mode = null;
        save();
      }
      paint();
    } catch {}
  }

  btn.addEventListener("click", async () => {
    if (busy) return;
    busy = true;
    try {
      if (mode) await disable();
      else await enable();
    } catch {
      hint("Failed");
    }
    busy = false;
  });

  let stored = null;
  try {
    stored = localStorage.getItem(STORE_KEY);
  } catch {}
  if (stored === "local") mode = "local";
  refresh();
})();
