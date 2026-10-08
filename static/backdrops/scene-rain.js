// Scene: rain.
// Verbatim extraction from static/chat-backdrop.js; chunk map: backdrop-manifest.json in the snapshot parent.
// Split from scenes-glyph.js; one scene per file.

import { tones } from "./color.js";
import { rand } from "./noise.js";
import { atlas, stamp } from "./kit.js";

// Glyph rain: hex and half-width kana falling in columns, a hot head and
// a cooling tail.
export const rain = {
  fps: 24,
  chars: "0123456789ABCDEFｦｱｳｴｵｶｷｹｺｻｼｽｾｿﾀﾂﾃﾅﾆﾇﾈﾊﾋﾎﾏﾐﾑﾒﾓﾔﾕﾗﾘﾜ:=+*<>",
  setup(s) {
    s.cw = 13 * s.k;
    s.ch = 19 * s.k;
  },
  resize(s) {
    s.cols = Math.ceil(s.w / s.cw);
    s.rows = Math.ceil(s.h / s.ch);
    s.A = atlas(s, this.chars, tones(s.pal), s.cw, s.ch, 13 * s.k);
    s.heat = new Float32Array(s.cols * s.rows);
    s.glyph = new Uint8Array(s.cols * s.rows);
    for (let i = 0; i < s.glyph.length; i += 1) s.glyph[i] = Math.floor(Math.random() * s.A.n);
    s.drops = Array.from({ length: s.cols }, () => this.spawn(s, true));
  },
  spawn(s, anywhere) {
    return { y: anywhere ? rand(-s.rows * 1.6, s.rows) : rand(-s.rows * 1.6, -2), v: rand(5, 16), fade: rand(1.5, 3.2) };
  },
  warm(s) {
    for (let i = 0; i < 60; i += 1) this.step(s, 1 / 24);
  },
  step(s, dt) {
    const { cols, rows, heat, glyph, drops } = s;
    for (let c = 0; c < cols; c += 1) {
      const d = drops[c];
      const decay = Math.exp(-dt * d.fade);
      for (let r = 0; r < rows; r += 1) heat[r * cols + c] *= decay;
      const from = Math.floor(d.y);
      d.y += d.v * dt;
      for (let r = Math.max(0, from); r <= Math.min(rows - 1, Math.floor(d.y)); r += 1) {
        const i = r * cols + c;
        if (heat[i] < 0.98) glyph[i] = Math.floor(Math.random() * s.A.n);
        heat[i] = 1;
      }
      if (d.y > rows + 10) drops[c] = this.spawn(s, false);
    }
    // Resting characters mutate now and then.
    for (let i = Math.ceil(cols * rows * 0.003); i > 0; i -= 1) glyph[Math.floor(Math.random() * glyph.length)] = Math.floor(Math.random() * s.A.n);
  },
  draw(s, t, dt) {
    this.step(s, Math.min(dt, 0.1));
    const { ctx, cols, rows, heat, glyph, cw, ch, A } = s;
    ctx.clearRect(0, 0, s.w, s.h);
    for (let r = 0; r < rows; r += 1) {
      for (let c = 0; c < cols; c += 1) {
        const i = r * cols + c;
        const h = heat[i];
        if (h < 0.05) continue;
        const tone = h > 0.985 ? 4 : h > 0.7 ? 3 : h > 0.4 ? 2 : h > 0.18 ? 1 : 0;
        stamp(ctx, A, glyph[i], tone, c * cw, r * ch);
      }
    }
  },
};
