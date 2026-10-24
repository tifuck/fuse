const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("fuseFrame", {
  platform: process.platform,
  send: (action) => ipcRenderer.send("titlebar", action),
  icon: (url) => ipcRenderer.send("titlebar-icon", url),
  onState: (fn) => ipcRenderer.on("titlebar-state", (_event, state) => fn(state)),
});
