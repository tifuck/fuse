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
function applyTheme(theme) {
  const root = document.documentElement;
  for (const name of TOKENS) {
    if (theme[name]) root.style.setProperty(`--${name}`, theme[name]);
  }
  root.style.colorScheme = theme.scheme === "light" ? "light" : "dark";
  root.dataset.scheme = theme.scheme === "light" ? "light" : "dark";
}

frame.onState(({ maximized, focused, title, theme }) => {
  if (theme) applyTheme(theme);
  document.body.classList.toggle("is-maximized", maximized);
  document.body.classList.toggle("is-blurred", !focused);
  maximize.title = maximized ? "Restore" : "Maximize";
  maximize.setAttribute("aria-label", maximize.title);
  if (title) document.getElementById("title").textContent = title;
});
