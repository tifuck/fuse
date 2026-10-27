// Starts the history sync and the linked thread's snapshot download from
// <head>, in parallel with the rest of the page's scripts. chat.js takes the
// responses in net.api (see takeBootSync) and openThread (see takeBootThread).
// Deliberately not a <link rel=preload> in the HTML: starting the
// multi-megabyte download before the stylesheets and scripts measurably
// delayed first paint on slow links.
(function () {
  try {
    if (location.pathname !== "/chat") return;
    window.__fuseBootSync = fetch("/chat/sync").catch(() => null);
    const id = (new URLSearchParams(location.search).get("t") || "").trim();
    if (!id) return;
    window.__fuseBootThread = {
      id,
      response: fetch("/chat/threads/" + encodeURIComponent(id) + "?pack=1&stream=1").catch(() => null),
    };
  } catch (_) {}
})();
