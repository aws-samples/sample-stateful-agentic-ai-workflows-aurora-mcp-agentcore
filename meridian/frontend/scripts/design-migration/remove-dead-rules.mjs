import fs from 'node:fs';
import path from 'node:path';
import { FRONTEND, SRC, migrationCssFiles, transformCss, writeReport } from './lib.mjs';

const probeDir = path.resolve(FRONTEND, process.argv[2] ?? '../.local/visual/baseline');

function sourceFiles(dir) {
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap(entry => {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) return entry.name === '__tests__' ? [] : sourceFiles(full);
    return /\.tsx?$/.test(entry.name) && !/\.test\.tsx?$/.test(entry.name) ? [full] : [];
  });
}

const source = sourceFiles(SRC).map(file => fs.readFileSync(file, 'utf8')).join('\n');
const dynamicPrefixes = [
  ...source.matchAll(/((?:mds|mc)-[\w-]*-)\$\{/g),
  ...source.matchAll(/['"]((?:mds|mc)-[\w-]*-)['"]\s*\+/g),
].map(match => match[1]);
const referenced = cls => new RegExp(`(?<![\\w-])${cls}(?![\\w-])`).test(source)
  || dynamicPrefixes.some(prefix => cls.startsWith(prefix));

const cssClasses = new Set(migrationCssFiles().flatMap(file =>
  [...fs.readFileSync(file, 'utf8').matchAll(/\.((?:mds|mc)-[\w-]+)/g)].map(match => match[1])));
const dead = new Set([...cssClasses].filter(cls => !referenced(cls)));

const probed = new Set(fs.readdirSync(probeDir).filter(file => file.startsWith('classes-'))
  .flatMap(file => JSON.parse(fs.readFileSync(path.join(probeDir, file), 'utf8'))));
if (probed.size === 0) {
  throw new Error(`No classes-*.json in ${probeDir}; run npm run visual first.`);
}
const contradicted = [...dead].filter(cls => probed.has(cls));
if (contradicted.length) {
  throw new Error(`Rendered classes the static scan calls dead: ${contradicted.join(', ')}`);
}

function outsideParens(branch) {
  let text = branch;
  while (/\([^()]*\)/.test(text)) text = text.replace(/\([^()]*\)/g, '');
  return text;
}
const isDeadBranch = branch =>
  [...outsideParens(branch).matchAll(/\.((?:mds|mc)-[\w-]+)/g)].some(match => dead.has(match[1]));

let removed = 0;
let trimmed = 0;
transformCss(migrationCssFiles(), root => {
  root.walkRules(rule => {
    if (rule.parent?.type === 'atrule' && /keyframes$/i.test(rule.parent.name)) return;
    const kept = rule.selectors.filter(branch => !isDeadBranch(branch));
    if (kept.length === 0) {
      rule.remove();
      removed += 1;
    } else if (kept.length < rule.selectors.length) {
      rule.selectors = kept;
      trimmed += 1;
    }
  });
  let emptied = true;
  while (emptied) {
    emptied = false;
    root.walkAtRules(atRule => {
      if (atRule.nodes && atRule.nodes.length === 0) {
        atRule.remove();
        emptied = true;
      }
    });
  }
});

// A keyframes block survives dead-rule deletion above (its own body has no
// class selectors to judge), but the rule that named it in an `animation`
// declaration may have just been deleted. Drop any @keyframes not named by
// a surviving `animation`/`animation-name` declaration in CSS, and not
// named by any TS/TSX source string (an inline style could still name it).
function removeOrphanedKeyframes(files) {
  const cssText = files.map(file => fs.readFileSync(file, 'utf8')).join('\n');
  const animationText = (cssText.match(/animation(?:-name)?\s*:[^;]+;/gi) ?? []).join('\n');
  const orphaned = [];
  transformCss(files, root => {
    root.walkAtRules(/^(?:-\w+-)?keyframes$/i, atRule => {
      const name = atRule.params.trim();
      const named = new RegExp(`(?<![\\w-])${name}(?![\\w-])`);
      if (named.test(animationText) || named.test(source)) return;
      atRule.remove();
      orphaned.push(name);
    });
    let emptied = true;
    while (emptied) {
      emptied = false;
      root.walkAtRules(atRule => {
        if (atRule.nodes && atRule.nodes.length === 0) {
          atRule.remove();
          emptied = true;
        }
      });
    }
  });
  return orphaned;
}

const orphanedKeyframes = removeOrphanedKeyframes(migrationCssFiles());

const summary = `${dead.size} dead classes, ${removed} rules removed, ${trimmed} lists trimmed.`;
const report = writeReport('dead-css', [
  '# Dead CSS', '', summary, '',
  ...[...dead].sort().map(cls => `- ${cls}`),
  '', '# Orphaned keyframes', '',
  ...orphanedKeyframes.sort().map(name => `- ${name}`),
]);
console.log(
  `dead classes ${dead.size}; removed ${removed} rules; trimmed ${trimmed}; `
  + `orphaned keyframes ${orphanedKeyframes.length}; report ${report}`,
);
