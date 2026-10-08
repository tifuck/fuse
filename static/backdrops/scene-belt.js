// Scene: belt.
// Verbatim extraction from static/chat-backdrop.js; chunk map: backdrop-manifest.json in the snapshot parent.
// Split from scenes-eclipse-belt-contact.js; one scene per file.

import { TAU } from "./env.js";
import { noise, field, rand } from "./noise.js";
import { cells, blit } from "./kit.js";
import { plot, sprite } from "./pixel.js";
import { seeded, newSeed } from "./botany.js";
import { cosmos, starfield, twinkle, sphere, step4 } from "./space.js";

// Belt: drifting through an asteroid field. Lumpy, cratered rocks tumble
// past in three depths, lit from one side, the near ones big and quick.
// A banded gas giant hangs behind them, and a little mining ship weaves
// among the rocks with its engine flickering.
export const belt = {
  fps: 20,
  setup(s) {
    s.time = Math.random() * 100;
  },
  resize(s) {
    cells(s, Math.max(2, Math.round(4 * s.k)));
    const { cols, rows, pal } = s;
    const R = seeded(newSeed());
    const k = cosmos(pal);
    s.star = k.star;
    s.grey = k.grey;
    s.rock = [[0.22, 0.32, 0.44, 0.58], [0.24, 0.36, 0.48, 0.62]].map((ls, n) => ls.map((l) => k.tone(n ? 24 : 34, n ? 0.18 : 0.1, l)));
    s.giant = [0.26, 0.38, 0.52, 0.68].map((l) => k.tone(k.hue + 180, k.sat * 0.6, l));
    s.giant2 = [0.22, 0.33, 0.46, 0.6].map((l) => k.tone(k.hue + 150, k.sat * 0.7, l));
    s.stars = starfield(R, cols, rows, 150);
    s.gr = Math.round(Math.min(cols, rows) * 0.34);
    s.gcx = cols * (R() < 0.5 ? 0.12 : 0.88);
    s.gcy = rows * 0.28;
    s.rocks = Array.from({ length: Math.round(Math.max(10, Math.min(46, cols * rows / 1300))) }, () => this.rock(s, R() * cols * 1.2 - cols * 0.1));
    s.ship = { x: cols * 0.3, y: rows * 0.5, tx: cols * 0.3, ty: rows * 0.5, next: 0 };
  },
  rock(s, x) {
    const z = Math.random();
    const depth = z < 0.55 ? 0 : z < 0.85 ? 1 : 2;
    const unit = Math.min(s.cols, s.rows);
    const r = Math.max(1.5, unit * [0.018, 0.035, 0.07][depth] * rand(0.7, 1.3));
    const seed = Math.random() * 100;
    // The outline and craters are laid out once, in the rock's own frame.
    const edge = Array.from({ length: 48 }, (_, n) => r * (0.78 + 0.34 * noise(Math.cos((n / 48) * TAU) * 1.4 + seed, Math.sin((n / 48) * TAU) * 1.4 + seed)));
    const span = Math.ceil(r * 1.3);
    const side = span * 2 + 1;
    const pits = new Uint8Array(side * side);
    if (r > 3) for (let j = 0; j < side; j += 1) for (let i = 0; i < side; i += 1) pits[j * side + i] = noise((i - span) * 0.45 + seed, (j - span) * 0.45) > 0.7 ? 1 : 0;
    return {
      x: x ?? s.cols + r * 2, y: rand(-0.05, 1.05) * s.rows, r, depth, v: [2.2, 5, 11][depth] * rand(0.8, 1.2) * Math.max(0.6, s.cols / 300),
      vy: rand(-0.4, 0.4), spin: rand(-0.5, 0.5), a: Math.random() * TAU, kind: Math.random() < 0.5 ? 0 : 1, edge, pits, span, side,
    };
  },
  drawRock(s, px, o) {
    const rr = Math.ceil(o.r * 1.3);
    const ca = Math.cos(o.a);
    const sa = Math.sin(o.a);
    const ramp = s.rock[o.kind];
    const fade = [0.45, 0.7, 0.95][o.depth];
    for (let j = -rr; j <= rr; j += 1) {
      for (let i = -rr; i <= rr; i += 1) {
        const d = Math.hypot(i, j);
        // Lumpy outline: radius wanders with angle in the rock's own frame.
        const ang = Math.atan2(j, i) - o.a;
        const edge = o.edge[((Math.round((ang / TAU) * 48) % 48) + 48) % 48];
        if (d > edge) continue;
        const nx = i / edge;
        const ny = j / edge;
        const nz = Math.sqrt(Math.max(0, 1 - nx * nx - ny * ny));
        let lit = -nx * 0.7 - ny * 0.45 + nz * 0.55;
        // Craters in the rock's own frame, so they turn with it.
        const lx = Math.round(i * ca + j * sa) + o.span;
        const ly = Math.round(-i * sa + j * ca) + o.span;
        if (lx >= 0 && ly >= 0 && lx < o.side && ly < o.side && o.pits[ly * o.side + lx]) lit -= 0.35;
        const x = o.x + i;
        const y = o.y + j;
        plot(s, px, x, y, ramp[step4(lit, Math.round(x), Math.round(y))], fade);
      }
    }
  },
  draw(s, t, dt) {
    s.time += Math.min(dt, 0.2);
    const time = s.time;
    const { cols, rows, pal } = s;
    const px = s.img.data;
    px.fill(0);
    twinkle(s, px, s.stars, s.star, time, 0.6);
    // The gas giant, banded, lit from the left.
    sphere(s, px, s.gcx, s.gcy, s.gr, (nx, ny, nz, x, y) => {
      const lit = -nx * 0.7 - ny * 0.35 + nz * 0.6;
      const b = Math.sin(ny * 11 + noise(nx * 3 + time * 0.03, ny * 8) * 2.4);
      return (b > 0.3 ? s.giant2 : s.giant)[step4(lit, x, y)];
    }, pal.dark ? 0.85 : 0.7);
    dt = Math.min(dt, 0.2);
    for (let d = 0; d < 3; d += 1) {
      for (let n = 0; n < s.rocks.length; n += 1) {
        const o = s.rocks[n];
        if (o.depth !== d) continue;
        o.x -= o.v * dt;
        o.y += o.vy * dt;
        o.a += o.spin * dt;
        if (o.x < -o.r * 2) s.rocks[n] = this.rock(s);
        else this.drawRock(s, px, o);
      }
      // The ship flies between the middle and near rocks.
      if (d === 1) {
        const sh = s.ship;
        if (time > sh.next) {
          sh.next = time + rand(2, 5);
          sh.tx = cols * rand(0.05, 0.95);
          sh.ty = rows * rand(0.1, 0.9);
        }
        sh.x += (sh.tx - sh.x) * Math.min(1, dt * 0.8);
        sh.y += (sh.ty - sh.y) * Math.min(1, dt * 0.8);
        const left = sh.tx < sh.x;
        const g = s.grey;
        sprite(s, px, [" aa   ", "abbbbc", "aaddda", " aa   "], Math.round(sh.x) - 3, Math.round(sh.y) - 2, { a: g[1], b: g[2], c: pal.tint, d: g[0] }, left);
        const flame = 1 + (Math.floor(time * 12) % 3);
        for (let f = 1; f <= flame; f += 1) plot(s, px, sh.x + (left ? 2 + f : -3 - f), sh.y, f === 1 ? pal.tint : pal.brass, 1 - f * 0.25);
      }
    }
    blit(s, false);
  },
};
