// Botanical kit shared by the garden family: seeded layouts, plant colours, wind, seasons, leaf outlines.
// Verbatim extraction from static/chat-backdrop.js; chunk map: backdrop-manifest.json in the snapshot parent.

import { mix, fromHsl, clamp01 } from "./color.js";
import { noise } from "./noise.js";

// —— Botanical kit ——
// Shared by the garden family (Garden, Botanica, Nouveau, Meadow, Pond,
// Koi). Plants keep their natural colours on every theme, pink blossom,
// green leaf, brown bark, pitched to the theme's dark or light ground and
// pulled a little toward its accent so they belong to it. Wood, ink and
// highlights come from the theme itself.

// A small seeded generator, so one planting can be laid out in full up
// front and still differ from the next.
export function seeded(n) {
  let a = n >>> 0;
  const r = () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
  r.range = (lo, hi) => lo + r() * (hi - lo);
  r.pick = (list) => list[Math.floor(r() * list.length)];
  r.sign = () => (r() < 0.5 ? -1 : 1);
  return r;
}
export const newSeed = () => Math.floor(Math.random() * 4294967296);

export function flora(pal) {
  const d = pal.dark;
  // One hue, dark to light, at lightness steps chosen per ground.
  const ramp = (h, sat, dark, light) => (d ? dark : light).map((l) => mix(fromHsl(h, sat, l), pal.brass, 0.1));
  return {
    blossom: ramp(343, 0.72, [0.52, 0.66, 0.77, 0.88], [0.42, 0.55, 0.67, 0.79]),
    leaf: ramp(108, 0.4, [0.3, 0.42, 0.56], [0.24, 0.34, 0.46]),
    moss: ramp(84, 0.34, [0.26, 0.36, 0.48], [0.3, 0.4, 0.5]),
    bark: ramp(20, 0.2, [0.3, 0.42, 0.55], [0.2, 0.3, 0.42]).map((c) => mix(c, pal.mute, 0.25)),
    violet: ramp(266, 0.5, [0.5, 0.63, 0.77], [0.4, 0.52, 0.66]),
    poppy: ramp(6, 0.72, [0.46, 0.6, 0.72], [0.4, 0.52, 0.64]),
    gold: ramp(44, 0.8, [0.5, 0.64, 0.78], [0.38, 0.5, 0.62]),
    sky: ramp(214, 0.6, [0.5, 0.64, 0.78], [0.38, 0.5, 0.62]),
    // Daisy white: near-white on dark grounds, a pale grey on light ones
    // so it still shows.
    white: ramp(40, 0.25, [0.74, 0.86, 0.95], [0.58, 0.68, 0.78]),
    // The theme's own accent as a three-step ramp, for flowers that should
    // carry the theme.
    accent: [mix(pal.brass, pal.ink, 0.3), pal.brass, pal.tint],
  };
}

// One wind for the whole garden: a slow swell rolling across, with a
// stronger gust through it every half minute or so. x is in screen widths.
export function gustiness(t) {
  return Math.pow(Math.max(0, Math.sin(t * 0.19) * Math.sin(t * 0.077 + 1.3)), 2);
}
export function gust(x, t) {
  const swell = Math.sin(x * 5.5 - t * 1.1) * 0.3 + (noise(x * 3 - t * 0.4, t * 0.05) - 0.5) * 0.9;
  const g = gustiness(t);
  return swell * (0.7 + g * 1.6) + g * 0.8;
}
export const sway = (x, t, amp = 1) => Math.max(-1, Math.min(1, Math.round(gust(x, t) * amp)));

// Grow → rest → fade → plant again. The scenes that draw themselves share
// this; it returns the scene's opacity for the frame.
export function season(s, dt, spec) {
  s.phaseT += dt;
  if (s.phase === "grow") {
    if (spec.grow(dt)) {
      s.phase = "rest";
      s.phaseT = 0;
    }
  } else if (s.phase === "rest") {
    spec.rest?.(dt);
    if (s.phaseT > spec.restFor) {
      s.phase = "fade";
      s.phaseT = 0;
    }
  } else {
    spec.rest?.(dt);
    if (s.phaseT > spec.fadeFor) {
      s.phase = "grow";
      s.phaseT = 0;
      spec.replant();
    }
  }
  return s.phase === "fade" ? 1 - clamp01(s.phaseT / spec.fadeFor) : 1;
}

// Leaf outlines at the origin, pointing along +x, ready to fill and stroke.
// A lanceolate leaf:
export function leafShape(c, L, W) {
  c.moveTo(0, 0);
  c.quadraticCurveTo(L * 0.42, -W * 1.6, L, 0);
  c.quadraticCurveTo(L * 0.42, W * 1.6, 0, 0);
}
// An ivy leaf, three shallow lobes around a heart-shaped base:
export function ivyLeaf(c, L) {
  c.moveTo(0, 0);
  c.bezierCurveTo(-L * 0.2, -L * 0.5, L * 0.3, -L * 0.7, L * 0.5, -L * 0.34);
  c.quadraticCurveTo(L * 0.72, -L * 0.3, L, 0);
  c.quadraticCurveTo(L * 0.72, L * 0.3, L * 0.5, L * 0.34);
  c.bezierCurveTo(L * 0.3, L * 0.7, -L * 0.2, L * 0.5, 0, 0);
}
