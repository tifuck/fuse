// Scene: eclipse.
// Verbatim extraction from static/chat-backdrop.js; chunk map: backdrop-manifest.json in the snapshot parent.
// Split from scenes-eclipse-belt-contact.js; one scene per file.

import { mix, clamp01 } from "./color.js";
import { noise, field, hash, rand } from "./noise.js";
import { cells, put, blit } from "./kit.js";
import { dither, plot, sprite } from "./pixel.js";
import { seeded, newSeed } from "./botany.js";
import { cosmos, starfield, twinkle, sphere, step4 } from "./space.js";

// Eclipse: the view from low orbit. A planet's limb fills the bottom of
// the screen, turning slowly beneath; cloud bands drift over its oceans on
// the day side, cities glitter on the night side, and a thin atmosphere
// glows along the curve. The sun sits just on the horizon, flaring, with
// its reflections strung across the sky. A space station crosses now and
// then.
export const eclipse = {
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
    s.k2 = k;
    s.left = R() < 0.5;
    s.R = cols * 0.95;
    s.cx = cols * (s.left ? 0.62 : 0.38);
    const lift = Math.max(rows * 0.3, Math.min(rows * 0.42, 60));
    s.cy = rows - lift + s.R;
    s.ocean = [0.2, 0.3, 0.42, 0.56].map((l) => k.tone(212, 0.55, l));
    s.land = [0.22, 0.32, 0.42, 0.54].map((l) => k.tone(95, 0.3, l));
    s.cloud = [0.62, 0.76, 0.9].map((l) => k.tone(210, 0.12, l));
    s.city = k.tone(40, 0.9, 0.7);
    s.air = k.tone(200, 0.75, 0.66);
    s.sunC = k.tone(46, 0.9, 0.86);
    s.night = mix(pal.ink, [4, 8, 16], 0.3);
    // The sun sits on the limb near one end.
    const ang = (s.left ? -1 : 1) * 0.36 - Math.PI / 2;
    s.sx = s.cx + Math.cos(ang) * s.R;
    s.sy = s.cy + Math.sin(ang) * s.R - 1;
    s.L = [Math.sign(s.sx - s.cx) * 0.9, -0.1, -0.4];
    s.stars = starfield(R, cols, Math.max(4, Math.round(rows - lift)), 150);
    s.station = null;
    s.seed = R() * 100;
  },
  draw(s, t, dt) {
    s.time += Math.min(dt, 0.2);
    const time = s.time;
    const { cols, rows, pal, R: pr, cx, cy, L } = s;
    const px = s.img.data;
    px.fill(0);
    twinkle(s, px, s.stars, s.star, time);
    const top = Math.max(0, Math.floor(cy - pr));
    const turn = time * 0.006 + s.seed;
    for (let y = top; y < rows; y += 1) {
      const v = (y - cy) / pr;
      for (let x = 0; x < cols; x += 1) {
        const u = (x - cx) / pr;
        const d2 = u * u + v * v;
        const i = y * cols + x;
        if (d2 > 1) {
          // The atmosphere: a thin blue rim, brightest toward the sun.
          const gap = (Math.sqrt(d2) - 1) * pr;
          if (gap < 3) {
            const sun = clamp01(1 - Math.hypot(x - s.sx, y - s.sy) / (cols * 0.45));
            const a = (1 - gap / 3) * (0.25 + 0.65 * sun);
            if (a + dither(x, y) * 0.3 > 0.2) put(px, i, s.air, a);
          }
          continue;
        }
        const nz = Math.sqrt(1 - d2);
        const lit = u * L[0] + v * L[1] + nz * L[2];
        const lon = Math.atan2(u, nz) + turn;
        const lat = v;
        const landy = noise(lon * 5 + 30, lat * 5) * 0.7 + noise(lon * 13, lat * 13 + 4) * 0.3 > 0.55;
        const cl = field(lon * 7 - time * 0.01, lat * 9, time * 0.02);
        if (lit < -0.04) {
          // Night: dark ground, city lights on land.
          put(px, i, s.night, 0.7);
          // Cities: clustered regions of land, lit a pixel here and there.
          if (landy && noise(lon * 14 + 50, lat * 14) > 0.6 && hash(x, y, 3) < 0.3) {
            const f = 0.55 + 0.45 * Math.sin(time * 2 + x * 7 + y * 3);
            put(px, i, s.city, 0.5 + 0.4 * f * clamp01(-lit * 6));
          }
          continue;
        }
        const k = step4(lit * 1.6 + 0.1, x, y);
        let c = landy ? s.land[k] : s.ocean[k];
        if (cl > 0.62) c = s.cloud[Math.min(2, Math.max(0, k - 1))];
        // The terminator: a narrow band of dusk.
        if (lit < 0.08) c = mix(c, s.night, 0.5);
        put(px, i, c, 0.9);
      }
    }
    // The sun on the limb: a hot core, rays that breathe, and its
    // reflections strung across the sky toward the far corner.
    const breathe = 0.85 + 0.15 * Math.sin(time * 1.4);
    sphere(s, px, s.sx, s.sy, 2, () => s.sunC, 1);
    const ray = Math.round(Math.max(4, cols * 0.06) * breathe);
    for (let n = 3; n <= ray; n += 1) {
      const a = (1 - n / ray) * 0.85;
      for (const [dx, dy] of [[1, 0], [-1, 0], [0, -1]]) plot(s, px, s.sx + dx * n, s.sy + dy * n, s.sunC, a);
      if (n < ray * 0.5) for (const [dx, dy] of [[1, -1], [-1, -1]]) plot(s, px, s.sx + dx * n * 0.7, s.sy + dy * n * 0.7, s.sunC, a * 0.6);
    }
    const fx = cols * (s.left ? 0.9 : 0.1) - s.sx;
    const fy = rows * 0.08 - s.sy;
    for (const [f, r, tone] of [[0.3, 1, 0], [0.5, 2, 1], [0.72, 1, 2], [0.9, 3, 1]]) {
      const c = [s.air, pal.spectrum[1], s.sunC][tone];
      sphere(s, px, s.sx + fx * f, s.sy + fy * f, r, (nx, ny, nz) => (nz < 0.75 || r < 2 ? c : null), 0.35 * breathe);
    }
    // A station: a truss, two pairs of panels, a blinking light.
    if (!s.station && Math.random() < dt * 0.04) {
      const right = Math.random() < 0.5;
      s.station = { x: right ? -10 : cols + 10, y: rand(0.08, 0.3) * rows, v: (right ? 1 : -1) * cols / rand(45, 70) };
    }
    if (s.station) {
      const st = s.station;
      st.x += st.v * dt;
      if (st.x < -12 || st.x > cols + 12) s.station = null;
      else {
        const g = s.k2.grey;
        sprite(s, px, ["aa  b  aa", "aacccccaa", "aa  b  aa"], Math.round(st.x) - 4, Math.round(st.y) - 1, { a: s.air, b: g[1], c: g[2] });
        if (Math.sin(time * 5) > 0.7) plot(s, px, st.x, st.y - 2, pal.fault, 0.95);
      }
    }
    blit(s, false);
  },
};
