// Scenes: glyphs, life, rain, ripple.
// Verbatim extraction from static/chat-backdrop.js; chunk map: backdrop-manifest.json in the snapshot parent.

import { TAU } from "./env.js";
import { css, tones } from "./color.js";
import { field, hash, rand } from "./noise.js";
import { atlas, stamp } from "./kit.js";

// ASCII flow field: a glyph per cell, chosen by the field's brightness.
// Peaks flicker through code characters.
export const glyphs = {
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
};

// Conway's Game of Life. Cells cool through the accent as they die, and
// fresh seeds land when the colony stalls.
export const life = {
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
};

// Glyph rain: hex and half-width kana falling in columns, a hot head and
// a cooling tail.
export const rain = {
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
};

// Ripple tank in braille: three wandering emitters interfere, and the
// crests are drawn as 2×4 dot cells.
export const ripple = {
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
};
