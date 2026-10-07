// Pixel art: ordered dither, cell plotting and the string-map sprites.
// Verbatim extraction from static/chat-backdrop.js; chunk map: backdrop-manifest.json in the snapshot parent.

import { put } from "./kit.js";

// Pixel art: a 2×2 ordered dither and tiny sprites drawn from string maps,
// one character per cell, each character a palette slot.
const BAYER = [-0.375, 0.125, 0.375, -0.125];
export const dither = (x, y) => BAYER[(x & 1) + ((y & 1) << 1)];
export function plot(s, px, x, y, c, a = 1) {
  x = Math.floor(x);
  y = Math.floor(y);
  if (x < 0 || y < 0 || x >= s.cols || y >= s.rows) return;
  put(px, y * s.cols + x, c, a);
}
export function sprite(s, px, map, x, y, slots, flip = false) {
  const w = map[0].length;
  for (let j = 0; j < map.length; j += 1) {
    const row = map[j];
    for (let i = 0; i < w; i += 1) {
      const c = slots[row[flip ? w - 1 - i : i]];
      if (c) plot(s, px, x + i, y + j, c);
    }
  }
}
export const SPRITES = {
  leaf: ["  cc", " bcc", "abb ", "a   "],
  leafSmall: [" c", "ab"],
  curl: [" aa", "a a", "a  ", " a "],
  bloom: [" c ", "cdc", " c "],
  tulip: ["a c", "abc", "abc", " b "],
  tulipOpen: ["a b c", "abbbc", " abc "],
  poppy: [" bc ", "abbc", " ab "],
  bell: [" a ", "abc", "a c"],
  bud: ["b", "a"],
  flyOpen: ["bb bb", "bcacb", " bab ", " b b "],
  flyShut: [" b b ", " bab ", "  a  ", "     "],
  dragonUp: ["b b  ", "aaaac", "     "],
  dragonDown: ["     ", "aaaac", "b b  "],
  lotus: ["  b  ", "a b a", "abdba", " aaa "],
  lotusBud: [" b ", "aba", " a "],
  koi: ["  abb   ", "aaabbacc", "  abb   "],
};
