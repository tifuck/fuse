const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("fuseFrame", {
  platform: process.platform,
  send: (action) => ipcRenderer.send("titlebar", action),
  onState: (fn) => ipcRenderer.on("titlebar-state", (_event, state) => fn(state)),
});
