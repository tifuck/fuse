// Palette: theme colour reading, colour maths, the spectrum ring and glyph tones.
// Verbatim extraction from static/chat-backdrop.js; chunk map: backdrop-manifest.json in the snapshot parent.

import { html } from "./env.js";

// —— Palette ——
function rgb(prop, fallback) {
  const raw = getComputedStyle(html).getPropertyValue(prop).trim();
  let m = raw.match(/^#([0-9a-f]{6})$/i);
  if (m) return [parseInt(m[1].slice(0, 2), 16), parseInt(m[1].slice(2, 4), 16), parseInt(m[1].slice(4, 6), 16)];
  m = raw.match(/rgba?\(\s*([\d.]+)[,\s]+([\d.]+)[,\s]+([\d.]+)/);
  if (m) return [Number(m[1]), Number(m[2]), Number(m[3])];
  return fallback;
}
export const css = (c, a = 1) => `rgba(${c[0] | 0},${c[1] | 0},${c[2] | 0},${a})`;
export const mix = (a, b, t) => [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t];
export const clamp01 = (v) => (v < 0 ? 0 : v > 1 ? 1 : v);

export function toHsl([r, g, b]) {
  r /= 255; g /= 255; b /= 255;
  const max = Math.max(r, g, b);
  const min = Math.min(r, g, b);
  const l = (max + min) / 2;
  if (max === min) return [0, 0, l];
  const d = max - min;
  const s = l > 0.5 ? d / (2 - max - min) : d / (max + min);
  let h = max === r ? (g - b) / d + (g < b ? 6 : 0) : max === g ? (b - r) / d + 2 : (r - g) / d + 4;
  return [h * 60, s, l];
}
export function fromHsl(h, s, l) {
  h = (((h % 360) + 360) % 360) / 360;
  const q = l < 0.5 ? l * (1 + s) : l + s - l * s;
  const p = 2 * l - q;
  const ch = (t) => {
    t = (t + 1) % 1;
    if (t < 1 / 6) return p + (q - p) * 6 * t;
    if (t < 1 / 2) return q;
    if (t < 2 / 3) return p + (q - p) * (2 / 3 - t) * 6;
    return p;
  };
  return [ch(h + 1 / 3) * 255, ch(h) * 255, ch(h - 1 / 3) * 255];
}

export function palette() {
  const pal = {
    ink: rgb("--ink", [18, 18, 18]),
    mist: rgb("--mist", [236, 236, 236]),
    mute: rgb("--mute", [154, 154, 154]),
    brass: rgb("--brass", [197, 223, 136]),
    steel: rgb("--steel", [166, 201, 175]),
    ok: rgb("--ok", [174, 211, 151]),
    fault: rgb("--fault", [242, 157, 142]),
  };
  pal.dark = toHsl(pal.ink)[2] < 0.5;
  // A light tint of the accent: what a head, a spark or a peak burns at.
  pal.tint = mix(pal.brass, pal.mist, 0.45);
  // Neighbours of the accent on the colour wheel, at a lightness that reads
  // on this theme: lavender brings pink and periwinkle, lime brings mint and
  // amber. A grey accent borrows the secondary colour's hue.
  let [h, s, l] = toHsl(pal.brass);
  if (s < 0.1) [h, s] = toHsl(pal.steel);
  const sat = s < 0.08 ? s : Math.max(0.45, Math.min(0.85, s));
  const lit = pal.dark ? Math.max(0.6, Math.min(0.78, l)) : Math.max(0.3, Math.min(0.46, l));
  pal.spectrum = [pal.brass, fromHsl(h + 42, sat, lit), pal.tint, fromHsl(h - 40, sat, lit), pal.steel];
  return pal;
}
// A colour at u (0..1, wrapping) around the spectrum ring.
export function ring(pal, u) {
  const sp = pal.spectrum;
  const x = (((u % 1) + 1) % 1) * sp.length;
  const i = Math.floor(x);
  return mix(sp[i], sp[(i + 1) % sp.length], x - i);
}
// Five tones from quiet to hot, shared by the glyph scenes.
export function tones(pal) {
  return [
    css(pal.mute, 0.5),
    css(mix(pal.mute, pal.steel, 0.6), 0.75),
    css(pal.steel, 0.92),
    css(pal.brass, 1),
    css(pal.tint, 1),
  ];
}
