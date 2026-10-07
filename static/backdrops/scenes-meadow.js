// Scenes: meadow, pond.
// Verbatim extraction from static/chat-backdrop.js; chunk map: backdrop-manifest.json in the snapshot parent.

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

// Pond: a pixel lily pond along the floor. Reeds and cattails sway at the
// edges, lily pads bob on shimmering water with lotus open on some, and a
// koi glides beneath. Everything above the water is mirrored in it,
// wavering. Dragonflies dart over it; on dark themes fireflies drift
// above the water and blink in their reflections too.
export const pond = {
  fps: 20,
  setup(s) {
    s.time = Math.random() * 100;
  },
  resize(s) {
    cells(s, Math.max(2, Math.round(4 * s.k)));
    const { cols, rows } = s;
    const fl = flora(s.pal);
    s.fl = fl;
    const H = Math.min(rows, cols * 1.2);
    s.H = H;
    s.wl = Math.round(rows - Math.max(4, H * 0.16));
    s.water = s.pal.dark ? mix(fl.sky[0], s.pal.ink, 0.5) : mix(fl.sky[2], s.pal.ink, 0.4);
    s.shine = mix(fl.sky[2], s.pal.tint, 0.4);
    const R = seeded(newSeed());
    // The far bank: a low dithered strip behind the water.
    s.base = new Uint8ClampedArray(s.img.data.length);
    const bank = mix(fl.moss[0], s.pal.ink, 0.3);
    for (let x = 0; x < cols; x += 1) {
      const top = s.wl - Math.round(1 + 5 * noise(x * 0.04, 2.7) * noise(x * 0.011, 5.3));
      for (let y = top; y <= s.wl; y += 1) if (y === top || dither(x, y) > 0) put(s.base, y * cols + x, bank, (y === top ? 0.6 : 0.4) * (s.pal.dark ? 1 : 0.5));
    }
    // Reeds crowd the two edges and thin toward the middle.
    s.reeds = [];
    for (let i = 0, n = Math.round(cols * 0.4); i < n; i += 1) {
      const edge = Math.pow(R(), 1.6) * 0.32;
      const x = Math.floor((R() < 0.5 ? edge : 1 - edge) * cols);
      s.reeds.push({ x, h: Math.max(3, Math.round(H * R.range(0.1, 0.34) * (1 - edge * 1.6))), set: R.range(-0.18, 0.18), cat: R() < 0.2, tone: R() < 0.35 ? 2 : 1 });
    }
    s.pads = [];
    const depth = rows - s.wl;
    for (let i = 0, n = Math.max(3, Math.round(cols / 20)); i < n; i += 1) {
      const x = R() * cols;
      const u = x / cols;
      if (u > 0.3 && u < 0.7 && R() < 0.6) continue;
      const w = Math.max(2, Math.round(R.range(3, 7) * Math.max(0.5, H / 200)));
      s.pads.push({ x, y: s.wl + 1 + Math.floor(R() * Math.max(1, depth * 0.55)), w, lotus: R() < 0.45 ? (R() < 0.3 ? "bud" : "open") : null, ph: R() * TAU });
    }
    s.pads.sort((a, b) => a.y - b.y);
    s.koi = { seed: R() * 100 };
    s.ripples = [];
    s.glow = mix(fromHsl(68, 0.9, 0.66), s.pal.tint, 0.2);
    s.bugs = s.pal.dark
      ? Array.from({ length: Math.round(Math.min(26, 8 + cols / 18)) }, () => ({ seed: Math.random() * 1000, rate: rand(0.6, 1.3), phase: Math.random() * TAU }))
      : [];
    s.flies = Array.from({ length: s.pal.dark ? 1 : 3 }, (_, i) => ({ x: cols * (i % 2 ? 0.85 : 0.15), y: s.wl - H * 0.1, tx: 0, ty: 0, next: 0, hue: [fl.sky[1], fl.accent[1], fl.poppy[1]][i % 3] }));
  },
  reed(s, px, r, t) {
    const { wl, fl, cols } = s;
    const lean = (gust(r.x / cols, t) * 0.28 + r.set) * r.h;
    let tx = r.x;
    for (let k = 0; k < r.h; k += 1) {
      const f = k / r.h;
      tx = r.x + Math.round(lean * f * f);
      plot(s, px, tx, wl - k, k >= r.h - 2 ? fl.leaf[2] : fl.leaf[r.tone]);
    }
    if (r.cat) {
      // A cattail: a brown club with a thin spike above it.
      for (let k = 0; k < 4; k += 1) plot(s, px, tx, wl - r.h - k, fl.bark[k === 3 ? 2 : k ? 1 : 0]);
      plot(s, px, tx, wl - r.h - 5, fl.leaf[1]);
    }
  },
  draw(s, t, dt) {
    s.time += Math.min(dt, 0.2);
    const time = s.time;
    const { cols, rows, wl, fl, H } = s;
    const px = s.img.data;
    px.set(s.base);
    // Above the water: everything drawn here is mirrored below.
    for (const r of s.reeds) this.reed(s, px, r, time);
    if (s.pal.dark) {
      for (const b of s.bugs) {
        const x = cols * (0.5 + 0.62 * (noise(b.seed, time * 0.03) - 0.5) * 2);
        const y = wl - 2 - H * 0.55 * noise(b.seed + 50, time * 0.04);
        const p = Math.pow(Math.max(0, Math.sin(time * b.rate + b.phase)), 3);
        if (p < 0.03) continue;
        plot(s, px, x, y, s.glow, p);
        for (const [dx, dy] of [[1, 0], [-1, 0], [0, 1], [0, -1]]) plot(s, px, x + dx, y + dy, s.glow, p * 0.4);
      }
    }
    // Dragonflies hover, then dart to a new spot.
    for (const f of s.flies) {
      if (time > f.next) {
        f.next = time + rand(1, 3);
        f.tx = cols * (Math.random() < 0.8 ? (Math.random() < 0.5 ? rand(0.02, 0.3) : rand(0.7, 0.98)) : rand(0.3, 0.7));
        f.ty = wl - 3 - H * rand(0.02, 0.3);
      }
      f.x += (f.tx - f.x) * Math.min(1, dt * 4);
      f.y += (f.ty - f.y) * Math.min(1, dt * 4);
      const right = f.tx >= f.x;
      sprite(s, px, Math.floor(time * 14) & 1 ? SPRITES.dragonUp : SPRITES.dragonDown, Math.round(f.x) - 2, Math.round(f.y + Math.sin(time * 5) * 0.6) - 1, { a: f.hue, b: fl.white[2], c: fl.bark[0] }, !right);
    }
    // The water: a tinted body with drifting glints, then the mirror.
    for (let y = wl + 1; y < rows; y += 1) {
      const k = (y - wl) / (rows - wl);
      for (let x = 0; x < cols; x += 1) {
        const i = y * cols + x;
        const glint = noise(x * 0.09 + time * (0.25 + k * 0.2), y * 0.8 - time * 0.05);
        if (glint > 0.74 && ((x + y) & 1) === 0) put(px, i, s.shine, 0.35 + (glint - 0.74) * 2.2);
        else put(px, i, s.water, 0.32 + k * 0.28);
      }
    }
    const reach = Math.min(rows - wl - 1, wl);
    for (let k = 1; k <= reach; k += 1) {
      const src = wl - k;
      const dst = wl + k;
      const wobble = Math.round(Math.sin(k * 0.9 + time * 2.2) * Math.min(1.5, k / 5));
      const fade = 0.55 * (1 - k / (reach + 1));
      for (let x = 0; x < cols; x += 1) {
        const sx = x - wobble;
        if (sx < 0 || sx >= cols) continue;
        const o = (src * cols + sx) * 4;
        const a = px[o + 3] / 255;
        if (a < 0.05) continue;
        put(px, dst * cols + x, mix([px[o], px[o + 1], px[o + 2]], s.water, 0.35), Math.max(px[(dst * cols + x) * 4 + 3] / 255, a * fade));
      }
    }
    // A koi under the surface.
    const kp = s.koi;
    const kx = cols * (0.5 + 0.46 * Math.sin(time * 0.021 + kp.seed));
    const ky = wl + 2 + (rows - wl - 4) * noise(time * 0.05, kp.seed);
    const left = Math.cos(time * 0.021 + kp.seed) < 0;
    const ks = { a: fl.poppy[1], b: fl.white[1], c: fl.poppy[0] };
    const map = SPRITES.koi;
    for (let j = 0; j < map.length; j += 1) {
      for (let i = 0; i < map[0].length; i += 1) {
        const c = ks[map[j][left ? map[0].length - 1 - i : i]];
        if (c) plot(s, px, Math.round(kx) + i, Math.round(ky) + j, mix(c, s.water, 0.3), 0.75);
      }
    }
    // Rings where something breaks the surface.
    if (Math.random() < dt * 0.35) s.ripples.push({ x: rand(0.02, 0.98) * cols, y: wl + 1 + Math.random() * (rows - wl - 2), age: 0 });
    s.ripples = s.ripples.filter((r) => {
      r.age += dt;
      const rad = r.age * 3.5;
      const a = 0.7 * (1 - r.age / 2.2);
      if (a <= 0) return false;
      for (let n = 0, m = Math.max(8, Math.round(rad * 6)); n < m; n += 1) {
        const ang = (n / m) * TAU;
        plot(s, px, r.x + Math.cos(ang) * rad, r.y + Math.sin(ang) * rad * 0.3, s.shine, a);
      }
      return true;
    });
    // Lily pads float on top, each with a slit to its heart.
    for (const p of s.pads) {
      const x0 = Math.round(p.x + Math.sin(time * 0.3 + p.ph) * 1.2);
      const hh = Math.max(1, Math.round(p.w * 0.3));
      for (let j = -hh; j <= hh; j += 1) {
        const half = Math.round(p.w * Math.sqrt(Math.max(0, 1 - (j / (hh + 0.5)) ** 2)));
        for (let i = -half; i <= half; i += 1) {
          if (j === 0 && i > 0) continue;
          const c = j < 0 ? (dither(x0 + i, p.y + j) > 0 ? fl.leaf[2] : fl.leaf[1]) : j === hh ? fl.leaf[0] : fl.leaf[1];
          plot(s, px, x0 + i, p.y + j, c);
        }
      }
      if (p.lotus === "open") sprite(s, px, SPRITES.lotus, x0 - 2, p.y - hh - 4, { a: fl.blossom[1], b: fl.blossom[2], c: fl.blossom[3], d: fl.gold[2] });
      else if (p.lotus === "bud") sprite(s, px, SPRITES.lotusBud, x0 - 1, p.y - hh - 3, { a: fl.blossom[1], b: fl.blossom[2] });
    }
    blit(s, false);
  },
};
