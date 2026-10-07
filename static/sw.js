/* Fuse service worker: Web Push for finished chats.
   A notification is suppressed when a visible window is already showing the
   finished thread (the user is watching it on this device). */
const ICON = "/static/icon-192.png";

self.addEventListener("install", () => {
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(self.clients.claim());
});

async function threadVisible(threadId) {
  if (!threadId) return false;
  const all = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
  return all.some((client) => {
    if (client.visibilityState !== "visible") return false;
    try {
      return new URL(client.url).searchParams.get("t") === threadId;
    } catch {
      return false;
    }
  });
}

self.addEventListener("push", (event) => {
  let data = {};
  try {
    data = event.data ? event.data.json() : {};
  } catch {
    data = {};
  }
  if (!data || typeof data !== "object") data = {};
  const title = typeof data.title === "string" && data.title ? data.title : "Fuse";
  const body = typeof data.body === "string" ? data.body : "";
  // Same-origin only: a leading "/" alone would let "//host" through.
  let url = "/chat";
  if (typeof data.url === "string" && data.url.startsWith("/")) {
    try {
      const parsed = new URL(data.url, self.location.origin);
      if (parsed.origin === self.location.origin) url = parsed.pathname + parsed.search + parsed.hash;
    } catch {}
  }
  const threadId = typeof data.threadId === "string" ? data.threadId : "";
  event.waitUntil(
    (async () => {
      if (await threadVisible(threadId)) return;
      await self.registration.showNotification(title, {
        body,
        icon: ICON,
        badge: ICON,
        tag: threadId ? `fuse-chat-${threadId}` : "fuse-chat",
        renotify: true,
        data: { url },
      });
    })()
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const target = (event.notification.data && event.notification.data.url) || "/chat";
  event.waitUntil(
    (async () => {
      const all = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
      const chat = all.find((client) => {
        try {
          return new URL(client.url).pathname === "/chat";
        } catch {
          return false;
        }
      });
      if (chat) {
        await chat.focus();
        try {
          await chat.navigate(new URL(target, self.location.origin).href);
        } catch {}
        return;
      }
      await self.clients.openWindow(target);
    })()
  );
});
