/* Animated chat backdrops. chat-appearance.js owns the choice and strength;
   this file only draws. Every scene reads its palette from the active theme
   (the garden family keeps natural plant colours, pitched to it), renders on
   its own frame budget, pauses while the tab is hidden, and holds a
   single still frame under prefers-reduced-motion. Scenes run on their own and
   never follow the pointer; the picker cards render a real frame of each
   scene, animating while hovered. */
(() => {
  const html = document.documentElement;
  const reduce = typeof matchMedia === "function" ? matchMedia("(prefers-reduced-motion: reduce)") : { matches: false };
  const TAU = Math.PI * 2;

  // —— Palette ——
  function rgb(prop, fallback) {
    const raw = getComputedStyle(html).getPropertyValue(prop).trim();
    let m = raw.match(/^#([0-9a-f]{6})$/i);
    if (m) return [parseInt(m[1].slice(0, 2), 16), parseInt(m[1].slice(2, 4), 16), parseInt(m[1].slice(4, 6), 16)];
    m = raw.match(/rgba?\(\s*([\d.]+)[,\s]+([\d.]+)[,\s]+([\d.]+)/);
    if (m) return [Number(m[1]), Number(m[2]), Number(m[3])];
    return fallback;
  }
  const css = (c, a = 1) => `rgba(${c[0] | 0},${c[1] | 0},${c[2] | 0},${a})`;
  const mix = (a, b, t) => [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t];
  const clamp01 = (v) => (v < 0 ? 0 : v > 1 ? 1 : v);

  function toHsl([r, g, b]) {
    r /= 255; g /= 255; b /= 255;
    const max = Math.max(r, g, b);
    const min = Math.min(r, g, b);
    const l = (max + min) / 2;
    if (max === min) return [0, 0, l];
    const d = max - min;
    const s = l > 0.5 ? d / (2 - max - min) : d / (max + min);
    let h = max === r ? (g - b) / d + (g < b ? 6 : 0) : max === g ? (b - r) / d + 2 : (r - g) / d + 4;
    return [h * 60, s, l];
  }
  function fromHsl(h, s, l) {
    h = (((h % 360) + 360) % 360) / 360;
    const q = l < 0.5 ? l * (1 + s) : l + s - l * s;
    const p = 2 * l - q;
    const ch = (t) => {
      t = (t + 1) % 1;
      if (t < 1 / 6) return p + (q - p) * 6 * t;
      if (t < 1 / 2) return q;
      if (t < 2 / 3) return p + (q - p) * (2 / 3 - t) * 6;
      return p;
    };
    return [ch(h + 1 / 3) * 255, ch(h) * 255, ch(h - 1 / 3) * 255];
  }

  function palette() {
    const pal = {
      ink: rgb("--ink", [18, 18, 18]),
      mist: rgb("--mist", [236, 236, 236]),
      mute: rgb("--mute", [154, 154, 154]),
      brass: rgb("--brass", [197, 223, 136]),
      steel: rgb("--steel", [166, 201, 175]),
      ok: rgb("--ok", [174, 211, 151]),
      fault: rgb("--fault", [242, 157, 142]),
    };
    pal.dark = toHsl(pal.ink)[2] < 0.5;
    // A light tint of the accent: what a head, a spark or a peak burns at.
    pal.tint = mix(pal.brass, pal.mist, 0.45);
    // Neighbours of the accent on the colour wheel, at a lightness that reads
    // on this theme: lavender brings pink and periwinkle, lime brings mint and
    // amber. A grey accent borrows the secondary colour's hue.
    let [h, s, l] = toHsl(pal.brass);
    if (s < 0.1) [h, s] = toHsl(pal.steel);
    const sat = s < 0.08 ? s : Math.max(0.45, Math.min(0.85, s));
    const lit = pal.dark ? Math.max(0.6, Math.min(0.78, l)) : Math.max(0.3, Math.min(0.46, l));
    pal.spectrum = [pal.brass, fromHsl(h + 42, sat, lit), pal.tint, fromHsl(h - 40, sat, lit), pal.steel];
    return pal;
  }
  // A colour at u (0..1, wrapping) around the spectrum ring.
  function ring(pal, u) {
    const sp = pal.spectrum;
    const x = (((u % 1) + 1) % 1) * sp.length;
    const i = Math.floor(x);
    return mix(sp[i], sp[(i + 1) % sp.length], x - i);
  }
  // Five tones from quiet to hot, shared by the glyph scenes.
  function tones(pal) {
    return [
      css(pal.mute, 0.5),
      css(mix(pal.mute, pal.steel, 0.6), 0.75),
      css(pal.steel, 0.92),
      css(pal.brass, 1),
      css(pal.tint, 1),
    ];
  }

  // —— Smooth value noise (2D, tileless) ——
  const PERM = new Uint8Array(512);
  (() => {
    const p = Array.from({ length: 256 }, (_, i) => i);
    let seed = 1337;
    for (let i = 255; i > 0; i -= 1) {
      seed = (seed * 16807) % 2147483647;
      const j = seed % (i + 1);
      [p[i], p[j]] = [p[j], p[i]];
    }
    for (let i = 0; i < 512; i += 1) PERM[i] = p[i & 255];
  })();
  const fade = (t) => t * t * (3 - 2 * t);
  function noise(x, y) {
    const xi = Math.floor(x);
    const yi = Math.floor(y);
    const u = fade(x - xi);
    const v = fade(y - yi);
    const X = xi & 255;
    const Y = yi & 255;
    const a = PERM[PERM[X] + Y] / 255;
    const b = PERM[PERM[X + 1] + Y] / 255;
    const c = PERM[PERM[X] + Y + 1] / 255;
    const d = PERM[PERM[X + 1] + Y + 1] / 255;
    return a + (b - a) * u + (c - a) * v + (a - b - c + d) * u * v;
  }
  // Two layers drifting against each other read as a slowly evolving field.
  function field(x, y, t) {
    return 0.62 * noise(x + t * 0.35, y - t * 0.12) + 0.38 * noise(x * 2.1 - t * 0.22, y * 2.1 + t * 0.3);
  }
  function hash(x, y, z = 0) {
    let h = Math.imul(x | 0, 374761393) ^ Math.imul(y | 0, 668265263) ^ Math.imul(z | 0, 1274126177);
    h = Math.imul(h ^ (h >>> 13), 1103515245);
    return ((h ^ (h >>> 16)) >>> 0) / 4294967296;
  }
  const rand = (a, b) => a + Math.random() * (b - a);
  const pick = (list) => list[Math.floor(Math.random() * list.length)];

  // —— Drawing kits ——
  // Glyph atlas: every char in every tone, pre-rendered, so a frame is only
  // drawImage calls.
  function atlas(s, chars, colors, cw, ch, size) {
    const list = Array.from(chars);
    const dpr = s.dpr;
    const a = document.createElement("canvas");
    a.width = Math.ceil(list.length * cw * dpr);
    a.height = Math.ceil(colors.length * ch * dpr);
    const c = a.getContext("2d");
    c.scale(dpr, dpr);
    c.font = `${size}px "Red Hat Mono", ui-monospace, "Cascadia Mono", Menlo, monospace`;
    c.textAlign = "center";
    c.textBaseline = "middle";
    colors.forEach((color, row) => {
      c.fillStyle = color;
      list.forEach((g, i) => { if (g !== " ") c.fillText(g, i * cw + cw / 2, row * ch + ch / 2); });
    });
    return { canvas: a, cw, ch, dpr, n: list.length, index: new Map(list.map((g, i) => [g, i])) };
  }
  function stamp(ctx, A, ci, ti, x, y) {
    const d = A.dpr;
    ctx.drawImage(A.canvas, ci * A.cw * d, ti * A.ch * d, A.cw * d, A.ch * d, x, y, A.cw, A.ch);
  }

  // Cell grid: one pixel per cell in an offscreen image, blown up with hard
  // edges, then a one-pixel gutter cut between cells so it reads as a panel of
  // lamps rather than a blurry bitmap.
  function cells(s, cell) {
    s.cell = cell;
    s.cols = Math.max(1, Math.ceil(s.w / cell));
    s.rows = Math.max(1, Math.ceil(s.h / cell));
    s.off = document.createElement("canvas");
    s.off.width = s.cols;
    s.off.height = s.rows;
    s.offCtx = s.off.getContext("2d");
    s.img = s.offCtx.createImageData(s.cols, s.rows);
    s.gutter = null;
    if (cell >= 4) {
      const g = document.createElement("canvas");
      const px = Math.max(1, Math.round(cell * s.dpr));
      g.width = px;
      g.height = px;
      const gc = g.getContext("2d");
      const gap = Math.max(1, Math.round(px * 0.18));
      gc.fillStyle = "#000";
      gc.fillRect(px - gap, 0, gap, px);
      gc.fillRect(0, px - gap, px, gap);
      s.gutter = s.ctx.createPattern(g, "repeat");
      s.gutter?.setTransform?.(new DOMMatrix().scale(1 / s.dpr));
    }
  }
  function blit(s, gutter = true, alpha = 1) {
    const { ctx } = s;
    s.offCtx.putImageData(s.img, 0, 0);
    ctx.clearRect(0, 0, s.w, s.h);
    ctx.imageSmoothingEnabled = false;
    ctx.globalAlpha = alpha;
    ctx.drawImage(s.off, 0, 0, s.cols * s.cell, s.rows * s.cell);
    ctx.globalAlpha = 1;
    if (gutter && s.gutter) {
      ctx.globalCompositeOperation = "destination-out";
      ctx.fillStyle = s.gutter;
      ctx.fillRect(0, 0, s.w, s.h);
      ctx.globalCompositeOperation = "source-over";
    }
  }
  function put(px, i, c, a) {
    const o = i * 4;
    px[o] = c[0]; px[o + 1] = c[1]; px[o + 2] = c[2]; px[o + 3] = a * 255;
  }

  // —— Botanical kit ——
  // Shared by the garden family (Garden, Botanica, Nouveau, Meadow, Pond,
  // Koi). Plants keep their natural colours on every theme, pink blossom,
  // green leaf, brown bark, pitched to the theme's dark or light ground and
  // pulled a little toward its accent so they belong to it. Wood, ink and
  // highlights come from the theme itself.

  // A small seeded generator, so one planting can be laid out in full up
  // front and still differ from the next.
  function seeded(n) {
    let a = n >>> 0;
    const r = () => {
      a = (a + 0x6d2b79f5) >>> 0;
      let t = a;
      t = Math.imul(t ^ (t >>> 15), t | 1);
      t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
    r.range = (lo, hi) => lo + r() * (hi - lo);
    r.pick = (list) => list[Math.floor(r() * list.length)];
    r.sign = () => (r() < 0.5 ? -1 : 1);
    return r;
  }
  const newSeed = () => Math.floor(Math.random() * 4294967296);

  function flora(pal) {
    const d = pal.dark;
    // One hue, dark to light, at lightness steps chosen per ground.
    const ramp = (h, sat, dark, light) => (d ? dark : light).map((l) => mix(fromHsl(h, sat, l), pal.brass, 0.1));
    return {
      blossom: ramp(343, 0.72, [0.52, 0.66, 0.77, 0.88], [0.42, 0.55, 0.67, 0.79]),
      leaf: ramp(108, 0.4, [0.3, 0.42, 0.56], [0.24, 0.34, 0.46]),
      moss: ramp(84, 0.34, [0.26, 0.36, 0.48], [0.3, 0.4, 0.5]),
      bark: ramp(20, 0.2, [0.3, 0.42, 0.55], [0.2, 0.3, 0.42]).map((c) => mix(c, pal.mute, 0.25)),
      violet: ramp(266, 0.5, [0.5, 0.63, 0.77], [0.4, 0.52, 0.66]),
      poppy: ramp(6, 0.72, [0.46, 0.6, 0.72], [0.4, 0.52, 0.64]),
      gold: ramp(44, 0.8, [0.5, 0.64, 0.78], [0.38, 0.5, 0.62]),
      sky: ramp(214, 0.6, [0.5, 0.64, 0.78], [0.38, 0.5, 0.62]),
      // Daisy white: near-white on dark grounds, a pale grey on light ones
      // so it still shows.
      white: ramp(40, 0.25, [0.74, 0.86, 0.95], [0.58, 0.68, 0.78]),
      // The theme's own accent as a three-step ramp, for flowers that should
      // carry the theme.
      accent: [mix(pal.brass, pal.ink, 0.3), pal.brass, pal.tint],
    };
  }

  // One wind for the whole garden: a slow swell rolling across, with a
  // stronger gust through it every half minute or so. x is in screen widths.
  function gustiness(t) {
    return Math.pow(Math.max(0, Math.sin(t * 0.19) * Math.sin(t * 0.077 + 1.3)), 2);
  }
  function gust(x, t) {
    const swell = Math.sin(x * 5.5 - t * 1.1) * 0.3 + (noise(x * 3 - t * 0.4, t * 0.05) - 0.5) * 0.9;
    const g = gustiness(t);
    return swell * (0.7 + g * 1.6) + g * 0.8;
  }
  const sway = (x, t, amp = 1) => Math.max(-1, Math.min(1, Math.round(gust(x, t) * amp)));

  // Grow → rest → fade → plant again. The scenes that draw themselves share
  // this; it returns the scene's opacity for the frame.
  function season(s, dt, spec) {
    s.phaseT += dt;
    if (s.phase === "grow") {
      if (spec.grow(dt)) {
        s.phase = "rest";
        s.phaseT = 0;
      }
    } else if (s.phase === "rest") {
      spec.rest?.(dt);
      if (s.phaseT > spec.restFor) {
        s.phase = "fade";
        s.phaseT = 0;
      }
    } else {
      spec.rest?.(dt);
      if (s.phaseT > spec.fadeFor) {
        s.phase = "grow";
        s.phaseT = 0;
        spec.replant();
      }
    }
    return s.phase === "fade" ? 1 - clamp01(s.phaseT / spec.fadeFor) : 1;
  }

  // Leaf outlines at the origin, pointing along +x, ready to fill and stroke.
  // A lanceolate leaf:
  function leafShape(c, L, W) {
    c.moveTo(0, 0);
    c.quadraticCurveTo(L * 0.42, -W * 1.6, L, 0);
    c.quadraticCurveTo(L * 0.42, W * 1.6, 0, 0);
  }
  // An ivy leaf, three shallow lobes around a heart-shaped base:
  function ivyLeaf(c, L) {
    c.moveTo(0, 0);
    c.bezierCurveTo(-L * 0.2, -L * 0.5, L * 0.3, -L * 0.7, L * 0.5, -L * 0.34);
    c.quadraticCurveTo(L * 0.72, -L * 0.3, L, 0);
    c.quadraticCurveTo(L * 0.72, L * 0.3, L * 0.5, L * 0.34);
    c.bezierCurveTo(L * 0.3, L * 0.7, -L * 0.2, L * 0.5, 0, 0);
  }

  // Pixel art: a 2×2 ordered dither and tiny sprites drawn from string maps,
  // one character per cell, each character a palette slot.
  const BAYER = [-0.375, 0.125, 0.375, -0.125];
  const dither = (x, y) => BAYER[(x & 1) + ((y & 1) << 1)];
  function plot(s, px, x, y, c, a = 1) {
    x = Math.floor(x);
    y = Math.floor(y);
    if (x < 0 || y < 0 || x >= s.cols || y >= s.rows) return;
    put(px, y * s.cols + x, c, a);
  }
  function sprite(s, px, map, x, y, slots, flip = false) {
    const w = map[0].length;
    for (let j = 0; j < map.length; j += 1) {
      const row = map[j];
      for (let i = 0; i < w; i += 1) {
        const c = slots[row[flip ? w - 1 - i : i]];
        if (c) plot(s, px, x + i, y + j, c);
      }
    }
  }
  const SPRITES = {
    leaf: ["  cc", " bcc", "abb ", "a   "],
    leafSmall: [" c", "ab"],
    curl: [" aa", "a a", "a  ", " a "],
    bloom: [" c ", "cdc", " c "],
    tulip: ["a c", "abc", "abc", " b "],
    tulipOpen: ["a b c", "abbbc", " abc "],
    poppy: [" bc ", "abbc", " ab "],
    bell: [" a ", "abc", "a c"],
    bud: ["b", "a"],
    flyOpen: ["bb bb", "bcacb", " bab ", " b b "],
    flyShut: [" b b ", " bab ", "  a  ", "     "],
    dragonUp: ["b b  ", "aaaac", "     "],
    dragonDown: ["     ", "aaaac", "b b  "],
    lotus: ["  b  ", "a b a", "abdba", " aaa "],
    lotusBud: [" b ", "aba", " a "],
    koi: ["  abb   ", "aaabbacc", "  abb   "],
  };
  // —— Space kit ——
  // Shared by the space family (Orbit, Galaxy, Eclipse, Belt, Contact).
  // Colours are pitched to the ground like the garden's and pulled a little
  // toward the accent; stars are the theme's text colour, so they show on
  // light grounds too.
  function cosmos(pal) {
    const [hue, s0] = toHsl(pal.brass);
    const sat = Math.max(0.4, Math.min(0.75, s0));
    const tone = (h, sv, l) => mix(fromHsl(h, sv, pal.dark ? l : l * 0.78), pal.brass, 0.1);
    return { hue, sat, tone, star: mix(pal.mist, pal.tint, 0.3), grey: [0.34, 0.56, 0.78].map((l) => tone(214, 0.1, l)) };
  }
  // Stars in three depths: most faint and far, a few near and bright.
  function starfield(R, cols, rows, per = 130) {
    const n = Math.round(Math.max(28, Math.min(900, (cols * rows) / per)));
    return Array.from({ length: n }, () => {
      const z = R();
      return { x: R() * cols, y: R() * rows, z: z < 0.6 ? 0 : z < 0.9 ? 1 : 2, ph: R() * TAU, rate: R.range(0.6, 2.4), big: R() < 0.12 };
    });
  }
  // Twinkle them; drift slides each depth left at its own pace, in cells/s.
  function twinkle(s, px, list, color, time, drift = 0) {
    const { cols } = s;
    for (const st of list) {
      const x = drift ? (((st.x - time * drift * [0.2, 0.5, 1][st.z]) % cols) + cols) % cols : st.x;
      const tw = 0.3 + 0.7 * Math.pow(Math.sin(time * st.rate + st.ph) * 0.5 + 0.5, 2);
      const a = (st.z === 0 ? 0.4 : st.z === 1 ? 0.65 : 0.9) * tw;
      plot(s, px, x, st.y, color, a);
      if (st.big && tw > 0.55) for (const [dx, dy] of [[1, 0], [-1, 0], [0, 1], [0, -1]]) plot(s, px, x + dx, st.y + dy, color, a * 0.45);
    }
  }
  // Shooting stars above maxY, a few a minute.
  function meteors(s, px, dt, maxY, head, tail, rate = 0.14) {
    s.shots = s.shots || [];
    if (Math.random() < dt * rate) {
      const right = Math.random() < 0.5;
      s.shots.push({ x: rand(0.1, 0.9) * s.cols, y: rand(0, 0.3) * maxY, vx: (right ? 1 : -1) * rand(50, 90), vy: rand(14, 30), age: 0 });
    }
    s.shots = s.shots.filter((sh) => {
      sh.age += dt;
      sh.x += sh.vx * dt;
      sh.y += sh.vy * dt;
      if (sh.age > 1.2 || sh.y > maxY) return false;
      const fade = 1 - sh.age / 1.2;
      for (let k = 0; k < 14; k += 1) plot(s, px, sh.x - (sh.vx / 60) * k * 1.1, sh.y - (sh.vy / 60) * k * 1.1, k < 2 ? head : tail, fade * (1 - k / 14) * 0.95);
      return true;
    });
  }
  // A ball of radius r: shade(nx, ny, nz, x, y) gives each cell its colour
  // (or null to leave it), with the surface normal to light it by.
  function sphere(s, px, cx, cy, r, shade, alpha = 0.95) {
    const r2 = r * r;
    for (let j = -r; j <= r; j += 1) {
      const y = Math.round(cy + j);
      if (y < 0 || y >= s.rows) continue;
      for (let i = -r; i <= r; i += 1) {
        const d2 = i * i + j * j;
        if (d2 > r2) continue;
        const x = Math.round(cx + i);
        if (x < 0 || x >= s.cols) continue;
        const c = shade(i / r, j / r, Math.sqrt(1 - d2 / r2), x, y);
        if (c) put(px, y * s.cols + x, c, alpha);
      }
    }
  }
  // Light (0..1-ish) to a four-step ramp index, dithered at the steps.
  const step4 = (v, x, y) => {
    v += dither(x, y) * 0.3;
    return v < 0.15 ? 0 : v < 0.5 ? 1 : v < 0.85 ? 2 : 3;
  };

  // —— Scenes ——
  const SCENES = {
    // ASCII flow field: a glyph per cell, chosen by the field's brightness.
    // Peaks flicker through code characters.
    glyphs: {
      fps: 12,
      ramp: " .·:-=+*#%@",
      code: "0123456789abcdef<>/{}[]$&",
      setup(s) {
        s.cw = 10 * s.k;
        s.ch = 18 * s.k;
      },
      resize(s) {
        s.cols = Math.ceil(s.w / s.cw);
        s.rows = Math.ceil(s.h / s.ch);
        s.A = atlas(s, this.ramp + this.code, tones(s.pal), s.cw, s.ch, 12 * s.k);
      },
      draw(s, t) {
        const { ctx, cols, rows, cw, ch, A } = s;
        const n = this.ramp.length - 1;
        const codeAt = this.ramp.length;
        const codeN = this.code.length;
        const k = 0.075;
        ctx.clearRect(0, 0, s.w, s.h);
        for (let y = 0; y < rows; y += 1) {
          for (let x = 0; x < cols; x += 1) {
            let v = field(x * k, y * k * 1.8, t * 0.12);
            // A slow diagonal swell sweeps through the field.
            v += 0.16 * Math.sin((x * 0.045 + y * 0.09) - t * 0.35);
            v = (v - 0.38) * 1.9;
            if (v <= 0.08) continue;
            let g = Math.min(n, 1 + Math.floor(v * n));
            const tone = v < 0.3 ? 0 : v < 0.5 ? 1 : v < 0.72 ? 2 : v < 0.95 ? 3 : 4;
            if (v > 0.66 && hash(x, y, Math.floor(t * 3)) < 0.05) g = codeAt + Math.floor(hash(y, x, Math.floor(t * 7)) * codeN);
            stamp(ctx, A, g, tone, x * cw, y * ch);
          }
        }
      },
    },

    // Conway's Game of Life. Cells cool through the accent as they die, and
    // fresh seeds land when the colony stalls.
    life: {
      fps: 8,
      setup(s) {
        s.cell = Math.max(3, Math.round(8 * s.k));
        s.quiet = 0;
      },
      resize(s) {
        s.cols = Math.ceil(s.w / s.cell);
        s.rows = Math.ceil(s.h / s.cell);
        s.grid = new Uint8Array(s.cols * s.rows);
        s.next = new Uint8Array(s.cols * s.rows);
        s.heat = new Float32Array(s.cols * s.rows);
        for (let i = 0; i < s.grid.length; i += 1) s.grid[i] = Math.random() < 0.11 ? 1 : 0;
      },
      warm(s) {
        for (let i = 0; i < 30; i += 1) this.step(s);
      },
      seed(s) {
        // An R-pentomino: small, and it runs for a long time.
        const shape = [[1, 0], [2, 0], [0, 1], [1, 1], [1, 2]];
        const ox = 2 + Math.floor(Math.random() * (s.cols - 5));
        const oy = 2 + Math.floor(Math.random() * (s.rows - 5));
        for (const [dx, dy] of shape) {
          const x = (ox + dx + s.cols) % s.cols;
          const y = (oy + dy + s.rows) % s.rows;
          s.grid[y * s.cols + x] = 1;
        }
      },
      step(s) {
        const { cols, rows, grid, next } = s;
        let alive = 0;
        let changed = 0;
        for (let y = 0; y < rows; y += 1) {
          const up = ((y - 1 + rows) % rows) * cols;
          const mid = y * cols;
          const down = ((y + 1) % rows) * cols;
          for (let x = 0; x < cols; x += 1) {
            const l = (x - 1 + cols) % cols;
            const r = (x + 1) % cols;
            const n = grid[up + l] + grid[up + x] + grid[up + r] + grid[mid + l] + grid[mid + r] + grid[down + l] + grid[down + x] + grid[down + r];
            const was = grid[mid + x];
            const now = n === 3 || (was && n === 2) ? 1 : 0;
            next[mid + x] = now;
            alive += now;
            changed += now !== was ? 1 : 0;
          }
        }
        s.grid = next;
        s.next = grid;
        s.quiet = changed < cols * rows * 0.004 ? s.quiet + 1 : 0;
        if (alive < cols * rows * 0.03 || s.quiet > 12) {
          for (let i = 0; i < 4; i += 1) this.seed(s);
          s.quiet = 0;
        }
      },
      draw(s) {
        this.step(s);
        const { ctx, cols, grid, heat, cell } = s;
        const hot = s.pal.brass;
        const cool = s.pal.steel;
        const gap = cell >= 6 ? 1 : 0;
        ctx.clearRect(0, 0, s.w, s.h);
        for (let i = 0; i < grid.length; i += 1) {
          heat[i] = grid[i] ? 1 : heat[i] * 0.78;
          const h = heat[i];
          if (h < 0.04) continue;
          const x = (i % cols) * cell;
          const y = Math.floor(i / cols) * cell;
          ctx.fillStyle = css(grid[i] ? hot : cool, grid[i] ? 0.7 : h * 0.5);
          ctx.fillRect(x + gap, y + gap, cell - gap * 2, cell - gap * 2);
        }
      },
    },

    // Glyph rain: hex and half-width kana falling in columns, a hot head and
    // a cooling tail.
    rain: {
      fps: 24,
      chars: "0123456789ABCDEFｦｱｳｴｵｶｷｹｺｻｼｽｾｿﾀﾂﾃﾅﾆﾇﾈﾊﾋﾎﾏﾐﾑﾒﾓﾔﾕﾗﾘﾜ:=+*<>",
      setup(s) {
        s.cw = 13 * s.k;
        s.ch = 19 * s.k;
      },
      resize(s) {
        s.cols = Math.ceil(s.w / s.cw);
        s.rows = Math.ceil(s.h / s.ch);
        s.A = atlas(s, this.chars, tones(s.pal), s.cw, s.ch, 13 * s.k);
        s.heat = new Float32Array(s.cols * s.rows);
        s.glyph = new Uint8Array(s.cols * s.rows);
        for (let i = 0; i < s.glyph.length; i += 1) s.glyph[i] = Math.floor(Math.random() * s.A.n);
        s.drops = Array.from({ length: s.cols }, () => this.spawn(s, true));
      },
      spawn(s, anywhere) {
        return { y: anywhere ? rand(-s.rows * 1.6, s.rows) : rand(-s.rows * 1.6, -2), v: rand(5, 16), fade: rand(1.5, 3.2) };
      },
      warm(s) {
        for (let i = 0; i < 60; i += 1) this.step(s, 1 / 24);
      },
      step(s, dt) {
        const { cols, rows, heat, glyph, drops } = s;
        for (let c = 0; c < cols; c += 1) {
          const d = drops[c];
          const decay = Math.exp(-dt * d.fade);
          for (let r = 0; r < rows; r += 1) heat[r * cols + c] *= decay;
          const from = Math.floor(d.y);
          d.y += d.v * dt;
          for (let r = Math.max(0, from); r <= Math.min(rows - 1, Math.floor(d.y)); r += 1) {
            const i = r * cols + c;
            if (heat[i] < 0.98) glyph[i] = Math.floor(Math.random() * s.A.n);
            heat[i] = 1;
          }
          if (d.y > rows + 10) drops[c] = this.spawn(s, false);
        }
        // Resting characters mutate now and then.
        for (let i = Math.ceil(cols * rows * 0.003); i > 0; i -= 1) glyph[Math.floor(Math.random() * glyph.length)] = Math.floor(Math.random() * s.A.n);
      },
      draw(s, t, dt) {
        this.step(s, Math.min(dt, 0.1));
        const { ctx, cols, rows, heat, glyph, cw, ch, A } = s;
        ctx.clearRect(0, 0, s.w, s.h);
        for (let r = 0; r < rows; r += 1) {
          for (let c = 0; c < cols; c += 1) {
            const i = r * cols + c;
            const h = heat[i];
            if (h < 0.05) continue;
            const tone = h > 0.985 ? 4 : h > 0.7 ? 3 : h > 0.4 ? 2 : h > 0.18 ? 1 : 0;
            stamp(ctx, A, glyph[i], tone, c * cw, r * ch);
          }
        }
      },
    },

    // Ripple tank in braille: three wandering emitters interfere, and the
    // crests are drawn as 2×4 dot cells.
    ripple: {
      fps: 11,
      setup(s) {
        s.pitch = 4.6 * s.k;
        s.cw = s.pitch * 2;
        s.ch = s.pitch * 4;
        s.phase = [rand(0, TAU), rand(0, TAU), rand(0, TAU)];
      },
      resize(s) {
        s.cols = Math.ceil(s.w / s.cw);
        s.rows = Math.ceil(s.h / s.ch);
        // 256 dot patterns in four tones, drawn as dots rather than font
        // braille so every platform shows the same cell.
        const dpr = s.dpr;
        const pal = s.pal;
        const colors = [css(pal.mute, 0.55), css(pal.steel, 0.9), css(pal.brass, 1), css(pal.tint, 1)];
        const a = document.createElement("canvas");
        a.width = Math.ceil(256 * s.cw * dpr);
        a.height = Math.ceil(colors.length * s.ch * dpr);
        const c = a.getContext("2d");
        c.scale(dpr, dpr);
        const r = Math.max(0.6, s.pitch * 0.26);
        colors.forEach((color, row) => {
          c.fillStyle = color;
          for (let m = 1; m < 256; m += 1) {
            c.beginPath();
            for (let bit = 0; bit < 8; bit += 1) {
              if (!(m & (1 << bit))) continue;
              const cx = m * s.cw + ((bit >> 2) + 0.5) * s.pitch;
              const cy = row * s.ch + ((bit & 3) + 0.5) * s.pitch;
              c.moveTo(cx + r, cy);
              c.arc(cx, cy, r, 0, TAU);
            }
            c.fill();
          }
        });
        s.A = { canvas: a, cw: s.cw, ch: s.ch, dpr };
      },
      sources(s, t) {
        const out = [];
        for (let i = 0; i < 3; i += 1) {
          const ph = s.phase[i];
          out.push([
            s.w * (0.5 + 0.46 * Math.sin(t * (0.021 + i * 0.007) + ph)),
            s.h * (0.5 + 0.44 * Math.sin(t * (0.017 + i * 0.005) + ph * 1.7)),
            1,
          ]);
        }
        return out;
      },
      draw(s, t) {
        const { ctx, cols, rows, pitch, cw, ch, A } = s;
        const src = this.sources(s, t);
        const kw = TAU / (46 * s.k);
        const om = t * 2.4;
        const reach = 1 / (380 * s.k);
        ctx.clearRect(0, 0, s.w, s.h);
        for (let cy = 0; cy < rows; cy += 1) {
          for (let cx = 0; cx < cols; cx += 1) {
            let mask = 0;
            let peak = 0;
            for (let bit = 0; bit < 8; bit += 1) {
              const x = cx * cw + ((bit >> 2) + 0.5) * pitch;
              const y = cy * ch + ((bit & 3) + 0.5) * pitch;
              let v = 0;
              for (const q of src) {
                const dx = x - q[0];
                const dy = y - q[1];
                const d = Math.sqrt(dx * dx + dy * dy);
                v += q[2] * Math.exp(-d * reach) * Math.cos(d * kw - om);
              }
              if (v > 0.3) {
                mask |= 1 << bit;
                if (v > peak) peak = v;
              }
            }
            if (!mask) continue;
            const tone = peak > 1 ? 3 : peak > 0.7 ? 2 : peak > 0.45 ? 1 : 0;
            const d = A.dpr;
            ctx.drawImage(A.canvas, mask * cw * d, tone * ch * d, cw * d, ch * d, cx * cw, cy * ch, cw, ch);
          }
        }
      },
    },

    // A circuit board: traces routed on a 45° grid between pads and chips,
    // with signal pulses running down them.
    circuit: {
      fps: 30,
      setup(s) {
        s.g = Math.max(4, 13 * s.k);
      },
      resize(s) {
        const g = s.g;
        const cols = Math.ceil(s.w / g) + 1;
        const rows = Math.ceil(s.h / g) + 1;
        const occ = new Uint8Array(cols * rows);
        const at = (x, y) => (x < 0 || y < 0 || x >= cols || y >= rows ? 1 : occ[y * cols + x]);
        // Chips first, so traces route around them.
        const chips = [];
        const nChips = Math.max(1, Math.round((cols * rows) / 900));
        for (let i = 0; i < nChips * 4 && chips.length < nChips; i += 1) {
          const cw = Math.floor(rand(4, 9));
          const chh = Math.floor(rand(3, 6));
          const x0 = Math.floor(rand(1, cols - cw - 1));
          const y0 = Math.floor(rand(1, rows - chh - 1));
          let free = true;
          for (let y = y0 - 1; y <= y0 + chh && free; y += 1) for (let x = x0 - 1; x <= x0 + cw && free; x += 1) if (at(x, y)) free = false;
          if (!free) continue;
          for (let y = y0; y < y0 + chh; y += 1) for (let x = x0; x < x0 + cw; x += 1) occ[y * cols + x] = 1;
          chips.push([x0, y0, cw, chh]);
        }
        const DIRS = [[1, 0], [1, 1], [0, 1], [-1, 1], [-1, 0], [-1, -1], [0, -1], [1, -1]];
        const traces = [];
        const tries = Math.round((cols * rows) / 5);
        for (let n = 0; n < tries; n += 1) {
          let x = Math.floor(Math.random() * cols);
          let y = Math.floor(Math.random() * rows);
          if (at(x, y)) continue;
          let dir = Math.floor(Math.random() * 4) * 2;
          const pts = [[x, y]];
          occ[y * cols + x] = 1;
          const want = Math.floor(rand(5, 34));
          for (let step = 0; step < want; step += 1) {
            if (Math.random() < 0.14) dir = (dir + (Math.random() < 0.5 ? 1 : 7)) % 8;
            // A diagonal run soon turns back to a straight one, as on a board.
            else if (dir % 2 && Math.random() < 0.35) dir = (dir + (Math.random() < 0.5 ? 1 : 7)) % 8;
            const [dx, dy] = DIRS[dir];
            const nx = x + dx;
            const ny = y + dy;
            if (at(nx, ny) || (dx && dy && at(x + dx, y) && at(x, y + dy))) break;
            x = nx;
            y = ny;
            occ[y * cols + x] = 1;
            pts.push([x, y]);
          }
          if (pts.length < 4) {
            for (const [px, py] of pts) occ[py * cols + px] = 0;
            continue;
          }
          const path = pts.map(([px, py]) => [px * g, py * g]);
          const len = [0];
          for (let i = 1; i < path.length; i += 1) len.push(len[i - 1] + Math.hypot(path[i][0] - path[i - 1][0], path[i][1] - path[i - 1][1]));
          traces.push({ path, len, total: len[len.length - 1], heat: 0 });
        }
        s.traces = traces;
        s.chips = chips;
        s.pulses = [];
        s.flashes = [];
        s.clock = 0;
        // The quiet board is drawn once.
        const layer = document.createElement("canvas");
        layer.width = Math.ceil(s.w * s.dpr);
        layer.height = Math.ceil(s.h * s.dpr);
        const c = layer.getContext("2d");
        c.scale(s.dpr, s.dpr);
        c.lineCap = "round";
        c.lineJoin = "round";
        c.strokeStyle = css(s.pal.steel, 0.32);
        c.lineWidth = Math.max(1, g * 0.1);
        c.beginPath();
        for (const tr of traces) {
          tr.path.forEach(([px, py], i) => (i ? c.lineTo(px, py) : c.moveTo(px, py)));
        }
        c.stroke();
        c.strokeStyle = css(s.pal.steel, 0.5);
        c.lineWidth = Math.max(1, g * 0.09);
        for (const tr of traces) {
          for (const [px, py] of [tr.path[0], tr.path[tr.path.length - 1]]) {
            c.beginPath();
            c.arc(px, py, g * 0.22, 0, TAU);
            c.stroke();
          }
        }
        for (const [x0, y0, cw, chh] of chips) {
          const X = x0 * g - g * 0.35;
          const Y = y0 * g - g * 0.35;
          const W = (cw - 1) * g + g * 0.7;
          const H = (chh - 1) * g + g * 0.7;
          c.fillStyle = css(s.pal.steel, 0.07);
          c.fillRect(X, Y, W, H);
          c.strokeStyle = css(s.pal.steel, 0.45);
          c.strokeRect(X, Y, W, H);
          c.beginPath();
          c.arc(X + g * 0.5, Y + g * 0.5, g * 0.12, 0, TAU);
          c.stroke();
          c.beginPath();
          for (let i = 0; i < cw; i += 1) {
            c.moveTo(x0 * g + i * g, Y); c.lineTo(x0 * g + i * g, Y - g * 0.3);
            c.moveTo(x0 * g + i * g, Y + H); c.lineTo(x0 * g + i * g, Y + H + g * 0.3);
          }
          c.stroke();
        }
        s.layer = layer;
      },
      warm(s) {
        for (let i = 0; i < 90; i += 1) this.step(s, 1 / 30);
      },
      at(tr, d) {
        const { path, len } = tr;
        let i = 1;
        while (i < len.length - 1 && len[i] < d) i += 1;
        const seg = len[i] - len[i - 1] || 1;
        const u = clamp01((d - len[i - 1]) / seg);
        return [path[i - 1][0] + (path[i][0] - path[i - 1][0]) * u, path[i - 1][1] + (path[i][1] - path[i - 1][1]) * u, i];
      },
      launch(s, tr) {
        if (!tr || s.pulses.length > 60) return;
        const fwd = Math.random() < 0.5;
        s.pulses.push({ tr, d: 0, fwd, v: rand(90, 220) * s.k, tail: rand(26, 60) * s.k });
      },
      step(s, dt) {
        s.clock += dt;
        const rate = Math.max(1.5, s.traces.length / 40);
        if (Math.random() < rate * dt) this.launch(s, pick(s.traces));
        for (const pu of s.pulses) {
          pu.d += pu.v * dt;
          pu.tr.heat = 1;
          if (pu.d >= pu.tr.total + pu.tail) {
            pu.done = true;
            const end = pu.fwd ? pu.tr.path[pu.tr.path.length - 1] : pu.tr.path[0];
            s.flashes.push({ x: end[0], y: end[1], life: 1 });
          }
        }
        s.pulses = s.pulses.filter((pu) => !pu.done);
        for (const f of s.flashes) f.life -= dt * 1.6;
        s.flashes = s.flashes.filter((f) => f.life > 0);
        for (const tr of s.traces) tr.heat *= Math.exp(-dt * 1.4);
      },
      sub(ctx, tr, a, b, fwd) {
        a = Math.max(0, a);
        b = Math.min(tr.total, b);
        if (b <= a) return;
        const map = (d) => (fwd ? d : tr.total - d);
        const [x0, y0, i0] = this.at(tr, map(a));
        const [x1, y1, i1] = this.at(tr, map(b));
        ctx.moveTo(x0, y0);
        if (fwd) for (let i = i0; i < i1; i += 1) ctx.lineTo(tr.path[i][0], tr.path[i][1]);
        else for (let i = i0 - 1; i >= i1; i -= 1) ctx.lineTo(tr.path[i][0], tr.path[i][1]);
        ctx.lineTo(x1, y1);
      },
      draw(s, t, dt) {
        this.step(s, Math.min(dt, 0.1));
        const { ctx, pal, g } = s;
        ctx.clearRect(0, 0, s.w, s.h);
        ctx.drawImage(s.layer, 0, 0, s.w, s.h);
        ctx.lineCap = "round";
        ctx.lineJoin = "round";
        ctx.lineWidth = Math.max(1, g * 0.12);
        for (const tr of s.traces) {
          if (tr.heat < 0.05) continue;
          ctx.strokeStyle = css(pal.brass, tr.heat * 0.28);
          ctx.beginPath();
          tr.path.forEach(([px, py], i) => (i ? ctx.lineTo(px, py) : ctx.moveTo(px, py)));
          ctx.stroke();
        }
        for (const pu of s.pulses) {
          for (const [frac, width, color] of [[1, 3.2, css(pal.brass, 0.18)], [0.6, 1.8, css(pal.brass, 0.7)], [0.18, 2, css(pal.tint, 1)]]) {
            ctx.beginPath();
            this.sub(ctx, pu.tr, pu.d - pu.tail * frac, pu.d, pu.fwd);
            ctx.lineWidth = Math.max(1, width * s.k);
            ctx.strokeStyle = color;
            ctx.stroke();
          }
        }
        for (const f of s.flashes) {
          ctx.beginPath();
          ctx.arc(f.x, f.y, g * (0.25 + (1 - f.life) * 0.9), 0, TAU);
          ctx.strokeStyle = css(pal.tint, f.life * 0.8);
          ctx.lineWidth = 1.2;
          ctx.stroke();
          ctx.beginPath();
          ctx.arc(f.x, f.y, g * 0.18, 0, TAU);
          ctx.fillStyle = css(pal.brass, f.life);
          ctx.fill();
        }
      },
    },

    // A cyclic cellular automaton: every cell advances when a neighbour is one
    // state ahead, and random soup organises itself into rotating spirals.
    // Each state takes a colour from the theme's spectrum.
    spiral: {
      fps: 15,
      previewK: 0.3,
      states: 12,
      setup(s) {
        s.quiet = 0;
      },
      resize(s) {
        cells(s, Math.max(2, Math.round(7 * s.k)));
        const n = s.cols * s.rows;
        s.grid = new Uint8Array(n);
        s.next = new Uint8Array(n);
        this.soup(s);
        const N = this.states;
        s.lut = Array.from({ length: 64 }, (_, i) => ring(s.pal, i / 64));
        s.hue = new Uint8Array(s.cols * s.rows);
        this.inks(s, 0);
        // Wavefronts are bright, the trough behind them nearly clear.
        s.alpha = Float32Array.from({ length: N }, (_, i) => 0.05 + 0.9 * Math.pow(1 - i / N, 2.6));
      },
      // A slow wash of colour across the board: each spiral burns in the
      // accent of the region it sits in.
      inks(s, t) {
        const { cols, rows, hue } = s;
        const k = 2.2 / Math.max(cols, rows);
        for (let y = 0; y < rows; y += 1) {
          for (let x = 0; x < cols; x += 1) hue[y * cols + x] = Math.floor(field(x * k, y * k, t * 0.02) * 1.6 * 64) & 63;
        }
      },
      soup(s) {
        const N = this.states;
        for (let i = 0; i < s.grid.length; i += 1) s.grid[i] = Math.floor(Math.random() * N);
      },
      warm(s) {
        for (let i = 0; i < 240; i += 1) this.step(s);
      },
      step(s) {
        const { cols, rows, grid, next } = s;
        const N = this.states;
        let changed = 0;
        for (let y = 0; y < rows; y += 1) {
          const up = ((y - 1 + rows) % rows) * cols;
          const mid = y * cols;
          const down = ((y + 1) % rows) * cols;
          for (let x = 0; x < cols; x += 1) {
            const v = grid[mid + x];
            const w = v + 1 === N ? 0 : v + 1;
            const l = (x - 1 + cols) % cols;
            const r = (x + 1) % cols;
            const adv = grid[up + x] === w || grid[down + x] === w || grid[mid + l] === w || grid[mid + r] === w;
            next[mid + x] = adv ? w : v;
            if (adv) changed += 1;
          }
        }
        s.grid = next;
        s.next = grid;
        s.quiet = changed < cols * rows * 0.01 ? s.quiet + 1 : 0;
        if (s.quiet > 20) {
          this.soup(s);
          s.quiet = 0;
        }
      },
      draw(s, t) {
        this.step(s);
        if (!s.inkAt || t - s.inkAt > 1.5) {
          s.inkAt = t;
          this.inks(s, t);
        }
        const { grid, img, lut, hue, alpha } = s;
        const N = this.states;
        const px = img.data;
        // Behind the front the colour drifts a little further round the ring.
        for (let i = 0; i < grid.length; i += 1) put(px, i, lut[(hue[i] + Math.floor((grid[i] * 20) / N)) & 63], alpha[grid[i]]);
        blit(s);
      },
    },

    // Gray–Scott reaction–diffusion in its coral regime. Growth fills the
    // screen, patches erode away, and the reef grows back into them.
    coral: {
      fps: 12,
      previewK: 0.4,
      setup(s) {
        s.clock = 0;
      },
      resize(s) {
        cells(s, Math.max(2, Math.round(5 * s.k)));
        const n = s.cols * s.rows;
        s.U = new Float32Array(n).fill(1);
        s.V = new Float32Array(n);
        s.U2 = new Float32Array(n);
        s.V2 = new Float32Array(n);
        const seeds = Math.max(3, Math.round(n / 1400));
        for (let i = 0; i < seeds; i += 1) this.drop(s, Math.floor(Math.random() * s.cols), Math.floor(Math.random() * s.rows), 3);
      },
      drop(s, cx, cy, r) {
        for (let y = -r; y <= r; y += 1) for (let x = -r; x <= r; x += 1) {
          if (x * x + y * y > r * r) continue;
          const i = ((cy + y + s.rows) % s.rows) * s.cols + ((cx + x + s.cols) % s.cols);
          s.U[i] = 0.5;
          s.V[i] = 0.25 + Math.random() * 0.1;
        }
      },
      erode(s, cx, cy, r) {
        for (let y = -r; y <= r; y += 1) for (let x = -r; x <= r; x += 1) {
          if (x * x + y * y > r * r) continue;
          const i = ((cy + y + s.rows) % s.rows) * s.cols + ((cx + x + s.cols) % s.cols);
          s.U[i] = 1;
          s.V[i] = 0;
        }
      },
      warm(s) {
        for (let i = 0; i < 420; i += 1) this.step(s);
      },
      step(s) {
        const { cols, rows } = s;
        const U = s.U;
        const V = s.V;
        const U2 = s.U2;
        const V2 = s.V2;
        const F = 0.0545;
        const K = 0.062;
        for (let y = 0; y < rows; y += 1) {
          const up = ((y - 1 + rows) % rows) * cols;
          const mid = y * cols;
          const down = ((y + 1) % rows) * cols;
          for (let x = 0; x < cols; x += 1) {
            const l = (x - 1 + cols) % cols;
            const r = (x + 1) % cols;
            const i = mid + x;
            const u = U[i];
            const v = V[i];
            const lu = 0.2 * (U[up + x] + U[down + x] + U[mid + l] + U[mid + r]) + 0.05 * (U[up + l] + U[up + r] + U[down + l] + U[down + r]) - u;
            const lv = 0.2 * (V[up + x] + V[down + x] + V[mid + l] + V[mid + r]) + 0.05 * (V[up + l] + V[up + r] + V[down + l] + V[down + r]) - v;
            const uvv = u * v * v;
            U2[i] = u + (lu - uvv + F * (1 - u));
            V2[i] = v + (0.5 * lv + uvv - (K + F) * v);
          }
        }
        s.U = U2; s.U2 = U;
        s.V = V2; s.V2 = V;
      },
      draw(s, t, dt) {
        s.clock += dt;
        if (s.clock > 5) {
          s.clock = 0;
          this.erode(s, Math.floor(Math.random() * s.cols), Math.floor(Math.random() * s.rows), Math.floor(rand(10, 24)));
          this.drop(s, Math.floor(Math.random() * s.cols), Math.floor(Math.random() * s.rows), 2);
        }
        for (let i = 0; i < 8; i += 1) this.step(s);
        const { V, img, pal } = s;
        const px = img.data;
        let total = 0;
        for (let i = 0; i < V.length; i += 1) {
          const v = V[i];
          total += v;
          const u = clamp01((v - 0.08) * 3.4);
          if (u <= 0.02) {
            px[i * 4 + 3] = 0;
            continue;
          }
          const c = u < 0.55 ? mix(pal.steel, pal.brass, u / 0.55) : mix(pal.brass, pal.tint, (u - 0.55) / 0.45);
          put(px, i, c, 0.18 + 0.82 * u);
        }
        if (total < V.length * 0.004) this.drop(s, Math.floor(Math.random() * s.cols), Math.floor(Math.random() * s.rows), 3);
        blit(s);
      },
    },

    // Riso print: three halftone screens at their own angles, one per spectrum
    // ink, each driven by its own drift and a little off register. Overlaps
    // mix like ink.
    riso: {
      fps: 8,
      previewK: 0.4,
      setup(s) {
        s.pitch = 17 * s.k;
      },
      resize(s) {
        const pal = s.pal;
        const inks = [pal.brass, pal.spectrum[1], pal.spectrum[3]];
        const R = s.pitch * 0.5;
        const steps = 20;
        const dpr = s.dpr;
        s.inks = inks.map((ink) => {
          const a = document.createElement("canvas");
          const cell = Math.ceil(R * 2 + 2);
          a.width = Math.ceil(steps * cell * dpr);
          a.height = Math.ceil(cell * dpr);
          const c = a.getContext("2d");
          c.scale(dpr, dpr);
          c.fillStyle = css(ink, 0.8);
          for (let i = 1; i < steps; i += 1) {
            c.beginPath();
            c.arc(i * cell + cell / 2, cell / 2, (R * i) / (steps - 1), 0, TAU);
            c.fill();
          }
          return { canvas: a, cell, steps };
        });
        s.angles = [0.26, 1.31, 0.79];
      },
      draw(s, t) {
        const { ctx, pitch } = s;
        ctx.clearRect(0, 0, s.w, s.h);
        ctx.globalCompositeOperation = s.pal.dark ? "screen" : "multiply";
        const cx0 = s.w / 2;
        const cy0 = s.h / 2;
        // Each ink's density is sampled on a coarse grid and interpolated.
        const G = 24 * s.k;
        const gw = Math.ceil(s.w / G) + 2;
        const gh = Math.ceil(s.h / G) + 2;
        if (!s.dens || s.dens.length !== gw * gh) s.dens = new Float32Array(gw * gh);
        const dens = s.dens;
        const reach = Math.hypot(s.w, s.h) / 2 + pitch;
        s.inks.forEach((ink, li) => {
          const ang = s.angles[li];
          const cos = Math.cos(ang);
          const sin = Math.sin(ang);
          const ox = cx0 + Math.sin(t * 0.07 + li * 2) * 3 * s.k;
          const oy = cy0 + Math.cos(t * 0.05 + li) * 3 * s.k;
          const span = Math.ceil(reach / pitch);
          const d = s.dpr;
          const size = ink.cell;
          const fk = 0.0028 / s.k;
          const tt = t * (0.05 + li * 0.012);
          for (let gy = 0; gy < gh; gy += 1) {
            for (let gx = 0; gx < gw; gx += 1) dens[gy * gw + gx] = field((gx - 1) * G * fk + li * 11.3, (gy - 1) * G * fk - li * 7.1, tt);
          }
          for (let j = -span; j <= span; j += 1) {
            for (let i = -span; i <= span; i += 1) {
              const lx = i * pitch;
              const ly = j * pitch;
              const x = ox + lx * cos - ly * sin;
              const y = oy + lx * sin + ly * cos;
              if (x < -pitch || y < -pitch || x > s.w + pitch || y > s.h + pitch) continue;
              const fx = x / G + 1;
              const fy = y / G + 1;
              const ix = Math.max(0, Math.min(gw - 2, Math.floor(fx)));
              const iy = Math.max(0, Math.min(gh - 2, Math.floor(fy)));
              const ux = fx - ix;
              const uy = fy - iy;
              const o = iy * gw + ix;
              let v = (dens[o] * (1 - ux) + dens[o + 1] * ux) * (1 - uy) + (dens[o + gw] * (1 - ux) + dens[o + gw + 1] * ux) * uy;
              v = (v - 0.5 - li * 0.02) * 2.6;
              if (v <= 0.04) continue;
              const k = Math.min(ink.steps - 1, Math.round(Math.sqrt(clamp01(v)) * (ink.steps - 1)));
              if (k < 2) continue;
              ctx.drawImage(ink.canvas, k * size * d, 0, size * d, size * d, x - size / 2, y - size / 2, size, size);
            }
          }
        });
        ctx.globalCompositeOperation = "source-over";
      },
    },

    // A glyph starfield flying out of a vanishing point: far stars are dots,
    // near ones become stars with a streak behind them. The vanishing point
    // wanders slowly around the middle of the screen.
    warp: {
      fps: 30,
      previewK: 0.8,
      chars: "·∙+×*✦",
      setup(s) {
        s.clock = Math.random() * 100;
      },
      resize(s) {
        const pal = s.pal;
        const size = Math.round(16 * s.k);
        s.A = atlas(s, this.chars, [css(pal.mute, 0.8), css(pal.steel, 0.9), css(pal.brass, 1), css(pal.tint, 1), css(pal.spectrum[1], 0.95)], size, size, 13 * s.k);
        const n = Math.round(Math.max(50, Math.min(1400, (s.w * s.h) / 1300)));
        s.stars = Array.from({ length: n }, () => this.star(Math.random()));
      },
      star(z) {
        return { x: rand(-1, 1), y: rand(-1, 1), z: z || 1, px: null, py: null, hue: Math.random() < 0.12 };
      },
      warm(s) {
        for (let i = 0; i < 30; i += 1) this.step(s, 1 / 30);
      },
      step(s, dt) {
        s.clock += dt;
        s.vx = 0.5 + 0.08 * Math.sin(s.clock * 0.05);
        s.vy = 0.5 + 0.06 * Math.sin(s.clock * 0.037 + 1.3);
        const v = 0.09;
        for (const st of s.stars) {
          st.z -= v * dt;
          if (st.z <= 0.03) Object.assign(st, this.star(1));
        }
      },
      draw(s, t, dt) {
        this.step(s, Math.min(dt, 0.1));
        const { ctx, A, pal } = s;
        const cx = s.w * s.vx;
        const cy = s.h * s.vy;
        const f = Math.max(s.w, s.h) * 0.42;
        const size = A.cw;
        ctx.clearRect(0, 0, s.w, s.h);
        ctx.lineCap = "round";
        for (const st of s.stars) {
          const x = cx + (st.x / st.z) * f;
          const y = cy + (st.y / st.z) * f;
          if (x < -20 || y < -20 || x > s.w + 20 || y > s.h + 20) {
            Object.assign(st, this.star(1));
            continue;
          }
          const near = 1 - st.z;
          if (st.px !== null && near > 0.4) {
            ctx.beginPath();
            ctx.moveTo(st.px, st.py);
            ctx.lineTo(x, y);
            ctx.strokeStyle = css(st.hue ? pal.spectrum[1] : pal.steel, (near - 0.4) * 0.9);
            ctx.lineWidth = 1 + near;
            ctx.stroke();
          }
          const g = Math.min(A.n - 1, Math.floor(near * near * A.n * 1.1));
          const tone = st.hue && near > 0.4 ? 4 : near > 0.85 ? 3 : near > 0.6 ? 2 : near > 0.3 ? 1 : 0;
          const big = size * (0.8 + near * near * 1.1);
          const d = A.dpr;
          ctx.drawImage(A.canvas, g * A.cw * d, tone * A.ch * d, A.cw * d, A.ch * d, x - big / 2, y - big / 2, big, big);
          // Remember a point a little behind, so streaks have length.
          st.px = x - (x - cx) * 0.06;
          st.py = y - (y - cy) * 0.06;
        }
      },
    },

    // A mandala of glyphs: twelve mirrored wedges of a drifting field, so the
    // noise folds into petals and lattices that bloom and close. The rosette
    // turns very slowly, and each ring burns in its own spectrum ink.
    mandala: {
      fps: 12,
      chars: "·∙:+*✦◆",
      folds: 12,
      setup(s) {
        s.pitch = Math.max(5, 15 * s.k);
      },
      resize(s) {
        const size = Math.round(s.pitch);
        s.A = atlas(s, this.chars, s.pal.spectrum.map((c) => css(c, 0.95)), size, size, 12 * s.k);
        s.cx = s.w / 2;
        s.cy = s.h / 2;
        s.R = Math.hypot(s.w, s.h) / 2 + s.pitch;
        // Sample points of one mirrored half-wedge, ring by ring.
        const pts = [];
        const wedge = Math.PI / this.folds;
        for (let r = s.pitch * 2; r < s.R; r += s.pitch) {
          const count = Math.max(1, Math.round((r * wedge) / s.pitch));
          for (let m = 0; m < count; m += 1) pts.push([r, ((m + 0.5) / count) * wedge]);
        }
        s.pts = pts;
      },
      draw(s, t) {
        const { ctx, A, pts, cx, cy, R, pitch } = s;
        const folds = this.folds;
        const n = A.n;
        const spin = t * 0.012;
        const size = A.cw;
        const d = A.dpr;
        ctx.clearRect(0, 0, s.w, s.h);
        for (const [r, a] of pts) {
          const u = r / R;
          // Polar field: rings breathe outward, petals come from the angle.
          let v = field(u * 6 - t * 0.05, Math.cos(a * folds) * 1.8 + Math.sin(a * 3) * 0.6, t * 0.05);
          v = (v - 0.42) * 2.4 + 0.25 * Math.sin(u * 22 - t * 0.4);
          if (v <= 0.05) continue;
          const g = Math.min(n - 1, Math.floor(v * n));
          const ink = Math.floor((u * 3.2 + t * 0.01) * 5) % 5;
          for (let f = 0; f < folds; f += 1) {
            const base = (f / folds) * TAU + spin;
            for (const ang of [base + a, base - a]) {
              const x = cx + Math.cos(ang) * r;
              const y = cy + Math.sin(ang) * r;
              if (x < -pitch || y < -pitch || x > s.w + pitch || y > s.h + pitch) continue;
              ctx.drawImage(A.canvas, g * A.cw * d, ink * A.ch * d, A.cw * d, A.ch * d, x - size / 2, y - size / 2, size, size);
            }
          }
        }
      },
    },

    // Aurora: curtains of light hang from a wandering arc, brightest along
    // their lower hem and combed into fine vertical rays. Two curtains drift
    // at their own pace, their colour sliding along the theme's spectrum.
    aurora: {
      fps: 20,
      setup(s) {
        s.cell = Math.max(2, Math.round(4 * s.k));
      },
      resize(s) {
        s.cols = Math.ceil(s.w / s.cell);
        s.rows = Math.ceil(s.h / s.cell);
        s.off = document.createElement("canvas");
        s.off.width = s.cols;
        s.off.height = s.rows;
        s.offCtx = s.off.getContext("2d");
        s.img = s.offCtx.createImageData(s.cols, s.rows);
        s.acc = new Float32Array(s.cols * s.rows * 3);
        s.lut = Array.from({ length: 64 }, (_, i) => ring(s.pal, i / 64));
      },
      draw(s, t) {
        const { cols, rows, acc, img, lut } = s;
        acc.fill(0);
        const kx = 1 / cols;
        for (let layer = 0; layer < 2; layer += 1) {
          const tt = t * (0.03 + layer * 0.017);
          const off = layer * 17.3;
          for (let x = 0; x < cols; x += 1) {
            const u = x * kx;
            const hem = rows * (0.34 + 0.3 * noise(u * 2.2 + off, tt) + 0.08 * Math.sin(u * 9 + tt * 6));
            const height = rows * (0.18 + 0.32 * noise(u * 1.4 - off, tt * 1.3 + 5));
            let glow = Math.pow(field(u * 3.5 + off, layer * 3, tt * 1.6), 2.2) * 2.2;
            // Fine rays combed through the curtain.
            glow *= 0.55 + 0.45 * noise(x * 0.35 + off, tt * 4);
            if (glow < 0.02) continue;
            const c = lut[Math.floor((u * 0.9 + layer * 0.35 + tt * 0.4) * 64) & 63];
            const top = Math.max(0, Math.floor(hem - height));
            const bottom = Math.min(rows - 1, Math.ceil(hem + 14));
            for (let y = top; y <= bottom; y += 1) {
              // Bright hem, fading up into the sky; a thin fall-off below.
              const f = y <= hem ? Math.pow(Math.max(0, (y - (hem - height)) / height), 2.4) : Math.exp(-(y - hem) / 3.5);
              const a = glow * f;
              if (a < 0.01) continue;
              const i = (y * cols + x) * 3;
              acc[i] += c[0] * a;
              acc[i + 1] += c[1] * a;
              acc[i + 2] += c[2] * a;
            }
          }
        }
        const px = img.data;
        for (let i = 0, o = 0; i < acc.length; i += 3, o += 4) {
          const r = acc[i];
          const g = acc[i + 1];
          const b = acc[i + 2];
          const m = Math.max(r, g, b);
          if (m < 3) {
            px[o + 3] = 0;
            continue;
          }
          // Keep the hue, carry the intensity in alpha.
          const a = Math.min(1, m / 255);
          const sc = 255 / m;
          px[o] = r * sc; px[o + 1] = g * sc; px[o + 2] = b * sc; px[o + 3] = a * 255;
        }
        s.offCtx.putImageData(img, 0, 0);
        s.ctx.clearRect(0, 0, s.w, s.h);
        s.ctx.imageSmoothingEnabled = true;
        s.ctx.drawImage(s.off, 0, 0, cols * s.cell, rows * s.cell);
      },
    },

    // —— Garden family ——

    // An 8-bit garden that plants itself. A sakura grows up from one side and
    // opens its blossom a cloud at a time, letting petals tumble down onto the
    // grass. A vine climbs the far wall and hangs wisteria from the top, and a
    // flower bed sprouts along the floor. After a long bloom the tree sheds,
    // the scene fades, and the next garden grows with the tree on the other
    // side.
    garden: {
      fps: 15,
      previewK: 0.4,
      rest: 85,
      setup(s) {
        s.side = Math.random() < 0.5 ? -1 : 1;
        s.time = Math.random() * 100;
      },
      resize(s) {
        cells(s, Math.max(2, Math.round(5 * s.k)));
        s.base = new Uint8ClampedArray(s.img.data.length);
        s.fl = flora(s.pal);
        const b = s.fl.blossom;
        // Slot 4 is leaf; 5 is a single flower's petal, near white.
        b[5] = mix(b[3], s.pal.dark ? [255, 255, 255] : s.pal.mist, s.pal.dark ? 0.55 : 0.1);
        s.fl.shade = [mix(b[0], s.pal.ink, 0.25), b[0], b[1], b[2], null, b[3]];
        this.plant(s);
        // A resize replants; show the new layout grown rather than bare.
        if (s.sized) this.warm(s, 0, true);
        s.sized = true;
      },
      plant(s) {
        const R = seeded(newSeed());
        // The scale of the planting; a tall phone screen gets a taller tree.
        const U = Math.min(s.rows, s.cols * 1.5);
        Object.assign(s, {
          U, clock: 0, phase: "grow", phaseT: 0, alpha: 1, speed: U * 0.075, spawn: 0,
          ink: [], inked: 0, clusters: [], racemes: [], flowers: [], petals: [],
          carpet: new Uint8Array(s.cols), grass: new Uint8Array(s.cols), fly: R() * 100,
        });
        s.base.fill(0);
        this.tree(s, R);
        this.vine(s, R);
        this.bed(s, R);
        s.ink.sort((a, b) => a.t - b.t);
        s.clusters.sort((a, b) => b.back - a.back);
        let end = 0;
        for (const e of s.ink) end = Math.max(end, e.t);
        for (const c of s.clusters) end = Math.max(end, c.t + 1);
        for (const r of s.racemes) end = Math.max(end, r.t + 2);
        for (const f of s.flowers) end = Math.max(end, f.t + f.grow);
        s.doneAt = end + 1;
      },

      // The sakura: a short, leaning trunk that splits low into limbs which
      // spread and level off into a wide crown over the top of the screen.
      tree(s, R) {
        const { cols, rows, U } = s;
        const inward = -s.side;
        s.trunkX = s.side < 0 ? cols * R.range(0.07, 0.14) : cols * R.range(0.86, 0.93);
        const r = Math.max(0.8, U * 0.026);
        this.limb(s, R, { x: s.trunkX, y: rows + r, ang: -Math.PI / 2 + inward * R.range(0.1, 0.24), len: U * R.range(0.24, 0.3), r, depth: 0, t: 0, inward });
      },
      limb(s, R, b) {
        const step = 0.7;
        const n = Math.max(2, Math.round(b.len / step));
        const wob = R() * 100;
        const top = s.rows * 0.06;
        let { x, y, ang } = b;
        let dir = ang;
        for (let i = 0; i < n; i += 1) {
          const t = b.t + (i * step) / s.speed;
          if (b.depth) {
            // Sakura limbs spread and level off rather than climb; the outer
            // twigs droop a little.
            const out = Math.cos(ang) >= 0 ? 1 : -1;
            const lift = b.depth >= 4 ? -0.08 : b.depth === 3 ? 0.2 : 0.55;
            const level = out > 0 ? -lift : -Math.PI + lift;
            ang += (level - ang) * 0.007 * b.depth;
          }
          if (y < top) ang += (Math.cos(ang) >= 0 ? 1 : -1) * 0.05;
          // A gnarled line: the wobble bends the limb without steering it.
          dir = ang + (noise(wob + i * 0.09, b.depth) - 0.5) * (b.depth ? 0.9 : 0.5);
          x += Math.cos(dir) * step;
          y += Math.sin(dir) * step;
          s.ink.push({ k: 0, x, y, r: b.r * (1 - 0.32 * (i / n)), t });
          // Blossom along the outer limbs, not only at their tips.
          if (b.depth >= 2 && i % 5 === 3 && R() < 0.6) this.cluster(s, R, x, y, t + 0.4, b.depth === 2 ? 0.8 : 0.65);
        }
        const tEnd = b.t + (n * step) / s.speed;
        if (b.depth >= 5) {
          this.cluster(s, R, x, y, tEnd + 0.2, 1);
          return;
        }
        const forks = b.depth === 0 ? 3 : R() < 0.3 ? 3 : 2;
        for (let f = 0; f < forks; f += 1) {
          const fan = b.depth === 0 ? 0.8 : b.depth === 1 ? 0.6 : 0.45;
          const spread = forks === 3 ? (f - 1) * fan * R.range(0.8, 1.1) : (f ? 1 : -1) * fan * R.range(0.6, 1);
          this.limb(s, R, {
            ...b, x, y,
            ang: dir + spread + (b.depth === 0 ? b.inward * 0.18 : 0),
            len: b.len * R.range(0.62, 0.8) * (b.depth === 0 ? 1.35 : 1),
            r: b.r * 0.64,
            depth: b.depth + 1,
            t: tEnd,
          });
        }
      },
      // A cloud of blossom: a few overlapping discs lit from the upper left,
      // dithered across four pinks with a dark rim underneath, dotted with
      // single flowers and a fresh leaf or two. Cells open from the middle out.
      cluster(s, R, x, y, t, size) {
        const r = Math.max(1.2, s.U * R.range(0.017, 0.028) * size);
        const discs = [[0, 0, r]];
        const extra = Math.floor(R() * 3);
        for (let i = 0; i < extra; i += 1) discs.push([R.range(-1, 1) * r * 0.9, R.range(-0.8, 0.5) * r * 0.8, r * R.range(0.5, 0.85)]);
        const inside = (i, j) => discs.some(([dx, dy, rr]) => (i - dx) ** 2 + (j - dy) ** 2 <= rr * rr + 0.3);
        const cx = Math.round(x);
        const cy = Math.round(y);
        const ext = Math.ceil(r * 2);
        const tone = new Map();
        for (let j = -ext; j <= ext; j += 1) {
          for (let i = -ext; i <= ext; i += 1) {
            if (!inside(i, j)) continue;
            const lit = clamp01(0.5 - (i * 0.5 + j * 0.85) / (r * 2.2));
            let v = Math.max(1, Math.min(3, Math.round(lit * 3 + dither(cx + i, cy + j) * 1.2)));
            if (!inside(i, j + 1) || (!inside(i + 1, j) && lit < 0.5)) v = 0;
            tone.set(i + "," + j, v);
          }
        }
        const keys = [...tone.keys()];
        for (let f = Math.max(1, Math.round(keys.length / 16)); f > 0; f -= 1) {
          const [i, j] = R.pick(keys).split(",").map(Number);
          if (tone.get(i + "," + j) === 0) continue;
          tone.set(i + "," + j, 1);
          for (const [di, dj] of [[1, 0], [-1, 0], [0, 1], [0, -1]]) if (tone.get(i + di + "," + (j + dj))) tone.set(i + di + "," + (j + dj), 5);
        }
        for (let l = Math.floor(R() * 3); l > 0; l -= 1) {
          const [i, j] = R.pick(keys).split(",").map(Number);
          if (!inside(i, j + 1)) tone.set(i + "," + j, 4);
        }
        const list = keys.map((key) => key.split(",").map(Number)).sort((a, b) => Math.hypot(a[0], a[1]) - Math.hypot(b[0], b[1]));
        const flat = new Int8Array(list.length * 3);
        list.forEach(([i, j], n) => { flat[n * 3] = i; flat[n * 3 + 1] = j; flat[n * 3 + 2] = tone.get(i + "," + j); });
        // A third of the clouds sit behind the rest, a shade darker.
        s.clusters.push({ x: cx, y: cy, t, cells: flat, shed: R(), gone: false, back: R() < 0.35 ? 1 : 0 });
      },

      // A vine up the far wall that arches over at the top and hangs wisteria.
      vine(s, R) {
        const { cols, rows, U, fl } = s;
        const wall = -s.side;
        const x0 = wall < 0 ? R.range(1, 3) : cols - R.range(2, 4);
        const top = rows * R.range(0.08, 0.14);
        const ph = R() * TAU;
        const amp = Math.max(1, U * 0.014);
        const speed = s.speed * 0.8;
        const leaf = { a: fl.leaf[0], b: fl.leaf[1], c: fl.leaf[2] };
        const bloom = { c: R() < 0.5 ? fl.violet[2] : fl.sky[2], d: fl.gold[2] };
        let t = R.range(0.5, 2);
        let n = 0;
        const along = (x, y, arch) => {
          s.ink.push({ k: 1, x, y, t });
          n += 1;
          t += 0.5 / speed;
          if (n % 7 === 0) {
            const up = (n / 7) & 1;
            if (arch) s.ink.push({ k: 2, map: SPRITES.leafSmall, x: x - (up ? 0 : 1), y: y + (up ? -2 : 1), slots: leaf, flip: !up, t: t + 0.3 });
            else s.ink.push({ k: 2, map: SPRITES.leaf, x: up ? x + 1 : x - 4, y: y - 3, slots: leaf, flip: !up, t: t + 0.3 });
          }
          if (!arch && n % 23 === 11) s.ink.push({ k: 2, map: SPRITES.curl, x: x - wall * 2 - 1, y: y - 4, slots: leaf, flip: wall > 0, t: t + 0.5 });
          if (n % 31 === 17) s.ink.push({ k: 2, map: SPRITES.bloom, x: x - 1, y: y - 1, slots: bloom, t: t + 0.8 });
        };
        let x = x0;
        for (let y = rows + 1; y > top; y -= 0.5) {
          x = x0 - wall * (amp + Math.sin(y * 0.11 + ph) * amp);
          along(x, y, false);
        }
        const xTop = x;
        const L = cols * R.range(0.16, 0.26);
        let next = R.range(2, 5);
        for (let d = 0; d < L; d += 0.5) {
          const u = d / L;
          const ax = xTop - wall * d;
          const ay = top - Math.sin(Math.min(1, u * 2) * Math.PI * 0.5) * U * 0.03 + u * u * U * 0.05;
          along(ax, ay, true);
          if (d >= next) {
            next = d + R.range(3, 6);
            s.racemes.push({ x: Math.round(ax), y: Math.round(ay) + 1, len: Math.max(2, Math.round(U * R.range(0.035, 0.09) * (1 - u * 0.5))), t: t + R.range(0.5, 3) });
          }
        }
      },

      // The flower bed: grass along the floor and a scatter of tulips,
      // daisies, poppies and bellflowers, sparse through the reading column.
      bed(s, R) {
        const { cols, U, fl } = s;
        for (let x = 0; x < cols; x += 1) s.grass[x] = Math.min(3, Math.floor(noise(x * 0.35, 3.1) * 3 + hash(x, 9) * 1.4));
        const three = (r) => ({ a: r[0], b: r[1], c: r[2] });
        const kinds = [
          { map: SPRITES.tulip, slots: three(fl.poppy) },
          { map: SPRITES.tulip, slots: three(fl.accent) },
          { map: SPRITES.tulipOpen, slots: three(fl.blossom.slice(1)) },
          { map: SPRITES.bloom, slots: { c: fl.white[1], d: fl.gold[1] } },
          { map: SPRITES.bloom, slots: { c: fl.gold[2], d: fl.gold[0] } },
          { map: SPRITES.poppy, slots: three(fl.poppy) },
          { map: SPRITES.bell, slots: three(fl.violet) },
          { map: SPRITES.bell, slots: three(fl.sky) },
        ];
        const n = Math.round(cols * 0.16);
        for (let i = 0; i < n; i += 1) {
          const x = Math.floor(R() * cols);
          const u = x / cols;
          if (u > 0.3 && u < 0.7 && R() < 0.75) continue;
          if (Math.abs(x - s.trunkX) < U * 0.045) continue;
          s.flowers.push({ x, h: Math.round(R.range(2, U * 0.06 + 2)), kind: R.pick(kinds), t: R.range(1.5, 24), grow: R.range(1.2, 3), leaf: R() < 0.6 ? R.range(0.3, 0.6) : 0, flip: R() < 0.5 });
        }
      },

      stamp(s, e) {
        const { base, fl } = s;
        if (e.k === 1) return plot(s, base, e.x, e.y, fl.leaf[0]);
        if (e.k === 2) return sprite(s, base, e.map, Math.round(e.x), Math.round(e.y), e.slots, e.flip);
        const r = e.r;
        if (r < 0.75) return plot(s, base, e.x, e.y, fl.bark[1]);
        const x0 = Math.floor(e.x - r);
        const y0 = Math.floor(e.y - r);
        for (let j = y0; j <= e.y + r; j += 1) {
          for (let i = x0; i <= e.x + r; i += 1) {
            const dx = i + 0.5 - e.x;
            const dy = j + 0.5 - e.y;
            if (dx * dx + dy * dy > r * r) continue;
            const u = dx / r;
            let v = u < -0.4 ? 2 : u > 0.35 ? 0 : 1;
            // Cherry bark: short horizontal lenticels across the trunk.
            if (r > 2 && v === 1 && hash(j, 71) < 0.16 && hash(i >> 1, j, 3) < 0.75) v = 0;
            plot(s, base, i, j, fl.bark[v]);
          }
        }
      },
      open(c, clock) {
        return !c.gone && clock >= c.t + 0.9;
      },
      step(s, dt) {
        s.time += dt;
        s.clock += dt;
        s.alpha = season(s, dt, {
          grow: () => {
            while (s.inked < s.ink.length && s.ink[s.inked].t <= s.clock) this.stamp(s, s.ink[s.inked++]);
            return s.clock >= s.doneAt;
          },
          rest: () => this.shed(s),
          restFor: this.rest,
          fadeFor: 4,
          replant: () => {
            s.side = -s.side;
            this.plant(s);
          },
        });
        this.drift(s, dt);
      },
      // The end of the bloom: the canopy lets go a cloud at a time.
      shed(s) {
        const from = this.rest - 16;
        if (s.phase !== "rest" || s.phaseT < from) return;
        for (const c of s.clusters) {
          if (c.gone || s.phaseT < from + c.shed * 14) continue;
          c.gone = true;
          for (let i = 0; i < c.cells.length; i += 3) {
            if (Math.random() < 0.3) this.petal(s, c.x + c.cells[i], c.y + c.cells[i + 1]);
          }
        }
      },
      petal(s, x, y) {
        if (s.petals.length > s.cols * 1.5) return;
        s.petals.push({ x, y, vy: s.U * rand(0.022, 0.04), ph: Math.random() * TAU, tone: 1 + Math.floor(Math.random() * 3) });
      },
      drift(s, dt) {
        const { cols, rows, U, clusters } = s;
        if (s.phase !== "fade" && clusters.length) {
          s.spawn += dt * (1.5 + gustiness(s.time) * 12) * (cols / 160);
          for (; s.spawn >= 1; s.spawn -= 1) {
            const c = clusters[Math.floor(Math.random() * clusters.length)];
            if (!this.open(c, s.clock)) continue;
            const i = Math.floor(Math.random() * (c.cells.length / 3)) * 3;
            this.petal(s, c.x + c.cells[i], c.y + c.cells[i + 1]);
          }
        }
        const k = U / 180;
        s.petals = s.petals.filter((p) => {
          p.x += (gust(p.x / cols, s.time) * 9 + Math.sin(p.ph + s.time * 2.1) * 2) * k * dt;
          p.y += p.vy * dt * (0.8 + 0.4 * Math.sin(p.ph + s.time * 1.3));
          if (p.x < -3 || p.x > cols + 3) return false;
          const x = Math.floor(p.x);
          if (x >= 0 && x < cols && p.y >= rows - 2 - s.carpet[x]) {
            if (s.carpet[x] < 2 && Math.random() < 0.5) s.carpet[x] += 1;
            return false;
          }
          return true;
        });
      },
      render(s) {
        const { cols, rows, fl, time, clock } = s;
        const px = s.img.data;
        px.set(s.base);
        // Grass, then the petals that have settled into it.
        const grown = Math.floor(clock * 2);
        for (let x = 0; x < cols; x += 1) {
          plot(s, px, x, rows - 1, fl.leaf[0]);
          const h = Math.min(s.grass[x], grown);
          for (let k = 0; k < h; k += 1) {
            const tip = k === h - 1;
            plot(s, px, x + (tip && h > 1 ? sway(x / cols, time, 0.6) : 0), rows - 2 - k, tip ? fl.leaf[2] : fl.leaf[1]);
          }
          for (let k = 0; k < s.carpet[x]; k += 1) plot(s, px, x, rows - 2 - k, fl.blossom[k ? 3 : 2]);
        }
        for (const f of s.flowers) {
          const g = clamp01((clock - f.t) / f.grow);
          if (g <= 0) continue;
          const h = Math.max(1, Math.round(f.h * g));
          const dx = sway(f.x / cols, time, 0.8);
          for (let k = 0; k < h; k += 1) plot(s, px, f.x + (k > h - 2 ? dx : 0), rows - 2 - k, fl.leaf[1]);
          if (f.leaf && h > 3) plot(s, px, f.x + (f.flip ? -1 : 1), rows - 2 - Math.round(h * f.leaf), fl.leaf[2]);
          if (g < 1) {
            sprite(s, px, SPRITES.bud, f.x + dx, rows - 3 - h, { a: fl.leaf[1], b: f.kind.slots.b || f.kind.slots.c });
            continue;
          }
          const map = f.kind.map;
          sprite(s, px, map, f.x + dx - (map[0].length >> 1), rows - 1 - h - map.length, f.kind.slots, f.flip);
        }
        // Wisteria: racemes open from the top down, the tips still in bud.
        for (const r of s.racemes) {
          const g = clamp01((clock - r.t) / 2);
          if (g <= 0) continue;
          const shown = Math.ceil(r.len * g);
          for (let k = 0; k < shown; k += 1) {
            const f = k / r.len;
            const width = f < 0.3 ? 3 : f < 0.7 ? 2 : 1;
            const dx = Math.max(-1, Math.min(1, Math.round(gust(r.x / cols, time) * f * 1.4)));
            for (let w = 0; w < width; w += 1) {
              const x = r.x - (width >> 1) + w + dx;
              const d = dither(x, r.y + k) > 0;
              plot(s, px, x, r.y + k, fl.violet[f < 0.55 ? (d ? 2 : 1) : d ? 1 : 0]);
            }
          }
        }
        for (const c of s.clusters) {
          if (c.gone) continue;
          const p = (clock - c.t) / 0.9;
          if (p <= 0) continue;
          const n = c.cells.length / 3;
          const shown = p >= 1 ? n : Math.ceil(n * p * p);
          const dx = sway(c.x / cols, time, 0.8);
          const bud = p < 0.4;
          const pink = c.back ? fl.shade : fl.blossom;
          for (let i = 0; i < shown; i += 1) {
            const v = c.cells[i * 3 + 2];
            plot(s, px, c.x + dx + c.cells[i * 3], c.y + c.cells[i * 3 + 1], v === 4 ? fl.leaf[1] : pink[bud ? Math.min(v, 1) : v]);
          }
        }
        // Petals tumble: flat, then edge-on, then flat again.
        for (const p of s.petals) {
          const c = fl.blossom[p.tone];
          plot(s, px, p.x, p.y, c);
          if (Math.sin(p.ph + time * 4.5) > 0.2) plot(s, px, p.x + 1, p.y, fl.blossom[p.tone - 1]);
        }
        // One butterfly works the flower bed once it is in bloom.
        if (s.phase === "rest") {
          const bx = cols * (0.5 + 0.47 * Math.sin(time * 0.037 + s.fly)) + (noise(time * 0.3, s.fly) - 0.5) * s.U * 0.12;
          const by = rows - s.U * (0.08 + 0.1 * noise(time * 0.22, s.fly + 5));
          sprite(s, px, Math.floor(time * 7) & 1 ? SPRITES.flyOpen : SPRITES.flyShut, Math.round(bx), Math.round(by), { a: fl.bark[0], b: fl.accent[1], c: fl.accent[2] });
        }
        blit(s, false, s.alpha);
      },
      warm(s, t, full) {
        const until = full ? s.doneAt + 14 : s.doneAt * 0.6;
        for (let i = 0; i < 5000 && s.clock < until && s.phase !== "fade"; i += 1) this.step(s, 1 / 15);
      },
      draw(s, t, dt) {
        this.step(s, Math.min(dt, 0.2));
        this.render(s);
      },
    },

    // Botanica: a pen-and-ink plate that draws itself, like an old botanical
    // engraving coloured by hand. Tall borders of wildflowers, ferns, vines,
    // dandelion clocks and grasses rise up both sides of the page, drawn with
    // a bold line and filled with solid colour so they hold their own behind
    // the text. Leaves and petals unfurl
    // as the pen reaches them. When the plate is full the dandelions let their
    // seeds go on the wind before it fades and is drawn again.
    botanica: {
      fps: 30,
      setup(s) {
        s.time = 0;
      },
      resize(s) {
        // Drawing scale: plants and flowers grow with the screen.
        s.z = s.k * Math.max(0.5, Math.min(1.6, Math.min(s.w, s.h) / 680));
        s.fl = flora(s.pal);
        // Green-black ink for outlines. On a dark ground the stems are drawn
        // in green and the outlines cut dark, like leading in glass.
        s.inkC = s.pal.dark ? mix(s.pal.ink, s.fl.leaf[0], 0.35) : mix(s.pal.mist, s.fl.leaf[0], 0.35);
        s.stemC = s.pal.dark ? s.fl.leaf[1] : s.inkC;
        s.bed = document.createElement("canvas");
        s.bed.width = Math.max(1, Math.round(s.w * s.dpr));
        s.bed.height = Math.max(1, Math.round(s.h * s.dpr));
        s.bctx = s.bed.getContext("2d");
        s.bctx.setTransform(s.dpr, 0, 0, s.dpr, 0, 0);
        s.bctx.lineCap = "round";
        s.bctx.lineJoin = "round";
        this.plant(s);
        if (s.sized) this.warm(s, 0, true);
        s.sized = true;
      },
      plant(s) {
        s.bctx.clearRect(0, 0, s.w, s.h);
        const want = Math.max(6, Math.round(s.w / (62 * s.z)));
        const bag = ["flower", "flower", "flower", "flower", "flower", "flower", "fern", "vine", "dandelion", "grass"];
        const queue = Array.from({ length: want }, (_, i) => (i < 2 ? "grass" : pick(bag)));
        Object.assign(s, { tips: [], live: [], heads: [], seeds: [], queue, planted: 0, clock: 0, nextPlant: 0, phase: "grow", phaseT: 0, alpha: 1 });
      },
      // Most plants come up out of the margins, where the page is quiet.
      plantOne(s) {
        const { z: k, h, w } = s;
        const sp = s.queue[s.planted];
        s.planted += 1;
        const fromWall = sp === "vine" || (sp === "flower" && Math.random() < 0.2);
        let x;
        let y;
        let ang;
        if (!fromWall) {
          // Two borders up the sides, thinning toward the reading column.
          const edge = Math.pow(Math.random(), 1.6) * 0.3;
          const u = Math.random() < 0.5 ? 0.01 + edge : 0.99 - edge;
          x = u * w;
          y = h + 2;
          ang = -Math.PI / 2 + rand(-0.22, 0.22);
        } else {
          const left = Math.random() < 0.5;
          x = left ? -2 : w + 2;
          y = rand(0.3, 0.92) * h;
          ang = left ? rand(-0.9, -0.3) : Math.PI + rand(0.3, 0.9);
        }
        const spec = {
          flower: [rand(0.22, 0.44) * h, 2.8],
          fern: [rand(0.28, 0.5) * h, 2.2],
          vine: [rand(0.35, 0.6) * h, 2],
          dandelion: [rand(0.18, 0.34) * h, 1.8],
          grass: [rand(0.08, 0.16) * h, 1.6],
        }[sp];
        const base = { sp, x, y, depth: 0, dist: 0, next: rand(8, 16) * k, side: pick([-1, 1]), curl: pick([-1, 1]) };
        base.bloom = pick(["daisy", "cosmos", "cosmos", "bell"]);
        base.color = base.bloom === "bell" ? pick([s.fl.violet[2], s.fl.sky[2]]) : pick([s.fl.blossom[2], s.fl.blossom[1], s.fl.white[1], s.fl.poppy[1], s.fl.poppy[2], s.fl.violet[2], s.fl.gold[2], s.fl.sky[2]]);
        if (sp === "grass") {
          for (let i = 0, n = 5 + Math.floor(Math.random() * 4); i < n; i += 1) {
            const len = spec[0] * rand(0.5, 1.1);
            s.tips.push({ ...base, ang: ang + (i / (n - 1) - 0.5) * 1.1 + rand(-0.1, 0.1), left: len, len, width: spec[1] * k + 0.3, seed: Math.random() * 100 });
          }
          return;
        }
        s.tips.push({ ...base, ang, left: spec[0], len: spec[0], width: spec[1] * k + 0.5, seed: Math.random() * 100 });
      },
      grow(s, dt) {
        s.clock += dt;
        if (s.planted < s.queue.length && s.clock >= s.nextPlant) {
          this.plantOne(s);
          s.nextPlant = s.clock + rand(0.35, 1.1);
        }
        const c = s.bctx;
        const ink = s.stemC;
        const next = [];
        for (const tip of s.tips) {
          const speed = (tip.sp === "grass" ? 95 : 72) * s.z;
          const stepLen = Math.min(tip.left, speed * dt * (1 - tip.depth * 0.12));
          const done = 1 - tip.left / tip.len;
          // Each plant's habit steers the stem; the pen's wobble only bends
          // the line around that course.
          if (tip.sp === "vine") tip.ang += Math.sin(tip.dist * 0.02 / s.z + tip.seed) * 0.03;
          else if (tip.sp === "fern") tip.ang += tip.curl * (0.002 + done * done * 0.014);
          else if (tip.sp === "grass") tip.ang += tip.curl * 0.008;
          else tip.ang += Math.sin(-Math.PI / 2 - tip.ang) * 0.02;
          const dir = tip.ang + (noise(tip.seed + tip.dist * 0.02 / s.z, tip.depth) - 0.5) * 0.9;
          const nx = tip.x + Math.cos(dir) * stepLen;
          const ny = tip.y + Math.sin(dir) * stepLen;
          const wdt = tip.width * (1 - done * (tip.sp === "grass" ? 0.8 : 0.4));
          c.beginPath();
          c.moveTo(tip.x, tip.y);
          c.lineTo(nx, ny);
          c.lineWidth = wdt;
          c.strokeStyle = css(tip.sp === "grass" ? s.fl.leaf[1] : ink, 0.95);
          c.stroke();
          // A lighter second pass beside the first, as a pen retraces a line.
          c.beginPath();
          c.moveTo(tip.x + 0.8, tip.y + 0.4);
          c.lineTo(nx + 0.8, ny + 0.4);
          c.lineWidth = Math.max(0.5, wdt * 0.4);
          c.strokeStyle = css(ink, 0.22);
          c.stroke();
          tip.x = nx;
          tip.y = ny;
          tip.left -= stepLen;
          tip.dist += stepLen;
          if (tip.dist >= tip.next) this.feature(s, tip, done);
          if (tip.left > 0.01) next.push(tip);
          else this.finish(s, tip, next);
        }
        s.tips = next;
        return s.planted >= s.queue.length && !s.tips.length && !s.live.length;
      },
      // Leaves, pinnae and tendrils along a stem as the pen passes.
      feature(s, tip, done) {
        const k = s.z;
        const { x, y, ang } = tip;
        tip.side = -tip.side;
        if (tip.sp === "flower" && tip.depth < 3) {
          const len = rand(14, 24) * k * (1 - tip.depth * 0.2);
          this.add(s, { type: "leaf", x, y, ang: ang + tip.side * rand(0.6, 1), len, wid: len * 0.32 });
          tip.next = tip.dist + rand(22, 36) * k;
        } else if (tip.sp === "fern") {
          const len = (5 + 20 * Math.sin(Math.PI * Math.min(1, done * 1.1))) * k;
          for (const side of [-1, 1]) this.add(s, { type: "leaf", x, y, ang: ang + side * 1.05, len, wid: len * 0.28, dur: 0.35, bare: true });
          tip.next = tip.dist + 5 * k;
        } else if (tip.sp === "vine") {
          if (Math.random() < 0.2) this.add(s, { type: "tendril", x, y, ang: ang + tip.side * 0.9, r: rand(6, 10) * k, turn: tip.side });
          else this.add(s, { type: "heart", x, y, ang: ang + tip.side * rand(0.8, 1.2), len: rand(16, 24) * k });
          tip.next = tip.dist + rand(14, 22) * k;
        } else {
          tip.next = Infinity;
        }
      },
      finish(s, tip, next) {
        const k = s.z;
        if (tip.sp === "flower") {
          // A stem ends in a full flower, and every fork on the way carries
          // a smaller one, so each plant finishes in a spray.
          const end = tip.depth >= 2 || tip.len < 22 * k;
          if (end || tip.depth >= 1) this.add(s, { type: "bloom", x: tip.x, y: tip.y, r: (end ? rand(14, 22) : rand(9, 14)) * k + 2, turn: Math.random() * TAU, bloom: tip.bloom, color: tip.color, dur: 0.9 });
          if (end) return;
          const forks = Math.random() < 0.3 ? 3 : 2;
          for (let f = 0; f < forks; f += 1) {
            const spread = forks === 3 ? (f - 1) * 0.55 : (f ? 1 : -1) * rand(0.3, 0.6);
            const len = tip.len * rand(0.5, 0.72);
            next.push({ ...tip, ang: tip.ang + spread + rand(-0.12, 0.12), left: len, len, width: tip.width * 0.72, depth: tip.depth + 1, seed: Math.random() * 100, dist: 0, next: rand(8, 14) * k });
          }
        } else if (tip.sp === "fern") {
          this.add(s, { type: "tendril", x: tip.x, y: tip.y, ang: tip.ang, r: rand(5, 8) * k, turn: tip.curl, dur: 0.8 });
        } else if (tip.sp === "vine") {
          this.add(s, { type: "bloom", x: tip.x, y: tip.y, r: rand(5, 7) * k + 1, turn: 0, bloom: "bell", color: s.fl.violet[2], dur: 0.9 });
        } else if (tip.sp === "dandelion") {
          const n = 26 + Math.floor(Math.random() * 10);
          s.heads.push({ x: tip.x, y: tip.y, r: rand(14, 20) * s.z, t0: s.time, seeds: Array.from({ length: n }, (_, i) => ({ a: (i / n) * TAU + rand(-0.08, 0.08), on: true })) });
        }
      },
      add(s, item) {
        item.t0 = s.time;
        item.dur = item.dur || 0.6;
        s.live.push(item);
      },
      // Finished leaves and petals are inked into the plate for good.
      settle(s) {
        s.live = s.live.filter((item) => {
          if (s.time - item.t0 < item.dur) return true;
          this.paint(s, s.bctx, item, 1);
          return false;
        });
      },
      paint(s, c, it, e) {
        const { fl } = s;
        const ink = css(s.inkC, 0.9);
        c.lineWidth = 1.1 * s.z + 0.3;
        if (it.type === "leaf") {
          const len = it.len * e;
          const wid = it.wid * e;
          const ca = Math.cos(it.ang);
          const sa = Math.sin(it.ang);
          const tx = it.x + ca * len;
          const ty = it.y + sa * len;
          const mx = it.x + ca * len * 0.42;
          const my = it.y + sa * len * 0.42;
          c.beginPath();
          c.moveTo(it.x, it.y);
          c.quadraticCurveTo(mx - sa * wid * 1.6, my + ca * wid * 1.6, tx, ty);
          c.quadraticCurveTo(mx + sa * wid * 1.6, my - ca * wid * 1.6, it.x, it.y);
          c.fillStyle = css(it.bare ? fl.leaf[2] : fl.leaf[1], 0.9);
          c.fill();
          c.strokeStyle = ink;
          c.stroke();
          if (it.bare || len < 6) return;
          // Midrib and a pair of veins, as an engraver would cut them.
          c.beginPath();
          c.moveTo(it.x, it.y);
          c.lineTo(it.x + ca * len * 0.88, it.y + sa * len * 0.88);
          for (const f of [0.35, 0.6]) {
            const bx = it.x + ca * len * f;
            const by = it.y + sa * len * f;
            for (const side of [-1, 1]) {
              c.moveTo(bx, by);
              c.lineTo(bx + ca * len * 0.14 - sa * wid * 0.8 * side, by + sa * len * 0.14 + ca * wid * 0.8 * side);
            }
          }
          c.lineWidth = 0.5;
          c.strokeStyle = css(s.inkC, 0.45);
          c.stroke();
        } else if (it.type === "heart") {
          // An ivy leaf: three shallow lobes around a heart-shaped base.
          const L = it.len * e;
          c.save();
          c.translate(it.x, it.y);
          c.rotate(it.ang);
          c.beginPath();
          ivyLeaf(c, L);
          c.fillStyle = css(fl.leaf[1], 0.9);
          c.fill();
          c.strokeStyle = ink;
          c.stroke();
          c.beginPath();
          c.moveTo(0, 0);
          c.lineTo(L * 0.85, 0);
          c.moveTo(L * 0.25, 0);
          c.lineTo(L * 0.55, -L * 0.3);
          c.moveTo(L * 0.25, 0);
          c.lineTo(L * 0.55, L * 0.3);
          c.lineWidth = 0.5;
          c.strokeStyle = css(s.inkC, 0.45);
          c.stroke();
          c.restore();
        } else if (it.type === "tendril") {
          // A coiled tendril, or a fern's unrolled fiddlehead.
          c.beginPath();
          const turns = 1.8 * e + 0.2;
          for (let i = 0; i <= 40; i += 1) {
            const f = i / 40;
            const a = it.ang + it.turn * f * turns * TAU;
            const r = it.r * (1 - f * 0.85);
            const px = it.x + Math.cos(it.ang) * it.r * f * 1.2 + Math.cos(a + it.turn * Math.PI / 2) * r * f;
            const py = it.y + Math.sin(it.ang) * it.r * f * 1.2 + Math.sin(a + it.turn * Math.PI / 2) * r * f;
            if (i) c.lineTo(px, py);
            else c.moveTo(px, py);
          }
          c.strokeStyle = ink;
          c.stroke();
        } else {
          this.bloom(s, c, it, e);
        }
      },
      bloom(s, c, b, e) {
        const { fl } = s;
        const ink = css(s.inkC, 0.9);
        const r = b.r * (0.2 + 0.8 * e);
        c.save();
        c.translate(b.x, b.y);
        c.lineWidth = 1 * s.z + 0.3;
        if (b.bloom === "bell") {
          // A nodding bellflower on a short hooked stalk.
          c.beginPath();
          c.moveTo(0, 0);
          c.quadraticCurveTo(r * 0.8, -r * 0.4, r * 0.9, r * 0.3);
          c.strokeStyle = ink;
          c.stroke();
          c.translate(r * 0.9, r * 0.3);
          c.beginPath();
          c.moveTo(-r * 0.28, 0);
          c.bezierCurveTo(-r * 0.4, r * 0.6, -r * 0.55, r * 0.9, -r * 0.6, r * 1.15);
          for (let i = 0; i <= 4; i += 1) c.lineTo(-r * 0.6 + (i + 0.5) * r * 0.3, r * (i % 2 ? 1.05 : 1.25));
          c.bezierCurveTo(r * 0.55, r * 0.9, r * 0.4, r * 0.6, r * 0.28, 0);
          c.closePath();
          c.fillStyle = css(b.color, 0.92);
          c.fill();
          c.strokeStyle = ink;
          c.stroke();
          c.beginPath();
          c.moveTo(0, r * 0.2);
          c.lineTo(0, r * 0.95);
          c.moveTo(-r * 0.2, r * 0.3);
          c.lineTo(-r * 0.3, r * 0.9);
          c.lineWidth = 0.5;
          c.strokeStyle = css(s.inkC, 0.4);
          c.stroke();
          c.restore();
          return;
        }
        c.rotate(b.turn + (1 - e) * 0.8);
        const daisy = b.bloom === "daisy";
        const n = daisy ? 14 : 8;
        for (let i = 0; i < n; i += 1) {
          c.save();
          c.rotate((i / n) * TAU);
          c.beginPath();
          if (daisy) {
            c.ellipse(r * 0.58, 0, r * 0.46, r * 0.12, 0, 0, TAU);
          } else {
            // A broad cosmos petal with a notched tip.
            const w = r * 0.34;
            c.moveTo(0, 0);
            c.quadraticCurveTo(r * 0.5, -w * 1.3, r, -w * 0.7);
            c.lineTo(r * 0.9, -w * 0.25);
            c.lineTo(r * 0.98, 0);
            c.lineTo(r * 0.9, w * 0.25);
            c.lineTo(r, w * 0.7);
            c.quadraticCurveTo(r * 0.5, w * 1.3, 0, 0);
          }
          c.fillStyle = css(daisy ? fl.white[1] : b.color, 0.92);
          c.fill();
          c.strokeStyle = ink;
          c.stroke();
          if (!daisy && r > 5) {
            // Hatching toward the heart of the flower.
            c.beginPath();
            for (const off of [-0.18, 0, 0.18]) {
              c.moveTo(r * 0.16, r * off * 0.6);
              c.lineTo(r * 0.42, r * off);
            }
            c.lineWidth = 0.45;
            c.strokeStyle = css(s.inkC, 0.4);
            c.stroke();
            c.lineWidth = 1 * s.z + 0.3;
          }
          c.restore();
        }
        c.beginPath();
        c.arc(0, 0, r * (daisy ? 0.22 : 0.16), 0, TAU);
        c.fillStyle = css(fl.gold[1], 0.9);
        c.fill();
        c.strokeStyle = ink;
        c.stroke();
        for (let i = 0; i < 5; i += 1) {
          const a = (i / 5) * TAU;
          c.fillStyle = css(fl.gold[0], 0.9);
          c.fillRect(Math.cos(a) * r * 0.1 - 0.5, Math.sin(a) * r * 0.1 - 0.5, 1, 1);
        }
        c.restore();
      },
      // Dandelion clocks: each seed a stalk and a tiny parachute.
      clock(s, c, hd) {
        const e = clamp01((s.time - hd.t0) / 0.7);
        const r = hd.r * e;
        c.strokeStyle = css(s.fl.white[1], 0.55);
        c.lineWidth = 0.5;
        c.beginPath();
        for (const sd of hd.seeds) {
          if (!sd.on) continue;
          const ex = hd.x + Math.cos(sd.a) * r;
          const ey = hd.y + Math.sin(sd.a) * r;
          c.moveTo(hd.x + Math.cos(sd.a) * r * 0.2, hd.y + Math.sin(sd.a) * r * 0.2);
          c.lineTo(ex, ey);
          for (const f of [-0.5, 0, 0.5]) {
            c.moveTo(ex, ey);
            c.lineTo(ex + Math.cos(sd.a + f) * r * 0.22, ey + Math.sin(sd.a + f) * r * 0.22);
          }
        }
        c.stroke();
        c.beginPath();
        c.arc(hd.x, hd.y, Math.max(1, r * 0.14), 0, TAU);
        c.fillStyle = css(s.fl.gold[0], 0.8);
        c.fill();
      },
      // In the quiet after the plate is full, the wind picks seeds off the
      // dandelion clocks and carries them away.
      release(s, dt) {
        const g = gustiness(s.time);
        for (const hd of s.heads) {
          if (Math.random() > dt * (0.6 + g * 5)) continue;
          const sd = hd.seeds.find((x) => x.on && Math.random() < 0.3);
          if (!sd) continue;
          sd.on = false;
          s.seeds.push({ x: hd.x + Math.cos(sd.a) * hd.r, y: hd.y + Math.sin(sd.a) * hd.r, vy: rand(-14, -5) * s.z, ph: Math.random() * TAU, life: 0 });
        }
      },
      drift(s, dt) {
        s.seeds = s.seeds.filter((sd) => {
          sd.life += dt;
          sd.x += (gust(sd.x / s.w, s.time) * 28 + 10) * s.z * dt;
          sd.y += (sd.vy + Math.sin(sd.ph + s.time * 1.7) * 6 * s.z) * dt;
          return sd.life < 14 && sd.x < s.w + 10 && sd.y > -10;
        });
      },
      step(s, dt) {
        s.time += dt;
        s.alpha = season(s, dt, {
          grow: () => this.grow(s, dt),
          rest: () => this.release(s, dt),
          restFor: 24,
          fadeFor: 3.5,
          replant: () => this.plant(s),
        });
        this.settle(s);
        this.drift(s, dt);
      },
      warm(s, t, full) {
        for (let i = 0; i < 4000; i += 1) {
          if (s.phase === "grow" ? !full && s.clock > 8 : !full || s.phaseT > 8) break;
          this.step(s, 1 / 30);
        }
      },
      draw(s, t, dt) {
        this.step(s, Math.min(dt, 0.1));
        const { ctx } = s;
        ctx.clearRect(0, 0, s.w, s.h);
        ctx.globalAlpha = s.alpha;
        ctx.drawImage(s.bed, 0, 0, s.w, s.h);
        ctx.lineCap = "round";
        ctx.lineJoin = "round";
        for (const item of s.live) this.paint(s, ctx, item, clamp01((s.time - item.t0) / item.dur));
        for (const hd of s.heads) this.clock(s, ctx, hd);
        ctx.strokeStyle = css(s.fl.white[1], 0.6);
        ctx.lineWidth = 0.6;
        ctx.beginPath();
        for (const sd of s.seeds) {
          const r = 3 * s.z + 1;
          ctx.moveTo(sd.x, sd.y);
          ctx.lineTo(sd.x - 0.4 * r, sd.y + r * 1.2);
          for (const f of [-0.6, 0, 0.6]) {
            ctx.moveTo(sd.x, sd.y);
            ctx.lineTo(sd.x + Math.sin(f) * r, sd.y - Math.cos(f) * r);
          }
        }
        ctx.stroke();
        ctx.globalAlpha = 1;
      },
    },

    // Nouveau: vines climb both side margins in whiplash curves and arch
    // along the top, framing the page like an Art Nouveau poster. Everything
    // carries a dark contour over flat two-tone fills: ivy leaves, long
    // leaves, spiral tendrils, morning-glory trumpets and berries, inside a
    // fine double rule in the theme's accent. Once grown, the leaves stir
    // in the wind; after a while it fades and grows again.
    ivy: {
      fps: 20,
      setup(s) {
        s.time = Math.random() * 100;
      },
      resize(s) {
        s.z = s.k * Math.max(0.45, Math.min(1.5, Math.min(s.w, s.h) / 700));
        s.fl = flora(s.pal);
        s.line = s.pal.dark ? mix(s.pal.ink, [0, 0, 0], 0.35) : mix(s.pal.mist, s.fl.leaf[0], 0.35);
        this.plant(s);
        if (s.sized) this.warm(s, 0, true);
        s.sized = true;
      },
      plant(s) {
        const R = seeded(newSeed());
        Object.assign(s, { clock: 0, phase: "grow", phaseT: 0, alpha: 1, vines: [], parts: [], speed: 95 * s.z });
        s.inset = R.range(26, 40) * s.z;
        for (const side of [-1, 1]) this.vine(s, R, side);
        s.parts.sort((a, b) => a.at - b.at);
        let end = 0;
        for (const v of s.vines) end = Math.max(end, v.start + v.len / s.speed);
        s.doneAt = end + 2;
      },
      // Lay out one vine in left-hand coordinates, then mirror it for the
      // right side. The stem steers along the margin and round the top
      // corner, and a slow sine in its heading throws it into whiplash S's.
      vine(s, R, side) {
        const { w, h, z } = s;
        const m = s.inset;
        const reach = w * R.range(0.3, 0.4);
        const om = R.range(0.011, 0.016) / z;
        const ph = R() * TAU;
        const amp = R.range(0.8, 1.1);
        const mirror = (p) => (side < 0 ? p : { ...p, x: w - p.x, ang: Math.PI - p.ang });
        const path = (x, y, ang, steer, max) => {
          const pts = [];
          for (let d = 0; d < max; d += 3) {
            const target = steer(x, y, d, ang);
            if (target === null) break;
            ang += (target - ang) * 0.08;
            x += Math.cos(ang) * 3;
            y += Math.sin(ang) * 3;
            pts.push({ x, y, ang, d });
          }
          return pts;
        };
        let top = false;
        const main = path(m * 0.4, h + 12, -Math.PI / 2, (x, y, d) => {
          if (!top && y < m + 70 * z) top = true;
          if (top && x > reach) return null;
          const lean = Math.sin(d * om + ph) * amp;
          // Free to swing into the room, but held off the edge of the screen.
          const pull = (q) => Math.max(-0.9, Math.min(1.2, q < m * 0.8 ? (m - q) / (22 * z) : (m - q) / (80 * z)));
          if (!top) return -Math.PI / 2 + pull(x) + lean;
          return pull(y) + lean * 0.8;
        }, 8000).map(mirror);
        const start = R.range(0, 1.5);
        s.vines.push({ pts: main, len: main.length * 3, start, width: 1.5 });
        // A slimmer twin stem winds round the main one.
        const twin = main.map((p, i) => {
          const off = Math.sin(i * 0.05 + ph) * 9 * z;
          return { x: p.x - Math.sin(p.ang) * off, y: p.y + Math.cos(p.ang) * off, ang: p.ang, d: p.d };
        });
        s.vines.push({ pts: twin, len: twin.length * 3, start: start + 0.6, width: 0.7 });
        const at = (i) => start + (i * 3) / s.speed;
        // Leaves, flowers and berries along the main stem.
        let i = Math.floor(R.range(6, 12));
        let flip = 1;
        let n = 0;
        while (i < main.length - 4) {
          const p = main[i];
          flip = -flip;
          n += 1;
          const out = p.ang + flip * (side < 0 ? 1 : -1) * R.range(0.8, 1.2);
          const kind = n % 6 === 3 ? "flower" : n % 9 === 7 ? "berries" : R() < 0.62 ? "heart" : "leaf";
          s.parts.push({ kind, x: p.x, y: p.y, ang: kind === "flower" ? p.ang + (side < 0 ? 1 : -1) * Math.PI / 2 * R.range(0.6, 1) : out, size: R.range(22, 36) * z * (kind === "flower" ? 1.2 : kind === "berries" ? 0.8 : 1), at: at(i) + 0.3, seed: R() * 100 });
          i += Math.floor((R.range(18, 28) * z) / 3);
        }
        // The stem ends in a spray of trumpets.
        const tip = main[main.length - 1];
        if (tip) {
          for (let f = 0; f < 3; f += 1) s.parts.push({ kind: "flower", x: tip.x, y: tip.y, ang: tip.ang + (f - 1) * 0.7, size: R.range(34, 44) * z, at: at(main.length) + 0.3 + f * 0.4, seed: R() * 100 });
        }
        // Side shoots that leave the stem and curl up into a spiral.
        for (let k = 0, shoots = 3 + Math.floor(R() * 3); k < shoots; k += 1) {
          const j = Math.floor(main.length * R.range(0.12, 0.92));
          const p = main[j];
          if (!p) continue;
          const room = side < 0 ? 1 : -1;
          const len = R.range(90, 180) * z;
          const curl = room * R.sign();
          let ang0 = p.ang + room * R.range(0.6, 1.1);
          const shoot = path(p.x, p.y, ang0, (x, y, d) => (d > len ? null : ang0 + curl * Math.pow(d / len, 2.2) * 5.5), len + 3);
          ang0 = shoot.length ? shoot[0].ang : ang0;
          s.vines.push({ pts: shoot, len: shoot.length * 3, start: at(j), width: 0.6 });
          for (let q = 5; q < shoot.length * 0.6; q += Math.floor((R.range(20, 30) * z) / 3)) {
            const sp = shoot[q];
            s.parts.push({ kind: R() < 0.5 ? "leaf" : "heart", x: sp.x, y: sp.y, ang: sp.ang + R.sign() * R.range(0.8, 1.2), size: R.range(15, 24) * z, at: at(j) + (q * 3) / s.speed + 0.3, seed: R() * 100 });
          }
        }
      },
      // A stem in three passes, contour, colour and a fine highlight,
      // tapering from its root to its growing tip in a few steps.
      stroke(s, c, v, upto) {
        const { pts } = v;
        const n = Math.min(pts.length, Math.floor(upto / 3));
        if (n < 2) return;
        const steps = 5;
        for (const [pass, k, color] of [[0, 1, css(s.line, 0.95)], [1, 0.5, css(s.fl.leaf[1], 1)], [2, 0.14, css(s.fl.leaf[2], 0.9)]]) {
          c.strokeStyle = color;
          for (let q = 0; q < steps; q += 1) {
            const a = Math.floor((q * (pts.length - 1)) / steps);
            const b = Math.min(n - 1, Math.floor(((q + 1) * (pts.length - 1)) / steps));
            if (b <= a) break;
            const wdt = (5 * v.width + 1) * s.z * (1 - (q / steps) * 0.55);
            c.lineWidth = Math.max(pass === 2 ? 0.5 : 0.8, wdt * k);
            c.beginPath();
            c.moveTo(pts[a].x, pts[a].y);
            for (let i = a + 1; i <= b; i += 1) c.lineTo(pts[i].x, pts[i].y);
            c.stroke();
          }
        }
      },
      // Flat Nouveau fills: a darker half and a lighter half either side of
      // the midrib, with the contour drawn over both.
      twoTone(s, c, dark, light) {
        c.fillStyle = css(dark, 1);
        c.fill();
        c.save();
        c.clip();
        c.fillStyle = css(light, 1);
        c.fillRect(-100, -100, 200, 100);
        c.restore();
        c.lineWidth = 1.5 * s.z + 0.3;
        c.strokeStyle = css(s.line, 1);
        c.stroke();
      },
      part(s, c, p, e) {
        const { fl, z } = s;
        const L = p.size * e;
        if (L < 1) return;
        c.save();
        c.translate(p.x, p.y);
        c.rotate(p.ang + gust(p.x / s.w + p.seed, s.time) * 0.05);
        if (p.kind === "heart" || p.kind === "leaf") {
          c.beginPath();
          if (p.kind === "heart") ivyLeaf(c, L);
          else leafShape(c, L, L * 0.3);
          this.twoTone(s, c, fl.leaf[0], fl.leaf[1]);
          c.beginPath();
          c.moveTo(0, 0);
          c.lineTo(L * 0.8, 0);
          c.lineWidth = 0.9 * z + 0.2;
          c.strokeStyle = css(fl.leaf[2], 0.9);
          c.stroke();
        } else if (p.kind === "flower") {
          // A morning glory, side on: a slim tube flaring into a wide mouth
          // with a pale star in it.
          c.beginPath();
          c.moveTo(0, -L * 0.06);
          c.quadraticCurveTo(L * 0.45, -L * 0.08, L * 0.72, -L * 0.46);
          c.lineTo(L * 0.72, L * 0.46);
          c.quadraticCurveTo(L * 0.45, L * 0.08, 0, L * 0.06);
          c.closePath();
          this.twoTone(s, c, fl.violet[0], fl.violet[1]);
          c.beginPath();
          c.ellipse(L * 0.72, 0, L * 0.14, L * 0.46, 0, 0, TAU);
          c.fillStyle = css(fl.violet[2], 1);
          c.fill();
          c.lineWidth = 1.5 * z + 0.3;
          c.strokeStyle = css(s.line, 1);
          c.stroke();
          c.beginPath();
          for (let i = 0; i < 5; i += 1) {
            const a = (i / 5) * TAU + 0.3;
            c.moveTo(L * 0.72, 0);
            c.lineTo(L * 0.72 + Math.cos(a) * L * 0.12, Math.sin(a) * L * 0.42);
          }
          c.lineWidth = 0.8 * z + 0.2;
          c.strokeStyle = css(mix(fl.violet[2], s.pal.tint, 0.6), 0.9);
          c.stroke();
          c.beginPath();
          c.ellipse(L * 0.72, 0, L * 0.05, L * 0.12, 0, 0, TAU);
          c.fillStyle = css(s.pal.tint, 1);
          c.fill();
          // Sepals where the tube meets the stem.
          c.beginPath();
          c.moveTo(0, 0);
          c.lineTo(L * 0.2, -L * 0.12);
          c.lineTo(L * 0.14, 0);
          c.lineTo(L * 0.2, L * 0.12);
          c.closePath();
          c.fillStyle = css(fl.leaf[1], 1);
          c.fill();
          c.lineWidth = 1 * z + 0.2;
          c.strokeStyle = css(s.line, 1);
          c.stroke();
        } else {
          for (let i = 0; i < 5; i += 1) {
            const a = i * 2.4;
            const r = L * 0.16;
            const bx = L * 0.3 + Math.cos(a) * L * 0.2 * (i ? 1 : 0);
            const by = Math.sin(a) * L * 0.2 * (i ? 1 : 0);
            c.beginPath();
            c.arc(bx, by, r, 0, TAU);
            c.fillStyle = css(fl.accent[1], 1);
            c.fill();
            c.lineWidth = 1.2 * z + 0.2;
            c.strokeStyle = css(s.line, 1);
            c.stroke();
            c.beginPath();
            c.arc(bx - r * 0.35, by - r * 0.35, r * 0.28, 0, TAU);
            c.fillStyle = css(s.pal.tint, 0.9);
            c.fill();
          }
        }
        c.restore();
      },
      // The poster's double rule, drawn up the sides and over the top.
      frame(s, c, g) {
        const m = s.inset * 0.55;
        const { w, h } = s;
        const side = h - m;
        const total = side * 2 + (w - 2 * m);
        let left = total * g;
        c.strokeStyle = css(s.pal.brass, 0.4);
        for (const [off, lw] of [[0, 1], [4 * s.z, 0.6]]) {
          left = total * g;
          c.lineWidth = lw;
          c.beginPath();
          const a = Math.min(left, side);
          c.moveTo(m + off, h);
          c.lineTo(m + off, h - a);
          const b = Math.min(Math.max(0, left - side), w - 2 * m);
          if (b > 0) c.lineTo(m + off + b - off * 2 * (b / (w - 2 * m)), m + off);
          c.moveTo(w - m - off, h);
          c.lineTo(w - m - off, h - a);
          c.stroke();
        }
      },
      step(s, dt) {
        s.time += dt;
        s.clock += dt;
        s.alpha = season(s, dt, {
          grow: () => s.clock >= s.doneAt,
          restFor: 75,
          fadeFor: 4,
          replant: () => this.plant(s),
        });
      },
      warm(s, t, full) {
        const until = full ? s.doneAt + 1 : s.doneAt * 0.6;
        while (s.clock < until && s.phase !== "fade") this.step(s, 0.25);
      },
      draw(s, t, dt) {
        this.step(s, Math.min(dt, 0.2));
        const { ctx } = s;
        ctx.clearRect(0, 0, s.w, s.h);
        ctx.globalAlpha = s.alpha;
        ctx.lineCap = "round";
        ctx.lineJoin = "round";
        this.frame(s, ctx, clamp01(s.clock / (s.doneAt * 0.7)));
        for (const v of s.vines) this.stroke(s, ctx, v, (s.clock - v.start) * s.speed);
        for (const p of s.parts) {
          const e = clamp01((s.clock - p.at) / 0.8);
          if (e <= 0) break;
          this.part(s, ctx, p, 1 - Math.pow(1 - e, 3));
        }
        ctx.globalAlpha = 1;
      },
    },

    // Meadow: a pixel field of grass along the floor under a faint far hill,
    // in three depths, with the wind rolling across it in waves. Wildflowers
    // nod on taller stems. Fireflies drift and blink above it on dark themes;
    // on light themes a few butterflies work the flowers instead.
    meadow: {
      fps: 20,
      setup(s) {
        s.time = Math.random() * 100;
      },
      resize(s) {
        cells(s, Math.max(2, Math.round(4 * s.k)));
        const { cols, rows } = s;
        const fl = flora(s.pal);
        s.fl = fl;
        s.base = new Uint8ClampedArray(s.img.data.length);
        // The far hill: a soft dithered band behind the grass.
        const H = Math.min(rows, cols * 1.2);
        s.H = H;
        const hill = mix(fl.moss[0], s.pal.ink, 0.35);
        for (let x = 0; x < cols; x += 1) {
          const top = Math.floor(rows - H * (0.17 + 0.07 * noise(x * 0.018, 4.2) + 0.03 * noise(x * 0.06, 7)));
          for (let y = Math.max(0, top); y < rows; y += 1) {
            const f = (y - top) / (rows - top);
            if (y === top || f + dither(x, y) * 0.5 > 0.25) put(s.base, y * cols + x, hill, (y === top ? 0.55 : 0.28) * (s.pal.dark ? 1 : 0.6));
          }
        }
        const R = seeded(newSeed());
        const layers = [
          { n: 1, h: [0.05, 0.1], body: mix(fl.leaf[0], s.pal.ink, 0.3), tip: fl.leaf[0], bend: 0.25 },
          { n: 0.75, h: [0.07, 0.14], body: fl.leaf[0], tip: fl.leaf[1], bend: 0.3 },
          { n: 0.45, h: [0.08, 0.19], body: fl.leaf[1], tip: fl.leaf[2], bend: 0.36 },
        ];
        const dry = [mix(fl.gold[0], fl.leaf[0], 0.65), mix(fl.gold[1], fl.leaf[1], 0.55)];
        s.blades = [];
        layers.forEach((L, li) => {
          for (let i = 0, n = Math.round(cols * L.n); i < n; i += 1) {
            const isDry = R() < 0.07;
            // Every blade has its own set, so the field has texture in a calm.
            s.blades.push({ x: Math.floor(R() * cols), h: Math.max(2, Math.round(H * R.range(L.h[0], L.h[1]))), bend: L.bend, set: R.range(-0.25, 0.25), body: isDry ? dry[0] : L.body, tip: isDry ? dry[1] : L.tip, li });
          }
        });
        const kinds = [
          { c: fl.poppy[1], d: fl.bark[0] },
          { c: fl.sky[1], d: fl.white[2] },
          { c: fl.white[1], d: fl.gold[1] },
          { c: fl.gold[2], d: fl.gold[0] },
          { c: fl.violet[1], d: fl.gold[2] },
        ];
        s.flowers = [];
        for (let i = 0, n = Math.round(cols * 0.08); i < n; i += 1) {
          s.flowers.push({ x: Math.floor(R() * cols), h: Math.max(3, Math.round(H * R.range(0.1, 0.22))), slots: R.pick(kinds) });
        }
        s.glow = mix(fromHsl(68, 0.9, 0.66), s.pal.tint, 0.2);
        s.bugs = Array.from({ length: s.pal.dark ? Math.round(Math.min(34, 10 + cols / 14)) : 3 }, () => ({
          seed: Math.random() * 1000, rate: rand(0.6, 1.3), phase: Math.random() * TAU, hue: pick([fl.accent[2], fl.gold[2], fl.sky[2], fl.blossom[2]]),
        }));
      },
      blade(s, px, x, h, bend, body, tip, t, set = 0) {
        const { rows, cols } = s;
        const lean = (gust(x / cols, t) * bend + set) * h;
        for (let k = 0; k < h; k += 1) {
          const f = k / h;
          plot(s, px, x + Math.round(lean * f * f), rows - 1 - k, k >= h - 2 ? tip : body);
        }
        return x + Math.round(lean);
      },
      draw(s, t, dt) {
        s.time += Math.min(dt, 0.2);
        const time = s.time;
        const { rows, cols, fl, H } = s;
        const px = s.img.data;
        px.set(s.base);
        for (const b of s.blades) this.blade(s, px, b.x, b.h, b.bend, b.body, b.tip, time, b.set);
        for (const f of s.flowers) {
          const hx = this.blade(s, px, f.x, f.h, 0.34, fl.leaf[1], fl.leaf[1], time);
          sprite(s, px, SPRITES.bloom, hx - 1, rows - 2 - f.h, f.slots);
        }
        if (s.pal.dark) {
          // Fireflies: a wandering glow that swells and goes dark.
          for (const b of s.bugs) {
            const x = cols * (0.5 + 0.62 * (noise(b.seed, time * 0.03) - 0.5) * 2);
            const y = rows - H * (0.12 + 0.9 * noise(b.seed + 50, time * 0.04));
            const p = Math.pow(Math.max(0, Math.sin(time * b.rate + b.phase)), 3);
            if (p < 0.03) continue;
            plot(s, px, x, y, s.glow, p);
            for (const [dx, dy] of [[1, 0], [-1, 0], [0, 1], [0, -1]]) plot(s, px, x + dx, y + dy, s.glow, p * 0.35);
            for (const [dx, dy] of [[1, 1], [-1, 1], [1, -1], [-1, -1], [2, 0], [-2, 0], [0, 2], [0, -2]]) plot(s, px, x + dx, y + dy, s.glow, p * 0.12);
          }
        } else {
          for (const b of s.bugs) {
            const x = cols * (0.5 + 0.55 * (noise(b.seed, time * 0.025) - 0.5) * 2);
            const y = rows - H * (0.22 + 0.25 * noise(b.seed + 50, time * 0.06)) + Math.sin(time * 3 + b.phase) * 1.5;
            sprite(s, px, Math.floor(time * 6 + b.phase) & 1 ? SPRITES.flyOpen : SPRITES.flyShut, Math.round(x) - 2, Math.round(y), { a: fl.bark[0], b: b.hue, c: s.pal.tint });
          }
        }
        blit(s, false);
      },
    },

    // Pond: a pixel lily pond along the floor. Reeds and cattails sway at the
    // edges, lily pads bob on shimmering water with lotus open on some, and a
    // koi glides beneath. Everything above the water is mirrored in it,
    // wavering. Dragonflies dart over it; on dark themes fireflies drift
    // above the water and blink in their reflections too.
    pond: {
      fps: 20,
      setup(s) {
        s.time = Math.random() * 100;
      },
      resize(s) {
        cells(s, Math.max(2, Math.round(4 * s.k)));
        const { cols, rows } = s;
        const fl = flora(s.pal);
        s.fl = fl;
        const H = Math.min(rows, cols * 1.2);
        s.H = H;
        s.wl = Math.round(rows - Math.max(4, H * 0.16));
        s.water = s.pal.dark ? mix(fl.sky[0], s.pal.ink, 0.5) : mix(fl.sky[2], s.pal.ink, 0.4);
        s.shine = mix(fl.sky[2], s.pal.tint, 0.4);
        const R = seeded(newSeed());
        // The far bank: a low dithered strip behind the water.
        s.base = new Uint8ClampedArray(s.img.data.length);
        const bank = mix(fl.moss[0], s.pal.ink, 0.3);
        for (let x = 0; x < cols; x += 1) {
          const top = s.wl - Math.round(1 + 5 * noise(x * 0.04, 2.7) * noise(x * 0.011, 5.3));
          for (let y = top; y <= s.wl; y += 1) if (y === top || dither(x, y) > 0) put(s.base, y * cols + x, bank, (y === top ? 0.6 : 0.4) * (s.pal.dark ? 1 : 0.5));
        }
        // Reeds crowd the two edges and thin toward the middle.
        s.reeds = [];
        for (let i = 0, n = Math.round(cols * 0.4); i < n; i += 1) {
          const edge = Math.pow(R(), 1.6) * 0.32;
          const x = Math.floor((R() < 0.5 ? edge : 1 - edge) * cols);
          s.reeds.push({ x, h: Math.max(3, Math.round(H * R.range(0.1, 0.34) * (1 - edge * 1.6))), set: R.range(-0.18, 0.18), cat: R() < 0.2, tone: R() < 0.35 ? 2 : 1 });
        }
        s.pads = [];
        const depth = rows - s.wl;
        for (let i = 0, n = Math.max(3, Math.round(cols / 20)); i < n; i += 1) {
          const x = R() * cols;
          const u = x / cols;
          if (u > 0.3 && u < 0.7 && R() < 0.6) continue;
          const w = Math.max(2, Math.round(R.range(3, 7) * Math.max(0.5, H / 200)));
          s.pads.push({ x, y: s.wl + 1 + Math.floor(R() * Math.max(1, depth * 0.55)), w, lotus: R() < 0.45 ? (R() < 0.3 ? "bud" : "open") : null, ph: R() * TAU });
        }
        s.pads.sort((a, b) => a.y - b.y);
        s.koi = { seed: R() * 100 };
        s.ripples = [];
        s.glow = mix(fromHsl(68, 0.9, 0.66), s.pal.tint, 0.2);
        s.bugs = s.pal.dark
          ? Array.from({ length: Math.round(Math.min(26, 8 + cols / 18)) }, () => ({ seed: Math.random() * 1000, rate: rand(0.6, 1.3), phase: Math.random() * TAU }))
          : [];
        s.flies = Array.from({ length: s.pal.dark ? 1 : 3 }, (_, i) => ({ x: cols * (i % 2 ? 0.85 : 0.15), y: s.wl - H * 0.1, tx: 0, ty: 0, next: 0, hue: [fl.sky[1], fl.accent[1], fl.poppy[1]][i % 3] }));
      },
      reed(s, px, r, t) {
        const { wl, fl, cols } = s;
        const lean = (gust(r.x / cols, t) * 0.28 + r.set) * r.h;
        let tx = r.x;
        for (let k = 0; k < r.h; k += 1) {
          const f = k / r.h;
          tx = r.x + Math.round(lean * f * f);
          plot(s, px, tx, wl - k, k >= r.h - 2 ? fl.leaf[2] : fl.leaf[r.tone]);
        }
        if (r.cat) {
          // A cattail: a brown club with a thin spike above it.
          for (let k = 0; k < 4; k += 1) plot(s, px, tx, wl - r.h - k, fl.bark[k === 3 ? 2 : k ? 1 : 0]);
          plot(s, px, tx, wl - r.h - 5, fl.leaf[1]);
        }
      },
      draw(s, t, dt) {
        s.time += Math.min(dt, 0.2);
        const time = s.time;
        const { cols, rows, wl, fl, H } = s;
        const px = s.img.data;
        px.set(s.base);
        // Above the water: everything drawn here is mirrored below.
        for (const r of s.reeds) this.reed(s, px, r, time);
        if (s.pal.dark) {
          for (const b of s.bugs) {
            const x = cols * (0.5 + 0.62 * (noise(b.seed, time * 0.03) - 0.5) * 2);
            const y = wl - 2 - H * 0.55 * noise(b.seed + 50, time * 0.04);
            const p = Math.pow(Math.max(0, Math.sin(time * b.rate + b.phase)), 3);
            if (p < 0.03) continue;
            plot(s, px, x, y, s.glow, p);
            for (const [dx, dy] of [[1, 0], [-1, 0], [0, 1], [0, -1]]) plot(s, px, x + dx, y + dy, s.glow, p * 0.4);
          }
        }
        // Dragonflies hover, then dart to a new spot.
        for (const f of s.flies) {
          if (time > f.next) {
            f.next = time + rand(1, 3);
            f.tx = cols * (Math.random() < 0.8 ? (Math.random() < 0.5 ? rand(0.02, 0.3) : rand(0.7, 0.98)) : rand(0.3, 0.7));
            f.ty = wl - 3 - H * rand(0.02, 0.3);
          }
          f.x += (f.tx - f.x) * Math.min(1, dt * 4);
          f.y += (f.ty - f.y) * Math.min(1, dt * 4);
          const right = f.tx >= f.x;
          sprite(s, px, Math.floor(time * 14) & 1 ? SPRITES.dragonUp : SPRITES.dragonDown, Math.round(f.x) - 2, Math.round(f.y + Math.sin(time * 5) * 0.6) - 1, { a: f.hue, b: fl.white[2], c: fl.bark[0] }, !right);
        }
        // The water: a tinted body with drifting glints, then the mirror.
        for (let y = wl + 1; y < rows; y += 1) {
          const k = (y - wl) / (rows - wl);
          for (let x = 0; x < cols; x += 1) {
            const i = y * cols + x;
            const glint = noise(x * 0.09 + time * (0.25 + k * 0.2), y * 0.8 - time * 0.05);
            if (glint > 0.74 && ((x + y) & 1) === 0) put(px, i, s.shine, 0.35 + (glint - 0.74) * 2.2);
            else put(px, i, s.water, 0.32 + k * 0.28);
          }
        }
        const reach = Math.min(rows - wl - 1, wl);
        for (let k = 1; k <= reach; k += 1) {
          const src = wl - k;
          const dst = wl + k;
          const wobble = Math.round(Math.sin(k * 0.9 + time * 2.2) * Math.min(1.5, k / 5));
          const fade = 0.55 * (1 - k / (reach + 1));
          for (let x = 0; x < cols; x += 1) {
            const sx = x - wobble;
            if (sx < 0 || sx >= cols) continue;
            const o = (src * cols + sx) * 4;
            const a = px[o + 3] / 255;
            if (a < 0.05) continue;
            put(px, dst * cols + x, mix([px[o], px[o + 1], px[o + 2]], s.water, 0.35), Math.max(px[(dst * cols + x) * 4 + 3] / 255, a * fade));
          }
        }
        // A koi under the surface.
        const kp = s.koi;
        const kx = cols * (0.5 + 0.46 * Math.sin(time * 0.021 + kp.seed));
        const ky = wl + 2 + (rows - wl - 4) * noise(time * 0.05, kp.seed);
        const left = Math.cos(time * 0.021 + kp.seed) < 0;
        const ks = { a: fl.poppy[1], b: fl.white[1], c: fl.poppy[0] };
        const map = SPRITES.koi;
        for (let j = 0; j < map.length; j += 1) {
          for (let i = 0; i < map[0].length; i += 1) {
            const c = ks[map[j][left ? map[0].length - 1 - i : i]];
            if (c) plot(s, px, Math.round(kx) + i, Math.round(ky) + j, mix(c, s.water, 0.3), 0.75);
          }
        }
        // Rings where something breaks the surface.
        if (Math.random() < dt * 0.35) s.ripples.push({ x: rand(0.02, 0.98) * cols, y: wl + 1 + Math.random() * (rows - wl - 2), age: 0 });
        s.ripples = s.ripples.filter((r) => {
          r.age += dt;
          const rad = r.age * 3.5;
          const a = 0.7 * (1 - r.age / 2.2);
          if (a <= 0) return false;
          for (let n = 0, m = Math.max(8, Math.round(rad * 6)); n < m; n += 1) {
            const ang = (n / m) * TAU;
            plot(s, px, r.x + Math.cos(ang) * rad, r.y + Math.sin(ang) * rad * 0.3, s.shine, a);
          }
          return true;
        });
        // Lily pads float on top, each with a slit to its heart.
        for (const p of s.pads) {
          const x0 = Math.round(p.x + Math.sin(time * 0.3 + p.ph) * 1.2);
          const hh = Math.max(1, Math.round(p.w * 0.3));
          for (let j = -hh; j <= hh; j += 1) {
            const half = Math.round(p.w * Math.sqrt(Math.max(0, 1 - (j / (hh + 0.5)) ** 2)));
            for (let i = -half; i <= half; i += 1) {
              if (j === 0 && i > 0) continue;
              const c = j < 0 ? (dither(x0 + i, p.y + j) > 0 ? fl.leaf[2] : fl.leaf[1]) : j === hh ? fl.leaf[0] : fl.leaf[1];
              plot(s, px, x0 + i, p.y + j, c);
            }
          }
          if (p.lotus === "open") sprite(s, px, SPRITES.lotus, x0 - 2, p.y - hh - 4, { a: fl.blossom[1], b: fl.blossom[2], c: fl.blossom[3], d: fl.gold[2] });
          else if (p.lotus === "bud") sprite(s, px, SPRITES.lotusBud, x0 - 1, p.y - hh - 3, { a: fl.blossom[1], b: fl.blossom[2] });
        }
        blit(s, false);
      },
    },

    // Orbit: Pond's pixel-art world, moved to a moon. A ringed planet in the
    // accent's hue hangs over a cratered horizon, a crescent moon and a dithered
    // nebula sit behind twinkling stars, and a rover trundles along the
    // surface. Shooting stars streak through now and then, a satellite crosses
    // slowly, and a small saucer hovers and darts like the pond's dragonfly.
    orbit: {
      fps: 20,
      setup(s) {
        s.time = Math.random() * 100;
      },
      resize(s) {
        cells(s, Math.max(2, Math.round(4 * s.k)));
        const { cols, rows, pal } = s;
        const dark = pal.dark;
        const R = seeded(newSeed());
        const { hue, sat, tone, star } = cosmos(pal);
        s.star = star;
        s.body = [0.24, 0.37, 0.5, 0.66].map((l) => tone(hue, sat, l));
        s.band = [0.2, 0.31, 0.44, 0.58].map((l) => tone(hue + 24, sat * 0.8, l));
        s.ringC = [tone(hue + 10, sat * 0.5, 0.46), tone(hue + 10, sat * 0.4, 0.68)];
        s.moonC = [tone(214, 0.12, 0.34), tone(214, 0.1, 0.56), tone(214, 0.1, 0.78)];
        s.ground = [mix(pal.mist, pal.ink, 0.86), mix(pal.mist, pal.ink, 0.74), mix(pal.mist, pal.ink, 0.58)];
        s.gh = Math.max(4, Math.round(rows * 0.13));
        s.gy = rows - s.gh;
        // The surface profile, so the rover and the props sit on it.
        s.gtop = Array.from({ length: cols }, (_, x) => Math.round(s.gy - 1 - 2.4 * noise(x * 0.03, 1.3) - 1.4 * noise(x * 0.12, 4.1)));
        const unit = Math.min(cols, rows);
        s.pr = Math.max(4, Math.min(38, Math.round(unit * 0.17)));
        s.px = Math.round(cols * (R() < 0.5 ? 0.78 : 0.2));
        s.py = Math.round(Math.min(rows * 0.34, s.gy - s.pr * 1.8));
        s.tilt = R.range(-0.4, -0.22);
        // The static layer: nebula, faint stars, crescent moon, surface.
        s.base = new Uint8ClampedArray(s.img.data.length);
        const neb = [pal.spectrum[1], pal.spectrum[3]];
        const off = R() * 100;
        for (let y = 0; y < s.gy; y += 1) {
          for (let x = 0; x < cols; x += 1) {
            for (let n = 0; n < 2; n += 1) {
              const v = noise(x * 0.028 + off + n * 40, y * 0.05 + n * 17) * noise(x * 0.011 + n * 9, y * 0.02 + off) * 1.9;
              if (v - 0.42 > 0.22 + dither(x, y) * 0.3) put(s.base, y * cols + x, neb[n], (dark ? 0.26 : 0.2) * Math.min(1, (v - 0.4) * 2));
            }
          }
        }
        const mc = Math.round(cols * (s.px / cols > 0.5 ? 0.16 : 0.84));
        const my = Math.round(Math.max(6, Math.min(rows * 0.2, s.gy * 0.3)));
        const mr = Math.max(3, Math.round(s.pr * 0.36));
        for (let j = -mr; j <= mr; j += 1) {
          for (let i = -mr; i <= mr; i += 1) {
            if (i * i + j * j > mr * mr) continue;
            // Cut a crescent: shade the side away from the light.
            const cut = (i - mr * 0.45) ** 2 + (j + mr * 0.2) ** 2 < (mr * 0.9) ** 2;
            const l = cut ? 0 : 1 - (i + mr) / (2.4 * mr);
            if (cut && dither(i, j) < 0) continue;
            const c = s.moonC[cut ? 0 : l > 0.62 ? 2 : 1];
            put(s.base, (my + j) * cols + mc + i, c, cut ? 0.25 : 0.9);
          }
        }
        for (let x = 0; x < cols; x += 1) {
          for (let y = s.gtop[x]; y < rows; y += 1) {
            const d = y - s.gtop[x];
            put(s.base, y * cols + x, s.ground[d === 0 ? 2 : d < 3 && dither(x, y) > 0 ? 1 : 0], d === 0 ? 0.7 : 0.5 + Math.min(0.2, d * 0.02));
          }
        }
        // Craters pressed into the ground, each with a lit rim on its far side.
        for (let i = 0, n = Math.max(3, Math.round(cols / 26)); i < n; i += 1) {
          const cx = Math.floor(R() * cols);
          const cy = Math.min(rows - 2, s.gtop[cx] + 2 + Math.floor(R() * Math.max(1, s.gh - 3)));
          const rx = Math.round(R.range(2, 6) * Math.max(0.6, unit / 160));
          const ry = Math.max(1, Math.round(rx * 0.32));
          for (let j = -ry; j <= ry; j += 1) {
            for (let k = -rx; k <= rx; k += 1) {
              const e = (k / rx) ** 2 + (j / ry) ** 2;
              if (e > 1) continue;
              const x = cx + k;
              const y = cy + j;
              if (x < 0 || y < 0 || x >= cols || y >= rows) continue;
              const rim = e > 0.6 && j < 0;
              put(s.base, y * cols + x, rim ? s.ground[2] : mix(s.ground[0], pal.ink, 0.5), rim ? 0.65 : 0.6);
            }
          }
        }
        // A flag and a radio mast stand on the surface; the mast's lamp blinks.
        s.props = [];
        for (const [u, kind] of [[R.range(0.05, 0.3), "flag"], [R.range(0.68, 0.94), "mast"]]) {
          const x = Math.floor(u * cols);
          const top = s.gtop[x];
          const h = Math.max(5, Math.round(unit * 0.05));
          for (let k = 0; k < h; k += 1) put(s.base, (top - k) * cols + x, s.ground[2], 0.85);
          if (kind === "flag") {
            for (let j = 0; j < 3; j += 1) for (let i = 1; i <= 4; i += 1) put(s.base, (top - h + j) * cols + x + i, pal.brass, 0.85);
          } else {
            for (let i = -2; i <= 2; i += 1) put(s.base, (top - h + 1) * cols + x + i, s.ground[2], 0.8);
            s.lamp = { x, y: top - h - 1 };
          }
        }
        // Twinkling stars in three depths; the near ones drift slowly left.
        s.stars = starfield(R, cols, s.gy);
        s.shots = [];
        s.sat = null;
        s.rover = { seed: R() * 100 };
        s.ufo = { x: cols * 0.4, y: s.gy * 0.4, tx: cols * 0.4, ty: s.gy * 0.4, next: 0 };
      },
      draw(s, t, dt) {
        s.time += Math.min(dt, 0.2);
        const time = s.time;
        const { cols, rows, pal, gy } = s;
        const px = s.img.data;
        px.set(s.base);
        twinkle(s, px, s.stars, s.star, time, 0.28);
        // The planet: a lit sphere, dithered, with cloud bands that drift.
        const { pr, body, band } = s;
        const lx = -0.62;
        const ly = -0.5;
        const lz = 0.6;
        for (let j = -pr; j <= pr; j += 1) {
          for (let i = -pr; i <= pr; i += 1) {
            const d2 = (i * i + j * j) / (pr * pr);
            if (d2 > 1) continue;
            const nz = Math.sqrt(1 - d2);
            const lit = (i / pr) * lx + (j / pr) * ly + nz * lz;
            const banded = Math.sin((j / pr) * 7 + noise(i * 0.12 + time * 0.05, j * 0.2) * 3) > 0.25;
            const v = lit * 1.15 + 0.1 + dither(s.px + i, s.py + j) * 0.3;
            const k = v < 0.15 ? 0 : v < 0.5 ? 1 : v < 0.85 ? 2 : 3;
            plot(s, px, s.px + i, s.py + j, (banded ? band : body)[k], 0.95);
          }
        }
        // The ring: an ellipse around the planet, passing behind the top half
        // and in front of the bottom half.
        const rr = pr * 1.75;
        const steps = Math.round(rr * 7);
        for (let front = 0; front < 2; front += 1) {
          for (let n = 0; n < steps; n += 1) {
            const a = (n / steps) * TAU;
            const sn = Math.sin(a);
            if ((sn > 0) !== (front === 1)) continue;
            const ex = Math.cos(a) * rr;
            const ey = sn * rr * 0.26;
            const x = ex * Math.cos(s.tilt) - ey * Math.sin(s.tilt);
            const y = ex * Math.sin(s.tilt) + ey * Math.cos(s.tilt);
            if (!front && x * x + y * y < pr * pr * 0.96) continue;
            const c = s.ringC[Math.cos(a * 2) > 0.2 ? 1 : 0];
            plot(s, px, s.px + x, s.py + y, c, front ? 0.85 : 0.6);
            if (pr > 10) plot(s, px, s.px + x * 0.88, s.py + y * 0.88, s.ringC[0], front ? 0.5 : 0.35);
          }
        }
        // A satellite makes one slow crossing, blinking as it goes.
        if (!s.sat && Math.random() < dt * 0.03) {
          const right = Math.random() < 0.5;
          s.sat = { x: right ? -6 : cols + 6, y: rand(0.08, 0.5) * gy, v: (right ? 1 : -1) * cols / rand(55, 90) };
        }
        if (s.sat) {
          const sa = s.sat;
          sa.x += sa.v * dt;
          if (sa.x < -8 || sa.x > cols + 8) s.sat = null;
          else {
            sprite(s, px, ["b   b", "bccab", "b   b"], Math.round(sa.x) - 2, Math.round(sa.y), { a: pal.tint, b: s.moonC[1], c: s.moonC[2] }, sa.v < 0);
            if (Math.sin(time * 4) > 0.8) plot(s, px, sa.x, sa.y - 1, pal.tint, 0.95);
          }
        }
        // A saucer hovers, then darts to a new spot.
        const u = s.ufo;
        if (time > u.next) {
          u.next = time + rand(3, 8);
          u.tx = cols * rand(0.06, 0.94);
          u.ty = gy * rand(0.1, 0.6);
        }
        u.x += (u.tx - u.x) * Math.min(1, dt * 1.6);
        u.y += (u.ty - u.y) * Math.min(1, dt * 1.6);
        const blink = Math.floor(time * 3) % 3;
        sprite(s, px, ["  bbb  ", " aaaaaaa", "c d e f"], Math.round(u.x) - 3, Math.round(u.y + Math.sin(time * 2) * 0.8) - 1, {
          a: s.moonC[2], b: s.moonC[1], c: blink === 0 ? pal.tint : s.moonC[0], d: blink === 1 ? pal.tint : s.moonC[0], e: blink === 2 ? pal.tint : s.moonC[0], f: blink === 0 ? pal.tint : s.moonC[0],
        });
        meteors(s, px, dt, gy, pal.tint, s.star);
        // The rover rolls slowly back and forth along the surface.
        const rv = s.rover;
        const ph = time * 0.024 + rv.seed;
        const rx = Math.round(cols * (0.5 + 0.44 * Math.sin(ph)));
        const face = Math.cos(ph) < 0;
        const ry = s.gtop[Math.max(0, Math.min(cols - 1, rx))];
        sprite(s, px, ["  aa  ", " bbbbc", "bbbbbb", "d d d "], rx - 3, ry - 3, { a: pal.tint, b: s.moonC[2], c: pal.brass, d: s.ground[1] }, face);
        if (s.lamp && Math.sin(time * 2.4) > 0.6) {
          plot(s, px, s.lamp.x, s.lamp.y, pal.fault, 0.95);
          for (const [dx, dy] of [[1, 0], [-1, 0], [0, -1]]) plot(s, px, s.lamp.x + dx, s.lamp.y + dy, pal.fault, 0.35);
        }
        blit(s, false);
      },
    },

    // Galaxy: a spiral galaxy seen from above and a little tilted, in pixels.
    // Two arms of stars wind out of a warm core, dotted with pink star-forming
    // knots, and the whole disc turns very slowly. Light piles up where stars
    // crowd, so the arms glow and fray at their tips. A small companion galaxy
    // and a far elliptical sit off to the side of the field.
    galaxy: {
      fps: 15,
      setup(s) {
        s.time = Math.random() * 100;
      },
      resize(s) {
        cells(s, Math.max(2, Math.round(4 * s.k)));
        const { cols, rows, pal } = s;
        const R = seeded(newSeed());
        const k = cosmos(pal);
        s.star = k.star;
        s.core = [0.5, 0.66, 0.82].map((l) => k.tone(40, 0.7, l));
        s.arm = [0.4, 0.56, 0.72, 0.86].map((l) => mix(k.tone(215, 0.5, l), pal.spectrum[4], 0.2));
        s.knot = k.tone(330, 0.7, 0.66);
        s.stars = starfield(R, cols, rows, 170);
        s.acc = new Float32Array(cols * rows);
        s.gx = cols * (R() < 0.5 ? 0.72 : 0.28);
        s.gy = rows * 0.42;
        s.gr = Math.min(cols * 0.22, rows * 0.6);
        s.tilt = R.range(-0.5, -0.2);
        s.flat = R.range(0.42, 0.55);
        // Each star keeps its place along the arm: radius, arm, and scatter.
        const n = Math.round(Math.max(1200, Math.min(7000, cols * rows * 0.09)));
        s.parts = Array.from({ length: n }, (_, i) => {
          const bulge = i < n * 0.22;
          const r = bulge ? Math.abs(R() + R() - 1) * 0.22 : 0.08 + Math.pow(R(), 0.8) * 0.92;
          return { r, arm: i & 1, off: bulge ? R() * TAU : (R() + R() + R() - 1.5) * (0.2 + r * 0.22), bulge, w: bulge ? 0.6 : R.range(0.35, 0.9), knot: !bulge && r > 0.25 && R() < 0.02 };
        });
        s.moons = [
          { x: s.gx + (s.gx > cols / 2 ? -1 : 1) * s.gr * 1.05, y: s.gy + s.gr * 0.55, r: Math.max(2, s.gr * 0.07), tone: 0.55 },
          { x: s.gx > cols / 2 ? cols * 0.1 : cols * 0.9, y: rows * 0.8, r: Math.max(1.5, s.gr * 0.04), tone: 0.4 },
        ];
      },
      draw(s, t, dt) {
        s.time += Math.min(dt, 0.2);
        const time = s.time;
        const { cols, rows, acc } = s;
        const px = s.img.data;
        px.fill(0);
        twinkle(s, px, s.stars, s.star, time);
        acc.fill(0);
        const spin = time * 0.012;
        const ct = Math.cos(s.tilt);
        const st = Math.sin(s.tilt);
        const add = (x, y, v) => {
          const xi = Math.round(x);
          const yi = Math.round(y);
          if (xi < 0 || yi < 0 || xi >= cols || yi >= rows) return;
          acc[yi * cols + xi] += v;
        };
        const knots = [];
        for (const p of s.parts) {
          // Arms are logarithmic spirals; stars near the core turn faster.
          const a = p.bulge ? p.off + spin * 3 : p.arm * Math.PI + Math.log(p.r + 0.04) * 3.1 + p.off + spin / (0.3 + p.r);
          const rr = p.r * s.gr;
          const ex = Math.cos(a) * rr;
          const ey = Math.sin(a) * rr * s.flat;
          const x = s.gx + ex * ct - ey * st;
          const y = s.gy + ex * st + ey * ct;
          add(x, y, p.w);
          if (p.knot) knots.push([x, y]);
        }
        for (let y = 0; y < rows; y += 1) {
          for (let x = 0; x < cols; x += 1) {
            const i = y * cols + x;
            const v = acc[i];
            if (v <= 0) continue;
            const dx = x - s.gx;
            const dy = (y - s.gy) / s.flat;
            const core = 1 - clamp01(Math.sqrt(dx * dx + dy * dy) / (s.gr * 0.3));
            const lv = v + dither(x, y) * 0.4;
            if (lv < 0.35) continue;
            const c = core > 0.25 ? s.core[Math.min(2, Math.floor(lv * 0.9))] : s.arm[Math.min(3, Math.floor(lv * 1.1))];
            put(px, i, c, Math.min(1, 0.45 + v * 0.3));
          }
        }
        // The core burns through as a soft, dithered glow.
        const cr = Math.max(2, Math.round(s.gr * 0.11));
        sphere(s, px, s.gx, s.gy, cr, (nx, ny, nz, x, y) => (nz * nz + dither(x, y) * 0.5 > 0.4 ? s.core[nz > 0.75 ? 2 : nz > 0.45 ? 1 : 0] : null), 0.85);
        for (const [x, y] of knots) {
          const a = 0.6 + 0.4 * Math.sin(time * 1.3 + x);
          plot(s, px, x, y, s.knot, a);
          plot(s, px, x + 1, y, s.knot, a * 0.4);
        }
        // Companions: soft dithered ellipticals.
        for (const m of s.moons) {
          const r = Math.round(m.r);
          for (let j = -r; j <= r; j += 1) {
            for (let i = -r * 1.5; i <= r * 1.5; i += 1) {
              const d = Math.sqrt((i / 1.5) ** 2 + j * j) / (r + 0.5);
              if (d > 1) continue;
              if (1 - d + dither(m.x + i, m.y + j) * 0.5 < 0.35) continue;
              plot(s, px, m.x + i, m.y + j, s.core[d < 0.35 ? 2 : 1], (1 - d) * m.tone);
            }
          }
        }
        blit(s, false);
      },
    },

    // Eclipse: the view from low orbit. A planet's limb fills the bottom of
    // the screen, turning slowly beneath; cloud bands drift over its oceans on
    // the day side, cities glitter on the night side, and a thin atmosphere
    // glows along the curve. The sun sits just on the horizon, flaring, with
    // its reflections strung across the sky. A space station crosses now and
    // then.
    eclipse: {
      fps: 15,
      setup(s) {
        s.time = Math.random() * 100;
      },
      resize(s) {
        cells(s, Math.max(2, Math.round(4 * s.k)));
        const { cols, rows, pal } = s;
        const R = seeded(newSeed());
        const k = cosmos(pal);
        s.star = k.star;
        s.k2 = k;
        s.left = R() < 0.5;
        s.R = cols * 0.95;
        s.cx = cols * (s.left ? 0.62 : 0.38);
        const lift = Math.max(rows * 0.3, Math.min(rows * 0.42, 60));
        s.cy = rows - lift + s.R;
        s.ocean = [0.2, 0.3, 0.42, 0.56].map((l) => k.tone(212, 0.55, l));
        s.land = [0.22, 0.32, 0.42, 0.54].map((l) => k.tone(95, 0.3, l));
        s.cloud = [0.62, 0.76, 0.9].map((l) => k.tone(210, 0.12, l));
        s.city = k.tone(40, 0.9, 0.7);
        s.air = k.tone(200, 0.75, 0.66);
        s.sunC = k.tone(46, 0.9, 0.86);
        s.night = mix(pal.ink, [4, 8, 16], 0.3);
        // The sun sits on the limb near one end.
        const ang = (s.left ? -1 : 1) * 0.36 - Math.PI / 2;
        s.sx = s.cx + Math.cos(ang) * s.R;
        s.sy = s.cy + Math.sin(ang) * s.R - 1;
        s.L = [Math.sign(s.sx - s.cx) * 0.9, -0.1, -0.4];
        s.stars = starfield(R, cols, Math.max(4, Math.round(rows - lift)), 150);
        s.station = null;
        s.seed = R() * 100;
      },
      draw(s, t, dt) {
        s.time += Math.min(dt, 0.2);
        const time = s.time;
        const { cols, rows, pal, R: pr, cx, cy, L } = s;
        const px = s.img.data;
        px.fill(0);
        twinkle(s, px, s.stars, s.star, time);
        const top = Math.max(0, Math.floor(cy - pr));
        const turn = time * 0.006 + s.seed;
        for (let y = top; y < rows; y += 1) {
          const v = (y - cy) / pr;
          for (let x = 0; x < cols; x += 1) {
            const u = (x - cx) / pr;
            const d2 = u * u + v * v;
            const i = y * cols + x;
            if (d2 > 1) {
              // The atmosphere: a thin blue rim, brightest toward the sun.
              const gap = (Math.sqrt(d2) - 1) * pr;
              if (gap < 3) {
                const sun = clamp01(1 - Math.hypot(x - s.sx, y - s.sy) / (cols * 0.45));
                const a = (1 - gap / 3) * (0.25 + 0.65 * sun);
                if (a + dither(x, y) * 0.3 > 0.2) put(px, i, s.air, a);
              }
              continue;
            }
            const nz = Math.sqrt(1 - d2);
            const lit = u * L[0] + v * L[1] + nz * L[2];
            const lon = Math.atan2(u, nz) + turn;
            const lat = v;
            const landy = noise(lon * 5 + 30, lat * 5) * 0.7 + noise(lon * 13, lat * 13 + 4) * 0.3 > 0.55;
            const cl = field(lon * 7 - time * 0.01, lat * 9, time * 0.02);
            if (lit < -0.04) {
              // Night: dark ground, city lights on land.
              put(px, i, s.night, 0.7);
              // Cities: clustered regions of land, lit a pixel here and there.
              if (landy && noise(lon * 14 + 50, lat * 14) > 0.6 && hash(x, y, 3) < 0.3) {
                const f = 0.55 + 0.45 * Math.sin(time * 2 + x * 7 + y * 3);
                put(px, i, s.city, 0.5 + 0.4 * f * clamp01(-lit * 6));
              }
              continue;
            }
            const k = step4(lit * 1.6 + 0.1, x, y);
            let c = landy ? s.land[k] : s.ocean[k];
            if (cl > 0.62) c = s.cloud[Math.min(2, Math.max(0, k - 1))];
            // The terminator: a narrow band of dusk.
            if (lit < 0.08) c = mix(c, s.night, 0.5);
            put(px, i, c, 0.9);
          }
        }
        // The sun on the limb: a hot core, rays that breathe, and its
        // reflections strung across the sky toward the far corner.
        const breathe = 0.85 + 0.15 * Math.sin(time * 1.4);
        sphere(s, px, s.sx, s.sy, 2, () => s.sunC, 1);
        const ray = Math.round(Math.max(4, cols * 0.06) * breathe);
        for (let n = 3; n <= ray; n += 1) {
          const a = (1 - n / ray) * 0.85;
          for (const [dx, dy] of [[1, 0], [-1, 0], [0, -1]]) plot(s, px, s.sx + dx * n, s.sy + dy * n, s.sunC, a);
          if (n < ray * 0.5) for (const [dx, dy] of [[1, -1], [-1, -1]]) plot(s, px, s.sx + dx * n * 0.7, s.sy + dy * n * 0.7, s.sunC, a * 0.6);
        }
        const fx = cols * (s.left ? 0.9 : 0.1) - s.sx;
        const fy = rows * 0.08 - s.sy;
        for (const [f, r, tone] of [[0.3, 1, 0], [0.5, 2, 1], [0.72, 1, 2], [0.9, 3, 1]]) {
          const c = [s.air, pal.spectrum[1], s.sunC][tone];
          sphere(s, px, s.sx + fx * f, s.sy + fy * f, r, (nx, ny, nz) => (nz < 0.75 || r < 2 ? c : null), 0.35 * breathe);
        }
        // A station: a truss, two pairs of panels, a blinking light.
        if (!s.station && Math.random() < dt * 0.04) {
          const right = Math.random() < 0.5;
          s.station = { x: right ? -10 : cols + 10, y: rand(0.08, 0.3) * rows, v: (right ? 1 : -1) * cols / rand(45, 70) };
        }
        if (s.station) {
          const st = s.station;
          st.x += st.v * dt;
          if (st.x < -12 || st.x > cols + 12) s.station = null;
          else {
            const g = s.k2.grey;
            sprite(s, px, ["aa  b  aa", "aacccccaa", "aa  b  aa"], Math.round(st.x) - 4, Math.round(st.y) - 1, { a: s.air, b: g[1], c: g[2] });
            if (Math.sin(time * 5) > 0.7) plot(s, px, st.x, st.y - 2, pal.fault, 0.95);
          }
        }
        blit(s, false);
      },
    },

    // Belt: drifting through an asteroid field. Lumpy, cratered rocks tumble
    // past in three depths, lit from one side, the near ones big and quick.
    // A banded gas giant hangs behind them, and a little mining ship weaves
    // among the rocks with its engine flickering.
    belt: {
      fps: 20,
      setup(s) {
        s.time = Math.random() * 100;
      },
      resize(s) {
        cells(s, Math.max(2, Math.round(4 * s.k)));
        const { cols, rows, pal } = s;
        const R = seeded(newSeed());
        const k = cosmos(pal);
        s.star = k.star;
        s.grey = k.grey;
        s.rock = [[0.22, 0.32, 0.44, 0.58], [0.24, 0.36, 0.48, 0.62]].map((ls, n) => ls.map((l) => k.tone(n ? 24 : 34, n ? 0.18 : 0.1, l)));
        s.giant = [0.26, 0.38, 0.52, 0.68].map((l) => k.tone(k.hue + 180, k.sat * 0.6, l));
        s.giant2 = [0.22, 0.33, 0.46, 0.6].map((l) => k.tone(k.hue + 150, k.sat * 0.7, l));
        s.stars = starfield(R, cols, rows, 150);
        s.gr = Math.round(Math.min(cols, rows) * 0.34);
        s.gcx = cols * (R() < 0.5 ? 0.12 : 0.88);
        s.gcy = rows * 0.28;
        s.rocks = Array.from({ length: Math.round(Math.max(10, Math.min(46, cols * rows / 1300))) }, () => this.rock(s, R() * cols * 1.2 - cols * 0.1));
        s.ship = { x: cols * 0.3, y: rows * 0.5, tx: cols * 0.3, ty: rows * 0.5, next: 0 };
      },
      rock(s, x) {
        const z = Math.random();
        const depth = z < 0.55 ? 0 : z < 0.85 ? 1 : 2;
        const unit = Math.min(s.cols, s.rows);
        const r = Math.max(1.5, unit * [0.018, 0.035, 0.07][depth] * rand(0.7, 1.3));
        const seed = Math.random() * 100;
        // The outline and craters are laid out once, in the rock's own frame.
        const edge = Array.from({ length: 48 }, (_, n) => r * (0.78 + 0.34 * noise(Math.cos((n / 48) * TAU) * 1.4 + seed, Math.sin((n / 48) * TAU) * 1.4 + seed)));
        const span = Math.ceil(r * 1.3);
        const side = span * 2 + 1;
        const pits = new Uint8Array(side * side);
        if (r > 3) for (let j = 0; j < side; j += 1) for (let i = 0; i < side; i += 1) pits[j * side + i] = noise((i - span) * 0.45 + seed, (j - span) * 0.45) > 0.7 ? 1 : 0;
        return {
          x: x ?? s.cols + r * 2, y: rand(-0.05, 1.05) * s.rows, r, depth, v: [2.2, 5, 11][depth] * rand(0.8, 1.2) * Math.max(0.6, s.cols / 300),
          vy: rand(-0.4, 0.4), spin: rand(-0.5, 0.5), a: Math.random() * TAU, kind: Math.random() < 0.5 ? 0 : 1, edge, pits, span, side,
        };
      },
      drawRock(s, px, o) {
        const rr = Math.ceil(o.r * 1.3);
        const ca = Math.cos(o.a);
        const sa = Math.sin(o.a);
        const ramp = s.rock[o.kind];
        const fade = [0.45, 0.7, 0.95][o.depth];
        for (let j = -rr; j <= rr; j += 1) {
          for (let i = -rr; i <= rr; i += 1) {
            const d = Math.hypot(i, j);
            // Lumpy outline: radius wanders with angle in the rock's own frame.
            const ang = Math.atan2(j, i) - o.a;
            const edge = o.edge[((Math.round((ang / TAU) * 48) % 48) + 48) % 48];
            if (d > edge) continue;
            const nx = i / edge;
            const ny = j / edge;
            const nz = Math.sqrt(Math.max(0, 1 - nx * nx - ny * ny));
            let lit = -nx * 0.7 - ny * 0.45 + nz * 0.55;
            // Craters in the rock's own frame, so they turn with it.
            const lx = Math.round(i * ca + j * sa) + o.span;
            const ly = Math.round(-i * sa + j * ca) + o.span;
            if (lx >= 0 && ly >= 0 && lx < o.side && ly < o.side && o.pits[ly * o.side + lx]) lit -= 0.35;
            const x = o.x + i;
            const y = o.y + j;
            plot(s, px, x, y, ramp[step4(lit, Math.round(x), Math.round(y))], fade);
          }
        }
      },
      draw(s, t, dt) {
        s.time += Math.min(dt, 0.2);
        const time = s.time;
        const { cols, rows, pal } = s;
        const px = s.img.data;
        px.fill(0);
        twinkle(s, px, s.stars, s.star, time, 0.6);
        // The gas giant, banded, lit from the left.
        sphere(s, px, s.gcx, s.gcy, s.gr, (nx, ny, nz, x, y) => {
          const lit = -nx * 0.7 - ny * 0.35 + nz * 0.6;
          const b = Math.sin(ny * 11 + noise(nx * 3 + time * 0.03, ny * 8) * 2.4);
          return (b > 0.3 ? s.giant2 : s.giant)[step4(lit, x, y)];
        }, pal.dark ? 0.85 : 0.7);
        dt = Math.min(dt, 0.2);
        for (let d = 0; d < 3; d += 1) {
          for (let n = 0; n < s.rocks.length; n += 1) {
            const o = s.rocks[n];
            if (o.depth !== d) continue;
            o.x -= o.v * dt;
            o.y += o.vy * dt;
            o.a += o.spin * dt;
            if (o.x < -o.r * 2) s.rocks[n] = this.rock(s);
            else this.drawRock(s, px, o);
          }
          // The ship flies between the middle and near rocks.
          if (d === 1) {
            const sh = s.ship;
            if (time > sh.next) {
              sh.next = time + rand(2, 5);
              sh.tx = cols * rand(0.05, 0.95);
              sh.ty = rows * rand(0.1, 0.9);
            }
            sh.x += (sh.tx - sh.x) * Math.min(1, dt * 0.8);
            sh.y += (sh.ty - sh.y) * Math.min(1, dt * 0.8);
            const left = sh.tx < sh.x;
            const g = s.grey;
            sprite(s, px, [" aa   ", "abbbbc", "aaddda", " aa   "], Math.round(sh.x) - 3, Math.round(sh.y) - 2, { a: g[1], b: g[2], c: pal.tint, d: g[0] }, left);
            const flame = 1 + (Math.floor(time * 12) % 3);
            for (let f = 1; f <= flame; f += 1) plot(s, px, sh.x + (left ? 2 + f : -3 - f), sh.y, f === 1 ? pal.tint : pal.brass, 1 - f * 0.25);
          }
        }
        blit(s, false);
      },
    },

    // Contact: a night on an alien world. Glowing stalks and spotted caps
    // grow from violet ground under two moons, and little antennaed locals
    // wander about. A mothership hovers over them with its rim lights running
    // through chases, alternations and strobes, a pilot in the dome, and now
    // and then a tractor beam that pours down and lifts sparks off the ground.
    // Two scout saucers dart about the sky.
    contact: {
      fps: 20,
      setup(s) {
        s.time = Math.random() * 100;
      },
      resize(s) {
        cells(s, Math.max(2, Math.round(4 * s.k)));
        const { cols, rows, pal } = s;
        const R = seeded(newSeed());
        const k = cosmos(pal);
        s.kit = k;
        s.star = k.star;
        s.soil = [0.14, 0.22, 0.32].map((l) => k.tone(276, 0.4, l));
        s.stem = [0.24, 0.34, 0.46].map((l) => k.tone(250, 0.35, l));
        s.glowA = k.tone(168, 0.85, 0.66);
        s.glowB = k.tone(312, 0.8, 0.7);
        s.glowC = k.tone(88, 0.85, 0.66);
        s.hull = [0.2, 0.32, 0.46, 0.64].map((l) => k.tone(228, 0.14, l));
        s.glass = k.tone(180, 0.5, 0.5);
        s.beam = k.tone(165, 0.8, 0.72);
        s.lamps = [pal.fault, k.tone(44, 0.9, 0.66), s.glowA, k.tone(210, 0.85, 0.7), s.glowB, k.tone(120, 0.7, 0.62)];
        s.gh = Math.max(4, Math.round(rows * 0.15));
        s.gy = rows - s.gh;
        s.gtop = Array.from({ length: cols }, (_, x) => Math.round(s.gy - 2.5 * noise(x * 0.025, 7.1) - 1.5 * noise(x * 0.09, 2.2)));
        s.base = new Uint8ClampedArray(s.img.data.length);
        for (let x = 0; x < cols; x += 1) {
          for (let y = s.gtop[x]; y < rows; y += 1) {
            const d = y - s.gtop[x];
            put(s.base, y * cols + x, s.soil[d === 0 ? 2 : d < 3 && dither(x, y) > 0 ? 1 : 0], d === 0 ? 0.8 : 0.65);
          }
        }
        s.stars = starfield(R, cols, s.gy, 140);
        const unit = Math.min(cols, rows);
        s.home = cols * (R() < 0.5 ? 0.24 : 0.76);
        const far = (u) => (s.home < cols / 2 ? 1 - u : u);
        s.moons = [
          { x: cols * far(R.range(0.06, 0.16)), y: rows * 0.14, r: Math.max(3, Math.round(unit * 0.09)), ramp: [0.3, 0.44, 0.58, 0.72].map((l) => k.tone(22, 0.5, l)) },
          { x: cols * far(R.range(0.3, 0.4)), y: rows * 0.08, r: Math.max(2, Math.round(unit * 0.04)), ramp: [0.3, 0.44, 0.58, 0.72].map((l) => k.tone(170, 0.4, l)) },
        ];
        // Plants gather toward the edges.
        s.plants = [];
        for (let i = 0, n = Math.round(cols / 7); i < n; i += 1) {
          const edge = Math.pow(R(), 1.5) * 0.4;
          const x = Math.floor((R() < 0.5 ? edge : 1 - edge) * cols);
          const cap = R() < 0.4;
          s.plants.push({ x, cap, h: cap ? Math.round(R.range(2, 4) * Math.max(0.7, unit / 180)) : Math.round(R.range(0.04, 0.12) * unit), glow: R.pick([s.glowA, s.glowB, s.glowC]), ph: R() * TAU, w: cap ? Math.round(R.range(2, 4)) : 0 });
        }
        s.W = Math.round(Math.max(8, Math.min(cols * 0.17, 64)));
        s.hy = Math.max(s.W * 0.5 + 2, rows * 0.3);
        s.critters = Array.from({ length: Math.max(2, Math.min(6, Math.round(cols / 70))) }, () => ({ x: R() * cols, dir: R.sign(), v: R.range(1.5, 3.5), wait: 0, hue: R.pick([s.glowC, s.glowA, s.glowB]) }));
        s.scouts = [0, 1].map((i) => ({ x: cols * (i ? 0.7 : 0.3), y: rows * 0.15, tx: 0, ty: 0, next: 0 }));
        s.motes = [];
        s.mode = 0;
        s.modeAt = 0;
      },
      // An ellipse of hull, shaded from above, optionally only its top or
      // bottom half.
      hullPart(s, px, cx, cy, rx, ry, half) {
        for (let j = -Math.ceil(ry); j <= Math.ceil(ry); j += 1) {
          if ((half < 0 && j > 0) || (half > 0 && j < 0)) continue;
          for (let i = -Math.ceil(rx); i <= Math.ceil(rx); i += 1) {
            const nx = i / (rx + 0.5);
            const ny = j / (ry + 0.5);
            if (nx * nx + ny * ny > 1) continue;
            const x = Math.round(cx + i);
            const y = Math.round(cy + j);
            const rim = nx * nx + ny * ny > 0.8 && ny > 0;
            plot(s, px, x, y, s.hull[rim ? 0 : step4(0.55 - nx * 0.35 - ny * 0.5, x, y)], 0.95);
          }
        }
      },
      draw(s, t, dt) {
        s.time += Math.min(dt, 0.2);
        dt = Math.min(dt, 0.2);
        const time = s.time;
        const { cols, rows, pal, W } = s;
        const px = s.img.data;
        px.set(s.base);
        twinkle(s, px, s.stars, s.star, time, 0.12);
        for (const m of s.moons) sphere(s, px, m.x, m.y, m.r, (nx, ny, nz, x, y) => m.ramp[step4(-nx * 0.8 - ny * 0.3 + nz * 0.45 + (noise(nx * 4 + 9, ny * 4) > 0.66 ? -0.3 : 0), x, y)], 0.9);
        meteors(s, px, dt, s.gy * 0.7, pal.tint, s.star, 0.06);
        // Plants: glowing bulbs on swaying stalks, and spotted caps.
        for (const p of s.plants) {
          const top = s.gtop[Math.min(cols - 1, Math.max(0, p.x))];
          const pulse = 0.55 + 0.45 * Math.sin(time * 1.2 + p.ph);
          if (p.cap) {
            for (let k = 0; k < p.h; k += 1) plot(s, px, p.x, top - k, s.stem[2]);
            const cy = top - p.h;
            for (let i = -p.w; i <= p.w; i += 1) {
              plot(s, px, p.x + i, cy, s.stem[1]);
              if (Math.abs(i) < p.w) plot(s, px, p.x + i, cy - 1, s.stem[2]);
              if (Math.abs(i) < p.w - 1) plot(s, px, p.x + i, cy - 2, s.stem[2]);
            }
            for (let i = -p.w + 1; i < p.w; i += 2) plot(s, px, p.x + i, cy - 1, p.glow, pulse);
          } else {
            const lean = gust(p.x / cols, time) * 0.18 * p.h;
            let tx = p.x;
            for (let k = 0; k < p.h; k += 1) {
              const f = k / p.h;
              tx = p.x + Math.round(lean * f * f);
              plot(s, px, tx, top - k, s.stem[k > p.h * 0.7 ? 1 : 0]);
            }
            const by = top - p.h;
            plot(s, px, tx, by, p.glow, 1);
            plot(s, px, tx, by - 1, p.glow, 0.8 + 0.2 * pulse);
            for (const [dx, dy] of [[1, 0], [-1, 0], [1, -1], [-1, -1], [0, -2], [0, 1]]) plot(s, px, tx + dx, by + dy, p.glow, 0.25 + 0.4 * pulse);
          }
        }
        // The mothership drifts about its spot and bobs.
        const mx = s.home + Math.sin(time * 0.05) * cols * 0.04;
        const my = s.hy + Math.sin(time * 0.7) * 0.8;
        // Beam cycle: off, pour down, hold, draw back up.
        const cyc = (time % 24) / 24;
        const beam = cyc < 0.55 ? 0 : cyc < 0.6 ? (cyc - 0.55) / 0.05 : cyc < 0.92 ? 1 : 1 - (cyc - 0.92) / 0.08;
        const b0 = my + W * 0.2;
        const ground = s.gtop[Math.max(0, Math.min(cols - 1, Math.round(mx)))];
        if (beam > 0) {
          const reach = b0 + (ground - b0) * Math.min(1, beam * 1.2);
          for (let y = Math.round(b0); y <= reach; y += 1) {
            const f = (y - b0) / Math.max(1, ground - b0);
            const half = W * (0.2 + 0.3 * f);
            const shimmer = 0.16 + 0.1 * Math.sin(y * 0.7 - time * 7);
            for (let i = -Math.round(half); i <= half; i += 1) {
              const x = Math.round(mx + i);
              const edge = Math.abs(Math.abs(i) - half) < 1;
              const a = (edge ? 0.45 : shimmer) * beam;
              if (!edge && dither(x, y) + 0.5 > a * 4) continue;
              plot(s, px, x, y, s.beam, a + 0.1);
            }
          }
          if (beam > 0.9 && Math.random() < dt * 14) s.motes.push({ x: mx + rand(-0.45, 0.45) * W, y: ground - 1, v: rand(4, 9), ph: Math.random() * TAU });
          // The ground glows where the beam lands.
          for (let i = -Math.round(W * 0.6); i <= W * 0.6; i += 1) {
            const x = Math.round(mx + i);
            const g = s.gtop[Math.max(0, Math.min(cols - 1, x))];
            plot(s, px, x, g, s.beam, beam * 0.7 * (1 - Math.abs(i) / (W * 0.6)));
          }
        }
        s.motes = s.motes.filter((m) => {
          m.y -= m.v * dt;
          m.x += Math.sin(time * 3 + m.ph) * dt * 2 + (mx - m.x) * dt * 0.3;
          if (m.y < b0 + 1) return false;
          plot(s, px, m.x, m.y, Math.sin(time * 9 + m.ph) > 0 ? pal.tint : s.beam, 0.9 * Math.max(0.2, beam));
          return true;
        });
        // Hull: underside, disc, upper deck, then the glass dome with its pilot.
        this.hullPart(s, px, mx, my + W * 0.05, W * 0.5, W * 0.14, 1);
        this.hullPart(s, px, mx, my, W, Math.max(1.5, W * 0.1), 0);
        this.hullPart(s, px, mx, my - W * 0.05, W * 0.62, W * 0.16, -1);
        const rd = Math.max(2, W * 0.27);
        const dcy = my - W * 0.17;
        for (let j = -Math.ceil(rd); j <= 0; j += 1) {
          for (let i = -Math.ceil(rd); i <= rd; i += 1) {
            const d = Math.hypot(i, j) / (rd + 0.4);
            if (d > 1) continue;
            const x = Math.round(mx + i);
            const y = Math.round(dcy + j);
            const shine = i < -rd * 0.2 && j < -rd * 0.4 && d > 0.55 && d < 0.8;
            plot(s, px, x, y, shine ? pal.tint : s.glass, shine ? 0.9 : 0.55);
          }
        }
        // The pilot: a big head, two dark eyes, and a slow blink.
        if (rd >= 4) {
          const hr = rd * 0.45;
          const hx = mx + Math.sin(time * 0.3) * rd * 0.15;
          const hy = dcy - rd * 0.35;
          for (let j = -Math.ceil(hr); j <= Math.ceil(hr * 1.3); j += 1) {
            for (let i = -Math.ceil(hr); i <= hr; i += 1) {
              const w = hr * (j < 0 ? 1 : 1 - j / (hr * 1.6));
              if (Math.abs(i) > w || Math.hypot(i, Math.min(0, j)) > hr + 0.3) continue;
              plot(s, px, hx + i, hy + j, s.glowC, 0.9);
            }
          }
          if ((time % 5) > 0.2) {
            const ex = Math.max(1, Math.round(hr * 0.45));
            for (const side of [-1, 1]) {
              plot(s, px, hx + side * ex, hy + 1, pal.ink, 1);
              plot(s, px, hx + side * ex + side, hy + 1, pal.ink, 1);
            }
          }
        }
        // Portholes along the upper deck, warm and flickering.
        for (let i = -W * 0.5, n = 0; i <= W * 0.5; i += Math.max(3, W / 8), n += 1) {
          const on = hash(n, Math.floor(time * 0.7 + n * 0.3), 4) > 0.2;
          plot(s, px, mx + i, my - W * 0.06, on ? s.lamps[1] : s.hull[0], on ? 0.85 : 0.9);
        }
        // Rim lights: the pattern changes every few seconds.
        if (time > s.modeAt) {
          s.mode = (s.mode + 1 + Math.floor(Math.random() * 3)) % 4;
          s.modeAt = time + rand(5, 9);
        }
        const N = Math.max(7, Math.min(17, Math.round(W / 3.5) | 1));
        const tick = Math.floor(time * 9);
        for (let n = 0; n < N; n += 1) {
          const u = (n / (N - 1)) * 2 - 1;
          const x = mx + u * W * 0.9;
          const y = my + Math.max(1, W * 0.1) * 0.5 * Math.sqrt(1 - u * u * 0.8);
          let on;
          if (s.mode === 0) on = (tick - n + N * 4) % 5 === 0 || (tick - n + N * 4) % 5 === 1;
          else if (s.mode === 1) on = (n + Math.floor(time * 3)) % 2 === 0;
          else if (s.mode === 2) on = Math.floor(time * 7) % 4 < 2 && Math.floor(time / 1.1) % 2 === 0;
          else on = Math.abs(u) < ((time * 1.3) % 1.4);
          const c = s.lamps[s.mode === 2 ? 0 : (n + (s.mode === 3 ? 0 : Math.floor(time * 0.5))) % s.lamps.length];
          plot(s, px, x, y, on ? c : mix(c, s.hull[0], 0.6), on ? 1 : 0.8);
          if (on) for (const [dx, dy] of [[1, 0], [-1, 0], [0, 1], [0, -1]]) plot(s, px, x + dx, y + dy, c, 0.4);
        }
        // Underside ports glow brighter while the beam runs.
        for (const f of [-0.3, 0, 0.3]) {
          const a = 0.4 + 0.3 * Math.sin(time * 2 + f * 9) + beam * 0.3;
          plot(s, px, mx + f * W, my + W * 0.16, s.beam, Math.min(1, a));
        }
        // Scouts dart about the upper sky.
        for (const sc of s.scouts) {
          if (time > sc.next) {
            sc.next = time + rand(2, 6);
            sc.tx = cols * rand(0.05, 0.95);
            sc.ty = rows * rand(0.05, 0.35);
          }
          sc.x += (sc.tx - sc.x) * Math.min(1, dt * 2);
          sc.y += (sc.ty - sc.y) * Math.min(1, dt * 2);
          const bl = Math.floor(time * 6) % 2;
          sprite(s, px, [" bb ", "aaaa", "c  d"], Math.round(sc.x) - 2, Math.round(sc.y) - 1, { a: s.hull[3], b: s.glass, c: bl ? s.lamps[0] : s.hull[0], d: bl ? s.hull[0] : s.lamps[2] });
        }
        // The locals wander, stop to look around, and glow at the antenna.
        for (const c of s.critters) {
          if (c.wait > 0) c.wait -= dt;
          else {
            c.x += c.dir * c.v * dt;
            if (Math.random() < dt * 0.15) c.wait = rand(1, 4);
            if (Math.random() < dt * 0.08 || c.x < 2 || c.x > cols - 3) c.dir = c.x < 2 ? 1 : c.x > cols - 3 ? -1 : -c.dir;
          }
          const x = Math.round(c.x);
          const g = s.gtop[Math.max(0, Math.min(cols - 1, x))];
          const step = c.wait > 0 ? 0 : Math.floor(time * 5) & 1;
          const legs = step ? " a a " : "a   a";
          sprite(s, px, ["b   b", " a a ", " aaa ", "acaca", " aaa ", legs], x - 2, g - 6, { a: c.hue, b: pal.tint, c: pal.ink });
        }
        blit(s, false);
      },
    },

    // Koi: a pond from above. Koi in kohaku, showa, ogon and asagi patterns
    // wind slowly through dappled water, flexing as they turn and sinking
    // and rising as they go, with their shadows on the bed below. Lily pads,
    // a few in flower, float on top; light plays over the water, rings open
    // where a fish mouths the surface, and now and then a petal drifts past.
    koi: {
      fps: 12,
      setup(s) {
        s.time = Math.random() * 100;
      },
      resize(s) {
        cells(s, Math.max(2, Math.round(4 * s.k)));
        const { cols, rows, pal } = s;
        const R = seeded(newSeed());
        const fl = flora(pal);
        s.fl = fl;
        const dark = pal.dark;
        s.deep = dark ? mix(fl.sky[0], pal.ink, 0.62) : mix(fl.sky[1], pal.ink, 0.72);
        s.shallow = dark ? mix(fl.moss[0], pal.ink, 0.45) : mix(fl.moss[1], pal.ink, 0.6);
        s.shine = mix(fl.sky[2], pal.tint, 0.3);
        s.shade = mix(pal.ink, [0, 0, 0], dark ? 0.6 : 0.25);
        s.base = new Uint8ClampedArray(s.img.data.length);
        const off = R() * 100;
        for (let y = 0; y < rows; y += 1) {
          for (let x = 0; x < cols; x += 1) {
            const d = noise(x * 0.03 + off, y * 0.03) + dither(x, y) * 0.12;
            put(s.base, y * cols + x, d > 0.52 ? s.deep : s.shallow, 0.42);
          }
        }
        const unit = Math.min(cols, rows);
        s.pads = [];
        for (let i = 0, n = Math.max(4, Math.round(cols * rows / 2600)); i < n; i += 1) {
          const x = R() * cols;
          if (Math.abs(x / cols - 0.5) < 0.2 && R() < 0.7) continue;
          s.pads.push({ x, y: R() * rows, r: Math.max(2, Math.round(R.range(0.035, 0.07) * unit)), notch: R() * TAU, flower: R() < 0.3, ph: R() * TAU });
        }
        const kinds = ["kohaku", "showa", "ogon", "asagi", "kohaku"];
        s.fish = Array.from({ length: Math.max(3, Math.min(7, Math.round(cols / 55))) }, (_, i) => {
          const L = Math.max(6, Math.round(unit * R.range(0.11, 0.17)));
          const f = { x: R() * cols, y: R() * rows, a: R() * TAU, L, v: R.range(2, 4) * Math.max(0.6, unit / 180), seed: R() * 100, kind: kinds[i % kinds.length], trail: [] };
          for (let n = 0; n < 120; n += 1) f.trail.push([f.x - Math.cos(f.a) * n * 0.3, f.y - Math.sin(f.a) * n * 0.3]);
          return f;
        });
        s.rings = [];
        s.petals = [];
      },
      // The colour of a koi at u along its body (0 head, 1 tail).
      skin(s, f, u, side) {
        const fl = s.fl;
        const white = fl.white[2];
        const red = fl.poppy[1];
        const patch = noise(u * 5 + f.seed, side * 0.8 + f.seed) > 0.52;
        if (f.kind === "ogon") return u < 0.08 ? fl.gold[2] : fl.gold[1];
        if (f.kind === "asagi") return side > 0.6 ? fl.poppy[2] : mix(fl.sky[1], white, 0.35);
        if (f.kind === "showa") return noise(u * 6 + f.seed + 20, side) > 0.6 ? mix(s.pal.ink, [20, 20, 24], 0.5) : patch ? red : white;
        return patch && u > 0.06 ? red : white;
      },
      spine(f) {
        // Walk back along the head's trail at even steps.
        const pts = [[f.x, f.y]];
        const seg = f.L / 12;
        let want = seg;
        let dist = 0;
        for (let i = 1; i < f.trail.length && pts.length < 13; i += 1) {
          const [ax, ay] = f.trail[i - 1];
          const [bx, by] = f.trail[i];
          const d = Math.hypot(bx - ax, by - ay);
          while (dist + d >= want && pts.length < 13) {
            const k = (want - dist) / (d || 1);
            pts.push([ax + (bx - ax) * k, ay + (by - ay) * k]);
            want += seg;
          }
          dist += d;
        }
        while (pts.length < 13) pts.push(pts[pts.length - 1]);
        return pts;
      },
      drawFish(s, px, f, time, shadow) {
        const pts = this.spine(f);
        const depth = 0.5 + 0.5 * noise(f.seed, time * 0.05);
        const off = shadow ? 1 + Math.round(depth * 2) : 0;
        const W = Math.max(1, f.L * 0.13);
        for (let n = 0; n < pts.length - 1; n += 1) {
          const u = n / (pts.length - 1);
          const [x0, y0] = pts[n];
          const [x1, y1] = pts[n + 1];
          const w = W * (u < 0.3 ? 0.7 + u : Math.max(0.25, (1 - u) * 1.45));
          const nx = -(y1 - y0);
          const ny = x1 - x0;
          const nl = Math.hypot(nx, ny) || 1;
          for (let sub = 0; sub < 2; sub += 1) {
            const cx = x0 + (x1 - x0) * sub * 0.5;
            const cy = y0 + (y1 - y0) * sub * 0.5;
            for (let k = -Math.ceil(w); k <= Math.ceil(w); k += 1) {
              if (Math.abs(k) > w + 0.2) continue;
              const x = cx + (nx / nl) * k + off;
              const y = cy + (ny / nl) * k + off;
              if (shadow) plot(s, px, x, y, s.shade, 0.35);
              else plot(s, px, x, y, mix(this.skin(s, f, u, Math.abs(k) / (w + 0.5)), s.deep, 0.5 - depth * 0.45), 0.55 + depth * 0.4);
            }
          }
          // Pectoral fins just behind the head.
          if (n === 2 && !shadow) {
            const flap = 1 + Math.round(Math.sin(time * 4 + f.seed));
            for (const side of [-1, 1]) plot(s, px, x0 + (nx / nl) * side * (w + flap), y0 + (ny / nl) * side * (w + flap), s.fl.white[1], 0.5 * depth + 0.2);
          }
        }
        // The tail fans and wags.
        const [tx, ty] = pts[pts.length - 1];
        const [ux, uy] = pts[pts.length - 2];
        const back = Math.atan2(ty - uy, tx - ux);
        const wag = Math.sin(time * 5 + f.seed) * 0.35;
        for (const side of [-0.5, 0.5]) {
          for (let k = 1; k <= Math.max(2, W * 1.6); k += 1) {
            const a = back + side + wag;
            const x = tx + Math.cos(a) * k + off;
            const y = ty + Math.sin(a) * k + off;
            if (shadow) plot(s, px, x, y, s.shade, 0.25);
            else plot(s, px, x, y, mix(this.skin(s, f, 1, 0), s.deep, 0.4), 0.45 + depth * 0.3);
          }
        }
        if (!shadow && depth > 0.9 && Math.random() < 0.02) s.rings.push({ x: f.x, y: f.y, age: 0 });
      },
      draw(s, t, dt) {
        s.time += Math.min(dt, 0.2);
        dt = Math.min(dt, 0.2);
        const time = s.time;
        const { cols, rows, fl } = s;
        const px = s.img.data;
        px.set(s.base);
        // Light playing over the water, sampled every third cell.
        for (let y = 0; y < rows; y += 3) {
          for (let x = (y / 3) & 1; x < cols; x += 3) {
            const v = Math.abs(noise(x * 0.07 + time * 0.18, y * 0.07) - noise(x * 0.07 - time * 0.13 + 5, y * 0.07 + time * 0.09));
            if (v < 0.035) {
              const a = 0.3 * (1 - v / 0.035);
              plot(s, px, x, y, s.shine, a);
              plot(s, px, x + 1, y, s.shine, a * 0.7);
            }
          }
        }
        // Fish swim: wander, turn back from the edges, favour the margins.
        for (const f of s.fish) {
          let turn = (noise(f.seed, time * 0.12) - 0.5) * 2.2;
          const m = f.L * 0.8;
          const want = (tx, ty) => {
            const d = Math.atan2(ty - f.y, tx - f.x) - f.a;
            return Math.atan2(Math.sin(d), Math.cos(d));
          };
          if (f.x < m || f.x > cols - m || f.y < m || f.y > rows - m) turn += want(cols / 2, rows / 2) * 1.5;
          f.a += turn * dt;
          const v = f.v * (0.7 + 0.6 * noise(f.seed + 7, time * 0.2));
          f.x += Math.cos(f.a) * v * dt;
          f.y += Math.sin(f.a) * v * dt;
          f.trail.unshift([f.x, f.y]);
          if (f.trail.length > 160) f.trail.pop();
        }
        for (const f of s.fish) this.drawFish(s, px, f, time, true);
        for (const f of s.fish) this.drawFish(s, px, f, time, false);
        // Rings where something breaks the surface.
        if (Math.random() < dt * 0.25) s.rings.push({ x: rand(0, 1) * cols, y: rand(0, 1) * rows, age: 0 });
        s.rings = s.rings.filter((r) => {
          r.age += dt;
          const rad = 1 + r.age * 3.2;
          const a = 0.55 * (1 - r.age / 2.4);
          if (a <= 0) return false;
          for (let n = 0, m = Math.max(10, Math.round(rad * 7)); n < m; n += 1) {
            const ang = (n / m) * TAU;
            plot(s, px, r.x + Math.cos(ang) * rad, r.y + Math.sin(ang) * rad, s.shine, a);
          }
          return true;
        });
        // Lily pads, seen from above: a disc with a notch, a shadow, veins.
        for (const p of s.pads) {
          const x0 = p.x + Math.sin(time * 0.2 + p.ph) * 0.8;
          const y0 = p.y + Math.cos(time * 0.17 + p.ph) * 0.6;
          const rot = p.notch + Math.sin(time * 0.1 + p.ph) * 0.15;
          for (const shadow of [true, false]) {
            const o = shadow ? 1 : 0;
            for (let j = -p.r; j <= p.r; j += 1) {
              for (let i = -p.r; i <= p.r; i += 1) {
                const d = Math.hypot(i, j);
                if (d > p.r + 0.3) continue;
                const a = Math.atan2(j, i) - rot;
                if (Math.abs(Math.atan2(Math.sin(a), Math.cos(a))) < 0.32 && d > 0.6) continue;
                const x = Math.round(x0 + i) + o;
                const y = Math.round(y0 + j) + o;
                if (shadow) { plot(s, px, x, y, s.shade, 0.3); continue; }
                const vein = p.r > 3 && Math.abs(Math.sin(a * 4)) < 0.18;
                const c = d > p.r - 0.7 ? fl.leaf[0] : vein ? fl.leaf[2] : dither(x, y) > 0 ? fl.leaf[1] : mix(fl.leaf[1], fl.leaf[2], 0.4);
                plot(s, px, x, y, c, 0.92);
              }
            }
          }
          if (p.flower) {
            const cx = x0 - Math.cos(rot) * p.r * 0.3;
            const cy = y0 - Math.sin(rot) * p.r * 0.3;
            const pr = Math.max(1.5, p.r * 0.55);
            for (let n = 0; n < 8; n += 1) {
              const a = (n / 8) * TAU + time * 0.02;
              for (let k = 1; k <= pr; k += 1) plot(s, px, cx + Math.cos(a) * k, cy + Math.sin(a) * k, fl.blossom[k > pr * 0.6 ? 3 : 1], 0.95);
            }
            plot(s, px, cx, cy, fl.gold[2], 1);
          }
        }
        // A petal drifts across now and then, turning as it goes.
        if (Math.random() < dt * 0.08) s.petals.push({ x: -2, y: rand(0.05, 0.95) * rows, v: rand(1.5, 3), ph: Math.random() * TAU, age: 0 });
        s.petals = s.petals.filter((p) => {
          p.age += dt;
          p.x += p.v * dt;
          p.y += Math.sin(time * 0.5 + p.ph) * dt * 0.6;
          if (p.x > cols + 2) return false;
          const flip = Math.sin(time * 1.3 + p.ph) > 0;
          plot(s, px, p.x, p.y, fl.blossom[2], 0.9);
          plot(s, px, p.x + (flip ? 1 : 0), p.y + (flip ? 0 : 1), fl.blossom[3], 0.8);
          return true;
        });
        blit(s, false);
      },
    },

    // Signal: a quiet bench of data lanes. Hex dumps scroll up the margins at
    // their own speeds, packets run up them as short bright trails, and a
    // refresh bar sweeps the screen, lighting whatever it crosses. The glyphs
    // bloom softly on dark themes, and now and then one band of the picture
    // slips sideways for a moment, like a tube catching its sync.
    signal: {
      fps: 12,
      previewK: 0.6,
      chars: "0123456789ABCDEF:./_-",
      setup(s) {
        s.cw = 9 * s.k;
        s.ch = 15 * s.k;
        s.clock = Math.random() * 100;
      },
      resize(s) {
        s.cols = Math.ceil(s.w / s.cw);
        s.rows = Math.ceil(s.h / s.ch);
        s.A = atlas(s, this.chars, tones(s.pal), s.cw, s.ch, 12 * s.k);
        // Glyphs are drawn to their own layer, then composited with a bloom.
        const mk = (w, h) => {
          const c = document.createElement("canvas");
          c.width = Math.max(1, Math.round(w));
          c.height = Math.max(1, Math.round(h));
          return c;
        };
        s.layer = mk(s.w * s.dpr, s.h * s.dpr);
        s.lctx = s.layer.getContext("2d");
        s.lctx.setTransform(s.dpr, 0, 0, s.dpr, 0, 0);
        s.glow = mk(s.w / 4, s.h / 4);
        s.gctx = s.glow.getContext("2d");
        // Lanes crowd the margins and thin out through the middle.
        const R = seeded(newSeed());
        s.lanes = [];
        for (let c = 1; c < s.cols - 4;) {
          const u = Math.abs(c / s.cols - 0.5) * 2;
          const width = Math.round(R.range(8, 13));
          if (R() < 0.12 + 0.88 * u * u) {
            s.lanes.push({ c, width, v: R.range(0.8, 4.2), gain: 0.45 + 0.55 * u, id: Math.floor(R() * 1e6), pv: R.range(3, 9), po: R() * 200, gap: R() * 0.2 });
            c += width + Math.round(R.range(2, 5));
          } else c += 2;
        }
        s.glitch = { at: s.clock + rand(14, 30), until: 0, y: 0, h: 0, dx: 0 };
      },
      draw(s, t, dt) {
        s.clock += Math.min(dt, 0.2);
        const now = s.clock;
        const { ctx, lctx, cols, rows, cw, ch, A, pal } = s;
        lctx.clearRect(0, 0, s.w, s.h);
        // The refresh bar, which crosses a little slower than it looks.
        const span = rows + 24;
        const bar = ((now * 2.6) % span) - 12;
        for (const lane of s.lanes) {
          const shift = Math.floor(now * lane.v);
          const head = rows + 10 - ((now * lane.pv + lane.po) % (rows + 40));
          for (let r = 0; r < rows; r += 1) {
            const row = r + shift;
            if (hash(lane.id, row, 1) < 0.14 + lane.gap) continue;
            const len = 3 + Math.floor(hash(lane.id, row, 2) * (lane.width - 2));
            const behind = r - head;
            const packet = behind > -1 && behind < 7 ? 1 - behind / 7 : 0;
            const sweep = Math.exp(-((r - bar) * (r - bar)) / 5);
            for (let i = 0; i < len; i += 1) {
              const c = lane.c + i;
              if (c >= cols) break;
              let b = 0.16 + 0.2 * hash(lane.id, row, 3 + i);
              if (i < 4) b *= 0.7;
              b = (b + packet * 0.75 + sweep * 0.55) * lane.gain * 1.25;
              if (b < 0.1) continue;
              const tone = Math.min(4, Math.floor(b * 5));
              // A few cells keep changing; the rest hold until they scroll off.
              const z = hash(c, row, 9) < 0.06 ? Math.floor(now * 2 + hash(c, row, 5) * 9) : 0;
              const g = Math.floor(hash(lane.id + i * 13, row, 7 + z) * (i < 4 ? 16 : A.n - 3)) % A.n;
              stamp(lctx, A, g, packet > 0.9 && i === 0 ? 4 : tone, c * cw, r * ch);
            }
          }
        }
        // The bar itself: a faint band of light on the tube.
        const by = bar * ch;
        if (by > -ch * 4 && by < s.h + ch * 4) {
          const grad = lctx.createLinearGradient(0, by - ch * 2, 0, by + ch * 2);
          grad.addColorStop(0, css(pal.steel, 0));
          grad.addColorStop(0.5, css(pal.steel, pal.dark ? 0.1 : 0.07));
          grad.addColorStop(1, css(pal.steel, 0));
          lctx.fillStyle = grad;
          lctx.fillRect(0, by - ch * 2, s.w, ch * 4);
        }
        ctx.clearRect(0, 0, s.w, s.h);
        ctx.drawImage(s.layer, 0, 0, s.w, s.h);
        if (pal.dark) {
          s.gctx.clearRect(0, 0, s.glow.width, s.glow.height);
          s.gctx.drawImage(s.layer, 0, 0, s.glow.width, s.glow.height);
          ctx.globalCompositeOperation = "lighter";
          ctx.globalAlpha = 0.9;
          ctx.drawImage(s.glow, 0, 0, s.w, s.h);
          ctx.globalAlpha = 1;
          ctx.globalCompositeOperation = "source-over";
        }
        // A band of the picture slips sideways for a few frames.
        const gl = s.glitch;
        if (now > gl.at && now > gl.until) {
          gl.until = now + rand(0.12, 0.3);
          gl.at = gl.until + rand(16, 40);
          gl.h = Math.round(rand(0.03, 0.09) * s.h);
          gl.y = Math.round(rand(0.05, 0.9) * (s.h - gl.h));
          gl.dx = Math.round(rand(6, 20) * s.k) * (Math.random() < 0.5 ? -1 : 1);
        }
        if (now < gl.until) {
          const d = s.dpr;
          ctx.clearRect(0, gl.y, s.w, gl.h);
          ctx.drawImage(s.layer, 0, gl.y * d, s.layer.width, gl.h * d, gl.dx, gl.y, s.w, gl.h);
        }
      },
    },
  };

  function makeState(ctx, k) {
    return { ctx, pal: palette(), dpr: 0, w: 0, h: 0, k };
  }

  // —— Runner ——
  const run = { kind: "", canvas: null, ctx: null, raf: 0, last: 0, acc: 0, t: 0, state: null, listening: false };

  function stop() {
    if (run.raf) cancelAnimationFrame(run.raf);
    run.raf = 0;
    run.last = 0;
  }

  function measure() {
    const s = run.state;
    if (!s || !run.canvas) return false;
    const dpr = Math.min(window.devicePixelRatio || 1, 1.5);
    const w = run.canvas.clientWidth || window.innerWidth;
    const h = run.canvas.clientHeight || window.innerHeight;
    if (w < 2 || h < 2) return false;
    const bw = Math.round(w * dpr);
    const bh = Math.round(h * dpr);
    if (run.canvas.width !== bw || run.canvas.height !== bh || s.dpr !== dpr) {
      run.canvas.width = bw;
      run.canvas.height = bh;
      run.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      Object.assign(s, { w, h, dpr });
      SCENES[run.kind].resize(s);
    }
    return true;
  }

  function frame(now) {
    run.raf = 0;
    const engine = SCENES[run.kind];
    if (!engine || !run.state) return;
    const sec = now / 1000;
    const dt = run.last ? Math.min(0.25, sec - run.last) : 0;
    run.last = sec;
    run.acc += dt;
    const budget = 1 / engine.fps;
    if (run.acc >= budget || !run.t) {
      const step = Math.max(run.acc, budget);
      run.acc = 0;
      run.t += step;
      if (measure()) engine.draw(run.state, run.t, step);
    }
    if (!document.hidden) run.raf = requestAnimationFrame(frame);
  }

  function start() {
    stop();
    const engine = SCENES[run.kind];
    if (!engine || !run.canvas) return;
    run.state = makeState(run.ctx, 1);
    engine.setup?.(run.state);
    // Start mid-flight so the first frame already looks settled.
    run.t = 40 + Math.random() * 60;
    if (!measure()) return;
    // Reduced motion holds one frame, so it should be a finished one.
    engine.warm?.(run.state, run.t, reduce.matches);
    engine.draw(run.state, run.t, 1 / engine.fps);
    if (reduce.matches) return;
    if (!document.hidden) run.raf = requestAnimationFrame(frame);
  }

  function listen() {
    if (run.listening) return;
    run.listening = true;
    document.addEventListener("visibilitychange", () => {
      if (document.hidden) stop();
      else if (SCENES[run.kind] && run.state && !reduce.matches && !run.raf) run.raf = requestAnimationFrame(frame);
    });
    let resizeFrame = 0;
    window.addEventListener("resize", () => {
      if (resizeFrame || !run.state) return;
      resizeFrame = requestAnimationFrame(() => {
        resizeFrame = 0;
        if (measure() && reduce.matches && SCENES[run.kind]) SCENES[run.kind].draw(run.state, run.t, 0.1);
      });
    });
    reduce.addEventListener?.("change", () => { if (SCENES[run.kind] && run.state) start(); });
  }

  // —— Picker previews ——
  // Each card shows a real, settled frame of its scene; the hovered card runs.
  const previews = new Map();
  let observer = null;
  const hover = { el: null, raf: 0, last: 0, acc: 0 };

  function paintPreview(el) {
    const kind = el.dataset.kind;
    const engine = SCENES[kind];
    if (!engine) return;
    const w = el.clientWidth;
    const h = el.clientHeight;
    if (w < 8 || h < 4) return;
    let canvas = el.querySelector("canvas");
    if (!canvas) {
      canvas = document.createElement("canvas");
      el.append(canvas);
    }
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    canvas.width = Math.round(w * dpr);
    canvas.height = Math.round(h * dpr);
    const ctx = canvas.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    const s = makeState(ctx, engine.previewK || 0.5);
    Object.assign(s, { w, h, dpr });
    engine.setup?.(s);
    engine.resize(s);
    const t = 50;
    engine.warm?.(s, t, true);
    engine.draw(s, t, 1 / engine.fps);
    previews.set(el, { s, t, engine });
  }

  function hoverFrame(now) {
    hover.raf = 0;
    const entry = hover.el && previews.get(hover.el);
    if (!entry) return;
    const sec = now / 1000;
    const dt = hover.last ? Math.min(0.25, sec - hover.last) : 0;
    hover.last = sec;
    hover.acc += dt;
    const budget = 1 / entry.engine.fps;
    if (hover.acc >= budget) {
      const step = Math.max(hover.acc, budget);
      hover.acc = 0;
      entry.t += step;
      entry.engine.draw(entry.s, entry.t, step);
    }
    hover.raf = requestAnimationFrame(hoverFrame);
  }

  function hoverPreview(el) {
    if (hover.el === el) return;
    if (hover.raf) cancelAnimationFrame(hover.raf);
    hover.raf = 0;
    hover.el = el;
    hover.last = 0;
    hover.acc = 0;
    if (el && previews.has(el) && !reduce.matches) hover.raf = requestAnimationFrame(hoverFrame);
  }

  function scanPreviews(root = document) {
    const els = root.querySelectorAll?.(".chat-backdrop-preview[data-kind]") || [];
    if (!observer && typeof ResizeObserver === "function") {
      observer = new ResizeObserver((entries) => {
        for (const entry of entries) {
          const el = entry.target;
          const box = entry.contentRect;
          const prev = previews.get(el);
          if (box.width > 8 && (!prev || Math.abs(prev.s.w - el.clientWidth) > 1 || Math.abs(prev.s.h - el.clientHeight) > 1)) paintPreview(el);
        }
      });
      document.addEventListener("pointerover", (event) => {
        const card = event.target.closest?.(".ap-backdrop");
        hoverPreview(card ? card.querySelector(".chat-backdrop-preview[data-kind]") : null);
      });
    }
    for (const el of els) {
      if (!SCENES[el.dataset.kind] || el.dataset.fxObserved) continue;
      el.dataset.fxObserved = "1";
      if (observer) observer.observe(el);
      else paintPreview(el);
    }
  }

  // Only a theme change alters a preview; Intensity and the choice itself
  // leave them as they are.
  let previewInk = "";
  function repaintPreviews() {
    const style = getComputedStyle(html);
    const ink = ["--ink", "--mist", "--mute", "--brass", "--steel"].map((p) => style.getPropertyValue(p).trim()).join();
    if (ink === previewInk) return;
    previewInk = ink;
    for (const el of previews.keys()) {
      if (!el.isConnected) previews.delete(el);
      else paintPreview(el);
    }
  }

  // chat-appearance.js calls this whenever the backdrop or theme changes.
  function sync(scene, kind) {
    repaintPreviews();
    const canvas = scene && scene.querySelector("canvas[data-chat-scene-fx]");
    const animated = Boolean(canvas && !scene.hidden && SCENES[kind]);
    if (!animated) {
      stop();
      run.kind = kind || "";
      run.state = null;
      if (canvas) canvas.hidden = true;
      return;
    }
    listen();
    canvas.hidden = false;
    run.canvas = canvas;
    run.ctx = canvas.getContext("2d");
    run.kind = kind;
    start();
  }

  window.FuseBackdrop = { sync, previews: scanPreviews, animated: (kind) => Boolean(SCENES[kind]) };
  scanPreviews();
  // chat-appearance.js may have applied a backdrop before this file loaded.
  const scene = document.querySelector("[data-chat-scene]");
  if (scene) sync(scene, html.dataset.chatBackdrop);
})();
