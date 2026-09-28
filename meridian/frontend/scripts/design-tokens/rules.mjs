import postcss from 'postcss';

const COLOR_LITERAL =
  /#[0-9a-fA-F]{3,8}\b|\b(?:rgba?|hsla?|hwb|lab|lch|oklab|oklch)\(|(?<![-\w])(?:white|black)(?![-\w])/;
const GRADIENT = /\b(?:repeating-)?(?:linear|radial|conic)-gradient\(/;
const VAR_REF = /var\(\s*(--[\w-]+)\s*(,)?/g;
const TYPE_TOKEN = /^var\(--mds-type-[a-z0-9-]+\)$/;
const WEIGHT_TOKEN = /^var\(--mds-weight-(?:regular|medium|semibold|bold)\)$/;
const TRACKING_OK = /^(?:0|normal|var\(--mds-tracking-(?:title|display)\))$/;
const RADIUS_PART = '(?:0|50%|var\\(--mds-radius-(?:s|m|l|full)\\))';
const RADIUS_OK = new RegExp(`^${RADIUS_PART}(?:\\s+${RADIUS_PART}){0,3}$`);
const MASK_PROPS = new Set(['mask', 'mask-image', '-webkit-mask', '-webkit-mask-image']);
const SHADOW_OK = new Set(['none', 'var(--mds-shadow-float)']);

function isTypeViolation(prop, value) {
  if (prop === 'font-size') return value !== 'inherit';
  if (prop === 'font') return value !== 'inherit' && !TYPE_TOKEN.test(value);
  if (prop === 'font-weight') return value !== 'inherit' && !WEIGHT_TOKEN.test(value);
  if (prop === 'letter-spacing') return !TRACKING_OK.test(value);
  return false;
}

function isShadowViolation(prop, value) {
  if (prop === 'box-shadow') return !SHADOW_OK.has(value);
  return prop === 'text-shadow' && value !== 'none';
}

function declarationRules(prop, value, { isTokensFile, definedVars }) {
  const found = [];
  if (!isTokensFile && COLOR_LITERAL.test(value)) found.push('color');
  if (!isTokensFile && GRADIENT.test(value) && !MASK_PROPS.has(prop)) found.push('gradient');
  if (isTypeViolation(prop, value)) found.push('type');
  if (/^border(?:-[a-z]+)*-radius$/.test(prop) && !RADIUS_OK.test(value)) found.push('radius');
  if (isShadowViolation(prop, value)) found.push('shadow');
  if (prop === 'backdrop-filter' || prop === '-webkit-backdrop-filter') found.push('blur');
  for (const [, name, fallback] of value.matchAll(VAR_REF)) {
    if (!definedVars.has(name)) found.push('undefined-var');
    if (fallback && name.startsWith('--mds-')) found.push('var-fallback');
  }
  return found;
}

/**
 * Checks one stylesheet against the Meridian token rules.
 *
 * @param {string} css Stylesheet source.
 * @param {{ file: string, isTokensFile: boolean, definedVars: Set<string> }} context
 * @returns {{ rule: string, file: string, line: number, prop: string, value: string }[]}
 */
export function checkCss(css, context) {
  const violations = [];
  postcss.parse(css, { from: context.file }).walkDecls(decl => {
    const prop = decl.prop.startsWith('--') ? decl.prop : decl.prop.toLowerCase();
    const value = decl.value.trim();
    for (const rule of declarationRules(prop, value, context)) {
      violations.push({ rule, file: context.file, line: decl.source?.start?.line ?? 0, prop, value });
    }
  });
  return violations;
}

/**
 * Lists the custom properties a stylesheet defines.
 *
 * @param {string} css Stylesheet source.
 * @returns {Set<string>}
 */
export function collectDefinedVars(css) {
  const names = new Set();
  postcss.parse(css).walkDecls(/^--/, decl => { names.add(decl.prop); });
  return names;
}
