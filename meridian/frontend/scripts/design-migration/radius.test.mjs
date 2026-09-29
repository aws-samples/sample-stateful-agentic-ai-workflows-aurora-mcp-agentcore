// @vitest-environment node
import { describe, expect, it } from 'vitest';
import { mapRadius } from './radius.mjs';

describe('mapRadius', () => {
  it('maps pixel radii onto the scale', () => {
    expect(mapRadius('4px')).toBe('var(--mds-radius-s)');
    expect(mapRadius('7px')).toBe('var(--mds-radius-s)');
    expect(mapRadius('8px')).toBe('var(--mds-radius-m)');
    expect(mapRadius('11px')).toBe('var(--mds-radius-m)');
    expect(mapRadius('14px')).toBe('var(--mds-radius-l)');
    expect(mapRadius('24px')).toBe('var(--mds-radius-l)');
    expect(mapRadius('999px')).toBe('var(--mds-radius-full)');
  });

  it('keeps 0 and 50% and maps each corner', () => {
    expect(mapRadius('0')).toBe('0');
    expect(mapRadius('50%')).toBe('50%');
    expect(mapRadius('16px 16px 4px 16px'))
      .toBe('var(--mds-radius-l) var(--mds-radius-l) var(--mds-radius-s) var(--mds-radius-l)');
  });

  it('refuses shapes it cannot express', () => {
    expect(mapRadius('55% 55% 0 0')).toBe(null);
    expect(mapRadius('8px / 4px')).toBe(null);
  });
});
