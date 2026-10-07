// Scene: koi.
// Verbatim extraction from static/chat-backdrop.js; chunk map: backdrop-manifest.json in the snapshot parent.

import { TAU } from "./env.js";
import { mix } from "./color.js";
import { noise, rand } from "./noise.js";
import { cells, put, blit } from "./kit.js";
import { dither, plot } from "./pixel.js";
import { seeded, newSeed, flora } from "./botany.js";

// Koi: a pond from above. Koi in kohaku, showa, ogon and asagi patterns
// wind slowly through dappled water, flexing as they turn and sinking
// and rising as they go, with their shadows on the bed below. Lily pads,
// a few in flower, float on top; light plays over the water, rings open
// where a fish mouths the surface, and now and then a petal drifts past.
export const koi = {
  fps: 12,
  setup(s) {
    s.time = Math.random() * 100;
  },
  resize(s) {
    cells(s, Math.max(2, Math.round(4 * s.k)));
    const { cols, rows, pal } = s;
    const R = seeded(newSeed());
    const fl = flora(pal);
    s.fl = fl;
    const dark = pal.dark;
    s.deep = dark ? mix(fl.sky[0], pal.ink, 0.62) : mix(fl.sky[1], pal.ink, 0.72);
    s.shallow = dark ? mix(fl.moss[0], pal.ink, 0.45) : mix(fl.moss[1], pal.ink, 0.6);
    s.shine = mix(fl.sky[2], pal.tint, 0.3);
    s.shade = mix(pal.ink, [0, 0, 0], dark ? 0.6 : 0.25);
    s.base = new Uint8ClampedArray(s.img.data.length);
    const off = R() * 100;
    for (let y = 0; y < rows; y += 1) {
      for (let x = 0; x < cols; x += 1) {
        const d = noise(x * 0.03 + off, y * 0.03) + dither(x, y) * 0.12;
        put(s.base, y * cols + x, d > 0.52 ? s.deep : s.shallow, 0.42);
      }
    }
    const unit = Math.min(cols, rows);
    s.pads = [];
    for (let i = 0, n = Math.max(4, Math.round(cols * rows / 2600)); i < n; i += 1) {
      const x = R() * cols;
      if (Math.abs(x / cols - 0.5) < 0.2 && R() < 0.7) continue;
      s.pads.push({ x, y: R() * rows, r: Math.max(2, Math.round(R.range(0.035, 0.07) * unit)), notch: R() * TAU, flower: R() < 0.3, ph: R() * TAU });
    }
    const kinds = ["kohaku", "showa", "ogon", "asagi", "kohaku"];
    s.fish = Array.from({ length: Math.max(3, Math.min(7, Math.round(cols / 55))) }, (_, i) => {
      const L = Math.max(6, Math.round(unit * R.range(0.11, 0.17)));
      const f = { x: R() * cols, y: R() * rows, a: R() * TAU, L, v: R.range(2, 4) * Math.max(0.6, unit / 180), seed: R() * 100, kind: kinds[i % kinds.length], trail: [] };
      for (let n = 0; n < 120; n += 1) f.trail.push([f.x - Math.cos(f.a) * n * 0.3, f.y - Math.sin(f.a) * n * 0.3]);
      return f;
    });
    s.rings = [];
    s.petals = [];
  },
  // The colour of a koi at u along its body (0 head, 1 tail).
  skin(s, f, u, side) {
    const fl = s.fl;
    const white = fl.white[2];
    const red = fl.poppy[1];
    const patch = noise(u * 5 + f.seed, side * 0.8 + f.seed) > 0.52;
    if (f.kind === "ogon") return u < 0.08 ? fl.gold[2] : fl.gold[1];
    if (f.kind === "asagi") return side > 0.6 ? fl.poppy[2] : mix(fl.sky[1], white, 0.35);
    if (f.kind === "showa") return noise(u * 6 + f.seed + 20, side) > 0.6 ? mix(s.pal.ink, [20, 20, 24], 0.5) : patch ? red : white;
    return patch && u > 0.06 ? red : white;
  },
  spine(f) {
    // Walk back along the head's trail at even steps.
    const pts = [[f.x, f.y]];
    const seg = f.L / 12;
    let want = seg;
    let dist = 0;
    for (let i = 1; i < f.trail.length && pts.length < 13; i += 1) {
      const [ax, ay] = f.trail[i - 1];
      const [bx, by] = f.trail[i];
      const d = Math.hypot(bx - ax, by - ay);
      while (dist + d >= want && pts.length < 13) {
        const k = (want - dist) / (d || 1);
        pts.push([ax + (bx - ax) * k, ay + (by - ay) * k]);
        want += seg;
      }
      dist += d;
    }
    while (pts.length < 13) pts.push(pts[pts.length - 1]);
    return pts;
  },
  drawFish(s, px, f, time, shadow) {
    const pts = this.spine(f);
    const depth = 0.5 + 0.5 * noise(f.seed, time * 0.05);
    const off = shadow ? 1 + Math.round(depth * 2) : 0;
    const W = Math.max(1, f.L * 0.13);
    for (let n = 0; n < pts.length - 1; n += 1) {
      const u = n / (pts.length - 1);
      const [x0, y0] = pts[n];
      const [x1, y1] = pts[n + 1];
      const w = W * (u < 0.3 ? 0.7 + u : Math.max(0.25, (1 - u) * 1.45));
      const nx = -(y1 - y0);
      const ny = x1 - x0;
      const nl = Math.hypot(nx, ny) || 1;
      for (let sub = 0; sub < 2; sub += 1) {
        const cx = x0 + (x1 - x0) * sub * 0.5;
        const cy = y0 + (y1 - y0) * sub * 0.5;
        for (let k = -Math.ceil(w); k <= Math.ceil(w); k += 1) {
          if (Math.abs(k) > w + 0.2) continue;
          const x = cx + (nx / nl) * k + off;
          const y = cy + (ny / nl) * k + off;
          if (shadow) plot(s, px, x, y, s.shade, 0.35);
          else plot(s, px, x, y, mix(this.skin(s, f, u, Math.abs(k) / (w + 0.5)), s.deep, 0.5 - depth * 0.45), 0.55 + depth * 0.4);
        }
      }
      // Pectoral fins just behind the head.
      if (n === 2 && !shadow) {
        const flap = 1 + Math.round(Math.sin(time * 4 + f.seed));
        for (const side of [-1, 1]) plot(s, px, x0 + (nx / nl) * side * (w + flap), y0 + (ny / nl) * side * (w + flap), s.fl.white[1], 0.5 * depth + 0.2);
      }
    }
    // The tail fans and wags.
    const [tx, ty] = pts[pts.length - 1];
    const [ux, uy] = pts[pts.length - 2];
    const back = Math.atan2(ty - uy, tx - ux);
    const wag = Math.sin(time * 5 + f.seed) * 0.35;
    for (const side of [-0.5, 0.5]) {
      for (let k = 1; k <= Math.max(2, W * 1.6); k += 1) {
        const a = back + side + wag;
        const x = tx + Math.cos(a) * k + off;
        const y = ty + Math.sin(a) * k + off;
        if (shadow) plot(s, px, x, y, s.shade, 0.25);
        else plot(s, px, x, y, mix(this.skin(s, f, 1, 0), s.deep, 0.4), 0.45 + depth * 0.3);
      }
    }
    if (!shadow && depth > 0.9 && Math.random() < 0.02) s.rings.push({ x: f.x, y: f.y, age: 0 });
  },
  draw(s, t, dt) {
    s.time += Math.min(dt, 0.2);
    dt = Math.min(dt, 0.2);
    const time = s.time;
    const { cols, rows, fl } = s;
    const px = s.img.data;
    px.set(s.base);
    // Light playing over the water, sampled every third cell.
    for (let y = 0; y < rows; y += 3) {
      for (let x = (y / 3) & 1; x < cols; x += 3) {
        const v = Math.abs(noise(x * 0.07 + time * 0.18, y * 0.07) - noise(x * 0.07 - time * 0.13 + 5, y * 0.07 + time * 0.09));
        if (v < 0.035) {
          const a = 0.3 * (1 - v / 0.035);
          plot(s, px, x, y, s.shine, a);
          plot(s, px, x + 1, y, s.shine, a * 0.7);
        }
      }
    }
    // Fish swim: wander, turn back from the edges, favour the margins.
    for (const f of s.fish) {
      let turn = (noise(f.seed, time * 0.12) - 0.5) * 2.2;
      const m = f.L * 0.8;
      const want = (tx, ty) => {
        const d = Math.atan2(ty - f.y, tx - f.x) - f.a;
        return Math.atan2(Math.sin(d), Math.cos(d));
      };
      if (f.x < m || f.x > cols - m || f.y < m || f.y > rows - m) turn += want(cols / 2, rows / 2) * 1.5;
      f.a += turn * dt;
      const v = f.v * (0.7 + 0.6 * noise(f.seed + 7, time * 0.2));
      f.x += Math.cos(f.a) * v * dt;
      f.y += Math.sin(f.a) * v * dt;
      f.trail.unshift([f.x, f.y]);
      if (f.trail.length > 160) f.trail.pop();
    }
    for (const f of s.fish) this.drawFish(s, px, f, time, true);
    for (const f of s.fish) this.drawFish(s, px, f, time, false);
    // Rings where something breaks the surface.
    if (Math.random() < dt * 0.25) s.rings.push({ x: rand(0, 1) * cols, y: rand(0, 1) * rows, age: 0 });
    s.rings = s.rings.filter((r) => {
      r.age += dt;
      const rad = 1 + r.age * 3.2;
      const a = 0.55 * (1 - r.age / 2.4);
      if (a <= 0) return false;
      for (let n = 0, m = Math.max(10, Math.round(rad * 7)); n < m; n += 1) {
        const ang = (n / m) * TAU;
        plot(s, px, r.x + Math.cos(ang) * rad, r.y + Math.sin(ang) * rad, s.shine, a);
      }
      return true;
    });
    // Lily pads, seen from above: a disc with a notch, a shadow, veins.
    for (const p of s.pads) {
      const x0 = p.x + Math.sin(time * 0.2 + p.ph) * 0.8;
      const y0 = p.y + Math.cos(time * 0.17 + p.ph) * 0.6;
      const rot = p.notch + Math.sin(time * 0.1 + p.ph) * 0.15;
      for (const shadow of [true, false]) {
        const o = shadow ? 1 : 0;
        for (let j = -p.r; j <= p.r; j += 1) {
          for (let i = -p.r; i <= p.r; i += 1) {
            const d = Math.hypot(i, j);
            if (d > p.r + 0.3) continue;
            const a = Math.atan2(j, i) - rot;
            if (Math.abs(Math.atan2(Math.sin(a), Math.cos(a))) < 0.32 && d > 0.6) continue;
            const x = Math.round(x0 + i) + o;
            const y = Math.round(y0 + j) + o;
            if (shadow) { plot(s, px, x, y, s.shade, 0.3); continue; }
            const vein = p.r > 3 && Math.abs(Math.sin(a * 4)) < 0.18;
            const c = d > p.r - 0.7 ? fl.leaf[0] : vein ? fl.leaf[2] : dither(x, y) > 0 ? fl.leaf[1] : mix(fl.leaf[1], fl.leaf[2], 0.4);
            plot(s, px, x, y, c, 0.92);
          }
        }
      }
      if (p.flower) {
        const cx = x0 - Math.cos(rot) * p.r * 0.3;
        const cy = y0 - Math.sin(rot) * p.r * 0.3;
        const pr = Math.max(1.5, p.r * 0.55);
        for (let n = 0; n < 8; n += 1) {
          const a = (n / 8) * TAU + time * 0.02;
          for (let k = 1; k <= pr; k += 1) plot(s, px, cx + Math.cos(a) * k, cy + Math.sin(a) * k, fl.blossom[k > pr * 0.6 ? 3 : 1], 0.95);
        }
        plot(s, px, cx, cy, fl.gold[2], 1);
      }
    }
    // A petal drifts across now and then, turning as it goes.
    if (Math.random() < dt * 0.08) s.petals.push({ x: -2, y: rand(0.05, 0.95) * rows, v: rand(1.5, 3), ph: Math.random() * TAU, age: 0 });
    s.petals = s.petals.filter((p) => {
      p.age += dt;
      p.x += p.v * dt;
      p.y += Math.sin(time * 0.5 + p.ph) * dt * 0.6;
      if (p.x > cols + 2) return false;
      const flip = Math.sin(time * 1.3 + p.ph) > 0;
      plot(s, px, p.x, p.y, fl.blossom[2], 0.9);
      plot(s, px, p.x + (flip ? 1 : 0), p.y + (flip ? 0 : 1), fl.blossom[3], 0.8);
      return true;
    });
    blit(s, false);
  },
};
