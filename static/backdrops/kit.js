// Drawing kits: the glyph atlas, cell grids, pixel buffers and blitting.
// Verbatim extraction from static/chat-backdrop.js; chunk map: backdrop-manifest.json in the snapshot parent.

// —— Drawing kits ——
// Glyph atlas: every char in every tone, pre-rendered, so a frame is only
// drawImage calls.
export function atlas(s, chars, colors, cw, ch, size) {
  const list = Array.from(chars);
  const dpr = s.dpr;
  const a = document.createElement("canvas");
  a.width = Math.ceil(list.length * cw * dpr);
  a.height = Math.ceil(colors.length * ch * dpr);
  const c = a.getContext("2d");
  c.scale(dpr, dpr);
  c.font = `${size}px "Red Hat Mono", ui-monospace, "Cascadia Mono", Menlo, monospace`;
  c.textAlign = "center";
  c.textBaseline = "middle";
  colors.forEach((color, row) => {
    c.fillStyle = color;
    list.forEach((g, i) => { if (g !== " ") c.fillText(g, i * cw + cw / 2, row * ch + ch / 2); });
  });
  return { canvas: a, cw, ch, dpr, n: list.length, index: new Map(list.map((g, i) => [g, i])) };
}
export function stamp(ctx, A, ci, ti, x, y) {
  const d = A.dpr;
  ctx.drawImage(A.canvas, ci * A.cw * d, ti * A.ch * d, A.cw * d, A.ch * d, x, y, A.cw, A.ch);
}

// Cell grid: one pixel per cell in an offscreen image, blown up with hard
// edges, then a one-pixel gutter cut between cells so it reads as a panel of
// lamps rather than a blurry bitmap.
export function cells(s, cell) {
  s.cell = cell;
  s.cols = Math.max(1, Math.ceil(s.w / cell));
  s.rows = Math.max(1, Math.ceil(s.h / cell));
  s.off = document.createElement("canvas");
  s.off.width = s.cols;
  s.off.height = s.rows;
  s.offCtx = s.off.getContext("2d");
  s.img = s.offCtx.createImageData(s.cols, s.rows);
  s.gutter = null;
  if (cell >= 4) {
    const g = document.createElement("canvas");
    const px = Math.max(1, Math.round(cell * s.dpr));
    g.width = px;
    g.height = px;
    const gc = g.getContext("2d");
    const gap = Math.max(1, Math.round(px * 0.18));
    gc.fillStyle = "#000";
    gc.fillRect(px - gap, 0, gap, px);
    gc.fillRect(0, px - gap, px, gap);
    s.gutter = s.ctx.createPattern(g, "repeat");
    s.gutter?.setTransform?.(new DOMMatrix().scale(1 / s.dpr));
  }
}
export function blit(s, gutter = true, alpha = 1) {
  const { ctx } = s;
  s.offCtx.putImageData(s.img, 0, 0);
  ctx.clearRect(0, 0, s.w, s.h);
  ctx.imageSmoothingEnabled = false;
  ctx.globalAlpha = alpha;
  ctx.drawImage(s.off, 0, 0, s.cols * s.cell, s.rows * s.cell);
  ctx.globalAlpha = 1;
  if (gutter && s.gutter) {
    ctx.globalCompositeOperation = "destination-out";
    ctx.fillStyle = s.gutter;
    ctx.fillRect(0, 0, s.w, s.h);
    ctx.globalCompositeOperation = "source-over";
  }
}
export function put(px, i, c, a) {
  const o = i * 4;
  px[o] = c[0]; px[o + 1] = c[1]; px[o + 2] = c[2]; px[o + 3] = a * 255;
}
