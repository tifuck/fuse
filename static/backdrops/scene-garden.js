// Scene: garden.
// Verbatim extraction from static/chat-backdrop.js; chunk map: backdrop-manifest.json in the snapshot parent.

import { TAU } from "./env.js";
import { mix, clamp01 } from "./color.js";
import { noise, hash, rand } from "./noise.js";
import { cells, blit } from "./kit.js";
import { plot, sprite, dither, SPRITES } from "./pixel.js";
import { seeded, newSeed, flora, season, gustiness, gust, sway } from "./botany.js";

// —— Garden family ——

// An 8-bit garden that plants itself. A sakura grows up from one side and
// opens its blossom a cloud at a time, letting petals tumble down onto the
// grass. A vine climbs the far wall and hangs wisteria from the top, and a
// flower bed sprouts along the floor. After a long bloom the tree sheds,
// the scene fades, and the next garden grows with the tree on the other
// side.
export const garden = {
  fps: 15,
  previewK: 0.4,
  rest: 85,
  setup(s) {
    s.side = Math.random() < 0.5 ? -1 : 1;
    s.time = Math.random() * 100;
  },
  resize(s) {
    cells(s, Math.max(2, Math.round(5 * s.k)));
    s.base = new Uint8ClampedArray(s.img.data.length);
    s.fl = flora(s.pal);
    const b = s.fl.blossom;
    // Slot 4 is leaf; 5 is a single flower's petal, near white.
    b[5] = mix(b[3], s.pal.dark ? [255, 255, 255] : s.pal.mist, s.pal.dark ? 0.55 : 0.1);
    s.fl.shade = [mix(b[0], s.pal.ink, 0.25), b[0], b[1], b[2], null, b[3]];
    this.plant(s);
    // A resize replants; show the new layout grown rather than bare.
    if (s.sized) this.warm(s, 0, true);
    s.sized = true;
  },
  plant(s) {
    const R = seeded(newSeed());
    // The scale of the planting; a tall phone screen gets a taller tree.
    const U = Math.min(s.rows, s.cols * 1.5);
    Object.assign(s, {
      U, clock: 0, phase: "grow", phaseT: 0, alpha: 1, speed: U * 0.075, spawn: 0,
      ink: [], inked: 0, clusters: [], racemes: [], flowers: [], petals: [],
      carpet: new Uint8Array(s.cols), grass: new Uint8Array(s.cols), fly: R() * 100,
    });
    s.base.fill(0);
    this.tree(s, R);
    this.vine(s, R);
    this.bed(s, R);
    s.ink.sort((a, b) => a.t - b.t);
    s.clusters.sort((a, b) => b.back - a.back);
    let end = 0;
    for (const e of s.ink) end = Math.max(end, e.t);
    for (const c of s.clusters) end = Math.max(end, c.t + 1);
    for (const r of s.racemes) end = Math.max(end, r.t + 2);
    for (const f of s.flowers) end = Math.max(end, f.t + f.grow);
    s.doneAt = end + 1;
  },

  // The sakura: a short, leaning trunk that splits low into limbs which
  // spread and level off into a wide crown over the top of the screen.
  tree(s, R) {
    const { cols, rows, U } = s;
    const inward = -s.side;
    s.trunkX = s.side < 0 ? cols * R.range(0.07, 0.14) : cols * R.range(0.86, 0.93);
    const r = Math.max(0.8, U * 0.026);
    this.limb(s, R, { x: s.trunkX, y: rows + r, ang: -Math.PI / 2 + inward * R.range(0.1, 0.24), len: U * R.range(0.24, 0.3), r, depth: 0, t: 0, inward });
  },
  limb(s, R, b) {
    const step = 0.7;
    const n = Math.max(2, Math.round(b.len / step));
    const wob = R() * 100;
    const top = s.rows * 0.06;
    let { x, y, ang } = b;
    let dir = ang;
    for (let i = 0; i < n; i += 1) {
      const t = b.t + (i * step) / s.speed;
      if (b.depth) {
        // Sakura limbs spread and level off rather than climb; the outer
        // twigs droop a little.
        const out = Math.cos(ang) >= 0 ? 1 : -1;
        const lift = b.depth >= 4 ? -0.08 : b.depth === 3 ? 0.2 : 0.55;
        const level = out > 0 ? -lift : -Math.PI + lift;
        ang += (level - ang) * 0.007 * b.depth;
      }
      if (y < top) ang += (Math.cos(ang) >= 0 ? 1 : -1) * 0.05;
      // A gnarled line: the wobble bends the limb without steering it.
      dir = ang + (noise(wob + i * 0.09, b.depth) - 0.5) * (b.depth ? 0.9 : 0.5);
      x += Math.cos(dir) * step;
      y += Math.sin(dir) * step;
      s.ink.push({ k: 0, x, y, r: b.r * (1 - 0.32 * (i / n)), t });
      // Blossom along the outer limbs, not only at their tips.
      if (b.depth >= 2 && i % 5 === 3 && R() < 0.6) this.cluster(s, R, x, y, t + 0.4, b.depth === 2 ? 0.8 : 0.65);
    }
    const tEnd = b.t + (n * step) / s.speed;
    if (b.depth >= 5) {
      this.cluster(s, R, x, y, tEnd + 0.2, 1);
      return;
    }
    const forks = b.depth === 0 ? 3 : R() < 0.3 ? 3 : 2;
    for (let f = 0; f < forks; f += 1) {
      const fan = b.depth === 0 ? 0.8 : b.depth === 1 ? 0.6 : 0.45;
      const spread = forks === 3 ? (f - 1) * fan * R.range(0.8, 1.1) : (f ? 1 : -1) * fan * R.range(0.6, 1);
      this.limb(s, R, {
        ...b, x, y,
        ang: dir + spread + (b.depth === 0 ? b.inward * 0.18 : 0),
        len: b.len * R.range(0.62, 0.8) * (b.depth === 0 ? 1.35 : 1),
        r: b.r * 0.64,
        depth: b.depth + 1,
        t: tEnd,
      });
    }
  },
  // A cloud of blossom: a few overlapping discs lit from the upper left,
  // dithered across four pinks with a dark rim underneath, dotted with
  // single flowers and a fresh leaf or two. Cells open from the middle out.
  cluster(s, R, x, y, t, size) {
    const r = Math.max(1.2, s.U * R.range(0.017, 0.028) * size);
    const discs = [[0, 0, r]];
    const extra = Math.floor(R() * 3);
    for (let i = 0; i < extra; i += 1) discs.push([R.range(-1, 1) * r * 0.9, R.range(-0.8, 0.5) * r * 0.8, r * R.range(0.5, 0.85)]);
    const inside = (i, j) => discs.some(([dx, dy, rr]) => (i - dx) ** 2 + (j - dy) ** 2 <= rr * rr + 0.3);
    const cx = Math.round(x);
    const cy = Math.round(y);
    const ext = Math.ceil(r * 2);
    const tone = new Map();
    for (let j = -ext; j <= ext; j += 1) {
      for (let i = -ext; i <= ext; i += 1) {
        if (!inside(i, j)) continue;
        const lit = clamp01(0.5 - (i * 0.5 + j * 0.85) / (r * 2.2));
        let v = Math.max(1, Math.min(3, Math.round(lit * 3 + dither(cx + i, cy + j) * 1.2)));
        if (!inside(i, j + 1) || (!inside(i + 1, j) && lit < 0.5)) v = 0;
        tone.set(i + "," + j, v);
      }
    }
    const keys = [...tone.keys()];
    for (let f = Math.max(1, Math.round(keys.length / 16)); f > 0; f -= 1) {
      const [i, j] = R.pick(keys).split(",").map(Number);
      if (tone.get(i + "," + j) === 0) continue;
      tone.set(i + "," + j, 1);
      for (const [di, dj] of [[1, 0], [-1, 0], [0, 1], [0, -1]]) if (tone.get(i + di + "," + (j + dj))) tone.set(i + di + "," + (j + dj), 5);
    }
    for (let l = Math.floor(R() * 3); l > 0; l -= 1) {
      const [i, j] = R.pick(keys).split(",").map(Number);
      if (!inside(i, j + 1)) tone.set(i + "," + j, 4);
    }
    const list = keys.map((key) => key.split(",").map(Number)).sort((a, b) => Math.hypot(a[0], a[1]) - Math.hypot(b[0], b[1]));
    const flat = new Int8Array(list.length * 3);
    list.forEach(([i, j], n) => { flat[n * 3] = i; flat[n * 3 + 1] = j; flat[n * 3 + 2] = tone.get(i + "," + j); });
    // A third of the clouds sit behind the rest, a shade darker.
    s.clusters.push({ x: cx, y: cy, t, cells: flat, shed: R(), gone: false, back: R() < 0.35 ? 1 : 0 });
  },

  // A vine up the far wall that arches over at the top and hangs wisteria.
  vine(s, R) {
    const { cols, rows, U, fl } = s;
    const wall = -s.side;
    const x0 = wall < 0 ? R.range(1, 3) : cols - R.range(2, 4);
    const top = rows * R.range(0.08, 0.14);
    const ph = R() * TAU;
    const amp = Math.max(1, U * 0.014);
    const speed = s.speed * 0.8;
    const leaf = { a: fl.leaf[0], b: fl.leaf[1], c: fl.leaf[2] };
    const bloom = { c: R() < 0.5 ? fl.violet[2] : fl.sky[2], d: fl.gold[2] };
    let t = R.range(0.5, 2);
    let n = 0;
    const along = (x, y, arch) => {
      s.ink.push({ k: 1, x, y, t });
      n += 1;
      t += 0.5 / speed;
      if (n % 7 === 0) {
        const up = (n / 7) & 1;
        if (arch) s.ink.push({ k: 2, map: SPRITES.leafSmall, x: x - (up ? 0 : 1), y: y + (up ? -2 : 1), slots: leaf, flip: !up, t: t + 0.3 });
        else s.ink.push({ k: 2, map: SPRITES.leaf, x: up ? x + 1 : x - 4, y: y - 3, slots: leaf, flip: !up, t: t + 0.3 });
      }
      if (!arch && n % 23 === 11) s.ink.push({ k: 2, map: SPRITES.curl, x: x - wall * 2 - 1, y: y - 4, slots: leaf, flip: wall > 0, t: t + 0.5 });
      if (n % 31 === 17) s.ink.push({ k: 2, map: SPRITES.bloom, x: x - 1, y: y - 1, slots: bloom, t: t + 0.8 });
    };
    let x = x0;
    for (let y = rows + 1; y > top; y -= 0.5) {
      x = x0 - wall * (amp + Math.sin(y * 0.11 + ph) * amp);
      along(x, y, false);
    }
    const xTop = x;
    const L = cols * R.range(0.16, 0.26);
    let next = R.range(2, 5);
    for (let d = 0; d < L; d += 0.5) {
      const u = d / L;
      const ax = xTop - wall * d;
      const ay = top - Math.sin(Math.min(1, u * 2) * Math.PI * 0.5) * U * 0.03 + u * u * U * 0.05;
      along(ax, ay, true);
      if (d >= next) {
        next = d + R.range(3, 6);
        s.racemes.push({ x: Math.round(ax), y: Math.round(ay) + 1, len: Math.max(2, Math.round(U * R.range(0.035, 0.09) * (1 - u * 0.5))), t: t + R.range(0.5, 3) });
      }
    }
  },

  // The flower bed: grass along the floor and a scatter of tulips,
  // daisies, poppies and bellflowers, sparse through the reading column.
  bed(s, R) {
    const { cols, U, fl } = s;
    for (let x = 0; x < cols; x += 1) s.grass[x] = Math.min(3, Math.floor(noise(x * 0.35, 3.1) * 3 + hash(x, 9) * 1.4));
    const three = (r) => ({ a: r[0], b: r[1], c: r[2] });
    const kinds = [
      { map: SPRITES.tulip, slots: three(fl.poppy) },
      { map: SPRITES.tulip, slots: three(fl.accent) },
      { map: SPRITES.tulipOpen, slots: three(fl.blossom.slice(1)) },
      { map: SPRITES.bloom, slots: { c: fl.white[1], d: fl.gold[1] } },
      { map: SPRITES.bloom, slots: { c: fl.gold[2], d: fl.gold[0] } },
      { map: SPRITES.poppy, slots: three(fl.poppy) },
      { map: SPRITES.bell, slots: three(fl.violet) },
      { map: SPRITES.bell, slots: three(fl.sky) },
    ];
    const n = Math.round(cols * 0.16);
    for (let i = 0; i < n; i += 1) {
      const x = Math.floor(R() * cols);
      const u = x / cols;
      if (u > 0.3 && u < 0.7 && R() < 0.75) continue;
      if (Math.abs(x - s.trunkX) < U * 0.045) continue;
      s.flowers.push({ x, h: Math.round(R.range(2, U * 0.06 + 2)), kind: R.pick(kinds), t: R.range(1.5, 24), grow: R.range(1.2, 3), leaf: R() < 0.6 ? R.range(0.3, 0.6) : 0, flip: R() < 0.5 });
    }
  },

  stamp(s, e) {
    const { base, fl } = s;
    if (e.k === 1) return plot(s, base, e.x, e.y, fl.leaf[0]);
    if (e.k === 2) return sprite(s, base, e.map, Math.round(e.x), Math.round(e.y), e.slots, e.flip);
    const r = e.r;
    if (r < 0.75) return plot(s, base, e.x, e.y, fl.bark[1]);
    const x0 = Math.floor(e.x - r);
    const y0 = Math.floor(e.y - r);
    for (let j = y0; j <= e.y + r; j += 1) {
      for (let i = x0; i <= e.x + r; i += 1) {
        const dx = i + 0.5 - e.x;
        const dy = j + 0.5 - e.y;
        if (dx * dx + dy * dy > r * r) continue;
        const u = dx / r;
        let v = u < -0.4 ? 2 : u > 0.35 ? 0 : 1;
        // Cherry bark: short horizontal lenticels across the trunk.
        if (r > 2 && v === 1 && hash(j, 71) < 0.16 && hash(i >> 1, j, 3) < 0.75) v = 0;
        plot(s, base, i, j, fl.bark[v]);
      }
    }
  },
  open(c, clock) {
    return !c.gone && clock >= c.t + 0.9;
  },
  step(s, dt) {
    s.time += dt;
    s.clock += dt;
    s.alpha = season(s, dt, {
      grow: () => {
        while (s.inked < s.ink.length && s.ink[s.inked].t <= s.clock) this.stamp(s, s.ink[s.inked++]);
        return s.clock >= s.doneAt;
      },
      rest: () => this.shed(s),
      restFor: this.rest,
      fadeFor: 4,
      replant: () => {
        s.side = -s.side;
        this.plant(s);
      },
    });
    this.drift(s, dt);
  },
  // The end of the bloom: the canopy lets go a cloud at a time.
  shed(s) {
    const from = this.rest - 16;
    if (s.phase !== "rest" || s.phaseT < from) return;
    for (const c of s.clusters) {
      if (c.gone || s.phaseT < from + c.shed * 14) continue;
      c.gone = true;
      for (let i = 0; i < c.cells.length; i += 3) {
        if (Math.random() < 0.3) this.petal(s, c.x + c.cells[i], c.y + c.cells[i + 1]);
      }
    }
  },
  petal(s, x, y) {
    if (s.petals.length > s.cols * 1.5) return;
    s.petals.push({ x, y, vy: s.U * rand(0.022, 0.04), ph: Math.random() * TAU, tone: 1 + Math.floor(Math.random() * 3) });
  },
  drift(s, dt) {
    const { cols, rows, U, clusters } = s;
    if (s.phase !== "fade" && clusters.length) {
      s.spawn += dt * (1.5 + gustiness(s.time) * 12) * (cols / 160);
      for (; s.spawn >= 1; s.spawn -= 1) {
        const c = clusters[Math.floor(Math.random() * clusters.length)];
        if (!this.open(c, s.clock)) continue;
        const i = Math.floor(Math.random() * (c.cells.length / 3)) * 3;
        this.petal(s, c.x + c.cells[i], c.y + c.cells[i + 1]);
      }
    }
    const k = U / 180;
    s.petals = s.petals.filter((p) => {
      p.x += (gust(p.x / cols, s.time) * 9 + Math.sin(p.ph + s.time * 2.1) * 2) * k * dt;
      p.y += p.vy * dt * (0.8 + 0.4 * Math.sin(p.ph + s.time * 1.3));
      if (p.x < -3 || p.x > cols + 3) return false;
      const x = Math.floor(p.x);
      if (x >= 0 && x < cols && p.y >= rows - 2 - s.carpet[x]) {
        if (s.carpet[x] < 2 && Math.random() < 0.5) s.carpet[x] += 1;
        return false;
      }
      return true;
    });
  },
  render(s) {
    const { cols, rows, fl, time, clock } = s;
    const px = s.img.data;
    px.set(s.base);
    // Grass, then the petals that have settled into it.
    const grown = Math.floor(clock * 2);
    for (let x = 0; x < cols; x += 1) {
      plot(s, px, x, rows - 1, fl.leaf[0]);
      const h = Math.min(s.grass[x], grown);
      for (let k = 0; k < h; k += 1) {
        const tip = k === h - 1;
        plot(s, px, x + (tip && h > 1 ? sway(x / cols, time, 0.6) : 0), rows - 2 - k, tip ? fl.leaf[2] : fl.leaf[1]);
      }
      for (let k = 0; k < s.carpet[x]; k += 1) plot(s, px, x, rows - 2 - k, fl.blossom[k ? 3 : 2]);
    }
    for (const f of s.flowers) {
      const g = clamp01((clock - f.t) / f.grow);
      if (g <= 0) continue;
      const h = Math.max(1, Math.round(f.h * g));
      const dx = sway(f.x / cols, time, 0.8);
      for (let k = 0; k < h; k += 1) plot(s, px, f.x + (k > h - 2 ? dx : 0), rows - 2 - k, fl.leaf[1]);
      if (f.leaf && h > 3) plot(s, px, f.x + (f.flip ? -1 : 1), rows - 2 - Math.round(h * f.leaf), fl.leaf[2]);
      if (g < 1) {
        sprite(s, px, SPRITES.bud, f.x + dx, rows - 3 - h, { a: fl.leaf[1], b: f.kind.slots.b || f.kind.slots.c });
        continue;
      }
      const map = f.kind.map;
      sprite(s, px, map, f.x + dx - (map[0].length >> 1), rows - 1 - h - map.length, f.kind.slots, f.flip);
    }
    // Wisteria: racemes open from the top down, the tips still in bud.
    for (const r of s.racemes) {
      const g = clamp01((clock - r.t) / 2);
      if (g <= 0) continue;
      const shown = Math.ceil(r.len * g);
      for (let k = 0; k < shown; k += 1) {
        const f = k / r.len;
        const width = f < 0.3 ? 3 : f < 0.7 ? 2 : 1;
        const dx = Math.max(-1, Math.min(1, Math.round(gust(r.x / cols, time) * f * 1.4)));
        for (let w = 0; w < width; w += 1) {
          const x = r.x - (width >> 1) + w + dx;
          const d = dither(x, r.y + k) > 0;
          plot(s, px, x, r.y + k, fl.violet[f < 0.55 ? (d ? 2 : 1) : d ? 1 : 0]);
        }
      }
    }
    for (const c of s.clusters) {
      if (c.gone) continue;
      const p = (clock - c.t) / 0.9;
      if (p <= 0) continue;
      const n = c.cells.length / 3;
      const shown = p >= 1 ? n : Math.ceil(n * p * p);
      const dx = sway(c.x / cols, time, 0.8);
      const bud = p < 0.4;
      const pink = c.back ? fl.shade : fl.blossom;
      for (let i = 0; i < shown; i += 1) {
        const v = c.cells[i * 3 + 2];
        plot(s, px, c.x + dx + c.cells[i * 3], c.y + c.cells[i * 3 + 1], v === 4 ? fl.leaf[1] : pink[bud ? Math.min(v, 1) : v]);
      }
    }
    // Petals tumble: flat, then edge-on, then flat again.
    for (const p of s.petals) {
      const c = fl.blossom[p.tone];
      plot(s, px, p.x, p.y, c);
      if (Math.sin(p.ph + time * 4.5) > 0.2) plot(s, px, p.x + 1, p.y, fl.blossom[p.tone - 1]);
    }
    // One butterfly works the flower bed once it is in bloom.
    if (s.phase === "rest") {
      const bx = cols * (0.5 + 0.47 * Math.sin(time * 0.037 + s.fly)) + (noise(time * 0.3, s.fly) - 0.5) * s.U * 0.12;
      const by = rows - s.U * (0.08 + 0.1 * noise(time * 0.22, s.fly + 5));
      sprite(s, px, Math.floor(time * 7) & 1 ? SPRITES.flyOpen : SPRITES.flyShut, Math.round(bx), Math.round(by), { a: fl.bark[0], b: fl.accent[1], c: fl.accent[2] });
    }
    blit(s, false, s.alpha);
  },
  warm(s, t, full) {
    const until = full ? s.doneAt + 14 : s.doneAt * 0.6;
    for (let i = 0; i < 5000 && s.clock < until && s.phase !== "fade"; i += 1) this.step(s, 1 / 15);
  },
  draw(s, t, dt) {
    this.step(s, Math.min(dt, 0.2));
    this.render(s);
  },
};
