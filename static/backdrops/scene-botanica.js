// Scene: botanica.
// Verbatim extraction from static/chat-backdrop.js; chunk map: backdrop-manifest.json in the snapshot parent.

import { TAU } from "./env.js";
import { css, mix, clamp01 } from "./color.js";
import { noise, rand, pick } from "./noise.js";
import { flora, season, gustiness, gust, ivyLeaf } from "./botany.js";

// Botanica: a pen-and-ink plate that draws itself, like an old botanical
// engraving coloured by hand. Tall borders of wildflowers, ferns, vines,
// dandelion clocks and grasses rise up both sides of the page, drawn with
// a bold line and filled with solid colour so they hold their own behind
// the text. Leaves and petals unfurl
// as the pen reaches them. When the plate is full the dandelions let their
// seeds go on the wind before it fades and is drawn again.
export const botanica = {
  fps: 30,
  setup(s) {
    s.time = 0;
  },
  resize(s) {
    // Drawing scale: plants and flowers grow with the screen.
    s.z = s.k * Math.max(0.5, Math.min(1.6, Math.min(s.w, s.h) / 680));
    s.fl = flora(s.pal);
    // Green-black ink for outlines. On a dark ground the stems are drawn
    // in green and the outlines cut dark, like leading in glass.
    s.inkC = s.pal.dark ? mix(s.pal.ink, s.fl.leaf[0], 0.35) : mix(s.pal.mist, s.fl.leaf[0], 0.35);
    s.stemC = s.pal.dark ? s.fl.leaf[1] : s.inkC;
    s.bed = document.createElement("canvas");
    s.bed.width = Math.max(1, Math.round(s.w * s.dpr));
    s.bed.height = Math.max(1, Math.round(s.h * s.dpr));
    s.bctx = s.bed.getContext("2d");
    s.bctx.setTransform(s.dpr, 0, 0, s.dpr, 0, 0);
    s.bctx.lineCap = "round";
    s.bctx.lineJoin = "round";
    this.plant(s);
    if (s.sized) this.warm(s, 0, true);
    s.sized = true;
  },
  plant(s) {
    s.bctx.clearRect(0, 0, s.w, s.h);
    const want = Math.max(6, Math.round(s.w / (62 * s.z)));
    const bag = ["flower", "flower", "flower", "flower", "flower", "flower", "fern", "vine", "dandelion", "grass"];
    const queue = Array.from({ length: want }, (_, i) => (i < 2 ? "grass" : pick(bag)));
    Object.assign(s, { tips: [], live: [], heads: [], seeds: [], queue, planted: 0, clock: 0, nextPlant: 0, phase: "grow", phaseT: 0, alpha: 1 });
  },
  // Most plants come up out of the margins, where the page is quiet.
  plantOne(s) {
    const { z: k, h, w } = s;
    const sp = s.queue[s.planted];
    s.planted += 1;
    const fromWall = sp === "vine" || (sp === "flower" && Math.random() < 0.2);
    let x;
    let y;
    let ang;
    if (!fromWall) {
      // Two borders up the sides, thinning toward the reading column.
      const edge = Math.pow(Math.random(), 1.6) * 0.3;
      const u = Math.random() < 0.5 ? 0.01 + edge : 0.99 - edge;
      x = u * w;
      y = h + 2;
      ang = -Math.PI / 2 + rand(-0.22, 0.22);
    } else {
      const left = Math.random() < 0.5;
      x = left ? -2 : w + 2;
      y = rand(0.3, 0.92) * h;
      ang = left ? rand(-0.9, -0.3) : Math.PI + rand(0.3, 0.9);
    }
    const spec = {
      flower: [rand(0.22, 0.44) * h, 2.8],
      fern: [rand(0.28, 0.5) * h, 2.2],
      vine: [rand(0.35, 0.6) * h, 2],
      dandelion: [rand(0.18, 0.34) * h, 1.8],
      grass: [rand(0.08, 0.16) * h, 1.6],
    }[sp];
    const base = { sp, x, y, depth: 0, dist: 0, next: rand(8, 16) * k, side: pick([-1, 1]), curl: pick([-1, 1]) };
    base.bloom = pick(["daisy", "cosmos", "cosmos", "bell"]);
    base.color = base.bloom === "bell" ? pick([s.fl.violet[2], s.fl.sky[2]]) : pick([s.fl.blossom[2], s.fl.blossom[1], s.fl.white[1], s.fl.poppy[1], s.fl.poppy[2], s.fl.violet[2], s.fl.gold[2], s.fl.sky[2]]);
    if (sp === "grass") {
      for (let i = 0, n = 5 + Math.floor(Math.random() * 4); i < n; i += 1) {
        const len = spec[0] * rand(0.5, 1.1);
        s.tips.push({ ...base, ang: ang + (i / (n - 1) - 0.5) * 1.1 + rand(-0.1, 0.1), left: len, len, width: spec[1] * k + 0.3, seed: Math.random() * 100 });
      }
      return;
    }
    s.tips.push({ ...base, ang, left: spec[0], len: spec[0], width: spec[1] * k + 0.5, seed: Math.random() * 100 });
  },
  grow(s, dt) {
    s.clock += dt;
    if (s.planted < s.queue.length && s.clock >= s.nextPlant) {
      this.plantOne(s);
      s.nextPlant = s.clock + rand(0.35, 1.1);
    }
    const c = s.bctx;
    const ink = s.stemC;
    const next = [];
    for (const tip of s.tips) {
      const speed = (tip.sp === "grass" ? 95 : 72) * s.z;
      const stepLen = Math.min(tip.left, speed * dt * (1 - tip.depth * 0.12));
      const done = 1 - tip.left / tip.len;
      // Each plant's habit steers the stem; the pen's wobble only bends
      // the line around that course.
      if (tip.sp === "vine") tip.ang += Math.sin(tip.dist * 0.02 / s.z + tip.seed) * 0.03;
      else if (tip.sp === "fern") tip.ang += tip.curl * (0.002 + done * done * 0.014);
      else if (tip.sp === "grass") tip.ang += tip.curl * 0.008;
      else tip.ang += Math.sin(-Math.PI / 2 - tip.ang) * 0.02;
      const dir = tip.ang + (noise(tip.seed + tip.dist * 0.02 / s.z, tip.depth) - 0.5) * 0.9;
      const nx = tip.x + Math.cos(dir) * stepLen;
      const ny = tip.y + Math.sin(dir) * stepLen;
      const wdt = tip.width * (1 - done * (tip.sp === "grass" ? 0.8 : 0.4));
      c.beginPath();
      c.moveTo(tip.x, tip.y);
      c.lineTo(nx, ny);
      c.lineWidth = wdt;
      c.strokeStyle = css(tip.sp === "grass" ? s.fl.leaf[1] : ink, 0.95);
      c.stroke();
      // A lighter second pass beside the first, as a pen retraces a line.
      c.beginPath();
      c.moveTo(tip.x + 0.8, tip.y + 0.4);
      c.lineTo(nx + 0.8, ny + 0.4);
      c.lineWidth = Math.max(0.5, wdt * 0.4);
      c.strokeStyle = css(ink, 0.22);
      c.stroke();
      tip.x = nx;
      tip.y = ny;
      tip.left -= stepLen;
      tip.dist += stepLen;
      if (tip.dist >= tip.next) this.feature(s, tip, done);
      if (tip.left > 0.01) next.push(tip);
      else this.finish(s, tip, next);
    }
    s.tips = next;
    return s.planted >= s.queue.length && !s.tips.length && !s.live.length;
  },
  // Leaves, pinnae and tendrils along a stem as the pen passes.
  feature(s, tip, done) {
    const k = s.z;
    const { x, y, ang } = tip;
    tip.side = -tip.side;
    if (tip.sp === "flower" && tip.depth < 3) {
      const len = rand(14, 24) * k * (1 - tip.depth * 0.2);
      this.add(s, { type: "leaf", x, y, ang: ang + tip.side * rand(0.6, 1), len, wid: len * 0.32 });
      tip.next = tip.dist + rand(22, 36) * k;
    } else if (tip.sp === "fern") {
      const len = (5 + 20 * Math.sin(Math.PI * Math.min(1, done * 1.1))) * k;
      for (const side of [-1, 1]) this.add(s, { type: "leaf", x, y, ang: ang + side * 1.05, len, wid: len * 0.28, dur: 0.35, bare: true });
      tip.next = tip.dist + 5 * k;
    } else if (tip.sp === "vine") {
      if (Math.random() < 0.2) this.add(s, { type: "tendril", x, y, ang: ang + tip.side * 0.9, r: rand(6, 10) * k, turn: tip.side });
      else this.add(s, { type: "heart", x, y, ang: ang + tip.side * rand(0.8, 1.2), len: rand(16, 24) * k });
      tip.next = tip.dist + rand(14, 22) * k;
    } else {
      tip.next = Infinity;
    }
  },
  finish(s, tip, next) {
    const k = s.z;
    if (tip.sp === "flower") {
      // A stem ends in a full flower, and every fork on the way carries
      // a smaller one, so each plant finishes in a spray.
      const end = tip.depth >= 2 || tip.len < 22 * k;
      if (end || tip.depth >= 1) this.add(s, { type: "bloom", x: tip.x, y: tip.y, r: (end ? rand(14, 22) : rand(9, 14)) * k + 2, turn: Math.random() * TAU, bloom: tip.bloom, color: tip.color, dur: 0.9 });
      if (end) return;
      const forks = Math.random() < 0.3 ? 3 : 2;
      for (let f = 0; f < forks; f += 1) {
        const spread = forks === 3 ? (f - 1) * 0.55 : (f ? 1 : -1) * rand(0.3, 0.6);
        const len = tip.len * rand(0.5, 0.72);
        next.push({ ...tip, ang: tip.ang + spread + rand(-0.12, 0.12), left: len, len, width: tip.width * 0.72, depth: tip.depth + 1, seed: Math.random() * 100, dist: 0, next: rand(8, 14) * k });
      }
    } else if (tip.sp === "fern") {
      this.add(s, { type: "tendril", x: tip.x, y: tip.y, ang: tip.ang, r: rand(5, 8) * k, turn: tip.curl, dur: 0.8 });
    } else if (tip.sp === "vine") {
      this.add(s, { type: "bloom", x: tip.x, y: tip.y, r: rand(5, 7) * k + 1, turn: 0, bloom: "bell", color: s.fl.violet[2], dur: 0.9 });
    } else if (tip.sp === "dandelion") {
      const n = 26 + Math.floor(Math.random() * 10);
      s.heads.push({ x: tip.x, y: tip.y, r: rand(14, 20) * s.z, t0: s.time, seeds: Array.from({ length: n }, (_, i) => ({ a: (i / n) * TAU + rand(-0.08, 0.08), on: true })) });
    }
  },
  add(s, item) {
    item.t0 = s.time;
    item.dur = item.dur || 0.6;
    s.live.push(item);
  },
  // Finished leaves and petals are inked into the plate for good.
  settle(s) {
    s.live = s.live.filter((item) => {
      if (s.time - item.t0 < item.dur) return true;
      this.paint(s, s.bctx, item, 1);
      return false;
    });
  },
  paint(s, c, it, e) {
    const { fl } = s;
    const ink = css(s.inkC, 0.9);
    c.lineWidth = 1.1 * s.z + 0.3;
    if (it.type === "leaf") {
      const len = it.len * e;
      const wid = it.wid * e;
      const ca = Math.cos(it.ang);
      const sa = Math.sin(it.ang);
      const tx = it.x + ca * len;
      const ty = it.y + sa * len;
      const mx = it.x + ca * len * 0.42;
      const my = it.y + sa * len * 0.42;
      c.beginPath();
      c.moveTo(it.x, it.y);
      c.quadraticCurveTo(mx - sa * wid * 1.6, my + ca * wid * 1.6, tx, ty);
      c.quadraticCurveTo(mx + sa * wid * 1.6, my - ca * wid * 1.6, it.x, it.y);
      c.fillStyle = css(it.bare ? fl.leaf[2] : fl.leaf[1], 0.9);
      c.fill();
      c.strokeStyle = ink;
      c.stroke();
      if (it.bare || len < 6) return;
      // Midrib and a pair of veins, as an engraver would cut them.
      c.beginPath();
      c.moveTo(it.x, it.y);
      c.lineTo(it.x + ca * len * 0.88, it.y + sa * len * 0.88);
      for (const f of [0.35, 0.6]) {
        const bx = it.x + ca * len * f;
        const by = it.y + sa * len * f;
        for (const side of [-1, 1]) {
          c.moveTo(bx, by);
          c.lineTo(bx + ca * len * 0.14 - sa * wid * 0.8 * side, by + sa * len * 0.14 + ca * wid * 0.8 * side);
        }
      }
      c.lineWidth = 0.5;
      c.strokeStyle = css(s.inkC, 0.45);
      c.stroke();
    } else if (it.type === "heart") {
      // An ivy leaf: three shallow lobes around a heart-shaped base.
      const L = it.len * e;
      c.save();
      c.translate(it.x, it.y);
      c.rotate(it.ang);
      c.beginPath();
      ivyLeaf(c, L);
      c.fillStyle = css(fl.leaf[1], 0.9);
      c.fill();
      c.strokeStyle = ink;
      c.stroke();
      c.beginPath();
      c.moveTo(0, 0);
      c.lineTo(L * 0.85, 0);
      c.moveTo(L * 0.25, 0);
      c.lineTo(L * 0.55, -L * 0.3);
      c.moveTo(L * 0.25, 0);
      c.lineTo(L * 0.55, L * 0.3);
      c.lineWidth = 0.5;
      c.strokeStyle = css(s.inkC, 0.45);
      c.stroke();
      c.restore();
    } else if (it.type === "tendril") {
      // A coiled tendril, or a fern's unrolled fiddlehead.
      c.beginPath();
      const turns = 1.8 * e + 0.2;
      for (let i = 0; i <= 40; i += 1) {
        const f = i / 40;
        const a = it.ang + it.turn * f * turns * TAU;
        const r = it.r * (1 - f * 0.85);
        const px = it.x + Math.cos(it.ang) * it.r * f * 1.2 + Math.cos(a + it.turn * Math.PI / 2) * r * f;
        const py = it.y + Math.sin(it.ang) * it.r * f * 1.2 + Math.sin(a + it.turn * Math.PI / 2) * r * f;
        if (i) c.lineTo(px, py);
        else c.moveTo(px, py);
      }
      c.strokeStyle = ink;
      c.stroke();
    } else {
      this.bloom(s, c, it, e);
    }
  },
  bloom(s, c, b, e) {
    const { fl } = s;
    const ink = css(s.inkC, 0.9);
    const r = b.r * (0.2 + 0.8 * e);
    c.save();
    c.translate(b.x, b.y);
    c.lineWidth = 1 * s.z + 0.3;
    if (b.bloom === "bell") {
      // A nodding bellflower on a short hooked stalk.
      c.beginPath();
      c.moveTo(0, 0);
      c.quadraticCurveTo(r * 0.8, -r * 0.4, r * 0.9, r * 0.3);
      c.strokeStyle = ink;
      c.stroke();
      c.translate(r * 0.9, r * 0.3);
      c.beginPath();
      c.moveTo(-r * 0.28, 0);
      c.bezierCurveTo(-r * 0.4, r * 0.6, -r * 0.55, r * 0.9, -r * 0.6, r * 1.15);
      for (let i = 0; i <= 4; i += 1) c.lineTo(-r * 0.6 + (i + 0.5) * r * 0.3, r * (i % 2 ? 1.05 : 1.25));
      c.bezierCurveTo(r * 0.55, r * 0.9, r * 0.4, r * 0.6, r * 0.28, 0);
      c.closePath();
      c.fillStyle = css(b.color, 0.92);
      c.fill();
      c.strokeStyle = ink;
      c.stroke();
      c.beginPath();
      c.moveTo(0, r * 0.2);
      c.lineTo(0, r * 0.95);
      c.moveTo(-r * 0.2, r * 0.3);
      c.lineTo(-r * 0.3, r * 0.9);
      c.lineWidth = 0.5;
      c.strokeStyle = css(s.inkC, 0.4);
      c.stroke();
      c.restore();
      return;
    }
    c.rotate(b.turn + (1 - e) * 0.8);
    const daisy = b.bloom === "daisy";
    const n = daisy ? 14 : 8;
    for (let i = 0; i < n; i += 1) {
      c.save();
      c.rotate((i / n) * TAU);
      c.beginPath();
      if (daisy) {
        c.ellipse(r * 0.58, 0, r * 0.46, r * 0.12, 0, 0, TAU);
      } else {
        // A broad cosmos petal with a notched tip.
        const w = r * 0.34;
        c.moveTo(0, 0);
        c.quadraticCurveTo(r * 0.5, -w * 1.3, r, -w * 0.7);
        c.lineTo(r * 0.9, -w * 0.25);
        c.lineTo(r * 0.98, 0);
        c.lineTo(r * 0.9, w * 0.25);
        c.lineTo(r, w * 0.7);
        c.quadraticCurveTo(r * 0.5, w * 1.3, 0, 0);
      }
      c.fillStyle = css(daisy ? fl.white[1] : b.color, 0.92);
      c.fill();
      c.strokeStyle = ink;
      c.stroke();
      if (!daisy && r > 5) {
        // Hatching toward the heart of the flower.
        c.beginPath();
        for (const off of [-0.18, 0, 0.18]) {
          c.moveTo(r * 0.16, r * off * 0.6);
          c.lineTo(r * 0.42, r * off);
        }
        c.lineWidth = 0.45;
        c.strokeStyle = css(s.inkC, 0.4);
        c.stroke();
        c.lineWidth = 1 * s.z + 0.3;
      }
      c.restore();
    }
    c.beginPath();
    c.arc(0, 0, r * (daisy ? 0.22 : 0.16), 0, TAU);
    c.fillStyle = css(fl.gold[1], 0.9);
    c.fill();
    c.strokeStyle = ink;
    c.stroke();
    for (let i = 0; i < 5; i += 1) {
      const a = (i / 5) * TAU;
      c.fillStyle = css(fl.gold[0], 0.9);
      c.fillRect(Math.cos(a) * r * 0.1 - 0.5, Math.sin(a) * r * 0.1 - 0.5, 1, 1);
    }
    c.restore();
  },
  // Dandelion clocks: each seed a stalk and a tiny parachute.
  clock(s, c, hd) {
    const e = clamp01((s.time - hd.t0) / 0.7);
    const r = hd.r * e;
    c.strokeStyle = css(s.fl.white[1], 0.55);
    c.lineWidth = 0.5;
    c.beginPath();
    for (const sd of hd.seeds) {
      if (!sd.on) continue;
      const ex = hd.x + Math.cos(sd.a) * r;
      const ey = hd.y + Math.sin(sd.a) * r;
      c.moveTo(hd.x + Math.cos(sd.a) * r * 0.2, hd.y + Math.sin(sd.a) * r * 0.2);
      c.lineTo(ex, ey);
      for (const f of [-0.5, 0, 0.5]) {
        c.moveTo(ex, ey);
        c.lineTo(ex + Math.cos(sd.a + f) * r * 0.22, ey + Math.sin(sd.a + f) * r * 0.22);
      }
    }
    c.stroke();
    c.beginPath();
    c.arc(hd.x, hd.y, Math.max(1, r * 0.14), 0, TAU);
    c.fillStyle = css(s.fl.gold[0], 0.8);
    c.fill();
  },
  // In the quiet after the plate is full, the wind picks seeds off the
  // dandelion clocks and carries them away.
  release(s, dt) {
    const g = gustiness(s.time);
    for (const hd of s.heads) {
      if (Math.random() > dt * (0.6 + g * 5)) continue;
      const sd = hd.seeds.find((x) => x.on && Math.random() < 0.3);
      if (!sd) continue;
      sd.on = false;
      s.seeds.push({ x: hd.x + Math.cos(sd.a) * hd.r, y: hd.y + Math.sin(sd.a) * hd.r, vy: rand(-14, -5) * s.z, ph: Math.random() * TAU, life: 0 });
    }
  },
  drift(s, dt) {
    s.seeds = s.seeds.filter((sd) => {
      sd.life += dt;
      sd.x += (gust(sd.x / s.w, s.time) * 28 + 10) * s.z * dt;
      sd.y += (sd.vy + Math.sin(sd.ph + s.time * 1.7) * 6 * s.z) * dt;
      return sd.life < 14 && sd.x < s.w + 10 && sd.y > -10;
    });
  },
  step(s, dt) {
    s.time += dt;
    s.alpha = season(s, dt, {
      grow: () => this.grow(s, dt),
      rest: () => this.release(s, dt),
      restFor: 24,
      fadeFor: 3.5,
      replant: () => this.plant(s),
    });
    this.settle(s);
    this.drift(s, dt);
  },
  warm(s, t, full) {
    for (let i = 0; i < 4000; i += 1) {
      if (s.phase === "grow" ? !full && s.clock > 8 : !full || s.phaseT > 8) break;
      this.step(s, 1 / 30);
    }
  },
  draw(s, t, dt) {
    this.step(s, Math.min(dt, 0.1));
    const { ctx } = s;
    ctx.clearRect(0, 0, s.w, s.h);
    ctx.globalAlpha = s.alpha;
    ctx.drawImage(s.bed, 0, 0, s.w, s.h);
    ctx.lineCap = "round";
    ctx.lineJoin = "round";
    for (const item of s.live) this.paint(s, ctx, item, clamp01((s.time - item.t0) / item.dur));
    for (const hd of s.heads) this.clock(s, ctx, hd);
    ctx.strokeStyle = css(s.fl.white[1], 0.6);
    ctx.lineWidth = 0.6;
    ctx.beginPath();
    for (const sd of s.seeds) {
      const r = 3 * s.z + 1;
      ctx.moveTo(sd.x, sd.y);
      ctx.lineTo(sd.x - 0.4 * r, sd.y + r * 1.2);
      for (const f of [-0.6, 0, 0.6]) {
        ctx.moveTo(sd.x, sd.y);
        ctx.lineTo(sd.x + Math.sin(f) * r, sd.y - Math.cos(f) * r);
      }
    }
    ctx.stroke();
    ctx.globalAlpha = 1;
  },
};
