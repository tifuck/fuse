// Scene: life.
// Verbatim extraction from static/chat-backdrop.js; chunk map: backdrop-manifest.json in the snapshot parent.
// Split from scenes-glyph.js; one scene per file.

import { css } from "./color.js";

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
