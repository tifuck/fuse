/* Animated chat backdrops. chat-appearance.js owns the choice and strength;
   this file only draws. Every scene reads its palette from the active theme,
   renders on its own frame budget, pauses while the tab is hidden, and holds a
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

    // Ridgelines, after the pulsar plot on the Unknown Pleasures sleeve:
    // stacked traces displaced by drifting peaks, each one hiding the lines
    // behind it. The terrain rolls slowly toward the viewer.
    pulsar: {
      fps: 20,
      setup(s) {
        s.gap = Math.max(4, 13 * s.k);
        s.step = Math.max(2, 5 * s.k);
      },
      resize(s) {
        s.lines = Math.ceil(s.h / s.gap) + 6;
        s.xs = Math.ceil(s.w / s.step) + 2;
        s.horizon = new Float32Array(s.xs);
      },
      draw(s, t) {
        const { ctx, pal, gap, step, lines, xs, horizon } = s;
        const amp = gap * 9;
        const kx = 0.006 / s.k;
        const roll = t * 0.35;
        ctx.clearRect(0, 0, s.w, s.h);
        ctx.lineJoin = "round";
        ctx.lineWidth = Math.max(1, 1.3 * s.k);
        // Floating horizon: front to back, a line only shows where it rises
        // above everything nearer, so the hidden parts are never drawn.
        horizon.fill(Infinity);
        for (let i = lines - 1; i >= 0; i -= 1) {
          const base = (i + 1) * gap;
          const row = i - roll;
          const u = i / lines;
          ctx.beginPath();
          let pen = false;
          let px = 0;
          let py = 0;
          let pv = false;
          for (let j = 0; j < xs; j += 1) {
            const x = j * step;
            // Peaks cluster in bands that wander across the width.
            const band = Math.pow(noise(x * kx * 0.35 + 40, row * 0.05), 2.4);
            const n = Math.pow(field(x * kx, row * 0.19, t * 0.02), 3);
            const y = base - amp * n * (0.35 + 2.2 * band);
            const vis = y < horizon[j];
            if (vis && !pen) {
              if (j && !pv) ctx.moveTo(px, Math.min(py, horizon[j - 1]));
              else ctx.moveTo(x, y);
              pen = true;
            }
            if (vis || pen) ctx.lineTo(x, vis ? y : horizon[j]);
            if (!vis) pen = false;
            if (vis) horizon[j] = y;
            px = x;
            py = y;
            pv = vis;
          }
          ctx.strokeStyle = css(mix(pal.steel, pal.brass, u), 0.35 + 0.65 * u);
          ctx.stroke();
        }
      },
    },

    // Ink: thousands of particles follow a slowly turning flow field and
    // leave hairline strokes that fade like wet ink, so the field paints
    // itself as combed, marbled currents. Each stroke takes the spectrum ink
    // of the region it was born in.
    ink: {
      fps: 30,
      setup(s) {
        s.inks = 5;
      },
      resize(s) {
        s.ctx.clearRect(0, 0, s.w, s.h);
        const n = Math.round(Math.max(120, Math.min(3200, (s.w * s.h) / 700)));
        s.p = Array.from({ length: n }, () => this.spawn(s, {}, 0));
        s.colors = s.pal.spectrum.map((c) => css(c, 0.42));
      },
      spawn(s, q, t) {
        q.x = Math.random() * s.w;
        q.y = Math.random() * s.h;
        q.life = rand(3, 9);
        q.ink = Math.floor(field(q.x * 0.0016 / s.k, q.y * 0.0016 / s.k, t * 0.01) * 1.7 * s.inks) % s.inks;
        return q;
      },
      warm(s, t) {
        for (let i = 0; i < 90; i += 1) this.tick(s, t - (90 - i) / 30, 1 / 30);
      },
      tick(s, t, dt) {
        const { ctx, p } = s;
        ctx.globalCompositeOperation = "destination-out";
        ctx.fillStyle = `rgba(0,0,0,${Math.min(0.2, dt * 0.9)})`;
        ctx.fillRect(0, 0, s.w, s.h);
        ctx.globalCompositeOperation = "source-over";
        const k = 0.0019 / s.k;
        const speed = 46 * s.k;
        const paths = Array.from({ length: s.inks }, () => new Path2D());
        for (const q of p) {
          const a = field(q.x * k, q.y * k, t * 0.015) * TAU * 2.2;
          const nx = q.x + Math.cos(a) * speed * dt;
          const ny = q.y + Math.sin(a) * speed * dt;
          paths[q.ink].moveTo(q.x, q.y);
          paths[q.ink].lineTo(nx, ny);
          q.x = nx;
          q.y = ny;
          q.life -= dt;
          if (q.life <= 0 || nx < -4 || ny < -4 || nx > s.w + 4 || ny > s.h + 4) this.spawn(s, q, t);
        }
        ctx.lineWidth = Math.max(0.6, 0.9 * s.k);
        ctx.lineCap = "round";
        paths.forEach((path, i) => {
          ctx.strokeStyle = s.colors[i];
          ctx.stroke(path);
        });
      },
      draw(s, t, dt) {
        this.tick(s, t, Math.min(dt, 0.1));
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

    // A garden that draws itself: stems rise from the edges of the screen,
    // lean and fork like a pen sketch, and finish in small blooms of spectrum
    // ink. When the bed is full it rests, fades away, and is planted again.
    garden: {
      fps: 30,
      setup(s) {
        s.phase = "grow";
      },
      resize(s) {
        this.reset(s);
      },
      reset(s) {
        s.ctx.clearRect(0, 0, s.w, s.h);
        s.tips = [];
        s.planted = 0;
        s.want = Math.max(3, Math.round(s.w / (150 * s.k)));
        s.clock = 0;
        s.nextPlant = 0;
        s.phase = "grow";
        s.rest = 0;
      },
      plant(s) {
        // Most rise from the floor; some lean in from the side walls.
        const side = Math.random();
        let x;
        let y;
        let ang;
        if (side < 0.7) {
          x = rand(0.02, 0.98) * s.w;
          y = s.h + 2;
          ang = -Math.PI / 2 + rand(-0.25, 0.25);
        } else {
          const left = side < 0.85;
          x = left ? -2 : s.w + 2;
          y = rand(0.35, 0.95) * s.h;
          ang = left ? rand(-0.9, -0.3) : Math.PI + rand(0.3, 0.9);
        }
        const len = rand(0.16, 0.3) * s.h;
        s.tips.push({ x, y, ang, left: len, len, width: 2.2 * s.k + 0.6, depth: 0, seed: Math.random() * 100 });
        s.planted += 1;
      },
      bloom(s, x, y) {
        const { ctx, pal } = s;
        const ink = pick(pal.spectrum);
        const petals = 5 + Math.floor(Math.random() * 3);
        const r = rand(3, 7) * s.k + 1;
        const turn = Math.random() * TAU;
        ctx.fillStyle = css(ink, 0.7);
        for (let i = 0; i < petals; i += 1) {
          const a = turn + (i / petals) * TAU;
          ctx.beginPath();
          ctx.ellipse(x + Math.cos(a) * r, y + Math.sin(a) * r, r * 0.75, r * 0.38, a, 0, TAU);
          ctx.fill();
        }
        ctx.fillStyle = css(pal.tint, 0.95);
        ctx.beginPath();
        ctx.arc(x, y, Math.max(0.8, r * 0.3), 0, TAU);
        ctx.fill();
      },
      grow(s, dt) {
        const { ctx, pal } = s;
        s.clock += dt;
        if (s.planted < s.want && s.clock >= s.nextPlant) {
          this.plant(s);
          s.nextPlant = s.clock + rand(0.6, 1.8);
        }
        const speed = 70 * s.k;
        const next = [];
        ctx.lineCap = "round";
        for (const tip of s.tips) {
          const stepLen = Math.min(tip.left, speed * dt * (1 - tip.depth * 0.12));
          // A pen's wobble, and a gentle pull back toward the sky.
          tip.ang += (noise(tip.seed + s.clock * 0.9, tip.depth) - 0.5) * 0.18;
          const up = -Math.PI / 2;
          tip.ang += Math.sin(up - tip.ang) * 0.01;
          const nx = tip.x + Math.cos(tip.ang) * stepLen;
          const ny = tip.y + Math.sin(tip.ang) * stepLen;
          ctx.beginPath();
          ctx.moveTo(tip.x, tip.y);
          ctx.lineTo(nx, ny);
          ctx.lineWidth = tip.width;
          ctx.strokeStyle = css(mix(pal.steel, pal.brass, Math.min(1, tip.depth / 4)), 0.8);
          ctx.stroke();
          tip.x = nx;
          tip.y = ny;
          tip.left -= stepLen;
          if (tip.left > 0.01) {
            next.push(tip);
            // Leaves: a short stroke off the stem now and then.
            if (Math.random() < dt * 1.4 && tip.depth < 4) {
              const side = Math.random() < 0.5 ? 1 : -1;
              const la = tip.ang + side * rand(0.7, 1.2);
              const ll = rand(4, 9) * s.k + 2;
              ctx.beginPath();
              ctx.moveTo(nx, ny);
              ctx.quadraticCurveTo(nx + Math.cos(la - side * 0.5) * ll, ny + Math.sin(la - side * 0.5) * ll, nx + Math.cos(la) * ll * 1.3, ny + Math.sin(la) * ll * 1.3);
              ctx.lineWidth = Math.max(0.7, tip.width * 0.6);
              ctx.strokeStyle = css(pal.steel, 0.55);
              ctx.stroke();
            }
            continue;
          }
          if (tip.depth >= 4 || tip.len < 10 * s.k) {
            this.bloom(s, nx, ny);
            continue;
          }
          const forks = Math.random() < 0.3 ? 3 : 2;
          for (let f = 0; f < forks; f += 1) {
            const spread = forks === 3 ? (f - 1) * 0.55 : (f ? 1 : -1) * rand(0.3, 0.6);
            const len = tip.len * rand(0.55, 0.78);
            next.push({ x: nx, y: ny, ang: tip.ang + spread + rand(-0.12, 0.12), left: len, len, width: tip.width * 0.72, depth: tip.depth + 1, seed: Math.random() * 100 });
          }
        }
        s.tips = next;
        if (s.planted >= s.want && !s.tips.length) s.phase = "rest";
      },
      warm(s) {
        for (let i = 0; i < 360 && s.phase === "grow"; i += 1) this.grow(s, 1 / 30);
      },
      draw(s, t, dt) {
        dt = Math.min(dt, 0.1);
        if (s.phase === "grow") this.grow(s, dt);
        else if (s.phase === "rest") {
          s.rest += dt;
          if (s.rest > 9) s.phase = "fade";
        } else {
          const { ctx } = s;
          ctx.globalCompositeOperation = "destination-out";
          ctx.fillStyle = `rgba(0,0,0,${Math.min(0.3, dt * 1.4)})`;
          ctx.fillRect(0, 0, s.w, s.h);
          ctx.globalCompositeOperation = "source-over";
          s.rest += dt;
          if (s.rest > 12.5) this.reset(s);
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
    engine.warm?.(run.state, run.t);
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
