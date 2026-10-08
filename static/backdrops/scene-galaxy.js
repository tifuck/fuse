// Scene: galaxy.
// Verbatim extraction from static/chat-backdrop.js; chunk map: backdrop-manifest.json in the snapshot parent.
// Split from scenes-orbit-galaxy.js; one scene per file.

import { TAU } from "./env.js";
import { mix, clamp01 } from "./color.js";
import { cells, put, blit } from "./kit.js";
import { dither, plot } from "./pixel.js";
import { seeded, newSeed } from "./botany.js";
import { cosmos, starfield, twinkle, sphere } from "./space.js";

// Galaxy: a spiral galaxy seen from above and a little tilted, in pixels.
// Two arms of stars wind out of a warm core, dotted with pink star-forming
// knots, and the whole disc turns very slowly. Light piles up where stars
// crowd, so the arms glow and fray at their tips. A small companion galaxy
// and a far elliptical sit off to the side of the field.
export const galaxy = {
  fps: 15,
  setup(s) {
    s.time = Math.random() * 100;
  },
  resize(s) {
    cells(s, Math.max(2, Math.round(4 * s.k)));
    const { cols, rows, pal } = s;
    const R = seeded(newSeed());
    const k = cosmos(pal);
    s.star = k.star;
    s.core = [0.5, 0.66, 0.82].map((l) => k.tone(40, 0.7, l));
    s.arm = [0.4, 0.56, 0.72, 0.86].map((l) => mix(k.tone(215, 0.5, l), pal.spectrum[4], 0.2));
    s.knot = k.tone(330, 0.7, 0.66);
    s.stars = starfield(R, cols, rows, 170);
    s.acc = new Float32Array(cols * rows);
    s.gx = cols * (R() < 0.5 ? 0.72 : 0.28);
    s.gy = rows * 0.42;
    s.gr = Math.min(cols * 0.22, rows * 0.6);
    s.tilt = R.range(-0.5, -0.2);
    s.flat = R.range(0.42, 0.55);
    // Each star keeps its place along the arm: radius, arm, and scatter.
    const n = Math.round(Math.max(1200, Math.min(7000, cols * rows * 0.09)));
    s.parts = Array.from({ length: n }, (_, i) => {
      const bulge = i < n * 0.22;
      const r = bulge ? Math.abs(R() + R() - 1) * 0.22 : 0.08 + Math.pow(R(), 0.8) * 0.92;
      return { r, arm: i & 1, off: bulge ? R() * TAU : (R() + R() + R() - 1.5) * (0.2 + r * 0.22), bulge, w: bulge ? 0.6 : R.range(0.35, 0.9), knot: !bulge && r > 0.25 && R() < 0.02 };
    });
    s.moons = [
      { x: s.gx + (s.gx > cols / 2 ? -1 : 1) * s.gr * 1.05, y: s.gy + s.gr * 0.55, r: Math.max(2, s.gr * 0.07), tone: 0.55 },
      { x: s.gx > cols / 2 ? cols * 0.1 : cols * 0.9, y: rows * 0.8, r: Math.max(1.5, s.gr * 0.04), tone: 0.4 },
    ];
  },
  draw(s, t, dt) {
    s.time += Math.min(dt, 0.2);
    const time = s.time;
    const { cols, rows, acc } = s;
    const px = s.img.data;
    px.fill(0);
    twinkle(s, px, s.stars, s.star, time);
    acc.fill(0);
    const spin = time * 0.012;
    const ct = Math.cos(s.tilt);
    const st = Math.sin(s.tilt);
    const add = (x, y, v) => {
      const xi = Math.round(x);
      const yi = Math.round(y);
      if (xi < 0 || yi < 0 || xi >= cols || yi >= rows) return;
      acc[yi * cols + xi] += v;
    };
    const knots = [];
    for (const p of s.parts) {
      // Arms are logarithmic spirals; stars near the core turn faster.
      const a = p.bulge ? p.off + spin * 3 : p.arm * Math.PI + Math.log(p.r + 0.04) * 3.1 + p.off + spin / (0.3 + p.r);
      const rr = p.r * s.gr;
      const ex = Math.cos(a) * rr;
      const ey = Math.sin(a) * rr * s.flat;
      const x = s.gx + ex * ct - ey * st;
      const y = s.gy + ex * st + ey * ct;
      add(x, y, p.w);
      if (p.knot) knots.push([x, y]);
    }
    for (let y = 0; y < rows; y += 1) {
      for (let x = 0; x < cols; x += 1) {
        const i = y * cols + x;
        const v = acc[i];
        if (v <= 0) continue;
        const dx = x - s.gx;
        const dy = (y - s.gy) / s.flat;
        const core = 1 - clamp01(Math.sqrt(dx * dx + dy * dy) / (s.gr * 0.3));
        const lv = v + dither(x, y) * 0.4;
        if (lv < 0.35) continue;
        const c = core > 0.25 ? s.core[Math.min(2, Math.floor(lv * 0.9))] : s.arm[Math.min(3, Math.floor(lv * 1.1))];
        put(px, i, c, Math.min(1, 0.45 + v * 0.3));
      }
    }
    // The core burns through as a soft, dithered glow.
    const cr = Math.max(2, Math.round(s.gr * 0.11));
    sphere(s, px, s.gx, s.gy, cr, (nx, ny, nz, x, y) => (nz * nz + dither(x, y) * 0.5 > 0.4 ? s.core[nz > 0.75 ? 2 : nz > 0.45 ? 1 : 0] : null), 0.85);
    for (const [x, y] of knots) {
      const a = 0.6 + 0.4 * Math.sin(time * 1.3 + x);
      plot(s, px, x, y, s.knot, a);
      plot(s, px, x + 1, y, s.knot, a * 0.4);
    }
    // Companions: soft dithered ellipticals.
    for (const m of s.moons) {
      const r = Math.round(m.r);
      for (let j = -r; j <= r; j += 1) {
        for (let i = -r * 1.5; i <= r * 1.5; i += 1) {
          const d = Math.sqrt((i / 1.5) ** 2 + j * j) / (r + 0.5);
          if (d > 1) continue;
          if (1 - d + dither(m.x + i, m.y + j) * 0.5 < 0.35) continue;
          plot(s, px, m.x + i, m.y + j, s.core[d < 0.35 ? 2 : 1], (1 - d) * m.tone);
        }
      }
    }
    blit(s, false);
  },
};
