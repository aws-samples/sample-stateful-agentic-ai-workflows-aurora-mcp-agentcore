import { CONTROL, SHELL } from './lib.mjs';

const WHITE = { r: 255, g: 255, b: 255 };
const BLACK = { r: 0, g: 0, b: 0 };
const TEXT_THRESHOLDS = { dark: { label: 15, label2: 9 }, light: { label: 13, label2: 6.5 } };
const SVG_SHAPE = /(?:^|[^\w-])(?:rect|circle|ellipse|polygon|path|line|marker)(?![\w-])/;

export const COLOR_IN_VALUE = new RegExp('#(?:[0-9a-fA-F]{8}|[0-9a-fA-F]{6}|[0-9a-fA-F]{3,4})\\b'
  + '|rgba?\\(\\s*[\\d.]+%?[\\s,]+[\\d.]+%?[\\s,]+[\\d.]+%?\\s*(?:[,/]\\s*[\\d.]+%?)?\\s*\\)'
  + '|(?<![-\\w])(?:white|black)(?![-\\w])', 'g');

export function parseColor(text) {
  const value = text.trim().toLowerCase();
  if (value === 'white') return { ...WHITE, a: 1 };
  if (value === 'black') return { ...BLACK, a: 1 };
  if (value.startsWith('#')) {
    let hex = value.slice(1);
    if (hex.length <= 4) hex = [...hex].map(ch => ch + ch).join('');
    const channel = index => parseInt(hex.slice(index, index + 2), 16);
    return {
      r: channel(0), g: channel(2), b: channel(4), a: hex.length === 8 ? channel(6) / 255 : 1,
    };
  }
  const parts = value.match(/[\d.]+%?/g) ?? [];
  const number = part => (part.endsWith('%') ? parseFloat(part) * 2.55 : parseFloat(part));
  const [r, g, b] = parts.slice(0, 3).map(number);
  const alpha = parts[3];
  if (alpha === undefined) return { r, g, b, a: 1 };
  const a = alpha.endsWith('%') ? parseFloat(alpha) / 100 : parseFloat(alpha);
  return { r, g, b, a };
}

const linear = channel => {
  const c = channel / 255;
  return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
};
const luminance = ({ r, g, b }) => 0.2126 * linear(r) + 0.7152 * linear(g) + 0.0722 * linear(b);
const contrast = (x, y) => {
  const [hi, lo] = [luminance(x), luminance(y)].sort((p, q) => q - p);
  return (hi + 0.05) / (lo + 0.05);
};
const composite = ({ r, g, b, a }, ground) => ({
  r: a * r + (1 - a) * ground.r, g: a * g + (1 - a) * ground.g, b: a * b + (1 - a) * ground.b,
});

function oklch({ r, g, b }) {
  const [lr, lg, lb] = [r, g, b].map(linear);
  const l = Math.cbrt(0.4122214708 * lr + 0.5363325363 * lg + 0.0514459929 * lb);
  const m = Math.cbrt(0.2119034982 * lr + 0.6806995451 * lg + 0.1073969566 * lb);
  const s = Math.cbrt(0.0883024619 * lr + 0.2817188376 * lg + 0.6299787005 * lb);
  const A = 1.9779984951 * l - 2.428592205 * m + 0.4505937099 * s;
  const B = 0.0259040371 * l + 0.7827717662 * m - 0.808675766 * s;
  return { C: Math.hypot(A, B), H: ((Math.atan2(B, A) * 180) / Math.PI + 360) % 360 };
}

function hueFamily(H) {
  if (H < 55 || H >= 340) return 'red';
  if (H < 115) return 'yellow';
  if (H < 175) return 'green';
  if (H < 240) return 'cyan';
  if (H < 290) return 'blue';
  return 'purple';
}

const ok = token => ({ token, review: false, why: '' });
const review = (token, why) => ({ token, review: true, why });

export function roleOf(prop, subject) {
  if (prop === 'fill' || prop === 'stroke') {
    if (!SVG_SHAPE.test(subject)) return 'text';
    return prop === 'fill' ? 'fill' : 'line';
  }
  if (['color', '-webkit-text-fill-color', 'caret-color', 'text-decoration-color', 'stop-color']
    .includes(prop)) return 'text';
  if (prop.startsWith('background')) return 'fill';
  if (prop.startsWith('outline') || prop === 'accent-color') return 'accent';
  if (prop.startsWith('border') || prop === 'column-rule') return 'line';
  return null;
}

function mapText(ratio, tint, theme) {
  const t = TEXT_THRESHOLDS[theme];
  if (tint && ratio >= 3 && ratio < t.label) return ok(`--mds-${tint}`);
  if (ratio >= t.label) return ok('--mds-label');
  if (ratio >= t.label2) return ok('--mds-label-2');
  if (ratio >= 3) return ok('--mds-label-3');
  return review('--mds-ground', 'text under 3:1 on the page; likely inverse text on a light fill');
}

function mapLine(color, tint, subject) {
  if (tint) {
    return color.a >= 0.4 ? ok(`--mds-${tint}`) : review(`--mds-${tint}`, 'faint tinted border');
  }
  return ok(CONTROL.test(subject) ? '--mds-control-line' : '--mds-separator');
}

function mapNeutralFill(color, solid, theme, subject) {
  const whiteish = color.r + color.g + color.b > 382;
  if (color.a < 1) {
    if (whiteish) return ok(theme === 'light' ? '--mds-surface' : '--mds-surface-2');
    return color.a >= 0.3 ? review('--mds-scrim', 'dark overlay') : ok('--mds-surface-2');
  }
  const y = luminance(solid);
  if (theme === 'light') {
    if (y >= 0.97) return ok(SHELL.test(subject) ? '--mds-ground' : '--mds-surface');
    if (y >= 0.7) return ok('--mds-surface-2');
    return review(y < 0.1 ? '--mds-label' : '--mds-surface-2', 'dark fill in the light theme');
  }
  if (y <= 0.005) {
    return SHELL.test(subject) ? ok('--mds-ground')
      : review('--mds-surface', 'black panel becomes a grouped surface');
  }
  if (y <= 0.06) return ok('--mds-surface-2');
  return review(y > 0.4 ? '--mds-label' : '--mds-surface-2', 'light fill in the dark theme');
}

function mapFill({ color, solid, ratio, tint, theme, subject }) {
  if (!tint) return mapNeutralFill(color, solid, theme, subject);
  const faint = color.a < 0.35 || ratio < (theme === 'light' ? 1.5 : 2);
  if (faint) return ok(`--mds-${tint}-fill`);
  if (tint === 'blue' && CONTROL.test(subject)) {
    return review('--mds-action', 'solid blue control fill; its label must use --mds-on-action');
  }
  return ok(`--mds-${tint}`);
}

/**
 * Chooses the role token for one color literal.
 *
 * @param {{ color: {r:number,g:number,b:number,a:number}, role: string|null,
 *   theme: 'dark'|'light', subject: string }} input
 * @returns {{ token: string, review: boolean, why: string }}
 */
export function mapLiteral({ color, role, theme, subject }) {
  const ground = theme === 'light' ? WHITE : BLACK;
  const solid = composite(color, ground);
  const ratio = contrast(solid, ground);
  const { C, H } = oklch(color);
  const family = C >= 0.045 ? hueFamily(H) : null;
  const tint = family && family !== 'purple' ? family : null;
  if (role === 'accent') return ok('--mds-tint');
  if (role === 'text') return mapText(ratio, tint, theme);
  if (role === 'line') return mapLine(color, tint, subject);
  if (role === 'fill') return mapFill({ color, solid, ratio, tint, theme, subject });
  return review('--mds-label', 'property has no color role');
}
