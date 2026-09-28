// @vitest-environment node
import { describe, expect, it } from 'vitest';
import { checkCss, collectDefinedVars } from './rules.mjs';

const DEFINED = new Set([
  '--mds-label', '--mds-radius-m', '--mds-type-body', '--mds-weight-bold', '--mds-shadow-float',
  '--mds-tracking-title',
]);
const rules = (css, extra = {}) => checkCss(css, {
  file: 'x.css', isTokensFile: false, definedVars: DEFINED, ...extra,
}).map(violation => violation.rule);

describe('checkCss', () => {
  it('accepts token-only declarations', () => {
    expect(rules(`.a {
      color: var(--mds-label); border-radius: var(--mds-radius-m) 0;
      font: var(--mds-type-body); font-weight: var(--mds-weight-bold);
      box-shadow: var(--mds-shadow-float); letter-spacing: var(--mds-tracking-title);
    }`)).toEqual([]);
  });

  it('flags color literals outside the tokens file only', () => {
    expect(rules('.a { color: #fff; background: rgb(0 0 0 / 0.5); border-color: white; }'))
      .toEqual(['color', 'color', 'color']);
    expect(rules(':root { --mds-label: #fff; }', { isTokensFile: true })).toEqual([]);
  });

  it('flags gradients except on masks', () => {
    expect(rules('.a { background: linear-gradient(#000, #fff); }')).toEqual(['color', 'gradient']);
    expect(rules('.a { mask-image: linear-gradient(black, transparent); }')).toEqual(['color']);
  });

  it('flags literal type values and allows inherit', () => {
    expect(rules('.a { font-size: 13px; font-weight: 600; letter-spacing: 0.04em; }'))
      .toEqual(['type', 'type', 'type']);
    expect(rules('.a { font: 14px sans-serif; }')).toEqual(['type']);
    expect(rules('.a { font-size: inherit; font-weight: inherit; letter-spacing: 0; }')).toEqual([]);
  });

  it('flags radius literals and allows 0, 50% and tokens', () => {
    expect(rules('.a { border-radius: 8px; }')).toEqual(['radius']);
    expect(rules('.a { border-radius: 50%; border-top-left-radius: 0; }')).toEqual([]);
  });

  it('flags literal shadows and any backdrop blur', () => {
    expect(rules('.a { box-shadow: 0 1px 2px var(--mds-label); text-shadow: 0 0 1px #f00; }'))
      .toEqual(['shadow', 'color', 'shadow']);
    expect(rules('.a { box-shadow: none; backdrop-filter: blur(4px); }')).toEqual(['blur']);
  });

  it('flags undefined variables and fallbacks on mds tokens', () => {
    expect(rules('.a { font-family: var(--mds-mono); color: var(--mds-label, #fff); }'))
      .toEqual(['undefined-var', 'color', 'var-fallback']);
  });

  it('reports the source line', () => {
    const [violation] = checkCss('.a {\n  color: #000;\n}', {
      file: 'x.css', isTokensFile: false, definedVars: DEFINED,
    });
    expect(violation).toMatchObject({ rule: 'color', line: 2, prop: 'color', value: '#000' });
  });
});

describe('collectDefinedVars', () => {
  it('collects custom property names', () => {
    expect([...collectDefinedVars('.a { --x: 1; } :root { --y: 2; color: red; }')])
      .toEqual(['--x', '--y']);
  });
});
