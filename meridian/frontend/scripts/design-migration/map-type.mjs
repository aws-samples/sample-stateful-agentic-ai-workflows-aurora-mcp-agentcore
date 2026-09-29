import postcss from 'postcss';
import { migrationCssFiles, transformCss, writeReport } from './lib.mjs';
import {
  STYLE_WEIGHT, effectivePx, roundWeight, styleFor, trackingFor, weightToken,
} from './type.mjs';

const OLD_TYPE_DEFINITIONS = new Set([
  '--mds-fs', '--mds-fs-chrome', '--mc-type-scale', '--mc-heading-weight', '--mc-label-weight',
  '--mc-heading-tracking', '--mds-serif', '--mds-font', '--mds-font-mono',
]);
const HUE_TEXT = /var\(--mds-(?:blue|green|yellow|red|cyan|tint)\)/;
const notes = [];
const note = (file, node, text) => {
  notes.push(`- ${file}:${node.source?.start?.line} \`${node.selector}\` ${text}`);
};

function last(rule, prop) {
  return rule.nodes.filter(node => node.type === 'decl' && node.prop === prop).at(-1);
}

function keepLineHeight(decl) {
  if (!decl) return false;
  const value = Number(decl.value.trim());
  return Number.isFinite(value) && value <= 1.15;
}

function rewriteSized(rule, file, parts) {
  const { size, weight, family, spacing, lineHeight, color, uppercase } = parts;
  const px = effectivePx(size.value.trim(), rule.selector);
  if (px === null) {
    note(file, rule, `REVIEW font-size ${size.value} not mapped`);
    return;
  }
  const rounded = weight ? roundWeight(weight.value.trim()) : null;
  const secondary = /--mds-label-[23]/.test(color?.value ?? '');
  const style = uppercase ? 'footnote' : styleFor(px, { weight: rounded ?? 400, secondary });
  const finalWeight = uppercase ? 600 : (rounded ?? STYLE_WEIGHT[style]);
  const overrides = [];
  if (finalWeight !== STYLE_WEIGHT[style]) {
    overrides.push({ prop: 'font-weight', value: weightToken(finalWeight) });
  }
  if (family && /mono/.test(family.value)) {
    overrides.push({ prop: 'font-family', value: 'var(--mds-font-mono)' });
  }
  if (keepLineHeight(lineHeight)) {
    overrides.push({ prop: 'line-height', value: lineHeight.value.trim() });
  }
  const tracking = trackingFor(style);
  if (tracking) overrides.push({ prop: 'letter-spacing', value: tracking });
  const font = postcss.decl({ prop: 'font', value: `var(--mds-type-${style})` });
  size.replaceWith(font);
  [weight, family, spacing, lineHeight].forEach(decl => decl?.remove());
  overrides.reverse().forEach(override => font.after(postcss.decl(override)));
  if (uppercase && color && !HUE_TEXT.test(color.value)) color.value = 'var(--mds-label-2)';
}

function rewriteUnsized(rule, file, { weight, family, spacing }) {
  if (weight && weight.value.trim() !== 'inherit') {
    const rounded = roundWeight(weight.value.trim());
    if (rounded) weight.value = weightToken(rounded);
    else note(file, rule, `REVIEW font-weight ${weight.value}`);
  }
  if (family) {
    if (/mono/.test(family.value)) family.value = 'var(--mds-font-mono)';
    else if (!/^(?:inherit|var\(--mds-font\))$/.test(family.value.trim())) family.remove();
  }
  if (spacing && !/^(?:0|normal)$/.test(spacing.value.trim())) spacing.remove();
}

function visitRule(rule, file) {
  const shorthand = last(rule, 'font');
  if (shorthand && !/^var\(--mds-type-/.test(shorthand.value)) {
    note(file, rule, `REVIEW font: ${shorthand.value}`);
  }
  const transform = last(rule, 'text-transform');
  const uppercase = Boolean(transform && /uppercase/.test(transform.value));
  if (uppercase) transform.remove();
  rule.walkDecls('font-feature-settings', decl => {
    if (/ss0\d|cv\d/.test(decl.value)) decl.remove();
  });
  const parts = {
    size: last(rule, 'font-size'), weight: last(rule, 'font-weight'),
    family: last(rule, 'font-family'), spacing: last(rule, 'letter-spacing'),
    lineHeight: last(rule, 'line-height'), color: last(rule, 'color'), uppercase,
  };
  if (parts.size && parts.size.value.trim() !== 'inherit') rewriteSized(rule, file, parts);
  else rewriteUnsized(rule, file, parts);
}

transformCss(migrationCssFiles(), (root, file) => {
  root.walkDecls(decl => { if (OLD_TYPE_DEFINITIONS.has(decl.prop)) decl.remove(); });
  root.walkRules(rule => { visitRule(rule, file); });
  root.walkRules(rule => { if (rule.nodes.length === 0) rule.remove(); });
});

const reviewCount = notes.filter(line => line.includes('REVIEW')).length;
const report = writeReport('type', ['# Type', '', `${reviewCount} REVIEW items`, '', ...notes]);
console.log(`${notes.length} notes, ${reviewCount} to review; report ${report}`);
