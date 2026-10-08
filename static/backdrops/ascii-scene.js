// Adapter that plays a self-contained ascii.rest scene piece — its compiled
// module (`meta` plus a default frame factory), copied verbatim into the
// scene-*.js files beside this one — as a Fuse backdrop scene. The pieces come
// from https://ascii.rest by @bas3line, MIT licensed.
//
// Rendering follows the piece's own canvas runtime: every (character, colour)
// pair is drawn once into an atlas and stamped into its cell, and a frame
// redraws only the cells that changed since the last one. Scenes draw a fixed
// cols×rows halftone frame, so the stage covers the canvas with it (picker
// cards show the whole picture) instead of using the theme cell grid.

export function asciiScene(meta, make) {
  const { cols, rows, fps, ground, palette = [] } = meta;
  const aspect = meta.cell || 2;
  return {
    fps,
    setup(s) {
      s.frame = make();
      s.color = new Uint8Array(cols * rows);
      s.last = "";
      s.lastColor = new Uint8Array(cols * rows);
      s.full = true;
      s.slots = new Map();
      s.atlas = document.createElement("canvas");
      s.actx = s.atlas.getContext("2d");
    },
    resize(s) {
      // The runner's k is 1; picker previews pass a smaller k, and see the
      // whole picture letterboxed on the piece's ground rather than a crop.
      const span = s.k >= 1 ? Math.max(s.w / cols, s.h / (rows * aspect)) : Math.min(s.w / cols, s.h / (rows * aspect));
      const dpr = s.dpr || 1;
      const w = span * dpr;
      const h = w * aspect;
      const ox = Math.round((s.w * dpr - w * cols) / 2);
      const oy = Math.round((s.h * dpr - h * rows) / 2);
      // Whole-pixel cell edges and glyph origins, so cells tile exactly and a
      // cell's stamp never reaches into its neighbours.
      s.xs = Int32Array.from({ length: cols + 1 }, (_, x) => Math.round(ox + x * w));
      s.ys = Int32Array.from({ length: rows + 1 }, (_, y) => Math.round(oy + y * h));
      s.sw = Math.ceil(w);
      s.sh = Math.ceil(h);
      s.pw = s.sw + 2;
      s.ph = s.sh + 2;
      s.gx = Int32Array.from({ length: cols }, (_, x) => Math.round(ox + x * w + (w - s.sw) / 2));
      s.gy = Int32Array.from({ length: rows }, (_, y) => Math.round(oy + y * h + (h - s.sh) / 2));
      // 32×32 slots hold 1024 glyphs; a piece has a handful of ramp characters
      // times at most 64 palette colours, so the atlas never runs out.
      s.atlas.width = s.pw * 32;
      s.atlas.height = s.ph * 32;
      s.slots.clear();
      s.font = `${w / 0.6}px ui-monospace, SFMono-Regular, Menlo, Consolas, monospace`;
      s.full = true;
    },
    // One atlas slot per (character, colour); drawn on first use.
    glyph(s, code, index) {
      const key = code * 256 + (index & 255);
      let slot = s.slots.get(key);
      if (slot !== undefined) return slot;
      slot = s.slots.size;
      const x = (slot % 32) * s.pw + 1;
      const y = Math.floor(slot / 32) * s.ph + 1;
      const c = s.actx;
      c.font = s.font;
      c.textAlign = "center";
      c.textBaseline = "middle";
      c.fillStyle = palette[index] || palette[0];
      c.fillText(String.fromCharCode(code), x + s.sw / 2, y + s.sh / 2);
      s.slots.set(key, slot);
      return slot;
    },
    draw(s, t) {
      const text = s.frame(t, { color: s.color });
      const ctx = s.ctx;
      const W = Math.round(s.w * (s.dpr || 1));
      const H = Math.round(s.h * (s.dpr || 1));
      // The engine's ctx carries a dpr transform; stamp in raw device pixels.
      ctx.save();
      ctx.setTransform(1, 0, 0, 1, 0, 0);
      if (s.full) {
        ctx.fillStyle = ground;
        ctx.fillRect(0, 0, W, H);
      }
      for (let k = 0, x = 0, y = 0; k < text.length; k += 1) {
        const c = text.charCodeAt(k);
        if (c === 10) {
          x = 0;
          y += 1;
          continue;
        }
        const i = y * cols + x;
        if (s.full || c !== s.last.charCodeAt(k) || s.color[i] !== s.lastColor[i]) {
          const x0 = s.xs[x];
          const y0 = s.ys[y];
          const cw = s.xs[x + 1] - x0;
          const ch = s.ys[y + 1] - y0;
          // Cover mode hangs cells off the canvas; skip what cannot show.
          if (x0 < W && y0 < H && x0 + cw > 0 && y0 + ch > 0) {
            if (!s.full) {
              ctx.fillStyle = ground;
              ctx.fillRect(x0, y0, cw, ch);
            }
            if (c !== 32) {
              const slot = this.glyph(s, c, s.color[i]);
              ctx.drawImage(s.atlas, (slot % 32) * s.pw + 1 + x0 - s.gx[x], Math.floor(slot / 32) * s.ph + 1 + y0 - s.gy[y], cw, ch, x0, y0, cw, ch);
            }
          }
        }
        x += 1;
      }
      s.last = text;
      s.lastColor.set(s.color);
      s.full = false;
      ctx.restore();
    },
  };
}
