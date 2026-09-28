import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import postcss from 'postcss';

export const FRONTEND = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
export const SRC = path.join(FRONTEND, 'src');
export const REPORT_DIR = path.resolve(FRONTEND, '../.local/design-migration');
export const TOKENS_FILE = path.join(SRC, 'showcase', 'tokens.css');

export const CONTROL = new RegExp('(?:^|[^\\w-])(?:input|textarea|select|button)(?![\\w-])'
  + '|[.-](?:btn|toggle|switch|checkbox|radio|fields?|composer|search|stepper|segmented|send'
  + '|controls?|picker)(?![\\w])', 'i');
export const FLOAT = /(?:^|[.\s])[\w-]*(?:tooltip|popover|toast|menu|dropdown|flyout)(?![\w-])/i;
export const SHELL = new RegExp('(?:^|[^\\w-])(?:html|body)(?![\\w-])|:root|\\.(?:mds-root'
  + '|mds-desktop-app|mds-desktop-main|mds-desktop-sidebar|mds-desktop-right|mds-desktop-dock'
  + '|mds-shell-header|route-skeleton)(?![\\w-])');
export const LIGHT = /\[data-theme=(['"]?)light\1\]/;
export const DARK = /\[data-theme=(['"]?)dark\1\]/;
export const PROJECTOR = /\[data-projector-readability=(['"]?)true\1\]/;
const SCOPE_ATTRIBUTE = /\[data-(?:theme|projector-readability)=(['"]?)[\w-]+\1\]/g;

export function migrationCssFiles() {
  const walk = dir => fs.readdirSync(dir, { withFileTypes: true }).flatMap(entry => {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) return walk(full);
    return entry.name.endsWith('.css') && full !== TOKENS_FILE ? [full] : [];
  });
  return [
    ...walk(path.join(SRC, 'showcase')),
    path.join(SRC, 'index.css'),
    path.join(SRC, 'preflight.css'),
  ];
}

export function transformCss(files, visit) {
  let changed = 0;
  for (const file of files) {
    const before = fs.readFileSync(file, 'utf8');
    const root = postcss.parse(before, { from: file });
    visit(root, path.relative(FRONTEND, file));
    const after = root.toString();
    if (after !== before) {
      fs.writeFileSync(file, after);
      changed += 1;
    }
  }
  return changed;
}

export function writeReport(name, lines) {
  fs.mkdirSync(REPORT_DIR, { recursive: true });
  const file = path.join(REPORT_DIR, `${name}.md`);
  fs.writeFileSync(file, `${lines.join('\n')}\n`);
  return file;
}

export function subjectOf(branch) {
  const parts = [];
  let depth = 0;
  let current = '';
  for (const ch of branch.trim()) {
    if (ch === '(' || ch === '[') depth += 1;
    if (ch === ')' || ch === ']') depth -= 1;
    if (depth === 0 && /[\s>+~]/.test(ch)) {
      if (current) parts.push(current);
      current = '';
    } else {
      current += ch;
    }
  }
  if (current) parts.push(current);
  return parts.at(-1) ?? '';
}

export function baseSelector(selector) {
  return postcss.list.comma(selector)
    .map(branch => branch
      .replace(SCOPE_ATTRIBUTE, '')
      .replace(/^\.mds-root\s+/, '')
      .replace(/\s+/g, ' ')
      .trim())
    .sort()
    .join(', ');
}

export function unscopeSelector(selector) {
  return postcss.list.comma(selector)
    .map(branch => branch.replace(SCOPE_ATTRIBUTE, '').trim())
    .join(', ');
}
