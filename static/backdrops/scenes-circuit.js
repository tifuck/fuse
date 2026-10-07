// Scenes: circuit, spiral, coral.
// Verbatim extraction from static/chat-backdrop.js; chunk map: backdrop-manifest.json in the snapshot parent.

import { TAU } from "./env.js";
import { css, clamp01, mix, ring } from "./color.js";
import { rand, pick, field } from "./noise.js";
import { cells, blit, put } from "./kit.js";

// A circuit board: traces routed on a 45° grid between pads and chips,
// with signal pulses running down them.
export const circuit = {
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
};

// A cyclic cellular automaton: every cell advances when a neighbour is one
// state ahead, and random soup organises itself into rotating spirals.
// Each state takes a colour from the theme's spectrum.
export const spiral = {
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
};

// Gray–Scott reaction–diffusion in its coral regime. Growth fills the
// screen, patches erode away, and the reef grows back into them.
export const coral = {
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
};
