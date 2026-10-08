// Scene: warp.
// Verbatim extraction from static/chat-backdrop.js; chunk map: backdrop-manifest.json in the snapshot parent.
// Split from scenes-field.js; one scene per file.

import { css } from "./color.js";
import { rand } from "./noise.js";
import { atlas } from "./kit.js";

// A glyph starfield flying out of a vanishing point: far stars are dots,
// near ones become stars with a streak behind them. The vanishing point
// wanders slowly around the middle of the screen.
export const warp = {
  fps: 30,
  previewK: 0.8,
  chars: "·∙+×*✦",
  setup(s) {
    s.clock = Math.random() * 100;
  },
  resize(s) {
    const pal = s.pal;
    const size = Math.round(16 * s.k);
    s.A = atlas(s, this.chars, [css(pal.mute, 0.8), css(pal.steel, 0.9), css(pal.brass, 1), css(pal.tint, 1), css(pal.spectrum[1], 0.95)], size, size, 13 * s.k);
    const n = Math.round(Math.max(50, Math.min(1400, (s.w * s.h) / 1300)));
    s.stars = Array.from({ length: n }, () => this.star(Math.random()));
  },
  star(z) {
    return { x: rand(-1, 1), y: rand(-1, 1), z: z || 1, px: null, py: null, hue: Math.random() < 0.12 };
  },
  warm(s) {
    for (let i = 0; i < 30; i += 1) this.step(s, 1 / 30);
  },
  step(s, dt) {
    s.clock += dt;
    s.vx = 0.5 + 0.08 * Math.sin(s.clock * 0.05);
    s.vy = 0.5 + 0.06 * Math.sin(s.clock * 0.037 + 1.3);
    const v = 0.09;
    for (const st of s.stars) {
      st.z -= v * dt;
      if (st.z <= 0.03) Object.assign(st, this.star(1));
    }
  },
  draw(s, t, dt) {
    this.step(s, Math.min(dt, 0.1));
    const { ctx, A, pal } = s;
    const cx = s.w * s.vx;
    const cy = s.h * s.vy;
    const f = Math.max(s.w, s.h) * 0.42;
    const size = A.cw;
    ctx.clearRect(0, 0, s.w, s.h);
    ctx.lineCap = "round";
    for (const st of s.stars) {
      const x = cx + (st.x / st.z) * f;
      const y = cy + (st.y / st.z) * f;
      if (x < -20 || y < -20 || x > s.w + 20 || y > s.h + 20) {
        Object.assign(st, this.star(1));
        continue;
      }
      const near = 1 - st.z;
      if (st.px !== null && near > 0.4) {
        ctx.beginPath();
        ctx.moveTo(st.px, st.py);
        ctx.lineTo(x, y);
        ctx.strokeStyle = css(st.hue ? pal.spectrum[1] : pal.steel, (near - 0.4) * 0.9);
        ctx.lineWidth = 1 + near;
        ctx.stroke();
      }
      const g = Math.min(A.n - 1, Math.floor(near * near * A.n * 1.1));
      const tone = st.hue && near > 0.4 ? 4 : near > 0.85 ? 3 : near > 0.6 ? 2 : near > 0.3 ? 1 : 0;
      const big = size * (0.8 + near * near * 1.1);
      const d = A.dpr;
      ctx.drawImage(A.canvas, g * A.cw * d, tone * A.ch * d, A.cw * d, A.ch * d, x - big / 2, y - big / 2, big, big);
      // Remember a point a little behind, so streaks have length.
      st.px = x - (x - cx) * 0.06;
      st.py = y - (y - cy) * 0.06;
    }
  },
};
