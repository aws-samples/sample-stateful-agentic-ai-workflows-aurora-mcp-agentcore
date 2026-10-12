import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { checkCss, collectDefinedVars } from './rules.mjs';

const FRONTEND = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const SRC = path.join(FRONTEND, 'src');
const TOKENS_FILE = path.join(SRC, 'showcase', 'tokens.css');

function cssFiles(dir) {
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap(entry => {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) return cssFiles(full);
    return entry.name.endsWith('.css') ? [full] : [];
  });
}

const read = file => fs.readFileSync(file, 'utf8');
const rel = file => path.relative(FRONTEND, file);
const scanned = [
  ...cssFiles(path.join(SRC, 'showcase')),
  path.join(SRC, 'index.css'),
  path.join(SRC, 'preflight.css'),
];
const definedVars = new Set(
  scanned.flatMap(file => [...collectDefinedVars(read(file))]),
);
const all = scanned.flatMap(file => checkCss(read(file), {
  file: rel(file), isTokensFile: file === TOKENS_FILE, definedVars,
}));

for (const v of all) console.error(`${v.file}:${v.line} [${v.rule}] ${v.prop}: ${v.value}`);
if (all.length) {
  console.error(`design tokens: ${all.length} violations`);
  process.exit(1);
}
console.log(`design tokens: ${scanned.length} files pass`);
