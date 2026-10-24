const { ipcRenderer } = require("electron");

// The site's accent lives in <html>'s inline style and data attributes; only
// the background reaches <meta name="theme-color">. Watching <html> lets the
// title bar and window icon follow an accent change the moment it happens.
// Nothing is exposed to the page.
let timer = 0;
window.addEventListener("DOMContentLoaded", () => {
  new MutationObserver(() => {
    clearTimeout(timer);
    timer = setTimeout(() => ipcRenderer.send("site-theme"), 80);
  }).observe(document.documentElement, { attributes: true, attributeFilter: ["style", "data-chat-theme"] });
});
