// Scenes: riso, warp, mandala, aurora.
// Verbatim extraction from static/chat-backdrop.js; chunk map: backdrop-manifest.json in the snapshot parent.

import { TAU } from "./env.js";
import { css, clamp01, ring } from "./color.js";
import { field, noise, rand } from "./noise.js";
import { atlas } from "./kit.js";

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

// A glyph starfield flying out of a vanishing point: far stars are dots,
// near ones become stars with a streak behind them. The vanishing point
// wanders slowly around the middle of the screen.
export const warp = {
  fps: 30,
  previewK: 0.8,
  chars: "·∙+×*✦",
  setup(s) {
    s.clock = Math.random() * 100;
  },
  resize(s) {
    const pal = s.pal;
    const size = Math.round(16 * s.k);
    s.A = atlas(s, this.chars, [css(pal.mute, 0.8), css(pal.steel, 0.9), css(pal.brass, 1), css(pal.tint, 1), css(pal.spectrum[1], 0.95)], size, size, 13 * s.k);
    const n = Math.round(Math.max(50, Math.min(1400, (s.w * s.h) / 1300)));
    s.stars = Array.from({ length: n }, () => this.star(Math.random()));
  },
  star(z) {
    return { x: rand(-1, 1), y: rand(-1, 1), z: z || 1, px: null, py: null, hue: Math.random() < 0.12 };
  },
  warm(s) {
    for (let i = 0; i < 30; i += 1) this.step(s, 1 / 30);
  },
  step(s, dt) {
    s.clock += dt;
    s.vx = 0.5 + 0.08 * Math.sin(s.clock * 0.05);
    s.vy = 0.5 + 0.06 * Math.sin(s.clock * 0.037 + 1.3);
    const v = 0.09;
    for (const st of s.stars) {
      st.z -= v * dt;
      if (st.z <= 0.03) Object.assign(st, this.star(1));
    }
  },
  draw(s, t, dt) {
    this.step(s, Math.min(dt, 0.1));
    const { ctx, A, pal } = s;
    const cx = s.w * s.vx;
    const cy = s.h * s.vy;
    const f = Math.max(s.w, s.h) * 0.42;
    const size = A.cw;
    ctx.clearRect(0, 0, s.w, s.h);
    ctx.lineCap = "round";
    for (const st of s.stars) {
      const x = cx + (st.x / st.z) * f;
      const y = cy + (st.y / st.z) * f;
      if (x < -20 || y < -20 || x > s.w + 20 || y > s.h + 20) {
        Object.assign(st, this.star(1));
        continue;
      }
      const near = 1 - st.z;
      if (st.px !== null && near > 0.4) {
        ctx.beginPath();
        ctx.moveTo(st.px, st.py);
        ctx.lineTo(x, y);
        ctx.strokeStyle = css(st.hue ? pal.spectrum[1] : pal.steel, (near - 0.4) * 0.9);
        ctx.lineWidth = 1 + near;
        ctx.stroke();
      }
      const g = Math.min(A.n - 1, Math.floor(near * near * A.n * 1.1));
      const tone = st.hue && near > 0.4 ? 4 : near > 0.85 ? 3 : near > 0.6 ? 2 : near > 0.3 ? 1 : 0;
      const big = size * (0.8 + near * near * 1.1);
      const d = A.dpr;
      ctx.drawImage(A.canvas, g * A.cw * d, tone * A.ch * d, A.cw * d, A.ch * d, x - big / 2, y - big / 2, big, big);
      // Remember a point a little behind, so streaks have length.
      st.px = x - (x - cx) * 0.06;
      st.py = y - (y - cy) * 0.06;
    }
  },
};

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
