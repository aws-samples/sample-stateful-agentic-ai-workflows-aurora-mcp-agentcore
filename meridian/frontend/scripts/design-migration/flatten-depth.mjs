import {
  CONTROL, FLOAT, migrationCssFiles, subjectOf, transformCss, writeReport,
} from './lib.mjs';

const GRADIENT = /\b(?:repeating-)?(?:linear|radial|conic)-gradient\(/;
const MASK_PROPS = new Set(['mask', 'mask-image', '-webkit-mask', '-webkit-mask-image']);
const IMAGE_SUBJECT = /(?<![a-z])(?:photo|visual|image|img|hero|thumb|media|cover)(?![a-z])/i;
const STATE = new RegExp('\\.is-(?:selected|active|featured|current|open|checked)'
  + '|\\[aria-(?:selected|current|pressed)|:checked');
const NESTED_ARGS = '\\((?:[^()]|\\([^()]*\\))*\\)';
const STOP = new RegExp('#(?:[0-9a-fA-F]{8}|[0-9a-fA-F]{6}|[0-9a-fA-F]{3,4})\\b'
  + `|(?:rgba?|color-mix)${NESTED_ARGS}|var\\(--[\\w-]+\\)|\\btransparent\\b`);
const NO_BORDER = /^(?:0|0px|none)$/;
const FADES_OUT = /\btransparent\b|,\s*0\)|\/\s*0\)/;
const notes = [];

function note(file, decl, text) {
  const where = decl.parent.selector ?? `@${decl.parent.name} ${decl.parent.params}`;
  notes.push(`- ${file}:${decl.source?.start?.line} \`${where}\` ${decl.prop}: ${text}`);
}

function splitTopLevel(value) {
  const layers = [];
  let depth = 0;
  let current = '';
  for (const ch of value) {
    if (ch === '(') depth += 1;
    if (ch === ')') depth -= 1;
    if (ch === ',' && depth === 0) {
      layers.push(current.trim());
      current = '';
    } else {
      current += ch;
    }
  }
  return [...layers, current.trim()];
}

function flattenShadow(decl, rule, subjects, file) {
  if (decl.prop.startsWith('--')) return decl.remove();
  if (decl.value.trim() === 'none') return undefined;
  if (/:focus/.test(rule.selector ?? '')) {
    if (!rule.some(node => node.type === 'decl' && node.prop === 'outline')) {
      decl.cloneBefore({ prop: 'outline', value: '2px solid var(--mds-blue)' });
      decl.cloneBefore({ prop: 'outline-offset', value: '2px' });
    }
    note(file, decl, 'focus shadow became an outline');
    return decl.remove();
  }
  if (subjects.some(subject => FLOAT.test(subject))) {
    decl.value = 'var(--mds-shadow-float)';
    return undefined;
  }
  if (subjects.some(subject => CONTROL.test(subject))) {
    note(file, decl, `REVIEW control shadow kept: ${decl.value}`);
    return undefined;
  }
  return decl.remove();
}

function flattenGradient(decl, subjects, file) {
  const layers = splitTopLevel(decl.value);
  if (layers.some(layer => /url\(/.test(layer))) {
    note(file, decl, 'REVIEW layered image background left untouched');
    return;
  }
  if (subjects.some(subject => IMAGE_SUBJECT.test(subject)) && FADES_OUT.test(decl.value)) {
    decl.value = 'var(--mds-image-scrim)';
    note(file, decl, 'image scrim');
    return;
  }
  if (decl.prop === 'border-image') {
    decl.remove();
    return;
  }
  const base = layers.at(-1);
  const source = GRADIENT.test(base) ? base.slice(base.search(GRADIENT)) : base;
  const stop = source.match(STOP)?.[0] ?? 'transparent';
  if (decl.prop === 'background-image') decl.prop = 'background-color';
  decl.value = stop;
  if (/background-clip/.test(decl.parent.toString())) note(file, decl, 'REVIEW gradient text');
}

function dropBorder(decl, rule, file) {
  if (STATE.test(rule.selector)) {
    note(file, decl, 'REVIEW selection border kept');
    return;
  }
  const zeroWidth = node => node.type === 'decl' && /^border(?:-width)?$/.test(node.prop)
    && NO_BORDER.test(node.value.trim());
  if (rule.some(zeroWidth)) return;
  if (decl.prop === 'border') {
    decl.value = '0';
    note(file, decl, 'decorative border removed');
  } else {
    decl.remove();
  }
  rule.walkDecls(/^border-color$/, sibling => { sibling.remove(); });
}

function visitDecl(decl, file) {
  const rule = decl.parent;
  const subjects = rule.type === 'rule' ? rule.selectors.map(subjectOf) : [];
  const prop = decl.prop.toLowerCase();
  const isBlur = prop === 'backdrop-filter' || prop === '-webkit-backdrop-filter';
  if (isBlur || prop === 'text-shadow') {
    decl.remove();
  } else if (prop === 'filter' && /drop-shadow\(/.test(decl.value)) {
    note(file, decl, 'drop-shadow removed');
    decl.remove();
  } else if (prop === 'box-shadow' || (prop.startsWith('--') && /shadow/.test(prop))) {
    flattenShadow(decl, rule, subjects, file);
  } else if (GRADIENT.test(decl.value) && !MASK_PROPS.has(prop)) {
    flattenGradient(decl, subjects, file);
  } else if (/^border(?:-width|-style)?$/.test(prop) && rule.type === 'rule'
    && !subjects.some(subject => CONTROL.test(subject) || FLOAT.test(subject))) {
    dropBorder(decl, rule, file);
  }
}

transformCss(migrationCssFiles(), (root, file) => {
  root.walkAtRules('supports', atRule => {
    if (/backdrop-filter/.test(atRule.params)) atRule.remove();
  });
  root.walkDecls(decl => { visitDecl(decl, file); });
  root.walkRules(rule => { if (rule.nodes.length === 0) rule.remove(); });
  root.walkAtRules(atRule => { if (atRule.nodes?.length === 0) atRule.remove(); });
});

const review = notes.filter(line => line.includes('REVIEW'));
const report = writeReport('depth', ['# Depth', '', `${review.length} REVIEW items`, '', ...notes]);
console.log(`${notes.length} notes, ${review.length} to review; report ${report}`);
