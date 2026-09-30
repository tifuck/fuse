const { test } = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { resolve } = require('node:path');
const vm = require('node:vm');

const source = readFileSync(resolve(__dirname, '../static/chat-appearance.js'), 'utf8');

function boot(saved = {}, blocked = false) {
  const storage = new Map(Object.entries(saved));
  const meta = {};
  const html = { dataset: {}, style: { setProperty() {}, removeProperty() {} }, removeAttribute(name) { delete this.dataset[name.replace(/^data-/, '').replace(/-([a-z])/g, (_, c) => c.toUpperCase())]; } };
  const buttons = ['cli', 'compact'].map(view => ({
    dataset: { view }, setAttribute(name, value) { this[name] = value; },
  }));
  const root = { dataset: { view: saved['fuse.chat.view.v2'] || 'cli' }, querySelectorAll: () => buttons };
  let ready;
  let observe;
  const world = {
    document: {
      documentElement: html,
      querySelector(selector) {
        if (selector === '[data-chat]') return root;
        if (selector === 'meta[name="color-scheme"]' || selector === 'meta[name="theme-color"]') {
          return { setAttribute: (name, value) => { meta[selector] = value; } };
        }
        return null;
      },
      querySelectorAll: (selector) => {
        if (selector === '[data-view-menu] [data-view]') return buttons;
        return [];
      },
      addEventListener: (name, fn) => { ready = fn; },
    },
    localStorage: {
      getItem(key) { if (blocked) throw new Error('Unavailable'); return storage.get(key); },
      setItem(key, value) { if (blocked) throw new Error('Unavailable'); storage.set(key, value); },
    },
    MutationObserver: class { constructor(fn) { observe = fn; } observe() {} },
  };
  world.window = world;
  vm.runInNewContext(source, world);
  return {
    html, root, meta, storage, buttons, ready, appearance: world.FuseChatAppearance,
    change(theme) { world.FuseChatAppearance.setTheme(theme); },
    view(view) { root.dataset.view = view; observe(); },
  };
}

test('restores theme and terminal before DOM ready', () => {
  const ui = boot({ 'fuse.chat.theme.v1': 'paper', 'fuse.chat.view.v2': 'cli' });
  assert.equal(ui.html.dataset.chatTheme, 'paper');
  assert.equal(ui.html.dataset.chatTerminal, 'true');
  assert.equal(ui.meta['meta[name="color-scheme"]'], 'light');
  assert.equal(ui.meta['meta[name="theme-color"]'], '#ffffff');
  ui.ready();
  ui.view('compact');
  assert.equal(ui.html.dataset.chatTheme, 'paper');
  assert.equal(ui.meta['meta[name="color-scheme"]'], 'light');
  assert.equal(ui.buttons[0]['aria-pressed'], 'false');
  assert.equal(ui.buttons[1]['aria-pressed'], 'true');
});

test('composer prefs default on and persist in this browser', () => {
  const ui = boot();
  ui.ready();
  const prefs = ui.appearance.getComposerPrefs();
  assert.equal(prefs.spell, true);
  assert.equal(prefs.palettes, true);
  assert.equal(prefs.history, true);
  ui.appearance.setComposerPrefs({ spell: false });
  assert.equal(JSON.parse(ui.storage.get('fuse.chat.composer.v1')).spell, false);
  assert.equal(ui.appearance.getComposerPrefs().palettes, true);
});

test('theme persistence is independent from reading mode', () => {
  const ui = boot();
  ui.ready();
  ui.change('midnight');
  assert.equal(ui.storage.get('fuse.chat.theme.v1'), 'midnight');
  ui.view('cli');
  assert.equal(ui.meta['meta[name="theme-color"]'], '#0a1018');
  ui.view('compact');
  assert.equal(ui.html.dataset.chatTheme, 'midnight');
  assert.equal(ui.meta['meta[name="theme-color"]'], '#0a1018');
});

test('void and paper palettes apply in terminal', () => {
  const ui = boot({ 'fuse.chat.theme.v1': 'void', 'fuse.chat.view.v2': 'cli' });
  assert.equal(ui.html.dataset.chatTheme, 'void');
  assert.equal(ui.meta['meta[name="color-scheme"]'], 'dark');
  assert.equal(ui.meta['meta[name="theme-color"]'], '#000000');
  ui.ready();
  ui.change('paper');
  assert.equal(ui.html.dataset.chatTheme, 'paper');
  assert.equal(ui.meta['meta[name="color-scheme"]'], 'light');
  assert.equal(ui.meta['meta[name="theme-color"]'], '#ffffff');
});

test('invalid or unavailable storage uses safe defaults', () => {
  const invalid = boot({ 'fuse.chat.theme.v1': 'unknown' });
  assert.equal(invalid.html.dataset.chatTheme, 'fuse');
  const ui = boot({}, true);
  ui.ready();
  assert.equal(ui.html.dataset.chatTheme, 'fuse');
  assert.doesNotThrow(() => ui.change('paper'));
  assert.equal(ui.html.dataset.chatTheme, 'paper');
  ui.change('invalid');
  assert.equal(ui.html.dataset.chatTheme, 'fuse');
});

test('backdrop kind persists independently of theme', () => {
  const ui = boot({ 'fuse.chat.backdrop.v1': 'signal', 'fuse.chat.backdrop.strength.v1': '40' });
  assert.equal(ui.html.dataset.chatBackdrop, 'signal');
  ui.ready();
  ui.appearance.setBackdrop('orbit');
  assert.equal(ui.html.dataset.chatBackdrop, 'orbit');
  assert.equal(ui.storage.get('fuse.chat.backdrop.v1'), 'orbit');
  ui.change('void');
  assert.equal(ui.html.dataset.chatBackdrop, 'orbit');
});

test('a saved view that was renamed opens its successor', () => {
  const ui = boot({ 'fuse.chat.view.v2': 'prism', 'fuse.chat.fonts.v1': JSON.stringify({ prism: 'serif' }) });
  assert.equal(ui.storage.get('fuse.chat.view.v2'), 'mainframe');
  assert.equal(ui.appearance.view, 'mainframe');
});

test('retired backdrops migrate to their replacement', () => {
  for (const [old, next] of [['tide', 'ripple'], ['pulsar', 'signal'], ['topo', 'ripple'], ['ink', 'aurora'], ['wind', 'aurora'], ['fern', 'meadow'], ['sumi', 'koi']]) {
    const ui = boot({ 'fuse.chat.backdrop.v1': old });
    assert.equal(ui.html.dataset.chatBackdrop, next, old);
    assert.equal(ui.storage.get('fuse.chat.backdrop.v1'), next, old);
  }
});

test('backdrop wallpaper is a scene layer, not an opaque plate', () => {
  const css = readFileSync(resolve(__dirname, '../static/chat-design.css'), 'utf8');
  const js = readFileSync(resolve(__dirname, '../static/chat-appearance.js'), 'utf8');
  assert.match(css, /\.chat-scene\[data-kind\] \[data-chat-scene-wash\]/);
  assert.match(css, /html\[data-chat-backdrop\]:not\(\[data-chat-backdrop="none"\]\) body\.is-chat :is\(\.stage, \.stage-inner, main, \.chat, \.chat-stage, \.chat-thread\),[^{]*\{\s*background:\s*transparent/);
  assert.match(css, /html\[data-chat-backdrop\]:not\(\[data-chat-backdrop="none"\]\) body\.is-chat \.chat \.chat-dock \{\s*background:\s*transparent/);
  assert.doesNotMatch(css, /chat-stage, \.chat-thread\) \{\s*background:\s*color-mix\(in srgb, var\(--ink\) 82%/);
  assert.doesNotMatch(css, /chat-scene-bubbles/);
  assert.match(js, /canvas data-chat-scene-fx/);
  assert.match(js, /signal: \{ label: "Signal"/);
  for (const kind of ['orbit', 'galaxy', 'eclipse', 'belt', 'contact', 'koi']) assert.match(js, new RegExp(`\\n    ${kind}: \\{ label`));
  for (const gone of ['pulsar', 'fern', 'ink', 'sumi']) assert.doesNotMatch(js, new RegExp(`\\n    ${gone}: \\{ label`));
  const engine = readFileSync(resolve(__dirname, '../static/chat-backdrop.js'), 'utf8');
  for (const kind of ['glyphs', 'life', 'signal', 'orbit', 'pond', 'koi', 'galaxy', 'eclipse', 'belt', 'contact']) assert.match(engine, new RegExp(`\\n    ${kind}: \\{`));
  for (const gone of ['pulsar', 'fern', 'ink', 'sumi']) assert.doesNotMatch(engine, new RegExp(`\\n    ${gone}: \\{`));
});

test('tool rows collapse to one line; thinking unwraps in place', () => {
  const css = [
    readFileSync(resolve(__dirname, '../static/app.css'), 'utf8'),
    readFileSync(resolve(__dirname, '../static/chat-design.css'), 'utf8'),
  ].join('\n');
  assert.match(css, /\.chat-step-label:not\(\.chat-think-preview\)[\s\S]*?text-overflow:\s*ellipsis/);
  assert.match(css, /\.chat-step-label:not\(\.chat-think-preview\)[\s\S]*?white-space:\s*nowrap/);
  assert.match(css, /\.chat-step\.is-workspace \.chat-step-label \{[\s\S]*?white-space:\s*nowrap/);
  assert.match(css, /\.chat-query-label[\s\S]*?text-overflow:\s*ellipsis/);
  assert.match(css, /\.chat-think-preview[\s\S]*?text-overflow:\s*ellipsis[\s\S]*?white-space:\s*nowrap/);
  assert.match(css, /:not\(\.is-live-think\) \.chat-step-toggle\[aria-expanded="true"\] \.chat-think-preview[\s\S]*?white-space:\s*normal/);
  assert.match(css, /\.is-live-think \.chat-think-preview[\s\S]*?white-space:\s*pre-wrap/);
  assert.match(css, /\.chat-step:not\(\.is-think\) \.chat-step-label/);
  assert.match(css, /\.chat-step:not\(\.is-think\) :is\(\.chat-step-toggle, \.chat-step-static\)/);
  assert.doesNotMatch(css, /\.chat-step\.is-think \.chat-step-toggle\[aria-expanded="true"\] \.chat-think-preview \{ display: none/);
  assert.match(css, /html\[data-chat-theme="void"\]/);
  assert.match(css, /html\[data-chat-theme="paper"\] \{[\s\S]*?--ink:\s*#ffffff/);
  assert.doesNotMatch(css, /Terminal is an appearance invariant/);
  const js = readFileSync(resolve(__dirname, '../static/chat.js'), 'utf8');
  assert.match(js, /function thinkOpenAt\(/);
  assert.match(js, /function thinkStepHtml\([\s\S]*?<\/div><\/li>/);
  assert.doesNotMatch(js, /function thinkStepHtml\([\s\S]*?chat-think-body/);
});
