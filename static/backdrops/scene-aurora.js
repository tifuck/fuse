// Scene: aurora.
// Verbatim extraction from static/chat-backdrop.js; chunk map: backdrop-manifest.json in the snapshot parent.
// Split from scenes-field.js; one scene per file.

import { ring } from "./color.js";
import { field, noise } from "./noise.js";

// Aurora: curtains of light hang from a wandering arc, brightest along
// their lower hem and combed into fine vertical rays. Two curtains drift
// at their own pace, their colour sliding along the theme's spectrum.
export const aurora = {
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
};
