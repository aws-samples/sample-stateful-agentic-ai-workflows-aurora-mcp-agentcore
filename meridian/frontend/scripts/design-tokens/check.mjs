import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { EXEMPTIONS } from './exemptions.mjs';
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
  [...scanned, ...cssFiles(path.join(SRC, 'stage'))].flatMap(
    file => [...collectDefinedVars(read(file))],
  ),
);
const all = scanned.flatMap(file => checkCss(read(file), {
  file: rel(file), isTokensFile: file === TOKENS_FILE, definedVars,
}));

if (process.argv.includes('--seed')) {
  const seed = {};
  for (const violation of all) (seed[violation.rule] ??= new Set()).add(violation.file);
  const lists = Object.entries(seed).map(([rule, files]) =>
    `  '${rule}': [\n${[...files].sort().map(file => `    '${file}',`).join('\n')}\n  ],`);
  console.log(`export const EXEMPTIONS = {\n${lists.join('\n')}\n};`);
  process.exit(0);
}

const exempt = violation => (EXEMPTIONS[violation.rule] ?? []).includes(violation.file);
const violations = all.filter(violation => !exempt(violation));
const stale = Object.entries(EXEMPTIONS).flatMap(([rule, files]) => files
  .filter(file => !all.some(violation => violation.rule === rule && violation.file === file))
  .map(file => `${rule}: ${file}`));

for (const v of violations) console.error(`${v.file}:${v.line} [${v.rule}] ${v.prop}: ${v.value}`);
for (const entry of stale) {
  console.error(`stale exemption, delete it from exemptions.mjs: ${entry}`);
}
if (violations.length || stale.length) {
  console.error(`design tokens: ${violations.length} violations, ${stale.length} stale exemptions`);
  process.exit(1);
}
console.log(`design tokens: ${scanned.length} files pass`);
