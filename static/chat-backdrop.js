/* Animated chat backdrops. chat-appearance.js owns the choice and strength;
   this file only draws. Every scene reads its palette from the active theme,
   renders at low resolution on its own frame budget, pauses while the tab is
   hidden, and holds a single still frame under prefers-reduced-motion. */
(() => {
  const html = document.documentElement;
  const reduce = typeof matchMedia === "function" ? matchMedia("(prefers-reduced-motion: reduce)") : { matches: false };

  // —— Palette ——
  function rgb(prop, fallback) {
    const raw = getComputedStyle(html).getPropertyValue(prop).trim();
    let m = raw.match(/^#([0-9a-f]{6})$/i);
    if (m) return [parseInt(m[1].slice(0, 2), 16), parseInt(m[1].slice(2, 4), 16), parseInt(m[1].slice(4, 6), 16)];
    m = raw.match(/rgba?\(\s*([\d.]+)[,\s]+([\d.]+)[,\s]+([\d.]+)/);
    if (m) return [Number(m[1]), Number(m[2]), Number(m[3])];
    return fallback;
  }
  function palette() {
    return {
      ink: rgb("--ink", [18, 18, 18]),
      mist: rgb("--mist", [236, 236, 236]),
      mute: rgb("--mute", [154, 154, 154]),
      brass: rgb("--brass", [197, 223, 136]),
      steel: rgb("--steel", [166, 201, 175]),
      ok: rgb("--ok", [174, 211, 151]),
      line: rgb("--line", [46, 46, 46]),
    };
  }
  const css = (c, a = 1) => `rgba(${c[0]},${c[1]},${c[2]},${a})`;
  const mix = (a, b, t) => [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t];

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
  function noise(x, y) {
    const xi = Math.floor(x);
    const yi = Math.floor(y);
    const xf = x - xi;
    const yf = y - yi;
    const X = xi & 255;
    const Y = yi & 255;
    const a = PERM[PERM[X] + Y] / 255;
    const b = PERM[PERM[X + 1] + Y] / 255;
    const c = PERM[PERM[X] + Y + 1] / 255;
    const d = PERM[PERM[X + 1] + Y + 1] / 255;
    const u = fade(xf);
    const v = fade(yf);
    return a + (b - a) * u + (c - a) * v + (a - b - c + d) * u * v;
  }
  // Two layers drifting against each other read as a slowly evolving field.
  function field(x, y, t) {
    return 0.62 * noise(x + t * 0.35, y - t * 0.12) + 0.38 * noise(x * 2.1 - t * 0.22, y * 2.1 + t * 0.3);
  }

  // 8×8 Bayer matrix, normalised to 0..1.
  const BAYER = (() => {
    const m = [0, 32, 8, 40, 2, 34, 10, 42, 48, 16, 56, 24, 50, 18, 58, 26, 12, 44, 4, 36, 14, 46, 6, 38, 60, 28, 52, 20, 62, 30, 54, 22,
      3, 35, 11, 43, 1, 33, 9, 41, 51, 19, 59, 27, 49, 17, 57, 25, 15, 47, 7, 39, 13, 45, 5, 37, 63, 31, 55, 23, 61, 29, 53, 21];
    return Float32Array.from(m, (v) => (v + 0.5) / 64);
  })();

  // —— Scenes ——
  const SCENES = {
    // 1-bit ordered dither of a drifting noise field, drawn at 1/5 scale and
    // blown up with hard pixels.
    dither: {
      fps: 12,
      setup(s) {
        s.cell = 5;
        s.off = document.createElement("canvas");
        s.offCtx = s.off.getContext("2d");
      },
      resize(s) {
        s.cols = Math.ceil(s.w / s.cell);
        s.rows = Math.ceil(s.h / s.cell);
        s.off.width = s.cols;
        s.off.height = s.rows;
        s.img = s.offCtx.createImageData(s.cols, s.rows);
      },
      draw(s, t) {
        const { cols, rows, img, pal } = s;
        const px = img.data;
        const hi = pal.brass;
        const lo = pal.steel;
        const k = 0.018;
        for (let y = 0; y < rows; y += 1) {
          // A soft horizon: the field thins toward the top of the screen.
          const lift = 0.18 + 0.82 * (y / rows);
          for (let x = 0; x < cols; x += 1) {
            const v = field(x * k, y * k * 1.25, t * 0.08) * lift;
            const i = (y * cols + x) * 4;
            if (v > BAYER[(x & 7) + ((y & 7) << 3)] + 0.12) {
              const c = v > 0.62 ? hi : lo;
              px[i] = c[0]; px[i + 1] = c[1]; px[i + 2] = c[2]; px[i + 3] = 255;
            } else {
              px[i + 3] = 0;
            }
          }
        }
        s.offCtx.putImageData(img, 0, 0);
        s.ctx.clearRect(0, 0, s.w, s.h);
        s.ctx.imageSmoothingEnabled = false;
        s.ctx.drawImage(s.off, 0, 0, cols * s.cell, rows * s.cell);
      },
    },

    // ASCII flow field: a glyph per cell, chosen by the field's brightness.
    // Glyphs are pre-rendered into an atlas so a frame is only drawImage calls.
    glyphs: {
      fps: 10,
      ramp: " .·:-=+*#%@",
      setup(s) {
        s.cw = 10;
        s.ch = 18;
      },
      resize(s) {
        s.cols = Math.ceil(s.w / s.cw);
        s.rows = Math.ceil(s.h / s.ch);
        this.atlas(s);
      },
      atlas(s) {
        const dpr = s.dpr;
        const ramp = this.ramp;
        const tones = [css(s.pal.mute, 0.55), css(s.pal.steel, 0.9), css(s.pal.brass, 1)];
        const a = document.createElement("canvas");
        a.width = Math.ceil(ramp.length * s.cw * dpr);
        a.height = Math.ceil(tones.length * s.ch * dpr);
        const c = a.getContext("2d");
        c.scale(dpr, dpr);
        c.font = `12px "Red Hat Mono", ui-monospace, monospace`;
        c.textAlign = "center";
        c.textBaseline = "middle";
        tones.forEach((tone, row) => {
          c.fillStyle = tone;
          for (let i = 1; i < ramp.length; i += 1) c.fillText(ramp[i], i * s.cw + s.cw / 2, row * s.ch + s.ch / 2);
        });
        s.atlasCanvas = a;
      },
      draw(s, t) {
        const { ctx, cols, rows, cw, ch, dpr } = s;
        const n = this.ramp.length - 1;
        const k = 0.075;
        ctx.clearRect(0, 0, s.w, s.h);
        for (let y = 0; y < rows; y += 1) {
          for (let x = 0; x < cols; x += 1) {
            let v = field(x * k, y * k * 1.8, t * 0.12);
            // A slow diagonal swell sweeps through the field.
            v += 0.16 * Math.sin((x * 0.045 + y * 0.09) - t * 0.35);
            v = (v - 0.38) * 1.9;
            if (v <= 0.08) continue;
            const g = Math.min(n, 1 + Math.floor(v * n));
            const tone = v > 0.82 ? 2 : v > 0.45 ? 1 : 0;
            ctx.drawImage(s.atlasCanvas, g * cw * dpr, tone * ch * dpr, cw * dpr, ch * dpr, x * cw, y * ch, cw, ch);
          }
        }
      },
    },

    // Oscilloscope: a beam traces morphing Lissajous figures. The canvas is
    // never cleared, only faded, so the trace leaves phosphor persistence.
    scope: {
      fps: 60,
      setup(s) {
        s.phase = 0;
      },
      resize(s) {
        s.ctx.clearRect(0, 0, s.w, s.h);
      },
      point(s, p, t) {
        // Frequency ratios glide between 1:2, 2:3 and 3:4; the phase drifts.
        const r = 2 + Math.sin(t * 0.045) * 0.5 + Math.sin(t * 0.017) * 0.5;
        const a = r + 1;
        const b = r;
        const amp = Math.min(s.w, s.h) * 0.34;
        return [s.w / 2 + amp * 1.25 * Math.sin(a * p + t * 0.21), s.h / 2 + amp * Math.sin(b * p)];
      },
      draw(s, t, dt) {
        const { ctx } = s;
        ctx.globalCompositeOperation = "destination-out";
        ctx.fillStyle = `rgba(0,0,0,${Math.min(0.35, dt * 2.2)})`;
        ctx.fillRect(0, 0, s.w, s.h);
        ctx.globalCompositeOperation = "source-over";
        const steps = 48;
        const span = Math.min(0.12, dt) * 2.4;
        const start = s.phase;
        s.phase += span;
        ctx.lineCap = "round";
        ctx.lineJoin = "round";
        // Wide dim halo, then the hot core.
        for (const [width, color] of [[5, css(s.pal.ok, 0.14)], [1.6, css(mix(s.pal.ok, s.pal.mist, 0.35), 0.95)]]) {
          ctx.beginPath();
          for (let i = 0; i <= steps; i += 1) {
            const [x, y] = this.point(s, start + (span * i) / steps, t);
            if (i) ctx.lineTo(x, y);
            else ctx.moveTo(x, y);
          }
          ctx.lineWidth = width;
          ctx.strokeStyle = color;
          ctx.stroke();
        }
      },
      still(s, t) {
        // Reduced motion: the whole figure, drawn once.
        const { ctx } = s;
        ctx.clearRect(0, 0, s.w, s.h);
        ctx.beginPath();
        for (let i = 0; i <= 900; i += 1) {
          const [x, y] = this.point(s, (i / 900) * Math.PI * 2, t);
          if (i) ctx.lineTo(x, y);
          else ctx.moveTo(x, y);
        }
        ctx.lineWidth = 1.4;
        ctx.strokeStyle = css(s.pal.ok, 0.8);
        ctx.stroke();
      },
    },

    // Conway's Game of Life. Cells cool through the accent as they die, and
    // fresh seeds land when the colony stalls.
    life: {
      fps: 8,
      setup(s) {
        s.cell = 8;
        s.quiet = 0;
      },
      resize(s) {
        s.cols = Math.ceil(s.w / s.cell);
        s.rows = Math.ceil(s.h / s.cell);
        s.grid = new Uint8Array(s.cols * s.rows);
        s.next = new Uint8Array(s.cols * s.rows);
        s.heat = new Float32Array(s.cols * s.rows);
        for (let i = 0; i < s.grid.length; i += 1) s.grid[i] = Math.random() < 0.11 ? 1 : 0;
      },
      seed(s) {
        // An R-pentomino: small, and it runs for a long time.
        const shape = [[1, 0], [2, 0], [0, 1], [1, 1], [1, 2]];
        const ox = 2 + Math.floor(Math.random() * (s.cols - 5));
        const oy = 2 + Math.floor(Math.random() * (s.rows - 5));
        for (const [dx, dy] of shape) s.grid[(oy + dy) * s.cols + ox + dx] = 1;
      },
      step(s) {
        const { cols, rows, grid, next } = s;
        let alive = 0;
        let changed = 0;
        for (let y = 0; y < rows; y += 1) {
          const up = ((y - 1 + rows) % rows) * cols;
          const mid = y * cols;
          const down = ((y + 1) % rows) * cols;
          for (let x = 0; x < cols; x += 1) {
            const l = (x - 1 + cols) % cols;
            const r = (x + 1) % cols;
            const n = grid[up + l] + grid[up + x] + grid[up + r] + grid[mid + l] + grid[mid + r] + grid[down + l] + grid[down + x] + grid[down + r];
            const was = grid[mid + x];
            const now = n === 3 || (was && n === 2) ? 1 : 0;
            next[mid + x] = now;
            alive += now;
            changed += now !== was ? 1 : 0;
          }
        }
        s.grid = next;
        s.next = grid;
        s.quiet = changed < cols * rows * 0.004 ? s.quiet + 1 : 0;
        if (alive < cols * rows * 0.03 || s.quiet > 12) {
          for (let i = 0; i < 4; i += 1) this.seed(s);
          s.quiet = 0;
        }
      },
      draw(s) {
        this.step(s);
        const { ctx, cols, grid, heat, cell } = s;
        const hot = s.pal.brass;
        const cool = s.pal.steel;
        ctx.clearRect(0, 0, s.w, s.h);
        for (let i = 0; i < grid.length; i += 1) {
          heat[i] = grid[i] ? 1 : heat[i] * 0.78;
          const h = heat[i];
          if (h < 0.04) continue;
          const x = (i % cols) * cell;
          const y = Math.floor(i / cols) * cell;
          ctx.fillStyle = css(grid[i] ? hot : cool, grid[i] ? 0.7 : h * 0.5);
          ctx.fillRect(x + 1, y + 1, cell - 2, cell - 2);
        }
      },
    },
  };

  // —— Runner ——
  const run = { kind: "", scene: null, canvas: null, ctx: null, raf: 0, last: 0, acc: 0, t: 0, state: null, listening: false };

  function stop() {
    if (run.raf) cancelAnimationFrame(run.raf);
    run.raf = 0;
    run.last = 0;
  }

  function measure() {
    const s = run.state;
    if (!s || !run.canvas) return false;
    const dpr = Math.min(window.devicePixelRatio || 1, 1.5);
    const w = run.canvas.clientWidth || window.innerWidth;
    const h = run.canvas.clientHeight || window.innerHeight;
    if (w < 2 || h < 2) return false;
    const bw = Math.round(w * dpr);
    const bh = Math.round(h * dpr);
    if (run.canvas.width !== bw || run.canvas.height !== bh || s.dpr !== dpr) {
      run.canvas.width = bw;
      run.canvas.height = bh;
      run.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      Object.assign(s, { w, h, dpr });
      SCENES[run.kind].resize(s);
    }
    return true;
  }

  function frame(now) {
    run.raf = 0;
    const engine = SCENES[run.kind];
    if (!engine || !run.state) return;
    const sec = now / 1000;
    const dt = run.last ? Math.min(0.25, sec - run.last) : 0;
    run.last = sec;
    run.acc += dt;
    const budget = 1 / engine.fps;
    if (run.acc >= budget || !run.t) {
      const step = Math.max(run.acc, budget);
      run.acc = 0;
      run.t += step;
      if (measure()) engine.draw(run.state, run.t, step);
    }
    if (!document.hidden) run.raf = requestAnimationFrame(frame);
  }

  function start() {
    stop();
    const engine = SCENES[run.kind];
    if (!engine || !run.canvas) return;
    run.state = { ctx: run.ctx, pal: palette(), dpr: 0, w: 0, h: 0 };
    engine.setup(run.state);
    // Start mid-flight so the first frame already looks settled.
    run.t = 40 + Math.random() * 60;
    if (!measure()) return;
    if (reduce.matches) {
      (engine.still || engine.draw).call(engine, run.state, run.t, 1 / engine.fps);
      return;
    }
    if (run.kind === "life") for (let i = 0; i < 30; i += 1) engine.step(run.state);
    engine.draw(run.state, run.t, 1 / engine.fps);
    if (!document.hidden) run.raf = requestAnimationFrame(frame);
  }

  function listen() {
    if (run.listening) return;
    run.listening = true;
    document.addEventListener("visibilitychange", () => {
      if (document.hidden) stop();
      else if (SCENES[run.kind] && !reduce.matches && !run.raf) run.raf = requestAnimationFrame(frame);
    });
    let resizeFrame = 0;
    window.addEventListener("resize", () => {
      if (resizeFrame || !run.state) return;
      resizeFrame = requestAnimationFrame(() => {
        resizeFrame = 0;
        measure();
        if (reduce.matches && SCENES[run.kind]) (SCENES[run.kind].still || SCENES[run.kind].draw).call(SCENES[run.kind], run.state, run.t, 0.1);
      });
    });
    reduce.addEventListener?.("change", () => { if (SCENES[run.kind]) start(); });
  }

  // chat-appearance.js calls this whenever the backdrop or theme changes.
  function sync(scene, kind) {
    const canvas = scene && scene.querySelector("canvas[data-chat-scene-fx]");
    const animated = Boolean(canvas && !scene.hidden && SCENES[kind]);
    if (!animated) {
      stop();
      run.kind = kind || "";
      run.state = null;
      if (canvas) canvas.hidden = true;
      return;
    }
    listen();
    canvas.hidden = false;
    run.canvas = canvas;
    run.ctx = canvas.getContext("2d");
    run.scene = scene;
    run.kind = kind;
    start();
  }

  window.FuseBackdrop = { sync, animated: (kind) => Boolean(SCENES[kind]) };
  // chat-appearance.js may have applied a backdrop before this file loaded.
  const scene = document.querySelector("[data-chat-scene]");
  if (scene) sync(scene, html.dataset.chatBackdrop);
})();
