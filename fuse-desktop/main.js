const { app, BaseWindow, WebContentsView, dialog, ipcMain, nativeImage, shell } = require("electron");
const { autoUpdater } = require("electron-updater");
const { appendFileSync, mkdirSync, readFileSync, statSync, writeFileSync } = require("fs");
const path = require("path");

// The site this build wraps: FUSE_URL at runtime, else the fuseUrl that
// scripts/dist.js stamps into package.json, else site.json for `npm start`.
function siteUrl() {
  if (process.env.FUSE_URL) return process.env.FUSE_URL;
  const stamped = require("./package.json").fuseUrl;
  if (stamped) return stamped;
  try {
    return JSON.parse(readFileSync(path.join(__dirname, "site.json"), "utf8")).url;
  } catch {
    return "https://fuse.example.com";
  }
}
const APP_URL = siteUrl();

// Windows attributes toasts to the Start-menu shortcut, whose AUMID the NSIS
// installer sets to appId. Electron's default (electron.app.Fuse) matches no
// shortcut, so alerts lost the app's name and icon in the Action Center.
const APP_ID = require("./package.json").fuseAppId;
if (process.platform === "win32" && APP_ID) app.setAppUserModelId(APP_ID);
const APP_ORIGIN = new URL(APP_URL).origin;
const BACKGROUND = "#141615";

// Windows and Linux draw Fuse's own dark title bar instead of the OS frame
// (the light-gray GNOME header on Ubuntu). macOS keeps its native frame.
const CUSTOM_FRAME = process.platform !== "darwin";
const TITLEBAR_HEIGHT = CUSTOM_FRAME ? 34 : 0;

// Chromium sandboxes through user namespaces when it can, else through the
// setuid chrome-sandbox helper. Only when neither is available (AppImage or a
// stripped helper on a kernel that restricts unprivileged user namespaces)
// is the sandbox turned off; it used to be off for every AppImage.
function userNamespacesUsable() {
  const flag = (file) => {
    try {
      return readFileSync(file, "utf8").trim();
    } catch {
      return null;
    }
  };
  if (flag("/proc/sys/kernel/unprivileged_userns_clone") === "0") return false;
  if (flag("/proc/sys/kernel/apparmor_restrict_unprivileged_userns") === "1") return false;
  return true;
}

if (process.platform === "linux" && process.getuid() !== 0) {
  let setuidHelper = false;
  if (!process.env.APPIMAGE) {
    try {
      const helper = path.join(path.dirname(process.execPath), "chrome-sandbox");
      setuidHelper = Boolean(statSync(helper).mode & 0o4000);
    } catch {}
  }
  if (!setuidHelper && !userNamespacesUsable()) app.commandLine.appendSwitch("no-sandbox");
}

// GPU rendering on Linux flashes random pink/green/black garbage blocks on
// some AMD and NVIDIA machines (seen on Strix Halo and an RTX 5060). Machines
// where Chromium already falls back to software never show it, so draw in
// software everywhere on Linux.
if (process.platform === "linux") app.disableHardwareAcceleration();

let win = null;
let bar = null;
let site = null;

// The bar takes the site's active theme (Paper, Sand, Midnight, ...). The site
// publishes its background in <meta name="theme-color"> on every theme change;
// the other tokens are read from the page. The last theme is remembered so
// the next launch opens in the right colours instead of flashing dark.
const THEME_FILE = () => path.join(app.getPath("userData"), "frame-theme.json");
const HEX = /^#[0-9a-f]{3,8}$/i;
let frameTheme = null;

function loadFrameTheme() {
  try {
    const saved = JSON.parse(readFileSync(THEME_FILE(), "utf8"));
    if (HEX.test(saved.ink)) frameTheme = saved;
  } catch {}
}

async function syncFrameTheme() {
  if (!site || site.webContents.isDestroyed()) return;
  let theme;
  try {
    theme = await site.webContents.executeJavaScript(`(() => {
      const css = getComputedStyle(document.documentElement);
      const read = (name) => css.getPropertyValue(name).trim();
      const meta = document.querySelector('meta[name="theme-color"]');
      return {
        ink: (meta && meta.content.trim()) || read("--ink"),
        mist: read("--mist"),
        mute: read("--mute"),
        line: read("--line"),
        brass: read("--brass"),
        steel: read("--steel"),
        scheme: css.colorScheme === "light" ? "light" : "dark",
      };
    })()`);
  } catch {
    return;
  }
  if (!theme || !HEX.test(theme.ink) || !win) return;
  if (JSON.stringify(theme) === JSON.stringify(frameTheme)) return;
  frameTheme = theme;
  win.setBackgroundColor(theme.ink);
  site.setBackgroundColor(theme.ink);
  if (bar) bar.setBackgroundColor(theme.ink);
  sendFrameState();
  try { writeFileSync(THEME_FILE(), JSON.stringify(theme)); } catch {}
}

function layout() {
  if (!win) return;
  // The content view's own bounds are the window's client area once the OS
  // has applied a maximize/resize; getContentBounds() can still report the
  // old size inside the "maximize" event on Linux.
  const { width, height } = win.contentView.getBounds();
  const top = bar && !win.isFullScreen() ? TITLEBAR_HEIGHT : 0;
  if (bar) bar.setBounds({ x: 0, y: 0, width, height: top });
  site.setBounds({ x: 0, y: top, width, height: Math.max(0, height - top) });
}

function sendFrameState() {
  if (!bar || bar.webContents.isDestroyed()) return;
  bar.webContents.send("titlebar-state", {
    maximized: win.isMaximized(),
    focused: win.isFocused(),
    theme: frameTheme,
  });
}

function handleShortcuts(event, input) {
  if (input.type !== "keyDown") return;
  const mod = input.control || input.meta;
  const key = input.key.toLowerCase();
  const wc = site.webContents;
  if (input.key === "F5" || (mod && key === "r")) {
    input.shift ? wc.reloadIgnoringCache() : wc.reload();
  } else if (input.control && input.key === "\\") {
    // Push-to-talk chord owned by an external listener; no view should act on it.
  } else if (input.key === "F12" || (mod && input.shift && key === "i")) {
    wc.toggleDevTools();
  } else if (mod && (key === "=" || key === "+")) {
    wc.setZoomLevel(wc.getZoomLevel() + 0.5);
  } else if (mod && key === "-") {
    wc.setZoomLevel(wc.getZoomLevel() - 0.5);
  } else if (mod && key === "0") {
    wc.setZoomLevel(0);
  } else if (input.alt && input.key === "ArrowLeft" && wc.navigationHistory.canGoBack()) {
    wc.navigationHistory.goBack();
  } else if (input.alt && input.key === "ArrowRight" && wc.navigationHistory.canGoForward()) {
    wc.navigationHistory.goForward();
  } else {
    return;
  }
  event.preventDefault();
}

// Window focus can return on the title bar or on the site with nothing
// focused; webContents.focus() alone does not bring the composer caret back.
function restoreFocus() {
  if (!site || site.webContents.isDestroyed()) return;
  site.webContents.focus();
  site.webContents.executeJavaScript(`(() => {
    const a = document.activeElement;
    if (a && a !== document.body) return;
    const e = window.__fuseLastEditable;
    if (e && e.isConnected) e.focus();
  })()`).catch(() => {});
}

function originOf(url) {
  try {
    return new URL(url).origin;
  } catch {
    return "";
  }
}

// Page content picks these URLs: only hand web links to the OS, never a
// custom scheme that would launch some installed protocol handler.
const EXTERNAL_PROTOCOLS = new Set(["https:", "http:", "mailto:"]);
function openExternalSafe(url) {
  let protocol = "";
  try {
    protocol = new URL(url).protocol;
  } catch {
    return;
  }
  if (EXTERNAL_PROTOCOLS.has(protocol)) shell.openExternal(url);
}

function createWindow() {
  const background = (frameTheme && frameTheme.ink) || BACKGROUND;
  win = new BaseWindow({
    width: 1280,
    height: 820,
    minWidth: 420,
    minHeight: 480,
    backgroundColor: background,
    autoHideMenuBar: true,
    frame: !CUSTOM_FRAME,
    title: "Fuse",
    icon: path.join(__dirname, "build", "icon.png"),
  });

  site = new WebContentsView({
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      preload: path.join(__dirname, "site-preload.js"),
    },
  });
  site.setBackgroundColor(background);
  win.contentView.addChildView(site);

  if (CUSTOM_FRAME) {
    bar = new WebContentsView({
      webPreferences: {
        contextIsolation: true,
        nodeIntegration: false,
        sandbox: true,
        preload: path.join(__dirname, "titlebar", "preload.js"),
      },
    });
    bar.setBackgroundColor(background);
    win.contentView.addChildView(bar);
    bar.webContents.loadFile(path.join(__dirname, "titlebar", "index.html"));
    bar.webContents.on("did-finish-load", sendFrameState);
    bar.webContents.on("before-input-event", handleShortcuts);
    for (const name of ["maximize", "unmaximize", "focus", "blur"]) win.on(name, sendFrameState);
    site.webContents.on("did-change-theme-color", syncFrameTheme);
    site.webContents.on("dom-ready", syncFrameTheme);
  }

  // bounds-changed fires after the new size is laid out; the window events
  // are a backstop, re-run once more on the next tick for late WM updates.
  win.contentView.on("bounds-changed", layout);
  for (const name of ["resize", "maximize", "unmaximize", "restore", "enter-full-screen", "leave-full-screen"]) {
    win.on(name, () => {
      layout();
      setTimeout(layout, 50);
    });
  }
  layout();

  const wc = site.webContents;
  wc.on("before-input-event", handleShortcuts);
  win.on("focus", restoreFocus);
  if (bar) bar.webContents.on("focus", restoreFocus);
  wc.on("dom-ready", () => {
    wc.executeJavaScript(`document.addEventListener("focusin", (ev) => {
      const t = ev.target;
      if (t && (t.isContentEditable || /^(INPUT|TEXTAREA)$/.test(t.tagName))) window.__fuseLastEditable = t;
    }, true)`).catch(() => {});
  });

  wc.setWindowOpenHandler(({ url }) => {
    if (originOf(url) === APP_ORIGIN) {
      wc.loadURL(url);
    } else {
      openExternalSafe(url);
    }
    return { action: "deny" };
  });

  wc.on("will-navigate", (event, url) => {
    if (url !== wc.getURL() && originOf(url) !== APP_ORIGIN) {
      event.preventDefault();
      openExternalSafe(url);
    }
  });
  // will-navigate does not see server redirects: an in-origin URL that
  // redirects elsewhere would otherwise render a foreign site in this window.
  wc.on("will-redirect", (event, url, _inPlace, isMainFrame) => {
    // Newer Electron puts the details on the event; older passes them positionally.
    const target = event.url || url;
    const main = event.isMainFrame !== undefined ? event.isMainFrame : isMainFrame;
    if (main && originOf(target) !== APP_ORIGIN) {
      event.preventDefault();
      openExternalSafe(target);
    }
  });

  win.on("closed", () => {
    wc.close();
    if (bar) bar.webContents.close();
    win = bar = site = null;
  });

  wc.loadURL(APP_URL);
  wc.focus();
}

ipcMain.on("titlebar", (event, action) => {
  if (!win || !bar || event.sender !== bar.webContents) return;
  if (action === "minimize") win.minimize();
  else if (action === "maximize") win.isMaximized() ? win.unmaximize() : win.maximize();
  else if (action === "close") win.close();
});

ipcMain.on("site-theme", (event) => {
  if (site && event.sender === site.webContents) syncFrameTheme();
});

ipcMain.on("site-raise", (event) => {
  if (!win || !site || event.sender !== site.webContents) return;
  if (win.isMinimized()) win.restore();
  win.show();
  win.focus();
});

// The title bar draws the logo in the theme's colours. Windows' taskbar and
// X11 window lists show it; GNOME's dock always shows the .desktop icon.
ipcMain.on("titlebar-icon", (event, url) => {
  if (!win || !bar || event.sender !== bar.webContents) return;
  if (typeof url !== "string" || !url.startsWith("data:image/png;base64,")) return;
  const image = nativeImage.createFromDataURL(url);
  if (!image.isEmpty()) win.setIcon(image);
});

// A single launch-time check with install-on-quit never updated a .deb install
// and failed silently, so check every few hours, offer a restart once an
// update is downloaded, and keep the updater's log in <logs>/updater.log.
const UPDATE_CHECK_MS = 4 * 60 * 60 * 1000;

function startUpdater() {
  const dir = app.getPath("logs");
  try {
    mkdirSync(dir, { recursive: true });
  } catch {}
  const file = path.join(dir, "updater.log");
  const write = (level) => (...args) => {
    try {
      appendFileSync(file, `${new Date().toISOString()} ${level} ${args.join(" ")}\n`);
    } catch {}
  };
  autoUpdater.logger = { info: write("info"), warn: write("warn"), error: write("error"), debug() {} };
  autoUpdater.autoDownload = true;
  autoUpdater.autoInstallOnAppQuit = true;

  let offered = null;
  autoUpdater.on("update-downloaded", async ({ version }) => {
    if (offered === version) return;
    offered = version;
    const options = {
      type: "info",
      buttons: ["Restart now", "Later"],
      defaultId: 0,
      cancelId: 1,
      message: `Fuse ${version} is ready to install`,
      detail: "Restart Fuse to finish updating. Choosing Later installs it the next time Fuse quits.",
    };
    const { response } = await (win ? dialog.showMessageBox(win, options) : dialog.showMessageBox(options));
    if (response === 0) autoUpdater.quitAndInstall(true, true);
  });

  const check = () => autoUpdater.checkForUpdates().catch(() => {});
  check();
  setInterval(check, UPDATE_CHECK_MS);
}

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
    // Lets the site drop its own sidebar brand in favour of this title bar.
    if (CUSTOM_FRAME) app.userAgentFallback = `${app.userAgentFallback} FuseDesktop/${app.getVersion()}`;
    loadFrameTheme();
    createWindow();

    if (app.isPackaged) startUpdater();

    app.on("activate", () => {
      if (!win) createWindow();
    });
  });

  app.on("window-all-closed", () => {
    if (process.platform !== "darwin") app.quit();
  });
}
