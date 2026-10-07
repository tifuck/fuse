const fs = require('node:fs');
const path = require('node:path');
const root = path.join(__dirname, '../static');
const theme = path.join(root, 'themes/console');
module.exports = fs.readFileSync(path.join(root, 'chat.js'), 'utf8') + '\n' +
  fs.readdirSync(theme).filter(name => name.endsWith('.js') && name !== 'index.js')
    .sort().map(name => fs.readFileSync(path.join(theme, name), 'utf8')
      .replace(/^(function|const) /gm, '  $1 ')).join('\n');
