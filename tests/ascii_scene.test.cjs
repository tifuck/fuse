const { test } = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { resolve } = require('node:path');
const vm = require('node:vm');

function scene() {
  const glyphs = [];
  const context = { fillText: (...args) => glyphs.push(args) };
  const world = { document: { createElement: () => ({ getContext: () => context }) } };
  const source = readFileSync(resolve(__dirname, '../static/backdrops/ascii-scene.js'), 'utf8');
  vm.runInNewContext(source.replace('export function asciiScene', 'function asciiScene'), world);
  return { engine: world.asciiScene({ cols: 200, rows: 100, cell: 1, fps: 15, palette: ['#ffffff'] }, () => () => ''), glyphs, context };
}

test('large stages use fine dots without changing scene bounds or cropping', () => {
  for (const [w, h, dpr] of [[1440, 900, 1], [3840, 2160, 1.5], [390, 844, 2]]) {
    const { engine, glyphs, context } = scene();
    const s = { w, h, dpr, k: 1 };
    engine.setup(s);
    engine.resize(s);
    const span = Math.max(w / 200, h / 100);
    assert.ok(span / s.subdivisions <= 3);
    assert.ok(s.xs[0] <= 0 && s.xs[200] >= w * dpr);
    assert.ok(s.ys[0] <= 0 && s.ys[100] >= h * dpr);
    engine.glyph(s, 9679, 0);
    assert.equal(glyphs.length, s.subdivisions ** 2);
    assert.ok(parseFloat(context.font) <= 5 * dpr);
    engine.glyph(s, 9679, 0);
    assert.equal(glyphs.length, s.subdivisions ** 2, 'atlas cache avoids repainting');
    engine.resize(s);
    assert.equal(s.slots.size, 0);
    assert.equal(s.full, true);
  }
});

test('small appearance previews retain the original single-glyph rendering', () => {
  const { engine, glyphs } = scene();
  const s = { w: 131, h: 42, dpr: 2, k: 0.5 };
  engine.setup(s);
  engine.resize(s);
  assert.equal(s.subdivisions, 1);
  engine.glyph(s, 9679, 0);
  assert.equal(glyphs.length, 1);
});
