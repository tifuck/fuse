const frame = window.fuseFrame;
document.documentElement.dataset.platform = frame.platform;

for (const id of ["minimize", "maximize", "close"]) {
  document.getElementById(id).addEventListener("click", () => frame.send(id));
}
document.querySelector(".bar").addEventListener("dblclick", (event) => {
  if (!event.target.closest("button")) frame.send("maximize");
});

const maximize = document.getElementById("maximize");
const TOKENS = ["ink", "mist", "mute", "line"];

// Any CSS colour -> [r, g, b] via the browser's own parser.
const probe = document.createElement("i");
function rgb(color) {
  probe.style.color = "";
  probe.style.color = color;
  if (!probe.style.color) return null;
  document.body.append(probe);
  const value = getComputedStyle(probe).color;
  probe.remove();
  const parts = value.match(/[\d.]+/g);
  if (!parts) return null;
  const scale = value.startsWith("color(") ? 255 : 1;
  return parts.slice(0, 3).map((n) => Number(n) * scale);
}
function luminance([r, g, b]) {
  const lin = (c) => (c /= 255) <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b);
}
function contrast(a, b) {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}
// The logo takes the theme accent, unless that accent would vanish into the
// bar (a pale accent on a light theme): then it falls back to the text colour.
function legible(color, ink, fallback) {
  const c = rgb(color);
  const bg = rgb(ink);
  return c && bg && contrast(c, bg) >= 2.6 ? color : fallback;
}

function applyTheme(theme) {
  const root = document.documentElement;
  for (const name of TOKENS) {
    if (theme[name]) root.style.setProperty(`--${name}`, theme[name]);
  }
  const mist = getComputedStyle(root).getPropertyValue("--mist").trim();
  const ink = getComputedStyle(root).getPropertyValue("--ink").trim();
  if (theme.brass) root.style.setProperty("--logo", legible(theme.brass, ink, mist));
  if (theme.steel) root.style.setProperty("--logo-dot", legible(theme.steel, ink, mist));
  root.style.colorScheme = theme.scheme === "light" ? "light" : "dark";
  root.dataset.scheme = theme.scheme === "light" ? "light" : "dark";
}

frame.onState(({ maximized, focused, theme }) => {
  if (theme) applyTheme(theme);
  document.body.classList.toggle("is-maximized", maximized);
  document.body.classList.toggle("is-blurred", !focused);
  maximize.title = maximized ? "Restore" : "Maximize";
  maximize.setAttribute("aria-label", maximize.title);
});
