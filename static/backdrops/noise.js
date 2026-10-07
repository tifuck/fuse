// Smooth value noise, drift fields, hashes and free randomness.
// Verbatim extraction from static/chat-backdrop.js; chunk map: backdrop-manifest.json in the snapshot parent.

// —— Smooth value noise (2D, tileless) ——
const PERM = new Uint8Array(512);
(() => {
  const p = Array.from({ length: 256 }, (_, i) => i);
  let seed = 1337;
  for (let i = 255; i > 0; i -= 1) {
    seed = (seed * 16807) % 2147483647;
    const j = seed % (i + 1);
    [p[i], p[j]] = [p[j], p[i]];
  }
  for (let i = 0; i < 512; i += 1) PERM[i] = p[i & 255];
})();
const fade = (t) => t * t * (3 - 2 * t);
export function noise(x, y) {
  const xi = Math.floor(x);
  const yi = Math.floor(y);
  const u = fade(x - xi);
  const v = fade(y - yi);
  const X = xi & 255;
  const Y = yi & 255;
  const a = PERM[PERM[X] + Y] / 255;
  const b = PERM[PERM[X + 1] + Y] / 255;
  const c = PERM[PERM[X] + Y + 1] / 255;
  const d = PERM[PERM[X + 1] + Y + 1] / 255;
  return a + (b - a) * u + (c - a) * v + (a - b - c + d) * u * v;
}
// Two layers drifting against each other read as a slowly evolving field.
export function field(x, y, t) {
  return 0.62 * noise(x + t * 0.35, y - t * 0.12) + 0.38 * noise(x * 2.1 - t * 0.22, y * 2.1 + t * 0.3);
}
export function hash(x, y, z = 0) {
  let h = Math.imul(x | 0, 374761393) ^ Math.imul(y | 0, 668265263) ^ Math.imul(z | 0, 1274126177);
  h = Math.imul(h ^ (h >>> 13), 1103515245);
  return ((h ^ (h >>> 16)) >>> 0) / 4294967296;
}
export const rand = (a, b) => a + Math.random() * (b - a);
export const pick = (list) => list[Math.floor(Math.random() * list.length)];
