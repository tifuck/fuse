/* Apply before paint; view rendering and navigation remain owned by chat.js. */
(() => {
  const themes = {
    fuse: { label: "Fuse", scheme: "dark", ink: "#121212", panel: "#1a1a1a", raise: "#242424", mist: "#ececec", mute: "#9a9a9a", line: "#2e2e2e", brass: "#c5df88", steel: "#a6c9af", reason: "#aebcc5", fault: "#f29d8e", ok: "#aed397", onBrass: "#121212", code: "#0e0e0e" },
    paper: { label: "Paper", scheme: "light", ink: "#ffffff", panel: "#f3f1ec", raise: "#e7e3da", mist: "#1c1b18", mute: "#6a655c", line: "#d5cfc4", brass: "#b45c2e", steel: "#2f6a4a", reason: "#4e5a66", fault: "#c24b40", ok: "#2f7a48", onBrass: "#ffffff", code: "#f4f1ea" },
    midnight: { label: "Midnight", scheme: "dark", ink: "#0a1018", panel: "#121a28", raise: "#1b2638", mist: "#e8eef6", mute: "#8b97a8", line: "#243044", brass: "#e3bc81", steel: "#7ec8e8", reason: "#a8b4c8", fault: "#f0a49b", ok: "#8fcdb0", onBrass: "#0a1018", code: "#070c12" },
    void: { label: "Void", scheme: "dark", ink: "#000000", panel: "#0c0c0c", raise: "#161616", mist: "#f2f2f2", mute: "#8c8c8c", line: "#222222", brass: "#5ef2c0", steel: "#8ad4c4", reason: "#9aa4b0", fault: "#ff8b7e", ok: "#7ee0a8", onBrass: "#000000", code: "#000000" },
    ember: { label: "Ember", scheme: "dark", ink: "#16110e", panel: "#1f1915", raise: "#2a221c", mist: "#f3ebe3", mute: "#a89888", line: "#3a3028", brass: "#e08a5c", steel: "#d4a07a", reason: "#cbb6a6", fault: "#ef8b78", ok: "#c5c07a", onBrass: "#16110e", code: "#120e0b" },
    moss: { label: "Moss", scheme: "dark", ink: "#101410", panel: "#171c17", raise: "#222822", mist: "#e8eee6", mute: "#8f9a88", line: "#2c342c", brass: "#c6b06a", steel: "#8fbf8a", reason: "#b7c4b0", fault: "#e09a7a", ok: "#9dca86", onBrass: "#101410", code: "#0c100c" },
    rose: { label: "Rose", scheme: "dark", ink: "#161014", panel: "#1f171c", raise: "#2b2128", mist: "#f4e8ee", mute: "#a8909a", line: "#3a2c34", brass: "#e8a0b4", steel: "#c9a0c4", reason: "#d0b8c4", fault: "#ef8b9a", ok: "#c8c090", onBrass: "#161014", code: "#120c10" },
    ocean: { label: "Ocean", scheme: "dark", ink: "#07141a", panel: "#0d1c24", raise: "#152830", mist: "#dff3f4", mute: "#7fa0a8", line: "#1e3640", brass: "#6ad4c8", steel: "#7eb8d4", reason: "#a8c8d0", fault: "#f0a49b", ok: "#8fcdb0", onBrass: "#07141a", code: "#051016" },
    dusk: { label: "Dusk", scheme: "dark", ink: "#12101a", panel: "#1a1826", raise: "#262436", mist: "#ece8f6", mute: "#9a94b0", line: "#322e44", brass: "#c4b5e8", steel: "#8eb0e8", reason: "#b8b0d0", fault: "#f0a4c0", ok: "#a8d0c0", onBrass: "#12101a", code: "#0e0c16" },
    wine: { label: "Wine", scheme: "dark", ink: "#140a0e", panel: "#1c1014", raise: "#2a181c", mist: "#f4e8e6", mute: "#a88888", line: "#3a2428", brass: "#d4b06a", steel: "#c49090", reason: "#d0b8b0", fault: "#ef8b78", ok: "#c0c080", onBrass: "#140a0e", code: "#10080c" },
    slate: { label: "Slate", scheme: "dark", ink: "#12151a", panel: "#191e26", raise: "#242a34", mist: "#e8eef4", mute: "#8b97a8", line: "#2c3440", brass: "#7aa2ff", steel: "#8ec8e0", reason: "#a8b8d0", fault: "#f0a49b", ok: "#8fcdb0", onBrass: "#12151a", code: "#0e1218" },
    carbon: { label: "Carbon", scheme: "dark", ink: "#0a0a0a", panel: "#141414", raise: "#1e1e1e", mist: "#f0f0f0", mute: "#8a8a8a", line: "#2a2a2a", brass: "#ff8a4c", steel: "#d4b090", reason: "#b0a8a0", fault: "#ff8b7e", ok: "#c8d080", onBrass: "#0a0a0a", code: "#050505" },
    sand: { label: "Sand", scheme: "light", ink: "#f6f0e6", panel: "#ebe3d4", raise: "#dfd4c2", mist: "#2a241c", mute: "#6e6558", line: "#d4c8b4", brass: "#b85c38", steel: "#3d6b52", reason: "#5a564c", fault: "#c24b40", ok: "#3d7a48", onBrass: "#ffffff", code: "#f3ebe0" },
    frost: { label: "Frost", scheme: "light", ink: "#f3f6f8", panel: "#e6eef2", raise: "#d7e2e8", mist: "#1a2428", mute: "#5c6c74", line: "#c8d4dc", brass: "#2a7a8c", steel: "#3d6a80", reason: "#4e5a66", fault: "#c24b40", ok: "#2f7a48", onBrass: "#ffffff", code: "#eef4f6" },
    matcha: { label: "Matcha", scheme: "light", ink: "#f3f4ea", panel: "#e6ead8", raise: "#d8dec6", mist: "#1c2418", mute: "#5c6850", line: "#c8d0b4", brass: "#4a6b32", steel: "#3d6a52", reason: "#4e5a46", fault: "#c24b40", ok: "#2f7a48", onBrass: "#ffffff", code: "#eef0e2" },
    ivory: { label: "Ivory", scheme: "light", ink: "#fffcf7", panel: "#f4efe6", raise: "#e8e0d4", mist: "#1c1a28", mute: "#5c5868", line: "#d8d0c4", brass: "#3d4a9a", steel: "#3d6a80", reason: "#4e5a66", fault: "#c24b40", ok: "#2f7a48", onBrass: "#ffffff", code: "#faf6f0" },
  };
  const BACKDROPS = {
    none: { label: "None", hint: "Solid" },
    glyphs: { label: "Glyphs", hint: "ASCII field" },
    life: { label: "Life", hint: "Cellular automaton" },
    rain: { label: "Rain", hint: "Falling glyphs" },
    ripple: { label: "Ripple", hint: "Braille waves" },
    signal: { label: "Signal", hint: "Data lanes · CRT glow" },
    circuit: { label: "Circuit", hint: "Signal traces" },
    garden: { label: "Garden", hint: "8-bit sakura · multicolor" },
    botanica: { label: "Botanica", hint: "Pen & ink blooms" },
    ivy: { label: "Nouveau", hint: "Art Nouveau vines" },
    meadow: { label: "Meadow", hint: "Pixel grass · fireflies" },
    pond: { label: "Pond", hint: "Pixel lilies · koi" },
    koi: { label: "Koi", hint: "Pond from above · koi" },
    orbit: { label: "Orbit", hint: "Pixel planets · comets" },
    galaxy: { label: "Galaxy", hint: "Pixel spiral · star clusters" },
    eclipse: { label: "Eclipse", hint: "Planet limb · sunrise" },
    belt: { label: "Belt", hint: "Asteroids · gas giant" },
    contact: { label: "Contact", hint: "Alien world · mothership" },
    warp: { label: "Warp", hint: "Glyph starfield" },
    coral: { label: "Coral", hint: "Reaction–diffusion" },
    aurora: { label: "Aurora", hint: "Light curtains · multicolor" },
    mandala: { label: "Mandala", hint: "Glyph rosette · multicolor" },
    spiral: { label: "Spiral", hint: "Cyclic automaton · multicolor" },
    riso: { label: "Riso", hint: "Halftone inks · multicolor" },
    alpine: { label: "Alpine Dawn", hint: "Snow peaks · dawn lake" },
    fjord: { label: "Aurora Fjord", hint: "Aurora · cabin · fjord" },
    reef: { label: "Deep Reef", hint: "Light shafts · kelp · fish" },
    desert: { label: "Desert Night", hint: "Milky way · meteors" },
    earthrise: { label: "Earthrise", hint: "Earth over the moon" },
    kyoto: { label: "Kyoto Dusk", hint: "Pagoda · cherry · lantern" },
    marine: { label: "Marine Drive", hint: "Bay lamps at night" },
    forest: { label: "Misty Forest", hint: "Pine ridges · fog · beams" },
    coast: { label: "Night Coast", hint: "Lighthouse · moonlit sea" },
    sunset: { label: "Ocean Sunset", hint: "Headland · lit cloud · sea" },
    storm: { label: "Storm Plains", hint: "Thunderhead · wheat field" },
    taj: { label: "Taj Dawn", hint: "Taj mahal · mirror pool" },
    varanasi: { label: "Varanasi Ghats", hint: "Ganga aarti · diyas" },
  };
  // Saved choices from retired backdrops map to their nearest replacement
  // instead of silently switching the wallpaper off.
  const LEGACY_BACKDROPS = {
    hush: "glyphs", tide: "ripple", veil: "glyphs", ember: "life",
    phosphor: "rain", dither: "riso", scope: "ripple",
    topo: "ripple", loom: "mandala", wind: "aurora", maze: "garden", ants: "spiral",
    pulsar: "signal", ink: "aurora", fern: "meadow", sumi: "koi",
  };
  // Reading fonts. Bricolage Grotesque and Red Hat Mono ship with every page
  // (base.html); the rest load from Google Fonts only when chosen, and the
  // picker loads a tiny glyph subset of each so its cards render in their face.
  const SANS = '"Segoe UI", system-ui, sans-serif';
  const SERIF = 'Georgia, "Times New Roman", serif';
  const MONO = 'ui-monospace, "Cascadia Mono", Menlo, Consolas, monospace';
  const W3 = "wght@400;500;700";
  const FONTS = {
    bricolage: { label: "Bricolage Grotesque", kind: "sans", spec: "", bundled: true },
    geist: { label: "Geist", kind: "sans", spec: W3 },
    instrument: { label: "Instrument Sans", kind: "sans", spec: W3 },
    figtree: { label: "Figtree", kind: "sans", spec: W3 },
    schibsted: { label: "Schibsted Grotesk", kind: "sans", spec: W3 },
    spacegrotesk: { label: "Space Grotesk", kind: "sans", spec: W3 },
    hanken: { label: "Hanken Grotesk", kind: "sans", spec: W3 },
    onest: { label: "Onest", kind: "sans", spec: W3 },
    manrope: { label: "Manrope", kind: "sans", spec: W3 },
    atkinson: { label: "Atkinson Hyperlegible Next", kind: "sans", spec: W3 },
    publicsans: { label: "Public Sans", kind: "sans", spec: W3 },
    fraunces: { label: "Fraunces", kind: "serif", spec: W3 },
    newsreader: { label: "Newsreader", kind: "serif", spec: W3 },
    sourceserif: { label: "Source Serif 4", kind: "serif", spec: W3 },
    literata: { label: "Literata", kind: "serif", spec: W3 },
    garamond: { label: "EB Garamond", kind: "serif", spec: W3 },
    plexserif: { label: "IBM Plex Serif", kind: "serif", spec: W3 },
    redhat: { label: "Red Hat Mono", kind: "mono", spec: "", bundled: true },
    jetbrains: { label: "JetBrains Mono", kind: "mono", spec: W3 },
    geistmono: { label: "Geist Mono", kind: "mono", spec: W3 },
    plexmono: { label: "IBM Plex Mono", kind: "mono", spec: W3 },
    firacode: { label: "Fira Code", kind: "mono", spec: W3 },
    martian: { label: "Martian Mono", kind: "mono", spec: W3 },
    victor: { label: "Victor Mono", kind: "mono", spec: W3 },
    sourcecode: { label: "Source Code Pro", kind: "mono", spec: W3 },
    azeret: { label: "Azeret Mono", kind: "mono", spec: W3 },
    splinemono: { label: "Spline Sans Mono", kind: "mono", spec: W3 },
    spacemono: { label: "Space Mono", kind: "mono", spec: "wght@400;700" },
    dmmono: { label: "DM Mono", kind: "mono", spec: "wght@400;500" },
    sometype: { label: "Sometype Mono", kind: "mono", spec: W3 },
  };
  for (const font of Object.values(FONTS)) {
    font.stack = `"${font.label}", ${font.kind === "mono" ? MONO : font.kind === "serif" ? SERIF : SANS}`;
  }
  // Retired picks map to the closest face in the new catalog.
  const LEGACY_FONTS = { system: "geist", serif: "newsreader", sysmono: "jetbrains" };
  const FONT_KINDS = { sans: "Sans", serif: "Serif", mono: "Mono" };
  const FONT_SUBSET = "AaBbCcDdEeFfGgHhIiJjKkLlMmNnOoPpQqRrSsTtUuVvWwXxYyZz0123456789 .,:;'\"!?-()[]{}<>/=+*&#@";
  const fontLinks = new Set();
  function addStylesheet(key, href) {
    if (fontLinks.has(key) || !document.head) return;
    fontLinks.add(key);
    const link = document.createElement("link");
    link.rel = "stylesheet";
    link.href = href;
    document.head.append(link);
  }
  function familyParam(font) {
    return "family=" + encodeURIComponent(font.label).replace(/%20/g, "+") + (font.spec ? ":" + font.spec : "");
  }
  function ensureFont(id) {
    const font = FONTS[id];
    if (!font || font.bundled) return;
    addStylesheet("font:" + id, `https://fonts.googleapis.com/css2?${familyParam(font)}&display=swap`);
  }
  function ensurePreviewFonts() {
    const families = Object.values(FONTS).filter((font) => !font.bundled)
      .map((font) => "family=" + encodeURIComponent(font.label).replace(/%20/g, "+")).join("&");
    addStylesheet("font:preview", `https://fonts.googleapis.com/css2?${families}&text=${encodeURIComponent(FONT_SUBSET)}&display=swap`);
  }
  const DEFAULT_FONT = "redhat";
  const THEME_VARS = {
    ink: "--ink",
    panel: "--panel",
    raise: "--raise",
    mist: "--mist",
    mute: "--mute",
    line: "--line",
    brass: "--brass",
    steel: "--steel",
    reason: "--reason",
    fault: "--fault",
    ok: "--ok",
    onBrass: "--on-brass",
    code: "--chat-code-bg",
  };
  const STORE_FONTS = "fuse.chat.fonts.v1";
  const STORE_SIZE = "fuse.chat.textsize.v1";
  const STORE_THEME = "fuse.chat.theme.v1";
  const STORE_COMPOSER = "fuse.chat.composer.v1";
  const COMPOSER_DEFAULTS = { spell: true, palettes: true, history: true, dial: true };
  const STORE_CUSTOM = "fuse.chat.custom.v1";
  const STORE_BACKDROP = "fuse.chat.backdrop.v1";
  const STORE_STRENGTH = "fuse.chat.backdrop.strength.v1";
  const SIZE_MIN = 30;
  const SIZE_MAX = 130;
  const SIZE_DEFAULT = 100;
  const STRENGTH_MIN = 12;
  const STRENGTH_MAX = 55;
  const STRENGTH_DEFAULT = 28;
  const html = document.documentElement;
  let theme = "fuse";
  let textScale = SIZE_DEFAULT;
  let selectedFont = "";
  let custom = { ink: "#121212", brass: "#c5df88" };
  let backdrop = "none";
  let strength = STRENGTH_DEFAULT;
  let fontFilter = "all";
  let fontHover = "";

  function clampHex(value, fallback) {
    const raw = String(value || "").trim();
    if (/^#[0-9a-fA-F]{6}$/.test(raw)) return raw.toLowerCase();
    if (/^#[0-9a-fA-F]{3}$/.test(raw)) {
      const h = raw.slice(1);
      return `#${h[0]}${h[0]}${h[1]}${h[1]}${h[2]}${h[2]}`.toLowerCase();
    }
    return fallback;
  }
  function hexToRgb(hex) {
    return {
      r: parseInt(hex.slice(1, 3), 16),
      g: parseInt(hex.slice(3, 5), 16),
      b: parseInt(hex.slice(5, 7), 16),
    };
  }
  function rgbToHex(r, g, b) {
    const n = (v) => Math.max(0, Math.min(255, Math.round(v))).toString(16).padStart(2, "0");
    return `#${n(r)}${n(g)}${n(b)}`;
  }
  function mix(a, b, t) {
    const A = hexToRgb(a);
    const B = hexToRgb(b);
    return rgbToHex(A.r + (B.r - A.r) * t, A.g + (B.g - A.g) * t, A.b + (B.b - A.b) * t);
  }
  function luma(hex) {
    const { r, g, b } = hexToRgb(hex);
    return (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255;
  }
  function buildCustom(ink, brass) {
    const dark = luma(ink) < 0.48;
    const mist = dark ? mix(ink, "#f4f1ea", 0.92) : mix(ink, "#161410", 0.84);
    const mute = dark ? mix(mist, ink, 0.45) : mix(mist, ink, 0.38);
    const panel = dark ? mix(ink, mist, 0.07) : mix(ink, "#1c1b18", 0.05);
    const raise = dark ? mix(ink, mist, 0.13) : mix(ink, "#1c1b18", 0.1);
    const line = dark ? mix(ink, mist, 0.16) : mix(ink, "#1c1b18", 0.14);
    const steel = mix(brass, dark ? "#7ec8e8" : "#2f6a4a", 0.38);
    const reason = mix(mist, steel, 0.28);
    const fault = dark ? "#f29d8e" : "#c24b40";
    const ok = mix(brass, dark ? "#8fcdb0" : "#2f7a48", 0.4);
    return {
      label: "Custom",
      scheme: dark ? "dark" : "light",
      ink,
      panel,
      raise,
      mist,
      mute,
      line,
      brass,
      steel,
      reason,
      fault,
      ok,
      onBrass: luma(brass) < 0.55 ? "#ffffff" : "#121212",
      code: dark ? mix(ink, "#000000", 0.35) : mix(ink, "#ffffff", 0.18),
    };
  }

  try {
    const saved = localStorage.getItem(STORE_THEME);
    if (saved === "custom" || Object.prototype.hasOwnProperty.call(themes, saved)) theme = saved;
    const storedSize = Number(localStorage.getItem(STORE_SIZE));
    if (Number.isFinite(storedSize) && storedSize >= SIZE_MIN && storedSize <= SIZE_MAX) textScale = Math.round(storedSize / 5) * 5;
    const storedFonts = JSON.parse(localStorage.getItem(STORE_FONTS) || "{}");
    if (storedFonts && typeof storedFonts === "object") {
      const id = LEGACY_FONTS[storedFonts.console] || storedFonts.console;
      if (FONTS[id]) selectedFont = id;
    }
    const storedCustom = JSON.parse(localStorage.getItem(STORE_CUSTOM) || "null");
    if (storedCustom && typeof storedCustom === "object") {
      custom = {
        ink: clampHex(storedCustom.ink, custom.ink),
        brass: clampHex(storedCustom.brass, custom.brass),
      };
    }
    let storedBackdrop = localStorage.getItem(STORE_BACKDROP);
    if (storedBackdrop && LEGACY_BACKDROPS[storedBackdrop]) {
      storedBackdrop = LEGACY_BACKDROPS[storedBackdrop];
      localStorage.setItem(STORE_BACKDROP, storedBackdrop);
    }
    if (storedBackdrop && Object.prototype.hasOwnProperty.call(BACKDROPS, storedBackdrop)) backdrop = storedBackdrop;
    const storedStrength = Number(localStorage.getItem(STORE_STRENGTH));
    if (Number.isFinite(storedStrength) && storedStrength >= STRENGTH_MIN && storedStrength <= STRENGTH_MAX) {
      strength = Math.round(storedStrength);
    }
  } catch (_) {}

  function fontFor() {
    return selectedFont || DEFAULT_FONT;
  }

  function specFor(id) {
    if (id === "custom") return buildCustom(custom.ink, custom.brass);
    return themes[id] || themes.fuse;
  }

  function clearThemeVars() {
    for (const prop of Object.values(THEME_VARS)) html.style.removeProperty(prop);
  }

  const SCENE_MARKUP = '<span data-chat-scene-wash></span><canvas data-chat-scene-fx hidden></canvas><span data-chat-scene-veil></span>';
  // Drawing lives in chat-backdrop.js (loaded after this file); until it
  // arrives, the scene simply shows its CSS layers.
  function syncFx(scene) {
    if (window.FuseBackdrop) window.FuseBackdrop.sync(scene, scene && !scene.hidden ? backdrop : "none");
  }

  function ensureScene() {
    if (!document.body) return null;
    let el = document.querySelector("[data-chat-scene]");
    if (!el) {
      el = document.createElement("div");
      el.className = "chat-scene";
      el.dataset.chatScene = "";
      el.setAttribute("aria-hidden", "true");
      el.innerHTML = SCENE_MARKUP;
      const stage = document.querySelector("body.is-chat .stage, body.is-settings .stage");
      if (stage) document.body.insertBefore(el, stage);
      else document.body.prepend(el);
    } else if (!el.querySelector("canvas[data-chat-scene-fx]") || !el.querySelector("[data-chat-scene-veil]")) {
      el.innerHTML = SCENE_MARKUP;
    }
    return el;
  }

  function apply() {
    const spec = specFor(theme);
    html.dataset.chatTheme = theme;
    html.dataset.chatConsole = "true";
    html.dataset.chatBackdrop = backdrop;
    html.dataset.chatView = "console";
    ensureFont(fontFor());
    html.style.setProperty("--chat-text-scale", String(textScale / 100));
    // The reading font is the site font: it drives the --sans and --mono
    // tokens every rule reads, not just the transcript. Code keeps a fixed
    // pitch (--code): the chosen face when it is a mono, Red Hat Mono otherwise.
    const fontId = fontFor();
    const font = FONTS[fontId];
    html.dataset.chatFont = fontId;
    html.style.setProperty("--chat-view-font", font.stack);
    html.style.setProperty("--sans", font.stack);
    html.style.setProperty("--mono", font.stack);
    html.style.setProperty("--code", font.kind === "mono" ? font.stack : FONTS.redhat.stack);
    html.style.setProperty("--chat-backdrop-strength", String(strength / 100));
    if (theme === "custom") {
      for (const [key, prop] of Object.entries(THEME_VARS)) html.style.setProperty(prop, spec[key]);
    } else {
      clearThemeVars();
    }
    html.style.colorScheme = spec.scheme;
    html.dataset.chatScheme = spec.scheme;
    document.querySelector('meta[name="color-scheme"]')?.setAttribute("content", spec.scheme);
    document.querySelector('meta[name="theme-color"]')?.setAttribute("content", spec.ink);
    const scene = ensureScene();
    if (scene) {
      scene.hidden = backdrop === "none";
      scene.dataset.kind = backdrop;
      syncFx(scene);
    }
    if (typeof CustomEvent === "function" && document.dispatchEvent) {
      try { document.dispatchEvent(new CustomEvent("fuse-composer-metrics")); } catch (_) {}
    }
  }
  apply();

  function getComposerPrefs() {
    try {
      const raw = JSON.parse(localStorage.getItem(STORE_COMPOSER) || "null");
      if (!raw || typeof raw !== "object") return { ...COMPOSER_DEFAULTS };
      return {
        spell: raw.spell !== false,
        palettes: raw.palettes !== false,
        history: raw.history !== false,
        dial: raw.dial !== false,
      };
    } catch (_) {
      return { ...COMPOSER_DEFAULTS };
    }
  }

  function setComposerPrefs(partial) {
    const next = { ...getComposerPrefs(), ...(partial || {}) };
    const value = {
      spell: next.spell !== false,
      palettes: next.palettes !== false,
      history: next.history !== false,
      dial: next.dial !== false,
    };
    try { localStorage.setItem(STORE_COMPOSER, JSON.stringify(value)); } catch (_) {}
    document.querySelectorAll("[data-composer-pref]").forEach((el) => {
      const key = el.getAttribute("data-composer-pref");
      if (key && Object.prototype.hasOwnProperty.call(value, key)) el.checked = value[key];
    });
    if (typeof CustomEvent === "function" && document.dispatchEvent) {
      try { document.dispatchEvent(new CustomEvent("fuse-composer-pref", { detail: value })); } catch (_) {}
    }
    return value;
  }

  function persistFonts() {
    try { localStorage.setItem(STORE_FONTS, JSON.stringify({ console: selectedFont })); } catch (_) {}
  }
  function persistTheme() {
    try { localStorage.setItem(STORE_THEME, theme); } catch (_) {}
  }
  function persistCustom() {
    try { localStorage.setItem(STORE_CUSTOM, JSON.stringify(custom)); } catch (_) {}
  }
  function persistBackdrop() {
    try {
      localStorage.setItem(STORE_BACKDROP, backdrop);
      localStorage.setItem(STORE_STRENGTH, String(strength));
    } catch (_) {}
  }

  function paintSwatches() {
    const themeHost = document.querySelector("[data-chat-theme-swatches]");
    if (themeHost && !themeHost.childElementCount) {
      const entries = [...Object.entries(themes), ["custom", { label: "Custom" }]];
      for (const [id, spec] of entries) {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "ap-theme";
        btn.dataset.chatTheme = id;
        btn.setAttribute("aria-pressed", "false");
        // A tiny window drawn in the theme's own colors: bar, prompt, text.
        btn.innerHTML = '<span class="ap-theme-art" aria-hidden="true"><b></b><i></i><i></i><em></em></span>' +
          `<span class="ap-theme-name">${spec.label}</span>`;
        themeHost.append(btn);
      }
    }
    const backHost = document.querySelector("[data-chat-backdrop-swatches]");
    if (backHost && !backHost.childElementCount) {
      for (const [id, spec] of Object.entries(BACKDROPS)) {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "ap-backdrop";
        btn.dataset.chatBackdrop = id;
        btn.setAttribute("aria-pressed", "false");
        btn.innerHTML = `<i class="chat-backdrop-preview" data-kind="${id}" aria-hidden="true"></i><span>${spec.label}</span><small>${spec.hint}</small>`;
        backHost.append(btn);
      }
      window.FuseBackdrop?.previews(backHost);
    }
  }

  function paintFonts() {
    const grid = document.querySelector("[data-chat-font-grid]");
    if (!grid) return;
    if (!grid.childElementCount) {
      const card = (id, name, kind, stack) =>
        `<button type="button" class="ap-font" role="radio" aria-checked="false" data-font-id="${id}" data-font-kind="${kind}" style="--ap-face: ${stack.replace(/"/g, "&quot;")}">` +
        `<span class="ap-font-glyph" aria-hidden="true">Ag</span><span class="ap-font-name">${name}</span><small>${FONT_KINDS[kind] || kind}</small></button>`;
      grid.innerHTML = card("", "Default", "default", "inherit") +
        Object.entries(FONTS).map(([id, font]) => card(id, font.label, font.kind, font.stack)).join("");
    }
    const fallback = FONTS[DEFAULT_FONT];
    const selected = selectedFont || "";
    grid.querySelectorAll(".ap-font").forEach((btn) => {
      const id = btn.dataset.fontId;
      if (!id) {
        btn.style.setProperty("--ap-face", fallback.stack);
        btn.querySelector("small").textContent = fallback.label;
      }
      const on = id === selected;
      btn.setAttribute("aria-checked", String(on));
      btn.tabIndex = on ? 0 : -1;
      btn.hidden = fontFilter !== "all" && id !== "" && btn.dataset.fontKind !== fontFilter;
    });
    document.querySelectorAll("[data-chat-font-filter]").forEach((btn) => {
      btn.setAttribute("aria-pressed", String(btn.dataset.chatFontFilter === fontFilter));
    });
    const sample = document.querySelector("[data-chat-font-sample]");
    if (sample) {
      const shown = fontHover !== "" && fontHover != null ? fontHover : fontFor();
      const font = FONTS[shown] || fallback;
      sample.style.fontFamily = font.stack;
      sample.dataset.fontLabel = font.label;
    }
  }

  function syncSwatches() {
    document.querySelectorAll("[data-chat-theme-swatches] [data-chat-theme]").forEach((btn) => {
      const id = btn.dataset.chatTheme;
      const spec = specFor(id);
      for (const key of ["ink", "panel", "raise", "mist", "mute", "line", "brass"]) {
        btn.style.setProperty(`--t-${key}`, spec[key]);
      }
      btn.setAttribute("aria-pressed", String(id === theme));
      btn.classList.toggle("is-on", id === theme);
    });
    document.querySelectorAll("[data-chat-backdrop-swatches] [data-chat-backdrop]").forEach((btn) => {
      const id = btn.dataset.chatBackdrop;
      btn.setAttribute("aria-pressed", String(id === backdrop));
      btn.classList.toggle("is-on", id === backdrop);
    });
    const customBox = document.querySelector("[data-chat-custom]");
    if (customBox) customBox.hidden = theme !== "custom";
    const ink = document.querySelector("[data-chat-custom-ink]");
    const brass = document.querySelector("[data-chat-custom-brass]");
    if (ink) ink.value = custom.ink;
    if (brass) brass.value = custom.brass;
    const strengthRange = document.querySelector("[data-chat-backdrop-strength]");
    const strengthOut = document.querySelector("[data-chat-backdrop-strength-value]");
    const strengthWrap = document.querySelector("[data-chat-backdrop-strength-wrap]");
    if (strengthRange) strengthRange.value = String(strength);
    if (strengthOut) strengthOut.textContent = `${strength}%`;
    if (strengthWrap) strengthWrap.hidden = backdrop === "none";
  }

  function syncUi() {
    paintSwatches();
    const range = document.querySelector("[data-chat-size]");
    if (range) range.value = String(textScale);
    const out = document.querySelector("[data-chat-size-value]");
    if (out) out.textContent = `${textScale}%`;
    paintFonts();
    const prefs = getComposerPrefs();
    document.querySelectorAll("[data-composer-pref]").forEach((el) => {
      const key = el.getAttribute("data-composer-pref");
      if (key && Object.prototype.hasOwnProperty.call(prefs, key)) el.checked = prefs[key];
    });
    syncSwatches();
  }

  function setTheme(next) {
    theme = next === "custom" || Object.prototype.hasOwnProperty.call(themes, next) ? next : "fuse";
    persistTheme();
    apply();
    syncSwatches();
  }

  function setCustom(partial) {
    custom = {
      ink: clampHex(partial.ink, custom.ink),
      brass: clampHex(partial.brass, custom.brass),
    };
    theme = "custom";
    persistCustom();
    persistTheme();
    apply();
    syncSwatches();
  }

  function setBackdrop(next) {
    backdrop = Object.prototype.hasOwnProperty.call(BACKDROPS, next) ? next : "none";
    persistBackdrop();
    apply();
    syncSwatches();
  }

  function setStrength(next) {
    const value = Math.round(Number(next));
    if (!Number.isFinite(value)) return;
    strength = Math.min(STRENGTH_MAX, Math.max(STRENGTH_MIN, value));
    persistBackdrop();
    apply();
    const range = document.querySelector("[data-chat-backdrop-strength]");
    if (range) range.value = String(strength);
    const out = document.querySelector("[data-chat-backdrop-strength-value]");
    if (out) out.textContent = `${strength}%`;
  }

  window.FuseChatAppearance = {
    syncUi,
    get size() { return textScale; },
    setSize(next) {
      const value = Math.round(Number(next));
      if (!Number.isFinite(value)) return;
      textScale = Math.min(SIZE_MAX, Math.max(SIZE_MIN, Math.round(value / 5) * 5));
      try { localStorage.setItem(STORE_SIZE, String(textScale)); } catch (_) {}
      apply();
      const range = document.querySelector("[data-chat-size]");
      if (range) range.value = String(textScale);
      const out = document.querySelector("[data-chat-size-value]");
      if (out) out.textContent = `${textScale}%`;
    },
    setFont(id) {
      selectedFont = id && FONTS[id] ? id : "";
      persistFonts();
      apply();
      syncUi();
    },
    setTheme,
    setBackdrop,
    setStrength,
    getComposerPrefs,
    setComposerPrefs,
  };

  document.addEventListener("DOMContentLoaded", () => {
    const root = document.querySelector("[data-chat]");
    const sizeRange = document.querySelector("[data-chat-size]");
    const fontGrid = document.querySelector("[data-chat-font-grid]");
    if (!root) {
      apply();
      return;
    }
    paintSwatches();
    document.querySelector("[data-chat-theme-swatches]")?.addEventListener("click", (event) => {
      const btn = event.target.closest("[data-chat-theme]");
      if (!btn) return;
      setTheme(btn.dataset.chatTheme);
    });
    document.querySelector("[data-chat-backdrop-swatches]")?.addEventListener("click", (event) => {
      const btn = event.target.closest("[data-chat-backdrop]");
      if (!btn) return;
      setBackdrop(btn.dataset.chatBackdrop);
    });
    document.querySelector("[data-chat-custom-ink]")?.addEventListener("input", (event) => {
      setCustom({ ink: event.target.value, brass: custom.brass });
    });
    document.querySelector("[data-chat-custom-brass]")?.addEventListener("input", (event) => {
      setCustom({ ink: custom.ink, brass: event.target.value });
    });
    document.querySelector("[data-chat-backdrop-strength]")?.addEventListener("input", (event) => {
      setStrength(event.target.value);
    });
    sizeRange?.addEventListener("input", () => {
      window.FuseChatAppearance.setSize(sizeRange.value);
    });
    document.querySelectorAll("[data-size-step]").forEach((btn) => {
      btn.addEventListener("click", () => {
        window.FuseChatAppearance.setSize(textScale + Number(btn.getAttribute("data-size-step")) * 5);
      });
    });
    // Picking loads the full face; hovering or focusing a card previews it
    // in the sample line without committing.
    fontGrid?.addEventListener("click", (event) => {
      const btn = event.target.closest(".ap-font");
      if (!btn) return;
      fontHover = "";
      window.FuseChatAppearance.setFont(btn.dataset.fontId || null);
    });
    const preview = (event) => {
      const btn = event.target.closest?.(".ap-font");
      const next = btn ? btn.dataset.fontId || DEFAULT_FONT : "";
      if (next === fontHover) return;
      fontHover = next;
      if (next) ensureFont(next);
      paintFonts();
    };
    fontGrid?.addEventListener("pointerover", preview);
    fontGrid?.addEventListener("focusin", preview);
    fontGrid?.addEventListener("pointerleave", () => { fontHover = ""; paintFonts(); });
    fontGrid?.addEventListener("focusout", (event) => {
      if (!fontGrid.contains(event.relatedTarget)) { fontHover = ""; paintFonts(); }
    });
    fontGrid?.addEventListener("keydown", (event) => {
      const keys = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 };
      if (!(event.key in keys)) return;
      const cards = [...fontGrid.querySelectorAll(".ap-font:not([hidden])")];
      const at = cards.indexOf(document.activeElement);
      if (at < 0) return;
      event.preventDefault();
      const next = cards[(at + keys[event.key] + cards.length) % cards.length];
      next.focus();
      next.click();
    });
    document.querySelector("[data-chat-font-filters]")?.addEventListener("click", (event) => {
      const btn = event.target.closest("[data-chat-font-filter]");
      if (!btn) return;
      fontFilter = btn.dataset.chatFontFilter;
      paintFonts();
    });
    // Card faces load only once the window is first opened.
    const menu = document.querySelector("[data-view-menu]");
    if (menu) {
      const loadOnOpen = new MutationObserver(() => {
        if (menu.hidden) return;
        ensurePreviewFonts();
        loadOnOpen.disconnect();
      });
      loadOnOpen.observe(menu, { attributes: true, attributeFilter: ["hidden"] });
    }
    document.querySelectorAll("[data-composer-pref]").forEach((el) => {
      el.addEventListener("change", () => {
        const key = el.getAttribute("data-composer-pref");
        if (!key) return;
        setComposerPrefs({ [key]: el.checked });
      });
    });
    apply();
    syncUi();
  });
})();
