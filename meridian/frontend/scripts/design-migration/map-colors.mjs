import postcss from 'postcss';
import { COLOR_IN_VALUE, mapLiteral, parseColor, roleOf } from './color.mjs';
import {
  CONTROL, DARK, LIGHT, PROJECTOR, baseSelector, migrationCssFiles, subjectOf, transformCss,
  unscopeSelector, writeReport,
} from './lib.mjs';
import {
  CHANNEL_HUE, DEFINITION_RENAME, DELETE_DEFINITIONS, LINE_VARS, RENAME,
} from './variable-map.mjs';

const CHANNEL = /rgba?\(\s*var\((--[\w-]+)\)\s*(?:\/\s*([\d.]+%?)\s*)?\)/g;
const notes = [];
const note = (file, decl, text) => notes.push(`- ${file}:${decl.source?.start?.line} `
  + `\`${decl.parent.selector ?? decl.parent.params}\` ${decl.prop}: ${text}`);

const themeOf = rule => (LIGHT.test(rule.selector ?? '') ? 'light' : 'dark');
const scoped = rule => [LIGHT, DARK, PROJECTOR].some(pattern => pattern.test(rule.selector ?? ''));
const contextOf = node => {
  const chain = [];
  for (let parent = node.parent; parent && parent.type !== 'root'; parent = parent.parent) {
    if (parent.type === 'atrule') chain.push(`@${parent.name} ${parent.params}`);
  }
  return chain.join(' | ');
};
const keyOf = (rule, prop) => `${contextOf(rule)}|${baseSelector(rule.selector)}|${prop}`;
const subjectsOf = decl => (
  decl.parent.type === 'rule' ? decl.parent.selectors.map(subjectOf) : ['']);

function renameVars(decl, subjects) {
  const control = subjects.some(subject => CONTROL.test(subject));
  const role = roleOf(decl.prop, subjects[0]);
  decl.value = decl.value
    .replace(/var\((--[\w-]+)\)/g, (match, name) => {
      if (RENAME[name]) return `var(${RENAME[name]})`;
      if (DEFINITION_RENAME[name]) return `var(${DEFINITION_RENAME[name]})`;
      if (LINE_VARS.has(name)) return control ? 'var(--mds-control-line)' : 'var(--mds-separator)';
      return match;
    })
    .replace(CHANNEL, (match, name, alpha) => {
      const hue = CHANNEL_HUE[name];
      if (!hue) return match;
      const a = alpha === undefined ? 1
        : alpha.endsWith('%') ? parseFloat(alpha) / 100 : parseFloat(alpha);
      return role === 'fill' && a < 0.35 ? `var(--mds-${hue}-fill)` : `var(--mds-${hue})`;
    });
  if (role === 'accent') decl.value = decl.value.replace('var(--mds-blue)', 'var(--mds-tint)');
}

function mapLiterals(decl, subjects, theme, file) {
  const role = roleOf(decl.prop, subjects[0]);
  decl.value = decl.value.replace(COLOR_IN_VALUE, literal => {
    const result = mapLiteral({ color: parseColor(literal), role, theme, subject: subjects[0] });
    if (result.review) note(file, decl, `REVIEW ${literal} -> ${result.token}: ${result.why}`);
    return `var(${result.token})`;
  });
}

function collapseScoped(root, file) {
  const base = new Map();
  root.walkRules(rule => {
    if (scoped(rule)) return;
    rule.walkDecls(decl => { base.set(keyOf(rule, decl.prop), decl.value); });
  });
  root.walkRules(rule => {
    if (!scoped(rule)) return;
    const moved = [];
    rule.each(decl => {
      if (decl.type !== 'decl' || decl.prop.startsWith('--') || !roleOf(decl.prop, '')) return;
      if (!/var\(--mds-/.test(decl.value)) return;
      const baseValue = base.get(keyOf(rule, decl.prop));
      if (PROJECTOR.test(rule.selector)) note(file, decl, 'projector color override removed');
      else if (baseValue !== undefined && baseValue !== decl.value) {
        note(file, decl, `REVIEW theme disagreement, kept base ${baseValue} over ${decl.value}`);
      } else if (baseValue === undefined) {
        moved.push(decl.clone());
        note(file, decl, 'REVIEW theme-only color now applies to both themes');
      }
      decl.remove();
    });
    if (moved.length) {
      rule.after(postcss.rule({ selector: unscopeSelector(rule.selector), nodes: moved }));
    }
    if (rule.nodes.length === 0) rule.remove();
  });
}

transformCss(migrationCssFiles(), (root, file) => {
  root.walkDecls(decl => {
    if (decl.prop.startsWith('--') && DELETE_DEFINITIONS.has(decl.prop)) return decl.remove();
    if (DEFINITION_RENAME[decl.prop]) decl.prop = DEFINITION_RENAME[decl.prop];
    const subjects = subjectsOf(decl);
    renameVars(decl, subjects);
    mapLiterals(decl, subjects, decl.parent.type === 'rule' ? themeOf(decl.parent) : 'dark', file);
    return undefined;
  });
  collapseScoped(root, file);
  root.walkRules(rule => { if (rule.nodes.length === 0) rule.remove(); });
});

const reviewCount = notes.filter(line => line.includes('REVIEW')).length;
const report = writeReport('colors', ['# Colors', '', `${reviewCount} REVIEW items`, '', ...notes]);
console.log(`${notes.length} notes, ${reviewCount} to review; report ${report}`);
