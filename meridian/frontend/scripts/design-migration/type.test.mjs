// @vitest-environment node
import { describe, expect, it } from 'vitest';
import { effectivePx, roundWeight, styleFor, trackingFor, weightToken } from './type.mjs';

describe('effectivePx', () => {
  it('applies the multiplier each variable had on the laptop', () => {
    expect(effectivePx('calc(12px * var(--mds-fs))', '.mds-card')).toBeCloseTo(13.92);
    expect(effectivePx('calc(12px * var(--mds-fs))', '.mds-desktop-app.is-discovery .x')).toBe(12);
    expect(effectivePx('calc(10px * var(--mds-fs-chrome))', '.x')).toBeCloseTo(10.8);
    expect(effectivePx('calc(13px * var(--mc-type-scale))', '.x')).toBeCloseTo(15.34);
    expect(effectivePx('15px', '.x')).toBe(15);
    expect(effectivePx('clamp(20px, 3vw, 28px)', '.x')).toBe(28);
    expect(effectivePx('0.9em', '.x')).toBe(null);
  });
});

describe('roundWeight', () => {
  it('rounds to the four weights', () => {
    expect([300, 450, 460, 550, 570, 580, 650, 680, 760, 900].map(String).map(roundWeight))
      .toEqual([400, 400, 500, 500, 500, 600, 600, 700, 700, 700]);
    expect(roundWeight('bold')).toBe(700);
    expect(roundWeight('var(--unknown)')).toBe(null);
  });
});

describe('styleFor', () => {
  it('snaps sizes to the ramp', () => {
    expect(styleFor(9, { weight: 400 })).toBe('caption');
    expect(styleFor(12.2, { weight: 400 })).toBe('caption');
    expect(styleFor(13.9, { weight: 400 })).toBe('footnote');
    expect(styleFor(14, { weight: 400, secondary: true })).toBe('footnote');
    expect(styleFor(14, { weight: 400 })).toBe('body');
    expect(styleFor(15.3, { weight: 600 })).toBe('headline');
    expect(styleFor(18, { weight: 400 })).toBe('title-3');
    expect(styleFor(24, { weight: 400 })).toBe('title-2');
    expect(styleFor(30, { weight: 400 })).toBe('title-1');
    expect(styleFor(42, { weight: 400 })).toBe('large-title');
  });
});

describe('tracking and weight tokens', () => {
  it('names the tokens', () => {
    expect(trackingFor('title-2')).toBe('var(--mds-tracking-title)');
    expect(trackingFor('large-title')).toBe('var(--mds-tracking-display)');
    expect(trackingFor('body')).toBe(null);
    expect(weightToken(600)).toBe('var(--mds-weight-semibold)');
  });
});
