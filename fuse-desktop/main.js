const { app, BrowserWindow, shell } = require("electron");
const { autoUpdater } = require("electron-updater");
const { statSync } = require("fs");
const path = require("path");

const APP_URL = process.env.FUSE_URL || "https://fuse.example.com";

if (process.env.APPIMAGE) {
  app.commandLine.appendSwitch("no-sandbox");
} else if (process.platform === "linux" && process.getuid() !== 0) {
  try {
    const helper = path.join(path.dirname(process.execPath), "chrome-sandbox");
    if (!(statSync(helper).mode & 0o4000)) app.commandLine.appendSwitch("no-sandbox");
  } catch {}
}

let win = null;

const gotLock = app.requestSingleInstanceLock();
if (!gotLock) {
  app.quit();
} else {
  app.on("second-instance", () => {
    if (win) {
      if (win.isMinimized()) win.restore();
      win.focus();
    }
  });

  app.whenReady().then(() => {
    win = new BrowserWindow({
      width: 1280,
      height: 820,
      minWidth: 420,
      minHeight: 480,
      backgroundColor: "#141615",
      autoHideMenuBar: true,
      title: "Fuse",
      icon: require("path").join(__dirname, "build", "icon.png"),
      webPreferences: {
        contextIsolation: true,
        nodeIntegration: false,
        sandbox: true,
      },
    });

    win.webContents.setWindowOpenHandler(({ url }) => {
      if (new URL(url).origin === APP_URL) {
        win.loadURL(url);
      } else {
        shell.openExternal(url);
      }
      return { action: "deny" };
    });

    win.webContents.on("will-navigate", (event, url) => {
      if (url !== win.webContents.getURL() && new URL(url).origin !== APP_URL) {
        event.preventDefault();
        shell.openExternal(url);
      }
    });

    win.loadURL(APP_URL);

    if (app.isPackaged) {
      autoUpdater.autoDownload = true;
      autoUpdater.autoInstallOnAppQuit = true;
      autoUpdater.checkForUpdatesAndNotify().catch(() => {});
    }

    app.on("activate", () => {
      if (BrowserWindow.getAllWindows().length === 0) app.whenReady().then(() => {});
    });
  });

  app.on("window-all-closed", () => {
    if (process.platform !== "darwin") app.quit();
  });
}
