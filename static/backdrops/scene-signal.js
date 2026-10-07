// Scene: signal.
// Verbatim extraction from static/chat-backdrop.js; chunk map: backdrop-manifest.json in the snapshot parent.

import { css, tones } from "./color.js";
import { rand, hash } from "./noise.js";
import { atlas, stamp } from "./kit.js";
import { seeded, newSeed } from "./botany.js";

// Signal: a quiet bench of data lanes. Hex dumps scroll up the margins at
// their own speeds, packets run up them as short bright trails, and a
// refresh bar sweeps the screen, lighting whatever it crosses. The glyphs
// bloom softly on dark themes, and now and then one band of the picture
// slips sideways for a moment, like a tube catching its sync.
export const signal = {
  fps: 12,
  previewK: 0.6,
  chars: "0123456789ABCDEF:./_-",
  setup(s) {
    s.cw = 9 * s.k;
    s.ch = 15 * s.k;
    s.clock = Math.random() * 100;
  },
  resize(s) {
    s.cols = Math.ceil(s.w / s.cw);
    s.rows = Math.ceil(s.h / s.ch);
    s.A = atlas(s, this.chars, tones(s.pal), s.cw, s.ch, 12 * s.k);
    // Glyphs are drawn to their own layer, then composited with a bloom.
    const mk = (w, h) => {
      const c = document.createElement("canvas");
      c.width = Math.max(1, Math.round(w));
      c.height = Math.max(1, Math.round(h));
      return c;
    };
    s.layer = mk(s.w * s.dpr, s.h * s.dpr);
    s.lctx = s.layer.getContext("2d");
    s.lctx.setTransform(s.dpr, 0, 0, s.dpr, 0, 0);
    s.glow = mk(s.w / 4, s.h / 4);
    s.gctx = s.glow.getContext("2d");
    // Lanes crowd the margins and thin out through the middle.
    const R = seeded(newSeed());
    s.lanes = [];
    for (let c = 1; c < s.cols - 4;) {
      const u = Math.abs(c / s.cols - 0.5) * 2;
      const width = Math.round(R.range(8, 13));
      if (R() < 0.12 + 0.88 * u * u) {
        s.lanes.push({ c, width, v: R.range(0.8, 4.2), gain: 0.45 + 0.55 * u, id: Math.floor(R() * 1e6), pv: R.range(3, 9), po: R() * 200, gap: R() * 0.2 });
        c += width + Math.round(R.range(2, 5));
      } else c += 2;
    }
    s.glitch = { at: s.clock + rand(14, 30), until: 0, y: 0, h: 0, dx: 0 };
  },
  draw(s, t, dt) {
    s.clock += Math.min(dt, 0.2);
    const now = s.clock;
    const { ctx, lctx, cols, rows, cw, ch, A, pal } = s;
    lctx.clearRect(0, 0, s.w, s.h);
    // The refresh bar, which crosses a little slower than it looks.
    const span = rows + 24;
    const bar = ((now * 2.6) % span) - 12;
    for (const lane of s.lanes) {
      const shift = Math.floor(now * lane.v);
      const head = rows + 10 - ((now * lane.pv + lane.po) % (rows + 40));
      for (let r = 0; r < rows; r += 1) {
        const row = r + shift;
        if (hash(lane.id, row, 1) < 0.14 + lane.gap) continue;
        const len = 3 + Math.floor(hash(lane.id, row, 2) * (lane.width - 2));
        const behind = r - head;
        const packet = behind > -1 && behind < 7 ? 1 - behind / 7 : 0;
        const sweep = Math.exp(-((r - bar) * (r - bar)) / 5);
        for (let i = 0; i < len; i += 1) {
          const c = lane.c + i;
          if (c >= cols) break;
          let b = 0.16 + 0.2 * hash(lane.id, row, 3 + i);
          if (i < 4) b *= 0.7;
          b = (b + packet * 0.75 + sweep * 0.55) * lane.gain * 1.25;
          if (b < 0.1) continue;
          const tone = Math.min(4, Math.floor(b * 5));
          // A few cells keep changing; the rest hold until they scroll off.
          const z = hash(c, row, 9) < 0.06 ? Math.floor(now * 2 + hash(c, row, 5) * 9) : 0;
          const g = Math.floor(hash(lane.id + i * 13, row, 7 + z) * (i < 4 ? 16 : A.n - 3)) % A.n;
          stamp(lctx, A, g, packet > 0.9 && i === 0 ? 4 : tone, c * cw, r * ch);
        }
      }
    }
    // The bar itself: a faint band of light on the tube.
    const by = bar * ch;
    if (by > -ch * 4 && by < s.h + ch * 4) {
      const grad = lctx.createLinearGradient(0, by - ch * 2, 0, by + ch * 2);
      grad.addColorStop(0, css(pal.steel, 0));
      grad.addColorStop(0.5, css(pal.steel, pal.dark ? 0.1 : 0.07));
      grad.addColorStop(1, css(pal.steel, 0));
      lctx.fillStyle = grad;
      lctx.fillRect(0, by - ch * 2, s.w, ch * 4);
    }
    ctx.clearRect(0, 0, s.w, s.h);
    ctx.drawImage(s.layer, 0, 0, s.w, s.h);
    if (pal.dark) {
      s.gctx.clearRect(0, 0, s.glow.width, s.glow.height);
      s.gctx.drawImage(s.layer, 0, 0, s.glow.width, s.glow.height);
      ctx.globalCompositeOperation = "lighter";
      ctx.globalAlpha = 0.9;
      ctx.drawImage(s.glow, 0, 0, s.w, s.h);
      ctx.globalAlpha = 1;
      ctx.globalCompositeOperation = "source-over";
    }
    // A band of the picture slips sideways for a few frames.
    const gl = s.glitch;
    if (now > gl.at && now > gl.until) {
      gl.until = now + rand(0.12, 0.3);
      gl.at = gl.until + rand(16, 40);
      gl.h = Math.round(rand(0.03, 0.09) * s.h);
      gl.y = Math.round(rand(0.05, 0.9) * (s.h - gl.h));
      gl.dx = Math.round(rand(6, 20) * s.k) * (Math.random() < 0.5 ? -1 : 1);
    }
    if (now < gl.until) {
      const d = s.dpr;
      ctx.clearRect(0, gl.y, s.w, gl.h);
      ctx.drawImage(s.layer, 0, gl.y * d, s.layer.width, gl.h * d, gl.dx, gl.y, s.w, gl.h);
    }
  },
};
