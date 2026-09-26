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
    hush: { label: "Hush", hint: "Soft mesh" },
    aurora: { label: "Aurora", hint: "Color drift" },
    tide: { label: "Tide", hint: "Ocean + bubbles" },
    veil: { label: "Veil", hint: "Cool wrap" },
    ember: { label: "Glow", hint: "Warm glow" },
  };
  const FONTS = {
    bricolage: { label: "Bricolage Grotesque", stack: '"Bricolage Grotesque", "Segoe UI", sans-serif' },
    system: { label: "System UI", stack: 'system-ui, -apple-system, "Segoe UI", Roboto, sans-serif' },
    serif: { label: "Georgia (serif)", stack: 'Georgia, "Times New Roman", serif' },
    redhat: { label: "Red Hat Mono", stack: '"Red Hat Mono", ui-monospace, "Cascadia Mono", Menlo, monospace' },
    sysmono: { label: "System Mono", stack: 'ui-monospace, "SF Mono", "Cascadia Mono", Menlo, Consolas, monospace' },
  };
  const VIEW_DEFAULT_FONT = {
    cli: "redhat",
    compact: "bricolage",
    console: "redhat",
    prism: "bricolage",
  };
  const VIEWS = Object.keys(VIEW_DEFAULT_FONT);
  const VIEW_LABELS = {
    cli: "Terminal",
    compact: "Compact",
    console: "Console",
    prism: "Prism",
  };
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
  const COMPOSER_DEFAULTS = { spell: true, palettes: true, history: true };
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
  let terminal = true;
  let consoleView = false;
  let textScale = SIZE_DEFAULT;
  let fontMap = {};
  let currentView = "cli";
  let custom = { ink: "#121212", brass: "#c5df88" };
  let backdrop = "none";
  let strength = STRENGTH_DEFAULT;

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
    const storedView = localStorage.getItem("fuse.chat.view.v2");
    if (VIEWS.includes(storedView)) currentView = storedView;
    terminal = currentView === "cli";
    consoleView = currentView === "console";
    const storedSize = Number(localStorage.getItem(STORE_SIZE));
    if (Number.isFinite(storedSize) && storedSize >= SIZE_MIN && storedSize <= SIZE_MAX) textScale = Math.round(storedSize / 5) * 5;
    const storedFonts = JSON.parse(localStorage.getItem(STORE_FONTS) || "{}");
    if (storedFonts && typeof storedFonts === "object") {
      for (const [view, id] of Object.entries(storedFonts)) {
        if (VIEWS.includes(view) && FONTS[id]) fontMap[view] = id;
      }
    }
    const storedCustom = JSON.parse(localStorage.getItem(STORE_CUSTOM) || "null");
    if (storedCustom && typeof storedCustom === "object") {
      custom = {
        ink: clampHex(storedCustom.ink, custom.ink),
        brass: clampHex(storedCustom.brass, custom.brass),
      };
    }
    const storedBackdrop = localStorage.getItem(STORE_BACKDROP);
    if (storedBackdrop && Object.prototype.hasOwnProperty.call(BACKDROPS, storedBackdrop)) backdrop = storedBackdrop;
    const storedStrength = Number(localStorage.getItem(STORE_STRENGTH));
    if (Number.isFinite(storedStrength) && storedStrength >= STRENGTH_MIN && storedStrength <= STRENGTH_MAX) {
      strength = Math.round(storedStrength);
    }
  } catch (_) {}

  function fontFor(view) {
    if (!VIEWS.includes(view)) view = "cli";
    return FONTS[fontMap[view]] ? fontMap[view] : VIEW_DEFAULT_FONT[view] || "redhat";
  }

  function specFor(id) {
    if (id === "custom") return buildCustom(custom.ink, custom.brass);
    return themes[id] || themes.fuse;
  }

  function clearThemeVars() {
    for (const prop of Object.values(THEME_VARS)) html.style.removeProperty(prop);
  }

  const SCENE_MARKUP = '<span data-chat-scene-wash></span><canvas data-chat-scene-fx hidden></canvas><span data-chat-scene-veil></span>';
  const fx = { canvas: null, ctx: null, bubbles: [], raf: 0, last: 0, w: 0, h: 0, listening: false };

  function cssVarHex(prop, fallback) {
    if (typeof getComputedStyle !== "function") return fallback;
    const raw = getComputedStyle(html).getPropertyValue(prop).trim();
    if (/^#/.test(raw)) return clampHex(raw, fallback);
    const m = raw.match(/rgba?\(\s*([\d.]+)\s*[,\s]\s*([\d.]+)\s*[,\s]\s*([\d.]+)/);
    if (m) return rgbToHex(Number(m[1]), Number(m[2]), Number(m[3]));
    return fallback;
  }

  function seedBubbles(w, h) {
    const count = Math.max(18, Math.min(36, Math.round(20 + Math.min(w, h) / 48)));
    const list = [];
    for (let i = 0; i < count; i += 1) {
      list.push({
        x: Math.random() * w,
        y: Math.random() * h,
        r: 2.4 + Math.random() * 8.5,
        speed: 14 + Math.random() * 32,
        wobble: 6 + Math.random() * 16,
        phase: Math.random() * Math.PI * 2,
        tint: Math.random(),
        alpha: 0.16 + Math.random() * 0.32,
      });
    }
    return list;
  }

  function sizeFxCanvas() {
    if (!fx.canvas || !fx.ctx) return;
    const dpr = Math.min((typeof window !== "undefined" && window.devicePixelRatio) || 1, 1.5);
    const w = fx.canvas.clientWidth || (typeof window !== "undefined" ? window.innerWidth : 0);
    const h = fx.canvas.clientHeight || (typeof window !== "undefined" ? window.innerHeight : 0);
    if (w < 2 || h < 2) return;
    const bw = Math.max(1, Math.round(w * dpr));
    const bh = Math.max(1, Math.round(h * dpr));
    if (fx.canvas.width !== bw || fx.canvas.height !== bh) {
      fx.canvas.width = bw;
      fx.canvas.height = bh;
      fx.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      fx.w = w;
      fx.h = h;
      if (!fx.bubbles.length) fx.bubbles = seedBubbles(w, h);
    }
  }

  function paintBubbles(dt) {
    const ctx = fx.ctx;
    if (!ctx) return;
    const w = fx.w;
    const h = fx.h;
    if (w < 2 || h < 2) return;
    const steel = hexToRgb(cssVarHex("--steel", "#7eb8d4"));
    const mist = hexToRgb(cssVarHex("--mist", "#e8eef6"));
    const brass = hexToRgb(cssVarHex("--brass", "#6ad4c8"));
    ctx.clearRect(0, 0, w, h);
    for (const b of fx.bubbles) {
      b.phase += dt * 0.6;
      b.y -= b.speed * dt;
      const x = b.x + Math.sin(b.phase) * b.wobble;
      if (b.y < -b.r * 3) {
        b.y = h + b.r * 2;
        b.x = Math.random() * w;
      }
      const src = b.tint > 0.72 ? brass : b.tint > 0.38 ? steel : mist;
      const g = ctx.createRadialGradient(x - b.r * 0.28, b.y - b.r * 0.32, b.r * 0.12, x, b.y, b.r);
      g.addColorStop(0, `rgba(255,255,255,${Math.min(0.55, b.alpha + 0.18)})`);
      g.addColorStop(0.35, `rgba(${src.r},${src.g},${src.b},${b.alpha})`);
      g.addColorStop(1, `rgba(${src.r},${src.g},${src.b},0)`);
      ctx.beginPath();
      ctx.fillStyle = g;
      ctx.arc(x, b.y, b.r, 0, Math.PI * 2);
      ctx.fill();
      ctx.beginPath();
      ctx.strokeStyle = `rgba(255,255,255,${b.alpha * 0.35})`;
      ctx.lineWidth = 0.7;
      ctx.arc(x, b.y, b.r * 0.92, 0, Math.PI * 2);
      ctx.stroke();
    }
  }

  function stopFx() {
    if (fx.raf && typeof cancelAnimationFrame === "function") cancelAnimationFrame(fx.raf);
    fx.raf = 0;
    fx.last = 0;
    if (fx.canvas) fx.canvas.hidden = true;
  }

  function fxFrame(now) {
    if (!fx.canvas || fx.canvas.hidden) return;
    const t = now * 0.001;
    const dt = fx.last ? Math.min(0.05, t - fx.last) : 0.016;
    fx.last = t;
    sizeFxCanvas();
    paintBubbles(dt);
    fx.raf = requestAnimationFrame(fxFrame);
  }

  function startFx(scene) {
    const canvas = scene.querySelector("canvas[data-chat-scene-fx]");
    if (!canvas || typeof canvas.getContext !== "function") return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    fx.canvas = canvas;
    fx.ctx = ctx;
    canvas.hidden = false;
    void canvas.offsetWidth;
    sizeFxCanvas();
    paintBubbles(0);
    const reduce = typeof matchMedia === "function" && matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (reduce || (typeof document !== "undefined" && document.hidden)) return;
    if (!fx.raf) fx.raf = requestAnimationFrame(fxFrame);
  }

  function onFxVisibility() {
    if (document.hidden) {
      if (fx.raf && typeof cancelAnimationFrame === "function") cancelAnimationFrame(fx.raf);
      fx.raf = 0;
      fx.last = 0;
      return;
    }
    if (fx.canvas && !fx.canvas.hidden && backdrop === "tide") startFx(fx.canvas.parentElement);
  }

  function onFxResize() {
    if (fx.canvas && !fx.canvas.hidden) sizeFxCanvas();
  }

  function bindFxListeners() {
    if (fx.listening || typeof document === "undefined") return;
    document.addEventListener("visibilitychange", onFxVisibility);
    if (typeof window !== "undefined") window.addEventListener("resize", onFxResize);
    fx.listening = true;
  }

  function syncFx(scene) {
    if (!scene || scene.hidden || backdrop !== "tide") {
      stopFx();
      return;
    }
    bindFxListeners();
    startFx(scene);
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
    html.dataset.chatTerminal = String(terminal);
    if (consoleView) html.dataset.chatConsole = "true";
    else html.removeAttribute("data-chat-console");
    html.dataset.chatBackdrop = backdrop;
    html.style.setProperty("--chat-text-scale", String(textScale / 100));
    html.style.setProperty("--chat-view-font", FONTS[fontFor(currentView)].stack);
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
    try { localStorage.setItem(STORE_FONTS, JSON.stringify(fontMap)); } catch (_) {}
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

  function viewLabel(view) {
    const fromMenu = document.querySelector(`[data-view-menu] [data-view="${view}"] span`)?.textContent;
    return fromMenu || VIEW_LABELS[view] || "View";
  }

  function paintSwatches() {
    const themeHost = document.querySelector("[data-chat-theme-swatches]");
    if (themeHost && !themeHost.childElementCount) {
      const entries = [...Object.entries(themes), ["custom", { label: "Custom", ink: custom.ink, brass: custom.brass }]];
      for (const [id, spec] of entries) {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "chat-theme-swatch";
        btn.dataset.chatTheme = id;
        btn.setAttribute("aria-pressed", "false");
        btn.innerHTML = `<i aria-hidden="true"></i><span>${spec.label}</span>`;
        themeHost.append(btn);
      }
    }
    const backHost = document.querySelector("[data-chat-backdrop-swatches]");
    if (backHost && !backHost.childElementCount) {
      for (const [id, spec] of Object.entries(BACKDROPS)) {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "chat-backdrop-swatch";
        btn.dataset.chatBackdrop = id;
        btn.setAttribute("aria-pressed", "false");
        btn.innerHTML = `<i class="chat-backdrop-preview" data-kind="${id}" aria-hidden="true"></i><span>${spec.label}</span><small>${spec.hint}</small>`;
        backHost.append(btn);
      }
    }
  }

  function syncSwatches() {
    document.querySelectorAll("[data-chat-theme-swatches] [data-chat-theme]").forEach((btn) => {
      const id = btn.dataset.chatTheme;
      const spec = specFor(id);
      btn.style.setProperty("--swatch-ink", spec.ink);
      btn.style.setProperty("--swatch-brass", spec.brass);
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
    const select = document.querySelector("[data-chat-font-select]");
    if (select) {
      if (!select.options.length) {
        select.append(new Option("View default", ""));
        for (const [id, font] of Object.entries(FONTS)) select.append(new Option(font.label, id));
      }
      select.options[0].textContent = `View default · ${FONTS[VIEW_DEFAULT_FONT[currentView]].label}`;
      select.value = fontMap[currentView] || "";
    }
    const label = document.querySelector("[data-chat-font-view]");
    if (label) label.textContent = viewLabel(currentView);
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
    get view() { return currentView; },
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
    setFont(view, id) {
      if (!VIEWS.includes(view)) return;
      if (id && FONTS[id]) fontMap[view] = id;
      else delete fontMap[view];
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
    const fontSelect = document.querySelector("[data-chat-font-select]");
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
    fontSelect?.addEventListener("change", () => {
      window.FuseChatAppearance.setFont(currentView, fontSelect.value || null);
    });
    document.querySelectorAll("[data-composer-pref]").forEach((el) => {
      el.addEventListener("change", () => {
        const key = el.getAttribute("data-composer-pref");
        if (!key) return;
        setComposerPrefs({ [key]: el.checked });
      });
    });
    function sync() {
      terminal = root.dataset.view === "cli";
      consoleView = root.dataset.view === "console";
      if (VIEWS.includes(root.dataset.view) && root.dataset.view !== currentView) {
        currentView = root.dataset.view;
      }
      document.querySelectorAll("[data-view-menu] [data-view]").forEach((button) => {
        button.setAttribute("aria-pressed", String(button.dataset.view === root.dataset.view));
      });
      apply();
      syncUi();
    }
    new MutationObserver(sync).observe(root, { attributes: true, attributeFilter: ["data-view"] });
    sync();
  });
})();
