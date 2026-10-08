// Scene: ripple.
// Verbatim extraction from static/chat-backdrop.js; chunk map: backdrop-manifest.json in the snapshot parent.
// Split from scenes-glyph.js; one scene per file.

import { TAU } from "./env.js";
import { css, tones } from "./color.js";
import { rand } from "./noise.js";

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
