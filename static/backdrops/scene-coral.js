// Scene: coral.
// Verbatim extraction from static/chat-backdrop.js; chunk map: backdrop-manifest.json in the snapshot parent.
// Split from scenes-circuit.js; one scene per file.

import { clamp01, mix } from "./color.js";
import { rand } from "./noise.js";
import { cells, blit, put } from "./kit.js";

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
