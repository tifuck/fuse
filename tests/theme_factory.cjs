const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const dir = path.join(__dirname, '../static/themes/console');
const factories = fs.readdirSync(dir).filter(name => name.endsWith('.js') && name !== 'index.js').map(name => {
  const source = fs.readFileSync(path.join(dir, name), 'utf8');
  return source.replace('export function create(', 'function create(') + '\ncreate';
});
const cache = new WeakMap();
module.exports = function themeFor(context) {
  if (cache.has(context)) return cache.get(context);
  const theme = {};
  const deps = new Proxy({}, { get: (_, key) => context[key] ?? theme[key], set: (_, key, value) => { context[key] = value; return true; } });
  for (const source of factories) Object.assign(theme, vm.runInContext(source, context)(deps));
  cache.set(context, theme);
  return theme;
};
