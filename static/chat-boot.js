// Starts the linked thread's snapshot download from <head>, in parallel with
// the rest of the page's scripts and the history sync. chat.js takes the
// response in openThread (see takeBootThread). Deliberately not a <link
// rel=preload> in the HTML: starting the multi-megabyte download before the
// stylesheets and scripts measurably delayed first paint on slow links.
(function () {
  try {
    const id = (new URLSearchParams(location.search).get("t") || "").trim();
    if (!id || location.pathname !== "/chat") return;
    window.__fuseBootThread = {
      id,
      response: fetch("/chat/threads/" + encodeURIComponent(id) + "?pack=1&stream=1").catch(() => null),
    };
  } catch (_) {}
})();
