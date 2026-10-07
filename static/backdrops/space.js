// Space kit shared by the space family: cosmos palette, starfields, meteors, shaded spheres.
// Verbatim extraction from static/chat-backdrop.js; chunk map: backdrop-manifest.json in the snapshot parent.

import { TAU } from "./env.js";
import { toHsl, fromHsl, mix } from "./color.js";
import { rand } from "./noise.js";
import { put } from "./kit.js";
import { plot, dither } from "./pixel.js";

// —— Space kit ——
// Shared by the space family (Orbit, Galaxy, Eclipse, Belt, Contact).
// Colours are pitched to the ground like the garden's and pulled a little
// toward the accent; stars are the theme's text colour, so they show on
// light grounds too.
export function cosmos(pal) {
  const [hue, s0] = toHsl(pal.brass);
  const sat = Math.max(0.4, Math.min(0.75, s0));
  const tone = (h, sv, l) => mix(fromHsl(h, sv, pal.dark ? l : l * 0.78), pal.brass, 0.1);
  return { hue, sat, tone, star: mix(pal.mist, pal.tint, 0.3), grey: [0.34, 0.56, 0.78].map((l) => tone(214, 0.1, l)) };
}
// Stars in three depths: most faint and far, a few near and bright.
export function starfield(R, cols, rows, per = 130) {
  const n = Math.round(Math.max(28, Math.min(900, (cols * rows) / per)));
  return Array.from({ length: n }, () => {
    const z = R();
    return { x: R() * cols, y: R() * rows, z: z < 0.6 ? 0 : z < 0.9 ? 1 : 2, ph: R() * TAU, rate: R.range(0.6, 2.4), big: R() < 0.12 };
  });
}
// Twinkle them; drift slides each depth left at its own pace, in cells/s.
export function twinkle(s, px, list, color, time, drift = 0) {
  const { cols } = s;
  for (const st of list) {
    const x = drift ? (((st.x - time * drift * [0.2, 0.5, 1][st.z]) % cols) + cols) % cols : st.x;
    const tw = 0.3 + 0.7 * Math.pow(Math.sin(time * st.rate + st.ph) * 0.5 + 0.5, 2);
    const a = (st.z === 0 ? 0.4 : st.z === 1 ? 0.65 : 0.9) * tw;
    plot(s, px, x, st.y, color, a);
    if (st.big && tw > 0.55) for (const [dx, dy] of [[1, 0], [-1, 0], [0, 1], [0, -1]]) plot(s, px, x + dx, st.y + dy, color, a * 0.45);
  }
}
// Shooting stars above maxY, a few a minute.
export function meteors(s, px, dt, maxY, head, tail, rate = 0.14) {
  s.shots = s.shots || [];
  if (Math.random() < dt * rate) {
    const right = Math.random() < 0.5;
    s.shots.push({ x: rand(0.1, 0.9) * s.cols, y: rand(0, 0.3) * maxY, vx: (right ? 1 : -1) * rand(50, 90), vy: rand(14, 30), age: 0 });
  }
  s.shots = s.shots.filter((sh) => {
    sh.age += dt;
    sh.x += sh.vx * dt;
    sh.y += sh.vy * dt;
    if (sh.age > 1.2 || sh.y > maxY) return false;
    const fade = 1 - sh.age / 1.2;
    for (let k = 0; k < 14; k += 1) plot(s, px, sh.x - (sh.vx / 60) * k * 1.1, sh.y - (sh.vy / 60) * k * 1.1, k < 2 ? head : tail, fade * (1 - k / 14) * 0.95);
    return true;
  });
}
// A ball of radius r: shade(nx, ny, nz, x, y) gives each cell its colour
// (or null to leave it), with the surface normal to light it by.
export function sphere(s, px, cx, cy, r, shade, alpha = 0.95) {
  const r2 = r * r;
  for (let j = -r; j <= r; j += 1) {
    const y = Math.round(cy + j);
    if (y < 0 || y >= s.rows) continue;
    for (let i = -r; i <= r; i += 1) {
      const d2 = i * i + j * j;
      if (d2 > r2) continue;
      const x = Math.round(cx + i);
      if (x < 0 || x >= s.cols) continue;
      const c = shade(i / r, j / r, Math.sqrt(1 - d2 / r2), x, y);
      if (c) put(px, y * s.cols + x, c, alpha);
    }
  }
}
// Light (0..1-ish) to a four-step ramp index, dithered at the steps.
export const step4 = (v, x, y) => {
  v += dither(x, y) * 0.3;
  return v < 0.15 ? 0 : v < 0.5 ? 1 : v < 0.85 ? 2 : 3;
};
