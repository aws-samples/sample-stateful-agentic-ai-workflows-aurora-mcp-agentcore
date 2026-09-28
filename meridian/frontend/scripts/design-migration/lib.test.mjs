// @vitest-environment node
import { describe, expect, it } from 'vitest';
import { baseSelector, CONTROL, FLOAT, SHELL, subjectOf, unscopeSelector } from './lib.mjs';

describe('selector helpers', () => {
  it('finds the subject compound outside parentheses and brackets', () => {
    expect(subjectOf('.mds-root .mds-card > .mds-title')).toBe('.mds-title');
    expect(subjectOf('.mds-root :is(.a, .b)')).toBe(':is(.a, .b)');
    expect(subjectOf(".mds-root[data-theme='light'] .mds-x:hover")).toBe('.mds-x:hover');
  });

  it('normalises theme-scoped selectors to their base form', () => {
    expect(baseSelector('.mds-root[data-theme="light"] .mds-foo')).toBe('.mds-foo');
    expect(baseSelector('.mds-root .mds-foo, .mds-bar')).toBe('.mds-bar, .mds-foo');
    expect(baseSelector(".mds-root[data-projector-readability='true'] .mds-foo")).toBe('.mds-foo');
  });

  it('removes only the theme attribute when unscoping', () => {
    expect(unscopeSelector('.mds-root[data-theme="light"] .mds-foo')).toBe('.mds-root .mds-foo');
  });

  it('classifies subjects', () => {
    expect(CONTROL.test('textarea')).toBe(true);
    expect(CONTROL.test('.mds-chat-send')).toBe(true);
    expect(CONTROL.test('.mds-card')).toBe(false);
    expect(FLOAT.test('.mds-popover')).toBe(true);
    expect(FLOAT.test('.mds-popover-primary')).toBe(false);
    expect(SHELL.test('.mds-desktop-app.is-discovery')).toBe(true);
    expect(SHELL.test('.mds-desktop-app-card')).toBe(false);
  });
});
