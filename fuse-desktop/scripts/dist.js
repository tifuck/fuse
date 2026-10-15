// Build installers for this instance without committing its URL or app id.
// Values come from FUSE_URL / FUSE_APP_ID or site.json (gitignored):
//   {"url": "https://fuse.example.com", "appId": "com.example.fuse"}
// Keep appId stable once released: installed apps update only within it.
const { spawnSync } = require("child_process");
const { readFileSync } = require("fs");
const path = require("path");

const root = path.join(__dirname, "..");
let site = {};
try {
  site = JSON.parse(readFileSync(path.join(root, "site.json"), "utf8"));
} catch {}
const url = String(process.env.FUSE_URL || site.url || "").replace(/\/+$/, "");
const appId = process.env.FUSE_APP_ID || site.appId || "";
if (!url || !appId) {
  console.error("Set FUSE_URL and FUSE_APP_ID, or create fuse-desktop/site.json with url and appId.");
  process.exit(1);
}
const bin = path.join(root, "node_modules", ".bin", process.platform === "win32" ? "electron-builder.cmd" : "electron-builder");
const args = [
  ...process.argv.slice(2),
  `-c.appId=${appId}`,
  `-c.publish.provider=generic`,
  `-c.publish.url=${url}/desktop/`,
  `-c.extraMetadata.fuseUrl=${url}`,
];
const result = spawnSync(bin, args, { stdio: "inherit", cwd: root, shell: process.platform === "win32" });
process.exit(result.status ?? 1);
