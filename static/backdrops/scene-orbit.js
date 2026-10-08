// Scene: orbit.
// Verbatim extraction from static/chat-backdrop.js; chunk map: backdrop-manifest.json in the snapshot parent.
// Split from scenes-orbit-galaxy.js; one scene per file.

import { TAU } from "./env.js";
import { mix } from "./color.js";
import { noise, rand } from "./noise.js";
import { cells, put, blit } from "./kit.js";
import { dither, plot, sprite } from "./pixel.js";
import { seeded, newSeed } from "./botany.js";
import { cosmos, starfield, twinkle, meteors, sphere } from "./space.js";

// Orbit: Pond's pixel-art world, moved to a moon. A ringed planet in the
// accent's hue hangs over a cratered horizon, a crescent moon and a dithered
// nebula sit behind twinkling stars, and a rover trundles along the
// surface. Shooting stars streak through now and then, a satellite crosses
// slowly, and a small saucer hovers and darts like the pond's dragonfly.
export const orbit = {
  fps: 20,
  setup(s) {
    s.time = Math.random() * 100;
  },
  resize(s) {
    cells(s, Math.max(2, Math.round(4 * s.k)));
    const { cols, rows, pal } = s;
    const dark = pal.dark;
    const R = seeded(newSeed());
    const { hue, sat, tone, star } = cosmos(pal);
    s.star = star;
    s.body = [0.24, 0.37, 0.5, 0.66].map((l) => tone(hue, sat, l));
    s.band = [0.2, 0.31, 0.44, 0.58].map((l) => tone(hue + 24, sat * 0.8, l));
    s.ringC = [tone(hue + 10, sat * 0.5, 0.46), tone(hue + 10, sat * 0.4, 0.68)];
    s.moonC = [tone(214, 0.12, 0.34), tone(214, 0.1, 0.56), tone(214, 0.1, 0.78)];
    s.ground = [mix(pal.mist, pal.ink, 0.86), mix(pal.mist, pal.ink, 0.74), mix(pal.mist, pal.ink, 0.58)];
    s.gh = Math.max(4, Math.round(rows * 0.13));
    s.gy = rows - s.gh;
    // The surface profile, so the rover and the props sit on it.
    s.gtop = Array.from({ length: cols }, (_, x) => Math.round(s.gy - 1 - 2.4 * noise(x * 0.03, 1.3) - 1.4 * noise(x * 0.12, 4.1)));
    const unit = Math.min(cols, rows);
    s.pr = Math.max(4, Math.min(38, Math.round(unit * 0.17)));
    s.px = Math.round(cols * (R() < 0.5 ? 0.78 : 0.2));
    s.py = Math.round(Math.min(rows * 0.34, s.gy - s.pr * 1.8));
    s.tilt = R.range(-0.4, -0.22);
    // The static layer: nebula, faint stars, crescent moon, surface.
    s.base = new Uint8ClampedArray(s.img.data.length);
    const neb = [pal.spectrum[1], pal.spectrum[3]];
    const off = R() * 100;
    for (let y = 0; y < s.gy; y += 1) {
      for (let x = 0; x < cols; x += 1) {
        for (let n = 0; n < 2; n += 1) {
          const v = noise(x * 0.028 + off + n * 40, y * 0.05 + n * 17) * noise(x * 0.011 + n * 9, y * 0.02 + off) * 1.9;
          if (v - 0.42 > 0.22 + dither(x, y) * 0.3) put(s.base, y * cols + x, neb[n], (dark ? 0.26 : 0.2) * Math.min(1, (v - 0.4) * 2));
        }
      }
    }
    const mc = Math.round(cols * (s.px / cols > 0.5 ? 0.16 : 0.84));
    const my = Math.round(Math.max(6, Math.min(rows * 0.2, s.gy * 0.3)));
    const mr = Math.max(3, Math.round(s.pr * 0.36));
    for (let j = -mr; j <= mr; j += 1) {
      for (let i = -mr; i <= mr; i += 1) {
        if (i * i + j * j > mr * mr) continue;
        // Cut a crescent: shade the side away from the light.
        const cut = (i - mr * 0.45) ** 2 + (j + mr * 0.2) ** 2 < (mr * 0.9) ** 2;
        const l = cut ? 0 : 1 - (i + mr) / (2.4 * mr);
        if (cut && dither(i, j) < 0) continue;
        const c = s.moonC[cut ? 0 : l > 0.62 ? 2 : 1];
        put(s.base, (my + j) * cols + mc + i, c, cut ? 0.25 : 0.9);
      }
    }
    for (let x = 0; x < cols; x += 1) {
      for (let y = s.gtop[x]; y < rows; y += 1) {
        const d = y - s.gtop[x];
        put(s.base, y * cols + x, s.ground[d === 0 ? 2 : d < 3 && dither(x, y) > 0 ? 1 : 0], d === 0 ? 0.7 : 0.5 + Math.min(0.2, d * 0.02));
      }
    }
    // Craters pressed into the ground, each with a lit rim on its far side.
    for (let i = 0, n = Math.max(3, Math.round(cols / 26)); i < n; i += 1) {
      const cx = Math.floor(R() * cols);
      const cy = Math.min(rows - 2, s.gtop[cx] + 2 + Math.floor(R() * Math.max(1, s.gh - 3)));
      const rx = Math.round(R.range(2, 6) * Math.max(0.6, unit / 160));
      const ry = Math.max(1, Math.round(rx * 0.32));
      for (let j = -ry; j <= ry; j += 1) {
        for (let k = -rx; k <= rx; k += 1) {
          const e = (k / rx) ** 2 + (j / ry) ** 2;
          if (e > 1) continue;
          const x = cx + k;
          const y = cy + j;
          if (x < 0 || y < 0 || x >= cols || y >= rows) continue;
          const rim = e > 0.6 && j < 0;
          put(s.base, y * cols + x, rim ? s.ground[2] : mix(s.ground[0], pal.ink, 0.5), rim ? 0.65 : 0.6);
        }
      }
    }
    // A flag and a radio mast stand on the surface; the mast's lamp blinks.
    s.props = [];
    for (const [u, kind] of [[R.range(0.05, 0.3), "flag"], [R.range(0.68, 0.94), "mast"]]) {
      const x = Math.floor(u * cols);
      const top = s.gtop[x];
      const h = Math.max(5, Math.round(unit * 0.05));
      for (let k = 0; k < h; k += 1) put(s.base, (top - k) * cols + x, s.ground[2], 0.85);
      if (kind === "flag") {
        for (let j = 0; j < 3; j += 1) for (let i = 1; i <= 4; i += 1) put(s.base, (top - h + j) * cols + x + i, pal.brass, 0.85);
      } else {
        for (let i = -2; i <= 2; i += 1) put(s.base, (top - h + 1) * cols + x + i, s.ground[2], 0.8);
        s.lamp = { x, y: top - h - 1 };
      }
    }
    // Twinkling stars in three depths; the near ones drift slowly left.
    s.stars = starfield(R, cols, s.gy);
    s.shots = [];
    s.sat = null;
    s.rover = { seed: R() * 100 };
    s.ufo = { x: cols * 0.4, y: s.gy * 0.4, tx: cols * 0.4, ty: s.gy * 0.4, next: 0 };
  },
  draw(s, t, dt) {
    s.time += Math.min(dt, 0.2);
    const time = s.time;
    const { cols, rows, pal, gy } = s;
    const px = s.img.data;
    px.set(s.base);
    twinkle(s, px, s.stars, s.star, time, 0.28);
    // The planet: a lit sphere, dithered, with cloud bands that drift.
    const { pr, body, band } = s;
    const lx = -0.62;
    const ly = -0.5;
    const lz = 0.6;
    for (let j = -pr; j <= pr; j += 1) {
      for (let i = -pr; i <= pr; i += 1) {
        const d2 = (i * i + j * j) / (pr * pr);
        if (d2 > 1) continue;
        const nz = Math.sqrt(1 - d2);
        const lit = (i / pr) * lx + (j / pr) * ly + nz * lz;
        const banded = Math.sin((j / pr) * 7 + noise(i * 0.12 + time * 0.05, j * 0.2) * 3) > 0.25;
        const v = lit * 1.15 + 0.1 + dither(s.px + i, s.py + j) * 0.3;
        const k = v < 0.15 ? 0 : v < 0.5 ? 1 : v < 0.85 ? 2 : 3;
        plot(s, px, s.px + i, s.py + j, (banded ? band : body)[k], 0.95);
      }
    }
    // The ring: an ellipse around the planet, passing behind the top half
    // and in front of the bottom half.
    const rr = pr * 1.75;
    const steps = Math.round(rr * 7);
    for (let front = 0; front < 2; front += 1) {
      for (let n = 0; n < steps; n += 1) {
        const a = (n / steps) * TAU;
        const sn = Math.sin(a);
        if ((sn > 0) !== (front === 1)) continue;
        const ex = Math.cos(a) * rr;
        const ey = sn * rr * 0.26;
        const x = ex * Math.cos(s.tilt) - ey * Math.sin(s.tilt);
        const y = ex * Math.sin(s.tilt) + ey * Math.cos(s.tilt);
        if (!front && x * x + y * y < pr * pr * 0.96) continue;
        const c = s.ringC[Math.cos(a * 2) > 0.2 ? 1 : 0];
        plot(s, px, s.px + x, s.py + y, c, front ? 0.85 : 0.6);
        if (pr > 10) plot(s, px, s.px + x * 0.88, s.py + y * 0.88, s.ringC[0], front ? 0.5 : 0.35);
      }
    }
    // A satellite makes one slow crossing, blinking as it goes.
    if (!s.sat && Math.random() < dt * 0.03) {
      const right = Math.random() < 0.5;
      s.sat = { x: right ? -6 : cols + 6, y: rand(0.08, 0.5) * gy, v: (right ? 1 : -1) * cols / rand(55, 90) };
    }
    if (s.sat) {
      const sa = s.sat;
      sa.x += sa.v * dt;
      if (sa.x < -8 || sa.x > cols + 8) s.sat = null;
      else {
        sprite(s, px, ["b   b", "bccab", "b   b"], Math.round(sa.x) - 2, Math.round(sa.y), { a: pal.tint, b: s.moonC[1], c: s.moonC[2] }, sa.v < 0);
        if (Math.sin(time * 4) > 0.8) plot(s, px, sa.x, sa.y - 1, pal.tint, 0.95);
      }
    }
    // A saucer hovers, then darts to a new spot.
    const u = s.ufo;
    if (time > u.next) {
      u.next = time + rand(3, 8);
      u.tx = cols * rand(0.06, 0.94);
      u.ty = gy * rand(0.1, 0.6);
    }
    u.x += (u.tx - u.x) * Math.min(1, dt * 1.6);
    u.y += (u.ty - u.y) * Math.min(1, dt * 1.6);
    const blink = Math.floor(time * 3) % 3;
    sprite(s, px, ["  bbb  ", " aaaaaaa", "c d e f"], Math.round(u.x) - 3, Math.round(u.y + Math.sin(time * 2) * 0.8) - 1, {
      a: s.moonC[2], b: s.moonC[1], c: blink === 0 ? pal.tint : s.moonC[0], d: blink === 1 ? pal.tint : s.moonC[0], e: blink === 2 ? pal.tint : s.moonC[0], f: blink === 0 ? pal.tint : s.moonC[0],
    });
    meteors(s, px, dt, gy, pal.tint, s.star);
    // The rover rolls slowly back and forth along the surface.
    const rv = s.rover;
    const ph = time * 0.024 + rv.seed;
    const rx = Math.round(cols * (0.5 + 0.44 * Math.sin(ph)));
    const face = Math.cos(ph) < 0;
    const ry = s.gtop[Math.max(0, Math.min(cols - 1, rx))];
    sprite(s, px, ["  aa  ", " bbbbc", "bbbbbb", "d d d "], rx - 3, ry - 3, { a: pal.tint, b: s.moonC[2], c: pal.brass, d: s.ground[1] }, face);
    if (s.lamp && Math.sin(time * 2.4) > 0.6) {
      plot(s, px, s.lamp.x, s.lamp.y, pal.fault, 0.95);
      for (const [dx, dy] of [[1, 0], [-1, 0], [0, -1]]) plot(s, px, s.lamp.x + dx, s.lamp.y + dy, pal.fault, 0.35);
    }
    blit(s, false);
  },
};
