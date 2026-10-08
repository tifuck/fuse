// Scene: meadow.
// Verbatim extraction from static/chat-backdrop.js; chunk map: backdrop-manifest.json in the snapshot parent.
// Split from scenes-meadow.js; one scene per file.

import { TAU } from "./env.js";
import { mix, fromHsl } from "./color.js";
import { noise, rand, pick } from "./noise.js";
import { cells, put, blit } from "./kit.js";
import { dither, plot, sprite, SPRITES } from "./pixel.js";
import { seeded, newSeed, flora, gust } from "./botany.js";

// Meadow: a pixel field of grass along the floor under a faint far hill,
// in three depths, with the wind rolling across it in waves. Wildflowers
// nod on taller stems. Fireflies drift and blink above it on dark themes;
// on light themes a few butterflies work the flowers instead.
export const meadow = {
  fps: 20,
  setup(s) {
    s.time = Math.random() * 100;
  },
  resize(s) {
    cells(s, Math.max(2, Math.round(4 * s.k)));
    const { cols, rows } = s;
    const fl = flora(s.pal);
    s.fl = fl;
    s.base = new Uint8ClampedArray(s.img.data.length);
    // The far hill: a soft dithered band behind the grass.
    const H = Math.min(rows, cols * 1.2);
    s.H = H;
    const hill = mix(fl.moss[0], s.pal.ink, 0.35);
    for (let x = 0; x < cols; x += 1) {
      const top = Math.floor(rows - H * (0.17 + 0.07 * noise(x * 0.018, 4.2) + 0.03 * noise(x * 0.06, 7)));
      for (let y = Math.max(0, top); y < rows; y += 1) {
        const f = (y - top) / (rows - top);
        if (y === top || f + dither(x, y) * 0.5 > 0.25) put(s.base, y * cols + x, hill, (y === top ? 0.55 : 0.28) * (s.pal.dark ? 1 : 0.6));
      }
    }
    const R = seeded(newSeed());
    const layers = [
      { n: 1, h: [0.05, 0.1], body: mix(fl.leaf[0], s.pal.ink, 0.3), tip: fl.leaf[0], bend: 0.25 },
      { n: 0.75, h: [0.07, 0.14], body: fl.leaf[0], tip: fl.leaf[1], bend: 0.3 },
      { n: 0.45, h: [0.08, 0.19], body: fl.leaf[1], tip: fl.leaf[2], bend: 0.36 },
    ];
    const dry = [mix(fl.gold[0], fl.leaf[0], 0.65), mix(fl.gold[1], fl.leaf[1], 0.55)];
    s.blades = [];
    layers.forEach((L, li) => {
      for (let i = 0, n = Math.round(cols * L.n); i < n; i += 1) {
        const isDry = R() < 0.07;
        // Every blade has its own set, so the field has texture in a calm.
        s.blades.push({ x: Math.floor(R() * cols), h: Math.max(2, Math.round(H * R.range(L.h[0], L.h[1]))), bend: L.bend, set: R.range(-0.25, 0.25), body: isDry ? dry[0] : L.body, tip: isDry ? dry[1] : L.tip, li });
      }
    });
    const kinds = [
      { c: fl.poppy[1], d: fl.bark[0] },
      { c: fl.sky[1], d: fl.white[2] },
      { c: fl.white[1], d: fl.gold[1] },
      { c: fl.gold[2], d: fl.gold[0] },
      { c: fl.violet[1], d: fl.gold[2] },
    ];
    s.flowers = [];
    for (let i = 0, n = Math.round(cols * 0.08); i < n; i += 1) {
      s.flowers.push({ x: Math.floor(R() * cols), h: Math.max(3, Math.round(H * R.range(0.1, 0.22))), slots: R.pick(kinds) });
    }
    s.glow = mix(fromHsl(68, 0.9, 0.66), s.pal.tint, 0.2);
    s.bugs = Array.from({ length: s.pal.dark ? Math.round(Math.min(34, 10 + cols / 14)) : 3 }, () => ({
      seed: Math.random() * 1000, rate: rand(0.6, 1.3), phase: Math.random() * TAU, hue: pick([fl.accent[2], fl.gold[2], fl.sky[2], fl.blossom[2]]),
    }));
  },
  blade(s, px, x, h, bend, body, tip, t, set = 0) {
    const { rows, cols } = s;
    const lean = (gust(x / cols, t) * bend + set) * h;
    for (let k = 0; k < h; k += 1) {
      const f = k / h;
      plot(s, px, x + Math.round(lean * f * f), rows - 1 - k, k >= h - 2 ? tip : body);
    }
    return x + Math.round(lean);
  },
  draw(s, t, dt) {
    s.time += Math.min(dt, 0.2);
    const time = s.time;
    const { rows, cols, fl, H } = s;
    const px = s.img.data;
    px.set(s.base);
    for (const b of s.blades) this.blade(s, px, b.x, b.h, b.bend, b.body, b.tip, time, b.set);
    for (const f of s.flowers) {
      const hx = this.blade(s, px, f.x, f.h, 0.34, fl.leaf[1], fl.leaf[1], time);
      sprite(s, px, SPRITES.bloom, hx - 1, rows - 2 - f.h, f.slots);
    }
    if (s.pal.dark) {
      // Fireflies: a wandering glow that swells and goes dark.
      for (const b of s.bugs) {
        const x = cols * (0.5 + 0.62 * (noise(b.seed, time * 0.03) - 0.5) * 2);
        const y = rows - H * (0.12 + 0.9 * noise(b.seed + 50, time * 0.04));
        const p = Math.pow(Math.max(0, Math.sin(time * b.rate + b.phase)), 3);
        if (p < 0.03) continue;
        plot(s, px, x, y, s.glow, p);
        for (const [dx, dy] of [[1, 0], [-1, 0], [0, 1], [0, -1]]) plot(s, px, x + dx, y + dy, s.glow, p * 0.35);
        for (const [dx, dy] of [[1, 1], [-1, 1], [1, -1], [-1, -1], [2, 0], [-2, 0], [0, 2], [0, -2]]) plot(s, px, x + dx, y + dy, s.glow, p * 0.12);
      }
    } else {
      for (const b of s.bugs) {
        const x = cols * (0.5 + 0.55 * (noise(b.seed, time * 0.025) - 0.5) * 2);
        const y = rows - H * (0.22 + 0.25 * noise(b.seed + 50, time * 0.06)) + Math.sin(time * 3 + b.phase) * 1.5;
        sprite(s, px, Math.floor(time * 6 + b.phase) & 1 ? SPRITES.flyOpen : SPRITES.flyShut, Math.round(x) - 2, Math.round(y), { a: fl.bark[0], b: b.hue, c: s.pal.tint });
      }
    }
    blit(s, false);
  },
};
