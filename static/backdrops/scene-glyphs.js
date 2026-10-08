// Scene: glyphs.
// Verbatim extraction from static/chat-backdrop.js; chunk map: backdrop-manifest.json in the snapshot parent.
// Split from scenes-glyph.js; one scene per file.

import { tones } from "./color.js";
import { field, hash } from "./noise.js";
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
