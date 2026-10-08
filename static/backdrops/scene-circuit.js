// Scene: circuit.
// Verbatim extraction from static/chat-backdrop.js; chunk map: backdrop-manifest.json in the snapshot parent.
// Split from scenes-circuit.js; one scene per file.

import { TAU } from "./env.js";
import { css, clamp01 } from "./color.js";
import { rand, pick } from "./noise.js";

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
