// Scene: ivy (Nouveau).
// Verbatim extraction from static/chat-backdrop.js; chunk map: backdrop-manifest.json in the snapshot parent.

import { TAU } from "./env.js";
import { css, mix, clamp01 } from "./color.js";
import { seeded, newSeed, flora, season, gust, ivyLeaf, leafShape } from "./botany.js";

// Nouveau: vines climb both side margins in whiplash curves and arch
// along the top, framing the page like an Art Nouveau poster. Everything
// carries a dark contour over flat two-tone fills: ivy leaves, long
// leaves, spiral tendrils, morning-glory trumpets and berries, inside a
// fine double rule in the theme's accent. Once grown, the leaves stir
// in the wind; after a while it fades and grows again.
export const ivy = {
  fps: 20,
  setup(s) {
    s.time = Math.random() * 100;
  },
  resize(s) {
    s.z = s.k * Math.max(0.45, Math.min(1.5, Math.min(s.w, s.h) / 700));
    s.fl = flora(s.pal);
    s.line = s.pal.dark ? mix(s.pal.ink, [0, 0, 0], 0.35) : mix(s.pal.mist, s.fl.leaf[0], 0.35);
    this.plant(s);
    if (s.sized) this.warm(s, 0, true);
    s.sized = true;
  },
  plant(s) {
    const R = seeded(newSeed());
    Object.assign(s, { clock: 0, phase: "grow", phaseT: 0, alpha: 1, vines: [], parts: [], speed: 95 * s.z });
    s.inset = R.range(26, 40) * s.z;
    for (const side of [-1, 1]) this.vine(s, R, side);
    s.parts.sort((a, b) => a.at - b.at);
    let end = 0;
    for (const v of s.vines) end = Math.max(end, v.start + v.len / s.speed);
    s.doneAt = end + 2;
  },
  // Lay out one vine in left-hand coordinates, then mirror it for the
  // right side. The stem steers along the margin and round the top
  // corner, and a slow sine in its heading throws it into whiplash S's.
  vine(s, R, side) {
    const { w, h, z } = s;
    const m = s.inset;
    const reach = w * R.range(0.3, 0.4);
    const om = R.range(0.011, 0.016) / z;
    const ph = R() * TAU;
    const amp = R.range(0.8, 1.1);
    const mirror = (p) => (side < 0 ? p : { ...p, x: w - p.x, ang: Math.PI - p.ang });
    const path = (x, y, ang, steer, max) => {
      const pts = [];
      for (let d = 0; d < max; d += 3) {
        const target = steer(x, y, d, ang);
        if (target === null) break;
        ang += (target - ang) * 0.08;
        x += Math.cos(ang) * 3;
        y += Math.sin(ang) * 3;
        pts.push({ x, y, ang, d });
      }
      return pts;
    };
    let top = false;
    const main = path(m * 0.4, h + 12, -Math.PI / 2, (x, y, d) => {
      if (!top && y < m + 70 * z) top = true;
      if (top && x > reach) return null;
      const lean = Math.sin(d * om + ph) * amp;
      // Free to swing into the room, but held off the edge of the screen.
      const pull = (q) => Math.max(-0.9, Math.min(1.2, q < m * 0.8 ? (m - q) / (22 * z) : (m - q) / (80 * z)));
      if (!top) return -Math.PI / 2 + pull(x) + lean;
      return pull(y) + lean * 0.8;
    }, 8000).map(mirror);
    const start = R.range(0, 1.5);
    s.vines.push({ pts: main, len: main.length * 3, start, width: 1.5 });
    // A slimmer twin stem winds round the main one.
    const twin = main.map((p, i) => {
      const off = Math.sin(i * 0.05 + ph) * 9 * z;
      return { x: p.x - Math.sin(p.ang) * off, y: p.y + Math.cos(p.ang) * off, ang: p.ang, d: p.d };
    });
    s.vines.push({ pts: twin, len: twin.length * 3, start: start + 0.6, width: 0.7 });
    const at = (i) => start + (i * 3) / s.speed;
    // Leaves, flowers and berries along the main stem.
    let i = Math.floor(R.range(6, 12));
    let flip = 1;
    let n = 0;
    while (i < main.length - 4) {
      const p = main[i];
      flip = -flip;
      n += 1;
      const out = p.ang + flip * (side < 0 ? 1 : -1) * R.range(0.8, 1.2);
      const kind = n % 6 === 3 ? "flower" : n % 9 === 7 ? "berries" : R() < 0.62 ? "heart" : "leaf";
      s.parts.push({ kind, x: p.x, y: p.y, ang: kind === "flower" ? p.ang + (side < 0 ? 1 : -1) * Math.PI / 2 * R.range(0.6, 1) : out, size: R.range(22, 36) * z * (kind === "flower" ? 1.2 : kind === "berries" ? 0.8 : 1), at: at(i) + 0.3, seed: R() * 100 });
      i += Math.floor((R.range(18, 28) * z) / 3);
    }
    // The stem ends in a spray of trumpets.
    const tip = main[main.length - 1];
    if (tip) {
      for (let f = 0; f < 3; f += 1) s.parts.push({ kind: "flower", x: tip.x, y: tip.y, ang: tip.ang + (f - 1) * 0.7, size: R.range(34, 44) * z, at: at(main.length) + 0.3 + f * 0.4, seed: R() * 100 });
    }
    // Side shoots that leave the stem and curl up into a spiral.
    for (let k = 0, shoots = 3 + Math.floor(R() * 3); k < shoots; k += 1) {
      const j = Math.floor(main.length * R.range(0.12, 0.92));
      const p = main[j];
      if (!p) continue;
      const room = side < 0 ? 1 : -1;
      const len = R.range(90, 180) * z;
      const curl = room * R.sign();
      let ang0 = p.ang + room * R.range(0.6, 1.1);
      const shoot = path(p.x, p.y, ang0, (x, y, d) => (d > len ? null : ang0 + curl * Math.pow(d / len, 2.2) * 5.5), len + 3);
      ang0 = shoot.length ? shoot[0].ang : ang0;
      s.vines.push({ pts: shoot, len: shoot.length * 3, start: at(j), width: 0.6 });
      for (let q = 5; q < shoot.length * 0.6; q += Math.floor((R.range(20, 30) * z) / 3)) {
        const sp = shoot[q];
        s.parts.push({ kind: R() < 0.5 ? "leaf" : "heart", x: sp.x, y: sp.y, ang: sp.ang + R.sign() * R.range(0.8, 1.2), size: R.range(15, 24) * z, at: at(j) + (q * 3) / s.speed + 0.3, seed: R() * 100 });
      }
    }
  },
  // A stem in three passes, contour, colour and a fine highlight,
  // tapering from its root to its growing tip in a few steps.
  stroke(s, c, v, upto) {
    const { pts } = v;
    const n = Math.min(pts.length, Math.floor(upto / 3));
    if (n < 2) return;
    const steps = 5;
    for (const [pass, k, color] of [[0, 1, css(s.line, 0.95)], [1, 0.5, css(s.fl.leaf[1], 1)], [2, 0.14, css(s.fl.leaf[2], 0.9)]]) {
      c.strokeStyle = color;
      for (let q = 0; q < steps; q += 1) {
        const a = Math.floor((q * (pts.length - 1)) / steps);
        const b = Math.min(n - 1, Math.floor(((q + 1) * (pts.length - 1)) / steps));
        if (b <= a) break;
        const wdt = (5 * v.width + 1) * s.z * (1 - (q / steps) * 0.55);
        c.lineWidth = Math.max(pass === 2 ? 0.5 : 0.8, wdt * k);
        c.beginPath();
        c.moveTo(pts[a].x, pts[a].y);
        for (let i = a + 1; i <= b; i += 1) c.lineTo(pts[i].x, pts[i].y);
        c.stroke();
      }
    }
  },
  // Flat Nouveau fills: a darker half and a lighter half either side of
  // the midrib, with the contour drawn over both.
  twoTone(s, c, dark, light) {
    c.fillStyle = css(dark, 1);
    c.fill();
    c.save();
    c.clip();
    c.fillStyle = css(light, 1);
    c.fillRect(-100, -100, 200, 100);
    c.restore();
    c.lineWidth = 1.5 * s.z + 0.3;
    c.strokeStyle = css(s.line, 1);
    c.stroke();
  },
  part(s, c, p, e) {
    const { fl, z } = s;
    const L = p.size * e;
    if (L < 1) return;
    c.save();
    c.translate(p.x, p.y);
    c.rotate(p.ang + gust(p.x / s.w + p.seed, s.time) * 0.05);
    if (p.kind === "heart" || p.kind === "leaf") {
      c.beginPath();
      if (p.kind === "heart") ivyLeaf(c, L);
      else leafShape(c, L, L * 0.3);
      this.twoTone(s, c, fl.leaf[0], fl.leaf[1]);
      c.beginPath();
      c.moveTo(0, 0);
      c.lineTo(L * 0.8, 0);
      c.lineWidth = 0.9 * z + 0.2;
      c.strokeStyle = css(fl.leaf[2], 0.9);
      c.stroke();
    } else if (p.kind === "flower") {
      // A morning glory, side on: a slim tube flaring into a wide mouth
      // with a pale star in it.
      c.beginPath();
      c.moveTo(0, -L * 0.06);
      c.quadraticCurveTo(L * 0.45, -L * 0.08, L * 0.72, -L * 0.46);
      c.lineTo(L * 0.72, L * 0.46);
      c.quadraticCurveTo(L * 0.45, L * 0.08, 0, L * 0.06);
      c.closePath();
      this.twoTone(s, c, fl.violet[0], fl.violet[1]);
      c.beginPath();
      c.ellipse(L * 0.72, 0, L * 0.14, L * 0.46, 0, 0, TAU);
      c.fillStyle = css(fl.violet[2], 1);
      c.fill();
      c.lineWidth = 1.5 * z + 0.3;
      c.strokeStyle = css(s.line, 1);
      c.stroke();
      c.beginPath();
      for (let i = 0; i < 5; i += 1) {
        const a = (i / 5) * TAU + 0.3;
        c.moveTo(L * 0.72, 0);
        c.lineTo(L * 0.72 + Math.cos(a) * L * 0.12, Math.sin(a) * L * 0.42);
      }
      c.lineWidth = 0.8 * z + 0.2;
      c.strokeStyle = css(mix(fl.violet[2], s.pal.tint, 0.6), 0.9);
      c.stroke();
      c.beginPath();
      c.ellipse(L * 0.72, 0, L * 0.05, L * 0.12, 0, 0, TAU);
      c.fillStyle = css(s.pal.tint, 1);
      c.fill();
      // Sepals where the tube meets the stem.
      c.beginPath();
      c.moveTo(0, 0);
      c.lineTo(L * 0.2, -L * 0.12);
      c.lineTo(L * 0.14, 0);
      c.lineTo(L * 0.2, L * 0.12);
      c.closePath();
      c.fillStyle = css(fl.leaf[1], 1);
      c.fill();
      c.lineWidth = 1 * z + 0.2;
      c.strokeStyle = css(s.line, 1);
      c.stroke();
    } else {
      for (let i = 0; i < 5; i += 1) {
        const a = i * 2.4;
        const r = L * 0.16;
        const bx = L * 0.3 + Math.cos(a) * L * 0.2 * (i ? 1 : 0);
        const by = Math.sin(a) * L * 0.2 * (i ? 1 : 0);
        c.beginPath();
        c.arc(bx, by, r, 0, TAU);
        c.fillStyle = css(fl.accent[1], 1);
        c.fill();
        c.lineWidth = 1.2 * z + 0.2;
        c.strokeStyle = css(s.line, 1);
        c.stroke();
        c.beginPath();
        c.arc(bx - r * 0.35, by - r * 0.35, r * 0.28, 0, TAU);
        c.fillStyle = css(s.pal.tint, 0.9);
        c.fill();
      }
    }
    c.restore();
  },
  // The poster's double rule, drawn up the sides and over the top.
  frame(s, c, g) {
    const m = s.inset * 0.55;
    const { w, h } = s;
    const side = h - m;
    const total = side * 2 + (w - 2 * m);
    let left = total * g;
    c.strokeStyle = css(s.pal.brass, 0.4);
    for (const [off, lw] of [[0, 1], [4 * s.z, 0.6]]) {
      left = total * g;
      c.lineWidth = lw;
      c.beginPath();
      const a = Math.min(left, side);
      c.moveTo(m + off, h);
      c.lineTo(m + off, h - a);
      const b = Math.min(Math.max(0, left - side), w - 2 * m);
      if (b > 0) c.lineTo(m + off + b - off * 2 * (b / (w - 2 * m)), m + off);
      c.moveTo(w - m - off, h);
      c.lineTo(w - m - off, h - a);
      c.stroke();
    }
  },
  step(s, dt) {
    s.time += dt;
    s.clock += dt;
    s.alpha = season(s, dt, {
      grow: () => s.clock >= s.doneAt,
      restFor: 75,
      fadeFor: 4,
      replant: () => this.plant(s),
    });
  },
  warm(s, t, full) {
    const until = full ? s.doneAt + 1 : s.doneAt * 0.6;
    while (s.clock < until && s.phase !== "fade") this.step(s, 0.25);
  },
  draw(s, t, dt) {
    this.step(s, Math.min(dt, 0.2));
    const { ctx } = s;
    ctx.clearRect(0, 0, s.w, s.h);
    ctx.globalAlpha = s.alpha;
    ctx.lineCap = "round";
    ctx.lineJoin = "round";
    this.frame(s, ctx, clamp01(s.clock / (s.doneAt * 0.7)));
    for (const v of s.vines) this.stroke(s, ctx, v, (s.clock - v.start) * s.speed);
    for (const p of s.parts) {
      const e = clamp01((s.clock - p.at) / 0.8);
      if (e <= 0) break;
      this.part(s, ctx, p, 1 - Math.pow(1 - e, 3));
    }
    ctx.globalAlpha = 1;
  },
};
