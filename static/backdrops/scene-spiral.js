// Scene: spiral.
// Verbatim extraction from static/chat-backdrop.js; chunk map: backdrop-manifest.json in the snapshot parent.
// Split from scenes-circuit.js; one scene per file.

import { ring } from "./color.js";
import { field } from "./noise.js";
import { cells, blit, put } from "./kit.js";

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
