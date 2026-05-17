import * as esbuild from 'esbuild';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

await esbuild.build({
  entryPoints: ['src/app.tsx'],
  bundle: true,
  platform: 'node',
  target: 'node22',
  format: 'esm',
  outfile: 'dist/wisp-tui.mjs',
  banner: {
    js: `import { createRequire } from 'module'; const require = createRequire(import.meta.url);`,
  },
  alias: {
    'react-devtools-core': path.join(__dirname, 'stubs', 'react-devtools-core.js'),
  },
});

// Ensure single shebang on line 1
let content = fs.readFileSync('dist/wisp-tui.mjs', 'utf-8');
content = content.replace(/^#!\/usr\/bin\/env node\n/gm, '');
content = '#!/usr/bin/env node\n' + content;
fs.writeFileSync('dist/wisp-tui.mjs', content);
fs.chmodSync('dist/wisp-tui.mjs', 0o755);
console.log('Built dist/wisp-tui.mjs (executable)');
