// Scene: mandala.
// Verbatim extraction from static/chat-backdrop.js; chunk map: backdrop-manifest.json in the snapshot parent.
// Split from scenes-field.js; one scene per file.

import { TAU } from "./env.js";
import { css, ring } from "./color.js";
import { field, noise } from "./noise.js";
import { atlas } from "./kit.js";

// A mandala of glyphs: twelve mirrored wedges of a drifting field, so the
// noise folds into petals and lattices that bloom and close. The rosette
// turns very slowly, and each ring burns in its own spectrum ink.
export const mandala = {
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
};
