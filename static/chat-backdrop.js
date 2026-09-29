/* Animated chat backdrops. chat-appearance.js owns the choice and strength;
   this file only draws. Every scene reads its palette from the active theme,
   renders on its own frame budget, pauses while the tab is hidden, and holds a
   single still frame under prefers-reduced-motion. Scenes answer the pointer
   (the scene layer itself never takes events), and the picker cards render a
   real frame of each scene, animating while hovered. */
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
  function blit(s, gutter = true) {
    const { ctx } = s;
    s.offCtx.putImageData(s.img, 0, 0);
    ctx.clearRect(0, 0, s.w, s.h);
    ctx.imageSmoothingEnabled = false;
    ctx.drawImage(s.off, 0, 0, s.cols * s.cell, s.rows * s.cell);
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
  // Pointer in cell coordinates, or null when it is idle.
  function near(s, cw, ch = cw) {
    const p = s.ptr;
    if (!p.on || p.e < 0.02) return null;
    return { x: p.x / cw, y: p.y / ch, e: p.e };
  }

  // 16 arrows, one per direction, for the wind field.
  function arrowAtlas(s, g, colors) {
    const dpr = s.dpr;
    const a = document.createElement("canvas");
    a.width = Math.ceil(16 * g * dpr);
    a.height = Math.ceil(colors.length * g * dpr);
    const c = a.getContext("2d");
    c.scale(dpr, dpr);
    c.lineCap = "round";
    c.lineJoin = "round";
    colors.forEach((color, row) => {
      c.strokeStyle = color;
      c.lineWidth = Math.max(1, g * 0.07);
      for (let i = 0; i < 16; i += 1) {
        const ang = (i / 16) * TAU;
        const cx = i * g + g / 2;
        const cy = row * g + g / 2;
        const L = g * 0.34;
        const dx = Math.cos(ang) * L;
        const dy = Math.sin(ang) * L;
        c.beginPath();
        c.moveTo(cx - dx, cy - dy);
        c.lineTo(cx + dx, cy + dy);
        const hx = Math.cos(ang + 2.6) * g * 0.16;
        const hy = Math.sin(ang + 2.6) * g * 0.16;
        const kx = Math.cos(ang - 2.6) * g * 0.16;
        const ky = Math.sin(ang - 2.6) * g * 0.16;
        c.moveTo(cx + dx + hx, cy + dy + hy);
        c.lineTo(cx + dx, cy + dy);
        c.lineTo(cx + dx + kx, cy + dy + ky);
        c.stroke();
      }
    });
    return { canvas: a, cw: g, ch: g, dpr };
  }

  // —— Scenes ——
  const SCENES = {
    // ASCII flow field: a glyph per cell, chosen by the field's brightness.
    // Peaks flicker through code characters; the pointer drops a lens that
    // swells the field and sends rings out through it.
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
        const p = near(s, cw, ch);
        ctx.clearRect(0, 0, s.w, s.h);
        for (let y = 0; y < rows; y += 1) {
          for (let x = 0; x < cols; x += 1) {
            let v = field(x * k, y * k * 1.8, t * 0.12);
            // A slow diagonal swell sweeps through the field.
            v += 0.16 * Math.sin((x * 0.045 + y * 0.09) - t * 0.35);
            if (p) {
              const dx = x - p.x;
              const dy = (y - p.y) * 1.8;
              const d = Math.sqrt(dx * dx + dy * dy);
              v += p.e * (0.42 * Math.exp(-(d * d) / 40) + 0.2 * Math.sin(d * 0.6 - t * 5) * Math.exp(-d / 16));
            }
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

    // Conway's Game of Life. Cells cool through the accent as they die, fresh
    // seeds land when the colony stalls, and the pointer paints live cells.
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
      seed(s, ox, oy) {
        // An R-pentomino: small, and it runs for a long time.
        const shape = [[1, 0], [2, 0], [0, 1], [1, 1], [1, 2]];
        ox = ox ?? 2 + Math.floor(Math.random() * (s.cols - 5));
        oy = oy ?? 2 + Math.floor(Math.random() * (s.rows - 5));
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
        const p = near(s, s.cell);
        if (p && p.e > 0.5) this.seed(s, Math.floor(p.x) - 1, Math.floor(p.y) - 1);
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
    // a cooling tail. Drops slow as they pass the pointer and bunch up there.
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
        const p = near(s, s.cw, s.ch);
        for (let c = 0; c < cols; c += 1) {
          const d = drops[c];
          const decay = Math.exp(-dt * d.fade);
          for (let r = 0; r < rows; r += 1) heat[r * cols + c] *= decay;
          let slow = 1;
          if (p) {
            const dx = Math.abs(c - p.x) / 7;
            const dy = Math.abs(d.y - p.y) / 6;
            const q = dx * dx + dy * dy;
            if (q < 1) slow = 1 - p.e * 0.8 * (1 - q);
          }
          const from = Math.floor(d.y);
          d.y += d.v * dt * slow;
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

    // Topographic map in box-drawing characters: marching squares over a
    // drifting height field. Every fourth contour is an index line, drawn
    // brighter. The pointer raises a hill, and rings close around it.
    topo: {
      fps: 10,
      cases: " ╲╱─╲╳│╱╱│╳╲─╱╲ ",
      levels: 7,
      setup(s) {
        s.cw = 9 * s.k;
        s.ch = 16 * s.k;
      },
      resize(s) {
        s.cols = Math.ceil(s.w / s.cw);
        s.rows = Math.ceil(s.h / s.ch);
        const pal = s.pal;
        s.A = atlas(s, this.cases + "·+", [
          css(pal.mute, 0.35), css(mix(pal.mute, pal.steel, 0.6), 0.7), css(pal.steel, 0.9), css(pal.brass, 1), css(pal.tint, 1),
        ], s.cw, s.ch, 14 * s.k);
        s.lv = new Uint8Array((s.cols + 1) * (s.rows + 1));
      },
      draw(s, t) {
        const { ctx, cols, rows, cw, ch, A, lv } = s;
        const L = this.levels;
        const W = cols + 1;
        const p = s.ptr.on && s.ptr.e > 0.02 ? s.ptr : null;
        const sig = 1 / (2 * 110 * 110);
        for (let y = 0; y <= rows; y += 1) {
          for (let x = 0; x <= cols; x += 1) {
            const fx = x * cw * 0.0026;
            const fy = y * ch * 0.0026;
            const tt = t * 0.035;
            let v = 0.78 * noise(fx + tt, fy - tt * 0.4) + 0.22 * noise(fx * 2.3 - tt * 0.6, fy * 2.3 + tt * 0.5);
            v = (v - 0.18) * 1.6;
            if (p) {
              const dx = x * cw - p.x;
              const dy = y * ch - p.y;
              v += 0.55 * p.e * Math.exp(-(dx * dx + dy * dy) * sig);
            }
            lv[y * W + x] = Math.max(0, Math.min(L + 2, Math.floor(v * L)));
          }
        }
        ctx.clearRect(0, 0, s.w, s.h);
        const dot = this.cases.length;
        for (let y = 0; y < rows; y += 1) {
          for (let x = 0; x < cols; x += 1) {
            const a = lv[y * W + x];
            const b = lv[y * W + x + 1];
            const c = lv[(y + 1) * W + x + 1];
            const d = lv[(y + 1) * W + x];
            const hi = Math.max(a, b, c, d);
            if (hi === Math.min(a, b, c, d)) {
              // Survey grid between the lines.
              if (x % 6 === 0 && y % 3 === 0) stamp(ctx, A, (x % 24 === 0 && y % 12 === 0) ? dot + 1 : dot, 0, x * cw, y * ch);
              continue;
            }
            const code = (a >= hi ? 8 : 0) | (b >= hi ? 4 : 0) | (c >= hi ? 2 : 0) | (d >= hi ? 1 : 0);
            const u = hi / L;
            let tone = u < 0.3 ? 1 : u < 0.62 ? 2 : 3;
            if (hi % 4 === 0) tone = Math.min(4, tone + 1);
            stamp(ctx, A, code, tone, x * cw, y * ch);
          }
        }
      },
    },

    // Ripple tank in braille: three wandering emitters (the pointer is a
    // fourth) interfere, and the crests are drawn as 2×4 dot cells.
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
        if (s.ptr.on && s.ptr.e > 0.02) out.push([s.ptr.x, s.ptr.y, 1.4 * s.ptr.e]);
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
    // with signal pulses running down them. The pointer wakes nearby traces.
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
        const p = s.ptr;
        if (p.on && p.e > 0.5 && Math.random() < dt * 14) {
          // Wake the trace nearest the pointer.
          let best = null;
          let bd = (90 * s.k) ** 2;
          for (const tr of s.traces) {
            const [x, y] = tr.path[0];
            const q = (x - p.x) ** 2 + (y - p.y) ** 2;
            const [x2, y2] = tr.path[tr.path.length - 1];
            const q2 = (x2 - p.x) ** 2 + (y2 - p.y) ** 2;
            if (Math.min(q, q2) < bd) { bd = Math.min(q, q2); best = tr; }
          }
          this.launch(s, best);
        }
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

    // A sampler cloth of elementary cellular automata: the width is split
    // into panels, each weaving its own rule in its own spectrum ink. Every
    // new generation lands at the bottom and the cloth scrolls up; a panel
    // changes rule now and then, leaving a seam. The pointer snags threads in
    // the row being woven.
    loom: {
      fps: 14,
      rules: [30, 110, 54, 45, 150, 73, 105, 90, 106, 62, 225, 137, 18, 126],
      resize(s) {
        cells(s, Math.max(3, Math.round(6 * s.k)));
        const { cols, rows } = s;
        s.buf = new Uint8Array(cols * rows);
        s.ink = new Uint8Array(cols * rows);
        s.head = 0;
        const n = Math.max(1, Math.round(cols / 48));
        const gap = n > 1 ? 2 : 0;
        const wide = (cols - gap * (n - 1)) / n;
        s.panels = Array.from({ length: n }, (_, i) => ({
          x0: Math.round(i * (wide + gap)),
          x1: Math.round(i * (wide + gap) + wide),
          rule: Math.floor(Math.random() * this.rules.length),
          ink: i % s.pal.spectrum.length,
          age: Math.floor(Math.random() * 200),
        }));
        for (let x = 0; x < cols; x += 1) s.buf[x] = Math.random() < 0.5 ? 1 : 0;
        for (let i = 1; i < rows; i += 1) this.weave(s);
      },
      weave(s) {
        const { cols, rows, buf, ink } = s;
        const prev = s.head * cols;
        const nextRow = (s.head + 1) % rows;
        const out = nextRow * cols;
        buf.fill(0, out, out + cols);
        const p = near(s, s.cell);
        for (const pan of s.panels) {
          pan.age += 1;
          if (pan.age > 220) {
            pan.age = Math.floor(Math.random() * 60);
            pan.rule = (pan.rule + 1 + Math.floor(Math.random() * 4)) % this.rules.length;
            pan.ink = (pan.ink + 1) % s.pal.spectrum.length;
          }
          const rule = this.rules[pan.rule];
          const { x0, x1 } = pan;
          const w = x1 - x0;
          let alive = 0;
          for (let x = x0; x < x1; x += 1) {
            const l = buf[prev + (x === x0 ? x1 - 1 : x - 1)];
            const c = buf[prev + x];
            const r = buf[prev + (x === x1 - 1 ? x0 : x + 1)];
            let v = (rule >> ((l << 2) | (c << 1) | r)) & 1;
            if (Math.random() < 0.0008) v ^= 1;
            buf[out + x] = v;
            ink[out + x] = pan.ink;
            alive += v;
          }
          // A dead or saturated panel gets fresh thread.
          if (alive < w * 0.03 || alive > w * 0.97) for (let x = x0; x < x1; x += 1) buf[out + x] = Math.random() < 0.5 ? 1 : 0;
          if (p && p.e > 0.3 && p.x >= x0 - 2 && p.x < x1 + 2) {
            const cx = Math.floor(p.x);
            for (let dx = -2; dx <= 2; dx += 1) if (cx + dx >= x0 && cx + dx < x1 && Math.random() < 0.6) buf[out + cx + dx] ^= 1;
          }
        }
        s.head = nextRow;
      },
      draw(s) {
        this.weave(s);
        const { cols, rows, buf, ink, img, pal } = s;
        const px = img.data;
        const sp = pal.spectrum;
        for (let r = 0; r < rows; r += 1) {
          const src = (s.head + 1 + r) % rows;
          const u = r / (rows - 1);
          const newest = r === rows - 1;
          const a = newest ? 1 : 0.14 + 0.72 * u * u;
          const row = src * cols;
          for (let x = 0; x < cols; x += 1) {
            const i = r * cols + x;
            if (!buf[row + x]) {
              px[i * 4 + 3] = 0;
              continue;
            }
            // Lone threads burn brighter than solid runs.
            const run = (x > 0 ? buf[row + x - 1] : 0) + (x < cols - 1 ? buf[row + x + 1] : 0);
            put(px, i, newest ? pal.tint : sp[ink[row + x]], a * (run === 0 ? 1 : run === 1 ? 0.78 : 0.5));
          }
        }
        blit(s);
      },
    },

    // A cyclic cellular automaton: every cell advances when a neighbour is one
    // state ahead, and random soup organises itself into rotating spirals.
    // Each state takes a colour from the theme's spectrum. The pointer stirs
    // new soup, which seeds new spirals.
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
      soup(s, cx, cy, r) {
        const N = this.states;
        if (cx === undefined) {
          for (let i = 0; i < s.grid.length; i += 1) s.grid[i] = Math.floor(Math.random() * N);
          return;
        }
        for (let y = -r; y <= r; y += 1) for (let x = -r; x <= r; x += 1) {
          if (x * x + y * y > r * r) continue;
          const gx = (cx + x + s.cols) % s.cols;
          const gy = (cy + y + s.rows) % s.rows;
          s.grid[gy * s.cols + gx] = Math.floor(Math.random() * N);
        }
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
        const p = near(s, s.cell);
        if (p && p.e > 0.6) this.soup(s, Math.floor(p.x), Math.floor(p.y), 3);
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
    // screen, patches erode away, and the reef grows back into them. The
    // pointer drops catalyst.
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
        const p = near(s, s.cell);
        if (p && p.e > 0.4) this.drop(s, Math.floor(p.x), Math.floor(p.y), 2);
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
    // mix like ink. The pointer is a loupe that swells the dots under it.
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
        const p = s.ptr.on && s.ptr.e > 0.02 ? s.ptr : null;
        const lens = 1 / (2 * (140 * s.k) ** 2);
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
              if (p) {
                const dx = x - p.x;
                const dy = y - p.y;
                v += 0.6 * p.e * Math.exp(-(dx * dx + dy * dy) * lens);
              }
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

    // Langton's ants: each ant turns on the colour under it, flips it, and
    // steps on. Chaos, then a highway. Every ant lays its own spectrum ink,
    // old ants retire and their ground dissolves, and the pointer hatches new
    // ants.
    ants: {
      fps: 24,
      setup(s) {
        s.nextId = 1;
        s.lastHatch = 0;
        s.clock = 0;
      },
      resize(s) {
        cells(s, Math.max(2, Math.round(5 * s.k)));
        const n = s.cols * s.rows;
        s.state = new Uint8Array(n);
        s.owner = new Uint8Array(n);
        s.heat = new Float32Array(n);
        s.fading = new Uint8Array(256);
        s.ants = [];
        for (let i = 0; i < 5; i += 1) this.hatch(s);
      },
      hatch(s, x, y) {
        const id = s.nextId;
        s.nextId = (s.nextId % 255) + 1;
        s.fading[id] = 0;
        s.ants.push({
          id,
          x: x ?? Math.floor(Math.random() * s.cols),
          y: y ?? Math.floor(Math.random() * s.rows),
          dir: Math.floor(Math.random() * 4),
          life: Math.floor(rand(14000, 26000)),
        });
      },
      warm(s) {
        for (let i = 0; i < 200; i += 1) this.step(s, 1 / 24);
      },
      step(s, dt) {
        s.clock += dt;
        const { cols, rows, state, owner, heat } = s;
        const steps = 30;
        for (const a of s.ants) {
          for (let k = 0; k < steps; k += 1) {
            const i = a.y * cols + a.x;
            a.dir = (a.dir + (state[i] ? 3 : 1)) % 4;
            state[i] ^= 1;
            owner[i] = a.id;
            heat[i] = 1;
            if (a.dir === 0) a.y = (a.y - 1 + rows) % rows;
            else if (a.dir === 1) a.x = (a.x + 1) % cols;
            else if (a.dir === 2) a.y = (a.y + 1) % rows;
            else a.x = (a.x - 1 + cols) % cols;
          }
          a.life -= steps;
        }
        for (const a of s.ants) if (a.life <= 0) s.fading[a.id] = 1;
        s.ants = s.ants.filter((a) => a.life > 0);
        while (s.ants.length < 4) this.hatch(s);
        const p = near(s, s.cell);
        if (p && p.e > 0.6 && s.clock - s.lastHatch > 1.2 && s.ants.length < 10) {
          this.hatch(s, Math.floor(p.x) % cols, Math.floor(p.y) % rows);
          s.lastHatch = s.clock;
        }
        // A retired ant's ground dissolves over a couple of seconds.
        for (let i = 0; i < state.length; i += 1) {
          if (state[i] && s.fading[owner[i]] && Math.random() < 0.04) state[i] = 0;
          heat[i] *= 0.9;
        }
      },
      draw(s, t, dt) {
        this.step(s, Math.min(dt, 0.1));
        const { state, owner, heat, img, pal } = s;
        const px = img.data;
        const sp = pal.spectrum;
        for (let i = 0; i < state.length; i += 1) {
          const h = heat[i];
          if (state[i]) put(px, i, h > 0.3 ? mix(sp[owner[i] % sp.length], pal.tint, h * 0.5) : sp[owner[i] % sp.length], 0.5 + h * 0.5);
          else if (h > 0.08) put(px, i, pal.mute, h * 0.4);
          else px[i * 4 + 3] = 0;
        }
        for (const a of s.ants) put(px, a.y * s.cols + a.x, pal.tint, 1);
        blit(s, s.cell >= 5);
      },
    },

    // A labyrinth carved live by a backtracking digger, then flooded from a
    // spring (the pointer, if it is on screen), the flood banded in the
    // theme's spectrum. The route to the farthest point lights up, the maze
    // fades, and a new one is dug.
    maze: {
      fps: 30,
      setup(s) {
        s.phase = "carve";
      },
      resize(s) {
        cells(s, Math.max(3, Math.round(7 * s.k)));
        this.reset(s);
      },
      reset(s) {
        const n = s.cols * s.rows;
        s.open = new Uint8Array(n);
        s.dist = new Int32Array(n).fill(-1);
        s.path = new Uint8Array(n);
        s.heat = new Float32Array(n);
        s.mw = Math.max(1, Math.floor((s.cols - 1) / 2));
        s.mh = Math.max(1, Math.floor((s.rows - 1) / 2));
        const sx = Math.floor(Math.random() * s.mw);
        const sy = Math.floor(Math.random() * s.mh);
        s.stack = [[sx, sy]];
        s.open[(sy * 2 + 1) * s.cols + sx * 2 + 1] = 1;
        s.phase = "carve";
        s.fade = 1;
        s.hold = 0;
        s.front = [];
        s.level = 0;
        s.trail = [];
      },
      carve(s, budget) {
        const { cols, open } = s;
        const DIRS = [[1, 0], [-1, 0], [0, 1], [0, -1]];
        while (budget-- > 0 && s.stack.length) {
          const [x, y] = s.stack[s.stack.length - 1];
          const options = [];
          for (const [dx, dy] of DIRS) {
            const nx = x + dx;
            const ny = y + dy;
            if (nx < 0 || ny < 0 || nx >= s.mw || ny >= s.mh) continue;
            if (!open[(ny * 2 + 1) * cols + nx * 2 + 1]) options.push([nx, ny, dx, dy]);
          }
          if (!options.length) {
            s.stack.pop();
            continue;
          }
          const [nx, ny, dx, dy] = pick(options);
          const wall = (y * 2 + 1 + dy) * cols + x * 2 + 1 + dx;
          const cell = (ny * 2 + 1) * cols + nx * 2 + 1;
          open[wall] = 1;
          open[cell] = 1;
          s.heat[wall] = 1;
          s.heat[cell] = 1;
          s.stack.push([nx, ny]);
        }
        if (!s.stack.length) {
          // The spring: the pointer's cell if it is over the maze.
          const p = s.ptr;
          let sx;
          let sy;
          if (p.on) {
            sx = Math.min(s.mw - 1, Math.max(0, Math.floor((p.x / s.cell - 1) / 2)));
            sy = Math.min(s.mh - 1, Math.max(0, Math.floor((p.y / s.cell - 1) / 2)));
          } else {
            sx = Math.floor(Math.random() * s.mw);
            sy = Math.floor(Math.random() * s.mh);
          }
          const i = (sy * 2 + 1) * cols + sx * 2 + 1;
          s.dist[i] = 0;
          s.front = [i];
          s.phase = "flood";
        }
      },
      flood(s, budget) {
        const { cols, open, dist } = s;
        while (budget-- > 0 && s.front.length) {
          const next = [];
          for (const i of s.front) {
            for (const j of [i + 1, i - 1, i + cols, i - cols]) {
              if (j < 0 || j >= open.length || !open[j] || dist[j] >= 0) continue;
              dist[j] = dist[i] + 1;
              next.push(j);
            }
          }
          if (!next.length) {
            // Trace back from the last cell the flood reached.
            let i = s.front[0];
            s.trail = [i];
            while (dist[i] > 0) {
              for (const j of [i + 1, i - 1, i + cols, i - cols]) {
                if (j >= 0 && j < open.length && dist[j] === dist[i] - 1) { i = j; break; }
              }
              s.trail.push(i);
            }
            s.phase = "trace";
            s.front = [];
            break;
          }
          s.level += 1;
          s.front = next;
        }
      },
      warm(s) {
        this.carve(s, Math.ceil(s.mw * s.mh * 0.6));
      },
      draw(s, t, dt) {
        const area = s.mw * s.mh;
        if (s.phase === "carve") this.carve(s, Math.max(2, Math.ceil(area / (this.fps * 11))));
        else if (s.phase === "flood") this.flood(s, Math.max(1, Math.ceil(area / (this.fps * 16))));
        else if (s.phase === "trace") {
          for (let k = Math.max(1, Math.ceil(s.trail.length / (this.fps * 2.5))); k > 0 && s.trail.length; k -= 1) s.path[s.trail.pop()] = 1;
          if (!s.trail.length) s.phase = "hold";
        } else if (s.phase === "hold") {
          s.hold += dt;
          if (s.hold > 5) s.phase = "fade";
        } else if (s.phase === "fade") {
          s.fade -= dt / 1.6;
          if (s.fade <= 0) this.reset(s);
        }
        const { open, dist, path, heat, img, pal, cols } = s;
        const px = img.data;
        const band = Math.max(40, (s.mw + s.mh) * 1.2);
        const head = s.phase === "carve" && s.stack.length ? s.stack[s.stack.length - 1] : null;
        const headAt = head ? (head[1] * 2 + 1) * cols + head[0] * 2 + 1 : -1;
        for (let i = 0; i < open.length; i += 1) {
          if (!open[i]) {
            px[i * 4 + 3] = 0;
            continue;
          }
          const h = heat[i];
          heat[i] = h * 0.94;
          let c = h > 0.02 ? mix(pal.steel, pal.tint, h) : pal.steel;
          let a = 0.3 + 0.7 * h;
          if (path[i]) {
            c = pal.tint;
            a = 1;
          } else if (dist[i] >= 0) {
            const behind = s.level - dist[i];
            c = ring(pal, dist[i] / band);
            a = s.phase === "flood" ? 0.45 + 0.55 * Math.exp(-behind / 6) : 0.5;
          }
          if (i === headAt) {
            c = pal.tint;
            a = 1;
          }
          put(px, i, c, a * s.fade);
        }
        blit(s, false);
      },
    },

    // A weather chart: an arrow per cell along the curl of a drifting field,
    // with gusts streaming through and lighting the arrows they pass. The
    // pointer stirs a vortex.
    wind: {
      fps: 15,
      setup(s) {
        s.g = Math.max(8, 22 * s.k);
      },
      resize(s) {
        s.cols = Math.ceil(s.w / s.g);
        s.rows = Math.ceil(s.h / s.g);
        const pal = s.pal;
        s.A = arrowAtlas(s, s.g, [css(pal.mute, 0.4), css(mix(pal.mute, pal.steel, 0.6), 0.75), css(pal.steel, 0.95), css(pal.brass, 1), css(pal.tint, 1)]);
        const n = s.cols * s.rows;
        s.heat = new Float32Array(n);
        s.vx = new Float32Array(n);
        s.vy = new Float32Array(n);
        s.psi = new Float32Array((s.cols + 1) * (s.rows + 1));
        const count = Math.round(n / 10);
        s.gusts = Array.from({ length: count }, () => ({ x: Math.random() * s.w, y: Math.random() * s.h, life: rand(2, 7) }));
      },
      // Velocity is the curl of a stream function sampled at cell corners, so
      // the flow has no sources or sinks: it only swirls.
      flow(s, t) {
        const { cols, rows, g, psi, vx, vy } = s;
        const k = 0.0032 / s.k;
        const W = cols + 1;
        for (let y = 0; y <= rows; y += 1) for (let x = 0; x <= cols; x += 1) psi[y * W + x] = field(x * g * k, y * g * k, t * 0.04);
        const gain = 4.8 / (g * k);
        const p = s.ptr;
        const live = p.on && p.e > 0.02;
        const fall = 1 / (160 * s.k);
        for (let y = 0; y < rows; y += 1) {
          for (let x = 0; x < cols; x += 1) {
            const a = psi[y * W + x];
            const b = psi[y * W + x + 1];
            const c = psi[(y + 1) * W + x];
            const d = psi[(y + 1) * W + x + 1];
            let u = ((c + d) - (a + b)) * 0.5 * gain;
            let v = -((b + d) - (a + c)) * 0.5 * gain;
            if (live) {
              const dx = (x + 0.5) * g - p.x;
              const dy = (y + 0.5) * g - p.y;
              const r = Math.sqrt(dx * dx + dy * dy) + 1;
              const f = (p.e * 1.4 * Math.exp(-r * fall)) / r;
              u -= dy * f;
              v += dx * f;
            }
            vx[y * cols + x] = u;
            vy[y * cols + x] = v;
          }
        }
      },
      warm(s) {
        for (let i = 0; i < 40; i += 1) this.step(s, 60 + i / 15, 1 / 15);
      },
      step(s, t, dt) {
        this.flow(s, t);
        const { cols, rows, heat, g, vx, vy } = s;
        const fade = Math.exp(-dt * 1.3);
        for (let i = 0; i < heat.length; i += 1) heat[i] *= fade;
        const speed = 170 * s.k;
        for (const q of s.gusts) {
          const i = Math.floor(q.y / g) * cols + Math.floor(q.x / g);
          if (q.life <= 0 || q.x < 0 || q.y < 0 || q.x >= s.w || q.y >= s.h || i < 0 || i >= cols * rows) {
            q.x = Math.random() * s.w;
            q.y = Math.random() * s.h;
            q.life = rand(2, 7);
            continue;
          }
          heat[i] = Math.min(1, heat[i] + 0.35);
          q.x += vx[i] * speed * dt;
          q.y += vy[i] * speed * dt;
          q.life -= dt;
        }
      },
      draw(s, t, dt) {
        this.step(s, t, Math.min(dt, 0.1));
        const { ctx, cols, rows, g, heat, vx, vy, A } = s;
        const d = A.dpr;
        ctx.clearRect(0, 0, s.w, s.h);
        for (let y = 0; y < rows; y += 1) {
          for (let x = 0; x < cols; x += 1) {
            const i = y * cols + x;
            const u = vx[i];
            const v = vy[i];
            const h = heat[i];
            const m = Math.sqrt(u * u + v * v) * 0.9 + h;
            if (m < 0.12) continue;
            const dir = Math.round((Math.atan2(v, u) / TAU) * 16 + 16) % 16;
            const tone = h > 0.75 ? 4 : h > 0.4 ? 3 : h > 0.15 ? 2 : m > 0.5 ? 1 : 0;
            ctx.drawImage(A.canvas, dir * g * d, tone * g * d, g * d, g * d, x * g, y * g, g, g);
          }
        }
      },
    },

    // A glyph starfield flying out of a vanishing point: far stars are dots,
    // near ones become stars with a streak behind them. The pointer steers
    // the vanishing point, and moving it opens the throttle.
    warp: {
      fps: 30,
      previewK: 0.8,
      chars: "·∙+×*✦",
      setup(s) {
        s.vx = 0.5;
        s.vy = 0.5;
        s.throttle = 0;
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
        const p = s.ptr;
        const tx = p.on && p.e > 0.02 ? 0.5 + (p.x / s.w - 0.5) * 0.7 : 0.5;
        const ty = p.on && p.e > 0.02 ? 0.5 + (p.y / s.h - 0.5) * 0.7 : 0.5;
        s.vx += (tx - s.vx) * Math.min(1, dt * 1.5);
        s.vy += (ty - s.vy) * Math.min(1, dt * 1.5);
        s.throttle += ((p.on ? p.e : 0) - s.throttle) * Math.min(1, dt * 2);
        const v = 0.09 * (1 + s.throttle * 3);
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
          st.px = x - (x - cx) * 0.06 * (1 + s.throttle * 2);
          st.py = y - (y - cy) * 0.06 * (1 + s.throttle * 2);
        }
      },
    },
  };

  // —— Pointer ——
  // The scene never takes events; it watches the window instead. `e` is how
  // recently the pointer moved: 1 while it moves, easing to 0 over a few
  // seconds of rest.
  const pointer = { x: -1e4, y: -1e4, on: false, e: 0 };
  const idle = { x: -1e4, y: -1e4, on: false, e: 0 };
  let pointerBound = false;
  function bindPointer() {
    if (pointerBound) return;
    pointerBound = true;
    window.addEventListener("pointermove", (event) => {
      pointer.x = event.clientX;
      pointer.y = event.clientY;
      pointer.on = true;
      pointer.e = 1;
    }, { passive: true });
    html.addEventListener("mouseleave", () => { pointer.on = false; });
    window.addEventListener("blur", () => { pointer.on = false; });
  }

  function makeState(ctx, k, ptr) {
    return { ctx, pal: palette(), dpr: 0, w: 0, h: 0, k, ptr };
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
      pointer.e *= Math.exp(-step * 0.9);
      if (measure()) engine.draw(run.state, run.t, step);
    }
    if (!document.hidden) run.raf = requestAnimationFrame(frame);
  }

  function start() {
    stop();
    const engine = SCENES[run.kind];
    if (!engine || !run.canvas) return;
    run.state = makeState(run.ctx, 1, reduce.matches ? idle : pointer);
    engine.setup?.(run.state);
    // Start mid-flight so the first frame already looks settled.
    run.t = 40 + Math.random() * 60;
    if (!measure()) return;
    engine.warm?.(run.state, run.t);
    engine.draw(run.state, run.t, 1 / engine.fps);
    if (reduce.matches) return;
    if (!document.hidden) run.raf = requestAnimationFrame(frame);
  }

  function listen() {
    if (run.listening) return;
    run.listening = true;
    bindPointer();
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
    const s = makeState(ctx, engine.previewK || 0.5, idle);
    Object.assign(s, { w, h, dpr });
    engine.setup?.(s);
    engine.resize(s);
    const t = 50;
    engine.warm?.(s, t);
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
