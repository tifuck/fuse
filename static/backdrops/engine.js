// Engine: scene state, the frame runner, picker previews and sync.
// Verbatim extraction from static/chat-backdrop.js; chunk map: backdrop-manifest.json in the snapshot parent.

import { html, reduce } from "./env.js";
import { palette } from "./color.js";
import { SCENES } from "./scenes.js";

function makeState(ctx, k) {
  return { ctx, pal: palette(), dpr: 0, w: 0, h: 0, k };
}

// —— Runner ——
const run = { kind: "", canvas: null, ctx: null, raf: 0, last: 0, acc: 0, t: 0, state: null, listening: false };

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
  run.state = makeState(run.ctx, 1);
  engine.setup?.(run.state);
  // Start mid-flight so the first frame already looks settled.
  run.t = 40 + Math.random() * 60;
  if (!measure()) return;
  // Reduced motion holds one frame, so it should be a finished one.
  engine.warm?.(run.state, run.t, reduce.matches);
  engine.draw(run.state, run.t, 1 / engine.fps);
  if (reduce.matches) return;
  if (!document.hidden) run.raf = requestAnimationFrame(frame);
}

function listen() {
  if (run.listening) return;
  run.listening = true;
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) stop();
    else if (SCENES[run.kind] && run.state && !reduce.matches && !run.raf) run.raf = requestAnimationFrame(frame);
  });
  let resizeFrame = 0;
  window.addEventListener("resize", () => {
    if (resizeFrame || !run.state) return;
    resizeFrame = requestAnimationFrame(() => {
      resizeFrame = 0;
      if (measure() && reduce.matches && SCENES[run.kind]) SCENES[run.kind].draw(run.state, run.t, 0.1);
    });
  });
  reduce.addEventListener?.("change", () => { if (SCENES[run.kind] && run.state) start(); });
}

// —— Picker previews ——
// Each card shows a real, settled frame of its scene; the hovered card runs.
const previews = new Map();
let observer = null;
const hover = { el: null, raf: 0, last: 0, acc: 0 };

function paintPreview(el) {
  const kind = el.dataset.kind;
  const engine = SCENES[kind];
  if (!engine) return;
  const w = el.clientWidth;
  const h = el.clientHeight;
  if (w < 8 || h < 4) return;
  let canvas = el.querySelector("canvas");
  if (!canvas) {
    canvas = document.createElement("canvas");
    el.append(canvas);
  }
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  canvas.width = Math.round(w * dpr);
  canvas.height = Math.round(h * dpr);
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  const s = makeState(ctx, engine.previewK || 0.5);
  Object.assign(s, { w, h, dpr });
  engine.setup?.(s);
  engine.resize(s);
  const t = 50;
  engine.warm?.(s, t, true);
  engine.draw(s, t, 1 / engine.fps);
  previews.set(el, { s, t, engine });
}

function hoverFrame(now) {
  hover.raf = 0;
  const entry = hover.el && previews.get(hover.el);
  if (!entry) return;
  const sec = now / 1000;
  const dt = hover.last ? Math.min(0.25, sec - hover.last) : 0;
  hover.last = sec;
  hover.acc += dt;
  const budget = 1 / entry.engine.fps;
  if (hover.acc >= budget) {
    const step = Math.max(hover.acc, budget);
    hover.acc = 0;
    entry.t += step;
    entry.engine.draw(entry.s, entry.t, step);
  }
  hover.raf = requestAnimationFrame(hoverFrame);
}

function hoverPreview(el) {
  if (hover.el === el) return;
  if (hover.raf) cancelAnimationFrame(hover.raf);
  hover.raf = 0;
  hover.el = el;
  hover.last = 0;
  hover.acc = 0;
  if (el && previews.has(el) && !reduce.matches) hover.raf = requestAnimationFrame(hoverFrame);
}

export function scanPreviews(root = document) {
  const els = root.querySelectorAll?.(".chat-backdrop-preview[data-kind]") || [];
  if (!observer && typeof ResizeObserver === "function") {
    observer = new ResizeObserver((entries) => {
      for (const entry of entries) {
        const el = entry.target;
        const box = entry.contentRect;
        const prev = previews.get(el);
        if (box.width > 8 && (!prev || Math.abs(prev.s.w - el.clientWidth) > 1 || Math.abs(prev.s.h - el.clientHeight) > 1)) paintPreview(el);
      }
    });
    document.addEventListener("pointerover", (event) => {
      const card = event.target.closest?.(".ap-backdrop");
      hoverPreview(card ? card.querySelector(".chat-backdrop-preview[data-kind]") : null);
    });
  }
  for (const el of els) {
    if (!SCENES[el.dataset.kind] || el.dataset.fxObserved) continue;
    el.dataset.fxObserved = "1";
    if (observer) observer.observe(el);
    else paintPreview(el);
  }
}

// Only a theme change alters a preview; Intensity and the choice itself
// leave them as they are.
let previewInk = "";
function repaintPreviews() {
  const style = getComputedStyle(html);
  const ink = ["--ink", "--mist", "--mute", "--brass", "--steel"].map((p) => style.getPropertyValue(p).trim()).join();
  if (ink === previewInk) return;
  previewInk = ink;
  for (const el of previews.keys()) {
    if (!el.isConnected) previews.delete(el);
    else paintPreview(el);
  }
}

// chat-appearance.js calls this whenever the backdrop or theme changes.
export function sync(scene, kind) {
  repaintPreviews();
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
  run.kind = kind;
  start();
}
