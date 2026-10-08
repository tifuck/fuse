// Scene: contact.
// Verbatim extraction from static/chat-backdrop.js; chunk map: backdrop-manifest.json in the snapshot parent.
// Split from scenes-eclipse-belt-contact.js; one scene per file.

import { TAU } from "./env.js";
import { mix } from "./color.js";
import { noise, hash, rand } from "./noise.js";
import { cells, put, blit } from "./kit.js";
import { dither, plot, sprite } from "./pixel.js";
import { seeded, newSeed, gust } from "./botany.js";
import { cosmos, starfield, twinkle, meteors, sphere, step4 } from "./space.js";

// Contact: a night on an alien world. Glowing stalks and spotted caps
// grow from violet ground under two moons, and little antennaed locals
// wander about. A mothership hovers over them with its rim lights running
// through chases, alternations and strobes, a pilot in the dome, and now
// and then a tractor beam that pours down and lifts sparks off the ground.
// Two scout saucers dart about the sky.
export const contact = {
  fps: 20,
  setup(s) {
    s.time = Math.random() * 100;
  },
  resize(s) {
    cells(s, Math.max(2, Math.round(4 * s.k)));
    const { cols, rows, pal } = s;
    const R = seeded(newSeed());
    const k = cosmos(pal);
    s.kit = k;
    s.star = k.star;
    s.soil = [0.14, 0.22, 0.32].map((l) => k.tone(276, 0.4, l));
    s.stem = [0.24, 0.34, 0.46].map((l) => k.tone(250, 0.35, l));
    s.glowA = k.tone(168, 0.85, 0.66);
    s.glowB = k.tone(312, 0.8, 0.7);
    s.glowC = k.tone(88, 0.85, 0.66);
    s.hull = [0.2, 0.32, 0.46, 0.64].map((l) => k.tone(228, 0.14, l));
    s.glass = k.tone(180, 0.5, 0.5);
    s.beam = k.tone(165, 0.8, 0.72);
    s.lamps = [pal.fault, k.tone(44, 0.9, 0.66), s.glowA, k.tone(210, 0.85, 0.7), s.glowB, k.tone(120, 0.7, 0.62)];
    s.gh = Math.max(4, Math.round(rows * 0.15));
    s.gy = rows - s.gh;
    s.gtop = Array.from({ length: cols }, (_, x) => Math.round(s.gy - 2.5 * noise(x * 0.025, 7.1) - 1.5 * noise(x * 0.09, 2.2)));
    s.base = new Uint8ClampedArray(s.img.data.length);
    for (let x = 0; x < cols; x += 1) {
      for (let y = s.gtop[x]; y < rows; y += 1) {
        const d = y - s.gtop[x];
        put(s.base, y * cols + x, s.soil[d === 0 ? 2 : d < 3 && dither(x, y) > 0 ? 1 : 0], d === 0 ? 0.8 : 0.65);
      }
    }
    s.stars = starfield(R, cols, s.gy, 140);
    const unit = Math.min(cols, rows);
    s.home = cols * (R() < 0.5 ? 0.24 : 0.76);
    const far = (u) => (s.home < cols / 2 ? 1 - u : u);
    s.moons = [
      { x: cols * far(R.range(0.06, 0.16)), y: rows * 0.14, r: Math.max(3, Math.round(unit * 0.09)), ramp: [0.3, 0.44, 0.58, 0.72].map((l) => k.tone(22, 0.5, l)) },
      { x: cols * far(R.range(0.3, 0.4)), y: rows * 0.08, r: Math.max(2, Math.round(unit * 0.04)), ramp: [0.3, 0.44, 0.58, 0.72].map((l) => k.tone(170, 0.4, l)) },
    ];
    // Plants gather toward the edges.
    s.plants = [];
    for (let i = 0, n = Math.round(cols / 7); i < n; i += 1) {
      const edge = Math.pow(R(), 1.5) * 0.4;
      const x = Math.floor((R() < 0.5 ? edge : 1 - edge) * cols);
      const cap = R() < 0.4;
      s.plants.push({ x, cap, h: cap ? Math.round(R.range(2, 4) * Math.max(0.7, unit / 180)) : Math.round(R.range(0.04, 0.12) * unit), glow: R.pick([s.glowA, s.glowB, s.glowC]), ph: R() * TAU, w: cap ? Math.round(R.range(2, 4)) : 0 });
    }
    s.W = Math.round(Math.max(8, Math.min(cols * 0.17, 64)));
    s.hy = Math.max(s.W * 0.5 + 2, rows * 0.3);
    s.critters = Array.from({ length: Math.max(2, Math.min(6, Math.round(cols / 70))) }, () => ({ x: R() * cols, dir: R.sign(), v: R.range(1.5, 3.5), wait: 0, hue: R.pick([s.glowC, s.glowA, s.glowB]) }));
    s.scouts = [0, 1].map((i) => ({ x: cols * (i ? 0.7 : 0.3), y: rows * 0.15, tx: 0, ty: 0, next: 0 }));
    s.motes = [];
    s.mode = 0;
    s.modeAt = 0;
  },
  // An ellipse of hull, shaded from above, optionally only its top or
  // bottom half.
  hullPart(s, px, cx, cy, rx, ry, half) {
    for (let j = -Math.ceil(ry); j <= Math.ceil(ry); j += 1) {
      if ((half < 0 && j > 0) || (half > 0 && j < 0)) continue;
      for (let i = -Math.ceil(rx); i <= Math.ceil(rx); i += 1) {
        const nx = i / (rx + 0.5);
        const ny = j / (ry + 0.5);
        if (nx * nx + ny * ny > 1) continue;
        const x = Math.round(cx + i);
        const y = Math.round(cy + j);
        const rim = nx * nx + ny * ny > 0.8 && ny > 0;
        plot(s, px, x, y, s.hull[rim ? 0 : step4(0.55 - nx * 0.35 - ny * 0.5, x, y)], 0.95);
      }
    }
  },
  draw(s, t, dt) {
    s.time += Math.min(dt, 0.2);
    dt = Math.min(dt, 0.2);
    const time = s.time;
    const { cols, rows, pal, W } = s;
    const px = s.img.data;
    px.set(s.base);
    twinkle(s, px, s.stars, s.star, time, 0.12);
    for (const m of s.moons) sphere(s, px, m.x, m.y, m.r, (nx, ny, nz, x, y) => m.ramp[step4(-nx * 0.8 - ny * 0.3 + nz * 0.45 + (noise(nx * 4 + 9, ny * 4) > 0.66 ? -0.3 : 0), x, y)], 0.9);
    meteors(s, px, dt, s.gy * 0.7, pal.tint, s.star, 0.06);
    // Plants: glowing bulbs on swaying stalks, and spotted caps.
    for (const p of s.plants) {
      const top = s.gtop[Math.min(cols - 1, Math.max(0, p.x))];
      const pulse = 0.55 + 0.45 * Math.sin(time * 1.2 + p.ph);
      if (p.cap) {
        for (let k = 0; k < p.h; k += 1) plot(s, px, p.x, top - k, s.stem[2]);
        const cy = top - p.h;
        for (let i = -p.w; i <= p.w; i += 1) {
          plot(s, px, p.x + i, cy, s.stem[1]);
          if (Math.abs(i) < p.w) plot(s, px, p.x + i, cy - 1, s.stem[2]);
          if (Math.abs(i) < p.w - 1) plot(s, px, p.x + i, cy - 2, s.stem[2]);
        }
        for (let i = -p.w + 1; i < p.w; i += 2) plot(s, px, p.x + i, cy - 1, p.glow, pulse);
      } else {
        const lean = gust(p.x / cols, time) * 0.18 * p.h;
        let tx = p.x;
        for (let k = 0; k < p.h; k += 1) {
          const f = k / p.h;
          tx = p.x + Math.round(lean * f * f);
          plot(s, px, tx, top - k, s.stem[k > p.h * 0.7 ? 1 : 0]);
        }
        const by = top - p.h;
        plot(s, px, tx, by, p.glow, 1);
        plot(s, px, tx, by - 1, p.glow, 0.8 + 0.2 * pulse);
        for (const [dx, dy] of [[1, 0], [-1, 0], [1, -1], [-1, -1], [0, -2], [0, 1]]) plot(s, px, tx + dx, by + dy, p.glow, 0.25 + 0.4 * pulse);
      }
    }
    // The mothership drifts about its spot and bobs.
    const mx = s.home + Math.sin(time * 0.05) * cols * 0.04;
    const my = s.hy + Math.sin(time * 0.7) * 0.8;
    // Beam cycle: off, pour down, hold, draw back up.
    const cyc = (time % 24) / 24;
    const beam = cyc < 0.55 ? 0 : cyc < 0.6 ? (cyc - 0.55) / 0.05 : cyc < 0.92 ? 1 : 1 - (cyc - 0.92) / 0.08;
    const b0 = my + W * 0.2;
    const ground = s.gtop[Math.max(0, Math.min(cols - 1, Math.round(mx)))];
    if (beam > 0) {
      const reach = b0 + (ground - b0) * Math.min(1, beam * 1.2);
      for (let y = Math.round(b0); y <= reach; y += 1) {
        const f = (y - b0) / Math.max(1, ground - b0);
        const half = W * (0.2 + 0.3 * f);
        const shimmer = 0.16 + 0.1 * Math.sin(y * 0.7 - time * 7);
        for (let i = -Math.round(half); i <= half; i += 1) {
          const x = Math.round(mx + i);
          const edge = Math.abs(Math.abs(i) - half) < 1;
          const a = (edge ? 0.45 : shimmer) * beam;
          if (!edge && dither(x, y) + 0.5 > a * 4) continue;
          plot(s, px, x, y, s.beam, a + 0.1);
        }
      }
      if (beam > 0.9 && Math.random() < dt * 14) s.motes.push({ x: mx + rand(-0.45, 0.45) * W, y: ground - 1, v: rand(4, 9), ph: Math.random() * TAU });
      // The ground glows where the beam lands.
      for (let i = -Math.round(W * 0.6); i <= W * 0.6; i += 1) {
        const x = Math.round(mx + i);
        const g = s.gtop[Math.max(0, Math.min(cols - 1, x))];
        plot(s, px, x, g, s.beam, beam * 0.7 * (1 - Math.abs(i) / (W * 0.6)));
      }
    }
    s.motes = s.motes.filter((m) => {
      m.y -= m.v * dt;
      m.x += Math.sin(time * 3 + m.ph) * dt * 2 + (mx - m.x) * dt * 0.3;
      if (m.y < b0 + 1) return false;
      plot(s, px, m.x, m.y, Math.sin(time * 9 + m.ph) > 0 ? pal.tint : s.beam, 0.9 * Math.max(0.2, beam));
      return true;
    });
    // Hull: underside, disc, upper deck, then the glass dome with its pilot.
    this.hullPart(s, px, mx, my + W * 0.05, W * 0.5, W * 0.14, 1);
    this.hullPart(s, px, mx, my, W, Math.max(1.5, W * 0.1), 0);
    this.hullPart(s, px, mx, my - W * 0.05, W * 0.62, W * 0.16, -1);
    const rd = Math.max(2, W * 0.27);
    const dcy = my - W * 0.17;
    for (let j = -Math.ceil(rd); j <= 0; j += 1) {
      for (let i = -Math.ceil(rd); i <= rd; i += 1) {
        const d = Math.hypot(i, j) / (rd + 0.4);
        if (d > 1) continue;
        const x = Math.round(mx + i);
        const y = Math.round(dcy + j);
        const shine = i < -rd * 0.2 && j < -rd * 0.4 && d > 0.55 && d < 0.8;
        plot(s, px, x, y, shine ? pal.tint : s.glass, shine ? 0.9 : 0.55);
      }
    }
    // The pilot: a big head, two dark eyes, and a slow blink.
    if (rd >= 4) {
      const hr = rd * 0.45;
      const hx = mx + Math.sin(time * 0.3) * rd * 0.15;
      const hy = dcy - rd * 0.35;
      for (let j = -Math.ceil(hr); j <= Math.ceil(hr * 1.3); j += 1) {
        for (let i = -Math.ceil(hr); i <= hr; i += 1) {
          const w = hr * (j < 0 ? 1 : 1 - j / (hr * 1.6));
          if (Math.abs(i) > w || Math.hypot(i, Math.min(0, j)) > hr + 0.3) continue;
          plot(s, px, hx + i, hy + j, s.glowC, 0.9);
        }
      }
      if ((time % 5) > 0.2) {
        const ex = Math.max(1, Math.round(hr * 0.45));
        for (const side of [-1, 1]) {
          plot(s, px, hx + side * ex, hy + 1, pal.ink, 1);
          plot(s, px, hx + side * ex + side, hy + 1, pal.ink, 1);
        }
      }
    }
    // Portholes along the upper deck, warm and flickering.
    for (let i = -W * 0.5, n = 0; i <= W * 0.5; i += Math.max(3, W / 8), n += 1) {
      const on = hash(n, Math.floor(time * 0.7 + n * 0.3), 4) > 0.2;
      plot(s, px, mx + i, my - W * 0.06, on ? s.lamps[1] : s.hull[0], on ? 0.85 : 0.9);
    }
    // Rim lights: the pattern changes every few seconds.
    if (time > s.modeAt) {
      s.mode = (s.mode + 1 + Math.floor(Math.random() * 3)) % 4;
      s.modeAt = time + rand(5, 9);
    }
    const N = Math.max(7, Math.min(17, Math.round(W / 3.5) | 1));
    const tick = Math.floor(time * 9);
    for (let n = 0; n < N; n += 1) {
      const u = (n / (N - 1)) * 2 - 1;
      const x = mx + u * W * 0.9;
      const y = my + Math.max(1, W * 0.1) * 0.5 * Math.sqrt(1 - u * u * 0.8);
      let on;
      if (s.mode === 0) on = (tick - n + N * 4) % 5 === 0 || (tick - n + N * 4) % 5 === 1;
      else if (s.mode === 1) on = (n + Math.floor(time * 3)) % 2 === 0;
      else if (s.mode === 2) on = Math.floor(time * 7) % 4 < 2 && Math.floor(time / 1.1) % 2 === 0;
      else on = Math.abs(u) < ((time * 1.3) % 1.4);
      const c = s.lamps[s.mode === 2 ? 0 : (n + (s.mode === 3 ? 0 : Math.floor(time * 0.5))) % s.lamps.length];
      plot(s, px, x, y, on ? c : mix(c, s.hull[0], 0.6), on ? 1 : 0.8);
      if (on) for (const [dx, dy] of [[1, 0], [-1, 0], [0, 1], [0, -1]]) plot(s, px, x + dx, y + dy, c, 0.4);
    }
    // Underside ports glow brighter while the beam runs.
    for (const f of [-0.3, 0, 0.3]) {
      const a = 0.4 + 0.3 * Math.sin(time * 2 + f * 9) + beam * 0.3;
      plot(s, px, mx + f * W, my + W * 0.16, s.beam, Math.min(1, a));
    }
    // Scouts dart about the upper sky.
    for (const sc of s.scouts) {
      if (time > sc.next) {
        sc.next = time + rand(2, 6);
        sc.tx = cols * rand(0.05, 0.95);
        sc.ty = rows * rand(0.05, 0.35);
      }
      sc.x += (sc.tx - sc.x) * Math.min(1, dt * 2);
      sc.y += (sc.ty - sc.y) * Math.min(1, dt * 2);
      const bl = Math.floor(time * 6) % 2;
      sprite(s, px, [" bb ", "aaaa", "c  d"], Math.round(sc.x) - 2, Math.round(sc.y) - 1, { a: s.hull[3], b: s.glass, c: bl ? s.lamps[0] : s.hull[0], d: bl ? s.hull[0] : s.lamps[2] });
    }
    // The locals wander, stop to look around, and glow at the antenna.
    for (const c of s.critters) {
      if (c.wait > 0) c.wait -= dt;
      else {
        c.x += c.dir * c.v * dt;
        if (Math.random() < dt * 0.15) c.wait = rand(1, 4);
        if (Math.random() < dt * 0.08 || c.x < 2 || c.x > cols - 3) c.dir = c.x < 2 ? 1 : c.x > cols - 3 ? -1 : -c.dir;
      }
      const x = Math.round(c.x);
      const g = s.gtop[Math.max(0, Math.min(cols - 1, x))];
      const step = c.wait > 0 ? 0 : Math.floor(time * 5) & 1;
      const legs = step ? " a a " : "a   a";
      sprite(s, px, ["b   b", " a a ", " aaa ", "acaca", " aaa ", legs], x - 2, g - 6, { a: c.hue, b: pal.tint, c: pal.ink });
    }
    blit(s, false);
  },
};
