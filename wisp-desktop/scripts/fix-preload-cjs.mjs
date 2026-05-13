import * as fs from 'node:fs';
import * as path from 'node:path';

const mjsPath = 'out/preload/index.mjs';
const jsPath = 'out/preload/index.js';

if (!fs.existsSync(mjsPath)) {
  console.log('No .mjs preload to fix');
  process.exit(0);
}

let content = fs.readFileSync(mjsPath, 'utf-8');

// Convert ESM import to CJS require
content = content.replace(
  /import\s*\{\s*([^}]+)\s*\}\s*from\s*["']electron["'];?/,
  "const { $1 } = require('electron');"
);

// Handle default imports or other patterns if needed
content = content.replace(
  /import\s+(\w+)\s+from\s*["']([^"']+)["'];?/g,
  "const $1 = require('$2');"
);

fs.writeFileSync(jsPath, content, 'utf-8');
fs.unlinkSync(mjsPath);
console.log('Converted preload to CJS:', jsPath);
