// @vitest-environment node
import { describe, expect, it } from 'vitest';
import { mapLiteral, parseColor, roleOf } from './color.mjs';

const map = (literal, role, theme = 'dark', subject = '.mds-x') =>
  mapLiteral({ color: parseColor(literal), role, theme, subject });

describe('parseColor', () => {
  it('reads hex, rgb, rgba and named colors', () => {
    expect(parseColor('#fff')).toEqual({ r: 255, g: 255, b: 255, a: 1 });
    expect(parseColor('#00000080').a).toBeCloseTo(0.502, 2);
    expect(parseColor('rgba(47, 140, 255, 0.12)')).toEqual({ r: 47, g: 140, b: 255, a: 0.12 });
    expect(parseColor('rgb(0 0 0 / 50%)')).toEqual({ r: 0, g: 0, b: 0, a: 0.5 });
    expect(parseColor('white')).toEqual({ r: 255, g: 255, b: 255, a: 1 });
  });
});

describe('mapLiteral', () => {
  it('maps dark text by contrast against the black ground', () => {
    expect(map('#ffffff', 'text').token).toBe('--mds-label');
    expect(map('#d0d0d0', 'text').token).toBe('--mds-label-2');
    expect(map('rgba(255, 255, 255, 0.5)', 'text').token).toBe('--mds-label-3');
    expect(map('#8fa0b4', 'text').token).toBe('--mds-label-3');
    expect(map('#9bc9ff', 'text').token).toBe('--mds-blue');
    expect(map('#141414', 'text')).toMatchObject({ token: '--mds-ground', review: true });
  });

  it('maps light text by contrast against the white ground', () => {
    expect(map('#111111', 'text', 'light').token).toBe('--mds-label');
    expect(map('#555555', 'text', 'light').token).toBe('--mds-label-2');
    expect(map('#6c7d8f', 'text', 'light').token).toBe('--mds-label-3');
  });

  it('maps fills by tint, alpha and subject', () => {
    expect(map('rgba(255, 255, 255, 0.04)', 'fill').token).toBe('--mds-surface-2');
    expect(map('rgba(47, 140, 255, 0.12)', 'fill').token).toBe('--mds-blue-fill');
    expect(map('#2f8cff', 'fill', 'dark', '.mds-trip-dot').token).toBe('--mds-blue');
    expect(map('#000000', 'fill', 'dark', '.mds-desktop-app').token).toBe('--mds-ground');
    expect(map('#000000', 'fill', 'dark', '.mds-card'))
      .toMatchObject({ token: '--mds-surface', review: true });
    expect(map('#176fb7', 'fill', 'dark', '.mds-chat-send'))
      .toMatchObject({ token: '--mds-action', review: true });
    expect(map('#ffffff', 'fill', 'light', '.mds-card').token).toBe('--mds-surface');
  });

  it('maps lines by subject and focus by tint', () => {
    expect(map('rgba(255, 255, 255, 0.08)', 'line', 'dark', '.mds-card').token)
      .toBe('--mds-separator');
    expect(map('rgba(255, 255, 255, 0.08)', 'line', 'dark', 'textarea').token)
      .toBe('--mds-control-line');
    expect(map('#2f8cff', 'accent').token).toBe('--mds-tint');
  });
});

describe('roleOf', () => {
  it('derives a role from the property and SVG subject', () => {
    expect(roleOf('color', '.x')).toBe('text');
    expect(roleOf('background-color', '.x')).toBe('fill');
    expect(roleOf('border-top', '.x')).toBe('line');
    expect(roleOf('outline', '.x')).toBe('accent');
    expect(roleOf('fill', '.x rect')).toBe('fill');
    expect(roleOf('stroke', '.x rect')).toBe('line');
    expect(roleOf('fill', '.x text')).toBe('text');
    expect(roleOf('content', '.x')).toBe(null);
  });
});
