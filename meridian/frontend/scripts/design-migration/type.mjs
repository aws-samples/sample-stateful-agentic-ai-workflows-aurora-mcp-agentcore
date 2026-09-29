export const STYLE_WEIGHT = {
  caption: 400, footnote: 400, body: 400, headline: 600,
  'title-3': 600, 'title-2': 700, 'title-1': 700, 'large-title': 700,
};
const WEIGHT_NAME = { 400: 'regular', 500: 'medium', 600: 'semibold', 700: 'bold' };
const KNOWN_WEIGHT_VARS = { 'var(--mc-heading-weight)': 520, 'var(--mc-label-weight)': 550 };

function scaleFor(variable, selector) {
  const discovery = /is-discovery/.test(selector);
  if (variable === '--mds-fs') return discovery ? 1 : 1.16;
  if (variable === '--mds-fs-chrome') return discovery ? 1 : 1.08;
  if (variable === '--mc-type-scale') return 1.18;
  return 1;
}

export function effectivePx(value, selector) {
  const scaled = value.match(/^calc\(\s*([\d.]+)px\s*\*\s*var\((--[\w-]+)\)\s*\)$/);
  if (scaled) return parseFloat(scaled[1]) * scaleFor(scaled[2], selector);
  const plain = value.match(/^([\d.]+)px$/);
  if (plain) return parseFloat(plain[1]);
  const clamp = value.match(/^clamp\([^,()]+,[^,()]+,\s*([\d.]+)px\s*\)$/);
  if (clamp) return parseFloat(clamp[1]);
  const rem = value.match(/^([\d.]+)rem$/);
  return rem ? parseFloat(rem[1]) * 16 : null;
}

export function roundWeight(value) {
  const known = KNOWN_WEIGHT_VARS[value];
  const weight = known ?? (value === 'bold' ? 700 : value === 'normal' ? 400 : Number(value));
  if (!Number.isFinite(weight)) return null;
  if (weight <= 450) return 400;
  if (weight < 580) return 500;
  if (weight < 680) return 600;
  return 700;
}

export function styleFor(px, { weight, secondary = false }) {
  if (px < 12.5) return 'caption';
  if (px < 14) return 'footnote';
  if (px < 14.5 && secondary) return 'footnote';
  if (px < 16.5) return weight >= 600 ? 'headline' : 'body';
  if (px < 20.5) return 'title-3';
  if (px < 25.5) return 'title-2';
  if (px < 32.5) return 'title-1';
  return 'large-title';
}

export function trackingFor(style) {
  if (style === 'title-3' || style === 'title-2') return 'var(--mds-tracking-title)';
  if (style === 'title-1' || style === 'large-title') return 'var(--mds-tracking-display)';
  return null;
}

export const weightToken = weight => `var(--mds-weight-${WEIGHT_NAME[weight]})`;
