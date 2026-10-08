// Scene: riso.
// Verbatim extraction from static/chat-backdrop.js; chunk map: backdrop-manifest.json in the snapshot parent.
// Split from scenes-field.js; one scene per file.

import { TAU } from "./env.js";
import { css, clamp01 } from "./color.js";
import { field } from "./noise.js";

// Riso print: three halftone screens at their own angles, one per spectrum
// ink, each driven by its own drift and a little off register. Overlaps
// mix like ink.
export const riso = {
  fps: 8,
  previewK: 0.4,
  setup(s) {
    s.pitch = 17 * s.k;
  },
  resize(s) {
    const pal = s.pal;
    const inks = [pal.brass, pal.spectrum[1], pal.spectrum[3]];
    const R = s.pitch * 0.5;
    const steps = 20;
    const dpr = s.dpr;
    s.inks = inks.map((ink) => {
      const a = document.createElement("canvas");
      const cell = Math.ceil(R * 2 + 2);
      a.width = Math.ceil(steps * cell * dpr);
      a.height = Math.ceil(cell * dpr);
      const c = a.getContext("2d");
      c.scale(dpr, dpr);
      c.fillStyle = css(ink, 0.8);
      for (let i = 1; i < steps; i += 1) {
        c.beginPath();
        c.arc(i * cell + cell / 2, cell / 2, (R * i) / (steps - 1), 0, TAU);
        c.fill();
      }
      return { canvas: a, cell, steps };
    });
    s.angles = [0.26, 1.31, 0.79];
  },
  draw(s, t) {
    const { ctx, pitch } = s;
    ctx.clearRect(0, 0, s.w, s.h);
    ctx.globalCompositeOperation = s.pal.dark ? "screen" : "multiply";
    const cx0 = s.w / 2;
    const cy0 = s.h / 2;
    // Each ink's density is sampled on a coarse grid and interpolated.
    const G = 24 * s.k;
    const gw = Math.ceil(s.w / G) + 2;
    const gh = Math.ceil(s.h / G) + 2;
    if (!s.dens || s.dens.length !== gw * gh) s.dens = new Float32Array(gw * gh);
    const dens = s.dens;
    const reach = Math.hypot(s.w, s.h) / 2 + pitch;
    s.inks.forEach((ink, li) => {
      const ang = s.angles[li];
      const cos = Math.cos(ang);
      const sin = Math.sin(ang);
      const ox = cx0 + Math.sin(t * 0.07 + li * 2) * 3 * s.k;
      const oy = cy0 + Math.cos(t * 0.05 + li) * 3 * s.k;
      const span = Math.ceil(reach / pitch);
      const d = s.dpr;
      const size = ink.cell;
      const fk = 0.0028 / s.k;
      const tt = t * (0.05 + li * 0.012);
      for (let gy = 0; gy < gh; gy += 1) {
        for (let gx = 0; gx < gw; gx += 1) dens[gy * gw + gx] = field((gx - 1) * G * fk + li * 11.3, (gy - 1) * G * fk - li * 7.1, tt);
      }
      for (let j = -span; j <= span; j += 1) {
        for (let i = -span; i <= span; i += 1) {
          const lx = i * pitch;
          const ly = j * pitch;
          const x = ox + lx * cos - ly * sin;
          const y = oy + lx * sin + ly * cos;
          if (x < -pitch || y < -pitch || x > s.w + pitch || y > s.h + pitch) continue;
          const fx = x / G + 1;
          const fy = y / G + 1;
          const ix = Math.max(0, Math.min(gw - 2, Math.floor(fx)));
          const iy = Math.max(0, Math.min(gh - 2, Math.floor(fy)));
          const ux = fx - ix;
          const uy = fy - iy;
          const o = iy * gw + ix;
          let v = (dens[o] * (1 - ux) + dens[o + 1] * ux) * (1 - uy) + (dens[o + gw] * (1 - ux) + dens[o + gw + 1] * ux) * uy;
          v = (v - 0.5 - li * 0.02) * 2.6;
          if (v <= 0.04) continue;
          const k = Math.min(ink.steps - 1, Math.round(Math.sqrt(clamp01(v)) * (ink.steps - 1)));
          if (k < 2) continue;
          ctx.drawImage(ink.canvas, k * size * d, 0, size * d, size * d, x - size / 2, y - size / 2, size, size);
        }
      }
    });
    ctx.globalCompositeOperation = "source-over";
  },
};
